"""The Project observation stream and the human decisions taken on it."""

from __future__ import annotations

import logging
from typing import Any

from aiohttp import web

from .. import (
    app_keys as keys,
)
from ..config import Config
from ..harness import (
    agent_harnesses,
    is_agent_harness,
)
from ..http_support import json_response
from ..land_queue import LandRefusal
from ..project_files import (
    ObservationsUnreadableError,
    append_observation,
    read_observations,
    update_observation_request,
    write_observations,
)
from ..prompt_queue import QueueError
from . import sessions, terminal
from .support import _human_sender_kind, _observations_project, _registered_identity

log = logging.getLogger(__name__)


async def get_observations(request: web.Request) -> web.Response:
    project = _observations_project(request)
    result = await read_observations(project.root, project=_registered_identity(project))
    result.update({"project_id": project.id, "project_name": project.name})
    return json_response(result)


async def post_observation(request: web.Request) -> web.Response:
    project = _observations_project(request)
    body = await request.json()
    try:
        result = await append_observation(
            project.root, str(body.get("body") or ""), project=_registered_identity(project)
        )
    except ObservationsUnreadableError as exc:
        # Refusing beats "read as empty, then clobber": the file holds the user's
        # own notes and the next append would be the thing that destroys them.
        return json_response({"error": str(exc), "code": "observations_unreadable"}, 409)
    result.update({"project_id": project.id, "project_name": project.name})
    return json_response(result)


async def put_observations(request: web.Request) -> web.Response:
    project = _observations_project(request)
    body = await request.json()
    observations = body.get("observations")
    if not isinstance(observations, list):
        raise ValueError("observations must be a list")
    try:
        result = await write_observations(
            project.root,
            observations,
            str(body.get("revision") or "missing"),
            project=_registered_identity(project),
        )
    except ObservationsUnreadableError as exc:
        return json_response({"error": str(exc), "code": "observations_unreadable"}, 409)
    except ValueError as exc:
        if "changed externally" in str(exc):
            return json_response({"error": str(exc), "code": "revision_conflict"}, 409)
        raise
    result.update({"project_id": project.id, "project_name": project.name})
    return json_response(result)


async def _approve_control_request(
    request: web.Request,
    project: Any,
    identity: Any,
    observation_id: str,
    req: dict[str, Any],
) -> web.Response:
    """Perform a human-approved drafted interrupt/end (Phase 7.6, CP §7.6).

    Approval is the human act that carries the authority; it runs the same shared
    daemon operation the granted path uses. The daemon-owner and non-agent guards
    still hold - a human approving cannot make the daemon-hosting session a valid
    target - and the readiness gate still protects an interrupt from landing in an
    approval prompt.
    """
    app = request.app
    action = str(req.get("action") or "")
    target_id = str(req.get("target_session_id") or "")
    target = app[keys.SESSIONS].sessions.get(target_id)
    extra: dict[str, Any] = {}
    if target is None or target.record.state in {"exited", "crashed"}:
        outcome = "target_gone"
    elif terminal._session_owns_daemon(target) or not is_agent_harness(target.record.backend):
        return json_response(
            {
                "error": "the target is not a valid control target",
                "code": "forbidden_target",
            },
            409,
        )
    elif action == "interrupt":
        evaluation = app[keys.PROMPT_QUEUE].readiness.evaluate(target)
        if str(evaluation.get("delivery_state") or "unknown") != "safe":
            return json_response(
                {
                    "error": "the target is not safe to interrupt right now",
                    "code": "readiness_not_safe",
                    "delivery_state": evaluation.get("delivery_state"),
                },
                409,
            )
        await terminal._interrupt_session_pty(app, target)
        outcome = "interrupted"
    elif action == "end_session":
        result = await terminal._end_session_gracefully(app, target, "agent_ended")
        outcome = "ended"
        extra = {"final_state": result.get("final_state"), "graceful": result.get("graceful")}
    else:
        raise ValueError(f"unknown control action {action!r}")
    updated = await update_observation_request(
        project.root,
        observation_id,
        {
            "status": "approved",
            "decided_by": _human_sender_kind(request),
            "outcome": outcome,
        },
        done=True,
        project=identity,
    )
    await app[keys.EVENTS].emit(
        "agent_session_control",
        session_id=str(req.get("from_session") or "") or None,
        source="user",
        action=action,
        outcome=outcome,
        target_session_id=target_id,
        request_id=observation_id,
        project_id=project.id,
    )
    updated.update(
        {"project_id": project.id, "project_name": project.name, "outcome": outcome, **extra}
    )
    return json_response(updated)


