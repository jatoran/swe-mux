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
            resize=lambda cols, rows: None,
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
        assert await ws.receive_json() == {
            "type": "replay_start",
            "reason": "attach",
            "allow_terminal_responses": True,
        }
        replay = await ws.receive()
        assert replay.type == WSMsgType.BINARY
        assert replay.data == b"past"
        assert await ws.receive_json() == {"type": "replay_end", "reason": "attach"}
        await ws.send_json({"type": "claim_input"})
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
        second = await client.ws_connect("/pty/mux-id")
        for socket in (first, second):
            await socket.receive_json()
            await socket.receive_json()
            await socket.receive_json()
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
        await asyncio.wait_for(sink_started.wait(), timeout=0.25)
        assert (await ws.receive_json())["type"] == "replay_start"
        assert (await ws.receive()).data == b"ready"
        assert (await ws.receive_json())["type"] == "replay_end"
        release_sink.set()
        await ws.close()


async def test_server_broadcast_targets_each_included_live_session_once() -> None:
    writes: dict[str, list[str]] = {"source": [], "included": [], "dead": [], "other": []}

    def fake_session(identity: str, *, included: bool, alive: bool = True) -> Any:
        return SimpleNamespace(
            record=SimpleNamespace(id=identity, broadcast=included),
            pty=SimpleNamespace(isalive=lambda: alive, write=writes[identity].append),
        )

    sessions = {
        "source": fake_session("source", included=True),
        "included": fake_session("included", included=True),
        "dead": fake_session("dead", included=True, alive=False),
        "other": fake_session("other", included=False),
    }
    events = EventBus()
    result = await deliver_broadcast(
        cast(Any, SimpleNamespace(sessions=sessions)), "hello", events, source_id="source"
    )

    assert result == {"delivered": ["included"], "skipped": ["dead"]}
    assert writes == {"source": [], "included": ["hello"], "dead": [], "other": []}
