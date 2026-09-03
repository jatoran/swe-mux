"""Reopening a conversation in a new pane, and everything that can refuse it.

This module is the **single resume authority**. Both callers go through it:

- `server.resume_history`, when a human presses Resume on a History row.
- `scheduler.py`, when a schedule whose action is ``resume`` fires.

That is deliberate and load-bearing, for the same reason `scheduler.py` spawns
through `_spawn_from_body` rather than reimplementing a spawn: a resume has five
refusal conditions, three of them invisible without a live probe, and a second
implementation would drift from this one silently. The drift would surface as an
unattended 3 a.m. schedule opening a grey pane with no message - the exact failure
`spawn_settled` exists to prevent.

What a resume owes, in order:

1. **The row is resumable at all.** An agent row with an observable transcript, a
   conversation id, a working directory that still exists, a transcript that still
   exists, a registered adapter, and a target Project.
2. **Nobody else holds the conversation.** A CLI opens a conversation once and
   answers a second opener by exiting, so this is checked twice: against mux's own
   live panes, and against `conversation_holder`, which sees processes mux does not
   own (a Claude background agent outlives the pane that parked it).
3. **Whether the resumed pane inherits the run row.** Only the adapter can say:
   Claude resolves a conversation from its working directory, Codex by thread id.
4. **The pane came up.** Spawned and then *proved*, because a CLI that refuses the
   conversation it was handed exits about a second after the caller was told it
   started.

The fork-and-resume path (`fork_run`) is the other half. It writes a **new**
conversation holding a prefix of an existing one and hands back its id, so a
schedule can resume from a fixed point over and over without the runs contaminating
each other. It reuses `transcript_view.resolve_cut_offset`, the same decision the
interactive branch picker makes, because a schedule that fired on a rule the picker
would have refused is an unattended session opened on a conversation the provider
rejects.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .harness import has_observable_transcript
from .spawn_probe import spawn_settled
from .transcript_fork import (
    ForkPlan,
    ForkRefused,
    ForkUnsupported,
    mint_conversation_id,
    write_fork,
)
from .transcript_repair import resolve_row_transcript
from .transcript_view import (
    CONVERSATION_MAX_LIMIT,
    conversation_cut_points,
    conversation_is_readable,
    resolve_cut_offset,
)

log = logging.getLogger(__name__)

# How long a resumed pane must stay alive before it counts as up. Measured against
# the failure this exists to catch: a Claude that refuses a held conversation prints
# one line and exits about 1.5 s after spawn.
RESUME_SETTLE_SECONDS = 2.5
# Two attempts, because a resume issued the moment the previous pane closed can lose
# one race with the exiting process, and one retry is the whole cost of surviving it.
RESUME_ATTEMPTS = 2
RESUME_RETRY_BACKOFF_SECONDS = 1.0
# A fork resumes a conversation nothing else has ever opened, so there is no release
# to race and nothing a second attempt would be closer to.
FORK_RESUME_ATTEMPTS = 1

# Reading a whole conversation to find its cut points must not be able to hold a
# request - or a scheduler tick - open.
FORK_POINTS_TIMEOUT_SECONDS = 20.0
FORK_WRITE_TIMEOUT_SECONDS = 60.0

# How far the "wherever this conversation has got to" resolver will walk. Each hop is
# a real continuation (a rollover within one pane, or a resume into a new one), so the
# bound is a runaway guard rather than a policy: a conversation resumed fifty times has
# other problems.
LATEST_RUN_MAX_HOPS = 64


class ResumeRefused(Exception):
    """A resume that will not be attempted, with the reason the operator sees.

    Carries an HTTP status because the human-facing route answers with one, and a
    machine ``code`` because both the browser and the schedule run-history row branch
    on it rather than on the prose.
    """

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status: int = 422,
        detail: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status = status
        self.detail = detail or {}

    def payload(self) -> dict[str, Any]:
        return {"error": str(self), "code": self.code, **self.detail}


@dataclass(frozen=True, slots=True)
class ResumeOutcome:
    """A resumed pane, and what it means for the conversation's history row."""

    session: Any
    #: The run row the pane inherited, or None when the resume opened a new run.
    adopted_run_id: str | None
    #: Attempts the spawn needed. Reported so a retry is visible rather than silent.
    attempts: int
    #: The conversation the pane was actually opened on. Differs from the source row's
    #: ``native_id`` for a fork, which is a genuinely new conversation.
    conversation_id: str
    #: The fork writer's report, when this resume opened a fork rather than a row.
    fork: dict[str, Any] | None = field(default=None)


