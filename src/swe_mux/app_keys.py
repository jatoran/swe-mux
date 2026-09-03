"""Typed handles for everything the daemon publishes into its `web.Application`.

aiohttp deprecated plain-string application keys: every `app["sessions"]` read
raises `NotAppKeyWarning`, which is the bulk of the warning noise a test run
prints, and a string key carries no type at all, so `app["sessions"]` was `Any`
and a typo was a runtime `KeyError` rather than a check failure.

Each key below is a `web.AppKey` - a distinct object, not a string - so the
mapping is typed at both ends and mypy resolves `app[SESSIONS]` to
`SessionManager`.

The keys are annotated (`AppKey[SessionManager]`) rather than constructed with a
runtime type argument (`AppKey("sessions", SessionManager)`) on purpose: the
runtime argument is used for `repr` only, and requiring it would make this
module import the whole daemon at startup and put an import cycle between the
key table and the services it names. Under `TYPE_CHECKING` the types cost
nothing and mypy sees exactly the same thing.

Route modules import this module as a namespace (`from . import app_keys as
keys`) so a handler reads `request.app[keys.SESSIONS]`.
"""

from __future__ import annotations

import asyncio
from collections import deque
from pathlib import Path
from typing import TYPE_CHECKING, Any

from aiohttp import web

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from .agent_context import AgentContextService
    from .agent_messaging import AgentMessagingService
    from .assistant import AssistantService, AssistantStore
    from .attention_narration import AttentionNarrator
    from .attention_ranking import AttentionRankingService
    from .auto_delivery import AutoDeliveryController
    from .automation import AutomationEngine
    from .automation_store import AutomationStore
    from .behavioral_consumers import BehavioralConsumerService
    from .clipboard_store import ClipboardStore
    from .code_graph import CodeGraphStore
    from .config import Config
    from .deterministic_consumers import DeterministicConsumerService
    from .device_presence import DevicePresenceStore
    from .event_bus import EventBus
    from .fleet_intelligence import FleetIntelligence
    from .frontend_overlay import FrontendChoice, OverlayStore
    from .ghost_windows import GhostWindowSweeper
    from .git_monitor import GitMonitor
    from .git_provenance import GitProvenanceService
    from .history import HistoryIndex
    from .history_backfill import HistoryBackfillManager
    from .history_scan import HistoryScanManager
    from .land_queue import LandQueueService
    from .land_store import LandStore
    from .llm_endpoint import CapabilityStore, LlmReadiness
    from .loop_lag import LoopLagMonitor
    from .mcp import McpService
    from .mcp_tools import LiveSnapshotStore
    from .meta_hooks import MetaHookEngine
    from .network_usage import NetworkUsage
    from .openrouter import OpenRouterClient
    from .operational_telemetry import OperationalTelemetryStore
    from .plugins import PluginManager
    from .process_reaper import ProcessReaper
    from .processes import PreviewRegistry, ProcessInspector
    from .project_actions import ProjectActionService
    from .project_context import ProjectContextService
    from .project_watcher import ProjectFileWatcher
    from .projects import ProjectManager
    from .prompt_library import PromptLibrary
    from .prompt_queue import PromptQueueService, PromptQueueStore
    from .provider_accounts import ProviderAccountManager
    from .push import PushStore
    from .readiness_watch import ReadinessWatcher
    from .scan_timeline import ScanTimelineService
    from .schedule_store import ScheduleStore
    from .scheduler import ScheduleService
    from .secret_store import PlatformSecretStore
    from .session import SessionManager
    from .session_control import SessionControlService
    from .session_recovery import SessionRecoveryStore
    from .session_watch import SessionWatchService
    from .settings_store import SettingsStore
    from .stall_watchdog import StallWatchdog
    from .startup_phases import StartupTimeline
    from .status_timeline import StatusTimelineStore
    from .storage_usage import StorageUsage
    from .supervisor_client import SupervisorClient
    from .telemetry_service import CanonicalTelemetryService
    from .tier0_store import Tier0Store
    from .update_check import UpdateChecker
    from .update_install import UpdateInstaller
    from .usage import UsageManager
    from .voice import VoiceService, VoiceStore
    from .worktree_verify import VerifyApprovalStore

