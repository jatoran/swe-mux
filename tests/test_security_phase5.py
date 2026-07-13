from __future__ import annotations

import asyncio
import time
from collections import deque
from types import SimpleNamespace

import pytest
from aiohttp import WSMsgType, web
from aiohttp.test_utils import TestClient, TestServer

from swe_mux.processes import PreviewRegistration
from swe_mux.server import (
    HOOK_RATE_LIMIT,
    PREVIEW_HTTP_CONCURRENCY,
    PREVIEW_WS_CONCURRENCY,
    allowed_browser_host,
    browser_origin_matches_request,
    error_middleware,
    hook_ingress,
    preview_proxy,
    preview_target,
    rewrite_preview_html,
    rewrite_preview_javascript,
    security_middleware,
)
from swe_mux.tailscale import _urls, is_tailscale_ip, listener_host_values

pytestmark = pytest.mark.filterwarnings(
    "ignore:It is recommended to use web.AppKey instances for keys"
)


def test_supported_browser_hosts_are_loopback_or_tailnet_dns() -> None:
    assert allowed_browser_host("localhost")
    assert allowed_browser_host("127.0.0.1")
    assert allowed_browser_host("::1")
    assert allowed_browser_host("workstation.example-tailnet.ts.net")
    assert allowed_browser_host("100.101.102.103")
    assert allowed_browser_host("fd7a:115c:a1e0::1234")
    assert not allowed_browser_host("0.0.0.0")
    assert not allowed_browser_host("workstation.ts.net.attacker.example")
    assert not allowed_browser_host("192.168.1.20")


def test_only_tailscale_address_ranges_are_accepted() -> None:
    assert is_tailscale_ip("100.64.0.1")
    assert is_tailscale_ip("100.127.255.254")
    assert is_tailscale_ip("fd7a:115c:a1e0::1")
    assert not is_tailscale_ip("100.63.255.255")
    assert not is_tailscale_ip("192.168.1.20")


def test_listener_uses_localhost_plus_only_the_detected_tailnet_address() -> None:
    assert listener_host_values("127.0.0.1", True, "100.101.102.103") == [
        "127.0.0.1", "100.101.102.103"
    ]
    assert listener_host_values("127.0.0.1", False, "100.101.102.103") == ["127.0.0.1"]
    assert listener_host_values("127.0.0.1", True, None) == ["127.0.0.1"]


def test_tailscale_status_url_discovery_is_bounded_to_values() -> None:
    payload = {"TCP": {"443": {"HTTPS": True}}, "Web": {"https://mux.tail.ts.net": {}}}
    assert _urls(payload) == ["https://mux.tail.ts.net"]


def test_preview_html_rewrites_root_resources_into_registration() -> None:
    source = b'<script src="/src/main.ts"></script><a href="/docs">docs</a>'
    rewritten = rewrite_preview_html(source, "/preview/preview-id/")
    assert b'src="/preview/preview-id/src/main.ts"' in rewritten
    assert b'href="/preview/preview-id/docs"' in rewritten
    assert b"class extends NativeWebSocket" in rewritten
    assert b"window.fetch" in rewritten
    javascript = b'import client from "/@vite/client"; import("/src/lazy.ts")'
    rewritten_javascript = rewrite_preview_javascript(javascript, "/preview/preview-id/")
    assert b'from "/preview/preview-id/@vite/client"' in rewritten_javascript
    assert b'import("/preview/preview-id/src/lazy.ts")' in rewritten_javascript


def test_browser_origin_must_match_host_and_explicit_port() -> None:
    assert browser_origin_matches_request(
        "http://100.101.102.103:8765", "100.101.102.103:8765"
    )
    assert browser_origin_matches_request("https://mux.example.ts.net", "mux.example.ts.net")
    assert not browser_origin_matches_request(
        "http://100.101.102.103:9999", "100.101.102.103:8765"
    )
    assert not browser_origin_matches_request("https://attacker.example", "mux.example.ts.net")


def test_preview_target_never_accepts_a_changed_destination() -> None:
    item = SimpleNamespace(url="http://127.0.0.1:5173/base/")
    target, origin = preview_target(item, "src/main.ts", "v=1")
    assert target == "http://127.0.0.1:5173/base/src/main.ts?v=1"
    assert origin == "http://127.0.0.1:5173"
    with pytest.raises(ValueError, match="loopback"):
        preview_target(SimpleNamespace(url="http://169.254.169.254/"), "metadata")


def _proxy_application(registration: PreviewRegistration) -> web.Application:
    app = web.Application(middlewares=[security_middleware], client_max_size=12 * 1024 * 1024)
    app["previews"] = SimpleNamespace(items={registration.id: registration})
    app["sessions"] = SimpleNamespace(sessions={registration.session_id: object()})
    app["preview_http_semaphore"] = asyncio.Semaphore(PREVIEW_HTTP_CONCURRENCY)
    app["preview_ws_semaphore"] = asyncio.Semaphore(PREVIEW_WS_CONCURRENCY)
    app.router.add_route("*", "/preview/{preview_id}/{tail:.*}", preview_proxy)
    return app


