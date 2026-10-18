"""The worker process host: a real worker spawned, called, killed, restarted and stopped.

`DAEMON_LOAD_ISOLATION.md` item 19. Every test that says "worker" here starts a
real child (`python -m swe_mux.worker`, the source-mode command) on an
OS-allocated loopback port under `tmp_path`, because the properties that matter -
the token arriving on stdin, the hello naming the pid that was spawned, a killed
child coming back, a stopped host leaving nothing behind - are properties of a
process, not of a fake.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import sys
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psutil
import pytest

from swe_mux import framed_ipc, worker, worker_host
from swe_mux.framed_ipc import FramedPeer, HandshakeRefused, RemoteError
from swe_mux.processes import is_session_helper_command
from swe_mux.worker_host import (
    STATE_DISABLED,
    STATE_RUNNING,
    STATE_STOPPED,
    WorkerHost,
    WorkerUnavailable,
    worker_command,
)
from tests.support.settle import until

READY_SECONDS = 60.0


@dataclass
class FakeConfig:
    data_dir: Path
    worker_process_enabled: bool = True
    log_level: str = "INFO"
    gil_switch_interval_ms: float = 5.0


def alive(pid: int | None) -> bool:
    if pid is None:
        return False
    try:
        return psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False


@contextlib.asynccontextmanager
async def hosting(tmp_path: Path, **kwargs: Any) -> AsyncIterator[WorkerHost]:
    host = WorkerHost(FakeConfig(tmp_path), restart_initial=0.1, **kwargs)
    await host.start()
    try:
        yield host
    finally:
        await asyncio.wait_for(host.stop(), timeout=30)


async def ready(host: WorkerHost) -> None:
    assert await host.wait_ready(READY_SECONDS), host.health()


# --- the command -------------------------------------------------------------------


def test_the_frozen_worker_is_an_entry_flag_and_never_a_session_helper() -> None:
    frozen = worker_command(
        port=1234,
        data_dir=Path("C:/data"),
        log_level="INFO",
        switch_interval_ms=5.0,
        daemon_pid=42,
        executable="C:/app/swe-mux.exe",
        frozen=True,
    )
    assert frozen[:2] == ["C:/app/swe-mux.exe", "--worker-child"]
    assert "-m" not in frozen
    # The test the redeploy and the process inspector apply to the app's own image.
    assert not is_session_helper_command(" ".join(frozen))
    assert frozen[frozen.index("--port") + 1] == "1234"
    assert frozen[frozen.index("--daemon-pid") + 1] == "42"

    source = worker_command(
        port=1234,
        data_dir=Path("/data"),
        log_level="DEBUG",
        switch_interval_ms=2.5,
        daemon_pid=42,
        executable="/venv/bin/python",
        frozen=False,
    )
    assert source[:3] == ["/venv/bin/python", "-m", "swe_mux.worker"]
    assert source[source.index("--switch-interval-ms") + 1] == "2.5"
    assert "--worker-child" not in source


def test_the_token_is_read_as_one_bounded_line(tmp_path: Path) -> None:
    def from_bytes(data: bytes, limit: int = worker.TOKEN_MAX_BYTES) -> str:
        read, write = os.pipe()
        os.write(write, data)
        os.close(write)
        try:
            return worker.read_token(read, limit=limit)
        finally:
            os.close(read)

    assert from_bytes(b"secret-token\nignored") == "secret-token"
    assert from_bytes(b"no-newline-then-eof") == "no-newline-then-eof"
    assert from_bytes(b"") == ""
    assert from_bytes(b"x" * 50 + b"\n", limit=16) == "", "an oversized line is refused"


async def test_a_worker_whose_daemon_pid_is_gone_stops(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(worker, "DAEMON_CHECK_SECONDS", 0.01)
    monkeypatch.setattr(worker, "_pid_alive", lambda pid: False)
    instance = worker.Worker(services={})
    await asyncio.wait_for(instance._watch_daemon(999_999), timeout=5)
    assert instance._stop.is_set()


# --- a real worker -------------------------------------------------------------------


async def test_a_no_op_service_round_trips_through_a_real_worker(tmp_path: Path) -> None:
    async with hosting(tmp_path) as host:
        await ready(host)
        reply, payload = await host.call("noop", "echo", {"value": 7}, b"\x00bytes")
        assert reply["echo"] == 7 and payload == b"\x00bytes"
        stats, _ = await host.call("worker", "stats")
        assert stats["pid"] == host.pid and stats["services"] == ["noop"]

        health = host.health()
        assert health["state"] == STATE_RUNNING and health["ready"] is True
        assert health["services"] == ["noop"] and health["spawns"] == 1
        assert alive(host.pid)
        # The daemon's own child - or, under a Windows venv's `python.exe`
        # launcher, the child of the daemon's own child.
        parent = psutil.Process(host.pid).ppid()
        if host.launcher_pid is None:
            assert parent == os.getpid()
        else:
            assert parent == host.launcher_pid
            assert psutil.Process(host.launcher_pid).ppid() == os.getpid()

        with pytest.raises(RemoteError, match="unknown service"):
            await host.call("nothing", "here")
        with pytest.raises(RemoteError, match="unknown method"):
            await host.call("noop", "nothing")
    log_text = (tmp_path / "worker.log").read_text(encoding="utf-8")
    assert f"starting for daemon pid {os.getpid()}" in log_text
    assert "leaving" in log_text, "the worker logs why it left"


async def test_a_killed_worker_is_restarted_and_rebuilt_from_the_daemon(tmp_path: Path) -> None:
    pushes: list[int] = []

    async def push_state(peer: FramedPeer) -> None:
        pushes.append(len(pushes) + 1)
        await peer.request({"t": "noop.configure", "state": {"generation": pushes[-1]}})

    host = WorkerHost(FakeConfig(tmp_path), restart_initial=0.1)
    host.on_ready(push_state)
    await host.start()
    try:
        await ready(host)
        first = host.pid
        described, _ = await host.call("noop", "describe")
        assert described["state"] == {"generation": 1}

        psutil.Process(first).kill()
        await until(
            lambda: host.ready and host.pid != first,
            seconds=READY_SECONDS,
            what="a second worker, ready",
        )
        assert not alive(first)
        assert host.restarts == 1 and host.spawns == 2
        # The new worker remembers nothing: it holds what the hook pushed it.
        described, _ = await host.call("noop", "describe")
        assert described["state"] == {"generation": 2}
        assert described["configured"] == 1, "configured once, by the hook, in this worker"
        assert pushes == [1, 2]
    finally:
        await asyncio.wait_for(host.stop(), timeout=30)


async def test_stopping_leaves_no_worker_behind(tmp_path: Path) -> None:
    host = WorkerHost(FakeConfig(tmp_path))
    await host.start()
    await ready(host)
    pid, launcher = host.pid, host.launcher_pid
    await asyncio.wait_for(host.stop(), timeout=30)
    assert not alive(pid) and not alive(launcher)
    assert host.state == STATE_STOPPED and host.last_exit_code == 0, host.health()
    with pytest.raises(WorkerUnavailable):
        await host.call("noop", "echo")


async def test_a_worker_whose_connection_closes_leaves_by_itself(tmp_path: Path) -> None:
    """The EOF half of 'dies with the daemon': exit code 0, not a kill."""
    async with hosting(tmp_path, exit_grace=20.0) as host:
        await ready(host)
        first = host.pid
        peer = host._peer
        assert peer is not None
        await peer.close("test closes the daemon's end")
        await until(lambda: not alive(first), seconds=20, what="the worker exited on EOF")
        await until(lambda: host.last_exit_code is not None, seconds=20, what="the exit recorded")
        assert host.last_exit_code == 0


async def test_notices_from_the_worker_reach_their_service_handler(tmp_path: Path) -> None:
    seen: list[tuple[str, Any]] = []
    async with hosting(tmp_path) as host:
        host.on_notice(
            "noop", lambda method, header, payload: seen.append((method, header["value"]))
        )
        await ready(host)
        await host.call("noop", "announce", {"value": "hello"})
        await until(lambda: bool(seen), what="the notice")
        assert seen == [("announced", "hello")]
        assert host.notify("noop", "anything") is True


async def test_a_stranger_with_the_token_but_not_the_pid_is_refused(tmp_path: Path) -> None:
    async with hosting(tmp_path) as host:
        await ready(host)
        assert host._server is not None
        with pytest.raises(HandshakeRefused, match="not the worker this daemon started"):
            await framed_ipc.connect(
                "127.0.0.1",
                host._server.port,
                token=host._token,
                protocol=worker.WORKER_PROTOCOL,
                hello={"role": worker.WORKER_ROLE, "pid": os.getpid()},
            )
        with pytest.raises(HandshakeRefused, match="not a worker"):
            await framed_ipc.connect(
                "127.0.0.1",
                host._server.port,
                token=host._token,
                protocol=worker.WORKER_PROTOCOL,
                hello={"role": "gateway", "pid": host.pid},
            )
        assert host.ready, "the real worker is untouched by either refusal"


async def test_the_worker_is_held_in_the_hosts_own_reaper(tmp_path: Path) -> None:
    """A reaper of its own, so it never shares a Job with a supervised session."""

    class RecordingReaper:
        def __init__(self) -> None:
            self.assigned: list[int] = []
            self.closed = False

        def assign(self, pid: int) -> None:
            self.assigned.append(pid)

        def process_ids(self) -> list[int]:
            return list(self.assigned)

        def create_child(self) -> RecordingReaper:
            raise AssertionError("the worker needs no nested reaper")

        def close(self) -> None:
            self.closed = True

    made: list[RecordingReaper] = []

    def factory() -> RecordingReaper:
        made.append(RecordingReaper())
        return made[-1]

    async with hosting(tmp_path, reaper_factory=factory) as host:
        await ready(host)
        spawned = host.launcher_pid or host.pid
        assert len(made) == 1 and made[0].assigned == [spawned]
    assert made[0].closed, "stopping closes the reaper, ending anything left in it"


async def test_a_daemon_that_dies_outright_takes_its_worker_with_it(tmp_path: Path) -> None:
    """No teardown, no shutdown request: the process is simply killed.

    This proves the property, not which of the three mechanisms delivered it -
    the reaper's Job closing, the control connection's EOF, or the worker's own
    check that its daemon pid still exists - and that is deliberate: they are
    redundant on purpose, and a user only ever sees whether a worker outlived
    its daemon.
    """
    probe = Path(__file__).parent / "support" / "worker_daemon_probe.py"
    daemon = await asyncio.create_subprocess_exec(
        sys.executable,
        str(probe),
        str(tmp_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        cwd=str(Path(__file__).resolve().parents[1]),
    )
    try:
        assert daemon.stdout is not None
        line = await asyncio.wait_for(daemon.stdout.readline(), timeout=READY_SECONDS + 10)
        report = __import__("json").loads(line)
        assert report["ready"] is True, report
        pids = [pid for pid in (report["pid"], report["launcher_pid"]) if pid]
        assert all(alive(pid) for pid in pids)
        # Kill the probe's whole interpreter chain as an outside kill would.
        targets = [psutil.Process(daemon.pid), *psutil.Process(daemon.pid).children()]
        for target in targets:
            if target.pid not in pids:
                with contextlib.suppress(psutil.Error):
                    target.kill()
        await until(
            lambda: not any(alive(pid) for pid in pids),
            seconds=30,
            what=f"the worker {pids} died with its daemon",
        )
    finally:
        with contextlib.suppress(ProcessLookupError):
            daemon.kill()
        await daemon.wait()


# --- failure shapes -------------------------------------------------------------------


def _python(code: str) -> worker_host.CommandFactory:
    def command(**_: Any) -> list[str]:
        return [sys.executable, "-c", code]

    return command


async def test_a_worker_that_dies_before_its_hello_is_restarted_with_its_exit_code(
    tmp_path: Path,
) -> None:
    async with hosting(tmp_path, command=_python("raise SystemExit(3)")) as host:
        await until(lambda: host.restarts >= 2, seconds=30, what="two restarts")
        assert host.last_exit_code == 3
        assert "exited with code 3 before its hello" in host.last_error
        with pytest.raises(WorkerUnavailable):
            await host.call("noop", "echo")


async def test_a_worker_that_never_says_hello_is_killed_at_the_deadline(tmp_path: Path) -> None:
    marker = "time.sleep(121)"
    hung = _python(f"import time; {marker}")

    def hung_children() -> list[int]:
        found = []
        for child in psutil.Process().children(recursive=True):
            with contextlib.suppress(psutil.Error):
                if marker in " ".join(child.cmdline()) and alive(child.pid):
                    found.append(child.pid)
        return found

    async with hosting(tmp_path, command=hung, ready_timeout=1.0, exit_grace=0.5) as host:
        await until(lambda: host.restarts >= 2, seconds=30, what="two deadlines and restarts")
        assert "no hello within" in host.last_error
        assert len(hung_children()) <= 1, "each hung worker is killed before the next is spawned"
    assert hung_children() == [], "a hung worker is killed, not abandoned"


async def test_a_disabled_worker_is_never_spawned(tmp_path: Path) -> None:
    host = WorkerHost(FakeConfig(tmp_path, worker_process_enabled=False))
    await host.start()
    assert host.health()["state"] == STATE_DISABLED and host.pid is None
    assert await host.wait_ready(0.1) is False
    with pytest.raises(WorkerUnavailable):
        await host.call("noop", "echo")
    assert host.notify("noop", "echo") is False
    await host.stop()
    assert not (tmp_path / "worker.log").exists()


async def test_health_is_serializable_in_every_state(tmp_path: Path) -> None:
    import json

    host = WorkerHost(FakeConfig(tmp_path))
    json.dumps(host.health())
    await host.start()
    try:
        json.dumps(host.health())
        await ready(host)
        json.dumps(host.health())
    finally:
        await host.stop()
    json.dumps(host.health())
