from __future__ import annotations

import json
import os
import re
from collections.abc import Callable, Sequence
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
TranscriptDialect = Literal["claude", "codex", "pi", "opencode"]
ToolCatalogSource = Literal["documented_catalog", "runtime_dependent"]
Backend = Literal["shell", "claude", "codex", "omp", "pi", "opencode"]
AdapterFamily = Literal["claude", "codex", "omp", "pi", "opencode"]
# How a harness forks a live conversation, for the harnesses that can.
#
# `native_slash_command` drives the CLI's own fork command over the PTY and lets
# it mint the new conversation. `resume_child_thread` spawns a resume of the
# parent, which the CLI opens as a child thread. `None` means mux has no
# implemented strategy and the server refuses the request rather than reaching
# for a generic one: resuming a live conversation whose writer is still attached
# would interleave two writers into one session file.
BranchStrategy = Literal["native_slash_command", "resume_child_thread"]
MemoryInventoryKind = Literal["claude_project_markdown", "codex_feature_flag"]


@dataclass(frozen=True, slots=True)
class ConversationDiscovery:
    """How mux finds conversations this harness wrote that mux did not run.

    Retroactive discovery is what puts a CLI's own past sessions into History, so it
    can be searched and resumed alongside the ones mux launched. It is a capability
    every harness has to answer, and answering it accidentally is the failure this
    declaration exists to stop: the scanner used to hold a hardcoded two-vendor
    tuple, so omp, pi, and opencode were silently undiscoverable with nothing saying
    so, in prose or in a test.

    `subdirectory` is relative to the harness's `data_home`, and `pattern` is the
    glob that matches one conversation inside it. `cwd_scoped` marks a layout that
    stores each conversation under a directory encoding its working directory, which
    lets a project-scoped backfill read a handful of directories instead of every
    conversation on the machine.

    `store` means the conversations are rows, found by querying rather than walking;
    the harness's `conversation_store_file` names the database.

    `None` on the whole field is a *declared* refusal: mux does not discover this
    harness's past conversations, and the reason belongs beside the declaration.
    """

    subdirectory: tuple[str, ...] | None = None
    pattern: str | None = None
    cwd_scoped: bool = False
    store: bool = False

    def __post_init__(self) -> None:
        if self.store:
            if self.subdirectory is not None or self.pattern is not None:
                raise ValueError("a store discovery declares no filesystem layout")
        elif not self.subdirectory or not self.pattern:
            raise ValueError("a filesystem discovery must declare a subdirectory and pattern")


@dataclass(frozen=True, slots=True)
class HeadlessProbes:
    """Non-interactive invocations the live conformance canary drives.

    Each is argv after the executable, with the prompt appended last. The canary runs
    a real authenticated CLI and replays what it wrote through the observer, so a CLI
    that changes its record shape or stops emitting a terminal signal is caught
    against the actual binary rather than against a fixture.

    Declared per harness because every CLI spells these differently, and declared
    *here* so the canary parametrizes from the registry: it used to name two vendors,
    which left three later harnesses with no live tier and nothing reporting the hole.

    `None` on a probe means the harness cannot be asked for that shape. That is a
    capability statement, not a deferral: pi ships no subagent tool, so a subagent
    canary for it would be asserting on behaviour that does not exist.
    """

    # One prompt, no tools at all. The baseline conformance run.
    read_only: tuple[str, ...] | None = None
    # One prompt with a read-only file or shell tool permitted, so the run produces
    # tool_use/tool_result records for the telemetry canary.
    read_tool: tuple[str, ...] | None = None
    # One prompt permitted to spawn a subagent, for the subagent-signal canary.
    subagent: tuple[str, ...] | None = None


