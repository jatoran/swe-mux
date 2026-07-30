from __future__ import annotations

import time
from collections import Counter, deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from .models import MuxEvent

DeliveryState = Literal["safe", "blocked", "unknown"]

READINESS_DEBOUNCE_SECONDS = 2.0
TERMINAL_EVIDENCE_MAX_AGE_SECONDS = 10.0
LIFECYCLE_EVIDENCE_MAX_AGE_SECONDS = 5 * 60.0


class ReadinessSession(Protocol):
    record: Any
    observation_state: dict[str, Any]
    subscribers: set[Any]
    input_owner: str | None
    input_revision: int
    last_input_event_ts: float
    terminal_mode: str | None
    terminal_mode_updated_at: float
    # Daemon-side screen tracking (`screen_mode.py`). Optional on the protocol so
    # a stand-in without a PTY simply has no daemon evidence.
    screen: Any


@dataclass(slots=True)
class ReadinessMemory:
    run_id: str | None
    phase: str = "unknown"
    reason: str = "no_root_lifecycle_evidence"
    source: str = "none"
    observed_at: float = 0.0
    phase_since: float = 0.0
    input_revision_at_completion: int | None = None
    screen_at_completion: str | None = None
    # Set when this session's own transcript has been read and interpreted, which
    # a live parser status cannot show for a session that has been idle since
    # before the daemon started.
    observation_proven: bool = False
    transition_count: int = 0
    subagent_events: int = 0
    transitions: deque[dict[str, Any]] = field(default_factory=lambda: deque(maxlen=32))


# `screen` is the buffer each CLI draws its prompt in, so that "the screen is not
# showing the agent's prompt" can be evidence at all. Claude Code enters the
# alternate screen at startup and never leaves it; mux launches Codex with
# `tui.alternate_screen="never"` for scrollback (`codex_tui.py`), so its prompt is
# on the normal screen. Only a *contradiction* of this blocks — see `_screen_check`.
ADAPTER_DELIVERY_ETIQUETTE: dict[str, dict[str, str]] = {
    "claude": {
        "submission": "terminal_line",
        "root_completion": "stop_or_transcript",
        "screen": "alternate",
    },
    "codex": {
        "submission": "terminal_line",
        "root_completion": "task_complete",
        "screen": "normal",
    },
}


