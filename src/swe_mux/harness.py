from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path
from typing import Literal, TypeGuard

StateSource = Literal["hook", "transcript", "pty", "cli_state"]
MeasurementSource = Literal["transcript", "none"]
ToolCatalogSource = Literal["documented_catalog", "runtime_dependent"]
Backend = Literal["shell", "claude", "codex", "omp"]
AdapterFamily = Literal["claude", "codex", "omp"]

DataHomeResolver = Callable[[], Path]
ToolCatalog = tuple[tuple[str, str], ...]


class HarnessLevel(IntEnum):
    """Derived product surface available for a harness."""

    launchable = 0
    identified = 1
    observed = 2
    hooked = 3
    managed = 4


@dataclass(frozen=True, slots=True)
class HarnessDescriptor:
    """Declared identity and capabilities for one agent harness."""

    name: str
    display_name: str
    executable: str
    default_args: tuple[str, ...]
    data_home: DataHomeResolver
    adapter_family: AdapterFamily
    config_dir_name: str
    script_base_name: str
    rollout_file_prefix: str | None
    reports_transcript_path: bool
    external_usage_command: bool
    provider_account_management: bool
    hook_ordering_guarantee: bool
    # Whether the harness's TUI repaints or reflows content that already sits in
    # xterm scrollback (Codex reflows its normal-screen transcript on resize; OMP
    # continuously repaints its tail on the normal screen). The frontend keeps
    # such harnesses on the DOM renderer under the `auto` preference because
    # full-screen redraws can corrupt WebGL scrollback while the viewport is
    # off-tail. An alternate-screen TUI (Claude) never rewrites scrollback, so
    # it stays WebGL-eligible.
    repaints_scrollback: bool

    state_sources: tuple[StateSource, ...]
    measurement_source: MeasurementSource

    reports_conversation_rollover: bool
    assigns_conversation_id: bool
    resolves_transcript_by_cwd: bool

    submission: str
    root_completion: str
    screen: str

    native_hooks: bool
    transcript: str | None
    pty: str | None
    normalized_events: tuple[str, ...]

    tool_catalog: ToolCatalog
    tool_catalog_source: ToolCatalogSource
    hook_events: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.name or not self.display_name:
            raise ValueError("harness identity fields must not be empty")
        if len(set(self.state_sources)) != len(self.state_sources):
            raise ValueError(f"duplicate state source for harness {self.name}")

    @property
    def level(self) -> HarnessLevel:
        """Derive the display tier from state and measurement capabilities.

        Measurement without lifecycle state can identify a transcript. Any state
        source makes the harness observed. Hooks add the hooked tier, while hooks
        plus transcript measurements provide the complete managed surface.
        """
        has_hooks = "hook" in self.state_sources
        has_measurements = self.measurement_source == "transcript"
        if has_hooks and has_measurements:
            return HarnessLevel.managed
        if has_hooks:
            return HarnessLevel.hooked
        if self.state_sources:
            return HarnessLevel.observed
        if has_measurements:
            return HarnessLevel.identified
        return HarnessLevel.launchable

    def delivery_etiquette(self) -> dict[str, str]:
        return {
            "submission": self.submission,
            "root_completion": self.root_completion,
            "screen": self.screen,
        }

    def automation_capabilities(self) -> dict[str, object]:
        return {
            "native_hooks": self.native_hooks,
            "transcript": self.transcript,
            "pty": self.pty,
            "normalized_events": list(self.normalized_events),
        }


def _claude_data_home() -> Path:
    configured = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(configured).expanduser() if configured else Path.home() / ".claude"


def _codex_data_home() -> Path:
    configured = os.environ.get("CODEX_HOME")
    return Path(configured).expanduser() if configured else Path.home() / ".codex"


def _omp_data_home() -> Path:
    configured = os.environ.get("PI_CODING_AGENT_DIR")
    return Path(configured).expanduser() if configured else Path.home() / ".omp" / "agent"


_NORMALIZED_AGENT_EVENTS = (
    "turn_started",
    "turn_ended",
    "tool_use",
    "tool_result",
    "approval_needed",
)

_CLAUDE_TOOLS: ToolCatalog = (
    ("Agent", "Spawn a subagent with an isolated context"),
    ("AskUserQuestion", "Request structured user input"),
    ("Bash", "Run shell commands"),
    ("Edit", "Apply exact text edits"),
    ("EnterPlanMode", "Enter planning mode"),
    ("ExitPlanMode", "Leave planning mode"),
    ("Glob", "Find files by pattern"),
    ("Grep", "Search file contents"),
    ("LSP", "Query language servers"),
    ("NotebookEdit", "Edit notebook cells"),
    ("Read", "Read files"),
    ("Skill", "Load an installed skill"),
    ("TaskCreate", "Create tracked work"),
    ("TaskGet", "Read tracked work"),
    ("TaskList", "List tracked work"),
    ("TaskOutput", "Read background task output"),
    ("TaskStop", "Stop background work"),
    ("TaskUpdate", "Update tracked work"),
    ("WebFetch", "Fetch a web page"),
    ("WebSearch", "Search the web"),
    ("Write", "Write a file"),
)

