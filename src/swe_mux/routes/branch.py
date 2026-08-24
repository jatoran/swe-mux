"""Branching a session from a point in its transcript."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from pathlib import Path
from typing import Any

from aiohttp import web

from .. import (
    app_keys as keys,
)
from .. import (
    session_titles,
)
from ..harness import (
    assigns_conversation_id,
    branch_strategy,
    has_observable_transcript,
)
from ..http_support import json_response
from ..layouts import attach_terminal
from ..session import (
    SessionManager,
)
from ..spawn_probe import SpawnFailure, spawn_settled
from ..transcript_fork import (
    ForkPlan,
    ForkRefused,
    ForkUnsupported,
    mint_conversation_id,
    write_fork,
)
from ..transcript_view import (
    CONVERSATION_DEFAULT_LIMIT,
    CONVERSATION_MAX_LIMIT,
    CutPoint,
    conversation_cut_points,
    conversation_is_readable,
    conversation_view_cached,
    resolve_cut_offset,
)

log = logging.getLogger(__name__)


def _branch_source_id(source: Any) -> str | None:
    """The canonical conversation id to fork from.

    Deliberately NOT ``record.native_session_id``: the transcript observer
    rewrites that field from whatever file it is following, and with sibling
    agents in one cwd it can latch onto a sibling's transcript (see the
    transcript-switch cross-attribution fix), so branching off it would fork the
    wrong conversation. ``agent_lifecycle_id`` is the lifecycle anchor the
    observer never overwrites.

    Where mux minted the conversation id, the mux id is itself a valid conversation
    id, so an unchanged native id is fine. Where the CLI mints its own, only a
    discovered id is meaningful and the mux id is a placeholder that would resume
    nothing.
    """
    record = source.record
    lifecycle = getattr(source, "agent_lifecycle_id", None)
    if assigns_conversation_id(record.backend):
        return str(lifecycle or record.native_session_id or record.id)
    candidate = str(lifecycle or record.native_session_id or "")
    return candidate if candidate and candidate != record.id else None


# How long a freshly spawned sibling must stay alive before it counts as up. The
# failure this measures killed a pane 1.3s after spawn, so the window has to clear it.
BRANCH_SIBLING_SETTLE_SECONDS = 3.0


# A `transcript_fork` sibling resumes a conversation nothing else has ever opened, so
# there is no release to race and nothing a second attempt would be further from. A
# `resume_child_thread` sibling reopens a conversation the source pane is still live
# on, which is exactly the race a retry helps.
BRANCH_SIBLING_ATTEMPTS = 3


BRANCH_SIBLING_RETRY_BACKOFF_SECONDS = 1.5


# Reading the conversation to list its branch points competes with nothing, but it
# parses a whole transcript and must not be able to hold a request open.
BRANCH_POINTS_TIMEOUT_SECONDS = 20.0


# Writing the fork copies the prefix and its sidecar files. Generous, because the cost
# is the conversation's size and a long one is the case worth being patient for.
BRANCH_WRITE_TIMEOUT_SECONDS = 60.0


# How much of the cut message is kept on the branch's lineage edge. Enough to
# recognise which turn it was, and short enough that a lineage row never becomes a
# second copy of a conversation in a table that is not a transcript store.
BRANCH_CUT_EXCERPT_CHARS = 200


def _branch_cut_excerpt(text: str) -> str:
    """One bounded line naming the message a branch was cut at."""
    flattened = " ".join(text.split())
    if len(flattened) <= BRANCH_CUT_EXCERPT_CHARS:
        return flattened
    return flattened[: BRANCH_CUT_EXCERPT_CHARS - 1] + "…"


def _branch_block_reason(session: Any) -> tuple[str, str] | None:
    """Why forking this pane's conversation *through the CLI* would damage it.

    Only `resume_child_thread` asks a live process to participate in the fork, so
    only it owes the "is this pane ready" question. Mid-turn, waiting on an approval,
    ended, or holding unsent composer text, the CLI is not in a state to be asked.

    A `transcript_fork` branch answers this question with the file system and is
    therefore not gated: nothing is typed, the source file is opened read-only, and a
    pane that is mid-turn or has already exited forks exactly as well as an idle one.

    Permissive where it cannot see: a record with no state is not evidence of a bad
    state, and this gate exists to catch the known-bad ones rather than to demand
    proof of health.
    """
    state = getattr(session.record, "state", "")
    if state in {"exited", "crashed"}:
        return "source_not_live", "the session has ended"
    if state == "working":
        return "source_busy", "the agent is mid-turn"
    if state == "awaiting":
        return "source_busy", "the agent is waiting on an approval"
    composer = getattr(session, "composer", None)
    if composer is not None and getattr(composer, "pending", False):
        return "source_composer_dirty", "the composer holds unsent text"
    return None


async def _spawn_branch_sibling(
    manager: Any, source_id: str, attempts: int, **spawn_kwargs: Any
) -> tuple[Any, int, SpawnFailure | None]:
    """Spawn the sibling pane and prove it survived before handing it back.

    Verification is the load-bearing part and applies to both strategies: a CLI that
    refuses the conversation it was given starts, prints one line, and exits *after*
    the response that announced success, so an unverified branch reaches the operator
    as a grey pane with no message. Whether to retry is the caller's, which is why
    ``attempts`` is a parameter rather than a constant.

    Returns ``(session, attempts_used, failure)``.
    """
    return await spawn_settled(
        manager,
        flow=f"branch sibling for {source_id}",
        settle_seconds=BRANCH_SIBLING_SETTLE_SECONDS,
        attempts=attempts,
        retry_backoff_seconds=BRANCH_SIBLING_RETRY_BACKOFF_SECONDS,
        **spawn_kwargs,
    )


# A branch ordinal already carried by a name, so branching a branch does not stack
# prefixes into `B1-B2-B1-…`. The subject is what the operator recognises; the ordinal
# is bookkeeping and only the newest one is worth the width.
_BRANCH_NAME_ORDINAL = re.compile(r"^B\d+-")


async def _branch_pane_name(app: web.Application, record: Any) -> str:
    """What to call the new pane: the source conversation's own subject, marked.

    Deliberately the source's **display** name rather than `record.name`. Those differ
    for exactly the sessions worth branching: a session nobody renamed shows its
    generated title while `record.name` is still the spawn default, so naming the
    branch after the raw field produced `claude-6vried branch` for a conversation the
    operator knows as "Update ABC". The two-name rule is `session_titles.py`'s, and
    asking it is what keeps this in step with every other surface.

    The ordinal counts the branches already cut from this conversation, so a second
    branch of one source is `B2-` rather than a duplicate of the first. It is a label,
    not an identity: branches at different depths of one tree can share a number, and
    the daemon never reads it back.

    The name is passed to `spawn`, which treats an explicit name as a rename
    (`auto_named=False`), so the titler cannot take it back the moment the branch says
    its first word.
    """
    store = app.get(keys.AUTOMATION_STORE)
    run_id = session_titles.record_run_id(record)
    titles = await session_titles.generated_titles(store, {run_id})
    display = session_titles.record_display_name(record, titles)
    subject = _BRANCH_NAME_ORDINAL.sub("", display).strip()
    ordinal = 1
    if store is not None:
        try:
            edges = await store.lineage(run_id)
        except Exception as exc:  # noqa: BLE001 - a name must not be able to fail a branch
            log.warning("branch could not count prior branches of %s: %s", run_id, exc)
            edges = []
        ordinal += sum(
            1
            for edge in edges
            if edge.get("relation") == "branch" and edge.get("parent_run_id") == run_id
        )
    return f"B{ordinal}-{subject}" if subject else f"B{ordinal}-branch"


def _branch_point_payload(
    point: CutPoint, previous: CutPoint | None, text: str
) -> dict[str, Any]:
    """One offered branch point, with both cuts and why each is or is not available.

    Two cuts per message rather than one, because the two things an operator wants
    from a conversation they are re-reading are opposite: *after* an agent's reply
    continues from a point it had reached, while *before* their own message replays
    the moment they were about to send it, so it can be sent differently. Neither is
    the other with an off-by-one; a message has records on both sides of it.

    ``default_mode`` is which of the two that message is normally branched at, and it
    follows the role: a user message is a thing to redo, an agent message is a thing
    to continue from.

    ``before`` is answered from the *preceding* message, because that is the record
    the cut actually lands on. Stated here exactly as `resolve_cut_offset` decides it:
    a listing that offered a cut the request then refused would be a picker whose rows
    lie.
    """
    return {
        "message_id": point.message_id,
        "ordinal": point.ordinal,
        "role": point.role,
        "ts": point.ts,
        "text": text,
        "default_mode": "before" if point.role == "user" else "after",
        "modes": {
            "after": _branch_mode_state(point),
            "before": _branch_mode_state(previous),
        },
    }


def _branch_mode_state(cut_at: CutPoint | None) -> dict[str, Any]:
    """Whether a cut landing on ``cut_at``'s end offset is available, and why not."""
    if cut_at is None:
        return {"eligible": False, "reason": "outside_window"}
    if cut_at.open_tool_calls:
        return {"eligible": False, "reason": "unanswered_tool_calls"}
    return {"eligible": True, "reason": None}


