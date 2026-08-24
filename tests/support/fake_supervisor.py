"""A loopback server that speaks the supervisor frame protocol and nothing else.

The real supervisor owns ConPTYs, jobs, and a process tree, which makes it a
Windows-only, wall-clock-sensitive dependency (`test_pty_supervisor.py` is
marked accordingly). Everything the *client* has to get right - tri-state
liveness, keeping RPC replies moving while one session's queue is full,
classifying a malformed frame, asking `spawn_status` before falling back - is
decided by the bytes on the wire and nothing else, so these tests drive the
bytes directly and run on every platform in milliseconds.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from swe_mux.supervisor import PROTOCOL_VERSION, discovery_path, encode_frame, read_frame

Responder = Callable[[dict[str, Any], bytes], dict[str, Any] | None]

__all__ = ["FakeSupervisor", "Responder"]


class FakeSupervisor:
    """Serve one client connection, recording every frame it receives."""

    def __init__(
        self,
        data_dir: Path,
        *,
        pid: int | None = None,
        token: str = "test-token",
        responder: Responder | None = None,
        sessions: list[dict[str, Any]] | None = None,
    ) -> None:
        self.data_dir = data_dir
        # Truthfully alive by default: the fake runs inside the test process, so
        # `_pid_running` on this pid answers the same "yes" a real live
        # supervisor would, which is exactly the condition the F1 tests need.
        self.pid = os.getpid() if pid is None else pid
        self.token = token
        self.responder: Responder = responder or default_responder
        self.sessions = sessions or []
        self.received: list[tuple[dict[str, Any], bytes]] = []
        self.port = 0
        self._server: asyncio.Server | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._serve_task: asyncio.Task[None] | None = None
        self.connections = 0

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = int(self._server.sockets[0].getsockname()[1])
        self.data_dir.mkdir(parents=True, exist_ok=True)
        discovery_path(self.data_dir).write_text(
            json.dumps(
                {
                    "pid": self.pid,
                    "port": self.port,
                    "token": self.token,
                    "protocol": PROTOCOL_VERSION,
                    "config_path": str(self.data_dir / "config.toml"),
                    "started_at": time.time(),
                }
            ),
            encoding="utf-8",
        )

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            await self._greet(reader, writer)
        except (asyncio.IncompleteReadError, ConnectionError, OSError, ValueError):
            # An ephemeral loopback port attracts unrelated probes (this host has
            # something that speaks HTTP at every listener it finds). A probe must
            # not become "the client", and its garbage must not surface as an
            # unhandled task exception in an unrelated test.
            writer.close()

    async def _greet(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        header, _ = await read_frame(reader)
        if header.get("token") != self.token:
            writer.write(encode_frame({"id": header.get("id"), "ok": False, "error": "bad token"}))
            await writer.drain()
            writer.close()
            return
        self.connections += 1
        self._writer = writer
        writer.write(
            encode_frame(
                {
                    "id": header.get("id"),
                    "ok": True,
                    "pid": self.pid,
                    "protocol": PROTOCOL_VERSION,
                    "sessions": self.sessions,
                }
            )
        )
        await writer.drain()
        self._serve_task = asyncio.create_task(self._serve(reader, writer))

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while True:
                header, payload = await read_frame(reader)
                self.received.append((header, payload))
                reply = self.responder(header, payload)
                if reply is None:
                    continue
                writer.write(encode_frame({"id": header.get("id"), **reply}))
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError, OSError, ValueError):
            return

    def send(self, header: dict[str, Any], payload: bytes = b"") -> None:
        """Push an unsolicited frame (output, pty_exit) to the connected client."""
        assert self._writer is not None, "no client is connected"
        self._writer.write(encode_frame(header, payload))

    def send_raw(self, raw: bytes) -> None:
        """Push arbitrary bytes, including bytes that are not a valid frame."""
        assert self._writer is not None, "no client is connected"
        self._writer.write(raw)

    def requests_of(self, kind: str) -> list[dict[str, Any]]:
        return [header for header, _ in self.received if header.get("t") == kind]

    async def drop_connection(self) -> None:
        """Close the socket without stopping the process the discovery file names."""
        if self._serve_task is not None and not self._serve_task.done():
            self._serve_task.cancel()
            await asyncio.gather(self._serve_task, return_exceptions=True)
        if self._writer is not None:
            self._writer.close()
            self._writer = None

    async def stop(self) -> None:
        await self.drop_connection()
        if self._server is not None:
            self._server.close()
            # Bounded on purpose: `wait_closed()` waits for every *connection*,
            # including a stray probe whose socket nobody in this test owns, and
            # that wait is unbounded. The port is released by `close()` either way.
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._server.wait_closed(), timeout=2)
            self._server = None


def default_responder(header: dict[str, Any], _payload: bytes) -> dict[str, Any] | None:
    """Accept the messages a healthy supervisor accepts; reject the rest by name."""
    kind = str(header.get("t") or "")
    if kind == "spawn":
        return {"ok": True, "pid": 4321, "reaper_assignment": "supervisor_job"}
    if kind in {"stop", "remove", "set_meta", "write", "resize", "set_graceful_exit"}:
        return {"ok": True} if header.get("id") is not None else None
    if kind == "subscribe":
        return {"ok": True, "alive": True, "exit_code": None}
    return {"ok": False, "error": f"unknown message type: {kind}"}
