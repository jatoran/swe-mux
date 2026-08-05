"""Durable status-detection timeline: the transition ledger made investigable.

The per-session ledger rings (`Session.state_transitions` / `state_changes`)
answer "how did this session reach its state" only while the daemon and the
session are both alive: a restart or redeploy wipes them, a busy turn's detail
churn evicts old entries, and a killed session takes its ledger with it. This
module persists every ledger entry — transitions *and* the non-transition
kinds (watchdog recoveries, standing-activity actions, cli-state
corroboration, layer readings, refused transitions, observer faults) — into a
SQLite table keyed by ``(session_id, agent_run_id, seq)`` so "at 12:39 the
session showed `working` — what did each layer say?" stays answerable after
the fact, including for sessions that no longer exist.

Persistence is a **sink, not part of the transition contract**:
``apply_state_transition`` (shared verbatim with the deterministic replay
harness) still only appends to the in-memory ring. The ring itself
(``LedgerRing``) stamps each entry with a session-lifetime monotonic ``seq``
and the run id it belongs to, and nudges a guarded callback — the same
discipline as ``Session.meta_sink`` — which marks the session dirty for a
write-behind batch drain on this store's dedicated SQLite worker. Nothing on
the hook ingress or transition path ever waits on the database.

Run-keying matters: a conversation rollover starts a new agent run, and its
timeline rows must not mix with the predecessor's (the cross-run bleed class
of incident). Entries are therefore stamped with the run id **at append
time**, not at drain time.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import time
from collections import deque
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
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

STATUS_TIMELINE_SCHEMA_VERSION = 1
STATUS_TIMELINE_FLUSH_LOOP = "status-timeline-flush"

# The write-behind drain batches a busy turn's ledger churn: after the first
# nudge it waits this long so dozens of same-second detail updates land as one
# executemany instead of dozens of commits.
FLUSH_BATCH_SECONDS = 1.0
# The flush loop wakes at least this often even with no nudge, as a safety net
# for a sink callback that was lost (it also drives periodic retention).
FLUSH_IDLE_WAKE_SECONDS = 60.0
PRUNE_INTERVAL_SECONDS = 6 * 3600.0
# Hard cap on rows one timeline query returns; the caller learns it was cut.
TIMELINE_QUERY_MAX_ROWS = 5000

SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS status_timeline (
  session_id TEXT NOT NULL,
  agent_run_id TEXT NOT NULL,
  seq INTEGER NOT NULL,
  ts REAL NOT NULL,
  kind TEXT NOT NULL,
  entry_json TEXT NOT NULL,
  PRIMARY KEY(session_id, agent_run_id, seq)
);
CREATE INDEX IF NOT EXISTS idx_status_timeline_session_ts
  ON status_timeline(session_id, ts);
CREATE INDEX IF NOT EXISTS idx_status_timeline_run_ts
  ON status_timeline(agent_run_id, ts);
CREATE INDEX IF NOT EXISTS idx_status_timeline_ts ON status_timeline(ts);
"""


class LedgerRing:
    """Bounded ledger ring that stamps entries for the durable sink.

    A drop-in for the plain ``deque`` the ledger used to be — every producer
    calls ``append`` and every consumer iterates/indexes — with two additions:

    - each entry is stamped with a monotonic ``seq`` (session-lifetime within
      one daemon boot; the store maps it onto a durable continuation) and the
      ``agent_run_id`` current *at append time*, so a rollover's successor
      entries can never be filed under the predecessor's run;
    - an optional guarded ``sink`` callback is nudged after each append, the
      ``meta_sink`` discipline: never raises into the appender, never blocks.

    The replay harness keeps its plain deques: seq stamping is bookkeeping for
    persistence, not part of the shared transition contract.
    """

    __slots__ = ("_entries", "next_seq", "run_id_provider", "sink")

    def __init__(
        self, maxlen: int, run_id_provider: Callable[[], str] | None = None
    ) -> None:
        self._entries: deque[dict[str, Any]] = deque(maxlen=maxlen)
        self.next_seq = 0
        self.run_id_provider = run_id_provider
        self.sink: Callable[[], None] | None = None

    def append(self, entry: dict[str, Any]) -> None:
        entry["seq"] = self.next_seq
        self.next_seq += 1
        provider = self.run_id_provider
        if provider is not None:
            try:
                entry.setdefault("agent_run_id", provider())
            except Exception:  # noqa: BLE001 - a broken provider must not lose the entry
                entry.setdefault("agent_run_id", None)
        self._entries.append(entry)
        sink = self.sink
        if sink is not None:
            try:
                sink()
            except Exception:  # noqa: BLE001 - the sink must never raise into a transition
                log.debug("ledger sink failed", exc_info=True)

    def __iter__(self) -> Iterator[dict[str, Any]]:
        return iter(self._entries)

    def __len__(self) -> int:
        return len(self._entries)

    def __bool__(self) -> bool:
        return bool(self._entries)

    def __getitem__(self, index: int) -> dict[str, Any]:
        return self._entries[index]


