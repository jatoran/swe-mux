"""The session lifecycle: listing, spawning, editing, ending, and attachments."""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
import shutil
import time
from pathlib import Path
from typing import Any, cast, get_args

from aiohttp import web
from aiohttp.multipart import BodyPartReader

from .. import (
    app_keys as keys,
)
from .. import worktree_mutation
from ..adapters import BackendAdapter
from ..approvals import DEFAULT_ALLOW_RULES, normalize_rules
from ..config import Config
from ..git_projects import resolve_project
from ..harness import (
    HARNESSES,
    descriptor,
    has_observable_transcript,
    is_agent_harness,
    publishes_cli_state,
    require_backend,
)
from ..http_support import json_response
from ..models import (
    APPROVAL_MODES,
    ProjectRecord,
    StandingActivityKind,
)
from ..profiles import resolve_agent_profile, resolve_profile
from ..project_files import (
    project_approval_ceiling,
    project_approval_rules,
    read_project_config,
)
from ..projects import ProjectManager
from ..prompt_queue import (
    stage_seed_argv,
)
from ..session import (
    Session,
    SessionManager,
    acknowledge_turns,
    approval_mode_within,
    clear_all_standing_activity,
    clear_standing_activity,
    mark_unread,
    set_approval_mode,
)
from ..session_attachments import (
    MAX_ATTACHMENT_BYTES,
    MAX_IMAGE_BYTES,
    attachment_workspace_root,
    store_session_attachment,
)
from ..session_media import session_media_directory
from ..session_recovery import SessionRecoveryStore
from ..spawn_contract import (
    SpawnRequest,
    apply_spawn_model,
    resolve_contained_cwd,
    resolve_listed_cwd,
    resolve_spawn_model,
)
from . import terminal, voice
from .support import _optional_json

log = logging.getLogger(__name__)


async def list_sessions(request: web.Request) -> web.Response:
    manager: SessionManager = request.app[keys.SESSIONS]
    sessions = []
    readiness = request.app[keys.FLEET].readiness
    for session in manager.sessions.values():
        item = session.record.snapshot()
        item["_snapshot_generation"] = request.app[keys.DAEMON_GENERATION]
        item["_snapshot_revision"] = session.revision
        item["_snapshot_enriched"] = True
        # This readiness is display-only and never authorizes a PTY write. Reuse a
        # bounded classifier verdict so simultaneous browser refreshes cannot make
        # GET /api/sessions repeatedly scan every live terminal on the event loop.
        delivery = readiness.evaluate(
            session,
            record_metrics=False,
            snapshot_pty_cache_seconds=1.0,
        )
        item["delivery_readiness"] = {
            "state": delivery["delivery_state"],
            "reason": delivery["reason"],
            "authorized": False,
        }
        # Present only while something is actually sitting in the composer, so a
        # client can treat presence as the whole signal. The character estimate
        # stays server-side: it is inferred from keystrokes, and a number on
        # screen would be read as a measurement (`composer_input.py`).
        composer = getattr(session, "composer", None)
        if composer is not None and composer.pending:
            item["unsent_input"] = {"since": composer.since}
        sessions.append(item)
    await _decorate_generated_titles(request.app, sessions)
    for field in ("project_id", "state", "backend"):
        value = request.query.get(field.removesuffix("_id") if field == "project_id" else field)
        if value:
            sessions = [s for s in sessions if s[field] == value]
    return json_response(sessions)


async def _decorate_generated_titles(app: web.Application, items: list[dict[str, Any]]) -> None:
    run_ids = {
        str(item.get("agent_run_id") or item.get("id"))
        for item in items
        if item.get("agent_run_id") or item.get("agent_visible")
    }
    if not run_ids:
        return
    # Filtered by run id, not swept off the newest N: a page of old History rows would
    # otherwise fall outside the window and render as never having been titled.
    annotations = await app[keys.AUTOMATION_STORE].annotations(
        agent_run_ids=sorted(run_ids), tag="title", limit=1000
    )
    by_run: dict[str, dict[str, Any]] = {}
    for annotation in annotations:
        run_id = str(annotation["agent_run_id"])
        if run_id in run_ids and run_id not in by_run:
            by_run[run_id] = annotation
    for item in items:
        run_id = str(item.get("agent_run_id") or item.get("id") or "")
        titled = by_run.get(run_id)
        if titled:
            item["generated_title"] = titled["content"]
            item["generated_title_annotation"] = titled


