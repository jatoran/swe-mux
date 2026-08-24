"""Project Actions: the source, the trust gate, and running one."""

from __future__ import annotations

import asyncio
import contextlib
import difflib
import logging
from pathlib import Path
from typing import Any

from aiohttp import web

from .. import (
    app_keys as keys,
)
from ..config import Config
from ..git_projects import ProjectIdentity
from ..http_support import json_response
from ..models import (
    ProjectRecord,
)
from ..project_actions import (
    ActionStep,
    ProjectActionService,
    action_spawn_body,
    read_actions_source,
    substituted_action,
    write_actions_source,
)
from ..project_files import (
    read_project_config,
)
from ..project_init import init_script_step, select_init_scripts
from ..session import (
    TERMINAL_SESSION_STATES,
)
from . import sessions as session_routes

log = logging.getLogger(__name__)


# What one task file's approval diff may occupy in a response. Generous enough for
# a rewritten `tasks.json` and bounded so a generated `package.json` cannot make the
# approval dialog unrenderable.
MAX_ACTION_DIFF = 64 * 1024


def _action_project(request: web.Request):  # type: ignore[no-untyped-def]
    project_id = request.match_info["project_id"]
    project = request.app[keys.PROJECTS].projects.get(project_id)
    if project is None:
        raise ValueError(f"unknown project: {project_id}")
    return project


async def list_project_actions(request: web.Request) -> web.Response:
    project = _action_project(request)
    service: ProjectActionService = request.app[keys.PROJECT_ACTIONS]
    return json_response(service.catalog(project.root).snapshot())


async def trust_project_actions(request: web.Request) -> web.Response:
    project = _action_project(request)
    body = await request.json()
    fingerprint = str(body.get("fingerprint") or "")
    if not fingerprint:
        raise ValueError({"fingerprint": "is required"})
    # With `source`, the fingerprint is that one file's digest and only it is
    # approved. Without, the fingerprint is the whole-catalog digest, which is what
    # the Run menu's single prompt sends and what every existing client sends.
    source = str(body.get("source")) if body.get("source") else None
    service: ProjectActionService = request.app[keys.PROJECT_ACTIONS]
    catalog = service.trust(project.root, fingerprint, source=source)
    log.info(
        "project_actions_trusted project_id=%s source=%s files=%d",
        project.id,
        source or "*",
        len(catalog.sources),
    )
    await request.app[keys.EVENTS].emit(
        "project_actions_trusted",
        source="user",
        project_id=project.id,
        fingerprint=catalog.fingerprint,
        approved_source=source,
        files=list(catalog.sources),
    )
    return json_response(catalog.snapshot())


async def diff_project_actions(request: web.Request) -> web.Response:
    """What changed in each task file since it was last approved.

    "These files changed" is not enough information to approve safely: it cannot
    separate a renamed label from a new `curl | sh`. Every source is reported, with
    an explicit reason when no diff can be produced, so a caller never has to read
    an empty diff as "nothing changed".
    """
    project = _action_project(request)
    service: ProjectActionService = request.app[keys.PROJECT_ACTIONS]
    catalog = service.catalog(project.root)
    root = Path(catalog.root)
    entries: list[dict[str, Any]] = []
    for item in catalog.files:
        if not item.present:
            entries.append({**item.snapshot(), "status": "absent", "diff": ""})
            continue
        if item.trusted:
            entries.append({**item.snapshot(), "status": "unchanged", "diff": ""})
            continue
        approved = service.approved_source(catalog.root, item.path)
        if approved is None:
            # Two different situations, and a reader needs to know which: a file
            # swe-mux has never seen, versus one whose approved bytes were too large
            # to retain (or predate the retained-snapshot store). The second still
            # means "this changed", it just cannot show how.
            entries.append(
                {
                    **item.snapshot(),
                    "status": "changed, approved bytes not retained"
                    if service.was_approved(catalog.root, item.path)
                    else "never approved",
                    "diff": "",
                }
            )
            continue
        try:
            current = (root / item.path).read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            entries.append({**item.snapshot(), "status": f"unreadable: {exc}", "diff": ""})
            continue
        diff = "".join(
            difflib.unified_diff(
                approved.splitlines(keepends=True),
                current.splitlines(keepends=True),
                fromfile=f"approved/{item.path}",
                tofile=f"current/{item.path}",
                n=3,
            )
        )
        entries.append({**item.snapshot(), "status": "changed", "diff": diff[:MAX_ACTION_DIFF]})
    return json_response({"project_root": catalog.root, "sources": entries})


async def _project_profile_id_for(  # type: ignore[no-untyped-def]
    app: web.Application, project
) -> str:
    """The shell launch profile a Project-owned command should run through."""
    portable = await read_project_config(
        project.root, project=ProjectIdentity(project.id, project.name, project.root, "registered")
    )
    values = portable["values"] if portable["status"] in {"ready", "read-only"} else {}
    return str(
        project.default_profile_id
        or values.get("default_shell_profile")
        or app[keys.CONFIG].default_shell_profile
    )


