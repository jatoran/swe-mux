"""Framed, token-authenticated IPC between swe-mux's own processes.

`development/DAEMON_LOAD_ISOLATION.md` item 18. The worker process and the
terminal gateway both need to talk to the daemon, and the gateway to the PTY
supervisor, over the same shape the supervisor already uses: a loopback TCP
connection, a `hello` carrying a shared token, then length-prefixed frames. They
cannot import `supervisor.py` to get it, because that drags in the whole PTY
stack and is the hash-gated closure whose every change reaps the fleet.

**The framing is the supervisor's, byte for byte** (`supervisor.encode_frame` and
`read_frame`): a four-byte big-endian header length, a compact JSON object, and a
payload whose length the header names as `plen`. `tests/test_framed_ipc.py`
asserts byte equality against the supervisor's encoder and reads each side's
frames with the other's reader, so the two cannot drift apart silently - and the
supervisor itself is not changed to share this module, because that would be a
supervisor revision for no behaviour.

**The message conventions are the supervisor's too**, so one peer class can
speak to a supervisor and to the new processes alike:

- a request carries `t` (its type) and `id`;
- a reply carries the `id` it answers and `ok`, with `error` when `ok` is false;
- anything without an `id` is an unsolicited notice.

Ids are per direction, and `ok` is what tells a reply from a request, so both
ends may issue requests on one connection without their ids colliding. `id` 0
belongs to `hello`.

**Frames stay small.** The receiving loop decodes every header as JSON on its
event loop, so bulk bytes belong in the payload, which is never parsed. A header
is capped at `MAX_HEADER_BYTES` (1 MiB) and a payload at `MAX_PAYLOAD_BYTES`
once authenticated; before `hello` a connection gets `HELLO_MAX_HEADER_BYTES`,
no payload allowance at all, and `AUTH_DEADLINE_SECONDS` to speak - the bounds
the supervisor learned it needed (its F13), applied from the start here.

**Nothing on this path waits on the peer it serves.** Requests from the peer run
as tasks, started in arrival order, so a slow handler never holds up the replies
this side is waiting for; past `MAX_INFLIGHT_REQUESTS` a request is answered
`busy` instead of queued without bound. Notices and reply hooks run inside the
read loop and must not await. A peer that stops reading is dropped once its
unwritten bytes pass `WRITE_BUFFER_LIMIT`, rather than costing this process
memory without bound.

Standard library only, and a test holds it to that: the shim-like processes that
import it must not pay for the daemon's imports.
"""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import inspect
import itertools
import json
import logging
import secrets
import struct
import time
from collections.abc import Awaitable, Callable, Iterable, Mapping
from typing import Any

log = logging.getLogger(__name__)

#: Header cap for an authenticated peer. Headers are decoded as JSON on the
#: receiving loop, so they are for small structured fields only.
MAX_HEADER_BYTES = 1024 * 1024
#: Payload cap for an authenticated peer: far above anything legitimate (one
#: OTLP body, one scrollback replay) and finite, which is the point.
MAX_PAYLOAD_BYTES = 64 * 1024 * 1024
#: Before `hello`, a connection has proven nothing and gets only this much header.
HELLO_MAX_HEADER_BYTES = 64 * 1024
#: ...and this long to send it, so it cannot hold a connection open by silence.
AUTH_DEADLINE_SECONDS = 15.0
CONNECT_TIMEOUT_SECONDS = 10.0
REQUEST_TIMEOUT_SECONDS = 30.0
#: Requests from the peer being handled at once; one more is answered `busy`.
MAX_INFLIGHT_REQUESTS = 64
#: Unwritten bytes queued for a peer past which it is dropped as not reading.
WRITE_BUFFER_LIMIT = 16 * 1024 * 1024
#: How long closing waits for the transport to finish before giving up on it.
CLOSE_WAIT_SECONDS = 2.0

HELLO = "hello"
ERROR_BUSY = "busy"

_HEADER_LENGTH = struct.Struct(">I")


class FramedIpcError(Exception):
    """Base class for everything this module raises."""


