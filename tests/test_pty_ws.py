from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, cast

import pytest
from aiohttp import WSMsgType, web
from aiohttp.test_utils import TestClient, TestServer

from swe_mux.event_bus import EventBus
from swe_mux.models import SessionRecord
from swe_mux.server import deliver_broadcast, pty_ws
from swe_mux.session import Session


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
    app["sessions"] = manager
    app["events"] = EventBus()
    app.router.add_get("/pty/{sid}", pty_ws)

    async with TestClient(TestServer(app)) as client:
        ws = await client.ws_connect("/pty/mux-id")
        state = await ws.receive_json()
        assert state["type"] == "state"
        assert state["revision"] == 0
        # Messages that race readiness are held until replay finishes, while the
        # fitted dimensions reach the PTY before any replay bytes are sent.
        await ws.send_json({"type": "claim_input"})
        await ws.send_json(
            {"type": "attach_ready", "cols": 132, "rows": 41, "renderer": "webgl"}
        )
        assert await ws.receive_json() == {
            "type": "replay_start",
            "reason": "attach",
            "allow_terminal_responses": True,
        }
        replay = await ws.receive()
        assert replay.type == WSMsgType.BINARY
        assert replay.data == b"past"
        assert await ws.receive_json() == {"type": "replay_end", "reason": "attach"}
        assert resizes == [(132, 41)]
        assert await ws.receive_json() == {"type": "input_owner", "active": True}
        await ws.send_json({"type": "terminal_state", "mode": "normal"})
        await ws.send_json(
            {"type": "input", "kind": "terminal_response", "data": "\x1b[?1;2c"}
        )
        await asyncio.sleep(0.01)
        assert writes == ["\x1b[?1;2c"]
        assert session.terminal_mode == "normal"
        assert session.input_revision == 0

        await ws.send_json({"type": "input", "data": "\x1b[200~fixture\x1b[201~"})
        await asyncio.sleep(0.01)
        assert session.input_revision == 1
        assert writes[-1] == "\x1b[200~fixture\x1b[201~"

        session.publish_output(b"live")
        live = await ws.receive()
        assert live.type == WSMsgType.BINARY
        assert live.data == b"live"

        assert session.transition("working", "tool", source="hook")
        update = await ws.receive_json()
        assert update["type"] == "update"
        assert update["revision"] == 1
        assert update["snapshot"]["state"] == "working"
        assert update["snapshot"]["state_detail"] == "tool"

        session.record.state = "exited"
        session.publish_exit("complete")
        exit_frame = await ws.receive_json()
        assert exit_frame["type"] == "exit"
        assert exit_frame["revision"] == 2
        assert exit_frame["reason"] == "complete"
        await ws.close()

    assert session.input_owner is None


@pytest.mark.filterwarnings("ignore:It is recommended to use web.AppKey instances for keys")
async def test_only_latest_claiming_browser_can_write_input() -> None:
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
    app["sessions"] = manager
    app["events"] = EventBus()
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
        await first.receive_json()
        await first.send_json({"type": "input", "data": "one"})
        await second.send_json({"type": "claim_input"})
        await second.receive_json()
        await second.send_json({"type": "input", "data": "two"})
        await first.send_json({"type": "input", "data": "stale"})
        await asyncio.sleep(0.05)
        await first.close()
        await second.close()

    assert writes == ["one", "two"]


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
    app["sessions"] = manager
    app["events"] = EventBus()
    app.router.add_get("/pty/{sid}", pty_ws)

    foreground = "\x1b]10;rgb:c0c0/caca/f5f5\x1b\\"
    background = "\x1b]11;rgb:1a1a/1b1b/2626\x1b\\"
    async with TestClient(TestServer(app)) as client:
        ws = await client.ws_connect("/pty/mux-id")
        assert (await ws.receive_json())["type"] == "state"
        await ws.send_json({"type": "attach_ready", "cols": 80, "rows": 24})
        assert (await ws.receive_json())["type"] == "replay_start"
        assert (await ws.receive_json())["type"] == "replay_end"
        await ws.send_json({"type": "claim_input"})
        assert await ws.receive_json() == {"type": "input_owner", "active": True}

        await ws.send_json(
            {"type": "input", "kind": "terminal_response", "data": foreground}
        )
        # Older cached browser builds mislabeled OSC color replies as user input.
        await ws.send_json({"type": "input", "kind": "user", "data": foreground + background})
        await ws.send_json({"type": "input", "kind": "user", "data": "hello"})
        await asyncio.sleep(0.01)
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
    app["sessions"] = SimpleNamespace(
        resolve=lambda _identity: session,
        sessions={record.id: session},
    )
    app["events"] = EventBus(blocked_sink)
    app.router.add_get("/pty/{sid}", pty_ws)

    async with TestClient(TestServer(app)) as client:
        ws = await client.ws_connect("/pty/mux-id")
        state = await asyncio.wait_for(ws.receive_json(), timeout=0.25)
        assert state["type"] == "state"
        # The legacy resize frame also releases replay for older browser builds.
        await ws.send_json({"type": "resize", "cols": 80, "rows": 24})
        await asyncio.wait_for(sink_started.wait(), timeout=0.25)
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
    app["sessions"] = SimpleNamespace(
        resolve=lambda _identity: session,
        sessions={record.id: session},
    )
    app["events"] = EventBus()
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
                "sessions": SimpleNamespace(resolve=lambda _sid: session),
                "events": SimpleNamespace(
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
    app["sessions"] = manager
    app["events"] = EventBus()

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
        assert (await ws.receive_json())["type"] == "state"
        await ws.send_json({"type": "attach_ready", "cols": 80, "rows": 24})
        await asyncio.sleep(0.05)
        await ws.close()

    await asyncio.sleep(0.05)
    assert session.subscribers == set()


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
    app["sessions"] = manager
    app["events"] = EventBus()
    app.router.add_get("/pty/{sid}", pty_ws)

    async with TestClient(TestServer(app)) as client:
        ws = await client.ws_connect("/pty/mux-id")
        assert (await ws.receive_json())["type"] == "state"
        await ws.send_json({"type": "attach_ready", "cols": 80, "rows": 24})
        assert (await ws.receive_json())["type"] == "replay_start"
        assert (await ws.receive_json())["type"] == "replay_end"
        assert (await ws.receive_json())["reason"] == "already_ended"
        await ws.close()

    await asyncio.sleep(0.05)
    assert session.subscribers == set()
