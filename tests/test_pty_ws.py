from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace
from typing import Any, cast

import pytest
from aiohttp import WSMsgType, web
from aiohttp.test_utils import TestClient, TestServer

from swe_mux import app_keys as keys
from swe_mux import server
from swe_mux.device_presence import DevicePresenceStore, DeviceReport
from swe_mux.event_bus import EventBus
from swe_mux.models import SessionRecord
from swe_mux.server import PtyOutputFlow, deliver_broadcast, pty_ws
from swe_mux.session import Session
from tests.support.settle import until


async def test_pty_output_flow_waits_for_xterm_parse_credit() -> None:
    flow = PtyOutputFlow(high_water_bytes=8)
    flow.enable()
    flow.sent(8)

    waiter = asyncio.create_task(flow.wait_for_credit())
    await asyncio.sleep(0)
    assert not waiter.done()

    flow.acknowledge(3)
    await asyncio.wait_for(waiter, timeout=0.1)
    assert flow.unacknowledged_bytes == 5


async def test_pty_output_flow_is_compatible_with_clients_without_ack_support() -> None:
    flow = PtyOutputFlow(high_water_bytes=1)
    flow.sent(1024)
    await asyncio.wait_for(flow.wait_for_credit(), timeout=0.1)
    assert flow.unacknowledged_bytes == 0


async def test_pty_sender_batches_output_already_waiting_in_the_queue() -> None:
    queue: asyncio.Queue[Any] = asyncio.Queue()
    queue.put_nowait(b"one")
    queue.put_nowait(b"two")
    queue.put_nowait(b"three")
    sent: list[bytes] = []

    async def send_bytes_classified(payload: bytes, kind: str) -> None:
        assert kind == "live_output"
        sent.append(payload)

    task = asyncio.create_task(
        server._pty_sender(
            cast(Any, SimpleNamespace(send_bytes_classified=send_bytes_classified)),
            cast(Any, SimpleNamespace()),
            SimpleNamespace(queue=queue),
            "generation",
            PtyOutputFlow(),
        )
    )
    try:
        for _ in range(20):
            if sent:
                break
            await asyncio.sleep(0)
        assert sent == [b"onetwothree"]
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def next_json(ws: Any, skip: tuple[str, ...] = ("geometry",)) -> Any:
    """Next JSON frame, ignoring the shared-geometry frames.

    Geometry is arbitrated across every attached client, so a `geometry` frame can
    arrive at any point once one attaches, resizes, or takes input over. Tests that
    are not about geometry step over it rather than pinning an incidental ordering.
    """
    while True:
        frame = await ws.receive_json()
        if frame.get("type") not in skip:
            return frame


async def next_bytes(ws: Any) -> bytes:
    """Next binary (terminal output) frame, stepping over geometry frames."""
    while True:
        message = await ws.receive()
        if message.type == WSMsgType.TEXT and json.loads(message.data).get("type") == "geometry":
            continue
        assert message.type == WSMsgType.BINARY
        return cast(bytes, message.data)


@pytest.mark.filterwarnings("ignore:It is recommended to use web.AppKey instances for keys")
async def test_pty_ws_orders_replay_then_live_updates_and_exit() -> None:
    writes: list[str] = []
    resizes: list[tuple[int, int]] = []
    record = SessionRecord(
        "mux-id",
        "agent",
        "default",
        "claude",
        "native-id",
        ".",
        "claude.exe",
        [],
        state="starting",
    )
    pty = cast(
        Any,
        SimpleNamespace(
            write=writes.append,
            resize=lambda cols, rows: resizes.append((cols, rows)),
            isalive=lambda: True,
        ),
    )
    adapter = cast(Any, SimpleNamespace())
    session = Session(record, pty, adapter, 32, "secret")
    session.scrollback.append(b"past")

    manager = SimpleNamespace(resolve=lambda identity: session, sessions={record.id: session})
    app = web.Application()
    app[keys.SESSIONS] = manager
    app[keys.EVENTS] = EventBus()
    events = app[keys.EVENTS].subscribe(name="input-diagnostic-test")
    app.router.add_get("/pty/{sid}", pty_ws)

    async with TestClient(TestServer(app)) as client:
        ws = await client.ws_connect("/pty/mux-id")
        # The real client sends `attach_ready` in `onopen`, before any frame arrives;
        # the daemon answers the completed handshake with state, then the replay.
        # Messages that race readiness are held until replay finishes, while the
        # fitted dimensions reach the PTY before any replay bytes are sent.
        await ws.send_json({"type": "claim_input"})
        await ws.send_json(
            {"type": "attach_ready", "cols": 132, "rows": 41, "renderer": "webgl"}
        )
        state = await ws.receive_json()
        assert state["type"] == "state"
        assert state["revision"] == 0
        assert state["snapshot"]["_snapshot_generation"] == "legacy"
        assert state["snapshot"]["_snapshot_revision"] == 0
        assert state["snapshot"]["_snapshot_enriched"] is False
        assert await ws.receive_json() == {
            "type": "replay_start",
            "reason": "attach",
            "allow_terminal_responses": True,
        }
        replay = await ws.receive()
        assert replay.type == WSMsgType.BINARY
        assert replay.data == b"past"
        replay_end = await ws.receive_json()
        assert replay_end["type"] == "replay_end"
        assert replay_end["reason"] == "attach"
        # The client's byte-cursor anchor: this plus every live byte it receives is
        # what it may offer as `since` on its next attach.
        assert replay_end["position"] == session.scrollback.position
        assert resizes == [(132, 41)]
        assert await next_json(ws) == {
            "type": "input_owner",
            "active": True,
            "epoch": 1,
            "reason": "granted_unowned",
            "owner_device": "unknown",
        }
        await ws.send_json({"type": "terminal_state", "mode": "normal"})
        await ws.send_json(
            {"type": "input", "kind": "terminal_response", "data": "\x1b[?1;2c"}
        )
        await until(lambda: writes == ["\x1b[?1;2c"], what="the terminal response reached the pty")
        assert session.terminal_mode == "normal"
        assert session.input_revision == 0

        await ws.send_json(
            {
                "type": "input",
                "data": "\x1b[200~fixture\x1b[201~",
                "input_seq": 7,
                "client_sent_at_ms": 1_800_000_000_000,
                "client_event_delay_ms": 8123,
                "client_queue_delay_ms": 8000,
                "input_source": "paste",
                "ws_buffered_bytes": 4096,
            }
        )
        ack = await next_json(ws)
        assert ack["type"] == "input_ack"
        assert ack["input_seq"] == 7
        assert isinstance(ack["server_received_at_ms"], int)
        await until(lambda: session.input_revision == 1, what="the paste was counted")
        assert writes[-1] == "\x1b[200~fixture\x1b[201~"
        input_event = await _next_event_of(events, "terminal_input")
        assert input_event.payload["input_seq"] == 7
        assert input_event.payload["client_event_delay_ms"] == 8123
        assert input_event.payload["client_queue_delay_ms"] == 8000
        assert input_event.payload["input_source"] == "paste"
        assert input_event.payload["ws_buffered_bytes"] == 4096
        assert "data" not in input_event.payload

        session.publish_output(b"live")
        assert await next_bytes(ws) == b"live"

        assert session.transition("working", "tool", source="hook")
        update = await next_json(ws)
        assert update["type"] == "update"
        assert update["revision"] == 1
        assert update["snapshot"]["state"] == "working"
        assert update["snapshot"]["state_detail"] == "tool"
        assert update["snapshot"]["_snapshot_revision"] == 1

        session.record.state = "exited"
        session.publish_exit("complete")
        exit_frame = await ws.receive_json()
        assert exit_frame["type"] == "exit"
        assert exit_frame["revision"] == 2
        assert exit_frame["reason"] == "complete"
        await ws.close()

    assert session.input_owner is None


