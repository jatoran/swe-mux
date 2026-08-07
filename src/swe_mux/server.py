"""HTTP/WebSocket control plane for the swe-mux daemon.

The daemon binds a single port (default 8765) and owns one data dir (~/.mux), so
exactly one instance may run per machine. Never start a second daemon from a
worktree: worktrees isolate the working tree, not the runtime, and the two
instances will fight over the same mux.db.
"""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import logging
import mimetypes
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
import tomllib
from collections import deque
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import replace
from pathlib import Path
from typing import Any, cast, get_args
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

from aiohttp import ClientError, ClientSession, ClientTimeout, WSMsgType, web
from aiohttp.multipart import BodyPartReader

from . import git_review
from .adapters import BackendAdapter, ShellAdapter, build_agent_adapter
from .agent_context import AgentContextConflict, AgentContextService
from .agent_environment import discover_agent_environment
from .agent_messaging import AgentMessagingService
from .agent_skills import discover_skills
from .auto_delivery import AutoDeliveryController
from .automation import (
    MAX_SLICE_BYTES,
    OBSERVER_SCHEMAS,
    AutomationEngine,
    RuleValidationError,
    TranscriptSliceService,
    normalize_event,
    parse_rules,
    serialize_rules,
    validate_observer_result,
)
from .automation_registry import REGISTRY as AUTOMATION_REGISTRY
from .automation_registry import resolve_config as resolve_automation_config
from .automation_store import AutomationStore
from .background_tasks import background
from .bundle_locks import bundle_lock_holders, describe_holders, frozen_bundle_root
from .clipboard_store import ClipboardStore
from .config import Config, load_config, update_config
from .deterministic_consumers import ConsumerContext, DeterministicConsumerService
from .device_presence import DevicePresenceStore, parse_device_report
from .event_bus import EventBus
from .file_manager import open_in_file_manager
from .fleet_intelligence import FleetIntelligence
from .git_monitor import GitMonitor, _git
from .git_projects import ProjectIdentity, resolve_project
from .harness import (
    AGENT_BACKENDS,
    HARNESSES,
    delivers_prompts_through_pty,
    descriptor,
    has_observable_transcript,
    is_agent_harness,
    public_harness_registry,
)
from .history import HistoryIndex
from .history_backfill import HistoryBackfillManager
from .keybindings import (
    DEFAULT_KEYBINDINGS,
    KEYBINDING_COMMANDS,
    KEYBINDINGS_FILE_VERSION,
    V2_DEFAULT_KEYBINDINGS,
    keybinding_policy,
    normalize_binding,
)
from .launchers import create_agent_shims, resolve_codex_pty_command, resolve_command
from .layouts import attach_leaf, attach_terminal, stack_leaf
from .lifecycle import HEARTBEAT_INTERVAL_SECONDS, daemon_clean_exit, daemon_started, heartbeat
from .logsetup import current_log_level, set_log_level
from .loop_lag import LoopLagMonitor
from .mcp import McpAuthError, McpService
from .meta_hooks import MetaHookEngine, parse_hook_rules
from .models import MuxEvent, StandingActivityKind
from .observation import (
    apply_hook_observation,
    cancel_pending_approval,
    conversation_rollover_decision,
    foreign_conversation_hook_id,
    hook_event_scope,
)
from .openrouter import OpenRouterClient, OpenRouterError
from .operational_telemetry import OperationalTelemetryStore
from .preview_capture import (
    INSTALL_HINT as PREVIEW_CAPTURE_INSTALL_HINT,
)
from .preview_capture import (
    VIEWPORT_WIDTHS,
    capture_available,
    capture_loopback,
)
from .processes import PreviewRegistry, ProcessInspector
from .profiles import profile_payload, resolve_profile
from .project_actions import ProjectActionService, action_spawn_body
from .project_card import ProjectCardContext, ProjectCardService
from .project_files import (
    GLOBAL_SCRATCHPAD_ID,
    ObservationsUnreadableError,
    ProjectFileRevisionConflict,
    ProjectImageUnavailable,
    ProjectResourceExists,
    append_observation,
    create_note,
    create_project_resource,
    delete_note,
    effective_project_ignores,
    ignored_project_path,
    list_project_directories,
    list_project_directory,
    project_automations,
    project_note_summaries,
    project_path,
    read_global_note,
    read_note,
    read_observations,
    read_project_config,
    read_project_file,
    read_project_image_content,
    search_project_files,
    update_observation_request,
    write_global_note,
    write_note,
    write_observations,
    write_project_config,
    write_project_file,
)
from .project_files import (
    revision as file_revision,
)
from .project_init import init_script_step, select_init_scripts
from .project_watcher import ProjectFileWatcher
from .projects import ProjectManager
from .prompt_library import PromptLibrary, PromptScope
from .prompt_queue import (
    PromptQueueService,
    PromptQueueStore,
    QueueError,
    stage_seed_argv,
)
from .provider_accounts import (
    ProviderAccountConflict,
    ProviderAccountError,
    ProviderAccountManager,
)
from .push import PUSH_SENDER_LOOP, PushSender, PushStore
from .reconcile import reconcile_external_history
from .scrollback import SCREEN_TAIL_BYTES
from .secret_store import PlatformSecretStore, SecretStoreError
from .session import (
    STATE_WATCHDOG_LOOP,
    Session,
    SessionManager,
    clear_all_standing_activity,
    clear_standing_activity,
    pty_tail_explain,
    session_is_unwitnessed,
)
from .session_attachments import (
    MAX_ATTACHMENT_BYTES,
    MAX_IMAGE_BYTES,
    attachment_workspace_root,
    store_session_attachment,
)
from .settings_store import SettingsStore
from .spawn_contract import (
    SpawnRequest,
    resolve_contained_cwd,
    resolve_listed_cwd,
    scrub_claude_session_markers,
)
from .status_timeline import StatusTimelineStore
from .subprocess_flags import background_creation_flags, popen_outside_job
from .supervisor_client import SupervisorClient
from .tailscale import (
    enable_mobile_voice_serve,
    is_tailscale_ip,
    tailscale_status,
)
from .terminal_arbitration import ClaimReason, ClaimRequest, evaluate_claim
from .tier0_store import Tier0Context, Tier0Store
from .transcript_view import (
    CONVERSATION_DEFAULT_LIMIT,
    CONVERSATION_MAX_LIMIT,
    conversation_view_cached,
    parse_transcript_with_watermark,
)
from .usage import UsageManager
from .voice import VoiceError, VoiceService, VoiceStore, clip_snapshot, last_reply_text
from .win_jobobj import ReaperJob

log = logging.getLogger(__name__)
Handler = Callable[[web.Request], Awaitable[web.StreamResponse]]
PREVIEW_HTTP_CONCURRENCY = 32
PREVIEW_WS_CONCURRENCY = 16
PREVIEW_REQUEST_BYTES = 10 * 1024 * 1024
PREVIEW_RESPONSE_BYTES = 20 * 1024 * 1024
PREVIEW_WS_MESSAGE_BYTES = 4 * 1024 * 1024
PREVIEW_WS_IDLE_SECONDS = 30 * 60
PREVIEW_WS_LIFETIME_SECONDS = 12 * 60 * 60
SESSION_MEDIA_TTL_SECONDS = 24 * 60 * 60
# Preview screenshots live inside the user's repository, so they get a longer
# window than pasted media (an agent may read one days later) but still expire.
PREVIEW_SHOT_TTL_SECONDS = 7 * 24 * 60 * 60
PTY_ATTACH_READY_TIMEOUT_SECONDS = 0.25
# How long a connection's repeated passive claims go unanswered after a refusal. The
# reply is what an older client re-claims on, so answering every one is what turns a
# refusal into a loop.
REFUSED_CLAIM_COOLDOWN_SECONDS = 1.0
HOOK_RATE_WINDOW_SECONDS = 10.0
HOOK_RATE_LIMIT = 500
# Sweep rate-limit windows for sessions that no longer exist once the map grows
# past a size no live fleet reaches.
HOOK_WINDOW_SWEEP_AT = 256
# MCP tool-call budget per session. Generous for a deliberate agent, tight
# enough that a retry loop cannot pull the whole archive through the endpoint.
MCP_RATE_WINDOW_SECONDS = 60.0
MCP_RATE_LIMIT = 120
MCP_BODY_BYTES = 256 * 1024
CONFIG_WATCH_LOOP = "config-watch"
LOOP_LAG_LOOP = "loop-lag"
LIFECYCLE_HEARTBEAT_LOOP = "lifecycle-heartbeat"
MEDIA_CLEANUP_LOOP = "media-cleanup"
RETENTION_LOOP = "store-retention"
# Startup past this is logged as a warning. A daemon reattaching a large fleet
# legitimately takes a few seconds; anything near the desktop shell's health
# budget is the shape of an incident, and it left no trace of its own until a
# 36s start expired the tray's wait and looked like a daemon that never started.
SLOW_STARTUP_SECONDS = 20.0
# Retained events replayed to a reconnecting /events client when it supplies no
# cursor: the NEWEST N, never the oldest — catch-up that replays ancient history
# delivers exactly the events the client already has and none that it missed.
EVENTS_CATCHUP_LIMIT = 2000
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


def hook_event_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Remove hook-owned envelope keys that collide with EventBus metadata.

    ``source`` is the collision that mattered: the CLI uses it for *why the
    session started* (`startup` / `resume` / `clear` / `compact`) while the
    EventBus uses it for *which channel observed this*. Dropping it lost the one
    field that explains a conversation rollover, so it is preserved under
    ``start_source`` instead.
    """
    kept = {
        key: value
        for key, value in payload.items()
        if key not in {"session_id", "source", "event_type", "scope"}
    }
    start_source = payload.get("source")
    if isinstance(start_source, str) and start_source:
        kept["start_source"] = start_source
    return kept


def _is_codex_default_color_response(backend: str, data: str) -> bool:
    """Reject late OSC 10/11 replies before Codex mistakes them for prompt input."""
    return backend == "codex" and _OSC_DEFAULT_COLOR_RESPONSE.fullmatch(data) is not None


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


def json_response(data: Any, status: int = 200) -> web.Response:
    return web.json_response(data, status=status)


def _log_task_failure(task: asyncio.Task[Any]) -> None:
    """Surface a one-shot background task's death instead of swallowing it."""
    if task.cancelled():
        return
    if (error := task.exception()) is not None:
        log.error("background task %s failed", task.get_name(), exc_info=error)


@web.middleware
async def error_middleware(request: web.Request, handler: Handler) -> web.StreamResponse:
    try:
        return await handler(request)
    except web.HTTPException:
        raise
    except KeyError as exc:
        return json_response({"error": f"not found: {exc.args[0]}"}, 404)
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
    except ProjectFileRevisionConflict as exc:
        return json_response({"error": str(exc), "code": "revision_conflict"}, 409)
    except ProjectImageUnavailable as exc:
        return json_response({"error": str(exc), "code": "image_unavailable"}, 415)
    except ProjectResourceExists as exc:
        return json_response({"error": str(exc), "code": "resource_exists"}, 409)
    except (ValueError, TypeError, ProviderAccountError) as exc:
        return json_response({"error": str(exc)}, 400)
    except Exception:
        log.exception("unhandled request error")
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


def is_loopback_peer(value: str) -> bool:
    peer = value.split("%", 1)[0]
    try:
        return ipaddress.ip_address(peer).is_loopback
    except ValueError:
        return False


def _apply_security_headers(response: web.StreamResponse, request: web.Request) -> None:
    """Stamp response security headers.

    Shared by the security middleware and the preview passthrough, which streams
    its own StreamResponse and so must set these before it calls prepare() (the
    middleware's post-handler stamping would otherwise be too late).
    """
    if request.path.startswith("/preview/"):
        csp = (
            "default-src * data: blob: 'unsafe-inline' 'unsafe-eval'; "
            "connect-src * data: blob:; frame-ancestors 'self'"
        )
    else:
        csp = (
            # 'wasm-unsafe-eval' permits the note editor's local WebAssembly
            # compilation without allowing general eval or any network access.
            "default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; "
            "connect-src 'self' ws: wss:; "
            "style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
            "frame-src 'self'; frame-ancestors 'none'; base-uri 'self'"
        )
    response.headers.setdefault("Content-Security-Policy", csp)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    # microphone=(self) keeps third-party frames blocked while allowing the app's
    # own dictation (STT) feature to request the microphone on secure contexts.
    response.headers.setdefault(
        "Permissions-Policy", "camera=(), microphone=(self), geolocation=()"
    )
    if not request.path.startswith("/preview/"):
        response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        response.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")


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
    _apply_security_headers(response, request)
    return response


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
    app = web.Application(
        middlewares=[error_middleware, security_middleware],
        client_max_size=MAX_ATTACHMENT_BYTES + 1024 * 1024,
    )
    app["config"] = config
    # Every client snapshot carries this process-generation identity alongside
    # the session-local revision. Session revisions restart from zero when a
    # daemon adopts supervisor-owned PTYs, so revision alone cannot distinguish
    # a stale pre-restart response from the new daemon's current state.
    app["daemon_generation"] = uuid4().hex
    app["frontend_dir"] = frontend_dir or Path(__file__).parent / "static"
    app["preview_http_semaphore"] = asyncio.Semaphore(PREVIEW_HTTP_CONCURRENCY)
    app["preview_ws_semaphore"] = asyncio.Semaphore(PREVIEW_WS_CONCURRENCY)
    app["hook_ingress_windows"] = {}
    app["mcp_rate_windows"] = {}
    app["attachment_locks"] = {}
    # Mutable holder because aiohttp freezes app keys once started; carries the
    # externally-signaled shutdown intent (quit vs restart/detach) to cleanup.
    app["shutdown_state"] = {"intent": None}
    if desktop_control_token is not None and desktop_shutdown_event is not None:
        app["desktop_control_token"] = desktop_control_token
        app["desktop_shutdown_event"] = desktop_shutdown_event
    # Self-restart needs a stop trigger and relaunch command in every mode,
    # independent of desktop-control authority.
    if desktop_shutdown_event is not None:
        app["daemon_stop_event"] = desktop_shutdown_event
    if relaunch_command:
        app["daemon_relaunch_command"] = list(relaunch_command)
    app.cleanup_ctx.append(runtime_context)
    app.add_routes(
        [
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
            web.get("/api/remote/status", remote_status),
            web.post("/api/remote/mobile-voice/enable", enable_mobile_voice),
            web.get("/api/config", get_config),
            web.get("/api/settings/bundle", settings_bundle),
            web.patch("/api/config", patch_config),
            web.post("/api/config/reset", reset_config),
            web.get("/api/keybindings", get_keybindings),
            web.put("/api/keybindings", put_keybindings),
            web.get("/api/hooks", get_hooks),
            web.get("/api/hooks/status", get_hook_status),
            web.put("/api/hooks", put_hooks),
            web.get("/api/automation", get_automation_status),
            web.get("/api/automation/rules", get_automation_rules),
            web.put("/api/automation/rules", put_automation_rules),
            web.patch("/api/automation/rules/{rule_id}", patch_automation_rule),
            web.post("/api/automation/dry-run", automation_dry_run),
            web.get("/api/automation/dashboard", automation_dashboard),
            web.get("/api/automation/firings", automation_firings),
            web.get("/api/annotations", list_annotations),
            web.get("/api/automation/provider", automation_provider_status),
            web.post("/api/automation/provider/key", automation_provider_key),
            web.post("/api/automation/provider/models/refresh", refresh_automation_models),
            web.get("/api/automation/notifications", automation_notifications),
            web.patch("/api/automation/notifications", patch_automation_notifications),
            web.patch(
                "/api/automation/notifications/{notification_id}",
                patch_automation_notification,
            ),
            web.get("/api/lineage", list_lineage),
            web.post("/api/lineage", create_lineage),
            web.get("/api/attention/absence", absence_report),
            web.get("/api/automation/injection-safety", injection_safety),
            web.post("/api/history/{sid}/second-opinion", second_opinion),
            web.get("/api/history/{sid}/handoff", export_handoff),
            web.get("/api/telemetry/workloads", workload_telemetry),
            web.get("/api/experiences", list_experiences),
            web.get("/api/automation/batches", list_observer_batches),
            web.post("/api/automation/batches", create_observer_batch),
            web.get("/api/profiles", list_profiles),
            web.get("/api/project/config", get_project_config),
            web.put("/api/project/config", put_project_config),
            web.get("/api/prompts", list_prompts),
            web.post("/api/prompts", create_prompt),
            web.put("/api/prompts/{scope}/{template_id}", put_prompt),
            web.delete("/api/prompts/{scope}/{template_id}", delete_prompt),
            web.post("/api/prompts/{scope}/{template_id}/use", use_prompt),
            web.patch("/api/prompts/{scope}/{template_id}/favorite", favorite_prompt),
            web.get("/api/queue", queue_summary),
            web.get("/api/queue/messages", queue_messages),
            web.post("/api/queue/messages", queue_create_message),
            web.patch("/api/queue/messages/{message_id}", queue_patch_message),
            web.post("/api/queue/messages/{message_id}/cancel", queue_cancel_message),
            web.get("/api/queue/messages/{message_id}/deliveries", queue_message_deliveries),
            web.post("/api/queue/send-next", queue_send_next),
            web.get("/api/queue/export", queue_export),
            # Phase 5: auto-delivery policy, the mailbox view, and the
            # emergency controls. Runtime state, not config-file state.
            web.get("/api/queue/auto", queue_auto_status),
            web.post("/api/queue/auto/pause", queue_auto_pause),
            web.put("/api/queue/auto/sessions/{sid}", queue_auto_session),
            web.post("/api/queue/auto/report-unsafe", queue_auto_report_unsafe),
            web.get("/api/queue/mailbox", queue_mailbox),
            web.get("/api/projects", list_projects),
            web.post("/api/projects", create_project),
            web.put("/api/projects/order", reorder_projects),
            web.patch("/api/projects/{project_id}", patch_project),
            web.delete("/api/projects/{project_id}", delete_project),
            web.get("/api/projects/{project_id}/actions", list_project_actions),
            web.post("/api/projects/{project_id}/actions/trust", trust_project_actions),
            web.post("/api/projects/{project_id}/actions/run", run_project_action),
            web.post("/api/projects/{project_id}/init-scripts/run", run_project_init_scripts),
            web.get("/api/global-notes/{note_id}", get_global_note),
            web.put("/api/global-notes/{note_id}", put_global_note),
            web.get("/api/notes", list_notes),
            web.post("/api/projects/{project_id}/notes", create_project_note),
            web.get("/api/projects/{project_id}/notes/{note_id}", get_note),
            web.put("/api/projects/{project_id}/notes/{note_id}", put_note),
            web.patch("/api/projects/{project_id}/notes/{note_id}", patch_note),
            web.delete("/api/projects/{project_id}/notes/{note_id}", delete_project_note),
            web.get("/api/projects/{project_id}/observations", get_observations),
            web.post("/api/projects/{project_id}/observations", post_observation),
            web.put("/api/projects/{project_id}/observations", put_observations),
            # Approving a drafted spawn request is the human act that creates
            # the session (`CONTROL_PLANE_ROADMAP.md` §7.2); dismissing it is
            # the other half. Both live here so mobile can do either.
            web.post(
                "/api/projects/{project_id}/observations/{observation_id}/decide",
                decide_observation_request,
            ),
            web.get("/api/projects/{project_id}/automations", get_project_automations),
            web.put("/api/projects/{project_id}/automations", put_project_automations),
            web.get("/api/projects/{project_id}/agent-context", get_agent_context),
            web.get(
                "/api/projects/{project_id}/agent-context/sources/{source_id}",
                get_agent_context_source,
            ),
            web.post(
                "/api/projects/{project_id}/agent-context/sources/{source_id}/reveal",
                reveal_agent_context_source,
            ),
            web.post(
                "/api/projects/{project_id}/agent-context/sync/preview",
                preview_agent_context_sync,
            ),
            web.post(
                "/api/projects/{project_id}/agent-context/sync",
                sync_agent_context,
            ),
            web.post(
                "/api/projects/{project_id}/agent-context/restore",
                restore_agent_context,
            ),
            web.get("/api/projects/{project_id}/files/tree", list_project_files_tree),
            web.get("/api/projects/{project_id}/files", list_project_files),
            web.post("/api/projects/{project_id}/resources", post_project_resource),
            web.get("/api/projects/{project_id}/search", search_project_files_route),
            web.get("/api/projects/{project_id}/file", get_project_file),
            web.get("/api/projects/{project_id}/file/content", get_project_file_content),
            web.put("/api/projects/{project_id}/file", put_project_file),
            web.post("/api/projects/{project_id}/reveal", reveal_project_resource),
            web.post("/api/projects/{project_id}/ignore", ignore_project_resource),
            web.put("/api/projects/{project_id}/watch", put_project_watch),
            web.delete("/api/projects/{project_id}/watch/{watch_id}", delete_project_watch),
            web.get("/api/project-groups", list_project_groups),
            web.post("/api/project-groups", create_project_group),
            web.put("/api/project-groups/order", reorder_project_groups),
            web.patch("/api/project-groups/{group_id}", patch_project_group),
            web.delete("/api/project-groups/{group_id}", delete_project_group),
            web.get("/api/git/projects", list_git_projects),
            web.post("/api/git/projects/resolve", resolve_project_scope),
            web.get("/api/git/projects/{scope_id}", get_project_scope),
            web.get("/api/artifacts", list_artifacts),
            web.post("/api/artifacts/{artifact_id}/transfer", transfer_artifact),
            web.get("/api/directories/pins", list_pinned_directories),
            web.post("/api/directories/pins", pin_directory),
            web.delete("/api/directories/pins", unpin_directory),
            web.get("/api/fs/roots", filesystem_roots),
            web.get("/api/fs/list", filesystem_list),
            web.get("/api/sessions", list_sessions),
            web.post("/api/sessions", spawn_session),
            web.get("/api/sessions/{sid}", get_session),
            web.get("/api/sessions/{sid}/state-log", get_session_state_log),
            web.get("/api/sessions/{sid}/diagnostic-bundle", get_session_diagnostic_bundle),
            web.get("/api/diagnostics/status-health", get_status_health),
            web.get("/api/diagnostics/background", get_background_health),
            web.get("/api/sessions/{sid}/last-reply", session_last_reply),
            web.get("/api/sessions/{sid}/transcript", session_transcript),
            web.get("/api/sessions/{sid}/skills", session_skills),
            web.get("/api/sessions/{sid}/agent-environment", session_agent_environment),
            web.patch("/api/sessions/{sid}", patch_session),
            web.post("/api/sessions/{sid}/title/regenerate", regenerate_session_title),
            web.post(
                "/api/sessions/{sid}/standing-activity/clear", clear_session_standing_activity
            ),
            web.delete("/api/sessions/{sid}", delete_session),
            web.post("/api/sessions/{sid}/relaunch", relaunch_session),
            web.post("/api/sessions/{sid}/branch", branch_session),
            web.post("/api/sessions/{sid}/input", session_input),
            web.post("/api/sessions/{sid}/startup-metrics", session_startup_metrics),
            web.post("/api/sessions/{sid}/broadcast-set", broadcast_set),
            web.post("/api/broadcast/input", broadcast_input_route),
            web.post("/api/sessions/{sid}/attachments", upload_session_attachment),
            web.post("/api/sessions/{sid}/media", upload_session_media),
            web.post("/api/sessions/{sid}/promote", promote_session),
            web.post("/api/sessions/{sid}/demote", demote_session),
            web.get("/api/history", list_history),
            web.get("/api/history/projects", list_history_projects),
            web.get("/api/history/backfills", list_history_backfills),
            web.post("/api/history/backfills", start_history_backfill),
            web.get("/api/history/backfills/{job_id}", get_history_backfill),
            web.delete("/api/history/backfills/{job_id}", cancel_history_backfill),
            # Registered before the `{sid}` routes so the static segment wins.
            web.get("/api/history/duplicates", list_history_duplicates),
            web.post("/api/history/duplicates/repair", repair_history_duplicates),
            web.get("/api/history/{sid}/transcript", history_transcript),
            web.post("/api/history/{sid}/resume", resume_history),
            web.delete("/api/history/{sid}", delete_history_entry),
            web.get("/api/events", list_events),
            web.get("/api/settings", get_settings),
            web.put("/api/settings/{profile}", put_settings),
            web.get("/api/clipboard", list_clipboard_entries),
            web.post("/api/clipboard", capture_clipboard_entry),
            web.delete("/api/clipboard", clear_clipboard_entries),
            web.get("/api/clipboard/{entry_id}", get_clipboard_entry),
            web.patch("/api/clipboard/{entry_id}", patch_clipboard_entry),
            web.delete("/api/clipboard/{entry_id}", delete_clipboard_entry),
            web.get("/api/push/vapid-public-key", get_vapid_public_key),
            web.post("/api/push/subscribe", push_subscribe),
            web.post("/api/push/unsubscribe", push_unsubscribe),
            web.post("/api/push/presence", push_presence),
            web.get("/api/push/presence", get_device_presence),
            web.get("/api/notifications", list_notifications),
            web.get("/api/voice", voice_status),
            web.post("/api/sessions/{sid}/voice/transcribe", voice_transcribe),
            web.post("/api/sessions/{sid}/voice/submit", voice_submit),
            web.post("/api/sessions/{sid}/voice/interrupt", voice_interrupt),
            web.post("/api/sessions/{sid}/voice/generate", voice_generate),
            web.get("/api/voice/clips", list_voice_clips),
            web.get("/api/voice/clips/{clip_id}/audio", voice_clip_audio),
            web.delete("/api/voice/clips/{clip_id}", delete_voice_clip),
            web.get("/api/usage", get_usage),
            web.post("/api/usage/refresh", refresh_usage),
            web.delete("/api/usage/cache", clear_usage_cache),
            web.get("/api/telemetry/operational", operational_telemetry),
            web.patch("/api/telemetry/quota-resets/{reset_id}", review_quota_reset),
            web.get("/api/provider-accounts", get_provider_accounts),
            web.get("/api/provider-accounts/audit", get_provider_account_audit),
            web.post("/api/provider-accounts/refresh", refresh_provider_accounts),
            web.post("/api/provider-accounts/verify", verify_provider_accounts),
            web.post("/api/provider-accounts/{provider}/capture", capture_provider_account),
            web.post("/api/provider-accounts/{provider}/login", login_provider_account),
            web.patch("/api/provider-accounts/{provider}/{account_id}", patch_provider_account),
            web.post(
                "/api/provider-accounts/{provider}/{account_id}/select",
                select_provider_account,
            ),
            web.post(
                "/api/provider-accounts/{provider}/{account_id}/adopt",
                adopt_provider_account,
            ),
            web.post(
                "/api/provider-accounts/{provider}/{account_id}/purge-telemetry",
                purge_provider_account_telemetry,
            ),
            web.delete("/api/provider-accounts/{provider}/{account_id}", remove_provider_account),
            web.get("/api/processes", list_processes),
            web.post("/api/processes/action", process_action),
            web.get("/api/previews", list_previews),
            web.post("/api/previews", create_preview),
            web.delete("/api/previews/{preview_id}", delete_preview),
            web.post("/api/previews/{preview_id}/capture", capture_preview),
            web.route("*", "/preview/{preview_id}/{tail:.*}", preview_proxy),
            web.post("/api/hooks/{sid}", hook_ingress),
            web.post("/mcp", mcp_endpoint),
            web.get("/api/git/worktrees", list_worktrees),
            web.get("/api/git/graph", git_graph),
            web.get("/api/git/commits/{oid}/changes", git_commit_changes),
            web.get("/api/git/diff", git_diff),
            web.post("/api/git/worktrees", create_worktree),
            web.delete("/api/git/worktrees", remove_worktree),
            web.post("/api/reveal", reveal_path),
            web.get("/pty/{sid}", pty_ws),
            web.get("/events", events_ws),
        ]
    )
    # Serve the editor engine's WebAssembly with the MIME type browsers require
    # for streaming compilation; Windows' registry often lacks this mapping and
    # nosniff would otherwise reject an application/octet-stream .wasm response.
    mimetypes.add_type("application/wasm", ".wasm")
    # Windows' registry rarely maps .webmanifest; without this the manifest is
    # served as octet-stream and Chrome refuses to treat the app as installable.
    mimetypes.add_type("application/manifest+json", ".webmanifest")
    assets = app["frontend_dir"] / "assets"
    if assets.is_dir():
        app.router.add_static("/assets", assets)
    notification_sounds = app["frontend_dir"] / "notification-sounds"
    if notification_sounds.is_dir():
        app.router.add_static("/notification-sounds", notification_sounds)
    icons = app["frontend_dir"] / "icons"
    if icons.is_dir():
        app.router.add_static("/icons", icons)
    return app


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
    config: Config = app["config"]
    # Death forensics first: report a predecessor that vanished without a clean
    # shutdown while this daemon is still barely started, then keep our own
    # heartbeat fresh so the next daemon can do the same for us.
    daemon_started(config.data_dir, log)
    # Nothing here is reachable until the listener binds, which happens only
    # after this context is fully built, so every second spent below is a second
    # the daemon is invisible to clients and to the desktop shell's health probe.
    startup_started = time.monotonic()
    background.start(LIFECYCLE_HEARTBEAT_LOOP, lambda: _lifecycle_heartbeat_loop(config.data_dir))
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
    # tables, so it stays on the startup path.
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
    tier0 = Tier0Store(config.database_path, retention_days=config.process_evidence_retention_days)
    # Durable per-session detection timeline: every ledger entry survives
    # daemon restarts and session ends so status incidents stay investigable
    # (status-detection.md § durable timeline). Pruned by its own flush loop.
    status_timeline = StatusTimelineStore(
        config.database_path, retention_days=config.status_timeline_retention_days
    )
    projects = ProjectManager(history)
    await projects.start()
    agent_context = AgentContextService(config.data_dir / "agent-context-backups")
    for project in projects.projects.values():
        agent_context.capture_project(project.root)
    history_backfills = HistoryBackfillManager(history, projects)
    reaper = ReaperJob()
    supervisor_client: SupervisorClient | None = None
    if config.pty_supervisor_enabled:
        try:
            connected_client = await SupervisorClient.connect_or_spawn(config)
            supervisor_client = connected_client
            log.info(
                "PTY supervisor connected (pid %d, %d existing session(s))",
                connected_client.supervisor_pid,
                len(connected_client.initial_sessions),
            )
        except Exception:
            log.exception(
                "PTY supervisor unavailable; sessions will run in-process and "
                "will not survive a daemon restart"
            )
    mcp_url = f"http://127.0.0.1:{config.port}/mcp"
    adapters: dict[str, BackendAdapter] = {"shell": ShellAdapter(config.shell_exe)}
    for name, harness in HARNESSES.items():
        adapters[name] = build_agent_adapter(
            harness,
            executable=config.harness_exe[name],
            args=config.harness_args[name],
            data_dir=config.data_dir,
            mcp_url=mcp_url,
            command_resolver=(
                resolve_codex_pty_command if harness.adapter_family == "codex" else None
            ),
        )
    child_env = create_agent_shims(
        config,
        adapters["claude"].settings_path,  # type: ignore[attr-defined]
        adapters["claude"].mcp_config_path,  # type: ignore[attr-defined]
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
    )
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
    git_monitor = GitMonitor(sessions, events, config.git_poll_seconds)
    hooks = MetaHookEngine(config.data_dir / "hooks.toml", events, sessions)
    # Pruned by `RETENTION_LOOP` a minute after start, not here.
    automation_store = AutomationStore(config.database_path)
    secret_store = PlatformSecretStore(config.data_dir / "automation.secrets.json")
    openrouter = OpenRouterClient(
        secret_store, timeout_seconds=config.openrouter_request_timeout_seconds
    )
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
    process_inspector = ProcessInspector(
        sessions,
        events,
        cadence=config.process_poll_seconds,
        telemetry=telemetry,
        orphan_grace_seconds=config.process_orphan_grace_seconds,
    )
    previews = PreviewRegistry(process_inspector, sessions)
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
        lambda session, data: _record_operator_input(
            events, session, data, source="queue", input_owner=False
        ),
    )
    # Phase 5: the one non-human caller of send_next, and the relay policy over
    # enqueue. Neither is a second delivery path — both call the typed queue
    # operations above (`CONTROL_PLANE_ROADMAP.md` §7.1).
    auto_delivery = AutoDeliveryController(prompt_queue, sessions, config)
    agent_messaging = AgentMessagingService(
        prompt_queue,
        sessions,
        projects,
        config,
        auto_delivery,
        append_observation=append_observation,
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

    async def _enabled_automations(root: str) -> frozenset[str]:
        """Per-project opt-in closure, resolved off-loop with a short TTL cache.

        Shared by Tier 0 capture and the deterministic consumers so a project can
        never have one running under a stale answer the other already refreshed.
        """
        now = time.monotonic()
        cached = automation_gate_cache.get(root)
        if cached and now - cached[0] < 5.0:
            return cached[1]
        project_map = await asyncio.to_thread(project_automations, root)
        enabled = resolve_automation_config(project_map).enabled
        automation_gate_cache[root] = (now, enabled)
        return enabled

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

    async def project_card_context(session_id: str) -> ProjectCardContext | None:
        # Same gate, same TTL cache as Tier 0: a session's card is only built
        # when its owning project opted the card in. The card is per *project*,
        # so the session lookup exists only to name one.
        resolved = _session_project_root(session_id)
        if resolved is None:
            return None
        record, root = resolved
        if not record.project_id:
            return None
        if "project_card" not in await _enabled_automations(root):
            return None
        return ProjectCardContext(project_id=record.project_id, project_root=root)

    async def project_card_enabled(root: str) -> bool:
        return "project_card" in await _enabled_automations(root)

    # Control-plane step 4: built lazily on the first consumer request, cached
    # per documentation fingerprint, and absent rather than guessed when no
    # provider is available.
    project_cards = ProjectCardService(
        automation_store,
        config,
        openrouter,
        resolve_session=project_card_context,
        resolve_project=project_card_enabled,
    )
    tier0.start(events, resolve_context=tier0_context)
    # Phase 3.7: model-free detectors over the facts Tier 0 just captured. Same
    # gate, same cache; writes annotations and nothing else.
    consumers = DeterministicConsumerService(
        tier0, automation_store, sessions, events, resolve_context=consumer_context
    )
    consumers.start()
    git_monitor.start()
    hooks.start()
    automation.start()
    usage.start()
    await provider_accounts.reconcile_startup()
    provider_accounts.start()
    await process_inspector.restore()
    process_inspector.start()
    fleet.start()
    voice.start()
    # After supervisor adoption: the startup reconcile strands queue items
    # whose target session or agent run did not survive the restart.
    await prompt_queue.start()
    # The auto-delivery controller starts regardless of the master switch: it
    # also sweeps message expiry, which is a promise the user made about any
    # delivery path, and it re-checks its own enablement every tick.
    auto_delivery.start()
    project_watcher.start()
    # Every long-lived loop runs under the background-task supervisor: restarted
    # with capped backoff, faults counted, health surfaced at
    # /api/diagnostics/background. An unsupervised loop that dies is invisible.
    # Started first among the supervised loops, so its own baseline is measured from
    # the same moment everything that can stall it begins running.
    loop_lag = LoopLagMonitor()
    app["loop_lag"] = loop_lag
    background.start(LOOP_LAG_LOOP, lambda: _loop_lag_loop(loop_lag))
    background.start(CONFIG_WATCH_LOOP, lambda: _watch_config(app))
    background.start(MEDIA_CLEANUP_LOOP, lambda: _media_cleanup_loop(config.data_dir, projects))
    background.start(
        RETENTION_LOOP,
        lambda: _retention_loop(automation_store, tier0, prompt_queue_store, config),
    )
    background.start(STATE_WATCHDOG_LOOP, sessions.state_watchdog_loop)
    status_timeline.start()
    push_sender = PushSender(push_store, settings_store, events, presence=device_presence)
    background.start(PUSH_SENDER_LOOP, push_sender.run)
    reconcile_task: asyncio.Task[int] | None = None
    if config.reconcile_external_history:
        reconcile_task = asyncio.create_task(
            reconcile_external_history(history), name="history-reconcile"
        )
        # A one-shot task that dies is silent by default; the scan's failure mode
        # is "external history is quietly stale", which nothing else reports.
        reconcile_task.add_done_callback(_log_task_failure)
    app.update(
        history=history,
        events=events,
        projects=projects,
        history_backfills=history_backfills,
        sessions=sessions,
        mcp=McpService(sessions, history, agent_messaging),
        reaper=reaper,
        supervisor=supervisor_client,
        git_monitor=git_monitor,
        hooks=hooks,
        automation=automation,
        automation_store=automation_store,
        secret_store=secret_store,
        openrouter=openrouter,
        usage=usage,
        telemetry=telemetry,
        status_timeline=status_timeline,
        tier0=tier0,
        deterministic_consumers=consumers,
        project_cards=project_cards,
        provider_accounts=provider_accounts,
        process_inspector=process_inspector,
        previews=previews,
        fleet=fleet,
        voice=voice,
        voice_store=voice_store,
        prompt_library=prompt_library,
        prompt_queue=prompt_queue,
        auto_delivery=auto_delivery,
        agent_messaging=agent_messaging,
        agent_context=agent_context,
        settings_store=settings_store,
        clipboard=clipboard,
        push_store=push_store,
        device_presence=device_presence,
        project_actions=project_actions,
        project_watcher=project_watcher,
        automation_tasks=set(),
    )
    # The startup duration nobody could see. A daemon takes this long to become
    # reachable, and the desktop shell budgets its health wait against it, so a
    # start that drifts is worth a line of its own rather than an inference from
    # the gap between two unrelated INFO timestamps.
    startup_seconds = time.monotonic() - startup_started
    log.log(
        logging.WARNING if startup_seconds > SLOW_STARTUP_SECONDS else logging.INFO,
        "daemon runtime ready in %.1fs (%d live session(s)); binding listeners",
        startup_seconds,
        len(sessions.sessions),
    )
    yield
    if reconcile_task:
        if not reconcile_task.done():
            reconcile_task.cancel()
        await asyncio.gather(reconcile_task, return_exceptions=True)
    await history_backfills.stop()
    for task in tuple(app["automation_tasks"]):
        task.cancel()
    await asyncio.gather(*app["automation_tasks"], return_exceptions=True)
    for loop_name in (
        CONFIG_WATCH_LOOP,
        MEDIA_CLEANUP_LOOP,
        RETENTION_LOOP,
        STATE_WATCHDOG_LOOP,
        PUSH_SENDER_LOOP,
    ):
        await background.stop(loop_name)
    await hooks.stop()
    await automation.stop()
    await consumers.stop()
    await auto_delivery.stop()
    await prompt_queue.stop()
    await voice.stop()
    await project_watcher.stop()
    await usage.stop()
    await provider_accounts.stop()
    await fleet.stop()
    await process_inspector.stop()
    await git_monitor.stop()
    # Shutdown intent (SESSION_PRESERVING_RELOAD §5.3): "quit" reaps everything
    # (today's behavior, and always the case without a supervisor); "detach"
    # leaves supervisor-owned sessions running so the next daemon reattaches.
    # The intent comes from outside the daemon (desktop shutdown endpoint);
    # with a supervisor attached, an unqualified exit (Ctrl-C, crash-adjacent
    # teardown) defaults to detach — the tmux model.
    intent = app["shutdown_state"]["intent"] or ("detach" if supervisor_client else "quit")
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
    await status_timeline.stop()
    await telemetry.stop()
    await tier0.stop()
    await clipboard.stop()
    history.close()
    automation_store.close()
    prompt_queue_store.close()
    voice_store.close()
    telemetry.close()
    status_timeline.close()
    tier0.close()
    clipboard.close()
    reaper.close()
    await background.stop(LIFECYCLE_HEARTBEAT_LOOP)
    # Last so an exception anywhere above still reads as an unclean exit.
    await asyncio.to_thread(daemon_clean_exit, config.data_dir, intent)


def _config_mtime(path: Path) -> int:
    """Config mtime, or 0 when the file is absent or momentarily unreadable.

    Editors save by delete+rename, so `exists()` and `stat()` genuinely disagree.
    An unguarded stat here used to kill config hot reload for the daemon's
    lifetime on a single unlucky poll.
    """
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return 0


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


async def _watch_config(app: web.Application) -> None:
    config: Config = app["config"]
    path = config.config_path
    if path is None:
        return
    modified = _config_mtime(path)
    while True:
        await asyncio.sleep(1)
        with background.iteration(CONFIG_WATCH_LOOP):
            current = _config_mtime(path)
            if current == modified:
                continue
            modified = current
            try:
                loaded = load_config(path)
                if loaded.public_dict() == config.public_dict():
                    continue
                changed = {
                    field_name
                    for field_name in Config.__dataclass_fields__
                    if getattr(config, field_name) != getattr(loaded, field_name)
                }
                loaded.revision = config.revision + 1
                for field_name in Config.__dataclass_fields__:
                    setattr(config, field_name, getattr(loaded, field_name))
                _apply_runtime_config(app, changed)
                await app["events"].emit(
                    "configuration_changed", source="external_file", revision=config.revision
                )
            except (OSError, ValueError, TypeError, tomllib.TOMLDecodeError) as exc:
                await app["events"].emit(
                    "configuration_error", source="external_file", error=str(exc)
                )


def _apply_runtime_config(app: web.Application, changed: set[str]) -> None:
    config: Config = app["config"]
    if "log_level" in changed:
        with suppress(ValueError):  # _validate already constrains the value
            set_log_level(config.log_level)
    sessions: SessionManager | None = app.get("sessions")
    if sessions:
        if "scrollback_bytes" in changed:
            sessions.max_scrollback = config.scrollback_bytes
        if "shell_exe" in changed:
            sessions.adapters["shell"].configure(config.shell_exe, [])
        if changed & {"harness_exe", "harness_args"}:
            for backend in HARNESSES:
                executable = config.harness_exe[backend]
                args = config.harness_args[backend]
                sessions.adapters[backend].configure(executable, args)
                prefix = f"MUX_{backend.upper().replace('-', '_')}"
                sessions.child_env[f"{prefix}_EXE"] = resolve_command(executable)
                sessions.child_env[f"{prefix}_ARGS"] = json.dumps(args)
    git_monitor: GitMonitor | None = app.get("git_monitor")
    if git_monitor and "git_poll_seconds" in changed:
        git_monitor.cadence = config.git_poll_seconds
    process_inspector: ProcessInspector | None = app.get("process_inspector")
    if process_inspector:
        if "process_poll_seconds" in changed:
            process_inspector.cadence = config.process_poll_seconds
        if "process_orphan_grace_seconds" in changed:
            process_inspector.orphan_grace_seconds = config.process_orphan_grace_seconds
    provider_accounts: ProviderAccountManager | None = app.get("provider_accounts")
    if provider_accounts:
        if "provider_quota_poll_minutes" in changed:
            provider_accounts.poll_seconds = config.provider_quota_poll_minutes * 60
        if "provider_quota_turn_refresh_enabled" in changed:
            provider_accounts.turn_refresh_enabled = config.provider_quota_turn_refresh_enabled
        if "provider_quota_turn_refresh_min_minutes" in changed:
            provider_accounts.turn_refresh_min_seconds = (
                config.provider_quota_turn_refresh_min_minutes * 60
            )
        if "harness_exe" in changed:
            for provider in provider_accounts.executables:
                provider_accounts.executables[provider] = config.harness_exe[provider]
    clipboard: ClipboardStore | None = app.get("clipboard")
    if clipboard and any(field.startswith("clipboard_history_") for field in changed):
        # Owns its own side effects: disabling drops the ring, and turning
        # persistence off deletes the rows already written.
        clipboard.apply_config(config)
    telemetry: OperationalTelemetryStore | None = app.get("telemetry")
    if telemetry and "operational_telemetry_retention_days" in changed:
        telemetry.retention_days = config.operational_telemetry_retention_days
    if telemetry and "process_evidence_retention_days" in changed:
        telemetry.process_retention_days = config.process_evidence_retention_days


async def index(request: web.Request) -> web.StreamResponse:
    path: Path = request.app["frontend_dir"] / "index.html"
    if not path.exists():
        return web.Response(
            text="swe-mux frontend is not built. Run: cd frontend; npm install; npm run build",
            content_type="text/plain",
        )
    return web.FileResponse(path)


async def manifest(request: web.Request) -> web.StreamResponse:
    path: Path = request.app["frontend_dir"] / "manifest.webmanifest"
    if not path.is_file():
        raise web.HTTPNotFound()
    return web.FileResponse(path)


async def service_worker(request: web.Request) -> web.StreamResponse:
    # Served from the origin root so its scope covers the whole app.
    path: Path = request.app["frontend_dir"] / "sw.js"
    if not path.is_file():
        raise web.HTTPNotFound()
    return web.FileResponse(
        path, headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-cache"}
    )


async def health(request: web.Request) -> web.Response:
    sessions: SessionManager = request.app.get("sessions")
    live = sum(s.pty.isalive() for s in sessions.sessions.values()) if sessions else 0
    supervisor = request.app.get("supervisor")
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
            "supervisor": connected,
            "supervisor_state": "connected" if connected else ("lost" if lost else "absent"),
            # Supervised sessions this daemon could not rebuild (snapshot drift,
            # a crash inside the spawn-meta window). They keep running under the
            # supervisor with no UI handle, so the count must be visible.
            "supervisor_unadopted": unadopted,
        }
    )


