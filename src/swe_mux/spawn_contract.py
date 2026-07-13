from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class SpawnRequest:
    backend: str | None = None
    profile_id: str | None = None
    executable: str | None = None
    argv: tuple[str, ...] = field(default_factory=tuple)
    cwd: str | None = None
    space_id: str = "default"
    name: str | None = None
    worktree: dict[str, Any] | None = None

    @classmethod
    def parse(cls, body: dict[str, Any]) -> SpawnRequest:
        known = {
            "backend", "profile_id", "executable", "exe", "argv", "exe_args",
            "cwd", "space", "name", "worktree",
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
        worktree = body.get("worktree")
        if worktree is not None and not isinstance(worktree, dict):
            raise ValueError({"worktree": "must be an object"})
        return cls(
            backend=backend,
            profile_id=profile,
            executable=str(executable) if executable else None,
            argv=tuple(raw_argv),
            cwd=str(body["cwd"]) if body.get("cwd") else None,
            space_id=str(body.get("space") or "default"),
            name=str(body["name"]) if body.get("name") else None,
            worktree=worktree,
        )
