"""Install-wide configuration: the config file, keybindings, hooks, UI settings."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiohttp import web

from .. import (
    app_keys as keys,
)
from ..config import Config, update_config
from ..http_support import json_response
from ..keybindings import (
    DEFAULT_KEYBINDINGS,
    KEYBINDING_COMMANDS,
    KEYBINDINGS_FILE_VERSION,
    V2_DEFAULT_KEYBINDINGS,
    keybinding_policy,
    normalize_binding,
)
from ..meta_hooks import MetaHookEngine, parse_hook_rules
from ..profiles import profile_payload
from ..project_files import (
    read_project_config,
)
from ..runtime_config import apply_runtime_config
from . import automation, projects

log = logging.getLogger(__name__)


async def get_config(request: web.Request) -> web.Response:
    config: Config = request.app[keys.CONFIG]
    response = json_response(config.public_dict())
    response.headers["ETag"] = f'"{config.revision}"'
    return response


async def settings_bundle(request: web.Request) -> web.Response:
    """Everything the Settings panel needs on open, in one round trip.

    The panel used to fan out nine GETs; each answered in well under 50ms, but
    on a high-RTT client (phone over Tailscale) connection setup and RTT per
    request dominated the perceived open delay. `config` is the one part the
    panel cannot render without, so its failure fails the request; every other
    part degrades to null with the reason under `errors`, and the client
    decides which missing parts it can tolerate.
    """
    config: Config = request.app[keys.CONFIG]
    cwd = request.query.get("cwd")
    parts: dict[str, Any] = {}
    errors: dict[str, str] = {}

    async def part(key: str, factory: Callable[[], Awaitable[Any]]) -> None:
        try:
            parts[key] = await factory()
        except Exception as exc:  # noqa: BLE001 — each part degrades independently
            parts[key] = None
            errors[key] = str(exc)

    async def keybindings() -> Any:
        return _keybindings_payload(config)

    async def profiles() -> Any:
        # Shell detection stats a handful of executables; keep it off the loop.
        return await asyncio.to_thread(profile_payload, config)

    async def usage() -> Any:
        return request.app[keys.USAGE].snapshot()

    async def project_config() -> Any:
        return await read_project_config(cwd) if cwd else None

    await asyncio.gather(
        part("keybindings", keybindings),
        part("profiles", profiles),
        part("projects", lambda: projects._projects_payload(request)),
        part("automation", lambda: automation._automation_status_payload(request)),
        part("provider", lambda: automation._provider_status(request)),
        part("usage", usage),
        part("project_config", project_config),
    )
    return json_response({"config": config.public_dict(), **parts, "errors": errors})


async def patch_config(request: web.Request) -> web.Response:
    config: Config = request.app[keys.CONFIG]
    supplied = request.headers.get("If-Match", "").strip('"')
    if supplied and supplied != str(config.revision):
        return json_response(
            {"error": "configuration changed externally", "revision": config.revision}, 409
        )
    body = await request.json()
    body_revision = body.pop("_revision", None)
    if body_revision is not None and int(body_revision) != config.revision:
        return json_response(
            {"error": "configuration changed externally", "revision": config.revision}, 409
        )
    try:
        hot, restart = update_config(config, body)
    except ValueError as exc:
        detail = exc.args[0]
        return json_response(
            {
                "error": "invalid configuration",
                "fields": detail if isinstance(detail, dict) else {},
            },
            422,
        )
    apply_runtime_config(request.app, hot)
    await request.app[keys.EVENTS].emit(
        "configuration_changed", source="settings", changed=sorted(hot | restart)
    )
    response = json_response(
        {**config.public_dict(), "hot_applied": sorted(hot), "restart_required": sorted(restart)}
    )
    response.headers["ETag"] = f'"{config.revision}"'
    return response


async def reset_config(request: web.Request) -> web.Response:
    config: Config = request.app[keys.CONFIG]
    defaults = Config(data_dir=config.data_dir)
    fields = {
        key: getattr(defaults, key)
        for key in Config.__dataclass_fields__
        if key
        not in {
            "schema_version",
            "revision",
            "token",
            "data_dir",
            "config_path",
            "shell_profiles",
            "default_shell_profile",
        }
    }
    hot, restart = update_config(config, fields)
    await request.app[keys.EVENTS].emit("configuration_changed", source="settings", reset=True)
    return json_response(
        {**config.public_dict(), "hot_applied": sorted(hot), "restart_required": sorted(restart)}
    )


def _keybindings_payload(config: Config) -> dict[str, Any]:
    defaults = dict(DEFAULT_KEYBINDINGS)
    path = config.data_dir / "keybindings.json"
    rejected: dict[str, str] = {}
    if path.exists():
        try:
            supplied = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid keybindings.json: {exc}") from exc
        replace_defaults = bool(
            isinstance(supplied, dict) and supplied.get("replace_defaults") is True
        )
        document_version = (
            int(supplied.get("version", 1))
            if replace_defaults and isinstance(supplied.get("version", 1), int)
            else 1
        )
        if replace_defaults:
            supplied = supplied.get("bindings", {})
            defaults = {}
        if not isinstance(supplied, dict):
            raise ValueError("keybindings.json must contain an object")
        for chord, command in supplied.items():
            try:
                key, command_id = normalize_binding(chord, command)
                defaults[key] = command_id
            except ValueError as exc:
                rejected[str(chord)] = str(exc)
        # Version 1 could not contain these chords through the Settings/API path:
        # both were rejected as browser-reserved. Seed the new desktop defaults
        # once, while a version 2 document continues to preserve an intentional
        # clear or remap.
        if replace_defaults and document_version < KEYBINDINGS_FILE_VERSION:
            for chord, command_id in V2_DEFAULT_KEYBINDINGS.items():
                defaults.setdefault(chord, command_id)
    commands = [
        {"id": command_id, "label": label, "category": category}
        for command_id, label, category in KEYBINDING_COMMANDS
    ]
    return {
        "bindings": defaults,
        "defaults": DEFAULT_KEYBINDINGS,
        "commands": commands,
        "policy": keybinding_policy(),
        "rejected": rejected,
    }


async def get_keybindings(request: web.Request) -> web.Response:
    return json_response(_keybindings_payload(request.app[keys.CONFIG]))


async def put_keybindings(request: web.Request) -> web.Response:
    body = await request.json()
    bindings = body.get("bindings", body)
    if not isinstance(bindings, dict):
        raise ValueError("bindings must be an object")
    rejected: dict[str, str] = {}
    normalized: dict[str, str] = {}
    for chord, command in bindings.items():
        try:
            key, command_id = normalize_binding(chord, command)
            normalized[key] = command_id
        except ValueError as exc:
            rejected[str(chord)] = str(exc)
    if rejected:
        return json_response({"error": "invalid keybindings", "fields": rejected}, 422)
    if request.query.get("validate") == "1":
        return json_response({"ok": True})
    path = request.app[keys.CONFIG].data_dir / "keybindings.json"
    temporary = path.with_suffix(".json.tmp")
    document = {
        "version": KEYBINDINGS_FILE_VERSION,
        "replace_defaults": True,
        "bindings": normalized,
    }
    temporary.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)
    await request.app[keys.EVENTS].emit("configuration_changed", source="keybindings")
    return await get_keybindings(request)


async def get_hooks(request: web.Request) -> web.Response:
    path = request.app[keys.CONFIG].data_dir / "hooks.toml"
    return json_response({"text": path.read_text(encoding="utf-8") if path.exists() else ""})


async def get_hook_status(request: web.Request) -> web.Response:
    hooks: MetaHookEngine = request.app[keys.HOOKS]
    return json_response(
        {
            "diagnostic": hooks.diagnostic,
            "deliveries": [item.snapshot() for item in hooks.deliveries[-100:]],
        }
    )


async def put_hooks(request: web.Request) -> web.Response:
    text = str((await request.json()).get("text", ""))
    try:
        parse_hook_rules(text)
    except ValueError as exc:
        return json_response({"error": "invalid hooks TOML", "fields": {"text": str(exc)}}, 422)
    if request.query.get("validate") == "1":
        return json_response({"ok": True})
    path = request.app[keys.CONFIG].data_dir / "hooks.toml"
    temporary = path.with_suffix(".toml.tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)
    await request.app[keys.EVENTS].emit("configuration_changed", source="hooks")
    return json_response({"text": text})


async def get_settings(request: web.Request) -> web.Response:
    return json_response(request.app[keys.SETTINGS_STORE].all())


async def put_settings(request: web.Request) -> web.Response:
    profile = request.match_info["profile"]
    updated = request.app[keys.SETTINGS_STORE].update(profile, await request.json())
    await request.app[keys.EVENTS].emit("settings_changed", source="user", profile=profile)
    return json_response({"profile": profile, "settings": updated})


ROUTES: tuple[web.RouteDef, ...] = (
    web.get("/api/config", get_config),
    web.get("/api/settings/bundle", settings_bundle),
    web.patch("/api/config", patch_config),
    web.post("/api/config/reset", reset_config),
    web.get("/api/keybindings", get_keybindings),
    web.put("/api/keybindings", put_keybindings),
    web.get("/api/hooks", get_hooks),
    web.get("/api/hooks/status", get_hook_status),
    web.put("/api/hooks", put_hooks),
    web.get("/api/settings", get_settings),
    web.put("/api/settings/{profile}", put_settings),
)
