"""Global and Project notes."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from aiohttp import web

from .. import (
    app_keys as keys,
)
from ..http_support import json_response
from ..project_files import (
    GLOBAL_SCRATCHPAD_ID,
    create_note,
    delete_note,
    note_save_loop_sample,
    project_note_summaries,
    read_global_note,
    read_note,
    write_global_note,
    write_note,
)
from ..projects import ProjectManager
from .support import _registered_identity

log = logging.getLogger(__name__)


def _notes_project(request: web.Request):  # type: ignore[no-untyped-def]
    project = request.app[keys.PROJECTS].projects.get(request.match_info["project_id"])
    if not project:
        raise ValueError("unknown project")
    return project


def _storage_note_id(project, note_id: str) -> str:  # type: ignore[no-untyped-def]
    # The initial note has historically used the Project id in layout resources.
    # Keep that stable while storing it at the existing `project.md` path.
    return "project" if note_id == project.id else note_id


def _global_note_id(request: web.Request) -> str:
    note_id = request.match_info["note_id"]
    if note_id != GLOBAL_SCRATCHPAD_ID:
        raise ValueError("unknown global note")
    return note_id


async def get_global_note(request: web.Request) -> web.Response:
    note_id = _global_note_id(request)
    note = await read_global_note(
        request.app[keys.CONFIG].data_dir,
        note_id,
        default_title="Scratchpad",
    )
    return json_response(note)


async def put_global_note(request: web.Request) -> web.Response:
    note_id = _global_note_id(request)
    body = await request.json()
    if not isinstance(body, dict):
        raise ValueError("note request body must be an object")
    try:
        result = await write_global_note(
            request.app[keys.CONFIG].data_dir,
            note_id,
            str(body.get("markdown") or ""),
            str(body.get("revision") or "missing"),
            title="Scratchpad",
        )
    except ValueError as exc:
        if "changed externally" in str(exc):
            return json_response({"error": str(exc), "code": "revision_conflict"}, 409)
        raise
    log.info(
        "global note saved note_id=%s revision=%s bytes=%d",
        note_id,
        result["revision"],
        result["bytes"],
    )
    await request.app[keys.EVENTS].emit(
        "note_changed",
        source="user",
        scope="global",
        note_id=note_id,
        revision=result["revision"],
    )
    return json_response(result)


async def _legacy_note_titles(request: web.Request, project) -> dict[str, str]:  # type: ignore[no-untyped-def]
    titles: dict[str, str] = {}
    if keys.HISTORY in request.app:
        owners = await request.app[keys.HISTORY].note_owner_labels(project.id)
        titles.update(
            {str(note_id): str(owner.get("name") or note_id) for note_id, owner in owners.items()}
        )
    if keys.SESSIONS in request.app:
        for session in request.app[keys.SESSIONS].sessions.values():
            if session.record.project_id == project.id:
                titles[session.record.id] = session.record.name
    return titles


async def _project_note_items(request: web.Request, project) -> list[dict[str, Any]]:  # type: ignore[no-untyped-def]
    titles = await _legacy_note_titles(request, project)
    return await asyncio.to_thread(
        project_note_summaries,
        project.root,
        default_note_id=project.id,
        default_title=f"{project.name} notes",
        legacy_titles=titles,
    )


async def list_notes(request: web.Request) -> web.Response:
    manager: ProjectManager = request.app[keys.PROJECTS]
    requested = request.query.get("project_id") or ""
    projects = [
        project
        for project in manager.ordered_projects()
        if not requested or project.id == requested
    ]
    if requested and not projects:
        raise ValueError("unknown project")
    items: list[dict[str, Any]] = []
    for project in projects:
        for summary in await _project_note_items(request, project):
            items.append({**summary, "project_id": project.id, "project_name": project.name})
    items.sort(key=lambda item: float(item["updated_at"]), reverse=True)
    return json_response({"items": items})


async def note_save_loop_diagnostic(request: web.Request) -> web.Response:
    """Record one browser-side note save loop the client's guards ended."""
    try:
        sample = note_save_loop_sample(await request.json())
    except ValueError as exc:
        return json_response({"error": str(exc)}, 400)
    return json_response(sample)


async def create_project_note(request: web.Request) -> web.Response:
    project = _notes_project(request)
    body = await request.json()
    if not isinstance(body, dict):
        raise ValueError("note request body must be an object")
    result = await create_note(
        project.root,
        str(body.get("title") or ""),
        project=_registered_identity(project),
    )
    result.update({"project_id": project.id, "project_name": project.name})
    log.info(
        "project note created project_id=%s note_id=%s title=%r",
        project.id,
        result["id"],
        result["title"],
    )
    await request.app[keys.EVENTS].emit(
        "note_changed",
        source="user",
        scope="project",
        project_id=project.id,
        note_id=result["id"],
        revision=result["revision"],
    )
    return json_response(result, 201)


