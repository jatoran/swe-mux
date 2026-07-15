from __future__ import annotations

import asyncio
import hashlib
import json
import logging
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
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

from aiohttp import ClientError, ClientSession, ClientTimeout, WSMsgType, web
from aiohttp.multipart import BodyPartReader

from .adapters import BackendAdapter, ClaudeAdapter, CodexAdapter, ShellAdapter
from .app_notes import list_space_notes, note_body, read_space_note, write_space_note
from .automation import (
    OBSERVER_SCHEMAS,
    AutomationEngine,
    RuleValidationError,
    normalize_event,
    parse_rules,
    serialize_rules,
    validate_observer_result,
)
from .automation_store import AutomationStore
from .config import Config, load_config, update_config
from .event_bus import EventBus
from .fleet_intelligence import FleetIntelligence
from .git_monitor import GitMonitor, _git
from .history import HistoryIndex
from .keybindings import (
    DEFAULT_KEYBINDINGS,
    KEYBINDING_COMMANDS,
    keybinding_policy,
    normalize_binding,
)
from .launchers import create_agent_shims
from .layouts import attach_leaf, attach_terminal, stack_leaf
from .meta_hooks import MetaHookEngine, parse_hook_rules
from .models import MuxEvent, SessionState
from .note_migration import migrate_space_notes, repair_misbound_project_notes
from .openrouter import OpenRouterClient, OpenRouterError
from .processes import PreviewRegistry, ProcessInspector
from .profiles import profile_payload, resolve_profile
from .project_files import (
    read_note,
    read_project_config,
    resolve_project_default_cwd,
    safe_note_filename,
    search_notes,
    write_note,
    write_project_config,
)
from .project_files import (
    revision as file_revision,
)
from .projects import resolve_project
from .reconcile import reconcile_external_history
from .secret_store import PlatformSecretStore, SecretStoreError
from .session import Session, SessionManager
from .spaces import SpaceManager
from .spawn_contract import SpawnRequest
from .tailscale import is_tailscale_ip, tailscale_status
from .transcript_view import parse_transcript_cached
from .usage import UsageManager
from .voice import VoiceError, VoiceService, VoiceStore, clip_snapshot
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
HOOK_RATE_WINDOW_SECONDS = 10.0
HOOK_RATE_LIMIT = 500


