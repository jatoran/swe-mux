from __future__ import annotations

import asyncio
import json
import time
from collections import deque
from types import SimpleNamespace
from typing import Any, cast

from swe_mux.models import SessionRecord
from swe_mux.runtime_cwd import Osc7Parser
from swe_mux.server import session_startup_metrics
from swe_mux.session import ScrollbackBuffer, Session, SessionManager


def _fake_session(max_bytes: int = 32) -> Any:
    fake = cast(Any, Session.__new__(Session))
    fake.scrollback = ScrollbackBuffer(max_bytes)
    fake.subscribers = set()
    fake.record = cast(
        Any, type("Record", (), {"snapshot": lambda self: {"state": "running"}})()
    )
    fake.revision = 0
    return fake


def test_scrollback_retains_exact_tail_across_chunk_boundaries() -> None:
    buffer = ScrollbackBuffer(5)
    buffer.append(b"abc")
    buffer.append(b"defg")
    assert buffer.bytes() == b"cdefg"


def test_scrollback_retains_tail_of_oversized_chunk() -> None:
    buffer = ScrollbackBuffer(4)
    buffer.append(b"old")
    buffer.append(b"012345")
    assert buffer.bytes() == b"2345"


def test_scrollback_zero_capacity_retains_nothing() -> None:
    buffer = ScrollbackBuffer(0)
    buffer.append(b"ignored")
    assert buffer.bytes() == b""


def test_scrollback_cursor_excludes_retained_output_and_tracks_new_tail() -> None:
    buffer = ScrollbackBuffer(8)
    buffer.append(b"old")
    position = buffer.position

    assert buffer.bytes_since(position) == b""

    buffer.append(b"-new-data")
    assert buffer.bytes() == b"new-data"
    assert buffer.bytes_since(position) == b"new-data"


def test_replay_subscription_boundary_is_atomic() -> None:
    # The method is intentionally synchronous: no fanout task can run between its
    # scrollback snapshot and subscriber registration on the event loop.
    fake = _fake_session(10)
    fake.scrollback.append(b"past")

    snapshot, revision, replay, queue = Session.replay_and_subscribe(fake)
    assert snapshot == {"state": "running"}
    assert revision == 0
    assert replay == b"past"
    assert queue in fake.subscribers


def test_subscriber_overflow_coalesces_into_one_resync() -> None:
    fake = _fake_session(6)
    subscriber = Session.subscribe(fake, maxsize=2)
    for chunk in (b"12", b"34", b"56"):
        fake.scrollback.append(chunk)
        Session.publish_output(fake, chunk)

    assert subscriber.queue.qsize() == 1
    assert subscriber.queue.get_nowait() == {"type": "resync"}
    assert subscriber.dropped_bytes == 6
    assert subscriber.dropped_chunks == 3

    fake.scrollback.append(b"78")
    Session.publish_output(fake, b"78")
    result = Session.take_resync(fake, subscriber)
    dropped_bytes, dropped_chunks, replay, snapshot, revision, exit_frame = result
    assert (dropped_bytes, dropped_chunks) == (8, 4)
    assert replay == b"345678"
    assert snapshot == {"state": "running"}
    assert revision == 0
    assert exit_frame is None


def test_exit_survives_subscriber_overflow() -> None:
    fake = _fake_session()
    subscriber = Session.subscribe(fake, maxsize=1)
    fake.scrollback.append(b"one")
    Session.publish_output(fake, b"one")
    fake.scrollback.append(b"two")
    Session.publish_output(fake, b"two")
    Session.publish_exit(fake, "process_exit")

    assert subscriber.queue.get_nowait() == {"type": "resync"}
    *_, exit_frame = Session.take_resync(fake, subscriber)
    assert exit_frame is not None
    assert exit_frame["type"] == "exit"
    assert exit_frame["reason"] == "process_exit"