def _decorate_conversation_holders(app: web.Application, items: list[dict[str, Any]]) -> None:
    """Mark history rows whose conversation a live CLI process already holds.

    A row is offered with a Resume action, and a conversation checked out by a
    background agent (or by any other live CLI) cannot take one: the resume would
    spawn a process that refuses and exits. The listing states that rather than
    letting the operator discover it by pressing the button, and it is read live
    rather than stored, because ownership ends when that process does and a stored
    flag would outlive the fact.

    One directory read for the whole page; rows of a harness that publishes no such
    state are left untouched. So is a conversation one of mux's own live panes is
    on: that is a different fact with its own refusal (`conversation_live`, which
    names the pane), and describing a pane the operator can see as "another CLI"
    would be worse than saying nothing.
    """
    manager = app.get(keys.SESSIONS)
    if manager is None or not items:
        return
    holders = manager.conversation_holders()
    if not holders:
        return
    mux_owned = {
        session.record.native_session_id
        for session in manager.sessions.values()
        if session.record.state not in {"exited", "crashed"}
    }
    for item in items:
        backend = str(item.get("backend") or "")
        if backend not in HARNESSES or not publishes_cli_state(require_backend(backend)):
            continue
        native_id = str(item.get("native_id") or "")
        if native_id in mux_owned:
            continue
        holder = holders.get(native_id)
        if holder is None:
            continue
        item["held_by"] = {
            "kind": holder.kind,
            "pid": holder.pid,
            "job_id": holder.job_id,
            "name": holder.name,
            "detail": holder.describe(),
        }


async def _project_agent_profile(
    backend: str,
    project: ProjectRecord,
    project_values: dict[str, Any],
    config: Config,
    *,
    app: web.Application,
    project_id: str,
) -> str | None:
    """This Project's default launch profile for one harness, if it has a usable one.

    Two sources, machine-local first: the Project record (chosen in the UI) and then
    the committed `.swe-mux/config.toml`. The committed one names a profile the user
    defined locally; it never carries argv of its own.

    An unusable default degrades to a diagnostic rather than to a failed spawn. It is
    a *default*, so refusing would make one stale id in a shared repository file stop
    every agent session in the Project from starting, which is a worse outcome than
    starting without the arguments and saying so. An explicitly requested
    `profile_id` is the opposite case and still raises.
    """
    selected = project.default_agent_profiles.get(backend) or (
        project_values.get("default_agent_profiles") or {}
    ).get(backend)
    if not selected:
        return None
    try:
        resolve_agent_profile(config, str(selected), backend)
    except ValueError as exc:
        detail = exc.args[0] if exc.args else str(exc)
        message = detail.get("profile_id", str(detail)) if isinstance(detail, dict) else str(detail)
        log.warning(
            "project_launch_profile_unavailable project_id=%s backend=%s profile_id=%s reason=%s",
            project_id,
            backend,
            selected,
            message,
        )
        await app[keys.EVENTS].emit(
            "project_launch_profile_unavailable",
            source="projects",
            project_id=project_id,
            backend=backend,
            profile_id=str(selected),
            error=message,
        )
        return None
    return str(selected)


