"""Announce delivery-readiness changes as they happen.

`delivery_readiness.py` classifies evidence; nothing in it pushes. Every surface
that displays a verdict therefore read one at fetch time and kept it until some
*other* fact happened to trigger a refresh, which left three different staleness
regimes on one line of UI:

- Lifecycle reasons (`root_agent_working`, `awaiting_*`, `session_ended`) ride
  `state_changed`, which the browser already treats as a reason to re-read the
  fleet. Those were live.
- Composer and screen reasons (`terminal_input_after_completion`,
  `operator_recently_typed`, `screen_not_at_agent_prompt`) turn on `terminal_input`
  and `terminal_mode_changed` — both deliberately excluded from fleet refresh,
  because they arrive at keystroke rate and refetching five endpoints per keypress
  would contend with terminal I/O. Those were stale for up to the browser's
  sixty-second safety poll.
- The clock-driven transitions have no event at all and never will: `operator_quiet`
  becoming true is the *absence* of typing, `readiness_debounce_pending` and
  `lifecycle_evidence_stale` are thresholds crossing. Nothing announces those, so a
  session that became deliverable while you watched it kept reading blocked.

This loop closes all three with one mechanism, and the third is why it has to be a
loop rather than another event subscription.

Three properties make it affordable, and each is load-bearing:

- **Edge-triggered.** A tick emits only when a session's `(state, reasons)` tuple
  differs from the last tick's. An unchanged fleet emits nothing.
- **Transient.** The frames never enter the durable `events` table (`MuxEvent.transient`).
  That table is capped at the newest 100k rows, so a per-second event type would
  evict the git-provenance and incident-forensics history the window exists to hold.
- **Gated on a listener.** The only consumer is a browser. With no `/events` client
  connected the tick returns immediately, so a headless daemon pays nothing.

It is also **read-only against the tracker** (`adopt=False`). `evaluate` mutates —
it fills lifecycle gaps and snapshots the live screen as `screen_at_completion` —
so a watcher on a timer would make those adoptions fire at the earliest legal
instant rather than at the operator's first GET or send, and could remember a
Claude session as having completed on the normal screen before it wrote `?1049h`.
An observer must not be able to change the verdict it observes.

The scan it does pay for is not additional: `_pty_state` writes the shared
snapshot cache, so `GET /api/sessions` reuses this tick's classification instead
of rescanning every terminal itself.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from .background_tasks import background
from .delivery_readiness import DeliveryReadinessTracker
from .event_bus import EventBus
from .prompt_queue import PromptQueueService, delivery_summary

log = logging.getLogger(__name__)

READINESS_WATCH_LOOP = "readiness-watch"

#: One second, matching the auto-delivery controller. Readiness is a thing a
#: person reads while deciding whether to interrupt an agent, so the useful
#: resolution is "before I finish looking at it", not "eventually".
TICK_SECONDS = 1.0

#: The `bus.subscribe` label the `/events` websocket registers under. Counting by
#: label rather than counting subscribers is the point: the daemon's own consumers
#: never unsubscribe, so a bare count is never zero.
BROWSER_SUBSCRIBER = "events-ws"

EVENT_TYPE = "delivery_readiness_changed"

#: Sessions past these states cannot become deliverable again.
_TERMINAL_STATES = frozenset({"exited", "crashed"})


class ReadinessWatcher:
    """Watch delivery readiness for the sessions a surface can be reading."""

    def __init__(
        self,
        sessions: Any,
        readiness: DeliveryReadinessTracker,
        queue: PromptQueueService,
        events: EventBus,
        *,
        tick_seconds: float = TICK_SECONDS,
    ) -> None:
        self.sessions = sessions
        self.readiness = readiness
        self.queue = queue
        self.events = events
        self._tick_seconds = tick_seconds
        # session_id -> (state, reasons tuple). The *last announced* verdict, which
        # is what "changed" is measured against.
        self._last: dict[str, tuple[str, tuple[str, ...]]] = {}

    # -- lifecycle ------------------------------------------------------------

    def start(self) -> None:
        background.start(READINESS_WATCH_LOOP, self._run)

    async def stop(self) -> None:
        await background.stop(READINESS_WATCH_LOOP)

    async def _run(self) -> None:
        while True:
            with background.iteration(READINESS_WATCH_LOOP):
                await self.tick()
            # Outside the guard on purpose: `background.iteration` times wall clock
            # including awaits, so a loop that sleeps inside it reports its idle
            # time as its own cost and lands at the top of `costliest` doing nothing.
            await asyncio.sleep(self._tick_seconds)

    # -- the pass -------------------------------------------------------------

    async def _watched_session_ids(self) -> set[str]:
        """Sessions whose readiness some surface can currently be displaying.

        Two populations, and they answer two different questions. A session with an
        **attached terminal** is one somebody is looking at, which is where the
        Queue tab sits and where the decision to interrupt is actually made. A
        session with **something pending in its queue** is one whose readiness is
        the thing being waited on, whether or not anyone has its pane open — an
        armed message that cannot be delivered is exactly the state a person opens
        the queue to understand.

        Everything else is deliberately excluded rather than merely deprioritized:
        classification measured 2.1 ms on a full 32 KiB tail, so following a fleet
        nobody is reading would spend real event-loop time on announcements with no
        recipient. Those sessions still report current readiness — `GET /api/sessions`
        recomputes on demand — they just do not stream it.
        """

        watched: set[str] = set()
        for session_id, session in self.sessions.sessions.items():
            if session.record.state in _TERMINAL_STATES:
                continue
            if getattr(session, "subscribers", None):
                watched.add(str(session_id))
        try:
            for row in await self.queue.store.summary():
                if int(row.get("pending") or 0) > 0:
                    watched.add(str(row["target_session_id"]))
        except Exception:  # noqa: BLE001 - a queue read must not stop the watch
            log.debug("readiness watch could not read queue targets", exc_info=True)
        return watched

    async def tick(self) -> list[str]:
        """One pass. Returns the session ids announced (for tests and logs)."""

        if not self.events.subscriber_count(BROWSER_SUBSCRIBER):
            # Nobody is listening. Forget the baseline too: the next client to
            # connect loads authoritative readiness over REST, and replaying a
            # backlog of changes it never saw would be noise at best and, for a
            # verdict that has since changed again, wrong.
            self._last.clear()
            return []

        watched = await self._watched_session_ids()
        # Sessions that ended, were reaped, or fell out of scope must not keep a
        # remembered verdict — re-entering scope should re-baseline, not "change".
        for session_id in tuple(self._last):
            if session_id not in watched:
                del self._last[session_id]

        announced: list[str] = []
        now = time.time()
        for session_id in watched:
            session = self.sessions.sessions.get(session_id)
            if session is None:
                continue
            evaluation = self.readiness.evaluate(
                session,
                # Never inflate the shadow-metric distribution behind the Phase 5
                # promotion argument with watcher-driven evaluations: those counters
                # are meant to describe delivery attempts, and this is not one.
                record_metrics=False,
                # Fresh, because this loop *is* the liveness. The scan it pays for
                # populates the snapshot cache that `GET /api/sessions` reads.
                snapshot_pty_cache_seconds=0.0,
                adopt=False,
            )
            state = str(evaluation["delivery_state"])
            reasons = tuple(str(item) for item in evaluation.get("reasons") or ())
            previous = self._last.get(session_id)
            self._last[session_id] = (state, reasons)
            if previous is None:
                # First sighting establishes the baseline silently. The client's
                # REST load already carries this verdict; announcing it again would
                # make every newly watched session emit a spurious "change".
                continue
            if previous == (state, reasons):
                continue
            self.events.emit_transient(
                EVENT_TYPE,
                session_id=session_id,
                readiness=delivery_summary(session.record, evaluation, observed_at=now),
            )
            announced.append(session_id)
        return announced
