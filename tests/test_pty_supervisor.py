"""Session-preserving reload: PtyHost contract + supervisor survival tests.

Three layers, matching SESSION_PRESERVING_RELOAD.md:

- §7.1: one behavioral contract suite that both the in-process ``PtyHost`` and
  the supervisor-backed ``RemotePtyHost`` must pass (spawn/write/read/resize/
  exit/exit_status/release/stop).
- §9: the load-bearing survival claim — a ConPTY spawned by a standalone
  supervisor process survives its client dying and can be re-subscribed with
  scrollback intact; ``reap_all_and_exit`` still reaps deterministically.
- §7.4: ``SessionManager`` end-to-end — spawn through the supervisor, drop the
  client, and a second manager (a "restarted daemon") adopts the live session.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.skipif(os.name != "nt", reason="ConPTY supervisor is Windows-only")

if os.name == "nt":
    from swe_mux.pty_host import PtyHost
    from swe_mux.supervisor import SupervisorServer, discovery_path
    from swe_mux.supervisor_client import (
        RemotePtyHost,
        SupervisorClient,
        host_for_adoption,
    )

CMD = os.environ.get("COMSPEC", r"C:\Windows\System32\cmd.exe")
READ_TIMEOUT = 30.0


async def read_until(
    queue: asyncio.Queue[bytes], needle: bytes, *, wait_seconds: float = READ_TIMEOUT
) -> bytes:
    buffer = b""
    deadline = time.monotonic() + wait_seconds
    while needle not in buffer:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise AssertionError(f"timed out waiting for {needle!r}; got {buffer!r}")
        chunk = await asyncio.wait_for(queue.get(), timeout=remaining)
        if chunk == b"":
            raise AssertionError(f"pty ended before {needle!r} appeared; got {buffer!r}")
        buffer += chunk
    return buffer


async def read_until_closed(
    queue: asyncio.Queue[bytes], *, wait_seconds: float = READ_TIMEOUT
) -> None:
    deadline = time.monotonic() + wait_seconds
    while True:
        remaining = deadline - time.monotonic()
        assert remaining > 0, "timed out waiting for the end-of-output sentinel"
        chunk = await asyncio.wait_for(queue.get(), timeout=remaining)
        if chunk == b"":
            return


class SupervisorHarness:
    """In-process supervisor + connected client for protocol-level tests."""

    def __init__(self, tmp_path: Path) -> None:
        self.tmp_path = tmp_path
        self.server: SupervisorServer | None = None
        self._run_task: asyncio.Task[None] | None = None
        self.clients: list[SupervisorClient] = []

    async def start(self) -> SupervisorServer:
        server = SupervisorServer(self.tmp_path / "config.toml", self.tmp_path)
        await server.start()
        self.server = server
        self._run_task = asyncio.create_task(server.run())
        return server

    async def connect(self) -> SupervisorClient:
        client = await SupervisorClient.connect(self.tmp_path)
        self.clients.append(client)
        return client

    async def close(self) -> None:
        for client in self.clients:
            with contextlib.suppress(Exception):
                await client.close()
        if self.server is not None:
            self.server.exit_event.set()
        if self._run_task is not None:
            await asyncio.wait_for(self._run_task, timeout=15)


@pytest.fixture
async def harness(tmp_path: Path):
    instance = SupervisorHarness(tmp_path)
    await instance.start()
    yield instance
    await instance.close()


async def spawn_host(mode: str, harness: SupervisorHarness, tmp_path: Path, sid: str):
    """Spawn one interactive cmd.exe through either implementation."""
    if mode == "local":
        host: Any = PtyHost(CMD, (), str(tmp_path))
        host.prepare()
        await asyncio.to_thread(host.spawn)
        return host, None
    client = await harness.connect()
    host = RemotePtyHost(
        client,
        sid,
        appname=CMD,
        argv=(),
        cwd=str(tmp_path),
        env=dict(os.environ),
        graceful_exit="exit\r",
        max_scrollback=256 * 1024,
    )
    host.prepare()
    await asyncio.to_thread(host.spawn)
    return host, client


# --------------------------------------------------------------------------
# §7.1 — the PtyHost behavioral contract, pinned for both implementations
# --------------------------------------------------------------------------


@pytest.mark.parametrize("mode", ["local", "remote"])
async def test_contract_spawn_write_read_exit_status(
    mode: str, harness: SupervisorHarness, tmp_path: Path
) -> None:
    host, _ = await spawn_host(mode, harness, tmp_path, f"contract-io-{mode}")
    try:
        assert host.pid > 0
        await read_until(host.output_queue, b">")
        assert host.isalive()
        assert host.exit_status() is None
        host.write("echo mux_contract_marker\r")
        output = await read_until(host.output_queue, b"mux_contract_marker")
        assert b"mux_contract_marker" in output
        host.write("exit\r")
        await read_until_closed(host.output_queue)
        deadline = time.monotonic() + 10
        while host.isalive() and time.monotonic() < deadline:  # noqa: ASYNC110 - polls a process
            await asyncio.sleep(0.05)
        assert not host.isalive()
        assert host.exit_status() == 0
    finally:
        if host.isalive():
            await asyncio.to_thread(host.stop, graceful=False)


@pytest.mark.parametrize("mode", ["local", "remote"])
async def test_contract_resize_and_stop(
    mode: str, harness: SupervisorHarness, tmp_path: Path
) -> None:
    host, _ = await spawn_host(mode, harness, tmp_path, f"contract-resize-{mode}")
    try:
        await read_until(host.output_queue, b">")
        host.resize(100, 40)
        assert (host.cols, host.rows) == (100, 40)
        assert host.isalive()
    finally:
        await asyncio.to_thread(host.stop, graceful=True, timeout=2.0)
    deadline = time.monotonic() + 10
    while host.isalive() and time.monotonic() < deadline:  # noqa: ASYNC110 - polls a process
        await asyncio.sleep(0.05)
    assert not host.isalive()


@pytest.mark.parametrize("mode", ["local", "remote"])
async def test_contract_release_refuses_live_pty(
    mode: str, harness: SupervisorHarness, tmp_path: Path
) -> None:
    host, _ = await spawn_host(mode, harness, tmp_path, f"contract-release-{mode}")
    try:
        await read_until(host.output_queue, b">")
        with pytest.raises(RuntimeError):
            host.release()
        host.write("exit\r")
        await read_until_closed(host.output_queue)
        host.release()  # ended: release must succeed
    finally:
        if host.isalive():
            await asyncio.to_thread(host.stop, graceful=False)


# --------------------------------------------------------------------------
# supervisor protocol: survival across client death (§9, in-process)
# --------------------------------------------------------------------------


async def test_session_survives_client_disconnect_and_resubscribes(
    harness: SupervisorHarness, tmp_path: Path
) -> None:
    sid = "survive-1"
    host, first_client = await spawn_host("remote", harness, tmp_path, sid)
    await read_until(host.output_queue, b">")
    host.write("echo before_reload_marker\r")
    await read_until(host.output_queue, b"before_reload_marker")

    assert first_client is not None
    await first_client.close()
    await asyncio.sleep(0.2)
    assert harness.server is not None
    entry = harness.server.sessions[sid]
    assert entry.alive, "session must survive its client going away"

    second_client = await harness.connect()
    infos = {info["sid"]: info for info in second_client.initial_sessions}
    assert infos[sid]["alive"] is True
    adopted = host_for_adoption(second_client, infos[sid])
    adopted.prepare()
    response, replay = await second_client.subscribe(adopted)
    assert response["alive"] is True
    assert b"before_reload_marker" in replay
    assert response["position"] >= len(replay)

    adopted.write("echo after_reload_marker\r")
    await read_until(adopted.output_queue, b"after_reload_marker")
    adopted.write("exit\r")
    await read_until_closed(adopted.output_queue)
    assert adopted.exit_status() == 0


async def test_metadata_round_trips_through_supervisor(
    harness: SupervisorHarness, tmp_path: Path
) -> None:
    sid = "meta-1"
    host, client = await spawn_host("remote", harness, tmp_path, sid)
    assert client is not None
    await read_until(host.output_queue, b">")
    client.queue_meta(sid, {"record": {"id": sid, "name": "renamed"}, "hook_secret": "s3"})
    await client.flush_meta()
    await asyncio.sleep(0.1)

    other = await harness.connect()
    infos = {info["sid"]: info for info in other.initial_sessions}
    assert infos[sid]["meta"]["record"]["name"] == "renamed"
    assert infos[sid]["meta"]["hook_secret"] == "s3"
    await asyncio.to_thread(host.stop, graceful=False)


async def test_hello_requires_valid_token(harness: SupervisorHarness, tmp_path: Path) -> None:
    discovery = json.loads(discovery_path(tmp_path).read_text(encoding="utf-8"))
    forged = dict(discovery, token="not-the-token")
    discovery_path(tmp_path).write_text(json.dumps(forged), encoding="utf-8")
    try:
        with pytest.raises(Exception, match="rejected|token|unavailable"):
            await SupervisorClient.connect(tmp_path)
    finally:
        discovery_path(tmp_path).write_text(json.dumps(discovery), encoding="utf-8")


# --------------------------------------------------------------------------
# §9 — the standalone-process prototype claim, end to end
# --------------------------------------------------------------------------


async def _connect_with_retries(data_dir: Path, deadline_seconds: float = 20.0):
    deadline = time.monotonic() + deadline_seconds
    last: Exception | None = None
    while time.monotonic() < deadline:
        try:
            return await SupervisorClient.connect(data_dir)
        except Exception as exc:  # noqa: BLE001 - retried until the deadline
            last = exc
            await asyncio.sleep(0.25)
    raise AssertionError(f"could not connect to supervisor subprocess: {last}")


async def test_supervisor_process_outlives_client_and_reaps_on_command(
    tmp_path: Path,
) -> None:
    config_file = tmp_path / "config.toml"
    config_file.write_text("", encoding="utf-8")
    process = subprocess.Popen(  # noqa: ASYNC220 - launching the process under test
        [sys.executable, "-m", "swe_mux.supervisor", "--config", str(config_file)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        cwd=str(tmp_path),
    )
    child_pid = -1
    try:
        first = await _connect_with_retries(tmp_path)
        host = RemotePtyHost(
            first,
            "proc-survive",
            appname=CMD,
            argv=(),
            cwd=str(tmp_path),
            env=dict(os.environ),
        )
        host.prepare()
        await asyncio.to_thread(host.spawn)
        child_pid = host.pid
        await read_until(host.output_queue, b">")
        host.write("echo subprocess_marker\r")
        await read_until(host.output_queue, b"subprocess_marker")

        # Simulate the daemon dying without any shutdown signal.
        first._writer.transport.abort()
        await asyncio.sleep(0.5)
        assert process.poll() is None, "supervisor must outlive its client"

        import psutil

        # The supervisor must anchor its cwd in the data dir: a cwd inherited
        # from the spawner (e.g. inside dist/) locks that directory on Windows
        # and blocks session-preserving app rebuilds.
        assert Path(psutil.Process(process.pid).cwd()).resolve() == tmp_path.resolve()

        assert psutil.pid_exists(child_pid), "agent process must outlive the client"

        second = await _connect_with_retries(tmp_path)
        infos = {info["sid"]: info for info in second.initial_sessions}
        assert infos["proc-survive"]["alive"] is True
        adopted = host_for_adoption(second, infos["proc-survive"])
        adopted.prepare()
        _, replay = await second.subscribe(adopted)
        assert b"subprocess_marker" in replay

        await second.reap_all_and_exit()
        assert process.wait(timeout=15) == 0
        deadline = time.monotonic() + 10
        while (  # noqa: ASYNC110 - polls an external process
            psutil.pid_exists(child_pid) and time.monotonic() < deadline
        ):
            await asyncio.sleep(0.1)
        assert not psutil.pid_exists(child_pid), "reap_all_and_exit must kill the tree"
        assert not discovery_path(tmp_path).exists()
    finally:
        if process.poll() is None:
            process.kill()
        if process.stdout is not None:
            process.stdout.close()


# --------------------------------------------------------------------------
# §7.4 — SessionManager spawns through the supervisor and a "restarted
# daemon" adopts the live session with scrollback and I/O intact
# --------------------------------------------------------------------------


async def test_session_manager_reattaches_after_daemon_restart(
    harness: SupervisorHarness, tmp_path: Path
) -> None:
    from swe_mux.adapters import ShellAdapter
    from swe_mux.event_bus import EventBus
    from swe_mux.history import HistoryIndex
    from swe_mux.session import SessionManager
    from swe_mux.win_jobobj import ReaperJob

    history = HistoryIndex(tmp_path / "mux.db")
    reaper = ReaperJob()
    workdir = tmp_path / "work"
    workdir.mkdir()

    def make_manager(client: SupervisorClient) -> SessionManager:
        return SessionManager(
            {"shell": ShellAdapter(CMD)},
            reaper,
            history,
            EventBus(),
            256 * 1024,
            "http://127.0.0.1:1",
            {},
            hook_spool_dir=tmp_path / "hook-spool",
            supervisor=client,
        )

    first_client = await harness.connect()
    manager_one = make_manager(first_client)
    try:
        session = await manager_one.spawn(
            backend="shell", name="reload-me", cwd=str(workdir), project_id="default"
        )
        sid = session.record.id
        assert isinstance(session.pty, RemotePtyHost)
        assert "nested_session_job_assigned" in session.record.process_job_assignment
        if session.registration_task is not None:
            await session.registration_task
        session.pty.write("echo daemon_one_marker\r")
        deadline = time.monotonic() + READ_TIMEOUT
        while b"daemon_one_marker" not in session.scrollback.bytes():
            assert time.monotonic() < deadline, "marker never reached daemon-side scrollback"
            await asyncio.sleep(0.05)

        # "Restart the daemon": drop manager one without stopping anything.
        for task in tuple(session.tasks):
            task.cancel()
        await asyncio.gather(*session.tasks, return_exceptions=True)
        await first_client.close()
        await asyncio.sleep(0.2)

        second_client = await harness.connect()
        manager_two = make_manager(second_client)
        adopted = await manager_two.adopt_supervisor_sessions()
        assert adopted == 1
        revived = manager_two.sessions[sid]
        assert revived.record.name == "reload-me"
        assert revived.record.backend == "shell"
        assert revived.hook_secret == session.hook_secret
        assert b"daemon_one_marker" in revived.scrollback.bytes()
        assert revived.pty.isalive()

        revived.pty.write("echo daemon_two_marker\r")
        deadline = time.monotonic() + READ_TIMEOUT
        while b"daemon_two_marker" not in revived.scrollback.bytes():
            assert time.monotonic() < deadline, "adopted session did not receive live output"
            await asyncio.sleep(0.05)

        await manager_two.stop(sid)
        assert revived.record.state in {"exited", "crashed"}
    finally:
        history.close()
        reaper.close()
