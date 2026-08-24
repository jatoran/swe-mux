"""Web Push subscriptions, device presence, and the notification list."""

from __future__ import annotations

import logging

from aiohttp import web

from .. import (
    app_keys as keys,
)
from ..http_support import json_response
from ..meta_hooks import MetaHookEngine

log = logging.getLogger(__name__)


async def get_vapid_public_key(request: web.Request) -> web.Response:
    return json_response({"key": request.app[keys.PUSH_STORE].application_server_key})


async def push_subscribe(request: web.Request) -> web.Response:
    body = await request.json()
    profile = str(body.get("profile") or "mobile")
    request.app[keys.PUSH_STORE].add(body.get("subscription"), profile)
    return json_response({"ok": True})


async def push_unsubscribe(request: web.Request) -> web.Response:
    body = await request.json()
    endpoint = str(body.get("endpoint") or "")
    if not endpoint:
        raise ValueError("endpoint is required")
    request.app[keys.PUSH_STORE].remove(endpoint)
    return json_response({"ok": True})


async def push_presence(request: web.Request) -> web.Response:
    body = await request.json()
    endpoint = str(body.get("endpoint") or "")
    if not endpoint:
        raise ValueError("endpoint is required")
    ttl = body.get("ttl")
    request.app[keys.PUSH_STORE].set_presence(
        endpoint, bool(body.get("focused")), float(ttl) if isinstance(ttl, (int, float)) else 90.0
    )
    return json_response({"ok": True})


async def get_device_presence(request: web.Request) -> web.Response:
    """Which devices the daemon believes are in use, and why.

    The suppression it feeds is invisible by construction — the symptom of getting
    it wrong is a notification that never arrives — so the inputs are readable.
    """
    return json_response(request.app[keys.DEVICE_PRESENCE].snapshot())


async def list_notifications(request: web.Request) -> web.Response:
    hooks: MetaHookEngine = request.app[keys.HOOKS]
    automation = await request.app[keys.AUTOMATION_STORE].notifications(limit=200)
    return json_response(
        {
            "notifications": hooks.notifications,
            "deliveries": [item.snapshot() for item in hooks.deliveries[-100:]],
            "automation": automation,
        }
    )


ROUTES: tuple[web.RouteDef, ...] = (
    web.get("/api/push/vapid-public-key", get_vapid_public_key),
    web.post("/api/push/subscribe", push_subscribe),
    web.post("/api/push/unsubscribe", push_unsubscribe),
    web.post("/api/push/presence", push_presence),
    web.get("/api/push/presence", get_device_presence),
    web.get("/api/notifications", list_notifications),
)
