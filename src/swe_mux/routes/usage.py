"""Provider usage, quota telemetry, and the accounts behind them."""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from aiohttp import web

from .. import (
    app_keys as keys,
)
from ..http_support import json_response
from ..operational_telemetry import OperationalTelemetryStore
from ..provider_accounts import (
    ProviderAccountManager,
)
from ..telemetry_queries import (
    EXPORT_KINDS,
    FILTER_COLUMNS,
    PAGE_EXTRA_FILTERS,
    TOOL_FILTER_COLUMNS,
    clean_filters,
)
from ..usage import UsageManager
from .support import _query_epoch

log = logging.getLogger(__name__)


async def get_usage(request: web.Request) -> web.Response:
    usage: UsageManager = request.app[keys.USAGE]
    return json_response(usage.snapshot())


async def refresh_usage(request: web.Request) -> web.Response:
    usage: UsageManager = request.app[keys.USAGE]
    return json_response(await usage.refresh())


async def clear_usage_cache(request: web.Request) -> web.Response:
    usage: UsageManager = request.app[keys.USAGE]
    await request.app[keys.EVENTS].emit("usage_cache_cleared", source="settings")
    return json_response(usage.clear())


async def operational_telemetry(request: web.Request) -> web.Response:
    telemetry: OperationalTelemetryStore = request.app[keys.TELEMETRY]
    try:
        limit = int(request.query.get("limit", 200))
    except ValueError:
        raise web.HTTPBadRequest(text="limit must be an integer") from None
    return json_response(
        await telemetry.snapshot(
            provider=request.query.get("provider"),
            account_id=request.query.get("account"),
            limit=limit,
        )
    )


def _telemetry_window(request: web.Request) -> tuple[float, float]:
    now = time.time()
    from_ts = _query_epoch(request, "from")
    to_ts = _query_epoch(request, "to")
    start = now - 7 * 86400 if from_ts is None else from_ts
    end = now if to_ts is None else to_ts
    if start >= end:
        raise web.HTTPBadRequest(text="from must be before to")
    return start, end


def _canonical_scope(
    request: web.Request, *, allowed: tuple[str, ...] = FILTER_COLUMNS
) -> dict[str, Any]:
    """Window, cohort, and exact-match filters shared by every v2 aggregate.

    `origin=all` widens the default mux-owned cohort to imported history. The
    filter columns are the ones both the entity tables and the rollup tables carry,
    so a filtered answer is as exact as an unfiltered one.
    """

    start, end = _telemetry_window(request)
    origin = request.query.get("origin", "mux_owned")
    filters = clean_filters(
        {
            "project_id": request.query.get("project"),
            "backend": request.query.get("backend"),
            "model": request.query.get("model"),
            "invocation_layer": request.query.get("layer"),
            "family": request.query.get("family"),
            "status": request.query.get("status"),
            "evidence_quality": request.query.get("evidence"),
        },
        allowed,
    )
    return {
        "from_ts": start,
        "to_ts": end,
        "origin": None if origin == "all" else origin,
        "filters": filters,
    }


def _page_arguments(request: web.Request, kind: str) -> dict[str, Any]:
    """Cursor, limit, and every exact-match filter a detail page of `kind` accepts."""

    scope = _canonical_scope(request, allowed=FILTER_COLUMNS)
    extra = {
        column: request.query.get(alias)
        for column, alias in (
            ("invocation_layer", "layer"),
            ("family", "family"),
            ("status", "status"),
            ("evidence_quality", "evidence"),
            ("raw_name", "tool"),
            ("run_id", "run"),
            ("turn_id", "turn"),
            ("session_id", "session"),
            ("skill_name", "skill"),
            ("invocation_trigger", "trigger"),
            ("framework", "framework"),
            ("successful", "successful"),
            ("query_source", "query_source"),
            ("metric_name", "metric"),
            ("event_type", "event"),
            ("source_kind", "source"),
        )
    }
    filters = dict(scope["filters"])
    filters.update(
        clean_filters(extra, {*FILTER_COLUMNS, *PAGE_EXTRA_FILTERS.get(kind, ())})
    )
    try:
        limit = int(request.query.get("limit", 100))
    except ValueError:
        raise web.HTTPBadRequest(text="limit must be an integer") from None
    return {
        "kind": kind,
        "from_ts": scope["from_ts"],
        "to_ts": scope["to_ts"],
        "origin": scope["origin"],
        "filters": filters,
        "limit": limit,
        "cursor": request.query.get("cursor"),
    }


