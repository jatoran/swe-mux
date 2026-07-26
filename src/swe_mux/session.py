from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import sqlite3
import time
import uuid
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from .adapters import BackendAdapter, SpawnOptions
from .event_bus import EventBus
from .git_projects import ProjectIdentity, resolve_project
from .history import HistoryIndex
from .models import GitState, SessionRecord, SessionState
from .pty_host import PtyHost, merge_environment
from .runtime_cwd import Osc7Parser, local_directory_from_osc7
from .scrollback import ScrollbackBuffer
from .spawn_contract import scrub_claude_session_markers
from .supervisor_client import RemotePtyHost, SupervisorClient, host_for_adoption
from .win_jobobj import ReaperJob

log = logging.getLogger(__name__)

# How often the observer looks for the agent having moved to another transcript,
# how long the current transcript must be quiet first, and how fresh the
# replacement must be to count as actively written.
TRANSCRIPT_SWITCH_POLL_SECONDS = 2.0
TRANSCRIPT_SWITCH_QUIET_SECONDS = 5.0
TRANSCRIPT_SWITCH_FRESH_SECONDS = 5.0

# A shell prompt rendered while a session is promoted means the nested CLI has
# exited (the shell is otherwise blocked on it). Prompts within the grace window
# of promotion are pipeline stragglers from before/around the launch.
AGENT_EXIT_PROMPT_GRACE_SECONDS = 3.0
AGENT_EXIT_TRANSCRIPT_QUIET_SECONDS = 2.0
AGENT_EXIT_CONFIRM_ATTEMPTS = 10
AGENT_EXIT_CHECK_INTERVAL_SECONDS = 1.0

# Codex currently has no startup hook and does not create its rollout until the
# first submitted turn.  A short quiet period after live PTY output is therefore
# the last-resort signal that its interactive prompt has settled.
AGENT_STARTUP_QUIET_SECONDS = 1.0

# The transcript observer only ever returns by raising; if it does, observation
# stops and state freezes.  It is supervised and restarted with capped backoff so
# a single malformed record can never permanently strand a session as "working".
OBSERVER_RESTART_BACKOFF_MIN_SECONDS = 0.5
OBSERVER_RESTART_BACKOFF_MAX_SECONDS = 30.0

# The quiescence watchdog is the safety net behind every end-of-turn signal
# (Stop hook, turn_duration, end_turn).  When an agent has been "working"/
# "awaiting" with no state change and a quiet transcript, it re-derives the true
# state from the transcript tail and, only when that tail proves the turn is over,
# forces the session idle.  A tool in flight never reads as over, so a genuinely
# long tool call is never cut short.
STATE_WATCHDOG_POLL_SECONDS = 5.0
# When the transcript tail *proves* the turn ended, a residual "working" is a
# stale hook or a missed close, not real work, so recover quickly. The longer
# window only guarded against cutting a live turn short, which the tail check
# already prevents (an in-flight tool reads "open").
STATE_WATCHDOG_ENDED_STUCK_SECONDS = 6.0
STATE_WATCHDOG_TRANSCRIPT_QUIET_SECONDS = 3.0
# The PTY backstop is a last resort for when the transcript carries no terminal
# record — schema drift ("unknown") or a tail left mid-tool with no closing
# marker ("open", e.g. an interrupt/crash before its record landed, or an
# observer stuck on the wrong sibling transcript). It fires only after a longer
# stall and only when the CLI is provably sitting at its idle input prompt.
STATE_WATCHDOG_PTY_STUCK_SECONDS = 60.0
# An "awaiting" that the user has already answered is invisible to the hook that
# raised it: nothing fires when a permission dialog is dismissed. The PTY is this
# session's own ground truth for that — once the CLI is back to its working
# spinner the block is provably gone. The short guard only lets the dialog finish
# painting after the notification, so a real prompt is never cleared.
STATE_WATCHDOG_AWAITING_RESUME_SECONDS = 5.0
# Per-session ring buffer of recent state transitions and faults, surfaced by the
# state-log debug endpoint so a frozen session is diagnosable after the fact.
STATE_TRANSITION_LOG_LIMIT = 64
# Real state changes get their own ring: one busy turn emits dozens of same-state
# detail updates ("working · Read" → "working · Bash"), which would otherwise
# evict the transitions that actually explain how a session reached its state.
STATE_CHANGE_LOG_LIMIT = 64

# Status contract: which observation sources may set each user-visible state.
# Positive evidence only — ambiguity resolves to the conservative prior, never a
# guessed active state. A transition from a source outside its state's set is a
# contract violation: it is still applied (conservatively refusing could strand a
# session) but ledgered and counted so the corpus can assert zero occurrences.
STATE_EVIDENCE_SOURCES: dict[str, frozenset[str]] = {
    # Lifecycle bookkeeping the daemon itself owns (spawn/promotion/demotion).
    "starting": frozenset({"daemon"}),
    "running": frozenset({"daemon"}),
    # Active states require provider evidence; the PTY may never invent work.
    # "watchdog-pty" is admitted for `working` under one narrow rule enforced by
    # watchdog_decision: it may only resolve an already-answered `awaiting` back
    # into the turn that is still running, never start a turn from `idle`.
    "working": frozenset({"transcript", "hook", "watchdog-pty"}),
    "awaiting": frozenset({"transcript", "hook"}),
    # Idle may be proven (transcript/hook boundary) or a bounded inferred
    # recovery (startup-quiet PTY fallback, quiescence watchdog, PTY backstop).
    "idle": frozenset({"transcript", "hook", "pty", "watchdog", "watchdog-pty", "daemon"}),
    # Terminal states come from the PTY (process ground truth) or the daemon.
    "exited": frozenset({"pty", "daemon"}),
    "crashed": frozenset({"pty", "daemon"}),
}

# Sources whose transitions are recovery inferences, never the primary path for a
# healthy session. Everything else is proven evidence (hook/transcript/
# notification/process). The startup-quiet PTY fallback passes inferred=True
# explicitly because "pty" is also the proven source for process exits.
INFERRED_TRANSITION_SOURCES = frozenset({"watchdog", "watchdog-pty"})

# Bound on the inferred share of turn terminals before status health alarms.
# A healthy fleet reaches terminal status by proven evidence; inferred
# recoveries are defects being absorbed, and a rising rate is a regression.
STATUS_HEALTH_MAX_INFERRED_TERMINAL_RATIO = 0.05
STATUS_HEALTH_MIN_TERMINALS_FOR_RATIO_ALARM = 20
# Stuck-active alarm. Time in state alone does NOT mean stuck — a single agent
# turn legitimately stays "working" for many minutes while tools run, and an
# elapsed-time-only bound alarms on every healthy long turn (observed live).
# The signal is the absence of *evidence*: no ledger entry at all — not even a
# tool detail change — for this long while the session claims to be active.
STATUS_HEALTH_STUCK_ACTIVE_SECONDS = 900.0


def session_status_health(session: Any, *, now: float | None = None) -> dict[str, Any]:
    """Per-session status-health metrics; shared by Session and the replay harness."""
    now = time.time() if now is None else now
    counters = dict(session.status_health_counters)
    return {
        "counters": counters,
        "watchdog_recoveries": session.watchdog_recoveries,
        "watchdog_recovery_actions": dict(session.watchdog_recovery_actions),
        "observer_restarts": getattr(session, "observer_restart_count", 0),
        "reopen_blocked": counters.get("reopen_blocked", 0),
        "contract_violations": counters.get("contract_violations", 0),
        "terminals": {
            "proven": counters.get("terminal_proven", 0),
            "inferred": counters.get("terminal_inferred", 0),
        },
        "terminal_latencies": list(session.terminal_latencies),
        "seconds_in_state": round(max(0.0, now - session.last_state_change_ts), 3),
        # Time since ANY observation landed (including same-state tool detail
        # updates). Long time-in-state is normal for a working turn; long
        # time-since-evidence is what indicates a stuck session.
        "seconds_since_evidence": round(
            max(0.0, now - getattr(session, "last_evidence_ts", session.last_state_change_ts)), 3
        ),
    }


def fleet_status_health(sessions: Any, *, now: float | None = None) -> dict[str, Any]:
    """Aggregate per-session status health into the fleet soak signal.

    The alarm fires when inferred recoveries exceed the healthy bound, when the
    status contract was violated, or when a session has sat in an active state
    past the stuck bound. This is the boundary the soak matrix asserts on.
    """
    now = time.time() if now is None else now
    per_session: list[dict[str, Any]] = []
    proven_terminals = 0
    inferred_terminals = 0
    watchdog_recoveries = 0
    recovery_actions: dict[str, int] = {}
    reopen_blocked = 0
    contract_violations = 0
    observer_restarts = 0
    stuck_sessions: list[str] = []
    for session in sessions:
        record = session.record
        if record.backend not in {"claude", "codex"}:
            continue
        health = session.status_health()
        per_session.append(
            {
                "id": record.id,
                "backend": record.backend,
                "state": record.state,
                "awaiting_reason": getattr(record, "awaiting_reason", None),
                **health,
            }
        )
        proven_terminals += health["terminals"]["proven"]
        inferred_terminals += health["terminals"]["inferred"]
        watchdog_recoveries += health["watchdog_recoveries"]
        for action, count in health["watchdog_recovery_actions"].items():
            recovery_actions[action] = recovery_actions.get(action, 0) + count
        reopen_blocked += health["reopen_blocked"]
        contract_violations += health["contract_violations"]
        observer_restarts += health["observer_restarts"]
        if (
            record.state in {"working", "awaiting"}
            and health["seconds_since_evidence"] > STATUS_HEALTH_STUCK_ACTIVE_SECONDS
        ):
            stuck_sessions.append(record.id)
    terminals = proven_terminals + inferred_terminals
    inferred_ratio = inferred_terminals / terminals if terminals else 0.0
    alarm_reasons: list[str] = []
    if (
        terminals >= STATUS_HEALTH_MIN_TERMINALS_FOR_RATIO_ALARM
        and inferred_ratio > STATUS_HEALTH_MAX_INFERRED_TERMINAL_RATIO
    ):
        alarm_reasons.append("inferred_terminal_ratio_exceeded")
    if contract_violations:
        alarm_reasons.append("status_contract_violation")
    if stuck_sessions:
        alarm_reasons.append("session_stuck_active")
    return {
        "terminals": {
            "proven": proven_terminals,
            "inferred": inferred_terminals,
            "inferred_ratio": round(inferred_ratio, 4),
        },
        "watchdog_recoveries": watchdog_recoveries,
        "watchdog_recovery_actions": recovery_actions,
        "reopen_blocked": reopen_blocked,
        "contract_violations": contract_violations,
        "observer_restarts": observer_restarts,
        "stuck_sessions": stuck_sessions,
        "bounds": {
            "max_inferred_terminal_ratio": STATUS_HEALTH_MAX_INFERRED_TERMINAL_RATIO,
            "min_terminals_for_ratio_alarm": STATUS_HEALTH_MIN_TERMINALS_FOR_RATIO_ALARM,
            "stuck_active_seconds": STATUS_HEALTH_STUCK_ACTIVE_SECONDS,
        },
        "alarm": bool(alarm_reasons),
        "alarm_reasons": alarm_reasons,
        "sessions": per_session,
    }


