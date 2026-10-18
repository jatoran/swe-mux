"""DAEMON_LOAD_ISOLATION item 28 - the terminal gateway process.

Against a real `SupervisorServer` whose children are queues (`supervisor_wire`), so
the supervisor half of every conversation is the production code: the gateway's
terminal-scope hello, its subscribes with `since`, the `output` it is sent and the
`gap` it is told about. The daemon half is a real `RemoteHub`. The last test runs
the gateway as a process of its own and blocks the daemon's loop under it, which
is the item's definition of done.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import subprocess
import sys
import threading
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import aiohttp
import pytest

from swe_mux import supervisor as supervisor_module
from swe_mux.scrollback import ScrollbackBuffer
from swe_mux.supervisor import SupervisedSession, SupervisorServer
from swe_mux.terminal_core import RemoteHub, TerminalStream
from swe_mux.terminal_core.protocol import CLOSE_USE_DAEMON
from swe_mux.terminal_gateway import (
    CAPABILITY_OUTPUT_GAP,
    CAPABILITY_SUBSCRIBE_SINCE,
    CAPABILITY_TERMINAL_SCOPE,
    DISCOVERY_FILENAME,
    GATEWAY_MODULES,
    SUBSCRIBE_EAGER,
    SUBSCRIBE_ON_ATTACH,
    SUPERVISOR_PROTOCOL,
    SUPERVISOR_SCOPE,
    GatewayOptions,
    InstanceLock,
    TerminalGateway,
    gateway_build_id,
    gateway_command,
    read_discovery,
)
from tests.support.settle import until
from tests.support.supervisor_wire import StreamHost, open_supervisor
from tests.support.terminal_rig import DaemonHost, descriptor

#: Any port number: the browser's page claims to come from the daemon on it.
DAEMON_PORT = 8765
SID = "s1"


class EchoHost(StreamHost):
    """A child whose terminal echoes every write straight back as output."""

    def write(self, data: bytes) -> None:
        super().write(data)
        self.output_queue.put_nowait(bytes(data))


def echo_session(server: SupervisorServer, sid: str = SID) -> tuple[SupervisedSession, EchoHost]:
    """`supervisor_wire.add_session`, with a child that echoes."""
    host = EchoHost()
    entry = SupervisedSession(sid, host, ScrollbackBuffer(256 * 1024 * 1024))  # type: ignore[arg-type]
    entry.started_at = time.time()
    entry.spawn_done.set()
    server.sessions[sid] = entry
    entry.fanout_task = asyncio.create_task(server._fanout(entry), name=f"echo-fanout-{sid}")
    return entry, host


@contextlib.asynccontextmanager
async def running_gateway(tmp_path: Path, **changes: Any) -> AsyncIterator[TerminalGateway]:
    options = GatewayOptions(data_dir=tmp_path, daemon_port=DAEMON_PORT, port=0, **changes)
    gateway = TerminalGateway(options)
    task = asyncio.create_task(gateway.run())
    try:
        await until(lambda: read_discovery(tmp_path) is not None, what="the gateway is up")
        yield gateway
    finally:
        gateway.stop.set()
        await asyncio.wait_for(task, timeout=15)


async def attach_daemon(gateway: TerminalGateway, host: DaemonHost | None = None) -> RemoteHub:
    hub = RemoteHub(generation="gen-1")
    await hub.connect("127.0.0.1", gateway.control_port, token=gateway.token)
    stream = TerminalStream(
        256 * 1024,
        attach_replay_bytes=None,
        stream_id="daemon-ring",
        remote_stream=lambda: "",
        rebuilt=lambda: False,
    )
    hub.adopt(stream=stream, descriptor=descriptor(), host=host or DaemonHost())
    await until(lambda: SID in gateway.hub.sessions, what="the gateway hosts the session")
    return hub


class Browser:
    """A real browser-side WebSocket to the gateway's listener."""

    def __init__(self, session: aiohttp.ClientSession, ws: aiohttp.ClientWebSocketResponse):
        self.session = session
        self.ws = ws
        self.frames: list[dict[str, Any]] = []
        self.output = bytearray()

    @classmethod
    async def open(cls, port: int, sid: str = SID) -> Browser:
        session = aiohttp.ClientSession()
        ws = await session.ws_connect(
            f"http://127.0.0.1:{port}/pty/{sid}",
            headers={"Origin": f"http://127.0.0.1:{DAEMON_PORT}"},
        )
        return cls(session, ws)

    async def read_until(self, predicate: Any, *, seconds: float = 10.0) -> None:
        async with asyncio.timeout(seconds):
            while not predicate():
                message = await self.ws.receive()
                if message.type == aiohttp.WSMsgType.TEXT:
                    self.frames.append(json.loads(message.data))
                elif message.type == aiohttp.WSMsgType.BINARY:
                    self.output += message.data
                else:
                    return

    def kinds(self) -> list[str]:
        return [str(frame.get("type")) for frame in self.frames]

    async def attach(self) -> None:
        await self.ws.send_json({"type": "attach_ready", "cols": 100, "rows": 30})
        await self.read_until(lambda: "replay_end" in self.kinds())
        await self.ws.send_json({"type": "claim_input", "device": "desktop", "focused": True})
        await self.read_until(lambda: "input_owner" in self.kinds())

    async def close(self) -> None:
        await self.ws.close()
        await self.session.close()


