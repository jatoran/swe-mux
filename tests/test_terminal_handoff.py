"""DAEMON_LOAD_ISOLATION item 32 - a session's terminal moves between the daemon and
the terminal gateway, with browsers attached, and lives in exactly one at a time.

The daemon side is the real thing - a `Session` over a supervisor-owned PTY, the real
`TerminalPlacement` and `RemoteHub` - and so is the gateway's `GatewayHub`, reached over
a real loopback connection. What stands in for the processes is only their edges: the
supervisor client records what the daemon would have written, and each side's PTY sink
records what reached the child from there. A write in the wrong list is two writers.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from swe_mux import app_keys as keys
from swe_mux.config import Config
from swe_mux.framed_ipc import new_token
from swe_mux.gateway_host import GatewayHost
from swe_mux.routes.pty import pty_ws
from swe_mux.session import Session
from swe_mux.terminal_core import (
    GatewayHub,
    RemoteHub,
    RemoteTerminal,
    TerminalCore,
    WriteCondition,
    WriteRefused,
    serve_connection,
)
from swe_mux.terminal_core.protocol import (
    CLOSE_GATEWAY_RESTARTING,
    CLOSE_USE_DAEMON,
    CLOSE_USE_GATEWAY,
)
from swe_mux.terminal_placement import TerminalPlacement
from tests.support.settle import until
from tests.support.supervised_session import (
    SUPERVISOR_STREAM,
    RecordingSupervisorClient,
    supervised_session,
)
from tests.support.terminal_rig import Browser, DaemonHost, FakeSocket, RecordingPty


@dataclass
class Rig:
    """One daemon, one gateway, and the sessions between them."""

    sessions: dict[str, Session] = field(default_factory=dict)
    clients: dict[str, RecordingSupervisorClient] = field(default_factory=dict)
    hosts: dict[str, DaemonHost] = field(default_factory=dict)
    gateway_pty: RecordingPty = field(default_factory=RecordingPty)
    token: str = field(default_factory=new_token)
    tasks: list[asyncio.Task[Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.gateway = GatewayHub(
            token=self.token,
            pty_for=lambda _sid: self.gateway_pty,
            stream_name=lambda _sid: SUPERVISOR_STREAM,
        )
        self.hub = RemoteHub(generation="gen-1")
        self.placement = TerminalPlacement(
            self.hub, sessions=lambda: self.sessions, host_for=self.host_for
        )
        self.port = 0

    def host_for(self, session: Session) -> DaemonHost:
        return self.hosts.setdefault(session.record.id, DaemonHost())

    def add(self, sid: str = "s1", **changes: Any) -> Session:
        session, client = supervised_session(sid, **changes)
        self.sessions[sid] = session
        self.clients[sid] = client
        return session

    async def connect(self, hub: RemoteHub | None = None) -> None:
        hub = hub or self.hub
        await hub.connect(
            "127.0.0.1", self.port, token=self.token, before_sync=self.placement.adopt_all
        )

    def daemon_browser(self, session: Session, connection_id: str) -> Browser:
        """A browser the daemon serves, as its `/pty` route would."""
        core = session.terminal
        assert isinstance(core, TerminalCore)
        socket = FakeSocket()
        task = asyncio.create_task(
            serve_connection(socket, core, self.host_for(session), connection_id=connection_id)
        )
        self.tasks.append(task)
        return Browser(socket, task)

    def gateway_browser(self, sid: str, connection_id: str) -> Browser:
        socket = FakeSocket()
        task = asyncio.create_task(self.gateway.serve(socket, sid, connection_id=connection_id))
        self.tasks.append(task)
        return Browser(socket, task)

    def seed_gateway_ring(self, sid: str) -> None:
        """What the gateway's eager supervisor subscription does: take the supervisor's
        buffer at its position, which is the daemon's ring at the same position."""
        daemon_ring = self.sessions[sid].terminal.stream.scrollback
        gateway_ring = self.gateway.sessions[sid].core.stream
        gateway_ring.reseed(daemon_ring.tail(None), daemon_ring.position)

    def feed(self, sid: str, data: bytes) -> None:
        """New output, reaching both processes' rings as the supervisor fans it out."""
        self.sessions[sid].terminal.stream.feed(data)
        if sid in self.gateway.sessions:
            self.gateway.feed_output(sid, data)


