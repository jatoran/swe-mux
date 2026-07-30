from __future__ import annotations

import asyncio
import json
import time
from collections import deque
from types import MethodType, SimpleNamespace
from typing import Any, cast

import pytest

from swe_mux.adapters import ShellAdapter
from swe_mux.git_projects import ProjectIdentity
from swe_mux.models import SessionRecord
from swe_mux.runtime_cwd import Osc7Parser
from swe_mux.screen_mode import ScreenModeParser
from swe_mux.server import session_startup_metrics
from swe_mux.session import ScrollbackBuffer, Session, SessionManager


def _fake_session(max_bytes: int = 32) -> Any:
    fake = cast(Any, Session.__new__(Session))
    fake.scrollback = ScrollbackBuffer(max_bytes)
    fake.subscribers = set()
    fake.record = cast(Any, type("Record", (), {"snapshot": lambda self: {"state": "running"}})())
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


async def test_natural_exit_releases_conpty_but_retains_scrollback() -> None:
    class EndedPty:
        released = False

        def exit_status(self) -> int:
            return 9

        def release(self) -> None:
            self.released = True

    class History:
        ended: list[tuple[str, str]] = []

        async def session_ended(self, record: SessionRecord, reason: str) -> None:
            self.ended.append((record.id, reason))

    class Events:
        emitted: list[tuple[str, dict[str, Any]]] = []

        async def emit(self, event_type: str, **payload: Any) -> None:
            self.emitted.append((event_type, payload))

    adapter = ShellAdapter()
    history = History()
    events = Events()
    manager = SessionManager(
        {"shell": adapter},
        cast(Any, SimpleNamespace()),
        cast(Any, history),
        cast(Any, events),
        1024,
        "http://127.0.0.1:1",
    )
    record = SessionRecord(
        "ended-session",
        "ended",
        "project",
        "shell",
        "native",
        ".",
        "cmd.exe",
        [],
        state="running",
    )
    pty = EndedPty()
    session = Session(record, cast(Any, pty), adapter, 1024, "secret")
    session.scrollback.append(b"retained output")
    subscriber = session.subscribe()

    await manager._mark_ended(session, "process_exit")

    assert pty.released is True
    assert session.record.state == "crashed"
    assert session.record.exit_code == 9
    assert session.scrollback.bytes() == b"retained output"
    assert history.ended == [(record.id, "process_exit")]
    exit_frame = await subscriber.queue.get()
    assert exit_frame["type"] == "exit"
    assert exit_frame["snapshot"]["state"] == "crashed"


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
    record = SessionRecord("mux", "shell", "default", "shell", "native", ".", "powershell.exe", [])
    updates: list[dict[str, float]] = []
    output: list[bytes] = []
    session = SimpleNamespace(
        pty=SimpleNamespace(output_queue=queue, first_output_at=time.perf_counter()),
        record=record,
        startup_started_at=time.perf_counter() - 0.05,
        stopping=True,
        output_window=deque(),
        osc7=Osc7Parser(),
        screen=ScreenModeParser(),
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


async def test_agent_startup_settles_to_ready_without_a_transcript(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("swe_mux.session.AGENT_STARTUP_QUIET_SECONDS", 0.01)
    record = SessionRecord(
        "mux",
        "codex",
        "default",
        "codex",
        "native",
        ".",
        "codex.exe",
        [],
        state="starting",
    )
    record.last_activity_ts = time.time()
    updates: list[str] = []
    session = SimpleNamespace(
        record=record,
        state_source_priority=-1,
        pty=SimpleNamespace(isalive=lambda: True),
        stopping=False,
        agent_ready_task=None,
        tasks=set(),
        revision=0,
        subscribers=set(),
        publish_update=lambda: updates.append(record.state),
    )
    session.transition = MethodType(Session.transition, session)
    emitted: list[tuple[str, dict[str, Any]]] = []

    async def emit(event_type: str, **payload: Any) -> None:
        emitted.append((event_type, payload))

    manager = cast(Any, SessionManager.__new__(SessionManager))
    manager.events = SimpleNamespace(emit=emit)
    SessionManager._queue_agent_ready_check(manager, session)
    assert session.agent_ready_task is not None
    await asyncio.wait_for(session.agent_ready_task, timeout=1)

    assert record.state == "idle"
    assert updates == ["idle"]
    assert emitted == [
        (
            "state_changed",
            {
                "session_id": "mux",
                "source": "pty",
                "previous": "starting",
                "state": "idle",
                "detail": None,
                "capability": "startup_quiet_fallback",
            },
        )
    ]


async def test_browser_startup_metrics_are_validated_and_persisted_once() -> None:
    record = SessionRecord("mux", "shell", "default", "shell", "native", ".", "powershell.exe", [])
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


async def test_spawn_returns_live_session_before_durable_registration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registration_started = asyncio.Event()
    release_registration = asyncio.Event()
    persisted: list[str] = []

    class BlockingHistory:
        async def register_project_scope(self, _project: ProjectIdentity) -> None:
            registration_started.set()
            await release_registration.wait()
            persisted.append("scope")

        async def session_started(self, record: SessionRecord, _transcript: str | None) -> None:
            persisted.append(record.id)

    class FakePty:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            self.pid = 123
            self.reaper_assignment = "fixture"
            self.output_queue: asyncio.Queue[bytes] = asyncio.Queue()
            self.first_output_at = None

        def prepare(self) -> None:
            return None

        def spawn(self) -> None:
            return None

        def isalive(self) -> bool:
            return True

    adapter = SimpleNamespace(
        name="shell",
        spawn_spec=lambda _native, opts: SimpleNamespace(
            executable=opts.exe or "pwsh.exe", argv=tuple(opts.args), env={}
        ),
        resume_spec=lambda _native, opts: SimpleNamespace(
            executable=opts.exe or "pwsh.exe", argv=tuple(opts.args), env={}
        ),
        graceful_exit_keys=lambda: "exit\r",
        session_env=lambda _sid: {},
        transcript_path=lambda _native, _cwd: None,
    )
    monkeypatch.setattr("swe_mux.session.PtyHost", FakePty)
    manager = SessionManager(
        {"shell": cast(Any, adapter)},
        cast(Any, SimpleNamespace()),
        cast(Any, BlockingHistory()),
        cast(Any, SimpleNamespace(emit=lambda *_args, **_kwargs: asyncio.sleep(0))),
        1024,
        "http://127.0.0.1:1",
    )
    project = ProjectIdentity("scope", "Project", ".", "cwd")

    session = await asyncio.wait_for(
        manager.spawn(backend="shell", name=None, cwd=".", project_id="project", project=project),
        timeout=0.25,
    )
    assert manager.sessions[session.record.id] is session
    assert session.record.pid == 123
    assert "server_ready" in session.record.startup_timing_ms
    await asyncio.wait_for(registration_started.wait(), timeout=0.25)
    assert persisted == []

    release_registration.set()
    assert session.registration_task is not None
    await asyncio.wait_for(session.registration_task, timeout=0.25)
    assert persisted == ["scope", session.record.id]
    assert "durable_registration" in session.record.startup_timing_ms

    for task in tuple(session.tasks):
        task.cancel()
    await asyncio.gather(*session.tasks, return_exceptions=True)
