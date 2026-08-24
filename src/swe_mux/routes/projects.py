"""Projects, their groups, scope, context, artifacts, and the filesystem browser."""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

from aiohttp import web

from .. import (
    app_keys as keys,
)
from ..config import Config, update_config
from ..errors import NotFound
from ..git_projects import ProjectIdentity, resolve_project
from ..harness import (
    agent_harnesses,
    is_agent_harness,
)
from ..history import HistoryIndex
from ..http_support import json_response
from ..profiles import find_profile, profile_payload, resolve_profile
from ..project_context import ProjectContext, ProjectContextService
from ..project_files import (
    read_project_config,
    write_project_config,
)
from ..project_files import (
    revision as file_revision,
)
from ..projects import ProjectManager
from .support import _config_identity, _observations_project

log = logging.getLogger(__name__)


async def list_profiles(request: web.Request) -> web.Response:
    return json_response(profile_payload(request.app[keys.CONFIG]))


async def get_project_config(request: web.Request) -> web.Response:
    identity = _config_identity(request, request.query.get("project_id") or "")
    return json_response(
        await read_project_config(
            request.query.get("cwd") or str(Path.cwd()),
            project=identity,
        )
    )


async def put_project_config(request: web.Request) -> web.Response:
    body = await request.json()
    values = dict(body.get("values") or {})
    identity = _config_identity(request, str(body.get("project_id") or ""))
    project_cwd = Path(str(body.get("cwd") or Path.cwd())).resolve()
    if values.get("default_shell_profile"):
        try:
            resolve_profile(
                request.app[keys.CONFIG], str(values["default_shell_profile"]), project_cwd
            )
        except ValueError as exc:
            raise ValueError({"default_shell_profile": str(exc)}) from exc
    try:
        result = await write_project_config(
            str(project_cwd),
            values,
            str(body.get("revision") or "missing"),
            project=identity,
        )
    except ValueError as exc:
        if "changed externally" in str(exc):
            return json_response({"error": str(exc), "code": "revision_conflict"}, 409)
        raise
    project = await resolve_project(result["project"]["root"])
    await request.app[keys.HISTORY].register_project_scope(project)
    await request.app[keys.EVENTS].emit(
        "project_configuration_changed", project_id=result["project"]["id"]
    )
    return json_response(result)


async def get_project_context(request: web.Request) -> web.Response:
    project = _observations_project(request)
    service: ProjectContextService = request.app[keys.PROJECT_CONTEXTS]
    payload = await asyncio.to_thread(
        service.read,
        ProjectContext(project_id=project.id, project_root=project.root),
    )
    return json_response(payload)


async def put_project_context(request: web.Request) -> web.Response:
    project = _observations_project(request)
    body = await request.json()
    if not isinstance(body.get("markdown"), str):
        raise ValueError("markdown must be a string")
    if not isinstance(body.get("revision"), str):
        raise ValueError("revision must be a string")
    service: ProjectContextService = request.app[keys.PROJECT_CONTEXTS]
    try:
        payload = await asyncio.to_thread(
            service.write,
            ProjectContext(project_id=project.id, project_root=project.root),
            body["markdown"],
            body["revision"],
        )
    except ValueError as exc:
        if "changed externally" in str(exc):
            return json_response({"error": str(exc), "code": "revision_conflict"}, 409)
        raise
    await request.app[keys.EVENTS].emit(
        "project_context_changed",
        source="user",
        project_id=project.id,
        revision=payload["revision"],
    )
    return json_response(payload)


async def resolve_project_scope(request: web.Request) -> web.Response:
    """Explicitly confirm/register the project containing a user-selected directory."""
    body = await request.json()
    try:
        cwd = Path(str(body.get("cwd") or "")).resolve(strict=True)
    except OSError as exc:
        raise ValueError("cwd must be an existing directory") from exc
    if not cwd.is_dir():
        raise ValueError("cwd must be an existing directory")
    project = await resolve_project(cwd)
    await request.app[keys.HISTORY].register_project_scope(project)
    if sid := body.get("session_id"):
        session = request.app[keys.SESSIONS].sessions.get(str(sid))
        if session and session.record.runtime_cwd:
            try:
                same = Path(session.record.runtime_cwd).resolve(strict=True) == cwd
            except OSError:
                same = False
            if same:
                session.record.runtime_project_scope_id = project.id
                session.publish_update()
    return json_response(
        project.__dict__
        if hasattr(project, "__dict__")
        else {
            "id": project.id,
            "label": project.label,
            "root": project.root,
            "source": project.source,
            "repo_group_id": project.repo_group_id,
            "repo_group_label": project.repo_group_label,
        }
    )


