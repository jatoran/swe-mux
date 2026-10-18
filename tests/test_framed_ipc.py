"""The framed IPC module the worker and the terminal gateway speak.

`DAEMON_LOAD_ISOLATION.md` item 18. Two properties are pinned against the PTY
supervisor rather than restated: the framing is byte-identical to
`supervisor.encode_frame`, and a `FramedPeer` can hold a terminal-scope
conversation with a real `SupervisorServer` - which is what the gateway will do.
The rest is the module's own contract: the handshake's refusals and bounds,
request and notice routing in both directions, ordering, back-pressure, and
closing.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import socket
import struct
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from swe_mux import framed_ipc, supervisor
from swe_mux.framed_ipc import (
    FramedPeer,
    FramedServer,
    HandshakeRefused,
    PeerClosed,
    RemoteError,
    RequestRefused,
)
from tests.support.settle import until
from tests.support.supervisor_wire import add_session, open_supervisor, pour

ROOT = Path(__file__).resolve().parents[1]
TOKEN = "test-token-not-secret"
PROTOCOL = 7

FRAMES: list[tuple[dict[str, Any], bytes]] = [
    ({"t": "ping", "id": 3}, b""),
    ({"t": "write", "sid": "abc", "id": 9}, b"\x00\x01\xff keystrokes"),
    ({"id": 4, "ok": True, "nested": {"a": [1, 2.5, None, True]}}, b""),
    ({"t": "output", "sid": "s", "text": "café ✓ \U0001f600"}, b"x" * 70_000),
    ({}, b""),
]
FRAME_IDS = ["request", "binary-payload", "nested-reply", "unicode-large-payload", "empty"]


# --- framing -------------------------------------------------------------------


@pytest.mark.parametrize(("header", "payload"), FRAMES, ids=FRAME_IDS)
def test_frames_are_byte_identical_to_the_supervisors(
    header: dict[str, Any], payload: bytes
) -> None:
    assert framed_ipc.encode_frame(header, payload) == supervisor.encode_frame(header, payload)


def _reader_over(data: bytes) -> asyncio.StreamReader:
    reader = asyncio.StreamReader()
    reader.feed_data(data)
    reader.feed_eof()
    return reader


@pytest.mark.parametrize(("header", "payload"), FRAMES, ids=FRAME_IDS)
async def test_each_side_reads_the_others_frames(header: dict[str, Any], payload: bytes) -> None:
    ours = framed_ipc.encode_frame(header, payload)
    theirs = supervisor.encode_frame(header, payload)
    expected_header = {**header, "plen": len(payload)} if payload else header

    assert await supervisor.read_frame(_reader_over(ours)) == (expected_header, payload)
    assert await framed_ipc.read_frame(_reader_over(theirs)) == (expected_header, payload)


@pytest.mark.parametrize(
    ("raw", "match"),
    [
        (struct.pack(">I", 40) + b"{}", "oversized frame header"),
        (struct.pack(">I", 2) + b"[]", "must be a JSON object"),
        (framed_ipc.encode_frame({"plen": -1}), "negative"),
        (framed_ipc.encode_frame({"plen": "lots"}), "not a number"),
        (framed_ipc.encode_frame({"plen": 11}), "oversized frame payload"),
    ],
)
async def test_a_length_is_refused_before_it_is_honoured(raw: bytes, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        await framed_ipc.read_frame(_reader_over(raw), max_header_bytes=32, max_payload_bytes=10)


# --- a server and a client ---------------------------------------------------------


class Recorder:
    """What one end's handlers saw."""

    def __init__(self) -> None:
        self.notices: list[tuple[dict[str, Any], bytes]] = []
        self.closed: list[str] = []
        self.connections: list[FramedPeer] = []

    def notice(self, peer: FramedPeer, header: dict[str, Any], payload: bytes) -> None:
        self.notices.append((header, payload))

    def close(self, peer: FramedPeer, reason: str) -> None:
        self.closed.append(reason)

    def connected(self, peer: FramedPeer) -> None:
        self.connections.append(peer)


async def echo(peer: FramedPeer, header: dict[str, Any], payload: bytes) -> Any:
    kind = header.get("t")
    if kind == "echo":
        return {"echo": header.get("value")}, payload
    if kind == "refuse":
        raise RequestRefused("not today")
    if kind == "crash":
        raise KeyError("boom")
    if kind == "nothing":
        return None
    raise RequestRefused(f"unknown message type: {kind!r}")