def transition_proof(source: str, inferred: bool | None = None) -> str:
    """Classify a transition as proven evidence or a recovery inference."""
    if inferred is None:
        inferred = source in INFERRED_TRANSITION_SOURCES
    return "inferred" if inferred else "proven"


def apply_state_transition(
    session: Any,
    state: SessionState,
    detail: str | None,
    *,
    source: str,
    evidence: str | None = None,
    inferred: bool | None = None,
    awaiting_reason: str | None = None,
    force: bool = False,
    now: float | None = None,
    monotonic_now: float | None = None,
) -> bool:
    """Single implementation of the status-transition contract.

    Both the live Session and the deterministic replay harness apply transitions
    through this function, so arbitration, the typed ledger entry, and the
    proven/inferred health counters cannot drift between production and the
    golden corpus. Returns True when the visible state actually changed.
    """
    now = time.time() if now is None else now
    monotonic_now = time.monotonic() if monotonic_now is None else monotonic_now
    if force:
        # Authoritative evidence (interrupt/abort markers, process exit,
        # lifecycle ownership changes) reclaims arbitration from any source.
        session.state_source_priority = -1
    priority = {"pty": 0, "transcript": 1, "hook": 2}.get(source, 0)
    if priority < session.state_source_priority:
        return False
    record = session.record
    if state != "awaiting":
        awaiting_reason = None
    previous = record.state
    previous_awaiting = getattr(record, "awaiting_reason", None)
    changed = (
        previous != state
        or record.state_detail != detail
        or previous_awaiting != awaiting_reason
    )
    session.state_source_priority = priority
    if not changed:
        return False
    proof = transition_proof(source, inferred)
    seconds_in_previous: float | None = None
    if previous != state:
        previous_change_monotonic = getattr(session, "last_state_change_monotonic", None)
        if previous_change_monotonic is not None:
            seconds_in_previous = max(0.0, monotonic_now - previous_change_monotonic)
        session.last_state_change_ts = now
        session.last_state_change_monotonic = monotonic_now
    # Any accepted transition — including a same-state detail update while tools
    # run — is evidence the session is being observed. Absence of this, not time
    # in state, is what makes a session stuck.
    session.last_evidence_ts = now
    # When a block is raised, remember when: only evidence provably newer than
    # this may clear it (the ordered transcript lags the hook side channel, so a
    # record written just before the prompt can arrive just after it).
    observation_state = getattr(session, "observation_state", None)
    if isinstance(observation_state, dict):
        if state == "awaiting":
            observation_state["awaiting_since"] = now
        elif previous == "awaiting":
            observation_state.pop("awaiting_since", None)
    # Guarded so this stays callable as a pure arbitration contract by
    # lightweight stubs that carry no diagnostics buffer.
    entry = {
        "ts": now,
        "monotonic": monotonic_now,
        "kind": "transition",
        "previous": previous,
        "state": state,
        "detail": detail,
        "awaiting_reason": awaiting_reason,
        "source": source,
        "priority": priority,
        "evidence": evidence,
        "proof": proof,
        "allowed_source": source in STATE_EVIDENCE_SOURCES.get(state, frozenset()),
        "seconds_in_previous": (
            round(seconds_in_previous, 3) if seconds_in_previous is not None else None
        ),
    }
    ledger = getattr(session, "state_transitions", None)
    if ledger is not None:
        ledger.append(entry)
    # Real state changes also go to their own ring so a busy turn's detail churn
    # cannot evict the history that explains how the session got here.
    if previous != state:
        changes = getattr(session, "state_changes", None)
        if changes is not None:
            changes.append(entry)
    counters = getattr(session, "status_health_counters", None)
    if counters is not None and previous != state:
        counters["transitions"] = counters.get("transitions", 0) + 1
        counters[proof] = counters.get(proof, 0) + 1
        if proof == "inferred":
            key = f"inferred:{source}"
            counters[key] = counters.get(key, 0) + 1
        if source not in STATE_EVIDENCE_SOURCES.get(state, frozenset()):
            counters["contract_violations"] = counters.get("contract_violations", 0) + 1
        if previous in {"working", "awaiting"} and state in {"idle", "exited", "crashed"}:
            counters[f"terminal_{proof}"] = counters.get(f"terminal_{proof}", 0) + 1
            latencies = getattr(session, "terminal_latencies", None)
            if latencies is not None and seconds_in_previous is not None:
                latencies.append({"proof": proof, "seconds": round(seconds_in_previous, 3)})
    record.state = state
    record.state_detail = detail
    if hasattr(record, "awaiting_reason"):
        record.awaiting_reason = awaiting_reason
    return True


# Exit codes that signal a clean or intentionally-interrupted shutdown rather
# than a crash. 0 is a normal exit; 130 is the POSIX 128+SIGINT convention;
# STATUS_CONTROL_C_EXIT (0xC000013A) is what Windows reports for a console app
# terminated by Ctrl+C. Quitting an interactive agent (double Ctrl+C, /exit)
# lands on one of these, so it must not be surfaced as "crashed".
CLEAN_EXIT_CODES = frozenset({0, 130, 0xC000013A, -1073741510})


def terminal_exit_outcome(
    completion_mode: str, *, stopping: bool, exit_code: int | None, reason: str
) -> tuple[SessionState, str, str | None]:
    """Map a PTY root exit to the user-visible terminal lifecycle."""
    completed = completion_mode == "one_shot" and exit_code == 0
    # An unreadable exit code (None) is not treated as a crash: we only flag a
    # crash when the process reports a code that is neither clean nor an interrupt.
    clean = stopping or completed or exit_code is None or exit_code in CLEAN_EXIT_CODES
    state: SessionState = "exited" if clean else "crashed"
    final_reason = "completed" if completed else reason
    detail = (
        f"exit code {exit_code}"
        if completion_mode == "one_shot" and exit_code is not None
        else None
    )
    return state, final_reason, detail


WatchdogAction = Literal["none", "force_idle_ended", "force_idle_pty", "resume_working"]

# What the CLI is showing right now, read from the scrollback tail. The tail
# holds redraw history, so presence alone is not enough: the marker that appears
# *last* is the live frame. An unrecognized TUI reads "unknown" and every caller
# treats that as "no evidence", never as a licence to change state.
PtyTailState = Literal["working", "approval", "idle", "unknown"]
PTY_WORKING_MARKERS = ("esc to interrupt",)
# Deliberately narrow: a false "approval" only ever makes the daemon *more*
# conservative (it vetoes clearing an awaiting and blocks the idle backstop).
PTY_APPROVAL_MARKERS = (
    "do you want to",
    "allow this command",
    "allow codex to",
)
PTY_IDLE_MARKERS = ("? for shortcuts",)


def pty_tail_state(tail: str) -> PtyTailState:
    """Classify the CLI's current screen from its scrollback tail.

    Ordering-aware by construction: a session that showed a permission dialog
    and then resumed still has the dialog text in the retained tail, so only the
    last marker describes the live frame.
    """
    lowered = tail.lower()
    positions: list[tuple[int, PtyTailState]] = []
    for markers, name in (
        (PTY_WORKING_MARKERS, "working"),
        (PTY_APPROVAL_MARKERS, "approval"),
        (PTY_IDLE_MARKERS, "idle"),
    ):
        found = max((lowered.rfind(marker) for marker in markers), default=-1)
        if found >= 0:
            positions.append((found, name))  # type: ignore[arg-type]
    if not positions:
        return "unknown"
    return max(positions)[1]


def pty_tail_appears_idle(tail: str) -> bool:
    """True only when the CLI's latest frame is its idle input prompt."""
    return pty_tail_state(tail) == "idle"


def watchdog_decision(
    state: SessionState,
    *,
    stalled_seconds: float,
    tail_verdict: str | None,
    pty_state: PtyTailState,
) -> WatchdogAction:
    """Pure quiescence-watchdog decision, shared with the replay harness.

    ``resume_working`` closes the one gap the transcript cannot: an `awaiting`
    the user already answered. Nothing fires when a permission dialog is
    dismissed, and an approved long-running tool writes no transcript record
    until it finishes, so the CLI's own working spinner is the only timely
    proof the block is gone. It may only ever move `awaiting` → `working`
    inside a turn that is already running — never start a turn from `idle`.

    ``force_idle_ended`` only when the transcript tail proves the turn ended;
    ``force_idle_pty`` only when the tail carries no proof ("unknown"/"open")
    yet the CLI has provably sat at its idle prompt for the full stall window.
    A genuine long tool call keeps "esc to interrupt" on screen, and a real
    pending dialog reads "approval", so neither is ever cut short.

    ``tail_verdict=None`` means the transcript tail has not been read: the
    caller is doing the cheap pass that can only resume an awaiting session.
    """
    if state not in {"working", "awaiting"}:
        return "none"
    if (
        state == "awaiting"
        and pty_state == "working"
        and stalled_seconds >= STATE_WATCHDOG_AWAITING_RESUME_SECONDS
    ):
        return "resume_working"
    if tail_verdict is None:
        return "none"
    if stalled_seconds < STATE_WATCHDOG_ENDED_STUCK_SECONDS:
        return "none"
    if tail_verdict == "ended":
        return "force_idle_ended"
    if (
        tail_verdict in {"unknown", "open"}
        and stalled_seconds >= STATE_WATCHDOG_PTY_STUCK_SECONDS
        and pty_state == "idle"
    ):
        return "force_idle_pty"
    return "none"


