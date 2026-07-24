"""Byte-exact terminal scrollback retention.

Standalone so the PTY supervisor process can retain scrollback without
importing the daemon's session/orchestration stack.
"""

from __future__ import annotations

import builtins
from collections import deque


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

    @property
    def position(self) -> int:
        return self._written

    def bytes_since(self, position: int) -> builtins.bytes:
        retained = self.bytes()
        retained_start = self._written - len(retained)
        if position >= self._written:
            return b""
        if position <= retained_start:
            return retained
        return retained[position - retained_start :]
