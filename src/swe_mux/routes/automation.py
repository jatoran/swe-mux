"""Automation rules, observers, spend, the provider behind them, and enablement."""

from __future__ import annotations

import asyncio
import logging
import threading
from dataclasses import replace
from pathlib import Path
from typing import Any, NamedTuple

from aiohttp import web

from .. import (
    app_keys as keys,
)
from ..agent_authority import (
    AUTHORITY_FIELDS,
    install_ceiling,
    install_default,
    resolve_authority,
)
from ..assistant import (
    ASSISTANT_RULE_ID,
)
from ..attention_narration import NARRATION_RULE_ID
from ..automation import (
    AutomationEngine,
    RuleValidationError,
    normalize_event,
    parse_rules,
    serialize_rules,
)
from ..automation_registry import (
    DEDICATED_INSTALL_SWITCHES,
    TITLE_REFINEMENTS_DEFAULT,
    TITLE_REFINEMENTS_MAX,
    dependency_closure,
    effective_global_allow,
    install_defaults,
    requested_from_config,
    resolve_scan_auto_enable,
    resolve_title_refinements,
)
from ..automation_registry import REGISTRY as AUTOMATION_REGISTRY
from ..automation_registry import resolve_config as resolve_automation_config
from ..automation_store import AutomationStore
from ..behavioral_consumers import ADAPTIVE_TITLE_RULE_ID
from ..config import Config
from ..errors import NotFound
from ..http_support import json_response
from ..llm_endpoint import (
    LLM_PROVIDERS,
    EndpointCapabilities,
    LlmEndpoint,
    LlmReadiness,
)
from ..llm_endpoint import capabilities_of_record as llm_capabilities_of_record
from ..llm_endpoint import readiness as llm_readiness
from ..llm_endpoint import resolve_endpoint as resolve_llm_endpoint
from ..llm_endpoint import verification_state as llm_verification_state
from ..models import (
    MuxEvent,
)
from ..openrouter import (
    CatalogProbe,
    OpenRouterClient,
    OpenRouterError,
    cache_saving_usd,
)
from ..project_card import PROJECT_CARD_RULE_ID
from ..project_context import ProjectContext
from ..project_files import (
    merge_project_config,
    read_project_config,
    write_project_config,
)
from ..runtime_config import forget_llm_readiness
from ..scan_timeline import SCAN_RULE_ID
from ..secret_store import PlatformSecretStore, SecretStoreError
from ..voice import (
    VOICE_RULE_ID,
)
from .support import _observations_project, _registered_identity

log = logging.getLogger(__name__)


# Diagnostic repository rules re-read/re-parse on every /automation request; cache
# the parsed entry per rules.toml path, invalidated by (mtime_ns, size).
_repo_rules_cache: dict[str, tuple[tuple[int, int], dict[str, Any]]] = {}


_repo_rules_lock = threading.Lock()


def _load_repo_rule_entry(project_id: str, root: str) -> dict[str, Any] | None:
    """Build one project's inert repository-rules diagnostic, cached by mtime+size.

    Runs entirely in a worker thread (stat + read + TOML parse all block). The
    diagnostic is a deterministic function of file content, so caching an
    invalid-parse entry by version is correct too. Returns None when there is no
    regular rules.toml, mirroring the original `not path.is_file()` skip.
    """
    path = Path(str(root)) / ".swe-mux" / "rules.toml"
    try:
        if not path.is_file():
            return None
        version = (path.stat().st_mtime_ns, path.stat().st_size)
    except OSError:
        return None
    key = str(path)
    with _repo_rules_lock:
        cached = _repo_rules_cache.get(key)
    if cached and cached[0] == version:
        return {**cached[1], "project_scope_id": project_id}
    try:
        rules = parse_rules(path.read_text(encoding="utf-8"), source="repository-inert")
        entry: dict[str, Any] = {
            "project_scope_id": project_id,
            "path": str(path),
            "valid": True,
            "rules": [rule.snapshot() for rule in rules],
            "execution": "inert",
        }
    except (OSError, RuleValidationError) as exc:
        entry = {
            "project_scope_id": project_id,
            "path": str(path),
            "valid": False,
            "diagnostic": str(exc),
            "execution": "inert",
        }
    with _repo_rules_lock:
        _repo_rules_cache[key] = (version, entry)
    return {**entry, "project_scope_id": project_id}


async def _automation_status_payload(request: web.Request) -> dict[str, Any]:
    automation: AutomationEngine = request.app[keys.AUTOMATION]
    projects = await request.app[keys.HISTORY].project_scopes(include_hidden=True)
    entries = await asyncio.gather(
        *(
            asyncio.to_thread(_load_repo_rule_entry, str(project["id"]), str(project["root"]))
            for project in projects
        )
    )
    repository_rules = [entry for entry in entries if entry is not None]
    return {
        **automation.status(),
        "legacy": {
            "path": str(request.app[keys.CONFIG].data_dir / "hooks.toml"),
            "active": bool(request.app[keys.HOOKS].rules),
            "diagnostic": request.app[keys.HOOKS].diagnostic,
            "migration": "explicit-save-required",
        },
        "repository_rules": repository_rules,
    }


async def get_automation_status(request: web.Request) -> web.Response:
    return json_response(await _automation_status_payload(request))


def _automation_rules_payload(request: web.Request) -> dict[str, Any]:
    path = request.app[keys.CONFIG].data_dir / "rules.toml"
    return {
        "version": 1,
        "text": path.read_text(encoding="utf-8") if path.exists() else "version = 1\n",
        "rules": [rule.snapshot() for rule in request.app[keys.AUTOMATION].rules],
        "diagnostic": request.app[keys.AUTOMATION].diagnostic,
    }


async def get_automation_rules(request: web.Request) -> web.Response:
    return json_response(_automation_rules_payload(request))


async def put_automation_rules(request: web.Request) -> web.Response:
    text = str((await request.json()).get("text", ""))
    try:
        parse_rules(text)
    except RuleValidationError as exc:
        return json_response({"error": "invalid rules TOML", "fields": {"text": str(exc)}}, 422)
    if request.query.get("validate") == "1":
        return json_response({"ok": True})
    path = request.app[keys.CONFIG].data_dir / "rules.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".toml.tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)
    automation: AutomationEngine = request.app[keys.AUTOMATION]
    automation.reload()
    await request.app[keys.EVENTS].emit("configuration_changed", source="settings")
    return await get_automation_rules(request)