@dataclass(frozen=True, slots=True)
class MemoryInventory:
    """Stable learned-memory inventory a harness exposes to Agent Context.

    ``None`` on :class:`HarnessDescriptor` is a declared unsupported capability.
    A concrete kind names a measured resolver, never a guessed private store.
    ``detail`` is the honest unavailable explanation when that resolver cannot
    return files for the current installation.
    """

    kind: MemoryInventoryKind
    detail: str


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
    # The file under `data_home` holding conversations, for a harness that keeps
    # them in a store rather than one file per conversation. `None` for the
    # file-per-conversation harnesses, whose location is an adapter computation from
    # the conversation id and (sometimes) the working directory.
    #
    # Declared because two independent readers need it: the adapter, for exact token
    # and cost figures off the session row, and the transcript reader, for the
    # messages themselves.
    conversation_store_file: str | None
    # How mux finds this harness's past conversations. See :class:`ConversationDiscovery`.
    conversation_discovery: ConversationDiscovery | None
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

    # Argv tokens that immediately precede a conversation id, as the harness's own
    # CLI spells them. `spawn_id_argv` is how mux *dictates* an id at spawn (only a
    # harness with `assigns_conversation_id` has one); `resume_argv` is how the CLI
    # is asked to reopen an existing conversation.
    #
    # Declared rather than pattern-matched because three consumers need the same
    # answer and each used to re-derive it from strings: recovering a pane's
    # conversation id from its recorded argv, refusing a resume whose id is still a
    # placeholder, and rendering the human-pasteable resume command. The string
    # matching recognized Claude and Codex only, so the other three harnesses fell
    # into an `else` that quietly did nothing.
    spawn_id_argv: tuple[str, ...]
    resume_argv: tuple[str, ...]
    # How a live conversation is forked, or `None` when mux implements no strategy.
    branch_strategy: BranchStrategy | None
    # The root instruction file this CLI reads from a project, and the directory
    # holding its user-level counterpart. `None` on both when mux has measured none,
    # which is a declared absence rather than an oversight: Agent Context lists no
    # instruction source for such a harness instead of guessing a filename.
    #
    # The user-level counterpart is its own home-relative path because no existing
    # field predicts it and every harness answers differently: Claude and Codex keep
    # it under `config_dir_name`, pi under its agent directory, oh-my-pi under
    # `~/.agent`, and opencode under `~/.config/opencode`.
    #
    # Home-relative rather than a resolver, because the reader of these files is
    # given the home to look under: tests point it at a temporary directory, which a
    # resolver reading the real environment would silently defeat. An env-relocated
    # data home is therefore not followed here, which is the behaviour Claude and
    # Codex already had.
    instruction_file_name: str | None
    global_instruction_parts: tuple[str, ...] | None
    # Stable learned-memory inventory, or None when none has been measured.
    # Agent Context enumerates this declaration for every harness so an absent
    # capability is visible as ``unsupported`` rather than as an omitted row.
    memory_inventory: MemoryInventory | None
    # What a user types to invoke an authored skill. Published to the browser so the
    # command rail stops re-deriving it: the rail's own copy knew `$` for Codex and
    # `/` for everything else, which types `/name` on oh-my-pi where the CLI wants
    # `/skill:name`.
    skill_invocation_prefix: str
    # npm package prefix and entrypoint suffix, when the CLI ships as an npm package
    # whose `.cmd` shim resolves to a JS entrypoint. Used to recognize a harness from
    # an already-launched command line. `None` when unmeasured; recognition then falls
    # back to the executable name, which every harness declares.
    npm_entrypoint: tuple[str, str] | None
    # The PTY must launch this CLI's JS entrypoint directly instead of its `.cmd`
    # shim, because an argument it needs cannot survive cmd.exe. Codex passes a
    # JSON-valued `-c notify=...` that npm's batch shim mangles; every other
    # npm-shipped harness reads its shim generically, which is cheaper and needs no
    # knowledge of the package layout.
    requires_direct_entrypoint: bool
    # Non-interactive invocations the live conformance canary drives against the real
    # CLI. See :class:`HeadlessProbes`.
    headless_probes: HeadlessProbes

    submission: str
    root_completion: str
    screen: str

    # Whether this CLI publishes its own liveness to a state file mux reads. The
    # single home for that question: a consumer that hardcoded the Claude answer
    # gave every other harness Claude's veto semantics without saying so.
    publishes_cli_state: bool

    # --- Terminal surface traits -------------------------------------------------
    # Each answers a question the browser must ask before it can render a pane, and
    # each was previously a harness name compiled into the frontend. A new harness
    # declares them here and the browser reads them off the registry payload.
    #
    # `webgl_unsafe` is *not* `repaints_scrollback` and must not be merged with it.
    # `repaints_scrollback` excludes a harness from WebGL under the `auto` preference
    # and an explicit `webgl` preference overrides it. `webgl_unsafe` is absolute:
    # Claude's retained alternate screen and OMP's continuous tail repaint both leave
    # a live WebGL context intermittently mangled with no context-loss event to
    # recover from, so neither exposes the override.
    webgl_unsafe: bool
    # The TUI keeps its own scroll viewport, so reaching the newest output has to be
    # asked of the application as well as of xterm. False does not mean "never": a
    # pane that has negotiated mouse tracking is handed scroll gestures dynamically
    # and answers the same way at runtime.
    owns_scroll_viewport: bool
    # The desktop column envelope (`claude_max_columns`) applies to this harness.
    applies_width_envelope: bool
    # A published minimum column count below which desktop panes reduce the font
    # rather than accept a narrower grid. `None` when the vendor publishes none.
    min_desktop_columns: int | None
    # The CLI's startup palette probe accepts OSC 10/11 replies for a bounded
    # interval and treats a late reply as composer input, so such replies are dropped
    # rather than delivered.
    suppresses_late_color_response: bool

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
        if not self.resume_argv:
            raise ValueError(f"harness {self.name} must declare how its CLI resumes")
        if not self.skill_invocation_prefix:
            raise ValueError(f"harness {self.name} must declare a skill invocation prefix")
        if bool(self.spawn_id_argv) != self.assigns_conversation_id:
            # The two say the same thing from opposite ends. A harness that dictates
            # its conversation id has argv that carries it, and a harness with such
            # argv is dictating one; letting them disagree is how a placeholder id
            # gets treated as authoritative.
            raise ValueError(
                f"harness {self.name} must declare spawn id argv exactly when it "
                f"assigns the conversation id"
            )

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