async def _next_event_of(queue: Any, wanted: str) -> Any:
    """Next bus event of the wanted type, stepping over attach/telemetry chatter."""
    while True:
        event = await asyncio.wait_for(queue.get(), timeout=2)
        if event.type == wanted:
            return event


def _repaint_test_session(backend: str, resizes: list[tuple[int, int]]) -> Session:
    record = SessionRecord(
        f"{backend}-id",
        "agent",
        "default",
        backend,
        "native-id",
        ".",
        f"{backend}.exe",
        [],
        state="working",
    )
    pty = cast(
        Any,
        SimpleNamespace(
            write=lambda _data: None,
            resize=lambda cols, rows: resizes.append((cols, rows)),
            isalive=lambda: True,
        ),
    )
    session = Session(record, pty, cast(Any, SimpleNamespace()), 1024, "secret")
    session.scrollback.append(b"bounded replay")
    session.set_viewport("existing", 80, 24, hidden=False)
    assert session.apply_geometry() is True
    resizes.clear()
    return session


def _repaint_test_app(session: Session) -> web.Application:
    app = web.Application()
    app[keys.SESSIONS] = SimpleNamespace(
        resolve=lambda _identity: session,
        sessions={session.record.id: session},
    )
    app[keys.EVENTS] = EventBus()
    app.router.add_get("/pty/{sid}", pty_ws)
    return app


def _outgrow_replay_budget(session: Session, budget: int = 8) -> None:
    """Put this session where every long-lived one ends up: retention past the budget.

    An attach then replays a window rather than everything held, which is the condition
    `Session.replay_window_truncated` reports and the only one that can hand an
    alternate-screen client a partial frame.
    """
    session.attach_replay_bytes = budget
    session.scrollback.append(b"x" * (budget * 4))
    assert session.replay_window_truncated() is True


@pytest.mark.filterwarnings("ignore:It is recommended to use web.AppKey instances for keys")
async def test_a_reconnect_offering_its_position_gets_a_delta_and_no_repaint_pulse() -> None:
    """The tab-switch fix: a reattach that appends instead of starting over.

    A client that survived its disconnect offers the ring position it parsed up to;
    the daemon answers with exactly the missed bytes into an un-reset terminal. No
    repaint pulse fires — the client's screen is exact after the append — where the
    windowed fallback pulses the child on every reattach.
    """
    resizes: list[tuple[int, int]] = []
    session = _repaint_test_session("claude", resizes)
    _outgrow_replay_budget(session)
    app = _repaint_test_app(session)

    async with TestClient(TestServer(app)) as client:
        # First attach: full windowed replay, anchored by its replay_end position.
        ws = await client.ws_connect("/pty/claude-id")
        await ws.send_json({"type": "attach_ready", "cols": 80, "rows": 24})
        assert (await ws.receive_json())["type"] == "state"
        assert (await ws.receive_json())["reason"] == "attach"
        assert await next_bytes(ws) == b"xxxxxxxx"
        first_end = await ws.receive_json()
        assert first_end["position"] == session.scrollback.position
        await ws.close()
        # Let the first attach's own truncated-replay pulse finish before asserting
        # that the delta adds none of its own. Waiting for the pulse rather than for
        # a fixed beat is what keeps a loaded worker from letting it leak past the
        # clear and land on the delta's account.
        await until(lambda: len(resizes) >= 2, what="the first attach pulsed")
        resizes.clear()

        # Output the client misses while its socket is down.
        session.scrollback.append(b"missed")
        session.publish_output(b"missed")

        second = await client.ws_connect("/pty/claude-id")
        await second.send_json(
            {"type": "attach_ready", "cols": 80, "rows": 24, "since": first_end["position"]}
        )
        assert (await second.receive_json())["type"] == "state"
        start = await second.receive_json()
        assert start["reason"] == "delta"
        # Never replay-with-responses: any query in the delta was answered (or
        # missed) once already.
        assert start["allow_terminal_responses"] is False
        assert await next_bytes(second) == b"missed"
        second_end = await second.receive_json()
        assert second_end["reason"] == "delta"
        assert second_end["position"] == session.scrollback.position
        await asyncio.sleep(0.1)
        # No truncated-replay repaint pulse for a delta.
        assert resizes == []
        await second.close()


@pytest.mark.filterwarnings("ignore:It is recommended to use web.AppKey instances for keys")
async def test_a_stale_position_falls_back_to_the_windowed_replay() -> None:
    """A gap the ring no longer covers is answered exactly as before deltas existed."""
    resizes: list[tuple[int, int]] = []
    session = _repaint_test_session("claude", resizes)
    _outgrow_replay_budget(session)
    app = _repaint_test_app(session)

    async with TestClient(TestServer(app)) as client:
        ws = await client.ws_connect("/pty/claude-id")
        await ws.send_json({"type": "attach_ready", "cols": 80, "rows": 24, "since": 1})
        assert (await ws.receive_json())["type"] == "state"
        assert (await ws.receive_json())["reason"] == "attach"
        assert await next_bytes(ws) == b"xxxxxxxx"
        assert (await ws.receive_json())["reason"] == "attach"
        await ws.close()


@pytest.mark.filterwarnings("ignore:It is recommended to use web.AppKey instances for keys")
async def test_windowed_replay_pulses_an_alternate_screen_child_once_per_session() -> None:
    """The fix for a Claude pane whose statusline is missing until the window is resized.

    Its retained bytes are a differential frame stream, so a windowed replay reconstructs
    to whichever cells happened to change inside the window — measured on live panes as a
    screen with no input box border and no prompt. Nothing else repairs it: the
    client-requested path refuses alternate-screen sessions, so the only repaint they had
    was a geometry change the user made by hand.
    """
    resizes: list[tuple[int, int]] = []
    session = _repaint_test_session("claude", resizes)
    _outgrow_replay_budget(session)
    app = _repaint_test_app(session)
    events = app[keys.EVENTS].subscribe(name="test")

    async with TestClient(TestServer(app)) as client:
        ws = await client.ws_connect("/pty/claude-id")
        await ws.send_json({"type": "attach_ready", "cols": 80, "rows": 24})
        assert (await ws.receive_json())["type"] == "state"
        assert (await ws.receive_json())["type"] == "replay_start"
        assert await next_bytes(ws) == b"xxxxxxxx"
        assert (await ws.receive_json())["type"] == "replay_end"

        event = await _next_event_of(events, "terminal_repaint_requested")
        assert event.payload["reason"] == "truncated_replay"
        assert event.payload["applied"] is True
        # One pulse off the arbitrated width and straight back to it.
        assert resizes == [(79, 24), (80, 24)]
        assert session.geometry == (80, 24)

        # A second device arriving on the same session inside the rate window rides the
        # restatement the first one already paid for.
        second = await client.ws_connect("/pty/claude-id")
        await second.send_json({"type": "attach_ready", "cols": 80, "rows": 24})
        assert (await second.receive_json())["type"] == "state"
        await asyncio.sleep(0.1)
        assert resizes == [(79, 24), (80, 24)]
        await second.close()
        await ws.close()


