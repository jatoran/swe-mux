"""Cold session recovery: sessions that outlive the processes that ran them.

The PTY supervisor already keeps live sessions running across a daemon restart
(`SESSION_PRESERVING_RELOAD.md`). What it cannot do is survive its *own* death:
its kill-on-close Job takes every process tree with it, and the authoritative
scrollback plus the mirrored per-session metadata are both process memory. So a
supervisor crash, a force close, a power loss, or simply running with
`pty_supervisor_enabled` off leaves the next daemon with no idea those sessions
ever existed - the sidebar comes up empty and the persisted pane layout is
pruned against the (empty) live set.

This module is the fallback behind that. Two layers, deliberately independent:

**Layer A - the durable registry.** Every session's metadata blob (the same one
`SessionManager._session_meta` already mirrors into the supervisor) is written
to SQLite with an *open* marker. A session that ends clears it. So at the next
boot, any row still marked open that the supervisor did not hand back is a
session whose process is gone and whose daemon never got to say so: a **cold
session**. It is rebuilt as an ordinary `Session` in a terminal state, which is
what puts it back in the sidebar, back in the layout, and back within reach of
Resume. This layer is what the feature is for, and it works with no terminal
bytes at all.

**Layer B - terminal checkpoints.** Bytes, so a cold pane shows what it printed
rather than an empty rectangle. Written from the *daemon*, not the supervisor:
the daemon already holds a byte-exact mirror of the authoritative buffer
(`Session.scrollback`, fed by the supervisor subscription), and the supervisor
is deliberately near-frozen because shipping a change to it reaps every live
session. Both processes die together in the case this exists for, so a
daemon-side writer captures the same bytes at the same moment. The residual: a
daemon that dies and stays dead while the supervisor keeps sessions running
leaves a checkpoint that ages until the next daemon attaches. The checkpoint
records when it was taken and the restore labels it, rather than pretending to
be current.

Checkpoints are only taken for harnesses whose retained bytes are a
*transcript*. An alternate-screen TUI's bytes are a differential frame stream:
a bounded window of one reconstructs to a blank or half-drawn screen, and the
repair for that (pulse the PTY, let the child restate itself) needs a live
process, which is exactly what a cold session does not have. Those sessions get
Layer A and a pointer at their transcript, which is a better reconstruction of
an agent conversation than any number of replayed escape sequences.

On-disk layout, per session, under ``<data_dir>/recovery/<session_id>/``:

- ``checkpoint.bin`` - base bytes: the ring tail at the last rebase.
- ``checkpoint.json`` - ``{generation, position, cols, rows, captured_at}``,
  written tmp+rename so a crash mid-write cannot leave a half-parsed base.
- ``output.log`` - framed appends since that base. The generation in its header
  must match the checkpoint's, which is what makes a crash between "rename the
  new checkpoint" and "truncate the log" safe: the stale log no longer matches
  and is ignored rather than replayed onto the wrong base.

The log is framed rather than raw because a crash can tear the final append.
Length prefixes make a torn tail detectable, so a restore truncates at the last
complete frame instead of replaying half an escape sequence into a terminal.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import shutil
import sqlite3
import struct
import time
from collections.abc import Callable, Collection
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Any, TypeVar

from .background_tasks import background
from .sqlite_store import (
    connect_or_quarantine,
    database_operation_lock,
    run_sqlite_operation,
    write_schema_version,
)

log = logging.getLogger(__name__)

T = TypeVar("T")

SESSION_RECOVERY_SCHEMA_VERSION = 1
SESSION_RECOVERY_FLUSH_LOOP = "session-recovery-flush"

#: How often the write-behind loop rewrites registry rows and appends terminal
#: bytes. The registry row is what a cold restore rebuilds a session from, so
#: this is also the worst-case staleness of a recovered session's name, state,
#: and token totals. Deliberately far slower than the 0.5 s supervisor meta sink:
#: that one races a daemon crash for a *live* session, this one only has to be
#: recent enough that a recovered row is recognisable.
FLUSH_INTERVAL_SECONDS = 5.0
#: Retention sweeps are cheap and rare.
PRUNE_INTERVAL_SECONDS = 6 * 3600.0

#: Framing for ``output.log``. Magic guards against reading an unrelated file;
#: the version lets a future format change be *detected* rather than
#: misparsed - an unreadable log falls back to the checkpoint alone, which is
#: always safe.
LOG_MAGIC = b"SMKL"
LOG_FORMAT_VERSION = 1
LOG_HEADER_BYTES = len(LOG_MAGIC) + 1 + 4

FRAME_OUTPUT = 0x01
FRAME_RESIZE = 0x02
_FRAME_HEADER_BYTES = 5

CHECKPOINT_BASE_NAME = "checkpoint.bin"
CHECKPOINT_META_NAME = "checkpoint.json"
CHECKPOINT_LOG_NAME = "output.log"

SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS session_recovery (
  session_id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  opened_at REAL NOT NULL,
  updated_at REAL NOT NULL,
  closed_at REAL,
  close_reason TEXT,
  daemon_pid INTEGER NOT NULL DEFAULT 0,
  meta_json TEXT NOT NULL,
  checkpoint_at REAL,
  checkpoint_cols INTEGER,
  checkpoint_rows INTEGER,
  checkpoint_skipped TEXT
);
CREATE INDEX IF NOT EXISTS idx_session_recovery_open
  ON session_recovery(closed_at, updated_at DESC);
"""

