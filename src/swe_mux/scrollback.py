"""Byte-exact terminal scrollback retention.

Standalone so the PTY supervisor process can retain scrollback without
importing the daemon's session/orchestration stack.
"""

from __future__ import annotations

import builtins
from collections import deque

# What every screen classifier reads. Enough to hold a full redraw of a tall
# terminal plus its escape sequences, and small enough that reading it is free
# next to the megabytes of retention behind it.
SCREEN_TAIL_BYTES = 8192
# How far past a trim point to look for a line boundary. Cutting the *start* of a
# retained stream can land inside an escape sequence, which the client would
# render as literal garbage on its first line; resuming after the next newline
# costs at most this much history and cannot start mid-sequence. Sized for a wide
# terminal's longest plausible single line rather than for the trim itself.
TAIL_ALIGN_LOOKAHEAD = 4096


class ScrollbackBuffer:
    def __init__(self, max_bytes: int) -> None:
        self.max_bytes = max_bytes
        self._chunks: deque[bytes] = deque()
        self._size = 0
        self._written = 0

    def append(self, data: bytes) -> None:
        self._written += len(data)
        if self.max_bytes <= 0:
            self._chunks.clear()
            self._size = 0
            return
        if len(data) >= self.max_bytes:
            self._chunks.clear()
            self._chunks.append(data[-self.max_bytes :])
            self._size = self.max_bytes
            return
        self._chunks.append(data)
        self._size += len(data)
        excess = self._size - self.max_bytes
        while excess > 0 and self._chunks:
            first = self._chunks[0]
            if len(first) <= excess:
                self._chunks.popleft()
                self._size -= len(first)
                excess -= len(first)
            else:
                self._chunks[0] = first[excess:]
                self._size -= excess
                excess = 0

    def seed(self, data: bytes, position: int) -> None:
        """Adopt retained bytes from an authoritative buffer in another process.

        ``position`` is the total number of bytes ever written to that buffer,
        so cursors taken against it (detection scans, ``bytes_since``) stay
        consistent after a daemon reattach.
        """
        self._chunks.clear()
        self._size = 0
        self._written = 0
        self.append(data)
        self._written = max(position, len(data))

    def bytes(self) -> bytes:
        return b"".join(self._chunks)

    def tail_bytes(self, count: int) -> builtins.bytes:
        """The newest ``count`` bytes, without materializing the whole buffer.

        Every caller that reads this buffer wants its *end* — the screen classifier
        reads 8 KiB, the replay budget reads a few hundred, nested-agent detection
        reads what arrived since its cursor — and each was reaching it through
        ``bytes()``, which joins the entire retention into one new object first.

        That is not a micro-optimization at this size. The state watchdog reads the
        screen for every agent session twice a pass on a 5-second loop, so a fleet of
        full 5 MiB buffers was allocating and copying tens of megabytes a second to
        look at 8 KiB — and it is worst exactly where the buffers are fullest, which
        is Codex, since it runs with `alternate_screen=never` and puts its whole
        transcript in scrollback rather than repainting one screen.

        Walking the chunk deque from the right touches only the chunks it needs.
        """
        if count <= 0:
            return b""
        if count >= self._size:
            return self.bytes()
        collected: list[builtins.bytes] = []
        remaining = count
        for chunk in reversed(self._chunks):
            if len(chunk) >= remaining:
                collected.append(chunk[len(chunk) - remaining :])
                break
            collected.append(chunk)
            remaining -= len(chunk)
        collected.reverse()
        return b"".join(collected)

    def tail(self, limit: int | None) -> builtins.bytes:
        """The newest ``limit`` bytes, trimmed to a line boundary.

        Retention and *replay* are separate budgets. The daemon keeps a large
        exact buffer because scrollback is history the user scrolls back through;
        a client attaching has to parse every byte of whatever it is handed
        before it can show anything, and xterm deliberately time-slices that work
        across render frames — so a full-buffer replay is watched happening. The
        cost is not uniform either: an alternate-screen TUI's bytes repaint one
        fixed screen, while a CLI in raw scrollback mode (how mux launches Codex,
        `codex_tui.py`) spends them on real lines that each allocate and scroll.

        ``limit=None`` returns everything, for callers that want exact retention
        rather than a replay budget.
        """
        if limit is None or limit <= 0 or self._size <= limit:
            return self.bytes()
        window = self.tail_bytes(limit)
        boundary = window.find(b"\n", 0, TAIL_ALIGN_LOOKAHEAD)
        return window[boundary + 1 :] if boundary >= 0 else window

    @property
    def position(self) -> int:
        return self._written

    @property
    def size(self) -> int:
        """Bytes currently retained, without materializing them."""
        return self._size

    def bytes_since(self, position: int) -> builtins.bytes:
        if position >= self._written:
            return b""
        if position <= self._written - self._size:
            return self.bytes()
        return self.tail_bytes(self._written - position)
