"""Attention ranking: which of N sessions actually needs the human.

Roadmap Phase 6.5, control-plane build-order step 7
(`CONTROL_PLANE_ROADMAP.md` §6.7). Every earlier control-plane layer *writes*
findings; this is the layer that decides which of them is worth a human's
attention, and when. Nothing here actuates a session: routing a finding to a
channel is the entire output.

Five properties are load-bearing and easy to lose:

- **The incident is the unit of interruption, not the finding.** Several
  detectors reporting one underlying event share an `incident_key` and therefore
  one budget slot. Budgeting per finding is how one stuck run becomes four
  interruptions.
- **Cheap-blocking and expensive-blocking never share a channel.** Answering a
  permission prompt costs seconds; discovering the plan is wrong costs an hour.
  Merging them is the clinical-alarm failure mode, and it trains the user to
  ignore the surface both arrive on.
- **The daily interrupt budget is a hard bound.** When it is spent, further
  incidents are still recorded and still readable — they are demoted with an
  explicit `suppressed_reason`, never dropped. A suppressed item the user cannot
  see is indistinguishable from a detector that never fired.
- **Rank against the live run only.** A finding anchored to a conversation the
  session has rolled past describes work the agent can no longer act on. It stays
  inspectable in the digest, attributed to the run it came from, and is excluded
  from ranking rather than deleted.
- **No push.** Ranked items surface in-app. This layer holds no push route, no
  device routing, and no sound; the settle-gated `waiting` alert in
  `notifications.py` is a separate, older path and is unchanged by any setting
  here.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from .background_tasks import background
from .deterministic_consumers import ConsumerContext

log = logging.getLogger(__name__)

ATTENTION_LOOP = "attention-ranking"

RANKING_AUTOMATION = "attention_ranking"
DIGEST_AUTOMATION = "absence_report"
NARRATION_AUTOMATION = "model_narration"

# Four channels, split by cost-to-resolve and never merged (§6.7).
INTERRUPT_NOW = "interrupt_now"
NEXT_BREAKPOINT = "next_breakpoint"
INBOX = "inbox"
DIGEST = "digest"
CHANNELS = (INTERRUPT_NOW, NEXT_BREAKPOINT, INBOX, DIGEST)

CHEAP_BLOCKING = "cheap_blocking"
EXPENSIVE_BLOCKING = "expensive_blocking"
NON_BLOCKING = "non_blocking"

# An interrupt has to be worth the interruption: only a worsening condition with
# a concrete action and high confidence may take a slot. Everything else waits
# for a breakpoint or the inbox. The threshold is deliberately high because a
# usually-wrong signal is worse than no signal.
INTERRUPT_CONFIDENCE = 0.8

# Two interaction events closer together than this belong to one burst. The
# fan-out estimate reads burst *duration* as interaction time and the gap between
# bursts as neglect time, so this constant defines both.
BURST_GAP_SECONDS = 60.0
# Below this many samples the fan-out estimate reports itself unavailable rather
# than publishing a number derived from two data points.
MIN_FANOUT_SAMPLES = 5
FANOUT_SAMPLE_LIMIT = 200
RESUMPTION_SAMPLE_LIMIT = 100

# Behaviour mining (PrefMiner): a class/channel pairing the user has dismissed
# this consistently, this many times, induces a demotion rule — proposed to the
# user, never applied silently.
RULE_MIN_OBSERVATIONS = 5
RULE_DISMISS_RATE = 0.8
RULE_WINDOW_SECONDS = 30 * 86400
# An accepted rule expires, which is the periodic forced judgment call: a
# standing suppression nobody re-confirms is how a surface quietly goes blind.
RULE_REVIEW_SECONDS = 14 * 86400
RULE_CHECKPOINT_PREFIX = "attention:rule:"
ACTIVITY_CHECKPOINT = "fleet:last_user_activity"


@dataclass(frozen=True, slots=True)
class KindPolicy:
    """How one detector output is classified before it is ranked."""

    incident_class: str
    cost_to_resolve: str
    worsening: bool
    base_score: float
    action: str | None


# The classification table. A kind absent here is unknown to ranking and is
# routed to the digest: an unclassified finding must never be able to spend an
# interrupt slot by default.
KIND_POLICY: dict[str, KindPolicy] = {
    "loop-detected": KindPolicy(
        "stuck",
        EXPENSIVE_BLOCKING,
        True,
        0.9,
        "Redirect the run: the same action repeated with nothing moving.",
    ),
    "stalled": KindPolicy(
        "stuck",
        EXPENSIVE_BLOCKING,
        True,
        0.8,
        "Check the session: it is working with no output and no CPU.",
    ),
    "runaway": KindPolicy(
        "stuck",
        EXPENSIVE_BLOCKING,
        True,
        0.7,
        "Check the session: output is running away outside a turn.",
    ),
    "declared-vs-verified": KindPolicy(
        "unverified",
        EXPENSIVE_BLOCKING,
        False,
        0.6,
        "Ask for a verification run before accepting the completion claim.",
    ),
    "claim_unverified": KindPolicy(
        "unverified",
        EXPENSIVE_BLOCKING,
        False,
        0.55,
        "Ask for a verification run before accepting the completion claim.",
    ),
    "context_pressure": KindPolicy(
        "context",
        EXPENSIVE_BLOCKING,
        True,
        0.75,
        "Compact or hand off before the context window runs out.",
    ),
    # Cheap-blocking: seconds to resolve, and it never spends interrupt budget.
    "unattended_attention": KindPolicy(
        "blocked_on_human",
        CHEAP_BLOCKING,
        False,
        0.5,
        "Answer the prompt this session is waiting on.",
    ),
    "port_collision": KindPolicy(
        "environment",
        CHEAP_BLOCKING,
        False,
        0.4,
        "Two sessions are bound to one port; move one of them.",
    ),
    # Non-blocking: a fact worth keeping, never worth an interruption.
    "doc-debt": KindPolicy("docs", NON_BLOCKING, False, 0.2, None),
    "provenance": KindPolicy("provenance", NON_BLOCKING, False, 0.1, None),
    "prior-resolution": KindPolicy("knowledge", NON_BLOCKING, False, 0.2, None),
}

# Fleet events that carry a fault. `cross_session_dev_server` is deliberately
# absent: it reaches the event bus as evidence and is not a fault, and a detector
# that fires on the documented workflow trains the user to ignore the surface.
FLEET_EVENT_KINDS = frozenset(
    {"stalled", "runaway", "context_pressure", "claim_unverified", "unattended_attention"}
)
INTERLOCK_FAULT_KINDS = frozenset({"port_collision"})
ANNOTATION_TAGS = frozenset(
    {"loop-detected", "declared-vs-verified", "doc-debt", "provenance", "prior-resolution"}
)

TITLES: dict[str, str] = {
    "stuck": "Session is not making progress",
    "unverified": "Completion claimed without verification",
    "blocked_on_human": "Session is waiting on you",
    "context": "Context window under pressure",
    "environment": "Environment conflict between sessions",
    "docs": "Documentation owes an update",
    "provenance": "Cross-session file provenance",
    "knowledge": "A prior run hit this before",
    "unclassified": "Unclassified finding",
}

UNCLASSIFIED = KindPolicy("unclassified", NON_BLOCKING, False, 0.1, None)


def policy_for(kind: str) -> KindPolicy:
    return KIND_POLICY.get(kind, UNCLASSIFIED)


def day_key(now: float) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(now))


def incident_key(
    *,
    incident_class: str,
    anchor: str,
    now: float,
    window_seconds: float,
) -> str:
    """Group findings about one underlying event onto one interruptible incident.

    The bucket is what keeps a recurrence a day later from silently merging into
    a resolved incident, and what keeps three detectors firing about the same
    stuck run inside one window from spending three slots.
    """
    bucket = int(now // max(1.0, window_seconds))
    raw = f"{incident_class}|{anchor}|{bucket}"
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


@dataclass(frozen=True, slots=True)
class Finding:
    """One normalized detector output on its way into ranking."""

    kind: str
    session_id: str | None
    agent_run_id: str | None
    project_id: str | None
    summary: str
    confidence: float
    evidence: list[dict[str, Any]]
    source: str
    source_id: str | None = None


@dataclass(frozen=True, slots=True)
class Routing:
    """The channel decision for one incident, with the reason it was demoted."""

    channel: str
    suppressed_reason: str | None
    spends_budget: bool


def route(
    policy: KindPolicy,
    *,
    confidence: float,
    superseded_run: bool,
    budget_available: bool,
    demoted_by_rule: str | None,
) -> Routing:
    """Decide one incident's channel. Pure, so the policy is testable on its own."""
    if superseded_run:
        # The agent cannot act on a conversation it has rolled past, and spending
        # interrupt budget on something the user already resolved by clearing is
        # the worst possible use of a small budget.
        return Routing(DIGEST, "superseded_run", False)
    if policy.cost_to_resolve == NON_BLOCKING:
        return Routing(DIGEST, None, False)
    if demoted_by_rule:
        return Routing(INBOX, f"rule:{demoted_by_rule}", False)
    if policy.cost_to_resolve == CHEAP_BLOCKING:
        # Cheap-blocking work batches and drains at the human's next pause. It
        # never spends interrupt budget, however many prompts are waiting.
        return Routing(NEXT_BREAKPOINT, None, False)
    if not policy.worsening or policy.action is None:
        return Routing(NEXT_BREAKPOINT, None, False)
    if confidence < INTERRUPT_CONFIDENCE:
        return Routing(INBOX, "low_confidence", False)
    if not budget_available:
        return Routing(INBOX, "budget_exhausted", False)
    return Routing(INTERRUPT_NOW, None, True)