#: Metadata keys that must never reach a cold session. Both authenticate a live
#: process against this daemon, and a cold session's process is gone: restoring
#: them would leave credentials on disk that authenticate nothing but could
#: authenticate *something* if a stale CLI or a replayed request arrived. The
#: restore mints a fresh unguessable hook secret instead, so nothing can present
#: a matching one (an empty secret would be worse than a random one -
#: ``compare_digest("", "")`` is True).
_UNPERSISTED_META_KEYS = ("hook_secret", "mcp_token")


def redact_meta(meta: dict[str, Any]) -> dict[str, Any]:
    """The persistable half of a session metadata blob."""
    return {key: value for key, value in meta.items() if key not in _UNPERSISTED_META_KEYS}


# --- framed append log ------------------------------------------------------


def encode_log_header(generation: int) -> bytes:
    return LOG_MAGIC + struct.pack("<BI", LOG_FORMAT_VERSION, generation & 0xFFFFFFFF)


def decode_log_header(data: bytes) -> int | None:
    """The generation this log was opened against, or None when unreadable."""
    if len(data) < LOG_HEADER_BYTES or data[: len(LOG_MAGIC)] != LOG_MAGIC:
        return None
    version, generation = struct.unpack("<BI", data[len(LOG_MAGIC) : LOG_HEADER_BYTES])
    if version != LOG_FORMAT_VERSION:
        return None
    return int(generation)


def encode_output_frame(payload: bytes) -> bytes:
    return struct.pack("<BI", FRAME_OUTPUT, len(payload)) + payload


def encode_resize_frame(cols: int, rows: int) -> bytes:
    payload = struct.pack("<HH", max(0, min(0xFFFF, cols)), max(0, min(0xFFFF, rows)))
    return struct.pack("<BI", FRAME_RESIZE, len(payload)) + payload


@dataclass(slots=True)
class DecodedLog:
    """What a log file contributed, and whether it ended mid-frame."""

    output: bytes = b""
    geometry: tuple[int, int] | None = None
    #: True when the file ended inside a frame - the normal shape of a crash
    #: during an append. Everything before it is still exact, so the complete
    #: prefix is replayed and only the torn remainder is dropped.
    truncated_tail: bool = False


def decode_log(data: bytes, generation: int) -> DecodedLog | None:
    """Replayable content of ``output.log``, or None when it cannot be trusted.

    Returns None for a missing/unknown header and for a generation that does not
    match the checkpoint it would be replayed onto - the latter is the ordinary
    outcome of a crash between rewriting the checkpoint and truncating the log,
    where the stale log's bytes are already inside the new base and appending
    them again would duplicate a screen's worth of output.
    """
    header = decode_log_header(data)
    if header is None or header != generation:
        return None
    result = DecodedLog()
    chunks: list[bytes] = []
    offset = LOG_HEADER_BYTES
    while offset < len(data):
        if offset + _FRAME_HEADER_BYTES > len(data):
            result.truncated_tail = True
            break
        kind, length = struct.unpack("<BI", data[offset : offset + _FRAME_HEADER_BYTES])
        start = offset + _FRAME_HEADER_BYTES
        end = start + length
        if end > len(data):
            result.truncated_tail = True
            break
        if kind == FRAME_OUTPUT:
            chunks.append(data[start:end])
        elif kind == FRAME_RESIZE:
            if length != 4:
                # A frame whose length contradicts its kind means the writer and
                # this reader disagree about the format. Everything after it is
                # unparseable by definition, so stop rather than guess.
                result.truncated_tail = True
                break
            cols, rows = struct.unpack("<HH", data[start:end])
            result.geometry = (int(cols), int(rows))
        else:
            result.truncated_tail = True
            break
        offset = end
    result.output = b"".join(chunks)
    return result


