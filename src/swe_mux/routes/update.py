"""The release update check and install: read, ask again, decline, plan, install.

The split between the handlers is the whole design. `GET` never touches the
network - it reports what the daily background check already found, so opening
the app, refreshing a phone, or polling these endpoints costs nothing and can
never be the reason a request hangs. `POST /api/update/check` and
`POST /api/update/plan` reach the network, and only on an explicit press.
`POST /api/update/install` is the one handler that changes anything, and it is
the most deliberate act in the API: it carries the explicit-gesture header *and*
has to name the version it means, so what gets installed is what the operator
was looking at - and, for a release that cannot be installed without ending
every live session, it has to say so a second time (`accept_supervisor_update`).

`update_check.py` holds the reasoning about intervals, schemas, and comparison;
`update_install.py` holds the reasoning about hashes, bundles, and the
supervisor; this module is transport. It never raises on any of them: an update
check that could 500 would put a failed network call in front of an operator who
did not ask about the network.
"""

from __future__ import annotations

import logging
from typing import Any

from aiohttp import web

from .. import __version__
from .. import (
    app_keys as keys,
)
from ..http_support import json_response
from ..update_check import UpdateChecker
from ..update_install import UpdateInstaller, UpdateRefused

log = logging.getLogger(__name__)

#: The explicit-gesture header values, one per act that reaches the network or
#: changes the install. Different words on purpose: a client that sends the
#: plan's word to the install route is a client that did not read the contract.
GESTURE_CHECK = "update-check"
GESTURE_PLAN = "update-plan"
GESTURE_INSTALL = "update-install"


def _checker(request: web.Request) -> UpdateChecker | None:
    return request.app.get(keys.UPDATE_CHECK)


def _installer(request: web.Request) -> UpdateInstaller | None:
    return request.app.get(keys.UPDATE_INSTALL)


