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
                pass
        return event

    def subscribe(self) -> asyncio.Queue[MuxEvent]:
        queue: asyncio.Queue[MuxEvent] = asyncio.Queue(maxsize=1024)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[MuxEvent]) -> None:
        self._subscribers.discard(queue)