async def list_git_projects(request: web.Request) -> web.Response:
    history: HistoryIndex = request.app[keys.HISTORY]
    scopes = await history.project_scopes(include_hidden=request.query.get("include_hidden") == "1")
    try:
        offset = max(0, int(request.query.get("offset", "0")))
        limit = max(1, min(500, int(request.query.get("limit", "200"))))
    except ValueError as exc:
        raise ValueError("project offset and limit must be integers") from exc
    total = len(scopes)
    scopes = scopes[offset : offset + limit]
    live = list(request.app[keys.SESSIONS].sessions.values())
    for scope in scopes:
        scope["root_exists"] = Path(scope["root"]).is_dir()
        scope["live_count"] = sum(item.record.trusted_scope_id == scope["id"] for item in live)
    next_offset = offset + len(scopes)
    return json_response(
        {
            "items": scopes,
            "total": total,
            "next_offset": next_offset if next_offset < total else None,
        }
    )


async def get_project_scope(request: web.Request) -> web.Response:
    history: HistoryIndex = request.app[keys.HISTORY]
    scope = await history.project_scope(request.match_info["scope_id"])
    if not scope:
        raise NotFound(request.match_info["scope_id"], kind="project scope")
    artifacts = await history.artifacts(scope["id"])
    for artifact in artifacts:
        path = (Path(scope["root"]) / artifact["relative_path"]).resolve()
        try:
            artifact["revision"] = file_revision(path.read_bytes())
        except OSError:
            artifact["revision"] = "missing"
    scope["config"] = await read_project_config(scope["root"])
    scope["detached_artifacts"] = [
        item
        for item in artifacts
        if (
            item["owner_type"] == "session"
            and not await history.history_entry(item["owner_id"])
            and item["owner_id"] not in request.app[keys.SESSIONS].sessions
        )
    ]
    scope["artifacts"] = artifacts
    scope["blockers"] = await history.project_blockers(scope["id"])
    scope["sessions"] = [
        item.record.snapshot()
        for item in request.app[keys.SESSIONS].sessions.values()
        if item.record.trusted_scope_id == scope["id"]
    ]
    return json_response(scope)


async def patch_project_scope(request: web.Request) -> web.Response:
    body = await request.json()
    changed = await request.app[keys.HISTORY].set_project_hidden(
        request.match_info["scope_id"], bool(body.get("hidden"))
    )
    if not changed:
        raise NotFound(request.match_info["scope_id"], kind="project scope")
    history = request.app[keys.HISTORY]
    return json_response(await history.project_scope(request.match_info["scope_id"]))


async def forget_project_scope(request: web.Request) -> web.Response:
    result = await request.app[keys.HISTORY].forget_project_scope(request.match_info["scope_id"])
    return json_response(result, 200 if result["forgotten"] else 409)


async def list_artifacts(request: web.Request) -> web.Response:
    return json_response(
        {"items": await request.app[keys.HISTORY].artifacts(request.query.get("project_scope_id"))}
    )