async def _spawn_from_body(
    app: web.Application, body: dict[str, Any], *, initial_output: bytes | None = None
) -> Session:
    startup_started_at = time.perf_counter()
    startup_timing_ms: dict[str, float] = {}
    spec = SpawnRequest.parse(body)
    manager: SessionManager = app[keys.SESSIONS]
    projects: ProjectManager = app[keys.PROJECTS]
    project_id = spec.project_id
    if project_id not in projects.projects:
        raise ValueError(f"unknown project: {project_id}")
    owning_project = projects.projects[project_id]
    config: Config = app[keys.CONFIG]
    seed_cwd = owning_project.root
    project_started_at = time.perf_counter()
    project = await resolve_project(seed_cwd)
    startup_timing_ms["project_resolution"] = round(
        (time.perf_counter() - project_started_at) * 1000, 1
    )
    config_started_at = time.perf_counter()
    project_config = await read_project_config(seed_cwd, project=project)
    startup_timing_ms["project_config"] = round((time.perf_counter() - config_started_at) * 1000, 1)
    project_values = (
        project_config["values"] if project_config["status"] in {"ready", "read-only"} else {}
    )
    if project_config["status"] == "malformed":
        await app[keys.EVENTS].emit(
            "project_configuration_error",
            source="project_file",
            path=project_config["path"],
            error=project_config.get("error"),
        )
    backend = (
        spec.backend
        or owning_project.default_backend
        or project_values.get("preferred_backend")
        or config.default_backend
    )
    if spec.completion_mode == "one_shot" and backend != "shell":
        raise ValueError("one-shot completion is available only for shell sessions")
    # A spawn may target a subdirectory of its own project (a task that runs in
    # ./frontend); the containment check is here because this is the only layer
    # that knows which project owns the request.
    cwd = owning_project.root
    worktree_project_root: Path | None = None
    if spec.cwd:
        try:
            cwd = resolve_contained_cwd(spec.cwd, Path(owning_project.root))
        except ValueError:
            # Outside the root. Before refusing, ask git whether this is a worktree of
            # the project's own repository — parallel agent worktrees are the same
            # codebase on another branch and a session belongs in them. The git query
            # only runs on this failure path, so ordinary spawns pay nothing for it.
            cwd = resolve_listed_cwd(
                spec.cwd,
                await worktree_mutation.listed_worktree_paths(owning_project.root),
            )
            worktree_project_root = Path(owning_project.root).resolve()
    executable = spec.executable
    argv = list(spec.argv)
    profile_id: str | None = None
    profile_env: dict[str, str] | None = None
    profile_start_cwd: str | None = None
    if backend == "shell" and not executable:
        profile_id = (
            spec.profile_id
            or owning_project.default_profile_id
            or project_values.get("default_shell_profile")
            or config.default_shell_profile
        )
        profile_started_at = time.perf_counter()
        profile = resolve_profile(config, profile_id, Path(cwd).resolve())
        startup_timing_ms["profile_resolution"] = round(
            (time.perf_counter() - profile_started_at) * 1000, 1
        )
        executable = profile.executable
        argv = [*profile.argv, *argv]
        profile_env = profile.env
        # `cwd_strategy='home'` is the only thing that moves the start directory
        # away from the Project root, and it moves *only* that. Project identity,
        # transcript resolution, and every record stay on the Project cwd: the
        # session still belongs to that Project, it just does not begin its prompt
        # inside it.
        if profile.start_cwd and profile.start_cwd != str(Path(cwd).resolve()):
            profile_start_cwd = profile.start_cwd
    elif is_agent_harness(backend) and not executable:
        # Three argument slots, least specific first: the harness's global
        # `harness_args`, then this profile's, then whatever the request itself asked
        # for. The adapters already concatenate `default_args` before `opts.args`, so
        # prepending here is the whole of the composition and no adapter changes.
        selected = spec.profile_id or await _project_agent_profile(
            backend,
            owning_project,
            project_values,
            config,
            app=app,
            project_id=project_id,
        )
        if selected:
            profile_started_at = time.perf_counter()
            agent_profile = resolve_agent_profile(config, selected, backend)
            startup_timing_ms["profile_resolution"] = round(
                (time.perf_counter() - profile_started_at) * 1000, 1
            )
            profile_id = agent_profile.profile_id
            executable = agent_profile.executable or executable
            argv = [*agent_profile.argv, *argv]
            profile_env = agent_profile.env or None
            log.info(
                "launch_profile_applied project_id=%s backend=%s profile_id=%s args=%d",
                project_id,
                backend,
                profile_id,
                len(agent_profile.argv),
            )
    if spec.model:
        # After the profile slots and before the seed prompt: the model is a flag
        # that replaces whatever those slots set, and the seed prompt is the
        # positional that must stay last on the command line.
        argv = apply_spawn_model(backend, argv, resolve_spawn_model(backend, spec.model))
    if spec.seed_text:
        if not is_agent_harness(backend):
            raise ValueError({"seed_text": "seed prompts require an agent backend"})
        # Short bodies ride argv; over-bound ones are staged into the workspace
        # with a reader prompt (file I/O off-loop). Either way the agent RUNS
        # the prompt — text that must stay unsent travels as `stage_text`.
        argv = [*argv, await asyncio.to_thread(stage_seed_argv, cwd, spec.seed_text)]
    if spec.stage_text and not is_agent_harness(backend):
        raise ValueError({"stage_text": "staged prompts require an agent backend"})
    if worktree_project_root is not None:
        adapter = manager.adapters.get(backend)
        if adapter is not None:
            try:
                await asyncio.to_thread(
                    adapter.preflight_worktree,
                    worktree_project_root,
                    Path(cwd).resolve(),
                )
            except Exception as exc:  # noqa: BLE001 - harness trust is best effort
                log.warning(
                    "worktree_harness_preflight_degraded project_id=%s backend=%s "
                    "path=%s error_type=%s",
                    project_id,
                    backend,
                    cwd,
                    type(exc).__name__,
                )
    spawn_values: dict[str, Any] = dict(
        backend=backend,
        name=spec.name,
        cwd=cwd,
        project_id=project_id,
        exe=executable,
        args=argv,
        shell_profile_id=profile_id,
        profile_env=profile_env,
        extra_env=dict(spec.env),
        project_label=owning_project.name,
    )
    if worktree_project_root is not None:
        spawn_values["worktree_project_root"] = worktree_project_root
    if initial_output:
        spawn_values["initial_output"] = initial_output
    if profile_start_cwd is not None:
        spawn_values["start_cwd"] = profile_start_cwd
    if isinstance(manager, SessionManager):
        spawn_values["project"] = project
        spawn_values["startup_started_at"] = startup_started_at
        spawn_values["startup_timing_ms"] = startup_timing_ms
    if spec.completion_mode != "interactive":
        spawn_values["completion_mode"] = spec.completion_mode
    session = await manager.spawn(**spawn_values)
    if spec.stage_text:
        await _stage_spawn_text(app, session, spec.stage_text)
    return session


# A freshly spawned Claude reaches its composer in about a second (measured live
# 2026-08-20: readiness at ~1.0s). The timeout is generous because a slow disk or
# an MCP handshake can stretch startup; hitting it does not fail the spawn.
STAGE_READY_TIMEOUT_SECONDS = 15.0


STAGE_READY_POLL_SECONDS = 0.05