async def _project_profile_id(request: web.Request, project) -> str:  # type: ignore[no-untyped-def]
    return await _project_profile_id_for(request.app, project)


async def get_project_actions_source(request: web.Request) -> web.Response:
    """The native action file's text, or a starter template when it does not exist."""
    project = _action_project(request)
    return json_response(await asyncio.to_thread(read_actions_source, project.root))


async def put_project_actions_source(request: web.Request) -> web.Response:
    """Validate and save the native action file, then return the fresh catalog.

    Saving changes the file's bytes, so it un-approves itself and the next run asks
    for approval again. That is the trust boundary working as designed and not a
    regression: an editor that could write a command *and* approve it would make the
    approval meaningless. The response carries the catalog so the caller can show the
    new state immediately.
    """
    project = _action_project(request)
    body = await request.json()
    text = body.get("text")
    if not isinstance(text, str):
        raise ValueError({"text": "is required"})
    diagnostics = await asyncio.to_thread(
        write_actions_source, project.root, text, str(body.get("revision") or "missing")
    )
    service: ProjectActionService = request.app[keys.PROJECT_ACTIONS]
    catalog = service.catalog(project.root)
    log.info(
        "project_actions_source_saved project_id=%s bytes=%d actions=%d diagnostics=%d",
        project.id,
        len(text.encode("utf-8")),
        len(catalog.actions),
        len(diagnostics),
    )
    await request.app[keys.EVENTS].emit(
        "project_actions_source_saved",
        source="user",
        project_id=project.id,
        actions=len(catalog.actions),
        diagnostics=len(diagnostics),
    )
    return json_response(
        {
            **await asyncio.to_thread(read_actions_source, project.root),
            "diagnostics": diagnostics,
            "catalog": catalog.snapshot(),
        }
    )


async def _start_project_action(
    app: web.Application,
    project: ProjectRecord,
    action_id: str,
    inputs: dict[str, str],
    *,
    origin: str,
) -> tuple[dict[str, Any], int]:
    """Run one approved action and return its response body and status.

    Shared by the HTTP route and the MCP tool so both go through the same trust
    check, the same substitution, and the same timeout arming. An agent-facing
    caller that reimplemented any of those would be a second authority path.
    """
    service: ProjectActionService = app[keys.PROJECT_ACTIONS]
    catalog, action = service.action(project.root, action_id)
    action = substituted_action(action, inputs, Path(catalog.root))
    profile_id = await _project_profile_id_for(app, project)
    sessions: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for batch in action.batches:
        results = await asyncio.gather(
            *(
                session_routes._spawn_from_body(
                    app,
                    action_spawn_body(
                        step,
                        project_id=project.id,
                        config=app[keys.CONFIG],
                        profile_id=str(profile_id),
                    ),
                )
                for step in batch
            ),
            return_exceptions=True,
        )
        for step, result in zip(batch, results, strict=True):
            if isinstance(result, BaseException):
                errors.append({"step": step.name, "error": str(result)})
                continue
            # Task shells retain their exact spawn argv, so their rail offers an
            # in-place Relaunch. The flag is set post-spawn and republished so
            # every attached client sees it, not only this action's caller.
            result.record.relaunchable = True
            result.publish_update()
            sessions.append(result.record.snapshot())
            if step.timeout_seconds is not None:
                _arm_action_timeout(app, result.record.id, step, project.id, action.id)
    log.info(
        "project_action_started project_id=%s action_id=%s origin=%s sessions=%d failures=%d",
        project.id,
        action.id,
        origin,
        len(sessions),
        len(errors),
    )
    await app[keys.EVENTS].emit(
        "project_action_started",
        source=origin,
        project_id=project.id,
        action_id=action.id,
        action_label=action.label,
        fingerprint=catalog.fingerprint,
        session_ids=[item["id"] for item in sessions],
        failures=len(errors),
    )
    body = {
        "action": action.snapshot(trusted=True),
        "sessions": sessions,
        "errors": errors,
        "inputs": inputs,
    }
    return body, 201 if not errors else 207


