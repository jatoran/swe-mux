"""Read-only agent context discovery and guarded instruction-file synchronization.

The browser never supplies filesystem paths to this module.  It receives opaque source
and backup ids from ``inventory`` and can only read or restore those allowlisted shapes.
The sole ordinary write is an explicit whole-file copy between root ``CLAUDE.md`` and
``AGENTS.md``, guarded by the revisions returned by ``preview_sync``.
"""

from __future__ import annotations

import base64
import difflib
import hashlib
import json
import os
import re
import stat
import subprocess
import tempfile
import time
import tomllib
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any
from uuid import uuid4

MAX_SOURCE_BYTES = 512 * 1024
MAX_MEMORY_ITEMS = 128
MAX_DIFF_CHARS = 256 * 1024
MAX_BACKUPS_RETURNED = 20
MAX_BACKUP_SCAN = 2_000

INSTRUCTION_SOURCES = {
    "instruction:claude": ("claude", "CLAUDE.md"),
    "instruction:codex": ("codex", "AGENTS.md"),
}
GLOBAL_INSTRUCTION_SOURCES = {
    "instruction:global:claude": (
        "claude",
        (".claude", "CLAUDE.md"),
        "~/.claude/CLAUDE.md",
    ),
    "instruction:global:codex": (
        "codex",
        (".codex", "AGENTS.md"),
        "~/.codex/AGENTS.md",
    ),
}
SYNC_DIRECTIONS = {
    "claude_to_agents": ("CLAUDE.md", "AGENTS.md"),
    "agents_to_claude": ("AGENTS.md", "CLAUDE.md"),
}


class AgentContextConflict(ValueError):
    """The files changed after the caller's preview."""


def _revision(data: bytes | None) -> str:
    return "missing" if data is None else hashlib.sha256(data).hexdigest()


def _normalized_text(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _line_ending(data: bytes) -> str:
    return "crlf" if b"\r\n" in data else "lf"


def _decode_text(data: bytes, *, label: str) -> str:
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError(f"{label} is not UTF-8 text") from exc


def _bounded_bytes(path: Path, *, label: str) -> bytes:
    if path.is_symlink():
        raise ValueError(f"{label} is a symbolic link and cannot be read here")
    try:
        info = path.stat()
    except OSError as exc:
        raise ValueError(f"{label} is unreadable: {exc}") from exc
    if not stat.S_ISREG(info.st_mode):
        raise ValueError(f"{label} is not a regular file")
    if info.st_size > MAX_SOURCE_BYTES:
        raise ValueError(f"{label} is larger than {MAX_SOURCE_BYTES // 1024} KiB")
    try:
        return path.read_bytes()
    except OSError as exc:
        raise ValueError(f"{label} is unreadable: {exc}") from exc


def _source_token(filename: str) -> str:
    encoded = base64.urlsafe_b64encode(filename.encode("utf-8")).decode("ascii")
    return encoded.rstrip("=")


def _source_filename(token: str) -> str:
    if not token or not re.fullmatch(r"[A-Za-z0-9_-]+", token):
        raise ValueError("unknown agent context source")
    padded = token + "=" * (-len(token) % 4)
    try:
        filename = base64.urlsafe_b64decode(padded).decode("utf-8")
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError("unknown agent context source") from exc
    if Path(filename).name != filename or not filename.casefold().endswith(".md"):
        raise ValueError("unknown agent context source")
    return filename


def _claude_project_key(root: Path) -> str:
    # Claude's default directory replaces every separator and the drive colon with
    # ``-`` (D:\\repo -> D--repo). Keep alphanumerics, underscores, and existing
    # hyphens unchanged so this matches the CLI's on-disk project key.
    return re.sub(r"[^A-Za-z0-9_-]", "-", str(root))


def canonical_repository_root(root: Path) -> Path:
    """Return the primary checkout for a Git worktree, or ``root`` outside Git."""

    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "rev-parse",
                "--path-format=absolute",
                "--git-common-dir",
            ],
            capture_output=True,
            check=False,
            encoding="utf-8",
            errors="replace",
            timeout=3,
        )
    except (OSError, subprocess.TimeoutExpired):
        return root
    if result.returncode != 0 or not result.stdout.strip():
        return root
    common = Path(result.stdout.strip()).resolve()
    return common.parent if common.name.casefold() == ".git" else root


