"""Helpers every route module needs, and nothing a single domain owns."""

from __future__ import annotations

import json
import logging
import secrets
from typing import Any

from aiohttp import web

from .. import (
    app_keys as keys,
)
from .. import (
    git_review,
)
from ..git_projects import ProjectIdentity
from ..harness import is_agent_harness

log = logging.getLogger(__name__)


def refuse_agent_session_caller(request: web.Request, *, operation: str) -> None:
    """Refuse an operator-only mutation arriving from an *agent* session's pane.

    Phase 23 W1: the CLI stamps its calling session onto every request
    (`X-Mux-Caller-Session` / `X-Mux-Caller-Token`, from the pane env), and an
    operator-only route refuses when that identity resolves to a live
    agent-backed session - naming the agent surface instead of acting, so the
    capability gap between the two transports is closed at the route rather
    than in any one client's prose.

    Scope, stated honestly: this constrains *well-behaved* callers. The
    same-host trust decision stands (`agent-messaging.md`), so an agent that
    strips its own identity headers still reaches the route - what changes is
    that the discoverable, documented path refuses, which is what stops the
    capability being one `swemux kill` away for every honestly-written agent.
    A shell pane's operator carries the same headers and passes, because the
    check is the session's backend; an unknown or mismatched identity passes
    too, because guessing about a forged header would refuse the person the
    route exists for.
    """
    headers = getattr(request, "headers", None) or {}
    session_id = headers.get("X-Mux-Caller-Session", "")
    token = headers.get("X-Mux-Caller-Token", "")
    if not session_id or not token:
        return
    session = request.app[keys.SESSIONS].sessions.get(session_id)
    if session is None:
        return
    held = str(getattr(session, "mcp_token", "") or "")
    if not held or not secrets.compare_digest(held, token):
        return
    if not is_agent_harness(str(getattr(session.record, "backend", "") or "")):
        return
    log.info(
        "operator_route_refused_for_agent_session session=%s operation=%s path=%s",
        session_id,
        operation,
        request.path,
    )
    raise web.HTTPForbidden(
        text=json.dumps(
            {
                "error": (
                    f"{operation} is operator surface. This request came from "
                    "an agent session's pane; use the mux MCP tools or "
                    "`swemux agent` instead - they take the same actions "
                    "through the queues, budgets, and provenance that make "
                    "agent-to-agent acts reviewable."
                ),
                "code": "operator_route",
            }
        ),
        content_type="application/json",
    )


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
