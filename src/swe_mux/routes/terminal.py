"""Writing into a live terminal: operator input, broadcast, interrupt, end."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any

from aiohttp import web

from .. import (
    app_keys as keys,
)
from ..composer_input import (
    DEFAULT_CLEAR_KEYS,
    DEFAULT_NEWLINE_KEYS,
    composer_insertion,
    note_composer_write,
)
from ..config import Config
from ..event_bus import EventBus
from ..harness import (
    HARNESSES,
    composer_insertion_rules,
)
from ..http_support import json_response
from ..observation import (
    note_interrupt_intent,
)
from ..session import (
    SessionManager,
    note_remote_shell_submission,
)
from ..supervisor_client import Liveness, liveness_of

log = logging.getLogger(__name__)


def _composer_insertion(backend: object, text: str) -> str:
    """The bytes that leave ``text`` unsent in this backend's composer.

    One resolver for every daemon-side path that stages text rather than typing
    it, so a harness quirk is declared once in the registry instead of being
    re-derived per call site.
    """
    newline_keys, lift = composer_insertion_rules(backend)
    return composer_insertion(text, newline_keys=newline_keys, lift_leading_newline=lift)


def _note_composer_write(events: EventBus, session: Any, data: str | bytes, source: str) -> None:
    """Track what one operator write left sitting in the composer.

    Every path that writes operator text to a PTY calls this, for the same reason
    every one of them advances `input_revision`: text inserted by a voice append,
    a send-to-agent, or a mobile draft is as unsent as text someone typed, and a
    row that only lit up for keystrokes would be silent on exactly the paths that
    stage text and walk away.

    Only the empty/non-empty crossing is announced. A keystroke-rate event would
    put one fanout on the bus per character typed, which is the traffic the
    throttled `terminal_input` event already exists to avoid.
    """
    composer = getattr(session, "composer", None)
    if composer is None:
        return
    text = data.decode("utf-8", "ignore") if isinstance(data, bytes) else data
    # Which keys empty a composer is the harness's fact, not this module's. An
    # unregistered backend keeps the historical Ctrl+U.
    harness = HARNESSES.get(session.record.backend)
    clear_keys = harness.composer_clear_keys if harness else DEFAULT_CLEAR_KEYS
    newline_keys = harness.composer_newline if harness else DEFAULT_NEWLINE_KEYS
    change = note_composer_write(composer, text, time.time(), clear_keys, newline_keys)
    if change is None:
        return
    ledger = getattr(session, "state_transitions", None)
    if ledger is not None:
        ledger.append(
            {"ts": time.time(), "kind": "composer", "action": change, "source": source}
        )
    log.debug(
        "composer %s for session %s (source %s)", change, session.record.id, source
    )
    events.emit_background(
        "composer_input_changed",
        session_id=session.record.id,
        source=source,
        pending=composer.pending,
    )


def session_accepts_input(session: Any) -> bool:
    """Whether this session still has a child that could receive keystrokes.

    An ended pane stays visible until it is dismissed, and a cold one is visible
    from the moment the daemon starts, so both are panes a person can click into
    and type at. Neither has a PTY: `PtyHost.write` raises for a released or
    never-spawned pseudoterminal, which arrives as a 500 on the HTTP paths and as
    a dropped socket on the WebSocket one. Refusing here makes it an ordinary,
    explainable "this session has ended" instead.
    """
    return str(getattr(session.record, "state", "")) not in {"exited", "crashed"}


def _record_operator_input(
    events: EventBus, session: Any, data: str, *, source: str, input_owner: bool = True
) -> None:
    """Write operator-originated text to a PTY with full evidence accounting.

    Every human-input path must advance `input_revision`/`last_input_event_ts`
    and emit `terminal_input`, or delivery-readiness reports
    `partial_input_absent`/`operator_quiet` as satisfied for text the operator
    just sent — corrupting the shadow-metric baseline Phase 5 promotion is
    validated against. The WS path does its own (throttled) accounting; this is
    the shared path for everything else.
    """
    if not session_accepts_input(session):
        raise ValueError("session has ended and cannot accept input")
    session.pty.write(data)
    now = time.monotonic()
    session.input_revision += 1
    note_remote_shell_submission(session, data)
    _note_composer_write(events, session, data, source)
    note_interrupt_intent(session, data, source=source)
    session.last_input_event_ts = now
    session.last_input_report_ts = now
    events.emit_background(
        "terminal_input",
        session_id=session.record.id,
        source=source,
        input_owner=input_owner,
        bytes=len(data.encode("utf-8")),
    )


#: The keystroke that interrupts an agent's current turn. Universal across the
#: harnesses (their CLIs all treat ETX as an interrupt), the same byte the voice
#: interrupt path and the browser terminal already send. The graceful *exit*
#: sequence, by contrast, is per-harness and lives on the adapter/PTY.
_INTERRUPT_KEYS = "\x03"


#: How long the graceful end lets the interrupt land before it sends the exit
#: sequence, and how often it polls for the CLI to tear itself down.
_INTERRUPT_SETTLE_SECONDS = 0.4


_GRACEFUL_POLL_SECONDS = 0.25


def _session_owns_daemon(session: Any) -> bool:
    """Whether ending this session would take the running daemon down.

    The hazard is job-object inheritance: a daemon relaunched from a shell inside
    a session is a descendant of that session's root process, so closing the
    session's Job on removal terminates the daemon too (see `popen_outside_job`,
    which exists to break this exact link). This checks the ancestry directly -
    is this daemon process a descendant of the session's process? - and fails
    closed only for the positive case. A shell session, the realistic host for a
    hand-launched daemon, is already rejected as a non-agent target; this is the
    defence in depth for the case that slips past that.
    """
    pid = getattr(session.record, "pid", None)
    if not pid:
        return False
    try:
        import psutil

        daemon = psutil.Process(os.getpid())
        return any(ancestor.pid == pid for ancestor in daemon.parents())
    except Exception:
        # Cannot prove ownership; the agent-only guard covers the realistic case,
        # so an inspection failure does not itself forbid every end.
        return False


async def _interrupt_session_pty(app: web.Application, session: Any) -> None:
    """Interrupt an agent's current turn through the shared operator-input path.

    The daemon operation MCP `interrupt` and the browser both call. It writes the
    interrupt byte with full input accounting - never straight to the PTY, which
    would skip the `input_revision`/`terminal_input` bookkeeping the
    delivery-readiness contract depends on.
    """
    _record_operator_input(
        app[keys.EVENTS], session, _INTERRUPT_KEYS, source="agent_control"
    )
    await app[keys.EVENTS].emit(
        "agent_turn_interrupted", session_id=session.record.id, source="agent_control"
    )


async def _end_session_gracefully(
    app: web.Application, session: Any, reason: str = "agent_ended"
) -> dict[str, Any]:
    """Graceful session end as a typed daemon operation (Phase 7.6).

    Interrupt the current turn, send the harness's own exit sequence from its
    adapter (carried on the PTY as `graceful_exit`), wait bounded for the CLI to
    tear itself down, and fall back to the existing hard stop only on timeout.
    The end reason is stamped on the record first, so a CLI that exits on its own
    still records `agent_ended` rather than the ordinary process-exit reason.
    """
    sessions = app[keys.SESSIONS]
    config: Config = app[keys.CONFIG]
    sid = str(session.record.id)
    session.record.requested_end_reason = reason
    if session.record.state in {"exited", "crashed"}:
        return {"final_state": session.record.state, "graceful": True, "reason": reason}
    _record_operator_input(
        app[keys.EVENTS], session, _INTERRUPT_KEYS, source="agent_control"
    )
    await asyncio.sleep(_INTERRUPT_SETTLE_SECONDS)
    exit_keys = str(getattr(session.pty, "graceful_exit", "") or "")
    if exit_keys and session.record.state not in {"exited", "crashed"}:
        _record_operator_input(
            app[keys.EVENTS], session, exit_keys, source="agent_control"
        )
    deadline = time.monotonic() + float(config.session_control_graceful_timeout_s)
    while time.monotonic() < deadline:
        if session.record.state in {"exited", "crashed"}:
            return {
                "final_state": session.record.state,
                "graceful": True,
                "reason": session.record.requested_end_reason or reason,
            }
        await asyncio.sleep(_GRACEFUL_POLL_SECONDS)
    # The CLI did not exit in time: fall back to the hard stop, still recording
    # the agent-initiated reason so the two remain distinguishable.
    await sessions.stop(sid, reason=reason)
    return {
        "final_state": session.record.state,
        "graceful": False,
        "reason": session.record.requested_end_reason or reason,
    }


async def session_input(request: web.Request) -> web.Response:
    body = await request.json()
    session = request.app[keys.SESSIONS].resolve(request.match_info["sid"])
    if session.record.state in {"exited", "crashed"}:
        return json_response({"error": "the session has ended"}, 409)
    data = str(body.get("data", ""))
    if not data:
        return json_response({"ok": True})
    _record_operator_input(request.app[keys.EVENTS], session, data, source="http")
    return json_response({"ok": True})


async def session_startup_metrics(request: web.Request) -> web.Response:
    session = request.app[keys.SESSIONS].resolve(request.match_info["sid"])
    raw = (await request.json()).get("timing_ms")
    if not isinstance(raw, dict):
        raise ValueError("timing_ms must be an object")
    allowed = {"api_response", "pane_mounted", "socket_open", "replay_ready"}
    timing_ms: dict[str, float] = {}
    for key, value in raw.items():
        if key not in allowed:
            raise ValueError(f"unsupported startup timing: {key}")
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise ValueError(f"startup timing must be numeric: {key}")
        measured = float(value)
        if not 0 <= measured <= 300_000:
            raise ValueError(f"startup timing is out of range: {key}")
        timing_ms[key] = round(measured, 1)
    if "replay_ready" not in timing_ms:
        raise ValueError("replay_ready startup timing is required")
    if not session.record.client_startup_timing_ms:
        session.record.client_startup_timing_ms = timing_ms
        session.publish_update()
        await request.app[keys.EVENTS].emit(
            "session_startup_client_measured",
            session_id=session.record.id,
            source="browser",
            timing_ms=dict(timing_ms),
        )
    return json_response({"ok": True, "timing_ms": session.record.client_startup_timing_ms})


async def broadcast_set(request: web.Request) -> web.Response:
    session = request.app[keys.SESSIONS].resolve(request.match_info["sid"])
    session.record.broadcast = bool((await request.json()).get("include", True))
    session.publish_update()
    await request.app[keys.EVENTS].emit(
        "broadcast_membership_changed",
        session_id=session.record.id,
        included=session.record.broadcast,
    )
    return json_response(session.record.snapshot())


async def deliver_broadcast(
    manager: SessionManager,
    data: str,
    events: EventBus,
    *,
    source_id: str | None = None,
) -> dict[str, list[str]]:
    delivered: list[str] = []
    skipped: list[str] = []
    for candidate in manager.sessions.values():
        if candidate.record.id == source_id or not candidate.record.broadcast:
            continue
        # Writable, not merely un-ended: a session whose supervisor connection
        # dropped is still running (`isalive()` is True by design) but every byte
        # written to it is discarded, and reporting those as delivered would be a
        # lie the sender acts on.
        if (
            candidate.record.state in {"exited", "crashed"}
            or liveness_of(candidate.pty) is not Liveness.ALIVE
        ):
            skipped.append(candidate.record.id)
            continue
        # Each target gets the same evidence accounting as any other operator
        # input; `input_owner=False` because the writer holds no ownership claim
        # on the target's PTY.
        _record_operator_input(events, candidate, data, source="broadcast", input_owner=False)
        delivered.append(candidate.record.id)
    events.emit_background(
        "broadcast_delivered",
        session_id=source_id,
        targets=delivered,
        skipped=skipped,
        bytes=len(data.encode("utf-8")),
    )
    return {"delivered": delivered, "skipped": skipped}


async def broadcast_input_route(request: web.Request) -> web.Response:
    data = str((await request.json()).get("data", ""))
    return json_response(
        await deliver_broadcast(request.app[keys.SESSIONS], data, request.app[keys.EVENTS])
    )


ROUTES: tuple[web.RouteDef, ...] = (
    web.post("/api/sessions/{sid}/input", session_input),
    web.post("/api/sessions/{sid}/startup-metrics", session_startup_metrics),
    web.post("/api/sessions/{sid}/broadcast-set", broadcast_set),
    web.post("/api/broadcast/input", broadcast_input_route),
)
