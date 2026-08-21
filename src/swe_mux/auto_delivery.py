"""Phase 5: gated auto-delivery of armed queue messages.

The only non-human caller of ``PromptQueueService.send_next``. It is not a
second delivery path (`CONTROL_PLANE_ROADMAP.md` §7.1): ordering, revision
checks, readiness, identity, constraints, and audit all stay in the typed queue
operation. What lives here is *when a send is attempted at all*, and every
bound the roadmap requires around that decision:

- **Master-gated, conversation-default.** The install-wide master switch stays
  off by default. Once enabled, each live agent conversation receives a
  bounded grant automatically; a human can turn that conversation off.
- **Same live run only.** The grant binds to the session's ``agent_run_id``;
  a replacement conversation receives a fresh default grant rather than
  inheriting the old run's counters or expiry.
- **Stability, not a snapshot.** ``delivery_state`` must be ``safe``
  continuously across a window for the *same* message revision. One safe
  sample is a race; a held window is evidence.
- **Never an override.** The controller cannot pass ``confirm`` (the queue
  operation rejects it from a non-human initiator), so blocked/unknown
  readiness always means "not now" and never "anyway".
- **The consecutive cap bounds an unattended run, and clears on evidence that
  the run is not unattended.** A human send and a reply the session itself wrote
  to a peer both reset the count and restore a grant the cap switched off
  (``PromptQueueStore.credit_auto_attention``). What the cap is for is a session
  being pushed by something that never answers; a session that answers is not
  that. Volume in an agent-to-agent exchange is bounded where it belongs, by the
  per-thread and per-origin bounds in `agent_messaging.py`.
- **The idle lapse is bounded, audited, and held off by exactly one thing.** A
  grant lapses when nobody has touched the conversation for
  ``auto_delivery_session_ttl_minutes``, and that lapse records when it fired,
  after how much idleness, under which window, and how many messages it left
  waiting — because a lapse is the one disable with no act behind it, so it is
  the one nobody can look up afterwards. The single exception to the lapse is a
  session that is *mid-exchange and owed a reply*
  (``auto_delivery_reply_window_minutes``): it is bounded by its own clock and by
  the exchange's message budget, it holds off the lapse and nothing else, and
  every other gate below still decides each send.
- **Fail closed and stay stopped.** A failed delivery — where the PTY write may
  or may not have landed — disables the session's grant and requires human
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
from collections.abc import Collection
from typing import Any

from .background_tasks import background
from .config import Config
from .harness import delivers_prompts_through_pty
from .prompt_queue import (
    AUTO_POLICY_GLOBAL,
    DELIVERY_NOW,
    HUMAN_SENDER_KINDS,
    SEND_CAP_REASON,
    PromptQueueService,
    QueueError,
    delivery_mode,
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
# widening decision — they do not gate the grant itself, which is bounded by
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
# How often a grant has switched itself off for idleness. Counted rather than
# only logged because "is this install losing grants constantly" is a rate
# question, and the operator asking it is usually asking why a message sat.
COUNTER_LAPSED = "auto_lapsed"

DEFAULT_POLICY_BY = "conversation-default"
FAILED_DELIVERY_REASON = (
    "a delivery failed mid-send; verify the terminal before re-enabling"
)
# A grant that ran out while nobody was using the conversation. Distinct from
# every other disable reason because it is the only one that is *not* a decision:
# an opt-out, an exhausted send budget, and a failed delivery each record
# something that happened and stay until a human resolves it, while this records
# only that time passed. It is therefore the one reason the conversation default
# may restore on its own when the session is in use again.
LAPSED_REASON = "auto-delivery grant lapsed while the conversation was idle"
# The reason written by builds before the window was measured against idleness.
# Rows carrying it are lapses under the old rule and must be restorable too,
# otherwise every conversation the previous build disabled stays disabled
# forever and the fix appears to do nothing on the machine that needed it.
LEGACY_EXPIRED_REASON = "auto-delivery grant expired"
LAPSE_REASONS = frozenset({LAPSED_REASON, LEGACY_EXPIRED_REASON})


def is_lapse_reason(reason: Any) -> bool:
    """Whether a grant is off only because its idle window ran out."""
    return str(reason or "") in LAPSE_REASONS


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
        self._policy_lock = asyncio.Lock()
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
        """The emergency disable. Persisted, instant, provider-independent.

        Logged on every call, including no-op repeats: this flag decides whether anything
        delivers itself, so "when did automatic delivery stop, and who stopped it" has to
        be answerable from the log alone. The row records only the latest writer.
        """
        was = await self.paused()
        row = await self.queue.store.set_auto_policy(
            AUTO_POLICY_GLOBAL, paused=int(bool(paused)), updated_by=by
        )
        log.warning(
            "auto-delivery %s by %s (was %s)",
            "paused" if paused else "resumed",
            by,
            "paused" if was else "armed",
        )
        return row

    async def enable_session(
        self,
        session_id: str,
        *,
        ttl_minutes: int | None = None,
        max_sends: int | None = None,
        by: str = "user",
    ) -> dict[str, Any]:
        """Grant one session a bounded auto-delivery window."""
        async with self._policy_lock:
            return await self._enable_session_unlocked(
                session_id, ttl_minutes=ttl_minutes, max_sends=max_sends, by=by
            )

    async def _enable_session_unlocked(
        self,
        session_id: str,
        *,
        ttl_minutes: int | None = None,
        max_sends: int | None = None,
        by: str,
        accept_agent_messages: bool | None = None,
        accept_agent_interjections: bool | None = None,
    ) -> dict[str, Any]:
        """Write a grant while ``_policy_lock`` is held.

        The grant records the run it was made against. A replacement run gets
        its own default grant and starts with a fresh expiry and send budget.

        ``accept_agent_messages`` is left alone unless a caller states it. Only
        the conversation-default path does: re-arming a grant the user turned
        off and on again must not silently undo their separate decision about
        who may arm messages at this session.
        """
        session = self.sessions.sessions.get(session_id)
        if session is None:
            raise QueueError("unknown_target", "no such session", status=404)
        record = session.record
        if not delivers_prompts_through_pty(record.backend):
            raise QueueError(
                "not_agent_target", "auto-delivery targets agent sessions only", status=400
            )
        if record.state in {"exited", "crashed"}:
            raise QueueError("target_ended", "the target session has ended")
        now = time.time()
        ttl = max(1, int(ttl_minutes or self.config.auto_delivery_session_ttl_minutes))
        cap = max(1, int(max_sends or self.config.auto_delivery_max_consecutive))
        accept: dict[str, Any] = {}
        if accept_agent_messages is not None:
            accept["accept_agent_messages"] = int(bool(accept_agent_messages))
        if accept_agent_interjections is not None:
            accept["accept_agent_interjections"] = int(bool(accept_agent_interjections))
        policy = await self.queue.store.set_auto_policy(
            session_id,
            enabled=1,
            agent_run_id=record.agent_run_id or None,
            expires_at=now + ttl * 60,
            max_sends=cap,
            sends_used=0,
            disabled_reason=None,
            enabled_at=now,
            # The lapse audit describes the grant that is being replaced, so it
            # is cleared with it. Leaving it would make a working grant read as
            # one that had just stranded messages.
            disabled_at=None,
            lapse_idle_seconds=None,
            lapse_window_minutes=None,
            lapse_pending=None,
            updated_by=by,
            **accept,
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
        self,
        session_id: str,
        *,
        reason: str = "disabled by user",
        by: str = "user",
        audit: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Switch one grant off, recording when and — for a lapse — on what evidence.

        ``audit`` is written only by the lapse path. Every other disable clears
        the columns rather than leaving them: a grant the user turned off is not
        explained by the last time it happened to lapse, and a stale record is
        worse than no record, because a reader acts on it.
        """
        now = time.time()
        async with self._policy_lock:
            session = self.sessions.sessions.get(session_id)
            record = getattr(session, "record", None)
            policy = await self.queue.store.set_auto_policy(
                session_id,
                enabled=0,
                agent_run_id=getattr(record, "agent_run_id", None) or None,
                disabled_reason=reason,
                disabled_at=now,
                lapse_idle_seconds=(audit or {}).get("idle_seconds"),
                lapse_window_minutes=(audit or {}).get("window_minutes"),
                lapse_pending=(audit or {}).get("pending"),
                updated_by=by,
            )
        self._stability.pop(session_id, None)
        self._backoff.pop(session_id, None)
        self.queue.events.emit_background(
            "queue_auto_policy",
            session_id=session_id,
            enabled=False,
            reason=reason,
            **({"lapse": audit} if audit else {}),
        )
        return policy

    async def _lapse_session(
        self, session_id: str, record: Any, policy: dict[str, Any], *, now: float
    ) -> dict[str, Any]:
        """Close a grant for idleness, and record enough to explain it later.

        A lapse is the only disable with no act behind it, so it is the only one
        whose reason nobody can look up afterwards. That is exactly the state a
        stranded sender lands in: its notification armed, nothing delivered it,
        and the target's grant says only that it "lapsed while the conversation
        was idle" — not when, not after how long, not under which window, and not
        how many messages it left waiting. All four are cheap here and
        unrecoverable later.
        """
        window_minutes = max(1, int(self.config.auto_delivery_session_ttl_minutes))
        last_seen = max(
            float(getattr(record, "last_activity_ts", 0.0) or 0.0),
            float(policy.get("enabled_at") or 0.0),
        )
        idle_seconds = max(0.0, now - last_seen) if last_seen else None
        pending = await self.queue.store.pending_message_count(session_id)
        await self.queue.store.bump_counter(COUNTER_LAPSED)
        audit = {
            "at": now,
            "idle_seconds": idle_seconds,
            "window_minutes": float(window_minutes),
            "pending": int(pending),
        }
        # WARNING rather than INFO precisely because of the pending count: a
        # lapse with messages waiting is a stalled delivery, and the log is where
        # an operator looks when a peer went quiet.
        log.warning(
            "auto-delivery grant lapsed session=%s idle=%ss window=%smin pending=%d",
            session_id,
            round(idle_seconds) if idle_seconds is not None else "unknown",
            window_minutes,
            pending,
        )
        return await self.disable_session(
            session_id, reason=LAPSED_REASON, by="controller", audit=audit
        )

    @staticmethod
    def lapse_record(row: dict[str, Any] | None) -> dict[str, Any] | None:
        """The lapse audit on one policy row, or None when it is not a lapse.

        Absent fields stay absent. A row written before the audit existed lapsed
        without the evidence being kept, and inventing a zero for it would read
        as "lapsed the instant it was granted".
        """
        if row is None or row.get("enabled"):
            return None
        if not is_lapse_reason(row.get("disabled_reason")):
            return None
        return {
            "at": row.get("disabled_at"),
            "idle_seconds": row.get("lapse_idle_seconds"),
            "window_minutes": row.get("lapse_window_minutes"),
            "pending": row.get("lapse_pending"),
        }

    async def set_accept_agent_messages(
        self, session_id: str, accept: bool, *, by: str = "user"
    ) -> dict[str, Any]:
        """Whether `mux.notify` may stage *armed* messages at this session.

        On by default for a live agent conversation, alongside the rest of the
        default grant. Turned off, an agent-authored message still arrives — as
        an inert draft the human arms — and the opt-out holds for that run.
        This is the receiver-side half of the A→B contract and is deliberately
        independent of auto-delivery: arming is authorization, auto-delivery is
        who presses send, and the install master switch still gates the latter.
        """
        session = self.sessions.sessions.get(session_id)
        if session is None:
            raise QueueError("unknown_target", "no such session", status=404)
        async with self._policy_lock:
            return await self.queue.store.set_auto_policy(
                session_id, accept_agent_messages=int(bool(accept)), updated_by=by
            )

    async def accepts_agent_messages(self, session_id: str) -> bool:
        row = await self.queue.store.auto_policy(session_id)
        return bool(row and row.get("accept_agent_messages"))

    async def accepts_agent_interjections(self, session_id: str) -> bool:
        """Whether a peer may have a message written into this session's turn.

        On by default for a live agent conversation, alongside the rest of the
        per-run grant, and independent of the other two switches: arming decides
        whether a message counts as authorized, auto-delivery decides who presses
        send, and this decides whether send may happen while a turn is running.
        Turning any one of them off never rewrites another.
        """
        row = await self.queue.store.auto_policy(session_id)
        return bool(row and row.get("accept_agent_interjections"))

    async def set_accept_agent_interjections(
        self, session_id: str, accept: bool, *, by: str = "user"
    ) -> dict[str, Any]:
        session = self.sessions.sessions.get(session_id)
        if session is None:
            raise QueueError("unknown_target", "no such session", status=404)
        async with self._policy_lock:
            return await self.queue.store.set_auto_policy(
                session_id, accept_agent_interjections=int(bool(accept)), updated_by=by
            )

    # -- the bounded reply window ---------------------------------------------

    async def reply_windows(
        self, session_ids: Collection[str]
    ) -> dict[str, dict[str, Any]]:
        """Which of these sessions are mid-exchange and owed an answer.

        The one thing that holds off the idle lapse, and it holds off *only* the
        lapse. A session whose own message was **delivered** to a peer inside
        ``auto_delivery_reply_window_minutes`` is not an untouched conversation:
        it is the half of a bounded exchange that is waiting. Losing the grant
        there is what strands the answer, which is the failure this exists for.

        Three things keep it from being a widening:

        - It is evidence, not authority. Every other gate — the master switch,
          the emergency pause, quiet hours, head-of-line order, the stability
          window, delivery readiness, the consecutive-send cap — is unchanged and
          still decides each send.
        - It is capped by the exchange's own budget. A thread that has spent
          ``agent_message_max_thread_turns`` opens no window, so the window can
          never outlive the conversation that justifies it.
        - It expires on the clock from the moment the message landed, not from
          the moment anyone looks.

        Staging a message opens nothing: an armed message nothing ever delivered
        is the symptom, not the evidence.
        """
        ids = [str(session_id) for session_id in session_ids]
        minutes = int(self.config.auto_delivery_reply_window_minutes or 0)
        if minutes <= 0 or not ids:
            return {}
        span = minutes * 60
        now = time.time()
        found = await self.queue.store.open_reply_windows(ids, now - span)
        max_turns = int(self.config.agent_message_max_thread_turns)
        windows: dict[str, dict[str, Any]] = {}
        for session_id, entry in found.items():
            thread_id = str(entry.get("thread_id") or "")
            if not thread_id:
                # A row from before the thread model carries no budget to be
                # capped by, so it earns no window rather than an unbounded one.
                continue
            turns = await self.queue.store.thread_message_count(thread_id)
            if turns >= max_turns:
                continue
            windows[session_id] = {
                **entry,
                "expires_at": float(entry.get("sent_at") or 0.0) + span,
                "window_minutes": minutes,
                "thread_messages_used": turns,
                "thread_messages_limit": max_turns,
            }
        return windows

    async def outlook(self, session_id: str) -> dict[str, Any]:
        """Whether anything will press send at this session, and what stops it.

        Written for the *sender* of an agent message. An armed message that
        nothing can deliver is indistinguishable, from the sender's side, from
        one that simply has not been read yet — so an agent whose peer's grant
        is off goes quiet instead of reporting the stall. This is the fact that
        makes the difference sayable: it names the install-wide brakes and the
        target's own grant, and it is derived rather than stored.
        """
        row = await self.queue.store.auto_policy(session_id)
        master = bool(self.config.auto_delivery_enabled)
        paused = await self.paused()
        quiet = in_quiet_window(
            self.config.auto_delivery_quiet_start, self.config.auto_delivery_quiet_end
        )
        enabled = bool(row and row.get("enabled"))
        accepts = bool(row and row.get("accept_agent_messages"))
        blocked_by: str | None = None
        if not master:
            blocked_by = "auto-delivery is switched off for this install"
        elif paused:
            blocked_by = "auto-delivery is paused install-wide"
        elif quiet:
            blocked_by = "quiet hours are active"
        elif row is None:
            # A live agent run gets its grant on the next controller tick, so
            # this is a sub-second window rather than a standing state. Worded so
            # it cannot be read as a refusal.
            blocked_by = "the target has no auto-delivery grant yet"
        elif not enabled:
            blocked_by = str(row.get("disabled_reason") or "the target's grant is off")
        elif not accepts:
            blocked_by = "the target is not accepting agent-authored messages armed"
        result: dict[str, Any] = {
            "auto_delivery": blocked_by is None,
            "blocked_by": blocked_by,
            "sends_remaining": (
                max(0, int(row.get("max_sends") or 0) - int(row.get("sends_used") or 0))
                if row
                else 0
            ),
        }
        # A lapse is the one blocked_by a sender can neither act on nor explain
        # from the sentence alone, so it carries its audit: when it happened,
        # after how much idleness, under what window, and how many messages were
        # already waiting when it fired.
        lapse = self.lapse_record(row)
        if lapse is not None:
            result["lapse"] = lapse
        return result

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

    async def _policies_with_conversation_defaults(self) -> list[dict[str, Any]]:
        """Materialize the default bounded grant for each live agent run.

        A row bound to the current run is authoritative, including an explicit
        opt-out, expiry, or exhausted budget. A new run starts enabled again,
        and accepting agent-authored messages armed comes back with it — both
        are per-run defaults, so an opt-out is scoped to the run that made it.
        The only cross-run hold is an ambiguous failed delivery: that state
        still requires the promised human verification and manual re-enable.
        """
        async with self._policy_lock:
            # Restricted to live sessions: only they can be delivered to, and policy
            # rows are never deleted (opt-outs and failed-delivery holds must
            # survive), so the unfiltered table grows by one row per session ever
            # granted and this runs every tick.
            live_ids = [str(session_id) for session_id in self.sessions.sessions]
            rows = await self.queue.store.auto_policies(live_ids)
            by_session = {str(row["session_id"]): row for row in rows}
            # Only lapsed rows can be held open by an exchange, so only they are
            # worth a query — this runs on the one-second tick, and an install
            # with nothing lapsed pays nothing for the feature.
            lapsed_ids = [
                str(row["session_id"])
                for row in rows
                if not row.get("enabled") and is_lapse_reason(row.get("disabled_reason"))
            ]
            windows = await self.reply_windows(lapsed_ids) if lapsed_ids else {}
            changed = False
            for session_id, session in self.sessions.sessions.items():
                record = getattr(session, "record", None)
                run_id = str(getattr(record, "agent_run_id", "") or "")
                if (
                    record is None
                    or not delivers_prompts_through_pty(record.backend)
                    or record.state in {"exited", "crashed"}
                    or not run_id
                ):
                    continue
                row = by_session.get(str(session_id))
                same_run = row is not None and str(row.get("agent_run_id") or "") == run_id
                if same_run:
                    # One exception to "a row bound to this run is authoritative":
                    # a grant that merely *lapsed* while the conversation was idle
                    # recorded no decision, so the conversation default comes back
                    # when the conversation does. An opt-out and a failed delivery
                    # stay until a human resolves them. An exhausted send budget
                    # is cleared by evidence rather than by time, so it is not
                    # restored here either — `credit_auto_attention` clears it at
                    # the moment the evidence arrives.
                    assert row is not None
                    if not self._restorable(
                        row,
                        record,
                        now=time.time(),
                        reply_window=windows.get(str(session_id)),
                    ):
                        continue
                elif row is not None and row.get("disabled_reason") == FAILED_DELIVERY_REASON:
                    continue
                if row is not None and not row.get("agent_run_id") and row.get("disabled_reason"):
                    # A user can opt out during startup before run identity is
                    # available. Do not immediately undo that explicit act.
                    continue
                by_session[str(session_id)] = await self._enable_session_unlocked(
                    str(session_id),
                    by=DEFAULT_POLICY_BY,
                    # Restoring a lapsed grant on the *same* run must not also
                    # restore a setting the user changed during it; only a fresh
                    # run gets the accept-agent-messages default with it.
                    accept_agent_messages=None if same_run else True,
                    accept_agent_interjections=None if same_run else True,
                )
                changed = True
            if changed:
                return await self.queue.store.auto_policies(live_ids)
            return rows

    async def status(self) -> dict[str, Any]:
        rows = await self._policies_with_conversation_defaults()
        counters = await self.queue.store.counters()
        now = time.time()
        # Read here rather than on the tick: this is the browser's endpoint, and
        # a grant being *held open* by an exchange is the thing an operator
        # cannot otherwise tell from a grant that is simply fresh.
        windows = await self.reply_windows(
            [str(row["session_id"]) for row in rows if row.get("enabled")]
        )
        sessions: list[dict[str, Any]] = []
        for row in rows:
            session_id = str(row["session_id"])
            session = self.sessions.sessions.get(session_id)
            record = getattr(session, "record", None)
            sessions.append(
                {
                    **row,
                    "lapse": self.lapse_record(row),
                    "reply_window": windows.get(session_id),
                    "enabled": bool(row.get("enabled")),
                    "accept_agent_messages": bool(row.get("accept_agent_messages")),
                    "accept_agent_interjections": bool(
                        row.get("accept_agent_interjections")
                    ),
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
            "reply_window_minutes": int(self.config.auto_delivery_reply_window_minutes),
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
        # Materialized even when the master switch is off — the per-session default
        # must exist for the UI to show before the user enables delivery (asserted by
        # test_master_switch_is_required_but_agent_conversations_default_on). The
        # fetch stays affordable per one-second tick because it is restricted to live
        # sessions rather than every row the table has ever accumulated.
        rows = await self._policies_with_conversation_defaults()
        if not self.config.auto_delivery_enabled:
            self._stability.clear()
            return delivered
        if await self.paused():
            self._stability.clear()
            return delivered
        quiet = in_quiet_window(
            self.config.auto_delivery_quiet_start, self.config.auto_delivery_quiet_end
        )
        for row in rows:
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

    def _restorable(
        self,
        row: dict[str, Any],
        record: Any,
        *,
        now: float,
        reply_window: dict[str, Any] | None = None,
    ) -> bool:
        """True for a lapsed grant that is live again — by activity or by exchange.

        Two facts restore it, and both are evidence rather than a decision: the
        conversation was touched inside the idle window, or it is mid-exchange
        and owed a reply. Every other disabled state records something that
        happened and stays until a human clears it.
        """
        if row.get("enabled"):
            return False
        if not is_lapse_reason(row.get("disabled_reason")):
            return False
        if reply_window is not None:
            return True
        return now < self._idle_deadline(record, {"enabled_at": 0.0})

    def _idle_deadline(self, record: Any, policy: dict[str, Any]) -> float:
        """When this grant lapses if the conversation stays untouched from now.

        Derived from the session's own last activity so an active conversation
        keeps its standing permission and a forgotten one loses it.
        """
        ttl = max(1, int(self.config.auto_delivery_session_ttl_minutes)) * 60
        last_activity = float(getattr(record, "last_activity_ts", 0.0) or 0.0)
        enabled_at = float(policy.get("enabled_at") or 0.0)
        # A grant written before the session has produced any activity still gets
        # its full window, rather than lapsing on the next tick.
        return max(last_activity, enabled_at) + ttl

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
        # The grant is bounded by *idleness*, not by the conversation's age.
        # Measuring it from the moment the grant was written meant a session
        # older than the TTL lost auto-delivery permanently while actively in
        # use — observed live 2026-08-13 with every session in the fleet reading
        # `grant expired` at `sends_used: 0`, so an agent-authored message
        # arrived armed and then sat there forever. What the bound is for is a
        # standing permission on a conversation nobody is watching; that is what
        # it now measures.
        deadline = self._idle_deadline(record, policy)
        if now >= deadline:
            # One exception, and only one: a session that is mid-exchange and
            # owed a reply is not an untouched conversation. The window is
            # bounded by its own clock and by the exchange's message budget, and
            # it holds off *this* check alone — everything below still runs.
            window = (await self.reply_windows([session_id])).get(session_id)
            if window is None:
                await self._lapse_session(session_id, record, policy, now=now)
                return None
            deadline = max(deadline, float(window["expires_at"]))
        expires_at = policy.get("expires_at")
        if expires_at and now >= float(expires_at):
            # Still in use, so the window moves with the conversation. Written at
            # most once per TTL per active session rather than on every tick.
            await self.queue.store.set_auto_policy(
                session_id, expires_at=deadline, updated_by="controller"
            )
            policy = {**policy, "expires_at": deadline}
        max_sends = int(policy.get("max_sends") or 0)
        if max_sends and int(policy.get("sends_used") or 0) >= max_sends:
            await self.disable_session(
                session_id,
                reason=SEND_CAP_REASON,
                by="controller",
            )
            return None
        if quiet:
            return None
        backoff_until = self._backoff.get(session_id)
        if backoff_until is not None:
            if monotonic < backoff_until:
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
            # An item that asked to land mid-turn is authorized by the readiness
            # tracker's separate, strictly narrower predicate. Every gate above
            # still applies to it — the master switch, the grant, the run
            # binding, the head-of-line rule, the consecutive cap, quiet hours,
            # the back-off. All this adds is that "the target is working" stops
            # being the thing that ends the pass, and only for an item whose
            # sender was authorized to ask (`agent_messaging.py`).
            if not (
                self.config.agent_interject_enabled
                and policy.get("accept_agent_interjections")
                and delivery_mode(head) == DELIVERY_NOW
                and str(evaluation.get("interject_state")) == "safe"
            ):
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
            await self.disable_session(session_id, reason=SEND_CAP_REASON, by="controller")
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
                    reason=FAILED_DELIVERY_REASON,
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
    beyond its Phase 5 bounds (master switch, grant expiry, consecutive cap):

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