def resumable_refusal(
    row: dict[str, Any],
    *,
    projects: Any,
    adapters: Any,
    target_project_id: str,
) -> ResumeRefused | None:
    """The first structural reason this row cannot be resumed, or None.

    Structural means "answerable from the row and the registry" - no live probe, no
    file read beyond an existence test. Ordered so the most fundamental answer wins:
    a row with no conversation id is not a transcript problem.
    """
    if not row.get("agent_visible") or not has_observable_transcript(row.get("backend")):
        return ResumeRefused(
            "not_agent", "only observable agent history can be resumed", status=422
        )
    checks: tuple[tuple[str, bool, str], ...] = (
        (
            "native_id_missing",
            not row.get("native_id"),
            "this run never reported a conversation id",
        ),
        (
            "cwd_missing",
            not row.get("cwd") or not Path(str(row["cwd"])).is_dir(),
            "the working directory this conversation ran in no longer exists",
        ),
        (
            "transcript_unavailable",
            not conversation_is_readable(
                Path(str(row["transcript_path"])) if row.get("transcript_path") else None,
                str(row.get("backend") or ""),
                str(row.get("native_id") or ""),
            ),
            # Far and away the most likely refusal for a schedule armed days ahead:
            # the CLIs prune their own transcripts on their own timers, and mux is
            # not consulted.
            "the conversation's transcript is gone; the agent CLI prunes its own "
            "history and mux does not own that file",
        ),
        (
            "target_project_missing",
            target_project_id not in projects.projects,
            "the Project this conversation would open in is no longer registered",
        ),
        (
            "adapter_missing",
            row.get("backend") not in adapters,
            f"no adapter for {row.get('backend')!r}",
        ),
    )
    for code, failed, message in checks:
        if failed:
            return ResumeRefused(
                code, message, status=409 if code == "transcript_unavailable" else 422
            )
    return None


def claim_refusal(row: dict[str, Any], *, sessions: Any) -> ResumeRefused | None:
    """Whether something already holds this conversation, mux-owned or not.

    Two checks rather than one, and neither subsumes the other. The first is mux's
    own panes: two sessions tracking one conversation is exactly the linked
    status/token cross-attribution the identity invariant forbids. The second sees
    processes mux never started - a conversation parked into a Claude background
    agent stays checked out for as long as that agent lives, and resuming it produces
    a pane that prints its refusal and exits a second later.

    Refused rather than forked. Forking silently produces a *second* conversation
    where the caller asked to return to one.
    """
    backend = str(row.get("backend") or "")
    native_id = str(row.get("native_id") or "")
    live_owner = next(
        (
            other
            for other in sessions.sessions.values()
            if other.record.backend == backend
            and other.record.state not in {"exited", "crashed"}
            and other.record.native_session_id == native_id
        ),
        None,
    )
    if live_owner is not None:
        return ResumeRefused(
            "conversation_live",
            f"conversation is live in session {live_owner.record.name}",
            status=409,
            detail={"session_id": live_owner.record.id},
        )
    holder = sessions.conversation_holder(backend, native_id)
    if holder is not None:
        log.info(
            "resume of run %s refused: %s held by pid %s (kind %s, job %s)",
            row.get("id"),
            native_id,
            holder.pid,
            holder.kind or "unknown",
            holder.job_id or "-",
        )
        return ResumeRefused(
            "conversation_held",
            holder.describe(),
            status=409,
            detail={
                "holder": {
                    "kind": holder.kind,
                    "pid": holder.pid,
                    "job_id": holder.job_id,
                    "name": holder.name,
                }
            },
        )
    return None


