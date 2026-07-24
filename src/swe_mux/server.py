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
import threading
import time
import tomllib
from collections import deque
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import replace
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

from aiohttp import ClientError, ClientSession, ClientTimeout, WSMsgType, web
from aiohttp.multipart import BodyPartReader

from .adapters import BackendAdapter, ClaudeAdapter, CodexAdapter, ShellAdapter
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
from .automation_registry import resolve_config as resolve_automation_config
from .automation_store import AutomationStore
from .config import Config, load_config, update_config
from .event_bus import EventBus
from .file_manager import open_in_file_manager
from .fleet_intelligence import FleetIntelligence
from .git_monitor import GitMonitor, _git
from .git_projects import ProjectIdentity, resolve_project
from .history import HistoryIndex
from .history_backfill import HistoryBackfillManager
from .keybindings import (
    DEFAULT_KEYBINDINGS,
    KEYBINDING_COMMANDS,
    keybinding_policy,
    normalize_binding,
)
from .launchers import create_agent_shims, resolve_codex_pty_command, resolve_command
from .layouts import attach_leaf, attach_terminal, stack_leaf
from .meta_hooks import MetaHookEngine, parse_hook_rules
from .models import MuxEvent
from .observation import apply_hook_observation, hook_event_scope
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
from .project_actions import ProjectActionService, runner_spawn_body
from .project_files import (
    append_observation,
    effective_project_ignores,
    initialize_note,
    list_project_directories,
    list_project_directory,
    note_exists,
    note_has_content,
    project_automations,
    project_path,
    read_note,
    read_observations,
    read_project_config,
    read_project_file,
    search_project_files,
    session_note_summaries,
    write_note,
    write_observations,
    write_project_config,
    write_project_file,
)
from .project_files import (
    revision as file_revision,
)
from .project_watcher import ProjectFileWatcher
from .projects import ProjectManager
from .prompt_library import PromptLibrary, PromptScope
from .provider_accounts import ProviderAccountError, ProviderAccountManager
from .push import PushSender, PushStore
from .reconcile import reconcile_external_history
from .secret_store import PlatformSecretStore, SecretStoreError
from .session import Session, SessionManager
from .settings_store import SettingsStore
from .spawn_contract import SpawnRequest
from .supervisor_client import SupervisorClient
from .tailscale import (
    enable_mobile_voice_serve,
    is_tailscale_ip,
    tailscale_status,
)
from .tier0_store import Tier0Store
from .transcript_view import parse_transcript_cached
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
PTY_ATTACH_READY_TIMEOUT_SECONDS = 0.25
HOOK_RATE_WINDOW_SECONDS = 10.0
HOOK_RATE_LIMIT = 500


