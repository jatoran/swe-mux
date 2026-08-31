"""Run-bound worktree selection for agent land-queue calls.

Claude's native worktree flow starts the agent process inside its checkout, so
``SessionRecord.git_cwd`` is already the right land target. Codex can create a
worktree after its session starts while the host process remains in the primary
checkout; per-command ``workdir`` values never become session cwd telemetry.

This module keeps those facts separate. A live linked-worktree cwd always wins.
Only a caller whose live cwd still resolves to the Project's primary checkout may
fall back to a persisted, run-bound selection. The land tools remain targetless:
the path-bearing operation is an earlier, auditable binding with its own exact-Git
validation and exclusive live-session ownership check.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .git_monitor import read_git
from .path_identity import same_path
from .worktree_mutation import listed_worktree_entries, worktree_root_matches

log = logging.getLogger(__name__)


class WorktreeContextRefusal(Exception):
    """A checkout selection or resolution the caller can act on."""

    def __init__(self, code: str, message: str, *, status: int = 409) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


@dataclass(frozen=True, slots=True)
class ResolvedLandWorktree:
    root: str
    branch: str
    source: str


def bound_worktree_root(record: Any) -> str:
    """The binding owned by this exact agent run, or an empty string.

    Conversation replacement is an identity boundary. Leaving an old binding on
    the durable record is harmless and diagnostically useful; a new run cannot use
    it without selecting the checkout again.
    """
    root = str(getattr(record, "land_worktree_root", "") or "")
    bound_run = str(getattr(record, "land_worktree_run_id", "") or "")
    current_run = str(getattr(record, "agent_run_id", "") or "")
    return root if root and bound_run and bound_run == current_run else ""


def _path_key(value: str) -> str:
    return os.path.normcase(str(Path(value).resolve()))


def _inside(path: str, root: str) -> bool:
    try:
        child = _path_key(path)
        parent = _path_key(root)
        return os.path.commonpath((child, parent)) == parent
    except (OSError, ValueError):
        return False


def session_occupies_worktree(record: Any, root: str) -> bool:
    """Whether this live record can currently be writing inside ``root``."""
    live_cwd = str(getattr(record, "git_cwd", "") or "")
    selected = bound_worktree_root(record)
    return bool(
        (live_cwd and _inside(live_cwd, root))
        or (selected and same_path(selected, root))
    )


def _entry_for(entries: dict[str, dict[str, Any]], root: str) -> dict[str, Any] | None:
    for entry in entries.values():
        listed = entry.get("worktree")
        if isinstance(listed, str) and same_path(listed, root):
            return entry
    return None


def _primary_entry(entries: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    """Git lists the primary working tree first in porcelain output."""
    return next(iter(entries.values()), None)


def _branch(entry: dict[str, Any] | None) -> str:
    value = entry.get("branch") if entry else None
    return str(value) if isinstance(value, str) else ""


def _short_branch(branch: str) -> str:
    return branch.removeprefix("refs/heads/")


async def _entries(project_root: str) -> dict[str, dict[str, Any]]:
    try:
        return await listed_worktree_entries(project_root)
    except (OSError, ValueError) as exc:
        raise WorktreeContextRefusal(
            "worktree_registry_unavailable",
            f"Git could not read this Project's worktree registry: {exc}",
            status=503,
        ) from exc


async def _live_root(record: Any) -> str:
    cwd = str(getattr(record, "git_cwd", "") or "")
    if not cwd:
        raise WorktreeContextRefusal(
            "no_worktree", "this session has no working directory to resolve."
        )
    code, output = await read_git(cwd, "rev-parse", "--show-toplevel")
    root = output.strip()
    if code or not root:
        raise WorktreeContextRefusal(
            "worktree_context_unavailable",
            "this session's live working directory is not a readable Git checkout.",
        )
    return root


def _other_owner(sessions: Any, caller_id: str, root: str) -> str | None:
    for session in getattr(sessions, "sessions", {}).values():
        record = getattr(session, "record", None)
        if record is None or str(getattr(record, "id", "")) == caller_id:
            continue
        if str(getattr(record, "state", "")) in {"exited", "crashed"}:
            continue
        if session_occupies_worktree(record, root):
            return str(getattr(record, "id", "") or "unknown")
    return None


def _binding_snapshot(record: Any) -> dict[str, Any] | None:
    root = str(getattr(record, "land_worktree_root", "") or "")
    if not root:
        return None
    branch = str(getattr(record, "land_worktree_branch", "") or "")
    run_id = str(getattr(record, "land_worktree_run_id", "") or "")
    return {
        "worktree_root": root,
        "branch": _short_branch(branch),
        "agent_run_id": run_id,
        "current_run": run_id == str(getattr(record, "agent_run_id", "") or ""),
        "bound_at": getattr(record, "land_worktree_bound_at", None),
    }


def _publish(caller: Any, sessions: Any, event_type: str, **payload: Any) -> None:
    publish_update = getattr(caller, "publish_update", None)
    if callable(publish_update):
        publish_update()
    events = getattr(sessions, "events", None)
    emit_background = getattr(events, "emit_background", None)
    if callable(emit_background):
        emit_background(
            event_type,
            session_id=str(getattr(caller.record, "id", "") or ""),
            source="mcp",
            project_id=str(getattr(caller.record, "project_id", "") or ""),
            **payload,
        )


async def _validated_binding(
    caller: Any,
    project_root: str,
    entries: dict[str, dict[str, Any]],
    sessions: Any,
) -> ResolvedLandWorktree:
    record = caller.record
    root = str(getattr(record, "land_worktree_root", "") or "")
    if not root:
        raise WorktreeContextRefusal(
            "worktree_context_required",
            "this session is running from the Project's primary checkout and has no "
            "selected worktree; call use_worktree with the worktree root, then retry.",
        )
    current_run = str(getattr(record, "agent_run_id", "") or "")
    bound_run = str(getattr(record, "land_worktree_run_id", "") or "")
    if not current_run or bound_run != current_run:
        raise WorktreeContextRefusal(
            "worktree_binding_expired",
            "the selected worktree belongs to an earlier agent run; call use_worktree "
            "again before requesting a land.",
        )
    primary_root = str((_primary_entry(entries) or {}).get("worktree") or project_root)
    if same_path(root, primary_root):
        raise WorktreeContextRefusal(
            "worktree_binding_stale",
            "the selected checkout now resolves to the Project's primary checkout.",
        )
    entry = _entry_for(entries, root)
    if entry is None or not await worktree_root_matches(root, root):
        raise WorktreeContextRefusal(
            "worktree_binding_stale",
            "the selected path is no longer an exact linked worktree of this Project.",
        )
    current_branch = _branch(entry)
    bound_branch = str(getattr(record, "land_worktree_branch", "") or "")
    if not current_branch or current_branch != bound_branch:
        raise WorktreeContextRefusal(
            "worktree_binding_stale",
            "the selected worktree's branch changed; call use_worktree again after "
            "checking which branch it now contains.",
        )
    owner = _other_owner(sessions, str(record.id), root)
    if owner:
        raise WorktreeContextRefusal(
            "worktree_in_use",
            f"the selected worktree is owned by live session {owner}.",
        )
    return ResolvedLandWorktree(str(entry["worktree"]), _short_branch(current_branch), "bound")


async def worktree_context(caller: Any, project_root: str, sessions: Any) -> dict[str, Any]:
    """Resolve the checkout a targetless land call would use, without changing it."""
    record = caller.record
    entries = await _entries(project_root)
    live_root = await _live_root(record)
    live_entry = _entry_for(entries, live_root)
    primary_root = str((_primary_entry(entries) or {}).get("worktree") or project_root)
    binding = _binding_snapshot(record)
    if live_entry is None:
        return {
            "source": "unavailable",
            "landable": False,
            "code": "worktree_context_unavailable",
            "message": "this session's live checkout is not registered to its Project.",
            "live_cwd": str(getattr(record, "git_cwd", "") or ""),
            "worktree_root": None,
            "branch": None,
            "binding": binding,
        }
    if not same_path(live_root, primary_root):
        branch = _branch(live_entry)
        if not branch:
            return {
                "source": "live_cwd",
                "landable": False,
                "code": "detached_worktree",
                "message": "the live linked worktree is detached and cannot be landed.",
                "live_cwd": str(getattr(record, "git_cwd", "") or ""),
                "worktree_root": str(live_entry["worktree"]),
                "branch": None,
                "binding": binding,
            }
        primary_branch = _branch(_primary_entry(entries))
        if primary_branch and branch == primary_branch:
            return {
                "source": "live_cwd",
                "landable": False,
                "code": "trunk_branch",
                "message": "the live linked worktree carries the Project's trunk branch.",
                "live_cwd": str(getattr(record, "git_cwd", "") or ""),
                "worktree_root": str(live_entry["worktree"]),
                "branch": _short_branch(branch),
                "binding": binding,
            }
        return {
            "source": "live_cwd",
            "landable": True,
            "code": None,
            "message": "the live linked-worktree cwd is authoritative.",
            "live_cwd": str(getattr(record, "git_cwd", "") or ""),
            "worktree_root": str(live_entry["worktree"]),
            "branch": _short_branch(branch),
            "binding": binding,
        }
    try:
        selected = await _validated_binding(caller, project_root, entries, sessions)
    except WorktreeContextRefusal as exc:
        return {
            "source": "primary_cwd",
            "landable": False,
            "code": exc.code,
            "message": exc.message,
            "live_cwd": str(getattr(record, "git_cwd", "") or ""),
            "worktree_root": None,
            "branch": None,
            "binding": binding,
        }
    return {
        "source": selected.source,
        "landable": True,
        "code": None,
        "message": "the run-bound worktree selection is authoritative while cwd stays on trunk.",
        "live_cwd": str(getattr(record, "git_cwd", "") or ""),
        "worktree_root": selected.root,
        "branch": selected.branch,
        "binding": binding,
    }


async def resolve_land_worktree(
    caller: Any, project_root: str, sessions: Any
) -> ResolvedLandWorktree:
    context = await worktree_context(caller, project_root, sessions)
    if not context["landable"]:
        raise WorktreeContextRefusal(
            str(context["code"] or "worktree_context_unavailable"),
            str(context["message"] or "no landable worktree is selected"),
        )
    return ResolvedLandWorktree(
        str(context["worktree_root"]), str(context["branch"] or ""), str(context["source"])
    )


async def use_worktree(
    caller: Any,
    project_root: str,
    requested: str | None,
    sessions: Any,
) -> dict[str, Any]:
    """Bind one exact linked worktree to this run, or clear the existing binding."""
    record = caller.record
    if requested is None:
        previous = _binding_snapshot(record)
        try:
            result = await worktree_context(caller, project_root, sessions)
        except WorktreeContextRefusal as exc:
            result = {
                "source": "unavailable",
                "landable": False,
                "code": exc.code,
                "message": exc.message,
                "live_cwd": str(getattr(record, "git_cwd", "") or ""),
                "worktree_root": None,
                "branch": None,
                "binding": previous,
            }
        record.land_worktree_root = None
        record.land_worktree_branch = None
        record.land_worktree_run_id = None
        record.land_worktree_bound_at = None
        if previous is not None:
            log.info(
                "agent_worktree_unbound session_id=%s project_id=%s worktree_root=%s branch=%s",
                record.id,
                record.project_id,
                previous["worktree_root"],
                previous["branch"],
            )
            _publish(
                caller,
                sessions,
                "agent_worktree_unbound",
                worktree_root=previous["worktree_root"],
                branch=previous["branch"],
            )
        result["binding"] = None
        if result["source"] not in {"live_cwd", "unavailable"}:
            result.update(
                source="primary_cwd",
                landable=False,
                code="worktree_context_required",
                message=(
                    "the worktree selection was cleared; call use_worktree before "
                    "requesting a land from the primary checkout."
                ),
                worktree_root=None,
                branch=None,
            )
        return result
    if not requested.strip() or not Path(requested).is_absolute():
        raise WorktreeContextRefusal(
            "invalid_worktree", "worktree_root must be a non-empty absolute path.", status=400
        )
    entries = await _entries(project_root)
    live_root = await _live_root(record)
    live_entry = _entry_for(entries, live_root)
    if live_entry is None:
        raise WorktreeContextRefusal(
            "worktree_context_unavailable",
            "this session's live checkout is not registered to its Project.",
        )
    entry = _entry_for(entries, requested)
    if entry is None or not await worktree_root_matches(
        requested, str(entry.get("worktree") or "")
    ):
        raise WorktreeContextRefusal(
            "worktree_not_found",
            "path is not an exact linked worktree root for this Project repository.",
            status=404,
        )
    exact_root = str(entry["worktree"])
    primary = _primary_entry(entries)
    primary_root = str((primary or {}).get("worktree") or project_root)
    if same_path(exact_root, primary_root):
        raise WorktreeContextRefusal(
            "primary_checkout",
            "the Project's primary checkout cannot be selected as a land worktree.",
        )
    branch = _branch(entry)
    if not branch:
        raise WorktreeContextRefusal(
            "detached_worktree", "a detached worktree cannot be selected for landing."
        )
    primary_branch = _branch(primary)
    if primary_branch and branch == primary_branch:
        raise WorktreeContextRefusal(
            "trunk_branch", "a worktree carrying the Project's trunk branch cannot be selected."
        )
    run_id = str(getattr(record, "agent_run_id", "") or "")
    if not run_id:
        raise WorktreeContextRefusal(
            "no_agent_run", "this session has no active agent run to own the selection."
        )
    owner = _other_owner(sessions, str(record.id), exact_root)
    if owner:
        raise WorktreeContextRefusal(
            "worktree_in_use", f"that worktree is owned by live session {owner}."
        )
    previous = _binding_snapshot(record)
    record.land_worktree_root = exact_root
    record.land_worktree_branch = branch
    record.land_worktree_run_id = run_id
    record.land_worktree_bound_at = time.time()
    log.info(
        "agent_worktree_bound session_id=%s agent_run_id=%s project_id=%s "
        "worktree_root=%s branch=%s previous_root=%s",
        record.id,
        run_id,
        record.project_id,
        exact_root,
        _short_branch(branch),
        str((previous or {}).get("worktree_root") or ""),
    )
    _publish(
        caller,
        sessions,
        "agent_worktree_bound",
        agent_run_id=run_id,
        worktree_root=exact_root,
        branch=_short_branch(branch),
        previous_root=(previous or {}).get("worktree_root"),
    )
    binding = _binding_snapshot(record)
    if not same_path(live_root, primary_root):
        live_branch = _branch(live_entry)
        return {
            "source": "live_cwd",
            "landable": True,
            "code": None,
            "message": "the live linked-worktree cwd is authoritative.",
            "live_cwd": str(getattr(record, "git_cwd", "") or ""),
            "worktree_root": str(live_entry["worktree"]),
            "branch": _short_branch(live_branch),
            "binding": binding,
        }
    return {
        "source": "bound",
        "landable": True,
        "code": None,
        "message": "the run-bound worktree selection is authoritative while cwd stays on trunk.",
        "live_cwd": str(getattr(record, "git_cwd", "") or ""),
        "worktree_root": exact_root,
        "branch": _short_branch(branch),
        "binding": binding,
    }
