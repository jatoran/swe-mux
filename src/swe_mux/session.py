"""Agent/terminal session state and its SQLite-backed store.

Live sessions are owned by the PTY supervisor process, so they outlive daemon
restarts and app rebuilds; this module holds only the daemon-side view of them.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import re
import secrets
import sqlite3
import time
import uuid
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

from .adapters import BackendAdapter, SpawnOptions
from .adapters.claude import claude_data_home
from .background_tasks import background
from .cli_state import CliStateMonitor
from .event_bus import EventBus
from .git_projects import ProjectIdentity, resolve_project
from .harness import (
    AGENT_BACKENDS,
    Backend,
    descriptor,
    has_observable_transcript,
    reports_lifecycle_hooks,
    require_backend,
)
from .history import HistoryIndex
from .models import (
    GitState,
    SessionRecord,
    SessionState,
    StandingActivity,
    StandingActivityKind,
)
from .pty_host import PtyHost, merge_environment
from .runtime_cwd import Osc7Parser, OscSignalParser, local_directory_from_osc7
from .screen_mode import (
    BRACKETED_PASTE_TOGGLE,
    SCREEN_TOGGLE,
    BracketedPasteParser,
    ScreenModeParser,
)
from .scrollback import SCREEN_TAIL_BYTES, ScrollbackBuffer
from .spawn_contract import (
    base_session_env,
    infer_agent_executable_backend,
    session_terminal_env,
)
from .status_timeline import LedgerRing, StatusTimelineStore, note_layer_reading
from .supervisor_client import RemotePtyHost, SupervisorClient, host_for_adoption
from .terminal_arbitration import OwnerState, effective_geometry, release_owner
from .win_jobobj import ReaperJob

log = logging.getLogger(__name__)

# How often the observer looks for the agent having moved to another transcript,
# how long the current transcript must be quiet first, and how fresh the
# replacement must be to count as actively written.
TRANSCRIPT_SWITCH_POLL_SECONDS = 2.0
TRANSCRIPT_SWITCH_QUIET_SECONDS = 5.0
TRANSCRIPT_SWITCH_FRESH_SECONDS = 5.0

# When the followed transcript has been quiet this long while the PTY kept
# producing output and no replacement could be corroborated, observation is marked
# stale rather than left silently pointed at a conversation that may have been
# replaced (Codex `/new` behind an unresolvable sibling is the motivating case).
# Comfortably longer than a slow tool call so an ordinary long turn never trips it:
# a working agent writes transcript records continuously, a replaced one never will.
TRANSCRIPT_STALE_SECONDS = 90.0

# `locate_transcript` searches every project directory the backend owns rather than
# computing one path, so it is the expensive way to answer "where is this
# conversation". Cheap in absolute terms (measured 9 ms over 448 directories) but
# not at the 0.5 s cadence of the binding loop, which would spend it forever on
# sessions whose CLI simply has not written its first record yet.
TRANSCRIPT_LOCATE_INTERVAL_SECONDS = 5.0

# A shell prompt rendered while a session is promoted means the nested CLI has
# exited (the shell is otherwise blocked on it). Prompts within the grace window
# of promotion are pipeline stragglers from before/around the launch.
AGENT_EXIT_PROMPT_GRACE_SECONDS = 3.0
AGENT_EXIT_TRANSCRIPT_QUIET_SECONDS = 2.0
AGENT_EXIT_CONFIRM_ATTEMPTS = 10
AGENT_EXIT_CHECK_INTERVAL_SECONDS = 1.0

# Codex lifecycle hooks may be disabled or awaiting one-time trust, and Codex does
# not create its rollout until the first submitted turn. A short quiet period after
# live PTY output remains the last-resort signal that its interactive prompt settled.
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
try:  # psutil is optional; process identity degrades to pid-only without it.
    import psutil
except ImportError:  # pragma: no cover - diagnostics cover an unsynchronized dev venv
    psutil = None


def process_started_at(pid: int) -> float | None:
    """OS creation time for a pid, or None when it cannot be read.

    Pairing a pid with its creation time is what makes it an identity: Windows
    recycles pids aggressively, and exited sessions are retained with their pid
    intact, so anything that walks `record.pid` later must be able to prove it is
    still the same process.
    """
    if psutil is None or pid <= 0:
        return None
    try:
        return float(psutil.Process(pid).create_time())
    except Exception:  # noqa: BLE001 - psutil raises provider-specific errors
        return None


STATE_WATCHDOG_LOOP = "state-watchdog"
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
# A startup dialog (Claude workspace-trust, Codex trust/update) blocks the
# session before any turn has ever run while hook-sourced idle wins the display.
# The screen must read `approval` continuously for this long before the watchdog
# raises `awaiting(approval)` — two poll passes, so a dialog flashing through a
# repaint cannot raise a phantom block.
STATE_WATCHDOG_STARTUP_DIALOG_SECONDS = 10.0
# Classifier-drift self-check: a witnessed session continuously `working` for
# this long while every screen read in the window returns "unknown" means the
# tail classifier cannot see the current CLI at all — the 2026-07-31 failure
# mode, previously discoverable only by a user noticing a stuck status.
SCREEN_CLASSIFIER_BLIND_SECONDS = 120.0
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
# Terminal-input claims, for diagnosing which device ended up owning a session and
# why. Small: only the last few decisions matter, and they are read by hand.
CLAIM_LOG_LIMIT = 24

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
    # "watchdog-pty" is admitted for `working` under two narrow rules, both
    # enforced by watchdog_decision and both requiring the CLI's own spinner to be
    # the *last* marker on screen:
    #   - `resume_working` resolves an already-answered `awaiting` back into the
    #     turn that is still running.
    #   - `begin_pty_turn` starts a turn from `idle`, but only for an
    #     *unwitnessed* session: an agent with no bound transcript that has never
    #     received a hook, i.e. one where the PTY is the only witness there is.
    #     This is the mirror of the startup-quiet PTY fallback below. A source
    #     trusted to say "the prompt is ready" has to be trusted to say "the
    #     prompt is no longer ready", or it is a one-way ratchet into a lie: a
    #     fresh Codex pane, which cannot bind its rollout until its first
    #     `agent-turn-complete` names the thread, otherwise reports "ready · turn
    #     complete" for the entire first turn (measured live at 200 s).
    "working": frozenset({"transcript", "hook", "watchdog-pty"}),
    # `watchdog-pty` covers exactly one awaiting: the startup-dialog rule, which
    # may raise `awaiting(approval)` on a session no turn has ever run — trust
    # and update dialogs block before the first turn while hook-sourced idle
    # wins the display, so no proven source can ever report them.
    "awaiting": frozenset({"transcript", "hook", "watchdog-pty"}),
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
# Fleet alarm bound for the classifier-drift self-check: one blind session can
# be a strange TUI state, but two independently blind sessions mean the screen
# classifier cannot read the current CLI generation at all.
STATUS_HEALTH_CLASSIFIER_BLIND_SESSIONS = 2


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
    classifier_blind_sessions: list[str] = []
    # Fleet totals for the consolidation counters (per-session values ride each
    # session's own counters dict): classifier blindness, CLI side-state
    # disagreement, and deterministically observed nested children.
    consolidation_counters: dict[str, int] = {}
    native_claims: dict[tuple[str, str], list[str]] = {}
    path_claims: dict[tuple[str, str], list[str]] = {}
    for session in sessions:
        record = session.record
        if record.backend not in AGENT_BACKENDS:
            continue
        if record.state not in {"exited", "crashed"}:
            if record.native_session_id:
                native_claims.setdefault(
                    (record.backend, record.native_session_id), []
                ).append(record.id)
            transcript_path = getattr(session, "transcript_path", None)
            if transcript_path:
                path_claims.setdefault(
                    (record.backend, str(transcript_path).casefold()), []
                ).append(record.id)
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
        session_counters = health["counters"]
        if session_counters.get("screen_classifier_blind"):
            classifier_blind_sessions.append(record.id)
        for key in (
            "screen_classifier_blind",
            "cli_state_disagrees",
            "nested_children_observed",
            "standing_activity_expired",
        ):
            if session_counters.get(key):
                consolidation_counters[key] = (
                    consolidation_counters.get(key, 0) + session_counters[key]
                )
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
    # No two live sessions may claim one conversation or tail one transcript;
    # a violation is exactly the cross-attribution that renders one session's
    # status and tokens under another's identity, so it alarms.
    identity_collisions = [
        {"kind": kind, "backend": backend, "value": value, "sessions": sorted(ids)}
        for kind, claims in (
            ("native_session_id", native_claims),
            ("transcript_path", path_claims),
        )
        for (backend, value), ids in claims.items()
        if len(ids) > 1
    ]
    if identity_collisions:
        alarm_reasons.append("identity_collision")
    if len(classifier_blind_sessions) >= STATUS_HEALTH_CLASSIFIER_BLIND_SESSIONS:
        alarm_reasons.append("screen_classifier_blind")
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
        "classifier_blind_sessions": classifier_blind_sessions,
        "consolidation_counters": consolidation_counters,
        "identity_collisions": identity_collisions,
        "bounds": {
            "max_inferred_terminal_ratio": STATUS_HEALTH_MAX_INFERRED_TERMINAL_RATIO,
            "min_terminals_for_ratio_alarm": STATUS_HEALTH_MIN_TERMINALS_FOR_RATIO_ALARM,
            "stuck_active_seconds": STATUS_HEALTH_STUCK_ACTIVE_SECONDS,
            "classifier_blind_sessions": STATUS_HEALTH_CLASSIFIER_BLIND_SESSIONS,
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


# Claude is spawned with an injected `--session-id <uuid>`, and the transcript
# stem is that uuid. A native id in this shape is therefore authoritative: no
# other file in the shared per-cwd directory belongs to this session.
_CLAUDE_NATIVE_ID = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE
)
# Allowance between "the agent run began" and "the CLI created its transcript",
# absorbing clock granularity and CLI startup ordering.
_TRANSCRIPT_CREATION_SLACK_SECONDS = 5.0


def file_created_at(path: Path) -> float | None:
    """Creation time for a transcript, or None when it cannot be read.

    `st_birthtime` is the real creation time where the platform provides it
    (Windows always does); `st_ctime` is the documented fallback.
    """
    try:
        stat = path.stat()
    except OSError:
        return None
    birthtime = getattr(stat, "st_birthtime", None)
    if isinstance(birthtime, int | float) and birthtime > 0:
        return float(birthtime)
    return float(stat.st_ctime)


def _safe_mtime(path: Path) -> float | None:
    """The filesystem's last-write time, for diagnostics only.

    Never a liveness signal on its own — see `_transcript_last_write_ts`.
    """
    try:
        return float(path.stat().st_mtime)
    except OSError:
        return None


def _consuming_spool_path(spool: Path) -> Path:
    """Sibling name used while a spool file is being replayed.

    Suffix-appended rather than suffix-replaced so a session id containing a dot
    cannot collide with the live spool.
    """
    return spool.parent / f"{spool.name}.consuming"


# Once a session reaches one of these the process is gone. Only the daemon (which
# observed the exit and wrote the durable history rows) may move it out again —
# e.g. relaunch, which constructs a fresh record anyway.
TERMINAL_STATES = frozenset({"exited", "crashed"})


def _record_transition_refusal(
    session: Any,
    state: SessionState,
    source: str,
    evidence: str | None,
    now: float,
    monotonic_now: float,
) -> None:
    """Ledger a rejected transition so a refused resurrection is diagnosable."""
    ledger = getattr(session, "state_transitions", None)
    if ledger is None:
        return
    ledger.append(
        {
            "ts": now,
            "monotonic": monotonic_now,
            "kind": "transition_refused",
            "state": state,
            "previous": getattr(session.record, "state", None),
            "source": source,
            "evidence": evidence,
            "reason": "terminal_latch",
        }
    )


# --- turn completions: the counter the sidebar's unread tier reads -----------
#
# "Has this agent finished saying something since I last looked?" is a question
# none of the status axes answer on their own, and it must not be answered from
# PTY traffic: a resize repaint, an idle spinner, and a finished turn are the
# same bytes. It is answered here, from the transition contract, because only
# the daemon sees every transition - and the acknowledgement is held on the
# record for the same reason a per-browser mark could not work: it has to follow
# the user between devices and survive a reload.


def is_turn_completion(previous: SessionState | None, state: SessionState) -> bool:
    """True when a transition means the agent just stopped holding the floor.

    An approval counts from any state but itself - it is the loudest thing a
    session can want, and one raised from `idle` still needs the user. Otherwise
    only `working -> idle` qualifies. `starting -> idle` is the CLI finishing its
    boot rather than the agent finishing a turn, and counting it would make every
    freshly spawned session unread before it had said anything; `awaiting -> idle`
    is the human answering the prompt, which is their own action, not news.
    """
    if state == "awaiting":
        return previous != "awaiting"
    return state == "idle" and previous == "working"


def note_turn_completion(
    session: Any, *, source: str, evidence: str | None, now: float
) -> int | None:
    """Advance the turn counter, returning the turn that just completed.

    Deliberately does not append a ledger entry of its own: the transition that
    completed the turn is already being ledgered, and carrying `turn_seq` on
    that entry says the same thing without doubling the durable timeline's write
    rate. `/api/sessions/{sid}/state-log` can still answer "why did this row
    light up?" - the answer is the entry whose `turn_seq` matches.
    """
    record = getattr(session, "record", None)
    if record is None or not hasattr(record, "turn_seq"):
        return None
    record.turn_seq = int(getattr(record, "turn_seq", 0) or 0) + 1
    record.last_turn_end_ts = now
    record.last_turn_evidence = f"{source}:{evidence}" if evidence else source
    return int(record.turn_seq)


def acknowledge_turns(
    record: Any, turn_seq: int | None = None, *, now: float | None = None
) -> bool:
    """Mark this session read up to `turn_seq` (its current counter when None).

    Clamped to the counter the daemon has actually reached, so a stale or
    hand-written client cannot acknowledge a turn that has not happened yet and
    silently swallow the next real one. Monotone: a second device that is behind
    cannot un-read what the first already cleared, which is what makes two
    browsers and a phone converge instead of fighting. Returns True when the
    mark actually moved.
    """
    current = int(getattr(record, "turn_seq", 0) or 0)
    target = current if turn_seq is None else min(int(turn_seq), current)
    if target <= int(getattr(record, "read_turn_seq", 0) or 0):
        return False
    record.read_turn_seq = target
    record.read_at = time.time() if now is None else now
    return True


def apply_state_transition(
    session: Any,
    state: SessionState,
    detail: str | None,
    *,
    source: str,
    evidence: str | None = None,
    inferred: bool | None = None,
    awaiting_reason: str | None = None,
    idle_reason: str | None = None,
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
    record_state = getattr(session.record, "state", None)
    if record_state in TERMINAL_STATES and state not in TERMINAL_STATES and source != "daemon":
        # Terminal latch. Exit is owned by the daemon (it saw the process end and
        # wrote the durable history rows); a late hook — a spooled SessionEnd that
        # the shim recreated after the unlink, say — must not resurrect a dead
        # session to idle. `force` deliberately does not override this: force
        # reclaims arbitration between live sources, not from process reality.
        _record_transition_refusal(session, state, source, evidence, now, monotonic_now)
        return False
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
    if state != "idle":
        idle_reason = None
    previous = record.state
    previous_awaiting = getattr(record, "awaiting_reason", None)
    previous_idle_reason = getattr(record, "idle_reason", None)
    changed = (
        previous != state
        or record.state_detail != detail
        or previous_awaiting != awaiting_reason
        or previous_idle_reason != idle_reason
    )
    session.state_source_priority = priority
    if not changed:
        return False
    proof = transition_proof(source, inferred)
    # Counted here, inside the one funnel every source arbitrates through, so a
    # turn cannot be counted twice when the hook and the transcript both report
    # it: whichever lands first is the transition, and the second is the no-op
    # `changed` check that returned above. A refused transition never reaches
    # this line, so a late hook against a dead session announces nothing.
    completed_turn = (
        note_turn_completion(session, source=source, evidence=evidence, now=now)
        if is_turn_completion(previous, state)
        else None
    )
    seconds_in_previous: float | None = None
    if previous != state:
        previous_change_monotonic = getattr(session, "last_state_change_monotonic", None)
        if previous_change_monotonic is not None:
            seconds_in_previous = max(0.0, monotonic_now - previous_change_monotonic)
        session.last_state_change_ts = now
        session.last_state_change_monotonic = monotonic_now
        # The record's mirror of the same instant. Wall-clock rather than
        # monotonic because it crosses the API boundary to a browser that has no
        # access to this process's clock origin.
        record.state_since = now
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
        "idle_reason": idle_reason,
        "source": source,
        "priority": priority,
        "evidence": evidence,
        "proof": proof,
        "allowed_source": source in STATE_EVIDENCE_SOURCES.get(state, frozenset()),
        "seconds_in_previous": (
            round(seconds_in_previous, 3) if seconds_in_previous is not None else None
        ),
    }
    # Present only on the transitions that completed a turn, so the timeline can
    # be filtered to them without carrying a null on every other row.
    if completed_turn is not None:
        entry["turn_seq"] = completed_turn
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
    if hasattr(record, "idle_reason"):
        record.idle_reason = idle_reason
    return True


# --- standing-activity annotations (the fifth status axis) -------------------
#
# Standing engagements — an armed /loop, a cron schedule, background tasks,
# live subagents — are annotations on the session, never states: they change
# nothing about SessionState, awaiting_reason, or delivery. Shared by the live
# Session and the replay harness the same way apply_state_transition is, so the
# add/refresh/expire/clear discipline cannot drift from the golden corpus.
# Every mutation lands in the transition ledger as a non-transition entry
# (kind "standing_activity", action added|updated|removed|expired).

# Grace added to evidence-implied expiries so a healthy re-arm (a loop wakeup
# firing and the model re-scheduling) does not flap the annotation.
STANDING_ACTIVITY_TTL_SLACK_SECONDS = 120.0
# Annotations refreshed by evidence recency (subagents, background tasks)
# decay after this long without any supporting evidence.
STANDING_ACTIVITY_QUIET_SECONDS = 120.0


def _standing_activity_ledger(
    session: Any,
    action: str,
    activity: StandingActivity,
    now: float,
    evidence: str | None = None,
) -> None:
    ledger = getattr(session, "state_transitions", None)
    if ledger is None:
        return
    ledger.append(
        {
            "ts": now,
            "kind": "standing_activity",
            "action": action,
            "activity": activity.kind,
            "source": activity.source,
            "evidence": evidence if evidence is not None else activity.evidence,
            "count": activity.count,
            "expires_at": activity.expires_at,
            "detail": activity.detail,
        }
    )


def set_standing_activity(
    session: Any,
    kind: StandingActivityKind,
    *,
    source: str,
    evidence: str,
    expires_at: float | None = None,
    count: int | None = None,
    detail: str | None = None,
    since: float | None = None,
    now: float | None = None,
) -> bool:
    """Add or refresh one annotation. Returns True when the visible set changed.

    ``count``/``detail`` left at None keep an existing annotation's values, so a
    corroborating source that cannot know them (the PTY's background-wait line
    knows *that* tasks run, not how many) refreshes without clobbering the
    counting source. A pure TTL refresh (unchanged count and detail) updates the
    expiry silently: subagent/background evidence renews at tool-record cadence,
    and ledgering or fanning out every renewal would bury the entries that matter.
    """
    now = time.time() if now is None else now
    record = session.record
    for activity in record.standing_activity:
        if activity.kind != kind:
            continue
        next_count = activity.count if count is None else count
        next_detail = activity.detail if detail is None else detail
        changed = bool(activity.count != next_count or activity.detail != next_detail)
        activity.source = source
        activity.evidence = evidence
        activity.expires_at = expires_at
        activity.count = next_count
        activity.detail = next_detail
        if changed:
            _standing_activity_ledger(session, "updated", activity, now)
        return changed
    activity = StandingActivity(
        kind=kind,
        source=source,
        evidence=evidence,
        since=now if since is None else since,
        expires_at=expires_at,
        count=1 if count is None else count,
        detail=detail,
    )
    record.standing_activity.append(activity)
    _standing_activity_ledger(session, "added", activity, now)
    return True


#: Annotation kinds that mean work is running *right now*, as opposed to a
#: scheduled engagement (`loop`, `cron`) with nothing in flight. This is the same
#: split the dot uses (`hasRunningActivity` in the frontend) and the one the
#: notification path suppresses on: an idle session with live subagents has not
#: finished, while an idle session with an armed loop genuinely has.
RUNNING_ACTIVITY_KINDS: frozenset[str] = frozenset({"subagents", "background_tasks"})


def standing_activity_kinds(session: Any) -> list[str]:
    """The open annotation kinds, for callers that only need the shape of the set.

    Events carry this rather than whole annotations: a consumer deciding whether
    to interrupt the human needs to know *that* subagents are running, never
    their count, source, or expiry, and a fat payload on every transition is paid
    by every websocket client.
    """
    record = getattr(session, "record", None)
    return [activity.kind for activity in getattr(record, "standing_activity", ()) or ()]


def clear_standing_activity(
    session: Any,
    kind: StandingActivityKind,
    *,
    evidence: str,
    now: float | None = None,
) -> bool:
    """Positively clear one annotation. Returns True when it existed."""
    now = time.time() if now is None else now
    record = session.record
    for activity in record.standing_activity:
        if activity.kind == kind:
            record.standing_activity.remove(activity)
            _standing_activity_ledger(session, "removed", activity, now, evidence=evidence)
            return True
    return False


def expire_standing_activity(session: Any, *, now: float | None = None) -> list[str]:
    """TTL sweep: drop every annotation past its expiry. Returns dropped kinds.

    An expiry is an annotation that was never positively cleared — usually the
    normal decay path (a loop that stopped being re-armed), but a rising rate is
    a drift signal, so each one also counts in status health.
    """
    now = time.time() if now is None else now
    record = session.record
    expired = [
        activity
        for activity in record.standing_activity
        if activity.expires_at is not None and now >= activity.expires_at
    ]
    if not expired:
        return []
    counters = getattr(session, "status_health_counters", None)
    for activity in expired:
        record.standing_activity.remove(activity)
        _standing_activity_ledger(session, "expired", activity, now)
        if counters is not None:
            counters["standing_activity_expired"] = (
                counters.get("standing_activity_expired", 0) + 1
            )
    return [activity.kind for activity in expired]


def clear_all_standing_activity(session: Any, *, evidence: str, now: float | None = None) -> bool:
    """Run-scope clear for lifecycle seams (rollover, heal, promote, demote, end).

    The annotations belong to the agent run that just ended; carrying any of
    them into the successor is the annotation-axis version of cross-attribution.
    """
    now = time.time() if now is None else now
    record = session.record
    if not record.standing_activity:
        return False
    for activity in tuple(record.standing_activity):
        _standing_activity_ledger(session, "removed", activity, now, evidence=evidence)
    record.standing_activity.clear()
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


WatchdogAction = Literal[
    "none",
    "force_idle_ended",
    "force_idle_pty",
    "resume_working",
    "begin_pty_turn",
    "end_pty_turn",
    "startup_dialog_block",
    "startup_dialog_clear",
]

# What the CLI is showing right now, read from named parts of its terminal
# evidence. Agent-owned viewers deliberately return `uninformative`: their body
# text describes another screen and must not be mistaken for live lifecycle
# evidence by any PTY consumer.
PtyTailState = Literal["working", "approval", "idle", "uninformative", "unknown"]
ScreenRuleState = Literal[
    "working", "approval", "idle", "background_wait", "uninformative"
]
ScreenRegionKind = Literal[
    "whole_tail",
    "bottom_non_empty_lines",
    "after_last_prompt_marker",
    "osc_title",
    "osc_progress",
]


@dataclass(frozen=True, slots=True)
class ScreenRegion:
    kind: ScreenRegionKind
    lines: int = 0


@dataclass(frozen=True, slots=True)
class ScreenRule:
    id: str
    state: ScreenRuleState
    region: ScreenRegion
    predicate: re.Pattern[str]
    backends: frozenset[str] | None = None


WHOLE_TAIL = ScreenRegion("whole_tail")
AFTER_LAST_PROMPT_MARKER = ScreenRegion("after_last_prompt_marker")
OSC_TITLE = ScreenRegion("osc_title")
OSC_PROGRESS = ScreenRegion("osc_progress")


def bottom_non_empty_lines(lines: int) -> ScreenRegion:
    if lines < 1:
        raise ValueError("screen region line count must be positive")
    return ScreenRegion("bottom_non_empty_lines", lines)


def _contains(value: str) -> re.Pattern[str]:
    return re.compile(re.escape(value), re.IGNORECASE)


# Declared order is conflict precedence. The first matching rule wins.
# Viewer predicates are deliberately conjunctive and footer-scoped so ordinary
# transcript prose containing words such as "model" or "resume" cannot hide a
# live state reading.
PTY_RULES: tuple[ScreenRule, ...] = (
    ScreenRule(
        "viewer.model_picker",
        "uninformative",
        bottom_non_empty_lines(12),
        re.compile(r"(?is)(?:select|choose) (?:a )?model.*(?:enter to select|esc to cancel)"),
    ),
    ScreenRule(
        "viewer.resume_list",
        "uninformative",
        bottom_non_empty_lines(12),
        re.compile(
            r"(?is)resume (?:a )?(?:conversation|session).*(?:enter to select|esc to cancel)"
        ),
    ),
    ScreenRule(
        "viewer.transcript",
        "uninformative",
        bottom_non_empty_lines(12),
        re.compile(r"(?is)transcript.*(?:esc to (?:close|go back)|press q to (?:quit|close))"),
    ),
    ScreenRule(
        "viewer.omp_model_picker",
        "uninformative",
        bottom_non_empty_lines(12),
        re.compile(
            r"(?is)all available models.*enter assign roles.*type to search.*esc close"
        ),
    ),
    ScreenRule(
        "viewer.omp_session_tree",
        "uninformative",
        bottom_non_empty_lines(12),
        re.compile(r"(?is)session tree.*enter: switch.*search:"),
    ),
    ScreenRule(
        "approval.question", "approval", AFTER_LAST_PROMPT_MARKER, _contains("do you want to")
    ),
    ScreenRule(
        "approval.command",
        "approval",
        AFTER_LAST_PROMPT_MARKER,
        _contains("allow this command"),
    ),
    ScreenRule("approval.codex", "approval", AFTER_LAST_PROMPT_MARKER, _contains("allow codex to")),
    ScreenRule("approval.cancel", "approval", AFTER_LAST_PROMPT_MARKER, _contains("esc to cancel")),
    ScreenRule("approval.amend", "approval", AFTER_LAST_PROMPT_MARKER, _contains("tab to amend")),
    ScreenRule(
        "approval.confirm",
        "approval",
        AFTER_LAST_PROMPT_MARKER,
        _contains("enter to confirm"),
    ),
    ScreenRule(
        "working.interrupt",
        "working",
        AFTER_LAST_PROMPT_MARKER,
        _contains("esc to interrupt"),
    ),
    ScreenRule(
        "working.spinner",
        "working",
        AFTER_LAST_PROMPT_MARKER,
        _contains("…"),
        frozenset({"claude"}),
    ),
    ScreenRule("idle.shortcuts", "idle", AFTER_LAST_PROMPT_MARKER, _contains("? for shortcuts")),
    ScreenRule(
        "idle.mode_cycle",
        "idle",
        AFTER_LAST_PROMPT_MARKER,
        _contains("(shift+tab to cycle)"),
    ),
    ScreenRule(
        "idle.omp_prompt",
        "idle",
        bottom_non_empty_lines(4),
        re.compile(r"(?is)π\s*>.*\(sub\)\s*▶"),
    ),
    ScreenRule(
        "background.still_running",
        "background_wait",
        AFTER_LAST_PROMPT_MARKER,
        re.compile(r"\d+\s+[a-z]+s?\s+still running", re.IGNORECASE),
    ),
    ScreenRule(
        "background.waiting_count",
        "background_wait",
        AFTER_LAST_PROMPT_MARKER,
        re.compile(r"waiting for\s+\d+\s+background task", re.IGNORECASE),
    ),
)

_PROMPT_FRAME_RULES = tuple(
    rule for rule in PTY_RULES if rule.region.kind == "after_last_prompt_marker"
)


# Escape sequences ride the same byte stream as the text and break it two ways.
# Window titles (OSC 0/2) carry arbitrary task text the CLI rewrites while
# working — text that could contain any marker, including the spinner ellipsis —
# so they are removed outright (including one the tail window cut mid-write).
# Cursor and styling codes land *inside* phrases: the current CLI positions
# every word of "Enter to confirm · Esc to cancel" at an absolute column
# (`Enter\x1b[8Gto\x1b[11Gconfirm`), so the spacing exists only as cursor
# movement and the marker is not a contiguous substring of the raw stream.
# Cursor movement therefore reads as a space, styling as nothing, and runs of
# whitespace collapse — restoring write-order prose, which is what the ordering
# comparison has always meant.
_OSC_TITLE_SEQUENCE = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
_OSC_TITLE_UNTERMINATED = re.compile(r"\x1b\][^\x07\x1b]*\Z")
_CSI_CURSOR_MOVEMENT = re.compile(r"\x1b\[[0-9;]*[ABCDEFGHfd]")
_CSI_OR_SIMPLE_ESCAPE = re.compile(r"\x1b\[[0-9;:?<=>]*[A-Za-z@`~]|\x1b[=>()#][0-9A-Za-z]?")
_TAIL_HORIZONTAL_WHITESPACE_RUN = re.compile(r"[^\S\r\n\x0b\x0c]+|[\x00-\x08\x0b\x0c\x0e-\x1f]+")


def _normalize_tail_text(tail: str) -> str:
    text = _OSC_TITLE_UNTERMINATED.sub("", _OSC_TITLE_SEQUENCE.sub("", tail))
    text = _CSI_CURSOR_MOVEMENT.sub(" ", text)
    text = _CSI_OR_SIMPLE_ESCAPE.sub("", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return _TAIL_HORIZONTAL_WHITESPACE_RUN.sub(" ", text)


def screen_region_text(
    region: ScreenRegion,
    normalized_tail: str,
    *,
    osc_title: str | None = None,
    osc_progress: str | None = None,
) -> str:
    if region.kind == "whole_tail":
        return normalized_tail
    if region.kind == "bottom_non_empty_lines":
        lines = [line for line in normalized_tail.splitlines() if line.strip()]
        return "\n".join(lines[-region.lines :])
    if region.kind == "osc_title":
        return osc_title or ""
    if region.kind == "osc_progress":
        return osc_progress or ""
    starts = [
        match.start()
        for rule in _PROMPT_FRAME_RULES
        for match in rule.predicate.finditer(normalized_tail)
    ]
    return normalized_tail[max(starts, default=0) :]


def pty_tail_explain(
    tail: str,
    *,
    backend: str | None = None,
    osc_title: str | None = None,
    osc_progress: str | None = None,
) -> dict[str, Any]:
    """Return the classifier outcome and bounded evidence for every rule."""
    normalized = _normalize_tail_text(tail)
    # Most rules share one of only a few regions. Computing the region per rule
    # repeatedly rescanned the whole 32 KiB tail, including every prompt-frame
    # regular expression, for each readiness evaluation. Fleet snapshots evaluate
    # every session, so that redundant scan became a daemon event-loop hot path.
    regions: dict[ScreenRegion, str] = {}
    evaluations: list[dict[str, Any]] = []
    outcome: PtyTailState = "unknown"
    for rule in PTY_RULES:
        applicable = rule.backends is None or backend is None or backend in rule.backends
        text = regions.get(rule.region)
        if text is None:
            text = screen_region_text(
                rule.region,
                normalized,
                osc_title=osc_title,
                osc_progress=osc_progress,
            )
            regions[rule.region] = text
        matched = applicable and rule.predicate.search(text) is not None
        evaluations.append(
            {
                "id": rule.id,
                "state": rule.state,
                "applicable": applicable,
                "region": rule.region.kind,
                "region_lines": rule.region.lines or None,
                "matched": matched,
                "preview": text[-240:],
            }
        )
        if matched and rule.state != "background_wait" and outcome == "unknown":
            outcome = cast(PtyTailState, rule.state)
    return {"outcome": outcome, "rules": evaluations}


def pty_tail_waiting_on_background(tail: str, *, backend: str | None = None) -> bool:
    """True when the live frame shows the CLI waiting on background work."""
    explanation = pty_tail_explain(tail, backend=backend)
    return any(
        item["matched"] and item["state"] == "background_wait"
        for item in explanation["rules"]
    )


def pty_tail_state(tail: str, *, backend: str | None = None) -> PtyTailState:
    """Classify the CLI's current screen from its scrollback tail."""
    return cast(PtyTailState, pty_tail_explain(tail, backend=backend)["outcome"])


