"""Scheduled runs: definitions, previews, and their run history."""

from __future__ import annotations

import logging
import time
from typing import Any

from aiohttp import web

from .. import (
    app_keys as keys,
)
from .. import (
    session_titles,
)
from ..config import Config
from ..http_support import json_response
from ..schedule_store import ScheduleStore
from ..scheduler import ScheduleService, spec_from_row
from ..schedules import first_occurrence, next_occurrence, parse_spec
from ..session_resume import resolve_latest_run
from .support import _observations_project

log = logging.getLogger(__name__)


def _schedule_service(request: web.Request) -> ScheduleService:
    service = request.app.get(keys.SCHEDULES)
    if service is None:  # pragma: no cover - only a partially built app
        raise web.HTTPServiceUnavailable(text="scheduled runs are unavailable")
    return service


async def _schedule_view(
    request: web.Request, schedule: dict[str, Any], *, runs: int = 5
) -> dict[str, Any]:
    """One schedule, plus the two things a reader cannot derive from the row.

    `blocked` is the live permission answer rather than a stored flag: a Project
    can be opted out after a schedule was written, and a row that still reads
    `enabled` while nothing will ever fire is the exact lie this surface exists
    to avoid. `runs` is the recent history the tab shows under the row.
    """
    store: ScheduleStore = request.app[keys.SCHEDULE_STORE]
    project = request.app[keys.PROJECTS].projects.get(str(schedule["project_id"]))
    blocked = ""
    if project is None:
        blocked = "project_missing"
    else:
        gate = request.app.get(keys.AUTOMATION_GATE)
        if gate is not None and "scheduled_runs" not in await gate(str(project.root)):
            blocked = "automation_disabled"
    config: Config = request.app[keys.CONFIG]
    if not config.scheduled_runs_enabled:
        blocked = blocked or "install_disabled"
    return {
        **schedule,
        "project_name": project.name if project else "",
        "blocked": blocked,
        "target": await _schedule_target_view(request, schedule),
        "runs": await store.runs(str(schedule["id"]), limit=runs),
    }


async def _schedule_target_view(
    request: web.Request, schedule: dict[str, Any]
) -> dict[str, Any] | None:
    """What a resume schedule points at, named the way the operator would name it.

    Resolved on read rather than stored beside the id, for the same reason `blocked`
    is: a stored label would keep claiming a conversation that has since been deleted
    from History, and a row that reads armed against a target that no longer exists is
    exactly the lie this surface exists to avoid. ``missing`` is the honest answer, and
    the schedule's next fire will disable itself on it.

    For a rolling continuation this also reports where the conversation has actually
    got to, which is the one thing about that kind a reader cannot work out for
    themselves.
    """
    if str(schedule.get("action") or "spawn") != "resume":
        return None
    run_id = str(schedule.get("target_run_id") or "")
    history = request.app.get(keys.HISTORY)
    if not run_id or history is None:
        return {"run_id": run_id, "missing": True}
    row = await history.history_entry(run_id)
    if row is None:
        return {"run_id": run_id, "missing": True}
    resolved = row
    if str(schedule.get("target_kind")) == "latest_of_session":
        resolved = (
            await resolve_latest_run(
                run_id,
                history=history,
                automation_store=request.app[keys.AUTOMATION_STORE],
            )
            or row
        )
    # The two-name rule is `session_titles.py`'s, and asking it is what keeps this row in
    # step with the History browser, the sidebar, and every other surface that names a run.
    titles = await session_titles.generated_titles(
        request.app.get(keys.AUTOMATION_STORE),
        {session_titles.row_run_id(row), session_titles.row_run_id(resolved)},
    )
    return {
        "run_id": run_id,
        "missing": False,
        "backend": str(row.get("backend") or ""),
        "name": session_titles.row_display_name(row, titles),
        "resolved_run_id": str(resolved["id"]),
        "resolved_name": session_titles.row_display_name(resolved, titles),
        "resolved_at": resolved.get("last_message_at") or resolved.get("spawned_at"),
        "context_pct": resolved.get("final_context_pct"),
    }


async def list_schedules(request: web.Request) -> web.Response:
    """Every schedule, or one Project's.

    The unscoped form is what makes the tab's fleet toggle possible: "what fires
    tonight" is not a per-Project question, even though every schedule belongs to
    exactly one Project.
    """
    store: ScheduleStore = request.app[keys.SCHEDULE_STORE]
    project_id = request.query.get("project_id") or None
    if project_id and project_id not in request.app[keys.PROJECTS].projects:
        raise ValueError(f"unknown project: {project_id}")
    rows = await store.list_schedules(project_id)
    service = _schedule_service(request)
    return json_response(
        {
            "schedules": [await _schedule_view(request, row) for row in rows],
            "status": await service.status(),
        }
    )


async def list_project_schedules(request: web.Request) -> web.Response:
    project = _observations_project(request)
    store: ScheduleStore = request.app[keys.SCHEDULE_STORE]
    rows = await store.list_schedules(project.id)
    service = _schedule_service(request)
    return json_response(
        {
            "project_id": project.id,
            "schedules": [await _schedule_view(request, row) for row in rows],
            "status": await service.status(),
        }
    )


