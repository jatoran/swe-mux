"""DAEMON_LOAD_ISOLATION item 26 - one browser policy for the daemon and the gateway.

The daemon requires a page to be its own origin. The terminal gateway serves
pages delivered by the daemon or by Tailscale Serve, on another port, so it
accepts the same host on the daemon's port, its own, or Serve's 443 - and never
another host, because a page from another machine on the tailnet is not this
machine's page.
"""

from __future__ import annotations

import json
import subprocess
import sys

import pytest
from aiohttp import WSServerHandshakeError, web
from aiohttp.test_utils import TestClient, TestServer

from swe_mux.http_origin_policy import (
    allowed_browser_host,
    authority_host,
    browser_origin_matches_request,
    gateway_middleware,
    gateway_origin_allowed,
)
from swe_mux.server import security_middleware

DAEMON = 8765
GATEWAY = 8766
TAILNET_IP = "100.101.102.103"
SERVE_NAME = "workstation.example-tailnet.ts.net"


def _gateway(origin: str, host: str) -> bool:
    return gateway_origin_allowed(origin, host, daemon_port=DAEMON, gateway_port=GATEWAY)


@pytest.mark.parametrize(
    ("origin", "host", "why"),
    [
        (f"http://127.0.0.1:{DAEMON}", f"127.0.0.1:{GATEWAY}", "the daemon's page on loopback"),
        (f"http://localhost:{DAEMON}", f"localhost:{GATEWAY}", "the daemon's page by name"),
        (f"http://[::1]:{DAEMON}", f"[::1]:{GATEWAY}", "the daemon's page over IPv6 loopback"),
        (f"http://{TAILNET_IP}:{DAEMON}", f"{TAILNET_IP}:{GATEWAY}", "direct tailnet HTTP"),
        (f"http://{TAILNET_IP}:{GATEWAY}", f"{TAILNET_IP}:{GATEWAY}", "the gateway's own port"),
        (f"https://{SERVE_NAME}", SERVE_NAME, "Serve's page through Serve's /pty mount"),
        (f"https://{SERVE_NAME}", f"{SERVE_NAME}:{GATEWAY}", "Serve's page, gateway direct"),
        (f"https://{SERVE_NAME}:443", f"{SERVE_NAME}:{GATEWAY}", "Serve's port stated explicitly"),
        ("http://localhost:5173", "localhost:5173", "a reverse proxy presenting the page's origin"),
        (f"HTTP://LOCALHOST:{DAEMON}", f"localhost:{GATEWAY}", "scheme and host case-insensitive"),
    ],
)
def test_the_gateway_accepts_a_page_this_machine_served(origin: str, host: str, why: str) -> None:
    assert _gateway(origin, host), why


@pytest.mark.parametrize(
    ("origin", "host", "why"),
    [
        (f"http://100.64.0.5:{DAEMON}", f"{TAILNET_IP}:{GATEWAY}", "another tailnet machine"),
        ("https://other.example-tailnet.ts.net", f"{SERVE_NAME}:{GATEWAY}", "another Serve name"),
        (f"http://127.0.0.1:{DAEMON}", f"localhost:{GATEWAY}", "a different name for this host"),
        ("http://127.0.0.1:9999", f"127.0.0.1:{GATEWAY}", "an unrelated local port"),
        (f"http://{TAILNET_IP}", f"{TAILNET_IP}:{GATEWAY}", "plain HTTP on 80 is not Serve"),
        (f"http://{SERVE_NAME}", f"{SERVE_NAME}:{GATEWAY}", "Serve is HTTPS, never HTTP on 80"),
        (f"https://{TAILNET_IP}", f"{TAILNET_IP}:{GATEWAY}", "443 counts only on a ts.net name"),
        ("https://localhost", f"localhost:{GATEWAY}", "443 on loopback is not Serve"),
        (f"http://192.168.1.20:{DAEMON}", f"192.168.1.20:{GATEWAY}", "a LAN host is not allowed"),
        ("https://attacker.example", "attacker.example", "same-origin on a disallowed host"),
        (f"https://{SERVE_NAME}.evil.example", f"{SERVE_NAME}.evil.example", "suffix trick"),
        ("null", f"127.0.0.1:{GATEWAY}", "a sandboxed frame"),
        (f"file://127.0.0.1:{DAEMON}", f"127.0.0.1:{GATEWAY}", "not a web scheme"),
        ("http://127.0.0.1:notaport", f"127.0.0.1:{GATEWAY}", "an unparseable origin"),
        (f"http://127.0.0.1:{DAEMON}", "127.0.0.1:notaport", "an unparseable Host"),
        (f"http://127.0.0.1:{DAEMON}", "", "no Host"),
    ],
)
def test_the_gateway_refuses_every_other_page(origin: str, host: str, why: str) -> None:
    assert not _gateway(origin, host), why


