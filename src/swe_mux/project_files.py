from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import time
import tomllib
import uuid
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .automation_registry import REGISTRY as AUTOMATION_REGISTRY
from .git_projects import ProjectIdentity, rebase_identity, resolve_project

PROJECT_CONFIG_VERSION = 1
PROJECT_CONFIG_FIELDS = {
    "default_shell_profile",
    "preferred_backend",
    "resource_open_mode",
    "prompt_library_scope",
    "notification_sounds_enabled",
    "ignore_patterns",
    "automations",
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


_NOTE_CONTENT_CACHE: dict[str, tuple[int, int, bool]] = {}


def note_has_content(root: str | Path, kind: str, identity: str) -> bool:
    """Report whether a note holds text the user actually wrote.

    A one-click note affordance creates files on stray clicks. Presence alone
    would then pin a permanent sidebar row per terminal, so the browser signal
    is content, not existence. Authorization still uses `note_exists`: an empty
    note must remain readable and writable.

    Session listing is a polled path, so the answer is memoized against the
    note's mtime and size and only re-read when the file actually changes.
    """
    path = note_path(root, kind, identity)
    try:
        stat = path.stat()
    except OSError:
        _NOTE_CONTENT_CACHE.pop(str(path), None)
        return False
    key = str(path)
    signature = (stat.st_mtime_ns, stat.st_size)
    cached = _NOTE_CONTENT_CACHE.get(key)
    if cached and cached[:2] == signature:
        return cached[2]
    try:
        body = _note_body(path.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return False
    result = bool(body.strip())
    if len(_NOTE_CONTENT_CACHE) > 4096:
        _NOTE_CONTENT_CACHE.clear()
    _NOTE_CONTENT_CACHE[key] = (*signature, result)
    return result


MAX_SESSION_NOTE_SUMMARIES = 500
SESSION_NOTE_EXCERPT_CHARS = 240


def session_note_summaries(root: str | Path) -> list[dict[str, Any]]:
    """Summarize every session note in a Project that holds real text.

    The browser lists notes from the filesystem rather than from history, so a
    note survives its owning session, its history row, and daemon restarts. The
    file stem is the note identity: `safe_note_filename` is idempotent over the
    names it produces, so a hashed stem round-trips back to the same file.
    """
    directory = Path(root).resolve() / ".swe-mux" / "notes" / "sessions"
    result: list[dict[str, Any]] = []
    try:
        entries = sorted(
            directory.glob("*.md"), key=lambda item: item.stat().st_mtime, reverse=True
        )
    except OSError:
        return []
    for path in entries[:MAX_SESSION_NOTE_SUMMARIES]:
        try:
            stat = path.stat()
            body = _note_body(path.read_text(encoding="utf-8", errors="replace")).strip()
        except OSError:
            continue
        if not body:
            continue
        excerpt = " ".join(body.split())
        result.append(
            {
                "note_id": path.stem,
                "updated_at": stat.st_mtime,
                "bytes": stat.st_size,
                "excerpt": excerpt[:SESSION_NOTE_EXCERPT_CHARS],
            }
        )
    return result


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


def project_automations(root: str | Path) -> dict[str, bool]:
    """Read a project's explicit automation opt-ins, or an empty map."""
    path = Path(root) / ".swe-mux" / "config.toml"
    try:
        if path.is_file():
            values = parse_project_config(path.read_bytes())
            automations = values.get("automations")
            if isinstance(automations, dict):
                return {str(key): bool(value) for key, value in automations.items()}
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError, ValueError):
        pass
    return {}


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


def list_project_directories(
    root: str | Path,
    relative_paths: Sequence[str],
    *,
    ignore_patterns: list[str] | None = None,
) -> dict[str, Any]:
    """List several folders in one call so a restored tree loads in one round trip.

    A path that no longer resolves to a folder (deleted, renamed, or now a file)
    is silently omitted rather than raising: the browser treats a missing entry
    as "this folder is gone" and prunes it from the persisted expand state, which
    is exactly the self-healing behaviour we want for a stale saved tree.
    """

    project_root = Path(root).resolve()
    directories: dict[str, Any] = {}
    for relative in dict.fromkeys(relative_paths):
        try:
            directories[relative] = list_project_directory(
                project_root, relative, ignore_patterns=ignore_patterns
            )
        except ValueError:
            continue
    return {"directories": directories}


def search_project_files(
    root: str | Path,
    query: str,
    *,
    mode: str = "names",
    ignore_patterns: list[str] | None = None,
    limit: int = 300,
) -> dict[str, Any]:
    """Recursively find files by name and/or UTF-8 content beneath the project root.

    The walk reuses the same ignore rules as the browser, prunes ignored directories, and is
    bounded on every axis (files visited, bytes read, per-file size, and result count) so a
    huge tree cannot stall the daemon. Content matching skips binary and oversized files and
    reports the first matching line as a trimmed snippet. Name matches sort before content
    matches; within each group results are path-ordered.
    """
    project_root = Path(root).resolve()
    needle = query.strip().casefold()
    if not needle:
        return {"items": [], "truncated": False}
    ignore = ignore_patterns or [".git"]
    want_names = mode in ("names", "both")
    want_contents = mode in ("contents", "both")
    max_files = 20000
    max_bytes = 64 * 1024 * 1024
    per_file_bytes = 1024 * 1024
    items: list[dict[str, Any]] = []
    truncated = False
    scanned_files = 0
    scanned_bytes = 0
    for dirpath, dirnames, filenames in os.walk(project_root):
        base = Path(dirpath).relative_to(project_root)
        prefix = "" if base == Path(".") else f"{base.as_posix()}/"
        dirnames[:] = [
            name
            for name in sorted(dirnames, key=str.casefold)
            if not ignored_project_path(f"{prefix}{name}", ignore)
        ]
        for name in sorted(filenames, key=str.casefold):
            relative = f"{prefix}{name}"
            if ignored_project_path(relative, ignore):
                continue
            scanned_files += 1
            if scanned_files > max_files:
                truncated = True
                break
            match: str | None = None
            line: int | None = None
            snippet: str | None = None
            if want_names and needle in name.casefold():
                match = "name"
            if match is None and want_contents and scanned_bytes < max_bytes:
                target = project_root / relative
                try:
                    if target.stat().st_size <= per_file_bytes:
                        data = target.read_bytes()
                        scanned_bytes += len(data)
                        if b"\x00" not in data:
                            text = data.decode("utf-8")
                            index = text.casefold().find(needle)
                            if index != -1:
                                match = "content"
                                line = text.count("\n", 0, index) + 1
                                start = text.rfind("\n", 0, index) + 1
                                end = text.find("\n", index)
                                stop = end if end != -1 else len(text)
                                snippet = text[start:stop].strip()[:200]
                except (OSError, UnicodeDecodeError):
                    match = None
            if match is not None:
                items.append(
                    {
                        "name": name,
                        "path": relative,
                        "match": match,
                        "line": line,
                        "snippet": snippet,
                    }
                )
                if len(items) >= limit:
                    truncated = True
                    break
        if truncated:
            break
    items.sort(key=lambda item: (item["match"] != "name", item["path"].casefold()))
    return {"items": items, "truncated": truncated}


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


async def project_status(
    cwd: str | Path, *, canonical_root: str | Path | None = None
) -> tuple[ProjectIdentity, Path]:
    """Resolve the identity to report and the ``.swe-mux`` directory to use.

    ``canonical_root`` is the explicit Project root when the caller already knows
    which Project owns the request. It is authoritative for paths: without it, a
    Project registered inside a larger worktree re-resolves to the enclosing
    toplevel and reads/writes that Project's notes, config, and observations.
    """
    project = await resolve_project(cwd)
    if canonical_root is not None:
        project = rebase_identity(project, canonical_root)
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
    if "automations" in parsed:
        automations = parsed["automations"]
        if not isinstance(automations, dict) or not all(
            isinstance(value, bool) for value in automations.values()
        ):
            raise ValueError("automations must be a table of boolean opt-ins")
        unknown_automations = sorted(set(automations) - set(AUTOMATION_REGISTRY))
        if unknown_automations:
            raise ValueError(f"unknown automations: {', '.join(unknown_automations)}")
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
    if automations := values.get("automations"):
        pairs = ", ".join(
            f"{key} = {'true' if bool(value) else 'false'}"
            for key, value in sorted(automations.items())
        )
        lines.append(f"automations = {{ {pairs} }}")
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
    cwd: str | Path,
    values: dict[str, Any],
    expected_revision: str,
    *,
    project: ProjectIdentity | None = None,
) -> dict[str, Any]:
    current = await read_project_config(cwd, project=project)
    if current["revision"] != expected_revision:
        raise ValueError("project config changed externally; reload before saving")
    data = serialize_project_config(values)
    _atomic_write(Path(current["path"]), data)
    return await read_project_config(cwd, project=project)


