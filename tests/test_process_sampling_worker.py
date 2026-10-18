"""Process sampling in the worker process: `DAEMON_LOAD_ISOLATION.md` item 20.

Two halves. The wire - `DeltaEncoder` on the worker's side, `_decode_delta` and
`_apply_delta` on the daemon's - is pure data and is tested as such, because the
property that matters is that a mirror updated only from deltas ends up equal to
the sampler it mirrors. The rest starts a real worker (`python -m swe_mux.worker`)
against a real process tree, because "the worker attributes what the daemon
would" and "a restarted worker is reloaded rather than trusted" are properties of
two processes and a process table, not of a fake.

The session tree is started under a stand-in supervisor for the same reason the
real one exists: everything this test process starts is its descendant, so
without a traversal boundary the infrastructure walk would claim the session's
processes as swe-mux's own, in the daemon and the worker alike.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import subprocess
import sys
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import psutil
import pytest

from swe_mux.event_bus import EventBus
from swe_mux.framed_ipc import RemoteError, RequestRefused
from swe_mux.models import SessionRecord
from swe_mux.process_service import DeltaEncoder, ProcessService, SampledSessions
from swe_mux.processes import (
    SESSION_FACT_FIELDS,
    OwnedProcess,
    ProcessInspector,
    _decode_delta,
    session_facts,
)
from swe_mux.worker_host import WorkerHost
from tests.support.settle import until

READY_SECONDS = 60.0

# A stand-in for the PTY supervisor: it starts the session root and reports its
# pid, and the root starts one child, so the session owns a tree with a leaf that
# is not its root.
SUPERVISOR = (
    "import subprocess, sys, time\n"
    "child = 'import subprocess, sys, time; "
    "subprocess.Popen([sys.executable, \"-c\", \"import time; time.sleep(300)\"]); "
    "time.sleep(300)'\n"
    "root = subprocess.Popen([sys.executable, '-c', child])\n"
    "print(root.pid, flush=True)\n"
    "time.sleep(300)\n"
)


# --- the wire --------------------------------------------------------------------------


def owned(pid: int, started: float, **overrides: Any) -> OwnedProcess:
    values: dict[str, Any] = {
        "pid": pid,
        "parent_pid": 1,
        "session_id": "session-a",
        "executable": "node.exe",
        "command": "node server.js --port 3000 " + "x" * 400,
        "started_at": started,
        "exited_at": None,
        "cpu_pct": 1.5,
        "memory_bytes": 1024,
        "listeners": [{"host": "127.0.0.1", "port": 3000}],
        "conditions": [],
        "parent_lineage": [{"pid": 1, "creation_time": 1.0}],
    }
    values.update(overrides)
    return OwnedProcess(**values)


def sampler() -> ProcessInspector:
    return ProcessInspector(cast(Any, SampledSessions()), cast(Any, None))


def mirror_of(source: ProcessInspector) -> ProcessInspector:
    """A daemon-side inspector holding an independent copy of `source`'s processes."""
    mirror = ProcessInspector(cast(Any, SimpleNamespace(sessions={})), EventBus())
    mirror.owned = {key: _copy(item) for key, item in source.owned.items()}
    return mirror


def _copy(item: OwnedProcess) -> OwnedProcess:
    snapshot = item.snapshot()
    snapshot.pop("server_eligible")
    return OwnedProcess(**snapshot)


def snapshots(inspector: ProcessInspector) -> dict[tuple[int, float], dict[str, Any]]:
    return {key: item.snapshot() for key, item in inspector.owned.items()}


