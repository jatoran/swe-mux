from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

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
CLAUDE_SESSION_MARKERS = frozenset(
    {
        "CLAUDECODE",
        "CLAUDE_CODE_CHILD_SESSION",
        "CLAUDE_CODE_ENTRYPOINT",
        "CLAUDE_CODE_SESSION_ID",
        "CLAUDE_CODE_SSE_PORT",
        "CLAUDE_CODE_EXECPATH",
        "CLAUDE_PID",
        "CLAUDE_EFFORT",
    }
)


def infer_agent_executable_backend(
    executable: str | None, arguments: Sequence[str] | None
) -> str | None:
    """Identify a direct agent root from its retained executable contract."""
    executable_name = Path(executable or "").name.casefold()
    entrypoint = (
        str(arguments[0]).replace("\\", "/").casefold() if arguments else ""
    )
    if executable_name in {"codex", "codex.exe", "codex.cmd", "codex.ps1"} or (
        "@openai/codex/" in entrypoint and entrypoint.endswith("/codex.js")
    ):
        return "codex"
    if executable_name in {"claude", "claude.exe", "claude.cmd", "claude.ps1"} or (
        "@anthropic-ai/claude-code/" in entrypoint and entrypoint.endswith("/cli.js")
    ):
        return "claude"
    return None


def scrub_claude_session_markers(environment: Mapping[str, str]) -> dict[str, str]:
    """Drop parent-Claude session markers so terminals get a clean lineage."""
    return {
        key: value
        for key, value in environment.items()
        if key.upper() not in CLAUDE_SESSION_MARKERS
    }


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
        if backend and backend not in {"shell", "claude", "codex"}:
            raise ValueError({"backend": "must be shell, claude, or codex"})
        if profile and backend in {"claude", "codex"}:
            raise ValueError({"profile_id": "shell profiles cannot be used with agent backends"})
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
