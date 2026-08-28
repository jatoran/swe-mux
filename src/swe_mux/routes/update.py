"""The release update check: read the answer, ask again, decline a version.

Three handlers, and the split between them is the whole design. `GET` never
touches the network - it reports what the daily background check already found,
so opening the app, refreshing a phone, or polling this endpoint costs nothing
and can never be the reason a request hangs. Only `POST /api/update/check`
reaches the network, and only on an explicit press.

`update_check.py` holds the reasoning about intervals, schemas, and comparison;
this module is transport. It never raises on any of them: an update check that
could 500 would put a failed network call in front of an operator who did not
ask about the network.
"""

from __future__ import annotations

import logging

from aiohttp import web

from .. import (
    app_keys as keys,
)
from ..http_support import json_response
from ..update_check import UpdateChecker

log = logging.getLogger(__name__)


def _checker(request: web.Request) -> UpdateChecker | None:
    return request.app.get(keys.UPDATE_CHECK)


def _unavailable() -> web.Response:
    """The answer when no checker was built (a minimal or partially-built app).

    Deliberately a 200 carrying `status: "unavailable"` rather than a 404 or a
    503: every consumer of this endpoint is a passive banner, and a client that
    has to distinguish "no update" from "this daemon has no update check" by
    catching an HTTP error would end up rendering an error where the honest
    answer is silence.
    """
    return json_response(
        {
            "enabled": False,
            "status": "unavailable",
            "update_available": False,
            "latest": None,
            "banner": False,
        }
    )


async def get_update(request: web.Request) -> web.Response:
    """What the last check found. Reads state; makes no outbound request."""
    checker = _checker(request)
    if checker is None:
        return _unavailable()
    await checker.ensure_loaded()
    response = json_response(checker.snapshot())
    # The whole value of this answer is that it is current, and a banner that a
    # cache kept alive across an upgrade would be the one bug the feature can
    # actually cause.
    response.headers["Cache-Control"] = "no-store"
    return response


async def post_update_check(request: web.Request) -> web.Response:
    """Check now. The one handler here that may reach the network.

    Gated on the explicit-action header for the same reason the firewall repair
    and mobile-voice endpoints are: this is the single outbound request swe-mux
    makes on its own behalf, and nothing a background poll or a stray reload can
    trigger should be able to make it. The switch still wins - a disabled check
    makes no request however it is called - and the bounded fetch plus the
    supervised timeout are what keep a slow site from holding this request open.
    """
    checker = _checker(request)
    if checker is None:
        return _unavailable()
    if request.headers.get("X-Mux-User-Gesture") != "update-check":
        return json_response(
            {"error": "checking for updates requires an explicit user action"}, 400
        )
    if not checker.enabled:
        # Refused rather than silently answered, because the caller pressed a
        # button and is owed the reason nothing happened.
        return json_response(
            {
                "error": "update_check_disabled",
                "message": (
                    "the update check is turned off, so nothing was requested; "
                    "enable it in Settings → Diagnostics"
                ),
                **checker.snapshot(),
            },
            409,
        )
    response = json_response(await checker.check(force=True))
    response.headers["Cache-Control"] = "no-store"
    return response


async def post_update_dismiss(request: web.Request) -> web.Response:
    """Decline one version, and keep it declined across restarts and devices."""
    checker = _checker(request)
    if checker is None:
        return _unavailable()
    try:
        body = await request.json()
    except (ValueError, UnicodeDecodeError):
        body = {}
    version = str(body.get("version", "")).strip() if isinstance(body, dict) else ""
    if not version:
        return json_response({"error": "version is required"}, 400)
    response = json_response(await checker.dismiss(version))
    response.headers["Cache-Control"] = "no-store"
    return response


ROUTES: tuple[web.RouteDef, ...] = (
    web.get("/api/update", get_update),
    web.post("/api/update/check", post_update_check),
    web.post("/api/update/dismiss", post_update_dismiss),
)