def test_a_mirror_kept_only_from_deltas_equals_the_sampler_it_mirrors() -> None:
    worker = sampler()
    worker.owned = {
        (100, 10.25): owned(100, 10.25),
        (200, 20.5): owned(200, 20.5, session_id="session-b"),
        (300, 30.125): owned(300, 30.125, exited_at=99.0, evidence_state="exited"),
    }
    encoder = DeltaEncoder()
    encoder.reset(worker)
    mirror = mirror_of(worker)

    # One pass's worth of change: a live process's readings move, one process
    # exits, one is new, one is pruned, and the machine-wide readings change.
    worker.owned[(100, 10.25)].cpu_pct = 42.0
    worker.owned[(100, 10.25)].last_seen = 1234.5
    worker.owned[(200, 20.5)].exited_at = 200.0
    worker.owned[(200, 20.5)].listeners = []
    worker.owned[(400, 40.0)] = owned(400, 40.0, command="python -m http.server")
    del worker.owned[(300, 30.125)]
    worker._daemon_resources = {"pid": 7, "processes": 2, "members": []}
    worker._listen_index = {("127.0.0.1", 3000): frozenset({100, 400})}
    worker._system_cpu_pct = 12.5

    payload = encoder.encode(worker)
    data = json.loads(payload)
    assert [record["pid"] for record in data["upserts"]] == [400]
    assert data["removed"] == [[300, 30.125]]
    patched = {pid: changed for pid, _started, changed in data["patches"]}
    assert set(patched) == {100, 200}
    assert set(patched[100]) == {"cpu_pct", "last_seen"}, "only what changed is sent"
    assert "command" not in patched[200]

    delta = _decode_delta(payload)
    assert mirror._apply_delta(delta) is True
    assert snapshots(mirror) == snapshots(worker)
    assert mirror._daemon_resources == worker._daemon_resources
    assert mirror._listen_index == worker._listen_index
    assert mirror.listener_pids("127.0.0.1", 3000) == frozenset({100, 400})
    assert mirror._system_cpu_pct == 12.5

    # A pass that changed nothing sends nothing about any process.
    quiet = json.loads(encoder.encode(worker))
    assert quiet["upserts"] == [] and quiet["patches"] == [] and quiet["removed"] == []


def test_diagnostics_cross_only_when_they_change() -> None:
    worker = sampler()
    encoder = DeltaEncoder()
    encoder.reset(worker)
    mirror = mirror_of(worker)
    mirror._ownership_diagnostics = [{"kind": "kept"}]

    first = encoder.encode(worker)
    assert "diagnostics" not in json.loads(first), "unchanged since the load"
    mirror._apply_delta(_decode_delta(first))
    assert mirror._ownership_diagnostics == [{"kind": "kept"}], "absent means unchanged"

    worker._ownership_diagnostics.append({"kind": "ambiguous_session_ownership"})
    second = encoder.encode(worker)
    assert json.loads(second)["diagnostics"] == [{"kind": "ambiguous_session_ownership"}]
    mirror._apply_delta(_decode_delta(second))
    assert mirror._ownership_diagnostics == [{"kind": "ambiguous_session_ownership"}]


def test_a_patch_for_a_process_the_mirror_lacks_reports_divergence() -> None:
    worker = sampler()
    worker.owned = {(100, 10.25): owned(100, 10.25)}
    encoder = DeltaEncoder()
    encoder.reset(worker)
    mirror = mirror_of(worker)
    mirror.owned.clear()
    worker.owned[(100, 10.25)].cpu_pct = 3.0
    assert mirror._apply_delta(_decode_delta(encoder.encode(worker))) is False


def test_session_facts_are_exactly_what_the_pass_reads() -> None:
    record = SessionRecord("s", "s", "p", "claude", "native-s", ".", "claude", [])
    record.pid = 4242
    record.root_started_at = 12.5
    facts = session_facts(SimpleNamespace(record=record))
    assert tuple(facts) == SESSION_FACT_FIELDS
    json.dumps(facts)
    sessions = SampledSessions()
    sessions.apply([facts, {"id": 7}, "junk"], supervisor_pid=True)
    assert list(sessions.sessions) == ["s"], "a malformed entry is skipped"
    assert sessions.sessions["s"].record.pid == 4242
    assert sessions.supervisor.supervisor_pid is None, "a bool is not a pid"


async def test_the_worker_refuses_to_sample_before_it_is_loaded() -> None:
    service = ProcessService()
    with pytest.raises(RequestRefused, match="not been loaded"):
        await service.handle("sample", {}, b"{}")
    reply = await service.handle("load", {}, json.dumps({"owned": []}).encode())
    assert reply == {"retained": 0}
    with pytest.raises(RequestRefused, match="unknown method"):
        await service.handle("nothing", {}, b"")