# --- identity and lifecycle --------------------------------------------------------


def test_the_supervisor_vocabulary_matches_the_supervisor() -> None:
    assert SUPERVISOR_PROTOCOL == supervisor_module.PROTOCOL_VERSION
    assert SUPERVISOR_SCOPE == supervisor_module.SCOPE_TERMINAL
    assert CAPABILITY_OUTPUT_GAP == supervisor_module.CAPABILITY_OUTPUT_GAP
    assert CAPABILITY_SUBSCRIBE_SINCE == supervisor_module.CAPABILITY_SUBSCRIBE_SINCE
    assert CAPABILITY_TERMINAL_SCOPE == supervisor_module.CAPABILITY_TERMINAL_SCOPE


def test_the_build_id_covers_exactly_what_the_gateway_loads() -> None:
    """The version-skew restart is only as good as the digest's coverage."""
    probe = (
        "import json, sys, swe_mux.terminal_gateway; "
        "print(json.dumps(sorted(m for m in sys.modules if m.startswith('swe_mux'))))"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True, timeout=60
    )
    assert json.loads(result.stdout) == sorted(GATEWAY_MODULES)
    assert gateway_build_id() == gateway_build_id()


def test_the_gateway_is_started_by_its_own_entry_never_the_daemons() -> None:
    frozen = gateway_command(
        data_dir=Path("D:/data"),
        daemon_port=8765,
        port=8766,
        hosts=("127.0.0.1", "100.64.0.9"),
        executable="C:/app/swe-mux.exe",
        frozen=True,
    )
    assert frozen[:2] == ["C:/app/swe-mux.exe", "--gateway-child"]
    assert frozen[-4:] == ["--host", "127.0.0.1", "--host", "100.64.0.9"]
    source = gateway_command(
        data_dir=Path("D:/data"), daemon_port=8765, port=8766, executable="py", frozen=False
    )
    assert source[:3] == ["py", "-m", "swe_mux.terminal_gateway"]


def test_one_gateway_per_data_directory(tmp_path: Path) -> None:
    first = InstanceLock(tmp_path / "terminal-gateway.lock")
    second = InstanceLock(tmp_path / "terminal-gateway.lock")
    assert first.acquire() is True
    try:
        assert second.acquire() is False
    finally:
        first.release()
    assert second.acquire() is True
    second.release()


async def test_discovery_names_the_gateway_and_leaves_with_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with open_supervisor(tmp_path, monkeypatch), running_gateway(tmp_path) as gateway:
        record = read_discovery(tmp_path)
        assert record is not None
        assert record["pid"] == os.getpid()
        assert record["port"] == gateway.port and record["port"] > 0
        assert record["control_port"] == gateway.control_port
        assert record["token"] == gateway.token
        assert record["build_id"] == gateway_build_id()
        assert record["protocol"] == 1
    assert not (tmp_path / DISCOVERY_FILENAME).exists()


