"""Supervisor protocol and reader-thread behaviour, without a real pseudoterminal.

`test_pty_supervisor.py` owns the survival claims that need real ConPTYs and real
processes, and is Windows-only and wall-clock sensitive for that reason. The
properties here are protocol and control-flow properties - what a duplicate
spawn does, what a lost reply can be recovered from, what teardown waits for,
which frames are refused - so they are exercised against fakes, run on every
host, and stay parallel-safe.

Findings covered: F2 (spawn idempotency and `spawn_status`), F8 (teardown
quiesce), F13 (frame bounds and auth-first), F14 (exit sentinel and read-failure
diagnostics), F22 (the shared nested-job helper). See
`.docs/development/CODE_QUALITY_AUDIT_2026-08-23.md`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import struct
import threading
from pathlib import Path
from typing import Any

import pytest

from swe_mux import pty_host as pty_host_module
from swe_mux import supervisor as supervisor_module
from swe_mux.nested_job import (
    ASSIGNED_SUFFIX,
    FAILED_SUFFIX_PREFIX,
    create_nested_session_job,
)
from swe_mux.pty_host import PtyHost
from swe_mux.supervisor import (
    PROTOCOL_VERSION,
    SupervisorServer,
    encode_frame,
    read_frame,
)

# --------------------------------------------------------------------------
# Fakes: one supervisor with no ConPTY under it
# --------------------------------------------------------------------------


class FakeReaper:
    """A `ProcessReaper` that records rather than owning anything."""

    def __init__(self, log: list[str], name: str = "root") -> None:
        self.log = log
        self.name = name
        self.assigned: list[int] = []
        self.closed = False
        self.children: list[FakeReaper] = []
        self.create_child_error: OSError | None = None
        self.assign_error: OSError | None = None

    def assign(self, pid: int) -> None:
        if self.assign_error is not None:
            raise self.assign_error
        self.assigned.append(pid)

    def process_ids(self) -> list[int]:
        return list(self.assigned)

    def create_child(self) -> FakeReaper:
        if self.create_child_error is not None:
            raise self.create_child_error
        child = FakeReaper(self.log, f"{self.name}/child{len(self.children)}")
        child.assign_error = self.assign_error
        self.children.append(child)
        return child

    def close(self) -> None:
        self.closed = True
        self.log.append(f"reaper-close:{self.name}")


class FakePtyHost:
    """Stands in for `PtyHost` inside the supervisor's spawn path."""

    instances: list[FakePtyHost] = []
    spawn_calls = 0
    spawn_gate: threading.Event | None = None
    spawn_error: Exception | None = None
    event_log: list[str] = []

    def __init__(self, appname: str, argv: Any = (), cwd: Any = None, **kwargs: Any) -> None:
        self.appname = appname
        self.argv = tuple(argv)
        self.cwd = cwd
        self.cols = int(kwargs.get("cols", 120))
        self.rows = int(kwargs.get("rows", 30))
        self.graceful_exit = str(kwargs.get("graceful_exit", "exit\r"))
        self.reaper = kwargs.get("reaper")
        self.pid = -1
        self.reaper_assignment = "not_attempted"
        self.read_errors = 0
        self.last_read_error: str | None = None
        self.last_read_error_at: float | None = None
        self.output_queue: asyncio.Queue[bytes] = asyncio.Queue()
        self.alive = True
        self.stopped = False
        self.released = False
        FakePtyHost.instances.append(self)

    @classmethod
    def reset(cls) -> None:
        cls.instances = []
        cls.spawn_calls = 0
        cls.spawn_gate = None
        cls.spawn_error = None
        cls.event_log = []

    def prepare(self) -> None:
        return None

    def spawn(self) -> None:
        # Runs on a worker thread, exactly as the real blocking spawn does.
        gate = FakePtyHost.spawn_gate
        if gate is not None:
            assert gate.wait(timeout=30), "spawn gate was never released"
        if FakePtyHost.spawn_error is not None:
            raise FakePtyHost.spawn_error
        FakePtyHost.spawn_calls += 1
        self.pid = 4200 + FakePtyHost.spawn_calls
        self.reaper_assignment = "daemon_job_assigned"

    def isalive(self) -> bool:
        return self.alive

    def exit_status(self) -> int | None:
        return None if self.alive else 0

    def stop(self, *, graceful: bool = True, timeout: float = 2.0) -> None:
        del graceful, timeout
        self.stopped = True
        self.alive = False
        FakePtyHost.event_log.append(f"host-stop:{self.pid}")

    def release(self) -> None:
        self.released = True

    def write(self, data: Any) -> None:
        return None

    def resize(self, cols: int, rows: int) -> None:
        self.cols, self.rows = cols, rows


