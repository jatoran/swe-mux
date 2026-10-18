"""One browser connection to a session's terminal, from handshake to detach."""

from __future__ import annotations

import asyncio
import json
import time
from contextlib import suppress
from typing import Any

from aiohttp import WSMsgType

from ..terminal_arbitration import ClaimReason, ClaimRequest, evaluate_claim
from .core import TerminalCore
from .flow import PTY_OUTPUT_BATCH_BYTES, PtyOutputFlow
from .host import TerminalHost, TerminalSocket
from .input import is_default_color_response, pointer_report_kind
from .stream import PtySubscriber

PTY_ATTACH_READY_TIMEOUT_SECONDS = 0.25


# How long a connection's repeated passive claims go unanswered after a refusal. The
# reply is what an older client re-claims on, so answering every one is what turns a
# refusal into a loop.
REFUSED_CLAIM_COOLDOWN_SECONDS = 1.0


def versioned_frame(frame: dict[str, Any], generation: str) -> dict[str, Any]:
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


async def serve_connection(
    ws: TerminalSocket,
    core: TerminalCore,
    host: TerminalHost,
    *,
    connection_id: str,
) -> None:
    """Run one prepared browser socket against a terminal core until it closes."""
    descriptor = core.descriptor
    allow_terminal_responses = (
        core.attachments_seen == 0
        and descriptor.state not in {"exited", "crashed"}
        and time.time() - descriptor.created_at <= 5
    )
    core.attachments_seen += 1
    output_flow = PtyOutputFlow()
    stream = core.stream
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
        # ...and the stream that position was counted in; a `since` is honoured only
        # together with this ring's own (`TerminalStream.stream_id`).
        attach_stream: str | None = None
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
                    stream_value = initial_frame.get("stream")
                    if isinstance(stream_value, str) and stream_value:
                        attach_stream = stream_value
                    geometry_queued = _apply_client_viewport(core, connection_id, initial_frame)
                    # A repaint-heavy TUI can wrap the retained ring until this
                    # attach's replay holds no transcript at all. The recovery is
                    # client-requested (a `repaint` frame once the parsed replay
                    # provably produced no scrollback, `handle_client_repaint`)
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
            return

        snapshot, revision, replay_kind, replay, ring_position, subscriber = stream.attach(
            attach_since, attach_stream, host.snapshot
        )
        # Read before the first await, so it names the stream `ring_position` counts.
        ring_stream = stream.stream_id
        host.emit("terminal_attached", source="daemon", connections=len(stream.subscribers))
        await ws.send_json(
            versioned_frame(
                {"type": "state", "snapshot": snapshot, "revision": revision},
                host.generation,
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
        # byte it receives afterwards is what it may offer as `since` next time,
        # together with `stream`.
        await ws.send_json(
            {
                "type": "replay_end",
                "reason": replay_kind,
                "position": ring_position,
                "stream": ring_stream,
            }
        )
        # The arbitrated size can differ from this client's own fit (another device owns
        # input), so tell it up front rather than letting it render at the wrong width
        # until something happens to change the geometry. Skipped when this attach
        # already changed the size, since that queued a frame for every client.
        if not geometry_queued:
            await ws.send_json(core.geometry_frame())
        if snapshot["state"] in {"exited", "crashed"}:
            await ws.send_json(
                versioned_frame(
                    {
                        "type": "exit",
                        "snapshot": snapshot,
                        "revision": revision,
                        "reason": "already_ended",
                    },
                    host.generation,
                )
            )
            return
        sender_task = asyncio.create_task(
            pty_sender(ws, core, host, subscriber, output_flow)
        )
        # The replay just sent may have been a window over a differential frame stream,
        # which reconstructs to whichever cells happened to change inside it. One pulse
        # makes the child restate the whole screen. A delta needs none: the client's
        # terminal kept its state and the delta is the stream itself, so after the
        # append its screen is exact — pulsing here would make every tab switch cost
        # the child a repaint.
        if replay_kind == "attach":
            core.schedule_attach_repaint(host)
        for pending in pending_messages:
            await _handle_client_message(ws, core, host, connection_id, pending, output_flow)
        async for message in ws:
            await _handle_client_message(ws, core, host, connection_id, message, output_flow)
    finally:
        # Every synchronous cleanup runs before the sender task is awaited. A handler
        # cancelled on peer disconnect re-raises at the first await inside its own
        # finally, which used to skip the unsubscribe and the ownership release
        # entirely — leaving the session reported as attended forever and its input
        # owned by a socket that no longer exists.
        released = core.release_input_owner(connection_id)
        core.drop_viewport(connection_id)
        core.claim_refusals.pop(connection_id, None)
        if subscriber is not None:
            stream.unsubscribe(subscriber)
        # A detach can hand geometry back to whoever is left: the phone closing its tab
        # returns the PTY to the desktop's width — a width change like any other, and
        # one nobody is dragging, so the screen it lands on has to be repaired too.
        if core.apply_geometry():
            core.schedule_resize_repaint(host)
        if released:
            stream.publish_control(
                {
                    "type": "input_owner_released",
                    "epoch": core.input_owner_epoch,
                }
            )
        host.emit("terminal_detached", source="daemon", connections=len(stream.subscribers))
        if sender_task is not None:
            sender_task.cancel()
            await asyncio.gather(sender_task, return_exceptions=True)


async def pty_sender(
    ws: TerminalSocket,
    core: TerminalCore,
    host: TerminalHost,
    subscriber: PtySubscriber,
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
            ) = core.stream.take_resync(subscriber, host.snapshot)
            ring_stream = core.stream.stream_id
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
                {
                    "type": "replay_end",
                    "reason": "resync",
                    "position": ring_position,
                    "stream": ring_stream,
                }
            )
            await ws.send_json(
                versioned_frame(
                    {"type": "update", "snapshot": current, "revision": current_revision},
                    host.generation,
                )
            )
            # Control frames are skipped while a subscriber is resyncing, so restate the
            # arbitrated geometry here rather than leaving this client sized by whatever
            # it last heard before the gap.
            await ws.send_json(core.geometry_frame())
            if exit_frame:
                await ws.send_json(versioned_frame(exit_frame, host.generation))
                return
        else:
            await ws.send_json(versioned_frame(message, host.generation))
            if message.get("type") == "exit":
                return


def _apply_client_viewport(core: TerminalCore, connection_id: str, frame: dict[str, Any]) -> bool:
    """Register one client's fitted size and re-arbitrate the shared PTY geometry.

    A client that reports itself hidden is deregistered instead: a minimized desktop
    pane still has layout, and its `resize` frames used to rewrap the agent TUI to
    desktop width on whatever device the user was actually holding.

    Returns whether the arbitrated size changed, which also means every attached
    client (including this one) has a `geometry` frame queued.
    """
    core.set_viewport(
        connection_id,
        int(frame["cols"]),
        int(frame["rows"]),
        hidden=bool(frame.get("hidden")),
    )
    return core.apply_geometry()


def _owner_frame(core: TerminalCore, *, active: bool, reason: str) -> dict[str, Any]:
    return {
        "type": "input_owner",
        "active": active,
        "epoch": core.input_owner_epoch,
        "reason": reason,
        "owner_device": core.input_owner_device,
    }


async def _claim_input(
    ws: TerminalSocket,
    core: TerminalCore,
    host: TerminalHost,
    connection_id: str,
    frame: dict[str, Any],
) -> None:
    reason: ClaimReason = "passive" if frame.get("reason") == "passive" else "gesture"
    device = str(frame.get("device") or "unknown")[:32]
    # Which device the human is at is a property of the whole app, not of this
    # session, so it comes from the host's presence tracking rather than from the
    # claiming client. Absent (older client, or a test app that wires no store) means
    # "no signal", which leaves the per-session rules to decide exactly as before.
    #
    # The device class whose human touched it most recently. Not "is any other device
    # active": a desktop left open and focused stays active for two minutes after the
    # last keystroke, so both classes are active exactly when someone picks up their
    # phone — and treating that as contention left every session with the desktop.
    leader = host.presence_leader()
    decision = evaluate_claim(
        core.owner_state(),
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
    core.record_claim(
        {
            "ts": time.time(),
            "device": device,
            "ask": reason,
            "focused": frame.get("focused") is not False,
            "leader": leader,
            "owner_device": core.input_owner_device,
            "verdict": decision.reason,
            "granted": decision.granted,
        }
    )
    if not decision.granted:
        core.input_claim_denials += 1
        now = time.monotonic()
        refused_at = core.claim_refusals.get(connection_id, 0.0)
        core.claim_refusals[connection_id] = now
        if reason == "passive" and now - refused_at < REFUSED_CLAIM_COOLDOWN_SECONDS:
            # A client that re-claims on its own refusal loops as fast as the round
            # trip; one live session logged 7566 refused claims that way. Newer
            # clients no longer do it, but a cached build still can, and the answer
            # it would act on is the one already sent. Stay silent instead.
            return
        await ws.send_json(_owner_frame(core, active=False, reason=decision.reason))
        return
    displaced = core.input_owner_socket if decision.changed else None
    core.apply_owner_state(decision.state)
    core.input_owner_socket = ws
    await ws.send_json(_owner_frame(core, active=True, reason=decision.reason))
    if displaced is not None:
        # Losing ownership used to be silent, so a desktop pane that still had DOM
        # focus kept typing into a session the phone had claimed and every keystroke
        # was dropped with no feedback — the terminal just looked hung.
        with suppress(ConnectionResetError, RuntimeError, ValueError):
            await displaced.send_json(
                _owner_frame(core, active=False, reason="claimed_elsewhere")
            )
    if decision.changed:
        # The device a human is typing into dictates the size everyone else renders.
        if core.apply_geometry():
            core.schedule_resize_repaint(host)
        host.emit(
            "terminal_input_owner",
            source="daemon",
            device=core.input_owner_device,
            epoch=core.input_owner_epoch,
            grant=decision.reason,
            denials=core.input_claim_denials,
        )


async def _handle_text_input(
    ws: TerminalSocket,
    core: TerminalCore,
    host: TerminalHost,
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
    descriptor = core.descriptor
    # Reject a late OSC 10/11 reply before the CLI mistakes it for prompt input.
    # Gated on the descriptor rather than the name (`is_default_color_response`).
    if descriptor.suppresses_late_color_response and is_default_color_response(data):
        return
    is_terminal_response = frame.get("kind") == "terminal_response"
    if core.input_owner != connection_id:
        # xterm device replies belong to the probe that asked for them, so a rejected
        # one is discarded rather than echoed back for replay: re-sending it late is
        # worse than losing it. Human input is echoed back so the client can re-claim
        # and resend the exact keystrokes instead of losing them to a lost race.
        if not is_terminal_response:
            core.note_input_rejected(host, len(data.encode("utf-8")))
            await ws.send_json(
                {
                    "type": "input_rejected",
                    "epoch": core.input_owner_epoch,
                    "owner_device": core.input_owner_device,
                    "data": data,
                    "broadcast": bool(frame.get("broadcast")),
                    "retry": bool(frame.get("retry")),
                    **({"input_seq": input_seq} if input_seq is not None else {}),
                }
            )
        return
    if descriptor.ended:
        # An ended or cold pane is read-only. Answered rather than dropped, so a
        # client typing into one learns why instead of watching characters vanish.
        await ws.send_json({"type": "input_refused", "reason": "session_ended"})
        return
    server_received_at_ms = int(time.time() * 1000)
    core.write(data)
    now = time.monotonic()
    pointer = pointer_report_kind(data)
    if not is_terminal_response and pointer is None:
        # Typing is the strongest evidence of where the human is; it also renews
        # this connection's protection from a background pane's passive re-claim.
        core.note_typed_input(now)
        host.note_typed_text(data)
        host.note_owner_submit(data)
    elif pointer == "button":
        # A click or a wheel notch is the human being here, but it puts no text in
        # the composer, so it moves the presence clock and not the input revision.
        core.note_pointer_input(now)
    if not is_terminal_response and pointer is None and now - core.last_input_report_ts >= 2:
        core.last_input_report_ts = now
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
        host.emit(
            "terminal_input",
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
        await host.broadcast(data)


async def _handle_binary_input(
    ws: TerminalSocket,
    core: TerminalCore,
    host: TerminalHost,
    connection_id: str,
    data: bytes,
) -> None:
    # No current client sends binary input; a non-owner's bytes are counted and
    # dropped rather than echoed back, since there is no frame shape to replay.
    if core.input_owner != connection_id:
        core.note_input_rejected(host, len(data))
        return
    if core.descriptor.ended:
        await ws.send_json({"type": "input_refused", "reason": "session_ended"})
        return
    core.write(data)
    now = time.monotonic()
    core.note_typed_input(now)
    host.note_typed_bytes(data)
    if now - core.last_input_report_ts >= 2:
        core.last_input_report_ts = now
        host.emit("terminal_input", source="daemon", input_owner=True, bytes=len(data))


async def _handle_client_message(
    ws: TerminalSocket,
    core: TerminalCore,
    host: TerminalHost,
    connection_id: str,
    message: Any,
    output_flow: PtyOutputFlow,
) -> None:
    if message.type == WSMsgType.BINARY:
        await _handle_binary_input(ws, core, host, connection_id, message.data)
    elif message.type == WSMsgType.TEXT:
        frame = json.loads(message.data)
        if frame.get("type") == "claim_input":
            await _claim_input(ws, core, host, connection_id, frame)
        elif frame.get("type") == "input":
            await _handle_text_input(ws, core, host, connection_id, frame)
        elif frame.get("type") == "terminal_state" and core.input_owner == connection_id:
            mode = str(frame.get("mode") or "")
            if mode not in {"normal", "alternate"}:
                raise ValueError("terminal mode must be normal or alternate")
            if core.set_terminal_mode(mode):
                host.emit("terminal_mode_changed", source="browser", mode=mode)
        elif frame.get("type") in {"attach_ready", "resize"}:
            if _apply_client_viewport(core, connection_id, frame):
                core.schedule_resize_repaint(host)
        elif frame.get("type") == "repaint":
            await core.handle_client_repaint(host)
        elif frame.get("type") == "client_diagnostic":
            core.note_client_diagnostic(host, connection_id, frame)
        elif frame.get("type") == "output_ack":
            byte_count = frame.get("bytes")
            if isinstance(byte_count, int) and not isinstance(byte_count, bool):
                output_flow.acknowledge(byte_count)
