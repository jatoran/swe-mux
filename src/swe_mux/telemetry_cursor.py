"""Incremental reads of append-only native transcripts for telemetry reconciliation.

Both telemetry stores reconcile a conversation by parsing its provider transcript.
They used to parse the whole file (its last 32 MiB, for a larger one) every time it
changed, and a working session changes on every pass: each five-minute canonical pass
JSON-decoded hundreds of megabytes on a worker thread, holding the GIL in Python's
json code while the event loop waited for it. A transcript is append-only JSONL, so
everything before the last line already read is already known.

A `ScanCursor` remembers where the last read stopped: the byte offset just past the
last complete line, the absolute number of that line, and two content samples (the
file's first bytes and the bytes just before the offset). A later read verifies both
samples and parses only what was appended. A file that shrank, or whose samples no
longer match - a Claude cancel or revert rewrites its transcript, and a replacement
can keep the same size - is read again from the start. File timestamps are never
consulted: Windows leaves a file a CLI holds open dated at its creation however much
it grows.

Lines are numbered absolutely, from the first byte of the file, so an identity built
from a line number (a call without a native id, a compaction) names the same record
on every read. The previous tail scan numbered lines from wherever its 32 MiB window
happened to start, which moved as the file grew and minted a new identity for the
same record on every pass.

Only newline-terminated lines are consumed. A line the CLI is still writing is left
for the next read, rather than parsed half-written and then counted again once its
newline arrives.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any

#: The prefix sample. A Claude transcript's first records name its session, and a
#: Codex rollout's first line is its `session_meta`, so a replaced file differs here.
HEAD_SAMPLE_BYTES = 4096
#: The sample ending at the cursor. A rewrite that kept the prefix (a `/rewind`
#: truncating and re-appending) differs here.
TAIL_SAMPLE_BYTES = 256
#: How much of a file a first read parses. Older records are counted (for absolute
#: line numbers) but not decoded, which keeps a first read of a multi-gigabyte
#: rollout bounded; the coverage the read reports says so.
FIRST_READ_TAIL_BYTES = 32 * 1024 * 1024
_CHUNK_BYTES = 1024 * 1024


@dataclass(frozen=True)
class ScanCursor:
    """Where the last read of one transcript stopped."""

    offset: int
    line: int
    head_sha256: str | None
    tail_sha256: str | None
    #: The read this cursor continues began at a tail window rather than at byte 0,
    #: so records before that window were never parsed.
    truncated: bool


@dataclass(frozen=True)
class AppendedRecords:
    """What one incremental read found."""

    #: (absolute line number, decoded record) for every complete JSON object line.
    records: list[tuple[int, dict[str, Any]]]
    cursor: ScanCursor
    #: `append` (continued a verified cursor), `unchanged`, `initial` (no cursor),
    #: `shrunk`, or `replaced`.
    reason: str
    start_offset: int
    bytes_read: int

    @property
    def rescanned(self) -> bool:
        """The read started over rather than continuing a cursor."""

        return self.reason not in {"append", "unchanged"}


def _sample(handle: IO[bytes], start: int, end: int) -> str:
    handle.seek(start)
    return hashlib.sha256(handle.read(max(0, end - start))).hexdigest()


def _head(handle: IO[bytes], offset: int) -> str:
    return _sample(handle, 0, min(HEAD_SAMPLE_BYTES, offset))


def _tail(handle: IO[bytes], offset: int) -> str:
    return _sample(handle, max(0, offset - TAIL_SAMPLE_BYTES), offset)


def _mismatch(handle: IO[bytes], size: int, cursor: ScanCursor) -> str | None:
    """Why `cursor` no longer describes this file, or None when it still does."""

    if cursor.offset > size:
        return "shrunk"
    if cursor.head_sha256 is not None and _head(handle, cursor.offset) != cursor.head_sha256:
        return "replaced"
    if cursor.tail_sha256 is not None and _tail(handle, cursor.offset) != cursor.tail_sha256:
        return "replaced"
    return None


def count_lines(handle: IO[bytes], end: int) -> int:
    """Newlines in the first `end` bytes, without decoding any of them."""

    handle.seek(0)
    remaining = end
    lines = 0
    while remaining > 0:
        block = handle.read(min(_CHUNK_BYTES, remaining))
        if not block:
            break
        lines += block.count(b"\n")
        remaining -= len(block)
    return lines


def _first_read_start(handle: IO[bytes], size: int, tail_bytes: int) -> tuple[int, int, bool]:
    if size <= tail_bytes:
        return 0, 0, False
    # Start at the first line boundary inside the window, never mid-record. Reading
    # from one byte before the window keeps a line that begins exactly on its edge.
    handle.seek(size - tail_bytes - 1)
    handle.readline()
    start = handle.tell()
    return start, count_lines(handle, start), True


def _complete_lines(
    handle: IO[bytes], start: int, end: int, first_line: int
) -> tuple[list[tuple[int, dict[str, Any]]], int, int]:
    """Decode every newline-terminated line in [start, end).

    Returns the records, the offset just past the last complete line, and the
    absolute number of the next line.
    """

    handle.seek(start)
    records: list[tuple[int, dict[str, Any]]] = []
    consumed = start
    line = first_line
    pending = b""
    remaining = end - start
    while remaining > 0:
        block = handle.read(min(_CHUNK_BYTES, remaining))
        if not block:
            break
        remaining -= len(block)
        data = pending + block
        cut = data.rfind(b"\n")
        if cut < 0:
            pending = data
            continue
        complete, pending = data[: cut + 1], data[cut + 1 :]
        for raw in complete.split(b"\n")[:-1]:
            if raw.strip():
                try:
                    item = json.loads(raw.decode("utf-8", "replace"))
                except json.JSONDecodeError:
                    item = None
                if isinstance(item, dict):
                    records.append((line, item))
            line += 1
        consumed += len(complete)
    return records, consumed, line


def read_appended(
    path: Path, cursor: ScanCursor | None, *, tail_bytes: int = FIRST_READ_TAIL_BYTES
) -> AppendedRecords:
    """Decode what was appended to `path` since `cursor`, or start over when it lies."""

    with path.open("rb") as handle:
        size = os.fstat(handle.fileno()).st_size
        reason = "initial"
        if cursor is not None:
            mismatch = _mismatch(handle, size, cursor)
            if mismatch is None:
                if cursor.offset == size:
                    return AppendedRecords([], cursor, "unchanged", size, 0)
                reason = "append"
            else:
                reason = mismatch
        if reason == "append" and cursor is not None:
            start, first_line, truncated = cursor.offset, cursor.line, cursor.truncated
        else:
            start, first_line, truncated = _first_read_start(handle, size, tail_bytes)
        records, end, next_line = _complete_lines(handle, start, size, first_line)
        if end == start and cursor is not None and reason == "append":
            # Only a partial line arrived; the cursor still describes the file.
            return AppendedRecords([], cursor, "unchanged", start, 0)
        advanced = ScanCursor(
            offset=end,
            line=next_line,
            head_sha256=_head(handle, end),
            tail_sha256=_tail(handle, end),
            truncated=truncated,
        )
        return AppendedRecords(records, advanced, reason, start, end - start)


def resumed_cursor(path: Path, offset: int) -> ScanCursor | None:
    """A cursor for a file a whole-file read already covered up to `offset`.

    Coverage recorded before cursors existed holds only the size the last whole read
    saw. Everything up to that size was read then, so the next read continues there
    instead of parsing the file again. Its samples are taken now, so it is trusted
    once rather than verified; None when the file is now shorter than that.
    """

    try:
        with path.open("rb") as handle:
            size = os.fstat(handle.fileno()).st_size
            if offset < 0 or offset > size:
                return None
            if offset:
                handle.seek(offset - 1)
                if handle.read(1) != b"\n":
                    # The old read stopped mid-line (an unterminated last record);
                    # numbering from here would be off by one, so start over.
                    return None
            return ScanCursor(
                offset=offset,
                line=count_lines(handle, offset),
                head_sha256=_head(handle, offset),
                tail_sha256=_tail(handle, offset),
                truncated=offset > FIRST_READ_TAIL_BYTES,
            )
    except OSError:
        return None
