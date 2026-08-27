"""The guided setup session and the options it offers."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from aiohttp import web

from .. import (
    __version__,
)
from .. import (
    app_keys as keys,
)
from ..automation_registry import effective_global_allow
from ..automation_registry import resolve_config as resolve_automation_config
from ..config import Config, update_config
from ..configurator import (
    RAIL_DOMAIN,
    RAIL_PROFILE,
    ConfiguratorService,
    compose_seed_prompt,
    install_mode,
    source_checkout,
)
from ..git_projects import resolve_project
from ..harness import (
    detect_installations_with_versions,
    enabled_backends,
    is_agent_harness,
    resolve_default_harness,
)
from ..http_support import json_response
from ..project_files import (
    PROJECT_CONFIG_FIELDS,
    read_project_config,
    write_project_config,
)
from ..projects import ProjectManager
from ..runtime_config import apply_runtime_config
from ..settings_store import SettingsStore
from . import diagnostics, sessions

log = logging.getLogger(__name__)


# ------------------------------------------------------------- configurator
#
# The configurator agent (`configurator.py`, `design/features/configurator.md`):
# a real harness session pointed at swe-mux itself, launched by one button
# rather than assembled by the operator. Everything below exists to make that
# button a single request - resolve which agent, resolve which Project anchors
# it, compose a prompt that names this machine's actual state, spawn, and mark
# the session as the one holding the configurator tools.


def _project_summaries(app: web.Application) -> list[dict[str, Any]]:
    """Registered Projects, in the cheap shape the manifest wants.

    Deliberately not `_projects_payload`: that one joins history activity and
    per-Project counts, and a configurator reading its inventory needs none of
    it. A capabilities read must not cost a fan-out of history queries.
    """
    manager: ProjectManager = app[keys.PROJECTS]
    return [
        {"id": item.id, "name": item.name, "root": str(item.root)}
        for item in manager.ordered_projects()
    ]


async def _configurator_apply_settings(
    app: web.Application, changes: dict[str, Any]
) -> dict[str, Any]:
    """Apply a settings batch through the same path `PATCH /api/config` uses.

    A refusal comes back as a *result* naming the offending fields rather than as
    an exception, for the same reason the queue's refusals do: the agent needs to
    know whether to adapt the value or stop asking, and an error string it has to
    parse tells it neither. Nothing partial can happen either way - `_validate`
    runs over the whole candidate before anything is written.
    """
    config: Config = app[keys.CONFIG]
    try:
        hot, restart = await asyncio.to_thread(update_config, config, changes)
    except ValueError as exc:
        detail = exc.args[0] if exc.args else {}
        return {
            "applied": False,
            "errors": detail if isinstance(detail, dict) else {"changes": str(exc)},
            "revision": config.revision,
        }
    apply_runtime_config(app, hot)
    # `source` is the provenance the event log keeps, and it is worth being able
    # to tell a configurator-driven change from one a human made in the panel
    # when reading back why a setting moved.
    await app[keys.EVENTS].emit(
        "configuration_changed", source="configurator", changed=sorted(hot | restart)
    )
    log.info(
        "configurator_settings_applied hot=%s restart_required=%s revision=%s",
        sorted(hot),
        sorted(restart),
        config.revision,
    )
    return {
        "applied": True,
        "hot_applied": sorted(hot),
        "restart_required": sorted(restart),
        "revision": config.revision,
    }


async def _configurator_edit_device_settings(
    app: web.Application,
    *,
    profile: str,
    domain: str,
    operations: Any,
    expect_digest: str = "",
) -> dict[str, Any]:
    """Apply path-scoped operations to one per-device settings domain.

    The reason this is a closure over the app rather than a call straight into
    the store: an edit has to emit `settings_changed`, and that event is what
    makes every attached browser refetch its device-settings cache and repaint.
    Without it the write lands on disk and the rail on screen does not move,
    which reads to the operator as a change that did not happen - the worst
    possible outcome for a tool whose whole value is doing the thing they asked.
    """
    store: SettingsStore = app[keys.SETTINGS_STORE]
    target = profile or (RAIL_PROFILE if domain == RAIL_DOMAIN else "desktop")
    result = await asyncio.to_thread(
        store.apply_operations, target, domain, operations, expect_digest
    )
    await app[keys.EVENTS].emit("settings_changed", source="configurator", profile=target)
    log.info(
        "configurator_device_settings_applied profile=%s domain=%s operations=%d",
        target,
        domain,
        len(operations) if isinstance(operations, list) else 0,
    )
    return result


def _configurator_resolve_project(app: web.Application, requested: str) -> Any:
    """A Project by id or exact name, or raise naming what exists.

    Name as well as id because an agent reading a Project *name* out of a rail
    projection and having to translate it back to a UUID to act on it is a
    round-trip that exists only to be got wrong.
    """
    manager: ProjectManager = app[keys.PROJECTS]
    needle = requested.strip()
    if not needle:
        raise ValueError("name a Project: this session is not owned by one")
    if needle in manager.projects:
        return manager.projects[needle]
    matches = [item for item in manager.ordered_projects() if item.name == needle]
    if len(matches) == 1:
        return matches[0]
    known = ", ".join(sorted(item.name for item in manager.ordered_projects())[:20])
    raise ValueError(f"no Project called {needle!r}; registered Projects: {known}")


async def _configurator_project_settings(
    app: web.Application, requested: str
) -> dict[str, Any]:
    """One Project's committed config, with its automation opt-ins resolved.

    The resolution is the part worth serving rather than the file: a consumer
    switched on without its substrate is *inert*, and the raw opt-in map cannot
    say which of the two an empty panel is. `effective` and `blocked` answer that
    directly, from the same resolver the runtime gates on.
    """
    project = _configurator_resolve_project(app, requested)
    stored = await read_project_config(str(project.root))
    values = stored["values"] if stored["status"] in {"ready", "read-only"} else {}
    opt_ins = values.get("automations")
    # The same resolver the runtime gates on, through the same readiness probe,
    # so this can never report a Project as enabled for something the consumers
    # are declining to run.
    readiness = await app[keys.LLM_READY]()
    config = app[keys.CONFIG]
    resolution = resolve_automation_config(
        opt_ins if isinstance(opt_ins, dict) else {},
        llm_ready=readiness.ready,
        global_allow=effective_global_allow(
            config.automation_global_allow,
            scan_timeline_enabled=config.scan_timeline_enabled,
        ),
    )
    return {
        "project": {"id": project.id, "name": project.name, "root": str(project.root)},
        "path": stored["path"],
        "exists": stored["exists"],
        "status": stored["status"],
        "revision": stored["revision"],
        "values": values,
        "error": stored.get("error"),
        "automations": {
            "requested": sorted(
                key for key, value in (opt_ins or {}).items() if value
            ) if isinstance(opt_ins, dict) else [],
            "effective": sorted(resolution.enabled),
            "blocked": {key: list(value) for key, value in resolution.blocked.items()},
            "unverified": sorted(resolution.unverified),
            "globally_disabled": sorted(resolution.globally_disabled),
            "note": (
                "`blocked` names dependencies that are still off: a consumer "
                "there is inert rather than broken. `unverified` is held back by "
                "something outside the graph (an unproven model provider), which "
                "no automation opt-in can fix. `globally_disabled` is turned off "
                "by the install-wide ceiling, which only Automation policy can "
                "lift."
            ),
        },
        "editable_fields": sorted(PROJECT_CONFIG_FIELDS),
        "committed": (
            "This file is committed to the repository. A change here reaches "
            "everyone who clones it."
        ),
    }


async def _configurator_apply_project_settings(
    app: web.Application, *, project: str, changes: dict[str, Any]
) -> dict[str, Any]:
    """Merge `changes` into one Project's committed config, or change nothing.

    Merged rather than replaced, and validated by `write_project_config`, which
    is the same revision-guarded path `PUT /api/project/config` takes: the closed
    field set refuses the daemon-authority keys outright, so a repository can
    never be talked into setting this daemon's bind address or the command a
    harness runs.
    """
    record = _configurator_resolve_project(app, project)
    stored = await read_project_config(str(record.root))
    if stored["status"] == "malformed":
        return {
            "applied": False,
            "errors": {"file": f"the existing file cannot be parsed: {stored.get('error')}"},
            "path": stored["path"],
        }
    merged = {**(stored["values"] or {}), **changes}
    try:
        result = await write_project_config(
            str(record.root), merged, str(stored["revision"])
        )
    except ValueError as exc:
        detail = exc.args[0] if exc.args else str(exc)
        return {
            "applied": False,
            "errors": detail if isinstance(detail, dict) else {"changes": str(detail)},
            "path": stored["path"],
        }
    identity = await resolve_project(result["project"]["root"])
    await app[keys.HISTORY].register_project_scope(identity)
    await app[keys.EVENTS].emit(
        "project_configuration_changed", source="configurator", project_id=record.id
    )
    log.info(
        "configurator_project_settings_applied project=%s fields=%s",
        record.id,
        sorted(changes),
    )
    return {
        "applied": True,
        "changed": sorted(changes),
        "path": result["path"],
        "revision": result["revision"],
        "values": result["values"],
    }


def build_configurator_service(app: web.Application) -> ConfiguratorService:
    """Wire the configurator's tools to the daemon's own operations.

    Closures over this application rather than the application itself, matching
    `action_runner`: `configurator.py` stays free of the HTTP layer and testable
    with three stubs, while every call it makes lands in the same implementation
    the browser reaches.
    """
    config: Config = app[keys.CONFIG]
    return ConfiguratorService(
        config=config,
        projects=lambda: _project_summaries(app),
        installations=lambda: detect_installations_with_versions(dict(config.harness_exe)),
        diagnostics=lambda: diagnostics._doctor_report(app),
        apply_settings=lambda changes: _configurator_apply_settings(app, changes),
        # Read straight from the store (a plain file store, no HTTP layer) but
        # write through a closure, because a write has to emit the event that
        # repaints every attached browser.
        settings_store=lambda: app[keys.SETTINGS_STORE],
        edit_device_settings=lambda **kwargs: _configurator_edit_device_settings(app, **kwargs),
        read_project_settings=lambda project: _configurator_project_settings(app, project),
        apply_project_settings=lambda **kwargs: _configurator_apply_project_settings(
            app, **kwargs
        ),
        version=__version__,
    )


def _configurator_candidates(config: Config) -> tuple[str, ...]:
    """Agent harnesses this machine can launch a configurator into."""
    return tuple(
        name
        for name in enabled_backends(dict(config.harness_enabled), dict(config.harness_exe))
        if is_agent_harness(name)
    )


def _configurator_harness(config: Config, requested: str, candidates: Sequence[str]) -> str | None:
    """Which agent to launch, honouring an explicit ask over every default."""
    if requested:
        return requested if requested in candidates else None
    return resolve_default_harness(
        preferences=(config.default_harness, config.default_backend), available=candidates
    )


def _configurator_project(app: web.Application, requested: str) -> Any:
    """The Project the configurator session is anchored to.

    A session must belong to a Project - that is what gives it a working
    directory, a scope, and a place in the sidebar - so this picks one rather
    than inventing one. The order matters: an explicit ask wins, then the Project
    that *is* this swe-mux checkout when the daemon runs from source (so a
    maintainer's configurator lands where swe-mux's own code is, which is the
    only place code changes are possible), then simply the first Project.
    """
    manager: ProjectManager = app[keys.PROJECTS]
    if requested and requested in manager.projects:
        return manager.projects[requested]
    ordered = manager.ordered_projects()
    checkout = source_checkout()
    if checkout is not None:
        for item in ordered:
            try:
                if Path(item.root).resolve() == checkout.resolve():
                    return item
            except OSError:
                continue
    return ordered[0] if ordered else None


async def configurator_options(request: web.Request) -> web.Response:
    """What the launcher can offer, so the button knows before it is pressed.

    Detection runs off the loop and includes CLI version probes, which is why
    this is its own request rather than something the button recomputes: the
    frontend asks once when the surface opens and renders a disabled control
    with a reason rather than a control that fails when clicked.
    """
    config: Config = request.app[keys.CONFIG]
    candidates = await asyncio.to_thread(_configurator_candidates, config)
    manager: ProjectManager = request.app[keys.PROJECTS]
    return json_response(
        {
            "harnesses": list(candidates),
            "default_harness": _configurator_harness(config, "", candidates),
            "configured_default": config.default_harness,
            "install_mode": install_mode(),
            "source_checkout": str(source_checkout() or ""),
            "projects": len(manager.projects),
        }
    )


async def launch_configurator(request: web.Request) -> web.Response:
    """Spawn a configurator session and run its opening prompt.

    `seed_text` rather than `stage_text`: the human pressed a button whose label
    says it starts a conversation about their install, so the opening turn is the
    thing they asked for and leaving it sitting unsent in a composer would be a
    worse answer to the same press. Nothing it says in that turn changes
    anything - the one write in its toolset is a separate, explicit call.

    The `configurator` marker is set after the spawn and republished, the same
    way a Project Action's `relaunchable` is: the spawn path takes a
    `SpawnRequest`, and deliberately has no field for this (see
    `SessionRecord.configurator`), so no request an agent can compose reaches it.
    """
    body = await request.json() if request.can_read_body else {}
    config: Config = request.app[keys.CONFIG]
    candidates = await asyncio.to_thread(_configurator_candidates, config)
    requested = str(body.get("harness") or "").strip()
    harness = _configurator_harness(config, requested, candidates)
    if harness is None:
        return json_response(
            {
                "error": (
                    f"{requested} is not an available agent harness"
                    if requested
                    else "no agent harness is installed and enabled on this machine"
                ),
                "code": "no_harness",
                "candidates": list(candidates),
            },
            409,
        )
    project = _configurator_project(request.app, str(body.get("project_id") or "").strip())
    if project is None:
        return json_response(
            {
                "error": (
                    "the configurator runs inside a Project, and none is registered yet; "
                    "add one first"
                ),
                "code": "no_project",
            },
            409,
        )
    installations = await asyncio.to_thread(
        detect_installations_with_versions, dict(config.harness_exe)
    )
    prompt = await asyncio.to_thread(
        compose_seed_prompt,
        config,
        harness=harness,
        cwd=str(project.root),
        installations=installations,
        projects=_project_summaries(request.app),
        doctor_summary=await _configurator_health_preview(request.app),
        version=__version__,
        project_name=project.name,
        project_id=project.id,
    )
    session = await sessions._spawn_from_body(
        request.app,
        {
            "project_id": project.id,
            "backend": harness,
            "name": "configurator",
            "seed_text": prompt,
        },
    )
    session.record.configurator = True
    session.publish_update()
    await request.app[keys.EVENTS].emit(
        "configurator_launched",
        source="user",
        session_id=session.record.id,
        backend=harness,
        project_id=project.id,
        install_mode=install_mode(),
    )
    log.info(
        "configurator_launched session=%s backend=%s project=%s mode=%s",
        session.record.id,
        harness,
        project.id,
        install_mode(),
    )
    # Exactly the body `POST /api/sessions` answers with, deliberately: the
    # browser places a new session into a pane itself, and a launcher that
    # returned a shape of its own would need a second placement path that drifts
    # from the one every other launch uses. The record already carries
    # `configurator: true`, so the caller can tell what it got without a wrapper.
    return json_response(session.record.snapshot(), 201)


#: How long the launch waits for a health summary before starting without one.
#: The full report inspects the firewall and probes CLI versions, and a button
#: press must not sit on either: the summary is a *nicety* in the opening turn,
#: and the agent can fetch the real report at any moment. Degrading is therefore
#: strictly better than a slow launch, and the fallback line says where to look.
CONFIGURATOR_HEALTH_BUDGET_SECONDS = 3.0


async def _configurator_health_preview(app: web.Application) -> str:
    """One sentence of health for the seed prompt, or nothing within the budget."""
    try:
        report = await asyncio.wait_for(
            diagnostics._doctor_report(app), CONFIGURATOR_HEALTH_BUDGET_SECONDS
        )
    except Exception as exc:  # noqa: BLE001 - a nicety never fails a launch
        log.info("configurator_health_preview_skipped error_type=%s", type(exc).__name__)
        return ""
    return _configurator_health_line(report)


def _configurator_health_line(report: dict[str, Any]) -> str:
    """One sentence of health for the seed prompt, or an empty string.

    A count and the worst few titles, never the whole report. The prompt's job is
    to make the agent *look*, and pasting a full diagnostic into it would both
    bloat the opening turn and freeze a snapshot into the transcript that the
    tool can answer freshly at any moment.
    """
    checks = report.get("checks")
    if not isinstance(checks, list):
        return ""
    failing = [
        check
        for check in checks
        if isinstance(check, dict) and check.get("status") in {"warn", "fail"}
    ]
    if not failing:
        return "Health report: every check passes right now."
    critical = [check for check in failing if check.get("severity") == "critical"]
    worst = (critical or failing)[:3]
    titles = "; ".join(str(check.get("title") or check.get("id") or "?") for check in worst)
    return (
        f"Health report: {len(failing)} check(s) are not clean"
        f"{f', {len(critical)} critical' if critical else ''} - {titles}. "
        "Call `configurator_diagnostics` for the current detail before acting on this."
    )


ROUTES: tuple[web.RouteDef, ...] = (
    web.get("/api/configurator/options", configurator_options),
    web.post("/api/configurator/launch", launch_configurator),
)
