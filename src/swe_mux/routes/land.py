"""The land queue and the verification command a human approves."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from aiohttp import web

from .. import (
    app_keys as keys,
)
from ..http_support import json_response
from ..land_queue import LandRefusal
from ..land_store import LandConflict, LandStore
from ..project_files import (
    read_project_config,
    write_project_config,
)
from ..worktree_verify import (
    MAX_VERIFY_COMMAND_CHARS,
    VerifyApprovalStore,
    describe_verify_command,
)
from ..worktree_verify import SCRIPT_NAME as VERIFY_SCRIPT_NAME
from .support import _config_identity

log = logging.getLogger(__name__)


def _land_project(request: web.Request) -> Any:
    project_id = request.query.get("project_id") or ""
    project = request.app[keys.PROJECTS].projects.get(project_id)
    if project is None:
        raise ValueError("unknown project")
    return project


async def list_land_requests(request: web.Request) -> web.Response:
    """The queue, for the Git tab's Land panel. Read-only."""
    service = request.app[keys.LAND_QUEUE]
    project_id = request.query.get("project_id") or None
    project = request.app[keys.PROJECTS].projects.get(project_id or "")
    return json_response(
        await service.status(
            project_id=project_id, project_root=project.root if project else None
        )
    )


async def request_land(request: web.Request) -> web.Response:
    """Enqueue an operator-initiated land, or a verify-only run of the same pipeline.

    The operator *is* the authority the grant defers to, so this does not consult
    it - but it consults nothing else differently either: the same preconditions,
    the same fixed vocabulary, the same serialisation.

    `kind` defaults to `"land"`, so a caller written before verify-only existed asks
    for exactly what it always asked for.
    """
    body = await request.json()
    project = request.app[keys.PROJECTS].projects.get(str(body.get("project_id") or ""))
    if project is None:
        raise ValueError("unknown project")
    worktree_root = str(body.get("worktree_root") or "").strip()
    if not worktree_root:
        raise ValueError("worktree_root is required")
    kind = str(body.get("kind") or "land").strip()
    if kind not in ("land", "verify"):
        raise ValueError("kind must be 'land' or 'verify'")
    try:
        row = await request.app[keys.LAND_QUEUE].request(
            project_id=project.id,
            project_root=project.root,
            worktree_root=worktree_root,
            kind=kind,
            origin="operator",
        )
    except LandRefusal as exc:
        return json_response({"error": exc.message, "code": exc.code}, 409)
    return json_response(row, 201)


async def cancel_land_request(request: web.Request) -> web.Response:
    try:
        row = await request.app[keys.LAND_QUEUE].cancel(request.match_info["request_id"])
    except LandConflict as exc:
        return json_response({"error": str(exc), "code": "not_cancellable"}, 409)
    return json_response(row)


async def land_request_events(request: web.Request) -> web.Response:
    """The per-step audit trail for one request: who asked, what verified, what moved."""
    store: LandStore = request.app[keys.LAND_STORE]
    return json_response({"events": await store.events(request.match_info["request_id"])})


async def read_land_verify_command(request: web.Request) -> web.Response:
    """What would run as this worktree's gate, and whether its bytes are approved.

    Returns the approved snapshot beside the current one so the approval prompt can
    show a diff. "The verify script changed" cannot separate a new test target from
    a new `curl | sh`, which is the whole reason Project Action trust retains bytes.
    """
    project = _land_project(request)
    worktree_root = request.query.get("worktree_root") or project.root
    identity = _config_identity(request, project.id)
    config = await read_project_config(project.root, project=identity)
    # The values, never the envelope: the resolver reads `worktree` off this dict, and
    # handing it the envelope is what made the override inert (`read_project_config_values`).
    values = config.get("values") if config.get("status") in {"ready", "read-only"} else {}
    values = values if isinstance(values, dict) else {}
    info = describe_verify_command(
        Path(worktree_root),
        values,
        request.app[keys.VERIFY_APPROVALS],
        project_root=project.root,
    )
    worktree_config = values.get("worktree")
    configured = ""
    if isinstance(worktree_config, dict):
        configured = str(worktree_config.get("verify_command") or "")
    store: LandStore = request.app[keys.LAND_STORE]
    plan = await store.verify_plan(project.root, info.digest or "")
    return json_response(
        {
            **info.public_dict(),
            "project_id": project.id,
            "worktree_root": worktree_root,
            "approved_source": info.approved_snapshot,
            "current_source": info.current_source,
            # The editable half, beside the resolved answer. The editor sets exactly
            # one key, so it is served alone rather than as the whole config: a surface
            # that round-trips every Project field would silently rewrite the ones it
            # does not draw.
            "config_command": configured,
            "config_revision": str(config.get("revision") or "missing"),
            "config_status": str(config.get("status") or "missing"),
            "config_path": str(config.get("path") or ""),
            "script_name": VERIFY_SCRIPT_NAME,
            "script_present": (Path(worktree_root) / VERIFY_SCRIPT_NAME).is_file(),
            # What a byte-identical run last did, when one has passed. Absent means the
            # progress reading will report a step number with no total, which is the
            # honest form rather than an invented one.
            "plan": plan,
        }
    )


