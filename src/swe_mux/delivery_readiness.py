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
    subscribers: set[Any]
    input_owner: str | None
    input_revision: int
    last_input_event_ts: float
    terminal_mode: str | None
    terminal_mode_updated_at: float


@dataclass(slots=True)
class ReadinessMemory:
    run_id: str | None
    phase: str = "unknown"
    reason: str = "no_root_lifecycle_evidence"
    source: str = "none"
    observed_at: float = 0.0
    phase_since: float = 0.0
    input_revision_at_completion: int | None = None
    transition_count: int = 0
    subagent_events: int = 0
    transitions: deque[dict[str, Any]] = field(default_factory=lambda: deque(maxlen=32))


ADAPTER_DELIVERY_ETIQUETTE: dict[str, dict[str, str]] = {
    "claude": {"submission": "terminal_line", "root_completion": "stop_or_transcript"},
    "codex": {"submission": "terminal_line", "root_completion": "task_complete"},
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
        elif phase != "ready":
            memory.input_revision_at_completion = None
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
                )
            elif outcome == "rate_limited":
                self._transition(
                    memory, phase="rate_limited", reason="provider_rate_limit", event=event
                )
            else:
                self._transition(
                    memory, phase="aborted", reason=f"root_turn_{outcome}", event=event
                )

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
        return None

    def evaluate(
        self, session: ReadinessSession, *, record_metrics: bool = True
    ) -> dict[str, Any]:
        now = self.clock()
        memory = self._memory(session)
        record = session.record
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
        terminal_compatible: bool | None
        if terminal_mode is None or terminal_age is None:
            terminal_compatible = None
        elif terminal_age > TERMINAL_EVIDENCE_MAX_AGE_SECONDS:
            terminal_compatible = None
        else:
            terminal_compatible = terminal_mode == "normal"

        root_ready = record.state == "idle" and memory.phase == "ready"
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
            "lifecycle_evidence_fresh": lifecycle_age <= LIFECYCLE_EVIDENCE_MAX_AGE_SECONDS,
            "parser_or_hook_supported": self._parser_check(session, memory),
            "operator_quiet": operator_quiet,
            "partial_input_absent": partial_input_absent,
            "terminal_mode_compatible": terminal_compatible,
            "terminal_observer_connected": terminal_observer_connected,
            "exclusive_input_owner_present": exclusive_input_owner,
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
        if terminal_compatible is False:
            hard_block_reasons.append("alternate_screen_active")
        if partial_input_absent is False:
            hard_block_reasons.append("terminal_input_after_completion")
        if not operator_quiet:
            hard_block_reasons.append("operator_recently_typed")
        if not terminal_observer_connected or not exclusive_input_owner:
            hard_block_reasons.append("terminal_observer_disconnected")
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
        if terminal_compatible is None:
            unknown_reasons.append("terminal_mode_unknown_or_stale")
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
                "terminal_mode": terminal_mode or "unknown",
                "terminal_age_s": round(terminal_age, 3) if terminal_age is not None else None,
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
