"""Crash/hang recovery never sacrifices sessions or races an intentional handoff."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

import psutil
import pytest

from swe_mux import daemon_recovery as recovery
from swe_mux import lifecycle
from swe_mux.subprocess_flags import background_creation_flags


class Fleet:
    def __init__(self, path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.path = path
        self.now = 100.0
        self.healthy = False
        self.states = {123: "gone", 456: "alive", 789: "alive"}
        self.supervisor: dict[str, Any] | None = {"pid": 789, "created_at": 1.0}
        self.spawned: list[int] = []
        self.terminated: list[int] = []
        self.deploying = False
        self.stop = threading.Event()
        self.monitor = recovery.DaemonRecovery(
            path,
            "test-secret",
            health=lambda: self.healthy,
            spawn=self.spawn,
            stop=self.stop,
            monotonic=lambda: self.now,
            wall_clock=lambda: self.now,
        )
        monkeypatch.setattr(
            recovery, "process_state", lambda r: self.states.get(r["pid"], "unknown")
        )
        monkeypatch.setattr(recovery, "supervisor_identity", lambda _: self.supervisor)
        monkeypatch.setattr(recovery, "terminate_generation", self.terminate)
        monkeypatch.setattr(self.monitor, "_redeploying", lambda: self.deploying)
        self.record = {
            "pid": 123,
            "created_at": 1.0,
            "owner": recovery.token_digest("test-secret"),
            "ready": True,
            "local_pty": False,
            "supervisor": self.supervisor,
            "intent": None,
        }
        self.write()

    def write(self, **changes: Any) -> None:
        self.record.update(changes)
        (self.path / recovery.RECORD_NAME).write_text(json.dumps(self.record))

    def spawn(self) -> dict[str, Any]:
        self.spawned.append(456)
        return {"pid": 456, "created_at": 2.0}

    def terminate(self, record: dict[str, Any]) -> bool:
        self.terminated.append(record["pid"])
        self.states[record["pid"]] = "gone"
        return True

    def step(self, seconds: float = 0) -> None:
        self.now += seconds
        self.monitor.step()

    def heartbeat(self, age: float = 0.0) -> None:
        """Leave a daemon heartbeat `age` seconds old, as the daemon's loop would."""
        (self.path / lifecycle.HEARTBEAT_NAME).write_text(
            json.dumps({"pid": 123, "heartbeat_at": self.now - age, "clean_exit": False})
        )

    def ledger(self) -> str:
        return (self.path / "lifecycle.log").read_text()


