"""The terminal and event WebSockets."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import secrets
import time
from contextlib import suppress
from typing import Any

from aiohttp import WSMsgType, web

from .. import (
    app_keys as keys,
)
from ..device_presence import DevicePresenceStore, parse_device_report
from ..event_bus import EventBus
from ..harness import (
    needs_resize_repaint,
    repaints_scrollback,
    replay_needs_repaint,
    suppresses_late_color_response,
)
from ..network_usage import (
    MeteredWebSocketResponse,
    metered_websocket,
)
from ..observation import (
    cancel_pending_approval,
    note_interrupt_intent,
)
from ..session import (
    PtySubscriber,
    Session,
    note_remote_shell_submission,
    session_is_unwitnessed,
)
from ..terminal_arbitration import ClaimReason, ClaimRequest, evaluate_claim
from ..ui_build import read_ui_build_id
from . import terminal

log = logging.getLogger(__name__)


PTY_ATTACH_READY_TIMEOUT_SECONDS = 0.25


# How long a connection's repeated passive claims go unanswered after a refusal. The
# reply is what an older client re-claims on, so answering every one is what turns a
# refusal into a loop.
REFUSED_CLAIM_COOLDOWN_SECONDS = 1.0


# Browser reconnects use a small recovery window. Wider gaps fall back to one
# authoritative REST refresh instead of replaying a large, stale event history.
EVENTS_CATCHUP_LIMIT = 64


# These hook lifecycle records remain durable for diagnostics, but browser state
# does not consume their large payloads. User-visible state changes arrive as
# separate, compact events.
BROWSER_OMITTED_EVENT_TYPES = frozenset({"PreToolUse", "PostToolUse", "tool_use", "tool_result"})


_OSC_DEFAULT_COLOR_RESPONSE = re.compile(
    r"(?:\x1b\](?:10|11);"
    r"(?:rgb:[0-9a-f]{1,4}(?:/[0-9a-f]{1,4}){2}"
    r"|rgba:[0-9a-f]{1,4}(?:/[0-9a-f]{1,4}){3})"
    r"(?:\x07|\x1b\\))+",
    re.IGNORECASE,
)


# Mouse reports, in the three encodings a browser terminal can emit: SGR (1006,
# what xterm.js sends and by far the common case), urxvt (1015), and X10, whose
# three payload bytes are raw and so must not be constrained to digits.
_MOUSE_REPORT_BODY = (
    r"\x1b\[(?:<(\d{1,4});\d{1,5};\d{1,5}[Mm]"
    r"|(\d{1,4});\d{1,5};\d{1,5}M"
    r"|M([\x20-\xff]{3}))"
)


_MOUSE_REPORT = re.compile(_MOUSE_REPORT_BODY)


_MOUSE_REPORT_ONLY = re.compile(f"(?:{_MOUSE_REPORT_BODY})+")


def _is_suppressed_color_response(backend: str, data: str) -> bool:
    """Reject a late OSC 10/11 reply before the CLI mistakes it for prompt input.

    Gated on the descriptor rather than the name: a harness whose startup palette
    probe accepts these replies for only a bounded interval declares
    `suppresses_late_color_response`, and browser/WS latency can deliver an
    otherwise valid xterm reply after that probe has ended.
    """
    return (
        suppresses_late_color_response(backend)
        and _OSC_DEFAULT_COLOR_RESPONSE.fullmatch(data) is not None
    )


def pointer_report_kind(data: str) -> str | None:
    """Classify a payload that consists only of mouse reports.

    ``"motion"`` for a pointer that merely moved across the pane, ``"button"``
    for a press, release, or wheel notch, and None for anything that is not
    purely mouse reports.

    This matters because these arrive on the same channel as typing. xterm hands
    every mouse report to `onData` once the child enables tracking, so a pointer
    crossing an agent's pane produced ~8 "keystrokes" a second — enough to keep
    `input_revision` climbing forever, which delivery readiness reads as *the
    operator has half-typed something into the composer*. A mouse report cannot
    put text in a composer, so it must not advance that revision; a motion report
    is not even presence, since the pointer can cross a pane on its way somewhere
    else.
    """
    if not data or not _MOUSE_REPORT_ONLY.fullmatch(data):
        return None
    for match in _MOUSE_REPORT.finditer(data):
        sgr, urxvt, x10 = match.groups()
        # X10 offsets every field by 32 so the report stays printable.
        button = int(sgr or urxvt) if sgr or urxvt else ord(x10[0]) - 32
        # Bit 5 is the motion flag. Wheel notches set bit 6 instead, and those
        # are a deliberate act, so they count as presence like a click does.
        if not button & 32:
            return "button"
    return "motion"


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


async def pty_ws(request: web.Request) -> web.WebSocketResponse:
    session = request.app[keys.SESSIONS].resolve(request.match_info["sid"])
    generation = request.app.get(keys.DAEMON_GENERATION)
    snapshot_generation = generation or "legacy"
    connection_id = secrets.token_urlsafe(12)
    ws = metered_websocket(request, "pty", heartbeat=20, max_msg_size=2 * 1024 * 1024)
    await ws.prepare(request)
    allow_terminal_responses = (
        session.attachments_seen == 0
        and session.record.state not in {"exited", "crashed"}
        and time.time() - session.record.created_at <= 5
    )
    session.attachments_seen += 1
    output_flow = PtyOutputFlow()
    # Everything after the subscribe runs inside the try, so no path can exit
    # without unsubscribing. A mid-replay disconnect (a slow mobile link is the
    # realistic case) used to orphan the subscriber, permanently marking the
    # session "attended" — which suppresses unattended-attention automation and
    # fleet absence reporting for that session's whole lifetime.
    sender_task: asyncio.Task[None] | None = None
    subscriber: PtySubscriber | None = None
    try:
        pending_messages: list[Any] = []
        attach_deadline = asyncio.get_running_loop().time() + PTY_ATTACH_READY_TIMEOUT_SECONDS
        attach_closed = False
        geometry_queued = False
        # The ring position this client claims to have parsed up to, from its
        # `attach_ready` frame. The handshake therefore runs *before* the replay
        # snapshot — the frame decides whether this attach is a delta or a window —
        # which is also why the snapshot/subscribe moved after this loop: taken
        # before it, output arriving during the handshake would be neither in the
        # replay nor in the subscription.
        attach_since: int | None = None
        # unsupervised-loop-ok: bounded attach handshake for one websocket.
        while True:
            remaining = attach_deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                break
            try:
                initial_message = await asyncio.wait_for(ws.receive(), timeout=remaining)
            except TimeoutError:
                break
            if initial_message.type == WSMsgType.TEXT:
                initial_frame = json.loads(initial_message.data)
                if initial_frame.get("type") in {"attach_ready", "resize"}:
                    if initial_frame.get("output_flow_control") is True:
                        output_flow.enable()
                    since_value = initial_frame.get("since")
                    if isinstance(since_value, int) and not isinstance(since_value, bool):
                        attach_since = since_value
                    geometry_queued = _apply_client_viewport(session, connection_id, initial_frame)
                    # A repaint-heavy TUI can wrap the retained ring until this
                    # attach's replay holds no transcript at all. The recovery is
                    # client-requested (a `repaint` frame once the parsed replay
                    # provably produced no scrollback, `_handle_client_repaint`)
                    # rather than pulsed here: only the client can see whether the
                    # replay was sufficient, and an unconditional attach pulse made
                    # the child restate its whole transcript on every healthy
                    # cold attach too — while covering neither hidden warm-mount
                    # attaches nor their later reveal, the path users actually hit.
                    break
                pending_messages.append(initial_message)
            elif initial_message.type in {WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.ERROR}:
                attach_closed = True
                break
            else:
                pending_messages.append(initial_message)

        if attach_closed:
            return ws

        snapshot, revision, replay_kind, replay, ring_position, subscriber = (
            session.attach_and_subscribe(attach_since)
        )
        request.app[keys.EVENTS].emit_background(
            "terminal_attached",
            session_id=session.record.id,
            source="daemon",
            connections=len(session.subscribers),
        )
        await ws.send_json(
            _versioned_pty_frame(
                {"type": "state", "snapshot": snapshot, "revision": revision},
                snapshot_generation,
            )
        )
        await ws.send_json(
            {
                "type": "replay_start",
                "reason": replay_kind,
                # A delta lands in a terminal that already answered (or missed) any
                # query in it once; replaying the answer would be stale.
                "allow_terminal_responses": (
                    allow_terminal_responses if replay_kind == "attach" else False
                ),
            }
        )
        if replay:
            # Count attach replay too. Its parse acknowledgement may arrive after
            # live sending starts; leaving it uncounted would let that acknowledgement
            # erase credit belonging to newer live output.
            output_flow.sent(len(replay))
            await ws.send_bytes_classified(
                replay, "attach_replay" if replay_kind == "attach" else "delta_replay"
            )
        # `position` anchors the client's byte cursor: this position plus every live
        # byte it receives afterwards is what it may offer as `since` next time.
        await ws.send_json(
            {"type": "replay_end", "reason": replay_kind, "position": ring_position}
        )
        # The arbitrated size can differ from this client's own fit (another device owns
        # input), so tell it up front rather than letting it render at the wrong width
        # until something happens to change the geometry. Skipped when this attach
        # already changed the size, since that queued a frame for every client.
        if not geometry_queued:
            await ws.send_json(session.geometry_frame())
        if snapshot["state"] in {"exited", "crashed"}:
            await ws.send_json(
                _versioned_pty_frame(
                    {
                        "type": "exit",
                        "snapshot": snapshot,
                        "revision": revision,
                        "reason": "already_ended",
                    },
                    snapshot_generation,
                )
            )
            return ws
        sender_task = asyncio.create_task(
            _pty_sender(ws, session, subscriber, snapshot_generation, output_flow)
        )
        # The replay just sent may have been a window over a differential frame stream,
        # which reconstructs to whichever cells happened to change inside it. One pulse
        # makes the child restate the whole screen. A delta needs none: the client's
        # terminal kept its state and the delta is the stream itself, so after the
        # append its screen is exact — pulsing here would make every tab switch cost
        # the child a repaint.
        if replay_kind == "attach":
            _schedule_attach_repaint(request, session)
        for pending_message in pending_messages:
            await _handle_pty_client_message(
                request, ws, session, connection_id, pending_message, output_flow
            )
        async for message in ws:
            await _handle_pty_client_message(
                request, ws, session, connection_id, message, output_flow
            )
    finally:
        # Every synchronous cleanup runs before the sender task is awaited. A handler
        # cancelled on peer disconnect re-raises at the first await inside its own
        # finally, which used to skip the unsubscribe and the ownership release
        # entirely — leaving the session reported as attended forever and its input
        # owned by a socket that no longer exists.
        released = session.release_input_owner(connection_id)
        session.drop_viewport(connection_id)
        session.claim_refusals.pop(connection_id, None)
        if subscriber is not None:
            session.unsubscribe(subscriber)
        # A detach can hand geometry back to whoever is left: the phone closing its tab
        # returns the PTY to the desktop's width — a width change like any other, and
        # one nobody is dragging, so the screen it lands on has to be repaired too.
        if session.apply_geometry():
            _schedule_resize_repaint(request, session)
        if released:
            session.publish_control(
                {
                    "type": "input_owner_released",
                    "epoch": session.input_owner_epoch,
                }
            )
        request.app[keys.EVENTS].emit_background(
            "terminal_detached",
            session_id=session.record.id,
            source="daemon",
            connections=len(session.subscribers),
        )
        if sender_task is not None:
            sender_task.cancel()
            await asyncio.gather(sender_task, return_exceptions=True)
    return ws


def _versioned_pty_frame(frame: dict[str, Any], generation: str) -> dict[str, Any]:
    """Attach the same ordering contract used by the REST fleet snapshot.

    PTY frames deliberately remain presentation-unenriched. The browser merger
    preserves generated titles from an enriched REST snapshot while applying
    newer core state from this channel.
    """
    snapshot = frame.get("snapshot")
    if not isinstance(snapshot, dict):
        return frame
    result = dict(frame)
    result["snapshot"] = {
        **snapshot,
        "_snapshot_generation": generation,
        "_snapshot_revision": int(frame.get("revision") or 0),
        "_snapshot_enriched": False,
    }
    return result


async def _pty_sender(
    ws: MeteredWebSocketResponse,
    session: Session,
    subscriber: Any,
    generation: str,
    output_flow: PtyOutputFlow,
) -> None:
    pending_message: Any | None = None
    # unsupervised-loop-ok: lives for one PTY websocket, not the daemon.
    while True:
        if pending_message is None:
            message = await subscriber.queue.get()
        else:
            message = pending_message
            pending_message = None
        if isinstance(message, bytes):
            chunks = [message]
            byte_count = len(message)
            # Drain only output that is already waiting. This reduces websocket
            # frame overhead without delaying the first byte or crossing a control
            # frame, whose ordering relative to output is significant.
            while byte_count < PTY_OUTPUT_BATCH_BYTES:
                try:
                    queued = subscriber.queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                if not isinstance(queued, bytes):
                    pending_message = queued
                    break
                chunks.append(queued)
                byte_count += len(queued)
            payload = message if len(chunks) == 1 else b"".join(chunks)
            await output_flow.wait_for_credit()
            # Reserve before the await. The receiver task can process a fast browser's
            # acknowledgement while send_bytes is yielding; counting afterward would
            # turn that valid ACK into phantom unacknowledged credit.
            output_flow.sent(len(payload))
            await ws.send_bytes_classified(payload, "live_output")
        elif message.get("type") == "resync":
            (
                dropped_bytes,
                dropped_chunks,
                replay_bytes,
                current,
                current_revision,
                ring_position,
                exit_frame,
            ) = session.take_resync(subscriber)
            await ws.send_json(
                {
                    "type": "gap",
                    "dropped_bytes": dropped_bytes,
                    "dropped_chunks": dropped_chunks,
                }
            )
            await ws.send_json({"type": "replay_start", "reason": "resync"})
            if replay_bytes:
                await output_flow.wait_for_credit()
                output_flow.sent(len(replay_bytes))
                await ws.send_bytes_classified(replay_bytes, "resync_replay")
            # Re-anchor the client's byte cursor: the drop broke its count, and a
            # stale cursor would cost every later reconnect a full replay.
            await ws.send_json(
                {"type": "replay_end", "reason": "resync", "position": ring_position}
            )
            await ws.send_json(
                _versioned_pty_frame(
                    {"type": "update", "snapshot": current, "revision": current_revision},
                    generation,
                )
            )
            # Control frames are skipped while a subscriber is resyncing, so restate the
            # arbitrated geometry here rather than leaving this client sized by whatever
            # it last heard before the gap.
            await ws.send_json(session.geometry_frame())
            if exit_frame:
                await ws.send_json(_versioned_pty_frame(exit_frame, generation))
                return
        else:
            await ws.send_json(_versioned_pty_frame(message, generation))
            if message.get("type") == "exit":
                return


def _apply_client_viewport(session: Session, connection_id: str, frame: dict[str, Any]) -> bool:
    """Register one client's fitted size and re-arbitrate the shared PTY geometry.

    A client that reports itself hidden is deregistered instead: a minimized desktop
    pane still has layout, and its `resize` frames used to rewrap the agent TUI to
    desktop width on whatever device the user was actually holding.

    Returns whether the arbitrated size changed, which also means every attached
    client (including this one) has a `geometry` frame queued.
    """
    session.set_viewport(
        connection_id,
        int(frame["cols"]),
        int(frame["rows"]),
        hidden=bool(frame.get("hidden")),
    )
    return session.apply_geometry()


def _owner_frame(session: Session, *, active: bool, reason: str) -> dict[str, Any]:
    return {
        "type": "input_owner",
        "active": active,
        "epoch": session.input_owner_epoch,
        "reason": reason,
        "owner_device": session.input_owner_device,
    }


def _note_input_rejected(request: web.Request, session: Session, byte_count: int) -> None:
    """Count input refused from a non-owner. Silent drops left this failure invisible:
    the user saw missing characters and there was nothing in telemetry to correlate."""
    session.input_rejections += 1
    now = time.monotonic()
    if now - session.last_input_reject_report_ts < 2:
        return
    session.last_input_reject_report_ts = now
    request.app[keys.EVENTS].emit_background(
        "terminal_input_rejected",
        session_id=session.record.id,
        source="daemon",
        owner_device=session.input_owner_device,
        bytes=byte_count,
        rejections=session.input_rejections,
    )


async def _claim_terminal_input(
    request: web.Request,
    ws: web.WebSocketResponse,
    session: Session,
    connection_id: str,
    frame: dict[str, Any],
) -> None:
    reason: ClaimReason = "passive" if frame.get("reason") == "passive" else "gesture"
    device = str(frame.get("device") or "unknown")[:32]
    # Which device the human is at is a property of the whole app, not of this
    # session, so it comes from the daemon's presence tracking rather than from the
    # claiming client. Absent (older client, or a test app that wires no store) means
    # "no signal", which leaves the per-session rules to decide exactly as before.
    presence: DevicePresenceStore | None = request.app.get(keys.DEVICE_PRESENCE)
    # The device class whose human touched it most recently. Not "is any other device
    # active": a desktop left open and focused stays active for two minutes after the
    # last keystroke, so both classes are active exactly when someone picks up their
    # phone — and treating that as contention left every session with the desktop.
    leader = presence.leading_profile() if presence is not None else None
    decision = evaluate_claim(
        session.owner_state(),
        ClaimRequest(
            connection_id=connection_id,
            now=time.monotonic(),
            reason=reason,
            device=device,
            # Absent on legacy clients, which only claimed on real interaction.
            focused=frame.get("focused") is not False,
            other_device_in_use=leader is not None and leader != device,
            this_device_in_use=leader == device,
        ),
    )
    session.claim_log.append(
        {
            "ts": time.time(),
            "device": device,
            "ask": reason,
            "focused": frame.get("focused") is not False,
            "leader": leader,
            "owner_device": session.input_owner_device,
            "verdict": decision.reason,
            "granted": decision.granted,
        }
    )
    if not decision.granted:
        session.input_claim_denials += 1
        now = time.monotonic()
        refused_at = session.claim_refusals.get(connection_id, 0.0)
        session.claim_refusals[connection_id] = now
        if reason == "passive" and now - refused_at < REFUSED_CLAIM_COOLDOWN_SECONDS:
            # A client that re-claims on its own refusal loops as fast as the round
            # trip; one live session logged 7566 refused claims that way. Newer
            # clients no longer do it, but a cached build still can, and the answer
            # it would act on is the one already sent. Stay silent instead.
            return
        await ws.send_json(_owner_frame(session, active=False, reason=decision.reason))
        return
    displaced = session.input_owner_socket if decision.changed else None
    session.apply_owner_state(decision.state)
    session.input_owner_socket = ws
    await ws.send_json(_owner_frame(session, active=True, reason=decision.reason))
    if displaced is not None:
        # Losing ownership used to be silent, so a desktop pane that still had DOM
        # focus kept typing into a session the phone had claimed and every keystroke
        # was dropped with no feedback — the terminal just looked hung.
        with suppress(ConnectionResetError, RuntimeError, ValueError):
            await displaced.send_json(
                _owner_frame(session, active=False, reason="claimed_elsewhere")
            )
    if decision.changed:
        # The device a human is typing into dictates the size everyone else renders.
        if session.apply_geometry():
            _schedule_resize_repaint(request, session)
        request.app[keys.EVENTS].emit_background(
            "terminal_input_owner",
            session_id=session.record.id,
            source="daemon",
            device=session.input_owner_device,
            epoch=session.input_owner_epoch,
            grant=decision.reason,
            denials=session.input_claim_denials,
        )


async def _handle_terminal_input(
    request: web.Request,
    ws: web.WebSocketResponse,
    session: Session,
    connection_id: str,
    frame: dict[str, Any],
) -> None:
    data = str(frame.get("data", ""))
    raw_input_seq = frame.get("input_seq")
    input_seq = (
        raw_input_seq
        if isinstance(raw_input_seq, int)
        and not isinstance(raw_input_seq, bool)
        and 0 < raw_input_seq <= 2_147_483_647
        else None
    )
    if _is_suppressed_color_response(session.record.backend, data):
        return
    is_terminal_response = frame.get("kind") == "terminal_response"
    if session.input_owner != connection_id:
        # xterm device replies belong to the probe that asked for them, so a rejected
        # one is discarded rather than echoed back for replay: re-sending it late is
        # worse than losing it. Human input is echoed back so the client can re-claim
        # and resend the exact keystrokes instead of losing them to a lost race.
        if not is_terminal_response:
            _note_input_rejected(request, session, len(data.encode("utf-8")))
            await ws.send_json(
                {
                    "type": "input_rejected",
                    "epoch": session.input_owner_epoch,
                    "owner_device": session.input_owner_device,
                    "data": data,
                    "broadcast": bool(frame.get("broadcast")),
                    "retry": bool(frame.get("retry")),
                    **({"input_seq": input_seq} if input_seq is not None else {}),
                }
            )
        return
    if not terminal.session_accepts_input(session):
        # An ended or cold pane is read-only. Answered rather than dropped, so a
        # client typing into one learns why instead of watching characters vanish.
        await ws.send_json({"type": "input_refused", "reason": "session_ended"})
        return
    server_received_at_ms = int(time.time() * 1000)
    session.pty.write(data)
    now = time.monotonic()
    pointer = pointer_report_kind(data)
    if not is_terminal_response and pointer is None:
        cancel_pending_approval(session, "terminal_input")
        session.input_revision += 1
        note_remote_shell_submission(session, data)
        terminal._note_composer_write(request.app[keys.EVENTS], session, data, "browser")
        note_interrupt_intent(session, data, source="terminal_input")
        session.last_input_event_ts = now
        # Typing is the strongest evidence of where the human is; it renews this
        # connection's protection from a background pane's passive re-claim.
        session.note_owner_input(now)
        if (
            session.record.state == "idle"
            and session_is_unwitnessed(session)
            and ("\r" in data or "\n" in data)
        ):
            # A retained spinner may predate this process or prompt. Only a
            # submit from the current input owner licenses the PTY-only first-turn
            # fallback to interpret a subsequent working frame as new work.
            session.observation_state["unwitnessed_turn_armed"] = True
            session.state_transitions.append(
                {
                    "ts": time.time(),
                    "kind": "unwitnessed_turn_armed",
                    "source": "terminal_input",
                }
            )
            request.app[keys.EVENTS].emit_background(
                "unwitnessed_turn_armed",
                session_id=session.record.id,
                source="terminal_input",
            )
    elif pointer == "button":
        # A click or a wheel notch is the human being here, but it puts no text in
        # the composer, so it moves the presence clock and not the input revision.
        session.last_input_event_ts = now
        session.note_owner_input(now)
    if not is_terminal_response and pointer is None and now - session.last_input_report_ts >= 2:
        session.last_input_report_ts = now
        input_diagnostic: dict[str, Any] = {}
        if input_seq is not None:
            input_diagnostic["input_seq"] = input_seq
            for key, maximum in (
                ("client_sent_at_ms", 10**15),
                ("client_event_delay_ms", 10 * 60 * 1000),
                ("client_queue_delay_ms", 10 * 60 * 1000),
                ("ws_buffered_bytes", 2**31 - 1),
            ):
                value = frame.get(key)
                if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= maximum:
                    input_diagnostic[key] = value
            input_source = frame.get("input_source")
            if input_source in {"beforeinput", "input", "keydown", "paste"}:
                input_diagnostic["input_source"] = input_source
            input_diagnostic["server_received_at_ms"] = server_received_at_ms
        request.app[keys.EVENTS].emit_background(
            "terminal_input",
            session_id=session.record.id,
            source="daemon",
            input_owner=True,
            bytes=len(data.encode("utf-8")),
            **input_diagnostic,
        )
    if input_seq is not None and not is_terminal_response and pointer is None:
        # The acknowledgement marks daemon receipt, not terminal echo. It carries no
        # input content and is optional telemetry, so a disconnect after the PTY write
        # must not turn an accepted keystroke into a handler failure.
        with suppress(ConnectionResetError, RuntimeError):
            await ws.send_json(
                {
                    "type": "input_ack",
                    "input_seq": input_seq,
                    "server_received_at_ms": server_received_at_ms,
                }
            )
    if frame.get("broadcast") and not is_terminal_response:
        await terminal.deliver_broadcast(
            request.app[keys.SESSIONS],
            data,
            request.app[keys.EVENTS],
            source_id=session.record.id,
        )


# A `repaint` frame makes the child restate its whole transcript (~100 KB+ for a deep
# session), so back-to-back requests — several panes of one session finishing replay
# together, or a reconnect storm — collapse into one restatement they all receive. The
# same window bounds the attach pulse an alternate-screen session takes
# (`_schedule_attach_repaint`), whose restatement is one screen rather than a transcript.
CLIENT_REPAINT_MIN_INTERVAL_SECONDS = 2.0


# How long the arbitrated geometry must hold still before an alternate-screen child is
# pulsed into restating its screen. Long enough that a drag (which emits changes at
# frame rate) settles first and costs one pulse rather than hundreds, short enough that
# a corrupt screen is not something the user has time to start working around.
RESIZE_REPAINT_SETTLE_SECONDS = 0.25


# Client repair events persisted to the durable event log. An allowlist rather than a
# free phase field: the browser page is same-user but still untrusted input, and the
# log must stay enumerable for the "which repairs still fire in production" question.
CLIENT_REPAIR_PHASES = frozenset(
    {
        "write_pipeline_dead",
        "write_pipeline_backlog",
        "surface_drift_repair",
        "viewport_fit_drift_repair",
        "viewport_fit_resumed",
        "surface_repair_resumed",
        "scrollback_repaint_requested",
        "webgl_render_error",
    }
)


CLIENT_INPUT_DIAGNOSTIC_PHASES = frozenset(
    {
        "input_ack_latency",
        "input_echo_latency",
        "input_event_delay",
        "input_main_thread_stall",
        "input_socket_backlog",
        # A deliberate mobile vertical drag that moved nothing. The symptom ("swiping does
        # nothing") names no layer, and the drag's four destinations — peek pan, xterm
        # scrollback, a forwarded wheel, or `disabled` — are indistinguishable from outside
        # when they fail. The report carries which one it took and what the pane believed.
        "mobile_drag_inert",
        # A mobile gesture that was not a tap left the soft keyboard in a different state
        # than it found it. The pane holds that as an invariant in both directions and
        # reports its own violations, because the symptom is unfalsifiable from outside:
        # "it sometimes opens the keyboard" names neither a layer nor a gesture. The report
        # carries the direction, whether it was a selection, and how long the hold was —
        # duration being the discriminator, since the compat-mouse suppression window that
        # used to leak was really a cap on how long a deliberate hold was allowed to be.
        "mobile_gesture_keyboard_changed",
    }
)


CLIENT_DIAGNOSTIC_MIN_INTERVAL_SECONDS = 1.0


CLIENT_DIAGNOSTIC_DETAIL_LIMIT = 512


# The paste trace (frontend pasteTrace.ts) deliberately carries bounded pasted-content
# evidence — a head/tail excerpt, flagged codepoints, and two composer snapshots — because
# the payload's invisible characters ARE the diagnosis it exists for. It therefore
# persists as its own event type instead of joining the content-free
# `terminal_input_diagnostic` phases, and its clamp is sized for the two snapshots.
CLIENT_PASTE_TRACE_PHASE = "terminal_paste_trace"


CLIENT_PASTE_TRACE_DETAIL_LIMIT = 4096


async def _repaint_when_resize_settles(request: web.Request, session: Session) -> None:
    """Wait out a resize gesture, then make the child restate its screen once.

    Trailing edge, not leading: pulsing while the pointer is still moving would repaint
    a size the user has already dragged past, and the screen that has to end up correct
    is the one they stop on. The deadline is re-read every wake rather than captured,
    so a gesture that outlives the first sleep extends this task instead of starting a
    second one.
    """
    # unsupervised-loop-ok: one settle window for one session's resize gesture, and it
    # can only extend while that gesture keeps pushing the deadline forward.
    while True:
        remaining = session.resize_repaint_deadline - time.monotonic()
        if remaining <= 0:
            break
        await asyncio.sleep(remaining)
    if session.stopping:
        return
    size = session.geometry
    repainted = await session.repaint_current_geometry()
    request.app[keys.EVENTS].emit_background(
        "terminal_repaint_requested",
        session_id=session.record.id,
        source="daemon",
        reason="resize_settled",
        backend=session.record.backend,
        cols=size[0] if size else None,
        rows=size[1] if size else None,
        applied=repainted,
    )


def _schedule_resize_repaint(request: web.Request, session: Session) -> None:
    """Arm (or extend) the trailing repaint for a harness that cannot self-repair.

    Called on every arbitrated geometry change, so it must stay cheap and idempotent:
    the common case is pushing a float forward while a task already waits on it.
    """
    if not needs_resize_repaint(session.record.backend):
        return
    session.resize_repaint_deadline = time.monotonic() + RESIZE_REPAINT_SETTLE_SECONDS
    pending = session.resize_repaint_task
    if pending is not None and not pending.done():
        return
    task = asyncio.create_task(
        _repaint_when_resize_settles(request, session),
        name=f"resize-repaint-{session.record.id}",
    )
    session.resize_repaint_task = task
    session.tasks.add(task)
    task.add_done_callback(session.tasks.discard)


async def _repaint_after_truncated_replay(request: web.Request, session: Session) -> None:
    repainted = await session.repaint_current_geometry()
    request.app[keys.EVENTS].emit_background(
        "terminal_repaint_requested",
        session_id=session.record.id,
        source="daemon",
        reason="truncated_replay",
        backend=session.record.backend,
        applied=repainted,
    )


def _schedule_attach_repaint(request: web.Request, session: Session) -> None:
    """Restate an alternate-screen child's screen when its replay was only a window.

    Unconditional on truncation rather than on any judgement about what the bytes
    parsed to, because for this harness class there is nothing to judge: a slice of a
    differential frame stream is complete only if a full repaint happened to fall
    inside it (`replay_needs_repaint`), and neither end can see whether one did. The
    client cannot either — the signal that serves the normal-screen case, "the parse
    produced no scrollback", is meaningless against a buffer that has none by design,
    which is why `_handle_client_repaint` refuses these sessions and why they were left
    with no attach-time repair at all. Their only repaint path was a geometry *change*,
    so the workaround was to resize the window by hand.

    The cost the retired OMP attach pulse was carrying does not transfer: that pulse
    bought a ~460-line transcript restatement on every healthy cold attach, while an
    alternate-screen child restates one screen. Deliberately not conditioned on
    `hidden` — a warm pane mass-mounted after a Reload UI parses into its buffer while
    its rendering is paused, so the reveal it is heading for finds a whole screen.

    Ordering is safe wherever this is called from: the pulse's bytes reach this client
    through the subscriber queue registered before the replay snapshot, so they are
    delivered after the replay rather than painted over by it.
    """
    if not replay_needs_repaint(session.record.backend):
        return
    if not session.replay_window_truncated():
        return
    now = time.monotonic()
    if now - session.last_client_repaint_ts < CLIENT_REPAINT_MIN_INTERVAL_SECONDS:
        return
    # Shares the stamp with `_handle_client_repaint`: both are a browser causing one
    # restatement, and a reconnect storm (desktop and phone returning together) must
    # cost the session one pulse rather than one per socket.
    session.last_client_repaint_ts = now
    task = asyncio.create_task(
        _repaint_after_truncated_replay(request, session),
        name=f"attach-repaint-{session.record.id}",
    )
    session.tasks.add(task)
    task.add_done_callback(session.tasks.discard)


async def _handle_client_repaint(request: web.Request, session: Session) -> None:
    """A client whose parsed replay produced no scrollback asks the child to restate it.

    Only the client can make that judgement: the daemon sees bytes, not what they parse
    to. Gated on the harness trait rather than the session name — a transcript-in-
    scrollback TUI that repaints its live region is both the only case that can wrap
    the ring into an empty-looking replay and the only one that answers a width pulse
    by re-rendering its transcript. Alternate-screen TUIs (Claude) never qualify:
    their buffer has no scrollback by design, so a client-side request keyed on
    missing scrollback would fire forever. Their own version of this problem — a
    windowed replay of a differential frame stream — is repaired without asking the
    client anything, in `_schedule_attach_repaint`.
    """
    if not repaints_scrollback(session.record.backend):
        return
    now = time.monotonic()
    if now - session.last_client_repaint_ts < CLIENT_REPAINT_MIN_INTERVAL_SECONDS:
        return
    # Stamped before the await so concurrent requests cannot interleave two pulses.
    session.last_client_repaint_ts = now
    repainted = await session.repaint_current_geometry()
    if repainted:
        request.app[keys.EVENTS].emit_background(
            "terminal_repaint_requested",
            session_id=session.record.id,
            source="browser",
            reason="missing_scrollback",
        )


def _handle_client_diagnostic(
    request: web.Request,
    session: Session,
    connection_id: str,
    frame: dict[str, Any],
) -> None:
    """Persist a bounded client-side terminal diagnostic to the durable event log.

    The repair layers (terminalHealth.ts and TerminalPane's fit/surface debt) fire in
    production browsers, where the opt-in in-page ring buffer is invisible and lost on
    reload. Recording each firing durably is what makes the layers individually
    auditable — a layer that never fires in months of logs is a removal candidate,
    one that fires daily is load-bearing. Rate-limited per session because the log is
    SQLite-backed; the question this answers needs existence and rough frequency, not
    an exact count.
    """
    phase = frame.get("phase")
    if not isinstance(phase, str) or phase not in (
        CLIENT_REPAIR_PHASES | CLIENT_INPUT_DIAGNOSTIC_PHASES | {CLIENT_PASTE_TRACE_PHASE}
    ):
        return
    now = time.monotonic()
    last_report = session.client_diagnostic_timestamps.get(phase, 0.0)
    if now - last_report < CLIENT_DIAGNOSTIC_MIN_INTERVAL_SECONDS:
        return
    session.client_diagnostic_timestamps[phase] = now
    detail = frame.get("detail")
    payload = json.dumps(detail) if isinstance(detail, dict) else ""
    if phase == CLIENT_PASTE_TRACE_PHASE:
        event_type = CLIENT_PASTE_TRACE_PHASE
        detail_limit = CLIENT_PASTE_TRACE_DETAIL_LIMIT
    elif phase in CLIENT_REPAIR_PHASES:
        event_type = "terminal_client_repair"
        detail_limit = CLIENT_DIAGNOSTIC_DETAIL_LIMIT
    else:
        event_type = "terminal_input_diagnostic"
        detail_limit = CLIENT_DIAGNOSTIC_DETAIL_LIMIT
    request.app[keys.EVENTS].emit_background(
        event_type,
        session_id=session.record.id,
        source="browser",
        phase=phase,
        detail=payload[:detail_limit],
        input_owner=session.input_owner == connection_id,
        owner_device=session.input_owner_device,
    )


async def _handle_pty_client_message(
    request: web.Request,
    ws: web.WebSocketResponse,
    session: Session,
    connection_id: str,
    message: Any,
    output_flow: PtyOutputFlow,
) -> None:
    if message.type == WSMsgType.BINARY:
        # No current client sends binary input; a non-owner's bytes are counted and
        # dropped rather than echoed back, since there is no frame shape to replay.
        if session.input_owner != connection_id:
            _note_input_rejected(request, session, len(message.data))
            return
        if not terminal.session_accepts_input(session):
            await ws.send_json({"type": "input_refused", "reason": "session_ended"})
            return
        session.pty.write(message.data)
        now = time.monotonic()
        session.input_revision += 1
        note_remote_shell_submission(session, message.data)
        terminal._note_composer_write(request.app[keys.EVENTS], session, message.data, "browser")
        session.last_input_event_ts = now
        session.note_owner_input(now)
        if now - session.last_input_report_ts >= 2:
            session.last_input_report_ts = now
            request.app[keys.EVENTS].emit_background(
                "terminal_input",
                session_id=session.record.id,
                source="daemon",
                input_owner=True,
                bytes=len(message.data),
            )
    elif message.type == WSMsgType.TEXT:
        frame = json.loads(message.data)
        if frame.get("type") == "claim_input":
            await _claim_terminal_input(request, ws, session, connection_id, frame)
        elif frame.get("type") == "input":
            await _handle_terminal_input(request, ws, session, connection_id, frame)
        elif frame.get("type") == "terminal_state" and session.input_owner == connection_id:
            mode = str(frame.get("mode") or "")
            if mode not in {"normal", "alternate"}:
                raise ValueError("terminal mode must be normal or alternate")
            changed = session.terminal_mode != mode
            session.terminal_mode = mode
            session.terminal_mode_updated_at = time.monotonic()
            if changed:
                request.app[keys.EVENTS].emit_background(
                    "terminal_mode_changed",
                    session_id=session.record.id,
                    source="browser",
                    mode=mode,
                )
        elif frame.get("type") in {"attach_ready", "resize"}:
            if _apply_client_viewport(session, connection_id, frame):
                _schedule_resize_repaint(request, session)
        elif frame.get("type") == "repaint":
            await _handle_client_repaint(request, session)
        elif frame.get("type") == "client_diagnostic":
            _handle_client_diagnostic(request, session, connection_id, frame)
        elif frame.get("type") == "output_ack":
            byte_count = frame.get("bytes")
            if isinstance(byte_count, int) and not isinstance(byte_count, bool):
                output_flow.acknowledge(byte_count)


async def events_ws(request: web.Request) -> web.WebSocketResponse:
    ws = metered_websocket(request, "events", heartbeat=20)
    await ws.prepare(request)
    bus: EventBus = request.app[keys.EVENTS]
    presence: DevicePresenceStore = request.app[keys.DEVICE_PRESENCE]
    connection_id = secrets.token_urlsafe(12)
    session_filter = request.query.get("session")
    # Parsed before subscribing: an int() failure between subscribe and the try
    # leaked a dead 1024-slot subscriber that kept paying per-event fanout cost
    # until the daemon restarted.
    raw_cursor = request.query.get("after_seq", "")
    try:
        last_sequence = int(raw_cursor) if raw_cursor else 0
    except ValueError:
        raise web.HTTPBadRequest(text="after_seq must be an integer") from None
    generation = request.app.get(keys.DAEMON_GENERATION)
    await ws.send_json(
        {
            "type": "events_hello",
            "ui_build_id": read_ui_build_id(request.app[keys.FRONTEND_DIR]),
            "daemon_generation": generation or "legacy",
        }
    )
    queue = bus.subscribe(name="events-ws")
    try:
        if last_sequence > 0:
            # Resume a small gap in order. Anything wider is cheaper and safer to
            # recover with one authoritative REST refresh.
            catch_up = await request.app[keys.HISTORY].events(
                session_id=session_filter,
                limit=EVENTS_CATCHUP_LIMIT + 1,
                after_seq=last_sequence,
            )
            truncated = len(catch_up) > EVENTS_CATCHUP_LIMIT
        else:
            # The initial REST load already supplies authoritative state. Start at
            # the durable watermark instead of replaying historical side effects.
            latest, _truncated = await request.app[keys.HISTORY].recent_events(
                session_id=session_filter, limit=1
            )
            last_sequence = int(latest[-1]["seq"]) if latest else 0
            catch_up = []
            truncated = False
            await ws.send_json({"type": "events_ready", "sequence": last_sequence})
            log.debug(
                "events websocket cold-started at sequence %d connection=%s session=%s",
                last_sequence,
                connection_id,
                session_filter or "*",
            )
        if truncated:
            latest, _truncated = await request.app[keys.HISTORY].recent_events(
                session_id=session_filter, limit=1
            )
            watermark = int(latest[-1]["seq"]) if latest else last_sequence
            log.info(
                "events websocket gap requires snapshot connection=%s session=%s "
                "cursor=%d watermark=%d missed_at_least=%d",
                connection_id,
                session_filter or "*",
                last_sequence,
                watermark,
                len(catch_up),
            )
            last_sequence = max(last_sequence, watermark)
            catch_up = []
            await ws.send_json(
                {
                    "type": "events_gap",
                    "reason": "catchup_truncated",
                    "sequence": last_sequence,
                }
            )
        last_visible_sequence = last_sequence
        for event in catch_up:
            event_sequence = int(event["seq"])
            last_sequence = max(last_sequence, event_sequence)
            if event.get("type") in BROWSER_OMITTED_EVENT_TYPES:
                continue
            # Catch-up events are a historical replay for state reconstruction, not
            # live activity. Mark them so the browser suppresses live-only side effects
            # (voice autoplay, notification sounds) that would otherwise re-fire every
            # reconnect or reopen.
            event["replay"] = True
            await ws.send_json(event)
            last_visible_sequence = event_sequence
        if catch_up and last_visible_sequence < last_sequence:
            await ws.send_json({"type": "events_cursor", "sequence": last_sequence})

        async def watch_client() -> None:
            """Read the socket to observe the client going away, and its presence.

            `/events` is otherwise server-to-client only, but a handler that never
            reads cannot see a close frame or process heartbeat pongs — so a
            suspended tab's socket lingers, holding a 1024-slot queue and paying
            per-event fanout, until some later send happens to fail.

            Device presence rides this socket because every client holds one
            whether or not it can receive Web Push (the Windows desktop shell
            cannot), and because the connection's lifetime is exactly the
            presence's lifetime — a closed socket is a device nobody is looking at.
            """
            # unsupervised-loop-ok: lives for one /events websocket, not the daemon.
            async for message in ws:
                if message.type != WSMsgType.TEXT:
                    continue
                try:
                    frame = json.loads(message.data)
                except json.JSONDecodeError:
                    continue
                if not isinstance(frame, dict) or frame.get("type") != "presence":
                    continue
                report = parse_device_report(frame)
                if report is not None:
                    presence.report(connection_id, report)

        reader = asyncio.create_task(watch_client())
        try:
            # unsupervised-loop-ok: lives for one /events websocket, not the daemon.
            while True:
                getter = asyncio.create_task(queue.get())
                done, _pending = await asyncio.wait(
                    (reader, getter), return_when=asyncio.FIRST_COMPLETED
                )
                if reader in done:
                    getter.cancel()
                    break
                live_event = getter.result()
                if live_event.seq <= last_sequence:
                    continue
                last_sequence = live_event.seq
                if live_event.type in BROWSER_OMITTED_EVENT_TYPES:
                    continue
                await ws.send_json(live_event.snapshot())
        finally:
            reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)
    except (asyncio.CancelledError, ConnectionResetError):
        pass
    finally:
        # Before the unsubscribe: a cancelled handler re-raises at its first await,
        # and this device must not be left looking present forever.
        presence.drop(connection_id)
        bus.unsubscribe(queue)
    return ws


ROUTES: tuple[web.RouteDef, ...] = (
    web.get("/pty/{sid}", pty_ws),
    web.get("/events", events_ws),
)
