from __future__ import annotations

import gzip
import json

from aiohttp import WSMsgType, web
from aiohttp.test_utils import TestClient, TestServer

from swe_mux import app_keys as keys
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


async def _classified_output(request: web.Request) -> web.WebSocketResponse:
    ws = metered_websocket(request, "pty")
    await ws.prepare(request)
    await ws.send_bytes_classified(b"retained terminal", "attach_replay")
    return ws


async def _network_snapshot(request: web.Request) -> web.Response:
    return compact_json_response(request.app[keys.NETWORK_USAGE].snapshot())


async def _always_compressed(request: web.Request) -> web.WebSocketResponse:
    ws = metered_websocket(request, "test", compress=True)
    await ws.prepare(request)
    await ws.send_str("ready")
    return ws


def _app() -> web.Application:
    app = web.Application(middlewares=[compressible_response_middleware])
    app[keys.NETWORK_USAGE] = NetworkUsage()
    app.on_response_prepare.append(record_network_response)
    app.router.add_get("/api/items/{item_id}", _payload)
    app.router.add_get("/api/diagnostics/network", _network_snapshot)
    app.router.add_get("/socket", _echo)
    app.router.add_get("/classified", _classified_output)
    app.router.add_get("/compressed", _always_compressed)
    return app


async def test_a_loopback_peer_is_not_offered_per_message_compression() -> None:
    """The client offers permessage-deflate, as every browser does, and is declined:
    on this machine the saving never crosses a network and the daemon pays for it,
    on the event loop for any frame of 16 KiB or less (`DAEMON_LOAD_ISOLATION.md`
    item 8)."""
    async with TestClient(TestServer(_app())) as client:
        ws = await client.ws_connect("/classified", compress=15)
        assert await ws.receive_bytes() == b"retained terminal"
        assert ws.compress == 0
        await ws.close()


async def test_a_tailnet_peer_behind_the_serve_proxy_keeps_compression() -> None:
    """Tailscale Serve reaches the loopback listener and names the phone in
    `X-Forwarded-For`; deciding on the direct address would strip compression from
    exactly the peer on a real network."""
    async with TestClient(TestServer(_app())) as client:
        ws = await client.ws_connect(
            "/classified", compress=15, headers={"X-Forwarded-For": "100.64.1.2"}
        )
        assert await ws.receive_bytes() == b"retained terminal"
        assert ws.compress == 15
        await ws.close()


async def test_a_caller_that_names_compression_decides_for_itself() -> None:
    async with TestClient(TestServer(_app())) as client:
        ws = await client.ws_connect("/compressed", compress=15)
        assert await ws.receive_str() == "ready"
        assert ws.compress == 15
        await ws.close()


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

    snapshot = app[keys.NETWORK_USAGE].snapshot()
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

    channel = app[keys.NETWORK_USAGE].snapshot()["websocket_channels"][0]
    assert channel == {
        "channel": "test",
        "connections": 1,
        "active_connections": 0,
        "received_frames": 1,
        "received_bytes": 5,
        "sent_frames": 1,
        "sent_bytes": 5,
    }


async def test_websocket_payload_classification_is_peer_aware_and_not_double_counted() -> None:
    app = _app()
    async with TestClient(TestServer(app)) as client:
        ws = await client.ws_connect(
            "/classified", headers={"X-Forwarded-For": "100.64.1.2"}
        )
        assert await ws.receive_bytes() == b"retained terminal"
        await ws.close()

    snapshot = app[keys.NETWORK_USAGE].snapshot()
    assert snapshot["totals"]["websocket"]["sent_bytes"] == len(b"retained terminal")
    assert snapshot["websocket_sent_payloads"] == [
        {
            "peer": "100.64.1.2",
            "channel": "pty",
            "kind": "attach_replay",
            "frames": 1,
            "bytes": len(b"retained terminal"),
        }
    ]


def test_reset_starts_a_new_measurement_window() -> None:
    meter = NetworkUsage()
    meter.websocket_opened("100.64.0.2", "events")
    meter.websocket_frame("100.64.0.2", "events", "sent", 128)
    assert meter.snapshot()["totals"]["websocket"]["sent_bytes"] == 128

    meter.reset()

    assert meter.snapshot()["totals"]["websocket"]["sent_bytes"] == 0
    assert meter.snapshot()["peers"] == []
    assert meter.snapshot()["websocket_sent_payloads"] == []


async def test_snapshot_request_does_not_measure_itself() -> None:
    app = _app()
    async with TestClient(TestServer(app)) as client:
        response = await client.get("/api/diagnostics/network")
        assert (await response.json())["totals"]["http"]["requests"] == 0

    assert app[keys.NETWORK_USAGE].snapshot()["totals"]["http"]["requests"] == 0


# --- traffic another process carried (the terminal gateway, item 32) -----------------


