"""The mux assistant's dialogs and the actions it proposes."""

from __future__ import annotations

import logging

from aiohttp import web

from .. import (
    app_keys as keys,
)
from ..assistant import (
    AssistantError,
    AssistantService,
    AssistantStore,
    action_snapshot,
)
from ..http_support import json_response

log = logging.getLogger(__name__)


async def assistant_status(request: web.Request) -> web.Response:
    assistant: AssistantService = request.app[keys.ASSISTANT]
    return json_response(await assistant.status())


async def assistant_dialogs(request: web.Request) -> web.Response:
    store: AssistantStore = request.app[keys.ASSISTANT_STORE]
    limit = int(request.query.get("limit", 20))
    return json_response({"items": await store.dialogs(limit=limit)})


async def assistant_create_dialog(request: web.Request) -> web.Response:
    assistant: AssistantService = request.app[keys.ASSISTANT]
    if not assistant.config.assistant_enabled:
        raise AssistantError("the assistant is disabled; enable it in Settings → Assistant")
    store: AssistantStore = request.app[keys.ASSISTANT_STORE]
    body = await request.json() if request.can_read_body else {}
    title = str(body.get("title") or "") if isinstance(body, dict) else ""
    dialog = await store.create_dialog(title)
    return json_response(dialog, 201)


async def assistant_dialog_detail(request: web.Request) -> web.Response:
    store: AssistantStore = request.app[keys.ASSISTANT_STORE]
    assistant: AssistantService = request.app[keys.ASSISTANT]
    dialog_id = request.match_info["dialog_id"]
    dialog = await store.dialog(dialog_id)
    if dialog is None:
        raise AssistantError("unknown dialog")
    return json_response(
        {
            "dialog": dialog,
            "messages": await store.messages(dialog_id),
            "actions": [action_snapshot(row) for row in await store.actions(dialog_id)],
            "turn_running": assistant.turn_running(dialog_id),
        }
    )


async def assistant_turn(request: web.Request) -> web.Response:
    assistant: AssistantService = request.app[keys.ASSISTANT]
    body = await request.json()
    if not isinstance(body, dict):
        raise ValueError("turn request body must be an object")
    client_context = body.get("client_context")
    turn_id = await assistant.start_turn(
        request.match_info["dialog_id"],
        str(body.get("text") or ""),
        client_context if isinstance(client_context, dict) else None,
    )
    # `queued` distinguishes accepted-and-waiting from accepted-and-running. It
    # replaces a refusal that used to lose whatever the operator said mid-turn.
    return json_response(
        {"turn_id": turn_id, "queued": assistant.turn_queued(turn_id)}, 202
    )


async def assistant_interrupt(request: web.Request) -> web.Response:
    assistant: AssistantService = request.app[keys.ASSISTANT]
    stopped = assistant.interrupt(request.match_info["dialog_id"])
    return json_response({"interrupted": stopped})


async def assistant_confirm_action(request: web.Request) -> web.Response:
    assistant: AssistantService = request.app[keys.ASSISTANT]
    return json_response(await assistant.confirm_action(request.match_info["action_id"]))


async def assistant_cancel_action(request: web.Request) -> web.Response:
    assistant: AssistantService = request.app[keys.ASSISTANT]
    return json_response(await assistant.cancel_action(request.match_info["action_id"]))


async def assistant_announced(request: web.Request) -> web.Response:
    """A device has begun speaking a scheduled card's announcement aloud.

    Restarts that card's cancel window so the operator's chance to object is not
    spent synthesizing the sentence that tells them there is something to object
    to. A no-op for anything not currently scheduled.
    """
    assistant: AssistantService = request.app[keys.ASSISTANT]
    return json_response(await assistant.announce_action(request.match_info["action_id"]))


async def assistant_ui_result(request: web.Request) -> web.Response:
    assistant: AssistantService = request.app[keys.ASSISTANT]
    body = await request.json()
    if not isinstance(body, dict):
        raise ValueError("ui-result body must be an object")
    accepted = assistant.report_ui_result(request.match_info["action_id"], body)
    return json_response({"accepted": accepted})


ROUTES: tuple[web.RouteDef, ...] = (
    web.get("/api/assistant", assistant_status),
    web.get("/api/assistant/dialogs", assistant_dialogs),
    web.post("/api/assistant/dialogs", assistant_create_dialog),
    web.get("/api/assistant/dialogs/{dialog_id}", assistant_dialog_detail),
    web.post("/api/assistant/dialogs/{dialog_id}/turns", assistant_turn),
    web.post("/api/assistant/dialogs/{dialog_id}/interrupt", assistant_interrupt),
    web.post("/api/assistant/actions/{action_id}/confirm", assistant_confirm_action),
    web.post("/api/assistant/actions/{action_id}/cancel", assistant_cancel_action),
    web.post("/api/assistant/actions/{action_id}/ui-result", assistant_ui_result),
    web.post("/api/assistant/actions/{action_id}/announced", assistant_announced),
)