# Where each CLI looks for its user-level context file, relative to the user's home.
# Measured 2026-08-11 against the installed CLIs rather than inferred from their
# config directories, because none of them agrees with the others:
#
# - pi's usage documentation states the load order as `~/.pi/agent/AGENTS.md` first,
#   then a walk up from the working directory.
# - oh-my-pi enumerates user paths as `~/.agent/<segment>` and `~/.agents/<segment>`
#   (`AGENT_DIR_CANDIDATES` in its `discovery/agents.ts`) and loads `AGENTS.md` from
#   each. The first candidate is the one mux reports, because a reader needs one
#   canonical path rather than a search order.
# - opencode documents `~/.config/opencode/AGENTS.md`, alongside its `opencode.json`.
#   It splits config from data, and a context file is config.
_CLAUDE_GLOBAL_INSTRUCTIONS = (".claude", "CLAUDE.md")
_CODEX_GLOBAL_INSTRUCTIONS = (".codex", "AGENTS.md")
_OMP_GLOBAL_INSTRUCTIONS = (".agent", "AGENTS.md")
_PI_GLOBAL_INSTRUCTIONS = (".pi", "agent", "AGENTS.md")
_OPENCODE_GLOBAL_INSTRUCTIONS = (".config", "opencode", "AGENTS.md")


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
        conversation_store_file=None,
        conversation_discovery=ConversationDiscovery(
            subdirectory=("projects",), pattern="*.jsonl", cwd_scoped=True
        ),
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
        spawn_id_argv=("--session-id",),
        resume_argv=("--resume",),
        branch_strategy="native_slash_command",
        instruction_file_name="CLAUDE.md",
        global_instruction_parts=_CLAUDE_GLOBAL_INSTRUCTIONS,
        memory_inventory=MemoryInventory(
            "claude_project_markdown",
            "Learned project memory files used by Claude. Repository worktrees share this source.",
        ),
        skill_invocation_prefix="/",
        npm_entrypoint=("@anthropic-ai/claude-code/", "/cli.js"),
        requires_direct_entrypoint=False,
        headless_probes=HeadlessProbes(
            read_only=("--print", "--safe-mode", "--tools", ""),
            read_tool=("--print", "--permission-mode", "dontAsk", "--allowedTools", "Read"),
            subagent=("--print", "--allowedTools", "Agent", "--permission-mode", "dontAsk"),
        ),
        submission="terminal_line",
        root_completion="stop_or_transcript",
        screen="alternate",
        publishes_cli_state=True,
        # Absolute, not the `repaints_scrollback` exclusion: Claude's retained
        # alternate screen mangles a live WebGL context after a hidden pane returns
        # or a deep session is rebuilt from bounded replay, with no context-loss
        # event and no recovery short of a real resize.
        webgl_unsafe=True,
        owns_scroll_viewport=True,
        applies_width_envelope=True,
        min_desktop_columns=None,
        suppresses_late_color_response=False,
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
        conversation_store_file=None,
        conversation_discovery=ConversationDiscovery(
            subdirectory=("sessions",), pattern="rollout-*.jsonl"
        ),
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
        spawn_id_argv=(),
        resume_argv=("resume",),
        branch_strategy="resume_child_thread",
        instruction_file_name="AGENTS.md",
        global_instruction_parts=_CODEX_GLOBAL_INSTRUCTIONS,
        memory_inventory=MemoryInventory(
            "codex_feature_flag",
            "Codex does not expose a stable project-memory file inventory.",
        ),
        skill_invocation_prefix="$",
        npm_entrypoint=("@openai/codex/", "/codex.js"),
        requires_direct_entrypoint=True,
        # One invocation covers all three: `exec` is already non-interactive and the
        # read-only sandbox still permits reads and subagent threads.
        headless_probes=HeadlessProbes(
            read_only=("exec", "--sandbox", "read-only", "--skip-git-repo-check", "--json"),
            read_tool=("exec", "--sandbox", "read-only", "--skip-git-repo-check", "--json"),
            subagent=("exec", "--sandbox", "read-only", "--skip-git-repo-check", "--json"),
        ),
        submission="terminal_line",
        root_completion="task_complete",
        screen="normal",
        publishes_cli_state=False,
        webgl_unsafe=False,
        owns_scroll_viewport=False,
        applies_width_envelope=False,
        # Codex publishes an 80-column floor in its own terminal diagnostics; below
        # it the composer wraps through visually blank rows.
        min_desktop_columns=80,
        suppresses_late_color_response=True,
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
        conversation_store_file=None,
        # `<data_home>/sessions/<cwd-bucket>/*.jsonl`. Not `cwd_scoped`: the bucket
        # name is a slug rather than a prefix-preserving encoding, so a project scan
        # cannot select directories by prefix and reads them all.
        conversation_discovery=ConversationDiscovery(
            subdirectory=("sessions",), pattern="*.jsonl"
        ),
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
        spawn_id_argv=(),
        resume_argv=("--resume",),
        # No strategy: `--resume` reopens the same session file, so forking a live
        # OMP conversation would put two writers on one file.
        branch_strategy=None,
        instruction_file_name="AGENTS.md",
        global_instruction_parts=_OMP_GLOBAL_INSTRUCTIONS,
        memory_inventory=None,
        skill_invocation_prefix="/skill:",
        npm_entrypoint=None,
        requires_direct_entrypoint=False,
        # `-p` is print mode; `--no-tools` is the read-only guarantee. Its `task`
        # tool is what makes a subagent probe meaningful, so the default tool set is
        # left in place for that one.
        headless_probes=HeadlessProbes(
            read_only=("-p", "--no-tools"),
            read_tool=("-p",),
            subagent=("-p",),
        ),
        submission="terminal_line",
        root_completion="assistant_stop",
        screen="normal",
        publishes_cli_state=False,
        # Its tail repaints continuously, so a mangled WebGL context is
        # indistinguishable from incomplete replay and the override is not offered.
        webgl_unsafe=True,
        owns_scroll_viewport=False,
        applies_width_envelope=False,
        min_desktop_columns=None,
        suppresses_late_color_response=False,
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
        conversation_store_file=None,
        # Same layout as oh-my-pi, which is unsurprising for a fork: sessions live
        # under `<data_home>/sessions/<cwd-bucket>/*.jsonl`.
        conversation_discovery=ConversationDiscovery(
            subdirectory=("sessions",), pattern="*.jsonl"
        ),
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
        # `pty` is a working-only reading, pinned to a captured pi 0.84.1 screen
        # (`working.pi_spinner`). It can veto delivery and detect activity; it
        # never grants idle, which hooks and the transcript already prove.
        state_sources=("hook", "transcript", "pty"),
        measurement_source="transcript",
        reports_conversation_rollover=True,
        assigns_conversation_id=False,
        resolves_transcript_by_cwd=True,
        spawn_id_argv=(),
        # `--session` and not `--resume`: pi's `--resume` is an interactive picker
        # that sits waiting for a keystroke, while `--session <id-or-path>` reopens
        # the named conversation directly.
        resume_argv=("--session",),
        # `--session` appends to the same file wherever pi stands, so a fork would
        # put two writers on one conversation.
        branch_strategy=None,
        instruction_file_name="AGENTS.md",
        global_instruction_parts=_PI_GLOBAL_INSTRUCTIONS,
        memory_inventory=None,
        skill_invocation_prefix="/",
        npm_entrypoint=None,
        requires_direct_entrypoint=False,
        # No subagent probe: pi's documented tool set is read/write/edit/bash/glob/
        # grep/list with no task tool, so there is no subagent for a canary to
        # observe. Declaring one would demand evidence the CLI cannot produce.
        headless_probes=HeadlessProbes(
            read_only=("-p", "--no-tools"),
            read_tool=("-p",),
            subagent=None,
        ),
        submission="terminal_line",
        root_completion="assistant_stop",
        screen="normal",
        publishes_cli_state=False,
        webgl_unsafe=False,
        owns_scroll_viewport=False,
        applies_width_envelope=False,
        min_desktop_columns=None,
        suppresses_late_color_response=False,
        native_hooks=True,
        transcript="semantic",
        pty="telemetry",
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
        # A dialect of its own, read from `opencode.db` rather than from a file.
        # `transcript_dialect` names the record *shape* and the reader that parses
        # it, which is a separate question from whether those records arrive as
        # lines in a file: `opencode_store` projects its `message` and `part` rows
        # into records, and `transcript_view` reads them like any other dialect.
        transcript_dialect="opencode",
        native_id_pattern=r"ses_[A-Za-z0-9]{8,64}",
        config_dir_name=".opencode",
        script_base_name="opencode",
        rollout_file_prefix=None,
        conversation_store_file="opencode.db",
        # Rows, not files: discovery queries `session` instead of walking a tree.
        conversation_discovery=ConversationDiscovery(store=True),
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
        # No `pty`, and this is a measured conclusion rather than a deferral.
        # opencode's screens were captured (2026-08-10): it paints
        # absolutely-positioned frames on the alternate screen, so the byte
        # stream carries no line structure for a tail rule to read. Stripping
        # its control sequences yields interleaved fragments — "hree",
        # "evenEight" — where cursor moves overwrote earlier text. Any regex
        # matching that would be matching one frame's paint order by accident,
        # which is the marker-drift problem the rule table exists to avoid.
        # Reading opencode's screen would need a real emulator applying the
        # sequences to a buffer, which `_normalize_tail_text` is not.
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
        spawn_id_argv=(),
        resume_argv=("--session",),
        # `--session` reopens the same row, so a fork would put two writers on one
        # conversation.
        branch_strategy=None,
        instruction_file_name="AGENTS.md",
        global_instruction_parts=_OPENCODE_GLOBAL_INSTRUCTIONS,
        memory_inventory=None,
        skill_invocation_prefix="/",
        npm_entrypoint=None,
        requires_direct_entrypoint=False,
        # `opencode run` is the non-interactive form. Its read-only guarantee is
        # configuration (`permission` in opencode.json) rather than a flag, so a
        # probe that needs one writes it; the argv is the same either way. Excluded
        # from the transcript canary regardless, because it writes no transcript.
        headless_probes=HeadlessProbes(
            read_only=("run",),
            read_tool=("run",),
            subagent=("run",),
        ),
        submission="terminal_line",
        root_completion="session_idle",
        screen="alternate",
        publishes_cli_state=False,
        webgl_unsafe=False,
        owns_scroll_viewport=False,
        applies_width_envelope=False,
        min_desktop_columns=None,
        suppresses_late_color_response=False,
        native_hooks=True,
        # Semantic records, read from the store rather than tailed from a file.
        # This is the automation-evidence label, not a claim that a byte-offset
        # tailer follows it: `transcript` is absent from `state_sources` above
        # because nothing here moves lifecycle state, while the records themselves
        # are readable on demand for the Transcript tab and history search.
        transcript="semantic",
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
    """Every registered harness, in declaration order.

    Declaration order is the product's own ordering, so the first entry is the
    reasonable default for a caller that must pick an agent and was given nothing to
    go on. Callers should prefer a configured default and fall back here.
    """
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