async def _stage_spawn_text(app: web.Application, session: Any, text: str) -> None:
    """Leave `text` waiting in a just-spawned agent's composer, unsent.

    Spawn → wait for readiness → bracketed paste with NO carriage return, all
    daemon-side: no mounted pane is involved, so this works headless and from
    any device (proven live 2026-08-20 — the staged session stayed idle with
    zero user messages, and a later Enter submitted exactly the staged text).
    The paste goes through `terminal._record_operator_input` so composer shadowing and
    delivery-readiness accounting see it as the partial input it is.

    A session that never reads ready still gets the paste: the PTY buffers
    input written before the CLI listens, and the live probe showed an
    immediate-after-spawn paste arriving intact. The `ready` flag on the event
    records which case this was.
    """
    deadline = time.monotonic() + STAGE_READY_TIMEOUT_SECONDS
    ready = False
    while time.monotonic() < deadline:
        if session.record.state in {"exited", "crashed"}:
            raise ValueError({"stage_text": "the session ended before its text could be staged"})
        if session.record.state == "idle":
            ready = True
            break
        await asyncio.sleep(STAGE_READY_POLL_SECONDS)
    if not ready:
        log.warning(
            "spawn_stage_not_ready session=%s state=%s waited=%.1fs",
            session.record.id,
            session.record.state,
            STAGE_READY_TIMEOUT_SECONDS,
        )
    terminal._record_operator_input(
        app[keys.EVENTS],
        session,
        terminal._composer_insertion(session.record.backend, text),
        source="spawn_stage",
    )
    await app[keys.EVENTS].emit(
        "spawn_text_staged",
        session_id=session.record.id,
        source="spawn_stage",
        characters=len(text),
        ready=ready,
    )


async def spawn_session(request: web.Request) -> web.Response:
    session = await _spawn_from_body(request.app, await request.json())
    return json_response(session.record.snapshot(), 201)


async def get_session(request: web.Request) -> web.Response:
    return json_response(
        request.app[keys.SESSIONS].resolve(request.match_info["sid"]).record.snapshot()
    )


async def patch_session(request: web.Request) -> web.Response:
    session = request.app[keys.SESSIONS].resolve(request.match_info["sid"])
    body = await request.json()
    if "name" in body:
        session.record.name = str(body["name"]).strip() or session.record.name
        session.record.auto_named = False
    if "project_id" in body or "project" in body:
        raise ValueError("a session's owning project cannot be changed")
    if "pin" in body:
        session.record.pinned_attention = bool(body["pin"])
    if "voice_mode" in body:
        mode = body["voice_mode"]
        if mode is not None and mode not in {"off", "on_demand", "auto"}:
            raise ValueError("voice_mode must be off, on_demand, auto, or null to inherit")
        session.record.voice_mode = mode
    if "voice_content" in body:
        content = body["voice_content"]
        if content is not None and content not in {"summary", "verbatim"}:
            raise ValueError("voice_content must be summary, verbatim, or null to inherit")
        session.record.voice_content = content
    await request.app[keys.HISTORY].update_session_metadata(session.record)
    session.publish_update()
    await request.app[keys.EVENTS].emit("session_updated", session_id=session.record.id)
    return json_response(session.record.snapshot())


async def mark_session_read(request: web.Request) -> web.Response:
    """Acknowledge this session's completed turns, or hand-mark it unread.

    Separate from PATCH because it is written on a dwell timer whenever a human
    is actually looking at a pane, and must not carry PATCH's history metadata
    write. The acknowledgement is clamped and monotone in `acknowledge_turns`, so
    a replayed or out-of-order call is a no-op rather than a lost notification.

    Three shapes, because the dwell timer and the user must not be able to
    impersonate each other:

    - `{"turn_seq": N}` (or an empty body) - implicit catch-up. Refused while an
      explicit unread pin is set, which is what keeps a pane the user marked
      unread from being re-read out from under them by the timer.
    - `{"read": true}` - explicit read. Clears the pin and acknowledges every
      counted turn. Written both by the menu item and by a client whose user has
      returned to a pane they had marked unread, which is the pin's designed end:
      it exists to survive the dwell of the visit that set it.
    - `{"read": false}` - explicit unread. Sets the pin and rolls the mark back.
    """
    session = request.app[keys.SESSIONS].resolve(request.match_info["sid"])
    body = await request.json() if request.body_exists else {}
    if not isinstance(body, dict):
        raise ValueError("body must be an object")
    raw = body.get("turn_seq")
    if raw is not None and (not isinstance(raw, int) or isinstance(raw, bool) or raw < 0):
        raise ValueError("turn_seq must be a non-negative integer")
    read = body.get("read")
    if read is not None and not isinstance(read, bool):
        raise ValueError("read must be a boolean")
    changed = (
        mark_unread(session.record)
        if read is False
        else acknowledge_turns(session.record, raw, explicit=read is True)
    )
    if changed:
        session.publish_update()
        # Other devices hold their own copy of the mark; this is what converges
        # them. A client that acknowledged it itself already shows the result.
        await request.app[keys.EVENTS].emit(
            "session_read",
            session_id=session.record.id,
            turn_seq=session.record.read_turn_seq,
            unread=session.record.unread_pin,
        )
    return json_response(
        {
            "id": session.record.id,
            "turn_seq": session.record.turn_seq,
            "read_turn_seq": session.record.read_turn_seq,
            "read_at": session.record.read_at,
            "unread_pin": session.record.unread_pin,
        }
    )