def _branch_source(request: web.Request) -> tuple[Any, str, Any] | web.Response:
    """The pane, its conversation id and its Project, or the refusal that replaces them.

    Shared by the branch-point listing and the branch itself so the two cannot
    disagree about which conversation a pane means — the listing offering points from
    one conversation while the fork cut another is the exact class of bug the
    ``agent_lifecycle_id`` anchor already exists to prevent.
    """
    manager: SessionManager = request.app[keys.SESSIONS]
    session = manager.resolve(request.match_info["sid"])
    record = session.record
    if not has_observable_transcript(record.backend):
        return json_response(
            {"error": "only observable agent sessions can branch", "code": "not_agent"}, 422
        )
    conversation = _branch_source_id(session)
    if not conversation:
        return json_response(
            {"error": "no conversation id to branch from yet", "code": "native_id_missing"}, 409
        )
    project = request.app[keys.PROJECTS].projects.get(record.project_id)
    if project is None:
        return json_response({"error": "project missing", "code": "project_missing"}, 422)
    return session, conversation, project


async def _read_branch_points(
    path: Path | None, backend: str, native_id: str | None, limit: int
) -> tuple[list[CutPoint], dict[str, str], bool] | str:
    """``(points, text_by_id, truncated)`` for this conversation, or a refusal code.

    The reader answers two questions in one parse: where the cuts are, and what the
    operator would recognise each one by. Both come from the same window, so a point
    can never be offered with somebody else's words beside it.

    Takes a conversation rather than a session, because the two pickers that need it
    do not both have a pane: the rail's Branch button has one, and choosing the fixed
    point a *schedule* will fork every night is done from a History row whose session
    ended weeks ago.
    """
    if not conversation_is_readable(path, backend, native_id):
        return "no_transcript"
    try:
        points = await asyncio.wait_for(
            asyncio.to_thread(
                conversation_cut_points,
                path,
                backend,
                limit=limit,
                native_id=native_id,
            ),
            timeout=BRANCH_POINTS_TIMEOUT_SECONDS,
        )
        view = await asyncio.wait_for(
            asyncio.to_thread(
                conversation_view_cached,
                path,
                backend,
                limit=limit,
                native_id=native_id,
            ),
            timeout=BRANCH_POINTS_TIMEOUT_SECONDS,
        )
    except (OSError, TimeoutError):
        return "unreadable"
    if points is None:
        return "dialect_unsupported"
    text_by_id = {
        str(message["message_id"]): str(message.get("text") or "")
        for message in view.get("messages", [])
    }
    return points, text_by_id, bool(view.get("truncated"))