class HandshakeRefused(FramedIpcError):
    """The `hello` was refused, by either side; `reason` says why."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class PeerClosed(FramedIpcError):
    """The connection is closed; `reason` is why it closed."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class RemoteError(FramedIpcError):
    """A request was answered with `ok: false`."""

    def __init__(self, error: str, header: Mapping[str, Any]) -> None:
        super().__init__(error)
        self.error = error
        self.header = dict(header)


class RequestRefused(FramedIpcError):
    """Raised by a request handler to answer `ok: false` with this message.

    A deliberate refusal, so it is not logged with a traceback; any other
    exception from a handler is answered the same way and logged as a bug.
    """


def new_token() -> str:
    """A fresh shared secret for one process's `hello`."""
    return secrets.token_urlsafe(32)


def encode_frame(header: Mapping[str, Any], payload: bytes = b"") -> bytes:
    """One frame, byte-identical to `supervisor.encode_frame`."""
    if payload:
        header = {**header, "plen": len(payload)}
    raw = json.dumps(header, separators=(",", ":")).encode("utf-8")
    return _HEADER_LENGTH.pack(len(raw)) + raw + payload


async def read_frame(
    reader: asyncio.StreamReader,
    *,
    max_header_bytes: int = MAX_HEADER_BYTES,
    max_payload_bytes: int | None = MAX_PAYLOAD_BYTES,
) -> tuple[dict[str, Any], bytes]:
    """Read one frame, refusing a length before honouring it.

    The same contract as `supervisor.read_frame`, except that the payload is
    bounded by default: every peer of this module is one of swe-mux's own
    processes, so no direction here needs the supervisor's unbounded
    client-inbound case. `None` still means unbounded for a caller that does.
    """
    header, payload, _ = await _read_frame(
        reader, max_header_bytes=max_header_bytes, max_payload_bytes=max_payload_bytes
    )
    return header, payload


async def _read_frame(
    reader: asyncio.StreamReader, *, max_header_bytes: int, max_payload_bytes: int | None
) -> tuple[dict[str, Any], bytes, int]:
    """`read_frame`, plus the frame's size on the wire for the byte counters."""
    (size,) = _HEADER_LENGTH.unpack(await reader.readexactly(_HEADER_LENGTH.size))
    if size > max_header_bytes:
        raise ValueError(f"oversized frame header: {size} bytes (cap {max_header_bytes})")
    header = json.loads(await reader.readexactly(size))
    if not isinstance(header, dict):
        raise ValueError("frame header must be a JSON object")
    raw_plen = header.get("plen", 0)
    try:
        plen = int(raw_plen)
    except (TypeError, ValueError):
        raise ValueError(f"frame payload length is not a number: {raw_plen!r}") from None
    if plen < 0:
        raise ValueError(f"negative frame payload length: {plen}")
    if max_payload_bytes is not None and plen > max_payload_bytes:
        raise ValueError(f"oversized frame payload: {plen} bytes (cap {max_payload_bytes})")
    payload = await reader.readexactly(plen) if plen else b""
    return header, payload, _HEADER_LENGTH.size + size + plen


RequestHandler = Callable[
    ["FramedPeer", dict[str, Any], bytes],
    Awaitable["tuple[Mapping[str, Any], bytes] | Mapping[str, Any] | None"],
]
NoticeHandler = Callable[["FramedPeer", dict[str, Any], bytes], None]
CloseHandler = Callable[["FramedPeer", str], None]
ReplyHook = Callable[[dict[str, Any], bytes], None]

_PEER_NUMBERS = itertools.count(1)


def _address(writer: asyncio.StreamWriter) -> str:
    peer = writer.get_extra_info("peername")
    return f"{peer[0]}:{peer[1]}" if isinstance(peer, tuple) else str(peer)


