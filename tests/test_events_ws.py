"""`/events` watermark, bounded recovery, and browser payload contract."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from aiohttp import ClientWebSocketResponse, web
from aiohttp.test_utils import TestClient, TestServer

from swe_mux import app_keys as keys
from swe_mux import server
from swe_mux.device_presence import DevicePresenceStore
from swe_mux.event_bus import EventBus
from swe_mux.history import HistoryIndex
from swe_mux.server import events_ws


def _app(
    history: HistoryIndex,
    events: EventBus,
    presence: DevicePresenceStore | None = None,
    frontend_dir: Path | None = None,
) -> web.Application:
    app = web.Application()
    app[keys.HISTORY] = history
    app[keys.EVENTS] = events
    app[keys.DEVICE_PRESENCE] = presence or DevicePresenceStore()
    app[keys.FRONTEND_DIR] = frontend_dir or Path("__missing_frontend__")
    app[keys.DAEMON_GENERATION] = "test-generation"
    app.router.add_get("/events", events_ws)
    return app


async def _receive_hello(
    ws: ClientWebSocketResponse, *, build_id: str | None = None
) -> None:
    assert await ws.receive_json() == {
        "type": "events_hello",
        "ui_build_id": build_id,
        "daemon_generation": "test-generation",
    }


async def _seed(
    history: HistoryIndex,
    count: int,
    *,
    session_id: str = "s1",
    event_type: str = "state_changed",
) -> None:
    events = EventBus(sink=history.append_event)
    for index in range(count):
        await events.emit(event_type, session_id=session_id, source="daemon", index=index)


@pytest.mark.filterwarnings("ignore:It is recommended to use web.AppKey instances for keys")
async def test_cold_open_sends_only_the_latest_watermark(tmp_path: Path) -> None:
    history = HistoryIndex(tmp_path / "mux.db")
    try:
        await _seed(history, 30)
        frontend = tmp_path / "static"
        frontend.mkdir()
        build_id = "a" * 64
        (frontend / "index.html").write_text(
            f'<meta name="ui-build" content="{build_id}">', encoding="utf-8"
        )
        app = _app(history, EventBus(), frontend_dir=frontend)
        async with TestClient(TestServer(app)) as client:
            ws = await client.ws_connect("/events")
            await _receive_hello(ws, build_id=build_id)
            first = await ws.receive_json()
            await ws.close()
        assert first == {"type": "events_ready", "sequence": 30}
    finally:
        history.close()


@pytest.mark.filterwarnings("ignore:It is recommended to use web.AppKey instances for keys")
async def test_reconnect_with_a_cursor_delivers_exactly_the_gap(tmp_path: Path) -> None:
    history = HistoryIndex(tmp_path / "mux.db")
    try:
        await _seed(history, 10)
        async with TestClient(TestServer(_app(history, EventBus()))) as client:
            ws = await client.ws_connect("/events?after_seq=7")
            await _receive_hello(ws)
            seqs = [(await ws.receive_json())["seq"] for _ in range(3)]
            await ws.close()
        assert seqs == [8, 9, 10]
    finally:
        history.close()


@pytest.mark.filterwarnings("ignore:It is recommended to use web.AppKey instances for keys")
async def test_large_reconnect_gap_skips_replay_and_advances_to_watermark(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(server, "EVENTS_CATCHUP_LIMIT", 5)
    history = HistoryIndex(tmp_path / "mux.db")
    try:
        await _seed(history, 30)
        async with TestClient(TestServer(_app(history, EventBus()))) as client:
            ws = await client.ws_connect("/events?after_seq=2")
            await _receive_hello(ws)
            frame = await ws.receive_json()
            await ws.close()
        assert frame == {
            "type": "events_gap",
            "reason": "catchup_truncated",
            "sequence": 30,
        }
    finally:
        history.close()


@pytest.mark.filterwarnings("ignore:It is recommended to use web.AppKey instances for keys")
async def test_live_events_follow_the_cold_watermark_without_duplication(tmp_path: Path) -> None:
    history = HistoryIndex(tmp_path / "mux.db")
    try:
        bus = EventBus(sink=history.append_event)
        await _seed(history, 2)
        async with TestClient(TestServer(_app(history, bus))) as client:
            ws = await client.ws_connect("/events")
            await _receive_hello(ws)
            assert await ws.receive_json() == {"type": "events_ready", "sequence": 2}
            await bus.emit("state_changed", session_id="s1", source="daemon", state="live")
            live = await ws.receive_json()
            assert live["seq"] == 3
            assert live.get("replay") is not True
            await ws.close()
    finally:
        history.close()


@pytest.mark.filterwarnings("ignore:It is recommended to use web.AppKey instances for keys")
async def test_browser_omits_audit_hook_payloads_and_advances_the_cursor(tmp_path: Path) -> None:
    history = HistoryIndex(tmp_path / "mux.db")
    try:
        await _seed(history, 3, event_type="PostToolUse")
        async with TestClient(TestServer(_app(history, EventBus()))) as client:
            ws = await client.ws_connect("/events?after_seq=1")
            await _receive_hello(ws)
            assert await ws.receive_json() == {"type": "events_cursor", "sequence": 3}
            await ws.close()
    finally:
        history.close()


@pytest.mark.filterwarnings("ignore:It is recommended to use web.AppKey instances for keys")
async def test_live_audit_hooks_are_omitted_but_later_state_is_delivered(tmp_path: Path) -> None:
    history = HistoryIndex(tmp_path / "mux.db")
    bus = EventBus(sink=history.append_event)
    try:
        async with TestClient(TestServer(_app(history, bus))) as client:
            ws = await client.ws_connect("/events")
            await _receive_hello(ws)
            assert await ws.receive_json() == {"type": "events_ready", "sequence": 0}
            await bus.emit("tool_result", session_id="s1", source="daemon", content="large")
            await bus.emit("state_changed", session_id="s1", source="daemon", state="live")
            live = await ws.receive_json()
            assert live["type"] == "state_changed"
            assert live["seq"] == 2
            await ws.close()
    finally:
        history.close()


@pytest.mark.filterwarnings("ignore:It is recommended to use web.AppKey instances for keys")
async def test_client_close_ends_the_handler_and_unsubscribes(tmp_path: Path) -> None:
    """A handler that never reads cannot see the client leave, so a suspended
    tab's socket lingers holding a 1024-slot queue and paying per-event fanout."""
    history = HistoryIndex(tmp_path / "mux.db")
    bus = EventBus()
    try:
        async with TestClient(TestServer(_app(history, bus))) as client:
            ws = await client.ws_connect("/events")
            await asyncio.sleep(0.02)
            assert bus.drop_stats()["subscribers"] == 1
            await ws.close()
            for _ in range(100):
                await asyncio.sleep(0.01)
                if bus.drop_stats()["subscribers"] == 0:
                    break
            assert bus.drop_stats()["subscribers"] == 0
    finally:
        history.close()


