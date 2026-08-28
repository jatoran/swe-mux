"""Owned processes and the Preview registry."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any
from uuid import uuid4

from aiohttp import web

from .. import (
    app_keys as keys,
)
from .. import preview_transport
from ..config import Config
from ..http_support import json_response
from ..layouts import attach_leaf, stack_leaf
from ..preview_capture import (
    VIEWPORT_WIDTHS,
    CaptureCapability,
    PreviewCaptureUnavailable,
    capture_capability,
    capture_loopback,
)
from ..processes import PreviewRegistry, ProcessInspector
from ..project_files import (
    is_static_preview_entry,
    project_path,
)
from ..projects import ProjectManager
from .support import _project_file_root

log = logging.getLogger(__name__)


async def list_processes(request: web.Request) -> web.Response:
    session_id = request.query.get("session")
    include_ended = request.query.get("include_ended", "").lower() in {"1", "true", "yes"}
    summary = request.query.get("summary", "").lower() in {"1", "true", "yes"}
    # Opt-in because unique-set-size sampling walks every working set. Views the user
    # opened ask for it; the background rail poll does not.
    unique_memory = request.query.get("unique_memory", "").lower() in {"1", "true", "yes"}
    inspector: ProcessInspector = request.app[keys.PROCESS_INSPECTOR]
    if summary and (session_id or include_ended or unique_memory):
        raise ValueError("summary cannot be combined with session, include_ended, or unique_memory")
    if session_id:
        payload = await inspector.snapshot(session_id, include_ended=include_ended)
    elif summary:
        payload = await inspector.snapshot_summary_all()
    else:
        payload = await inspector.snapshot_all(
            include_ended=include_ended,
            unique_memory=unique_memory,
        )
    return json_response(payload)


async def process_action(request: web.Request) -> web.Response:
    body = await request.json()
    inspector: ProcessInspector = request.app[keys.PROCESS_INSPECTOR]
    return json_response(
        await inspector.act(
            str(body["session_id"]),
            int(body["pid"]),
            str(body["action"]),
            identity_id=str(body.get("identity_id") or "") or None,
        )
    )


async def list_previews(request: web.Request) -> web.Response:
    previews: PreviewRegistry = request.app[keys.PREVIEWS]
    # Reap on read so a client never sees a preview whose server has stopped; the
    # browser drops the matching tab and sidebar row when it disappears from here.
    previews.prune()
    return json_response(await previews.list(request.query.get("session")))


async def _register_static_preview(request: web.Request, body: dict[str, Any]) -> Any:
    """Register a document in a Project checkout as a static Preview.

    Everything security-relevant happens here rather than in the registry: this is
    the layer that knows which Project and which worktree the request is scoped
    to, so it is the only one that can prove the requested path is inside that
    checkout before a route is minted for its directory.
    """
    projects: ProjectManager = request.app[keys.PROJECTS]
    project = projects.projects.get(str(body.get("project_id") or ""))
    if project is None:
        raise ValueError("unknown project")
    root = await _project_file_root(project.root, body.get("worktree"))
    relative = str(body.get("path") or "")
    if not is_static_preview_entry(relative):
        raise ValueError("a static preview entry must be an .html, .htm, or .xhtml file")
    target = await asyncio.to_thread(project_path, root, relative)
    if not await asyncio.to_thread(target.is_file):
        raise ValueError("static preview target is not a file")
    # The served directory, not the file. A page's own `./style.css` and
    # `../assets/x.png` are the normal case, and serving one file would 404 every
    # one of them. `project` widens it to the whole checkout for a page whose
    # absolute paths are repo-root-relative - a built `dist/index.html`.
    resolved_root = Path(root).resolve()
    doc_root = resolved_root if str(body.get("scope") or "file") == "project" else target.parent
    relative_doc_root = doc_root.relative_to(resolved_root).as_posix()
    previews: PreviewRegistry = request.app[keys.PREVIEWS]
    return previews.register_static(
        project_id=project.id,
        doc_root=str(doc_root),
        entry=target.relative_to(doc_root).as_posix(),
        doc_root_relative="" if relative_doc_root == "." else relative_doc_root,
        # "" means the Project root, so a preview opened from a worktree file tab
        # cannot silently serve the primary checkout's copy of the same path.
        worktree="" if resolved_root == Path(project.root).resolve() else str(resolved_root),
        label=target.name,
    )


async def create_preview(request: web.Request) -> web.Response:
    body = await request.json()
    previews: PreviewRegistry = request.app[keys.PREVIEWS]
    static = str(body.get("kind") or "loopback") == "static"
    if static:
        item = await _register_static_preview(request, body)
    else:
        item = await previews.register(
            str(body["session_id"]), str(body["url"]), approved=bool(body.get("approved"))
        )
    if body.get("attach", True):
        projects: ProjectManager = request.app[keys.PROJECTS]
        project = projects.projects[item.project_id]
        # A preview belongs beside whatever spawned it: group it as a tab in the
        # owning session's region instead of splitting off an unrelated one. A
        # static preview has no owning session, so the caller names the view it
        # was launched from - the file tab - and the preview lands in that pane.
        # Fall back to a split when the target has no leaf in this layout.
        grouped = stack_leaf(
            project.layout,
            "preview",
            item.id,
            target_id=str(body.get("target_view_id") or "") or item.session_id,
        )
        project.layout = (
            grouped
            if grouped is not None
            else attach_leaf(
                project.layout,
                "preview",
                item.id,
                target_id=str(body.get("target_session_id") or "") or None,
                direction=str(body.get("direction") or "horizontal"),
            )
        )
        project.layout_revision += 1
        await projects.history.upsert_project(project)
    else:
        project = request.app[keys.PROJECTS].projects[item.project_id]
    await request.app[keys.EVENTS].emit(
        "preview_registered",
        # A static preview is unowned, so it reports no session rather than the
        # empty string a consumer would have to know to read as "none".
        session_id=item.session_id or None,
        source="user",
        preview_id=item.id,
        url=item.url,
    )
    return json_response({"preview": item.snapshot(), "project": project.snapshot()}, 201)


async def delete_preview(request: web.Request) -> web.Response:
    previews: PreviewRegistry = request.app[keys.PREVIEWS]
    preview_id = request.match_info["preview_id"]
    item = previews.items.get(preview_id)
    previews.remove(preview_id)
    await request.app[keys.EVENTS].emit(
        "preview_removed",
        session_id=item.session_id if item else None,
        source="user",
        preview_id=preview_id,
    )
    return json_response({"ok": True})


def _capture_unavailable(capability: CaptureCapability) -> web.Response:
    """The one shape an absent capture backend reports, from either discovery path.

    A 200 with `available: false`, not an error status: an optional integration
    that is simply not installed is a state, not a fault. `state` is the machine
    -readable discriminator and `reason`/`remedy` are what a human reads, so no
    consumer has to parse prose to tell "no Playwright" from "no Chromium".
    """
    log.warning(
        "preview capture unavailable state=%s",
        capability.state,
        extra={"state": capability.state, "remedy": capability.remedy},
    )
    return json_response(
        {
            "available": False,
            "state": capability.state,
            "reason": capability.detail,
            "remedy": capability.remedy,
        }
    )


async def capture_preview(request: web.Request) -> web.Response:
    """Headlessly screenshot a registered preview for the agent.

    Returns a typed unavailable state naming *which* half of the optional backend
    is missing — the Playwright package or the Chromium binary under it — with the
    exact command for that half. Nothing here installs or downloads either. The
    image is saved server-side and its path returned; the browser inserts a
    reference into the target agent's composer — this route never writes a PTY or
    submits anything.
    """
    previews: PreviewRegistry = request.app[keys.PREVIEWS]
    config: Config = request.app[keys.CONFIG]
    item = previews.items.get(request.match_info["preview_id"])
    if not item:
        raise ValueError("unknown preview")
    body = await request.json() if request.can_read_body else {}
    capability = capture_capability()
    if not capability.ready:
        return _capture_unavailable(capability)
    viewport = str(body.get("viewport") or "responsive")
    width = int(body.get("width") or VIEWPORT_WIDTHS.get(viewport, 1280))
    height = int(body.get("height") or 800)
    raw_clip = body.get("clip")
    clip = raw_clip if isinstance(raw_clip, dict) else None
    # A static preview has no upstream port; the daemon's own loopback proxy route
    # is the thing that renders it, and pointing the capture there means the
    # screenshot is of exactly what the pane draws rather than of a second render
    # path that could drift from it.
    url = (
        f"http://127.0.0.1:{config.port}/preview/{item.id}/"
        if getattr(item, "kind", "loopback") == "static"
        else f"http://{item.host}:{item.port}/"
    )
    # Save into the owning project's .swe-mux so a local agent can read it without
    # hunting through the mux data dir; fall back to the data dir if unresolvable.
    session = request.app[keys.SESSIONS].sessions.get(item.session_id)
    root: str | None = None
    if session is not None:
        record = session.record
        root = record.project_root or record.spawn_project_root
        if not root and record.project_id:
            project = request.app[keys.PROJECTS].projects.get(record.project_id)
            root = project.root if project else None
    if not root and item.project_id:
        # The unowned case: a static preview belongs to a Project, not a session,
        # so its shot still lands in the repository an agent is working in.
        owner = request.app[keys.PROJECTS].projects.get(item.project_id)
        root = owner.root if owner else None
    shot_dir = (Path(root) / ".swe-mux" if root else config.data_dir) / "preview-shots"
    out_path = shot_dir / f"{item.id}-{uuid4().hex[:8]}.png"
    try:
        await capture_loopback(url, out_path, width=width, height=height, clip=clip)
    except PreviewCaptureUnavailable as exc:
        # The pre-check said ready and the launch disagreed: a browsers root this
        # host uses that the scan does not know about. Playwright's own verdict
        # wins, and the operator gets the actionable state rather than a raw
        # launch error.
        return _capture_unavailable(exc.capability)
    except Exception as exc:  # noqa: BLE001 - a capture failure must not 500
        log.exception("preview capture failed for %s", url)
        message = str(exc).splitlines()[0][:300] if str(exc).strip() else exc.__class__.__name__
        return json_response({"available": True, "error": f"Capture failed: {message}"}, 502)
    return json_response(
        {
            "available": True,
            "path": str(out_path),
            "url": url,
            "width": width,
            "height": height,
            "region": bool(clip),
        }
    )


ROUTES: tuple[web.RouteDef, ...] = (
    web.get("/api/processes", list_processes),
    web.post("/api/processes/action", process_action),
    web.get("/api/previews", list_previews),
    web.post("/api/previews", create_preview),
    web.delete("/api/previews/{preview_id}", delete_preview),
    web.post("/api/previews/{preview_id}/capture", capture_preview),
    # The proxy handler itself lives in `preview_transport`, with the rewriting
    # it exists to drive; the registry that answers "which Preview is this"
    # lives here, so the route is registered beside its siblings.
    web.route("*", "/preview/{preview_id}/{tail:.*}", preview_transport.preview_proxy),
)