def note_layer_reading(
    session: Any,
    layer: str,
    reading: str,
    *,
    now: float,
    detail: dict[str, Any] | None = None,
) -> bool:
    """Ledger one detection-ladder layer's reading — only when it changes.

    The ladder's layer readings (`pty_tail_state` verdict, the cli-state
    file's status, hook recency vs the staleness threshold) are computed every
    watchdog pass and were discarded, which is what made "what did each layer
    say at 12:39" unanswerable. Appending on *change only* records the full
    step function of each layer's reading without a 5-second firehose: the
    reading at any instant is the last entry at or before it.

    Returns True when an entry was appended. Safe on harness stand-ins that
    carry no ``layer_readings`` tracker or ledger.
    """
    readings = getattr(session, "layer_readings", None)
    if readings is None:
        readings = {}
        try:
            session.layer_readings = readings
        except (AttributeError, TypeError):
            return False
    previous = readings.get(layer)
    if previous == reading:
        return False
    readings[layer] = reading
    ledger = getattr(session, "state_transitions", None)
    if ledger is None:
        return False
    entry: dict[str, Any] = {
        "ts": now,
        "kind": "layer_reading",
        "layer": layer,
        "reading": reading,
        "previous": previous,
    }
    if detail:
        entry.update(detail)
    ledger.append(entry)
    return True