def inherited_name(row: dict[str, Any]) -> str:
    """The name a resumed pane keeps.

    The conversation keeps its own name: a suffix here compounded on every resume
    ("… resumed resumed") and, for Claude, retitled an entry the resumed pane now
    shares rather than replaces.

    ``auto_named`` arrives from SQLite as 0/1, so an ``is not False`` test never
    matched and a conversation the user had renamed came back under its *generated*
    title instead of the name they pinned.
    """
    if bool(row.get("auto_named")) and row.get("generated_title"):
        return str(row["generated_title"])
    return str(row.get("name") or "")


async def resume_run(
    row: dict[str, Any],
    *,
    sessions: Any,
    projects: Any,
    target_project_id: str,
    name: str = "",
    flow: str = "",
    conversation_id: str = "",
    fork: dict[str, Any] | None = None,
) -> ResumeOutcome:
    """Reopen ``row``'s conversation in a new pane, or raise `ResumeRefused`.

    ``conversation_id`` overrides which conversation is opened, which is how a fork
    is resumed: the guards above still run against the *source* row (its cwd, its
    adapter, its Project), but the pane opens the fork, and a fork is a conversation
    nothing has ever held - so the claim checks are skipped and the resumed pane
    earns its own run row rather than inheriting the source's.

    Layout attachment and lineage stay with the caller. They are what the two callers
    genuinely differ on: the route attaches beside the pane the operator was looking
    at, and a schedule has no such pane.
    """
    # Before the structural guards, not after: `transcript_unavailable` is the refusal a
    # moved file produces, and it is the one refusal here that can be wrong. The CLI
    # re-homes a conversation when a session enters or leaves a worktree, so the row can
    # name a path that stopped existing while the conversation itself is intact one slug
    # away. Resolving first means the guards judge where the conversation *is*
    # (`transcript_repair`), and the repair reaches the scheduler's resume too, because
    # both callers come through here.
    await resolve_row_transcript(
        row,
        adapters=sessions.adapters,
        history=getattr(sessions, "history", None),
        events=getattr(sessions, "events", None),
    )
    refusal = resumable_refusal(
        row, projects=projects, adapters=sessions.adapters, target_project_id=target_project_id
    )
    if refusal is not None:
        raise refusal
    forked = bool(conversation_id) and conversation_id != str(row["native_id"])
    if not forked and (claimed := claim_refusal(row, sessions=sessions)) is not None:
        raise claimed
    project = projects.projects[target_project_id]
    adapter = sessions.adapters[str(row["backend"])]
    # A resume that reopens the same conversation, in the same file, under the same id
    # continues an agent run that already has a row: the pane inherits it rather than
    # opening a second entry over one file. Only the adapter can say whether this
    # resume is that, because the answer is the CLI's own transcript-resolution rule.
    # A fork never inherits: it is a new conversation in a new file.
    adopts = (
        False
        if forked
        else bool(adapter.resume_continues_conversation(str(row["cwd"]), str(project.root)))
    )
    requested = name.strip()
    session, attempts, failure = await spawn_settled(
        sessions,
        alive=_conversation_taken(sessions, row, conversation_id or str(row["native_id"])),
        flow=flow or f"resume of run {row['id']}",
        settle_seconds=RESUME_SETTLE_SECONDS,
        attempts=FORK_RESUME_ATTEMPTS if forked else RESUME_ATTEMPTS,
        retry_backoff_seconds=RESUME_RETRY_BACKOFF_SECONDS,
        backend=str(row["backend"]),
        name=requested or inherited_name(row),
        cwd=str(project.root),
        project_id=target_project_id,
        resume_native_id=conversation_id or str(row["native_id"]),
        adopt_run_id=str(row["id"]) if adopts else None,
        auto_named=None if requested else bool(row.get("auto_named")),
        project_label=project.name,
    )
    if session is None:
        detail = failure.describe() if failure is not None else "no failure recorded"
        log.error(
            "resume of run %s (conversation %s) died on spawn after %d attempts: %s",
            row["id"],
            conversation_id or row["native_id"],
            attempts,
            detail,
        )
        raise ResumeRefused(
            "resume_failed",
            f"the resumed session exited immediately: {detail}",
            status=503,
            detail={"attempts": attempts, "detail": failure.text if failure is not None else ""},
        )
    return ResumeOutcome(
        session=session,
        adopted_run_id=str(row["id"]) if adopts else None,
        attempts=attempts,
        conversation_id=conversation_id or str(row["native_id"]),
        fork=fork,
    )


