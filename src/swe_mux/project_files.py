from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import tomllib
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .git_projects import ProjectIdentity, resolve_project

PROJECT_CONFIG_VERSION = 1
PROJECT_CONFIG_FIELDS = {
    "default_shell_profile",
    "preferred_backend",
    "resource_open_mode",
    "prompt_library_scope",
    "notification_sounds_enabled",
    "ignore_patterns",
}
FORBIDDEN_PROJECT_FIELDS = {
    "token",
    "bind",
    "host",
    "port",
    "data_dir",
    "hooks",
    "executable",
    "command",
}
NOTE_KINDS = {"projects", "sessions"}
_SAFE_NOTE_ID = re.compile(r"[A-Za-z0-9._-]{1,120}\Z")


def revision(data: bytes | None) -> str:
    return hashlib.sha256(data or b"").hexdigest()[:24] if data is not None else "missing"


def safe_note_filename(identity: str) -> str:
    if _SAFE_NOTE_ID.fullmatch(identity):
        return identity
    if not identity or len(identity) > 512 or any(ord(character) < 32 for character in identity):
        raise ValueError("note identity must be a non-empty printable string up to 512 characters")
    return f"id-{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:24]}"


def note_path(root: str | Path, kind: str, identity: str) -> Path:
    if kind not in NOTE_KINDS:
        raise ValueError("note kind must be projects or sessions")
    mux_dir = Path(root).resolve() / ".swe-mux" / "notes"
    if kind == "projects":
        return mux_dir / "project.md"
    return mux_dir / "sessions" / f"{safe_note_filename(identity)}.md"


def note_exists(root: str | Path, kind: str, identity: str) -> bool:
    return note_path(root, kind, identity).is_file()


def _note_body(text: str) -> str:
    if not text.startswith("---\nswe_mux_note = 1\n"):
        return text
    boundary = text.find("\n---\n", 4)
    return text[boundary + 5 :] if boundary >= 0 else text


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_bytes(data)
    os.replace(temporary, path)


def _create_note_file(path: Path, header: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(header)
    except FileExistsError:
        pass


def project_path(root: str | Path, relative_path: str = "") -> Path:
    project_root = Path(root).resolve()
    relative = Path(relative_path.replace("\\", "/"))
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("project file path must stay inside the project root")
    target = (project_root / relative).resolve()
    if target != project_root and not target.is_relative_to(project_root):
        raise ValueError("project file path resolves outside the project root")
    return target


def ignored_project_path(relative_path: str | Path, patterns: Sequence[str]) -> bool:
    normalized = Path(str(relative_path).replace("\\", "/")).as_posix().strip("/")
    if not normalized:
        return False
    parts = normalized.split("/")
    for raw_pattern in patterns:
        pattern = raw_pattern.strip().replace("\\", "/").strip("/")
        if not pattern:
            continue
        if "/" in pattern:
            if fnmatch.fnmatchcase(normalized, pattern) or fnmatch.fnmatchcase(
                normalized, f"*/{pattern}"
            ):
                return True
        elif any(fnmatch.fnmatchcase(part, pattern) for part in parts):
            return True
    return False


def effective_project_ignores(root: str | Path, global_patterns: list[str]) -> list[str]:
    patterns = list(global_patterns)
    path = Path(root) / ".swe-mux" / "config.toml"
    try:
        if path.is_file():
            values = parse_project_config(path.read_bytes())
            patterns.extend(values.get("ignore_patterns", []))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError, ValueError):
        pass
    return list(dict.fromkeys(pattern.strip() for pattern in patterns if pattern.strip()))


def list_project_directory(
    root: str | Path, relative_path: str = "", *, ignore_patterns: list[str] | None = None
) -> dict[str, Any]:
    project_root = Path(root).resolve()
    target = project_path(project_root, relative_path)
    if not target.is_dir():
        raise ValueError("project path is not a folder")
    items: list[dict[str, Any]] = []
    try:
        children = sorted(
            target.iterdir(), key=lambda item: (not item.is_dir(), item.name.casefold())
        )
    except OSError as exc:
        raise ValueError(f"unable to browse project folder: {exc}") from exc
    visible_children = [
        child
        for child in children
        if not ignored_project_path(
            child.relative_to(project_root).as_posix(), ignore_patterns or [".git"]
        )
    ]
    for child in visible_children[:2000]:
        relative = child.relative_to(project_root).as_posix()
        try:
            is_dir = child.is_dir()
            size = None if is_dir else child.stat().st_size
        except OSError:
            continue
        items.append(
            {
                "name": child.name,
                "path": relative,
                "kind": "directory" if is_dir else "file",
                "size": size,
            }
        )
    parent = None if target == project_root else target.parent.relative_to(project_root).as_posix()
    return {
        "path": target.relative_to(project_root).as_posix() if target != project_root else "",
        "parent": parent,
        "items": items,
        "truncated": len(visible_children) > 2000,
    }


