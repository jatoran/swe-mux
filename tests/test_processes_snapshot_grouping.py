"""`snapshot_all`'s grouping, against a literal transcription of the one it replaced.

The change is a projection change and nothing else: one pass over the owned
processes instead of a full scan per session, plus one serialization per process.
So the test is an equivalence test - the reference below is the previous
implementation copied verbatim, and any divergence is the regression.
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any, cast

import pytest

from swe_mux.event_bus import EventBus
from swe_mux.processes import OwnedProcess, ProcessInspector


def owned(
    pid: int,
    session_id: str,
    *,
    exited_at: float | None = None,
    project_id: str | None = None,
) -> OwnedProcess:
    return OwnedProcess(
        pid,
        1,
        session_id,
        "worker.exe",
        "worker.exe --serve",
        10.0,
        exited_at,
        0.0,
        1024,
        [],
        [],
        project_id=project_id,
    )


def session(project_id: str, *, agent_run_id: str | None = None) -> Any:
    return SimpleNamespace(
        record=SimpleNamespace(
            project_id=project_id,
            trusted_scope_id=f"scope-{project_id}",
            agent_run_id=agent_run_id,
            run_repo_group_id="run-group",
            spawn_repo_group_id="spawn-group",
        )
    )


def legacy_groups(
    inspector: ProcessInspector, *, include_ended: bool
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """The pre-S8 body, transcribed. Do not tidy it: it is the oracle."""
    from swe_mux.processes import MAX_PROCESSES_PER_SESSION

    groups: list[dict[str, Any]] = []
    all_processes: list[dict[str, Any]] = []
    session_ids = list(inspector.sessions.sessions)
    session_ids.extend(
        sorted(
            {item.session_id for item in inspector.owned.values()}
            - set(inspector.sessions.sessions)
        )
    )
    for session_id in session_ids:
        current = inspector.sessions.sessions.get(session_id)
        processes = [
            item.snapshot()
            for item in inspector.owned.values()
            if item.session_id == session_id and (include_ended or item.exited_at is None)
        ]
        processes.sort(key=lambda item: (item["exited_at"] is not None, item["pid"]))
        if not processes and current is None:
            continue
        all_processes.extend(processes)
        groups.append(
            {
                "session_id": session_id,
                "project_id": (
                    current.record.project_id
                    if current
                    else next(
                        (
                            item.project_id
                            for item in inspector.owned.values()
                            if item.session_id == session_id
                        ),
                        None,
                    )
                ),
                "project_scope_id": current.record.trusted_scope_id if current else None,
                "repo_group_id": (
                    current.record.run_repo_group_id
                    if current and current.record.agent_run_id
                    else current.record.spawn_repo_group_id
                    if current
                    else None
                ),
                "processes": processes[:MAX_PROCESSES_PER_SESSION],
            }
        )
    return groups, all_processes


@pytest.fixture
def inspector() -> ProcessInspector:
    sessions = {
        "live-a": session("project-a", agent_run_id="run-1"),
        "live-b": session("project-b"),
        "live-empty": session("project-c"),
    }
    built = ProcessInspector(cast(Any, SimpleNamespace(sessions=sessions)), EventBus())
    # Nothing here samples: the cached collection is fresh, so `_ensure_sampled`
    # returns without walking the real process table.
    built._last_collect = time.monotonic()
    # Insertion order is load-bearing for the project fallback below, so it is set
    # here deliberately rather than incidentally.
    for item in (
        owned(30, "live-a", project_id="project-a"),
        owned(10, "live-a", project_id="project-a"),
        owned(20, "live-a", exited_at=99.0, project_id="project-a"),
        owned(40, "live-b", project_id="project-b"),
        owned(50, "gone-session", exited_at=50.0, project_id="project-from-ended"),
        owned(60, "gone-session", project_id="project-from-live"),
        owned(70, "fully-ended-session", exited_at=70.0, project_id="project-d"),
    ):
        built.owned[(item.pid, 10.0)] = item
    return built


async def test_grouping_matches_the_scan_per_session_it_replaced(
    inspector: ProcessInspector,
) -> None:
    expected, expected_all = legacy_groups(inspector, include_ended=False)

    snapshot = await inspector.snapshot_all()

    assert snapshot["sessions"] == expected
    assert snapshot["totals"]["processes"] == len(
        [item for item in expected_all if item["exited_at"] is None]
    )


async def test_grouping_matches_the_old_one_with_ended_processes_included(
    inspector: ProcessInspector,
) -> None:
    expected, _ = legacy_groups(inspector, include_ended=True)

    snapshot = await inspector.snapshot_all(include_ended=True)

    assert snapshot["sessions"] == expected


async def test_a_dead_session_keeps_the_project_of_its_first_owned_process(
    inspector: ProcessInspector,
) -> None:
    """Including when that first process is an ended one the projection dropped.

    The old fallback read `self.owned` in insertion order with no exited filter, so
    an index that only remembered *retained* processes would answer differently
    here - which is the one way a one-pass rewrite could silently drift.
    """
    snapshot = await inspector.snapshot_all()
    group = next(item for item in snapshot["sessions"] if item["session_id"] == "gone-session")

    assert group["project_id"] == "project-from-ended"
    assert [process["pid"] for process in group["processes"]] == [60]
    assert group["project_scope_id"] is None


async def test_a_session_with_nothing_left_to_act_on_is_dropped_entirely(
    inspector: ProcessInspector,
) -> None:
    snapshot = await inspector.snapshot_all()
    listed = {group["session_id"] for group in snapshot["sessions"]}

    assert "fully-ended-session" not in listed
    # A live session with no processes is still listed, so the operator can see the
    # session itself.
    assert "live-empty" in listed


async def test_each_process_is_serialized_once_and_ordered_live_first(
    inspector: ProcessInspector,
) -> None:
    snapshot = await inspector.snapshot_all(include_ended=True)
    group = next(item for item in snapshot["sessions"] if item["session_id"] == "live-a")

    assert [process["pid"] for process in group["processes"]] == [10, 30, 20]
    every_pid = [
        process["pid"] for entry in snapshot["sessions"] for process in entry["processes"]
    ]
    assert len(every_pid) == len(set(every_pid))
