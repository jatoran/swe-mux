"""Helpers every route module needs, and nothing a single domain owns."""

from __future__ import annotations

import json
import logging
from typing import Any

from aiohttp import web

from .. import (
    app_keys as keys,
)
from .. import (
    git_review,
)
from ..git_projects import ProjectIdentity

log = logging.getLogger(__name__)


def _registered_identity(project) -> ProjectIdentity:  # type: ignore[no-untyped-def]
    """Identity for an explicitly registered Project.

    Once a route has resolved an explicit Project, its canonical root is
    authoritative. Letting a Project-resource helper re-run Git discovery on that
    root silently retargets a Project registered inside a larger worktree to the
    enclosing toplevel, bleeding notes, config, and observations across Projects.
    """
    return ProjectIdentity(project.id, project.name, project.root, "registered")


async def _optional_json(request: web.Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except (ValueError, json.JSONDecodeError):
        return {}
    return body if isinstance(body, dict) else {}


def _request_project(request: web.Request):  # type: ignore[no-untyped-def]
    project = request.app[keys.PROJECTS].projects.get(request.match_info["project_id"])
    if not project:
        raise ValueError("unknown project")
    return project


def _observations_project(request: web.Request):  # type: ignore[no-untyped-def]
    project = request.app[keys.PROJECTS].projects.get(request.match_info["project_id"])
    if not project:
        raise ValueError("unknown project")
    return project


def _config_identity(request: web.Request, project_id: str) -> ProjectIdentity | None:
    """Registered identity for an explicit `project_id`, when the caller named one.

    The route is cwd-addressed for the Git-scope path, but the per-Project
    settings editor always addresses a registered Project. Naming it keeps a
    Project registered inside a larger worktree from editing the enclosing
    worktree's `.swe-mux/config.toml`.
    """
    if not project_id:
        return None
    project = request.app[keys.PROJECTS].projects.get(project_id)
    if not project:
        raise ValueError("unknown project")
    return _registered_identity(project)


def _project_root_for(app: web.Application, project_id: str, cwd: Any) -> str:
    """Resolve a Project's checkout root from its id, falling back to the run cwd."""
    projects = app.get(keys.PROJECTS)
    if project_id and projects is not None:
        project = projects.projects.get(project_id)
        root = getattr(project, "root", None) if project else None
        if root:
            return str(root)
    return str(cwd or "")


def _query_epoch(request: web.Request, key: str) -> float | None:
    """Parse an epoch-seconds query parameter, tolerating blanks."""
    raw = request.query.get(key)
    if raw is None or not raw.strip():
        return None
    try:
        return float(raw)
    except ValueError:
        raise web.HTTPBadRequest(text=f"{key} must be epoch seconds") from None


async def _project_file_root(project_root: str, requested: object) -> str:
    """Resolve an optional exact worktree root without widening Project ownership."""
    if requested is None or requested == "":
        return project_root
    if not isinstance(requested, str):
        raise git_review.GitReviewError("invalid_worktree", "worktree must be a string")
    repository, _common = await git_review.repository_identity(project_root)
    return await git_review.validate_worktree_root(repository, requested)


def _human_sender_kind(request: web.Request) -> str:
    """`user` for a local act, `remote_user` for an authenticated remote device.

    Derived from the transport, never from the request body: sender provenance
    that a client can claim is provenance that means nothing (`ROADMAP.md`
    Phase 5, "explicit sender provenance"). Remote origin is recorded, not
    privileged — it weakens no check anywhere downstream.
    """
    peer = request.transport.get_extra_info("peername") if request.transport else None
    host = peer[0] if peer else ""
    return "user" if host in {"127.0.0.1", "::1", ""} else "remote_user"