def read_project_file(root: str | Path, relative_path: str) -> dict[str, Any]:
    target = project_path(root, relative_path)
    if not target.is_file():
        raise ValueError("project file does not exist")
    data = target.read_bytes()
    if len(data) > 2 * 1024 * 1024:
        return {
            "path": relative_path,
            "revision": revision(data),
            "status": "too-large",
            "size": len(data),
        }
    if b"\x00" in data:
        return {
            "path": relative_path,
            "revision": revision(data),
            "status": "binary",
            "size": len(data),
        }
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return {
            "path": relative_path,
            "revision": revision(data),
            "status": "binary",
            "size": len(data),
        }
    return {
        "path": target.relative_to(Path(root).resolve()).as_posix(),
        "revision": revision(data),
        "status": "ready" if os.access(target, os.W_OK) else "read-only",
        "size": len(data),
        "text": text,
    }


def write_project_file(
    root: str | Path, relative_path: str, text: str, expected_revision: str
) -> dict[str, Any]:
    data = text.encode("utf-8")
    if len(data) > 2 * 1024 * 1024:
        raise ValueError("project file exceeds the 2 MiB editor limit")
    target = project_path(root, relative_path)
    if not target.is_file():
        raise ValueError("project file does not exist")
    current = target.read_bytes()
    if revision(current) != expected_revision:
        raise ValueError("project file changed externally; reload before saving")
    _atomic_write(target, data)
    return read_project_file(root, relative_path)


async def project_status(cwd: str | Path) -> tuple[ProjectIdentity, Path]:
    project = await resolve_project(cwd)
    return project, Path(project.root) / ".swe-mux"


def parse_project_config(data: bytes) -> dict[str, Any]:
    parsed = tomllib.loads(data.decode("utf-8"))
    version = parsed.pop("version", None)
    if version != PROJECT_CONFIG_VERSION:
        raise ValueError(f"project config version must be {PROJECT_CONFIG_VERSION}")
    forbidden = sorted(set(parsed) & FORBIDDEN_PROJECT_FIELDS)
    unknown = sorted(set(parsed) - PROJECT_CONFIG_FIELDS)
    if forbidden:
        raise ValueError(f"forbidden project fields: {', '.join(forbidden)}")
    if unknown:
        raise ValueError(f"unknown project fields: {', '.join(unknown)}")
    if "default_shell_profile" in parsed and not isinstance(parsed["default_shell_profile"], str):
        raise ValueError("default_shell_profile must be a string")
    if parsed.get("preferred_backend") not in {None, "shell", "claude", "codex"}:
        raise ValueError("preferred_backend must be shell, claude, or codex")
    if parsed.get("resource_open_mode") not in {None, "dock", "popout"}:
        raise ValueError("resource_open_mode must be dock or popout")
    if parsed.get("prompt_library_scope") not in {None, "off", "global", "project", "both"}:
        raise ValueError("prompt_library_scope must be off, global, project, or both")
    if "notification_sounds_enabled" in parsed and not isinstance(
        parsed["notification_sounds_enabled"], bool
    ):
        raise ValueError("notification_sounds_enabled must be a boolean")
    if "ignore_patterns" in parsed and (
        not isinstance(parsed["ignore_patterns"], list)
        or not all(isinstance(item, str) for item in parsed["ignore_patterns"])
        or len(parsed["ignore_patterns"]) > 256
        or any(not item.strip() or len(item) > 200 for item in parsed["ignore_patterns"])
    ):
        raise ValueError("ignore_patterns must be an array of at most 256 non-empty strings")
    return parsed


def serialize_project_config(values: dict[str, Any]) -> bytes:
    invalid = sorted(set(values) - PROJECT_CONFIG_FIELDS)
    if invalid:
        raise ValueError(f"unknown project fields: {', '.join(invalid)}")
    lines = [f"version = {PROJECT_CONFIG_VERSION}"]
    for key in (
        "default_shell_profile",
        "preferred_backend",
        "resource_open_mode",
        "prompt_library_scope",
    ):
        if value := values.get(key):
            escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
            lines.append(f'{key} = "{escaped}"')
    if "notification_sounds_enabled" in values:
        lines.append(
            "notification_sounds_enabled = "
            + ("true" if values["notification_sounds_enabled"] else "false")
        )
    if patterns := values.get("ignore_patterns"):
        encoded = ", ".join(json.dumps(str(pattern)) for pattern in patterns)
        lines.append(f"ignore_patterns = [{encoded}]")
    data = ("\n".join(lines) + "\n").encode("utf-8")
    parse_project_config(data)
    return data