# --- restore ----------------------------------------------------------------


@dataclass(slots=True)
class RestoredTerminal:
    """Terminal bytes recovered for one cold session."""

    data: bytes
    captured_at: float
    geometry: tuple[int, int] | None = None
    truncated_tail: bool = False


@dataclass(slots=True)
class RecoveredSession:
    """One durable registry row, as the restore path consumes it."""

    session_id: str
    project_id: str
    opened_at: float
    updated_at: float
    meta: dict[str, Any]
    checkpoint_at: float | None = None
    checkpoint_skipped: str | None = None
    terminal: RestoredTerminal | None = None


def read_checkpoint(directory: Path, budget: int) -> RestoredTerminal | None:
    """Reconstruct one session's terminal bytes from disk.

    Pure file reading so the crash/torn-write behaviour is testable without a
    store, a daemon, or a pseudoterminal - which is the only way the interesting
    cases (torn tail, stale generation, missing log) get exercised at all.
    """
    try:
        raw_meta = (directory / CHECKPOINT_META_NAME).read_bytes()
    except OSError:
        return None
    try:
        meta = json.loads(raw_meta)
    except ValueError:
        return None
    if not isinstance(meta, dict):
        return None
    try:
        generation = int(meta.get("generation", -1))
        captured_at = float(meta.get("captured_at") or 0.0)
    except (TypeError, ValueError):
        return None
    if generation < 0:
        return None
    try:
        base = (directory / CHECKPOINT_BASE_NAME).read_bytes()
    except OSError:
        base = b""
    geometry: tuple[int, int] | None = None
    raw_cols, raw_rows = meta.get("cols"), meta.get("rows")
    if isinstance(raw_cols, int) and isinstance(raw_rows, int) and raw_cols > 0 and raw_rows > 0:
        geometry = (raw_cols, raw_rows)
    appended = b""
    truncated = False
    try:
        decoded = decode_log((directory / CHECKPOINT_LOG_NAME).read_bytes(), generation)
    except OSError:
        decoded = None
    if decoded is not None:
        appended = decoded.output
        truncated = decoded.truncated_tail
        if decoded.geometry is not None:
            # The log's own last word outranks the checkpoint's: the checkpoint
            # records the geometry at the *rebase*, and everything appended since
            # was written under whatever the log last recorded. Two stores that
            # can disagree after a crash is exactly why the geometry is framed
            # into the log rather than only held in the registry row.
            geometry = decoded.geometry
    data = base + appended
    if not data:
        return None
    if budget > 0 and len(data) > budget:
        data = data[-budget:]
    return RestoredTerminal(
        data=data, captured_at=captured_at, geometry=geometry, truncated_tail=truncated
    )


# --- store ------------------------------------------------------------------


@dataclass(eq=False)
class _Writer:
    """Per-session checkpoint file state, owned by the store's worker thread."""

    directory: Path
    generation: int = 0
    #: Ring position the log has been written up to. The next flush appends
    #: exactly ``scrollback.bytes_since(cursor)``; a ring that wrapped past it
    #: means bytes were lost between flushes, which is a *gap* rather than a
    #: delta and forces a rebase.
    cursor: int = 0
    log_bytes: int = 0
    geometry: tuple[int, int] | None = None
    started: bool = False