class FramedPeer:
    """One authenticated connection, from either end.

    Built by `connect` on the client side and by `FramedServer` on the server
    side, after `hello` has succeeded; `start()` begins reading. `capabilities`
    is what both ends announced, and `remote_hello` is the other end's half of
    the handshake (the reply for a client, the request for a server).
    """

    def __init__(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        *,
        name: str,
        remote_hello: Mapping[str, Any],
        capabilities: Iterable[str] = (),
        on_request: RequestHandler | None = None,
        on_notice: NoticeHandler | None = None,
        on_close: CloseHandler | None = None,
        max_header_bytes: int = MAX_HEADER_BYTES,
        max_payload_bytes: int = MAX_PAYLOAD_BYTES,
        max_inflight: int = MAX_INFLIGHT_REQUESTS,
        write_buffer_limit: int = WRITE_BUFFER_LIMIT,
    ) -> None:
        self.name = f"{name}#{next(_PEER_NUMBERS)}"
        self.remote_hello = dict(remote_hello)
        self.capabilities = frozenset(capabilities)
        self._reader = reader
        self._writer = writer
        self._on_request = on_request
        self._on_notice = on_notice
        self._on_close = on_close
        self._max_header_bytes = max_header_bytes
        self._max_payload_bytes = max_payload_bytes
        self._max_inflight = max_inflight
        self._write_buffer_limit = write_buffer_limit
        self._next_id = 1
        self._pending: dict[int, asyncio.Future[tuple[dict[str, Any], bytes]]] = {}
        self._reply_hooks: dict[int, ReplyHook] = {}
        self._handlers: set[asyncio.Task[None]] = set()
        self._read_task: asyncio.Task[None] | None = None
        self._closed = asyncio.Event()
        self.close_reason = ""
        self.opened_at = time.monotonic()
        self.requests_sent = 0
        self.requests_handled = 0
        self.requests_refused_busy = 0
        self.notices_sent = 0
        self.notices_received = 0
        self.bytes_sent = 0
        self.bytes_received = 0
        self.peer_address = _address(writer)

    # -- lifecycle --------------------------------------------------------------

    @property
    def closed(self) -> bool:
        return self._closed.is_set()

    def start(self) -> None:
        if self._read_task is None and not self.closed:
            self._read_task = asyncio.create_task(self._read_loop(), name=f"framed-ipc-{self.name}")

    async def wait_closed(self) -> None:
        await self._closed.wait()

    async def close(self, reason: str = "closed") -> None:
        """Close the connection; idempotent, and safe from inside a handler."""
        if self.closed:
            return
        self._mark_closed(reason)
        current = asyncio.current_task()
        tasks = [task for task in self._handlers if task is not current]
        if self._read_task is not None and self._read_task is not current:
            tasks.append(self._read_task)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        with contextlib.suppress(Exception):
            await asyncio.wait_for(self._writer.wait_closed(), timeout=CLOSE_WAIT_SECONDS)

    def _mark_closed(self, reason: str) -> None:
        """The synchronous half of closing: everything a caller can observe."""
        if self.closed:
            return
        self.close_reason = reason
        self._closed.set()
        for future in self._pending.values():
            if not future.done():
                future.set_exception(PeerClosed(reason))
        self._pending.clear()
        self._reply_hooks.clear()
        with contextlib.suppress(Exception):
            self._writer.close()
        log.info(
            "framed ipc connection %s to %s closed: %s",
            self.name,
            self.peer_address,
            reason,
            extra={"ipc_peer": self.name, "ipc_close_reason": reason, **self._counters()},
        )
        if self._on_close is not None:
            try:
                self._on_close(self, reason)
            except Exception:
                log.exception("framed ipc close handler for %s failed", self.name)

    def _abandon(self, reason: str) -> None:
        """Close from inside the read loop or a callback, which cannot await."""
        if self.closed:
            return
        self._mark_closed(reason)
        current = asyncio.current_task()
        for task in self._handlers:
            if task is not current:
                task.cancel()
        if self._read_task is not None and self._read_task is not current:
            self._read_task.cancel()

    # -- sending ----------------------------------------------------------------

    def _write(self, frame: bytes) -> None:
        if self.closed:
            raise PeerClosed(self.close_reason)
        try:
            self._writer.write(frame)
        except (ConnectionError, RuntimeError, OSError) as exc:
            self._abandon(f"write failed ({type(exc).__name__}: {exc})")
            raise PeerClosed(self.close_reason) from exc
        self.bytes_sent += len(frame)
        transport = self._writer.transport
        buffered = transport.get_write_buffer_size() if transport is not None else 0
        if buffered > self._write_buffer_limit:
            # A peer that stopped reading. Dropping it bounds what it costs this
            # process; its own reconnect logic is what brings it back.
            log.warning(
                "framed ipc peer %s stopped reading: %s bytes unwritten (limit %s)",
                self.name,
                buffered,
                self._write_buffer_limit,
                extra={"ipc_peer": self.name, "ipc_unwritten_bytes": buffered},
            )
            self._abandon(f"peer not reading ({buffered} bytes unwritten)")

    def notify(self, header: Mapping[str, Any], payload: bytes = b"") -> None:
        """Send an unsolicited frame. Raises `PeerClosed` once closed."""
        if "id" in header:
            raise ValueError("a notice carries no id; use request() for a reply")
        self._write(encode_frame(header, payload))
        self.notices_sent += 1

    async def drain(self) -> None:
        """Wait until the transport's buffer is below its high-water mark."""
        if self.closed:
            raise PeerClosed(self.close_reason)
        try:
            await self._writer.drain()
        except (ConnectionError, RuntimeError, OSError) as exc:
            self._abandon(f"drain failed ({type(exc).__name__}: {exc})")
            raise PeerClosed(self.close_reason) from exc

    async def request(
        self,
        header: Mapping[str, Any],
        payload: bytes = b"",
        *,
        timeout_seconds: float = REQUEST_TIMEOUT_SECONDS,
        on_reply: ReplyHook | None = None,
    ) -> tuple[dict[str, Any], bytes]:
        """Send one request and wait for its reply.

        Raises `RemoteError` for `ok: false`, `PeerClosed` if the connection
        ends first, and `TimeoutError` after `timeout_seconds`, which covers waiting
        for the transport to take the request as well as for the reply. A
        timed-out request is forgotten: a reply that arrives later is dropped.
        `on_reply` runs inside
        the read loop the moment the reply is read, before the next frame is:
        the caller resumes a loop turn later, by which time frames that follow
        the reply may already have been dispatched.
        """
        if "t" not in header:
            raise ValueError("a request needs a type (`t`)")
        if self.closed:
            raise PeerClosed(self.close_reason)
        request_id = self._next_id
        self._next_id += 1
        future: asyncio.Future[tuple[dict[str, Any], bytes]] = (
            asyncio.get_running_loop().create_future()
        )
        self._pending[request_id] = future
        if on_reply is not None:
            self._reply_hooks[request_id] = on_reply
        try:
            async with asyncio.timeout(timeout_seconds):
                self._write(encode_frame({**header, "id": request_id}, payload))
                self.requests_sent += 1
                await self.drain()
                reply, reply_payload = await future
        finally:
            self._pending.pop(request_id, None)
            self._reply_hooks.pop(request_id, None)
        if not reply.get("ok"):
            raise RemoteError(str(reply.get("error") or "request failed"), reply)
        return reply, reply_payload

    # -- receiving --------------------------------------------------------------

    async def _read_loop(self) -> None:
        try:
            while True:  # unsupervised-loop-ok: one connection's reader; a fault closes the peer
                header, payload, size = await _read_frame(
                    self._reader,
                    max_header_bytes=self._max_header_bytes,
                    max_payload_bytes=self._max_payload_bytes,
                )
                self.bytes_received += size
                self._dispatch(header, payload)
                if self.closed:
                    return
        except asyncio.CancelledError:
            raise
        except ValueError as exc:
            # A frame that does not parse is a protocol fault, not a transport
            # one, and it says something different to whoever reads the log.
            log.warning(
                "framed ipc protocol error from %s: %s",
                self.name,
                exc,
                extra={"ipc_peer": self.name},
            )
            self._abandon(f"protocol error: {exc}")
        except (asyncio.IncompleteReadError, ConnectionError, OSError) as exc:
            self._abandon(f"connection lost ({type(exc).__name__})")

    def _dispatch(self, header: dict[str, Any], payload: bytes) -> None:
        """Route one frame. Must never await: replies share this loop with everything."""
        request_id = header.get("id")
        if "ok" in header:
            self._dispatch_reply(request_id, header, payload)
            return
        if request_id is not None:
            self._dispatch_request(request_id, header, payload)
            return
        self.notices_received += 1
        if self._on_notice is None:
            return
        try:
            self._on_notice(self, header, payload)
        except Exception:
            log.exception(
                "framed ipc notice handler for %s failed on %r", self.name, header.get("t")
            )

    def _dispatch_reply(self, request_id: Any, header: dict[str, Any], payload: bytes) -> None:
        if not isinstance(request_id, int) or isinstance(request_id, bool):
            log.warning("framed ipc reply from %s names no request: %r", self.name, request_id)
            return
        hook = self._reply_hooks.pop(request_id, None)
        if hook is not None:
            try:
                hook(header, payload)
            except Exception:
                log.exception(
                    "framed ipc reply hook for %s request %s failed", self.name, request_id
                )
        future = self._pending.get(request_id)
        if future is not None and not future.done():
            future.set_result((header, payload))

    def _dispatch_request(self, request_id: Any, header: dict[str, Any], payload: bytes) -> None:
        kind = header.get("t")
        if self._on_request is None:
            self._reply_error(request_id, f"unknown message type: {kind!r}")
            return
        if len(self._handlers) >= self._max_inflight:
            self.requests_refused_busy += 1
            if self.requests_refused_busy == 1 or self.requests_refused_busy % 100 == 0:
                log.warning(
                    "framed ipc peer %s has %s requests in flight; answering busy (%s so far)",
                    self.name,
                    len(self._handlers),
                    self.requests_refused_busy,
                    extra={"ipc_peer": self.name, "ipc_busy": self.requests_refused_busy},
                )
            self._reply_error(request_id, ERROR_BUSY)
            return
        # Created now, so handlers start in the order their requests arrived:
        # whatever a handler does before its first await happens in that order.
        task = asyncio.create_task(
            self._handle(request_id, header, payload), name=f"framed-ipc-{self.name}-{kind}"
        )
        self._handlers.add(task)
        task.add_done_callback(self._handlers.discard)

    async def _handle(self, request_id: Any, header: dict[str, Any], payload: bytes) -> None:
        assert self._on_request is not None
        try:
            result = self._on_request(self, header, payload)
            outcome = await result if inspect.isawaitable(result) else result
        except asyncio.CancelledError:
            raise
        except RequestRefused as exc:
            self._reply_error(request_id, str(exc) or "refused")
            return
        except Exception as exc:
            log.exception(
                "framed ipc request handler for %s failed on %r", self.name, header.get("t")
            )
            self._reply_error(request_id, f"{type(exc).__name__}: {exc}")
            return
        self.requests_handled += 1
        fields: Mapping[str, Any]
        reply_payload = b""
        if outcome is None:
            fields = {}
        elif isinstance(outcome, tuple):
            fields, reply_payload = outcome
        else:
            fields = outcome
        with contextlib.suppress(PeerClosed):
            self._write(encode_frame({**fields, "id": request_id, "ok": True}, reply_payload))

    def _reply_error(self, request_id: Any, error: str) -> None:
        with contextlib.suppress(PeerClosed):
            self._write(encode_frame({"id": request_id, "ok": False, "error": error}))

    # -- diagnostics ------------------------------------------------------------

    def _counters(self) -> dict[str, Any]:
        return {
            "ipc_requests_sent": self.requests_sent,
            "ipc_requests_handled": self.requests_handled,
            "ipc_requests_busy": self.requests_refused_busy,
            "ipc_notices_sent": self.notices_sent,
            "ipc_notices_received": self.notices_received,
            "ipc_bytes_sent": self.bytes_sent,
            "ipc_bytes_received": self.bytes_received,
        }

    def stats(self) -> dict[str, Any]:
        """What a diagnostics endpoint shows about this connection."""
        transport = self._writer.transport
        return {
            "name": self.name,
            "peer": self.peer_address,
            "closed": self.closed,
            "close_reason": self.close_reason,
            "age_seconds": round(time.monotonic() - self.opened_at, 3),
            "capabilities": sorted(self.capabilities),
            "pending_requests": len(self._pending),
            "inflight_handlers": len(self._handlers),
            "unwritten_bytes": transport.get_write_buffer_size() if transport is not None else 0,
            "requests_sent": self.requests_sent,
            "requests_handled": self.requests_handled,
            "requests_refused_busy": self.requests_refused_busy,
            "notices_sent": self.notices_sent,
            "notices_received": self.notices_received,
            "bytes_sent": self.bytes_sent,
            "bytes_received": self.bytes_received,
        }


