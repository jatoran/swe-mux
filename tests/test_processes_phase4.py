from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest

from swe_mux.event_bus import EventBus
from swe_mux.layouts import attach_leaf, layout_terminal_ids
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
        def __init__(self, pid: int, created: float, memory: int, children: list[Any] | None = None):
            self.pid = pid
            self.created = created
            self.memory = memory
            self._children = children or []

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

    daemon_pid = processes.os.getpid()
    attributed = FakeProcess(20, 2.0, 900)
    helper = FakeProcess(30, 3.0, 300)
    root = FakeProcess(daemon_pid, 1.0, 100, [attributed, helper])
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
    }
    assert (20, 2.0) not in seen
    assert (daemon_pid, 1.0) in seen
    assert (30, 3.0) in seen