_CODEX_TOOLS: ToolCatalog = (
    ("shell", "Run commands under the active sandbox and approval policy"),
    ("apply_patch", "Apply structured workspace patches"),
    ("plan", "Track multi-step work"),
    ("skills", "Load installed skills"),
    ("multi-agent", "Coordinate isolated agent threads"),
    ("web", "Search or fetch when enabled"),
    ("MCP", "Use tools exposed by configured MCP servers"),
    ("apps", "Use enabled app and connector tools"),
)

_OMP_TOOLS: ToolCatalog = (
    ("read", "Read files, directories, archives, databases, documents, URLs, and virtual paths"),
    ("write", "Create or overwrite files, archive entries, database rows, and xd:// devices"),
    ("edit", "Apply hashline edits with stale-anchor recovery"),
    ("ast_edit", "Preview and apply structural ast-grep rewrites [xd://]"),
    ("ast_grep", "Run structural queries across tree-sitter grammars [xd://]"),
    ("grep", "Search files, globs, and virtual paths with regular expressions"),
    ("glob", "Find paths using glob patterns"),
    ("bash", "Run workspace shell commands, PTYs, and background jobs"),
    ("eval", "Run persistent Python and JavaScript cells"),
    ("lsp", "Query language-server diagnostics and code intelligence [xd://]"),
    ("debug", "Drive Debug Adapter Protocol sessions [xd://]"),
    ("security_scan", "Plan and run native security reviews [xd://, gated]"),
    ("task", "Run parallel or workspace-isolated subagents"),
    ("hub", "Coordinate agents and background jobs"),
    ("todo", "Maintain the ordered session task list"),
    ("ask", "Request structured user input"),
    ("browser", "Drive Chromium and CDP-attached applications [xd://]"),
    ("computer", "Control host windows, input, screenshots, accessibility, and clipboard"),
    ("web_search", "Search configured web providers with citations"),
    ("github", "Operate GitHub repositories, pull requests, issues, and Actions [xd://, gated]"),
    ("generate_image", "Generate or edit images [xd://, gated]"),
    ("inspect_image", "Analyze local images with a vision model [xd://, conditional]"),
    ("tts", "Generate speech audio [xd://, gated]"),
    ("checkpoint", "Mark conversation state for later collapse [xd://, gated]"),
    ("rewind", "Prune exploratory context while retaining a report [xd://, gated]"),
    ("retain", "Store durable memory [xd://, gated]"),
    ("recall", "Search durable memory [xd://, gated]"),
    ("reflect", "Synthesize an answer from durable memory [xd://, gated]"),
    ("memory_edit", "Update or invalidate durable memories [xd://, gated]"),
    ("learn", "Capture a reusable lesson or managed skill"),
    ("manage_skill", "Create or update isolated managed skills"),
)

_CLAUDE_HOOK_EVENTS = (
    "SessionStart",
    "UserPromptSubmit",
    "PreToolUse",
    "PostToolUse",
    "PostToolUseFailure",
    "PermissionRequest",
    "Notification",
    "SubagentStart",
    "SubagentStop",
    "Stop",
    "SessionEnd",
)

_CODEX_HOOK_EVENTS = (
    "SessionStart",
    "UserPromptSubmit",
    "PreToolUse",
    "PermissionRequest",
    "PostToolUse",
    "SubagentStart",
    "SubagentStop",
    "Stop",
    "SessionEnd",
)

_OMP_HOOK_EVENTS = (
    "SessionStart",
    "UserPromptSubmit",
    "turn_started",
    "turn_ended",
    "task_started",
    "task_complete",
    "PreToolUse",
    "PostToolUse",
    "PostToolUseFailure",
    "PermissionRequest",
    "approval_resolved",
    "context_compacted",
    "SessionEnd",
)


