"""Which browser pages may drive swe-mux's HTTP and WebSocket surfaces.

One policy for every process that serves a browser: the daemon, and the terminal
gateway (`development/DAEMON_LOAD_ISOLATION.md` items 26 and 28). A second copy in
the gateway would be a second decision, and the two would drift on exactly the
question this module exists to answer.

Two checks, in order:

- **The Host.** Only loopback, a Tailscale address, or a `*.ts.net` name. That is
  the DNS-rebinding defence: a page on an attacker's name that resolves to
  127.0.0.1 still sends its own name as the Host and is refused.
- **The Origin**, on the requests a page could abuse - a state change or a
  WebSocket upgrade. The daemon requires the page to be its own origin. The
  gateway serves pages the daemon (or Tailscale Serve) delivered, on another port,
  so it accepts the same host with the daemon's port, its own, or Serve's 443; it
  never accepts another host, because every name on the allowlist above names a
  machine, and a page from another machine on the tailnet is not this one's page.

A request with no `Origin` is not a browser's (every browser sends one on a
WebSocket upgrade and on a cross-origin state change), so it is judged on its Host
alone, as it always has been.

Kept free of the daemon's runtime so the gateway can import it without one.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Awaitable, Callable
from urllib.parse import SplitResult, urlsplit

from aiohttp import web
from aiohttp.typedefs import Middleware

LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
#: The suffix of a MagicDNS name, which is how Tailscale Serve's HTTPS page is reached.
TAILNET_DNS_SUFFIX = ".ts.net"
#: Tailscale Serve terminates HTTPS here (`tailscale.MOBILE_VOICE_HTTPS_PORT`).
SERVE_HTTPS_PORT = 443
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_DEFAULT_PORTS = {"http": 80, "https": 443}
_TAILNET_V4 = ipaddress.ip_network("100.64.0.0/10")
_TAILNET_V6 = ipaddress.ip_network("fd7a:115c:a1e0::/48")

Handler = Callable[[web.Request], Awaitable[web.StreamResponse]]


def is_tailscale_ip(value: str) -> bool:
    """Whether ``value`` is an address in Tailscale's CGNAT or ULA range."""
    try:
        address = ipaddress.ip_address(value.strip())
    except ValueError:
        return False
    return address in (_TAILNET_V4 if address.version == 4 else _TAILNET_V6)


def allowed_browser_host(host: str) -> bool:
    normalized = host.strip().rstrip(".").casefold()
    return (
        normalized in LOOPBACK_HOSTS
        or normalized.endswith(TAILNET_DNS_SUFFIX)
        or is_tailscale_ip(normalized)
    )


def authority_host(raw_host: str) -> str:
    """The host name in a `Host` header value, or "" when it does not parse."""
    try:
        return urlsplit(f"//{raw_host}").hostname or ""
    except ValueError:
        return ""


def request_host(request: web.BaseRequest) -> str:
    return authority_host(request.headers.get("Host", ""))


def is_websocket_upgrade(request: web.BaseRequest) -> bool:
    return request.headers.get("Upgrade", "").casefold() == "websocket"


def origin_to_check(request: web.BaseRequest) -> str | None:
    """The `Origin` this request must be judged on, or None when it needs none.

    Only a state change or a WebSocket upgrade can do what a page should not: a
    cross-origin page can trigger a plain GET but cannot read its answer.
    """
    origin = request.headers.get("Origin")
    if not origin:
        return None
    if request.method in SAFE_METHODS and not is_websocket_upgrade(request):
        return None
    return origin


def _parsed(origin: str, raw_host: str) -> tuple[SplitResult, SplitResult, int | None, int | None]:
    """Both authorities and their explicit ports; raises ValueError on a bad one."""
    parsed_origin = urlsplit(origin)
    parsed_host = urlsplit(f"//{raw_host}")
    return parsed_origin, parsed_host, parsed_origin.port, parsed_host.port


def browser_origin_matches_request(origin: str, raw_host: str) -> bool:
    """The daemon's rule: the page is this server's own origin, port included."""
    try:
        parsed_origin, parsed_host, origin_port, request_port = _parsed(origin, raw_host)
    except ValueError:
        return False
    if parsed_origin.scheme not in _DEFAULT_PORTS:
        return False
    if not parsed_origin.hostname or not parsed_host.hostname:
        return False
    if parsed_origin.hostname.casefold() != parsed_host.hostname.casefold():
        return False
    # Browsers include a non-default port in Origin and Host. A missing port on both sides
    # also covers Tailscale Serve's ordinary HTTPS authority.
    return origin_port == request_port


def gateway_origin_allowed(
    origin: str, raw_host: str, *, daemon_port: int, gateway_port: int
) -> bool:
    """The terminal gateway's rule: a page this machine served, on any of its ports.

    The page's host must be the host the gateway was reached on and must itself be
    allowed; its port may be the daemon's (the page the daemon serves directly),
    the gateway's own, or 443 on a `*.ts.net` name (the page Tailscale Serve
    serves). The daemon's exact same-origin rule is accepted too, which is what a
    reverse proxy in front of the gateway - Serve's `/gateway` mount, or Vite in
    development - presents.
    """
    try:
        parsed_origin, parsed_host, origin_port, _request_port = _parsed(origin, raw_host)
    except ValueError:
        return False
    scheme = parsed_origin.scheme
    origin_name = (parsed_origin.hostname or "").casefold()
    host_name = (parsed_host.hostname or "").casefold()
    if scheme not in _DEFAULT_PORTS or not origin_name or origin_name != host_name:
        return False
    if not allowed_browser_host(origin_name):
        return False
    if browser_origin_matches_request(origin, raw_host):
        return True
    effective_port = origin_port if origin_port is not None else _DEFAULT_PORTS[scheme]
    if effective_port in {daemon_port, gateway_port}:
        return True
    return (
        scheme == "https"
        and effective_port == SERVE_HTTPS_PORT
        and origin_name.rstrip(".").endswith(TAILNET_DNS_SUFFIX)
    )


def gateway_middleware(
    *, daemon_port: int, gateway_port: int | Callable[[], int]
) -> Middleware:
    """The terminal gateway's request policy, as an aiohttp middleware.

    The same two refusals the daemon's `security_middleware` makes, with the
    gateway's origin rule: 421 for a Host outside the allowlist, 403 for a page
    that is not this machine's. ``gateway_port`` may be read per request, for a
    gateway whose port the OS chose when it bound.
    """
    port_of = gateway_port if callable(gateway_port) else lambda: gateway_port

    @web.middleware
    async def gateway_policy(request: web.Request, handler: Handler) -> web.StreamResponse:
        if not allowed_browser_host(request_host(request)):
            raise web.HTTPMisdirectedRequest(text="unsupported Host")
        origin = origin_to_check(request)
        if origin is not None and not gateway_origin_allowed(
            origin,
            request.headers.get("Host", ""),
            daemon_port=daemon_port,
            gateway_port=port_of(),
        ):
            raise web.HTTPForbidden(text="cross-origin browser control is not allowed")
        return await handler(request)

    return gateway_policy
