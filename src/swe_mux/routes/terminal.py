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
from ..config import Config
from ..event_bus import EventBus
from ..http_support import json_response
from ..operator_input import record_operator_input
from ..session import SessionManager
from ..supervisor_client import Liveness, liveness_of
from .support import refuse_agent_session_caller

log = logging.getLogger(__name__)


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
    record_operator_input(
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
    record_operator_input(
        app[keys.EVENTS], session, _INTERRUPT_KEYS, source="agent_control"
    )
    await asyncio.sleep(_INTERRUPT_SETTLE_SECONDS)
    exit_keys = str(getattr(session.pty, "graceful_exit", "") or "")
    if exit_keys and session.record.state not in {"exited", "crashed"}:
        record_operator_input(
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
    # Writing into another session's terminal bypasses the prompt queue,
    # readiness, arming, and revocation - the exact machinery agent-to-agent
    # messaging exists to route through - so an agent session's pane is
    # refused here and pointed at notify/`swemux agent` (Phase 23 W1).
    refuse_agent_session_caller(
        request, operation="writing terminal input over HTTP"
    )
    body = await request.json()
    session = request.app[keys.SESSIONS].resolve(request.match_info["sid"])
    if session.record.state in {"exited", "crashed"}:
        return json_response({"error": "the session has ended"}, 409)
    data = str(body.get("data", ""))
    if not data:
        return json_response({"ok": True})
    record_operator_input(request.app[keys.EVENTS], session, data, source="http")
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
        record_operator_input(events, candidate, data, source="broadcast", input_owner=False)
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
    refuse_agent_session_caller(
        request, operation="broadcasting terminal input over HTTP"
    )
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
