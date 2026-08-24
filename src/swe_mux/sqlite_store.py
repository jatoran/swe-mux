from __future__ import annotations

import logging
import os
import shutil
import sqlite3
import threading
import time
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_LOCKS_GUARD = threading.Lock()
_DATABASE_LOCKS: dict[str, Any] = {}

# One integrity verdict per database file per process. `PRAGMA quick_check`
# reads every page, so its cost is the size of the file: measured 11.5s on a
# 2.73 GB `mux.db`, and eleven stores share that one file. Probing per *store*
# therefore spent ~126s of every daemon start re-answering a question about a
# file that had not changed since the answer before it - the single largest
# component of a measured 226.6s startup, and entirely invisible because the
# probe logs nothing when it passes.
#
# The verdict belongs to the file, not to the store, so caching it is not a
# weakening. It is the stricter reading of the two: after a corrupt file is
# quarantined and recreated, the later stores were probing a *different* file
# from the one the first store judged, which is a race the cache removes by
# recording the replacement (`_remember_integrity`).
_INTEGRITY_GUARD = threading.Lock()
_INTEGRITY_RESULTS: dict[str, str | None] = {}

_SCHEMA_VERSIONS = (
    "CREATE TABLE IF NOT EXISTS schema_versions("
    "store TEXT PRIMARY KEY, version INTEGER NOT NULL)"
)


def escape_like(value: str) -> str:
    """Make a user's text literal inside a SQL ``LIKE`` pattern.

    ``%`` and ``_`` are wildcards there, so a search for ``100%`` would otherwise
    match everything after ``100`` and one for ``land_store`` would also match
    ``land-store``. Both directions are wrong and neither is visible in the
    result: the caller is silently handed a different query from the one it
    asked for.

    Lives here rather than in one store because every store searching text has
    the same problem, and three private copies is how two of them ended up
    without one. **Paired with** ``ESCAPE '\\'`` at the call site - the escape
    character is not SQLite's default, so a pattern built by this function and
    used without that clause is escaped into literal backslashes and matches
    nothing.
    """
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def like_contains(value: str) -> str:
    """A literal substring ``LIKE`` pattern. Pair with ``ESCAPE '\\'``."""
    return f"%{escape_like(value)}%"


def read_schema_version(db: sqlite3.Connection, store: str) -> int:
    """Read one store's schema version from the shared per-store table.

    `PRAGMA user_version` is a property of the *file*, and several stores share
    `mux.db`: each one stamping its own number means the last connect wins and
    every store reads a neighbour's version. The mechanism looked armed while
    being unusable, so versions live in a per-store row instead.
    """
    db.execute(_SCHEMA_VERSIONS)
    row = db.execute("SELECT version FROM schema_versions WHERE store=?", (store,)).fetchone()
    return int(row[0]) if row else 0


def write_schema_version(db: sqlite3.Connection, store: str, version: int) -> None:
    db.execute(_SCHEMA_VERSIONS)
    db.execute(
        "INSERT INTO schema_versions(store,version) VALUES(?,?) "
        "ON CONFLICT(store) DO UPDATE SET version=excluded.version",
        (store, int(version)),
    )


def connect_or_quarantine(
    path: Path, connect: Callable[[], sqlite3.Connection]
) -> sqlite3.Connection:
    """Open a store connection, quarantining a corrupt database rather than dying.

    Almost everything in `mux.db` is rebuildable derivative data (native
    transcripts remain the authoritative source), while a malformed file used to
    raise out of store construction and take the daemon down at startup — under
    the desktop shell that presents as an app that simply refuses to come up, or
    restart-loops, until someone deletes the file by hand.
    """
    problem = verify_database(path)
    if problem is None:
        return connect()
    log.error("SQLite database %s is unusable (%s); quarantining and recreating", path, problem)
    stamp = int(os.stat(path).st_mtime)
    for suffix in ("", "-wal", "-shm"):
        source = Path(str(path) + suffix)
        if not source.exists():
            continue
        try:
            shutil.move(str(source), f"{source}.corrupt-{stamp}")
        except OSError:
            log.exception("could not quarantine %s", source)
            raise
    # The file behind this path is now a fresh one the probe has never seen.
    # Recording it healthy is what stops each remaining store re-probing (and
    # potentially re-quarantining) a database that was just replaced.
    _remember_integrity(path, None)
    return connect()


