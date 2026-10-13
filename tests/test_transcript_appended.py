"""`transcript_appended`: the cue an open Transcript tab reads a delta on, mid-turn.

Before it, the reader refreshed on user messages and turn boundaries only, so a long
agent turn showed nothing new until it ended. The notice is throttled per session and
delivered to browser event sockets alone, because it fires continuously while an
agent works and nothing in the daemon has a use for it.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

from swe_mux import observation
from swe_mux.event_bus import EventBus
from swe_mux.observation import AppendNotices, observe_transcript
from tests.support.settle import drained_until, until
from tests.test_observation import record


class FakeTimers:
    def __init__(self) -> None:
        self.now = 100.0
        self.scheduled: list[tuple[float, Any]] = []

    def later(self, delay: float, callback: Any) -> Any:
        entry = [self.now + delay, callback, False]
        self.scheduled.append((self.now + delay, entry))

        class Handle:
            def cancel(self) -> None:
                entry[2] = True

        return Handle()

    def advance(self, seconds: float) -> None:
        self.now += seconds
        due = [entry for at, entry in self.scheduled if at <= self.now and not entry[2]]
        self.scheduled = [(at, entry) for at, entry in self.scheduled if at > self.now]
        for entry in due:
            entry[2] = True
            entry[1]()


def notices(timers: FakeTimers, fired: list[float]) -> AppendNotices:
    return AppendNotices(
        lambda: fired.append(timers.now),
        interval=2.0,
        clock=lambda: timers.now,
        later=timers.later,  # type: ignore[arg-type]
    )


def test_the_first_growth_is_announced_at_once() -> None:
    timers, fired = FakeTimers(), []
    notices(timers, fired).note()
    assert fired == [100.0]


def test_growth_inside_the_interval_is_announced_once_when_it_closes() -> None:
    timers, fired = FakeTimers(), []
    appended = notices(timers, fired)
    appended.note()
    timers.advance(0.5)
    appended.note()
    timers.advance(0.5)
    appended.note()
    assert fired == [100.0]
    timers.advance(1.0)
    # One trailing notice for both, at the interval's end - not one per growth, and
    # not dropped: the last thing written before a pause must still reach the reader.
    assert fired == [100.0, 102.0]
    timers.advance(10.0)
    assert fired == [100.0, 102.0]


def test_growth_after_a_quiet_interval_is_announced_at_once() -> None:
    timers, fired = FakeTimers(), []
    appended = notices(timers, fired)
    appended.note()
    timers.advance(3.0)
    appended.note()
    assert fired == [100.0, 103.0]


def test_closing_cancels_a_pending_notice() -> None:
    timers, fired = FakeTimers(), []
    appended = notices(timers, fired)
    appended.note()
    timers.advance(0.5)
    appended.note()
    appended.close()
    timers.advance(5.0)
    assert fired == [100.0]


async def test_growth_reaches_browser_sockets_and_no_daemon_consumer(tmp_path: Path) -> None:
    path = tmp_path / "native.jsonl"
    path.write_bytes(b"")
    session = cast(Any, SimpleNamespace(record=record("claude"), publish_update=lambda: None))
    session.record.state = "idle"
    events = EventBus()
    browser = events.subscribe(name=observation.TRANSCRIPT_READER_SUBSCRIBER)
    daemon_consumer = events.subscribe(name="automation")
    stop = asyncio.Event()
    task = asyncio.create_task(observe_transcript(session, path, events, stop))
    try:
        # Attached first: bytes present at attach are history, not growth.
        await until(lambda: session.record.parser_status == "watching")
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"type": "summary", "summary": "grew"}) + "\n")
        seen = await drained_until(browser, "transcript_appended")
    finally:
        stop.set()
        await asyncio.wait_for(task, timeout=5)
    appended = [event for event in seen if event.type == "transcript_appended"]
    assert appended[0].session_id == session.record.id
    assert appended[0].transient is True
    assert not any(
        daemon_consumer.get_nowait().type == "transcript_appended"
        for _ in range(daemon_consumer.qsize())
    )


def test_the_browser_socket_subscribes_under_the_label_the_notice_targets() -> None:
    """The label is a string on both sides; this keeps the two from drifting apart."""
    import inspect

    from swe_mux.routes import pty

    assert f'subscribe(name="{observation.TRANSCRIPT_READER_SUBSCRIBER}")' in inspect.getsource(
        pty.events_ws
    )