async def regenerate_session_title(request: web.Request) -> web.Response:
    session = request.app[keys.SESSIONS].resolve(request.match_info["sid"])
    record = session.record
    if not has_observable_transcript(record.backend) or not record.agent_run_id:
        raise ValueError("title regeneration requires an active agent run")
    if record.state in {"exited", "crashed"}:
        raise ValueError("an ended session cannot regenerate its title")
    if record.auto_named is False:
        raise ValueError("a manually named session keeps its user title")
    await request.app[keys.EVENTS].emit(
        "title_regenerate_requested",
        session_id=record.id,
        source="user",
        force_title=True,
    )
    return json_response({"ok": True}, 202)


async def clear_session_standing_activity(request: web.Request) -> web.Response:
    """Manually retract a standing-activity annotation the user can see is wrong.

    Every annotation source is evidence about something the daemon cannot
    observe directly, so any of them can be left holding a claim the user knows
    is false - a completion notification that never arrived, a set adopted
    across a daemon restart whose closes were read as history. The decay path
    for that is a 30-minute TTL, which is a long time to look at a session that
    says an agent is still working when nothing is.

    Bounded on purpose: annotations are not states, so this cannot move
    `SessionState`, `awaiting_reason`, or `delivery_state`, and it cannot
    *assert* activity - only retract it. The run-scoped launch bookkeeping goes
    with it, so a later duplicate completion cannot decrement a fresh
    annotation, and the clear is ledgered like every other one (evidence
    `manual`) rather than silently mutating the record.
    """
    session = request.app[keys.SESSIONS].resolve(request.match_info["sid"])
    try:
        body = await request.json()
    except (ValueError, json.JSONDecodeError):
        body = {}
    kind = str((body or {}).get("kind") or "").strip()
    if kind:
        if kind not in get_args(StandingActivityKind):
            raise ValueError(f"unknown standing-activity kind: {kind}")
        cleared = clear_standing_activity(
            session, cast(StandingActivityKind, kind), evidence="manual"
        )
    else:
        cleared = clear_all_standing_activity(session, evidence="manual")
    if cleared:
        observation_state = getattr(session, "observation_state", None)
        if isinstance(observation_state, dict) and kind in {"", "background_tasks"}:
            observation_state.get("background_open", {}).clear()
            observation_state.get("background_labels", {}).clear()
        session.publish_update()
    return json_response(
        {
            "ok": True,
            "cleared": cleared,
            "standing_activity": [
                activity.snapshot() for activity in session.record.standing_activity
            ],
        }
    )


def _approval_project_root(app: web.Application, session: Any) -> Path | None:
    """The Project root whose `.swe-mux/config.toml` governs this session."""
    project_id = getattr(session.record, "project_id", "")
    project = app[keys.PROJECTS].projects.get(project_id) if project_id else None
    if project is not None and project.root:
        return Path(project.root)
    cwd = getattr(session.record, "trusted_cwd", "") or getattr(session.record, "cwd", "")
    return Path(cwd) if cwd else None


async def _approval_context(app: web.Application, session: Any) -> dict[str, Any]:
    """Everything the strip needs to render, and the endpoint needs to decide.

    The two Project-file reads happen here — off the hook path, on an explicit
    request — and never inside a decision, which runs while the agent is parked.
    """
    config = app[keys.CONFIG]
    harness = descriptor(session.record.backend) if session.record.backend in HARNESSES else None
    supported = bool(harness and harness.hook_approval_decisions)
    root = _approval_project_root(app, session)
    if root is None:
        rules, ceiling = None, "wait"
    else:
        rules, ceiling = await asyncio.gather(
            asyncio.to_thread(project_approval_rules, root),
            asyncio.to_thread(project_approval_ceiling, root),
        )
    effective_rules = normalize_rules(list(DEFAULT_ALLOW_RULES) if rules is None else rules)
    if not config.approval_allow_all_permitted and ceiling == "allow_all":
        ceiling = "allowlisted"
    unavailable: str | None = None
    if not config.approval_auto_enabled:
        unavailable = "off for this install"
    elif not supported:
        name = harness.display_name if harness else session.record.backend
        unavailable = f"{name} cannot answer approvals through a hook"
    elif not session.record.agent_run_id:
        unavailable = "no agent conversation is running here"
    elif ceiling == "wait":
        unavailable = "this Project does not permit auto-approval"
    return {
        "supported": supported,
        "enabled": bool(config.approval_auto_enabled),
        "ceiling": ceiling,
        "rules": effective_rules,
        "rules_source": "project" if rules is not None else "default",
        "unavailable": unavailable,
        "ttl_seconds": config.approval_grant_ttl_minutes * 60.0,
        "max_auto": config.approval_max_auto_per_grant,
    }