async def transfer_artifact(request: web.Request) -> web.Response:
    history: HistoryIndex = request.app[keys.HISTORY]
    artifact = next(
        (a for a in await history.artifacts() if a["id"] == request.match_info["artifact_id"]), None
    )
    if not artifact:
        raise NotFound(request.match_info["artifact_id"], kind="artifact")
    body = await request.json()
    target = await history.project_scope(str(body.get("project_scope_id") or ""))
    source = await history.project_scope(artifact["project_scope_id"])
    if not source or not target:
        raise ValueError("unknown source or target project scope")
    source_path = (Path(source["root"]) / artifact["relative_path"]).resolve()
    target_path = (Path(target["root"]) / artifact["relative_path"]).resolve()
    if not source_path.is_relative_to(
        Path(source["root"]).resolve()
    ) or not target_path.is_relative_to(Path(target["root"]).resolve()):
        raise ValueError("artifact path escapes project scope")
    action = str(body.get("action") or "keep")
    if action == "keep":
        await history.acknowledge_artifact_placement(artifact["id"], target["id"])
        artifact = next(item for item in await history.artifacts() if item["id"] == artifact["id"])
        return json_response({"artifact": artifact, "action": action})
    if not source_path.is_file() or target_path.exists():
        raise ValueError("source missing or destination already exists")
    expected_revision = str(body.get("revision") or "")
    current_revision = file_revision(source_path.read_bytes())
    if not expected_revision or expected_revision != current_revision:
        return json_response(
            {
                "error": "artifact changed externally; reload before transferring",
                "code": "revision_conflict",
                "revision": current_revision,
            },
            409,
        )
    target_path.parent.mkdir(parents=True, exist_ok=True)
    if action == "copy":
        shutil.copy2(source_path, target_path)
        copy = await history.bind_artifact(
            artifact_id=str(uuid4()),
            kind=artifact["kind"],
            owner_type=artifact["owner_type"],
            owner_id=f"{artifact['owner_id']}-copy-{uuid4().hex[:6]}",
            owner_label=f"{artifact.get('owner_label') or artifact['owner_id']} (copy)",
            project_scope_id=target["id"],
            relative_path=artifact["relative_path"],
        )
        return json_response({"artifact": copy, "action": action})
    if action != "move":
        raise ValueError("action must be keep, copy, or move")
    if (
        source_path.drive
        and target_path.drive
        and source_path.drive.casefold() != target_path.drive.casefold()
    ):
        raise ValueError("cross-volume note moves are not atomic; use Copy instead")
    shutil.move(str(source_path), str(target_path))
    await history.move_artifact_scope(artifact["id"], target["id"], artifact["relative_path"])
    return json_response(
        {
            "artifact": next(a for a in await history.artifacts() if a["id"] == artifact["id"]),
            "action": action,
        }
    )


async def list_pinned_directories(request: web.Request) -> web.Response:
    return json_response({"paths": request.app[keys.CONFIG].pinned_directories})


async def pin_directory(request: web.Request) -> web.Response:
    config: Config = request.app[keys.CONFIG]
    path = str(Path(str((await request.json()).get("path", ""))).resolve())
    if not Path(path).is_dir():
        raise ValueError({"path": "directory does not exist"})
    values = list(dict.fromkeys([*config.pinned_directories, path]))
    update_config(config, {"pinned_directories": values})
    await request.app[keys.EVENTS].emit("configuration_changed", source="directory_pins")
    return json_response({"paths": values})


async def unpin_directory(request: web.Request) -> web.Response:
    config: Config = request.app[keys.CONFIG]
    path = str(Path(str((await request.json()).get("path", ""))).resolve())
    values = [item for item in config.pinned_directories if item.casefold() != path.casefold()]
    update_config(config, {"pinned_directories": values})
    await request.app[keys.EVENTS].emit("configuration_changed", source="directory_pins")
    return json_response({"paths": values})


_FS_ROOTS_TTL = 10.0


_fs_roots_cache: tuple[float, list[str]] | None = None


def _probe_drive_roots() -> list[str]:
    if os.name != "nt":
        return ["/"]
    return [
        f"{letter}:\\" for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ" if Path(f"{letter}:\\").is_dir()
    ]


async def filesystem_roots(request: web.Request) -> web.Response:
    # Probe all 26 drive letters off the event loop and cache briefly: each
    # is_dir() is a blocking syscall and absent/network letters can stall for
    # hundreds of ms. `remote` stays per-request and is never cached.
    global _fs_roots_cache
    now = time.monotonic()
    if _fs_roots_cache is None or now >= _fs_roots_cache[0]:
        roots = await asyncio.to_thread(_probe_drive_roots)
        _fs_roots_cache = (now + _FS_ROOTS_TTL, roots)
    else:
        roots = _fs_roots_cache[1]
    return json_response({"roots": roots, "remote": request.remote not in {"127.0.0.1", "::1"}})


