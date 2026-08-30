"""Install-wide configuration: the config file, keybindings, hooks, UI settings."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
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


# The parts the Settings panel blocks its first paint on, and the parts it does
# not. The split is by *what the paint needs*, not by where the data comes from,
# and that distinction is the whole point: the original bundle grouped nine GETs
# by origin, which is the right instinct applied to the wrong axis.
#
# Measured 2026-08-30 against the development host's daemon: the bundle answered
# in 107ms with 518 KiB, of which `usage` was 319.8 KiB (62%) and `provider`
# 177.3 KiB (34%). The config the panel actually renders from is 12.7 KiB. So
# the panel blocked on 40x the data it needed, for two tabs the operator usually
# has not opened.
# Measured per part, 2026-08-30, after the first split (each figure includes the
# ~14ms baseline of serializing `config`, which every response carries):
#   keybindings      1.1ms   the Save path PUTs these back, so rendering without
#                            them would let a Save overwrite the file with empty
#                            defaults - it is the one cheap part that must stay
#   projects        21.7ms
#   automation      30.0ms   read only inside `activeTab==='automation'`
#   profiles        42.0ms   read only inside `activeTab==='terminals'`; it stats
#                            executables to detect shells, which is why it leads
PAINT_PARTS = ("keybindings", "projects", "project_config")
# Fetched by the tab that needs them, on the same endpoint.
DEFERRED_PARTS = ("usage", "provider", "profiles", "automation")
BUNDLE_PARTS = PAINT_PARTS + DEFERRED_PARTS


async def settings_bundle(request: web.Request) -> web.Response:
    """What the Settings panel asked for, in one round trip.

    The panel used to fan out nine GETs; each answered in well under 50ms, but on
    a high-RTT client (phone over Tailscale) connection setup and RTT per request
    dominated the perceived open delay, so they were folded into one. That fold
    was by origin rather than by need, and it made the panel's first paint wait
    on half a megabyte of tab content - see `PAINT_PARTS`.

    So the caller names what it wants. No `parts` query is the paint set, which
    is what opening the panel asks for; `?parts=usage,provider` is what the Usage
    and Accounts tabs ask for when they are opened. `config` is always included
    because it is the one part the panel cannot render without, and its failure
    fails the request; every other part degrades to null with the reason under
    `errors`, and the client decides which missing parts it can tolerate.

    An unknown part is refused rather than ignored. Silently dropping one would
    hand the client a payload missing a key it asked for, which reads exactly
    like a part that failed - and those need different handling.
    """
    config: Config = request.app[keys.CONFIG]
    cwd = request.query.get("cwd")
    requested = request.query.get("parts")
    if requested is None:
        wanted = set(PAINT_PARTS)
    else:
        wanted = {name.strip() for name in requested.split(",") if name.strip()}
        unknown = sorted(wanted - set(BUNDLE_PARTS))
        if unknown:
            return json_response(
                {
                    "error": f"unknown settings bundle part(s): {', '.join(unknown)}",
                    "code": "unknown_bundle_part",
                    "known": list(BUNDLE_PARTS),
                },
                400,
            )
    parts: dict[str, Any] = {}
    errors: dict[str, str] = {}

    async def part(key: str, factory: Callable[[], Awaitable[Any]]) -> None:
        if key not in wanted:
            return
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
    body = await request.json()
    body_revision = body.pop("_revision", None)
    conflict = _revision_conflict(config, request, body_revision)
    if conflict is not None:
        return conflict
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


async def apply_experience_tier(request: web.Request) -> web.Response:
    """Apply one experience tier's absolute key assignment (`experience_tiers.py`).

    A dedicated route rather than a browser-computed PATCH because the key sets
    are policy, and policy computed in the browser is a second copy that drifts.
    The write itself goes through `update_config` exactly like a PATCH, so
    validation, revision bumping, hot/restart classification, and the
    `configuration_changed` event are all the ordinary ones.
    """
    from ..experience_tiers import TIERS, tier_changes

    config: Config = request.app[keys.CONFIG]
    body = await request.json()
    tier = body.get("tier")
    if tier not in TIERS:
        return json_response(
            {"error": "invalid configuration", "fields": {"tier": f"must be one of {TIERS}"}},
            422,
        )
    hot, restart = update_config(config, tier_changes(tier))
    apply_runtime_config(request.app, hot)
    log.info(
        "experience tier applied",
        extra={"tier": tier, "restart_required": ",".join(sorted(restart))},
    )
    await request.app[keys.EVENTS].emit(
        "configuration_changed", source="experience-tier", changed=sorted(hot | restart)
    )
    response = json_response(
        {**config.public_dict(), "hot_applied": sorted(hot), "restart_required": sorted(restart)}
    )
    response.headers["ETag"] = f'"{config.revision}"'
    return response


def _revision_conflict(
    config: Config, request: web.Request, body_revision: Any
) -> web.Response | None:
    """The other-device contract, shared by `PATCH /api/config` and the atomic save.

    Either channel carries it: the `If-Match` header, or a `_revision` in the body.
    A mismatch is a 409 naming the revision the daemon actually holds, so the client
    can reload and re-present the edit rather than silently overwriting a stranger's.
    """
    supplied = request.headers.get("If-Match", "").strip('"')
    if supplied and supplied != str(config.revision):
        return json_response(
            {"error": "configuration changed externally", "revision": config.revision}, 409
        )
    if body_revision is not None and int(body_revision) != config.revision:
        return json_response(
            {"error": "configuration changed externally", "revision": config.revision}, 409
        )
    return None


async def apply_settings(request: web.Request) -> web.Response:
    """Commit the config delta and the keybindings document, or commit neither.

    The Settings panel used to spend two requests here - a `PATCH /api/config` and a
    `PUT /api/keybindings` fired together through `Promise.all`. Either could fail
    alone, and the panel's one catch reported the pair as "invalid · nothing was
    changed"; a `_revision` conflict raised by another device saying exactly that while
    the keybindings file had already been rewritten. Pre-validating the keybindings
    closed one direction only, because the conflict lives on the other one.

    So both halves land here, and the ordering makes the lie impossible:

    1. the revision is checked, and the chords are normalized - both pure, so an
       invalid document of either kind is a 422 with nothing written;
    2. the keybindings document is *staged* next to its destination;
    3. `update_config` validates the whole candidate config before it saves, so a
       rejected field leaves the staged file unpublished and discarded;
    4. the staged file is renamed into place - one `os.replace`, the last step, and
       the only one that can fail after something has committed.

    Step 4 failing is a disk-level fault rather than a validation one, and it is the
    single case where a half-commit is real. The response says so - 500 with
    `committed: ["config"]` - instead of claiming nothing changed.
    """
    config: Config = request.app[keys.CONFIG]
    body = await request.json()
    if not isinstance(body, dict):
        raise ValueError("body must be an object")

    changes = body.get("config")
    if changes is None:
        changes = {}
    if not isinstance(changes, dict):
        raise ValueError("config must be an object")
    changes = dict(changes)

    supplied_bindings = body.get("keybindings")
    if isinstance(supplied_bindings, dict) and isinstance(
        supplied_bindings.get("bindings"), dict
    ):
        supplied_bindings = supplied_bindings["bindings"]
    if supplied_bindings is not None and not isinstance(supplied_bindings, dict):
        raise ValueError("keybindings must be an object")

    body_revision = body.get("_revision", changes.pop("_revision", None))
    conflict = _revision_conflict(config, request, body_revision)
    if conflict is not None:
        return conflict

    normalized: dict[str, str] | None = None
    if supplied_bindings is not None:
        normalized, rejected = _normalize_bindings(supplied_bindings)
        if rejected:
            return json_response(
                {
                    "error": "invalid keybindings",
                    "section": "keybindings",
                    "fields": rejected,
                    "committed": [],
                },
                422,
            )

    staged: Path | None = None
    if normalized is not None:
        staged = _stage_keybindings(config, normalized)

    try:
        hot, restart = update_config(config, changes)
    except ValueError as exc:
        if staged is not None:
            staged.unlink(missing_ok=True)
        detail = exc.args[0]
        return json_response(
            {
                "error": "invalid configuration",
                "section": "config",
                "fields": detail if isinstance(detail, dict) else {},
                "committed": [],
            },
            422,
        )
    except Exception:
        if staged is not None:
            staged.unlink(missing_ok=True)
        raise

    committed = ["config"]
    if staged is not None:
        try:
            _publish_keybindings(config, staged)
        except OSError as exc:
            # The config is already on disk and already hot-applied; saying otherwise
            # would be the exact lie this endpoint exists to remove.
            log.error("keybindings commit failed after the config committed: %s", exc)
            apply_runtime_config(request.app, hot)
            await request.app[keys.EVENTS].emit(
                "configuration_changed", source="settings", changed=sorted(hot | restart)
            )
            return json_response(
                {
                    "error": f"settings saved, but the shortcuts could not be written: {exc}",
                    "section": "keybindings",
                    "committed": committed,
                    "failed": ["keybindings"],
                    "config": {
                        **config.public_dict(),
                        "hot_applied": sorted(hot),
                        "restart_required": sorted(restart),
                    },
                },
                500,
            )
        committed.append("keybindings")

    apply_runtime_config(request.app, hot)
    await request.app[keys.EVENTS].emit(
        "configuration_changed",
        source="settings",
        changed=sorted(hot | restart),
        keybindings=staged is not None,
    )
    response = json_response(
        {
            "config": {
                **config.public_dict(),
                "hot_applied": sorted(hot),
                "restart_required": sorted(restart),
            },
            "keybindings": _keybindings_payload(config),
            "committed": committed,
        }
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


def _normalize_bindings(bindings: dict[Any, Any]) -> tuple[dict[str, str], dict[str, str]]:
    """Chord/command pairs the daemon will accept, and the ones it will not.

    Pure: nothing here touches the filesystem, which is what lets the atomic
    endpoint below learn that the keybindings half is invalid before it has
    committed the config half.
    """
    rejected: dict[str, str] = {}
    normalized: dict[str, str] = {}
    for chord, command in bindings.items():
        try:
            key, command_id = normalize_binding(chord, command)
            normalized[key] = command_id
        except ValueError as exc:
            rejected[str(chord)] = str(exc)
    return normalized, rejected


def _stage_keybindings(config: Config, normalized: dict[str, str]) -> Path:
    """Write the keybindings document beside its destination without publishing it.

    Splitting the write from the rename is the whole trick: after this returns,
    committing the keybindings half is a single `os.replace`, so it can be
    ordered *after* the config commit and still be the one step that cannot
    half-succeed.
    """
    path = config.data_dir / "keybindings.json"
    temporary = path.with_suffix(".json.tmp")
    document = {
        "version": KEYBINDINGS_FILE_VERSION,
        "replace_defaults": True,
        "bindings": normalized,
    }
    temporary.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    return temporary


def _publish_keybindings(config: Config, temporary: Path) -> None:
    temporary.replace(config.data_dir / "keybindings.json")


async def put_keybindings(request: web.Request) -> web.Response:
    body = await request.json()
    bindings = body.get("bindings", body)
    if not isinstance(bindings, dict):
        raise ValueError("bindings must be an object")
    normalized, rejected = _normalize_bindings(bindings)
    if rejected:
        return json_response({"error": "invalid keybindings", "fields": rejected}, 422)
    if request.query.get("validate") == "1":
        return json_response({"ok": True})
    config: Config = request.app[keys.CONFIG]
    _publish_keybindings(config, _stage_keybindings(config, normalized))
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
    web.post("/api/experience-tier", apply_experience_tier),
    web.post("/api/settings/apply", apply_settings),
    web.post("/api/config/reset", reset_config),
    web.get("/api/keybindings", get_keybindings),
    web.put("/api/keybindings", put_keybindings),
    web.get("/api/hooks", get_hooks),
    web.get("/api/hooks/status", get_hook_status),
    web.put("/api/hooks", put_hooks),
    web.get("/api/settings", get_settings),
    web.put("/api/settings/{profile}", put_settings),
)
