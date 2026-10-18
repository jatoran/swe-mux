"""The daemon's side of the terminal gateway: a mirror of each terminal whose core
runs there, and the connection that keeps it current (`DAEMON_LOAD_ISOLATION.md`
item 27).

A `RemoteTerminal` stands where a `TerminalCore` stands in process - on
`Session.terminal` - and answers the same attribute names from what the gateway
last reported, so delivery readiness, the attended checks and diagnostics read it
without knowing where the core is. It keeps the daemon's own `TerminalStream`
(the ring that status detection reads, fed by the daemon's fanout as before);
only the browsers are elsewhere.

What is authoritative where:

- **The gateway** owns the input accounting (`input_revision` and the last input's
  time), ownership, viewports, geometry and the terminal mode, because keystrokes
  arrive there. The mirror merges what it reports; a revision only grows and the
  last input only gets more recent, so a stale report never moves either backwards.
- **The daemon** owns everything typing *means*: approval cancellation, the
  composer estimate, interrupt intent, the unwitnessed-turn arming. The gateway
  sends each keystroke write and the daemon runs the same `TerminalHost` side
  effects on it that it runs in process, in order, exactly once per daemon.
- **The daemon** also owns `pending_submit`, which the gateway needs only as a
  condition, so it is sent the other way.

Writes go to the gateway as ordered notices with a request id. `write` and
`write_operator_input` do not wait, so the in-process callers keep their shape;
`write_when` - the conditional write a delivery makes - waits for the gateway's
verdict, because only the process holding the keystrokes can make it.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from collections.abc import Coroutine
from typing import Any

from ..composer_input import ComposerState, PendingSubmit
from ..framed_ipc import (
    FramedPeer,
    HandshakeRefused,
    PeerClosed,
    connect,
)
from .core import CLAIM_LOG_LIMIT, WriteCondition, WriteRefused
from .host import TerminalDescriptor, TerminalHost
from .protocol import (
    BROADCAST,
    CAP_DIAGNOSTICS,
    DIAGNOSTICS,
    EVENT,
    EXIT,
    GATEWAY_CAPABILITIES,
    GATEWAY_PROTOCOL_VERSION,
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
    DescriptorState,
    condition_to_wire,
    decode_write_entry,
    optional_age,
)
from .stream import TerminalStream

log = logging.getLogger(__name__)

#: How long a conditional write or a repaint waits for the gateway's answer.
REMOTE_ANSWER_TIMEOUT_SECONDS = 10.0


class TerminalUnavailable(RuntimeError):
    """The gateway holding this terminal cannot be reached or did not answer."""


class RemoteTerminal:
    """The daemon's handle on one terminal whose core runs in the gateway."""

    def __init__(
        self,
        hub: RemoteHub,
        *,
        stream: TerminalStream,
        descriptor: TerminalDescriptor,
        host: TerminalHost,
        seed: Any = None,
    ) -> None:
        self.hub = hub
        self.stream = stream
        self.descriptor = descriptor
        self.host = host
        self.session_id = descriptor.session_id
        # The names `TerminalCore` uses, so `Session`'s views read either.
        self.attachments_seen = 0
        self.input_owner: str | None = None
        self.input_owner_socket: Any = None
        self.input_owner_epoch = 0
        self.input_owner_device: str | None = None
        self.input_owner_gesture_ts: float | None = None
        self.input_rejections = 0
        self.last_input_reject_report_ts = 0.0
        self.last_client_repaint_ts = 0.0
        self.client_diagnostic_timestamps: dict[str, float] = {}
        self.resize_repaint_deadline = 0.0
        self.resize_repaint_task: asyncio.Task[None] | None = None
        self.input_claim_denials = 0
        self.claim_refusals: dict[str, float] = {}
        self.claim_log: deque[dict[str, Any]] = deque(maxlen=CLAIM_LOG_LIMIT)
        self.viewports: dict[str, tuple[int, int]] = {}
        self.geometry: tuple[int, int] | None = None
        self.last_input_event_ts = 0.0
        self.last_input_report_ts = 0.0
        self.input_revision = 0
        self.composer = ComposerState()
        self._pending_submit: PendingSubmit | None = None
        self.terminal_mode: str | None = None
        self.terminal_mode_updated_at = 0.0
        self.attached_connections = 0
        # The number of the next keystroke write this daemon has not yet applied;
        # None until the gateway has said anything, so a new daemon applies all.
        self.next_write_seq: int | None = None
        self.writes_lost = 0
        if seed is not None:
            self._take_state(seed)

    def _take_state(self, core: Any) -> None:
        """Carry the daemon-side state over from the core this replaces."""
        self.input_revision = core.input_revision
        self.last_input_event_ts = core.last_input_event_ts
        self.last_input_report_ts = core.last_input_report_ts
        self.composer = core.composer
        self._pending_submit = core.pending_submit
        self.terminal_mode = core.terminal_mode
        self.terminal_mode_updated_at = core.terminal_mode_updated_at
        self.geometry = core.geometry
        self.input_owner_device = core.input_owner_device
        self.input_owner_epoch = core.input_owner_epoch
        self.attachments_seen = core.attachments_seen

    # -- the daemon-owned half -------------------------------------------------------

    @property
    def pending_submit(self) -> PendingSubmit | None:
        return self._pending_submit

    @pending_submit.setter
    def pending_submit(self, value: PendingSubmit | None) -> None:
        self._pending_submit = value
        self.hub.send_pending_submit(self)

    def last_input_age(self) -> float | None:
        if self.last_input_event_ts <= 0:
            return None
        return max(0.0, time.monotonic() - self.last_input_event_ts)

    # -- writes --------------------------------------------------------------------

    def write(self, data: str | bytes, *, condition: WriteCondition | None = None) -> None:
        """Send a write without waiting; it applies in order with every other one."""
        if condition is not None:
            raise TypeError("a conditional write to a remote terminal is awaited: write_when")
        self.hub.send_write(self, data, accounted=False, condition=None)

    def write_operator_input(
        self, data: str, *, condition: WriteCondition | None = None
    ) -> None:
        """Send accounted input without waiting, and account for it here at once.

        The gateway counts the same write, so its next report agrees; until then
        readiness already sees the composer touched and the operator active.
        """
        if condition is not None:
            raise TypeError("a conditional write to a remote terminal is awaited: write_when")
        self.hub.send_write(self, data, accounted=True, condition=None)
        now = time.monotonic()
        self.input_revision += 1
        self.last_input_event_ts = now
        self.last_input_report_ts = now

    async def write_when(
        self, data: str | bytes, *, condition: WriteCondition | None, accounted: bool
    ) -> None:
        """Write only if ``condition`` still holds where the keystrokes arrive.

        Raises `WriteRefused` with the gateway's reason, or `TerminalUnavailable`
        when it cannot be asked or does not answer.
        """
        if accounted and not isinstance(data, str):
            raise TypeError("accounted input is text")
        await self.hub.write_and_wait(self, data, accounted=accounted, condition=condition)

    async def repaint_current_geometry(self) -> bool:
        return await self.hub.repaint(self)

    # -- geometry belongs to the gateway --------------------------------------------

    def set_viewport(self, connection_id: str, cols: int, rows: int, *, hidden: bool) -> None:
        """Browsers report their viewports to the gateway, never to the daemon."""

    def apply_geometry(self) -> bool:
        return False

    def geometry_frame(self) -> dict[str, Any]:
        cols, rows = self.geometry or (80, 24)
        return {
            "type": "geometry",
            "cols": cols,
            "rows": rows,
            "owner_device": self.input_owner_device,
        }

    # -- frames for the browsers ------------------------------------------------------

    def publish_update(self, frame: dict[str, Any]) -> None:
        self.stream.publish_update(frame)
        self.hub.send_frame(self, UPDATE, frame)

    def publish_exit(self, frame: dict[str, Any]) -> None:
        self.stream.publish_exit(frame)
        self.hub.send_frame(self, EXIT, frame)

    # -- what the gateway reports -----------------------------------------------------

    def merge(self, header: dict[str, Any]) -> None:
        now = time.monotonic()
        revision = header.get("revision")
        if isinstance(revision, int) and not isinstance(revision, bool):
            self.input_revision = max(self.input_revision, revision)
        age = optional_age(header.get("age"))
        if age is not None:
            stamp = now - age
            if stamp > self.last_input_event_ts:
                self.last_input_event_ts = stamp
                self.last_input_report_ts = stamp
        if "mode" not in header:
            return  # a write result carries only the accounting
        mode = header.get("mode")
        self.terminal_mode = mode if mode in {"normal", "alternate"} else None
        mode_age = optional_age(header.get("mode_age"))
        self.terminal_mode_updated_at = now - mode_age if mode_age is not None else 0.0
        owner = header.get("owner_connection")
        self.input_owner = owner if isinstance(owner, str) else None
        device = header.get("owner_device")
        self.input_owner_device = device if isinstance(device, str) else None
        self.input_owner_epoch = int(header.get("owner_epoch") or 0)
        geometry = header.get("geometry")
        self.geometry = (int(geometry[0]), int(geometry[1])) if isinstance(geometry, list) else None
        viewports = header.get("viewports")
        if isinstance(viewports, dict):
            self.viewports = {
                str(cid): (int(size[0]), int(size[1]))
                for cid, size in viewports.items()
                if isinstance(size, list) and len(size) == 2
            }
        self.attached_connections = int(header.get("attached") or 0)
        self.attachments_seen = int(header.get("attachments_seen") or 0)
        self.input_rejections = int(header.get("rejections") or 0)
        self.input_claim_denials = int(header.get("denials") or 0)
        claims = header.get("claims")
        if isinstance(claims, list):
            self.claim_log.clear()
            self.claim_log.extend(claim for claim in claims if isinstance(claim, dict))

    def apply_writes(self, first: int, writes: list[Any]) -> int:
        """Run the side effects of every write this daemon has not yet applied.

        Returns the acknowledgement: the number below which every write is applied.
        """
        expected = self.next_write_seq
        start = 0
        if expected is not None:
            if first > expected:
                # The gateway dropped writes it held for too long. Their side effects
                # are display evidence and approval cancellation; the composer is
                # the one that can be left wrong, so it reads as typed-in.
                lost = first - expected
                self.writes_lost += lost
                if not self.composer.pending:
                    self.composer.chars = 1
                    self.composer.since = time.time()
                log.warning(
                    "session %s: the terminal gateway dropped %d keystroke write(s) "
                    "before this daemon read them",
                    self.session_id,
                    lost,
                )
            start = max(0, expected - first)
        host = self.host
        for entry in writes[start:]:
            try:
                kind, data, submit = decode_write_entry(entry)
                if kind == WRITE_BYTES:
                    assert isinstance(data, bytes)
                    host.note_typed_bytes(data)
                    continue
                assert isinstance(data, str)
                if kind != WRITE_SUBMIT:
                    host.note_typed_text(data)
                if submit or kind == WRITE_SUBMIT:
                    host.note_owner_submit(data)
            except Exception:
                log.exception("session %s: a keystroke side effect failed", self.session_id)
        upto = first + len(writes)
        self.next_write_seq = max(upto, expected or 0)
        return upto