def _conversation_taken(sessions: Any, row: dict[str, Any], conversation_id: str) -> Any:
    """The liveness predicate: *we* now publish the conversation under our own pid.

    The same file that proves somebody else holds a conversation proves we do once
    the resume lands, which ends the settle window as soon as the answer is known
    instead of paying it in full on every success. Matched on pid: a state file
    naming the conversation while another process owns it is the failure this whole
    path exists to catch, not evidence of it working.

    A fork has no such published claim to wait for on every harness, but the check is
    the same shape and simply settles the full window when the CLI publishes nothing.
    """

    def taken(pane: Any) -> bool:
        holder = sessions.conversation_holder(str(row["backend"]), conversation_id)
        return holder is not None and holder.pid == getattr(pane.record, "pid", 0)

    return taken


# ------------------------------------------------------------------ fork-and-resume


async def fork_run(
    row: dict[str, Any],
    *,
    sessions: Any,
    projects: Any,
    target_project_id: str,
    message_id: str,
    mode: str,
) -> dict[str, Any]:
    """Write a fork of ``row``'s conversation cut at ``message_id``, and describe it.

    The source is opened read-only and is never touched, which is what makes this
    repeatable: a schedule can fork the same pinned point every night and each run
    starts from identical context instead of inheriting the previous run's.

    Raises `ResumeRefused` for every reason the fork cannot be written, including the
    cut point having vanished from the conversation - which is a real outcome for a
    pinned point, since the picker only ever names the newest window of messages.
    """
    if mode not in {"before", "after"}:
        raise ResumeRefused("bad_mode", "mode must be 'before' or 'after'", status=422)
    project = projects.projects.get(target_project_id)
    if project is None:
        raise ResumeRefused(
            "target_project_missing",
            "the Project this fork would open in is no longer registered",
            status=422,
        )
    adapter = sessions.adapters.get(str(row.get("backend") or ""))
    if adapter is None:
        raise ResumeRefused("adapter_missing", f"no adapter for {row.get('backend')!r}", status=422)
    # A fork reads the source file directly, so it fails on a moved conversation exactly
    # the way an open does, and for the same wrong reason (`transcript_repair`).
    await resolve_row_transcript(
        row,
        adapters=sessions.adapters,
        history=getattr(sessions, "history", None),
        events=getattr(sessions, "events", None),
    )
    source_path = Path(str(row["transcript_path"])) if row.get("transcript_path") else None
    if source_path is None or not source_path.is_file():
        raise ResumeRefused(
            "transcript_unavailable",
            "the conversation this fork is cut from is gone",
            status=409,
        )
    try:
        points = await asyncio.wait_for(
            asyncio.to_thread(
                conversation_cut_points,
                source_path,
                str(row["backend"]),
                limit=CONVERSATION_MAX_LIMIT,
                native_id=str(row.get("native_id") or ""),
            ),
            timeout=FORK_POINTS_TIMEOUT_SECONDS,
        )
    except (OSError, TimeoutError) as exc:
        raise ResumeRefused(
            "unreadable", f"the conversation could not be read: {exc}", status=409
        ) from exc
    if points is None:
        raise ResumeRefused(
            "dialect_unsupported",
            f"mux cannot fork a {row['backend']} conversation at a chosen point",
            status=422,
        )
    cut, detail = resolve_cut_offset(points, message_id, mode)
    if cut is None:
        assert isinstance(detail, str)
        raise ResumeRefused(detail, f"that point cannot be branched from: {detail}", status=409)
    fork_id = mint_conversation_id(str(row["backend"]))
    # Derived from the cwd the new pane will run in, not from where the source file
    # happens to sit: Claude resolves a conversation from its working directory, so a
    # fork written beside a relocated source file is a fork the CLI cannot open.
    target_path = adapter.transcript_path(fork_id, Path(str(project.root)))
    if target_path is None:
        raise ResumeRefused(
            "no_transcript",
            f"{row['backend']} does not keep this conversation in a file mux can fork",
            status=409,
        )
    plan = ForkPlan(
        backend=str(row["backend"]),
        source_path=source_path,
        source_conversation_id=str(row["native_id"]),
        fork_conversation_id=fork_id,
        target_path=target_path,
        cut_offset=cut,
        title_marker=f"[branch {fork_id[:8]}]",
    )
    try:
        written = await asyncio.wait_for(
            asyncio.to_thread(write_fork, plan), timeout=FORK_WRITE_TIMEOUT_SECONDS
        )
    except ForkRefused as exc:
        raise ResumeRefused(exc.code, str(exc), status=409) from exc
    except ForkUnsupported as exc:
        raise ResumeRefused("branch_unsupported", str(exc), status=422) from exc
    except (OSError, TimeoutError) as exc:
        raise ResumeRefused(
            "fork_write_failed", f"the fork could not be written: {exc}", status=500
        ) from exc
    assert not isinstance(detail, str)
    log.info(
        "scheduled fork of run %s wrote %s at %s/%s (cut %d, %d records)",
        row["id"],
        fork_id,
        message_id,
        mode,
        cut,
        written.records_written,
    )
    return {
        "conversation_id": written.conversation_id,
        "path": str(written.path),
        "cut_offset": cut,
        "from_message_id": message_id,
        "from_message_role": detail.role,
        "mode": mode,
        "records_written": written.records_written,
        "records_dropped": written.records_dropped,
        "attachments_copied": written.attachments_copied,
        "bytes_written": written.bytes_written,
    }


