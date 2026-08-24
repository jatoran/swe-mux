"""Daemon identity, the app shell, restart and redeploy, and host integration."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import shutil
import subprocess
import sys
from contextlib import suppress
from pathlib import Path
from typing import Any

from aiohttp import web

from .. import (
    app_keys as keys,
)
from ..bundle_locks import (
    REDEPLOY_LOCK_NAME,
    bundle_lock_holders,
    describe_holders,
    frozen_bundle_root,
    live_redeploy_lock_pid,
    write_redeploy_lock,
)
from ..config import Config
from ..event_bus import EventBus
from ..harness import (
    detect_installations_with_versions,
    public_harness_registry,
)
from ..http_support import is_loopback_peer, json_response
from ..lifecycle import planned_handoff
from ..logsetup import current_log_level, set_log_level
from ..prerequisites import detect_prerequisites
from ..processes import PreviewRegistry
from ..session import (
    SessionManager,
)
from ..spawn_contract import (
    scrub_claude_session_markers,
)
from ..startup_phases import StartupTimeline
from ..subprocess_flags import background_creation_flags, popen_outside_job
from ..tailscale import (
    enable_mobile_voice_serve,
    tailscale_ipv4,
    tailscale_status,
)
from ..ui_build import read_ui_build_id
from ..windows_firewall import (
    firewall_supported,
    inspect_firewall,
    repair_firewall,
    repair_wsl_firewall,
)
from ..wsl_bridge import WslBridgeError, wsl_adapter_subnet
from ..wsl_bridge import clear_status_cache as clear_wsl_status_cache
from ..wsl_bridge import install_bridge as install_wsl_bridge
from ..wsl_bridge import setup_status as wsl_setup_status

log = logging.getLogger(__name__)

#: The installed `swe_mux` package directory (`src/swe_mux` in a checkout).
PACKAGE_DIR = Path(__file__).resolve().parents[1]


# How long the daemon lingers after broadcasting `daemon_redeploy_stopping` so the
# frame reaches the `/events` sockets it is about to close. Long enough for a
# loopback and a tailnet write; short enough that it is noise against a swap and
# a cold PyInstaller start.
REDEPLOY_STOPPING_DRAIN_SECONDS = 0.35


async def index(request: web.Request) -> web.StreamResponse:
    path: Path = request.app[keys.FRONTEND_DIR] / "index.html"
    if not path.exists():
        return web.Response(
            text="swe-mux frontend is not built. Run: cd frontend; npm install; npm run build",
            content_type="text/plain",
        )
    return web.FileResponse(path)


async def manifest(request: web.Request) -> web.StreamResponse:
    path: Path = request.app[keys.FRONTEND_DIR] / "manifest.webmanifest"
    if not path.is_file():
        raise web.HTTPNotFound()
    return web.FileResponse(path)


async def service_worker(request: web.Request) -> web.StreamResponse:
    # Served from the origin root so its scope covers the whole app.
    path: Path = request.app[keys.FRONTEND_DIR] / "sw.js"
    if not path.is_file():
        raise web.HTTPNotFound()
    return web.FileResponse(
        path, headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-cache"}
    )


async def health(request: web.Request) -> web.Response:
    """Liveness *and* readiness, in one answer, from the moment the socket opens.

    The daemon now binds its listeners before it builds its runtime, so this
    endpoint answers during a start that used to refuse connections outright.
    That window is reported as HTTP 503 carrying the phase in flight and the
    phases already done, which is what turns a 5-minute wait from an outage into
    progress - both for the redeploy script and for whoever is watching it.

    `ok` and the status code move together, and both stay false until the
    runtime is built. Every existing consumer already reads one or the other as
    "not up yet"; the tray, the redeploy wait, and the browser's post-restart
    reload would each declare a daemon usable on a 200 here, and it would not be.
    """
    timeline: StartupTimeline | None = request.app.get(keys.STARTUP)
    startup = timeline.snapshot() if timeline is not None else {"status": "ready"}
    if timeline is not None and not timeline.ready:
        return json_response({"ok": False, "version": "0.1.0", **startup}, 503)
    sessions: SessionManager | None = request.app.get(keys.SESSIONS)
    live = sum(s.pty.isalive() for s in sessions.sessions.values()) if sessions else 0
    supervisor = request.app.get(keys.SUPERVISOR)
    connected = bool(supervisor is not None and supervisor.connected)
    # "lost" is deliberately distinct from "false": the supervisor is alive and
    # still holds live sessions, this daemon just cannot reach them. Reporting it
    # as absent hides sessions that are running and unkillable from here.
    lost = bool(supervisor is not None and getattr(supervisor, "lost", False))
    unadopted = int(getattr(sessions, "unadopted_supervisor_sessions", 0) or 0) if sessions else 0
    return json_response(
        {
            "ok": True,
            "live_sessions": live,
            "version": "0.1.0",
            "ui_build_id": read_ui_build_id(request.app[keys.FRONTEND_DIR]),
            "supervisor": connected,
            "supervisor_state": "connected" if connected else ("lost" if lost else "absent"),
            # Supervised sessions this daemon could not rebuild (snapshot drift,
            # a crash inside the spawn-meta window). They keep running under the
            # supervisor with no UI handle, so the count must be visible.
            "supervisor_unadopted": unadopted,
            # Sessions rebuilt from durable recovery data because their processes
            # died with a daemon that never recorded how they ended. A number
            # here is the signal that something took the whole app down.
            "cold_sessions": (
                sum(1 for s in sessions.sessions.values() if s.record.cold) if sessions else 0
            ),
            "session_recovery": request.app.get(keys.SESSION_RECOVERY) is not None,
            # The same block the starting answer carries, so one consumer reads
            # one shape either way - and so the phase breakdown of the start that
            # just finished stays readable without going to the log.
            **startup,
        }
    )


async def get_harnesses(request: web.Request) -> web.Response:
    config: Config = request.app[keys.CONFIG]
    # Detection touches the filesystem (PATH resolution and a data-home stat per
    # harness), so it runs off the event loop. The configured executable override
    # is passed through so detection agrees with what the launcher would run.
    installations = await asyncio.to_thread(
        detect_installations_with_versions, dict(config.harness_exe)
    )
    return json_response(public_harness_registry(installations))


async def get_log_level(request: web.Request) -> web.Response:
    return json_response({"level": current_log_level()})


async def put_log_level(request: web.Request) -> web.Response:
    """Runtime verbosity toggle: flip DEBUG on a live daemon, no restart.

    Applies to the root logger (console + rotating daemon.log) for this daemon
    process only; ``log_level`` in config remains the startup default.
    """
    body = await request.json()
    if not isinstance(body, dict) or not isinstance(body.get("level"), str):
        raise ValueError("body must be an object with a string 'level'")
    level = set_log_level(body["level"])
    log.info("log level set to %s via /api/debug/log-level", level)
    return json_response({"level": level})


async def desktop_shutdown(request: web.Request) -> web.Response:
    """Stop a desktop-managed daemon without exposing network shutdown authority.

    ``mode`` carries the shutdown intent one level down (session-preserving
    reload): "quit" (default) reaps every session including supervisor-owned
    ones; "restart" detaches, leaving supervisor-owned sessions running for the
    next daemon to reattach.
    """
    expected: str | None = request.app.get(keys.DESKTOP_CONTROL_TOKEN)
    shutdown_event: asyncio.Event | None = request.app.get(keys.DESKTOP_SHUTDOWN_EVENT)
    if expected is None or shutdown_event is None:
        raise web.HTTPNotFound()
    if not is_loopback_peer(request.remote or ""):
        raise web.HTTPForbidden(text="desktop control is loopback-only")
    authorization = request.headers.get("Authorization", "")
    supplied = authorization[7:].strip() if authorization.startswith("Bearer ") else ""
    if not supplied or not secrets.compare_digest(supplied, expected):
        raise web.HTTPForbidden(text="invalid desktop control token")
    try:
        body = await request.json()
    except (ValueError, UnicodeDecodeError):
        body = {}
    mode = str(body.get("mode", "quit")) if isinstance(body, dict) else "quit"
    if mode not in {"quit", "restart"}:
        raise web.HTTPBadRequest(text="mode must be quit or restart")
    intent = "quit" if mode == "quit" else "detach"
    request.app[keys.SHUTDOWN_STATE]["intent"] = intent
    # Recorded now rather than at the clean exit, because the caller that sent
    # this request is usually the redeploy script, and it terminates this
    # process as soon as health stops answering - several seconds before the
    # teardown reaches its clean-exit write. Without this the successor reported
    # a crash on every planned restart (`lifecycle.planned_handoff`).
    #
    # Looked up defensively for the same reason the broadcast below is: a
    # minimal desktop-control app carries no config, and a forensic breadcrumb
    # must never be the reason a shutdown request 500s instead of shutting down.
    shutdown_config: Config | None = request.app.get(keys.CONFIG)
    if shutdown_config is not None:
        await asyncio.to_thread(planned_handoff, shutdown_config.data_dir, intent)
    # The one authoritative "the outage starts now" signal a client can get: the
    # redeploy script stops the daemon through this endpoint, so the daemon is
    # still alive and still has its sockets when it learns the build finished.
    # Inferring it from a dropped socket instead would be indistinguishable from
    # an ordinary network blip, which is why the client wants to be told.
    await _announce_redeploy_stopping(request)
    shutdown_event.set()
    response = json_response({"status": "shutting_down", "mode": mode}, 202)
    response.headers["Cache-Control"] = "no-store"
    return response


async def _announce_redeploy_stopping(request: web.Request) -> None:
    """Broadcast the start of a redeploy's daemon-down window, then let it drain.

    Only for a shutdown that is part of a live redeploy: an ordinary desktop quit
    is not an outage anyone reconnects from. The sleep is the point - `emit` only
    puts the event on each subscriber's queue, and the shutdown that follows this
    call closes those sockets, so without a turn of the loop to write them the
    frame the client most needs would be the one it never receives.
    """
    # Everything here is looked up defensively. This runs on the daemon's way
    # out, including an ordinary desktop quit, and a courtesy broadcast must
    # never be the reason a shutdown request 500s instead of shutting down.
    config: Config | None = request.app.get(keys.CONFIG)
    events: EventBus | None = request.app.get(keys.EVENTS)
    if config is None or events is None or _redeploy_lock_pid(config) is None:
        return
    with suppress(Exception):
        await events.emit("daemon_redeploy_stopping", source="daemon", phase="stopping")
    await asyncio.sleep(REDEPLOY_STOPPING_DRAIN_SECONDS)


def _spawn_daemon_successor(command: list[str], log_path: Path) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("ab", buffering=0) as log_file:
        # Returns immediately; the successor runs --relaunch-wait and starts
        # once this daemon has released its listeners. Detached from this
        # process group so our exit never signals it, and broken away from any
        # Job this daemon inherited (a restart requested from inside a session
        # must not leave the successor in that session's kill-on-close Job).
        popen_outside_job(  # noqa: ASYNC220 - non-blocking Popen from a sync helper
            command,
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            cwd=str(log_path.parent),
            creationflags=background_creation_flags()
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        )


async def daemon_restart(request: web.Request) -> web.Response:
    """Session-preserving daemon self-restart (the UI/agent "reload daemon").

    Spawns a successor daemon (which waits for the port), signals this daemon
    to shut down with detach intent, and lets the successor reattach to the
    PTY supervisor's live sessions. Without an attached supervisor a restart
    would kill every session, so it is refused unless the caller passes
    ``{"force": true}`` — the same authority level as killing sessions.

    "Attached" means the supervisor process is alive, not that this daemon can
    currently talk to it. `connected` alone is the binary collapse that tri-state
    liveness removed everywhere else: while the socket is down but the supervisor
    is up (`client.lost`), the sessions are running and adoptable, and a restart
    is precisely the recovery `supervisor_client` logs and `doctor` recommends.
    Gating on `connected` refused that recovery, and the escape it advertised
    made things worse - the same flag decided the shutdown intent, so `force=true`
    quit rather than detached and reaped the sessions that were still alive.
    """
    stop_event: asyncio.Event | None = request.app.get(keys.DAEMON_STOP_EVENT)
    relaunch: list[str] | None = request.app.get(keys.DAEMON_RELAUNCH_COMMAND)
    if stop_event is None or not relaunch:
        return json_response(
            {
                "error": "restart_unavailable",
                "message": "this daemon was not started with a relaunchable entry point",
            },
            409,
        )
    supervisor = request.app.get(keys.SUPERVISOR)
    attached = bool(supervisor is not None and (supervisor.connected or supervisor.lost))
    try:
        body = await request.json()
    except (ValueError, UnicodeDecodeError):
        body = {}
    force = bool(body.get("force")) if isinstance(body, dict) else False
    if not attached and not force:
        return json_response(
            {
                "error": "supervisor_not_attached",
                "message": (
                    "the PTY supervisor is not attached, so a daemon restart would "
                    "kill every session; enable pty_supervisor_enabled or pass "
                    "force=true"
                ),
            },
            409,
        )
    config: Config = request.app[keys.CONFIG]
    intent = "detach" if attached else "quit"
    request.app[keys.SHUTDOWN_STATE]["intent"] = intent
    # Before the successor is spawned: it reads this record to decide how long
    # to wait for this daemon's drain, and to tell a planned handoff from a
    # crash once it starts.
    await asyncio.to_thread(planned_handoff, config.data_dir, intent)
    _spawn_daemon_successor(list(relaunch), config.data_dir / "daemon-relaunch.log")
    stop_event.set()
    response = json_response({"status": "restarting", "sessions_preserved": attached}, 202)
    response.headers["Cache-Control"] = "no-store"
    return response


def redeploy_source_root() -> Path | None:
    """The source checkout this daemon can rebuild itself from, if any.

    Frozen builds live at ``<root>/dist/swe-mux/swe-mux.exe`` inside the
    checkout; source runs resolve from this file. A frozen app deployed away
    from its checkout has neither, and redeploy is refused.
    """
    candidates: list[Path] = []
    if getattr(sys, "frozen", False):
        with suppress(OSError, IndexError):
            candidates.append(Path(sys.executable).resolve().parents[2])
    with suppress(OSError, IndexError):
        # Anchored on the package directory rather than counted from this file:
        # this handler used to live in `server.py`, one level up, and counting
        # from `__file__` would have silently repointed "the checkout" at `src/`
        # when it moved.
        candidates.append(PACKAGE_DIR.parents[1])
    for root in candidates:
        if (root / "packaging" / "redeploy_desktop.py").is_file() and (
            root / "pyproject.toml"
        ).is_file():
            return root
    return None


def _redeploy_lock_pid(config: Config) -> int | None:
    """PID of a live in-flight redeploy, or None (missing/stale lock).

    The lock is claimed by whoever starts the redeploy - this daemon for a
    UI/API trigger, the script itself when run straight from a terminal - and
    always names the *script* process, so the process is the authority and
    nothing has to clean the file up after a crash.

    "The process" means its identity rather than its number: a bare
    `pid_exists` made a completed redeploy's lock read as live forever once
    Windows recycled the pid, silently refusing every redeploy afterwards
    (`bundle_locks.REDEPLOY_LOCK_NAME`). Both readers share that rule so they
    cannot disagree about whether a redeploy is happening.
    """
    return live_redeploy_lock_pid(config.data_dir / REDEPLOY_LOCK_NAME)


#: Said in the same breath as the interruption list, every time. The reflex when
#: a redeploy is announced is to assume it will take your dev server with it, and
#: the reflex after that is to start killing things defensively. It will not:
#: `stop_app_processes` targets the app's own image, and even the blunt
#: `force_stop_app_images` escalation is scoped to `swe-mux.exe`.
REDEPLOY_INTERRUPTION_NOTE = (
    "Your servers keep running - a redeploy never stops them. Only swe-mux's proxy "
    "to them restarts, so these URLs are unreachable until the app is back."
)


def _redeploy_interruptions(request: web.Request) -> dict[str, Any]:
    """What a redeploy would make unreachable, without stopping anything.

    Read straight off the in-memory registry rather than through
    `PreviewRegistry.list`, which forces a process scan: this runs on the accept
    path and on every status poll, and an answer that is one detection cycle
    stale is worth far more than one that costs a scan.

    Reported, never enforced. Refusing a redeploy because a port is open would
    make it nearly un-runnable - there is almost always a dev server up - and
    redeploy is the only mechanism that ships anything, including the fix for a
    gate that refuses wrongly. The genuine blocker (a process anchoring the
    bundle, which really does fail the swap) is a separate, existing refusal.
    """
    previews: PreviewRegistry | None = request.app.get(keys.PREVIEWS)
    items = []
    if previews is not None:
        items = [
            {
                "id": item.id,
                "url": item.url,
                "host": item.host,
                "port": item.port,
                "source": item.source,
                "project_id": item.project_id,
                "session_id": item.session_id,
                # The proxy path a phone or a copied link actually uses. Stable
                # across the restart now (`preview_id`), so it is the same URL
                # afterwards - which is exactly what the caller wants to know.
                "proxy_path": f"/preview/{item.id}/",
            }
            for item in previews.items.values()
            if item.listed
        ]
    return {
        "previews": sorted(items, key=lambda entry: (entry["host"], entry["port"])),
        "kills_processes": False,
        "note": REDEPLOY_INTERRUPTION_NOTE,
    }


def _redeploy_last_result(config: Config) -> dict[str, Any] | None:
    """The previous redeploy's machine-readable outcome, or None.

    Written by the script at every terminal path. A rollback used to be visible
    only as English in `redeploy.log`, so the successor daemon came back with the
    OLD app and nothing told the operator: this is what lets the reconnecting UI
    say so once.
    """
    try:
        payload = json.loads((config.data_dir / "redeploy-result.json").read_text("utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


async def _announce_redeploy_started(request: web.Request, *, pid: int) -> None:
    """Tell every connected client a redeploy just began.

    The build stage runs for minutes with this daemon still serving, so clients
    learn about the redeploy long before it can affect them - which is the whole
    point: a UI that knows can show a progress chip and stay usable, instead of
    discovering the redeploy as a wall of failed requests when the daemon finally
    goes down.
    """
    events: EventBus | None = request.app.get(keys.EVENTS)
    if events is None:
        return
    with suppress(Exception):
        await events.emit("daemon_redeploy_started", source="daemon", pid=pid, phase="building")


async def daemon_redeploy(request: web.Request) -> web.Response:
    """Kick off the staged frozen-app redeploy (the UI "Rebuild + redeploy").

    Spawns ``packaging/redeploy_desktop.py`` detached from this daemon's
    lifetime and returns immediately. The script builds into staging while
    this daemon keeps serving, stops it only after a successful build, swaps
    the bundle in, and rolls back to the previous bundle if the new one never
    reports healthy — so a failed redeploy never strands a remote client.
    """
    config: Config = request.app[keys.CONFIG]
    root = redeploy_source_root()
    if root is None:
        return json_response(
            {
                "error": "no_source_checkout",
                "message": (
                    "this daemon is not running from a source checkout, so it cannot "
                    "rebuild itself; run the redeploy from the repo instead"
                ),
            },
            409,
        )
    uv = shutil.which("uv")
    if uv is None:
        return json_response(
            {"error": "uv_not_found", "message": "uv is required to run the redeploy script"},
            409,
        )
    try:
        body = await request.json()
    except (ValueError, UnicodeDecodeError):
        body = {}
    force = bool(body.get("force")) if isinstance(body, dict) else False
    supervisor = request.app.get(keys.SUPERVISOR)
    attached = bool(supervisor is not None and supervisor.connected)
    if not attached and not force:
        return json_response(
            {
                "error": "supervisor_not_attached",
                "message": (
                    "the PTY supervisor is not attached, so a redeploy would kill "
                    "every session; enable pty_supervisor_enabled or pass force=true"
                ),
            },
            409,
        )
    in_flight = _redeploy_lock_pid(config)
    if in_flight is not None:
        return json_response(
            {
                "error": "redeploy_in_progress",
                "message": f"a redeploy is already running (pid {in_flight})",
            },
            409,
        )
    if not force:
        # The swap's one non-retryable step is renaming dist/swe-mux, and a
        # foreign process anchoring it (a dev server behind a Preview tab, a
        # terminal cd'd into the bundle) survives everything the redeploy may
        # stop — sessions descend from the supervisor, which outlives the app.
        # Refuse with the holders named instead of failing after minutes of
        # build (measured live 2026-08-02: two redeploys died at this rename).
        bundle = frozen_bundle_root() or (root / "dist" / "swe-mux")
        holders = await asyncio.to_thread(bundle_lock_holders, bundle)
        if holders:
            return json_response(
                {
                    "error": "bundle_in_use",
                    "message": (
                        "the app bundle is held open, so the redeploy swap would "
                        f"fail: {describe_holders(holders)}. This is usually a dev "
                        "server or preview process, or a terminal whose working "
                        "directory is inside dist/swe-mux — stop those processes "
                        "(or close their tabs/sessions) and retry, or pass "
                        "force=true to attempt anyway."
                    ),
                    "holders": holders,
                },
                409,
            )
    lock_path = config.data_dir / REDEPLOY_LOCK_NAME
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    # `_redeploy_lock_pid` already reported no live redeploy, so any file still
    # here is stale (a crash between claiming the lock and writing the pid).
    # Leaving it would make O_EXCL refuse every future redeploy.
    with suppress(OSError):
        lock_path.unlink(missing_ok=True)
    try:
        # Claimed atomically *before* the spawn. Writing it afterwards let a
        # double-submit (desktop plus phone, or a double tap) start two staged
        # redeploys that then race the same dist/.staging tree and the swap.
        lock_handle = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return json_response(
            {"error": "redeploy_in_progress", "message": "a redeploy is already starting"},
            409,
        )
    os.close(lock_handle)
    log_path = config.data_dir / "redeploy.log"
    command = [
        uv,
        "run",
        "--project",
        str(root),
        "python",
        str(root / "packaging" / "redeploy_desktop.py"),
        "--restore-visibility",
        # The lock above is already claimed and already names this child, and the
        # start is broadcast below; without this the script would refuse itself.
        "--lock-held",
    ]
    # Without this the script targets ~/.mux, so a daemon on an alternate config
    # reads the wrong supervisor discovery file and aborts — or worse,
    # detach-stops a *different* instance while swapping the shared bundle.
    if (config_path := getattr(config, "config_path", None)) is not None:
        command += ["--config", str(config_path)]
    log_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with log_path.open("wb", buffering=0) as log_file:
            # Detached from this daemon's process group, lifetime, and any Job it
            # inherited: the script stops this very daemon mid-run, so it must not
            # die with it. cwd is the source root (never inside dist/, which would
            # lock the rebuild) and the env is scrubbed of parent-Claude session
            # markers.
            process = popen_outside_job(  # noqa: ASYNC220 - non-blocking Popen
                command,
                stdin=subprocess.DEVNULL,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                cwd=str(root),
                env=scrub_claude_session_markers(os.environ),
                creationflags=background_creation_flags()
                | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
            )
    except OSError:
        # The placeholder lock must not outlive a spawn that never happened.
        with suppress(OSError):
            lock_path.unlink(missing_ok=True)
        raise
    write_redeploy_lock(lock_path, process.pid)
    # Told to every client now, minutes before the daemon can actually go away,
    # which is what lets them show progress instead of discovering the redeploy
    # as failed requests. The script does not announce a run spawned from here.
    await _announce_redeploy_started(request, pid=process.pid)
    response = json_response(
        {
            "status": "redeploying",
            "pid": process.pid,
            "log": str(log_path),
            # An agent that triggered this gets the consequences in the same
            # reply, so it can say what it is about to interrupt rather than
            # discovering it as a dead proxy minutes later.
            "interrupted": _redeploy_interruptions(request),
        },
        202,
    )
    response.headers["Cache-Control"] = "no-store"
    return response


async def daemon_redeploy_announce(request: web.Request) -> web.Response:
    """Broadcast `daemon_redeploy_started` for a redeploy this daemon did not spawn.

    A redeploy run straight from a terminal (`uv run python
    packaging/redeploy_desktop.py`) is otherwise invisible to every client until
    the daemon vanishes underneath them. The script claims the same
    `redeploy.lock` and posts here, so a CLI redeploy and a UI redeploy look
    identical to the UI.

    Loopback-only, and refused unless the lock actually names a live process:
    this exists to describe a redeploy that is really happening, not to let
    anything put the fleet's UI into a fake maintenance mode.
    """
    config: Config = request.app[keys.CONFIG]
    if not is_loopback_peer(request.remote or ""):
        raise web.HTTPForbidden(text="redeploy announcements are loopback-only")
    pid = _redeploy_lock_pid(config)
    if pid is None:
        return json_response(
            {
                "error": "no_redeploy_in_flight",
                "message": "redeploy.lock names no live process, so there is nothing to announce",
            },
            409,
        )
    await _announce_redeploy_started(request, pid=pid)
    response = json_response({"status": "announced", "pid": pid}, 202)
    response.headers["Cache-Control"] = "no-store"
    return response


def _redeploy_log_tail(config: Config, *, running: bool) -> str:
    """The in-flight redeploy's build log, or "" when it is not that run's.

    Only a redeploy this daemon spawned writes `redeploy.log`; one launched from
    a terminal prints to its own stdout and leaves whatever the last endpoint
    run left there. Serving that file regardless makes the UI's progress chip
    show a *previous* redeploy's build output for the whole of this one, which
    reads as real progress and is not. The lock is created at run start, so a
    log older than the lock belongs to an earlier run.
    """
    log_path = config.data_dir / "redeploy.log"
    try:
        if running:
            lock_mtime = (config.data_dir / REDEPLOY_LOCK_NAME).stat().st_mtime
            if log_path.stat().st_mtime < lock_mtime:
                return ""
        return log_path.read_bytes()[-8192:].decode("utf-8", "replace")
    except OSError:
        return ""


async def daemon_redeploy_status(request: web.Request) -> web.Response:
    """Whether a redeploy is in flight, plus the tail of its build log.

    While the build stage runs this daemon is still alive, so the UI can
    detect an early build failure (running=false without ever losing the
    daemon) and surface the log instead of waiting out a reconnect window.
    """
    config: Config = request.app[keys.CONFIG]
    pid = _redeploy_lock_pid(config)
    tail = _redeploy_log_tail(config, running=pid is not None)
    response = json_response(
        {
            "running": pid is not None,
            "pid": pid,
            # Answering this at all means the daemon is up, so a live lock is
            # always the build stage: the stop/swap/relaunch stage has no daemon
            # to ask. The UI keeps the app fully usable while `phase` is
            # "building" and only blocks once it observes the daemon go away.
            "phase": "building" if pid is not None else "idle",
            "log_tail": tail.splitlines()[-40:],
            "last_result": _redeploy_last_result(config),
            # Served whether or not one is in flight: the confirm dialog reads it
            # before you commit, which is the only moment the answer can change
            # what you do.
            "interrupted": _redeploy_interruptions(request),
            "available": redeploy_source_root() is not None and shutil.which("uv") is not None,
        }
    )
    response.headers["Cache-Control"] = "no-store"
    return response


async def remote_status(request: web.Request) -> web.Response:
    config: Config = request.app[keys.CONFIG]
    return json_response(
        await tailscale_status(config.port, tailnet_enabled=config.tailnet_enabled)
    )


async def enable_mobile_voice(request: web.Request) -> web.Response:
    config: Config = request.app[keys.CONFIG]
    if request.headers.get("X-Mux-User-Gesture") != "mobile-voice-setup":
        return json_response({"error": "mobile voice setup requires an explicit user action"}, 400)
    if not config.tailnet_enabled:
        return json_response(
            {"error": "Enable the Tailscale listener in Settings before mobile voice."}, 409
        )
    result = await enable_mobile_voice_serve(config.port)
    return json_response(result, 200 if result.get("status") == "ready" else 409)


async def prerequisites_status(request: web.Request) -> web.Response:
    """Presence of Git, Node, npm, and Tailscale, each with what it backs and a next step."""
    del request
    return json_response({"prerequisites": await asyncio.to_thread(detect_prerequisites)})


async def firewall_status(request: web.Request) -> web.Response:
    """Whether Windows Defender Firewall admits phone connections to swe-mux.

    Off Windows (or off a frozen build) this reports ``supported: false`` and the
    UI hides the panel; the tailnet listener there is governed by the host's own
    firewall, covered by the reachability guidance instead.
    """
    config: Config = request.app[keys.CONFIG]
    if not firewall_supported():
        return json_response(await inspect_firewall(None))
    # The tailnet adapter's network category decides which firewall profile
    # governs the inbound phone connection, so its address seeds the lookup.
    # Serve state matters more: when Serve proxies the port over loopback, the
    # phone never touches the direct 100.x socket the firewall governs, so a
    # missing inbound rule is not a connect failure. Derive it so the panel does
    # not cry wolf on the normal (Serve) path.
    status = await tailscale_status(config.port, tailnet_enabled=config.tailnet_enabled)
    serve_active = bool(status.get("serve_configured") or status.get("mobile_voice_configured"))
    address = await tailscale_ipv4()
    return json_response(
        await inspect_firewall(config.port, address, serve_active=serve_active)
    )


async def firewall_repair(request: web.Request) -> web.Response:
    """Elevated one-click repair: drop blocking rules, add one scoped Allow rule.

    Requires an explicit user gesture header so a stray poll can never trigger a
    UAC prompt.
    """
    config: Config = request.app[keys.CONFIG]
    if request.headers.get("X-Mux-User-Gesture") != "firewall-repair":
        return json_response({"error": "firewall repair requires an explicit user action"}, 400)
    if not firewall_supported():
        return json_response({"ok": False, "reason": "unsupported"}, 409)
    result = await repair_firewall(config.port)
    return json_response(result, 200 if result.get("ok") else 409)


async def wsl_bridge_status(request: web.Request) -> web.Response:
    """What the WSL bridge would need on this host, answerable before enabling it.

    Deliberately does not require `wsl_bridge_enabled`. A user cannot be asked to
    turn something on before anything will tell them whether it would work, and
    the shipped diagnostic had exactly that shape - silent until after the decision
    it existed to inform.

    `?probe=1` inspects each distribution, which *starts* a stopped one and costs
    seconds. Off by default so opening a settings page never does that unasked.
    """
    config: Config = request.app[keys.CONFIG]
    probe = request.query.get("probe") in {"1", "true", "yes"}
    payload = await asyncio.to_thread(
        wsl_setup_status,
        daemon_port=config.port,
        enabled=bool(getattr(config, "wsl_bridge_enabled", False)),
        probe=probe,
    )
    return json_response(payload)


async def wsl_bridge_install(request: web.Request) -> web.Response:
    """Materialize the distro-side bridge into one distribution.

    A write into the user's distribution, so it takes an explicit gesture header
    for the same reason the firewall repair does: nothing a background poll can
    trigger should modify a machine.
    """
    if request.headers.get("X-Mux-User-Gesture") != "wsl-bridge-install":
        return json_response(
            {"error": "installing the bridge requires an explicit user action"}, 400
        )
    body = await request.json()
    distro = str(body.get("distro") or "").strip()
    if not distro:
        return json_response({"error": "distro is required"}, 400)
    config: Config = request.app[keys.CONFIG]
    try:
        status = await asyncio.to_thread(install_wsl_bridge, distro)
    except WslBridgeError as exc:
        return json_response({"ok": False, "reason": str(exc)}, 409)
    # The freshly written bridge invalidates whatever the status cache held.
    await asyncio.to_thread(clear_wsl_status_cache)
    del config
    return json_response({"ok": True, "bridge": status.as_dict()})


async def wsl_bridge_firewall_repair(request: web.Request) -> web.Response:
    """Elevated: add the inbound rule a bridged agent needs to reach the daemon.

    Separate from the tailnet repair rather than folded into it, because the two
    scopes are different and so is the consent: enabling the WSL bridge is not
    agreement to phone access, or the reverse.
    """
    config: Config = request.app[keys.CONFIG]
    if request.headers.get("X-Mux-User-Gesture") != "wsl-firewall-repair":
        return json_response({"error": "firewall repair requires an explicit user action"}, 400)
    subnet = await asyncio.to_thread(wsl_adapter_subnet)
    result = await repair_wsl_firewall(config.port, subnet)
    return json_response(result, 200 if result.get("ok") else 409)


ROUTES: tuple[web.RouteDef, ...] = (
    web.get("/", index),
    web.get("/manifest.webmanifest", manifest),
    web.get("/sw.js", service_worker),
    web.get("/api/health", health),
    web.get("/api/harnesses", get_harnesses),
    web.get("/api/debug/log-level", get_log_level),
    web.post("/api/debug/log-level", put_log_level),
    web.post("/api/desktop/shutdown", desktop_shutdown),
    web.post("/api/daemon/restart", daemon_restart),
    web.post("/api/daemon/redeploy", daemon_redeploy),
    web.get("/api/daemon/redeploy", daemon_redeploy_status),
    web.post("/api/daemon/redeploy/announce", daemon_redeploy_announce),
    web.get("/api/remote/status", remote_status),
    web.post("/api/remote/mobile-voice/enable", enable_mobile_voice),
    web.get("/api/remote/firewall", firewall_status),
    web.post("/api/remote/firewall/repair", firewall_repair),
    web.get("/api/wsl/bridge", wsl_bridge_status),
    web.post("/api/wsl/bridge/install", wsl_bridge_install),
    web.post("/api/wsl/bridge/firewall/repair", wsl_bridge_firewall_repair),
    web.get("/api/diagnostics/prerequisites", prerequisites_status),
)