HARNESSES: dict[str, HarnessDescriptor] = {
    "claude": HarnessDescriptor(
        name="claude",
        display_name="Claude Code",
        executable="claude.exe",
        default_args=(),
        data_home=_claude_data_home,
        adapter_family="claude",
        config_dir_name=".claude",
        script_base_name="claude",
        rollout_file_prefix=None,
        reports_transcript_path=True,
        external_usage_command=True,
        provider_account_management=True,
        hook_ordering_guarantee=False,
        repaints_scrollback=False,
        state_sources=("transcript", "hook", "pty", "cli_state"),
        measurement_source="transcript",
        reports_conversation_rollover=True,
        assigns_conversation_id=True,
        resolves_transcript_by_cwd=True,
        submission="terminal_line",
        root_completion="stop_or_transcript",
        screen="alternate",
        native_hooks=True,
        transcript="semantic",
        pty="telemetry",
        normalized_events=_NORMALIZED_AGENT_EVENTS,
        tool_catalog=_CLAUDE_TOOLS,
        tool_catalog_source="documented_catalog",
        hook_events=_CLAUDE_HOOK_EVENTS,
    ),
    "codex": HarnessDescriptor(
        name="codex",
        display_name="Codex",
        executable="codex.exe",
        default_args=(),
        data_home=_codex_data_home,
        adapter_family="codex",
        config_dir_name=".codex",
        script_base_name="codex",
        rollout_file_prefix="rollout-",
        reports_transcript_path=True,
        external_usage_command=True,
        provider_account_management=True,
        hook_ordering_guarantee=False,
        repaints_scrollback=True,
        state_sources=("transcript", "hook", "pty"),
        measurement_source="transcript",
        reports_conversation_rollover=False,
        assigns_conversation_id=False,
        resolves_transcript_by_cwd=False,
        submission="terminal_line",
        root_completion="task_complete",
        screen="normal",
        native_hooks=True,
        transcript="semantic",
        pty="telemetry",
        normalized_events=_NORMALIZED_AGENT_EVENTS,
        tool_catalog=_CODEX_TOOLS,
        tool_catalog_source="runtime_dependent",
        hook_events=_CODEX_HOOK_EVENTS,
    ),
    "omp": HarnessDescriptor(
        name="omp",
        display_name="oh-my-pi",
        executable="omp",
        default_args=(),
        data_home=_omp_data_home,
        adapter_family="omp",
        config_dir_name=".omp",
        script_base_name="omp",
        rollout_file_prefix=None,
        reports_transcript_path=True,
        external_usage_command=False,
        provider_account_management=False,
        hook_ordering_guarantee=True,
        repaints_scrollback=True,
        state_sources=("hook", "transcript", "pty"),
        measurement_source="transcript",
        reports_conversation_rollover=True,
        assigns_conversation_id=False,
        resolves_transcript_by_cwd=True,
        submission="terminal_line",
        root_completion="assistant_stop",
        screen="normal",
        native_hooks=True,
        transcript="semantic",
        pty="telemetry",
        normalized_events=_NORMALIZED_AGENT_EVENTS,
        tool_catalog=_OMP_TOOLS,
        tool_catalog_source="documented_catalog",
        hook_events=_OMP_HOOK_EVENTS,
    ),
}

AGENT_BACKENDS = frozenset(HARNESSES)


def descriptor(name: str) -> HarnessDescriptor:
    return HARNESSES[name]


def agent_harnesses() -> tuple[str, ...]:
    return tuple(HARNESSES)


def harnesses_at_least(level: HarnessLevel | str) -> tuple[str, ...]:
    threshold = HarnessLevel[level] if isinstance(level, str) else level
    return tuple(name for name, harness in HARNESSES.items() if harness.level >= threshold)


def is_agent_harness(name: object) -> bool:
    return isinstance(name, str) and name in HARNESSES


def is_backend(name: object) -> TypeGuard[Backend]:
    return isinstance(name, str) and (name == "shell" or name in HARNESSES)


def require_backend(name: str) -> Backend:
    if is_backend(name):
        return name
    raise ValueError(f"unknown backend: {name}")


def has_observable_transcript(name: object) -> bool:
    return (
        isinstance(name, str)
        and name in HARNESSES
        and HARNESSES[name].level >= HarnessLevel.observed
    )


def delivers_prompts_through_pty(name: object) -> bool:
    return (
        isinstance(name, str)
        and name in HARNESSES
        and HARNESSES[name].submission == "terminal_line"
    )


def reports_lifecycle_hooks(name: object) -> bool:
    return isinstance(name, str) and name in HARNESSES and HARNESSES[name].native_hooks


def repaints_scrollback(name: object) -> bool:
    """Whether this harness's TUI rewrites content already committed to scrollback.

    Doubles as the gate for client-requested repaints: only a harness that keeps its
    transcript on the normal screen and floods the retained ring with live-region
    repaint traffic can leave a fresh attach with no scrollback to show, and only
    such a harness restates its transcript in response to a width pulse.
    """
    return isinstance(name, str) and name in HARNESSES and HARNESSES[name].repaints_scrollback


def external_usage_harnesses() -> tuple[str, ...]:
    return tuple(name for name, harness in HARNESSES.items() if harness.external_usage_command)


def provider_account_harnesses() -> tuple[str, ...]:
    return tuple(
        name for name, harness in HARNESSES.items() if harness.provider_account_management
    )


def public_harness_registry() -> dict[str, object]:
    """Browser-safe registry projection used to gate frontend surfaces."""
    return {
        "version": 1,
        "harnesses": [
            {
                "name": harness.name,
                "display_name": harness.display_name,
                "level": harness.level.name,
                "state_sources": list(harness.state_sources),
                "measurement_source": harness.measurement_source,
                "capabilities": {
                    "observed": harness.level >= HarnessLevel.observed,
                    "transcript": bool(harness.transcript),
                    "measurement": harness.measurement_source != "none",
                    "lifecycle_hooks": harness.native_hooks,
                    "pty_delivery": harness.submission == "terminal_line",
                    "external_usage": harness.external_usage_command,
                    "provider_accounts": harness.provider_account_management,
                    "repaints_scrollback": harness.repaints_scrollback,
                },
            }
            for harness in HARNESSES.values()
        ],
    }
