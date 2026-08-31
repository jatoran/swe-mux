"""HTTP/WebSocket control plane for the swe-mux daemon.

The daemon binds a single port (default 8765) and owns one data dir (~/.mux), so
exactly one instance may run per machine. Never start a second daemon from a
worktree: worktrees isolate the working tree, not the runtime, and the two
instances will fight over the same mux.db.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import mimetypes
import os
import time
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

from aiohttp import web

from . import (
    __version__,
    git_init,
    git_review,
    mcp_tools,
    routes,
)
from . import (
    app_keys as keys,
)
from .adapters import BackendAdapter, ShellAdapter, build_agent_adapter
from .agent_authority import authority_resolver
from .agent_context import AgentContextService
from .agent_messaging import AgentMessagingService
from .agent_worktree_context import session_occupies_worktree
from .assistant import (
    AssistantError,
    AssistantService,
    AssistantStore,
    apply_note_write,
)
from .attention_narration import AttentionNarrator
from .attention_ranking import AttentionRankingService
from .auto_delivery import AutoDeliveryController
from .automation import (
    AutomationEngine,
)
from .automation_registry import effective_global_allow
from .automation_registry import resolve_config as resolve_automation_config
from .automation_store import AutomationStore
from .background_tasks import background
from .behavioral_consumers import BehavioralConsumerService
from .build_support import precompress_static
from .clipboard_store import ClipboardStore
from .code_graph import CodeGraphStore
from .config import Config
from .db_maintenance import clear_request as clear_maintenance_request
from .db_maintenance import describe as describe_maintenance
from .db_maintenance import read_request as read_maintenance_request
from .db_maintenance import run_maintenance
from .deterministic_consumers import ConsumerContext, DeterministicConsumerService
from .device_presence import DevicePresenceStore
from .errors import NotFound
from .event_bus import EventBus
from .fleet_intelligence import FleetIntelligence
from .frontend_overlay import (
    OverlayStore,
    daemon_api_digest,
    log_choice,
    resolve_frontend_dir,
)
from .ghost_windows import GhostWindowSweeper
from .git_monitor import GitMonitor
from .git_provenance import GitProvenanceService
from .harness import (
    HARNESSES,
    enabled_backends,
)
from .history import HistoryIndex
from .history_backfill import HistoryBackfillManager
from .history_scan import HistoryScanManager
from .http_support import (
    REQUEST_ID_HEADER,
    REQUEST_ID_KEY,
    apply_security_headers,
    json_response,
    log_task_failure,
)
from .land_queue import LandQueueService
from .land_store import LandStore
from .launchers import (
    create_agent_shims,
    resolve_codex_pty_command,
    resolve_npm_shim_pty_command,
)
from .lifecycle import (
    HEARTBEAT_INTERVAL_SECONDS,
    daemon_clean_exit,
    daemon_started,
    heartbeat,
    heartbeat_pid,
    ledger,
    pid_running,
)
from .llm_endpoint import LLM_PROVIDERS, CapabilityStore, LlmReadiness
from .llm_endpoint import capabilities_of_record as llm_capabilities_of_record
from .llm_endpoint import readiness as llm_readiness
from .llm_endpoint import resolve_endpoint as resolve_llm_endpoint
from .logsetup import bound_request_id, new_request_id, valid_request_id
from .loop_lag import LoopLagMonitor
from .mcp import McpService
from .meta_hooks import MetaHookEngine
from .models import (
    ProjectRecord,
)
from .network_usage import (
    NetworkUsage,
    compressible_response_middleware,
    record_network_response,
)
from .openrouter import OpenRouterClient
from .operational_telemetry import OperationalTelemetryStore
from .path_identity import same_path
from .preview_store import PreviewStore
from .preview_transport import PREVIEW_HTTP_CONCURRENCY, PREVIEW_WS_CONCURRENCY
from .process_reaper import create_reaper
from .processes import PreviewRegistry, ProcessInspector
from .project_actions import (
    ProjectActionService,
    preview_action_run,
)
from .project_context import ProjectContext, ProjectContextService
from .project_files import (
    ProjectConfigConflict,
    ProjectFileRevisionConflict,
    ProjectImageUnavailable,
    ProjectNoteProtected,
    ProjectResourceExists,
    append_observation,
    project_automations,
    project_note_summaries,
    read_note,
    read_observations,
    read_project_config,
    read_project_config_values,
    write_note,
)
from .project_watcher import ProjectFileWatcher
from .projects import ProjectManager
from .prompt_library import PromptLibrary
from .prompt_queue import (
    PromptQueueService,
    PromptQueueStore,
    QueueError,
)
from .provider_accounts import (
    ProviderAccountConflict,
    ProviderAccountError,
    ProviderAccountManager,
)
from .push import PUSH_SENDER_LOOP, PushSender, PushStore
from .readiness_watch import ReadinessWatcher
from .reconcile import reconcile_external_history
from .routes import configurator as configurator_routes
from .routes import history as history_routes
from .routes import notes as notes_routes
from .routes import project_actions as project_action_routes
from .routes import sessions as session_routes
from .routes import terminal as terminal_routes
from .routes.support import _registered_identity
from .runtime_config import CONFIG_WATCH_LOOP, watch_config
from .scan_timeline import ScanContext, ScanTimelineService
from .schedule_store import ScheduleStore
from .scheduler import ScheduleService
from .schedules import ScheduleError
from .secret_store import PlatformSecretStore
from .session import (
    STATE_WATCHDOG_LOOP,
    Session,
    SessionManager,
)
from .session_attachments import (
    MAX_ATTACHMENT_BYTES,
)
from .session_control import AGENT_SPAWN_SETTLE_SECONDS, SessionControlService
from .session_media import cleanup_expired_preview_shots, cleanup_expired_session_media
from .session_recovery import SessionRecoveryStore
from .session_watch import SessionWatchService
from .settings_store import SettingsStore
from .spawn_probe import discard_pane, settle_pane
from .sqlite_store import (
    VerificationControl,
    begin_shutdown_drain,
    prepare_database,
    record_database_problem,
    record_database_verified,
    run_full_verification,
    verification_record_path,
)
from .startup_phases import StartupTimeline
from .status_timeline import StatusTimelineStore
from .storage_usage import ProjectFootprintTarget, StorageUsage
from .supervisor_client import SupervisorClient, SupervisorUnavailable
from .tailscale import (
    is_tailscale_ip,
)
from .tier0_store import Tier0Context, Tier0Store
from .update_check import UPDATE_CHECK_LOOP, UpdateChecker
from .update_install import UpdateInstaller
from .usage import UsageManager
from .voice import (
    VoiceError,
    VoiceService,
    VoiceStore,
)
from .worktree_mutation import sweep_graveyards
from .worktree_verify import (
    VerifyApprovalStore,
)

log = logging.getLogger(__name__)

#: Wall-clock ceiling on the assistant's own archive search.
#: Generous for an indexed FTS hit and far short of the minutes an unindexed
#: LIKE scan over a multi-gigabyte database takes. The point is not speed, it is
#: that the failure is a tool result the model can read instead of a wedged app.
ASSISTANT_HISTORY_SEARCH_BUDGET_MS = 4_000
Handler = Callable[[web.Request], Awaitable[web.StreamResponse]]
LOOP_LAG_LOOP = "loop-lag"
LIFECYCLE_HEARTBEAT_LOOP = "lifecycle-heartbeat"
DATABASE_INTEGRITY_LOOP = "database-integrity-check"
# How long the database-maintenance phase waits for a predecessor to release
# `mux.db`. Far longer than the ordinary startup gate's 20s, because this only
# runs when an operator asked for a compaction and is already expecting the
# daemon to be unavailable - and because on the development host the
# predecessor exceeded that 20s gate on every measured restart.
MAINTENANCE_PREDECESSOR_WAIT_SECONDS = 180.0
MAINTENANCE_WAIT_REPORT_SECONDS = 15.0
MEDIA_CLEANUP_LOOP = "media-cleanup"
RETENTION_LOOP = "store-retention"
# Startup past this is logged as a warning. A daemon reattaching a large fleet
# legitimately takes a few seconds; anything near the desktop shell's health
# budget is the shape of an incident, and it left no trace of its own until a
# 36s start expired the tray's wait and looked like a daemon that never started.
SLOW_STARTUP_SECONDS = 20.0
# The routes that may answer before the runtime exists. Everything else is
# refused with the current phase, because a handler reaching for a runtime
# handle that has not been built yet is a 500 that says nothing.
#
# Health is the point of the whole arrangement: a probe gets "starting, phase X"
# instead of a refused connection. The static document and its assets are here
# so a browser opened during a start renders the app shell and can show that
# answer, rather than failing to load at all; they read nothing but
# `frontend_dir`, which `create_app` sets before any listener binds.
STARTUP_OPEN_PATHS = frozenset({"/api/health", "/", "/manifest.webmanifest", "/sw.js"})
STARTUP_OPEN_PREFIXES = ("/assets/", "/icons/", "/notification-sounds/")
# How long a client is asked to wait before probing again while the runtime is
# still building. Long enough not to be a poll storm from a page full of panes,
# short enough that readiness is noticed promptly.
STARTUP_RETRY_AFTER_SECONDS = 3


@web.middleware
async def correlation_middleware(request: web.Request, handler: Handler) -> web.StreamResponse:
    """Give every request an id, put it on the response, and bind it for logging.

    Outermost of the middleware chain so that a refusal from the security or
    startup middleware is correlated too - those are exactly the requests an
    operator is trying to account for when they read `access.log` beside
    `daemon.log`.

    A well-formed inbound `X-Request-ID` is adopted rather than replaced, so a
    caller that already has a trace (the browser, an agent's MCP client, the
    redeploy script) keeps one id across the boundary. It is validated first:
    the value lands in a log file, and an unbounded one could forge a field
    boundary or a whole extra line there.

    The id is bound as a contextvar, not passed down: `asyncio.create_task` and
    `asyncio.to_thread` both inherit the context, so the background work a
    handler starts stays correlated after the response has been written -
    which is the span an incident actually covers.
    """
    supplied = request.headers.get(REQUEST_ID_HEADER, "").strip()
    request_id = supplied if valid_request_id(supplied) else new_request_id()
    request[REQUEST_ID_KEY] = request_id
    with bound_request_id(request_id):
        try:
            response = await handler(request)
        except web.HTTPException as exc:
            exc.headers[REQUEST_ID_HEADER] = request_id
            raise
    # A prepared response has already written its headers (every WebSocket
    # upgrade, and the preview passthrough's stream); stamping one there would
    # be a silent no-op rather than an error, and pretending otherwise is worse
    # than the absence.
    if not response.prepared:
        response.headers[REQUEST_ID_HEADER] = request_id
    return response


@web.middleware
async def error_middleware(request: web.Request, handler: Handler) -> web.StreamResponse:
    try:
        return await handler(request)
    except web.HTTPException:
        raise
    except NotFound as exc:
        # The deliberate half of the KeyError convention (`errors.NotFound`).
        # The key stays in the log and out of the body: echoing it back made a
        # 404 a reflection of arbitrary request text, and told the caller
        # nothing it did not already know.
        log.debug(
            "request_not_found method=%s path=%s kind=%s key=%s",
            request.method,
            request.path,
            exc.kind,
            exc.key,
        )
        return json_response({"error": str(exc), "code": "not_found", "kind": exc.kind}, 404)
    except ProviderAccountConflict as exc:
        # Distinct from a bad request: the caller must resolve an ownership
        # clash or explicitly force the action.
        return json_response({"error": str(exc), "conflict": True}, 409)
    except QueueError as exc:
        # Typed queue-operation failures carry their own status and a machine
        # code the queue UI branches on (head-of-line, revision, readiness).
        return json_response({"error": str(exc), "code": exc.code, **exc.payload}, exc.status)
    except git_review.GitReviewError as exc:
        log.warning(
            "git_review_request method=%s path=%s code=%s status=%s result=error",
            request.method,
            request.path,
            exc.code,
            exc.status,
        )
        return json_response({"error": str(exc), "code": exc.code}, exc.status)
    except ProjectConfigConflict as exc:
        # A field-scoped conflict, so it says which fields moved and hands back the
        # current file: the editor resyncs and shows the collision in place rather
        # than telling the operator to close the panel and open it again. Must
        # precede the generic `ValueError` clause below.
        return json_response(
            {
                "error": str(exc),
                "code": "revision_conflict",
                "conflicts": exc.fields,
                "current": exc.current,
            },
            409,
        )
    except ProjectFileRevisionConflict as exc:
        return json_response({"error": str(exc), "code": "revision_conflict"}, 409)
    except ProjectImageUnavailable as exc:
        return json_response({"error": str(exc), "code": "image_unavailable"}, 415)
    except ProjectResourceExists as exc:
        return json_response({"error": str(exc), "code": "resource_exists"}, 409)
    except ProjectNoteProtected as exc:
        # A policy refusal, not a malformed request: the note exists and the
        # caller's revision was current. The browser branches on the code to
        # explain the rule rather than reporting a failed delete.
        return json_response({"error": str(exc), "code": "note_protected"}, 409)
    except AssistantError as exc:
        # Typed assistant failures are user-visible refusals (disabled, budget
        # exhausted, unknown dialog), never internal errors.
        return json_response({"error": str(exc)}, 400)
    except VoiceError as exc:
        # Same class of refusal, translated centrally since 2026-08-29 rather than
        # route by route. Most voice routes already caught this and returned 409;
        # `check_lexicon` and `build_lexicon_entry` did not, and a frozen-app user
        # who had not yet acquired the speech closure met `500 internal server
        # error` on a surface that draws a tick or a cross - while the daemon log
        # held the exact sentence naming the button he needed to press.
        #
        # The lesson generalises past voice: a typed, user-visible error class
        # that is translated at the call sites can only be right where somebody
        # remembered, and the places nobody remembered are the ones users find.
        # The per-route clauses stay, because several of them choose a different
        # status or add fields; this is the floor under them.
        #
        # 409 rather than 400: these are conflicts with the daemon's current state
        # (an engine that is unavailable, an asset that is not downloaded), not
        # malformed requests, and it is the status the voice routes already use.
        log.info(
            "voice refusal method=%s path=%s code=%s reason=%s",
            request.method,
            request.path,
            exc.code or "-",
            exc,
        )
        return json_response(exc.as_payload(), 409)
    except ScheduleError as exc:
        # A ValueError subclass, so it must be caught before the generic clause
        # below: the schedule editor branches on the machine code and highlights
        # the exact field, which a bare message string cannot support.
        return json_response(
            {"error": str(exc), "code": exc.code, "fields": exc.fields}, exc.status
        )
    except (ValueError, ProviderAccountError) as exc:
        # `TypeError` used to be translated here too, and almost never meant a
        # bad request: it is what a handler raises when it calls something with
        # the wrong arguments. It now falls through to the 500 path with a
        # traceback. Route-level *validation* that wants a 400 raises
        # `ValueError`, which is the only one of the two a caller can act on.
        log.debug(
            "request_rejected method=%s path=%s error_type=%s",
            request.method,
            request.path,
            type(exc).__name__,
        )
        return json_response({"error": str(exc)}, 400)
    except SupervisorUnavailable as exc:
        # A refusal with a reason, not a bug. The one that matters is the spawn
        # the daemon deliberately fails rather than falling back in-process when
        # the socket died mid-spawn and the supervisor is still alive: a fallback
        # there is a coin flip on two agents in one workspace. Reaching the
        # generic clause below turned that into `500 internal server error`, so
        # the reason the daemon had just logged never got to the operator - who
        # is the only one who can act on it, by restarting the daemon.
        log.warning(
            "supervisor unavailable method=%s path=%s reason=%s",
            request.method,
            request.path,
            exc,
        )
        return json_response({"error": str(exc), "code": "supervisor_unreachable"}, 503)
    except TimeoutError as exc:
        # `TimeoutError` subclasses `OSError`, so it matched nothing above and
        # became a 500 whose body said nothing. A deadline that expired is an
        # upstream condition the caller can retry, and it has a message.
        log.warning(
            "request timed out method=%s path=%s reason=%s", request.method, request.path, exc
        )
        return json_response(
            {"error": str(exc) or "the operation timed out", "code": "timeout"}, 504
        )
    except Exception:
        # Where an accidental `KeyError` or `TypeError` now lands, with the
        # traceback that names the line. The request id is on this record too
        # (the correlation filter), so the 500 the caller saw and the traceback
        # that explains it can be matched without guessing from timestamps.
        log.exception(
            "unhandled request error method=%s path=%s", request.method, request.path
        )
        return json_response({"error": "internal server error"}, 500)


def allowed_browser_host(host: str) -> bool:
    normalized = host.strip().rstrip(".").casefold()
    return (
        normalized in {"localhost", "127.0.0.1", "::1"}
        or normalized.endswith(".ts.net")
        or is_tailscale_ip(normalized)
    )


def request_host(request: web.Request) -> str:
    raw = request.headers.get("Host", "")
    try:
        return urlsplit(f"//{raw}").hostname or ""
    except ValueError:
        return ""


def browser_origin_matches_request(origin: str, raw_host: str) -> bool:
    try:
        parsed_origin = urlsplit(origin)
        parsed_host = urlsplit(f"//{raw_host}")
        origin_port = parsed_origin.port
        request_port = parsed_host.port
    except ValueError:
        return False
    if parsed_origin.scheme not in {"http", "https"}:
        return False
    if not parsed_origin.hostname or not parsed_host.hostname:
        return False
    if parsed_origin.hostname.casefold() != parsed_host.hostname.casefold():
        return False
    # Browsers include a non-default port in Origin and Host. A missing port on both sides
    # also covers Tailscale Serve's ordinary HTTPS authority.
    return origin_port == request_port


def _apply_static_cache_headers(response: web.StreamResponse, request: web.Request) -> None:
    if request.path == "/":
        # The document names content-addressed assets and carries the UI build identity.
        # Revalidate it on every open/reload so a post-redeploy browser cannot boot stale HTML.
        response.headers["Cache-Control"] = "no-cache, must-revalidate"
    elif request.path.startswith("/assets/"):
        response.headers.setdefault("Cache-Control", "public, max-age=31536000, immutable")


@web.middleware
async def security_middleware(request: web.Request, handler: Handler) -> web.StreamResponse:
    host = request_host(request)
    if not allowed_browser_host(host):
        raise web.HTTPMisdirectedRequest(text="unsupported Host")
    mutating = request.method not in {"GET", "HEAD", "OPTIONS"}
    websocket = request.headers.get("Upgrade", "").casefold() == "websocket"
    origin = request.headers.get("Origin")
    if origin and (mutating or websocket):
        sandboxed_preview = request.path.startswith("/preview/") and origin == "null"
        if not sandboxed_preview and not browser_origin_matches_request(
            origin, request.headers.get("Host", "")
        ):
            raise web.HTTPForbidden(text="cross-origin browser control is not allowed")
    response = await handler(request)
    if websocket:
        return response
    apply_security_headers(response, request)
    _apply_static_cache_headers(response, request)
    return response


def startup_open(path: str) -> bool:
    """Whether this route may be served before the runtime is built."""
    return path in STARTUP_OPEN_PATHS or path.startswith(STARTUP_OPEN_PREFIXES)


@web.middleware
async def starting_middleware(request: web.Request, handler: Handler) -> web.StreamResponse:
    """Refuse routes whose state does not exist yet, naming the phase in flight.

    The daemon binds its listeners before it builds its runtime, so this window
    is real and a client will hit it. Answering 503 with the phase is the whole
    improvement over the previous behaviour, which was a refused connection: it
    is the difference between "the app is starting, it is reattaching sessions"
    and "there is nothing there".

    503 and not 200: every existing consumer - the tray's health probe, the
    redeploy script's wait, the browser's post-restart reload - already treats a
    non-2xx answer as "not up yet". Serving 200 with a `starting` body would make
    each of them declare victory on a daemon that cannot answer a single request.
    """
    timeline: StartupTimeline | None = request.app.get(keys.STARTUP)
    if timeline is None or timeline.ready or startup_open(request.path):
        return await handler(request)
    response = json_response(
        {
            "error": "the daemon is still starting",
            "code": "daemon_starting",
            **timeline.snapshot(),
        },
        503,
    )
    response.headers["Retry-After"] = str(STARTUP_RETRY_AFTER_SECONDS)
    return response


def publish(app: web.Application, handles: Mapping[web.AppKey[Any], Any]) -> None:
    """Write runtime handles into an application whose runner has already started.

    aiohttp freezes an Application once its runner starts and deprecates state
    writes from that point, on the assumption that every handle exists before
    the socket does. The daemon deliberately inverts that ordering: it binds
    first, so a client during a 226s start gets "starting, phase X" instead of a
    refused connection, and the runtime is published as it is built behind the
    open socket.

    `Application` is a MutableMapping and `_state` is the dict behind it, so this
    writes exactly the entries `app[key] = value` writes, minus a freeze check
    that exists to catch the accidental case rather than this deliberate one.
    Reads are unaffected - `request.app[keys.HISTORY]` is the same mapping either
    way. Overriding the check by subclassing is the alternative, and aiohttp
    deprecates subclassing `Application` as well; this keeps the coupling to one
    greppable line, pinned by `tests/test_startup_gate.py` so an aiohttp upgrade
    that moves `_state` fails loudly instead of silently dropping every handle.
    """
    for key, value in handles.items():
        app._state[key] = value


async def wait_runtime_ready(app: web.Application) -> None:
    """Block until the background runtime build has finished (or re-raise it).

    Callers that need a fully built daemon rather than a merely reachable one -
    the in-process test harnesses, above all - await this instead of assuming
    that a started server implies a populated app. Deliberately unbounded: the
    caller owns the deadline (`asyncio.timeout`), because how long a build may
    take depends on the fleet being rebuilt and not on this function.
    """
    build = app.get(keys.RUNTIME_BUILD)
    if build is not None:
        await build


def create_app(
    config: Config,
    *,
    frontend_dir: Path | None = None,
    desktop_control_token: str | None = None,
    desktop_shutdown_event: asyncio.Event | None = None,
    relaunch_command: list[str] | None = None,
) -> web.Application:
    if desktop_control_token is not None and desktop_shutdown_event is None:
        raise ValueError("desktop control requires a shutdown event")
    # `starting_middleware` sits inside the security check so an unauthorized
    # caller is still refused as unauthorized during the startup window, and
    # outside compression so a 503 is compressed like any other response.
    # `correlation_middleware` is outermost so that every answer - including the
    # two refusals above, which never reach a handler - carries an id, and so
    # that anything those middlewares log is correlated with it.
    app = web.Application(
        middlewares=[
            correlation_middleware,
            error_middleware,
            security_middleware,
            starting_middleware,
            compressible_response_middleware,
        ],
        client_max_size=MAX_ATTACHMENT_BYTES + 1024 * 1024,
    )
    app[keys.CONFIG] = config
    app[keys.NETWORK_USAGE] = NetworkUsage()
    app.on_response_prepare.append(record_network_response)
    # Every client snapshot carries this process-generation identity alongside
    # the session-local revision. Session revisions restart from zero when a
    # daemon adopts supervisor-owned PTYs, so revision alone cannot distinguish
    # a stale pre-restart response from the new daemon's current state.
    app[keys.DAEMON_GENERATION] = uuid4().hex
    # The single point where "which static tree does this daemon serve" is
    # decided, and deliberately still single: `FRONTEND_DIR` is read by the
    # assets/notification-sounds/icons static mounts below, by the precompressor,
    # and by three route modules, and teaching each of those about overlays would
    # be six places to keep in agreement instead of one.
    #
    # An explicit `frontend_dir` still wins outright. That is what an override
    # means to the callers that pass one, and it keeps every test that points the
    # daemon at a fixture tree independent of whatever is installed in a data dir.
    bundled_frontend = Path(__file__).parent / "static"
    # Both halves of the overlay's compatibility pin, computed once per start.
    # The API digest is the route table this process is about to register, which
    # is what catches a frontend built against endpoints this daemon does not
    # have - `__version__` cannot, because the frozen app is rebuilt from a
    # checkout that moves between releases while the version string does not.
    api_digest = daemon_api_digest()
    if frontend_dir is not None:
        app[keys.FRONTEND_DIR] = frontend_dir
    else:
        choice = resolve_frontend_dir(
            data_dir=config.data_dir,
            bundled=bundled_frontend,
            backend_version=__version__,
            api_digest=api_digest,
            enabled=bool(getattr(config, "frontend_overlay_enabled", True)),
        )
        log_choice(choice)
        app[keys.FRONTEND_CHOICE] = choice
        app[keys.FRONTEND_DIR] = choice.directory
    app[keys.FRONTEND_OVERLAY] = OverlayStore(
        config.data_dir, backend_version=__version__, api_digest=api_digest
    )
    app[keys.PREVIEW_HTTP_SEMAPHORE] = asyncio.Semaphore(PREVIEW_HTTP_CONCURRENCY)
    app[keys.PREVIEW_WS_SEMAPHORE] = asyncio.Semaphore(PREVIEW_WS_CONCURRENCY)
    app[keys.HOOK_INGRESS_WINDOWS] = {}
    app[keys.MCP_RATE_WINDOWS] = {}
    # Newest runtime tool inventory per session, published by the injected OMP
    # extension. In memory only: it describes one process generation, and a
    # snapshot that outlived its process would be a false liveness claim.
    app[keys.RUNTIME_INVENTORIES] = mcp_tools.LiveSnapshotStore()
    app[keys.MCP_TOOLS_WINDOWS] = {}
    app[keys.ATTACHMENT_LOCKS] = {}
    # Mutable holder because aiohttp freezes app keys once started; carries the
    # externally-signaled shutdown intent (quit vs restart/detach) to cleanup.
    app[keys.SHUTDOWN_STATE] = {"intent": None}
    if desktop_control_token is not None and desktop_shutdown_event is not None:
        app[keys.DESKTOP_CONTROL_TOKEN] = desktop_control_token
        app[keys.DESKTOP_SHUTDOWN_EVENT] = desktop_shutdown_event
    # Self-restart needs a stop trigger and relaunch command in every mode,
    # independent of desktop-control authority.
    if desktop_shutdown_event is not None:
        app[keys.DAEMON_STOP_EVENT] = desktop_shutdown_event
    if relaunch_command:
        app[keys.DAEMON_RELAUNCH_COMMAND] = list(relaunch_command)
    app.cleanup_ctx.append(runtime_context)
    # The route table is assembled from the per-domain modules in `routes/`;
    # `routes.all_routes()` fixes the registration order, which is what decides
    # whether a static path or a dynamic one wins a collision.
    app.add_routes(routes.all_routes())
    # Serve the editor engine's WebAssembly with the MIME type browsers require
    # for streaming compilation; Windows' registry often lacks this mapping and
    # nosniff would otherwise reject an application/octet-stream .wasm response.
    mimetypes.add_type("application/wasm", ".wasm")
    # Windows' registry rarely maps .webmanifest; without this the manifest is
    # served as octet-stream and Chrome refuses to treat the app as installable.
    mimetypes.add_type("application/manifest+json", ".webmanifest")
    assets = app[keys.FRONTEND_DIR] / "assets"
    if assets.is_dir():
        app.router.add_static("/assets", assets)
    notification_sounds = app[keys.FRONTEND_DIR] / "notification-sounds"
    if notification_sounds.is_dir():
        app.router.add_static("/notification-sounds", notification_sounds)
    icons = app[keys.FRONTEND_DIR] / "icons"
    if icons.is_dir():
        app.router.add_static("/icons", icons)
    return app


def _database_size_gb(path: Path) -> float:
    """The database's size in GB, or 0 when it cannot be read.

    Only ever used to annotate a log line, so an unreadable file is reported as
    zero rather than being allowed to interrupt a start.
    """
    try:
        return path.stat().st_size / 1e9
    except OSError:
        return 0.0


async def _lifecycle_heartbeat_loop(data_dir: Path) -> None:
    """Keep the heartbeat fresh so the next daemon can judge how this one died.

    Supervised like every other loop, and for a sharper reason than most: this
    loop's own death is indistinguishable from the daemon's death. One failed
    write (a locked file, a full disk) used to end it silently, after which the
    daemon kept running perfectly while every subsequent start reported it as
    "died without a clean shutdown" — a false forensic that sends the next
    investigation after a crash that never happened.
    """
    while True:
        await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)
        with background.iteration(LIFECYCLE_HEARTBEAT_LOOP):
            await asyncio.to_thread(heartbeat, data_dir)


async def runtime_context(app: web.Application):  # type: ignore[no-untyped-def]
    """Publish a startup report immediately; build the runtime behind it.

    aiohttp runs this during `AppRunner.setup()`, before any listener binds, so
    everything this context did inline used to be downtime: a measured 226.6s
    start with 30 live sessions was 226.6s of refused connections, and the tray,
    the redeploy script and the browser could not tell it from a hung daemon.

    So the build moved into a task and this context returns at once. The socket
    opens, `/api/health` answers "starting, phase X" from `app[keys.STARTUP]`, and
    `starting_middleware` refuses every route whose state does not exist yet.
    Readiness is a real signal rather than an assumption: `wait_runtime_ready`
    is how a caller that needs the built daemon waits for one.

    A build that fails still ends the daemon, exactly as an exception raised
    inline used to - see `_build_runtime`. Half-alive-forever is not an option
    that existed before this change and must not become one.
    """
    config: Config = app[keys.CONFIG]
    # Read *before* `daemon_started`, which stamps our own pid over it. The
    # database-maintenance phase needs to know whether the predecessor is still
    # holding `mux.db`, and by the time that phase runs the heartbeat names this
    # process - so a probe taken there reads self and proceeds into a locked
    # file, which is exactly what happened twice against the real database.
    predecessor_pid = heartbeat_pid(config.data_dir)
    if predecessor_pid == os.getpid():
        predecessor_pid = -1
    # Death forensics first: report a predecessor that vanished without a clean
    # shutdown while this daemon is still barely started, then keep our own
    # heartbeat fresh so the next daemon can do the same for us. The verdict
    # also decides how hard the database-integrity phase must look: an
    # unplanned death forces the full probe.
    predecessor_died_uncleanly = daemon_started(config.data_dir, log)
    app[keys.PREDECESSOR_PID] = predecessor_pid
    timeline = StartupTimeline(log, ledger=lambda message: ledger(config.data_dir, message))
    app[keys.STARTUP] = timeline
    watchdog = asyncio.create_task(timeline.watchdog(), name="startup-watchdog")
    build = asyncio.create_task(
        _build_runtime(app, timeline, predecessor_died_uncleanly), name="daemon-runtime-build"
    )
    app[keys.RUNTIME_BUILD] = build
    try:
        yield
    finally:
        watchdog.cancel()
        if not build.done():
            build.cancel()
        await asyncio.gather(build, watchdog, return_exceptions=True)
        await _teardown_runtime(app)


async def _build_runtime(
    app: web.Application, timeline: StartupTimeline, predecessor_died_uncleanly: bool = False
) -> None:
    """Build the runtime, and stop the daemon if it cannot be built.

    The failure path is the reason this wrapper exists. While the build ran
    inline, an exception propagated out of `AppRunner.setup()` and the process
    died - loudly, and in a way the desktop shell and the redeploy script both
    already handle. Now that the listener is already open, an unhandled build
    failure would instead leave a daemon serving 503 forever, which is a worse
    outcome than the crash it replaced. So a failure is recorded on the
    timeline (a probe reads a reason, not a stall) and then asks the daemon to
    stop through the same event a restart uses.
    """
    try:
        await _build_runtime_handles(app, timeline, predecessor_died_uncleanly)
    except asyncio.CancelledError:
        raise
    except BaseException as error:  # noqa: BLE001 - re-raised after being reported
        timeline.fail(error)
        log.exception("daemon runtime build failed; stopping the daemon")
        stop_event: asyncio.Event | None = app.get(keys.DAEMON_STOP_EVENT)
        if stop_event is not None:
            stop_event.set()
        raise


async def _hydrate_llm_capabilities(
    capabilities: CapabilityStore, store: AutomationStore
) -> None:
    """Load what each provider's endpoint was last measured to be capable of.

    Runs before the first completion can, so nothing observes the store in its
    empty state. Extracted from the composition root rather than inlined there
    for a reason the linter noticed and is right about: `_build_runtime_handles`
    already sits exactly on the complexity ceiling, and this is a self-contained
    unit of work rather than another phase of assembly.

    A provider with no row keeps the unproven default, which is both the honest
    reading and the one that changes nothing for an install that never verified.
    """
    for provider in LLM_PROVIDERS:
        if row := await store.provider_verification(provider):
            capabilities.set(provider, llm_capabilities_of_record(row))


async def _restore_durable_sessions(
    config: Config,
    sessions: SessionManager,
    recovery: SessionRecoveryStore,
    projects: ProjectManager,
) -> None:
    """Restore explicit inactive rows, then optional unexpected-loss rows."""
    def project_exists(project_id: str) -> bool:
        return project_id in projects.projects
    try:
        inactive = await sessions.restore_inactive_sessions(project_exists=project_exists)
        if inactive:
            log.info("restored %d inactive session(s) from the durable registry", inactive)
    except Exception:
        log.exception("inactive session restoration failed")
    if config.session_recovery_enabled:
        try:
            restored = await sessions.restore_cold_sessions(project_exists=project_exists)
            if restored:
                log.info("restored %d cold session(s) from recovery data", restored)
        except Exception:
            log.exception("cold session restore failed")
    try:
        # A `discard` that died between deleting the row and the files, or a
        # quarantined database, both leave directories nothing will ever read.
        await recovery.sweep_orphan_directories(await recovery.known_ids())
    except Exception:
        log.exception("could not sweep orphan recovery directories")


async def _wait_for_exclusive_database(
    config: Config, predecessor_pid: int, operations: tuple[str, ...]
) -> bool:
    """Wait for the predecessor to release `mux.db`. True when it is ours.

    Separate from the ordinary startup gate on purpose, and much more patient
    than it. `wait_for_predecessor_exit` bounds its wait at 20s because a wedged
    predecessor must never stop a restart; that is the right trade for every
    start except this one, where an operator has explicitly asked for work that
    cannot run without exclusive ownership and is already expecting the daemon
    to be unavailable. Measured on the development host: the predecessor exceeded
    the 20s gate on both real `compact-db` runs, so without this the feature
    simply never fires.

    Reports while it waits, because this is the startup path and a silent minute
    here is the failure mode the whole timeline exists to prevent.
    """
    if predecessor_pid <= 0 or not pid_running(predecessor_pid):
        return True
    log.warning(
        "database maintenance (%s) is pending and the previous daemon (pid %d) still "
        "holds %s; waiting up to %.0fs for it to exit",
        ", ".join(operations),
        predecessor_pid,
        config.database_path,
        MAINTENANCE_PREDECESSOR_WAIT_SECONDS,
    )
    deadline = time.monotonic() + MAINTENANCE_PREDECESSOR_WAIT_SECONDS
    reported = time.monotonic()
    while time.monotonic() < deadline:
        if not pid_running(predecessor_pid):
            log.warning(
                "previous daemon pid %d has exited; this start owns the database",
                predecessor_pid,
            )
            return True
        if time.monotonic() - reported >= MAINTENANCE_WAIT_REPORT_SECONDS:
            reported = time.monotonic()
            log.warning(
                "still waiting for previous daemon pid %d to exit (%.0fs left)",
                predecessor_pid,
                deadline - time.monotonic(),
            )
        await asyncio.sleep(0.25)
    log.warning(
        "previous daemon pid %d did not exit within %.0fs, so this start cannot own %s. "
        "The maintenance request is kept and the next start will try again; if this "
        "repeats, stop swe-mux fully once and start it again.",
        predecessor_pid,
        MAINTENANCE_PREDECESSOR_WAIT_SECONDS,
        config.database_path,
    )
    return False


async def _run_pending_maintenance(config: Config, predecessor_pid: int = -1) -> None:
    """Honour a `swemux compact-db` request, if one is pending.

    Returns immediately when there is none, which is every ordinary start - the
    phase costs a single failed file read.

    **This start is not guaranteed to own the database, and the first version of
    this assumed it did.** `wait_for_predecessor_exit` waits for the predecessor
    process, but the wait is bounded at 20s and a timeout is deliberately a
    warning rather than a refusal - a wedged predecessor must not stop a
    restart. Against the real `mux.db` the gate gave up, this ran 74ms later,
    and `VACUUM` failed with `database is locked` because it must be the only
    connection. So exclusivity is *checked* here, and a lock is retryable rather
    than terminal.

    Which failures keep the request is the whole correctness question:

    - **The window was not available** (the predecessor is still alive, or the
      file is locked): the request survives, because it will succeed on a later
      start and consuming it silently drops what the operator asked for.
    - **The operation does not work** (an unknown operation, an unreadable
      database, no disk): the request is consumed, because it will fail the same
      way every time and a standing request would make every start slow.

    Never raises. A daemon that will not start because a compaction failed is
    strictly worse than one that starts on an uncompacted database and says so.
    """
    request = read_maintenance_request(config.data_dir)
    if request is None:
        return
    # The predecessor must be gone before this can work, and on this machine it
    # routinely is not: `wait_for_predecessor_exit` gives up at 20s and starts
    # anyway (correctly - a wedged predecessor must not block a restart), and
    # both real `compact-db` runs then found the file locked.
    #
    # So wait again, here, for much longer. That is affordable exactly because a
    # compaction is pending: an operator asked for this and is already expecting
    # the daemon to be unavailable, whereas an ordinary start is right to refuse
    # to wait. A predecessor that never exits keeps the request rather than
    # failing it.
    if not await _wait_for_exclusive_database(config, predecessor_pid, request.operations):
        return
    log.warning(
        "database maintenance requested (%s); the daemon will not serve until it "
        "finishes. Live sessions are held by the PTY supervisor and are unaffected.",
        ", ".join(request.operations),
    )
    control = VerificationControl()
    try:
        result = await asyncio.to_thread(
            run_maintenance, config.database_path, request, control
        )
    except asyncio.CancelledError:
        control.cancel()
        # Kept, not consumed: being asked to stop is not the operation failing,
        # and the operator still wants it done.
        log.warning("database maintenance cancelled; the request is kept for the next start")
        raise
    except Exception:  # noqa: BLE001 - a failed compaction must not stop the daemon
        log.exception("database maintenance raised; continuing without it")
        clear_maintenance_request(config.data_dir)
        return
    if result.retryable:
        log.warning(
            "%s. The request is kept and the next start will try again.",
            describe_maintenance(result),
        )
        if result.backup_path is not None:
            log.warning("a pre-compaction copy was left at %s", result.backup_path)
        return
    clear_maintenance_request(config.data_dir)
    log.log(logging.ERROR if result.error else logging.WARNING, "%s", describe_maintenance(result))
    if result.backup_path is not None:
        log.warning(
            "the pre-compaction copy is at %s; delete it once you are satisfied "
            "(it is the size of the database)",
            result.backup_path,
        )
    if result.error is None and result.performed:
        # The file was rewritten, so any recorded verdict describes bytes that no
        # longer exist. Forcing a fresh check is cheap now that it runs behind
        # the ready daemon, and it is the honest state.
        with contextlib.suppress(OSError):
            verification_record_path(config.database_path).unlink()


async def _database_integrity_loop(path: Path, delay_seconds: float, reason: str) -> None:
    """Run the owed full `PRAGMA quick_check` once, behind the serving daemon.

    Runs once and returns; the task supervisor treats a loop that returns
    normally as finished on purpose. It is a supervised task rather than a bare
    one so that it is named in `/api/diagnostics/background`, restarted if it
    faults, and stopped by name at teardown like everything else.

    Two things it deliberately does not do. It does not quarantine: every store
    is already open on this file, so moving it aside here would pull the
    database out from under live sessions. It records the failure instead, and
    `prepare_database` turns that record into a quarantine on the next start,
    before the first store connects. And it does not report a cancelled walk as
    a problem - being asked to stop is not evidence about the file.
    """
    await asyncio.sleep(delay_seconds)
    control = VerificationControl()
    started = time.monotonic()
    try:
        result = await asyncio.to_thread(run_full_verification, path, control)
    except asyncio.CancelledError:
        # `asyncio.to_thread` does not cancel its worker, and the loop joins what
        # is abandoned at `shutdown_default_executor` - after the daemon has
        # already reported a clean stop. Interrupting is what keeps that join
        # instant instead of up to 78s of unexplained silence.
        control.cancel()
        log.info(
            "database integrity: full check of %s cancelled after %.2fs (daemon stopping)",
            path,
            time.monotonic() - started,
        )
        raise
    if result.cancelled:
        return
    if result.problem is None:
        record_database_verified(path)
        log.info(
            "database integrity: background full check of %s (%.2f GB) passed in %.2fs "
            "(%.2fs of it prefetch) because %s",
            path,
            _database_size_gb(path),
            result.seconds,
            result.prefetch_seconds,
            reason,
        )
        return
    record_database_problem(path, result.problem)
    log.error(
        "database integrity: background full check of %s (%.2f GB) FOUND A PROBLEM after "
        "%.2fs: %s. The daemon keeps serving - every store is already open on this file - "
        "and the verdict is recorded, so the next start quarantines it as "
        "%s.corrupt-<ts> and recreates the schema. Native transcripts remain the "
        "authoritative source for anything rebuildable from them; restart when convenient.",
        path,
        _database_size_gb(path),
        result.seconds,
        result.problem,
        path,
    )


def _precompress_frontend(frontend_dir: Path) -> None:
    """Refresh the static tree's gzip sidecars, and say what that cost.

    Reported at INFO only when it did something, because the steady state is a
    no-op and a line on every start would train a reader to skip it. A failure is
    a WARNING and never an exception: a static tree on a read-only filesystem
    means larger downloads, which is not a reason to refuse to start.
    """
    result = precompress_static(frontend_dir)
    if result.failed:
        log.warning(
            "could not precompress %d static asset(s) under %s; they will be served "
            "uncompressed",
            result.failed,
            frontend_dir,
        )
    if result.changed:
        log.info(
            "precompressed %d static asset(s) in %.2fs (%d already current, %d orphan "
            "sidecar(s) removed, %d -> %d bytes)",
            result.written,
            result.seconds,
            result.kept,
            result.orphans_removed,
            result.source_bytes,
            result.encoded_bytes,
        )


async def _build_runtime_handles(  # noqa: PLR0915 - one composition root, phase by phase
    app: web.Application, timeline: StartupTimeline, predecessor_died_uncleanly: bool = False
) -> None:
    config: Config = app[keys.CONFIG]
    background.start(LIFECYCLE_HEARTBEAT_LOOP, lambda: _lifecycle_heartbeat_loop(config.data_dir))
    # Published before anything can be appended to it, because the first entry is
    # now started at the very top of the build (below) rather than near the end:
    # teardown cancels and gathers this list, and a task created before the list
    # exists is a task nothing stops.
    deferred_tasks: list[asyncio.Task[Any]] = []
    publish(app, {keys.STARTUP_DEFERRED_TASKS: deferred_tasks})
    # Connecting to the supervisor - or spawning one and waiting for its
    # discovery file - depends on nothing else this function builds, and it is
    # nearly all waiting: measured 4.39s on the 2026-08-30 post-reboot start.
    # Started here and awaited at its own phase, so it runs underneath
    # everything between instead of after it. The phase then measures the
    # residual wait, which is the honest number - if the supervisor is already
    # connected by the time the phase opens, the daemon really did wait 0s for
    # it.
    supervisor_connect: asyncio.Task[SupervisorClient] | None = None
    if config.pty_supervisor_enabled:
        supervisor_connect = asyncio.create_task(
            SupervisorClient.connect_or_spawn(config), name="supervisor-connect"
        )
        deferred_tasks.append(supervisor_connect)
    # First, because it is the UI's own readiness and depends on nothing else
    # here. The wheel and the sdist deliberately carry no precompressed sidecars
    # (they were 35% of the download, re-compressing what the archive had already
    # compressed), so the daemon makes them instead: a measured 0.93 s once, after
    # an install or an upgrade, and a stat-and-CRC pass that writes nothing on
    # every start after that. In a thread because it is CPU-bound and this path
    # must not block the event loop the health endpoint answers on.
    timeline.mark("static-precompress")
    await asyncio.to_thread(_precompress_frontend, app[keys.FRONTEND_DIR])
    # `PRAGMA quick_check` reads every page of the database and eleven stores
    # share `mux.db`, so this used to be paid eleven times, on the event loop,
    # inside whichever store constructor happened to touch the file first: 11.5s
    # per pass against a measured 2.73 GB file, ~126s per start, logged nowhere.
    # It became once-per-file, then conditional, and since 2026-08-30 it is not
    # on this path at all: the full check is *scheduled* here and runs behind the
    # ready daemon (`_database_integrity_loop`). What remains is the milliseconds
    # header-and-schema probe, which is the only verdict a start can act on
    # anyway - quarantining is safe only before the first store connects.
    # `sqlite_store.prepare_database` holds the argument for why an unclean
    # predecessor death no longer blocks a start: under `journal_mode=wal` with
    # `synchronous=FULL` a process or OS crash cannot corrupt the file, so the
    # 77.7s that signal cost on the 2026-08-30 post-reboot start was spent
    # answering a question the storage layer had already answered.
    # Before the integrity probe and before any store opens the file, because
    # this is the one moment the daemon owns it exclusively: the predecessor
    # process has exited (`__main__.wait_for_predecessor_exit`) and no store has
    # connected yet. `VACUUM` and a cross-file table move both need that, and no
    # running daemon can ever provide it.
    #
    # This deliberately puts minutes of work on the startup path, against the
    # rule the rest of this function now follows. The exception is narrow and
    # explicit: an operator typed `swemux compact-db`, it happens once, the phase
    # reports itself while it runs, and the alternative is stopping swe-mux,
    # which reaps every live session. Nothing schedules it and no route triggers
    # it.
    timeline.mark("database-maintenance")
    await _run_pending_maintenance(config, app.get(keys.PREDECESSOR_PID, -1))
    timeline.mark("database-integrity")
    preparation = await asyncio.to_thread(
        prepare_database,
        config.database_path,
        predecessor_died_uncleanly=predecessor_died_uncleanly,
    )
    # Always one line: when corruption surfaces later, what this start checked -
    # and what it deferred - is the first forensic question. A found problem is a
    # WARNING here; the quarantine itself still logs its own ERROR from
    # `connect_or_quarantine` when the first store opens the file.
    log.log(
        logging.WARNING if preparation.problem else logging.INFO,
        "database integrity: %s check of %s (%.2f GB) took %.2fs because %s%s%s",
        preparation.mode,
        config.database_path,
        _database_size_gb(config.database_path),
        preparation.seconds,
        preparation.reason,
        f"; found: {preparation.problem}" if preparation.problem else "",
        (
            f"; a full check is scheduled in {preparation.full_check_delay_seconds:.0f}s"
            if preparation.full_check_owed
            else ""
        ),
    )
    # Started here rather than after `timeline.finish()` so that a cancelled
    # build still stops it: `background.start` registers the task with the
    # supervisor, and teardown stops it by name whether or not the build
    # completed. It sleeps out its delay before touching the file, so it costs
    # this path nothing but the registration.
    if preparation.full_check_owed:
        background.start(
            DATABASE_INTEGRITY_LOOP,
            lambda: _database_integrity_loop(
                config.database_path,
                preparation.full_check_delay_seconds,
                preparation.reason,
            ),
        )
    timeline.mark("stores")
    history = HistoryIndex(config.database_path)
    events = EventBus(history.append_event)
    telemetry = OperationalTelemetryStore(
        config.database_path,
        retention_days=config.operational_telemetry_retention_days,
        process_retention_days=config.process_evidence_retention_days,
    )
    # Retention is housekeeping and belongs to `TELEMETRY_RETENTION_LOOP`, which
    # runs it 5s after start and hourly after that. The identity repair below is
    # not: it hides false runs that would otherwise be served as history from the
    # first request, and it is index-backed rather than a scan of the retention
    # tables, so it stays on the startup path. Measured at 4ms against a 4,909-row
    # history table, so there is nothing here to defer even if it were deferrable.
    timeline.mark("history-identity-reconcile")
    historical_identity_repairs = await history.reconcile_historical_provider_collisions()
    for session_id, false_run_id, canonical_root_run_id in historical_identity_repairs:
        await telemetry.quarantine_agent_run_provider_observations(
            session_id, false_run_id, canonical_root_run_id
        )
        await events.emit(
            "session_identity_history_reconciled",
            session_id=session_id,
            source="daemon",
            false_agent_run_id=false_run_id,
            root_agent_run_id=canonical_root_run_id,
        )
    if historical_identity_repairs:
        log.warning(
            "quarantined %d historical provider collision(s)",
            len(historical_identity_repairs),
        )
    # Pruned by `RETENTION_LOOP` a minute after start, not here.
    timeline.mark("stores")
    tier0 = Tier0Store(config.database_path, retention_days=config.process_evidence_retention_days)
    # Phase 7.9 structural graph, maintained off the same Tier 0 file_write stream
    # by the deterministic consumer and read by the blast-radius/navigation MCP
    # tools and the per-session change map. Shares mux.db.
    code_graph_store = CodeGraphStore(config.database_path)
    publish(app, {keys.CODE_GRAPH: code_graph_store})
    # Durable per-session detection timeline: every ledger entry survives
    # daemon restarts and session ends so status incidents stay investigable
    # (status-detection.md § durable timeline). Pruned by its own flush loop.
    # The durable registry is always present because an explicit inactive session
    # must survive even when unexpected-crash recovery is disabled. That switch
    # controls only cold restoration and terminal checkpoint capture.
    session_recovery = SessionRecoveryStore(
        config.database_path,
        config.data_dir / "recovery",
        checkpoint_bytes=(
            config.session_recovery_checkpoint_bytes if config.session_recovery_enabled else 0
        ),
        retention_days=config.session_recovery_retention_days,
        max_cold_sessions=config.session_recovery_max_sessions,
    )
    status_timeline = StatusTimelineStore(
        config.database_path, retention_days=config.status_timeline_retention_days
    )
    timeline.mark("projects")
    projects = ProjectManager(history)
    await projects.start()
    agent_context = AgentContextService(config.data_dir / "agent-context-backups")
    # Hashes each Project's instruction files so a later sync can tell a change
    # this daemon made from one the user made. It is per-Project file I/O on a
    # path that has no business blocking the loop - and with the listener now
    # open, blocking the loop is blocking the health answer.
    captured_roots = tuple(project.root for project in projects.projects.values())

    def _capture_project_instructions() -> None:
        for root in captured_roots:
            agent_context.capture_project(root)

    await asyncio.to_thread(_capture_project_instructions)
    history_backfills = HistoryBackfillManager(history, projects)
    history_scan = HistoryScanManager(history, config)
    reaper = create_reaper()
    timeline.mark("supervisor-connect")
    supervisor_client: SupervisorClient | None = None
    if supervisor_connect is not None:
        try:
            connected_client = await supervisor_connect
            supervisor_client = connected_client
            log.info(
                "PTY supervisor connected (pid %d, %d existing session(s))",
                connected_client.supervisor_pid,
                len(connected_client.initial_sessions),
            )
        except Exception:
            # The one place the default-on supervisor is allowed to fail, and it
            # must fail *here* rather than anywhere upstream: a daemon that will
            # not start is far worse than an unsupervised one, so every reason a
            # supervisor cannot be reached or spawned - no dedicated bundle
            # beside a frozen app, a loopback port that cannot be taken, a child
            # that dies before it writes its discovery file - degrades to
            # in-process spawning and says so once, loudly, with the traceback.
            log.exception(
                "PTY supervisor unavailable; sessions will run in-process and "
                "will not survive a daemon restart. This is a degraded start: "
                "the supervisor is on by default. See supervisor-console.log in "
                "the data directory, and `swemux doctor`"
            )
    timeline.mark("adapters-and-shims")
    mcp_url = f"http://127.0.0.1:{config.port}/mcp"
    adapters: dict[str, BackendAdapter] = {"shell": ShellAdapter(config.shell_exe)}
    for name, harness in HARNESSES.items():
        adapters[name] = build_agent_adapter(
            harness,
            executable=config.harness_exe[name],
            args=config.harness_args[name],
            data_dir=config.data_dir,
            # Per-harness toggles (absent key = on). Empty mcp_url drops the mux MCP
            # registration; instrument=False launches without lifecycle hooks. Both
            # are restart-scoped because adapters are built once here.
            mcp_url=mcp_url if config.harness_mcp_enabled.get(name, True) else "",
            instrument=config.harness_instrument_enabled.get(name, True),
            # Skill delivery defaults OFF - the opposite of the MCP map - because
            # its non-Claude half writes into the user's checkout at spawn.
            skill=config.harness_skill_enabled.get(name, False),
            approval_hook_timeout=config.approval_hook_timeout_seconds,
            # A harness that declares `requires_direct_entrypoint` has an argument a
            # `.cmd` shim cannot carry, so its JS entrypoint is launched directly.
            # Every other npm-shipped harness reads its shim generically, which needs
            # no knowledge of the package layout.
            command_resolver=(
                resolve_codex_pty_command
                if harness.requires_direct_entrypoint
                else resolve_npm_shim_pty_command
            ),
        )
    # Shim-launched harnesses need the per-session artifacts their adapter would
    # otherwise materialize. Only an adapter that keeps a settings file has one to
    # hand over, so the shims take whichever adapters expose the attributes rather
    # than a named harness.
    child_env = create_agent_shims(
        config,
        harness_settings={
            name: (
                getattr(adapter, "settings_path", None),
                getattr(adapter, "mcp_config_path", None),
            )
            for name, adapter in adapters.items()
            if name in HARNESSES
        },
    )
    sessions = SessionManager(
        adapters,
        reaper,
        history,
        events,
        config.scrollback_bytes,
        f"http://127.0.0.1:{config.port}",
        child_env,
        hook_spool_dir=config.data_dir / "hook-spool",
        supervisor=supervisor_client,
        attach_replay_bytes=config.attach_replay_bytes,
        status_timeline=status_timeline,
        recovery=session_recovery,
    )
    # The observer decides an approval; this is the only way it can deliver one.
    # Installed as a factory rather than called from `observation.py` directly
    # because `terminal_routes._record_operator_input` owns the evidence accounting every human
    # input path owes delivery readiness, and that lives here with the event bus.
    # Per-session bindings are attached by the manager as sessions are created,
    # adopted, and cold-restored (`_attach_operator_input`).
    def _operator_input_sink(session: Session) -> Callable[[str, str], None]:
        def write(data: str, source: str) -> None:
            terminal_routes._record_operator_input(events, session, data, source=source)

        return write

    sessions.operator_input_sink_factory = _operator_input_sink
    # Bounds the observer reads off the session rather than importing config,
    # matching how the approval stabilization window is already published.
    sessions.session_defaults.update(
        approval_keystroke_delivery=config.approval_keystroke_delivery,
        approval_keystroke_window_seconds=config.approval_keystroke_window_seconds,
    )
    timeline.mark("session-reattach")
    if supervisor_client is not None:
        try:
            adopted = await sessions.adopt_supervisor_sessions()
            if adopted:
                log.info("reattached %d live session(s) from the PTY supervisor", adopted)
        except Exception:
            log.exception("supervisor session adoption failed")
        for repaired_session_id, root_run_id in sessions.identity_repairs:
            try:
                await telemetry.reset_session_provider_observations(
                    repaired_session_id, root_run_id
                )
            except Exception:
                log.exception(
                    "could not reset misattributed provider telemetry for session %s",
                    repaired_session_id,
                )
    timeline.mark("cold-session-restore")
    if session_recovery is not None:
        # Strictly after adoption: an open recovery row for a session the
        # supervisor just handed back describes a *live* process, and restoring
        # it as cold would present a running agent as a dead pane.
        await _restore_durable_sessions(config, sessions, session_recovery, projects)
    try:
        # Runs after both recovery paths have claimed what they can, so it can
        # only close rows that genuinely have no live pane behind them.
        closed = await history.close_orphaned_runs(history_routes._live_history_run_ids(sessions))
        if closed:
            log.info("closed %d history run(s) left open by an unclean shutdown", closed)
    except Exception:
        log.exception("could not close orphaned history runs")

    timeline.mark("services")

    # The monitor's branch-scoped diff is measured against the same base the Git
    # drawer uses, so the sidebar and the drawer cannot report a session's branch
    # against two different refs. A Project that has vanished infers, like any
    # Project that never set an override.
    def _project_compare_ref(project_id: str) -> str | None:
        project = projects.projects.get(project_id)
        return project.git_compare_ref if project else None

    git_monitor = GitMonitor(
        sessions, events, config.git_poll_seconds, compare_override=_project_compare_ref
    )
    # Tier 0 is what makes contributor attribution possible: the write facts a
    # commit's changed files are matched against. Without it (or with Tier 0 off
    # for a Project) committer attribution still works and contributors stay empty.
    git_provenance_service = GitProvenanceService(history, sessions, events, tier0)
    hooks = MetaHookEngine(config.data_dir / "hooks.toml", events, sessions)
    # Pruned by `RETENTION_LOOP` a minute after start, not here.
    automation_store = AutomationStore(config.database_path)
    secret_store = PlatformSecretStore(config.data_dir / "automation.secrets.json")
    # Hydrated from the durable verification row below, before anything can call.
    # Empty here is the honest starting state and not a hazard: a miss yields the
    # unproven profile, which is how every custom endpoint behaved before it was
    # possible to measure one.
    llm_capabilities = CapabilityStore()
    openrouter = OpenRouterClient(
        secret_store,
        timeout_seconds=config.openrouter_request_timeout_seconds,
        # A callable, not a value: `config` is mutated in place by the settings
        # write and by the file watcher, so re-resolving per request is what lets
        # a corrected base URL take effect on the very next call - which is the
        # verify press itself, and would otherwise need a daemon restart to test.
        # The capability store is read through the same closure and for the same
        # reason: a verification changes what the endpoint is allowed to do, and
        # that must land on the next call rather than the next restart.
        endpoint=lambda: resolve_llm_endpoint(config, llm_capabilities),
    )
    await _hydrate_llm_capabilities(llm_capabilities, automation_store)
    openrouter.set_model_catalog((await automation_store.model_cache())["models"])
    automation = AutomationEngine(
        config.data_dir / "rules.toml",
        events,
        sessions,
        automation_store,
        config,
        openrouter,
    )
    usage = UsageManager(config, events)
    provider_accounts = ProviderAccountManager(
        config.data_dir,
        events,
        executables=config.harness_exe,
        poll_seconds=config.provider_quota_poll_minutes * 60,
        telemetry=telemetry,
        sessions=sessions,
        turn_refresh_enabled=config.provider_quota_turn_refresh_enabled,
        turn_refresh_min_seconds=config.provider_quota_turn_refresh_min_minutes * 60,
    )
    # Late-wired, in this direction only: the account manager already holds the
    # session manager, so a session asking it for the current account at spawn
    # has to arrive as a callable rather than as a second reference.
    sessions.provider_attribution = provider_accounts.spawn_attribution
    process_inspector = ProcessInspector(
        sessions,
        events,
        cadence=config.process_poll_seconds,
        telemetry=telemetry,
        orphan_grace_seconds=config.process_orphan_grace_seconds,
    )
    ghost_windows = GhostWindowSweeper(
        cadence=config.ghost_window_poll_seconds,
        enabled=config.ghost_window_sweep_enabled,
    )
    previews = PreviewRegistry(
        process_inspector, sessions, store=PreviewStore(config.data_dir)
    )
    fleet = FleetIntelligence(
        sessions, events, automation_store, process_inspector, previews, config
    )
    fleet.automation = automation
    voice_store = VoiceStore(config.database_path)
    voice = VoiceService(config, events, sessions, voice_store, automation_store, openrouter)
    prompt_queue_store = PromptQueueStore(config.database_path)
    prompt_queue = PromptQueueService(
        prompt_queue_store,
        sessions,
        events,
        fleet.readiness,
        # Queue delivery is operator input and must share the single accounting
        # helper (input_owner=False: the sender holds no ownership claim on the
        # target's PTY, same as broadcast).
        lambda session, data: terminal_routes._record_operator_input(
            events, session, data, source="queue", input_owner=False
        ),
    )
    # Phase 5: the one non-human caller of send_next, and the relay policy over
    # enqueue. Neither is a second delivery path — both call the typed queue
    # operations above (`CONTROL_PLANE_ROADMAP.md` §7.1).
    auto_delivery = AutoDeliveryController(prompt_queue, sessions, config)
    # Display-only sibling of the controller above: it presses nothing and only
    # announces when a verdict changes, so the surfaces that show readiness stop
    # depending on an unrelated event happening to trigger a fleet refresh.
    readiness_watch = ReadinessWatcher(sessions, fleet.readiness, prompt_queue, events)
    agent_messaging = AgentMessagingService(
        prompt_queue,
        sessions,
        projects,
        config,
        auto_delivery,
        append_observation=append_observation,
        read_observations=read_observations,
        interject_grant_field=authority_resolver(config, "interject_grant"),
        envelope_field=authority_resolver(config, "message_envelope"),
    )
    prompt_library = PromptLibrary(config.data_dir)
    settings_store = SettingsStore(config.data_dir)
    clipboard = ClipboardStore(
        config.database_path,
        enabled=config.clipboard_history_enabled,
        persist=config.clipboard_history_persist,
        limit=config.clipboard_history_limit,
        entry_max_chars=config.clipboard_history_entry_max_chars,
        retention_hours=config.clipboard_history_retention_hours,
        redact_secrets=config.clipboard_history_redact_secrets,
    )
    # Adopts the persisted ring when persistence is on, and deletes any rows left
    # behind by an earlier persisted run when it is off.
    await clipboard.load()
    push_store = PushStore(config.data_dir)
    device_presence = DevicePresenceStore()
    project_actions = ProjectActionService(config.data_dir)
    project_watcher = ProjectFileWatcher(projects, events, config)
    telemetry.start(events, sessions=sessions, history=history)

    automation_gate_cache: dict[str, tuple[float, frozenset[str]]] = {}
    publish(app, {keys.AUTOMATION_GATE_CACHE: automation_gate_cache})
    # The install-wide half of the gate, cached beside the per-Project half and on
    # the same clock. Its input is a config read plus one SQLite row, which is
    # cheap but not free, and `_enabled_automations` runs on every Tier 0 write.
    llm_readiness_cache: dict[str, tuple[float, LlmReadiness]] = {}
    publish(app, {keys.LLM_READINESS_CACHE: llm_readiness_cache})
    publish(app, {keys.LLM_CAPABILITIES: llm_capabilities})

    async def _llm_ready() -> LlmReadiness:
        """Whether a proven model provider exists, and the sentence saying why not.

        Recomputed from the live config and the durable verification row rather
        than cached as a boolean at startup: an endpoint is edited and verified
        while the daemon runs, and a readiness answer that needed a restart would
        make the verify button appear to do nothing.
        """
        now = time.monotonic()
        cached = llm_readiness_cache.get("current")
        if cached and now - cached[0] < 5.0:
            return cached[1]
        endpoint = resolve_llm_endpoint(config, llm_capabilities)
        record = (
            await automation_store.provider_verification(endpoint.provider)
            if endpoint.requires_verification
            else None
        )
        answer = llm_readiness(
            endpoint,
            api_key=secret_store.get(endpoint.secret_name),
            verified_fingerprint=str((record or {}).get("fingerprint") or "") or None,
        )
        llm_readiness_cache["current"] = (now, answer)
        return answer

    publish(app, {keys.LLM_READY: _llm_ready})

    async def _enabled_automations(root: str) -> frozenset[str]:
        """Per-project opt-in closure, resolved off-loop with a short TTL cache.

        Shared by Tier 0 capture and the deterministic consumers so a project can
        never have one running under a stale answer the other already refreshed.

        The provider check joins the resolution here rather than at each call
        site, so an unverified endpoint makes exactly the model-backed
        automations inert - through the same DAG, at the same chokepoint - and
        leaves the free consumers over them running on the records they already
        have. `Resolution.unverified` carries which ones and the status endpoint
        carries why, so the switch reads as held back rather than as broken.

        The install-wide ceiling joins it at the same chokepoint: an automation
        the operator disallowed globally (`automation_global_allow`, plus the
        scan timeline's dedicated switch) is off in every Project along with
        everything that depends on it, however the Project's own map reads.
        """
        now = time.monotonic()
        cached = automation_gate_cache.get(root)
        if cached and now - cached[0] < 5.0:
            return cached[1]
        project_map = await asyncio.to_thread(project_automations, root)
        ready = await _llm_ready()
        enabled = resolve_automation_config(
            project_map,
            llm_ready=ready.ready,
            global_allow=effective_global_allow(
                config.automation_global_allow,
                scan_timeline_enabled=config.scan_timeline_enabled,
            ),
        ).enabled
        automation_gate_cache[root] = (now, enabled)
        return enabled

    # Exposed so module-level endpoints (Phase 7.7 scan-timeline consumers) can
    # resolve a Project's opt-in closure the same way the in-loop consumers do.
    publish(app, {keys.AUTOMATION_GATE: _enabled_automations})

    async def _settled_spawn_failure(manager: Any, session: Any) -> str | None:
        """Wait out an agent-created pane's settle window; discard one that died.

        The two halves belong together: a pane that failed to take its launch is
        not a degraded success, so it is taken back out of the world rather than
        handed to the caller as a session id it will keep asking about. The words
        returned are the harness's own (`spawn_probe.pane_text`), which is what
        makes this work for a refusal no version of mux has seen.
        """
        failure = await settle_pane(session, AGENT_SPAWN_SETTLE_SECONDS)
        if failure is None:
            return None
        await discard_pane(manager, session)
        return failure.describe()

    # Phase 7.6 session control. Every bound lives here in the daemon operation;
    # the MCP tools are thin callers. The interrupt and graceful-end operations
    # are the shared daemon ops the browser and CLI would call too, bound to this
    # app.
    session_control = SessionControlService(
        sessions=sessions,
        projects=projects,
        config=config,
        events=events,
        readiness_evaluate=prompt_queue.readiness.evaluate,
        automation_gate=_enabled_automations,
        grant_field=authority_resolver(config, "session_control_grant"),
        interrupt_op=lambda session: terminal_routes._interrupt_session_pty(app, session),
        graceful_end_op=lambda session, reason: terminal_routes._end_session_gracefully(
            app, session, reason
        ),
        is_daemon_owner=terminal_routes._session_owns_daemon,
        spawn_grant_field=authority_resolver(config, "spawn_grant"),
        # The granted spawn goes through the identical spawn path the browser and
        # the Fleet Queue approval use, so an agent-created session is spawned no
        # differently from any other.
        spawn_op=lambda body: session_routes._spawn_from_body(app, body),
        # ...and the proof that it came up, which only this path takes: an
        # operator watching a pane appear sees it die, an agent does not.
        settle_op=lambda session: _settled_spawn_failure(sessions, session),
        draft_spawn=agent_messaging.request_spawn,
        append_observation=append_observation,
        read_observations=read_observations,
    )
    publish(app, {keys.SESSION_CONTROL: session_control})

    # Session-settle watches. Same shape again: the service owns the bounds and
    # the MCP tool is a caller. The only write it produces is a `rule`-sender
    # queue item addressed to the session that armed the watch, so it borrows the
    # land queue's handback path exactly rather than growing a delivery path.
    async def _session_watch_notice(**kwargs: Any) -> dict[str, Any]:
        """The notice, through the ordinary Phase 5 queue and nothing else.

        A watcher that ended between the sweep's liveness check and this enqueue
        is not an error worth raising: there is nobody left to read the notice,
        and the resolution is already in the log and the event stream.
        """
        try:
            return await prompt_queue.enqueue(**kwargs)
        except QueueError as exc:
            if exc.code in {"target_ended", "unknown_target"}:
                log.info("session_watch_target_gone code=%s", exc.code)
                return {}
            raise

    session_watch = SessionWatchService(
        sessions=sessions,
        projects=projects,
        config=config,
        events=events,
        queue_message=_session_watch_notice,
    )
    publish(app, {keys.SESSION_WATCH: session_watch})

    # Phase 14 land queue. Same shape as session control: every bound lives in the
    # service and the MCP tool and HTTP route are thin callers. The trunk is the
    # Project root and the ref is the one the Git drawer and the session monitor
    # already share, so no third opinion about "the base" can appear here.
    land_store = LandStore(config.data_dir / "land-queue.sqlite3")
    publish(app, {keys.LAND_STORE: land_store})
    verify_approvals = VerifyApprovalStore(config.data_dir)
    publish(app, {keys.VERIFY_APPROVALS: verify_approvals})

    async def _land_compare_ref(root: str) -> str | None:
        project = next(
            (item for item in projects.projects.values() if same_path(item.root, root)),
            None,
        )
        override = getattr(project, "git_compare_ref", None) if project else None
        resolved = await git_review.resolve_comparison_ref(root, override)
        return resolved.get("ref")

    async def _land_project_values(root: str) -> dict[str, Any]:
        """The Project's *config values*, which is what command resolution reads.

        Handed the whole envelope instead, `resolve_worktree_command` looked for
        `worktree` at the top level, found nothing, and silently fell through to the
        `.worktree-verify` convention - so `[worktree] verify_command` was declared,
        documented, and inert. `read_project_config_values` is the named accessor that
        makes that mistake impossible to repeat quietly.
        """
        return await read_project_config_values(root)

    async def _land_busy_sessions(worktree_root: str) -> tuple[str, ...]:
        """Sessions living in this checkout that are not safe to merge underneath.

        A session counts when its live cwd or its current-run land binding is the
        worktree, and when it is doing something a merge would disturb. The second
        path is what makes a Codex session whose host process stayed on trunk protect
        the checkout where its commands are writing. Starting up counts: a harness
        that has not settled is exactly the one whose first act may be writing files.
        """
        busy: list[str] = []
        for session in sessions.sessions.values():
            record = session.record
            if record.state in {"exited", "crashed"}:
                continue
            if not session_occupies_worktree(record, worktree_root):
                continue
            if record.state in {"starting", "working", "awaiting"}:
                busy.append(str(record.id))
        return tuple(busy)

    def _land_origin_run(session_id: str) -> str:
        """The run id the origin session is on now, for the handback's run binding.

        A land request records the run that made it; a session that resumed into a
        new conversation is a different correspondent, and its predecessor's consent
        is not its own (`land_queue.py`, `_reply_arming`).
        """
        session = sessions.sessions.get(str(session_id))
        return str(getattr(getattr(session, "record", None), "agent_run_id", "") or "")

    async def _land_queue_message(**kwargs: Any) -> dict[str, Any]:
        """The handback, through the ordinary Phase 5 queue and nothing else.

        A `rule` sender, so it is a bounded deterministic template rather than a new
        agent-to-agent path. It arrives armed only when it is the answer to the
        target's own `request_land` and the service says so; every readiness and
        auto-delivery gate still decides the send. A target that has ended is not an
        error worth raising: the branch's agent is gone, and the land row already
        records why it stopped.
        """
        try:
            return await prompt_queue.enqueue(**kwargs)
        except QueueError as exc:
            if exc.code in {"target_ended", "unknown_target"}:
                log.info("land_handback_target_gone code=%s", exc.code)
                return {}
            raise

    async def _land_draft(
        *,
        project_root: str,
        project_id: str,
        worktree_root: str,
        branch: str,
        origin_session_id: str,
        origin_run_id: str = "",
        reason: str = "",
    ) -> dict[str, Any]:
        origin = sessions.sessions.get(origin_session_id)
        origin_name = origin.record.name if origin else origin_session_id
        body = f"{origin_name} asks to land {branch or 'its branch'}" + (
            f": {reason}" if reason.strip() else ""
        )
        appended = await append_observation(
            project_root,
            body,
            kind="land_request",
            request={
                "action": "land",
                "worktree_root": worktree_root,
                "project_root": project_root,
                "branch": branch,
                "reason": str(reason or "")[:500],
                "from_session": origin_session_id,
                "from_name": origin_name,
                "from_run_id": origin_run_id,
                "project_id": project_id,
                "status": "pending",
            },
        )
        request_id = str(appended.get("appended_id") or "")
        await events.emit(
            "agent_land_drafted",
            session_id=origin_session_id or None,
            source="agent",
            request_id=request_id,
            project_id=project_id,
        )
        return {"request_id": request_id}

    land_queue_service = LandQueueService(
        store=land_store,
        approvals=verify_approvals,
        config=config,
        events=events,
        automation_gate=_enabled_automations,
        grant_field=authority_resolver(config, "land_grant"),
        verify_grant_field=authority_resolver(config, "land_verify_grant"),
        project_values=_land_project_values,
        comparison_ref=_land_compare_ref,
        busy_sessions=_land_busy_sessions,
        session_run=_land_origin_run,
        queue_message=_land_queue_message,
        record_fact=tier0.record_fact if tier0 is not None else None,
        draft_request=_land_draft,
    )
    publish(app, {keys.LAND_QUEUE: land_queue_service})
    # The second half of the same consent: a session that asked to land is the waiting
    # half of an exchange it opened, so its grant must not lapse while the pipeline is
    # still computing the answer. Registered here rather than injected at construction
    # because the controller is built well before the land service exists.
    auto_delivery.set_solicited_requests(land_queue_service.origin_windows)

    # Phase 10.6 Mux assistant: daemon-owned dialogs behind the voice grammar's
    # tier-3 fallback and the workspace chat surface. Reuses the identical
    # interrupt/end/spawn operations the session-control service is built on,
    # so an assistant-driven mutation travels no path of its own.
    assistant_store = AssistantStore(config.database_path)

    async def _assistant_note_summaries(project: Any) -> list[dict[str, Any]]:
        titles: dict[str, str] = {}
        owners = await history.note_owner_labels(project.id)
        titles.update(
            {str(note_id): str(owner.get("name") or note_id) for note_id, owner in owners.items()}
        )
        for session in sessions.sessions.values():
            if session.record.project_id == project.id:
                titles[session.record.id] = session.record.name
        return await asyncio.to_thread(
            project_note_summaries,
            project.root,
            default_note_id=project.id,
            default_title=f"{project.name} notes",
            legacy_titles=titles,
        )

    async def _assistant_note_list(project_id: str) -> list[dict[str, Any]]:
        project = projects.projects.get(project_id)
        if project is None:
            raise ValueError("unknown project")
        return await _assistant_note_summaries(project)

    async def _assistant_resolve_note(
        project: Any, note_reference: str | None
    ) -> dict[str, Any]:
        """Note id for a spoken/typed title — exact casefold first, then a
        unique substring; ambiguity is answered with candidates, never a guess."""
        if not note_reference:
            return {"note_id": project.id}
        summaries = await _assistant_note_summaries(project)
        needle = note_reference.strip().casefold()
        exact = [
            item for item in summaries
            if str(item.get("title") or "").casefold() == needle
        ]
        matches = exact or [
            item for item in summaries
            if needle in str(item.get("title") or "").casefold()
        ]
        if len(matches) != 1:
            return {
                "error": "note did not resolve"
                if not matches
                else "more than one note matches",
                "candidates": [str(item.get("title") or "") for item in matches[:6]],
            }
        return {"note_id": str(matches[0]["note_id"])}

    async def _assistant_note_read(
        project_id: str, note_reference: str | None = None
    ) -> dict[str, Any]:
        project = projects.projects.get(project_id)
        if project is None:
            raise ValueError("unknown project")
        resolved = await _assistant_resolve_note(project, note_reference)
        if resolved.get("error"):
            return resolved
        return await read_note(
            project.root,
            notes_routes._storage_note_id(project, str(resolved["note_id"])),
            default_title=f"{project.name} notes",
            project=_registered_identity(project),
        )

    async def _assistant_note_write(
        project_id: str, note_reference: str | None, payload: dict[str, Any]
    ) -> dict[str, Any]:
        """One note write through the ordinary revisioned write path.

        The transform itself is `assistant.apply_note_write` (pure, tested); this
        closure supplies what only the daemon has — the note inventory, the
        current revision, and the change event other devices refresh on.
        """
        project = projects.projects.get(project_id)
        if project is None:
            raise ValueError("unknown project")
        resolved = await _assistant_resolve_note(project, note_reference)
        if resolved.get("error"):
            return resolved
        identity = _registered_identity(project)
        storage_id = notes_routes._storage_note_id(project, str(resolved["note_id"]))
        current = await read_note(
            project.root,
            storage_id,
            default_title=f"{project.name} notes",
            project=identity,
        )
        edited = apply_note_write(str(current.get("markdown") or ""), payload)
        result = await write_note(
            project.root,
            storage_id,
            edited,
            str(current.get("revision") or "missing"),
            default_title=f"{project.name} notes",
            project=identity,
        )
        await events.emit(
            "note_changed",
            source="assistant",
            scope="project",
            project_id=project.id,
            note_id=str(resolved["note_id"]),
            revision=result.get("revision"),
        )
        return result

    async def _assistant_history_search(*, query: str, limit: int) -> dict[str, Any]:
        """The assistant's archive search, bounded in a way an operator's is not.

        A human running this watches a spinner and can give up; a tool call has
        nobody to give up, and this one holds the single history executor thread
        while it runs, so an unbounded search takes every other history read down
        with it. Measured 2026-08-23 on a 2.79 GB database: minutes of pinned
        thread, `/api/sessions` timing out, and a chat stuck on
        "running search_history" with no way to stop it.
        """
        return await history.history_page(
            query=query,
            search_scope="all",
            limit=limit,
            budget_ms=ASSISTANT_HISTORY_SEARCH_BUDGET_MS,
        )

    async def _assistant_create_project(arguments: dict[str, Any]) -> dict[str, Any]:
        """The assistant's create_project execution: the ordinary registration path.

        The preflight already resolved the absolute root inside the configured
        new-project parent, so this only registers (creating the one missing
        folder) and emits the same events the HTTP create path does. The optional
        git init reuses the one-time repository initialization and keeps its
        contract: nothing is staged and no commit is made. A git failure never
        unwinds the registration - the same rule Project setup commands follow.
        """
        name = str(arguments.get("name") or "")
        root = str(arguments.get("root") or "")
        registration = await projects.register(name, root, create_missing=True)
        project = registration.project
        log.info(
            "assistant_project_created project_id=%s root=%s restored=%s git_requested=%s",
            project.id,
            project.root,
            registration.restored,
            bool(arguments.get("git")),
        )
        await events.emit(
            "project_restored" if registration.restored else "project_created",
            source="assistant",
            project_id=project.id,
            root=project.root,
        )
        result: dict[str, Any] = {
            "created": True,
            "project": project.name,
            "root": project.root,
            "restored": registration.restored,
            "note": "created without setup commands; the operator can run them from "
            "the project's Run menu",
        }
        if arguments.get("git"):
            operation_id = uuid4().hex
            try:
                await git_review.repository_identity(project.root)
            except git_review.GitReviewError as exc:
                if exc.code != "not_git_repository":
                    result["git"] = f"not initialized: {exc}"
                else:
                    log.info(
                        "repository_init_started operation_id=%s project_id=%s root=%s "
                        "trigger=assistant",
                        operation_id,
                        project.id,
                        project.root,
                    )
                    try:
                        init = await git_init.initialize_repository(
                            project.root, operation_id=operation_id
                        )
                    except git_init.RepositoryInitError as init_error:
                        log.warning(
                            "repository_init_failed operation_id=%s project_id=%s root=%s",
                            operation_id,
                            project.id,
                            project.root,
                        )
                        result["git"] = f"failed: {init_error}"
                    else:
                        await events.emit("git_changed", project_id=project.id)
                        result["git"] = (
                            f"initialized an empty repository on branch {init.branch}; "
                            "no commits were made"
                        )
            else:
                result["git"] = "the folder is already inside a git repository"
        return result

    def _assistant_action_project(project_id: str) -> ProjectRecord:
        project = projects.projects.get(project_id)
        if project is None:
            raise ValueError("unknown project")
        return project

    async def _assistant_action_catalog(project_id: str) -> dict[str, Any]:
        """One Project's declared actions and their per-file approval state."""
        project = _assistant_action_project(project_id)
        catalog = await asyncio.to_thread(project_actions.catalog, project.root)
        return catalog.snapshot()

    async def _assistant_action_preview(
        project_id: str, reference: str, inputs: dict[str, str]
    ) -> dict[str, Any]:
        """Resolve an action named in conversation to exactly what would run.

        The assistant's preflight calls this so a card never pends for something
        the executor will refuse, and so an unapproved action is answered with
        the file a human has to review rather than with a failure after the
        operator confirmed it.
        """
        project = _assistant_action_project(project_id)
        catalog = await asyncio.to_thread(project_actions.catalog, project.root)
        return await asyncio.to_thread(
            preview_action_run,
            catalog,
            reference,
            dict(inputs),
            project_label=project.name,
        )

    async def _assistant_run_action(
        project_id: str, action_id: str, inputs: dict[str, str]
    ) -> dict[str, Any]:
        """Start one approved action through the Run menu's own execution path."""
        project = _assistant_action_project(project_id)
        payload, _status = await project_action_routes._start_project_action(
            app, project, action_id, dict(inputs), origin="assistant"
        )
        return payload

    assistant = AssistantService(
        config,
        events,
        sessions,
        projects,
        assistant_store,
        automation_store,
        openrouter,
        prompt_queue=prompt_queue,
        spawn_op=lambda body: session_routes._spawn_from_body(app, body),
        interrupt_op=lambda session: terminal_routes._interrupt_session_pty(app, session),
        end_op=lambda session, reason: terminal_routes._end_session_gracefully(
            app, session, reason
        ),
        history_search=_assistant_history_search,
        note_read=_assistant_note_read,
        note_list=_assistant_note_list,
        note_write=_assistant_note_write,
        create_project_op=_assistant_create_project,
        action_catalog=_assistant_action_catalog,
        action_preview=_assistant_action_preview,
        action_run=_assistant_run_action,
    )
    publish(app, {keys.ASSISTANT: assistant})
    publish(app, {keys.ASSISTANT_STORE: assistant_store})

    # Scheduled runs. Machine-local definitions, the same spawn path the Run menu
    # uses, and the same prompt queue for anything staged behind the seed prompt.
    # Constructed here because it needs all three of those plus the per-Project
    # opt-in closure, and it must be able to answer "may this fire" at fire time
    # rather than trusting an answer cached when the schedule was written.
    schedule_store = ScheduleStore(config.database_path)
    schedules = ScheduleService(
        store=schedule_store,
        projects=projects,
        sessions=sessions,
        config=config,
        events=events,
        automation_gate=_enabled_automations,
        spawn_op=lambda body: session_routes._spawn_from_body(app, body),
        enqueue=prompt_queue.enqueue,
        notify=automation_store.notify,
        # Read-only, and only for a schedule whose action is `resume`: the conversation
        # is named by a history run id, and following one to where it has got to walks
        # rollovers in the index and `resume` edges in the lineage table.
        history=history,
        automation_store=automation_store,
    )
    publish(app, {keys.SCHEDULES: schedules})
    publish(app, {keys.SCHEDULE_STORE: schedule_store})

    def _session_project_root(session_id: str) -> tuple[Any, str] | None:
        session = sessions.sessions.get(session_id)
        if session is None:
            return None
        record = session.record
        root = record.project_root or record.spawn_project_root
        if not root and record.project_id:
            project = projects.projects.get(record.project_id)
            root = project.root if project else None
        return (record, root) if root else None

    async def consumer_context(session_id: str) -> ConsumerContext | None:
        resolved = _session_project_root(session_id)
        if resolved is None:
            return None
        record, root = resolved
        if not record.project_id:
            return None
        enabled = await _enabled_automations(root)
        return ConsumerContext(
            project_id=record.project_id,
            project_root=root,
            agent_run_id=record.agent_run_id or None,
            enabled=enabled,
        )

    async def tier0_context(session_id: str) -> Tier0Context | None:
        # The per-project enablement gate: Tier 0 only captures for a session
        # whose owning project opted it in. Resolved off-loop with a short TTL
        # cache so the event path never blocks on a config read. Returns the
        # session's ownership context so every fact is stamped with the run and
        # project it belongs to — per-run/per-project queries are the substrate's
        # whole purpose and cannot be recovered from session_id alone once a
        # session is resumed, promoted, or branched.
        resolved = _session_project_root(session_id)
        if resolved is None:
            return None
        record, root = resolved
        if "tier0" not in await _enabled_automations(root):
            return None
        return Tier0Context(
            agent_run_id=record.agent_run_id or None,
            project_id=record.project_id or None,
        )

    async def project_context(session_id: str) -> ProjectContext | None:
        resolved = _session_project_root(session_id)
        if resolved is None:
            return None
        record, root = resolved
        if not record.project_id:
            return None
        return ProjectContext(project_id=record.project_id, project_root=root)

    async def scan_context(session_id: str) -> ScanContext | None:
        resolved = _session_project_root(session_id)
        if resolved is None:
            return None
        record, root = resolved
        if not record.project_id or not record.agent_run_id:
            return None
        enabled = await _enabled_automations(root)
        if "scan_timeline" not in enabled:
            return None
        portable = await read_project_config(
            root,
            project=_registered_identity(projects.projects[record.project_id])
            if record.project_id in projects.projects
            else None,
        )
        values = portable["values"] if portable["status"] in {"ready", "read-only"} else {}
        return ScanContext(
            project_id=record.project_id,
            project_root=root,
            agent_run_id=record.agent_run_id,
            dead_end_memory_enabled="dead_end_memory" in enabled,
            auto_enable=bool(values.get("scan_timeline_auto_enable", False)),
            continuous_title_enabled="continuous_title" in enabled,
            phase_transitions_enabled="phase_transitions" in enabled,
        )

    project_contexts = ProjectContextService(resolve_session=project_context)
    # Phase 7.7: adaptive titling + phase-transition signals ride a freshly saved
    # scan record. It never enters the scan path's budget or latency; a fault in
    # it is contained by the scan service.
    behavioral_consumers = BehavioralConsumerService(
        store=automation_store,
        sessions=sessions,
        config=config,
        provider=openrouter,
        events=events,
    )
    publish(app, {keys.BEHAVIORAL_CONSUMERS: behavioral_consumers})
    scan_timeline = ScanTimelineService(
        store=automation_store,
        tier0=tier0,
        sessions=sessions,
        events=events,
        config=config,
        provider=openrouter,
        project_contexts=project_contexts,
        resolve_context=scan_context,
        history=history,
        on_record_saved=behavioral_consumers.on_scan_record,
    )
    tier0.start(events, resolve_context=tier0_context)
    # Phase 3.7: model-free detectors over the facts Tier 0 just captured. Same
    # gate, same cache; writes annotations and nothing else.
    consumers = DeterministicConsumerService(
        tier0,
        automation_store,
        sessions,
        events,
        resolve_context=consumer_context,
        code_graph=code_graph_store,
    )
    consumers.start()
    # Phase 6.5: the consumer of everything above. It routes findings into four
    # channels under a hard daily interrupt budget and writes no session.
    attention_narrator = AttentionNarrator(automation_store, config, openrouter)
    attention_ranking = AttentionRankingService(
        automation_store,
        sessions,
        events,
        config,
        resolve_context=consumer_context,
        narrator=attention_narrator,
    )
    # Each `restore()` below reads durable state its own loop is about to act
    # on, so none of them may be moved behind the `start()` that follows it: a
    # loop that ticks before its restore double-fires, re-strands, or re-runs a
    # step nothing recorded. They stay on the startup path deliberately, and are
    # now named so a restore that grows is visible rather than inferred.
    timeline.mark("restore-attention")
    await attention_ranking.restore()
    attention_ranking.start()
    timeline.mark("restore-scan-timeline")
    await scan_timeline.restore()
    scan_timeline.start()
    timeline.mark("start-loops")
    git_provenance_service.start()
    git_monitor.start()
    hooks.start()
    automation.start()
    usage.start()
    # Left on the startup path on purpose. Provider *system* auth is
    # authoritative (architecture.md invariant 10) and this is what derives the
    # saved selection from it; running it behind the listener would let the
    # daemon answer "which account is active" from pre-restart registry memory
    # for as long as it took, which is the one answer this reconcile exists to
    # prevent anyone giving.
    timeline.mark("provider-accounts-reconcile")
    await provider_accounts.reconcile_startup()
    provider_accounts.start()
    # Deferred: a full psutil sweep of every process on the machine, measured at
    # 20.7s cold and 6.0s warm across 482 processes, and it was the second silent
    # stretch of a 226.6s start. Nothing that serves a request depends on it -
    # it populates process ownership for the Processes surfaces, which the
    # inspector's own poll refreshes on the same cadence forever afterwards, so
    # the only consequence of deferring is that the first reading arrives a few
    # seconds later than the rest of the daemon.
    #
    # `start()` stays *inside* the task rather than beside it: the periodic
    # reconcile must not run against a half-restored ownership map, and one task
    # doing restore-then-start preserves that ordering exactly as the inline
    # sequence did.
    async def _restore_process_ownership() -> None:
        await process_inspector.restore()
        process_inspector.start()

    process_restore_task = asyncio.create_task(
        _restore_process_ownership(), name="process-ownership-restore"
    )
    process_restore_task.add_done_callback(log_task_failure)
    deferred_tasks.append(process_restore_task)
    # Deferred for the same reason: a removal whose purge was cancelled by the last
    # shutdown left a buried checkout on disk, and nobody would ever notice it. Two
    # stats per Project, then whatever deletion the leftovers need.
    graveyard_sweep = asyncio.create_task(
        asyncio.to_thread(
            sweep_graveyards, [project.root for project in projects.projects.values()]
        ),
        name="worktree-graveyard-sweep",
    )
    graveyard_sweep.add_done_callback(log_task_failure)
    deferred_tasks.append(graveyard_sweep)
    ghost_windows.start()
    fleet.start()
    voice.start()
    # After supervisor adoption: the startup reconcile strands queue items
    # whose target session or agent run did not survive the restart.
    timeline.mark("restore-queues")
    await prompt_queue.start()
    # Repairs a cron schedule's next fire against the current timezone database
    # and arms anything an older build left without one, then sweeps. A window
    # that passed while this daemon was down stays in the past on purpose - the
    # sweep, not the restore, decides whether it is replayed or recorded missed.
    await schedules.restore()
    schedules.start()
    # Phase 14: return any step orphaned by a restart to the queue before the
    # sweep begins, so a half-run land is re-checked from scratch rather than
    # resumed from a position nothing recorded.
    await land_queue_service.restore()
    land_queue_service.start()
    # Watches are in-memory and therefore start empty: there is nothing to
    # restore, because the previous daemon flushed each open watch as a notice on
    # its way out rather than leaving a promise nothing could keep.
    session_watch.start()
    # The auto-delivery controller starts regardless of the master switch: it
    # also sweeps message expiry, which is a promise the user made about any
    # delivery path, and it re-checks its own enablement every tick.
    auto_delivery.start()
    readiness_watch.start()
    project_watcher.start()
    # Every long-lived loop runs under the background-task supervisor: restarted
    # with capped backoff, faults counted, health surfaced at
    # /api/diagnostics/background. An unsupervised loop that dies is invisible.
    # Started first among the supervised loops, so its own baseline is measured from
    # the same moment everything that can stall it begins running.
    timeline.mark("background-loops")
    loop_lag = LoopLagMonitor()
    publish(app, {keys.LOOP_LAG: loop_lag})
    background.start(LOOP_LAG_LOOP, lambda: _loop_lag_loop(loop_lag))
    background.start(CONFIG_WATCH_LOOP, lambda: watch_config(app))
    background.start(MEDIA_CLEANUP_LOOP, lambda: _media_cleanup_loop(config.data_dir, projects))
    background.start(
        RETENTION_LOOP,
        lambda: _retention_loop(
            automation_store, tier0, prompt_queue_store, schedule_store, config
        ),
    )
    background.start(STATE_WATCHDOG_LOOP, sessions.state_watchdog_loop)
    status_timeline.start()
    if session_recovery is not None:
        session_recovery.start()
    push_sender = PushSender(
        push_store,
        settings_store,
        events,
        presence=device_presence,
        decision_store=telemetry,
    )
    background.start(PUSH_SENDER_LOOP, push_sender.run)
    # The one outbound request swe-mux makes on its own behalf, and the only
    # loop here whose first iteration is deliberately delayed: the interval is
    # persisted, so a restart cannot turn into a request loop, and the delay
    # keeps a start from being accompanied by a network call on a machine whose
    # link comes up after the daemon does. Off entirely under
    # `update_check_enabled`, which it re-reads at every check.
    update_checker = UpdateChecker(config)
    update_checker.start()
    # No loop and no task: an install happens only when someone presses the
    # button, so this costs nothing until then. It is constructed here rather
    # than lazily in the route so that its durable state is loaded - and a
    # download abandoned by a restart is reported as abandoned - by the first
    # read rather than by the first install.
    update_installer = UpdateInstaller(config)
    history_search_maintenance_task = asyncio.create_task(
        history.maintain_message_search_indexes(), name="history-message-search-maintenance"
    )
    history_search_maintenance_task.add_done_callback(log_task_failure)
    reconcile_task: asyncio.Task[int] | None = None
    if config.reconcile_external_history:
        # Scope the startup scan to the harnesses the user has enabled. Detection
        # runs off the loop; a disabled harness's past sessions are simply not
        # indexed this start and are picked up when it is enabled and a scan runs.
        reconcile_backends = await asyncio.to_thread(
            enabled_backends, dict(config.harness_enabled), dict(config.harness_exe)
        )
        reconcile_task = asyncio.create_task(
            reconcile_external_history(history, backends=reconcile_backends),
            name="history-reconcile",
        )
        # A one-shot task that dies is silent by default; the scan's failure mode
        # is "external history is quietly stale", which nothing else reports.
        reconcile_task.add_done_callback(log_task_failure)
    publish(
        app,
        {
            keys.HISTORY: history,
            keys.EVENTS: events,
            keys.PROJECTS: projects,
            keys.HISTORY_BACKFILLS: history_backfills,
            keys.HISTORY_SCAN: history_scan,
            keys.SESSIONS: sessions,
            keys.TIER0: tier0,
            keys.MCP: McpService(
                sessions,
                history,
                agent_messaging,
                automation_store,
                agent_context,
                projects,
                project_actions,
                # A closure over the same app, so an agent-started action goes through the
                # identical trust check, substitution, spawn path, and timeout arming as
                # the Run menu. A second implementation would be a second authority path.
                lambda project, action_id, inputs: project_action_routes._start_project_action(
                    app, project, action_id, inputs, origin="agent"
                ),
                # Phase 7.5 memory reads: the deterministic fact store and the same
                # per-project enablement closure Tier 0 capture and the detectors gate
                # on, so an MCP read can never run under a stale opt-in answer one of
                # them already refreshed.
                tier0=tier0,
                automation_gate=_enabled_automations,
                session_control=session_control,
                # Phase 7.9: the structural graph the blast-radius/navigation/context/
                # test-gap reads answer from, gated on the same `code_graph` opt-in.
                code_graph=code_graph_store,
                # Phase 14: `request_land` is a caller over this service, which owns
                # every bound including the grant the tool defaults to drafting under.
                land_queue=land_queue_service,
                # Phase 7.11: the scan service, read for its enablement/liveness block
                # only. The records themselves come from the store, so the drawer and
                # the `scan_timeline` tool answer "is this timeline stopped" from one
                # implementation rather than two that can disagree.
                scan_timeline_service=scan_timeline,
                # The settle-watch service: `watch_session` arms through it and
                # nothing else reaches it, because a watch is only ever asked for by
                # the session that will receive the notice.
                session_watch=session_watch,
                # The configurator family's backing service. Reachable only from a
                # session the daemon itself launched as a configurator; every other
                # caller is never shown the tools and is refused if it guesses a name.
                configurator=configurator_routes.build_configurator_service(app),
            ),
            keys.REAPER: reaper,
            keys.SUPERVISOR: supervisor_client,
            keys.GIT_MONITOR: git_monitor,
            keys.GIT_PROVENANCE: git_provenance_service,
            keys.HOOKS: hooks,
            keys.AUTOMATION: automation,
            keys.AUTOMATION_STORE: automation_store,
            keys.SECRET_STORE: secret_store,
            keys.OPENROUTER: openrouter,
            keys.USAGE: usage,
            keys.TELEMETRY: telemetry,
            keys.STATUS_TIMELINE: status_timeline,
            keys.SESSION_RECOVERY: session_recovery,
            keys.STORAGE_USAGE: StorageUsage(
                config.data_dir,
                lambda: [
                    ProjectFootprintTarget(id=project.id, label=project.name, root=project.root)
                    for project in projects.ordered_projects()
                ],
            ),
            keys.DETERMINISTIC_CONSUMERS: consumers,
            keys.ATTENTION_RANKING: attention_ranking,
            keys.ATTENTION_NARRATOR: attention_narrator,
            keys.PROJECT_CONTEXTS: project_contexts,
            keys.SCAN_TIMELINE: scan_timeline,
            keys.PROVIDER_ACCOUNTS: provider_accounts,
            keys.PROCESS_INSPECTOR: process_inspector,
            keys.GHOST_WINDOWS: ghost_windows,
            keys.PREVIEWS: previews,
            keys.FLEET: fleet,
            keys.VOICE: voice,
            keys.VOICE_STORE: voice_store,
            keys.PROMPT_LIBRARY: prompt_library,
            keys.PROMPT_QUEUE: prompt_queue,
            keys.AUTO_DELIVERY: auto_delivery,
            keys.READINESS_WATCH: readiness_watch,
            keys.SCHEDULES: schedules,
            keys.SCHEDULE_STORE: schedule_store,
            keys.AGENT_MESSAGING: agent_messaging,
            keys.AGENT_CONTEXT: agent_context,
            keys.SETTINGS_STORE: settings_store,
            keys.CLIPBOARD: clipboard,
            keys.PUSH_STORE: push_store,
            keys.DEVICE_PRESENCE: device_presence,
            keys.PROJECT_ACTIONS: project_actions,
            keys.PROJECT_WATCHER: project_watcher,
            keys.AUTOMATION_TASKS: set(),
            # One entry per running Project Action step that declared `timeout_seconds`.
            # Kept beside the automation set and cancelled the same way, so a daemon
            # shutdown does not leave a timer holding a reference to a dead session.
            keys.ACTION_TIMEOUT_TASKS: set(),
            # One entry per worktree-removal purge in flight. Cancelled at shutdown like
            # the rest: the graveyard is durable, so a cancelled purge costs disk until
            # the next removal or the sweep at the next daemon start.
            keys.GRAVEYARD_TASKS: set(),
            # Cancelled in teardown alongside every other one-shot task; published
            # rather than kept as a local because teardown no longer shares this
            # function's scope.
            keys.RECONCILE_TASK: reconcile_task,
            keys.HISTORY_SEARCH_MAINTENANCE_TASK: history_search_maintenance_task,
            keys.PROMPT_QUEUE_STORE: prompt_queue_store,
            keys.UPDATE_CHECK: update_checker,
            keys.UPDATE_INSTALL: update_installer,
        },
    )
    # The startup duration nobody could see. The listeners are already bound by
    # the time this runs, so it is no longer downtime - but it is still the
    # number the desktop shell's health wait and the redeploy budget are set
    # against, and every phase that makes it up is on the lines above.
    startup_seconds = timeline.finish(f"{len(sessions.sessions)} live session(s)")
    log.log(
        logging.WARNING if startup_seconds > SLOW_STARTUP_SECONDS else logging.INFO,
        "daemon runtime ready in %.1fs (%d live session(s)); serving every route",
        startup_seconds,
        len(sessions.sessions),
    )


async def _teardown_runtime(app: web.Application) -> None:  # noqa: PLR0912, PLR0915
    """Stop and close whatever the build managed to construct.

    Every handle is read back out of `app` rather than closed over, and every
    one is optional. That is not defensiveness for its own sake: the build now
    runs as a task, so a shutdown (or a build failure) can arrive with the
    runtime half-constructed, and a teardown that assumed a complete runtime
    would raise on the first missing handle and leak everything after it.
    """
    # Everything below this line is the last chance any of these rows have to be
    # written, and the listener is already closed - so a successor may already
    # be holding `mux.db`. `begin_shutdown_drain` lets a colliding write wait for
    # the file instead of being dropped, and makes a write that is dropped
    # anyway say so (`sqlite_store`).
    begin_shutdown_drain()
    config: Config = app[keys.CONFIG]
    supervisor_client: SupervisorClient | None = app.get(keys.SUPERVISOR)
    network_usage: NetworkUsage | None = app.get(keys.NETWORK_USAGE)
    if network_usage is not None:
        network_snapshot = network_usage.snapshot()
        network_totals = network_snapshot["totals"]
        log.info(
            "network usage at daemon shutdown after %.1fs: http_rx=%d http_tx=%d ws_rx=%d ws_tx=%d",
            network_snapshot["uptime_seconds"],
            network_totals["http"]["request_bytes"],
            network_totals["http"]["response_bytes"],
            network_totals["websocket"]["received_bytes"],
            network_totals["websocket"]["sent_bytes"],
        )
    one_shot_tasks = [
        task
        for task in (
            app.get(keys.RECONCILE_TASK),
            app.get(keys.HISTORY_SEARCH_MAINTENANCE_TASK),
            *(app.get(keys.STARTUP_DEFERRED_TASKS) or ()),
        )
        if task is not None
    ]
    for task in one_shot_tasks:
        if not task.done():
            task.cancel()
    if one_shot_tasks:
        await asyncio.gather(*one_shot_tasks, return_exceptions=True)
    for holder in (keys.AUTOMATION_TASKS, keys.ACTION_TIMEOUT_TASKS, keys.GRAVEYARD_TASKS):
        held: set[asyncio.Task[Any]] = app.get(holder) or set()
        pending = tuple(held)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
    for loop_name in (
        CONFIG_WATCH_LOOP,
        MEDIA_CLEANUP_LOOP,
        RETENTION_LOOP,
        STATE_WATCHDOG_LOOP,
        PUSH_SENDER_LOOP,
        UPDATE_CHECK_LOOP,
        # Early in nothing and late in nothing, but it must be here: its worker
        # holds a second connection to `mux.db`, and the stores below are closed
        # on the assumption that nothing else is reading the file.
        DATABASE_INTEGRITY_LOOP,
    ):
        await background.stop(loop_name)
    # An in-flight download is cancelled and *awaited*, not abandoned: it holds a
    # socket and an open `.part` file, and a task still running when the loop
    # closes is the failure mode that reddens whichever test happens to be
    # running when the collector gets to it.
    await _stop_handle(app, keys.UPDATE_INSTALL)
    # Stopped in the order they were started, each skipped when the build never
    # got far enough to construct it. `history_backfills`/`history_scan` lead
    # because they own cancellable scans over the stores closed further down.
    #
    # Declared once for every loop below: each tuple holds `AppKey`s of a
    # different service type, and `AppKey` is invariant, so without this mypy
    # narrows the loop variable to the first tuple's union and rejects the rest.
    key: web.AppKey[Any]
    for key in (
        keys.HISTORY_BACKFILLS,
        keys.HISTORY_SCAN,
        keys.HOOKS,
        keys.AUTOMATION,
        keys.SCAN_TIMELINE,
        keys.DETERMINISTIC_CONSUMERS,
    ):
        await _stop_handle(app, key)
    # The fan-out estimate is built from weeks of interaction samples; persisting
    # them is what keeps a daemon restart from resetting the estimate to unknown.
    attention_ranking = app.get(keys.ATTENTION_RANKING)
    if attention_ranking is not None:
        try:
            await attention_ranking.persist_telemetry()
        except Exception:  # noqa: BLE001 - one store must not strand the rest
            log.exception("could not persist attention telemetry at shutdown")
    for key in (
        keys.ATTENTION_RANKING,
        keys.AUTO_DELIVERY,
        keys.READINESS_WATCH,
        keys.SCHEDULES,
        keys.LAND_QUEUE,
        # Before `prompt_queue`, and that position is load-bearing rather than
        # alphabetical: stopping the watch service flushes every open watch as a
        # durable notice, which is what keeps a routine daemon restart from
        # silently un-arming an orchestrator's watches.
        keys.SESSION_WATCH,
        keys.PROMPT_QUEUE,
        keys.ASSISTANT,
        keys.VOICE,
        keys.PROJECT_WATCHER,
        keys.USAGE,
        # Closes the `aiohttp.ClientSession` the provider-accounts reconcile opens.
        keys.PROVIDER_ACCOUNTS,
        keys.FLEET,
        keys.PROCESS_INSPECTOR,
        keys.GHOST_WINDOWS,
        keys.GIT_MONITOR,
        keys.GIT_PROVENANCE,
    ):
        await _stop_handle(app, key)
    # Shutdown intent (SESSION_PRESERVING_RELOAD §5.3): "quit" reaps everything
    # (today's behavior, and always the case without a supervisor); "detach"
    # leaves supervisor-owned sessions running so the next daemon reattaches.
    # The intent comes from outside the daemon (desktop shutdown endpoint);
    # with a supervisor attached, an unqualified exit (Ctrl-C, crash-adjacent
    # teardown) defaults to detach — the tmux model.
    intent = app[keys.SHUTDOWN_STATE]["intent"] or ("detach" if supervisor_client else "quit")
    sessions: SessionManager | None = app.get(keys.SESSIONS)
    if sessions is not None:
        await sessions.shutdown(intent=intent)
    if supervisor_client is not None:
        if intent == "quit":
            await supervisor_client.reap_all_and_exit()
        else:
            log.info(
                "detaching from PTY supervisor; live sessions keep running "
                "(muxd --shutdown stops everything)"
            )
        await supervisor_client.close()
    # After sessions.shutdown(): the terminal ledger entries it appends are the
    # final drain's whole point.
    #
    # `session_recovery` is also after shutdown, and for the mirror-image reason:
    # a `quit` closes every session's row on the way out, and a `detach` leaves
    # the surviving sessions' rows open on purpose — they are still running, and
    # the next daemon reaches them through the supervisor rather than through here.
    for key in (
        keys.STATUS_TIMELINE,
        keys.SESSION_RECOVERY,
        keys.TELEMETRY,
        keys.TIER0,
        keys.CLIPBOARD,
    ):
        await _stop_handle(app, key)
    for key in (
        keys.HISTORY,
        keys.AUTOMATION_STORE,
        keys.PROMPT_QUEUE_STORE,
        keys.SCHEDULE_STORE,
        keys.LAND_STORE,
        keys.VOICE_STORE,
        keys.ASSISTANT_STORE,
        keys.TELEMETRY,
        keys.STATUS_TIMELINE,
        keys.SESSION_RECOVERY,
        keys.TIER0,
        keys.CLIPBOARD,
        keys.REAPER,
    ):
        _close_handle(app, key)
    await background.stop(LIFECYCLE_HEARTBEAT_LOOP)
    # Last so an exception anywhere above still reads as an unclean exit.
    await asyncio.to_thread(daemon_clean_exit, config.data_dir, intent)


async def _stop_handle(app: web.Application, key: web.AppKey[Any]) -> None:
    """`await app[key].stop()`, tolerating both absence and failure.

    One service raising on the way down used to abandon every service after it,
    which is how a shutdown leaves a WAL file open and the next start finds work
    to recover that never needed doing.

    **The key is looked up here rather than passed in resolved, and it is typed as
    an `AppKey` rather than a name.** `publish` writes every handle under the
    `AppKey` objects in `app_keys`, and an `AppKey` is hashed by identity - so
    `app.get("provider_accounts")` is not the same lookup as
    `app.get(keys.PROVIDER_ACCOUNTS)`, it is a miss. This teardown was written
    against string names and kept them through the move to `AppKey`, which turned
    every one of its teardown lines into a silent no-op: for a week no store was closed and no
    service was stopped at shutdown, and the only visible trace was one unclosed
    `aiohttp.ClientSession` printed by a finalizer. Taking the app and the key
    makes that mistake a mypy error instead of a shutdown that quietly does
    nothing, and `test_shutdown_teardown.py` asserts the handles resolve.
    """
    handle = app.get(key)
    if handle is None:
        return
    try:
        await handle.stop()
    except Exception:  # noqa: BLE001 - shutdown continues past one bad citizen
        log.exception("could not stop %s at shutdown", key)


def _close_handle(app: web.Application, key: web.AppKey[Any]) -> None:
    handle = app.get(key)
    if handle is None:
        return
    try:
        handle.close()
    except Exception:  # noqa: BLE001 - same rule as `_stop_handle`
        log.exception("could not close %s at shutdown", key)


async def _loop_lag_loop(monitor: LoopLagMonitor) -> None:
    """Record how late this event loop is running its own scheduled work.

    The sleep stays outside the supervisor's `iteration()` guard on purpose: timing it
    would measure this probe's sleep rather than anything blocking the loop. Only the
    recording is guarded, which is what makes a dead lag monitor visible in the same
    place as every other stalled loop.
    """
    # unsupervised-loop-ok: supervised by `background.start(LOOP_LAG_LOOP, ...)`.
    while True:
        lag = await monitor.sample()
        with background.iteration(LOOP_LAG_LOOP):
            monitor.observe(lag)


async def _media_cleanup_loop(data_dir: Path, projects: ProjectManager) -> None:
    while True:
        with background.iteration(MEDIA_CLEANUP_LOOP):
            now = time.time()
            await asyncio.to_thread(cleanup_expired_session_media, data_dir, now)
            roots = [Path(project.root) for project in projects.projects.values()]
            roots.append(data_dir / "preview-shots")
            await asyncio.to_thread(cleanup_expired_preview_shots, roots, now)
        await asyncio.sleep(60 * 60)


async def _retention_loop(
    automation_store: AutomationStore,
    tier0: Tier0Store,
    prompt_queue_store: PromptQueueStore,
    schedule_store: ScheduleStore,
    config: Config,
) -> None:
    """The only retention pass for these stores; nothing prunes at startup.

    Session-preserving reload makes weeks-long uptimes the norm, so a
    startup-only prune would mean "bounded by age" holds only across restarts.
    Retention ran at startup *as well* until it was measured there: a prune of
    the retention tables is a scan whose cost tracks database size and page
    cache, and on the startup path it delays the listener bind by exactly that
    much. Housekeeping must never gate the port.
    """
    await asyncio.sleep(60)
    while True:
        with background.iteration(RETENTION_LOOP):
            await automation_store.prune(config.automation_retention_days)
            await tier0.prune()
            await prompt_queue_store.prune(config.prompt_queue_retention_days)
            await schedule_store.prune(config.scheduled_run_retention_days)
        await asyncio.sleep(60 * 60)