async def write_land_verify_command(request: web.Request) -> web.Response:
    """Set or clear `[worktree] verify_command` for a Project.

    Two properties make this safe to expose beside the approval it does *not* grant:

    - It writes exactly one key. The revision guard is the Project config's own, so a
      concurrent edit to some other field loses the race rather than being clobbered.
    - The result is unapproved by construction. Approval is a digest over the bytes
      that will run, so changing them invalidates it without this route saying anything
      about approval at all - which is what keeps "an agent cannot approve the command
      its own land runs" true no matter who calls this.

    An empty command clears the override, falling back to the `.worktree-verify`
    convention. That is a real choice - "use the script in the tree" - and is
    distinguished from "leave it alone" by the field being absent from the request.
    """
    body = await request.json()
    project = request.app[keys.PROJECTS].projects.get(str(body.get("project_id") or ""))
    if project is None:
        raise ValueError("unknown project")
    command = str(body.get("command") or "").strip()
    if len(command) > MAX_VERIFY_COMMAND_CHARS:
        raise ValueError(f"verify_command must be at most {MAX_VERIFY_COMMAND_CHARS} characters")
    identity = _config_identity(request, project.id)
    current = await read_project_config(project.root, project=identity)
    if current.get("status") == "malformed":
        return json_response(
            {
                "error": "this Project's .swe-mux/config.toml cannot be parsed; fix it first",
                "code": "project_config_malformed",
            },
            409,
        )
    values = dict(current.get("values") or {})
    worktree_values = dict(values.get("worktree") or {})
    if command:
        worktree_values["verify_command"] = command
    else:
        worktree_values.pop("verify_command", None)
    if worktree_values:
        values["worktree"] = worktree_values
    else:
        values.pop("worktree", None)
    revision = str(body.get("revision") or current.get("revision") or "missing")
    try:
        written = await write_project_config(project.root, values, revision, project=identity)
    except ValueError as exc:
        if "changed externally" in str(exc):
            return json_response({"error": str(exc), "code": "revision_conflict"}, 409)
        raise
    await request.app[keys.EVENTS].emit(
        "project_configuration_changed", project_id=written["project"]["id"]
    )
    # Its own audit record, exactly as an approval leaves one. Without it, "who changed
    # the gate" would be answerable only from the repository's own history - which is
    # the wrong place to look for a change this endpoint made on this machine.
    await request.app[keys.EVENTS].emit(
        "land_verify_command_changed", source="user", project_id=project.id
    )
    worktree_root = str(body.get("worktree_root") or project.root)
    refreshed = describe_verify_command(
        Path(worktree_root),
        written.get("values") or {},
        request.app[keys.VERIFY_APPROVALS],
        project_root=project.root,
    )
    return json_response(
        {
            **refreshed.public_dict(),
            "project_id": project.id,
            "worktree_root": worktree_root,
            "approved_source": refreshed.approved_snapshot,
            "current_source": refreshed.current_source,
            "config_command": command,
            "config_revision": str(written.get("revision") or "missing"),
            "config_status": str(written.get("status") or "missing"),
            "config_path": str(written.get("path") or ""),
            "script_name": VERIFY_SCRIPT_NAME,
            "script_present": (Path(worktree_root) / VERIFY_SCRIPT_NAME).is_file(),
            "plan": None,
        }
    )


async def approve_land_verify_command(request: web.Request) -> web.Response:
    """Approve the exact bytes that will run as the gate.

    The digest must be the one the caller was shown. A stale digest means the file
    moved between the prompt and the click, and approving it would grant authority
    to bytes nobody read.
    """
    body = await request.json()
    project = request.app[keys.PROJECTS].projects.get(str(body.get("project_id") or ""))
    if project is None:
        raise ValueError("unknown project")
    worktree_root = str(body.get("worktree_root") or project.root)
    digest = str(body.get("digest") or "")
    values = await read_project_config(project.root)
    approvals: VerifyApprovalStore = request.app[keys.VERIFY_APPROVALS]
    info = describe_verify_command(
        Path(worktree_root), values, approvals, project_root=project.root
    )
    if not info.configured or info.digest is None:
        return json_response(
            {"error": "no verification command is configured", "code": "not_configured"}, 409
        )
    if digest != info.digest:
        return json_response(
            {
                "error": "the verification command changed; review it again before approving",
                "code": "digest_mismatch",
                "digest": info.digest,
            },
            409,
        )
    await asyncio.to_thread(
        approvals.approve, project.root, info.digest, snapshot=info.current_source
    )
    refreshed = describe_verify_command(
        Path(worktree_root), values, approvals, project_root=project.root
    )
    await request.app[keys.EVENTS].emit(
        "land_verify_approved", source="user", project_id=project.id
    )
    return json_response({**refreshed.public_dict(), "project_id": project.id})


ROUTES: tuple[web.RouteDef, ...] = (
    # Phase 14 land queue. Read the queue, ask for a land, cancel one, and
    # approve the verification command's exact bytes. No route performs a
    # land: the service's own sweep is the only thing that moves a trunk.
    web.get("/api/land", list_land_requests),
    web.post("/api/land", request_land),
    web.delete("/api/land/{request_id}", cancel_land_request),
    web.get("/api/land/{request_id}/events", land_request_events),
    web.get("/api/land/verify-command", read_land_verify_command),
    # Editing the gate and approving it are deliberately two routes and two
    # acts. A write always leaves the result unapproved (the digest moved), so
    # nothing that can author a command can also authorise it.
    web.put("/api/land/verify-command", write_land_verify_command),
    web.post("/api/land/verify-command/approve", approve_land_verify_command),
)
