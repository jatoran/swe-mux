"""Install-wide maintenance: today, the factory reset.

The reset itself is `factory_reset`; this module is the surface that asks for
one. What lives here is the part that has to happen *while a daemon is still
running*: telling the operator exactly what they are about to lose, reaping the
sessions, and handing the work to a successor that can do it.

Three constraints shape the endpoint, and none of them is about the wipe:

**It is loopback-only.** Every other destructive control in swe-mux is reachable
from the phone, because every other one is scoped to a session or a Project. A
factory reset is scoped to the machine, so it is refused from anywhere but the
machine - the same boundary `desktop_shutdown` draws, and for the same reason: a
phone on the tailnet is a remote control, not a console.

**It refuses when it cannot come back.** A daemon with no relaunch command would
reset the install and then exit, leaving a data directory in the trash and
nothing to rebuild it. That is a 409 rather than an attempt.

**The confirmation phrase comes from the daemon.** The client renders what it is
told to require and echoes back what was typed, so there is one copy of the
phrase. A client-side check alone would also be no check at all for anything
that is not the client.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from aiohttp import web

from .. import app_keys as keys
from ..config import Config
from ..event_bus import EventBus
from ..factory_reset import (
    CONFIRMATION_PHRASE,
    EXTERNAL_LEFT,
    KEEP_ENTRIES,
    planned_entries,
    read_result,
    worktree_directories,
    write_request,
)
from ..http_support import is_loopback_peer, json_response
from ..lifecycle import planned_handoff
from ..session import SessionManager
from ..supervisor_client import kill_server
from . import system

log = logging.getLogger(__name__)


def _live_sessions(request: web.Request) -> list[dict[str, Any]]:
    """The sessions a reset would end, named the way the operator named them.

    Enumerated for the dialog rather than counted, because "3 sessions will be
    terminated" and "your release branch agent, mid-run, will be terminated" are
    different sentences and only one of them is a decision.
    """
    manager: SessionManager | None = request.app.get(keys.SESSIONS)
    if manager is None:
        return []
    projects = request.app.get(keys.PROJECTS)
    rows: list[dict[str, Any]] = []
    for session in manager.sessions.values():
        record = session.record
        if record.state in {"exited", "crashed"}:
            continue
        project = getattr(projects, "projects", {}).get(record.project_id) if projects else None
        rows.append(
            {
                "id": record.id,
                "name": record.name,
                "backend": str(record.backend),
                "state": record.state,
                "project": getattr(project, "name", "") if project is not None else "",
            }
        )
    return sorted(rows, key=lambda row: (row["project"], row["name"]))


async def factory_reset_preview(request: web.Request) -> web.Response:
    """Everything the confirmation dialog needs, measured rather than described.

    Deliberately does no filesystem walk beyond one `iterdir` of the data
    directory and one of `worktrees/`: this is the panel's "are you sure", not
    the storage report, and a confirmation that takes ten seconds to appear
    trains people to click through it.
    """
    config: Config = request.app[keys.CONFIG]
    data_dir = config.data_dir
    return json_response(
        {
            "confirmation_phrase": CONFIRMATION_PHRASE,
            "data_dir": str(data_dir),
            "sessions": _live_sessions(request),
            "worktrees": worktree_directories(data_dir),
            "entries": planned_entries(data_dir),
            "kept": sorted(name for name in KEEP_ENTRIES if (data_dir / name).exists()),
            "external_left": [{"item": item, "detail": detail} for item, detail in EXTERNAL_LEFT],
            "relaunchable": keys.DAEMON_RELAUNCH_COMMAND in request.app,
            # Answered here as well as enforced on the press, so a phone is told
            # before it types the phrase rather than after. A refusal that only
            # arrives on the button reads as the button being broken.
            "local": is_loopback_peer(request.remote or ""),
            "last_result": read_result(data_dir),
        }
    )


def _refusal(reason: str, message: str, status: int = 409) -> web.Response:
    return json_response({"error": reason, "message": message}, status)


async def factory_reset(request: web.Request) -> web.Response:
    """Reap every session, then restart into a daemon that resets the install.

    The sequence is the whole correctness argument, and each step exists because
    the step after it cannot be trusted without it:

    1. **Write the request first.** It is the only durable record of consent. A
       reset that reaped sessions and then failed to record why would be an
       unexplained outage.
    2. **Reap the sessions and stop the supervisor.** The supervisor is a
       separate process holding live PTYs; nothing the daemon deletes can reach
       them, and a supervisor that survived would re-attach agents to an install
       that no longer exists. This is the one operation in swe-mux where reaping
       every session *is* the point rather than a cost.
    3. **Spawn the successor, then stop.** The successor performs the reset in a
       startup phase before any store opens a file - the only moment the data
       directory has no handles into it.
    """
    if not is_loopback_peer(request.remote or ""):
        return _refusal(
            "not_local",
            "a factory reset can only be started from the machine swe-mux runs on",
            403,
        )
    stop_event: asyncio.Event | None = request.app.get(keys.DAEMON_STOP_EVENT)
    relaunch: list[str] | None = request.app.get(keys.DAEMON_RELAUNCH_COMMAND)
    if stop_event is None or not relaunch:
        return _refusal(
            "restart_unavailable",
            "this daemon was not started with a relaunchable entry point, so it could "
            "reset the install but never come back. Start swe-mux normally and retry",
        )
    config: Config = request.app[keys.CONFIG]
    if system._redeploy_lock_pid(config) is not None:
        return _refusal(
            "redeploy_in_flight",
            "an app redeploy is running; wait for it to finish before resetting",
        )
    try:
        body = await request.json()
    except (ValueError, UnicodeDecodeError):
        body = {}
    supplied = str(body.get("confirm", "")) if isinstance(body, dict) else ""
    if supplied.strip().casefold() != CONFIRMATION_PHRASE.casefold():
        return _refusal(
            "not_confirmed",
            f'the confirmation phrase must be "{CONFIRMATION_PHRASE}"',
            400,
        )
    external = bool(body.get("external")) if isinstance(body, dict) else False
    sessions = _live_sessions(request)
    log.warning(
        "factory reset confirmed from %s: %d live session(s) will be reaped, external "
        "cleanup %s",
        request.remote,
        len(sessions),
        "requested" if external else "not requested",
    )
    write_request(config.data_dir, external=external)
    events: EventBus | None = request.app.get(keys.EVENTS)
    if events is not None:
        await events.emit("factory_reset_started", source="settings", sessions=len(sessions))
    # Before the successor is spawned, so it cannot come up and find the
    # supervisor still holding files it is about to move.
    reaped = await kill_server(config)
    request.app[keys.SHUTDOWN_STATE]["intent"] = "quit"
    await asyncio.to_thread(planned_handoff, config.data_dir, "quit")
    system._spawn_daemon_successor(list(relaunch), config.data_dir / "daemon-relaunch.log")
    stop_event.set()
    response = json_response(
        {
            "status": "resetting",
            "sessions_reaped": len(sessions),
            "supervisor_stopped": reaped,
            "external": external,
            # The daemon cannot reach the browser's own storage, and a reset that
            # left the last install's layouts, dismissed banners and remembered
            # tab in place would look like one that did not work. The client
            # clears its origin before it reloads.
            "clear_client_storage": True,
        },
        202,
    )
    response.headers["Cache-Control"] = "no-store"
    return response


ROUTES = (
    web.get("/api/maintenance/factory-reset", factory_reset_preview),
    web.post("/api/maintenance/factory-reset", factory_reset),
)