async def canonical_tool_summary(request: web.Request) -> web.Response:
    """Exact aggregate across every ledger segment in the requested window."""

    service = request.app[keys.CANONICAL_TELEMETRY]
    result = await service.tool_summary(
        **_canonical_scope(request, allowed=TOOL_FILTER_COLUMNS)
    )
    result["collection"] = service.health()
    return json_response(result)


async def canonical_tool_calls(request: web.Request) -> web.Response:
    """Cursor-bounded details; the matching total remains exact and uncapped."""

    service = request.app[keys.CANONICAL_TELEMETRY]
    try:
        result = await service.entity_page(**_page_arguments(request, "tool_calls"))
    except ValueError as exc:
        raise web.HTTPBadRequest(text=str(exc)) from None
    result["matching_calls"] = result["matching"]
    result["collection"] = service.health()
    return json_response(result)


async def canonical_entity_page(request: web.Request) -> web.Response:
    """Newest-first page of runs, turns, skills, verifications, requests, or metrics."""

    kind = request.match_info["kind"].replace("-", "_")
    if kind not in EXPORT_KINDS or kind == "tool_calls":
        raise web.HTTPNotFound(text="unknown telemetry entity kind")
    service = request.app[keys.CANONICAL_TELEMETRY]
    try:
        result = await service.entity_page(**_page_arguments(request, kind))
    except ValueError as exc:
        raise web.HTTPBadRequest(text=str(exc)) from None
    return json_response(result)


async def canonical_workload(request: web.Request) -> web.Response:
    service = request.app[keys.CANONICAL_TELEMETRY]
    result = await service.workload_summary(**_canonical_scope(request))
    result["collection"] = service.health()
    return json_response(result)


async def canonical_skill_summary(request: web.Request) -> web.Response:
    service = request.app[keys.CANONICAL_TELEMETRY]
    result = await service.skill_summary(**_canonical_scope(request))
    result["collection"] = service.health()
    return json_response(result)


async def canonical_verification_summary(request: web.Request) -> web.Response:
    service = request.app[keys.CANONICAL_TELEMETRY]
    result = await service.verification_summary(**_canonical_scope(request))
    result["collection"] = service.health()
    return json_response(result)


async def canonical_metric_summary(request: web.Request) -> web.Response:
    """Provider self-reported counters beside the ledger's own counts, per run."""

    service = request.app[keys.CANONICAL_TELEMETRY]
    result = await service.metric_summary(**_canonical_scope(request))
    result["collection"] = service.health()
    return json_response(result)


async def canonical_tool_audit(request: web.Request) -> web.Response:
    result = await request.app[keys.CANONICAL_TELEMETRY].tool_audit(
        request.match_info["tool_call_id"]
    )
    if result is None:
        raise web.HTTPNotFound(text="unknown canonical tool call")
    return json_response(result)


async def canonical_run_audit(request: web.Request) -> web.Response:
    result = await request.app[keys.CANONICAL_TELEMETRY].run_audit(request.match_info["run_id"])
    if result is None:
        raise web.HTTPNotFound(text="unknown canonical run")
    return json_response(result)


async def canonical_turn_audit(request: web.Request) -> web.Response:
    result = await request.app[keys.CANONICAL_TELEMETRY].turn_audit(
        request.match_info["turn_id"]
    )
    if result is None:
        raise web.HTTPNotFound(text="unknown canonical turn")
    return json_response(result)