def _approval_snapshot(session: Any, context: dict[str, Any]) -> dict[str, Any]:
    policy = session.record.approval_policy
    now = time.time()
    return {
        **context,
        "policy": policy.snapshot(),
        # The mode that is actually in force, which is not always the stored one:
        # an expired grant or one made against a replaced conversation still
        # reads its stored mode and applies as `wait`. The UI renders this.
        "effective_mode": policy.effective_mode(session.record.agent_run_id or None, now),
        "modes": list(APPROVAL_MODES),
    }


async def get_session_approvals(request: web.Request) -> web.Response:
    session = request.app[keys.SESSIONS].resolve(request.match_info["sid"])
    context = await _approval_context(request.app, session)
    return json_response(_approval_snapshot(session, context))


async def put_session_approvals(request: web.Request) -> web.Response:
    """Set this conversation's approval mode.

    Refusals are explicit and named rather than silently downgrading to `wait`:
    an operator who selects `allow_all` and gets `wait` with no explanation will
    reasonably conclude the control does not work, and then stop trusting the
    one it does have.
    """
    session = request.app[keys.SESSIONS].resolve(request.match_info["sid"])
    body = await request.json()
    mode = str((body or {}).get("mode") or "").strip()
    if mode not in APPROVAL_MODES:
        return json_response(
            {"error": f"mode must be one of {', '.join(APPROVAL_MODES)}", "code": "invalid_mode"},
            400,
        )
    context = await _approval_context(request.app, session)
    if mode != "wait":
        if context["unavailable"]:
            return json_response(
                {"error": context["unavailable"], "code": "approvals_unavailable"}, 409
            )
        if not approval_mode_within(mode, str(context["ceiling"])):
            return json_response(
                {
                    "error": (
                        f"this Project's approval ceiling is {context['ceiling']}"
                        if context["ceiling"] != "allowlisted"
                        or request.app[keys.CONFIG].approval_allow_all_permitted
                        else "allow_all is disabled for this install"
                    ),
                    "code": "above_ceiling",
                },
                409,
            )
        if mode == "allowlisted" and not context["rules"]:
            return json_response(
                {
                    "error": "this Project's approval allowlist is empty",
                    "code": "empty_allowlist",
                },
                409,
            )
    set_approval_mode(
        session,
        mode,
        rules=list(context["rules"]),
        ttl_seconds=float(context["ttl_seconds"]),
        max_auto=int(context["max_auto"]),
        set_by=str((body or {}).get("set_by") or "ui"),
    )
    session.publish_update()
    await request.app[keys.EVENTS].emit(
        "approval_mode_set",
        session_id=session.record.id,
        source="user",
        mode=mode,
    )
    return json_response(_approval_snapshot(session, context))


async def approve_pending_request(request: web.Request) -> web.Response:
    """Answer the approval this session is showing right now, once.

    Not a mode and deliberately not routed through the policy: this is the
    operator pressing the button the CLI is already displaying, from a device
    that may not have a keyboard on the pane. The guards are the ones the voice
    path established - the same session, the same agent run, this session's own
    screen still classifying as an approval, and the same prompt fingerprint -
    minus voice's two-step challenge, because that exists to compensate for a
    caller who cannot see the screen and a UI button sits next to it.
    """
    session = request.app[keys.SESSIONS].resolve(request.match_info["sid"])
    current = voice._current_voice_approval(session)
    if current is None:
        return json_response(
            {"error": "this session is not showing an approval", "code": "no_approval"}, 409
        )
    operation, fingerprint = current
    expected = str((await _optional_json(request)).get("fingerprint") or "")
    if expected and expected != fingerprint:
        # The dialog changed between render and click. Answering the new one
        # would be approving something the operator never read.
        return json_response(
            {"error": "the approval changed; re-read it", "code": "fingerprint_changed"}, 409
        )
    terminal._record_operator_input(request.app[keys.EVENTS], session, "\r", source="approve-once")
    await request.app[keys.EVENTS].emit(
        "approval_answered_once",
        session_id=session.record.id,
        source="user",
        detail=operation,
    )
    return json_response({"ok": True, "operation": operation, "fingerprint": fingerprint})


async def _discard_session_media(app: web.Application, session_id: str) -> None:
    """Clear a removed session's attachment and paste directory, off the event loop.

    Unbounded filesystem work: the directory holds every image and file the operator
    ever handed this session. Run inline it stalls the loop that carries every other
    session's PTY output, and the sessions with the most attachments are exactly the
    long-lived ones whose close is already the slowest.
    """
    await asyncio.to_thread(
        shutil.rmtree,
        session_media_directory(app[keys.CONFIG].data_dir, session_id),
        ignore_errors=True,
    )


