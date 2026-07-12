from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any

from .models import MuxEvent

EventSink = Callable[[MuxEvent], Awaitable[None]]


class EventBus:
    def __init__(self, sink: EventSink | None = None) -> None:
        self._subscribers: set[asyncio.Queue[MuxEvent]] = set()
        self._sink = sink

    async def emit(
        self,
        event_type: str,
        *,
        session_id: str | None = None,
        source: str = "daemon",
        **payload: Any,
    ) -> MuxEvent:
        event = MuxEvent(time.time(), session_id, source, event_type, payload)
        if self._sink:
            await self._sink(event)
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
