"""Agent Context inventory, reveal, copy, linking, unlinking, and restore routes."""

from __future__ import annotations

import asyncio
import logging

from aiohttp import web

from .. import (
    app_keys as keys,
)
from ..agent_context import AgentContextConflict, AgentContextService
from ..file_manager import open_in_file_manager
from ..http_support import json_response
from .support import _request_project

log = logging.getLogger(__name__)


async def get_agent_context(request: web.Request) -> web.Response:
    """Inventory the bounded context sources the selected Project's agents can use.

    Memoized in the service on a stat signature over the files it reads; `refresh=1` is
    the tab's rescan control and bypasses that outright, which is what keeps the cache
    honest about the one thing a stat cannot see.
    """

    project = _request_project(request)
    service: AgentContextService = request.app[keys.AGENT_CONTEXT]
    refresh = request.query.get("refresh", "") in {"1", "true"}
    payload = await asyncio.to_thread(
        lambda: service.inventory(project.id, project.name, project.root, refresh=refresh)
    )
    return json_response(payload)


async def get_agent_context_source(request: web.Request) -> web.Response:
    project = _request_project(request)
    service: AgentContextService = request.app[keys.AGENT_CONTEXT]
    payload = await asyncio.to_thread(
        service.read_source, project.root, request.match_info["source_id"]
    )
    return json_response(payload)


async def reveal_agent_context_source(request: web.Request) -> web.Response:
    project = _request_project(request)
    service: AgentContextService = request.app[keys.AGENT_CONTEXT]
    path = await asyncio.to_thread(
        service.source_path, project.root, request.match_info["source_id"]
    )
    await asyncio.to_thread(open_in_file_manager, path)
    return json_response({"ok": True})


async def preview_agent_context_sync(request: web.Request) -> web.Response:
    project = _request_project(request)
    body = await request.json()
    direction = str(body.get("direction") or "")
    service: AgentContextService = request.app[keys.AGENT_CONTEXT]
    return json_response(await asyncio.to_thread(service.preview_sync, project.root, direction))


async def sync_agent_context(request: web.Request) -> web.Response:
    project = _request_project(request)
    body = await request.json()
    direction = str(body.get("direction") or "")
    source_revision = str(body.get("source_revision") or "")
    target_revision = str(body.get("target_revision") or "")
    if not source_revision or not target_revision:
        raise ValueError("source_revision and target_revision are required")
    service: AgentContextService = request.app[keys.AGENT_CONTEXT]
    try:
        result = await asyncio.to_thread(
            service.sync,
            project.id,
            project.root,
            direction,
            source_revision,
            target_revision,
        )
    except AgentContextConflict as exc:
        return json_response({"error": str(exc), "code": "revision_conflict"}, 409)
    log.info(
        "agent_context_synced project_id=%s direction=%s source=%s target=%s revision=%s",
        project.id,
        direction,
        result["source"],
        result["target"],
        result["revision"],
    )
    await request.app[keys.EVENTS].emit(
        "agent_context_changed",
        source="user",
        operation="sync",
        project_id=project.id,
        direction=direction,
        revision=result["revision"],
    )
    return json_response(result)


async def preview_agent_context_link(request: web.Request) -> web.Response:
    project = _request_project(request)
    body = await request.json()
    direction = str(body.get("direction") or "")
    service: AgentContextService = request.app[keys.AGENT_CONTEXT]
    return json_response(await asyncio.to_thread(service.preview_link, project.root, direction))


async def link_agent_context(request: web.Request) -> web.Response:
    project = _request_project(request)
    body = await request.json()
    direction = str(body.get("direction") or "")
    source_revision = str(body.get("source_revision") or "")
    target_revision = str(body.get("target_revision") or "")
    if not source_revision or not target_revision:
        raise ValueError("source_revision and target_revision are required")
    service: AgentContextService = request.app[keys.AGENT_CONTEXT]
    try:
        result = await asyncio.to_thread(
            service.link,
            project.id,
            project.root,
            direction,
            source_revision,
            target_revision,
        )
    except AgentContextConflict as exc:
        return json_response({"error": str(exc), "code": "revision_conflict"}, 409)
    log.info(
        "agent_context_linked project_id=%s direction=%s source=%s target=%s revision=%s",
        project.id,
        direction,
        result["source"],
        result["target"],
        result["revision"],
    )
    await request.app[keys.EVENTS].emit(
        "agent_context_changed",
        source="user",
        operation="link",
        project_id=project.id,
        direction=direction,
        target=result["target"],
        revision=result["revision"],
    )
    return json_response(result)


async def unlink_agent_context(request: web.Request) -> web.Response:
    project = _request_project(request)
    body = await request.json()
    source_id = str(body.get("source_id") or "")
    target_revision = str(body.get("target_revision") or "")
    if not source_id or not target_revision:
        raise ValueError("source_id and target_revision are required")
    service: AgentContextService = request.app[keys.AGENT_CONTEXT]
    try:
        result = await asyncio.to_thread(
            service.unlink,
            project.id,
            project.root,
            source_id,
            target_revision,
        )
    except AgentContextConflict as exc:
        return json_response({"error": str(exc), "code": "revision_conflict"}, 409)
    log.info(
        "agent_context_unlinked project_id=%s source_id=%s target=%s revision=%s",
        project.id,
        source_id,
        result["target"],
        result["revision"],
    )
    await request.app[keys.EVENTS].emit(
        "agent_context_changed",
        source="user",
        operation="unlink",
        project_id=project.id,
        target=result["target"],
        revision=result["revision"],
    )
    return json_response(result)


async def restore_agent_context(request: web.Request) -> web.Response:
    project = _request_project(request)
    body = await request.json()
    backup_id = str(body.get("backup_id") or "")
    target_revision = str(body.get("target_revision") or "")
    if not backup_id or not target_revision:
        raise ValueError("backup_id and target_revision are required")
    service: AgentContextService = request.app[keys.AGENT_CONTEXT]
    try:
        result = await asyncio.to_thread(
            service.restore,
            project.id,
            project.root,
            backup_id,
            target_revision,
        )
    except AgentContextConflict as exc:
        return json_response({"error": str(exc), "code": "revision_conflict"}, 409)
    log.info(
        "agent_context_restored project_id=%s backup_id=%s target=%s revision=%s",
        project.id,
        backup_id,
        result["target"],
        result["revision"],
    )
    await request.app[keys.EVENTS].emit(
        "agent_context_changed",
        source="user",
        operation="restore",
        project_id=project.id,
        target=result["target"],
        revision=result["revision"],
    )
    return json_response(result)


ROUTES: tuple[web.RouteDef, ...] = (
    web.get("/api/projects/{project_id}/agent-context", get_agent_context),
    web.get(
        "/api/projects/{project_id}/agent-context/sources/{source_id}",
        get_agent_context_source,
    ),
    web.post(
        "/api/projects/{project_id}/agent-context/sources/{source_id}/reveal",
        reveal_agent_context_source,
    ),
    web.post(
        "/api/projects/{project_id}/agent-context/sync/preview",
        preview_agent_context_sync,
    ),
    web.post(
        "/api/projects/{project_id}/agent-context/sync",
        sync_agent_context,
    ),
    web.post(
        "/api/projects/{project_id}/agent-context/link/preview",
        preview_agent_context_link,
    ),
    web.post(
        "/api/projects/{project_id}/agent-context/link",
        link_agent_context,
    ),
    web.post(
        "/api/projects/{project_id}/agent-context/unlink",
        unlink_agent_context,
    ),
    web.post(
        "/api/projects/{project_id}/agent-context/restore",
        restore_agent_context,
    ),
)
