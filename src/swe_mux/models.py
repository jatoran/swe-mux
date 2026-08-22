from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from .harness import Backend

SessionState = Literal["starting", "running", "working", "idle", "awaiting", "exited", "crashed"]

# Typed sub-reason for the "awaiting" state: a blocking permission approval, a
# question the agent asked (Q&A), an MCP/elicitation dialog, or a provider rate
# limit. Free-text state_detail stays display-only; this field is the contract.
AwaitingReason = Literal["approval", "question", "elicitation", "rate_limit", "authentication"]

# The standing-activity axis: an engagement that outlives the current turn — an
# armed /loop wakeup, a cron schedule, running background tasks, live subagents.
# Deliberately NOT states: an idle session with an armed loop is exactly as
# idle, and as deliverable, as one without. Annotations compose (a session can
# hold several) and every one either self-expires or is positively cleared.
StandingActivityKind = Literal["loop", "cron", "background_tasks", "subagents"]

# What mux may answer on the agent's behalf when the harness asks for tool
# permission. Three positions and no more: `wait` routes every request to the
# human (the default and the only one that changes nothing), `allowlisted`
# answers requests matching the Project's rules, `allow_all` answers everything
# the hard floor in `approvals.py` does not forbid. "Deny" is deliberately not a
# position — see that module's docstring.
ApprovalMode = Literal["wait", "allowlisted", "allow_all"]
APPROVAL_MODES: tuple[ApprovalMode, ...] = ("wait", "allowlisted", "allow_all")