class AgentContextService:
    """Project-scoped discovery plus manual, reversible instruction sync."""

    def __init__(
        self,
        backup_root: Path,
        *,
        home: Path | None = None,
        repository_root: Callable[[Path], Path] = canonical_repository_root,
    ) -> None:
        self.backup_root = Path(backup_root)
        self.home = Path(home) if home is not None else Path.home()
        self.repository_root = repository_root
        self._start_revisions: dict[tuple[str, str], str] = {}
        for _, relative_path, _ in GLOBAL_INSTRUCTION_SOURCES.values():
            path = self.home.joinpath(*relative_path)
            self._start_revisions[(str(path.parent.resolve()), path.name)] = self._file_revision(
                path
            )

    def capture_project(self, root: str | Path) -> None:
        project_root = Path(root).resolve()
        for filename in ("CLAUDE.md", "AGENTS.md"):
            self._start_revisions[(str(project_root), filename)] = self._file_revision(
                project_root / filename
            )

    def _file_revision(self, path: Path) -> str:
        if not path.exists() or path.is_symlink() or not path.is_file():
            return "missing"
        try:
            if path.stat().st_size > MAX_SOURCE_BYTES:
                return "too_large"
            return _revision(path.read_bytes())
        except OSError:
            return "unreadable"

    def _changed_since_start(self, root: Path, filename: str, current: str) -> bool:
        key = (str(root), filename)
        if key not in self._start_revisions:
            self._start_revisions[key] = current
        return self._start_revisions[key] != current

    def _instruction_item(self, root: Path, source_id: str) -> dict[str, Any]:
        if source_id in INSTRUCTION_SOURCES:
            provider, filename = INSTRUCTION_SOURCES[source_id]
            path = root / filename
            scope = "project"
            label = filename
        else:
            provider, relative_path, label = GLOBAL_INSTRUCTION_SOURCES[source_id]
            path = self.home.joinpath(*relative_path)
            filename = path.name
            scope = "global"
        item: dict[str, Any] = {
            "id": source_id,
            "provider": provider,
            "kind": "instructions",
            "scope": scope,
            "label": label,
            "status": "missing",
            "revealable": False,
            "revision": "missing",
            "size": 0,
            "modified_at": None,
        }
        if path.is_symlink():
            item.update(status="unsupported", detail="Symbolic links are not followed.")
        elif path.exists():
            try:
                info = path.stat()
                if not stat.S_ISREG(info.st_mode):
                    item.update(status="unsupported", detail="This path is not a regular file.")
                else:
                    item["revealable"] = True
                    if info.st_size > MAX_SOURCE_BYTES:
                        item.update(
                            status="too_large",
                            size=info.st_size,
                            modified_at=info.st_mtime,
                            revision="too_large",
                            detail=f"The file exceeds {MAX_SOURCE_BYTES // 1024} KiB.",
                        )
                    else:
                        data = path.read_bytes()
                        _decode_text(data, label=filename)
                        item.update(
                            status="available",
                            size=len(data),
                            modified_at=info.st_mtime,
                            revision=_revision(data),
                            line_ending=_line_ending(data),
                        )
            except (OSError, ValueError) as exc:
                item.update(status="unreadable", detail=str(exc), revision="unreadable")
        item["changed_since_start"] = self._changed_since_start(
            path.parent.resolve(), filename, str(item["revision"])
        )
        return item

    def _claude_memory_directory(self, root: Path) -> tuple[Path, str | None]:
        settings = self.home / ".claude" / "settings.json"
        if settings.is_file() and not settings.is_symlink():
            try:
                raw = json.loads(settings.read_text(encoding="utf-8"))
                configured = raw.get("autoMemoryDirectory") if isinstance(raw, dict) else None
                if isinstance(configured, str) and configured.strip():
                    configured_path = configured.strip()
                    if configured_path == "~" or configured_path.startswith(("~/", "~\\")):
                        value = (
                            self.home / configured_path[2:]
                            if len(configured_path) > 1
                            else self.home
                        )
                    else:
                        value = Path(configured_path)
                    if not value.is_absolute():
                        value = self.home / value
                    return value.resolve(), None
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                return settings, f"Claude settings are unreadable: {exc}"
        repository_root = self.repository_root(root).resolve()
        return (
            self.home / ".claude" / "projects" / _claude_project_key(repository_root) / "memory",
            None,
        )

    def _claude_provider(self, root: Path) -> dict[str, Any]:
        directory, settings_error = self._claude_memory_directory(root)
        provider: dict[str, Any] = {
            "id": "claude",
            "label": "Claude",
            "status": "missing",
            "detail": "No learned project memory directory was found.",
            "items": [],
            "item_count": 0,
            "truncated": False,
        }
        if settings_error:
            provider.update(status="unreadable", detail=settings_error)
            return provider
        if directory.is_symlink():
            provider.update(status="unsupported", detail="The learned memory directory is a link.")
            return provider
        if not directory.exists():
            return provider
        if not directory.is_dir():
            provider.update(
                status="unreadable", detail="The learned memory path is not a directory."
            )
            return provider
        try:
            paths = sorted(
                (item for item in directory.iterdir() if item.name.casefold().endswith(".md")),
                key=lambda item: (item.name.casefold() != "memory.md", item.name.casefold()),
            )
        except OSError as exc:
            provider.update(status="unreadable", detail=f"Learned memory is unreadable: {exc}")
            return provider
        provider["truncated"] = len(paths) > MAX_MEMORY_ITEMS
        for path in paths[:MAX_MEMORY_ITEMS]:
            source_id = f"memory:claude:{_source_token(path.name)}"
            item: dict[str, Any] = {
                "id": source_id,
                "provider": "claude",
                "kind": "memory",
                "scope": "project",
                "label": path.name,
                "status": "available",
                "revealable": False,
                "size": 0,
                "modified_at": None,
            }
            if path.is_symlink():
                item.update(status="unsupported", detail="Symbolic links are not followed.")
            else:
                try:
                    info = path.stat()
                    if not stat.S_ISREG(info.st_mode):
                        item.update(status="unsupported", detail="This is not a regular file.")
                    else:
                        item["revealable"] = True
                        if info.st_size > MAX_SOURCE_BYTES:
                            item.update(
                                status="too_large",
                                size=info.st_size,
                                modified_at=info.st_mtime,
                                detail=f"The file exceeds {MAX_SOURCE_BYTES // 1024} KiB.",
                            )
                        else:
                            data = path.read_bytes()
                            _decode_text(data, label=path.name)
                            item.update(size=len(data), modified_at=info.st_mtime)
                except (OSError, ValueError) as exc:
                    item.update(status="unreadable", detail=f"Unreadable: {exc}")
            provider["items"].append(item)
        provider["item_count"] = len(paths)
        provider.update(
            status="available" if provider["items"] else "missing",
            detail=(
                "Learned project memory files used by Claude. Repository worktrees "
                "share this provider source."
                if provider["items"]
                else "The learned memory directory contains no Markdown files."
            ),
        )
        return provider

    def _codex_provider(self) -> dict[str, Any]:
        config = self.home / ".codex" / "config.toml"
        enabled = False
        if config.is_file() and not config.is_symlink():
            try:
                raw = tomllib.loads(config.read_text(encoding="utf-8"))
                features = raw.get("features") if isinstance(raw, dict) else None
                enabled = bool(features.get("memories")) if isinstance(features, dict) else False
            except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
                return {
                    "id": "codex",
                    "label": "Codex",
                    "status": "unreadable",
                    "detail": f"Codex memory configuration is unreadable: {exc}",
                    "items": [],
                    "item_count": 0,
                    "truncated": False,
                }
        return {
            "id": "codex",
            "label": "Codex",
            "status": "unsupported" if enabled else "disabled",
            "detail": (
                "Memory is enabled, but Codex does not expose a stable "
                "project-memory file inventory."
                if enabled
                else "Codex project memory is disabled or not configured."
            ),
            "items": [],
            "item_count": 0,
            "truncated": False,
        }

    def inventory(self, project_id: str, project_name: str, root: str | Path) -> dict[str, Any]:
        project_root = Path(root).resolve()
        instructions = [
            self._instruction_item(project_root, source_id) for source_id in INSTRUCTION_SOURCES
        ]
        available = [item for item in instructions if item["status"] == "available"]
        if len(available) < 2:
            comparison = "missing"
        else:
            left = self.read_source(project_root, str(instructions[0]["id"]))["text"]
            right = self.read_source(project_root, str(instructions[1]["id"]))["text"]
            comparison = (
                "in_sync" if _normalized_text(left) == _normalized_text(right) else "different"
            )
        return {
            "project": {"id": project_id, "name": project_name},
            "generated_at": time.time(),
            "instructions": {"comparison": comparison, "items": instructions},
            "global_instructions": {
                "items": [
                    self._instruction_item(project_root, source_id)
                    for source_id in GLOBAL_INSTRUCTION_SOURCES
                ]
            },
            "providers": [self._claude_provider(project_root), self._codex_provider()],
            "backups": self._backups(project_id),
        }

    def _source_descriptor(
        self, root: str | Path, source_id: str
    ) -> tuple[Path, str, str, str, str, str]:
        project_root = Path(root).resolve()
        if source_id in INSTRUCTION_SOURCES:
            provider, filename = INSTRUCTION_SOURCES[source_id]
            path = project_root / filename
            kind = "instructions"
            scope = "project"
            label = filename
        elif source_id in GLOBAL_INSTRUCTION_SOURCES:
            provider, relative_path, label = GLOBAL_INSTRUCTION_SOURCES[source_id]
            path = self.home.joinpath(*relative_path)
            filename = path.name
            kind = "instructions"
            scope = "global"
        elif source_id.startswith("memory:claude:"):
            filename = _source_filename(source_id.removeprefix("memory:claude:"))
            directory, settings_error = self._claude_memory_directory(project_root)
            if settings_error:
                raise ValueError(settings_error)
            path = directory / filename
            provider = "claude"
            kind = "memory"
            scope = "project"
            label = filename
        else:
            raise ValueError("unknown agent context source")
        return path, provider, kind, scope, label, filename

    def source_path(self, root: str | Path, source_id: str) -> Path:
        """Resolve an opaque source id to a regular file that the OS may reveal."""

        path, _provider, _kind, _scope, _label, filename = self._source_descriptor(
            root, source_id
        )
        if not path.exists():
            raise ValueError(f"{filename} is missing")
        if path.is_symlink():
            raise ValueError(f"{filename} is a symbolic link and cannot be revealed here")
        try:
            info = path.stat()
        except OSError as exc:
            raise ValueError(f"{filename} is unreadable: {exc}") from exc
        if not stat.S_ISREG(info.st_mode):
            raise ValueError(f"{filename} is not a regular file")
        return path.resolve()

    def read_source(self, root: str | Path, source_id: str) -> dict[str, Any]:
        path, provider, kind, scope, label, filename = self._source_descriptor(root, source_id)
        if not path.exists():
            raise ValueError(f"{filename} is missing")
        data = _bounded_bytes(path, label=filename)
        return {
            "source": {
                "id": source_id,
                "provider": provider,
                "kind": kind,
                "scope": scope,
                "label": label,
                "revealable": True,
                "revision": _revision(data),
                "size": len(data),
                "modified_at": path.stat().st_mtime,
            },
            "text": _decode_text(data, label=filename),
        }

    def _sync_paths(self, root: Path, direction: str) -> tuple[Path, Path]:
        try:
            source_name, target_name = SYNC_DIRECTIONS[direction]
        except KeyError as exc:
            raise ValueError("direction must be claude_to_agents or agents_to_claude") from exc
        return root / source_name, root / target_name

    def _sync_snapshot(self, root: Path, direction: str) -> tuple[Path, Path, bytes, bytes | None]:
        source, target = self._sync_paths(root, direction)
        if not source.exists():
            raise ValueError(f"{source.name} is missing")
        source_data = _bounded_bytes(source, label=source.name)
        _decode_text(source_data, label=source.name)
        target_data: bytes | None = None
        if target.exists():
            target_data = _bounded_bytes(target, label=target.name)
            _decode_text(target_data, label=target.name)
        elif target.is_symlink():
            raise ValueError(f"{target.name} is a symbolic link and cannot be replaced")
        return source, target, source_data, target_data

    def preview_sync(self, root: str | Path, direction: str) -> dict[str, Any]:
        project_root = Path(root).resolve()
        source, target, source_data, target_data = self._sync_snapshot(project_root, direction)
        source_text = _normalized_text(_decode_text(source_data, label=source.name))
        target_text = (
            _normalized_text(_decode_text(target_data, label=target.name))
            if target_data is not None
            else ""
        )
        diff = "".join(
            difflib.unified_diff(
                target_text.splitlines(keepends=True),
                source_text.splitlines(keepends=True),
                fromfile=target.name,
                tofile=source.name,
            )
        )
        truncated = len(diff) > MAX_DIFF_CHARS
        if truncated:
            diff = diff[:MAX_DIFF_CHARS] + "\n… diff truncated …\n"
        return {
            "direction": direction,
            "source": {"label": source.name, "revision": _revision(source_data)},
            "target": {"label": target.name, "revision": _revision(target_data)},
            "in_sync": source_text == target_text,
            "diff": diff,
            "diff_truncated": truncated,
        }

    def _project_backup_dir(self, project_id: str) -> Path:
        key = hashlib.sha256(project_id.encode("utf-8")).hexdigest()[:24]
        return self.backup_root / key

    def _create_backup(self, project_id: str, target: Path, data: bytes | None) -> dict[str, Any]:
        directory = self._project_backup_dir(project_id)
        if directory.is_symlink():
            raise ValueError("agent context backup directory cannot be a symbolic link")
        directory.mkdir(parents=True, exist_ok=True)
        backup_id = uuid4().hex
        manifest = {
            "id": backup_id,
            "target": target.name,
            "created_at": time.time(),
            "existed": data is not None,
            "revision": _revision(data),
            "size": len(data) if data is not None else 0,
        }
        if data is not None:
            self._atomic_write(directory / f"{backup_id}.bin", data)
        self._atomic_write(
            directory / f"{backup_id}.json",
            json.dumps(manifest, sort_keys=True).encode("utf-8"),
        )
        return manifest

    def _backups(self, project_id: str) -> list[dict[str, Any]]:
        directory = self._project_backup_dir(project_id)
        if not directory.is_dir() or directory.is_symlink():
            return []
        manifests: list[dict[str, Any]] = []
        for index, path in enumerate(directory.glob("*.json")):
            if index >= MAX_BACKUP_SCAN:
                break
            if path.is_symlink():
                continue
            try:
                item = json.loads(_bounded_bytes(path, label="backup manifest").decode("utf-8"))
                if (
                    isinstance(item, dict)
                    and item.get("id") == path.stem
                    and item.get("target") in {"CLAUDE.md", "AGENTS.md"}
                ):
                    manifests.append(item)
            except (OSError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
                continue
        return sorted(manifests, key=lambda item: float(item["created_at"]), reverse=True)[
            :MAX_BACKUPS_RETURNED
        ]

    @staticmethod
    def _atomic_write(path: Path, data: bytes, *, mode: int | None = None) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        handle, temp_name = tempfile.mkstemp(prefix=".agent-context-", dir=path.parent)
        temp = Path(temp_name)
        try:
            with os.fdopen(handle, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            if mode is not None:
                os.chmod(temp, stat.S_IMODE(mode))
            os.replace(temp, path)
        finally:
            if temp.exists():
                temp.unlink()

    def sync(
        self,
        project_id: str,
        root: str | Path,
        direction: str,
        source_revision: str,
        target_revision: str,
    ) -> dict[str, Any]:
        project_root = Path(root).resolve()
        source, target, source_data, target_data = self._sync_snapshot(project_root, direction)
        if _revision(source_data) != source_revision or _revision(target_data) != target_revision:
            raise AgentContextConflict("instruction files changed since the sync preview")
        source_text = _normalized_text(_decode_text(source_data, label=source.name))
        eol = "\r\n" if target_data is not None and _line_ending(target_data) == "crlf" else "\n"
        output = source_text.replace("\n", eol).encode("utf-8")
        mode = target.stat().st_mode if target_data is not None else None
        backup = self._create_backup(project_id, target, target_data)
        self._atomic_write(target, output, mode=mode)
        return {
            "ok": True,
            "direction": direction,
            "source": source.name,
            "target": target.name,
            "revision": _revision(output),
            "backup": backup,
        }

    def _backup_manifest(self, project_id: str, backup_id: str) -> dict[str, Any]:
        if not re.fullmatch(r"[0-9a-f]{32}", backup_id):
            raise ValueError("unknown agent context backup")
        path = self._project_backup_dir(project_id) / f"{backup_id}.json"
        try:
            raw: Any = json.loads(
                _bounded_bytes(path, label="backup manifest").decode("utf-8")
            )
        except (OSError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("unknown agent context backup") from exc
        if not isinstance(raw, dict):
            raise ValueError("invalid agent context backup")
        manifest: dict[str, Any] = {str(key): value for key, value in raw.items()}
        if manifest.get("id") != backup_id or manifest.get("target") not in {
            "CLAUDE.md",
            "AGENTS.md",
        }:
            raise ValueError("invalid agent context backup")
        return manifest

    def restore(
        self,
        project_id: str,
        root: str | Path,
        backup_id: str,
        target_revision: str,
    ) -> dict[str, Any]:
        project_root = Path(root).resolve()
        manifest = self._backup_manifest(project_id, backup_id)
        target = project_root / str(manifest["target"])
        if target.is_symlink():
            raise ValueError(f"{target.name} is a symbolic link and cannot be restored")
        current = _bounded_bytes(target, label=target.name) if target.exists() else None
        if _revision(current) != target_revision:
            raise AgentContextConflict("instruction file changed before the backup was restored")
        data: bytes | None = None
        if bool(manifest["existed"]):
            data_path = self._project_backup_dir(project_id) / f"{backup_id}.bin"
            data = _bounded_bytes(data_path, label="backup")
            if _revision(data) != manifest.get("revision"):
                raise ValueError("agent context backup is corrupt")
        undo = self._create_backup(project_id, target, current)
        if data is not None:
            mode = target.stat().st_mode if target.exists() else None
            self._atomic_write(target, data, mode=mode)
            revision = _revision(data)
        else:
            if target.exists():
                target.unlink()
            revision = "missing"
        return {
            "ok": True,
            "target": target.name,
            "revision": revision,
            "backup": undo,
        }


def instruction_filenames() -> Iterable[str]:
    """Expose the tiny allowlist for focused tests and documentation tooling."""

    return ("CLAUDE.md", "AGENTS.md")
