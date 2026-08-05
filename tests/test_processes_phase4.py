from __future__ import annotations

import json
import os
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from swe_mux.event_bus import EventBus
from swe_mux.layouts import attach_leaf, layout_terminal_ids, stack_leaf
from swe_mux.processes import (
    OwnedProcess,
    PreviewRegistry,
    ProcessInspector,
    listener_record,
)


class FakeInspector:
    async def snapshot(self, session_id: str, *, force: bool = False) -> dict[str, Any]:
        return {
            "available": True,
            "session_id": session_id,
            "processes": [
                {
                    "pid": 44,
                    "listeners": [
                        {
                            "host": "127.0.0.1",
                            "port": 4321,
                            "loopback": True,
                            "url": "http://127.0.0.1:4321/",
                        }
                    ],
                }
            ],
        }


def fake_sessions() -> Any:
    session = SimpleNamespace(
        record=SimpleNamespace(
            id="session-a", project_id="default", pid=10, state="running", root_started_at=None
        )
    )
    return SimpleNamespace(
        sessions={"session-a": session},
        resolve=lambda identity: session if identity == "session-a" else None,
    )


class ProjectInspector:
    def __init__(self) -> None:
        self.snapshots = {
            "frontend": {
                "available": True,
                "session_id": "frontend",
                "project_id": "project-a",
                "processes": [{"pid": 11, "listeners": [listener_record("127.0.0.1", 37656)]}],
            },
            "backend": {
                "available": True,
                "session_id": "backend",
                "project_id": "project-a",
                "processes": [{"pid": 12, "listeners": [listener_record("127.0.0.1", 37655)]}],
            },
        }

    async def snapshot(self, session_id: str, *, force: bool = False) -> dict[str, Any]:
        return self.snapshots[session_id]

    async def snapshot_all(self) -> dict[str, Any]:
        return {
            "available": True,
            "sessions": [self.snapshots["frontend"], self.snapshots["backend"]],
        }


def project_sessions() -> Any:
    sessions = {
        identity: SimpleNamespace(
            record=SimpleNamespace(
                id=identity,
                project_id="project-a",
                pid=pid,
                state="running",
                root_started_at=None,
            )
        )
        for identity, pid in (("frontend", 11), ("backend", 12))
    }
    return SimpleNamespace(sessions=sessions, resolve=lambda identity: sessions[identity])


@pytest.mark.asyncio
async def test_preview_registration_requires_loopback_and_session_listener() -> None:
    registry = PreviewRegistry(cast(Any, FakeInspector()), cast(Any, fake_sessions()))
    item = await registry.register("session-a", "http://127.0.0.1:4321/")
    assert item.source == "detected"
    assert item.session_id == "session-a"

    with pytest.raises(ValueError, match="loopback"):
        await registry.register("session-a", "https://example.com/")
    with pytest.raises(ValueError, match="literal loopback"):
        await registry.register("session-a", "http://localhost:4321/")
    with pytest.raises(ValueError, match="credentials"):
        await registry.register("session-a", "http://user:pass@127.0.0.1:4321/")
    with pytest.raises(ValueError, match="query"):
        await registry.register("session-a", "http://127.0.0.1:4321/?target=other")
    with pytest.raises(ValueError, match="approval"):
        await registry.register("session-a", "http://127.0.0.1:9999/")
    approved = await registry.register("session-a", "http://127.0.0.1:9999/", approved=True)
    assert approved.source == "user-approved"


@pytest.mark.asyncio
async def test_preview_registration_reuses_the_same_session_listener() -> None:
    registry = PreviewRegistry(cast(Any, FakeInspector()), cast(Any, fake_sessions()))
    first = await registry.register("session-a", "http://127.0.0.1:4321/")
    second = await registry.register("session-a", "http://127.0.0.1:4321/other", approved=True)

    assert second is first
    assert len(registry.items) == 1


@pytest.mark.asyncio
async def test_preview_registration_attributes_a_printed_url_to_its_actual_project_owner() -> None:
    registry = PreviewRegistry(cast(Any, ProjectInspector()), cast(Any, project_sessions()))

    # The frontend terminal printed its backend address. Endpoint ownership comes
    # from the live listener, not the terminal where the link happened to appear.
    opened = await registry.register("frontend", "http://127.0.0.1:37655/", approved=True)
    reopened = await registry.register("backend", "http://127.0.0.1:37655/")

    assert opened is reopened
    assert opened.session_id == "backend"
    assert opened.source == "detected"
    assert len(registry.items) == 1


@pytest.mark.asyncio
async def test_live_project_services_get_routing_registrations_without_opening_tabs() -> None:
    registry = PreviewRegistry(cast(Any, ProjectInspector()), cast(Any, project_sessions()))

    await registry.ensure_detected("project-a")

    assert {(item.session_id, item.port) for item in registry.items.values()} == {
        ("frontend", 37656),
        ("backend", 37655),
    }
    assert set(registry.routes_for_project("project-a")) == {
        "http://127.0.0.1:37656",
        "http://127.0.0.1:37655",
    }


def test_wildcard_binds_are_reported_at_the_address_a_client_can_reach() -> None:
    # `python -m http.server` and most dev servers bind the wildcard, which does
    # serve loopback, so the listener must be usable rather than reported as 0.0.0.0.
    assert listener_record("0.0.0.0", 30674) == {
        "host": "127.0.0.1",
        "port": 30674,
        "loopback": True,
        "url": "http://127.0.0.1:30674/",
    }
    assert listener_record("::", 30674) == {
        "host": "::1",
        "port": 30674,
        "loopback": True,
        "url": "http://[::1]:30674/",
    }
    assert listener_record("::ffff:0.0.0.0", 30674)["host"] == "127.0.0.1"