@asynccontextmanager
async def rig() -> AsyncIterator[Rig]:
    state = Rig()
    state.port = await state.gateway.start()
    try:
        yield state
    finally:
        for task in state.tasks:
            task.cancel()
        await asyncio.gather(*state.tasks, return_exceptions=True)
        await state.hub.close()
        await state.gateway.close()


def replay_end(browser: Browser) -> dict[str, Any]:
    return browser.frames("replay_end")[-1]


# --- the daemon hands a terminal to the gateway ---------------------------------------


async def test_a_terminal_moves_to_the_gateway_with_a_browser_attached() -> None:
    async with rig() as state:
        session = state.add()
        state.feed("s1", b"before the move\r\n")
        desktop = state.daemon_browser(session, "desktop-1")
        await desktop.attach()
        await desktop.claim()
        desktop.type("a")
        await until(lambda: state.clients["s1"].written() == [b"a"], what="the daemon wrote it")
        old_core = session.terminal
        assert isinstance(old_core, TerminalCore)
        cursor = replay_end(desktop)

        await state.connect()

        # Moved in one instant: the session holds a mirror, the gateway the core, and
        # the daemon's own core is retired with its browser told where to go.
        assert isinstance(session.terminal, RemoteTerminal)
        assert old_core.retired == (CLOSE_USE_GATEWAY, b"moved")
        await until(lambda: "s1" in state.gateway.sessions, what="the gateway holds it")
        await asyncio.wait_for(desktop.task, timeout=5)
        assert desktop.socket.closed is not None
        assert desktop.socket.closed[0] == CLOSE_USE_GATEWAY
        # The accounting went with it, so a delivery judged before the move still
        # means the same thing after it.
        assert state.gateway.sessions["s1"].core.input_revision == old_core.input_revision == 1

        # The browser reconnects to the gateway with the cursor it had: a delta.
        state.seed_gateway_ring("s1")
        state.feed("s1", b"after the move\r\n")
        phone = state.gateway_browser("s1", "desktop-2")
        phone.send(
            {
                "type": "attach_ready",
                "cols": 100,
                "rows": 30,
                "since": cursor["position"],
                "stream": cursor["stream"],
            }
        )
        await until(lambda: bool(phone.frames("replay_end")), what="the reattach")
        assert replay_end(phone)["reason"] == "delta"
        assert b"".join(phone.socket.binary) == b"after the move\r\n"

        # Typing now reaches the child from the gateway, never from the daemon, and the
        # daemon still applies what it means.
        await phone.claim()
        phone.type("b")
        await until(lambda: state.gateway_pty.writes == ["b"], what="the gateway wrote it")
        await until(lambda: state.hosts["s1"].typed == ["a", "b"], what="the daemon heard it")
        assert state.clients["s1"].written() == [b"a"]
        assert session.input_revision == 2


async def test_a_keystroke_that_arrives_after_the_move_is_not_written_by_the_daemon() -> None:
    async with rig() as state:
        session = state.add()
        desktop = state.daemon_browser(session, "desktop-1")
        await desktop.attach()
        await desktop.claim()

        await state.connect()
        # Already queued on the daemon's socket when the terminal moved.
        desktop.type("late")
        await asyncio.wait_for(desktop.task, timeout=5)

        assert state.clients["s1"].written() == []
        assert desktop.socket.closed is not None
        assert desktop.socket.closed[0] == CLOSE_USE_GATEWAY