@pytest.mark.filterwarnings("ignore:It is recommended to use web.AppKey instances for keys")
async def test_windowed_replay_pulses_a_hidden_warm_mounted_pane() -> None:
    """The path the retired OMP attach pulse never covered.

    Panes mass-mounted after a Reload UI cold-attach hidden and are never re-attached on
    reveal, so a repair conditioned on visibility would miss exactly the case the user
    hits. A hidden pane still parses into its buffer — only its rendering is paused — so
    the reveal it is heading for finds a whole screen.
    """
    resizes: list[tuple[int, int]] = []
    session = _repaint_test_session("claude", resizes)
    _outgrow_replay_budget(session)
    app = _repaint_test_app(session)

    async with TestClient(TestServer(app)) as client:
        ws = await client.ws_connect("/pty/claude-id")
        await ws.send_json({"type": "attach_ready", "cols": 80, "rows": 24, "hidden": True})
        assert (await ws.receive_json())["type"] == "state"
        assert (await ws.receive_json())["type"] == "replay_start"
        assert await next_bytes(ws) == b"xxxxxxxx"
        assert (await ws.receive_json())["type"] == "replay_end"
        await until(lambda: len(resizes) >= 2, what="the hidden warm mount pulsed")

        # Hidden deregisters the viewport, so the pulse runs on the geometry the
        # remaining clients arbitrated rather than on this pane's own fit.
        assert resizes == [(79, 24), (80, 24)]
        await ws.close()


@pytest.mark.filterwarnings("ignore:It is recommended to use web.AppKey instances for keys")
async def test_complete_replay_never_pulses() -> None:
    """A replay of everything retained is self-contained, whatever the harness draws."""
    resizes: list[tuple[int, int]] = []
    session = _repaint_test_session("claude", resizes)
    assert session.replay_window_truncated() is False
    app = _repaint_test_app(session)

    async with TestClient(TestServer(app)) as client:
        ws = await client.ws_connect("/pty/claude-id")
        await ws.send_json({"type": "attach_ready", "cols": 80, "rows": 24})
        assert (await ws.receive_json())["type"] == "state"
        assert (await ws.receive_json())["type"] == "replay_start"
        assert await next_bytes(ws) == b"bounded replay"
        assert (await ws.receive_json())["type"] == "replay_end"
        await asyncio.sleep(0.1)

        assert resizes == []
        await ws.close()


@pytest.mark.filterwarnings("ignore:It is recommended to use web.AppKey instances for keys")
async def test_windowed_replay_never_pulses_a_normal_screen_harness() -> None:
    """Their window is a shorter history, not a partial frame.

    A normal-screen harness truncates to real scrollback lines, so what survives renders
    correctly. Its own failure — a ring wrapped until the window holds no transcript at
    all — costs a full transcript restatement to repair, which is why it stays behind a
    client request that can prove the replay produced nothing.
    """
    resizes: list[tuple[int, int]] = []
    session = _repaint_test_session("omp", resizes)
    _outgrow_replay_budget(session)
    app = _repaint_test_app(session)

    async with TestClient(TestServer(app)) as client:
        ws = await client.ws_connect("/pty/omp-id")
        await ws.send_json({"type": "attach_ready", "cols": 80, "rows": 24})
        assert (await ws.receive_json())["type"] == "state"
        assert (await ws.receive_json())["type"] == "replay_start"
        assert await next_bytes(ws) == b"xxxxxxxx"
        assert (await ws.receive_json())["type"] == "replay_end"
        await asyncio.sleep(0.1)

        assert resizes == []
        await ws.close()


@pytest.mark.filterwarnings("ignore:It is recommended to use web.AppKey instances for keys")
async def test_client_repaint_request_pulses_one_rate_limited_restatement() -> None:
    resizes: list[tuple[int, int]] = []
    session = _repaint_test_session("omp", resizes)
    app = _repaint_test_app(session)
    events = app[keys.EVENTS].subscribe(name="test")

    async with TestClient(TestServer(app)) as client:
        ws = await client.ws_connect("/pty/omp-id")
        # A hidden warm-mount attach: no viewport registered, and — unlike the
        # retired attach-time pulse — no unconditional restatement either.
        await ws.send_json({"type": "attach_ready", "cols": 80, "rows": 24, "hidden": True})
        assert (await ws.receive_json())["type"] == "state"
        assert (await ws.receive_json())["type"] == "replay_start"
        assert await next_bytes(ws) == b"bounded replay"
        assert (await ws.receive_json())["type"] == "replay_end"
        assert resizes == []

        # The client judged its parsed replay scrollback-free and asks for the pulse.
        await ws.send_json({"type": "repaint"})
        event = await _next_event_of(events, "terminal_repaint_requested")
        assert event.payload["reason"] == "missing_scrollback"
        assert resizes == [(79, 24), (80, 24)]
        assert session.geometry == (80, 24)

        # Immediately asking again is absorbed by the per-session rate limit.
        await ws.send_json({"type": "repaint"})
        await asyncio.sleep(0.1)
        assert resizes == [(79, 24), (80, 24)]
        await ws.close()


@pytest.mark.filterwarnings("ignore:It is recommended to use web.AppKey instances for keys")
async def test_client_repaint_request_ignored_for_alternate_screen_harness() -> None:
    resizes: list[tuple[int, int]] = []
    session = _repaint_test_session("claude", resizes)
    app = _repaint_test_app(session)

    async with TestClient(TestServer(app)) as client:
        ws = await client.ws_connect("/pty/claude-id")
        await ws.send_json({"type": "attach_ready", "cols": 80, "rows": 24, "hidden": True})
        assert (await ws.receive_json())["type"] == "state"
        assert (await ws.receive_json())["type"] == "replay_start"
        assert await next_bytes(ws) == b"bounded replay"
        assert (await ws.receive_json())["type"] == "replay_end"
        await ws.send_json({"type": "repaint"})
        await asyncio.sleep(0.1)
        assert resizes == []
        await ws.close()


