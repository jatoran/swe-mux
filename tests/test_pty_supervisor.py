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
from types import SimpleNamespace
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
    # A daemon relaunched from inside an agent session carries these; they must
    # never reach a terminal's environment (nested `claude` would disable
    # transcript saving and break observation).
    os.environ["CLAUDE_CODE_CHILD_SESSION"] = "1"
    try:
        session = await manager_one.spawn(
            backend="shell", name="reload-me", cwd=str(workdir), project_id="default"
        )
        sid = session.record.id
        assert isinstance(session.pty, RemotePtyHost)
        assert "nested_session_job_assigned" in session.record.process_job_assignment
        assert harness.server is not None
        spawned_env = harness.server.sessions[sid].host.env_extra or {}
        assert "CLAUDE_CODE_CHILD_SESSION" not in spawned_env
        assert "MUX_SESSION_ID" in spawned_env
        # Colour capability reaches every harness, not just OMP: a plain shell
        # session is spawned describing swe-mux's xterm.js PTY, so a CLI never
        # falls back to monochrome because the frozen daemon inherited no TERM.
        assert spawned_env["TERM"] == "xterm-256color"
        assert spawned_env["COLORTERM"] == "truecolor"
        # An inherited rich-emulator marker is shadowed, never forwarded.
        assert spawned_env["WT_SESSION"] == ""
        # ...but a shell keeps honouring pipe semantics: colour is not force-set,
        # so `cmd > file` never gets ANSI. That forcing is scoped to agent panes.
        assert "FORCE_COLOR" not in spawned_env
        assert "CLICOLOR_FORCE" not in spawned_env
        if session.registration_task is not None:
            await session.registration_task
        session.observation_state["hook_sequences"] = {"omp-extension": 17}
        session.observation_state["hook_sequence_duplicates"] = 2
        session.meta_sink()
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
        assert revived.observation_state["hook_sequences"] == {"omp-extension": 17}
        assert revived.observation_state["hook_sequence_duplicates"] == 2
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
        os.environ.pop("CLAUDE_CODE_CHILD_SESSION", None)
        history.close()
        reaper.close()


# ---- §7.1 backpressure must never fabricate an exit ---------------------------


class _FakePty:
    """Minimal stand-in for the reader's thread-local PTY handle."""

    def __init__(self, alive: bool = True) -> None:
        self.alive = alive

    def isalive(self) -> bool:
        return self.alive


async def test_backpressured_put_waits_instead_of_fabricating_end_of_output() -> None:
    """A full output queue must throttle the child, not end the session.

    Timing out the cross-thread handoff aborted the reader thread, whose finally
    injected the b"" end-of-output sentinel — an exit fabricated purely from
    consumer backpressure. The supervisor then closed the session's
    kill-on-close job, killing a live agent tree.
    """
    from swe_mux import pty_host as pty_host_module

    host = PtyHost("cmd.exe")
    host.prepare()
    queue = host.output_queue
    while not queue.full():
        queue.put_nowait(b"x")
    pty = _FakePty(alive=True)

    # Re-check liveness quickly so the test does not sit on the production cadence.
    original_poll = pty_host_module._QUEUE_PUT_POLL_SECONDS
    pty_host_module._QUEUE_PUT_POLL_SECONDS = 0.01
    try:
        result: list[bool] = []
        worker = asyncio.get_running_loop().run_in_executor(
            None, lambda: result.append(host._put_with_backpressure(b"payload", pty))
        )
        # The child is alive: the reader keeps waiting rather than giving up.
        await asyncio.sleep(0.1)
        assert result == []

        # Draining one slot lets the handoff complete normally.
        queue.get_nowait()
        await asyncio.wait_for(worker, timeout=5)
        assert result == [True]
        assert b"payload" in list(queue._queue)  # type: ignore[attr-defined]
    finally:
        pty_host_module._QUEUE_PUT_POLL_SECONDS = original_poll


async def test_backpressured_put_gives_up_once_the_child_is_really_gone() -> None:
    from swe_mux import pty_host as pty_host_module

    host = PtyHost("cmd.exe")
    host.prepare()
    queue = host.output_queue
    while not queue.full():
        queue.put_nowait(b"x")
    pty = _FakePty(alive=True)

    original_poll = pty_host_module._QUEUE_PUT_POLL_SECONDS
    pty_host_module._QUEUE_PUT_POLL_SECONDS = 0.01
    try:
        result: list[bool] = []
        worker = asyncio.get_running_loop().run_in_executor(
            None, lambda: result.append(host._put_with_backpressure(b"payload", pty))
        )
        await asyncio.sleep(0.05)
        pty.alive = False
        await asyncio.wait_for(worker, timeout=5)
        assert result == [False]
    finally:
        pty_host_module._QUEUE_PUT_POLL_SECONDS = original_poll


