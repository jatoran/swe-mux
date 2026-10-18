"""DAEMON_LOAD_ISOLATION item 29 - the daemon owns the terminal gateway's lifecycle.

Every test here runs the real gateway as a process of its own (`python -m
swe_mux.terminal_gateway`, the source-mode command) and drives it with the real
`GatewayHost`, so what is proven is what a daemon does: start one, find one a
previous daemon left running, replace one built from other code, start one again
when it dies, leave it running on a restart and end it on a quit. No supervisor is
needed: a gateway announces itself and takes its daemon before it has one.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import socket
import subprocess
import sys
import time
import types
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import psutil
import pytest

from swe_mux import gateway_host as gateway_host_module
from swe_mux.config import Config
from swe_mux.daemon_recovery import process_state, terminate_generation
from swe_mux.doctor import _terminal_transport_check
from swe_mux.gateway_host import (
    STATE_DISABLED,
    STATE_RUNNING,
    STATE_UNAVAILABLE,
    TRANSPORT_DAEMON,
    TRANSPORT_GATEWAY,
    GatewayHost,
    terminal_transport,
)
from swe_mux.terminal_core import RemoteTerminal, TerminalCore
from swe_mux.terminal_gateway import (
    DISCOVERY_FILENAME,
    LOCK_FILENAME,
    InstanceLock,
    gateway_command,
    read_discovery,
)
from swe_mux.terminal_placement import TerminalPlacement
from tests.support.settle import until
from tests.support.supervised_session import supervised_session
from tests.support.terminal_rig import DaemonHost

#: Long enough for a gateway to be found again, short enough that one a failed test
#: leaves behind is gone before anyone notices it.
TEST_IDLE_EXIT_SECONDS = 30


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


@dataclass
class FakeConfig:
    data_dir: Path
    port: int
    gateway_port: int
    terminal_gateway_enabled: bool = True
    log_level: str = "INFO"
    gil_switch_interval_ms: float = 5.0

    def terminal_gateway_listen_port(self) -> int:
        return self.gateway_port


def short_lived_command(**options: Any) -> list[str]:
    return [*gateway_command(**options), "--idle-exit-seconds", str(TEST_IDLE_EXIT_SECONDS)]


async def loopback_only() -> Sequence[str]:
    return ["127.0.0.1"]


def make_host(config: FakeConfig, **changes: Any) -> GatewayHost:
    options: dict[str, Any] = {
        "generation": "gen-test",
        "hosts": loopback_only,
        "command": short_lived_command,
        "restart_initial": 0.1,
        "restart_max": 0.5,
        "quit_grace": 10.0,
    }
    options.update(changes)
    return GatewayHost(config, **options)


@pytest.fixture
def config(tmp_path: Path) -> FakeConfig:
    return FakeConfig(data_dir=tmp_path, port=free_port(), gateway_port=free_port())


@contextlib.asynccontextmanager
async def started(host: GatewayHost) -> AsyncIterator[GatewayHost]:
    """A started host, quit at the end whatever the test did to it."""
    await host.start()
    try:
        await until(lambda: host.ready, seconds=60, what="the gateway is connected")
        yield host
    finally:
        await host.stop(quit=True)


def alive(pid: int, created_at: float) -> bool:
    return process_state({"pid": pid, "created_at": created_at}) == "alive"


def identity(host: GatewayHost) -> tuple[int, float]:
    gateway = host.gateway
    assert gateway is not None
    return int(gateway["pid"]), float(gateway["created_at"])


def end_process(pid: int) -> None:
    with contextlib.suppress(psutil.Error):
        process = psutil.Process(pid)
        process.kill()
        process.wait(timeout=10)


# --- starting, finding, stopping -------------------------------------------------------


async def test_a_daemon_starts_a_gateway_and_connects_to_it(config: FakeConfig) -> None:
    host = make_host(config)
    async with started(host):
        record = read_discovery(config.data_dir)
        assert record is not None
        assert host.pid == record["pid"]
        assert record["port"] == config.gateway_port
        assert record["daemon_port"] == config.port
        assert (host.spawns, host.adoptions, host.replacements) == (1, 0, 0)
        assert host.transport() == {
            "transport": TRANSPORT_GATEWAY,
            "state": STATE_RUNNING,
            "port": config.gateway_port,
        }
        health = host.health()
        assert health["spawned_here"] is True
        assert health["build_id"] == record["build_id"]
        assert "token" not in (health["gateway"] or {})
        # The gateway now has a daemon, which is half of what its health waits for.
        assert host.hub.connected


async def test_a_quitting_daemon_takes_the_gateway_with_it(config: FakeConfig) -> None:
    host = make_host(config)
    await host.start()
    await until(lambda: host.ready, seconds=60, what="the gateway is connected")
    pid, created = identity(host)
    process = host._process
    assert process is not None

    await host.stop(quit=True)

    assert not alive(pid, created)
    # It left because it was asked, not because it was killed: a clean exit, and
    # its discovery record taken down with it.
    assert process.wait(timeout=10) == 0
    assert not (config.data_dir / DISCOVERY_FILENAME).exists()
    assert host.transport()["transport"] == TRANSPORT_DAEMON


async def test_a_restarted_daemon_adopts_the_gateway_it_left_running(
    config: FakeConfig,
) -> None:
    first = make_host(config)
    await first.start()
    await until(lambda: first.ready, seconds=60, what="the first daemon's gateway")
    pid, created = identity(first)

    await first.stop(quit=False)  # a detach restart: terminals stay carried

    assert alive(pid, created)
    second = make_host(config)
    async with started(second):
        assert second.pid == pid
        assert (second.spawns, second.adoptions) == (0, 1)
        assert second.health()["spawned_here"] is False
        assert second.transport()["transport"] == TRANSPORT_GATEWAY
    assert not alive(pid, created)


async def test_a_gateway_built_from_other_code_is_asked_to_quit_and_replaced(
    config: FakeConfig,
) -> None:
    old_daemon = make_host(config)
    await old_daemon.start()
    await until(lambda: old_daemon.ready, seconds=60, what="the old build's gateway")
    old_pid, old_created = identity(old_daemon)
    old_process = old_daemon._process
    assert old_process is not None
    await old_daemon.stop(quit=False)

    # The successor computes a different build id, as a redeployed daemon does.
    new_daemon = make_host(config, build_id=lambda: "a-different-build")
    async with started(new_daemon):
        assert new_daemon.replacements == 1
        assert "a-different-build" in new_daemon.last_replacement
        assert new_daemon.spawns == 1
        assert new_daemon.pid != old_pid
        assert not alive(old_pid, old_created)
        # Orchestrated: the old gateway exited on the request, cleanly.
        assert old_process.wait(timeout=10) == 0
        # The gateway this daemon started is never replaced for its build, even
        # though it, too, reports a build this daemon does not compute.
        await new_daemon.hub.close()
        await until(
            lambda: new_daemon.ready and new_daemon.restarts >= 1,
            seconds=30,
            what="the daemon reconnects to its own gateway",
        )
        assert new_daemon.replacements == 1
        assert new_daemon.spawns == 1


@pytest.mark.parametrize(
    ("changed", "reason"),
    [("gateway_port", "listens on port"), ("port", "serves the daemon on port")],
)
async def test_a_gateway_started_for_other_ports_is_replaced(
    config: FakeConfig, changed: str, reason: str
) -> None:
    """A port change applies at the next daemon start, so that start replaces it."""
    old_daemon = make_host(config)
    await old_daemon.start()
    await until(lambda: old_daemon.ready, seconds=60, what="the gateway on the old ports")
    old_pid, old_created = identity(old_daemon)
    await old_daemon.stop(quit=False)

    setattr(config, changed, free_port())
    new_daemon = make_host(config)
    async with started(new_daemon):
        assert new_daemon.replacements == 1
        assert reason in new_daemon.last_replacement
        assert not alive(old_pid, old_created)
        record = read_discovery(config.data_dir)
        assert record is not None
        assert (record["port"], record["daemon_port"]) == (config.gateway_port, config.port)


async def test_a_gateway_that_speaks_another_protocol_is_ended_and_replaced(
    config: FakeConfig,
) -> None:
    # A process that cannot be asked to quit in this daemon's protocol: it is
    # terminated by its identity instead, after the grace.
    stranger = await asyncio.to_thread(
        subprocess.Popen, [sys.executable, "-c", "import time; time.sleep(120)"]
    )
    try:
        created = psutil.Process(stranger.pid).create_time()
        (config.data_dir / DISCOVERY_FILENAME).write_text(
            json.dumps(
                {
                    "pid": stranger.pid,
                    "created_at": created,
                    "port": config.gateway_port,
                    "control_port": free_port(),
                    "token": "not-ours",
                    "build_id": "old",
                    "protocol": 999,
                    "daemon_port": config.port,
                }
            ),
            encoding="utf-8",
        )
        host = make_host(config, quit_grace=0.5)
        async with started(host):
            assert host.replacements == 1
            assert "protocol 999" in host.last_replacement
            assert stranger.wait(timeout=10) is not None
            assert host.pid != stranger.pid
    finally:
        end_process(stranger.pid)


async def test_a_gateway_that_dies_is_started_again(config: FakeConfig) -> None:
    host = make_host(config)
    async with started(host):
        pid, _created = identity(host)
        end_process(pid)
        await until(
            lambda: host.ready and host.pid != pid,
            seconds=60,
            what="a new gateway replaces the dead one",
        )
        assert host.spawns == 2
        assert host.restarts >= 1
        assert host.adoptions == 0


async def test_a_spawn_that_loses_the_lock_reports_it_and_tries_again(
    config: FakeConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(gateway_host_module, "LOCKED_RECORD_SECONDS", 0.5)
    lock = InstanceLock(config.data_dir / LOCK_FILENAME)
    assert lock.acquire()
    host = make_host(config)
    await host.start()
    try:
        await until(
            lambda: host.last_exit_code == gateway_host_module.EXIT_LOCKED,
            seconds=60,
            what="the spawned gateway is refused by the lock",
        )
        await until(lambda: "holds this data directory" in host.last_error, seconds=30)
        assert not host.ready
        assert host.transport()["transport"] == TRANSPORT_DAEMON
        lock.release()
        await until(lambda: host.ready, seconds=60, what="the next spawn takes the lock")
    finally:
        lock.release()
        await host.stop(quit=True)


async def test_a_host_stopped_while_its_spawn_is_starting_leaves_nothing_behind(
    config: FakeConfig,
) -> None:
    host = make_host(config)
    await host.start()
    await until(lambda: host._process is not None, seconds=30, what="the spawn")
    process = host._process
    assert process is not None
    await host.stop(quit=True)
    assert process.wait(timeout=15) is not None


# --- which process holds the terminals (item 32) ---------------------------------------


def place(host: GatewayHost, *sessions: Any) -> dict[str, Any]:
    """Give ``host`` a placement over these sessions, as the composition root does."""
    registry = {session.record.id: session for session in sessions}
    host.placement = TerminalPlacement(
        host.hub, sessions=lambda: registry, host_for=lambda _session: DaemonHost()
    )
    return registry


async def test_the_gateway_takes_the_terminals_and_gives_them_back_when_it_dies(
    config: FakeConfig,
) -> None:
    # A long restart backoff, so the terminal must come back when the connection
    # closes rather than whenever the next gateway happens to be looked for.
    host = make_host(config, restart_initial=5.0, restart_max=5.0)
    session, _client = supervised_session()
    place(host, session)
    async with started(host):
        assert isinstance(session.terminal, RemoteTerminal)
        assert host.placement is not None and host.placement.handovers == 1
        pid, created = identity(host)
        assert host.holder == (pid, created)

        end_process(pid)

        await until(
            lambda: isinstance(session.terminal, TerminalCore),
            seconds=3,
            what="the terminal comes back as soon as the dead gateway's connection closes",
        )
        assert "exited" in host.placement.last_takeback_reason
        await until(
            lambda: host.ready and host.pid != pid and isinstance(session.terminal, RemoteTerminal),
            seconds=60,
            what="and goes to the gateway that replaces it",
        )
        assert host.placement.handovers == 2


async def test_a_hung_gateway_is_ended_and_its_terminals_come_back(config: FakeConfig) -> None:
    """Silent to its pings and to its own `/health`: ended by its identity, terminals back.

    Suspending the process is the honest stand-in for a hung loop: it keeps its
    sockets, accepts connections into their backlog, and answers nothing.
    """
    host = make_host(config, probe_interval=0.2, probe_timeout=0.5, hung_probes=2)
    session, _client = supervised_session()
    place(host, session)
    async with started(host):
        pid, created = identity(host)
        gateway = psutil.Process(pid)
        gateway.suspend()
        try:
            await until(
                lambda: host.hung_ends == 1 and isinstance(session.terminal, TerminalCore),
                seconds=60,
                what="the hung gateway is ended and its terminals come back",
            )
            assert not alive(pid, created)
            assert "pings unanswered" in host.last_hung_reason
            await until(
                lambda: host.ready and host.pid != pid,
                seconds=60,
                what="a new gateway takes over",
            )
        finally:
            with contextlib.suppress(psutil.Error):
                gateway.resume()
            end_process(pid)


async def test_a_gateway_still_serving_its_browsers_is_never_ended(
    config: FakeConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pings unanswered but `/health` answering: the connection is renewed, the gateway
    and its terminals stay. Killing on the control path's word alone would let a
    daemon-side fault end a healthy gateway."""
    host = make_host(config, probe_interval=0.2, probe_timeout=0.5, hung_probes=2)
    session, _client = supervised_session()
    place(host, session)
    async with started(host):
        pid, created = identity(host)

        async def silent(_timeout: float) -> bool:
            return False

        monkeypatch.setattr(host.hub, "ping", silent)
        await until(lambda: host.restarts >= 1, seconds=30, what="the connection is renewed")
        await until(lambda: host.ready, seconds=30, what="and reconnected")
        assert host.hung_ends == 0
        assert alive(pid, created)
        assert host.pid == pid
        assert isinstance(session.terminal, RemoteTerminal)