async def session_branch_points(request: web.Request) -> web.Response:
    """Where this session's conversation can be forked, and what each point says.

    A listing rather than a capability flag because eligibility is per point, not per
    harness: the same conversation offers a legal cut after one reply and an illegal
    one after the next, purely because the second asked for a tool whose result had
    not yet arrived. A picker that cannot say which is which would offer a branch
    that writes a conversation the provider rejects.

    Every "nothing to offer" case answers 200 with a ``reason``. Opening the picker
    on a pane that has not spoken yet is an ordinary thing to do, not an error.
    """
    resolved = _branch_source(request)
    if isinstance(resolved, web.Response):
        return resolved
    session, conversation, _project = resolved
    record = session.record
    strategy = branch_strategy(record.backend)
    try:
        limit = int(request.query.get("limit") or CONVERSATION_DEFAULT_LIMIT)
    except ValueError:
        raise web.HTTPBadRequest(text="limit must be an integer") from None
    limit = max(1, min(limit, CONVERSATION_MAX_LIMIT))
    empty: dict[str, Any] = {
        "session_id": record.id,
        "backend": record.backend,
        "conversation_id": conversation,
        "strategy": strategy,
        "from_message": strategy == "transcript_fork",
        "points": [],
        "truncated": False,
        "reason": None,
    }
    if strategy != "transcript_fork":
        return json_response({**empty, "reason": "strategy_has_no_points"})
    outcome = await _read_branch_points(
        session.transcript_path, record.backend, record.native_session_id, limit
    )
    if isinstance(outcome, str):
        return json_response({**empty, "reason": outcome})
    points, text_by_id, truncated = outcome
    # Only the oldest point in the window has no preceding record to cut after, and
    # only there is "branch before this" unavailable. Whether the window itself is
    # truncated is beside the point: a fork always carries the conversation from its
    # first byte, so a bounded read costs which cuts can be *named*, never what one
    # contains.
    payload = [
        _branch_point_payload(
            point,
            points[index - 1] if index else None,
            text_by_id.get(point.message_id, ""),
        )
        for index, point in enumerate(points)
    ]
    return json_response({**empty, "points": payload, "truncated": truncated})


