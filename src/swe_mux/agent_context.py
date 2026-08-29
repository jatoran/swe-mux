"""Read-only agent context discovery and guarded instruction-file synchronization.

The browser never supplies filesystem paths to this module.  It receives opaque source
and backup ids from ``inventory`` and can only read or restore those allowlisted shapes.
The sole ordinary write is an explicit whole-file copy between distinct root
instruction files declared by registered harness descriptors, guarded by the
revisions returned by ``preview_sync``.
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
from collections import OrderedDict
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from .harness import HARNESSES, descriptor, instruction_harnesses

MAX_SOURCE_BYTES = 512 * 1024
MAX_MEMORY_ITEMS = 128
MAX_DIFF_CHARS = 256 * 1024
MAX_BACKUPS_RETURNED = 20
MAX_BACKUP_SCAN = 2_000
#: How many Projects' instruction inventories are retained at once.
INVENTORY_CACHE_LIMIT = 8


def _project_instruction_sources() -> dict[str, tuple[str, str, tuple[str, ...]]]:
    """Project-root instruction files, one entry per distinct file.

    Keyed by file rather than by harness, because the file is the artifact and a
    harness is a reader of it: four of the five harnesses read the same root
    ``AGENTS.md``, and listing that file four times would be four handles onto one
    path. The id is minted from the first harness declaring the file, which keeps
    the two ids this surface has always published (``instruction:claude`` for
    ``CLAUDE.md``, ``instruction:codex`` for ``AGENTS.md``) while every later
    harness joins an existing entry instead of adding one.
    """
    owner: dict[str, str] = {}
    readers: dict[str, list[str]] = {}
    files: dict[str, str] = {}
    for name in instruction_harnesses():
        filename = descriptor(name).instruction_file_name
        if filename is None:  # pragma: no cover - instruction_harnesses filters on it
            continue
        source_id = owner.setdefault(filename, f"instruction:{name}")
        files[source_id] = filename
        readers.setdefault(source_id, []).append(name)
    return {
        source_id: (readers[source_id][0], files[source_id], tuple(readers[source_id]))
        for source_id in files
    }


def _global_instruction_sources() -> dict[str, tuple[str, tuple[str, ...], str]]:
    """User-level instruction files, one entry per harness.

    Not collapsed by filename the way project sources are: every harness keeps its
    global context file somewhere different, so these are genuinely distinct paths
    that happen to share a name.
    """
    sources: dict[str, tuple[str, tuple[str, ...], str]] = {}
    for name in instruction_harnesses():
        parts = descriptor(name).global_instruction_parts
        if parts is None:
            continue
        sources[f"instruction:global:{name}"] = (name, parts, "~/" + "/".join(parts))
    return sources


INSTRUCTION_SOURCES = _project_instruction_sources()
GLOBAL_INSTRUCTION_SOURCES = _global_instruction_sources()
LEGACY_SYNC_DIRECTIONS = {
    "claude_to_agents": ("CLAUDE.md", "AGENTS.md"),
    "agents_to_claude": ("AGENTS.md", "CLAUDE.md"),
}

EntryKind = Literal["missing", "regular", "symlink"]


@dataclass(frozen=True, slots=True)
class InstructionEntry:
    """One Project-root instruction directory entry without following unknown links."""

    kind: EntryKind
    revision: str
    data: bytes | None = None
    mode: int | None = None
    link_target: str | None = None


def _sync_direction(source_id: str, target_id: str) -> str:
    return f"{source_id}->{target_id}"


def _sync_options() -> list[dict[str, str]]:
    sources = list(INSTRUCTION_SOURCES)
    return [
        {
            "direction": _sync_direction(source_id, target_id),
            "source_id": source_id,
            "source": INSTRUCTION_SOURCES[source_id][1],
            "target_id": target_id,
            "target": INSTRUCTION_SOURCES[target_id][1],
        }
        for source_id in sources
        for target_id in sources
        if source_id != target_id
    ]


class AgentContextConflict(ValueError):
    """The files changed after the caller's preview."""


def _revision(data: bytes | None) -> str:
    return "missing" if data is None else hashlib.sha256(data).hexdigest()


def _link_revision(target: str) -> str:
    return "link:" + hashlib.sha256(target.encode("utf-8")).hexdigest()