@pytest.mark.filterwarnings("ignore:It is recommended to use web.AppKey instances for keys")
async def test_malformed_cursor_is_rejected_without_leaking_a_subscriber(
    tmp_path: Path,
) -> None:
    history = HistoryIndex(tmp_path / "mux.db")
    bus = EventBus()
    try:
        async with TestClient(TestServer(_app(history, bus))) as client:
            ws = await client.ws_connect("/events?after_seq=abc")
            # The upgrade completes, then the handler rejects and closes.
            await ws.receive()
            await ws.close()
        await asyncio.sleep(0.05)
        assert bus.drop_stats()["subscribers"] == 0
    finally:
        history.close()


@pytest.mark.filterwarnings("ignore:It is recommended to use web.AppKey instances for keys")
async def test_the_socket_carries_device_presence_and_forgets_it_on_close(
    tmp_path: Path,
) -> None:
    """Presence rides this socket because every client holds one — including the
    desktop WebView, which cannot subscribe to Web Push and so reported nothing at
    all through the push-presence path."""
    history = HistoryIndex(tmp_path / "mux.db")
    presence = DevicePresenceStore()
    try:
        async with TestClient(TestServer(_app(history, EventBus(), presence))) as client:
            ws = await client.ws_connect("/events")
            await _receive_hello(ws)
            await ws.send_json(
                {
                    "type": "presence",
                    "profile": "desktop",
                    "visible": True,
                    "focused": True,
                    "interaction_age": 2,
                }
            )
            for _ in range(100):
                await asyncio.sleep(0.01)
                if presence.active_profiles():
                    break
            assert presence.active_profiles() == {"desktop"}
            # Junk on the socket is ignored rather than dropping the connection.
            await ws.send_str("not json")
            await ws.send_json({"type": "presence", "profile": "tablet"})
            await asyncio.sleep(0.02)
            assert presence.active_profiles() == {"desktop"}
            await ws.close()
        for _ in range(100):
            await asyncio.sleep(0.01)
            if not presence.snapshot()["devices"]:
                break
        # A closed socket is a device nobody is looking at.
        assert presence.snapshot()["devices"] == []
    finally:
        history.close()
