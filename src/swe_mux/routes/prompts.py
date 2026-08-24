"""The prompt library."""

from __future__ import annotations

import logging
from typing import Any, cast

from aiohttp import web

from .. import (
    app_keys as keys,
)
from ..http_support import json_response
from ..models import (
    ProjectRecord,
)
from ..prompt_library import PromptScope

log = logging.getLogger(__name__)


def _prompt_scope(request: web.Request) -> PromptScope:
    value = request.match_info.get("scope") or ""
    if value not in {"global", "project"}:
        raise ValueError("prompt scope must be global or project")
    return cast(PromptScope, value)


def _prompt_project(request: web.Request, body: dict[str, Any] | None = None):  # type: ignore[no-untyped-def]
    project_id = str((body or {}).get("project_id") or request.query.get("project_id") or "")
    if not project_id:
        return None
    project = request.app[keys.PROJECTS].projects.get(project_id)
    if project is None:
        raise ValueError("unknown project")
    return project


async def list_prompts(request: web.Request) -> web.Response:
    # `all_projects=1` is the management view: it reads every registered Project's
    # library so templates can be found and edited without first focusing their
    # Project. It is opt-in because the default listing is also what the Action
    # layout pins from, and that must stay confined to the focused Project.
    others: list[ProjectRecord] = []
    if request.query.get("all_projects") in {"1", "true"}:
        others = sorted(
            request.app[keys.PROJECTS].projects.values(),
            key=lambda item: (item.position, item.name.casefold()),
        )
    return json_response(
        request.app[keys.PROMPT_LIBRARY].list(_prompt_project(request), other_projects=others)
    )


async def create_prompt(request: web.Request) -> web.Response:
    body = await request.json()
    scope_value = str(body.get("scope") or "")
    if scope_value not in {"global", "project"}:
        raise ValueError({"scope": "must be global or project"})
    scope = cast(PromptScope, scope_value)
    item = request.app[keys.PROMPT_LIBRARY].create(scope, body, _prompt_project(request, body))
    await request.app[keys.EVENTS].emit(
        "prompt_template_changed", source="user", operation="created", template_id=item["id"]
    )
    return json_response(item, 201)


async def put_prompt(request: web.Request) -> web.Response:
    body = await request.json()
    try:
        item = request.app[keys.PROMPT_LIBRARY].update(
            _prompt_scope(request),
            request.match_info["template_id"],
            body,
            str(body.get("revision") or ""),
            _prompt_project(request, body),
        )
    except ValueError as exc:
        if "changed externally" in str(exc):
            return json_response({"error": str(exc), "code": "revision_conflict"}, 409)
        raise
    await request.app[keys.EVENTS].emit(
        "prompt_template_changed", source="user", operation="updated", template_id=item["id"]
    )
    return json_response(item)


async def delete_prompt(request: web.Request) -> web.Response:
    body = await request.json()
    try:
        request.app[keys.PROMPT_LIBRARY].delete(
            _prompt_scope(request),
            request.match_info["template_id"],
            str(body.get("revision") or ""),
            _prompt_project(request, body),
        )
    except ValueError as exc:
        if "changed externally" in str(exc):
            return json_response({"error": str(exc), "code": "revision_conflict"}, 409)
        raise
    await request.app[keys.EVENTS].emit(
        "prompt_template_changed",
        source="user",
        operation="deleted",
        template_id=request.match_info["template_id"],
    )
    return json_response({"ok": True})


async def use_prompt(request: web.Request) -> web.Response:
    project = _prompt_project(request, await request.json())
    del project
    key = f"{_prompt_scope(request)}:{request.match_info['template_id']}"
    return json_response(request.app[keys.PROMPT_LIBRARY].record_use(key))


async def favorite_prompt(request: web.Request) -> web.Response:
    body = await request.json()
    _prompt_project(request, body)
    key = f"{_prompt_scope(request)}:{request.match_info['template_id']}"
    return json_response(
        request.app[keys.PROMPT_LIBRARY].set_favorite(key, bool(body.get("favorite")))
    )


ROUTES: tuple[web.RouteDef, ...] = (
    web.get("/api/prompts", list_prompts),
    web.post("/api/prompts", create_prompt),
    web.put("/api/prompts/{scope}/{template_id}", put_prompt),
    web.delete("/api/prompts/{scope}/{template_id}", delete_prompt),
    web.post("/api/prompts/{scope}/{template_id}/use", use_prompt),
    web.patch("/api/prompts/{scope}/{template_id}/favorite", favorite_prompt),
)
