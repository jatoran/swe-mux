"""Git observation and mutation: the graph, provenance, diffs, and worktrees."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

from aiohttp import web

from .. import (
    app_keys as keys,
)
from .. import (
    git_init,
    git_review,
    session_titles,
    worktree_graveyard,
    worktree_mutation,
)
from ..config import Config
from ..file_manager import open_in_file_manager
from ..git_monitor import _git
from ..git_operations import run_git_mutation
from ..git_projects import resolve_project
from ..git_provenance import summarize_git_provenance
from ..http_support import json_response, log_task_failure
from ..network_usage import (
    compact_json_bytes,
)
from ..project_files import (
    read_project_config,
)
from ..session import (
    SessionManager,
)
from ..worktree_setup import WorktreeSetupResult, run_worktree_setup
from . import sessions

log = logging.getLogger(__name__)


GIT_GRAPH_DEFAULT_LIMIT = 80


GIT_GRAPH_MAX_LIMIT = 200


async def list_worktrees(request: web.Request) -> web.Response:
    """The Map's inventory, in one of two readings, conditionally.

    `detail=summary` withholds every per-file list, which is what a Map row actually
    draws: four lists of up to two hundred file records per worktree, served so a badge
    can say "12 local". The full reading is what a row expansion asks for.

    The `ETag` is over the reading that is being served, so the two cannot be confused
    for one another, and it is the first conditional request anywhere in this daemon:
    the overview is refetched by every client on any session's five-second dirty tick,
    and the great majority of those answers are byte-identical to the one that client
    already has.
    """
    extras = set(request.query) - {"project_id", "detail", "worktree"}
    if extras:
        raise git_review.GitReviewError(
            "invalid_parameters", f"unsupported parameters: {', '.join(sorted(extras))}"
        )
    detail = request.query.get("detail", "full")
    if detail not in {"full", "summary"}:
        raise git_review.GitReviewError(
            "invalid_parameters", "detail must be 'full' or 'summary'"
        )
    project_id = request.query.get("project_id", "")
    project = request.app[keys.PROJECTS].projects.get(project_id)
    if project is None:
        raise git_review.GitReviewError("project_not_found", "unknown Project", 404)
    payload = await git_review.shared_worktree_overview(
        project.id, project.root, project.git_compare_ref, request.query.get("worktree") or None
    )
    if detail == "summary":
        payload = git_review.summarize_overview(payload)
    body = compact_json_bytes(payload)
    etag = f'W/"{hashlib.sha256(body).hexdigest()[:32]}"'
    # Weak, and honestly so: this is a semantic identity over the reading, not a promise
    # about the octets - `compact_json_bytes` is deterministic here, but nothing in the
    # contract says a future serializer must be.
    # `no-cache` is "you may store this, but revalidate before every use" - not "do not
    # store". It is what makes the conditional request happen at all from a browser,
    # which never sends `If-None-Match` for a response it was given no freshness rule
    # for. The client code is unchanged: `fetch` turns the 304 back into a 200 from its
    # own cache, so only the bytes on the wire go away.
    headers = {"ETag": etag, "Cache-Control": "no-cache"}
    if _etag_matches(request.headers.get("If-None-Match"), etag):
        return web.Response(status=304, headers=headers)
    return web.Response(body=body, content_type="application/json", headers=headers)


def _etag_matches(header: str | None, etag: str) -> bool:
    """RFC 9110 `If-None-Match`: `*`, or any listed tag by weak comparison.

    Weak comparison because the tag is weak: `W/"x"` and `"x"` name the same reading,
    and a client library that strips the prefix must not silently stop matching.
    """
    if not header:
        return False
    candidates = [item.strip() for item in header.split(",")]
    if "*" in candidates:
        return True
    target = etag.removeprefix("W/")
    return any(item.removeprefix("W/") == target for item in candidates if item)


async def git_graph(request: web.Request) -> web.Response:
    """Return a bounded, read-only commit graph with Git's own lane layout.

    With `grep` or `author` it is a search instead, run by Git over every commit rather
    than by this handler over the page it happened to fetch. `regex` opts the pattern
    out of `--fixed-strings`; the search is case-insensitive either way.
    """
    extras = set(request.query) - {"project_id", "limit", "grep", "author", "regex"}
    if extras:
        raise git_review.GitReviewError(
            "invalid_parameters", f"unsupported parameters: {', '.join(sorted(extras))}"
        )
    project_id = request.query.get("project_id", "")
    project = request.app[keys.PROJECTS].projects.get(project_id)
    if project is None:
        raise git_review.GitReviewError("project_not_found", "unknown Project", 404)
    raw_limit = request.query.get("limit") or str(GIT_GRAPH_DEFAULT_LIMIT)
    try:
        limit = int(raw_limit)
    except ValueError:
        return json_response({"error": "limit must be an integer"}, 400)
    if not 1 <= limit <= GIT_GRAPH_MAX_LIMIT:
        return json_response({"error": f"limit must be between 1 and {GIT_GRAPH_MAX_LIMIT}"}, 400)
    return json_response(
        await git_review.git_graph(
            project.id,
            project.root,
            limit,
            grep=request.query.get("grep", ""),
            author=request.query.get("author", ""),
            regex=request.query.get("regex", "") in {"1", "true"},
        )
    )


async def git_provenance(request: web.Request) -> web.Response:
    """Return durable commit associations for one Project, session, run, or OID set."""
    extras = set(request.query) - {
        "project_id",
        "session_id",
        "agent_run_id",
        "commit",
        "limit",
        "subject",
    }
    if extras:
        raise git_review.GitReviewError(
            "invalid_parameters", f"unsupported parameters: {', '.join(sorted(extras))}"
        )
    project_id = request.query.get("project_id", "")
    project = request.app[keys.PROJECTS].projects.get(project_id)
    if project is None:
        raise git_review.GitReviewError("project_not_found", "unknown Project", 404)
    raw_limit = request.query.get("limit") or "200"
    try:
        limit = int(raw_limit)
    except ValueError:
        return json_response({"error": "limit must be an integer"}, 400)
    if not 1 <= limit <= 500:
        return json_response({"error": "limit must be between 1 and 500"}, 400)
    commit_oids = [value for value in request.query.getall("commit", []) if value]
    if len(commit_oids) > 500 or any(
        not re.fullmatch(r"[0-9a-fA-F]{40,64}", oid) for oid in commit_oids
    ):
        return json_response({"error": "commit must contain full Git object IDs"}, 400)
    subject = request.query.get("subject", "")[:200]
    history = request.app[keys.HISTORY]
    items = await history.git_provenance(
        project_id=project.id,
        session_id=request.query.get("session_id") or None,
        agent_run_id=request.query.get("agent_run_id") or None,
        commit_oids=commit_oids or None,
        limit=limit,
        subject_query=subject,
    )
    await _decorate_provenance_identity(request.app, items)
    # Reference moves are checkout facts and are not filtered by session: asking
    # "what did this session do" and "what happened to this checkout" are
    # different questions, and answering the first with the second is what used to
    # put a merge nobody in the checkout had made on every session's ledger.
    # A subject search narrows the ledger, so the checkout facts beside it are narrowed
    # to the same commits. Left unfiltered, "Reference movements" would go on listing the
    # whole Project under a result set of three, which reads as the search having failed.
    move_oids = commit_oids or None
    if subject.strip() and not commit_oids:
        move_oids = [str(item.get("commit_oid") or "") for item in items] or ["0" * 40]
    moves = await history.git_ref_moves(project_id=project.id, commit_oids=move_oids)
    # `items` stays one row per session per commit, which is what each piece of
    # evidence is about. `commits` answers the reader's question — who made this
    # commit and whose work is in it — without a second round trip.
    return json_response(
        {
            "items": items,
            "commits": summarize_git_provenance(items),
            "ref_moves": moves,
        }
    )


async def _decorate_provenance_identity(
    app: web.Application, items: list[dict[str, Any]]
) -> None:
    """Add the session's *current* display name and History row to provenance rows.

    `session_name` on a provenance row is durable evidence: it is what the session was
    called when the commit was observed, and rewriting it would corrupt the ledger. It
    is also the wrong thing to show, because the reader is looking at a fleet whose
    sessions are named by the sidebar's rule — a row still reading `claude-0e7d93`
    after a title arrived names a session nobody can find.

    So both travel: `session_name` stays untouched, `display_name` is resolved live
    (session manager first, History second, the snapshot last), and `history_id` is
    the row the History browser opens for an ended session. A row whose session left
    no History behind keeps the snapshot and gets no `history_id`, which is what makes
    the click a no-op instead of a dead end.
    """
    if not items:
        return
    manager: SessionManager = app[keys.SESSIONS]
    lookup_ids: set[str] = set()
    for item in items:
        session_id = str(item.get("session_id") or "")
        run_id = str(item.get("agent_run_id") or "")
        if session_id and session_id not in manager.sessions:
            lookup_ids.add(session_id)
            if run_id:
                lookup_ids.add(run_id)
    rows = await app[keys.HISTORY].history_naming_rows(sorted(lookup_ids))
    run_ids = {
        session_titles.record_run_id(session.record)
        for session in manager.sessions.values()
    }
    run_ids |= {session_titles.row_run_id(row) for row in rows.values()}
    titles = await session_titles.generated_titles(app[keys.AUTOMATION_STORE], run_ids)
    unresolved = 0
    for item in items:
        session_id = str(item.get("session_id") or "")
        run_id = str(item.get("agent_run_id") or "")
        live = manager.sessions.get(session_id)
        if live is not None:
            item["display_name"] = session_titles.record_display_name(live.record, titles)
            item["history_id"] = session_titles.record_run_id(live.record)
            continue
        # The run row is the exact conversation; the session row is the fallback for
        # provenance captured before a run id existed.
        row = rows.get(run_id) or rows.get(session_id)
        if row is None:
            item["display_name"] = str(item.get("session_name") or "")
            unresolved += 1
            continue
        item["display_name"] = session_titles.row_display_name(row, titles)
        item["history_id"] = row["id"]
    if unresolved:
        log.debug(
            "git provenance: %d of %d rows have no live session or History row; "
            "showing the recorded name",
            unresolved,
            len(items),
        )


async def git_commit_changes(request: web.Request) -> web.Response:
    allowed = {"project_id", "parent"}
    extras = set(request.query) - allowed
    if extras:
        raise git_review.GitReviewError(
            "invalid_parameters", f"unsupported parameters: {', '.join(sorted(extras))}"
        )
    project = request.app[keys.PROJECTS].projects.get(request.query.get("project_id", ""))
    if project is None:
        raise git_review.GitReviewError("project_not_found", "unknown Project", 404)
    return json_response(
        await git_review.commit_changes(
            project.id,
            project.root,
            request.match_info["oid"],
            request.query.get("parent") or None,
        )
    )


async def git_diff(request: web.Request) -> web.Response:
    allowed = {
        "project_id",
        "scope",
        "worktree",
        "path",
        "commit",
        "parent",
        "expected_head",
        "patch_hash",
    }
    extras = set(request.query) - allowed
    if extras:
        raise git_review.GitReviewError(
            "invalid_parameters", f"unsupported parameters: {', '.join(sorted(extras))}"
        )
    project = request.app[keys.PROJECTS].projects.get(request.query.get("project_id", ""))
    if project is None:
        raise git_review.GitReviewError("project_not_found", "unknown Project", 404)
    scope = request.query.get("scope", "")
    if scope not in {"unstaged", "staged", "conflicted", "branch", "commit"}:
        raise git_review.GitReviewError("invalid_scope", "unsupported Git diff scope")
    return json_response(
        await git_review.patch_snapshot(
            project_id=project.id,
            project_root=project.root,
            compare_override=project.git_compare_ref,
            scope=scope,  # type: ignore[arg-type]
            path=request.query.get("path", ""),
            worktree=request.query.get("worktree") or None,
            commit=request.query.get("commit") or None,
            requested_parent=request.query.get("parent") or None,
            expected_head=request.query.get("expected_head") or None,
            expected_patch_hash=request.query.get("patch_hash") or None,
        )
    )


async def _spawn_into_worktree(
    app: web.Application,
    spawn_body: Any,
    path: str,
    setup: WorktreeSetupResult | None = None,
) -> dict[str, Any]:
    """Start a session whose cwd is a worktree that was just created.

    Reports failure rather than raising: the worktree already exists and is the durable
    artefact, so a rejected or failed spawn must not unwind it or turn the whole request
    into an error. The caller sees ``status`` and can retry the spawn alone.

    The cwd is forced to the new worktree — a caller cannot use this path to redirect a
    session somewhere else, and `sessions._spawn_from_body` re-validates it against
    `git worktree list` regardless.
    """
    if not isinstance(spawn_body, dict):
        return {"status": "error", "error": "spawn must be an object"}
    if not spawn_body.get("project_id"):
        return {"status": "error", "error": "spawn requires project_id"}
    try:
        forced_body = {**spawn_body, "cwd": path}
        session = (
            await sessions._spawn_from_body(
                app, forced_body, initial_output=setup.terminal_output()
            )
            if setup is not None
            else await sessions._spawn_from_body(app, forced_body)
        )
    except ValueError as exc:
        log.warning(
            "worktree_spawn_failed project_id=%s backend=%s path=%s error_type=validation error=%s",
            spawn_body.get("project_id"),
            spawn_body.get("backend"),
            path,
            exc,
        )
        result: dict[str, Any] = {"status": "error", "error": str(exc)}
        if setup is not None:
            result["setup"] = setup.public_dict()
        return result
    except Exception as exc:  # noqa: BLE001 - the worktree must survive any spawn failure
        log.exception(
            "worktree_spawn_failed project_id=%s backend=%s path=%s error_type=%s",
            spawn_body.get("project_id"),
            spawn_body.get("backend"),
            path,
            type(exc).__name__,
        )
        result = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
        if setup is not None:
            result["setup"] = setup.public_dict()
        return result
    result = {
        "status": "spawned",
        "session_id": session.record.id,
        "cwd": path,
        "session": session.record.snapshot(),
    }
    if setup is not None:
        result["setup"] = setup.public_dict()
    return result


async def _prepare_worktree_setup(
    app: web.Application, spawn_body: Any, path: str
) -> WorktreeSetupResult:
    if not isinstance(spawn_body, dict) or not spawn_body.get("project_id"):
        return WorktreeSetupResult("not_configured")
    project_id = str(spawn_body["project_id"])
    project = app[keys.PROJECTS].projects.get(project_id)
    if project is None:
        return WorktreeSetupResult("not_configured")
    try:
        resolved_path = Path(path).resolve()
        listed = await worktree_mutation.listed_worktree_paths(project.root)
        if str(resolved_path).casefold() not in listed:
            return WorktreeSetupResult(
                "error", error="new worktree does not belong to the selected Project"
            )
        identity = await resolve_project(project.root)
        project_config = await read_project_config(project.root, project=identity)
        if project_config["status"] == "malformed":
            return WorktreeSetupResult(
                "error", error=f"Project config is malformed: {project_config.get('error')}"
            )
        values = (
            project_config["values"] if project_config["status"] in {"ready", "read-only"} else {}
        )
        return await run_worktree_setup(resolved_path, values, project_id=project_id)
    except Exception as exc:  # noqa: BLE001 - setup failure must not block spawn
        log.warning(
            "worktree_setup_preparation_failed project_id=%s path=%s error_type=%s",
            project_id,
            path,
            type(exc).__name__,
        )
        return WorktreeSetupResult("error", error=str(exc))


def _ensure_worktree_parent(config: Config, target: Path) -> None:
    """Create missing target parents only below the configured worktree root."""

    parent = target.parent
    if parent.is_dir():
        return
    configured_root = config.resolved_worktree_root
    try:
        parent.relative_to(configured_root)
    except ValueError as exc:
        raise ValueError({"path": "target parent directory does not exist"}) from exc
    try:
        parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ValueError({"path": f"unable to create target parent: {exc}"}) from exc
    log.info(
        "worktree_parent_created path=%s configured_root=%s",
        parent,
        configured_root,
    )


async def init_repository(request: web.Request) -> web.Response:
    """Create a Git repository for a Project whose folder does not have one yet."""

    operation_id = uuid4().hex
    body = await request.json()
    project = request.app[keys.PROJECTS].projects.get(str(body.get("project_id", "")))
    if project is None:
        raise git_review.GitReviewError("project_not_found", "unknown Project", 404)
    if not Path(project.root).is_dir():
        raise git_review.GitReviewError(
            "root_unavailable", "the Project's folder no longer exists", 404
        )
    # Re-checked here rather than trusted from whatever the caller last read: `git init`
    # on a folder Git already tracks reinitializes it, which is not what any caller of
    # this endpoint is asking for.
    try:
        await git_review.repository_identity(project.root)
    except git_review.GitReviewError as exc:
        if exc.code != "not_git_repository":
            raise
    else:
        raise git_review.GitReviewError(
            "already_initialized", "this Project is already inside a Git repository", 409
        )
    log.info(
        "repository_init_started operation_id=%s project_id=%s root=%s",
        operation_id,
        project.id,
        project.root,
    )
    try:
        result = await git_init.initialize_repository(project.root, operation_id=operation_id)
    except git_init.RepositoryInitError as exc:
        log.warning(
            "repository_init_failed operation_id=%s project_id=%s root=%s",
            operation_id,
            project.id,
            project.root,
        )
        return json_response(
            {"error": str(exc), "code": "git_error", "operation_id": operation_id}, 400
        )
    await request.app[keys.EVENTS].emit("git_changed", project_id=project.id)
    return json_response(
        {
            "ok": True,
            "root": result.root,
            "branch": result.branch,
            "gitignore": result.gitignore,
            "operation_id": operation_id,
        }
    )


async def create_worktree(request: web.Request) -> web.Response:
    started_at = time.perf_counter()
    operation_id = uuid4().hex
    body = await request.json()
    cwd, path = str(body["cwd"]), str(Path(body["path"]).resolve())
    spawn_body = body.get("spawn")
    log.info(
        "worktree_create_started operation_id=%s cwd=%s path=%s branch=%s start_point=%s "
        "spawn_requested=%s project_id=%s backend=%s",
        operation_id,
        cwd,
        path,
        body.get("branch"),
        body.get("start_point"),
        spawn_body is not None,
        spawn_body.get("project_id") if isinstance(spawn_body, dict) else None,
        spawn_body.get("backend") if isinstance(spawn_body, dict) else None,
    )
    if not Path(cwd).is_dir():
        raise ValueError({"cwd": "repository directory does not exist"})
    _ensure_worktree_parent(request.app[keys.CONFIG], Path(path))
    existing = await worktree_mutation.listed_worktree_paths(cwd)
    if path.casefold() in existing:
        raise ValueError({"path": "target is already a registered worktree"})
    # A freshly initialized repository has an unborn HEAD, and `git worktree add`
    # answers that with a raw `fatal: invalid reference: HEAD` - true but useless.
    # Checked here so the failure names the actual fix. Deliberately not fixed by
    # committing anything: repository initialization stages nothing by design. An
    # explicit start_point skips the check - git resolves that ref without HEAD.
    head_code = 0
    if not body.get("start_point"):
        head_code, _head = await _git(cwd, "rev-parse", "--verify", "-q", "HEAD")
    if head_code:
        log.info(
            "worktree_create_refused operation_id=%s cwd=%s reason=no_commits", operation_id, cwd
        )
        return json_response(
            {
                "error": "the repository has no commits yet - make a first commit "
                "before creating a worktree",
                "code": "repository_has_no_commits",
                "operation_id": operation_id,
            },
            400,
        )
    args = ["worktree", "add"]
    if branch := body.get("branch"):
        args.extend(["-b", str(branch)])
    args.append(path)
    if start_point := body.get("start_point"):
        args.append(str(start_point))
    mutation = await run_git_mutation(
        cwd, *args, operation="worktree_create", operation_id=operation_id
    )
    if mutation.code:
        log.warning(
            "worktree_create_failed operation_id=%s cwd=%s path=%s branch=%s "
            "git_code=%s duration_ms=%.1f",
            operation_id,
            cwd,
            path,
            body.get("branch"),
            mutation.code,
            (time.perf_counter() - started_at) * 1000,
        )
        return json_response(
            {
                "error": mutation.output or "git worktree add failed",
                "code": "git_timeout" if mutation.timed_out else "git_error",
                "operation_id": operation_id,
            },
            504 if mutation.timed_out else 400,
        )
    result: dict[str, Any] = {
        "ok": True,
        "path": path,
        "operation_id": operation_id,
        "spawn": {"status": "not_requested"},
    }
    if spawn_body is not None:
        setup = await _prepare_worktree_setup(request.app, spawn_body, path)
        result["spawn"] = await _spawn_into_worktree(request.app, spawn_body, path, setup)
    await request.app[keys.EVENTS].emit("worktree_created", source="user", cwd=cwd, path=path)
    log.info(
        "worktree_create_completed operation_id=%s cwd=%s path=%s branch=%s "
        "spawn_status=%s session_id=%s duration_ms=%.1f",
        operation_id,
        cwd,
        path,
        body.get("branch"),
        result["spawn"]["status"],
        result["spawn"].get("session_id"),
        (time.perf_counter() - started_at) * 1000,
    )
    return json_response(result, 201)


async def spawn_worktree_session(request: web.Request) -> web.Response:
    """Bootstrap an existing Project worktree, then start its session.

    This endpoint is separate from worktree creation so interactive clients can close
    their creation UI as soon as the durable Git artifact exists. Validation remains
    in the setup and ordinary spawn paths, both of which require an exact Git-listed
    worktree owned by the selected Project.
    """
    started_at = time.perf_counter()
    body = await request.json()
    path = str(Path(body["path"]).resolve())
    spawn_body = body.get("spawn")
    log.info(
        "worktree_session_start_requested path=%s project_id=%s backend=%s",
        path,
        spawn_body.get("project_id") if isinstance(spawn_body, dict) else None,
        spawn_body.get("backend") if isinstance(spawn_body, dict) else None,
    )
    setup = await _prepare_worktree_setup(request.app, spawn_body, path)
    result = await _spawn_into_worktree(request.app, spawn_body, path, setup)
    log.info(
        "worktree_session_start_completed path=%s project_id=%s backend=%s "
        "spawn_status=%s session_id=%s setup_status=%s duration_ms=%.1f",
        path,
        spawn_body.get("project_id") if isinstance(spawn_body, dict) else None,
        spawn_body.get("backend") if isinstance(spawn_body, dict) else None,
        result["status"],
        result.get("session_id"),
        setup.status,
        (time.perf_counter() - started_at) * 1000,
    )
    return json_response(result)


async def remove_worktree(request: web.Request) -> web.Response:
    started_at = time.perf_counter()
    operation_id = uuid4().hex
    body = await request.json()
    cwd = str(body["cwd"])
    requested = str(Path(str(body["path"])).resolve())
    force = body.get("force") is True
    log.info(
        "worktree_remove_started operation_id=%s cwd=%s path=%s force=%s",
        operation_id,
        cwd,
        requested,
        force,
    )
    listed = await worktree_mutation.listed_worktree_entries(cwd)
    entry = listed.get(requested.casefold())
    if not entry:
        log.warning(
            "worktree_remove_refused operation_id=%s cwd=%s path=%s "
            "reason=not_registered duration_ms=%.1f",
            operation_id,
            cwd,
            requested,
            (time.perf_counter() - started_at) * 1000,
        )
        return json_response(
            {
                "error": "path is not a registered worktree for this repository",
                "code": "not_registered_worktree",
                "operation_id": operation_id,
            },
            409,
        )
    registered = str(entry["worktree"])
    repaired = False
    if "prunable" in entry:
        worktree_path = Path(registered)
        if not worktree_path.is_dir() or (worktree_path / ".git").exists():
            log.warning(
                "worktree_remove_refused operation_id=%s cwd=%s path=%s "
                "reason=prunable_not_repairable prune_reason=%s duration_ms=%.1f",
                operation_id,
                cwd,
                registered,
                entry.get("prunable"),
                (time.perf_counter() - started_at) * 1000,
            )
            return json_response(
                {
                    "error": "worktree is prunable but cannot be repaired at its registered path",
                    "code": "prunable_worktree",
                    "operation_id": operation_id,
                },
                409,
            )
        log.info(
            "worktree_remove_repair_started operation_id=%s cwd=%s path=%s prune_reason=%s",
            operation_id,
            cwd,
            registered,
            entry.get("prunable"),
        )
        repair = await run_git_mutation(
            cwd,
            "worktree",
            "repair",
            registered,
            operation="worktree_repair",
            operation_id=operation_id,
        )
        try:
            repaired_entries = await worktree_mutation.listed_worktree_entries(cwd)
        except ValueError as exc:
            log.warning(
                "worktree_remove_repair_failed operation_id=%s cwd=%s path=%s "
                "reason=relist_failed git_code=%s duration_ms=%.1f",
                operation_id,
                cwd,
                registered,
                repair.code,
                (time.perf_counter() - started_at) * 1000,
            )
            return json_response(
                {
                    "error": repair.output or str(exc),
                    "code": "git_timeout"
                    if repair.timed_out
                    else "worktree_repair_failed",
                    "operation_id": operation_id,
                },
                504 if repair.timed_out else 409,
            )
        repaired_entry = repaired_entries.get(requested.casefold())
        repair_is_usable = bool(
            repaired_entry
            and "prunable" not in repaired_entry
            and (Path(str(repaired_entry["worktree"])) / ".git").exists()
            and await worktree_mutation.worktree_root_matches(
                str(repaired_entry["worktree"]), requested
            )
        )
        if not repair_is_usable:
            log.warning(
                "worktree_remove_repair_failed operation_id=%s cwd=%s path=%s "
                "reason=unusable_post_state git_code=%s duration_ms=%.1f",
                operation_id,
                cwd,
                registered,
                repair.code,
                (time.perf_counter() - started_at) * 1000,
            )
            return json_response(
                {
                    "error": repair.output or "Git did not restore the worktree registration",
                    "code": "git_timeout"
                    if repair.timed_out
                    else "worktree_repair_failed",
                    "operation_id": operation_id,
                },
                504 if repair.timed_out else 409,
            )
        assert repaired_entry is not None
        registered = str(repaired_entry["worktree"])
        repaired = True
        log.log(
            logging.WARNING if repair.code else logging.INFO,
            "worktree_remove_repair_completed operation_id=%s cwd=%s path=%s git_code=%s",
            operation_id,
            cwd,
            registered,
            repair.code,
        )
    # The fast path, when it applies: the directory is renamed into the repository's
    # graveyard with one call, and what Git removes afterwards is a registration whose
    # tree is already gone - measured to succeed and to drop only this entry, where
    # `git worktree prune` is global and would take unrelated broken checkouts with it.
    # `worktree_mutation.bury_worktree` answers `None` for every case where this
    # would change what the
    # removal means, and the code below is then exactly what it always was.
    buried = await worktree_mutation.bury_worktree(
        registered,
        entry,
        # Git lists the main working tree first, and the pre-repair listing is where
        # that is read from because the main tree is not the thing a repair moves.
        is_main=next(iter(listed), None) == requested.casefold(),
        force=force,
        operation_id=operation_id,
    )
    args = ["worktree", "remove"]
    if force:
        args.append("--force")
    args.append(registered)
    mutation = await run_git_mutation(
        cwd, *args, operation="worktree_remove", operation_id=operation_id
    )
    if mutation.code and buried is not None:
        # Git kept the registration after the tree was renamed away, which is a state
        # nothing here knows how to reason about. Put the tree back exactly where it
        # was and let the ordinary in-place removal produce Git's own answer.
        restored = await asyncio.to_thread(worktree_graveyard.exhume, buried, registered)
        log.warning(
            "worktree_remove_fast_path_reverted operation_id=%s cwd=%s path=%s "
            "git_code=%s restored=%s",
            operation_id,
            cwd,
            registered,
            mutation.code,
            restored,
        )
        buried = None
        if restored:
            mutation = await run_git_mutation(
                cwd, *args, operation="worktree_remove", operation_id=operation_id
            )
    if mutation.code:
        try:
            post_remove_entries = await worktree_mutation.listed_worktree_entries(cwd)
        except ValueError:
            post_remove_entries = {requested.casefold(): entry}
        if requested.casefold() not in post_remove_entries:
            cleanup_status = "removed"
            orphaned_path: str | None = None
            if Path(registered).exists():
                try:
                    orphaned_path = await asyncio.to_thread(
                        worktree_mutation.quarantine_orphaned_worktree, registered, operation_id
                    )
                    cleanup_status = "quarantined"
                except OSError as exc:
                    log.warning(
                        "worktree_remove_cleanup_failed operation_id=%s cwd=%s path=%s "
                        "git_code=%s error_type=%s duration_ms=%.1f",
                        operation_id,
                        cwd,
                        registered,
                        mutation.code,
                        type(exc).__name__,
                        (time.perf_counter() - started_at) * 1000,
                    )
                    return json_response(
                        {
                            "error": "Git removed the worktree registration but its directory "
                            "could not be quarantined",
                            "code": "worktree_cleanup_failed",
                            "operation_id": operation_id,
                            "repaired": repaired,
                            "removed": True,
                            "path": registered,
                        },
                        409,
                    )
            await request.app[keys.EVENTS].emit(
                "worktree_removed", source="user", cwd=cwd, path=registered
            )
            log.warning(
                "worktree_remove_completed operation_id=%s cwd=%s path=%s force=%s "
                "repaired=%s git_code=%s cleanup_status=%s orphaned_path=%s "
                "duration_ms=%.1f",
                operation_id,
                cwd,
                registered,
                force,
                repaired,
                mutation.code,
                cleanup_status,
                orphaned_path or "",
                (time.perf_counter() - started_at) * 1000,
            )
            return json_response(
                {
                    "ok": True,
                    "operation_id": operation_id,
                    "repaired": repaired,
                    "cleanup": {"status": cleanup_status, "path": orphaned_path},
                }
            )
        log.warning(
            "worktree_remove_failed operation_id=%s cwd=%s path=%s force=%s repaired=%s "
            "git_code=%s duration_ms=%.1f",
            operation_id,
            cwd,
            registered,
            force,
            repaired,
            mutation.code,
            (time.perf_counter() - started_at) * 1000,
        )
        return json_response(
            {
                "error": mutation.output or "git worktree remove failed",
                "code": "git_timeout" if mutation.timed_out else "git_error",
                "operation_id": operation_id,
                "repaired": repaired,
            },
            504 if mutation.timed_out else 400,
        )
    cleanup: dict[str, Any] = {"status": "removed", "path": None}
    if buried is not None:
        _schedule_graveyard_purge(request.app, buried.parent, operation_id)
        cleanup = {"status": "purging", "path": str(buried)}
    await request.app[keys.EVENTS].emit("worktree_removed", source="user", cwd=cwd, path=registered)
    log.info(
        "worktree_remove_completed operation_id=%s cwd=%s path=%s force=%s repaired=%s "
        "cleanup_status=%s buried_path=%s duration_ms=%.1f",
        operation_id,
        cwd,
        registered,
        force,
        repaired,
        cleanup["status"],
        cleanup["path"] or "",
        (time.perf_counter() - started_at) * 1000,
    )
    return json_response(
        {
            "ok": True,
            "operation_id": operation_id,
            "repaired": repaired,
            "cleanup": cleanup,
        }
    )


async def reveal_path(request: web.Request) -> web.Response:
    path = Path((await request.json())["path"]).resolve()
    if not path.exists():
        raise ValueError("path does not exist")
    await asyncio.to_thread(open_in_file_manager, path)
    return json_response({"ok": True})




def _schedule_graveyard_purge(app: web.Application, root: Path, operation_id: str) -> None:
    """Delete what the graveyard holds, off the request's clock.

    Everything under the root is purged rather than only what this removal buried:
    a purge interrupted by a daemon shutdown leaves bytes behind, and the next
    removal is the cheapest moment to notice. A cancelled purge is not an error -
    the graveyard is durable and the sweep at daemon start tries again.
    """
    task = asyncio.create_task(
        asyncio.to_thread(worktree_graveyard.purge, root),
        name=f"worktree-graveyard-purge-{operation_id}",
    )
    task.add_done_callback(log_task_failure)
    tasks = app.get(keys.GRAVEYARD_TASKS)
    if isinstance(tasks, set):
        tasks.add(task)
        task.add_done_callback(tasks.discard)


ROUTES: tuple[web.RouteDef, ...] = (
    web.get("/api/git/worktrees", list_worktrees),
    web.get("/api/git/graph", git_graph),
    web.get("/api/git/provenance", git_provenance),
    web.get("/api/git/commits/{oid}/changes", git_commit_changes),
    web.get("/api/git/diff", git_diff),
    web.post("/api/git/init", init_repository),
    web.post("/api/git/worktrees", create_worktree),
    web.post("/api/git/worktrees/session", spawn_worktree_session),
    web.delete("/api/git/worktrees", remove_worktree),
    web.post("/api/reveal", reveal_path),
)