async def connect(
    host: str,
    port: int,
    *,
    token: str,
    protocol: int,
    capabilities: Iterable[str] = (),
    hello: Mapping[str, Any] | None = None,
    name: str = "client",
    timeout_seconds: float = CONNECT_TIMEOUT_SECONDS,
    on_request: RequestHandler | None = None,
    on_notice: NoticeHandler | None = None,
    on_close: CloseHandler | None = None,
    max_header_bytes: int = MAX_HEADER_BYTES,
    max_payload_bytes: int = MAX_PAYLOAD_BYTES,
    max_inflight: int = MAX_INFLIGHT_REQUESTS,
    write_buffer_limit: int = WRITE_BUFFER_LIMIT,
) -> FramedPeer:
    """Connect, complete `hello`, and return a started peer.

    `hello` adds fields to the request (a supervisor's `scope`, say). The
    peer's `capabilities` are the ones both ends announced; the server's reply
    is kept whole as `remote_hello`. Raises `HandshakeRefused` for a refusal
    and for any failure to complete the handshake, with the cause chained.
    """
    wanted = list(dict.fromkeys(capabilities))
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=timeout_seconds
        )
    except (OSError, TimeoutError) as exc:
        raise HandshakeRefused(f"not reachable on {host}:{port} ({type(exc).__name__})") from exc
    request: dict[str, Any] = {**(hello or {}), "t": HELLO, "id": 0, "token": token}
    request["protocol"] = protocol
    request["capabilities"] = wanted
    try:
        writer.write(encode_frame(request))
        await writer.drain()
        reply, _ = await asyncio.wait_for(
            read_frame(reader, max_header_bytes=max_header_bytes, max_payload_bytes=0),
            timeout=timeout_seconds,
        )
    except (OSError, TimeoutError, ValueError, asyncio.IncompleteReadError) as exc:
        writer.close()
        raise HandshakeRefused(
            f"handshake with {host}:{port} failed ({type(exc).__name__}: {exc})"
        ) from exc
    if reply.get("id") != 0 or not reply.get("ok"):
        writer.close()
        raise HandshakeRefused(str(reply.get("error") or "handshake refused"))
    offered = reply.get("capabilities")
    agreed = set(wanted) & set(offered) if isinstance(offered, list) else set()
    peer = FramedPeer(
        reader,
        writer,
        name=name,
        remote_hello=reply,
        capabilities=agreed,
        on_request=on_request,
        on_notice=on_notice,
        on_close=on_close,
        max_header_bytes=max_header_bytes,
        max_payload_bytes=max_payload_bytes,
        max_inflight=max_inflight,
        write_buffer_limit=write_buffer_limit,
    )
    log.info(
        "framed ipc %s connected to %s:%s (protocol %s, capabilities %s)",
        peer.name,
        host,
        port,
        protocol,
        sorted(agreed) or "none",
        extra={"ipc_peer": peer.name, "ipc_protocol": protocol},
    )
    peer.start()
    return peer


