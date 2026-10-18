"""One session's terminal, with its core in process or in a terminal gateway.

`DAEMON_LOAD_ISOLATION.md` item 27 is done when the core's behaviour holds against
both hubs, so the same tests drive either: `LocalRig` puts the core where the
daemon has always had it, and `RemoteRig` puts it in a `GatewayHub` reached over a
real loopback `framed_ipc` connection, with the daemon holding a `RemoteTerminal`
mirror. Each rig exposes the same three things - ``terminal`` (what the daemon
holds on `Session.terminal`), ``connect`` (a browser), and ``host`` (the daemon's
side effects and event log) - and the tests never ask which one they have.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from aiohttp import WSMessage, WSMsgType

from swe_mux.framed_ipc import new_token
from swe_mux.terminal_core import (
    DescriptorState,
    GatewayHub,
    RemoteHub,
    RemoteTerminal,
    TerminalCore,
    TerminalStream,
    serve_connection,
)
from tests.support.settle import until

SESSION_ID = "s1"


def descriptor(**changes: Any) -> DescriptorState:
    fields: dict[str, Any] = {
        "session_id": SESSION_ID,
        "backend": "claude",
        "state": "idle",
        "created_at": 0.0,
    }
    fields.update(changes)
    return DescriptorState(**fields)


class RecordingPty:
    def __init__(self) -> None:
        self.writes: list[str | bytes] = []
        self.resizes: list[tuple[int, int]] = []

    def write(self, data: str | bytes) -> None:
        self.writes.append(data)

    def resize(self, cols: int, rows: int) -> None:
        self.resizes.append((cols, rows))


class DaemonHost:
    """The daemon's `TerminalHost`: what typing means, and the event log."""

    def __init__(self) -> None:
        self.leader: str | None = None
        self.typed: list[str] = []
        self.typed_bytes: list[bytes] = []
        self.submits: list[str] = []
        self.events: list[tuple[str, str, dict[str, Any]]] = []
        self.broadcasts: list[str] = []
        self.record: dict[str, Any] = {"id": SESSION_ID, "state": "idle"}
        self.revision = 0

    @property
    def generation(self) -> str:
        return "gen-1"

    def snapshot(self) -> tuple[dict[str, Any], int]:
        return dict(self.record), self.revision

    def presence_leader(self) -> str | None:
        return self.leader

    def emit(self, kind: str, *, source: str, **payload: Any) -> None:
        self.events.append((kind, source, payload))

    def event_kinds(self) -> list[str]:
        return [kind for kind, _source, _payload in self.events]

    def note_typed_text(self, data: str) -> None:
        self.typed.append(data)

    def note_typed_bytes(self, data: bytes) -> None:
        self.typed_bytes.append(data)

    def note_owner_submit(self, data: str) -> None:
        self.submits.append(data)

    async def broadcast(self, data: str) -> None:
        self.broadcasts.append(data)


class FakeSocket:
    """A prepared browser WebSocket, as the core uses one (`TerminalSocket`)."""

    def __init__(self) -> None:
        self.inbound: asyncio.Queue[WSMessage] = asyncio.Queue()
        self.sent: list[dict[str, Any]] = []
        self.binary: list[bytes] = []
        self.closed: tuple[int, bytes] | None = None

    async def send_json(self, data: Any) -> None:
        self.sent.append(data)

    async def send_bytes_classified(self, data: bytes, kind: str) -> None:
        self.binary.append(data)

    async def receive(self) -> WSMessage:
        return await self.inbound.get()

    def __aiter__(self) -> FakeSocket:
        return self

    async def __anext__(self) -> WSMessage:
        message = await self.inbound.get()
        if message.type in {WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.ERROR}:
            raise StopAsyncIteration
        return message

    async def close(self, *, code: int = 1000, message: bytes = b"") -> None:
        self.closed = (code, message)
        self.inbound.put_nowait(WSMessage(WSMsgType.CLOSED, None, None))