def hook_event_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Remove hook-owned envelope keys that collide with EventBus metadata."""
    return {
        key: value
        for key, value in payload.items()
        if key not in {"session_id", "source", "event_type", "scope"}
    }


def json_response(data: Any, status: int = 200) -> web.Response:
    return web.json_response(data, status=status)


@web.middleware
async def error_middleware(request: web.Request, handler: Handler) -> web.StreamResponse:
    try:
        return await handler(request)
    except web.HTTPException:
        raise
    except KeyError as exc:
        return json_response({"error": f"not found: {exc.args[0]}"}, 404)
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
) -> web.Application:
    if (desktop_control_token is None) != (desktop_shutdown_event is None):
        raise ValueError("desktop control requires both a token and shutdown event")
    app = web.Application(
        middlewares=[error_middleware, security_middleware], client_max_size=12 * 1024 * 1024
    )
    app["config"] = config
    app["frontend_dir"] = frontend_dir or Path(__file__).parent / "static"
    app["preview_http_semaphore"] = asyncio.Semaphore(PREVIEW_HTTP_CONCURRENCY)
    app["preview_ws_semaphore"] = asyncio.Semaphore(PREVIEW_WS_CONCURRENCY)
    app["hook_ingress_windows"] = {}
    # Mutable holder because aiohttp freezes app keys once started; carries the
    # externally-signaled shutdown intent (quit vs restart/detach) to cleanup.
    app["shutdown_state"] = {"intent": None}
    if desktop_control_token is not None and desktop_shutdown_event is not None:
        app["desktop_control_token"] = desktop_control_token
        app["desktop_shutdown_event"] = desktop_shutdown_event
    app.cleanup_ctx.append(runtime_context)
    app.add_routes(
        [
            web.get("/", index),
            web.get("/manifest.webmanifest", manifest),
            web.get("/sw.js", service_worker),
            web.get("/api/health", health),
            web.post("/api/desktop/shutdown", desktop_shutdown),
            web.get("/api/remote/status", remote_status),
            web.post("/api/remote/mobile-voice/enable", enable_mobile_voice),
            web.get("/api/config", get_config),
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
            web.get("/api/projects", list_projects),
            web.post("/api/projects", create_project),
            web.put("/api/projects/order", reorder_projects),
            web.patch("/api/projects/{project_id}", patch_project),
            web.delete("/api/projects/{project_id}", delete_project),
            web.get("/api/projects/{project_id}/actions", list_project_actions),
            web.post("/api/projects/{project_id}/actions/trust", trust_project_actions),
            web.post("/api/projects/{project_id}/actions/run", run_project_action),
            web.get("/api/projects/{project_id}/note", get_project_note),
            web.put("/api/projects/{project_id}/note", put_project_note),
            web.get("/api/projects/{project_id}/observations", get_observations),
            web.post("/api/projects/{project_id}/observations", post_observation),
            web.put("/api/projects/{project_id}/observations", put_observations),
            web.get("/api/session-notes", list_session_notes),
            web.get("/api/projects/{project_id}/session-notes/{note_id}", get_session_note),
            web.post("/api/projects/{project_id}/session-notes/{note_id}", initialize_session_note),
            web.put("/api/projects/{project_id}/session-notes/{note_id}", put_session_note),
            web.get("/api/projects/{project_id}/files/tree", list_project_files_tree),
            web.get("/api/projects/{project_id}/files", list_project_files),
            web.get("/api/projects/{project_id}/search", search_project_files_route),
            web.get("/api/projects/{project_id}/file", get_project_file),
            web.put("/api/projects/{project_id}/file", put_project_file),
            web.post("/api/projects/{project_id}/reveal", reveal_project_resource),
            web.post("/api/projects/{project_id}/ignore", ignore_project_resource),
            web.put("/api/projects/{project_id}/watch", put_project_watch),
            web.delete("/api/projects/{project_id}/watch/{watch_id}", delete_project_watch),
            web.get("/api/project-groups", list_project_groups),
            web.post("/api/project-groups", create_project_group),
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
            web.get("/api/sessions/{sid}/last-reply", session_last_reply),
            web.patch("/api/sessions/{sid}", patch_session),
            web.delete("/api/sessions/{sid}", delete_session),
            web.post("/api/sessions/{sid}/relaunch", relaunch_session),
            web.post("/api/sessions/{sid}/branch", branch_session),
            web.post("/api/sessions/{sid}/input", session_input),
            web.post("/api/sessions/{sid}/startup-metrics", session_startup_metrics),
            web.post("/api/sessions/{sid}/broadcast-set", broadcast_set),
            web.post("/api/broadcast/input", broadcast_input_route),
            web.post("/api/sessions/{sid}/media", upload_session_media),
            web.post("/api/sessions/{sid}/promote", promote_session),
            web.post("/api/sessions/{sid}/demote", demote_session),
            web.get("/api/history", list_history),
            web.get("/api/history/projects", list_history_projects),
            web.get("/api/history/backfills", list_history_backfills),
            web.post("/api/history/backfills", start_history_backfill),
            web.get("/api/history/backfills/{job_id}", get_history_backfill),
            web.delete("/api/history/backfills/{job_id}", cancel_history_backfill),
            web.get("/api/history/{sid}/transcript", history_transcript),
            web.post("/api/history/{sid}/resume", resume_history),
            web.delete("/api/history/{sid}", delete_history_entry),
            web.get("/api/events", list_events),
            web.get("/api/settings", get_settings),
            web.put("/api/settings/{profile}", put_settings),
            web.get("/api/push/vapid-public-key", get_vapid_public_key),
            web.post("/api/push/subscribe", push_subscribe),
            web.post("/api/push/unsubscribe", push_unsubscribe),
            web.post("/api/push/presence", push_presence),
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
            web.post("/api/provider-accounts/refresh", refresh_provider_accounts),
            web.post("/api/provider-accounts/{provider}/capture", capture_provider_account),
            web.post("/api/provider-accounts/{provider}/login", login_provider_account),
            web.patch("/api/provider-accounts/{provider}/{account_id}", patch_provider_account),
            web.post(
                "/api/provider-accounts/{provider}/{account_id}/select",
                select_provider_account,
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
            web.get("/api/git/worktrees", list_worktrees),
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


async def runtime_context(app: web.Application):  # type: ignore[no-untyped-def]
    config: Config = app["config"]
    history = HistoryIndex(config.database_path)
    events = EventBus(history.append_event)
    telemetry = OperationalTelemetryStore(
        config.database_path,
        retention_days=config.operational_telemetry_retention_days,
        process_retention_days=config.process_evidence_retention_days,
    )
    await telemetry.prune(process_retention_days=config.process_evidence_retention_days)
    tier0 = Tier0Store(
        config.database_path, retention_days=config.process_evidence_retention_days
    )
    await tier0.prune()
    projects = ProjectManager(history)
    await projects.start()
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
    adapters: dict[str, BackendAdapter] = {
        "shell": ShellAdapter(config.shell_exe),
        "claude": ClaudeAdapter(config.claude_exe, config.data_dir, config.claude_args),
        "codex": CodexAdapter(
            config.codex_exe,
            notify=True,
            default_args=config.codex_args,
            command_resolver=resolve_codex_pty_command,
        ),
    }
    child_env = create_agent_shims(config, adapters["claude"].settings_path)  # type: ignore[attr-defined]
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
    )
    if supervisor_client is not None:
        try:
            adopted = await sessions.adopt_supervisor_sessions()
            if adopted:
                log.info("reattached %d live session(s) from the PTY supervisor", adopted)
        except Exception:
            log.exception("supervisor session adoption failed")
    git_monitor = GitMonitor(sessions, events, config.git_poll_seconds)
    hooks = MetaHookEngine(config.data_dir / "hooks.toml", events, sessions)
    automation_store = AutomationStore(config.database_path)
    await automation_store.prune(config.automation_retention_days)
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
        claude_exe=config.claude_exe,
        codex_exe=config.codex_exe,
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
    voice_store = VoiceStore(config.database_path)
    voice = VoiceService(config, events, sessions, voice_store, automation_store, openrouter)
    prompt_library = PromptLibrary(config.data_dir)
    settings_store = SettingsStore(config.data_dir)
    push_store = PushStore(config.data_dir)
    project_actions = ProjectActionService(config.data_dir)
    project_watcher = ProjectFileWatcher(projects, events, config)
    telemetry.start(events, sessions=sessions, history=history)

    tier0_enabled_cache: dict[str, tuple[float, bool]] = {}

    async def tier0_enabled(session_id: str) -> bool:
        # The per-project enablement gate: Tier 0 only captures for a session
        # whose owning project opted it in. Resolved off-loop with a short TTL
        # cache so the event path never blocks on a config read.
        session = sessions.sessions.get(session_id)
        if session is None:
            return False
        record = session.record
        root = record.project_root or record.spawn_project_root
        if not root and record.project_id:
            project = projects.projects.get(record.project_id)
            root = project.root if project else None
        if not root:
            return False
        now = time.monotonic()
        cached = tier0_enabled_cache.get(root)
        if cached and now - cached[0] < 5.0:
            return cached[1]
        project_map = await asyncio.to_thread(project_automations, root)
        enabled = resolve_automation_config(project_map).is_enabled("tier0")
        tier0_enabled_cache[root] = (now, enabled)
        return enabled

    tier0.start(events, resolve_enabled=tier0_enabled)
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
    project_watcher.start()
    config_watch = asyncio.create_task(_watch_config(app), name="config-watch")
    media_cleanup_task = asyncio.create_task(
        _media_cleanup_loop(config.data_dir), name="media-cleanup"
    )
    state_watchdog_task = asyncio.create_task(
        sessions.state_watchdog_loop(), name="state-watchdog"
    )
    push_task = asyncio.create_task(
        PushSender(push_store, settings_store, events).run(), name="push-sender"
    )
    reconcile_task: asyncio.Task[int] | None = None
    if config.reconcile_external_history:
        reconcile_task = asyncio.create_task(
            reconcile_external_history(history), name="history-reconcile"
        )
    app.update(
        history=history,
        events=events,
        projects=projects,
        history_backfills=history_backfills,
        sessions=sessions,
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
        tier0=tier0,
        provider_accounts=provider_accounts,
        process_inspector=process_inspector,
        previews=previews,
        fleet=fleet,
        voice=voice,
        voice_store=voice_store,
        prompt_library=prompt_library,
        settings_store=settings_store,
        push_store=push_store,
        project_actions=project_actions,
        project_watcher=project_watcher,
        automation_tasks=set(),
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
    config_watch.cancel()
    await asyncio.gather(config_watch, return_exceptions=True)
    media_cleanup_task.cancel()
    await asyncio.gather(media_cleanup_task, return_exceptions=True)
    state_watchdog_task.cancel()
    await asyncio.gather(state_watchdog_task, return_exceptions=True)
    push_task.cancel()
    await asyncio.gather(push_task, return_exceptions=True)
    await hooks.stop()
    await automation.stop()
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
    await telemetry.stop()
    await tier0.stop()
    history.close()
    automation_store.close()
    voice_store.close()
    telemetry.close()
    tier0.close()
    reaper.close()


async def _watch_config(app: web.Application) -> None:
    config: Config = app["config"]
    path = config.config_path
    if path is None:
        return
    modified = path.stat().st_mtime_ns if path.exists() else 0
    while True:
        await asyncio.sleep(1)
        current = path.stat().st_mtime_ns if path.exists() else 0
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
            await app["events"].emit("configuration_error", source="external_file", error=str(exc))


def _apply_runtime_config(app: web.Application, changed: set[str]) -> None:
    config: Config = app["config"]
    sessions: SessionManager | None = app.get("sessions")
    if sessions:
        if "scrollback_bytes" in changed:
            sessions.max_scrollback = config.scrollback_bytes
        adapter_config = {
            "shell": (config.shell_exe, []),
            "claude": (config.claude_exe, config.claude_args),
            "codex": (config.codex_exe, config.codex_args),
        }
        for backend, (executable, args) in adapter_config.items():
            relevant = {f"{backend}_exe", f"{backend}_args"}
            if changed & relevant:
                sessions.adapters[backend].configure(executable, args)
                if backend != "shell":
                    sessions.child_env[f"MUX_{backend.upper()}_EXE"] = resolve_command(executable)
                    sessions.child_env[f"MUX_{backend.upper()}_ARGS"] = json.dumps(args)
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
        if "claude_exe" in changed:
            provider_accounts.executables["claude"] = config.claude_exe
        if "codex_exe" in changed:
            provider_accounts.executables["codex"] = config.codex_exe
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
    return json_response(
        {
            "ok": True,
            "live_sessions": live,
            "version": "0.1.0",
            "supervisor": bool(supervisor is not None and supervisor.connected),
        }
    )


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


async def get_keybindings(request: web.Request) -> web.Response:
    defaults = dict(DEFAULT_KEYBINDINGS)
    path = request.app["config"].data_dir / "keybindings.json"
    rejected: dict[str, str] = {}
    if path.exists():
        try:
            supplied = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid keybindings.json: {exc}") from exc
        replace_defaults = bool(
            isinstance(supplied, dict) and supplied.get("replace_defaults") is True
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
    commands = [
        {"id": command_id, "label": label, "category": category}
        for command_id, label, category in KEYBINDING_COMMANDS
    ]
    return json_response(
        {
            "bindings": defaults,
            "defaults": DEFAULT_KEYBINDINGS,
            "commands": commands,
            "policy": keybinding_policy(),
            "rejected": rejected,
        }
    )


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
        "version": 1,
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


async def get_automation_status(request: web.Request) -> web.Response:
    automation: AutomationEngine = request.app["automation"]
    projects = await request.app["history"].project_scopes(include_hidden=True)
    entries = await asyncio.gather(
        *(
            asyncio.to_thread(_load_repo_rule_entry, str(project["id"]), str(project["root"]))
            for project in projects
        )
    )
    repository_rules = [entry for entry in entries if entry is not None]
    return json_response(
        {
            **automation.status(),
            "legacy": {
                "path": str(request.app["config"].data_dir / "hooks.toml"),
                "active": bool(request.app["hooks"].rules),
                "diagnostic": request.app["hooks"].diagnostic,
                "migration": "explicit-save-required",
            },
            "repository_rules": repository_rules,
        }
    )


async def get_automation_rules(request: web.Request) -> web.Response:
    path = request.app["config"].data_dir / "rules.toml"
    return json_response(
        {
            "version": 1,
            "text": path.read_text(encoding="utf-8") if path.exists() else "version = 1\n",
            "rules": [rule.snapshot() for rule in request.app["automation"].rules],
            "diagnostic": request.app["automation"].diagnostic,
        }
    )


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
    if not source or source.get("backend") not in {"claude", "codex"}:
        raise KeyError(source_id)
    body = await request.json()
    backend = str(body.get("backend") or ("codex" if source["backend"] == "claude" else "claude"))
    if backend not in {"claude", "codex"} or backend == source["backend"]:
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
    if not row or row.get("backend") not in {"claude", "codex"}:
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
            or row.get("backend") not in {"claude", "codex"}
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


async def get_project_config(request: web.Request) -> web.Response:
    return json_response(await read_project_config(request.query.get("cwd") or str(Path.cwd())))


async def put_project_config(request: web.Request) -> web.Response:
    body = await request.json()
    values = dict(body.get("values") or {})
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


async def get_project_note(request: web.Request) -> web.Response:
    project_id = request.match_info["project_id"]
    project = request.app["projects"].projects.get(project_id)
    if not project:
        raise ValueError("unknown project")
    note = await read_note(project.root, "projects", project.id)
    note.update({"project_id": project.id, "project_name": project.name})
    return json_response(note)


async def put_project_note(request: web.Request) -> web.Response:
    project_id = request.match_info["project_id"]
    project = request.app["projects"].projects.get(project_id)
    if not project:
        raise ValueError("unknown project")
    body = await request.json()
    try:
        result = await write_note(
            project.root,
            "projects",
            project.id,
            str(body.get("markdown") or ""),
            str(body.get("revision") or "missing"),
        )
    except ValueError as exc:
        if "changed externally" in str(exc):
            return json_response({"error": str(exc), "code": "revision_conflict"}, 409)
        raise
    await request.app["events"].emit(
        "project_note_changed",
        source="user",
        project_id=project.id,
        revision=result["revision"],
    )
    result.update({"project_id": project.id, "project_name": project.name})
    return json_response(result)


def _observations_project(request: web.Request):  # type: ignore[no-untyped-def]
    project = request.app["projects"].projects.get(request.match_info["project_id"])
    if not project:
        raise ValueError("unknown project")
    return project


async def get_observations(request: web.Request) -> web.Response:
    project = _observations_project(request)
    result = await read_observations(project.root)
    result.update({"project_id": project.id, "project_name": project.name})
    return json_response(result)


async def post_observation(request: web.Request) -> web.Response:
    project = _observations_project(request)
    body = await request.json()
    result = await append_observation(project.root, str(body.get("body") or ""))
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
            project.root, observations, str(body.get("revision") or "missing")
        )
    except ValueError as exc:
        if "changed externally" in str(exc):
            return json_response({"error": str(exc), "code": "revision_conflict"}, 409)
        raise
    result.update({"project_id": project.id, "project_name": project.name})
    return json_response(result)


async def list_session_notes(request: web.Request) -> web.Response:
    """List session notes that hold real text, newest first, for the notes browser.

    The listing is filesystem-derived so a note remains reachable after its
    session, its history row, and the daemon that created it are all gone.
    History and live sessions only supply display labels.
    """
    manager: ProjectManager = request.app["projects"]
    requested = request.query.get("project_id") or ""
    projects = [
        project
        for project in manager.ordered_projects()
        if not requested or project.id == requested
    ]
    if requested and not projects:
        raise ValueError("unknown project")
    live_names = {
        session.record.id: session.record for session in request.app["sessions"].sessions.values()
    }
    items: list[dict[str, Any]] = []
    for project in projects:
        summaries = await asyncio.to_thread(session_note_summaries, project.root)
        if not summaries:
            continue
        owners = await request.app["history"].note_owner_labels(project.id)
        for summary in summaries:
            note_id = str(summary["note_id"])
            record = live_names.get(note_id)
            owner = owners.get(note_id) or {}
            live = record is not None and record.state not in {"exited", "crashed"}
            items.append(
                {
                    **summary,
                    "project_id": project.id,
                    "project_name": project.name,
                    "owner_label": (record.name if record else owner.get("name")) or note_id,
                    "owner_backend": (record.backend if record else owner.get("backend")) or None,
                    "owner_live": live,
                    "owner_known": record is not None or bool(owner),
                }
            )
    items.sort(key=lambda item: float(item["updated_at"]), reverse=True)
    return json_response({"items": items})


async def _session_note_owner(request: web.Request):  # type: ignore[no-untyped-def]
    project_id = request.match_info["project_id"]
    note_id = request.match_info["note_id"]
    project = request.app["projects"].projects.get(project_id)
    if not project:
        raise ValueError("unknown project")
    live = request.app["sessions"].sessions.get(note_id)
    live_owned = bool(live and live.record.project_id == project_id)
    historical = await request.app["history"].session_note_owned(project_id, note_id)
    if not live_owned and not historical and not note_exists(project.root, "sessions", note_id):
        raise ValueError("session note is not owned by this project")
    return project, note_id


async def get_session_note(request: web.Request) -> web.Response:
    project, note_id = await _session_note_owner(request)
    note = await read_note(project.root, "sessions", note_id)
    note.update({"project_id": project.id, "project_name": project.name})
    return json_response(note)


async def initialize_session_note(request: web.Request) -> web.Response:
    project, note_id = await _session_note_owner(request)
    note = await initialize_note(project.root, "sessions", note_id)
    await request.app["events"].emit(
        "session_note_initialized", source="user", project_id=project.id, note_id=note_id
    )
    note.update({"project_id": project.id, "project_name": project.name})
    return json_response(note, 201)


async def put_session_note(request: web.Request) -> web.Response:
    project, note_id = await _session_note_owner(request)
    body = await request.json()
    try:
        result = await write_note(
            project.root,
            "sessions",
            note_id,
            str(body.get("markdown") or ""),
            str(body.get("revision") or "missing"),
        )
    except ValueError as exc:
        if "changed externally" in str(exc):
            return json_response({"error": str(exc), "code": "revision_conflict"}, 409)
        raise
    await request.app["events"].emit(
        "session_note_changed",
        source="user",
        project_id=project.id,
        note_id=note_id,
        revision=result["revision"],
    )
    result.update({"project_id": project.id, "project_name": project.name})
    return json_response(result)


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
        item["note_id"] = session.record.id
        project = request.app["projects"].projects.get(session.record.project_id)
        # Content, not presence: a note created by a stray click must not pin a
        # permanent sidebar row under its terminal.
        item["note_exists"] = bool(
            project and note_has_content(project.root, "sessions", session.record.id)
        )
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
    if spec.profile_id and backend in {"claude", "codex"}:
        raise ValueError({"profile_id": "shell profiles cannot be used with agent backends"})
    cwd = owning_project.root
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
    spawn_values: dict[str, Any] = dict(
        backend=backend,
        name=spec.name,
        cwd=cwd,
        project_id=project_id,
        exe=executable,
        args=argv,
        shell_profile_id=profile_id,
        profile_env=profile_env,
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


async def get_session_state_log(request: web.Request) -> web.Response:
    """End-detection diagnostics: recent transitions, faults, and watchdog activity."""
    session = request.app["sessions"].resolve(request.match_info["sid"])
    now = time.time()
    transcript = session.transcript_path
    transcript_mtime: float | None = None
    if transcript is not None:
        try:
            transcript_mtime = transcript.stat().st_mtime
        except OSError:
            transcript_mtime = None
    return json_response(
        {
            "id": session.record.id,
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
            "observer_restart_count": session.observer_restart_count,
            "observer_last_fault": session.observer_last_fault,
            "watchdog_recoveries": session.watchdog_recoveries,
            "observation_replay": session.observation_replay,
            "transitions": list(session.state_transitions),
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


async def delete_session(request: web.Request) -> web.Response:
    manager: SessionManager = request.app["sessions"]
    session = manager.resolve(request.match_info["sid"])
    if session.record.state not in {"exited", "crashed"}:
        await manager.stop(session.record.id)
    manager.sessions.pop(session.record.id, None)
    shutil.rmtree(
        session_media_directory(request.app["config"].data_dir, session.record.id),
        ignore_errors=True,
    )
    return json_response({"ok": True})


async def relaunch_session(request: web.Request) -> web.Response:
    """Replay a task-launched shell in place: spawn a fresh copy, retire the old.

    Relaunch-from-record — the replacement re-runs the exact retained executable/argv
    (env is baked into the Project Action payload), so no task file is re-read and no
    trust re-approval is needed. Only sessions the daemon marked relaunchable qualify;
    agent and plain shell sessions are rejected so this never touches their lifecycle.
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
    }
    # Spawn the replacement first: if it raises, the original is left fully intact.
    session = await _spawn_from_body(request.app, body)
    session.record.relaunchable = True
    session.publish_update()
    old_id = old.record.id
    if old.record.state not in {"exited", "crashed"}:
        await manager.stop(old_id)
    manager.sessions.pop(old_id, None)
    shutil.rmtree(
        session_media_directory(request.app["config"].data_dir, old_id),
        ignore_errors=True,
    )
    return json_response({"session": session.record.snapshot(), "replaced": old_id}, 201)


