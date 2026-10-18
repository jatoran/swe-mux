"""Ending a terminal gateway from a process that is not its daemon (`gateway_discovery`).

`DAEMON_LOAD_ISOLATION.md` item 33. The staged swap has to end this data directory's
gateway before it renames the bundle the gateway runs from, and it does so from the
checkout's redeploy script or from the frozen console client - neither of which is
the daemon, and the second of which cannot import aiohttp. So everything here runs
against real processes: the gateway itself for the polite path, and a stand-in that
only sleeps for the paths where the gateway cannot be asked.
"""

from __future__ import annotations

import asyncio
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import psutil
import pytest

from swe_mux import gateway_discovery
from swe_mux.gateway_discovery import (
    STOP_NONE,
    STOP_QUIT,
    STOP_TERMINATED,
    STOP_UNIDENTIFIED,
    discovery_path,
    live_identity,
    read_discovery,
    stop_gateway,
)
from swe_mux.terminal_core import RemoteHub, TerminalStream, protocol
from swe_mux.terminal_gateway import gateway_command
from tests.support.settle import until
from tests.support.terminal_rig import DaemonHost, descriptor


def _closed_port() -> int:
    """A loopback port nothing is listening on."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture
def sleeper() -> Iterator[subprocess.Popen[bytes]]:
    """A process that is not a gateway and will never answer one's control port."""
    process = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(120)"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        yield process
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=15)


def _record(pid: int, *, created_at: float | None) -> dict[str, Any]:
    record: dict[str, Any] = {
        "pid": pid,
        "port": 1,
        "control_port": _closed_port(),
        "token": "not-a-gateway",
        "build_id": "stand-in",
        "protocol": gateway_discovery.GATEWAY_PROTOCOL,
        "daemon_port": 1,
    }
    if created_at is not None:
        record["created_at"] = created_at
    return record


def _write(data_dir: Path, record: dict[str, Any]) -> None:
    import json

    discovery_path(data_dir).write_text(json.dumps(record), encoding="utf-8")