async def get_harnesses(_request: web.Request) -> web.Response:
    return json_response(public_harness_registry())


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
    expected: str | None = request.app.get("desktop_control_token")
    shutdown_event: asyncio.Event | None = request.app.get("desktop_shutdown_event")
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
    request.app["shutdown_state"]["intent"] = "quit" if mode == "quit" else "detach"
    shutdown_event.set()
    response = json_response({"status": "shutting_down", "mode": mode}, 202)
    response.headers["Cache-Control"] = "no-store"
    return response


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
    """
    stop_event: asyncio.Event | None = request.app.get("daemon_stop_event")
    relaunch: list[str] | None = request.app.get("daemon_relaunch_command")
    if stop_event is None or not relaunch:
        return json_response(
            {
                "error": "restart_unavailable",
                "message": "this daemon was not started with a relaunchable entry point",
            },
            409,
        )
    supervisor = request.app.get("supervisor")
    attached = bool(supervisor is not None and supervisor.connected)
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
    config: Config = request.app["config"]
    request.app["shutdown_state"]["intent"] = "detach" if attached else "quit"
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
        candidates.append(Path(__file__).resolve().parents[2])
    for root in candidates:
        if (root / "packaging" / "redeploy_desktop.py").is_file() and (
            root / "pyproject.toml"
        ).is_file():
            return root
    return None


def _redeploy_lock_pid(config: Config) -> int | None:
    """PID of a live in-flight redeploy, or None (missing/stale lock)."""
    import psutil

    try:
        pid = int((config.data_dir / "redeploy.lock").read_text(encoding="ascii").strip())
    except (OSError, ValueError):
        return None
    return pid if psutil.pid_exists(pid) else None


async def daemon_redeploy(request: web.Request) -> web.Response:
    """Kick off the staged frozen-app redeploy (the UI "Rebuild + redeploy").

    Spawns ``packaging/redeploy_desktop.py`` detached from this daemon's
    lifetime and returns immediately. The script builds into staging while
    this daemon keeps serving, stops it only after a successful build, swaps
    the bundle in, and rolls back to the previous bundle if the new one never
    reports healthy — so a failed redeploy never strands a remote client.
    """
    config: Config = request.app["config"]
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
    supervisor = request.app.get("supervisor")
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
    lock_path = config.data_dir / "redeploy.lock"
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
        "--hidden",
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
    lock_path.write_text(str(process.pid), encoding="ascii")
    response = json_response(
        {"status": "redeploying", "pid": process.pid, "log": str(log_path)}, 202
    )
    response.headers["Cache-Control"] = "no-store"
    return response


async def daemon_redeploy_status(request: web.Request) -> web.Response:
    """Whether a redeploy is in flight, plus the tail of its build log.

    While the build stage runs this daemon is still alive, so the UI can
    detect an early build failure (running=false without ever losing the
    daemon) and surface the log instead of waiting out a reconnect window.
    """
    config: Config = request.app["config"]
    pid = _redeploy_lock_pid(config)
    tail = ""
    try:
        data = (config.data_dir / "redeploy.log").read_bytes()
        tail = data[-8192:].decode("utf-8", "replace")
    except OSError:
        pass
    response = json_response(
        {
            "running": pid is not None,
            "pid": pid,
            "log_tail": tail.splitlines()[-40:],
            "available": redeploy_source_root() is not None and shutil.which("uv") is not None,
        }
    )
    response.headers["Cache-Control"] = "no-store"
    return response


async def remote_status(request: web.Request) -> web.Response:
    config: Config = request.app["config"]
    return json_response(
        await tailscale_status(config.port, tailnet_enabled=config.tailnet_enabled)
    )


async def enable_mobile_voice(request: web.Request) -> web.Response:
    config: Config = request.app["config"]
    if request.headers.get("X-Mux-User-Gesture") != "mobile-voice-setup":
        return json_response({"error": "mobile voice setup requires an explicit user action"}, 400)
    if not config.tailnet_enabled:
        return json_response(
            {"error": "Enable the Tailscale listener in Settings before mobile voice."}, 409
        )
    result = await enable_mobile_voice_serve(config.port)
    return json_response(result, 200 if result.get("status") == "ready" else 409)


async def get_config(request: web.Request) -> web.Response:
    config: Config = request.app["config"]
    response = json_response(config.public_dict())
    response.headers["ETag"] = f'"{config.revision}"'
    return response


async def settings_bundle(request: web.Request) -> web.Response:
    """Everything the Settings panel needs on open, in one round trip.

    The panel used to fan out nine GETs; each answered in well under 50ms, but
    on a high-RTT client (phone over Tailscale) connection setup and RTT per
    request dominated the perceived open delay. `config` is the one part the
    panel cannot render without, so its failure fails the request; every other
    part degrades to null with the reason under `errors`, and the client
    decides which missing parts it can tolerate.
    """
    config: Config = request.app["config"]
    cwd = request.query.get("cwd")
    parts: dict[str, Any] = {}
    errors: dict[str, str] = {}

    async def part(key: str, factory: Callable[[], Awaitable[Any]]) -> None:
        try:
            parts[key] = await factory()
        except Exception as exc:  # noqa: BLE001 — each part degrades independently
            parts[key] = None
            errors[key] = str(exc)

    async def keybindings() -> Any:
        return _keybindings_payload(config)

    async def rules() -> Any:
        return _automation_rules_payload(request)

    async def profiles() -> Any:
        # Shell detection stats a handful of executables; keep it off the loop.
        return await asyncio.to_thread(profile_payload, config)

    async def usage() -> Any:
        return request.app["usage"].snapshot()

    async def project_config() -> Any:
        return await read_project_config(cwd) if cwd else None

    await asyncio.gather(
        part("automation_rules", rules),
        part("keybindings", keybindings),
        part("profiles", profiles),
        part("projects", lambda: _projects_payload(request)),
        part("automation", lambda: _automation_status_payload(request)),
        part("provider", lambda: _provider_status(request)),
        part("usage", usage),
        part("project_config", project_config),
    )
    return json_response({"config": config.public_dict(), **parts, "errors": errors})


async def patch_config(request: web.Request) -> web.Response:
    config: Config = request.app["config"]
    supplied = request.headers.get("If-Match", "").strip('"')
    if supplied and supplied != str(config.revision):
        return json_response(
            {"error": "configuration changed externally", "revision": config.revision}, 409
        )
    body = await request.json()
    body_revision = body.pop("_revision", None)
    if body_revision is not None and int(body_revision) != config.revision:
        return json_response(
            {"error": "configuration changed externally", "revision": config.revision}, 409
        )
    try:
        hot, restart = update_config(config, body)
    except ValueError as exc:
        detail = exc.args[0]
        return json_response(
            {
                "error": "invalid configuration",
                "fields": detail if isinstance(detail, dict) else {},
            },
            422,
        )
    _apply_runtime_config(request.app, hot)
    await request.app["events"].emit(
        "configuration_changed", source="settings", changed=sorted(hot | restart)
    )
    response = json_response(
        {**config.public_dict(), "hot_applied": sorted(hot), "restart_required": sorted(restart)}
    )
    response.headers["ETag"] = f'"{config.revision}"'
    return response


async def reset_config(request: web.Request) -> web.Response:
    config: Config = request.app["config"]
    defaults = Config(data_dir=config.data_dir)
    fields = {
        key: getattr(defaults, key)
        for key in Config.__dataclass_fields__
        if key
        not in {
            "schema_version",
            "revision",
            "token",
            "data_dir",
            "config_path",
            "shell_profiles",
            "default_shell_profile",
        }
    }
    hot, restart = update_config(config, fields)
    await request.app["events"].emit("configuration_changed", source="settings", reset=True)
    return json_response(
        {**config.public_dict(), "hot_applied": sorted(hot), "restart_required": sorted(restart)}
    )


def _keybindings_payload(config: Config) -> dict[str, Any]:
    defaults = dict(DEFAULT_KEYBINDINGS)
    path = config.data_dir / "keybindings.json"
    rejected: dict[str, str] = {}
    if path.exists():
        try:
            supplied = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid keybindings.json: {exc}") from exc
        replace_defaults = bool(
            isinstance(supplied, dict) and supplied.get("replace_defaults") is True
        )
        document_version = (
            int(supplied.get("version", 1))
            if replace_defaults and isinstance(supplied.get("version", 1), int)
            else 1
        )
        if replace_defaults:
            supplied = supplied.get("bindings", {})
            defaults = {}
        if not isinstance(supplied, dict):
            raise ValueError("keybindings.json must contain an object")
        for chord, command in supplied.items():
            try:
                key, command_id = normalize_binding(chord, command)
                defaults[key] = command_id
            except ValueError as exc:
                rejected[str(chord)] = str(exc)
        # Version 1 could not contain these chords through the Settings/API path:
        # both were rejected as browser-reserved. Seed the new desktop defaults
        # once, while a version 2 document continues to preserve an intentional
        # clear or remap.
        if replace_defaults and document_version < KEYBINDINGS_FILE_VERSION:
            for chord, command_id in V2_DEFAULT_KEYBINDINGS.items():
                defaults.setdefault(chord, command_id)
    commands = [
        {"id": command_id, "label": label, "category": category}
        for command_id, label, category in KEYBINDING_COMMANDS
    ]
    return {
        "bindings": defaults,
        "defaults": DEFAULT_KEYBINDINGS,
        "commands": commands,
        "policy": keybinding_policy(),
        "rejected": rejected,
    }


async def get_keybindings(request: web.Request) -> web.Response:
    return json_response(_keybindings_payload(request.app["config"]))


async def put_keybindings(request: web.Request) -> web.Response:
    body = await request.json()
    bindings = body.get("bindings", body)
    if not isinstance(bindings, dict):
        raise ValueError("bindings must be an object")
    rejected: dict[str, str] = {}
    normalized: dict[str, str] = {}
    for chord, command in bindings.items():
        try:
            key, command_id = normalize_binding(chord, command)
            normalized[key] = command_id
        except ValueError as exc:
            rejected[str(chord)] = str(exc)
    if rejected:
        return json_response({"error": "invalid keybindings", "fields": rejected}, 422)
    if request.query.get("validate") == "1":
        return json_response({"ok": True})
    path = request.app["config"].data_dir / "keybindings.json"
    temporary = path.with_suffix(".json.tmp")
    document = {
        "version": KEYBINDINGS_FILE_VERSION,
        "replace_defaults": True,
        "bindings": normalized,
    }
    temporary.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)
    await request.app["events"].emit("configuration_changed", source="keybindings")
    return await get_keybindings(request)


async def get_hooks(request: web.Request) -> web.Response:
    path = request.app["config"].data_dir / "hooks.toml"
    return json_response({"text": path.read_text(encoding="utf-8") if path.exists() else ""})


async def get_hook_status(request: web.Request) -> web.Response:
    hooks: MetaHookEngine = request.app["hooks"]
    return json_response(
        {
            "diagnostic": hooks.diagnostic,
            "deliveries": [item.snapshot() for item in hooks.deliveries[-100:]],
        }
    )


async def put_hooks(request: web.Request) -> web.Response:
    text = str((await request.json()).get("text", ""))
    try:
        parse_hook_rules(text)
    except ValueError as exc:
        return json_response({"error": "invalid hooks TOML", "fields": {"text": str(exc)}}, 422)
    if request.query.get("validate") == "1":
        return json_response({"ok": True})
    path = request.app["config"].data_dir / "hooks.toml"
    temporary = path.with_suffix(".toml.tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)
    await request.app["events"].emit("configuration_changed", source="hooks")
    return json_response({"text": text})


# Diagnostic repository rules re-read/re-parse on every /automation request; cache
# the parsed entry per rules.toml path, invalidated by (mtime_ns, size).
_repo_rules_cache: dict[str, tuple[tuple[int, int], dict[str, Any]]] = {}
_repo_rules_lock = threading.Lock()


def _load_repo_rule_entry(project_id: str, root: str) -> dict[str, Any] | None:
    """Build one project's inert repository-rules diagnostic, cached by mtime+size.

    Runs entirely in a worker thread (stat + read + TOML parse all block). The
    diagnostic is a deterministic function of file content, so caching an
    invalid-parse entry by version is correct too. Returns None when there is no
    regular rules.toml, mirroring the original `not path.is_file()` skip.
    """
    path = Path(str(root)) / ".swe-mux" / "rules.toml"
    try:
        if not path.is_file():
            return None
        version = (path.stat().st_mtime_ns, path.stat().st_size)
    except OSError:
        return None
    key = str(path)
    with _repo_rules_lock:
        cached = _repo_rules_cache.get(key)
    if cached and cached[0] == version:
        return {**cached[1], "project_scope_id": project_id}
    try:
        rules = parse_rules(path.read_text(encoding="utf-8"), source="repository-inert")
        entry: dict[str, Any] = {
            "project_scope_id": project_id,
            "path": str(path),
            "valid": True,
            "rules": [rule.snapshot() for rule in rules],
            "execution": "inert",
        }
    except (OSError, RuleValidationError) as exc:
        entry = {
            "project_scope_id": project_id,
            "path": str(path),
            "valid": False,
            "diagnostic": str(exc),
            "execution": "inert",
        }
    with _repo_rules_lock:
        _repo_rules_cache[key] = (version, entry)
    return {**entry, "project_scope_id": project_id}


async def _automation_status_payload(request: web.Request) -> dict[str, Any]:
    automation: AutomationEngine = request.app["automation"]
    projects = await request.app["history"].project_scopes(include_hidden=True)
    entries = await asyncio.gather(
        *(
            asyncio.to_thread(_load_repo_rule_entry, str(project["id"]), str(project["root"]))
            for project in projects
        )
    )
    repository_rules = [entry for entry in entries if entry is not None]
    return {
        **automation.status(),
        "legacy": {
            "path": str(request.app["config"].data_dir / "hooks.toml"),
            "active": bool(request.app["hooks"].rules),
            "diagnostic": request.app["hooks"].diagnostic,
            "migration": "explicit-save-required",
        },
        "repository_rules": repository_rules,
    }


async def get_automation_status(request: web.Request) -> web.Response:
    return json_response(await _automation_status_payload(request))


def _automation_rules_payload(request: web.Request) -> dict[str, Any]:
    path = request.app["config"].data_dir / "rules.toml"
    return {
        "version": 1,
        "text": path.read_text(encoding="utf-8") if path.exists() else "version = 1\n",
        "rules": [rule.snapshot() for rule in request.app["automation"].rules],
        "diagnostic": request.app["automation"].diagnostic,
    }


async def get_automation_rules(request: web.Request) -> web.Response:
    return json_response(_automation_rules_payload(request))


async def put_automation_rules(request: web.Request) -> web.Response:
    text = str((await request.json()).get("text", ""))
    try:
        parse_rules(text)
    except RuleValidationError as exc:
        return json_response({"error": "invalid rules TOML", "fields": {"text": str(exc)}}, 422)
    if request.query.get("validate") == "1":
        return json_response({"ok": True})
    path = request.app["config"].data_dir / "rules.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".toml.tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)
    automation: AutomationEngine = request.app["automation"]
    automation.reload()
    await request.app["events"].emit("configuration_changed", source="settings")
    return await get_automation_rules(request)


async def patch_automation_rule(request: web.Request) -> web.Response:
    body = await request.json()
    if not isinstance(body, dict) or not body or set(body) - {"enabled", "shadow"}:
        raise ValueError("only enabled and shadow may be changed through the ordinary editor")
    if any(not isinstance(value, bool) for value in body.values()):
        raise ValueError("enabled and shadow must be boolean")
    rule_id = request.match_info["rule_id"]
    automation: AutomationEngine = request.app["automation"]
    found = False
    rules = []
    for rule in automation.rules:
        if rule.id != rule_id:
            rules.append(rule)
            continue
        found = True
        rules.append(
            replace(
                rule,
                enabled=body.get("enabled", rule.enabled),
                shadow=body.get("shadow", rule.shadow),
            )
        )
    if not found:
        raise KeyError(rule_id)
    text = serialize_rules(rules)
    parse_rules(text)
    path = request.app["config"].data_dir / "rules.toml"
    temporary = path.with_suffix(".toml.tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)
    automation.reload()
    await request.app["events"].emit("configuration_changed", source="settings")
    return await get_automation_rules(request)


async def automation_dry_run(request: web.Request) -> web.Response:
    body = await request.json()
    sequence = int(body.get("event_seq") or 0)
    rows = await request.app["history"].events(after_seq=max(0, sequence - 1), limit=1)
    if not rows or int(rows[0]["seq"]) != sequence:
        raise KeyError(sequence)
    row = rows[0]
    event = MuxEvent(
        float(row["ts"]),
        row.get("session_id"),
        str(row["source"]),
        str(row["type"]),
        row["payload"],
        int(row["seq"]),
    )
    session = request.app["sessions"].sessions.get(row.get("session_id") or "")
    normalized = normalize_event(
        event,
        session.record if session else None,
        attended=bool(session and session.subscribers),
    )
    supplied = body.get("text")
    rules = parse_rules(str(supplied), source="dry-run") if supplied is not None else None
    reports = await request.app["automation"].evaluate(normalized, rules=rules, dry_run=True)
    return json_response({"event": normalized.snapshot(), "reports": reports})


async def automation_dashboard(request: web.Request) -> web.Response:
    store: AutomationStore = request.app["automation_store"]
    return json_response(
        {
            **await store.dashboard(),
            "engine": request.app["automation"].status(),
            "provider": await _provider_status(request),
            "recent_firings": await store.firings(limit=25),
            "recent_action_results": await store.action_results(limit=50),
            "recent_observer_calls": await store.observer_calls(limit=50),
            "recent_annotations": await store.annotations(limit=25),
        }
    )


async def automation_firings(request: web.Request) -> web.Response:
    return json_response(
        {
            "items": await request.app["automation_store"].firings(
                rule_id=request.query.get("rule"),
                limit=int(request.query.get("limit", 200)),
            )
        }
    )


async def list_annotations(request: web.Request) -> web.Response:
    return json_response(
        {
            "items": await request.app["automation_store"].annotations(
                agent_run_id=request.query.get("agent_run_id"),
                tag=request.query.get("tag"),
                limit=int(request.query.get("limit", 200)),
            )
        }
    )


async def _provider_status(request: web.Request) -> dict[str, Any]:
    return {
        "secret": request.app["secret_store"].status("openrouter_api_key"),
        "models": await request.app["automation_store"].model_cache(),
        "origin": "https://openrouter.ai/api/v1",
        "cheap_model": request.app["config"].openrouter_cheap_model,
        "standard_model": request.app["config"].openrouter_standard_model,
    }


async def automation_provider_status(request: web.Request) -> web.Response:
    return json_response(await _provider_status(request))


async def automation_provider_key(request: web.Request) -> web.Response:
    body = await request.json()
    operation = str(body.get("operation") or "test")
    value = body.get("key")
    store: PlatformSecretStore = request.app["secret_store"]
    provider: OpenRouterClient = request.app["openrouter"]
    try:
        if operation == "test":
            result = await provider.test_key(str(value) if value else None)
            return json_response({**result, "status": store.status("openrouter_api_key")})
        if operation in {"set", "replace"}:
            if not isinstance(value, str) or not value.strip():
                raise ValueError("key is required")
            if body.get("test", True):
                await provider.test_key(value)
            store.set("openrouter_api_key", value)
            return json_response({"ok": True, "status": store.status("openrouter_api_key")})
        if operation == "clear":
            store.clear("openrouter_api_key")
            return json_response({"ok": True, "status": store.status("openrouter_api_key")})
        raise ValueError("operation must be test, set, replace, or clear")
    except (OpenRouterError, SecretStoreError) as exc:
        return json_response({"error": str(exc), "status": store.status("openrouter_api_key")}, 422)


async def refresh_automation_models(request: web.Request) -> web.Response:
    store: AutomationStore = request.app["automation_store"]
    try:
        models = await request.app["openrouter"].models()
        await store.cache_models(models)
    except OpenRouterError as exc:
        await store.record_model_error(str(exc))
        return json_response({"error": str(exc), **await store.model_cache()}, 422)
    return json_response(await store.model_cache())


async def automation_notifications(request: web.Request) -> web.Response:
    return json_response(
        {
            "items": await request.app["automation_store"].notifications(
                unread=request.query.get("unread") == "1",
                limit=int(request.query.get("limit", 200)),
            )
        }
    )


async def patch_automation_notification(request: web.Request) -> web.Response:
    body = await request.json()
    changed = await request.app["automation_store"].mark_notification(
        request.match_info["notification_id"], bool(body.get("read", True))
    )
    if not changed:
        raise KeyError(request.match_info["notification_id"])
    return json_response({"ok": True})


async def patch_automation_notifications(request: web.Request) -> web.Response:
    """Bulk read/unread over the whole attention inbox (the drawer's "clear all")."""
    body = await request.json()
    changed = await request.app["automation_store"].mark_all_notifications(
        bool(body.get("read", True))
    )
    return json_response({"ok": True, "changed": changed})


async def list_lineage(request: web.Request) -> web.Response:
    return json_response(
        {"items": await request.app["automation_store"].lineage(request.query.get("run_id"))}
    )


async def create_lineage(request: web.Request) -> web.Response:
    body = await request.json()
    parent = str(body.get("parent_run_id") or "")
    child = str(body.get("child_run_id") or "")
    relation = str(body.get("relation") or "")
    if not parent or not child or relation not in {"resume", "handoff", "continuation", "review"}:
        raise ValueError("parent_run_id, child_run_id, and a valid relation are required")
    return json_response(
        await request.app["automation_store"].add_lineage(
            parent, child, relation, body.get("metadata")
        ),
        201,
    )


async def absence_report(request: web.Request) -> web.Response:
    since = float(request.query["since"]) if request.query.get("since") else None
    return json_response(await request.app["fleet"].absence_report(since))


async def injection_safety(request: web.Request) -> web.Response:
    return json_response(request.app["fleet"].injection_safety())


async def second_opinion(request: web.Request) -> web.Response:
    source_id = request.match_info["sid"]
    history: HistoryIndex = request.app["history"]
    source = await history.history_entry(source_id)
    if not source:
        live = next(
            (
                item.record
                for item in request.app["sessions"].sessions.values()
                if item.record.agent_run_id == source_id or item.record.id == source_id
            ),
            None,
        )
        if live and live.agent_run_id:
            source_id = live.agent_run_id
            source = await history.history_entry(source_id)
    if not source or not has_observable_transcript(source.get("backend")):
        raise KeyError(source_id)
    body = await request.json()
    backend = str(body.get("backend") or ("codex" if source["backend"] == "claude" else "claude"))
    if not has_observable_transcript(backend) or backend == source["backend"]:
        raise ValueError("second opinion backend must be the other supported agent")
    annotations = await request.app["automation_store"].annotations(
        agent_run_id=source_id, limit=50
    )
    summaries = [
        str(item["content"])
        for item in reversed(annotations)
        if item["tag"] in {"turn-summary", "summary"}
    ][-12:]
    worktree_context = await _review_worktree_context(str(source["cwd"]))
    prompt = (
        f"Review the work from a {source['backend']} agent run in {source['cwd']}.\n"
        "Act as an independent reviewer. Inspect the current working tree and identify "
        "incorrect changes, missing tests, regressions, or unsupported completion claims. "
        "Do not assume the prior agent was correct.\n"
    )
    if summaries:
        prompt += "\nPrior run summaries:\n- " + "\n- ".join(summaries)
    if worktree_context:
        prompt += f"\n\nCurrent bounded worktree context:\n```text\n{worktree_context}\n```"
    if body.get("instructions"):
        prompt += f"\n\nUser review instructions:\n{str(body['instructions'])[:4000]}"
    preview = {
        "source_run_id": source_id,
        "source_backend": source["backend"],
        "backend": backend,
        "cwd": source["cwd"],
        "worktree_context": worktree_context,
        "prompt": prompt,
        "relation": "review",
    }
    preview_token = hashlib.sha256(
        json.dumps(preview, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    preview["preview_token"] = preview_token
    if not body.get("confirm"):
        return json_response({"preview": preview, "spawned": False})
    if not secrets.compare_digest(str(body.get("preview_token") or ""), preview_token):
        raise ValueError("review confirmation requires the current preview token")
    target_project = str(body.get("project_id") or source.get("project_id") or "")
    session = await _spawn_from_body(
        request.app,
        {
            "backend": backend,
            "name": body.get("name") or f"{backend} review · {source['name']}",
            "project_id": target_project,
            "argv": [prompt],
        },
    )
    project_record = request.app["projects"].projects[target_project]
    next_layout = attach_terminal(
        project_record.layout,
        session.record.id,
        target_id=body.get("target_session_id"),
        direction=body.get("direction"),
    )
    try:
        await request.app["projects"].update(
            target_project,
            layout=next_layout,
            layout_revision=project_record.layout_revision,
        )
    except Exception:
        await request.app["sessions"].stop(session.record.id)
        request.app["sessions"].sessions.pop(session.record.id, None)
        raise
    lineage = await request.app["automation_store"].add_lineage(
        source_id,
        session.record.agent_run_id or session.record.id,
        "review",
        {
            "prompt_reviewed": True,
            "preview_token": preview_token,
            "source_backend": source["backend"],
        },
    )
    return json_response(
        {
            "preview": preview,
            "spawned": True,
            "session": session.record.snapshot(),
            "lineage": lineage,
        },
        201,
    )


async def _review_worktree_context(cwd: str) -> str:
    """Return bounded, reviewable Git evidence without persisting a repository diff."""
    (status_code, status), (diff_code, diff) = await asyncio.gather(
        _git(cwd, "status", "--short", "--branch", "--untracked-files=normal"),
        _git(cwd, "diff", "--stat", "--", "."),
    )
    sections: list[str] = []
    if status_code == 0 and status:
        sections.append("STATUS\n" + status[:6000])
    if diff_code == 0 and diff:
        sections.append("DIFF STAT\n" + diff[:4000])
    return "\n\n".join(sections)[:10_000]


async def export_handoff(request: web.Request) -> web.Response:
    run_id = request.match_info["sid"]
    row = await request.app["history"].history_entry(run_id)
    if not row or not has_observable_transcript(row.get("backend")):
        raise KeyError(run_id)
    annotations = await request.app["automation_store"].annotations(agent_run_id=run_id, limit=200)
    summaries = [
        item
        for item in reversed(annotations)
        if item["tag"] in {"turn-summary", "summary", "handoff-suggestion"}
    ]
    history_id = str(row["id"])
    native_id = str(row.get("native_id") or "").strip()
    transcript_path = str(row.get("transcript_path") or "").strip()
    escaped_transcript_path = transcript_path.replace("`", "\\`")
    transcript_available = bool(transcript_path and Path(transcript_path).is_file())
    lines = [
        f"# Handoff: {row['name']}",
        "",
        f"- Backend: {row['backend']}",
        f"- Working directory: {row['cwd']}",
        f"- swe-mux history ID: {history_id}",
        f"- Provider session ID: {native_id or 'unavailable'}",
        "",
        "## Native transcript",
        "",
        (
            f"`{escaped_transcript_path}`"
            if escaped_transcript_path
            else "Unavailable in the current swe-mux history index."
        ),
        "",
        (
            "Read this provider-native file directly to review the complete conversation. "
            "The summary in this handoff is not a transcript copy."
            if transcript_available
            else (
                "This recorded provider-native path is not currently available. Use the provider "
                "session ID to locate the conversation."
                if escaped_transcript_path
                else "Use the provider session ID to locate the native conversation when available."
            )
        ),
        "",
        "## Progress",
        "",
    ]
    lines.extend(f"- {item['content']}" for item in summaries)
    if not summaries:
        lines.append("- No observer summaries are available yet.")
    lines.extend(
        [
            "",
            "## Provenance",
            "",
            "Generated from read-only swe-mux annotations. Review before using it as context.",
        ]
    )
    return json_response({"run_id": history_id, "markdown": "\n".join(lines) + "\n"})


async def workload_telemetry(request: web.Request) -> web.Response:
    since = float(request.query.get("since", 0))
    result = await request.app["history"].workload_telemetry(since)
    result["observer_spend"] = await request.app["automation_store"].spend()
    provider_costs: list[dict[str, Any]] = []
    usage = request.app.get("usage")
    providers = (usage.cache.get("providers") or {}) if usage else {}
    for backend, payload in providers.items():
        for row in payload.get("models") or []:
            provider_costs.append(
                {
                    "backend": backend,
                    "model": row.get("model") or "unknown",
                    "tokens": int(row.get("total_tokens") or 0),
                    "cost_usd": float(row.get("cost_usd") or 0),
                    "cost_is_estimate": bool(row.get("cost_is_estimate", True)),
                    "attribution": "ccusage_provider_model_aggregate",
                }
            )
    result["provider_cost_dimensions"] = provider_costs
    result["cost_note"] = (
        "ccusage costs are backend/model aggregates and are not attributed to individual runs"
    )
    return json_response(result)


async def list_experiences(request: web.Request) -> web.Response:
    return json_response(
        {
            "items": await request.app["automation_store"].experiences(
                query=request.query.get("q", ""),
                project_scope_id=request.query.get("project_scope_id"),
                limit=int(request.query.get("limit", 100)),
            ),
            "advisory_only": True,
        }
    )


async def list_observer_batches(request: web.Request) -> web.Response:
    return json_response(
        {
            "items": await request.app["automation_store"].batches(
                int(request.query.get("limit", 50))
            )
        }
    )


async def create_observer_batch(request: web.Request) -> web.Response:
    body = await request.json()
    kind = str(body.get("kind") or "")
    allowed = {"experience", "procedure", "doc-drift", "convention", "regression"}
    if kind not in allowed:
        raise ValueError(f"kind must be one of {', '.join(sorted(allowed))}")
    run_ids = body.get("run_ids")
    if (
        not isinstance(run_ids, list)
        or not 1 <= len(run_ids) <= 25
        or not all(isinstance(item, str) for item in run_ids)
    ):
        raise ValueError("run_ids must select between 1 and 25 agent runs")
    rows: list[dict[str, Any]] = []
    for identity in run_ids:
        row = await request.app["history"].history_entry(identity)
        if (
            not row
            or not has_observable_transcript(row.get("backend"))
            or not row.get("exited_at")
            or not row.get("transcript_path")
        ):
            raise ValueError(f"batch run is not an ended agent transcript: {identity}")
        rows.append(row)
    estimate = {
        "calls": len(rows),
        "maximum_input_tokens": len(rows) * request.app["config"].automation_max_input_tokens,
        "maximum_output_tokens": len(rows) * request.app["config"].automation_max_output_tokens,
        "repository_mutation": False,
    }
    preview_token = hashlib.sha256(
        json.dumps(
            {"kind": kind, "run_ids": run_ids, "estimate": estimate},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    if not body.get("confirm"):
        return json_response(
            {
                "preview": True,
                "preview_token": preview_token,
                "kind": kind,
                "runs": run_ids,
                "estimate": estimate,
            }
        )
    if not secrets.compare_digest(str(body.get("preview_token") or ""), preview_token):
        raise ValueError("batch confirmation requires the current preview token")
    if not request.app["config"].automation_enabled:
        raise ValueError("automation kill switch is off")
    batch_id = await request.app["automation_store"].create_batch(kind, run_ids)
    task = asyncio.create_task(
        _run_observer_batch(request.app, batch_id, kind, rows),
        name=f"observer-batch-{batch_id}",
    )
    request.app["automation_tasks"].add(task)
    task.add_done_callback(request.app["automation_tasks"].discard)
    return json_response({"id": batch_id, "status": "running", "estimate": estimate}, 202)


async def _run_observer_batch(
    app: web.Application, batch_id: str, kind: str, rows: list[dict[str, Any]]
) -> None:
    store: AutomationStore = app["automation_store"]
    config: Config = app["config"]
    model = config.openrouter_standard_model or config.openrouter_cheap_model
    results: list[dict[str, Any]] = []
    calls = tokens = 0
    cost = 0.0
    error: str | None = None
    if not model:
        await store.finish_batch(
            batch_id,
            status="failed",
            preview=[],
            calls=0,
            tokens=0,
            cost_usd=0,
            error="no OpenRouter standard or cheap model is configured",
        )
        return
    schema_name = "experience_v1" if kind == "experience" else "summary_v1"
    prompts = {
        "experience": (
            "Extract one concrete error and its demonstrated resolution. If no resolution "
            "is demonstrated, state that clearly. Return only the schema."
        ),
        "procedure": "Summarize one repeatable procedure demonstrated by this run.",
        "doc-drift": "Identify a plausible documentation drift candidate; do not edit files.",
        "convention": "Summarize one project convention evidenced by this run.",
        "regression": "Summarize one concrete regression-test candidate from this run.",
    }
    try:
        for row in rows:
            spend = await store.spend()
            rule_id = f"batch.{kind}"
            rule_spend = await store.spend(rule_id=rule_id)
            if (
                spend["tokens"] >= config.automation_daily_token_budget
                or spend["cost_usd"] >= config.automation_daily_budget_usd
            ):
                raise ValueError("global daily observer budget is exhausted")
            if (
                rule_spend["tokens"] >= config.automation_rule_daily_token_budget
                or rule_spend["cost_usd"] >= config.automation_rule_daily_budget_usd
            ):
                raise ValueError("batch observer rule budget is exhausted")
            hour_ago = time.time() - 3600
            if await store.observer_call_count(hour_ago) >= config.automation_hourly_call_cap:
                raise ValueError("global hourly observer call cap is exhausted")
            if (
                await store.observer_call_count(hour_ago, rule_id=rule_id)
                >= config.automation_rule_hourly_call_cap
            ):
                raise ValueError("batch observer hourly call cap is exhausted")
            path = Path(str(row["transcript_path"]))
            transcript = await app["automation"].slices.build(
                path,
                str(row["backend"]),
                "last_n_messages",
                max_messages=24,
                max_bytes=min(config.automation_max_input_tokens * 4, 512 * 1024),
            )
            input_text = transcript.render()
            call_id = await store.observer_started(
                firing_id=batch_id,
                rule_id=rule_id,
                model=model,
                input_hash=transcript.input_hash,
                input_bytes=transcript.bytes,
            )
            try:
                completion = await app["openrouter"].complete_json(
                    model=model,
                    messages=[
                        {"role": "system", "content": prompts[kind]},
                        {"role": "user", "content": input_text},
                    ],
                    schema_name=schema_name,
                    schema=OBSERVER_SCHEMAS[schema_name],
                    max_tokens=config.automation_max_output_tokens,
                )
                validate_observer_result(completion.value, schema_name)
                await store.observer_finished(
                    call_id,
                    status="completed",
                    resolved_model=completion.resolved_model,
                    generation_id=completion.generation_id,
                    input_tokens=completion.input_tokens,
                    output_tokens=completion.output_tokens,
                    cost_usd=completion.cost_usd,
                    latency_ms=completion.latency_ms,
                )
                await store.add_spend(
                    rule_id=rule_id,
                    model=completion.resolved_model,
                    input_tokens=completion.input_tokens,
                    output_tokens=completion.output_tokens,
                    cost_usd=completion.cost_usd or 0,
                    call_id=call_id,
                )
            except Exception as exc:
                await store.observer_finished(call_id, status="failed", error=str(exc)[:1000])
                results.append({"run_id": row["id"], "error": str(exc)})
                continue
            calls += 1
            tokens += completion.input_tokens + completion.output_tokens
            cost += completion.cost_usd or 0
            result = {"run_id": row["id"], "result": completion.value}
            results.append(result)
            if kind == "experience":
                await store.add_experience(
                    project_scope_id=row.get("project_scope_id"),
                    backend=str(row["backend"]),
                    error=str(completion.value["error"]),
                    resolution=str(completion.value["resolution"]),
                    source_run_id=str(row["id"]),
                    confidence=float(completion.value["confidence"]),
                )
    except Exception as exc:
        error = str(exc)
    await store.finish_batch(
        batch_id,
        status="failed" if error else "completed",
        preview=results,
        calls=calls,
        tokens=tokens,
        cost_usd=cost,
        error=error,
    )


async def list_profiles(request: web.Request) -> web.Response:
    return json_response(profile_payload(request.app["config"]))


def _config_identity(request: web.Request, project_id: str) -> ProjectIdentity | None:
    """Registered identity for an explicit `project_id`, when the caller named one.

    The route is cwd-addressed for the Git-scope path, but the per-Project
    settings editor always addresses a registered Project. Naming it keeps a
    Project registered inside a larger worktree from editing the enclosing
    worktree's `.swe-mux/config.toml`.
    """
    if not project_id:
        return None
    project = request.app["projects"].projects.get(project_id)
    if not project:
        raise ValueError("unknown project")
    return _registered_identity(project)


async def get_project_config(request: web.Request) -> web.Response:
    identity = _config_identity(request, request.query.get("project_id") or "")
    return json_response(
        await read_project_config(
            request.query.get("cwd") or str(Path.cwd()),
            project=identity,
        )
    )


async def put_project_config(request: web.Request) -> web.Response:
    body = await request.json()
    values = dict(body.get("values") or {})
    identity = _config_identity(request, str(body.get("project_id") or ""))
    project_cwd = Path(str(body.get("cwd") or Path.cwd())).resolve()
    if values.get("default_shell_profile"):
        try:
            resolve_profile(
                request.app["config"], str(values["default_shell_profile"]), project_cwd
            )
        except ValueError as exc:
            raise ValueError({"default_shell_profile": str(exc)}) from exc
    try:
        result = await write_project_config(
            str(project_cwd),
            values,
            str(body.get("revision") or "missing"),
            project=identity,
        )
    except ValueError as exc:
        if "changed externally" in str(exc):
            return json_response({"error": str(exc), "code": "revision_conflict"}, 409)
        raise
    project = await resolve_project(result["project"]["root"])
    await request.app["history"].register_project_scope(project)
    await request.app["events"].emit(
        "project_configuration_changed", project_id=result["project"]["id"]
    )
    return json_response(result)


def _prompt_scope(request: web.Request) -> PromptScope:
    value = request.match_info.get("scope") or ""
    if value not in {"global", "project"}:
        raise ValueError("prompt scope must be global or project")
    return cast(PromptScope, value)


def _prompt_project(request: web.Request, body: dict[str, Any] | None = None):  # type: ignore[no-untyped-def]
    project_id = str((body or {}).get("project_id") or request.query.get("project_id") or "")
    if not project_id:
        return None
    project = request.app["projects"].projects.get(project_id)
    if project is None:
        raise ValueError("unknown project")
    return project


async def list_prompts(request: web.Request) -> web.Response:
    return json_response(request.app["prompt_library"].list(_prompt_project(request)))


async def create_prompt(request: web.Request) -> web.Response:
    body = await request.json()
    scope_value = str(body.get("scope") or "")
    if scope_value not in {"global", "project"}:
        raise ValueError({"scope": "must be global or project"})
    scope = cast(PromptScope, scope_value)
    item = request.app["prompt_library"].create(scope, body, _prompt_project(request, body))
    await request.app["events"].emit(
        "prompt_template_changed", source="user", operation="created", template_id=item["id"]
    )
    return json_response(item, 201)


async def put_prompt(request: web.Request) -> web.Response:
    body = await request.json()
    try:
        item = request.app["prompt_library"].update(
            _prompt_scope(request),
            request.match_info["template_id"],
            body,
            str(body.get("revision") or ""),
            _prompt_project(request, body),
        )
    except ValueError as exc:
        if "changed externally" in str(exc):
            return json_response({"error": str(exc), "code": "revision_conflict"}, 409)
        raise
    await request.app["events"].emit(
        "prompt_template_changed", source="user", operation="updated", template_id=item["id"]
    )
    return json_response(item)


async def delete_prompt(request: web.Request) -> web.Response:
    body = await request.json()
    try:
        request.app["prompt_library"].delete(
            _prompt_scope(request),
            request.match_info["template_id"],
            str(body.get("revision") or ""),
            _prompt_project(request, body),
        )
    except ValueError as exc:
        if "changed externally" in str(exc):
            return json_response({"error": str(exc), "code": "revision_conflict"}, 409)
        raise
    await request.app["events"].emit(
        "prompt_template_changed",
        source="user",
        operation="deleted",
        template_id=request.match_info["template_id"],
    )
    return json_response({"ok": True})


async def use_prompt(request: web.Request) -> web.Response:
    project = _prompt_project(request, await request.json())
    del project
    key = f"{_prompt_scope(request)}:{request.match_info['template_id']}"
    return json_response(request.app["prompt_library"].record_use(key))


async def favorite_prompt(request: web.Request) -> web.Response:
    body = await request.json()
    _prompt_project(request, body)
    key = f"{_prompt_scope(request)}:{request.match_info['template_id']}"
    return json_response(
        request.app["prompt_library"].set_favorite(key, bool(body.get("favorite")))
    )


def _registered_identity(project) -> ProjectIdentity:  # type: ignore[no-untyped-def]
    """Identity for an explicitly registered Project.

    Once a route has resolved an explicit Project, its canonical root is
    authoritative. Letting a Project-resource helper re-run Git discovery on that
    root silently retargets a Project registered inside a larger worktree to the
    enclosing toplevel, bleeding notes, config, and observations across Projects.
    """
    return ProjectIdentity(project.id, project.name, project.root, "registered")


def _notes_project(request: web.Request):  # type: ignore[no-untyped-def]
    project = request.app["projects"].projects.get(request.match_info["project_id"])
    if not project:
        raise ValueError("unknown project")
    return project


def _storage_note_id(project, note_id: str) -> str:  # type: ignore[no-untyped-def]
    # The initial note has historically used the Project id in layout resources.
    # Keep that stable while storing it at the existing `project.md` path.
    return "project" if note_id == project.id else note_id


def _global_note_id(request: web.Request) -> str:
    note_id = request.match_info["note_id"]
    if note_id != GLOBAL_SCRATCHPAD_ID:
        raise ValueError("unknown global note")
    return note_id


async def get_global_note(request: web.Request) -> web.Response:
    note_id = _global_note_id(request)
    note = await read_global_note(
        request.app["config"].data_dir,
        note_id,
        default_title="Scratchpad",
    )
    return json_response(note)


async def put_global_note(request: web.Request) -> web.Response:
    note_id = _global_note_id(request)
    body = await request.json()
    if not isinstance(body, dict):
        raise ValueError("note request body must be an object")
    try:
        result = await write_global_note(
            request.app["config"].data_dir,
            note_id,
            str(body.get("markdown") or ""),
            str(body.get("revision") or "missing"),
            title="Scratchpad",
        )
    except ValueError as exc:
        if "changed externally" in str(exc):
            return json_response({"error": str(exc), "code": "revision_conflict"}, 409)
        raise
    log.info(
        "global note saved note_id=%s revision=%s bytes=%d",
        note_id,
        result["revision"],
        result["bytes"],
    )
    await request.app["events"].emit(
        "note_changed",
        source="user",
        scope="global",
        note_id=note_id,
        revision=result["revision"],
    )
    return json_response(result)


async def _legacy_note_titles(request: web.Request, project) -> dict[str, str]:  # type: ignore[no-untyped-def]
    titles: dict[str, str] = {}
    if "history" in request.app:
        owners = await request.app["history"].note_owner_labels(project.id)
        titles.update(
            {
                str(note_id): str(owner.get("name") or note_id)
                for note_id, owner in owners.items()
            }
        )
    if "sessions" in request.app:
        for session in request.app["sessions"].sessions.values():
            if session.record.project_id == project.id:
                titles[session.record.id] = session.record.name
    return titles


async def _project_note_items(request: web.Request, project) -> list[dict[str, Any]]:  # type: ignore[no-untyped-def]
    titles = await _legacy_note_titles(request, project)
    return await asyncio.to_thread(
        project_note_summaries,
        project.root,
        default_note_id=project.id,
        default_title=f"{project.name} notes",
        legacy_titles=titles,
    )


async def list_notes(request: web.Request) -> web.Response:
    manager: ProjectManager = request.app["projects"]
    requested = request.query.get("project_id") or ""
    projects = [
        project
        for project in manager.ordered_projects()
        if not requested or project.id == requested
    ]
    if requested and not projects:
        raise ValueError("unknown project")
    items: list[dict[str, Any]] = []
    for project in projects:
        for summary in await _project_note_items(request, project):
            items.append(
                {**summary, "project_id": project.id, "project_name": project.name}
            )
    items.sort(key=lambda item: float(item["updated_at"]), reverse=True)
    return json_response({"items": items})


async def create_project_note(request: web.Request) -> web.Response:
    project = _notes_project(request)
    body = await request.json()
    if not isinstance(body, dict):
        raise ValueError("note request body must be an object")
    result = await create_note(
        project.root,
        str(body.get("title") or ""),
        project=_registered_identity(project),
    )
    result.update({"project_id": project.id, "project_name": project.name})
    log.info(
        "project note created project_id=%s note_id=%s title=%r",
        project.id,
        result["id"],
        result["title"],
    )
    await request.app["events"].emit(
        "note_changed",
        source="user",
        scope="project",
        project_id=project.id,
        note_id=result["id"],
        revision=result["revision"],
    )
    return json_response(result, 201)


async def get_note(request: web.Request) -> web.Response:
    project = _notes_project(request)
    note_id = request.match_info["note_id"]
    await _project_note_items(request, project)
    storage_id = _storage_note_id(project, note_id)
    note = await read_note(
        project.root,
        storage_id,
        default_title=f"{project.name} notes" if storage_id == "project" else "Untitled note",
        project=_registered_identity(project),
    )
    if not note["exists"]:
        raise ValueError("unknown note")
    note.update(
        {"id": note_id, "project_id": project.id, "project_name": project.name}
    )
    return json_response(note)


async def _write_project_note(request: web.Request, *, title_only: bool = False) -> web.Response:
    project = _notes_project(request)
    note_id = request.match_info["note_id"]
    await _project_note_items(request, project)
    storage_id = _storage_note_id(project, note_id)
    body = await request.json()
    if not isinstance(body, dict):
        raise ValueError("note request body must be an object")
    current = await read_note(
        project.root,
        storage_id,
        default_title=f"{project.name} notes" if storage_id == "project" else "Untitled note",
        project=_registered_identity(project),
    )
    if not current["exists"]:
        raise ValueError("unknown note")
    try:
        result = await write_note(
            project.root,
            storage_id,
            str(current["markdown"] if title_only else body.get("markdown") or ""),
            str(body.get("revision") or "missing"),
            title=str(body.get("title") or "") if title_only else str(current["title"]),
            default_title=f"{project.name} notes" if storage_id == "project" else "Untitled note",
            project=_registered_identity(project),
        )
    except ValueError as exc:
        if "changed externally" in str(exc):
            return json_response({"error": str(exc), "code": "revision_conflict"}, 409)
        raise
    result.update(
        {"id": note_id, "project_id": project.id, "project_name": project.name}
    )
    log.info(
        "project note %s project_id=%s note_id=%s revision=%s",
        "renamed" if title_only else "saved",
        project.id,
        note_id,
        result["revision"],
    )
    await request.app["events"].emit(
        "note_changed",
        source="user",
        scope="project",
        project_id=project.id,
        note_id=note_id,
        revision=result["revision"],
    )
    return json_response(result)


async def put_note(request: web.Request) -> web.Response:
    return await _write_project_note(request)


async def patch_note(request: web.Request) -> web.Response:
    return await _write_project_note(request, title_only=True)


async def delete_project_note(request: web.Request) -> web.Response:
    project = _notes_project(request)
    note_id = request.match_info["note_id"]
    await _project_note_items(request, project)
    body = await request.json()
    if not isinstance(body, dict):
        raise ValueError("note request body must be an object")
    try:
        result = await delete_note(
            project.root,
            _storage_note_id(project, note_id),
            str(body.get("revision") or "missing"),
            project=_registered_identity(project),
        )
    except ValueError as exc:
        if "changed externally" in str(exc):
            return json_response({"error": str(exc), "code": "revision_conflict"}, 409)
        raise
    log.info(
        "project note deleted project_id=%s note_id=%s bytes=%d",
        project.id,
        note_id,
        result["bytes"],
    )
    await request.app["events"].emit(
        "note_changed",
        source="user",
        scope="project",
        project_id=project.id,
        note_id=note_id,
        revision="missing",
    )
    return json_response({"deleted": True, "project_id": project.id, "note_id": note_id})


def _observations_project(request: web.Request):  # type: ignore[no-untyped-def]
    project = request.app["projects"].projects.get(request.match_info["project_id"])
    if not project:
        raise ValueError("unknown project")
    return project


async def get_observations(request: web.Request) -> web.Response:
    project = _observations_project(request)
    result = await read_observations(project.root, project=_registered_identity(project))
    result.update({"project_id": project.id, "project_name": project.name})
    return json_response(result)


async def post_observation(request: web.Request) -> web.Response:
    project = _observations_project(request)
    body = await request.json()
    try:
        result = await append_observation(
            project.root, str(body.get("body") or ""), project=_registered_identity(project)
        )
    except ObservationsUnreadableError as exc:
        # Refusing beats "read as empty, then clobber": the file holds the user's
        # own notes and the next append would be the thing that destroys them.
        return json_response({"error": str(exc), "code": "observations_unreadable"}, 409)
    result.update({"project_id": project.id, "project_name": project.name})
    return json_response(result)


async def put_observations(request: web.Request) -> web.Response:
    project = _observations_project(request)
    body = await request.json()
    observations = body.get("observations")
    if not isinstance(observations, list):
        raise ValueError("observations must be a list")
    try:
        result = await write_observations(
            project.root,
            observations,
            str(body.get("revision") or "missing"),
            project=_registered_identity(project),
        )
    except ObservationsUnreadableError as exc:
        return json_response({"error": str(exc), "code": "observations_unreadable"}, 409)
    except ValueError as exc:
        if "changed externally" in str(exc):
            return json_response({"error": str(exc), "code": "revision_conflict"}, 409)
        raise
    result.update({"project_id": project.id, "project_name": project.name})
    return json_response(result)


async def decide_observation_request(request: web.Request) -> web.Response:
    """Approve or dismiss a drafted `mux.requestSpawn` (Phase 5, CP §7.2).

    Approval is the human act that creates the session — the agent never held
    spawn authority, it only asked. The prompt travels as ``seed_text`` through
    the ordinary spawn path, so nothing about the new session is special.
    """
    project = _observations_project(request)
    observation_id = request.match_info["observation_id"]
    body = await request.json()
    decision = str(body.get("decision") or "").strip()
    if decision not in {"approve", "dismiss"}:
        raise ValueError("decision must be approve or dismiss")
    identity = _registered_identity(project)
    current = await read_observations(project.root, project=identity)
    if current.get("status") == "malformed":
        return json_response(
            {"error": str(current.get("error") or ""), "code": "observations_unreadable"}, 409
        )
    item = next(
        (
            entry
            for entry in current["observations"]
            if entry.get("id") == observation_id and entry.get("kind") == "spawn_request"
        ),
        None,
    )
    if item is None:
        raise ValueError("no such spawn request")
    spawn_request = dict(item.get("request") or {})
    if spawn_request.get("status") not in {None, "", "pending"}:
        return json_response(
            {
                "error": f"this request was already {spawn_request.get('status')}",
                "code": "already_decided",
            },
            409,
        )
    if decision == "dismiss":
        result = await update_observation_request(
            project.root,
            observation_id,
            {"status": "dismissed", "decided_by": _human_sender_kind(request)},
            done=True,
            project=identity,
        )
        result.update({"project_id": project.id, "project_name": project.name})
        return json_response(result)
    prompt = str(body.get("prompt") or spawn_request.get("prompt") or "")
    if not prompt.strip():
        raise ValueError("the request has no prompt to seed")
    spawn_body: dict[str, Any] = {
        "project_id": project.id,
        "backend": str(body.get("backend") or spawn_request.get("backend") or "claude"),
        "seed_text": prompt,
    }
    name = str(body.get("name") or spawn_request.get("name") or "")
    if name:
        spawn_body["name"] = name
    cwd = str(body.get("cwd") or spawn_request.get("cwd") or "")
    if cwd:
        spawn_body["cwd"] = cwd
    session = await _spawn_from_body(request.app, spawn_body)
    result = await update_observation_request(
        project.root,
        observation_id,
        {
            "status": "approved",
            "session_id": session.record.id,
            "decided_by": _human_sender_kind(request),
        },
        done=True,
        project=identity,
    )
    result.update(
        {
            "project_id": project.id,
            "project_name": project.name,
            "session": session.record.snapshot(),
        }
    )
    return json_response(result, 201)


async def get_project_automations(request: web.Request) -> web.Response:
    """The per-project control-plane opt-in state, with its dependency graph.

    The registry ships with the response deliberately: a toggle surface has to
    show *why* a consumer is unavailable ("dead-end memory needs Tier 0 and the
    scan timeline"), and a flat checkbox list cannot. `implemented` marks ids
    that are reserved but have no code behind them yet, so the UI never presents
    a placeholder as ready to switch on.
    """
    project = _observations_project(request)
    identity = _registered_identity(project)
    config = await read_project_config(project.root, project=identity)
    values = config["values"] if config["status"] in {"ready", "read-only"} else {}
    requested = {
        key: bool(value)
        for key, value in (values.get("automations") or {}).items()
        if key in AUTOMATION_REGISTRY
    }
    resolution = resolve_automation_config(requested)
    return json_response(
        {
            "project_id": project.id,
            "revision": config["revision"],
            "status": config["status"],
            "requested": requested,
            "enabled": sorted(resolution.enabled),
            "blocked": {key: list(value) for key, value in resolution.blocked.items()},
            "automations": [
                {
                    "id": automation.id,
                    "kind": automation.kind,
                    "label": automation.label,
                    "requires": list(automation.requires),
                    "implemented": automation.implemented,
                }
                for automation in sorted(AUTOMATION_REGISTRY.values(), key=lambda a: a.id)
            ],
        }
    )


async def put_project_automations(request: web.Request) -> web.Response:
    """Replace a project's opt-in table.

    Writes through the ordinary project-config path, so the file stays the source
    of truth and the revision check still guards a concurrent edit.
    """
    project = _observations_project(request)
    identity = _registered_identity(project)
    body = await request.json()
    requested = body.get("automations")
    if not isinstance(requested, dict):
        raise ValueError("automations must be a table of boolean opt-ins")
    unknown = sorted(set(requested) - set(AUTOMATION_REGISTRY))
    if unknown:
        raise ValueError(f"unknown automations: {', '.join(unknown)}")
    unimplemented = sorted(
        key
        for key, value in requested.items()
        if value and not AUTOMATION_REGISTRY[key].implemented
    )
    if unimplemented:
        # Refusing beats a toggle that reads as on and does nothing.
        return json_response(
            {
                "error": f"not implemented yet: {', '.join(unimplemented)}",
                "code": "automation_not_implemented",
            },
            409,
        )
    current = await read_project_config(project.root, project=identity)
    values = dict(current["values"]) if current["status"] != "malformed" else {}
    values["automations"] = {key: bool(value) for key, value in requested.items() if value}
    try:
        await write_project_config(
            project.root,
            values,
            str(body.get("revision") or current["revision"]),
            project=identity,
        )
    except ValueError as exc:
        if "changed externally" in str(exc):
            return json_response({"error": str(exc), "code": "revision_conflict"}, 409)
        raise
    await request.app["events"].emit("project_configuration_changed", project_id=project.id)
    return await get_project_automations(request)


async def resolve_project_scope(request: web.Request) -> web.Response:
    """Explicitly confirm/register the project containing a user-selected directory."""
    body = await request.json()
    try:
        cwd = Path(str(body.get("cwd") or "")).resolve(strict=True)
    except OSError as exc:
        raise ValueError("cwd must be an existing directory") from exc
    if not cwd.is_dir():
        raise ValueError("cwd must be an existing directory")
    project = await resolve_project(cwd)
    await request.app["history"].register_project_scope(project)
    if sid := body.get("session_id"):
        session = request.app["sessions"].sessions.get(str(sid))
        if session and session.record.runtime_cwd:
            try:
                same = Path(session.record.runtime_cwd).resolve(strict=True) == cwd
            except OSError:
                same = False
            if same:
                session.record.runtime_project_scope_id = project.id
                session.publish_update()
    return json_response(
        project.__dict__
        if hasattr(project, "__dict__")
        else {
            "id": project.id,
            "label": project.label,
            "root": project.root,
            "source": project.source,
            "repo_group_id": project.repo_group_id,
            "repo_group_label": project.repo_group_label,
        }
    )


async def list_git_projects(request: web.Request) -> web.Response:
    history: HistoryIndex = request.app["history"]
    scopes = await history.project_scopes(include_hidden=request.query.get("include_hidden") == "1")
    try:
        offset = max(0, int(request.query.get("offset", "0")))
        limit = max(1, min(500, int(request.query.get("limit", "200"))))
    except ValueError as exc:
        raise ValueError("project offset and limit must be integers") from exc
    total = len(scopes)
    scopes = scopes[offset : offset + limit]
    live = list(request.app["sessions"].sessions.values())
    for scope in scopes:
        scope["root_exists"] = Path(scope["root"]).is_dir()
        scope["live_count"] = sum(item.record.trusted_scope_id == scope["id"] for item in live)
    next_offset = offset + len(scopes)
    return json_response(
        {
            "items": scopes,
            "total": total,
            "next_offset": next_offset if next_offset < total else None,
        }
    )


async def get_project_scope(request: web.Request) -> web.Response:
    history: HistoryIndex = request.app["history"]
    scope = await history.project_scope(request.match_info["scope_id"])
    if not scope:
        raise KeyError(request.match_info["scope_id"])
    artifacts = await history.artifacts(scope["id"])
    for artifact in artifacts:
        path = (Path(scope["root"]) / artifact["relative_path"]).resolve()
        try:
            artifact["revision"] = file_revision(path.read_bytes())
        except OSError:
            artifact["revision"] = "missing"
    scope["config"] = await read_project_config(scope["root"])
    scope["detached_artifacts"] = [
        item
        for item in artifacts
        if (
            item["owner_type"] == "session"
            and not await history.history_entry(item["owner_id"])
            and item["owner_id"] not in request.app["sessions"].sessions
        )
    ]
    scope["artifacts"] = artifacts
    scope["blockers"] = await history.project_blockers(scope["id"])
    scope["sessions"] = [
        item.record.snapshot()
        for item in request.app["sessions"].sessions.values()
        if item.record.trusted_scope_id == scope["id"]
    ]
    return json_response(scope)


async def patch_project_scope(request: web.Request) -> web.Response:
    body = await request.json()
    changed = await request.app["history"].set_project_hidden(
        request.match_info["scope_id"], bool(body.get("hidden"))
    )
    if not changed:
        raise KeyError(request.match_info["scope_id"])
    return json_response(await request.app["history"].project_scope(request.match_info["scope_id"]))


async def forget_project_scope(request: web.Request) -> web.Response:
    result = await request.app["history"].forget_project_scope(request.match_info["scope_id"])
    return json_response(result, 200 if result["forgotten"] else 409)


async def list_artifacts(request: web.Request) -> web.Response:
    return json_response(
        {"items": await request.app["history"].artifacts(request.query.get("project_scope_id"))}
    )


async def transfer_artifact(request: web.Request) -> web.Response:
    history: HistoryIndex = request.app["history"]
    artifact = next(
        (a for a in await history.artifacts() if a["id"] == request.match_info["artifact_id"]), None
    )
    if not artifact:
        raise KeyError(request.match_info["artifact_id"])
    body = await request.json()
    target = await history.project_scope(str(body.get("project_scope_id") or ""))
    source = await history.project_scope(artifact["project_scope_id"])
    if not source or not target:
        raise ValueError("unknown source or target project scope")
    source_path = (Path(source["root"]) / artifact["relative_path"]).resolve()
    target_path = (Path(target["root"]) / artifact["relative_path"]).resolve()
    if not source_path.is_relative_to(
        Path(source["root"]).resolve()
    ) or not target_path.is_relative_to(Path(target["root"]).resolve()):
        raise ValueError("artifact path escapes project scope")
    action = str(body.get("action") or "keep")
    if action == "keep":
        await history.acknowledge_artifact_placement(artifact["id"], target["id"])
        artifact = next(item for item in await history.artifacts() if item["id"] == artifact["id"])
        return json_response({"artifact": artifact, "action": action})
    if not source_path.is_file() or target_path.exists():
        raise ValueError("source missing or destination already exists")
    expected_revision = str(body.get("revision") or "")
    current_revision = file_revision(source_path.read_bytes())
    if not expected_revision or expected_revision != current_revision:
        return json_response(
            {
                "error": "artifact changed externally; reload before transferring",
                "code": "revision_conflict",
                "revision": current_revision,
            },
            409,
        )
    target_path.parent.mkdir(parents=True, exist_ok=True)
    if action == "copy":
        shutil.copy2(source_path, target_path)
        copy = await history.bind_artifact(
            artifact_id=str(uuid4()),
            kind=artifact["kind"],
            owner_type=artifact["owner_type"],
            owner_id=f"{artifact['owner_id']}-copy-{uuid4().hex[:6]}",
            owner_label=f"{artifact.get('owner_label') or artifact['owner_id']} (copy)",
            project_scope_id=target["id"],
            relative_path=artifact["relative_path"],
        )
        return json_response({"artifact": copy, "action": action})
    if action != "move":
        raise ValueError("action must be keep, copy, or move")
    if (
        source_path.drive
        and target_path.drive
        and source_path.drive.casefold() != target_path.drive.casefold()
    ):
        raise ValueError("cross-volume note moves are not atomic; use Copy instead")
    shutil.move(str(source_path), str(target_path))
    await history.move_artifact_scope(artifact["id"], target["id"], artifact["relative_path"])
    return json_response(
        {
            "artifact": next(a for a in await history.artifacts() if a["id"] == artifact["id"]),
            "action": action,
        }
    )


async def list_pinned_directories(request: web.Request) -> web.Response:
    return json_response({"paths": request.app["config"].pinned_directories})


async def pin_directory(request: web.Request) -> web.Response:
    config: Config = request.app["config"]
    path = str(Path(str((await request.json()).get("path", ""))).resolve())
    if not Path(path).is_dir():
        raise ValueError({"path": "directory does not exist"})
    values = list(dict.fromkeys([*config.pinned_directories, path]))
    update_config(config, {"pinned_directories": values})
    await request.app["events"].emit("configuration_changed", source="directory_pins")
    return json_response({"paths": values})


async def unpin_directory(request: web.Request) -> web.Response:
    config: Config = request.app["config"]
    path = str(Path(str((await request.json()).get("path", ""))).resolve())
    values = [item for item in config.pinned_directories if item.casefold() != path.casefold()]
    update_config(config, {"pinned_directories": values})
    await request.app["events"].emit("configuration_changed", source="directory_pins")
    return json_response({"paths": values})


_FS_ROOTS_TTL = 10.0
_fs_roots_cache: tuple[float, list[str]] | None = None


def _probe_drive_roots() -> list[str]:
    if os.name != "nt":
        return ["/"]
    return [
        f"{letter}:\\" for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ" if Path(f"{letter}:\\").is_dir()
    ]


async def filesystem_roots(request: web.Request) -> web.Response:
    # Probe all 26 drive letters off the event loop and cache briefly: each
    # is_dir() is a blocking syscall and absent/network letters can stall for
    # hundreds of ms. `remote` stays per-request and is never cached.
    global _fs_roots_cache
    now = time.monotonic()
    if _fs_roots_cache is None or now >= _fs_roots_cache[0]:
        roots = await asyncio.to_thread(_probe_drive_roots)
        _fs_roots_cache = (now + _FS_ROOTS_TTL, roots)
    else:
        roots = _fs_roots_cache[1]
    return json_response({"roots": roots, "remote": request.remote not in {"127.0.0.1", "::1"}})


async def filesystem_list(request: web.Request) -> web.Response:
    path = Path(request.query.get("path") or Path.cwd()).resolve()
    if not path.is_dir():
        raise ValueError({"path": "directory does not exist"})
    try:
        directories = sorted(
            (item for item in path.iterdir() if item.is_dir()),
            key=lambda item: item.name.casefold(),
        )[:500]
    except PermissionError as exc:
        raise ValueError({"path": "permission denied"}) from exc
    return json_response(
        {
            "path": str(path),
            "parent": str(path.parent) if path.parent != path else None,
            "directories": [{"name": item.name, "path": str(item)} for item in directories],
        }
    )


async def list_sessions(request: web.Request) -> web.Response:
    manager: SessionManager = request.app["sessions"]
    sessions = []
    readiness = request.app["fleet"].readiness
    for session in manager.sessions.values():
        item = session.record.snapshot()
        item["_snapshot_generation"] = request.app["daemon_generation"]
        item["_snapshot_revision"] = session.revision
        item["_snapshot_enriched"] = True
        delivery = readiness.evaluate(session, record_metrics=False)
        item["delivery_readiness"] = {
            "state": delivery["delivery_state"],
            "reason": delivery["reason"],
            "authorized": False,
        }
        sessions.append(item)
    await _decorate_generated_titles(request.app, sessions)
    for field in ("project_id", "state", "backend"):
        value = request.query.get(field.removesuffix("_id") if field == "project_id" else field)
        if value:
            sessions = [s for s in sessions if s[field] == value]
    return json_response(sessions)


async def _decorate_generated_titles(app: web.Application, items: list[dict[str, Any]]) -> None:
    run_ids = {
        str(item.get("agent_run_id") or item.get("id"))
        for item in items
        if item.get("agent_run_id") or item.get("agent_visible")
    }
    if not run_ids:
        return
    annotations = await app["automation_store"].annotations(tag="title", limit=1000)
    by_run: dict[str, dict[str, Any]] = {}
    for annotation in annotations:
        run_id = str(annotation["agent_run_id"])
        if run_id in run_ids and run_id not in by_run:
            by_run[run_id] = annotation
    for item in items:
        run_id = str(item.get("agent_run_id") or item.get("id") or "")
        annotation = by_run.get(run_id)
        if annotation:
            item["generated_title"] = annotation["content"]
            item["generated_title_annotation"] = annotation


async def _spawn_from_body(app: web.Application, body: dict[str, Any]) -> Session:
    startup_started_at = time.perf_counter()
    startup_timing_ms: dict[str, float] = {}
    spec = SpawnRequest.parse(body)
    manager: SessionManager = app["sessions"]
    projects: ProjectManager = app["projects"]
    project_id = spec.project_id
    if project_id not in projects.projects:
        raise ValueError(f"unknown project: {project_id}")
    owning_project = projects.projects[project_id]
    config: Config = app["config"]
    seed_cwd = owning_project.root
    project_started_at = time.perf_counter()
    project = await resolve_project(seed_cwd)
    startup_timing_ms["project_resolution"] = round(
        (time.perf_counter() - project_started_at) * 1000, 1
    )
    config_started_at = time.perf_counter()
    project_config = await read_project_config(seed_cwd, project=project)
    startup_timing_ms["project_config"] = round((time.perf_counter() - config_started_at) * 1000, 1)
    project_values = (
        project_config["values"] if project_config["status"] in {"ready", "read-only"} else {}
    )
    if project_config["status"] == "malformed":
        await app["events"].emit(
            "project_configuration_error",
            source="project_file",
            path=project_config["path"],
            error=project_config.get("error"),
        )
    backend = (
        spec.backend
        or owning_project.default_backend
        or project_values.get("preferred_backend")
        or config.default_backend
    )
    if spec.completion_mode == "one_shot" and backend != "shell":
        raise ValueError("one-shot completion is available only for shell sessions")
    if spec.profile_id and is_agent_harness(backend):
        raise ValueError({"profile_id": "shell profiles cannot be used with agent backends"})
    # A spawn may target a subdirectory of its own project (a task that runs in
    # ./frontend); the containment check is here because this is the only layer
    # that knows which project owns the request.
    cwd = owning_project.root
    if spec.cwd:
        try:
            cwd = resolve_contained_cwd(spec.cwd, Path(owning_project.root))
        except ValueError:
            # Outside the root. Before refusing, ask git whether this is a worktree of
            # the project's own repository — parallel agent worktrees are the same
            # codebase on another branch and a session belongs in them. The git query
            # only runs on this failure path, so ordinary spawns pay nothing for it.
            cwd = resolve_listed_cwd(spec.cwd, await _listed_worktree_paths(owning_project.root))
    executable = spec.executable
    argv = list(spec.argv)
    profile_id: str | None = None
    profile_env: dict[str, str] | None = None
    if backend == "shell" and not executable:
        profile_id = (
            spec.profile_id
            or owning_project.default_profile_id
            or project_values.get("default_shell_profile")
            or config.default_shell_profile
        )
        profile_started_at = time.perf_counter()
        profile = resolve_profile(config, profile_id, Path(cwd).resolve())
        startup_timing_ms["profile_resolution"] = round(
            (time.perf_counter() - profile_started_at) * 1000, 1
        )
        executable = profile.executable
        argv = [*profile.argv, *argv]
        profile_env = profile.env
    if spec.seed_text:
        if not is_agent_harness(backend):
            raise ValueError({"seed_text": "seed prompts require an agent backend"})
        # Short bodies ride argv; over-bound ones are staged into the workspace
        # with a reader prompt (file I/O off-loop).
        argv = [*argv, await asyncio.to_thread(stage_seed_argv, cwd, spec.seed_text)]
    spawn_values: dict[str, Any] = dict(
        backend=backend,
        name=spec.name,
        cwd=cwd,
        project_id=project_id,
        exe=executable,
        args=argv,
        shell_profile_id=profile_id,
        profile_env=profile_env,
        extra_env=dict(spec.env),
        project_label=owning_project.name,
    )
    if isinstance(manager, SessionManager):
        spawn_values["project"] = project
        spawn_values["startup_started_at"] = startup_started_at
        spawn_values["startup_timing_ms"] = startup_timing_ms
    if spec.completion_mode != "interactive":
        spawn_values["completion_mode"] = spec.completion_mode
    session = await manager.spawn(**spawn_values)
    return session


async def spawn_session(request: web.Request) -> web.Response:
    session = await _spawn_from_body(request.app, await request.json())
    return json_response(session.record.snapshot(), 201)


async def get_session(request: web.Request) -> web.Response:
    return json_response(
        request.app["sessions"].resolve(request.match_info["sid"]).record.snapshot()
    )


def _query_epoch(request: web.Request, key: str) -> float | None:
    """Parse an epoch-seconds query parameter, tolerating blanks."""
    raw = request.query.get(key)
    if raw is None or not raw.strip():
        return None
    try:
        return float(raw)
    except ValueError:
        raise web.HTTPBadRequest(text=f"{key} must be epoch seconds") from None


def _live_state_log_payload(app: Any, session: Any, now: float) -> dict[str, Any]:
    """The live half of the state-log: current fields plus the in-memory rings."""
    transcript = session.transcript_path
    transcript_mtime: float | None = None
    if transcript is not None:
        try:
            transcript_mtime = transcript.stat().st_mtime
        except OSError:
            transcript_mtime = None
    try:
        pty_tail = session.scrollback.tail_bytes(SCREEN_TAIL_BYTES).decode("utf-8", "replace")
        pty_explanation = pty_tail_explain(
            pty_tail,
            backend=session.record.backend,
            osc_title=session.osc_signals.title,
            osc_progress=session.osc_signals.progress,
        )
    except (AttributeError, OSError, ValueError):
        pty_explanation = {"outcome": "unknown", "rules": []}
    raw_hook_sequences = session.observation_state.get("hook_sequences", {})
    hook_sequences = dict(raw_hook_sequences) if isinstance(raw_hook_sequences, dict) else {}
    return {
        "id": session.record.id,
        "live": True,
        "backend": session.record.backend,
        "state": session.record.state,
        "state_detail": session.record.state_detail,
        "state_source_priority": session.state_source_priority,
        "now": now,
        "last_state_change_ts": session.last_state_change_ts,
        "seconds_in_state": round(now - session.last_state_change_ts, 3),
        "last_hook_ts": session.last_hook_ts or None,
        "transcript_path": str(transcript) if transcript else None,
        "transcript_mtime": transcript_mtime,
        # Whether that path was proven or guessed. A provisional binding drives
        # state only, so a session reporting live turns with no tokens and a
        # placeholder conversation id is explained by this field and not a bug.
        "transcript_provisional": session.transcript_provisional,
        # No transcript and no hook ever: the PTY screen is the only source
        # that can move this session, which is what licenses the
        # begin/end_pty_turn watchdog pair.
        "unwitnessed": session_is_unwitnessed(session),
        "observer_restart_count": session.observer_restart_count,
        "observer_last_fault": session.observer_last_fault,
        "watchdog_recoveries": session.watchdog_recoveries,
        "hook_sequences": hook_sequences,
        "hook_sequence_duplicates": session.observation_state.get(
            "hook_sequence_duplicates", 0
        ),
        "observation_replay": session.observation_replay,
        # The one fault class that presents as a perfectly healthy session:
        # the transcript parses fine, it is just not this PTY's conversation
        # any more. Paired with the run counter, because "which conversation
        # am I looking at" is the question this endpoint exists to answer.
        "observation_stale_since": session.record.observation_stale_since,
        "observation_diagnostic": session.record.observation_diagnostic,
        # When the tailer last saw that file grow, which is what staleness is
        # actually decided on. Reported beside `transcript_mtime` because the pair
        # is the diagnosis: a frozen `transcript_mtime` next to a recent
        # `transcript_growth_ts` is a filesystem that stopped dating a live file
        # (routine for Codex rollouts on Windows), not a replaced conversation.
        "transcript_growth_ts": session.transcript_growth_ts or None,
        "agent_run_id": session.record.agent_run_id,
        "agent_run_seq": session.record.agent_run_seq,
        "native_session_id": session.record.native_session_id,
        "agent_lifecycle_id": session.agent_lifecycle_id,
        "awaiting_reason": session.record.awaiting_reason,
        "standing_activity": [activity.snapshot() for activity in session.record.standing_activity],
        # The CLI's own published state for this conversation
        # (~/.claude/sessions/<pid>.json) — corroboration only, never a
        # transition source. None until the poller matches a file.
        "cli_state": session.cli_state,
        # Last observed reading per detection-ladder layer; the flips behind
        # these values are ledgered as `layer_reading` timeline entries.
        "layer_readings": dict(session.layer_readings),
        "pty_explain": pty_explanation,
        "status_health": session.status_health(now),
        # Multi-device terminal arbitration. Non-zero rejections mean keystrokes
        # arrived from a client that had lost input ownership; non-zero denials
        # mean a background pane tried to take it back.
        "input_arbitration": {
            # The device classes the daemon believes are in use: a passive claim
            # from any other one is refused, so this is the first thing to check
            # when input ownership is not where it is expected to be.
            "active_devices": sorted(app["device_presence"].active_profiles()),
            "leading_device": app["device_presence"].leading_profile(),
            "owner_device": session.input_owner_device,
            "owner_epoch": session.input_owner_epoch,
            "attached_viewports": len(session.viewports),
            "geometry": list(session.geometry) if session.geometry else None,
            "input_rejections": session.input_rejections,
            "claim_denials": session.input_claim_denials,
            "claims": list(session.claim_log),
        },
        # Real state changes are kept separately: a busy turn emits dozens
        # of same-state tool detail updates that would otherwise evict the
        # history explaining how the session reached its current state.
        "state_changes": list(session.state_changes),
        "transitions": list(session.state_transitions),
    }


async def _post_mortem_state_log(
    app: Any, sid: str, from_ts: float | None, to_ts: float | None
) -> web.Response:
    """State-log for a session that no longer exists, from the durable store.

    The timeline table records (session_id, agent_run_id) pairs, so the mux id
    or any of its run ids (a history row's key) reaches the same rows.
    """
    store: StatusTimelineStore = app["status_timeline"]
    timeline, truncated = await store.timeline(sid, from_ts=from_ts, to_ts=to_ts)
    history_row = await app["history"].history_entry(sid)
    if not timeline and not history_row:
        raise KeyError(sid)
    runs = await store.runs_for_session(sid)
    if not runs and history_row:
        runs = await store.runs_for_session(str(history_row["id"]))
    return json_response(
        {
            "id": sid,
            "live": False,
            "history": history_row,
            "runs": runs,
            "from": from_ts,
            "to": to_ts,
            "timeline": timeline,
            "timeline_truncated": truncated,
            "timeline_sink": store.stats(),
        }
    )


async def get_session_state_log(request: web.Request) -> web.Response:
    """End-detection diagnostics: transitions, faults, watchdog and layer activity.

    With `from`/`to` (epoch seconds) the response adds a `timeline` slice from
    the durable store — the live ring is flushed first, so the slice is
    complete up to the moment of the request. For an ended session the durable
    timeline is the whole answer (post-mortem mode).
    """
    sid = request.match_info["sid"]
    from_ts = _query_epoch(request, "from")
    to_ts = _query_epoch(request, "to")
    try:
        session = request.app["sessions"].resolve(sid)
    except KeyError:
        return await _post_mortem_state_log(request.app, sid, from_ts, to_ts)
    now = time.time()
    payload = _live_state_log_payload(request.app, session, now)
    store: StatusTimelineStore = request.app["status_timeline"]
    if from_ts is not None or to_ts is not None:
        await store.flush_session(session)
        timeline, truncated = await store.timeline(session.record.id, from_ts=from_ts, to_ts=to_ts)
        payload["from"] = from_ts
        payload["to"] = to_ts
        payload["timeline"] = timeline
        payload["timeline_truncated"] = truncated
    payload["timeline_sink"] = store.stats()
    return json_response(payload)


# Bounded transcript slice per agent run in a diagnostic bundle; the timeline
# names the moments, the transcript shows what the agent was actually doing.
DIAGNOSTIC_BUNDLE_MAX_MESSAGES_PER_RUN = 200
DIAGNOSTIC_BUNDLE_DEFAULT_WINDOW_SECONDS = 3600.0


async def _bundle_transcript_slices(
    app: Any,
    run_ids: list[str],
    from_ts: float,
    to_ts: float,
) -> list[dict[str, Any]]:
    """Native-timestamped transcript records inside the window, per agent run.

    Reuses history indexing: each run id is a history row whose transcript
    path is parsed through the shared cache; messages are filtered by their
    native timestamps. Runs whose transcript is gone report that instead of
    silently vanishing from the bundle.
    """
    from .history import _message_timestamp

    slices: list[dict[str, Any]] = []
    for run_id in run_ids:
        row = await app["history"].history_entry(run_id)
        if not row:
            slices.append({"agent_run_id": run_id, "error": "no history row"})
            continue
        transcript = row.get("transcript_path")
        if not transcript or not Path(transcript).is_file():
            slices.append({"agent_run_id": run_id, "error": "native transcript is unavailable"})
            continue
        try:
            messages, _mtime_ns, _size = await asyncio.to_thread(
                parse_transcript_with_watermark, Path(transcript), str(row["backend"])
            )
        except (OSError, ValueError):
            slices.append({"agent_run_id": run_id, "error": "native transcript is unreadable"})
            continue
        in_window: list[dict[str, Any]] = []
        for message in messages:
            ts = _message_timestamp(message.get("ts"))
            if ts is None or ts < from_ts or ts > to_ts:
                continue
            in_window.append({**message, "ts_epoch": ts})
        truncated = len(in_window) > DIAGNOSTIC_BUNDLE_MAX_MESSAGES_PER_RUN
        slices.append(
            {
                "agent_run_id": run_id,
                "transcript_path": str(transcript),
                "messages": in_window[:DIAGNOSTIC_BUNDLE_MAX_MESSAGES_PER_RUN],
                "truncated": truncated,
            }
        )
    return slices


async def get_session_diagnostic_bundle(request: web.Request) -> web.Response:
    """One-fetch investigation artifact for a status incident.

    Packages, for the requested window (default: the last hour): the durable
    detection timeline, the current state-log fields (live sessions), the
    fleet status-health aggregate, and the transcript records whose native
    timestamps fall inside the window. STATUS_INCIDENT_RUNBOOK.md documents
    how to read it.
    """
    from .session import fleet_status_health

    sid = request.match_info["sid"]
    now = time.time()
    to_ts = _query_epoch(request, "to")
    from_ts = _query_epoch(request, "from")
    if to_ts is None:
        to_ts = now
    if from_ts is None:
        from_ts = to_ts - DIAGNOSTIC_BUNDLE_DEFAULT_WINDOW_SECONDS
    store: StatusTimelineStore = request.app["status_timeline"]
    session = None
    try:
        session = request.app["sessions"].resolve(sid)
    except KeyError:
        pass
    state_log: dict[str, Any] | None = None
    identity = sid
    if session is not None:
        await store.flush_session(session)
        state_log = _live_state_log_payload(request.app, session, now)
        identity = session.record.id
    timeline, truncated = await store.timeline(identity, from_ts=from_ts, to_ts=to_ts)
    history_row = await request.app["history"].history_entry(identity)
    if session is None and not timeline and not history_row:
        raise KeyError(sid)
    # Every run the window touches: the timeline's own run keys, plus the live
    # run and the history row for sessions the durable rows do not (yet) cover.
    run_ids = [str(entry["agent_run_id"]) for entry in timeline]
    if session is not None:
        run_ids.append(str(session.record.agent_run_id or session.record.id))
    if history_row:
        run_ids.append(str(history_row["id"]))
    ordered_runs = list(dict.fromkeys(run_ids))
    transcripts = await _bundle_transcript_slices(request.app, ordered_runs, from_ts, to_ts)
    return json_response(
        {
            "id": identity,
            "live": session is not None,
            "from": from_ts,
            "to": to_ts,
            "now": now,
            "state_log": state_log,
            "history": history_row,
            "runs": ordered_runs,
            "timeline": timeline,
            "timeline_truncated": truncated,
            "timeline_sink": store.stats(),
            "fleet_status_health": fleet_status_health(request.app["sessions"].sessions.values()),
            "transcripts": transcripts,
        }
    )


async def get_status_health(request: web.Request) -> web.Response:
    """Fleet status-health diagnostic: inferred-recovery counts, bounds, alarm.

    A healthy fleet reaches terminal status by proven evidence; a rise in
    inferred recoveries, a contract violation, or a session stuck active past
    the bound raises the alarm the soak matrix asserts on.
    """
    from .session import fleet_status_health

    return json_response(fleet_status_health(request.app["sessions"].sessions.values()))


async def get_background_health(request: web.Request) -> web.Response:
    """Background-task diagnostic: which long-lived loops are alive and faulting.

    Every loop is supervised (restart with capped backoff) and every event-bus
    drop is attributed, so a dead poller or a starved consumer is visible here
    rather than presenting as a feature that quietly stopped working.
    """
    tier0: Tier0Store = request.app["tier0"]
    events: EventBus = request.app["events"]
    consumers: DeterministicConsumerService = request.app["deterministic_consumers"]
    loop_lag: LoopLagMonitor = request.app["loop_lag"]
    return json_response(
        {
            **background.health(),
            # Read this before the per-loop numbers when the question is "why does the
            # UI feel slow". Loop cost tells you which subsystem is expensive; this
            # tells you whether anything is blocking the thread every request, frame
            # and keystroke shares.
            "loop_lag": loop_lag.snapshot(),
            "event_bus": events.drop_stats(),
            "tier0_capture": tier0.capture_stats(),
            # A detector that stopped producing findings is indistinguishable from
            # a quiet fleet unless the loop's own liveness is reported.
            "deterministic_consumers": consumers.status(),
            # "no card" is a legitimate outcome, so the reason a project has none
            # has to be readable somewhere or it looks like nothing was enabled.
            "project_cards": request.app["project_cards"].status(),
            "mcp": request.app["mcp"].status(),
        }
    )


async def patch_session(request: web.Request) -> web.Response:
    session = request.app["sessions"].resolve(request.match_info["sid"])
    body = await request.json()
    if "name" in body:
        session.record.name = str(body["name"]).strip() or session.record.name
        session.record.auto_named = False
    if "project_id" in body or "project" in body:
        raise ValueError("a session's owning project cannot be changed")
    if "pin" in body:
        session.record.pinned_attention = bool(body["pin"])
    if "voice_mode" in body:
        mode = body["voice_mode"]
        if mode is not None and mode not in {"off", "on_demand", "auto"}:
            raise ValueError("voice_mode must be off, on_demand, auto, or null to inherit")
        session.record.voice_mode = mode
    if "voice_content" in body:
        content = body["voice_content"]
        if content is not None and content not in {"summary", "verbatim"}:
            raise ValueError("voice_content must be summary, verbatim, or null to inherit")
        session.record.voice_content = content
    await request.app["history"].update_session_metadata(session.record)
    session.publish_update()
    await request.app["events"].emit("session_updated", session_id=session.record.id)
    return json_response(session.record.snapshot())


async def regenerate_session_title(request: web.Request) -> web.Response:
    session = request.app["sessions"].resolve(request.match_info["sid"])
    record = session.record
    if not has_observable_transcript(record.backend) or not record.agent_run_id:
        raise ValueError("title regeneration requires an active agent run")
    if record.state in {"exited", "crashed"}:
        raise ValueError("an ended session cannot regenerate its title")
    if record.auto_named is False:
        raise ValueError("a manually named session keeps its user title")
    await request.app["events"].emit(
        "title_regenerate_requested",
        session_id=record.id,
        source="user",
        force_title=True,
    )
    return json_response({"ok": True}, 202)


async def clear_session_standing_activity(request: web.Request) -> web.Response:
    """Manually retract a standing-activity annotation the user can see is wrong.

    Every annotation source is evidence about something the daemon cannot
    observe directly, so any of them can be left holding a claim the user knows
    is false - a completion notification that never arrived, a set adopted
    across a daemon restart whose closes were read as history. The decay path
    for that is a 30-minute TTL, which is a long time to look at a session that
    says an agent is still working when nothing is.

    Bounded on purpose: annotations are not states, so this cannot move
    `SessionState`, `awaiting_reason`, or `delivery_state`, and it cannot
    *assert* activity - only retract it. The run-scoped launch bookkeeping goes
    with it, so a later duplicate completion cannot decrement a fresh
    annotation, and the clear is ledgered like every other one (evidence
    `manual`) rather than silently mutating the record.
    """
    session = request.app["sessions"].resolve(request.match_info["sid"])
    try:
        body = await request.json()
    except (ValueError, json.JSONDecodeError):
        body = {}
    kind = str((body or {}).get("kind") or "").strip()
    if kind:
        if kind not in get_args(StandingActivityKind):
            raise ValueError(f"unknown standing-activity kind: {kind}")
        cleared = clear_standing_activity(
            session, cast(StandingActivityKind, kind), evidence="manual"
        )
    else:
        cleared = clear_all_standing_activity(session, evidence="manual")
    if cleared:
        observation_state = getattr(session, "observation_state", None)
        if isinstance(observation_state, dict) and kind in {"", "background_tasks"}:
            observation_state.get("background_open", {}).clear()
            observation_state.get("background_labels", {}).clear()
        session.publish_update()
    return json_response(
        {
            "ok": True,
            "cleared": cleared,
            "standing_activity": [
                activity.snapshot() for activity in session.record.standing_activity
            ],
        }
    )


async def delete_session(request: web.Request) -> web.Response:
    manager: SessionManager = request.app["sessions"]
    session = manager.resolve(request.match_info["sid"])
    if session.record.state not in {"exited", "crashed"}:
        await manager.stop(session.record.id)
    manager.sessions.pop(session.record.id, None)
    attachment_locks = request.app.get("attachment_locks", {})
    for key in tuple(attachment_locks):
        if key[1] == session.record.id:
            attachment_locks.pop(key, None)
    shutil.rmtree(
        session_media_directory(request.app["config"].data_dir, session.record.id),
        ignore_errors=True,
    )
    return json_response({"ok": True})


async def relaunch_session(request: web.Request) -> web.Response:
    """Replay a task-launched shell in place: spawn a fresh copy, retire the old.

    Relaunch-from-record: the replacement re-runs the exact retained
    executable/argv/cwd/env, so no task file is re-read and no trust re-approval is
    needed. All four are replayed from the record because a task step's directory and
    environment are spawn inputs in their own right, not something recoverable from
    the argv. Only sessions the daemon marked relaunchable qualify; agent and plain
    shell sessions are rejected so this never touches their lifecycle.
    """
    manager: SessionManager = request.app["sessions"]
    old = manager.resolve(request.match_info["sid"])
    if not old.record.relaunchable:
        raise ValueError("session is not relaunchable")
    body = {
        "project_id": old.record.project_id,
        "backend": "shell",
        "name": old.record.name,
        "executable": old.record.exe,
        "argv": list(old.record.args),
        "completion_mode": old.record.completion_mode,
        "env": dict(old.record.spawn_env),
    }
    if old.record.spawn_cwd:
        body["cwd"] = old.record.spawn_cwd
    # Spawn the replacement first: if it raises, the original is left fully intact.
    session = await _spawn_from_body(request.app, body)
    session.record.relaunchable = True
    session.publish_update()
    old_id = old.record.id
    if old.record.state not in {"exited", "crashed"}:
        await manager.stop(old_id)
    manager.sessions.pop(old_id, None)
    attachment_locks = request.app.get("attachment_locks", {})
    for key in tuple(attachment_locks):
        if key[1] == old_id:
            attachment_locks.pop(key, None)
    shutil.rmtree(
        session_media_directory(request.app["config"].data_dir, old_id),
        ignore_errors=True,
    )
    return json_response({"session": session.record.snapshot(), "replaced": old_id}, 201)


def _branch_source_id(source: Any) -> str | None:
    """The canonical conversation id to fork from.

    Deliberately NOT ``record.native_session_id``: the transcript observer
    rewrites that field from whatever file it is following, and with sibling
    agents in one cwd it can latch onto a sibling's transcript (see the
    transcript-switch cross-attribution fix), so branching off it would fork the
    wrong conversation. ``agent_lifecycle_id`` is the lifecycle anchor the
    observer never overwrites. For Claude the mux id is itself a valid transcript
    stem (spawned via ``--session-id``), so an unchanged native id is fine; for
    Codex only a detected rollout id is meaningful — the mux id is a placeholder.
    """
    record = source.record
    lifecycle = getattr(source, "agent_lifecycle_id", None)
    if record.backend == "claude":
        return str(lifecycle or record.native_session_id or record.id)
    candidate = str(lifecycle or record.native_session_id or "")
    return candidate if candidate and candidate != record.id else None


def _claude_transcript_stems(adapter: Any, cwd: str) -> set[str]:
    """Existing Claude transcript ids (file stems) for a working directory."""
    try:
        directory = adapter.transcript_path("probe", Path(cwd)).parent
        return {path.stem for path in directory.glob("*.jsonl")} if directory.exists() else set()
    except OSError:
        return set()


async def _await_claude_fork(
    adapter: Any, cwd: str, original: str, before: set[str], timeout_seconds: float
) -> tuple[bool, str | None]:
    """Wait for ``/branch`` to write a brand-new transcript in ``cwd``.

    Watching the filesystem directly (rather than ``record.native_session_id``)
    sidesteps the sibling-cwd switch gate, which intentionally suppresses the
    daemon's own id update when other agents share the directory.

    Returns ``(forked, branch_id)``. The id is the conversation the source pane
    moved to, and it is reported only when exactly one new transcript appeared:
    another agent starting in this cwd in the same instant makes "which file is the
    fork" a guess, and the caller must not roll a pane's identity onto a guess. The
    fork itself is still confirmed, so the branch proceeds without the roll.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_seconds
    while loop.time() < deadline:
        await asyncio.sleep(0.3)
        appeared = _claude_transcript_stems(adapter, cwd) - before - {original}
        if appeared:
            return True, next(iter(appeared)) if len(appeared) == 1 else None
    return False, None


# Harnesses with an implemented branch strategy. Claude forks natively in place;
# Codex simulates a fork by resuming a child thread. Any other backend is refused
# in branch_session — see the comment there for why the generic fallback is not
# safe to reuse.
_BRANCH_STRATEGY_BACKENDS = frozenset({"claude", "codex"})


async def branch_session(request: web.Request) -> web.Response:
    """Fork an agent conversation, keeping the original and the branch both open.

    Claude has a native ``/branch`` that forks to a fresh session id in place, so
    we inject it, wait for the new transcript to appear (confirming the fork
    froze the original), then reopen the original id in a sibling pane — the
    source pane holds the new branch. Codex has no in-CLI branch, so we simulate
    one: ``codex resume`` starts a child thread (``parent_thread_id`` set) that
    diverges from the still-live original without sharing its rollout, so the
    source pane keeps the original and the new pane holds the branch.

    In the Claude case the sibling *continues* the original conversation rather than
    starting a new one, so it inherits that conversation's run exactly as a resume
    does; opening a second row there left one conversation showing as two entries
    with one file indexed twice. That inheritance is only sound once the source pane
    has let go of the run, so the confirmed fork id is applied to it first: the
    source retires the original row and opens its own, and the sibling then reopens
    the row the original conversation owns. Without the explicit roll the source pane
    keeps the retired id until a hook happens to report the replacement.
    """
    manager: SessionManager = request.app["sessions"]
    source = manager.resolve(request.match_info["sid"])
    record = source.record
    if not has_observable_transcript(record.backend):
        return json_response(
            {"error": "only observable agent sessions can branch", "code": "not_agent"}, 422
        )
    if record.backend not in _BRANCH_STRATEGY_BACKENDS:
        # The fallback below resumes the original conversation id while the
        # source is still live. Codex tolerates that (resume starts a child
        # thread with its own rollout); OMP's --resume appends to the *same*
        # session file, so two live processes would interleave writes into one
        # JSONL. A harness without an implemented strategy is refused outright
        # rather than corrupted politely.
        return json_response(
            {
                "error": f"branching is not implemented for {record.backend} sessions",
                "code": "branch_unsupported",
            },
            422,
        )
    original = _branch_source_id(source)
    if not original:
        return json_response(
            {"error": "no conversation id to branch from yet", "code": "native_id_missing"}, 409
        )
    project = request.app["projects"].projects.get(record.project_id)
    if project is None:
        return json_response({"error": "project missing", "code": "project_missing"}, 422)
    body = await request.json() if request.can_read_body else {}
    # Claude resolves transcripts by working directory, so the fork and the
    # resumed copy must run in the same cwd the conversation was recorded under.
    branch_cwd = record.run_cwd or record.cwd

    # Captured before the fork: after it, the source pane's run is its own new
    # conversation's, and the row this sibling continues is the one it holds now.
    original_run_id = record.agent_run_id or record.id
    adopt_run_id: str | None = None
    if record.backend == "claude":
        adapter = manager.adapters.get("claude")
        before = _claude_transcript_stems(adapter, branch_cwd) if adapter else set()
        source.pty.write("/branch\r")
        forked, branch_id = (
            await _await_claude_fork(
                adapter, branch_cwd, original, before, timeout_seconds=15.0
            )
            if adapter
            else (False, None)
        )
        if not forked:
            # The fork never registered; do not resume the (still-live) original —
            # that would collide on its transcript. The source pane may already be
            # branched; the original stays resumable from history.
            return json_response(
                {"error": "branch did not complete in time; try again", "code": "branch_timeout"},
                504,
            )
        if branch_id is not None and await manager.roll_agent_conversation(
            record.id, native_id=branch_id, reason="branched", source="branch"
        ):
            # The source pane is on its own conversation now, so the original's row
            # is free for the sibling that continues it.
            adopt_run_id = original_run_id
    suffix = "original" if record.backend == "claude" else "branch"
    session = await manager.spawn(
        backend=record.backend,
        name=body.get("name") or f"{record.name} {suffix}",
        cwd=branch_cwd,
        project_id=record.project_id,
        resume_native_id=original,
        adopt_run_id=adopt_run_id,
        project_label=project.name,
    )
    next_layout = attach_terminal(
        project.layout,
        session.record.id,
        target_id=body.get("target_session_id") or record.id,
        direction=body.get("direction") or "after",
    )
    try:
        await request.app["projects"].update(
            record.project_id, layout=next_layout, layout_revision=project.layout_revision
        )
    except Exception:
        await manager.stop(session.record.id)
        manager.sessions.pop(session.record.id, None)
        raise
    return json_response({"session": session.record.snapshot(), "source": record.id}, 201)


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
    session.pty.write(data)
    now = time.monotonic()
    session.input_revision += 1
    session.last_input_event_ts = now
    session.last_input_report_ts = now
    events.emit_background(
        "terminal_input",
        session_id=session.record.id,
        source=source,
        input_owner=input_owner,
        bytes=len(data.encode("utf-8")),
    )


async def session_input(request: web.Request) -> web.Response:
    body = await request.json()
    session = request.app["sessions"].resolve(request.match_info["sid"])
    if session.record.state in {"exited", "crashed"}:
        return json_response({"error": "the session has ended"}, 409)
    data = str(body.get("data", ""))
    if not data:
        return json_response({"ok": True})
    _record_operator_input(request.app["events"], session, data, source="http")
    return json_response({"ok": True})


async def session_startup_metrics(request: web.Request) -> web.Response:
    session = request.app["sessions"].resolve(request.match_info["sid"])
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
        await request.app["events"].emit(
            "session_startup_client_measured",
            session_id=session.record.id,
            source="browser",
            timing_ms=dict(timing_ms),
        )
    return json_response({"ok": True, "timing_ms": session.record.client_startup_timing_ms})


async def broadcast_set(request: web.Request) -> web.Response:
    session = request.app["sessions"].resolve(request.match_info["sid"])
    session.record.broadcast = bool((await request.json()).get("include", True))
    session.publish_update()
    await request.app["events"].emit(
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
        if candidate.record.state in {"exited", "crashed"} or not candidate.pty.isalive():
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
        await deliver_broadcast(request.app["sessions"], data, request.app["events"])
    )


# ---------------------------------------------------------------------------
# Phase 4: persistent manual prompt queue. Thin handlers only — ordering,
# revision checks, readiness, identity, and audit live in PromptQueueService.


async def queue_summary(request: web.Request) -> web.Response:
    return json_response({"targets": await request.app["prompt_queue"].summary()})


async def queue_messages(request: web.Request) -> web.Response:
    target = request.query.get("target_session_id", "").strip()
    if not target:
        raise ValueError("target_session_id is required")
    return json_response(await request.app["prompt_queue"].target_view(target))


def _human_sender_kind(request: web.Request) -> str:
    """`user` for a local act, `remote_user` for an authenticated remote device.

    Derived from the transport, never from the request body: sender provenance
    that a client can claim is provenance that means nothing (`ROADMAP.md`
    Phase 5, "explicit sender provenance"). Remote origin is recorded, not
    privileged — it weakens no check anywhere downstream.
    """
    peer = request.transport.get_extra_info("peername") if request.transport else None
    host = peer[0] if peer else ""
    return "user" if host in {"127.0.0.1", "::1", ""} else "remote_user"


async def queue_create_message(request: web.Request) -> web.Response:
    body = await request.json()
    sender_kind = _human_sender_kind(request)
    message = await request.app["prompt_queue"].enqueue(
        target_session_id=str(body.get("target_session_id") or ""),
        body=str(body.get("body") or ""),
        armed=bool(body.get("armed", False)),
        insert_after=str(body["insert_after"]) if body.get("insert_after") else None,
        # The HTTP surface is a human surface: the sender kind is derived from
        # the transport (local vs remote device). Agent and observer senders
        # are in-process callers only and never reach this route.
        sender_kind=sender_kind,
        sender_id=str(body["sender_id"]) if body.get("sender_id") else None,
        sender_label=str(body["sender_label"])[:80] if body.get("sender_label") else None,
        correlation_id=str(body["correlation_id"]) if body.get("correlation_id") else None,
        constraints=body.get("constraints"),
    )
    return json_response(message, 201)


async def queue_patch_message(request: web.Request) -> web.Response:
    queue: PromptQueueService = request.app["prompt_queue"]
    message_id = request.match_info["message_id"]
    body = await request.json()
    if body.get("retarget_session_id"):
        return json_response(
            await queue.retarget(message_id, target_session_id=str(body["retarget_session_id"]))
        )
    if "body" in body:
        revision = body.get("revision")
        if not isinstance(revision, int):
            raise ValueError("revision is required to edit a message body")
        return json_response(
            await queue.edit(message_id, revision=revision, body=str(body["body"]))
        )
    if "armed" in body:
        return json_response(await queue.set_armed(message_id, bool(body["armed"])))
    if "after" in body:
        after = body.get("after")
        return json_response(await queue.move(message_id, after=str(after) if after else None))
    if "constraints" in body:
        # Scheduling is a property of the queued item, not of a sender's UI.
        return json_response(await queue.set_constraints(message_id, body.get("constraints")))
    raise ValueError("nothing to change")


async def queue_cancel_message(request: web.Request) -> web.Response:
    body = await request.json()
    return json_response(
        await request.app["prompt_queue"].cancel(
            request.match_info["message_id"],
            kind=str(body.get("kind") or "cancelled"),
        )
    )


async def queue_message_deliveries(request: web.Request) -> web.Response:
    return json_response(
        {
            "deliveries": await request.app["prompt_queue"].store.deliveries(
                request.match_info["message_id"]
            )
        }
    )


async def queue_send_next(request: web.Request) -> web.Response:
    body = await request.json()
    message_id = str(body.get("message_id") or "")
    revision = body.get("revision")
    if not message_id or not isinstance(revision, int):
        raise ValueError("message_id and revision are required")
    return json_response(
        await request.app["prompt_queue"].send_next(
            message_id,
            revision=revision,
            idempotency_key=str(body["idempotency_key"]) if body.get("idempotency_key") else None,
            confirm=bool(body.get("confirm", False)),
        )
    )


async def queue_export(request: web.Request) -> web.Response:
    target = request.query.get("target_session_id", "").strip()
    if not target:
        raise ValueError("target_session_id is required")
    redact = request.query.get("redact_secrets", "1") not in {"0", "false"}
    return json_response(
        await request.app["prompt_queue"].export_target(target, redact_secrets=redact)
    )


# ---------------------------------------------------------------------------
# Phase 5: auto-delivery policy, mailbox, and the emergency controls. The
# bounds live in AutoDeliveryController/AgentMessagingService; these handlers
# only carry user acts to them.


async def queue_auto_status(request: web.Request) -> web.Response:
    return json_response(await request.app["auto_delivery"].status())


async def queue_auto_pause(request: web.Request) -> web.Response:
    """Pause-all / emergency disable. One flag, persisted, provider-independent."""
    body = await request.json()
    controller: AutoDeliveryController = request.app["auto_delivery"]
    await controller.set_paused(bool(body.get("paused", True)), by=_human_sender_kind(request))
    return json_response(await controller.status())


async def queue_auto_session(request: web.Request) -> web.Response:
    """Per-session opt-in: auto-delivery and/or accepting agent messages."""
    controller: AutoDeliveryController = request.app["auto_delivery"]
    session_id = request.match_info["sid"]
    body = await request.json()
    by = _human_sender_kind(request)
    if "accept_agent_messages" in body:
        await controller.set_accept_agent_messages(
            session_id, bool(body["accept_agent_messages"]), by=by
        )
    if "enabled" in body:
        if body["enabled"]:
            await controller.enable_session(
                session_id,
                ttl_minutes=int(body["ttl_minutes"]) if body.get("ttl_minutes") else None,
                max_sends=int(body["max_sends"]) if body.get("max_sends") else None,
                by=by,
            )
        else:
            await controller.disable_session(session_id, reason="disabled by user", by=by)
    return json_response(await controller.status())


async def queue_auto_report_unsafe(request: web.Request) -> web.Response:
    """Operator review: record a confirmed bad automatic delivery.

    Resets the proving period and pauses auto-delivery — the promotion criteria
    require zero known false-safe deliveries, so this is not a statistic to
    average away.
    """
    body = await request.json()
    controller: AutoDeliveryController = request.app["auto_delivery"]
    await controller.report_unsafe(str(body.get("note") or ""))
    return json_response(await controller.status())


async def queue_mailbox(request: web.Request) -> web.Response:
    author = request.query.get("author", "all").strip() or "all"
    role = request.query.get("role")
    project_id = request.query.get("project_id", "").strip() or None
    target_session_id = request.query.get("target_session_id", "").strip() or None
    try:
        limit = int(request.query.get("limit", "100") or 100)
    except ValueError as exc:
        raise QueueError("invalid_limit", "limit must be an integer", status=400) from exc
    return json_response(
        await request.app["agent_messaging"].mailbox(
            author=author,
            role=role.strip() if role else None,
            project_id=project_id,
            target_session_id=target_session_id,
            limit=limit,
        )
    )


_MEDIA_TYPES = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
}
_MEDIA_SIGNATURES = {
    "image/png": (b"\x89PNG\r\n\x1a\n",),
    "image/jpeg": (b"\xff\xd8\xff",),
    "image/webp": (b"RIFF",),
    "image/gif": (b"GIF87a", b"GIF89a"),
}
_HOOK_EVENT_TYPES = {
    event
    for harness in HARNESSES.values()
    for event in harness.hook_events
} | {
    # Compatibility events emitted by older Codex notify integrations and test
    # probes. Native hook catalogs live on the descriptors above.
    "turn_started",
    "turn_ended",
    "agent-turn-complete",
    "approval_needed",
    "approval-requested",
    "task_started",
    "task_complete",
    "rate_limit",
    "rate_limited",
}

_NORMALIZED_HOOK_EVENT_TYPES = {
    "turn_started",
    "turn_ended",
    "approval_needed",
    "task_started",
    "task_complete",
    "approval_resolved",
    "context_compacted",
    "rate_limit",
    "rate_limited",
}

# Hooks whose event necessarily wrote records into the *root* transcript: a prompt
# was submitted, a tool ran, a turn stopped. Only these date the "the CLI ran a turn
# and none of it landed in the file we are following" evidence that marks observation
# stale (`_note_transcript_staleness`).
#
# Deliberately excludes `Notification` — its most common form, `idle_prompt`, fires
# ~60 s *after* a turn ends to report that the agent is waiting, so it is guaranteed
# to arrive with no accompanying transcript activity. Including it marked every
# healthy idle agent in the fleet as stale (8 false positives across 4 sessions on
# the first live pass, and zero true ones). Also excludes `SessionStart`/`SessionEnd`
# (lifecycle, not turn content) and the subagent hooks, whose records go to sidechain
# files rather than the root transcript.
#
# `agent-turn-complete` is Codex's raw turn-end notify and MUST be here. Lifecycle
# hooks can be disabled, untrusted, or unavailable, so a `/new` behind a sibling
# that cannot be ruled out can still be invisible. This set is tested against the
# *raw* event type, so omitting the compatibility notify made
# `_note_transcript_staleness` unreachable for the one backend it was written for.
# Verified live: a Codex pane rolled by `/new` kept reporting the abandoned
# conversation as live, with its retired token counts, for 200s while
# `last_turn_hook_ts` stayed unset.
_TRANSCRIPT_BACKED_HOOK_EVENTS = {
    "UserPromptSubmit",
    "PreToolUse",
    "PostToolUse",
    "PostToolUseFailure",
    "Stop",
    "agent-turn-complete",
    "turn_started",
    "turn_ended",
    "task_started",
    "task_complete",
}


def validate_session_media(media_type: str, data: bytes | bytearray) -> str:
    suffix = _MEDIA_TYPES.get(media_type)
    if suffix is None:
        raise ValueError("supported clipboard image types: PNG, JPEG, WebP, GIF")
    if len(data) > 10 * 1024 * 1024:
        raise ValueError("clipboard image exceeds the 10 MiB limit")
    if not any(data.startswith(signature) for signature in _MEDIA_SIGNATURES[media_type]):
        raise ValueError("clipboard image content does not match its declared type")
    if media_type == "image/webp" and data[8:12] != b"WEBP":
        raise ValueError("clipboard image content does not match its declared type")
    return suffix


def session_media_directory(data_dir: Path, session_id: str) -> Path:
    root = (data_dir / "media").resolve()
    directory = (root / session_id).resolve()
    if directory.parent != root:
        raise ValueError("invalid session media identity")
    return directory


def cleanup_expired_session_media(data_dir: Path, now: float) -> int:
    root = (data_dir / "media").resolve()
    if not root.is_dir():
        return 0
    removed = 0
    cutoff = now - SESSION_MEDIA_TTL_SECONDS
    try:
        directories = list(root.iterdir())
    except OSError:
        return 0
    for directory in directories:
        if not directory.is_dir() or directory.is_symlink():
            continue
        try:
            # A session deleted mid-sweep used to end media cleanup for the
            # daemon's lifetime, after which 10 MiB clipboard images accumulated.
            entries = list(directory.iterdir())
        except OSError:
            continue
        for path in entries:
            try:
                if path.is_file() and not path.is_symlink() and path.stat().st_mtime < cutoff:
                    path.unlink()
                    removed += 1
            except OSError:
                continue
        with suppress(OSError):
            directory.rmdir()
    return removed


def cleanup_expired_preview_shots(roots: list[Path], now: float) -> int:
    """Age out headless preview screenshots.

    They are saved into the owning Project (data-dir fallback) so a local agent
    can read them, which also means they accumulate inside the user's repository:
    a UI-iteration session takes dozens of multi-hundred-KB PNGs a day and nothing
    ever removed them.
    """
    removed = 0
    cutoff = now - PREVIEW_SHOT_TTL_SECONDS
    for root in roots:
        directory = root / ".swe-mux" / "preview-shots" if root.name != "preview-shots" else root
        if not directory.is_dir() or directory.is_symlink():
            continue
        try:
            entries = list(directory.iterdir())
        except OSError:
            continue
        for path in entries:
            try:
                if path.is_file() and not path.is_symlink() and path.stat().st_mtime < cutoff:
                    path.unlink()
                    removed += 1
            except OSError:
                continue
    return removed


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
        await asyncio.sleep(60 * 60)


async def _upload_session_attachment(
    request: web.Request,
    *,
    image_only: bool,
) -> web.Response:
    allowed_gestures = (
        {"terminal-image", "clipboard-image"} if image_only else {"terminal-attachment"}
    )
    if request.headers.get("X-Mux-User-Gesture") not in allowed_gestures:
        noun = "image upload" if image_only else "attachment upload"
        raise web.HTTPForbidden(text=f"terminal {noun} requires an explicit user action")
    session = request.app["sessions"].resolve(request.match_info["sid"])
    adapter: BackendAdapter = request.app["sessions"].adapters[session.record.backend]
    if not is_agent_harness(session.record.backend):
        raise ValueError("attachments are supported only in registered agent sessions")
    if session.record.state in {"exited", "crashed"}:
        raise ValueError("attachments cannot be added to an ended session")
    project = request.app["projects"].projects.get(session.record.project_id)
    if project is None:
        raise ValueError("the session's owning Project is unavailable")
    workspace = await asyncio.to_thread(
        attachment_workspace_root,
        project.root,
        session.record.spawn_cwd or session.record.cwd,
    )
    if not request.content_type.startswith("multipart/"):
        raise ValueError("attachment upload must use multipart form data")
    reader = await request.multipart()
    part = await reader.next()
    if not isinstance(part, BodyPartReader) or part.name != "file":
        raise ValueError("multipart field 'file' is required")
    media_type = str(part.headers.get("Content-Type", "")).split(";", 1)[0].lower()
    data = bytearray()
    max_bytes = MAX_IMAGE_BYTES if image_only else MAX_ATTACHMENT_BYTES
    while chunk := await part.read_chunk(size=64 * 1024):
        data.extend(chunk)
        if len(data) > max_bytes:
            limit = "10 MiB" if image_only else "25 MiB"
            raise ValueError(f"attachment exceeds the {limit} limit")
    if await reader.next() is not None:
        raise ValueError("exactly one multipart file is required")
    filename = part.filename or "attachment"
    lock_key = (str(workspace), session.record.id)
    lock = request.app["attachment_locks"].setdefault(lock_key, asyncio.Lock())
    async with lock:
        stored = await asyncio.to_thread(
            store_session_attachment,
            workspace,
            session.record.id,
            filename,
            media_type,
            data,
            image_only=image_only,
        )
    reference = adapter.media_reference(stored.path) if stored.kind == "image" else str(stored.path)
    await request.app["events"].emit(
        "session_media_uploaded" if image_only else "session_attachment_uploaded",
        session_id=session.record.id,
        attachment_kind=stored.kind,
        media_type=stored.media_type,
        bytes=stored.bytes,
    )
    return json_response(stored.payload(reference), 201)


async def upload_session_attachment(request: web.Request) -> web.Response:
    return await _upload_session_attachment(request, image_only=False)


async def upload_session_media(request: web.Request) -> web.Response:
    """Compatibility endpoint for older image-paste clients."""
    return await _upload_session_attachment(request, image_only=True)


async def promote_session(request: web.Request) -> web.Response:
    session = request.app["sessions"].resolve(request.match_info["sid"])
    supplied = request.headers.get("X-Mux-Hook-Secret", "")
    if not secrets.compare_digest(supplied, session.hook_secret):
        raise web.HTTPForbidden(text="invalid hook secret")
    body = await request.json()
    promoted = await request.app["sessions"].promote(
        session.record.id,
        str(body["backend"]),
        str(body["native_id"]),
        str(body["cwd"]) if body.get("cwd") else None,
    )
    return json_response(promoted.record.snapshot())


async def demote_session(request: web.Request) -> web.Response:
    session = request.app["sessions"].resolve(request.match_info["sid"])
    supplied = request.headers.get("X-Mux-Hook-Secret", "")
    if not secrets.compare_digest(supplied, session.hook_secret):
        raise web.HTTPForbidden(text="invalid hook secret")
    body = await request.json()
    demoted = await request.app["sessions"].demote(
        session.record.id, str(body["backend"]), str(body["native_id"])
    )
    return json_response(demoted.record.snapshot())


async def _projects_payload(request: web.Request) -> list[dict[str, Any]]:
    manager: ProjectManager = request.app["projects"]
    activity = await request.app["history"].project_last_activity()
    return await asyncio.gather(
        *(_project_snapshot(request, item, activity) for item in manager.ordered_projects())
    )


async def list_projects(request: web.Request) -> web.Response:
    return json_response(await _projects_payload(request))


async def _project_snapshot(  # type: ignore[no-untyped-def]
    request: web.Request, project, activity: dict[str, float]
) -> dict[str, Any]:
    identity = ProjectIdentity(project.id, project.name, project.root, "registered")
    portable = await read_project_config(project.root, project=identity)
    values = portable["values"] if portable["status"] in {"ready", "read-only"} else {}
    public_values = {key: value for key, value in values.items() if key != "resource_open_mode"}
    config: Config = request.app["config"]
    effective = {
        "backend": project.default_backend
        or values.get("preferred_backend")
        or config.default_backend,
        "profile_id": project.default_profile_id
        or values.get("default_shell_profile")
        or config.default_shell_profile,
        "prompt_library_scope": values.get("prompt_library_scope") or "both",
        "notification_sounds_enabled": values.get("notification_sounds_enabled", True),
    }
    sources = {
        "backend": "project_record"
        if project.default_backend
        else "project_file"
        if values.get("preferred_backend")
        else "global",
        "profile_id": "project_record"
        if project.default_profile_id
        else "project_file"
        if values.get("default_shell_profile")
        else "global",
        "prompt_library_scope": "project_file" if values.get("prompt_library_scope") else "global",
        "notification_sounds_enabled": "project_file"
        if "notification_sounds_enabled" in values
        else "global",
    }
    snapshot = project.snapshot()
    # Retain the column/parser as a read-compatibility shim for older databases and
    # Project config files, but do not advertise a presentation mode the v6 browser
    # no longer implements.
    snapshot.pop("resource_open_mode", None)
    return {
        **snapshot,
        # Derived, not stored: history already dates every session a Project ever ran,
        # so a second write path that could drift from it would buy nothing. 0 means a
        # Project that has never run one, which the sidebar orders last.
        "last_activity": activity.get(project.id, 0.0),
        "portable_options": public_values,
        "effective_options": effective,
        "option_sources": sources,
        "project_config_status": portable["status"],
    }


async def create_project(request: web.Request) -> web.Response:
    body = await request.json()
    if not isinstance(body.get("create_missing", False), bool):
        raise ValueError({"create_missing": "must be a boolean"})
    project = await request.app["projects"].create(
        str(body.get("name") or Path(str(body.get("root") or "")).name or "New project"),
        str(body.get("root") or ""),
        group_id=str(body["group_id"]) if body.get("group_id") else None,
        create_missing=bool(body.get("create_missing", False)),
    )
    await request.app["events"].emit(
        "project_created", source="user", project_id=project.id, root=project.root
    )
    activity = await request.app["history"].project_last_activity()
    return json_response(await _project_snapshot(request, project, activity), 201)


async def patch_project(request: web.Request) -> web.Response:
    body = await request.json()
    if "position" in body:
        raise ValueError({"position": "use the Project order endpoint"})
    if "sidebar_visible" in body and not isinstance(body["sidebar_visible"], bool):
        raise ValueError({"sidebar_visible": "must be a boolean"})
    backend = body.get("default_backend")
    if backend is not None and backend != "shell" and not is_agent_harness(backend):
        raise ValueError({"default_backend": "must be shell, a registered agent, or null"})
    profile_id = body.get("default_profile_id")
    if profile_id is not None and profile_id not in {
        profile.id for profile in request.app["config"].shell_profiles
    }:
        raise ValueError({"default_profile_id": "unknown shell profile"})
    project = await request.app["projects"].update(request.match_info["project_id"], **body)
    activity = await request.app["history"].project_last_activity()
    return json_response(await _project_snapshot(request, project, activity))


async def reorder_projects(request: web.Request) -> web.Response:
    body = await request.json()
    ordered_ids = body.get("project_ids")
    expected_order = body.get("expected_order")
    if not isinstance(ordered_ids, list) or not all(isinstance(item, str) for item in ordered_ids):
        raise ValueError({"project_ids": "must be an array of Project ids"})
    if not isinstance(expected_order, list) or not all(
        isinstance(item, str) for item in expected_order
    ):
        raise ValueError({"expected_order": "must be the last observed Project order"})
    try:
        projects = await request.app["projects"].reorder(ordered_ids, expected_order=expected_order)
    except ValueError as exc:
        if "order changed" in str(exc):
            return json_response({"error": str(exc), "code": "order_conflict"}, 409)
        raise
    await request.app["events"].emit("projects_reordered", source="user", project_ids=ordered_ids)
    activity = await request.app["history"].project_last_activity()
    return json_response(
        await asyncio.gather(*(_project_snapshot(request, item, activity) for item in projects))
    )


async def delete_project(request: web.Request) -> web.Response:
    project_id = request.match_info["project_id"]
    if any(
        item.record.project_id == project_id for item in request.app["sessions"].sessions.values()
    ):
        raise ValueError("remove this project's sessions before deleting it")
    await request.app["projects"].delete(project_id)
    return json_response({"ok": True})


def _action_project(request: web.Request):  # type: ignore[no-untyped-def]
    project_id = request.match_info["project_id"]
    project = request.app["projects"].projects.get(project_id)
    if project is None:
        raise ValueError(f"unknown project: {project_id}")
    return project


async def list_project_actions(request: web.Request) -> web.Response:
    project = _action_project(request)
    service: ProjectActionService = request.app["project_actions"]
    return json_response(service.catalog(project.root).snapshot())


async def trust_project_actions(request: web.Request) -> web.Response:
    project = _action_project(request)
    body = await request.json()
    fingerprint = str(body.get("fingerprint") or "")
    if not fingerprint:
        raise ValueError({"fingerprint": "is required"})
    service: ProjectActionService = request.app["project_actions"]
    catalog = service.trust(project.root, fingerprint)
    await request.app["events"].emit(
        "project_actions_trusted",
        source="user",
        project_id=project.id,
        fingerprint=catalog.fingerprint,
        files=list(catalog.sources),
    )
    return json_response(catalog.snapshot())


async def _project_profile_id(request: web.Request, project) -> str:  # type: ignore[no-untyped-def]
    """The shell profile a Project-owned command should be launched through."""
    portable = await read_project_config(
        project.root, project=ProjectIdentity(project.id, project.name, project.root, "registered")
    )
    values = portable["values"] if portable["status"] in {"ready", "read-only"} else {}
    return str(
        project.default_profile_id
        or values.get("default_shell_profile")
        or request.app["config"].default_shell_profile
    )


async def run_project_action(request: web.Request) -> web.Response:
    project = _action_project(request)
    body = await request.json()
    action_id = str(body.get("action_id") or "")
    if not action_id:
        raise ValueError({"action_id": "is required"})
    service: ProjectActionService = request.app["project_actions"]
    try:
        catalog, action = service.action(project.root, action_id)
    except PermissionError as exc:
        return json_response(
            {
                "error": str(exc),
                "code": "project_actions_trust_required",
                "catalog": service.catalog(project.root).snapshot(),
            },
            409,
        )
    except KeyError as exc:
        raise ValueError(f"unknown Project Action: {action_id}") from exc
    profile_id = await _project_profile_id(request, project)
    sessions: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for batch in action.batches:
        results = await asyncio.gather(
            *(
                _spawn_from_body(
                    request.app,
                    action_spawn_body(
                        step,
                        project_id=project.id,
                        config=request.app["config"],
                        profile_id=str(profile_id),
                    ),
                )
                for step in batch
            ),
            return_exceptions=True,
        )
        for step, result in zip(batch, results, strict=True):
            if isinstance(result, BaseException):
                errors.append({"step": step.name, "error": str(result)})
            else:
                # Task shells retain their exact spawn argv, so their rail offers an
                # in-place Relaunch. The flag is set post-spawn and republished so
                # every attached client sees it, not only this action's caller.
                result.record.relaunchable = True
                result.publish_update()
                sessions.append(result.record.snapshot())
    await request.app["events"].emit(
        "project_action_started",
        source="user",
        project_id=project.id,
        action_id=action.id,
        action_label=action.label,
        fingerprint=catalog.fingerprint,
        session_ids=[item["id"] for item in sessions],
        failures=len(errors),
    )
    return json_response(
        {"action": action.snapshot(), "sessions": sessions, "errors": errors},
        201 if not errors else 207,
    )


async def run_project_init_scripts(request: web.Request) -> web.Response:
    """Run the selected user-authored init scripts inside a Project.

    Each script becomes one visible one-shot terminal at the Project root, started in
    configured order. Start order is all that is promised: a script that must finish
    before the next one begins belongs in the same script, using the shell's own `&&`
    or `;`. Nothing here reads or trusts repository content, so no fingerprint approval
    is involved (see project_init.py).
    """
    project = _action_project(request)
    body = await request.json()
    raw_ids = body.get("script_ids")
    if not isinstance(raw_ids, list) or any(not isinstance(item, str) for item in raw_ids):
        raise ValueError({"script_ids": "must be an array of init script ids"})
    config: Config = request.app["config"]
    chosen, unknown = select_init_scripts(config, [str(item) for item in raw_ids])
    if unknown:
        raise ValueError({"script_ids": f"unknown init scripts: {', '.join(unknown)}"})
    profile_id = await _project_profile_id(request, project)
    sessions: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for script in chosen:
        step = init_script_step(script, root=project.root)
        try:
            session = await _spawn_from_body(
                request.app,
                action_spawn_body(
                    step, project_id=project.id, config=config, profile_id=profile_id
                ),
            )
        except Exception as exc:  # one failed launch must not strand the rest
            errors.append({"script": script["id"], "error": str(exc)})
            continue
        # Same rationale as a Project Action step: the exact argv is retained, so the
        # terminal rail can offer an in-place Relaunch.
        session.record.relaunchable = True
        session.publish_update()
        sessions.append(session.record.snapshot())
    await request.app["events"].emit(
        "project_init_scripts_started",
        source="user",
        project_id=project.id,
        script_ids=[script["id"] for script in chosen],
        session_ids=[item["id"] for item in sessions],
        failures=len(errors),
    )
    return json_response({"sessions": sessions, "errors": errors}, 201 if not errors else 207)


async def list_project_groups(request: web.Request) -> web.Response:
    manager: ProjectManager = request.app["projects"]
    return json_response([item.snapshot() for item in manager.ordered_groups()])


async def create_project_group(request: web.Request) -> web.Response:
    body = await request.json()
    group = await request.app["projects"].create_group(str(body.get("name") or ""))
    return json_response(group.snapshot(), 201)


async def patch_project_group(request: web.Request) -> web.Response:
    group = await request.app["projects"].update_group(
        request.match_info["group_id"], **await request.json()
    )
    return json_response(group.snapshot())


async def reorder_project_groups(request: web.Request) -> web.Response:
    body = await request.json()
    ordered_ids = body.get("group_ids")
    expected_order = body.get("expected_order")
    if not isinstance(ordered_ids, list) or not all(isinstance(item, str) for item in ordered_ids):
        raise ValueError({"group_ids": "must be an array of group ids"})
    if not isinstance(expected_order, list) or not all(
        isinstance(item, str) for item in expected_order
    ):
        raise ValueError({"expected_order": "must be the last observed group order"})
    try:
        groups = await request.app["projects"].reorder_groups(
            ordered_ids, expected_order=expected_order
        )
    except ValueError as exc:
        if "order changed" in str(exc):
            return json_response({"error": str(exc), "code": "order_conflict"}, 409)
        raise
    return json_response([item.snapshot() for item in groups])


async def delete_project_group(request: web.Request) -> web.Response:
    await request.app["projects"].delete_group(request.match_info["group_id"])
    return json_response({"ok": True})


def _request_project(request: web.Request):  # type: ignore[no-untyped-def]
    project = request.app["projects"].projects.get(request.match_info["project_id"])
    if not project:
        raise ValueError("unknown project")
    return project


async def get_agent_context(request: web.Request) -> web.Response:
    """Inventory the bounded context sources the selected Project's agents can use."""

    project = _request_project(request)
    service: AgentContextService = request.app["agent_context"]
    payload = await asyncio.to_thread(service.inventory, project.id, project.name, project.root)
    return json_response(payload)


async def get_agent_context_source(request: web.Request) -> web.Response:
    project = _request_project(request)
    service: AgentContextService = request.app["agent_context"]
    payload = await asyncio.to_thread(
        service.read_source, project.root, request.match_info["source_id"]
    )
    return json_response(payload)


async def reveal_agent_context_source(request: web.Request) -> web.Response:
    project = _request_project(request)
    service: AgentContextService = request.app["agent_context"]
    path = await asyncio.to_thread(
        service.source_path, project.root, request.match_info["source_id"]
    )
    await asyncio.to_thread(open_in_file_manager, path)
    return json_response({"ok": True})


async def preview_agent_context_sync(request: web.Request) -> web.Response:
    project = _request_project(request)
    body = await request.json()
    direction = str(body.get("direction") or "")
    service: AgentContextService = request.app["agent_context"]
    return json_response(await asyncio.to_thread(service.preview_sync, project.root, direction))


async def sync_agent_context(request: web.Request) -> web.Response:
    project = _request_project(request)
    body = await request.json()
    direction = str(body.get("direction") or "")
    source_revision = str(body.get("source_revision") or "")
    target_revision = str(body.get("target_revision") or "")
    if not source_revision or not target_revision:
        raise ValueError("source_revision and target_revision are required")
    service: AgentContextService = request.app["agent_context"]
    try:
        result = await asyncio.to_thread(
            service.sync,
            project.id,
            project.root,
            direction,
            source_revision,
            target_revision,
        )
    except AgentContextConflict as exc:
        return json_response({"error": str(exc), "code": "revision_conflict"}, 409)
    await request.app["events"].emit(
        "agent_context_changed",
        source="user",
        operation="sync",
        project_id=project.id,
        direction=direction,
        revision=result["revision"],
    )
    return json_response(result)


async def restore_agent_context(request: web.Request) -> web.Response:
    project = _request_project(request)
    body = await request.json()
    backup_id = str(body.get("backup_id") or "")
    target_revision = str(body.get("target_revision") or "")
    if not backup_id or not target_revision:
        raise ValueError("backup_id and target_revision are required")
    service: AgentContextService = request.app["agent_context"]
    try:
        result = await asyncio.to_thread(
            service.restore,
            project.id,
            project.root,
            backup_id,
            target_revision,
        )
    except AgentContextConflict as exc:
        return json_response({"error": str(exc), "code": "revision_conflict"}, 409)
    await request.app["events"].emit(
        "agent_context_changed",
        source="user",
        operation="restore",
        project_id=project.id,
        target=result["target"],
        revision=result["revision"],
    )
    return json_response(result)


async def list_project_files(request: web.Request) -> web.Response:
    project = _request_project(request)
    patterns = effective_project_ignores(
        project.root, request.app["config"].project_ignore_patterns
    )
    return json_response(
        list_project_directory(
            project.root,
            request.query.get("path", ""),
            ignore_patterns=patterns,
        )
    )


async def post_project_resource(request: web.Request) -> web.Response:
    project = _request_project(request)
    body = await request.json()
    if not isinstance(body, dict):
        raise TypeError("project resource body must be an object")
    parent = body.get("parent", "")
    name = body.get("name")
    kind = body.get("kind")
    if not isinstance(parent, str):
        raise TypeError("project resource parent must be a string")
    if not isinstance(name, str):
        raise TypeError("project resource name must be a string")
    if not isinstance(kind, str):
        raise TypeError("project resource kind must be a string")

    result = await asyncio.to_thread(
        create_project_resource,
        project.root,
        parent,
        name,
        kind,
    )
    patterns = await asyncio.to_thread(
        effective_project_ignores,
        project.root,
        request.app["config"].project_ignore_patterns,
    )
    result["hidden"] = ignored_project_path(str(result["path"]), patterns)
    return json_response(result, 201)


async def list_project_files_tree(request: web.Request) -> web.Response:
    """Batch-list the root plus every persisted-expanded folder in one round trip.

    Restoring a saved tree otherwise costs one request per open folder, which
    stacks up latency (and HTTP/1.1 connection limits) on a phone over Tailscale.
    Listings are blocking filesystem walks, so run the whole batch off the loop.
    """

    project = _request_project(request)
    patterns = effective_project_ignores(
        project.root, request.app["config"].project_ignore_patterns
    )
    paths = request.query.getall("path", [])
    # Always include the root, dedupe, and bound the fan-out so a hostile or
    # runaway query cannot ask us to stat thousands of directories.
    wanted = list(dict.fromkeys(["", *paths]))[:1000]
    result = await asyncio.get_running_loop().run_in_executor(
        None,
        lambda: list_project_directories(project.root, wanted, ignore_patterns=patterns),
    )
    return json_response(result)


async def search_project_files_route(request: web.Request) -> web.Response:
    project = _request_project(request)
    mode = request.query.get("mode", "names")
    if mode not in ("names", "contents", "both"):
        mode = "names"
    query = request.query.get("q", "")
    patterns = effective_project_ignores(
        project.root, request.app["config"].project_ignore_patterns
    )
    # The recursive walk (and any content reads) is blocking, so keep it off the event loop.
    result = await asyncio.get_running_loop().run_in_executor(
        None,
        lambda: search_project_files(project.root, query, mode=mode, ignore_patterns=patterns),
    )
    return json_response(result)


async def _project_file_root(project_root: str, requested: object) -> str:
    """Resolve an optional exact worktree root without widening Project ownership."""
    if requested is None or requested == "":
        return project_root
    if not isinstance(requested, str):
        raise git_review.GitReviewError("invalid_worktree", "worktree must be a string")
    repository, _common = await git_review.repository_identity(project_root)
    return await git_review.validate_worktree_root(repository, requested)


async def get_project_file(request: web.Request) -> web.Response:
    project = _request_project(request)
    root = await _project_file_root(project.root, request.query.get("worktree"))
    result = await asyncio.to_thread(read_project_file, root, request.query.get("path", ""))
    if root != project.root:
        result["worktree"] = root
    return json_response(result)


async def get_project_file_content(request: web.Request) -> web.Response:
    """Serve only a revision-pinned image that passed the Project viewer allowlist."""

    project = _request_project(request)
    root = await _project_file_root(project.root, request.query.get("worktree"))
    relative_path = request.query.get("path", "")
    expected_revision = request.query.get("revision", "")
    data, payload = await asyncio.to_thread(
        read_project_image_content,
        root,
        relative_path,
        expected_revision,
    )
    presentation = payload["presentation"]
    response = web.Response(
        body=data,
        headers={
            "Content-Type": str(presentation["mime"]),
            "Content-Length": str(len(data)),
            "Content-Disposition": "inline",
            "Cache-Control": "private, no-store",
            "ETag": f'"{payload["revision"]}"',
            "Accept-Ranges": "none",
            # If the URL is ever navigated to directly, it still cannot become a same-origin
            # active document. The ordinary middleware preserves endpoint-specific CSP values.
            "Content-Security-Policy": "sandbox; default-src 'none'",
        },
    )
    _apply_security_headers(response, request)
    return response


async def put_project_file(request: web.Request) -> web.Response:
    project = _request_project(request)
    body = await request.json()
    root = await _project_file_root(project.root, body.get("worktree"))
    try:
        result = await asyncio.to_thread(
            write_project_file,
            root,
            str(body.get("path") or ""),
            str(body.get("text") or ""),
            str(body.get("revision") or "missing"),
        )
    except ValueError as exc:
        if "changed externally" in str(exc):
            return json_response({"error": str(exc), "code": "revision_conflict"}, 409)
        raise
    return json_response(result)


async def reveal_project_resource(request: web.Request) -> web.Response:
    project = _request_project(request)
    body = await request.json()
    root = await _project_file_root(project.root, body.get("worktree"))
    target = project_path(root, str(body.get("path") or ""))
    if not target.exists():
        raise ValueError("project resource does not exist")
    await asyncio.to_thread(open_in_file_manager, target)
    return json_response({"ok": True})


async def ignore_project_resource(request: web.Request) -> web.Response:
    project = _request_project(request)
    body = await request.json()
    scope = str(body.get("scope") or "")
    if scope not in {"global", "project"}:
        raise ValueError("ignore scope must be global or project")
    root = Path(project.root).resolve()
    target = project_path(root, str(body.get("path") or ""))
    if target == root or not target.exists():
        raise ValueError("project resource does not exist")
    relative = target.relative_to(root).as_posix()
    pattern = target.name if scope == "global" else relative

    if scope == "global":
        config: Config = request.app["config"]
        patterns = list(config.project_ignore_patterns)
        added = pattern not in patterns
        if added:
            hot, _restart = update_config(config, {"project_ignore_patterns": [*patterns, pattern]})
            _apply_runtime_config(request.app, hot)
            await request.app["events"].emit(
                "configuration_changed",
                source="project_file_browser",
                changed=["project_ignore_patterns"],
            )
        return json_response({"ok": True, "scope": scope, "pattern": pattern, "added": added})

    identity = _registered_identity(project)
    current = await read_project_config(project.root, project=identity)
    if current["status"] == "malformed":
        raise ValueError("project config is malformed; fix it before adding an ignore")
    values = dict(current["values"])
    patterns = list(values.get("ignore_patterns", []))
    added = pattern not in patterns
    if added:
        values["ignore_patterns"] = [*patterns, pattern]
        await write_project_config(project.root, values, current["revision"], project=identity)
        await request.app["events"].emit("project_configuration_changed", project_id=project.id)
    return json_response({"ok": True, "scope": scope, "pattern": pattern, "added": added})


async def put_project_watch(request: web.Request) -> web.Response:
    body = await request.json()
    raw_paths = body.get("paths", [])
    if not isinstance(raw_paths, list) or not all(isinstance(item, str) for item in raw_paths):
        raise ValueError("paths must be an array of project-relative directories")
    watch_id = body.get("watch_id")
    if watch_id is not None and (not isinstance(watch_id, str) or len(watch_id) > 100):
        raise ValueError("watch_id must be a string of 100 characters or fewer")
    project = _request_project(request)
    root = await _project_file_root(project.root, body.get("worktree"))
    lease = request.app["project_watcher"].register(project.id, raw_paths, watch_id, root=root)
    return json_response(
        {
            "watch_id": lease.watch_id,
            "paths": list(lease.paths),
            "worktree": lease.root,
            "lease_seconds": 45,
        }
    )


async def delete_project_watch(request: web.Request) -> web.Response:
    request.app["project_watcher"].remove(
        request.match_info["project_id"], request.match_info["watch_id"]
    )
    return json_response({"ok": True})


async def list_history(request: web.Request) -> web.Response:
    external_value = request.query.get("external")
    page = await request.app["history"].history_page(
        query=request.query.get("q", ""),
        search_scope=request.query.get("scope", "all"),
        backend=request.query.get("backend"),
        project_id=request.query.get("project"),
        state=request.query.get("state"),
        external=(external_value.lower() == "true") if external_value is not None else None,
        date_from=float(request.query["date_from"]) if request.query.get("date_from") else None,
        date_to=float(request.query["date_to"]) if request.query.get("date_to") else None,
        time_basis=request.query.get("time_basis", "started"),
        cursor=request.query.get("cursor"),
        limit=int(request.query.get("limit", min(50, request.app["config"].history_limit))),
    )
    await request.app["history"].refresh_time_summaries(page["items"])
    await _decorate_generated_titles(request.app, page["items"])
    return json_response(page)


async def list_history_projects(request: web.Request) -> web.Response:
    return json_response({"items": await request.app["history"].history_projects()})


async def start_history_backfill(request: web.Request) -> web.Response:
    body = await request.json()
    project_id = str(body.get("project_id") or "")
    if not project_id:
        raise ValueError("project_id is required")
    return json_response({"job": request.app["history_backfills"].start(project_id)}, 202)


async def list_history_backfills(request: web.Request) -> web.Response:
    return json_response(
        {"items": request.app["history_backfills"].list(request.query.get("project_id"))}
    )


async def get_history_backfill(request: web.Request) -> web.Response:
    return json_response(
        {"job": request.app["history_backfills"].get(request.match_info["job_id"])}
    )


async def cancel_history_backfill(request: web.Request) -> web.Response:
    return json_response(
        {"job": request.app["history_backfills"].cancel(request.match_info["job_id"])}
    )


async def history_transcript(request: web.Request) -> web.Response:
    row = await request.app["history"].history_entry(request.match_info["sid"])
    if not row:
        raise KeyError(request.match_info["sid"])
    transcript = row.get("transcript_path")
    if not transcript or not Path(transcript).is_file():
        return json_response(
            {"error": "native transcript is unavailable", "code": "transcript_unavailable"},
            409,
        )
    # Parse off the event loop and reuse the shared (path, mtime, size, backend,
    # max_bytes) cache; large transcripts otherwise block the loop on every open.
    # The watermark comes back from the same call, so it can never claim to cover
    # bytes this parse did not read.
    messages, mtime_ns, size = await asyncio.to_thread(
        parse_transcript_with_watermark, Path(transcript), str(row["backend"])
    )
    await request.app["history"].replace_history_messages(
        str(row["id"]), messages, mtime_ns=mtime_ns, size=size
    )
    row = await request.app["history"].history_entry(str(row["id"])) or row
    matches = await request.app["history"].history_message_matches(
        str(row["id"]), request.query.get("q", ""), request.query.get("scope", "all")
    )
    annotations = await request.app["automation_store"].annotations(
        agent_run_id=str(row["id"]), limit=200
    )
    await _decorate_generated_titles(request.app, [row])
    return json_response(
        {"entry": row, "messages": messages, "annotations": annotations, "matches": matches}
    )


async def resume_history(request: web.Request) -> web.Response:
    row = await request.app["history"].history_entry(request.match_info["sid"])
    if not row:
        raise KeyError(request.match_info["sid"])
    if not row.get("agent_visible") or not has_observable_transcript(row.get("backend")):
        return json_response(
            {"error": "only observable agent history can be resumed", "code": "not_agent"},
            422,
        )
    # History stores the stable raw session name; generated titles live in run
    # annotations. Resolve the effective visible name before Codex mints its new
    # run, otherwise the annotation remains keyed to the retired run and the
    # resumed pane falls back to `codex-<id>`.
    annotation_reader = getattr(request.app.get("automation_store"), "annotations", None)
    if callable(annotation_reader):
        await _decorate_generated_titles(request.app, [row])
    body = await request.json() if request.can_read_body else {}
    target_project = str(body.get("project_id") or row.get("project_id") or "")
    requirements = {
        "native_id_missing": not row.get("native_id"),
        "cwd_missing": not row.get("cwd") or not Path(str(row["cwd"])).is_dir(),
        "transcript_unavailable": not row.get("transcript_path")
        or not Path(str(row["transcript_path"])).is_file(),
        "target_project_missing": target_project not in request.app["projects"].projects,
        "adapter_missing": row.get("backend") not in request.app["sessions"].adapters,
    }
    if code := next((key for key, failed in requirements.items() if failed), None):
        return json_response(
            {"error": code.replace("_", " "), "code": code},
            409 if code == "transcript_unavailable" else 422,
        )
    # A conversation a live session currently claims must not be resumed into a
    # second pane: two sessions tracking one conversation is exactly the linked
    # status/token cross-attribution the identity invariant forbids. The live
    # pane is where that conversation continues; Branch is the flow for forking
    # it. Rows whose pane has since rolled to a different conversation resume
    # fine — their native id is no longer claimed.
    live_owner = next(
        (
            other
            for other in request.app["sessions"].sessions.values()
            if other.record.backend == row["backend"]
            and other.record.state not in {"exited", "crashed"}
            and other.record.native_session_id == row["native_id"]
        ),
        None,
    )
    if live_owner is not None:
        return json_response(
            {
                "error": f"conversation is live in session {live_owner.record.name}",
                "code": "conversation_live",
                "session_id": live_owner.record.id,
            },
            409,
        )
    owning_project = request.app["projects"].projects[target_project]
    # A resume that reopens the same conversation, in the same file, under the same
    # id continues an agent run that already has a row: the pane inherits it rather
    # than opening a second entry over one file. Only the adapter can say whether
    # this resume is that, because the answer is the CLI's own transcript-resolution
    # rule -- Claude resolves by working directory (a resume into another root writes
    # a different file, so that is a new conversation), Codex by thread id.
    adapter = request.app["sessions"].adapters[str(row["backend"])]
    adopts_run = bool(adapter.resume_continues_conversation(str(row["cwd"]), owning_project.root))
    requested_name = str(body.get("name") or "")
    # `auto_named` arrives from SQLite as 0/1, so an `is not False` test never
    # matched: a conversation the user had renamed came back under its *generated*
    # title instead of the name they pinned.
    inherited_name = (
        str(row.get("generated_title"))
        if bool(row.get("auto_named")) and row.get("generated_title")
        else str(row["name"])
    )
    session = await request.app["sessions"].spawn(
        backend=str(row["backend"]),
        # The conversation keeps its name. A suffix here compounded on every
        # resume ("… resumed resumed") and, for Claude, retitled an entry the
        # resumed pane now shares rather than replaces.
        name=requested_name or inherited_name,
        cwd=owning_project.root,
        project_id=target_project,
        resume_native_id=str(row["native_id"]),
        adopt_run_id=str(row["id"]) if adopts_run else None,
        auto_named=None if requested_name else bool(row.get("auto_named")),
        project_label=owning_project.name,
    )
    next_layout = attach_terminal(
        owning_project.layout,
        session.record.id,
        target_id=body.get("target_session_id"),
        direction=body.get("direction"),
    )
    try:
        await request.app["projects"].update(
            target_project,
            layout=next_layout,
            layout_revision=owning_project.layout_revision,
        )
    except Exception:
        await request.app["sessions"].stop(session.record.id)
        request.app["sessions"].sessions.pop(session.record.id, None)
        raise
    child_run_id = session.record.agent_run_id or session.record.id
    # An inherited run is the same run, not a descendant of one: recording an
    # edge from a conversation to itself would make every consumer that walks
    # lineage see a cycle where nothing was forked.
    if child_run_id != str(row["id"]):
        await request.app["automation_store"].add_lineage(
            str(row["id"]),
            child_run_id,
            "resume",
            {"backend": row["backend"], "project_id": target_project},
        )
    return json_response(session.record.snapshot(), 201)


def _live_agent_run_ids(manager: SessionManager) -> frozenset[str]:
    """Run rows a live pane is still writing to."""
    return frozenset(
        session.record.agent_run_id or session.record.id
        for session in manager.sessions.values()
        if session.record.backend in AGENT_BACKENDS
        and session.record.state not in {"exited", "crashed"}
    )


async def list_history_duplicates(request: web.Request) -> web.Response:
    """Conversations whose history is split across more than one entry."""
    return json_response({"items": await request.app["history"].duplicate_conversation_rows()})


async def repair_history_duplicates(request: web.Request) -> web.Response:
    """Fold duplicate rows back into each conversation's own entry.

    Explicit and dry by default. Merging rewrites history entries, so it is never
    something a daemon start or a migration does on its own — the duplicates it
    repairs came from bugs, but an automatic merge would be indistinguishable from
    losing entries, and there is no undo.
    """
    body = await request.json() if request.can_read_body else {}
    dry_run = bool(body.get("dry_run", True))
    result = await request.app["history"].merge_duplicate_conversation_rows(
        live_run_ids=_live_agent_run_ids(request.app["sessions"]), dry_run=dry_run
    )
    if not dry_run and result["merged"]:
        await request.app["events"].emit(
            "history_duplicates_merged",
            source="user",
            conversations=len(result["merged"]),
            removed=sum(len(item["removed"]) for item in result["merged"]),
        )
    return json_response(result)


async def delete_history_entry(request: web.Request) -> web.Response:
    row = await request.app["history"].history_entry(request.match_info["sid"])
    if not row or not row.get("agent_visible"):
        raise KeyError(request.match_info["sid"])
    await request.app["history"].delete_history_entry(request.match_info["sid"])
    await request.app["events"].emit(
        "history_entry_deleted", session_id=request.match_info["sid"], source="user"
    )
    return json_response({"ok": True, "native_transcript_deleted": False})


async def list_events(request: web.Request) -> web.Response:
    return json_response(
        await request.app["history"].events(
            float(request.query.get("since", 0)),
            request.query.get("session"),
            min(int(request.query.get("limit", 500)), 2000),
            int(request.query.get("after_seq", 0)),
        )
    )


async def get_settings(request: web.Request) -> web.Response:
    return json_response(request.app["settings_store"].all())


async def put_settings(request: web.Request) -> web.Response:
    profile = request.match_info["profile"]
    updated = request.app["settings_store"].update(profile, await request.json())
    await request.app["events"].emit("settings_changed", source="user", profile=profile)
    return json_response({"profile": profile, "settings": updated})


def _clipboard_store(request: web.Request) -> ClipboardStore:
    store: ClipboardStore = request.app["clipboard"]
    return store


async def _emit_clipboard_changed(request: web.Request, reason: str, entry_id: str = "") -> None:
    """Announce a ring change so other clients refetch.

    The payload carries no copied text: these events are persisted in the history
    event log, and putting clipboard contents there would defeat the point of the
    memory-only default.
    """

    await request.app["events"].emit(
        "clipboard_changed",
        source="user",
        reason=reason,
        entry_id=entry_id,
        count=len(_clipboard_store(request).entries()),
    )


async def list_clipboard_entries(request: web.Request) -> web.Response:
    store = _clipboard_store(request)
    await store.prune()
    return json_response(
        {
            **store.status(),
            "entries": [entry.snapshot() for entry in store.entries()],
        }
    )


async def capture_clipboard_entry(request: web.Request) -> web.Response:
    store = _clipboard_store(request)
    body = await request.json() if request.can_read_body else {}
    entry, reason = await store.capture(
        body.get("text"),
        source=str(body.get("source") or ""),
        session_id=body.get("session_id"),
        project_id=body.get("project_id"),
        device=str(body.get("device") or ""),
    )
    if entry is not None:
        await _emit_clipboard_changed(request, reason, entry.id)
    return json_response(
        {
            "stored": entry is not None,
            "reason": reason,
            "entry": entry.snapshot() if entry else None,
        },
        201 if reason == "stored" else 200,
    )


async def get_clipboard_entry(request: web.Request) -> web.Response:
    entry = _clipboard_store(request).entry(request.match_info["entry_id"])
    if entry is None:
        raise KeyError(request.match_info["entry_id"])
    return json_response(entry.snapshot(include_text=True))


async def patch_clipboard_entry(request: web.Request) -> web.Response:
    body = await request.json() if request.can_read_body else {}
    if "pinned" not in body:
        raise ValueError("pinned is required")
    entry = await _clipboard_store(request).set_pinned(
        request.match_info["entry_id"], bool(body["pinned"])
    )
    await _emit_clipboard_changed(request, "pinned" if entry.pinned else "unpinned", entry.id)
    return json_response(entry.snapshot())


async def delete_clipboard_entry(request: web.Request) -> web.Response:
    entry_id = request.match_info["entry_id"]
    if not await _clipboard_store(request).delete(entry_id):
        raise KeyError(entry_id)
    await _emit_clipboard_changed(request, "deleted", entry_id)
    return json_response({"ok": True})


async def clear_clipboard_entries(request: web.Request) -> web.Response:
    include_pinned = request.query.get("include_pinned", "").lower() in {"1", "true", "yes"}
    removed = await _clipboard_store(request).clear(include_pinned=include_pinned)
    await _emit_clipboard_changed(request, "cleared")
    return json_response({"ok": True, "removed": removed})


async def get_vapid_public_key(request: web.Request) -> web.Response:
    return json_response({"key": request.app["push_store"].application_server_key})


async def push_subscribe(request: web.Request) -> web.Response:
    body = await request.json()
    profile = str(body.get("profile") or "mobile")
    request.app["push_store"].add(body.get("subscription"), profile)
    return json_response({"ok": True})


async def push_unsubscribe(request: web.Request) -> web.Response:
    body = await request.json()
    endpoint = str(body.get("endpoint") or "")
    if not endpoint:
        raise ValueError("endpoint is required")
    request.app["push_store"].remove(endpoint)
    return json_response({"ok": True})


async def push_presence(request: web.Request) -> web.Response:
    body = await request.json()
    endpoint = str(body.get("endpoint") or "")
    if not endpoint:
        raise ValueError("endpoint is required")
    ttl = body.get("ttl")
    request.app["push_store"].set_presence(
        endpoint, bool(body.get("focused")), float(ttl) if isinstance(ttl, (int, float)) else 90.0
    )
    return json_response({"ok": True})


async def get_device_presence(request: web.Request) -> web.Response:
    """Which devices the daemon believes are in use, and why.

    The suppression it feeds is invisible by construction — the symptom of getting
    it wrong is a notification that never arrives — so the inputs are readable.
    """
    return json_response(request.app["device_presence"].snapshot())


async def list_notifications(request: web.Request) -> web.Response:
    hooks: MetaHookEngine = request.app["hooks"]
    automation = await request.app["automation_store"].notifications(limit=200)
    return json_response(
        {
            "notifications": hooks.notifications,
            "deliveries": [item.snapshot() for item in hooks.deliveries[-100:]],
            "automation": automation,
        }
    )


async def voice_status(request: web.Request) -> web.Response:
    voice: VoiceService = request.app["voice"]
    return json_response(await voice.status())


async def voice_transcribe(request: web.Request) -> web.Response:
    voice: VoiceService = request.app["voice"]
    session = request.app["sessions"].resolve(request.match_info["sid"])
    if not is_agent_harness(session.record.backend):
        return json_response({"error": "conversation mode requires an agent session"}, 409)
    if request.content_type not in {"audio/wav", "audio/x-wav", "application/octet-stream"}:
        return json_response({"error": "voice transcription requires WAV audio"}, 415)
    if request.content_length is not None and request.content_length > 2 * 1024 * 1024:
        return json_response({"error": "voice utterance must not exceed 2 MiB"}, 413)
    try:
        audio = await request.read()
        text = await voice.transcribe_wav(audio)
    except VoiceError as exc:
        return json_response({"error": str(exc)}, 409)
    return json_response({"text": text})


async def voice_submit(request: web.Request) -> web.Response:
    voice: VoiceService = request.app["voice"]
    session = request.app["sessions"].resolve(request.match_info["sid"])
    if not delivers_prompts_through_pty(session.record.backend):
        return json_response({"error": "conversation mode requires an agent session"}, 409)
    if session.record.state in {"exited", "crashed"}:
        return json_response({"error": "the agent session has ended"}, 409)
    body = await request.json()
    text = str(body.get("text") or "").strip()
    utterance_id = str(body.get("utterance_id") or "").strip()
    if not utterance_id or len(utterance_id) > 100:
        raise ValueError("utterance_id is required and must be at most 100 characters")
    if not text or len(text) > 20_000:
        raise ValueError("voice prompt must contain 1–20000 characters")
    if any(ord(character) < 32 and character not in {"\t", "\n"} for character in text):
        raise ValueError("voice prompt contains terminal control characters")
    if not voice.claim_submission(utterance_id):
        return json_response({"ok": True, "duplicate": True})
    _record_operator_input(request.app["events"], session, f"{text}\r", source="voice")
    await request.app["events"].emit(
        "voice_prompt_submitted",
        session_id=session.record.id,
        source="voice",
        characters=len(text),
    )
    return json_response({"ok": True, "duplicate": False, "characters": len(text)})


async def voice_interrupt(request: web.Request) -> web.Response:
    session = request.app["sessions"].resolve(request.match_info["sid"])
    if not delivers_prompts_through_pty(session.record.backend):
        return json_response({"error": "conversation mode requires an agent session"}, 409)
    if session.record.state in {"exited", "crashed"}:
        return json_response({"ok": True, "already_ended": True})
    _record_operator_input(request.app["events"], session, "\x03", source="voice")
    await request.app["events"].emit(
        "voice_agent_interrupted", session_id=session.record.id, source="voice"
    )
    return json_response({"ok": True, "already_ended": False})


async def session_last_reply(request: web.Request) -> web.Response:
    """Return normalized assistant text without routing through terminal OSC 52."""
    session = request.app["sessions"].resolve(request.match_info["sid"])
    if not has_observable_transcript(session.record.backend):
        return json_response({"error": "last reply is available only for agent sessions"}, 409)
    path = session.transcript_path
    if not path or not path.exists():
        return json_response({"error": "the agent transcript is not available yet"}, 409)
    try:
        transcript = await TranscriptSliceService().build(
            path,
            session.record.backend,
            "last_n_messages",
            max_bytes=MAX_SLICE_BYTES,
        )
    except (OSError, TimeoutError, ValueError) as exc:
        return json_response({"error": str(exc) or "the agent transcript could not be read"}, 409)
    text = last_reply_text(transcript.messages)
    if not text:
        return json_response(
            {"error": "no assistant reply text was found in the recent transcript"}, 409
        )
    return json_response({"text": text, "agent_run_id": session.record.agent_run_id})


# A parse this misses is a blank reading column, never a wrong one, so the
# budget is generous: the largest Codex rollout on record (550 MB) parses in
# about a second, and the byte cap in `conversation_view` bounds the rest.
CONVERSATION_PARSE_TIMEOUT_SECONDS = 5.0


async def session_transcript(request: web.Request) -> web.Response:
    """The focused session's readable conversation, for the drawer's reader tab.

    Deliberately NOT `/api/history/{sid}/transcript`: that endpoint reindexes the
    run's searchable messages and loads its annotations on every call, which is
    right for opening a history entry once and wrong for a surface that refreshes
    whenever a turn ends. This one only reads.

    Every "there is nothing to show" case answers 200 with a `reason` rather than
    an error status. A shell pane and an agent that has not written its first
    record yet are ordinary states of a passive view, not failures, and the tab
    renders a sentence for each.
    """
    session = request.app["sessions"].resolve(request.match_info["sid"])
    record = session.record
    try:
        limit = int(request.query.get("limit") or CONVERSATION_DEFAULT_LIMIT)
    except ValueError:
        raise web.HTTPBadRequest(text="limit must be an integer") from None
    limit = max(1, min(limit, CONVERSATION_MAX_LIMIT))
    empty: dict[str, Any] = {
        "session_id": record.id,
        "agent_run_id": record.agent_run_id,
        "backend": record.backend,
        # The transcript observer can end up following a conversation that is no
        # longer this PTY's. The reader is the one surface where that is plainly
        # visible, so it reports the doubt instead of presenting a sibling's
        # conversation as this session's.
        "observation_stale_since": record.observation_stale_since,
        "messages": [],
        "hidden": 0,
        "truncated": False,
        "reason": None,
    }
    if not has_observable_transcript(record.backend):
        return json_response({**empty, "reason": "not_agent"})
    path = session.transcript_path
    if path is None or not path.is_file():
        return json_response({**empty, "reason": "no_transcript"})
    try:
        view = await asyncio.wait_for(
            asyncio.to_thread(conversation_view_cached, path, record.backend, limit=limit),
            timeout=CONVERSATION_PARSE_TIMEOUT_SECONDS,
        )
    except (OSError, TimeoutError):
        return json_response({**empty, "reason": "unreadable"})
    return json_response({**empty, **view})


async def session_skills(request: web.Request) -> web.Response:
    """The skills this session's CLI can see, read from the directories it reads.

    Scoped to the session because both inputs are: the backend decides which
    roots exist and how a skill is invoked, and the *live* cwd decides which repo
    skills apply — Codex resolves `.codex/skills` and `.agents/skills` from its
    working directory, so a session sitting in a worktree sees a different set
    than one in the primary checkout of the same Project.
    """
    session = request.app["sessions"].resolve(request.match_info["sid"])
    backend = session.record.backend
    if not is_agent_harness(backend):
        return json_response({"error": "skills are available only for agent sessions"}, 409)
    record = session.record
    cwd = Path(
        (record.runtime_cwd if record.runtime_cwd_live else None)
        or record.run_cwd
        or record.spawn_cwd
        or record.cwd
    )
    if not cwd.is_dir():
        cwd = Path(record.spawn_cwd or record.cwd)
    payload = await asyncio.to_thread(
        discover_skills,
        backend,
        cwd,
        refresh=request.query.get("refresh") in {"1", "true"},
    )
    # Conversation rollover does not restart the CLI. Root sessions therefore
    # retain their process start; promoted shell sessions retain the promotion
    # timestamp rather than treating every /clear or /new as a skill reload.
    started = _agent_loaded_at(session)
    skills = [
        {**skill, "added_after_start": skill["mtime"] > started} for skill in payload["skills"]
    ]
    return json_response(
        {
            **payload,
            "skills": skills,
            "agent_loaded_at": started,
            "agent_run_started_at": record.agent_run_started_at or record.created_at,
        }
    )


def _agent_loaded_at(session: Any) -> float:
    """Start of the live CLI process generation, not its current conversation."""
    record = session.record
    if record.agent_loaded_at is not None:
        return float(record.agent_loaded_at)
    if record.spawn_backend == record.backend:
        return float(record.created_at)
    return float(
        getattr(session, "agent_promoted_at", None)
        or record.agent_run_started_at
        or record.created_at
    )


async def session_agent_environment(request: web.Request) -> web.Response:
    """Return a bounded passive inventory for the focused agent CLI."""
    session = request.app["sessions"].resolve(request.match_info["sid"])
    record = session.record
    if not is_agent_harness(record.backend):
        return json_response(
            {"error": "agent environment is available only for agent sessions"}, 409
        )
    cwd = Path(
        (record.runtime_cwd if record.runtime_cwd_live else None)
        or record.run_cwd
        or record.spawn_cwd
        or record.cwd
    )
    if not cwd.is_dir():
        cwd = Path(record.spawn_cwd or record.cwd)
    refresh = request.query.get("refresh") in {"1", "true"}
    payload = await asyncio.to_thread(
        discover_agent_environment,
        backend=record.backend,
        cwd=cwd,
        executable=record.exe,
        args=list(record.args),
        model=record.model,
        loaded_at=_agent_loaded_at(session),
        run_started_at=record.agent_run_started_at,
        refresh=refresh,
    )
    if refresh:
        log.info(
            "agent environment refreshed session=%s backend=%s sources=%d sections=%d",
            record.id,
            record.backend,
            len(payload["sources"]),
            len(payload["sections"]),
        )
    return json_response(payload)


async def voice_generate(request: web.Request) -> web.Response:
    voice: VoiceService = request.app["voice"]
    session = request.app["sessions"].resolve(request.match_info["sid"])
    try:
        clip = await voice.generate(session.record.id, trigger="manual")
    except VoiceError as exc:
        return json_response({"error": str(exc)}, 409)
    return json_response(clip)


async def list_voice_clips(request: web.Request) -> web.Response:
    store: VoiceStore = request.app["voice_store"]
    session_id = request.query.get("session") or None
    if session_id:
        session_id = request.app["sessions"].resolve(session_id).record.id
    rows = await store.clips(
        session_id=session_id,
        agent_run_id=request.query.get("run") or None,
        limit=int(request.query.get("limit") or 20),
    )
    return json_response({"items": [clip_snapshot(row) for row in rows]})


async def voice_clip_audio(request: web.Request) -> web.StreamResponse:
    store: VoiceStore = request.app["voice_store"]
    row = await store.clip(request.match_info["clip_id"])
    if not row or row["status"] != "ready":
        raise web.HTTPNotFound(text="voice clip not found")
    path = Path(str(row["file_path"]))
    if not path.is_file():
        raise web.HTTPNotFound(text="voice clip audio is no longer cached")
    content_type = "audio/mpeg" if row["format"] == "mp3" else "audio/wav"
    return web.FileResponse(path, headers={"Content-Type": content_type})


async def delete_voice_clip(request: web.Request) -> web.Response:
    store: VoiceStore = request.app["voice_store"]
    file_path = await store.delete_clip(request.match_info["clip_id"])
    if file_path:
        Path(file_path).unlink(missing_ok=True)
    return json_response({"ok": True})


async def get_usage(request: web.Request) -> web.Response:
    usage: UsageManager = request.app["usage"]
    return json_response(usage.snapshot())


async def refresh_usage(request: web.Request) -> web.Response:
    usage: UsageManager = request.app["usage"]
    body = await request.json() if request.can_read_body else {}
    return json_response(await usage.refresh(body.get("provider")))


async def clear_usage_cache(request: web.Request) -> web.Response:
    usage: UsageManager = request.app["usage"]
    await request.app["events"].emit("usage_cache_cleared", source="settings")
    return json_response(usage.clear())


async def operational_telemetry(request: web.Request) -> web.Response:
    telemetry: OperationalTelemetryStore = request.app["telemetry"]
    return json_response(
        await telemetry.snapshot(
            provider=request.query.get("provider"),
            account_id=request.query.get("account"),
            limit=int(request.query.get("limit", 200)),
        )
    )


async def review_quota_reset(request: web.Request) -> web.Response:
    body = await request.json()
    resolution = str(body.get("resolution") or "")
    telemetry: OperationalTelemetryStore = request.app["telemetry"]
    reviewed = await telemetry.review_quota_reset(request.match_info["reset_id"], resolution)
    await request.app["events"].emit(
        "quota_reset_reviewed",
        source="user",
        reset_id=reviewed["id"],
        provider=reviewed["provider"],
        resolution=reviewed["review_status"],
    )
    return json_response({"item": reviewed, "reset_alert": await telemetry.reset_summary()})


async def get_provider_accounts(request: web.Request) -> web.Response:
    accounts: ProviderAccountManager = request.app["provider_accounts"]
    snapshot = await accounts.reconcile_current()
    telemetry: OperationalTelemetryStore = request.app["telemetry"]
    latest = await telemetry.latest_quota_by_account()
    for account in snapshot["accounts"]:
        conflict = account.get("conflict")
        if conflict and not conflict.get("is_primary"):
            # Durable samples for a duplicate slot are the primary account's
            # numbers; showing them again is the mirrored-usage illusion.
            continue
        if account["id"] in latest:
            account["quota"] = latest[account["id"]]
    snapshot["reset_alert"] = await telemetry.reset_summary()
    return json_response(snapshot)


async def get_provider_account_audit(request: web.Request) -> web.Response:
    accounts: ProviderAccountManager = request.app["provider_accounts"]
    limit = max(1, min(1000, int(request.query.get("limit") or 100)))
    return json_response({"items": accounts.audit_entries(limit)})


async def refresh_provider_accounts(request: web.Request) -> web.Response:
    accounts: ProviderAccountManager = request.app["provider_accounts"]
    body = await request.json() if request.can_read_body else {}
    return json_response(await accounts.refresh(body.get("account_id"), force_identity_probe=True))


async def verify_provider_accounts(request: web.Request) -> web.Response:
    accounts: ProviderAccountManager = request.app["provider_accounts"]
    return json_response(await accounts.verify_identities())


async def capture_provider_account(request: web.Request) -> web.Response:
    accounts: ProviderAccountManager = request.app["provider_accounts"]
    body = await request.json() if request.can_read_body else {}
    return json_response(
        await accounts.capture_current(
            request.match_info["provider"],
            label=body.get("label"),
            replace_id=body.get("replace_id"),
        )
    )


async def login_provider_account(request: web.Request) -> web.Response:
    accounts: ProviderAccountManager = request.app["provider_accounts"]
    body = await request.json() if request.can_read_body else {}
    return json_response(
        await accounts.login_and_capture(
            request.match_info["provider"],
            label=body.get("label"),
            replace_id=body.get("replace_id"),
        )
    )


async def patch_provider_account(request: web.Request) -> web.Response:
    accounts: ProviderAccountManager = request.app["provider_accounts"]
    body = await request.json()
    return json_response(
        await accounts.rename(
            request.match_info["provider"], request.match_info["account_id"], str(body["label"])
        )
    )


async def select_provider_account(request: web.Request) -> web.Response:
    accounts: ProviderAccountManager = request.app["provider_accounts"]
    return json_response(
        await accounts.select(
            request.match_info["provider"],
            request.match_info["account_id"],
        )
    )


async def adopt_provider_account(request: web.Request) -> web.Response:
    accounts: ProviderAccountManager = request.app["provider_accounts"]
    return json_response(
        await accounts.adopt(request.match_info["provider"], request.match_info["account_id"])
    )


async def purge_provider_account_telemetry(request: web.Request) -> web.Response:
    accounts: ProviderAccountManager = request.app["provider_accounts"]
    body = await request.json() if request.can_read_body else {}
    since = body.get("since")
    return json_response(
        await accounts.purge_telemetry(
            request.match_info["provider"],
            request.match_info["account_id"],
            since=float(since) if since is not None else None,
        )
    )


async def remove_provider_account(request: web.Request) -> web.Response:
    accounts: ProviderAccountManager = request.app["provider_accounts"]
    return json_response(
        await accounts.remove(request.match_info["provider"], request.match_info["account_id"])
    )


async def list_processes(request: web.Request) -> web.Response:
    session_id = request.query.get("session")
    include_ended = request.query.get("include_ended", "").lower() in {"1", "true", "yes"}
    # Opt-in because unique-set-size sampling walks every working set. Views the user
    # opened ask for it; the background rail poll does not.
    unique_memory = request.query.get("unique_memory", "").lower() in {"1", "true", "yes"}
    inspector: ProcessInspector = request.app["process_inspector"]
    return json_response(
        await inspector.snapshot(session_id, include_ended=include_ended)
        if session_id
        else await inspector.snapshot_all(include_ended=include_ended, unique_memory=unique_memory)
    )


async def process_action(request: web.Request) -> web.Response:
    body = await request.json()
    inspector: ProcessInspector = request.app["process_inspector"]
    return json_response(
        await inspector.act(
            str(body["session_id"]),
            int(body["pid"]),
            str(body["action"]),
            identity_id=str(body.get("identity_id") or "") or None,
        )
    )


async def list_previews(request: web.Request) -> web.Response:
    previews: PreviewRegistry = request.app["previews"]
    # Reap on read so a client never sees a preview whose server has stopped; the
    # browser drops the matching tab and sidebar row when it disappears from here.
    previews.prune()
    return json_response(await previews.list(request.query.get("session")))


async def create_preview(request: web.Request) -> web.Response:
    body = await request.json()
    previews: PreviewRegistry = request.app["previews"]
    item = await previews.register(
        str(body["session_id"]), str(body["url"]), approved=bool(body.get("approved"))
    )
    if body.get("attach", True):
        projects: ProjectManager = request.app["projects"]
        project = projects.projects[item.project_id]
        # A preview belongs beside whatever spawned it: group it as a tab in the
        # owning session's region instead of splitting off an unrelated one. Fall
        # back to a split when that session has no terminal in this layout.
        grouped = stack_leaf(project.layout, "preview", item.id, target_id=item.session_id)
        project.layout = (
            grouped
            if grouped is not None
            else attach_leaf(
                project.layout,
                "preview",
                item.id,
                target_id=str(body.get("target_session_id") or "") or None,
                direction=str(body.get("direction") or "horizontal"),
            )
        )
        project.layout_revision += 1
        await projects.history.upsert_project(project)
    else:
        project = request.app["projects"].projects[item.project_id]
    await request.app["events"].emit(
        "preview_registered",
        session_id=item.session_id,
        source="user",
        preview_id=item.id,
        url=item.url,
    )
    return json_response({"preview": item.snapshot(), "project": project.snapshot()}, 201)


async def delete_preview(request: web.Request) -> web.Response:
    previews: PreviewRegistry = request.app["previews"]
    preview_id = request.match_info["preview_id"]
    item = previews.items.get(preview_id)
    previews.remove(preview_id)
    await request.app["events"].emit(
        "preview_removed",
        session_id=item.session_id if item else None,
        source="user",
        preview_id=preview_id,
    )
    return json_response({"ok": True})


async def capture_preview(request: web.Request) -> web.Response:
    """Headlessly screenshot a session-owned loopback preview for the agent.

    Returns a typed unavailable state when the optional Playwright backend is not
    installed. The image is saved server-side and its path returned; the browser
    inserts a reference into the target agent's composer — this route never writes
    a PTY or submits anything.
    """
    previews: PreviewRegistry = request.app["previews"]
    config: Config = request.app["config"]
    item = previews.items.get(request.match_info["preview_id"])
    if not item:
        raise ValueError("unknown preview")
    body = await request.json() if request.can_read_body else {}
    if not capture_available():
        return json_response(
            {
                "available": False,
                "reason": "Preview capture needs the optional Playwright backend.",
                "install": PREVIEW_CAPTURE_INSTALL_HINT,
            }
        )
    viewport = str(body.get("viewport") or "responsive")
    width = int(body.get("width") or VIEWPORT_WIDTHS.get(viewport, 1280))
    height = int(body.get("height") or 800)
    raw_clip = body.get("clip")
    clip = raw_clip if isinstance(raw_clip, dict) else None
    url = f"http://{item.host}:{item.port}/"
    # Save into the owning project's .swe-mux so a local agent can read it without
    # hunting through the mux data dir; fall back to the data dir if unresolvable.
    session = request.app["sessions"].sessions.get(item.session_id)
    root: str | None = None
    if session is not None:
        record = session.record
        root = record.project_root or record.spawn_project_root
        if not root and record.project_id:
            project = request.app["projects"].projects.get(record.project_id)
            root = project.root if project else None
    shot_dir = (Path(root) / ".swe-mux" if root else config.data_dir) / "preview-shots"
    out_path = shot_dir / f"{item.id}-{uuid4().hex[:8]}.png"
    try:
        await capture_loopback(url, out_path, width=width, height=height, clip=clip)
    except Exception as exc:  # noqa: BLE001 - a capture failure must not 500
        log.exception("preview capture failed for %s", url)
        message = str(exc).splitlines()[0][:300] if str(exc).strip() else exc.__class__.__name__
        return json_response({"available": True, "error": f"Capture failed: {message}"}, 502)
    return json_response(
        {
            "available": True,
            "path": str(out_path),
            "url": url,
            "width": width,
            "height": height,
            "region": bool(clip),
        }
    )


_PROXY_RESPONSE_HEADERS = {
    "accept-ranges",
    "cache-control",
    "content-language",
    "content-type",
    "etag",
    "expires",
    "last-modified",
}
_PROXY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}


def _preview_runtime_bridge(prefix: str, project_routes: dict[str, str] | None = None) -> str:
    encoded = json.dumps(prefix)
    encoded_routes = json.dumps(project_routes or {}, separators=(",", ":"))
    return f"""<script>(function(){{
const prefix={encoded};
const projectRoutes={encoded_routes};
// A client-side router reads location.pathname directly and Location is not
// patchable, so the mount point cannot be hidden from it the way asset URLs are.
// Advertise it instead: an app passes this to its router's basename (React Router,
// vue-router, SvelteKit) and falls back to "/" when it is not inside a preview.
window.__MUX_PREVIEW_BASE__=prefix;
const canonicalOrigin=function(url){{
  let protocol=url.protocol;
  if(protocol==="ws:")protocol="http:";
  if(protocol==="wss:")protocol="https:";
  let hostname=url.hostname.toLowerCase();
  if(hostname==="localhost"||hostname==="0.0.0.0")hostname="127.0.0.1";
  if(hostname==="[::]"||hostname==="::")hostname="[::1]";
  if(hostname.includes(":" )&&!hostname.startsWith("["))hostname="["+hostname+"]";
  const defaultPort=(protocol==="http:"&&url.port==="80")||(protocol==="https:"&&url.port==="443");
  return protocol+"//"+hostname+(url.port&&!defaultPort?":"+url.port:"");
}};
const route=function(value){{
  try {{
    const url=new URL(String(value),location.href);
    const projectPrefix=projectRoutes[canonicalOrigin(url)];
    if(projectPrefix){{
      url.protocol=location.protocol==="https:"?(url.protocol.startsWith("ws")?"wss:":"https:"):(url.protocol.startsWith("ws")?"ws:":"http:");
      url.host=location.host;
      url.pathname=projectPrefix+url.pathname.replace(/^\\/+/,"");
    }} else if(url.host===location.host&&!url.pathname.startsWith("/preview/")){{
      url.pathname=prefix+url.pathname.replace(/^\\/+/,"");
    }}
    return url.toString();
  }} catch (_) {{ return value; }}
}};
const urlAttributes=new Set(["src","href","action"]);
const routeAttribute=function(value){{
  const raw=String(value);
  if(raw.startsWith("/")&&!raw.startsWith("//"))return route(raw);
  try {{
    const url=new URL(raw,location.href);
    if(projectRoutes[canonicalOrigin(url)])return route(raw);
  }} catch (_) {{}}
  return value;
}};
const rewriteMarkup=function(value){{
  const source=String(value);
  return source.replace(/(\\b(?:src|href|action)\\s*=\\s*["'])([^"']+)/gi,
    function(_,start,target){{return start+routeAttribute(target);}});
}};
const nativeSetAttribute=Element.prototype.setAttribute;
Element.prototype.setAttribute=function(name,value){{
  const next=urlAttributes.has(String(name).toLowerCase())?routeAttribute(value):value;
  return nativeSetAttribute.call(this,name,next);
}};
const patchMarkupProperty=function(name){{
  const descriptor=Object.getOwnPropertyDescriptor(Element.prototype,name);
  if(!descriptor||typeof descriptor.set!=="function")return;
  try {{
    Object.defineProperty(Element.prototype,name,{{
      configurable:descriptor.configurable,
      enumerable:descriptor.enumerable,
      get:descriptor.get,
      set:function(value){{descriptor.set.call(this,rewriteMarkup(value));}}
    }});
  }} catch (_) {{}}
}};
patchMarkupProperty("innerHTML");
patchMarkupProperty("outerHTML");
const nativeInsertAdjacentHTML=Element.prototype.insertAdjacentHTML;
Element.prototype.insertAdjacentHTML=function(position,value){{
  return nativeInsertAdjacentHTML.call(this,position,rewriteMarkup(value));
}};
const patchUrlProperty=function(constructorName,name){{
  const constructor=window[constructorName];
  if(!constructor)return;
  const descriptor=Object.getOwnPropertyDescriptor(constructor.prototype,name);
  if(!descriptor||typeof descriptor.set!=="function")return;
  try {{
    Object.defineProperty(constructor.prototype,name,{{
      configurable:descriptor.configurable,
      enumerable:descriptor.enumerable,
      get:descriptor.get,
      set:function(value){{descriptor.set.call(this,routeAttribute(value));}}
    }});
  }} catch (_) {{}}
}};
[
  ["HTMLImageElement","src"],
  ["HTMLScriptElement","src"],
  ["HTMLIFrameElement","src"],
  ["HTMLSourceElement","src"],
  ["HTMLMediaElement","src"],
  ["HTMLLinkElement","href"],
  ["HTMLAnchorElement","href"],
  ["HTMLAreaElement","href"],
  ["HTMLFormElement","action"]
].forEach(function(entry){{patchUrlProperty(entry[0],entry[1]);}});
const rerouteOwnAttributes=function(element){{
  urlAttributes.forEach(function(name){{
    if(!element.hasAttribute(name))return;
    const current=element.getAttribute(name);
    const next=routeAttribute(current);
    if(next!==current)nativeSetAttribute.call(element,name,next);
  }});
}};
const rerouteTree=function(node){{
  if(!(node instanceof Element))return;
  rerouteOwnAttributes(node);
  node.querySelectorAll("[src],[href],[action]").forEach(rerouteOwnAttributes);
}};
new MutationObserver(function(records){{
  records.forEach(function(record){{
    if(record.type==="attributes")rerouteOwnAttributes(record.target);
    else record.addedNodes.forEach(rerouteTree);
  }});
}}).observe(document,{{subtree:true,childList:true,attributes:true,
  attributeFilter:["src","href","action"]}});
const NativeWebSocket=window.WebSocket;
window.WebSocket=class extends NativeWebSocket{{
  constructor(url,protocols){{super(route(url),protocols);}}
}};
const nativeFetch=window.fetch.bind(window);
window.fetch=function(input,init){{
  if(input instanceof Request) input=new Request(route(input.url),input);
  else input=route(input);
  return nativeFetch(input,init);
}};
const nativeOpen=XMLHttpRequest.prototype.open;
XMLHttpRequest.prototype.open=function(method,url){{
  const args=Array.prototype.slice.call(arguments);args[1]=route(url);
  return nativeOpen.apply(this,args);
}};
if(window.EventSource){{
  const NativeEventSource=window.EventSource;
  window.EventSource=class extends NativeEventSource{{
    constructor(url,init){{super(route(url),init);}}
  }};
}}
}})();</script>"""


def rewrite_preview_html(
    data: bytes, prefix: str, project_routes: dict[str, str] | None = None
) -> bytes:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return data
    text = re.sub(
        r'(?P<attr>\b(?:src|href|action)\s*=\s*["\'])/',
        rf"\g<attr>{prefix}",
        text,
        flags=re.IGNORECASE,
    )
    # Inline module scripts carry specifiers no attribute rewrite can reach --
    # @vitejs/plugin-react's refresh preamble imports "/@react-refresh" from an inline
    # <script type="module">. Left alone it 404s on the mux origin, the preamble never
    # runs, and every transformed module throws "can't detect preamble": a white page.
    # Runs before the bridge is injected so the bridge's own source is never rewritten.
    text = _rewrite_inline_scripts(text, prefix)
    bridge = _preview_runtime_bridge(prefix, project_routes)
    head = re.search(r"<head(?:\s[^>]*)?>", text, flags=re.IGNORECASE)
    if head:
        text = text[: head.end()] + bridge + text[head.end() :]
    else:
        text = bridge + text
    return text.encode("utf-8")


def rewrite_preview_css(data: bytes, prefix: str) -> bytes:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return data
    text = re.sub(
        r"(?P<start>url\(\s*[\"']?)/(?P<tail>[^)\"']+)",
        rf"\g<start>{prefix}\g<tail>",
        text,
        flags=re.IGNORECASE,
    )
    return text.encode("utf-8")


def rewrite_preview_javascript(data: bytes, prefix: str) -> bytes:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return data
    return _rewrite_javascript_text(text, prefix).encode("utf-8")


_JS_ROOT_SPECIFIER = re.compile(r"(?P<start>\b(?:from\s*|import\s*|import\s*\(\s*)[\"'])/")
_SCRIPT_ELEMENT = re.compile(
    r"(?P<open><script\b(?P<attrs>[^>]*)>)(?P<body>.*?)(?P<close></script\s*>)",
    flags=re.IGNORECASE | re.DOTALL,
)
_SCRIPT_TYPE = re.compile(r"""\btype\s*=\s*["']?([^"'\s>]+)""", flags=re.IGNORECASE)
_SCRIPT_SRC = re.compile(r"\bsrc\s*=", flags=re.IGNORECASE)


def _rewrite_javascript_text(text: str, prefix: str) -> str:
    return _JS_ROOT_SPECIFIER.sub(rf"\g<start>{prefix}", text)


def _rewrite_inline_scripts(text: str, prefix: str) -> str:
    """Prefix root-absolute module specifiers inside inline <script> bodies.

    Only bodies that the browser executes as JavaScript are touched; a data block
    (``application/json``, ``importmap``, ``text/template``) keeps its exact bytes.
    """

    def replace(match: re.Match[str]) -> str:
        attrs = match.group("attrs")
        if _SCRIPT_SRC.search(attrs):
            return match.group(0)
        declared = _SCRIPT_TYPE.search(attrs)
        mime = declared.group(1).casefold() if declared else ""
        if mime and mime != "module" and not ("javascript" in mime or "ecmascript" in mime):
            return match.group(0)
        body = _rewrite_javascript_text(match.group("body"), prefix)
        return f"{match.group('open')}{body}{match.group('close')}"

    return _SCRIPT_ELEMENT.sub(replace, text)


def preview_target(item: Any, tail: str, query: str = "") -> tuple[str, str]:
    parsed = urlsplit(item.url)
    if parsed.scheme not in {"http", "https"} or parsed.hostname not in {"127.0.0.1", "::1"}:
        raise ValueError("preview registration is no longer a valid loopback destination")
    if parsed.username or parsed.password or parsed.fragment:
        raise ValueError("preview registration is invalid")
    path = f"{parsed.path.rstrip('/')}/{tail.lstrip('/')}"
    target = urlunsplit((parsed.scheme, parsed.netloc, path, query, ""))
    origin = urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))
    return target, origin


def _preview_request_headers(request: web.Request, upstream_origin: str) -> dict[str, str]:
    headers = {
        name: value
        for name, value in request.headers.items()
        if name.casefold() not in _PROXY_HOP_HEADERS
        and name.casefold()
        not in {
            "host",
            "origin",
            "referer",
            "content-length",
            "x-mux-hook-secret",
        }
        and not name.casefold().startswith("sec-websocket-")
    }
    headers["Origin"] = upstream_origin
    headers["Referer"] = f"{upstream_origin}/"
    return headers


async def _acquire_preview_slot(semaphore: asyncio.Semaphore) -> None:
    try:
        await asyncio.wait_for(semaphore.acquire(), timeout=0.1)
    except TimeoutError as exc:
        raise web.HTTPServiceUnavailable(
            text="preview proxy is at its concurrency limit",
            headers={"Retry-After": "1"},
        ) from exc


async def _proxy_websocket(request: web.Request, target: str, origin: str) -> web.WebSocketResponse:
    semaphore: asyncio.Semaphore = request.app["preview_ws_semaphore"]
    await _acquire_preview_slot(semaphore)
    offered_protocols = tuple(
        value.strip()
        for value in request.headers.get("Sec-WebSocket-Protocol", "").split(",")
        if value.strip()
    )
    client = ClientSession(timeout=ClientTimeout(total=None, sock_connect=10))
    upstream = None
    downstream = None
    try:
        upstream = await client.ws_connect(
            target,
            headers=_preview_request_headers(request, origin),
            protocols=offered_protocols,
            autoclose=False,
            autoping=False,
            max_msg_size=PREVIEW_WS_MESSAGE_BYTES,
        )
        selected = (upstream.protocol,) if upstream.protocol else ()
        downstream = web.WebSocketResponse(
            protocols=selected,
            autoclose=False,
            autoping=False,
            max_msg_size=PREVIEW_WS_MESSAGE_BYTES,
        )
        await downstream.prepare(request)

        async def relay(source: Any, destination: Any) -> None:
            # unsupervised-loop-ok: lives for one preview websocket, not the daemon.
            while True:
                message = await asyncio.wait_for(source.receive(), timeout=PREVIEW_WS_IDLE_SECONDS)
                if message.type == WSMsgType.TEXT:
                    await destination.send_str(message.data)
                elif message.type == WSMsgType.BINARY:
                    await destination.send_bytes(message.data)
                elif message.type == WSMsgType.PING:
                    await destination.ping(message.data)
                elif message.type == WSMsgType.PONG:
                    await destination.pong(message.data)
                elif message.type in {WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.ERROR}:
                    return

        async with asyncio.timeout(PREVIEW_WS_LIFETIME_SECONDS):
            tasks = {
                asyncio.create_task(relay(downstream, upstream)),
                asyncio.create_task(relay(upstream, downstream)),
            }
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            await asyncio.gather(*done, *pending, return_exceptions=True)
    except (ClientError, OSError, TimeoutError) as exc:
        if downstream is None:
            raise web.HTTPBadGateway(text=f"preview websocket unavailable: {exc}") from exc
        await downstream.close(code=1011, message=b"preview websocket unavailable")
    finally:
        if upstream is not None and not upstream.closed:
            with suppress(Exception):
                await upstream.close()
        if downstream is not None and not downstream.closed:
            with suppress(Exception):
                await downstream.close()
        await client.close()
        semaphore.release()
    if downstream is None:  # pragma: no cover - pre-prepare failures raise above
        raise web.HTTPBadGateway(text="preview websocket unavailable")
    return downstream


async def preview_proxy(request: web.Request) -> web.StreamResponse:
    if request.method in {"CONNECT", "TRACE"}:
        raise web.HTTPMethodNotAllowed(
            request.method, ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
        )
    preview_id = request.match_info["preview_id"]
    item = request.app["previews"].items.get(preview_id)
    if item is None:
        raise web.HTTPNotFound(text="preview registration not found")
    if item.session_id not in request.app["sessions"].sessions:
        raise web.HTTPGone(text="preview session is no longer live")
    tail = request.match_info.get("tail", "")
    previews = request.app["previews"]
    ensure_detected = getattr(previews, "ensure_detected", None)
    if ensure_detected is not None:
        await ensure_detected(item.project_id)
    routes_for_project = getattr(previews, "routes_for_project", None)
    project_routes = routes_for_project(item.project_id) if routes_for_project else {}
    target, origin = preview_target(item, tail, request.query_string)
    if request.headers.get("Upgrade", "").casefold() == "websocket":
        return await _proxy_websocket(request, target, origin)
    if request.content_length is not None and request.content_length > PREVIEW_REQUEST_BYTES:
        raise web.HTTPRequestEntityTooLarge(
            max_size=PREVIEW_REQUEST_BYTES, actual_size=request.content_length
        )
    body = await request.read()
    if len(body) > PREVIEW_REQUEST_BYTES:
        raise web.HTTPRequestEntityTooLarge(max_size=PREVIEW_REQUEST_BYTES, actual_size=len(body))
    semaphore: asyncio.Semaphore = request.app["preview_http_semaphore"]
    await _acquire_preview_slot(semaphore)
    try:
        # Per-operation timeouts (not a wall-clock total) so a legitimately large
        # or slow passthrough download is not aborted mid-stream, while a hung
        # upstream still trips sock_read and a dead loopback port fails fast.
        async with ClientSession(
            timeout=ClientTimeout(total=None, sock_connect=10, sock_read=30)
        ) as client:
            async with client.request(
                request.method,
                target,
                headers=_preview_request_headers(request, origin),
                data=body or None,
                allow_redirects=False,
            ) as upstream:
                if (
                    upstream.content_length is not None
                    and upstream.content_length > PREVIEW_RESPONSE_BYTES
                ):
                    raise web.HTTPRequestEntityTooLarge(
                        max_size=PREVIEW_RESPONSE_BYTES,
                        actual_size=upstream.content_length,
                    )
                content_type = upstream.headers.get("Content-Type", "")
                casefolded = content_type.casefold()
                prefix = f"/preview/{preview_id}/"
                needs_rewrite = (
                    "text/html" in casefolded
                    or "text/css" in casefolded
                    or any(m in casefolded for m in ("javascript", "ecmascript", "typescript"))
                )
                # Build the outbound headers (whitelist + Location reject/rewrite +
                # CORS-null) up front so an external redirect still fails with a 502
                # BEFORE any bytes are written on the streaming path.
                response_headers = {
                    name: value
                    for name, value in upstream.headers.items()
                    if name.casefold() in _PROXY_RESPONSE_HEADERS
                    and name.casefold() not in {"cache-control", "expires"}
                }
                # A Preview is a development viewport. Revalidate every resource so
                # replacing same-URL HTML, bundles, or images cannot require a new
                # port merely to escape an upstream max-age cache entry.
                response_headers["Cache-Control"] = "no-cache"
                location = upstream.headers.get("Location")
                if location:
                    resolved = urlsplit(origin)._replace(path="", query="", fragment="")
                    destination = urlsplit(location)
                    if destination.hostname and (
                        destination.hostname != resolved.hostname
                        or destination.port != resolved.port
                        or destination.scheme != resolved.scheme
                    ):
                        raise web.HTTPBadGateway(
                            text="preview upstream attempted an external redirect"
                        )
                    response_headers["Location"] = prefix + destination.path.lstrip("/")
                    if destination.query:
                        response_headers["Location"] += f"?{destination.query}"
                if request.headers.get("Origin") == "null":
                    response_headers["Access-Control-Allow-Origin"] = "null"
                    response_headers["Vary"] = "Origin"
                    if request.method == "OPTIONS":
                        response_headers["Access-Control-Allow-Methods"] = (
                            "GET, HEAD, POST, PUT, PATCH, DELETE, OPTIONS"
                        )
                        requested_headers = request.headers.get(
                            "Access-Control-Request-Headers", ""
                        )[:1000]
                        if requested_headers:
                            response_headers["Access-Control-Allow-Headers"] = requested_headers
                if request.method == "HEAD":
                    return web.Response(body=b"", status=upstream.status, headers=response_headers)
                if needs_rewrite:
                    # HTML/CSS/JS rewriting needs the whole body, so buffer it (with the
                    # running-total cap) and rewrite before responding.
                    chunks: list[bytes] = []
                    total = 0
                    async for chunk in upstream.content.iter_chunked(64 * 1024):
                        total += len(chunk)
                        if total > PREVIEW_RESPONSE_BYTES:
                            raise web.HTTPRequestEntityTooLarge(
                                max_size=PREVIEW_RESPONSE_BYTES, actual_size=total
                            )
                        chunks.append(chunk)
                    data = b"".join(chunks)
                    if "text/html" in casefolded:
                        data = rewrite_preview_html(data, prefix, project_routes)
                    elif "text/css" in casefolded:
                        data = rewrite_preview_css(data, prefix)
                    else:
                        data = rewrite_preview_javascript(data, prefix)
                    return web.Response(body=data, status=upstream.status, headers=response_headers)
                # Passthrough: stream unrewritten bodies straight through instead of
                # materialising up to PREVIEW_RESPONSE_BYTES in daemon RAM per request.
                response = web.StreamResponse(status=upstream.status, headers=response_headers)
                # Only advertise the upstream Content-Length for an identity-encoded
                # body. aiohttp auto-decompresses gzip/deflate/br, so we stream the
                # DECOMPRESSED bytes while ``upstream.content_length`` is the raw
                # (compressed) header value; copying it would make aiohttp truncate the
                # outbound body to the compressed length (a silent fail-open). Leaving
                # it unset makes aiohttp chunk-frame the decompressed stream. The
                # response never carries Content-Encoding (not whitelisted), so the
                # client correctly receives already-decompressed bytes.
                content_encoding = upstream.headers.get("Content-Encoding", "").strip().casefold()
                if (
                    content_encoding in ("", "identity")
                    and upstream.content_length is not None
                    and upstream.content_length <= PREVIEW_RESPONSE_BYTES
                ):
                    response.content_length = upstream.content_length
                # The security middleware stamps its headers only after the handler
                # returns, which is too late once we prepare() and stream ourselves.
                _apply_security_headers(response, request)
                await response.prepare(request)
                total = 0
                async for chunk in upstream.content.iter_chunked(64 * 1024):
                    total += len(chunk)
                    if total > PREVIEW_RESPONSE_BYTES:
                        # Headers are already sent, so a clean 413 is impossible: abort
                        # the connection so the client sees a broken transfer, never a
                        # well-formed response carrying a silently truncated body.
                        if request.transport is not None:
                            request.transport.close()
                        raise web.HTTPRequestEntityTooLarge(
                            max_size=PREVIEW_RESPONSE_BYTES, actual_size=total
                        )
                    await response.write(chunk)
                await response.write_eof()
                return response
    except (ClientError, OSError, TimeoutError) as exc:
        raise web.HTTPBadGateway(text=f"preview unavailable: {exc}") from exc
    finally:
        semaphore.release()


async def mcp_endpoint(request: web.Request) -> web.Response:
    """The mux MCP v0 endpoint: JSON-RPC over streamable HTTP (`mcp.py`).

    Same-host trust boundary per the 2026-07-28 decision (`ROADMAP.md` Phase
    4.5): loopback-only like hook ingress, bearer token as caller *identity*
    and Project read scope. Read-only end to end — the service exposes no tool
    that can enqueue, deliver, spawn, or write to a PTY.
    """
    if request.content_length is not None and request.content_length > MCP_BODY_BYTES:
        raise web.HTTPRequestEntityTooLarge(
            max_size=MCP_BODY_BYTES, actual_size=request.content_length
        )
    peer = request.transport.get_extra_info("peername") if request.transport else None
    host = peer[0] if peer else ""
    if host not in {"127.0.0.1", "::1"}:
        raise web.HTTPForbidden(text="the mux MCP endpoint is loopback-only")
    service: McpService = request.app["mcp"]
    try:
        caller = service.resolve_caller(request.headers.get("Authorization"))
    except McpAuthError as exc:
        return json_response({"error": str(exc)}, 401)
    now = time.monotonic()
    windows: dict[str, deque[float]] = request.app["mcp_rate_windows"]
    if len(windows) > HOOK_WINDOW_SWEEP_AT:
        live = request.app["sessions"].sessions
        for stale in [sid for sid in windows if sid not in live]:
            windows.pop(stale, None)
    window = windows.setdefault(caller.record.id, deque())
    while window and now - window[0] >= MCP_RATE_WINDOW_SECONDS:
        window.popleft()
    if len(window) >= MCP_RATE_LIMIT:
        raise web.HTTPTooManyRequests(
            text="MCP call rate limit exceeded", headers={"Retry-After": "5"}
        )
    window.append(now)
    try:
        message = json.loads(await request.read())
    except ValueError:
        return json_response(
            {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}},
            400,
        )
    if not isinstance(message, dict):
        # JSON-RPC batching was removed in protocol 2025-06-18; no client we
        # target sends it.
        return json_response(
            {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32600, "message": "expected a single JSON-RPC object"},
            },
            400,
        )
    response = await service.handle_rpc(caller, message)
    if response is None:
        return web.Response(status=202)
    return json_response(response)