async def _await_native_switch(record: Any, previous: str, timeout_seconds: float) -> bool:
    """Wait for the daemon to observe an in-CLI conversation id switch."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_seconds
    while loop.time() < deadline:
        current = str(record.native_session_id or "")
        if current and current != previous:
            return True
        await asyncio.sleep(0.25)
    return False


async def branch_session(request: web.Request) -> web.Response:
    """Fork an agent conversation, keeping the original and the branch both open.

    Claude has a native ``/branch`` that forks to a fresh session id in place, so
    we inject it, wait for the daemon to see the id switch (which freezes the
    original transcript), then reopen the original id in a sibling pane — the
    source pane holds the new branch. Codex has no in-CLI branch, so we simulate
    one: ``codex resume`` starts a child thread (``parent_thread_id`` set) that
    diverges from the still-live original without sharing its rollout, so the
    source pane keeps the original and the new pane holds the branch.
    """
    manager: SessionManager = request.app["sessions"]
    source = manager.resolve(request.match_info["sid"])
    record = source.record
    if record.backend not in {"claude", "codex"}:
        return json_response(
            {"error": "only Claude and Codex sessions can branch", "code": "not_agent"}, 422
        )
    native_id = str(record.native_session_id or "")
    if not native_id or native_id == record.id:
        return json_response(
            {"error": "no native session id to branch from yet", "code": "native_id_missing"}, 409
        )
    project = request.app["projects"].projects.get(record.project_id)
    if project is None:
        return json_response({"error": "project missing", "code": "project_missing"}, 422)
    body = await request.json() if request.can_read_body else {}

    if record.backend == "claude":
        source.pty.write("/branch\r")
        if not await _await_native_switch(record, native_id, timeout_seconds=15.0):
            # The fork never registered; do not resume the (still-live) original —
            # that would collide on its transcript. The source pane may already be
            # branched; the original stays resumable from history.
            return json_response(
                {"error": "branch did not complete in time; try again", "code": "branch_timeout"},
                504,
            )
    suffix = "original" if record.backend == "claude" else "branch"
    session = await manager.spawn(
        backend=record.backend,
        name=body.get("name") or f"{record.name} {suffix}",
        cwd=record.cwd,
        project_id=record.project_id,
        resume_native_id=native_id,
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


async def session_input(request: web.Request) -> web.Response:
    body = await request.json()
    session = request.app["sessions"].resolve(request.match_info["sid"])
    session.pty.write(str(body.get("data", "")))
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
        if not candidate.pty.isalive():
            skipped.append(candidate.record.id)
            continue
        candidate.pty.write(data)
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
    "SessionStart",
    "UserPromptSubmit",
    "PreToolUse",
    "PostToolUse",
    "PostToolUseFailure",
    "PermissionRequest",
    "Notification",
    "Stop",
    "SessionEnd",
    "SubagentStart",
    "SubagentStop",
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
    "rate_limit",
    "rate_limited",
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
    for directory in root.iterdir():
        if not directory.is_dir() or directory.is_symlink():
            continue
        for path in directory.iterdir():
            try:
                if path.is_file() and not path.is_symlink() and path.stat().st_mtime < cutoff:
                    path.unlink()
                    removed += 1
            except OSError:
                continue
        with suppress(OSError):
            directory.rmdir()
    return removed


async def _media_cleanup_loop(data_dir: Path) -> None:
    while True:
        await asyncio.to_thread(cleanup_expired_session_media, data_dir, time.time())
        await asyncio.sleep(60 * 60)


async def upload_session_media(request: web.Request) -> web.Response:
    if request.headers.get("X-Mux-User-Gesture") not in {"terminal-image", "clipboard-image"}:
        raise web.HTTPForbidden(
            text="terminal image upload requires an explicit paste or drop action"
        )
    session = request.app["sessions"].resolve(request.match_info["sid"])
    adapter: BackendAdapter = request.app["sessions"].adapters[session.record.backend]
    if session.record.backend not in {"claude", "codex"}:
        raise ValueError("clipboard images are supported only in Claude and Codex sessions")
    reader = await request.multipart()
    part = await reader.next()
    if not isinstance(part, BodyPartReader) or part.name != "file":
        raise ValueError("multipart field 'file' is required")
    media_type = str(part.headers.get("Content-Type", "")).split(";", 1)[0].lower()
    data = bytearray()
    while chunk := await part.read_chunk(size=64 * 1024):
        data.extend(chunk)
        if len(data) > 10 * 1024 * 1024:
            raise ValueError("clipboard image exceeds the 10 MiB limit")
    suffix = validate_session_media(media_type, data)
    directory = session_media_directory(request.app["config"].data_dir, session.record.id)
    directory.mkdir(parents=True, exist_ok=True)
    if sum(1 for item in directory.iterdir() if item.is_file()) >= 32:
        raise ValueError("this session has reached the 32-image clipboard limit")
    path = directory / f"{uuid4().hex}{suffix}"
    temporary = path.with_suffix(f"{suffix}.tmp")
    temporary.write_bytes(data)
    temporary.replace(path)
    await request.app["events"].emit(
        "session_media_uploaded",
        session_id=session.record.id,
        media_type=media_type,
        bytes=len(data),
    )
    return json_response(
        {
            "path": str(path),
            "reference": adapter.media_reference(path),
            "media_type": media_type,
            "bytes": len(data),
        },
        201,
    )


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


async def list_projects(request: web.Request) -> web.Response:
    manager: ProjectManager = request.app["projects"]
    return json_response(
        await asyncio.gather(
            *(_project_snapshot(request, item) for item in manager.ordered_projects())
        )
    )


async def _project_snapshot(request: web.Request, project) -> dict[str, Any]:  # type: ignore[no-untyped-def]
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
        "portable_options": public_values,
        "effective_options": effective,
        "option_sources": sources,
        "project_config_status": portable["status"],
    }


async def create_project(request: web.Request) -> web.Response:
    body = await request.json()
    project = await request.app["projects"].create(
        str(body.get("name") or Path(str(body.get("root") or "")).name or "New project"),
        str(body.get("root") or ""),
        group_id=str(body["group_id"]) if body.get("group_id") else None,
    )
    await request.app["events"].emit(
        "project_created", source="user", project_id=project.id, root=project.root
    )
    return json_response(await _project_snapshot(request, project), 201)


async def patch_project(request: web.Request) -> web.Response:
    body = await request.json()
    if "position" in body:
        raise ValueError({"position": "use the Project order endpoint"})
    if "sidebar_visible" in body and not isinstance(body["sidebar_visible"], bool):
        raise ValueError({"sidebar_visible": "must be a boolean"})
    backend = body.get("default_backend")
    if backend not in {None, "shell", "claude", "codex"}:
        raise ValueError({"default_backend": "must be shell, claude, codex, or null"})
    profile_id = body.get("default_profile_id")
    if profile_id is not None and profile_id not in {
        profile.id for profile in request.app["config"].shell_profiles
    }:
        raise ValueError({"default_profile_id": "unknown shell profile"})
    project = await request.app["projects"].update(request.match_info["project_id"], **body)
    return json_response(await _project_snapshot(request, project))


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
    return json_response(
        await asyncio.gather(*(_project_snapshot(request, item) for item in projects))
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
    portable = await read_project_config(
        project.root, project=ProjectIdentity(project.id, project.name, project.root, "registered")
    )
    values = portable["values"] if portable["status"] in {"ready", "read-only"} else {}
    profile_id = (
        project.default_profile_id
        or values.get("default_shell_profile")
        or request.app["config"].default_shell_profile
    )
    sessions: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for batch in action.batches:
        results = await asyncio.gather(
            *(
                _spawn_from_body(
                    request.app,
                    runner_spawn_body(
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


async def list_project_groups(request: web.Request) -> web.Response:
    manager: ProjectManager = request.app["projects"]
    return json_response([item.snapshot() for item in manager.groups.values()])


async def create_project_group(request: web.Request) -> web.Response:
    body = await request.json()
    group = await request.app["projects"].create_group(str(body.get("name") or ""))
    return json_response(group.snapshot(), 201)


async def patch_project_group(request: web.Request) -> web.Response:
    group = await request.app["projects"].update_group(
        request.match_info["group_id"], **await request.json()
    )
    return json_response(group.snapshot())


async def delete_project_group(request: web.Request) -> web.Response:
    await request.app["projects"].delete_group(request.match_info["group_id"])
    return json_response({"ok": True})


def _request_project(request: web.Request):  # type: ignore[no-untyped-def]
    project = request.app["projects"].projects.get(request.match_info["project_id"])
    if not project:
        raise ValueError("unknown project")
    return project


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
        lambda: search_project_files(
            project.root, query, mode=mode, ignore_patterns=patterns
        ),
    )
    return json_response(result)


async def get_project_file(request: web.Request) -> web.Response:
    project = _request_project(request)
    return json_response(read_project_file(project.root, request.query.get("path", "")))


async def put_project_file(request: web.Request) -> web.Response:
    project = _request_project(request)
    body = await request.json()
    try:
        result = write_project_file(
            project.root,
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
    target = project_path(project.root, str(body.get("path") or ""))
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

    current = await read_project_config(project.root)
    if current["status"] == "malformed":
        raise ValueError("project config is malformed; fix it before adding an ignore")
    values = dict(current["values"])
    patterns = list(values.get("ignore_patterns", []))
    added = pattern not in patterns
    if added:
        values["ignore_patterns"] = [*patterns, pattern]
        await write_project_config(project.root, values, current["revision"])
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
    lease = request.app["project_watcher"].register(
        request.match_info["project_id"], raw_paths, watch_id
    )
    return json_response(
        {
            "watch_id": lease.watch_id,
            "paths": list(lease.paths),
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
    # Parse off the event loop and reuse the shared (path, mtime, backend,
    # max_bytes) cache; large transcripts otherwise block the loop on every open.
    messages = await asyncio.to_thread(
        parse_transcript_cached, Path(transcript), str(row["backend"])
    )
    stat = await asyncio.to_thread(Path(transcript).stat)
    await request.app["history"].replace_history_messages(
        str(row["id"]), messages, mtime_ns=stat.st_mtime_ns, size=stat.st_size
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
    if not row.get("agent_visible") or row.get("backend") not in {"claude", "codex"}:
        return json_response(
            {"error": "only Claude and Codex history can be resumed", "code": "not_agent"},
            422,
        )
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
    owning_project = request.app["projects"].projects[target_project]
    session = await request.app["sessions"].spawn(
        backend=str(row["backend"]),
        name=body.get("name") or f"{row['name']} resumed",
        cwd=owning_project.root,
        project_id=target_project,
        resume_native_id=str(row["native_id"]),
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
    await request.app["automation_store"].add_lineage(
        str(row["id"]),
        session.record.agent_run_id or session.record.id,
        "resume",
        {"backend": row["backend"], "project_id": target_project},
    )
    return json_response(session.record.snapshot(), 201)


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
    if session.record.backend not in {"claude", "codex"}:
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


def _record_voice_input(request: web.Request, session: Any, data: str) -> None:
    session.pty.write(data)
    now = time.monotonic()
    session.input_revision += 1
    session.last_input_event_ts = now
    session.last_input_report_ts = now
    request.app["events"].emit_background(
        "terminal_input",
        session_id=session.record.id,
        source="voice",
        input_owner=True,
        bytes=len(data.encode("utf-8")),
    )


async def voice_submit(request: web.Request) -> web.Response:
    voice: VoiceService = request.app["voice"]
    session = request.app["sessions"].resolve(request.match_info["sid"])
    if session.record.backend not in {"claude", "codex"}:
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
    _record_voice_input(request, session, f"{text}\r")
    await request.app["events"].emit(
        "voice_prompt_submitted",
        session_id=session.record.id,
        source="voice",
        characters=len(text),
    )
    return json_response({"ok": True, "duplicate": False, "characters": len(text)})


async def voice_interrupt(request: web.Request) -> web.Response:
    session = request.app["sessions"].resolve(request.match_info["sid"])
    if session.record.backend not in {"claude", "codex"}:
        return json_response({"error": "conversation mode requires an agent session"}, 409)
    if session.record.state in {"exited", "crashed"}:
        return json_response({"ok": True, "already_ended": True})
    _record_voice_input(request, session, "\x03")
    await request.app["events"].emit(
        "voice_agent_interrupted", session_id=session.record.id, source="voice"
    )
    return json_response({"ok": True, "already_ended": False})


async def session_last_reply(request: web.Request) -> web.Response:
    """Return normalized assistant text without routing through terminal OSC 52."""
    session = request.app["sessions"].resolve(request.match_info["sid"])
    if session.record.backend not in {"claude", "codex"}:
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
        if account["id"] in latest:
            account["quota"] = latest[account["id"]]
    snapshot["reset_alert"] = await telemetry.reset_summary()
    return json_response(snapshot)


async def refresh_provider_accounts(request: web.Request) -> web.Response:
    accounts: ProviderAccountManager = request.app["provider_accounts"]
    body = await request.json() if request.can_read_body else {}
    return json_response(await accounts.refresh(body.get("account_id"), force_identity_probe=True))


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
        await accounts.select(request.match_info["provider"], request.match_info["account_id"])
    )


async def remove_provider_account(request: web.Request) -> web.Response:
    accounts: ProviderAccountManager = request.app["provider_accounts"]
    return json_response(
        await accounts.remove(request.match_info["provider"], request.match_info["account_id"])
    )


async def list_processes(request: web.Request) -> web.Response:
    session_id = request.query.get("session")
    include_ended = request.query.get("include_ended", "").lower() in {"1", "true", "yes"}
    inspector: ProcessInspector = request.app["process_inspector"]
    return json_response(
        await inspector.snapshot(session_id, include_ended=include_ended)
        if session_id
        else await inspector.snapshot_all(include_ended=include_ended)
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
    text = re.sub(
        r"(?P<start>\b(?:from\s*|import\s*|import\s*\(\s*)[\"'])/",
        rf"\g<start>{prefix}",
        text,
    )
    return text.encode("utf-8")


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
                }
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
    event_payload = hook_event_payload(payload)
    scope = hook_event_scope(event_type, payload)
    session.last_hook_ts = time.time()
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
    return json_response({"ok": True})


async def list_worktrees(request: web.Request) -> web.Response:
    cwd = request.query.get("cwd") or str(Path.cwd())
    code, output = await _git(cwd, "worktree", "list", "--porcelain")
    if code:
        return json_response(
            {
                "error": output or "unable to list Git worktrees",
                "code": "git_timeout" if code == 124 else "git_error",
            },
            504 if code == 124 else 400,
        )
    return json_response(_parse_worktrees(output))


def _parse_worktrees(output: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    current: dict[str, Any] = {}
    for line in [*output.splitlines(), ""]:
        if not line:
            if current:
                items.append(current)
                current = {}
        elif " " in line:
            key, value = line.split(" ", 1)
            current[key] = value
        else:
            current[line] = True
    return items


async def _listed_worktree_paths(cwd: str) -> dict[str, str]:
    code, output = await _git(cwd, "worktree", "list", "--porcelain")
    if code:
        raise ValueError(output or "unable to inspect repository worktrees")
    return {
        str(Path(str(item["worktree"])).resolve()).casefold(): str(item["worktree"])
        for item in _parse_worktrees(output)
        if item.get("worktree")
    }


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
    if body.get("spawn") is not None:
        result["spawn"] = {
            "status": "unsupported",
            "error": "worktrees are Git-only; create sessions from an explicit project",
        }
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
    request.app["events"].emit_background(
        "terminal_attached",
        session_id=session.record.id,
        source="daemon",
        connections=len(session.subscribers),
    )
    await ws.send_json({"type": "state", "snapshot": snapshot, "revision": revision})
    pending_messages: list[Any] = []
    attach_deadline = asyncio.get_running_loop().time() + PTY_ATTACH_READY_TIMEOUT_SECONDS
    attach_closed = False
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
                session.pty.resize(
                    int(initial_frame["cols"]), int(initial_frame["rows"])
                )
                break
            pending_messages.append(initial_message)
        elif initial_message.type in {WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.ERROR}:
            attach_closed = True
            break
        else:
            pending_messages.append(initial_message)

    if attach_closed:
        session.unsubscribe(subscriber)
        request.app["events"].emit_background(
            "terminal_detached",
            session_id=session.record.id,
            source="daemon",
            connections=len(session.subscribers),
        )
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

    async def sender() -> None:
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
                    {"type": "update", "snapshot": current, "revision": current_revision}
                )
                if exit_frame:
                    await ws.send_json(exit_frame)
                    return
            else:
                await ws.send_json(message)
                if message.get("type") == "exit":
                    return

    if snapshot["state"] in {"exited", "crashed"}:
        await ws.send_json(
            {
                "type": "exit",
                "snapshot": snapshot,
                "revision": revision,
                "reason": "already_ended",
            }
        )
        session.unsubscribe(subscriber)
        return ws

    async def handle_client_message(message: Any) -> None:
        if message.type == WSMsgType.BINARY:
            if session.input_owner == connection_id:
                session.pty.write(message.data)
                now = time.monotonic()
                session.input_revision += 1
                session.last_input_event_ts = now
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
                session.input_owner = connection_id
                await ws.send_json({"type": "input_owner", "active": True})
            elif frame.get("type") == "input" and session.input_owner == connection_id:
                data = str(frame.get("data", ""))
                session.pty.write(data)
                is_terminal_response = frame.get("kind") == "terminal_response"
                now = time.monotonic()
                if not is_terminal_response:
                    session.input_revision += 1
                    session.last_input_event_ts = now
                if not is_terminal_response and now - session.last_input_report_ts >= 2:
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
                session.pty.resize(int(frame["cols"]), int(frame["rows"]))

    task = asyncio.create_task(sender())
    try:
        for pending_message in pending_messages:
            await handle_client_message(pending_message)
        async for message in ws:
            await handle_client_message(message)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        if session.input_owner == connection_id:
            session.input_owner = None
        session.unsubscribe(subscriber)
        request.app["events"].emit_background(
            "terminal_detached",
            session_id=session.record.id,
            source="daemon",
            connections=len(session.subscribers),
        )
    return ws


async def events_ws(request: web.Request) -> web.WebSocketResponse:
    ws = web.WebSocketResponse(heartbeat=20)
    await ws.prepare(request)
    bus: EventBus = request.app["events"]
    queue = bus.subscribe()
    last_sequence = int(request.query.get("after_seq", 0))
    try:
        catch_up = await request.app["history"].events(
            session_id=request.query.get("session"),
            limit=2000,
            after_seq=last_sequence,
        )
        for event in catch_up:
            # Catch-up events are a historical replay for state reconstruction, not
            # live activity. Mark them so the browser suppresses live-only side effects
            # (voice autoplay, notification sounds) that would otherwise re-fire every
            # reconnect or reopen.
            event["replay"] = True
            await ws.send_json(event)
            last_sequence = max(last_sequence, int(event["seq"]))
        while True:
            event = await queue.get()
            if event.seq <= last_sequence:
                continue
            await ws.send_json(event.snapshot())
            last_sequence = event.seq
    except (asyncio.CancelledError, ConnectionResetError):
        pass
    finally:
        bus.unsubscribe(queue)
    return ws
