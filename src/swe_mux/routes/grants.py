"""The one additive write behind every gate notice."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from aiohttp import web

from .. import (
    app_keys as keys,
)
from ..automation_registry import (
    AUTONOMY_PROJECT_AUTOMATIONS,
    LLM_PROJECT_AUTOMATIONS,
    RECOMMENDED_PROJECT_AUTOMATIONS,
)
from ..automation_registry import REGISTRY as AUTOMATION_REGISTRY
from ..config import Config, update_config
from ..grants import (
    AUTONOMY_PROJECT_VALUES,
    GRANTABLE_INSTALL_KEYS,
    GRANTABLE_PROJECT_VALUES,
    LLM_PROJECT_VALUES,
    GrantRefusal,
    plan_grant,
    project_values_after,
)
from ..http_support import json_response
from ..project_context import ProjectContext
from ..project_files import (
    read_project_config,
    write_project_config,
)
from ..runtime_config import apply_runtime_config
from . import automation
from .support import _registered_identity

log = logging.getLogger(__name__)


async def describe_grants(request: web.Request) -> web.Response:
    """What a gate is allowed to switch on. Read-only, and the contract both ends check.

    The browser holds its own catalogue of gates (`frontend/src/grants.ts`) because a
    gate has to render its disclosure before any request is made. This read is what
    stops the two copies drifting: a test asserts every grant the browser can offer is
    one the daemon will accept, so a renamed switch fails a test instead of failing at
    the click - the same rule `settingTargets.test.ts` already applies to deep links.
    """
    config: Config = request.app[keys.CONFIG]
    return json_response(
        {
            "install": sorted(GRANTABLE_INSTALL_KEYS),
            "values": {
                key: list(allowed) for key, allowed in sorted(GRANTABLE_PROJECT_VALUES.items())
            },
            # With the config threaded in, each entry carries `globally_allowed`
            # so the creation form and every gate can grey a set the install-wide
            # ceiling blocks instead of offering a grant the daemon will refuse.
            "automations": automation._automation_registry_payload(config),
            # The install's inherited default template as *stored*, which the
            # creation form needs beside each entry's resolved `install_default`
            # for one reason: an id the operator explicitly defaulted off and an
            # id the install has never had an opinion about both resolve to
            # `false`, and only the second may be pre-ticked on a new Project.
            "project_defaults": dict(config.automation_project_defaults),
            "recommended_project_automations": list(RECOMMENDED_PROJECT_AUTOMATIONS),
            # The named starting sets the create form offers as checkboxes. Served
            # rather than restated in the browser so the form and the daemon cannot
            # drift; each applies through the ordinary POST above, dependency closure
            # and audit record included.
            "project_starting_sets": {
                "recommended": {
                    "automations": list(RECOMMENDED_PROJECT_AUTOMATIONS),
                    "values": {},
                },
                "llm": {
                    "automations": list(LLM_PROJECT_AUTOMATIONS),
                    "values": dict(LLM_PROJECT_VALUES),
                },
                "autonomy": {
                    "automations": list(AUTONOMY_PROJECT_AUTOMATIONS),
                    "values": dict(AUTONOMY_PROJECT_VALUES),
                },
            },
            # So a gate can disclose "and this needs a model provider you have not
            # proven yet" before the press, from the same read that tells it what
            # it may grant at all.
            "llm": (await automation._llm_readiness(request)).as_dict(),
        }
    )


async def apply_grants(request: web.Request) -> web.Response:
    """Turn things on from the surface that cannot work without them.

    The one write behind every gate notice in the app. A gate states what is off, what
    turning it on would do, and offers this - which is the Land queue's verification
    approval generalised: a deliberate act, made where the block is, recorded once.

    Three properties are what make a write reachable from a drawer pane safe:

    - **Additive only.** `grants.plan_grant` refuses anything but "on", so no surface
      but the owning editor can take a permission away. Many granters, one owner.
    - **Allowlisted.** Only `GRANTABLE_INSTALL_KEYS` and `GRANTABLE_PROJECT_VALUES`,
      both checked against `Config`/`project_files` at import.
    - **Project first, then install.** The Project write is the one that can fail (a
      stale revision, a read-only checkout, a malformed file), so it goes first and a
      failure leaves nothing applied. The install write is validated `Config` and
      effectively cannot; if it somehow does, the response still names what landed.
      Rolling a Project file back would be a second write that can fail in turn.
    """
    body = await request.json()
    if not isinstance(body, dict):
        raise ValueError("grant request body must be an object")
    config: Config = request.app[keys.CONFIG]
    install_request = body.get("install") or {}
    if not isinstance(install_request, dict):
        raise ValueError("install must be a table of switches")
    automations_request = body.get("automations") or []
    if not isinstance(automations_request, list) or not all(
        isinstance(item, str) for item in automations_request
    ):
        raise ValueError("automations must be a list of automation ids")
    values_request = body.get("values") or {}
    if not isinstance(values_request, dict):
        raise ValueError("values must be a table of Project fields")

    project = None
    project_config: dict[str, Any] | None = None
    current_automations: dict[str, bool] = {}
    current_values: dict[str, Any] = {}
    if automations_request or values_request:
        project_id = str(body.get("project_id") or "")
        project = request.app[keys.PROJECTS].projects.get(project_id)
        if project is None:
            raise ValueError("a Project grant needs a known project_id")
        project_config = await read_project_config(
            project.root, project=_registered_identity(project)
        )
        if project_config["status"] == "malformed":
            return json_response(
                {
                    "error": "this Project's .swe-mux/config.toml could not be parsed",
                    "code": "project_config_malformed",
                },
                409,
            )
        if project_config["status"] == "read-only":
            return json_response(
                {
                    "error": "this Project's .swe-mux/config.toml is read-only",
                    "code": "project_config_read_only",
                },
                409,
            )
        current_values = dict(project_config["values"])
        current_automations = {
            key: bool(value)
            for key, value in (current_values.get("automations") or {}).items()
            if key in AUTOMATION_REGISTRY
        }

    try:
        plan = plan_grant(
            install=install_request,
            automations=automations_request,
            values=values_request,
            current_install={
                key: getattr(config, key, None) for key in GRANTABLE_INSTALL_KEYS
            },
            current_automations=current_automations,
            current_values=current_values,
            global_allow=automation._global_allow(config),
            project_defaults=automation._project_defaults(config),
        )
    except GrantRefusal as refusal:
        return json_response({"error": refusal.message, "code": refusal.code}, 409)

    applied_automations = sorted(plan.automations)
    applied_values = sorted(plan.values)
    resumed_lands: list[dict[str, Any]] = []
    if project is not None and project_config is not None and (plan.automations or plan.values):
        merged = project_values_after(current_values, plan, current_automations)
        try:
            await write_project_config(
                project.root,
                merged,
                str(body.get("revision") or project_config["revision"]),
                project=_registered_identity(project),
            )
        except ValueError as exc:
            if "changed externally" in str(exc):
                return json_response({"error": str(exc), "code": "revision_conflict"}, 409)
            raise
        if gate_cache := request.app.get(keys.AUTOMATION_GATE_CACHE):
            gate_cache.clear()
        contexts = request.app.get(keys.PROJECT_CONTEXTS)
        if "scan_timeline" in plan.automations and contexts is not None:
            # Parity with the registry's own write: permitting the timeline creates the
            # blank Project context file the scans read, so the first scan is not the
            # thing that discovers it is missing.
            await asyncio.to_thread(
                request.app[keys.PROJECT_CONTEXTS].ensure,
                ProjectContext(project_id=project.id, project_root=project.root),
            )
        await request.app[keys.EVENTS].emit(
            "project_configuration_changed", project_id=project.id
        )
        land_queue = request.app.get(keys.LAND_QUEUE)
        if "land_verify_grant" in plan.values and land_queue is not None:
            # Raising this clears every verification block whose bytes this machine
            # wrote, so the lands those blocks refused are waiting on nothing. Same act,
            # same consequence as approving the bytes one at a time - and a grant that
            # cleared the block while leaving the queue empty would be the defect the
            # approve route was just fixed for, reappearing one control over.
            # `trusted_only`, because the grant does not reach a gate somebody else
            # wrote: resuming those would queue a land that is about to refuse again.
            resumed_lands = await land_queue.resume_verification_blocked(
                project_id=project.id, project_root=project.root, trusted_only=True
            )

    applied_install: list[str] = []
    if plan.install:
        hot, restart = update_config(config, dict(plan.install))
        applied_install = sorted(plan.install)
        apply_runtime_config(request.app, hot)
        await request.app[keys.EVENTS].emit(
            "configuration_changed", source="grant", changed=sorted(hot | restart)
        )

    if not plan.empty:
        # One audit record for the whole act, the way an approved verification command
        # leaves exactly one `land_verify_approved`. Without it a permission raised from
        # a drawer pane would be indistinguishable, afterwards, from one that was
        # always on.
        await request.app[keys.EVENTS].emit(
            "grant_applied",
            source="user",
            project_id=project.id if project is not None else None,
            keys=plan.audit_keys(),
            spends=plan.spends,
        )
        log.info(
            "grant applied project_id=%s keys=%s spends=%s",
            project.id if project is not None else "-",
            ",".join(plan.audit_keys()),
            plan.spends,
        )

    result: dict[str, Any] = {
        "applied": {
            "install": applied_install,
            "automations": applied_automations,
            "values": applied_values,
        },
        "spends": plan.spends,
        # Reported alongside the verdict rather than instead of it: the grant did
        # land, and the switch is still inert until a provider is proven. A gate
        # that reported only success would hand back exactly the enabled-and-does-
        # nothing state the whole enablement design exists to prevent.
        "needs_llm": plan.needs_llm,
        "llm": (await automation._llm_readiness(request)).as_dict(),
        "config": config.public_dict(),
        # What raising an authority *started*, as opposed to what it permitted. Only
        # `land_verify_grant` produces any today; an empty list is the ordinary answer
        # and is not the same as the key being absent.
        "resumed_lands": [
            {"id": row["id"], "branch": row["branch"], "kind": row.get("kind") or "land"}
            for row in resumed_lands
        ],
    }
    if project is not None:
        result["project"] = {
            **await automation._project_automation_state(
                project,
                llm=await automation._llm_readiness(request),
                global_allow=automation._global_allow(config),
                # Threaded, unlike before: without it this answer resolves the
                # Project against the registry's defaults rather than the
                # install's, so a gate press would report back a state the
                # daemon does not agree with.
                install=config,
            ),
            "automations": automation._automation_registry_payload(config),
        }
    return json_response(result)


ROUTES: tuple[web.RouteDef, ...] = (
    # The one write behind every gate notice. Additive by construction, so it
    # is safe to reach from a drawer pane; see `grants.py`.
    web.post("/api/grants", apply_grants),
    web.get("/api/grants", describe_grants),
)
