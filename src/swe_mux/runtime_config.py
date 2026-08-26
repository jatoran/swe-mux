"""Applying a changed config to the live runtime without a restart."""

from __future__ import annotations

import asyncio
import json
import logging
import tomllib
from contextlib import suppress
from pathlib import Path

from aiohttp import web

from . import (
    app_keys as keys,
)
from .background_tasks import background
from .clipboard_store import ClipboardStore
from .config import Config, load_config
from .ghost_windows import GhostWindowSweeper
from .git_monitor import GitMonitor
from .harness import (
    HARNESSES,
)
from .launchers import (
    resolve_command,
)
from .logsetup import set_log_level
from .operational_telemetry import OperationalTelemetryStore
from .processes import ProcessInspector
from .provider_accounts import (
    ProviderAccountManager,
)
from .session import (
    SessionManager,
)
from .session_recovery import SessionRecoveryStore
from .status_timeline import StatusTimelineStore
from .voice import (
    VoiceService,
)

log = logging.getLogger(__name__)


CONFIG_WATCH_LOOP = "config-watch"


def config_mtime(path: Path) -> int:
    """Config mtime, or 0 when the file is absent or momentarily unreadable.

    Editors save by delete+rename, so `exists()` and `stat()` genuinely disagree.
    An unguarded stat here used to kill config hot reload for the daemon's
    lifetime on a single unlucky poll.
    """
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return 0


async def watch_config(app: web.Application) -> None:
    config: Config = app[keys.CONFIG]
    path = config.config_path
    if path is None:
        return
    modified = config_mtime(path)
    while True:
        await asyncio.sleep(1)
        with background.iteration(CONFIG_WATCH_LOOP):
            current = config_mtime(path)
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
                apply_runtime_config(app, changed)
                await app[keys.EVENTS].emit(
                    "configuration_changed", source="external_file", revision=config.revision
                )
            except (OSError, ValueError, TypeError, tomllib.TOMLDecodeError) as exc:
                await app[keys.EVENTS].emit(
                    "configuration_error", source="external_file", error=str(exc)
                )


#: Config fields that change which endpoint a completion goes to, or whether the
#: one it goes to is still the one that was proven. Editing any of them must drop
#: both cached answers immediately: the readiness verdict, and the per-Project
#: gate that was resolved under it.
LLM_ENDPOINT_FIELDS = frozenset({"llm_provider", "custom_llm_base_url", "custom_llm_model"})


def forget_llm_readiness(app: web.Application) -> None:
    """Drop the cached provider verdict and every gate answer resolved under it.

    Called on an endpoint edit, a key write, and a verification - the three acts
    that can flip readiness. Without the second half a Project would keep running
    under a five-second-old closure computed against the previous verdict, which
    is exactly long enough for a verify press to look like it did nothing.
    """
    if cache := app.get(keys.LLM_READINESS_CACHE):
        cache.clear()
    if gate_cache := app.get(keys.AUTOMATION_GATE_CACHE):
        gate_cache.clear()


