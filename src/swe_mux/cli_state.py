"""Claude's CLI-published per-process session state (`~/.claude/sessions/<pid>.json`).

The CLI writes one JSON file per running process — verified live 2026-07-31 on
2.1.220: ``{sessionId, cwd, pid, procStart, kind, name, status, statusUpdatedAt,
updatedAt, version}`` with observed ``status`` values ``busy`` and ``idle``.
This is the `cli-state` layer of the detection ladder (status-detection.md):
hook-free, CLI-authoritative, and — in this phase — **corroboration only**. It
never drives a SessionState transition; it feeds counters and the ledger so a
release of disagreement telemetry exists before any promotion is considered.

Two measured caveats shape what this module does *not* do:

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
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .status_timeline import note_layer_reading

log = logging.getLogger(__name__)

# A disagreement is counted only when both sides are settled: the CLI's status
# flip and mux's own transition race each other at boundaries, and a comparison
# inside that window measures the race, not a defect.
CLI_STATE_SETTLE_SECONDS = 10.0


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

    def snapshot(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "pid": self.pid,
            "proc_start": self.proc_start,
            "kind": self.kind,
            "status": self.status,
            "status_updated_at": self.status_updated_at,
            "updated_at": self.updated_at,
            "version": self.version,
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
    )


class CliStateMonitor:
    """Stat-then-parse poller over the side-state directory.

    ``poll`` does the file I/O (call it off the event loop); ``observe`` applies
    the results to live sessions (call it on the loop — it mutates session
    counters and ledgers).
    """

    def __init__(self, root: Path) -> None:
        self.root = root
        self._cache: dict[str, tuple[float, int, CliSessionState | None]] = {}
        # One count per standing fact, not per 5 s poll: the key that was last
        # counted per session, for disagreements and for nested children.
        self._counted_disagreements: dict[str, tuple[str, str, float]] = {}
        self._nested_counted: dict[str, set[str]] = {}

    def poll(self) -> list[CliSessionState]:
        try:
            paths = list(self.root.glob("*.json"))
        except OSError:
            return []
        seen: set[str] = set()
        states: list[CliSessionState] = []
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
            if state is not None:
                states.append(state)
        for key in tuple(self._cache):
            if key not in seen:
                del self._cache[key]
        return states

    def observe(
        self, states: list[CliSessionState], sessions: Iterable[Any], now: float | None = None
    ) -> None:
        now = time.time() if now is None else now
        by_conversation = {state.session_id: state for state in states}
        live: list[Any] = [
            session
            for session in sessions
            if session.record.backend == "claude"
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
        for state in states:
            if state.session_id in live_conversations or state.session_id in live_ids:
                continue
            owners = cwd_owners.get(_normalized_cwd(state.cwd)) or []
            if len(owners) != 1:
                # Two live sessions in one cwd make the attribution ambiguous;
                # a wrong nested-child count is worse than a missed one.
                continue
            session = owners[0]
            started = session.record.agent_run_started_at or session.record.created_at
            if state.updated_at and state.updated_at < started:
                # A leftover file from before this run is not this run's child.
                continue
            self._note_nested_child(session, state, now)

    def _corroborate_status(self, session: Any, state: CliSessionState, now: float) -> None:
        """Count a settled contradiction between the CLI's status and mux's.

        Only the two clear-cut cases count: the CLI saying ``busy`` while mux
        shows ``idle``, and the CLI saying ``idle`` while mux shows ``working``.
        ``awaiting``/``starting``/``running`` involve dialogs and lifecycle the
        file's two-value enum cannot express.
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
    try:
        return str(Path(cwd).resolve()).casefold()
    except (OSError, ValueError):
        return cwd.casefold()
