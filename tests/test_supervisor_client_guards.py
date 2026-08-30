"""Guards on the two irreversible daemon-side actions (S2.3/S2.6, audit F2/F7).

Both are "act on evidence you do not have" defects:

- a spawn RPC that did not answer used to fall back to an in-process PTY
  unconditionally, while the supervisor finished the spawn anyway - two agents,
  one workspace;
- `swemuxd --shutdown` used to terminate whatever pid the discovery file named as
  long as its process name contained "swe", which after a pid recycle is an
  unrelated program, killed along with every process tree it owns.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from swe_mux import supervisor_client
from swe_mux.adapters import ShellAdapter
from swe_mux.models import SessionRecord
from swe_mux.server import error_middleware
from swe_mux.session import Session, SessionManager
from swe_mux.supervisor import discovery_path
from swe_mux.supervisor_client import (
    RemotePtyHost,
    SupervisorClient,
    SupervisorUnavailable,
    _supervisor_identity_check,
    _terminate_supervisor,
)

from .support.fake_supervisor import FakeSupervisor, default_responder
from .support.settle import until

SPAWN_FAILURE = SupervisorUnavailable("no reply to spawn")


def status_responder(state: str | None, *, pid: int = 4321) -> Any:
    """A supervisor whose spawn never answers, and whose spawn_status does."""

    def responder(header: dict[str, Any], payload: bytes) -> dict[str, Any] | None:
        kind = header.get("t")
        if kind == "spawn":
            return None  # the reply the daemon never receives
        if kind == "spawn_status":
            if state is None:
                return {"ok": False, "error": "unknown message type: spawn_status"}
            return {"ok": True, "state": state, "pid": pid, "started_at": time.time()}
        return default_responder(header, payload)

    return responder


async def resolve(
    tmp_path: Path, state: str | None
) -> tuple[FakeSupervisor, SupervisorClient, RemotePtyHost, RemotePtyHost | None]:
    supervisor = FakeSupervisor(tmp_path, responder=status_responder(state))
    await supervisor.start()
    client = await SupervisorClient.connect(tmp_path)
    manager = cast(Any, SimpleNamespace(supervisor=client))
    host = RemotePtyHost(client, "s1", appname="cmd.exe", cwd=".")
    host.prepare()
    outcome = await SessionManager._resolve_failed_supervisor_spawn(manager, host, SPAWN_FAILURE)
    return supervisor, client, host, outcome


async def test_a_live_spawn_is_adopted_instead_of_duplicated(tmp_path: Path) -> None:
    supervisor, client, host, outcome = await resolve(tmp_path, "live")
    try:
        assert outcome is host, "the supervisor has the session; a fallback would duplicate it"
        assert host.isalive() is True
        assert host.pid == 4321
        assert client.hosts.get("s1") is host
    finally:
        await client.close()
        await supervisor.stop()


async def test_a_reserved_spawn_is_adopted_but_not_reported_alive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # `reserved` is the one state that polls; shorten the production 30s bound
    # rather than serve it out in the gate.
    monkeypatch.setattr(supervisor_client, "SPAWN_STATUS_DEADLINE_SECONDS", 1.0)
    supervisor, client, host, outcome = await resolve(tmp_path, "reserved")
    try:
        assert outcome is host
        # Reserved means the id is taken and the child may or may not exist. The
        # ticker ends it and the release that follows tells the supervisor to
        # stop whatever did appear; claiming it alive would strand it instead.
        assert host.isalive() is False
    finally:
        await client.close()
        await supervisor.stop()


async def test_an_exited_spawn_is_adopted_so_the_record_shows_what_happened(
    tmp_path: Path,
) -> None:
    supervisor, client, host, outcome = await resolve(tmp_path, "exited")
    try:
        assert outcome is host
        assert host.isalive() is False
    finally:
        await client.close()
        await supervisor.stop()


async def test_a_failure_before_reservation_falls_back_in_process(tmp_path: Path) -> None:
    supervisor, client, host, outcome = await resolve(tmp_path, "unknown")
    try:
        assert outcome is None, "the supervisor never reserved the id; a local PTY is safe"
        assert "s1" not in client.hosts, "the reserved host must not stay registered"
    finally:
        await client.close()
        await supervisor.stop()


async def test_an_older_supervisor_keeps_the_old_behavior(tmp_path: Path) -> None:
    """No `spawn_status` to ask: the documented ambiguity, taken deliberately."""
    supervisor, client, host, outcome = await resolve(tmp_path, None)
    try:
        assert outcome is None
        assert supervisor.requests_of("spawn_status"), "the query must still be attempted"
    finally:
        await client.close()
        await supervisor.stop()


async def test_an_unanswerable_query_with_a_live_supervisor_refuses_to_fall_back(
    tmp_path: Path,
) -> None:
    supervisor = FakeSupervisor(tmp_path, responder=status_responder("live"))
    await supervisor.start()
    client = await SupervisorClient.connect(tmp_path)
    try:
        host = RemotePtyHost(client, "s1", appname="cmd.exe", cwd=".")
        host.prepare()
        await supervisor.drop_connection()
        await until(lambda: not client.connected, what="the client noticed the drop")
        manager = cast(Any, SimpleNamespace(supervisor=client))

        with pytest.raises(SupervisorUnavailable, match="refusing an in-process fallback"):
            await SessionManager._resolve_failed_supervisor_spawn(manager, host, SPAWN_FAILURE)
    finally:
        await client.close()
        await supervisor.stop()


async def test_an_unanswerable_query_with_a_dead_supervisor_falls_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    supervisor = FakeSupervisor(tmp_path, responder=status_responder("live"))
    await supervisor.start()
    client = await SupervisorClient.connect(tmp_path)
    try:
        host = RemotePtyHost(client, "s1", appname="cmd.exe", cwd=".")
        host.prepare()
        monkeypatch.setattr(supervisor_client, "_pid_running", lambda _pid: False)
        await supervisor.drop_connection()
        await until(lambda: not client.connected, what="the client noticed the drop")
        manager = cast(Any, SimpleNamespace(supervisor=client))

        outcome = await SessionManager._resolve_failed_supervisor_spawn(
            manager, host, SPAWN_FAILURE
        )

        assert outcome is None, "a dead supervisor's job already reaped any child"
    finally:
        await client.close()
        await supervisor.stop()


async def test_spawn_status_reports_an_unrecognised_state_as_no_evidence(
    tmp_path: Path,
) -> None:
    supervisor = FakeSupervisor(tmp_path, responder=status_responder("halfway"))
    await supervisor.start()
    client = await SupervisorClient.connect(tmp_path)
    try:
        status = await client.spawn_status("s1")

        assert status.state == "indeterminate"
        assert status.is_evidence is False
        assert status.reserved_or_live is False
    finally:
        await client.close()
        await supervisor.stop()


# -- discovery kill guard ------------------------------------------------------


class FakeProcess:
    """Only what `_terminate_supervisor` reads, plus what it does."""

    def __init__(self, created: float, cmdline: list[str]) -> None:
        self._created = created
        self._cmdline = cmdline
        self.terminated = False
        self.waited = False

    def create_time(self) -> float:
        return self._created

    def cmdline(self) -> list[str]:
        return self._cmdline

    def terminate(self) -> None:
        self.terminated = True

    def wait(self, timeout: float | None = None) -> int:
        self.waited = True
        return 0


def install(monkeypatch: pytest.MonkeyPatch, process: FakeProcess) -> None:
    import psutil

    monkeypatch.setattr(psutil, "Process", lambda _pid: process)


CONFIG = "C:/tmp/mux/config.toml"
SUPERVISOR_ARGV = ["python.exe", "-m", "swe_mux.supervisor", "--config", CONFIG]


def test_a_verified_supervisor_is_terminated(monkeypatch: pytest.MonkeyPatch) -> None:
    started = time.time()
    process = FakeProcess(started - 0.4, SUPERVISOR_ARGV)
    install(monkeypatch, process)

    assert _terminate_supervisor(4321, started_at=started, config_path=CONFIG) is True
    assert process.terminated is True


def test_a_recycled_pid_is_never_terminated(monkeypatch: pytest.MonkeyPatch) -> None:
    """The F7 case: the pid is live, and it belongs to somebody else now."""
    started = time.time()
    process = FakeProcess(started + 4000, ["swe-something-unrelated.exe"])
    install(monkeypatch, process)

    assert _terminate_supervisor(4321, started_at=started, config_path=CONFIG) is False
    assert process.terminated is False


def test_a_missing_started_at_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    process = FakeProcess(time.time(), SUPERVISOR_ARGV)
    install(monkeypatch, process)

    assert _terminate_supervisor(4321, started_at=None, config_path=CONFIG) is False
    assert process.terminated is False


def test_a_matching_pid_with_the_wrong_command_line_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = time.time()
    process = FakeProcess(started - 0.2, ["swe-mux.exe", "--serve"])
    install(monkeypatch, process)

    assert _terminate_supervisor(4321, started_at=started, config_path=CONFIG) is False
    assert process.terminated is False


def test_a_supervisor_for_another_config_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    started = time.time()
    process = FakeProcess(started - 0.2, SUPERVISOR_ARGV)
    install(monkeypatch, process)

    assert (
        _terminate_supervisor(4321, started_at=started, config_path="C:/other/config.toml") is False
    )
    assert process.terminated is False


def test_unreadable_process_details_fail_closed() -> None:
    class Denied(FakeProcess):
        def cmdline(self) -> list[str]:
            raise PermissionError("access is denied")

    process = Denied(time.time() - 1, SUPERVISOR_ARGV)

    rejection = _supervisor_identity_check(process, started_at=time.time(), config_path=CONFIG)

    assert "command line could not be read" in rejection


def test_every_supported_launch_shape_verifies() -> None:
    started = time.time()
    for argv in (
        ["python.exe", "-m", "swe_mux.supervisor", "--config", CONFIG],
        ["swe-mux.exe", "--supervisor-child", "--config", CONFIG],
        ["D:/dist/swe-mux-supervisor/swe-mux-supervisor.exe", "--config", CONFIG],
    ):
        process = FakeProcess(started - 1.0, argv)

        assert _supervisor_identity_check(process, started_at=started, config_path=CONFIG) == "", (
            argv
        )


async def test_kill_server_declines_an_unverifiable_discovery_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End to end: an unusable supervisor whose identity cannot be proven survives."""
    discovery_path(tmp_path).parent.mkdir(parents=True, exist_ok=True)
    discovery_path(tmp_path).write_text(
        json.dumps({"pid": 4321, "port": 1, "token": "t", "config_path": CONFIG}),
        encoding="utf-8",
    )
    process = FakeProcess(time.time(), SUPERVISOR_ARGV)
    install(monkeypatch, process)
    monkeypatch.setattr(supervisor_client, "_pid_running", lambda _pid: True)

    assert await supervisor_client.kill_server(cast(Any, SimpleNamespace(data_dir=tmp_path))) is (
        False
    )
    assert process.terminated is False, "no started_at means no proof means no kill"
    assert discovery_path(tmp_path).exists()


