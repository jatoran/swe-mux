"""A real supervisor listener with its pseudoterminals replaced by queues.

`test_pty_supervisor.py` needs a real ConPTY and is Windows-only for it; the wire
behaviour of the supervisor - what it sends whom, in what order, and what it
waits on - needs none of that. These helpers run a real `SupervisorServer` on an
OS-allocated loopback port, put sessions into it whose "child" is a bounded queue
the test feeds, and connect raw clients that read only when the test reads, so a
"stalled" client is a real socket that nothing drains.
"""

from __future__ import annotations

import asyncio
import contextlib
import socket
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from swe_mux import supervisor as supervisor_module
from swe_mux.scrollback import ScrollbackBuffer
from swe_mux.supervisor import (
    PROTOCOL_VERSION,
    Connection,
    SupervisedSession,
    SupervisorServer,
    encode_frame,
    read_frame,
)
from tests.support.settle import until

__all__ = [
    "CHUNK",
    "Client",
    "FakeReaper",
    "Running",
    "StreamHost",
    "add_session",
    "chunk",
    "drained",
    "open_supervisor",
    "pour",
]

CHUNK = 16 * 1024
REPLY_SECONDS = 10.0


class FakeReaper:
    def __init__(self) -> None:
        self.closed = False

    def assign(self, pid: int) -> None:
        del pid

    def create_child(self) -> FakeReaper:
        return FakeReaper()

    def process_ids(self) -> list[int]:
        return []

    def close(self) -> None:
        self.closed = True


class StreamHost:
    """What the supervisor reads from a host: a bounded output queue, size and liveness."""

    def __init__(self, queue_size: int = 1024) -> None:
        self.output_queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=queue_size)
        self.pid = 4242
        self.cols, self.rows = 120, 30
        self.reaper_assignment = "fake"
        self.read_errors = 0
        self.last_read_error: str | None = None
        self.last_read_error_at: float | None = None
        self.graceful_exit = "exit\r"
        self.alive = True
        self.writes: list[bytes] = []
        self.stopped = False
        self.released = False

    def write(self, data: bytes) -> None:
        self.writes.append(bytes(data))

    def resize(self, cols: int, rows: int) -> None:
        self.cols, self.rows = max(2, cols), max(1, rows)

    def isalive(self) -> bool:
        return self.alive

    def exit_status(self) -> int | None:
        return None if self.alive else 0

    def stop(self, *, graceful: bool = True, timeout: float = 2.0) -> None:
        del graceful, timeout
        self.stopped = True
        self.alive = False

    def release(self) -> None:
        self.released = True


class Client:
    """One raw supervisor connection that reads only when the test reads."""

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.reader = reader
        self.writer = writer
        self.frames: list[tuple[dict[str, Any], bytes]] = []
        self._next_id = 1

    async def request(
        self, header: dict[str, Any], payload: bytes = b""
    ) -> tuple[dict[str, Any], bytes]:
        """Send a request and read until its reply, keeping every frame before it."""
        request_id = self._next_id
        self._next_id += 1
        self.writer.write(encode_frame({**header, "id": request_id}, payload))
        await self.writer.drain()
        while True:
            frame, body = await asyncio.wait_for(read_frame(self.reader), timeout=REPLY_SECONDS)
            if frame.get("id") == request_id:
                return frame, body
            self.frames.append((frame, body))

    async def notify(self, header: dict[str, Any], payload: bytes = b"") -> None:
        self.writer.write(encode_frame(header, payload))
        await self.writer.drain()

    def output(self, sid: str) -> bytes:
        return b"".join(
            body
            for frame, body in self.frames
            if frame.get("t") == "output" and frame["sid"] == sid
        )

    def kinds(self, sid: str) -> list[str]:
        return [str(frame.get("t")) for frame, _ in self.frames if frame.get("sid") == sid]

    def unsolicited(self, kind: str) -> list[dict[str, Any]]:
        return [frame for frame, _ in self.frames if frame.get("t") == kind]

    async def close(self) -> None:
        self.writer.close()
        with contextlib.suppress(Exception):
            await self.writer.wait_closed()


@dataclass
class Running:
    server: SupervisorServer
    port: int
    clients: list[Client] = field(default_factory=list)

    async def connect(
        self,
        *,
        capabilities: tuple[str, ...] = (),
        stalls: bool = False,
        **hello_fields: Any,
    ) -> tuple[Client, dict[str, Any]]:
        """Connect and send `hello`; the reply is returned whether or not it was ok."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if stalls:
            # A small window, so the kernel stops absorbing what this client never
            # reads after kilobytes rather than megabytes.
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
        sock.setblocking(False)
        await asyncio.get_running_loop().sock_connect(sock, ("127.0.0.1", self.port))
        reader, writer = await asyncio.open_connection(sock=sock, limit=4096 if stalls else 2**16)
        client = Client(reader, writer)
        self.clients.append(client)
        hello: dict[str, Any] = {
            "t": "hello",
            "id": 0,
            "token": self.server.token,
            "protocol": PROTOCOL_VERSION,
            **hello_fields,
        }
        if capabilities:
            hello["capabilities"] = list(capabilities)
        writer.write(encode_frame(hello))
        await writer.drain()
        reply, _ = await asyncio.wait_for(read_frame(reader), timeout=REPLY_SECONDS)
        return client, reply

    def lossy_connections(self) -> list[Connection]:
        return [c for c in self.server.connections if c.output_gap]


@contextlib.asynccontextmanager
async def open_supervisor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Running]:
    monkeypatch.setattr(supervisor_module, "create_reaper", FakeReaper)
    server = SupervisorServer(tmp_path / "config.toml", tmp_path)
    running = Running(server, await server.start())
    try:
        yield running
    finally:
        for client in running.clients:
            await client.close()
        server.exit_event.set()
        await asyncio.wait_for(server._teardown(), timeout=30)
        fanouts = [entry.fanout_task for entry in server.sessions.values() if entry.fanout_task]
        await asyncio.gather(*fanouts, return_exceptions=True)


def add_session(
    server: SupervisorServer,
    sid: str,
    *,
    queue_size: int = 1024,
    scrollback_bytes: int = 256 * 1024 * 1024,
) -> tuple[SupervisedSession, StreamHost]:
    host = StreamHost(queue_size)
    entry = SupervisedSession(sid, host, ScrollbackBuffer(scrollback_bytes))  # type: ignore[arg-type]
    entry.started_at = time.time()
    entry.spawn_done.set()
    server.sessions[sid] = entry
    entry.fanout_task = asyncio.create_task(server._fanout(entry), name=f"test-fanout-{sid}")
    return entry, host


def chunk(index: int) -> bytes:
    return bytes([65 + index % 26]) * CHUNK


async def pour(host: StreamHost, count: int, *, first: int = 0) -> None:
    """Feed the reader queue as a child would, failing if the queue ever fills.

    `put_nowait` is the assertion: a fanout that waited on any client long enough
    for the reader's 1,024 chunks to back up raises `QueueFull` here, which is
    exactly the point at which a real reader thread would block the agent.
    """
    for index in range(first, first + count):
        host.output_queue.put_nowait(chunk(index))
        await asyncio.sleep(0)


async def drained(host: StreamHost) -> None:
    """The fanout has taken every chunk, and offered each to a lossy client unawaited."""
    await until(host.output_queue.empty, seconds=30, what="the fanout drained the reader queue")