async def filesystem_list(request: web.Request) -> web.Response:
    path = Path(request.query.get("path") or Path.cwd()).resolve()
    if not path.is_dir():
        raise ValueError({"path": "directory does not exist"})
    try:
        directories = sorted(
            (item for item in path.iterdir() if item.is_dir()),
            key=lambda item: item.name.casefold(),
        )[:500]
    except PermissionError as exc:
        raise ValueError({"path": "permission denied"}) from exc
    return json_response(
        {
            "path": str(path),
            "parent": str(path.parent) if path.parent != path else None,
            "directories": [{"name": item.name, "path": str(item)} for item in directories],
        }
    )


async def _projects_payload(request: web.Request) -> list[dict[str, Any]]:
    manager: ProjectManager = request.app[keys.PROJECTS]
    activity = await request.app[keys.HISTORY].project_last_activity()
    history_counts = await request.app[keys.HISTORY].project_history_counts()
    return await asyncio.gather(
        *(
            _project_snapshot(request, item, activity, history_counts)
            for item in manager.ordered_projects()
        )
    )


async def list_projects(request: web.Request) -> web.Response:
    return json_response(await _projects_payload(request))


_PROJECT_USE_REASONS = frozenset({"prompt_submitted", "session_started"})


async def record_project_use(request: web.Request) -> web.Response:
    """Persist an explicit user action as shared Project recency evidence."""

    body = await request.json()
    reason = str(body.get("reason") or "")
    if reason not in _PROJECT_USE_REASONS:
        raise ValueError({"reason": "must be prompt_submitted or session_started"})
    project = await request.app[keys.PROJECTS].touch_used(request.match_info["project_id"])
    await request.app[keys.EVENTS].emit(
        "project_used",
        source="user",
        project_id=project.id,
        last_used_at=project.last_used_at,
        reason=reason,
    )
    return json_response({"project_id": project.id, "last_used_at": project.last_used_at})


async def _project_snapshot(  # type: ignore[no-untyped-def]
    request: web.Request,
    project,
    activity: dict[str, float],
    history_counts: dict[str, int] | None = None,
) -> dict[str, Any]:
    identity = ProjectIdentity(project.id, project.name, project.root, "registered")
    portable = await read_project_config(project.root, project=identity)
    values = portable["values"] if portable["status"] in {"ready", "read-only"} else {}
    public_values = {key: value for key, value in values.items() if key != "resource_open_mode"}
    config: Config = request.app[keys.CONFIG]
    effective = {
        "backend": project.default_backend
        or values.get("preferred_backend")
        or config.default_backend,
        "profile_id": project.default_profile_id
        or values.get("default_shell_profile")
        or config.default_shell_profile,
        "prompt_library_scope": values.get("prompt_library_scope") or "both",
        "notification_sounds_enabled": values.get("notification_sounds_enabled", True),
        # Which launch profile each harness starts with here, after the Project
        # record and the committed file have both had their say. Empty for a harness
        # with no default, which the Run menu renders as the plain harness entry.
        "agent_profile_ids": {
            harness: selection
            for harness in agent_harnesses()
            if (
                selection := project.default_agent_profiles.get(harness)
                or (values.get("default_agent_profiles") or {}).get(harness)
            )
        },
    }
    sources = {
        "backend": "project_record"
        if project.default_backend
        else "project_file"
        if values.get("preferred_backend")
        else "global",
        "profile_id": "project_record"
        if project.default_profile_id
        else "project_file"
        if values.get("default_shell_profile")
        else "global",
        "prompt_library_scope": "project_file" if values.get("prompt_library_scope") else "global",
        "notification_sounds_enabled": "project_file"
        if "notification_sounds_enabled" in values
        else "global",
    }
    snapshot = project.snapshot()
    # Retain the column/parser as a read-compatibility shim for older databases and
    # Project config files, but do not advertise a presentation mode the v6 browser
    # no longer implements.
    snapshot.pop("resource_open_mode", None)
    return {
        **snapshot,
        # Derived, not stored: history already dates every session a Project ever ran,
        # so a second write path that could drift from it would buy nothing. 0 means a
        # Project that has never run one, which the sidebar orders last.
        "last_activity": activity.get(project.id, 0.0),
        "history_count": (history_counts or {}).get(project.id, 0),
        "root_available": Path(project.root).is_dir(),
        "portable_options": public_values,
        "effective_options": effective,
        "option_sources": sources,
        "project_config_status": portable["status"],
    }


