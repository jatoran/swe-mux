"""Phase 5: gated auto-delivery of armed queue messages.

The only non-human caller of ``PromptQueueService.send_next``. It is not a
second delivery path (`CONTROL_PLANE_ROADMAP.md` §7.1): ordering, revision
checks, readiness, identity, constraints, and audit all stay in the typed queue
operation. What lives here is *when a send is attempted at all*, and every
bound the roadmap requires around that decision:

- **Opt-in, twice.** A master switch (config, off by default) and a per-session
  opt-in that carries an expiry and a consecutive-send cap. Standing
  authorization is what turns a bounded convenience into an unattended
  actuator, so the per-session grant dies on its own.
- **Same live run only.** The opt-in binds to the session's ``agent_run_id``;
  a replaced run disables it rather than inheriting the grant.
- **Stability, not a snapshot.** ``delivery_state`` must be ``safe``
  continuously across a window for the *same* message revision. One safe
  sample is a race; a held window is evidence.
- **Never an override.** The controller cannot pass ``confirm`` (the queue
  operation rejects it from a non-human initiator), so blocked/unknown
  readiness always means "not now" and never "anyway".
- **Fail closed and stay stopped.** A failed delivery — where the PTY write may
  or may not have landed — disables the session's opt-in and requires human
  reconciliation. It never retries blindly.
- **Emergency disable.** A persisted pause flag in SQLite, independent of the
  config file and of any provider, plus quiet hours.

Instrumentation is part of the feature, not an afterthought: the promotion
criteria for widening this capability are quantitative (`ROADMAP.md` Phase 5),
so the counts behind them are persisted here and surfaced on the policy
endpoint.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import Any

from .background_tasks import background
from .config import Config
from .prompt_queue import (
    AUTO_POLICY_GLOBAL,
    HUMAN_SENDER_KINDS,
    PromptQueueService,
    QueueError,
    schedule_status,
)

log = logging.getLogger("swe_mux.auto_delivery")

AUTO_DELIVERY_LOOP = "auto-delivery"
TICK_SECONDS = 1.0
# Expiry sweeps are cheap but not free; a queue item's expiry is a minutes-scale
# promise, so once every 30 ticks is ample.
EXPIRY_SWEEP_TICKS = 30

# Proving-period criteria for promoting auto-delivery beyond its Phase 5 bounds
# (`ROADMAP.md` Phase 5, "quantitative promotion criteria"). These gate a future
# widening decision — they do not gate the opt-in itself, which is bounded by
# construction.
PROVING_MIN_SENDS = 50
PROVING_MIN_DAYS = 14
# The fixture classes a false-safe must never appear in. Pinned by
# `tests/test_delivery_readiness_promotion.py`; named here so the criteria the
# API reports and the criteria the suite enforces are one list.
PROVING_FIXTURE_CLASSES = (
    "approval_required",
    "awaiting_user_input",
    "rate_limited",
    "subagent_activity",
    "active_operator_input",
    "run_replacement",
)

COUNTER_SENT = "auto_sent"
COUNTER_REFUSED = "auto_refused"
COUNTER_FAILED = "auto_failed"
COUNTER_UNSAFE = "unsafe_reported"
COUNTER_PROVING_SINCE = "proving_since"


def _minutes(value: str) -> int | None:
    if len(value) == 5 and value[2] == ":" and value[:2].isdigit() and value[3:].isdigit():
        return int(value[:2]) * 60 + int(value[3:])
    return None


def in_quiet_window(start: str, end: str, now: time.struct_time | None = None) -> bool:
    """Local-time quiet window, wrapping midnight — mirrors the push sender's rule."""
    first = _minutes(str(start or ""))
    last = _minutes(str(end or ""))
    if first is None or last is None or first == last:
        return False
    moment = now or time.localtime()
    current = moment.tm_hour * 60 + moment.tm_min
    if first < last:
        return first <= current < last
    return current >= first or current < last