@contextlib.asynccontextmanager
async def serving(**kwargs: Any):  # type: ignore[no-untyped-def]
    server_seen = Recorder()
    options: dict[str, Any] = {
        "token": TOKEN,
        "protocol": PROTOCOL,
        "capabilities": ("alpha", "beta"),
        "on_request": echo,
        "on_notice": server_seen.notice,
        "on_close": server_seen.close,
        "on_connection": server_seen.connected,
        **kwargs,
    }
    server = FramedServer(**options)
    await server.start()
    try:
        yield server, server_seen
    finally:
        await asyncio.wait_for(server.close(), timeout=10)


async def dial(server: FramedServer, **kwargs: Any) -> FramedPeer:
    options: dict[str, Any] = {"token": TOKEN, "protocol": PROTOCOL, **kwargs}
    return await framed_ipc.connect("127.0.0.1", server.port, **options)


async def raw_hello(port: int, frame: bytes) -> dict[str, Any] | None:
    """Send one raw frame as a hello; the reply, or None if the server just closed."""
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        writer.write(frame)
        await writer.drain()
        try:
            header, _ = await asyncio.wait_for(framed_ipc.read_frame(reader), timeout=5)
        except asyncio.IncompleteReadError:
            return None
        # Refused or not, the server closes the connection after a refusal.
        rest = await asyncio.wait_for(reader.read(), timeout=5)
        assert rest == b"", "a refused connection is closed"
        return header
    finally:
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()


async def test_a_hello_with_the_token_connects_and_agrees_on_capabilities() -> None:
    async with serving() as (server, server_seen):
        client = await dial(server, capabilities=["beta", "gamma"])
        try:
            assert client.capabilities == {"beta"}, "only what both ends announced"
            assert client.remote_hello["capabilities"] == ["alpha", "beta"]
            assert client.remote_hello["protocol"] == PROTOCOL
            await until(lambda: len(server_seen.connections) == 1, what="the server saw it")
            assert server_seen.connections[0].capabilities == {"beta"}
            assert server_seen.connections[0].remote_hello["capabilities"] == ["beta", "gamma"]
        finally:
            await client.close()


@pytest.mark.parametrize(
    ("hello", "reason"),
    [
        ({"t": "hello", "id": 0, "token": "wrong", "protocol": PROTOCOL}, "invalid token"),
        ({"t": "hello", "id": 0, "token": None, "protocol": PROTOCOL}, "invalid token"),
        ({"t": "hello", "id": 0, "token": TOKEN, "protocol": PROTOCOL + 1}, "protocol mismatch"),
        ({"t": "ping", "id": 0, "token": TOKEN, "protocol": PROTOCOL}, "hello with a valid token"),
    ],
)
async def test_a_bad_hello_is_refused_and_closed(hello: dict[str, Any], reason: str) -> None:
    async with serving() as (server, server_seen):
        reply = await raw_hello(server.port, framed_ipc.encode_frame(hello))
        assert reply is not None and reply["ok"] is False
        assert reason in reply["error"]
        assert server.refused == 1
        assert not server.peers and not server_seen.connections


async def test_the_client_reports_a_refusal_as_a_handshake_error() -> None:
    async with serving() as (server, _):
        with pytest.raises(HandshakeRefused, match="invalid token"):
            await dial(server, token="wrong")
        with pytest.raises(HandshakeRefused, match="protocol mismatch"):
            await dial(server, protocol=PROTOCOL + 1)


async def test_an_unauthenticated_peer_gets_no_payload_and_no_room() -> None:
    async with serving() as (server, _):
        with_payload = framed_ipc.encode_frame(
            {"t": "hello", "id": 0, "token": TOKEN, "protocol": PROTOCOL}, b"smuggled"
        )
        reply = await raw_hello(server.port, with_payload)
        assert reply is not None and "malformed hello" in reply["error"]
        oversized = struct.pack(">I", framed_ipc.HELLO_MAX_HEADER_BYTES + 1)
        reply = await raw_hello(server.port, oversized)
        assert reply is not None and "oversized frame header" in reply["error"]
        assert server.refused == 2


async def test_a_silent_peer_is_dropped_at_the_deadline() -> None:
    async with serving(auth_deadline=0.2) as (server, _):
        reader, writer = await asyncio.open_connection("127.0.0.1", server.port)
        try:
            header, _ = await asyncio.wait_for(framed_ipc.read_frame(reader), timeout=5)
            assert header["ok"] is False and "deadline" in header["error"]
            assert await asyncio.wait_for(reader.read(), timeout=5) == b""
        finally:
            writer.close()