def publishes_cli_state(name: object) -> bool:
    """Whether this CLI writes its own liveness to a state file mux reads.

    The single home for the question. Both the layer that reads those files and the
    PTY classifier that lets a CLI-state reading veto or override a screen reading
    must agree, and the classifier used to answer it with a hardcoded ``"claude"``.
    """
    return isinstance(name, str) and name in HARNESSES and HARNESSES[name].publishes_cli_state


def assigns_conversation_id(name: object) -> bool:
    """Whether mux dictates this harness's conversation id at spawn.

    True makes ``record.native_session_id`` authoritative from the first moment, so
    only an exact id match may bind a transcript and a mismatched file is the wrong
    file rather than a newer identity. False means the CLI mints its own and the
    record carries a placeholder until discovery. Fails closed for a shell or an
    unknown name, because treating a placeholder as authoritative leaves a session
    permanently unobserved.
    """
    return isinstance(name, str) and name in HARNESSES and HARNESSES[name].assigns_conversation_id


def conversation_id_argv(name: object) -> tuple[str, ...]:
    """Argv tokens after which this harness's argv carries a conversation id.

    Spawn and resume tokens together, because the caller recovering an id from a
    recorded command line does not know which kind of launch produced it.
    """
    if not isinstance(name, str) or name not in HARNESSES:
        return ()
    harness = HARNESSES[name]
    return (*harness.spawn_id_argv, *harness.resume_argv)


