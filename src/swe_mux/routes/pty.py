"""The terminal and event WebSockets.

The terminal socket is an adapter: everything about a session's terminal lives in
`terminal_core`, and this module supplies the daemon's side of each connection
(`SessionTerminalHost`) - the record, the event log, device presence, and what
typing means to approvals, status detection and the composer estimate.
"""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
import time
from typing import Any

from aiohttp import WSMsgType, web

from .. import (
    app_keys as keys,
)
from ..device_presence import DevicePresenceStore, parse_device_report
from ..event_bus import EventBus
from ..network_usage import metered_websocket
from ..observation import (
    cancel_pending_approval,
    note_interrupt_intent,
)
from ..session import (
    Session,
    note_remote_shell_submission,
    session_is_unwitnessed,
)
from ..terminal_core import serve_connection
from ..ui_build import ui_build_id_cached
from . import terminal

log = logging.getLogger(__name__)


# Browser reconnects use a small recovery window. Wider gaps fall back to one
# authoritative REST refresh instead of replaying a large, stale event history.
EVENTS_CATCHUP_LIMIT = 64


# These hook lifecycle records remain durable for diagnostics, but browser state
# does not consume their large payloads. User-visible state changes arrive as
# separate, compact events.
BROWSER_OMITTED_EVENT_TYPES = frozenset({"PreToolUse", "PostToolUse", "tool_use", "tool_result"})


class SessionTerminalHost:
    """The daemon's side of one terminal connection to one session."""

    def __init__(self, app: web.Application, session: Session, generation: str) -> None:
        self.app = app
        self.session = session
        self.generation = generation
        self._events: EventBus = app[keys.EVENTS]

    def snapshot(self) -> tuple[dict[str, Any], int]:
        return self.session.terminal_snapshot()

    def presence_leader(self) -> str | None:
        presence: DevicePresenceStore | None = self.app.get(keys.DEVICE_PRESENCE)
        return presence.leading_profile() if presence is not None else None

    def emit(self, kind: str, *, source: str, **payload: Any) -> None:
        self._events.emit_background(
            kind, session_id=self.session.record.id, source=source, **payload
        )

    def note_typed_text(self, data: str) -> None:
        session = self.session
        cancel_pending_approval(session, "terminal_input")
        note_remote_shell_submission(session, data)
        terminal._note_composer_write(self._events, session, data, "browser")
        note_interrupt_intent(session, data, source="terminal_input")

    def note_typed_bytes(self, data: bytes) -> None:
        note_remote_shell_submission(self.session, data)
        terminal._note_composer_write(self._events, self.session, data, "browser")

    def note_owner_submit(self, data: str) -> None:
        session = self.session
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
            self._events.emit_background(
                "unwitnessed_turn_armed",
                session_id=session.record.id,
                source="terminal_input",
            )

    async def broadcast(self, data: str) -> None:
        await terminal.deliver_broadcast(
            self.app[keys.SESSIONS],
            data,
            self._events,
            source_id=self.session.record.id,
        )


async def pty_ws(request: web.Request) -> web.WebSocketResponse:
    session = request.app[keys.SESSIONS].resolve(request.match_info["sid"])
    generation = request.app.get(keys.DAEMON_GENERATION) or "legacy"
    connection_id = secrets.token_urlsafe(12)
    ws = metered_websocket(request, "pty", heartbeat=20, max_msg_size=2 * 1024 * 1024)
    await ws.prepare(request)
    await serve_connection(
        ws,
        session.terminal,
        SessionTerminalHost(request.app, session, generation),
        connection_id=connection_id,
    )
    return ws


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
            "ui_build_id": await ui_build_id_cached(request.app[keys.FRONTEND_DIR]),
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
                if live_event.type in BROWSER_OMITTED_EVENT_TYPES:
                    continue
                if live_event.transient:
                    # Never persisted, so it has no sequence number and must not
                    # touch the resume cursor — advancing it here would skip real
                    # durable events, and comparing against it would drop every
                    # transient frame (they all carry seq 0). A reconnecting client
                    # re-reads these facts from REST instead of resuming them.
                    await ws.send_json(live_event.snapshot())
                    continue
                if live_event.seq <= last_sequence:
                    continue
                last_sequence = live_event.seq
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
