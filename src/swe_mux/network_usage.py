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


class NetworkUsage:
    """Daemon-boot traffic counters grouped by peer, route, and socket channel."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.started_at = time.time()
        self._http_routes: dict[tuple[str, str], HttpCounter] = defaultdict(HttpCounter)
        self._http_peers: dict[str, HttpCounter] = defaultdict(HttpCounter)
        self._ws_channels: dict[str, WebSocketCounter] = defaultdict(WebSocketCounter)
        self._ws_peers: dict[str, WebSocketCounter] = defaultdict(WebSocketCounter)

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

    def snapshot(self) -> dict[str, Any]:
        http_total = HttpCounter()
        for http_counter in self._http_routes.values():
            http_total.add(http_counter)
        websocket_total = WebSocketCounter()
        for websocket_counter in self._ws_channels.values():
            websocket_total.add(websocket_counter)
        peers = sorted(set(self._http_peers) | set(self._ws_peers))
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
                    "http": asdict(self._http_peers.get(peer, HttpCounter())),
                    "websocket": asdict(self._ws_peers.get(peer, WebSocketCounter())),
                }
                for peer in peers
            ],
            "http_routes": [
                {"method": method, "route": route, **asdict(counter)}
                for (method, route), counter in sorted(self._http_routes.items())
            ],
            "websocket_channels": [
                {"channel": channel, **asdict(counter)}
                for channel, counter in sorted(self._ws_channels.items())
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


def metered_websocket(
    request: web.Request, channel: str, **kwargs: Any
) -> MeteredWebSocketResponse:
    meter = request.app.get("network_usage")
    return MeteredWebSocketResponse(
        meter=meter if isinstance(meter, NetworkUsage) else None,
        peer=request_peer(request),
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
    meter = request.app.get("network_usage")
    if isinstance(meter, NetworkUsage):
        meter.record_http(request, response)


def compact_json_response(data: Any, status: int = 200) -> web.Response:
    """JSON response without insignificant spaces; compression is negotiated later."""

    return web.json_response(
        data,
        status=status,
        dumps=lambda value: json.dumps(value, separators=(",", ":")),
    )