def test_a_running_gateway_is_asked_to_quit_and_goes(tmp_path: Path) -> None:
    """The polite path, end to end, against the real gateway process.

    Exit code 0 and the record removed are the gateway's own quit path - a
    termination would leave the record behind and exit non-zero on POSIX - so they
    are what distinguish "asked" from "ended".
    """
    command = gateway_command(
        data_dir=tmp_path, daemon_port=1, port=0, executable=sys.executable, frozen=False
    )
    gateway = subprocess.Popen(
        [*command, "--idle-exit-seconds", "60"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 60
        while live_identity(tmp_path) is None:
            assert gateway.poll() is None, f"the gateway exited with {gateway.returncode}"
            assert time.monotonic() < deadline, "the gateway never wrote its record"
            time.sleep(0.1)
        said: list[str] = []

        stopped = stop_gateway(tmp_path, say=said.append)

        assert stopped.outcome == STOP_QUIT, stopped
        assert stopped.stopped and stopped.pid is not None
        assert gateway.wait(timeout=15) == 0
        assert read_discovery(tmp_path) is None
        assert any("asking terminal gateway" in line for line in said), said
        # Nothing is left to stop.
        assert stop_gateway(tmp_path).outcome == STOP_NONE
    finally:
        if gateway.poll() is None:
            gateway.kill()
        gateway.wait(timeout=15)


async def test_a_gateway_with_something_to_say_still_hears_quit(tmp_path: Path) -> None:
    """The live failure of 2026-10-18, reproduced.

    A gateway treats every authenticated control connection as its daemon and sends
    it what it holds the moment it arrives: buffered events and a state flush for
    each session. A quit sender that closed straight after sending left those unread,
    Windows answered the close with a reset, and the reset discarded the gateway's
    unread copy of `quit` - on the operator's machine the connection was accepted
    and aborted within a millisecond, and the gateway was terminated ten seconds
    later instead of quitting. A gateway with no sessions sends nothing, which is
    why the plain quit test above passed throughout.
    """
    command = gateway_command(
        data_dir=tmp_path, daemon_port=1, port=0, executable=sys.executable, frozen=False
    )
    gateway = subprocess.Popen(  # noqa: ASYNC220 - a long-lived child, waited on below
        [*command, "--idle-exit-seconds", "60"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    hub = RemoteHub(generation="gen-1")
    try:
        await until(lambda: live_identity(tmp_path) is not None, seconds=60, what="it started")
        record = read_discovery(tmp_path)
        assert record is not None
        await hub.connect("127.0.0.1", int(record["control_port"]), token=str(record["token"]))
        stream = TerminalStream(
            64 * 1024,
            attach_replay_bytes=None,
            stream_id="daemon-ring",
            remote_stream=lambda: "",
            rebuilt=lambda: False,
        )
        hub.adopt(stream=stream, descriptor=descriptor(), host=DaemonHost())
        # Answered in order, so the gateway has taken the session once this returns.
        assert await hub.ping(10.0) is True
        # The daemon goes; the gateway keeps the session, as it does through a restart.
        await hub.close()

        stopped = await asyncio.to_thread(stop_gateway, tmp_path)

        assert stopped.outcome == STOP_QUIT, stopped
        assert await asyncio.to_thread(gateway.wait, 15) == 0
    finally:
        await hub.close()
        if gateway.poll() is None:
            gateway.kill()
        await asyncio.to_thread(gateway.wait, 15)


def test_a_gateway_that_cannot_be_asked_is_terminated_by_identity(
    tmp_path: Path, sleeper: subprocess.Popen[bytes]
) -> None:
    created = psutil.Process(sleeper.pid).create_time()
    _write(tmp_path, _record(sleeper.pid, created_at=created))

    stopped = stop_gateway(tmp_path, quit_grace=0.2, terminate_grace=10.0)

    assert stopped.outcome == STOP_TERMINATED, stopped
    assert sleeper.wait(timeout=15) is not None


def test_a_recycled_pid_is_never_signalled(
    tmp_path: Path, sleeper: subprocess.Popen[bytes]
) -> None:
    """The same number with another creation time is another process."""
    created = psutil.Process(sleeper.pid).create_time()
    _write(tmp_path, _record(sleeper.pid, created_at=created - 1000.0))

    stopped = stop_gateway(tmp_path, quit_grace=0.2, terminate_grace=0.2)

    assert stopped.outcome == STOP_NONE, stopped
    assert sleeper.poll() is None, "a process the record does not name was ended"


def test_a_record_without_a_creation_time_is_asked_but_never_signalled(
    tmp_path: Path, sleeper: subprocess.Popen[bytes]
) -> None:
    _write(tmp_path, _record(sleeper.pid, created_at=None))

    stopped = stop_gateway(tmp_path, quit_grace=0.3, terminate_grace=0.2)

    assert stopped.outcome == STOP_UNIDENTIFIED, stopped
    assert not stopped.stopped
    assert sleeper.poll() is None


@pytest.mark.skipif(sys.platform == "win32", reason="Windows has no zombie processes")
def test_an_exited_child_its_parent_has_not_reaped_is_gone() -> None:
    """A POSIX zombie has exited and holds nothing; reading it as alive made a daemon
    wait out the whole grace on a gateway it had already asked to quit (measured on
    Linux: the gateway test's `quit` took 15 s longer than the restart test's)."""
    from swe_mux.daemon_recovery import process_state

    child = subprocess.Popen([sys.executable, "-c", "pass"])
    try:
        created = psutil.Process(child.pid).create_time()
        deadline = time.monotonic() + 30
        while psutil.Process(child.pid).status() != psutil.STATUS_ZOMBIE:
            assert time.monotonic() < deadline, "the child never exited"
            time.sleep(0.05)
        assert not gateway_discovery.process_alive((child.pid, created))
        assert process_state({"pid": child.pid, "created_at": created}) == "gone"
    finally:
        child.wait(timeout=15)


def test_no_record_and_an_unusable_one_are_nothing_to_stop(tmp_path: Path) -> None:
    assert stop_gateway(tmp_path).outcome == STOP_NONE
    discovery_path(tmp_path).write_text("{not json", encoding="utf-8")
    assert stop_gateway(tmp_path).outcome == STOP_NONE
    _write(tmp_path, {"pid": "1"})
    assert stop_gateway(tmp_path).outcome == STOP_NONE


def test_the_restated_protocol_matches_the_gateways() -> None:
    """Restated because `terminal_core` imports aiohttp; held equal here."""
    assert gateway_discovery.GATEWAY_PROTOCOL == protocol.GATEWAY_PROTOCOL_VERSION
    assert gateway_discovery.QUIT == protocol.QUIT


def test_the_swap_and_this_module_import_without_aiohttp() -> None:
    """The frozen console client runs the swap and carries no aiohttp
    (`packaging/swe_mux_cli.spec`); a module-scope import of it here would be a
    `ModuleNotFoundError` in the one process that installs updates."""
    probe = (
        "import sys; sys.modules['aiohttp'] = None; "
        "import swe_mux.gateway_discovery, swe_mux.bundle_apply; "
        "assert 'swe_mux.terminal_core' not in sys.modules, 'terminal_core was imported'"
    )
    completed = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, timeout=120
    )
    assert completed.returncode == 0, completed.stderr