def native_id_from_args(name: object, args: Sequence[str]) -> str | None:
    """The conversation id a recorded command line names, if it names one.

    Returns ``None`` when the argv carries no id, which is the ordinary case for a
    harness that mints its own: the id is discovered later rather than dictated.
    """
    tokens = conversation_id_argv(name)
    if not tokens:
        return None
    for token in tokens:
        if token not in args:
            continue
        index = list(args).index(token) + 1
        if index < len(args):
            return str(args[index])
    return None


def resume_command(name: object, native_id: str) -> str | None:
    """The plain vendor CLI command that reopens this conversation elsewhere.

    Deliberately the clean command rather than mux's instrumented argv: it exists to
    be pasted into an ordinary terminal, so it carries no hook settings, no notify
    override, and no per-session extension. ``None`` for a non-harness.
    """
    if not isinstance(name, str) or name not in HARNESSES or not native_id:
        return None
    harness = HARNESSES[name]
    return " ".join((harness.script_base_name, *harness.resume_argv, native_id))


def suppresses_late_color_response(name: object) -> bool:
    """Whether a late OSC 10/11 reply must be dropped rather than delivered.

    True for a CLI whose startup palette probe accepts such replies for a bounded
    interval and treats a later one as composer input. Dropping an optional response
    is safer than injecting one the CLI will type.
    """
    return (
        isinstance(name, str)
        and name in HARNESSES
        and HARNESSES[name].suppresses_late_color_response
    )