def _arm_action_timeout(
    app: web.Application, session_id: str, step: ActionStep, project_id: str, action_id: str
) -> None:
    """Stop this step's session if it is still running when its timeout elapses.

    A timer rather than a supervised loop: it fires once and is done, so restarting
    it on failure (which is what the background-task supervisor does) would be
    wrong. It resolves the session by id at fire time and does nothing if the
    session already ended, so a completed step leaves no trace beyond the timer's
    own wakeup.

    Not restored across a daemon restart. The alternative is persisting a deadline
    per session and reconciling it at adoption, which is real machinery for a bound
    whose purpose is stopping a runaway task on the machine the user is sitting at.
    Stated here rather than left to be discovered.
    """
    seconds = float(step.timeout_seconds or 0)

    async def expire() -> None:
        await asyncio.sleep(seconds)
        sessions = app[keys.SESSIONS]
        session = sessions.sessions.get(session_id)
        # The terminal states by name, not a word that reads like one: `SessionState`
        # has no "ended" member, and a finished one-shot step stays in the table as
        # `exited`. Guarding on the wrong name meant the timer fired an hour after a
        # 20-second step succeeded, reporting a timeout for a task that had already
        # completed and calling stop() on a dead session.
        if session is None or session.record.state in TERMINAL_SESSION_STATES:
            return
        log.warning(
            "project_action_step_timeout project_id=%s action_id=%s step=%s "
            "session_id=%s seconds=%.1f",
            project_id,
            action_id,
            step.name,
            session_id,
            seconds,
        )
        await app[keys.EVENTS].emit(
            "project_action_step_timeout",
            source="project_actions",
            session_id=session_id,
            project_id=project_id,
            action_id=action_id,
            step=step.name,
            timeout_seconds=seconds,
        )
        with contextlib.suppress(KeyError, OSError, RuntimeError):
            await sessions.stop(session_id)

    task = asyncio.create_task(expire(), name=f"action-timeout-{session_id}")
    app[keys.ACTION_TIMEOUT_TASKS].add(task)
    task.add_done_callback(app[keys.ACTION_TIMEOUT_TASKS].discard)


def _action_inputs(body: dict[str, Any]) -> dict[str, str]:
    raw = body.get("inputs")
    if raw is None:
        return {}
    if not isinstance(raw, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in raw.items()
    ):
        raise ValueError({"inputs": "must be a map of input id to string value"})
    return dict(raw)


async def run_project_action(request: web.Request) -> web.Response:
    project = _action_project(request)
    body = await request.json()
    action_id = str(body.get("action_id") or "")
    if not action_id:
        raise ValueError({"action_id": "is required"})
    service: ProjectActionService = request.app[keys.PROJECT_ACTIONS]
    # The lookup is what can raise KeyError for an id nobody declares. Wrapping the
    # whole run in that `except` turned any incidental KeyError inside the spawn path
    # into "unknown Project Action", which is a wrong answer rather than a slow one.
    try:
        service.action(project.root, action_id)
    except PermissionError:
        pass  # Reported below, with the catalog, after the same call inside the run.
    except KeyError as exc:
        raise ValueError(f"unknown Project Action: {action_id}") from exc
    try:
        payload, status = await _start_project_action(
            request.app, project, action_id, _action_inputs(body), origin="user"
        )
    except PermissionError as exc:
        return json_response(
            {
                "error": str(exc),
                "code": "project_actions_trust_required",
                "catalog": service.catalog(project.root).snapshot(),
            },
            409,
        )
    return json_response(payload, status)


async def run_project_init_scripts(request: web.Request) -> web.Response:
    """Run the selected user-authored init scripts inside a Project.

    Each script becomes one visible one-shot terminal at the Project root, started in
    configured order. Start order is all that is promised: a script that must finish
    before the next one begins belongs in the same script, using the shell's own `&&`
    or `;`. Nothing here reads or trusts repository content, so no fingerprint approval
    is involved (see project_init.py).
    """
    project = _action_project(request)
    body = await request.json()
    raw_ids = body.get("script_ids")
    if not isinstance(raw_ids, list) or any(not isinstance(item, str) for item in raw_ids):
        raise ValueError({"script_ids": "must be an array of init script ids"})
    config: Config = request.app[keys.CONFIG]
    chosen, unknown = select_init_scripts(config, [str(item) for item in raw_ids])
    if unknown:
        raise ValueError({"script_ids": f"unknown init scripts: {', '.join(unknown)}"})
    profile_id = await _project_profile_id(request, project)
    sessions: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for script in chosen:
        step = init_script_step(script, root=project.root)
        try:
            session = await session_routes._spawn_from_body(
                request.app,
                action_spawn_body(
                    step, project_id=project.id, config=config, profile_id=profile_id
                ),
            )
        except Exception as exc:  # one failed launch must not strand the rest
            errors.append({"script": script["id"], "error": str(exc)})
            continue
        # Same rationale as a Project Action step: the exact argv is retained, so the
        # terminal rail can offer an in-place Relaunch.
        session.record.relaunchable = True
        session.publish_update()
        sessions.append(session.record.snapshot())
    await request.app[keys.EVENTS].emit(
        "project_init_scripts_started",
        source="user",
        project_id=project.id,
        script_ids=[script["id"] for script in chosen],
        session_ids=[item["id"] for item in sessions],
        failures=len(errors),
    )
    return json_response({"sessions": sessions, "errors": errors}, 201 if not errors else 207)


ROUTES: tuple[web.RouteDef, ...] = (
    web.get("/api/projects/{project_id}/actions", list_project_actions),
    web.get("/api/projects/{project_id}/actions/diff", diff_project_actions),
    web.get("/api/projects/{project_id}/actions/source", get_project_actions_source),
    web.put("/api/projects/{project_id}/actions/source", put_project_actions_source),
    web.post("/api/projects/{project_id}/actions/trust", trust_project_actions),
    web.post("/api/projects/{project_id}/actions/run", run_project_action),
    web.post("/api/projects/{project_id}/init-scripts/run", run_project_init_scripts),
)
