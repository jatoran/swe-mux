"""HTTP surface for plugin lifecycle, contributions, and scoped callbacks."""

from __future__ import annotations

from importlib.resources import files

from aiohttp import web

from .. import app_keys as keys
from ..plugins import PluginError
from .support import _optional_json


def _manager(request: web.Request):  # type: ignore[no-untyped-def]
    return request.app[keys.PLUGINS]


def _failure(error: PluginError) -> web.Response:
    status = (
        404
        if error.code.endswith("not_found")
        else 403
        if error.code in {"permission_denied", "invalid_token"}
        else 409
        if error.code
        in {"approval_required", "source_conflict", "manifest_changed", "approval_stale"}
        else 400
    )
    return web.json_response({"error": str(error), "code": error.code}, status=status)


async def plugin_list(request: web.Request) -> web.Response:
    return web.json_response(await _manager(request).list())


async def plugin_schema(request: web.Request) -> web.Response:
    text = files("swe_mux.assets").joinpath("plugin-schema.md").read_text(encoding="utf-8")
    return web.Response(text=text, content_type="text/markdown")


async def plugin_inspect(request: web.Request) -> web.Response:
    body = await _optional_json(request)
    try:
        return web.json_response(await _manager(request).inspect(str(body.get("path") or "")))
    except (PluginError, ValueError) as exc:
        return _failure(
            exc if isinstance(exc, PluginError) else PluginError("invalid_manifest", str(exc))
        )


async def plugin_link(request: web.Request) -> web.Response:
    body = await _optional_json(request)
    try:
        result = await _manager(request).link(
            str(body.get("path") or ""),
            approve=bool(body.get("approve")),
            enable=bool(body.get("enable")),
        )
        await request.app[keys.EVENTS].emit(
            "plugin_changed", source="user", plugin_id=result["id"], operation="link"
        )
        return web.json_response(result, status=201)
    except (PluginError, ValueError) as exc:
        return _failure(
            exc if isinstance(exc, PluginError) else PluginError("invalid_manifest", str(exc))
        )


async def plugin_install(request: web.Request) -> web.Response:
    body = await _optional_json(request)
    try:
        result = await _manager(request).install(
            str(body.get("source") or ""),
            ref=str(body.get("ref") or ""),
            approve=bool(body.get("approve")),
            enable=bool(body.get("enable")),
        )
        await request.app[keys.EVENTS].emit(
            "plugin_changed", source="user", plugin_id=result["id"], operation="install"
        )
        return web.json_response(result, status=201)
    except PluginError as exc:
        return _failure(exc)


async def plugin_approve(request: web.Request) -> web.Response:
    body = await _optional_json(request)
    try:
        result = await _manager(request).approve(
            request.match_info["plugin_id"], enable=bool(body.get("enable", True))
        )
        await request.app[keys.EVENTS].emit(
            "plugin_changed", source="user", plugin_id=result["id"], operation="approve"
        )
        return web.json_response(result)
    except PluginError as exc:
        return _failure(exc)


async def plugin_enable(request: web.Request) -> web.Response:
    body = await _optional_json(request)
    try:
        result = await _manager(request).enable(
            request.match_info["plugin_id"], bool(body.get("enabled", True))
        )
        await request.app[keys.EVENTS].emit(
            "plugin_changed",
            source="user",
            plugin_id=result["id"],
            operation="enable" if result["enabled"] else "disable",
        )
        return web.json_response(result)
    except PluginError as exc:
        return _failure(exc)


async def plugin_uninstall(request: web.Request) -> web.Response:
    try:
        result = await _manager(request).uninstall(
            request.match_info["plugin_id"], purge=request.query.get("purge") == "1"
        )
        await request.app[keys.EVENTS].emit(
            "plugin_changed", source="user", plugin_id=result["id"], operation="uninstall"
        )
        return web.json_response(result)
    except PluginError as exc:
        return _failure(exc)


async def plugin_rollback(request: web.Request) -> web.Response:
    try:
        return web.json_response(
            await _manager(request).rollback_plugin(request.match_info["plugin_id"])
        )
    except PluginError as exc:
        return _failure(exc)


async def plugin_update(request: web.Request) -> web.Response:
    body = await _optional_json(request)
    try:
        result = await _manager(request).update(
            request.match_info["plugin_id"],
            approve=bool(body.get("approve")),
            enable=bool(body.get("enable")),
        )
        await request.app[keys.EVENTS].emit(
            "plugin_changed",
            source="user",
            plugin_id=result["id"],
            operation="update",
        )
        return web.json_response(result)
    except PluginError as exc:
        return _failure(exc)


async def plugin_action(request: web.Request) -> web.Response:
    body = await _optional_json(request)
    try:
        result = await _manager(request).invoke_action(
            request.match_info["plugin_id"], request.match_info["action_id"], body, source="user"
        )
        await request.app[keys.EVENTS].emit(
            "plugin_action_finished",
            source="plugin",
            plugin_id=request.match_info["plugin_id"],
            action_id=request.match_info["action_id"],
            outcome=result["outcome"],
            correlation_id=result["correlation_id"],
        )
        return web.json_response(result)
    except PluginError as exc:
        return _failure(exc)


