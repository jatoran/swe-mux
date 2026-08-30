from __future__ import annotations

import json
import os
import re
import shutil
import sys
import tomllib
from collections.abc import Callable, Collection
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any

from .budget import Budget, coerce_budget
from .budget import validate as validate_budget
from .harness import (
    HARNESSES,
    host_executable,
    is_agent_harness,
    reserved_launch_arg_conflict,
)
from .host_platform import IS_MACOS, IS_WINDOWS, platform_key
from .keybindings import is_command
from .llm_endpoint import LLM_PROVIDERS, base_url_error
from .llm_endpoint import catalog_url_error as llm_catalog_url_error
from .llm_endpoint import model_error as llm_model_error


def default_data_dir() -> Path:
    """Where this host keeps swe-mux's data, honouring `MUX_DATA_DIR` first.

    Windows keeps `~/.mux`, because moving the proving platform's data directory
    would strand every existing install for no benefit. POSIX follows platform
    convention instead of carrying `~/.mux` across by accident: XDG on Linux
    (`$XDG_DATA_HOME`, else `~/.local/share/swe-mux`) and Application Support on
    macOS.

    **An existing `~/.mux` always wins**, on every host. That is the forward-safe
    rule: anyone who already has data - including a Linux user of an earlier build,
    or someone who copied a directory across - keeps using it rather than silently
    starting from an empty one beside it. A convention is only applied to a fresh
    install, where there is nothing to lose.
    """
    override = os.environ.get("MUX_DATA_DIR", "").strip()
    if override:
        return Path(override).expanduser()
    legacy = Path.home() / ".mux"
    if IS_WINDOWS or legacy.exists():
        return legacy
    if IS_MACOS:
        return Path.home() / "Library" / "Application Support" / "swe-mux"
    data_home = os.environ.get("XDG_DATA_HOME", "").strip()
    root = Path(data_home).expanduser() if data_home else Path.home() / ".local" / "share"
    return root / "swe-mux"


def default_shell_executable() -> str:
    """The interactive shell a fresh install starts with on this host.

    Windows keeps its shipped answer exactly - `powershell.exe`, upgraded to
    `pwsh.exe` by the first-run logic when PowerShell 7 is present - because the
    proving platform's default must not move underneath existing installs.

    POSIX asks the user's own environment first. `$SHELL` is what the account is
    configured to use and what any other terminal would honour; guessing `bash`
    over it would silently override a deliberate choice of zsh or fish. `/bin/sh`
    is the last resort because it always exists, which is exactly why it must not
    be allowed to win over a real login shell.
    """
    if IS_WINDOWS:
        return "powershell.exe"
    declared = os.environ.get("SHELL", "").strip()
    if declared and Path(declared).is_file():
        return declared
    for candidate in ("bash", "zsh", "fish", "sh"):
        if found := shutil.which(candidate):
            return found
    return "/bin/sh"


#: A drive-letter path, which only Windows has.
_WINDOWS_DRIVE_PATH = re.compile(r"^[A-Za-z]:[\\/]")
#: The three PATHEXT shapes an agent CLI actually ships as on Windows. Kept
#: deliberately short: every entry has to be a program image Windows starts and
#: POSIX cannot, so `.ps1` (a script `pwsh` runs on either host) is not one.
_WINDOWS_EXECUTABLE_SUFFIXES = (".exe", ".cmd", ".bat")


def is_foreign_host_path(value: str) -> bool:
    """Whether this string is *shaped* for a host other than the one running.

    The rule is shape, and never resolution. Both halves of that are load-bearing.

    **Shape, so a CLI that is merely not installed today is left alone.**
    `shutil.which` cannot tell the two apart: an operator who has not installed
    Codex yet and one whose `config.toml` came off a Windows box look identical to
    it. Rewriting on a failed lookup would silently discard a deliberate override
    the moment its target was uninstalled, so nothing here asks the filesystem
    anything.

    **Not resolution, because resolution actively lies on the host this matters
    most on.** Under WSL the Windows installs are on PATH through interop, so
    `which("claude.exe")` *succeeds* and resolves to `/mnt/c/.../claude.exe` - the
    exact outcome `harness.host_executable` exists to prevent, measured
    2026-08-17. A value that resolves is therefore not evidence that it belongs
    here.

    So a value is foreign only when the string could not have been meant for this
    host: on POSIX a Windows separator, a drive-letter path, or a Windows program
    suffix; on Windows a POSIX absolute path, with `//host/share` excepted because
    that is a UNC path Windows does accept. Everything else survives, which is
    what keeps a deliberate override alive - `claude.cmd` on Windows, an absolute
    `/usr/local/bin/claude` on Linux, and a bare `claude` on either, which is what
    most overrides look like.

    A POSIX directory name may legally contain a backslash, and one written that
    way is misread here. That is accepted: the value it costs is one exotic
    directory name, and the value it buys is every Windows path in the file, which
    is the shape a copied config actually carries.
    """
    text = value.strip()
    if not text:
        return False
    if IS_WINDOWS:
        return text.startswith("/") and not text.startswith("//")
    if "\\" in text or _WINDOWS_DRIVE_PATH.match(text):
        return True
    return text.casefold().endswith(_WINDOWS_EXECUTABLE_SUFFIXES)


SCHEMA_VERSION = 35
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
THEMES = {
    "light",
    "dark",
    "system",
    "solarized-dark",
    "tokyo-night",
    "gruvbox-dark",
    "catppuccin-mocha",
    "catppuccin-latte",
    "nord",
    "dracula",
    "everforest-dark",
    "rose-pine",
    "kanagawa",
    "ayu-dark",
    "tron",
    "synthwave-84",
    "cyberpunk-neon",
    "amber-crt",
    "green-phosphor",
    "borland-dos",
    "phosphor-blue",
    "phosphor-purple",
    "commodore-64",
    "amiga-workbench",
    "cga",
    "macintosh-system-6",
    "game-boy",
    "virtual-boy",
    "custom",
}
CUSTOM_THEME_KEYS = {"background", "panel", "line", "foreground", "muted", "accent", "error"}
# Chrome scale steps, as a multiplier on the 11 px base the whole non-terminal UI
# renders at. Discrete rather than a free number: the browser multiplies both the
# font and a fixed set of row/bar heights by this, and there is no useful
# difference between 1.13 and 1.15 — only a way to land on a value that looks
# broken. 1.4 is the ceiling because past it the fixed paddings and grid tracks
# that deliberately do *not* scale start to crowd.
UI_SCALES = {0.9, 1.0, 1.1, 1.25, 1.4}
RAIL_DENSITIES = ("comfortable", "compact", "dense")
# Accepted values for `claude_max_columns`, in terminal columns, where 0 means "no
# cap". Discrete for the same reason the scale steps are: there is no useful
# difference between a 121- and a 123-column envelope, only a way to land on a value
# that reads as broken. 320 is the widest step because past it "no cap" is the
# honest answer rather than a larger number.
CLAUDE_MAX_COLUMNS = {0, 100, 120, 140, 160, 200, 240, 320}
# The note editor's own grammars: a normalized chord (`mod+shift+r`) and a
# command id (`markdown.toggle_task`). The daemon cannot know which commands the
# vendored editor actually implements, so it checks shape only and the browser
# drops anything the editor no longer recognizes.
NOTE_SHORTCUT_CHORD = re.compile(r"^[a-z0-9+`\-=\[\]\\;',./]{1,40}$")
NOTE_SHORTCUT_COMMAND = re.compile(r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")
RESTART_FIELDS = {
    "host",
    "port",
    "data_dir",
    "reconcile_external_history",
    "tailnet_enabled",
    "automation_concurrency",
    "automation_queue_size",
    "openrouter_request_timeout_seconds",
    "pty_supervisor_enabled",
    # Unexpected-loss restoration and checkpoint capture are selected once at
    # daemon start. The durable registry itself remains present for inactive
    # sessions, so this switch never controls explicit Stand down persistence.
    "session_recovery_enabled",
    # Adapters are constructed once at daemon start, so the per-harness MCP and
    # instrumentation gates only re-read on the next restart. Marking them
    # restart-required keeps the UI honest rather than reporting a hot apply that
    # does not reach already-built adapters.
    "harness_mcp_enabled",
    "harness_instrument_enabled",
    "harness_skill_enabled",
}
BUILTIN_THEME_PAIRS = {
    "dark": ("#090a0c", "#d9dde2"),
    "light": ("#f5f2e9", "#252821"),
    "solarized-dark": ("#002b36", "#93a1a1"),
    "tokyo-night": ("#1a1b26", "#c0caf5"),
    "gruvbox-dark": ("#282828", "#ebdbb2"),
    "catppuccin-mocha": ("#1e1e2e", "#cdd6f4"),
    "catppuccin-latte": ("#eff1f5", "#4c4f69"),
    "nord": ("#2e3440", "#d8dee9"),
    "dracula": ("#282a36", "#f8f8f2"),
    "everforest-dark": ("#2d353b", "#d3c6aa"),
    "rose-pine": ("#191724", "#e0def4"),
    "kanagawa": ("#1f1f28", "#dcd7ba"),
    "ayu-dark": ("#0b0e14", "#bfbdb6"),
    "tron": ("#061014", "#b8e6ff"),
    "synthwave-84": ("#262335", "#f4f4f8"),
    "cyberpunk-neon": ("#000b1e", "#0abdc6"),
    "amber-crt": ("#140d00", "#ffb000"),
    "green-phosphor": ("#001100", "#33ff33"),
    "borland-dos": ("#0000a8", "#ffff54"),
    "phosphor-blue": ("#020817", "#9dd7ff"),
    "phosphor-purple": ("#0b0312", "#dda6ff"),
    "commodore-64": ("#40318d", "#bbb7f2"),
    "amiga-workbench": ("#0050a4", "#ffffff"),
    "cga": ("#000000", "#aaaaaa"),
    "macintosh-system-6": ("#f5f5ed", "#111111"),
    "game-boy": ("#0f380f", "#b6d44a"),
    "virtual-boy": ("#000000", "#ff3045"),
}
CCUSAGE_PACKAGE = "ccusage@latest"
MAX_PROJECT_INIT_SCRIPTS = 32
INIT_SCRIPT_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
# HH:MM, 24-hour — the same shape the notification quiet window already uses.
QUIET_TIME = re.compile(r"([01]\d|2[0-3]):[0-5]\d")
#: How many ignore patterns one install may carry. Named because the schema-32 migration
#: has to respect the same ceiling `_validate` enforces: appending past it would turn a
#: silent upgrade into a daemon that refuses to start.
PROJECT_IGNORE_PATTERN_LIMIT = 256
GIT_SWE_MUX_PROMPT_DECISIONS = frozenset({"keep_visible", "ignore_all"})
GIT_SWE_MUX_PROMPT_DECISION_LIMIT = 4096

#: Where the agent providers put worktrees they branch from this checkout, and the bare
#: path Codex also registers. Carried as ignore patterns rather than left to the dynamic
#: `nested_worktrees` lookup because a *pattern* keeps working after a checkout is abandoned
#: and `git worktree list` stops naming it - which is exactly the state a directory nobody
#: is using any more ends up in, and the one where hiding it matters most.
#: These are path patterns rather than bare names on purpose: `.claude` also holds
#: `settings.json`, `agents/`, and `skills/`, which are Project content a person browses.
WORKTREE_IGNORE_PATTERNS = [
    ".claude/worktrees",
    ".codex/worktrees",
    ".agents/worktrees",
    ".worktrees",
]

#: Everything schema 32 added, and what an install written before it gets appended to its
#: stored list. `.trash` joins the worktree roots because it is the same complaint arriving
#: by a second route: the convention that moves a directory there instead of deleting it is
#: what fills it with *abandoned checkouts*, so a browser that hides `.claude/worktrees` and
#: lists `.trash/orphaned-worktrees` has hidden the tidy half of the problem. Deleted content
#: is also the least defensible thing for a file browser to surface by default, and one line
#: in Settings puts it back.
SCHEMA_32_IGNORE_ADDITIONS = [*WORKTREE_IGNORE_PATTERNS, ".trash"]

DEFAULT_PROJECT_IGNORE_PATTERNS = [
    ".git",
    ".swe-mux",
    *SCHEMA_32_IGNORE_ADDITIONS,
    ".venv",
    "venv",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".nox",
    "node_modules",
    ".next",
    ".nuxt",
    ".turbo",
    ".cache",
    "dist",
    "build",
    "coverage",
    "*.pyc",
    "*.pyo",
    "*.code-workspace",
    "uv.lock",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
]


# Conversation-mode command actions the client knows how to execute. Wake words
# and the spoken phrases mapped to each action are user-configurable; the action
# set itself is fixed because each action is wired to code.
VOICE_COMMAND_ACTIONS = (
    "send",
    "append",
    "cancel",
    "undo",
    "mute",
    "read",
    "summary",
    "verbatim",
    "interrupt",
    "help",
    "standby",
    "resume",
    "hold",
    "proceed",
    "comms_on",
    "comms_off",
    "stop",
)
DEFAULT_VOICE_WAKE_WORDS = ["mux", "mucks", "max"]


def default_voice_commands() -> list[dict[str, Any]]:
    return [
        {"action": action, "phrases": list(phrases)}
        for action, phrases in (
            (
                "send",
                [
                    "send",
                    "send it",
                    "send that",
                    "send message",
                    "submit",
                    "submit it",
                    "submit that",
                    "submit message",
                ],
            ),
            (
                "append",
                [
                    "append",
                    "append it",
                    "append that",
                    "append message",
                    "insert",
                    "insert it",
                    "insert that",
                ],
            ),
            ("cancel", ["cancel", "cancel that", "clear", "clear that"]),
            (
                "undo",
                [
                    "undo",
                    "undo that",
                    "undo last",
                    "undo last phrase",
                    "delete last",
                    "delete last phrase",
                ],
            ),
            ("mute", ["mute", "stop", "stop speaking", "stop playback", "stop audio"]),
            (
                "read",
                [
                    "read",
                    "read reply",
                    "read the reply",
                    "read reply again",
                    "read the reply again",
                    "read response",
                    "speak reply",
                    "speak the reply",
                ],
            ),
            ("summary", ["summary", "summary mode", "use summaries"]),
            ("verbatim", ["verbatim", "verbatim mode", "read verbatim"]),
            ("interrupt", ["interrupt", "interrupt agent", "interrupt the agent"]),
            ("help", ["help", "list commands", "what can i say"]),
            ("standby", ["sleep", "go to sleep", "stand by", "standby", "pause listening"]),
            ("resume", ["wake", "wake up", "resume", "start listening"]),
            # Brainstorm hold: chat mode keeps transcribing but stops answering
            # until a proceed cue. Bare exact phrases ("hold on", "go ahead")
            # also work in chat mode; these are the wake-worded forms.
            ("hold", ["listen", "just listen", "brainstorm", "hold that thought", "let me think"]),
            ("proceed", ["go ahead", "your turn", "over to you", "proceed"]),
            (
                "comms_on",
                ["voice comms", "voice comms on", "start voice comms", "enter voice comms"],
            ),
            ("comms_off", ["voice comms off", "stop voice comms", "exit voice comms"]),
            ("stop", ["stop listening", "turn off", "shut down"]),
        )
    ]


# Touch-gesture slots recognized on mobile. Edge- and top-anchored swipes are
# deliberately absent: on Android those belong to the OS (back / home / notification
# shade), so the mappable channels are mid-screen single-finger horizontal swipes and
# two-finger gestures. Each slot maps to a command id (see keybindings.COMMAND_IDS) or
# "" to disable it.
# Vertical two-finger swipes are mappable: only the *single*-finger vertical
# channel is reserved for the terminal (scrollback / application wheel).
# `rail_swipe_up` is the one region-scoped slot: it is recognized only for a touch
# that began on the command rail, which owns every horizontal touch for its own pan
# but has no vertical scroll for an upward swipe to steal.
MOBILE_GESTURE_SLOTS = (
    "swipe_left",
    "swipe_right",
    "two_finger_swipe_left",
    "two_finger_swipe_right",
    "two_finger_swipe_up",
    "two_finger_swipe_down",
    "two_finger_tap",
    "rail_swipe_up",
)


def default_mobile_gestures() -> dict[str, str]:
    return {
        "swipe_left": "mobileTab.next",
        "swipe_right": "mobileTab.previous",
        # Left/right are directional: swiping right drags the left-edge sidebar in,
        # swiping left (i.e. starting at the right edge) drags the right-edge utility
        # drawer in. Both were sidebar.toggle before that drawer existed.
        "two_finger_swipe_left": "drawer.toggle",
        "two_finger_swipe_right": "sidebar.toggle",
        "two_finger_swipe_up": "notes.open",
        "two_finger_swipe_down": "terminal.keyboardToggle",
        "two_finger_tap": "palette.open",
        # The app menu, from the strip under the operator's thumb. Its only other
        # door on a phone is the sidebar footer.
        "rail_swipe_up": "menu.toggle",
    }


def default_ccusage_command(source: str | None = None) -> list[str]:
    if source:
        return ["ccusage", source, "daily", "--json"]
    return ["ccusage", "daily", "--json", "--by-agent"]


def default_harness_executables() -> dict[str, str]:
    return {name: host_executable(harness) for name, harness in HARNESSES.items()}


def default_harness_args() -> dict[str, list[str]]:
    return {name: list(harness.default_args) for name, harness in HARNESSES.items()}


def default_usage_commands() -> dict[str, list[str]]:
    """Legacy per-source overrides retained for custom existing configurations."""
    return {}


_LEGACY_CCUSAGE_COMMANDS = {
    "claude": ["--no-install", "ccusage@17.1.5", "daily", "--json"],
    "codex": ["--no-install", "@ccusage/codex@0.2.7", "daily", "--json"],
}


def contrast_ratio(first: str, second: str) -> float:
    def luminance(color: str) -> float:
        channels = [int(color[index : index + 2], 16) / 255 for index in (1, 3, 5)]
        linear = [
            channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4
            for channel in channels
        ]
        return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]

    light, dark = sorted((luminance(first), luminance(second)), reverse=True)
    return (light + 0.05) / (dark + 0.05)