async def create_project(request: web.Request) -> web.Response:
    body = await request.json()
    if not isinstance(body.get("create_missing", False), bool):
        raise ValueError({"create_missing": "must be a boolean"})
    registration = await request.app[keys.PROJECTS].register(
        str(body.get("name") or Path(str(body.get("root") or "")).name or "New project"),
        str(body.get("root") or ""),
        group_id=str(body["group_id"]) if body.get("group_id") else None,
        create_missing=bool(body.get("create_missing", False)),
    )
    project = registration.project
    await request.app[keys.EVENTS].emit(
        "project_restored" if registration.restored else "project_created",
        source="user",
        project_id=project.id,
        root=project.root,
    )
    activity = await request.app[keys.HISTORY].project_last_activity()
    history_counts = await request.app[keys.HISTORY].project_history_counts()
    snapshot = await _project_snapshot(request, project, activity, history_counts)
    return json_response(
        {**snapshot, "restored": registration.restored},
        200 if registration.restored else 201,
    )


async def patch_project(request: web.Request) -> web.Response:
    body = await request.json()
    if "position" in body:
        raise ValueError({"position": "use the Project order endpoint"})
    if "sidebar_visible" in body and not isinstance(body["sidebar_visible"], bool):
        raise ValueError({"sidebar_visible": "must be a boolean"})
    backend = body.get("default_backend")
    if backend is not None and backend != "shell" and not is_agent_harness(backend):
        raise ValueError({"default_backend": "must be shell, a registered agent, or null"})
    config: Config = request.app[keys.CONFIG]
    profile_id = body.get("default_profile_id")
    if profile_id is not None and profile_id not in {
        profile.id for profile in config.shell_profiles if profile.backend == "shell"
    }:
        raise ValueError({"default_profile_id": "unknown shell launch profile"})
    if "default_agent_profiles" in body:
        selections = body["default_agent_profiles"]
        if not isinstance(selections, dict) or not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in selections.items()
        ):
            raise ValueError(
                {"default_agent_profiles": "must be a map of backend to launch profile id"}
            )
        for harness, selection in selections.items():
            profile = find_profile(config, selection)
            if profile is None or profile.backend != harness or not profile.enabled:
                # Named individually rather than as one message, because a caller
                # sending several selections needs to know which one is wrong.
                raise ValueError(
                    {
                        f"default_agent_profiles.{harness}": (
                            f"unknown or mismatched launch profile: {selection}"
                        )
                    }
                )
    project = await request.app[keys.PROJECTS].update(request.match_info["project_id"], **body)
    activity = await request.app[keys.HISTORY].project_last_activity()
    history_counts = await request.app[keys.HISTORY].project_history_counts()
    return json_response(await _project_snapshot(request, project, activity, history_counts))


async def reorder_projects(request: web.Request) -> web.Response:
    body = await request.json()
    ordered_ids = body.get("project_ids")
    expected_order = body.get("expected_order")
    if not isinstance(ordered_ids, list) or not all(isinstance(item, str) for item in ordered_ids):
        raise ValueError({"project_ids": "must be an array of Project ids"})
    if not isinstance(expected_order, list) or not all(
        isinstance(item, str) for item in expected_order
    ):
        raise ValueError({"expected_order": "must be the last observed Project order"})
    try:
        projects = await request.app[keys.PROJECTS].reorder(
            ordered_ids, expected_order=expected_order
        )
    except ValueError as exc:
        if "order changed" in str(exc):
            return json_response({"error": str(exc), "code": "order_conflict"}, 409)
        raise
    await request.app[keys.EVENTS].emit(
        "projects_reordered", source="user", project_ids=ordered_ids
    )
    activity = await request.app[keys.HISTORY].project_last_activity()
    history_counts = await request.app[keys.HISTORY].project_history_counts()
    return json_response(
        await asyncio.gather(
            *(_project_snapshot(request, item, activity, history_counts) for item in projects)
        )
    )