def pty_tail_appears_idle(tail: str, *, backend: str | None = None) -> bool:
    """True only when the CLI's latest frame is its idle input prompt."""
    return pty_tail_state(tail, backend=backend) == "idle"


def session_is_unwitnessed(session: Any) -> bool:
    """True when this agent's PTY screen is the only source that can ever speak.

    Not "no evidence yet" — *no channel*. Both tiers that are allowed to prove
    work are structurally absent: no transcript is bound (so the ordered record
    cannot be read, provisionally or otherwise) and no hook has ever arrived (so
    the side channel has never reached this session).

    This is a real, reachable configuration rather than a defensive guard. Codex
    lifecycle hooks may be disabled, untrusted, or unreachable; because Codex
    mints its own thread id, the compatibility binding signal is then the
    `agent-turn-complete` notify at the *end* of turn one. Until then a fresh pane
    may have neither tier. It is also where a misconfigured `notify` program or a
    hook ingress the CLI cannot reach leaves a session permanently, which is why
    the predicate is written in terms of the channels rather than in terms of Codex.

    Deliberately one hook, not one *recent* hook: a session that has ever been
    witnessed has a working channel, and a temporary silence on it is what the
    stall-gated recoveries above are for.
    """
    if getattr(session.record, "backend", None) not in AGENT_BACKENDS:
        return False
    if getattr(session, "transcript_path", None) is not None:
        return False
    return not getattr(session, "last_hook_ts", 0.0)