def test_explicit_binds_are_reported_verbatim() -> None:
    assert listener_record("127.0.0.1", 5173)["loopback"] is True
    assert listener_record("::1", 5173)["url"] == "http://[::1]:5173/"
    # A bind to one specific LAN address genuinely is not reachable on loopback and
    # must not be rewritten into one.
    remote = listener_record("192.168.1.20", 5173)
    assert remote == {
        "host": "192.168.1.20",
        "port": 5173,
        "loopback": False,
        "url": "http://192.168.1.20:5173/",
    }


class WildcardInspector:
    """A session whose server bound the wildcard, as most dev servers do."""

    async def snapshot(self, session_id: str, *, force: bool = False) -> dict[str, Any]:
        return {
            "available": True,
            "session_id": session_id,
            "processes": [
                {
                    "pid": 71472,
                    "listeners": [
                        listener_record("0.0.0.0", 30674),
                        listener_record("::", 30674),
                    ],
                }
            ],
        }


@pytest.mark.asyncio
async def test_a_wildcard_bound_server_is_previewable_at_its_loopback_address() -> None:
    registry = PreviewRegistry(cast(Any, WildcardInspector()), cast(Any, fake_sessions()))

    detected = await registry.register("session-a", "http://127.0.0.1:30674/")
    assert detected.source == "detected"  # ownership auto-approval, not a user override
    ipv6 = await registry.register("session-a", "http://[::1]:30674/")
    assert ipv6.source == "detected"

    # Normalising the listener must not relax the destination rule: the wildcard
    # address itself is still never a legal thing to dial.
    with pytest.raises(ValueError, match="literal loopback"):
        await registry.register("session-a", "http://0.0.0.0:30674/")


class StoppableInspector:
    """An inspector whose listener set can be turned off mid-test."""

    def __init__(self) -> None:
        self.listening = True

    def live_listeners(self) -> set[tuple[str, int, str]]:
        return {("session-a", 4321, "127.0.0.1")} if self.listening else set()

    async def snapshot(self, session_id: str, *, force: bool = False) -> dict[str, Any]:
        listeners = [listener_record("127.0.0.1", 4321)] if self.listening else []
        return {
            "available": True,
            "session_id": session_id,
            "processes": [{"pid": 44, "listeners": listeners}],
        }


@pytest.mark.asyncio
async def test_a_stopped_server_loses_its_preview_after_the_restart_grace() -> None:
    inspector = StoppableInspector()
    registry = PreviewRegistry(cast(Any, inspector), cast(Any, fake_sessions()))
    item = await registry.register("session-a", "http://127.0.0.1:4321/")

    # Still listening: never reaped, however long it runs.
    assert registry.prune(now=1_000_000) == []
    assert item.id in registry.items

    inspector.listening = False
    # A restarting dev server briefly stops listening and must keep its tab.
    assert registry.prune(now=1_000_010) == []
    assert item.id in registry.items

    # Stopped for good.
    removed = registry.prune(now=1_000_035)
    assert [entry.id for entry in removed] == [item.id]
    assert item.id not in registry.items
    assert (await registry.list("session-a"))["items"] == []


@pytest.mark.asyncio
async def test_a_restarted_server_keeps_its_preview() -> None:
    inspector = StoppableInspector()
    registry = PreviewRegistry(cast(Any, inspector), cast(Any, fake_sessions()))
    item = await registry.register("session-a", "http://127.0.0.1:4321/")

    inspector.listening = False
    assert registry.prune(now=1_000_005) == []
    inspector.listening = True
    # Coming back refreshes liveness, so the old absence cannot accumulate.
    assert registry.prune(now=1_000_010) == []
    assert registry.prune(now=1_000_100) == []
    assert item.id in registry.items


@pytest.mark.asyncio
async def test_a_user_approved_preview_is_never_reaped() -> None:
    inspector = StoppableInspector()
    registry = PreviewRegistry(cast(Any, inspector), cast(Any, fake_sessions()))
    # Not attributable to the session, so its absence proves nothing about liveness.
    item = await registry.register("session-a", "http://127.0.0.1:9999/", approved=True)
    assert item.source == "user-approved"

    assert registry.prune(now=1_000_600) == []
    assert item.id in registry.items


def test_process_action_cannot_target_another_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from swe_mux import processes

    monkeypatch.setattr(processes, "psutil", SimpleNamespace())
    inspector = ProcessInspector(cast(Any, fake_sessions()), EventBus())
    inspector.owned[(55, 1.0)] = OwnedProcess(
        55, 10, "session-b", "server", "server", 1.0, None, 0, 1, [], []
    )
    with pytest.raises(ValueError, match="not owned"):
        inspector._owned_live("session-a", 55)


def test_preview_leaf_attaches_without_losing_terminal() -> None:
    layout = attach_leaf(None, "terminal", "terminal-a")
    layout = attach_leaf(
        layout,
        "preview",
        "preview-a",
        target_id="terminal-a",
        direction="horizontal",
    )
    assert layout_terminal_ids(layout) == ["terminal-a"]
    assert layout["root"]["second"]["children"] == [  # type: ignore[index]
        {"type": "leaf", "kind": "preview", "id": "preview-a"}
    ]


def test_reopening_an_existing_preview_activates_its_stack_tab() -> None:
    layout = attach_leaf(None, "terminal", "terminal-a")
    layout = stack_leaf(layout, "preview", "preview-a", target_id="terminal-a")
    assert layout is not None
    layout = stack_leaf(layout, "terminal", "terminal-a", target_id="terminal-a")
    assert layout is not None
    assert layout["root"]["active_child_id"] == "terminal-a"  # type: ignore[index]

    reopened = stack_leaf(layout, "preview", "preview-a", target_id="terminal-a")

    assert reopened is not None
    assert reopened["root"]["active_child_id"] == "preview-a"  # type: ignore[index]