async def canonical_inefficiencies(request: web.Request) -> web.Response:
    service = request.app[keys.CANONICAL_TELEMETRY]
    result = await service.inefficiency_findings(
        **_canonical_scope(request, allowed=TOOL_FILTER_COLUMNS),
        include_reviewed=request.query.get("reviewed", "1") != "0",
    )
    result["collection_health"] = service.health()
    return json_response(result)


async def review_inefficiency(request: web.Request) -> web.Response:
    """Record the operator's verdict on a finding; the only feedback channel there is."""

    body = await request.json()
    if not isinstance(body, dict):
        raise web.HTTPBadRequest(text="body must be an object")
    service = request.app[keys.CANONICAL_TELEMETRY]
    try:
        result = await service.review_finding(
            finding_key=str(body.get("finding_key") or ""),
            kind=str(body.get("kind") or "unknown")[:80],
            verdict=str(body.get("verdict") or ""),
            note=(str(body["note"]) if body.get("note") is not None else None),
        )
    except ValueError as exc:
        raise web.HTTPBadRequest(text=str(exc)) from None
    return json_response(result)


async def canonical_compare(request: web.Request) -> web.Response:
    """Cohorts split on one dimension, comparable only when the rest is fixed."""

    service = request.app[keys.CANONICAL_TELEMETRY]
    split = request.query.get("split", "model")
    try:
        result = await service.compare_cohorts(split=split, **_canonical_scope(request))
    except ValueError as exc:
        raise web.HTTPBadRequest(text=str(exc)) from None
    result["collection"] = service.health()
    return json_response(result)


async def canonical_quality(request: web.Request) -> web.Response:
    service = request.app[keys.CANONICAL_TELEMETRY]
    result = await service.quality_summary(**_canonical_scope(request))
    result["collection"] = service.health()
    return json_response(result)


async def canonical_compactions(request: web.Request) -> web.Response:
    service = request.app[keys.CANONICAL_TELEMETRY]
    result = await service.compaction_summary(**_canonical_scope(request))
    result["collection"] = service.health()
    return json_response(result)


async def canonical_parsers(request: web.Request) -> web.Response:
    """Which provider event names each harness version has sent, understood or not."""

    service = request.app[keys.CANONICAL_TELEMETRY]
    return json_response(
        {
            "signatures": await service.parser_signatures(),
            "schema": await service.schema_status(),
            "collection": service.health(),
        }
    )


async def canonical_shadow(request: web.Request) -> web.Response:
    """Legacy tool table against the canonical ledger, every disagreement classified."""

    start, end = _telemetry_window(request)
    service = request.app[keys.CANONICAL_TELEMETRY]
    result = await service.shadow_comparison(from_ts=start, to_ts=end)
    result["legacy_dashboard_enabled"] = bool(
        request.app[keys.CONFIG].canonical_telemetry_legacy_dashboard_enabled
    )
    result["collection"] = service.health()
    return json_response(result)


async def canonical_reconcile(request: web.Request) -> web.Response:
    """One direct native-store reconciliation pass now, and its result."""

    service = request.app[keys.CANONICAL_TELEMETRY]
    return json_response(await service.reconcile_now())


def _csv_cell(value: Any) -> str:
    if value is None:
        return ""
    text = str(value)
    if any(character in text for character in ',"\r\n'):
        return '"' + text.replace('"', '""') + '"'
    return text