async def hook_ingress(request: web.Request) -> web.Response:
    if request.content_length is not None and request.content_length > 256 * 1024:
        raise web.HTTPRequestEntityTooLarge(max_size=256 * 1024, actual_size=request.content_length)
    peer = request.transport.get_extra_info("peername") if request.transport else None
    host = peer[0] if peer else ""
    if host not in {"127.0.0.1", "::1"}:
        raise web.HTTPForbidden(text="hook ingress is loopback-only")
    sid = request.match_info["sid"]
    session = request.app["sessions"].resolve(sid)
    if session.record.state in {"exited", "crashed"}:
        raise web.HTTPGone(text="hook session has ended")
    supplied = request.headers.get("X-Mux-Hook-Secret", "")
    if not secrets.compare_digest(supplied, session.hook_secret):
        raise web.HTTPForbidden(text="invalid hook secret")
    now = time.monotonic()
    windows: dict[str, deque[float]] = request.app["hook_ingress_windows"]
    if len(windows) > HOOK_WINDOW_SWEEP_AT:
        # One entry plus up to HOOK_RATE_LIMIT timestamps per session that ever
        # received a hook, retained for the daemon's (weeks-long) lifetime.
        live = request.app["sessions"].sessions
        for stale in [sid for sid in windows if sid not in live]:
            windows.pop(stale, None)
    window = windows.setdefault(session.record.id, deque())
    while window and now - window[0] >= HOOK_RATE_WINDOW_SECONDS:
        window.popleft()
    if len(window) >= HOOK_RATE_LIMIT:
        raise web.HTTPTooManyRequests(
            text="hook event burst limit exceeded", headers={"Retry-After": "1"}
        )
    window.append(now)
    raw = await request.read()
    if len(raw) > 256 * 1024:
        raise web.HTTPRequestEntityTooLarge(max_size=256 * 1024, actual_size=len(raw))
    body = json.loads(raw)
    if not isinstance(body, dict):
        raise ValueError("hook body must be a JSON object")
    event_type = str(body.get("event") or body.get("type") or "hook")
    if event_type not in _HOOK_EVENT_TYPES:
        raise ValueError("unsupported hook event type")
    payload = body.get("payload") or {}
    if not isinstance(payload, dict):
        raise ValueError("hook payload must be a JSON object")
    hook_source = str(body.get("source") or "")[:64]
    sequence_value = body.get("sequence")
    sequence: int | None = None
    sequence_state = session.observation_state.setdefault("hook_sequences", {})
    if not isinstance(sequence_state, dict):
        sequence_state = {}
        session.observation_state["hook_sequences"] = sequence_state
    if sequence_value is not None:
        if isinstance(sequence_value, bool) or not isinstance(sequence_value, int):
            raise ValueError("hook sequence must be a positive integer")
        if sequence_value < 1 or not hook_source:
            raise ValueError("sequenced hooks require a source and positive sequence")
        sequence = sequence_value
        previous_sequence = sequence_state.get(hook_source)
        if isinstance(previous_sequence, int) and sequence <= previous_sequence:
            duplicate_count = session.observation_state.get("hook_sequence_duplicates", 0)
            session.observation_state["hook_sequence_duplicates"] = (
                duplicate_count + 1 if isinstance(duplicate_count, int) else 1
            )
            session.state_transitions.append(
                {
                    "ts": time.time(),
                    "kind": "hook_sequence_duplicate",
                    "source": hook_source,
                    "previous": previous_sequence,
                    "sequence": sequence,
                }
            )
            return json_response({"ok": True, "ignored": "duplicate_or_stale_sequence"})
        if isinstance(previous_sequence, int) and sequence > previous_sequence + 1:
            session.state_transitions.append(
                {
                    "ts": time.time(),
                    "kind": "hook_sequence_gap",
                    "source": hook_source,
                    "previous": previous_sequence,
                    "sequence": sequence,
                }
            )
    elif descriptor(session.record.backend).hook_ordering_guarantee:
        raise ValueError("this harness requires sequenced hook events")
    event_payload = hook_event_payload(payload)
    scope = hook_event_scope(event_type, payload)
    # An in-CLI `/clear` replaces the conversation under a live PTY: the CLI
    # reports its new id here, and that ends the current agent run. Rolled before
    # the observation below so the SessionStart transition lands on the new run.
    # Claude blocks on this POST, so a failure must not break the user's `/clear`
    # — but it must not degrade to the old silent-swap behaviour either. Failing
    # closed marks observation stale, which is exactly true: we know the
    # conversation moved and we did not manage to follow it.
    decision = conversation_rollover_decision(session, event_type, payload)
    if decision.roll_to is not None:
        reported_transcript = (
            str(payload.get("transcript_path") or "")
            if descriptor(session.record.backend).reports_transcript_path
            else ""
        )
        try:
            await request.app["sessions"].roll_agent_conversation(
                session.record.id,
                native_id=decision.roll_to,
                reason="conversation_rolled",
                source=str(payload.get("source") or "hook"),
                # The CLI names the file it is now writing; deriving it from the
                # session cwd instead re-guesses what the hook already proves.
                transcript=Path(reported_transcript) if reported_transcript else None,
            )
        except Exception:
            log.exception(
                "conversation rollover failed for session %s; marking observation stale",
                session.record.id,
            )
            session.record.observation_stale_since = time.time()
            session.record.observation_diagnostic = (
                "the CLI reported a new conversation that could not be adopted"
            )
            session.state_transitions.append(
                {
                    "ts": time.time(),
                    "kind": "observation_liveness_lost",
                    "reason": "rollover_unadoptable",
                }
            )
            session.publish_update()
    elif decision.refused is not None:
        session.state_transitions.append(
            {
                "ts": time.time(),
                "kind": "conversation_rollover_refused",
                "native_session_id": decision.refused,
                "reason": decision.refusal_reason,
            }
        )
        await request.app["events"].emit(
            "conversation_rollover_refused",
            session_id=session.record.id,
            source="hook",
            backend=session.record.backend,
            native_session_id=decision.refused,
            reason=decision.refusal_reason,
        )
        # A refused SessionStart belongs to another process generation. It is
        # diagnostic evidence only and must not continue into binding, liveness,
        # automation, or status observation. Continuing used to let a Codex
        # startup emitted around compaction force the active root session idle.
        return json_response({"ok": True, "ignored": "foreign_conversation"})
    # The session's own spawn conversation speaking while the record is bound
    # elsewhere is proof the identity was stolen (a nested child rolled it away);
    # heal before the foreign check below would discard the evidence.
    await request.app["sessions"].maybe_heal_from_own_conversation_hook(session, payload)
    if foreign_conversation_hook_id(session, payload) is None:
        # Only this session's own conversation counts as evidence: a nested
        # child's hooks must not refresh liveness, date staleness, or reach the
        # event bus as this session's activity.
        session.last_hook_ts = time.time()
        if event_type in _TRANSCRIPT_BACKED_HOOK_EVENTS and scope != "subagent":
            # Root scope only. `last_turn_hook_ts` means "the CLI ran a turn
            # whose records must have landed in the transcript we follow", and a
            # *subagent's* tool call is not that: a background subagent writes
            # nothing into the root transcript. Counting its PreToolUse/PostToolUse
            # stream here made a session waiting on background agents look like a
            # conversation that had been replaced — a quiet root transcript plus
            # a "turn" hook is exactly `_note_transcript_staleness`'s trigger —
            # so `observation_stale_since` false-fired, revoking transcript
            # authority and painting the status line's staleness warning on a
            # perfectly healthy session (measured live 2026-08-02, 666s).
            session.last_turn_hook_ts = session.last_hook_ts
        # Where the CLI says it is standing, and which file it says it is writing.
        # Both are only meaningful for a hook that speaks for this session's own
        # conversation, which is why they live inside this branch: a nested child
        # inherits the hook wiring, and letting its readings through would move a
        # session's cwd and its observation onto a conversation it does not own.
        # Only staged here — the ingress must return fast, because Claude blocks the
        # user's turn on this POST.
        request.app["sessions"].note_hook_cwd(session, payload)
        request.app["sessions"].note_hook_transcript_path(session, payload)
        request.app["automation"].note_native_hook(session.record.id)
        if event_type not in _NORMALIZED_HOOK_EVENT_TYPES:
            await request.app["events"].emit(
                event_type,
                session_id=session.record.id,
                source="hook",
                scope=scope,
                **event_payload,
            )
    await apply_hook_observation(session, event_type, payload, request.app["events"])
    if sequence is not None:
        sequence_state = session.observation_state.setdefault("hook_sequences", {})
        if isinstance(sequence_state, dict):
            sequence_state[hook_source] = sequence
    return json_response({"ok": True})