# ------------------------------------------------------- "wherever it has got to"


async def resolve_latest_run(
    run_id: str, *, history: Any, automation_store: Any
) -> dict[str, Any] | None:
    """The newest run this conversation has become, following continuations only.

    A conversation does not stay in one history row. Two things move it, and neither
    is visible from the other:

    - A **rollover** (``/clear``, an in-CLI ``/resume``) retires the run and mints a
      new one *in the same pane*, which is what ``note_id`` chains together.
    - A **resume** opens a new pane on the same conversation and records a ``resume``
      lineage edge.

    Both are followed; nothing else is. A ``branch`` edge is a different conversation
    by construction, and a ``review`` or ``handoff`` edge is a different agent reading
    this one - following either would silently retarget a schedule at work its author
    never pointed it at, which is the whole failure mode this resolver exists to avoid
    a naive "whatever that pane holds now" lookup causing.

    Returns the row itself when nothing continues it, and ``None`` when the starting
    row is gone.
    """
    current: dict[str, Any] | None = await history.history_entry(run_id)
    if current is None:
        return None
    seen = {str(current["id"])}
    for _hop in range(LATEST_RUN_MAX_HOPS):
        following = await _next_run(current, history=history, automation_store=automation_store)
        if following is None or str(following["id"]) in seen:
            return current
        seen.add(str(following["id"]))
        current = following
    log.warning(
        "resolve_latest_run walked %d hops from %s and stopped", LATEST_RUN_MAX_HOPS, run_id
    )
    return current


async def _next_run(
    row: dict[str, Any], *, history: Any, automation_store: Any
) -> dict[str, Any] | None:
    """The single run that continues ``row``, rollover first, then resume."""
    note_id = str(row.get("note_id") or "")
    if note_id:
        siblings: list[dict[str, Any]] = await history.agent_runs_for_session(note_id)
        ordered = [item for item in siblings if str(item["id"]) != str(row["id"])]
        later = [
            item
            for item in ordered
            if (int(item.get("agent_run_seq") or 0), float(item.get("spawned_at") or 0.0))
            > (int(row.get("agent_run_seq") or 0), float(row.get("spawned_at") or 0.0))
        ]
        if later:
            return later[0]
    edges: list[dict[str, Any]] = await automation_store.lineage(str(row["id"]))
    children = [
        edge
        for edge in edges
        if edge.get("relation") == "resume" and str(edge.get("parent_run_id")) == str(row["id"])
    ]
    for edge in sorted(children, key=lambda item: float(item.get("created_at") or 0.0)):
        child: dict[str, Any] | None = await history.history_entry(str(edge["child_run_id"]))
        if child is not None:
            return child
    return None
