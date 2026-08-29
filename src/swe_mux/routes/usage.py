"""Provider usage, quota telemetry, and the accounts behind them."""

from __future__ import annotations

import logging
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
