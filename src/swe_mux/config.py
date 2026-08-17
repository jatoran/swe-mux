from __future__ import annotations

import json
import os
import re
import shutil
import sys
import tomllib
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .harness import (
    HARNESSES,
    host_executable,
    is_agent_harness,
    reserved_launch_arg_conflict,
)
from .host_platform import IS_MACOS, IS_WINDOWS, platform_key
from .keybindings import is_command


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


SCHEMA_VERSION = 25
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
    # Adapters are constructed once at daemon start, so the per-harness MCP and
    # instrumentation gates only re-read on the next restart. Marking them
    # restart-required keeps the UI honest rather than reporting a hot apply that
    # does not reach already-built adapters.
    "harness_mcp_enabled",
    "harness_instrument_enabled",
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
DEFAULT_PROJECT_IGNORE_PATTERNS = [
    ".git",
    ".swe-mux",
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
MOBILE_GESTURE_SLOTS = (
    "swipe_left",
    "swipe_right",
    "two_finger_swipe_left",
    "two_finger_swipe_right",
    "two_finger_swipe_up",
    "two_finger_swipe_down",
    "two_finger_tap",
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
    # live sessions survive a daemon restart. Off by default while the split
    # proves itself; in-process spawning remains the automatic fallback.
    pty_supervisor_enabled: bool = False
    # Startup default for the daemon's root-logger level (rotating daemon.log +
    # console). Runtime changes go through POST /api/debug/log-level or a
    # config-file edit; neither requires a restart.
    log_level: str = "INFO"
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
    # Empty means the app-managed directory below data_dir. The public config exposes
    # the resolved absolute value so browser clients never infer the daemon's home.
    worktree_root: str = ""
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
    theme: str = "dark"
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
    pinned_directories: list[str] = field(default_factory=list)
    project_ignore_patterns: list[str] = field(
        default_factory=lambda: list(DEFAULT_PROJECT_IGNORE_PATTERNS)
    )
    project_init_scripts: list[dict[str, Any]] = field(default_factory=list)
    automation_enabled: bool = False
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
    # Phase 5 agent-to-agent messaging (`mux.notify`). The tool exists by
    # default because a notify lands as an inert draft unless the *receiving*
    # session opted in to accepting agent messages.
    agent_messaging_enabled: bool = True
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
    agent_message_max_thread_turns: int = 12
    # `mux.requestSpawn` creates an inert Fleet Queue approval draft and nothing
    # else; approval is a human act.
    request_spawn_enabled: bool = True
    # Phase 7.6 agent session control (`mux.interrupt`, `mux.end_session`). This is
    # the install-wide master switch; per-Project the capability is off until the
    # `session_control` automation is opted in, and even then the authority is
    # `draft` (a human approves every action) until the Project's
    # `session_control_grant` is raised. False here refuses both tools everywhere,
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
    automation_concurrency: int = 2
    automation_queue_size: int = 256
    automation_max_input_tokens: int = 4096
    automation_max_output_tokens: int = 256
    # These are the shared ceilings over *every* automation. They were sized for
    # episodic observers that fire once per session, and a continuous sampler
    # (scan timeline) exhausted the per-rule token cap after ten calls costing
    # under half a cent. The token axis must never be the binding constraint
    # while the dollar axis sits at 0.2% - the dollar figures are the ones that
    # describe real cost, so the token caps are now headroom rather than policy.
    automation_daily_token_budget: int = 10_000_000
    automation_daily_budget_usd: float = 20.0
    automation_rule_daily_token_budget: int = 4_000_000
    automation_rule_daily_budget_usd: float = 10.0
    automation_hourly_call_cap: int = 1_200
    automation_rule_hourly_call_cap: int = 600
    # Control-plane project card (CP §5.4). Per-project opt-in gates whether it
    # runs at all; these bound what one build may cost. Empty model falls back
    # to the automation cheap model; with neither set there is simply no card.
    project_card_model: str = ""
    project_card_daily_budget_usd: float = 0.25
    project_card_max_input_tokens: int = 6000
    project_card_max_output_tokens: int = 600
    # Phase 5.5 semantic timeline. The global switch is an emergency/master
    # boundary, while Project permission and the current-run toggle are checked
    # separately. The undated OpenRouter id follows the provider's latest V4
    # Flash revision without silently changing model family.
    scan_timeline_enabled: bool = False
    scan_timeline_model: str = "deepseek/deepseek-v4-flash"
    # Scan timeline samples continuously: an event debounce plus a three-minute
    # heartbeat, per session, for as long as a run is enabled. Charging that to
    # the shared `automation_rule_*` caps put it in the same envelope as the
    # session titler, which fires once. It now carries its own daily token
    # budget and hourly call cap and is exempt from the per-rule caps; the
    # global automation ceilings above still apply as an emergency bound, and
    # the dollar budgets (global, per-rule, and the Project's own) are the real
    # ceiling. At the observed ~$0.08 per million tokens the daily budget below
    # is about a quarter of a dollar.
    scan_timeline_daily_token_budget: int = 3_000_000
    scan_timeline_hourly_call_cap: int = 600
    scan_timeline_run_token_budget: int = 500_000
    # The feature's dollar ceiling, and the one number worth adjusting. It was a
    # per-Project field in a committed `.swe-mux/config.toml`, which meant the
    # cap that could stop scanning lived in a file nobody opens, defaulted
    # differently per checkout, and had to be raised once per Project. One
    # global setting, edited in Settings -> Automation. At the observed ~$0.08
    # per million tokens this is far above the daily token budget above, so the
    # tokens run out first - which is the intended order.
    scan_timeline_daily_budget_usd: float = 5.0
    # The schema permits ~2,600 characters of prose across five fields. 420
    # output tokens could not hold its own worst case, and a truncated strict
    # JSON body is an unparseable response that costs a record.
    scan_timeline_max_output_tokens: int = 900
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
    attention_narration_enabled: bool = False
    attention_narration_model: str = ""
    attention_narration_daily_budget_usd: float = 0.10
    attention_narration_max_output_tokens: int = 200
    openrouter_cheap_model: str = ""
    openrouter_standard_model: str = ""
    openrouter_request_timeout_seconds: float = 30.0
    observer_titler_enabled: bool = False
    # Phase 7.7 retired the turn summarizer; the scan timeline is the single
    # behavioral-summary producer. A config predating the removal still carries
    # `observer_summarizer_enabled`; `load_config` copies only known dataclass
    # fields, so the stale key is dropped on load and on the next write rather
    # than erroring.
    phase7_observers_enabled: bool = False
    tts_enabled: bool = False
    tts_default_mode: str = "off"
    tts_content: str = "summary"
    tts_engine: str = "edge"
    # A neutral en-US default rather than a locale-specific voice, so every new
    # user gets a sensible voice without an accent chosen for one operator. TTS is
    # off by default, so this only matters once a user turns it on.
    tts_edge_voice: str = "en-US-JennyNeural"
    tts_edge_rate: str = "+10%"
    tts_edge_pitch: str = "+0Hz"
    tts_soften_stops: bool = True
    tts_sapi_voice: str = ""
    tts_sapi_rate: int = 0
    tts_summary_model: str = ""
    tts_summary_max_tokens: int = 500
    tts_verbatim_max_chars: int = 6000
    tts_daily_budget_usd: float = 1.0
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
    # Resolved from the real home directory with no environment override, so every
    # process on this machine shares one data dir. Tests must inject an explicit path
    # (see load_config) rather than relying on isolation; two suites running at once
    # from different git worktrees will otherwise contend over the same mux.db.
    data_dir: Path = field(default_factory=default_data_dir)
    config_path: Path | None = field(default=None, repr=False)

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


def _validate(config: Config) -> None:
    errors: dict[str, str] = {}
    if config.host not in LOOPBACK_HOSTS:
        errors["host"] = (
            "must be a loopback address (127.0.0.1, localhost, or ::1); "
            "direct tailnet listening uses the detected Tailscale address automatically"
        )
    if not 1 <= config.port <= 65535:
        errors["port"] = "must be between 1 and 65535"
    if config.default_backend != "shell" and not is_agent_harness(config.default_backend):
        errors["default_backend"] = "must be shell or a registered agent"
    if config.terminal_renderer not in {"auto", "dom", "webgl"}:
        errors["terminal_renderer"] = "must be auto, dom, or webgl"
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
    if config.notes_default_open not in {"dock", "popout"}:
        errors["notes_default_open"] = "must be dock or popout"
    if config.note_syntax not in {"markdown", "plain"}:
        errors["note_syntax"] = "must be markdown or plain"
    if config.note_tab_behavior not in {"indent", "focus"}:
        errors["note_tab_behavior"] = "must be indent or focus"
    if config.note_shortcut_policy not in {"browser-safe", "editor-first", "none"}:
        errors["note_shortcut_policy"] = "must be browser-safe, editor-first, or none"
    if config.note_command_rail not in {"auto", "on", "off"}:
        errors["note_command_rail"] = "must be auto, on, or off"
    if len(config.note_font_family) > 200:
        errors["note_font_family"] = "must be 200 characters or fewer"
    if config.note_font_size_px != 0 and not 8 <= config.note_font_size_px <= 48:
        errors["note_font_size_px"] = "must be 0 (editor default) or between 8 and 48"
    if config.note_line_height != 0 and not 1.0 <= config.note_line_height <= 3.0:
        errors["note_line_height"] = "must be 0 (editor default) or between 1.0 and 3.0"
    if config.note_rail_button_size_px != 0 and not 32 <= config.note_rail_button_size_px <= 96:
        errors["note_rail_button_size_px"] = "must be 0 (editor default) or between 32 and 96"
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
    if config.mobile_vertical_drag not in {"smart", "terminal", "application", "disabled"}:
        errors["mobile_vertical_drag"] = "must be smart, terminal, application, or disabled"
    if config.mobile_scroll_direction not in {"natural", "wheel"}:
        errors["mobile_scroll_direction"] = "must be natural or wheel"
    if not 0.25 <= config.mobile_scroll_sensitivity <= 4:
        errors["mobile_scroll_sensitivity"] = "must be between 0.25 and 4"
    if config.mobile_long_press not in {"context_menu", "disabled"}:
        errors["mobile_long_press"] = "must be context_menu or disabled"
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
    for bool_map in ("harness_enabled", "harness_mcp_enabled", "harness_instrument_enabled"):
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
    ):
        value = getattr(config, field_name)
        if isinstance(value, dict):
            unknown = set(value) - set(HARNESSES)
            if unknown:
                errors[field_name] = "unknown harnesses: " + ", ".join(sorted(unknown))
    if not isinstance(config.project_ignore_patterns, list) or not all(
        isinstance(item, str) for item in config.project_ignore_patterns
    ):
        errors["project_ignore_patterns"] = "must be an array of strings"
    if isinstance(config.project_ignore_patterns, list) and (
        len(config.project_ignore_patterns) > 256
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
    if not 0 <= config.ccusage_refresh_minutes <= 24 * 60:
        errors["ccusage_refresh_minutes"] = "must be between 0 and 1440 minutes"
    if not 1024 <= config.scrollback_bytes <= 1024 * 1024 * 1024:
        errors["scrollback_bytes"] = "must be between 1 KiB and 1 GiB"
    if not 1024 <= config.attach_replay_bytes <= 1024 * 1024 * 1024:
        errors["attach_replay_bytes"] = "must be between 1 KiB and 1 GiB"
    if not 0.25 <= config.git_poll_seconds <= 3600:
        errors["git_poll_seconds"] = "must be between 0.25 and 3600 seconds"
    if not isinstance(config.worktree_root, str):
        errors["worktree_root"] = "must be an absolute directory path or empty"
    elif config.worktree_root.strip():
        worktree_root = Path(config.worktree_root.strip()).expanduser()
        if not worktree_root.is_absolute():
            errors["worktree_root"] = "must be an absolute directory path or empty"
        elif worktree_root.parent == worktree_root:
            errors["worktree_root"] = "must not be a filesystem root"
    if not 0.5 <= config.process_poll_seconds <= 60:
        errors["process_poll_seconds"] = "must be between 0.5 and 60 seconds"
    if not 1 <= config.process_orphan_grace_seconds <= 3600:
        errors["process_orphan_grace_seconds"] = "must be between 1 and 3600 seconds"
    if not 0.5 <= config.ghost_window_poll_seconds <= 60:
        errors["ghost_window_poll_seconds"] = "must be between 0.5 and 60 seconds"
    if not 1 <= config.process_evidence_retention_days <= 3650:
        errors["process_evidence_retention_days"] = "must be between 1 and 3650"
    if not 1 <= config.operational_telemetry_retention_days <= 3650:
        errors["operational_telemetry_retention_days"] = "must be between 1 and 3650"
    if not 1 <= config.status_timeline_retention_days <= 3650:
        errors["status_timeline_retention_days"] = "must be between 1 and 3650"
    if not 5 <= config.provider_quota_poll_minutes <= 1440:
        errors["provider_quota_poll_minutes"] = "must be between 5 and 1440"
    if not 1 <= config.provider_quota_turn_refresh_min_minutes <= 1440:
        errors["provider_quota_turn_refresh_min_minutes"] = "must be between 1 and 1440"
    if not 1 <= config.history_limit <= 10000:
        errors["history_limit"] = "must be between 1 and 10000"
    if not 1 <= config.clipboard_history_limit <= 2000:
        errors["clipboard_history_limit"] = "must be between 1 and 2000 entries"
    if not 256 <= config.clipboard_history_entry_max_chars <= 1_000_000:
        errors["clipboard_history_entry_max_chars"] = "must be between 256 and 1000000 characters"
    if not 0 <= config.clipboard_history_retention_hours <= 8760:
        errors["clipboard_history_retention_hours"] = (
            "must be between 0 (keep until evicted) and 8760 hours"
        )
    if not 1 <= config.automation_retention_days <= 3650:
        errors["automation_retention_days"] = "must be between 1 and 3650"
    if not 1 <= config.prompt_queue_retention_days <= 3650:
        errors["prompt_queue_retention_days"] = "must be between 1 and 3650"
    # Auto-delivery bounds. The lower bounds are the point: a zero-length
    # stability window or an unbounded grant would defeat the gate they exist
    # to be (`ROADMAP.md` Phase 5).
    if not 2 <= config.auto_delivery_stable_seconds <= 600:
        errors["auto_delivery_stable_seconds"] = "must be between 2 and 600 seconds"
    if not 1 <= config.auto_delivery_max_consecutive <= 50:
        errors["auto_delivery_max_consecutive"] = "must be between 1 and 50 sends"
    if not 1 <= config.auto_delivery_session_ttl_minutes <= 1440:
        errors["auto_delivery_session_ttl_minutes"] = "must be between 1 and 1440 minutes"
    if not 0 <= config.auto_delivery_refusal_backoff_seconds <= 3600:
        errors["auto_delivery_refusal_backoff_seconds"] = "must be between 0 and 3600 seconds"
    # Approval bounds. Every lower bound here is the point: an unbounded grant or
    # an unbounded answer count is standing authority, which is the one thing
    # this feature must not become.
    if not 1 <= config.approval_grant_ttl_minutes <= 480:
        errors["approval_grant_ttl_minutes"] = "must be between 1 and 480 minutes"
    if not 1 <= config.approval_max_auto_per_grant <= 5000:
        errors["approval_max_auto_per_grant"] = "must be between 1 and 5000 requests"
    if not 1 <= config.approval_hook_timeout_seconds <= 60:
        errors["approval_hook_timeout_seconds"] = "must be between 1 and 60 seconds"
    for field_name in ("auto_delivery_quiet_start", "auto_delivery_quiet_end"):
        value = str(getattr(config, field_name) or "")
        if value and not QUIET_TIME.fullmatch(value):
            errors[field_name] = "must be empty or a HH:MM time"
    if not 1 <= config.agent_message_max_chars <= 100_000:
        errors["agent_message_max_chars"] = "must be between 1 and 100000 characters"
    if not 0 <= config.agent_message_hourly_budget <= 1000:
        errors["agent_message_hourly_budget"] = "must be between 0 and 1000 messages per hour"
    if not 1 <= config.agent_message_pending_per_target <= 100:
        errors["agent_message_pending_per_target"] = "must be between 1 and 100 messages"
    if not 1 <= config.agent_message_max_chain_depth <= 10:
        errors["agent_message_max_chain_depth"] = "must be between 1 and 10 hops"
    if not 1 <= config.agent_message_max_thread_turns <= 100:
        errors["agent_message_max_thread_turns"] = "must be between 1 and 100 messages"
    if not 0 <= config.session_control_hourly_budget <= 1000:
        errors["session_control_hourly_budget"] = "must be between 0 and 1000 actions per hour"
    if not 1 <= config.session_control_graceful_timeout_s <= 120:
        errors["session_control_graceful_timeout_s"] = "must be between 1 and 120 seconds"
    if not 0 <= config.agent_spawn_hourly_budget <= 1000:
        errors["agent_spawn_hourly_budget"] = "must be between 0 and 1000 spawns per hour"
    if not 0 <= config.scheduled_runs_max_concurrent <= 50:
        errors["scheduled_runs_max_concurrent"] = "must be between 0 and 50 sessions"
    if not 1 <= config.scheduled_runs_poll_seconds <= 300:
        errors["scheduled_runs_poll_seconds"] = "must be between 1 and 300 seconds"
    if not 1 <= config.scheduled_run_retention_days <= 3650:
        errors["scheduled_run_retention_days"] = "must be between 1 and 3650 days"
    if not 1 <= config.automation_concurrency <= 16:
        errors["automation_concurrency"] = "must be between 1 and 16"
    if not 16 <= config.automation_queue_size <= 4096:
        errors["automation_queue_size"] = "must be between 16 and 4096"
    if not 128 <= config.automation_max_input_tokens <= 128_000:
        errors["automation_max_input_tokens"] = "must be between 128 and 128000"
    if not 16 <= config.automation_max_output_tokens <= 8192:
        errors["automation_max_output_tokens"] = "must be between 16 and 8192"
    if not 0 <= config.automation_daily_token_budget <= 100_000_000:
        errors["automation_daily_token_budget"] = "must be between 0 and 100000000"
    if not 0 <= config.automation_daily_budget_usd <= 10_000:
        errors["automation_daily_budget_usd"] = "must be between 0 and 10000"
    if not 0 <= config.automation_rule_daily_token_budget <= 100_000_000:
        errors["automation_rule_daily_token_budget"] = "must be between 0 and 100000000"
    if not 0 <= config.automation_rule_daily_budget_usd <= 10_000:
        errors["automation_rule_daily_budget_usd"] = "must be between 0 and 10000"
    if not 1 <= config.automation_hourly_call_cap <= 10_000:
        errors["automation_hourly_call_cap"] = "must be between 1 and 10000"
    if not 1 <= config.automation_rule_hourly_call_cap <= 10_000:
        errors["automation_rule_hourly_call_cap"] = "must be between 1 and 10000"
    if not 0 <= config.project_card_daily_budget_usd <= 100:
        errors["project_card_daily_budget_usd"] = "must be between 0 and 100"
    if not 512 <= config.project_card_max_input_tokens <= 128_000:
        errors["project_card_max_input_tokens"] = "must be between 512 and 128000"
    if not 128 <= config.project_card_max_output_tokens <= 4096:
        errors["project_card_max_output_tokens"] = "must be between 128 and 4096"
    if not config.scan_timeline_model.strip() or len(config.scan_timeline_model) > 200:
        errors["scan_timeline_model"] = "must be an exact OpenRouter model id"
    if not 512 <= config.scan_timeline_run_token_budget <= 20_000_000:
        errors["scan_timeline_run_token_budget"] = "must be between 512 and 20000000"
    if not 512 <= config.scan_timeline_daily_token_budget <= 100_000_000:
        errors["scan_timeline_daily_token_budget"] = "must be between 512 and 100000000"
    if not 1 <= config.scan_timeline_hourly_call_cap <= 100_000:
        errors["scan_timeline_hourly_call_cap"] = "must be between 1 and 100000"
    if not 256 <= config.scan_timeline_max_output_tokens <= 8_192:
        errors["scan_timeline_max_output_tokens"] = "must be between 256 and 8192"
    if not 0 <= config.scan_timeline_daily_budget_usd <= 1_000:
        errors["scan_timeline_daily_budget_usd"] = "must be between 0 and 1000"
    if not 0 <= config.attention_daily_interrupt_budget <= 100:
        errors["attention_daily_interrupt_budget"] = "must be between 0 and 100"
    if not 0 <= config.attention_hourly_interrupt_cap <= 100:
        errors["attention_hourly_interrupt_cap"] = "must be between 0 and 100"
    if not 60 <= config.attention_incident_window_seconds <= 86_400:
        errors["attention_incident_window_seconds"] = "must be between 60 and 86400"
    if not 0 <= config.attention_narration_daily_budget_usd <= 100:
        errors["attention_narration_daily_budget_usd"] = "must be between 0 and 100"
    if not 32 <= config.attention_narration_max_output_tokens <= 2048:
        errors["attention_narration_max_output_tokens"] = "must be between 32 and 2048"
    if not 1 <= config.openrouter_request_timeout_seconds <= 120:
        errors["openrouter_request_timeout_seconds"] = "must be between 1 and 120"
    if config.tts_default_mode not in {"off", "on_demand", "auto"}:
        errors["tts_default_mode"] = "must be off, on_demand, or auto"
    if config.tts_content not in {"summary", "verbatim"}:
        errors["tts_content"] = "must be summary or verbatim"
    if config.tts_engine not in {"edge", "sapi"}:
        errors["tts_engine"] = "must be edge or sapi"
    if not config.tts_edge_voice.strip():
        errors["tts_edge_voice"] = "must name an edge-tts voice, e.g. en-US-JennyNeural"
    if not re.fullmatch(r"[+-]\d{1,3}%", config.tts_edge_rate):
        errors["tts_edge_rate"] = "must look like +10% or -5%"
    if not re.fullmatch(r"[+-]\d{1,3}Hz", config.tts_edge_pitch):
        errors["tts_edge_pitch"] = "must look like +0Hz or -20Hz"
    if not -10 <= config.tts_sapi_rate <= 10:
        errors["tts_sapi_rate"] = "must be between -10 and 10"
    if not 64 <= config.tts_summary_max_tokens <= 2000:
        errors["tts_summary_max_tokens"] = "must be between 64 and 2000"
    if not 200 <= config.tts_verbatim_max_chars <= 40_000:
        errors["tts_verbatim_max_chars"] = "must be between 200 and 40000"
    if not 0 <= config.tts_daily_budget_usd <= 100:
        errors["tts_daily_budget_usd"] = "must be between 0 and 100"
    if not 10 <= config.tts_cache_mb <= 5000:
        errors["tts_cache_mb"] = "must be between 10 and 5000"
    if config.stt_engine not in {"sapi", "whisper"}:
        errors["stt_engine"] = "must be sapi or whisper"
    if not re.fullmatch(r"[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})?", config.stt_language):
        errors["stt_language"] = "must be a language tag such as en-US"
    if not config.stt_whisper_model.strip() or len(config.stt_whisper_model) > 120:
        errors["stt_whisper_model"] = "must name a Whisper model in 120 characters or fewer"
    if len(config.stt_routing_model) > 120:
        errors["stt_routing_model"] = (
            "must name a Whisper model in 120 characters or fewer, or be blank"
        )
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
    if config.theme not in THEMES:
        errors["theme"] = f"must be one of {', '.join(sorted(THEMES))}"
    if config.drawer_tab_display not in {"icon", "title"}:
        errors["drawer_tab_display"] = "must be icon or title"
    if config.utility_rail_display not in {"icon", "title"}:
        errors["utility_rail_display"] = "must be icon or title"
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
        cfg.harness_exe = {**default_harness_executables(), **configured_exe}
        cfg.harness_args = {**default_harness_args(), **configured_args}
        cfg.usage_commands = configured_usage
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
            # The automation token caps and the scan-timeline run budget were
            # sized for episodic observers and made the token axis bind at a
            # fraction of a cent, which silently stopped the scan timeline
            # mid-session. Lift only values that are still the untouched
            # schema-22 defaults, so a deliberately lowered cap survives.
            legacy_caps = {
                "automation_daily_token_budget": 200_000,
                "automation_daily_budget_usd": 2.0,
                "automation_rule_daily_token_budget": 50_000,
                "automation_rule_daily_budget_usd": 0.5,
                "automation_hourly_call_cap": 60,
                "automation_rule_hourly_call_cap": 20,
                "scan_timeline_run_token_budget": 100_000,
            }
            for key, legacy in legacy_caps.items():
                if key not in raw or raw[key] == legacy:
                    setattr(cfg, key, Config.__dataclass_fields__[key].default)
                    migrated = True
        if source_schema < 22 and "harness_setup_complete" not in raw:
            # The first-run harness panel is new. An existing config is by definition
            # not a first run, so mark setup complete on upgrade; only a brand-new
            # install (no config file, so this block never runs) shows the panel.
            cfg.harness_setup_complete = True
            migrated = True
    if not cfg.shell_profiles:
        if "shell_exe" not in raw:
            if IS_WINDOWS and shutil.which("pwsh.exe"):
                cfg.shell_exe = "pwsh.exe"
            elif not IS_WINDOWS:
                # A config file written on another host, or one predating POSIX
                # support, can carry a `powershell.exe` default this host cannot
                # start. Re-derive rather than inherit a shell that is not here.
                cfg.shell_exe = default_shell_executable()
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
