from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

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


def scrub_claude_session_markers(environment: Mapping[str, str]) -> dict[str, str]:
    """Drop parent-Claude session markers so terminals get a clean lineage."""
    return {
        key: value
        for key, value in environment.items()
        if key.upper() not in CLAUDE_SESSION_MARKERS
    }


@dataclass(frozen=True, slots=True)
class SpawnRequest:
    project_id: str
    backend: str | None = None
    profile_id: str | None = None
    executable: str | None = None
    argv: tuple[str, ...] = field(default_factory=tuple)
    name: str | None = None
    completion_mode: str = "interactive"

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
        return cls(
            project_id=project_id,
            backend=backend,
            profile_id=profile,
            executable=str(executable) if executable else None,
            argv=tuple(raw_argv),
            name=str(body["name"]) if body.get("name") else None,
            completion_mode=completion_mode,
        )
