"""Session-scoped media: what may be stored, where it lives, when it expires."""

from __future__ import annotations

import logging
from contextlib import suppress
from pathlib import Path

log = logging.getLogger(__name__)


SESSION_MEDIA_TTL_SECONDS = 24 * 60 * 60


# Preview screenshots live inside the user's repository, so they get a longer
# window than pasted media (an agent may read one days later) but still expire.
PREVIEW_SHOT_TTL_SECONDS = 7 * 24 * 60 * 60


_MEDIA_TYPES = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
}


_MEDIA_SIGNATURES = {
    "image/png": (b"\x89PNG\r\n\x1a\n",),
    "image/jpeg": (b"\xff\xd8\xff",),
    "image/webp": (b"RIFF",),
    "image/gif": (b"GIF87a", b"GIF89a"),
}


def validate_session_media(media_type: str, data: bytes | bytearray) -> str:
    suffix = _MEDIA_TYPES.get(media_type)
    if suffix is None:
        raise ValueError("supported clipboard image types: PNG, JPEG, WebP, GIF")
    if len(data) > 10 * 1024 * 1024:
        raise ValueError("clipboard image exceeds the 10 MiB limit")
    if not any(data.startswith(signature) for signature in _MEDIA_SIGNATURES[media_type]):
        raise ValueError("clipboard image content does not match its declared type")
    if media_type == "image/webp" and data[8:12] != b"WEBP":
        raise ValueError("clipboard image content does not match its declared type")
    return suffix


def session_media_directory(data_dir: Path, session_id: str) -> Path:
    root = (data_dir / "media").resolve()
    directory = (root / session_id).resolve()
    if directory.parent != root:
        raise ValueError("invalid session media identity")
    return directory


def cleanup_expired_session_media(data_dir: Path, now: float) -> int:
    root = (data_dir / "media").resolve()
    if not root.is_dir():
        return 0
    removed = 0
    cutoff = now - SESSION_MEDIA_TTL_SECONDS
    try:
        directories = list(root.iterdir())
    except OSError:
        return 0
    for directory in directories:
        if not directory.is_dir() or directory.is_symlink():
            continue
        try:
            # A session deleted mid-sweep used to end media cleanup for the
            # daemon's lifetime, after which 10 MiB clipboard images accumulated.
            entries = list(directory.iterdir())
        except OSError:
            continue
        for path in entries:
            try:
                if path.is_file() and not path.is_symlink() and path.stat().st_mtime < cutoff:
                    path.unlink()
                    removed += 1
            except OSError:
                continue
        with suppress(OSError):
            directory.rmdir()
    return removed


def cleanup_expired_preview_shots(roots: list[Path], now: float) -> int:
    """Age out headless preview screenshots.

    They are saved into the owning Project (data-dir fallback) so a local agent
    can read them, which also means they accumulate inside the user's repository:
    a UI-iteration session takes dozens of multi-hundred-KB PNGs a day and nothing
    ever removed them.
    """
    removed = 0
    cutoff = now - PREVIEW_SHOT_TTL_SECONDS
    for root in roots:
        directory = root / ".swe-mux" / "preview-shots" if root.name != "preview-shots" else root
        if not directory.is_dir() or directory.is_symlink():
            continue
        try:
            entries = list(directory.iterdir())
        except OSError:
            continue
        for path in entries:
            try:
                if path.is_file() and not path.is_symlink() and path.stat().st_mtime < cutoff:
                    path.unlink()
                    removed += 1
            except OSError:
                continue
    return removed
