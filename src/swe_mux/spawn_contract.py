from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .harness import Backend, harness_for_command, is_agent_harness, is_backend

# A spawn may direct a session at a subdirectory of its project (a task that runs
# in ./frontend), never outside it, and may carry a bounded environment.
MAX_SPAWN_ENV = 64

# Environment markers a live Claude Code process stamps on its children to
# identify them as nested/child sessions. When swe-mux itself is (re)launched
# from inside an agent session — the designed redeploy workflow — these leak
# down to every terminal, and a `claude` started there then reports
# "Transcript saving is off — inherited CLAUDE_CODE_CHILD_SESSION marker",
# which also breaks swe-mux's transcript-based observation. Only per-process
# identity/lifecycle markers are scrubbed; deliberate user configuration
# (feature flags, ANTHROPIC_* credentials) passes through untouched.
#
# `CLAUDE_JOB_DIR` is the worst of them because the CLI treats its mere presence
# as proof that the process *is* that background job, with no check of its own
# session kind: `jobStateNameSync` reads `<job dir>/state.json`, adopts its
# `name`, and watches the file for changes (its sibling bg-only hook is guarded,
# this one is not). Every pane the daemon spawns then shows one dead agent's
# title — in every Project, not just this one — a `/rename` in any pane renames
# them all and stamps a `custom-title` into each transcript, the per-session
# `$CLAUDE_JOB_DIR/tmp` scratch dir is shared instead of private, and live
# sessions write exit-cause files into a finished job's directory. Upstream:
# anthropics/claude-code#86531 (confirmed on 2.1.233), #59848, #82199.
CLAUDE_SESSION_MARKERS = frozenset(
    {
        "CLAUDECODE",
        "CLAUDE_CODE_CHILD_SESSION",
        "CLAUDE_CODE_ENTRYPOINT",
        "CLAUDE_CODE_SESSION_ID",
        "CLAUDE_CODE_SSE_PORT",
        "CLAUDE_CODE_EXECPATH",
        "CLAUDE_JOB_DIR",
        "CLAUDE_PID",
        "CLAUDE_EFFORT",
    }
)


def infer_agent_executable_backend(
    executable: str | None, arguments: Sequence[str] | None
) -> Backend | None:
    """Identify a direct agent root from its retained executable contract.

    Answered from the registry (`harness_for_command`) rather than from a hardcoded
    pair of vendors. The hardcoded form recognized only Claude and Codex and returned
    ``None`` for everything else, which is a silent answer: the history repair that
    detects a row whose claimed backend disagrees with the executable actually
    launched simply never fired for omp, pi, or opencode.
    """
    name = harness_for_command(executable or "", arguments or ())
    return name if is_backend(name) else None


def scrub_claude_session_markers(environment: Mapping[str, str]) -> dict[str, str]:
    """Drop parent-Claude session markers so terminals get a clean lineage."""
    return {
        key: value
        for key, value in environment.items()
        if key.upper() not in CLAUDE_SESSION_MARKERS
    }


# The terminal on the far side of swe-mux's retained-byte WebSocket is always the
# browser's xterm.js client, never whatever launched the daemon. A frozen,
# tray-launched daemon inherits no terminal-emulator capability variables at all
# (so a spawned CLI sees neither TERM nor COLORTERM), while a daemon relaunched
# from inside a rich terminal inherits the *wrong* ones (that emulator's markers,
# describing a terminal the client is not). Either way, colour-capability
# detection in the spawned CLI (Claude Code, Codex, and every chalk/supports-color
# program) is left without a signal and prints monochrome — the whole pane renders
# in the terminal's default foreground. Describing the real terminal is therefore a
# property of the PTY, not of any one harness, so SessionManager applies it to
# every session and a newly added harness cannot silently regress to no-colour.
TERMINAL_CAPABILITY_ENV: Mapping[str, str] = {
    "TERM": "xterm-256color",
    "COLORTERM": "truecolor",
    "TERM_PROGRAM": "swe-mux",
    # Owned-but-empty rather than absent: a value we set shadows any inherited one,
    # and no CLI should key behaviour off a version/feature string swe-mux does not
    # advertise.
    "TERM_PROGRAM_VERSION": "",
    "TERM_FEATURES": "",
}

# Rich-emulator and terminal-multiplexer markers a frozen daemon may have inherited
# from whatever launched it. They describe the daemon's parent, not the xterm.js
# client, so a CLI must never conclude it is running under Windows Terminal, Kitty,
# Ghostty, WezTerm, iTerm2, VS Code, Alacritty, tmux, screen, Zellij, or CMUX.
# Empty values deliberately shadow any inherited marker.
TERMINAL_MARKER_SHADOWS: Mapping[str, str] = {
    "WT_SESSION": "",
    "KITTY_WINDOW_ID": "",
    "GHOSTTY_RESOURCES_DIR": "",
    "WEZTERM_PANE": "",
    "ITERM_SESSION_ID": "",
    "VSCODE_PID": "",
    "ALACRITTY_WINDOW_ID": "",
    "TMUX": "",
    "TMUX_PANE": "",
    "STY": "",
    "ZELLIJ": "",
    "ZELLIJ_PANE_ID": "",
    "ZELLIJ_SESSION_NAME": "",
    "CMUX_WORKSPACE_ID": "",
    "CMUX_SURFACE_ID": "",
    "CMUX_REMOTE_TRANSPORT": "",
}