class DeliveryReadinessTracker:
    """Conservative, read-only readiness evidence.

    The tracker never grants actuation authority. It records root lifecycle evidence and
    combines it with volatile terminal/input facts when diagnostics are requested.
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self.clock = clock
        self._sessions: dict[str, ReadinessMemory] = {}
        self._evaluation_counts: Counter[str] = Counter()
        self._reason_counts: Counter[str] = Counter()

    def forget(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)

    def _memory(self, session: ReadinessSession) -> ReadinessMemory:
        session_id = str(session.record.id)
        run_id = session.record.agent_run_id
        memory = self._sessions.get(session_id)
        if memory is None or memory.run_id != run_id:
            now = self.clock()
            memory = ReadinessMemory(run_id=run_id, observed_at=now, phase_since=now)
            self._sessions[session_id] = memory
        return memory

    def _transition(
        self,
        memory: ReadinessMemory,
        *,
        phase: str,
        reason: str,
        event: MuxEvent,
        input_revision: int | None = None,
        screen: str | None = None,
    ) -> None:
        now = self.clock()
        changed = memory.phase != phase or memory.reason != reason
        if changed:
            memory.phase_since = now
            memory.transition_count += 1
        memory.phase = phase
        memory.reason = reason
        memory.source = event.source
        memory.observed_at = now
        if input_revision is not None:
            memory.input_revision_at_completion = input_revision
            memory.screen_at_completion = screen
        elif phase != "ready":
            memory.input_revision_at_completion = None
            memory.screen_at_completion = None
        memory.transitions.append(
            {
                "phase": phase,
                "reason": reason,
                "event": event.type,
                "source": event.source,
                "scope": str(event.payload.get("scope") or "root"),
            }
        )

    def observe(self, event: MuxEvent, session: ReadinessSession | None) -> None:
        if not event.session_id or session is None:
            return
        memory = self._memory(session)
        scope = str(event.payload.get("scope") or "root")
        if scope == "subagent" or event.type == "subagent_activity":
            memory.subagent_events += 1
            memory.transitions.append(
                {
                    "phase": memory.phase,
                    "reason": "subagent_event_ignored_for_root_readiness",
                    "event": event.type,
                    "source": event.source,
                    "scope": "subagent",
                }
            )
            return

        if event.type in {"backend_detected", "session_spawned"}:
            self._sessions.pop(event.session_id, None)
            self._memory(session)
        elif event.type in {"backend_demoted", "session_exited", "session_crashed"}:
            self._transition(memory, phase="ended", reason=event.type, event=event)
        elif event.type == "turn_started":
            self._transition(memory, phase="working", reason="root_turn_started", event=event)
        elif event.type == "tool_use":
            self._transition(memory, phase="working", reason="root_tool_active", event=event)
        elif event.type == "approval_needed":
            kind = str(event.payload.get("kind") or "approval")
            reason = (
                "awaiting_user_input"
                if kind in {"input", "question", "elicitation"}
                else "approval_required"
            )
            self._transition(memory, phase="awaiting", reason=reason, event=event)
        elif event.type == "rate_limited":
            self._transition(
                memory, phase="rate_limited", reason="provider_rate_limit", event=event
            )
        elif event.type == "stalled":
            self._transition(memory, phase="working", reason="root_turn_stalled", event=event)
        elif event.type == "turn_aborted":
            outcome = str(event.payload.get("outcome") or "aborted")
            self._transition(
                memory, phase="aborted", reason=f"root_turn_{outcome}", event=event
            )
        elif event.type == "turn_ended":
            outcome = str(event.payload.get("outcome") or "completed")
            if outcome == "completed":
                self._transition(
                    memory,
                    phase="ready",
                    reason="root_turn_completed",
                    event=event,
                    input_revision=int(getattr(session, "input_revision", 0)),
                    # The screen the CLI was drawing its prompt on the moment it
                    # finished: the baseline a later takeover is measured against.
                    # Daemon-observed only, for the same reason the check is.
                    screen=getattr(getattr(session, "screen", None), "mode", None),
                )
            elif outcome == "rate_limited":
                self._transition(
                    memory, phase="rate_limited", reason="provider_rate_limit", event=event
                )
            else:
                self._transition(
                    memory, phase="aborted", reason=f"root_turn_{outcome}", event=event
                )

    def _adopt_catchup_settle(
        self, session: ReadinessSession, memory: ReadinessMemory
    ) -> None:
        """Take the observer's catch-up conclusion as this session's turn boundary.

        Only ever fills a gap. With any lifecycle evidence of its own the tracker
        ignores this, because "the transcript holds no live root turn" is weaker
        provenance than a completion record and must never overrule one — hence
        its own reason string, and the `idle` requirement on top.

        Read here rather than handled as an event because the observer that has
        it caught up during adoption, hundreds of lines of startup before the
        fleet subscribed to the bus, so the announcement reached nobody. Sessions
        left running across a restart are exactly the ones this is for.
        """
        if memory.phase != "unknown" or session.record.state != "idle":
            return
        observation_state = getattr(session, "observation_state", None)
        settled = observation_state.get("catchup_settled") if observation_state else None
        if not isinstance(settled, dict) or not settled.get("records"):
            return
        now = self.clock()
        memory.phase = "ready"
        memory.reason = "root_turn_settled_after_catchup"
        memory.source = "transcript"
        memory.observed_at = now
        memory.phase_since = now
        memory.transition_count += 1
        memory.observation_proven = True
        # Captured when the observer settled, not now: the composer-collision
        # guard has to measure from the moment the conclusion was true.
        memory.input_revision_at_completion = int(settled.get("input_revision") or 0)
        screen = settled.get("screen")
        memory.screen_at_completion = screen if isinstance(screen, str) else None
        memory.transitions.append(
            {
                "phase": "ready",
                "reason": "root_turn_settled_after_catchup",
                "event": "catchup_settled",
                "source": "transcript",
                "scope": "root",
            }
        )

    @staticmethod
    def _screen_evidence(session: ReadinessSession, now: float) -> tuple[str | None, str]:
        """The screen buffer this PTY's child is drawing to, and where that came from.

        The daemon's own reading wins and never expires: a child stays in the
        buffer it selected until it selects another, and the daemon sees that
        switch on the PTY whether or not anyone is watching the pane.

        The browser's report is reported alongside it but is deliberately not
        allowed to block (`_screen_check`). It is not the same kind of fact: xterm
        reports the buffer *its own* replayed copy of the stream put it in, so a
        pane that attached after the child's `?1049h` scrolled out of the retained
        scrollback reports `normal` for a child that has been on the alternate
        screen since startup. Measured on a live Claude session immediately after
        a daemon restart. A derived state that is wrong exactly when the daemon's
        own reading is missing cannot be the fallback for it.
        """
        parser = getattr(session, "screen", None)
        daemon_mode = getattr(parser, "mode", None)
        if isinstance(daemon_mode, str) and daemon_mode:
            return daemon_mode, "daemon"
        updated_at = float(getattr(session, "terminal_mode_updated_at", 0.0))
        browser_mode = getattr(session, "terminal_mode", None)
        if (
            isinstance(browser_mode, str)
            and browser_mode
            and updated_at > 0
            and now - updated_at <= TERMINAL_EVIDENCE_MAX_AGE_SECONDS
        ):
            return browser_mode, "browser"
        return None, "none"

    @staticmethod
    def _expected_screen(record: Any, memory: ReadinessMemory) -> str | None:
        """Where this session's agent prompt was, last time it was definitely there.

        The screen observed at root completion is the best answer, because the CLI
        had just finished rendering its prompt: it needs no per-version or
        per-configuration knowledge, and it does not punish a Codex launched with
        an explicit `tui.alternate_screen` override. The adapter's declaration is
        the fallback for a session whose completion predates any screen evidence.
        """
        if memory.screen_at_completion:
            return memory.screen_at_completion
        return ADAPTER_DELIVERY_ETIQUETTE.get(str(record.backend), {}).get("screen")

    @classmethod
    def _screen_check(
        cls, record: Any, memory: ReadinessMemory, mode: str | None, source: str
    ) -> bool:
        """False only when the daemon watched the child leave its prompt's screen.

        A veto, not a prerequisite, and only on the daemon's own reading. Absence
        of screen evidence is not evidence of danger — requiring it made `safe`
        unreachable for every session nobody happened to be watching, which is
        the entire population the queue exists to serve — and a browser's report
        is not strong enough to block on, because it describes the buffer that
        pane's replayed copy of the stream selected rather than the one the child
        did.
        """
        expected = cls._expected_screen(record, memory)
        if source != "daemon" or mode is None or expected is None:
            return True
        return mode == expected

    @staticmethod
    def _parser_check(session: ReadinessSession, memory: ReadinessMemory) -> bool | None:
        status = str(session.record.parser_status or "")
        if status == "degraded":
            return None
        if status == "ready":
            return True
        # A native hook is positive lifecycle evidence even while transcript discovery is
        # pending. PTY-only or daemon-only inference is insufficient.
        if memory.source == "hook":
            return True
        # So is having read this session's own transcript: `parser_status` only
        # reaches "ready" off a *live* record, which a session idle since before
        # the daemon started will not produce until its next turn, and no hook
        # fires while it waits. The observer's catch-up over real records proves
        # the same capability.
        if memory.observation_proven:
            return True
        return None

    def evaluate(
        self, session: ReadinessSession, *, record_metrics: bool = True
    ) -> dict[str, Any]:
        now = self.clock()
        memory = self._memory(session)
        record = session.record
        self._adopt_catchup_settle(session, memory)
        lifecycle_age = max(0.0, now - memory.observed_at)
        terminal_mode = getattr(session, "terminal_mode", None)
        terminal_mode_updated_at = float(getattr(session, "terminal_mode_updated_at", 0.0))
        input_revision = int(getattr(session, "input_revision", 0))
        last_input_event_ts = float(getattr(session, "last_input_event_ts", 0.0))
        terminal_observer_connected = bool(getattr(session, "subscribers", ()))
        exclusive_input_owner = bool(getattr(session, "input_owner", None))
        terminal_age = (
            max(0.0, now - terminal_mode_updated_at)
            if terminal_mode_updated_at > 0
            else None
        )
        screen_mode, screen_source = self._screen_evidence(session, now)
        screen_at_agent_prompt = self._screen_check(record, memory, screen_mode, screen_source)

        root_ready = record.state == "idle" and memory.phase == "ready"
        # An agent parked at its prompt does not decay. Freshness guards against
        # an observation pipeline that died mid-turn and left a claim nobody is
        # updating; "the root turn completed and the session is still idle" is a
        # resting state that any new activity would itself contradict — a turn
        # start, an approval, or operator input all arrive through paths this
        # tracker already watches. Every other phase keeps the age bound.
        lifecycle_fresh = root_ready or lifecycle_age <= LIFECYCLE_EVIDENCE_MAX_AGE_SECONDS
        partial_input_absent: bool | None = None
        if memory.input_revision_at_completion is not None:
            partial_input_absent = input_revision == memory.input_revision_at_completion
        operator_quiet = now - last_input_event_ts >= READINESS_DEBOUNCE_SECONDS
        checks: dict[str, bool | None] = {
            "live_agent_run": bool(
                record.agent_run_id and record.backend in ADAPTER_DELIVERY_ETIQUETTE
            ),
            "stable_run_identity": bool(memory.run_id and memory.run_id == record.agent_run_id),
            "root_prompt_ready": root_ready,
            "root_completion_observed": memory.phase == "ready",
            "lifecycle_evidence_fresh": lifecycle_fresh,
            "parser_or_hook_supported": self._parser_check(session, memory),
            "operator_quiet": operator_quiet,
            "partial_input_absent": partial_input_absent,
            "screen_at_agent_prompt": screen_at_agent_prompt,
            # An attached browser and an input owner are *not* preconditions for
            # writing to a PTY: the daemon owns the PTY, and delivery is the same
            # write whether or not a pane is rendering it. Both facts stay in the
            # evidence below for diagnostics.
            # swe-mux currently has no application-level composer. Terminal draft input is
            # covered conservatively by input_revision after root completion.
            "application_composer_empty": True,
            "adapter_etiquette_defined": record.backend in ADAPTER_DELIVERY_ETIQUETTE,
            "readiness_debounce_elapsed": now - memory.phase_since >= READINESS_DEBOUNCE_SECONDS,
        }

        hard_block_reasons: list[str] = []
        if not checks["live_agent_run"]:
            hard_block_reasons.append("not_live_agent_run")
        if record.state in {"working", "starting", "running"} or memory.phase == "working":
            hard_block_reasons.append("root_agent_working")
        if record.state == "awaiting" or memory.phase == "awaiting":
            hard_block_reasons.append(memory.reason)
        if memory.phase == "rate_limited":
            hard_block_reasons.append("provider_rate_limit")
        if memory.phase == "aborted":
            hard_block_reasons.append(memory.reason)
        if record.state in {"exited", "crashed"} or memory.phase == "ended":
            hard_block_reasons.append("session_ended")
        if not screen_at_agent_prompt:
            hard_block_reasons.append("screen_not_at_agent_prompt")
        if partial_input_absent is False:
            hard_block_reasons.append("terminal_input_after_completion")
        if not operator_quiet:
            hard_block_reasons.append("operator_recently_typed")
        if getattr(record, "observation_stale_since", None):
            # The followed transcript is no longer the conversation this PTY runs
            # (an unfollowable in-CLI `/clear` or `/new`). Every positive readiness
            # signal below is then being read off a retired conversation, so this
            # blocks outright rather than degrading to unknown — writing into a
            # session whose state we are provably misreading is the false-safe case
            # the whole contract exists to prevent.
            hard_block_reasons.append("transcript_stale")

        unknown_reasons: list[str] = []
        if memory.phase == "unknown":
            unknown_reasons.append("no_root_lifecycle_evidence")
        if checks["parser_or_hook_supported"] is None:
            unknown_reasons.append("observation_capability_unknown")
        if partial_input_absent is None:
            unknown_reasons.append("completion_input_boundary_unknown")
        if not checks["lifecycle_evidence_fresh"]:
            unknown_reasons.append("lifecycle_evidence_stale")
        if root_ready and not checks["readiness_debounce_elapsed"]:
            unknown_reasons.append("readiness_debounce_pending")

        if hard_block_reasons:
            delivery_state: DeliveryState = "blocked"
            reasons = list(dict.fromkeys(hard_block_reasons))
        elif unknown_reasons:
            delivery_state = "unknown"
            reasons = list(dict.fromkeys(unknown_reasons))
        elif all(value is True for value in checks.values()):
            delivery_state = "safe"
            reasons = ["all_required_evidence_positive"]
        else:
            delivery_state = "unknown"
            reasons = ["incomplete_readiness_evidence"]

        if record_metrics:
            self._evaluation_counts[delivery_state] += 1
            for reason in reasons:
                self._reason_counts[reason] += 1
        return {
            "session_id": record.id,
            "agent_run_id": record.agent_run_id,
            "backend": record.backend,
            "state": record.state,
            "delivery_state": delivery_state,
            "reason": reasons[0],
            "reasons": reasons,
            "checks": checks,
            "evidence": {
                "root_phase": memory.phase,
                "root_reason": memory.reason,
                "source": memory.source,
                "age_s": round(lifecycle_age, 3),
                "screen_mode": screen_mode or "unknown",
                "screen_source": screen_source,
                "expected_screen": self._expected_screen(record, memory),
                "completion_screen": memory.screen_at_completion,
                "terminal_mode": terminal_mode or "unknown",
                "terminal_age_s": round(terminal_age, 3) if terminal_age is not None else None,
                "terminal_observer_connected": terminal_observer_connected,
                "exclusive_input_owner": exclusive_input_owner,
                "input_revision": input_revision,
                "completion_input_revision": memory.input_revision_at_completion,
                "subagent_events_ignored": memory.subagent_events,
                "recent_transitions": list(memory.transitions),
            },
            "candidate_safe": delivery_state == "safe",
            "authorized": False,
            "diagnostic": ", ".join(reasons),
        }

    def metrics(self) -> dict[str, Any]:
        now = self.clock()
        return {
            "evaluations": dict(self._evaluation_counts),
            "reasons": dict(self._reason_counts),
            "tracked_sessions": len(self._sessions),
            "unknown_duration_s": round(
                sum(
                    max(0.0, now - item.phase_since)
                    for item in self._sessions.values()
                    if item.phase == "unknown"
                ),
                3,
            ),
            "transitions": sum(item.transition_count for item in self._sessions.values()),
        }
