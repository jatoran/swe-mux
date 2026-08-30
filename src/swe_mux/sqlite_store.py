from __future__ import annotations

import json
import logging
import os
import shutil
import sqlite3
import threading
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
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
# `mux.db` before it serves anything. The conditional full check makes that
# hold milliseconds on most planned restarts, but the budget stays sized for
# the starts that still run it: after an unclean death, and when the last
# passing check has aged out.
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

# How long a passing `PRAGMA quick_check` stays trusted before another is owed.
# The full probe reads every page, so its cost is the size of the file - a
# measured 60-84s per cold pass against this host's 3.36 GB `mux.db`.
# A constant rather than a `Config` field on purpose: the record file beside
# the database (`<db>.last-verified.json`) is the operator's lever - deleting
# it forces a full probe on the next start - and an interval knob would be a
# way to quietly weaken corruption detection without a code review seeing it.
FULL_VERIFICATION_INTERVAL_SECONDS = 24 * 3600.0

# The full check no longer runs on the startup path at all (2026-08-30). It runs
# behind the ready daemon, and this is the argument for why that is not a
# weakening - which matters, because the trigger it replaces *looked* like the
# safety-critical one.
#
# `mux.db` is opened `journal_mode=wal` with `synchronous=FULL` (verified by
# `test_startup_gate`). Under those two settings SQLite's atomic-commit contract
# says a process kill and an OS crash both leave the file consistent: that is
# what the write-ahead log is *for*, and recovery happens in the next opener
# before any store reads a row. So "the previous daemon died uncleanly" - a
# reboot, a taskkill, a hard crash - is not evidence about the bytes on disk. It
# was nonetheless the one condition that forced a full check to run *while the
# user waited*, which on 2026-08-30 turned a reboot into an 85.7s start whose
# 77.7s `database-integrity` phase was answering a question the storage layer
# had already answered.
#
# What actually corrupts a SQLite file is the storage beneath it: a failing
# drive, a filesystem bug, a device that lies about flushing. None of those
# correlate with how the last process exited, and none of them are detectable
# any sooner by making the check block a start.
#
# So the signal is kept and its *urgency* is what it now buys: an unclean death
# schedules the full check immediately rather than at the next interval, and it
# runs behind an already-serving daemon. Nothing is checked less often than
# before; the check simply stopped being on the critical path.
NEAR_TERM_VERIFICATION_DELAY_SECONDS = 5.0

# How long the background check waits before starting on an ordinary start.
# Long enough to be behind session reattachment and the first burst of client
# requests, short enough that a daemon left running for an afternoon has still
# answered the question.
ROUTINE_VERIFICATION_DELAY_SECONDS = 300.0

# `PRAGMA quick_check` walks the btree one 4 KiB page at a time, synchronously,
# through a 2 MB page cache. On a cold cache that is one queue-depth-1 read per
# page with no readahead: measured 77.67s against a 3.36 GB `mux.db` on an NVMe
# SSD, an effective 43 MB/s on a device rated in gigabytes per second. The same
# check against a warm cache is 11.55s, so ~85% of a cold check is the daemon
# waiting on reads it could have issued all at once.
#
# Reading the file once, sequentially, in large chunks before the walk turns
# that into a streaming read and lets the walk run at its warm cost. It is pure
# prefetch - the bytes are dropped - so it cannot change the verdict, only what
# the verdict costs.
PREFETCH_CHUNK_BYTES = 8 * 1024 * 1024

# Prefetch is only ever a win while the file fits in memory the machine can
# spare; past that the tail evicts the head and the walk pays the reads anyway,
# on a machine that is now also short of cache. Measured against available
# rather than total memory because a busy host is exactly where this matters.
PREFETCH_MEMORY_FRACTION = 0.5

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
    # potentially re-quarantining) a database that was just replaced. The
    # durable record follows for the same reason across starts: the replacement
    # is known-good by construction, so the next start need not re-scan it.
    _remember_integrity(path, None)
    record_database_verified(path)
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


def verification_record_path(path: Path) -> Path:
    """Where a database's last full-check verdict is recorded, beside the file."""
    return Path(str(path) + ".last-verified.json")