def _gateway_report(
    *, sent: int, received: int = 0, frames: int = 1, active: int = 1, started_at: float = 0.0
) -> dict[str, object]:
    """A terminal gateway's cumulative `NetworkUsage.snapshot()`, as it sends it."""
    websocket = {
        "connections": 1,
        "active_connections": active,
        "received_frames": frames,
        "received_bytes": received,
        "sent_frames": frames,
        "sent_bytes": sent,
    }
    return {
        "started_at": started_at,
        "peers": [{"peer": "100.64.0.7", "http": {"requests": 0}, "websocket": websocket}],
        "http_routes": [],
        "websocket_channels": [{"channel": "pty", **websocket}],
        "websocket_sent_payloads": [
            {
                "peer": "100.64.0.7",
                "channel": "pty",
                "kind": "live_output",
                "frames": frames,
                "bytes": sent,
            }
        ],
    }


def test_the_gateways_traffic_is_counted_under_its_own_name() -> None:
    usage = NetworkUsage()
    usage.websocket_opened("127.0.0.1", "pty")
    usage.websocket_frame("127.0.0.1", "pty", "sent", 100)
    usage.merge_remote(
        {"gateway_id": "g1", "usage": _gateway_report(sent=500, started_at=usage.started_at + 1)}
    )
    snapshot = usage.snapshot()
    channels = {row["channel"]: row for row in snapshot["websocket_channels"]}
    assert channels["pty"]["sent_bytes"] == 100
    assert channels["pty (gateway)"]["sent_bytes"] == 500
    assert snapshot["totals"]["websocket"]["sent_bytes"] == 600
    peers = {row["peer"]: row for row in snapshot["peers"]}
    assert peers["100.64.0.7"]["websocket"]["sent_bytes"] == 500
    payloads = {(row["channel"], row["kind"]): row for row in snapshot["websocket_sent_payloads"]}
    assert payloads[("pty (gateway)", "live_output")]["bytes"] == 500
    # Folded into the answer, never into this process's own counters.
    assert usage.snapshot()["totals"]["websocket"]["sent_bytes"] == 600


def test_a_report_replaces_the_last_one_from_the_same_process() -> None:
    usage = NetworkUsage()
    later = usage.started_at + 1
    usage.merge_remote({"gateway_id": "g1", "usage": _gateway_report(sent=500, started_at=later)})
    usage.merge_remote({"gateway_id": "g1", "usage": _gateway_report(sent=800, started_at=later)})
    # Cumulative: the second report is the total, not an increment.
    assert usage.snapshot()["totals"]["websocket"]["sent_bytes"] == 800
    # A restarted gateway is a new process, and both count.
    usage.merge_remote({"gateway_id": "g2", "usage": _gateway_report(sent=50, started_at=later)})
    assert usage.snapshot()["totals"]["websocket"]["sent_bytes"] == 850


def test_a_fresh_window_counts_only_what_comes_after_it() -> None:
    usage = NetworkUsage()
    later = usage.started_at + 1
    usage.merge_remote({"gateway_id": "g1", "usage": _gateway_report(sent=500, started_at=later)})
    usage.reset()
    assert usage.snapshot()["totals"]["websocket"]["sent_bytes"] == 0
    usage.merge_remote(
        {"gateway_id": "g1", "usage": _gateway_report(sent=700, active=2, started_at=later)}
    )
    totals = usage.snapshot()["totals"]["websocket"]
    assert totals["sent_bytes"] == 200
    # A gauge is the latest reading, not a difference.
    assert totals["active_connections"] == 2


def test_a_process_older_than_the_window_starts_counting_at_its_first_report() -> None:
    """A gateway that outlived the previous daemon carried some traffic before this
    daemon's window began; none of that is this window's."""
    usage = NetworkUsage()
    earlier = usage.started_at - 60
    usage.merge_remote(
        {"gateway_id": "g1", "usage": _gateway_report(sent=9000, started_at=earlier)}
    )
    assert usage.snapshot()["totals"]["websocket"]["sent_bytes"] == 0
    usage.merge_remote(
        {"gateway_id": "g1", "usage": _gateway_report(sent=9400, started_at=earlier)}
    )
    assert usage.snapshot()["totals"]["websocket"]["sent_bytes"] == 400


def test_a_malformed_report_counts_nothing_and_breaks_nothing() -> None:
    usage = NetworkUsage()
    usage.merge_remote({"gateway_id": "", "usage": {}})
    usage.merge_remote({"gateway_id": "g1", "usage": "nonsense"})
    usage.merge_remote(
        {
            "gateway_id": "g2",
            "usage": {
                "started_at": usage.started_at + 1,
                "peers": ["not a row", {"peer": "x", "websocket": {"sent_bytes": "many"}}],
                "websocket_channels": [{"channel": "pty", "sent_bytes": -5, "sent_frames": True}],
            },
        }
    )
    snapshot = usage.snapshot()
    assert snapshot["totals"]["websocket"]["sent_bytes"] == 0
    assert snapshot["totals"]["websocket"]["sent_frames"] == 0


def test_the_processes_remembered_are_bounded() -> None:
    from swe_mux.network_usage import MAX_REMOTE_SOURCES

    usage = NetworkUsage()
    later = usage.started_at + 1
    for index in range(MAX_REMOTE_SOURCES + 3):
        usage.merge_remote(
            {"gateway_id": f"g{index}", "usage": _gateway_report(sent=1, started_at=later)}
        )
    assert usage.snapshot()["totals"]["websocket"]["sent_bytes"] == MAX_REMOTE_SOURCES
