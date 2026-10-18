"""The terminal gateway's side of the control protocol: terminal cores for the
daemon's sessions, hosted away from the daemon (`DAEMON_LOAD_ISOLATION.md` items
27 and 28).

`GatewayHub` listens for the daemon on a token-authenticated `framed_ipc` port and
hosts one `GatewaySession` per session the daemon sends: a `TerminalStream` and a
`TerminalCore`, exactly the ones the daemon runs in process, so a browser attached
here gets the same protocol, byte for byte. What the core asks of its host
(`GatewayTerminalHost`) is answered from what the daemon last sent - the record
snapshot, the leading device - and what typing means is sent back to the daemon,
which still owns it, in each session's coalesced `state` (`protocol.py`).

Nothing here waits on the daemon. A keystroke is written to the PTY and its
accounting done before anything is sent, and a daemon that stops reading costs
one pending summary per session, not a queue.

The process around it - the supervisor connection that feeds `feed_output` and
takes the core's writes, the browser listener that calls `serve`, discovery and
lifecycle - is `terminal_gateway.py` (item 28).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import secrets
import time
from collections import deque
from collections.abc import Callable, Coroutine
from typing import Any

from ..composer_input import PendingSubmit
from ..framed_ipc import FramedPeer, FramedServer, PeerClosed, RequestRefused
from .connection import serve_connection
from .core import TerminalCore, WriteRefused
from .host import PtySink, TerminalSocket
from .protocol import (
    BROADCAST,
    CLOSE_USE_DAEMON,
    DIAGNOSTICS,
    EVENT,
    EXIT,
    GATEWAY_CAPABILITIES,
    GATEWAY_PROTOCOL_VERSION,
    MAX_PENDING_EVENTS,
    MAX_PENDING_WRITE_BYTES,
    MAX_PENDING_WRITES,
    PENDING_SUBMIT,
    PRESENCE,
    REMOVE,
    REPAINT,
    REPAINT_RESULT,
    SESSIONS,
    STATE,
    STATE_ACK,
    UPDATE,
    UPSERT,
    WRITE,
    WRITE_BYTES,
    WRITE_RESULT,
    WRITE_SUBMIT,
    WRITE_TEXT,
    DescriptorState,
    condition_from_wire,
    encode_write_entry,
    entry_size,
)
from .stream import TerminalStream

log = logging.getLogger(__name__)

#: A session's ring when the daemon names none (the daemon's own default).
DEFAULT_MAX_SCROLLBACK = 2 * 1024 * 1024


class MirroredDescriptor:
    """A `TerminalDescriptor` answered from what the daemon last sent."""

    __slots__ = ("current",)

    def __init__(self, current: DescriptorState) -> None:
        self.current = current

    @property
    def session_id(self) -> str:
        return self.current.session_id

    @property
    def backend(self) -> str:
        return self.current.backend

    @property
    def state(self) -> str:
        return self.current.state

    @property
    def created_at(self) -> float:
        return self.current.created_at

    @property
    def stopping(self) -> bool:
        return self.current.stopping

    @property
    def ended(self) -> bool:
        return self.current.ended

    @property
    def needs_resize_repaint(self) -> bool:
        return self.current.needs_resize_repaint

    @property
    def repaints_scrollback(self) -> bool:
        return self.current.repaints_scrollback

    @property
    def replay_needs_repaint(self) -> bool:
        return self.current.replay_needs_repaint

    @property
    def suppresses_late_color_response(self) -> bool:
        return self.current.suppresses_late_color_response


class GatewayTerminalHost:
    """The gateway's side of one session's connections (`TerminalHost`).

    The snapshot and the leading device are the daemon's last word; events and the
    side effects typed input owes go back to the daemon rather than happening here.
    """

    def __init__(self, session: GatewaySession) -> None:
        self._session = session

    @property
    def generation(self) -> str:
        return self._session.hub.generation

    def snapshot(self) -> tuple[dict[str, Any], int]:
        return self._session.snapshot, self._session.revision

    def presence_leader(self) -> str | None:
        return self._session.hub.leader

    def emit(self, kind: str, *, source: str, **payload: Any) -> None:
        session = self._session
        session.hub.send_event(
            {"t": EVENT, "sid": session.id, "kind": kind, "source": source, "payload": payload}
        )
        # Attach, detach and ownership events change what the daemon mirrors.
        session.mark_dirty()

    def note_typed_text(self, data: str) -> None:
        self._session.record_write(encode_write_entry(WRITE_TEXT, data))

    def note_typed_bytes(self, data: bytes) -> None:
        self._session.record_write(encode_write_entry(WRITE_BYTES, data))

    def note_owner_submit(self, data: str) -> None:
        self._session.record_owner_submit(data)

    async def broadcast(self, data: str) -> None:
        # Delivered by the daemon, which owns the broadcast set, each target's
        # liveness and the accounting every operator write owes
        # (`routes/terminal.deliver_broadcast`): one implementation, not two.
        session = self._session
        session.hub.send_event({"t": BROADCAST, "sid": session.id, "data": data})


class GatewaySession:
    """One session's terminal in the gateway, and what the daemon has not yet heard."""

    def __init__(
        self,
        hub: GatewayHub,
        descriptor: DescriptorState,
        *,
        snapshot: dict[str, Any],
        revision: int,
        max_scrollback: int,
        attach_replay_bytes: int | None,
        input_revision: int,
        pending_submit: PendingSubmit | None,
    ) -> None:
        self.hub = hub
        self.id = descriptor.session_id
        self.descriptor = MirroredDescriptor(descriptor)
        self.snapshot = snapshot
        self.revision = revision
        self.tasks: set[asyncio.Task[Any]] = set()
        sid = self.id
        stream = TerminalStream(
            max_scrollback,
            attach_replay_bytes=attach_replay_bytes,
            stream_id=f"{sid}@gateway.{secrets.token_hex(6)}",
            remote_stream=lambda: hub.stream_name(sid),
            # Cold and inactive sessions are never served here (item 32).
            rebuilt=lambda: False,
        )
        self.core = TerminalCore(
            stream, pty=lambda: hub.pty_for(sid), descriptor=self.descriptor, tasks=self.tasks
        )
        self.core.input_revision = input_revision
        self.core.pending_submit = pending_submit
        self.core.on_change = self.mark_dirty
        self.host = GatewayTerminalHost(self)
        self.sockets: set[TerminalSocket] = set()
        # The keystroke writes the daemon has not acknowledged, oldest first, with
        # the sequence number of the first; numbered so a resend after a lost ack is
        # applied once by the daemon that already saw it, and in full by a new one.
        self.writes: deque[list[Any]] = deque()
        self.write_bytes = 0
        self.first_write_seq = 0
        # Every write numbered below this has gone out in some `state` and must not
        # change any more, even while it waits for its acknowledgement.
        self.sent_upto = 0
        self.writes_dropped = 0
        self.dirty = False
        self.in_flight = False
        self.claims_sent: tuple[int, int] | None = None

    # -- what the daemon has not yet heard -------------------------------------------

    def mark_dirty(self) -> None:
        self.dirty = True
        self.hub.schedule_flush()

    def record_write(self, entry: list[Any]) -> None:
        self.writes.append(entry)
        self.write_bytes += entry_size(entry)
        while self.writes and (
            len(self.writes) > MAX_PENDING_WRITES or self.write_bytes > MAX_PENDING_WRITE_BYTES
        ):
            dropped = self.writes.popleft()
            self.write_bytes -= entry_size(dropped)
            self.first_write_seq += 1
            self.writes_dropped += 1
        self.mark_dirty()

    def record_owner_submit(self, data: str) -> None:
        # The owner's submit follows its own typed text, so it rides on that entry
        # rather than repeating the data - unless that entry has already been sent.
        if self.writes and self.first_write_seq + len(self.writes) > self.sent_upto:
            last = self.writes[-1]
            if last[0] == WRITE_TEXT and len(last) == 2 and last[1] == data:
                last.append(1)
                self.mark_dirty()
                return
        self.record_write(encode_write_entry(WRITE_SUBMIT, data))

    def acknowledge(self, upto: int) -> None:
        """The daemon has applied every write numbered below ``upto``."""
        while self.writes and self.first_write_seq < upto:
            entry = self.writes.popleft()
            self.write_bytes -= entry_size(entry)
            self.first_write_seq += 1
        self.in_flight = False
        if self.dirty:
            self.hub.schedule_flush()

    def state_header(self, *, force_claims: bool) -> dict[str, Any]:
        core = self.core
        now = time.monotonic()
        header: dict[str, Any] = {
            "t": STATE,
            "sid": self.id,
            "revision": core.input_revision,
            "age": core.last_input_age(),
            "first_write_seq": self.first_write_seq,
            "writes": list(self.writes),
            "mode": core.terminal_mode,
            "mode_age": (
                max(0.0, now - core.terminal_mode_updated_at)
                if core.terminal_mode_updated_at > 0
                else None
            ),
            "owner_connection": core.input_owner,
            "owner_device": core.input_owner_device,
            "owner_epoch": core.input_owner_epoch,
            "geometry": list(core.geometry) if core.geometry else None,
            "viewports": {cid: list(size) for cid, size in core.viewports.items()},
            "attached": core.attached_connections,
            "attachments_seen": core.attachments_seen,
            "rejections": core.input_rejections,
            "denials": core.input_claim_denials,
        }
        claim_log = core.claim_log
        fingerprint = (len(claim_log), id(claim_log[-1]) if claim_log else 0)
        if force_claims or fingerprint != self.claims_sent:
            header["claims"] = list(claim_log)
            self.claims_sent = fingerprint
        return header

    def diagnostics(self) -> dict[str, Any]:
        core = self.core
        return {
            "input_revision": core.input_revision,
            "last_input_age": core.last_input_age(),
            "owner_device": core.input_owner_device,
            "owner_epoch": core.input_owner_epoch,
            "geometry": list(core.geometry) if core.geometry else None,
            "viewports": len(core.viewports),
            "attached": core.attached_connections,
            "claims": list(core.claim_log),
            "rejections": core.input_rejections,
            "denials": core.input_claim_denials,
            "terminal_mode": core.terminal_mode,
            "pending_writes": len(self.writes),
            "writes_dropped": self.writes_dropped,
            "in_flight": self.in_flight,
            "ring_position": core.stream.scrollback.position,
        }

    async def close_sockets(self, code: int, message: bytes) -> None:
        for socket in tuple(self.sockets):
            close = getattr(socket, "close", None)
            if close is not None:
                with contextlib.suppress(Exception):
                    await close(code=code, message=message)
        for task in tuple(self.tasks):
            task.cancel()