# -- task registry -------------------------------------------------------------


def cwd_manager() -> SessionManager:
    return SessionManager(
        {"shell": ShellAdapter()},
        cast(Any, SimpleNamespace()),
        cast(Any, SimpleNamespace()),
        cast(Any, SimpleNamespace()),
        1024,
        "http://127.0.0.1:1",
    )


def cwd_session() -> Session:
    record = SessionRecord(
        "s1", "s1", "project", "shell", "native", ".", "cmd.exe", [], state="running"
    )
    return Session(
        record, cast(Any, SimpleNamespace(isalive=lambda: True)), ShellAdapter(), 1024, "secret"
    )


async def drain(session: Session) -> None:
    """Cancel whatever debounce is in flight and let the callbacks run."""
    if session.cwd_debounce_task is not None and not session.cwd_debounce_task.done():
        session.cwd_debounce_task.cancel()
        await asyncio.gather(session.cwd_debounce_task, return_exceptions=True)
    await until(lambda: not session.tasks, what="every finished task left the registry")


async def test_osc7_cwd_telemetry_tasks_do_not_accumulate(tmp_path: Path) -> None:
    """Audit F9: a shell emits OSC 7 once per prompt, for as long as it lives."""
    manager = cwd_manager()
    session = cwd_session()
    for index in range(50):
        directory = tmp_path / f"dir-{index}"
        directory.mkdir()
        manager._queue_runtime_cwd(session, directory.resolve().as_uri())

    assert session.tasks, "the fixture must actually have scheduled work"
    await drain(session)