def watchdog_decision(
    state: SessionState,
    *,
    stalled_seconds: float,
    tail_verdict: str | None,
    pty_state: PtyTailState,
    awaiting_reason: str | None = None,
    unwitnessed: bool = False,
    unwitnessed_turn_armed: bool = False,
    startup_no_turn: bool = False,
    startup_dialog_seconds: float | None = None,
    startup_dialog_raised: bool = False,
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

    ``begin_pty_turn``/``end_pty_turn`` are the *unwitnessed* pair, and the only
    rules here that read an `idle` session. ``unwitnessed`` means this agent has
    no bound transcript and has never received a hook: there is no other source
    that could ever speak, so refusing the PTY does not keep the status
    conservative, it freezes it. Both directions require the marker to be the
    *last* one on screen (`pty_tail_state` is ordering-aware), which is also what
    makes them safe around a dialog: a pending approval reads "approval", never
    "working" or "idle", so this pair can neither start a turn on top of a
    prompt the user has not answered nor close one that is still blocked.

    Deliberately symmetric and deliberately not stall-gated. The stall windows
    below exist because a *proven* source might still be about to speak; here
    nothing else can, so waiting only lengthens a status that is already wrong.

    ``tail_verdict=None`` means the transcript tail has not been read: the
    caller is doing the cheap pass that can only resume an awaiting session.
    """
    if unwitnessed:
        if state == "idle" and unwitnessed_turn_armed and pty_state == "working":
            return "begin_pty_turn"
        if state == "working" and pty_state == "idle":
            return "end_pty_turn"
    # Startup dialogs (Claude workspace-trust, Codex trust/update) block a
    # session no turn has ever run while the displayed state reads idle: the
    # SessionStart hook fires before the trust gate, and typed input lands in
    # the dialog. Gated hard on "no turn yet" so this can never fight
    # mid-conversation evidence, and on a sustained approval screen so a
    # repaint cannot raise a phantom block. The same pair un-blocks: only the
    # rule that raised the awaiting may clear it from the screen alone.
    if startup_no_turn:
        if (
            state == "idle"
            and pty_state == "approval"
            and (startup_dialog_seconds or 0.0) >= STATE_WATCHDOG_STARTUP_DIALOG_SECONDS
        ):
            return "startup_dialog_block"
        if (
            state == "awaiting"
            and startup_dialog_raised
            and pty_state in {"working", "idle"}
        ):
            return "startup_dialog_clear"
    if state not in {"working", "awaiting"}:
        return "none"
    if (
        state == "awaiting"
        # Approval is the only block whose dialog the tail classifier can see, so
        # it is the only one where "the spinner is up" proves the block is gone.
        # A Codex question or an elicitation shows neither an approval nor an idle
        # marker, while redraw history in the same tail still holds "esc to
        # interrupt" from before the block — which would resume the session to
        # `working` and hide a prompt the user must answer.
        and (awaiting_reason or "approval") == "approval"
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


def startup_dialog_observation(
    session: Any, pty_state: PtyTailState, now: float
) -> tuple[bool, float | None]:
    """Track continuous approval-screen time for a session with no turn yet.

    Returns ``(no_turn, seconds_on_approval_screen)``. Shared by the live
    watchdog pass and the replay harness so the 10 s sustain requirement cannot
    drift between them. The tracker resets the moment a turn runs or the screen
    stops reading `approval`.
    """
    state = session.observation_state
    no_turn = not state.get("root_turn_active") and not state.get("root_completion_seen")
    if not no_turn or pty_state != "approval":
        session.startup_dialog_screen_since = None
        return no_turn, None
    if session.startup_dialog_screen_since is None:
        session.startup_dialog_screen_since = now
    return True, max(0.0, now - session.startup_dialog_screen_since)


def note_classifier_blindness(session: Any, pty_state: PtyTailState, now: float) -> bool:
    """Count a witnessed working session whose screen classifier reads nothing.

    The 2026-07-31 marker-drift incident ran for weeks with `pty_tail_state`
    returning "unknown" on every busy screen — every screen-based recovery
    silently disabled, discovered only by a user noticing a stuck status. This
    turns the next CLI redesign into a counter within minutes. Zero
    false-positive cost: it changes no state, it only counts once per
    continuous blind window.
    """
    if session.record.state != "working" or session_is_unwitnessed(session):
        session.classifier_blind_since = None
        session.classifier_blind_counted = False
        return False
    if pty_state != "unknown":
        session.classifier_blind_since = None
        session.classifier_blind_counted = False
        return False
    if session.classifier_blind_since is None:
        session.classifier_blind_since = now
        return False
    if (
        session.classifier_blind_counted
        or now - session.classifier_blind_since < SCREEN_CLASSIFIER_BLIND_SECONDS
    ):
        return False
    session.classifier_blind_counted = True
    counters = getattr(session, "status_health_counters", None)
    if counters is not None:
        counters["screen_classifier_blind"] = counters.get("screen_classifier_blind", 0) + 1
    ledger = getattr(session, "state_transitions", None)
    if ledger is not None:
        ledger.append(
            {
                "ts": now,
                "kind": "screen_classifier_blind",
                "blind_seconds": round(now - session.classifier_blind_since, 3),
                "state": session.record.state,
            }
        )
    return True


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
    from .observation import _begin_root_turn, _finish_root_turn, _transition

    if action == "startup_dialog_block":
        # A blocking dialog on a session no turn has ever run. Not a turn event:
        # nothing started, nothing can finish — only the displayed state and the
        # delivery gate change. Forced: the hook-sourced startup idle holds
        # arbitration at hook priority and would refuse a PTY-priority block
        # forever. Safe to reclaim because the no-turn gate guarantees no
        # hook-raised approval can exist yet, and a new turn resets arbitration
        # so real evidence takes ownership the moment it speaks.
        session.observation_state["startup_dialog_raised"] = True
        session.note_watchdog_recovery(
            "startup_dialog_block",
            stalled_seconds=stalled_seconds,
            tail_verdict=tail_verdict,
        )
        await _transition(
            session,
            events,
            "awaiting",
            "startup dialog",
            source="watchdog-pty",
            force=True,
            inferred=True,
            awaiting_reason="approval",
            evidence="startup_dialog",
        )
        return
    if action == "startup_dialog_clear":
        # Only the rule that raised the block may clear it from the screen
        # alone; a hook- or transcript-raised awaiting never passes the gate.
        session.observation_state.pop("startup_dialog_raised", None)
        session.note_watchdog_recovery(
            "startup_dialog_cleared",
            stalled_seconds=stalled_seconds,
            tail_verdict=tail_verdict,
        )
        await _transition(
            session,
            events,
            "idle",
            source="watchdog-pty",
            force=True,
            inferred=True,
            evidence="startup_dialog_cleared",
        )
        return
    if action == "begin_pty_turn":
        # Opening a turn, not repairing one: go through the normal turn-start
        # path so the bookkeeping, `turn_started` event, and delivery readiness
        # see exactly what a transcript- or hook-started turn produces. Pre-setting
        # `root_turn_active` (as the repair actions below do) would suppress that
        # emit and leave the turn half-open.
        session.note_watchdog_recovery(
            "pty_turn_started_unwitnessed",
            stalled_seconds=stalled_seconds,
            tail_verdict=tail_verdict,
        )
        await _begin_root_turn(
            session,
            events,
            source="watchdog-pty",
            evidence="pty_working_spinner_unwitnessed",
        )
        return
    session.observation_state["root_turn_active"] = True
    session.observation_state["root_completion_seen"] = False
    if action == "end_pty_turn":
        # The same screen that opened this turn now shows the input prompt. No
        # stall window: nothing else can ever close it, so waiting would only
        # hold a session at "working" after it has visibly finished.
        session.note_watchdog_recovery(
            "pty_turn_ended_unwitnessed",
            stalled_seconds=stalled_seconds,
            tail_verdict=tail_verdict,
        )
        await _finish_root_turn(
            session,
            events,
            source="watchdog-pty",
            force=True,
            inferred=True,
            evidence="pty_idle_prompt_unwitnessed",
        )
        session.observation_state.pop("unwitnessed_turn_armed", None)
    elif action == "resume_working":
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


def reset_session_observation_state(session: Any, evidence: str) -> None:
    """Reset run-scoped observation state without leaving delayed work behind."""
    # Local import avoids the observation -> session module cycle.
    from .observation import cancel_pending_approval

    cancel_pending_approval(session, evidence)
    previous = session.observation_state if isinstance(session.observation_state, dict) else {}
    # Ordered hook delivery belongs to the live harness process, not one
    # conversation run. OMP keeps the same extension instance and sequence
    # across /new, /fork, /resume, and /clear, so resetting this ledger at a
    # rollover would let a delayed pre-rollover retry mutate the new run.
    hook_sequences = previous.get("hook_sequences")
    hook_sequence_duplicates = previous.get("hook_sequence_duplicates")
    session.observation_state = {
        "root_turn_active": False,
        "root_completion_seen": False,
        "codex_scope": "root",
    }
    if isinstance(hook_sequences, dict):
        session.observation_state["hook_sequences"] = dict(hook_sequences)
    if isinstance(hook_sequence_duplicates, int) and hook_sequence_duplicates >= 0:
        session.observation_state["hook_sequence_duplicates"] = hook_sequence_duplicates


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
        *,
        mcp_token: str | None = None,
        attach_replay_bytes: int | None = None,
    ) -> None:
        self.record, self.pty, self.adapter = record, pty, adapter
        self.scrollback = ScrollbackBuffer(max_scrollback)
        # What a fresh attach or a resync replays, as distinct from what is
        # retained above. Carried on the session rather than read per-request so
        # every attach path (browser, adopted supervisor session, tests) is bound
        # by the same policy. ``None`` replays everything retained.
        self.attach_replay_bytes = attach_replay_bytes
        self.subscribers: set[PtySubscriber] = set()
        self.tasks: set[asyncio.Task[Any]] = set()
        self.stopping = False
        self.stop_event = asyncio.Event()
        self.hook_secret = hook_secret
        # MCP caller identity (CP §7.4): minted at spawn, injected into the
        # session env, recovered from supervisor meta at adoption. Empty means
        # "no MCP identity" (pre-feature session) and must never authenticate.
        self.mcp_token = mcp_token or ""
        self.ownership_job = ownership_job
        self.startup_started_at = startup_started_at or time.perf_counter()
        self.startup_measurement_task: asyncio.Task[Any] | None = None
        self.registration_task: asyncio.Task[Any] | None = None
        self.attachments_seen = 0
        # Exactly one browser connection may write terminal-generated responses
        # and user keystrokes. Without this, two attached xterms both answer
        # device-status queries and the duplicate response appears at the prompt.
        self.input_owner: str | None = None
        # The owner's socket, so a displaced owner can be told it lost the claim.
        # Silent displacement left the previous client typing into a void.
        self.input_owner_socket: Any = None
        # Transfer counter and the owning device's label, carried on every
        # `input_owner` frame so a client can discard an ownership notification that
        # lost a race with a newer one. See terminal_arbitration.
        self.input_owner_epoch = 0
        self.input_owner_device: str | None = None
        # Monotonic time of the last gesture-backed claim or keystroke by the owner:
        # what protects an actively typed-in device from a passive re-claim by a
        # background pane on another device.
        self.input_owner_gesture_ts: float | None = None
        # Non-owner input is refused rather than silently dropped; these make the
        # refusals visible in telemetry instead of leaving the user to notice
        # missing characters by feel.
        self.input_rejections = 0
        self.last_input_reject_report_ts = 0.0
        # Monotonic stamps bounding client-requested work: a `repaint` frame makes the
        # child restate its whole transcript, and `client_diagnostic` frames are
        # persisted to the durable event log, so neither may be driven at frame rate
        # by a misbehaving or malicious page.
        self.last_client_repaint_ts = 0.0
        self.client_diagnostic_timestamps: dict[str, float] = {}
        # A drag emits geometry changes continuously, and an alternate-screen child
        # cannot repair its own screen after one (`needs_resize_repaint`). These carry
        # the trailing-edge debounce that pulses it once the size stops moving: the
        # deadline is pushed forward by every change, and one task waits it out, so a
        # gesture of any length costs exactly one pulse after the user lets go.
        self.resize_repaint_deadline = 0.0
        self.resize_repaint_task: asyncio.Task[None] | None = None
        # Passive claims refused because another device is actively being typed into:
        # the direct measure of how often a background pane tried to steal the keyboard.
        self.input_claim_denials = 0
        # Last refusal per connection, so a client that re-claims on its own refusal
        # stops being answered instead of looping. Cleared when the connection ends.
        self.claim_refusals: dict[str, float] = {}
        # Recent claim decisions, newest last. Ownership disputes are otherwise only
        # visible as a counter going up, which says a claim was refused but never
        # which device asked, what it reported about itself, or what the daemon
        # believed at the time — the three things any diagnosis actually needs.
        self.claim_log: deque[dict[str, Any]] = deque(maxlen=CLAIM_LOG_LIMIT)
        # Per-connection fitted terminal size for every attached client that reports
        # itself visible. Hidden panes deregister, so a minimized window can no longer
        # reshape ConPTY for the device a human is actually using.
        self.viewports: dict[str, tuple[int, int]] = {}
        self.geometry: tuple[int, int] | None = None
        self.revision = 0
        self.state_source_priority = -1
        # Diagnostics for end-detection health: when the state last changed, when a
        # native hook last arrived, a bounded transition/fault log, and observer
        # supervision counters.  These feed the state-log debug endpoint and the
        # quiescence watchdog; none are serialized into the frequent record snapshot.
        self.last_state_change_ts = time.time()
        self.last_state_change_monotonic = time.monotonic()
        # A record adopted from an older daemon carries no `state_since`; seed it
        # here so an age reads as "since adoption" rather than 1970.
        if not self.record.state_since:
            self.record.state_since = self.last_state_change_ts
        self.last_evidence_ts = time.time()
        self.last_hook_ts = 0.0
        # Only hooks whose event *must* have produced transcript records — a prompt
        # submitted, a tool run, a turn stopped. Staleness detection keys off this
        # and not `last_hook_ts`, because the hook that fires most often on a quiet
        # session is `Notification:idle_prompt` ("waiting for your input"), which by
        # definition accompanies no transcript activity at all. Keying off every
        # hook made every healthy idle agent look like a replaced conversation
        # 90 s after its last turn.
        self.last_turn_hook_ts = 0.0
        # Wall clock when the tailer last saw the *followed* transcript grow past
        # the bytes that existed when it attached. This, not `stat().st_mtime`, is
        # what "the file is still being written" has to be measured with.
        #
        # Windows does not keep a live file's last-write time current. Measured
        # 2026-08-06 on five Codex rollouts: every one reported an mtime frozen at
        # the file's *creation*, 290 s to 3.5 h behind content that had grown to
        # 5 MB, and `os.stat`, `GetFileAttributesExW`, `FindFirstFileW`, and
        # `GetFileInformationByHandle` all agreed — so there is no better call to
        # reach for. `st_size` stays accurate throughout, which is why the tailer's
        # own growth reading is trustworthy where the timestamp is not. Claude
        # transcripts were unaffected in the same survey, but the fix is not
        # backend-specific: nothing here may assume a CLI keeps timestamps live.
        #
        # Reset whenever the followed path changes, because it describes one file.
        self.transcript_growth_ts = 0.0
        # Provider timestamp carried by the newest valid record read from the
        # followed transcript. This covers the bind race where the first observer
        # attaches after a complete turn is already in the file: those bytes are
        # historical to the tailer, but their timestamp still proves when this
        # exact conversation wrote them. Kept separate from growth so a daemon
        # restart never invents a fresh write merely by replaying old bytes.
        self.transcript_record_ts = 0.0
        diagnostic = record.observation_diagnostic or ""
        if " last written " in diagnostic:
            self.observation_stale_reason: str | None = "transcript_stale"
        elif " is missing " in diagnostic:
            self.observation_stale_reason = "transcript_missing"
        elif record.observation_stale_since is not None:
            self.observation_stale_reason = "explicit_conversation_mismatch"
        else:
            self.observation_stale_reason = None
        # The transition ledger is a LedgerRing rather than a plain deque: each
        # append is stamped with a monotonic seq and the run id current at that
        # moment, and nudges the durable status-timeline sink when the manager
        # attached one (status_timeline.py). Producers and consumers see the
        # same bounded-ring interface as before.
        self.state_transitions: LedgerRing = LedgerRing(
            STATE_TRANSITION_LOG_LIMIT,
            run_id_provider=lambda: record.agent_run_id or record.id,
        )
        self.state_changes: deque[dict[str, Any]] = deque(maxlen=STATE_CHANGE_LOG_LIMIT)
        # Last observed reading per detection-ladder layer (pty_tail, cli_state,
        # hook_recency), so `note_layer_reading` ledgers changes and only changes.
        self.layer_readings: dict[str, str] = {}
        self.observer_restart_count = 0
        self.observer_last_fault: dict[str, Any] | None = None
        self.watchdog_recoveries = 0
        # Startup-dialog rule: when the screen began continuously reading
        # `approval` on a session no turn has ever run (see watchdog_decision).
        self.startup_dialog_screen_since: float | None = None
        # Classifier-drift self-check: when the current continuous
        # working-but-screen-unknown window began, and whether it was counted.
        self.classifier_blind_since: float | None = None
        self.classifier_blind_counted = False
        # Latest CLI-published side state for this session's conversation
        # (~/.claude/sessions/<pid>.json), corroboration only — never a
        # transition source. None until the poller matches a file.
        self.cli_state: dict[str, Any] | None = None
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
        # A transcript adopted by elimination rather than by identity evidence
        # (`_may_adopt_sole_candidate`). It may drive *state* and nothing else:
        # see `provisional_observation_blocks` for the exact list and why.
        self.transcript_provisional = False
        # A transcript path the CLI named for itself in a hook, staged by the
        # ingress task for the observer's switch watcher to pick up. Two objects
        # because they answer different questions: the path is *what* to follow, the
        # event is *when* — without it a relocation would wait out a poll tick while
        # the session showed a frozen conversation.
        self.pending_transcript_path: Path | None = None
        self.transcript_relocation_signal = asyncio.Event()
        self.detection_task: asyncio.Task[Any] | None = None
        # The launcher-generated lifecycle id remains stable even when an
        # adapter later discovers and records a different native transcript id.
        # Demotion must match this token so Codex can return to its parent shell.
        self.agent_lifecycle_id: str | None = None
        # Root prompts this pane's user submitted, bounded and captured from the hook
        # ingress, so a session can be titled from the user's actual request before any
        # turn has completed — the tab needs a name at the moment you spawn three panes,
        # not a minute later. Both belong to the conversation, so a rollover clears them.
        #
        # `first_user_prompt` is what the titler reads, and it is deliberately not the
        # latest one: a title attempt that fails (a provider rate limit is the usual
        # cause) is retried on a later turn, and titling that retry from the newest
        # prompt is what made tab names drift away from what the session is about.
        self.first_user_prompt: str | None = None
        self.last_user_prompt: str | None = None
        # Detection is a fallback for agents launched without the mux shim. Once a
        # native run has explicitly exited, its still-recent transcript must not
        # immediately promote the containing shell again. An explicit launcher
        # promotion may reuse the same native id (for example, resume).
        self.ignored_detection_runs: set[tuple[str, str]] = set()
        self.osc7 = Osc7Parser()
        self.osc_signals = OscSignalParser()
        self.cwd_debounce_task: asyncio.Task[Any] | None = None
        self.cwd_switches: deque[float] = deque()
        self.cwd_telemetry_dropped = 0
        self.last_input_event_ts = 0.0
        self.last_input_report_ts = 0.0
        self.input_revision = 0
        # Two sources for the same fact, deliberately kept apart. The daemon reads
        # the child's own screen switch off the PTY and so has it for every
        # session; the browser reports xterm's active buffer only while a pane is
        # attached and owns input, which makes it corroboration rather than the
        # source of record (`delivery-readiness.md`).
        self.screen = ScreenModeParser()
        self.bracketed_paste = BracketedPasteParser()
        self.terminal_mode: str | None = None
        self.terminal_mode_updated_at = 0.0
        self.observation_state: dict[str, Any] = {
            "root_turn_active": False,
            "root_completion_seen": False,
            "codex_scope": "root",
        }
        # A short-lived approval candidate is mirrored separately from the
        # public SessionRecord. The supervisor retains it across daemon reloads
        # so the stabilization timer cannot silently disappear mid-window.
        self.approval_candidate: dict[str, Any] | None = None
        # True while the observer replays transcript content that predates its
        # attachment; state transitions and update fanout are suppressed then.
        self.observation_replay = False
        # Set when this PTY was promoted around a nested agent CLI; used to
        # ignore shell-prompt echoes from just before/around the promotion.
        self.agent_promoted_at: float | None = None
        # Agent backends whose name has appeared in this (still unpromoted) shell's
        # output: an agent launch is in flight and will create a transcript here.
        self.pending_agent_backends: set[str] = set()
        self.agent_exit_check_task: asyncio.Task[Any] | None = None
        self.agent_ready_task: asyncio.Task[Any] | None = None
        self.output_window: deque[tuple[float, int]] = deque()
        # Native transcripts report tool results by opaque invocation id.  Keep
        # this correlation in memory only; normalized events expose the stable
        # tool name without persisting backend-specific transcript identifiers.
        self.tool_names: dict[str, str] = {}
        # Same correlation for the tool's normalized target, so a tool_result can
        # carry the target its tool_use captured (Tier 0 fingerprints results
        # per-action rather than collapsing them onto one value).
        self.tool_targets: dict[str, str] = {}
        # Supervisor-backed sessions mirror their metadata (record snapshot,
        # hook secret, transcript path) into the supervisor so a future daemon
        # can rebuild this Session after a restart. None for in-process PTYs.
        self.meta_sink: Callable[[], None] | None = None
        # Called by the hook path the moment this session's conversation id is
        # proven, so the manager can confirm or discard a provisional transcript
        # binding. Sync: it schedules its own task rather than blocking the hook.
        self.provisional_binding_sink: Callable[[], None] | None = None

    def subscribe(self, maxsize: int = 1024) -> PtySubscriber:
        subscriber = PtySubscriber(asyncio.Queue(maxsize=maxsize))
        self.subscribers.add(subscriber)
        return subscriber

    def replay_and_subscribe(self) -> tuple[dict[str, Any], int, bytes, PtySubscriber]:
        """Atomically snapshot replay bytes and register for subsequent output.

        This method has no await points, so the single event-loop fanout task cannot
        append output between the snapshot and subscription. A new attachment therefore
        neither misses nor duplicates the boundary chunk.

        `attach_replay_bytes` bounds only what this attach replays
        (`ScrollbackBuffer.tail`); retention is untouched and later output is
        unaffected.
        """
        subscriber = self.subscribe()
        return self.record.snapshot(), self.revision, self.replay_bytes(), subscriber

    def replay_bytes(self) -> bytes:
        """Retained output for a fresh attach, bounded and self-contained.

        A bounded window can begin after the child selected the alternate screen,
        which would leave the client painting a full-screen TUI into its *normal*
        buffer - every repaint growing scrollback instead of overwriting one
        screen, which is precisely the cost the bound exists to remove. The daemon
        tracks the mode from the stream itself (`screen_mode.py`), so it can
        restate it.

        Only restated when the window carries no toggle of its own. A window that
        contains its own answer, and prefixing would override a child that
        deliberately left the alternate screen inside it.
        """
        replay = self.scrollback.tail(self.attach_replay_bytes)
        suffix = b""
        # Boundary guard, not a diagnosis: the 2026-08-06 black OMP panes were an
        # xterm bundling defect crashing on DECRQM (see
        # frontend/scripts/patch-xterm-requestmode.mjs), not an unmatched paint
        # bracket. The guard stays because the hazard it names is real for any
        # harness that brackets paints: the replay snapshot may land between a
        # DEC 2026 begin/end or an autowrap off/on pair, and a window ending on
        # the open half leaves a fresh client withholding (2026) or mis-wrapping
        # (autowrap) everything after it until the live stream happens to close
        # the bracket - forever, if the child stops painting. Close only modes
        # whose complete opening sequence is newer than their complete closing
        # sequence; this does not splice bytes into a partial CSI at the
        # snapshot boundary.
        if replay.rfind(b"\x1b[?2026h") > replay.rfind(b"\x1b[?2026l"):
            suffix += b"\x1b[?2026l"
        if replay.rfind(b"\x1b[?7l") > replay.rfind(b"\x1b[?7h"):
            suffix += b"\x1b[?7h"
        if len(replay) == self.scrollback.size:
            return replay + suffix
        preamble = b""
        # Agent CLIs enable bracketed paste once at startup and never restate it, so
        # a bounded window over a long session never carries it. A reconnecting pane
        # resets its terminal, and without this it pastes unwrapped: xterm turns each
        # newline into Enter, and the CLI submits the paste a line at a time.
        if self.bracketed_paste.enabled and not BRACKETED_PASTE_TOGGLE.search(replay):
            preamble += b"\x1b[?2004h"
        if self.screen.mode == "alternate" and not SCREEN_TOGGLE.search(replay):
            preamble += b"\x1b[?1049h"
        return preamble + replay + suffix

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

    # --- terminal input ownership and shared geometry (see terminal_arbitration) ---

    def owner_state(self) -> OwnerState:
        return OwnerState(
            connection_id=self.input_owner,
            epoch=self.input_owner_epoch,
            device=self.input_owner_device,
            last_gesture_at=self.input_owner_gesture_ts,
        )

    def apply_owner_state(self, state: OwnerState) -> None:
        self.input_owner = state.connection_id
        self.input_owner_epoch = state.epoch
        self.input_owner_device = state.device
        self.input_owner_gesture_ts = state.last_gesture_at

    def note_owner_input(self, now: float) -> None:
        """Human keystrokes renew the owner's protection from passive claims."""
        self.input_owner_gesture_ts = now

    def release_input_owner(self, connection_id: str) -> bool:
        """Drop ownership held by a detaching connection. True when it held it."""
        if self.input_owner != connection_id:
            return False
        self.apply_owner_state(release_owner(self.owner_state(), connection_id))
        self.input_owner_socket = None
        return True

    def set_viewport(self, connection_id: str, cols: int, rows: int, *, hidden: bool) -> None:
        """Record (or, for a hidden client, forget) one client's fitted size."""
        if hidden:
            self.viewports.pop(connection_id, None)
            return
        self.viewports[connection_id] = (max(2, cols), max(1, rows))

    def drop_viewport(self, connection_id: str) -> None:
        self.viewports.pop(connection_id, None)

    def apply_geometry(self) -> bool:
        """Resize the PTY to the arbitrated size. True when it actually changed.

        Called after every viewport update, ownership transfer and detach. Resizing is
        gated on a real change because each one is a SIGWINCH that makes an agent TUI
        repaint its whole screen.
        """
        size = effective_geometry(self.viewports, self.input_owner)
        if size is None or size == self.geometry:
            return False
        self.geometry = size
        self.pty.resize(size[0], size[1])
        self.publish_control(self.geometry_frame())
        return True

    async def repaint_current_geometry(self) -> bool:
        """Make a full-screen child repaint without changing canonical geometry.

        A repaint-heavy TUI (OMP's spinner alone emits kilobytes per second) wraps the
        retained ring until a bounded replay holds only live-region repaint traffic,
        which parses to a single screen with no scrollback. The child holds the full
        transcript and restates it in response to a real width change (measured
        2026-08-07: one pulse elicited a ~460-line transcript re-render), so pulse one
        column and restore it after the child has had a scheduler turn. Triggered by a
        client `repaint` frame when its parsed replay produced no scrollback; the
        restated bytes also repopulate the ring for every later attach.

        Another client may resize during the yield. In that case its new canonical
        geometry has already won, so never restore the stale size over it.
        """
        size = self.geometry
        if size is None or self.record.state in {"exited", "crashed"}:
            return False
        cols, rows = size
        pulse_cols = cols - 1 if cols > 2 else cols + 1
        self.pty.resize(pulse_cols, rows)
        await asyncio.sleep(0.02)
        if self.geometry != size:
            return False
        self.pty.resize(cols, rows)
        return True

    def geometry_frame(self) -> dict[str, Any]:
        # getattr-guarded for the lightweight PTY stubs used by protocol tests, which
        # implement resize/write but carry no dimensions of their own.
        cols, rows = self.geometry or (
            int(getattr(self.pty, "cols", 80)),
            int(getattr(self.pty, "rows", 24)),
        )
        return {
            "type": "geometry",
            "cols": cols,
            "rows": rows,
            "owner_device": self.input_owner_device,
        }

    def publish_control(self, frame: dict[str, Any]) -> None:
        """Fan out a small control frame. Unlike output it is never worth a resync:
        a client mid-resync gets the current geometry with its resync `update`."""
        for subscriber in tuple(self.subscribers):
            if subscriber.resync_pending:
                continue
            try:
                subscriber.queue.put_nowait(frame)
            except asyncio.QueueFull:
                self._schedule_resync(subscriber)

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
        idle_reason: str | None = None,
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
            idle_reason=idle_reason,
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
        """Capture a deterministic recovery boundary without yielding the event loop.

        Bounded by the same budget as a fresh attach, and for the same reason: the
        client resets its terminal on a resync, so what it receives is a complete
        replay into an empty buffer rather than a patch. A resync is also triggered
        by a client that could not keep up, which is the worst moment to hand one
        the largest possible payload.
        """
        dropped_bytes = subscriber.dropped_bytes
        dropped_chunks = subscriber.dropped_chunks
        replay = self.replay_bytes()
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
        attach_replay_bytes: int | None = None,
        status_timeline: StatusTimelineStore | None = None,
    ) -> None:
        self.adapters, self.reaper, self.history, self.events = adapters, reaper, history, events
        # Durable sink for the per-session transition ledgers; None in tests
        # that exercise the manager without persistence.
        self.status_timeline = status_timeline
        self.max_scrollback = max_scrollback
        # Retention budget above, replay budget here: every Session this manager
        # builds is bound by it, whether spawned or adopted from the supervisor.
        self.attach_replay_bytes = attach_replay_bytes
        self.ingress_url = ingress_url.rstrip("/")
        self.child_env = child_env or {}
        self.hook_spool_dir = hook_spool_dir
        if hook_spool_dir is not None:
            hook_spool_dir.mkdir(parents=True, exist_ok=True)
        # When set, PTYs are spawned in the out-of-process supervisor so live
        # sessions survive a daemon restart; in-process spawning remains the
        # fallback whenever the supervisor is unreachable.
        self.supervisor = supervisor
        # Supervised sessions this daemon could not rebuild at adoption. They keep
        # running with no UI handle, so the count is surfaced at /health.
        self.unadopted_supervisor_sessions = 0
        self.sessions: dict[str, Session] = {}
        # Repairs discovered while adopting supervisor metadata are consumed by
        # the composition root to invalidate rebuildable provider telemetry.
        self.identity_repairs: list[tuple[str, str | None]] = []
        # Collision groups the live identity sweep has already reported, so a
        # persistent (unhealable) conflict logs once rather than every pass.
        self._known_identity_collisions: set[tuple[str, str]] = set()
        # The `cli-state` corroboration layer (status-detection.md § ladder):
        # Claude's own per-process side state, polled on the watchdog cadence.
        # Corroboration only in this phase — it never drives SessionState.
        self.cli_state_monitor = CliStateMonitor(claude_data_home() / "sessions")

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
        adopt_run_id: str | None = None,
        auto_named: bool | None = None,
        shell_profile_id: str | None = None,
        profile_env: dict[str, str] | None = None,
        extra_env: dict[str, str] | None = None,
        project_label: str | None = None,
        project: ProjectIdentity | None = None,
        startup_started_at: float | None = None,
        startup_timing_ms: dict[str, float] | None = None,
        completion_mode: Literal["interactive", "one_shot"] = "interactive",
        worktree_project_root: Path | None = None,
        initial_output: bytes | None = None,
    ) -> Session:
        startup_started_at = startup_started_at or time.perf_counter()
        startup_timing_ms = dict(startup_timing_ms or {})
        if backend not in self.adapters:
            raise ValueError(f"unknown backend: {backend}")
        backend = require_backend(backend)
        if completion_mode not in {"interactive", "one_shot"}:
            raise ValueError(f"unknown completion mode: {completion_mode}")
        if completion_mode == "one_shot" and backend != "shell":
            raise ValueError("one-shot completion is available only for shell sessions")
        # Inheriting a run means claiming an existing conversation's history row,
        # so it is only ever valid for the pane that is resuming that exact
        # conversation. Anything else would point two conversations at one row.
        if adopt_run_id and (backend not in AGENT_BACKENDS or not resume_native_id):
            raise ValueError("adopting an agent run requires resuming its conversation")
        sid = str(uuid.uuid4())
        native_id = resume_native_id or sid
        hook_secret = secrets.token_urlsafe(24)
        mcp_token = secrets.token_urlsafe(32)
        resolved_cwd = Path(cwd or Path.cwd()).resolve()
        if not resolved_cwd.is_dir():
            raise ValueError(f"cwd does not exist: {resolved_cwd}")
        adapter = self.adapters[backend]
        opts = SpawnOptions(
            resolved_cwd,
            exe,
            args or [],
            sid,
            mcp_token,
            worktree_project_root,
        )
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
            # A resume carries the conversation's own name forward, so the pane is
            # not user-named just because a name was supplied: the caller passes
            # the flag the conversation already had, and a conversation nobody
            # renamed stays auto-titleable.
            auto_named=(name is None) if auto_named is None else auto_named,
            state="running" if backend == "shell" else "starting",
            startup_timing_ms=startup_timing_ms,
            completion_mode=completion_mode,
        )
        record.spawn_backend = backend
        record.spawn_native_session_id = native_id
        record.spawn_agent_run_id = adopt_run_id
        record.spawn_env = dict(extra_env or {})
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
        if backend in AGENT_BACKENDS:
            record.agent_run_id = adopt_run_id or sid
            # The run's start stays this PTY's own: it is the floor for spooled
            # hook replay and transcript candidacy, and backdating it to the
            # resumed conversation's start would re-admit a retired pane's events.
            record.agent_run_started_at = record.created_at
            record.agent_loaded_at = record.created_at
            record.run_cwd = str(resolved_cwd)
            record.run_project_scope_id = project.id
            record.run_repo_group_id = project.repo_group_id
        env_extra = {
            # Terminal capability, lowest precedence: colour never depends on how
            # the daemon was launched, and agent harnesses also get colour forced
            # past their TTY-hiding launch chain (see spawn_contract). Central here
            # so no harness can be forgotten. Everything below overrides it.
            **session_terminal_env(backend),
            **self.child_env,
            **{
                key: value
                for candidate in self.adapters.values()
                for key, value in candidate.session_env(sid).items()
            },
            **spawn_spec.env,
            **(profile_env or {}),
            # A task step's own env is the most specific instruction available, so
            # it wins over the shell profile's; mux identity below still wins over
            # both so a task shell can never spoof another session's hooks.
            **(extra_env or {}),
            "MUX_SESSION_ID": sid,
            "MUX_HOOK_URL": f"{self.ingress_url}/api/hooks/{sid}",
            "MUX_PROMOTE_URL": f"{self.ingress_url}/api/sessions/{sid}/promote",
            "MUX_DEMOTE_URL": f"{self.ingress_url}/api/sessions/{sid}/demote",
            "MUX_HOOK_SECRET": hook_secret,
            "MUX_MCP_URL": f"{self.ingress_url}/mcp",
            "MUX_MCP_TOKEN": mcp_token,
            **(
                {"MUX_HOOK_SPOOL": str(self.hook_spool_dir / f"{sid}.jsonl")}
                if self.hook_spool_dir is not None
                else {}
            ),
        }
        pty_started_at = time.perf_counter()
        # Scrubbed base environment: a daemon (re)launched from inside an agent
        # session must not pass parent-Claude markers to terminals, and an agent
        # pane additionally drops any inherited NO_COLOR that would fight the colour
        # it forces (see spawn_contract.base_session_env).
        env_base = base_session_env(os.environ, backend)
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
                initial_output=initial_output or b"",
                # Adoptable from its first instant. The meta mirror is coalesced
                # (~0.5s), so a daemon crash inside that window used to leave the
                # supervisor holding a live session with meta {} — permanently
                # unadoptable, invisible in the UI, and stoppable only by reaping
                # every session.
                meta={
                    "record": record.snapshot(),
                    "hook_secret": hook_secret,
                    "mcp_token": mcp_token,
                },
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
        record.root_started_at = await asyncio.to_thread(process_started_at, record.pid)
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
            mcp_token=mcp_token,
            attach_replay_bytes=self.attach_replay_bytes,
        )
        if initial_output:
            session.scrollback.append(initial_output)
        self.sessions[sid] = session
        self._attach_ledger_sink(session)
        if isinstance(pty, RemotePtyHost):
            self._attach_meta_sink(session)
        transcript = adapter.transcript_path(native_id, resolved_cwd)
        if transcript is not None:
            # A pane must never start out tailing a file a live pane already
            # follows: two sessions on one transcript is the cross-attribution the
            # identity invariant forbids. Codex Branch is the real case — it
            # deliberately resumes a *live* conversation to make the CLI fork a
            # child thread, and now that a Codex conversation can be located by id,
            # asking for the resumed id here answers with the original's rollout.
            # Discovery picks up the child rollout once the fork writes it.
            _, claimed_paths = self._live_transcript_claims(session)
            if self._path_key(transcript) in claimed_paths:
                transcript = None
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
        if has_observable_transcript(backend):
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
            if session.record.spawn_agent_run_id:
                await self.history.resume_agent_run(session.record, transcript)
            else:
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

    async def job_process_ids(self) -> dict[str, list[int]]:
        """Per-session Win32 job membership, merged across both PTY ownerships.

        Process attribution walks *down* from the session root, which cannot
        reach a descendant whose intermediate parent has exited -- Windows never
        clears the dead ppid and never re-parents. That is the normal shape for
        anything Codex starts, because its shell tool runs one-shot and must
        detach whatever it launches. Job membership is the ownership record that
        survives it.

        Supervisor-owned sessions hold their job handle in the supervisor and
        are fetched over RPC; a daemon-owned PTY (supervisor disabled or
        unreachable) holds its own and is read directly. Either source missing
        yields no entry, and the caller keeps the parent walk as its only
        evidence.
        """
        result: dict[str, list[int]] = {}
        client = self.supervisor
        if client is not None and client.connected:
            result.update(await client.job_pids())
        local: dict[str, list[int]] = {}
        for sid, session in self.sessions.items():
            job = session.ownership_job
            if job is None or sid in result:
                continue
            try:
                local[sid] = await asyncio.to_thread(job.process_ids)
            except OSError:
                continue
        result.update(local)
        return result

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

    def _attach_ledger_sink(self, session: Session) -> None:
        """Wire the transition ledger into the durable status timeline.

        Same guarded-callback discipline as the meta sink: the ring nudges the
        store's dirty set synchronously and the write happens later, batched,
        on the store's own SQLite worker — never on a transition or the hook
        ingress. getattr-guarded because tests build partial managers via
        __new__ (the identity_repairs precedent).
        """
        store = getattr(self, "status_timeline", None)
        if store is not None:
            store.attach(session)

    @staticmethod
    def _session_meta(session: Session) -> dict[str, Any]:
        # A provisional path is deliberately not mirrored. The successor daemon
        # reads this metadata as an *established* binding and starts its observer
        # on it directly, which would silently promote a guess to a fact across a
        # restart — and across exactly the restart that erased the reasoning
        # behind it. Omitting it costs one re-derivation: the adopted session
        # re-enters `_await_owned_transcript` and either exact-matches (the hook
        # bound it in the meantime) or guesses again under the same rules.
        transcript = None if session.transcript_provisional else session.transcript_path
        observation_state = getattr(session, "observation_state", {})
        hook_sequences = observation_state.get("hook_sequences")
        ordered_hook_state = (
            {
                str(source)[:64]: sequence
                for source, sequence in hook_sequences.items()
                if isinstance(source, str)
                and isinstance(sequence, int)
                and not isinstance(sequence, bool)
                and sequence > 0
            }
            if isinstance(hook_sequences, dict)
            else {}
        )
        hook_sequence_duplicates = observation_state.get("hook_sequence_duplicates", 0)
        return {
            "record": session.record.snapshot(),
            "hook_secret": session.hook_secret,
            "mcp_token": session.mcp_token,
            "transcript_path": str(transcript) if transcript else None,
            "agent_lifecycle_id": session.agent_lifecycle_id,
            "approval_candidate": getattr(session, "approval_candidate", None),
            "hook_sequences": ordered_hook_state,
            "hook_sequence_duplicates": (
                hook_sequence_duplicates
                if isinstance(hook_sequence_duplicates, int)
                and not isinstance(hook_sequence_duplicates, bool)
                and hook_sequence_duplicates >= 0
                else 0
            ),
        }

    @staticmethod
    def _infer_spawn_backend(record: SessionRecord) -> Backend:
        """Recover the immutable root provider from legacy supervisor metadata."""
        if record.spawn_backend == "shell" or record.spawn_backend in AGENT_BACKENDS:
            return record.spawn_backend
        inferred = infer_agent_executable_backend(record.exe, record.args)
        if inferred is not None:
            return inferred
        # Older direct-agent records already used the mux id as their durable
        # history owner. A promoted shell receives a separate run id — as does a
        # root agent that has rolled its conversation, which is what agent_run_seq
        # distinguishes from the promoted case, and one that inherited the run of
        # the conversation it resumed, which spawn_agent_run_id distinguishes.
        if record.backend in AGENT_BACKENDS and (
            record.agent_run_id == record.id
            or record.agent_run_seq > 0
            or (
                record.spawn_agent_run_id is not None
                and record.agent_run_id == record.spawn_agent_run_id
            )
        ):
            return record.backend
        return "shell"

    @classmethod
    def _ensure_spawn_identity(cls, record: SessionRecord) -> None:
        record.spawn_backend = cls._infer_spawn_backend(record)
        if record.agent_loaded_at is None:
            if record.spawn_backend in AGENT_BACKENDS:
                record.agent_loaded_at = record.created_at
            elif record.backend in AGENT_BACKENDS:
                # Compatibility for supervisor records written before this field
                # existed. The first observed run is the closest durable floor.
                record.agent_loaded_at = record.agent_run_started_at or record.created_at
        if record.spawn_native_session_id:
            return
        native_id: str | None = None
        args = record.args
        if record.spawn_backend == "claude":
            for flag in ("--session-id", "--resume"):
                if flag in args:
                    index = args.index(flag) + 1
                    if index < len(args):
                        native_id = str(args[index])
                        break
        elif record.spawn_backend == "codex" and "resume" in args:
            index = args.index("resume") + 1
            if index < len(args):
                native_id = str(args[index])
        record.spawn_native_session_id = native_id or record.id

    @staticmethod
    def _path_key(path: Path | str) -> str:
        try:
            return str(Path(path).resolve()).casefold()
        except OSError:
            return str(path).casefold()

    def _live_transcript_claims(
        self, exclude: Session | None = None
    ) -> tuple[set[tuple[str, str]], set[str]]:
        native_ids: set[tuple[str, str]] = set()
        paths: set[str] = set()
        for other in getattr(self, "sessions", {}).values():
            if other is exclude or other.record.state in {"exited", "crashed"}:
                continue
            if other.record.backend in AGENT_BACKENDS:
                native_ids.add((other.record.backend, other.record.native_session_id))
                transcript_path = getattr(other, "transcript_path", None)
                if transcript_path:
                    paths.add(self._path_key(transcript_path))
        return native_ids, paths

    def _unclaimed_transcripts(
        self,
        session: Session,
        candidates: list[tuple[float, Path, str]],
    ) -> list[tuple[float, Path, str]]:
        claimed_ids, claimed_paths = self._live_transcript_claims(session)
        backend = session.adapter.name
        distinct: dict[tuple[str, str], tuple[float, Path, str]] = {}
        for item in candidates:
            modified, path, native_id = item
            if (backend, native_id) in claimed_ids:
                continue
            if self._path_key(path) in claimed_paths:
                continue
            if (backend, native_id) in session.ignored_detection_runs:
                continue
            key = (native_id, self._path_key(path))
            if key not in distinct or modified > distinct[key][0]:
                distinct[key] = item
        return list(distinct.values())

    def _named_conversation_transcript(self, session: Session, cwd: Path) -> Path | None:
        """This pane's own conversation file, when the adapter can name it outright.

        Only for an id mux did not invent: a backend that mints its own conversation
        id carries the mux session id as a placeholder until discovery, and asking
        where *that* conversation lives is asking about a conversation nobody has
        seen. A resumed pane is the case this exists for. Its id came from the row it
        resumed, its file already exists, and its mtime predates the pane — so the
        recency scan below can never see it, and on Windows an open rollout's mtime
        can stay frozen at creation for hours, which makes "wait for the mtime to
        catch up" no fallback at all. Before this, a resumed Codex pane stayed
        unobserved for its whole life unless it happened to write a turn *and* the
        mtime happened to refresh: no transcript tab, no tokens, no context, and a
        history entry that could never be resumed again.

        Identity is still proved, never guessed: the adapter answers from the file's
        own first record, and the same claim rules as `_unclaimed_transcripts` apply,
        so this can only ever bind a file no live pane owns.
        """
        record = session.record
        if not record.native_session_id or record.native_session_id == record.id:
            return None
        try:
            path = session.adapter.transcript_path(record.native_session_id, cwd)
            if path is None or not path.is_file():
                return None
        except OSError:
            return None
        backend = session.adapter.name
        claimed_ids, claimed_paths = self._live_transcript_claims(session)
        if (backend, record.native_session_id) in claimed_ids:
            return None
        if self._path_key(path) in claimed_paths:
            return None
        if (backend, record.native_session_id) in session.ignored_detection_runs:
            return None
        return path

    def _relocated_conversation_transcript(
        self,
        adapter: Any,
        backend: str,
        native_id: str,
        claimed_ids: set[tuple[str, str]],
        claimed_paths: set[str],
    ) -> Path | None:
        """This conversation's file when the cwd it was computed from is no longer true.

        `_named_conversation_transcript` asks the adapter where the conversation
        *would* live given a cwd, which is the right question right up until the CLI
        moves: Claude relocates the file into the slug for its new working directory
        when a session enters a native worktree, and every path the daemon derived
        from the spawn cwd stops existing. Across a daemon restart there is then
        nothing left to adopt — the mirrored path is dead, the recency scan reads the
        wrong directory, and the session comes back permanently unobserved.

        Searching by conversation id is safe here in a way an mtime scan is not: mux
        dictates the id at spawn, so a file named after it is this session's by
        construction. The ordinary claim rules still apply on top, so a file a live
        sibling already follows is never taken.
        """
        if not native_id:
            return None
        if (backend, native_id) in claimed_ids:
            return None
        locate = getattr(adapter, "locate_transcript", None)
        if locate is None:
            return None
        try:
            path = cast("Path | None", locate(native_id))
        except OSError:
            return None
        if path is None or self._path_key(path) in claimed_paths:
            return None
        return path

    async def _await_owned_transcript(
        self, session: Session, stop_event: asyncio.Event
    ) -> tuple[Path, bool] | None:
        """Wait for a transcript this live PTY may follow, and say how strongly.

        Returns ``(path, provisional)``. ``provisional=False`` is an exact
        conversation-id match and binds everything. ``provisional=True`` is an
        adoption by elimination and may only drive state
        (`_may_adopt_sole_candidate`).
        """
        located_at = 0.0
        while not stop_event.is_set():
            cwd = Path(session.record.run_cwd or session.record.cwd)
            started = session.record.agent_run_started_at or session.record.created_at
            direct = self._named_conversation_transcript(session, cwd)
            if direct is not None:
                return direct, False
            try:
                candidates = self._unclaimed_transcripts(
                    session, session.adapter.recent_transcripts(cwd, started)
                )
            except OSError:
                candidates = []
            exact = [
                item for item in candidates if item[2] == session.record.native_session_id
            ]
            if exact:
                return max(exact)[1], False
            # Every candidate above was read out of the directory the *spawn* cwd
            # names. A conversation whose CLI has since moved is not in there at all,
            # so before falling back to elimination, ask where the file actually is.
            # Throttled and off-thread: this searches rather than computes, and the
            # common reason to be in this loop is a CLI that has simply not written
            # its first record yet.
            now = time.monotonic()
            if (
                getattr(session.adapter, "resolves_transcript_by_cwd", False)
                and session.record.native_session_id
                and now - located_at >= TRANSCRIPT_LOCATE_INTERVAL_SECONDS
            ):
                located_at = now
                claimed_ids, claimed_paths = self._live_transcript_claims(session)
                located = await asyncio.to_thread(
                    self._relocated_conversation_transcript,
                    session.adapter,
                    session.adapter.name,
                    session.record.native_session_id,
                    claimed_ids,
                    claimed_paths,
                )
                if located is not None:
                    return located, False
            if len(candidates) == 1 and self._may_adopt_sole_candidate(
                session, candidates[0][1], started
            ):
                return candidates[0][1], True
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=0.5)
            except TimeoutError:
                pass
        return None

    @staticmethod
    def _may_adopt_sole_candidate(session: Session, candidate: Path, started: float) -> bool:
        """Whether the sole unclaimed candidate may be followed *provisionally*.

        Never for identity. The candidate pool is the backend's shared per-cwd
        transcript directory, so "the only unclaimed file" can easily be an
        unmanaged CLI's conversation (a headless `claude -p` from a script, a
        plain-terminal `codex`), and no filesystem gate separates them:

        - For a backend that mints its own conversation id, "created after this run
          began" plus "our PTY was producing output when it appeared" both pass for an
          outsider, because an agent TUI repaints continuously — verified live: an
          unbound Codex pane adopted the rollout of a `codex` run started outside mux
          in the same cwd, rekeying itself to the stranger's thread.
        - Nothing in Codex's `session_meta` separates them either. `originator`
          distinguishes only the headless `codex exec` case (`codex_exec`/`exec`); an
          interactive outsider reports `codex-tui`/`cli`, exactly like ours.

        So identity still comes only from a hook, which an outsider cannot forge:
        it arrives over this session's own loopback ingress authenticated with this
        session's own secret, and Codex names its `thread-id` on
        `agent-turn-complete` (`_bind_native_id_from_hook`).

        What changed is the *consequence* of following a file. Adoption used to
        mean rekeying `native_session_id`, writing the history row, and publishing
        the file's tokens and context — all of which mis-render a stranger's work
        under this pane's identity, which is why this returned False outright. A
        provisional follow does none of that (`provisional_observation_blocks`);
        it may move turn state and nothing else. The worst case for a wrong guess
        is therefore a pane that reads "working" while an unrelated codex runs:
        cosmetic, self-correcting on the next real hook, and strictly more
        conservative for delivery than the alternative.

        The alternative is not "stay safe", it is "say nothing". Codex cannot name
        its thread until its first turn *ends*, so refusing here left every fresh
        pane reporting "ready · turn complete" for the whole first turn while its
        rollout sat on disk unread — measured live at 200 s, with the rollout's own
        `task_started` written 4 s after spawn.

        Gates, all required:

        - The backend must mint its own conversation id. Claude never needs this
          path (its transcript is *derived* from the id mux injected as
          `--session-id`, so the exact match always exists), and taking it there
          would be a pure downgrade.
        - The session must still be unbound, so this can never displace or race a
          conversation the daemon already established.
        - The file must have been *created* around this agent run's start, not
          merely written to recently. This is the one gate the original analysis
          did not apply: `recent_transcripts` filters on mtime, which any live
          outsider passes continuously, while creation time is a fact about the
          file's own origin that a long-running outsider cannot satisfy.
        """
        adapter = getattr(session, "adapter", None)
        if getattr(adapter, "assigns_conversation_id", True):
            return False
        from .observation import conversation_unbound

        if not conversation_unbound(session):
            return False
        created = file_created_at(candidate)
        if created is None:
            return False
        return abs(created - started) <= _TRANSCRIPT_CREATION_SLACK_SECONDS

    def _adoption_transcript_claims(
        self,
        records: dict[str, SessionRecord],
        metas: dict[str, dict[str, Any]],
        exclude_sid: str,
    ) -> tuple[set[tuple[str, str]], set[str]]:
        """Return claims backed by each other session's root-process identity."""
        native_ids: set[tuple[str, str]] = set()
        paths: set[str] = set()
        for other_sid, other in records.items():
            if other_sid == exclude_sid or other.state in {"exited", "crashed"}:
                continue
            root_backend = other.spawn_backend or "shell"
            backend = root_backend if root_backend in AGENT_BACKENDS else other.backend
            if backend not in AGENT_BACKENDS:
                continue
            # A direct root agent has priority over mutable metadata. For a
            # promoted shell, the active backend/native pair remains authoritative.
            claim_ids: set[str] = set()
            if root_backend in AGENT_BACKENDS:
                if other.agent_run_seq > 0 and other.backend == backend:
                    # A rolled conversation is no longer the one it spawned with,
                    # so a borrowed (--resume) spawn id claims nothing and the
                    # live id claims the file.
                    claim_ids.add(other.native_session_id)
                    if backend == "claude":
                        # But the conversation named by this pane's own mux id was
                        # minted for this pane (`--session-id`) and can never be
                        # another live pane's: a sibling holding it after this
                        # pane rolled can only have been cross-attributed onto
                        # it. Without this standing claim, the rightful owner's
                        # own corruption hid the conflict from every sibling.
                        claim_ids.add(other.id)
                elif backend == "claude":
                    claim_ids.add(other.spawn_native_session_id or other.id)
                elif other.backend == backend:
                    claim_ids.add(other.native_session_id)
            else:
                claim_ids.add(other.native_session_id)
            for native_id in claim_ids:
                if native_id:
                    native_ids.add((backend, native_id))
            raw_path = metas.get(other_sid, {}).get("transcript_path")
            if other.backend == backend and isinstance(raw_path, str) and raw_path:
                paths.add(self._path_key(raw_path))
        return native_ids, paths

    def _adoption_transcript(
        self,
        record: SessionRecord,
        meta: dict[str, Any],
        records: dict[str, SessionRecord],
        metas: dict[str, dict[str, Any]],
    ) -> Path | None:
        backend = record.spawn_backend or "shell"
        if backend not in AGENT_BACKENDS:
            return None
        adapter = self.adapters[backend]
        claimed_ids, claimed_paths = self._adoption_transcript_claims(
            records, metas, record.id
        )
        current_raw = meta.get("transcript_path")
        current = Path(current_raw) if isinstance(current_raw, str) and current_raw else None
        if current is not None and record.backend == backend and current.is_file():
            # `is_file` because a mirrored path is only a memory of where the file
            # was. A Claude session that entered a worktree while the daemon was down
            # left this path behind entirely, and re-adopting it hands the observer a
            # name for something that no longer exists — which then fails silently,
            # since a missing file is exactly what the staleness guard cannot read.
            current_native = adapter.transcript_native_id(current)
            if (
                current_native
                and (backend, current_native) not in claimed_ids
                and self._path_key(current) not in claimed_paths
            ):
                return current
        cwd = Path(record.run_cwd or record.spawn_cwd or record.cwd)
        # After a conversation rollover the spawn id names a retired conversation;
        # the live native id is what this run is expected to be writing.
        expected = (
            record.native_session_id
            if record.agent_run_seq > 0
            else record.spawn_native_session_id
        )
        # A conversation the adapter can name outright needs no correlation. This is
        # what re-binds a resumed pane across a daemon restart that lost its mirrored
        # path: its transcript is older than the pane, so the recency scan below is
        # blind to it (`_named_conversation_transcript`).
        if expected and expected != record.id:
            try:
                named = adapter.transcript_path(expected, cwd)
            except OSError:
                named = None
            if (
                named is not None
                and named.is_file()
                and (backend, expected) not in claimed_ids
                and self._path_key(named) not in claimed_paths
            ):
                return named
        try:
            candidates = adapter.recent_transcripts(cwd, record.created_at)
        except OSError:
            return None
        unclaimed = [
            (modified, path, native_id)
            for modified, path, native_id in candidates
            if (backend, native_id) not in claimed_ids
            and self._path_key(path) not in claimed_paths
        ]
        exact = [item for item in unclaimed if item[2] == expected]
        if exact:
            return max(exact)[1]
        # Everything above reads the directory the recorded cwd names. A session
        # whose CLI moved (a Claude native worktree) writes somewhere else entirely,
        # so ask for the conversation by id before settling for elimination.
        located = self._relocated_conversation_transcript(
            adapter, backend, expected or record.id, claimed_ids, claimed_paths
        )
        if located is not None:
            return located
        # Ambiguity is not identity evidence. Waiting for a lifecycle hook or a
        # unique transcript is safer than attaching a sibling's conversation.
        distinct = {self._path_key(item[1]): item for item in unclaimed}
        return next(iter(distinct.values()))[1] if len(distinct) == 1 else None

    @staticmethod
    def _reset_provider_observation(record: SessionRecord) -> None:
        record.state = "starting"
        record.state_detail = None
        record.awaiting_reason = None
        record.tokens_in = 0
        record.tokens_out = 0
        record.tokens_cache_read = 0
        record.tokens_cache_write = 0
        record.cost_usd = 0.0
        record.context_window = 0
        record.context_pct = 0
        record.context_peak_pct = 0
        record.compaction_count = 0
        record.last_compaction_at = None
        record.compaction_capability = None
        record.compaction_confidence = None
        record.provider = None
        record.provider_account_hashes = {}
        record.model = None
        record.measurement_source = None
        record.parser_status = "waiting"
        record.parser_diagnostic = None
        record.parser_events_seen = 0
        record.parser_unknown_events = 0
        record.parser_unknown_signatures = {}
        record.parser_schema_version = None
        record.observation_stale_since = None
        record.observation_diagnostic = None
        # Run-scoped like the annotations: a turn duration measured in a replaced
        # conversation is not this one's, and keeping it would date the new run's
        # first idle with the old run's last turn.
        record.last_turn_ms = None
        record.turn_started_at = None
        # Annotations describe the observation identity being reset here. This is
        # a silent record-level clear for the adoption-repair paths (no Session,
        # no ledger yet); live callers ledger via clear_all_standing_activity
        # *before* reaching this.
        record.standing_activity.clear()

    def _reconcile_adopted_root_identity(
        self,
        record: SessionRecord,
        meta: dict[str, Any],
        records: dict[str, SessionRecord],
        metas: dict[str, dict[str, Any]],
    ) -> tuple[Path | None, str | None, dict[str, str] | None]:
        """Reassert a direct agent's provider ownership after daemon reload."""
        backend = record.spawn_backend or "shell"
        raw_path = meta.get("transcript_path")
        current_path = Path(raw_path) if isinstance(raw_path, str) and raw_path else None
        if backend == "shell":
            if record.backend not in AGENT_BACKENDS:
                return current_path, None, None
            claimed_ids, claimed_paths = self._adoption_transcript_claims(
                records, metas, record.id
            )
            current_claimed = (record.backend, record.native_session_id) in claimed_ids or (
                current_path is not None and self._path_key(current_path) in claimed_paths
            )
            if not current_claimed:
                return current_path, None, None
            previous = {
                "backend": record.backend,
                "native_session_id": record.native_session_id,
                "transcript_path": str(current_path) if current_path else "",
            }
            bad_run_id = record.agent_run_id
            record.backend = "shell"
            record.native_session_id = record.spawn_native_session_id or record.id
            record.agent_run_id = None
            record.agent_run_started_at = None
            record.agent_loaded_at = None
            record.run_cwd = None
            record.run_project_scope_id = None
            record.run_repo_group_id = None
            record.repository_id = record.spawn_project_scope_id
            record.project_scope_id = record.spawn_project_scope_id
            record.repo_group_id = record.spawn_repo_group_id
            record.project_label = record.spawn_project_label
            record.project_root = record.spawn_project_root
            if record.auto_named:
                record.name = f"shell-{record.id[:6]}"
            self._reset_provider_observation(record)
            record.state = "running"
            record.parser_status = "not_applicable"
            return None, bad_run_id, previous
        if backend not in AGENT_BACKENDS:
            return current_path, None, None
        transcript = self._adoption_transcript(record, meta, records, metas)
        adapter = self.adapters[backend]
        transcript_native = adapter.transcript_native_id(transcript) if transcript else None
        claimed_ids, claimed_paths = self._adoption_transcript_claims(
            records, metas, record.id
        )
        current_claimed = (record.backend, record.native_session_id) in claimed_ids or (
            current_path is not None and self._path_key(current_path) in claimed_paths
        )
        # A root agent's run id is normally its session id, and anything else is
        # corruption to repair. A conversation rollover is the one legitimate
        # exception: `agent_run_seq > 0` records that the daemon itself minted a
        # successor run for this PTY, so the id is expected rather than bad. Without
        # this the first daemon restart after a `/clear` would "repair" the live run
        # away and quarantine its history row as misattributed.
        rolled = record.agent_run_seq > 0 and bool(record.agent_run_id)
        # A resumed pane is the second legitimate exception, and the mirror of the
        # first: it inherited the run of the conversation it resumed, so a run id
        # that is not the session id is expected from its first moment. The
        # evidence is immutable spawn metadata rather than a counter, and it
        # lapses on its own — a later rollover mints a run of this pane's own,
        # which stops matching.
        adopted = (
            record.agent_run_seq == 0
            and record.spawn_agent_run_id is not None
            and record.agent_run_id == record.spawn_agent_run_id
        )
        # The exception does not extend to a rolled conversation that a sibling's
        # root identity claims: two panes cannot write one transcript, and the
        # sibling's claim is immutable spawn evidence while this record's rolled id
        # is mutable observer output. Blanket-trusting it is how a cross-attributed
        # identity survived every daemon restart. Treat the roll as corrupt and let
        # the repair below fall back to this pane's own spawn anchor.
        if (rolled or adopted) and (backend, record.native_session_id) in claimed_ids:
            rolled = False
            adopted = False
        expected_run_id = record.agent_run_id if rolled or adopted else record.id
        changed = (
            record.backend != backend
            or current_claimed
            or record.agent_run_id != expected_run_id
            or (
                current_path is not None
                and (
                    transcript is None
                    or self._path_key(current_path) != self._path_key(transcript)
                )
            )
        )
        if not changed:
            return transcript or current_path, None, None
        previous = {
            "backend": record.backend,
            "native_session_id": record.native_session_id,
            "transcript_path": str(current_path) if current_path else "",
        }
        # A repaired-away run id is normally this pane's own misattributed row, and
        # quarantining it is how its copied messages stop surfacing. An inherited
        # one is not the pane's: it is the resumed conversation's own row, holding
        # that conversation's history, so it is dropped rather than quarantined.
        bad_run_id = (
            record.agent_run_id
            if record.agent_run_id not in {expected_run_id, record.spawn_agent_run_id}
            else None
        )
        record.backend = backend
        fallback_native_id = (
            record.native_session_id
            if rolled
            else (record.spawn_native_session_id or record.id)
        )
        record.native_session_id = transcript_native or fallback_native_id
        record.agent_run_id = expected_run_id
        if not rolled:
            record.agent_run_started_at = record.created_at
            # A repaired root is back on its spawn conversation. Clearing the roll
            # counter also drops the (equally corrupt) persisted lifecycle anchor
            # in the adoption path below, which only honours it for rolled roots.
            record.agent_run_seq = 0
        record.run_cwd = record.spawn_cwd or record.cwd
        record.run_project_scope_id = record.spawn_project_scope_id or record.project_scope_id
        record.run_repo_group_id = record.spawn_repo_group_id or record.repo_group_id
        record.repository_id = record.spawn_project_scope_id or record.repository_id
        record.project_scope_id = record.spawn_project_scope_id or record.project_scope_id
        record.repo_group_id = record.spawn_repo_group_id or record.repo_group_id
        record.project_label = record.spawn_project_label or record.project_label
        record.project_root = record.spawn_project_root or record.project_root
        if record.auto_named:
            record.name = f"{backend}-{record.id[:6]}"
        self._reset_provider_observation(record)
        return transcript, bad_run_id, previous

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
        records: dict[str, SessionRecord] = {}
        metas: dict[str, dict[str, Any]] = {}
        for info in client.initial_sessions:
            sid = str(info.get("sid") or "")
            raw_meta = info.get("meta")
            pre_meta = raw_meta if isinstance(raw_meta, dict) else {}
            snapshot = pre_meta.get("record")
            if not sid or not isinstance(snapshot, dict):
                continue
            try:
                parsed_record = SessionRecord.from_snapshot(snapshot)
            except (TypeError, ValueError):
                continue
            self._ensure_spawn_identity(parsed_record)
            records[sid] = parsed_record
            metas[sid] = pre_meta
        adopted = 0
        self.unadopted_supervisor_sessions = 0
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
                # know it may still come back. Counted, because otherwise a live
                # agent running with no UI handle is visible only as a log line.
                log.warning("supervised session %s has no metadata; not adopting", sid)
                self.unadopted_supervisor_sessions += 1
                continue
            record = records.get(sid)
            if record is None:
                log.warning("could not rebuild session record for %s", sid)
                self.unadopted_supervisor_sessions += 1
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
                self.unadopted_supervisor_sessions += 1
                continue
            transcript_path, bad_run_id, previous_identity = (
                self._reconcile_adopted_root_identity(record, meta, records, metas)
            )
            # The preflight maps are also consulted by sessions adopted later in
            # this pass. Replace stale ownership evidence immediately so a
            # repaired record cannot make its former sibling path look claimed.
            meta["transcript_path"] = str(transcript_path) if transcript_path else None
            adapter = self.adapters.get(record.backend) or self.adapters["shell"]
            hook_secret = str(meta.get("hook_secret") or "") or secrets.token_urlsafe(24)
            # Never regenerate the MCP token: the agent holds the original in its
            # env, so a fresh one would authenticate nobody. Empty stays empty.
            session = Session(
                record,
                host,
                adapter,
                self.max_scrollback,
                hook_secret,
                mcp_token=str(meta.get("mcp_token") or ""),
                attach_replay_bytes=self.attach_replay_bytes,
            )
            raw_hook_sequences = meta.get("hook_sequences")
            if isinstance(raw_hook_sequences, dict):
                session.observation_state["hook_sequences"] = {
                    str(source)[:64]: sequence
                    for source, sequence in raw_hook_sequences.items()
                    if isinstance(source, str)
                    and isinstance(sequence, int)
                    and not isinstance(sequence, bool)
                    and sequence > 0
                }
            raw_hook_sequence_duplicates = meta.get("hook_sequence_duplicates")
            if (
                isinstance(raw_hook_sequence_duplicates, int)
                and not isinstance(raw_hook_sequence_duplicates, bool)
                and raw_hook_sequence_duplicates >= 0
            ):
                session.observation_state["hook_sequence_duplicates"] = (
                    raw_hook_sequence_duplicates
                )
            raw_approval_candidate = meta.get("approval_candidate")
            if isinstance(raw_approval_candidate, dict):
                raw_candidate_started_at = raw_approval_candidate.get("started_at")
                try:
                    candidate_started_at = (
                        float(raw_candidate_started_at)
                        if isinstance(raw_candidate_started_at, (int, float, str))
                        else 0.0
                    )
                except (TypeError, ValueError):
                    candidate_started_at = 0.0
                if candidate_started_at > 0.0 and math.isfinite(candidate_started_at):
                    session.approval_candidate = {
                        "started_at": candidate_started_at,
                        "source": str(raw_approval_candidate.get("source") or "hook"),
                        "evidence": str(
                            raw_approval_candidate.get("evidence") or "approval:restored"
                        ),
                        "detail": str(
                            raw_approval_candidate.get("detail") or "Approval needed"
                        ),
                    }
            if previous_identity is not None:
                # The candidate belonged to the disputed run. Identity repair
                # deliberately refuses to carry run-scoped attention across it.
                session.approval_candidate = None
            session.scrollback.seed(replay, int(response.get("position", len(replay))))
            # The screen switch is written once, at startup, and never repeated.
            # Replaying the retained bytes through the parser is what keeps an
            # adopted session's screen mode known across a daemon restart; without
            # it the fact would be lost for the whole remaining life of the PTY.
            session.screen.feed(replay)
            session.bracketed_paste.feed(replay)
            session.osc_signals.feed(replay)
            session.transcript_path = transcript_path
            lifecycle = meta.get("agent_lifecycle_id")
            # The lifecycle anchor is what Branch forks from and what the exit
            # check demotes against, and the observer never rewrites it. A promoted
            # shell has always carried one; a *root* agent now does too once it has
            # rolled its conversation, and dropping it there would send Branch back
            # to the pre-rollover conversation after every daemon restart.
            session.agent_lifecycle_id = (
                lifecycle
                if isinstance(lifecycle, str)
                and lifecycle
                and record.backend in AGENT_BACKENDS
                and (record.spawn_backend == "shell" or record.agent_run_seq > 0)
                else None
            )
            if record.spawn_backend == "shell" and record.backend in AGENT_BACKENDS:
                session.agent_promoted_at = time.time()
            self.sessions[sid] = session
            self._attach_ledger_sink(session)
            self._attach_meta_sink(session)
            if session.approval_candidate is not None:
                # Local import avoids the observation -> session module cycle.
                from .observation import restore_pending_approval

                await restore_pending_approval(session, self.events)
            # Identity repair mutates record.state directly (it runs before the
            # Session exists). Ledger it now that one does, so the state-log after
            # a restart does not start with a state nothing explains — the
            # scenario where the ledger is most needed.
            if previous_identity is not None:
                apply_state_transition(
                    session,
                    record.state,
                    record.state_detail,
                    source="daemon",
                    evidence="adoption:identity_repair",
                    force=True,
                )
            session.tasks.add(asyncio.create_task(self._fanout(session), name=f"fanout-{sid}"))
            session.tasks.add(asyncio.create_task(self._ticker(session), name=f"ticker-{sid}"))
            if host.isalive():
                if previous_identity is not None:
                    if not hasattr(self, "identity_repairs"):
                        self.identity_repairs = []
                    self.identity_repairs.append((sid, record.agent_run_id))
                    if bad_run_id:
                        await self.history.quarantine_misattributed_agent_run(
                            bad_run_id, "root_identity_reconciled"
                        )
                    if record.backend in AGENT_BACKENDS:
                        await self.history.session_promoted(
                            record,
                            str(session.transcript_path) if session.transcript_path else "",
                        )
                        await self.history.reopen_agent_run(record.agent_run_id or record.id)
                    await self.events.emit(
                        "session_identity_reconciled",
                        session_id=sid,
                        source="daemon",
                        previous=previous_identity,
                        backend=record.backend,
                        native_session_id=record.native_session_id,
                        transcript_path=(
                            str(session.transcript_path) if session.transcript_path else None
                        ),
                    )
                if has_observable_transcript(record.backend):
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
        # Every caller that hands a path here derived it from identity evidence
        # (spawn id, hook-reported rollover, adoption metadata). Only the
        # elimination path inside `_observe` sets this, and it clears here so a
        # re-bind can never inherit a previous run's provisional standing.
        session.transcript_provisional = False
        session.provisional_binding_sink = lambda: self._queue_provisional_resolution(session)
        task = asyncio.create_task(
            self._observe(session, transcript, session.agent_stop_event),
            name=f"observe-{session.record.id}",
        )
        session.observer_task = task
        session.tasks.add(task)

    def _queue_provisional_resolution(self, session: Session) -> None:
        """Schedule provisional-binding resolution off the hook request path."""
        if not session.transcript_provisional:
            return
        task = asyncio.create_task(
            self._resolve_provisional_transcript(session),
            name=f"provisional-bind-{session.record.id}",
        )
        session.tasks.add(task)
        task.add_done_callback(session.tasks.discard)

    async def _resolve_provisional_transcript(self, session: Session) -> None:
        """Confirm or discard a guessed transcript now that the id is proven.

        Runs in its own task, never inside the observer, so the discard branch is
        free to restart that observer without cancelling its own caller.
        """
        if not session.transcript_provisional:
            return
        path = session.transcript_path
        native_id = session.record.native_session_id
        observed = session.adapter.transcript_native_id(path) if path is not None else None
        if path is not None and observed and observed == native_id:
            # The guess was right. Everything the provisional standing withheld —
            # identity-derived history, tokens, context — is now safe, and the
            # observer already tailing this file simply keeps going.
            session.transcript_provisional = False
            await self.history.session_promoted(session.record, str(path))
            await self.events.emit(
                "transcript_binding_confirmed",
                session_id=session.record.id,
                source="hook",
                scope="root",
                backend=session.record.backend,
                transcript_path=str(path),
                native_session_id=native_id,
            )
            session.publish_update()
            return
        # Refuted: the pane was following someone else's conversation. Nothing
        # durable was written under it (that is the point of the standing), so
        # dropping the guess is complete. Re-entering the observer with no path
        # now takes the exact-match route, which exists from this moment on.
        log.info(
            "session %s discarding provisional transcript %s: conversation is %s",
            session.record.id,
            path.name if path is not None else "<none>",
            native_id,
        )
        await self.events.emit(
            "transcript_binding_discarded",
            session_id=session.record.id,
            source="hook",
            scope="root",
            backend=session.record.backend,
            transcript_path=str(path) if path is not None else None,
            native_session_id=native_id,
        )
        self._start_observer(session, None)

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
        # Published on the session so a sibling agent's transcript-switch watcher
        # can see that an unpromoted agent launch is in flight in this cwd and
        # refuse to adopt the transcript that launch is about to create.
        seen_names: set[str] = session.pending_agent_backends
        seen_names.clear()
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
            try:
                candidates = [
                    (modified, adapter.name, path, native_id)
                    for adapter in launched
                    for modified, path, native_id in adapter.recent_transcripts(
                        Path(session.record.runtime_cwd or session.record.cwd),
                        detection_started_at,
                    )
                    if (adapter.name, native_id) not in session.ignored_detection_runs
                ]
            except OSError:
                # The CLI's own startup cleanup deletes old transcripts under the
                # glob. One unlucky stat must not kill the shim-less promotion
                # fallback for the rest of the session's life.
                candidates = []
            claimed_ids, claimed_paths = self._live_transcript_claims(session)
            distinct = {
                (backend, native_id, self._path_key(path)): (
                    modified,
                    backend,
                    path,
                    native_id,
                )
                for modified, backend, path, native_id in candidates
                if (backend, native_id) not in claimed_ids
                and self._path_key(path) not in claimed_paths
            }
            if len(distinct) == 1:
                _, backend, path, native_id = next(iter(distinct.values()))
                backend = require_backend(backend)
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
                reset_session_observation_state(session, "backend_detected")
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
        if backend not in AGENT_BACKENDS:
            raise ValueError(f"cannot promote session to {backend}")
        backend = require_backend(backend)
        session = self.resolve(sid)
        self._ensure_spawn_identity(session.record)
        if session.record.spawn_backend in AGENT_BACKENDS:
            await self.events.emit(
                "backend_promotion_ignored",
                session_id=session.record.id,
                source="daemon",
                reason="root_agent_owns_pty",
                root_backend=session.record.spawn_backend,
                requested_backend=backend,
                requested_native_session_id=native_id,
            )
            return session
        if session.record.backend == backend and session.agent_lifecycle_id == native_id:
            return session
        if session.record.backend == backend and session.record.native_session_id == native_id:
            session.agent_lifecycle_id = native_id
            return session
        if session.record.backend in AGENT_BACKENDS:
            await self.events.emit(
                "backend_promotion_ignored",
                session_id=session.record.id,
                source="daemon",
                reason="agent_run_already_active",
                root_backend=session.record.spawn_backend,
                requested_backend=backend,
                requested_native_session_id=native_id,
            )
            return session
        session.ignored_detection_runs.discard((backend, native_id))
        session.agent_lifecycle_id = native_id
        # Anything spooled before this promotion belongs to a run that is over.
        self.discard_hook_spool(session.record.id)
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
        reset_session_observation_state(session, "promotion")
        clear_all_standing_activity(session, evidence="promotion")
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
        self._ensure_spawn_identity(session.record)
        if session.record.spawn_backend in AGENT_BACKENDS:
            await self.events.emit(
                "backend_demotion_ignored",
                session_id=session.record.id,
                source="daemon",
                reason="root_agent_owns_pty",
                root_backend=session.record.spawn_backend,
                requested_backend=backend,
                requested_native_session_id=native_id,
            )
            return session
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
        await self._stop_observer(session)
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
        reset_session_observation_state(session, "demotion")
        clear_all_standing_activity(session, evidence="demotion")
        session.observation_replay = False
        session.agent_promoted_at = None
        session.transcript_path = None
        session.transcript_provisional = False
        # The spool is keyed by mux session id, which survives demotion. Anything
        # the ending agent run left behind would replay into the next promotion.
        self.discard_hook_spool(session.record.id)
        session.record.agent_run_id = None
        session.record.agent_run_started_at = None
        session.record.agent_loaded_at = None
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

    async def _stop_observer(self, session: Session) -> None:
        """Halt transcript observation and wait for the tail task to be gone.

        Callers that are about to rewrite the record's conversation identity must
        do this first: a still-running observer re-derives ``native_session_id``
        from the file it is tailing at the top of every loop, so a cancellation
        that has not landed yet can put the *previous* conversation's id back over
        the one just written.
        """
        session.agent_stop_event.set()
        if session.observer_task and not session.observer_task.done():
            session.observer_task.cancel()
            await asyncio.gather(session.observer_task, return_exceptions=True)
        session.observer_task = None

    async def _apply_conversation_rollover(
        self,
        session: Session,
        *,
        native_id: str,
        transcript: Path | None,
        reason: str,
        source: str,
        confirmed: bool = True,
    ) -> bool:
        """Retire the current agent run and open a new one on the same PTY.

        An in-CLI ``/clear`` (Claude) or ``/new`` (Codex) replaces the provider
        conversation underneath a live session: new native id, new transcript file,
        same PTY, same mux session, same hook secret and MCP token. That is a new
        *agent run*, and treating it as one is what keeps every run-scoped consumer
        honest — queue bindings strand instead of delivering into a wiped
        conversation, Tier 0 facts and detector evidence stop spanning the seam,
        the titler retitles, and Branch forks the conversation the user is actually
        in rather than its predecessor.

        The outgoing run is closed exactly as an agent exit closes it, so its
        history row keeps its own native id, transcript path, indexed messages, and
        final token/context figures. The successor gets a *new* row. Nothing that
        measured the old conversation carries into the new one.

        Does not touch the observer task — the two callers differ on that. Returns
        ``True`` when a rollover actually happened.
        """
        record = session.record
        backend = record.backend
        if backend not in AGENT_BACKENDS or session.stopping:
            return False
        if record.state in TERMINAL_STATES:
            return False
        if not native_id or native_id == record.native_session_id:
            return False
        if session.agent_lifecycle_id == native_id:
            return False
        owner = self._live_conversation_owner(session, backend, native_id)
        if owner is not None:
            # An in-CLI `/resume` onto a conversation a live sibling owns. Following
            # it would put two panes on one conversation and — because a rollover
            # moves `agent_lifecycle_id` — would make this pane look like a rightful
            # owner to the identity sweep, which then heals neither and leaves the
            # collision standing. Verified live: two panes both reported the same
            # conversation, and its tokens, indefinitely.
            #
            # Refusing keeps this pane's identity intact, but its CLI really is
            # writing somewhere else now, so our view of it is no longer true:
            # fail closed the same way an unfollowable rollover does.
            record.observation_stale_since = time.time()
            session.observation_stale_reason = "conversation_owned_elsewhere"
            record.observation_diagnostic = (
                f"the CLI moved to conversation {native_id}, which live session "
                f"{owner.record.id} owns; refusing to follow it"
            )
            session.state_transitions.append(
                {
                    "ts": time.time(),
                    "kind": "observation_liveness_lost",
                    "reason": "rollover_claimed_by_live_sibling",
                    "native_session_id": native_id,
                }
            )
            log.warning(
                "session %s tried to roll onto conversation %s owned by live session %s",
                record.id,
                native_id,
                owner.record.id,
            )
            await self.events.emit(
                "conversation_rollover_refused",
                session_id=record.id,
                source="daemon",
                backend=backend,
                native_session_id=native_id,
                owner_session_id=owner.record.id,
                reason="claimed_by_live_sibling",
            )
            session.publish_update()
            return False
        await self._await_registration(session)
        previous_run_id = record.agent_run_id
        previous_native_id = record.native_session_id
        # Close the outgoing run against its own final numbers, before any reset.
        await self.history.update_agent_summary(record)
        await self.history.agent_run_ended(record, reason)
        # Spooled hook events and the retired conversation both belong to the run
        # that just ended; replaying either into the successor is cross-attribution.
        self.discard_hook_spool(record.id)
        if previous_native_id:
            session.ignored_detection_runs.add((backend, previous_native_id))
        record.agent_run_id = str(uuid.uuid4())
        record.agent_run_started_at = time.time()
        record.agent_run_seq += 1
        record.native_session_id = native_id
        # The lifecycle anchor is what Branch forks from and what identity
        # reconciliation heals back to, so only CLI-confirmed rollovers (hook
        # ingress) may move it. A heuristic transcript switch is the daemon's
        # guess; letting it rewrite the anchor is how a single wrong guess became
        # permanent, unrepairable cross-attribution. Codex keeps the old
        # behaviour — the anchor doubles as its demotion token and must track
        # the observed conversation there.
        if confirmed or backend != "claude":
            session.agent_lifecycle_id = native_id
        session.transcript_path = transcript
        # Transcript liveness evidence belongs to the file it was observed on.
        session.transcript_growth_ts = 0.0
        session.transcript_record_ts = 0.0
        # The retired conversation's prompts must not title the new one.
        session.first_user_prompt = None
        session.last_user_prompt = None
        record.observation_stale_since = None
        record.observation_diagnostic = None
        session.observation_stale_reason = None
        record.tokens_in = 0
        record.tokens_out = 0
        record.tokens_cache_read = 0
        record.tokens_cache_write = 0
        record.cost_usd = 0.0
        record.context_window = 0
        record.context_pct = 0.0
        record.context_peak_pct = 0.0
        record.compaction_count = 0
        record.last_compaction_at = None
        record.compaction_capability = None
        record.compaction_confidence = None
        record.model = None
        record.measurement_source = None
        record.parser_status = "waiting"
        record.parser_diagnostic = None
        record.parser_events_seen = 0
        record.parser_unknown_events = 0
        record.parser_unknown_signatures = {}
        record.parser_schema_version = None
        reset_session_observation_state(session, f"conversation_rolled:{source}")
        clear_all_standing_activity(session, evidence=f"conversation_rolled:{source}")
        session.observation_replay = False
        session.agent_promoted_at = time.time()
        session.state_source_priority = -1
        session.transition(
            "starting", None, source="daemon", evidence=f"conversation_rolled:{source}", force=True
        )
        await self.history.session_promoted(record, str(transcript) if transcript else "")
        session.publish_update()
        await self.events.emit(
            "agent_conversation_rolled",
            session_id=record.id,
            source="daemon",
            backend=backend,
            reason=reason,
            trigger=source,
            previous_agent_run_id=previous_run_id,
            agent_run_id=record.agent_run_id,
            previous_native_session_id=previous_native_id,
            native_session_id=native_id,
            agent_run_seq=record.agent_run_seq,
        )
        return True

    async def roll_agent_conversation(
        self,
        sid: str,
        *,
        native_id: str,
        reason: str,
        source: str,
        transcript: Path | None = None,
    ) -> bool:
        """Public rollover entry point for callers outside the observer.

        Never call this *from* the observer task: it stops and restarts
        observation, which would cancel the caller. The observer applies the
        rollover in place instead and keeps following.
        """
        session = self.sessions.get(sid)
        if session is None:
            return False
        record = session.record
        if not reports_lifecycle_hooks(record.backend):
            return False
        adapter = self.adapters.get(record.backend)
        if transcript is None and adapter is not None:
            transcript = adapter.transcript_path(
                native_id, Path(record.run_cwd or record.cwd)
            )
        # Stop first: an in-flight observer would otherwise re-bind the retired
        # conversation id from the file it is still tailing.
        await self._stop_observer(session)
        rolled = await self._apply_conversation_rollover(
            session,
            native_id=native_id,
            transcript=transcript,
            reason=reason,
            source=source,
        )
        self._start_observer(session, transcript if rolled else session.transcript_path)
        return rolled

    async def _observe(
        self, session: Session, transcript: Path | None, stop_event: asyncio.Event
    ) -> None:
        from .observation import observe_transcript

        adapter = session.adapter
        path = transcript
        provisional = False
        if path is None or not path.exists():
            found = await self._await_owned_transcript(session, stop_event)
            path, provisional = found if found else (None, False)
        backoff = OBSERVER_RESTART_BACKOFF_MIN_SECONDS
        while path and not stop_event.is_set():
            self._aim_observer(session, path)
            session.transcript_provisional = provisional
            native_id = adapter.transcript_native_id(path)
            if native_id and native_id != session.record.native_session_id and not provisional:
                # Codex binds a placeholder mux id until the rollout file names
                # the real conversation, so re-deriving from the file is correct
                # there. For Claude the record identity is authoritative (spawn
                # `--session-id` or a hook-reported rollover) and every path
                # handed to the observer was derived from it — a mismatched stem
                # means the path is wrong, and rekeying the record to match it is
                # exactly the cross-attribution this design forbids.
                #
                # A provisional follow never reaches here: the whole point is that
                # the file was chosen by elimination, so its id is precisely what
                # has not been established. Identity waits for the hook.
                if session.record.backend == "claude":
                    log.warning(
                        "observer for session %s handed transcript %s that does not "
                        "match its conversation %s; keeping the record identity",
                        session.record.id,
                        path.name,
                        session.record.native_session_id,
                    )
                else:
                    session.record.native_session_id = native_id
            await self._await_registration(session)
            if provisional:
                # The history row is keyed by conversation and carries the
                # transcript path: writing it now would file a possibly-foreign
                # conversation under this pane, which is the cross-attribution
                # this whole path is designed to avoid. `promote_provisional_
                # transcript` writes it if and when the hook confirms the file.
                await self.events.emit(
                    "transcript_provisionally_bound",
                    session_id=session.record.id,
                    source="daemon",
                    scope="root",
                    backend=session.record.backend,
                    transcript_path=str(path),
                )
            else:
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
                # Following a different transcript is not a file-path detail: the
                # CLI is on another conversation, so the agent run ends here and a
                # new one begins. Applied in place rather than through
                # roll_agent_conversation, which would cancel this very task.
                #
                # Unless the file being left was never ours to begin with. A
                # rollover retires a conversation — it rekeys identity, closes the
                # history row, and mints a new agent run — and none of that is
                # meaningful for a guess. Re-aim the guess instead and stay
                # provisional; the hook still decides what this session really is.
                switched_native = adapter.transcript_native_id(switch)
                # A file naming the conversation this session already owns is a
                # *relocation*, not a rollover. Rolling would rekey identity,
                # close the history row, and mint a new agent run for a
                # conversation that never ended - the annotation-and-identity
                # damage of a `/clear` applied to a session that only changed
                # directory.
                if (
                    switched_native
                    and not provisional
                    and switched_native != session.record.native_session_id
                ):
                    await self._apply_conversation_rollover(
                        session,
                        native_id=switched_native,
                        transcript=switch,
                        reason="conversation_rolled",
                        source="transcript_switch",
                        confirmed=False,
                    )
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

        Backends whose CLI reports conversation replacement itself (Claude's
        SessionStart ingress) never take the heuristic path: their rollovers
        arrive through `roll_agent_conversation`, and guessing from mtimes is the
        one mechanism that can latch onto a sibling's conversation in a shared
        cwd. For them this watcher only keeps staleness detection alive.
        """
        heuristic = not getattr(session.adapter, "reports_conversation_rollover", False)
        while not stop_event.is_set() and not observe_task.done():
            await self._await_switch_tick(session, stop_event)
            if stop_event.is_set() or observe_task.done():
                break
            if not has_observable_transcript(session.record.backend):
                break
            # The CLI's own word first. A hook names the file it is writing, which
            # settles in one comparison what the readings below have to infer from
            # a cwd the poller happened to catch and a path the daemon recomputes.
            reported = self._staged_transcript_relocation(session, current)
            if reported is not None:
                await self.events.emit(
                    "transcript_relocated",
                    session_id=session.record.id,
                    source="hook",
                    backend=session.record.backend,
                    previous=str(current),
                    path=str(reported),
                )
                log.info(
                    "session %s transcript relocated to %s (reported by the CLI)",
                    session.record.id,
                    reported,
                )
                return reported
            pending_resolver = getattr(session.adapter, "pending_transcript_path", None)
            try:
                pending = (
                    pending_resolver(session.record.id, current)
                    if callable(pending_resolver)
                    else None
                )
            except (OSError, ValueError):
                pending = None
            if isinstance(pending, Path):
                await self.events.emit(
                    "transcript_retargeted",
                    session_id=session.record.id,
                    source="breadcrumb",
                    backend=session.record.backend,
                    previous=str(current),
                    path=str(pending),
                )
                return pending
            relocated = self._relocated_transcript_candidate(session, current)
            if relocated is not None:
                await self.events.emit(
                    "transcript_relocated",
                    session_id=session.record.id,
                    source="daemon",
                    backend=session.record.backend,
                    previous=str(current),
                    path=str(relocated),
                )
                return relocated
            candidate = (
                self._transcript_switch_candidate(session, current) if heuristic else None
            )
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
            await self._note_transcript_staleness(session, current)
        return None

    @staticmethod
    async def _await_switch_tick(session: Session, stop_event: asyncio.Event) -> None:
        """Sleep one watch tick, waking early when a hook names a new transcript.

        The poll interval is sized for the filesystem readings below, which have
        nothing better to do than look again. A hook is not a reading: the CLI has
        already told us where it is writing, and every tick spent sleeping on that
        is a tick the session spends showing a conversation it has left.
        """
        waiters = [
            asyncio.ensure_future(stop_event.wait()),
            asyncio.ensure_future(session.transcript_relocation_signal.wait()),
        ]
        try:
            await asyncio.wait(
                waiters,
                timeout=TRANSCRIPT_SWITCH_POLL_SECONDS,
                return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            for waiter in waiters:
                waiter.cancel()
            await asyncio.gather(*waiters, return_exceptions=True)

    def _staged_transcript_relocation(self, session: Session, current: Path) -> Path | None:
        """The transcript path a hook reported for this session's own conversation.

        Staged by `note_hook_transcript_path` on the ingress task and consumed here,
        because re-aiming observation means stopping and restarting the tail task and
        the hook handler must never do that: Claude blocks on the POST.

        Cleared before it is read, never after. A hook landing in the gap then leaves
        the signal set and costs one spurious tick, where the other order would drop
        the report entirely and leave the session following a dead file.
        """
        session.transcript_relocation_signal.clear()
        staged = session.pending_transcript_path
        if staged is None:
            return None
        session.pending_transcript_path = None
        if getattr(session, "transcript_provisional", False):
            # A provisional follow has not established which conversation this is,
            # so there is no identity for a reported path to match against.
            return None
        if self._path_key(staged) == self._path_key(current) or not staged.is_file():
            return None
        native_id = session.record.native_session_id
        if not native_id or session.adapter.transcript_native_id(staged) != native_id:
            return None
        _claimed_ids, claimed_paths = self._live_transcript_claims(session)
        if self._path_key(staged) in claimed_paths:
            return None
        return staged

    def note_hook_transcript_path(self, session: Session, payload: dict[str, Any]) -> None:
        """Stage the transcript file the CLI itself says it is writing.

        Every Claude hook payload carries `transcript_path`, and until now the daemon
        read it only when the payload also reported a *new conversation id* — so the
        one case it could not see was the file moving while the conversation stayed
        the same. That is precisely what entering a native worktree does, and the
        result was a session that went silently unobserved for the rest of its life:
        no reader tab, frozen tokens, no titles, and a history row pointing at a path
        that no longer existed.

        The evidence is strong enough to act on without corroboration. The POST is
        loopback-only and authenticated with this session's own hook secret; the
        caller has already established that the payload speaks for *this* session's
        conversation (`foreign_conversation_hook_id`); and the reported file must
        still be the one named after that conversation id, which mux dictated at
        spawn. A nested child CLI inherits the hook wiring but not the id, so it
        cannot point this session anywhere.

        Explicitly not a rollover. The conversation did not end - it changed address.
        Rolling would rekey identity, close the history row, and mint a new agent run
        for a session that only changed directory.
        """
        record = session.record
        if not reports_lifecycle_hooks(record.backend) or session.stopping:
            return
        if not descriptor(record.backend).reports_transcript_path:
            return
        if not getattr(session.adapter, "resolves_transcript_by_cwd", False):
            # A backend that addresses conversations by id never moves a file, so a
            # differing path here would be a report about some other conversation.
            return
        raw = str(payload.get("transcript_path") or "").strip()
        if not raw or not record.native_session_id:
            return
        current = session.transcript_path
        # Textual normcase first and `_path_key` only on a genuine difference: this
        # runs on every hook of every turn, and `_path_key` resolves the path, which
        # is filesystem I/O for a question that is almost always "no, same file".
        if current is not None and os.path.normcase(raw) == os.path.normcase(str(current)):
            return
        reported = Path(raw)
        if session.adapter.transcript_native_id(reported) != record.native_session_id:
            return
        if current is not None and self._path_key(reported) == self._path_key(current):
            return
        session.pending_transcript_path = reported
        session.transcript_relocation_signal.set()

    def _relocated_transcript_candidate(self, session: Session, current: Path) -> Path | None:
        """The same conversation's file after the CLI's cwd moved it.

        Claude derives a transcript's directory from the CLI's working directory,
        so entering a native worktree *moves the file*: the path resolved at
        spawn stops existing and a file with the same name appears under the new
        directory's slug. Nothing in the daemon is told. Measured live
        2026-08-06 on a session that entered `.claude/worktrees/<name>`: the
        observer sat on a path that no longer existed, `parser_status` stayed
        frozen at `ready` from its last successful read, and because
        `_transcript_authoritative` reads that field it kept suppressing the hook
        tier as redundant. The result was a session latched `idle` for four
        minutes while its own screen showed the working spinner, its cli-state
        file read `busy`, and root turn hooks kept arriving 8 s apart - every
        layer that could have spoken was either blind or being dropped.

        This is emphatically **not** the mtime heuristic in
        `_transcript_switch_candidate`, and needs none of its ownership
        analysis. That one guesses *which* conversation a session moved to and
        can latch onto a sibling's; this one re-finds a file named by the
        conversation id the session already owns. Both halves are proven, not
        inferred: the followed path is gone, and the candidate's stem is this
        session's own `native_session_id`.

        Requires an adapter that derives a path from (native id, cwd) - Claude.
        Codex mints rollout filenames the daemon cannot reconstruct, so it falls
        through to the staleness net instead.
        """
        record = session.record
        native_id = record.native_session_id
        if not native_id or getattr(session, "transcript_provisional", False):
            return None
        if current.exists():
            # A live file is never relocated out from under us; only the CLI
            # moving its own conversation makes the followed path disappear.
            return None
        cli_state = getattr(session, "cli_state", None)
        cwd = str(cli_state.get("cwd") or "") if isinstance(cli_state, dict) else ""
        if not cwd or self._path_key(cwd) == self._path_key(record.run_cwd or record.cwd):
            return None
        resolver = getattr(session.adapter, "transcript_path", None)
        if resolver is None:
            return None
        try:
            candidate = cast("Path", resolver(native_id, Path(cwd)))
        except (OSError, ValueError):
            return None
        if candidate == current or not candidate.exists():
            return None
        if session.adapter.transcript_native_id(candidate) != native_id:
            return None
        return candidate

    @staticmethod
    def _aim_observer(session: Session, path: Path) -> None:
        """Point the session at `path`, discarding growth evidence if it moved.

        Transcript growth and record timestamps describe one file, so re-aiming
        has to invalidate them. Re-tailing the *same* file after an observer fault must not: the
        observer loop runs this on every restart, and clearing there would drop the
        session back onto the filesystem timestamp this evidence exists to replace
        — silently, and for as long as the agent then stayed quiet.
        """
        if session.transcript_path != path:
            session.transcript_growth_ts = 0.0
            session.transcript_record_ts = 0.0
        session.transcript_path = path

    @staticmethod
    def _transcript_last_write_ts(session: Session, current: Path) -> float | None:
        """When the followed transcript was last written, as well as we can know it.

        `stat().st_mtime` alone is **not** that answer. Windows does not keep a
        live file's last-write time current: measured 2026-08-06, every long Codex
        rollout on this machine reported an mtime frozen at the file's creation
        while its content ran hours ahead, and every Win32 timestamp API agreed, so
        there is no better call to substitute. Trusting it made
        `_note_transcript_staleness` fire on healthy Codex sessions roughly 90 s
        into their life and then keep flapping — which hard-blocks delivery on
        `transcript_stale`, so an armed queue message sat unsent at the exact moment
        the agent finished and was ready for it.

        The tailer's own reading is the fix: it polls `st_size` (which stays
        accurate) every 250 ms and stamps `transcript_growth_ts` when bytes appear
        past its attach snapshot. That is a first-hand observation of a write rather
        than the filesystem's opinion about one.

        The newest valid provider record timestamp covers the remaining bind race:
        the observer can attach after a complete first turn is already present, so
        the tailer correctly labels those bytes historical and reports no growth.
        Replaying an old file does not make this value fresh because it retains the
        record's original wall time. All three readings are lower bounds on "this
        file was alive at T", so the later one is the better answer.
        """
        try:
            mtime = current.stat().st_mtime
        except OSError:
            return None
        return max(
            mtime,
            float(getattr(session, "transcript_growth_ts", 0.0)),
            float(getattr(session, "transcript_record_ts", 0.0)),
        )

    async def _clear_transcript_staleness(self, session: Session, evidence: str) -> None:
        """Retract the staleness claim once the followed file proves itself alive.

        Without this the flag is a one-way door for anything the observer cannot
        parse: `_record_parser_observation` clears it on a *record*, so a file that
        resumes growing without yielding a complete record — or one marked stale by
        a transient — stays blocked for delivery until the session ends.
        """
        record = session.record
        marked = record.observation_stale_since
        if marked is None:
            return
        record.observation_stale_since = None
        record.observation_diagnostic = None
        session.observation_stale_reason = None
        session.state_transitions.append(
            {
                "ts": time.time(),
                "kind": "observation_liveness_restored",
                "reason": evidence,
            }
        )
        log.info(
            "session %s observation no longer stale after %.1fs (%s)",
            record.id,
            max(0.0, time.time() - marked),
            evidence,
        )
        session.publish_update()
        await self.events.emit(
            "observation_stale_cleared",
            session_id=record.id,
            source="daemon",
            backend=record.backend,
            evidence=evidence,
            stale_for_s=round(max(0.0, time.time() - marked), 3),
        )

    async def _note_transcript_staleness(self, session: Session, current: Path) -> None:
        """Fail closed when the conversation moved somewhere we cannot prove.

        A Codex `SessionStart` normally reports `/new`, but hooks may be disabled,
        untrusted, or unavailable. In that fallback path, a `/new` behind a sibling
        that cannot be ruled out leaves the observer tailing a file that will never
        change again. Silence alone is not evidence of that — an idle agent is also quiet.
        The evidence is a hook whose event *must* have written transcript records
        (a prompt submitted, a tool run, a turn stopped) arriving after the file
        went dead: the CLI ran a turn and none of it landed where we are looking.

        `last_turn_hook_ts`, never `last_hook_ts`. The most frequent hook on a quiet
        session is `Notification:idle_prompt`, which fires ~60 s after a turn ends to
        say the agent is waiting — it accompanies no transcript activity by design,
        so counting it flagged every healthy idle agent in the fleet.

        "Last written" is `_transcript_last_write_ts`, never `stat().st_mtime` on
        its own — the filesystem's timestamp for a file a CLI holds open is not
        reliable, and believing it turned this fail-closed guard into a false alarm
        on every healthy long-lived Codex session.

        Marking it stale is what stops the session from reporting a retired
        conversation's status as live: hooks resume driving state
        (`_transcript_authoritative` goes false) and delivery hard-blocks. It is
        cleared by the next record read on any followed transcript, by the followed
        file growing again (below), or by a rollover.
        """
        record = session.record
        last_write = self._transcript_last_write_ts(session, current)
        now = time.time()
        if last_write is None:
            # The followed file is unreadable or gone. This used to return -
            # "no reading" was treated as "no evidence" - which made the one
            # guard written for a conversation that moved unreachable in the
            # case where it moved *hardest*: a relocated transcript leaves a
            # path that does not exist, so there is no timestamp to be quiet.
            # The session then kept `parser_status == "ready"` from its last
            # successful read, and that keeps hooks suppressed as redundant to a
            # transcript that can no longer report anything.
            #
            # A vanished file is only *evidence* alongside a turn hook: the CLI
            # ran a turn and none of it landed where we are looking. Without one
            # this is an ordinary startup race (the observer is aimed before the
            # CLI creates the file) and must stay silent.
            if record.observation_stale_since is not None or not session.last_turn_hook_ts:
                return
            if now - session.last_turn_hook_ts > TRANSCRIPT_STALE_SECONDS:
                return
            record.observation_stale_since = now
            session.observation_stale_reason = "transcript_missing"
            record.observation_diagnostic = (
                f"transcript {current.name} is missing while the CLI kept reporting "
                "activity; the conversation may have moved"
            )
            session.state_transitions.append(
                {
                    "ts": now,
                    "kind": "observation_liveness_lost",
                    "reason": "transcript_missing",
                    "transcript_path": str(current),
                }
            )
            log.warning(
                "session %s observation stale: transcript %s missing with a turn hook %.0fs ago",
                record.id,
                current,
                now - session.last_turn_hook_ts,
            )
            session.publish_update()
            await self.events.emit(
                "observation_stale",
                session_id=record.id,
                source="daemon",
                backend=record.backend,
                transcript_path=str(current),
                transcript_last_write=None,
                transcript_mtime=None,
                transcript_growth_ts=float(getattr(session, "transcript_growth_ts", 0.0)),
                last_turn_hook_ts=session.last_turn_hook_ts,
                reason="transcript_missing",
            )
            return
        if record.observation_stale_since is not None:
            # Already blocked. The only thing worth checking is whether the file we
            # gave up on has been written since we did, which retracts the claim.
            # Deliberately *not* the negation of the predicate below: the other two
            # callers that set this flag (a rollover refused because a live sibling
            # owns the conversation, a CLI-reported rollover that could not be
            # adopted) know the CLI is elsewhere, and quiet on the abandoned file is
            # not permission to forget that.
            corroborates_latest_turn = (
                getattr(session, "observation_stale_reason", None)
                in {"transcript_stale", "transcript_missing"}
                and session.last_turn_hook_ts > 0.0
                and session.last_turn_hook_ts
                <= last_write + TRANSCRIPT_SWITCH_QUIET_SECONDS
            )
            if corroborates_latest_turn:
                await self._clear_transcript_staleness(
                    session, "transcript_record_corroborated_turn"
                )
            elif last_write > record.observation_stale_since:
                await self._clear_transcript_staleness(session, "transcript_written_again")
            return
        stale = (
            now - last_write >= TRANSCRIPT_STALE_SECONDS
            and session.last_turn_hook_ts > last_write + TRANSCRIPT_SWITCH_QUIET_SECONDS
        )
        if not stale:
            return
        record.observation_stale_since = now
        session.observation_stale_reason = "transcript_stale"
        record.observation_diagnostic = (
            f"transcript {current.name} last written "
            f"{int(now - last_write)}s ago while the CLI kept reporting activity; "
            "the conversation may have been replaced"
        )
        session.state_transitions.append(
            {
                "ts": now,
                "kind": "observation_liveness_lost",
                "reason": "transcript_stale",
                "transcript_path": str(current),
            }
        )
        # Warned, not just emitted: this blocks delivery and revokes the
        # transcript's authority, and the 2026-08-06 false-positive incident left
        # nothing in `daemon.log` at all — it took a query against the `events`
        # table to find. Both readings are on the line so the log alone
        # distinguishes a dead file from a filesystem that stopped dating a live one.
        log.warning(
            "session %s observation stale: transcript %s last written %.0fs ago "
            "(mtime %.0fs ago, growth %.0fs ago, record %.0fs ago) with a turn hook %.0fs ago",
            record.id,
            current.name,
            now - last_write,
            now - (_safe_mtime(current) or now),
            now - (getattr(session, "transcript_growth_ts", 0.0) or now),
            now - (getattr(session, "transcript_record_ts", 0.0) or now),
            now - (session.last_turn_hook_ts or now),
        )
        session.publish_update()
        await self.events.emit(
            "observation_stale",
            session_id=record.id,
            source="daemon",
            backend=record.backend,
            transcript_path=str(current),
            transcript_last_write=last_write,
            # Both inputs, so a post-mortem can tell a genuinely dead file from a
            # filesystem that stopped dating a live one.
            transcript_mtime=_safe_mtime(current),
            transcript_growth_ts=float(getattr(session, "transcript_growth_ts", 0.0)),
            transcript_record_ts=float(getattr(session, "transcript_record_ts", 0.0)),
            last_turn_hook_ts=session.last_turn_hook_ts,
        )

    @staticmethod
    def _resolved_cwd(record: SessionRecord) -> Path:
        cwd = Path(record.run_cwd or record.cwd)
        try:
            return cwd.resolve()
        except OSError:
            return cwd

    def _same_cwd_siblings(self, session: Session, cwd: Path) -> list[Session]:
        try:
            key = cwd.resolve()
        except OSError:
            key = cwd
        return [
            other
            for other in self.sessions.values()
            if other is not session
            and other.record.state not in TERMINAL_STATES
            and self._resolved_cwd(other.record) == key
        ]

    def _pending_agent_launch_sibling(self, session: Session, cwd: Path) -> bool:
        """An unpromoted shell here is about to create a transcript of its own.

        Its shim-less launch has echoed the agent's name but has not been promoted
        yet, so it owns no transcript and no native id — there is nothing to rule
        it out *with*. This session's 2s switch watcher can beat that shell's 0.5s
        detection loop to the claim, stealing the new CLI's conversation and
        permanently rekeying this record. Blocks unconditionally.
        """
        backend = session.record.backend
        return any(
            other.record.backend == "shell" and backend in other.pending_agent_backends
            for other in self._same_cwd_siblings(session, cwd)
        )

    def _unresolved_transcript_sibling(
        self, session: Session, cwd: Path, created: float
    ) -> bool:
        """True when a same-backend sibling here cannot be excluded as the writer.

        The blanket "any sibling in this cwd blocks the switch" rule was correct
        but far too broad: in a project with two agents open it suppressed every
        in-CLI conversation change forever, which is how a `/clear` left the
        observer tailing a dead file. The ambiguity it defends against is specific
        — *which* session created this new transcript — and two pieces of evidence
        already on the record settle it per candidate:

        - a sibling whose own transcript was still being written after the
          candidate appeared is demonstrably still on its own conversation, and
        - a sibling whose PTY produced nothing across the candidate's creation
          cannot have produced the candidate (the mirror of the corroboration
          ``_session_could_have_written`` applies to this session).

        Anything else — a quiet sibling that was also talking, or one with no
        transcript bound yet — stays a blocker. Uncertainty keeps the old answer.
        """
        backend = session.record.backend
        for other in self._same_cwd_siblings(session, cwd):
            if other.record.backend != backend:
                continue
            sibling_path = getattr(other, "transcript_path", None)
            if sibling_path is not None:
                # The sibling's own growth reading counts here for the same reason
                # it does for ours: a frozen mtime would hide the very evidence
                # that clears this sibling of having written the candidate.
                sibling_write = self._transcript_last_write_ts(other, sibling_path)
                if sibling_write is not None and sibling_write > created:
                    continue
            last_output = other.record.last_activity_ts
            if last_output and last_output < created - TRANSCRIPT_SWITCH_QUIET_SECONDS:
                continue
            return True
        return False

    def _transcript_switch_candidate(self, session: Session, current: Path) -> Path | None:
        record = session.record
        now = time.time()
        # "The file we follow has gone quiet" is the precondition for retargeting at
        # all. Read from `stat().st_mtime` alone it was not enforced on Windows,
        # where a live transcript's timestamp can sit frozen at its creation — so
        # this ran its candidate search against an actively-written file, and the
        # only thing standing between that and adopting a sibling's conversation
        # was the ownership evidence below.
        last_write = self._transcript_last_write_ts(session, current)
        if last_write is None:
            last_write = 0.0
        if now - last_write < TRANSCRIPT_SWITCH_QUIET_SECONDS:
            return None
        started = record.agent_run_started_at or record.created_at
        cwd = Path(record.run_cwd or record.cwd)
        # A shim-less agent launch in a sibling shell is about to create a
        # transcript here and cannot be ruled out by any evidence, so it blocks
        # every candidate. Same-backend siblings are evaluated per candidate below.
        if self._pending_agent_launch_sibling(session, cwd):
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
            if not self._session_could_have_written(session, path):
                continue
            created = file_created_at(path)
            if created is None or self._unresolved_transcript_sibling(session, cwd, created):
                continue
            if best is None or modified > best[0]:
                best = (modified, path)
        return best[1] if best else None

    @staticmethod
    def _session_could_have_written(session: Session, candidate: Path) -> bool:
        """Corroborate that *this* PTY produced the candidate transcript.

        The candidate pool is the backend's shared per-cwd transcript directory,
        which every CLI on the machine writes into — a VS Code Claude extension or
        a one-off terminal `claude` in the same repo lands there too. Without
        corroboration an idle mux session adopts that outsider's conversation,
        rekeys its own native_session_id, rebinds its history row, and renders the
        outsider's status and tokens as its own.

        The corroboration is cheap and hard to fake: if this session's CLI wrote
        the file, this session's PTY produced output while the file was being
        written. An outside CLI leaves our PTY silent.
        """
        created = file_created_at(candidate)
        if created is None:
            return False
        last_output = session.record.last_activity_ts
        if not last_output:
            return False
        # Output must have continued after the candidate appeared, with a small
        # allowance for the CLI writing its first record before it repaints.
        return last_output >= created - TRANSCRIPT_SWITCH_QUIET_SECONDS

    def _claude_owns_conversation(self, session: Session, native_id: str) -> bool:
        """Evidence that this pane's claim on the conversation is legitimate.

        Its own mux id (minted via ``--session-id``), a CLI-confirmed lifecycle
        anchor, or an unrolled resume still sitting on the conversation it was
        spawned to resume. Anything else claiming a contested conversation is
        holding observer output, not identity evidence.
        """
        record = session.record
        return (
            record.id == native_id
            or session.agent_lifecycle_id == native_id
            or (record.agent_run_seq == 0 and record.spawn_native_session_id == native_id)
        )

    def _live_conversation_owner(
        self, session: Session, backend: str, native_id: str
    ) -> Session | None:
        """The live session that legitimately holds `native_id`, if any.

        Used to refuse a rollover onto a conversation somebody else is already on.
        A sibling only counts as the owner when its own claim is supported by
        identity evidence; deferring to a sibling that is itself misattributed
        would freeze the corruption in place instead of letting the sweep heal it.
        """
        for other in self.sessions.values():
            if other is session:
                continue
            record = other.record
            if (
                record.backend != backend
                or record.state in TERMINAL_STATES
                or other.stopping
                or record.native_session_id != native_id
            ):
                continue
            if backend == "claude" and not self._claude_owns_conversation(other, native_id):
                continue
            return other
        return None

    async def _reconcile_identity_collisions(self) -> None:
        """Enforce that no two live sessions claim one conversation.

        The observer machinery is designed never to create such a claim, so a
        collision means corrupted state (legacy heuristic switches, or an in-CLI
        resume of a live sibling's conversation). Every group is reported once;
        Claude members whose claim is unsupported by identity evidence are healed
        back to their own anchor, because for Claude the anchor is provable —
        the conversation named by the pane's own mux id, or the last one the CLI
        itself confirmed over the hook ingress.
        """
        groups: dict[tuple[str, str], list[Session]] = {}
        for session in tuple(self.sessions.values()):
            record = session.record
            if (
                record.backend in AGENT_BACKENDS
                and record.state not in TERMINAL_STATES
                and not session.stopping
                and record.native_session_id
            ):
                groups.setdefault((record.backend, record.native_session_id), []).append(session)
        collisions = {key: members for key, members in groups.items() if len(members) > 1}
        self._known_identity_collisions &= set(collisions)
        for (backend, native_id), members in collisions.items():
            if (backend, native_id) not in self._known_identity_collisions:
                self._known_identity_collisions.add((backend, native_id))
                log.warning(
                    "identity collision: sessions %s all claim %s conversation %s",
                    ", ".join(sorted(s.record.id for s in members)),
                    backend,
                    native_id,
                )
                await self.events.emit(
                    "identity_collision_detected",
                    source="daemon",
                    backend=backend,
                    native_session_id=native_id,
                    session_ids=sorted(s.record.id for s in members),
                )
            if backend != "claude":
                continue
            for session in members:
                if not self._claude_owns_conversation(session, native_id):
                    await self._heal_claude_identity(session, native_id)

    async def maybe_heal_from_own_conversation_hook(
        self, session: Session, payload: dict[str, Any]
    ) -> bool:
        """Heal a session whose own spawn conversation speaks while bound elsewhere.

        Claude is spawned with ``--session-id <mux id>``, so a hook naming exactly
        ``record.id`` can only come from the conversation this PTY was created to
        run. If the record is bound to some other conversation at that moment, the
        binding is corruption — a nested child CLI rolled the identity away — and
        the pane's own conversation speaking is the strongest possible proof.

        The one legitimate way the spawn conversation retires is an in-CLI
        replacement (`/clear`), and a rollover records exactly that in
        ``ignored_detection_runs`` — so a retired conversation's stale hook can
        never un-clear a session. The set is in-memory and dies with the daemon,
        which is correct on both sides: a retired conversation cannot outlive its
        CLI process to speak after a restart, while a corrupted binding adopted
        from the supervisor is healed by the first real hook that arrives.
        """
        record = session.record
        if record.backend != "claude" or record.state in TERMINAL_STATES or session.stopping:
            return False
        native_id = str(payload.get("session_id") or payload.get("sessionId") or "")
        if not _CLAUDE_NATIVE_ID.fullmatch(native_id) or native_id != record.id:
            return False
        disputed = record.native_session_id or ""
        if not disputed or disputed == native_id:
            return False
        if ("claude", native_id) in session.ignored_detection_runs:
            return False
        await self._heal_claude_identity(
            session, disputed, trigger="own_conversation_hook"
        )
        return record.native_session_id == native_id

    async def _heal_claude_identity(
        self, session: Session, disputed: str, *, trigger: str = "live_sweep"
    ) -> None:
        """Send a Claude session back to its own conversation.

        The disputed conversation provably is not this pane's, so keeping the
        observer on it renders a sibling's status and tokens under this
        session's identity. The heal is the inverse of the corruption: restore
        the strongest available anchor, rebind the observer to that
        conversation's deterministic transcript, and point the history row at
        it. If the anchor is the spawn conversation, the pane's original run
        row is reopened and the run minted for the stolen conversation is
        quarantined; otherwise the current run row is repaired in place and its
        copied messages dropped so the correct file reindexes from the start.
        """
        record = session.record
        anchor_candidates: list[str | None] = [session.agent_lifecycle_id]
        if record.spawn_backend == "claude":
            anchor_candidates.extend([record.spawn_native_session_id, record.id])
        anchor = next(
            (
                candidate
                for candidate in anchor_candidates
                if candidate and candidate != disputed and _CLAUDE_NATIVE_ID.fullmatch(candidate)
            ),
            None,
        )
        if anchor is None:
            return
        previous = {
            "backend": record.backend,
            "native_session_id": record.native_session_id,
            "transcript_path": str(session.transcript_path) if session.transcript_path else "",
        }
        await self._stop_observer(session)
        bad_run_id: str | None = None
        if anchor == record.id:
            # An inherited run row belongs to the conversation this pane resumed,
            # not to this pane, so leaving it behind is all that is warranted:
            # quarantining it would delete a conversation's real history over a
            # dispute about which conversation this PTY is on.
            if record.agent_run_id and record.agent_run_id not in {
                record.id,
                record.spawn_agent_run_id,
            }:
                bad_run_id = record.agent_run_id
            record.agent_run_id = record.id
            record.agent_run_started_at = record.created_at
            record.agent_run_seq = 0
        record.native_session_id = anchor
        session.agent_lifecycle_id = anchor
        # Ledgered clear first: _reset_provider_observation empties the record's
        # annotation list silently, which would leave nothing to ledger.
        clear_all_standing_activity(session, evidence="identity_reconciled")
        self._reset_provider_observation(record)
        reset_session_observation_state(session, "identity_reconciled")
        session.observation_replay = False
        session.state_source_priority = -1
        session.transcript_path = None
        session.transcript_provisional = False
        apply_state_transition(
            session,
            "starting",
            None,
            source="daemon",
            evidence="identity_reconciled",
            force=True,
        )
        transcript = session.adapter.transcript_path(
            anchor, Path(record.run_cwd or record.cwd)
        )
        run_id = record.agent_run_id or record.id
        if bad_run_id:
            await self.history.quarantine_misattributed_agent_run(
                bad_run_id, "live_identity_reconciled"
            )
        else:
            await self.history.reset_run_transcript_copy(run_id)
        await self.history.session_promoted(record, str(transcript) if transcript else "")
        await self.history.reopen_agent_run(run_id)
        session.publish_update()
        await self.events.emit(
            "session_identity_reconciled",
            session_id=record.id,
            source="daemon",
            previous=previous,
            backend=record.backend,
            native_session_id=anchor,
            transcript_path=str(transcript) if transcript else None,
            trigger=trigger,
        )
        self._start_observer(session, transcript)

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
            await asyncio.sleep(STATE_WATCHDOG_POLL_SECONDS)
            # Never let the safety net die silently; the guard also publishes the
            # loop's liveness and fault count to /api/diagnostics/background.
            with background.iteration(STATE_WATCHDOG_LOOP):
                await self._reconcile_identity_collisions()
                now = time.time()
                # File I/O off the loop; counter/ledger application on it.
                cli_states = await asyncio.to_thread(self.cli_state_monitor.poll)
                self.cli_state_monitor.observe(
                    cli_states, tuple(self.sessions.values()), now
                )
                for session in tuple(self.sessions.values()):
                    await self._watchdog_check_session(session, now)

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
        if not has_observable_transcript(record.backend):
            return
        # Annotation TTL sweep runs before any early return below: expiry is
        # time-based and owes nothing to turn state or transcript quiet.
        if expire_standing_activity(session, now=now):
            session.publish_update()
        await self._drain_hook_spool(session)
        # Hook-recency layer reading, before any early return: crossing the
        # staleness threshold (or the first turn hook arriving) is ledgered
        # once per flip, so a post-mortem can say what this layer read at any
        # moment. `last_turn_hook_ts`, never `last_hook_ts` — idle_prompt
        # notifications accompany no transcript activity by design.
        # getattr-guarded for the lightweight watchdog stand-ins in tests.
        last_turn_hook_ts = getattr(session, "last_turn_hook_ts", 0.0)
        if last_turn_hook_ts:
            since_turn_hook = now - last_turn_hook_ts
            note_layer_reading(
                session,
                "hook_recency",
                "stale" if since_turn_hook >= TRANSCRIPT_STALE_SECONDS else "fresh",
                now=now,
                detail={"seconds_since_turn_hook": round(since_turn_hook, 1)},
            )
        else:
            note_layer_reading(session, "hook_recency", "never", now=now)
        if session.observation_replay:
            return
        # The unwitnessed pair runs before the active-state gate below, because it
        # is the only rule here that reads an `idle` session — the state a fresh
        # Codex pane is stranded in for its whole first turn.
        if session_is_unwitnessed(session) and await self._check_unwitnessed_pty_turn(
            session, now
        ):
            return
        # Startup dialogs also read idle sessions: a trust/update dialog blocks
        # before any turn has ever run while hook-sourced idle wins the display.
        # Inline rather than a method so the lightweight manager stand-ins in
        # tests exercise this rule through the same call they already make.
        if record.state in {"idle", "awaiting"}:
            dialog_pty_state = self._pty_tail_state(session)
            note_layer_reading(session, "pty_tail", dialog_pty_state, now=now)
            no_turn, dialog_seconds = startup_dialog_observation(
                session, dialog_pty_state, now
            )
            if no_turn:
                dialog_action = watchdog_decision(
                    record.state,
                    stalled_seconds=now - session.last_state_change_ts,
                    tail_verdict=None,
                    pty_state=dialog_pty_state,
                    awaiting_reason=record.awaiting_reason,
                    startup_no_turn=True,
                    startup_dialog_seconds=dialog_seconds,
                    startup_dialog_raised=bool(
                        session.observation_state.get("startup_dialog_raised")
                    ),
                )
                if dialog_action in {"startup_dialog_block", "startup_dialog_clear"}:
                    await apply_watchdog_recovery(
                        session,
                        self.events,
                        dialog_action,
                        stalled_seconds=dialog_seconds,
                        tail_verdict=None,
                    )
                    return
        else:
            session.startup_dialog_screen_since = None
        if record.state not in {"working", "awaiting"}:
            # Leaving the active states ends any blind window.
            session.classifier_blind_since = None
            session.classifier_blind_counted = False
            return
        stalled = now - session.last_state_change_ts
        pty_state = self._pty_tail_state(session)
        note_layer_reading(session, "pty_tail", pty_state, now=now)
        note_classifier_blindness(session, pty_state, now)
        # An answered permission prompt is resolved from the PTY alone and must
        # be checked before the transcript-quiet gate below: after approval the
        # transcript is usually *busy*, which would skip this pass entirely.
        if (
            watchdog_decision(
                record.state,
                stalled_seconds=stalled,
                tail_verdict=None,
                pty_state=pty_state,
                awaiting_reason=record.awaiting_reason,
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
        verdict: str | None
        if path is None:
            # No transcript at all (never bound, or bound to a file that has since
            # gone) is exactly the case the PTY backstop is documented to cover:
            # returning here made "wrong/missing transcript still recovers"
            # unreachable, which is also the case with no other recovery path.
            verdict = "unknown"
        else:
            try:
                mtime = path.stat().st_mtime
            except OSError:
                verdict = "unknown"
            else:
                if now - mtime < STATE_WATCHDOG_TRANSCRIPT_QUIET_SECONDS:
                    # The transcript is still moving; the live observer owns it.
                    return
                verdict = await asyncio.to_thread(
                    transcript_tail_turn_state, record.backend, path
                )
        # Re-derive after the await. `stalled`/`pty_state`/`record.state` were
        # captured before a threaded tail read that can take real time, and an
        # approval raised inside that window would otherwise be judged against
        # pre-approval evidence and instantly resumed to `working` — hiding a
        # prompt the user has not answered, the one outcome this design forbids.
        if record.state not in {"working", "awaiting"}:
            return
        stalled = time.time() - session.last_state_change_ts
        pty_state = self._pty_tail_state(session)
        note_layer_reading(session, "pty_tail", pty_state, now=now)
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
            awaiting_reason=record.awaiting_reason,
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

    async def _check_unwitnessed_pty_turn(self, session: Session, now: float) -> bool:
        """Drive turn state from the screen while nothing else can. True if applied.

        Only reached for a session `session_is_unwitnessed` accepts, so this can
        neither outrank nor pre-empt a real source: the moment a transcript binds
        (even provisionally) or a single hook lands, the predicate goes false and
        this path stands down for the rest of the session's life. The transition
        itself is filed at PTY priority, so ordered evidence arriving in the same
        turn takes ownership without needing to force.
        """
        record = session.record
        action = watchdog_decision(
            record.state,
            stalled_seconds=now - session.last_state_change_ts,
            tail_verdict=None,
            pty_state=self._pty_tail_state(session),
            awaiting_reason=record.awaiting_reason,
            unwitnessed=True,
            unwitnessed_turn_armed=bool(
                session.observation_state.get("unwitnessed_turn_armed")
            ),
        )
        if action not in {"begin_pty_turn", "end_pty_turn"}:
            return False
        await apply_watchdog_recovery(
            session,
            self.events,
            action,
            stalled_seconds=now - session.last_state_change_ts,
            tail_verdict=None,
        )
        return True

    def _hook_spool_path(self, session_id: str) -> Path | None:
        # getattr keeps lifecycle paths callable from the partially-constructed
        # managers the unit tests build; a real manager always sets the field.
        spool_dir: Path | None = getattr(self, "hook_spool_dir", None)
        if spool_dir is None:
            return None
        return spool_dir / f"{session_id}.jsonl"

    def discard_hook_spool(self, session_id: str) -> None:
        """Drop any spooled hooks belonging to a run that is over.

        The spool file is keyed by mux session id, which is stable across
        demote/re-promote — so a terminal event left behind by one agent run would
        otherwise replay into the next one and force it idle mid-turn.
        """
        path = self._hook_spool_path(session_id)
        if path is None:
            return
        path.unlink(missing_ok=True)
        _consuming_spool_path(path).unlink(missing_ok=True)

    async def _drain_hook_spool(self, session: Session) -> None:
        """Replay hook events the shim spooled to disk after a failed POST.

        Stop/SessionEnd delivered over a lost HTTP request would otherwise vanish;
        the shim appends them here as a durable fallback that the daemon consumes.

        A spooled event is only replayed when it can still be true: it must be
        newer than the current agent run's start (a leftover from a previous run
        is not evidence about this one) and newer than the current turn's start (a
        Stop from turn N must not close turn N+1).
        """
        path = self._hook_spool_path(session.record.id)
        if path is None:
            return
        from .observation import apply_hook_observation, foreign_conversation_hook_id

        record = session.record
        if record.state in TERMINAL_STATES:
            # The process is gone; nothing spooled can be about a live turn, and
            # replaying a SessionEnd here used to resurrect the session to idle.
            self.discard_hook_spool(record.id)
            return
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
        # Consume by rename: a shim append landing after this snapshot goes to a
        # fresh file instead of being truncated away by the rewrite that used to
        # follow the read.
        consuming = _consuming_spool_path(path)
        try:
            if consuming.exists():
                # A previous drain died mid-replay: fold the live spool onto the
                # leftover so nothing is lost, then consume the combined file.
                consuming.write_bytes(consuming.read_bytes() + path.read_bytes())
                path.unlink(missing_ok=True)
            else:
                # On Windows this fails outright while the shim holds the file
                # open, which is the desired outcome: retry on the next pass
                # rather than truncate an append away.
                path.replace(consuming)
            data = consuming.read_bytes()
        except OSError:
            return
        consumed = data.rfind(b"\n") + 1
        remainder = data[consumed:]
        try:
            if remainder:
                # A trailing partial line goes back to the live spool so the
                # shim's completion of it is not orphaned.
                tail = path.read_bytes() if path.exists() else b""
                path.write_bytes(remainder + tail)
            consuming.unlink(missing_ok=True)
        except OSError:
            pass
        if not consumed:
            return
        floor = self._hook_spool_floor(session)
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
            raw_spooled_at = item.get("spooled_at")
            spooled_at = (
                float(raw_spooled_at) if isinstance(raw_spooled_at, int | float) else None
            )
            if spooled_at is not None and spooled_at < floor:
                session.state_transitions.append(
                    {
                        "ts": time.time(),
                        "kind": "hook_spool_discarded",
                        "event": event_type,
                        "spooled_at": spooled_at,
                        "floor": floor,
                    }
                )
                continue
            # Same identity discipline as the live ingress: the pane's own spawn
            # conversation heals a stolen binding, and a foreign conversation's
            # spooled event neither refreshes liveness nor drives state (the
            # observation layer ledgers and drops it).
            await self.maybe_heal_from_own_conversation_hook(session, payload)
            if foreign_conversation_hook_id(session, payload) is None:
                session.last_hook_ts = time.time()
                session.state_transitions.append(
                    {"ts": time.time(), "kind": "hook_spool_replay", "event": event_type}
                )
            try:
                await apply_hook_observation(session, event_type, payload, self.events)
            except Exception:
                log.exception("failed to replay spooled hook %s", event_type)

    @staticmethod
    def _hook_spool_floor(session: Session) -> float:
        """Oldest spool timestamp that can still describe the session's live turn."""
        record = session.record
        floor = float(record.agent_run_started_at or record.created_at)
        observation_state = getattr(session, "observation_state", None)
        if isinstance(observation_state, dict):
            turn_started = observation_state.get("turn_started_at")
            if isinstance(turn_started, int | float):
                floor = max(floor, float(turn_started))
        return floor

    @staticmethod
    def _pty_tail_state(session: Session) -> PtyTailState:
        """Classify what this session's CLI is showing right now."""
        scrollback = getattr(session, "scrollback", None)
        if scrollback is None:
            return "unknown"
        try:
            tail = scrollback.tail_bytes(SCREEN_TAIL_BYTES).decode("utf-8", "replace")
        except (OSError, ValueError):
            return "unknown"
        osc_signals = getattr(session, "osc_signals", None)
        return cast(
            PtyTailState,
            pty_tail_explain(
                tail,
                backend=session.record.backend,
                osc_title=getattr(osc_signals, "title", None),
                osc_progress=getattr(osc_signals, "progress", None),
            )["outcome"],
        )

    def _pty_appears_idle(self, session: Session) -> bool:
        """Apply the shared idle-prompt heuristic to this session's scrollback tail."""
        return self._pty_tail_state(session) == "idle"

    async def _fanout(self, session: Session) -> None:
        # unsupervised-loop-ok: scoped to one session, not the daemon. It ends with
        # its PTY, so it has no place in a registry keyed by singleton loop name.
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
            session.screen.feed(chunk)
            session.bracketed_paste.feed(chunk)
            session.osc_signals.feed(chunk)
            prompt_uris = session.osc7.feed(chunk)
            if prompt_uris and "first_prompt" not in session.record.startup_timing_ms:
                session.record.startup_timing_ms["first_prompt"] = round(
                    (time.perf_counter() - session.startup_started_at) * 1000, 1
                )
                timing_changed = True
            for uri in prompt_uris:
                self._queue_runtime_cwd(session, uri)
            if prompt_uris and session.record.backend in AGENT_BACKENDS:
                self._queue_agent_exit_check(session)
            session.scrollback.append(chunk)
            session.publish_output(chunk)
            if session.record.backend in AGENT_BACKENDS and session.record.state == "starting":
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
                    # This is the second producer of `state_changed`, and it was
                    # the quieter one: it never stamped `proof`, so an inferred
                    # startup idle reached consumers looking exactly as solid as a
                    # hook-proven turn end. Keep the two payloads in step.
                    idle_reason=None,
                    standing=standing_activity_kinds(session),
                    proof=transition_proof("pty", True),
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
            if backend not in AGENT_BACKENDS:
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

    async def _accept_runtime_cwd(
        self,
        session: Session,
        path: Path,
        *,
        source: str = "osc7",
        channel: str = "pty",
        debounce: float = 1.25,
    ) -> None:
        """Adopt a reported working directory as this session's live cwd.

        The debounce exists for OSC 7, which a shell emits on every prompt and
        therefore several times during one `cd`. A hook reports a cwd the CLI has
        already settled on, so it waits for nothing.

        Deliberately does not touch `project_id`/`repository_id`. A worktree is the
        same Project as the checkout it was cut from, and a session that steps into
        one must not vanish from its group in the sidebar.
        """
        try:
            if debounce:
                await asyncio.sleep(debounce)
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
            session.record.runtime_cwd_source = source
            session.record.runtime_cwd_updated_at = time.time()
            session.record.runtime_project_scope_id = project.id if known else None
            session.record.git = GitState()
            session.publish_update()
            await self.events.emit(
                "runtime_cwd_changed",
                session_id=session.record.id,
                source=channel,
                cwd=value,
                project_scope_id=session.record.runtime_project_scope_id,
                dropped=session.cwd_telemetry_dropped,
            )
        except asyncio.CancelledError:
            raise

    def note_hook_cwd(self, session: Session, payload: dict[str, Any]) -> None:
        """Take the working directory the CLI reports in its own hooks as live cwd.

        An agent pane never produced cwd telemetry before this. OSC 7 comes from a
        shell drawing its prompt, and a CLI holding the terminal draws no prompt, so
        `runtime_cwd_live` stayed False for the entire life of every Claude session
        and `git_cwd` fell back to the spawn directory. A session working inside a
        native worktree therefore had its Git rail, diff, and branch chip reporting
        the primary checkout - a different branch and a different set of changes than
        the one the agent was editing.

        Rate limiting and validation are the OSC 7 path's, unchanged: a directory
        that does not exist is ignored, and a session that changes cwd more than
        twelve times a minute has its telemetry dropped rather than trusted.
        """
        record = session.record
        if session.stopping or session.stop_event.is_set():
            return
        raw = str(payload.get("cwd") or "").strip()
        if not raw:
            return
        # Compared before scheduling, not inside the task: this fires on every hook
        # of every turn, and the value is the same one all day.
        if record.runtime_cwd_live and record.runtime_cwd == raw:
            return
        if session.cwd_debounce_task and not session.cwd_debounce_task.done():
            session.cwd_debounce_task.cancel()
        task = asyncio.create_task(
            self._accept_runtime_cwd(
                session, Path(raw), source="hook", channel="hook", debounce=0.0
            ),
            name=f"cwd-hook-{record.id}",
        )
        session.cwd_debounce_task = task
        session.tasks.add(task)

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
        started_at = time.time()
        session.record.agent_run_started_at = started_at
        session.record.agent_loaded_at = started_at
        session.record.run_cwd = str(cwd.resolve())
        session.record.run_project_scope_id = project.id
        session.record.run_repo_group_id = project.repo_group_id
        session.record.tokens_in = 0
        session.record.tokens_out = 0
        session.record.tokens_cache_read = 0
        session.record.tokens_cache_write = 0
        session.record.cost_usd = 0.0
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
        # A dead process holds no standing engagements; publish_exit below
        # carries the cleared set in its final snapshot.
        clear_all_standing_activity(session, evidence=f"process_exit:{final_reason}")
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
        self.discard_hook_spool(session.record.id)
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
        timeline_store = getattr(self, "status_timeline", None)
        if timeline_store is not None:
            # Final drain now rather than at the next batch window: an ended
            # session's terminal entries are exactly what a post-mortem needs,
            # and the Session object is about to lose its last strong holder.
            try:
                await timeline_store.flush_session(session)
            except Exception:
                log.exception(
                    "could not flush status timeline for ended session %s",
                    session.record.id,
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
        preserved = [
            session
            for session in self.sessions.values()
            if intent == "detach" and isinstance(session.pty, RemotePtyHost)
        ]
        # Stop the per-session tickers of preserved sessions *first*. Once the
        # client disconnects, `RemotePtyHost.isalive()` is False by definition, so
        # one more tick would call `_mark_ended` on every live remote session and
        # persist a spurious exit for an agent that is still running.
        for session in preserved:
            session.stopping = True
            for task in tuple(session.tasks):
                if task.get_name().startswith("ticker-"):
                    task.cancel()
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
