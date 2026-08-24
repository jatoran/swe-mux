"""The prompt queue and its auto-delivery controls."""

from __future__ import annotations

import logging

from aiohttp import web

from .. import (
    app_keys as keys,
)
from ..auto_delivery import AutoDeliveryController
from ..http_support import json_response
from ..prompt_queue import (
    PromptQueueService,
    QueueError,
)
from .support import _human_sender_kind

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Phase 4: persistent manual prompt queue. Thin handlers only — ordering,
# revision checks, readiness, identity, and audit live in PromptQueueService.


async def queue_summary(request: web.Request) -> web.Response:
    return json_response({"targets": await request.app[keys.PROMPT_QUEUE].summary()})


async def queue_messages(request: web.Request) -> web.Response:
    target = request.query.get("target_session_id", "").strip()
    if not target:
        raise ValueError("target_session_id is required")
    return json_response(await request.app[keys.PROMPT_QUEUE].target_view(target))


async def queue_create_message(request: web.Request) -> web.Response:
    body = await request.json()
    sender_kind = _human_sender_kind(request)
    message = await request.app[keys.PROMPT_QUEUE].enqueue(
        target_session_id=str(body.get("target_session_id") or ""),
        body=str(body.get("body") or ""),
        armed=bool(body.get("armed", False)),
        insert_after=str(body["insert_after"]) if body.get("insert_after") else None,
        # The HTTP surface is a human surface: the sender kind is derived from
        # the transport (local vs remote device). Agent and observer senders
        # are in-process callers only and never reach this route.
        sender_kind=sender_kind,
        sender_id=str(body["sender_id"]) if body.get("sender_id") else None,
        sender_label=str(body["sender_label"])[:80] if body.get("sender_label") else None,
        correlation_id=str(body["correlation_id"]) if body.get("correlation_id") else None,
        constraints=body.get("constraints"),
    )
    return json_response(message, 201)


async def queue_patch_message(request: web.Request) -> web.Response:
    queue: PromptQueueService = request.app[keys.PROMPT_QUEUE]
    message_id = request.match_info["message_id"]
    body = await request.json()
    if body.get("retarget_session_id"):
        return json_response(
            await queue.retarget(message_id, target_session_id=str(body["retarget_session_id"]))
        )
    if "body" in body:
        revision = body.get("revision")
        if not isinstance(revision, int):
            raise ValueError("revision is required to edit a message body")
        return json_response(
            await queue.edit(message_id, revision=revision, body=str(body["body"]))
        )
    if "armed" in body:
        return json_response(await queue.set_armed(message_id, bool(body["armed"])))
    if "after" in body:
        after = body.get("after")
        return json_response(await queue.move(message_id, after=str(after) if after else None))
    if "constraints" in body:
        # Scheduling is a property of the queued item, not of a sender's UI.
        return json_response(await queue.set_constraints(message_id, body.get("constraints")))
    raise ValueError("nothing to change")


async def queue_cancel_message(request: web.Request) -> web.Response:
    body = await request.json()
    return json_response(
        await request.app[keys.PROMPT_QUEUE].cancel(
            request.match_info["message_id"],
            kind=str(body.get("kind") or "cancelled"),
        )
    )


async def queue_delete_message(request: web.Request) -> web.Response:
    result = await request.app[keys.PROMPT_QUEUE].delete(request.match_info["message_id"])
    log.info(
        "queue message deleted message_id=%s target_session_id=%s previous_state=%s "
        "sender_kind=%s already_deleted=%s",
        result["id"],
        result["target_session_id"],
        result["previous_state"],
        result["sender_kind"],
        result["already_deleted"],
    )
    return json_response(
        {
            "deleted": True,
            "message_id": result["id"],
            "already_deleted": result["already_deleted"],
        }
    )


async def queue_message_deliveries(request: web.Request) -> web.Response:
    return json_response(
        {
            "deliveries": await request.app[keys.PROMPT_QUEUE].store.deliveries(
                request.match_info["message_id"]
            )
        }
    )


async def queue_send_next(request: web.Request) -> web.Response:
    body = await request.json()
    message_id = str(body.get("message_id") or "")
    revision = body.get("revision")
    if not message_id or not isinstance(revision, int):
        raise ValueError("message_id and revision are required")
    return json_response(
        await request.app[keys.PROMPT_QUEUE].send_next(
            message_id,
            revision=revision,
            idempotency_key=str(body["idempotency_key"]) if body.get("idempotency_key") else None,
            confirm=bool(body.get("confirm", False)),
        )
    )


