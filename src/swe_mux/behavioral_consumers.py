"""Adaptive titling and phase-transition signals (Phase 7.7).

Both consumers ride the scan timeline: when a scan record lands, the
:class:`BehavioralConsumerService` evaluates one shared *pivot* definition over
that run's scan spine and drives two independent, per-Project-toggleable
outputs:

- **Adaptive titling** (`continuous_title`): broadens an auto-named run's title
  only on a genuine pivot, on the cheap model, biased hard toward keeping the
  current title. Stability is the default; a re-title is the exception. It never
  overwrites a human-set name and stays scoped to one ``agent_run_id``.
- **Phase-transition signals** (`phase_transitions`): emits a durable annotation
  (which the attention pipeline ranks) on a genuine ``work_phase`` pivot and on
  a prolonged flat-novelty stall within one phase.

The two share exactly one pivot definition (:func:`evaluate_pivot`), so they can
never disagree about what a pivot is - the binding constraint from
``CONTROL_PLANE_ROADMAP.md`` §6.11: a title is a *handle*, and a handle that
moves on routine progress is not one.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from . import budget
from .openrouter import OpenRouterError
from .scan_consumers import first_nonempty, target_set

if TYPE_CHECKING:
    from .scan_timeline import ScanContext

log = logging.getLogger(__name__)

ADAPTIVE_TITLE_RULE_ID = "builtin:adaptive-title"
ADAPTIVE_TITLE_CHECKPOINT_PREFIX = "adaptive-title:"
PIVOT_BASELINE_CHECKPOINT_PREFIX = "pivot-baseline:"
PHASE_SIGNAL_CHECKPOINT_PREFIX = "phase-signal:"

# A pivot needs a genuine novelty spike; routine progress scores low against the
# same-run records it is compared to.
NOVELTY_PIVOT = 0.6
# Two target sets below this Jaccard overlap count as a target shift.
TARGET_OVERLAP_PIVOT = 0.5
# The scan spine must have accumulated before an adaptive re-title is possible;
# the one-shot prompt title owns the opening name.
MIN_PRIOR_RECORDS = 2

# A flat-novelty stall: at least this many consecutive low-novelty records in one
# work phase spanning at least this long. Sized to the roadmap's "stuck in
# debugging for 40 min" example, biased to not cry stall on a short quiet patch.
STALL_NOVELTY = 0.15
STALL_MIN_RECORDS = 3
STALL_MIN_SECONDS = 1800.0
_UNKNOWN_PHASE = "unknown"

# Titler hysteresis: never rewrite twice in quick succession.
RETITLE_COOLDOWN_SECONDS = 120.0
RETITLE_MIN_RECORDS = 2

# `required` lists every property deliberately: a `strict` json_schema response
# format is rejected outright ("'required' ... to be an array including every key
# in properties") when one is left optional, so an omission here is not a looser
# schema but a call that can never succeed. `test_llm_schemas.py` guards the rule.
TITLE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["title", "confidence"],
    "properties": {
        "title": {"type": "string", "maxLength": 80},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
}

TITLE_SYSTEM_PROMPT = (
    "You maintain the short tab title for one coding-agent run. You are given the "
    "CURRENT title and recent behavioral records (work_phase, intent, user_ask, "
    "summary) for THIS run only. Keep the current title UNCHANGED unless the run's "
    "subject has materially changed. When in doubt, return the current title "
    "verbatim - 'no change' is the common, correct answer. Prefer broadening the "
    "existing handle over inventing a new one (for example 'Phase 7' becomes "
    "'Phase 7 + diagnostics' once the scope widens). Emit a compact task label of "
    "2-5 words. Never prefix with Terminal Session, Session, Claude, Codex, User, "
    "or Conversation. Return only the schema."
)


def _norm(text: Any) -> str:
    return " ".join(str(text or "").split()).casefold()


def _jaccard(left: set[str], right: set[str]) -> float:
    union = left | right
    return len(left & right) / len(union) if union else 0.0


@dataclass(frozen=True, slots=True)
class PivotDecision:
    is_pivot: bool
    is_stall: bool
    from_phase: str
    to_phase: str
    stall_seconds: float
    novelty: float
    baseline: dict[str, Any]
    reasons: list[str] = field(default_factory=list)


def _detect_stall(records: list[dict[str, Any]]) -> tuple[bool, float, str]:
    """A prolonged flat-novelty stall within one work phase.

    Walks back from the newest record while the phase is unchanged and novelty
    stays low, and reports a stall once the streak is long enough in both count
    and wall-clock. Returns (is_stall, stall_seconds, phase).
    """
    if not records:
        return (False, 0.0, _UNKNOWN_PHASE)
    latest = records[-1]
    phase = str(latest.get("work_phase") or _UNKNOWN_PHASE)
    if phase == _UNKNOWN_PHASE:
        return (False, 0.0, phase)
    streak: list[dict[str, Any]] = []
    for record in reversed(records):
        if str(record.get("work_phase") or _UNKNOWN_PHASE) != phase:
            break
        if float(record.get("novelty") or 0.0) > STALL_NOVELTY:
            break
        streak.append(record)
    if len(streak) < STALL_MIN_RECORDS:
        return (False, 0.0, phase)
    span = float(latest.get("t1") or 0.0) - float(streak[-1].get("t0") or 0.0)
    return (span >= STALL_MIN_SECONDS, span, phase)


def evaluate_pivot(
    record: dict[str, Any],
    prior_records: list[dict[str, Any]],
    baseline: dict[str, Any] | None,
) -> PivotDecision:
    """The one shared pivot definition for titling and phase signals.

    A pivot fires when a novelty spike coincides with a structural transition -
    a new ``work_phase``, a materially different ``target`` set, or a new
    ``user_ask`` - measured against the *baseline* established at the last pivot
    (or the run's first record). ``prior_records`` is oldest-first and excludes
    the new record.
    """
    phase = str(record.get("work_phase") or _UNKNOWN_PHASE)
    novelty = float(record.get("novelty") or 0.0)
    targets = target_set(record)
    user_ask = _norm(record.get("user_ask"))

    reasons: list[str] = []
    is_stall, stall_seconds, _ = _detect_stall([*prior_records, record])

    base_phase = str((baseline or {}).get("phase") or "") or None
    base_targets = {str(item) for item in (baseline or {}).get("targets") or []}
    base_user_ask = _norm((baseline or {}).get("user_ask"))

    new_baseline = {
        "phase": phase,
        "targets": sorted(targets),
        "user_ask": user_ask,
        "t1": float(record.get("t1") or 0.0),
    }
    if baseline is None or base_phase is None or len(prior_records) < MIN_PRIOR_RECORDS:
        # Establish (or re-establish) the baseline without ever firing on the
        # first records; the one-shot prompt title owns the opening name.
        return PivotDecision(
            is_pivot=False,
            is_stall=is_stall,
            from_phase=base_phase or phase,
            to_phase=phase,
            stall_seconds=stall_seconds,
            novelty=novelty,
            baseline=new_baseline if baseline is None else baseline,
        )

    novelty_spike = novelty >= NOVELTY_PIVOT
    phase_shift = phase != _UNKNOWN_PHASE and phase != base_phase
    target_shift = (
        bool(targets)
        and bool(base_targets)
        and _jaccard(targets, base_targets) < TARGET_OVERLAP_PIVOT
    )
    ask_shift = bool(user_ask) and user_ask != base_user_ask
    if phase_shift:
        reasons.append(f"work_phase {base_phase}->{phase}")
    if target_shift:
        reasons.append("target set shifted")
    if ask_shift:
        reasons.append("new user ask")
    if not novelty_spike:
        reasons.append(f"novelty {novelty:.2f} below {NOVELTY_PIVOT}")

    is_pivot = novelty_spike and (phase_shift or target_shift or ask_shift)
    return PivotDecision(
        is_pivot=is_pivot,
        is_stall=is_stall,
        from_phase=base_phase,
        to_phase=phase,
        stall_seconds=stall_seconds,
        novelty=novelty,
        baseline=new_baseline if is_pivot else (baseline or new_baseline),
        reasons=reasons,
    )


class BehavioralConsumerService:
    """Drives adaptive titling and phase-transition signals off scan records."""

    def __init__(
        self, *, store: Any, sessions: Any, config: Any, provider: Any, events: Any
    ) -> None:
        self.store = store
        self.sessions = sessions
        self.config = config
        self.provider = provider
        self.events = events

    async def on_scan_record(
        self,
        *,
        session: Any,
        context: ScanContext,
        record: dict[str, Any],
        prior_records: list[dict[str, Any]],
    ) -> None:
        """Hook called by the scan timeline right after a record is saved.

        Any failure here is contained: a titling or signalling fault must never
        break scanning, so the caller wraps this and this method also guards its
        own model call.
        """
        wants_title = bool(getattr(context, "continuous_title_enabled", False))
        wants_phase = bool(getattr(context, "phase_transitions_enabled", False))
        if not (wants_title or wants_phase):
            return
        run_id = str(context.agent_run_id or "")
        if not run_id:
            return

        baseline_key = f"{PIVOT_BASELINE_CHECKPOINT_PREFIX}{run_id}"
        baseline = await self.store.checkpoint(baseline_key)
        decision = evaluate_pivot(record, prior_records, baseline)
        # Persist the (possibly re-established) baseline so "since last pivot" is
        # stable across records and daemon restarts.
        if baseline is None or decision.is_pivot:
            await self.store.set_checkpoint(baseline_key, decision.baseline)

        if wants_phase:
            await self._phase_signals(session, context, record, decision)
        if wants_title:
            await self._maybe_retitle(session, context, record, prior_records, decision)

    async def _phase_signals(
        self,
        session: Any,
        context: ScanContext,
        record: dict[str, Any],
        decision: PivotDecision,
    ) -> None:
        run_id = str(context.agent_run_id or "")
        key = f"{PHASE_SIGNAL_CHECKPOINT_PREFIX}{run_id}"
        state = dict(await self.store.checkpoint(key) or {})
        changed = False

        if decision.is_pivot:
            ask = _norm(record.get("user_ask"))
            signature = f"{decision.from_phase}->{decision.to_phase}:{ask}"
            if state.get("last_pivot_signature") != signature:
                detail = first_nonempty(record, "intent", "summary", "user_ask") or "scope widened"
                await self._emit_signal(
                    session,
                    context,
                    tag="phase-pivot",
                    content=(
                        f"Pivoted from {decision.from_phase} to {decision.to_phase}: {detail}"
                    )[:600],
                    confidence=float(record.get("confidence") or 0.7),
                    record=record,
                    reasons=decision.reasons,
                )
                state["last_pivot_signature"] = signature
                changed = True

        if decision.is_stall:
            # One signal per stall episode: re-arm only when the phase changes.
            if state.get("stall_phase") != decision.to_phase:
                minutes = int(decision.stall_seconds // 60)
                await self._emit_signal(
                    session,
                    context,
                    tag="phase-stall",
                    content=(
                        f"Stuck in {decision.to_phase} for ~{minutes} min with no new "
                        "progress in the scan spine."
                    )[:600],
                    confidence=0.7,
                    record=record,
                    reasons=["flat novelty stall"],
                )
                state["stall_phase"] = decision.to_phase
                changed = True
        elif state.get("stall_phase") and state.get("stall_phase") != decision.to_phase:
            # Left the stalled phase: re-arm.
            state.pop("stall_phase", None)
            changed = True

        if changed:
            await self.store.set_checkpoint(key, state)

    async def _emit_signal(
        self,
        session: Any,
        context: ScanContext,
        *,
        tag: str,
        content: str,
        confidence: float,
        record: dict[str, Any],
        reasons: list[str],
    ) -> None:
        annotation = await self.store.create_annotation(
            agent_run_id=str(context.agent_run_id or ""),
            project_id=str(context.project_id or ""),
            session_id=str(session.record.id),
            tag=tag,
            content=content,
            provenance="scan_timeline",
            rule_id="builtin:phase-transitions",
            confidence=confidence,
            evidence=[
                {
                    "kind": tag,
                    "scan_record_id": str(record.get("id") or ""),
                    "reasons": reasons,
                }
            ],
        )
        await self.events.emit(
            "annotation_created",
            session_id=str(session.record.id),
            source="automation",
            annotation_id=annotation["id"],
            tag=tag,
            rule_id="builtin:phase-transitions",
        )

    async def _maybe_retitle(
        self,
        session: Any,
        context: ScanContext,
        record: dict[str, Any],
        prior_records: list[dict[str, Any]],
        decision: PivotDecision,
    ) -> None:
        run_id = str(context.agent_run_id or "")
        key = f"{ADAPTIVE_TITLE_CHECKPOINT_PREFIX}{run_id}"
        state = dict(await self.store.checkpoint(key) or {})
        state["records_seen"] = int(state.get("records_seen") or 0) + 1
        # Count every processed record, reset only on a successful re-title, so the
        # "records since the last re-title" hysteresis actually advances between
        # pivots instead of sticking at the value it held when the last one landed.
        state["records_since_retitle"] = int(state.get("records_since_retitle") or 0) + 1

        # A manually named session keeps its user title, permanently and across a
        # rollover; the pin is a property of the session.
        if getattr(session.record, "auto_named", True) is False:
            await self.store.set_checkpoint(key, state)
            return
        if not decision.is_pivot:
            await self.store.set_checkpoint(key, state)
            return

        now = time.time()
        retitles = int(state.get("retitle_count") or 0)
        last_retitle_ts = float(state.get("last_retitle_ts") or 0.0)
        # Hysteresis: never twice in quick succession.
        if retitles and (
            state["records_since_retitle"] < RETITLE_MIN_RECORDS
            or now - last_retitle_ts < RETITLE_COOLDOWN_SECONDS
        ):
            await self.store.set_checkpoint(key, state)
            return

        current = await self.store.recent_annotation(run_id, "title", 0)
        current_title = str((current or {}).get("content") or "").strip()
        records = [*prior_records, record]
        new_title = await self._synthesize_title(
            run_id, str(context.project_id or ""), current_title, records
        )
        if not new_title or _norm(new_title) == _norm(current_title):
            # No change is a first-class, cheap outcome: it writes nothing (the
            # record counter already advanced above).
            await self.store.set_checkpoint(key, state)
            return

        annotation = await self.store.create_annotation(
            agent_run_id=run_id,
            project_id=str(context.project_id or ""),
            session_id=str(session.record.id),
            tag="title",
            content=new_title[:80],
            provenance="adaptive_title",
            rule_id=ADAPTIVE_TITLE_RULE_ID,
            confidence=float(record.get("confidence") or 0.7),
            evidence=[
                {
                    "kind": "adaptive_title",
                    "from": current_title,
                    "scan_record_id": str(record.get("id") or ""),
                    "reasons": decision.reasons,
                }
            ],
        )
        await self.events.emit(
            "annotation_created",
            session_id=str(session.record.id),
            source="automation",
            annotation_id=annotation["id"],
            tag="title",
            rule_id=ADAPTIVE_TITLE_RULE_ID,
        )
        state.update(
            retitle_count=retitles + 1,
            records_since_retitle=0,
            last_retitle_ts=now,
            last_title=new_title[:80],
        )
        await self.store.set_checkpoint(key, state)
        log.info(
            "adaptive title re-titled run=%s from=%r to=%r reasons=%s",
            run_id,
            current_title,
            new_title,
            decision.reasons,
        )

    async def _synthesize_title(
        self,
        run_id: str,
        project_id: str,
        current_title: str,
        records: list[dict[str, Any]],
    ) -> str | None:
        """One cheap-model synthesis call, budget-guarded and fully accounted."""
        model = str(getattr(self.config, "openrouter_cheap_model", "") or "")
        if not model:
            # Logged rather than skipped silently: a pivot was detected and then
            # deliberately not acted on, which reads from the outside exactly like
            # a titler that never fires. These are the only two paths that decline
            # before an observer row exists to record the decision.
            log.info("adaptive title skipped for run=%s: no cheap model configured", run_id)
            return None
        global_spend = await self.store.spend()
        if budget.spent_out(
            self.config.automation_daily_budget, global_spend, label="the global daily automation"
        ).exhausted:
            log.info("adaptive title skipped for run=%s: daily automation budget spent", run_id)
            return None
        recent = [
            {
                "work_phase": item.get("work_phase"),
                "intent": first_nonempty(item, "intent"),
                "user_ask": first_nonempty(item, "user_ask"),
                "summary": first_nonempty(item, "summary"),
            }
            for item in records[-8:]
        ]
        user_content = json.dumps(
            {"current_title": current_title, "records": recent},
            separators=(",", ":"),
            ensure_ascii=False,
        )
        input_hash = hashlib.sha256(user_content.encode("utf-8")).hexdigest()
        call_id = await self.store.observer_started(
            firing_id=f"adaptive-title:{run_id}:{int(time.time() * 1000)}",
            rule_id=ADAPTIVE_TITLE_RULE_ID,
            model=model,
            input_hash=input_hash,
            input_bytes=len(user_content.encode("utf-8")),
        )
        try:
            completion = await self.provider.complete_json(
                model=model,
                messages=[
                    {"role": "system", "content": TITLE_SYSTEM_PROMPT},
                    {"role": "user", "content": user_content},
                ],
                schema_name="adaptive_title_v1",
                schema=TITLE_SCHEMA,
                max_tokens=64,
                reasoning_enabled=False,
            )
        except asyncio.CancelledError:
            # A cancelled call still has to leave a terminal row: the shutdown that
            # cancels it is exactly when a `running` row would be stranded forever.
            await self.store.observer_finished(call_id, status="cancelled", error="cancelled")
            raise
        except OpenRouterError as exc:
            # The whole diagnostic point of the ledger is that a failed call can be
            # read back without replaying it, so record what the failure *was* -
            # status and retryability above all - and not only that one happened.
            await self.store.observer_finished(
                call_id,
                status="failed",
                resolved_model=exc.resolved_model,
                generation_id=exc.generation_id,
                input_tokens=exc.input_tokens,
                output_tokens=exc.output_tokens,
                cost_usd=exc.cost_usd,
                latency_ms=exc.latency_ms,
                provider_name=exc.provider_name,
                finish_reason=exc.finish_reason,
                response_content_type=exc.response_content_type,
                response_content_length=exc.response_content_length,
                http_status=exc.status,
                retryable=exc.retryable,
                error=str(exc)[:1000],
            )
            if exc.generation_id or exc.input_tokens or exc.output_tokens or exc.cost_usd:
                await self.store.add_spend(
                    rule_id=ADAPTIVE_TITLE_RULE_ID,
                    model=exc.resolved_model or model,
                    input_tokens=exc.input_tokens,
                    output_tokens=exc.output_tokens,
                    cost_usd=exc.cost_usd,
                    call_id=call_id,
                    project_id=project_id,
                    agent_run_id=run_id,
                )
            log.warning("adaptive title synthesis failed for run=%s: %s", run_id, exc)
            return None
        except Exception as exc:  # noqa: BLE001 - titling must never break scanning
            # Anything the provider layer did not wrap (a serialization fault, a
            # store error) would otherwise leave the row `running` and the failure
            # invisible, which is how this consumer stayed broken unnoticed.
            await self.store.observer_finished(
                call_id, status="failed", error=f"{type(exc).__name__}: {exc}"[:1000]
            )
            log.warning("adaptive title synthesis failed for run=%s: %s", run_id, exc)
            return None
        await self.store.observer_finished(
            call_id,
            status="completed",
            resolved_model=completion.resolved_model,
            generation_id=completion.generation_id,
            input_tokens=completion.input_tokens,
            output_tokens=completion.output_tokens,
            cost_usd=float(completion.cost_usd or 0.0),
            latency_ms=completion.latency_ms,
            provider_name=completion.provider_name,
            finish_reason=completion.finish_reason,
        )
        await self.store.add_spend(
            rule_id=ADAPTIVE_TITLE_RULE_ID,
            model=completion.resolved_model or model,
            input_tokens=completion.input_tokens,
            output_tokens=completion.output_tokens,
            cost_usd=completion.cost_usd,
            call_id=call_id,
            project_id=project_id,
            agent_run_id=run_id,
        )
        return str(completion.value.get("title") or "").strip() or None