def _unavailable() -> web.Response:
    """The answer when no checker was built (a minimal or partially-built app).

    Deliberately a 200 carrying `status: "unavailable"` rather than a 404 or a
    503: every consumer of this endpoint is a passive banner, and a client that
    has to distinguish "no update" from "this daemon has no update check" by
    catching an HTTP error would end up rendering an error where the honest
    answer is silence.

    It still names the running version. Which build this is does not depend on
    an update checker having been built, and Settings states it outright beside
    the switch - so leaving it out here would blank the one fact this daemon
    knows for certain on exactly the installs least able to find it elsewhere.
    """
    return json_response(
        {
            "enabled": False,
            "status": "unavailable",
            "current_version": __version__,
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
    if request.headers.get("X-Mux-User-Gesture") != GESTURE_CHECK:
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
                    "enable it in Settings → Diagnostics → swe-mux version"
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


def _install_unavailable() -> web.Response:
    """No installer on this runtime, answered the same quiet way as the check."""
    return json_response(
        {
            "install_kind": "unknown",
            "swappable": False,
            "phase": "idle",
            "reason": "unavailable",
            "message": "this daemon has no updater",
        }
    )


async def get_update_install(request: web.Request) -> web.Response:
    """What the last (or current) install attempt is doing. Reads state only.

    Polled while a download runs, so it must stay free: it computes from the
    installer's own state and makes no request, touches no archive, and never
    inspects the bundle.
    """
    installer = _installer(request)
    if installer is None:
        return _install_unavailable()
    await installer.ensure_loaded()
    response = json_response(installer.snapshot())
    response.headers["Cache-Control"] = "no-store"
    return response


async def _named_version(request: web.Request) -> tuple[str, dict[str, Any]]:
    """The body's `version`, or "" - plus the body, for the flags beside it."""
    try:
        body = await request.json()
    except (ValueError, UnicodeDecodeError):
        body = {}
    if not isinstance(body, dict):
        body = {}
    return str(body.get("version", "")).strip(), body


def _version_required() -> web.Response:
    return json_response(
        {
            "error": "version_required",
            "message": ("name the version to install, so what is installed is what you were shown"),
        },
        400,
    )


def _live_sessions(request: web.Request) -> int:
    """How many sessions a reap would end, from the daemon's own registry."""
    sessions = request.app.get(keys.SESSIONS)
    if sessions is None:
        return 0
    try:
        return sum(1 for session in sessions.sessions.values() if session.pty.isalive())
    except Exception:  # noqa: BLE001 - a count on a dialog must never 500 it
        return 0


async def post_update_plan(request: web.Request) -> web.Response:
    """What installing a named release would do, said before anything is fetched.

    Reaches the network for the manifest and two small hashed sidecars, behind
    its own gesture header. It is the confirm dialog's content: which mode the
    release needs, whether that ends the operator's sessions and how many, how
    much of the bundle is rewritten, and - for a source install - the command to
    run instead. Nothing is persisted and nothing is downloaded; a refusal that
    needs no archive (a source install, a manifest that moved, no artifact for
    this platform) is answered as `409` with the same words the install would
    use.
    """
    installer = _installer(request)
    if installer is None:
        return _install_unavailable()
    if request.headers.get("X-Mux-User-Gesture") != GESTURE_PLAN:
        return json_response({"error": "planning an update requires an explicit user action"}, 400)
    version, _ = await _named_version(request)
    if not version:
        return _version_required()
    try:
        plan = await installer.plan(version)
    except UpdateRefused as refusal:
        log.info(
            "update plan refused",
            extra={"update_reason": refusal.reason, "update_version": version},
        )
        return json_response(
            {
                "error": refusal.reason,
                "message": refusal.message,
                "consent": refusal.consent,
                **installer.snapshot(),
            },
            409,
        )
    plan["live_sessions"] = _live_sessions(request)
    response = json_response(plan)
    response.headers["Cache-Control"] = "no-store"
    return response


async def post_update_install(request: web.Request) -> web.Response:
    """Download, verify, and hand a named release to the staged swap.

    Two gates before anything is fetched, and they answer different questions.
    The **gesture header** is the same one the manual check requires: nothing a
    background poll or a stray reload can trigger may reach the network on this
    daemon's behalf, and this one also replaces the application. The **named
    version** is consent about a specific release - the manifest moves, and
    "install whatever is latest right now" is not what a person pressing a button
    labelled with a version number agreed to.

    A third, `accept_supervisor_update`, is consent about *cost*: a release that
    replaces the PTY supervisor ends every live session, and the install refuses
    it (`409` carrying `consent: "supervisor_update"`) until the request says
    that was understood. The flag is permission, not an instruction - a release
    that can be installed around the sessions is, whatever the flag says.

    Everything it can refuse without touching the network is refused here, as the
    response to this request, so a caller learns "this is a source install" or
    "a redeploy is already running" immediately instead of by polling for it.
    """
    installer = _installer(request)
    if installer is None:
        return _install_unavailable()
    if request.headers.get("X-Mux-User-Gesture") != GESTURE_INSTALL:
        return json_response(
            {"error": "installing an update requires an explicit user action"}, 400
        )
    version, body = await _named_version(request)
    if not version:
        return _version_required()
    accepted = body.get("accept_supervisor_update") is True
    try:
        snapshot = await installer.start(version, accept_supervisor_update=accepted)
    except UpdateRefused as refusal:
        log.info(
            "update install refused",
            extra={"update_reason": refusal.reason, "update_version": version},
        )
        return json_response(
            {
                "error": refusal.reason,
                "message": refusal.message,
                **installer.snapshot(),
            },
            409,
        )
    response = json_response(snapshot, 202)
    response.headers["Cache-Control"] = "no-store"
    return response


ROUTES: tuple[web.RouteDef, ...] = (
    web.get("/api/update", get_update),
    web.post("/api/update/check", post_update_check),
    web.post("/api/update/dismiss", post_update_dismiss),
    web.post("/api/update/plan", post_update_plan),
    web.get("/api/update/install", get_update_install),
    web.post("/api/update/install", post_update_install),
)