async def apply_watchdog_recovery(
    session: Any,
    events: EventBus,
    action: WatchdogAction,
    *,
    stalled_seconds: float | None = None,
    tail_verdict: str | None = None,
) -> None:
    """Apply a non-"none" watchdog decision; shared with the replay harness.

    A completion recorded but never applied leaves the bookkeeping "completion
    seen, turn inactive", which would make _finish_root_turn a no-op. Re-open
    the turn so the forced close always lands and re-emits the boundary.
    """
    from .observation import _finish_root_turn, _transition

    session.observation_state["root_turn_active"] = True
    session.observation_state["root_completion_seen"] = False
    if action == "resume_working":
        # The block is gone but the turn is not: reclaim arbitration from the
        # hook that raised the awaiting and let the run finish normally. The
        # turn bookkeeping above guarantees the eventual close still lands.
        session.note_watchdog_recovery(
            "pty_working_after_awaiting",
            stalled_seconds=stalled_seconds,
            tail_verdict=tail_verdict,
        )
        await _transition(
            session,
            events,
            "working",
            source="watchdog-pty",
            force=True,
            inferred=True,
            evidence="pty_working_spinner_after_awaiting",
        )
    elif action == "force_idle_ended":
        session.note_watchdog_recovery(
            "transcript_tail_terminal",
            stalled_seconds=stalled_seconds,
            tail_verdict=tail_verdict,
        )
        await _finish_root_turn(
            session,
            events,
            source="watchdog",
            force=True,
            evidence="transcript_tail=ended",
        )
    elif action == "force_idle_pty":
        # No terminal record exists (schema drift, or a turn cut off before its
        # marker), but the PTY shows the idle prompt and no in-flight tool. This
        # is the last-resort backstop; mark it inferred for honesty.
        session.note_watchdog_recovery(
            "pty_idle_prompt",
            stalled_seconds=stalled_seconds,
            tail_verdict=tail_verdict,
        )
        await _finish_root_turn(
            session,
            events,
            source="watchdog-pty",
            force=True,
            inferred=True,
            evidence=f"pty_idle_prompt,tail={tail_verdict}",
        )


@dataclass(eq=False, slots=True)
class PtySubscriber:
    queue: asyncio.Queue[bytes | dict[str, Any]]
    resync_pending: bool = False
    dropped_bytes: int = 0
    dropped_chunks: int = 0
    exit_pending: dict[str, Any] | None = None


class Session:
    def __init__(
        self,
        record: SessionRecord,
        pty: PtyHost | RemotePtyHost,
        adapter: BackendAdapter,
        max_scrollback: int,
        hook_secret: str,
        ownership_job: ReaperJob | None = None,
        startup_started_at: float | None = None,
    ) -> None:
        self.record, self.pty, self.adapter = record, pty, adapter
        self.scrollback = ScrollbackBuffer(max_scrollback)
        self.subscribers: set[PtySubscriber] = set()
        self.tasks: set[asyncio.Task[Any]] = set()
        self.stopping = False
        self.stop_event = asyncio.Event()
        self.hook_secret = hook_secret
        self.ownership_job = ownership_job
        self.startup_started_at = startup_started_at or time.perf_counter()
        self.startup_measurement_task: asyncio.Task[Any] | None = None
        self.registration_task: asyncio.Task[Any] | None = None
        self.attachments_seen = 0
        # Exactly one browser connection may write terminal-generated responses
        # and user keystrokes. Without this, two attached xterms both answer
        # device-status queries and the duplicate response appears at the prompt.
        self.input_owner: str | None = None
        self.revision = 0
        self.state_source_priority = -1
        # Diagnostics for end-detection health: when the state last changed, when a
        # native hook last arrived, a bounded transition/fault log, and observer
        # supervision counters.  These feed the state-log debug endpoint and the
        # quiescence watchdog; none are serialized into the frequent record snapshot.
        self.last_state_change_ts = time.time()
        self.last_state_change_monotonic = time.monotonic()
        self.last_evidence_ts = time.time()
        self.last_hook_ts = 0.0
        self.state_transitions: deque[dict[str, Any]] = deque(maxlen=STATE_TRANSITION_LOG_LIMIT)
        self.state_changes: deque[dict[str, Any]] = deque(maxlen=STATE_CHANGE_LOG_LIMIT)
        self.observer_restart_count = 0
        self.observer_last_fault: dict[str, Any] | None = None
        self.watchdog_recoveries = 0
        # Status-health metrics: proven/inferred transition counts, per-source
        # inferred recoveries, contract violations, terminal outcomes, blocked
        # reopen attempts, and recent working→terminal latencies. These feed the
        # state-log endpoint and the fleet status-health diagnostic.
        self.status_health_counters: dict[str, int] = {}
        self.watchdog_recovery_actions: dict[str, int] = {}
        self.terminal_latencies: deque[dict[str, Any]] = deque(maxlen=32)
        self.agent_stop_event = asyncio.Event()
        self.observer_task: asyncio.Task[Any] | None = None
        self.transcript_path: Path | None = None
        self.detection_task: asyncio.Task[Any] | None = None
        # The launcher-generated lifecycle id remains stable even when an
        # adapter later discovers and records a different native transcript id.
        # Demotion must match this token so Codex can return to its parent shell.
        self.agent_lifecycle_id: str | None = None
        # Detection is a fallback for agents launched without the mux shim. Once a
        # native run has explicitly exited, its still-recent transcript must not
        # immediately promote the containing shell again. An explicit launcher
        # promotion may reuse the same native id (for example, resume).
        self.ignored_detection_runs: set[tuple[str, str]] = set()
        self.osc7 = Osc7Parser()
        self.cwd_debounce_task: asyncio.Task[Any] | None = None
        self.cwd_switches: deque[float] = deque()
        self.cwd_telemetry_dropped = 0
        self.last_input_event_ts = 0.0
        self.last_input_report_ts = 0.0
        self.input_revision = 0
        self.terminal_mode: str | None = None
        self.terminal_mode_updated_at = 0.0
        self.observation_state: dict[str, Any] = {
            "root_turn_active": False,
            "root_completion_seen": False,
            "codex_scope": "root",
        }
        # True while the observer replays transcript content that predates its
        # attachment; state transitions and update fanout are suppressed then.
        self.observation_replay = False
        # Set when this PTY was promoted around a nested agent CLI; used to
        # ignore shell-prompt echoes from just before/around the promotion.
        self.agent_promoted_at: float | None = None
        self.agent_exit_check_task: asyncio.Task[Any] | None = None
        self.agent_ready_task: asyncio.Task[Any] | None = None
        self.output_window: deque[tuple[float, int]] = deque()
        # Native transcripts report tool results by opaque invocation id.  Keep
        # this correlation in memory only; normalized events expose the stable
        # tool name without persisting backend-specific transcript identifiers.
        self.tool_names: dict[str, str] = {}
        # Supervisor-backed sessions mirror their metadata (record snapshot,
        # hook secret, transcript path) into the supervisor so a future daemon
        # can rebuild this Session after a restart. None for in-process PTYs.
        self.meta_sink: Callable[[], None] | None = None

    def subscribe(self, maxsize: int = 1024) -> PtySubscriber:
        subscriber = PtySubscriber(asyncio.Queue(maxsize=maxsize))
        self.subscribers.add(subscriber)
        return subscriber

    def replay_and_subscribe(
        self,
    ) -> tuple[dict[str, Any], int, bytes, PtySubscriber]:
        """Atomically snapshot replay bytes and register for subsequent output.

        This method has no await points, so the single event-loop fanout task cannot
        append output between the snapshot and subscription. A new attachment therefore
        neither misses nor duplicates the boundary chunk.
        """
        subscriber = self.subscribe()
        return self.record.snapshot(), self.revision, self.scrollback.bytes(), subscriber

    def _schedule_resync(self, subscriber: PtySubscriber, rejected: bytes | None = None) -> None:
        if rejected is not None:
            subscriber.dropped_bytes += len(rejected)
            subscriber.dropped_chunks += 1
        while not subscriber.queue.empty():
            queued = subscriber.queue.get_nowait()
            if isinstance(queued, bytes):
                subscriber.dropped_bytes += len(queued)
                subscriber.dropped_chunks += 1
        if not subscriber.resync_pending:
            subscriber.resync_pending = True
            subscriber.queue.put_nowait({"type": "resync"})

    def publish_output(self, data: bytes) -> None:
        for subscriber in tuple(self.subscribers):
            if subscriber.resync_pending:
                subscriber.dropped_bytes += len(data)
                subscriber.dropped_chunks += 1
                continue
            try:
                subscriber.queue.put_nowait(data)
            except asyncio.QueueFull:
                self._schedule_resync(subscriber, data)

    def publish_update(self) -> None:
        self.revision += 1
        frame = {
            "type": "update",
            "snapshot": self.record.snapshot(),
            "revision": self.revision,
        }
        for subscriber in tuple(self.subscribers):
            if subscriber.resync_pending:
                continue
            try:
                subscriber.queue.put_nowait(frame)
            except asyncio.QueueFull:
                self._schedule_resync(subscriber)
        # getattr-guarded so publish_update stays callable from lightweight
        # stubs that exercise transition() as a pure arbitration contract.
        meta_sink = getattr(self, "meta_sink", None)
        if meta_sink is not None:
            try:
                meta_sink()
            except Exception:
                log.debug("session meta sink failed", exc_info=True)

    def publish_exit(self, reason: str) -> None:
        self.revision += 1
        frame = {
            "type": "exit",
            "snapshot": self.record.snapshot(),
            "revision": self.revision,
            "reason": reason,
        }
        for subscriber in tuple(self.subscribers):
            if subscriber.resync_pending:
                subscriber.exit_pending = frame
                continue
            try:
                subscriber.queue.put_nowait(frame)
            except asyncio.QueueFull:
                self._schedule_resync(subscriber)
                subscriber.exit_pending = frame

    def transition(
        self,
        state: SessionState,
        detail: str | None,
        *,
        source: str,
        evidence: str | None = None,
        inferred: bool | None = None,
        awaiting_reason: str | None = None,
        force: bool = False,
    ) -> bool:
        """Apply a state transition only when its observation source is authoritative.

        Every applied transition lands in the typed ledger with the evidence
        that justified it and its proven/inferred classification; see
        apply_state_transition for the shared contract.
        """
        changed = apply_state_transition(
            self,
            state,
            detail,
            source=source,
            evidence=evidence,
            inferred=inferred,
            awaiting_reason=awaiting_reason,
            force=force,
        )
        if changed:
            self.publish_update()
        return changed

    def note_observer_fault(self, error: str, path: Path | None) -> None:
        """Record that the transcript observer crashed and is being restarted."""
        self.observer_restart_count += 1
        fault = {
            "ts": time.time(),
            "kind": "observer_fault",
            "error": error[:500],
            "path": str(path) if path else None,
            "restart_count": self.observer_restart_count,
        }
        self.observer_last_fault = fault
        self.state_transitions.append(fault)

    def note_watchdog_recovery(
        self,
        action: str,
        detail: str | None = None,
        *,
        stalled_seconds: float | None = None,
        tail_verdict: str | None = None,
    ) -> None:
        """Record that the quiescence watchdog resolved a stuck state."""
        self.watchdog_recoveries += 1
        self.watchdog_recovery_actions[action] = self.watchdog_recovery_actions.get(action, 0) + 1
        self.state_transitions.append(
            {
                "ts": time.time(),
                "kind": "watchdog_recovery",
                "action": action,
                "detail": detail,
                "stalled_seconds": (
                    round(stalled_seconds, 3) if stalled_seconds is not None else None
                ),
                "tail_verdict": tail_verdict,
            }
        )

    def note_reopen_blocked(self, source: str) -> None:
        """Record a late, unordered begin refused by the closed_by_transcript latch.

        Each occurrence is a hook/transcript race that would have reopened
        "working" on a finished session; the count is a regression signal.
        """
        counters = self.status_health_counters
        counters["reopen_blocked"] = counters.get("reopen_blocked", 0) + 1
        self.state_transitions.append(
            {
                "ts": time.time(),
                "kind": "reopen_blocked",
                "source": source,
                "state": self.record.state,
            }
        )

    def status_health(self, now: float | None = None) -> dict[str, Any]:
        """Per-session status-health metrics for diagnostics and soak bounds."""
        return session_status_health(self, now=now)

    def take_resync(
        self, subscriber: PtySubscriber
    ) -> tuple[int, int, bytes, dict[str, Any], int, dict[str, Any] | None]:
        """Capture a deterministic recovery boundary without yielding the event loop."""
        dropped_bytes = subscriber.dropped_bytes
        dropped_chunks = subscriber.dropped_chunks
        replay = self.scrollback.bytes()
        snapshot = self.record.snapshot()
        revision = self.revision
        exit_frame = subscriber.exit_pending
        subscriber.resync_pending = False
        subscriber.dropped_bytes = 0
        subscriber.dropped_chunks = 0
        subscriber.exit_pending = None
        return dropped_bytes, dropped_chunks, replay, snapshot, revision, exit_frame

    def unsubscribe(self, subscriber: PtySubscriber) -> None:
        self.subscribers.discard(subscriber)