@pytest.mark.filterwarnings("ignore:It is recommended to use web.AppKey instances for keys")
async def test_a_settled_drag_pulses_an_alternate_screen_child_exactly_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The fix for a Claude pane that comes back mangled from being dragged wider.

    xterm cannot reflow the alternate buffer and ConPTY only emits what changed in its
    own already-rewrapped copy, so the browser keeps cells from the old wrapping and
    nothing arrives to overwrite them (`needs_resize_repaint`). One pulse after the
    gesture settles is what the user was otherwise doing by hand.
    """
    monkeypatch.setattr(server, "RESIZE_REPAINT_SETTLE_SECONDS", 0.05)
    resizes: list[tuple[int, int]] = []
    session = _repaint_test_session("claude", resizes)
    app = _repaint_test_app(session)
    events = app[keys.EVENTS].subscribe(name="test")

    async with TestClient(TestServer(app)) as client:
        ws = await client.ws_connect("/pty/claude-id")
        await ws.send_json({"type": "attach_ready", "cols": 78, "rows": 24})
        assert (await ws.receive_json())["type"] == "state"
        assert (await ws.receive_json())["type"] == "replay_start"
        assert await next_bytes(ws) == b"bounded replay"
        assert (await ws.receive_json())["type"] == "replay_end"
        # A drag: geometry moves on every frame. Nothing is pulsed while it does,
        # because the screen worth repairing is the one the user stops on.
        for cols in (76, 74, 72, 70):
            await ws.send_json({"type": "resize", "cols": cols, "rows": 24})
        # Every frame of the drag has to have landed before the negative half of this
        # assertion means anything; the settle timer restarts on each one, so waiting
        # for them cannot let the pulse in early.
        await until(lambda: len(resizes) >= 5, what="the drag's own resizes landed")
        # Only the widths the drag itself asked for. A pulse is the off-by-one width
        # (69) and is the one thing that must not have happened yet.
        assert resizes == [(78, 24), (76, 24), (74, 24), (72, 24), (70, 24)]

        event = await _next_event_of(events, "terminal_repaint_requested")
        assert event.payload["reason"] == "resize_settled"
        assert event.payload["applied"] is True
        # Every width the drag passed through, then one pulse off the final width and
        # straight back to it. The session is left at the size it was dragged to.
        assert resizes == [(78, 24), (76, 24), (74, 24), (72, 24), (70, 24), (69, 24), (70, 24)]
        assert session.geometry == (70, 24)

        # A settled session is not pulsed again for as long as it stays settled.
        await asyncio.sleep(0.15)
        assert resizes[-2:] == [(69, 24), (70, 24)]
        await ws.close()


@pytest.mark.filterwarnings("ignore:It is recommended to use web.AppKey instances for keys")
async def test_a_settled_drag_never_pulses_a_normal_screen_harness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Codex and OMP keep repainting their live region, so a gap fills within a frame.

    Pulsing them anyway would spend a full transcript re-render on every drag, which is
    the cost `repaints_scrollback` deliberately rations behind a client request.
    """
    monkeypatch.setattr(server, "RESIZE_REPAINT_SETTLE_SECONDS", 0.05)
    resizes: list[tuple[int, int]] = []
    session = _repaint_test_session("codex", resizes)
    app = _repaint_test_app(session)

    async with TestClient(TestServer(app)) as client:
        ws = await client.ws_connect("/pty/codex-id")
        await ws.send_json({"type": "attach_ready", "cols": 78, "rows": 24})
        assert (await ws.receive_json())["type"] == "state"
        assert (await ws.receive_json())["type"] == "replay_start"
        assert await next_bytes(ws) == b"bounded replay"
        assert (await ws.receive_json())["type"] == "replay_end"
        await ws.send_json({"type": "resize", "cols": 70, "rows": 24})
        await asyncio.sleep(0.15)

        assert resizes == [(78, 24), (70, 24)]
        assert session.resize_repaint_task is None
        await ws.close()


@pytest.mark.filterwarnings("ignore:It is recommended to use web.AppKey instances for keys")
async def test_client_diagnostic_persists_allowlisted_repairs_rate_limited() -> None:
    resizes: list[tuple[int, int]] = []
    session = _repaint_test_session("omp", resizes)
    app = _repaint_test_app(session)
    events = app[keys.EVENTS].subscribe(name="test")

    async with TestClient(TestServer(app)) as client:
        ws = await client.ws_connect("/pty/omp-id")
        await ws.send_json({"type": "attach_ready", "cols": 80, "rows": 24, "hidden": True})
        assert (await ws.receive_json())["type"] == "state"
        assert (await ws.receive_json())["type"] == "replay_start"
        assert await next_bytes(ws) == b"bounded replay"
        assert (await ws.receive_json())["type"] == "replay_end"

        await ws.send_json(
            {"type": "client_diagnostic", "phase": "surface_drift_repair", "detail": {"cols": 80}}
        )
        event = await _next_event_of(events, "terminal_client_repair")
        assert event.payload["phase"] == "surface_drift_repair"
        assert json.loads(event.payload["detail"]) == {"cols": 80}

        # A phase outside the allowlist is dropped, and an allowed phase inside the
        # rate window is absorbed; neither reaches the durable log.
        await ws.send_json({"type": "client_diagnostic", "phase": "made_up_phase"})
        await ws.send_json(
            {"type": "client_diagnostic", "phase": "surface_drift_repair"}
        )
        await asyncio.sleep(0.1)
        while not events.empty():
            assert events.get_nowait().type != "terminal_client_repair"

        # Input latency has its own durable event class and per-phase rate window,
        # so a repair report cannot hide the evidence that explains a typing stall.
        await ws.send_json(
            {
                "type": "client_diagnostic",
                "phase": "input_main_thread_stall",
                "detail": {"durationMs": 8500},
            }
        )
        input_event = await _next_event_of(events, "terminal_input_diagnostic")
        assert input_event.payload["phase"] == "input_main_thread_stall"
        assert json.loads(input_event.payload["detail"]) == {"durationMs": 8500}
        assert input_event.payload["input_owner"] is False
        await ws.close()


@pytest.mark.filterwarnings("ignore:It is recommended to use web.AppKey instances for keys")
async def test_paste_trace_persists_as_its_own_event_type_with_its_own_clamp() -> None:
    """The paste trace carries bounded pasted-content evidence by design.

    It must neither join the content-free `terminal_input_diagnostic` class nor be
    truncated by that class's 512-byte clamp, because its two composer snapshots are
    the evidence and a clipped snapshot adjudicates nothing.
    """
    session = _repaint_test_session("omp", [])
    app = _repaint_test_app(session)
    events = app[keys.EVENTS].subscribe(name="test")

    async with TestClient(TestServer(app)) as client:
        ws = await client.ws_connect("/pty/omp-id")
        await ws.send_json({"type": "attach_ready", "cols": 80, "rows": 24, "hidden": True})
        assert (await ws.receive_json())["type"] == "state"
        assert (await ws.receive_json())["type"] == "replay_start"
        assert await next_bytes(ws) == b"bounded replay"
        assert (await ws.receive_json())["type"] == "replay_end"

        detail = {
            "afterMs": 600,
            "payload": {"chars": 600, "head": "h" * 48, "tail": "t" * 48, "flagged": ["3:U+00A0"]},
            "before": {"cursorCol": 10, "rows": ["r" * 120 for _ in range(6)]},
            "after": {"cursorCol": 58, "rows": ["r" * 120 for _ in range(6)]},
        }
        assert len(json.dumps(detail)) > server.CLIENT_DIAGNOSTIC_DETAIL_LIMIT
        await ws.send_json(
            {"type": "client_diagnostic", "phase": "terminal_paste_trace", "detail": detail}
        )
        event = await _next_event_of(events, "terminal_paste_trace")
        assert event.payload["phase"] == "terminal_paste_trace"
        assert json.loads(event.payload["detail"]) == detail
        await ws.close()


async def test_repaint_pulse_never_restores_over_a_concurrent_resize() -> None:
    resizes: list[tuple[int, int]] = []
    session = _repaint_test_session("omp", resizes)

    task = asyncio.create_task(session.repaint_current_geometry())
    # Runs the pulse synchronously to its yield: the off-by-one resize is out.
    await asyncio.sleep(0)
    assert resizes == [(79, 24)]
    # Another client wins arbitration during the yield; its geometry must survive.
    session.geometry = (100, 30)
    assert await task is False
    assert resizes == [(79, 24)]


