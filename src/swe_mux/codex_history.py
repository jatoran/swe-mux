"""Read Codex copy-on-write history without modifying provider-owned files.

A paginated fork contains a fixed prefix reference, not copies of its parent's
messages. Byte coordinates in this module describe the concatenated history;
message identities also include the originating thread so offsets cannot alias.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)
_ID = re.compile(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\Z")
META_BYTES = 256 * 1024
MAX_DEPTH = 32
SOURCE_THREAD = "__swe_mux_source_thread"
OFFSET = "__swe_mux_source_offset"
END = "__swe_mux_source_end"


def metadata(path: Path) -> dict[str, Any]:
    with path.open("rb") as handle:
        line = handle.readline(META_BYTES + 1)
    if len(line) > META_BYTES:
        raise OSError("Codex transcript metadata exceeds the read limit")
    try:
        record = json.loads(line)
    except ValueError:
        return {}
    if not isinstance(record, dict) or record.get("type") != "session_meta":
        return {}
    data = record.get("payload")
    return data if isinstance(data, dict) else {}


def _parent(path: Path, thread: str) -> Path:
    if not _ID.fullmatch(thread):
        raise OSError("Invalid Codex history reference")
    # The usual case costs one directory listing. Native histories may span dates
    # or be archived, so also search the same provider home's history roots.
    roots = [path.parent]
    for ancestor in path.parents:
        if ancestor.name in {"sessions", "archived_sessions"}:
            roots += [ancestor.parent / "sessions", ancestor.parent / "archived_sessions"]
            break
    for root in roots:
        for candidate in root.glob(f"**/*-{thread}.jsonl"):
            if metadata(candidate).get("id") == thread:
                return candidate
    raise OSError(f"Inherited Codex transcript is unavailable: {thread}")


def segments(
    path: Path, *, end: int | None = None, seen: frozenset[Path] = frozenset()
) -> list[tuple[Path, int]]:
    resolved = path.resolve()
    if resolved in seen or len(seen) >= MAX_DEPTH:
        raise OSError("Cyclic or excessively deep Codex history reference")
    size = path.stat().st_size
    if end is not None and (end < 0 or end > size):
        raise OSError("Inherited Codex transcript prefix is incomplete")
    meta = metadata(path)
    base = meta.get("history_base")
    result: list[tuple[Path, int]] = []
    if isinstance(base, dict):
        thread, boundary = base.get("thread_id"), base.get("end_byte_offset")
        if (
            not isinstance(thread, str)
            or not isinstance(boundary, int)
            or isinstance(boundary, bool)
        ):
            raise OSError("Unsupported Codex history reference")
        result = segments(_parent(path, thread), end=boundary, seen=seen | {resolved})
    result.append((path, size if end is None else end))
    return result


def size(path: Path) -> int:
    return sum(length for _, length in segments(path))


def revision(path: Path) -> tuple[str, int, int]:
    """A bounded, replacement-aware fingerprint including inherited prefixes.

    Parent appends beyond the pinned prefix do not alter the conversation.
    Prefix/tail samples also detect the same-size replacements Windows may not
    date. Never infer freshness of a live writer from its mtime alone.
    """
    digest = hashlib.sha256()
    total = 0
    for source, length in segments(path):
        stat = source.stat()
        digest.update(f"{source.resolve()}:{stat.st_ino}:{length}:".encode())
        if source == path:
            digest.update(f"{stat.st_mtime_ns}:{stat.st_ctime_ns}:".encode())
        with source.open("rb") as handle:
            digest.update(handle.read(min(length, 4096)))
            handle.seek(max(0, length - 4096))
            digest.update(handle.read(min(length, 4096)))
        total += length
    return f"{path}#{digest.hexdigest()}", 0, total


def page(
    path: Path, *, direction: str = "tail", anchor: int | None = None, max_bytes: int | None = None
) -> tuple[list[dict[str, Any]], bool, int]:
    sources = segments(path)
    total = sum(length for _, length in sources)
    budget = total if max_bytes is None else max_bytes
    if direction == "head":
        start = min(total, max(0, anchor or 0))
        end = min(total, start + budget)
    else:
        end = min(total, max(0, total if anchor is None else anchor))
        start = max(0, end - budget)
    records: list[dict[str, Any]] = []
    base = 0
    boundary = end if direction == "head" else start
    for source, length in sources:
        local_start, local_end = max(0, start - base), min(length, end - base)
        if local_start >= local_end:
            base += length
            continue
        with source.open("rb") as handle:
            source_id = metadata(source).get("id", source.name) if source != path else None
            handle.seek(local_start)
            if local_start:
                handle.seek(local_start - 1)
                if handle.read(1) != b"\n":
                    handle.readline()
            while handle.tell() < local_end:
                offset = handle.tell()
                line = handle.readline(local_end - offset)
                if not line.endswith(b"\n") and local_end < length:
                    if direction == "head":
                        boundary = base + offset
                        if boundary == start:
                            # Oversized single records must not strand a paging
                            # cursor at the same byte indefinitely.
                            handle.readline()
                            boundary = base + min(length, handle.tell())
                    break
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if isinstance(event, dict):
                    event[OFFSET], event[END] = base + offset, base + handle.tell()
                    if source != path:
                        event[SOURCE_THREAD] = f"{source_id}:{offset}"
                    records.append(event)
        base += length
    more = boundary < total if direction == "head" else start > 0
    return records, more, boundary


@lru_cache(maxsize=256)
def _record_timestamp(path: Path, size: int, mtime: int, inode: int) -> float:
    del mtime, inode  # Cache identity, not evidence of freshness.
    with path.open("rb") as handle:
        handle.seek(max(0, size - 65536))
        lines = handle.read(min(size, 65536)).splitlines()
    for line in reversed(lines):
        try:
            value = json.loads(line).get("timestamp")
            stamp = datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
            if 0 < stamp <= time.time() + 5:
                return stamp
        except (ValueError, TypeError, AttributeError, OverflowError):
            continue
    return 0.0


def last_write(path: Path, modified: float) -> float:
    stat = path.stat()
    return max(modified, _record_timestamp(path, stat.st_size, stat.st_mtime_ns, stat.st_ino))