def test_process_reconciliation_records_descendant_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from swe_mux import processes

    monkeypatch.setattr(processes, "psutil", SimpleNamespace(net_connections=lambda **_: []))
    inspector = ProcessInspector(cast(Any, fake_sessions()), EventBus())
    child = OwnedProcess(55, 10, "session-a", "server", "server", 1.0, None, 0, 1, [], [])
    samples = [[child], []]

    def collect(_session: Any, _conn_map: Any) -> list[OwnedProcess]:
        return samples.pop(0)

    monkeypatch.setattr(inspector, "_collect_session", collect)
    inspector._collect_all()
    inspector._collect_all()

    assert inspector.owned[(55, 1.0)].exited_at is not None


@pytest.mark.asyncio
async def test_unified_process_snapshot_groups_sessions_and_aggregates_resources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from swe_mux import processes

    monkeypatch.setattr(processes, "psutil", SimpleNamespace())
    records = {
        "session-a": SimpleNamespace(
            id="session-a",
            project_id="project-a",
            trusted_scope_id="scope-a",
            agent_run_id=None,
            run_repo_group_id=None,
            spawn_repo_group_id="repo-a",
        ),
        "session-b": SimpleNamespace(
            id="session-b",
            project_id="project-b",
            trusted_scope_id="scope-b",
            agent_run_id="run-b",
            run_repo_group_id="repo-b",
            spawn_repo_group_id=None,
        ),
    }
    sessions = SimpleNamespace(
        sessions={identity: SimpleNamespace(record=record) for identity, record in records.items()}
    )
    inspector = ProcessInspector(cast(Any, sessions), EventBus())
    first = OwnedProcess(
        55,
        10,
        "session-a",
        "server",
        "server",
        1.0,
        None,
        12.5,
        128 * 1024 * 1024,
        [{"host": "127.0.0.1", "port": 3000}],
        [],
        [{"remote_host": "127.0.0.1", "remote_port": 9000}],
    )
    second = OwnedProcess(
        77, 20, "session-b", "worker", "worker", 2.0, None, 7.5, 64 * 1024 * 1024, [], []
    )
    inspector.owned = {(55, 1.0): first, (77, 2.0): second}
    monkeypatch.setattr(inspector, "_collect_all", lambda: [first, second])

    result = await inspector.snapshot_all()

    assert [group["session_id"] for group in result["sessions"]] == [
        "session-a",
        "session-b",
    ]
    assert result["sessions"][0]["project_id"] == "project-a"
    assert result["totals"] == {
        "processes": 2,
        "cpu_pct": 20.0,
        "memory_bytes": 192 * 1024 * 1024,
        "listeners": 1,
        "connections": 1,
    }
    assert result["daemon"]["pid"] > 0


def _fleet_inspector(monkeypatch: pytest.MonkeyPatch) -> Any:
    from swe_mux import processes

    monkeypatch.setattr(processes, "psutil", SimpleNamespace())
    record = SimpleNamespace(
        id="session-a",
        project_id="project-a",
        trusted_scope_id="scope-a",
        agent_run_id=None,
        run_repo_group_id=None,
        spawn_repo_group_id="repo-a",
    )
    sessions = SimpleNamespace(sessions={"session-a": SimpleNamespace(record=record)})
    inspector = ProcessInspector(cast(Any, sessions), EventBus())
    live = OwnedProcess(55, 10, "session-a", "server", "server", 1.0, None, 5.0, 1024, [], [])
    ended = OwnedProcess(56, 10, "session-a", "old", "old", 2.0, 100.0, 0, 0, [], [])
    ended.evidence_state = "exited"
    # A survivor of a session that no longer exists: still running, so still the
    # operator's problem even though its session is gone.
    orphan = OwnedProcess(88, 1, "session-gone", "stray", "stray", 3.0, None, 1.0, 512, [], [])
    orphan.evidence_state = "suspected_orphan"
    dead_only = OwnedProcess(99, 1, "session-dead", "past", "past", 4.0, 90.0, 0, 0, [], [])
    dead_only.evidence_state = "exited"
    inspector.owned = {
        (55, 1.0): live,
        (56, 2.0): ended,
        (88, 3.0): orphan,
        (99, 4.0): dead_only,
    }
    monkeypatch.setattr(inspector, "_collect_all", lambda: [live, orphan])
    return inspector


@pytest.mark.asyncio
async def test_fleet_hides_ended_processes_but_never_hides_live_survivors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inspector = _fleet_inspector(monkeypatch)

    result = await inspector.snapshot_all()

    groups = {group["session_id"]: group for group in result["sessions"]}
    # The live session keeps only its running process.
    assert [item["pid"] for item in groups["session-a"]["processes"]] == [55]
    # A dead session that still has a running survivor stays visible.
    assert [item["pid"] for item in groups["session-gone"]["processes"]] == [88]
    # A dead session whose processes all ended disappears completely.
    assert "session-dead" not in groups
    assert result["totals"]["processes"] == 2