class SessionRecoveryStore:
    """Durable session registry plus terminal checkpoints.

    Same shape as the other mux.db stores: one dedicated worker thread owns the
    connection, operations are serialized in submission order, and rows are
    pruned by a retention window. File writes for the checkpoints run on the same
    worker, so a session's registry row and its bytes are never written
    concurrently with each other.
    """

    _db: sqlite3.Connection

    def __init__(
        self,
        path: Path,
        recovery_dir: Path,
        *,
        checkpoint_bytes: int = 256 * 1024,
        retention_days: int = 7,
        max_cold_sessions: int = 40,
        # Cap on one session's append log before it is rebased onto a fresh
        # checkpoint. Bounds both disk per session and restore replay cost; a
        # rebase is a single write of at most ``checkpoint_bytes``.
        log_max_bytes: int = 1024 * 1024,
    ) -> None:
        self.path = path
        self.recovery_dir = recovery_dir
        self.checkpoint_bytes = checkpoint_bytes
        self.retention_days = retention_days
        self.max_cold_sessions = max_cold_sessions
        self.log_max_bytes = log_max_bytes
        self._writers: dict[str, _Writer] = {}
        self._tracked: dict[str, Any] = {}
        self._closed = False
        path.parent.mkdir(parents=True, exist_ok=True)
        self._operation_lock = database_operation_lock(path)
        self._executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="mux-session-recovery-db"
        )
        self._executor.submit(self._connect).result()
        self._flush_lock = asyncio.Lock()
        self._next_prune = time.monotonic()
        self.write_stats: dict[str, int] = {
            "rows_written": 0,
            "checkpoints_written": 0,
            "appends_written": 0,
            "append_bytes": 0,
            "gap_rebases": 0,
            "flushes": 0,
            "rows_pruned": 0,
        }

    # -- lifecycle ------------------------------------------------------------

    def _open(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, check_same_thread=False)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA synchronous=NORMAL")
        db.execute("PRAGMA busy_timeout=5000")
        return db

    def _connect(self) -> None:
        with self._operation_lock:
            self._db = connect_or_quarantine(self.path, self._open)
            self._db.executescript(SCHEMA)
            write_schema_version(self._db, "session_recovery", SESSION_RECOVERY_SCHEMA_VERSION)
            self._db.commit()
        # 0o700 is a no-op on Windows (the profile ACL already restricts it) and
        # the meaningful bound on POSIX. Terminal bytes are whatever the child
        # printed, which includes anything a command echoed.
        with contextlib.suppress(OSError):
            self.recovery_dir.mkdir(parents=True, exist_ok=True, mode=0o700)

    async def _run(self, fn: Callable[[], T]) -> T:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self._executor, run_sqlite_operation, self._db, self._operation_lock, fn
        )

    async def _run_io(self, fn: Callable[[], T]) -> T:
        """File work on the same worker, but *outside* the database lock.

        `database_operation_lock` is per-database-file and shared with the
        history, automation, telemetry, and voice workers on `mux.db`. Writing a
        few hundred kilobytes of terminal bytes while holding it would make an
        unrelated history write wait on this store's disk I/O. Same executor, so
        a session's file writes stay ordered with each other and with its row.
        """
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._executor, fn)

    def start(self) -> None:
        background.start(SESSION_RECOVERY_FLUSH_LOOP, self._flush_loop)

    async def stop(self) -> None:
        await background.stop(SESSION_RECOVERY_FLUSH_LOOP)
        try:
            await self.flush_dirty()
        except Exception:
            log.exception("final session recovery flush failed")

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        with contextlib.suppress(Exception):
            self._executor.submit(self._db.close).result(timeout=5)
        self._executor.shutdown(wait=False)

    def stats(self) -> dict[str, int]:
        return dict(self.write_stats)

    # -- registry -------------------------------------------------------------

    async def open_session(self, session: Any) -> None:
        """Record a session as live, before anything else can go wrong.

        Awaited on the spawn path rather than left to the flush loop: a crash in
        the first few seconds otherwise leaves a session that ran and can never
        be recovered, which is the same window the supervisor spawn RPC already
        carries its initial metadata through for the same reason.
        """
        sid = str(session.record.id)
        meta = redact_meta(self._meta_of(session))
        payload = json.dumps(meta, default=str)
        now = time.time()
        project_id = str(session.record.project_id or "")
        pid = os.getpid()

        def op() -> None:
            self._db.execute(
                "INSERT INTO session_recovery"
                "(session_id,project_id,opened_at,updated_at,closed_at,close_reason,"
                "daemon_pid,meta_json) VALUES(?,?,?,?,NULL,NULL,?,?) "
                "ON CONFLICT(session_id) DO UPDATE SET project_id=excluded.project_id,"
                "updated_at=excluded.updated_at,closed_at=NULL,close_reason=NULL,"
                "daemon_pid=excluded.daemon_pid,meta_json=excluded.meta_json",
                (sid, project_id, now, now, pid, payload),
            )
            self._db.commit()

        await self._run(op)
        self.write_stats["rows_written"] += 1

    async def close_session(self, sid: str, reason: str) -> None:
        """Mark a session's end, which is what stops it coming back as cold.

        The distinction the whole feature rests on: a row with no ``closed_at``
        describes a session nobody was able to say goodbye for.
        """
        async with self._flush_lock:
            await self._close_session_locked(sid, reason)

    async def _close_session_locked(self, sid: str, reason: str) -> None:
        now = time.time()

        def op() -> None:
            self._db.execute(
                "UPDATE session_recovery SET closed_at=?,close_reason=?,updated_at=? "
                "WHERE session_id=?",
                (now, reason[:64], now, str(sid)),
            )
            self._db.commit()

        await self._run(op)
        self._tracked.pop(str(sid), None)
        self._writers.pop(str(sid), None)

    async def discard(self, sid: str) -> None:
        """Drop a session's row and its recovery data entirely.

        Only an explicit dismissal reaches here. Recovery data outlives an
        ordinary end on purpose - the row is what a post-crash restore reads -
        so deleting it is the operator saying they are done with this session.
        """
        key = str(sid)
        # Under the flush lock so a pass already in flight for this session
        # cannot recreate the directory this is about to remove.
        async with self._flush_lock:
            self._tracked.pop(key, None)
            self._writers.pop(key, None)

            def op() -> None:
                self._db.execute("DELETE FROM session_recovery WHERE session_id=?", (key,))
                self._db.commit()

            # Row first, files second, and outside the shared database lock. A
            # failure between them leaves a directory no row names, which the
            # boot sweep removes; the reverse - a row naming files that are gone -
            # would have a restore report content it cannot produce.
            await self._run(op)
            await self._run_io(partial(_remove_directory, self.recovery_dir / key))

    async def open_rows(self, *, exclude: Collection[str] = ()) -> list[RecoveredSession]:
        """Every row still marked open, newest first, with its terminal bytes.

        The caller decides which of these are genuinely cold: a row the
        supervisor just handed back describes a session that is still running,
        and adopting it as cold would show one live agent as a dead pane.

        Those are excluded *here* rather than by the caller because reading a
        checkpoint is file I/O on the startup path. After a session-preserving
        restart - the common case, and the one nothing went wrong in - every open
        row belongs to a live adopted session, so filtering afterwards would read
        every one of their checkpoints to throw them all away.
        """
        budget = self.checkpoint_bytes
        skip = {str(sid) for sid in exclude}

        def op() -> list[RecoveredSession]:
            rows = self._db.execute(
                "SELECT session_id,project_id,opened_at,updated_at,meta_json,"
                "checkpoint_at,checkpoint_skipped FROM session_recovery "
                "WHERE closed_at IS NULL ORDER BY updated_at DESC"
            ).fetchall()
            recovered: list[RecoveredSession] = []
            for row in rows:
                sid = str(row["session_id"])
                if sid in skip:
                    continue
                try:
                    meta = json.loads(row["meta_json"])
                except ValueError:
                    log.warning("recovery row %s has unreadable metadata; skipping", sid)
                    continue
                if not isinstance(meta, dict):
                    continue
                recovered.append(
                    RecoveredSession(
                        session_id=sid,
                        project_id=str(row["project_id"] or ""),
                        opened_at=float(row["opened_at"] or 0.0),
                        updated_at=float(row["updated_at"] or 0.0),
                        meta=meta,
                        checkpoint_at=row["checkpoint_at"],
                        checkpoint_skipped=row["checkpoint_skipped"],
                        terminal=(
                            read_checkpoint(self.recovery_dir / sid, budget)
                            if budget > 0
                            else None
                        ),
                    )
                )
            return recovered

        return await self._run(op)

    # -- write-behind ---------------------------------------------------------

    def attach(self, session: Any) -> None:
        """Track one live session for the periodic registry/checkpoint drain.

        Deliberately a standing registration rather than the dirty-set sink the
        status timeline uses. That store is driven by discrete appends it must
        not lose; this one is a *sampler* - each pass asks every live session
        what changed since the last one - so there is nothing for a producer to
        nudge, and a dirty set could only go stale in the direction that stops
        checkpointing a session that is still running.
        """
        self._tracked[str(session.record.id)] = session

    def detach(self, sid: str) -> None:
        self._tracked.pop(str(sid), None)

    async def flush_dirty(self) -> int:
        async with self._flush_lock:
            written = 0
            for sid in tuple(self._tracked):
                session = self._tracked.get(sid)
                if session is None:
                    continue
                try:
                    written += await self._flush_session(session)
                except Exception:
                    log.exception("session recovery flush failed for %s", sid)
                    self._tracked.pop(sid, None)
            self.write_stats["flushes"] += 1
            return written

    async def _flush_session(self, session: Any) -> int:
        sid = str(session.record.id)
        if session.record.state in {"exited", "crashed"}:
            # Terminal sessions are drained by `close_session`; a cold session
            # must never write over the very checkpoint it was restored from.
            self._tracked.pop(sid, None)
            return 0
        meta = redact_meta(self._meta_of(session))
        payload = json.dumps(meta, default=str)
        skipped = checkpoint_skip_reason(session)
        capture: _Capture | None = None
        if self.checkpoint_bytes > 0 and skipped is None:
            capture = self._capture(session)
        now = time.time()

        # The bytes go first. A row claiming a checkpoint that was never written
        # would have the restore report terminal content it cannot produce; the
        # reverse - bytes on disk that the row has not caught up with yet - is
        # read correctly, because `read_checkpoint` trusts the files, not the row.
        written = await self._run_io(partial(self._write_capture, sid, capture)) if capture else 0

        def op() -> int:
            cols, rows = (capture.geometry or (None, None)) if capture else (None, None)
            self._db.execute(
                "UPDATE session_recovery SET updated_at=?,meta_json=?,checkpoint_skipped=?,"
                "checkpoint_at=COALESCE(?,checkpoint_at),"
                "checkpoint_cols=COALESCE(?,checkpoint_cols),"
                "checkpoint_rows=COALESCE(?,checkpoint_rows) WHERE session_id=?",
                (now, payload, skipped, now if capture else None, cols, rows, sid),
            )
            self._db.commit()
            return written

        await self._run(op)
        self.write_stats["rows_written"] += 1
        return written

    def _meta_of(self, session: Any) -> dict[str, Any]:
        provider = getattr(session, "recovery_meta", None)
        if callable(provider):
            meta = provider()
            if isinstance(meta, dict):
                return meta
        return {"record": session.record.snapshot()}

    def _capture(self, session: Any) -> _Capture | None:
        """Snapshot what needs writing, on the event loop, without touching disk.

        Read here rather than on the worker because the ring is only coherent on
        the loop that appends to it: a worker thread reading it mid-append could
        pair a position with bytes that do not match it.

        **Every decision between a rebase and an append is made here**, for the
        same reason. A rebase writes a whole new base and an append writes only
        the delta, so a writer that could promote one to the other on the worker
        would write the *delta* as the new base and silently discard everything
        before it - a checkpoint holding the last few seconds of output instead
        of the whole budget. The worker only ever persists what it is handed.
        """
        writer = self._writers.get(str(session.record.id))
        scrollback = session.scrollback
        position = int(scrollback.position)
        geometry = _session_geometry(session)

        def rebase() -> _Capture:
            return _Capture(
                rebase=True,
                data=scrollback.tail(self.checkpoint_bytes) or b"",
                position=position,
                geometry=geometry,
            )

        if writer is None or not writer.started:
            return rebase()
        if position == writer.cursor and geometry == writer.geometry:
            return None
        gap = position - writer.cursor
        if gap < 0 or gap > scrollback.size:
            # The ring wrapped past what the log has, so appending its tail would
            # splice a hole into the stream. A rebase is the only correct answer;
            # counted, because a silent one reads as a healthy checkpoint.
            self.write_stats["gap_rebases"] += 1
            return rebase()
        if writer.log_bytes + gap > self.log_max_bytes:
            # The append log has grown past its cap. Folding it back into a fresh
            # base is what bounds both disk per session and restore replay cost.
            return rebase()
        return _Capture(
            rebase=False,
            data=scrollback.bytes_since(writer.cursor) if gap else b"",
            position=position,
            geometry=geometry,
        )

    def _write_capture(self, sid: str, capture: _Capture) -> int:
        """Persist one capture verbatim. Runs on the store's worker thread."""
        writer = self._writers.get(sid)
        if writer is None:
            writer = _Writer(directory=self.recovery_dir / sid)
            self._writers[sid] = writer
        try:
            if capture.rebase:
                self._rebase(writer, capture)
                self.write_stats["checkpoints_written"] += 1
                return len(capture.data)
            return self._append(writer, capture)
        except OSError:
            # A checkpoint that cannot be written is a degraded recovery, never a
            # failed session: the registry row (Layer A) is what actually brings
            # the session back, and it lives in SQLite.
            log.warning("could not write terminal checkpoint for %s", sid, exc_info=True)
            writer.started = False
            return 0

    def _rebase(self, writer: _Writer, capture: _Capture) -> None:
        writer.directory.mkdir(parents=True, exist_ok=True)
        generation = writer.generation + 1
        base_path = writer.directory / CHECKPOINT_BASE_NAME
        meta_path = writer.directory / CHECKPOINT_META_NAME
        log_path = writer.directory / CHECKPOINT_LOG_NAME
        _atomic_write(base_path, capture.data)
        cols, rows = capture.geometry or (0, 0)
        # The log is truncated to the new generation *before* the metadata that
        # names it lands, so the two can only ever disagree in the safe
        # direction: a crash in between leaves a log whose generation matches
        # nothing the reader will ask for, and `decode_log` refuses it.
        log_path.write_bytes(encode_log_header(generation))
        _atomic_write(
            meta_path,
            json.dumps(
                {
                    "generation": generation,
                    "position": capture.position,
                    "cols": cols,
                    "rows": rows,
                    "captured_at": time.time(),
                }
            ).encode("utf-8"),
        )
        writer.generation = generation
        writer.cursor = capture.position
        writer.log_bytes = LOG_HEADER_BYTES
        writer.geometry = capture.geometry
        writer.started = True

    def _append(self, writer: _Writer, capture: _Capture) -> int:
        frames = b""
        if capture.geometry is not None and capture.geometry != writer.geometry:
            frames += encode_resize_frame(*capture.geometry)
        if capture.data:
            frames += encode_output_frame(capture.data)
        if not frames:
            return 0
        with (writer.directory / CHECKPOINT_LOG_NAME).open("ab") as handle:
            handle.write(frames)
            # The point of the whole framing exercise is that a torn tail is
            # detectable, not that it never happens - so this deliberately does
            # not fsync. Paying a flush every 5 s per session to narrow a window
            # the format already tolerates is the wrong trade.
            handle.flush()
        writer.log_bytes += len(frames)
        writer.cursor = capture.position
        writer.geometry = capture.geometry
        self.write_stats["appends_written"] += 1
        self.write_stats["append_bytes"] += len(capture.data)
        return len(capture.data)

    # -- background loop ------------------------------------------------------

    async def _flush_loop(self) -> None:
        while True:
            await asyncio.sleep(FLUSH_INTERVAL_SECONDS)
            with background.iteration(SESSION_RECOVERY_FLUSH_LOOP):
                await self.flush_dirty()
                if time.monotonic() >= self._next_prune:
                    self._next_prune = time.monotonic() + PRUNE_INTERVAL_SECONDS
                    await self.prune()

    async def prune(self, *, now: float | None = None) -> int:
        """Bound recovery data by age and count.

        Without this the registry accumulates one row and one directory per
        crashed session forever, and the sidebar fills with ghosts nobody
        dismissed. Closed rows age out on the retention window; *open* rows are
        additionally capped by count, because those are the ones that come back
        as cold sessions.
        """
        cutoff = (now or time.time()) - self.retention_days * 86400.0
        limit = max(0, int(self.max_cold_sessions))

        def op() -> list[str]:
            doomed = {
                str(row["session_id"])
                for row in self._db.execute(
                    "SELECT session_id FROM session_recovery "
                    "WHERE closed_at IS NOT NULL AND closed_at < ?",
                    (cutoff,),
                ).fetchall()
            }
            doomed.update(
                str(row["session_id"])
                for row in self._db.execute(
                    "SELECT session_id FROM session_recovery WHERE closed_at IS NULL "
                    "ORDER BY updated_at DESC LIMIT -1 OFFSET ?",
                    (limit,),
                ).fetchall()
            )
            if not doomed:
                return []
            self._db.executemany(
                "DELETE FROM session_recovery WHERE session_id=?",
                [(sid,) for sid in sorted(doomed)],
            )
            self._db.commit()
            return sorted(doomed)

        async with self._flush_lock:
            doomed = await self._run(op)
            if doomed:
                for sid in doomed:
                    self._tracked.pop(sid, None)
                    self._writers.pop(sid, None)
                await self._run_io(
                    partial(_remove_directories, [self.recovery_dir / sid for sid in doomed])
                )
        self.write_stats["rows_pruned"] += len(doomed)
        return len(doomed)

    async def sweep_orphan_directories(self, known: set[str]) -> int:
        """Delete checkpoint directories with no registry row behind them.

        A `discard` that crashed between the row delete and the file delete, or a
        database quarantined out from under its files, both land here. Cheap, and
        it runs once at boot.
        """

        def op() -> int:
            removed = 0
            with contextlib.suppress(OSError):
                for child in self.recovery_dir.iterdir():
                    if child.is_dir() and child.name not in known:
                        _remove_directory(child)
                        removed += 1
            return removed

        # Pure file work: no database lock to take.
        return await self._run_io(op)

    async def known_ids(self) -> set[str]:
        def op() -> set[str]:
            return {
                str(row["session_id"])
                for row in self._db.execute("SELECT session_id FROM session_recovery").fetchall()
            }

        return await self._run(op)


