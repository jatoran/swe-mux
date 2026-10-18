"""In-memory application-payload accounting for HTTP and WebSocket traffic.

The counters deliberately stop at the application boundary. HTTP response bytes are the
encoded body size after negotiated compression; WebSocket bytes are frame payload sizes before
per-message compression. Packet, TLS, and Tailscale overhead require a packet capture and are
not inferred here.
"""

from __future__ import annotations

import ipaddress
import json
import time
from collections import defaultdict
from dataclasses import asdict, dataclass
from typing import Any

from aiohttp import WSMsgType, hdrs, web

from . import app_keys as keys

COMPRESS_MIN_BYTES = 1024
_COMPRESSIBLE_CONTENT_TYPES = (
    "application/javascript",
    "application/json",
    "application/manifest+json",
    "application/xml",
    "image/svg+xml",
    "text/",
)


@dataclass
class HttpCounter:
    requests: int = 0
    request_bytes: int = 0
    response_bytes: int = 0
    compressed_responses: int = 0
    unknown_request_bodies: int = 0
    unknown_response_bodies: int = 0

    def add(self, other: HttpCounter) -> None:
        for name in asdict(self):
            setattr(self, name, getattr(self, name) + getattr(other, name))


@dataclass
class WebSocketCounter:
    connections: int = 0
    active_connections: int = 0
    received_frames: int = 0
    received_bytes: int = 0
    sent_frames: int = 0
    sent_bytes: int = 0

    def add(self, other: WebSocketCounter) -> None:
        for name in asdict(self):
            setattr(self, name, getattr(self, name) + getattr(other, name))


@dataclass
class WebSocketPayloadCounter:
    frames: int = 0
    bytes: int = 0


def request_peer(request: web.Request) -> str:
    """Return a bounded peer identity, honoring a local reverse proxy's forwarded peer."""

    direct = request.remote or "unknown"
    try:
        direct_ip = ipaddress.ip_address(direct)
    except ValueError:
        return direct[:80]
    if direct_ip.is_loopback:
        forwarded = request.headers.get("X-Forwarded-For", "").split(",", 1)[0].strip()
        if forwarded:
            try:
                return str(ipaddress.ip_address(forwarded))
            except ValueError:
                pass
    return str(direct_ip)


def request_route(request: web.Request) -> str:
    """Use the router template rather than user/session ids as the counter key."""

    resource = getattr(request.match_info.route, "resource", None)
    canonical = getattr(resource, "canonical", None)
    return str(canonical or request.path)[:240]


def _known_empty_response(request: web.Request, response: web.StreamResponse) -> bool:
    return (
        request.method == "HEAD"
        or response.status in {101, 204, 304}
        or 100 <= response.status < 200
    )


#: How many other processes' reports are kept: one per terminal gateway process this
#: daemon has heard from, which is one at a time and a few across restarts.
MAX_REMOTE_SOURCES = 8
#: How a process's routes and channels are told apart from the daemon's own.
REMOTE_LABEL = "gateway"