class AutoDeliveryController:
    """The clock and the gate; the queue operation is still the only sender."""

    def __init__(
        self,
        queue: PromptQueueService,
        sessions: Any,
        config: Config,
        *,
        tick_seconds: float = TICK_SECONDS,
    ) -> None:
        self.queue = queue
        self.sessions = sessions
        self.config = config
        self._tick_seconds = tick_seconds
        # (session_id) -> (message_id, revision, first_safe_monotonic)
        self._stability: dict[str, tuple[str, int, float]] = {}
        # (session_id) -> monotonic deadline after a refused attempt
        self._backoff: dict[str, float] = {}
        self._ticks = 0
        self.last_error = ""

    # -- lifecycle ------------------------------------------------------------

    def start(self) -> None:
        background.start(AUTO_DELIVERY_LOOP, self._run)

    async def stop(self) -> None:
        await background.stop(AUTO_DELIVERY_LOOP)

    async def _run(self) -> None:
        while True:
            with background.iteration(AUTO_DELIVERY_LOOP):
                await self.tick()
            await asyncio.sleep(self._tick_seconds)

    # -- policy ---------------------------------------------------------------

    async def paused(self) -> bool:
        row = await self.queue.store.auto_policy(AUTO_POLICY_GLOBAL)
        return bool(row and row.get("paused"))

    async def set_paused(self, paused: bool, *, by: str = "user") -> dict[str, Any]:
        """The emergency disable. Persisted, instant, provider-independent."""
        return await self.queue.store.set_auto_policy(
            AUTO_POLICY_GLOBAL, paused=int(bool(paused)), updated_by=by
        )

    async def enable_session(
        self,
        session_id: str,
        *,
        ttl_minutes: int | None = None,
        max_sends: int | None = None,
        by: str = "user",
    ) -> dict[str, Any]:
        """Grant one session a bounded auto-delivery window.

        The grant records the run it was made against: an opt-in is consent for
        *this conversation*, and a replaced run has to be authorized again.
        """
        session = self.sessions.sessions.get(session_id)
        if session is None:
            raise QueueError("unknown_target", "no such session", status=404)
        record = session.record
        if record.backend not in {"claude", "codex"}:
            raise QueueError(
                "not_agent_target", "auto-delivery targets agent sessions only", status=400
            )
        if record.state in {"exited", "crashed"}:
            raise QueueError("target_ended", "the target session has ended")
        now = time.time()
        ttl = max(1, int(ttl_minutes or self.config.auto_delivery_session_ttl_minutes))
        cap = max(1, int(max_sends or self.config.auto_delivery_max_consecutive))
        policy = await self.queue.store.set_auto_policy(
            session_id,
            enabled=1,
            agent_run_id=record.agent_run_id or None,
            expires_at=now + ttl * 60,
            max_sends=cap,
            sends_used=0,
            disabled_reason=None,
            enabled_at=now,
            updated_by=by,
        )
        # A proving period starts the first time the capability is ever armed.
        counters = await self.queue.store.counters()
        if not counters.get(COUNTER_PROVING_SINCE):
            await self.queue.store.set_counter(COUNTER_PROVING_SINCE, now)
        self._stability.pop(session_id, None)
        self._backoff.pop(session_id, None)
        self.queue.events.emit_background(
            "queue_auto_policy",
            session_id=session_id,
            enabled=True,
            reason="enabled",
        )
        return policy

    async def disable_session(
        self, session_id: str, *, reason: str = "disabled by user", by: str = "user"
    ) -> dict[str, Any]:
        policy = await self.queue.store.set_auto_policy(
            session_id, enabled=0, disabled_reason=reason, updated_by=by
        )
        self._stability.pop(session_id, None)
        self._backoff.pop(session_id, None)
        self.queue.events.emit_background(
            "queue_auto_policy", session_id=session_id, enabled=False, reason=reason
        )
        return policy

    async def set_accept_agent_messages(
        self, session_id: str, accept: bool, *, by: str = "user"
    ) -> dict[str, Any]:
        """Whether `mux.notify` may stage *armed* messages at this session.

        Off (the default) an agent-authored message still arrives — as an inert
        draft the human arms. This is the receiver-side half of the A→B
        contract and is deliberately independent of auto-delivery: arming is
        authorization, auto-delivery is who presses send.
        """
        session = self.sessions.sessions.get(session_id)
        if session is None:
            raise QueueError("unknown_target", "no such session", status=404)
        return await self.queue.store.set_auto_policy(
            session_id, accept_agent_messages=int(bool(accept)), updated_by=by
        )

    async def accepts_agent_messages(self, session_id: str) -> bool:
        row = await self.queue.store.auto_policy(session_id)
        return bool(row and row.get("accept_agent_messages"))

    async def report_unsafe(self, note: str = "") -> dict[str, float]:
        """Operator review input: one confirmed bad automatic delivery.

        A single report resets the proving period and pauses auto-delivery —
        the promotion criteria require *zero* known false-safe deliveries, so a
        report is not a statistic to average away.
        """
        await self.queue.store.bump_counter(COUNTER_UNSAFE)
        await self.queue.store.set_counter(COUNTER_PROVING_SINCE, time.time())
        await self.set_paused(True, by="unsafe-report")
        log.warning("auto-delivery reported unsafe by the operator: %s", note or "(no note)")
        return await self.queue.store.counters()

    # -- status ---------------------------------------------------------------

    async def status(self) -> dict[str, Any]:
        counters = await self.queue.store.counters()
        rows = await self.queue.store.auto_policies()
        now = time.time()
        sessions: list[dict[str, Any]] = []
        for row in rows:
            session_id = str(row["session_id"])
            session = self.sessions.sessions.get(session_id)
            record = getattr(session, "record", None)
            sessions.append(
                {
                    **row,
                    "enabled": bool(row.get("enabled")),
                    "accept_agent_messages": bool(row.get("accept_agent_messages")),
                    "live": bool(record is not None and record.state not in {"exited", "crashed"}),
                    "label": getattr(record, "name", None),
                    "run_matches": bool(
                        record is not None
                        and (
                            not row.get("agent_run_id")
                            or row.get("agent_run_id") == record.agent_run_id
                        )
                    ),
                    "expires_in_s": (
                        max(0.0, float(row["expires_at"]) - now) if row.get("expires_at") else None
                    ),
                    "sends_remaining": max(
                        0, int(row.get("max_sends") or 0) - int(row.get("sends_used") or 0)
                    ),
                }
            )
        return {
            "master_enabled": bool(self.config.auto_delivery_enabled),
            "paused": await self.paused(),
            "quiet_hours": {
                "start": self.config.auto_delivery_quiet_start,
                "end": self.config.auto_delivery_quiet_end,
                "active": in_quiet_window(
                    self.config.auto_delivery_quiet_start,
                    self.config.auto_delivery_quiet_end,
                ),
            },
            "stable_seconds": float(self.config.auto_delivery_stable_seconds),
            "max_consecutive": int(self.config.auto_delivery_max_consecutive),
            "session_ttl_minutes": int(self.config.auto_delivery_session_ttl_minutes),
            "sessions": sessions,
            "counters": counters,
            "promotion": promotion_status(counters),
            "last_error": self.last_error,
        }

    # -- the loop -------------------------------------------------------------

    async def tick(self) -> list[str]:
        """One pass. Returns the message ids delivered (for tests and logs)."""
        self._ticks += 1
        delivered: list[str] = []
        if self._ticks % EXPIRY_SWEEP_TICKS == 1:
            # Expiry is enforced even when auto-delivery is off: an expired
            # message is a promise the user made about *any* delivery path.
            with contextlib.suppress(Exception):
                await self.queue.expire_due()
        if not self.config.auto_delivery_enabled:
            self._stability.clear()
            return delivered
        if await self.paused():
            self._stability.clear()
            return delivered
        quiet = in_quiet_window(
            self.config.auto_delivery_quiet_start, self.config.auto_delivery_quiet_end
        )
        for row in await self.queue.store.auto_policies():
            if not row.get("enabled"):
                continue
            session_id = str(row["session_id"])
            try:
                sent = await self._consider(session_id, row, quiet=quiet)
            except Exception as exc:  # a bad session must not stop the fleet
                self.last_error = f"{session_id}: {exc}"
                log.exception("auto-delivery pass failed for session %s", session_id)
                continue
            if sent:
                delivered.append(sent)
        return delivered

    async def _consider(
        self, session_id: str, policy: dict[str, Any], *, quiet: bool
    ) -> str | None:
        now = time.time()
        monotonic = time.monotonic()
        session = self.sessions.sessions.get(session_id)
        if session is None or session.record.state in {"exited", "crashed"}:
            await self.disable_session(
                session_id, reason="target session ended", by="controller"
            )
            return None
        record = session.record
        bound_run = policy.get("agent_run_id")
        if bound_run and record.agent_run_id and bound_run != record.agent_run_id:
            await self.disable_session(
                session_id, reason="target agent run was replaced", by="controller"
            )
            return None
        expires_at = policy.get("expires_at")
        if expires_at and now >= float(expires_at):
            await self.disable_session(
                session_id, reason="auto-delivery opt-in expired", by="controller"
            )
            return None
        max_sends = int(policy.get("max_sends") or 0)
        if max_sends and int(policy.get("sends_used") or 0) >= max_sends:
            await self.disable_session(
                session_id,
                reason=f"reached {max_sends} consecutive automatic sends",
                by="controller",
            )
            return None
        if quiet:
            return None
        deadline = self._backoff.get(session_id)
        if deadline is not None:
            if monotonic < deadline:
                return None
            self._backoff.pop(session_id, None)

        view = await self.queue.store.messages_for_target(session_id)
        head = next(
            (item for item in view["messages"] if item["state"] in ("draft", "armed", "blocked")),
            None,
        )
        if head is None or head["state"] != "armed":
            # Only an armed head is authorized. A draft was never armed, and a
            # blocked one carries a refusal the user has not resolved: both
            # need a human act before any automatic attempt.
            self._stability.pop(session_id, None)
            return None
        sender_kind = str(head.get("sender_kind") or "user")
        if sender_kind not in HUMAN_SENDER_KINDS:
            if sender_kind != "agent" or not policy.get("accept_agent_messages"):
                self._stability.pop(session_id, None)
                return None
        if schedule_status(head, now) != "due":
            self._stability.pop(session_id, None)
            return None
        bound = head.get("target_agent_run_id")
        if bound and record.agent_run_id and bound != record.agent_run_id:
            # send_next would strand it; leave that to an explicit attempt.
            self._stability.pop(session_id, None)
            return None

        evaluation = self.queue.readiness.evaluate(session)
        if str(evaluation.get("delivery_state")) != "safe":
            self._stability.pop(session_id, None)
            return None
        key = (str(head["id"]), int(head["revision"]))
        tracked = self._stability.get(session_id)
        if tracked is None or (tracked[0], tracked[1]) != key:
            self._stability[session_id] = (key[0], key[1], monotonic)
            return None
        if monotonic - tracked[2] < float(self.config.auto_delivery_stable_seconds):
            return None

        if not await self.queue.store.consume_auto_send(session_id, max_sends):
            await self.disable_session(
                session_id, reason="automatic send budget exhausted", by="controller"
            )
            return None
        self._stability.pop(session_id, None)
        try:
            await self.queue.send_next(
                str(head["id"]),
                revision=int(head["revision"]),
                # Deterministic per (message, revision): a controller restart
                # mid-send replays the recorded outcome instead of delivering
                # the same body twice.
                idempotency_key=f"auto:{head['id']}:{head['revision']}",
                confirm=False,
                initiator="auto",
            )
        except QueueError as exc:
            await self.queue.store.release_auto_send(session_id)
            if exc.code == "delivery_failed":
                await self.queue.store.bump_counter(COUNTER_FAILED)
                await self.disable_session(
                    session_id,
                    reason="a delivery failed mid-send; verify the terminal before re-enabling",
                    by="controller",
                )
                return None
            await self.queue.store.bump_counter(COUNTER_REFUSED)
            self._backoff[session_id] = monotonic + float(
                self.config.auto_delivery_refusal_backoff_seconds
            )
            self.last_error = f"{session_id}: {exc.code}"
            return None
        await self.queue.store.bump_counter(COUNTER_SENT)
        return str(head["id"])