async def queue_export(request: web.Request) -> web.Response:
    target = request.query.get("target_session_id", "").strip()
    if not target:
        raise ValueError("target_session_id is required")
    redact = request.query.get("redact_secrets", "1") not in {"0", "false"}
    return json_response(
        await request.app[keys.PROMPT_QUEUE].export_target(target, redact_secrets=redact)
    )


# ---------------------------------------------------------------------------
# Phase 5: auto-delivery policy, mailbox, and the emergency controls. The
# bounds live in AutoDeliveryController/AgentMessagingService; these handlers
# only carry user acts to them.


async def queue_auto_status(request: web.Request) -> web.Response:
    return json_response(await request.app[keys.AUTO_DELIVERY].status())


async def queue_auto_pause(request: web.Request) -> web.Response:
    """Pause-all / emergency disable. One flag, persisted, provider-independent."""
    body = await request.json()
    controller: AutoDeliveryController = request.app[keys.AUTO_DELIVERY]
    await controller.set_paused(bool(body.get("paused", True)), by=_human_sender_kind(request))
    return json_response(await controller.status())


async def queue_auto_session(request: web.Request) -> web.Response:
    """Per-session opt-in: auto-delivery, accepting agent messages, mid-turn ones.

    Three independent switches on purpose. Arming decides whether an agent
    message counts as authorized, auto-delivery decides who presses send, and
    accepting interjections decides whether send may happen while a turn runs.
    Cycling one never rewrites another.
    """
    controller: AutoDeliveryController = request.app[keys.AUTO_DELIVERY]
    session_id = request.match_info["sid"]
    body = await request.json()
    by = _human_sender_kind(request)
    if "accept_agent_messages" in body:
        await controller.set_accept_agent_messages(
            session_id, bool(body["accept_agent_messages"]), by=by
        )
    if "accept_agent_interjections" in body:
        await controller.set_accept_agent_interjections(
            session_id, bool(body["accept_agent_interjections"]), by=by
        )
    if "enabled" in body:
        if body["enabled"]:
            await controller.enable_session(
                session_id,
                ttl_minutes=int(body["ttl_minutes"]) if body.get("ttl_minutes") else None,
                max_sends=int(body["max_sends"]) if body.get("max_sends") else None,
                by=by,
            )
        else:
            await controller.disable_session(session_id, reason="disabled by user", by=by)
    return json_response(await controller.status())


async def queue_auto_report_unsafe(request: web.Request) -> web.Response:
    """Operator review: record a confirmed bad automatic delivery.

    Resets the proving period and pauses auto-delivery — the promotion criteria
    require zero known false-safe deliveries, so this is not a statistic to
    average away.
    """
    body = await request.json()
    controller: AutoDeliveryController = request.app[keys.AUTO_DELIVERY]
    await controller.report_unsafe(str(body.get("note") or ""))
    return json_response(await controller.status())


async def queue_mailbox(request: web.Request) -> web.Response:
    author = request.query.get("author", "all").strip() or "all"
    role = request.query.get("role")
    project_id = request.query.get("project_id", "").strip() or None
    target_session_id = request.query.get("target_session_id", "").strip() or None
    try:
        limit = int(request.query.get("limit", "100") or 100)
    except ValueError as exc:
        raise QueueError("invalid_limit", "limit must be an integer", status=400) from exc
    return json_response(
        await request.app[keys.AGENT_MESSAGING].mailbox(
            author=author,
            role=role.strip() if role else None,
            project_id=project_id,
            target_session_id=target_session_id,
            limit=limit,
        )
    )


ROUTES: tuple[web.RouteDef, ...] = (
    web.get("/api/queue", queue_summary),
    web.get("/api/queue/messages", queue_messages),
    web.post("/api/queue/messages", queue_create_message),
    web.patch("/api/queue/messages/{message_id}", queue_patch_message),
    web.delete("/api/queue/messages/{message_id}", queue_delete_message),
    web.post("/api/queue/messages/{message_id}/cancel", queue_cancel_message),
    web.get("/api/queue/messages/{message_id}/deliveries", queue_message_deliveries),
    web.post("/api/queue/send-next", queue_send_next),
    web.get("/api/queue/export", queue_export),
    # Phase 5: auto-delivery policy, the mailbox view, and the
    # emergency controls. Runtime state, not config-file state.
    web.get("/api/queue/auto", queue_auto_status),
    web.post("/api/queue/auto/pause", queue_auto_pause),
    web.put("/api/queue/auto/sessions/{sid}", queue_auto_session),
    web.post("/api/queue/auto/report-unsafe", queue_auto_report_unsafe),
    web.get("/api/queue/mailbox", queue_mailbox),
)