def _count(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


def _counter(cls: type[Any], row: Any) -> Any:
    """A counter from one report row, its unknown or malformed fields read as zero."""
    values = row if isinstance(row, dict) else {}
    return cls(**{name: _count(values.get(name)) for name in asdict(cls())})


def _delta(cls: type[Any], latest: Any, base: Any) -> Any:
    """What a cumulative counter gained since ``base``. A gauge (active connections)
    is the latest reading, not a difference."""
    now, before = _counter(cls, latest), _counter(cls, base)
    for name in asdict(now):
        if name != "active_connections":
            setattr(now, name, max(0, getattr(now, name) - getattr(before, name)))
    return now


def _rows(report: Any, key: str) -> list[dict[str, Any]]:
    rows = report.get(key) if isinstance(report, dict) else None
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _find(report: Any, key: str, **match: str) -> dict[str, Any] | None:
    """The row of ``report[key]`` whose fields equal ``match``, or None."""
    for row in _rows(report, key):
        if all(str(row.get(name)) == value for name, value in match.items()):
            return row
    return None


class NetworkUsage:
    """Daemon-boot traffic counters grouped by peer, route, and socket channel.

    Traffic another of swe-mux's processes carried for the same browsers - the
    terminal gateway's terminal sockets - is folded in from the cumulative reports it
    sends (`merge_remote`), so the window counts every byte a browser exchanged with
    swe-mux, and that process's routes and channels carry its name.
    """

    def __init__(self) -> None:
        self._remote: dict[str, dict[str, Any]] = {}
        self._remote_baselines: dict[str, dict[str, Any] | None] = {}
        self.reset()

    def reset(self) -> None:
        self.started_at = time.time()
        self._http_routes: dict[tuple[str, str], HttpCounter] = defaultdict(HttpCounter)
        self._http_peers: dict[str, HttpCounter] = defaultdict(HttpCounter)
        self._ws_channels: dict[str, WebSocketCounter] = defaultdict(WebSocketCounter)
        self._ws_peers: dict[str, WebSocketCounter] = defaultdict(WebSocketCounter)
        self._ws_sent_payloads: dict[
            tuple[str, str, str], WebSocketPayloadCounter
        ] = defaultdict(WebSocketPayloadCounter)
        # A report is cumulative over its process's life, so a fresh window counts
        # only what each process carries from here on.
        self._remote_baselines = dict(self._remote)

    def merge_remote(self, header: dict[str, Any]) -> None:
        """Take another process's latest cumulative report (`terminal_gateway`'s
        `metering`). Each process is one source, named by its `gateway_id`."""
        source = header.get("gateway_id")
        usage = header.get("usage")
        if not isinstance(source, str) or not source or not isinstance(usage, dict):
            return
        if source not in self._remote:
            started = usage.get("started_at")
            # A process counting since before this window opened carried some of it
            # outside the window: its first report here is where this window starts.
            began_earlier = isinstance(started, (int, float)) and started < self.started_at
            self._remote_baselines[source] = usage if began_earlier else None
        self._remote.pop(source, None)
        self._remote[source] = usage
        while len(self._remote) > MAX_REMOTE_SOURCES:
            oldest = next(iter(self._remote))
            self._remote.pop(oldest)
            self._remote_baselines.pop(oldest, None)

    def _fold_remote(
        self,
        http_routes: dict[tuple[str, str], HttpCounter],
        http_peers: dict[str, HttpCounter],
        ws_channels: dict[str, WebSocketCounter],
        ws_peers: dict[str, WebSocketCounter],
        ws_sent: dict[tuple[str, str, str], WebSocketPayloadCounter],
    ) -> None:
        for source, report in self._remote.items():
            base = self._remote_baselines.get(source)
            for row in _rows(report, "http_routes"):
                method, route = str(row.get("method")), str(row.get("route"))
                before = _find(base, "http_routes", method=method, route=route)
                http_routes[(method, f"{route} ({REMOTE_LABEL})")].add(
                    _delta(HttpCounter, row, before)
                )
            for row in _rows(report, "peers"):
                peer = str(row.get("peer"))
                before = _find(base, "peers", peer=peer) or {}
                http_peers[peer].add(_delta(HttpCounter, row.get("http"), before.get("http")))
                ws_peers[peer].add(
                    _delta(WebSocketCounter, row.get("websocket"), before.get("websocket"))
                )
            for row in _rows(report, "websocket_channels"):
                channel = str(row.get("channel"))
                before = _find(base, "websocket_channels", channel=channel)
                ws_channels[f"{channel} ({REMOTE_LABEL})"].add(
                    _delta(WebSocketCounter, row, before)
                )
            for row in _rows(report, "websocket_sent_payloads"):
                peer, channel, kind = (
                    str(row.get("peer")),
                    str(row.get("channel")),
                    str(row.get("kind")),
                )
                before = _find(
                    base, "websocket_sent_payloads", peer=peer, channel=channel, kind=kind
                )
                gained = _delta(WebSocketPayloadCounter, row, before)
                counter = ws_sent[(peer, f"{channel} ({REMOTE_LABEL})", kind)]
                counter.frames += gained.frames
                counter.bytes += gained.bytes

    def record_http(self, request: web.Request, response: web.StreamResponse) -> None:
        request_length = request.content_length
        response_length_header = response.headers.get(hdrs.CONTENT_LENGTH)
        response_length = (
            int(response_length_header)
            if response_length_header and response_length_header.isdigit()
            else 0
        )
        counter = HttpCounter(
            requests=1,
            request_bytes=max(0, request_length or 0),
            response_bytes=response_length,
            compressed_responses=int(hdrs.CONTENT_ENCODING in response.headers),
            unknown_request_bodies=int(request.can_read_body and request_length is None),
            unknown_response_bodies=int(
                response_length_header is None and not _known_empty_response(request, response)
            ),
        )
        self._http_routes[(request.method, request_route(request))].add(counter)
        self._http_peers[request_peer(request)].add(counter)

    def websocket_opened(self, peer: str, channel: str) -> None:
        for counter in (self._ws_channels[channel], self._ws_peers[peer]):
            counter.connections += 1
            counter.active_connections += 1

    def websocket_closed(self, peer: str, channel: str) -> None:
        for counter in (self._ws_channels[channel], self._ws_peers[peer]):
            counter.active_connections = max(0, counter.active_connections - 1)

    def websocket_frame(self, peer: str, channel: str, direction: str, size: int) -> None:
        for counter in (self._ws_channels[channel], self._ws_peers[peer]):
            if direction == "received":
                counter.received_frames += 1
                counter.received_bytes += max(0, size)
            else:
                counter.sent_frames += 1
                counter.sent_bytes += max(0, size)

    def websocket_sent_payload(
        self, peer: str, channel: str, kind: str, size: int
    ) -> None:
        """Classify a sent frame without adding it to aggregate traffic twice."""

        counter = self._ws_sent_payloads[(peer, channel, kind)]
        counter.frames += 1
        counter.bytes += max(0, size)

    def snapshot(self) -> dict[str, Any]:
        # Copies, so another process's traffic is folded into the answer and never
        # into this process's own counters.
        http_routes: dict[tuple[str, str], HttpCounter] = defaultdict(HttpCounter)
        http_peers: dict[str, HttpCounter] = defaultdict(HttpCounter)
        ws_channels: dict[str, WebSocketCounter] = defaultdict(WebSocketCounter)
        ws_peers: dict[str, WebSocketCounter] = defaultdict(WebSocketCounter)
        ws_sent: dict[tuple[str, str, str], WebSocketPayloadCounter] = defaultdict(
            WebSocketPayloadCounter
        )
        for route_key, http_counter in self._http_routes.items():
            http_routes[route_key].add(http_counter)
        for peer, http_counter in self._http_peers.items():
            http_peers[peer].add(http_counter)
        for channel, websocket_counter in self._ws_channels.items():
            ws_channels[channel].add(websocket_counter)
        for peer, websocket_counter in self._ws_peers.items():
            ws_peers[peer].add(websocket_counter)
        for payload_key, payload_counter in self._ws_sent_payloads.items():
            ws_sent[payload_key].frames += payload_counter.frames
            ws_sent[payload_key].bytes += payload_counter.bytes
        self._fold_remote(http_routes, http_peers, ws_channels, ws_peers, ws_sent)
        http_total = HttpCounter()
        for http_counter in http_routes.values():
            http_total.add(http_counter)
        websocket_total = WebSocketCounter()
        for websocket_counter in ws_channels.values():
            websocket_total.add(websocket_counter)
        peers = sorted(set(http_peers) | set(ws_peers))
        return {
            "started_at": self.started_at,
            "uptime_seconds": round(max(0.0, time.time() - self.started_at), 3),
            "measurement": {
                "http": "encoded_body_bytes_excluding_headers_tls_and_transport",
                "websocket": "frame_payload_bytes_before_permessage_compression",
            },
            "totals": {
                "http": asdict(http_total),
                "websocket": asdict(websocket_total),
            },
            "peers": [
                {
                    "peer": peer,
                    "http": asdict(http_peers.get(peer, HttpCounter())),
                    "websocket": asdict(ws_peers.get(peer, WebSocketCounter())),
                }
                for peer in peers
            ],
            "http_routes": [
                {"method": method, "route": route, **asdict(counter)}
                for (method, route), counter in sorted(http_routes.items())
            ],
            "websocket_channels": [
                {"channel": channel, **asdict(counter)}
                for channel, counter in sorted(ws_channels.items())
            ],
            "websocket_sent_payloads": [
                {
                    "peer": peer,
                    "channel": channel,
                    "kind": kind,
                    **asdict(counter),
                }
                for (peer, channel, kind), counter in sorted(ws_sent.items())
            ],
        }


class MeteredWebSocketResponse(web.WebSocketResponse):
    """WebSocket response that accounts application frame payloads without altering them."""

    def __init__(
        self,
        *args: Any,
        meter: NetworkUsage | None,
        peer: str,
        channel: str,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._network_meter = meter
        self._network_peer = peer
        self._network_channel = channel
        self._network_open_recorded = False
        self._network_close_recorded = False

    async def prepare(self, request: web.BaseRequest) -> Any:
        writer = await super().prepare(request)
        if self._network_meter is not None and not self._network_open_recorded:
            self._network_open_recorded = True
            self._network_meter.websocket_opened(self._network_peer, self._network_channel)
        return writer

    async def send_str(self, data: str, compress: int | None = None) -> None:
        if self._network_meter is not None:
            self._network_meter.websocket_frame(
                self._network_peer, self._network_channel, "sent", len(data.encode("utf-8"))
            )
        await super().send_str(data, compress=compress)

    async def send_bytes(self, data: bytes, compress: int | None = None) -> None:
        if self._network_meter is not None:
            self._network_meter.websocket_frame(
                self._network_peer, self._network_channel, "sent", len(data)
            )
        await super().send_bytes(data, compress=compress)

    async def send_bytes_classified(
        self, data: bytes, kind: str, compress: int | None = None
    ) -> None:
        """Send and meter one binary frame with a bounded caller-owned payload kind."""

        if self._network_meter is not None:
            size = len(data)
            self._network_meter.websocket_frame(
                self._network_peer, self._network_channel, "sent", size
            )
            self._network_meter.websocket_sent_payload(
                self._network_peer, self._network_channel, kind, size
            )
        await super().send_bytes(data, compress=compress)

    async def receive(self, timeout: float | None = None) -> Any:  # noqa: ASYNC109
        message = await super().receive(timeout=timeout)
        if self._network_meter is not None:
            if message.type == WSMsgType.TEXT:
                self._network_meter.websocket_frame(
                    self._network_peer,
                    self._network_channel,
                    "received",
                    len(message.data.encode("utf-8")),
                )
            elif message.type == WSMsgType.BINARY:
                self._network_meter.websocket_frame(
                    self._network_peer,
                    self._network_channel,
                    "received",
                    len(message.data),
                )
        return message

    async def close(self, *args: Any, **kwargs: Any) -> bool:
        try:
            return await super().close(*args, **kwargs)
        finally:
            if (
                self._network_meter is not None
                and self._network_open_recorded
                and not self._network_close_recorded
            ):
                self._network_close_recorded = True
                self._network_meter.websocket_closed(
                    self._network_peer, self._network_channel
                )


def _is_loopback(peer: str) -> bool:
    try:
        return ipaddress.ip_address(peer).is_loopback
    except ValueError:
        return False


def metered_websocket(
    request: web.Request, channel: str, **kwargs: Any
) -> MeteredWebSocketResponse:
    """A metered WebSocket response, with per-message compression only off-host.

    Every browser offers permessage-deflate and aiohttp accepts it by default. For a
    peer on this machine the bytes it saves never cross a network, and the daemon
    pays for them: frames of 16 KiB or less are deflated on the event loop itself,
    larger ones on an executor thread, and every connection keeps a deflate window.
    So a loopback peer is not offered it. The peer is the forwarded one
    (`request_peer`): the phone reaches the loopback listener through Tailscale
    Serve, which names it in `X-Forwarded-For`, and it keeps compression. A caller
    that passes ``compress`` decides for itself.
    """
    meter = request.app.get(keys.NETWORK_USAGE)
    peer = request_peer(request)
    kwargs.setdefault("compress", not _is_loopback(peer))
    return MeteredWebSocketResponse(
        meter=meter if isinstance(meter, NetworkUsage) else None,
        peer=peer,
        channel=channel,
        **kwargs,
    )


@web.middleware
async def compressible_response_middleware(
    request: web.Request, handler: Any
) -> web.StreamResponse:
    """Negotiate compression for non-streamed dynamic text bodies of meaningful size."""

    response = await handler(request)
    if not isinstance(response, web.Response) or isinstance(response, web.FileResponse):
        return response
    if response.headers.get(hdrs.CONTENT_ENCODING) or request.method == "HEAD":
        return response
    body = response.body
    content_type = response.content_type.casefold()
    if (
        isinstance(body, (bytes, bytearray))
        and len(body) >= COMPRESS_MIN_BYTES
        and any(content_type.startswith(prefix) for prefix in _COMPRESSIBLE_CONTENT_TYPES)
    ):
        response.enable_compression()
    return response


async def record_network_response(
    request: web.Request, response: web.StreamResponse
) -> None:
    if request_route(request) == "/api/diagnostics/network":
        return
    meter = request.app.get(keys.NETWORK_USAGE)
    if isinstance(meter, NetworkUsage):
        meter.record_http(request, response)


def compact_json_bytes(data: Any) -> bytes:
    """The exact octets `compact_json_response` would send.

    Named so a handler that has to *fingerprint* what it is about to serve - a
    conditional request's `ETag` - derives the tag from the same bytes rather than from
    a second serialization that might disagree with them.
    """
    return json.dumps(data, separators=(",", ":")).encode("utf-8")


def compact_json_response(data: Any, status: int = 200) -> web.Response:
    """JSON response without insignificant spaces; compression is negotiated later."""

    return web.json_response(
        data,
        status=status,
        dumps=lambda value: json.dumps(value, separators=(",", ":")),
    )