def promotion_status(counters: dict[str, float]) -> dict[str, Any]:
    """Quantitative promotion criteria for Phase 1 shadow readiness.

    Three conditions, all of which must hold before auto-delivery is widened
    beyond its Phase 5 bounds (per-session opt-in, expiry, consecutive cap):

    1. **Zero known false-safe deliveries.** Both machine-checked — the
       readiness fixtures in `PROVING_FIXTURE_CLASSES` must never evaluate
       ``safe`` — and operator-reported: one `report_unsafe` resets the clock
       and pauses the feature.
    2. **Volume.** At least ``PROVING_MIN_SENDS`` automatic deliveries, so the
       evidence is not a handful of lucky sends.
    3. **Duration.** At least ``PROVING_MIN_DAYS`` days of operator-reviewed
       running since the capability was first armed (or since the last unsafe
       report), so the sample spans real working conditions rather than one
       afternoon.
    """
    since = float(counters.get(COUNTER_PROVING_SINCE) or 0.0)
    days = (time.time() - since) / 86400 if since else 0.0
    sends = int(counters.get(COUNTER_SENT, 0))
    unsafe = int(counters.get(COUNTER_UNSAFE, 0))
    criteria = {
        "no_false_safe": unsafe == 0,
        "min_sends": sends >= PROVING_MIN_SENDS,
        "min_days": days >= PROVING_MIN_DAYS,
    }
    return {
        "criteria": criteria,
        "met": all(criteria.values()),
        "auto_sends": sends,
        "unsafe_reports": unsafe,
        "proving_since": since or None,
        "proving_days": round(days, 2),
        "required_sends": PROVING_MIN_SENDS,
        "required_days": PROVING_MIN_DAYS,
        "fixture_classes": list(PROVING_FIXTURE_CLASSES),
    }