async def patch_automation_rule(request: web.Request) -> web.Response:
    body = await request.json()
    if not isinstance(body, dict) or not body or set(body) - {"enabled", "shadow"}:
        raise ValueError("only enabled and shadow may be changed through the ordinary editor")
    if any(not isinstance(value, bool) for value in body.values()):
        raise ValueError("enabled and shadow must be boolean")
    rule_id = request.match_info["rule_id"]
    automation: AutomationEngine = request.app[keys.AUTOMATION]
    found = False
    rules = []
    for rule in automation.rules:
        if rule.id != rule_id:
            rules.append(rule)
            continue
        found = True
        rules.append(
            replace(
                rule,
                enabled=body.get("enabled", rule.enabled),
                shadow=body.get("shadow", rule.shadow),
            )
        )
    if not found:
        raise NotFound(rule_id, kind="automation rule")
    text = serialize_rules(rules)
    parse_rules(text)
    path = request.app[keys.CONFIG].data_dir / "rules.toml"
    temporary = path.with_suffix(".toml.tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)
    automation.reload()
    await request.app[keys.EVENTS].emit("configuration_changed", source="settings")
    return await get_automation_rules(request)


async def automation_dry_run(request: web.Request) -> web.Response:
    body = await request.json()
    sequence = int(body.get("event_seq") or 0)
    rows = await request.app[keys.HISTORY].events(after_seq=max(0, sequence - 1), limit=1)
    if not rows or int(rows[0]["seq"]) != sequence:
        raise NotFound(sequence, kind="event")
    row = rows[0]
    event = MuxEvent(
        float(row["ts"]),
        row.get("session_id"),
        str(row["source"]),
        str(row["type"]),
        row["payload"],
        int(row["seq"]),
    )
    session = request.app[keys.SESSIONS].sessions.get(row.get("session_id") or "")
    normalized = normalize_event(
        event,
        session.record if session else None,
        attended=bool(session and session.subscribers),
    )
    supplied = body.get("text")
    rules = parse_rules(str(supplied), source="dry-run") if supplied is not None else None
    reports = await request.app[keys.AUTOMATION].evaluate(normalized, rules=rules, dry_run=True)
    return json_response({"event": normalized.snapshot(), "reports": reports})


class FeatureSpender(NamedTuple):
    """A feature that bills the observer budget without being an automation rule.

    Every field here exists because the spend table lies without it. Grouping by
    `rule_id` alone prints a raw id, so a reader cannot tell an expensive feature
    from an expensive rule. And `enabled` is the column that separates a live
    bill from spent history, so it has to name the switch that actually governs
    the feature rather than assert that features are always on.
    """

    label: str
    detail: str
    #: The install-wide config flag that turns this spender off. Per-project
    #: opt-ins are deliberately not consulted: this column answers "is this
    #: still running", and the honest install-wide answer is the global switch.
    setting_key: str
    setting_label: str


# Anything that spends under its own rule id belongs here. A spender missing from
# this table is indistinguishable from one that was retired, and the row says
# "retired · off" about a feature the operator is actively using — which is what
# `builtin:assistant` did between Phase 10.6 shipping and 2026-08-20.
FEATURE_SPENDERS: dict[str, FeatureSpender] = {
    SCAN_RULE_ID: FeatureSpender(
        "Scan timeline", "Per-run scans that extract timeline records",
        "scan_timeline_enabled", "Scan timeline",
    ),
    VOICE_RULE_ID: FeatureSpender(
        "Read aloud", "Spoken summaries of agent replies",
        "tts_enabled", "Read aloud",
    ),
    PROJECT_CARD_RULE_ID: FeatureSpender(
        "Project card", "Generated Project context cards",
        # No install-wide switch of its own: it is per-Project, under the
        # automation kill switch, which is the only global truth to report.
        "automation_enabled", "Automation",
    ),
    NARRATION_RULE_ID: FeatureSpender(
        "Attention narration", "Model narration of ranked attention",
        "attention_narration_enabled", "Attention narration",
    ),
    ASSISTANT_RULE_ID: FeatureSpender(
        "Mux assistant", "Conversational fleet operation, typed and spoken",
        "assistant_enabled", "Mux assistant",
    ),
    ADAPTIVE_TITLE_RULE_ID: FeatureSpender(
        "Adaptive title", "Session titles rewritten from scan records",
        # Per-Project beneath that, but it consumes scan records and cannot
        # spend at all without the timeline that produces them.
        "scan_timeline_enabled", "Scan timeline",
    ),
}


def _label_spend_rows(
    rows: list[dict[str, Any]], engine: dict[str, Any], config: Config
) -> list[dict[str, Any]]:
    """Name every spending rule, and say what kind of thing it is.

    Cost is only actionable next to the control that turns it off, so each row also carries
    the setting that governs it and whether that setting is currently on: a rule at the top
    of the list that is already disabled is spent history, not a live bill.
    """
    known: dict[str, dict[str, Any]] = {}
    for rule in engine.get("built_in_rules") or []:
        known[str(rule["id"])] = {
            "label": str(rule.get("name") or rule["id"]),
            "detail": str(rule.get("description") or ""),
            "kind": "observer",
            "enabled": bool(rule.get("enabled")),
            "setting_label": str(rule.get("setting_label") or ""),
        }
    for rule in engine.get("rules") or []:
        known[str(rule["id"])] = {
            "label": str(rule.get("name") or rule["id"]),
            "detail": "",
            "kind": "custom",
            "enabled": bool(rule.get("enabled")),
            "setting_label": "",
        }
    for rule_id, feature in FEATURE_SPENDERS.items():
        known.setdefault(
            rule_id,
            {
                "label": feature.label,
                "detail": feature.detail,
                "kind": "feature",
                # Read from config rather than asserted: a feature switched off
                # still has spend in the window, and calling that a live bill
                # sends the reader looking for something to turn off that is
                # already off.
                "enabled": bool(getattr(config, feature.setting_key, False)),
                "setting_label": feature.setting_label,
            },
        )
    labelled = []
    for row in rows:
        meta = known.get(
            row["rule_id"],
            {
                "label": row["rule_id"],
                "detail": "",
                # Retired or renamed: it billed, and nothing on this page can turn it off.
                "kind": "retired",
                "enabled": False,
                "setting_label": "",
            },
        )
        labelled.append({**row, **meta})
    return labelled


async def _price_cache_saving(request: web.Request, breakdown: dict[str, Any]) -> None:
    """Price what caching saved each rule, from the persisted model catalog.

    Derived rather than reported, and separated from the store because pricing is
    provider knowledge: `automation_store` deliberately knows nothing about
    OpenRouter, so it hands over the token counts per (rule, model) and this
    applies the catalog to them.

    The measured field beside it (`cache_discount_usd`) stays whatever the
    provider said, which today is nothing at all - `cache_discount` lives in
    OpenRouter's `/generation` stats, not in a completion's usage payload. Keeping
    the two apart is the point: one is a measurement that is usually absent, the
    other is arithmetic over prices that are always published, and collapsing
    them would leave nobody able to say which they were reading.
    """
    store: AutomationStore = request.app[keys.AUTOMATION_STORE]
    try:
        catalog = {
            str(entry["id"]): entry
            for entry in (await store.model_cache())["models"]
            if isinstance(entry, dict) and entry.get("id")
        }
    except Exception:  # noqa: BLE001 - a cost view must not fail over its own annotation
        log.debug("cache saving pricing skipped: model catalog unavailable", exc_info=True)
        return
    window_total = 0.0
    today_total = 0.0
    priced_any = False
    for rule in breakdown.get("rules") or []:
        usage = rule.pop("cache_usage_by_model", [])
        saving, priced = cache_saving_usd(usage, catalog)
        today, _ = cache_saving_usd(
            [
                {
                    "model": row.get("model"),
                    "cached_tokens": row.get("today_cached_tokens"),
                    "cache_write_tokens": row.get("today_cache_write_tokens"),
                }
                for row in usage
            ],
            catalog,
        )
        rule["cache_saving_usd"] = saving
        rule["today_cache_saving_usd"] = today
        # How many of this rule's models the catalog could price. A partial
        # figure is still worth showing and must still be readable as partial.
        rule["cache_saving_models_priced"] = priced
        rule["cache_saving_models"] = len(usage)
        if saving is not None:
            priced_any = True
            window_total += saving
        if today is not None:
            today_total += today
    totals = breakdown.setdefault("totals", {})
    totals["cache_saving_usd"] = round(window_total, 6) if priced_any else None
    totals["today_cache_saving_usd"] = round(today_total, 6) if priced_any else None


async def automation_dashboard(request: web.Request) -> web.Response:
    store: AutomationStore = request.app[keys.AUTOMATION_STORE]
    engine = request.app[keys.AUTOMATION].status()
    breakdown = await store.spend_breakdown(days=7)
    breakdown["rules"] = _label_spend_rows(
        breakdown["rules"], engine, request.app[keys.CONFIG]
    )
    await _price_cache_saving(request, breakdown)
    return json_response(
        {
            **await store.dashboard(),
            "controls": {
                "automation_enabled": bool(request.app[keys.CONFIG].automation_enabled),
                "scan_timeline_enabled": bool(
                    request.app[keys.CONFIG].scan_timeline_enabled
                ),
            },
            "engine": engine,
            "provider": await _provider_status(request),
            "recent_firings": await store.firings(limit=25),
            "recent_action_results": await store.action_results(limit=50),
            "recent_observer_calls": await store.observer_calls(limit=50),
            "recent_annotations": await store.annotations(limit=25),
            # Per-rule, so the cost view can answer which automation to turn off rather
            # than only what automation cost in total.
            "spend_breakdown": breakdown,
        }
    )


async def automation_firings(request: web.Request) -> web.Response:
    return json_response(
        {
            "items": await request.app[keys.AUTOMATION_STORE].firings(
                rule_id=request.query.get("rule"),
                limit=int(request.query.get("limit", 200)),
            )
        }
    )


async def _annotation_session_run_ids(app: web.Application, session_id: str) -> list[str]:
    """Every agent-run id belonging to one session, live run plus its history.

    A session filter on the Findings surface matches these ids against the
    annotations' ``agent_run_id`` column, because that column is the only anchor
    every run-scoped detector writes (the ``session_id`` column is populated by
    one detector alone). The live record carries the current run; superseded runs
    (a ``/clear`` mints a fresh one) live in history, so both are unioned.
    """
    run_ids: set[str] = set()
    live = app[keys.SESSIONS].sessions.get(session_id)
    if live is not None:
        current = str(getattr(live.record, "agent_run_id", "") or "")
        if current:
            run_ids.add(current)
    for row in await app[keys.HISTORY].agent_runs_for_session(session_id):
        run_id = str(row.get("agent_run_id") or "")
        if run_id:
            run_ids.add(run_id)
    return sorted(run_ids)


def _mark_unsupported(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Retract, at read time, findings whose own evidence no longer supports them.

    A stored finding is a record of what a detector concluded and is never edited
    or deleted to change that record. What *can* change is whether a reader is
    told it stands: the loop detector now refuses to seed on a fact carrying no
    target and no content hash, and the same rule applied here withdraws the 390
    of 397 historical findings that rest on exactly those facts
    (`deterministic_consumers.loop_finding_unsupported`). The row keeps saying
    what it said; the read says why it does not hold.
    """
    from ..deterministic_consumers import LOOP_UNSUPPORTED_REASON, loop_finding_unsupported

    for item in items:
        if item.get("tag") != "loop-detected":
            continue
        if loop_finding_unsupported(item.get("evidence_json")):
            item["unsupported"] = True
            item["unsupported_reason"] = LOOP_UNSUPPORTED_REASON
    return items


async def list_annotations(request: web.Request) -> web.Response:
    """Findings read: annotations filtered by tag, project, session, run, and time.

    Extends the original run/tag read rather than forking a second endpoint. A
    ``session_id`` is resolved to the session's run-id set (see
    ``_annotation_session_run_ids``); ``tag_counts`` reports per-tag totals in the
    same scope but ignores the tag chip, so the human surface can tell a quiet
    scope from a filtered one.
    """
    store = request.app[keys.AUTOMATION_STORE]
    query = request.query
    agent_run_id = query.get("agent_run_id")
    project_id = query.get("project_id")
    tag = query.get("tag")
    raw_since = query.get("since")
    since = float(raw_since) if raw_since not in (None, "") else None
    session_id = query.get("session_id")
    agent_run_ids = (
        await _annotation_session_run_ids(request.app, session_id)
        if session_id
        else None
    )
    return json_response(
        {
            "items": _mark_unsupported(await store.annotations(
                agent_run_id=agent_run_id,
                agent_run_ids=agent_run_ids,
                project_id=project_id,
                tag=tag,
                since=since,
                limit=int(query.get("limit", 200)),
            )),
            "tag_counts": await store.annotation_tag_counts(
                agent_run_id=agent_run_id,
                agent_run_ids=agent_run_ids,
                project_id=project_id,
                since=since,
            ),
        }
    )


async def _llm_readiness(request: web.Request) -> LlmReadiness:
    """The install's provider verdict, through the app's cache when it has one.

    An app with no provider wiring at all - a partial harness answering a
    dependency-graph question, never the daemon - reports `unknown` rather than
    raising. The alternative is a `KeyError` turning a perfectly answerable
    question about the DAG into a 404, and `unknown` is honest: it says nobody
    was asked, which is different from both verdicts. The daemon installs
    `llm_ready` in `create_app`, so no real request reaches this branch.
    """
    resolver = request.app.get(keys.LLM_READY)
    if resolver is not None:
        return await resolver()
    config = request.app.get(keys.CONFIG)
    store = request.app.get(keys.SECRET_STORE)
    automation_store = request.app.get(keys.AUTOMATION_STORE)
    if config is None or store is None or automation_store is None:
        return LlmReadiness(
            True, "openrouter", "unknown", "No model provider is wired into this daemon."
        )
    record = await automation_store.provider_verification(
        str(getattr(config, "llm_provider", "openrouter") or "openrouter")
    )
    # Read straight off the row rather than from the live store, because this is
    # the fallback path for an app that has no `llm_ready` and may equally have no
    # capability store. The durable record is the same answer either way.
    endpoint = resolve_llm_endpoint(config, llm_capabilities_of_record(record))
    return llm_readiness(
        endpoint,
        api_key=store.get(endpoint.secret_name),
        verified_fingerprint=str((record or {}).get("fingerprint") or "") or None,
    )


async def _provider_status(request: web.Request) -> dict[str, Any]:
    """Everything Settings → Accounts needs to describe the model provider.

    `secret` stays keyed to OpenRouter for compatibility - the browser's existing
    key controls read it - and `providers` is the per-provider view that replaces
    it: each configured endpoint with its own key status, its verification, and
    the reason it is not usable when it is not. `llm` is the resolved verdict for
    the *active* one, which is what every gate in the app renders.
    """
    config: Config = request.app[keys.CONFIG]
    store: PlatformSecretStore = request.app[keys.SECRET_STORE]
    automation_store: AutomationStore = request.app[keys.AUTOMATION_STORE]
    capabilities = request.app[keys.LLM_CAPABILITIES]
    active = resolve_llm_endpoint(config, capabilities)
    providers: list[dict[str, Any]] = []
    for name in LLM_PROVIDERS:
        endpoint = (
            active
            if name == active.provider
            else resolve_llm_endpoint(replace(config, llm_provider=name), capabilities)
        )
        api_key = store.get(endpoint.secret_name)
        record = await automation_store.provider_verification(name)
        providers.append(
            {
                "id": name,
                "label": endpoint.label,
                "active": name == active.provider,
                "origin": endpoint.origin,
                "model": endpoint.model_override,
                "requires_verification": endpoint.requires_verification,
                "cache_policy": endpoint.cache_policy,
                "secret": store.status(endpoint.secret_name),
                "verification": llm_verification_state(
                    endpoint, api_key=api_key, record=record
                ),
                "readiness": llm_readiness(
                    endpoint,
                    api_key=api_key,
                    verified_fingerprint=str((record or {}).get("fingerprint") or "") or None,
                ).as_dict(),
            }
        )
    return {
        "secret": store.status("openrouter_api_key"),
        "models": await automation_store.model_cache(),
        "origin": active.origin,
        "cheap_model": config.openrouter_cheap_model,
        "standard_model": config.openrouter_standard_model,
        "provider": active.provider,
        "providers": providers,
        "llm": (await _llm_readiness(request)).as_dict(),
    }


async def automation_provider_status(request: web.Request) -> web.Response:
    return json_response(await _provider_status(request))


def _requested_endpoint(request: web.Request, body: dict[str, Any]) -> LlmEndpoint:
    """The endpoint a provider request names, defaulting to the active one."""
    config: Config = request.app[keys.CONFIG]
    capabilities = request.app[keys.LLM_CAPABILITIES]
    name = str(body.get("provider") or "").strip()
    if not name:
        return resolve_llm_endpoint(config, capabilities)
    if name not in LLM_PROVIDERS:
        raise ValueError("provider must be " + " or ".join(LLM_PROVIDERS))
    if name == config.llm_provider:
        return resolve_llm_endpoint(config, capabilities)
    return resolve_llm_endpoint(replace(config, llm_provider=name), capabilities)


def _unproven_endpoint(request: web.Request, body: dict[str, Any]) -> LlmEndpoint:
    """The same endpoint, shaped as though nothing had ever been measured about it.

    What a verification must run against. Resolving with the *stored* capabilities
    would make the probe circular: a previously-annotated endpoint keeps a blank
    `model_override`, so the proving completion would carry whatever model id a
    feature happens to be configured with rather than the one the operator typed
    into the endpoint form - and an endpoint edited down from a router to a single
    local model could never be re-proven, because it would keep asking for models
    the new server has never heard of.
    """
    config: Config = request.app[keys.CONFIG]
    name = str(body.get("provider") or "").strip() or config.llm_provider
    if name not in LLM_PROVIDERS:
        raise ValueError("provider must be " + " or ".join(LLM_PROVIDERS))
    return resolve_llm_endpoint(replace(config, llm_provider=name))


async def automation_provider_key(request: web.Request) -> web.Response:
    body = await request.json()
    operation = str(body.get("operation") or "test")
    value = body.get("key")
    store: PlatformSecretStore = request.app[keys.SECRET_STORE]
    provider: OpenRouterClient = request.app[keys.OPENROUTER]
    automation_store: AutomationStore = request.app[keys.AUTOMATION_STORE]
    try:
        endpoint = _requested_endpoint(request, body)
        secret_name = endpoint.secret_name
        if operation == "test":
            result = await provider.test_key(
                str(value) if value else None, endpoint=endpoint
            )
            return json_response({**result, "status": store.status(secret_name)})
        if operation in {"set", "replace"}:
            if not isinstance(value, str) or not value.strip():
                raise ValueError("key is required")
            if body.get("test", True):
                await provider.test_key(value, endpoint=endpoint)
            store.set(secret_name, value)
            # The key is part of the verified fingerprint, so a replacement
            # un-verifies the endpoint on its own. Dropping the row as well keeps
            # the surface from showing a sample reply that a different credential
            # produced, which reads as reassurance for a state nobody proved.
            await automation_store.clear_provider_verification(endpoint.provider)
            request.app[keys.LLM_CAPABILITIES].clear(endpoint.provider)
            forget_llm_readiness(request.app)
            return json_response({"ok": True, "status": store.status(secret_name)})
        if operation == "clear":
            store.clear(secret_name)
            await automation_store.clear_provider_verification(endpoint.provider)
            request.app[keys.LLM_CAPABILITIES].clear(endpoint.provider)
            forget_llm_readiness(request.app)
            return json_response({"ok": True, "status": store.status(secret_name)})
        raise ValueError("operation must be test, set, replace, or clear")
    except (OpenRouterError, SecretStoreError) as exc:
        return json_response(
            {"error": str(exc), "status": store.status("openrouter_api_key")}, 422
        )


def _verification_model(
    endpoint: LlmEndpoint, catalog_ids: list[str], config: Config
) -> str:
    """Which model one proving completion should go to.

    The endpoint's own pin wins wherever there is one: that is the model it will
    actually serve, so proving anything else proves the wrong thing.

    Otherwise the endpoint publishes a catalog and the pin is deliberately blank,
    and the choice is only about which id makes the cheapest honest probe. The
    install's cheap model is preferred when the catalog has it - it is the one a
    reader already chose for high-volume work, so a failure here is informative
    rather than incidental - and the first catalogued id is the fallback.

    An empty string means neither existed, and `verify` refuses with its own
    message rather than this guessing at one.
    """
    if endpoint.model_override:
        return endpoint.model_override
    catalogued = set(catalog_ids)
    for preferred in (config.openrouter_cheap_model, config.openrouter_standard_model):
        if preferred and preferred in catalogued:
            return preferred
    return catalog_ids[0] if catalog_ids else ""


def _no_model_reason(endpoint: LlmEndpoint, probe: CatalogProbe) -> str:
    """Why there was no model to send a proving completion to.

    Two states hide under one empty catalog and they want opposite fixes, so this
    says which one happened. A fetch that was *refused* - the common case being an
    endpoint that wants a credential nobody has stored - is a problem with the URL
    or the key, and reporting it as "an exact model id is required" points at the
    one field that is correctly blank.
    """
    if probe.error:
        return (
            f"Could not read a model catalog at {endpoint.catalog_url}: {probe.error}. "
            "Fix the catalog URL or the API key above, or name the one model this "
            "endpoint serves."
        )
    return (
        "This endpoint publishes no model catalog, so it needs the one model it "
        "serves named above before it can be verified."
    )


async def verify_automation_provider(request: web.Request) -> web.Response:
    """Prove one configured endpoint with a single completion, and record it.

    The output comes back rather than a bare ok, because "reachable" and "usable"
    are different findings and only the words separate them - a chat template
    echoing its own scaffolding, or a model answering in the wrong language,
    passes every check a boolean could make.

    A failure records nothing. The previous verification, if any, is left exactly
    as it was: an endpoint that worked yesterday and is unreachable this minute
    has not been disproven, and deleting the record here would turn a network
    blip into a Project-wide switch-off.
    """
    body = await request.json() if request.can_read_body else {}
    provider: OpenRouterClient = request.app[keys.OPENROUTER]
    store: PlatformSecretStore = request.app[keys.SECRET_STORE]
    automation_store: AutomationStore = request.app[keys.AUTOMATION_STORE]
    capability_store = request.app[keys.LLM_CAPABILITIES]
    config: Config = request.app[keys.CONFIG]
    endpoint = _unproven_endpoint(request, body)
    # Probed before the completion rather than after, to answer the one question
    # the completion cannot: *which model to send it to*. An endpoint that
    # publishes a catalog does not need its single-model field filled in, so
    # requiring one in order to prove that was a dead end - the field the act of
    # verifying makes unnecessary was the field blocking the verify.
    #
    # Probing early is safe because nothing is *recorded* until the completion
    # succeeds. A probe against an unreachable host reports `none`, and durably
    # pinning a capable endpoint to the pessimistic profile over one bad minute
    # is the thing that would matter - the same reasoning that makes a failed
    # verification record nothing at all.
    probe = await provider.catalog_probe(endpoint=endpoint)
    try:
        model = _verification_model(endpoint, probe.ids, config)
        if not model:
            # Said here rather than left to `verify`, which can only report that a
            # model is required and cannot know that the reason none was available
            # is a catalog fetch the endpoint refused. Pointing at the model field
            # over a 401 sends the reader to the wrong control entirely.
            raise OpenRouterError(_no_model_reason(endpoint, probe))
        result = await provider.verify(endpoint=endpoint, model=model)
    except (OpenRouterError, ValueError) as exc:
        record = await automation_store.provider_verification(endpoint.provider)
        return json_response(
            {
                "ok": False,
                "provider": endpoint.provider,
                "error": str(exc),
                "verification": llm_verification_state(
                    endpoint, api_key=store.get(endpoint.secret_name), record=record
                ),
                "llm": (await _llm_readiness(request)).as_dict(),
            },
            422,
        )
    # Recorded only now the completion has proved the endpoint answers at all.
    capabilities = EndpointCapabilities(
        catalog=probe.shape,
        reports_cost=result.reports_cost,
        reports_cache=result.reports_cache,
    )
    stored = await automation_store.record_provider_verification(
        provider=endpoint.provider,
        fingerprint=endpoint.fingerprint(store.get(endpoint.secret_name)),
        base_url=endpoint.origin,
        model=result.requested_model,
        resolved_model=result.resolved_model,
        sample=result.output,
        latency_ms=result.latency_ms,
        capabilities=capabilities.as_dict(),
    )
    capability_store.set(endpoint.provider, capabilities)
    # Re-resolved so everything below reports the endpoint as it is *now*
    # permitted to behave, rather than as the deliberately unproven shape the
    # probe itself had to run against.
    endpoint = _requested_endpoint(request, body)
    # The cached catalog belongs to whichever endpoint was last asked, and a
    # verification is exactly the moment that changed. Leaving it meant the model
    # pickers offered the *previous* endpoint's models until somebody thought to
    # press Refresh - and every one of them would then read as absent from the new
    # catalog, which is the opposite of the truth. Failure is not fatal here: the
    # endpoint is proven either way, and a stale catalog is a worse reason to
    # report a successful verification as failed.
    if endpoint.supports_model_catalog:
        try:
            await automation_store.cache_models(await provider.models(endpoint=endpoint))
        except OpenRouterError as exc:
            log.info("catalog refresh after verification failed: %s", exc)
    forget_llm_readiness(request.app)
    await request.app[keys.EVENTS].emit(
        "llm_provider_verified",
        source="user",
        provider=endpoint.provider,
        model=result.requested_model,
    )
    log.info(
        "llm provider verified provider=%s origin=%s model=%s latency_ms=%s",
        endpoint.provider,
        endpoint.origin,
        result.requested_model,
        result.latency_ms,
    )
    return json_response(
        {
            "ok": True,
            "provider": endpoint.provider,
            "output": result.output,
            "requested_model": result.requested_model,
            "resolved_model": result.resolved_model,
            "latency_ms": result.latency_ms,
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
            "cost_usd": result.cost_usd,
            "capabilities": capabilities.as_dict(),
            # An empty reply is normally the finding a verify exists to surface.
            # This one case is not: a reasoning model can spend the probe's whole
            # output budget thinking, and reporting that as "the endpoint answered
            # with nothing" would condemn a working endpoint for the size of the
            # question rather than for anything it did.
            "spent_budget_reasoning": result.spent_budget_reasoning,
            "verification": llm_verification_state(
                endpoint, api_key=store.get(endpoint.secret_name), record=stored
            ),
            "llm": (await _llm_readiness(request)).as_dict(),
        }
    )


async def refresh_automation_models(request: web.Request) -> web.Response:
    store: AutomationStore = request.app[keys.AUTOMATION_STORE]
    try:
        models = await request.app[keys.OPENROUTER].models()
        await store.cache_models(models)
    except OpenRouterError as exc:
        await store.record_model_error(str(exc))
        return json_response({"error": str(exc), **await store.model_cache()}, 422)
    return json_response(await store.model_cache())


async def automation_notifications(request: web.Request) -> web.Response:
    return json_response(
        {
            "items": await request.app[keys.AUTOMATION_STORE].notifications(
                unread=request.query.get("unread") == "1",
                limit=int(request.query.get("limit", 200)),
            )
        }
    )


async def patch_automation_notification(request: web.Request) -> web.Response:
    body = await request.json()
    changed = await request.app[keys.AUTOMATION_STORE].mark_notification(
        request.match_info["notification_id"], bool(body.get("read", True))
    )
    if not changed:
        raise NotFound(request.match_info["notification_id"], kind="notification")
    return json_response({"ok": True})


async def patch_automation_notifications(request: web.Request) -> web.Response:
    """Bulk read/unread over the whole attention inbox (the drawer's "clear all")."""
    body = await request.json()
    changed = await request.app[keys.AUTOMATION_STORE].mark_all_notifications(
        bool(body.get("read", True))
    )
    return json_response({"ok": True, "changed": changed})


#: "The caller did not mention this field", which JSON cannot spell and which a
#: three-position control needs kept apart from an explicit null.
_UNSET: Any = object()


def _global_allow(config: Config) -> dict[str, bool]:
    """The install-wide ceiling as `resolve` consumes it."""
    return effective_global_allow(
        config.automation_global_allow,
        scan_timeline_enabled=config.scan_timeline_enabled,
    )


def _project_defaults(config: Config | None) -> dict[str, bool]:
    """The inherited default template as `resolve` consumes it.

    Without a `config` this is the registry's own answer, which is what a caller
    with no install to consult should get - the same shape `_global_allow`'s
    absence means permissive.
    """
    return install_defaults(config.automation_project_defaults if config is not None else None)


def _automation_registry_payload(config: Config | None = None) -> list[dict[str, Any]]:
    """The enablement registry as every opt-in surface receives it.

    With a `config`, each entry also answers whether the install-wide ceiling
    allows it anywhere at all (`globally_allowed` - the id itself and its whole
    dependency closure), so a toggle surface and a grant gate render the ceiling
    from the same resolution the daemon enforces, and what a Project that never
    wrote the id down inherits (`install_default`, the merge of the operator's
    template with the registry's own). `install_switch` names the dedicated
    `Config` boolean where one exists, because those rows' global toggle writes
    that key and never an `automation_global_allow` entry.

    Both keys are absent without a `config` rather than defaulted, so a surface
    can tell "this install says nothing" from "this install says no": a form that
    rendered a missing answer as `false` would show every inherited automation as
    off, which is the reading the whole inheritance layer exists to remove.
    """
    allow = _global_allow(config) if config is not None else None
    defaults = _project_defaults(config) if config is not None else None

    def allowed(automation_id: str) -> bool:
        if allow is None:
            return True
        closure = {automation_id, *dependency_closure(automation_id)}
        return all(allow.get(item, True) for item in closure)

    return [
        {
            "id": automation.id,
            "kind": automation.kind,
            "label": automation.label,
            # The matrix block this row is drawn in (`FAMILIES`), what it is about
            # rather than what it depends on.
            "family": automation.family,
            "requires": list(automation.requires),
            "implemented": automation.implemented,
            # Whether switching this on can cost money. Read by the toggle surface and
            # by every gate that offers it, so "free" and "spends" are one fact from
            # one source rather than a claim each surface makes for itself.
            "spends": automation.spends,
            # Whether it is inert without a proven model provider. Separate from
            # `spends` because a local endpoint is a dependency without a bill.
            "needs_llm": automation.needs_llm,
            # Whether a Project that never wrote this id down has it on. The
            # toggle surface needs it to render an unset checkbox as checked -
            # and to write an explicit `false` on untick, since deleting the
            # key would just fall back to the default it is trying to leave.
            "default_on": automation.default_on,
            "install_switch": DEDICATED_INSTALL_SWITCHES.get(automation.id),
            **({"globally_allowed": allowed(automation.id)} if allow is not None else {}),
            **(
                {"install_default": bool(defaults.get(automation.id))}
                if defaults is not None
                else {}
            ),
        }
        # Registry order, not the id's spelling: the matrix keeps this order inside
        # a family, which is how the session titler stays directly above the
        # re-titler rather than wherever "c" sorts against "s".
        for automation in AUTOMATION_REGISTRY.values()
    ]


async def _project_automation_state(  # type: ignore[no-untyped-def]
    project,
    *,
    llm: LlmReadiness | None = None,
    global_allow: dict[str, bool] | None = None,
    install: Config | None = None,
) -> dict[str, Any]:
    """One project's opt-in table, resolved against the registry DAG.

    `llm` is the install-wide provider verdict. It is threaded in rather than
    fetched here so the fleet matrix resolves every Project against one reading
    instead of asking the same question per row, and so the payload can carry
    the reason verbatim: `unverified` says which switches are held back, and
    `llm.reason` is the sentence the surface renders instead of leaving them
    looking simply off.

    `global_allow` is the install-wide ceiling, threaded on the same terms.
    `globally_disabled` carries the requested ids it turns off, so the toggle
    surface greys them with the right fix (global policy) instead of rendering
    them as merely off for this Project.

    `install` is the daemon `Config`, needed for the agent authority layers and
    threaded rather than fetched for the same reason as the two above. Absent it
    reports the Project layer alone, which is what the pre-2026-08-29 callers
    saw.
    """
    identity = _registered_identity(project)
    stored = await read_project_config(project.root, project=identity)
    status = str(stored["status"])
    values = stored["values"] if status in {"ready", "read-only"} else {}
    requested = {
        key: bool(value)
        for key, value in (values.get("automations") or {}).items()
        if key in AUTOMATION_REGISTRY
    }
    resolution = resolve_automation_config(
        requested,
        _project_defaults(install),
        llm_ready=llm.ready if llm is not None else True,
        global_allow=global_allow,
    )
    def _stored_authority(_root: str | Path, field: str) -> tuple[str | None, bool]:
        """Resolve authority against the values already read above.

        A closure rather than another file read: the matrix asks this for every
        field of every Project in the fleet, and re-opening `config.toml` five
        times per row would turn one read into six.
        """
        value = values.get(field)
        return (value if isinstance(value, str) else None), status != "malformed"

    return {
        "project_id": project.id,
        "revision": stored["revision"],
        "status": status,
        "requested": requested,
        "enabled": sorted(resolution.enabled),
        "blocked": {key: list(value) for key, value in resolution.blocked.items()},
        "unverified": sorted(resolution.unverified),
        "globally_disabled": sorted(resolution.globally_disabled),
        "llm": llm.as_dict() if llm is not None else None,
        # Two readings again, for the same reason the authority pair below carries
        # two: `scan_timeline_auto_enable` is what the daemon will actually do
        # (the install default layered under the Project's own value, so every
        # existing reader keeps getting a plain boolean that is *true*), and
        # `scan_timeline_auto_enable_own` is null where the Project said nothing
        # - which is what lets its control offer "Follow global" rather than
        # pinning the inherited value the moment anything else on the row is
        # edited.
        "scan_timeline_auto_enable": resolve_scan_auto_enable(
            values.get("scan_timeline_auto_enable"),
            default=install.scan_timeline_auto_enable_default if install else False,
        ),
        "scan_timeline_auto_enable_own": (
            values["scan_timeline_auto_enable"]
            if isinstance(values.get("scan_timeline_auto_enable"), bool)
            else None
        ),
        # The same two readings for the titler's refinement count, the second
        # Project field that qualifies an opt-in rather than being one.
        "title_refinements": resolve_title_refinements(
            values.get("title_refinements"),
            default=install.title_refinements_default
            if install
            else TITLE_REFINEMENTS_DEFAULT,
        ),
        "title_refinements_own": (
            values["title_refinements"]
            if isinstance(values.get("title_refinements"), int)
            and not isinstance(values.get("title_refinements"), bool)
            else None
        ),
        # Two readings, because the matrix needs both and cannot derive one from
        # the other: `authority` is what this repository's file explicitly says
        # (None where it left the field alone, which is what makes "Follow
        # global" a distinct third position rather than a synonym for the
        # default), and `authority_effective` is what the daemon will actually
        # enforce once the install default and ceiling are layered on. A row
        # showing only the second could not tell a pinned Project from one
        # inheriting the same value.
        "authority": {
            name: (values.get(name) if isinstance(values.get(name), str) else None)
            for name in AUTHORITY_FIELDS
        },
        "authority_effective": {
            name: resolve_authority(
                install, project.root, name, read_project=_stored_authority
            )
            for name in AUTHORITY_FIELDS
        },
    }


async def get_project_automations(request: web.Request) -> web.Response:
    """The per-project control-plane opt-in state, with its dependency graph.

    The registry ships with the response deliberately: a toggle surface has to
    show *why* a consumer is unavailable ("dead-end memory needs Tier 0 and the
    scan timeline"), and a flat checkbox list cannot. `implemented` marks ids
    that are reserved but have no code behind them yet, so the UI never presents
    a placeholder as ready to switch on.
    """
    project = _observations_project(request)
    config: Config = request.app[keys.CONFIG]
    state = await _project_automation_state(
        project,
        llm=await _llm_readiness(request),
        global_allow=_global_allow(config),
        install=config,
    )
    return json_response({**state, "automations": _automation_registry_payload(config)})


async def automation_project_matrix(request: web.Request) -> web.Response:
    """Which Projects opted into which automations — the dashboard's fleet answer.

    The global switches say whether the pipeline *may* run; whether anything
    actually runs is decided per Project in each `.swe-mux/config.toml`. This
    read aggregates those files so the Automation dashboard can answer "what is
    running where" and link to the Project settings that change it. Read-only by
    design: the write path stays the revision-checked per-Project route, so this
    surface can never race an open Project editor.
    """
    llm = await _llm_readiness(request)
    config: Config = request.app[keys.CONFIG]
    allow = _global_allow(config)
    rows = [
        {
            **await _project_automation_state(
                project, llm=llm, global_allow=allow, install=config
            ),
            "project_name": project.name,
        }
        for project in request.app[keys.PROJECTS].ordered_projects()
    ]
    return json_response(
        {
            "automations": _automation_registry_payload(config),
            "projects": rows,
            # The install-wide ceiling, as stored: the map the Global column
            # writes, and the dedicated switches beside it. The per-entry
            # `globally_allowed` above is the *resolved* reading (closure
            # included); this is what the toggles edit.
            "global_allow": dict(config.automation_global_allow),
            # The inherited default template, as stored: what the Default column
            # writes. The per-entry `install_default` above is the *resolved*
            # reading (the registry's own defaults merged in, closure completed);
            # this is the operator's own map, so unticking a row it never named
            # is a write that changes nothing rather than one that pins the
            # registry's answer into the file.
            "project_defaults": dict(config.automation_project_defaults),
            # The default under the one Project field that qualifies an opt-in
            # rather than being one. Beside `project_defaults` rather than inside
            # `install_switches`: that map is exactly the dedicated per-automation
            # ceilings, and a fifth key with different semantics in it is how a
            # payload starts meaning two things.
            "scan_timeline_auto_enable_default": config.scan_timeline_auto_enable_default,
            "title_refinements_default": config.title_refinements_default,
            "title_refinements_max": TITLE_REFINEMENTS_MAX,
            "install_switches": {
                "automation_enabled": config.automation_enabled,
                "scan_timeline_enabled": config.scan_timeline_enabled,
                "scheduled_runs_enabled": config.scheduled_runs_enabled,
                "land_queue_enabled": config.land_queue_enabled,
            },
            # The agent authority rows' Global cell, in the same two readings the
            # per-Project rows carry. `authority_default` is what an unset field
            # inherits - always a concrete level, since it falls through to the
            # built-in - and `authority_ceiling` is null unless the operator
            # locked the row, so the surface can tell "the default happens to be
            # draft" from "nothing here may be anything but draft".
            "authority_fields": [
                {
                    "field": name,
                    "label": entry.label,
                    "levels": list(entry.levels),
                    "builtin": entry.builtin,
                    "gated_by": entry.gated_by,
                }
                for name, entry in AUTHORITY_FIELDS.items()
            ],
            "authority_default": {
                name: install_default(config, name) for name in AUTHORITY_FIELDS
            },
            "authority_ceiling": {
                name: install_ceiling(config, name) for name in AUTHORITY_FIELDS
            },
        }
    )


async def put_project_automations(request: web.Request) -> web.Response:
    """Replace a project's opt-in table.

    Writes through the ordinary project-config path, so the file stays the source
    of truth and a concurrent edit is still guarded.

    Two guard shapes, matching `PUT /api/project/config`. `base` - what the caller
    believed the `automations` table, `scan_timeline_auto_enable`, and any
    authority fields it is changing held - writes only those fields and collides
    only when one of them actually moved, which
    is what the toggle list in the Projects editor needs: it shares one file with
    the authority table and the portable options beside it, and a whole-file guard
    made every one of those writes read as an external edit to the other two. A
    bare `revision` keeps the older whole-file check.
    """
    project = _observations_project(request)
    identity = _registered_identity(project)
    body = await request.json()
    requested = body.get("automations")
    if not isinstance(requested, dict):
        raise ValueError("automations must be a table of boolean opt-ins")
    unknown = sorted(set(requested) - set(AUTOMATION_REGISTRY))
    if unknown:
        raise ValueError(f"unknown automations: {', '.join(unknown)}")
    unimplemented = sorted(
        key
        for key, value in requested.items()
        if value and not AUTOMATION_REGISTRY[key].implemented
    )
    if unimplemented:
        # Refusing beats a toggle that reads as on and does nothing.
        return json_response(
            {
                "error": f"not implemented yet: {', '.join(unimplemented)}",
                "code": "automation_not_implemented",
            },
            409,
        )
    # Three positions, so a sentinel rather than `None`: an absent key leaves the
    # field alone, an explicit `null` *removes* it (the "Follow global" position,
    # the same spelling the authority fields use), and a boolean pins it. Reading
    # `null` as "leave alone" would make returning to the inherited value
    # unsayable over this route, which is how the field became a per-Project
    # write nobody could undo in one place.
    auto_enable = body.get("scan_timeline_auto_enable", _UNSET)
    if auto_enable is not _UNSET and auto_enable is not None and not isinstance(auto_enable, bool):
        raise ValueError("scan_timeline_auto_enable must be a boolean or null")
    refinements = body.get("title_refinements", _UNSET)
    if refinements is not _UNSET and refinements is not None and (
        isinstance(refinements, bool)
        or not isinstance(refinements, int)
        or not 0 <= refinements <= TITLE_REFINEMENTS_MAX
    ):
        raise ValueError(
            f"title_refinements must be an integer from 0 to {TITLE_REFINEMENTS_MAX} or null"
        )
    # Agent authority arrives on the same write as the opt-ins it qualifies,
    # because the matrix edits both and a Project's file is one revision. A
    # field mapped to None is the "Follow global" position: the key is *removed*
    # rather than written with the global's current value, which is the whole
    # difference between a Project that inherits and one that happens to agree
    # with the global today.
    raw_authority = body.get("authority")
    if raw_authority is not None and (
        not isinstance(raw_authority, dict)
        or any(not isinstance(key, str) for key in raw_authority)
        or any(
            value is not None and not isinstance(value, str) for value in raw_authority.values()
        )
    ):
        raise ValueError("authority must map authority fields to a level or null")
    authority: dict[str, Any] = dict(raw_authority or {})
    unknown_authority = sorted(set(authority) - set(AUTHORITY_FIELDS))
    if unknown_authority:
        raise ValueError(f"unknown authority fields: {', '.join(unknown_authority)}")
    invalid_levels = sorted(
        f"{key} ({value})"
        for key, value in authority.items()
        if value is not None and AUTHORITY_FIELDS[key].rank(str(value)) < 0
    )
    if invalid_levels:
        raise ValueError(f"invalid authority levels: {', '.join(invalid_levels)}")
    # `scan_timeline_daily_budget_usd` is deliberately not accepted here any
    # more: it is one global setting in Settings -> Automation. A body that
    # still sends it is ignored rather than refused, and the retired key is
    # dropped from the file on this write.
    #
    # Every explicit value is persisted, `false` included. It used to be stripped
    # as noise wherever absence already meant off, which was true while absence
    # could only mean the registry's own default. It stopped being true when the
    # install gained a default template: absence now means *inherit*, so a
    # stripped `false` is not a Project that is off, it is a Project that will
    # silently come on the moment the operator defaults the id on. "Off" has to
    # stay sayable, and the file is the only place it can be said.
    automations = {key: bool(value) for key, value in requested.items()}
    changes: dict[str, Any] = {"automations": automations, **authority}
    # Auto-enable is meaningless without the permission it rides on, and leaving
    # it set would silently re-arm every run the moment the Project is opted in
    # again. Opting out clears it - back to inheriting, which is the only thing
    # clearing can mean now that the install has a default for it too.
    effective_requested = requested_from_config(
        automations, _project_defaults(request.app[keys.CONFIG])
    )
    if "scan_timeline" not in effective_requested:
        changes["scan_timeline_auto_enable"] = None
    elif auto_enable is not _UNSET:
        changes["scan_timeline_auto_enable"] = auto_enable
    # The refinement count rides the titler the same way: with the titler opted
    # out it is meaningless, and clearing it means inheriting.
    if "session_titler" not in effective_requested:
        changes["title_refinements"] = None
    elif refinements is not _UNSET:
        changes["title_refinements"] = refinements
    base = body.get("base")
    if isinstance(base, dict):
        # `ProjectConfigConflict` is answered by the error middleware, which names
        # the field that moved instead of blaming the whole file.
        await merge_project_config(project.root, changes, dict(base), project=identity)
    else:
        current = await read_project_config(project.root, project=identity)
        values = dict(current["values"]) if current["status"] != "malformed" else {}
        for key, value in changes.items():
            if value is None:
                values.pop(key, None)
            else:
                values[key] = value
        try:
            await write_project_config(
                project.root,
                values,
                str(body.get("revision") or current["revision"]),
                project=identity,
            )
        except ValueError as exc:
            if "changed externally" in str(exc):
                return json_response({"error": str(exc), "code": "revision_conflict"}, 409)
            raise
    if gate_cache := request.app.get(keys.AUTOMATION_GATE_CACHE):
        gate_cache.clear()
    if requested.get("scan_timeline") and request.app.get(keys.PROJECT_CONTEXTS) is not None:
        await asyncio.to_thread(
            request.app[keys.PROJECT_CONTEXTS].ensure,
            ProjectContext(project_id=project.id, project_root=project.root),
        )
    await request.app[keys.EVENTS].emit("project_configuration_changed", project_id=project.id)
    return await get_project_automations(request)


ROUTES: tuple[web.RouteDef, ...] = (
    web.get("/api/automation", get_automation_status),
    web.get("/api/automation/rules", get_automation_rules),
    web.put("/api/automation/rules", put_automation_rules),
    web.patch("/api/automation/rules/{rule_id}", patch_automation_rule),
    web.post("/api/automation/dry-run", automation_dry_run),
    web.get("/api/automation/dashboard", automation_dashboard),
    web.get("/api/automation/projects", automation_project_matrix),
    web.get("/api/automation/firings", automation_firings),
    web.get("/api/annotations", list_annotations),
    web.get("/api/automation/provider", automation_provider_status),
    web.post("/api/automation/provider/key", automation_provider_key),
    web.post("/api/automation/provider/verify", verify_automation_provider),
    web.post("/api/automation/provider/models/refresh", refresh_automation_models),
    web.get("/api/automation/notifications", automation_notifications),
    web.patch("/api/automation/notifications", patch_automation_notifications),
    web.patch(
        "/api/automation/notifications/{notification_id}",
        patch_automation_notification,
    ),
    web.get("/api/projects/{project_id}/automations", get_project_automations),
    web.put("/api/projects/{project_id}/automations", put_project_automations),
)