@dataclass(slots=True)
class LaunchProfile:
    """A named executable/argv/environment definition for one backend.

    Shells have had these since the beginning; an agent harness gets the same thing,
    which is what lets one Project offer "Claude" and "Claude (plan)" side by side
    instead of one global argument list per harness. `backend` is what separates the
    two, and it is the only field an existing shell profile does not already carry -
    which is why it defaults to `shell` and sits last, so a profile written by an
    older build loads unchanged.

    The stored configuration keys stay `shell_profiles` / `default_shell_profile` /
    `SessionRecord.shell_profile_id`. Renaming them would rewrite a user's
    `~/.mux/config.toml`, a committed `.swe-mux/config.toml` key, and a history
    column, none of which buys a capability. The *concept* is a launch profile
    everywhere it is spoken about; those three names are storage history.

    An agent profile may leave `executable` empty, which inherits `harness_exe` for
    its backend. `cwd_strategy`, `cwd_integration`, and `marker` describe an
    interactive shell and are refused on an agent profile rather than silently
    ignored: `resolve_profile`'s PowerShell bootstrap and WSL path translation both
    assume a shell, and an agent launch never reaches them.
    """

    id: str
    label: str
    executable: str = ""
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    platforms: list[str] = field(default_factory=lambda: [platform_key()])
    cwd_strategy: str = "native"
    marker: str = "sh"
    capabilities: list[str] = field(default_factory=lambda: ["interactive", "agent-aware"])
    cwd_integration: bool = False
    enabled: bool = True
    backend: str = "shell"


@dataclass(frozen=True, slots=True)
class BudgetSpec:
    """One spending cap: where it lives, what it replaced, and its bounds.

    Every model-cost ceiling in the install is described here exactly once, and
    the three things that used to be scattered - the field, its validation
    range, and the pre-`Budget` keys it must absorb without loosening - are one
    record so they cannot drift apart.

    `legacy_tokens` / `legacy_usd` name the scalar settings this budget replaced,
    and `default` carries the value each of those scalars defaulted to. That
    pairing is what makes the upgrade lossless: a config that set only one half
    of a pair had the *other* half enforced at its default by the previous
    build, so migration must fill it from here rather than leave it unset.
    """

    field: str
    default: Budget
    max_tokens: int
    max_usd: float
    legacy_tokens: str = ""
    legacy_usd: str = ""
    min_tokens: int = 0
    min_usd: float = 0.0
    label: str = ""


#: The install's complete set of spending caps.
#:
#: The mode each one defaults to is the unit the pre-`Budget` build enforced:
#: the automation ceilings checked tokens *and* dollars, which is `either`;
#: the assistant, read-aloud summaries, and the Project card checked dollars
#: only. Migration reads the mode from here, so no existing cap can silently
#: widen. The scan timeline and attention narration once carried their own
#: caps here; both now spend under `automation_daily_budget` and the global
#: hourly call cap, and a config still naming the retired fields loads with
#: them dropped.
#:
#: Rate limits are deliberately absent. `automation_hourly_call_cap`,
#: `agent_message_hourly_budget`, `attention_daily_interrupt_budget`, and the
#: rest count *acts*, not spend; they never read the ledger, and forcing them
#: into a tokens-or-dollars choice would ask the operator to denominate a thing
#: that has no price. Per-call ceilings (`automation_max_output_tokens` and its
#: siblings) are absent for the same reason: they bound one request's size
#: rather than a period's spend.
BUDGET_SPECS: tuple[BudgetSpec, ...] = (
    BudgetSpec(
        field="automation_daily_budget",
        default=Budget(tokens=10_000_000, usd=20.0, mode="either"),
        max_tokens=100_000_000,
        max_usd=10_000.0,
        legacy_tokens="automation_daily_token_budget",
        legacy_usd="automation_daily_budget_usd",
        label="All automation",
    ),
    BudgetSpec(
        field="automation_rule_daily_budget",
        default=Budget(tokens=4_000_000, usd=10.0, mode="either"),
        max_tokens=100_000_000,
        max_usd=10_000.0,
        legacy_tokens="automation_rule_daily_token_budget",
        legacy_usd="automation_rule_daily_budget_usd",
        label="Per automation rule",
    ),
    BudgetSpec(
        field="assistant_daily_budget",
        default=Budget(tokens=None, usd=2.0, mode="usd"),
        max_tokens=100_000_000,
        max_usd=1_000.0,
        legacy_usd="assistant_daily_budget_usd",
        label="Assistant, daily",
    ),
    BudgetSpec(
        field="tts_daily_budget",
        default=Budget(tokens=None, usd=1.0, mode="usd"),
        max_tokens=100_000_000,
        max_usd=100.0,
        legacy_usd="tts_daily_budget_usd",
        label="Read-aloud summaries, daily",
    ),
    BudgetSpec(
        field="project_card_daily_budget",
        default=Budget(tokens=None, usd=0.25, mode="usd"),
        max_tokens=100_000_000,
        max_usd=100.0,
        legacy_usd="project_card_daily_budget_usd",
        label="Project context card, daily",
    ),
)

BUDGET_FIELDS: dict[str, BudgetSpec] = {spec.field: spec for spec in BUDGET_SPECS}