class GatewayHub:
    """Every session the gateway hosts, and the one daemon it answers to.

    ``pty_for(session_id)`` is where a core's writes and resizes go and
    ``stream_name(session_id)`` names the byte stream its ring positions count -
    both the supervisor's, in the gateway process.
    """

    def __init__(
        self,
        *,
        token: str,
        pty_for: Callable[[str], PtySink],
        stream_name: Callable[[str], str] = lambda _sid: "",
        gateway_id: str | None = None,
    ) -> None:
        self.pty_for = pty_for
        self.stream_name = stream_name
        self.gateway_id = gateway_id or secrets.token_hex(8)
        self.sessions: dict[str, GatewaySession] = {}
        self.leader: str | None = None
        self.generation = "legacy"
        self.daemon: FramedPeer | None = None
        # Events and broadcasts held while no daemon is attached; the oldest go first.
        self.outbox: deque[dict[str, Any]] = deque(maxlen=MAX_PENDING_EVENTS)
        self.events_dropped = 0
        self._flush_scheduled = False
        self._tasks: set[asyncio.Task[Any]] = set()
        self.server = FramedServer(
            token=token,
            protocol=GATEWAY_PROTOCOL_VERSION,
            capabilities=GATEWAY_CAPABILITIES,
            name="terminal-gateway",
            on_connection=self._daemon_connected,
            on_request=self._on_request,
            on_notice=self._on_notice,
            on_close=self._daemon_closed,
            hello_reply=self._hello_reply,
        )

    async def start(self, host: str = "127.0.0.1", port: int = 0) -> int:
        return await self.server.start(host, port)

    async def close(self) -> None:
        await self.server.close()
        for session in tuple(self.sessions.values()):
            for task in tuple(session.tasks):
                task.cancel()
        for task in tuple(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

    # -- browsers and the PTY ----------------------------------------------------------

    async def serve(self, ws: TerminalSocket, session_id: str, *, connection_id: str) -> None:
        """Run one prepared browser socket. Raises `KeyError` for a session not hosted
        here, which the caller answers with `CLOSE_USE_DAEMON`."""
        session = self.sessions[session_id]
        session.sockets.add(ws)
        try:
            await serve_connection(ws, session.core, session.host, connection_id=connection_id)
        finally:
            session.sockets.discard(ws)

    def feed_output(self, session_id: str, data: bytes) -> None:
        session = self.sessions.get(session_id)
        if session is not None:
            session.core.stream.feed(data)

    # -- the daemon connection ---------------------------------------------------------

    def _hello_reply(self, request: dict[str, Any]) -> dict[str, Any]:
        generation = request.get("generation")
        if isinstance(generation, str) and generation:
            self.generation = generation
        return {"gateway_id": self.gateway_id}

    def _daemon_connected(self, peer: FramedPeer) -> None:
        previous, self.daemon = self.daemon, peer
        if previous is not None and not previous.closed:
            # A restarted daemon's successor: the newest one is the daemon.
            self._spawn(previous.close("superseded by a newer daemon connection"))
        for session in self.sessions.values():
            session.in_flight = False
        while self.outbox:
            if not self._notify(self.outbox[0]):
                return
            self.outbox.popleft()
        self._flush_all(force_claims=True)

    def _daemon_closed(self, peer: FramedPeer, reason: str) -> None:
        if peer is self.daemon:
            self.daemon = None
            for session in self.sessions.values():
                session.in_flight = False

    def _notify(self, header: dict[str, Any], payload: bytes = b"") -> bool:
        peer = self.daemon
        if peer is None or peer.closed:
            return False
        try:
            peer.notify(header, payload)
        except PeerClosed:
            return False
        return True

    def send_event(self, header: dict[str, Any]) -> None:
        if self.daemon is not None and not self.outbox and self._notify(header):
            return
        if len(self.outbox) == self.outbox.maxlen:
            self.events_dropped += 1
        self.outbox.append(header)

    def schedule_flush(self) -> None:
        if self._flush_scheduled or self.daemon is None:
            return
        self._flush_scheduled = True
        asyncio.get_running_loop().call_soon(self._flush)

    def _flush(self) -> None:
        self._flush_scheduled = False
        self._flush_all(force_claims=False)

    def _flush_all(self, *, force_claims: bool) -> None:
        for session in tuple(self.sessions.values()):
            if (session.dirty or force_claims) and not session.in_flight:
                self._send_state(session, force_claims=force_claims)

    def _send_state(self, session: GatewaySession, *, force_claims: bool) -> None:
        header = session.state_header(force_claims=force_claims)
        if self._notify(header):
            session.dirty = False
            session.in_flight = True
            session.sent_upto = session.first_write_seq + len(session.writes)
        else:
            session.claims_sent = None

    def _spawn(self, coroutine: Coroutine[Any, Any, Any]) -> None:
        task = asyncio.get_running_loop().create_task(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    # -- what the daemon says ------------------------------------------------------------

    def _on_notice(self, peer: FramedPeer, header: dict[str, Any], payload: bytes) -> None:
        if peer is not self.daemon:
            return  # a superseded daemon, still draining
        kind = header.get("t")
        handler = self._NOTICES.get(str(kind))
        if handler is None:
            log.warning("terminal gateway ignored an unknown notice %r from the daemon", kind)
            return
        handler(self, header, payload)

    def _upsert(self, header: dict[str, Any], _payload: bytes) -> None:
        descriptor = DescriptorState.from_wire(header.get("descriptor"))
        sid = descriptor.session_id
        snapshot = header.get("snapshot")
        if not isinstance(snapshot, dict):
            raise ValueError(f"upsert for {sid} carries no snapshot")
        revision = int(header.get("revision") or 0)
        input_revision = int(header.get("input_revision") or 0)
        pending = _pending_submit(header.get("pending_submit"))
        session = self.sessions.get(sid)
        if session is None:
            replay = header.get("attach_replay_bytes")
            session = GatewaySession(
                self,
                descriptor,
                snapshot=snapshot,
                revision=revision,
                max_scrollback=int(header.get("max_scrollback") or DEFAULT_MAX_SCROLLBACK),
                attach_replay_bytes=int(replay) if isinstance(replay, int) else None,
                input_revision=input_revision,
                pending_submit=pending,
            )
            self.sessions[sid] = session
            log.info("terminal gateway now hosts session %s (%s)", sid, descriptor.backend)
        else:
            # A daemon that restarted re-sends what it serves; everything the
            # browsers built up here - ownership, viewports, the revision - stays.
            session.descriptor.current = descriptor
            session.snapshot, session.revision = snapshot, revision
            session.core.input_revision = max(session.core.input_revision, input_revision)
            session.core.pending_submit = pending
        session.mark_dirty()

    def _remove(self, header: dict[str, Any], _payload: bytes) -> None:
        self.remove(str(header.get("sid")))

    def remove(self, session_id: str) -> None:
        session = self.sessions.pop(session_id, None)
        if session is None:
            return
        log.info("terminal gateway no longer hosts session %s", session_id)
        self._spawn(session.close_sockets(CLOSE_USE_DAEMON, b"moved"))

    def _sessions(self, header: dict[str, Any], _payload: bytes) -> None:
        wanted = header.get("sids")
        if not isinstance(wanted, list):
            raise ValueError("sessions needs a list of session ids")
        keep = {str(sid) for sid in wanted}
        for sid in tuple(self.sessions):
            if sid not in keep:
                self.remove(sid)

    def _frame(self, header: dict[str, Any], *, exit_frame: bool) -> GatewaySession | None:
        session = self.sessions.get(str(header.get("sid")))
        if session is None:
            return None
        frame = header.get("frame")
        if not isinstance(frame, dict) or not isinstance(frame.get("snapshot"), dict):
            raise ValueError("a snapshot frame needs a snapshot")
        session.snapshot = frame["snapshot"]
        session.revision = int(frame.get("revision") or session.revision)
        if header.get("descriptor") is not None:
            session.descriptor.current = DescriptorState.from_wire(header["descriptor"])
        if exit_frame:
            session.core.publish_exit(frame)
        else:
            session.core.publish_update(frame)
        return session

    def _update(self, header: dict[str, Any], _payload: bytes) -> None:
        self._frame(header, exit_frame=False)

    def _exit(self, header: dict[str, Any], _payload: bytes) -> None:
        self._frame(header, exit_frame=True)

    def _presence(self, header: dict[str, Any], _payload: bytes) -> None:
        leader = header.get("leader")
        self.leader = str(leader) if isinstance(leader, str) and leader else None

    def _pending_submit_notice(self, header: dict[str, Any], _payload: bytes) -> None:
        session = self.sessions.get(str(header.get("sid")))
        if session is not None:
            session.core.pending_submit = _pending_submit(header.get("pending_submit"))

    def _write(self, header: dict[str, Any], payload: bytes) -> None:
        sid = str(header.get("sid"))
        rid = header.get("rid")
        session = self.sessions.get(sid)
        reason: str | None = None
        if session is None:
            reason = "not_hosted"
        else:
            core = session.core
            try:
                condition = condition_from_wire(header.get("condition"))
                if header.get("accounted"):
                    core.write_operator_input(payload.decode("utf-8"), condition=condition)
                else:
                    data: str | bytes = payload.decode("utf-8") if header.get("text") else payload
                    core.write(data, condition=condition)
            except WriteRefused as refused:
                reason = refused.reason
            except Exception as exc:
                log.warning(
                    "terminal gateway write to %s failed: %s: %s", sid, type(exc).__name__, exc
                )
                reason = f"write_failed: {type(exc).__name__}"
        # `written`, never `ok`: a frame carrying `ok` is an RPC reply to framed_ipc.
        result: dict[str, Any] = {
            "t": WRITE_RESULT,
            "sid": sid,
            "rid": rid,
            "written": reason is None,
        }
        if reason is not None:
            result["reason"] = reason
        if session is not None:
            result["revision"] = session.core.input_revision
            result["age"] = session.core.last_input_age()
        self._notify(result)

    def _repaint(self, header: dict[str, Any], _payload: bytes) -> None:
        sid = str(header.get("sid"))
        rid = header.get("rid")
        session = self.sessions.get(sid)
        if session is None:
            self._notify({"t": REPAINT_RESULT, "sid": sid, "rid": rid, "applied": False})
            return

        async def repaint(target: GatewaySession) -> None:
            applied = await target.core.repaint_current_geometry()
            self._notify({"t": REPAINT_RESULT, "sid": sid, "rid": rid, "applied": applied})

        self._spawn(repaint(session))

    def _state_ack(self, header: dict[str, Any], _payload: bytes) -> None:
        session = self.sessions.get(str(header.get("sid")))
        upto = header.get("upto")
        if session is not None and isinstance(upto, int):
            session.acknowledge(upto)

    _NOTICES: dict[str, Callable[[GatewayHub, dict[str, Any], bytes], None]] = {
        UPSERT: _upsert,
        REMOVE: _remove,
        SESSIONS: _sessions,
        UPDATE: _update,
        EXIT: _exit,
        PRESENCE: _presence,
        PENDING_SUBMIT: _pending_submit_notice,
        WRITE: _write,
        REPAINT: _repaint,
        STATE_ACK: _state_ack,
    }

    async def _on_request(
        self, peer: FramedPeer, header: dict[str, Any], _payload: bytes
    ) -> dict[str, Any]:
        if header.get("t") != DIAGNOSTICS:
            raise RequestRefused(f"unknown message type: {header.get('t')!r}")
        wanted = header.get("sid")
        sessions = self.sessions
        if isinstance(wanted, str):
            one = self.sessions.get(wanted)
            sessions = {wanted: one} if one is not None else {}
        return {
            "gateway_id": self.gateway_id,
            "events_dropped": self.events_dropped,
            "sessions": {sid: session.diagnostics() for sid, session in sessions.items()},
        }

    def stats(self) -> dict[str, Any]:
        return {
            "gateway_id": self.gateway_id,
            "sessions": len(self.sessions),
            "daemon_connected": self.daemon is not None,
            "outbox": len(self.outbox),
            "events_dropped": self.events_dropped,
            "server": self.server.stats(),
        }


def _pending_submit(value: Any) -> PendingSubmit | None:
    if not isinstance(value, dict):
        return None
    return PendingSubmit(
        message_id=str(value.get("message_id") or ""),
        at=float(value.get("at") or 0.0),
        byte_count=int(value.get("byte_count") or 0),
    )


__all__ = [
    "DEFAULT_MAX_SCROLLBACK",
    "GatewayHub",
    "GatewaySession",
    "GatewayTerminalHost",
    "MirroredDescriptor",
]
