from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any

from .models import MuxEvent

EventSink = Callable[[MuxEvent], Awaitable[int | None]]


class EventBus:
    def __init__(self, sink: EventSink | None = None) -> None:
        self._subscribers: set[asyncio.Queue[MuxEvent]] = set()
        self._sink = sink
        self._semantic_events: dict[tuple[object, ...], MuxEvent] = {}

    async def emit(
        self,
        event_type: str,
        *,
        session_id: str | None = None,
        source: str = "daemon",
        **payload: Any,
    ) -> MuxEvent:
        event = MuxEvent(time.time(), session_id, source, event_type, payload)
        if event_type in {"turn_started", "turn_ended", "tool_use", "approval_needed"}:
            semantic = (
                session_id,
                event_type,
                payload.get("tool"),
                payload.get("kind"),
                payload.get("detail"),
            )
            previous = self._semantic_events.get(semantic)
            if previous and previous.source != source and event.ts - previous.ts < 2:
                return previous
            self._semantic_events[semantic] = event
            if len(self._semantic_events) > 2048:
                cutoff = event.ts - 10
                self._semantic_events = {
                    key: value
                    for key, value in self._semantic_events.items()
                    if value.ts >= cutoff
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