async def test_hook_cwd_telemetry_tasks_do_not_accumulate(tmp_path: Path) -> None:
    """The same leak on the hook path, which fires once per agent turn."""
    manager = cwd_manager()
    session = cwd_session()
    for index in range(50):
        directory = tmp_path / f"hook-{index}"
        directory.mkdir()
        manager.note_hook_cwd(session, {"cwd": str(directory)})

    assert session.tasks, "the fixture must actually have scheduled work"
    await drain(session)


async def test_the_refusal_reaches_the_operator_with_a_status_and_a_reason(
    tmp_path: Path,
) -> None:
    """The refusal above is only useful if the person who can act on it sees it.

    Found in the D1 soak: `SupervisorUnavailable` subclasses `RuntimeError` and
    matched no clause in `error_middleware`, so the refused spawn came back as
    `500 {"error": "internal server error"}`. The daemon had just written the
    whole reason to its log, and the operator - who is the only one who can act
    on it, by restarting the daemon - was told nothing.

    The exception is raised by the real refusal path rather than constructed, so
    this cannot pass against a message the code no longer produces.
    """
    supervisor = FakeSupervisor(tmp_path, responder=status_responder("live"))
    await supervisor.start()
    client = await SupervisorClient.connect(tmp_path)
    try:
        host = RemotePtyHost(client, "s1", appname="cmd.exe", cwd=".")
        host.prepare()
        await supervisor.drop_connection()
        await until(lambda: not client.connected, what="the client noticed the drop")
        manager = cast(Any, SimpleNamespace(supervisor=client))
        with pytest.raises(SupervisorUnavailable) as raised:
            await SessionManager._resolve_failed_supervisor_spawn(manager, host, SPAWN_FAILURE)
        refusal = raised.value
    finally:
        await client.close()
        await supervisor.stop()

    async def spawn(_request: web.Request) -> web.Response:
        raise refusal

    app = web.Application(middlewares=[error_middleware])
    app.router.add_post("/api/sessions", spawn)
    async with TestClient(TestServer(app)) as http:
        response = await http.post("/api/sessions", json={})
        payload = await response.json()

    # 503, not 500: nothing was created, the daemon is refusing on purpose, and
    # the condition clears when the daemon is restarted.
    assert response.status == 503
    assert payload["code"] == "supervisor_unreachable"
    assert "refusing an in-process fallback" in payload["error"]
    assert str(client.supervisor_pid) in payload["error"]


async def test_a_timeout_is_a_deadline_not_an_internal_error() -> None:
    """`TimeoutError` subclasses `OSError`, so it used to fall through to 500.

    The same trip the refusal took: nothing above the generic clause matched it,
    and a body reading "internal server error" told the caller neither that a
    deadline had expired nor that retrying was reasonable.
    """

    async def slow(_request: web.Request) -> web.Response:
        raise TimeoutError("no reply within 5s")

    app = web.Application(middlewares=[error_middleware])
    app.router.add_get("/slow", slow)
    async with TestClient(TestServer(app)) as http:
        response = await http.get("/slow")
        payload = await response.json()

    assert response.status == 504
    assert payload == {"error": "no reply within 5s", "code": "timeout"}