def score_for(policy: KindPolicy, *, confidence: float, contributions: int) -> float:
    """Rank within a channel: base severity, weighted by confidence and corroboration."""
    corroboration = min(1.0, 0.7 + 0.1 * max(0, contributions - 1))
    return round(policy.base_score * max(0.0, min(1.0, confidence)) * corroboration, 4)


@dataclass(frozen=True, slots=True)
class InducedRule:
    """A demotion rule mined from behaviour, awaiting or holding user acceptance."""

    incident_class: str
    channel: str
    dismissed: int
    total: int
    statement: str
    state: str
    expires_at: float | None = None

    def snapshot(self) -> dict[str, Any]:
        return {
            "incident_class": self.incident_class,
            "channel": self.channel,
            "dismissed": self.dismissed,
            "total": self.total,
            "dismiss_rate": round(self.dismissed / self.total, 3) if self.total else 0.0,
            "statement": self.statement,
            "state": self.state,
            "expires_at": self.expires_at,
        }


def mine_rules(stats: list[dict[str, Any]]) -> list[InducedRule]:
    """Induce demotion rules from act/dismiss behaviour, never from stated preference.

    The rule is surfaced for an explicit accept or reject: a suppression the user
    never agreed to is indistinguishable, from the user's side, from a detector
    that silently broke.
    """
    totals: dict[tuple[str, str], dict[str, int]] = defaultdict(
        lambda: {"dismissed": 0, "total": 0}
    )
    for row in stats:
        key = (str(row["incident_class"]), str(row["channel"]))
        count = int(row["count"])
        totals[key]["total"] += count
        if str(row["action"]) == "dismissed":
            totals[key]["dismissed"] += count
    rules: list[InducedRule] = []
    for (incident_class, channel), counts in sorted(totals.items()):
        total = counts["total"]
        dismissed = counts["dismissed"]
        if total < RULE_MIN_OBSERVATIONS or dismissed / total < RULE_DISMISS_RATE:
            continue
        rules.append(
            InducedRule(
                incident_class=incident_class,
                channel=channel,
                dismissed=dismissed,
                total=total,
                statement=(
                    f"You dismissed {dismissed} of {total} "
                    f"{incident_class.replace('_', ' ')} items on {channel.replace('_', ' ')}. "
                    "Hold them in the inbox instead?"
                ),
                state="proposed",
            )
        )
    return rules