async def test_a_retired_core_touches_the_pty_no_more() -> None:
    session, client = supervised_session()
    core = session.terminal
    assert isinstance(core, TerminalCore)
    core.set_viewport("c1", 100, 30, hidden=False)
    assert core.apply_geometry() is True
    resizes = [header for header, _ in client.notices if header.get("t") == "resize"]

    core.retire(CLOSE_USE_GATEWAY, b"moved")

    with pytest.raises(WriteRefused) as refused:
        core.write("x")
    assert refused.value.reason == "moved"
    core.set_viewport("c2", 80, 20, hidden=False)
    core.drop_viewport("c1")
    assert core.apply_geometry() is False
    assert await core.repaint_current_geometry() is False
    assert [header for header, _ in client.notices if header.get("t") == "resize"] == resizes
    assert client.written() == []

    # A socket that reaches a retired core is refused before it is served.
    late = FakeSocket()
    await serve_connection(late, core, DaemonHost(), connection_id="late")
    assert late.closed == (CLOSE_USE_GATEWAY, b"moved")
    assert late.sent == []


async def test_only_sessions_the_gateway_can_reach_move() -> None:
    async with rig() as state:
        live = state.add("live")
        ended = state.add("ended", state="exited")
        cold = state.add("cold")
        cold.record.cold = True
        in_process = state.add("local")
        in_process.pty = cast(Any, RecordingPty())

        await state.connect()

        assert isinstance(live.terminal, RemoteTerminal)
        for stays in (ended, cold, in_process):
            assert isinstance(stays.terminal, TerminalCore), stays.record.id
        await until(lambda: set(state.gateway.sessions) == {"live"}, what="only the live one")


async def test_a_session_started_while_connected_goes_straight_to_the_gateway() -> None:
    async with rig() as state:
        await state.connect()
        session = state.add("new")
        state.placement.place(session)
        assert isinstance(session.terminal, RemoteTerminal)
        await until(lambda: "new" in state.gateway.sessions, what="announced at once")

        # Nothing is placed on a gateway that is not connected.
        await state.hub.close()
        later = state.add("later")
        state.placement.place(later)
        assert isinstance(later.terminal, TerminalCore)


# --- the gateway gives a terminal back -----------------------------------------------


async def test_a_terminal_comes_back_when_the_gateway_is_gone() -> None:
    async with rig() as state:
        session = state.add()
        await state.connect()
        await until(lambda: "s1" in state.gateway.sessions, what="the gateway holds it")
        phone = state.gateway_browser("s1", "phone-1")
        await phone.attach()
        await phone.claim("phone")
        phone.type("x")
        await until(lambda: session.input_revision == 1, what="the mirror heard the revision")
        remote = session.terminal
        assert isinstance(remote, RemoteTerminal)
        judged = WriteCondition(input_revision=session.input_revision)

        # The gateway goes; its browsers are told to reconnect.
        await state.gateway.close()
        await asyncio.wait_for(phone.task, timeout=5)
        assert phone.socket.closed is not None
        assert phone.socket.closed[0] == CLOSE_GATEWAY_RESTARTING
        await until(lambda: not state.hub.connected, what="the daemon saw it go")
        assert state.placement.take_back_all("the terminal gateway exited") == 1

        core = session.terminal
        assert isinstance(core, TerminalCore)
        assert state.hub.terminals == {}
        # The revision moved on once: the delivery judged against the gateway's count
        # cannot land on whatever the gateway accepted after its last report.
        assert core.input_revision == remote.input_revision + 1
        with pytest.raises(WriteRefused) as refused:
            await core.write_when("paste", condition=judged, accounted=True)
        assert refused.value.reason == "input_revision_moved"
        # The same stream: the daemon's ring never stopped being fed.
        assert core.stream is remote.stream

        # And the daemon serves it again, writing to the child itself.
        desktop = state.daemon_browser(session, "desktop-1")
        await desktop.attach()
        await desktop.claim()
        desktop.type("y")
        await until(lambda: state.clients["s1"].written() == [b"y"], what="the daemon wrote it")
        assert state.gateway_pty.writes == ["x"]


