"""Workspace-local files attached to interactive agent sessions."""

from __future__ import annotations

import hashlib
import mimetypes
import os
import re
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024
MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_ATTACHMENTS_PER_SESSION = 32
MAX_ATTACHMENT_BYTES_PER_SESSION = 100 * 1024 * 1024

_IMAGE_SUFFIXES = {
    ".gif": "image/gif",
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}
_WINDOWS_INVALID = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WHITESPACE = re.compile(r"\s+")
_SAFE_SESSION_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")


@dataclass(frozen=True, slots=True)
class StoredAttachment:
    id: str
    name: str
    path: Path
    relative_path: str
    kind: str
    media_type: str
    bytes: int

    def payload(self, reference: str) -> dict[str, object]:
        return {
            "id": self.id,
            "name": self.name,
            "path": str(self.path),
            "relative_path": self.relative_path,
            "reference": reference,
            "kind": self.kind,
            "media_type": self.media_type,
            "bytes": self.bytes,
        }


def attachment_workspace_root(project_root: str | Path, spawn_cwd: str | Path) -> Path:
    """Choose the registered Project, or an explicitly launched Git worktree."""
    project = Path(project_root).expanduser().resolve()
    cwd = Path(spawn_cwd).expanduser().resolve()
    if not project.is_dir():
        raise ValueError("the session's Project folder is unavailable")
    if not cwd.is_dir():
        raise ValueError("the session's working folder is unavailable")
    try:
        cwd.relative_to(project)
    except ValueError:
        # Spawn validation is the authority for an out-of-Project cwd. The only
        # allowed escape for an agent session is an exact listed Git worktree root.
        return cwd
    return project


def sanitize_attachment_name(filename: str) -> str:
    """Return a short, single-component display/storage name."""
    name = Path(str(filename).replace("\\", "/")).name
    name = _WINDOWS_INVALID.sub("_", name)
    name = _WHITESPACE.sub("_", name).strip(" ._")
    if not name:
        return "attachment"
    if len(name) <= 96:
        return name
    suffix = Path(name).suffix[:16]
    stem_limit = max(1, 96 - len(suffix))
    return f"{Path(name).stem[:stem_limit].rstrip(' ._') or 'attachment'}{suffix}"


def _sniff_image_type(data: bytes | bytearray) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def classify_attachment(
    filename: str, declared_media_type: str, data: bytes | bytearray
) -> tuple[str, str]:
    """Classify native image inputs by content, rejecting misleading image metadata."""
    declared = declared_media_type.split(";", 1)[0].strip().lower()
    suffix_type = _IMAGE_SUFFIXES.get(Path(filename).suffix.lower())
    sniffed = _sniff_image_type(data)
    if sniffed is not None:
        if declared in set(_IMAGE_SUFFIXES.values()) and declared != sniffed:
            raise ValueError("image content does not match its declared type")
        if suffix_type is not None and suffix_type != sniffed:
            raise ValueError("image content does not match its filename extension")
        return "image", sniffed
    if declared in set(_IMAGE_SUFFIXES.values()) or suffix_type is not None:
        raise ValueError("image content does not match its declared type")
    media_type = declared if declared and len(declared) <= 127 else ""
    if not media_type:
        media_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    return "file", media_type


def _safe_session_component(session_id: str) -> str:
    if _SAFE_SESSION_ID.fullmatch(session_id):
        return session_id
    return hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:32]


def _checked_directory(parent: Path, name: str) -> Path:
    directory = parent / name
    if directory.exists() and (directory.is_symlink() or not directory.is_dir()):
        raise ValueError(f"attachment storage path is not a folder: {directory}")
    directory.mkdir(exist_ok=True)
    if directory.is_symlink():
        raise ValueError(f"attachment storage path cannot be a symlink: {directory}")
    return directory


def session_attachment_directory(workspace_root: Path, session_id: str) -> Path:
    root = workspace_root.expanduser().resolve()
    if not root.is_dir():
        raise ValueError("attachment workspace is unavailable")
    mux = _checked_directory(root, ".swe-mux")
    attachments = _checked_directory(mux, "attachments")
    ignore = attachments / ".gitignore"
    if ignore.exists() and (ignore.is_symlink() or not ignore.is_file()):
        raise ValueError("attachment ignore file is unsafe")
    if not ignore.exists():
        ignore.write_text("*\n", encoding="utf-8")
    directory = _checked_directory(attachments, _safe_session_component(session_id))
    try:
        directory.resolve().relative_to(root)
    except ValueError as exc:
        raise ValueError("attachment storage escaped the workspace") from exc
    return directory


def store_session_attachment(
    workspace_root: Path,
    session_id: str,
    filename: str,
    declared_media_type: str,
    data: bytes | bytearray,
    *,
    image_only: bool = False,
) -> StoredAttachment:
    if len(data) > MAX_ATTACHMENT_BYTES:
        raise ValueError("attachment exceeds the 25 MiB limit")
    safe_name = sanitize_attachment_name(filename)
    kind, media_type = classify_attachment(safe_name, declared_media_type, data)
    if image_only and kind != "image":
        raise ValueError("supported clipboard image types: PNG, JPEG, WebP, GIF")
    if kind == "image" and len(data) > MAX_IMAGE_BYTES:
        raise ValueError("image exceeds the 10 MiB limit")

    directory = session_attachment_directory(workspace_root, session_id)
    entries = [
        item
        for item in directory.iterdir()
        if item.is_file() and not item.is_symlink() and not item.name.endswith(".tmp")
    ]
    if len(entries) >= MAX_ATTACHMENTS_PER_SESSION:
        raise ValueError("this session has reached the 32-file attachment limit")
    total = sum(item.stat().st_size for item in entries)
    if total + len(data) > MAX_ATTACHMENT_BYTES_PER_SESSION:
        raise ValueError("this session has reached the 100 MiB attachment limit")

    attachment_id = uuid4().hex
    path = directory / f"{attachment_id}-{safe_name}"
    temporary = directory / f".{attachment_id}.tmp"
    temporary.write_bytes(data)
    os.replace(temporary, path)
    relative_path = path.relative_to(workspace_root.resolve()).as_posix()
    return StoredAttachment(
        id=attachment_id,
        name=safe_name,
        path=path,
        relative_path=relative_path,
        kind=kind,
        media_type=media_type,
        bytes=len(data),
    )
