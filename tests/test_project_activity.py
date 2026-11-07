"""A Project rises under Recently used when one of its sessions comes to life."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from swe_mux.event_bus import EventBus
from swe_mux.history import HistoryIndex
from swe_mux.project_activity import COALESCE_SECONDS, ProjectActivityService, activity_reason
from swe_mux.projects import ProjectManager


def _event(
    kind: str, *, ts: float = 10_000.0, session_id: str | None = "s1", **payload: Any
) -> Any:
    return SimpleNamespace(type=kind, ts=ts, session_id=session_id, payload=payload)


@pytest.mark.parametrize(
    ("kind", "payload", "reason"),
    [
        ("session_spawned", {}, "session_started"),
        ("turn_started", {"scope": "root"}, "turn_started"),
        ("state_changed", {"previous": "idle", "state": "working"}, "session_resumed"),
        ("state_changed", {"previous": "awaiting", "state": "working"}, "session_resumed"),
        ("state_changed", {"previous": "starting", "state": "working"}, "session_resumed"),
        ("queue_delivery", {"outcome": "sent"}, "message_delivered"),
    ],
)
def test_what_counts_as_a_session_coming_to_life(
    kind: str, payload: dict[str, Any], reason: str
) -> None:
    assert activity_reason(_event(kind, **payload)) == reason


@pytest.mark.parametrize(
    ("kind", "payload"),
    [
        # Finishing is not waking, and the sidebar is sorted by what started most recently.
        ("state_changed", {"previous": "working", "state": "idle"}),
        ("state_changed", {"previous": "working", "state": "awaiting"}),
        ("state_changed", {"previous": "working", "state": "working"}),
        ("session_exited", {}),
        ("turn_ended", {}),
        # A subagent's turn is inside a run that already counted.
        ("turn_started", {"scope": "subagent"}),
        # A message that never reached the session did nothing there.
        ("queue_delivery", {"outcome": "failed"}),
        # Being looked at and being typed into before submit are not activity.
        ("terminal_attached", {}),
        ("terminal_input", {}),
        ("project_used", {}),
    ],
)
def test_what_does_not_count(kind: str, payload: dict[str, Any]) -> None:
    assert activity_reason(_event(kind, **payload)) is None


async def _service(
    tmp_path: Path, *, session_id: str = "s1", turn_started_at: float | None = None
) -> tuple[ProjectActivityService, Any, Any, EventBus, HistoryIndex]:
    history = HistoryIndex(tmp_path / "mux.db")
    projects = ProjectManager(history)
    await projects.start()
    root = tmp_path / "repo"
    root.mkdir()
    project = await projects.create("Main", str(root))
    record = SimpleNamespace(project_id=project.id, turn_started_at=turn_started_at)
    sessions = SimpleNamespace(sessions={session_id: SimpleNamespace(record=record)})
    events = EventBus()
    return ProjectActivityService(projects, sessions, events), projects, project, events, history


async def test_activity_stamps_the_sessions_project_and_tells_every_client(tmp_path: Path) -> None:
    """The reported gap: a message or a schedule woke a session with no browser involved,
    so nothing moved its Project."""
    service, _projects, project, events, history = await _service(tmp_path)
    stamp = project.last_used_at + 60.0
    queue = events.subscribe(name="client")

    await service.handle_event(_event("queue_delivery", ts=stamp, outcome="sent"))

    assert project.last_used_at == stamp
    emitted = queue.get_nowait()
    assert emitted.type == "project_used"
    assert emitted.source == "daemon"
    assert emitted.payload == {
        "project_id": project.id,
        "last_used_at": stamp,
        "reason": "message_delivered",
    }
    history.close()


async def test_the_stamp_survives_a_restart(tmp_path: Path) -> None:
    service, _projects, project, _events, history = await _service(tmp_path)
    stamp = project.last_used_at + 60.0

    await service.handle_event(_event("session_spawned", ts=stamp))
    history.close()

    reopened_history = HistoryIndex(tmp_path / "mux.db")
    reopened = ProjectManager(reopened_history)
    await reopened.start()
    assert reopened.projects[project.id].last_used_at == stamp
    reopened_history.close()


async def test_one_activation_is_one_write_and_one_event(tmp_path: Path) -> None:
    """A delivered message opens a turn, which reports twice. All one fact to the sidebar."""
    service, _projects, project, events, history = await _service(tmp_path)
    first = project.last_used_at + 60.0
    queue = events.subscribe(name="client")

    await service.handle_event(_event("queue_delivery", ts=first, outcome="sent"))
    await service.handle_event(
        _event("state_changed", ts=first + 0.5, previous="idle", state="working")
    )
    await service.handle_event(_event("turn_started", ts=first + 0.6, scope="root"))

    assert project.last_used_at == first
    assert queue.qsize() == 1

    later = first + COALESCE_SECONDS + 1
    await service.handle_event(_event("turn_started", ts=later, scope="root"))
    assert project.last_used_at == later
    assert queue.qsize() == 2
    history.close()


async def test_a_turn_readopted_after_a_restart_does_not_reorder_the_sidebar(
    tmp_path: Path,
) -> None:
    """A session working across a restart emits `turn_started` again, dated from when the
    turn really began. Stamping the report instead would float every busy Project to the top
    on each daemon restart and redeploy."""
    service, _projects, project, events, history = await _service(
        tmp_path, turn_started_at=None
    )
    used = project.last_used_at + 3_600.0
    project.last_used_at = used
    began = used - 600.0
    session = service.sessions.sessions["s1"]
    session.record.turn_started_at = began
    queue = events.subscribe(name="client")

    await service.handle_event(_event("turn_started", ts=used + 30.0, scope="root"))

    assert project.last_used_at == used
    assert queue.empty()
    history.close()


async def test_a_fresh_turn_is_stamped_from_when_it_began(tmp_path: Path) -> None:
    service, _projects, project, _events, history = await _service(tmp_path)
    began = project.last_used_at + 60.0
    service.sessions.sessions["s1"].record.turn_started_at = began

    await service.handle_event(_event("turn_started", ts=began + 0.2, scope="root"))

    assert project.last_used_at == began
    history.close()


@pytest.mark.parametrize(
    "event_session",
    [None, "gone"],
    ids=["no session on the event", "session no longer registered"],
)
async def test_an_event_with_no_live_session_is_ignored(
    tmp_path: Path, event_session: str | None
) -> None:
    service, _projects, project, _events, history = await _service(tmp_path)
    before = project.last_used_at

    await service.handle_event(
        _event("session_spawned", ts=before + 60.0, session_id=event_session)
    )

    assert project.last_used_at == before
    history.close()


async def test_a_session_whose_project_was_removed_is_ignored(tmp_path: Path) -> None:
    service, projects, project, _events, history = await _service(tmp_path)
    await projects.remove(project.id)

    await service.handle_event(_event("session_spawned", ts=project.last_used_at + 60.0))

    history.close()


async def test_a_failed_stamp_is_logged_and_the_loop_goes_on(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A sort key must never take down the subscription, or every later activation is lost."""
    service, projects, project, _events, history = await _service(tmp_path)

    async def locked(_project_id: str, *, used_at: float | None = None) -> Any:
        raise OSError("database is locked")

    original = projects.touch_used
    projects.touch_used = locked
    stamp = project.last_used_at + 60.0
    with caplog.at_level("WARNING", logger="swe_mux.project_activity"):
        await service.handle_event(_event("session_spawned", ts=stamp))
    assert "project_used_not_recorded" in caplog.text
    assert project.last_used_at != stamp

    projects.touch_used = original
    await service.handle_event(_event("session_spawned", ts=stamp))
    assert project.last_used_at == stamp
    history.close()


async def test_the_running_service_reacts_to_the_bus(tmp_path: Path) -> None:
    """Subscribed end to end, rather than only callable."""
    service, _projects, project, events, history = await _service(tmp_path)
    client = events.subscribe(name="client")
    # Older than the coalescing window, or registering it just now would swallow the event.
    project.last_used_at = 1_000.0
    service.start()
    try:
        await events.emit("session_spawned", session_id="s1", backend="claude", name="x")
        while True:
            event = await asyncio.wait_for(client.get(), timeout=5)
            if event.type == "project_used":
                break
        assert event.payload["reason"] == "session_started"
        assert project.last_used_at > 1_000.0
    finally:
        await service.stop()
        history.close()