async def plugin_pane(request: web.Request) -> web.Response:
    body = await _optional_json(request)
    try:
        result = await _manager(request).open_pane(
            request.match_info["plugin_id"], request.match_info["pane_id"], body
        )
        await request.app[keys.EVENTS].emit(
            "plugin_pane_opened",
            source="plugin",
            plugin_id=request.match_info["plugin_id"],
            pane_id=request.match_info["pane_id"],
            session_id=result["session"]["id"],
        )
        return web.json_response(result, status=201)
    except PluginError as exc:
        return _failure(exc)


async def plugin_logs(request: web.Request) -> web.Response:
    try:
        limit = int(request.query.get("limit", "100"))
    except ValueError:
        limit = 100
    return web.json_response(
        await _manager(request).store.logs(request.query.get("plugin_id"), limit)
    )


async def plugin_links(request: web.Request) -> web.Response:
    return web.json_response(await _manager(request).link_handlers())


async def plugin_link_activate(request: web.Request) -> web.Response:
    body = await _optional_json(request)
    url = str(body.pop("url", ""))
    try:
        return web.json_response(
            await _manager(request).activate_link(
                request.match_info["plugin_id"], request.match_info["handler_id"], url, body
            )
        )
    except PluginError as exc:
        return _failure(exc)


async def plugin_execution(request: web.Request) -> web.Response:
    body = await _optional_json(request)
    manager = _manager(request)
    await manager.set_execution_enabled(bool(body.get("enabled", True)))
    return web.json_response({"execution_enabled": manager.execution_enabled})


async def plugin_marketplace(request: web.Request) -> web.Response:
    try:
        return web.json_response(await _manager(request).marketplace())
    except PluginError as exc:
        return _failure(exc)


def _bearer(request: web.Request) -> str:
    value = request.headers.get("Authorization", "")
    return (
        value[7:].strip()
        if value.lower().startswith("bearer ")
        else str(request.headers.get("X-Swemux-Plugin-Token", ""))
    )


async def plugin_callback(request: web.Request) -> web.Response:
    body = await _optional_json(request)
    operation = str(body.get("operation") or "")
    permission = {
        "projects.list": "projects.read",
        "sessions.list": "sessions.read",
        "terminal.write": "terminal.write",
        "session.stop": "sessions.control",
        "notify": "notifications.write",
        "self.describe": "plugins.self",
    }.get(operation)
    if permission is None:
        return _failure(PluginError("unknown_operation", "unknown plugin callback operation"))
    try:
        grant = _manager(request).authorize(_bearer(request), permission)
    except PluginError as exc:
        return _failure(exc)
    if operation == "projects.list":
        return web.json_response(
            [project.snapshot() for project in request.app[keys.PROJECTS].projects.values()]
        )
    if operation == "sessions.list":
        return web.json_response(
            [session.record.snapshot() for session in request.app[keys.SESSIONS].sessions.values()]
        )
    if operation == "self.describe":
        return web.json_response(
            {
                "plugin_id": grant.plugin_id,
                "version": grant.version,
                "permissions": sorted(grant.permissions),
                "contribution": grant.contribution,
                "session_id": grant.session_id,
            }
        )
    if operation == "terminal.write":
        session = request.app[keys.SESSIONS].sessions.get(str(body.get("session_id") or ""))
        if session is None:
            return _failure(PluginError("session_not_found", "unknown session"))
        if session.approval_input_sink is None:
            return _failure(PluginError("session_unavailable", "session input is unavailable"))
        session.approval_input_sink(str(body.get("data") or ""), f"plugin:{grant.plugin_id}")
        return web.json_response({"ok": True})
    if operation == "session.stop":
        await request.app[keys.SESSIONS].stop(
            str(body.get("session_id") or ""), reason=f"plugin:{grant.plugin_id}"
        )
        return web.json_response({"ok": True})
    if operation == "notify":
        await request.app[keys.EVENTS].emit(
            "plugin_notification",
            source="plugin",
            plugin_id=grant.plugin_id,
            title=str(body.get("title") or grant.plugin_id)[:256],
            message=str(body.get("message") or "")[:4096],
        )
        return web.json_response({"ok": True})
    raise AssertionError(operation)


ROUTES = (
    web.get("/api/plugins", plugin_list),
    web.get("/api/plugins/schema", plugin_schema),
    web.post("/api/plugins/inspect", plugin_inspect),
    web.post("/api/plugins/link", plugin_link),
    web.post("/api/plugins/install", plugin_install),
    web.post("/api/plugins/execution", plugin_execution),
    web.get("/api/plugins/logs", plugin_logs),
    web.get("/api/plugins/link-handlers", plugin_links),
    web.get("/api/plugins/marketplace", plugin_marketplace),
    web.post("/api/plugins/callback", plugin_callback),
    web.post("/api/plugins/{plugin_id}/approve", plugin_approve),
    web.post("/api/plugins/{plugin_id}/enable", plugin_enable),
    web.post("/api/plugins/{plugin_id}/rollback", plugin_rollback),
    web.post("/api/plugins/{plugin_id}/update", plugin_update),
    web.delete("/api/plugins/{plugin_id}", plugin_uninstall),
    web.post("/api/plugins/{plugin_id}/actions/{action_id}", plugin_action),
    web.post("/api/plugins/{plugin_id}/panes/{pane_id}", plugin_pane),
    web.post("/api/plugins/{plugin_id}/links/{handler_id}", plugin_link_activate),
)