# ---- §9 the sentinel alone is not proof of exit ------------------------------


class _SentinelHost:
    def __init__(self, alive: bool) -> None:
        self.output_queue: asyncio.Queue[bytes] = asyncio.Queue()
        self._alive = alive

    def isalive(self) -> bool:
        return self._alive

    def exit_status(self) -> int | None:
        return None if self._alive else 0


def _supervised(host: Any) -> Any:
    from swe_mux.scrollback import ScrollbackBuffer
    from swe_mux.supervisor import SupervisedSession

    return SupervisedSession(sid="s1", host=host, scrollback=ScrollbackBuffer(1024))


async def test_fanout_does_not_report_exit_while_the_process_is_alive(
    tmp_path: Path,
) -> None:
    """`b""` means the reader stopped, not that the child died."""
    server = SupervisorServer(tmp_path / "config.toml", tmp_path)
    entry = _supervised(_SentinelHost(alive=True))
    server.sessions["s1"] = entry
    await entry.host.output_queue.put(b"")
    await asyncio.wait_for(server._fanout(entry), timeout=5)
    assert entry.alive is True
    assert entry.output_ended_while_alive is True


async def test_fanout_reports_exit_when_the_process_is_really_gone(
    tmp_path: Path,
) -> None:
    server = SupervisorServer(tmp_path / "config.toml", tmp_path)
    entry = _supervised(_SentinelHost(alive=False))
    server.sessions["s1"] = entry
    await entry.host.output_queue.put(b"")
    await asyncio.wait_for(server._fanout(entry), timeout=5)
    assert entry.alive is False
    assert entry.output_ended_while_alive is False
    assert entry.exit_code == 0


async def test_job_pids_reports_per_session_membership(tmp_path: Path) -> None:
    """The daemon cannot read these handles itself.

    The nested per-session job is held here on purpose — a daemon-held handle
    would kill the tree on daemon exit — so job membership, the only ownership
    evidence that survives a detached launch's parent exiting, has to come back
    over the wire.
    """
    server = SupervisorServer(tmp_path / "config.toml", tmp_path)
    with_job = _supervised(_SentinelHost(alive=True))
    with_job.ownership_job = SimpleNamespace(process_ids=lambda: [10, 777])
    without_job = _supervised(_SentinelHost(alive=True))
    without_job.sid = "s2"
    unreadable = _supervised(_SentinelHost(alive=True))
    unreadable.sid = "s3"
    unreadable.ownership_job = SimpleNamespace(
        process_ids=lambda: (_ for _ in ()).throw(OSError("handle closed"))
    )
    server.sessions["s1"] = with_job
    server.sessions["s2"] = without_job
    server.sessions["s3"] = unreadable

    # A session with no job and one whose handle fails both drop out rather than
    # reporting an empty list, which the daemon would read as "nothing is owned".
    assert server._job_pids() == {"s1": [10, 777]}