@dataclass(frozen=True)
class VerificationRecord:
    """The durable verdict of the last full check, as read back off disk.

    Two shapes, and the second is the one that earns the type. A *pass* carries
    `verified_at` and is what the interval is measured against. A *failure*
    carries `failed_at` and `problem`, and exists because the full check now
    runs behind an already-serving daemon: by the time it finds a mangled page
    every store is already open on that file, so the quarantine-and-recreate
    path cannot run right then without pulling the runtime out from under live
    sessions. Persisting the verdict is what makes the remediation deterministic
    instead - the next start reads it, hands the problem to the first store's
    `connect_or_quarantine`, and the file is quarantined before anything writes
    another row to it.
    """

    verified_at: float | None = None
    failed_at: float | None = None
    problem: str | None = None

    @property
    def failed(self) -> bool:
        return self.problem is not None


def read_verification_record(path: Path) -> VerificationRecord:
    """Read the durable verdict beside `path`; an empty record when there is none.

    Anything unreadable - a missing file, mangled JSON, a wrong shape - reads as
    "never verified", which fails safe: a full check is owed.
    """
    try:
        raw = json.loads(verification_record_path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return VerificationRecord()
    if not isinstance(raw, dict):
        return VerificationRecord()

    def moment(key: str) -> float | None:
        value = raw.get(key)
        return float(value) if isinstance(value, (int, float)) else None

    problem = raw.get("problem")
    return VerificationRecord(
        verified_at=moment("verified_at"),
        failed_at=moment("failed_at"),
        problem=problem if isinstance(problem, str) and problem else None,
    )


def _write_verification_record(path: Path, payload: dict[str, Any]) -> None:
    """Replace the record beside `path` atomically; never raises.

    Best-effort by design: on a filesystem that refuses the write, a full check
    is simply owed again next time, which is the pre-existing behaviour rather
    than a new failure mode.
    """
    record = verification_record_path(path)
    temporary = record.with_name(record.name + ".tmp")
    try:
        temporary.write_text(json.dumps(payload), encoding="utf-8")
        os.replace(temporary, record)
    except OSError:
        log.warning(
            "could not record the integrity verdict for %s; the next start will "
            "run the full check again",
            path,
            exc_info=True,
        )


def record_database_verified(path: Path, now: float | None = None) -> None:
    """Durably record that the file behind `path` passed a full check just now."""
    _write_verification_record(path, {"verified_at": time.time() if now is None else now})


def record_database_problem(path: Path, problem: str, now: float | None = None) -> None:
    """Durably record that a full check found `problem` in the file behind `path`.

    Read back by `prepare_database` on the next start, which is where it becomes
    a quarantine. Deliberately overwrites any passing record: the file has been
    read since, and the newer verdict is the one that describes it.
    """
    _write_verification_record(
        path,
        {"failed_at": time.time() if now is None else now, "problem": problem},
    )


def _full_check_plan(
    path: Path, predecessor_died_uncleanly: bool, interval_seconds: float
) -> tuple[bool, float, str]:
    """Whether a full check is owed, how soon, and why - in those words.

    The delay is the whole difference between the two reasons a check is owed.
    An unclean death is not evidence about the bytes (see
    `NEAR_TERM_VERIFICATION_DELAY_SECONDS`), but it is the cheapest correlate
    available of "something happened to this machine", so it earns promptness
    rather than a blocked start.
    """
    if predecessor_died_uncleanly:
        return True, NEAR_TERM_VERIFICATION_DELAY_SECONDS, "the previous daemon died uncleanly"
    if interval_seconds <= 0:
        return True, NEAR_TERM_VERIFICATION_DELAY_SECONDS, "the verification interval is disabled"
    record = read_verification_record(path)
    if record.verified_at is None:
        return True, ROUTINE_VERIFICATION_DELAY_SECONDS, "no passing full check is on record"
    age = time.time() - record.verified_at
    if age < 0:
        return (
            True,
            ROUTINE_VERIFICATION_DELAY_SECONDS,
            "the last full check is recorded in the future (the clock moved)",
        )
    if age > interval_seconds:
        return (
            True,
            ROUTINE_VERIFICATION_DELAY_SECONDS,
            f"the last passing full check was {age / 3600:.1f}h ago, "
            f"beyond the {interval_seconds / 3600:.1f}h window",
        )
    return (
        False,
        0.0,
        f"the last passing full check was {age / 3600:.1f}h ago, "
        f"within the {interval_seconds / 3600:.1f}h window",
    )


@dataclass(frozen=True)
class DatabasePreparation:
    """What the startup integrity phase did to one database, and why."""

    seconds: float
    mode: str  # "light" read the header and schema; "quarantine" acted on a recorded failure
    reason: str
    problem: str | None
    full_check_owed: bool = False
    full_check_delay_seconds: float = 0.0


def prepare_database(
    path: Path,
    *,
    predecessor_died_uncleanly: bool = False,
    full_verification_interval_seconds: float = FULL_VERIFICATION_INTERVAL_SECONDS,
) -> DatabasePreparation:
    """Answer the integrity question the startup path actually needs, and say what
    the background owes.

    The startup path runs the milliseconds-scale header-and-schema probe and
    nothing else. That probe catches the class the quarantine was built for - a
    truncated or overwritten file, an unparseable schema, the class that used to
    crash-loop the daemon before a store could open - and it is the only class a
    start can act on anyway, because quarantining is only safe before the first
    store connects.

    The full `PRAGMA quick_check` is not skipped; it is *scheduled*. Its cost is
    the size of the file (60-84s cold against a 3.36 GB `mux.db`) and it answers
    a question about the storage layer, not about this start, so it runs behind
    a ready daemon. See `NEAR_TERM_VERIFICATION_DELAY_SECONDS` for why "the
    previous daemon died uncleanly" no longer blocks a start, and why it still
    changes when the check runs.

    A recorded *failure* from a previous run of that background check is the one
    thing here that is not a probe: it is handed straight to the verdict cache,
    so the first store to open the file takes `connect_or_quarantine`'s
    quarantine-and-recreate path. That is the deferred half of a remediation the
    background check cannot perform itself.
    """
    start = time.monotonic()
    if not path.exists():
        return DatabasePreparation(
            time.monotonic() - start, "light", "the file does not exist", None
        )
    record = read_verification_record(path)
    if record.failed and record.problem is not None:
        # Believe the stronger probe. A background full check read every page of
        # this file and found it damaged; the header-and-schema probe reads far
        # too little to overturn that, so running it here would only risk
        # replacing a true verdict with a passing one.
        _remember_integrity(path, record.problem)
        return DatabasePreparation(
            time.monotonic() - start,
            "quarantine",
            "a previous background full check recorded a problem",
            record.problem,
        )
    problem = _light_integrity_problem(path)
    _remember_integrity(path, problem)
    owed, delay, reason = _full_check_plan(
        path, predecessor_died_uncleanly, full_verification_interval_seconds
    )
    return DatabasePreparation(
        time.monotonic() - start, "light", reason, problem, owed, delay
    )


def reset_integrity_cache() -> None:
    """Forget every cached verdict. For tests that rewrite a database file."""
    with _INTEGRITY_GUARD:
        _INTEGRITY_RESULTS.clear()


def _light_integrity_problem(path: Path) -> str | None:
    """A milliseconds-scale probe for gross corruption: the header and the schema.

    Not a substitute for `PRAGMA quick_check` - a mangled interior page passes
    here - but it catches the class that used to take the daemon down at
    startup (a truncated or overwritten file, an unparseable schema), which is
    the class `connect_or_quarantine` exists to remediate. Same throwaway
    connection discipline as `_integrity_problem`, for the same Windows rename
    reason.
    """
    probe: sqlite3.Connection | None = None
    try:
        probe = sqlite3.connect(path)
        probe.execute("PRAGMA schema_version").fetchone()
        probe.execute("SELECT count(*) FROM sqlite_master").fetchone()
        return None
    except sqlite3.DatabaseError as exc:
        return str(exc)
    finally:
        if probe is not None:
            with suppress(sqlite3.Error):
                probe.close()


def _integrity_problem(path: Path, control: VerificationControl | None = None) -> str | None:
    """Probe the file on a throwaway connection, closed before anything moves it.

    Deliberately not the store's own connection: Windows refuses to rename a file
    this process still holds open, and a failure raised *inside* the store's
    connect keeps its half-built connection alive in the traceback frame.

    `control` is what makes this abortable from another thread, which it has to
    be now that it runs on a worker behind a live daemon: a 12-78s walk that
    ignored shutdown would be joined by `shutdown_default_executor` *after* every
    log handler has reported a clean stop, and read as an unexplained hang.
    """
    probe: sqlite3.Connection | None = None
    try:
        probe = sqlite3.connect(path)
        if control is not None:
            control.adopt(probe)
        row = probe.execute("PRAGMA quick_check").fetchone()
        if row and str(row[0]).lower() == "ok":
            return None
        return f"quick_check: {row[0] if row else 'no result'}"
    except sqlite3.DatabaseError as exc:
        if control is not None and control.cancelled:
            # `interrupt()` surfaces here as an ordinary DatabaseError. Reporting
            # it as a problem would quarantine a healthy database because the
            # daemon was asked to stop, which is the worst available outcome.
            return None
        return str(exc)
    finally:
        if control is not None:
            control.release()
        if probe is not None:
            with suppress(sqlite3.Error):
                probe.close()


class VerificationControl:
    """Aborts a full verification running on a worker thread.

    Two halves, because the work has two halves that stop differently. The
    prefetch is a plain read loop and checks `cancelled` between chunks;
    `PRAGMA quick_check` is a single statement inside SQLite and can only be
    stopped by `Connection.interrupt()`, which is documented as safe to call
    from another thread. Neither half can be stopped by cancelling the awaiting
    task, because `asyncio.to_thread` does not cancel its worker.
    """

    def __init__(self) -> None:
        self._guard = threading.Lock()
        self._cancelled = False
        self._connection: sqlite3.Connection | None = None

    @property
    def cancelled(self) -> bool:
        with self._guard:
            return self._cancelled

    def cancel(self) -> None:
        """Ask the worker to stop. Safe to call from any thread, and repeatedly."""
        with self._guard:
            self._cancelled = True
            connection = self._connection
        if connection is not None:
            with suppress(sqlite3.Error):
                connection.interrupt()

    def adopt(self, connection: sqlite3.Connection) -> None:
        """Register the connection to interrupt, honouring a cancel that already came."""
        with self._guard:
            self._connection = connection
            already = self._cancelled
        if already:
            with suppress(sqlite3.Error):
                connection.interrupt()

    def release(self) -> None:
        with self._guard:
            self._connection = None


def _available_memory_bytes() -> int | None:
    """Memory this machine can spare right now, or None if it will not say."""
    try:
        import psutil

        return int(psutil.virtual_memory().available)
    except Exception:  # noqa: BLE001 - a prefetch heuristic never fails a check
        return None


def prefetch_database(path: Path, control: VerificationControl | None = None) -> float:
    """Read the file once, sequentially, so the walk that follows runs warm.

    Returns the seconds spent, or 0.0 when the prefetch was skipped or failed.
    Pure prefetch: the bytes are dropped, so this cannot change a verdict - only
    what the verdict costs (see `PREFETCH_CHUNK_BYTES` for the measurements).

    Skipped when the file will not fit in memory the machine can spare, because
    past that point the tail evicts the head and the walk pays for the reads
    anyway on a host that is now also short of cache.
    """
    try:
        size = path.stat().st_size
    except OSError:
        return 0.0
    available = _available_memory_bytes()
    if available is not None and size > available * PREFETCH_MEMORY_FRACTION:
        log.info(
            "skipping the integrity prefetch for %s: %.2f GB does not fit in the "
            "%.2f GB this machine can spare",
            path,
            size / 1e9,
            available * PREFETCH_MEMORY_FRACTION / 1e9,
        )
        return 0.0
    start = time.monotonic()
    try:
        with open(path, "rb", buffering=0) as handle:
            # unsupervised-loop-ok: bounded by one file handle reaching EOF, on a
            # worker thread, and it checks `control` every chunk. It is not a
            # daemon loop - the supervised task is the caller.
            while True:
                if control is not None and control.cancelled:
                    return time.monotonic() - start
                if not handle.read(PREFETCH_CHUNK_BYTES):
                    break
    except OSError:
        # The walk still works from a cold cache; it is only slower.
        log.warning("could not prefetch %s before its integrity check", path, exc_info=True)
        return 0.0
    return time.monotonic() - start


@dataclass(frozen=True)
class FullVerification:
    """What one background full check did, what it cost, and what it found."""

    seconds: float
    prefetch_seconds: float
    problem: str | None
    cancelled: bool


def run_full_verification(
    path: Path, control: VerificationControl | None = None
) -> FullVerification:
    """Run `PRAGMA quick_check` over every page of `path`, prefetched and abortable.

    The caller owns what the verdict *means*: this records nothing and
    quarantines nothing, because it runs behind a daemon whose stores already
    hold the file open. `server._database_integrity_loop` is the one caller.
    """
    start = time.monotonic()
    prefetch_seconds = prefetch_database(path, control)
    if control is not None and control.cancelled:
        return FullVerification(time.monotonic() - start, prefetch_seconds, None, True)
    problem = _integrity_problem(path, control)
    cancelled = control is not None and control.cancelled
    return FullVerification(
        time.monotonic() - start, prefetch_seconds, None if cancelled else problem, cancelled
    )


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