class RecordingConnection:
    """A `Connection` stand-in that captures replies, or drops them on demand."""

    def __init__(self, *, drop: bool = False) -> None:
        self.frames: list[dict[str, Any]] = []
        self.payloads: list[bytes] = []
        self.authenticated = True
        self.drop = drop

    def send(self, header: dict[str, Any], payload: bytes = b"") -> None:
        if self.drop:
            return
        self.frames.append(header)
        self.payloads.append(payload)

    @property
    def last(self) -> dict[str, Any]:
        assert self.frames, "no reply was sent"
        return self.frames[-1]


def make_server(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SupervisorServer:
    FakePtyHost.reset()
    order: list[str] = []
    monkeypatch.setattr(supervisor_module, "PtyHost", FakePtyHost)
    monkeypatch.setattr(supervisor_module, "create_reaper", lambda: FakeReaper(order))
    server = SupervisorServer(tmp_path / "config.toml", tmp_path)
    FakePtyHost.event_log = order
    return server


def spawn_header(sid: str, request_id: int) -> dict[str, Any]:
    return {
        "t": "spawn",
        "id": request_id,
        "sid": sid,
        "appname": "agent.exe",
        "argv": [],
        "cwd": None,
        "cols": 80,
        "rows": 24,
        "env": {},
    }


async def drain_background(server: SupervisorServer) -> None:
    pending = [task for task in server._background_tasks if not task.done()]
    if pending:
        await asyncio.wait_for(asyncio.gather(*pending, return_exceptions=True), timeout=10)


# --------------------------------------------------------------------------
# F2 - spawn idempotency and `spawn_status`
# --------------------------------------------------------------------------


async def test_a_lost_spawn_reply_is_recoverable_and_a_retry_spawns_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The finding-2 scenario end to end.

    The supervisor spawns successfully and the reply never reaches the daemon (a
    >60s ConPTY stall under Defender is enough to make the RPC time out first).
    Before this, the daemon's only options were to give up on a live agent or to
    retry into a second agent process on the same workspace - the retry used to
    be refused with "session already exists", which reads like a failure.
    """
    server = make_server(tmp_path, monkeypatch)
    lost = RecordingConnection(drop=True)

    await server._dispatch(lost, spawn_header("s1", 1), b"")
    await drain_background(server)
    assert lost.frames == [], "the reply is deliberately lost in this scenario"
    assert FakePtyHost.spawn_calls == 1

    # The daemon asks what actually happened.
    asking = RecordingConnection()
    await server._dispatch(asking, {"t": "spawn_status", "id": 2, "sid": "s1"}, b"")
    status = asking.last
    assert status["ok"] is True
    assert status["state"] == "live"
    assert status["pid"] == FakePtyHost.instances[0].pid
    assert isinstance(status["started_at"], float)

    # And a naive retry returns the first outcome instead of a second agent.
    await server._dispatch(asking, spawn_header("s1", 3), b"")
    await drain_background(server)
    retry = asking.last
    assert retry["ok"] is True
    assert retry["deduped"] is True
    assert retry["state"] == "live"
    assert retry["pid"] == FakePtyHost.instances[0].pid
    assert FakePtyHost.spawn_calls == 1, "a duplicate spawn must not start a second process"
    assert len(server.sessions) == 1


async def test_spawn_status_reports_unknown_for_an_id_that_was_never_reserved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """"unknown" is the answer that makes the daemon's fallback safe.

    Nothing was reserved, so nothing can be duplicated by spawning now - which is
    the only condition under which an in-process fallback is correct.
    """
    server = make_server(tmp_path, monkeypatch)
    connection = RecordingConnection()
    await server._dispatch(connection, {"t": "spawn_status", "id": 1, "sid": "nope"}, b"")
    assert connection.last == {"id": 1, "ok": True, "sid": "nope", "state": "unknown"}


async def test_spawn_status_reports_reserved_while_the_spawn_is_still_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A reservation exists from the instant the request is accepted."""
    server = make_server(tmp_path, monkeypatch)
    gate = threading.Event()
    FakePtyHost.spawn_gate = gate
    connection = RecordingConnection()

    await server._dispatch(connection, spawn_header("s1", 1), b"")
    await server._dispatch(connection, {"t": "spawn_status", "id": 2, "sid": "s1"}, b"")
    reserved = connection.last
    assert reserved["state"] == "reserved"
    assert "pid" not in reserved, "there is no pid to report yet, so none is reported"

    gate.set()
    await drain_background(server)
    await server._dispatch(connection, {"t": "spawn_status", "id": 3, "sid": "s1"}, b"")
    assert connection.last["state"] == "live"


async def test_spawn_status_reports_exited_with_its_exit_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = make_server(tmp_path, monkeypatch)
    connection = RecordingConnection()
    await server._dispatch(connection, spawn_header("s1", 1), b"")
    await drain_background(server)

    entry = server.sessions["s1"]
    entry.alive = False
    entry.exit_code = 3

    await server._dispatch(connection, {"t": "spawn_status", "id": 2, "sid": "s1"}, b"")
    status = connection.last
    assert status["state"] == "exited"
    assert status["exit_code"] == 3
    assert status["pid"] == FakePtyHost.instances[0].pid


async def test_a_failed_spawn_releases_the_id_so_a_retry_really_retries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Deduplication keys on the reservation, and a failed spawn has none.

    The opposite behaviour - remembering the failure - would make a session id
    permanently unusable after one bad spawn.
    """
    server = make_server(tmp_path, monkeypatch)
    FakePtyHost.spawn_error = RuntimeError("ConPTY refused")
    connection = RecordingConnection()

    await server._dispatch(connection, spawn_header("s1", 1), b"")
    await drain_background(server)
    assert connection.last["ok"] is False
    assert "s1" not in server.sessions

    await server._dispatch(connection, {"t": "spawn_status", "id": 2, "sid": "s1"}, b"")
    assert connection.last["state"] == "unknown"

    FakePtyHost.spawn_error = None
    await server._dispatch(connection, spawn_header("s1", 3), b"")
    await drain_background(server)
    assert connection.last["ok"] is True
    assert FakePtyHost.spawn_calls == 1


async def test_a_duplicate_spawn_waits_for_the_original_rather_than_racing_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both callers get the same answer, and only one process is created."""
    server = make_server(tmp_path, monkeypatch)
    gate = threading.Event()
    FakePtyHost.spawn_gate = gate
    first = RecordingConnection()
    second = RecordingConnection()

    await server._dispatch(first, spawn_header("s1", 1), b"")
    await server._dispatch(second, spawn_header("s1", 2), b"")
    await asyncio.sleep(0.05)
    assert second.frames == [], "the duplicate must not answer before the original resolves"

    gate.set()
    await drain_background(server)
    assert first.last["ok"] is True and first.last["pid"] == FakePtyHost.instances[0].pid
    assert second.last["ok"] is True and second.last["pid"] == first.last["pid"]
    assert second.last["deduped"] is True
    assert FakePtyHost.spawn_calls == 1


async def test_a_duplicate_spawn_does_not_silently_subscribe_the_second_connection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Attaching is `subscribe`'s job, because only it is atomic with the snapshot."""
    server = make_server(tmp_path, monkeypatch)
    first = RecordingConnection()
    second = RecordingConnection()
    await server._dispatch(first, spawn_header("s1", 1), b"")
    await drain_background(server)
    await server._dispatch(second, spawn_header("s1", 2), b"")
    await drain_background(server)
    assert second not in server.sessions["s1"].subscribers


# --------------------------------------------------------------------------
# F8 - teardown quiesce
# --------------------------------------------------------------------------


async def test_teardown_drains_an_in_flight_spawn_before_reaping(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The orphan-escapes-the-job race.

    A spawn that was already past its refusal check when teardown began creates
    its child moments before the reaper Job closes. Closing the Job first leaves
    that child assigned to nothing and killed by nothing: a reap that reported
    success while an agent kept running. Teardown must wait for the spawn, stop
    the child it produced, and only then close the Job.
    """
    server = make_server(tmp_path, monkeypatch)
    gate = threading.Event()
    FakePtyHost.spawn_gate = gate
    connection = RecordingConnection()

    await server._dispatch(connection, spawn_header("s1", 1), b"")
    teardown = asyncio.create_task(server._teardown())
    await asyncio.sleep(0.05)
    assert not server.reaper.closed, "the reap must not happen while a spawn is in flight"

    gate.set()
    await asyncio.wait_for(teardown, timeout=15)

    host = FakePtyHost.instances[0]
    assert host.stopped is True, "a child born during shutdown must be stopped, not orphaned"
    assert "s1" not in server.sessions
    assert server.reaper.closed is True
    assert FakePtyHost.event_log.index(f"host-stop:{host.pid}") < FakePtyHost.event_log.index(
        "reaper-close:root"
    )
    assert connection.last["ok"] is False


async def test_a_spawn_dispatched_after_shutdown_began_is_refused_outright(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = make_server(tmp_path, monkeypatch)
    server.exit_event.set()
    connection = RecordingConnection()
    with pytest.raises(RuntimeError, match="shutting down"):
        await server._dispatch(connection, spawn_header("s1", 1), b"")
    assert FakePtyHost.spawn_calls == 0


async def test_teardown_gives_up_on_a_wedged_operation_rather_than_never_reaping(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The drain is bounded: a wedged ConPTY must not hold the reap open forever."""
    monkeypatch.setattr(supervisor_module, "SHUTDOWN_DRAIN_SECONDS", 0.1)
    server = make_server(tmp_path, monkeypatch)
    stuck = asyncio.Event()

    async def never_finishes() -> None:
        await stuck.wait()

    server._spawn_background(never_finishes(), RecordingConnection(), None)  # type: ignore[arg-type]
    await asyncio.wait_for(server._teardown(), timeout=10)
    assert server.reaper.closed is True


# --------------------------------------------------------------------------
# F13 - frame bounds and auth-first
# --------------------------------------------------------------------------


def framed(header: dict[str, Any], payload: bytes = b"") -> bytes:
    raw = json.dumps(header).encode("utf-8")
    return struct.pack(">I", len(raw)) + raw + payload


async def feed(data: bytes) -> asyncio.StreamReader:
    reader = asyncio.StreamReader()
    reader.feed_data(data)
    reader.feed_eof()
    return reader


async def test_a_negative_payload_length_is_refused_before_readexactly() -> None:
    reader = await feed(framed({"t": "write", "plen": -1}))
    with pytest.raises(ValueError, match="negative frame payload length"):
        await read_frame(reader)


async def test_a_non_numeric_payload_length_is_refused() -> None:
    reader = await feed(framed({"t": "write", "plen": "lots"}))
    with pytest.raises(ValueError, match="not a number"):
        await read_frame(reader)


async def test_an_oversized_payload_length_is_refused_against_a_cap() -> None:
    reader = await feed(framed({"t": "write", "plen": 10_000_000}))
    with pytest.raises(ValueError, match="oversized frame payload"):
        await read_frame(reader, max_payload_bytes=1024)


async def test_an_oversized_header_is_refused_against_a_cap() -> None:
    reader = await feed(framed({"t": "hello", "token": "x" * 5000}))
    with pytest.raises(ValueError, match="oversized frame header"):
        await read_frame(reader, max_header_bytes=256)


async def test_the_client_inbound_direction_stays_unbounded_by_default() -> None:
    """A legitimate `subscribe` reply carries a whole scrollback buffer.

    The cap is for the daemon-inbound direction; defaulting it on would reject
    real data in the other one, which is exactly what the audit warned about.
    """
    payload = b"s" * (6 * 1024 * 1024)
    reader = await feed(framed({"t": "output", "sid": "s1", "plen": len(payload)}, payload))
    header, body = await read_frame(reader)
    assert header["sid"] == "s1"
    assert len(body) == len(payload)


async def test_an_unauthenticated_hello_may_not_carry_a_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Auth-first: the payload was read in full *before* the token was checked."""
    server = make_server(tmp_path, monkeypatch)
    port = await server.start()
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(
            encode_frame(
                {"t": "hello", "id": 0, "token": server.token, "protocol": PROTOCOL_VERSION},
                b"unexpected",
            )
        )
        await writer.drain()
        assert await reader.read() == b"", "the connection is dropped, not answered"
        writer.close()
    finally:
        server.exit_event.set()
        await server._teardown()


async def test_a_connection_that_never_authenticates_is_dropped_on_a_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(supervisor_module, "AUTH_DEADLINE_SECONDS", 0.2)
    server = make_server(tmp_path, monkeypatch)
    port = await server.start()
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        assert await asyncio.wait_for(reader.read(), timeout=10) == b""
        writer.close()
    finally:
        server.exit_event.set()
        await server._teardown()


async def test_a_valid_hello_still_authenticates_and_announces_sessions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The bounds must not have broken the one frame they exist to admit."""
    server = make_server(tmp_path, monkeypatch)
    port = await server.start()
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(
            encode_frame(
                {"t": "hello", "id": 0, "token": server.token, "protocol": PROTOCOL_VERSION}
            )
        )
        await writer.drain()
        header, _ = await asyncio.wait_for(read_frame(reader), timeout=10)
        assert header["ok"] is True
        assert header["protocol"] == PROTOCOL_VERSION
        assert header["sessions"] == []
        writer.close()
    finally:
        server.exit_event.set()
        await server._teardown()


async def test_an_unknown_message_type_is_the_graceful_degradation_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The graceful-degradation contract, from the other side.

    `spawn_status` is deliberately not gated on PROTOCOL_VERSION: a bump would
    stop a new daemon from driving an already-running older supervisor, orphaning
    every live session over a query. An older supervisor answers exactly this.
    """
    server = make_server(tmp_path, monkeypatch)
    connection = RecordingConnection()
    with pytest.raises(ValueError, match="unknown message type"):
        await server._dispatch(connection, {"t": "not_a_real_message", "id": 1}, b"")


# --------------------------------------------------------------------------
# F14 - the exit sentinel and read-failure diagnostics
# --------------------------------------------------------------------------


async def test_the_exit_sentinel_waits_for_a_full_queue_instead_of_being_lost(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Losing this byte is worse than waiting for it.

    It is the only signal that a session's output ended: without it there is no
    `pty_exit`, the session is phantom-alive forever, the supervisor lingers
    because it still counts a live session, and the pane never resolves. The old
    code gave up after two seconds and swallowed the exception.
    """
    monkeypatch.setattr(pty_host_module, "_QUEUE_PUT_POLL_SECONDS", 0.02)
    host = PtyHost("agent.exe")
    host.prepare()
    queue = host.output_queue
    while not queue.full():
        queue.put_nowait(b"x")

    delivered: list[bool] = []
    worker = asyncio.get_running_loop().run_in_executor(
        None, lambda: (host._put_end_of_output(1234), delivered.append(True))
    )
    await asyncio.sleep(0.2)
    assert delivered == [], "it must still be waiting, not have given up"

    # Drain the queue; the sentinel lands behind what was already buffered.
    seen: list[bytes] = []
    while len(seen) < queue.maxsize + 1:
        seen.append(await asyncio.wait_for(queue.get(), timeout=5))
    await asyncio.wait_for(worker, timeout=5)
    assert delivered == [True]
    assert seen[-1] == b""


async def test_the_exit_sentinel_gives_up_once_a_stop_removed_its_consumer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The one bounded case: nothing will ever drain this queue again."""
    monkeypatch.setattr(pty_host_module, "_QUEUE_PUT_POLL_SECONDS", 0.02)
    host = PtyHost("agent.exe")
    host.prepare()
    queue = host.output_queue
    while not queue.full():
        queue.put_nowait(b"x")
    host._stop.set()

    await asyncio.wait_for(
        asyncio.get_running_loop().run_in_executor(None, host._put_end_of_output, 1234),
        timeout=5,
    )


async def test_a_swallowed_read_failure_is_counted_and_rate_limited(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A reader that keeps going silently is a session that is alive and mute."""
    monkeypatch.setattr(pty_host_module, "_READ_ERROR_LOG_INTERVAL_SECONDS", 3600.0)
    host = PtyHost("agent.exe")
    with caplog.at_level(logging.WARNING, logger="swe_mux.pty_host"):
        for index in range(5):
            host._note_read_error(OSError(f"read {index} failed"), 4242)

    assert host.read_errors == 5
    assert host.last_read_error == "OSError: read 4 failed"
    assert isinstance(host.last_read_error_at, float)
    warnings = [record for record in caplog.records if "pty read failed" in record.message]
    assert len(warnings) == 1, "the first failure is logged; the rest are counted, not repeated"
    assert "pid=4242" in warnings[0].getMessage()


async def test_read_failure_counters_reach_the_daemon_through_the_session_inventory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The daemon cannot see the supervisor's reader thread; this is the only view."""
    server = make_server(tmp_path, monkeypatch)
    connection = RecordingConnection()
    await server._dispatch(connection, spawn_header("s1", 1), b"")
    await drain_background(server)

    host = FakePtyHost.instances[0]
    host.read_errors = 7
    host.last_read_error = "PtyError: pipe broken"
    host.last_read_error_at = 1_700_000_000.0

    await server._dispatch(connection, {"t": "list", "id": 2}, b"")
    info = connection.last["sessions"][0]
    assert info["read_errors"] == 7
    assert info["last_read_error"] == "PtyError: pipe broken"
    assert info["last_read_error_at"] == 1_700_000_000.0
    assert isinstance(info["started_at"], float)


# --------------------------------------------------------------------------
# F22 - the shared nested-job helper
# --------------------------------------------------------------------------


def test_the_nested_job_helper_reports_ownership_it_actually_has() -> None:
    parent = FakeReaper([])
    outcome = create_nested_session_job(parent, 4242, sid="s1")
    assert outcome.owned is True
    assert outcome.suffix == ASSIGNED_SUFFIX
    assert outcome.error is None
    assert parent.children[0].assigned == [4242]


def test_the_nested_job_helper_closes_a_job_it_could_not_assign() -> None:
    """Reporting ownership it does not have is the failure that matters here."""
    parent = FakeReaper([])
    parent.assign_error = OSError("access denied")
    outcome = create_nested_session_job(parent, 4242, sid="s1")
    assert outcome.owned is False
    assert outcome.suffix.startswith(FAILED_SUFFIX_PREFIX)
    assert "access denied" in outcome.suffix
    assert parent.children[0].closed is True


def test_the_nested_job_helper_tolerates_a_reaper_that_cannot_nest() -> None:
    outcome = create_nested_session_job(None, 4242)
    assert outcome.owned is False
    assert outcome.suffix == ""
    assert outcome.error is None