@pytest.mark.filterwarnings("ignore:It is recommended to use web.AppKey instances for keys")
async def test_only_latest_gesture_claiming_browser_can_write_input() -> None:
    writes: list[str] = []
    record = SessionRecord(
        "mux-id",
        "shell",
        "default",
        "shell",
        "mux-id",
        ".",
        "powershell.exe",
        [],
        state="running",
    )
    pty = cast(
        Any,
        SimpleNamespace(
            write=writes.append,
            resize=lambda cols, rows: None,
            isalive=lambda: True,
        ),
    )
    session = Session(record, pty, cast(Any, SimpleNamespace()), 32, "secret")
    manager = SimpleNamespace(resolve=lambda identity: session, sessions={record.id: session})
    app = web.Application()
    app[keys.SESSIONS] = manager
    app[keys.EVENTS] = EventBus()
    app.router.add_get("/pty/{sid}", pty_ws)

    async with TestClient(TestServer(app)) as client:
        first = await client.ws_connect("/pty/mux-id")
        await first.receive_json()
        await first.send_json({"type": "attach_ready", "cols": 80, "rows": 24})
        await first.receive_json()
        await first.receive_json()
        second = await client.ws_connect("/pty/mux-id")
        await second.receive_json()
        await second.send_json({"type": "attach_ready", "cols": 80, "rows": 24})
        await second.receive_json()
        await second.receive_json()
        await first.send_json({"type": "input", "data": "ignored"})
        await first.send_json({"type": "claim_input"})
        assert (await next_json(first))["type"] == "input_rejected"
        assert (await next_json(first))["active"] is True
        await first.send_json({"type": "input", "data": "one"})
        # A gesture claim is the user's own hand on a device: it always wins, even
        # against an owner that was typed into a moment ago.
        await second.send_json({"type": "claim_input", "reason": "gesture"})
        assert (await next_json(second))["active"] is True
        await second.send_json({"type": "input", "data": "two"})
        await first.send_json({"type": "input", "data": "stale"})
        await asyncio.sleep(0.05)
        # The displaced client is told, and its stale keystrokes come back for replay
        # rather than disappearing.
        displaced = [await next_json(first), await next_json(first)]
        assert [frame["type"] for frame in displaced] == ["input_owner", "input_rejected"]
        assert displaced[0]["reason"] == "claimed_elsewhere"
        assert displaced[1]["data"] == "stale"
        await first.close()
        await second.close()

    assert writes == ["one", "two"]
    assert session.input_rejections == 2


@pytest.mark.filterwarnings("ignore:It is recommended to use web.AppKey instances for keys")
async def test_codex_drops_late_default_color_responses_from_current_and_stale_clients() -> None:
    writes: list[str] = []
    record = SessionRecord(
        "mux-id",
        "codex",
        "default",
        "codex",
        "native-id",
        ".",
        "codex.exe",
        [],
        state="idle",
    )
    pty = cast(
        Any,
        SimpleNamespace(
            write=writes.append,
            resize=lambda cols, rows: None,
            isalive=lambda: True,
        ),
    )
    session = Session(record, pty, cast(Any, SimpleNamespace()), 32, "secret")
    manager = SimpleNamespace(resolve=lambda identity: session, sessions={record.id: session})
    app = web.Application()
    app[keys.SESSIONS] = manager
    app[keys.EVENTS] = EventBus()
    app.router.add_get("/pty/{sid}", pty_ws)

    foreground = "\x1b]10;rgb:c0c0/caca/f5f5\x1b\\"
    background = "\x1b]11;rgb:1a1a/1b1b/2626\x1b\\"
    async with TestClient(TestServer(app)) as client:
        ws = await client.ws_connect("/pty/mux-id")
        await ws.send_json({"type": "attach_ready", "cols": 80, "rows": 24})
        assert (await ws.receive_json())["type"] == "state"
        assert (await ws.receive_json())["type"] == "replay_start"
        assert (await ws.receive_json())["type"] == "replay_end"
        await ws.send_json({"type": "claim_input"})
        assert (await next_json(ws))["active"] is True

        await ws.send_json(
            {"type": "input", "kind": "terminal_response", "data": foreground}
        )
        # Older cached browser builds mislabeled OSC color replies as user input.
        await ws.send_json({"type": "input", "kind": "user", "data": foreground + background})
        await ws.send_json({"type": "input", "kind": "user", "data": "hello"})
        await until(lambda: writes == ["hello"], what="the typed input reached the pty")
        await ws.close()

    assert writes == ["hello"]
    assert session.input_revision == 1


@pytest.mark.filterwarnings("ignore:It is recommended to use web.AppKey instances for keys")
async def test_pty_replay_does_not_wait_for_event_persistence() -> None:
    sink_started = asyncio.Event()
    release_sink = asyncio.Event()

    async def blocked_sink(_event: Any) -> int:
        sink_started.set()
        await release_sink.wait()
        return 1

    record = SessionRecord(
        "mux-id", "shell", "default", "shell", "mux-id", ".", "pwsh.exe", [], state="running"
    )
    pty = cast(
        Any,
        SimpleNamespace(
            write=lambda _data: None,
            resize=lambda _cols, _rows: None,
            isalive=lambda: True,
        ),
    )
    session = Session(record, pty, cast(Any, SimpleNamespace()), 32, "secret")
    session.scrollback.append(b"ready")
    app = web.Application()
    app[keys.SESSIONS] = SimpleNamespace(
        resolve=lambda _identity: session,
        sessions={record.id: session},
    )
    app[keys.EVENTS] = EventBus(blocked_sink)
    app.router.add_get("/pty/{sid}", pty_ws)

    async with TestClient(TestServer(app)) as client:
        ws = await client.ws_connect("/pty/mux-id")
        # The legacy resize frame also releases replay for older browser builds.
        await ws.send_json({"type": "resize", "cols": 80, "rows": 24})
        await asyncio.wait_for(sink_started.wait(), timeout=0.25)
        state = await asyncio.wait_for(ws.receive_json(), timeout=0.25)
        assert state["type"] == "state"
        assert (await ws.receive_json())["type"] == "replay_start"
        assert (await ws.receive()).data == b"ready"
        assert (await ws.receive_json())["type"] == "replay_end"
        release_sink.set()
        await ws.close()


@pytest.mark.filterwarnings("ignore:It is recommended to use web.AppKey instances for keys")
async def test_pty_replay_has_a_bounded_fallback_for_clients_without_readiness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("swe_mux.server.PTY_ATTACH_READY_TIMEOUT_SECONDS", 0.01)
    record = SessionRecord(
        "mux-id", "shell", "default", "shell", "mux-id", ".", "pwsh.exe", [], state="running"
    )
    pty = cast(
        Any,
        SimpleNamespace(
            write=lambda _data: None,
            resize=lambda _cols, _rows: None,
            isalive=lambda: True,
        ),
    )
    session = Session(record, pty, cast(Any, SimpleNamespace()), 32, "secret")
    app = web.Application()
    app[keys.SESSIONS] = SimpleNamespace(
        resolve=lambda _identity: session,
        sessions={record.id: session},
    )
    app[keys.EVENTS] = EventBus()
    app.router.add_get("/pty/{sid}", pty_ws)

    async with TestClient(TestServer(app)) as client:
        ws = await client.ws_connect("/pty/mux-id")
        assert (await ws.receive_json())["type"] == "state"
        assert (await asyncio.wait_for(ws.receive_json(), timeout=0.1))["type"] == "replay_start"
        assert (await ws.receive_json())["type"] == "replay_end"
        await ws.close()