@pytest.mark.asyncio
async def test_preview_proxy_preserves_http_methods_query_and_upstream_origin() -> None:
    async def inspect(request: web.Request) -> web.Response:
        return web.json_response(
            {
                "method": request.method,
                "query": request.query_string,
                "body": (await request.read()).decode(),
                "origin": request.headers.get("Origin"),
            }
        )

    upstream_app = web.Application()
    upstream_app.router.add_route("*", "/api/items", inspect)
    async with TestServer(upstream_app, host="127.0.0.1") as upstream:
        registration = PreviewRegistration(
            "preview-a",
            "session-a",
            "default",
            f"http://127.0.0.1:{upstream.port}/",
            "127.0.0.1",
            upstream.port,
            "detected",
            0,
        )
        async with TestClient(TestServer(_proxy_application(registration))) as client:
            origin = str(client.make_url("/")).rstrip("/")
            response = await client.post(
                "/preview/preview-a/api/items?mode=mobile",
                data=b"payload",
                headers={"Origin": origin, "Content-Type": "text/plain"},
            )
            assert response.status == 200
            payload = await response.json()
            assert payload == {
                "method": "POST",
                "query": "mode=mobile",
                "body": "payload",
                "origin": f"http://127.0.0.1:{upstream.port}",
            }
            assert response.headers["X-Content-Type-Options"] == "nosniff"


@pytest.mark.asyncio
async def test_preview_proxy_bridges_websocket_subprotocol_and_messages() -> None:
    async def hmr(request: web.Request) -> web.WebSocketResponse:
        assert request.headers["Origin"].startswith("http://127.0.0.1:")
        ws = web.WebSocketResponse(protocols=("vite-hmr",))
        await ws.prepare(request)
        async for message in ws:
            if message.type == WSMsgType.TEXT:
                await ws.send_str(f"hmr:{message.data}")
        return ws

    upstream_app = web.Application()
    upstream_app.router.add_get("/hmr", hmr)
    async with TestServer(upstream_app, host="127.0.0.1") as upstream:
        registration = PreviewRegistration(
            "preview-a",
            "session-a",
            "default",
            f"http://127.0.0.1:{upstream.port}/",
            "127.0.0.1",
            upstream.port,
            "detected",
            0,
        )
        async with TestClient(TestServer(_proxy_application(registration))) as client:
            origin = str(client.make_url("/")).rstrip("/")
            ws = await client.ws_connect(
                "/preview/preview-a/hmr",
                protocols=("vite-hmr",),
                headers={"Origin": origin},
            )
            assert ws.protocol == "vite-hmr"
            await ws.send_str("update")
            assert (await ws.receive()).data == "hmr:update"
            await ws.close()


@pytest.mark.asyncio
async def test_security_boundary_accepts_tailnet_and_serve_origins_only() -> None:
    async def mutate(_: web.Request) -> web.Response:
        return web.json_response({"ok": True})

    async def socket(request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.close()
        return ws

    app = web.Application(middlewares=[security_middleware])
    app.router.add_post("/mutate", mutate)
    app.router.add_get("/socket", socket)
    async with TestClient(TestServer(app)) as client:
        direct = await client.post(
            "/mutate",
            headers={
                "Host": "100.101.102.103:8765",
                "Origin": "http://100.101.102.103:8765",
            },
        )
        assert direct.status == 200
        serve = await client.post(
            "/mutate",
            headers={"Host": "mux.example.ts.net", "Origin": "https://mux.example.ts.net"},
        )
        assert serve.status == 200
        crossed = await client.post(
            "/mutate",
            headers={"Host": "mux.example.ts.net", "Origin": "https://attacker.example"},
        )
        assert crossed.status == 403
        lan = await client.post(
            "/mutate",
            headers={"Host": "192.168.1.10:8765", "Origin": "http://192.168.1.10:8765"},
        )
        assert lan.status == 421
        ws = await client.ws_connect(
            "/socket",
            headers={
                "Host": "100.101.102.103:8765",
                "Origin": "http://100.101.102.103:8765",
            },
        )
        await ws.close()


@pytest.mark.asyncio
async def test_hook_ingress_rejects_expired_sessions_and_bounded_bursts() -> None:
    class Events:
        async def emit(self, *_: object, **__: object) -> None:
            return None

    record = SimpleNamespace(id="session-a", state="running")
    session = SimpleNamespace(record=record, hook_secret="secret")
    app = web.Application(middlewares=[error_middleware, security_middleware])
    app["sessions"] = SimpleNamespace(resolve=lambda _: session)
    app["events"] = Events()
    app["hook_ingress_windows"] = {
        "session-a": deque([time.monotonic()] * HOOK_RATE_LIMIT)
    }
    app.router.add_post("/api/hooks/{sid}", hook_ingress)
    async with TestClient(TestServer(app)) as client:
        limited = await client.post(
            "/api/hooks/session-a",
            json={"event": "Stop", "payload": {}},
            headers={"X-Mux-Hook-Secret": "secret"},
        )
        assert limited.status == 429
        record.state = "exited"
        app["hook_ingress_windows"]["session-a"].clear()
        expired = await client.post(
            "/api/hooks/session-a",
            json={"event": "Stop", "payload": {}},
            headers={"X-Mux-Hook-Secret": "secret"},
        )
        assert expired.status == 410