GIT_GRAPH_DEFAULT_LIMIT = 80
GIT_GRAPH_MAX_LIMIT = 200


async def list_worktrees(request: web.Request) -> web.Response:
    extras = set(request.query) - {"project_id"}
    if extras:
        raise git_review.GitReviewError(
            "invalid_parameters", f"unsupported parameters: {', '.join(sorted(extras))}"
        )
    project_id = request.query.get("project_id", "")
    project = request.app["projects"].projects.get(project_id)
    if project is None:
        raise git_review.GitReviewError("project_not_found", "unknown Project", 404)
    return json_response(
        await git_review.worktree_overview(project.id, project.root, project.git_compare_ref)
    )


async def git_graph(request: web.Request) -> web.Response:
    """Return a bounded, read-only commit graph with Git's own lane layout."""
    extras = set(request.query) - {"project_id", "limit"}
    if extras:
        raise git_review.GitReviewError(
            "invalid_parameters", f"unsupported parameters: {', '.join(sorted(extras))}"
        )
    project_id = request.query.get("project_id", "")
    project = request.app["projects"].projects.get(project_id)
    if project is None:
        raise git_review.GitReviewError("project_not_found", "unknown Project", 404)
    raw_limit = request.query.get("limit") or str(GIT_GRAPH_DEFAULT_LIMIT)
    try:
        limit = int(raw_limit)
    except ValueError:
        return json_response({"error": "limit must be an integer"}, 400)
    if not 1 <= limit <= GIT_GRAPH_MAX_LIMIT:
        return json_response({"error": f"limit must be between 1 and {GIT_GRAPH_MAX_LIMIT}"}, 400)
    return json_response(await git_review.git_graph(project.id, project.root, limit))