async def history_branch_points(request: web.Request) -> web.Response:
    """Where an *ended* conversation can be forked, for a schedule to pin a point in.

    The same listing `session_branch_points` produces, read from a History row instead
    of a live pane. It exists because the fixed point a nightly fork-and-resume cuts at
    is chosen from History - the pane that held that conversation is usually long gone,
    and requiring one would restrict the choice to conversations that happen to be open.

    Every "nothing to offer" case answers 200 with a ``reason``, for the same reason the
    session listing does: asking a conversation with nothing to fork is ordinary.
    """
    row = await request.app[keys.HISTORY].history_entry(request.match_info["sid"])
    if not row:
        raise KeyError(request.match_info["sid"])
    backend = str(row.get("backend") or "")
    try:
        limit = int(request.query.get("limit") or CONVERSATION_DEFAULT_LIMIT)
    except ValueError:
        raise web.HTTPBadRequest(text="limit must be an integer") from None
    limit = max(1, min(limit, CONVERSATION_MAX_LIMIT))
    strategy = branch_strategy(backend)
    empty: dict[str, Any] = {
        "history_id": str(row["id"]),
        "backend": backend,
        "conversation_id": str(row.get("native_id") or ""),
        "strategy": strategy,
        "from_message": strategy == "transcript_fork",
        "points": [],
        "truncated": False,
        "reason": None,
    }
    if not row.get("agent_visible") or not has_observable_transcript(backend):
        return json_response({**empty, "reason": "not_agent"})
    if strategy != "transcript_fork":
        return json_response({**empty, "reason": "strategy_has_no_points"})
    outcome = await _read_branch_points(
        Path(str(row["transcript_path"])) if row.get("transcript_path") else None,
        backend,
        str(row.get("native_id") or ""),
        limit,
    )
    if isinstance(outcome, str):
        return json_response({**empty, "reason": outcome})
    points, text_by_id, truncated = outcome
    payload = [
        _branch_point_payload(
            point,
            points[index - 1] if index else None,
            text_by_id.get(point.message_id, ""),
        )
        for index, point in enumerate(points)
    ]
    return json_response({**empty, "points": payload, "truncated": truncated})


