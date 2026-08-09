from __future__ import annotations

import gzip
import json

from aiohttp import WSMsgType, web
from aiohttp.test_utils import TestClient, TestServer

from swe_mux.network_usage import (
    NetworkUsage,
    compact_json_response,
    compressible_response_middleware,
    metered_websocket,
    record_network_response,
)


async def _payload(request: web.Request) -> web.Response:
    return compact_json_response({"value": "repeated-value-" * 400})


async def _echo(request: web.Request) -> web.WebSocketResponse:
    ws = metered_websocket(request, "test")
    await ws.prepare(request)
    message = await ws.receive()
    assert message.type == WSMsgType.TEXT
    await ws.send_str(message.data.upper())
    return ws


async def _network_snapshot(request: web.Request) -> web.Response:
    return compact_json_response(request.app["network_usage"].snapshot())


def _app() -> web.Application:
    app = web.Application(middlewares=[compressible_response_middleware])
    app["network_usage"] = NetworkUsage()
    app.on_response_prepare.append(record_network_response)
    app.router.add_get("/api/items/{item_id}", _payload)
    app.router.add_get("/api/diagnostics/network", _network_snapshot)
    app.router.add_get("/socket", _echo)
    return app


async def test_http_usage_counts_compressed_bytes_by_template_and_peer() -> None:
    app = _app()
    async with TestClient(TestServer(app), auto_decompress=False) as client:
        response = await client.get(
            "/api/items/user-provided-id", headers={"Accept-Encoding": "gzip"}
        )
        encoded = await response.read()

    assert response.headers["Content-Encoding"] == "gzip"
    decoded = gzip.decompress(encoded)
    assert json.loads(decoded) == {"value": "repeated-value-" * 400}
    assert len(encoded) < len(decoded) // 4

    snapshot = app["network_usage"].snapshot()
    assert snapshot["totals"]["http"] == {
        "requests": 1,
        "request_bytes": 0,
        "response_bytes": len(encoded),
        "compressed_responses": 1,
        "unknown_request_bodies": 0,
        "unknown_response_bodies": 0,
    }
    assert snapshot["http_routes"][0]["route"] == "/api/items/{item_id}"
    assert snapshot["peers"][0]["peer"] == "127.0.0.1"


async def test_websocket_usage_counts_application_frames_and_connections() -> None:
    app = _app()
    async with TestClient(TestServer(app)) as client:
        ws = await client.ws_connect("/socket")
        await ws.send_str("phone")
        assert await ws.receive_str() == "PHONE"
        await ws.close()

    channel = app["network_usage"].snapshot()["websocket_channels"][0]
    assert channel == {
        "channel": "test",
        "connections": 1,
        "active_connections": 0,
        "received_frames": 1,
        "received_bytes": 5,
        "sent_frames": 1,
        "sent_bytes": 5,
    }


def test_reset_starts_a_new_measurement_window() -> None:
    meter = NetworkUsage()
    meter.websocket_opened("100.64.0.2", "events")
    meter.websocket_frame("100.64.0.2", "events", "sent", 128)
    assert meter.snapshot()["totals"]["websocket"]["sent_bytes"] == 128

    meter.reset()

    assert meter.snapshot()["totals"]["websocket"]["sent_bytes"] == 0
    assert meter.snapshot()["peers"] == []


async def test_snapshot_request_does_not_measure_itself() -> None:
    app = _app()
    async with TestClient(TestServer(app)) as client:
        response = await client.get("/api/diagnostics/network")
        assert (await response.json())["totals"]["http"]["requests"] == 0

    assert app["network_usage"].snapshot()["totals"]["http"]["requests"] == 0