class StatusTimelineStore:
    """Durable, bounded store for the per-session detection timeline.

    Same shape as the other mux.db stores: all sqlite access confined to one
    dedicated worker thread, operations serialized in submission order, rows
    pruned by a retention window like operational telemetry.
    """

    _db: sqlite3.Connection

    def __init__(self, path: Path, *, retention_days: int = 30) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.retention_days = retention_days
        self._operation_lock = database_operation_lock(path)
        self._closed = False
        self._executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="mux-status-timeline-db"
        )
        self._executor.submit(self._connect).result()
        # Write-behind state. `_dirty` holds a strong reference to each session
        # awaiting a drain, so a session removed from the manager between its
        # final ledger entry and the next flush still gets persisted.
        self._dirty: dict[str, Any] = {}
        self._wake = asyncio.Event()
        # Last in-memory seq drained per session id (checkpoint), and the
        # durable-seq base per (session_id, run_id): persisted seq = base +
        # in-memory seq, where base continues after whatever a previous daemon
        # boot already wrote. That is what makes restarts collision- and
        # double-write-free without the ring knowing the database exists.
        self._cursor: dict[str, int] = {}
        self._base: dict[tuple[str, str], int] = {}
        # Serializes flushes: the loop drain, an endpoint's flush-then-query,
        # and the session-end drain can interleave on the event loop, and two
        # concurrent base computations for one run would mint two different
        # durable-seq offsets for the same entries (duplicated rows).
        self._flush_lock = asyncio.Lock()
        self._next_prune = time.monotonic() + PRUNE_INTERVAL_SECONDS
        self.write_stats: dict[str, int] = {
            "rows_written": 0,
            "rows_lost_to_ring_eviction": 0,
            "flushes": 0,
            "rows_pruned": 0,
        }

    def _open(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, check_same_thread=False)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA synchronous=NORMAL")
        db.execute("PRAGMA busy_timeout=5000")
        db.execute("PRAGMA cache_size=-8000")
        return db

    def _connect(self) -> None:
        with self._operation_lock:
            self._db = connect_or_quarantine(self.path, self._open)
            self._db.executescript(SCHEMA)
            write_schema_version(self._db, "status_timeline", STATUS_TIMELINE_SCHEMA_VERSION)
            self._db.commit()

    async def _run(self, fn: Callable[[], T]) -> T:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self._executor, run_sqlite_operation, self._db, self._operation_lock, fn
        )

    # --- sink side -----------------------------------------------------------

    def note_dirty(self, session: Any) -> None:
        """Sink callback body: O(1), synchronous, never raises upward.

        Called from `LedgerRing.append` on the event loop; the actual write
        happens on the store's worker after the batch window.
        """
        self._dirty[session.record.id] = session
        self._wake.set()

    def attach(self, session: Any) -> None:
        """Wire one live session's ledger ring into the write-behind drain."""
        ring = getattr(session, "state_transitions", None)
        if not isinstance(ring, LedgerRing):
            return
        ring.sink = lambda: self.note_dirty(session)
        if len(ring):
            # Entries appended before the sink existed (spawn ledgering runs
            # before the manager attaches) are already in the ring; mark dirty
            # so the first batch picks them up.
            self.note_dirty(session)

    async def flush_session(self, session: Any) -> int:
        """Drain one session's unflushed ledger entries. Returns rows written."""
        async with self._flush_lock:
            return await self._flush_session_locked(session)

    async def _flush_session_locked(self, session: Any) -> int:
        ring = getattr(session, "state_transitions", None)
        if not isinstance(ring, LedgerRing):
            return 0
        sid = str(session.record.id)
        self._dirty.pop(sid, None)
        cursor = self._cursor.get(sid, -1)
        entries = [entry for entry in ring if int(entry.get("seq", -1)) > cursor]
        if not entries:
            return 0
        first_seq = int(entries[0]["seq"])
        if first_seq > cursor + 1:
            # The bounded ring evicted entries between drains. Counted, never
            # silent: a lost row reads as "nothing happened" in a post-mortem.
            self.write_stats["rows_lost_to_ring_eviction"] += first_seq - cursor - 1
        default_run = str(
            getattr(session.record, "agent_run_id", None) or session.record.id
        )
        run_ids = {
            str(entry.get("agent_run_id") or default_run) for entry in entries
        }
        bases: dict[str, int] = {}
        for run_id in run_ids:
            key = (sid, run_id)
            base = self._base.get(key)
            if base is None:
                first_for_run = min(
                    int(entry["seq"])
                    for entry in entries
                    if str(entry.get("agent_run_id") or default_run) == run_id
                )
                max_seq = await self._run(partial(self._max_seq, sid, run_id))
                base = (max_seq + 1) - first_for_run if max_seq is not None else 0
                self._base[key] = base
            bases[run_id] = base
        rows = [
            (
                sid,
                str(entry.get("agent_run_id") or default_run),
                bases[str(entry.get("agent_run_id") or default_run)]
                + int(entry["seq"]),
                float(entry.get("ts") or 0.0),
                str(entry.get("kind") or "unknown"),
                json.dumps(entry, default=str),
            )
            for entry in entries
        ]

        def op() -> int:
            self._db.executemany(
                "INSERT OR IGNORE INTO status_timeline"
                "(session_id,agent_run_id,seq,ts,kind,entry_json) VALUES(?,?,?,?,?,?)",
                rows,
            )
            self._db.commit()
            return len(rows)

        written = await self._run(op)
        self._cursor[sid] = int(entries[-1]["seq"])
        self.write_stats["rows_written"] += written
        self.write_stats["flushes"] += 1
        return written

    def _max_seq(self, session_id: str, run_id: str) -> int | None:
        row = self._db.execute(
            "SELECT MAX(seq) FROM status_timeline WHERE session_id=? AND agent_run_id=?",
            (session_id, run_id),
        ).fetchone()
        value = row[0] if row else None
        return int(value) if value is not None else None

    async def flush_dirty(self) -> int:
        total = 0
        for sid in tuple(self._dirty):
            session = self._dirty.get(sid)
            if session is None:
                continue
            try:
                total += await self.flush_session(session)
            except Exception:
                log.exception("status timeline flush failed for %s", sid)
                self._dirty.pop(sid, None)
        return total

    # --- background loop -----------------------------------------------------

    def start(self) -> None:
        background.start(STATUS_TIMELINE_FLUSH_LOOP, self._flush_loop)

    async def stop(self) -> None:
        await background.stop(STATUS_TIMELINE_FLUSH_LOOP)
        # Final drain so terminal entries appended during shutdown survive.
        try:
            await self.flush_dirty()
        except Exception:
            log.exception("final status timeline flush failed")

    async def _flush_loop(self) -> None:
        while True:
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=FLUSH_IDLE_WAKE_SECONDS)
            except TimeoutError:
                pass
            if self._wake.is_set():
                # Batch window: let a busy turn's churn accumulate so it lands as one
                # write instead of one commit per entry. Deliberately *outside* the
                # supervisor's guard: `iteration()` times what it wraps, so a wait
                # inside it is reported as this loop's cost. Measured live before the
                # move, that put a purely idle batching sleep at the top of `costliest`
                # with a 1.02s p95 — the loop looked like the most expensive in the
                # daemon while doing nothing at all.
                await asyncio.sleep(FLUSH_BATCH_SECONDS)
                self._wake.clear()
            with background.iteration(STATUS_TIMELINE_FLUSH_LOOP):
                await self.flush_dirty()
                if time.monotonic() >= self._next_prune:
                    self._next_prune = time.monotonic() + PRUNE_INTERVAL_SECONDS
                    await self.prune()

    # --- read side -----------------------------------------------------------

    async def timeline(
        self,
        identity: str,
        *,
        from_ts: float | None = None,
        to_ts: float | None = None,
        kinds: list[str] | None = None,
        limit: int = TIMELINE_QUERY_MAX_ROWS,
    ) -> tuple[list[dict[str, Any]], bool]:
        """Chronological timeline slice for one session (or one agent run).

        ``identity`` matches either the mux session id or an agent run id, so a
        history row (keyed by run id) leads here directly. Returns
        ``(entries, truncated)`` — entries oldest-first, each the verbatim
        ledger payload overlaid with the durable ``seq``/``agent_run_id`` keys.
        """
        limit = max(1, min(int(limit), TIMELINE_QUERY_MAX_ROWS))

        def op() -> tuple[list[dict[str, Any]], bool]:
            clauses: list[str] = []
            shared: list[Any] = []
            if from_ts is not None:
                clauses.append("ts>=?")
                shared.append(float(from_ts))
            if to_ts is not None:
                clauses.append("ts<=?")
                shared.append(float(to_ts))
            if kinds:
                placeholders = ",".join("?" for _ in kinds)
                clauses.append(f"kind IN ({placeholders})")
                shared.extend(kinds)
            extra = ("" if not clauses else " AND " + " AND ".join(clauses))
            select = "SELECT session_id,agent_run_id,seq,ts,kind,entry_json FROM status_timeline"
            # UNION of two indexed lookups instead of an OR that would scan:
            # `identity` may be a mux session id or an agent run id (a history
            # row's key), and UNION dedupes the overlap.
            rows = self._db.execute(
                f"{select} WHERE session_id=?{extra} "
                f"UNION {select} WHERE agent_run_id=?{extra} "
                "ORDER BY ts,agent_run_id,seq LIMIT ?",
                (identity, *shared, identity, *shared, limit + 1),
            ).fetchall()
            truncated = len(rows) > limit
            entries: list[dict[str, Any]] = []
            for row in rows[:limit]:
                try:
                    entry = json.loads(row["entry_json"])
                except ValueError:
                    entry = {"kind": row["kind"], "ts": row["ts"]}
                if not isinstance(entry, dict):
                    entry = {"kind": row["kind"], "ts": row["ts"]}
                entry["session_id"] = row["session_id"]
                entry["agent_run_id"] = row["agent_run_id"]
                entry["seq"] = row["seq"]
                entries.append(entry)
            return entries, truncated

        return await self._run(op)

    async def runs_for_session(self, session_id: str) -> list[str]:
        """Agent run ids this session's timeline spans, oldest first."""

        def op() -> list[str]:
            rows = self._db.execute(
                "SELECT agent_run_id, MIN(ts) AS first_ts FROM status_timeline "
                "WHERE session_id=? GROUP BY agent_run_id ORDER BY first_ts",
                (session_id,),
            ).fetchall()
            return [str(row["agent_run_id"]) for row in rows]

        return await self._run(op)

    async def prune(self, retention_days: int | None = None) -> int:
        """Delete rows older than the retention window. Returns rows removed."""
        days = self.retention_days if retention_days is None else retention_days
        cutoff = time.time() - days * 86400

        def op() -> int:
            removed = self._db.execute(
                "DELETE FROM status_timeline WHERE ts<?", (cutoff,)
            ).rowcount
            self._db.commit()
            return int(removed or 0)

        removed = await self._run(op)
        self.write_stats["rows_pruned"] += removed
        return removed

    def stats(self) -> dict[str, Any]:
        """Write-behind health: volume, batching, and loss counters."""
        return {**self.write_stats, "dirty_sessions": len(self._dirty)}

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True

        def op() -> None:
            with self._operation_lock:
                self._db.close()

        self._executor.submit(op).result()
        self._executor.shutdown(wait=True)
