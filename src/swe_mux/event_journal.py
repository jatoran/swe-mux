"""Event persistence that never makes an emitter wait.

`EventBus.emit` used to await its own `INSERT` into `mux.db` before any subscriber saw
the event, and the hook ingress emits two or three events per hook, so every hook
reply - including a `PermissionRequest` decision, which the CLI waits 3 s for and
then asks the user - queued behind inserts on the history store's one worker and
the `mux.db` operation lock it shares with every other store
(`DAEMON_LOAD_ISOLATION.md` item 10). At 36 sessions that worker took 105 single-row
transactions a second and queued up to 35 deep.

Here a sequence number is assigned in memory, on the loop, in emit order, and the
event goes to its subscribers at once; one writer task persists whatever has
accumulated in a single transaction, in order. Nothing reads the `events` table
without first waiting for everything assigned before the read (`flush`), so a reader
never sees a hole a pending event will fill.

A block of numbers is skipped at every start: the next number is `JOURNAL_LIMIT` past
the highest ever written, because a daemon that dies with events assigned and not
yet written has handed those numbers to subscribers, and a browser resuming from one
of them must not be told about a different event under it. Gaps in the sequence are
already normal (retention, deduplicated emits) and every reader tolerates them.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import time
from collections import deque
from collections.abc import Callable
from concurrent.futures import Executor
from typing import Any

from .models import MuxEvent

log = logging.getLogger(__name__)

#: Events assigned and not yet written, at most; past it the oldest are dropped and
#: counted. Also the gap a start leaves after the highest number written.
JOURNAL_LIMIT = 10_000
#: Events written per transaction.
JOURNAL_BATCH = 500
#: How long a failed write waits before it is retried, doubling to the cap.
RETRY_SECONDS = 0.5
RETRY_CAP_SECONDS = 10.0
#: Retention, as `append_event` applied it: 90 days, and the newest 100,000 numbers.
RETENTION_SECONDS = 90 * 86400
RETENTION_EVENTS = 100_000

#: Runs one write on the history store's worker: (operation) -> result.
Submit = Callable[[Callable[[], Any]], Any]


def first_sequence(db: sqlite3.Connection) -> int:
    """The number the journal starts at for this database (worker thread)."""
    row = db.execute("SELECT seq FROM sqlite_sequence WHERE name='events'").fetchone()
    highest = int(row[0]) if row is not None and row[0] is not None else 0
    written = db.execute("SELECT MAX(seq) FROM events").fetchone()
    highest = max(highest, int(written[0] or 0) if written is not None else 0)
    return highest + 1 + (JOURNAL_LIMIT if highest else 0)


def _payload_json(event: MuxEvent) -> str:
    try:
        return json.dumps(event.payload)
    except (TypeError, ValueError):
        # One unserializable field must not hold back every event behind it.
        return json.dumps(event.payload, default=repr)


def write_events(db: sqlite3.Connection, batch: list[MuxEvent]) -> None:
    """Insert ``batch`` in order and apply retention, in one transaction (worker thread)."""
    rows = [
        (event.seq, event.ts, event.session_id, event.source, event.type, _payload_json(event))
        for event in batch
    ]
    try:
        db.executemany(
            "INSERT INTO events(seq,ts,session_id,source,type,payload_json) VALUES(?,?,?,?,?,?)",
            rows,
        )
    except sqlite3.IntegrityError:
        # Another writer took one of these numbers: a second process on the same
        # database. The events are kept under numbers SQLite assigns; only their
        # positions in the sequence change, which is logged.
        db.rollback()
        log.warning(
            "event journal numbers %d-%d collided with another writer; renumbering",
            batch[0].seq,
            batch[-1].seq,
        )
        db.executemany(
            "INSERT INTO events(ts,session_id,source,type,payload_json) VALUES(?,?,?,?,?)",
            [row[1:] for row in rows],
        )
    first, last = batch[0].seq, batch[-1].seq
    if last // 100 > (first - 1) // 100:
        db.execute(
            "DELETE FROM events WHERE ts<? OR seq<=?",
            (batch[-1].ts - RETENTION_SECONDS, max(0, last - RETENTION_EVENTS)),
        )
    db.commit()


class EventJournal:
    """Assigns event numbers on the loop and writes the events behind it, in order."""

    def __init__(self, first_seq: int, write: Callable[[list[MuxEvent]], Any]) -> None:
        """``write`` persists one batch and is run on ``executor`` by the writer."""
        self._next = first_seq
        self._written = first_seq - 1
        self._write = write
        self._pending: deque[MuxEvent] = deque()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._wake: asyncio.Event | None = None
        self._progress: asyncio.Condition | None = None
        self._task: asyncio.Task[None] | None = None
        self._executor: Executor | None = None
        self._closed = False
        # The last number of the batch on the worker right now, 0 when none: `close`
        # runs its own write behind it on the same single-threaded executor, so those
        # events must not be written twice.
        self._in_flight_through = 0
        self.batches = 0
        self.events_written = 0
        self.dropped = 0
        self.failures = 0
        self.max_pending = 0
        self.last_error: str | None = None

    def bind(self, executor: Executor) -> None:
        self._executor = executor

    # -- the emit path (loop thread) -------------------------------------------

    def assign(self, event: MuxEvent) -> int:
        """Number ``event`` and queue it for writing. The `EventBus` sink."""
        seq = self._next
        self._next += 1
        event.seq = seq
        if self._closed:
            return seq
        self._pending.append(event)
        if len(self._pending) > JOURNAL_LIMIT:
            self._pending.popleft()
            self.dropped += 1
            if self.dropped == 1 or self.dropped % 1000 == 0:
                log.warning("event journal full; dropped %d unwritten events", self.dropped)
        self.max_pending = max(self.max_pending, len(self._pending))
        self._ensure_writer()
        assert self._wake is not None
        self._wake.set()
        return seq

    async def flush(self) -> None:
        """Wait until every event numbered before this call is written."""
        target = self._next - 1
        if self._written >= target or not self._pending:
            return
        self._ensure_writer()
        assert self._wake is not None and self._progress is not None
        self._wake.set()
        async with self._progress:
            await self._progress.wait_for(
                lambda: self._written >= target or not self._pending or self._closed
            )

    async def append(self, event: MuxEvent) -> int:
        """Number, queue and wait for ``event``: the old awaited sink, for callers that
        need the row written before they go on."""
        seq = self.assign(event)
        await self.flush()
        return seq

    def _ensure_writer(self) -> None:
        loop = asyncio.get_running_loop()
        if self._loop is not loop or self._task is None or self._task.done():
            if self._loop is not loop:
                self._wake = asyncio.Event()
                self._progress = asyncio.Condition()
                self._loop = loop
            self._task = loop.create_task(self._run(), name="event-journal")

    async def _run(self) -> None:
        assert self._wake is not None and self._progress is not None
        delay = RETRY_SECONDS
        # unsupervised-loop-ok: the writer is restarted by the next emit if it ever
        # ends, and every failure inside is contained and retried.
        while True:
            await self._wake.wait()
            self._wake.clear()
            while self._pending:
                count = min(JOURNAL_BATCH, len(self._pending))
                batch = [self._pending[index] for index in range(count)]
                self._in_flight_through = batch[-1].seq
                try:
                    # Shielded: a writer cancelled while its batch waits for the worker
                    # (the loop shutting down) must not cancel the write itself, because
                    # `close` counts on that batch landing ahead of its own.
                    assert self._executor is not None
                    await asyncio.shield(
                        asyncio.wrap_future(self._executor.submit(self._write, batch))
                    )
                except Exception as exc:  # noqa: BLE001 - retried; the events stay queued
                    self._in_flight_through = 0
                    self.failures += 1
                    self.last_error = repr(exc)
                    log.exception("event journal write failed; retrying in %.1fs", delay)
                    await asyncio.sleep(delay)
                    delay = min(RETRY_CAP_SECONDS, delay * 2)
                    continue
                delay = RETRY_SECONDS
                self._in_flight_through = 0
                # Only now are they removed: an emit during the write appended behind
                # them, and a drop under pressure took from the front.
                written = batch[-1].seq
                while self._pending and self._pending[0].seq <= written:
                    self._pending.popleft()
                self._written = max(self._written, written)
                self.batches += 1
                self.events_written += len(batch)
                async with self._progress:
                    self._progress.notify_all()

    # -- shutdown (any thread, after the loop stops emitting) ------------------

    def close(self, write_now: Callable[[list[MuxEvent]], Any]) -> None:
        """Write what is still queued with ``write_now`` and stop accepting writes."""
        self._closed = True
        if self._task is not None and not self._task.done() and self._loop is not None:
            if not self._loop.is_closed():
                self._loop.call_soon_threadsafe(self._task.cancel)
        # A batch already on the worker finishes before anything submitted after it.
        in_flight = self._in_flight_through
        while self._pending and self._pending[0].seq <= in_flight:
            self._pending.popleft()
        while self._pending:
            batch = [self._pending.popleft() for _ in range(min(JOURNAL_BATCH, len(self._pending)))]
            try:
                write_now(batch)
            except Exception:  # noqa: BLE001 - closing must finish
                log.exception("event journal could not write %d events at close", len(batch))
                return
            self._written = max(self._written, batch[-1].seq)

    def snapshot(self) -> dict[str, Any]:
        return {
            "next_seq": self._next,
            "written_seq": self._written,
            "pending": len(self._pending),
            "max_pending": self.max_pending,
            "batches": self.batches,
            "events_written": self.events_written,
            "events_per_batch": round(self.events_written / self.batches, 1)
            if self.batches
            else None,
            "dropped": self.dropped,
            "failures": self.failures,
            "last_error": self.last_error,
            "at": time.time(),
        }