def _broadcast_session(
    writes: dict[str, list[str]],
    identity: str,
    *,
    included: bool,
    alive: bool = True,
    state: str = "running",
) -> Any:
    return SimpleNamespace(
        record=SimpleNamespace(id=identity, broadcast=included, state=state),
        pty=SimpleNamespace(isalive=lambda: alive, write=writes[identity].append),
        input_revision=0,
        last_input_event_ts=0.0,
        last_input_report_ts=0.0,
    )


async def test_server_broadcast_targets_each_included_live_session_once() -> None:
    writes: dict[str, list[str]] = {
        "source": [], "included": [], "dead": [], "ended": [], "other": []
    }
    sessions = {
        "source": _broadcast_session(writes, "source", included=True),
        "included": _broadcast_session(writes, "included", included=True),
        "dead": _broadcast_session(writes, "dead", included=True, alive=False),
        "ended": _broadcast_session(writes, "ended", included=True, state="exited"),
        "other": _broadcast_session(writes, "other", included=False),
    }
    events = EventBus()
    result = await deliver_broadcast(
        cast(Any, SimpleNamespace(sessions=sessions)), "hello", events, source_id="source"
    )

    assert result == {"delivered": ["included"], "skipped": ["dead", "ended"]}
    assert writes == {
        "source": [], "included": ["hello"], "dead": [], "ended": [], "other": []
    }


async def test_broadcast_targets_carry_operator_input_evidence() -> None:
    """Fan-out must account like any other operator input on every target.

    Without the per-target `input_revision`/`last_input_event_ts` advance and
    `terminal_input` emission, delivery-readiness reported `partial_input_absent`
    and `operator_quiet` as satisfied for text the operator just broadcast —
    corrupting the shadow-metric baseline Phase 5 promotion is validated against.
    """
    writes: dict[str, list[str]] = {"source": [], "included": []}
    sessions = {
        "source": _broadcast_session(writes, "source", included=True),
        "included": _broadcast_session(writes, "included", included=True),
    }
    background: list[tuple[str, dict[str, Any]]] = []
    events = cast(
        Any,
        SimpleNamespace(
            emit_background=lambda kind, **payload: background.append((kind, payload))
        ),
    )
    await deliver_broadcast(
        cast(Any, SimpleNamespace(sessions=sessions)), "hi", events, source_id="source"
    )

    target = sessions["included"]
    assert target.input_revision == 1
    assert target.last_input_event_ts > 0
    assert sessions["source"].input_revision == 0
    terminal_inputs = [payload for kind, payload in background if kind == "terminal_input"]
    assert [item["session_id"] for item in terminal_inputs] == ["included"]
    # The writer holds no ownership claim on the target's PTY.
    assert terminal_inputs[0]["input_owner"] is False


async def test_http_session_input_guards_ended_sessions_and_carries_evidence() -> None:
    """`POST /api/sessions/{sid}/input` mirrors the WS/voice evidence contract."""
    from swe_mux.server import session_input

    writes: dict[str, list[str]] = {"live": [], "ended": []}
    live = _broadcast_session(writes, "live", included=False)
    ended = _broadcast_session(writes, "ended", included=False, state="exited")
    background: list[tuple[str, dict[str, Any]]] = []

    def request_for(session: Any, data: str) -> Any:
        class Request:
            match_info = {"sid": session.record.id}
            app = {
                keys.SESSIONS: SimpleNamespace(resolve=lambda _sid: session),
                keys.EVENTS: SimpleNamespace(
                    emit_background=lambda kind, **payload: background.append(
                        (kind, payload)
                    )
                ),
            }

            async def json(self) -> dict[str, str]:
                return {"data": data}

        return cast(Any, Request())

    ok = await session_input(request_for(live, "echo hi\r"))
    assert ok.status == 200
    assert writes["live"] == ["echo hi\r"]
    assert live.input_revision == 1
    assert live.last_input_event_ts > 0
    assert [kind for kind, _ in background] == ["terminal_input"]
    assert background[0][1]["source"] == "http"

    refused = await session_input(request_for(ended, "echo hi\r"))
    assert refused.status == 409
    assert writes["ended"] == []
    assert ended.input_revision == 0

    # Empty payloads write nothing and leave no evidence.
    empty = await session_input(request_for(live, ""))
    assert empty.status == 200
    assert live.input_revision == 1


@pytest.mark.filterwarnings("ignore:It is recommended to use web.AppKey instances for keys")
async def test_pty_ws_unsubscribes_when_the_replay_send_fails() -> None:
    """An exception in the attach/replay window must not orphan the subscriber.

    A mid-replay disconnect (slow mobile link) used to skip the unsubscribe
    entirely, leaving the session permanently reported as attended — which
    suppresses unattended-attention automation and fleet absence reporting for
    that session's whole lifetime.
    """
    record = SessionRecord(
        "mux-id", "shell", "default", "shell", "mux-id", ".", "pwsh.exe", [], state="running"
    )
    pty = cast(
        Any,
        SimpleNamespace(
            write=lambda _data: None,
            resize=lambda _cols, _rows: None,
            isalive=lambda: True,
        ),
    )
    session = Session(record, pty, cast(Any, SimpleNamespace()), 1024, "secret")
    session.scrollback.append(b"scrollback")
    manager = SimpleNamespace(resolve=lambda identity: session, sessions={record.id: session})
    app = web.Application()
    app[keys.SESSIONS] = manager
    app[keys.EVENTS] = EventBus()

    async def failing_pty_ws(request: web.Request) -> web.WebSocketResponse:
        real_prepare = web.WebSocketResponse.prepare

        async def prepare(self: web.WebSocketResponse, req: web.Request) -> Any:
            prepared = await real_prepare(self, req)
            original = self.send_bytes

            async def send_bytes(data: bytes, compress: int | None = None) -> None:
                del data, compress
                raise ConnectionResetError("client vanished mid-replay")

            self.send_bytes = send_bytes  # type: ignore[method-assign]
            del original
            return prepared

        web.WebSocketResponse.prepare = prepare  # type: ignore[method-assign]
        try:
            return await pty_ws(request)
        finally:
            web.WebSocketResponse.prepare = real_prepare  # type: ignore[method-assign]

    app.router.add_get("/pty/{sid}", failing_pty_ws)

    async with TestClient(TestServer(app)) as client:
        ws = await client.ws_connect("/pty/mux-id")
        await ws.send_json({"type": "attach_ready", "cols": 80, "rows": 24})
        assert (await ws.receive_json())["type"] == "state"
        await ws.close()

    await until(lambda: session.subscribers == set(), what="the failed attach unsubscribed")


@pytest.mark.filterwarnings("ignore:It is recommended to use web.AppKey instances for keys")
async def test_pty_ws_unsubscribes_for_an_already_ended_session() -> None:
    record = SessionRecord(
        "mux-id", "shell", "default", "shell", "mux-id", ".", "pwsh.exe", [], state="exited"
    )
    pty = cast(
        Any,
        SimpleNamespace(
            write=lambda _data: None,
            resize=lambda _cols, _rows: None,
            isalive=lambda: False,
        ),
    )
    session = Session(record, pty, cast(Any, SimpleNamespace()), 32, "secret")
    manager = SimpleNamespace(resolve=lambda identity: session, sessions={record.id: session})
    app = web.Application()
    app[keys.SESSIONS] = manager
    app[keys.EVENTS] = EventBus()
    app.router.add_get("/pty/{sid}", pty_ws)

    async with TestClient(TestServer(app)) as client:
        ws = await client.ws_connect("/pty/mux-id")
        await ws.send_json({"type": "attach_ready", "cols": 80, "rows": 24})
        assert (await ws.receive_json())["type"] == "state"
        assert (await ws.receive_json())["type"] == "replay_start"
        assert (await ws.receive_json())["type"] == "replay_end"
        assert (await ws.receive_json())["reason"] == "already_ended"
        await ws.close()

    await until(lambda: session.subscribers == set(), what="the ended session unsubscribed")