def terminal_env() -> dict[str, str]:
    """Harness-agnostic environment describing swe-mux's xterm.js PTY.

    Every session is spawned with this so a CLI's colour detection sees a 24-bit
    xterm regardless of how (or from where) the daemon itself was launched. It is
    the lowest-precedence layer of a session's environment: an adapter's own spawn
    env, a user's shell profile, and a task's env all override it. Adapters add
    only their own namespaced tuning (e.g. OMP's ``PI_*`` variables) on top.
    """
    return {**TERMINAL_CAPABILITY_ENV, **TERMINAL_MARKER_SHADOWS}


# Colour capability alone is a signal a CLI may still ignore. Colour detection in
# the Node ecosystem (Claude Code via chalk/supports-color) gates on the stream
# being a TTY first — ``if (!isTTY && FORCE_COLOR === undefined) return 0`` — and
# swe-mux launches agents through a shim -> windowed frozen ``swe-mux.exe`` ->
# ``cmd.exe`` -> node chain that hides the ConPTY's TTY-ness from that check, so
# TERM/COLORTERM never get consulted and the pane renders monochrome. Rust CLIs
# (Codex, OMP) key off COLORTERM regardless and are unaffected, but the fix has to
# hold for every agent harness, present and future. FORCE_COLOR (Node) and
# CLICOLOR_FORCE (the BSD/Rust/Go convention) together force colour independent of
# TTY detection. This is scoped to agent harnesses on purpose: they are
# always-interactive TUIs that own the terminal, whereas a plain shell must keep
# honouring pipe semantics (a forced flag would leak ANSI into ``cmd > file``).
AGENT_FORCE_COLOR_ENV: Mapping[str, str] = {
    "FORCE_COLOR": "3",
    "CLICOLOR_FORCE": "1",
}


def session_terminal_env(backend: str) -> dict[str, str]:
    """The terminal environment a session of ``backend`` is spawned with.

    Every session gets :func:`terminal_env`; agent harnesses additionally get
    :data:`AGENT_FORCE_COLOR_ENV` because their launch chain can hide the PTY from
    a CLI's colour detection. This is the lowest-precedence layer of a session's
    environment — adapter, shell-profile, and task env all override it.
    """
    env = terminal_env()
    if is_agent_harness(backend):
        env.update(AGENT_FORCE_COLOR_ENV)
    return env


def base_session_env(environment: Mapping[str, str], backend: str) -> dict[str, str]:
    """The scrubbed daemon environment a session of ``backend`` inherits.

    Always drops parent-Claude session markers (:func:`scrub_claude_session_markers`) and
    inherited forced-colour flags. The latter are session policy rather than ambient user
    configuration: agent panes add the known values back through
    :func:`session_terminal_env`, while shells must keep pipe output free of forced ANSI.
    Agent harnesses additionally drop ``NO_COLOR``: those panes force colour
    (:func:`session_terminal_env`), so an ambient ``NO_COLOR`` — inherited when the
    daemon was (re)launched from inside an agent session, the designed redeploy
    flow — only contradicts the force. Node then warns (``NO_COLOR ignored due to
    FORCE_COLOR``) and a ``NO_COLOR``-first CLI (Codex strips colour while keeping
    bold/dim) refuses colour outright despite ``CLICOLOR_FORCE``. A ``NO_COLOR``
    override that a session's terminal env cannot add is instead removed here.
    A deliberate per-session opt-out still works through shell-profile or task env,
    which override this base. Shells keep an inherited ``NO_COLOR`` (no-color.org).
    """
    base = {
        key: value
        for key, value in scrub_claude_session_markers(environment).items()
        if key.upper() not in {"FORCE_COLOR", "CLICOLOR_FORCE"}
    }
    if is_agent_harness(backend):
        base = {key: value for key, value in base.items() if key.upper() != "NO_COLOR"}
    return base


WORKTREE_CWD_REFUSAL = "cwd must stay inside the Project root, or be a git worktree of it"


def resolve_listed_cwd(value: str, allowed: Mapping[str, str]) -> str:
    """Resolve a spawn cwd against an explicit allow-list of absolute directories.

    The escape hatch from :func:`resolve_contained_cwd`, and deliberately the *only*
    one: a parallel agent worktree is the same repository on another branch, so a
    session belongs there, but it lives outside the Project root and containment
    rejects it. ``allowed`` is keyed by casefolded resolved path and is expected to
    come from ``git worktree list`` — git itself is the authority on which paths are
    worktrees of a given repo, so this cannot be talked into an arbitrary directory
    the way accepting any absolute path could.

    Relative values are refused outright here: they are only meaningful against a
    root, and this path has none.
    """
    target = Path(value)
    if not target.is_absolute():
        raise ValueError(WORKTREE_CWD_REFUSAL)
    match = allowed.get(str(target.resolve(strict=False)).casefold())
    if not match:
        raise ValueError(WORKTREE_CWD_REFUSAL)
    return str(Path(match).resolve(strict=False))