async def delete_session(request: web.Request) -> web.Response:
    """Stop a session and drop it from the registry.

    Unavoidably slow for a live session: the graceful exit keys are typed, the child
    is given time to act on them, an agent mid-turn that never does is force-killed,
    and the run is then persisted. The UI no longer waits for any of that: it removes
    the session on sight and settles this request in the background, so the durable
    `session_removed` event is the only remaining record of how long a close actually
    took and whether it was live when asked. Keep it: without it, a close that quietly
    starts taking ten seconds is invisible to everyone.
    """
    manager: SessionManager = request.app[keys.SESSIONS]
    session = manager.resolve(request.match_info["sid"])
    started = time.monotonic()
    was_live = session.record.state not in {"exited", "crashed"}
    if was_live:
        await manager.stop(session.record.id)
    stopped = time.monotonic()
    manager.sessions.pop(session.record.id, None)
    attachment_locks = request.app.get(keys.ATTACHMENT_LOCKS, {})
    for key in tuple(attachment_locks):
        if key[1] == session.record.id:
            attachment_locks.pop(key, None)
    await _discard_session_media(request.app, session.record.id)
    recovery: SessionRecoveryStore | None = request.app.get(keys.SESSION_RECOVERY)
    if recovery is not None:
        # Dismissal is the one thing that deletes recovery data. An ordinary end
        # only *closes* the row, because "this session finished" and "I am done
        # looking at this session" are different statements, and only the second
        # one is a reason to throw away what it printed.
        await recovery.discard(session.record.id)
    request.app[keys.EVENTS].emit_background(
        "session_removed",
        session_id=session.record.id,
        source="http",
        was_live=was_live,
        exit_code=session.record.exit_code,
        stop_ms=round((stopped - started) * 1000),
        total_ms=round((time.monotonic() - started) * 1000),
    )
    return json_response({"ok": True})


async def relaunch_session(request: web.Request) -> web.Response:
    """Replay a task-launched shell in place: spawn a fresh copy, retire the old.

    Relaunch-from-record: the replacement re-runs the exact retained
    executable/argv/cwd/env, so no task file is re-read and no trust re-approval is
    needed. All four are replayed from the record because a task step's directory and
    environment are spawn inputs in their own right, not something recoverable from
    the argv. Only sessions the daemon marked relaunchable qualify; agent and plain
    shell sessions are rejected so this never touches their lifecycle.

    A **cold** shell is the one deliberate widening of that rule. The gate exists
    to keep this away from a live lifecycle, and a cold session has none: its
    process died with the daemon that owned it, and re-running its recorded argv
    is the only way back. Cold *agents* stay excluded - replaying an agent's argv
    would start a fresh conversation while re-injecting the old one's
    `--session-id`, where the operator asked to return to the conversation. That
    is Resume's job, and a cold agent already has it.
    """
    manager: SessionManager = request.app[keys.SESSIONS]
    old = manager.resolve(request.match_info["sid"])
    cold_shell = bool(old.record.cold and old.record.backend == "shell")
    # The recovered-agent case first, so it gets its own answer rather than the
    # generic refusal: the operator asked for a way back and there is one.
    if old.record.cold and not cold_shell:
        raise ValueError("a recovered agent session is resumed, not relaunched")
    if not old.record.relaunchable and not cold_shell:
        raise ValueError("session is not relaunchable")
    if not old.record.exe:
        raise ValueError("no recorded command to relaunch")
    body = {
        "project_id": old.record.project_id,
        "backend": "shell",
        "name": old.record.name,
        "executable": old.record.exe,
        "argv": list(old.record.args),
        "completion_mode": old.record.completion_mode,
        "env": dict(old.record.spawn_env),
    }
    if old.record.spawn_cwd:
        body["cwd"] = old.record.spawn_cwd
    # Spawn the replacement first: if it raises, the original is left fully intact.
    session = await _spawn_from_body(request.app, body)
    # A cold shell was never a task terminal, so relaunching one must not promote
    # it into one: the flag drives a Relaunch affordance that only makes sense for
    # a step whose argv the daemon vouches for.
    session.record.relaunchable = old.record.relaunchable
    session.publish_update()
    old_id = old.record.id
    if old.record.state not in {"exited", "crashed"}:
        await manager.stop(old_id)
    manager.sessions.pop(old_id, None)
    attachment_locks = request.app.get(keys.ATTACHMENT_LOCKS, {})
    for key in tuple(attachment_locks):
        if key[1] == old_id:
            attachment_locks.pop(key, None)
    await _discard_session_media(request.app, old_id)
    recovery: SessionRecoveryStore | None = request.app.get(keys.SESSION_RECOVERY)
    if recovery is not None:
        # The replacement supersedes it, which is the operator being done with it.
        await recovery.discard(old_id)
    return json_response({"session": session.record.snapshot(), "replaced": old_id}, 201)