def _arbitration_app() -> tuple[Session, web.Application, list[str], list[tuple[int, int]]]:
    """A live session whose PTY records every write and every resize."""
    writes: list[str] = []
    resizes: list[tuple[int, int]] = []
    record = SessionRecord(
        "mux-id", "shell", "default", "shell", "mux-id", ".", "pwsh.exe", [], state="running"
    )
    pty = cast(
        Any,
        SimpleNamespace(
            write=writes.append,
            resize=lambda cols, rows: resizes.append((cols, rows)),
            isalive=lambda: True,
        ),
    )
    session = Session(record, pty, cast(Any, SimpleNamespace()), 32, "secret")
    app = web.Application()
    app[keys.SESSIONS] = SimpleNamespace(
        resolve=lambda _identity: session, sessions={record.id: session}
    )
    app[keys.EVENTS] = EventBus()
    app[keys.DEVICE_PRESENCE] = DevicePresenceStore()
    app.router.add_get("/pty/{sid}", pty_ws)
    return session, app, writes, resizes


def _in_use(app: web.Application, profile: str, interaction_age: float = 1.0) -> None:
    """Report `profile` as the device class the human is using right now."""
    app[keys.DEVICE_PRESENCE].report(
        f"{profile}-events",
        DeviceReport(profile=profile, visible=True, focused=True, interaction_age=interaction_age),
    )


async def _attach(client: TestClient, cols: int, rows: int, *, hidden: bool = False) -> Any:
    ws = await client.ws_connect("/pty/mux-id")
    await ws.send_json({"type": "attach_ready", "cols": cols, "rows": rows, "hidden": hidden})
    assert (await ws.receive_json())["type"] == "state"
    assert (await next_json(ws))["type"] == "replay_start"
    assert (await next_json(ws))["type"] == "replay_end"
    return ws


async def _claim(ws: Any, reason: str, device: str, *, focused: bool = True) -> Any:
    await ws.send_json(
        {"type": "claim_input", "reason": reason, "device": device, "focused": focused}
    )
    return await next_json(ws)


@pytest.mark.filterwarnings("ignore:It is recommended to use web.AppKey instances for keys")
async def test_a_background_pane_cannot_take_input_from_the_device_in_use() -> None:
    """The multi-device failure this arbitration exists for.

    A desktop pane keeps `document.activeElement` inside its terminal while minimized,
    so it re-claimed input every time the phone claimed it. The phone then typed while
    its ownership belief was one round trip stale and the daemon dropped those
    keystrokes silently. Now the passive re-claim is refused, and input that does lose
    the race comes back for replay instead of vanishing.
    """
    session, app, writes, resizes = _arbitration_app()

    async with TestClient(TestServer(app)) as client:
        phone = await _attach(client, 40, 20)
        assert (await _claim(phone, "gesture", "mobile"))["active"] is True
        await phone.send_json({"type": "input", "data": "hi"})
        await asyncio.sleep(0.02)

        desktop = await _attach(client, 200, 50)
        assert await _claim(desktop, "passive", "desktop") == {
            "type": "input_owner",
            "active": False,
            "epoch": 1,
            "reason": "denied_active_owner",
            "owner_device": "mobile",
        }
        # The phone is still the one being typed into, so it still sizes the PTY.
        assert resizes == [(40, 20)]

        await desktop.send_json({"type": "input", "data": "x"})
        rejected = await next_json(desktop)
        assert rejected["type"] == "input_rejected"
        assert rejected["data"] == "x"
        assert rejected["owner_device"] == "mobile"
        assert writes == ["hi"]

        # Taking over is one gesture, and the replayed keystroke lands.
        assert (await _claim(desktop, "gesture", "desktop"))["active"] is True
        await desktop.send_json({"type": "input", "data": "x", "retry": True})
        await until(lambda: writes == ["hi", "x"], what="the replayed keystroke landed")
        assert resizes == [(40, 20), (200, 50)]
        await phone.close()
        await desktop.close()

    assert session.input_rejections == 1
    assert session.input_claim_denials == 1


@pytest.mark.filterwarnings("ignore:It is recommended to use web.AppKey instances for keys")
async def test_a_hidden_client_does_not_size_the_pty() -> None:
    """A minimized window still reports layout; it must not rewrap the agent TUI on
    whatever device the user is actually holding."""
    _session, app, _writes, resizes = _arbitration_app()

    async with TestClient(TestServer(app)) as client:
        desktop = await _attach(client, 200, 50, hidden=True)
        assert resizes == []

        phone = await _attach(client, 40, 20)
        assert resizes == [(40, 20)]

        # Coming back into view registers the viewport, but with nobody owning input
        # the smallest attached client still bounds the shared size.
        await desktop.send_json({"type": "resize", "cols": 200, "rows": 50, "hidden": False})
        await asyncio.sleep(0.02)
        assert resizes == [(40, 20)]

        # Owning it is what hands the size over.
        assert (await _claim(desktop, "gesture", "desktop"))["active"] is True
        await until(lambda: len(resizes) >= 2, what="the owner's size was applied")
        assert resizes == [(40, 20), (200, 50)]
        await phone.close()
        await desktop.close()


@pytest.mark.filterwarnings("ignore:It is recommended to use web.AppKey instances for keys")
async def test_the_owner_detaching_releases_input_and_resizes_for_who_is_left() -> None:
    session, app, _writes, resizes = _arbitration_app()

    async with TestClient(TestServer(app)) as client:
        desktop = await _attach(client, 200, 50)
        phone = await _attach(client, 40, 20)
        assert (await _claim(phone, "gesture", "mobile"))["active"] is True
        assert resizes[-1] == (40, 20)

        await phone.close()
        await until(lambda: session.input_owner is None, what="the owner's detach released input")
        assert resizes[-1] == (200, 50)

        # The pane left behind is told, so it can stop showing "input active on mobile"
        # and go back to rendering at its own size.
        frames: list[Any] = []
        while len(frames) < 6:
            frames.append(await next_json(desktop, ()))
            if frames[-1]["type"] == "input_owner_released":
                break
        assert frames[-1]["type"] == "input_owner_released"
        geometry = [frame for frame in frames if frame["type"] == "geometry"]
        assert (geometry[-1]["cols"], geometry[-1]["rows"]) == (200, 50)
        await desktop.close()