class AttentionTelemetry:
    """Fan-out and resumption-lag sampling from attach/input telemetry.

    Fan-out follows Olsen & Goodrich: sustainable agents ≈ neglect time ÷
    interaction time + 1. Both halves are measured here rather than assumed —
    interaction time is the duration of a human's burst of input on one session,
    neglect time is how long that session then ran without them. No vendor can
    compute this, because it needs the layer that owns the human's terminals.

    Resumption lag, not throughput, is the cost of an interruption: interrupted
    work completes faster but pays in a return cost that throughput hides.
    """

    def __init__(self) -> None:
        self._burst_start: dict[str, float] = {}
        self._burst_last: dict[str, float] = {}
        self._burst_end: dict[str, float] = {}
        self._interaction: deque[float] = deque(maxlen=FANOUT_SAMPLE_LIMIT)
        self._neglect: deque[float] = deque(maxlen=FANOUT_SAMPLE_LIMIT)
        self._resumption: deque[float] = deque(maxlen=RESUMPTION_SAMPLE_LIMIT)
        self.last_session: str | None = None
        self.last_activity_ts: float = 0.0
        self._pending_resumption: dict[str, Any] | None = None
        self._left_for_other = False

    def observe_interaction(self, session_id: str, ts: float) -> None:
        """Fold one human interaction event into the burst model."""
        previous_last = self._burst_last.get(session_id)
        if previous_last is None or ts - previous_last > BURST_GAP_SECONDS:
            if previous_last is not None:
                self._close_burst(session_id, previous_last)
            last_end = self._burst_end.get(session_id)
            if last_end is not None and ts > last_end:
                self._neglect.append(ts - last_end)
            self._burst_start[session_id] = ts
        self._burst_last[session_id] = ts
        if self.last_session and self.last_session != session_id:
            self._left_for_other = True
        self._resolve_resumption(session_id, ts)
        self.last_session = session_id
        self.last_activity_ts = max(self.last_activity_ts, ts)

    def _close_burst(self, session_id: str, ended_at: float) -> None:
        started = self._burst_start.pop(session_id, None)
        self._burst_end[session_id] = ended_at
        if started is not None and ended_at > started:
            self._interaction.append(ended_at - started)

    def forget(self, session_id: str) -> None:
        last = self._burst_last.pop(session_id, None)
        if last is not None:
            self._close_burst(session_id, last)
        self._burst_end.pop(session_id, None)
        if self.last_session == session_id:
            self.last_session = None

    def note_interruption(self, session_id: str, ts: float) -> None:
        """An interrupt was delivered; start measuring what it costs to come back."""
        origin = self.last_session
        if not origin or origin == session_id:
            return
        self._pending_resumption = {
            "origin": origin,
            "left_at": self._burst_last.get(origin, ts),
            "at": ts,
        }
        self._left_for_other = False

    def _resolve_resumption(self, session_id: str, ts: float) -> None:
        pending = self._pending_resumption
        if not pending or not self._left_for_other or pending["origin"] != session_id:
            return
        self._resumption.append(max(0.0, ts - float(pending["left_at"])))
        self._pending_resumption = None
        self._left_for_other = False

    @staticmethod
    def _mean(samples: deque[float]) -> float | None:
        return round(sum(samples) / len(samples), 2) if samples else None

    def fanout(self, *, attended_now: int) -> dict[str, Any]:
        interaction = self._mean(self._interaction)
        neglect = self._mean(self._neglect)
        samples = min(len(self._interaction), len(self._neglect))
        if samples < MIN_FANOUT_SAMPLES or not interaction or not neglect:
            return {
                "status": "insufficient_samples",
                "samples": samples,
                "required": MIN_FANOUT_SAMPLES,
                "interaction_seconds": interaction,
                "neglect_seconds": neglect,
                "sustainable_agents": None,
                "attended_now": attended_now,
            }
        return {
            "status": "ok",
            "samples": samples,
            "required": MIN_FANOUT_SAMPLES,
            "interaction_seconds": interaction,
            "neglect_seconds": neglect,
            "sustainable_agents": max(1, int(neglect / interaction) + 1),
            "attended_now": attended_now,
        }

    def resumption(self) -> dict[str, Any]:
        return {
            "samples": len(self._resumption),
            "mean_seconds": self._mean(self._resumption),
            "max_seconds": round(max(self._resumption), 2) if self._resumption else None,
        }

    def snapshot(self) -> dict[str, Any]:
        return {
            "interaction": list(self._interaction),
            "neglect": list(self._neglect),
            "resumption": list(self._resumption),
            "last_activity_ts": self.last_activity_ts,
        }

    def restore(self, snapshot: dict[str, Any]) -> None:
        """Adopt persisted samples so a daemon restart does not zero the estimate."""
        for key, target in (
            ("interaction", self._interaction),
            ("neglect", self._neglect),
            ("resumption", self._resumption),
        ):
            values = snapshot.get(key)
            if isinstance(values, list):
                keep = target.maxlen or len(values)
                target.clear()
                target.extend(float(item) for item in values[-keep:])
        self.last_activity_ts = float(snapshot.get("last_activity_ts") or 0.0)


