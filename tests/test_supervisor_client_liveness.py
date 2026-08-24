"""Tri-state liveness and ticker gating (roadmap S2.1/S2.2, audit F1).

The defect these pin: `RemotePtyHost.isalive()` was `self._alive and
self.client.connected`, the 1s per-session ticker read a False as process exit,
and `_mark_ended` persisted it. A socket fault with the supervisor process still
running therefore ended every live session durably - the agents kept working
invisibly, history recorded exits that never happened, and the next daemon start
re-adopted sessions the UI had shown as finished. Nothing tested it.

A session may become durably ended on exactly two pieces of evidence: a
definitive `pty_exit` frame, or a supervisor death this client confirmed (which
closed its kill-on-close job and really did reap the trees). Every test here is
one of those two, or the third case that must NOT end anything.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from swe_mux import supervisor_client
from swe_mux.adapters import ShellAdapter
from swe_mux.models import SessionRecord
from swe_mux.session import Session, SessionManager
from swe_mux.supervisor_client import Liveness, RemotePtyHost, SupervisorClient, liveness_of

from .support.fake_supervisor import FakeSupervisor
from .support.settle import until


async def connected_client(tmp_path: Path) -> tuple[FakeSupervisor, SupervisorClient]:
    supervisor = FakeSupervisor(tmp_path)
    await supervisor.start()
    client = await SupervisorClient.connect(tmp_path)
    return supervisor, client


async def spawned_host(client: SupervisorClient, sid: str = "s1") -> RemotePtyHost:
    host = RemotePtyHost(client, sid, appname="cmd.exe", cwd=".")
    host.prepare()
    await asyncio.to_thread(host.spawn)
    return host


def make_manager() -> SessionManager:
    """A manager with just enough wiring for the ticker; nothing here does I/O."""
    return SessionManager(
        {"shell": ShellAdapter()},
        cast(Any, SimpleNamespace()),
        cast(Any, SimpleNamespace()),
        cast(Any, SimpleNamespace()),
        1024,
        "http://127.0.0.1:1",
    )


def make_session(pty: Any, sid: str = "s1") -> Session:
    record = SessionRecord(
        sid,
        sid,
        "project",
        "shell",
        "native",
        ".",
        "cmd.exe",
        [],
        state="running",
    )
    return Session(record, pty, ShellAdapter(), 1024, "secret")


class TickerHarness:
    """Runs the real `_ticker` and records whether it ended the session."""

    def __init__(self, pty: Any) -> None:
        self.manager = make_manager()
        self.session = make_session(pty)
        self.ended: list[str] = []

        async def record_end(session: Session, reason: str) -> None:
            self.ended.append(reason)
            session.record.state = "exited"

        self.manager._mark_ended = record_end  # type: ignore[method-assign]
        self.task: asyncio.Task[None] | None = None

    def start(self) -> None:
        self.task = asyncio.create_task(self.manager._ticker(self.session))

    async def stop(self) -> None:
        if self.task is not None and not self.task.done():
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)


async def test_liveness_is_alive_while_the_connection_is_up(tmp_path: Path) -> None:
    supervisor, client = await connected_client(tmp_path)
    try:
        host = await spawned_host(client)

        assert host.liveness() is Liveness.ALIVE
        assert host.isalive() is True
        assert liveness_of(host) is Liveness.ALIVE
    finally:
        await client.close()
        await supervisor.stop()


async def test_socket_loss_with_a_live_supervisor_ends_no_session(tmp_path: Path) -> None:
    """The F1 test that was missing: dropped socket, supervisor alive, nothing dies."""
    supervisor, client = await connected_client(tmp_path)
    try:
        host_a = await spawned_host(client, "a")
        host_b = await spawned_host(client, "b")

        await supervisor.drop_connection()
        await until(lambda: not client.connected, what="the client noticed the drop")

        # The supervisor pid in the discovery file is this test process, which is
        # very much alive - the exact condition that used to fabricate exits.
        assert client.lost is True
        assert client.lost_reason
        for host in (host_a, host_b):
            assert host.liveness() is Liveness.UNREACHABLE
            assert host.isalive() is True, "an unreachable session is not a dead one"
            assert host.exit_status() is None
            assert host.output_queue.empty(), "no end-of-output sentinel may be queued"
    finally:
        await client.close()
        await supervisor.stop()


async def test_confirmed_supervisor_death_marks_sessions_dead(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other half: a pid that is really gone took its kill-on-close job with it."""
    supervisor, client = await connected_client(tmp_path)
    try:
        host = await spawned_host(client)
        monkeypatch.setattr(supervisor_client, "_pid_running", lambda _pid: False)

        await supervisor.drop_connection()
        await until(lambda: not client.connected, what="the client noticed the drop")

        assert client.lost is False
        assert host.liveness() is Liveness.DEAD
        assert host.isalive() is False
        assert await asyncio.wait_for(host.output_queue.get(), timeout=5) == b""
    finally:
        await client.close()
        await supervisor.stop()