async def delete_project(request: web.Request) -> web.Response:
    project_id = request.match_info["project_id"]
    live = [
        item.record
        for item in request.app[keys.SESSIONS].sessions.values()
        if item.record.project_id == project_id
        and item.record.state not in {"exited", "crashed"}
    ]
    if live:
        return json_response(
            {
                "error": (
                    f"{len(live)} live session{'s' if len(live) != 1 else ''} "
                    "must be closed before removal"
                ),
                "code": "project_has_live_sessions",
                "live_sessions": [
                    {"id": item.id, "name": item.name, "state": item.state} for item in live
                ],
            },
            409,
        )
    project = request.app[keys.PROJECTS].projects[project_id]
    history_count = len(await request.app[keys.HISTORY].project_session_ids(project_id))
    await request.app[keys.PROJECTS].remove(project_id)
    await request.app[keys.EVENTS].emit(
        "project_removed",
        source="user",
        project_id=project_id,
        root=project.root,
        history_rows=history_count,
    )
    return json_response({"ok": True, "history_preserved": history_count})


async def list_project_groups(request: web.Request) -> web.Response:
    manager: ProjectManager = request.app[keys.PROJECTS]
    return json_response([item.snapshot() for item in manager.ordered_groups()])


async def create_project_group(request: web.Request) -> web.Response:
    body = await request.json()
    group = await request.app[keys.PROJECTS].create_group(str(body.get("name") or ""))
    return json_response(group.snapshot(), 201)


async def patch_project_group(request: web.Request) -> web.Response:
    group = await request.app[keys.PROJECTS].update_group(
        request.match_info["group_id"], **await request.json()
    )
    return json_response(group.snapshot())


async def reorder_project_groups(request: web.Request) -> web.Response:
    body = await request.json()
    ordered_ids = body.get("group_ids")
    expected_order = body.get("expected_order")
    if not isinstance(ordered_ids, list) or not all(isinstance(item, str) for item in ordered_ids):
        raise ValueError({"group_ids": "must be an array of group ids"})
    if not isinstance(expected_order, list) or not all(
        isinstance(item, str) for item in expected_order
    ):
        raise ValueError({"expected_order": "must be the last observed group order"})
    try:
        groups = await request.app[keys.PROJECTS].reorder_groups(
            ordered_ids, expected_order=expected_order
        )
    except ValueError as exc:
        if "order changed" in str(exc):
            return json_response({"error": str(exc), "code": "order_conflict"}, 409)
        raise
    return json_response([item.snapshot() for item in groups])


async def delete_project_group(request: web.Request) -> web.Response:
    await request.app[keys.PROJECTS].delete_group(request.match_info["group_id"])
    return json_response({"ok": True})


ROUTES: tuple[web.RouteDef, ...] = (
    web.get("/api/profiles", list_profiles),
    web.get("/api/project/config", get_project_config),
    web.put("/api/project/config", put_project_config),
    web.get("/api/projects", list_projects),
    web.post("/api/projects", create_project),
    web.put("/api/projects/order", reorder_projects),
    web.post("/api/projects/{project_id}/used", record_project_use),
    web.patch("/api/projects/{project_id}", patch_project),
    web.delete("/api/projects/{project_id}", delete_project),
    web.get("/api/projects/{project_id}/project-context", get_project_context),
    web.put("/api/projects/{project_id}/project-context", put_project_context),
    web.get("/api/project-groups", list_project_groups),
    web.post("/api/project-groups", create_project_group),
    web.put("/api/project-groups/order", reorder_project_groups),
    web.patch("/api/project-groups/{group_id}", patch_project_group),
    web.delete("/api/project-groups/{group_id}", delete_project_group),
    web.get("/api/git/projects", list_git_projects),
    web.post("/api/git/projects/resolve", resolve_project_scope),
    web.get("/api/git/projects/{scope_id}", get_project_scope),
    web.get("/api/artifacts", list_artifacts),
    web.post("/api/artifacts/{artifact_id}/transfer", transfer_artifact),
    web.get("/api/directories/pins", list_pinned_directories),
    web.post("/api/directories/pins", pin_directory),
    web.delete("/api/directories/pins", unpin_directory),
    web.get("/api/fs/roots", filesystem_roots),
    web.get("/api/fs/list", filesystem_list),
)