class RemoteHub:
    """The daemon's connection to the terminal gateway, and every terminal it holds."""

    def __init__(self, *, generation: str = "legacy") -> None:
        self.generation = generation
        self.peer: FramedPeer | None = None
        self.gateway_id: str | None = None
        self.terminals: dict[str, RemoteTerminal] = {}
        self.leader: str | None = None
        self._next_rid = 1
        self._answers: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._tasks: set[asyncio.Task[Any]] = set()
        self.write_failures = 0

    @property
    def connected(self) -> bool:
        return self.peer is not None and not self.peer.closed

    async def connect(self, host: str, port: int, *, token: str) -> None:
        """Connect, then tell the gateway everything it should be serving."""
        peer = await connect(
            host,
            port,
            token=token,
            protocol=GATEWAY_PROTOCOL_VERSION,
            capabilities=GATEWAY_CAPABILITIES,
            hello={"generation": self.generation},
            name="terminal-gateway-client",
            on_notice=self._on_notice,
            on_close=self._on_close,
        )
        if peer.remote_hello.get("protocol") != GATEWAY_PROTOCOL_VERSION:
            await peer.close("protocol mismatch")
            raise HandshakeRefused(
                f"the gateway speaks protocol {peer.remote_hello.get('protocol')!r}, "
                f"this daemon {GATEWAY_PROTOCOL_VERSION}"
            )
        gateway_id = str(peer.remote_hello.get("gateway_id") or "")
        if gateway_id != self.gateway_id:
            # A different gateway process numbers its writes from zero again.
            for terminal in self.terminals.values():
                terminal.next_write_seq = None
        self.gateway_id = gateway_id
        self.peer = peer
        for terminal in self.terminals.values():
            self._upsert(peer, terminal)
        peer.notify({"t": SESSIONS, "sids": sorted(self.terminals)})
        peer.notify({"t": PRESENCE, "leader": self.leader})

    async def close(self) -> None:
        peer, self.peer = self.peer, None
        if peer is not None:
            await peer.close("daemon closing")
        for task in tuple(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

    # -- which terminals the gateway holds -------------------------------------------

    def adopt(
        self,
        *,
        stream: TerminalStream,
        descriptor: TerminalDescriptor,
        host: TerminalHost,
        seed: Any = None,
    ) -> RemoteTerminal:
        """A mirror for one session whose core the gateway is to run."""
        terminal = RemoteTerminal(self, stream=stream, descriptor=descriptor, host=host, seed=seed)
        self.terminals[terminal.session_id] = terminal
        if self.peer is not None and not self.peer.closed:
            self._upsert(self.peer, terminal)
        return terminal

    def release(self, session_id: str) -> None:
        if self.terminals.pop(session_id, None) is not None:
            self._notify({"t": REMOVE, "sid": session_id})

    def set_presence(self, leader: str | None) -> None:
        if leader == self.leader:
            return
        self.leader = leader
        self._notify({"t": PRESENCE, "leader": leader})

    def _upsert(self, peer: FramedPeer, terminal: RemoteTerminal) -> None:
        snapshot, revision = terminal.host.snapshot()
        stream = terminal.stream
        peer.notify(
            {
                "t": UPSERT,
                "descriptor": DescriptorState.of(terminal.descriptor).to_wire(),
                "snapshot": snapshot,
                "revision": revision,
                "max_scrollback": stream.scrollback.max_bytes,
                "attach_replay_bytes": stream.attach_replay_bytes,
                "input_revision": terminal.input_revision,
                "pending_submit": _pending_wire(terminal.pending_submit),
            }
        )

    # -- sending -----------------------------------------------------------------------

    def _notify(self, header: dict[str, Any], payload: bytes = b"") -> bool:
        peer = self.peer
        if peer is None or peer.closed:
            return False
        try:
            peer.notify(header, payload)
        except PeerClosed:
            return False
        return True

    def send_frame(self, terminal: RemoteTerminal, kind: str, frame: dict[str, Any]) -> None:
        self._notify(
            {
                "t": kind,
                "sid": terminal.session_id,
                "frame": frame,
                "descriptor": DescriptorState.of(terminal.descriptor).to_wire(),
            }
        )

    def send_pending_submit(self, terminal: RemoteTerminal) -> None:
        self._notify(
            {
                "t": PENDING_SUBMIT,
                "sid": terminal.session_id,
                "pending_submit": _pending_wire(terminal.pending_submit),
            }
        )

    def _rid(self) -> int:
        rid = self._next_rid
        self._next_rid += 1
        return rid

    def send_write(
        self,
        terminal: RemoteTerminal,
        data: str | bytes,
        *,
        accounted: bool,
        condition: WriteCondition | None,
        rid: int | None = None,
    ) -> int:
        text = isinstance(data, str)
        payload = data.encode("utf-8") if isinstance(data, str) else data
        rid = self._rid() if rid is None else rid
        header = {
            "t": WRITE,
            "sid": terminal.session_id,
            "rid": rid,
            "text": text,
            "accounted": accounted,
            "condition": condition_to_wire(condition),
        }
        if not self._notify(header, payload):
            raise TerminalUnavailable(
                f"the terminal gateway holding session {terminal.session_id} is not connected"
            )
        return rid

    async def write_and_wait(
        self,
        terminal: RemoteTerminal,
        data: str | bytes,
        *,
        accounted: bool,
        condition: WriteCondition | None,
    ) -> None:
        rid = self._rid()
        answer = self._expect(rid)
        try:
            self.send_write(terminal, data, accounted=accounted, condition=condition, rid=rid)
            result = await self._wait(answer, rid, f"write to {terminal.session_id}")
        finally:
            self._answers.pop(rid, None)
        if not result.get("written"):
            reason = str(result.get("reason") or "refused")
            if reason.startswith("write_failed") or reason == "not_hosted":
                raise TerminalUnavailable(f"the gateway could not write: {reason}")
            raise WriteRefused(reason)

    async def repaint(self, terminal: RemoteTerminal) -> bool:
        rid = self._rid()
        answer = self._expect(rid)
        try:
            if not self._notify({"t": REPAINT, "sid": terminal.session_id, "rid": rid}):
                return False
            result = await self._wait(answer, rid, f"repaint of {terminal.session_id}")
        except TerminalUnavailable:
            return False
        finally:
            self._answers.pop(rid, None)
        return bool(result.get("applied"))

    async def diagnostics(self, session_id: str | None = None) -> dict[str, Any] | None:
        peer = self.peer
        if peer is None or peer.closed or CAP_DIAGNOSTICS not in peer.capabilities:
            return None
        header: dict[str, Any] = {"t": DIAGNOSTICS}
        if session_id is not None:
            header["sid"] = session_id
        reply, _ = await peer.request(header, timeout_seconds=REMOTE_ANSWER_TIMEOUT_SECONDS)
        return {key: value for key, value in reply.items() if key not in {"id", "ok"}}

    def _expect(self, rid: int) -> asyncio.Future[dict[str, Any]]:
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._answers[rid] = future
        return future

    async def _wait(
        self, answer: asyncio.Future[dict[str, Any]], rid: int, what: str
    ) -> dict[str, Any]:
        try:
            return await asyncio.wait_for(answer, timeout=REMOTE_ANSWER_TIMEOUT_SECONDS)
        except TimeoutError:
            raise TerminalUnavailable(
                f"the terminal gateway did not answer the {what} in "
                f"{REMOTE_ANSWER_TIMEOUT_SECONDS:g}s"
            ) from None

    # -- receiving -----------------------------------------------------------------------

    def _on_notice(self, peer: FramedPeer, header: dict[str, Any], payload: bytes) -> None:
        kind = header.get("t")
        if kind == STATE:
            self._apply_state(peer, header)
        elif kind in {WRITE_RESULT, REPAINT_RESULT}:
            self._apply_result(header)
        elif kind == EVENT:
            self._apply_event(header)
        elif kind == BROADCAST:
            self._apply_broadcast(header)
        else:
            log.warning("terminal gateway client ignored an unknown notice %r", kind)

    def _apply_state(self, peer: FramedPeer, header: dict[str, Any]) -> None:
        sid = str(header.get("sid"))
        first = header.get("first_write_seq")
        writes = header.get("writes")
        if not isinstance(first, int) or not isinstance(writes, list):
            raise ValueError(f"a state for {sid} carries no writes")
        terminal = self.terminals.get(sid)
        upto = first + len(writes)
        if terminal is not None:
            terminal.merge(header)
            upto = terminal.apply_writes(first, writes)
        # Acknowledged even for a session this daemon no longer serves, so the
        # gateway is never left holding a summary nobody will read.
        try:
            peer.notify({"t": STATE_ACK, "sid": sid, "upto": upto})
        except PeerClosed:
            pass

    def _apply_result(self, header: dict[str, Any]) -> None:
        terminal = self.terminals.get(str(header.get("sid")))
        if terminal is not None and header.get("t") == WRITE_RESULT:
            terminal.merge(header)
        rid = header.get("rid")
        future = self._answers.get(rid) if isinstance(rid, int) else None
        if future is not None:
            if not future.done():
                future.set_result(header)
            return
        if header.get("t") == WRITE_RESULT and not header.get("written"):
            self.write_failures += 1
            log.warning(
                "terminal gateway refused a write to %s: %s",
                header.get("sid"),
                header.get("reason"),
            )

    def _apply_event(self, header: dict[str, Any]) -> None:
        terminal = self.terminals.get(str(header.get("sid")))
        payload = header.get("payload")
        if terminal is None or not isinstance(payload, dict):
            return
        terminal.host.emit(str(header.get("kind")), source=str(header.get("source")), **payload)

    def _apply_broadcast(self, header: dict[str, Any]) -> None:
        terminal = self.terminals.get(str(header.get("sid")))
        data = header.get("data")
        if terminal is not None and isinstance(data, str):
            self._spawn(terminal.host.broadcast(data))

    def _on_close(self, peer: FramedPeer, reason: str) -> None:
        if peer is not self.peer:
            return
        self.peer = None
        for rid, future in tuple(self._answers.items()):
            if not future.done():
                future.set_exception(
                    TerminalUnavailable(f"the terminal gateway connection closed: {reason}")
                )
            self._answers.pop(rid, None)
        log.warning("terminal gateway connection closed: %s", reason)

    def _spawn(self, coroutine: Coroutine[Any, Any, Any]) -> None:
        task = asyncio.get_running_loop().create_task(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def stats(self) -> dict[str, Any]:
        return {
            "connected": self.connected,
            "gateway_id": self.gateway_id,
            "terminals": len(self.terminals),
            "write_failures": self.write_failures,
            "peer": self.peer.stats() if self.peer is not None else None,
        }


def _pending_wire(value: PendingSubmit | None) -> dict[str, Any] | None:
    if value is None:
        return None
    return {"message_id": value.message_id, "at": value.at, "byte_count": value.byte_count}


__all__ = [
    "REMOTE_ANSWER_TIMEOUT_SECONDS",
    "RemoteHub",
    "RemoteTerminal",
    "TerminalUnavailable",
]