async def test_an_ended_session_comes_back_after_its_browsers_see_the_end() -> None:
    async with rig() as state:
        session = state.add()
        await state.connect()
        await until(lambda: "s1" in state.gateway.sessions, what="the gateway holds it")
        phone = state.gateway_browser("s1", "phone-1")
        await phone.attach()

        session.record.state = "exited"
        # Ended but its exit not yet sent: it stays, so its browsers see the end there.
        state.placement.reconcile()
        assert isinstance(session.terminal, RemoteTerminal)

        session.publish_exit("process_exit")
        state.placement.place(session)

        assert isinstance(session.terminal, TerminalCore)
        await until(lambda: "s1" not in state.gateway.sessions, what="the gateway let it go")
        await asyncio.wait_for(phone.task, timeout=5)
        assert phone.frames("exit"), "the end reached the gateway's browser first"
        assert phone.socket.closed is not None
        assert phone.socket.closed[0] == CLOSE_USE_DAEMON


async def test_a_session_removed_or_replaced_is_released_from_the_gateway() -> None:
    async with rig() as state:
        state.add("gone")
        state.add("replaced")
        await state.connect()
        await until(lambda: len(state.gateway.sessions) == 2, what="both held")

        del state.sessions["gone"]
        successor = state.add("replaced")  # a new Session object under the same id
        state.placement.reconcile()

        assert "gone" not in state.hub.terminals
        assert isinstance(successor.terminal, RemoteTerminal)
        assert state.hub.terminals["replaced"] is successor.terminal
        await until(lambda: set(state.gateway.sessions) == {"replaced"}, what="released")


# --- a daemon restart --------------------------------------------------------------


async def test_a_restarted_daemon_takes_over_without_moving_a_terminal() -> None:
    """The gateway keeps the terminal and its browsers across the daemon's restart, and
    the successor applies what was typed while nobody was listening."""
    async with rig() as state:
        session = state.add()
        await state.connect()
        await until(lambda: "s1" in state.gateway.sessions, what="the gateway holds it")
        hosted = state.gateway.sessions["s1"]
        phone = state.gateway_browser("s1", "phone-1")
        await phone.attach()
        await phone.claim("phone")

        await state.hub.close()  # the daemon restarts
        phone.type("typed while away")
        await until(
            lambda: state.gateway_pty.writes == ["typed while away"], what="still echoing"
        )

        # The successor: its own sessions (rebuilt from the supervisor), its own hub.
        rebuilt, _client = supervised_session()
        state.sessions["s1"] = rebuilt
        state.hosts.pop("s1")
        state.hub = RemoteHub(generation="gen-2")
        state.placement = TerminalPlacement(
            state.hub, sessions=lambda: state.sessions, host_for=state.host_for
        )
        await state.connect()

        assert isinstance(rebuilt.terminal, RemoteTerminal)
        assert state.gateway.sessions["s1"] is hosted, "the gateway kept the same terminal"
        assert not phone.task.done(), "and its browser never noticed"
        await until(
            lambda: state.hosts["s1"].typed == ["typed while away"],
            what="the successor applied what it missed",
        )
        assert session is not rebuilt


# --- the daemon's route, the liveness ping, and the wiring ----------------------------


def gateway_config(tmp_path: Path) -> SimpleNamespace:
    return SimpleNamespace(
        terminal_gateway_enabled=True,
        data_dir=tmp_path,
        port=8765,
        terminal_gateway_listen_port=lambda: 8766,
        log_level="INFO",
        gil_switch_interval_ms=5.0,
    )