HelloReply = Callable[[dict[str, Any]], Mapping[str, Any] | None]
ConnectionHandler = Callable[[FramedPeer], Awaitable[None] | None]


class FramedServer:
    """Accept authenticated peers on a loopback port.

    `hello_reply(request)` may add fields to the reply, or raise
    `HandshakeRefused` to refuse a hello that is well formed but unwelcome (a
    scope this server does not grant). `on_connection(peer)` runs before the
    peer starts reading, so whatever it registers sees the first frame.
    """

    def __init__(
        self,
        *,
        token: str,
        protocol: int,
        capabilities: Iterable[str] = (),
        name: str = "server",
        on_connection: ConnectionHandler | None = None,
        on_request: RequestHandler | None = None,
        on_notice: NoticeHandler | None = None,
        on_close: CloseHandler | None = None,
        hello_reply: HelloReply | None = None,
        max_header_bytes: int = MAX_HEADER_BYTES,
        max_payload_bytes: int = MAX_PAYLOAD_BYTES,
        max_inflight: int = MAX_INFLIGHT_REQUESTS,
        write_buffer_limit: int = WRITE_BUFFER_LIMIT,
        auth_deadline: float = AUTH_DEADLINE_SECONDS,
    ) -> None:
        if not token:
            raise ValueError("a framed ipc server needs a token")
        self._token = token.encode("utf-8")
        self.protocol = protocol
        self.capabilities = tuple(dict.fromkeys(capabilities))
        self.name = name
        self._on_connection = on_connection
        self._on_request = on_request
        self._on_notice = on_notice
        self._on_close = on_close
        self._hello_reply = hello_reply
        self._max_header_bytes = max_header_bytes
        self._max_payload_bytes = max_payload_bytes
        self._max_inflight = max_inflight
        self._write_buffer_limit = write_buffer_limit
        self._auth_deadline = auth_deadline
        self._server: asyncio.Server | None = None
        self._handshakes: set[asyncio.Task[Any]] = set()
        self.peers: set[FramedPeer] = set()
        self.port = 0
        self.refused = 0

    async def start(self, host: str = "127.0.0.1", port: int = 0) -> int:
        """Listen, returning the bound port (an OS-allocated one for port 0)."""
        self._server = await asyncio.start_server(self._accept, host, port)
        self.port = int(self._server.sockets[0].getsockname()[1])
        log.info(
            "framed ipc server %s listening on %s:%s (protocol %s)",
            self.name,
            host,
            self.port,
            self.protocol,
            extra={"ipc_server": self.name, "ipc_port": self.port},
        )
        return self.port

    async def close(self) -> None:
        """Stop listening and close every peer.

        Peers are closed before the listener is awaited: since Python 3.12.1
        `Server.wait_closed()` also waits for every connected client, so a peer
        left open would hold this open for as long as it stayed connected.
        """
        server, self._server = self._server, None
        if server is not None:
            server.close()
        for task in tuple(self._handshakes):
            task.cancel()
        if self._handshakes:
            await asyncio.gather(*self._handshakes, return_exceptions=True)
        peers = tuple(self.peers)
        if peers:
            await asyncio.gather(*(peer.close("server closing") for peer in peers))
        if server is not None:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(server.wait_closed(), timeout=CLOSE_WAIT_SECONDS)
        log.info("framed ipc server %s closed", self.name, extra={"ipc_server": self.name})

    async def _accept(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        if task is not None:
            self._handshakes.add(task)
        try:
            await self._handshake(reader, writer)
        finally:
            if task is not None:
                self._handshakes.discard(task)

    async def _handshake(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        address = _address(writer)
        request_id: Any = 0
        try:
            header, _ = await asyncio.wait_for(
                read_frame(reader, max_header_bytes=HELLO_MAX_HEADER_BYTES, max_payload_bytes=0),
                timeout=self._auth_deadline,
            )
            request_id = header.get("id", 0)
            extra = self._check_hello(header)
        except HandshakeRefused as refusal:
            await self._refuse(writer, address, request_id, refusal.reason)
            return
        except TimeoutError:
            await self._refuse(writer, address, None, "no hello before the deadline")
            return
        except (ValueError, asyncio.IncompleteReadError, ConnectionError, OSError) as exc:
            reason = f"malformed hello ({type(exc).__name__}: {exc})"
            await self._refuse(writer, address, None, reason)
            return
        except Exception:
            # A `hello_reply` that failed rather than refused: a bug on this
            # side, so the peer is told nothing more specific than that.
            log.exception(
                "framed ipc server %s failed checking a hello from %s", self.name, address
            )
            await self._refuse(writer, address, request_id, "internal error")
            return
        requested = header.get("capabilities")
        wanted = {str(item) for item in requested} if isinstance(requested, list) else set()
        agreed = wanted & set(self.capabilities)
        reply = {
            **extra,
            "id": request_id,
            "ok": True,
            "protocol": self.protocol,
            "capabilities": list(self.capabilities),
        }
        peer = FramedPeer(
            reader,
            writer,
            name=self.name,
            remote_hello=header,
            capabilities=agreed,
            on_request=self._on_request,
            on_notice=self._on_notice,
            on_close=self._forget,
            max_header_bytes=self._max_header_bytes,
            max_payload_bytes=self._max_payload_bytes,
            max_inflight=self._max_inflight,
            write_buffer_limit=self._write_buffer_limit,
        )
        self.peers.add(peer)
        try:
            peer._write(encode_frame(reply))
        except PeerClosed:
            return
        log.info(
            "framed ipc server %s accepted %s from %s (capabilities %s)",
            self.name,
            peer.name,
            address,
            sorted(agreed) or "none",
            extra={"ipc_server": self.name, "ipc_peer": peer.name},
        )
        if self._on_connection is not None:
            try:
                result = self._on_connection(peer)
                if inspect.isawaitable(result):
                    await result
            except Exception:
                log.exception("framed ipc connection handler for %s failed", peer.name)
                await peer.close("connection handler failed")
                return
        peer.start()

    def _check_hello(self, header: dict[str, Any]) -> dict[str, Any]:
        if header.get("t") != HELLO:
            raise HandshakeRefused("hello with a valid token is required first")
        given = header.get("token")
        if not isinstance(given, str):
            raise HandshakeRefused("invalid token")
        if not hmac.compare_digest(given.encode("utf-8"), self._token):
            raise HandshakeRefused("invalid token")
        if header.get("protocol") != self.protocol:
            raise HandshakeRefused(
                f"protocol mismatch: this end speaks {self.protocol}, "
                f"the peer asked for {header.get('protocol')!r}"
            )
        if self._hello_reply is None:
            return {}
        return dict(self._hello_reply(header) or {})

    async def _refuse(
        self, writer: asyncio.StreamWriter, address: str, request_id: Any, reason: str
    ) -> None:
        self.refused += 1
        log.warning(
            "framed ipc server %s refused a connection from %s: %s",
            self.name,
            address,
            reason,
            extra={"ipc_server": self.name, "ipc_refusal": reason},
        )
        with contextlib.suppress(Exception):
            writer.write(encode_frame({"id": request_id, "ok": False, "error": reason}))
            await asyncio.wait_for(writer.drain(), timeout=CLOSE_WAIT_SECONDS)
        writer.close()
        with contextlib.suppress(Exception):
            await asyncio.wait_for(writer.wait_closed(), timeout=CLOSE_WAIT_SECONDS)

    def _forget(self, peer: FramedPeer, reason: str) -> None:
        self.peers.discard(peer)
        if self._on_close is not None:
            self._on_close(peer, reason)

    def stats(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "port": self.port,
            "listening": self._server is not None,
            "refused": self.refused,
            "peers": [peer.stats() for peer in self.peers],
        }


__all__ = [
    "AUTH_DEADLINE_SECONDS",
    "ERROR_BUSY",
    "HELLO_MAX_HEADER_BYTES",
    "MAX_HEADER_BYTES",
    "MAX_INFLIGHT_REQUESTS",
    "MAX_PAYLOAD_BYTES",
    "WRITE_BUFFER_LIMIT",
    "FramedIpcError",
    "FramedPeer",
    "FramedServer",
    "HandshakeRefused",
    "PeerClosed",
    "RemoteError",
    "RequestRefused",
    "connect",
    "encode_frame",
    "new_token",
    "read_frame",
]
