"""Claude's CLI-published per-process session state (`~/.claude/sessions/<pid>.json`).

The CLI writes one JSON file per running process — verified live 2026-07-31 on
2.1.220: ``{sessionId, cwd, pid, procStart, kind, name, status, statusUpdatedAt,
updatedAt, version}``. Observed ``status`` values are ``busy``, ``idle``, and
``waiting`` (measured 2026-08-01: the file reads ``waiting`` for the duration of
a permission dialog, flipping ``busy`` → ``waiting`` → ``busy`` around it).
This is the `cli-state` layer of the detection ladder (status-detection.md):
hook-free and CLI-authoritative. It feeds counters and the ledger, and its
``waiting`` value conservatively vetoes raw PTY evidence that would otherwise
hide an approval, while ``busy`` vetoes a transient idle repaint during parallel
tool completion. It does not initiate a SessionState transition by itself.

Three measured caveats shape what this module does *not* do:

- ``updatedAt`` is a status-change timestamp, not a heartbeat. A legitimately
  busy session's file was observed 51 minutes stale mid-turn, so "file stale
  while working" is NOT evidence of a stuck session and no staleness counter
  exists here.
- ``pid``/``procStart`` name the CLI process, not the mux session; matching is
  by conversation id (``sessionId`` vs ``native_session_id``), which the daemon
  itself assigned at spawn. A file in a session's working directory bound to a
  conversation no live session owns is a nested child CLI observed
  deterministically — the signal the 2026-07-31 incident had to infer from hook
  ``source`` fields.
- ``parkedJobId`` (added by 2.1.227) is the CLI's report that this pane's
  conversation has moved into a background job, and it is the *only* report of
  that move: the background CLI runs under a shared ``claude daemon run`` tree,
  so it neither carries this pane's hook credentials nor speaks for this pane's
  conversation, and no hook ever reaches the pane. Measured 2026-08-10, a pane
  whose conversation was parked this way sat nameless and displayed idle for 42
  minutes while its background job ran a full task. Following the move is
  therefore a rollover on the CLI's own authority, not the transcript-switch
  heuristic a Claude pane forbids (``adapters/base.py``).

The same directory answers one question that is not a detection question at all:
which conversations a live CLI process already holds (``conversation_holders``).
The CLI opens a conversation once, so that set is exactly the set of resumes that
would exit instead of starting rather than fail to start, and it is read at the
moment of the resume rather than sampled on the poll.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .harness import Backend, publishes_cli_state
from .path_identity import identity_key
from .status_timeline import note_layer_reading

try:
    import psutil
except ImportError:  # pragma: no cover - diagnostics cover an unsynchronized dev venv
    psutil = None

log = logging.getLogger(__name__)

# A disagreement is counted only when both sides are settled: the CLI's status
# flip and mux's own transition race each other at boundaries, and a comparison
# inside that window measures the race, not a defect.
CLI_STATE_SETTLE_SECONDS = 10.0
# A move the pane refuses to adopt is retried a bounded number of times and then
# left alone. Retrying forever is not free: `roll_agent_conversation` stops and
# restarts the observer on every attempt, so an unadoptable move would thrash
# observation once per poll for the pane's whole life.
PARKED_MOVE_ATTEMPTS = 3


def _publishes_cli_state(backend: Backend) -> bool:
    """Whether this layer has a state file to read for ``backend``.

    Declared on the descriptor (`publishes_cli_state`) rather than answered here,
    because the PTY classifier asks the same question when deciding whether a
    CLI-state reading may veto or override a screen reading, and answering it in two
    places is how every non-Claude harness silently inherited Claude's semantics.

    opencode is the one harness whose answer is not obvious: it does publish live
    status, but over its server's SSE stream rather than a state file on disk, which
    is not what this layer reads. Its descriptor records that.
    """
    return publishes_cli_state(backend)


@dataclass(frozen=True, slots=True)
class ParkedConversation:
    """A background job holding a pane's conversation (`~/.claude/jobs/<id>/state.json`).

    ``session_id`` is the conversation the job runs and ``transcript`` is the file
    it writes; the CLI publishes both, so neither is guessed from cwd.
    """

    job_id: str
    session_id: str
    cwd: str
    state: str
    transcript: str


@dataclass(frozen=True, slots=True)
class ParkedMove:
    """A pane whose conversation must be rolled onto its background job."""

    session_id: str
    native_session_id: str
    transcript: str
    job_id: str
    job_state: str


@dataclass(frozen=True, slots=True)
class ConversationHolder:
    """A live CLI process that already holds a conversation.

    The CLI refuses to open one conversation twice, so a holder is the reason a
    resume of that conversation would exit instead of starting: measured
    2026-08-14 on 2.1.232, ``--resume`` of a conversation running as a background
    agent printed "Session <id> is currently running as a background agent (bg).
    Use `claude agents` to find and attach to it, or add --fork-session to branch
    off a copy" and exited 1 about 1.5 s later.

    A holder is only ever reported for a *proven* process: the state file names a
    pid, and that pid must still be running with the start time the file records.
    An unverifiable pid yields no holder, because the cost of the two errors is
    not symmetric — a missed holder degrades to the ordinary spawn failure the
    settle check reports, while a phantom one makes a resumable conversation
    permanently unresumable.
    """

    conversation_id: str
    # The CLI's own vocabulary: `bg` for a background agent, `interactive` for a
    # pane. Kept verbatim rather than translated, because the remedy differs and
    # the message quotes it back to the operator.
    kind: str
    pid: int
    job_id: str
    name: str
    cwd: str

    @property
    def is_background_agent(self) -> bool:
        return self.kind == "bg"

    def describe(self) -> str:
        """One sentence naming the holder and the way to reach it."""
        label = f'"{self.name}"' if self.name else self.conversation_id
        if self.is_background_agent:
            return (
                f"conversation {self.conversation_id} is running as a Claude background "
                f"agent ({label}, pid {self.pid}); attach to it with `claude agents` in "
                f"{self.cwd or 'its working directory'}, or branch a copy instead of "
                f"resuming it"
            )
        return (
            f"conversation {self.conversation_id} is open in another Claude process "
            f"({label}, pid {self.pid}); close that one or branch a copy"
        )


# Resolves a pid to the epoch second it started, or None when no such process is
# running. Injected so the liveness rule can be tested without real processes.
ProcessStart = Callable[[int], float | None]


def _psutil_process_start(pid: int) -> float | None:
    if psutil is None or pid <= 0:
        return None
    try:
        return float(psutil.Process(pid).create_time())
    except Exception:  # noqa: BLE001 - any failure to prove liveness means "unknown"
        return None


# The CLI stamps `startedAt` when it writes the file, which is *after* the process
# it describes began: measured 2026-08-14 across 13 live CLIs, the file ran 0.42 s
# to 1.31 s behind the OS's creation time, and a cold start can be slower still. So
# the window is asymmetric — generous in the direction the lag actually goes, tight
# in the direction that would mean the pid names something else. It is a pid-reuse
# guard, not a clock comparison: reuse requires the first process to have exited,
# which puts the replacement's creation time far outside either bound.
_PROCESS_START_MAX_LAG_SECONDS = 120.0
_PROCESS_START_MAX_LEAD_SECONDS = 5.0


@dataclass(slots=True)
class CliSessionState:
    path: str
    session_id: str
    cwd: str
    pid: int
    proc_start: str
    kind: str
    status: str
    status_updated_at: float
    updated_at: float
    version: str
    mtime: float
    # The CLI's own title for the process, its background-job id when it has one,
    # and when the process started. Read for ownership answers rather than for
    # status: `started_at` is what proves the recorded pid is still that process.
    name: str = ""
    job_id: str = ""
    started_at: float = 0.0
    parked_job_id: str = ""
    parked: ParkedConversation | None = None

    def snapshot(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            # The CLI's *live* working directory, which the spawn cwd stops
            # describing the moment the agent enters a worktree. Claude derives a
            # transcript's directory from cwd, so this is what re-finds a
            # conversation whose file moved out from under the observer
            # (`SessionManager._relocated_transcript_candidate`).
            "cwd": self.cwd,
            "pid": self.pid,
            "proc_start": self.proc_start,
            "kind": self.kind,
            "status": self.status,
            "status_updated_at": self.status_updated_at,
            "updated_at": self.updated_at,
            "version": self.version,
            "parked_job_id": self.parked_job_id,
            "parked_session_id": self.parked.session_id if self.parked else "",
        }


def _parse_state(path: Path, mtime: float) -> CliSessionState | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    session_id = str(data.get("sessionId") or "")
    if not session_id:
        return None
    try:
        pid = int(data.get("pid") or 0)
    except (TypeError, ValueError):
        pid = 0
    def _millis(key: str) -> float:
        try:
            return float(data.get(key) or 0.0) / 1000.0
        except (TypeError, ValueError):
            return 0.0
    return CliSessionState(
        path=str(path),
        session_id=session_id,
        cwd=str(data.get("cwd") or ""),
        pid=pid,
        proc_start=str(data.get("procStart") or ""),
        kind=str(data.get("kind") or ""),
        status=str(data.get("status") or ""),
        status_updated_at=_millis("statusUpdatedAt"),
        updated_at=_millis("updatedAt"),
        version=str(data.get("version") or ""),
        mtime=mtime,
        name=str(data.get("name") or ""),
        job_id=str(data.get("jobId") or ""),
        started_at=_millis("startedAt"),
        parked_job_id=str(data.get("parkedJobId") or ""),
    )


def _parse_job(root: Path, job_id: str) -> ParkedConversation | None:
    try:
        data = json.loads((root / job_id / "state.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    session_id = str(data.get("sessionId") or "")
    if not session_id:
        return None
    return ParkedConversation(
        job_id=job_id,
        session_id=session_id,
        cwd=str(data.get("cwd") or ""),
        state=str(data.get("state") or ""),
        transcript=str(data.get("linkScanPath") or ""),
    )


class CliStateMonitor:
    """Stat-then-parse poller over the side-state directory.

    ``poll`` does the file I/O (call it off the event loop); ``observe`` applies
    the results to live sessions (call it on the loop — it mutates session
    counters and ledgers). ``conversation_holders`` answers a different question
    from the same directory — who currently owns a conversation — and shares
    neither the cache nor the cadence, so it is safe to call from either side.
    """

    def __init__(
        self,
        root: Path,
        jobs_root: Path | None = None,
        process_start: ProcessStart | None = None,
    ) -> None:
        self.root = root
        # Sibling of the per-process state directory in the CLI's data home.
        self.jobs_root = jobs_root if jobs_root is not None else root.parent / "jobs"
        self._process_start = process_start or _psutil_process_start
        self._cache: dict[str, tuple[float, int, CliSessionState | None]] = {}
        # One count per standing fact, not per 5 s poll: the key that was last
        # counted per session, for disagreements and for nested children.
        self._counted_disagreements: dict[str, tuple[str, str, float]] = {}
        self._nested_counted: dict[str, set[str]] = {}
        # Attempts already yielded per (mux session, backgrounded conversation).
        self._parked_attempts: dict[tuple[str, str], int] = {}
        self._parked_noted: dict[str, str] = {}

    def poll(self) -> list[CliSessionState]:
        try:
            paths = list(self.root.glob("*.json"))
        except OSError:
            return []
        seen: set[str] = set()
        states: list[CliSessionState] = []
        # Jobs are resolved once per poll rather than once per state: several
        # panes can point at one job, and this is the threaded half of the loop.
        jobs: dict[str, ParkedConversation | None] = {}
        for path in paths:
            key = str(path)
            seen.add(key)
            try:
                stat = path.stat()
            except OSError:
                continue
            cached = self._cache.get(key)
            if cached is not None and cached[0] == stat.st_mtime and cached[1] == stat.st_size:
                state = cached[2]
            else:
                state = _parse_state(path, stat.st_mtime)
                self._cache[key] = (stat.st_mtime, stat.st_size, state)
            if state is None:
                continue
            if state.parked_job_id:
                # Never cached with the state file: the pane's file stops changing
                # the moment its conversation is parked, while the job it points
                # at is the thing still moving.
                if state.parked_job_id not in jobs:
                    jobs[state.parked_job_id] = _parse_job(self.jobs_root, state.parked_job_id)
                state.parked = jobs[state.parked_job_id]
            states.append(state)
        for key in tuple(self._cache):
            if key not in seen:
                del self._cache[key]
        return states

    def _holds(self, state: CliSessionState) -> bool:
        """Whether a live process stands behind this state file.

        The CLI removes its file on a clean exit, so an unmatched file is either a
        crash leftover or a pid the OS has since reused. Both would name a holder
        that is not there, and a phantom holder is the one failure this check
        exists to prevent, so nothing short of the recorded process still running
        counts.
        """
        started = self._process_start(state.pid)
        if started is None:
            return False
        if not state.started_at:
            # An older CLI that publishes no start time: the pid is alive and the
            # file is its own, which is as much as that version can prove.
            return True
        lag = state.started_at - started
        return -_PROCESS_START_MAX_LEAD_SECONDS <= lag <= _PROCESS_START_MAX_LAG_SECONDS

    def conversation_holders(self) -> dict[str, ConversationHolder]:
        """Every conversation a live CLI process holds right now, by conversation id.

        Read fresh on every call rather than from ``poll``'s cache: this answers a
        question asked at the moment an operator resumes something, where a five
        second old answer is a wrong answer, and the directory is a dozen small
        files.
        """
        holders: dict[str, ConversationHolder] = {}
        try:
            paths = sorted(self.root.glob("*.json"))
        except OSError:
            return holders
        for path in paths:
            try:
                state = _parse_state(path, path.stat().st_mtime)
            except OSError:
                continue
            if state is None or state.session_id in holders:
                continue
            if not self._holds(state):
                continue
            holders[state.session_id] = ConversationHolder(
                conversation_id=state.session_id,
                kind=state.kind,
                pid=state.pid,
                job_id=state.job_id,
                name=state.name,
                cwd=state.cwd,
            )
        return holders

    def conversation_holder(self, backend: Backend, native_id: str) -> ConversationHolder | None:
        """The live process holding ``native_id``, or None if it is free to open."""
        if not native_id or not _publishes_cli_state(backend):
            return None
        return self.conversation_holders().get(native_id)

    def observe(
        self, states: list[CliSessionState], sessions: Iterable[Any], now: float | None = None
    ) -> list[ParkedMove]:
        """Apply the poll to live sessions and report conversations to follow.

        The returned moves are the caller's work, not this method's: adopting one
        is an async rollover that stops and restarts the observer, and this runs
        on the loop purely to mutate counters and ledgers.
        """
        now = time.time() if now is None else now
        moves: list[ParkedMove] = []
        by_conversation = {state.session_id: state for state in states}
        live: list[Any] = [
            session
            for session in sessions
            if _publishes_cli_state(session.record.backend)
            and session.record.state not in {"exited", "crashed"}
        ]
        live_conversations = {session.record.native_session_id for session in live}
        live_ids = {session.record.id for session in live}
        cwd_owners: dict[str, list[Any]] = {}
        for session in live:
            cwd = _normalized_cwd(session.record.run_cwd or session.record.cwd)
            cwd_owners.setdefault(cwd, []).append(session)
        for session in live:
            own = by_conversation.get(session.record.native_session_id)
            session.cli_state = own.snapshot() if own is not None else None
            # Layer reading, ledgered on change only: the file's `status` value
            # flipping (or the file appearing/vanishing) is the whole signal —
            # its `updatedAt` is a status-change stamp, not a heartbeat, so age
            # is deliberately never read here.
            note_layer_reading(
                session,
                "cli_state",
                own.status if own is not None else "absent",
                now=now,
                detail=(
                    {"status_updated_at": own.status_updated_at}
                    if own is not None
                    else None
                ),
            )
            if own is not None:
                self._corroborate_status(session, own, now)
                move = self._parked_move(session, own, now)
                if move is not None:
                    moves.append(move)
        for state in states:
            if state.session_id in live_conversations or state.session_id in live_ids:
                continue
            owners = cwd_owners.get(_normalized_cwd(state.cwd)) or []
            if len(owners) != 1:
                # Two live sessions in one cwd make the attribution ambiguous;
                # a wrong nested-child count is worse than a missed one.
                continue
            session = owners[0]
            if (session.record.backend, state.session_id) in getattr(
                session, "ignored_detection_runs", ()
            ):
                # A conversation this pane itself retired — the interactive CLI's
                # own file after its conversation was parked into a background
                # job — is the pane's past, not a child of it.
                continue
            started = session.record.agent_run_started_at or session.record.created_at
            if state.updated_at and state.updated_at < started:
                # A leftover file from before this run is not this run's child.
                continue
            self._note_nested_child(session, state, now)
        return moves

    def _corroborate_status(self, session: Any, state: CliSessionState, now: float) -> None:
        """Count a settled contradiction between the CLI's status and mux's.

        Only the two clear-cut cases count: the CLI saying ``busy`` while mux
        shows ``idle``, and the CLI saying ``idle`` while mux shows ``working``.
        ``awaiting``/``starting``/``running`` involve dialogs and lifecycle the
        comparison deliberately stays out of. ``waiting`` (the CLI's own
        dialog status) therefore counts as neither agreement nor disagreement
        here. It is recorded as a ``layer_reading`` and separately used by the
        effective PTY classifier as a conservative approval veto. It never
        initiates a transition by itself.
        """
        record = session.record
        mismatch = (state.status == "busy" and record.state == "idle") or (
            state.status == "idle" and record.state == "working"
        )
        if not mismatch:
            self._counted_disagreements.pop(record.id, None)
            return
        if (
            now - state.status_updated_at < CLI_STATE_SETTLE_SECONDS
            or now - session.last_state_change_ts < CLI_STATE_SETTLE_SECONDS
        ):
            return
        key = (state.status, record.state, state.status_updated_at)
        if self._counted_disagreements.get(record.id) == key:
            return
        self._counted_disagreements[record.id] = key
        counters = getattr(session, "status_health_counters", None)
        if counters is not None:
            counters["cli_state_disagrees"] = counters.get("cli_state_disagrees", 0) + 1
        ledger = getattr(session, "state_transitions", None)
        if ledger is not None:
            ledger.append(
                {
                    "ts": now,
                    "kind": "cli_state",
                    "action": "status_disagrees",
                    "cli_status": state.status,
                    "mux_state": record.state,
                    "status_updated_at": state.status_updated_at,
                }
            )

    def _parked_move(
        self, session: Any, state: CliSessionState, now: float
    ) -> ParkedMove | None:
        """The conversation this pane must follow into a background job, if any.

        Every guard here answers "is this pane's own conversation demonstrably
        somewhere else now":

        - the state file is the pane's own (the caller matched it by conversation
          id) and belongs to an ``interactive`` CLI, so a background CLI's file
          can never move the pane that requested it;
        - the job resolves and names a *different* conversation, which is also
          what makes this idempotent — once the rollover lands, the record's
          conversation equals the job's and no further move is produced;
        - the job stands in the pane's own working directory, so a job belonging
          to another Project is never adopted.
        """
        parked = state.parked
        record = session.record
        if parked is None or state.kind != "interactive":
            return None
        if not parked.session_id or parked.session_id == record.native_session_id:
            return None
        if _normalized_cwd(parked.cwd) != _normalized_cwd(record.run_cwd or record.cwd):
            return None
        key = (record.id, parked.session_id)
        attempts = self._parked_attempts.get(key, 0)
        if attempts >= PARKED_MOVE_ATTEMPTS:
            return None
        self._parked_attempts[key] = attempts + 1
        if self._parked_noted.get(record.id) != parked.session_id:
            self._parked_noted[record.id] = parked.session_id
            ledger = getattr(session, "state_transitions", None)
            if ledger is not None:
                ledger.append(
                    {
                        "ts": now,
                        "kind": "cli_state",
                        "action": "conversation_parked",
                        "native_session_id": parked.session_id,
                        "job_id": parked.job_id,
                        "job_state": parked.state,
                        "pid": state.pid,
                    }
                )
            log.info(
                "cli-state: session %s conversation parked into background job %s "
                "(conversation %s, job state %s, pid %s)",
                record.id,
                parked.job_id,
                parked.session_id,
                parked.state or "unknown",
                state.pid,
            )
        elif attempts + 1 >= PARKED_MOVE_ATTEMPTS:
            log.warning(
                "cli-state: session %s did not adopt backgrounded conversation %s "
                "after %d attempts; leaving it bound to %s",
                record.id,
                parked.session_id,
                PARKED_MOVE_ATTEMPTS,
                record.native_session_id,
            )
        return ParkedMove(
            session_id=record.id,
            native_session_id=parked.session_id,
            transcript=parked.transcript,
            job_id=parked.job_id,
            job_state=parked.state,
        )

    def _note_nested_child(self, session: Any, state: CliSessionState, now: float) -> None:
        counted = self._nested_counted.setdefault(session.record.id, set())
        if state.session_id in counted:
            return
        counted.add(state.session_id)
        counters = getattr(session, "status_health_counters", None)
        if counters is not None:
            counters["nested_children_observed"] = (
                counters.get("nested_children_observed", 0) + 1
            )
        ledger = getattr(session, "state_transitions", None)
        if ledger is not None:
            ledger.append(
                {
                    "ts": now,
                    "kind": "cli_state",
                    "action": "nested_child_observed",
                    "native_session_id": state.session_id,
                    "pid": state.pid,
                    "cli_status": state.status,
                }
            )
        log.info(
            "cli-state: nested child CLI %s (pid %s) observed under session %s",
            state.session_id,
            state.pid,
            session.record.id,
        )


def _normalized_cwd(cwd: str) -> str:
    """A grouping key two spellings of one directory share on this host.

    `identity_key` rather than `casefold`: folding case on a case-sensitive
    filesystem merges two different working directories into one key, which here
    would attribute one CLI's state to a session standing somewhere else.
    """
    return identity_key(cwd)