class AttentionRankingService:
    """Ranks detector findings into four channels under a hard daily budget."""

    def __init__(
        self,
        store: Any,
        sessions: Any,
        events: Any,
        config: Any,
        *,
        resolve_context: Callable[[str], Awaitable[ConsumerContext | None]],
        narrator: Any | None = None,
    ) -> None:
        self.store = store
        self.sessions = sessions
        self.events = events
        self.config = config
        self._resolve_context = resolve_context
        self.narrator = narrator
        self.telemetry = AttentionTelemetry()
        self._queue: asyncio.Queue[Any] | None = None
        self._narrations: set[asyncio.Task[None]] = set()
        self.ranked = 0
        self.interrupts = 0
        self.suppressed = 0
        self.last_error: str | None = None

    # ---- lifecycle -------------------------------------------------------

    def start(self) -> None:
        if self._queue is not None:
            return
        self._queue = self.events.subscribe(name=ATTENTION_LOOP)
        background.start(ATTENTION_LOOP, self._consume)

    async def stop(self) -> None:
        await background.stop(ATTENTION_LOOP)
        if self._queue is not None:
            self.events.unsubscribe(self._queue)
            self._queue = None
        for task in tuple(self._narrations):
            task.cancel()
        await asyncio.gather(*self._narrations, return_exceptions=True)
        self._narrations.clear()

    async def restore(self) -> None:
        """Adopt persisted fan-out samples across a daemon restart."""
        snapshot = await self.store.checkpoint("attention:telemetry")
        if snapshot:
            self.telemetry.restore(snapshot)

    async def _consume(self) -> None:
        assert self._queue is not None
        while True:
            event = await self._queue.get()
            with background.iteration(ATTENTION_LOOP):
                try:
                    await self._consume_one(event)
                except Exception as exc:  # noqa: BLE001 - one event must not kill the loop
                    self.last_error = str(exc)[:200]
                    log.warning("attention ranking failed on %s: %s", event.type, exc)

    async def _consume_one(self, event: Any) -> None:
        if event.type in {"terminal_input", "terminal_attached"} and event.session_id:
            self.telemetry.observe_interaction(str(event.session_id), float(event.ts))
            return
        if event.type in {"session_exited", "session_crashed"} and event.session_id:
            self.telemetry.forget(str(event.session_id))
            return
        if event.type == "shell_command_finished" and event.session_id:
            await self.breakpoint_reached(str(event.session_id))
            return
        finding = await self._finding_from(event)
        if finding is not None:
            await self.ingest(finding)

    async def _finding_from(self, event: Any) -> Finding | None:
        if event.type == "annotation_created":
            tag = str(event.payload.get("tag") or "")
            if tag not in ANNOTATION_TAGS or not event.session_id:
                # A project-anchored finding (doc debt, provenance) carries no
                # session, so it has no run to rank against and no session to
                # send anyone to. It stays readable in the digest, which reads
                # annotations directly.
                return None
            annotation = await self.store.annotation(str(event.payload.get("annotation_id")))
            if annotation is None:
                return None
            return Finding(
                kind=tag,
                session_id=str(event.session_id),
                agent_run_id=annotation.get("agent_run_id"),
                project_id=annotation.get("project_id"),
                summary=str(annotation.get("content") or "")[:600],
                confidence=float(annotation.get("confidence") or 0.9),
                evidence=[
                    {"source": "annotation", "annotation_id": annotation["id"], "tag": tag}
                ],
                source="annotation",
                source_id=str(annotation["id"]),
            )
        if event.type == "environment_interlock":
            kind = str(event.payload.get("kind") or "")
            if kind not in INTERLOCK_FAULT_KINDS or not event.session_id:
                return None
            return self._fleet_finding(event, kind)
        if event.type in FLEET_EVENT_KINDS and event.session_id:
            return self._fleet_finding(event, event.type)
        return None

    def _fleet_finding(self, event: Any, kind: str) -> Finding | None:
        session = self.sessions.sessions.get(str(event.session_id))
        if session is None:
            return None
        record = session.record
        evidence = event.payload.get("evidence")
        return Finding(
            kind=kind,
            session_id=str(event.session_id),
            agent_run_id=record.agent_run_id,
            project_id=record.project_id,
            summary=_fleet_summary(kind, record.name or str(event.session_id)),
            confidence=float(event.payload.get("confidence") or 0.7),
            evidence=[
                {"source": "fleet_event", "event_type": event.type, "kind": kind},
                *(evidence if isinstance(evidence, list) else []),
            ],
            source="fleet",
        )

    # ---- ranking ---------------------------------------------------------

    async def ingest(self, finding: Finding) -> dict[str, Any] | None:
        """Rank one finding, or fold it into the incident it belongs to."""
        if not finding.session_id:
            return None
        context = await self._resolve_context(finding.session_id)
        if context is None or not context.wants(RANKING_AUTOMATION):
            return None
        now = time.time()
        policy = policy_for(finding.kind)
        anchor = finding.agent_run_id or finding.session_id or context.project_id
        key = incident_key(
            incident_class=policy.incident_class,
            anchor=anchor,
            now=now,
            window_seconds=float(
                getattr(self.config, "attention_incident_window_seconds", 3600.0)
            ),
        )
        existing = await self._existing(key)
        if existing is not None:
            return await self._merge(existing, finding, policy)
        superseded = self._superseded(finding)
        rule = await self._demotion_rule(policy.incident_class)
        budget = await self.budget(now)
        routing = route(
            policy,
            confidence=finding.confidence,
            superseded_run=superseded,
            budget_available=bool(budget["remaining"] > 0 and budget["burst_remaining"] > 0),
            demoted_by_rule=rule,
        )
        delivered = now if routing.channel == INTERRUPT_NOW else None
        item: dict[str, Any] = await self.store.upsert_attention_item(
            incident_key=key,
            project_id=finding.project_id or context.project_id,
            session_id=finding.session_id,
            agent_run_id=finding.agent_run_id,
            incident_class=policy.incident_class,
            kinds=[finding.kind],
            title=TITLES.get(policy.incident_class, TITLES["unclassified"]),
            summary=finding.summary,
            action=policy.action,
            channel=routing.channel,
            cost_to_resolve=policy.cost_to_resolve,
            score=score_for(policy, confidence=finding.confidence, contributions=1),
            confidence=finding.confidence,
            evidence=finding.evidence,
            suppressed_reason=routing.suppressed_reason,
            budget_day=day_key(now) if routing.spends_budget else None,
            delivered_at=delivered,
        )
        self.ranked += 1
        if routing.suppressed_reason:
            self.suppressed += 1
        if routing.spends_budget:
            self.interrupts += 1
            self.telemetry.note_interruption(finding.session_id, now)
        log.info(
            "attention item %s class=%s kind=%s channel=%s suppressed=%s session=%s run=%s",
            item["id"],
            policy.incident_class,
            finding.kind,
            routing.channel,
            routing.suppressed_reason or "-",
            finding.session_id,
            finding.agent_run_id or "-",
        )
        await self.events.emit(
            "attention_item_ranked",
            session_id=finding.session_id,
            source="automation",
            item_id=item["id"],
            incident_class=policy.incident_class,
            channel=routing.channel,
            suppressed_reason=routing.suppressed_reason,
        )
        self._schedule_narration(item, context)
        return item

    async def _existing(self, key: str) -> dict[str, Any] | None:
        item: dict[str, Any] | None = await self.store.attention_item_by_key(key)
        return item

    async def _merge(
        self, existing: dict[str, Any], finding: Finding, policy: KindPolicy
    ) -> dict[str, Any]:
        contributions = int(existing.get("contributions") or 1) + 1
        merged: dict[str, Any] = await self.store.upsert_attention_item(
            incident_key=str(existing["incident_key"]),
            project_id=existing.get("project_id"),
            session_id=existing.get("session_id"),
            agent_run_id=existing.get("agent_run_id"),
            incident_class=str(existing["incident_class"]),
            kinds=[finding.kind],
            title=str(existing["title"]),
            summary=finding.summary,
            action=str(existing.get("action") or "") or policy.action,
            channel=str(existing["channel"]),
            cost_to_resolve=str(existing["cost_to_resolve"]),
            score=score_for(
                policy, confidence=finding.confidence, contributions=contributions
            ),
            confidence=finding.confidence,
            evidence=finding.evidence,
        )
        log.info(
            "attention item %s absorbed kind=%s (contributions=%s, one budget slot)",
            merged["id"],
            finding.kind,
            merged.get("contributions"),
        )
        return merged

    def _superseded(self, finding: Finding) -> bool:
        """True when the finding names a conversation the session has rolled past."""
        if not finding.agent_run_id or not finding.session_id:
            return False
        session = self.sessions.sessions.get(finding.session_id)
        if session is None:
            return False
        live = session.record.agent_run_id
        return bool(live) and live != finding.agent_run_id

    # ---- budget ----------------------------------------------------------

    async def budget(self, now: float | None = None) -> dict[str, Any]:
        """The day's interrupt budget, and the hourly burst limiter under it.

        The hourly cap is only a burst limiter: on its own it silently authorizes
        8-16 interruptions a day, which is already fatigue territory.
        """
        now = time.time() if now is None else now
        daily = int(getattr(self.config, "attention_daily_interrupt_budget", 4))
        hourly = int(getattr(self.config, "attention_hourly_interrupt_cap", 2))
        used = await self.store.attention_interrupts_used(day_key(now))
        recent = await self.store.attention_items(channel=INTERRUPT_NOW, since=now - 3600)
        burst_used = len([item for item in recent if item.get("delivered_at")])
        return {
            "day": day_key(now),
            "daily_budget": daily,
            "used": used,
            "remaining": max(0, daily - used),
            "hourly_cap": hourly,
            "burst_used": burst_used,
            "burst_remaining": max(0, hourly - burst_used),
        }

    # ---- narration -------------------------------------------------------

    def _schedule_narration(self, item: dict[str, Any], context: ConsumerContext) -> None:
        if self.narrator is None or not context.wants(NARRATION_AUTOMATION):
            return
        if item["channel"] == DIGEST:
            return
        task = asyncio.create_task(
            self._narrate(item), name=f"attention-narration-{item['id']}"
        )
        self._narrations.add(task)
        task.add_done_callback(self._narrations.discard)

    async def _narrate(self, item: dict[str, Any]) -> None:
        assert self.narrator is not None
        text, status = await self.narrator.narrate(item)
        await self.store.update_attention_item(
            item["id"], narration=text, narration_status=status
        )
        log.info("attention narration %s status=%s", item["id"], status)

    # ---- reads -----------------------------------------------------------

    async def inbox(self, *, limit: int = 200) -> dict[str, Any]:
        """The ranked inbox: open items by channel, plus what was held back and why."""
        now = time.time()
        items = await self.store.attention_items(state="open", limit=limit)
        items = [await self._refresh_supersession(item) for item in items]
        by_channel: dict[str, list[dict[str, Any]]] = {channel: [] for channel in CHANNELS}
        suppressed: dict[str, int] = defaultdict(int)
        for item in items:
            by_channel.setdefault(item["channel"], []).append(item)
            if item.get("suppressed_reason"):
                suppressed[str(item["suppressed_reason"])] += 1
        attended = len(
            [
                session
                for session in self.sessions.sessions.values()
                if session.record.agent_run_id
                and session.record.state not in {"exited", "crashed"}
            ]
        )
        return {
            "generated_at": now,
            "channels": by_channel,
            "suppressed": dict(suppressed),
            "suppressed_total": sum(suppressed.values()),
            "budget": await self.budget(now),
            "fanout": self.telemetry.fanout(attended_now=attended),
            "resumption_lag": self.telemetry.resumption(),
            "rules": [rule.snapshot() for rule in await self.rules()],
            # Stated once rather than implied: nothing here reaches a phone.
            "delivery": {"push": False, "surface": "in_app"},
        }

    async def _refresh_supersession(self, item: dict[str, Any]) -> dict[str, Any]:
        """Demote an item whose conversation was replaced after it was ranked."""
        if item.get("suppressed_reason") or item["channel"] == DIGEST:
            return item
        session_id = item.get("session_id")
        run_id = item.get("agent_run_id")
        if not session_id or not run_id:
            return item
        session = self.sessions.sessions.get(str(session_id))
        if session is None or not session.record.agent_run_id:
            return item
        if session.record.agent_run_id == run_id:
            return item
        updated = await self.store.update_attention_item(
            str(item["id"]), channel=DIGEST, suppressed_reason="superseded_run"
        )
        self.suppressed += 1
        log.info("attention item %s demoted: its conversation was replaced", item["id"])
        return updated or item

    async def digest(self, since: float | None = None) -> dict[str, Any]:
        """What happened while the human was away, with rollover boundaries kept.

        A rollover inside the absence window is rendered as a boundary rather than
        smoothed over: "you cleared this session here" is the one piece of context
        that makes the rest of a multi-run digest legible, and a digest that
        narrates two conversations as one stretch of work is actively misleading
        about what the agent currently knows.
        """
        checkpoint = await self.store.checkpoint(ACTIVITY_CHECKPOINT)
        start = (
            since
            if since is not None
            else float(
                (checkpoint or {}).get("ts") or self.telemetry.last_activity_ts or time.time()
            )
        )
        items = await self.store.attention_items(since=start, limit=500)
        boundaries: list[dict[str, Any]] = []
        for session in list(self.sessions.sessions.values()):
            rows = await self.store.scan_boundaries(session.record.id)
            for row in rows:
                if float(row["created_at"]) < start:
                    continue
                boundaries.append(
                    {
                        "session_id": session.record.id,
                        "session_name": session.record.name,
                        "previous_run_id": row["previous_run_id"],
                        "next_run_id": row["next_run_id"],
                        "reason": row["reason"],
                        "created_at": row["created_at"],
                        "note": (
                            "You cleared this session here; "
                            "what follows is a new conversation."
                        ),
                    }
                )
        suppressed: dict[str, int] = defaultdict(int)
        for item in items:
            if item.get("suppressed_reason"):
                suppressed[str(item["suppressed_reason"])] += 1
        return {
            "since": start,
            "generated_at": time.time(),
            "items": items,
            "boundaries": sorted(boundaries, key=lambda row: float(row["created_at"])),
            "suppressed": dict(suppressed),
            "fanout": self.telemetry.fanout(
                attended_now=len(
                    [
                        session
                        for session in self.sessions.sessions.values()
                        if session.record.agent_run_id
                    ]
                )
            ),
            "resumption_lag": self.telemetry.resumption(),
        }

    async def breakpoint_reached(self, session_id: str) -> list[dict[str, Any]]:
        """Drain the next-breakpoint channel when the human finishes their own work.

        The strongest interrupt moment is the human's breakpoint, not the agent's,
        and swe-mux owns the human's terminals: a shell reporting its command
        finished is that moment. Draining moves items to the inbox as *delivered*;
        it never writes to a session.
        """
        items = await self.store.attention_items(channel=NEXT_BREAKPOINT, state="open")
        drained: list[dict[str, Any]] = []
        now = time.time()
        for item in items:
            updated = await self.store.update_attention_item(
                str(item["id"]), channel=INBOX, delivered_at=now
            )
            if updated:
                drained.append(updated)
        if drained:
            log.info(
                "attention breakpoint on %s drained %d item(s) to the inbox",
                session_id,
                len(drained),
            )
            await self.events.emit(
                "attention_breakpoint",
                session_id=session_id,
                source="automation",
                drained=len(drained),
            )
        return drained

    # ---- feedback and mined rules ---------------------------------------

    async def feedback(self, item_id: str, action: str) -> dict[str, Any] | None:
        """Record what the human did with one item; this is the only learning input."""
        if action not in {"acted", "dismissed"}:
            raise ValueError("action must be acted or dismissed")
        item = await self.store.attention_item(item_id)
        if item is None:
            return None
        now = time.time()
        surfaced = float(item.get("delivered_at") or item["created_at"])
        await self.store.record_attention_feedback(
            item_id=item_id,
            incident_class=str(item["incident_class"]),
            channel=str(item["channel"]),
            action=action,
            latency_seconds=max(0.0, now - surfaced),
        )
        updated: dict[str, Any] | None = await self.store.update_attention_item(
            item_id, state=action, resolved_at=now
        )
        log.info("attention item %s resolved as %s", item_id, action)
        return updated

    async def rules(self) -> list[InducedRule]:
        """Mined rules, each carrying whether the user has accepted it and until when."""
        stats = await self.store.attention_feedback_stats(time.time() - RULE_WINDOW_SECONDS)
        mined = {(rule.incident_class, rule.channel): rule for rule in mine_rules(stats)}
        stored = await self.store.checkpoints_with_prefix(RULE_CHECKPOINT_PREFIX)
        now = time.time()
        resolved: list[InducedRule] = []
        for key, value in stored:
            parts = key[len(RULE_CHECKPOINT_PREFIX) :].split(":", 1)
            if len(parts) != 2:
                continue
            incident_class, channel = parts
            expires = float(value.get("expires_at") or 0.0)
            base = mined.pop(
                (incident_class, channel),
                InducedRule(incident_class, channel, 0, 0, "", "accepted"),
            )
            state = "accepted" if expires > now and value.get("accepted") else "proposed"
            resolved.append(
                InducedRule(
                    incident_class=incident_class,
                    channel=channel,
                    dismissed=base.dismissed,
                    total=base.total,
                    statement=base.statement
                    or (
                        f"Hold {incident_class.replace('_', ' ')} items in the inbox "
                        "instead of interrupting?"
                    ),
                    state=state,
                    expires_at=expires or None,
                )
            )
        resolved.extend(mined.values())
        return sorted(resolved, key=lambda rule: (rule.incident_class, rule.channel))

    async def decide_rule(self, incident_class: str, channel: str, accept: bool) -> None:
        """Accept or reject a mined rule. Acceptance expires, forcing a re-judgment."""
        key = f"{RULE_CHECKPOINT_PREFIX}{incident_class}:{channel}"
        if not accept:
            await self.store.clear_checkpoint(key)
            log.info("attention rule %s rejected", key)
            return
        now = time.time()
        await self.store.set_checkpoint(
            key,
            {
                "accepted": True,
                "accepted_at": now,
                "expires_at": now + RULE_REVIEW_SECONDS,
            },
        )
        log.info("attention rule %s accepted until %.0f", key, now + RULE_REVIEW_SECONDS)

    async def _demotion_rule(self, incident_class: str) -> str | None:
        stored = await self.store.checkpoint(
            f"{RULE_CHECKPOINT_PREFIX}{incident_class}:{INTERRUPT_NOW}"
        )
        if not stored or not stored.get("accepted"):
            return None
        if float(stored.get("expires_at") or 0.0) <= time.time():
            return None
        return incident_class

    async def persist_telemetry(self) -> None:
        await self.store.set_checkpoint("attention:telemetry", self.telemetry.snapshot())

    def status(self) -> dict[str, Any]:
        loops = background.health().get("loops", [])
        running = any(
            item.get("name") == ATTENTION_LOOP and item.get("running") for item in loops
        )
        return {
            "ranked": self.ranked,
            "interrupts": self.interrupts,
            "suppressed": self.suppressed,
            "last_error": self.last_error,
            "running": running,
        }


def _fleet_summary(kind: str, label: str) -> str:
    summaries = {
        "stalled": f"{label} has been working with no output and no CPU.",
        "runaway": f"{label} is producing output outside a turn.",
        "context_pressure": f"{label} is close to the end of its context window.",
        "claim_unverified": f"{label} claimed completion without a verification run.",
        "unattended_attention": f"{label} is waiting on a human answer.",
        "port_collision": f"{label} shares a listening port with another session.",
    }
    return summaries.get(kind, f"{label} reported {kind.replace('_', ' ')}.")
