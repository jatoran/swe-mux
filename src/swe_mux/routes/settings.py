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
    COMMAND_GROUPS,
    KEYBINDING_COMMANDS,
    WHEN_FLAGS,
    Rule,
    document_for,
    normalize_rule,
    parse_document,
    prefixes,
    resolve,
)
from ..keychords import BROWSER_UNREACHABLE, HOSTS, PLATFORMS, chord_policy, sequence_label
from ..keymaps import DEFAULT_PRESET, default_rules, preset_rules, preset_summaries
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

    host, platform = _host_of(request)
    measured = _measured_unreachable(request, host)

    async def keybindings() -> Any:
        return _keybindings_payload(config, host=host, platform=platform, unreachable=measured)

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


async def get_experience_tiers(request: web.Request) -> web.Response:
    """Every tier and autonomy assignment, for the first-run panel to draw from.

    The panel's granular view shows what a tier sets before applying it, and
    the only honest source for that is the module that owns the policy - a
    table restated in the browser is the second copy `POST /api/experience-tier`
    exists to avoid. Values only; nothing here writes.
    """
    from ..automation_registry import REGISTRY, install_defaults
    from ..experience_tiers import (
        AUTONOMY_LEVELS,
        OVERRIDABLE_KEYS,
        TIERS,
        autonomy_changes,
        tier_changes,
    )

    config: Config = request.app[keys.CONFIG]
    previous = install_defaults(config.automation_project_defaults)

    return json_response(
        {
            "tiers": {tier: tier_changes(tier) for tier in TIERS},
            "autonomy": {level: autonomy_changes(level) for level in AUTONOMY_LEVELS},
            "overridable": sorted(OVERRIDABLE_KEYS),
            "project_defaults": {
                tier: [
                    {
                        "id": name,
                        "label": REGISTRY[name].label,
                        "enabled": enabled,
                        "previous": previous.get(name, False),
                    }
                    for name, enabled in tier_changes(tier)["automation_project_defaults"].items()
                ]
                for tier in TIERS
            },
        }
    )


