"""A Preview whose port another process also holds.

The incident these pin (2026-09-26): a Project task started a standard-library HTTP
server on 127.0.0.1:8766 while two orphaned `python -m http.server 8766` processes
from earlier sessions still held that port. Python sets `SO_REUSEADDR`, and on
Windows that lets a second socket bind a port another process is listening on
without any error, so the task started cleanly and never received a request: every
connection went to the oldest orphan. The Preview - correctly attributed to the
task's session - rendered the orphan's directory listing, and nothing on screen
said the page came from a different process.
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket
import sys
from collections import namedtuple
from types import SimpleNamespace
from typing import Any, cast

import pytest

from swe_mux import processes
from swe_mux.event_bus import EventBus
from swe_mux.processes import (
    OwnedProcess,
    PreviewProbeResult,
    PreviewRegistry,
    ProcessInspector,
    index_listeners,
    probe_listener_answerer,
)

from .support.settle import until
from .test_processes_phase4 import FakeSession, FakeSessionManager, session_record

Address = namedtuple("Address", "ip port")
Connection = namedtuple("Connection", "laddr raddr status pid")

PORT = 8766


def _listening(pid: int, host: str, port: int = PORT) -> Connection:
    return Connection(Address(host, port), (), "LISTEN", pid)


def test_a_wildcard_and_a_loopback_bind_of_one_port_are_the_same_address() -> None:
    index = index_listeners(
        {
            10: [_listening(10, "127.0.0.1")],
            20: [_listening(20, "0.0.0.0")],
            # One process bound on both stacks is still one process.
            30: [_listening(30, "::1", 9000), _listening(30, "::", 9000)],
            # An established connection is not a listener, whatever its port.
            40: [
                Connection(Address("127.0.0.1", PORT), Address("127.0.0.1", 5), "ESTABLISHED", 40)
            ],
        }
    )
    assert index[("127.0.0.1", PORT)] == frozenset({10, 20})
    assert index[("::1", 9000)] == frozenset({30})


def test_a_listener_row_names_the_other_processes_on_its_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        processes,
        "psutil",
        SimpleNamespace(CONN_LISTEN="LISTEN", CONN_ESTABLISHED="ESTABLISHED"),
    )
    inspector = ProcessInspector(cast(Any, FakeSessionManager({})), EventBus())
    conn_map = {
        10: [_listening(10, "127.0.0.1")],
        20: [_listening(20, "127.0.0.1"), _listening(20, "127.0.0.1", 9000)],
    }
    inspector._listen_index = index_listeners(conn_map)

    shared, alone = inspector._network_from(20, conn_map)[0]

    assert shared["port"] == PORT and shared["shared_with"] == [10]
    assert alone["port"] == 9000 and "shared_with" not in alone


def _owned(pid: int, session_id: str, *, state: str = "active") -> OwnedProcess:
    item = OwnedProcess(
        pid,
        None,
        session_id,
        "python.exe",
        f"python.exe -m server {pid}",
        float(pid),
        None,
        0.0,
        0,
        [],
        [],
        project_id="default",
    )
    item.evidence_state = state
    return item


def _conflicted_inspector(monkeypatch: pytest.MonkeyPatch, answering: int | None) -> Any:
    inspector = ProcessInspector(cast(Any, FakeSessionManager({})), EventBus())
    inspector.owned = {
        (25604, 25604.0): _owned(25604, "task"),
        (136796, 136796.0): _owned(136796, "ended", state="suspected_orphan"),
    }
    inspector._listen_index = {("127.0.0.1", PORT): frozenset({25604, 136796})}
    probes: list[tuple[str, int]] = []

    async def answerer(host: str, port: int) -> int | None:
        probes.append((host, port))
        return answering

    monkeypatch.setattr(processes, "probe_listener_answerer", answerer)
    return inspector, probes


@pytest.mark.asyncio
async def test_a_shared_port_reports_that_the_owner_is_not_the_process_answering(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inspector, _ = _conflicted_inspector(monkeypatch, answering=136796)

    conflict = await inspector.port_conflict("127.0.0.1", PORT, session_id="task")

    assert conflict is not None
    assert conflict["answering_pid"] == 136796
    assert conflict["answering_is_owner"] is False
    by_pid = {item["pid"]: item for item in conflict["listeners"]}
    assert by_pid[25604]["owner"] is True
    assert by_pid[136796]["owner"] is False
    assert by_pid[136796]["evidence_state"] == "suspected_orphan"
    assert by_pid[136796]["session_id"] == "ended"


@pytest.mark.asyncio
async def test_an_unshared_port_is_not_a_conflict_and_is_never_probed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inspector, probes = _conflicted_inspector(monkeypatch, answering=25604)
    inspector._listen_index = {("127.0.0.1", PORT): frozenset({25604})}

    assert await inspector.port_conflict("127.0.0.1", PORT, session_id="task") is None
    assert probes == []


@pytest.mark.asyncio
async def test_the_answer_is_reused_until_the_listening_set_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inspector, probes = _conflicted_inspector(monkeypatch, answering=136796)

    await inspector.port_conflict("127.0.0.1", PORT, session_id="task")
    await inspector.port_conflict("127.0.0.1", PORT, session_id="task")
    assert len(probes) == 1

    inspector._listen_index = {("127.0.0.1", PORT): frozenset({25604, 136796, 38396})}
    await inspector.port_conflict("127.0.0.1", PORT, session_id="task")
    assert len(probes) == 2


@pytest.mark.asyncio
async def test_an_answer_naming_no_listener_is_unknown_rather_than_a_verdict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A recycled or unreadable pid must not be presented as the server that answered.
    inspector, _ = _conflicted_inspector(monkeypatch, answering=4)

    conflict = await inspector.port_conflict("127.0.0.1", PORT, session_id="task")

    assert conflict is not None
    assert conflict["answering_pid"] is None
    assert conflict["answering_is_owner"] is None


class ConflictInspector:
    """A session listening on `PORT`, whose port a foreign process also holds."""

    def __init__(self) -> None:
        self.conflict: dict[str, Any] | None = {
            "host": "127.0.0.1",
            "port": PORT,
            "listeners": [{"pid": 1, "owner": True}, {"pid": 2, "owner": False}],
            "answering_pid": 2,
            "answering_is_owner": False,
        }

    def _group(self) -> dict[str, Any]:
        return {
            "session_id": "session-a",
            "project_id": "default",
            "processes": [
                {
                    "pid": 1,
                    "command": "python.exe logger.py",
                    "listeners": [
                        {
                            "host": "127.0.0.1",
                            "port": PORT,
                            "loopback": True,
                            "url": f"http://127.0.0.1:{PORT}/",
                        }
                    ],
                }
            ],
        }

    async def snapshot(self, session_id: str, *, force: bool = False) -> dict[str, Any]:
        return {"available": True, **self._group()}

    async def snapshot_all(self) -> dict[str, Any]:
        return {"available": True, "sessions": [self._group()]}

    async def port_conflict(
        self, host: str, port: int, *, session_id: str
    ) -> dict[str, Any] | None:
        return self.conflict


def _registry(inspector: Any) -> PreviewRegistry:
    async def browser_facing(_url: str) -> PreviewProbeResult:
        return PreviewProbeResult(True, 200, "text/html", "test")

    session = FakeSession(session_record("session-a", "default"))
    sessions = FakeSessionManager({"session-a": session})
    return PreviewRegistry(cast(Any, inspector), cast(Any, sessions), preview_probe=browser_facing)


@pytest.mark.asyncio
async def test_the_preview_list_carries_the_conflict_and_logs_it_once(
    caplog: pytest.LogCaptureFixture,
) -> None:
    inspector = ConflictInspector()
    registry = _registry(inspector)
    caplog.set_level(logging.INFO, logger="swe_mux.processes")

    first = await registry.list()
    second = await registry.list()

    [item] = first["items"]
    assert item["port_conflict"]["answering_pid"] == 2
    assert second["items"][0]["port_conflict"] == item["port_conflict"]
    warnings = [r for r in caplog.records if "preview port shared" in r.getMessage()]
    assert len(warnings) == 1
    assert warnings[0].levelno == logging.WARNING

    inspector.conflict = None
    cleared = await registry.list()
    assert cleared["items"][0]["port_conflict"] is None
    assert any("no longer shared" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_a_live_conflict_is_never_written_into_the_registration() -> None:
    registry = _registry(ConflictInspector())
    await registry.list()

    [item] = registry.items.values()
    assert "port_conflict" not in item.snapshot()


@pytest.mark.skipif(sys.platform == "darwin", reason="macOS names no socket owner without root")
@pytest.mark.asyncio
async def test_the_answerer_probe_names_the_process_that_accepted_the_connection() -> None:
    accepted: list[asyncio.StreamWriter] = []

    async def hold(_reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        accepted.append(writer)

    server = await asyncio.start_server(hold, "127.0.0.1", 0)
    try:
        port = int(server.sockets[0].getsockname()[1])
        assert await probe_listener_answerer("127.0.0.1", port) == os.getpid()
    finally:
        server.close()
        for writer in accepted:
            writer.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_a_port_nothing_listens_on_has_no_answerer() -> None:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = int(probe.getsockname()[1])
    assert await probe_listener_answerer("127.0.0.1", port) is None


_SHARED_LISTENER = """
import os, socket, sys
sock = socket.socket()
sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
sock.bind(("127.0.0.1", int(sys.argv[1])))
sock.listen(8)
print(os.getpid(), flush=True)
sys.stdin.read()
"""


@pytest.mark.skipif(
    sys.platform != "win32", reason="Windows lets SO_REUSEADDR share a listening port"
)
@pytest.mark.asyncio
async def test_two_processes_really_share_a_port_and_the_probe_names_one_of_them() -> None:
    """The incident's mechanism, reproduced rather than described.

    Two processes each bind the same loopback port with `SO_REUSEADDR` and neither
    gets an error. The machine-wide index sees both; the probe names one of them.
    Which one the kernel prefers is not asserted - that is the OS's choice and the
    point is that swe-mux reports it rather than assuming it.
    """
    with socket.socket() as free:
        free.bind(("127.0.0.1", 0))
        port = int(free.getsockname()[1])
    children = [
        await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            _SHARED_LISTENER,
            str(port),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
        )
        for _ in range(2)
    ]
    try:
        # Each child reports its own pid once it is listening: a venv's `python.exe`
        # on Windows is a launcher that runs the interpreter as a child of its own,
        # so the spawned pid is not the process holding the socket - which is exactly
        # the parent/child split the incident's orphans had.
        pids = set()
        for child in children:
            assert child.stdout is not None
            pids.add(int(await child.stdout.readline()))
        inspector = ProcessInspector(cast(Any, FakeSessionManager({})), EventBus())

        def both_listed() -> bool:
            inspector._listen_index = index_listeners(inspector._connections_by_pid())
            return pids <= inspector.listener_pids("127.0.0.1", port)

        await until(both_listed)
        conflict = await inspector.port_conflict("127.0.0.1", port, session_id="none")
        assert conflict is not None
        assert {item["pid"] for item in conflict["listeners"]} >= pids
        assert conflict["answering_pid"] in pids
        assert all(item["executable"] for item in conflict["listeners"] if item["pid"] in pids)
    finally:
        # Closing stdin ends each child; waiting it out here is what closes its
        # transport before the loop does.
        for child in children:
            await asyncio.wait_for(child.communicate(b""), 10)