async def canonical_export(request: web.Request) -> web.StreamResponse:
    """Stream every matching canonical row as JSONL or CSV, provenance included.

    Pages are pulled from the ledger's worker one at a time, so a million-row
    export never sits in memory and never holds the event loop; the response is
    the whole window, not a capped slice.
    """

    kind = request.match_info["kind"].replace("-", "_")
    if kind not in EXPORT_KINDS:
        raise web.HTTPNotFound(text="unknown telemetry export kind")
    output = request.query.get("format", "jsonl")
    if output not in {"jsonl", "csv"}:
        raise web.HTTPBadRequest(text="format must be jsonl or csv")
    scope = _canonical_scope(request)
    service = request.app[keys.CANONICAL_TELEMETRY]
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(scope["from_ts"]))
    response = web.StreamResponse(
        headers={
            "Content-Type": (
                "application/x-ndjson; charset=utf-8"
                if output == "jsonl"
                else "text/csv; charset=utf-8"
            ),
            "Content-Disposition": (
                f'attachment; filename="swe-mux-telemetry-{kind}-{stamp}.{output}"'
            ),
            "Cache-Control": "no-store",
        }
    )
    await response.prepare(request)
    cursor: str | None = None
    columns: list[str] | None = None
    # unsupervised-loop-ok: one response's page loop, ended by the ledger's cursor
    while True:
        page = await service.export_page(kind=kind, cursor=cursor, limit=2000, **scope)
        lines: list[str] = []
        for row in page["items"]:
            if output == "jsonl":
                lines.append(json.dumps(row, separators=(",", ":"), default=str))
                continue
            if columns is None:
                columns = list(row)
                lines.append(",".join(columns))
            lines.append(",".join(_csv_cell(row.get(column)) for column in columns))
        if lines:
            await response.write(("\n".join(lines) + "\n").encode("utf-8"))
        cursor = page["next_cursor"]
        if cursor is None:
            break
    await response.write_eof()
    return response


async def quota_telemetry_series(request: web.Request) -> web.Response:
    telemetry: OperationalTelemetryStore = request.app[keys.TELEMETRY]
    try:
        limit = int(request.query.get("limit", 3650))
    except ValueError:
        raise web.HTTPBadRequest(text="limit must be an integer") from None
    try:
        result = await telemetry.quota_series(
            provider=request.query.get("provider"),
            account_id=request.query.get("account"),
            since=_query_epoch(request, "since"),
            until=_query_epoch(request, "until"),
            resolution=request.query.get("resolution", "daily"),
            limit=limit,
        )
    except ValueError as exc:
        raise web.HTTPBadRequest(text=str(exc)) from None
    return json_response(result)


async def review_quota_resets(request: web.Request) -> web.Response:
    body = await request.json()
    resolution = str(body.get("resolution") or "")
    raw_ids = body.get("ids")
    if not isinstance(raw_ids, list):
        raise web.HTTPBadRequest(text="ids must be a list of quota reset ids")
    telemetry: OperationalTelemetryStore = request.app[keys.TELEMETRY]
    try:
        reviewed = await telemetry.review_quota_resets([str(item) for item in raw_ids], resolution)
    except ValueError as exc:
        raise web.HTTPBadRequest(text=str(exc)) from None
    except KeyError as exc:
        raise web.HTTPNotFound(text=f"unknown quota reset {exc.args[0]}") from None
    await request.app[keys.EVENTS].emit(
        "quota_reset_reviewed",
        source="user",
        reset_ids=[item["id"] for item in reviewed],
        providers=sorted({str(item["provider"]) for item in reviewed}),
        resolution=resolution,
    )
    return json_response({"items": reviewed, "reset_alert": await telemetry.reset_summary()})


async def _enriched_accounts(
    request: web.Request, snapshot: dict[str, Any]
) -> dict[str, Any]:
    """One provider-accounts payload, whichever call produced the snapshot.

    Every route here hands the browser a whole `ProviderAccountsStatus` and the
    browser replaces its state with it wholesale, so a mutation that answered
    with the bare manager snapshot dropped the two things only this function adds
    - the durable quota readings and the unreviewed reset alert - until the next
    poll came round sixty seconds later.
    """
    telemetry: OperationalTelemetryStore = request.app[keys.TELEMETRY]
    latest = await telemetry.latest_quota_by_account()
    for account in snapshot["accounts"]:
        conflict = account.get("conflict")
        if conflict and not conflict.get("is_primary"):
            # Durable samples for a duplicate slot are the primary account's
            # numbers; showing them again is the mirrored-usage illusion.
            continue
        if account["id"] in latest:
            account["quota"] = latest[account["id"]]
    snapshot["reset_alert"] = await telemetry.reset_summary()
    return snapshot