def branch_strategy(name: object) -> BranchStrategy | None:
    """How a live conversation is forked for this harness, or ``None`` for no strategy."""
    if not isinstance(name, str) or name not in HARNESSES:
        return None
    return HARNESSES[name].branch_strategy


def branchable_harnesses() -> tuple[str, ...]:
    return tuple(name for name, harness in HARNESSES.items() if harness.branch_strategy)


def live_canary_harnesses() -> tuple[str, ...]:
    """Harnesses the live conformance canary can drive against a real CLI.

    Two declarations have to hold. The harness must declare a headless probe, so
    there is a non-interactive read-only invocation to run, and it must declare a
    transcript dialect, because the canary's assertion is that the observer parses
    what the real binary just wrote. A harness that keeps its conversation in its own
    database (opencode) is excluded here by derivation rather than by a skip: there
    is no transcript for this canary to replay, and pretending otherwise would make
    the tier report a pass it never ran.
    """
    return tuple(
        name
        for name, harness in HARNESSES.items()
        if harness.headless_probes.read_only is not None
        and harness.transcript_dialect is not None
    )


def live_subagent_harnesses() -> tuple[str, ...]:
    """Harnesses whose live canary can be asked to spawn a subagent.

    A harness with no subagent tool declares no subagent probe, and is excluded here
    rather than skipped inside the test: skipping reports a pass for a run that never
    happened, while a derived exclusion states that the capability is absent.
    """
    return tuple(
        name
        for name in live_canary_harnesses()
        if HARNESSES[name].headless_probes.subagent is not None
    )