async def test_the_daemon_route_waits_to_learn_which_process_serves_a_terminal(
    tmp_path: Path,
) -> None:
    """A restarted daemon must not serve a terminal its old gateway may still carry."""
    session, client = supervised_session()
    host = GatewayHost(gateway_config(tmp_path))
    host.placement = TerminalPlacement(
        host.hub, sessions=lambda: {"s1": session}, host_for=lambda _session: DaemonHost()
    )
    app = web.Application()
    app[keys.SESSIONS] = cast(Any, SimpleNamespace(resolve=lambda _sid: session))
    app[keys.TERMINAL_GATEWAY] = host
    app.router.add_get("/pty/{sid}", pty_ws)

    async def first_connection_settles() -> None:
        await asyncio.sleep(0.3)
        assert host.placement is not None
        host.placement._to_gateway(session)
        host.settled.set()

    settling = asyncio.create_task(first_connection_settles())
    async with TestClient(TestServer(app)) as http:
        began = time.monotonic()
        ws = await http.ws_connect("/pty/s1")
        await ws.receive()
        assert ws.close_code == CLOSE_USE_GATEWAY
        assert time.monotonic() - began >= 0.25, "answered before the gateway was known"
        await ws.close()
    await settling
    assert client.written() == []


async def test_a_ping_is_answered_and_moves_the_liveness_reading() -> None:
    token = new_token()
    gateway = GatewayHub(token=token, pty_for=lambda _sid: RecordingPty())
    port = await gateway.start()
    hub = RemoteHub()
    try:
        assert await hub.ping(1.0) is None, "nothing to ask before connecting"
        await hub.connect("127.0.0.1", port, token=token)
        # Zeroed rather than compared with a later reading: the monotonic clock's
        # granularity on Windows is coarser than the gap a test can afford to wait.
        hub.last_heard = 0.0
        assert await hub.ping(5.0) is True
        assert hub.last_heard > 0.0, "the answer itself moved the reading"
    finally:
        await hub.close()
        await gateway.close()


async def test_a_gateway_that_never_agreed_ping_is_not_asked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Silence from a gateway that cannot answer is not evidence of anything."""
    from swe_mux.terminal_core import gateway as gateway_module
    from swe_mux.terminal_core.protocol import CAP_DIAGNOSTICS

    monkeypatch.setattr(gateway_module, "GATEWAY_CAPABILITIES", (CAP_DIAGNOSTICS,))
    token = new_token()
    gateway = GatewayHub(token=token, pty_for=lambda _sid: RecordingPty())
    port = await gateway.start()
    hub = RemoteHub()
    try:
        await hub.connect("127.0.0.1", port, token=token)
        assert await hub.ping(0.5) is None
    finally:
        await hub.close()
        await gateway.close()


async def test_the_composition_root_installs_the_placement_and_the_metering(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from swe_mux.network_usage import NetworkUsage
    from swe_mux.server import _start_terminal_gateway

    async def not_started(_self: GatewayHost) -> None:
        return None

    monkeypatch.setattr(GatewayHost, "start", not_started)
    sessions = SimpleNamespace(sessions={}, terminal_placement=None)
    usage = NetworkUsage()
    app = web.Application()
    app[keys.DAEMON_GENERATION] = "gen-1"
    app[keys.SESSIONS] = cast(Any, sessions)
    app[keys.NETWORK_USAGE] = usage
    config = Config(data_dir=tmp_path, terminal_gateway_enabled=True)

    host = await _start_terminal_gateway(app, config, cast(Any, object()))

    assert app[keys.TERMINAL_GATEWAY] is host
    assert host.placement is not None
    assert sessions.terminal_placement == host.placement.place
    assert host.hub.on_metering == usage.merge_remote

    # Without a supervisor nothing is placed, because there is nothing to carry.
    bare = SimpleNamespace(sessions={}, terminal_placement=None)
    app2 = web.Application()
    app2[keys.DAEMON_GENERATION] = "gen-1"
    app2[keys.SESSIONS] = cast(Any, bare)
    unsupervised = await _start_terminal_gateway(app2, config, None)
    assert unsupervised.placement is None and bare.terminal_placement is None