async def _branch_by_transcript_fork(
    request: web.Request, session: Any, conversation: str, body: dict[str, Any]
) -> tuple[dict[str, Any], str | None] | web.Response:
    """Write the forked conversation, or the refusal explaining why there is none.

    Returns ``(fork_details, seed_text)``. ``seed_text`` is the message the cut
    excluded, and only for a ``before`` cut on something the operator typed: the
    point of replaying that moment is to send it differently, and handing the words
    back is what makes editing them the obvious next act rather than retyping from
    memory.
    """
    record = session.record
    manager: SessionManager = request.app[keys.SESSIONS]
    adapter = manager.adapters.get(record.backend)
    if adapter is None:
        return json_response(
            {"error": f"no adapter for {record.backend}", "code": "adapter_missing"}, 422
        )
    outcome = await _read_branch_points(
        session.transcript_path,
        record.backend,
        record.native_session_id,
        CONVERSATION_MAX_LIMIT,
    )
    if isinstance(outcome, str):
        return json_response(
            {"error": f"the conversation cannot be forked: {outcome}", "code": outcome}, 409
        )
    points, text_by_id, _truncated = outcome
    if not points:
        return json_response(
            {"error": "the conversation has no messages to branch from", "code": "no_messages"},
            409,
        )
    message_id = str(body.get("from_message_id") or "") or points[-1].message_id
    chosen = next((point for point in points if point.message_id == message_id), None)
    mode = str(body.get("mode") or "") or (
        "before" if chosen is not None and chosen.role == "user" else "after"
    )
    if mode not in {"before", "after"}:
        return json_response(
            {"error": "mode must be 'before' or 'after'", "code": "bad_mode"}, 422
        )
    cut, detail = resolve_cut_offset(points, message_id, mode)
    if cut is None:
        assert isinstance(detail, str)
        log.info(
            "branch refused for %s at %s/%s: %s", record.id, message_id, mode, detail
        )
        return json_response(
            {"error": f"that point cannot be branched from: {detail}", "code": detail}, 409
        )
    assert isinstance(detail, CutPoint)
    branch_cwd = record.run_cwd or record.cwd
    fork_id = mint_conversation_id(record.backend)
    # Derived from the cwd the new pane will run in, not from where the source file
    # happens to sit. Claude resolves a conversation from its working directory, so a
    # fork written beside a relocated source file is a fork the CLI cannot open.
    target_path = adapter.transcript_path(fork_id, Path(branch_cwd))
    source_path = session.transcript_path
    if target_path is None or source_path is None:
        return json_response(
            {
                "error": f"{record.backend} does not keep this conversation in a file "
                "mux can fork",
                "code": "no_transcript",
            },
            409,
        )
    plan = ForkPlan(
        backend=record.backend,
        source_path=source_path,
        source_conversation_id=conversation,
        fork_conversation_id=fork_id,
        target_path=target_path,
        cut_offset=cut,
        title_marker=f"[branch {fork_id[:8]}]",
    )
    log.info(
        "branch fork writing session=%s conversation=%s fork=%s at %s/%s cut=%d cwd=%s",
        record.id,
        conversation,
        fork_id,
        message_id,
        mode,
        cut,
        branch_cwd,
    )
    try:
        written = await asyncio.wait_for(
            asyncio.to_thread(write_fork, plan), timeout=BRANCH_WRITE_TIMEOUT_SECONDS
        )
    except ForkRefused as exc:
        log.warning("branch fork of %s refused: %s (%s)", conversation, exc, exc.code)
        return json_response({"error": str(exc), "code": exc.code}, 409)
    except ForkUnsupported as exc:
        return json_response({"error": str(exc), "code": "branch_unsupported"}, 422)
    except (OSError, TimeoutError) as exc:
        log.error("branch fork of %s failed to write: %s", conversation, exc)
        return json_response(
            {"error": f"the fork could not be written: {exc}", "code": "fork_write_failed"}, 500
        )
    seed = text_by_id.get(message_id) if mode == "before" and detail.role == "user" else None
    return {
        "conversation_id": written.conversation_id,
        "path": str(written.path),
        "cut_offset": cut,
        "from_message_id": message_id,
        "from_message_role": detail.role,
        # Kept with the branch rather than resolved from `cut_offset` on demand. The
        # only reader is a human asking "where did this come from", weeks later, by
        # which time the parent transcript may have been compacted, relocated by a cwd
        # change, or deleted outright - and re-reading a whole conversation to render
        # one line is the wrong shape even when it is still there.
        "from_message_text": _branch_cut_excerpt(text_by_id.get(message_id, "")),
        "mode": mode,
        "records_written": written.records_written,
        "records_dropped": written.records_dropped,
        "attachments_copied": written.attachments_copied,
        "bytes_written": written.bytes_written,
    }, seed