async def _upload_session_attachment(
    request: web.Request,
    *,
    image_only: bool,
) -> web.Response:
    allowed_gestures = (
        {"terminal-image", "clipboard-image"} if image_only else {"terminal-attachment"}
    )
    if request.headers.get("X-Mux-User-Gesture") not in allowed_gestures:
        noun = "image upload" if image_only else "attachment upload"
        raise web.HTTPForbidden(text=f"terminal {noun} requires an explicit user action")
    session = request.app[keys.SESSIONS].resolve(request.match_info["sid"])
    adapter: BackendAdapter = request.app[keys.SESSIONS].adapters[session.record.backend]
    if not is_agent_harness(session.record.backend):
        raise ValueError("attachments are supported only in registered agent sessions")
    if session.record.state in {"exited", "crashed"}:
        raise ValueError("attachments cannot be added to an ended session")
    project = request.app[keys.PROJECTS].projects.get(session.record.project_id)
    if project is None:
        raise ValueError("the session's owning Project is unavailable")
    workspace = await asyncio.to_thread(
        attachment_workspace_root,
        project.root,
        session.record.spawn_cwd or session.record.cwd,
    )
    if not request.content_type.startswith("multipart/"):
        raise ValueError("attachment upload must use multipart form data")
    reader = await request.multipart()
    part = await reader.next()
    if not isinstance(part, BodyPartReader) or part.name != "file":
        raise ValueError("multipart field 'file' is required")
    media_type = str(part.headers.get("Content-Type", "")).split(";", 1)[0].lower()
    data = bytearray()
    max_bytes = MAX_IMAGE_BYTES if image_only else MAX_ATTACHMENT_BYTES
    while chunk := await part.read_chunk(size=64 * 1024):
        data.extend(chunk)
        if len(data) > max_bytes:
            limit = "10 MiB" if image_only else "25 MiB"
            raise ValueError(f"attachment exceeds the {limit} limit")
    if await reader.next() is not None:
        raise ValueError("exactly one multipart file is required")
    filename = part.filename or "attachment"
    lock_key = (str(workspace), session.record.id)
    lock = request.app[keys.ATTACHMENT_LOCKS].setdefault(lock_key, asyncio.Lock())
    async with lock:
        stored = await asyncio.to_thread(
            store_session_attachment,
            workspace,
            session.record.id,
            filename,
            media_type,
            data,
            image_only=image_only,
        )
    reference = adapter.media_reference(stored.path) if stored.kind == "image" else str(stored.path)
    await request.app[keys.EVENTS].emit(
        "session_media_uploaded" if image_only else "session_attachment_uploaded",
        session_id=session.record.id,
        attachment_kind=stored.kind,
        media_type=stored.media_type,
        bytes=stored.bytes,
    )
    return json_response(stored.payload(reference), 201)


async def upload_session_attachment(request: web.Request) -> web.Response:
    return await _upload_session_attachment(request, image_only=False)


async def upload_session_media(request: web.Request) -> web.Response:
    """Compatibility endpoint for older image-paste clients."""
    return await _upload_session_attachment(request, image_only=True)


async def promote_session(request: web.Request) -> web.Response:
    session = request.app[keys.SESSIONS].resolve(request.match_info["sid"])
    supplied = request.headers.get("X-Mux-Hook-Secret", "")
    if not secrets.compare_digest(supplied, session.hook_secret):
        raise web.HTTPForbidden(text="invalid hook secret")
    body = await request.json()
    promoted = await request.app[keys.SESSIONS].promote(
        session.record.id,
        str(body["backend"]),
        str(body["native_id"]),
        str(body["cwd"]) if body.get("cwd") else None,
    )
    return json_response(promoted.record.snapshot())


async def demote_session(request: web.Request) -> web.Response:
    session = request.app[keys.SESSIONS].resolve(request.match_info["sid"])
    supplied = request.headers.get("X-Mux-Hook-Secret", "")
    if not secrets.compare_digest(supplied, session.hook_secret):
        raise web.HTTPForbidden(text="invalid hook secret")
    body = await request.json()
    demoted = await request.app[keys.SESSIONS].demote(
        session.record.id, str(body["backend"]), str(body["native_id"])
    )
    return json_response(demoted.record.snapshot())


ROUTES: tuple[web.RouteDef, ...] = (
    web.get("/api/sessions", list_sessions),
    web.post("/api/sessions", spawn_session),
    web.get("/api/sessions/{sid}", get_session),
    web.patch("/api/sessions/{sid}", patch_session),
    web.post("/api/sessions/{sid}/read", mark_session_read),
    web.post("/api/sessions/{sid}/title/regenerate", regenerate_session_title),
    web.post(
        "/api/sessions/{sid}/standing-activity/clear", clear_session_standing_activity
    ),
    web.get("/api/sessions/{sid}/approvals", get_session_approvals),
    web.put("/api/sessions/{sid}/approvals", put_session_approvals),
    web.post("/api/sessions/{sid}/approvals/approve-once", approve_pending_request),
    web.delete("/api/sessions/{sid}", delete_session),
    web.post("/api/sessions/{sid}/relaunch", relaunch_session),
    web.post("/api/sessions/{sid}/attachments", upload_session_attachment),
    web.post("/api/sessions/{sid}/media", upload_session_media),
    web.post("/api/sessions/{sid}/promote", promote_session),
    web.post("/api/sessions/{sid}/demote", demote_session),
)