@dataclass(slots=True)
class ApprovalPolicy:
    """The live auto-approval grant for one agent conversation.

    **Keyed on `agent_run_id`, not on the session.** A session outlives its
    task: `/clear`, `/resume`, Branch, and conversation rollover all start work
    the operator never granted anything for, and a grant that survived them
    would be the stuck-status bug class applied to authority. `run_id` is
    therefore compared on every decision and a mismatch reads as `wait`.

    Bounded the same way for the same reason: `expires_at` is always set for a
    non-`wait` mode, so a mode nobody remembers switching on decays by itself
    rather than waiting to be noticed.
    """

    mode: ApprovalMode = "wait"
    #: The conversation this grant was made against. None while mode is `wait`.
    run_id: str | None = None
    #: Wall clock. Always set for a non-`wait` mode; the grant reads as `wait`
    #: past it without needing anything to sweep.
    expires_at: float | None = None
    granted_at: float | None = None
    #: Free-text origin of the grant ("ui", "palette", "voice") for the ledger.
    set_by: str = ""
    #: The allow rules resolved from the Project at the moment the grant was
    #: made, rather than re-read per request. Two reasons, and both matter: the
    #: decision runs on the agent's critical path and must do no file I/O, and a
    #: grant is authorization of *the rules the operator saw*, so an edit to the
    #: committed Project file must not silently widen a grant already standing.
    #: Empty while mode is `wait` or `allow_all`, neither of which consults them.
    rules: list[str] = field(default_factory=list)
    #: Requests answered under this grant, and the last one, so the strip can
    #: say what the mode has actually been doing rather than only that it is on.
    auto_approved: int = 0
    #: How many requests this grant may answer in total, fixed when it was made.
    #: Carried on the grant rather than read from config per decision so the
    #: strip can render "4 of 200" and so lowering the setting cannot retroactively
    #: revoke a grant mid-task.
    max_auto: int = 200
    last_decision_at: float | None = None
    last_request: str | None = None
    #: Requests the floor refused to answer while the mode was on. Surfaced
    #: because "allow_all is on and it still asked me" is otherwise a bug report.
    floor_deferred: int = 0

    def snapshot(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_snapshot(cls, data: dict[str, Any]) -> ApprovalPolicy:
        known = set(cls.__dataclass_fields__)
        kwargs = {key: value for key, value in data.items() if key in known}
        try:
            policy = cls(**kwargs)
        except TypeError:
            return cls()
        if policy.mode not in APPROVAL_MODES:
            return cls()
        # A grant restored without its rules would silently become "allowlisted
        # with an empty allowlist", which reads as the feature being broken
        # rather than as the drift it is. Drop to `wait` instead, which is the
        # only safe direction and is visible in the strip.
        policy.rules = [rule for rule in policy.rules if isinstance(rule, str) and rule.strip()]
        if policy.mode == "allowlisted" and not policy.rules:
            return cls()
        return policy

    def effective_mode(self, run_id: str | None, now: float) -> ApprovalMode:
        """The mode that actually applies right now.

        Expiry and run-scoping are evaluated at read time rather than swept,
        because a sweep that does not run leaves authority standing while a
        read-time check cannot.
        """
        if self.mode == "wait":
            return "wait"
        if self.expires_at is not None and now >= self.expires_at:
            return "wait"
        if self.run_id and run_id and self.run_id != run_id:
            return "wait"
        if self.run_id and not run_id:
            return "wait"
        return self.mode


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
    """What Git says about the checkout a session is working in.

    **Every field here describes the checkout, not the session.** Sessions
    sharing a working tree share one measurement by construction: `git status`
    reports the whole repository regardless of which subdirectory it runs in, so
    two agents in one checkout cannot be told apart by anything Git can answer.
    ``root`` is served so a client can say so — a per-session row printing a
    per-checkout quantity invites reading it as "what this agent changed".
    """

    branch: str | None = None
    dirty: int = 0
    ahead: int = 0
    behind: int = 0
    #: Leaf directory name of the checkout when it is a *linked* worktree rather
    #: than the repository's primary checkout; None for the primary checkout and
    #: for anything outside a repository.
    worktree: str | None = None
    #: Lines added/removed against HEAD across staged and unstaged **tracked**
    #: changes. Untracked files are counted by ``dirty`` but contribute no lines
    #: here, because their content has never been compared to anything.
    #:
    #: ``None`` means "not measured" (no HEAD yet, or the diff failed); ``0``
    #: means "measured, and nothing changed". A display that conflates the two
    #: reports a clean tree for a repository it simply failed to read.
    added: int | None = None
    removed: int | None = None
    #: Absolute working-tree root. The identity of the checkout every other field
    #: here is about, and the only key by which two sessions can be known to share
    #: one. None outside a repository.
    root: str | None = None
    #: Comparison base this checkout is measured against, and the change set
    #: between that base's merge base with HEAD and the current working tree.
    #:
    #: This is the branch-scoped answer to "what has this work changed", where
    #: ``added``/``removed`` are the HEAD-scoped one. They differ the moment a
    #: session commits: committed work leaves the HEAD diff entirely and stays in
    #: this one, which is why a worktree-per-branch fleet reads +0 -0 without it.
    #:
    #: All four are ``None`` when no base resolves or the diff failed — never 0,
    #: which would claim a branch identical to its base.
    compare_ref: str | None = None
    compare_added: int | None = None
    compare_removed: int | None = None
    compare_files: int | None = None
    #: Exact checked-out commit. Unlike the display-only branch name, this is
    #: also the baseline used by durable session-to-commit provenance. It stays
    #: a checkout property: sessions sharing a worktree report the same HEAD.
    head: str | None = None


@dataclass(slots=True)
class SessionRecord:
    id: str
    name: str
    project_id: str
    backend: Backend
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
    #: Epoch seconds of the transition into ``state``. The mirror of
    #: ``Session.last_state_change_ts`` on the record, so a UI can age the current
    #: state without asking the daemon a second question. Adopted across daemon
    #: restarts with the rest of the record; a record restored from an older
    #: snapshot keeps 0.0 and is rendered as "unknown", never as "just now".
    state_since: float = 0.0
    #: Wall-clock duration of the last **completed** root turn, milliseconds.
    #: None until a turn has ended on this run. Reset with observation identity,
    #: because a duration from a replaced conversation is not this one's.
    last_turn_ms: float | None = None
    #: Epoch seconds the current root turn began; None while no turn is open.
    #: This, not ``state_since``, is what "how long has it been working" means:
    #: a turn survives every tool call and every approval blip inside it, while
    #: ``state_since`` restarts on each of them, so a busy agent's timer reset
    #: every few seconds and never reported the length of the actual work.
    turn_started_at: float | None = None
    #: Monotonic root-turn generation within this observation identity.
    #: Consumers can distinguish a restarted timer from an update to the same
    #: turn without inferring identity from timestamps.
    turn_epoch: int = 0
    #: Provider-native or mux-synthesized identity of the open root turn.
    #: None while no root turn is open or when the harness has not supplied an
    #: identity yet. Terminal evidence carrying a different id is stale and may
    #: not close the active generation.
    active_turn_id: str | None = None
    #: Epoch seconds when the operator first requested an unresolved interruption
    #: of the active root turn. This is intent, not proof of completion: delivery
    #: remains blocked until provider or PTY evidence closes the turn.
    interrupt_pending_at: float | None = None
    interrupt_pending_source: str | None = None
    #: Epoch seconds a **human** last submitted a request to this session; None
    #: when none has been observed on this run.
    #:
    #: Deliberately not the same question as ``turn_started_at``. A turn is one
    #: request-to-completion cycle, and plenty of them are opened by something
    #: other than a person: mux delivering an agent-authored queued message, or a
    #: Stop hook injecting a teammate message the instant the previous turn ends.
    #: A session can therefore be minutes into a fresh turn and an hour past
    #: anything its operator said, which is exactly the gap that made "how long
    #: has this been going" unanswerable from the turn alone.
    #:
    #: Stamped from the submit hook, because authorship is only knowable at the
    #: moment of delivery — the transcript records an injected prompt and a typed
    #: one identically. It survives a session-preserving restart on the snapshot
    #: and is left None on a cold adoption rather than guessed. Reset with
    #: observation identity, like the turn fields.
    last_human_prompt_at: float | None = None
    #: Epoch seconds the current stretch of *running* work began; None when no
    #: such stretch is open.
    #:
    #: The third answer to "how long has this been going", and the one the other
    #: two cannot give. A harness that dispatches background agents ends its root
    #: turn and hands off: ``turn_started_at`` goes None, ``last_turn_ms`` freezes
    #: at the length of the dispatching turn, and the row then reports a finished
    #: fragment of the request as though it were the whole of it — measured live
    #: 2026-08-19 as `10m` on a session 80 minutes into work whose subagents were
    #: still running.
    #:
    #: Latched, not tracked. It is stamped when a running annotation
    #: (``RUNNING_ACTIVITY_KINDS``) opens with none already latched, anchored to
    #: the turn that dispatched the work rather than to the annotation, because
    #: the request started when the operator asked for it and not when the first
    #: agent happened to register. It deliberately survives the gaps between
    #: phases: a workflow's subagent count reaches zero for seconds at a time
    #: between rounds, and re-anchoring there would report a multi-phase run as
    #: however long its newest phase has lasted.
    #:
    #: Cleared only when a root turn closes with nothing running — the main loop
    #: came back, finished, and left nothing behind, which is the one observable
    #: that means the request is over. Reset with observation identity, like the
    #: turn fields.
    running_work_since: float | None = None
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
    # Whether mux answers this conversation's tool-permission requests itself.
    # Run-scoped and self-expiring (see `ApprovalPolicy`); cleared wherever
    # observation identity resets, exactly like `standing_activity`.
    approval_policy: ApprovalPolicy = field(default_factory=ApprovalPolicy)
    tokens_in: int = 0
    tokens_out: int = 0
    tokens_cache_read: int = 0
    tokens_cache_write: int = 0
    cost_usd: float = 0.0
    context_window: int = 0
    context_pct: float = 0.0
    context_peak_pct: float = 0.0
    compaction_count: int = 0
    last_compaction_at: float | None = None
    compaction_capability: str | None = None
    compaction_confidence: str | None = None
    provider: str | None = None
    # OMP can use more than one provider in one conversation. Values are the
    # pseudonymous, linkable SHA-256 hashes OMP records, never raw account ids.
    provider_account_hashes: dict[str, str] = field(default_factory=dict)
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
    spawn_backend: Backend | None = None
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
    # Best-effort execution boundary derived from positive OSC 7 evidence or a
    # narrowly classified SSH authentication/transport frame. ``remote`` disables
    # local integrations and automatic delivery; malformed or absent telemetry
    # never asserts a boundary change.
    runtime_boundary: Literal["local", "remote", "unknown"] = "local"
    remote_authority: str | None = None
    remote_since: float | None = None
    remote_transport_state: Literal["connected", "authentication", "ended"] | None = None
    agent_run_id: str | None = None
    agent_run_started_at: float | None = None
    # Start of the current CLI process generation. Unlike agent_run_started_at,
    # this does not move when /clear or /new replaces the conversation.
    agent_loaded_at: float | None = None
    # Per-source content digest of the agent configuration this CLI generation
    # loaded (`agent_environment.capture_config_baseline`), captured once when
    # `agent_loaded_at` is set. The Agent Config tab reports drift by comparing
    # against it; empty means "no snapshot", which the tab shows as untracked
    # rather than as "nothing changed". A handful of short strings, so it costs
    # nothing to mirror to the supervisor with the rest of the record.
    agent_env_baseline: dict[str, str] = field(default_factory=dict)
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
    observation_diagnostic: str | None = None
    last_activity_ts: float = field(default_factory=time.time)
    # --- attention: turn completions, and the read mark against them ----------
    #
    # `last_activity_ts` above is a liveness signal: it moves on every byte the
    # PTY emits, which includes the full-screen repaint every SIGWINCH provokes
    # (see `Session.apply_geometry`) and the idle repaint traffic of a spinner or
    # a status footer. Deriving "the agent said something you have not read" from
    # it made resizing a window, collapsing the sidebar, or attaching a phone
    # indistinguishable from the agent speaking, and lit up whole projects at
    # once. `turn_seq` counts *semantic* turn completions instead - it advances
    # only where the status contract settles a working session or raises an
    # approval - so no amount of repainting can move it. It is compared as a
    # monotone integer rather than a timestamp, which also makes the read mark
    # immune to clock skew between the daemon and any client.
    turn_seq: int = 0
    last_turn_end_ts: float = 0.0
    last_turn_evidence: str | None = None
    # Highest `turn_seq` a human has acknowledged, held on the session rather
    # than in a browser so the mark follows the user across devices and survives
    # a reload - the discipline attention records already use for `read_at`.
    read_turn_seq: int = 0
    read_at: float | None = None
    # Set only by an explicit "mark unread" from a menu or the palette, and the
    # one thing allowed to move the mark backwards. `acknowledge_turns` is
    # monotone so a device that is behind can never un-read what another already
    # cleared; a human saying "I have not dealt with this yet" is the single case
    # that has to, and it also has to survive the dwell timer that would
    # otherwise re-acknowledge the very pane the menu was opened on. So the flag
    # carries both halves: the mark rolls back one turn, and implicit
    # acknowledgement is suppressed until the user reads it explicitly or the
    # agent completes another turn (which supersedes the pin - see
    # `note_turn_completion`).
    #
    # "Explicitly" is not only the menu item: a client also writes an explicit
    # read when the user returns to the pane the mark was set on, because the pin
    # is meant to survive that one visit rather than to become a flag only a
    # second menu click can clear (`sessionAttention.ts`, `trackPinVisits`).
    # Which panes are on screen is client state, so the daemon cannot make that
    # call itself - it only refuses the implicit shape, which is what keeps the
    # dwell timer of the marking visit from undoing the mark.
    unread_pin: bool = False
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
    # Restored from durable recovery data rather than from a process: the daemon
    # and its PTY owner both died without recording how this session ended, so it
    # comes back visible-but-dead instead of vanishing from the sidebar and the
    # layout. Deliberately a flag alongside `state="crashed"` rather than a new
    # `SessionState`: dozens of consumers gate on `state in {"exited","crashed"}`,
    # and a cold session must be excluded from every one of them (delivery,
    # auto-delivery, attention, identity claims, MCP) by construction. Only the
    # UI and the revive paths ever need to know the difference.
    cold: bool = False
    #: When this session was recovered, and what its recovery data could tell us.
    #: `cold_reason` names why it is cold (`daemon_lost`, `supervisor_lost`);
    #: `cold_terminal_at` is when its last terminal checkpoint was taken, which
    #: bounds how stale the replayed screen is, and is None when there is none.
    cold_since: float | None = None
    cold_reason: str | None = None
    cold_terminal_at: float | None = None
    #: Why no terminal bytes were kept for this session, when none were. An
    #: alternate-screen or repaint-heavy harness is excluded on purpose: its
    #: retained bytes are a differential frame stream that reconstructs to a
    #: blank or half-drawn screen, and repairing that needs a live child to
    #: pulse. Naming the reason is what lets the pane say so instead of looking
    #: broken.
    cold_terminal_skipped: str | None = None
    # The end reason to persist when this session terminates, set by a deliberate
    # end operation before it sends the exit sequence (Phase 7.6). It lets an
    # agent-initiated graceful end record `agent_ended` even when the CLI exits on
    # its own and the ordinary process-exit path is what marks the record. None
    # leaves the terminal path to classify the exit as it always has.
    requested_end_reason: str | None = None
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
        nested = {"git", "standing_activity", "approval_policy"}
        kwargs = {key: value for key, value in data.items() if key in known and key not in nested}
        record = cls(**kwargs)
        policy = data.get("approval_policy")
        if isinstance(policy, dict):
            # A grant survives a session-preserving daemon restart, which is a
            # routine operation here — losing it mid-task would silently return
            # the session to `wait` and read as the feature not working. Its own
            # expiry and run check still bound it on the other side.
            record.approval_policy = ApprovalPolicy.from_snapshot(policy)
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

    @property
    def agent_environment_cwd(self) -> Path:
        """The directory the CLI actually trusts, with a fallback that exists.

        The live cwd decides which project configuration layer wins. Shared by
        the inventory, the MCP tool fetch, and the drift baseline capture, so a
        baseline is never taken from a different project than the one the tab
        later describes.
        """
        cwd = Path(
            (self.runtime_cwd if self.runtime_cwd_live else None)
            or self.run_cwd
            or self.spawn_cwd
            or self.cwd
        )
        if not cwd.is_dir():
            cwd = Path(self.spawn_cwd or self.cwd)
        return cwd


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
    #: The shell launch profile `New terminal` uses in this Project.
    default_profile_id: str | None = None
    #: Per-harness launch profile this Project starts agent sessions with, keyed by
    #: backend name. Separate from `default_profile_id` because a Project genuinely
    #: has one answer per backend: the terminal default and the Claude default are
    #: unrelated choices, and one field would make selecting either clear the other.
    default_agent_profiles: dict[str, str] = field(default_factory=dict)
    #: Optional machine-local Git comparison ref. None means automatic inference.
    git_compare_ref: str | None = None
    resource_open_mode: Literal["dock", "popout"] | None = None
    sidebar_visible: bool = True
    #: Registration time, epoch seconds. 0 means unknown: databases that predate the
    #: column keep it when nothing in history dates the Project, and the sidebar's
    #: date ordering sorts those last rather than inventing a day for them.
    created_at: float = 0.0
    #: Latest explicit user use, epoch seconds. Shared by every client for the
    #: sidebar's Recently used ordering; 0 means no recorded use.
    last_used_at: float = 0.0

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
