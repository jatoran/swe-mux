from __future__ import annotations

import hashlib
import json
import os
import re
import tomllib
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .projects import ProjectIdentity, resolve_project

PROJECT_CONFIG_VERSION = 1
PROJECT_CONFIG_FIELDS = {
    "project_label", "default_cwd", "default_shell_profile", "notes_enabled"
}
FORBIDDEN_PROJECT_FIELDS = {
    "token", "bind", "host", "port", "data_dir", "hooks", "executable", "command",
}
NOTE_KINDS = {"spaces", "sessions"}
_SAFE_ID = re.compile(r"[A-Za-z0-9._-]{1,120}\Z")


def revision(data: bytes | None) -> str:
    return hashlib.sha256(data or b"").hexdigest()[:24] if data is not None else "missing"


def safe_note_filename(identity: str) -> str:
    if _SAFE_ID.fullmatch(identity):
        return identity
    return f"id-{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:24]}"


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
    if "default_cwd" in parsed:
        if not isinstance(parsed["default_cwd"], str):
            raise ValueError("default_cwd must be a string")
        default_cwd = Path(parsed["default_cwd"])
        if default_cwd.is_absolute() or ".." in default_cwd.parts:
            raise ValueError("default_cwd must stay relative to the project root")
    if "default_shell_profile" in parsed and not isinstance(
        parsed["default_shell_profile"], str
    ):
        raise ValueError("default_shell_profile must be a string")
    if "notes_enabled" in parsed and not isinstance(parsed["notes_enabled"], bool):
        raise ValueError("notes_enabled must be true or false")
    if "project_label" in parsed and (
        not isinstance(parsed["project_label"], str)
        or not parsed["project_label"].strip()
        or len(parsed["project_label"]) > 120
    ):
        raise ValueError("project_label must be a non-empty string up to 120 characters")
    return parsed


def serialize_project_config(values: dict[str, Any]) -> bytes:
    invalid = sorted(set(values) - PROJECT_CONFIG_FIELDS)
    if invalid:
        raise ValueError(f"unknown project fields: {', '.join(invalid)}")
    lines = [f"version = {PROJECT_CONFIG_VERSION}"]
    for key in ("project_label", "default_cwd", "default_shell_profile"):
        if value := values.get(key):
            escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
            lines.append(f'{key} = "{escaped}"')
    if "notes_enabled" in values:
        lines.append(f"notes_enabled = {'true' if values['notes_enabled'] else 'false'}")
    data = ("\n".join(lines) + "\n").encode("utf-8")
    parse_project_config(data)
    return data


def resolve_project_default_cwd(project_root: str | Path, relative_cwd: str) -> Path:
    root = Path(project_root).resolve()
    relative = Path(relative_cwd)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("default_cwd must stay relative to the project root")
    candidate = (root / relative).resolve()
    if not candidate.is_relative_to(root):
        raise ValueError("default_cwd resolves outside the project root")
    return candidate


async def read_project_config(cwd: str | Path) -> dict[str, Any]:
    project, mux_dir = await project_status(cwd)
    path = mux_dir / "config.toml"
    if not path.exists():
        return {
            "project": asdict(project), "path": str(path), "exists": False,
            "revision": "missing", "values": {}, "status": "missing",
        }
    try:
        data = path.read_bytes()
        values = parse_project_config(data)
        status = "ready" if os.access(path, os.W_OK) else "read-only"
        return {
            "project": asdict(project), "path": str(path), "exists": True,
            "revision": revision(data), "values": values, "status": status,
        }
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError, ValueError) as exc:
        try:
            current_data = path.read_bytes() if path.exists() else None
        except OSError:
            current_data = None
        return {
            "project": asdict(project), "path": str(path), "exists": True,
            "revision": revision(current_data),
            "values": {}, "status": "malformed", "error": str(exc),
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
        raise ValueError("note kind must be spaces or sessions")
    project, mux_dir = await project_status(cwd)
    filename = f"{safe_note_filename(identity)}.md"
    path = mux_dir / "notes" / kind / filename
    if not path.exists():
        return {
            "project": asdict(project), "kind": kind, "id": identity,
            "path": str(path), "exists": False, "revision": "missing", "markdown": "",
            "status": "missing",
        }
    try:
        data = path.read_bytes()
        markdown = _note_body(data.decode("utf-8"))
        status = "ready" if os.access(path, os.W_OK) else "read-only"
        return {
            "project": asdict(project), "kind": kind, "id": identity,
            "path": str(path), "exists": True, "revision": revision(data),
            "markdown": markdown, "status": status,
        }
    except (OSError, UnicodeDecodeError) as exc:
        return {
            "project": asdict(project), "kind": kind, "id": identity,
            "path": str(path), "exists": True, "revision": "unreadable", "markdown": "",
            "status": "malformed", "error": str(exc),
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
    header = (
        "---\nswe_mux_note = 1\n"
        f"kind = {json.dumps(kind)}\nid = {json.dumps(identity)}\n---\n"
    )
    body = markdown
    if not markdown.startswith("---\nswe_mux_note = 1\n"):
        body = header + markdown
    _atomic_write(Path(current["path"]), body.encode("utf-8"))
    return await read_note(cwd, kind, identity)


async def search_notes(cwd: str | Path, query: str) -> list[dict[str, Any]]:
    _, mux_dir = await project_status(cwd)
    root = mux_dir / "notes"
    if not root.exists():
        return []
    needle = query.casefold()
    results: list[dict[str, Any]] = []
    for path in sorted(root.glob("*/*.md"))[:1000]:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if needle in text.casefold():
            excerpt = next(
                (line for line in text.splitlines() if needle in line.casefold()), ""
            )[:240]
            identity = path.stem
            if match := re.search(r"^id = (.+)$", text, re.MULTILINE):
                try:
                    parsed_identity = json.loads(match.group(1))
                    if isinstance(parsed_identity, str):
                        identity = parsed_identity
                except json.JSONDecodeError:
                    pass
            results.append(
                {
                    "kind": path.parent.name,
                    "id": identity,
                    "filename": path.name,
                    "path": str(path),
                    "excerpt": excerpt,
                }
            )
    return results[:100]
