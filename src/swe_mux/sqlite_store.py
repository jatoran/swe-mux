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

# How long a store write may wait for SQLite's single writer slot while this
# daemon is draining on its way out. The number the stores connect with (5s, the
# `sqlite3.connect` default) is sized for this process competing with itself; a
# session-preserving restart is the one window where it competes with a *second
# daemon*, and that daemon's own startup can hold the file for far longer than
# five seconds - a measured 12-61s of `PRAGMA quick_check` over a 2.7 GB
# `mux.db` before it serves anything.
#
# Deliberately a *whole-drain* budget rather than a per-statement one: the
# predecessor does not get to choose how long it lives. The redeploy terminates
# it about three seconds after its listener closes, so a per-statement 20s wait
# would simply be killed mid-wait and lose the same rows more slowly.
SHUTDOWN_DRAIN_BUDGET_SECONDS = 8.0

_DRAIN_GUARD = threading.Lock()
_DRAIN_DEADLINE: float | None = None

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


def begin_shutdown_drain(budget_seconds: float = SHUTDOWN_DRAIN_BUDGET_SECONDS) -> None:
    """Enter the shutdown drain: store writes may now wait out a foreign lock.

    Called once, at the top of the daemon's teardown. From here until the
    process exits, a store operation that finds the database's writer slot held
    by another process waits for it (up to what is left of the budget) instead
    of failing immediately - and if it fails anyway, it says so.
    """
    global _DRAIN_DEADLINE
    with _DRAIN_GUARD:
        _DRAIN_DEADLINE = time.monotonic() + max(0.0, budget_seconds)


def end_shutdown_drain() -> None:
    """Leave the drain. For tests; a real daemon exits instead."""
    global _DRAIN_DEADLINE
    with _DRAIN_GUARD:
        _DRAIN_DEADLINE = None


def drain_remaining_ms() -> int:
    """Milliseconds left in the shutdown drain, or 0 when not draining."""
    with _DRAIN_GUARD:
        deadline = _DRAIN_DEADLINE
    if deadline is None:
        return 0
    return max(0, int((deadline - time.monotonic()) * 1000))


def is_locked_error(error: BaseException) -> bool:
    """Whether this is SQLite refusing to wait any longer for the writer slot.

    Matched on the message because `sqlite3` reports both `SQLITE_BUSY` and
    `SQLITE_LOCKED` as a bare `OperationalError`. Narrow on purpose: the same
    class is also how a store reports a missing table and how `history.py`
    surfaces an interrupted query, and treating either as a lock would retry
    something that will never succeed.
    """
    if not isinstance(error, sqlite3.OperationalError):
        return False
    text = str(error).lower()
    return "locked" in text or "busy" in text


def _operation_name(operation: Callable[[], Any]) -> str:
    """A store operation's identity for a log line: `Store.method`.

    Every store's operation is a closure named `op` inside the method that
    submits it (73 of them in `history.py` alone), so the qualified name already
    carries both halves and no store has to pass its own name down. `<locals>`
    markers are dropped and the innermost two names kept, which is `Store.method`
    for a store and still readable for anything nested more deeply.
    """
    name = getattr(operation, "__qualname__", "") or repr(operation)
    parts = [part for part in name.split(".") if part and part != "<locals>"]
    if len(parts) > 1 and parts[-1] == "op":
        parts.pop()
    return ".".join(parts[-2:]) if parts else name


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

    Two things happen here that this process cannot arrange anywhere else,
    because this is the one place every store's every operation passes through.

    **During a shutdown drain the busy timeout is widened** to what is left of
    the drain budget, so a last write that collides with a *successor daemon*
    already holding the file waits for it. Widening the timeout rather than
    re-running the operation is deliberate: SQLite does the waiting, so nothing
    is executed twice, and an operation that commits in batches cannot re-apply
    a batch it already committed.

    **A write lost to a lock is loud.** It used to be a bare `OperationalError`
    that each caller swallowed or logged in its own words, which is how ten of
    them - the telemetry, recovery, notification and history rows that would
    have explained a restart - went missing across a restart with nothing in
    `daemon.log` naming the loss as a loss.
    """

    with operation_lock:
        drain_ms = drain_remaining_ms()
        if drain_ms > 0:
            # On the store's own worker thread, inside the lock: legal, cheap,
            # and it cannot race another operation on this connection.
            with suppress(sqlite3.Error):
                db.execute(f"PRAGMA busy_timeout={drain_ms}")
        try:
            result = operation()
            if db.in_transaction:
                db.rollback()
                raise RuntimeError("SQLite store operation returned with an open transaction")
            return result
        except BaseException as error:
            if db.in_transaction:
                try:
                    db.rollback()
                except sqlite3.Error:
                    pass
            if is_locked_error(error):
                log.error(
                    "sqlite_write_lost operation=%s draining=%s error=%s",
                    _operation_name(operation),
                    "true" if drain_ms > 0 else "false",
                    error,
                    extra={
                        "sqlite_operation": _operation_name(operation),
                        "sqlite_draining": drain_ms > 0,
                        "sqlite_wait_ms": drain_ms,
                    },
                )
            raise
