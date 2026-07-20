from __future__ import annotations

import os
import sqlite3
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

_LOCKS_GUARD = threading.Lock()
_DATABASE_LOCKS: dict[str, Any] = {}


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