async def apply_experience_tier(request: web.Request) -> web.Response:
    """Apply one experience tier's absolute key assignment (`experience_tiers.py`).

    A dedicated route rather than a browser-computed PATCH because the key sets
    are policy, and policy computed in the browser is a second copy that drifts.
    The write itself goes through `update_config` exactly like a PATCH, so
    validation, revision bumping, hot/restart classification, and the
    `configuration_changed` event are all the ordinary ones.

    Two optional refinements ride the same write so the first-run panel's
    granular choices land atomically with the tier: `autonomy` names one of the
    orthogonal auto-delivery assignments, and `overrides` names individual
    boolean deviations from the tier's own inventory. Both are validated
    against the closed sets the tier module owns - an unknown key is refused,
    never dropped, because a dropped override reads exactly like an applied one.
    """
    from ..experience_tiers import (
        AUTONOMY_LEVELS,
        OVERRIDABLE_KEYS,
        TIERS,
        autonomy_changes,
        tier_changes,
    )

    config: Config = request.app[keys.CONFIG]
    body = await request.json()
    tier = body.get("tier")
    if tier not in TIERS:
        return json_response(
            {"error": "invalid configuration", "fields": {"tier": f"must be one of {TIERS}"}},
            422,
        )
    changes = tier_changes(
        tier,
        project_defaults=config.automation_project_defaults,
        global_allow=config.automation_global_allow,
    )
    autonomy = body.get("autonomy")
    if autonomy is not None:
        if autonomy not in AUTONOMY_LEVELS:
            return json_response(
                {
                    "error": "invalid configuration",
                    "fields": {"autonomy": f"must be one of {AUTONOMY_LEVELS}"},
                },
                422,
            )
        changes.update(autonomy_changes(autonomy))
    overrides = body.get("overrides")
    if overrides is not None:
        if not isinstance(overrides, dict):
            return json_response(
                {
                    "error": "invalid configuration",
                    "fields": {"overrides": "must be an object of tier keys to booleans"},
                },
                422,
            )
        unknown = sorted(set(overrides) - OVERRIDABLE_KEYS)
        if unknown:
            return json_response(
                {
                    "error": "invalid configuration",
                    "fields": {key: "not an overridable tier key" for key in unknown},
                },
                422,
            )
        changes.update(overrides)
    if changes.get("automation_enabled") or changes.get("scan_timeline_enabled"):
        readiness = await automation._llm_readiness(request)
        if not readiness.ready or readiness.code == "unknown":
            return json_response(
                {
                    "error": (
                        "Set up and verify a model provider first, or continue with Deterministic."
                    ),
                    "code": "provider_required",
                    "llm": readiness.as_dict(),
                },
                409,
            )
        from ..llm_endpoint import resolve_endpoint

        endpoint = resolve_endpoint(config, request.app.get(keys.LLM_CAPABILITIES))
        if not endpoint.model_override and (
            not config.openrouter_cheap_model or not config.openrouter_standard_model
        ):
            return json_response(
                {
                    "error": (
                        "Choose and approve the cheap and standard models before enabling "
                        "Automations."
                    ),
                    "code": "models_required",
                },
                409,
            )
        from .. import model_setup

        secrets = request.app.get(keys.SECRET_STORE)
        if secrets is None or not model_setup.verified(
            config, endpoint, secrets.get(endpoint.secret_name)
        ):
            return json_response(
                {
                    "error": (
                        "Approve and test the selected model roles in guided model setup first."
                    ),
                    "code": "model_verification_required",
                },
                409,
            )
    hot, restart = update_config(config, changes)
    apply_runtime_config(request.app, hot)
    log.info(
        "experience tier applied",
        extra={
            "tier": tier,
            "autonomy": autonomy or "",
            "overrides": ",".join(sorted(overrides)) if overrides else "",
            "restart_required": ",".join(sorted(restart)),
        },
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
    saved_host, saved_platform = _host_of(request)
    body = await request.json()
    if not isinstance(body, dict):
        raise ValueError("body must be an object")

    changes = body.get("config")
    if changes is None:
        changes = {}
    if not isinstance(changes, dict):
        raise ValueError("config must be an object")
    changes = dict(changes)

    supplied = body.get("keybindings")
    if supplied is not None and not isinstance(supplied, dict):
        raise ValueError("keybindings must be an object")

    body_revision = body.get("_revision", changes.pop("_revision", None))
    conflict = _revision_conflict(config, request, body_revision)
    if conflict is not None:
        return conflict

    normalized: list[Rule] | None = None
    supplied_preset = "custom"
    if supplied is not None:
        supplied_preset = str(supplied.get("preset") or "custom")
        normalized, rejected = _normalize_rules(supplied.get("rules"))
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
        staged = _stage_keybindings(config, supplied_preset, normalized)

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
            "keybindings": _keybindings_payload(
                config,
                host=saved_host,
                platform=saved_platform,
                unreachable=_measured_unreachable(request, saved_host),
            ),
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


def _host_of(request: web.Request) -> tuple[str, str]:
    """Which client is asking, from its own query.

    Resolution runs here rather than in the browser for the reason the
    experience-tier assignment does: a browser-computed answer would be a second
    copy of the policy and the copy is what drifts. So the client states what it
    is and the daemon answers for that host. Unknown or absent values fall back
    to the most restrictive combination a real client can be (a browser tab on
    Windows), because a *wrong* permissive answer shows a dead chord as live.
    """
    host = request.query.get("host", "")
    platform = request.query.get("platform", "")
    return (
        host if host in HOSTS else "browser",
        platform if platform in PLATFORMS else "win",
    )


def _read_document(config: Config) -> tuple[str, list[Rule], dict[str, str]]:
    """This install's saved rules, or the default preset's when it has none."""
    path = config.data_dir / "keybindings.json"
    if not path.exists():
        return DEFAULT_PRESET, default_rules(), {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid keybindings.json: {exc}") from exc
    preset, rules, rejected = parse_document(raw)
    if rejected:
        log.warning(
            "keybindings.json carries rules this build cannot use",
            extra={"rejected": ",".join(sorted(rejected)), "preset": preset or "custom"},
        )
    return preset or DEFAULT_PRESET, rules, rejected


def _measured_unreachable(request: web.Request, host: str) -> set[str] | None:
    """What this device measured about its own browser, or None if it never did.

    Read from the per-device settings store rather than sent on the request, so the
    correction outlives the tab that made it and applies to every later read. Only a
    *tested* chord moves the shipped answer: `unknown` leaves it standing, because an
    untried chord is not evidence and treating it as one would quietly hand back
    every chord nobody probed (`frontend/src/hostKeyboardProbe.ts` holds the same
    rule on the other side).
    """
    if host != "browser":
        return None
    try:
        store = request.app[keys.SETTINGS_STORE]
    except KeyError:  # pragma: no cover - a request built without the runtime
        return None
    corrected = set(BROWSER_UNREACHABLE)
    found = False
    for profile in store.all().get("profiles", {}).values():
        report = (profile or {}).get("keyboard", {}).get("probe")
        if not isinstance(report, dict) or report.get("host") != "browser":
            continue
        for chord, result in (report.get("results") or {}).items():
            verdict = (result or {}).get("verdict") if isinstance(result, dict) else None
            if verdict == "delivered":
                corrected.discard(str(chord))
                found = True
            elif verdict == "blocked":
                corrected.add(str(chord))
                found = True
    return corrected if found else None


def _keybindings_payload(
    config: Config,
    *,
    host: str,
    platform: str,
    unreachable: set[str] | None = None,
) -> dict[str, Any]:
    """Everything a client needs to dispatch keystrokes and to edit its own map.

    `rules` is the durable document; `resolved` is what THIS host dispatches on,
    with `undeliverable` and `contested` naming what was dropped or shadowed and
    why. Handing back both is what lets Settings say "works in the desktop app"
    about a chord the asking browser will never receive, instead of drawing it as
    though it were live.
    """
    preset, rules, rejected = _read_document(config)
    resolution = resolve(rules, host=host, platform=platform, unreachable=unreachable)
    return {
        "preset": preset,
        "measured": sorted(unreachable) if unreachable is not None else None,
        "presets": preset_summaries(),
        "host": host,
        "platform": platform,
        "rules": [rule.as_dict() for rule in rules],
        "resolved": resolution.bindings,
        "prefixes": sorted(prefixes(rules)),
        "undeliverable": resolution.undeliverable,
        "contested": resolution.contested,
        "labels": {
            sequence: sequence_label(sequence, platform=platform)
            for sequence in {*resolution.bindings, *prefixes(rules)}
        },
        "commands": [
            {"id": command_id, "label": label, "category": category}
            for command_id, label, category in KEYBINDING_COMMANDS
        ],
        "groups": [
            {"category": category, "key": key, "title": title}
            for category, key, title in COMMAND_GROUPS
        ],
        "when_flags": list(WHEN_FLAGS),
        "policy": chord_policy(),
        "rejected": rejected,
    }


async def get_keybindings(request: web.Request) -> web.Response:
    host, platform = _host_of(request)
    return json_response(
        _keybindings_payload(
            request.app[keys.CONFIG],
            host=host,
            platform=platform,
            unreachable=_measured_unreachable(request, host),
        )
    )


def _normalize_rules(raw: object) -> tuple[list[Rule], dict[str, str]]:
    """Rules the daemon will accept, and the ones it will not.

    Pure: nothing here touches the filesystem, which is what lets the atomic
    endpoint below learn that the keybindings half is invalid before it has
    committed the config half.
    """
    if not isinstance(raw, list):
        raise ValueError("rules must be a list")
    rejected: dict[str, str] = {}
    normalized: list[Rule] = []
    for index, entry in enumerate(raw):
        try:
            normalized.append(normalize_rule(entry))
        except ValueError as exc:
            label = str(entry.get("keys")) if isinstance(entry, dict) else f"rule {index}"
            rejected[label] = str(exc)
    return normalized, rejected


def _stage_keybindings(config: Config, preset: str, rules: list[Rule]) -> Path:
    """Write the keybindings document beside its destination without publishing it.

    Splitting the write from the rename is the whole trick: after this returns,
    committing the keybindings half is a single `os.replace`, so it can be
    ordered *after* the config commit and still be the one step that cannot
    half-succeed.
    """
    path = config.data_dir / "keybindings.json"
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(document_for(preset, rules), indent=2) + "\n", encoding="utf-8")
    return temporary


def _publish_keybindings(config: Config, temporary: Path) -> None:
    temporary.replace(config.data_dir / "keybindings.json")


async def put_keybindings(request: web.Request) -> web.Response:
    body = await request.json()
    if not isinstance(body, dict):
        raise ValueError("body must be an object")
    normalized, rejected = _normalize_rules(body.get("rules"))
    if rejected:
        return json_response({"error": "invalid keybindings", "fields": rejected}, 422)
    if request.query.get("validate") == "1":
        return json_response({"ok": True})
    config: Config = request.app[keys.CONFIG]
    preset = str(body.get("preset") or "custom")
    _publish_keybindings(config, _stage_keybindings(config, preset, normalized))
    log.info(
        "keybindings written",
        extra={"preset": preset, "rules": str(len(normalized))},
    )
    await request.app[keys.EVENTS].emit("configuration_changed", source="keybindings")
    return await get_keybindings(request)


async def apply_keymap_preset(request: web.Request) -> web.Response:
    """Rewrite `keybindings.json` from one shipped preset.

    The same shape as `POST /api/experience-tier` and for the same reason: the
    preset table is policy, so a browser that computed the rule list would be a
    second copy of it. The assignment is absolute rather than a delta - the
    document is replaced, not merged - so applying a preset twice is idempotent
    and switching between two is deterministic whatever came before. The cost,
    stated plainly and repeated in the UI: it overwrites hand-edited bindings,
    which is why the control applies on an explicit press.
    """
    config: Config = request.app[keys.CONFIG]
    body = await request.json()
    preset = str(body.get("preset", ""))
    known = sorted(str(item["id"]) for item in preset_summaries())
    try:
        rules = preset_rules(preset)
    except ValueError:
        return json_response(
            {"error": "invalid configuration", "fields": {"preset": f"must be one of {known}"}},
            422,
        )
    _publish_keybindings(config, _stage_keybindings(config, preset, rules))
    hot, restart = update_config(config, {"keymap_preset": preset})
    apply_runtime_config(request.app, hot)
    log.info("keymap preset applied", extra={"preset": preset, "rules": str(len(rules))})
    await request.app[keys.EVENTS].emit(
        "configuration_changed", source="keymap-preset", changed=sorted(hot | restart)
    )
    host, platform = _host_of(request)
    return json_response(
        {
            "config": config.public_dict(),
            "keybindings": _keybindings_payload(
                config,
                host=host,
                platform=platform,
                unreachable=_measured_unreachable(request, host),
            ),
        }
    )


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
    web.get("/api/experience-tiers", get_experience_tiers),
    web.post("/api/experience-tier", apply_experience_tier),
    web.post("/api/keymap-preset", apply_keymap_preset),
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
