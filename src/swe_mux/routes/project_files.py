"""Reading and writing files inside a Project checkout."""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

from aiohttp import web

from .. import (
    app_keys as keys,
)
from ..config import Config, update_config
from ..file_manager import open_in_file_manager
from ..http_support import apply_security_headers, json_response
from ..project_files import (
    create_project_resource,
    effective_project_ignores,
    ignored_project_path,
    list_project_directories,
    list_project_directory,
    project_path,
    read_project_config,
    read_project_file,
    read_project_image_content,
    search_project_files,
    write_project_config,
    write_project_file,
)
from ..recent_files import read_recent_files
from ..runtime_config import apply_runtime_config
from .support import _project_file_root, _registered_identity, _request_project

log = logging.getLogger(__name__)


async def list_project_files(request: web.Request) -> web.Response:
    """List one Project folder.

    Off the loop, unlike the version that shipped before: the listing was always a blocking
    filesystem walk, and it now also asks Git which of this Project's subdirectories are
    separate worktrees (`nested_worktrees`). Its batch sibling has run in an executor for
    exactly this reason; a subprocess on the event loop stalls every session's WebSocket.
    """
    project = _request_project(request)
    patterns = await asyncio.to_thread(
        effective_project_ignores,
        project.root,
        request.app[keys.CONFIG].project_ignore_patterns,
    )
    result = await asyncio.to_thread(
        list_project_directory,
        project.root,
        request.query.get("path", ""),
        ignore_patterns=patterns,
    )
    return json_response(result)


async def post_project_resource(request: web.Request) -> web.Response:
    project = _request_project(request)
    body = await request.json()
    if not isinstance(body, dict):
        raise ValueError("project resource body must be an object")
    parent = body.get("parent", "")
    name = body.get("name")
    kind = body.get("kind")
    if not isinstance(parent, str):
        raise ValueError("project resource parent must be a string")
    if not isinstance(name, str):
        raise ValueError("project resource name must be a string")
    if not isinstance(kind, str):
        raise ValueError("project resource kind must be a string")

    result = await asyncio.to_thread(
        create_project_resource,
        project.root,
        parent,
        name,
        kind,
    )
    patterns = await asyncio.to_thread(
        effective_project_ignores,
        project.root,
        request.app[keys.CONFIG].project_ignore_patterns,
    )
    result["hidden"] = ignored_project_path(str(result["path"]), patterns)
    return json_response(result, 201)


async def list_project_files_tree(request: web.Request) -> web.Response:
    """Batch-list the root plus every persisted-expanded folder in one round trip.

    Restoring a saved tree otherwise costs one request per open folder, which
    stacks up latency (and HTTP/1.1 connection limits) on a phone over Tailscale.
    Listings are blocking filesystem walks, so run the whole batch off the loop.
    """

    project = _request_project(request)
    configured = request.app[keys.CONFIG].project_ignore_patterns
    paths = request.query.getall("path", [])
    # Always include the root, dedupe, and bound the fan-out so a hostile or
    # runaway query cannot ask us to stat thousands of directories.
    wanted = list(dict.fromkeys(["", *paths]))[:1000]
    result = await asyncio.get_running_loop().run_in_executor(
        None,
        lambda: list_project_directories(
            project.root,
            wanted,
            ignore_patterns=effective_project_ignores(project.root, configured),
        ),
    )
    return json_response(result)


async def list_recent_project_files(request: web.Request) -> web.Response:
    """The Files explorer's Recent view: what Git says was touched here, newest first.

    Deliberately Git-backed rather than an mtime walk - see `recent_files`. The ignore
    patterns are read off the loop because they parse the Project's config file; the Git
    calls are already async and bounded.
    """
    project = _request_project(request)
    patterns = await asyncio.to_thread(
        effective_project_ignores,
        project.root,
        request.app[keys.CONFIG].project_ignore_patterns,
    )
    return json_response(await read_recent_files(project.root, ignore_patterns=patterns))


async def search_project_files_route(request: web.Request) -> web.Response:
    started = time.monotonic()
    project = _request_project(request)
    mode = request.query.get("mode", "names")
    if mode not in ("names", "contents", "both"):
        mode = "names"
    query = request.query.get("q", "")
    configured = request.app[keys.CONFIG].project_ignore_patterns
    # The recursive walk (and any content reads) is blocking, so keep it off the event loop.
    # So are the Project config parse and the nested-worktree Git call it now makes, which is
    # why the whole thing is one executor hop rather than a walk with two reads in front of it.
    result = await asyncio.get_running_loop().run_in_executor(
        None,
        lambda: search_project_files(
            project.root,
            query,
            mode=mode,
            ignore_patterns=effective_project_ignores(project.root, configured),
        ),
    )
    log.info(
        "project_file_search_completed project_id=%s mode=%s query_chars=%d results=%d "
        "scanned_files=%d scanned_bytes=%d truncated=%s reason=%s stopped_at=%s duration_ms=%.1f",
        project.id,
        mode,
        len(query.strip()),
        len(result["items"]),
        result["scanned_files"],
        result["scanned_bytes"],
        result["truncated"],
        result["truncated_reason"],
        result["stopped_at"],
        (time.monotonic() - started) * 1000,
    )
    return json_response(result)


async def get_project_file(request: web.Request) -> web.Response:
    project = _request_project(request)
    root = await _project_file_root(project.root, request.query.get("worktree"))
    result = await asyncio.to_thread(read_project_file, root, request.query.get("path", ""))
    if root != project.root:
        result["worktree"] = root
    return json_response(result)


