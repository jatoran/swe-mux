"""Setup progress and bounded, read-only discovery of native project history."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

from aiohttp import web

from .. import app_keys as keys
from .. import model_setup, onboarding
from ..config import Config, update_config
from ..git_projects import _git, resolve_project
from ..harness import HARNESSES
from ..http_support import json_response
from ..llm_endpoint import resolve_endpoint
from ..openrouter import OpenRouterError
from ..reconcile import ExternalTranscript, scan_external_transcripts_async
from ..runtime_config import apply_runtime_config

log = logging.getLogger(__name__)


async def get_onboarding(request: web.Request) -> web.Response:
    return json_response(onboarding.read_state(request.app[keys.CONFIG]))


async def patch_onboarding(request: web.Request) -> web.Response:
    config: Config = request.app[keys.CONFIG]
    body = await request.json()
    if not isinstance(body, dict) or type(body.get("revision")) is not int:
        return json_response({"error": "onboarding revision is required"}, 422)
    revision = body.pop("revision")
    action = body.pop("action", "progress")
    if action not in {"progress", "restart", "fresh", "reuse"}:
        return json_response({"error": "unknown setup action"}, 422)
    current = onboarding.read_state(config)
    if revision != current["revision"]:
        return json_response(
            {"error": "Setup changed on another device. Reload and try again.", "state": current},
            409,
        )
    backup = None
    restart: set[str] = set()
    try:
        onboarding.validate_patch(body)
        if action == "restart":
            body.update(step="existing", status="active", hidden=False)
        elif action == "reuse":
            body.update(step="finish", status="active", hidden=False)
        elif action == "fresh":
            backup = onboarding.backup_preferences(config)
            defaults = Config(data_dir=config.data_dir)
            # Keep the connection and process identity stable while clients are
            # attached. Fresh preferences never mutate repositories or accounts.
            keep = {
                "schema_version",
                "revision",
                "token",
                "data_dir",
                "config_path",
                "port",
                "host",
                "tailnet_enabled",
                "tailnet_ip",
                "shell_profiles",
                "default_shell_profile",
                "pty_supervisor_enabled",
            }
            changes = {
                name: getattr(defaults, name)
                for name in Config.__dataclass_fields__
                if name not in keep
            }
            hot, restart = update_config(config, changes)
            apply_runtime_config(request.app, hot)
            from ..keymaps import DEFAULT_PRESET, default_rules
            from . import settings

            settings._publish_keybindings(
                config, settings._stage_keybindings(config, DEFAULT_PRESET, default_rules())
            )
            body.update(
                step="experience",
                status="active",
                hidden=False,
                draft={},
                dismissed=[],
                completed=[],
                tour_status="pending",
                tour_step="welcome",
            )
        state = onboarding.change_state(config, body, revision)
        if state["status"] == "complete" and not config.harness_setup_complete:
            update_config(config, {"harness_setup_complete": True})
    except ValueError as exc:
        return json_response({"error": str(exc)}, 422)
    await request.app[keys.EVENTS].emit(
        "onboarding_changed", revision=state["revision"], action=action
    )
    if action == "fresh" or state["status"] == "complete":
        await request.app[keys.EVENTS].emit("configuration_changed", source="onboarding")
    return json_response(
        {**state, "backup": str(backup) if backup else None, "restart_required": sorted(restart)}
    )


async def project_candidates(items: list[ExternalTranscript]) -> list[dict[str, Any]]:
    """Aggregate paths before bounded Git probes; never register or import them."""
    paths: dict[str, dict[str, Any]] = {}
    for item in items:
        if not item.cwd or not Path(item.cwd).is_absolute():
            continue
        root = Path(item.cwd).resolve()
        key = os.path.normcase(str(root))
        row = paths.setdefault(
            key, {"root": str(root), "last_activity": 0.0, "sessions": 0, "harnesses": set()}
        )
        row["last_activity"] = max(row["last_activity"], item.created_at, item.mtime_ns / 1e9)
        row["sessions"] += 1
        row["harnesses"].add(item.backend)
    semaphore = asyncio.Semaphore(4)

    async def resolve(row: dict[str, Any]) -> dict[str, Any]:
        async with semaphore:
            root = Path(row["root"])
            if root.is_dir():
                identity = await resolve_project(root)
                root = Path(identity.root)
                # The common .git directory belongs to the main checkout, even
                # when the recorded cwd is inside a linked worktree.
                common = await _git(root, "rev-parse", "--path-format=absolute", "--git-common-dir")
                if common and Path(common).name == ".git":
                    root = Path(common).parent
            return {**row, "root": str(root), "name": root.name, "available": root.is_dir()}

    recent = sorted(paths.values(), key=lambda row: row["last_activity"], reverse=True)[:200]
    resolved = await asyncio.gather(*(resolve(row) for row in recent))
    grouped: dict[str, dict[str, Any]] = {}
    for row in resolved:
        key = os.path.normcase(row["root"])
        if key in grouped:
            found = grouped[key]
            found["last_activity"] = max(found["last_activity"], row["last_activity"])
            found["sessions"] += row["sessions"]
            found["harnesses"].update(row["harnesses"])
        else:
            grouped[key] = row
    return [
        {**row, "harnesses": sorted(row["harnesses"])}
        for row in sorted(grouped.values(), key=lambda row: row["last_activity"], reverse=True)
    ]


async def discover_projects(request: web.Request) -> web.Response:
    requested = request.query.get("harnesses", "").split(",")
    backends = [name for name in requested if name]
    if not backends or any(name not in HARNESSES for name in backends):
        return json_response({"error": "Select at least one registered harness to scan."}, 422)
    started = time.monotonic()
    try:
        async with asyncio.timeout(45):
            items = await scan_external_transcripts_async(backends=backends, limit=2000)
            candidates = await project_candidates(items)
    except TimeoutError:
        log.warning(
            "onboarding project discovery timed out", extra={"harnesses": ",".join(backends)}
        )
        return json_response(
            {
                "error": (
                    "History discovery took too long. Add a folder manually or retry "
                    "with fewer harnesses."
                )
            },
            504,
        )
    log.info(
        "onboarding projects discovered",
        extra={
            "harnesses": ",".join(backends),
            "candidates": len(candidates),
            "elapsed_ms": round((time.monotonic() - started) * 1000),
        },
    )
    return json_response(
        {"items": candidates, "limited": len(items) >= 2000 or len(candidates) >= 200}
    )


async def verify_models(request: web.Request) -> web.Response:
    """Explicit, bounded probes of the actual model roles, including tool output.

    No tool is executed. Unlike endpoint verification this proves the response
    shapes the enabled features require, not merely HTTP reachability.
    """
    from . import automation

    config: Config = request.app[keys.CONFIG]
    ready = await automation._llm_readiness(request)
    if not ready.ready or ready.code == "unknown":
        return json_response({"error": ready.reason}, 409)
    endpoint = resolve_endpoint(config, request.app.get(keys.LLM_CAPABILITIES))
    provider = request.app[keys.OPENROUTER]
    secrets = request.app[keys.SECRET_STORE]
    digest = model_setup.fingerprint(config, endpoint, secrets.get(endpoint.secret_name))
    models = model_setup.role_models(config, endpoint)
    if not all(models.values()):
        return json_response({"error": "Choose a model for every required role."}, 422)
    schema = {
        "type": "object",
        "properties": {"ok": {"type": "boolean"}},
        "required": ["ok"],
        "additionalProperties": False,
    }
    checks = []
    try:
        for model in dict.fromkeys(model for role, model in models.items() if role != "assistant"):
            result = await provider.complete_json(
                model=model,
                messages=[{"role": "user", "content": 'Return the JSON object {"ok":true}.'}],
                schema_name="setup_check",
                schema=schema,
                max_tokens=512,
            )
            if result.value.get("ok") is not True:
                raise ValueError(f"{model} did not return the requested structured result.")
            checks.append(
                {"model": model, "check": "structured output", "cost_usd": result.cost_usd}
            )
        turn = await provider.complete_tools(
            model=models["assistant"],
            messages=[
                {
                    "role": "user",
                    "content": (
                        "Call setup_check with ok=true. This is a capability "
                        "test; no tool will execute."
                    ),
                }
            ],
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "setup_check",
                        "description": "Verify tool support",
                        "parameters": schema,
                    },
                }
            ],
            max_tokens=512,
        )
        if not any(
            isinstance(call.get("function"), dict)
            and isinstance(call["function"].get("arguments"), str)
            and call.get("function", {}).get("name") == "setup_check"
            and json.loads(call.get("function", {}).get("arguments", "{}")) == {"ok": True}
            for call in turn.tool_calls
        ):
            raise ValueError(f"{models['assistant']} did not produce the requested tool call.")
        checks.append(
            {"model": models["assistant"], "check": "tool calling", "cost_usd": turn.cost_usd}
        )
    except (OpenRouterError, ValueError) as exc:
        log.warning("onboarding model verification failed: %s", exc)
        return json_response({"error": str(exc), "checks": checks}, 422)
    current_endpoint = resolve_endpoint(config, request.app.get(keys.LLM_CAPABILITIES))
    if digest != model_setup.fingerprint(
        config, current_endpoint, secrets.get(current_endpoint.secret_name)
    ):
        return json_response(
            {"error": "Model settings changed during verification. Review and test again."}, 409
        )
    model_setup.record_verification(config, digest, checks)
    log.info(
        "onboarding model roles verified",
        extra={"models": ",".join(sorted(set(models.values()))), "calls": len(checks)},
    )
    return json_response({"ok": True, "checks": checks})


ROUTES = (
    web.get("/api/onboarding", get_onboarding),
    web.patch("/api/onboarding", patch_onboarding),
    web.get("/api/onboarding/projects", discover_projects),
    web.post("/api/onboarding/models/verify", verify_models),
)