def apply_runtime_config(app: web.Application, changed: set[str]) -> None:
    config: Config = app[keys.CONFIG]
    if changed & LLM_ENDPOINT_FIELDS:
        forget_llm_readiness(app)
        # The measurement described the endpoint that *was* configured. Keeping
        # it across an edit is the one way this feature could do harm rather than
        # merely fail to help: a base URL changed from an OpenRouter-shaped proxy
        # to a local Ollama would otherwise keep its `annotated` record, and the
        # next call would carry a `provider` routing block and a cache breakpoint
        # to a server that has never heard of either. Dropped rather than
        # re-probed, because re-proving an endpoint is the verify press's job and
        # doing it implicitly here would spend a completion nobody asked for.
        if capabilities := app.get(keys.LLM_CAPABILITIES):
            capabilities.clear()
    if "log_level" in changed:
        with suppress(ValueError):  # _validate already constrains the value
            set_log_level(config.log_level)
    sessions: SessionManager | None = app.get(keys.SESSIONS)
    if sessions:
        if "scrollback_bytes" in changed:
            sessions.max_scrollback = config.scrollback_bytes
        if "attach_replay_bytes" in changed:
            # Every live session carries its own copy and reads it at attach time,
            # so the budget is pushed down rather than left to apply only to
            # sessions spawned after the change. Unlike `scrollback_bytes` above -
            # which sizes a buffer already allocated - this is a number consulted
            # per attach, so a live update genuinely takes effect on the next one.
            sessions.attach_replay_bytes = config.attach_replay_bytes
            for session in sessions.sessions.values():
                session.attach_replay_bytes = config.attach_replay_bytes
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
    git_monitor: GitMonitor | None = app.get(keys.GIT_MONITOR)
    if git_monitor and "git_poll_seconds" in changed:
        git_monitor.cadence = config.git_poll_seconds
    process_inspector: ProcessInspector | None = app.get(keys.PROCESS_INSPECTOR)
    if process_inspector:
        if "process_poll_seconds" in changed:
            process_inspector.cadence = config.process_poll_seconds
        if "process_orphan_grace_seconds" in changed:
            process_inspector.orphan_grace_seconds = config.process_orphan_grace_seconds
    recovery: SessionRecoveryStore | None = app.get(keys.SESSION_RECOVERY)
    if recovery:
        # Three bounds the store reads at each checkpoint and each prune, so they
        # apply live. `session_recovery_enabled` is restart-scoped because it gates
        # unexpected-loss restoration and checkpoint capture; the durable registry
        # itself stays present for explicit inactive sessions.
        if "session_recovery_checkpoint_bytes" in changed:
            recovery.checkpoint_bytes = (
                config.session_recovery_checkpoint_bytes if config.session_recovery_enabled else 0
            )
        if "session_recovery_retention_days" in changed:
            recovery.retention_days = config.session_recovery_retention_days
        if "session_recovery_max_sessions" in changed:
            recovery.max_cold_sessions = config.session_recovery_max_sessions
    timeline_store: StatusTimelineStore | None = app.get(keys.STATUS_TIMELINE)
    if timeline_store and "status_timeline_retention_days" in changed:
        timeline_store.retention_days = config.status_timeline_retention_days
    ghost_windows: GhostWindowSweeper | None = app.get(keys.GHOST_WINDOWS)
    if ghost_windows:
        if "ghost_window_poll_seconds" in changed:
            ghost_windows.cadence = config.ghost_window_poll_seconds
        if "ghost_window_sweep_enabled" in changed:
            # The loop reads `enabled` each tick, so a live toggle takes effect
            # without restarting the task.
            ghost_windows.enabled = config.ghost_window_sweep_enabled
    provider_accounts: ProviderAccountManager | None = app.get(keys.PROVIDER_ACCOUNTS)
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
    clipboard: ClipboardStore | None = app.get(keys.CLIPBOARD)
    if clipboard and any(field.startswith("clipboard_history_") for field in changed):
        # Owns its own side effects: disabling drops the ring, and turning
        # persistence off deletes the rows already written.
        clipboard.apply_config(config)
    voice: VoiceService | None = app.get(keys.VOICE)
    if voice:
        if "tts_lexicon" in changed:
            # Rebuilds the engine's merged lexicon and drops the per-word and
            # preview caches — without this the change silently waits for a
            # daemon restart.
            voice.apply_lexicon()
        elif "tts_kokoro_speed" in changed:
            # Audition previews cache per voice at synthesis-time speed.
            voice.invalidate_kokoro_previews()
    telemetry: OperationalTelemetryStore | None = app.get(keys.TELEMETRY)
    if telemetry and "operational_telemetry_retention_days" in changed:
        telemetry.retention_days = config.operational_telemetry_retention_days
    if telemetry and "process_evidence_retention_days" in changed:
        telemetry.process_retention_days = config.process_evidence_retention_days