def _instruction_id_for_filename(filename: str) -> str | None:
    for source_id, (_provider, declared, _readers) in INSTRUCTION_SOURCES.items():
        if declared == filename:
            return source_id
    return None


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
    """Project-scoped discovery plus reversible instruction copy and linking."""

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
        #: (project id, root) -> (signature, payload). Bounded: the drawer reads one
        #: Project at a time, and a fleet of them would otherwise retain one inventory
        #: each for the life of the daemon.
        self._inventory_cache: OrderedDict[
            tuple[str, str], tuple[tuple[Any, ...], dict[str, Any]]
        ] = OrderedDict()
        for _, relative_path, _ in GLOBAL_INSTRUCTION_SOURCES.values():
            path = self.home.joinpath(*relative_path)
            self._start_revisions[(str(path.parent.resolve()), path.name)] = self._file_revision(
                path
            )

    @staticmethod
    def _managed_link_target(root: Path, path: Path) -> tuple[str, Path, str]:
        """Resolve only a relative link to another declared root instruction file."""

        try:
            raw_target = os.readlink(path)
        except OSError as exc:
            raise ValueError(f"{path.name} is an unreadable symbolic link: {exc}") from exc
        source_id = _instruction_id_for_filename(raw_target)
        if source_id is None or raw_target == path.name:
            raise ValueError(f"{path.name} links outside the declared Project instruction files")
        target = root / raw_target
        if target.is_symlink():
            raise ValueError(f"{path.name} links to another symbolic link")
        if not target.exists():
            raise ValueError(f"{path.name} links to missing {raw_target}")
        try:
            info = target.stat()
        except OSError as exc:
            raise ValueError(f"{path.name} link target is unreadable: {exc}") from exc
        if not stat.S_ISREG(info.st_mode):
            raise ValueError(f"{path.name} link target is not a regular file")
        return source_id, target, raw_target

    def _instruction_entry(self, root: Path, path: Path) -> InstructionEntry:
        if path.is_symlink():
            _source_id, _target, raw_target = self._managed_link_target(root, path)
            return InstructionEntry(
                kind="symlink",
                revision=_link_revision(raw_target),
                link_target=raw_target,
            )
        if not path.exists():
            return InstructionEntry(kind="missing", revision="missing")
        data = _bounded_bytes(path, label=path.name)
        return InstructionEntry(
            kind="regular",
            revision=_revision(data),
            data=data,
            mode=path.stat().st_mode,
        )

    def capture_project(self, root: str | Path) -> None:
        project_root = Path(root).resolve()
        for _provider, filename, _readers in INSTRUCTION_SOURCES.values():
            self._start_revisions[(str(project_root), filename)] = self._file_revision(
                project_root / filename
            )

    def _file_revision(self, path: Path) -> str:
        if path.is_symlink():
            try:
                return _link_revision(os.readlink(path))
            except OSError:
                return "unreadable"
        if not path.exists() or not path.is_file():
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
            provider, filename, readers = INSTRUCTION_SOURCES[source_id]
            path = root / filename
            scope = "project"
            label = filename
        else:
            provider, relative_path, label = GLOBAL_INSTRUCTION_SOURCES[source_id]
            path = self.home.joinpath(*relative_path)
            filename = path.name
            scope = "global"
            readers = (provider,)
        item: dict[str, Any] = {
            "id": source_id,
            "provider": provider,
            "harness": provider,
            # Every harness that reads this file. A project-root instruction file is
            # shared, so naming only the harness the id was minted from would
            # under-report who a change reaches.
            "readers": list(readers),
            "kind": "instructions",
            "scope": scope,
            "label": label,
            "status": "missing",
            "revealable": False,
            "revision": "missing",
            "size": 0,
            "modified_at": None,
            "entrypoint_kind": (
                "project_root_instructions" if scope == "project" else "global_instructions"
            ),
        }
        if path.is_symlink():
            if scope != "project":
                item.update(status="unsupported", detail="Symbolic links are not followed.")
            else:
                try:
                    target_id, target, raw_target = self._managed_link_target(root, path)
                    info = target.stat()
                    item.update(
                        revision=_link_revision(raw_target),
                        link_target_id=target_id,
                        link_target=raw_target,
                        revealable=True,
                        detail=f"Relative link to {raw_target}.",
                    )
                    if info.st_size > MAX_SOURCE_BYTES:
                        item.update(
                            status="too_large",
                            size=info.st_size,
                            modified_at=info.st_mtime,
                            detail=f"{raw_target} exceeds {MAX_SOURCE_BYTES // 1024} KiB.",
                        )
                    else:
                        data = target.read_bytes()
                        _decode_text(data, label=filename)
                        item.update(
                            status="available",
                            size=len(data),
                            modified_at=info.st_mtime,
                            content_revision=_revision(data),
                            line_ending=_line_ending(data),
                        )
                except (OSError, ValueError) as exc:
                    item.update(
                        status="unsupported",
                        detail=str(exc),
                        revision=self._file_revision(path),
                    )
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
            "label": descriptor("claude").display_name,
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
                "harness": "claude",
                "kind": "memory",
                "scope": "project",
                "label": path.name,
                "status": "available",
                "revealable": False,
                "size": 0,
                "modified_at": None,
                "revision": "missing",
                "entrypoint_kind": "entrypoint" if path.name.casefold() == "memory.md" else "topic",
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
                            item.update(
                                size=len(data),
                                modified_at=info.st_mtime,
                                revision=_revision(data),
                            )
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

    def _codex_provider(self, unsupported_detail: str) -> dict[str, Any]:
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
                    "label": descriptor("codex").display_name,
                    "status": "unreadable",
                    "detail": f"Codex memory configuration is unreadable: {exc}",
                    "items": [],
                    "item_count": 0,
                    "truncated": False,
                }
        return {
            "id": "codex",
            "label": descriptor("codex").display_name,
            "status": "unsupported" if enabled else "disabled",
            "detail": (
                unsupported_detail
                if enabled
                else "Codex project memory is disabled or not configured."
            ),
            "items": [],
            "item_count": 0,
            "truncated": False,
        }

    def _memory_provider(self, harness_name: str, root: Path) -> dict[str, Any]:
        harness = descriptor(harness_name)
        capability = harness.memory_inventory
        if capability is None:
            return {
                "id": harness_name,
                "label": harness.display_name,
                "status": "unsupported",
                "detail": "This harness declares no stable learned-memory file inventory.",
                "items": [],
                "item_count": 0,
                "truncated": False,
            }
        if capability.kind == "claude_project_markdown":
            return self._claude_provider(root)
        if capability.kind == "codex_feature_flag":
            return self._codex_provider(capability.detail)
        raise AssertionError(f"unhandled memory inventory kind: {capability.kind}")

    def _inventory_signature(self, project_root: Path) -> tuple[Any, ...]:
        """A cheap fingerprint of everything `inventory` reads.

        Stat calls only. What makes the inventory expensive is *reading and normalizing*
        every instruction file - up to four project files plus the global ones, decoded,
        hashed, and compared against each other for the in-sync verdict - and that is
        exactly the work this lets a repeat call skip.

        Size beside mtime, because the two together are what an editor moves and either
        alone is not: a rewrite can land in the same nanosecond as the read that
        preceded it, and a same-size edit is common. Where even that is not enough, the
        surface has an explicit rescan, which is the honest escape hatch - and the
        reason this may be a stat signature rather than a content hash at all.
        """
        marks: list[Any] = []
        paths = [project_root / filename for _p, filename, _r in INSTRUCTION_SOURCES.values()]
        paths.extend(
            self.home.joinpath(*relative_path)
            for _p, relative_path, _l in GLOBAL_INSTRUCTION_SOURCES.values()
        )
        # The two directories whose *listing* is part of the answer. A file added to
        # either changes the directory's own mtime, which is what has to be noticed.
        directory, _error = self._claude_memory_directory(project_root)
        paths.extend([directory, self.home / ".claude" / "settings.json"])
        for path in paths:
            try:
                info = path.lstat()
                link_target = os.readlink(path) if stat.S_ISLNK(info.st_mode) else None
                marks.append(
                    (
                        str(path),
                        stat.S_IFMT(info.st_mode),
                        info.st_mtime_ns,
                        info.st_size,
                        link_target,
                    )
                )
            except OSError:
                # "Absent" is a state the inventory reports, so it is part of the
                # signature: a file appearing must invalidate, not read as unchanged.
                marks.append((str(path), None, None))
        return tuple(marks)

    def inventory(
        self,
        project_id: str,
        project_name: str,
        root: str | Path,
        *,
        refresh: bool = False,
    ) -> dict[str, Any]:
        """The Agent tab's Instructions reading, memoized on what it reads.

        The tab opens on this, and every open re-read and re-normalized every
        instruction file with nothing retained on either side of the wire. The cache is
        invalidated by the files themselves moving; `refresh` is the rescan control,
        which bypasses it outright rather than trusting the signature.
        """
        project_root = Path(root).resolve()
        key = (project_id, str(project_root))
        signature = self._inventory_signature(project_root)
        if not refresh:
            cached = self._inventory_cache.get(key)
            if cached is not None and cached[0] == signature:
                self._inventory_cache.move_to_end(key)
                return cached[1]
        payload = self._inventory(project_id, project_name, project_root)
        self._inventory_cache[key] = (signature, payload)
        self._inventory_cache.move_to_end(key)
        while len(self._inventory_cache) > INVENTORY_CACHE_LIMIT:
            self._inventory_cache.popitem(last=False)
        return payload

    def _inventory(self, project_id: str, project_name: str, project_root: Path) -> dict[str, Any]:
        instructions = [
            self._instruction_item(project_root, source_id) for source_id in INSTRUCTION_SOURCES
        ]
        available = [item for item in instructions if item["status"] == "available"]
        if len(available) < 2:
            comparison = "missing"
        else:
            normalized = {
                _normalized_text(self.read_source(project_root, str(item["id"]))["text"])
                for item in available
            }
            if len(normalized) != 1:
                comparison = "different"
            else:
                identities = {str(item.get("link_target_id") or item["id"]) for item in available}
                linked = any(item.get("link_target_id") for item in available)
                comparison = "linked" if linked and len(identities) == 1 else "in_sync"
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
            "providers": [self._memory_provider(name, project_root) for name in HARNESSES],
            "sync_options": _sync_options(),
            "backups": self._backups(project_id),
        }

    def _source_descriptor(
        self, root: str | Path, source_id: str
    ) -> tuple[Path, str, str, str, str, str]:
        project_root = Path(root).resolve()
        if source_id in INSTRUCTION_SOURCES:
            provider, filename, _readers = INSTRUCTION_SOURCES[source_id]
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
        elif source_id.startswith("memory:"):
            parts = source_id.split(":", 2)
            if len(parts) != 3 or parts[1] not in HARNESSES:
                raise ValueError("unknown agent context source")
            provider = parts[1]
            capability = descriptor(provider).memory_inventory
            if capability is None or capability.kind != "claude_project_markdown":
                raise ValueError("unknown agent context source")
            filename = _source_filename(parts[2])
            directory, settings_error = self._claude_memory_directory(project_root)
            if settings_error:
                raise ValueError(settings_error)
            path = directory / filename
            kind = "memory"
            scope = "project"
            label = filename
        else:
            raise ValueError("unknown agent context source")
        return path, provider, kind, scope, label, filename

    def source_path(self, root: str | Path, source_id: str) -> Path:
        """Resolve an opaque source id to a regular file that the OS may reveal."""

        path, _provider, kind, scope, _label, filename = self._source_descriptor(root, source_id)
        if path.is_symlink():
            if kind != "instructions" or scope != "project":
                raise ValueError(f"{filename} is a symbolic link and cannot be revealed here")
            _target_id, path, _raw_target = self._managed_link_target(Path(root).resolve(), path)
        if not path.exists():
            raise ValueError(f"{filename} is missing")
        try:
            info = path.stat()
        except OSError as exc:
            raise ValueError(f"{filename} is unreadable: {exc}") from exc
        if not stat.S_ISREG(info.st_mode):
            raise ValueError(f"{filename} is not a regular file")
        return path.resolve()

    def read_source(self, root: str | Path, source_id: str) -> dict[str, Any]:
        path, provider, kind, scope, label, filename = self._source_descriptor(root, source_id)
        read_path = path
        link_fields: dict[str, Any] = {}
        if path.is_symlink():
            if kind != "instructions" or scope != "project":
                raise ValueError(f"{filename} is a symbolic link and cannot be read here")
            target_id, read_path, raw_target = self._managed_link_target(Path(root).resolve(), path)
            link_fields = {
                "link_target_id": target_id,
                "link_target": raw_target,
            }
        if not read_path.exists():
            raise ValueError(f"{filename} is missing")
        data = _bounded_bytes(read_path, label=filename)
        if link_fields:
            link_fields["content_revision"] = _revision(data)
        readers = (
            list(INSTRUCTION_SOURCES[source_id][2])
            if source_id in INSTRUCTION_SOURCES
            else [provider]
        )
        entrypoint_kind = (
            "project_root_instructions"
            if kind == "instructions" and scope == "project"
            else "global_instructions"
            if kind == "instructions"
            else "entrypoint"
            if filename.casefold() == "memory.md"
            else "topic"
        )
        return {
            "source": {
                "id": source_id,
                "provider": provider,
                "harness": provider,
                "readers": readers,
                "kind": kind,
                "scope": scope,
                "label": label,
                "revealable": True,
                "revision": _link_revision(str(link_fields["link_target"]))
                if link_fields
                else _revision(data),
                "size": len(data),
                "modified_at": read_path.stat().st_mtime,
                **link_fields,
                "entrypoint_kind": entrypoint_kind,
            },
            "text": _decode_text(data, label=filename),
        }

    def _sync_paths(self, root: Path, direction: str) -> tuple[Path, Path]:
        legacy = LEGACY_SYNC_DIRECTIONS.get(direction)
        if legacy is not None and set(legacy).issubset(set(instruction_filenames())):
            source_name, target_name = legacy
            return root / source_name, root / target_name
        options = {item["direction"]: item for item in _sync_options()}
        option = options.get(direction)
        if option is None:
            raise ValueError("direction must name two declared instruction sources")
        source_name = option["source"]
        target_name = option["target"]
        return root / source_name, root / target_name

    def _sync_snapshot(self, root: Path, direction: str) -> tuple[Path, Path, bytes, bytes | None]:
        source, target = self._sync_paths(root, direction)
        if source.is_symlink():
            raise ValueError(f"{source.name} is a symbolic link and cannot be copied")
        if not source.exists():
            raise ValueError(f"{source.name} is missing")
        if not source.is_file():
            raise ValueError(f"{source.name} is not a regular file")
        source_data = _bounded_bytes(source, label=source.name)
        _decode_text(source_data, label=source.name)
        target_data: bytes | None = None
        if target.is_symlink():
            raise ValueError(f"{target.name} is a symbolic link and cannot be replaced")
        if target.exists():
            if not target.is_file():
                raise ValueError(f"{target.name} is not a regular file")
            target_data = _bounded_bytes(target, label=target.name)
            _decode_text(target_data, label=target.name)
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

    def _create_backup(
        self, project_id: str, target: Path, entry: InstructionEntry
    ) -> dict[str, Any]:
        directory = self._project_backup_dir(project_id)
        if directory.is_symlink():
            raise ValueError("agent context backup directory cannot be a symbolic link")
        directory.mkdir(parents=True, exist_ok=True)
        backup_id = uuid4().hex
        manifest = {
            "id": backup_id,
            "target": target.name,
            "created_at": time.time(),
            "existed": entry.kind != "missing",
            "entry_kind": entry.kind,
            "revision": entry.revision,
            "size": len(entry.data) if entry.data is not None else 0,
            "mode": stat.S_IMODE(entry.mode) if entry.mode is not None else None,
        }
        if entry.link_target is not None:
            manifest["link_target"] = entry.link_target
        if entry.data is not None:
            self._atomic_write(directory / f"{backup_id}.bin", entry.data)
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
                    and item.get("target") in set(instruction_filenames())
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

    @staticmethod
    def _stage_symlink(path: Path, link_target: str) -> Path:
        temp = path.parent / f".agent-context-{uuid4().hex}.link"
        try:
            os.symlink(link_target, temp, target_is_directory=False)
        except OSError as exc:
            if temp.is_symlink() or temp.exists():
                temp.unlink()
            hint = (
                " Enable Windows Developer Mode or grant symbolic-link privilege."
                if os.name == "nt"
                else ""
            )
            raise ValueError(
                f"Could not create a symbolic link in {path.parent}.{hint} {exc}"
            ) from exc
        return temp

    @classmethod
    def _atomic_symlink(cls, path: Path, link_target: str) -> None:
        temp = cls._stage_symlink(path, link_target)
        try:
            os.replace(temp, path)
        finally:
            if temp.is_symlink() or temp.exists():
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
        target_entry = InstructionEntry(
            kind="regular" if target_data is not None else "missing",
            revision=_revision(target_data),
            data=target_data,
            mode=mode,
        )
        backup = self._create_backup(project_id, target, target_entry)
        self._atomic_write(target, output, mode=mode)
        return {
            "ok": True,
            "direction": direction,
            "source": source.name,
            "target": target.name,
            "revision": _revision(output),
            "backup": backup,
        }

    def _link_snapshot(
        self, root: Path, direction: str
    ) -> tuple[Path, Path, bytes, InstructionEntry, bool]:
        source, target = self._sync_paths(root, direction)
        source_entry = self._instruction_entry(root, source)
        if source_entry.kind != "regular" or source_entry.data is None:
            if source_entry.kind == "symlink":
                raise ValueError(
                    f"{source.name} is already a link; unlink it before making it canonical"
                )
            raise ValueError(f"{source.name} must be an existing regular file")
        _decode_text(source_entry.data, label=source.name)
        target_entry = self._instruction_entry(root, target)
        already_linked = target_entry.kind == "symlink" and target_entry.link_target == source.name
        if target_entry.kind == "symlink" and not already_linked:
            raise ValueError(f"{target.name} is linked to a different instruction file")
        if target_entry.data is not None:
            _decode_text(target_entry.data, label=target.name)
        return source, target, source_entry.data, target_entry, already_linked

    def preview_link(self, root: str | Path, direction: str) -> dict[str, Any]:
        project_root = Path(root).resolve()
        source, target, source_data, target_entry, already_linked = self._link_snapshot(
            project_root, direction
        )
        target_data = (
            source_data
            if already_linked
            else target_entry.data
            if target_entry.kind == "regular"
            else None
        )
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
            diff = diff[:MAX_DIFF_CHARS] + "\n... diff truncated ...\n"
        return {
            "direction": direction,
            "source": {"label": source.name, "revision": _revision(source_data)},
            "target": {"label": target.name, "revision": target_entry.revision},
            "already_linked": already_linked,
            "diff": diff,
            "diff_truncated": truncated,
        }

    def link(
        self,
        project_id: str,
        root: str | Path,
        direction: str,
        source_revision: str,
        target_revision: str,
    ) -> dict[str, Any]:
        project_root = Path(root).resolve()
        source, target, source_data, target_entry, already_linked = self._link_snapshot(
            project_root, direction
        )
        if _revision(source_data) != source_revision or target_entry.revision != target_revision:
            raise AgentContextConflict("instruction files changed since the link preview")
        if already_linked:
            raise ValueError(f"{target.name} already links to {source.name}")
        temp = self._stage_symlink(target, source.name)
        try:
            backup = self._create_backup(project_id, target, target_entry)
            os.replace(temp, target)
        finally:
            if temp.is_symlink() or temp.exists():
                temp.unlink()
        revision = _link_revision(source.name)
        return {
            "ok": True,
            "direction": direction,
            "source": source.name,
            "target": target.name,
            "revision": revision,
            "backup": backup,
        }

    def unlink(
        self,
        project_id: str,
        root: str | Path,
        source_id: str,
        target_revision: str,
    ) -> dict[str, Any]:
        if source_id not in INSTRUCTION_SOURCES:
            raise ValueError("source_id must name a Project instruction file")
        project_root = Path(root).resolve()
        target = project_root / INSTRUCTION_SOURCES[source_id][1]
        if not target.is_symlink():
            raise ValueError(f"{target.name} is not a managed instruction link")
        target_entry = self._instruction_entry(project_root, target)
        if target_entry.revision != target_revision:
            raise AgentContextConflict("instruction link changed before it was unlinked")
        _canonical_id, canonical, raw_target = self._managed_link_target(project_root, target)
        data = _bounded_bytes(canonical, label=raw_target)
        _decode_text(data, label=raw_target)
        backup = self._create_backup(project_id, target, target_entry)
        self._atomic_write(target, data, mode=canonical.stat().st_mode)
        return {
            "ok": True,
            "source_id": source_id,
            "target": target.name,
            "revision": _revision(data),
            "backup": backup,
        }

    def _backup_manifest(self, project_id: str, backup_id: str) -> dict[str, Any]:
        if not re.fullmatch(r"[0-9a-f]{32}", backup_id):
            raise ValueError("unknown agent context backup")
        path = self._project_backup_dir(project_id) / f"{backup_id}.json"
        try:
            raw: Any = json.loads(_bounded_bytes(path, label="backup manifest").decode("utf-8"))
        except (OSError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("unknown agent context backup") from exc
        if not isinstance(raw, dict):
            raise ValueError("invalid agent context backup")
        manifest: dict[str, Any] = {str(key): value for key, value in raw.items()}
        if manifest.get("id") != backup_id or manifest.get("target") not in set(
            instruction_filenames()
        ):
            raise ValueError("invalid agent context backup")
        return manifest

    def _backup_entry(self, project_id: str, manifest: dict[str, Any]) -> InstructionEntry:
        raw_kind = manifest.get("entry_kind")
        if raw_kind is None:
            raw_kind = "regular" if bool(manifest.get("existed")) else "missing"
        raw_mode = manifest.get("mode")
        mode = raw_mode if isinstance(raw_mode, int) else None
        if raw_kind == "missing":
            if manifest.get("revision") != "missing":
                raise ValueError("agent context backup is corrupt")
            return InstructionEntry(kind="missing", revision="missing")
        if raw_kind == "regular":
            backup_id = str(manifest["id"])
            data_path = self._project_backup_dir(project_id) / f"{backup_id}.bin"
            data = _bounded_bytes(data_path, label="backup")
            if _revision(data) != manifest.get("revision"):
                raise ValueError("agent context backup is corrupt")
            return InstructionEntry(
                kind="regular",
                revision=_revision(data),
                data=data,
                mode=mode,
            )
        if raw_kind == "symlink":
            link_target = manifest.get("link_target")
            target_name = str(manifest.get("target") or "")
            if (
                not isinstance(link_target, str)
                or _instruction_id_for_filename(link_target) is None
                or link_target == target_name
                or _link_revision(link_target) != manifest.get("revision")
            ):
                raise ValueError("agent context backup is corrupt")
            return InstructionEntry(
                kind="symlink",
                revision=_link_revision(link_target),
                link_target=link_target,
            )
        raise ValueError("agent context backup has an invalid entry kind")

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
        current = self._instruction_entry(project_root, target)
        if current.revision != target_revision:
            raise AgentContextConflict("instruction file changed before the backup was restored")
        desired = self._backup_entry(project_id, manifest)
        staged_link: Path | None = None
        if desired.kind == "symlink" and desired.link_target is not None:
            canonical = project_root / desired.link_target
            if canonical.is_symlink() or not canonical.is_file():
                raise ValueError(
                    f"Cannot restore {target.name}: {desired.link_target} is not a regular file"
                )
            staged_link = self._stage_symlink(target, desired.link_target)
        try:
            undo = self._create_backup(project_id, target, current)
            if desired.kind == "regular" and desired.data is not None:
                mode = desired.mode if desired.mode is not None else current.mode
                self._atomic_write(target, desired.data, mode=mode)
            elif desired.kind == "symlink" and staged_link is not None:
                os.replace(staged_link, target)
            else:
                if target.is_symlink() or target.exists():
                    target.unlink()
        finally:
            if staged_link is not None and (staged_link.is_symlink() or staged_link.exists()):
                staged_link.unlink()
        revision = desired.revision
        return {
            "ok": True,
            "target": target.name,
            "revision": revision,
            "backup": undo,
        }


def instruction_filenames() -> Iterable[str]:
    """Expose the tiny allowlist for focused tests and documentation tooling."""

    return tuple(dict.fromkeys(item[1] for item in INSTRUCTION_SOURCES.values()))