async def get_provider_accounts(request: web.Request) -> web.Response:
    accounts: ProviderAccountManager = request.app[keys.PROVIDER_ACCOUNTS]
    return json_response(
        await _enriched_accounts(request, await accounts.reconcile_current())
    )


async def get_provider_account_audit(request: web.Request) -> web.Response:
    accounts: ProviderAccountManager = request.app[keys.PROVIDER_ACCOUNTS]
    limit = max(1, min(1000, int(request.query.get("limit") or 100)))
    return json_response({"items": accounts.audit_entries(limit)})


async def refresh_provider_accounts(request: web.Request) -> web.Response:
    accounts: ProviderAccountManager = request.app[keys.PROVIDER_ACCOUNTS]
    body = await request.json() if request.can_read_body else {}
    return json_response(
        await _enriched_accounts(
            request, await accounts.refresh(body.get("account_id"), force_identity_probe=True)
        )
    )


async def verify_provider_accounts(request: web.Request) -> web.Response:
    accounts: ProviderAccountManager = request.app[keys.PROVIDER_ACCOUNTS]
    return json_response(await _enriched_accounts(request, await accounts.verify_identities()))


async def capture_provider_account(request: web.Request) -> web.Response:
    accounts: ProviderAccountManager = request.app[keys.PROVIDER_ACCOUNTS]
    body = await request.json() if request.can_read_body else {}
    return json_response(
        await _enriched_accounts(
            request,
            await accounts.capture_current(
                request.match_info["provider"],
                label=body.get("label"),
                replace_id=body.get("replace_id"),
            ),
        )
    )


async def login_provider_account(request: web.Request) -> web.Response:
    """Start an interactive sign-in; the response is the state, not the outcome.

    This used to block for as long as the provider CLI took, up to five minutes.
    The reply now names a sign-in that is *running*, and the caller watches it in
    `login` on any subsequent accounts read.
    """
    accounts: ProviderAccountManager = request.app[keys.PROVIDER_ACCOUNTS]
    body = await request.json() if request.can_read_body else {}
    return json_response(
        await _enriched_accounts(
            request,
            await accounts.start_login(
                request.match_info["provider"],
                label=body.get("label"),
                replace_id=body.get("replace_id"),
            ),
        )
    )


async def dismiss_provider_login(request: web.Request) -> web.Response:
    accounts: ProviderAccountManager = request.app[keys.PROVIDER_ACCOUNTS]
    return json_response(
        await _enriched_accounts(
            request, await accounts.dismiss_login(request.match_info["provider"])
        )
    )


async def patch_provider_account(request: web.Request) -> web.Response:
    accounts: ProviderAccountManager = request.app[keys.PROVIDER_ACCOUNTS]
    body = await request.json()
    return json_response(
        await _enriched_accounts(
            request,
            await accounts.rename(
                request.match_info["provider"],
                request.match_info["account_id"],
                str(body["label"]),
            ),
        )
    )


async def select_provider_account(request: web.Request) -> web.Response:
    accounts: ProviderAccountManager = request.app[keys.PROVIDER_ACCOUNTS]
    return json_response(
        await _enriched_accounts(
            request,
            await accounts.select(
                request.match_info["provider"],
                request.match_info["account_id"],
            ),
        )
    )


async def adopt_provider_account(request: web.Request) -> web.Response:
    accounts: ProviderAccountManager = request.app[keys.PROVIDER_ACCOUNTS]
    return json_response(
        await _enriched_accounts(
            request,
            await accounts.adopt(
                request.match_info["provider"], request.match_info["account_id"]
            ),
        )
    )