async def _approve_land_request(
    request: web.Request,
    project: Any,
    identity: Any,
    observation_id: str,
    req: dict[str, Any],
) -> web.Response:
    """Enqueue a human-approved drafted land (Phase 14).

    Approval is the human act that carries the authority, so this enqueues on the
    operator path and the grant is not consulted again. The originating session is
    retained as the request's origin, because a handback has to reach the agent that
    asked rather than the human who approved.
    """
    app = request.app
    try:
        row = await app[keys.LAND_QUEUE].request(
            project_id=project.id,
            project_root=str(req.get("project_root") or project.root),
            worktree_root=str(req.get("worktree_root") or ""),
            origin="agent_approved",
            origin_session_id=str(req.get("from_session") or ""),
            origin_run_id=str(req.get("from_run_id") or ""),
            reason=str(req.get("reason") or ""),
            # Part of what the session asked for, not part of what the human decides:
            # approval says the land may run, and the requester's own request says
            # whether it wants to be told that it did.
            report_success=bool(req.get("report_success")),
        )
    except LandRefusal as exc:
        return json_response({"error": exc.message, "code": exc.code}, 409)
    updated = await update_observation_request(
        project.root,
        observation_id,
        {
            "status": "approved",
            "decided_by": _human_sender_kind(request),
            "outcome": "queued",
            "request_id": str(row.get("id") or ""),
        },
        done=True,
        project=identity,
    )
    await app[keys.EVENTS].emit(
        "agent_land_decided",
        session_id=str(req.get("from_session") or "") or None,
        source="user",
        request_id=observation_id,
        project_id=project.id,
        decision="approved",
    )
    updated.update({"project_id": project.id, "project_name": project.name, "land": row})
    return json_response(updated)


async def decide_observation_request(request: web.Request) -> web.Response:
    """Approve or dismiss a drafted `mux.requestSpawn` (Phase 5, CP §7.2).

    Approval is the human act that creates the session — the agent never held
    spawn authority, it only asked. The prompt travels as ``seed_text`` through
    the ordinary spawn path, so nothing about the new session is special.
    """
    project = _observations_project(request)
    observation_id = request.match_info["observation_id"]
    body = await request.json()
    decision = str(body.get("decision") or "").strip()
    if decision not in {"approve", "dismiss"}:
        raise ValueError("decision must be approve or dismiss")
    identity = _registered_identity(project)
    current = await read_observations(project.root, project=identity)
    if current.get("status") == "malformed":
        return json_response(
            {"error": str(current.get("error") or ""), "code": "observations_unreadable"}, 409
        )
    item = next(
        (
            entry
            for entry in current["observations"]
            if entry.get("id") == observation_id
            and entry.get("kind") in {"spawn_request", "control_request", "land_request"}
        ),
        None,
    )
    if item is None:
        raise ValueError("no such request")
    kind = str(item.get("kind"))
    pending_request = dict(item.get("request") or {})
    if pending_request.get("status") not in {None, "", "pending"}:
        return json_response(
            {
                "error": f"this request was already {pending_request.get('status')}",
                "code": "already_decided",
            },
            409,
        )
    if decision == "dismiss":
        result = await update_observation_request(
            project.root,
            observation_id,
            {"status": "dismissed", "decided_by": _human_sender_kind(request)},
            done=True,
            project=identity,
        )
        await request.app[keys.EVENTS].emit(
            {
                "control_request": "control_request_decided",
                "land_request": "agent_land_decided",
            }.get(kind, "spawn_request_decided"),
            session_id=str(pending_request.get("from_session") or "") or None,
            source="user",
            request_id=observation_id,
            project_id=project.id,
            decision="dismissed",
        )
        result.update({"project_id": project.id, "project_name": project.name})
        return json_response(result)
    if kind == "control_request":
        # Phase 7.6: approving a drafted interrupt/end is the human act that
        # performs it, through the same daemon operation the granted path uses.
        return await _approve_control_request(
            request, project, identity, observation_id, pending_request
        )
    if kind == "land_request":
        # Phase 14: approving a drafted land is the human act that enqueues it,
        # through the same service the granted path uses.
        return await _approve_land_request(
            request, project, identity, observation_id, pending_request
        )
    spawn_request = pending_request
    prompt = str(body.get("prompt") or spawn_request.get("prompt") or "")
    if not prompt.strip():
        raise ValueError("the request has no prompt to seed")
    # An observation spawn always seeds a prompt, so it needs an agent. It honours a
    # configured default when that default is one, and otherwise takes the first
    # registered harness rather than a name written in here. `default_backend` is
    # allowed to be `shell` and cannot be used unfiltered.
    config: Config = request.app[keys.CONFIG]
    configured_default = project.default_backend or config.default_backend
    spawn_body: dict[str, Any] = {
        "project_id": project.id,
        "backend": str(
            body.get("backend")
            or spawn_request.get("backend")
            or (
                configured_default if is_agent_harness(configured_default) else agent_harnesses()[0]
            )
        ),
        "seed_text": prompt,
    }
    # The requester's model, if it asked for one. Approving a card that says "on
    # opus" has to start an opus session: a field the approval drops is worse than
    # one it never accepted, because the human has already agreed to it. It was
    # validated against this backend when the request was drafted, and is
    # validated again by the spawn contract below - a Project whose default
    # harness changed in between refuses here rather than spawning something else.
    model = str(body.get("model") or spawn_request.get("model") or "")
    if model:
        spawn_body["model"] = model
    name = str(body.get("name") or spawn_request.get("name") or "")
    if name:
        spawn_body["name"] = name
    cwd = str(body.get("cwd") or spawn_request.get("cwd") or "")
    if cwd:
        spawn_body["cwd"] = cwd
    session = await sessions._spawn_from_body(request.app, spawn_body)
    pane_hint = str(spawn_request.get("pane") or "")
    if pane_hint in {"split_horizontal", "split_vertical"}:
        # The requester's deferred placement ask, honoured only now that a
        # human has approved the spawn. One-shot: the first browser viewing
        # the Project claims and clears it.
        session.record.pane_hint = pane_hint
    watch_outcome = await _arm_requested_watch(request, spawn_request, session)
    result = await update_observation_request(
        project.root,
        observation_id,
        {
            "status": "approved",
            "session_id": session.record.id,
            "decided_by": _human_sender_kind(request),
        },
        done=True,
        project=identity,
    )
    await request.app[keys.EVENTS].emit(
        "spawn_request_decided",
        session_id=str(spawn_request.get("from_session") or "") or None,
        source="user",
        request_id=observation_id,
        project_id=project.id,
        decision="approved",
        spawned_session_id=session.record.id,
    )
    result.update(
        {
            "project_id": project.id,
            "project_name": project.name,
            "session": session.record.snapshot(),
            **({"watch": watch_outcome} if watch_outcome else {}),
        }
    )
    return json_response(result, 201)