async def test_a_late_timer_is_this_daemons_stall_not_the_gateways_silence(
    config: FakeConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    host = make_host(config, probe_interval=0.01, probe_timeout=0.05, hung_probes=2)
    suspected: list[str] = []

    async def suspect(_record: dict[str, Any], why: str) -> None:
        suspected.append(why)

    monkeypatch.setattr(host, "_suspect_hung", suspect)
    peer = types.SimpleNamespace(closed=False)

    async def late(wait_seconds: float) -> bool:
        # The timer that ends this wait fires long after it was due: the daemon's
        # own loop was not running.
        await asyncio.sleep(wait_seconds * 3)
        return False

    monkeypatch.setattr(host.hub, "ping", late)
    watch = asyncio.create_task(host._watch(peer, {"pid": 1, "port": 1}))
    await asyncio.sleep(0.6)
    assert host.probe_misses == 0 and suspected == []

    async def silent(_timeout: float) -> bool:
        return False

    monkeypatch.setattr(host.hub, "ping", silent)
    await until(lambda: bool(suspected), seconds=5, what="on-time silence is counted")
    assert host.probe_misses == 2
    await asyncio.wait_for(watch, timeout=5)


async def test_a_terminal_socket_waits_for_the_first_connection_to_settle(
    config: FakeConfig,
) -> None:
    host = make_host(config)
    session, _client = supervised_session()
    local, _client = supervised_session("local")
    local.pty = cast(Any, types.SimpleNamespace())
    place(host, session, local)

    # A session the gateway can never hold does not wait.
    await asyncio.wait_for(host.settle(local), timeout=0.5)

    waiter = asyncio.create_task(host.settle(session))
    await asyncio.sleep(0.2)
    assert not waiter.done()
    host.settled.set()
    await asyncio.wait_for(waiter, timeout=1)

    # And a gateway that is off never makes anything wait.
    config.terminal_gateway_enabled = False
    off = make_host(config)
    place(off, session)
    await asyncio.wait_for(off.settle(session), timeout=0.5)


def test_a_gateway_holding_terminals_through_a_lost_connection_still_serves_them(
    config: FakeConfig,
) -> None:
    host = make_host(config)
    session, _client = supervised_session()
    place(host, session)
    host.gateway = {"pid": 1, "port": config.gateway_port}
    host.state = gateway_host_module.STATE_BACKOFF
    assert host.transport()["transport"] == TRANSPORT_DAEMON
    assert host.placement is not None
    host.placement._to_gateway(session)
    assert host.transport() == {
        "transport": TRANSPORT_GATEWAY,
        "state": gateway_host_module.STATE_BACKOFF,
        "port": config.gateway_port,
    }


# --- reporting -------------------------------------------------------------------------


async def test_a_disabled_or_unsupervised_gateway_sends_browsers_to_the_daemon(
    config: FakeConfig,
) -> None:
    config.terminal_gateway_enabled = False
    off = make_host(config)
    await off.start()
    assert not off.started
    assert off.transport() == {"transport": TRANSPORT_DAEMON, "state": STATE_DISABLED, "port": None}
    assert terminal_transport(None)["state"] == STATE_DISABLED

    config.terminal_gateway_enabled = True
    no_supervisor = make_host(config)
    no_supervisor.mark_unavailable("no PTY supervisor is attached")
    assert no_supervisor.transport() == {
        "transport": TRANSPORT_DAEMON,
        "state": STATE_UNAVAILABLE,
        "port": None,
        "reason": "no PTY supervisor is attached",
    }


async def test_a_daemon_without_a_supervisor_reports_why_it_runs_no_gateway(
    tmp_path: Path,
) -> None:
    """Enabled, but there is nothing for a gateway to carry: health says so."""
    from aiohttp.test_utils import TestClient, TestServer

    from swe_mux import app_keys as keys
    from swe_mux.server import create_app, wait_runtime_ready

    app = create_app(
        Config(
            data_dir=tmp_path / "data",
            pty_supervisor_enabled=False,
            reconcile_external_history=False,
            terminal_gateway_enabled=True,
        )
    )
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        await wait_runtime_ready(app)
        payload = await (await client.get("/api/health")).json()
        assert payload["terminal_transport"] == {
            "transport": TRANSPORT_DAEMON,
            "state": STATE_UNAVAILABLE,
            "port": None,
            "reason": "no PTY supervisor is attached",
        }
        host = app[keys.TERMINAL_GATEWAY]
        assert host.started is False
        background = await (await client.get("/api/diagnostics/background")).json()
        assert background["terminal_gateway"]["state"] == STATE_UNAVAILABLE
    finally:
        await client.close()
    assert not (tmp_path / "data" / DISCOVERY_FILENAME).exists()


def test_doctor_reads_the_transport() -> None:
    assert _terminal_transport_check(None) is None
    carried = _terminal_transport_check(
        {"transport": "gateway", "state": "running", "port": 8766}
    )
    assert carried is not None and carried["status"] == "ok" and "8766" in carried["detail"]
    off = _terminal_transport_check({"transport": "daemon", "state": "disabled", "port": None})
    assert off is not None and off["status"] == "ok" and off["severity"] == "info"
    down = _terminal_transport_check(
        {"transport": "daemon", "state": "backoff", "port": None, "reason": "bind failed"}
    )
    assert down is not None and down["status"] == "warn" and "bind failed" in down["detail"]


# --- configuration ---------------------------------------------------------------------


def test_the_gateway_port_defaults_to_the_daemons_plus_one(tmp_path: Path) -> None:
    assert Config(data_dir=tmp_path, port=8765).terminal_gateway_listen_port() == 8766
    explicit = Config(data_dir=tmp_path, port=8765, terminal_gateway_port=9100)
    assert explicit.terminal_gateway_listen_port() == 9100
    assert Config(data_dir=tmp_path).terminal_gateway_enabled is False


@pytest.mark.parametrize(
    ("port", "gateway_port", "message"),
    [
        (8765, 8765, "must differ"),
        (65535, 0, "set explicitly"),
        (8765, 70000, "between 1 and 65535"),
    ],
)
def test_an_impossible_gateway_port_is_refused(
    tmp_path: Path, port: int, gateway_port: int, message: str
) -> None:
    from swe_mux.config import _validate

    _validate(Config(data_dir=tmp_path, port=8765))
    with pytest.raises(ValueError, match=f"terminal_gateway_port.*{message}"):
        _validate(
            Config(
                data_dir=tmp_path,
                port=port,
                terminal_gateway_port=gateway_port,
                terminal_gateway_enabled=True,
            )
        )


def test_a_daemon_on_the_last_port_is_valid_while_its_gateway_is_off(tmp_path: Path) -> None:
    """The derived port only has to exist for a daemon that will start a gateway."""
    from swe_mux.config import _validate

    _validate(Config(data_dir=tmp_path, port=65535))
    _validate(
        Config(
            data_dir=tmp_path,
            port=65535,
            terminal_gateway_port=65534,
            terminal_gateway_enabled=True,
        )
    )


# --- desktop recovery ----------------------------------------------------------------


def test_recovery_terminates_the_daemon_and_never_its_gateway(tmp_path: Path) -> None:
    """The recovery record names the gateway, and terminating the daemon spares it."""
    script = tmp_path / "daemon_with_gateway.py"
    script.write_text(
        "import subprocess, sys, time\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
        "print(child.pid, flush=True)\n"
        "time.sleep(120)\n",
        encoding="utf-8",
    )
    daemon = subprocess.Popen([sys.executable, str(script)], stdout=subprocess.PIPE, text=True)
    gateway_pid = 0
    try:
        assert daemon.stdout is not None
        gateway_pid = int(daemon.stdout.readline())
        gateway_created = psutil.Process(gateway_pid).create_time()
        record = {
            "pid": daemon.pid,
            "created_at": psutil.Process(daemon.pid).create_time(),
            "terminal_gateway": {"pid": gateway_pid, "created_at": gateway_created},
        }

        assert terminate_generation(record)

        assert daemon.wait(timeout=10) is not None
        assert alive(gateway_pid, gateway_created)
        # A record that somehow names the gateway as the daemon is refused outright.
        assert not terminate_generation(
            {
                "pid": gateway_pid,
                "created_at": gateway_created,
                "terminal_gateway": {"pid": gateway_pid, "created_at": gateway_created},
            }
        )
        assert alive(gateway_pid, gateway_created)
    finally:
        end_process(daemon.pid)
        if gateway_pid:
            end_process(gateway_pid)
        if daemon.stdout is not None:
            daemon.stdout.close()


async def test_the_build_id_this_daemon_computes_matches_a_fresh_gateways() -> None:
    """The replacement rule compares the two, so they must agree for the same code.

    This process has most of swe-mux loaded, as a daemon does; the child has only
    the gateway's closure, as a gateway does. A digest that depended on what else
    was loaded would replace every gateway on every daemon start.
    """
    from swe_mux.terminal_gateway import gateway_build_id

    started_at = time.monotonic()
    child = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        "from swe_mux.terminal_gateway import gateway_build_id; print(gateway_build_id())",
        stdout=asyncio.subprocess.PIPE,
    )
    output, _ = await child.communicate()
    assert child.returncode == 0, time.monotonic() - started_at
    assert output.decode().strip() == gateway_build_id()