async def test_health_waits_for_a_supervisor_and_a_daemon(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with open_supervisor(tmp_path, monkeypatch), running_gateway(tmp_path) as gateway:
        url = f"http://127.0.0.1:{gateway.port}/health"
        async with aiohttp.ClientSession() as client:
            await until(lambda: gateway.link.connected, what="the supervisor link")
            async with client.get(url) as response:
                assert response.status == 503
                assert (await response.json())["status"] == "starting"
            hub = await attach_daemon(gateway)
            try:
                async with client.get(url) as response:
                    body = await response.json()
                    assert response.status == 200, body
                    assert body["daemon_attached"] is True
                    assert body["supervisor"]["connected"] is True
            finally:
                await hub.close()


async def test_the_daemon_quitting_stops_the_gateway(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with open_supervisor(tmp_path, monkeypatch):
        gateway = TerminalGateway(
            GatewayOptions(data_dir=tmp_path, daemon_port=DAEMON_PORT, port=0)
        )
        task = asyncio.create_task(gateway.run())
        await until(lambda: read_discovery(tmp_path) is not None, what="the gateway is up")
        hub = await attach_daemon(gateway)
        assert hub.quit()
        assert await asyncio.wait_for(task, timeout=15) == 0
        assert gateway.exit_reason == "the daemon quit"
        await hub.close()


async def test_nobody_to_serve_for_long_enough_ends_the_gateway(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("swe_mux.terminal_gateway.LIFECYCLE_CHECK_SECONDS", 0.05)
    gateway = TerminalGateway(
        GatewayOptions(data_dir=tmp_path, daemon_port=DAEMON_PORT, port=0, idle_exit_seconds=0.2)
    )
    # No supervisor at all, and no daemon: there is nothing this gateway could do.
    assert await asyncio.wait_for(gateway.run(), timeout=15) == 0
    assert "no daemon and nothing live" in gateway.exit_reason


# --- terminals through the gateway -------------------------------------------------


@pytest.mark.parametrize("policy", [SUBSCRIBE_EAGER, SUBSCRIBE_ON_ATTACH])
async def test_a_browser_types_through_the_gateway_and_sees_the_echo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, policy: str
) -> None:
    async with open_supervisor(tmp_path, monkeypatch) as running:
        entry, host = echo_session(running.server)
        entry.scrollback.append(b"earlier output\r\n")
        async with running_gateway(tmp_path, policy=policy) as gateway:
            daemon = DaemonHost()
            hub = await attach_daemon(gateway, daemon)
            if policy == SUBSCRIBE_EAGER:
                await until(
                    lambda: gateway.link.stats()["subscribed"] == 1,
                    what="subscribed before any browser asked",
                )
            # The supervisor knows this connection only by the narrow scope.
            assert [c.scope for c in running.server.connections] == [SUPERVISOR_SCOPE]
            browser = await Browser.open(gateway.port)
            try:
                await browser.attach()
                assert b"earlier output" in browser.output, "the attach replayed the ring"
                await browser.ws.send_json({"type": "input", "data": "hi"})
                await browser.read_until(lambda: browser.output.endswith(b"hi"))
                assert host.writes == [b"hi"]
                await until(lambda: daemon.typed == ["hi"], what="the daemon heard it")
                assert hub.terminals[SID].input_revision == 1
            finally:
                await browser.close()
                await hub.close()


async def test_an_on_attach_subscription_releases_its_ring_after_the_linger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with open_supervisor(tmp_path, monkeypatch) as running:
        entry, host = echo_session(running.server)
        entry.scrollback.append(b"history ")
        async with running_gateway(
            tmp_path, policy=SUBSCRIBE_ON_ATTACH, linger_seconds=0.05
        ) as gateway:
            hub = await attach_daemon(gateway)
            try:
                assert gateway.link.stats()["subscribed"] == 0, "nobody is watching yet"
                ring = gateway.hub.sessions[SID].core.stream.scrollback
                assert ring.size == 0
                first = await Browser.open(gateway.port)
                await first.attach()
                assert b"history" in first.output
                await first.close()
                subscription = gateway.link.subscriptions[SID]
                await until(lambda: not subscription.ready.is_set(), what="the linger ended")
                assert ring.size == 0, "the released session's ring was kept"

                host.output_queue.put_nowait(b"while unwatched")
                await until(
                    lambda: entry.scrollback.position == len(b"history while unwatched"),
                    what="the supervisor took the output",
                )
                second = await Browser.open(gateway.port)
                try:
                    await second.attach()
                    # A whole-buffer subscribe, not a delta onto an emptied ring.
                    assert b"history while unwatched" in second.output
                    assert gateway.link.full_subscribes == 2
                    assert gateway.link.delta_subscribes == 0
                finally:
                    await second.close()
            finally:
                await hub.close()


async def test_a_gap_is_recovered_without_losing_a_byte(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with open_supervisor(tmp_path, monkeypatch) as running:
        entry, host = echo_session(running.server)
        async with running_gateway(tmp_path) as gateway:
            hub = await attach_daemon(gateway)
            try:
                ring = gateway.hub.sessions[SID].core.stream
                await until(lambda: gateway.link.subscriptions[SID].ready.is_set(), what="ready")
                host.output_queue.put_nowait(b"one ")
                await until(lambda: ring.scrollback.position == 4, what="the first chunk")

                # What a supervisor does when this reader falls behind: it stops
                # sending this session and says where the gateway's copy stops.
                connection = next(c for c in running.server.connections if c.output_gap)
                connection.gapped.add(SID)
                host.output_queue.put_nowait(b"two ")
                await until(lambda: entry.scrollback.position == 8, what="dropped for us")
                assert ring.scrollback.position == 4
                assert gateway.link.peer is not None
                gateway.link._on_notice(
                    gateway.link.peer, {"t": "gap", "sid": SID, "position": 4}, b""
                )
                await until(lambda: ring.scrollback.position == 8, what="the gap recovered")
                assert ring.scrollback.tail(None) == b"one two "
                assert gateway.link.gaps == 1
                assert gateway.link.delta_subscribes == 1, "the recovery was not a delta"
            finally:
                await hub.close()


async def test_a_lost_supervisor_connection_resubscribes_from_where_it_was(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with open_supervisor(tmp_path, monkeypatch) as running:
        entry, host = echo_session(running.server)
        async with running_gateway(tmp_path) as gateway:
            hub = await attach_daemon(gateway)
            try:
                ring = gateway.hub.sessions[SID].core.stream
                host.output_queue.put_nowait(b"before ")
                await until(lambda: ring.scrollback.position == 7, what="the first chunk")
                assert gateway.link.peer is not None
                await gateway.link.peer.close("test drop")
                host.output_queue.put_nowait(b"during ")
                await until(lambda: gateway.link.connects == 2, seconds=15, what="reconnected")
                await until(lambda: ring.scrollback.position == 14, what="caught up")
                assert ring.scrollback.tail(None) == b"before during "
                lossy = [item for item in running.server.connections if item.output_gap]
                assert lossy, "the reconnect asked for output_gap again"
                assert gateway.link.delta_subscribes == 1, "the resubscribe was not a delta"
            finally:
                await hub.close()


async def test_a_session_the_gateway_does_not_hold_goes_to_the_daemon(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with open_supervisor(tmp_path, monkeypatch), running_gateway(tmp_path) as gateway:
        hub = await attach_daemon(gateway)
        try:
            async with aiohttp.ClientSession() as client:
                ws = await client.ws_connect(
                    f"http://127.0.0.1:{gateway.port}/pty/elsewhere",
                    headers={"Origin": f"http://127.0.0.1:{DAEMON_PORT}"},
                )
                await ws.receive()
                assert ws.close_code == CLOSE_USE_DAEMON
        finally:
            await hub.close()


async def test_a_foreign_page_is_refused_by_the_gateways_listener(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with open_supervisor(tmp_path, monkeypatch), running_gateway(tmp_path) as gateway:
        async with aiohttp.ClientSession() as client:
            with pytest.raises(aiohttp.WSServerHandshakeError) as refused:
                await client.ws_connect(
                    f"http://127.0.0.1:{gateway.port}/pty/{SID}",
                    headers={"Origin": "http://evil.example"},
                )
            assert refused.value.status == 403


async def test_the_traffic_it_carries_reaches_the_daemons_counters(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("swe_mux.terminal_gateway.METERING_INTERVAL_SECONDS", 0.05)
    async with open_supervisor(tmp_path, monkeypatch) as running:
        echo_session(running.server)
        async with running_gateway(tmp_path) as gateway:
            hub = await attach_daemon(gateway)
            reports: list[dict[str, Any]] = []
            hub.on_metering = reports.append
            browser = await Browser.open(gateway.port)
            try:
                await browser.attach()
                await until(lambda: bool(reports), what="a metering report")
                assert reports[-1]["gateway_id"] == gateway.hub.gateway_id
                assert reports[-1]["usage"]
            finally:
                await browser.close()
                await hub.close()


# --- the definition of done --------------------------------------------------------


class _SupervisorThread:
    """A real supervisor on its own thread and loop, so blocking the daemon's loop
    leaves the terminal's other two processes running."""

    def __init__(self, data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(supervisor_module, "create_reaper", _NoReaper)
        self.data_dir = data_dir
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.server: SupervisorServer | None = None

    def start(self) -> None:
        self.thread.start()

        async def build() -> None:
            server = SupervisorServer(self.data_dir / "config.toml", self.data_dir)
            await server.start()
            echo_session(server)
            self.server = server

        asyncio.run_coroutine_threadsafe(build(), self.loop).result(15)

    def stop(self) -> None:
        server = self.server
        if server is not None:

            async def teardown() -> None:
                server.exit_event.set()
                await server._teardown()
                fanouts = [e.fanout_task for e in server.sessions.values() if e.fanout_task]
                for task in fanouts:
                    task.cancel()
                await asyncio.gather(*fanouts, return_exceptions=True)

            asyncio.run_coroutine_threadsafe(teardown(), self.loop).result(30)
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(15)
        self.loop.close()


class _NoReaper:
    def assign(self, pid: int) -> None:
        del pid

    def create_child(self) -> _NoReaper:
        return _NoReaper()

    def process_ids(self) -> list[int]:
        return []

    def close(self) -> None:
        pass


class _TypingBrowser:
    """A browser on a thread of its own: it types every 50 ms and times each echo."""

    def __init__(self, port: int) -> None:
        self.port = port
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.echo_ms: list[float] = []
        self.error: BaseException | None = None

    def start(self) -> None:
        self.thread.start()

    def type_for(self, seconds: float) -> asyncio.Future[Any]:
        return asyncio.wrap_future(
            asyncio.run_coroutine_threadsafe(self._type(seconds), self.loop)
        )

    async def _type(self, seconds: float) -> None:
        browser = await Browser.open(self.port)
        try:
            await browser.attach()
            deadline = time.monotonic() + seconds
            index = 0
            while time.monotonic() < deadline:
                key = chr(ord("a") + index % 26)
                sent = time.perf_counter()
                expected = len(browser.output) + 1
                await browser.ws.send_json({"type": "input", "data": key})
                await browser.read_until(
                    lambda wanted=expected: len(browser.output) >= wanted, seconds=5
                )
                self.echo_ms.append((time.perf_counter() - sent) * 1000)
                index += 1
                await asyncio.sleep(0.05)
        finally:
            await browser.close()

    def stop(self) -> None:
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(15)
        self.loop.close()


async def test_typing_keeps_echoing_while_the_daemon_is_blocked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Item 28's definition of done, with the gateway as a real process."""
    supervisor = _SupervisorThread(tmp_path, monkeypatch)
    supervisor.start()
    command = gateway_command(
        data_dir=tmp_path, daemon_port=DAEMON_PORT, port=0, executable=sys.executable, frozen=False
    )
    gateway = subprocess.Popen(  # noqa: ASYNC220 - a long-lived child, never waited on here
        [*command, "--idle-exit-seconds", "30"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    typing: _TypingBrowser | None = None
    hub = RemoteHub(generation="gen-1")
    try:
        await until(lambda: read_discovery(tmp_path) is not None, seconds=30, what="it started")
        record = read_discovery(tmp_path)
        assert record is not None
        await hub.connect("127.0.0.1", int(record["control_port"]), token=str(record["token"]))
        daemon = DaemonHost()
        stream = TerminalStream(
            64 * 1024,
            attach_replay_bytes=None,
            stream_id="daemon-ring",
            remote_stream=lambda: "",
            rebuilt=lambda: False,
        )
        terminal = hub.adopt(stream=stream, descriptor=descriptor(), host=daemon)

        typing = _TypingBrowser(int(record["port"]))
        typing.start()
        typed = typing.type_for(1.5)
        await asyncio.sleep(0.3)  # typing is under way
        # The daemon stops answering anything: a GIL-bound stall on its loop.
        time.sleep(1.0)  # noqa: ASYNC251 - the blocked loop is what this test is about
        await asyncio.wait_for(typed, timeout=30)

        assert len(typing.echo_ms) >= 15, typing.echo_ms
        # Every keystroke echoed, including the ones typed while the daemon slept,
        # and none waited for the daemon to wake: a blocked daemon would have held
        # the ones in that second for most of it.
        assert max(typing.echo_ms) < 500, typing.echo_ms
        # And the daemon, awake again, still hears every one of them, in order.
        await until(lambda: len(daemon.typed) == len(typing.echo_ms), what="the daemon caught up")
        assert terminal.input_revision == len(typing.echo_ms)
    finally:
        if typing is not None:
            typing.stop()
        if hub.connected:
            hub.quit()
        await hub.close()
        try:
            gateway.wait(timeout=15)
        except subprocess.TimeoutExpired:
            gateway.kill()
            gateway.wait(timeout=15)
        supervisor.stop()