def hook_event_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Remove hook-owned envelope keys that collide with EventBus metadata."""
    return {
        key: value
        for key, value in payload.items()
        if key not in {"session_id", "source", "event_type"}
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
    except (ValueError, TypeError) as exc:
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
            "default-src 'self'; connect-src 'self' ws: wss:; "
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


def create_app(config: Config, *, frontend_dir: Path | None = None) -> web.Application:
    app = web.Application(
        middlewares=[error_middleware, security_middleware], client_max_size=12 * 1024 * 1024
    )
    app["config"] = config
    app["frontend_dir"] = frontend_dir or Path(__file__).parent / "static"
    app["preview_http_semaphore"] = asyncio.Semaphore(PREVIEW_HTTP_CONCURRENCY)
    app["preview_ws_semaphore"] = asyncio.Semaphore(PREVIEW_WS_CONCURRENCY)
    app["hook_ingress_windows"] = {}
    app.cleanup_ctx.append(runtime_context)
    app.add_routes(
        [
            web.get("/", index),
            web.get("/api/health", health),
            web.get("/api/remote/status", remote_status),
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
            web.get("/api/project/notes", get_project_note),
            web.put("/api/project/notes", put_project_note),
            web.get("/api/notes", get_project_note),
            web.put("/api/notes", put_project_note),
            web.get("/api/project/notes/search", find_project_notes),
            web.get("/api/space-notes", list_app_space_notes),
            web.get("/api/note-shelf", list_note_shelf),
            web.get("/api/projects", list_projects),
            web.post("/api/projects/resolve", resolve_project_scope),
            web.get("/api/projects/{scope_id}", get_project_scope),
            web.patch("/api/projects/{scope_id}", patch_project_scope),
            web.delete("/api/projects/{scope_id}", forget_project_scope),
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
            web.patch("/api/sessions/{sid}", patch_session),
            web.delete("/api/sessions/{sid}", delete_session),
            web.post("/api/sessions/{sid}/input", session_input),
            web.post("/api/sessions/{sid}/startup-metrics", session_startup_metrics),
            web.post("/api/sessions/{sid}/broadcast-set", broadcast_set),
            web.post("/api/broadcast/input", broadcast_input_route),
            web.post("/api/sessions/{sid}/media", upload_session_media),
            web.post("/api/sessions/{sid}/promote", promote_session),
            web.post("/api/sessions/{sid}/demote", demote_session),
            web.get("/api/spaces", list_spaces),
            web.post("/api/spaces", create_space),
            web.patch("/api/spaces/{sid}", patch_space),
            web.delete("/api/spaces/{sid}", delete_space),
            web.get("/api/history", list_history),
            web.get("/api/history/projects", list_history_projects),
            web.get("/api/history/{sid}/transcript", history_transcript),
            web.post("/api/history/{sid}/resume", resume_history),
            web.delete("/api/history/{sid}", delete_history_entry),
            web.get("/api/events", list_events),
            web.get("/api/notifications", list_notifications),
            web.get("/api/voice", voice_status),
            web.post("/api/sessions/{sid}/voice/generate", voice_generate),
            web.get("/api/voice/clips", list_voice_clips),
            web.get("/api/voice/clips/{clip_id}/audio", voice_clip_audio),
            web.delete("/api/voice/clips/{clip_id}", delete_voice_clip),
            web.get("/api/usage", get_usage),
            web.post("/api/usage/refresh", refresh_usage),
            web.delete("/api/usage/cache", clear_usage_cache),
            web.get("/api/processes", list_processes),
            web.post("/api/processes/action", process_action),
            web.get("/api/previews", list_previews),
            web.post("/api/previews", create_preview),
            web.delete("/api/previews/{preview_id}", delete_preview),
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
    assets = app["frontend_dir"] / "assets"
    if assets.is_dir():
        app.router.add_static("/assets", assets)
    return app


async def runtime_context(app: web.Application):  # type: ignore[no-untyped-def]
    config: Config = app["config"]
    history = HistoryIndex(config.database_path)
    events = EventBus(history.append_event)
    spaces = SpaceManager(history)
    await spaces.start()
    await migrate_space_notes(config.data_dir, history, spaces)
    await repair_misbound_project_notes(history, spaces)
    reaper = ReaperJob()
    adapters: dict[str, BackendAdapter] = {
        "shell": ShellAdapter(config.shell_exe),
        "claude": ClaudeAdapter(config.claude_exe, config.data_dir, config.claude_args),
        "codex": CodexAdapter(config.codex_exe, notify=True, default_args=config.codex_args),
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
    )
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
    process_inspector = ProcessInspector(sessions, events)
    previews = PreviewRegistry(process_inspector, sessions)
    fleet = FleetIntelligence(
        sessions, events, automation_store, process_inspector, previews, config
    )
    voice_store = VoiceStore(config.database_path)
    voice = VoiceService(config, events, sessions, voice_store, automation_store, openrouter)
    git_monitor.start()
    hooks.start()
    automation.start()
    usage.start()
    process_inspector.start()
    fleet.start()
    voice.start()
    config_watch = asyncio.create_task(_watch_config(app), name="config-watch")
    media_cleanup_task = asyncio.create_task(
        _media_cleanup_loop(config.data_dir), name="media-cleanup"
    )
    reconcile_task: asyncio.Task[int] | None = None
    if config.reconcile_external_history:
        reconcile_task = asyncio.create_task(
            reconcile_external_history(history), name="history-reconcile"
        )
    app.update(
        history=history,
        events=events,
        spaces=spaces,
        sessions=sessions,
        reaper=reaper,
        git_monitor=git_monitor,
        hooks=hooks,
        automation=automation,
        automation_store=automation_store,
        secret_store=secret_store,
        openrouter=openrouter,
        usage=usage,
        process_inspector=process_inspector,
        previews=previews,
        fleet=fleet,
        voice=voice,
        voice_store=voice_store,
        automation_tasks=set(),
    )
    yield
    if reconcile_task:
        if not reconcile_task.done():
            reconcile_task.cancel()
        await asyncio.gather(reconcile_task, return_exceptions=True)
    for task in tuple(app["automation_tasks"]):
        task.cancel()
    await asyncio.gather(*app["automation_tasks"], return_exceptions=True)
    config_watch.cancel()
    await asyncio.gather(config_watch, return_exceptions=True)
    media_cleanup_task.cancel()
    await asyncio.gather(media_cleanup_task, return_exceptions=True)
    await hooks.stop()
    await automation.stop()
    await voice.stop()
    await usage.stop()
    await fleet.stop()
    await process_inspector.stop()
    await git_monitor.stop()
    await sessions.shutdown()
    history.close()
    automation_store.close()
    voice_store.close()
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
                    sessions.child_env[f"MUX_{backend.upper()}_ARGS"] = json.dumps(args)
    git_monitor: GitMonitor | None = app.get("git_monitor")
    if git_monitor and "git_poll_seconds" in changed:
        git_monitor.cadence = config.git_poll_seconds


async def index(request: web.Request) -> web.StreamResponse:
    path: Path = request.app["frontend_dir"] / "index.html"
    if not path.exists():
        return web.Response(
            text="swe-mux frontend is not built. Run: cd frontend; npm install; npm run build",
            content_type="text/plain",
        )
    return web.FileResponse(path)


async def health(request: web.Request) -> web.Response:
    sessions: SessionManager = request.app.get("sessions")
    live = sum(s.pty.isalive() for s in sessions.sessions.values()) if sessions else 0
    return json_response({"ok": True, "live_sessions": live, "version": "0.1.0"})


async def remote_status(request: web.Request) -> web.Response:
    config: Config = request.app["config"]
    return json_response(
        await tailscale_status(config.port, tailnet_enabled=config.tailnet_enabled)
    )


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
    target_space = str(body.get("space") or source.get("space_id") or "default")
    session = await _spawn_from_body(
        request.app,
        {
            "backend": backend,
            "name": body.get("name") or f"{backend} review · {source['name']}",
            "cwd": source["cwd"],
            "space": target_space,
            "argv": [prompt],
        },
    )
    space = request.app["spaces"].spaces[target_space]
    next_layout = attach_terminal(
        space.layout,
        session.record.id,
        target_id=body.get("target_session_id"),
        direction=body.get("direction"),
    )
    try:
        await request.app["spaces"].update(
            target_space, layout=next_layout, layout_revision=space.layout_revision
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
    lines = [
        f"# Handoff: {row['name']}",
        "",
        f"- Backend: {row['backend']}",
        f"- Working directory: {row['cwd']}",
        f"- Agent run: {run_id}",
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
    return json_response({"run_id": run_id, "markdown": "\n".join(lines) + "\n"})


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
    try:
        result = await write_project_config(
            str(body.get("cwd") or Path.cwd()),
            dict(body.get("values") or {}),
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
    if label := result["values"].get("project_label"):
        await request.app["history"].update_project_label(result["project"]["id"], label)
        for session in request.app["sessions"].sessions.values():
            if session.record.project_id == result["project"]["id"]:
                session.record.project_label = label
                session.publish_update()
    return json_response(result)


async def get_project_note(request: web.Request) -> web.Response:
    kind = request.query.get("kind") or "spaces"
    identity = request.query.get("id") or "default"
    if kind == "spaces":
        space = request.app["spaces"].spaces.get(identity)
        label = space.name if space else None
        return json_response(read_space_note(request.app["config"].data_dir, identity, label))
    cwd, scope_id = await _note_scope(request, kind, identity, request.query)
    note = await read_note(
        cwd,
        kind,
        identity,
    )
    note["project_scope_id"] = scope_id
    note["owner_label"] = await _artifact_owner_label(
        request, kind.removesuffix("s"), identity
    )
    note["project_label"] = note["project"].get("label")
    project_config = await read_project_config(cwd)
    if project_config["values"].get("notes_enabled") is False:
        note["status"] = "disabled"
        note["error"] = "project notes are disabled in .swe-mux/config.toml"
    return json_response(note)


async def _artifact_owner_label(request: web.Request, owner_type: str, owner_id: str) -> str:
    if owner_type == "project":
        scope = await request.app["history"].project_scope(owner_id)
        if scope:
            return str(scope.get("label") or owner_id)
    if owner_type == "space":
        space = request.app["spaces"].spaces.get(owner_id)
        if space:
            return str(space.name)
    if owner_type == "session":
        session = request.app["sessions"].sessions.get(owner_id)
        if session:
            return str(session.record.name)
        history = await request.app["history"].history_entry(owner_id)
        if history:
            return str(history.get("name") or owner_id)
    return owner_id


async def put_project_note(request: web.Request) -> web.Response:
    body = await request.json()
    kind = str(body.get("kind") or "spaces")
    identity = str(body.get("id") or "default")
    if kind == "spaces":
        space = request.app["spaces"].spaces.get(identity)
        existing = read_space_note(request.app["config"].data_dir, identity, None)
        if not space and not existing["exists"]:
            raise ValueError("unknown space note")
        label = space.name if space else str(existing.get("owner_label") or identity)
        try:
            result = write_space_note(
                request.app["config"].data_dir,
                identity,
                label,
                str(body.get("markdown") or ""),
                str(body.get("revision") or "missing"),
            )
        except ValueError as exc:
            if "changed externally" in str(exc):
                return json_response({"error": str(exc), "code": "revision_conflict"}, 409)
            raise
        await request.app["events"].emit("space_note_changed", source="user", space_id=identity)
        return json_response(result)
    cwd, scope_id = await _note_scope(request, kind, identity, body)
    if not await request.app["history"].project_scope(scope_id):
        project = await resolve_project(cwd)
        await request.app["history"].register_project_scope(project)
        scope_id = project.id
    project_config = await read_project_config(cwd)
    if project_config["values"].get("notes_enabled") is False:
        return json_response({"error": "project notes are disabled", "code": "notes_disabled"}, 409)
    try:
        result = await write_note(
            cwd,
            kind,
            identity,
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
        project_id=result["project"]["id"],
        note_kind=result["kind"],
        note_id=result["id"],
        project_scope_id=scope_id,
    )
    relative = str(Path(result["path"]).resolve().relative_to(Path(cwd).resolve()))
    artifact = await request.app["history"].bind_artifact(
        artifact_id=str(uuid4()),
        kind="note",
        owner_type=kind.removesuffix("s"),
        owner_id=identity,
        owner_label=await _artifact_owner_label(request, kind.removesuffix("s"), identity),
        project_scope_id=scope_id,
        relative_path=relative,
    )
    result["artifact"] = artifact
    result["project_scope_id"] = artifact["project_scope_id"]
    return json_response(result)


async def _note_scope(
    request: web.Request,
    kind: str,
    identity: str,
    values: Any,
) -> tuple[str, str]:
    history: HistoryIndex = request.app["history"]
    owner_type = kind.removesuffix("s")
    bound = next(
        (
            item
            for item in await history.artifacts()
            if item["kind"] == "note"
            and item["owner_type"] == owner_type
            and item["owner_id"] == identity
        ),
        None,
    )
    if kind == "projects":
        scope = await history.project_scope(identity)
        if not scope:
            raise ValueError("unknown project scope")
        if bound and bound["project_scope_id"] != identity:
            await history.delete_artifact_binding(str(bound["id"]))
        return str(scope["root"]), str(scope["id"])
    if not bound:
        relative = (
            str(Path(".swe-mux") / "notes" / "project.md")
            if kind == "projects"
            else str(Path(".swe-mux") / "notes" / kind / f"{safe_note_filename(identity)}.md")
        )
        matches = [
            scope
            for scope in await history.project_scopes(include_hidden=True)
            if (Path(scope["root"]) / relative).is_file()
        ]
        if len(matches) > 1:
            raise web.HTTPConflict(
                text=json.dumps(
                    {
                        "error": "multiple project scopes contain this legacy note",
                        "code": "artifact_conflict",
                        "project_scope_ids": [item["id"] for item in matches],
                    }
                ),
                content_type="application/json",
            )
        if matches:
            bound = await history.bind_artifact(
                artifact_id=str(uuid4()),
                kind="note",
                owner_type=owner_type,
                owner_id=identity,
                owner_label=await _artifact_owner_label(request, owner_type, identity),
                project_scope_id=matches[0]["id"],
                relative_path=relative,
            )
    requested_scope = values.get("project_scope_id") if hasattr(values, "get") else None
    scope_id = bound["project_scope_id"] if bound else requested_scope
    if not scope_id and kind == "sessions":
        session = request.app["sessions"].sessions.get(identity)
        if session:
            if session.record.backend == "shell":
                raise web.HTTPConflict(
                    text=json.dumps(
                        {
                            "error": (
                                "plain shells do not own durable notes; choose a space "
                                "or project note"
                            ),
                            "code": "shell_note_redirect",
                            "space_id": session.record.space_id,
                        }
                    ),
                    content_type="application/json",
                )
            scope_id = session.record.project_scope_id
        else:
            row = await history.history_entry(identity)
            scope_id = row.get("project_scope_id") if row else None
    if scope_id:
        scope = await history.project_scope(str(scope_id))
        if not scope:
            raise ValueError("unknown project scope")
        return str(scope["root"]), str(scope["id"])
    cwd = str(values.get("cwd") or Path.cwd())
    project = await resolve_project(cwd)
    return project.root, project.id


async def list_app_space_notes(request: web.Request) -> web.Response:
    labels = {item.id: item.name for item in request.app["spaces"].spaces.values()}
    return json_response(list_space_notes(request.app["config"].data_dir, labels))


def _note_preview(path: Path, *, app_owned: bool = False) -> tuple[str | None, str]:
    """Return a bounded, presentation-only title and excerpt for a note."""
    try:
        with path.open("rb") as handle:
            raw = handle.read(64 * 1024 + 1)[: 64 * 1024]
        text = raw.decode("utf-8", errors="replace")
    except OSError:
        return None, ""
    if app_owned:
        text = note_body(text)
    title: str | None = None
    body: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if title is None and stripped.startswith("#"):
            candidate = stripped.lstrip("#").strip()
            if candidate:
                title = candidate[:120]
                continue
        if stripped and not stripped.startswith("---"):
            body.append(stripped)
    excerpt = re.sub(r"\s+", " ", " ".join(body)).strip()
    if len(excerpt) > 240:
        excerpt = f"{excerpt[:237].rstrip()}..."
    return title, excerpt


_NOTE_PREVIEW_CACHE: dict[tuple[str, bool], tuple[int, int, str | None, str]] = {}
_NOTE_CACHE_LOCK = threading.Lock()


def _note_card(path: Path, app_owned: bool = False) -> tuple[str | None, str, float]:
    """Return ``(title, excerpt, modified_at)`` for a note, cached by path+mtime+size.

    Combines the stat and the 64KB read+parse into one worker-thread call and
    serves unchanged notes from a process cache, so a /note-shelf request no
    longer re-reads every note on the event loop. A stat failure returns
    ``(None, "", 0.0)``, matching the original _note_preview/_note_modified_at
    fallbacks; ``app_owned`` is part of the key so the note_body-stripped space
    variant can never collide with a raw preview of the same path.
    """
    try:
        st = path.stat()
    except OSError:
        return None, "", 0.0
    key = (str(path), app_owned)
    with _NOTE_CACHE_LOCK:
        hit = _NOTE_PREVIEW_CACHE.get(key)
    if hit is not None and hit[0] == st.st_mtime_ns and hit[1] == st.st_size:
        return hit[2], hit[3], st.st_mtime
    title, excerpt = _note_preview(path, app_owned=app_owned)
    with _NOTE_CACHE_LOCK:
        if len(_NOTE_PREVIEW_CACHE) >= 4096:
            _NOTE_PREVIEW_CACHE.clear()  # bound growth over a long-lived daemon
        _NOTE_PREVIEW_CACHE[key] = (st.st_mtime_ns, st.st_size, title, excerpt)
    return title, excerpt, st.st_mtime


def _safe_project_note_path(scope: dict[str, Any], relative_path: str) -> Path | None:
    root = Path(str(scope["root"]))
    mux = root / ".swe-mux"
    candidate = root / relative_path
    try:
        relative = candidate.relative_to(mux)
        cursor = mux
        for part in relative.parts:
            cursor /= part
            if cursor.is_symlink():
                return None
        if mux.is_symlink():
            return None
        resolved = candidate.resolve(strict=True)
        mux_root = mux.resolve(strict=True)
    except (OSError, ValueError):
        return None
    if not resolved.is_file() or not resolved.is_relative_to(mux_root):
        return None
    return resolved


async def note_shelf_items(request: web.Request) -> list[dict[str, Any]]:
    """Build the unified, read-only discovery index without creating project files."""
    history: HistoryIndex = request.app["history"]
    spaces = request.app["spaces"].spaces
    scopes = await history.project_scopes(include_hidden=True)
    scope_by_id = {str(item["id"]): item for item in scopes}
    history_rows = await history.history(limit=1000)
    if "automation_store" in request.app:
        await _decorate_generated_titles(request.app, history_rows)
    history_by_id = {str(item["id"]): item for item in history_rows}
    live_by_run: dict[str, Any] = {}
    for session in request.app["sessions"].sessions.values():
        live_by_run[str(session.record.id)] = session.record
        if session.record.agent_run_id:
            live_by_run[str(session.record.agent_run_id)] = session.record

    results: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    labels = {item.id: item.name for item in spaces.values()}
    data_dir: Path = request.app["config"].data_dir
    for summary in list_space_notes(data_dir, labels):
        path = Path(str(summary["path"]))
        title, excerpt, modified_at = await asyncio.to_thread(_note_card, path, True)
        seen_paths.add(str(path.resolve()))
        results.append(
            {
                "id": f"space:{summary['id']}",
                "category": "space",
                "kind": "spaces",
                "identity": summary["id"],
                "owner_label": summary["label"],
                "content_title": title,
                "project_scope_id": None,
                "project_label": None,
                "project_root": None,
                "space_id": summary["id"],
                "backend": None,
                "run_state": "live" if summary["active"] else "archived",
                "storage": "app-data",
                "path": str(path),
                "modified_at": modified_at,
                "excerpt": excerpt,
                "linked": True,
                "recovered": False,
                "active": bool(summary["active"]),
                "openable": True,
            }
        )

    artifacts = [item for item in await history.artifacts() if item["kind"] == "note"]
    artifacts_by_scope: dict[str, list[dict[str, Any]]] = {}
    for artifact in artifacts:
        scope_id = str(artifact["project_scope_id"])
        artifacts_by_scope.setdefault(scope_id, []).append(artifact)
        scope = scope_by_id.get(scope_id)
        if not scope:
            continue
        artifact_path = _safe_project_note_path(scope, str(artifact["relative_path"]))
        if not artifact_path:
            continue
        seen_paths.add(str(artifact_path))
        owner_type = str(artifact["owner_type"])
        identity = str(artifact["owner_id"])
        row = history_by_id.get(identity)
        if owner_type == "session" and not row:
            candidate = await history.history_entry(identity)
            if candidate and candidate.get("backend") in {"claude", "codex"}:
                row = candidate
                if "automation_store" in request.app:
                    await _decorate_generated_titles(request.app, [row])
                history_by_id[identity] = row
        live = live_by_run.get(identity)
        if owner_type == "project":
            category = "project"
            artifact_kind: str | None = "projects"
            owner_label = str(scope.get("label") or artifact.get("owner_label") or identity)
            openable = True
        elif owner_type == "session":
            category = "agent-run" if row or live else "recovered"
            artifact_kind = "sessions" if row or live else None
            generated = (
                row.get("generated_title") if row else getattr(live, "generated_title", None)
            )
            auto_named = row.get("auto_named", 1) if row else getattr(live, "auto_named", True)
            owner_label = str(
                generated
                if auto_named and generated
                else (row or {}).get("name")
                or getattr(live, "name", None)
                or artifact.get("owner_label")
                or identity
            )
            openable = bool(row or live)
        else:
            category = "recovered"
            artifact_kind = None
            owner_label = str(artifact.get("owner_label") or identity)
            openable = False
        title, excerpt, modified_at = await asyncio.to_thread(_note_card, artifact_path, False)
        results.append(
            {
                "id": f"artifact:{artifact['id']}",
                "category": category,
                "kind": artifact_kind,
                "identity": identity,
                "owner_label": owner_label,
                "content_title": title,
                "project_scope_id": scope_id,
                "project_label": scope.get("label"),
                "project_root": scope.get("root"),
                "space_id": (row or {}).get("space_id") or getattr(live, "space_id", None),
                "backend": (row or {}).get("backend") or getattr(live, "backend", None),
                "run_state": (row or {}).get("final_state") or getattr(live, "state", None),
                "storage": "project",
                "path": str(artifact_path),
                "modified_at": modified_at,
                "excerpt": excerpt,
                "linked": True,
                "recovered": category == "recovered",
                "active": bool(live),
                "openable": openable,
            }
        )

    for scope in scopes:
        scope_id = str(scope["id"])
        for item in _scope_inventory(scope, artifacts_by_scope.get(scope_id, []))["unlinked"]:
            path = Path(str(item["path"]))
            try:
                resolved = str(path.resolve(strict=True))
            except OSError:
                continue
            if resolved in seen_paths:
                continue
            seen_paths.add(resolved)
            source_kind = str(item["kind"])
            identity = path.stem
            row = history_by_id.get(identity) if source_kind == "sessions" else None
            if source_kind == "sessions" and not row:
                candidate = await history.history_entry(identity)
                if candidate and candidate.get("backend") in {"claude", "codex"}:
                    row = candidate
                    if "automation_store" in request.app:
                        await _decorate_generated_titles(request.app, [row])
                    history_by_id[identity] = row
            if source_kind == "projects" and path.name == "project.md":
                category, inventory_kind, identity, openable = (
                    "project",
                    "projects",
                    scope_id,
                    True,
                )
                owner_label = str(scope.get("label") or scope_id)
            elif source_kind == "sessions" and row:
                category, inventory_kind, openable = "agent-run", "sessions", True
                owner_label = str(
                    row.get("generated_title")
                    if row.get("auto_named", 1) and row.get("generated_title")
                    else row.get("name") or identity
                )
            else:
                category, inventory_kind, openable = "recovered", None, False
                owner_label = f"Unlinked {source_kind.removesuffix('s') or 'note'} note"
            title, excerpt, modified_at = await asyncio.to_thread(_note_card, path, False)
            results.append(
                {
                    "id": f"recovered:{scope_id}:{path.name}",
                    "category": category,
                    "kind": inventory_kind,
                    "identity": identity,
                    "owner_label": owner_label,
                    "content_title": title,
                    "project_scope_id": scope_id,
                    "project_label": scope.get("label"),
                    "project_root": scope.get("root"),
                    "space_id": (row or {}).get("space_id"),
                    "backend": (row or {}).get("backend"),
                    "run_state": (row or {}).get("final_state"),
                    "storage": "project",
                    "path": str(path),
                    "modified_at": modified_at,
                    "excerpt": excerpt,
                    "linked": False,
                    "recovered": True,
                    "active": False,
                    "openable": openable,
                }
            )
    return sorted(results, key=lambda item: (-float(item["modified_at"]), item["owner_label"]))


async def list_note_shelf(request: web.Request) -> web.Response:
    return json_response({"items": await note_shelf_items(request)})


def _scope_inventory(scope: dict[str, Any], artifacts: list[dict[str, Any]]) -> dict[str, Any]:
    root = Path(str(scope["root"]))
    mux = root / ".swe-mux"
    known = {str((root / item["relative_path"]).resolve()) for item in artifacts}
    unlinked: list[dict[str, Any]] = []
    if mux.is_dir() and not mux.is_symlink():
        notes = mux / "notes"
        if notes.is_dir() and not notes.is_symlink():
            note_paths = [*notes.glob("*.md"), *notes.glob("*/*.md")]
            for path in sorted(note_paths)[:1000]:
                try:
                    resolved = path.resolve(strict=True)
                    if (
                        not resolved.is_relative_to(mux.resolve())
                        or path.is_symlink()
                        or path.parent.is_symlink()
                    ):
                        continue
                except OSError:
                    continue
                if str(resolved) not in known:
                    unlinked.append(
                        {
                            "path": str(path),
                            "kind": "projects" if path.parent == notes else path.parent.name,
                            "filename": path.name,
                        }
                    )
    return {
        "root_exists": root.is_dir(),
        "config_exists": (mux / "config.toml").is_file(),
        "rules_present_inert": (mux / "rules.toml").is_file(),
        "unlinked": unlinked,
        "scan_truncated": len(unlinked) >= 1000,
    }


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


async def list_projects(request: web.Request) -> web.Response:
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
    scope["inventory"] = _scope_inventory(scope, artifacts)
    scope["config"] = await read_project_config(scope["root"])
    all_scopes = await history.project_scopes(include_hidden=True)
    conflicts: list[dict[str, Any]] = []
    for item in scope["inventory"]["unlinked"]:
        relative = str(Path(item["path"]).resolve().relative_to(Path(scope["root"]).resolve()))
        peers = [
            candidate["id"]
            for candidate in all_scopes
            if candidate["id"] != scope["id"] and (Path(candidate["root"]) / relative).is_file()
        ]
        if peers:
            conflicts.append({**item, "other_project_scope_ids": peers})
    scope["inventory"]["conflicting"] = conflicts
    live_space_ids = set(request.app["spaces"].spaces)
    scope["detached_artifacts"] = [
        item
        for item in artifacts
        if (item["owner_type"] == "space" and item["owner_id"] not in live_space_ids)
        or (
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


async def find_project_notes(request: web.Request) -> web.Response:
    return json_response(
        {
            "items": await search_notes(
                request.query.get("cwd") or str(Path.cwd()), request.query.get("q", "")
            )
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
    sessions = [s.record.snapshot() for s in manager.sessions.values()]
    await _decorate_generated_titles(request.app, sessions)
    for field in ("space_id", "state", "backend"):
        value = request.query.get(field.removesuffix("_id") if field == "space_id" else field)
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
    spaces: SpaceManager = app["spaces"]
    space_id = spec.space_id
    if space_id not in spaces.spaces:
        raise ValueError(f"unknown space: {space_id}")
    space = spaces.spaces[space_id]
    config: Config = app["config"]
    backend = spec.backend or space.default_backend or config.default_backend
    seed_cwd = spec.cwd or space.default_cwd or config.startup_cwd or str(Path.cwd())
    project_started_at = time.perf_counter()
    project = await resolve_project(seed_cwd)
    startup_timing_ms["project_resolution"] = round(
        (time.perf_counter() - project_started_at) * 1000, 1
    )
    config_started_at = time.perf_counter()
    project_config = await read_project_config(seed_cwd, project=project)
    startup_timing_ms["project_config"] = round(
        (time.perf_counter() - config_started_at) * 1000, 1
    )
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
    cwd = spec.cwd or space.default_cwd
    if not cwd and project_values.get("default_cwd"):
        try:
            cwd = str(
                resolve_project_default_cwd(Path(project.root), str(project_values["default_cwd"]))
            )
            if not Path(cwd).is_dir():
                raise ValueError("directory does not exist")
        except ValueError as exc:
            cwd = seed_cwd
            await app["events"].emit(
                "project_default_cwd_rejected",
                source="project_file",
                project_scope_id=project.id,
                value=project_values.get("default_cwd"),
                error=str(exc),
            )
    cwd = cwd or seed_cwd
    executable = spec.executable
    argv = list(spec.argv)
    profile_id: str | None = None
    profile_env: dict[str, str] | None = None
    if backend == "shell" and not executable:
        profile_id = (
            spec.profile_id
            or space.default_profile_id
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
        space_id=space_id,
        exe=executable,
        args=argv,
        shell_profile_id=profile_id,
        profile_env=profile_env,
        project_label=str(project_values.get("project_label") or "") or None,
    )
    if isinstance(manager, SessionManager):
        spawn_values["project"] = project
        spawn_values["startup_started_at"] = startup_started_at
        spawn_values["startup_timing_ms"] = startup_timing_ms
    session = await manager.spawn(**spawn_values)
    return session


async def spawn_session(request: web.Request) -> web.Response:
    session = await _spawn_from_body(request.app, await request.json())
    return json_response(session.record.snapshot(), 201)


async def get_session(request: web.Request) -> web.Response:
    return json_response(
        request.app["sessions"].resolve(request.match_info["sid"]).record.snapshot()
    )


async def patch_session(request: web.Request) -> web.Response:
    session = request.app["sessions"].resolve(request.match_info["sid"])
    body = await request.json()
    if "name" in body:
        session.record.name = str(body["name"]).strip() or session.record.name
        session.record.auto_named = False
    if "space" in body:
        if body["space"] not in request.app["spaces"].spaces:
            raise ValueError("unknown space")
        session.record.space_id = body["space"]
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
    await events.emit(
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
    "turn_started",
    "turn_ended",
    "agent-turn-complete",
    "approval_needed",
    "approval-requested",
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
        raise web.HTTPForbidden(text="terminal image upload requires an explicit paste or drop action")
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


async def list_spaces(request: web.Request) -> web.Response:
    spaces: SpaceManager = request.app["spaces"]
    return json_response([s.snapshot() for s in spaces.spaces.values()])


async def create_space(request: web.Request) -> web.Response:
    body = await request.json()
    space = await request.app["spaces"].create(str(body.get("name") or "New space"))
    return json_response(space.snapshot(), 201)


async def patch_space(request: web.Request) -> web.Response:
    body = await request.json()
    profile_id = body.get("default_profile_id")
    if profile_id is not None and profile_id not in {
        profile.id for profile in request.app["config"].shell_profiles
    }:
        raise ValueError({"default_profile_id": "unknown shell profile"})
    if "default_cwd" in body and body["default_cwd"]:
        project = await resolve_project(str(body["default_cwd"]))
        await request.app["history"].register_project_scope(project)
    space = await request.app["spaces"].update(request.match_info["sid"], **body)
    return json_response(space.snapshot())


async def delete_space(request: web.Request) -> web.Response:
    sid = request.match_info["sid"]
    manager: SessionManager = request.app["sessions"]
    body = await request.json() if request.can_read_body else {}
    contained = [s for s in manager.sessions.values() if s.record.space_id == sid]
    disposition = str(body.get("disposition") or "reject")
    target = str(body.get("target_space") or "default")
    live = [s for s in contained if s.pty.isalive()]
    if live and disposition == "reject":
        raise ValueError("choose disposition=move or disposition=kill for live sessions")
    if disposition == "move":
        if target == sid or target not in request.app["spaces"].spaces:
            raise ValueError("invalid target space")
        for session in contained:
            session.record.space_id = target
            await request.app["history"].update_session_metadata(session.record)
            session.publish_update()
    elif disposition == "kill":
        await asyncio.gather(*(manager.stop(session.record.id) for session in live))
    elif live:
        raise ValueError("disposition must be move or kill")
    await request.app["spaces"].delete(sid)
    return json_response({"ok": True})


async def list_history(request: web.Request) -> web.Response:
    external_value = request.query.get("external")
    page = await request.app["history"].history_page(
        query=request.query.get("q", ""),
        backend=request.query.get("backend"),
        project=request.query.get("project"),
        state=request.query.get("state"),
        space=request.query.get("space"),
        external=(external_value.lower() == "true") if external_value is not None else None,
        date_from=float(request.query["date_from"]) if request.query.get("date_from") else None,
        date_to=float(request.query["date_to"]) if request.query.get("date_to") else None,
        cursor=request.query.get("cursor"),
        limit=int(request.query.get("limit", min(50, request.app["config"].history_limit))),
    )
    await _decorate_generated_titles(request.app, page["items"])
    return json_response(page)


async def list_history_projects(request: web.Request) -> web.Response:
    return json_response({"items": await request.app["history"].history_projects()})


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
    annotations = await request.app["automation_store"].annotations(
        agent_run_id=str(row["id"]), limit=200
    )
    await _decorate_generated_titles(request.app, [row])
    return json_response({"entry": row, "messages": messages, "annotations": annotations})


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
    target_space = str(body.get("space") or "default")
    requirements = {
        "native_id_missing": not row.get("native_id"),
        "cwd_missing": not row.get("cwd") or not Path(str(row["cwd"])).is_dir(),
        "transcript_unavailable": not row.get("transcript_path")
        or not Path(str(row["transcript_path"])).is_file(),
        "target_space_missing": target_space not in request.app["spaces"].spaces,
        "adapter_missing": row.get("backend") not in request.app["sessions"].adapters,
    }
    if code := next((key for key, failed in requirements.items() if failed), None):
        return json_response(
            {"error": code.replace("_", " "), "code": code},
            409 if code == "transcript_unavailable" else 422,
        )
    session = await request.app["sessions"].spawn(
        backend=str(row["backend"]),
        name=body.get("name") or f"{row['name']} resumed",
        cwd=str(row["cwd"]),
        space_id=target_space,
        resume_native_id=str(row["native_id"]),
    )
    space = request.app["spaces"].spaces[target_space]
    next_layout = attach_terminal(
        space.layout,
        session.record.id,
        target_id=body.get("target_session_id"),
        direction=body.get("direction"),
    )
    try:
        await request.app["spaces"].update(
            target_space, layout=next_layout, layout_revision=space.layout_revision
        )
    except Exception:
        await request.app["sessions"].stop(session.record.id)
        request.app["sessions"].sessions.pop(session.record.id, None)
        raise
    await request.app["automation_store"].add_lineage(
        str(row["id"]),
        session.record.agent_run_id or session.record.id,
        "resume",
        {"backend": row["backend"], "space_id": target_space},
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


async def list_processes(request: web.Request) -> web.Response:
    session_id = request.query.get("session")
    inspector: ProcessInspector = request.app["process_inspector"]
    return json_response(
        await inspector.snapshot(session_id) if session_id else await inspector.snapshot_all()
    )


async def process_action(request: web.Request) -> web.Response:
    body = await request.json()
    inspector: ProcessInspector = request.app["process_inspector"]
    return json_response(
        await inspector.act(str(body["session_id"]), int(body["pid"]), str(body["action"]))
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
        spaces: SpaceManager = request.app["spaces"]
        space = spaces.spaces[item.space_id]
        # A preview belongs beside whatever spawned it: group it as a tab in the
        # owning session's region instead of splitting off an unrelated one. Fall
        # back to a split when that session has no terminal in this layout.
        grouped = stack_leaf(space.layout, "preview", item.id, target_id=item.session_id)
        space.layout = (
            grouped
            if grouped is not None
            else attach_leaf(
                space.layout,
                "preview",
                item.id,
                target_id=str(body.get("target_session_id") or "") or None,
                direction=str(body.get("direction") or "horizontal"),
            )
        )
        space.layout_revision += 1
        await spaces.history.upsert_space(space)
    else:
        space = request.app["spaces"].spaces[item.space_id]
    await request.app["events"].emit(
        "preview_registered",
        session_id=item.session_id,
        source="user",
        preview_id=item.id,
        url=item.url,
    )
    return json_response({"preview": item.snapshot(), "space": space.snapshot()}, 201)


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


def _preview_runtime_bridge(prefix: str) -> str:
    encoded = json.dumps(prefix)
    return f"""<script>(function(){{
const prefix={encoded};
const route=function(value){{
  try {{
    const url=new URL(String(value),location.href);
    if(url.host===location.host&&!url.pathname.startsWith(prefix)){{
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


def rewrite_preview_html(data: bytes, prefix: str) -> bytes:
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
    bridge = _preview_runtime_bridge(prefix)
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
                        data = rewrite_preview_html(data, prefix)
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
    event_payload = hook_event_payload(payload)
    request.app["automation"].note_native_hook(session.record.id)
    await request.app["events"].emit(
        event_type, session_id=session.record.id, source="hook", **event_payload
    )
    previous = session.record.state
    next_state: SessionState | None = None
    next_detail: str | None = None
    semantic: str | None = None
    if event_type == "SessionStart":
        next_state = "idle"
    elif event_type in {"UserPromptSubmit", "turn_started"}:
        next_state = "working"
        semantic = "turn_started"
    elif event_type == "PreToolUse":
        next_state = "working"
        tool = payload.get("tool_name") or payload.get("name") or "tool"
        next_detail = str(tool)
        semantic = "tool_use"
    elif event_type in {"PostToolUse", "PostToolUseFailure"}:
        next_state = "working"
    elif event_type in {"PermissionRequest", "approval_needed", "approval-requested"}:
        next_state = "awaiting"
        tool = payload.get("tool_name") or payload.get("message") or "approval"
        next_detail = str(tool)
        semantic = "approval_needed"
    elif event_type == "Notification":
        notification = str(payload.get("notification_type") or "")
        if notification in {"permission_prompt", "elicitation_dialog", "idle_prompt"}:
            next_state = "awaiting"
            next_detail = str(payload.get("message") or notification)
            semantic = "approval_needed"
    elif event_type in {"Stop", "turn_ended", "agent-turn-complete"}:
        next_state = "idle"
        semantic = "turn_ended"
    if next_state:
        session.transition(next_state, next_detail, source="hook")
    if semantic and semantic != event_type:
        await request.app["events"].emit(
            semantic, session_id=session.record.id, source="hook", **event_payload
        )
    if session.record.state != previous:
        await request.app["events"].emit(
            "state_changed",
            session_id=session.record.id,
            source="hook",
            previous=previous,
            state=session.record.state,
            detail=session.record.state_detail,
        )
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
    spawn = body.get("spawn")
    if isinstance(spawn, dict):
        try:
            spawn_payload = {
                key: value
                for key, value in spawn.items()
                if key not in {"target_session_id", "direction"}
            }
            session = await _spawn_from_body(request.app, {**spawn_payload, "cwd": path})
            spaces: SpaceManager = request.app["spaces"]
            space = spaces.spaces[session.record.space_id]
            target = str(spawn.get("target_session_id") or "") or None
            direction = str(spawn.get("direction") or "") or None
            space.layout = attach_terminal(
                space.layout, session.record.id, target_id=target, direction=direction
            )
            space.layout_revision += 1
            await spaces.history.upsert_space(space)
            result["spawn"] = {
                "status": "created",
                "session": session.record.snapshot(),
                "space": space.snapshot(),
            }
        except Exception as exc:
            log.exception("worktree created but terminal spawn failed")
            result["spawn"] = {
                "status": "failed",
                "error": str(exc),
                "worktree_retained": True,
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
    await asyncio.create_subprocess_exec("explorer.exe", str(path))
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
    await request.app["events"].emit(
        "terminal_attached",
        session_id=session.record.id,
        source="daemon",
        connections=len(session.subscribers),
    )
    await ws.send_json({"type": "state", "snapshot": snapshot, "revision": revision})
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

    task = asyncio.create_task(sender())
    try:
        async for message in ws:
            if message.type == WSMsgType.BINARY:
                if session.input_owner == connection_id:
                    session.pty.write(message.data)
                    if time.monotonic() - session.last_input_event_ts >= 2:
                        session.last_input_event_ts = time.monotonic()
                        await request.app["events"].emit(
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
                    if time.monotonic() - session.last_input_event_ts >= 2:
                        session.last_input_event_ts = time.monotonic()
                        await request.app["events"].emit(
                            "terminal_input",
                            session_id=session.record.id,
                            source="daemon",
                            input_owner=True,
                            bytes=len(data.encode("utf-8")),
                        )
                    if frame.get("broadcast"):
                        await deliver_broadcast(
                            request.app["sessions"],
                            data,
                            request.app["events"],
                            source_id=session.record.id,
                        )
                elif frame.get("type") == "resize":
                    session.pty.resize(int(frame["cols"]), int(frame["rows"]))
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        if session.input_owner == connection_id:
            session.input_owner = None
        session.unsubscribe(subscriber)
        await request.app["events"].emit(
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