async def read_project_config(
    cwd: str | Path, *, project: ProjectIdentity | None = None
) -> dict[str, Any]:
    if project is None:
        project, mux_dir = await project_status(cwd)
    else:
        mux_dir = Path(project.root) / ".swe-mux"
    path = mux_dir / "config.toml"
    if not path.exists():
        return {
            "project": asdict(project),
            "path": str(path),
            "exists": False,
            "revision": "missing",
            "values": {},
            "status": "missing",
        }
    try:
        data = path.read_bytes()
        values = parse_project_config(data)
        status = "ready" if os.access(path, os.W_OK) else "read-only"
        return {
            "project": asdict(project),
            "path": str(path),
            "exists": True,
            "revision": revision(data),
            "values": values,
            "status": status,
        }
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError, ValueError) as exc:
        try:
            current_data = path.read_bytes() if path.exists() else None
        except OSError:
            current_data = None
        return {
            "project": asdict(project),
            "path": str(path),
            "exists": True,
            "revision": revision(current_data),
            "values": {},
            "status": "malformed",
            "error": str(exc),
        }


async def write_project_config(
    cwd: str | Path, values: dict[str, Any], expected_revision: str
) -> dict[str, Any]:
    current = await read_project_config(cwd)
    if current["revision"] != expected_revision:
        raise ValueError("project config changed externally; reload before saving")
    data = serialize_project_config(values)
    _atomic_write(Path(current["path"]), data)
    return await read_project_config(cwd)


async def read_note(cwd: str | Path, kind: str, identity: str) -> dict[str, Any]:
    if kind not in NOTE_KINDS:
        raise ValueError("note kind must be projects or sessions")
    project, mux_dir = await project_status(cwd)
    path = (
        mux_dir / "notes" / "project.md"
        if kind == "projects"
        else mux_dir / "notes" / "sessions" / f"{safe_note_filename(identity)}.md"
    )
    if not path.exists():
        return {
            "project": asdict(project),
            "kind": kind,
            "id": identity,
            "path": str(path),
            "exists": False,
            "revision": "missing",
            "markdown": "",
            "status": "missing",
        }
    try:
        data = path.read_bytes()
        markdown = _note_body(data.decode("utf-8"))
        status = "ready" if os.access(path, os.W_OK) else "read-only"
        return {
            "project": asdict(project),
            "kind": kind,
            "id": identity,
            "path": str(path),
            "exists": True,
            "revision": revision(data),
            "markdown": markdown,
            "status": status,
        }
    except (OSError, UnicodeDecodeError) as exc:
        return {
            "project": asdict(project),
            "kind": kind,
            "id": identity,
            "path": str(path),
            "exists": True,
            "revision": "unreadable",
            "markdown": "",
            "status": "malformed",
            "error": str(exc),
        }


async def write_note(
    cwd: str | Path,
    kind: str,
    identity: str,
    markdown: str,
    expected_revision: str,
) -> dict[str, Any]:
    if len(markdown.encode("utf-8")) > 1024 * 1024:
        raise ValueError("note exceeds the 1 MiB limit")
    current = await read_note(cwd, kind, identity)
    if current["revision"] != expected_revision:
        raise ValueError("note changed externally; reload before saving")
    header = f"---\nswe_mux_note = 1\nkind = {json.dumps(kind)}\nid = {json.dumps(identity)}\n---\n"
    body = markdown
    if not markdown.startswith("---\nswe_mux_note = 1\n"):
        body = header + markdown
    _atomic_write(Path(current["path"]), body.encode("utf-8"))
    return await read_note(cwd, kind, identity)


async def initialize_note(cwd: str | Path, kind: str, identity: str) -> dict[str, Any]:
    """Create an empty durable note without overwriting an existing note."""
    current = await read_note(cwd, kind, identity)
    if current["exists"]:
        return current
    path = Path(current["path"])
    header = f"---\nswe_mux_note = 1\nkind = {json.dumps(kind)}\nid = {json.dumps(identity)}\n---\n"
    _create_note_file(path, header)
    return await read_note(cwd, kind, identity)
