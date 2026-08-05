from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

SessionState = Literal["starting", "running", "working", "idle", "awaiting", "exited", "crashed"]

# Typed sub-reason for the "awaiting" state: a blocking permission approval, a
# question the agent asked (Q&A), an MCP/elicitation dialog, or a provider rate
# limit. Free-text state_detail stays display-only; this field is the contract.
AwaitingReason = Literal["approval", "question", "elicitation", "rate_limit"]

# The standing-activity axis: an engagement that outlives the current turn — an
# armed /loop wakeup, a cron schedule, running background tasks, live subagents.
# Deliberately NOT states: an idle session with an armed loop is exactly as
# idle, and as deliverable, as one without. Annotations compose (a session can
# hold several) and every one either self-expires or is positively cleared.
StandingActivityKind = Literal["loop", "cron", "background_tasks", "subagents"]


@dataclass(slots=True)
class StandingActivity:
    kind: StandingActivityKind
    # Same vocabulary as transition sources: cli-state|hook|transcript|pty|process.
    source: str
    # e.g. "transcript:ScheduleWakeup", "hook:SubagentStart".
    evidence: str
    since: float
    # Self-expiry; None means "until positively cleared". A wrong annotation must
    # decay on its own — the lesson of every stuck-status incident here.
    expires_at: float | None = None
    count: int = 1
    detail: str | None = None

    def snapshot(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_snapshot(cls, data: dict[str, Any]) -> StandingActivity | None:
        known = set(cls.__dataclass_fields__)
        kwargs = {key: value for key, value in data.items() if key in known}
        try:
            return cls(**kwargs)
        except TypeError:
            # A snapshot missing required fields (schema drift) is dropped rather
            # than poisoning adoption of the whole record.
            return None


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
    # OS creation time of the root process, captured at spawn. A PID alone is not
    # an identity on Windows — it is recycled aggressively — and exited sessions
    # are retained with their pid intact, so evidence collection pairs the two.
    root_started_at: float | None = None
    process_job_assignment: str = "unknown"
    created_at: float = field(default_factory=time.time)
    state: SessionState = "starting"
    state_detail: str | None = None
    # Set whenever state == "awaiting"; cleared by every transition elsewhere.
    awaiting_reason: str | None = None
    # The idle-axis sibling of `awaiting_reason`. `waiting_on_background` means
    # the turn genuinely ended (the composer accepts input, delivery is safe) but
    # the agent has background work running and will resume itself. Rendering that
    # as a plain "ready · turn complete" is true and misleading at the same time:
    # it invites the user to treat the session as finished. Cleared by every
    # transition off `idle`.
    idle_reason: str | None = None
    # The fifth status axis (state / awaiting_reason / idle_reason /
    # delivery_state / this): standing engagements that outlive the turn.
    # Run-scoped — every site that resets observation identity clears it.
    standing_activity: list[StandingActivity] = field(default_factory=list)
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
    # Immutable root-process identity. ``backend`` and ``native_session_id`` may
    # temporarily describe an agent launched inside a shell, but lifecycle
    # hooks from nested child CLIs must never replace the provider that owns the
    # PTY itself. Optional defaults keep supervisor snapshots from older daemons
    # adoptable; SessionManager reconstructs them from the retained spawn argv.
    spawn_backend: str | None = None
    spawn_native_session_id: str | None = None
    # Set only when this PTY was spawned to continue a conversation that already
    # owns a history row: Claude's ``--resume`` appends to the same transcript
    # under the same conversation id, so the pane inherits that run instead of
    # opening a second row over one file. Immutable spawn evidence, which is what
    # lets the adoption path tell a legitimately inherited ``agent_run_id`` from
    # the misattribution it repairs — the role ``agent_run_seq`` plays for a
    # rollover. It never moves; a later rollover mints a run of the pane's own
    # and the inheritance simply stops matching.
    spawn_agent_run_id: str | None = None
    runtime_cwd: str | None = None
    runtime_cwd_live: bool = False
    runtime_cwd_source: str = "spawn"
    runtime_cwd_updated_at: float | None = None
    runtime_project_scope_id: str | None = None
    runtime_cwd_dropped: int = 0
    agent_run_id: str | None = None
    agent_run_started_at: float | None = None
    # Start of the current CLI process generation. Unlike agent_run_started_at,
    # this does not move when /clear or /new replaces the conversation.
    agent_loaded_at: float | None = None
    # Which agent run of this session this is: 0 is the run the session spawned
    # (or was promoted into), and every in-CLI conversation replacement (`/clear`,
    # `/new`) increments it. A root agent's run id is otherwise indistinguishable
    # from a corrupted one — the adoption path repairs `agent_run_id != id` back to
    # the session id — so the counter is what makes a legitimately rolled run
    # survive a daemon restart instead of being quarantined as misattribution.
    agent_run_seq: int = 0
    run_cwd: str | None = None
    run_project_scope_id: str | None = None
    run_repo_group_id: str | None = None
    # Set when the followed transcript has gone quiet while this PTY is still
    # producing output and no switch could be corroborated: the conversation moved
    # somewhere we cannot prove. Observation then fails closed — hooks resume
    # driving state and delivery blocks — rather than reporting a dead
    # conversation's status as live.
    observation_stale_since: float | None = None
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
    completion_mode: Literal["interactive", "one_shot"] = "interactive"
    exit_code: int | None = None
    # True only for Project Action / task-launched shells, whose exact spawn argv
    # is retained on this record and can be replayed in place. Agent and plain
    # shell sessions leave this False so their rails never show Relaunch.
    relaunchable: bool = False
    # Daemon-supplied environment for a task shell, retained so Relaunch can
    # replay it. Carried through snapshot()/from_snapshot() so a relaunch still
    # works after a daemon restart adopts the record. Task env is authored in the
    # project's own task files and was equally visible in the retained argv
    # before, so this changes legibility, not exposure.
    spawn_env: dict[str, str] = field(default_factory=dict)

    def snapshot(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_snapshot(cls, data: dict[str, Any]) -> SessionRecord:
        """Rebuild a record from a ``snapshot()`` dict persisted by another daemon.

        Tolerant of schema drift in both directions: unknown keys from a newer
        daemon are dropped, and keys a newer daemon added keep their defaults
        when adopting metadata written by an older one.
        """
        known = set(cls.__dataclass_fields__)
        nested = {"git", "standing_activity"}
        kwargs = {key: value for key, value in data.items() if key in known and key not in nested}
        record = cls(**kwargs)
        git = data.get("git")
        if isinstance(git, dict):
            record.git = GitState(
                **{k: v for k, v in git.items() if k in GitState.__dataclass_fields__}
            )
        standing = data.get("standing_activity")
        if isinstance(standing, list):
            record.standing_activity = [
                activity
                for item in standing
                if isinstance(item, dict)
                and (activity := StandingActivity.from_snapshot(item)) is not None
            ]
        return record

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
    #: Optional machine-local Git comparison ref. None means automatic inference.
    git_compare_ref: str | None = None
    resource_open_mode: Literal["dock", "popout"] | None = None
    sidebar_visible: bool = True
    #: Registration time, epoch seconds. 0 means unknown: databases that predate the
    #: column keep it when nothing in history dates the Project, and the sidebar's
    #: date ordering sorts those last rather than inventing a day for them.
    created_at: float = 0.0

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