async def git_commit_changes(request: web.Request) -> web.Response:
    allowed = {"project_id", "parent"}
    extras = set(request.query) - allowed
    if extras:
        raise git_review.GitReviewError(
            "invalid_parameters", f"unsupported parameters: {', '.join(sorted(extras))}"
        )
    project = request.app["projects"].projects.get(request.query.get("project_id", ""))
    if project is None:
        raise git_review.GitReviewError("project_not_found", "unknown Project", 404)
    return json_response(
        await git_review.commit_changes(
            project.id,
            project.root,
            request.match_info["oid"],
            request.query.get("parent") or None,
        )
    )


async def git_diff(request: web.Request) -> web.Response:
    allowed = {
        "project_id",
        "scope",
        "worktree",
        "path",
        "commit",
        "parent",
        "expected_head",
        "patch_hash",
    }
    extras = set(request.query) - allowed
    if extras:
        raise git_review.GitReviewError(
            "invalid_parameters", f"unsupported parameters: {', '.join(sorted(extras))}"
        )
    project = request.app["projects"].projects.get(request.query.get("project_id", ""))
    if project is None:
        raise git_review.GitReviewError("project_not_found", "unknown Project", 404)
    scope = request.query.get("scope", "")
    if scope not in {"unstaged", "staged", "conflicted", "branch", "commit"}:
        raise git_review.GitReviewError("invalid_scope", "unsupported Git diff scope")
    return json_response(
        await git_review.patch_snapshot(
            project_id=project.id,
            project_root=project.root,
            compare_override=project.git_compare_ref,
            scope=scope,  # type: ignore[arg-type]
            path=request.query.get("path", ""),
            worktree=request.query.get("worktree") or None,
            commit=request.query.get("commit") or None,
            requested_parent=request.query.get("parent") or None,
            expected_head=request.query.get("expected_head") or None,
            expected_patch_hash=request.query.get("patch_hash") or None,
        )
    )