async def test_pty_exit_frame_marks_the_session_dead(tmp_path: Path) -> None:
    supervisor, client = await connected_client(tmp_path)
    try:
        host = await spawned_host(client)

        supervisor.send({"t": "pty_exit", "sid": "s1", "exit_code": 3})
        await until(lambda: not host._alive, what="the pty_exit frame was applied")

        assert host.liveness() is Liveness.DEAD
        assert host.exit_status() == 3
        assert await asyncio.wait_for(host.output_queue.get(), timeout=5) == b""
    finally:
        await client.close()
        await supervisor.stop()


async def test_ticker_freezes_instead_of_ending_an_unreachable_session(tmp_path: Path) -> None:
    supervisor, client = await connected_client(tmp_path)
    harness = TickerHarness(await spawned_host(client))
    try:
        harness.start()
        await supervisor.drop_connection()
        await until(lambda: not client.connected, what="the client noticed the drop")
        await until(
            lambda: harness.session.supervisor_unreachable_since is not None,
            seconds=10,
            what="the ticker recorded the unreachable window",
        )

        assert harness.ended == [], "an unreachable session must never be ended"
        assert harness.session.record.state == "running"
    finally:
        await harness.stop()
        await client.close()
        await supervisor.stop()


async def test_ticker_ends_the_session_on_a_definitive_pty_exit(tmp_path: Path) -> None:
    supervisor, client = await connected_client(tmp_path)
    harness = TickerHarness(await spawned_host(client))
    try:
        harness.start()
        supervisor.send({"t": "pty_exit", "sid": "s1", "exit_code": 0})
        await until(lambda: bool(harness.ended), seconds=10, what="the ticker ended the session")

        assert harness.ended == ["process_exit"]
        assert harness.session.supervisor_unreachable_since is None
    finally:
        await harness.stop()
        await client.close()
        await supervisor.stop()


async def test_ticker_clears_the_unreachable_window_when_the_socket_returns(
    tmp_path: Path,
) -> None:
    """Reachability is restored by reconnecting; the frozen session resumes, unended."""
    supervisor, client = await connected_client(tmp_path)
    host = await spawned_host(client)
    harness = TickerHarness(host)
    try:
        harness.start()
        await supervisor.drop_connection()
        await until(lambda: not client.connected, what="the client noticed the drop")
        await until(
            lambda: harness.session.supervisor_unreachable_since is not None,
            seconds=10,
            what="the ticker recorded the unreachable window",
        )

        # Stand in for the reattach a daemon restart performs: the same host, a
        # connection that works again.
        client.connected = True
        client.lost = False
        await until(
            lambda: harness.session.supervisor_unreachable_since is None,
            seconds=10,
            what="the ticker cleared the unreachable window",
        )

        assert harness.ended == []
    finally:
        await harness.stop()
        await client.close()
        await supervisor.stop()


def test_liveness_of_an_in_process_host_is_two_state() -> None:
    """A local PtyHost cannot be unreachable: it is observed inside this process."""
    assert liveness_of(SimpleNamespace(isalive=lambda: True)) is Liveness.ALIVE
    assert liveness_of(SimpleNamespace(isalive=lambda: False)) is Liveness.DEAD
