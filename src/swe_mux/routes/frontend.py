"""The frontend overlay: what is being served, install one, revert to the bundle.

`frontend_overlay.py` holds all the reasoning about hashes, the compatibility pin
and the tree layout; this module is transport, in the same split `routes/update.py`
has against `update_check`/`update_install`.

Two access rules, and they are deliberately different from each other.

**Installing is loopback-only.** The request names a path on the daemon's own
filesystem, or a URL the daemon will fetch, and neither is something a phone on
the tailnet should be choosing. It also carries an explicit-gesture header, for
the same reason the firewall repair and the update install do: nothing a
background poll or a stray reload can trigger should be able to replace the
application's UI.

**Reverting is not.** It is the safe direction - back to the tree that shipped
with this build - and restricting it would mean the one control that exists for
"the overlay broke the UI" was reachable from fewer places than the thing that
broke. Note that a broken overlay usually breaks *every* client, including the
phone, which is why `swemux ui-overlay revert` exists and is the real answer;
this endpoint is the convenient one, not the last resort.

Nothing here re-verifies on a read. Verification is a start-time act whose answer
is the process's `FRONTEND_CHOICE`, and a status endpoint that re-hashed 24 MiB
per poll would turn a passive panel into real work for no new information.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from aiohttp import web

from .. import (
    app_keys as keys,
)
from ..frontend_overlay import OverlayRefused, OverlayStore
from ..http_support import is_loopback_peer, json_response

log = logging.getLogger(__name__)


def _store(request: web.Request) -> OverlayStore | None:
    return request.app.get(keys.FRONTEND_OVERLAY)


def _unavailable() -> web.Response:
    """The answer when no store was built (a minimal or partially-built app).

    A 200 carrying `supported: false` rather than a 404, for the reason the update
    check answers that way: every consumer is a passive panel, and a client that
    had to catch an HTTP error to learn "this daemon has no overlay support" would
    render an error where the honest answer is a hidden section.
    """
    return json_response({"supported": False, "installed": False, "active": False})


def _no_store(response: web.Response) -> web.Response:
    response.headers["Cache-Control"] = "no-store"
    return response


async def get_overlay(request: web.Request) -> web.Response:
    """What is installed, and what this daemon process actually resolved to serve."""
    store = _store(request)
    if store is None:
        return _unavailable()
    choice = request.app.get(keys.FRONTEND_CHOICE)
    payload = await asyncio.to_thread(store.status, choice)
    # Present only when a caller passed an explicit `frontend_dir`, which is how
    # tests and fixtures point the daemon at a tree; saying so is better than
    # reporting an overlay decision that was never made.
    payload["override"] = choice is None
    return _no_store(json_response(payload))


async def post_overlay_install(request: web.Request) -> web.Response:
    """Install an overlay from a local archive, a local directory, or a URL.

    Exactly one source per request, because "install this" with two answers is a
    question about precedence nobody should have to know. A URL additionally
    requires the SHA-256 it must match: there is no manifest here to take a hash
    from, and bytes off a network that reach the served tree without one are an
    arbitrary-code-execution path into the application's own UI.
    """
    store = _store(request)
    if store is None:
        return _unavailable()
    if not is_loopback_peer(request.remote or ""):
        raise web.HTTPForbidden(text="installing a frontend overlay is loopback-only")
    if request.headers.get("X-Mux-User-Gesture") != "frontend-overlay-install":
        return json_response(
            {"error": "installing a frontend overlay requires an explicit user action"}, 400
        )
    try:
        body = await request.json()
    except (ValueError, UnicodeDecodeError):
        body = {}
    if not isinstance(body, dict):
        body = {}
    archive = str(body.get("archive") or "").strip()
    directory = str(body.get("directory") or "").strip()
    url = str(body.get("url") or "").strip()
    sha256 = str(body.get("sha256") or "").strip()
    sources = (("archive", archive), ("directory", directory), ("url", url))
    named = [name for name, value in sources if value]
    if len(named) != 1:
        return json_response(
            {
                "error": "source_required",
                "message": (
                    "name exactly one of archive, directory or url; "
                    f"{len(named)} were given"
                ),
            },
            400,
        )
    try:
        if url:
            result = await store.install_from_url(url, sha256=sha256)
        elif archive:
            result = await asyncio.to_thread(
                store.install_from_archive, Path(archive), sha256=sha256 or None
            )
        else:
            result = await asyncio.to_thread(store.install_from_directory, Path(directory))
    except OverlayRefused as refusal:
        log.warning(
            "frontend overlay install refused",
            extra={"overlay_reason": refusal.reason, "overlay_source": named[0]},
        )
        return _no_store(
            json_response({"error": refusal.reason, "message": refusal.message}, 409)
        )
    payload = result.as_dict()
    payload["message"] = (
        "The overlay is verified and installed. It serves from the next daemon "
        "start - reload the daemon to apply it now."
    )
    payload["restart_required"] = True
    return _no_store(json_response(payload, 202))


async def post_overlay_revert(request: web.Request) -> web.Response:
    """Switch the installed overlay off and go back to the bundled frontend."""
    store = _store(request)
    if store is None:
        return _unavailable()
    if request.headers.get("X-Mux-User-Gesture") != "frontend-overlay-revert":
        return json_response(
            {"error": "reverting the frontend overlay requires an explicit user action"}, 400
        )
    try:
        payload = await asyncio.to_thread(store.revert)
    except OverlayRefused as refusal:
        return _no_store(
            json_response({"error": refusal.reason, "message": refusal.message}, 409)
        )
    payload["restart_required"] = bool(payload.get("changed"))
    return _no_store(json_response(payload))


async def post_overlay_restore(request: web.Request) -> web.Response:
    """Switch a reverted overlay back on. The exact inverse of the revert."""
    store = _store(request)
    if store is None:
        return _unavailable()
    if request.headers.get("X-Mux-User-Gesture") != "frontend-overlay-restore":
        return json_response(
            {"error": "restoring the frontend overlay requires an explicit user action"}, 400
        )
    try:
        payload = await asyncio.to_thread(store.restore)
    except OverlayRefused as refusal:
        return _no_store(
            json_response({"error": refusal.reason, "message": refusal.message}, 409)
        )
    payload["restart_required"] = bool(payload.get("changed"))
    return _no_store(json_response(payload))


ROUTES: tuple[web.RouteDef, ...] = (
    web.get("/api/frontend/overlay", get_overlay),
    web.post("/api/frontend/overlay/install", post_overlay_install),
    web.post("/api/frontend/overlay/revert", post_overlay_revert),
    web.post("/api/frontend/overlay/restore", post_overlay_restore),
)