def resolve_contained_cwd(value: str, root: Path) -> str:
    """Resolve a spawn/action cwd, refusing anything outside the project root.

    The containment check for both entry points: a Project Action step's declared cwd
    and a spawn request's ``cwd`` field. Relative values resolve against the root, and
    symlinks are collapsed before the comparison so a link inside the project cannot
    point a session at an arbitrary directory.

    Spawns may fall back to :func:`resolve_listed_cwd` when this refuses. Project
    Actions deliberately may not: an action is repo-authored script content, so its
    reach stays bounded by the Project root.
    """
    target = Path(value) if value else root
    if not target.is_absolute():
        target = root / target
    target = target.resolve(strict=False)
    try:
        target.relative_to(root.resolve(strict=False))
    except ValueError as exc:
        raise ValueError("cwd must stay inside the Project root") from exc
    return str(target)


def parse_spawn_env(value: Any) -> dict[str, str]:
    """Validate an environment mapping supplied with a spawn or action step."""
    if value is None:
        return {}
    if not isinstance(value, dict) or len(value) > MAX_SPAWN_ENV:
        raise ValueError(f"env must be an object with at most {MAX_SPAWN_ENV} entries")
    result: dict[str, str] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not key or "=" in key or "\0" in key:
            raise ValueError("env names must be non-empty strings without '=' or NUL")
        if not isinstance(item, (str, int, float, bool)):
            raise ValueError("env values must be strings or scalar values")
        text = str(item)
        if "\0" in text:
            raise ValueError("env values cannot contain NUL")
        result[key] = text
    return result


@dataclass(frozen=True, slots=True)
class SpawnRequest:
    project_id: str
    backend: str | None = None
    profile_id: str | None = None
    executable: str | None = None
    argv: tuple[str, ...] = field(default_factory=tuple)
    name: str | None = None
    completion_mode: str = "interactive"
    # Both are project-relative capabilities: ``cwd`` is containment-checked
    # against the owning project's root by the spawn handler, which is the only
    # place that knows it.
    cwd: str | None = None
    env: Mapping[str, str] = field(default_factory=dict)
    # A new agent session's seed prompt as text, with no argv length ceiling:
    # the spawn handler turns it into an argv prompt directly, or stages an
    # over-bound body into the workspace with a short reader prompt
    # (`prompt_queue.stage_seed_argv`). Agent backends only.
    seed_text: str | None = None

    @classmethod
    def parse(cls, body: dict[str, Any]) -> SpawnRequest:
        known = {
            "backend",
            "profile_id",
            "executable",
            "exe",
            "argv",
            "exe_args",
            "project_id",
            "project",
            "name",
            "completion_mode",
            "cwd",
            "env",
            "seed_text",
        }
        unknown = set(body) - known
        if unknown:
            raise ValueError({key: "unknown spawn field" for key in sorted(unknown)})
        backend = str(body["backend"]) if body.get("backend") else None
        profile = str(body["profile_id"]) if body.get("profile_id") else None
        executable = body.get("executable", body.get("exe"))
        raw_argv = body.get("argv", body.get("exe_args", []))
        if not isinstance(raw_argv, list) or not all(isinstance(item, str) for item in raw_argv):
            raise ValueError({"argv": "must be an array of strings"})
        if backend and backend != "shell" and not is_agent_harness(backend):
            raise ValueError({"backend": "must be shell or a registered agent"})
        if profile and executable:
            raise ValueError({"executable": "cannot be combined with profile_id"})
        if executable is not None and not str(executable).strip():
            raise ValueError({"executable": "cannot be empty"})
        project_id = str(body.get("project_id") or body.get("project") or "").strip()
        if not project_id:
            raise ValueError({"project_id": "is required"})
        completion_mode = str(body.get("completion_mode") or "interactive")
        if completion_mode not in {"interactive", "one_shot"}:
            raise ValueError({"completion_mode": "must be interactive or one_shot"})
        raw_cwd = body.get("cwd")
        if raw_cwd is not None and (not isinstance(raw_cwd, str) or not raw_cwd.strip()):
            raise ValueError({"cwd": "must be a non-empty string"})
        try:
            environment = parse_spawn_env(body.get("env"))
        except ValueError as exc:
            raise ValueError({"env": str(exc)}) from exc
        raw_seed = body.get("seed_text")
        if raw_seed is not None:
            if not isinstance(raw_seed, str) or not raw_seed.strip():
                raise ValueError({"seed_text": "must be a non-empty string"})
            if len(raw_seed) > 500_000:
                raise ValueError({"seed_text": "must contain at most 500000 characters"})
        return cls(
            project_id=project_id,
            backend=backend,
            profile_id=profile,
            executable=str(executable) if executable else None,
            argv=tuple(raw_argv),
            name=str(body["name"]) if body.get("name") else None,
            completion_mode=completion_mode,
            cwd=raw_cwd.strip() if isinstance(raw_cwd, str) else None,
            env=environment,
            seed_text=raw_seed,
        )
