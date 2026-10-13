"""Restarting a live agent in place: a fresh process on the same conversation.

A long-lived agent CLI accumulates process state that has nothing to do with its
conversation: a Claude process that has carried a conversation for days holds
hundreds of megabytes more than one that has just loaded it, and a Codex process
keeps every MCP helper any of its subagents ever started. The conversation itself
lives in the CLI's transcript, so the cure is the same as a Resume on an ended
pane, applied to a pane that is still alive: end the process gracefully, then
resume the same conversation into the same pane.

This module is the *policy* half - which sessions may be restarted, and which
refusals a human may override. The orchestration lives beside the other in-place
replacements in `routes/sessions.py`, and the resume itself goes through the single
resume authority (`session_resume.resume_run`), so a restart can never open a
conversation a Resume would have refused.

Two kinds of refusal, and the split is the whole design:

- **Identity refusals are never overridable.** A restart resumes the conversation
  the record names. If that name is unknown, provisional, stale, or not readable,
  the restart would end a working process and then open the wrong conversation or
  none - strictly worse than doing nothing, whoever asked.
- **Activity refusals are overridable by a human** (`force`), and only by one:
  a turn in flight, a pending approval, background work, unsent composer text,
  queued messages that would strand, or an open auto-merge request. Each names
  exactly what the restart would lose, so the confirmation the UI shows is the
  daemon's own list rather than the browser's guess. The route that accepts
  `force` refuses agent-session callers (`refuse_agent_session_caller`) and the
  operation is not on the agent MCP surface at all, so no agent can restart a
  session - its own or another's.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .harness import assigns_conversation_id, has_observable_transcript, is_agent_harness

#: The end reason a restarted process records. Distinct from `killed` and
#: `agent_ended` so a post-mortem can tell an operator's restart from a kill.
RESTART_END_REASON = "restarted"

#: Lineage relation for the edge a restart records when the replacement opens a
#: new run row (`session_resume.CONTINUATION_RELATIONS` follows it).
RESTART_LINEAGE_RELATION = "restart"

#: How long after the graceful exit the restart waits for the old session's
#: teardown (history row, transcript index, recovery row) to finish before it
#: resumes. The resume adopts the same run row, so writing it while the old pane
#: is still closing it would race two writers on one row.
END_SETTLE_TIMEOUT_SECONDS = 60.0

#: Operator input this recent counts as somebody typing into the pane.
RECENT_INPUT_SECONDS = 3.0

_WORKING_STATES = frozenset({"working", "running", "starting"})
_ENDED_STATES = frozenset({"exited", "crashed"})


@dataclass(frozen=True, slots=True)
class RestartBlocker:
    """One reason a restart will not run unattended."""

    code: str
    message: str
    #: Whether an explicit human confirmation may proceed anyway. When true,
    #: ``message`` describes what the restart would lose.
    forceable: bool

    def payload(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "forceable": self.forceable}


@dataclass(frozen=True, slots=True)
class RestartFacts:
    """What the policy needs that is not on the session itself.

    Gathered by the caller because each one is a store or OS read that the pure
    policy below must not perform: the Project registry, the prompt queue, the
    land queue, the transcript on disk, and the daemon's own ancestry.
    """

    project_known: bool = True
    transcript_readable: bool = True
    hosts_daemon: bool = False
    queued_messages: int = 0
    open_land_request: bool = False
    history_row_known: bool = True
    #: Monotonic seconds since the last operator input, or None when unknown.
    input_age_seconds: float | None = None


@dataclass(frozen=True, slots=True)
class RestartAssessment:
    blockers: tuple[RestartBlocker, ...] = field(default_factory=tuple)

    @property
    def eligible(self) -> bool:
        return not self.blockers

    @property
    def force_allowed(self) -> bool:
        """True when every blocker is one a human may override."""
        return bool(self.blockers) and all(item.forceable for item in self.blockers)

    def permits(self, *, force: bool) -> bool:
        return self.eligible or (force and self.force_allowed)

    def payload(self) -> dict[str, Any]:
        first = self.blockers[0] if self.blockers else None
        return {
            "eligible": self.eligible,
            "code": first.code if first else None,
            "reason": first.message if first else None,
            "force_allowed": self.force_allowed,
            "blockers": [item.payload() for item in self.blockers],
            "would_lose": [item.message for item in self.blockers if item.forceable],
        }


def _identity_blockers(session: Any, facts: RestartFacts) -> list[RestartBlocker]:
    """Refusals no confirmation may override: the restart could not come back."""
    record = session.record
    backend = str(getattr(record, "backend", "") or "")
    if not is_agent_harness(backend) or not has_observable_transcript(backend):
        return [
            RestartBlocker(
                "not_agent",
                "only an agent whose conversation mux can resume can be restarted",
                False,
            )
        ]
    if str(getattr(record, "state", "")) in _ENDED_STATES:
        return [
            RestartBlocker(
                "session_ended", "this session has already ended; use Resume instead", False
            )
        ]
    blockers: list[RestartBlocker] = []
    if getattr(record, "inactive", False) or getattr(record, "cold", False):
        blockers.append(
            RestartBlocker(
                "session_inactive", "an inactive session is resumed, not restarted", False
            )
        )
    if getattr(session, "restart_in_progress", False):
        blockers.append(
            RestartBlocker("restart_in_progress", "this session is already being restarted", False)
        )
    if getattr(record, "runtime_boundary", "local") != "local":
        blockers.append(
            RestartBlocker(
                "remote_boundary",
                "the agent is running beyond this machine's terminal boundary, so a local "
                "resume would not reopen it",
                False,
            )
        )
    blockers.extend(_conversation_blockers(session, facts))
    if facts.hosts_daemon:
        blockers.append(
            RestartBlocker(
                "hosts_daemon",
                "this session hosts the running daemon; ending it would take the daemon down",
                False,
            )
        )
    if not facts.project_known:
        blockers.append(
            RestartBlocker(
                "project_missing", "the session's owning Project is no longer registered", False
            )
        )
    return blockers


def _conversation_blockers(session: Any, facts: RestartFacts) -> list[RestartBlocker]:
    """Whether the conversation this pane would resume is known and reachable."""
    record = session.record
    native_id = str(getattr(record, "native_session_id", "") or "")
    placeholder = not assigns_conversation_id(record.backend) and native_id == str(record.id)
    if not native_id or placeholder or not getattr(record, "agent_run_id", None):
        return [
            RestartBlocker(
                "conversation_unknown",
                "the agent has not reported which conversation it is on yet, so there is "
                "nothing to resume",
                False,
            )
        ]
    blockers: list[RestartBlocker] = []
    if getattr(session, "transcript_provisional", False):
        blockers.append(
            RestartBlocker(
                "conversation_unproven",
                "the transcript this pane follows is not yet proven to be its own "
                "conversation",
                False,
            )
        )
    if getattr(record, "observation_stale_since", None):
        blockers.append(
            RestartBlocker(
                "transcript_stale",
                "the agent appears to have moved to another conversation mux cannot follow, "
                "so a restart could reopen the wrong one",
                False,
            )
        )
    if not facts.history_row_known:
        blockers.append(
            RestartBlocker(
                "history_unavailable", "the conversation's history row is unavailable", False
            )
        )
    if not facts.transcript_readable:
        blockers.append(
            RestartBlocker(
                "transcript_unavailable",
                "the conversation's transcript cannot be read, so it could not be resumed",
                False,
            )
        )
    return blockers


def _activity_blockers(session: Any, facts: RestartFacts) -> list[RestartBlocker]:
    """Refusals a human may override, each naming what the restart would lose."""
    record = session.record
    state = str(getattr(record, "state", ""))
    blockers: list[RestartBlocker] = []
    if state in _WORKING_STATES:
        blockers.append(
            RestartBlocker("agent_working", "the agent's current turn will be interrupted", True)
        )
    if state == "awaiting":
        reason = str(getattr(record, "awaiting_reason", "") or "input")
        blockers.append(
            RestartBlocker(
                "awaiting_input",
                f"the pending {reason.replace('_', ' ')} prompt will be abandoned",
                True,
            )
        )
    standing = list(getattr(record, "standing_activity", None) or [])
    if standing or getattr(record, "running_work_since", None):
        kinds = sorted({str(getattr(item, "kind", "work")).replace("_", " ") for item in standing})
        detail = ", ".join(kinds) if kinds else "background work"
        blockers.append(
            RestartBlocker(
                "background_work", f"running background work will be stopped ({detail})", True
            )
        )
    composer = getattr(session, "composer", None)
    if (composer is not None and getattr(composer, "pending", False)) or getattr(
        session, "pending_submit", None
    ) is not None:
        blockers.append(
            RestartBlocker(
                "unsent_input", "text sitting in the agent's composer will be lost", True
            )
        )
    if facts.input_age_seconds is not None and facts.input_age_seconds < RECENT_INPUT_SECONDS:
        blockers.append(
            RestartBlocker("recent_input", "somebody typed into this pane a moment ago", True)
        )
    if facts.queued_messages:
        plural = "message" if facts.queued_messages == 1 else "messages"
        blockers.append(
            RestartBlocker(
                "queue_pending",
                f"{facts.queued_messages} queued {plural} addressed to this pane will be "
                "stranded (they can be retargeted from the Queue)",
                True,
            )
        )
    if facts.open_land_request:
        blockers.append(
            RestartBlocker(
                "land_in_progress",
                "the answer to this pane's open auto-merge request will not reach the new process",
                True,
            )
        )
    return blockers


def assess_restart(session: Any, facts: RestartFacts) -> RestartAssessment:
    """Every reason ``session`` may not be restarted, identity refusals first.

    Pure over the session and the gathered facts, so the probe the menu shows and
    the check the restart makes are the same function and cannot disagree.
    Identity refusals short-circuit nothing: the full list is returned so the
    probe can say everything at once, but any identity refusal makes
    ``force_allowed`` false.
    """
    identity = _identity_blockers(session, facts)
    if identity and identity[0].code in {"not_agent", "session_ended"}:
        return RestartAssessment(tuple(identity))
    return RestartAssessment(tuple(identity + _activity_blockers(session, facts)))