@dataclass(slots=True)
class Config:
    schema_version: int = SCHEMA_VERSION
    revision: int = 1
    host: str = "127.0.0.1"
    port: int = 8765
    tailnet_enabled: bool = True
    # Whether the daemon also listens on the WSL virtual adapter, which is what an
    # agent running natively inside a distribution needs in order to reach its hook
    # ingress. Off by default and deliberately not inferred from "WSL exists":
    # binding it lets any process in any distribution on this machine reach the
    # daemon, and swe-mux has no application login, so that is a real widening of
    # who holds terminal and code-execution authority. `design/features/backends.md`
    # states the boundary; `wsl_bridge.py` explains why loopback cannot serve here.
    wsl_bridge_enabled: bool = False
    default_backend: str = "shell"
    # Which agent to launch when something needs *an agent* and nobody named one.
    #
    # A separate question from `default_backend`, which answers "what does Run
    # open by default" and legitimately defaults to `shell`. A shell is not an
    # answer here: it cannot receive a seeded prompt, so a caller that needs an
    # agent and reads `shell` has to fall through to something, and every such
    # caller inventing its own fallback is how two launchers end up disagreeing
    # about which harness is "yours".
    #
    # Empty means "resolve by detection", which is the right default because a
    # machine with exactly one agent installed has no choice to make and should
    # not be asked. `harness.resolve_default_harness` owns the whole rule.
    default_harness: str = ""
    shell_exe: str = field(default_factory=lambda: default_shell_executable())
    harness_exe: dict[str, str] = field(default_factory=default_harness_executables)
    harness_args: dict[str, list[str]] = field(default_factory=default_harness_args)
    # Explicit per-harness enablement choices only. An absent key means "follow
    # detection", so a CLI installed later appears on its own and one the user
    # forces on or off stays that way. This is a launcher/UI filter: a disabled
    # harness is hidden from the pickers but stays spawnable by an explicit API or
    # CLI call, and every status, transcript, and history surface keeps seeing all
    # registered harnesses. Empty by default, so a fresh install follows detection
    # for everything.
    harness_enabled: dict[str, bool] = field(default_factory=dict)
    # Per-harness "attach the mux MCP server" choice; absent key means on. Turning
    # it off for a harness removes only that agent's fleet visibility and
    # messaging (the read-only mux MCP surface); status, history, and the queue are
    # unaffected. Empty by default (everything on). Restart-scoped: adapters are
    # built once at daemon start, so a change takes effect on the next daemon
    # restart (sessions survive it).
    harness_mcp_enabled: dict[str, bool] = field(default_factory=dict)
    # Per-harness "deliver the swe-mux agent skill automatically" choice; absent
    # key means OFF - the opposite default from the MCP map, deliberately. For
    # Claude the delivery is a data-dir plugin named on the spawn argv
    # (`--plugin-dir`), which writes nothing into anyone's checkout; for every
    # other harness it is a spawn-time write of `.agents/skills/swe-mux/` into
    # the session's project tree, because none of those CLIs accepts a skills
    # directory by flag, env var, or config key - and a write into a user's
    # repository is opt-in, never a default. Turning it off stops the writes and
    # leaves existing files; `swemux install-skill --remove` takes them back.
    # Restart-scoped like the MCP toggle, and for the same reason.
    harness_skill_enabled: dict[str, bool] = field(default_factory=dict)
    # Per-harness "instrument / launch clean" choice; absent key means instrument.
    # Turning it off launches that harness WITHOUT mux's lifecycle hooks, which
    # drops it to unobserved: no status detection, no history capture, no prompt
    # queue for its sessions. Load-bearing, so the UI must name that consequence.
    # Empty by default (everything instrumented). Restart-scoped like the MCP toggle.
    harness_instrument_enabled: dict[str, bool] = field(default_factory=dict)
    # Whether the first-run harness panel has been dismissed (enabled or skipped).
    # Machine-side rather than device-local, because harness enablement is machine
    # config: a first-run choice made on the desktop must not reappear on the phone.
    # Skipping sets only this flag and writes no `harness_enabled` entries, so a
    # harness installed next week is still picked up by detection.
    harness_setup_complete: bool = False
    scrollback_bytes: int = 5 * 1024 * 1024
    # What a *fresh attach* replays, as opposed to what the daemon retains. The
    # client has to parse every replayed byte before it can render anything, and
    # xterm time-slices that across render frames, so a full-buffer replay is
    # visibly watched happening — worst on a CLI whose transcript lives in
    # scrollback (Codex, `tui.alternate_screen="never"`), whose bytes are real
    # lines that each allocate and scroll rather than repaints of one alternate
    # screen. Retention is unchanged: scrolling back
    # further is a client concern, reconnect latency is everyone's.
    attach_replay_bytes: int = 512 * 1024
    # Session-preserving reload: spawn PTYs in the out-of-process supervisor so
    # live sessions survive a daemon restart.
    #
    # **On by default since 2026-08-28.** It shipped off "while the split proves
    # itself", and the consequence of leaving it there was that a fresh install
    # got no supervisor at all while every description of the product - the
    # README, the site, the tray's own "Restart daemon (keep sessions)" - claimed
    # sessions outlive a restart. The only two ways out were to weaken the claim
    # or to make it true. This is the second.
    #
    # What carries the residual risk is the fallback, and it is the reason this
    # flip is safe rather than brave: `server.py` wraps
    # `SupervisorClient.connect_or_spawn` in a bare `except Exception` and starts
    # the daemon unsupervised when anything at all goes wrong - no supervisor
    # bundle beside a frozen app, no `config_path`, a port the loopback socket
    # cannot take, a child that dies before it writes its discovery file. So the
    # worst case of this default is the behaviour that used to be the *only*
    # behaviour, plus one logged exception, and a daemon that will not start is
    # not among the outcomes. `tests/test_live_daemon.py` holds that fallback and
    # the supervised path to the same standard on every CI runner.
    #
    # A **source** install (pip/uv tool/pipx) needs no bundle: `supervisor_command`
    # launches `python -m swe_mux.supervisor`, which is the same wheel the daemon
    # was imported from. Only a *frozen* app resolves a dedicated bundle, and only
    # a frozen app can be missing one.
    pty_supervisor_enabled: bool = True
    # Cold session recovery: keep a durable registry of live sessions so ones
    # whose daemon *and* PTY owner both died come back as visible-but-dead rows
    # rather than vanishing. On by default - the supervisor covers a daemon
    # restart, and this covers everything that kills both.
    session_recovery_enabled: bool = True
    # Terminal bytes kept per session for a cold restore, so a recovered pane
    # shows what it printed. 0 keeps the registry (the part that brings sessions
    # back) and stores no bytes at all. Deliberately far below `scrollback_bytes`:
    # a cold pane is a post-mortem, not a session, and only harnesses whose
    # retained bytes are a real transcript are checkpointed in the first place.
    session_recovery_checkpoint_bytes: int = 256 * 1024
    # How long recovery data for an *ended* session is kept. Cold sessions
    # themselves are bounded by count below, not by this.
    session_recovery_retention_days: int = 7
    # Hard cap on how many recoverable sessions are retained, newest first.
    # Without it a machine that crashes repeatedly accumulates cold rows forever.
    session_recovery_max_sessions: int = 40
    # Startup default for the daemon's root-logger level (rotating daemon.log +
    # console). Runtime changes go through POST /api/debug/log-level or a
    # config-file edit; neither requires a restart.
    log_level: str = "INFO"
    # Whether the daemon may ask https://swemux.dev/version.json once a day
    # whether a newer release exists (`update_check.py`). On by default and
    # deliberately visible in Settings, because it is the **only** request
    # swe-mux makes on its own behalf and the README's no-telemetry claim is
    # written around it: the fetch carries no query string, no custom header, no
    # cookie, and no install id, so it is byte-identical for every install. Off
    # means nothing leaves the machine at all - not a reduced check, not a
    # deferred one. Read live at each check, so a toggle takes effect without a
    # restart.
    update_check_enabled: bool = True
    # Whether the daemon will prefer a hash-verified `static/` overlay in the data
    # dir over its own bundled frontend (`frontend_overlay.py`). On by default,
    # because an overlay only exists at all when somebody deliberately installed
    # one and every failure resolves back to the bundled tree. Off is for an
    # operator who wants the mechanism gone: the daemon then serves the bundle and
    # says so, without removing anything that is installed. Read at app
    # construction, so a change takes effect at the next daemon start - the same
    # moment the overlay itself would.
    frontend_overlay_enabled: bool = True
    terminal_renderer: str = "auto"
    # Desktop width envelope for Claude panes, in columns. Claude Code's live-region
    # renderer can leave stale and duplicated cells across large column changes, so a
    # Claude pane dragged wider than this adds margin instead of resizing the PTY
    # again. 0 removes the cap and lets a Claude pane fill its box like every other
    # backend. Configurable rather than fixed because the evidence for any particular
    # number is a defect in a CLI that ships on its own schedule, while the cost of a
    # stale number lands on someone who cannot tell an envelope from a broken resize.
    # 120 is what the app has always done: installing this build changes nothing.
    claude_max_columns: int = 120
    git_poll_seconds: float = 5.0
    # The Git tab's per-Project repository-setup question is machine-side, not
    # browser-local: answering on the phone must not ask again on the desktop.
    # The mapping records only explicit Project decisions. "Never ask again" flips
    # the global switch without manufacturing a decision for every registered Project,
    # so turning the switch back on genuinely restores unresolved prompts.
    git_swe_mux_prompt_enabled: bool = True
    git_swe_mux_prompt_decisions: dict[str, str] = field(default_factory=dict)
    # Empty means the app-managed directory below data_dir. The public config exposes
    # the resolved absolute value so browser clients never infer the daemon's home.
    worktree_root: str = ""
    # The one parent directory the assistant's create_project tool may create new
    # project folders inside. Deliberately name-only at the tool: the model never
    # supplies a path, so a chat message cannot create a folder anywhere else on
    # disk. Empty disables assistant project creation (the tool refuses and points
    # here); the Add-project dialog is unaffected and may name any parent. Shape is
    # validated here, existence at use time - a directory deleted while the daemon
    # is down must not stop the config from loading.
    new_project_parent: str = ""
    process_poll_seconds: float = 5.0
    process_orphan_grace_seconds: float = 15.0
    # Windows-only sweep for headless-browser windows that DWM composites even
    # though Win32 reports them hidden (ghost_windows.py). Off by config only
    # when an operator deliberately wants such a window left on screen.
    ghost_window_sweep_enabled: bool = True
    ghost_window_poll_seconds: float = 5.0
    process_evidence_retention_days: int = 30
    operational_telemetry_retention_days: int = 180
    # Durable per-session detection timeline (status_timeline.py): chattier
    # than the other telemetry (every ledger entry, including a busy turn's
    # detail churn), so its window is its own knob.
    status_timeline_retention_days: int = 30
    provider_quota_poll_minutes: int = 15
    provider_quota_turn_refresh_enabled: bool = False
    provider_quota_turn_refresh_min_minutes: int = 5
    reconcile_external_history: bool = True
    startup_cwd: str = ""
    history_limit: int = 200
    # Reached only by an install with no `config.toml` at all: `_serialize` writes
    # every field unconditionally, so any existing install already has its own
    # `theme` on disk and `load_config`'s field-copy loop restores it. Flipping
    # this default therefore cannot repaint a machine that has ever run swe-mux -
    # neither one that chose `dark` nor one that simply never touched the setting -
    # and needs no migration to say so.
    theme: str = "tokyo-night"
    # Drawer tab strips and the desktop right rail independently render either
    # their compact icon or short registry title. These presentation-only
    # settings apply live in every connected browser.
    drawer_tab_display: str = "icon"
    utility_rail_display: str = "icon"
    custom_theme: dict[str, str] = field(
        default_factory=lambda: {
            "background": "#090a0c",
            "panel": "#0d0f12",
            "line": "#2a2e34",
            "foreground": "#d9dde2",
            "muted": "#848b94",
            "accent": "#8bd450",
            "error": "#f07178",
        }
    )
    # Chrome scale, split by device class because the same UI is driven from a
    # desktop browser and a phone and they do not want the same density — a
    # single number cannot say "the phone is too small but the desktop is fine".
    # The browser picks one by the same `(max-width:760px)` breakpoint the
    # workspace uses, so a desktop window dragged narrow switches with it.
    # Both default to 1.0: installing this build changes nothing on screen.
    ui_scale_desktop: float = 1.0
    ui_scale_mobile: float = 1.0
    # How tightly the terminal's Action rail packs, split per device class for the
    # same reason chrome scale is: the desktop is where the height a rail costs is
    # worth trading away, and the phone has a touch floor the desktop does not.
    # Both default to "comfortable", which is exactly the spacing that shipped
    # before the setting existed - installing this build changes nothing on screen.
    rail_density_desktop: str = "comfortable"
    rail_density_mobile: str = "comfortable"
    middle_click_paste: bool = True
    broadcast_default: bool = False
    mobile_vertical_drag: str = "smart"
    mobile_scroll_direction: str = "natural"
    mobile_scroll_sensitivity: float = 1.0
    mobile_long_press: str = "context_menu"
    mobile_gestures: dict[str, str] = field(default_factory=default_mobile_gestures)
    # While the sidebar or utility drawer is open, the horizontal swipe pointing back
    # toward the edge it slid in from closes it instead of running that slot's binding.
    mobile_gesture_swipe_away_close: bool = True
    # While any overlay level is open (a modal, or a drill-down inside one), a rightward
    # swipe closes one level instead of running that slot's binding. Off restores the
    # original behaviour, where an overlay ignored every gesture rather than reassigning
    # them: a swipe over a modal must never run a workspace binding behind it.
    mobile_gesture_overlay_back: bool = True
    # With no overlay open, back steps through the tabs and Projects most recently looked
    # at on this device (a bounded in-memory ring, mobile layout only) before it leaves
    # the app. Off restores the original behaviour, where back on a session backgrounded
    # the whole PWA.
    mobile_back_view_history: bool = True
    # Swipes that belong to one piece of chrome rather than to the screen: the voice
    # panel's header (size and mode), the mobile top bar's Project name (step through
    # Projects, or open the Project menu), a tab on the mobile tab rail (its menu), and
    # the note editor's command rail (the heading outline). Each acts on the surface it
    # starts on, so unlike `mobile_gestures` there is nothing to rebind - one action per
    # direction is the only one that means anything there. Off turns all four off; the
    # command rail's swipe is a rebindable slot instead and is unaffected.
    mobile_surface_gestures: bool = True
    terminal_auto_copy_selection: bool = True
    # Clipboard history (clipboard_store.py). Capture is in-app only — nothing
    # polls the OS clipboard — and the ring is memory-only unless `persist` is
    # set, because a durable ring of copied text accumulates credentials.
    clipboard_history_enabled: bool = True
    clipboard_history_persist: bool = False
    clipboard_history_limit: int = 200
    clipboard_history_entry_max_chars: int = 100_000
    clipboard_history_retention_hours: int = 24
    clipboard_history_redact_secrets: bool = True
    notes_default_open: str = "dock"
    # Note editor (the vendored Continuity Markdown editor). Only what the
    # editor genuinely exposes is here: element properties/attributes
    # (`spellcheck`, `syntax`, `tab-behavior`, `shortcut-policy`,
    # `command-rail`) and its `--continuity-*` custom properties. Colours are
    # deliberately absent — style.css already maps the app palette onto the
    # editor's colour variables, and a second source for them would fight it.
    note_spellcheck: bool = False
    note_syntax: str = "markdown"
    note_tab_behavior: str = "indent"
    note_shortcut_policy: str = "browser-safe"
    # Empty/zero means "keep the editor's own default" rather than pinning a
    # value here, so a Continuity upgrade can still move its defaults.
    note_font_family: str = ""
    note_font_size_px: int = 0
    note_line_height: float = 0.0
    note_command_rail: str = "auto"
    note_rail_button_size_px: int = 0
    # Vertical rules at each enclosing indent level. Continuity ships this off so that
    # upgrading it changes no embedder's appearance; on is the right default here because
    # notes are mostly nested lists, and it matches Continuity's own desktop application.
    note_indent_guides: bool = True
    # Chord → command overlay on the editor's built-in shortcut table, checked
    # before the policy filter. The empty string carries the library's `null`
    # (release the chord to the browser) because TOML has no null; the browser
    # maps it back. The two defaults reclaim the bullet/task chords that
    # `browser-safe` would otherwise hand to Chromium.
    note_shortcut_overrides: dict[str, str] = field(
        default_factory=lambda: {
            "mod+r": "editor.toggle_bullet_at_line_start",
            "mod+e": "markdown.toggle_task",
        }
    )
    ccusage_enabled: bool = False
    ccusage_refresh_minutes: int = 0
    usage_command: list[str] = field(default_factory=default_ccusage_command)
    usage_commands: dict[str, list[str]] = field(default_factory=default_usage_commands)
    default_shell_profile: str = "default"
    shell_profiles: list[LaunchProfile] = field(default_factory=list)
    # Whether a shell pane's PATH begins with `~/.mux/bin`, so that typing an
    # agent's name in a terminal launches it *through* mux and promotes the pane
    # (`backends.md`, "A plain terminal promotes itself").
    #
    # On by default, because that promotion is what makes a typed launch a
    # first-class session rather than an unnamed shell. It is a setting because
    # the trade is real and was previously unavoidable: the shim inserts two
    # processes between the shell and the CLI, and someone using swe-mux purely as
    # a terminal multiplexer is paying for a feature they are not using — `claude`
    # in their terminal should be able to mean *their* `claude`.
    #
    # Turning it off changes only what a shell's PATH advertises. Agents mux
    # launches itself never go through a shim (they are spawned straight into the
    # pseudoconsole), so no harness, hook, or MCP wiring depends on this; a typed
    # launch simply stays a shell until the transcript detector notices it.
    agent_shims_on_shell_path: bool = True
    pinned_directories: list[str] = field(default_factory=list)
    project_ignore_patterns: list[str] = field(
        default_factory=lambda: list(DEFAULT_PROJECT_IGNORE_PATTERNS)
    )
    project_init_scripts: list[dict[str, Any]] = field(default_factory=list)
    automation_enabled: bool = False
    # The install-wide ceiling over the per-Project automation opt-ins: an id
    # mapped to False here is off in every Project, along with everything that
    # depends on it, and the per-Project switch renders greyed rather than
    # silently inert. Absent means allowed, so an empty map changes nothing.
    # Three ids never appear here because they already have dedicated install
    # switches (`scan_timeline_enabled`, `scheduled_runs_enabled`,
    # `land_queue_enabled`) - one switch, one key. Unknown ids are scrubbed on
    # load (a registry that retired an id must not brick the config) and
    # refused on write (a typo must fail loudly, not silently allow).
    automation_global_allow: dict[str, bool] = field(default_factory=dict)
    # Install-wide *default* for the per-Project agent authority fields
    # (`agent_authority.AUTHORITY_FIELDS`): field id -> level. It applies only
    # where a Project left the field unset, so it can never change what a
    # Project that wrote a value already does, and an empty map reproduces the
    # built-in defaults exactly. Unknown ids and levels are scrubbed on load and
    # refused on write, the same asymmetry `automation_global_allow` uses: a
    # retired field must not brick the config, a typo must fail loudly.
    agent_authority_default: dict[str, str] = field(default_factory=dict)
    # Install-wide *ceiling* over the same fields, and the only layer that can
    # reach a Project whose file holds an explicit value. It can only narrow.
    # Separate from the default above because they answer different questions:
    # "what should an undecided Project do" against "what may no Project on this
    # machine do, whatever its file says". A single map with a precedence rule
    # would make the first silently mean the second for pinned Projects.
    agent_authority_ceiling: dict[str, str] = field(default_factory=dict)
    automation_retention_days: int = 90
    # Prompt-queue history (sent/cancelled/failed/stranded items and their
    # delivery audit) ages out on this window; pending items never do.
    prompt_queue_retention_days: int = 90
    # Phase 5 auto-delivery. The master switch is off by default; when enabled,
    # every live Claude/Codex conversation gets a bounded default-on grant. The
    # emergency pause and per-conversation overrides live in SQLite
    # (`queue_auto_policy`) so it stays instant, persistent, and independent of
    # config-file writes. These knobs are the bounds each grant runs under.
    auto_delivery_enabled: bool = False
    # How long `delivery_state=safe` must hold, continuously, before a send.
    auto_delivery_stable_seconds: float = 8.0
    # Consecutive automatic sends allowed before the grant disables itself. A
    # manual send by the user resets the count — it is evidence of attention.
    auto_delivery_max_consecutive: int = 3
    # How long a conversation may sit *idle* before its grant lapses. Standing
    # authorization is what turns a bounded convenience into an unattended
    # actuator, and idleness is the thing that makes it unattended. Measuring it
    # from the grant's creation instead disabled auto-delivery on every session
    # older than the window while it was actively in use.
    auto_delivery_session_ttl_minutes: int = 60
    # How long a session that has just had a message *delivered* to a peer keeps
    # its grant while it waits for the answer. A conversation waiting on a reply
    # it is owed is not the untouched conversation the idle lapse exists to
    # close - it is the middle of a bounded exchange - and losing the grant there
    # is what stranded an orchestrator's hand-off on 2026-08-21: the notify armed,
    # the worker answered, and nothing could deliver the answer back.
    #
    # This widens nothing on its own. It only holds off the *lapse*; the master
    # switch, the emergency pause, quiet hours, head-of-line order, the stability
    # window, readiness, and the consecutive-send cap all still decide every send.
    # And it is capped by the exchange itself: a thread that has spent its
    # `agent_message_max_thread_turns` budget opens no window, so the reply window
    # can never outlive the conversation it belongs to. 0 disables it entirely and
    # restores the pre-2026-08-21 behaviour.
    auto_delivery_reply_window_minutes: int = 30
    # Local-time quiet window (HH:MM). Auto-delivery pauses inside it; manual
    # sends are unaffected.
    auto_delivery_quiet_start: str = ""
    auto_delivery_quiet_end: str = ""
    # Back-off after a refused automatic attempt, so a session whose readiness
    # flaps cannot spin the audit log.
    auto_delivery_refusal_backoff_seconds: float = 30.0
    # Control-plane approvals (`approvals.py`). The master switch is off by
    # default: with it off, `PermissionRequest` is observed exactly as before and
    # no session can hold a non-`wait` mode. The bounds below are what every
    # grant runs under; the grant itself is per-conversation runtime state on the
    # session record, not config, so switching it back off is instant.
    approval_auto_enabled: bool = False
    # How long a grant lives before it decays back to `wait`. Short on purpose:
    # the failure mode this bounds is an operator who switched `allow_all` on for
    # one task and left it on for the day.
    approval_grant_ttl_minutes: int = 30
    # Requests one grant may answer before it disables itself. The cap is per
    # grant rather than per hour so a runaway loop is bounded by count, which is
    # the thing that actually runs away.
    approval_max_auto_per_grant: int = 200
    # What the generated hook settings tell the CLI to wait for. Claude's own
    # default is 600 s and a timed-out hook falls through to the normal prompt,
    # so this is not a correctness gate — it is the difference between a daemon
    # hiccup costing a second and costing ten minutes of a stalled agent.
    approval_hook_timeout_seconds: float = 5.0
    # Whether `allow_all` may be selected at all on this install. Separate from
    # the master switch because "let mux answer the boring ones" and "let mux
    # answer everything" are different decisions, and the first is the one most
    # installs want.
    approval_allow_all_permitted: bool = True
    # Deliver an already-decided approval as a keystroke when the CLI publishes
    # the request but ignores the answer. Measured on Claude Code 2.1.234: the
    # `PermissionRequest` hook fires, mux answers `allow` in 0.25 s, and the CLI
    # shows the prompt anyway a constant ~6 s later regardless of the hook
    # timeout — so the documented decision channel exists and does nothing.
    #
    # This types only what the structured request already authorized, only while
    # this session's own screen is showing that dialog. It cannot decide
    # anything, so a trust dialog or a `/clear` confirmation — neither of which
    # raises a permission request — is unreachable from here. On by default
    # because a decided approval that never arrives is the failure it exists to
    # fix; a CLI that starts honouring the hook silently retires it, since the
    # dialog never appears for the watcher to answer.
    approval_keystroke_delivery: bool = True
    # How long a decided approval waits for its dialog to appear before the
    # watcher gives up and lets the ordinary visible approval stand.
    approval_keystroke_window_seconds: float = 30.0
    # Phase 5 agent-to-agent messaging (`mux.notify`). The tool exists by
    # default because a notify lands as an inert draft unless the *receiving*
    # session opted in to accepting agent messages.
    agent_messaging_enabled: bool = True
    # Whether the configurable rate bounds below actually bind. Off (the
    # default), an orchestrator relaying work across a fleet for hours never
    # trips a send/receive budget: the six rate bounds - the hourly budget, the
    # per-target backlog, chain depth, thread turns, and the two interject
    # bounds - are replaced by the fixed backstop ceilings in
    # `agent_message_bounds()`, which exist to end a runaway loop rather than
    # to pace a working exchange. On, the configured values below bind exactly
    # as they always did. Deliberately *not* unlimited when off: the thread
    # budget is the one brake that ends two agents talking forever (a reply
    # refreshes the peer's auto-delivery grant, so a two-party exchange renews
    # itself), and removing it entirely would let that loop run unattended.
    # `agent_message_max_chars`, ring detection, expiry, and the kill switches
    # are not rate limits and bind regardless.
    agent_message_limits_enabled: bool = False
    agent_message_max_chars: int = 4000
    agent_message_hourly_budget: int = 20
    agent_message_pending_per_target: int = 5
    # Two separate relay bounds, because they answer different questions.
    # `max_chain_depth` bounds *propagation* - how many distinct sessions one
    # relay thread may reach - and only grows when a message reaches a session
    # that has not spoken in it yet. `max_thread_turns` bounds *volume* within a
    # single thread, which is what actually stops two agents talking forever.
    # Replying to whoever messaged you is an ordinary turn under both.
    #
    # Depth 3 was calibrated when the only shape anyone used was "tell one
    # sibling", and it forbids an ordinary operator-authored relay across a
    # fleet outright: a hand-off passed down five sessions is refused at the
    # fourth with no way for the chain to continue. The hazard the bound exists
    # for is *breadth* - one injected instruction fanning out - which the hourly
    # budget, the per-target backlog, and the ring detector all bound
    # separately. The default is now one longer than a full pass over a
    # typical fleet, and it is still a bound rather than an invitation.
    agent_message_max_chain_depth: int = 6
    # 12 was a guess made before any exchange had run long enough to test it. A
    # three-way fleet doing real work reached 9 in ninety minutes (measured
    # 2026-08-19), so the bound was about to stop a working conversation rather
    # than a runaway one — and its refusal ("summarise for a human") is right for
    # the second and wrong for the first. This is the *volume* bound and it is
    # now the one that actually stops two agents talking forever, since a reply
    # also refreshes the receiving session's auto-delivery budget
    # (`auto_delivery.py`), so it is sized to outlast a working exchange while
    # still ending one that has stopped converging.
    agent_message_max_thread_turns: int = 40
    # Mid-turn delivery (`mux.notify(delivery="now")`). The install-wide master
    # switch; per-Project the capability defaults to granted and is withdrawn by
    # setting `interject_grant = "off"` in that Project's `.swe-mux/config.toml`
    # (flipped 2026-08-25 - it began life default-off), and the receiving
    # session can still opt out for its run. False here refuses it everywhere.
    #
    # An interject is not an override: it is authorized by its own, strictly
    # narrower predicate (`interject_state` in `delivery_readiness.py`), which
    # requires the lifecycle evidence and the CLI's own screen to agree that a
    # turn is running and nothing else is true. What it buys is latency - the
    # CLI buffers the text and takes it at the turn boundary - not preemption.
    agent_interject_enabled: bool = True
    # How many mid-turn deliveries one origin session may ask for per hour. Far
    # tighter than the ordinary message budget: a message that waits costs the
    # receiver nothing until it is read, and one that lands mid-turn costs it
    # attention immediately.
    agent_interject_hourly_budget: int = 10
    # The floor between two mid-turn deliveries into the *same* session, so a
    # peer cannot machine-gun a session that is trying to work.
    agent_interject_min_interval_seconds: float = 60.0
    # `mux.requestSpawn` creates an inert Fleet Queue approval draft and nothing
    # else; approval is a human act.
    request_spawn_enabled: bool = True
    # Phase 7.6 agent session control (`mux.interrupt`, `mux.end_session`). This is
    # the install-wide master switch. Per-Project the `session_control` automation
    # is on by default (`automation_registry.DEFAULT_ON_AUTOMATIONS`, flipped
    # 2026-08-25) and the authority defaults to `granted`; a Project withdraws
    # either with `session_control = false` in its automations table or
    # `session_control_grant = "draft"`. False here refuses both tools everywhere,
    # regardless of any Project's grant.
    session_control_enabled: bool = True
    # How many control actions one origin session may take per hour on the granted
    # path, the analogue of `agent_message_hourly_budget` for actuation.
    session_control_hourly_budget: int = 30
    # How long a graceful end waits for the CLI to tear itself down after the exit
    # sequence before it falls back to a hard stop.
    session_control_graceful_timeout_s: float = 12.0
    # Phase 7.6 follow-on: how many sessions one origin may spawn per hour on the
    # granted path (`mux.requestSpawn` with a Project `spawn_grant` of "granted").
    # Spawn's blast radius is a single injection into fan-out, so this is smaller
    # than the interrupt/end budget and is what bounds that fan-out.
    agent_spawn_hourly_budget: int = 10
    # Session-settle watches (`mux.watch_session`). The install-wide switch is
    # here for the same reason the others are: it is the emergency stop, and off
    # means no watch is armed anywhere. A watch reads a state the caller can
    # already read and produces one deterministic queue item addressed to the
    # caller itself, so there is no per-Project opt-in and no grant - the bounds
    # that matter are how many one session may hold and how long one may run.
    session_watch_enabled: bool = True
    # How many watches one session may hold open at once. Sized for an
    # orchestrator fanning out to a handful of workers, not for a fleet-wide
    # sweep: a session that wants to watch everything should be reading
    # `list_sessions` instead, which costs one call rather than N notices.
    session_watch_max_per_session: int = 8
    # The ceiling on one watch's timeout. Watches live in daemon memory, so a
    # window longer than a working session is a promise this service cannot keep
    # across the restarts that routinely happen inside it.
    session_watch_max_minutes: int = 240
    # Scheduled runs. The install-wide master switch is here rather than
    # per-Project because it is the emergency stop: off means no schedule fires
    # anywhere, whatever any Project opted into. The caps are global for the same
    # reason spend limits are - what a scheduled fleet may cost this machine is
    # not a per-repository decision.
    scheduled_runs_enabled: bool = True
    # How many schedule-started sessions may be alive at once. Unattended agents
    # that accumulate are the failure mode this bounds: nothing ends an agent
    # session automatically, so without a ceiling a nightly job on five Projects
    # is five forgotten panes a week later.
    scheduled_runs_max_concurrent: int = 3
    # Sweep cadence. Schedules resolve to the minute, so this only decides how
    # promptly a due minute is noticed.
    scheduled_runs_poll_seconds: float = 5.0
    # How long the run history behind each schedule is kept. Long enough that
    # "has this been failing all week" is answerable.
    scheduled_run_retention_days: int = 60
    # Phase 14 land queue. The install-wide master switch is here for the same reason
    # the scheduled-runs one is: it is the emergency stop, and off means no branch
    # lands anywhere whatever any Project opted into.
    land_queue_enabled: bool = True
    # How many land requests one origin session may make per hour on the granted
    # path. A land costs wall-clock rather than tokens, so this bounds a runaway
    # request loop rather than spend.
    land_hourly_budget: int = 12
    # How long a request holds for a busy worktree before it gives up and hands back.
    # Long enough that an ordinary agent turn finishes inside it, short enough that a
    # request against an abandoned worktree does not sit in the queue forever.
    land_hold_timeout_seconds: float = 30 * 60.0
    # Whether a failed verification is retried once. Off by default and never silent:
    # a flaky gate that loops is worse than one that stops, and a retry that fails
    # differently from the first attempt stops rather than retrying again.
    land_retry_verification: bool = False
    # How long a green gate verdict stands for the exact (git tree, command digest) it
    # was observed on, so a verify-only run and the land that follows it do not spend
    # the same minutes twice. Bounded rather than forever because a tree hash is a claim
    # about *content* and the machine underneath it drifts - an installed dependency, a
    # toolchain, an OS update - none of which changes the tree. Zero disables reuse.
    land_verify_memo_seconds: float = 24 * 3600.0
    automation_concurrency: int = 2
    automation_queue_size: int = 256
    automation_max_input_tokens: int = 4096
    # The one per-call output ceiling every automation runs under, including the
    # scan timeline and attention narration since their dedicated ceilings were
    # retired (schema 34 lifts an older config to the loosest of the three, so
    # no feature that fit before stops fitting). Sized for the largest known
    # consumer: the scan schema permits ~2,600 characters of prose across five
    # fields, and a truncated strict JSON body is an unparseable response that
    # costs a record.
    automation_max_output_tokens: int = 1000
    # These are the shared ceilings over *every* automation. They were sized for
    # episodic observers that fire once per session, and a continuous sampler
    # (scan timeline) exhausted the per-rule token cap after ten calls costing
    # under half a cent. The token axis must never be the binding constraint
    # while the dollar axis sits at 0.2% - the dollar figures are the ones that
    # describe real cost, so the token caps are now headroom rather than policy.
    # Both axes are enforced (`either`), which is what the two scalar settings
    # these replaced did together; see `BUDGET_SPECS`.
    automation_daily_budget: Budget = field(
        default_factory=lambda: BUDGET_FIELDS["automation_daily_budget"].default
    )
    automation_rule_daily_budget: Budget = field(
        default_factory=lambda: BUDGET_FIELDS["automation_rule_daily_budget"].default
    )
    automation_hourly_call_cap: int = 1_200
    automation_rule_hourly_call_cap: int = 600
    # Control-plane project card (CP §5.4). Per-project opt-in gates whether it
    # runs at all; these bound what one build may cost. Empty model falls back
    # to the automation cheap model; with neither set there is simply no card.
    project_card_model: str = ""
    project_card_daily_budget: Budget = field(
        default_factory=lambda: BUDGET_FIELDS["project_card_daily_budget"].default
    )
    project_card_max_input_tokens: int = 6000
    project_card_max_output_tokens: int = 600
    # Phase 5.5 semantic timeline. The global switch is an emergency/master
    # boundary, while Project permission and the current-run toggle are checked
    # separately. The undated OpenRouter id follows the provider's latest V4
    # Flash revision without silently changing model family.
    scan_timeline_enabled: bool = False
    scan_timeline_model: str = "deepseek/deepseek-v4-flash"
    # The scan timeline spends under the global automation ceilings
    # (`automation_daily_budget`, `automation_hourly_call_cap`,
    # `automation_max_output_tokens`). It once carried its own daily budget,
    # per-conversation budget, hourly cap, and output ceiling; those were
    # retired in favour of one set of global bounds, and a config still naming
    # them loads with the retired keys dropped. It stays exempt from the
    # `automation_rule_*` per-rule caps: it samples continuously, and charging
    # it to the same envelope as a rule that fires once starves the rules.
    # Phase 6.5 attention ranking. The daily budget is the hard bound on how many
    # times ranking may decide something is worth interrupting for; the hourly cap
    # is only a burst limiter beneath it. Cheap-blocking work (a permission
    # prompt) never spends either. Ranked items surface in-app and are never
    # routed to web push, which is why no push setting appears here.
    attention_daily_interrupt_budget: int = 4
    attention_hourly_interrupt_cap: int = 2
    # Findings about one underlying event inside this window are one incident and
    # spend one slot between them.
    attention_incident_window_seconds: float = 3600.0
    # OSC 133 shell-integration markers in the user's own shells, which is how a
    # next-breakpoint item learns the human just finished something.
    attention_breakpoint_markers: bool = True
    # Narration is the one model-cost part of the phase, off until asked for.
    # It spends under the global automation ceilings; its dedicated daily
    # budget and output ceiling were retired with the scan timeline's.
    attention_narration_enabled: bool = False
    attention_narration_model: str = ""
    openrouter_cheap_model: str = ""
    openrouter_standard_model: str = ""
    openrouter_request_timeout_seconds: float = 30.0
    # Phase 15 bring-your-own endpoint. STT and TTS already run on this machine;
    # this is the language model catching up. `openrouter` is the default and is
    # what every existing install keeps without touching anything.
    #
    # `custom` is one OpenAI-compatible `/chat/completions` - llama.cpp, Ollama,
    # vLLM, and LM Studio all present that shape - with the key in the secret
    # store rather than here.
    #
    # `custom_llm_model` is the single model an endpoint with no catalog serves,
    # and is used only then: once a catalog is proven there is something to choose
    # between, so every other model setting in this file means what it says and
    # this one is ignored. Blank is therefore legal and `readiness` decides whether
    # it was needed, because that answer depends on a measurement `_validate`
    # cannot reach. See `llm_endpoint.py`.
    #
    # `custom_llm_catalog_url` is where that catalog lives, when it is not the
    # `/models` beside the chat route. Blank derives it, which is right for every
    # OpenAI-compatible server and for the gateway. It exists because a catalog is
    # not always served by the thing serving completions - a proxy may publish one
    # elsewhere, and an operator may want to point at a hand-written document that
    # names and prices the models their server actually loads. It is an operator's
    # assertion about their own install rather than something inferred, which is
    # also why it is install configuration and never a request parameter.
    llm_provider: str = "openrouter"
    custom_llm_base_url: str = ""
    custom_llm_model: str = ""
    custom_llm_catalog_url: str = ""
    # Phase 10.6 Mux assistant: the conversational operator behind the voice
    # grammar's tier-3 fallback and the workspace chat surface. Off by default
    # like every model-cost feature; the model slot is configurable with a
    # verified tool-calling default, and spend is capped by its own daily
    # budget under the `builtin:assistant` ledger rule.
    assistant_enabled: bool = False
    assistant_model: str = "openai/gpt-5.6-terra"
    assistant_daily_budget: Budget = field(
        default_factory=lambda: BUDGET_FIELDS["assistant_daily_budget"].default
    )
    assistant_max_output_tokens: int = 700
    # How many stored dialog messages one turn's prompt may carry. The fleet
    # snapshot and command catalog ride every turn regardless; this bounds only
    # conversational memory so a days-old dialog cannot grow the prompt without
    # limit.
    assistant_context_messages: int = 30
    # Trust for the reversible action class (queue a message, append to a note,
    # spawn a session): `auto` executes silently, `cancel_window` announces and
    # waits a short grace period, `confirm` requires an explicit confirmation.
    # The consequential class (send-now, interrupt, end session) always
    # confirms and is deliberately not configurable below that.
    assistant_trust_reversible: str = "cancel_window"
    # Stream the model's reply token by token so the first sentence can be spoken
    # while the rest is still generating. Off means the turn is buffered whole,
    # which is the pre-streaming behaviour and the escape hatch if a configured
    # model's provider streams tool calls badly; correctness does not depend on
    # it either way, only time-to-first-word.
    assistant_stream_replies: bool = True
    observer_titler_enabled: bool = False
    # The one switch behind the three attention observers (stalled-run triage,
    # approval-request triage, context-handoff suggestion), which is why the
    # dashboard enables or disables them as a group.
    #
    # It was named `phase7_observers_enabled` until schema 31. The turn
    # summarizer it once sat beside was retired earlier; the scan timeline is
    # the single behavioral-summary producer, and a config predating that
    # removal still carries `observer_summarizer_enabled`. `load_config` copies
    # only known dataclass fields, so a stale key is dropped on load and on the
    # next write rather than erroring - which is exactly why the rename needs
    # the explicit schema-31 migration below and could not be a bare rename.
    attention_observers_enabled: bool = False
    tts_enabled: bool = False
    tts_default_mode: str = "off"
    tts_content: str = "summary"
    # The OS voice is the default because it speaks with no download and no
    # network call; `kokoro` is the bundled quality tier. `edge` is an explicit
    # external integration: the frozen app never carries the LGPL package, and
    # synthesis is refused until the operator acknowledges the online-service
    # disclosure. A pre-schema-26 `edge` config still migrates to `sapi`; schema
    # 33 does not silently reconnect an install that was moved offline.
    tts_engine: str = "sapi"
    # Kokoro-82M through onnxruntime. The voice must be one of the downloaded
    # English voice vectors; existence is enforced at synthesis time so config
    # loading never depends on model files.
    tts_kokoro_voice: str = "af_heart"
    tts_kokoro_speed: float = 1.0
    # User respellings for words the lexicon-only Kokoro G2P cannot resolve
    # (word -> how to say it, e.g. "vaultspaces" -> "vault spaces"). Merged over
    # the built-in project lexicon in the repair ladder, so one entry stops a
    # recurring name from being spelled out letter by letter.
    tts_kokoro_lexicon: dict[str, str] = field(default_factory=dict)
    tts_sapi_voice: str = ""
    tts_sapi_rate: int = 0
    # The Python interpreter that owns the optional `edge-tts` installation.
    # Blank means this interpreter in source mode; a frozen build requires an
    # explicit external interpreter because LGPL code is never bundled.
    tts_edge_python: str = ""
    tts_edge_voice: str = "en-US-JennyNeural"
    tts_edge_rate_percent: int = 0
    tts_edge_volume_percent: int = 0
    tts_edge_pitch_hz: int = 0
    # Versioned acknowledgement of the disclosure rendered in Settings. This is
    # awareness, not a claim that Microsoft has authorized the integration.
    tts_edge_risk_ack_version: int = 0
    tts_summary_model: str = ""
    tts_summary_max_tokens: int = 500
    tts_verbatim_max_chars: int = 6000
    tts_daily_budget: Budget = field(
        default_factory=lambda: BUDGET_FIELDS["tts_daily_budget"].default
    )
    tts_cache_mb: int = 200
    # Off by default so a fresh install never downloads the multi-hundred-MB
    # Whisper model and the Silero VAD assets on the first Talk without warning.
    # Enabling it in Settings -> Voice is the explicit opt-in; the Voice tab states
    # that the first capture downloads a speech model.
    stt_enabled: bool = False
    stt_engine: str = "whisper"
    stt_language: str = "en-US"
    stt_whisper_model: str = "turbo"
    # The routing decoder, used for the speculative pass that only has to recognize a
    # wake word and a command phrase. Small and English-only because that pass is a
    # reflex path; blank falls back to the dictation model.
    stt_routing_model: str = "small.en"
    voice_wake_words: list[str] = field(default_factory=lambda: list(DEFAULT_VOICE_WAKE_WORDS))
    voice_commands: list[dict[str, Any]] = field(default_factory=default_voice_commands)
    # Extra trailing-silence patience before a plain chat-mode utterance becomes an
    # assistant turn, so thinking out loud is not answered at every pause. Applied
    # only while the assistant is the microphone's addressee; wake-worded commands
    # keep their speed through the speculative short-circuit. 0 keeps the command
    # tail (352 ms on Silero).
    voice_chat_patience_ms: int = 1200
    # Resolved from the real home directory with no environment override, so every
    # process on this machine shares one data dir. Tests must inject an explicit path
    # (see load_config) rather than relying on isolation; two suites running at once
    # from different git worktrees will otherwise contend over the same mux.db.
    data_dir: Path = field(default_factory=default_data_dir)
    config_path: Path | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        # `Config(**asdict(other))` is how `update_config` builds a candidate and
        # how several tests build a variant, and `asdict` flattens a nested
        # dataclass to a plain dict. Coercing here means a `Budget` field is a
        # `Budget` however the instance was constructed, so enforcement never has
        # to ask whether it is holding the shape or the mapping.
        for name, spec in BUDGET_FIELDS.items():
            setattr(self, name, coerce_budget(getattr(self, name), fallback=spec.default))
        self.scrub_registry_maps()

    def scrub_registry_maps(self) -> None:
        """Drop map entries naming something this build's registries do not have.

        Three maps key off a registry that can retire an id: the automation
        ceiling and the two agent-authority maps. A config file that outlived
        one of those retirements must still *load* - the alternative is a daemon
        that will not start because a setting mentions a feature that no longer
        exists - while a typo written over the API must fail loudly rather than
        silently allow. That asymmetry is what this method plus `_validate` buy
        between them, and it only works if both callers run:

        - `__post_init__`, which covers `update_config`'s candidate construction.
        - `load_config`, explicitly, because it `setattr`s every stored value
          onto an already-constructed instance and so never re-enters
          `__post_init__` at all. That second call was missing until 2026-08-29,
          which made this method's promise false for `automation_global_allow`
          for as long as it had existed: a retired automation id in a stored
          config raised `unknown automations` out of `_validate` and refused to
          start. Measured before it was fixed, not assumed.

        Deliberately not a `_validate` concern: validation reports what a caller
        got wrong, and a build that retired an id is not something the operator
        got wrong.
        """
        from . import automation_registry as _registry
        from .agent_authority import AUTHORITY_FIELDS as _authority

        if isinstance(self.automation_global_allow, dict):
            self.automation_global_allow = {
                name: flag
                for name, flag in self.automation_global_allow.items()
                if isinstance(name, str)
                and isinstance(flag, bool)
                and name in _registry.REGISTRY
                and name not in _registry.DEDICATED_INSTALL_SWITCHES
            }
        for map_name in ("agent_authority_default", "agent_authority_ceiling"):
            current = getattr(self, map_name)
            if not isinstance(current, dict):
                continue
            setattr(
                self,
                map_name,
                {
                    name: level
                    for name, level in current.items()
                    if isinstance(name, str)
                    and isinstance(level, str)
                    and name in _authority
                    and _authority[name].rank(level) >= 0
                },
            )

    @property
    def database_path(self) -> Path:
        return self.data_dir / "mux.db"

    @property
    def resolved_worktree_root(self) -> Path:
        configured = self.worktree_root.strip()
        root = Path(configured).expanduser() if configured else self.data_dir / "worktrees"
        return root.resolve()

    def public_dict(self) -> dict[str, object]:
        result = asdict(self)
        result["data_dir"] = str(self.data_dir)
        result["worktree_root"] = str(self.resolved_worktree_root)
        result.pop("config_path", None)
        # Legacy input is still accepted so existing config files start cleanly,
        # but layout v6 has no dock/pop-out presentation preference.
        result.pop("notes_default_open", None)
        result["access_mode"] = "local+tailnet" if self.tailnet_enabled else "loopback"
        result["requires_auth"] = False
        # The daemon retains exact bytes. Browsers retain an approximate line
        # window using a documented 160-byte average, bounded for xterm.
        result["xterm_scrollback_lines"] = max(1_000, min(100_000, self.scrollback_bytes // 160))
        result["pty_windows"] = windows_pty_compatibility()
        return result


def windows_pty_compatibility() -> dict[str, object] | None:
    """Describe the host PTY to xterm.js, or None when it is not a ConPTY.

    xterm's ``windowsPty`` option gates workarounds it cannot infer on its own:
    below ConPTY build 21376 it must disable reflow (ConPTY hard-wraps and never
    reports the wrap flag, so rewrapping a resized pane corrupts scrollback) and
    treat a line whose last cell is non-blank as wrapped. On every ConPTY build
    it also has to grow the viewport with blank rows instead of pulling
    scrollback back down, because ConPTY reprints its own view of the screen.
    Sent to the browser because only the daemon knows what the PTY really is.
    """
    if sys.platform != "win32":
        return None
    return {"backend": "conpty", "build_number": sys.getwindowsversion().build}


# Backstop ceilings that bind while `agent_message_limits_enabled` is off. They are
# deliberately far above the configurable defaults - an orchestrator relaying work
# across a fleet for hours must not trip one - and deliberately finite, because the
# thread budget is the only brake that ends two agents talking forever: a reply
# refreshes the peer's auto-delivery grant, so a two-party exchange renews itself
# and nothing else in the pipeline stops it. Constants rather than config fields:
# a backstop someone can raise is a limit, and the limits are the other mode.
UNLIMITED_MESSAGE_HOURLY_BUDGET = 500
UNLIMITED_MESSAGE_PENDING_PER_TARGET = 50
UNLIMITED_MESSAGE_CHAIN_DEPTH = 10
UNLIMITED_MESSAGE_THREAD_TURNS = 1000
UNLIMITED_INTERJECT_HOURLY_BUDGET = 120
# A floor rather than a ceiling, so its backstop is *lower*: enough spacing that
# two interjects cannot land inside one turn's paste-settle, and no more.
UNLIMITED_INTERJECT_MIN_INTERVAL_SECONDS = 5.0


@dataclass(frozen=True, slots=True)
class AgentMessageBounds:
    """The rate bounds agent messaging actually enforces right now.

    Resolved through `agent_message_bounds()` by everything that reads one of the
    six rate bounds (`agent_messaging.py` staging, `auto_delivery.py`'s reply
    window), so the manual and automatic paths can never disagree about which
    mode the install is in. `limits_enabled` rides along so a refusal can name
    the configured setting when it bound and the fixed ceiling when that did.
    """

    limits_enabled: bool
    hourly_budget: int
    pending_per_target: int
    max_chain_depth: int
    max_thread_turns: int
    interject_hourly_budget: int
    interject_min_interval_seconds: float


def agent_message_bounds(config: Config) -> AgentMessageBounds:
    """The effective messaging rate bounds: configured values, or the backstops.

    `agent_message_max_chars`, ring detection, message expiry, and the kill
    switches are not here on purpose - they are not rate limits and bind in both
    modes.
    """
    if config.agent_message_limits_enabled:
        return AgentMessageBounds(
            limits_enabled=True,
            hourly_budget=int(config.agent_message_hourly_budget),
            pending_per_target=int(config.agent_message_pending_per_target),
            max_chain_depth=int(config.agent_message_max_chain_depth),
            max_thread_turns=int(config.agent_message_max_thread_turns),
            interject_hourly_budget=int(config.agent_interject_hourly_budget),
            interject_min_interval_seconds=float(
                config.agent_interject_min_interval_seconds
            ),
        )
    return AgentMessageBounds(
        limits_enabled=False,
        hourly_budget=UNLIMITED_MESSAGE_HOURLY_BUDGET,
        pending_per_target=UNLIMITED_MESSAGE_PENDING_PER_TARGET,
        max_chain_depth=UNLIMITED_MESSAGE_CHAIN_DEPTH,
        max_thread_turns=UNLIMITED_MESSAGE_THREAD_TURNS,
        interject_hourly_budget=UNLIMITED_INTERJECT_HOURLY_BUDGET,
        interject_min_interval_seconds=UNLIMITED_INTERJECT_MIN_INTERVAL_SECONDS,
    )


def _validate_project_init_scripts(config: Config, errors: dict[str, str]) -> None:
    """Validate the user-authored commands offered when a Project is registered.

    These are machine-local and typed by the user in Settings, never imported from a
    repository, so the only thing enforced here is shape: a stable id to select by, a
    label to show, and a command string. What the command does is the user's business.
    """
    scripts = config.project_init_scripts
    if not isinstance(scripts, list) or len(scripts) > MAX_PROJECT_INIT_SCRIPTS:
        errors["project_init_scripts"] = (
            f"must be an array of at most {MAX_PROJECT_INIT_SCRIPTS} init scripts"
        )
        return
    seen: set[str] = set()
    for index, script in enumerate(scripts):
        prefix = f"project_init_scripts.{index}"
        if not isinstance(script, dict):
            errors[prefix] = "must be an object with id, label, and command"
            continue
        unknown = set(script) - {"id", "label", "command", "default_enabled"}
        if unknown:
            errors[prefix] = f"unknown keys: {', '.join(sorted(unknown))}"
        identifier = script.get("id")
        if not isinstance(identifier, str) or not INIT_SCRIPT_ID.fullmatch(identifier):
            errors[f"{prefix}.id"] = (
                "must be 1–64 characters of letters, digits, hyphen, or underscore"
            )
        elif identifier in seen:
            errors[f"{prefix}.id"] = f"duplicate init script id {identifier}"
        else:
            seen.add(identifier)
        label = script.get("label")
        if not isinstance(label, str) or not label.strip() or len(label) > 80:
            errors[f"{prefix}.label"] = "must be a non-empty name of 80 characters or fewer"
        command = script.get("command")
        if not isinstance(command, str) or not command.strip() or len(command) > 4000:
            errors[f"{prefix}.command"] = "must be a non-empty command of 4000 characters or fewer"
        if not isinstance(script.get("default_enabled", False), bool):
            errors[f"{prefix}.default_enabled"] = "must be a boolean"


# ---------------------------------------------------------------------------- #
# Validation tables
#
# `_validate` below is one deliberate choke point (see its own comment), and that
# is the right shape: one place answers "is this settings payload legal", so no
# surface can accept a value another surface would refuse. What it should not be
# is one hundred and seventy hand-written branches, which is what it had grown to
# and what made C901 read it as the worst function in the codebase by a factor of
# two (`.docs/development/CODE_QUALITY_AUDIT_2026-08-23.md` finding 27).
#
# So the three mechanical families - a numeric range, a fixed set of spellings, a
# bounded string - are declared as data and checked by one loop each. Everything
# that is not mechanical stays written out in `_validate`, because a rule with a
# reason attached is worth reading.
#
# The messages here are the exact strings the previous branches produced, byte
# for byte: they are asserted on by tests and shown in the settings UI, so this
# is a restatement of the same rules, not a redefinition of them.
# ---------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class _Range:
    """An inclusive numeric range, with the message for a value outside it."""

    field: str
    low: float
    high: float
    message: str
    #: 0 means "off" or "inherit the default" for this field and skips the range.
    zero_disables: bool = False


@dataclass(frozen=True, slots=True)
class _Choice:
    """A field whose value must be one of a fixed set of spellings."""

    field: str
    #: Membership is all this needs, so an existing module constant can be named
    #: directly rather than copied into a tuple that could then drift from it.
    allowed: Collection[str]
    message: str


@dataclass(frozen=True, slots=True)
class _Text:
    """A string field bounded by length, and optionally required to be non-empty."""

    field: str
    max_chars: int
    message: str
    required: bool = False


@dataclass(frozen=True, slots=True)
class _Pattern:
    """A string field that must match a pattern whole."""

    field: str
    pattern: re.Pattern[str]
    message: str


_RANGE_RULES: tuple[_Range, ...] = (
    _Range("port", 1, 65535, "must be between 1 and 65535"),
    _Range(
        "note_font_size_px",
        8,
        48,
        "must be 0 (editor default) or between 8 and 48",
        zero_disables=True,
    ),
    _Range(
        "note_line_height",
        1.0,
        3.0,
        "must be 0 (editor default) or between 1.0 and 3.0",
        zero_disables=True,
    ),
    _Range(
        "note_rail_button_size_px",
        32,
        96,
        "must be 0 (editor default) or between 32 and 96",
        zero_disables=True,
    ),
    _Range("mobile_scroll_sensitivity", 0.25, 4, "must be between 0.25 and 4"),
    _Range("ccusage_refresh_minutes", 0, 24 * 60, "must be between 0 and 1440 minutes"),
    _Range("scrollback_bytes", 1024, 1024 * 1024 * 1024, "must be between 1 KiB and 1 GiB"),
    _Range("attach_replay_bytes", 1024, 1024 * 1024 * 1024, "must be between 1 KiB and 1 GiB"),
    _Range(
        "session_recovery_checkpoint_bytes",
        0,
        64 * 1024 * 1024,
        "must be between 0 and 64 MiB",
    ),
    _Range("session_recovery_retention_days", 0, 365, "must be between 0 and 365 days"),
    _Range("session_recovery_max_sessions", 0, 1000, "must be between 0 and 1000 sessions"),
    _Range("git_poll_seconds", 0.25, 3600, "must be between 0.25 and 3600 seconds"),
    _Range("process_poll_seconds", 0.5, 60, "must be between 0.5 and 60 seconds"),
    _Range("process_orphan_grace_seconds", 1, 3600, "must be between 1 and 3600 seconds"),
    _Range("ghost_window_poll_seconds", 0.5, 60, "must be between 0.5 and 60 seconds"),
    _Range("process_evidence_retention_days", 1, 3650, "must be between 1 and 3650"),
    _Range("operational_telemetry_retention_days", 1, 3650, "must be between 1 and 3650"),
    _Range("status_timeline_retention_days", 1, 3650, "must be between 1 and 3650"),
    _Range("provider_quota_poll_minutes", 5, 1440, "must be between 5 and 1440"),
    _Range("provider_quota_turn_refresh_min_minutes", 1, 1440, "must be between 1 and 1440"),
    _Range("history_limit", 1, 10000, "must be between 1 and 10000"),
    _Range("clipboard_history_limit", 1, 2000, "must be between 1 and 2000 entries"),
    _Range(
        "clipboard_history_entry_max_chars",
        256,
        1000000,
        "must be between 256 and 1000000 characters",
    ),
    _Range(
        "clipboard_history_retention_hours",
        0,
        8760,
        "must be between 0 (keep until evicted) and 8760 hours",
    ),
    _Range("automation_retention_days", 1, 3650, "must be between 1 and 3650"),
    _Range("prompt_queue_retention_days", 1, 3650, "must be between 1 and 3650"),
    # Auto-delivery bounds. The lower bounds are the point: a zero-length
    # stability window or an unbounded grant would defeat the gate they exist
    # to be (`ROADMAP.md` Phase 5).
    _Range("auto_delivery_stable_seconds", 2, 600, "must be between 2 and 600 seconds"),
    _Range("auto_delivery_max_consecutive", 1, 50, "must be between 1 and 50 sends"),
    _Range("auto_delivery_session_ttl_minutes", 1, 1440, "must be between 1 and 1440 minutes"),
    _Range("auto_delivery_refusal_backoff_seconds", 0, 3600, "must be between 0 and 3600 seconds"),
    # 0 is legal here and nowhere else in this group: the reply window is the one
    # bound that only ever *holds off* another bound, so switching it off is a
    # narrowing rather than the unbounded grant the others must refuse.
    _Range("auto_delivery_reply_window_minutes", 0, 1440, "must be between 0 and 1440 minutes"),
    # Approval bounds. Every lower bound here is the point: an unbounded grant or
    # an unbounded answer count is standing authority, which is the one thing
    # this feature must not become.
    _Range("approval_grant_ttl_minutes", 1, 480, "must be between 1 and 480 minutes"),
    _Range("approval_max_auto_per_grant", 1, 5000, "must be between 1 and 5000 requests"),
    _Range("approval_hook_timeout_seconds", 1, 60, "must be between 1 and 60 seconds"),
    _Range("approval_keystroke_window_seconds", 1, 300, "must be between 1 and 300 seconds"),
    _Range("agent_message_max_chars", 1, 100000, "must be between 1 and 100000 characters"),
    _Range("agent_message_hourly_budget", 0, 1000, "must be between 0 and 1000 messages per hour"),
    _Range("agent_message_pending_per_target", 1, 100, "must be between 1 and 100 messages"),
    _Range("agent_message_max_chain_depth", 1, 10, "must be between 1 and 10 hops"),
    _Range("agent_message_max_thread_turns", 1, 100, "must be between 1 and 100 messages"),
    _Range(
        "agent_interject_hourly_budget",
        0,
        1000,
        "must be between 0 and 1000 mid-turn deliveries per hour",
    ),
    _Range("agent_interject_min_interval_seconds", 0, 3600, "must be between 0 and 3600 seconds"),
    _Range("session_control_hourly_budget", 0, 1000, "must be between 0 and 1000 actions per hour"),
    _Range("session_control_graceful_timeout_s", 1, 120, "must be between 1 and 120 seconds"),
    _Range("agent_spawn_hourly_budget", 0, 1000, "must be between 0 and 1000 spawns per hour"),
    _Range("session_watch_max_per_session", 1, 100, "must be between 1 and 100 watches"),
    _Range("session_watch_max_minutes", 1, 24 * 60, "must be between 1 minute and 24 hours"),
    _Range("scheduled_runs_max_concurrent", 0, 50, "must be between 0 and 50 sessions"),
    _Range("land_hourly_budget", 0, 1000, "must be between 0 and 1000 land requests per hour"),
    _Range("land_hold_timeout_seconds", 60, 24 * 3600, "must be between 60 seconds and 24 hours"),
    _Range("land_verify_memo_seconds", 0, 7 * 24 * 3600, "must be between 0 (no reuse) and 7 days"),
    _Range("scheduled_runs_poll_seconds", 1, 300, "must be between 1 and 300 seconds"),
    _Range("scheduled_run_retention_days", 1, 3650, "must be between 1 and 3650 days"),
    _Range("automation_concurrency", 1, 16, "must be between 1 and 16"),
    _Range("automation_queue_size", 16, 4096, "must be between 16 and 4096"),
    _Range("automation_max_input_tokens", 128, 128000, "must be between 128 and 128000"),
    _Range("automation_max_output_tokens", 16, 8192, "must be between 16 and 8192"),
    _Range("automation_hourly_call_cap", 1, 10000, "must be between 1 and 10000"),
    _Range("automation_rule_hourly_call_cap", 1, 10000, "must be between 1 and 10000"),
    _Range("project_card_max_input_tokens", 512, 128000, "must be between 512 and 128000"),
    _Range("project_card_max_output_tokens", 128, 4096, "must be between 128 and 4096"),
    _Range("attention_daily_interrupt_budget", 0, 100, "must be between 0 and 100"),
    _Range("attention_hourly_interrupt_cap", 0, 100, "must be between 0 and 100"),
    _Range("attention_incident_window_seconds", 60, 86400, "must be between 60 and 86400"),
    _Range("openrouter_request_timeout_seconds", 1, 120, "must be between 1 and 120"),
    _Range("assistant_max_output_tokens", 128, 8192, "must be between 128 and 8192"),
    _Range("assistant_context_messages", 2, 200, "must be between 2 and 200"),
    _Range("tts_kokoro_speed", 0.5, 2.0, "must be between 0.5 and 2.0"),
    _Range("tts_sapi_rate", -10, 10, "must be between -10 and 10"),
    _Range("tts_edge_rate_percent", -100, 100, "must be between -100 and 100"),
    _Range("tts_edge_volume_percent", -100, 100, "must be between -100 and 100"),
    _Range("tts_edge_pitch_hz", -100, 100, "must be between -100 and 100"),
    _Range("tts_edge_risk_ack_version", 0, 1, "must be 0 or 1"),
    _Range("tts_summary_max_tokens", 64, 2000, "must be between 64 and 2000"),
    _Range("tts_verbatim_max_chars", 200, 40000, "must be between 200 and 40000"),
    _Range("tts_cache_mb", 10, 5000, "must be between 10 and 5000"),
    _Range("voice_chat_patience_ms", 0, 5000, "must be between 0 and 5000 milliseconds"),
)

_CHOICE_RULES: tuple[_Choice, ...] = (
    _Choice(
        "host",
        LOOPBACK_HOSTS,
        "must be a loopback address (127.0.0.1, localhost, or ::1); "
        "direct tailnet listening uses the detected Tailscale address automatically",
    ),
    _Choice("terminal_renderer", ("auto", "dom", "webgl"), "must be auto, dom, or webgl"),
    _Choice("notes_default_open", ("dock", "popout"), "must be dock or popout"),
    _Choice("note_syntax", ("markdown", "plain"), "must be markdown or plain"),
    _Choice("note_tab_behavior", ("indent", "focus"), "must be indent or focus"),
    _Choice(
        "note_shortcut_policy",
        ("browser-safe", "editor-first", "none"),
        "must be browser-safe, editor-first, or none",
    ),
    _Choice("note_command_rail", ("auto", "on", "off"), "must be auto, on, or off"),
    _Choice(
        "mobile_vertical_drag",
        ("smart", "terminal", "application", "disabled"),
        "must be smart, terminal, application, or disabled",
    ),
    _Choice("mobile_scroll_direction", ("natural", "wheel"), "must be natural or wheel"),
    _Choice("mobile_long_press", ("context_menu", "disabled"), "must be context_menu or disabled"),
    _Choice(
        "assistant_trust_reversible",
        ("auto", "cancel_window", "confirm"),
        "must be auto, cancel_window, or confirm",
    ),
    _Choice("tts_default_mode", ("off", "on_demand", "auto"), "must be off, on_demand, or auto"),
    _Choice("tts_content", ("summary", "verbatim"), "must be summary or verbatim"),
    _Choice(
        "tts_engine",
        ("sapi", "kokoro", "edge"),
        "must be sapi (the OS voice), kokoro, or edge",
    ),
    _Choice("stt_engine", ("sapi", "whisper"), "must be sapi or whisper"),
    _Choice("drawer_tab_display", ("icon", "title"), "must be icon or title"),
    _Choice("utility_rail_display", ("icon", "title"), "must be icon or title"),
    # These three build their message from the allowed set, exactly as the branches
    # they replace did, so a new theme or density is named by the error without an
    # edit here.
    _Choice("theme", tuple(sorted(THEMES)), f"must be one of {', '.join(sorted(THEMES))}"),
    _Choice(
        "rail_density_desktop",
        tuple(RAIL_DENSITIES),
        f"must be one of {', '.join(RAIL_DENSITIES)}",
    ),
    _Choice(
        "rail_density_mobile",
        tuple(RAIL_DENSITIES),
        f"must be one of {', '.join(RAIL_DENSITIES)}",
    ),
)

_TEXT_RULES: tuple[_Text, ...] = (
    _Text("note_font_family", 200, "must be 200 characters or fewer"),
    _Text("scan_timeline_model", 200, "must be an exact OpenRouter model id", required=True),
    _Text("assistant_model", 200, "must be an exact OpenRouter model id", required=True),
    _Text(
        "stt_whisper_model",
        120,
        "must name a Whisper model in 120 characters or fewer",
        required=True,
    ),
    _Text(
        "stt_routing_model",
        120,
        "must name a Whisper model in 120 characters or fewer, or be blank",
    ),
    _Text(
        "tts_edge_python",
        1000,
        "must name an external Python interpreter in 1000 characters or fewer, or be blank",
    ),
    _Text(
        "tts_edge_voice",
        160,
        "must name an Edge voice in 160 characters or fewer",
        required=True,
    ),
)

_PATTERN_RULES: tuple[_Pattern, ...] = (
    _Pattern(
        "tts_kokoro_voice",
        re.compile(r"[a-z]{2}_[a-z]+"),
        "must be a Kokoro voice id such as af_heart",
    ),
    _Pattern(
        "stt_language",
        re.compile(r"[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})?"),
        "must be a language tag such as en-US",
    ),
)


def _validate(config: Config) -> None:
    errors: dict[str, str] = {}
    # Every spending cap validates through one implementation against the bounds
    # declared beside it, so a new budget cannot ship without a range and an
    # existing one cannot drift from the range its surface advertises.
    for spec in BUDGET_SPECS:
        validate_budget(
            coerce_budget(getattr(config, spec.field), fallback=spec.default),
            field=spec.field,
            errors=errors,
            max_tokens=spec.max_tokens,
            max_usd=spec.max_usd,
            min_tokens=spec.min_tokens,
            min_usd=spec.min_usd,
        )
    # The mechanical families, declared as data above. A field appears in exactly
    # one table and in no hand-written check below, so neither can shadow the other.
    for range_rule in _RANGE_RULES:
        range_value = getattr(config, range_rule.field)
        if range_rule.zero_disables and range_value == 0:
            continue
        if not range_rule.low <= range_value <= range_rule.high:
            errors[range_rule.field] = range_rule.message
    for choice_rule in _CHOICE_RULES:
        if getattr(config, choice_rule.field) not in choice_rule.allowed:
            errors[choice_rule.field] = choice_rule.message
    for text_rule in _TEXT_RULES:
        text_value = str(getattr(config, text_rule.field))
        if len(text_value) > text_rule.max_chars or (text_rule.required and not text_value.strip()):
            errors[text_rule.field] = text_rule.message
    for pattern_rule in _PATTERN_RULES:
        if not pattern_rule.pattern.fullmatch(str(getattr(config, pattern_rule.field))):
            errors[pattern_rule.field] = pattern_rule.message
    if config.default_backend != "shell" and not is_agent_harness(config.default_backend):
        errors["default_backend"] = "must be shell or a registered agent"
    if config.default_harness and not is_agent_harness(config.default_harness):
        # No `shell` escape hatch here, unlike `default_backend`: this field
        # exists precisely to answer "which agent", and accepting `shell` would
        # let it be set to a value that can never satisfy the callers that read it.
        errors["default_harness"] = "must be empty or a registered agent"
    # `bool` is an `int` subclass and `True` would otherwise validate as a 1-column cap.
    if (
        not isinstance(config.claude_max_columns, int)
        or isinstance(config.claude_max_columns, bool)
        or config.claude_max_columns not in CLAUDE_MAX_COLUMNS
    ):
        allowed = ", ".join(str(step) for step in sorted(CLAUDE_MAX_COLUMNS))
        errors["claude_max_columns"] = f"must be one of {allowed} (0 removes the cap)"
    if str(config.log_level).strip().upper() not in {"DEBUG", "INFO", "WARNING", "ERROR"}:
        errors["log_level"] = "must be DEBUG, INFO, WARNING, or ERROR"
    if not isinstance(config.note_shortcut_overrides, dict):
        errors["note_shortcut_overrides"] = "must be a mapping of chord to command"
    elif len(config.note_shortcut_overrides) > 128:
        errors["note_shortcut_overrides"] = "must hold at most 128 chords"
    else:
        bad_chords = sorted(
            str(chord)
            for chord, command in config.note_shortcut_overrides.items()
            if not isinstance(chord, str)
            or not NOTE_SHORTCUT_CHORD.match(chord)
            or not isinstance(command, str)
            # "" is the released-chord marker; anything else must name a command.
            or (command != "" and not NOTE_SHORTCUT_COMMAND.match(command))
        )
        if bad_chords:
            errors["note_shortcut_overrides"] = (
                'each entry must map a normalized chord to a command id or "" '
                "(release); invalid: " + ", ".join(bad_chords)
            )
    if not isinstance(config.mobile_gestures, dict):
        errors["mobile_gestures"] = "must be a mapping of gesture slots to command ids"
    else:
        unknown_slots = set(config.mobile_gestures) - set(MOBILE_GESTURE_SLOTS)
        if unknown_slots:
            errors["mobile_gestures"] = "unknown gesture slots: " + ", ".join(sorted(unknown_slots))
        else:
            bad = sorted(
                slot
                for slot, command in config.mobile_gestures.items()
                if command != "" and not is_command(command)
            )
            if bad:
                errors["mobile_gestures"] = "unknown command for gestures: " + ", ".join(bad)
    if (
        not isinstance(config.usage_command, list)
        or not config.usage_command
        or not all(isinstance(item, str) and item for item in config.usage_command)
    ):
        errors["usage_command"] = "must be a non-empty array of strings"
    for field_name in ("harness_args", "usage_commands"):
        value = getattr(config, field_name)
        if not isinstance(value, dict) or any(
            not isinstance(name, str)
            or not isinstance(command, list)
            or (field_name == "usage_commands" and not command)
            or not all(isinstance(item, str) and item for item in command)
            for name, command in value.items()
        ):
            errors[field_name] = (
                "must map source names to non-empty arrays of strings"
                if field_name == "usage_commands"
                else "must map harness names to arrays of non-empty strings"
            )
    if not isinstance(config.harness_exe, dict) or any(
        not isinstance(name, str) or not isinstance(executable, str) or not executable.strip()
        for name, executable in config.harness_exe.items()
    ):
        errors["harness_exe"] = "must map harness names to non-empty executable strings"
    for bool_map in (
        "harness_enabled",
        "harness_mcp_enabled",
        "harness_instrument_enabled",
        "harness_skill_enabled",
    ):
        value = getattr(config, bool_map)
        if not isinstance(value, dict) or any(
            not isinstance(name, str) or not isinstance(flag, bool) for name, flag in value.items()
        ):
            errors[bool_map] = "must map harness names to booleans"
    for field_name in (
        "harness_exe",
        "harness_args",
        "harness_enabled",
        "harness_mcp_enabled",
        "harness_instrument_enabled",
        "harness_skill_enabled",
    ):
        value = getattr(config, field_name)
        if isinstance(value, dict):
            unknown = set(value) - set(HARNESSES)
            if unknown:
                errors[field_name] = "unknown harnesses: " + ", ".join(sorted(unknown))
    allow_map = config.automation_global_allow
    if not isinstance(allow_map, dict) or any(
        not isinstance(name, str) or not isinstance(flag, bool) for name, flag in allow_map.items()
    ):
        errors["automation_global_allow"] = "must map automation ids to booleans"
    else:
        from . import automation_registry as _registry

        unknown_ids = set(allow_map) - set(_registry.REGISTRY)
        switched = set(allow_map) & set(_registry.DEDICATED_INSTALL_SWITCHES)
        if unknown_ids:
            errors["automation_global_allow"] = "unknown automations: " + ", ".join(
                sorted(unknown_ids)
            )
        elif switched:
            # One switch, one key: these ids are governed by their dedicated
            # install switches, and a second entry here would be a second owner.
            errors["automation_global_allow"] = "governed by dedicated switches: " + ", ".join(
                sorted(
                    f"{name} ({_registry.DEDICATED_INSTALL_SWITCHES[name]})" for name in switched
                )
            )
    from .agent_authority import AUTHORITY_FIELDS as _authority_fields

    for map_name in ("agent_authority_default", "agent_authority_ceiling"):
        authority_map = getattr(config, map_name)
        if not isinstance(authority_map, dict) or any(
            not isinstance(name, str) or not isinstance(level, str)
            for name, level in authority_map.items()
        ):
            errors[map_name] = "must map agent authority fields to levels"
            continue
        unknown_fields = sorted(set(authority_map) - set(_authority_fields))
        if unknown_fields:
            errors[map_name] = "unknown authority fields: " + ", ".join(unknown_fields)
            continue
        bad_levels = sorted(
            f"{name} ({level})"
            for name, level in authority_map.items()
            if _authority_fields[name].rank(level) < 0
        )
        if bad_levels:
            errors[map_name] = "invalid levels: " + ", ".join(bad_levels)
    if not isinstance(config.project_ignore_patterns, list) or not all(
        isinstance(item, str) for item in config.project_ignore_patterns
    ):
        errors["project_ignore_patterns"] = "must be an array of strings"
    if isinstance(config.project_ignore_patterns, list) and (
        len(config.project_ignore_patterns) > PROJECT_IGNORE_PATTERN_LIMIT
        or any(
            not isinstance(pattern, str) or not pattern.strip() or len(pattern) > 200
            for pattern in config.project_ignore_patterns
        )
    ):
        errors["project_ignore_patterns"] = (
            "must contain at most 256 non-empty patterns of 200 characters or fewer"
        )
    if config.ccusage_enabled and not config.usage_command:
        errors["usage_command"] = "must not be empty while usage analytics is enabled"
    if not isinstance(config.worktree_root, str):
        errors["worktree_root"] = "must be an absolute directory path or empty"
    elif config.worktree_root.strip():
        worktree_root = Path(config.worktree_root.strip()).expanduser()
        if not worktree_root.is_absolute():
            errors["worktree_root"] = "must be an absolute directory path or empty"
        elif worktree_root.parent == worktree_root:
            errors["worktree_root"] = "must not be a filesystem root"
    if not isinstance(config.git_swe_mux_prompt_enabled, bool):
        errors["git_swe_mux_prompt_enabled"] = "must be true or false"
    decisions = config.git_swe_mux_prompt_decisions
    if not isinstance(decisions, dict) or len(decisions) > GIT_SWE_MUX_PROMPT_DECISION_LIMIT:
        errors["git_swe_mux_prompt_decisions"] = (
            f"must map at most {GIT_SWE_MUX_PROMPT_DECISION_LIMIT} Project ids to decisions"
        )
    elif any(
        not isinstance(project_id, str)
        or not project_id
        or len(project_id) > 128
        or decision not in GIT_SWE_MUX_PROMPT_DECISIONS
        for project_id, decision in decisions.items()
    ):
        errors["git_swe_mux_prompt_decisions"] = (
            "must map bounded Project ids to keep_visible or ignore_all"
        )
    if not isinstance(config.new_project_parent, str):
        errors["new_project_parent"] = "must be an absolute directory path or empty"
    elif config.new_project_parent.strip():
        project_parent = Path(config.new_project_parent.strip()).expanduser()
        if not project_parent.is_absolute():
            errors["new_project_parent"] = "must be an absolute directory path or empty"
        elif project_parent.parent == project_parent:
            errors["new_project_parent"] = "must not be a filesystem root"
    for field_name in ("auto_delivery_quiet_start", "auto_delivery_quiet_end"):
        value = str(getattr(config, field_name) or "")
        if value and not QUIET_TIME.fullmatch(value):
            errors[field_name] = "must be empty or a HH:MM time"
    if config.llm_provider not in LLM_PROVIDERS:
        errors["llm_provider"] = "must be " + " or ".join(LLM_PROVIDERS)
    elif config.llm_provider == "custom":
        # Only validated while it is the selected provider. A half-filled custom
        # endpoint left behind after switching back to OpenRouter is inert
        # configuration, and refusing to save the whole settings form over it
        # would make switching away from a broken endpoint impossible.
        if error := base_url_error(config.custom_llm_base_url):
            errors["custom_llm_base_url"] = error
        if error := llm_model_error(config.custom_llm_model):
            errors["custom_llm_model"] = error
        if error := llm_catalog_url_error(config.custom_llm_catalog_url):
            errors["custom_llm_catalog_url"] = error
    if not isinstance(config.tts_kokoro_lexicon, dict) or len(config.tts_kokoro_lexicon) > 500:
        errors["tts_kokoro_lexicon"] = "must be a map of at most 500 respellings"
    else:
        for lexicon_word, lexicon_spoken in config.tts_kokoro_lexicon.items():
            # Keys must be single tokens in the exact shape kokoro_tts._WORD
            # matches, or the ladder's casefolded whole-word lookup can never
            # hit them; values are what gets spoken in the word's place.
            if (
                not isinstance(lexicon_word, str)
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9'_.\-]{0,59}", lexicon_word)
                or not isinstance(lexicon_spoken, str)
                or not lexicon_spoken.strip()
                or len(lexicon_spoken) > 200
            ):
                errors["tts_kokoro_lexicon"] = (
                    "keys must be single words of at most 60 characters and values "
                    "non-empty respellings of at most 200 characters"
                )
                break
    if (
        not isinstance(config.voice_wake_words, list)
        or not 1 <= len(config.voice_wake_words) <= 64
        or any(
            not isinstance(word, str) or not word.strip() or len(word) > 40
            for word in config.voice_wake_words
        )
    ):
        errors["voice_wake_words"] = "must be 1–64 non-empty wake words of 40 characters or fewer"
    if not isinstance(config.voice_commands, list) or len(config.voice_commands) > 64:
        errors["voice_commands"] = "must be an array of at most 64 command definitions"
    else:
        seen_actions: set[str] = set()
        for index, command in enumerate(config.voice_commands):
            prefix = f"voice_commands.{index}"
            if not isinstance(command, dict):
                errors[prefix] = "must be an object with action and phrases"
                continue
            action = command.get("action")
            phrases = command.get("phrases")
            if action not in VOICE_COMMAND_ACTIONS:
                errors[f"{prefix}.action"] = f"must be one of {', '.join(VOICE_COMMAND_ACTIONS)}"
            elif action in seen_actions:
                errors[f"{prefix}.action"] = f"duplicate action {action}"
            else:
                seen_actions.add(str(action))
            if (
                not isinstance(phrases, list)
                or len(phrases) > 64
                or any(
                    not isinstance(phrase, str) or not phrase.strip() or len(phrase) > 80
                    for phrase in (phrases or [])
                )
            ):
                errors[f"{prefix}.phrases"] = (
                    "must be up to 64 non-empty phrases of 80 characters or fewer"
                )
    for scale_field in ("ui_scale_desktop", "ui_scale_mobile"):
        # TOML round-trips 1.0 as a float but a JSON PATCH sends bare `1`, so an
        # int is a legitimate spelling of a scale and must not be rejected here.
        value = getattr(config, scale_field)
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            errors[scale_field] = "must be a number"
        elif not any(abs(float(value) - step) < 1e-9 for step in UI_SCALES):
            allowed = ", ".join(f"{step:g}" for step in sorted(UI_SCALES))
            errors[scale_field] = f"must be one of {allowed}"
    if set(config.custom_theme) != CUSTOM_THEME_KEYS or any(
        not isinstance(value, str)
        or len(value) != 7
        or not value.startswith("#")
        or any(character not in "0123456789abcdefABCDEF" for character in value[1:])
        for value in config.custom_theme.values()
    ):
        errors["custom_theme"] = "must contain every semantic token as a #RRGGBB color"
    elif contrast_ratio(config.custom_theme["background"], config.custom_theme["foreground"]) < 4.5:
        errors["custom_theme"] = "background and foreground require at least 4.5:1 contrast"
    ids = [profile.id for profile in config.shell_profiles]
    if len(ids) != len(set(ids)) or any(not value.strip() for value in ids):
        errors["shell_profiles"] = "profile ids must be non-empty and unique"
    shell_ids = [
        profile.id for profile in config.shell_profiles if profile.backend == "shell"
    ]
    if config.shell_profiles and config.default_shell_profile not in shell_ids:
        # Scoped to shell profiles because this is the default for `New terminal`.
        # An agent profile named here would make every plain terminal unspawnable.
        errors["default_shell_profile"] = "must reference an existing shell launch profile"
    _validate_project_init_scripts(config, errors)
    for index, profile in enumerate(config.shell_profiles):
        prefix = f"shell_profiles.{index}"
        agent = is_agent_harness(profile.backend)
        if profile.backend != "shell" and not agent:
            errors[f"{prefix}.backend"] = "must be shell or a registered agent harness"
        if not profile.label.strip():
            errors[prefix] = "label is required"
        elif not agent and not profile.executable.strip():
            # An agent profile may inherit `harness_exe`; a shell profile has nothing
            # to inherit, so its executable is still required.
            errors[prefix] = "label and executable are required"
        if profile.cwd_strategy not in {"native", "home", "wsl"}:
            errors[f"{prefix}.cwd_strategy"] = "must be native, home, or wsl"
        if not all(isinstance(item, str) for item in profile.args):
            errors[f"{prefix}.args"] = "must be an array of strings"
        if not all(
            isinstance(key, str) and isinstance(value, str) for key, value in profile.env.items()
        ):
            errors[f"{prefix}.env"] = "must be a string map"
        if agent:
            if profile.cwd_strategy != "native":
                errors[f"{prefix}.cwd_strategy"] = "an agent launch profile must use native"
            if profile.cwd_integration:
                errors[f"{prefix}.cwd_integration"] = "is available only for a shell profile"
            conflict = reserved_launch_arg_conflict(profile.backend, profile.args)
            if conflict:
                errors[f"{prefix}.args"] = (
                    f"{conflict} is built by swe-mux for {profile.backend} and cannot be set here"
                )
    if errors:
        raise ValueError(errors)


def _toml_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    if isinstance(value, dict):
        # Keys are always quoted: a shortcut chord (`mod+r`) is not a valid bare
        # TOML key, and quoting parses identically for the keys that are.
        return (
            "{ "
            + ", ".join(f"{json.dumps(key)} = {_toml_value(item)}" for key, item in value.items())
            + " }"
        )
    raise TypeError(f"cannot encode {type(value).__name__}")


def _serialize(config: Config) -> str:
    values = asdict(config)
    values.pop("config_path", None)
    values["data_dir"] = str(config.data_dir)
    # TOML has no null, so an unset axis is written as an absent key rather than
    # as a zero - which would be a *total* ceiling, the strictest possible cap,
    # arrived at by a serializer rather than by the operator.
    for name in BUDGET_FIELDS:
        budget = getattr(config, name)
        values[name] = budget.as_toml_dict()
    profiles = values.pop("shell_profiles")
    lines = ["# swe-mux configuration (canonical, schema versioned)"]
    for key, value in values.items():
        lines.append(f"{key} = {_toml_value(value)}")
    for profile in profiles:
        lines.append("")
        lines.append("[[shell_profiles]]")
        for key, value in profile.items():
            lines.append(f"{key} = {_toml_value(value)}")
    return "\n".join(lines) + "\n"


def save_config(config: Config, *, backup: bool = False) -> None:
    path = config.config_path or config.data_dir / "config.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    _validate(config)
    if backup and path.exists():
        shutil.copy2(path, path.with_suffix(".toml.bak"))
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(_serialize(config), encoding="utf-8")
    os.replace(temporary, path)
    config.config_path = path


def _migrate_legacy_ccusage_commands(config: Config) -> bool:
    if not config.usage_commands:
        return False
    defaults_only = True
    for source, command in config.usage_commands.items():
        executable = Path(command[0]).stem.casefold() if command else ""
        legacy = _LEGACY_CCUSAGE_COMMANDS.get(source)
        if command != default_ccusage_command(source) and not (
            legacy and executable == "npx" and command[1:] == legacy
        ):
            defaults_only = False
            break
    if not defaults_only:
        return False
    config.usage_commands = {}
    return True


def _default_shell_profile(executable: str) -> LaunchProfile:
    executable_name = Path(executable).name.casefold()
    args = (
        ["-NoLogo"]
        if executable_name in {"powershell", "powershell.exe", "pwsh", "pwsh.exe"}
        else []
    )
    if executable_name in {"pwsh", "pwsh.exe"}:
        return LaunchProfile("default", "PowerShell 7", executable, args, marker="ps7")
    if executable_name in {"powershell", "powershell.exe"}:
        return LaunchProfile("default", "Windows PowerShell", executable, args, marker="ps")
    if not IS_WINDOWS:
        # Labelled with the shell's own name rather than "Default shell": on a
        # POSIX host the profile list is a list of real shells, and one row saying
        # "Default shell" beside `bash` and `zsh` tells the reader nothing about
        # which one it is.
        return LaunchProfile(
            "default", executable_name or "Shell", executable, args, marker="sh"
        )
    return LaunchProfile("default", "Default shell", executable, args)


def _is_auto_managed_windows_powershell_default(config: Config) -> bool:
    if config.default_shell_profile != "default" or len(config.shell_profiles) != 1:
        return False
    profile = config.shell_profiles[0]
    return (
        Path(config.shell_exe).name.casefold() in {"powershell", "powershell.exe"}
        and profile.id == "default"
        and profile.label in {"Default shell", "Windows PowerShell"}
        and Path(profile.executable).name.casefold() in {"powershell", "powershell.exe"}
        and profile.args == ["-NoLogo"]
        and profile.env == {}
        and profile.platforms == ["windows"]
        and profile.cwd_strategy == "native"
        # `marker` and `capabilities` are deliberately absent. Both are display-only
        # (a scannable tag and a derived summary), and comparing them here made
        # editing a cosmetic field silently opt the profile out of the PowerShell 7
        # auto-upgrade, with nothing saying so. Everything that distinguishes "the
        # default we created" from "a profile the user shaped" is already above.
        and not profile.cwd_integration
        and profile.enabled
        and profile.backend == "shell"
    )


#: Schema 23 raised the call caps that a continuous sampler exhausted in ten
#: calls. Only untouched schema-22 values are lifted, so a deliberately lowered
#: cap survives the upgrade.
SCHEMA_23_LEGACY_CALL_CAPS: dict[str, int] = {
    "automation_hourly_call_cap": 60,
    "automation_rule_hourly_call_cap": 20,
}

#: The same release's token and dollar figures, keyed by the pre-`Budget` scalar
#: they were written under. Read by `_migrate_budget_fields`, which is where the
#: legacy scalars are still visible.
SCHEMA_23_LEGACY_BUDGET_SCALARS: dict[str, float] = {
    "automation_daily_token_budget": 200_000,
    "automation_daily_budget_usd": 2.0,
    "automation_rule_daily_token_budget": 50_000,
    "automation_rule_daily_budget_usd": 0.5,
}


def _migrate_budget_fields(
    cfg: Config, raw: dict[str, Any], *, source_schema: int
) -> bool:
    """Fold the pre-`Budget` scalar caps into the one budget shape, losslessly.

    The non-negotiable rule: a config written by the previous build must enforce
    exactly what it enforced before. That means the mode is dictated by the unit
    the old code checked, never by what looks tidy - the automation daily
    ceilings checked tokens *and* dollars, so they arrive as `either`; the
    dollar caps arrive as `usd`. Each spec's `default` carries both the mode and
    the value the old scalar defaulted to, so a config that set one half of a
    pair keeps the other half at the figure that was silently enforcing it all
    along. Caps retired outright (the scan timeline's and attention
    narration's) are simply not visited: their keys, legacy or budget-shaped,
    load as unknown fields and are dropped.

    A config already carrying the new table wins outright: it was written by this
    build or a later one, and the legacy keys beside it (if a rollback wrote any)
    are stale.
    """
    migrated = False
    lifted = SCHEMA_23_LEGACY_BUDGET_SCALARS if source_schema < 23 else {}
    for spec in BUDGET_SPECS:
        if isinstance(raw.get(spec.field), dict):
            setattr(cfg, spec.field, coerce_budget(raw[spec.field], fallback=spec.default))
            continue
        tokens = spec.default.tokens
        usd = spec.default.usd
        touched = False
        for key, axis in ((spec.legacy_tokens, "tokens"), (spec.legacy_usd, "usd")):
            if not key or key not in raw:
                continue
            touched = True
            value = raw[key]
            if key in lifted and value == lifted[key]:
                # Still the untouched schema-22 figure, which schema 23 raised.
                continue
            if axis == "tokens":
                tokens = _coerce_int(value, tokens)
            else:
                usd = _coerce_float(value, usd)
        setattr(cfg, spec.field, Budget(tokens=tokens, usd=usd, mode=spec.default.mode))
        migrated = migrated or touched
    return migrated


def _coerce_int(value: Any, fallback: int | None) -> int | None:
    if isinstance(value, bool):
        return fallback
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _coerce_float(value: Any, fallback: float | None) -> float | None:
    if isinstance(value, bool):
        return fallback
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


# ---------------------------------------------------------------------------- #
# Foreign-host reconciliation
#
# A `config.toml` outlives the host that wrote it. It gets copied onto a new
# machine, restored from a backup, or - the case this was built for - written by
# a Windows build of swe-mux and then loaded by a Linux daemon in WSL over the
# same home directory. Every stored value in it was correct where it was written
# and some of them cannot be correct here.
#
# The failure that forced this was silent and permanent (measured on a live WSL
# Ubuntu daemon, 2026-08-28). `harness_exe` held `{"claude": "claude.exe",
# "codex": "codex.exe"}`, so the Run menu exec'd `claude.exe` on Linux and
# provider login died with `No such file or directory: 'codex.exe'`, while typing
# `claude` in a shell worked perfectly. `default_harness_executables()` already
# derived the right names through `harness.host_executable`, but the merge below
# was `{**defaults, **stored}` - a stored value always wins - so no default could
# ever displace it and the install could never heal itself.
#
# `shell_exe` had had half of this guard since POSIX support landed, and the
# asymmetry is the whole finding: `/bin/bash` healed and `claude.exe` did not.
#
# Two rules the table exists to enforce.
#
# **Every reconciliation is a rule in this table, never a branch in the loader.**
# The table is what `tests/test_foreign_host_config.py` walks, and a field that
# can hold a path or an executable and is in neither the table nor the reasoned
# exemptions beside it fails that test. A hand-written branch would be invisible
# to it, which is how `harness_exe` went unhandled for eleven days.
#
# **A repair re-derives; it never translates.** Turning
# `C:\tools\claude.exe` into `claude` by taking its stem would look clever and
# would be a guess about a machine nobody here can see. Re-deriving this host's
# own default is the same thing `shell_exe` already did, and it is checkable.
# ---------------------------------------------------------------------------- #


def _is_foreign(value: object) -> bool:
    return isinstance(value, str) and is_foreign_host_path(value)


def _repair_shell_exe(cfg: Config, path: Path) -> bool:
    del path
    if not _is_foreign(cfg.shell_exe):
        return False
    cfg.shell_exe = default_shell_executable()
    return True


def _repair_harness_exe(cfg: Config, path: Path) -> bool:
    del path
    if not isinstance(cfg.harness_exe, dict):
        return False
    changed = False
    repaired = dict(cfg.harness_exe)
    for name, executable in cfg.harness_exe.items():
        # A key the registry does not know has no default to re-derive. Leaving it
        # is the honest answer: `_validate` refuses it a moment later and names it,
        # which beats inventing a command for a harness this build never had.
        if _is_foreign(executable) and name in HARNESSES:
            repaired[name] = host_executable(HARNESSES[name])
            changed = True
    cfg.harness_exe = repaired
    return changed


def _repair_data_dir(cfg: Config, path: Path) -> bool:
    """Fall back to where the file being loaded actually is.

    `data_dir` is stored *and* re-read, so a config carried across hosts names a
    directory on the other one. On POSIX a `C:\\Users\\...` value is not even
    absolute, so every store, log, and clip directory would be created relative to
    whatever the daemon's working directory happened to be. The config file's own
    location is the fact that cannot be stale.

    Read through `as_posix()` because this is the one field the loader coerces to
    a `Path` before anything here sees it, and `str()` on a `Path` prints the
    *host's* separators - `WindowsPath("/opt/tools")` renders as `\\opt\\tools`,
    which no longer looks like the POSIX path it plainly is. `as_posix()` gives
    back the spelling that was stored, which is what says which host wrote it.
    """
    if not _is_foreign(cfg.data_dir.as_posix()):
        return False
    cfg.data_dir = path.parent
    return True


def _repair_pinned_directories(cfg: Config, path: Path) -> bool:
    del path
    if not isinstance(cfg.pinned_directories, list):
        return False
    kept = [entry for entry in cfg.pinned_directories if not _is_foreign(entry)]
    if len(kept) == len(cfg.pinned_directories):
        return False
    # Dropped rather than translated: a pin is a shortcut to a directory that
    # exists, and there is no directory on this host that a Windows path is a
    # shortcut to.
    cfg.pinned_directories = kept
    return True


def _repair_usage_command(cfg: Config, path: Path) -> bool:
    del path
    command = cfg.usage_command
    # Only argv[0] is a path. A later element is an argument, and on Windows an
    # argument legitimately starts with `/`.
    if not isinstance(command, list) or not command or not _is_foreign(command[0]):
        return False
    cfg.usage_command = default_ccusage_command()
    return True


def _repair_usage_commands(cfg: Config, path: Path) -> bool:
    del path
    if not isinstance(cfg.usage_commands, dict):
        return False
    changed = False
    repaired: dict[str, list[str]] = {}
    for source, command in cfg.usage_commands.items():
        if isinstance(command, list) and command and _is_foreign(command[0]):
            repaired[source] = default_ccusage_command(source)
            changed = True
        else:
            repaired[source] = command
    cfg.usage_commands = repaired
    return changed


def _repair_shell_profiles(cfg: Config, path: Path) -> bool:
    """Drop the profiles that name another host's executable, and keep the rest.

    A profile whose executable is shaped for another host is dead configuration:
    there is no machine state that could make `powershell.exe` start on Linux. It
    is dropped rather than left in place because leaving it is what turned the
    reported install into a permanent one - a stored value that cannot work here
    and cannot be displaced.

    Whether a profile is *permitted* on this host stays
    `profiles.profile_host_error`'s question - it owns `platforms` and already
    refuses a Windows profile on POSIX with a reason. This one answers what that
    refusal leaves open: whether anything is left to fall back to. An agent
    profile with an empty `executable` inherits `harness_exe`, so it is never
    foreign and is never dropped.

    Runs after `_repair_shell_exe`, because the shell it falls back to is that
    field. The rules tuple's order is what guarantees it.
    """
    kept = [profile for profile in cfg.shell_profiles if not _is_foreign(profile.executable)]
    if len(kept) == len(cfg.shell_profiles):
        return False
    shell_ids = [profile.id for profile in kept if profile.backend == "shell"]
    if not shell_ids:
        # Nothing left to open a terminal with. `_validate` requires a shell
        # profile and requires the default to name one, so a list of agent
        # profiles alone would refuse to load.
        fallback = _default_shell_profile(cfg.shell_exe)
        if any(profile.id == fallback.id for profile in kept):
            fallback = replace(fallback, id="default-shell")
        kept.insert(0, fallback)
        shell_ids = [fallback.id]
    cfg.shell_profiles = kept
    if cfg.default_shell_profile not in shell_ids:
        cfg.default_shell_profile = shell_ids[0]
    return True


def _clearing_repair(field_name: str) -> Callable[[Config, Path], bool]:
    """Reconcile a single stored path by emptying it, restoring the field's default.

    Every field this is used for reads its empty value as "derive it" - an
    app-managed worktree root, no assistant project parent, no startup directory,
    the managed Edge interpreter - so clearing is how the default is re-derived
    rather than a value being thrown away.
    """

    def repair(cfg: Config, path: Path) -> bool:
        del path
        if not _is_foreign(getattr(cfg, field_name)):
            return False
        setattr(cfg, field_name, "")
        return True

    return repair


#: Every `Config` field that can carry a host-shaped value, and how each one is
#: re-derived when the value stored in the file belongs to a different host.
_HOST_SHAPED_RULES: tuple[tuple[str, Callable[[Config, Path], bool]], ...] = (
    ("shell_exe", _repair_shell_exe),
    ("harness_exe", _repair_harness_exe),
    ("shell_profiles", _repair_shell_profiles),
    ("data_dir", _repair_data_dir),
    ("worktree_root", _clearing_repair("worktree_root")),
    ("new_project_parent", _clearing_repair("new_project_parent")),
    ("startup_cwd", _clearing_repair("startup_cwd")),
    ("tts_edge_python", _clearing_repair("tts_edge_python")),
    ("pinned_directories", _repair_pinned_directories),
    ("usage_command", _repair_usage_command),
    ("usage_commands", _repair_usage_commands),
)

#: Fields the discovery in `host_shaped_field_candidates` picks up by name and
#: that deliberately have no rule, each with the reason. An entry here is a
#: recorded decision, not a suppression: the test that reads it also fails when
#: one names a field that no longer exists.
HOST_SHAPED_FIELD_EXEMPTIONS: dict[str, str] = {
    "agent_shims_on_shell_path": (
        "a boolean switch, matched by the `_path` convention because it names one "
        "rather than holding one. A `bool` cannot carry a host-shaped value."
    ),
    "config_path": (
        "the path of the file being loaded, assigned by `load_config` itself - the "
        "loop that copies stored values skips it, so no stored value can reach it."
    ),
    "voice_commands": (
        "spoken phrases mapped to a fixed action set, not shell commands; nothing "
        "in it is ever executed and `_validate` bounds it against "
        "`VOICE_COMMAND_ACTIONS`."
    ),
    "project_init_scripts": (
        "free-form command lines the operator typed into Settings. There is no "
        "shape that separates a Windows-authored command from a legitimate one "
        "without parsing a shell grammar, and a command line that fails when it is "
        "run fails visibly at Project registration - unlike an executable swe-mux "
        "launches on the operator's behalf, which is what the rules above cover."
    ),
    "shell_profiles.executable": (
        "reconciled as part of `shell_profiles`, which replaces the whole stored "
        "list when nothing in it can start on this host."
    ),
}

#: The naming conventions this repository uses for a field that can hold a
#: filesystem path, an executable, or a command line. Discovery is by convention
#: rather than by a list of field names on purpose: a list beside the loader is a
#: second loader, and the copy is what drifts. A newly added `*_exe` or `*_root`
#: is picked up the moment it is declared, and fails the ratchet until it has
#: either a rule above or an exemption beside it.
HOST_SHAPED_FIELD_MARKERS = (
    "_exe",
    "_cwd",
    "_dir",
    "_root",
    "_parent",
    "_path",
    "_python",
    "_command",
    "_commands",
    "_directories",
    "_profiles",
    "_scripts",
    "executable",
)


def host_shaped_field_candidates() -> tuple[str, ...]:
    """Every configuration field whose value could be shaped for a host.

    Walks the dataclasses rather than a transcribed list, and reaches
    `LaunchProfile` as well as `Config` because a profile carries its own
    executable. Nested names are dotted (`shell_profiles.executable`).
    """
    found: list[str] = [
        name
        for name in Config.__dataclass_fields__
        if name.endswith(HOST_SHAPED_FIELD_MARKERS)
    ]
    found.extend(
        f"shell_profiles.{name}"
        for name in LaunchProfile.__dataclass_fields__
        if name.endswith(HOST_SHAPED_FIELD_MARKERS)
    )
    return tuple(found)


def _reconcile_foreign_host_values(cfg: Config, path: Path) -> bool:
    """Re-derive every stored value that is shaped for a different host.

    Returns whether anything changed, which `load_config` folds into `migrated` so
    the healed values are written back - an install that keeps re-deriving the same
    values on every start has not actually recovered.

    Deliberately reached from `load_config` and not from `update_config`. This
    answers a question about a *file that outlived its host*, and a value arriving
    through the settings API is an operator typing on the machine it will run on;
    rewriting that under them would be a control silently disagreeing with what it
    just showed. Whether a live edit should be refused is `_validate`'s question,
    and it is a different one.
    """
    migrated = False
    for _, repair in _HOST_SHAPED_RULES:
        migrated = repair(cfg, path) or migrated
    return migrated


def load_config(path: Path | None = None) -> Config:
    path = path or default_data_dir() / "config.toml"
    cfg = Config(data_dir=path.parent, config_path=path)
    migrated = False
    raw: dict[str, Any] = {}
    if path.exists():
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
        source_schema = int(raw.get("schema_version", 0))
        migrated = source_schema < SCHEMA_VERSION
        for key in Config.__dataclass_fields__:
            if key in {"config_path", "shell_profiles"}:
                continue
            if key in raw:
                setattr(cfg, key, Path(raw[key]) if key == "data_dir" else raw[key])
        configured_exe = (
            dict(raw["harness_exe"]) if isinstance(raw.get("harness_exe"), dict) else {}
        )
        configured_args = (
            dict(raw["harness_args"]) if isinstance(raw.get("harness_args"), dict) else {}
        )
        configured_usage = (
            dict(raw["usage_commands"]) if isinstance(raw.get("usage_commands"), dict) else {}
        )
        for name in HARNESSES:
            legacy_exe = raw.get(f"{name}_exe")
            legacy_args = raw.get(f"{name}_args")
            legacy_usage = raw.get(f"ccusage_{name}_command")
            if isinstance(legacy_exe, str) and name not in configured_exe:
                configured_exe[name] = legacy_exe
                migrated = True
            if isinstance(legacy_args, list) and name not in configured_args:
                configured_args[name] = legacy_args
                migrated = True
            if isinstance(legacy_usage, list) and name not in configured_usage:
                configured_usage[name] = legacy_usage
                migrated = True
        # A stored value wins this merge, which is what makes an override an
        # override - and is also why no default could ever displace a value
        # written on another host. `_repair_harness_exe` is what closes that,
        # after the legacy `<name>_exe` keys above have been folded in.
        cfg.harness_exe = {**default_harness_executables(), **configured_exe}
        cfg.harness_args = {**default_harness_args(), **configured_args}
        cfg.usage_commands = configured_usage
        # A gesture slot added after this file was written must arrive carrying its
        # default, not absent. The frontend already falls back per slot, so the runtime
        # was right either way — but Settings reads the stored map directly, so a
        # missing key rendered a live gesture as "Disabled" and the first save of any
        # *other* slot made that lie true. Merged rather than migrated because it then
        # holds for every future slot; a slot the operator deliberately turned off is
        # stored as "" and is not missing.
        if isinstance(raw.get("mobile_gestures"), dict):
            cfg.mobile_gestures = {**default_mobile_gestures(), **raw["mobile_gestures"]}
        # Unknown *top-level* keys are deliberately tolerated above; profile keys
        # get the same treatment. Without it a mistyped key — or a profile field
        # added by a newer build, which the redeploy rollback path makes real —
        # kills startup with a raw TypeError instead of the clean invalid-config
        # message, and an older daemon cannot start at all.
        profile_fields = set(LaunchProfile.__dataclass_fields__)
        cfg.shell_profiles = [
            LaunchProfile(**{key: value for key, value in item.items() if key in profile_fields})
            for item in raw.get("shell_profiles", [])
            if isinstance(item, dict)
        ]
        if (
            source_schema < 14
            and raw.get("stt_engine", "sapi") == "sapi"
            and raw.get("stt_whisper_model", "base.en") == "base.en"
        ):
            # SAPI/base.en was the short-lived initial Conversation-mode default.
            # Migrate only that untouched pair; explicit engine/model choices survive.
            cfg.stt_engine = "whisper"
            cfg.stt_whisper_model = "turbo"
        if source_schema < 16:
            # The two-finger sidebar gestures shipped as directional open/close, but a
            # single toggle is what users expect. Upgrade only the untouched old defaults
            # so any custom mapping the user chose is left intact.
            gestures = dict(cfg.mobile_gestures)
            if gestures.get("two_finger_swipe_right") == "sidebar.open":
                gestures["two_finger_swipe_right"] = "sidebar.toggle"
                migrated = True
            if gestures.get("two_finger_swipe_left") == "sidebar.close":
                gestures["two_finger_swipe_left"] = "sidebar.toggle"
                migrated = True
            cfg.mobile_gestures = gestures
        if source_schema < 17:
            # The utility drawer added a right-edge slide-in panel. Both horizontal
            # two-finger swipes toggled the (left-edge) sidebar, which made one of
            # them redundant; the leftward swipe now pulls the new panel in. Only the
            # duplicate default is upgraded, so a deliberate mapping is left intact.
            gestures = dict(cfg.mobile_gestures)
            if gestures.get("two_finger_swipe_left") == "sidebar.toggle":
                gestures["two_finger_swipe_left"] = "drawer.toggle"
                migrated = True
            cfg.mobile_gestures = gestures
        if source_schema < 18:
            gestures = dict(cfg.mobile_gestures)
            for slot, command in gestures.items():
                if command in {"project.note", "session.note"}:
                    gestures[slot] = "notes.open"
                    migrated = True
            cfg.mobile_gestures = gestures
        if source_schema < 20:
            # Append and Voice Comms did not exist before schema 20. Add only the
            # new actions, preserving every phrase and omission the user could have
            # chosen for the older command set.
            existing = {item.get("action") for item in cfg.voice_commands if isinstance(item, dict)}
            additions = [
                item
                for item in default_voice_commands()
                if item["action"] in {"append", "comms_on", "comms_off"}
                and item["action"] not in existing
            ]
            if additions:
                cfg.voice_commands = [*cfg.voice_commands, *additions]
                migrated = True
        if source_schema < 21:
            # Bare "stop" is the natural interruption while audio is speaking.
            # Upgrade only the untouched schema-20 mute phrases so a customized or
            # deliberately disabled action remains exactly as the user configured it.
            old_mute_phrases = ["mute", "stop speaking", "stop playback", "stop audio"]
            for voice_command in cfg.voice_commands:
                if (
                    isinstance(voice_command, dict)
                    and voice_command.get("action") == "mute"
                    and voice_command.get("phrases") == old_mute_phrases
                ):
                    voice_command["phrases"] = ["mute", "stop", *old_mute_phrases[1:]]
                    migrated = True
                    break
        if source_schema < 23:
            # The hourly call caps were sized for episodic observers and starved
            # the continuous sampler. Lift only values that are still the
            # untouched schema-22 defaults, so a deliberately lowered cap
            # survives. Their token/dollar siblings were lifted by the same
            # release and are handled in `_migrate_budget_fields`, which has to
            # read the pre-`Budget` scalars anyway.
            for key, legacy in SCHEMA_23_LEGACY_CALL_CAPS.items():
                if key not in raw or raw[key] == legacy:
                    setattr(cfg, key, Config.__dataclass_fields__[key].default)
                    migrated = True
        migrated = _migrate_budget_fields(cfg, raw, source_schema=source_schema) or migrated
        if source_schema < 28:
            # The brainstorm hold/proceed pair did not exist before schema 28.
            # Add only the new actions, preserving every phrase and omission the
            # user could have chosen for the older command set.
            existing = {item.get("action") for item in cfg.voice_commands if isinstance(item, dict)}
            additions = [
                item
                for item in default_voice_commands()
                if item["action"] in {"hold", "proceed"} and item["action"] not in existing
            ]
            if additions:
                cfg.voice_commands = [*cfg.voice_commands, *additions]
                migrated = True
        if source_schema < 26 and raw.get("tts_engine") == "edge":
            # Phase 10.5 removed the network edge-tts engine (LGPL payload,
            # unauthorized Microsoft endpoint). The OS voice is the engine that
            # always works with no download, so an `edge` config lands there;
            # Kokoro stays a deliberate choice once its model is downloaded.
            cfg.tts_engine = "sapi"
        if source_schema < 34:
            # Schema 34 retired the scan timeline's and attention narration's
            # dedicated per-call output ceilings; both features now run under
            # `automation_max_output_tokens`. The global ceiling must therefore
            # absorb the retired ones at the loosest of the three - the scan's
            # was 900 by default, and a global ceiling left at the old 256 would
            # truncate every scan response into an unparseable record. Lifting
            # only, so a deliberately lowered global ceiling that already covers
            # the retired ones is untouched, and a lowered *scan* ceiling still
            # bounds what it bounded (now for everything).
            retired_scan = _coerce_int(raw.get("scan_timeline_max_output_tokens"), 900) or 900
            retired_narration = (
                _coerce_int(raw.get("attention_narration_max_output_tokens"), 200) or 200
            )
            lifted_ceiling = max(cfg.automation_max_output_tokens, retired_scan, retired_narration)
            if lifted_ceiling != cfg.automation_max_output_tokens:
                cfg.automation_max_output_tokens = lifted_ceiling
                migrated = True
        if source_schema < 33 and "tts_kokoro_lexicon" not in raw:
            # Schema 33 names the dictionary by its actual owner. It contains
            # Kokoro respellings and exact phoneme forms that no other provider
            # may interpret. Copy every entry before the old key is dropped by
            # canonical serialization.
            legacy_lexicon = raw.get("tts_lexicon")
            if isinstance(legacy_lexicon, dict):
                cfg.tts_kokoro_lexicon = dict(legacy_lexicon)
        if source_schema < 31 and "attention_observers_enabled" not in raw:
            # `phase7_observers_enabled` was renamed to `attention_observers_enabled`:
            # the old name leaked this project's roadmap numbering into `/api/config`
            # and named a release rather than the thing it switches. The field-copy
            # loop above only reads known dataclass fields, so without this the old
            # key would be silently dropped and every install that had enabled the
            # attention observers would find them off after one load - a switch
            # flipping itself on upgrade, with a re-save that erases the evidence.
            legacy_attention = raw.get("phase7_observers_enabled")
            if isinstance(legacy_attention, bool):
                cfg.attention_observers_enabled = legacy_attention
                migrated = True
        if source_schema < 32:
            # `project_ignore_patterns` is persisted in full, so an install written before
            # this release keeps its stored list forever and never sees a new default. That
            # is the whole reason this block exists: without it the worktree patterns ship
            # and reach only brand-new installs, which is indistinguishable from not
            # shipping them at all on every machine that has been running swe-mux.
            #
            # Only ever *adds*, and only patterns that did not exist before schema 32, so
            # there is no "the user deliberately removed this" case to get wrong: a pattern
            # absent from an older install is absent because it was never offered.
            existing = {
                pattern.strip().replace("\\", "/").strip("/")
                for pattern in cfg.project_ignore_patterns
                if isinstance(pattern, str)
            }
            missing = [
                pattern for pattern in SCHEMA_32_IGNORE_ADDITIONS if pattern not in existing
            ]
            # Respect the ceiling `_validate` enforces rather than appending past it and
            # failing startup on a config this migration itself made invalid.
            room = PROJECT_IGNORE_PATTERN_LIMIT - len(cfg.project_ignore_patterns)
            missing = missing[: max(0, room)]
            if missing:
                cfg.project_ignore_patterns = [*cfg.project_ignore_patterns, *missing]
                migrated = True
        if source_schema < 22 and "harness_setup_complete" not in raw:
            # The first-run harness panel is new. An existing config is by definition
            # not a first run, so mark setup complete on upgrade; only a brand-new
            # install (no config file, so this block never runs) shows the panel.
            cfg.harness_setup_complete = True
            migrated = True
    # After every stored value has been read and before anything is derived from
    # one: a config that outlived its host carries values this host cannot use,
    # and the shell-profile default below is built from `shell_exe`.
    migrated = _reconcile_foreign_host_values(cfg, path) or migrated
    if not cfg.shell_profiles:
        # Only the PowerShell 7 upgrade is left here. The POSIX half this block
        # used to carry - "a config written on another host can name a shell this
        # host cannot start, so re-derive" - is now `_repair_shell_exe`, and the
        # move is a widening rather than a relocation: keyed off the value's shape,
        # it also fires for a config that *does* store `shell_exe`, which is every
        # config this daemon has ever written and was the whole hole. What stood
        # here only ever ran when the key was absent, in which case the field
        # already held this host's default and the branch reassigned it to itself.
        if "shell_exe" not in raw and IS_WINDOWS and shutil.which("pwsh.exe"):
            cfg.shell_exe = "pwsh.exe"
        cfg.shell_profiles = [_default_shell_profile(cfg.shell_exe)]
    elif (
        IS_WINDOWS
        and _is_auto_managed_windows_powershell_default(cfg)
        and shutil.which("pwsh.exe")
    ):
        cfg.shell_exe = "pwsh.exe"
        cfg.shell_profiles = [_default_shell_profile(cfg.shell_exe)]
        migrated = True
    migrated = _migrate_legacy_ccusage_commands(cfg) or migrated
    cfg.schema_version = SCHEMA_VERSION
    # Every stored value has been assigned by now, and none of it went through
    # `__post_init__`, so this is where a retired registry id gets dropped
    # rather than refused by the validation below.
    cfg.scrub_registry_maps()
    _validate(cfg)
    if migrated or not path.exists():
        save_config(cfg, backup=path.exists())
    return cfg


def update_config(config: Config, changes: dict[str, Any]) -> tuple[set[str], set[str]]:
    allowed = set(Config.__dataclass_fields__) - {
        "schema_version",
        "revision",
        "data_dir",
        "config_path",
    }
    unknown = set(changes) - allowed
    if unknown:
        raise ValueError({key: "unknown or read-only setting" for key in sorted(unknown)})
    candidate = Config(**{**asdict(config), "config_path": config.config_path})
    candidate.data_dir = config.data_dir
    candidate.shell_profiles = list(config.shell_profiles)
    changed: set[str] = set()
    for key, value in changes.items():
        if key in BUDGET_FIELDS:
            # Coerce before comparing: the browser round-trips the JSON object it
            # was handed, and a dict never equals the `Budget` it describes, so
            # comparing raw would report every save as a change to every budget.
            value = coerce_budget(value, fallback=BUDGET_FIELDS[key].default)
        if key == "shell_profiles":
            if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
                raise ValueError({"shell_profiles": "must be an array of profile objects"})
            # Unknown keys are dropped rather than raised, matching `load_config`.
            # The browser round-trips whatever `/api/profiles` handed it, including
            # the `configured` marker the detected list carries, and a raw TypeError
            # here would surface as a failed save with no field named.
            profile_fields = set(LaunchProfile.__dataclass_fields__)
            value = [
                LaunchProfile(**{key: item for key, item in entry.items() if key in profile_fields})
                for entry in value
            ]
        if getattr(candidate, key) != value:
            setattr(candidate, key, value)
            changed.add(key)
    candidate.revision = config.revision + 1
    _validate(candidate)
    save_config(candidate)
    for field_name in Config.__dataclass_fields__:
        setattr(config, field_name, getattr(candidate, field_name))
    return changed - RESTART_FIELDS, changed & RESTART_FIELDS