async def purge_provider_account_telemetry(request: web.Request) -> web.Response:
    accounts: ProviderAccountManager = request.app[keys.PROVIDER_ACCOUNTS]
    body = await request.json() if request.can_read_body else {}
    since = body.get("since")
    return json_response(
        await _enriched_accounts(
            request,
            await accounts.purge_telemetry(
                request.match_info["provider"],
                request.match_info["account_id"],
                since=float(since) if since is not None else None,
            ),
        )
    )


async def remove_provider_account(request: web.Request) -> web.Response:
    accounts: ProviderAccountManager = request.app[keys.PROVIDER_ACCOUNTS]
    return json_response(
        await _enriched_accounts(
            request,
            await accounts.remove(
                request.match_info["provider"], request.match_info["account_id"]
            ),
        )
    )


ROUTES: tuple[web.RouteDef, ...] = (
    web.get("/api/usage", get_usage),
    web.post("/api/usage/refresh", refresh_usage),
    web.delete("/api/usage/cache", clear_usage_cache),
    web.get("/api/telemetry/operational", operational_telemetry),
    web.get("/api/telemetry/v2/tools/summary", canonical_tool_summary),
    web.get("/api/telemetry/v2/tools", canonical_tool_calls),
    web.get("/api/telemetry/v2/tools/{tool_call_id}", canonical_tool_audit),
    web.get("/api/telemetry/v2/workload", canonical_workload),
    web.get("/api/telemetry/v2/skills/summary", canonical_skill_summary),
    web.get("/api/telemetry/v2/verifications/summary", canonical_verification_summary),
    web.get("/api/telemetry/v2/metrics/summary", canonical_metric_summary),
    web.get("/api/telemetry/v2/runs/{run_id}", canonical_run_audit),
    web.get("/api/telemetry/v2/turns/{turn_id}", canonical_turn_audit),
    web.get("/api/telemetry/v2/inefficiencies", canonical_inefficiencies),
    web.post("/api/telemetry/v2/inefficiencies/review", review_inefficiency),
    web.get("/api/telemetry/v2/compare", canonical_compare),
    web.get("/api/telemetry/v2/quality", canonical_quality),
    web.get("/api/telemetry/v2/compactions", canonical_compactions),
    web.get("/api/telemetry/v2/parsers", canonical_parsers),
    web.get("/api/telemetry/v2/shadow", canonical_shadow),
    web.post("/api/telemetry/v2/reconcile", canonical_reconcile),
    web.get("/api/telemetry/v2/export/{kind}", canonical_export),
    web.get("/api/telemetry/v2/{kind}", canonical_entity_page),
    web.get("/api/telemetry/quota-series", quota_telemetry_series),
    web.post("/api/telemetry/quota-resets/review", review_quota_resets),
    web.get("/api/provider-accounts", get_provider_accounts),
    web.get("/api/provider-accounts/audit", get_provider_account_audit),
    web.post("/api/provider-accounts/refresh", refresh_provider_accounts),
    web.post("/api/provider-accounts/verify", verify_provider_accounts),
    web.post("/api/provider-accounts/{provider}/capture", capture_provider_account),
    web.post("/api/provider-accounts/{provider}/login", login_provider_account),
    # Three segments after the prefix, so it cannot be read as an `{account_id}`
    # named "login" by the two-segment routes below.
    web.post("/api/provider-accounts/{provider}/login/dismiss", dismiss_provider_login),
    web.patch("/api/provider-accounts/{provider}/{account_id}", patch_provider_account),
    web.post(
        "/api/provider-accounts/{provider}/{account_id}/select",
        select_provider_account,
    ),
    web.post(
        "/api/provider-accounts/{provider}/{account_id}/adopt",
        adopt_provider_account,
    ),
    web.post(
        "/api/provider-accounts/{provider}/{account_id}/purge-telemetry",
        purge_provider_account_telemetry,
    ),
    web.delete("/api/provider-accounts/{provider}/{account_id}", remove_provider_account),
)