async def _arm_requested_watch(
    request: web.Request, spawn_request: dict[str, Any], session: Any
) -> dict[str, Any] | None:
    """Arm the settle watch a drafted `request_spawn(watch=true)` deferred.

    The consent travels with the request: the watch is armed for the session
    that asked, and only while its conversation is still the run that asked -
    a rolled or ended requester gets nothing, exactly as a live watch would
    have been dropped (`session_watch.py`). A watch that cannot be armed never
    fails the approval; the spawn is the human's act and it has succeeded.
    """
    if str(spawn_request.get("watch") or "") != "true":
        return None
    watch_service = request.app.get(keys.SESSION_WATCH)
    if watch_service is None:
        return {"armed": False, "reason": "session watches are unavailable"}
    requester_id = str(spawn_request.get("from_session") or "")
    requester = request.app[keys.SESSIONS].sessions.get(requester_id)
    if requester is None:
        return {"armed": False, "reason": "the requesting session has ended"}
    asked_run = str(spawn_request.get("from_run_id") or "")
    live_run = str(getattr(requester.record, "agent_run_id", "") or "")
    if asked_run and live_run and asked_run != live_run:
        return {"armed": False, "reason": "the requesting conversation was replaced"}
    try:
        view = await watch_service.watch(
            requester,
            target=str(session.record.id),
            timeout_minutes=str(spawn_request.get("watch_timeout_minutes") or "")
            or None,
            project="fleet",
        )
    except QueueError as exc:
        return {"armed": False, "reason": f"{exc.code}: {exc}"}
    except Exception:  # noqa: BLE001 - a failed watch must not fail the approval
        log.exception(
            "spawn approval watch arming failed requester=%s target=%s",
            requester_id,
            session.record.id,
        )
        return {"armed": False, "reason": "the watch could not be armed"}
    return {"armed": True, "watch_id": str(view.get("watch_id") or "")}


ROUTES: tuple[web.RouteDef, ...] = (
    web.get("/api/projects/{project_id}/observations", get_observations),
    web.post("/api/projects/{project_id}/observations", post_observation),
    web.put("/api/projects/{project_id}/observations", put_observations),
    # Approving a drafted spawn request is the human act that creates
    # the session (`CONTROL_PLANE_ROADMAP.md` §7.2); dismissing it is
    # the other half. Both live here so mobile can do either.
    web.post(
        "/api/projects/{project_id}/observations/{observation_id}/decide",
        decide_observation_request,
    ),
)