# --- process-level identity and configuration -------------------------------

CONFIG: web.AppKey[Config] = web.AppKey("config")
#: Regenerated per daemon process. Session revisions restart from zero when a
#: daemon adopts supervisor-owned PTYs, so revision alone cannot distinguish a
#: stale pre-restart response from the new daemon's current state.
DAEMON_GENERATION: web.AppKey[str] = web.AppKey("daemon_generation")
FRONTEND_DIR: web.AppKey[Path] = web.AppKey("frontend_dir")
#: Why `FRONTEND_DIR` is what it is: a verified overlay in the data dir, or the
#: bundled tree and the reason the overlay was not used. Per process rather than
#: persisted, because it describes what this daemon is serving right now and a
#: record that outlived its process would be a false claim about that.
#: Absent when a caller passed an explicit `frontend_dir` override.
FRONTEND_CHOICE: web.AppKey[FrontendChoice] = web.AppKey("frontend_choice")
#: Installs, reverts and describes the data dir's overlay. Present in every app,
#: including one built with an explicit frontend override, because "what is
#: installed" is a question about the data dir and not about what is served.
FRONTEND_OVERLAY: web.AppKey[OverlayStore] = web.AppKey("frontend_overlay")
NETWORK_USAGE: web.AppKey[NetworkUsage] = web.AppKey("network_usage")
STARTUP: web.AppKey[StartupTimeline] = web.AppKey("startup")
RUNTIME_BUILD: web.AppKey[asyncio.Task[None]] = web.AppKey("runtime_build")

# --- shutdown and desktop control -------------------------------------------

#: Mutable holder because aiohttp freezes app keys once started; carries the
#: externally-signaled shutdown intent (quit vs restart/detach) to cleanup.
SHUTDOWN_STATE: web.AppKey[dict[str, Any]] = web.AppKey("shutdown_state")
DESKTOP_CONTROL_TOKEN: web.AppKey[str] = web.AppKey("desktop_control_token")
DESKTOP_SHUTDOWN_EVENT: web.AppKey[asyncio.Event] = web.AppKey("desktop_shutdown_event")
DAEMON_STOP_EVENT: web.AppKey[asyncio.Event] = web.AppKey("daemon_stop_event")
DAEMON_RELAUNCH_COMMAND: web.AppKey[list[str]] = web.AppKey("daemon_relaunch_command")

# --- rate-limit and concurrency state ---------------------------------------

PREVIEW_HTTP_SEMAPHORE: web.AppKey[asyncio.Semaphore] = web.AppKey("preview_http_semaphore")
PREVIEW_WS_SEMAPHORE: web.AppKey[asyncio.Semaphore] = web.AppKey("preview_ws_semaphore")
HOOK_INGRESS_WINDOWS: web.AppKey[dict[str, deque[float]]] = web.AppKey("hook_ingress_windows")
MCP_RATE_WINDOWS: web.AppKey[dict[str, deque[float]]] = web.AppKey("mcp_rate_windows")
MCP_TOOLS_WINDOWS: web.AppKey[dict[str, deque[float]]] = web.AppKey("mcp_tools_windows")
#: Keyed by (attachment workspace root, session id): one lock per workspace a
#: session writes into, not one per session.
ATTACHMENT_LOCKS: web.AppKey[dict[tuple[str, str], asyncio.Lock]] = web.AppKey("attachment_locks")

# --- core runtime handles ----------------------------------------------------

EVENTS: web.AppKey[EventBus] = web.AppKey("events")
SESSIONS: web.AppKey[SessionManager] = web.AppKey("sessions")
PROJECTS: web.AppKey[ProjectManager] = web.AppKey("projects")
HISTORY: web.AppKey[HistoryIndex] = web.AppKey("history")
HISTORY_BACKFILLS: web.AppKey[HistoryBackfillManager] = web.AppKey("history_backfills")
HISTORY_SCAN: web.AppKey[HistoryScanManager] = web.AppKey("history_scan")
SUPERVISOR: web.AppKey[SupervisorClient] = web.AppKey("supervisor")
REAPER: web.AppKey[ProcessReaper] = web.AppKey("reaper")
MCP: web.AppKey[McpService] = web.AppKey("mcp")
LOOP_LAG: web.AppKey[LoopLagMonitor] = web.AppKey("loop_lag")
STALL_WATCHDOG: web.AppKey[StallWatchdog] = web.AppKey("stall_watchdog")

