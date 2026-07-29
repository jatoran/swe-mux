"""`/events` catch-up contract.

Regression coverage for the audited defect: with no cursor the daemon replayed
the 2000 *oldest* retained events on every connect, so an established install
re-sent days-old history and never delivered the events the client actually
missed. The reconnect mechanism did the opposite of its purpose.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from swe_mux import server
from swe_mux.device_presence import DevicePresenceStore
from swe_mux.event_bus import EventBus
from swe_mux.history import HistoryIndex
from swe_mux.server import events_ws


def _app(
    history: HistoryIndex, events: EventBus, presence: DevicePresenceStore | None = None
) -> web.Application:
    app = web.Application()
    app["history"] = history
    app["events"] = events
    app["device_presence"] = presence or DevicePresenceStore()
    app.router.add_get("/events", events_ws)
    return app


async def _seed(history: HistoryIndex, count: int, *, session_id: str = "s1") -> None:
    events = EventBus(sink=history.append_event)
    for index in range(count):
        await events.emit("tool_use", session_id=session_id, source="daemon", tool=f"t{index}")


@pytest.mark.filterwarnings("ignore:It is recommended to use web.AppKey instances for keys")
async def test_cold_open_serves_the_newest_events_not_the_oldest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(server, "EVENTS_CATCHUP_LIMIT", 5)
    history = HistoryIndex(tmp_path / "mux.db")
    try:
        await _seed(history, 30)
        async with TestClient(TestServer(_app(history, EventBus()))) as client:
            ws = await client.ws_connect("/events")
            first = await ws.receive_json()
            # More history exists than the window carries: the client is told to
            # full-refresh rather than assume the replay covered the gap.
            assert first == {"type": "events_gap", "reason": "catchup_truncated"}
            seqs = []
            for _ in range(5):
                frame = await ws.receive_json()
                assert frame["replay"] is True
                seqs.append(frame["seq"])
            await ws.close()
        # Newest window, still ascending so the client can apply it in order.
        assert seqs == [26, 27, 28, 29, 30]
    finally:
        history.close()


@pytest.mark.filterwarnings("ignore:It is recommended to use web.AppKey instances for keys")
async def test_reconnect_with_a_cursor_delivers_exactly_the_gap(tmp_path: Path) -> None:
    history = HistoryIndex(tmp_path / "mux.db")
    try:
        await _seed(history, 10)
        async with TestClient(TestServer(_app(history, EventBus()))) as client:
            ws = await client.ws_connect("/events?after_seq=7")
            seqs = [(await ws.receive_json())["seq"] for _ in range(3)]
            await ws.close()
        assert seqs == [8, 9, 10]
    finally:
        history.close()


@pytest.mark.filterwarnings("ignore:It is recommended to use web.AppKey instances for keys")
async def test_short_history_is_replayed_without_a_gap_marker(tmp_path: Path) -> None:
    history = HistoryIndex(tmp_path / "mux.db")
    try:
        await _seed(history, 3)
        async with TestClient(TestServer(_app(history, EventBus()))) as client:
            ws = await client.ws_connect("/events")
            frames = [await ws.receive_json() for _ in range(3)]
            await ws.close()
        assert [frame["seq"] for frame in frames] == [1, 2, 3]
        assert all(frame["type"] == "tool_use" for frame in frames)
    finally:
        history.close()


@pytest.mark.filterwarnings("ignore:It is recommended to use web.AppKey instances for keys")
async def test_live_events_follow_the_catch_up_without_duplication(tmp_path: Path) -> None:
    history = HistoryIndex(tmp_path / "mux.db")
    try:
        bus = EventBus(sink=history.append_event)
        await _seed(history, 2)
        async with TestClient(TestServer(_app(history, bus))) as client:
            ws = await client.ws_connect("/events")
            assert [(await ws.receive_json())["seq"] for _ in range(2)] == [1, 2]
            await bus.emit("tool_use", session_id="s1", source="daemon", tool="live")
            live = await ws.receive_json()
            assert live["seq"] == 3
            assert live.get("replay") is not True
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
