from __future__ import annotations

import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path
from typing import Literal, TypeGuard

StateSource = Literal["hook", "transcript", "pty", "cli_state"]
# Where tokens, cost, and model come from. `transcript` parses the harness's own
# conversation file. `database` reads a harness-owned store instead: opencode
# maintains running totals on its `session` row, so measurement is one indexed
# read rather than a parse, with no byte offsets and no timestamp-derived
# liveness. Both are exact and native; the difference is the reader, not the
# confidence.
MeasurementSource = Literal["transcript", "database", "none"]
# The *shape* of a harness's conversation records, as distinct from the harness
# itself. Two harnesses share a dialect when one reader can parse both: pi and
# oh-my-pi forked from a common ancestor and still write the same
# `{"type":"session"}` header, `id`/`parentId` entry tree, and `usage`/`cost`
# block, so both declare `pi`.
#
# Every consumer that parses records dispatches on this rather than on the
# harness name. That is deliberate: a name-keyed `if/elif` chain has no
# exhaustiveness guard unless someone remembers to add `assert_never`, and the
# one place that forgot (`transcript_view`) silently rendered an empty
# Transcript tab for a newly added harness rather than failing. Dispatching on a
# closed dialect makes the compiler ask the question instead.
#
# `None` means the harness writes no parseable conversation records at all
# (opencode keeps its conversations as rows in `opencode.db`).
TranscriptDialect = Literal["claude", "codex", "pi"]
ToolCatalogSource = Literal["documented_catalog", "runtime_dependent"]
Backend = Literal["shell", "claude", "codex", "omp", "pi", "opencode"]
AdapterFamily = Literal["claude", "codex", "omp", "pi", "opencode"]

DataHomeResolver = Callable[[], Path]
ToolCatalog = tuple[tuple[str, str], ...]

# Claude, Codex, omp, and pi all mint UUID conversation ids.
_UUID_NATIVE_ID = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"


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
    # Which record reader parses this harness's conversation. `None` for a
    # harness that writes no parseable transcript.
    transcript_dialect: TranscriptDialect | None
    # The shape a conversation id takes for this harness, as an anchored regex.
    #
    # A sanity filter on hook input, not identity logic: the ingress is
    # authenticated, but the id it carries still ends up in file paths and
    # database keys, so an unbounded value is not accepted. Declared per harness
    # because the shape is a genuine deviation — four harnesses mint UUIDs and
    # opencode mints `ses_<base62>`. It was previously a single hardcoded UUID
    # pattern, which silently refused every opencode id and left those sessions
    # permanently unbound with no error anywhere.
    native_id_pattern: str
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
        has_measurements = self.measurement_source != "none"
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


def _pi_data_home() -> Path:
    """pi's agent directory.

    ``PI_CODING_AGENT_DIR`` is deliberately read here even though
    :func:`_omp_data_home` reads it too: oh-my-pi is a fork of pi and the two
    genuinely share the variable (measured 2026-08-10 — it is the only ``PI_*``
    directory variable pi 0.74.2 reads, and omp reads it as well). Mirroring the
    CLI's own resolution is the whole job of a data-home resolver, so when the
    user exports one value both harnesses resolve to it exactly as both CLIs
    would. Binding survives the overlap because a pi conversation is bound from
    its extension's reported session file and a live transcript may only be
    claimed by one session.

    ``PI_CONFIG_DIR`` is *not* read: it is an omp addition and pi ignores it.
    """
    configured = os.environ.get("PI_CODING_AGENT_DIR")
    return Path(configured).expanduser() if configured else Path.home() / ".pi" / "agent"


def _opencode_data_home() -> Path:
    """opencode's data directory, holding ``opencode.db``.

    opencode uses the XDG-style ``~/.local/share/opencode`` on every platform,
    including Windows (measured: the installed 1.18.16 created
    ``C:\\Users\\<user>\\.local\\share\\opencode``), so this does not branch on
    ``APPDATA``. ``OPENCODE_DATA_DIR`` may name a comma-separated list; the first
    entry is the write target and the only one mux follows.
    """
    configured = os.environ.get("OPENCODE_DATA_DIR")
    if configured:
        first = configured.split(",")[0].strip()
        if first:
            return Path(first).expanduser()
    return Path.home() / ".local" / "share" / "opencode"


_NORMALIZED_AGENT_EVENTS = (
    "turn_started",
    "turn_ended",
    "tool_use",
    "tool_result",
    "approval_needed",
)

