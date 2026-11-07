"""Move a Project up Recently used when one of its sessions comes to life.

A browser can only report what it saw: its own prompt submit or its own session start.
Everything else that wakes a session happens with no browser involved - another agent's
message, a scheduled run, a background task finishing, a loop firing - so the daemon watches
for it here, in the one place all of those paths already report through.

The stamp is best effort. A failed sort write is logged and forgotten, and because this runs
on its own subscription it can never reach the session start or the delivery that caused it.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from .background_tasks import background
from .event_bus import EventBus
from .models import MuxEvent
from .projects import ProjectManager, mark_project_used

log = logging.getLogger(__name__)

PROJECT_ACTIVITY_LOOP = "project-activity"

#: One activation reaches this module several times: a delivered message opens a turn, and an
#: opening turn reports both a state change and a turn start. Without this each of them would
#: be its own database write and its own event to every client, for a sort key that is only
#: ever compared with other Projects' stamps.
COALESCE_SECONDS = 5.0


def activity_reason(event: MuxEvent) -> str | None:
    """Why this event means a session came to life, or None when it does not."""

    payload = event.payload or {}
    if event.type == "session_spawned":
        return "session_started"
    if event.type == "turn_started":
        return "turn_started" if payload.get("scope", "root") == "root" else None
    if event.type == "state_changed":
        # Beside `turn_started` because an answered approval resumes a turn without
        # opening one, and a turn that opens over a still-`working` session has no
        # state change at all, so neither signal covers the other.
        waking = payload.get("state") == "working" and payload.get("previous") != "working"
        return "session_resumed" if waking else None
    if event.type == "queue_delivery":
        # Also the only signal for a message that lands mid-turn, which opens nothing.
        return "message_delivered" if payload.get("outcome") == "sent" else None
    return None


def _activity_time(event: MuxEvent, record: Any) -> float:
    """When this happened, which for a turn is when it began rather than when it was reported.

    A session working across a daemon restart is re-adopted with the date its turn really
    began (`observation._begin_root_turn`), and emits `turn_started` again. Dating the stamp
    from the turn is what stops a restart or a redeploy reordering the sidebar.
    """

    if event.type != "turn_started":
        return event.ts
    started = getattr(record, "turn_started_at", None)
    return min(event.ts, float(started)) if started else event.ts


class ProjectActivityService:
    def __init__(self, projects: ProjectManager, sessions: Any, events: EventBus) -> None:
        self.projects = projects
        self.sessions = sessions
        self.events = events
        self._queue: asyncio.Queue[MuxEvent] | None = None
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        self._queue = self.events.subscribe(name=PROJECT_ACTIVITY_LOOP)
        self._task = background.start(PROJECT_ACTIVITY_LOOP, self._consume)

    async def stop(self) -> None:
        if self._queue is not None:
            self.events.unsubscribe(self._queue)
        if self._task is not None:
            await background.stop(PROJECT_ACTIVITY_LOOP)
        self._queue = None
        self._task = None

    async def _consume(self) -> None:
        assert self._queue is not None
        while True:
            event = await self._queue.get()
            with background.iteration(PROJECT_ACTIVITY_LOOP):
                await self.handle_event(event)

    async def handle_event(self, event: MuxEvent) -> None:
        reason = activity_reason(event)
        if reason is None or not event.session_id:
            return
        session = self.sessions.sessions.get(event.session_id)
        if session is None:
            return
        project_id = str(session.record.project_id or "")
        project = self.projects.projects.get(project_id)
        if project is None:
            return
        used_at = _activity_time(event, session.record)
        if used_at < project.last_used_at + COALESCE_SECONDS:
            return
        try:
            await mark_project_used(
                self.projects,
                self.events,
                project_id,
                reason=reason,
                source="daemon",
                used_at=used_at,
            )
        except Exception:  # noqa: BLE001 - only the sort stamp was lost; the loop must live
            log.warning(
                "project_used_not_recorded project_id=%s reason=%s session_id=%s",
                project_id,
                reason,
                event.session_id,
                exc_info=True,
            )