@dataclass(slots=True)
class _Capture:
    rebase: bool
    data: bytes
    position: int
    geometry: tuple[int, int] | None


def checkpoint_skip_reason(session: Any) -> str | None:
    """Why this session's terminal bytes are not worth replaying cold, if so.

    Two independent tests, because the descriptor and the live stream answer
    different questions. The descriptor says what this harness *always* does:
    an alternate-screen CLI's retained bytes are a differential frame stream
    whose bounded window reconstructs to a blank or half-drawn screen, and a
    repaint-heavy one wraps its own ring until the window holds no transcript at
    all. Neither can be repaired without a live child to pulse, which is the one
    thing a cold session does not have.

    The live screen tracker then catches what the descriptor cannot: a plain
    shell that happened to be inside `vim`, `htop`, or `less` when the crash
    came is in exactly the same position as an agent TUI, and restoring one
    frame of it would show a corrupt screen rather than the session's history.
    """
    # Local import: `harness` pulls the registry, and this module is imported by
    # the session manager, which the registry must not depend on.
    from .harness import repaints_scrollback, replay_needs_repaint

    backend = getattr(session.record, "backend", "")
    if replay_needs_repaint(backend):
        return "alternate_screen_harness"
    if repaints_scrollback(backend):
        return "repaints_scrollback"
    screen = getattr(session, "screen", None)
    if screen is not None and getattr(screen, "mode", None) == "alternate":
        return "alternate_screen"
    return None


