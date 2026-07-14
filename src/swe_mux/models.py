from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

SessionState = Literal["starting", "running", "working", "idle", "awaiting", "exited", "crashed"]


@dataclass(slots=True)
class GitState:
    branch: str | None = None
    dirty: int = 0
    ahead: int = 0
    behind: int = 0


@dataclass(slots=True)
class SessionRecord:
    id: str
    name: str
    space_id: str
    backend: str
    native_session_id: str
    cwd: str
    exe: str
    args: list[str]
    shell_profile_id: str | None = None
    auto_named: bool = True
    pid: int = -1
    created_at: float = field(default_factory=time.time)
    state: SessionState = "starting"
    state_detail: str | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    context_window: int = 0
    context_pct: float = 0.0
    context_peak_pct: float = 0.0
    model: str | None = None
    measurement_source: str | None = None
    parser_status: str = "not_applicable"
    parser_diagnostic: str | None = None
    parser_events_seen: int = 0
    project_id: str | None = None
    project_label: str | None = None
    project_root: str | None = None
    project_scope_id: str | None = None
    repo_group_id: str | None = None
    # A PTY may wander between projects. Spawn fields are daemon-resolved and
    # authoritative for shell-scoped behavior; runtime fields are untrusted
    # display telemetry; run fields are captured once for an agent invocation.
    spawn_cwd: str | None = None
    spawn_project_scope_id: str | None = None
    spawn_repo_group_id: str | None = None
    spawn_project_label: str | None = None
    spawn_project_root: str | None = None
    runtime_cwd: str | None = None
    runtime_cwd_live: bool = False
    runtime_cwd_source: str = "spawn"
    runtime_cwd_updated_at: float | None = None
    runtime_project_scope_id: str | None = None
    runtime_cwd_dropped: int = 0
    agent_run_id: str | None = None
    agent_run_started_at: float | None = None
    run_cwd: str | None = None
    run_project_scope_id: str | None = None
    run_repo_group_id: str | None = None
    last_activity_ts: float = field(default_factory=time.time)
    git: GitState = field(default_factory=GitState)
    pinned_attention: bool = False
    broadcast: bool = False

    def snapshot(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def trusted_scope_id(self) -> str | None:
        if self.agent_run_id:
            return self.run_project_scope_id or self.project_scope_id
        return self.spawn_project_scope_id or self.project_scope_id

    @property
    def trusted_cwd(self) -> str:
        return self.run_cwd if self.agent_run_id and self.run_cwd else self.spawn_cwd or self.cwd

    @property
    def git_cwd(self) -> str:
        if self.runtime_cwd_live and self.runtime_cwd:
            return self.runtime_cwd
        return self.spawn_cwd or self.cwd


@dataclass(slots=True)
class SpaceRecord:
    id: str
    name: str
    position: int
    layout: dict[str, Any] | None = None
    default_cwd: str | None = None
    default_backend: str | None = None
    layout_revision: int = 0
    default_profile_id: str | None = None
    anchor_mode: Literal["auto", "fixed", "none"] = "auto"
    anchor_project_scope_id: str | None = None
    anchor_revision: int = 0

    def snapshot(self) -> dict[str, Any]:
        result = asdict(self)
        # Anchor columns remain in the database only so older Phase 5.5 data can
        # be migrated safely. They are no longer part of the public space model.
        for key in ("anchor_mode", "anchor_project_scope_id", "anchor_revision"):
            result.pop(key, None)
        return result


@dataclass(slots=True)
class MuxEvent:
    ts: float
    session_id: str | None
    source: str
    type: str
    payload: dict[str, Any]
    seq: int = 0

    def snapshot(self) -> dict[str, Any]:
        return asdict(self)
