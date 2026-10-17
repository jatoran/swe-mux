"""An emit never waits for SQLite, and the `events` table still says exactly what it did.

Every `EventBus.emit` awaited its own insert on the history store's one worker before
any subscriber saw the event, so each hook reply - a `PermissionRequest` decision
among them - queued behind inserts (`DAEMON_LOAD_ISOLATION.md` item 10). The journal
numbers events in memory and writes them behind, in order, in batches.
"""

from __future__ import annotations

import asyncio
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from swe_mux import event_journal
from swe_mux.event_bus import EventBus
from swe_mux.history import HistoryIndex
from swe_mux.models import MuxEvent


async def test_an_emit_reaches_subscribers_before_its_row_is_written(tmp_path: Path) -> None:
    history = HistoryIndex(tmp_path / "mux.db")
    release = threading.Event()
    try:
        # Hold the history worker, as a long reindex or a busy `mux.db` lock does.
        history._executor.submit(release.wait, 10)
        bus = EventBus(history.journal_event)
        subscriber = bus.subscribe()
        started = time.perf_counter()
        emitted = await bus.emit("PostToolUse", session_id="s1", tool_name="Bash")
        assert time.perf_counter() - started < 0.5, "the emit did not wait for the worker"
        assert (await asyncio.wait_for(subscriber.get(), 1)) is emitted
        assert emitted.seq == 1
        release.set()
        rows = await history.events()
        assert [(row["seq"], row["type"]) for row in rows] == [(1, "PostToolUse")]
    finally:
        release.set()
        history.close()


async def test_events_are_written_in_emit_order_in_few_transactions(tmp_path: Path) -> None:
    history = HistoryIndex(tmp_path / "mux.db")
    try:
        bus = EventBus(history.journal_event)
        for index in range(1200):
            await bus.emit("state_changed", session_id=f"s{index % 7}", index=index)
        rows = await history.events(limit=2000)
        assert [row["payload"]["index"] for row in rows] == list(range(1200))
        assert [row["seq"] for row in rows] == list(range(1, 1201))
        snapshot = history.journal.snapshot()
        assert snapshot["events_written"] == 1200
        assert snapshot["batches"] < 1200 / 2, "inserts were batched, not one per emit"
        assert snapshot["pending"] == 0
    finally:
        history.close()


async def test_every_reader_waits_for_what_was_numbered_before_it(tmp_path: Path) -> None:
    history = HistoryIndex(tmp_path / "mux.db")
    try:
        bus = EventBus(history.journal_event)
        for index in range(5):
            await bus.emit("turn_ended", session_id="s1", index=index)
        recent, truncated = await history.recent_events(limit=10)
        assert [row["payload"]["index"] for row in recent] == list(range(5))
        assert truncated is False
        assert isinstance(await history.workload_telemetry(), dict)
    finally:
        history.close()


def test_closing_writes_what_was_still_queued(tmp_path: Path) -> None:
    path = tmp_path / "mux.db"
    history = HistoryIndex(path)

    async def emit_without_waiting() -> None:
        bus = EventBus(history.journal_event)
        # Stop the writer from running before the loop ends.
        history._executor.submit(time.sleep, 0.3)
        for index in range(50):
            await bus.emit("state_changed", index=index)

    asyncio.run(emit_without_waiting())
    history.close()
    with sqlite3.connect(path) as db:
        count = db.execute("SELECT COUNT(*), MIN(seq), MAX(seq) FROM events").fetchone()
    assert count == (50, 1, 50)


def test_a_restart_never_reissues_a_number_a_dead_daemon_handed_out(tmp_path: Path) -> None:
    """A daemon that dies with events numbered and unwritten has shown those numbers
    to subscribers; the next one starts past every number it could have shown."""
    path = tmp_path / "mux.db"
    history = HistoryIndex(path)

    async def emit_some() -> None:
        bus = EventBus(history.journal_event)
        for index in range(3):
            await bus.emit("state_changed", index=index)
        await history.flush_events()

    asyncio.run(emit_some())
    history.close()
    reopened = HistoryIndex(path)
    try:
        assert reopened.journal.snapshot()["next_seq"] == 3 + 1 + event_journal.JOURNAL_LIMIT
    finally:
        reopened.close()
    fresh = HistoryIndex(tmp_path / "fresh.db")
    try:
        assert fresh.journal.snapshot()["next_seq"] == 1, "a new database starts at one"
    finally:
        fresh.close()


async def test_a_failed_write_is_retried_without_losing_or_reordering(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(event_journal, "RETRY_SECONDS", 0.01)
    history = HistoryIndex(tmp_path / "mux.db")
    real = history.journal._write
    failures = [2]

    def flaky(batch: list[MuxEvent]) -> Any:
        if failures[0]:
            failures[0] -= 1
            raise sqlite3.OperationalError("database is locked")
        return real(batch)

    history.journal._write = flaky
    try:
        bus = EventBus(history.journal_event)
        for index in range(20):
            await bus.emit("state_changed", index=index)
        rows = await asyncio.wait_for(history.events(), 5)
        assert [row["payload"]["index"] for row in rows] == list(range(20))
        assert history.journal.snapshot()["failures"] == 2
    finally:
        history.close()


async def test_an_unserializable_field_does_not_hold_back_the_events_behind_it(
    tmp_path: Path,
) -> None:
    history = HistoryIndex(tmp_path / "mux.db")
    try:
        bus = EventBus(history.journal_event)
        await bus.emit("odd", value=object())
        await bus.emit("plain", value=1)
        rows = await history.events()
        assert [row["type"] for row in rows] == ["odd", "plain"]
        assert rows[0]["payload"]["value"].startswith("<object object")
    finally:
        history.close()


async def test_the_awaited_sink_still_writes_before_returning(tmp_path: Path) -> None:
    history = HistoryIndex(tmp_path / "mux.db")
    try:
        bus = EventBus(history.append_event)
        emitted = await bus.emit("state_changed", index=0)
        with sqlite3.connect(tmp_path / "mux.db") as db:
            assert db.execute("SELECT seq FROM events").fetchall() == [(emitted.seq,)]
    finally:
        history.close()