def live_telemetry_harnesses() -> tuple[str, ...]:
    """Harnesses whose live canary can drive a read-only tool and produce telemetry."""
    return tuple(
        name
        for name in live_canary_harnesses()
        if HARNESSES[name].headless_probes.read_tool is not None
    )


def conversation_store_path(name: object) -> Path | None:
    """The store file holding this harness's conversations, if it keeps one.

    ``None`` for a harness with one file per conversation, whose location an adapter
    computes from the conversation id. Registry-derived so the two independent
    readers of that store - the adapter for token and cost figures, and the
    transcript reader for the messages - resolve the same file.
    """
    if not isinstance(name, str) or name not in HARNESSES:
        return None
    harness = HARNESSES[name]
    if harness.conversation_store_file is None:
        return None
    return harness.data_home() / harness.conversation_store_file


def instruction_harnesses() -> tuple[str, ...]:
    """Harnesses that read a measured root instruction file."""
    return tuple(name for name, harness in HARNESSES.items() if harness.instruction_file_name)


def harness_for_command(executable: str, arguments: Sequence[str]) -> str | None:
    """Which harness an already-launched command line is running, if any.

    Answers from the registry rather than from a hardcoded pair of vendors. The
    executable name is the primary signal and every harness declares one; the npm
    entrypoint is the fallback for a CLI launched through its JS entry rather than
    its shim, and only the harnesses that ship that way declare one.

    ``None`` for a shell or anything unrecognized, which callers must treat as "no
    claim" rather than as a default harness.
    """
    name = Path(executable or "").name.casefold()
    entrypoint = str(arguments[0]).replace("\\", "/").casefold() if arguments else ""
    for harness in HARNESSES.values():
        base = harness.script_base_name.casefold()
        if name in {base, f"{base}.exe", f"{base}.cmd", f"{base}.ps1"}:
            return harness.name
        if harness.npm_entrypoint is not None:
            package, suffix = harness.npm_entrypoint
            if package.casefold() in entrypoint and entrypoint.endswith(suffix.casefold()):
                return harness.name
    return None


def replay_needs_repaint(name: object) -> bool:
    """Whether a *bounded* replay window can leave this harness's screen incomplete.

    An alternate-screen TUI's retained bytes are a differential frame stream, not a
    transcript. Measured 2026-08-11 across eight live Claude panes: steady-state output
    addresses only the five rows that actually change (spinner, context meter, status),
    while the input box border and the prompt are drawn once and then left alone for as
    long as they look the same. `Session.replay_bytes` restates `?1049h` for a window
    carrying no toggle of its own, and that sequence *clears* the alternate buffer — so a
    bounded window starts the client from a blank screen and can only fill the cells it
    happens to contain.

    Whether that reconstructs to a whole screen is therefore luck: it holds only when a
    full repaint landed inside the window. The same sweep, run twice, found one pane of
    eight reconstructing with no border and no prompt — the user-visible "statusline is
    missing until I resize the window" — and it was a different pane each run.

    Normal-screen harnesses are excluded for the reason given in `needs_resize_repaint`:
    their transcript is real scrollback lines, so a bounded window is a shorter history
    rather than a partial frame. Their opposite problem — a ring wrapped until the window
    holds no transcript at all — is `repaints_scrollback`.
    """
    return isinstance(name, str) and name in HARNESSES and HARNESSES[name].screen == "alternate"