# pi has no native approval flow at all: the CLI runs a tool as soon as the model
# asks for it, and gating is something a *user* extension implements on top of
# `tool_call` (its own examples ship `permission-gate.ts`, which pairs `tool_call`
# with `ui.confirm`). Declaring `approval_needed` here would promise an evidence
# kind pi cannot produce, so the corpus would demand a fixture that cannot exist.
_PI_NORMALIZED_EVENTS = (
    "turn_started",
    "turn_ended",
    "tool_use",
    "tool_result",
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

_PI_TOOLS: ToolCatalog = (
    ("read", "Read a file"),
    ("write", "Create or overwrite a file"),
    ("edit", "Apply an exact string replacement"),
    ("bash", "Run a workspace shell command"),
    ("glob", "Find paths using glob patterns"),
    ("grep", "Search file contents with regular expressions"),
    ("list", "List a directory"),
)

_OPENCODE_TOOLS: ToolCatalog = (
    ("read", "Read a file"),
    ("write", "Create or overwrite a file"),
    ("edit", "Apply an exact string replacement"),
    ("patch", "Apply a structured patch"),
    ("bash", "Run a workspace shell command"),
    ("glob", "Find paths using glob patterns"),
    ("grep", "Search file contents with regular expressions"),
    ("list", "List a directory"),
    ("task", "Run a subagent"),
    ("todowrite", "Maintain the session task list"),
    ("webfetch", "Fetch a web page"),
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


_PI_HOOK_EVENTS = (
    "SessionStart",
    "UserPromptSubmit",
    "turn_started",
    "turn_ended",
    "task_started",
    "task_complete",
    "PreToolUse",
    "PostToolUse",
    "PostToolUseFailure",
    "context_compacted",
    "SessionEnd",
)

# opencode's plugin surface is one universal `event` subscription plus a small
# number of named hooks. The set below is what the mux plugin binds and
# normalizes; opencode's full bus carries LSP, installation, TUI, and PTY events
# that say nothing about agent lifecycle.
_OPENCODE_HOOK_EVENTS = (
    "SessionStart",
    "UserPromptSubmit",
    "turn_started",
    "turn_ended",
    "PreToolUse",
    "PostToolUse",
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
        transcript_dialect="claude",
        native_id_pattern=_UUID_NATIVE_ID,
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
        transcript_dialect="codex",
        native_id_pattern=_UUID_NATIVE_ID,
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
        transcript_dialect="pi",
        native_id_pattern=_UUID_NATIVE_ID,
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
    "pi": HarnessDescriptor(
        name="pi",
        display_name="pi",
        executable="pi",
        default_args=(),
        data_home=_pi_data_home,
        adapter_family="pi",
        transcript_dialect="pi",
        native_id_pattern=_UUID_NATIVE_ID,
        config_dir_name=".pi",
        script_base_name="pi",
        rollout_file_prefix=None,
        # The mux extension reports `ctx.sessionManager.getSessionFile()` on
        # `session_start`. That is pi's only strong pane-to-conversation link:
        # unlike oh-my-pi it writes no `terminal-sessions/<terminal-id>`
        # breadcrumb (measured 2026-08-10 — its data home holds only auth.json,
        # extensions/, and sessions/, and upstream pi-tui ships no ttyid.ts).
        reports_transcript_path=True,
        external_usage_command=False,
        provider_account_management=False,
        # In-process extension, ordered against its own transcript writes, as omp.
        hook_ordering_guarantee=True,
        repaints_scrollback=True,
        # No `pty`: the PTY rule table is backend-scoped and each harness's
        # markers have to be pinned to captured screens from the installed
        # build. pi's have not been captured, and a screen rule guessed from
        # documentation is the marker-drift problem restated. Hooks and the
        # transcript already reach `managed`; adding `pty` is a measurement
        # exercise, not a code one.
        state_sources=("hook", "transcript"),
        measurement_source="transcript",
        reports_conversation_rollover=True,
        assigns_conversation_id=False,
        resolves_transcript_by_cwd=True,
        submission="terminal_line",
        root_completion="assistant_stop",
        screen="normal",
        native_hooks=True,
        transcript="semantic",
        pty=None,
        normalized_events=_PI_NORMALIZED_EVENTS,
        tool_catalog=_PI_TOOLS,
        tool_catalog_source="documented_catalog",
        hook_events=_PI_HOOK_EVENTS,
    ),
    "opencode": HarnessDescriptor(
        name="opencode",
        display_name="opencode",
        executable="opencode",
        default_args=(),
        data_home=_opencode_data_home,
        adapter_family="opencode",
        transcript_dialect=None,
        native_id_pattern=r"ses_[A-Za-z0-9]{8,64}",
        config_dir_name=".opencode",
        script_base_name="opencode",
        rollout_file_prefix=None,
        # There is no transcript file to report; conversations live as rows in
        # `opencode.db`, addressed by session id.
        reports_transcript_path=False,
        external_usage_command=False,
        provider_account_management=False,
        # The plugin posts over loopback HTTP while the server writes the store
        # on its own schedule; nothing measured guarantees the POST lands after
        # the row it describes. Declared false so the transcript-liveness rule
        # keeps arbitrating rather than trusting hook order.
        hook_ordering_guarantee=False,
        repaints_scrollback=False,
        # opencode keeps conversations as rows in `opencode.db`, not as an
        # append-only file, so the byte-offset transcript tailer has nothing to
        # attach to and `transcript` is absent from the state sources. Its
        # `session` row carries running token and cost totals, which the adapter
        # reads directly on turn boundaries — exact, native, and not a parse.
        # No `pty` for the same reason as pi: opencode's screens have not been
        # captured, and its TUI holds the alternate screen, where mux's tail
        # rules read a repainted frame rather than a transcript.
        state_sources=("hook",),
        measurement_source="database",
        # `/new` mints a fresh `ses_…` in the same pane and the plugin reports it
        # as `session.created`, which is a CLI-reported rollover exactly as
        # Claude's SessionStart is. There is also no filesystem transcript-switch
        # heuristic here for the flag to suppress.
        reports_conversation_rollover=True,
        assigns_conversation_id=False,
        # A session row is keyed by id and carries its own `directory`; the
        # conversation never moves when the CLI's working directory changes.
        resolves_transcript_by_cwd=False,
        submission="terminal_line",
        root_completion="session_idle",
        screen="alternate",
        native_hooks=True,
        transcript=None,
        pty=None,
        normalized_events=_NORMALIZED_AGENT_EVENTS,
        tool_catalog=_OPENCODE_TOOLS,
        tool_catalog_source="runtime_dependent",
        hook_events=_OPENCODE_HOOK_EVENTS,
    ),
}

AGENT_BACKENDS = frozenset(HARNESSES)


def descriptor(name: str) -> HarnessDescriptor:
    return HARNESSES[name]


def native_id_matches(name: object, native_id: str) -> bool:
    """Whether ``native_id`` has the shape this harness's conversation ids take.

    Anchored: a substring match would accept an id with arbitrary text around it,
    and the value reaches file paths and database keys.
    """
    if not isinstance(name, str) or name not in HARNESSES:
        return False
    return re.fullmatch(HARNESSES[name].native_id_pattern, native_id) is not None


def transcript_dialect(name: object) -> TranscriptDialect | None:
    """Which record reader parses this backend's conversation, if any.

    The single question every transcript-parsing consumer asks. `shell` and any
    harness without parseable records answer ``None``, which callers must handle
    as "there is nothing to read" rather than "read it as the default".
    """
    if not isinstance(name, str) or name not in HARNESSES:
        return None
    return HARNESSES[name].transcript_dialect


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


def needs_resize_repaint(name: object) -> bool:
    """Whether a settled width change leaves this harness's screen needing a pulse.

    An alternate-screen TUI cannot recover from a width change on its own, and the
    reason is a three-way disagreement no single participant can see:

    - xterm never reflows the alternate buffer. Reflow is gated on the buffer having
      scrollback, which the alternate buffer does not, so a width change only pads or
      truncates each line in place and leaves the old wrapping behind.
    - ConPTY *does* rewrap its console buffer, then emits a diff against its own
      previous frame. Lines whose content it considers unchanged are never sent.
    - The child repaints on SIGWINCH, but into a ConPTY buffer that already holds the
      rewrapped text, so most of that repaint also diffs away to nothing.

    Everyone is individually correct and the browser is left holding cells from the
    old wrapping with nothing on the way to overwrite them. Growing is the visible
    direction because rewrapping wider changes little in ConPTY's own buffer, so it
    emits least exactly when the browser needs most; shrinking pushes text down, which
    changes enough lines that the emitted diff happens to repair the screen. Hence the
    hand workaround this replaces: drag wide, then nudge narrower to force a real one.

    Normal-screen harnesses (Codex, OMP) are excluded because they keep appending and
    repainting their live region, so any gap is overwritten within a frame. Their
    scrollback problem is the opposite one and is served by `repaints_scrollback`.
    """
    return isinstance(name, str) and name in HARNESSES and HARNESSES[name].screen == "alternate"


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
