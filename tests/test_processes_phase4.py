from __future__ import annotations

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
    session = SimpleNamespace(record=SimpleNamespace(id="session-a", project_id="default", pid=10))
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
            record=SimpleNamespace(id=identity, project_id="project-a", pid=pid)
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