@pytest.fixture
def fleet(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Fleet:
    return Fleet(tmp_path, monkeypatch)


def test_dead_daemon_restarts_and_follows_a_self_restarted_successor(fleet: Fleet) -> None:
    fleet.step()
    fleet.step(recovery.DEAD_GRACE_SECONDS)
    assert fleet.spawned == [456]
    assert fleet.terminated == []
    # A process that has not published its startup record is already a replacement.
    fleet.step(30)
    assert fleet.spawned == [456]
    fleet.write(pid=456, created_at=2.0)
    fleet.healthy = True
    fleet.step()
    # It later self-restarts to a process the desktop did not parent.
    fleet.write(pid=999, created_at=3.0)
    fleet.states[999] = "gone"
    fleet.healthy = False
    fleet.step()
    fleet.step(recovery.DEAD_GRACE_SECONDS)
    assert fleet.spawned == [456, 456]


def test_a_persistent_hang_replaces_only_the_daemon(fleet: Fleet) -> None:
    fleet.states[123] = "alive"
    fleet.step()
    fleet.step(recovery.HANG_SECONDS - 1)
    assert not fleet.terminated
    fleet.step(1)
    assert fleet.terminated == [123]
    assert fleet.spawned == [456]
    assert fleet.states[789] == "alive"
    assert "state=restarting_hung pid=123" in (fleet.path / "lifecycle.log").read_text()


@pytest.mark.parametrize(
    "condition",
    [
        "local",
        "unknown",
        "supervisor_lost",
        "supervisor_changed",
        "starting",
        "unmanaged",
        "quit",
        "redeploy",
        "stopped",
    ],
)
def test_protected_and_uncertain_states_are_never_terminated(fleet: Fleet, condition: str) -> None:
    fleet.states[123] = "alive"
    if condition == "local":
        fleet.write(local_pty=True)
    elif condition == "unknown":
        fleet.states[123] = "unknown"
    elif condition == "supervisor_lost":
        fleet.supervisor = None
    elif condition == "supervisor_changed":
        fleet.supervisor = {"pid": 790, "created_at": 7.0}
    elif condition == "starting":
        fleet.write(ready=False)
    elif condition == "unmanaged":
        fleet.write(owner="another-desktop")
    elif condition == "quit":
        fleet.write(intent="quit")
    elif condition == "redeploy":
        fleet.deploying = True
    elif condition == "stopped":
        fleet.stop.set()
    fleet.step()
    fleet.step(1000)
    assert not fleet.terminated
    assert not fleet.spawned


def test_brief_misses_and_health_returning_at_commit_do_not_restart(fleet: Fleet) -> None:
    fleet.states[123] = "alive"
    fleet.step()
    fleet.healthy = True
    fleet.step(recovery.HANG_SECONDS)
    fleet.healthy = False
    fleet.step()
    fleet.step(recovery.HANG_SECONDS - 1)
    assert not fleet.spawned
    answers = iter([False, True])
    fleet.monitor.health = lambda: next(answers)
    fleet.step(1)
    assert not fleet.terminated


def test_manual_restart_pauses_automatic_recovery(fleet: Fleet) -> None:
    fleet.step()
    fleet.monitor.pause.set()
    fleet.step(100)
    assert not fleet.spawned


def test_failed_planned_handoff_gets_a_bounded_recovery_grace(fleet: Fleet) -> None:
    fleet.write(intent="detach")
    fleet.step()
    fleet.step(recovery.HANDOFF_SECONDS - 1)
    assert not fleet.spawned
    fleet.step(1)
    assert fleet.spawned == [456]


def test_repeated_startup_crashes_are_rate_limited(fleet: Fleet) -> None:
    fleet.states[456] = "gone"
    fleet.step()
    for _ in range(recovery.MAX_RESTARTS + 2):
        fleet.step(recovery.DEAD_GRACE_SECONDS)
    assert len(fleet.spawned) == recovery.MAX_RESTARTS
    assert "state=retry_limit" in (fleet.path / "lifecycle.log").read_text()
    fleet.step(recovery.RETRY_WINDOW_SECONDS)
    assert len(fleet.spawned) == recovery.MAX_RESTARTS + 1


def test_corrupt_record_does_not_authorize_recovery(fleet: Fleet) -> None:
    (fleet.path / recovery.RECORD_NAME).write_text("{broken")
    fleet.step()
    fleet.step(1000)
    assert not fleet.spawned


def test_a_daemon_whose_loop_still_turns_is_given_the_longer_window(fleet: Fleet) -> None:
    # 2026-09-14: a fifty-session daemon answered every request, just not within
    # the 1s probe, and was killed at 45s for a 111s restart. A fresh heartbeat
    # proves the loop is running, so the probe miss is load (or a dead listener
    # being rebound) and the kill is deferred to the unreachable window.
    fleet.states[123] = "alive"
    fleet.heartbeat()
    fleet.step()
    # The loop keeps writing its heartbeat while the probes keep missing.
    while fleet.now < 100.0 + recovery.HANG_SECONDS + 1:
        fleet.heartbeat()
        fleet.step(10)
    assert not fleet.terminated
    assert "state=unresponsive pid=123" in fleet.ledger()
    assert "loop_stalled" not in fleet.ledger()
    # A live loop that stays unreachable long enough is still replaced: a
    # listener that could not be rebound leaves nothing else that can.
    while fleet.now < 100.0 + recovery.UNREACHABLE_SECONDS:
        fleet.heartbeat()
        fleet.step(10)
    assert fleet.terminated == [123]
    assert fleet.spawned == [456]


def test_a_stale_heartbeat_keeps_the_short_hang_window(fleet: Fleet) -> None:
    fleet.states[123] = "alive"
    fleet.heartbeat(age=recovery.HEARTBEAT_STALE_SECONDS + 1)
    fleet.step()
    fleet.step(recovery.HANG_SECONDS - 1)
    assert not fleet.terminated
    assert "state=unresponsive_loop_stalled pid=123" in fleet.ledger()
    fleet.step(1)
    assert fleet.terminated == [123]


def test_a_recovered_replacement_restores_the_retry_budget(fleet: Fleet) -> None:
    # Three replacements that died at bind within a minute used to leave the
    # budget spent for the real outage that followed minutes later.
    fleet.states[456] = "gone"
    fleet.step()
    for _ in range(recovery.MAX_RESTARTS + 1):
        fleet.step(recovery.DEAD_GRACE_SECONDS)
    assert len(fleet.spawned) == recovery.MAX_RESTARTS
    assert "state=retry_limit" in fleet.ledger()
    # The replacement comes up and stays up.
    fleet.write(pid=456, created_at=2.0)
    fleet.states[456] = "alive"
    fleet.healthy = True
    fleet.step()
    fleet.step(recovery.RECOVERED_STABLE_SECONDS - 1)
    # Not yet: a daemon that answers one probe and dies must not clear its budget.
    fleet.healthy = False
    fleet.states[456] = "gone"
    fleet.step()
    fleet.step(recovery.DEAD_GRACE_SECONDS)
    assert len(fleet.spawned) == recovery.MAX_RESTARTS
    fleet.write(pid=456, created_at=2.0)
    fleet.states[456] = "alive"
    fleet.healthy = True
    fleet.step()
    fleet.step(recovery.RECOVERED_STABLE_SECONDS)
    assert "daemon_recovery budget reset" in fleet.ledger()
    fleet.healthy = False
    fleet.states[456] = "gone"
    fleet.step()
    fleet.step(recovery.DEAD_GRACE_SECONDS)
    assert len(fleet.spawned) == recovery.MAX_RESTARTS + 1


def test_registration_never_clobbers_a_live_generation_with_no_handoff(tmp_path: Path) -> None:
    # A second daemon spawned while the first is still building its runtime
    # used to take the record, die at bind, and leave the monitor restarting a
    # daemon that was fine. The parent process stands in for the live daemon.
    live = {
        "pid": os.getppid(),
        "created_at": psutil.Process(os.getppid()).create_time(),
        "owner": None,
        "ready": False,
        "local_pty": False,
        "supervisor": None,
        "intent": None,
        "intent_at": None,
    }
    (tmp_path / recovery.RECORD_NAME).write_text(json.dumps(live))
    assert recovery.register_daemon(tmp_path, "token") is False
    assert recovery.read_record(tmp_path)["pid"] == os.getppid()
    assert "not registered for recovery" in (tmp_path / "lifecycle.log").read_text()
    # A planned handoff hands the record over while the predecessor is alive.
    live["intent"] = "detach"
    (tmp_path / recovery.RECORD_NAME).write_text(json.dumps(live))
    assert recovery.register_daemon(tmp_path, "token") is True
    assert recovery.read_record(tmp_path)["pid"] == os.getpid()
    # And a dead generation is simply replaced.
    dead = {**live, "pid": 4_000_000_000, "intent": None}
    (tmp_path / recovery.RECORD_NAME).write_text(json.dumps(dead))
    assert recovery.register_daemon(tmp_path, "token") is True
    assert recovery.read_record(tmp_path)["pid"] == os.getpid()


def test_supervisor_probe_accepts_a_large_inventory_and_verifies_its_pid(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import struct

    pid = os.getpid()
    info = {"pid": pid, "port": 12345, "protocol": 1, "token": "private-test-token"}
    (tmp_path / "supervisor.json").write_text(json.dumps(info))
    requests: list[dict[str, Any]] = []

    class Socket:
        def __init__(self, response_pid: int) -> None:
            payload = json.dumps(
                {"ok": True, "id": 0, "pid": response_pid, "sessions": [{"meta": "x" * 32_000}]}
            ).encode()
            self.data = struct.pack(">I", len(payload)) + payload

        def __enter__(self) -> Socket:
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def settimeout(self, seconds: float) -> None:
            assert 0 < seconds <= 1

        def sendall(self, data: bytes) -> None:
            requests.append(json.loads(data[4:]))

        def recv(self, size: int) -> bytes:
            result, self.data = self.data[:size], self.data[size:]
            return result

    monkeypatch.setattr(recovery.socket, "create_connection", lambda *a, **k: Socket(pid))
    assert recovery.supervisor_identity(tmp_path) == {
        "pid": pid,
        "created_at": psutil.Process().create_time(),
    }
    assert [r["t"] for r in requests] == ["hello"]
    monkeypatch.setattr(recovery.socket, "create_connection", lambda *a, **k: Socket(pid + 1))
    assert recovery.supervisor_identity(tmp_path) is None


def test_local_pty_revocation_survives_ready_and_intent_updates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recovery.register_daemon(tmp_path, "token")
    recovery.revoke_for_local_pty(tmp_path)
    monkeypatch.setattr(recovery, "supervisor_identity", lambda _: {"pid": 789, "created_at": 1.0})
    recovery.mark_ready(tmp_path, 789)
    lifecycle.planned_handoff(tmp_path, "detach")
    record = recovery.read_record(tmp_path)
    assert record["local_pty"] is True
    assert record["ready"] is True
    assert record["intent"] == "detach"
    assert "token" not in json.dumps(record)


@pytest.mark.asyncio
@pytest.mark.parametrize("write_fails", [False, True])
async def test_local_spawn_is_fenced_before_pty_allocation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    write_fails: bool,
) -> None:
    from types import SimpleNamespace
    from typing import cast

    from swe_mux.adapters import ShellAdapter
    from swe_mux.session import SessionManager

    recovery.register_daemon(tmp_path, "token")
    manager = SessionManager(
        {"shell": ShellAdapter()},
        cast(Any, SimpleNamespace()),
        cast(Any, SimpleNamespace()),
        cast(Any, SimpleNamespace()),
        1024,
        "http://127.0.0.1:1",
    )
    loop_thread = threading.get_ident()

    def revoke() -> None:
        assert threading.get_ident() != loop_thread
        if write_fails:
            raise OSError("revocation unavailable")
        recovery.revoke_for_local_pty(tmp_path)

    def allocate(*args: Any, **kwargs: Any) -> Any:
        assert not write_fails
        assert recovery.read_record(tmp_path)["local_pty"] is True
        raise OSError("allocation reached after revocation")

    manager.before_local_pty = revoke
    monkeypatch.setattr("swe_mux.session.PtyHost", allocate)
    message = "revocation unavailable" if write_fails else "allocation reached after revocation"
    with pytest.raises(OSError, match=message):
        await manager.spawn(
            backend="shell", name=None, cwd=str(tmp_path), project_id="project", exe=sys.executable
        )


def test_unknown_or_recycled_pid_never_terminates_an_unrelated_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current = psutil.Process()
    record = {"pid": current.pid, "created_at": current.create_time() - 10}
    monkeypatch.setattr(
        psutil.Process, "terminate", lambda _: pytest.fail("PID reuse must not kill")
    )
    assert recovery.process_state(record) == "gone"
    assert recovery.terminate_generation(record) is True  # That generation has already gone.
    assert recovery.process_state({"pid": current.pid, "created_at": float("nan")}) == "unknown"


def test_kernel_fence_excludes_other_processes_and_releases_after_close(tmp_path: Path) -> None:
    script = """
import sys
from pathlib import Path
from swe_mux.daemon_recovery import recovery_lock
try:
    with recovery_lock(Path(sys.argv[1]), timeout=0):
        print('acquired')
except TimeoutError:
    print('blocked')
"""

    def run() -> str:
        return subprocess.check_output(
            [sys.executable, "-c", script, str(tmp_path)],
            timeout=10,
            creationflags=background_creation_flags(),
            text=True,
        ).strip()

    with recovery.recovery_lock(tmp_path):
        assert run() == "blocked"
    assert run() == "acquired"


def test_termination_uses_only_the_exact_scratch_process() -> None:
    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        creationflags=background_creation_flags(),
    )
    try:
        record = {"pid": child.pid, "created_at": psutil.Process(child.pid).create_time()}
        assert recovery.terminate_generation(record)
        child.wait(timeout=5)
        assert psutil.pid_exists(os.getpid())
    finally:
        if child.poll() is None:
            child.terminate()
            child.wait(timeout=5)