# --- stores and services -----------------------------------------------------

AGENT_CONTEXT: web.AppKey[AgentContextService] = web.AppKey("agent_context")
AGENT_MESSAGING: web.AppKey[AgentMessagingService] = web.AppKey("agent_messaging")
ASSISTANT: web.AppKey[AssistantService] = web.AppKey("assistant")
ASSISTANT_STORE: web.AppKey[AssistantStore] = web.AppKey("assistant_store")
ATTENTION_NARRATOR: web.AppKey[AttentionNarrator] = web.AppKey("attention_narrator")
ATTENTION_RANKING: web.AppKey[AttentionRankingService] = web.AppKey("attention_ranking")
AUTO_DELIVERY: web.AppKey[AutoDeliveryController] = web.AppKey("auto_delivery")
AUTOMATION: web.AppKey[AutomationEngine] = web.AppKey("automation")
AUTOMATION_STORE: web.AppKey[AutomationStore] = web.AppKey("automation_store")
BEHAVIORAL_CONSUMERS: web.AppKey[BehavioralConsumerService] = web.AppKey("behavioral_consumers")
CLIPBOARD: web.AppKey[ClipboardStore] = web.AppKey("clipboard")
CODE_GRAPH: web.AppKey[CodeGraphStore] = web.AppKey("code_graph")
DETERMINISTIC_CONSUMERS: web.AppKey[DeterministicConsumerService] = web.AppKey(
    "deterministic_consumers"
)
DEVICE_PRESENCE: web.AppKey[DevicePresenceStore] = web.AppKey("device_presence")
FLEET: web.AppKey[FleetIntelligence] = web.AppKey("fleet")
GHOST_WINDOWS: web.AppKey[GhostWindowSweeper] = web.AppKey("ghost_windows")
GIT_MONITOR: web.AppKey[GitMonitor] = web.AppKey("git_monitor")
GIT_PROVENANCE: web.AppKey[GitProvenanceService] = web.AppKey("git_provenance")
HOOKS: web.AppKey[MetaHookEngine] = web.AppKey("hooks")
LAND_QUEUE: web.AppKey[LandQueueService] = web.AppKey("land_queue")
LAND_STORE: web.AppKey[LandStore] = web.AppKey("land_store")
OPENROUTER: web.AppKey[OpenRouterClient] = web.AppKey("openrouter")
PREVIEWS: web.AppKey[PreviewRegistry] = web.AppKey("previews")
PROCESS_INSPECTOR: web.AppKey[ProcessInspector] = web.AppKey("process_inspector")
PLUGINS: web.AppKey[PluginManager] = web.AppKey("plugins")
PROJECT_ACTIONS: web.AppKey[ProjectActionService] = web.AppKey("project_actions")
PROJECT_CONTEXTS: web.AppKey[ProjectContextService] = web.AppKey("project_contexts")
PROJECT_WATCHER: web.AppKey[ProjectFileWatcher] = web.AppKey("project_watcher")
PROMPT_LIBRARY: web.AppKey[PromptLibrary] = web.AppKey("prompt_library")
PROMPT_QUEUE: web.AppKey[PromptQueueService] = web.AppKey("prompt_queue")
PROMPT_QUEUE_STORE: web.AppKey[PromptQueueStore] = web.AppKey("prompt_queue_store")
PROVIDER_ACCOUNTS: web.AppKey[ProviderAccountManager] = web.AppKey("provider_accounts")
PUSH_STORE: web.AppKey[PushStore] = web.AppKey("push_store")
READINESS_WATCH: web.AppKey[ReadinessWatcher] = web.AppKey("readiness_watch")
RUNTIME_INVENTORIES: web.AppKey[LiveSnapshotStore] = web.AppKey("runtime_inventories")
SCAN_TIMELINE: web.AppKey[ScanTimelineService] = web.AppKey("scan_timeline")
SCHEDULE_STORE: web.AppKey[ScheduleStore] = web.AppKey("schedule_store")
SCHEDULES: web.AppKey[ScheduleService] = web.AppKey("schedules")
SECRET_STORE: web.AppKey[PlatformSecretStore] = web.AppKey("secret_store")
SESSION_CONTROL: web.AppKey[SessionControlService] = web.AppKey("session_control")
SESSION_RECOVERY: web.AppKey[SessionRecoveryStore] = web.AppKey("session_recovery")
SESSION_WATCH: web.AppKey[SessionWatchService] = web.AppKey("session_watch")
SETTINGS_STORE: web.AppKey[SettingsStore] = web.AppKey("settings_store")
STATUS_TIMELINE: web.AppKey[StatusTimelineStore] = web.AppKey("status_timeline")
STORAGE_USAGE: web.AppKey[StorageUsage] = web.AppKey("storage_usage")
TELEMETRY: web.AppKey[OperationalTelemetryStore] = web.AppKey("telemetry")
CANONICAL_TELEMETRY: web.AppKey[CanonicalTelemetryService] = web.AppKey(
    "canonical_telemetry"
)
TIER0: web.AppKey[Tier0Store] = web.AppKey("tier0")
UPDATE_CHECK: web.AppKey[UpdateChecker] = web.AppKey("update_check")
UPDATE_INSTALL: web.AppKey[UpdateInstaller] = web.AppKey("update_install")
USAGE: web.AppKey[UsageManager] = web.AppKey("usage")
VERIFY_APPROVALS: web.AppKey[VerifyApprovalStore] = web.AppKey("verify_approvals")
VOICE: web.AppKey[VoiceService] = web.AppKey("voice")
VOICE_STORE: web.AppKey[VoiceStore] = web.AppKey("voice_store")