async def get_note(request: web.Request) -> web.Response:
    project = _notes_project(request)
    note_id = request.match_info["note_id"]
    await _project_note_items(request, project)
    storage_id = _storage_note_id(project, note_id)
    note = await read_note(
        project.root,
        storage_id,
        default_title=f"{project.name} notes" if storage_id == "project" else "Untitled note",
        project=_registered_identity(project),
    )
    if not note["exists"]:
        raise ValueError("unknown note")
    note.update({"id": note_id, "project_id": project.id, "project_name": project.name})
    return json_response(note)


async def _write_project_note(request: web.Request, *, title_only: bool = False) -> web.Response:
    project = _notes_project(request)
    note_id = request.match_info["note_id"]
    await _project_note_items(request, project)
    storage_id = _storage_note_id(project, note_id)
    body = await request.json()
    if not isinstance(body, dict):
        raise ValueError("note request body must be an object")
    current = await read_note(
        project.root,
        storage_id,
        default_title=f"{project.name} notes" if storage_id == "project" else "Untitled note",
        project=_registered_identity(project),
    )
    if not current["exists"]:
        raise ValueError("unknown note")
    try:
        result = await write_note(
            project.root,
            storage_id,
            str(current["markdown"] if title_only else body.get("markdown") or ""),
            str(body.get("revision") or "missing"),
            title=str(body.get("title") or "") if title_only else str(current["title"]),
            default_title=f"{project.name} notes" if storage_id == "project" else "Untitled note",
            project=_registered_identity(project),
        )
    except ValueError as exc:
        if "changed externally" in str(exc):
            return json_response({"error": str(exc), "code": "revision_conflict"}, 409)
        raise
    result.update({"id": note_id, "project_id": project.id, "project_name": project.name})
    log.info(
        "project note %s project_id=%s note_id=%s revision=%s",
        "renamed" if title_only else "saved",
        project.id,
        note_id,
        result["revision"],
    )
    await request.app[keys.EVENTS].emit(
        "note_changed",
        source="user",
        scope="project",
        project_id=project.id,
        note_id=note_id,
        revision=result["revision"],
    )
    return json_response(result)


async def put_note(request: web.Request) -> web.Response:
    return await _write_project_note(request)


async def patch_note(request: web.Request) -> web.Response:
    return await _write_project_note(request, title_only=True)


async def delete_project_note(request: web.Request) -> web.Response:
    project = _notes_project(request)
    note_id = request.match_info["note_id"]
    await _project_note_items(request, project)
    body = await request.json()
    if not isinstance(body, dict):
        raise ValueError("note request body must be an object")
    try:
        result = await delete_note(
            project.root,
            _storage_note_id(project, note_id),
            str(body.get("revision") or "missing"),
            project=_registered_identity(project),
        )
    except ValueError as exc:
        if "changed externally" in str(exc):
            return json_response({"error": str(exc), "code": "revision_conflict"}, 409)
        raise
    log.info(
        "project note deleted project_id=%s note_id=%s bytes=%d trashed=%s",
        project.id,
        note_id,
        result["bytes"],
        result["trashed_path"],
    )
    await request.app[keys.EVENTS].emit(
        "note_changed",
        source="user",
        scope="project",
        project_id=project.id,
        note_id=note_id,
        revision="missing",
    )
    return json_response(
        {
            "deleted": True,
            "project_id": project.id,
            "note_id": note_id,
            "trashed_path": result["trashed_path"],
        }
    )


ROUTES: tuple[web.RouteDef, ...] = (
    web.get("/api/global-notes/{note_id}", get_global_note),
    web.put("/api/global-notes/{note_id}", put_global_note),
    web.get("/api/notes", list_notes),
    web.post("/api/notes/save-loop-diagnostic", note_save_loop_diagnostic),
    web.post("/api/projects/{project_id}/notes", create_project_note),
    web.get("/api/projects/{project_id}/notes/{note_id}", get_note),
    web.put("/api/projects/{project_id}/notes/{note_id}", put_note),
    web.patch("/api/projects/{project_id}/notes/{note_id}", patch_note),
    web.delete("/api/projects/{project_id}/notes/{note_id}", delete_project_note),
)