def test_hook_state_blocks_lower_priority_transcript_regression() -> None:
    fake = _fake_session()
    fake.record = SessionRecord(
        "mux", "agent", "default", "claude", "native", ".", "claude.exe", []
    )
    fake.state_source_priority = -1

    assert Session.transition(fake, "awaiting", "permission", source="hook")
    assert not Session.transition(fake, "working", None, source="transcript")
    assert fake.record.state == "awaiting"
    assert fake.record.state_detail == "permission"
    assert Session.transition(fake, "idle", None, source="hook")
    assert fake.record.state == "idle"


async def test_fanout_records_first_output_and_prompt_startup_milestones() -> None:
    queue: asyncio.Queue[bytes] = asyncio.Queue()
    await queue.put(b"profile output\r\n\x1b]7;file:///D:/PROJECTS/swe-mux\x07")
    await queue.put(b"")
    record = SessionRecord(
        "mux", "shell", "default", "shell", "native", ".", "powershell.exe", []
    )
    updates: list[dict[str, float]] = []
    output: list[bytes] = []
    session = SimpleNamespace(
        pty=SimpleNamespace(output_queue=queue, first_output_at=time.perf_counter()),
        record=record,
        startup_started_at=time.perf_counter() - 0.05,
        stopping=True,
        output_window=deque(),
        osc7=Osc7Parser(),
        scrollback=ScrollbackBuffer(1024),
        publish_output=output.append,
        publish_update=lambda: updates.append(dict(record.startup_timing_ms)),
        startup_measurement_task=None,
        tasks=set(),
    )
    manager = cast(Any, SessionManager.__new__(SessionManager))
    prompt_uris: list[str] = []
    events: list[tuple[str, dict[str, Any]]] = []

    async def emit(event_type: str, **payload: Any) -> None:
        events.append((event_type, payload))

    manager.events = SimpleNamespace(emit=emit)
    manager._queue_runtime_cwd = lambda _session, uri: prompt_uris.append(uri)

    await SessionManager._fanout(manager, session)
    await session.startup_measurement_task

    assert record.startup_timing_ms["first_output"] >= 40
    assert record.startup_timing_ms["first_prompt"] >= record.startup_timing_ms["first_output"]
    assert record.snapshot()["startup_timing_ms"] == record.startup_timing_ms
    assert prompt_uris == ["file:///D:/PROJECTS/swe-mux"]
    assert output == [b"profile output\r\n\x1b]7;file:///D:/PROJECTS/swe-mux\x07"]
    assert len(updates) == 1
    assert events[0][0] == "session_startup_measured"
    assert events[0][1]["milestone"] == "first_prompt"
    assert events[0][1]["timing_ms"] == record.startup_timing_ms


async def test_browser_startup_metrics_are_validated_and_persisted_once() -> None:
    record = SessionRecord(
        "mux", "shell", "default", "shell", "native", ".", "powershell.exe", []
    )
    updates: list[dict[str, float]] = []
    emitted: list[tuple[str, dict[str, Any]]] = []
    session = SimpleNamespace(
        record=record,
        publish_update=lambda: updates.append(dict(record.client_startup_timing_ms)),
    )

    async def emit(event_type: str, **payload: Any) -> None:
        emitted.append((event_type, payload))

    class Request:
        app = {
            "sessions": SimpleNamespace(resolve=lambda _sid: session),
            "events": SimpleNamespace(emit=emit),
        }
        match_info = {"sid": "mux"}

        async def json(self) -> dict[str, Any]:
            return {
                "timing_ms": {
                    "api_response": 40.04,
                    "pane_mounted": 51.15,
                    "socket_open": 61.26,
                    "replay_ready": 72.37,
                }
            }

    response = await session_startup_metrics(cast(Any, Request()))
    await session_startup_metrics(cast(Any, Request()))
    payload = json.loads(response.text)

    assert payload["timing_ms"]["replay_ready"] == 72.4
    assert record.client_startup_timing_ms["api_response"] == 40.0
    assert len(updates) == 1
    assert len(emitted) == 1
    assert emitted[0][0] == "session_startup_client_measured"