# --- where the pass runs ------------------------------------------------------------------


@dataclass
class FlakyWorker:
    """A worker that is ready and answers every request with an error."""

    ready: bool = True
    enabled: bool = True
    generation: int = 1
    calls: list[str] = field(default_factory=list)

    async def call(self, service: str, method: str, *_: Any, **__: Any) -> Any:
        self.calls.append(f"{service}.{method}")
        raise RemoteError("worker exploded", {})


async def test_a_failing_worker_pass_falls_back_to_a_pass_here(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inspector = ProcessInspector(cast(Any, SimpleNamespace(sessions={})), EventBus())
    collected: list[bool] = []
    monkeypatch.setattr(inspector, "_collect_all", lambda *_: collected.append(True) or [])
    worker = FlakyWorker()
    inspector.attach_worker(worker)

    await inspector._sample_pass(startup=False, prune=False)

    assert worker.calls == ["processes.load"], "a load comes first, and it is what failed"
    assert collected == [True], "the pass ran here instead"
    stats = inspector.sampling_stats()
    assert stats["remote_failures"] == 1 and stats["local_passes"] == 1
    assert "worker exploded" in stats["last_failure"]
    assert stats["in_worker"] is False


# --- a real worker, a real tree --------------------------------------------------------------


@dataclass
class FakePty:
    written: list[bytes] = field(default_factory=list)

    def write(self, data: bytes) -> None:
        self.written.append(data)


@dataclass
class FakeSession:
    record: SessionRecord
    pty: FakePty = field(default_factory=FakePty)


@dataclass
class FakeConfig:
    data_dir: Path
    worker_process_enabled: bool = True
    log_level: str = "INFO"
    gil_switch_interval_ms: float = 5.0


@dataclass
class Tree:
    supervisor: subprocess.Popen[bytes]
    root: int

    def session_pids(self) -> set[int]:
        root = psutil.Process(self.root)
        return {self.root, *(child.pid for child in root.children(recursive=True))}

    def leaf(self) -> int:
        root = psutil.Process(self.root)
        leaves = [child for child in root.children(recursive=True) if not child.children()]
        assert leaves, "the root has started its child"
        return max(leaves, key=lambda child: child.create_time()).pid


def _alive(pid: int) -> bool:
    try:
        return psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False


def _reap(process: subprocess.Popen[bytes]) -> None:
    with contextlib.suppress(psutil.Error):
        for child in psutil.Process(process.pid).children(recursive=True):
            with contextlib.suppress(psutil.Error):
                child.kill()
    with contextlib.suppress(OSError):
        process.kill()
    process.wait(timeout=30)
    if process.stdout is not None:
        process.stdout.close()


@contextlib.contextmanager
def session_tree() -> Iterator[Tree]:
    supervisor = subprocess.Popen(
        [sys.executable, "-c", SUPERVISOR], stdout=subprocess.PIPE, stdin=subprocess.DEVNULL
    )
    try:
        assert supervisor.stdout is not None
        root = int(supervisor.stdout.readline())
        yield Tree(supervisor, root)
    finally:
        _reap(supervisor)


def manager_for(tree: Tree) -> Any:
    record = SessionRecord("s1", "s1", "p1", "claude", "native-s1", ".", "claude", [])
    record.pid = tree.root
    record.state = "running"
    record.root_started_at = psutil.Process(tree.root).create_time()
    return SimpleNamespace(
        sessions={"s1": FakeSession(record)},
        supervisor=SimpleNamespace(supervisor_pid=tree.supervisor.pid),
    )


@contextlib.asynccontextmanager
async def hosting(tmp_path: Path) -> AsyncIterator[WorkerHost]:
    host = WorkerHost(FakeConfig(tmp_path), restart_initial=0.1)
    await host.start()
    try:
        assert await host.wait_ready(READY_SECONDS), host.health()
        yield host
    finally:
        await asyncio.wait_for(host.stop(), timeout=30)


def live_session_rows(inspector: ProcessInspector) -> dict[int, tuple[str, str]]:
    return {
        item.pid: (item.session_id, item.attribution_source)
        for item in inspector.owned.values()
        if item.exited_at is None
    }


def _sleeper_running(tree: Tree) -> bool:
    """Whether the root's child - the leaf interpreter itself, past any launcher - runs.

    Under a Windows virtual environment every `python.exe` here is a launcher with
    the real interpreter as its child, so the tree is two levels deeper than the
    script reads and is still growing well after the root exists.
    """
    for child in psutil.Process(tree.root).children(recursive=True):
        with contextlib.suppress(psutil.Error):
            command = " ".join(child.cmdline())
            if "time.sleep(300)" in command and "subprocess" not in command:
                if not child.children():
                    return True
    return False


async def settled_tree(tree: Tree) -> set[int]:
    """The session's pids once the whole tree has started."""
    await until(lambda: _sleeper_running(tree), seconds=30, what="the session tree started")
    return tree.session_pids()


async def test_a_pass_in_the_worker_attributes_what_a_pass_here_does(tmp_path: Path) -> None:
    with session_tree() as tree:
        expected = await settled_tree(tree)
        sessions = manager_for(tree)
        here = ProcessInspector(sessions, EventBus())
        there = ProcessInspector(sessions, EventBus())
        async with hosting(tmp_path) as host:
            there.attach_worker(host)
            await there.reconcile(startup=True)
            await here.reconcile(startup=True)

            assert set(live_session_rows(there)) == expected
            assert live_session_rows(there) == live_session_rows(here)
            assert live_session_rows(there)[tree.root] == ("s1", "session_root")
            stats = there.sampling_stats()
            assert stats["in_worker"] is True and stats["remote_passes"] == 1, stats
            assert stats["loads"] == 1 and stats["local_passes"] == 0, stats
            # The worker sampled *this* process as the daemon, and counted itself
            # among swe-mux's own processes rather than as nobody's.
            resources = there._daemon_resources
            assert resources["pid"] == os.getpid()
            assert host.pid in {member["pid"] for member in resources["members"]}

            # A second pass sends what moved, not the tree again.
            await there.reconcile()
            stats = there.sampling_stats()
            assert stats["last_upserts"] == 0 and stats["last_patches"] >= 1, stats
            assert set(live_session_rows(there)) == expected

            usage = await there._unique_memory([tree.root])
            assert usage.get(tree.root, 0) > 0, "USS is read in the worker"


async def test_actions_run_in_the_worker_and_a_root_is_interrupted_through_its_pty(
    tmp_path: Path,
) -> None:
    with session_tree() as tree:
        await settled_tree(tree)
        sessions = manager_for(tree)
        inspector = ProcessInspector(sessions, EventBus())
        async with hosting(tmp_path) as host:
            inspector.attach_worker(host)
            await inspector.reconcile(startup=True)
            assert inspector.sampling_stats()["in_worker"] is True

            with pytest.raises(ValueError, match="not owned by this session"):
                await inspector.act("s1", 4, "terminate")

            await inspector.act("s1", tree.root, "interrupt")
            assert sessions.sessions["s1"].pty.written == [b"\x03"]
            assert _alive(tree.root), "the root is interrupted through its PTY, not signalled"

            leaf = tree.leaf()
            await inspector.act("s1", leaf, "terminate")
            await until(lambda: not _alive(leaf), seconds=30, what="the leaf terminated")
            assert _alive(tree.root)


async def test_a_restarted_worker_is_reloaded_before_it_samples(tmp_path: Path) -> None:
    with session_tree() as tree:
        expected = await settled_tree(tree)
        inspector = ProcessInspector(manager_for(tree), EventBus())
        async with hosting(tmp_path) as host:
            inspector.attach_worker(host)
            await inspector.reconcile(startup=True)
            first = host.pid
            assert first is not None
            psutil.Process(first).kill()
            await until(
                lambda: host.ready and host.generation == 2,
                seconds=READY_SECONDS,
                what="a second worker, ready",
            )

            await inspector.reconcile()

            stats = inspector.sampling_stats()
            assert stats["loads"] == 2, "the new worker was given the mirror first"
            assert stats["in_worker"] is True, stats
            assert set(live_session_rows(inspector)) == expected