async def branch_session(request: web.Request) -> web.Response:
    """Fork a conversation into a new pane, leaving the source pane exactly as it was.

    Two strategies, and they differ in who does the forking.

    ``transcript_fork`` (Claude) is mux's own, and it is the one that can branch from
    a *point*. The daemon reads the source conversation, writes a **new** conversation
    file holding its records up to the chosen message, and resumes that in a sibling
    pane. Nothing is typed into anybody's terminal, the source file is opened
    read-only, and the source pane keeps its conversation, its identity and its run
    untouched — so there is no fork to wait for, no release to race, and no reason to
    refuse a pane that is mid-turn or has already exited. The new conversation is
    genuinely new and gets its own history row; the fork is recorded as a ``branch``
    lineage edge rather than inferred from a shared transcript.

    ``resume_child_thread`` (Codex) asks the CLI: ``codex resume`` opens a child
    thread with its own rollout, diverging from the still-live original. That is only
    ever a fork from now, and because it reopens a conversation a live process is
    still on, it keeps the readiness gate and the spawn retry that a CLI-mediated
    fork needs.

    Both prove the sibling survived before handing it back (`spawn_probe.py`). A CLI
    that refuses the conversation it was given exits *after* the response announcing
    success, so an unverified branch reaches the operator as a grey pane and no
    message.
    """
    resolved = _branch_source(request)
    if isinstance(resolved, web.Response):
        return resolved
    session, conversation, project = resolved
    manager: SessionManager = request.app[keys.SESSIONS]
    record = session.record
    strategy = branch_strategy(record.backend)
    if strategy is None:
        # Every remaining harness's resume appends to the *same* session file, so
        # two live processes would interleave writes into one conversation. Refused
        # outright rather than corrupted politely.
        return json_response(
            {
                "error": f"branching is not implemented for {record.backend} sessions",
                "code": "branch_unsupported",
            },
            422,
        )
    body = await request.json() if request.can_read_body else {}
    if strategy != "transcript_fork" and body.get("from_message_id"):
        return json_response(
            {
                "error": f"{record.backend} can only branch from where the conversation "
                "stands now",
                "code": "branch_point_unsupported",
            },
            422,
        )
    if strategy == "resume_child_thread" and (blocked := _branch_block_reason(session)):
        code, why = blocked
        log.info("branch refused for %s: %s (%s)", record.id, why, code)
        return json_response({"error": f"cannot branch while {why}", "code": code}, 409)
    branch_cwd = record.run_cwd or record.cwd
    started_at = time.monotonic()
    events = request.app.get(keys.EVENTS)
    fork: dict[str, Any] | None = None
    seed_text: str | None = None
    if strategy == "transcript_fork":
        outcome = await _branch_by_transcript_fork(request, session, conversation, body)
        if isinstance(outcome, web.Response):
            return outcome
        fork, seed_text = outcome
        resume_id = fork["conversation_id"]
        attempts_allowed = 1
    else:
        resume_id = conversation
        attempts_allowed = BRANCH_SIBLING_ATTEMPTS
        log.info(
            "branch started session=%s backend=%s conversation=%s cwd=%s strategy=%s",
            record.id,
            record.backend,
            conversation,
            branch_cwd,
            strategy,
        )
    session_new, attempts, failure = await _spawn_branch_sibling(
        manager,
        record.id,
        attempts_allowed,
        backend=record.backend,
        name=body.get("name") or await _branch_pane_name(request.app, record),
        cwd=branch_cwd,
        project_id=record.project_id,
        resume_native_id=resume_id,
        project_label=project.name,
    )
    if session_new is None:
        detail = failure.describe() if failure is not None else "no failure recorded"
        log.error(
            "branch of %s could not open the fork after %d attempts: %s",
            conversation,
            attempts,
            detail,
        )
        return json_response(
            {
                "error": f"the branch was created but would not open ({detail}); "
                "reopen it from History",
                "code": "branch_sibling_failed",
                "attempts": attempts,
                "conversation_id": resume_id,
            },
            503,
        )
    await _record_branch_lineage(request, record, session_new, strategy, conversation, fork)
    if events is not None:
        events.emit_background(
            "session_branched",
            session_id=record.id,
            backend=record.backend,
            strategy=strategy,
            original=conversation,
            branch_id=resume_id,
            sibling_id=session_new.record.id,
            from_message_id=(fork or {}).get("from_message_id"),
            mode=(fork or {}).get("mode"),
            records_written=(fork or {}).get("records_written"),
            attempts=attempts,
            duration_ms=round((time.monotonic() - started_at) * 1000, 1),
        )
    log.info(
        "branch completed session=%s original=%s branch=%s sibling=%s attempts=%d in %.1fs",
        record.id,
        conversation,
        resume_id,
        session_new.record.id,
        attempts,
        time.monotonic() - started_at,
    )
    next_layout = attach_terminal(
        project.layout,
        session_new.record.id,
        target_id=body.get("target_session_id") or record.id,
        direction=body.get("direction") or "after",
    )
    try:
        await request.app[keys.PROJECTS].update(
            record.project_id, layout=next_layout, layout_revision=project.layout_revision
        )
    except Exception:
        await manager.stop(session_new.record.id)
        manager.sessions.pop(session_new.record.id, None)
        raise
    return json_response(
        {
            "session": session_new.record.snapshot(),
            "source": record.id,
            "strategy": strategy,
            "fork": fork,
            "seed_text": seed_text,
        },
        201,
    )