def _integrity_key(path: Path) -> str:
    try:
        return os.path.normcase(str(path.resolve()))
    except OSError:
        return os.path.normcase(str(path))


def _remember_integrity(path: Path, problem: str | None) -> None:
    with _INTEGRITY_GUARD:
        _INTEGRITY_RESULTS[_integrity_key(path)] = problem


def verify_database(path: Path) -> str | None:
    """The file's integrity verdict: a description of the problem, or None.

    Answered once per file per process (see `_INTEGRITY_RESULTS`). The lock is
    held across the probe on purpose: two stores opening the same database
    concurrently should wait for one answer rather than each pay for a full-file
    read, and the only databases involved are the two this daemon owns, so
    serialising distinct files costs nothing worth measuring.
    """
    key = _integrity_key(path)
    with _INTEGRITY_GUARD:
        if key in _INTEGRITY_RESULTS:
            return _INTEGRITY_RESULTS[key]
        problem = _integrity_problem(path) if path.exists() else None
        _INTEGRITY_RESULTS[key] = problem
        return problem


def prepare_database(path: Path) -> float:
    """Answer the integrity question up front; returns the seconds it took.

    Called once from the daemon's startup path, off the event loop, so the
    per-file cost is paid where it can be named and timed instead of inside the
    first store constructor that happens to touch the file - which is where it
    used to hide, on the event loop, eleven times over.
    """
    start = time.monotonic()
    verify_database(path)
    return time.monotonic() - start


def reset_integrity_cache() -> None:
    """Forget every cached verdict. For tests that rewrite a database file."""
    with _INTEGRITY_GUARD:
        _INTEGRITY_RESULTS.clear()


def _integrity_problem(path: Path) -> str | None:
    """Probe the file on a throwaway connection, closed before anything moves it.

    Deliberately not the store's own connection: Windows refuses to rename a file
    this process still holds open, and a failure raised *inside* the store's
    connect keeps its half-built connection alive in the traceback frame.
    """
    probe: sqlite3.Connection | None = None
    try:
        probe = sqlite3.connect(path)
        row = probe.execute("PRAGMA quick_check").fetchone()
        if row and str(row[0]).lower() == "ok":
            return None
        return f"quick_check: {row[0] if row else 'no result'}"
    except sqlite3.DatabaseError as exc:
        return str(exc)
    finally:
        if probe is not None:
            with suppress(sqlite3.Error):
                probe.close()


def database_operation_lock(path: Path) -> Any:
    """Return the process-wide operation lock for one SQLite database file."""

    key = os.path.normcase(str(path.resolve()))
    with _LOCKS_GUARD:
        lock = _DATABASE_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _DATABASE_LOCKS[key] = lock
        return lock


def run_sqlite_operation[T](
    db: sqlite3.Connection, operation_lock: Any, operation: Callable[[], T]
) -> T:
    """Run one coordinated store operation without leaking a transaction.

    A failed statement can leave Python's sqlite3 connection inside its implicit
    transaction. With several WAL connections sharing the mux database, that
    abandoned transaction retains the only writer slot until an explicit
    rollback and can make unrelated PTY/history writes fail as "database is
    locked". Every dedicated store worker uses this guard as a final safety net.

    The per-database lock also serializes complete operations across the history,
    automation, telemetry, and voice worker threads. WAL still permits external
    readers, while swe-mux never makes its own connections compete for SQLite's
    single writer slot.
    """

    with operation_lock:
        try:
            result = operation()
            if db.in_transaction:
                db.rollback()
                raise RuntimeError("SQLite store operation returned with an open transaction")
            return result
        except BaseException:
            if db.in_transaction:
                try:
                    db.rollback()
                except sqlite3.Error:
                    pass
            raise