class Browser:
    def __init__(self, socket: FakeSocket, task: asyncio.Task[Any]) -> None:
        self.socket = socket
        self.task = task

    def send(self, frame: dict[str, Any]) -> None:
        self.socket.inbound.put_nowait(WSMessage(WSMsgType.TEXT, json.dumps(frame), None))

    def frames(self, kind: str) -> list[dict[str, Any]]:
        return [frame for frame in self.socket.sent if frame.get("type") == kind]

    async def attach(self, cols: int = 100, rows: int = 30, *, hidden: bool = False) -> None:
        self.send({"type": "attach_ready", "cols": cols, "rows": rows, "hidden": hidden})
        await until(lambda: bool(self.frames("replay_end")), what="the attach replay ended")

    async def claim(self, device: str = "desktop", *, reason: str = "gesture") -> dict[str, Any]:
        seen = len(self.frames("input_owner"))
        self.send({"type": "claim_input", "device": device, "reason": reason, "focused": True})
        await until(lambda: len(self.frames("input_owner")) > seen, what="a claim answer")
        return self.frames("input_owner")[-1]

    def type(self, data: str, **extra: Any) -> None:
        self.send({"type": "input", "data": data, **extra})

    async def close(self) -> None:
        self.socket.inbound.put_nowait(WSMessage(WSMsgType.CLOSE, None, None))
        await asyncio.wait_for(self.task, timeout=5)


class LocalRig:
    """The core in process, where the daemon has always run it."""

    kind = "local"

    def __init__(self) -> None:
        self.pty = RecordingPty()
        self.host = DaemonHost()
        self.tasks: set[asyncio.Task[Any]] = set()
        self.browsers: list[Browser] = []
        self.terminal: TerminalCore | RemoteTerminal

    async def start(self) -> None:
        stream = TerminalStream(
            64 * 1024,
            attach_replay_bytes=None,
            stream_id=f"{SESSION_ID}@test",
            remote_stream=lambda: "",
            rebuilt=lambda: False,
        )
        self.core = TerminalCore(
            stream, pty=lambda: self.pty, descriptor=descriptor(), tasks=self.tasks
        )
        self.terminal = self.core

    def connect(self, connection_id: str) -> Browser:
        socket = FakeSocket()
        task = asyncio.create_task(
            serve_connection(socket, self.core, self.host, connection_id=connection_id)
        )
        browser = Browser(socket, task)
        self.browsers.append(browser)
        return browser

    async def set_leader(self, leader: str | None) -> None:
        self.host.leader = leader

    def feed(self, data: bytes) -> None:
        self.core.stream.feed(data)

    async def stop(self) -> None:
        for browser in self.browsers:
            if not browser.task.done():
                await browser.close()
        for task in tuple(self.tasks):
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)


class RemoteRig:
    """The core in a terminal gateway, the daemon holding a mirror of it."""

    kind = "remote"

    def __init__(self) -> None:
        self.pty = RecordingPty()
        self.host = DaemonHost()
        self.browsers: list[Browser] = []
        self.token = new_token()
        self.terminal: TerminalCore | RemoteTerminal

    async def start(self) -> None:
        self.gateway = GatewayHub(token=self.token, pty_for=lambda _sid: self.pty)
        self.port = await self.gateway.start()
        self.hub = RemoteHub(generation="gen-1")
        await self.hub.connect("127.0.0.1", self.port, token=self.token)
        self.daemon_stream = TerminalStream(
            64 * 1024,
            attach_replay_bytes=None,
            stream_id=f"{SESSION_ID}@daemon",
            remote_stream=lambda: "",
            rebuilt=lambda: False,
        )
        self.terminal = self.hub.adopt(
            stream=self.daemon_stream, descriptor=descriptor(), host=self.host
        )
        await until(lambda: SESSION_ID in self.gateway.sessions, what="the gateway hosts it")
        self.core = self.gateway.sessions[SESSION_ID].core

    def connect(self, connection_id: str) -> Browser:
        socket = FakeSocket()
        task = asyncio.create_task(
            self.gateway.serve(socket, SESSION_ID, connection_id=connection_id)
        )
        browser = Browser(socket, task)
        self.browsers.append(browser)
        return browser

    async def set_leader(self, leader: str | None) -> None:
        self.host.leader = leader
        self.hub.set_presence(leader)
        await until(lambda: self.gateway.leader == leader, what="the gateway heard the leader")

    def feed(self, data: bytes) -> None:
        self.gateway.feed_output(SESSION_ID, data)

    async def stop(self) -> None:
        for browser in self.browsers:
            if not browser.task.done():
                await browser.close()
        await self.hub.close()
        await self.gateway.close()


Rig = LocalRig | RemoteRig