async def create_project_schedule(request: web.Request) -> web.Response:
    """Write a new schedule for one Project.

    Permission is deliberately *not* required to write one: a user may author a
    schedule and opt the Project in afterwards, and refusing the write would make
    the toggle discoverable only by failing. What permission gates is firing, and
    the response says so through `blocked`.
    """
    project = _observations_project(request)
    store: ScheduleStore = request.app[keys.SCHEDULE_STORE]
    body = await request.json()
    spec = parse_spec(body)
    now = time.time()
    schedule = await store.create(
        project_id=project.id,
        project_root=str(project.root),
        spec=spec,
        next_fire_at=first_occurrence(spec, now=now),
        now=now,
    )
    await request.app[keys.EVENTS].emit(
        "schedule_changed",
        source="user",
        action="created",
        schedule_id=schedule["id"],
        project_id=project.id,
    )
    return json_response(await _schedule_view(request, schedule), 201)


async def patch_schedule(request: web.Request) -> web.Response:
    """Replace a definition, or arm/disarm it.

    A body carrying only `enabled` is the pause switch and keeps the existing
    definition; anything else is a full replacement validated exactly like a
    create, because a schedule is small and a partial-update surface over a
    trigger is how one ends up with a cron expression and an interval both set.
    """
    store: ScheduleStore = request.app[keys.SCHEDULE_STORE]
    schedule_id = request.match_info["schedule_id"]
    current = await store.get(schedule_id)
    if current is None:
        raise KeyError(schedule_id)
    body = await request.json()
    revision = body.get("revision")
    if revision is not None and not isinstance(revision, int):
        raise ValueError("revision must be an integer")
    now = time.time()
    if set(body) <= {"enabled", "revision"} and "enabled" in body:
        spec = spec_from_row(current)
        enabled = bool(body["enabled"])
        updated = await store.set_enabled(
            schedule_id,
            enabled,
            next_fire_at=first_occurrence(spec, now=now) if enabled else None,
            reason="" if enabled else "paused",
            now=now,
        )
    else:
        spec = parse_spec(body)
        updated = await store.replace(
            schedule_id,
            spec=spec,
            next_fire_at=first_occurrence(spec, now=now),
            revision=revision,
            now=now,
        )
        if updated is None:
            return json_response(
                {
                    "error": "this schedule changed elsewhere; re-read it and try again",
                    "code": "revision_conflict",
                },
                409,
            )
    if updated is None:
        raise KeyError(schedule_id)
    await request.app[keys.EVENTS].emit(
        "schedule_changed",
        source="user",
        action="updated",
        schedule_id=schedule_id,
        project_id=str(updated["project_id"]),
    )
    return json_response(await _schedule_view(request, updated))


async def delete_schedule(request: web.Request) -> web.Response:
    store: ScheduleStore = request.app[keys.SCHEDULE_STORE]
    schedule_id = request.match_info["schedule_id"]
    existing = await store.get(schedule_id)
    if not await store.delete(schedule_id):
        raise KeyError(schedule_id)
    await request.app[keys.EVENTS].emit(
        "schedule_changed",
        source="user",
        action="deleted",
        schedule_id=schedule_id,
        project_id=str(existing["project_id"]) if existing else "",
    )
    return json_response({"deleted": True, "id": schedule_id})


async def run_schedule_now(request: web.Request) -> web.Response:
    """Fire one schedule immediately.

    Still subject to every fire-time guard except lateness - an explicit request
    is never a missed window - so "Run now" cannot be used to walk around the
    Project opt-in, the overlap policy, or the concurrency ceiling.
    """
    service = _schedule_service(request)
    schedule_id = request.match_info["schedule_id"]
    run = await service.run_now(schedule_id)
    if run is None:
        raise KeyError(schedule_id)
    store: ScheduleStore = request.app[keys.SCHEDULE_STORE]
    schedule = await store.get(schedule_id)
    return json_response(
        {
            "run": run,
            "schedule": await _schedule_view(request, schedule) if schedule else None,
        }
    )


async def list_schedule_runs(request: web.Request) -> web.Response:
    store: ScheduleStore = request.app[keys.SCHEDULE_STORE]
    schedule_id = request.match_info["schedule_id"]
    if await store.get(schedule_id) is None:
        raise KeyError(schedule_id)
    limit = int(request.query.get("limit") or 50)
    return json_response({"runs": await store.runs(schedule_id, limit=limit)})


async def preview_schedule(request: web.Request) -> web.Response:
    """Answer "when would this fire" for an unsaved definition.

    The editor calls this on every trigger change. A cron expression is the one
    field in this feature whose meaning cannot be read off its own text, and a
    surface that shows the next three fire times is the difference between
    writing one confidently and finding out at 3 a.m.
    """
    spec = parse_spec(await request.json())
    now = time.time()
    fires: list[float] = []
    cursor = now
    for _ in range(5):
        following = next_occurrence(spec, cursor)
        if following is None:
            break
        fires.append(following)
        cursor = following
    return json_response({"next_fires": fires, "now": now})


ROUTES: tuple[web.RouteDef, ...] = (
    # Scheduled runs. Definitions are Project-owned (a spawn belongs to
    # exactly one Project) while the listing is also reachable unscoped,
    # because "what fires tonight" spans them.
    web.get("/api/schedules", list_schedules),
    web.post("/api/schedules/preview", preview_schedule),
    web.get("/api/projects/{project_id}/schedules", list_project_schedules),
    web.post("/api/projects/{project_id}/schedules", create_project_schedule),
    web.patch("/api/schedules/{schedule_id}", patch_schedule),
    web.delete("/api/schedules/{schedule_id}", delete_schedule),
    web.post("/api/schedules/{schedule_id}/run", run_schedule_now),
    web.get("/api/schedules/{schedule_id}/runs", list_schedule_runs),
)