async def test_a_hello_the_server_declines_is_refused_with_its_reason() -> None:
    def scoped(hello: dict[str, Any]) -> dict[str, Any]:
        if hello.get("scope") not in (None, "terminal"):
            raise HandshakeRefused(f"unknown scope {hello.get('scope')!r}")
        return {"scope": hello.get("scope") or "daemon"}

    async with serving(hello_reply=scoped) as (server, _):
        client = await dial(server, hello={"scope": "terminal"})
        assert client.remote_hello["scope"] == "terminal"
        await client.close()
        with pytest.raises(HandshakeRefused, match="unknown scope"):
            await dial(server, hello={"scope": "root"})


async def test_a_failing_hello_check_is_an_internal_error_not_a_crash(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def broken(hello: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("bug")

    async with serving(hello_reply=broken) as (server, _):
        with pytest.raises(HandshakeRefused, match="internal error"):
            await dial(server)
    assert any("failed checking a hello" in record.message for record in caplog.records)


async def test_a_hello_cannot_smuggle_a_different_token_through_extra_fields() -> None:
    async with serving() as (server, _):
        # The caller's extra fields come first, so they cannot override the
        # identity fields `connect` writes.
        with pytest.raises(HandshakeRefused, match="invalid token"):
            await dial(server, token="wrong", hello={"token": TOKEN})


# --- requests and notices --------------------------------------------------------


async def test_requests_round_trip_with_payloads_and_refusals(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async with serving() as (server, _):
        client = await dial(server)
        try:
            reply, payload = await client.request({"t": "echo", "value": 5}, b"\x00bytes\xff")
            assert reply["echo"] == 5 and reply["ok"] is True and payload == b"\x00bytes\xff"
            reply, payload = await client.request({"t": "nothing"})
            assert reply == {"id": reply["id"], "ok": True} and payload == b""

            with pytest.raises(RemoteError, match="not today") as refused:
                await client.request({"t": "refuse"})
            assert refused.value.header["ok"] is False
            assert not any("not today" in record.message for record in caplog.records), (
                "a deliberate refusal is not logged as a failure"
            )

            with pytest.raises(RemoteError, match="KeyError"):
                await client.request({"t": "crash"})
            assert any("request handler" in record.message for record in caplog.records)
        finally:
            await client.close()


async def test_an_end_with_no_request_handler_answers_unknown_type() -> None:
    async with serving(on_request=None) as (server, _):
        client = await dial(server)
        try:
            with pytest.raises(RemoteError, match="unknown message type: 'anything'"):
                await client.request({"t": "anything"})
        finally:
            await client.close()


async def test_either_end_can_ask_and_either_can_notify() -> None:
    client_seen = Recorder()

    async def client_answers(peer: FramedPeer, header: dict[str, Any], payload: bytes) -> Any:
        return {"from": "client", "got": header.get("t")}

    async with serving() as (server, server_seen):
        client = await dial(server, on_request=client_answers, on_notice=client_seen.notice)
        try:
            await until(lambda: len(server_seen.connections) == 1, what="the server saw it")
            server_side = server_seen.connections[0]

            reply, _ = await server_side.request({"t": "status"})
            assert reply["from"] == "client" and reply["got"] == "status"
            # Ids are per direction: the client's first request reuses the
            # number the server just used, and neither is confused.
            reply, _ = await client.request({"t": "echo", "value": "x"})
            assert reply["echo"] == "x"

            client.notify({"t": "input", "sid": "a"}, b"keys")
            server_side.notify({"t": "output", "sid": "a"}, b"screen")
            await until(lambda: server_seen.notices and client_seen.notices, what="both notices")
            assert server_seen.notices == [({"t": "input", "sid": "a", "plen": 4}, b"keys")]
            assert client_seen.notices == [({"t": "output", "sid": "a", "plen": 6}, b"screen")]
            with pytest.raises(ValueError, match="no id"):
                client.notify({"t": "input", "id": 3})
        finally:
            await client.close()


async def test_handlers_start_in_the_order_their_requests_arrived() -> None:
    started: list[int] = []

    async def slow_first(peer: FramedPeer, header: dict[str, Any], payload: bytes) -> Any:
        index = int(header["n"])
        started.append(index)  # before the first await: the ordering guarantee
        await asyncio.sleep(0.05 * (5 - index))
        return {"n": index}

    async with serving(on_request=slow_first) as (server, _):
        client = await dial(server)
        try:
            replies = await asyncio.gather(
                *(client.request({"t": "work", "n": n}) for n in range(5))
            )
            assert [reply["n"] for reply, _ in replies] == list(range(5))
            assert started == list(range(5))
        finally:
            await client.close()


async def test_a_reply_hook_runs_before_the_next_frame_is_dispatched() -> None:
    state: dict[str, Any] = {}
    seen_with_state: list[Any] = []

    async def answer_then_notify(peer: FramedPeer, header: dict[str, Any], payload: bytes) -> Any:
        # The notice is written straight after the reply, so both arrive in one
        # read and are dispatched back to back.
        loop = asyncio.get_running_loop()
        loop.call_soon(peer.notify, {"t": "after"})
        return {"position": 42}

    def on_notice(peer: FramedPeer, header: dict[str, Any], payload: bytes) -> None:
        seen_with_state.append(state.get("position"))

    async with serving(on_request=answer_then_notify) as (server, _):
        client = await dial(server, on_notice=on_notice)
        try:
            await client.request(
                {"t": "subscribe"},
                on_reply=lambda reply, _payload: state.update(position=reply["position"]),
            )
            await until(lambda: bool(seen_with_state), what="the notice after the reply")
            assert seen_with_state == [42]
        finally:
            await client.close()


async def test_requests_past_the_inflight_bound_are_answered_busy() -> None:
    release = asyncio.Event()

    async def blocked(peer: FramedPeer, header: dict[str, Any], payload: bytes) -> Any:
        await release.wait()
        return {"done": True}

    async with serving(on_request=blocked, max_inflight=2) as (server, _):
        client = await dial(server)
        try:
            first = asyncio.ensure_future(client.request({"t": "a"}))
            second = asyncio.ensure_future(client.request({"t": "b"}))
            await until(
                lambda: any(len(peer._handlers) == 2 for peer in server.peers),
                what="two handlers in flight",
            )
            with pytest.raises(RemoteError, match=framed_ipc.ERROR_BUSY):
                await client.request({"t": "c"})
            release.set()
            assert (await first)[0]["done"] and (await second)[0]["done"]
            (server_side,) = server.peers
            assert server_side.requests_refused_busy == 1
        finally:
            await client.close()


async def test_a_request_times_out_and_its_late_reply_is_dropped() -> None:
    release = asyncio.Event()

    async def late(peer: FramedPeer, header: dict[str, Any], payload: bytes) -> Any:
        await release.wait()
        return {"late": True}

    async with serving(on_request=late) as (server, _):
        client = await dial(server)
        try:
            with pytest.raises(TimeoutError):
                await client.request({"t": "slow"}, timeout_seconds=0.1)
            release.set()
            reply, _ = await client.request({"t": "echo"}, timeout_seconds=5)
            assert reply["late"] is True, "the next request gets its own reply, not the stale one"
            assert not client._pending
        finally:
            await client.close()


# --- bounds and closing ---------------------------------------------------------------


async def test_a_frame_over_the_authenticated_bound_closes_the_connection() -> None:
    async with serving(max_payload_bytes=1024) as (server, server_seen):
        client = await dial(server)
        client.notify({"t": "big"}, b"x" * 2048)
        await asyncio.wait_for(client.wait_closed(), timeout=5)
        await until(lambda: bool(server_seen.closed), what="the server closed it")
        assert "oversized frame payload" in server_seen.closed[0]
        assert client.close_reason.startswith("connection lost")


async def test_a_peer_that_stops_reading_is_dropped_rather_than_buffered() -> None:
    async with serving(write_buffer_limit=64 * 1024) as (server, server_seen):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
        sock.setblocking(False)
        await asyncio.get_running_loop().sock_connect(sock, ("127.0.0.1", server.port))
        reader, writer = await asyncio.open_connection(sock=sock, limit=4096)
        try:
            writer.write(
                framed_ipc.encode_frame(
                    {"t": "hello", "id": 0, "token": TOKEN, "protocol": PROTOCOL}
                )
            )
            await writer.drain()
            await until(lambda: len(server_seen.connections) == 1, what="the handshake")
            server_side = server_seen.connections[0]
            # This client reads its hello reply and nothing after it.
            for _ in range(64):
                if server_side.closed:
                    break
                with contextlib.suppress(PeerClosed):
                    server_side.notify({"t": "output"}, b"y" * (1024 * 1024))
                await asyncio.sleep(0)
            assert server_side.closed
            assert "not reading" in server_side.close_reason
            assert not server.peers
        finally:
            writer.close()


async def test_closing_fails_what_is_pending_and_reports_once() -> None:
    hang = asyncio.Event()

    async def never(peer: FramedPeer, header: dict[str, Any], payload: bytes) -> Any:
        await hang.wait()

    client_seen = Recorder()
    async with serving(on_request=never) as (server, server_seen):
        client = await dial(server, on_close=client_seen.close)
        pending = asyncio.ensure_future(client.request({"t": "wait"}))
        await until(lambda: any(peer._handlers for peer in server.peers), what="in flight")
        (server_side,) = server.peers
        await server_side.close("test says so")

        with pytest.raises(PeerClosed):
            await asyncio.wait_for(pending, timeout=5)
        await asyncio.wait_for(client.wait_closed(), timeout=5)
        assert len(client_seen.closed) == 1
        assert server_seen.closed == ["test says so"]
        with pytest.raises(PeerClosed):
            await client.request({"t": "after"})
        with pytest.raises(PeerClosed):
            client.notify({"t": "after"})
        await client.close()
        assert len(client_seen.closed) == 1, "closing twice reports once"


async def test_a_server_closes_promptly_with_clients_still_connected() -> None:
    async with serving() as (server, _):
        clients = [await dial(server) for _ in range(3)]
        await until(lambda: len(server.peers) == 3, what="three peers")
        await asyncio.wait_for(server.close(), timeout=5)
        for client in clients:
            await asyncio.wait_for(client.wait_closed(), timeout=5)
        assert not server.peers


async def test_stats_describe_the_connection() -> None:
    async with serving() as (server, _):
        client = await dial(server, capabilities=["alpha"])
        try:
            await client.request({"t": "echo", "value": 1}, b"abc")
            client.notify({"t": "n"})
            stats = client.stats()
            assert stats["requests_sent"] == 1 and stats["notices_sent"] == 1
            assert stats["capabilities"] == ["alpha"]
            assert stats["bytes_sent"] > 0 and stats["bytes_received"] > 0
            assert stats["closed"] is False
            served = server.stats()
            assert served["port"] == server.port and len(served["peers"]) == 1
            json.dumps(served)
        finally:
            await client.close()


# --- against the real supervisor -------------------------------------------------------


async def test_a_framed_peer_drives_the_supervisor_with_terminal_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The gateway's conversation, through this module rather than a test client."""
    output: list[tuple[str, bytes]] = []

    def on_notice(peer: FramedPeer, header: dict[str, Any], payload: bytes) -> None:
        if header.get("t") == "output":
            output.append((str(header.get("sid")), payload))

    async with open_supervisor(tmp_path, monkeypatch) as running:
        entry, host = add_session(running.server, "s1")
        peer = await framed_ipc.connect(
            "127.0.0.1",
            running.port,
            token=running.server.token,
            protocol=supervisor.PROTOCOL_VERSION,
            capabilities=[supervisor.CAPABILITY_OUTPUT_GAP, supervisor.CAPABILITY_TERMINAL_SCOPE],
            hello={"scope": supervisor.SCOPE_TERMINAL},
            max_header_bytes=supervisor.MAX_HEADER_BYTES,
            on_notice=on_notice,
        )
        try:
            assert peer.remote_hello["scope"] == supervisor.SCOPE_TERMINAL
            assert peer.capabilities == {
                supervisor.CAPABILITY_OUTPUT_GAP,
                supervisor.CAPABILITY_TERMINAL_SCOPE,
            }
            reply, replay = await peer.request({"t": "subscribe", "sid": "s1"})
            assert reply["stream"] == entry.stream and "meta" not in reply and replay == b""
            await peer.request({"t": "ping"})
            with pytest.raises(RemoteError, match="terminal-scope"):
                await peer.request({"t": "spawn", "sid": "s2"})

            await pour(host, 3)
            await until(lambda: len(output) == 3, what="three output frames")
            assert all(sid == "s1" for sid, _ in output)
        finally:
            await peer.close()


# --- what importing it costs ---------------------------------------------------------


def test_importing_it_loads_only_the_standard_library() -> None:
    """The processes that import this must not pay for the daemon's imports."""
    probe = (
        "import sys; sys.path.insert(0, sys.argv[1]); import swe_mux.framed_ipc, json; "
        "top = {name.split('.')[0] for name in sys.modules}; "
        "print(json.dumps({"
        "'foreign': sorted(top - set(sys.stdlib_module_names) - {'swe_mux', '__main__'}), "
        "'ours': sorted(n for n in sys.modules if n.split('.')[0] == 'swe_mux')}))"
    )
    # -S keeps site-packages `.pth` hooks out, so what is measured is the import.
    result = subprocess.run(
        [sys.executable, "-S", "-c", probe, str(ROOT / "src")],
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    loaded = json.loads(result.stdout)
    assert loaded == {"foreign": [], "ours": ["swe_mux", "swe_mux.framed_ipc"]}