async def _record_branch_lineage(
    request: web.Request,
    record: Any,
    branched: Any,
    strategy: str,
    conversation: str,
    fork: dict[str, Any] | None,
) -> None:
    """Record the branch edge, so the fork point survives the request that made it.

    Written after the pane is proved up rather than before it: an edge to a
    conversation that never opened would describe a branch nobody can visit. Failure
    to record it is logged and swallowed — the branch itself is done, and losing the
    edge degrades the tree view rather than the conversation.
    """
    store = request.app.get(keys.AUTOMATION_STORE)
    if store is None:
        return
    parent = record.agent_run_id or record.id
    child = branched.record.agent_run_id or branched.record.id
    if parent == child:
        return
    try:
        await store.add_lineage(
            parent,
            child,
            "branch",
            {
                "backend": record.backend,
                "strategy": strategy,
                "source_conversation_id": conversation,
                "branch_conversation_id": branched.record.native_session_id,
                "from_message_id": (fork or {}).get("from_message_id"),
                "from_message_role": (fork or {}).get("from_message_role"),
                "from_message_text": (fork or {}).get("from_message_text"),
                "mode": (fork or {}).get("mode"),
                "cut_offset": (fork or {}).get("cut_offset"),
            },
        )
    except Exception as exc:  # noqa: BLE001 - the branch succeeded; the edge is bookkeeping
        log.warning("branch of %s could not record its lineage edge: %s", conversation, exc)


ROUTES: tuple[web.RouteDef, ...] = (
    web.get("/api/sessions/{sid}/branch-points", session_branch_points),
    web.post("/api/sessions/{sid}/branch", branch_session),
    web.get("/api/history/{sid}/branch-points", history_branch_points),
)