@pytest.mark.filterwarnings("ignore:It is recommended to use web.AppKey instances for keys")
async def test_the_phone_keeps_input_across_sessions_while_it_is_the_device_in_use() -> None:
    """The reported failure: on the phone, every session had to be taken over by hand,
    and pausing on one let the desktop take it straight back.

    Per-session ownership cannot express "the human is on their phone right now": the
    gesture window only protects the one session being typed into, and only for
    seconds. So a desktop pane that was already attached owned every *other* session,
    and its next reconnect reclaimed the one the phone had just been given.
    """
    session, app, writes, _resizes = _arbitration_app()
    _in_use(app, "mobile")

    async with TestClient(TestServer(app)) as client:
        # A desktop pane got there first and holds input, as it would for every
        # session left open in the desktop workspace.
        desktop = await _attach(client, 200, 50)
        assert (await _claim(desktop, "gesture", "desktop"))["active"] is True

        # The phone opens that session. Attaching is passive — no tap on the terminal
        # itself — and that is exactly the case that used to demand "take over".
        phone = await _attach(client, 40, 20)
        granted = await _claim(phone, "passive", "mobile")
        assert granted["active"] is True
        assert granted["reason"] == "granted_device_in_use"
        await phone.send_json({"type": "input", "data": "hi"})
        await until(lambda: writes == ["hi"], what="the phone's keystroke landed")
        # The desktop pane is told it lost the claim.
        assert (await next_json(desktop))["reason"] == "claimed_elsewhere"

        # Now the phone sits idle past its gesture protection while the desktop pane
        # reconnects and re-claims, the way a background pane does on its own.
        session.input_owner_gesture_ts = time.monotonic() - 60
        denied = await _claim(desktop, "passive", "desktop")
        assert denied["active"] is False
        assert denied["reason"] == "denied_device_in_use"
        assert session.input_owner_device == "mobile"
        await phone.close()
        await desktop.close()


@pytest.mark.filterwarnings("ignore:It is recommended to use web.AppKey instances for keys")
async def test_a_desktop_pane_left_open_does_not_take_sessions_back_from_the_phone() -> None:
    """The reported loop, end to end.

    Switching sessions on the phone switches them in the desktop workspace too, so a
    desktop pane mounts and claims for every session opened on the phone. Meanwhile
    the desktop sits open and focused, which keeps it "active" for two minutes after
    the last keystroke — so both device classes are active at once precisely when
    someone picks up their phone. Deciding that as contention handed every session
    back to the incumbent, which is what made the phone demand a takeover each time.
    """
    session, app, _writes, _resizes = _arbitration_app()
    # The desk was in use a minute and a half ago and is still open and focused; the
    # phone was touched a second ago. That is where the human is.
    _in_use(app, "desktop", interaction_age=90)
    _in_use(app, "mobile", interaction_age=1)

    async with TestClient(TestServer(app)) as client:
        desktop = await _attach(client, 200, 50)
        assert (await _claim(desktop, "gesture", "desktop"))["active"] is True

        phone = await _attach(client, 40, 20)
        granted = await _claim(phone, "passive", "mobile")
        assert granted["active"] is True
        assert granted["reason"] == "granted_device_in_use"
        assert (await next_json(desktop))["reason"] == "claimed_elsewhere"

        # The desktop pane the shared tab switch just mounted claims on attach, and
        # keeps claiming as it regains DOM focus. None of it takes the session back.
        denied = await _claim(desktop, "passive", "desktop")
        assert denied["active"] is False
        assert denied["reason"] == "denied_device_in_use"

        # An immediate repeat goes unanswered rather than refused again: the refusal
        # is what an older client re-claims on, which is how one live session logged
        # 7566 refused claims in a loop.
        for _ in range(3):
            await desktop.send_json(
                {"type": "claim_input", "reason": "passive", "device": "desktop", "focused": True}
            )
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(desktop.receive_json(), timeout=0.1)
        assert session.input_owner_device == "mobile"

        # Every decision is recorded with what the daemon knew at the time. Ownership
        # disputes are otherwise visible only as a counter going up, which says a
        # claim was refused but not which device asked or what it reported.
        verdicts = [(entry["device"], entry["verdict"]) for entry in session.claim_log]
        assert ("mobile", "granted_device_in_use") in verdicts
        assert ("desktop", "denied_device_in_use") in verdicts
        assert all(entry["leader"] == "mobile" for entry in session.claim_log)
        await phone.close()
        await desktop.close()


@pytest.mark.filterwarnings("ignore:It is recommended to use web.AppKey instances for keys")
async def test_sitting_down_at_the_other_device_still_takes_input_with_one_click() -> None:
    """The rule above must not strand a device: a real click is still a real click."""
    session, app, _writes, _resizes = _arbitration_app()
    _in_use(app, "mobile")

    async with TestClient(TestServer(app)) as client:
        phone = await _attach(client, 40, 20)
        assert (await _claim(phone, "gesture", "mobile"))["active"] is True
        desktop = await _attach(client, 200, 50)
        assert (await _claim(desktop, "gesture", "desktop"))["active"] is True
        assert session.input_owner_device == "desktop"
        await phone.close()
        await desktop.close()


@pytest.mark.filterwarnings("ignore:It is recommended to use web.AppKey instances for keys")
async def test_mouse_reports_are_not_counted_as_typed_input() -> None:
    """A pointer crossing a pane must not look like a half-typed prompt.

    Once an agent CLI enables mouse tracking, xterm delivers every report on the
    same channel as keystrokes. Counting those advanced `input_revision`, which
    delivery readiness reads as "the operator has started typing into the
    composer since the turn ended" — a session the pointer merely passed over
    then reported `terminal_input_after_completion` until its next turn, forever.
    One live session was accumulating ~170 of these every 20 seconds.
    """
    session, app, writes, _resizes = _arbitration_app()

    async with TestClient(TestServer(app)) as client:
        pane = await _attach(client, 80, 24)
        assert (await _claim(pane, "gesture", "desktop"))["active"] is True

        await pane.send_json({"type": "input", "data": "\x1b[<35;80;10M\x1b[<35;81;10M"})
        # Still delivered: the child asked for these reports and renders from them.
        # Waiting on the write is also what proves the frame was handled before the
        # two "still zero" assertions below are allowed to mean anything.
        await until(
            lambda: writes[-1:] == ["\x1b[<35;80;10M\x1b[<35;81;10M"],
            what="the motion reports reached the pty",
        )
        assert session.input_revision == 0
        assert session.last_input_event_ts == 0.0

        # A click is the human being here, but it puts no text in the composer.
        await pane.send_json({"type": "input", "data": "\x1b[<0;80;10M"})
        await until(lambda: session.last_input_event_ts > 0.0, what="the click was seen")
        assert session.input_revision == 0

        await pane.send_json({"type": "input", "data": "h"})
        await until(lambda: session.input_revision == 1, what="the keystroke was counted")
        await pane.close()


def test_pointer_report_classification() -> None:
    from swe_mux.server import pointer_report_kind

    assert pointer_report_kind("\x1b[<35;10;20M") == "motion"      # SGR motion
    assert pointer_report_kind("\x1b[<32;10;20M") == "motion"      # SGR drag
    assert pointer_report_kind("\x1b[<0;10;20M") == "button"       # SGR press
    assert pointer_report_kind("\x1b[<0;10;20m") == "button"       # SGR release
    assert pointer_report_kind("\x1b[<64;10;20M") == "button"      # wheel up
    assert pointer_report_kind("\x1b[M\x20\x30\x30") == "button"   # X10 press
    assert pointer_report_kind("\x1b[M\x43\x30\x30") == "motion"   # X10 motion (32+3)
    assert pointer_report_kind("\x1b[35;10;20M") == "motion"       # urxvt motion
    assert pointer_report_kind("\x1b[<35;10;20M\x1b[<0;10;20M") == "button"
    # Anything that is not purely mouse reports is input, including a report that
    # arrives glued to a keystroke — the keystroke is what matters.
    assert pointer_report_kind("hello") is None
    assert pointer_report_kind("\x1b[<35;10;20Mx") is None
    assert pointer_report_kind("\x1b[A") is None
    assert pointer_report_kind("") is None
