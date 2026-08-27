from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from .models import MuxEvent

log = logging.getLogger(__name__)

EventSink = Callable[[MuxEvent], Awaitable[int | None]]


class EventBus:
    def __init__(
        self,
        sink: EventSink | None = None,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._subscribers: set[asyncio.Queue[MuxEvent]] = set()
        self._sink = sink
        self._clock = clock
        self._semantic_events: dict[tuple[object, ...], MuxEvent] = {}
        self._background: set[asyncio.Task[MuxEvent]] = set()
        # A full subscriber queue drops events on the floor. That is the correct
        # backpressure policy (one slow consumer must not stall the bus), but a
        # silent drop is indistinguishable from "nothing happened" for durable
        # consumers like Tier 0 — so every drop is attributed and counted.
        self._labels: dict[int, str] = {}
        self._drops: dict[str, int] = {}
        self._last_drop_ts: dict[str, float] = {}

    def emit_background(
        self,
        event_type: str,
        *,
        session_id: str | None = None,
        source: str = "daemon",
        **payload: Any,
    ) -> asyncio.Task[MuxEvent]:
        """Persist non-critical telemetry without blocking an interactive path.

        SQLite durability can briefly queue behind transcript reconciliation. Terminal
        attach/input/detach telemetry must never inherit that latency, so callers that
        do not need a sequence number can schedule the ordinary durable emit here.
        """

        task = asyncio.create_task(
            self.emit(
                event_type,
                session_id=session_id,
                source=source,
                **payload,
            ),
            name=f"event-{event_type}",
        )
        self._background.add(task)

        def finished(done: asyncio.Task[MuxEvent]) -> None:
            self._background.discard(done)
            if done.cancelled():
                return
            try:
                done.result()
            except Exception:
                log.exception("background event persistence failed: %s", event_type)

        task.add_done_callback(finished)
        return task

    def emit_transient(
        self,
        event_type: str,
        *,
        session_id: str | None = None,
        source: str = "daemon",
        **payload: Any,
    ) -> MuxEvent:
        """Fan one event out to live subscribers **without persisting it**.

        See ``MuxEvent.transient``: the event reaches everyone currently subscribed,
        carries no sequence number, and leaves the capped `events` history untouched.
        For derived current-state fanout at a per-second cadence, where writing a row
        would evict real history rather than add to it.

        Its own method rather than a flag on `emit`, because `emit` takes its payload
        as `**kwargs`: a keyword-only `transient=` there would silently swallow any
        event that legitimately wanted a payload field of that name, and the failure
        would be a missing field rather than an error.

        Synchronous, because there is no sink to await. That also means it is safe to
        call from a path that must not yield — nothing here can be interleaved with.

        The coalescing `emit` applies to retried lifecycle events is deliberately not
        applied: that dedupe exists for unordered side channels delivering the same
        fact twice, and a transient event has no retry to collapse.
        """

        event = MuxEvent(
            self._clock(), session_id, source, event_type, payload, transient=True
        )
        for queue in tuple(self._subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                self._record_drop(queue, event)
        return event

    async def emit(
        self,
        event_type: str,
        *,
        session_id: str | None = None,
        source: str = "daemon",
        **payload: Any,
    ) -> MuxEvent:
        event = MuxEvent(self._clock(), session_id, source, event_type, payload)
        if event_type in {"turn_started", "turn_ended", "tool_use", "approval_needed"}:
            semantic = (
                session_id,
                event_type,
                payload.get("tool"),
                payload.get("kind"),
                payload.get("scope", "root"),
                payload.get("turn_id"),
            )
            previous = self._semantic_events.get(semantic)
            if previous and previous.source != source and event.ts - previous.ts < 2:
                return previous
            self._semantic_events[semantic] = event
            if len(self._semantic_events) > 2048:
                cutoff = event.ts - 10
                self._semantic_events = {
                    key: value for key, value in self._semantic_events.items() if value.ts >= cutoff
                }
        if self._sink:
            event.seq = int(await self._sink(event) or 0)
        for queue in tuple(self._subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                self._record_drop(queue, event)
        return event

    def _record_drop(self, queue: asyncio.Queue[MuxEvent], event: MuxEvent) -> None:
        label = self._labels.get(id(queue), "anonymous")
        count = self._drops.get(label, 0) + 1
        self._drops[label] = count
        self._last_drop_ts[label] = event.ts
        # First drop per subscriber is loud (it means a consumer fell behind or
        # died); afterwards the counter carries the signal without log spam.
        if count == 1 or count % 1000 == 0:
            log.warning(
                "event bus dropped %s for subscriber %r (%d dropped total)",
                event.type,
                label,
                count,
            )

    def drop_stats(self) -> dict[str, Any]:
        """Per-subscriber drop counts for diagnostics.

        Also reports live queue depth so a consumer that is merely slow can be
        told apart from one that has stopped draining entirely.
        """
        depths: dict[str, int] = {}
        for queue in self._subscribers:
            depths[self._labels.get(id(queue), "anonymous")] = queue.qsize()
        return {
            "subscribers": len(self._subscribers),
            "dropped": dict(self._drops),
            "dropped_total": sum(self._drops.values()),
            "last_drop_ts": dict(self._last_drop_ts),
            "queue_depth": depths,
        }

    def subscriber_count(self, name: str) -> int:
        """How many live subscribers registered under ``name``.

        For loops whose only consumer is a browser: a producer that costs real work
        per tick should be able to ask whether anyone is listening, rather than
        computing a fanout nobody receives. Counted by label because the daemon's
        own consumers subscribe to the same bus, so a bare subscriber count is
        never zero and answers a different question.
        """

        return sum(1 for queue in self._subscribers if self._labels.get(id(queue)) == name)

    def subscribe(self, *, name: str = "anonymous") -> asyncio.Queue[MuxEvent]:
        queue: asyncio.Queue[MuxEvent] = asyncio.Queue(maxsize=1024)
        self._subscribers.add(queue)
        self._labels[id(queue)] = name
        return queue

    def unsubscribe(self, queue: asyncio.Queue[MuxEvent]) -> None:
        self._subscribers.discard(queue)
        # Drop counts outlive the subscription on purpose: a leaked or churned
        # subscriber's losses must stay visible in diagnostics.
        self._labels.pop(id(queue), None)