def external_usage_harnesses() -> tuple[str, ...]:
    return tuple(name for name, harness in HARNESSES.items() if harness.external_usage_command)


def provider_account_harnesses() -> tuple[str, ...]:
    return tuple(
        name for name, harness in HARNESSES.items() if harness.provider_account_management
    )


FRONTEND_REGISTRY_SEED_PATH = ("frontend", "src", "harnessRegistrySeed.ts")


def frontend_registry_seed_source() -> str:
    """The generated TypeScript seed the browser renders with before its first snapshot.

    Generated rather than hand-written because a hand-written copy is a second
    registry: the previous one had opencode two capability levels below its
    descriptor and pi missing a state source, and nothing compared them. The daemon
    replaces this seed atomically on the first application refresh, so its only job
    is to keep that first render coherent - but "coherent" means agreeing with the
    daemon, which only generation guarantees.

    Emitted as TypeScript rather than JSON so it type-checks against the payload
    interface and needs no import attributes in any of the three runtimes that read
    it (Vite, `tsc`, and `node --experimental-strip-types`).
    """
    payload = json.dumps(public_harness_registry(), indent=2, sort_keys=False)
    return (
        "// GENERATED FILE - do not edit by hand.\n"
        "// Regenerate: uv run python packaging/generate_frontend_registry.py\n"
        "// Source of truth: src/swe_mux/harness.py (public_harness_registry).\n"
        "// tests/test_harness_registry.py fails when this file drifts from it.\n"
        "import type { HarnessRegistryPayload } from './harnessRegistry.ts'\n"
        "\n"
        f"export const HARNESS_REGISTRY_SEED: HarnessRegistryPayload = {payload}\n"
    )


def public_harness_registry() -> dict[str, object]:
    """Browser-safe registry projection used to gate frontend surfaces.

    Every per-harness fact the browser needs travels here. A trait absent from this
    payload is one the frontend has to hardcode, which is how a harness name ends up
    compiled into a terminal module and drifts from the descriptor that owns it.

    `version` stays at 1 while fields are only added: consumers read the keys they
    know and default the rest, so a browser older than the daemon keeps working.
    Removing or re-typing a field is what would require a bump.
    """
    return {
        "version": 1,
        "harnesses": [
            {
                "name": harness.name,
                "display_name": harness.display_name,
                "level": harness.level.name,
                "state_sources": list(harness.state_sources),
                "measurement_source": harness.measurement_source,
                # The clean vendor CLI name and the tokens preceding a conversation
                # id, so the browser composes a resume command instead of knowing one.
                "cli_name": harness.script_base_name,
                "resume_argv": list(harness.resume_argv),
                "skill_invocation_prefix": harness.skill_invocation_prefix,
                "capabilities": {
                    "observed": harness.level >= HarnessLevel.observed,
                    "transcript": bool(harness.transcript),
                    "measurement": harness.measurement_source != "none",
                    "lifecycle_hooks": harness.native_hooks,
                    "pty_delivery": harness.submission == "terminal_line",
                    "external_usage": harness.external_usage_command,
                    "provider_accounts": harness.provider_account_management,
                    "repaints_scrollback": harness.repaints_scrollback,
                    "assigns_conversation_id": harness.assigns_conversation_id,
                    # The CLI resolves a conversation's directory from its working
                    # directory, so a copied resume command only works from the
                    # session's cwd and the browser has to say so.
                    "resolves_transcript_by_cwd": harness.resolves_transcript_by_cwd,
                    "branch": harness.branch_strategy is not None,
                    "webgl_unsafe": harness.webgl_unsafe,
                    "owns_scroll_viewport": harness.owns_scroll_viewport,
                    "width_envelope": harness.applies_width_envelope,
                    "min_desktop_columns": harness.min_desktop_columns,
                    "suppresses_late_color_response": harness.suppresses_late_color_response,
                },
            }
            for harness in HARNESSES.values()
        ],
    }
