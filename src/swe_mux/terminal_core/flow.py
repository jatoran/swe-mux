"""Output credit between one terminal connection and the xterm that parses it."""

from __future__ import annotations

import asyncio

PTY_OUTPUT_HIGH_WATER_BYTES = 128 * 1024


PTY_OUTPUT_BATCH_BYTES = 32 * 1024


class PtyOutputFlow:
    """Per-browser credit based on bytes xterm has actually parsed.

    TCP/WebSocket backpressure ends when Chromium accepts a frame, not when xterm
    has parsed and painted it. Without this second boundary, a repaint-heavy TUI
    can queue megabytes in xterm and put typed echo behind seconds of old output.
    """

    def __init__(self, high_water_bytes: int = PTY_OUTPUT_HIGH_WATER_BYTES) -> None:
        self.high_water_bytes = max(1, high_water_bytes)
        self.enabled = False
        self.unacknowledged_bytes = 0
        self._credit_available = asyncio.Event()
        self._credit_available.set()

    def enable(self) -> None:
        self.enabled = True

    async def wait_for_credit(self) -> None:
        while self.enabled and self.unacknowledged_bytes >= self.high_water_bytes:
            self._credit_available.clear()
            await self._credit_available.wait()

    def sent(self, byte_count: int) -> None:
        if not self.enabled or byte_count <= 0:
            return
        self.unacknowledged_bytes += byte_count
        if self.unacknowledged_bytes >= self.high_water_bytes:
            self._credit_available.clear()

    def acknowledge(self, byte_count: int) -> None:
        if not self.enabled or byte_count <= 0:
            return
        self.unacknowledged_bytes = max(0, self.unacknowledged_bytes - byte_count)
        if self.unacknowledged_bytes < self.high_water_bytes:
            self._credit_available.set()