def _session_geometry(session: Any) -> tuple[int, int] | None:
    geometry = getattr(session, "geometry", None)
    if isinstance(geometry, tuple) and len(geometry) == 2:
        return (int(geometry[0]), int(geometry[1]))
    pty = getattr(session, "pty", None)
    cols, rows = getattr(pty, "cols", 0), getattr(pty, "rows", 0)
    if isinstance(cols, int) and isinstance(rows, int) and cols > 0 and rows > 0:
        return (cols, rows)
    return None


def _atomic_write(path: Path, data: bytes) -> None:
    """tmp+rename, so a crash mid-write cannot leave a half-parsed file behind."""
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(data)
    os.replace(temporary, path)


def _remove_directory(path: Path) -> None:
    with contextlib.suppress(OSError):
        shutil.rmtree(path, ignore_errors=True)


def _remove_directories(paths: list[Path]) -> None:
    for path in paths:
        _remove_directory(path)


__all__ = [
    "DecodedLog",
    "RecoveredSession",
    "RestoredTerminal",
    "SessionRecoveryStore",
    "checkpoint_skip_reason",
    "decode_log",
    "decode_log_header",
    "encode_log_header",
    "encode_output_frame",
    "encode_resize_frame",
    "read_checkpoint",
    "redact_meta",
]