async def get_project_file_content(request: web.Request) -> web.Response:
    """Serve only a revision-pinned image that passed the Project viewer allowlist."""

    project = _request_project(request)
    root = await _project_file_root(project.root, request.query.get("worktree"))
    relative_path = request.query.get("path", "")
    expected_revision = request.query.get("revision", "")
    data, payload = await asyncio.to_thread(
        read_project_image_content,
        root,
        relative_path,
        expected_revision,
    )
    presentation = payload["presentation"]
    response = web.Response(
        body=data,
        headers={
            "Content-Type": str(presentation["mime"]),
            "Content-Length": str(len(data)),
            "Content-Disposition": "inline",
            "Cache-Control": "private, no-store",
            "ETag": f'"{payload["revision"]}"',
            "Accept-Ranges": "none",
            # If the URL is ever navigated to directly, it still cannot become a same-origin
            # active document. The ordinary middleware preserves endpoint-specific CSP values.
            "Content-Security-Policy": "sandbox; default-src 'none'",
        },
    )
    apply_security_headers(response, request)
    return response


async def put_project_file(request: web.Request) -> web.Response:
    project = _request_project(request)
    body = await request.json()
    root = await _project_file_root(project.root, body.get("worktree"))
    try:
        result = await asyncio.to_thread(
            write_project_file,
            root,
            str(body.get("path") or ""),
            str(body.get("text") or ""),
            str(body.get("revision") or "missing"),
        )
    except ValueError as exc:
        if "changed externally" in str(exc):
            return json_response({"error": str(exc), "code": "revision_conflict"}, 409)
        raise
    return json_response(result)


async def reveal_project_resource(request: web.Request) -> web.Response:
    project = _request_project(request)
    body = await request.json()
    root = await _project_file_root(project.root, body.get("worktree"))
    target = project_path(root, str(body.get("path") or ""))
    if not target.exists():
        raise ValueError("project resource does not exist")
    await asyncio.to_thread(open_in_file_manager, target)
    return json_response({"ok": True})


async def ignore_project_resource(request: web.Request) -> web.Response:
    project = _request_project(request)
    body = await request.json()
    scope = str(body.get("scope") or "")
    if scope not in {"global", "project"}:
        raise ValueError("ignore scope must be global or project")
    root = Path(project.root).resolve()
    target = project_path(root, str(body.get("path") or ""))
    if target == root or not target.exists():
        raise ValueError("project resource does not exist")
    relative = target.relative_to(root).as_posix()
    pattern = target.name if scope == "global" else relative

    if scope == "global":
        config: Config = request.app[keys.CONFIG]
        patterns = list(config.project_ignore_patterns)
        added = pattern not in patterns
        if added:
            hot, _restart = update_config(config, {"project_ignore_patterns": [*patterns, pattern]})
            apply_runtime_config(request.app, hot)
            await request.app[keys.EVENTS].emit(
                "configuration_changed",
                source="project_file_browser",
                changed=["project_ignore_patterns"],
            )
        return json_response({"ok": True, "scope": scope, "pattern": pattern, "added": added})

    identity = _registered_identity(project)
    current = await read_project_config(project.root, project=identity)
    if current["status"] == "malformed":
        raise ValueError("project config is malformed; fix it before adding an ignore")
    values = dict(current["values"])
    patterns = list(values.get("ignore_patterns", []))
    added = pattern not in patterns
    if added:
        values["ignore_patterns"] = [*patterns, pattern]
        await write_project_config(project.root, values, current["revision"], project=identity)
        await request.app[keys.EVENTS].emit("project_configuration_changed", project_id=project.id)
    return json_response({"ok": True, "scope": scope, "pattern": pattern, "added": added})


async def put_project_watch(request: web.Request) -> web.Response:
    body = await request.json()
    raw_paths = body.get("paths", [])
    if not isinstance(raw_paths, list) or not all(isinstance(item, str) for item in raw_paths):
        raise ValueError("paths must be an array of project-relative directories")
    watch_id = body.get("watch_id")
    if watch_id is not None and (not isinstance(watch_id, str) or len(watch_id) > 100):
        raise ValueError("watch_id must be a string of 100 characters or fewer")
    project = _request_project(request)
    root = await _project_file_root(project.root, body.get("worktree"))
    lease = request.app[keys.PROJECT_WATCHER].register(project.id, raw_paths, watch_id, root=root)
    return json_response(
        {
            "watch_id": lease.watch_id,
            "paths": list(lease.paths),
            "worktree": lease.root,
            "lease_seconds": 45,
        }
    )


async def delete_project_watch(request: web.Request) -> web.Response:
    request.app[keys.PROJECT_WATCHER].remove(
        request.match_info["project_id"], request.match_info["watch_id"]
    )
    return json_response({"ok": True})


ROUTES: tuple[web.RouteDef, ...] = (
    web.get("/api/projects/{project_id}/files/tree", list_project_files_tree),
    web.get("/api/projects/{project_id}/files/recent", list_recent_project_files),
    web.get("/api/projects/{project_id}/files", list_project_files),
    web.post("/api/projects/{project_id}/resources", post_project_resource),
    web.get("/api/projects/{project_id}/search", search_project_files_route),
    web.get("/api/projects/{project_id}/file", get_project_file),
    web.get("/api/projects/{project_id}/file/content", get_project_file_content),
    web.put("/api/projects/{project_id}/file", put_project_file),
    web.post("/api/projects/{project_id}/reveal", reveal_project_resource),
    web.post("/api/projects/{project_id}/ignore", ignore_project_resource),
    web.put("/api/projects/{project_id}/watch", put_project_watch),
    web.delete("/api/projects/{project_id}/watch/{watch_id}", delete_project_watch),
)