@pytest.mark.asyncio
async def test_fleet_returns_ended_processes_only_when_explicitly_requested(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inspector = _fleet_inspector(monkeypatch)

    result = await inspector.snapshot_all(include_ended=True)

    groups = {group["session_id"]: group for group in result["sessions"]}
    assert [item["pid"] for item in groups["session-a"]["processes"]] == [55, 56]
    assert "session-dead" in groups
    # Ended records never inflate the resource totals, requested or not.
    assert result["totals"]["processes"] == 2


@pytest.mark.asyncio
async def test_session_snapshot_hides_ended_processes_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inspector = _fleet_inspector(monkeypatch)

    default = await inspector.snapshot("session-a")
    requested = await inspector.snapshot("session-a", include_ended=True)

    assert [item["pid"] for item in default["processes"]] == [55]
    assert [item["pid"] for item in requested["processes"]] == [55, 56]


@pytest.mark.asyncio
async def test_restore_skips_already_exited_durable_evidence() -> None:
    """A previous run's dead processes must not repopulate the live fleet."""

    class Telemetry:
        def __init__(self) -> None:
            self.persisted: list[dict[str, Any]] = []

        async def record_process_observations(self, items: list[dict[str, Any]]) -> None:
            self.persisted = items

        async def process_candidates(self) -> list[dict[str, Any]]:
            return [
                {
                    "pid": 10,
                    "identity_id": "still-running",
                    "session_id": "session-a",
                    "creation_time": 1.0,
                    "exited_at": None,
                },
                {
                    "pid": 11,
                    "identity_id": "long-gone",
                    "session_id": "session-a",
                    "creation_time": 2.0,
                    "exited_at": 500.0,
                },
            ]

    inspector = ProcessInspector(
        cast(Any, fake_sessions()), EventBus(), telemetry=cast(Any, Telemetry())
    )
    await inspector.restore()

    assert [item.identity_id for item in inspector.owned.values()] == ["still-running"]


def test_daemon_resource_sample_excludes_session_attributed_descendants(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from swe_mux import processes

    class OneShot:
        def __enter__(self) -> None:
            return None

        def __exit__(self, *_args: object) -> None:
            return None

    class FakeProcess:
        def __init__(
            self,
            pid: int,
            created: float,
            memory: int,
            children: list[Any] | None = None,
            *,
            parent_pid: int = 1,
            name: str = "helper.exe",
        ):
            self.pid = pid
            self.created = created
            self.memory = memory
            self._children = children or []
            self.parent_pid = parent_pid
            self.process_name = name

        def children(self, recursive: bool = False) -> list[Any]:
            assert recursive is True
            return self._children

        def oneshot(self) -> OneShot:
            return OneShot()

        def create_time(self) -> float:
            return self.created

        def cpu_times(self) -> Any:
            return SimpleNamespace(user=1.0, system=0.5)

        def memory_info(self) -> Any:
            return SimpleNamespace(rss=self.memory)

        def ppid(self) -> int:
            return self.parent_pid

        def name(self) -> str:
            return self.process_name

        def cmdline(self) -> list[str]:
            return [self.process_name, "--smoke"]

    daemon_pid = processes.os.getpid()
    attributed = FakeProcess(20, 2.0, 900)
    helper = FakeProcess(30, 3.0, 300, parent_pid=daemon_pid)
    root = FakeProcess(daemon_pid, 1.0, 100, [attributed, helper], name="swe-mux.exe")
    monkeypatch.setattr(
        processes,
        "psutil",
        SimpleNamespace(
            Process=lambda pid: root,
            NoSuchProcess=RuntimeError,
            AccessDenied=PermissionError,
        ),
    )
    inspector = ProcessInspector(cast(Any, SimpleNamespace(sessions={})), EventBus())
    seen: set[tuple[int, float]] = set()

    result = inspector._collect_daemon_resources({20}, seen)

    assert result == {
        "pid": daemon_pid,
        "processes": 2,
        "cpu_pct": 0.0,
        "memory_bytes": 400,
        "listeners": 0,
        "connections": 0,
        "members": [
            {
                "pid": daemon_pid,
                "parent_pid": 1,
                "executable": "swe-mux.exe",
                "command": "swe-mux.exe --smoke",
                "started_at": 1.0,
                "cpu_pct": 0.0,
                "memory_bytes": 100,
                "listeners": [],
                "connections": [],
                "conditions": [],
            },
            {
                "pid": 30,
                "parent_pid": daemon_pid,
                "executable": "helper.exe",
                "command": "helper.exe --smoke",
                "started_at": 3.0,
                "cpu_pct": 0.0,
                "memory_bytes": 300,
                "listeners": [],
                "connections": [],
                "conditions": [],
            },
        ],
    }
    assert (20, 2.0) not in seen
    assert (daemon_pid, 1.0) in seen
    assert (30, 3.0) in seen


def test_orphan_grace_elapses_once_the_session_record_is_gone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A survivor of a vanished session must eventually escalate to suspected_orphan.

    The grace used to be measured against ``last_seen``, which every pass refreshed to
    ``now``, so the window slid forever and a real orphan stayed "grace pending"
    indefinitely -- observed live on a process that had outlived its session by days.
    """
    from swe_mux import processes

    class Survivor:
        pid = 77

        def create_time(self) -> float:
            return 10.0

        def oneshot(self) -> Survivor:
            return self

        def __enter__(self) -> Survivor:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def ppid(self) -> int:
            return 1

        def name(self) -> str:
            return "server.exe"

        def cmdline(self) -> list[str]:
            return ["server.exe"]

        def memory_info(self) -> Any:
            return SimpleNamespace(rss=2048)

    monkeypatch.setattr(
        processes,
        "psutil",
        SimpleNamespace(
            Process=lambda _pid: Survivor(),
            NoSuchProcess=RuntimeError,
            AccessDenied=PermissionError,
        ),
    )
    inspector = ProcessInspector(
        cast(Any, SimpleNamespace(sessions={})), EventBus(), orphan_grace_seconds=15
    )
    item = OwnedProcess(
        77, 1, "gone-session", "server.exe", "", 10.0, None, 0, 0, [], [], first_seen=1_000.0
    )
    inspector.owned[(77, 10.0)] = item

    # First pass after the session disappears: inside the grace window.
    inspector._revalidate_unseen(set(), {}, 1_000.0, False)
    assert item.evidence_state == "escaped"
    assert item.root_ended_at == 1_000.0

    # Later passes keep refreshing last_seen; the stamp must not move with it.
    inspector._revalidate_unseen(set(), {}, 1_010.0, False)
    assert item.evidence_state == "escaped"
    assert item.root_ended_at == 1_000.0

    inspector._revalidate_unseen(set(), {}, 1_020.0, False)
    assert item.evidence_state == "suspected_orphan"
    assert "suspected_orphan" in item.conditions


def test_a_session_that_comes_back_clears_its_orphan_stamp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from swe_mux import processes

    class Live:
        pid = 88

        def create_time(self) -> float:
            return 5.0

        def oneshot(self) -> Live:
            return self

        def __enter__(self) -> Live:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def ppid(self) -> int:
            return 1

        def name(self) -> str:
            return "worker.exe"

        def cmdline(self) -> list[str]:
            return ["worker.exe"]

        def memory_info(self) -> Any:
            return SimpleNamespace(rss=512)

    monkeypatch.setattr(
        processes,
        "psutil",
        SimpleNamespace(
            Process=lambda _pid: Live(),
            NoSuchProcess=RuntimeError,
            AccessDenied=PermissionError,
        ),
    )
    record = SimpleNamespace(state="running", last_activity_ts=900.0)
    sessions = SimpleNamespace(sessions={})
    inspector = ProcessInspector(cast(Any, sessions), EventBus(), orphan_grace_seconds=15)
    item = OwnedProcess(
        88, 1, "session-a", "worker.exe", "", 5.0, None, 0, 0, [], [], first_seen=900.0
    )
    inspector.owned[(88, 5.0)] = item

    inspector._revalidate_unseen(set(), {}, 1_000.0, False)
    assert item.root_ended_at == 900.0

    sessions.sessions["session-a"] = SimpleNamespace(record=record)
    inspector._revalidate_unseen(set(), {}, 1_010.0, False)

    assert item.root_ended_at is None
    assert item.evidence_state == "escaped"


def test_sampling_reuses_process_handles_across_passes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Handles and identity attributes are read once, not once per reconcile tick.

    Constructing a psutil.Process and reading cmdline() are the two dominant costs of a
    pass and neither changes for a live process, so repeating them every 5s only
    starved the event loop (which surfaced as laggy terminal input).
    """
    from swe_mux import processes

    constructed: list[int] = []
    cmdline_reads: list[int] = []

    class Fake:
        def __init__(self, pid: int) -> None:
            self.pid = pid
            constructed.append(pid)

        def create_time(self) -> float:
            return 7.0

        def oneshot(self) -> Fake:
            return self

        def __enter__(self) -> Fake:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def ppid(self) -> int:
            return 1 if self.pid == 100 else 100

        def name(self) -> str:
            return f"proc-{self.pid}"

        def cmdline(self) -> list[str]:
            cmdline_reads.append(self.pid)
            return [f"proc-{self.pid}"]

        def cpu_times(self) -> Any:
            return SimpleNamespace(user=1.0, system=0.0)

        def memory_info(self) -> Any:
            return SimpleNamespace(rss=1024)

    monkeypatch.setattr(
        processes,
        "psutil",
        SimpleNamespace(
            Process=Fake,
            NoSuchProcess=RuntimeError,
            AccessDenied=PermissionError,
            _ppid_map=lambda: {100: 1, 101: 100},
        ),
    )
    record = SimpleNamespace(
        id="session-a",
        pid=100,
        project_id="project-a",
        agent_run_id=None,
        process_job_assignment="job",
        last_activity_ts=0.0,
        state="running",
        root_started_at=7.0,
    )
    sessions = SimpleNamespace(sessions={"session-a": SimpleNamespace(record=record)})
    inspector = ProcessInspector(cast(Any, sessions), EventBus())

    inspector._refresh_tree()
    first = inspector._collect_session(cast(Any, sessions.sessions["session-a"]), {})
    assert {item.pid for item in first} == {100, 101}
    assert sorted(constructed) == [100, 101]
    assert sorted(cmdline_reads) == [100, 101]

    inspector._refresh_tree()
    second = inspector._collect_session(cast(Any, sessions.sessions["session-a"]), {})

    assert {item.pid for item in second} == {100, 101}
    # No handle rebuilt and no command line re-read on the second pass.
    assert sorted(constructed) == [100, 101]
    assert sorted(cmdline_reads) == [100, 101]
    assert second[0].command_hash == first[0].command_hash


def test_a_recycled_parent_link_cannot_splice_another_tree_into_a_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A descendant older than the root is a stale ppid, not a child.

    Windows leaves a dead parent's pid in the child's ppid field and recycles pids, so
    the raw parent map contains links that were never real. Trusting one made the PTY
    supervisor look like a descendant of a single session; because the supervisor
    parents every session, that session absorbed the entire fleet (34 processes, three
    claude.exe, another session's listeners) while its siblings reported zero.
    """
    from swe_mux import processes

    # 500 = session root. 501 = its real child. 900 = the supervisor, created long
    # before the root, whose stale ppid now points at the recycled pid 501.
    # 901/902 = other sessions' agents, real children of the supervisor.
    created = {500: 1_000.0, 501: 1_001.0, 900: 10.0, 901: 20.0, 902: 30.0}
    names = {500: "claude.exe", 501: "bash.exe", 900: "supervisor.exe", 901: "claude.exe",
             902: "claude.exe"}

    class Fake:
        def __init__(self, pid: int) -> None:
            self.pid = pid

        def create_time(self) -> float:
            return created[self.pid]

        def oneshot(self) -> Fake:
            return self

        def __enter__(self) -> Fake:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def ppid(self) -> int:
            return 1

        def name(self) -> str:
            return names[self.pid]

        def cmdline(self) -> list[str]:
            return [names[self.pid]]

        def cpu_times(self) -> Any:
            return SimpleNamespace(user=0.0, system=0.0)

        def memory_info(self) -> Any:
            return SimpleNamespace(rss=1024)

    monkeypatch.setattr(
        processes,
        "psutil",
        SimpleNamespace(
            Process=Fake,
            NoSuchProcess=RuntimeError,
            AccessDenied=PermissionError,
            _ppid_map=lambda: {500: 1, 501: 500, 900: 501, 901: 900, 902: 900},
        ),
    )
    inspector = ProcessInspector(cast(Any, SimpleNamespace(sessions={})), EventBus())
    inspector._refresh_tree()

    handles = inspector._tree_handles(500, 256)

    pids = {handle.pid for handle in handles}
    assert pids == {500, 501}, "the supervisor and every session under it must be excluded"
    # Excluding the stale link must also stop the walk there, or the other sessions'
    # agents would still be pulled in through it.
    assert 900 not in pids and 901 not in pids and 902 not in pids


def test_a_foreign_child_that_postdates_the_root_but_predates_its_recycled_parent_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every parent edge must be causal, not merely newer than the session root."""
    from swe_mux import processes

    # 500 is the old session root. 501 is a new, real child that reused a dead
    # pid. The foreign long-lived process 900 still names 501 as its parent, but
    # it predates the current 501 and therefore cannot be its child. A root-only
    # check accepts all three because both descendants postdate 500.
    created = {500: 1_000.0, 501: 3_000.0, 900: 2_000.0, 901: 2_001.0}

    class Fake:
        def __init__(self, pid: int) -> None:
            self.pid = pid

        def create_time(self) -> float:
            return created[self.pid]

        def ppid(self) -> int:
            return 1

    monkeypatch.setattr(
        processes,
        "psutil",
        SimpleNamespace(
            Process=Fake,
            NoSuchProcess=RuntimeError,
            AccessDenied=PermissionError,
            _ppid_map=lambda: {500: 1, 501: 500, 900: 501, 901: 900},
        ),
    )
    inspector = ProcessInspector(cast(Any, SimpleNamespace(sessions={})), EventBus())
    inspector._refresh_tree()

    assert {handle.pid for handle in inspector._tree_handles(500, 256)} == {500, 501}
    assert inspector._ownership_diagnostics[-1]["kind"] == "causally_impossible_parent_edge"


def test_uncorroborated_legacy_ownership_is_retired_without_stopping_the_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from swe_mux import processes

    class LiveProcess:
        def __init__(self, pid: int) -> None:
            self.pid = pid

        def create_time(self) -> float:
            return 10.0

    monkeypatch.setattr(
        processes,
        "psutil",
        SimpleNamespace(
            Process=LiveProcess,
            NoSuchProcess=RuntimeError,
            AccessDenied=PermissionError,
        ),
    )
    inspector = ProcessInspector(cast(Any, SimpleNamespace(sessions={})), EventBus())
    item = OwnedProcess(
        77,
        1,
        "session-a",
        "foreign-server.exe",
        "",
        10.0,
        None,
        0,
        0,
        [listener_record("127.0.0.1", 8384)],
        [],
        attribution_version=1,
        attribution_source="legacy",
    )
    inspector.owned[(77, 10.0)] = item

    inspector._revalidate_unseen(set(), {}, 100.0, False)

    assert item.evidence_state == "stale"
    assert item.evidence_reason == "legacy_attribution_uncorroborated"
    assert item.exit_evidence == "ownership_rejected"
    assert item.exited_at == 100.0
    assert item.listeners == []


def test_daemon_sampling_cannot_preserve_a_false_session_claim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from swe_mux import processes

    class DaemonProcess:
        pid = 900

        def create_time(self) -> float:
            return 50.0

        def ppid(self) -> int:
            return 1

        def oneshot(self) -> DaemonProcess:
            return self

        def __enter__(self) -> DaemonProcess:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def name(self) -> str:
            return "swe-mux.exe"

        def cmdline(self) -> list[str]:
            return ["swe-mux.exe", "--daemon-child"]

        def cpu_times(self) -> Any:
            return SimpleNamespace(user=0.0, system=0.0)

        def memory_info(self) -> Any:
            return SimpleNamespace(rss=1024)

    monkeypatch.setattr(processes.os, "getpid", lambda: 900)
    monkeypatch.setattr(
        processes,
        "psutil",
        SimpleNamespace(
            Process=lambda _pid: DaemonProcess(),
            NoSuchProcess=RuntimeError,
            AccessDenied=PermissionError,
            _ppid_map=lambda: {900: 1},
            net_connections=lambda **_: [],
        ),
    )
    inspector = ProcessInspector(cast(Any, SimpleNamespace(sessions={})), EventBus())
    false_claim = OwnedProcess(
        900,
        1,
        "session-a",
        "swe-mux.exe",
        "",
        50.0,
        None,
        0,
        1024,
        [listener_record("127.0.0.1", 8765)],
        [],
        attribution_source="parent_walk",
    )
    inspector.owned[(900, 50.0)] = false_claim

    inspector._collect_all()

    assert false_claim.evidence_state == "stale"
    assert false_claim.evidence_reason == "reserved_infrastructure_fingerprint"
    assert false_claim.exit_evidence == "ownership_rejected"
    assert false_claim.listeners == []


def test_equal_strength_claims_from_two_sessions_are_quarantined(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from swe_mux import processes

    monkeypatch.setattr(processes, "psutil", SimpleNamespace(net_connections=lambda **_: []))
    sessions = SimpleNamespace(
        sessions={
            "session-a": SimpleNamespace(record=SimpleNamespace(id="session-a")),
            "session-b": SimpleNamespace(record=SimpleNamespace(id="session-b")),
        }
    )
    inspector = ProcessInspector(cast(Any, sessions), EventBus())

    def collect(session: Any, _connections: Any) -> list[OwnedProcess]:
        return [
            OwnedProcess(
                77,
                1,
                session.record.id,
                "server.exe",
                "",
                10.0,
                None,
                0,
                0,
                [],
                [],
                attribution_source="parent_walk",
            )
        ]

    monkeypatch.setattr(inspector, "_collect_session", collect)

    assert inspector._collect_all() == []
    assert inspector.owned == {}
    assert inspector._ownership_diagnostics[-1]["kind"] == "ambiguous_session_ownership"


def test_an_unreadable_identity_is_retried_rather_than_cached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A transient AccessDenied must not pin a placeholder for the handle's lifetime."""
    from swe_mux import processes

    class Flaky:
        pid = 61
        attempts = 0

        def name(self) -> str:
            return "worker.exe"

        def cmdline(self) -> list[str]:
            Flaky.attempts += 1
            if Flaky.attempts == 1:
                raise PermissionError("denied")
            return ["worker.exe", "--serve"]

    monkeypatch.setattr(
        processes,
        "psutil",
        SimpleNamespace(
            Process=lambda _pid: Flaky(), NoSuchProcess=RuntimeError, AccessDenied=PermissionError
        ),
    )
    inspector = ProcessInspector(cast(Any, SimpleNamespace(sessions={})), EventBus())
    handle = Flaky()

    assert inspector._identity(handle, 61) == ("worker.exe", "", processes.command_hash(""))
    assert 61 not in inspector._static

    name, command, _ = inspector._identity(handle, 61)
    assert (name, command) == ("worker.exe", "worker.exe --serve")
    assert 61 in inspector._static


def test_a_recycled_pid_rebuilds_its_cached_handle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A changed parent pid is proof of recycling, so the memoized identity is dropped."""
    from swe_mux import processes

    constructed: list[int] = []

    class Fake:
        def __init__(self, pid: int) -> None:
            self.pid = pid
            constructed.append(pid)

        def create_time(self) -> float:
            return 7.0

        def oneshot(self) -> Fake:
            return self

        def __enter__(self) -> Fake:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def ppid(self) -> int:
            return 1

        def name(self) -> str:
            return "proc"

        def cmdline(self) -> list[str]:
            return ["proc"]

    monkeypatch.setattr(
        processes,
        "psutil",
        SimpleNamespace(
            Process=Fake, NoSuchProcess=RuntimeError, AccessDenied=PermissionError
        ),
    )
    inspector = ProcessInspector(cast(Any, SimpleNamespace(sessions={})), EventBus())

    inspector._parents = {55: 10}
    inspector._handle(55)
    inspector._handle(55)
    assert constructed == [55]

    inspector._parents = {55: 11}
    inspector._handle(55)

    assert constructed == [55, 55]


def _recycling_psutil(create_time: float) -> Any:
    class Fake:
        def __init__(self, pid: int) -> None:
            self.pid = pid

        def create_time(self) -> float:
            return create_time

        def oneshot(self) -> Any:
            return self

        def __enter__(self) -> Any:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def ppid(self) -> int:
            return 1

        def name(self) -> str:
            return f"proc-{self.pid}"

        def cmdline(self) -> list[str]:
            return [f"proc-{self.pid}"]

        def cpu_times(self) -> Any:
            return SimpleNamespace(user=1.0, system=0.0)

        def memory_info(self) -> Any:
            return SimpleNamespace(rss=1024)

    return SimpleNamespace(
        Process=Fake,
        NoSuchProcess=RuntimeError,
        AccessDenied=PermissionError,
        _ppid_map=lambda: {100: 1},
    )


def _inspector_for(record: Any, monkeypatch: pytest.MonkeyPatch, create_time: float) -> Any:
    from swe_mux import processes

    monkeypatch.setattr(processes, "psutil", _recycling_psutil(create_time))
    sessions = SimpleNamespace(sessions={"session-a": SimpleNamespace(record=record)})
    inspector = ProcessInspector(cast(Any, sessions), EventBus())
    inspector._refresh_tree()
    return inspector, sessions


def _process_record(**overrides: Any) -> Any:
    base = dict(
        id="session-a",
        pid=100,
        project_id="project-a",
        agent_run_id=None,
        process_job_assignment="job",
        last_activity_ts=0.0,
        state="running",
        root_started_at=7.0,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_exited_session_root_pid_is_never_walked(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ended sessions stay listed with record.pid intact.

    Windows recycles pids aggressively, so walking that pid attributed an
    unrelated process tree to the dead session as high-confidence evidence — with
    interrupt/terminate offered on it.
    """
    record = _process_record(state="exited")
    inspector, sessions = _inspector_for(record, monkeypatch, 7.0)
    assert inspector._collect_session(cast(Any, sessions.sessions["session-a"]), {}) == []
    record.state = "crashed"
    assert inspector._collect_session(cast(Any, sessions.sessions["session-a"]), {}) == []


def test_recycled_root_pid_fails_the_creation_time_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same pid, different process: the tree belongs to someone else now."""
    record = _process_record(root_started_at=7.0)
    inspector, sessions = _inspector_for(record, monkeypatch, 9999.0)
    assert inspector._collect_session(cast(Any, sessions.sessions["session-a"]), {}) == []


def test_live_root_with_matching_creation_time_is_collected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = _process_record(root_started_at=7.0)
    inspector, sessions = _inspector_for(record, monkeypatch, 7.0)
    collected = inspector._collect_session(cast(Any, sessions.sessions["session-a"]), {})
    assert [item.pid for item in collected] == [100]


def test_session_without_a_recorded_start_falls_back_to_pid_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sessions adopted from a supervisor predating the field keep working."""
    record = _process_record(root_started_at=None)
    inspector, sessions = _inspector_for(record, monkeypatch, 1234.0)
    collected = inspector._collect_session(cast(Any, sessions.sessions["session-a"]), {})
    assert [item.pid for item in collected] == [100]


def test_a_detached_descendant_is_attributed_through_job_membership(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A server whose launching shell exited is still this session's.

    Codex's shell tool runs one-shot, so anything long-lived has to be started
    detached (`Start-Process`). Windows neither re-parents the orphan nor clears
    the dead pid from its ppid field, so the downward walk can never reach it and
    the session reported a live dev server as zero listeners — no sidebar row, no
    Preview, while Claude (whose Bash tool holds the parent open) worked fine.
    """
    record = _process_record(root_started_at=7.0)
    inspector, sessions = _inspector_for(record, monkeypatch, 7.0)
    # 777 is the detached server: absent from _ppid_map's children of 100.
    inspector._job_pids = {"session-a": [100, 777]}

    collected = inspector._collect_session(cast(Any, sessions.sessions["session-a"]), {})

    assert sorted(item.pid for item in collected) == [100, 777]
    detached = next(item for item in collected if item.pid == 777)
    assert detached.evidence_state == "active"
    assert detached.evidence_reason == "live_job_object_member"
    assert detached.confidence == "high"
    # No lineage inside the session: its parent is dead. It renders as a root.
    assert detached.parent_lineage == []


def test_job_membership_is_ignored_when_the_root_pid_was_recycled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Job evidence never outranks the root fingerprint check.

    The job handle is keyed to the session, so a root that turns out to be a
    recycled pid means the whole attribution is someone else's — including
    anything the job would have named.
    """
    record = _process_record(root_started_at=7.0)
    inspector, sessions = _inspector_for(record, monkeypatch, 9999.0)
    inspector._job_pids = {"session-a": [100, 777]}

    assert inspector._collect_session(cast(Any, sessions.sessions["session-a"]), {}) == []


def test_job_membership_never_duplicates_a_walked_descendant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = _process_record(root_started_at=7.0)
    inspector, sessions = _inspector_for(record, monkeypatch, 7.0)
    inspector._job_pids = {"session-a": [100, 100, 100]}

    collected = inspector._collect_session(cast(Any, sessions.sessions["session-a"]), {})

    assert [item.pid for item in collected] == [100]
    assert collected[0].evidence_reason == "live_descendant_fingerprint_match"


@pytest.mark.asyncio
async def test_job_pid_refresh_keeps_the_previous_map_when_the_source_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A dropped supervisor response must not blank attribution for a tick.

    Losing the map would drop every detached listener out of the fleet and reap
    its Preview, then bring it back on the next pass — a tab that flickers on an
    unrelated RPC hiccup.
    """
    from swe_mux import processes

    monkeypatch.setattr(processes, "psutil", SimpleNamespace())

    async def failing() -> dict[str, list[int]]:
        raise RuntimeError("supervisor went away")

    sessions = SimpleNamespace(sessions={}, job_process_ids=failing)
    inspector = ProcessInspector(cast(Any, sessions), EventBus())
    inspector._job_pids = {"session-a": [100, 777]}

    await inspector._refresh_job_pids()

    assert inspector._job_pids == {"session-a": [100, 777]}


@pytest.mark.asyncio
async def test_job_pid_refresh_is_skipped_when_the_manager_cannot_supply_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-Windows and older supervisors simply contribute no job evidence."""
    from swe_mux import processes

    monkeypatch.setattr(processes, "psutil", SimpleNamespace())
    inspector = ProcessInspector(cast(Any, SimpleNamespace(sessions={})), EventBus())

    await inspector._refresh_job_pids()

    assert inspector._job_pids == {}


@pytest.mark.asyncio
async def test_preview_capture_reports_unavailable_and_resolves_the_shot_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The capture endpoint shipped with its control-plane checkbox ticked and no
    # test: a regression in the `.swe-mux/preview-shots` fallback or in the
    # unavailable-Playwright shape would not have failed CI. Both halves are
    # testable without Chromium.
    from types import SimpleNamespace

    from swe_mux import server
    from swe_mux.config import Config
    from swe_mux.server import capture_preview

    project_root = tmp_path / "repo"
    project_root.mkdir()
    item = SimpleNamespace(id="pv1", host="127.0.0.1", port=5173, session_id="s1")
    record = SimpleNamespace(project_root=str(project_root), spawn_project_root=None,
                             project_id="p1")
    app = {
        "previews": SimpleNamespace(items={"pv1": item}),
        "config": Config(data_dir=tmp_path / "data"),
        "sessions": SimpleNamespace(sessions={"s1": SimpleNamespace(record=record)}),
        "projects": SimpleNamespace(projects={}),
    }

    async def body() -> dict[str, object]:
        return {}

    request = SimpleNamespace(
        match_info={"preview_id": "pv1"}, app=app, can_read_body=True, json=body
    )

    # Backend missing: a typed unavailable state with an install hint, never a 500.
    monkeypatch.setattr(server, "capture_available", lambda: False)
    payload = json.loads((await capture_preview(request)).body)  # type: ignore[arg-type]
    assert payload["available"] is False
    assert payload["install"]

    # Backend present: the shot lands in the owning Project, not the data dir.
    captured: dict[str, Path] = {}

    async def fake_capture(url: str, out_path: Path, **_kwargs: object) -> None:
        captured["path"] = out_path
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(b"png")

    monkeypatch.setattr(server, "capture_available", lambda: True)
    monkeypatch.setattr(server, "capture_loopback", fake_capture)
    payload = json.loads((await capture_preview(request)).body)  # type: ignore[arg-type]
    assert payload["available"] is True
    assert captured["path"].parent == project_root / ".swe-mux" / "preview-shots"
    assert Path(payload["path"]).is_file()


@pytest.mark.asyncio
async def test_preview_shots_expire_but_recent_ones_survive(tmp_path: Path) -> None:
    # They live inside the user's repository and a UI-iteration session takes
    # dozens a day, so "no sweep" meant unbounded growth in the checkout.
    from swe_mux.server import PREVIEW_SHOT_TTL_SECONDS, cleanup_expired_preview_shots

    shots = tmp_path / ".swe-mux" / "preview-shots"
    shots.mkdir(parents=True)
    old, fresh = shots / "old.png", shots / "fresh.png"
    old.write_bytes(b"png")
    fresh.write_bytes(b"png")
    now = time.time()
    os.utime(old, (now - PREVIEW_SHOT_TTL_SECONDS - 60, now - PREVIEW_SHOT_TTL_SECONDS - 60))

    assert cleanup_expired_preview_shots([tmp_path], now) == 1
    assert not old.exists()
    assert fresh.exists()