def test_the_daemon_still_requires_its_exact_origin() -> None:
    # The daemon's rule is unchanged by sharing: a page on the gateway's port, or
    # Serve's page reaching the daemon directly, is not the daemon's own origin.
    assert browser_origin_matches_request(f"http://{TAILNET_IP}:{DAEMON}", f"{TAILNET_IP}:{DAEMON}")
    assert browser_origin_matches_request(f"https://{SERVE_NAME}", SERVE_NAME)
    assert not browser_origin_matches_request(
        f"http://{TAILNET_IP}:{GATEWAY}", f"{TAILNET_IP}:{DAEMON}"
    )
    assert not browser_origin_matches_request(f"https://{SERVE_NAME}", f"{SERVE_NAME}:{DAEMON}")


def test_hosts_and_authorities() -> None:
    assert allowed_browser_host(f"{SERVE_NAME}.")
    assert not allowed_browser_host("")
    assert authority_host(f"[::1]:{GATEWAY}") == "::1"
    assert authority_host("127.0.0.1:notaport") == "127.0.0.1"
    assert authority_host("[::1") == ""


async def _socket(request: web.Request) -> web.WebSocketResponse:
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    await ws.close()
    return ws


async def _health(_: web.Request) -> web.Response:
    return web.json_response({"ok": True})


async def _upgrade(client: TestClient, path: str, headers: dict[str, str]) -> int:
    """The status a WebSocket upgrade gets: 101, or the refusal's."""
    try:
        ws = await client.ws_connect(path, headers=headers)
    except WSServerHandshakeError as refused:
        return refused.status
    await ws.close()
    return 101


@pytest.mark.asyncio
async def test_the_gateway_middleware_applies_both_refusals() -> None:
    policy = gateway_middleware(daemon_port=DAEMON, gateway_port=GATEWAY)
    app = web.Application(middlewares=[policy])
    app.router.add_get("/pty/{sid}", _socket)
    app.router.add_get("/health", _health)
    async with TestClient(TestServer(app)) as client:
        direct = {"Host": f"{TAILNET_IP}:{GATEWAY}", "Origin": f"http://{TAILNET_IP}:{DAEMON}"}
        assert await _upgrade(client, "/pty/s1", direct) == 101
        serve = {"Host": SERVE_NAME, "Origin": f"https://{SERVE_NAME}"}
        assert await _upgrade(client, "/pty/s1", serve) == 101
        # A client that is not a browser sends no Origin and is judged on its Host.
        assert await _upgrade(client, "/pty/s1", {"Host": f"127.0.0.1:{GATEWAY}"}) == 101

        foreign = {"Host": f"{TAILNET_IP}:{GATEWAY}", "Origin": f"http://100.64.0.5:{DAEMON}"}
        assert await _upgrade(client, "/pty/s1", foreign) == 403
        lan = {"Host": f"192.168.1.20:{GATEWAY}", "Origin": f"http://192.168.1.20:{DAEMON}"}
        assert await _upgrade(client, "/pty/s1", lan) == 421

        # A cross-origin page may trigger a GET but cannot read it, so a GET is
        # judged on its Host alone - as the daemon judges one.
        health = await client.get(
            "/health", headers={"Host": f"127.0.0.1:{GATEWAY}", "Origin": "https://x.example"}
        )
        assert health.status == 200
        rebound = await client.get("/health", headers={"Host": f"attacker.example:{GATEWAY}"})
        assert rebound.status == 421


@pytest.mark.asyncio
async def test_the_daemon_refuses_a_page_on_the_gateway_port() -> None:
    """The daemon's middleware kept its exact rule when it moved onto the module."""
    app = web.Application(middlewares=[security_middleware])
    app.router.add_get("/pty/{sid}", _socket)
    async with TestClient(TestServer(app)) as client:
        own = {"Host": f"{TAILNET_IP}:{DAEMON}", "Origin": f"http://{TAILNET_IP}:{DAEMON}"}
        assert await _upgrade(client, "/pty/s1", own) == 101
        gateway_page = {"Host": f"{TAILNET_IP}:{DAEMON}", "Origin": f"http://{TAILNET_IP}:{GATEWAY}"}
        assert await _upgrade(client, "/pty/s1", gateway_page) == 403


def test_the_policy_loads_none_of_the_daemon() -> None:
    """The gateway imports this module, so it must not drag the daemon in."""
    probe = (
        "import json, sys, swe_mux.http_origin_policy; "
        "print(json.dumps(sorted(m for m in sys.modules if m.startswith('swe_mux'))))"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True, timeout=60
    )
    assert set(json.loads(result.stdout)) == {"swe_mux", "swe_mux.http_origin_policy"}