# --- gate closures and their caches ------------------------------------------

#: `(root) -> the automation ids enabled for that Project`, memoized behind
#: `AUTOMATION_GATE_CACHE` because it runs on every Tier 0 write.
AUTOMATION_GATE: web.AppKey[Callable[[str], Awaitable[frozenset[str]]]] = web.AppKey(
    "automation_gate"
)
AUTOMATION_GATE_CACHE: web.AppKey[dict[str, tuple[float, frozenset[str]]]] = web.AppKey(
    "automation_gate_cache"
)
LLM_READY: web.AppKey[Callable[[], Awaitable[LlmReadiness]]] = web.AppKey("llm_ready")
LLM_READINESS_CACHE: web.AppKey[dict[str, tuple[float, LlmReadiness]]] = web.AppKey(
    "llm_readiness_cache"
)
#: What each provider's endpoint was measured to be capable of, hydrated at start
#: from the durable verification row and refreshed by the verify route. Read
#: synchronously by the per-request endpoint resolver, which is why it is a live
#: object here rather than a value threaded through `Config`.
LLM_CAPABILITIES: web.AppKey[CapabilityStore] = web.AppKey("llm_capabilities")

# --- task sets owned by the composition root ---------------------------------

AUTOMATION_TASKS: web.AppKey[set[asyncio.Task[Any]]] = web.AppKey("automation_tasks")
#: One entry per running Project Action step that declared `timeout_seconds`.
ACTION_TIMEOUT_TASKS: web.AppKey[set[asyncio.Task[Any]]] = web.AppKey("action_timeout_tasks")
#: One entry per worktree-removal purge in flight.
GRAVEYARD_TASKS: web.AppKey[set[asyncio.Task[Any]]] = web.AppKey("graveyard_tasks")
#: The pid the heartbeat named when this daemon started, or -1. Captured before
#: `daemon_started` stamps our own pid over it, because the database-maintenance
#: phase has to know whether the predecessor is still holding `mux.db` and a
#: probe taken later reads this process.
PREDECESSOR_PID: web.AppKey[int] = web.AppKey("predecessor_pid")
STARTUP_DEFERRED_TASKS: web.AppKey[list[asyncio.Task[Any]]] = web.AppKey("startup_deferred_tasks")
RECONCILE_TASK: web.AppKey[asyncio.Task[Any] | None] = web.AppKey("reconcile_task")
HISTORY_SEARCH_MAINTENANCE_TASK: web.AppKey[asyncio.Task[Any] | None] = web.AppKey(
    "history_search_maintenance_task"
)