OBSERVATIONS_VERSION = 1
MAX_OBSERVATIONS = 500
MAX_OBSERVATION_CHARS = 2000
# Phase 5: an inbox item may carry a typed, inert *request* alongside its human
# summary line — today only `mux.requestSpawn` (`ROADMAP.md` Phase 5,
# `CONTROL_PLANE_ROADMAP.md` §7.2/§16). The item is text until a human approves
# it; nothing here starts anything.
OBSERVATION_KINDS = ("note", "spawn_request")
MAX_SPAWN_REQUEST_PROMPT = 8000
_REQUEST_STRING_FIELDS = (
    "prompt",
    "backend",
    "name",
    "reason",
    "cwd",
    "from_session",
    "from_name",
    "from_run_id",
    "project_id",
    "status",
    "session_id",
    "decided_by",
)


class ObservationsUnreadableError(ValueError):
    """The inbox file exists but cannot be parsed.

    Distinct from "missing" on purpose: an unparseable file (hand edit, merge
    conflict markers) read as an empty list means the next captured note rewrites
    it with one item and silently discards every prior observation.
    """


def _load_observations(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ObservationsUnreadableError(f"observations.json is unreadable: {exc}") from exc
    items = parsed.get("observations") if isinstance(parsed, dict) else None
    if not isinstance(items, list):
        raise ObservationsUnreadableError("observations.json has no observations list")
    result: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            continue
        entry: dict[str, Any] = {
            "id": item["id"],
            "body": str(item.get("body") or "")[:MAX_OBSERVATION_CHARS],
            "done": bool(item.get("done")),
            "created_at": float(item.get("created_at") or 0.0),
        }
        kind = str(item.get("kind") or "note")
        if kind in OBSERVATION_KINDS and kind != "note":
            entry["kind"] = kind
            entry["request"] = _clean_request(item.get("request"))
        result.append(entry)
    return result


def _clean_request(request: Any) -> dict[str, Any]:
    """Normalize a typed request payload; unknown keys are dropped, not trusted."""
    source = request if isinstance(request, dict) else {}
    cleaned: dict[str, Any] = {}
    for key in _REQUEST_STRING_FIELDS:
        value = source.get(key)
        if isinstance(value, str) and value:
            cleaned[key] = value[:MAX_SPAWN_REQUEST_PROMPT]
    created = source.get("created_at")
    if isinstance(created, int | float):
        cleaned["created_at"] = float(created)
    cleaned.setdefault("status", "pending")
    return cleaned


def _validate_observations(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(items, list) or len(items) > MAX_OBSERVATIONS:
        raise ValueError(f"observations must be a list of at most {MAX_OBSERVATIONS} items")
    cleaned: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("each observation must be an object")
        identity = item.get("id")
        body = item.get("body")
        if not isinstance(identity, str) or not _SAFE_NOTE_ID.fullmatch(identity):
            raise ValueError("observation id must be a short safe token")
        if identity in seen:
            raise ValueError("observation ids must be unique")
        seen.add(identity)
        if not isinstance(body, str) or not body.strip() or len(body) > MAX_OBSERVATION_CHARS:
            raise ValueError(f"observation body must be 1–{MAX_OBSERVATION_CHARS} characters")
        entry: dict[str, Any] = {
            "id": identity,
            "body": body,
            "done": bool(item.get("done")),
            "created_at": float(item.get("created_at") or 0.0),
        }
        kind = str(item.get("kind") or "note")
        if kind not in OBSERVATION_KINDS:
            raise ValueError(f"observation kind must be one of {', '.join(OBSERVATION_KINDS)}")
        if kind != "note":
            entry["kind"] = kind
            entry["request"] = _clean_request(item.get("request"))
        cleaned.append(entry)
    return cleaned


def _serialize_observations(items: list[dict[str, Any]]) -> bytes:
    return (
        json.dumps({"version": OBSERVATIONS_VERSION, "observations": items}, indent=2) + "\n"
    ).encode("utf-8")


async def read_observations(
    cwd: str | Path, *, project: ProjectIdentity | None = None
) -> dict[str, Any]:
    """Read a Project's capture inbox — lightweight notes-to-self dropped while testing."""
    if project is None:
        project, mux_dir = await project_status(cwd)
    else:
        mux_dir = Path(project.root) / ".swe-mux"
    path = mux_dir / "observations.json"
    try:
        data = path.read_bytes() if path.is_file() else None
    except OSError:
        data = None
    try:
        observations = _load_observations(path)
    except ObservationsUnreadableError as exc:
        return {
            "project": asdict(project),
            "path": str(path),
            "exists": True,
            "revision": revision(data),
            "observations": [],
            "status": "malformed",
            "error": str(exc),
        }
    return {
        "project": asdict(project),
        "path": str(path),
        "exists": path.is_file(),
        "revision": revision(data),
        "observations": observations,
        "status": "ready",
    }


async def append_observation(
    cwd: str | Path,
    body: str,
    *,
    project: ProjectIdentity | None = None,
    kind: str = "note",
    request: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Append one observation. Append-only capture is conflict-free, so no revision check.

    ``kind``/``request`` carry a typed, inert draft (Phase 5
    ``mux.requestSpawn``). The item is still just a row in the user's own inbox
    until a human approves it.
    """
    text = body.strip()
    if not text:
        raise ValueError("observation body must not be empty")
    if len(text) > MAX_OBSERVATION_CHARS:
        raise ValueError(f"observation body must be {MAX_OBSERVATION_CHARS} characters or fewer")
    if kind not in OBSERVATION_KINDS:
        raise ValueError(f"observation kind must be one of {', '.join(OBSERVATION_KINDS)}")
    if project is None:
        project, mux_dir = await project_status(cwd)
    else:
        mux_dir = Path(project.root) / ".swe-mux"
    path = mux_dir / "observations.json"
    current = _load_observations(path)
    if len(current) >= MAX_OBSERVATIONS:
        raise ValueError(f"observation inbox is full ({MAX_OBSERVATIONS} items); clear some first")
    identity = uuid.uuid4().hex[:16]
    entry: dict[str, Any] = {
        "id": identity,
        "body": text,
        "done": False,
        "created_at": time.time(),
    }
    if kind != "note":
        entry["kind"] = kind
        entry["request"] = _clean_request({**(request or {}), "created_at": time.time()})
    current.append(entry)
    _atomic_write(path, _serialize_observations(_validate_observations(current)))
    result = await read_observations(cwd, project=project)
    result["appended_id"] = identity
    return result


async def update_observation_request(
    cwd: str | Path,
    observation_id: str,
    patch: dict[str, Any],
    *,
    done: bool | None = None,
    project: ProjectIdentity | None = None,
) -> dict[str, Any]:
    """Record the outcome of a typed request (approved/dismissed) in place.

    Deliberately not revision-checked: this is the daemon writing the result of
    an act it just performed, and losing that record to a concurrent edit of an
    unrelated note would leave an approved request looking pending.
    """
    if project is None:
        project, mux_dir = await project_status(cwd)
    else:
        mux_dir = Path(project.root) / ".swe-mux"
    path = mux_dir / "observations.json"
    current = _load_observations(path)
    found = False
    for item in current:
        if item.get("id") != observation_id:
            continue
        found = True
        item["request"] = _clean_request({**(item.get("request") or {}), **patch})
        if done is not None:
            item["done"] = done
    if not found:
        raise ValueError("no such observation")
    _atomic_write(path, _serialize_observations(_validate_observations(current)))
    return await read_observations(cwd, project=project)


async def write_observations(
    cwd: str | Path,
    observations: list[dict[str, Any]],
    expected_revision: str,
    *,
    project: ProjectIdentity | None = None,
) -> dict[str, Any]:
    """Replace the whole inbox (toggle done, delete, reorder) with a revision check."""
    current = await read_observations(cwd, project=project)
    if current.get("status") == "malformed":
        raise ObservationsUnreadableError(str(current.get("error") or "observations.json"))
    if current["revision"] != expected_revision:
        raise ValueError("observations changed externally; reload before saving")
    cleaned = _validate_observations(observations)
    _atomic_write(Path(current["path"]), _serialize_observations(cleaned))
    return await read_observations(cwd, project=project)


async def read_note(
    cwd: str | Path, kind: str, identity: str, *, project: ProjectIdentity | None = None
) -> dict[str, Any]:
    if kind not in NOTE_KINDS:
        raise ValueError("note kind must be projects or sessions")
    if project is None:
        project, mux_dir = await project_status(cwd)
    else:
        mux_dir = Path(project.root) / ".swe-mux"
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
    *,
    project: ProjectIdentity | None = None,
) -> dict[str, Any]:
    if len(markdown.encode("utf-8")) > 1024 * 1024:
        raise ValueError("note exceeds the 1 MiB limit")
    current = await read_note(cwd, kind, identity, project=project)
    if current["revision"] != expected_revision:
        raise ValueError("note changed externally; reload before saving")
    header = f"---\nswe_mux_note = 1\nkind = {json.dumps(kind)}\nid = {json.dumps(identity)}\n---\n"
    body = markdown
    if not markdown.startswith("---\nswe_mux_note = 1\n"):
        body = header + markdown
    _atomic_write(Path(current["path"]), body.encode("utf-8"))
    return await read_note(cwd, kind, identity, project=project)


async def initialize_note(
    cwd: str | Path, kind: str, identity: str, *, project: ProjectIdentity | None = None
) -> dict[str, Any]:
    """Create an empty durable note without overwriting an existing note."""
    current = await read_note(cwd, kind, identity, project=project)
    if current["exists"]:
        return current
    path = Path(current["path"])
    header = f"---\nswe_mux_note = 1\nkind = {json.dumps(kind)}\nid = {json.dumps(identity)}\n---\n"
    _create_note_file(path, header)
    return await read_note(cwd, kind, identity, project=project)