async def _listed_worktree_paths(cwd: str) -> dict[str, str]:
    code, output = await _git(cwd, "worktree", "list", "--porcelain")
    if code:
        raise ValueError(output or "unable to inspect repository worktrees")
    return {
        str(Path(str(item["worktree"])).resolve()).casefold(): str(item["worktree"])
        for item in git_review.parse_worktrees(output)
        if item.get("worktree")
    }


async def _spawn_into_worktree(app: web.Application, spawn_body: Any, path: str) -> dict[str, Any]:
    """Start a session whose cwd is a worktree that was just created.

    Reports failure rather than raising: the worktree already exists and is the durable
    artefact, so a rejected or failed spawn must not unwind it or turn the whole request
    into an error. The caller sees ``status`` and can retry the spawn alone.

    The cwd is forced to the new worktree — a caller cannot use this path to redirect a
    session somewhere else, and `_spawn_from_body` re-validates it against
    `git worktree list` regardless.
    """
    if not isinstance(spawn_body, dict):
        return {"status": "error", "error": "spawn must be an object"}
    if not spawn_body.get("project_id"):
        return {"status": "error", "error": "spawn requires project_id"}
    try:
        session = await _spawn_from_body(app, {**spawn_body, "cwd": path})
    except ValueError as exc:
        return {"status": "error", "error": str(exc)}
    except Exception as exc:  # noqa: BLE001 - the worktree must survive any spawn failure
        logging.getLogger(__name__).exception("spawn into worktree %s failed", path)
        return {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
    return {"status": "spawned", "session_id": session.record.id, "cwd": path}


async def create_worktree(request: web.Request) -> web.Response:
    body = await request.json()
    cwd, path = str(body["cwd"]), str(Path(body["path"]).resolve())
    if not Path(cwd).is_dir():
        raise ValueError({"cwd": "repository directory does not exist"})
    if not Path(path).parent.is_dir():
        raise ValueError({"path": "target parent directory does not exist"})
    existing = await _listed_worktree_paths(cwd)
    if path.casefold() in existing:
        raise ValueError({"path": "target is already a registered worktree"})
    args = ["worktree", "add"]
    if branch := body.get("branch"):
        args.extend(["-b", str(branch)])
    args.append(path)
    if start_point := body.get("start_point"):
        args.append(str(start_point))
    code, output = await _git(cwd, *args)
    if code:
        return json_response(
            {
                "error": output or "git worktree add failed",
                "code": "git_timeout" if code == 124 else "git_error",
            },
            504 if code == 124 else 400,
        )
    result: dict[str, Any] = {"ok": True, "path": path, "spawn": {"status": "not_requested"}}
    if (spawn_body := body.get("spawn")) is not None:
        result["spawn"] = await _spawn_into_worktree(request.app, spawn_body, path)
    await request.app["events"].emit("worktree_created", source="user", cwd=cwd, path=path)
    return json_response(result, 201)


async def remove_worktree(request: web.Request) -> web.Response:
    body = await request.json()
    cwd = str(body["cwd"])
    requested = str(Path(str(body["path"])).resolve())
    listed = await _listed_worktree_paths(cwd)
    registered = listed.get(requested.casefold())
    if not registered:
        return json_response(
            {
                "error": "path is not a registered worktree for this repository",
                "code": "not_registered_worktree",
            },
            409,
        )
    args = ["worktree", "remove"]
    if body.get("force"):
        args.append("--force")
    args.append(registered)
    code, output = await _git(cwd, *args)
    if code:
        return json_response(
            {
                "error": output or "git worktree remove failed",
                "code": "git_timeout" if code == 124 else "git_error",
            },
            504 if code == 124 else 400,
        )
    await request.app["events"].emit("worktree_removed", source="user", cwd=cwd, path=registered)
    return json_response({"ok": True})


async def reveal_path(request: web.Request) -> web.Response:
    path = Path((await request.json())["path"]).resolve()
    if not path.exists():
        raise ValueError("path does not exist")
    await asyncio.to_thread(open_in_file_manager, path)
    return json_response({"ok": True})


async def pty_ws(request: web.Request) -> web.WebSocketResponse:
    session = request.app["sessions"].resolve(request.match_info["sid"])
    snapshot_generation = str(request.app.get("daemon_generation") or "legacy")
    connection_id = secrets.token_urlsafe(12)
    ws = web.WebSocketResponse(heartbeat=20, max_msg_size=2 * 1024 * 1024)
    await ws.prepare(request)
    allow_terminal_responses = (
        session.attachments_seen == 0
        and session.record.state not in {"exited", "crashed"}
        and time.time() - session.record.created_at <= 5
    )
    session.attachments_seen += 1
    snapshot, revision, replay, subscriber = session.replay_and_subscribe()
    # Everything after the subscribe runs inside the try, so no path can exit
    # without unsubscribing. A mid-replay disconnect (a slow mobile link is the
    # realistic case) used to orphan the subscriber, permanently marking the
    # session "attended" — which suppresses unattended-attention automation and
    # fleet absence reporting for that session's whole lifetime.
    sender_task: asyncio.Task[None] | None = None
    try:
        request.app["events"].emit_background(
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
        pending_messages: list[Any] = []
        attach_deadline = asyncio.get_running_loop().time() + PTY_ATTACH_READY_TIMEOUT_SECONDS
        attach_closed = False
        geometry_queued = False
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
                    geometry_queued = _apply_client_viewport(session, connection_id, initial_frame)
                    break
                pending_messages.append(initial_message)
            elif initial_message.type in {WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.ERROR}:
                attach_closed = True
                break
            else:
                pending_messages.append(initial_message)

        if attach_closed:
            return ws

        await ws.send_json(
            {
                "type": "replay_start",
                "reason": "attach",
                "allow_terminal_responses": allow_terminal_responses,
            }
        )
        if replay:
            await ws.send_bytes(replay)
        await ws.send_json({"type": "replay_end", "reason": "attach"})
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
            _pty_sender(ws, session, subscriber, snapshot_generation)
        )
        for pending_message in pending_messages:
            await _handle_pty_client_message(request, ws, session, connection_id, pending_message)
        async for message in ws:
            await _handle_pty_client_message(request, ws, session, connection_id, message)
    finally:
        # Every synchronous cleanup runs before the sender task is awaited. A handler
        # cancelled on peer disconnect re-raises at the first await inside its own
        # finally, which used to skip the unsubscribe and the ownership release
        # entirely — leaving the session reported as attended forever and its input
        # owned by a socket that no longer exists.
        released = session.release_input_owner(connection_id)
        session.drop_viewport(connection_id)
        session.claim_refusals.pop(connection_id, None)
        session.unsubscribe(subscriber)
        # A detach can hand geometry back to whoever is left: the phone closing its tab
        # returns the PTY to the desktop's width.
        session.apply_geometry()
        if released:
            session.publish_control(
                {
                    "type": "input_owner_released",
                    "epoch": session.input_owner_epoch,
                }
            )
        request.app["events"].emit_background(
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
    ws: web.WebSocketResponse, session: Session, subscriber: Any, generation: str
) -> None:
    # unsupervised-loop-ok: lives for one PTY websocket, not the daemon.
    while True:
        message = await subscriber.queue.get()
        if isinstance(message, bytes):
            await ws.send_bytes(message)
        elif message.get("type") == "resync":
            (
                dropped_bytes,
                dropped_chunks,
                replay_bytes,
                current,
                current_revision,
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
                await ws.send_bytes(replay_bytes)
            await ws.send_json({"type": "replay_end", "reason": "resync"})
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
    request.app["events"].emit_background(
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
    presence: DevicePresenceStore | None = request.app.get("device_presence")
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
        session.apply_geometry()
        request.app["events"].emit_background(
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
    if _is_codex_default_color_response(session.record.backend, data):
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
                }
            )
        return
    session.pty.write(data)
    now = time.monotonic()
    pointer = pointer_report_kind(data)
    if not is_terminal_response and pointer is None:
        cancel_pending_approval(session, "terminal_input")
        session.input_revision += 1
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
            request.app["events"].emit_background(
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
        request.app["events"].emit_background(
            "terminal_input",
            session_id=session.record.id,
            source="daemon",
            input_owner=True,
            bytes=len(data.encode("utf-8")),
        )
    if frame.get("broadcast") and not is_terminal_response:
        await deliver_broadcast(
            request.app["sessions"],
            data,
            request.app["events"],
            source_id=session.record.id,
        )


async def _handle_pty_client_message(
    request: web.Request,
    ws: web.WebSocketResponse,
    session: Session,
    connection_id: str,
    message: Any,
) -> None:
    if message.type == WSMsgType.BINARY:
        # No current client sends binary input; a non-owner's bytes are counted and
        # dropped rather than echoed back, since there is no frame shape to replay.
        if session.input_owner != connection_id:
            _note_input_rejected(request, session, len(message.data))
            return
        session.pty.write(message.data)
        now = time.monotonic()
        session.input_revision += 1
        session.last_input_event_ts = now
        session.note_owner_input(now)
        if now - session.last_input_report_ts >= 2:
            session.last_input_report_ts = now
            request.app["events"].emit_background(
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
                request.app["events"].emit_background(
                    "terminal_mode_changed",
                    session_id=session.record.id,
                    source="browser",
                    mode=mode,
                )
        elif frame.get("type") in {"attach_ready", "resize"}:
            _apply_client_viewport(session, connection_id, frame)


async def events_ws(request: web.Request) -> web.WebSocketResponse:
    ws = web.WebSocketResponse(heartbeat=20)
    await ws.prepare(request)
    bus: EventBus = request.app["events"]
    presence: DevicePresenceStore = request.app["device_presence"]
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
    queue = bus.subscribe(name="events-ws")
    try:
        if last_sequence > 0:
            # Resume: everything the client missed, oldest first.
            catch_up = await request.app["history"].events(
                session_id=session_filter,
                limit=EVENTS_CATCHUP_LIMIT,
                after_seq=last_sequence,
            )
            truncated = len(catch_up) >= EVENTS_CATCHUP_LIMIT
        else:
            # Cold open: the NEWEST retained events. Serving the oldest is what
            # the "after_seq absent" default used to do, which on any established
            # install replayed days-old history and delivered none of the events
            # the client actually missed.
            catch_up, truncated = await request.app["history"].recent_events(
                session_id=session_filter, limit=EVENTS_CATCHUP_LIMIT
            )
        if truncated:
            # More was missed than the replay carries: the client must full-refresh
            # rather than assume the gap is covered.
            await ws.send_json({"type": "events_gap", "reason": "catchup_truncated"})
        for event in catch_up:
            # Catch-up events are a historical replay for state reconstruction, not
            # live activity. Mark them so the browser suppresses live-only side effects
            # (voice autoplay, notification sounds) that would otherwise re-fire every
            # reconnect or reopen.
            event["replay"] = True
            await ws.send_json(event)
            last_sequence = max(last_sequence, int(event["seq"]))

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
                event = getter.result()
                if event.seq <= last_sequence:
                    continue
                await ws.send_json(event.snapshot())
                last_sequence = event.seq
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
