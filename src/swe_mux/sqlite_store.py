from __future__ import annotations

import logging
import os
import shutil
import sqlite3
import threading
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_LOCKS_GUARD = threading.Lock()
_DATABASE_LOCKS: dict[str, Any] = {}

_SCHEMA_VERSIONS = (
    "CREATE TABLE IF NOT EXISTS schema_versions("
    "store TEXT PRIMARY KEY, version INTEGER NOT NULL)"
)


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
    problem = _integrity_problem(path) if path.exists() else None
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
    return connect()


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
