"""Database maintenance that needs exclusive ownership of the file.

`VACUUM` and a cross-file table move both require that nothing else holds the
database, which a running daemon can never give. The window that does exist is
the successor daemon's own startup: `__main__.wait_for_predecessor_exit` waits
for the predecessor *process*, not just its port, so by the time the runtime is
built this process is the only one holding `mux.db` - and the PTY supervisor
owns the sessions throughout, so nothing the operator is running dies for it.

So maintenance is a durable *request* rather than a command: `swemux compact-db`
writes the request and triggers the ordinary session-preserving restart, and the
successor honours it in a named startup phase before any store opens the file.
That deliberately puts minutes of work on the startup path, which everything
else here exists to keep clear (`development/PERFORMANCE_RUNBOOK.md`). The
exception is narrow and is the whole reason the request is explicit: the
operator asked for it, it happens once, the phase reports itself while it runs,
and the alternative is an outage that reaps every session.

Nothing here is reachable from the daemon's HTTP surface or from MCP. A
compaction is minutes of unavailability and a rewrite of the operator's data;
it is a thing a person types.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import sqlite3
import time
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .sqlite_store import VerificationControl

log = logging.getLogger(__name__)

REQUEST_NAME = "db-maintenance.json"

# What a request may ask for. A closed set on purpose: the request file is read
# by a process that is about to rewrite the operator's database, so an unknown
# operation is refused rather than ignored - ignoring it would report success
# for work that never happened.
OPERATION_VACUUM = "vacuum"
OPERATION_REBUILD_TRIGRAM = "rebuild-trigram"
OPERATIONS = (OPERATION_REBUILD_TRIGRAM, OPERATION_VACUUM)

# The page size a `VACUUM` rewrites to. `mux.db` was built at SQLite's 4096
# default, which means an 819,902-page file and one 4 KiB read per page for
# anything that walks it. 16 KiB cuts the page count 4x, and with it the read
# count of every full scan - including the integrity check, whose cold cost was
# the reason any of this was measured. Applied by setting the pragma *before*
# `VACUUM`, which is the only time SQLite will change it on an existing file.
VACUUM_PAGE_SIZE = 16384

# A backup is taken before anything destructive, and it is a real copy rather
# than a rename: a rename would leave the daemon with no database if the process
# died between the rename and the rewrite. It costs the file's size in disk for
# the duration, which is checked before it is attempted.
BACKUP_SUFFIX = ".pre-compact"

# How long to wait for a database another process still holds.
#
# The startup window is *not* guaranteed exclusive, which is the correction this
# constant exists for. `__main__.wait_for_predecessor_exit` waits for the
# predecessor process, but the wait is bounded (20s) and a timeout is
# deliberately a warning rather than a refusal - a wedged predecessor must not
# stop a restart. It even says so: "its last writes may be lost to a database
# lock". The first run of `compact-db` against the real `mux.db` hit exactly
# that, 74ms after the gate gave up, and `VACUUM` failed with `database is
# locked` because it must be the only connection.
#
# So exclusivity is checked and waited for rather than assumed, and a lock is
# classified as retryable so the request survives to the next start.
LOCK_WAIT_SECONDS = 30.0


def is_lock_error(exc: BaseException) -> bool:
    """Whether this failure means "someone else holds the file", not "this is broken".

    Narrow on purpose, the same way `sqlite_store.is_locked_error` is:
    `OperationalError` is also how SQLite reports a missing table, and treating
    that as retryable would keep a request that can never succeed and make every
    start pay for it.
    """
    return isinstance(exc, sqlite3.OperationalError) and "locked" in str(exc).lower()


@dataclass(frozen=True)
class MaintenanceRequest:
    """What the operator asked for, read back off disk."""

    operations: tuple[str, ...]
    requested_at: float
    backup: bool = True

    def unknown(self) -> tuple[str, ...]:
        return tuple(op for op in self.operations if op not in OPERATIONS)


@dataclass
class MaintenanceResult:
    """What one maintenance pass did, for the log and the operator."""

    performed: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    seconds: float = 0.0
    bytes_before: int = 0
    bytes_after: int = 0
    backup_path: Path | None = None
    error: str | None = None
    # What the vacuum actually achieved, which is not always what it asked for -
    # see `_vacuum`. Reported rather than assumed, because the first version of
    # this silently did nothing.
    page_size: int = 0
    # Whether the failure was "the window was not available" rather than "this
    # operation does not work". The distinction decides whether the request
    # survives, and getting it wrong in either direction is bad: consuming a
    # retryable failure silently drops what the operator asked for, and keeping
    # a terminal one makes every start slow forever.
    retryable: bool = False

    @property
    def bytes_reclaimed(self) -> int:
        return max(0, self.bytes_before - self.bytes_after)


def request_path(data_dir: Path) -> Path:
    return data_dir / REQUEST_NAME


def read_request(data_dir: Path) -> MaintenanceRequest | None:
    """The pending request, or None. Anything unreadable is *not* a request.

    Failing towards "do nothing" is the safe direction here, and it is the
    opposite of the integrity record's rule: an unreadable verification record
    means re-check, because checking is cheap and safe, while an unreadable
    maintenance request would mean rewriting a database on the strength of a
    file this process could not parse.
    """
    path = request_path(data_dir)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict):
        log.warning("ignoring a malformed maintenance request at %s", path)
        return None
    operations = raw.get("operations")
    if not isinstance(operations, list) or not all(isinstance(op, str) for op in operations):
        log.warning("ignoring a maintenance request with no readable operations at %s", path)
        return None
    requested_at = raw.get("requested_at")
    return MaintenanceRequest(
        operations=tuple(operations),
        requested_at=float(requested_at) if isinstance(requested_at, (int, float)) else 0.0,
        backup=bool(raw.get("backup", True)),
    )


def write_request(data_dir: Path, operations: tuple[str, ...], *, backup: bool = True) -> Path:
    """Record a maintenance request for the next daemon start to honour."""
    path = request_path(data_dir)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(
            {
                "operations": list(operations),
                "requested_at": time.time(),
                "backup": backup,
            }
        ),
        encoding="utf-8",
    )
    os.replace(temporary, path)
    return path


def clear_request(data_dir: Path) -> None:
    """Consume the request. Called whether the pass succeeded or failed.

    A failed pass must not leave the request standing: it would run again on the
    next start, and a maintenance operation that fails once will generally fail
    the same way twice, which turns one bad start into every start being slow.
    The failure is logged and the operator re-requests.
    """
    with suppress(OSError):
        request_path(data_dir).unlink()


def _database_bytes(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _free_bytes(path: Path) -> int | None:
    try:
        return int(shutil.disk_usage(path.parent).free)
    except OSError:
        return None


def take_backup(path: Path) -> Path | None:
    """Copy the database aside before anything rewrites it.

    Returns the backup path, or None when it could not be taken - in which case
    the caller must not proceed. `VACUUM` rewrites the whole file and a
    `rebuild-trigram` drops an index that takes minutes to regenerate; neither is
    something to attempt without a way back.
    """
    size = _database_bytes(path)
    free = _free_bytes(path)
    # Two copies plus VACUUM's own temporary rewrite of the file.
    needed = size * 2
    if free is not None and free < needed:
        log.error(
            "refusing to compact %s: the backup and the rewrite need about %.2f GB and "
            "only %.2f GB is free",
            path,
            needed / 1e9,
            free / 1e9,
        )
        return None
    backup = path.with_name(path.name + BACKUP_SUFFIX)
    try:
        shutil.copy2(path, backup)
    except OSError:
        log.exception("could not back up %s before compacting it", path)
        return None
    return backup


def _rebuild_trigram_index(db: sqlite3.Connection, control: VerificationControl | None) -> bool:
    """Drop the history trigram index so it is rebuilt fresh and dense.

    This started out as a plan to recreate it with `detail=none`, on the
    strength of its size: measured 2026-08-30, `history_messages_trigram_data`
    was 480 MB and `history_messages_trigram` a further 98 MB to index 116 MB of
    messages. **That does not work**, and the reason is in `history.py` beside
    the schema: FTS5 refuses a phrase query without position data, a trigram
    substring match *is* a phrase query over trigrams, and `message_rows` uses
    `MATCH` and `bm25()`. `detail=column` fails the same way.

    What survives is worth more than it looks. The index this repository has
    grew incrementally over months, and a fresh rebuild of the same definition
    is markedly denser - measured 422.3 MB against 480 MB - so dropping it lets
    the `VACUUM` that follows compact the file without it and lets
    `history.py`'s existing search-maintenance path rebuild it in one pass.
    End to end on the real file: 3.36 GB -> 2.23 GB immediately, settling at
    2.69 GB once the index is back, against 3.19 GB for a `VACUUM` alone. Most
    of the win is the defragmentation, not the vacuum.

    Dropping rather than redefining is deliberate: `history.py` owns this
    schema, and a second definition here is a second thing to keep in step.
    Dropping it is also *seen* - `_ensure_message_search_schema` notices the
    missing table, sets `reset_required=1, ready=0`, and the daemon's search
    maintenance task backfills it while `ready=0` tells every search surface the
    index is incomplete rather than empty.

    Returns False when the table is not present, which is the ordinary case on
    a fresh install and not an error.
    """
    present = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='history_messages_trigram'"
    ).fetchone()
    if present is None:
        return False
    if control is not None and control.cancelled:
        return False
    # Dropping takes the shadow tables and the triggers with it; `history.py`
    # recreates both on its next open, so the schema is owned in exactly one
    # place and this does not become a second definition to keep in step.
    db.execute("DROP TABLE IF EXISTS history_messages_trigram")
    for trigger in (
        "history_messages_trigram_ai",
        "history_messages_trigram_ad",
        "history_messages_trigram_au",
    ):
        db.execute(f"DROP TRIGGER IF EXISTS {trigger}")
    db.commit()
    return True


def _vacuum(db: sqlite3.Connection, page_size: int) -> int:
    """Rewrite the file densely, at `page_size`. Returns the page size in force.

    **`page_size` cannot be changed on a WAL database.** SQLite accepts the
    pragma, reports no error, and ignores it - so the obvious implementation
    (set the pragma, `VACUUM`) silently leaves a 4 KiB file at 4 KiB, which is
    exactly what the first version of this function did against the real 3.36 GB
    `mux.db`. It passed its unit test because the test's fixture was in the
    default rollback mode rather than in WAL, which is the shape of test that
    proves nothing about production. The journal mode is now part of the
    fixture, and this function returns what it actually achieved rather than
    what it asked for.

    So the sequence is: leave WAL, set the size, `VACUUM`, return to WAL. Each
    step is checked, and any failure leaves the database in WAL with its
    original page size - a smaller win, never a broken file.
    """
    previous_isolation = db.isolation_level
    db.isolation_level = None  # VACUUM and journal_mode cannot run in a transaction
    started_mode = str(db.execute("PRAGMA journal_mode").fetchone()[0]).lower()
    try:
        if started_mode == "wal":
            # DELETE rather than OFF: OFF discards the rollback journal, which is
            # the one thing making the mode switch itself crash-safe.
            db.execute("PRAGMA journal_mode=DELETE").fetchone()
        db.execute(f"PRAGMA page_size={int(page_size)}")
        db.execute("VACUUM")
        return int(db.execute("PRAGMA page_size").fetchone()[0])
    finally:
        # Restore the mode the caller had, on every path. Leaving `mux.db` in
        # rollback mode would silently cost every store WAL's concurrency, and
        # converting a caller's non-WAL database *to* WAL would be this function
        # changing something it was not asked to change.
        with suppress(sqlite3.Error):
            if str(db.execute("PRAGMA journal_mode").fetchone()[0]).lower() != started_mode:
                db.execute(f"PRAGMA journal_mode={started_mode.upper()}").fetchone()
        db.isolation_level = previous_isolation


def run_maintenance(
    path: Path,
    request: MaintenanceRequest,
    control: VerificationControl | None = None,
) -> MaintenanceResult:
    """Perform a maintenance request against an exclusively-held database.

    The caller owns the exclusivity guarantee: this must run before any store
    has opened the file, which in the daemon means inside the startup phase and
    after the predecessor process has exited.
    """
    started = time.monotonic()
    result = MaintenanceResult(bytes_before=_database_bytes(path))
    unknown = request.unknown()
    if unknown:
        result.error = f"unknown maintenance operation(s): {', '.join(unknown)}"
        log.error("%s; nothing was done to %s", result.error, path)
        result.seconds = time.monotonic() - started
        return result
    if not path.exists():
        result.error = "the database does not exist"
        result.seconds = time.monotonic() - started
        return result

    if request.backup:
        backup = take_backup(path)
        if backup is None:
            result.error = "the pre-compaction backup could not be taken"
            result.seconds = time.monotonic() - started
            return result
        result.backup_path = backup

    db: sqlite3.Connection | None = None
    try:
        # A generous busy timeout for the case the startup gate cannot close: a
        # predecessor that is still draining when this runs. `VACUUM` needs to be
        # the only connection, so a draining predecessor fails it outright, and
        # the default 5s is shorter than a slow drain.
        db = sqlite3.connect(path, timeout=LOCK_WAIT_SECONDS)
        db.execute(f"PRAGMA busy_timeout={int(LOCK_WAIT_SECONDS * 1000)}")
        if control is not None:
            control.adopt(db)
        # Ordered rather than as-requested: dropping the index frees the pages
        # that the vacuum then returns to the filesystem. The other order leaves
        # ~580 MB in the freelist and reports a much smaller reclaim.
        if OPERATION_REBUILD_TRIGRAM in request.operations:
            if _rebuild_trigram_index(db, control):
                result.performed.append(OPERATION_REBUILD_TRIGRAM)
            else:
                result.skipped.append(OPERATION_REBUILD_TRIGRAM)
        if OPERATION_VACUUM in request.operations:
            if control is not None and control.cancelled:
                result.skipped.append(OPERATION_VACUUM)
            else:
                result.page_size = _vacuum(db, VACUUM_PAGE_SIZE)
                result.performed.append(OPERATION_VACUUM)
    except sqlite3.DatabaseError as exc:
        result.error = str(exc)
        result.retryable = is_lock_error(exc)
        log.exception("database maintenance on %s failed", path)
    finally:
        if control is not None:
            control.release()
        if db is not None:
            with suppress(sqlite3.Error):
                db.close()

    result.bytes_after = _database_bytes(path)
    result.seconds = time.monotonic() - started
    return result


def describe(result: MaintenanceResult) -> str:
    """One line for the log and for the operator's terminal."""
    if result.error:
        return f"database maintenance failed after {result.seconds:.1f}s: {result.error}"
    did = ", ".join(result.performed) if result.performed else "nothing"
    tail = f"; skipped {', '.join(result.skipped)}" if result.skipped else ""
    page = f"; page size now {result.page_size}" if result.page_size else ""
    return (
        f"database maintenance did {did} in {result.seconds:.1f}s: "
        f"{result.bytes_before / 1e9:.2f} GB -> {result.bytes_after / 1e9:.2f} GB "
        f"({result.bytes_reclaimed / 1e9:.2f} GB reclaimed){page}{tail}"
    )


def maintenance_summary(data_dir: Path) -> dict[str, Any]:
    """What a pending request asks for, for `swemux doctor` and the CLI."""
    request = read_request(data_dir)
    if request is None:
        return {"pending": False}
    return {
        "pending": True,
        "operations": list(request.operations),
        "requested_at": request.requested_at,
        "backup": request.backup,
        "unknown": list(request.unknown()),
    }
