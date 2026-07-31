"""Byte-exact terminal scrollback retention.

Standalone so the PTY supervisor process can retain scrollback without
importing the daemon's session/orchestration stack.
"""

from __future__ import annotations

import builtins
from collections import deque

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
        retained = self.bytes()
        if limit is None or limit <= 0 or len(retained) <= limit:
            return retained
        window = retained[-limit:]
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
        retained = self.bytes()
        retained_start = self._written - len(retained)
        if position >= self._written:
            return b""
        if position <= retained_start:
            return retained
        return retained[position - retained_start :]