class SessionManager:
    def __init__(
        self,
        adapters: dict[str, BackendAdapter],
        reaper: ReaperJob,
        history: HistoryIndex,
        events: EventBus,
        max_scrollback: int,
        ingress_url: str,
        child_env: dict[str, str] | None = None,
        hook_spool_dir: Path | None = None,
        supervisor: SupervisorClient | None = None,
    ) -> None:
        self.adapters, self.reaper, self.history, self.events = adapters, reaper, history, events
        self.max_scrollback = max_scrollback
        self.ingress_url = ingress_url.rstrip("/")
        self.child_env = child_env or {}
        self.hook_spool_dir = hook_spool_dir
        if hook_spool_dir is not None:
            hook_spool_dir.mkdir(parents=True, exist_ok=True)
        # When set, PTYs are spawned in the out-of-process supervisor so live
        # sessions survive a daemon restart; in-process spawning remains the
        # fallback whenever the supervisor is unreachable.
        self.supervisor = supervisor
        self.sessions: dict[str, Session] = {}

    async def spawn(
        self,
        *,
        backend: str,
        name: str | None,
        cwd: str | None,
        project_id: str,
        exe: str | None = None,
        args: list[str] | None = None,
        resume_native_id: str | None = None,
        shell_profile_id: str | None = None,
        profile_env: dict[str, str] | None = None,
        project_label: str | None = None,
        project: ProjectIdentity | None = None,
        startup_started_at: float | None = None,
        startup_timing_ms: dict[str, float] | None = None,
        completion_mode: Literal["interactive", "one_shot"] = "interactive",
    ) -> Session:
        startup_started_at = startup_started_at or time.perf_counter()
        startup_timing_ms = dict(startup_timing_ms or {})
        if backend not in self.adapters:
            raise ValueError(f"unknown backend: {backend}")
        if completion_mode not in {"interactive", "one_shot"}:
            raise ValueError(f"unknown completion mode: {completion_mode}")
        if completion_mode == "one_shot" and backend != "shell":
            raise ValueError("one-shot completion is available only for shell sessions")
        sid = str(uuid.uuid4())
        native_id = resume_native_id or sid
        resolved_cwd = Path(cwd or Path.cwd()).resolve()
        if not resolved_cwd.is_dir():
            raise ValueError(f"cwd does not exist: {resolved_cwd}")
        adapter = self.adapters[backend]
        opts = SpawnOptions(resolved_cwd, exe, args or [], sid)
        spawn_spec = (
            adapter.resume_spec(native_id, opts)
            if resume_native_id
            else adapter.spawn_spec(native_id, opts)
        )
        record = SessionRecord(
            sid,
            name or f"{backend}-{sid[:6]}",
            project_id,
            backend,
            native_id,
            str(resolved_cwd),
            spawn_spec.executable,
            list(spawn_spec.argv),
            shell_profile_id=shell_profile_id,
            auto_named=name is None,
            state="running" if backend == "shell" else "starting",
            startup_timing_ms=startup_timing_ms,
            completion_mode=completion_mode,
        )
        if project is None:
            project_started_at = time.perf_counter()
            project = await resolve_project(resolved_cwd)
            startup_timing_ms["project_resolution"] = round(
                (time.perf_counter() - project_started_at) * 1000, 1
            )
        record.repository_id = project.id
        record.project_label = project_label or project.label
        record.project_root = project.root
        record.project_scope_id = project.id
        record.repo_group_id = project.repo_group_id
        record.spawn_cwd = str(resolved_cwd)
        record.spawn_project_scope_id = project.id
        record.spawn_repo_group_id = project.repo_group_id
        record.spawn_project_label = record.project_label
        record.spawn_project_root = project.root
        record.runtime_cwd = str(resolved_cwd)
        if backend in {"claude", "codex"}:
            record.agent_run_id = sid
            record.agent_run_started_at = record.created_at
            record.run_cwd = str(resolved_cwd)
            record.run_project_scope_id = project.id
            record.run_repo_group_id = project.repo_group_id
        hook_secret = secrets.token_urlsafe(24)
        env_extra = {
            **self.child_env,
            **{
                key: value
                for candidate in self.adapters.values()
                for key, value in candidate.session_env(sid).items()
            },
            **spawn_spec.env,
            **(profile_env or {}),
            "MUX_SESSION_ID": sid,
            "MUX_HOOK_URL": f"{self.ingress_url}/api/hooks/{sid}",
            "MUX_PROMOTE_URL": f"{self.ingress_url}/api/sessions/{sid}/promote",
            "MUX_DEMOTE_URL": f"{self.ingress_url}/api/sessions/{sid}/demote",
            "MUX_HOOK_SECRET": hook_secret,
            **(
                {"MUX_HOOK_SPOOL": str(self.hook_spool_dir / f"{sid}.jsonl")}
                if self.hook_spool_dir is not None
                else {}
            ),
        }
        pty_started_at = time.perf_counter()
        # Scrubbed base environment: a daemon (re)launched from inside an agent
        # session must not pass parent-Claude session markers to terminals.
        env_base = scrub_claude_session_markers(os.environ)
        pty: PtyHost | RemotePtyHost | None = None
        if self.supervisor is not None and self.supervisor.connected:
            remote = RemotePtyHost(
                self.supervisor,
                sid,
                appname=spawn_spec.executable,
                argv=tuple(spawn_spec.argv),
                cwd=str(resolved_cwd),
                # The supervisor spawns children with exactly this daemon-built
                # environment; its own (potentially stale) environ is excluded.
                env=merge_environment(env_base, env_extra),
                graceful_exit=adapter.graceful_exit_keys(),
                max_scrollback=self.max_scrollback,
            )
            remote.prepare()
            try:
                await asyncio.to_thread(remote.spawn)
                pty = remote
            except Exception:
                log.exception(
                    "supervisor spawn failed for %s; falling back to in-process PTY", sid
                )
                self.supervisor.unregister_host(remote)
        if pty is None:
            pty = PtyHost(
                spawn_spec.executable,
                spawn_spec.argv,
                str(resolved_cwd),
                reaper=self.reaper,
                graceful_exit=adapter.graceful_exit_keys(),
                env_extra=env_extra,
                env_base=env_base,
            )
            # winpty/ConPTY process creation is synchronous and can be slow when
            # Windows security scanning or a shell profile is busy. Keep it off the
            # aiohttp loop so the UI, event stream, and other terminals stay
            # responsive meanwhile.
            pty.prepare()
            await asyncio.to_thread(pty.spawn)
        startup_timing_ms["pty_spawn"] = round((time.perf_counter() - pty_started_at) * 1000, 1)
        record.pid = pty.pid
        record.process_job_assignment = pty.reaper_assignment
        registration_started_at = time.perf_counter()
        ownership_job: ReaperJob | None = None
        ownership_error: str | None = None
        # Supervisor-owned PTYs get their nested per-session job supervisor-side;
        # a daemon-held job handle would kill the tree on daemon exit and defeat
        # the whole survival property.
        create_child = (
            None if isinstance(pty, RemotePtyHost) else getattr(self.reaper, "create_child", None)
        )
        if create_child:
            try:
                ownership_job = create_child()
                ownership_job.assign(record.pid)
                record.process_job_assignment += ";nested_session_job_assigned"
            except OSError as exc:
                ownership_error = str(exc)
                record.process_job_assignment += f";nested_session_job_failed:{ownership_error}"
                if ownership_job:
                    ownership_job.close()
                ownership_job = None
        session = Session(
            record,
            pty,
            adapter,
            self.max_scrollback,
            hook_secret,
            ownership_job,
            startup_started_at,
        )
        self.sessions[sid] = session
        if isinstance(pty, RemotePtyHost):
            self._attach_meta_sink(session)
        transcript = adapter.transcript_path(native_id, resolved_cwd)
        # The PTY is usable now. Durable history/event registration shares SQLite
        # with transcript reconciliation and can occasionally queue behind a large
        # import. Never hide an already-running shell behind that bookkeeping.
        registration_task = asyncio.create_task(
            self._persist_spawn_registration(
                session,
                project,
                str(transcript) if transcript else None,
                ownership_error,
                registration_started_at,
            ),
            name=f"register-{sid}",
        )
        session.registration_task = registration_task
        session.tasks.add(registration_task)
        registration_task.add_done_callback(session.tasks.discard)
        session.tasks.add(asyncio.create_task(self._fanout(session), name=f"fanout-{sid}"))
        session.tasks.add(asyncio.create_task(self._ticker(session), name=f"ticker-{sid}"))
        if backend in {"claude", "codex"}:
            record.parser_status = "waiting"
            self._start_observer(session, transcript)
        elif backend == "shell":
            self._start_detection(session)
        startup_timing_ms["registration"] = round(
            (time.perf_counter() - registration_started_at) * 1000, 1
        )
        startup_timing_ms["server_ready"] = round(
            (time.perf_counter() - startup_started_at) * 1000, 1
        )
        return session

    async def _persist_spawn_registration(
        self,
        session: Session,
        project: ProjectIdentity,
        transcript: str | None,
        ownership_error: str | None,
        started_at: float,
    ) -> None:
        """Persist spawn metadata after the live session is already attachable."""

        try:
            await self.history.register_project_scope(project)
            await self.history.session_started(session.record, transcript)
            await self.events.emit(
                "session_spawned",
                session_id=session.record.id,
                backend=session.record.backend,
                name=session.record.name,
                project_scope_id=session.record.project_scope_id,
                repo_group_id=session.record.repo_group_id,
            )
            if ownership_error:
                await self.events.emit(
                    "process_ownership_degraded",
                    session_id=session.record.id,
                    source="process",
                    error=ownership_error,
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            # A persistence failure is operationally important, but it must not
            # tear down the PTY or strand the browser's optimistic session tab.
            log.exception("session spawn registration failed: %s", session.record.id)
        finally:
            session.record.startup_timing_ms["durable_registration"] = round(
                (time.perf_counter() - started_at) * 1000,
                1,
            )
            session.publish_update()

    @staticmethod
    async def _await_registration(session: Session) -> None:
        task = getattr(session, "registration_task", None)
        if task is not None and task is not asyncio.current_task() and not task.done():
            await asyncio.shield(task)

    def _attach_meta_sink(self, session: Session) -> None:
        client = self.supervisor
        if client is None:
            return

        def push() -> None:
            client.queue_meta(session.record.id, self._session_meta(session))

        session.meta_sink = push
        push()

    @staticmethod
    def _session_meta(session: Session) -> dict[str, Any]:
        return {
            "record": session.record.snapshot(),
            "hook_secret": session.hook_secret,
            "transcript_path": (
                str(session.transcript_path) if session.transcript_path else None
            ),
            "agent_lifecycle_id": session.agent_lifecycle_id,
        }

    async def adopt_supervisor_sessions(self) -> int:
        """Rebuild live sessions announced by the supervisor at daemon boot.

        This is the reattach half of the session-preserving reload: the
        supervisor kept the ConPTYs and authoritative scrollback while the
        daemon was gone; each entry's mirrored metadata rebuilds the record,
        the scrollback snapshot seeds the local mirror, and the normal fanout/
        observer/detection machinery resumes from there.
        """
        client = self.supervisor
        if client is None:
            return 0
        adopted = 0
        for info in client.initial_sessions:
            sid = str(info.get("sid") or "")
            if not sid or sid in self.sessions:
                continue
            raw_meta = info.get("meta")
            meta: dict[str, Any] = raw_meta if isinstance(raw_meta, dict) else {}
            record_snapshot = meta.get("record")
            if not isinstance(record_snapshot, dict):
                # Nothing to rebuild from. Leave the entry untouched: killing it
                # would violate the survival contract, and a daemon that does
                # know it may still come back.
                log.warning("supervised session %s has no metadata; not adopting", sid)
                continue
            try:
                record = SessionRecord.from_snapshot(record_snapshot)
            except (TypeError, ValueError):
                log.exception("could not rebuild session record for %s", sid)
                continue
            if record.state in {"exited", "crashed"}:
                # Fully ended and already persisted by a previous daemon; the
                # supervisor entry is a leftover.
                client.notify({"t": "remove", "sid": sid})
                continue
            host = host_for_adoption(client, info)
            host.prepare()
            try:
                response, replay = await client.subscribe(host)
            except Exception:
                log.exception("could not subscribe to supervised session %s", sid)
                client.unregister_host(host)
                continue
            adapter = self.adapters.get(record.backend) or self.adapters["shell"]
            hook_secret = str(meta.get("hook_secret") or "") or secrets.token_urlsafe(24)
            session = Session(record, host, adapter, self.max_scrollback, hook_secret)
            session.scrollback.seed(replay, int(response.get("position", len(replay))))
            transcript = meta.get("transcript_path")
            session.transcript_path = (
                Path(transcript) if isinstance(transcript, str) and transcript else None
            )
            lifecycle = meta.get("agent_lifecycle_id")
            session.agent_lifecycle_id = lifecycle if isinstance(lifecycle, str) else None
            if record.backend in {"claude", "codex"}:
                session.agent_promoted_at = time.time()
            self.sessions[sid] = session
            self._attach_meta_sink(session)
            session.tasks.add(asyncio.create_task(self._fanout(session), name=f"fanout-{sid}"))
            session.tasks.add(asyncio.create_task(self._ticker(session), name=f"ticker-{sid}"))
            if host.isalive():
                if record.backend in {"claude", "codex"}:
                    self._start_observer(session, session.transcript_path)
                elif record.backend == "shell":
                    self._start_detection(session)
            else:
                await self._mark_ended(session, "process_exit")
            adopted += 1
            await self.events.emit(
                "session_reattached",
                session_id=sid,
                source="daemon",
                backend=record.backend,
                pid=record.pid,
            )
        return adopted

    def _start_observer(self, session: Session, transcript: Path | None) -> None:
        if session.observer_task and not session.observer_task.done():
            session.agent_stop_event.set()
            session.observer_task.cancel()
        session.agent_stop_event = asyncio.Event()
        session.transcript_path = transcript
        task = asyncio.create_task(
            self._observe(session, transcript, session.agent_stop_event),
            name=f"observe-{session.record.id}",
        )
        session.observer_task = task
        session.tasks.add(task)

    def _start_detection(self, session: Session) -> None:
        if session.detection_task and not session.detection_task.done():
            session.detection_task.cancel()
        task = asyncio.create_task(
            self._detect_nested_agent(session), name=f"detect-{session.record.id}"
        )
        session.detection_task = task
        session.tasks.add(task)

    async def _detect_nested_agent(self, session: Session) -> None:
        scan_cursor = session.scrollback.position
        detection_started_at = time.time()
        agent_names = [name for name in self.adapters if name != "shell"]
        max_name_len = max((len(name) for name in agent_names), default=0)
        seen_names: set[str] = set()
        carry = b""
        while not session.stop_event.is_set() and session.pty.isalive():
            if session.record.backend != "shell":
                return
            # Scan only output produced since the previous poll, accumulating which
            # agent names have appeared. A plain shell may never launch an agent, so
            # re-joining and re-lowercasing the whole retained scrollback twice a
            # second is pure waste; the carry tail catches a name split across the
            # poll boundary, and remembering names keeps the sticky wait-for-transcript
            # behavior even after the echoed command scrolls out of the window.
            current = session.scrollback.position
            if current > scan_cursor:
                haystack = (carry + session.scrollback.bytes_since(scan_cursor)).lower()
                scan_cursor = current
                for name in agent_names:
                    if name.encode() in haystack:
                        seen_names.add(name)
                carry = haystack[-(max_name_len - 1) :] if max_name_len > 1 else b""
            # Full-screen CLIs redraw the echoed command quickly and ANSI can split
            # prompt text, so exact ``> claude`` matching is not reliable. Only
            # output and native transcript activity created during this detection
            # pass may promote the shell; retained output from an ended agent must
            # never identify a new run.
            launched = [self.adapters[name] for name in seen_names]
            if not launched:
                try:
                    await asyncio.wait_for(session.stop_event.wait(), timeout=0.5)
                except TimeoutError:
                    pass
                continue
            candidates = [
                (modified, adapter.name, path, native_id)
                for adapter in launched
                for modified, path, native_id in adapter.recent_transcripts(
                    Path(session.record.runtime_cwd or session.record.cwd),
                    detection_started_at,
                )
                if (adapter.name, native_id) not in session.ignored_detection_runs
            ]
            if candidates:
                _, backend, path, native_id = max(candidates)
                await self._begin_agent_run(session)
                session.adapter = self.adapters[backend]
                session.pty.graceful_exit = session.adapter.graceful_exit_keys()
                session.record.backend = backend
                session.record.native_session_id = native_id
                session.transition(
                    "starting", None, source="daemon", evidence="backend_detected", force=True
                )
                session.record.parser_status = "waiting"
                session.record.parser_diagnostic = None
                session.record.parser_events_seen = 0
                session.record.parser_unknown_events = 0
                session.record.parser_unknown_signatures = {}
                session.record.parser_schema_version = None
                session.observation_state = {
                    "root_turn_active": False,
                    "root_completion_seen": False,
                    "codex_scope": "root",
                }
                session.agent_promoted_at = time.time()
                session.publish_update()
                await self.history.session_promoted(session.record, str(path))
                await self.events.emit(
                    "backend_detected",
                    session_id=session.record.id,
                    backend=backend,
                    native_session_id=native_id,
                )
                self._start_observer(session, path)
                return
            try:
                await asyncio.wait_for(session.stop_event.wait(), timeout=0.5)
            except TimeoutError:
                pass

    async def promote(
        self, sid: str, backend: str, native_id: str, launch_cwd: str | None = None
    ) -> Session:
        if backend not in {"claude", "codex"}:
            raise ValueError(f"cannot promote session to {backend}")
        session = self.resolve(sid)
        if session.record.backend == backend and session.agent_lifecycle_id == native_id:
            return session
        if session.record.backend == backend and session.record.native_session_id == native_id:
            session.agent_lifecycle_id = native_id
            return session
        session.ignored_detection_runs.discard((backend, native_id))
        session.agent_lifecycle_id = native_id
        await self._begin_agent_run(session, launch_cwd)
        adapter = self.adapters[backend]
        session.adapter = adapter
        session.pty.graceful_exit = adapter.graceful_exit_keys()
        session.record.backend = backend
        session.record.native_session_id = native_id
        session.transition("starting", None, source="daemon", evidence="promotion", force=True)
        session.record.parser_status = "waiting"
        session.record.parser_diagnostic = None
        session.record.parser_events_seen = 0
        session.record.parser_unknown_events = 0
        session.record.parser_unknown_signatures = {}
        session.record.parser_schema_version = None
        session.observation_state = {
            "root_turn_active": False,
            "root_completion_seen": False,
            "codex_scope": "root",
        }
        session.record.state_detail = None
        session.state_source_priority = -1
        session.agent_promoted_at = time.time()
        if session.record.auto_named:
            session.record.name = Path(session.record.run_cwd or session.record.cwd).name or backend
        session.publish_update()
        transcript = adapter.transcript_path(
            native_id, Path(session.record.run_cwd or session.record.cwd)
        )
        await self.history.session_promoted(session.record, str(transcript) if transcript else "")
        await self.events.emit(
            "backend_detected",
            session_id=session.record.id,
            source="daemon",
            backend=backend,
            native_session_id=native_id,
        )
        self._start_observer(session, transcript)
        return session

    async def demote(self, sid: str, backend: str, native_id: str) -> Session:
        session = self.resolve(sid)
        await self._await_registration(session)
        if session.record.backend != backend:
            return session
        lifecycle_id = session.agent_lifecycle_id or session.record.native_session_id
        if lifecycle_id != native_id:
            return session
        observed_native_id = session.record.native_session_id
        session.ignored_detection_runs.add((backend, native_id))
        session.ignored_detection_runs.add((backend, observed_native_id))
        await self.history.update_agent_summary(session.record)
        await self.history.agent_run_ended(session.record, "agent_exit")
        session.agent_stop_event.set()
        if session.observer_task and not session.observer_task.done():
            session.observer_task.cancel()
            await asyncio.gather(session.observer_task, return_exceptions=True)
        session.observer_task = None
        session.agent_lifecycle_id = None
        session.adapter = self.adapters["shell"]
        session.pty.graceful_exit = session.adapter.graceful_exit_keys()
        session.record.backend = "shell"
        session.record.native_session_id = session.record.id
        session.transition("running", None, source="daemon", evidence="demotion", force=True)
        session.record.context_window = 0
        session.record.context_pct = 0
        session.record.parser_status = "not_applicable"
        session.record.parser_diagnostic = None
        session.record.parser_events_seen = 0
        session.record.parser_unknown_events = 0
        session.record.parser_unknown_signatures = {}
        session.record.parser_schema_version = None
        session.observation_state = {
            "root_turn_active": False,
            "root_completion_seen": False,
            "codex_scope": "root",
        }
        session.observation_replay = False
        session.agent_promoted_at = None
        session.transcript_path = None
        session.record.agent_run_id = None
        session.record.agent_run_started_at = None
        session.record.run_cwd = None
        session.record.run_project_scope_id = None
        session.record.run_repo_group_id = None
        session.record.repository_id = session.record.spawn_project_scope_id
        session.record.project_label = session.record.spawn_project_label
        session.record.project_root = session.record.spawn_project_root
        session.record.project_scope_id = session.record.spawn_project_scope_id
        session.record.repo_group_id = session.record.spawn_repo_group_id
        session.state_source_priority = -1
        if session.record.auto_named:
            session.record.name = f"shell-{session.record.id[:6]}"
        session.publish_update()
        await self.events.emit(
            "backend_demoted",
            session_id=session.record.id,
            source="daemon",
            backend=backend,
            native_session_id=observed_native_id,
        )
        self._start_detection(session)
        return session

    async def _observe(
        self, session: Session, transcript: Path | None, stop_event: asyncio.Event
    ) -> None:
        from .observation import observe_transcript

        adapter = session.adapter
        path = transcript
        if path is None or not path.exists():
            path = await adapter.await_transcript(
                session.record.native_session_id,
                Path(session.record.run_cwd or session.record.cwd),
                session.record.created_at,
                stop_event,
            )
        backoff = OBSERVER_RESTART_BACKOFF_MIN_SECONDS
        while path and not stop_event.is_set():
            session.transcript_path = path
            native_id = adapter.transcript_native_id(path)
            if native_id:
                session.record.native_session_id = native_id
            await self._await_registration(session)
            await self.history.session_promoted(session.record, str(path))
            observe_task = asyncio.create_task(
                observe_transcript(session, path, self.events, stop_event),
                name=f"observe-tail-{session.record.id}",
            )
            try:
                switch = await self._watch_transcript_switch(
                    session, path, stop_event, observe_task
                )
            finally:
                if not observe_task.done():
                    observe_task.cancel()
                await asyncio.gather(observe_task, return_exceptions=True)
            if switch is not None:
                path = switch
                backoff = OBSERVER_RESTART_BACKOFF_MIN_SECONDS
                continue
            if stop_event.is_set():
                break
            # _watch_transcript_switch returned without a new file and the tail task
            # is finished. observe_transcript only ends by raising (its loop runs
            # until stop_event); a bare return means nothing is left to follow. A
            # raised exception must not silently end observation — that is exactly
            # what freezes a session as "working". Log it, record the fault, back
            # off, and re-tail the same file so a later terminal record is still seen.
            fault = None if observe_task.cancelled() else observe_task.exception()
            if fault is None:
                break
            log.exception(
                "transcript observer for session %s crashed; restarting",
                session.record.id,
                exc_info=fault,
            )
            session.note_observer_fault(repr(fault), path)
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=backoff)
            except TimeoutError:
                pass
            backoff = min(backoff * 2, OBSERVER_RESTART_BACKOFF_MAX_SECONDS)

    async def _watch_transcript_switch(
        self,
        session: Session,
        current: Path,
        stop_event: asyncio.Event,
        observe_task: asyncio.Task[Any],
    ) -> Path | None:
        """Follow the agent when it moves to another native conversation.

        In-CLI resume/new-conversation switches the CLI to a different transcript
        file without any daemon-visible lifecycle signal. When the observed file
        goes quiet and another transcript for the same run cwd is being actively
        written (and is not owned by another live session), observation moves.
        """
        while not stop_event.is_set() and not observe_task.done():
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=TRANSCRIPT_SWITCH_POLL_SECONDS)
            except TimeoutError:
                pass
            if stop_event.is_set() or observe_task.done():
                break
            if session.record.backend not in {"claude", "codex"}:
                break
            candidate = self._transcript_switch_candidate(session, current)
            if candidate is not None:
                await self.events.emit(
                    "transcript_retargeted",
                    session_id=session.record.id,
                    source="daemon",
                    backend=session.record.backend,
                    previous=str(current),
                    path=str(candidate),
                )
                return candidate
        return None

    @staticmethod
    def _resolved_cwd(record: SessionRecord) -> Path:
        cwd = Path(record.run_cwd or record.cwd)
        try:
            return cwd.resolve()
        except OSError:
            return cwd

    def _has_transcript_sibling(self, session: Session, cwd: Path) -> bool:
        """True when another live agent session writes transcripts into this cwd."""
        try:
            key = cwd.resolve()
        except OSError:
            key = cwd
        for other in self.sessions.values():
            if other is session:
                continue
            if other.record.backend != session.record.backend:
                continue
            if other.record.state in {"exited", "crashed"}:
                continue
            if self._resolved_cwd(other.record) == key:
                return True
        return False

    def _transcript_switch_candidate(self, session: Session, current: Path) -> Path | None:
        record = session.record
        now = time.time()
        try:
            current_mtime = current.stat().st_mtime
        except OSError:
            current_mtime = 0.0
        if now - current_mtime < TRANSCRIPT_SWITCH_QUIET_SECONDS:
            return None
        started = record.agent_run_started_at or record.created_at
        cwd = Path(record.run_cwd or record.cwd)
        # Following a freshly-written transcript is only safe when this is the sole
        # agent writing into that directory. With sibling sessions in the same cwd,
        # a fresh sibling transcript is indistinguishable from this CLI resuming
        # into a new conversation; switching would attach this observer to the
        # sibling's file and bleed its status/tokens/context into this record (the
        # native-id swap seen with multiple sessions in one project). The
        # single-session in-CLI resume case still works — it has no sibling.
        if self._has_transcript_sibling(session, cwd):
            return None
        other_native_ids = {
            other.record.native_session_id
            for other in self.sessions.values()
            if other is not session
        }
        other_paths = {
            str(other.transcript_path)
            for other in self.sessions.values()
            if other is not session and other.transcript_path
        }
        best: tuple[float, Path] | None = None
        try:
            candidates = session.adapter.recent_transcripts(cwd, started)
        except OSError:
            return None
        for modified, path, native_id in candidates:
            if path == current or str(path) in other_paths:
                continue
            if native_id in other_native_ids:
                continue
            if (session.adapter.name, native_id) in session.ignored_detection_runs:
                continue
            if now - modified > TRANSCRIPT_SWITCH_FRESH_SECONDS:
                continue
            if best is None or modified > best[0]:
                best = (modified, path)
        return best[1] if best else None

    async def state_watchdog_loop(self) -> None:
        """Safety net for a session left "working"/"awaiting" after its turn ended.

        Runs independently of the observer and hooks. When an agent has been busy
        with no state change and a quiet transcript for the stuck window, it drains
        any spooled hook fallback, then re-derives the true state from the
        transcript tail. It only forces idle when that tail proves the turn is
        over, so a legitimately long tool call (whose tail reads as mid-tool) is
        never cut short.
        """
        while True:
            try:
                await asyncio.sleep(STATE_WATCHDOG_POLL_SECONDS)
                now = time.time()
                for session in tuple(self.sessions.values()):
                    await self._watchdog_check_session(session, now)
            except asyncio.CancelledError:
                raise
            except Exception:  # never let the safety net die silently
                log.exception("state watchdog iteration failed")

    async def _watchdog_check_session(self, session: Session, now: float) -> None:
        """Re-derive one session's true state and force idle if its turn is over.

        Force-idles only when the transcript tail *proves* the turn ended, or, as a
        last resort, when the transcript gives no proof of an end ("unknown"/"open")
        yet the PTY has provably sat at its idle prompt for the full stall window.
        A legitimately long tool call keeps "esc to interrupt" on screen, so the PTY
        check never cuts it short.
        """
        from .observation import transcript_tail_turn_state

        record = session.record
        if record.backend not in {"claude", "codex"}:
            return
        await self._drain_hook_spool(session)
        if record.state not in {"working", "awaiting"}:
            return
        if session.observation_replay:
            return
        stalled = now - session.last_state_change_ts
        pty_state = self._pty_tail_state(session)
        # An answered permission prompt is resolved from the PTY alone and must
        # be checked before the transcript-quiet gate below: after approval the
        # transcript is usually *busy*, which would skip this pass entirely.
        if (
            watchdog_decision(
                record.state,
                stalled_seconds=stalled,
                tail_verdict=None,
                pty_state=pty_state,
            )
            == "resume_working"
        ):
            await apply_watchdog_recovery(
                session,
                self.events,
                "resume_working",
                stalled_seconds=stalled,
                tail_verdict=None,
            )
            return
        if stalled < STATE_WATCHDOG_ENDED_STUCK_SECONDS:
            return
        path = session.transcript_path
        if path is None:
            return
        try:
            mtime = path.stat().st_mtime
        except OSError:
            return
        if now - mtime < STATE_WATCHDOG_TRANSCRIPT_QUIET_SECONDS:
            # The transcript is still moving; the live observer owns it.
            return
        verdict = await asyncio.to_thread(transcript_tail_turn_state, record.backend, path)
        # PTY ground-truth backstop. The transcript may give no proof the turn
        # ended: "unknown" (schema drift) or "open" (the tail is a tool_use/
        # tool_result, so by the record the model still owes a response). Both
        # happen when the true terminal signal is missing entirely — a Stop hook
        # that never fired, a turn interrupted or crashed before its marker landed,
        # an observer stuck on the wrong sibling transcript. In every such case the
        # CLI sitting at its idle input prompt for the full stall window is
        # decisive: the PTY belongs to *this* session (never mis-attributed like a
        # transcript can be), and a live turn always shows "esc to interrupt", so
        # _pty_appears_idle stays False through any genuine in-flight tool.
        action = watchdog_decision(
            record.state,
            stalled_seconds=stalled,
            tail_verdict=verdict,
            pty_state=pty_state,
        )
        if action == "none":
            return
        await apply_watchdog_recovery(
            session,
            self.events,
            action,
            stalled_seconds=stalled,
            tail_verdict=verdict,
        )

    async def _drain_hook_spool(self, session: Session) -> None:
        """Replay hook events the shim spooled to disk after a failed POST.

        Stop/SessionEnd delivered over a lost HTTP request would otherwise vanish;
        the shim appends them here as a durable fallback that the daemon consumes.
        """
        if self.hook_spool_dir is None:
            return
        from .observation import apply_hook_observation

        path = self.hook_spool_dir / f"{session.record.id}.jsonl"
        try:
            stat = path.stat()
        except OSError:
            return
        if not stat.st_size:
            return
        # Only consume when the shim is not mid-append, so a partial line is never
        # parsed and a concurrent write is not truncated away.
        if time.time() - stat.st_mtime < 1.0:
            return
        try:
            data = path.read_bytes()
        except OSError:
            return
        consumed = data.rfind(b"\n") + 1
        if not consumed:
            return
        remainder = data[consumed:]
        try:
            path.write_bytes(remainder)
        except OSError:
            return
        for line in data[:consumed].split(b"\n"):
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line.decode("utf-8", "replace"))
            except json.JSONDecodeError:
                continue
            if not isinstance(item, dict):
                continue
            event_type = str(item.get("event") or item.get("type") or "")
            payload = item.get("payload")
            if not event_type or not isinstance(payload, dict):
                continue
            session.last_hook_ts = time.time()
            session.state_transitions.append(
                {"ts": time.time(), "kind": "hook_spool_replay", "event": event_type}
            )
            try:
                await apply_hook_observation(session, event_type, payload, self.events)
            except Exception:
                log.exception("failed to replay spooled hook %s", event_type)

    @staticmethod
    def _pty_tail_state(session: Session) -> PtyTailState:
        """Classify what this session's CLI is showing right now."""
        scrollback = getattr(session, "scrollback", None)
        if scrollback is None:
            return "unknown"
        try:
            tail = scrollback.bytes()[-8192:].decode("utf-8", "replace")
        except (OSError, ValueError):
            return "unknown"
        return pty_tail_state(tail)

    def _pty_appears_idle(self, session: Session) -> bool:
        """Apply the shared idle-prompt heuristic to this session's scrollback tail."""
        return self._pty_tail_state(session) == "idle"

    async def _fanout(self, session: Session) -> None:
        while True:
            chunk = await session.pty.output_queue.get()
            if chunk == b"":
                if not session.stopping:
                    await self._mark_ended(session, "process_exit")
                return
            session.record.last_activity_ts = time.time()
            timing_changed = False
            if "first_output" not in session.record.startup_timing_ms:
                first_output_at = getattr(session.pty, "first_output_at", None)
                session.record.startup_timing_ms["first_output"] = round(
                    ((first_output_at or time.perf_counter()) - session.startup_started_at) * 1000,
                    1,
                )
                timing_changed = True
            session.output_window.append((session.record.last_activity_ts, len(chunk)))
            while (
                session.output_window
                and session.record.last_activity_ts - session.output_window[0][0] > 60
            ):
                session.output_window.popleft()
            prompt_uris = session.osc7.feed(chunk)
            if prompt_uris and "first_prompt" not in session.record.startup_timing_ms:
                session.record.startup_timing_ms["first_prompt"] = round(
                    (time.perf_counter() - session.startup_started_at) * 1000, 1
                )
                timing_changed = True
            for uri in prompt_uris:
                self._queue_runtime_cwd(session, uri)
            if prompt_uris and session.record.backend in {"claude", "codex"}:
                self._queue_agent_exit_check(session)
            session.scrollback.append(chunk)
            session.publish_output(chunk)
            if session.record.backend in {"claude", "codex"} and session.record.state == "starting":
                self._queue_agent_ready_check(session)
            if timing_changed:
                session.publish_update()
            if prompt_uris:
                self._schedule_startup_measurement(session, "first_prompt")

    def _queue_agent_ready_check(self, session: Session) -> None:
        """Use settled PTY output only while semantic startup evidence is absent."""

        task = session.agent_ready_task
        if task is not None and not task.done():
            return
        task = asyncio.create_task(
            self._confirm_agent_ready(session), name=f"agent-ready-{session.record.id}"
        )
        session.agent_ready_task = task
        session.tasks.add(task)
        task.add_done_callback(session.tasks.discard)

    async def _confirm_agent_ready(self, session: Session) -> None:
        while not session.stopping and session.record.state == "starting" and session.pty.isalive():
            quiet_for = time.time() - session.record.last_activity_ts
            remaining = AGENT_STARTUP_QUIET_SECONDS - quiet_for
            if remaining > 0:
                await asyncio.sleep(remaining)
                continue
            previous = session.record.state
            if session.transition(
                "idle",
                None,
                source="pty",
                evidence="startup_quiet_fallback",
                inferred=True,
            ):
                await self.events.emit(
                    "state_changed",
                    session_id=session.record.id,
                    source="pty",
                    previous=previous,
                    state="idle",
                    detail=None,
                    capability="startup_quiet_fallback",
                )
            return

    def _queue_agent_exit_check(self, session: Session) -> None:
        promoted_at = session.agent_promoted_at
        if promoted_at is None or time.time() - promoted_at < AGENT_EXIT_PROMPT_GRACE_SECONDS:
            return
        if session.agent_exit_check_task and not session.agent_exit_check_task.done():
            return
        task = asyncio.create_task(
            self._confirm_agent_exit(session), name=f"agent-exit-{session.record.id}"
        )
        session.agent_exit_check_task = task
        session.tasks.add(task)
        task.add_done_callback(session.tasks.discard)

    async def _confirm_agent_exit(self, session: Session) -> None:
        """Demote a promoted session once its shell prompt has returned.

        The shim posts an authenticated demotion, but agents launched without it
        (profile PATH rewrites, manual launches) would otherwise stay [claude]/
        [codex] forever. The transcript-quiet requirement keeps a still-writing
        agent from being demoted by any stray prompt sequence; the bounded retry
        absorbs exit-time transcript writes.
        """
        for _attempt in range(AGENT_EXIT_CONFIRM_ATTEMPTS):
            await asyncio.sleep(AGENT_EXIT_CHECK_INTERVAL_SECONDS)
            if session.stop_event.is_set() or session.stopping:
                return
            backend = session.record.backend
            if backend not in {"claude", "codex"}:
                return
            path = session.transcript_path
            quiet = True
            if path is not None:
                try:
                    quiet = (
                        time.time() - path.stat().st_mtime >= AGENT_EXIT_TRANSCRIPT_QUIET_SECONDS
                    )
                except OSError:
                    quiet = True
            if quiet:
                native_id = session.agent_lifecycle_id or session.record.native_session_id
                await self.demote(session.record.id, backend, native_id)
                return

    def _schedule_startup_measurement(self, session: Session, milestone: str) -> None:
        if session.startup_measurement_task is not None:
            return
        task = asyncio.create_task(
            self.events.emit(
                "session_startup_measured",
                session_id=session.record.id,
                source="daemon",
                milestone=milestone,
                backend=session.record.backend,
                shell_profile_id=session.record.shell_profile_id,
                timing_ms=dict(session.record.startup_timing_ms),
            ),
            name=f"startup-measurement-{session.record.id}",
        )
        session.startup_measurement_task = task
        session.tasks.add(task)
        task.add_done_callback(session.tasks.discard)

    def _queue_runtime_cwd(self, session: Session, uri: str) -> None:
        path = local_directory_from_osc7(uri)
        if path is None:
            self._drop_runtime_cwd(session)
            return
        now = time.monotonic()
        while session.cwd_switches and now - session.cwd_switches[0] >= 60:
            session.cwd_switches.popleft()
        if len(session.cwd_switches) >= 12:
            self._drop_runtime_cwd(session)
            return
        if session.cwd_debounce_task and not session.cwd_debounce_task.done():
            session.cwd_debounce_task.cancel()
        task = asyncio.create_task(
            self._accept_runtime_cwd(session, path), name=f"cwd-telemetry-{session.record.id}"
        )
        session.cwd_debounce_task = task
        session.tasks.add(task)

    async def _accept_runtime_cwd(self, session: Session, path: Path) -> None:
        try:
            await asyncio.sleep(1.25)
            if session.stop_event.is_set() or not path.is_dir():
                return
            value = str(path)
            if session.record.runtime_cwd_live and session.record.runtime_cwd == value:
                return
            now = time.monotonic()
            while session.cwd_switches and now - session.cwd_switches[0] >= 60:
                session.cwd_switches.popleft()
            if len(session.cwd_switches) >= 12:
                self._drop_runtime_cwd(session)
                return
            project = await resolve_project(path)
            known = await self.history.project_scope(project.id)
            session.cwd_switches.append(now)
            session.record.runtime_cwd = value
            session.record.runtime_cwd_live = True
            session.record.runtime_cwd_source = "osc7"
            session.record.runtime_cwd_updated_at = time.time()
            session.record.runtime_project_scope_id = project.id if known else None
            session.record.git = GitState()
            session.publish_update()
            await self.events.emit(
                "runtime_cwd_changed",
                session_id=session.record.id,
                source="pty",
                cwd=value,
                project_scope_id=session.record.runtime_project_scope_id,
                dropped=session.cwd_telemetry_dropped,
            )
        except asyncio.CancelledError:
            raise

    @staticmethod
    def _drop_runtime_cwd(session: Session) -> None:
        session.cwd_telemetry_dropped = min(session.cwd_telemetry_dropped + 1, 1_000_000)
        session.record.runtime_cwd_dropped = session.cwd_telemetry_dropped

    async def _begin_agent_run(self, session: Session, launch_cwd: str | None = None) -> None:
        """Capture immutable ownership for one agent invocation."""
        await self._await_registration(session)
        if session.record.agent_run_id:
            return
        cwd = Path(
            launch_cwd
            or session.record.runtime_cwd
            or session.record.spawn_cwd
            or session.record.cwd
        )
        if not cwd.is_dir():
            cwd = Path(session.record.spawn_cwd or session.record.cwd)
        project = await resolve_project(cwd)
        await self.history.register_project_scope(project)
        session.record.agent_run_id = str(uuid.uuid4())
        session.record.agent_run_started_at = time.time()
        session.record.run_cwd = str(cwd.resolve())
        session.record.run_project_scope_id = project.id
        session.record.run_repo_group_id = project.repo_group_id
        session.record.tokens_in = 0
        session.record.tokens_out = 0
        session.record.context_window = 0
        session.record.context_pct = 0
        session.record.context_peak_pct = 0
        session.record.model = None
        session.record.measurement_source = None
        if launch_cwd:
            session.record.runtime_cwd = session.record.run_cwd
            session.record.runtime_cwd_live = True
            session.record.runtime_cwd_source = "agent-launcher"
            session.record.runtime_cwd_updated_at = time.time()
            session.record.runtime_project_scope_id = project.id
        # Compatibility fields describe the active authoritative owner. Explicit
        # spawn/runtime/run fields remove the old ambiguity for new clients.
        session.record.repository_id = project.id
        session.record.project_label = project.label
        session.record.project_root = project.root
        session.record.project_scope_id = project.id
        session.record.repo_group_id = project.repo_group_id

    async def _ticker(self, session: Session) -> None:
        while not session.stopping and session.record.state not in {"exited", "crashed"}:
            await asyncio.sleep(1)
            if not session.pty.isalive():
                await self._mark_ended(session, "process_exit")
                return
            if session.meta_sink is not None:
                # Catches metadata changes that never pass through
                # publish_update (e.g. an observer retargeting the transcript
                # path); the client dedupes, so unchanged state costs nothing.
                try:
                    session.meta_sink()
                except Exception:
                    log.debug("session meta sink failed", exc_info=True)

    async def _mark_ended(self, session: Session, reason: str) -> None:
        if session.record.state in {"exited", "crashed"}:
            return
        exit_status = getattr(session.pty, "exit_status", None)
        exit_code = exit_status() if callable(exit_status) else None
        session.record.exit_code = exit_code
        state, final_reason, detail = terminal_exit_outcome(
            session.record.completion_mode,
            stopping=session.stopping,
            exit_code=exit_code,
            reason=reason,
        )
        # Ledger the terminal transition without an update frame: publish_exit
        # below already carries the final snapshot to every subscriber.
        apply_state_transition(
            session,
            state,
            detail if detail is not None else session.record.state_detail,
            source="pty",
            evidence=f"process_exit:{final_reason}",
            force=True,
        )
        release_pty = getattr(session.pty, "release", None)
        if callable(release_pty):
            # Session scrollback is independent of ConPTY. Detach the ended
            # pseudoconsole now so its conhost cannot live as long as this
            # retained crashed/completed session record.
            release_pty()
        session.record.last_activity_ts = time.time()
        self._schedule_startup_measurement(session, "session_end")
        if session.startup_measurement_task:
            await asyncio.gather(session.startup_measurement_task, return_exceptions=True)
        await self._await_registration(session)
        session.agent_stop_event.set()
        if session.cwd_debounce_task and not session.cwd_debounce_task.done():
            session.cwd_debounce_task.cancel()
        if session.record.agent_run_id:
            await self.history.update_agent_summary(session.record)
            await self.history.agent_run_ended(session.record, final_reason)
        for adapter in self.adapters.values():
            adapter.cleanup(session.record.id)
        if self.hook_spool_dir is not None:
            (self.hook_spool_dir / f"{session.record.id}.jsonl").unlink(missing_ok=True)
        if session.ownership_job:
            session.ownership_job.close()
            session.ownership_job = None
        await self.history.session_ended(session.record, final_reason)
        session.publish_exit(final_reason)
        await self.events.emit(
            "session_exited" if session.record.state == "exited" else "session_crashed",
            session_id=session.record.id,
            source="pty",
            reason=final_reason,
            exit_code=exit_code,
        )
        if session.transcript_path and session.transcript_path.is_file():
            try:
                await self.history.index_transcript(
                    session.record.agent_run_id or session.record.id,
                    session.transcript_path,
                    session.record.backend,
                )
            except (OSError, ValueError, sqlite3.Error):
                log.warning(
                    "could not index ended session transcript %s",
                    session.record.id,
                    exc_info=True,
                )

    async def stop(self, sid: str) -> None:
        session = self.sessions[sid]
        session.stopping = True
        session.stop_event.set()
        await asyncio.to_thread(session.pty.stop)
        await self._mark_ended(session, "killed")

    async def shutdown(self, *, intent: str = "quit") -> None:
        """Stop sessions for daemon shutdown.

        ``intent="quit"`` is today's behavior: every live session is stopped
        and its end persisted. ``intent="detach"`` is the session-preserving
        reload path: supervisor-owned sessions are left running untouched
        (their metadata is flushed so the next daemon can reattach) and only
        in-process fallback sessions — which die with this process regardless —
        are stopped cleanly.
        """
        targets = [
            sid
            for sid, session in self.sessions.items()
            if session.record.state not in {"exited", "crashed"}
            and not (intent == "detach" and isinstance(session.pty, RemotePtyHost))
        ]
        await asyncio.gather(*(self.stop(sid) for sid in targets), return_exceptions=True)
        if intent == "detach" and self.supervisor is not None:
            await self.supervisor.flush_meta()

    def resolve(self, identity: str) -> Session:
        if identity in self.sessions:
            return self.sessions[identity]
        matches = [s for s in self.sessions.values() if s.record.name == identity]
        if len(matches) != 1:
            raise KeyError(identity)
        return matches[0]