async def test_an_older_supervisor_degrades_instead_of_breaking_the_daemon(
    tmp_path: Path,
) -> None:
    """`job_pids` is deliberately not gated on PROTOCOL_VERSION.

    Bumping the protocol to add it would stop a new daemon from driving the
    already-running supervisor, orphaning every live session over an attribution
    nicety. An older supervisor answers "unknown message type" instead, and the
    client turns that into "no job evidence".
    """
    from swe_mux.supervisor_client import SupervisorClient

    client = SupervisorClient.__new__(SupervisorClient)

    async def unknown_message(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("unknown message type: 'job_pids'")

    client.request = unknown_message  # type: ignore[method-assign]
    assert await client.job_pids() == {}

    async def malformed(*_args: Any, **_kwargs: Any) -> Any:
        return {"ok": True, "jobs": {"s1": [1, "two", 3], "s2": "nope"}}, b""

    client.request = malformed  # type: ignore[method-assign]
    assert await client.job_pids() == {"s1": [1, 3]}


async def test_remove_stops_a_session_the_daemon_wrongly_believes_is_dead(
    tmp_path: Path,
) -> None:
    """The release branch suppresses "cannot release a live pseudoconsole" and
    then closes a kill-on-close job. A stale `alive=False` must not take it."""

    class Host:
        def __init__(self) -> None:
            self.output_queue: asyncio.Queue[bytes] = asyncio.Queue()
            self.stopped = False
            self.released = False

        def isalive(self) -> bool:
            return True

        def stop(self, *, graceful: bool = True, timeout: float = 2.0) -> None:
            del graceful, timeout
            self.stopped = True

        def release(self) -> None:
            self.released = True

    class Job:
        def __init__(self) -> None:
            self.closed = False

        def close(self) -> None:
            self.closed = True

    class Sink:
        def send(self, header: dict[str, Any], payload: bytes = b"") -> None:
            del header, payload

    server = SupervisorServer(tmp_path / "config.toml", tmp_path)
    host = Host()
    entry = _supervised(host)
    entry.alive = False  # the daemon's (wrong) belief
    entry.ownership_job = Job()  # type: ignore[assignment]
    server.sessions["s1"] = entry

    await server._remove_and_reply(Sink(), {"sid": "s1"}, None)  # type: ignore[arg-type]

    assert host.stopped is True
    assert host.released is False


def test_the_reader_poll_follows_the_session_instead_of_a_single_fixed_interval() -> None:
    """One interval cannot serve a live burst and a session idle at its prompt.

    The read is nonblocking by choice — `pty.read(blocking=True)` parks the reader
    thread where neither `_stop` nor a dead child can reach it — so the cadence is the
    only lever. A fixed 10ms spent it as latency on every chunk of flowing output while
    also costing 100 wakeups a second, per session, forever, on a fleet that is idle
    most of the time.
    """
    from swe_mux.pty_host import (
        _READ_POLL_ACTIVE_SECONDS,
        _READ_POLL_DEEP_IDLE_SECONDS,
        _READ_POLL_RECENT_SECONDS,
        read_poll_interval,
    )

    # Output flowing, or a keystroke just written: the echo a human is waiting on must
    # not queue behind an interval tuned for an empty pseudoconsole.
    assert read_poll_interval(0.0) == _READ_POLL_ACTIVE_SECONDS
    assert read_poll_interval(0.2) == _READ_POLL_ACTIVE_SECONDS

    # Between bursts, exactly the old fixed interval: nothing that was fast enough
    # before this ladder existed is allowed to get slower because of it.
    assert read_poll_interval(0.25) == _READ_POLL_RECENT_SECONDS
    assert read_poll_interval(4.9) == _READ_POLL_RECENT_SECONDS

    # Whole seconds of silence. The only cost is unprompted output arriving up to one
    # interval late, paid once per wake rather than per chunk.
    assert read_poll_interval(5.0) == _READ_POLL_DEEP_IDLE_SECONDS
    assert read_poll_interval(600.0) == _READ_POLL_DEEP_IDLE_SECONDS

    # The ladder only ever slows down, and never below the responsiveness the fixed
    # interval already guaranteed while a session was doing anything.
    steps = [read_poll_interval(idle) for idle in (0.0, 0.1, 0.3, 1.0, 6.0, 60.0)]
    assert steps == sorted(steps)
    assert _READ_POLL_ACTIVE_SECONDS < _READ_POLL_RECENT_SECONDS < _READ_POLL_DEEP_IDLE_SECONDS


def test_writing_input_arms_the_reader_active_window() -> None:
    """A keystroke is the one case whose response latency a human is timing.

    Without this a session quiet at its prompt has its reader sitting on the deep-idle
    interval at the exact moment the user types, so the echo pays it.
    """
    from swe_mux.pty_host import PtyHost

    written: list[str] = []

    class FakePty:
        def write(self, data: str) -> None:
            written.append(data)

    host = PtyHost(appname="x", argv=())
    host._pty = FakePty()  # type: ignore[assignment]
    host._last_io_at = time.monotonic() - 3600.0

    host.write("ls\r")

    assert written == ["ls\r"]
    assert time.monotonic() - host._last_io_at < 1.0


def test_writing_input_wakes_a_reader_already_sleeping() -> None:
    """Re-tiering the ladder decides the next interval; only the wake cuts the current one.

    Measured before this existed: a keystroke into a session idle past the deep-idle
    threshold took 30ms p50 and 40ms max end to end, against a 40ms rung, because the
    reader was already inside `time.sleep` when the write landed. That was *worse* than
    the fixed 10ms interval the ladder replaced, in precisely the case it exists to
    improve. Arming the window means nothing if the wait cannot be interrupted.
    """
    from swe_mux.pty_host import PtyHost

    class FakePty:
        def write(self, data: str) -> None:
            return None

    host = PtyHost(appname="x", argv=())
    host._pty = FakePty()  # type: ignore[assignment]
    host._last_io_at = time.monotonic() - 3600.0
    assert not host._io_wake.is_set()

    host.write("x")

    assert host._io_wake.is_set(), "a reader parked on the deep-idle interval must be woken"
