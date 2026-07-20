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
    project_id: str
    backend: str
    native_session_id: str
    cwd: str
    exe: str
    args: list[str]
    shell_profile_id: str | None = None
    auto_named: bool = True
    pid: int = -1
    process_job_assignment: str = "unknown"
    created_at: float = field(default_factory=time.time)
    state: SessionState = "starting"
    state_detail: str | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    context_window: int = 0
    context_pct: float = 0.0
    context_peak_pct: float = 0.0
    compaction_count: int = 0
    last_compaction_at: float | None = None
    compaction_capability: str | None = None
    compaction_confidence: str | None = None
    model: str | None = None
    measurement_source: str | None = None
    parser_status: str = "not_applicable"
    parser_diagnostic: str | None = None
    parser_events_seen: int = 0
    parser_unknown_events: int = 0
    parser_unknown_signatures: dict[str, int] = field(default_factory=dict)
    parser_schema_version: str | None = None
    repository_id: str | None = None
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
    # Read-aloud generation mode and spoken-content override for this session.
    # None inherits the configured global default; both are volatile and die
    # with the live session.
    voice_mode: str | None = None
    voice_content: str | None = None
    startup_timing_ms: dict[str, float] = field(default_factory=dict)
    client_startup_timing_ms: dict[str, float] = field(default_factory=dict)

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
class ProjectRecord:
    id: str
    name: str
    root: str
    position: int
    group_id: str | None = None
    layout: dict[str, Any] | None = None
    default_backend: str | None = None
    layout_revision: int = 0
    default_profile_id: str | None = None
    resource_open_mode: Literal["dock", "popout"] | None = None
    sidebar_visible: bool = True

    def snapshot(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ProjectGroupRecord:
    id: str
    name: str
    position: int

    def snapshot(self) -> dict[str, Any]:
        return asdict(self)


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
