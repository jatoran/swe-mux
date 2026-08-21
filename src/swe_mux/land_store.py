"""Phase 14: durable rows for the land queue and its per-step audit trail.

**Machine-local by construction**, for the reason `schedule_store.py` already states
about schedules: a queue committed to a repository would arm itself in every clone and
every worktree of it. The Project *opt-in* (`land_queue`) stays portable and is inert
on its own - a clone inherits permission to land and has no requests.

Structure follows `ScheduleStore`: one dedicated worker thread, statements executed in
submission order, and state transitions written as conditional UPDATEs so a stale
caller loses a race rather than corrupting the row.

Two indexes carry the design:

- ``land_requests_active`` is partial over the live states and unique on
  ``(project_root, branch)``. It is what makes a request idempotent: an agent that asks
  twice, or asks again after a daemon restart, cannot create a second pipeline for one
  branch. Enqueue *is* the claim.
- ``land_requests_inflight`` is partial over the running states and unique on
  ``project_root`` alone. Serialisation per trunk is therefore a property of the
  schema, not of the worker's care: two workers cannot both mark a step running against
  one primary checkout even if both believe they should.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
import uuid
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from .sqlite_store import (
    connect_or_quarantine,
    database_operation_lock,
    run_sqlite_operation,
    write_schema_version,
)

LAND_SCHEMA_VERSION = 4

#: States a request can occupy. `queued` is accepted-not-started; `waiting` is a
#: precondition hold that a human can read; the three step states are in-flight; the
#: last four are terminal.
LAND_STATES = (
    "queued",
    "waiting",
    "reconciling",
    "verifying",
    "landing",
    "landed",
    "already_landed",
    "handed_back",
    "refused",
    "cancelled",
)

#: A request the queue still owns. The partial unique index over these is what makes
#: a duplicate request impossible rather than merely unlikely.
ACTIVE_STATES = ("queued", "waiting", "reconciling", "verifying", "landing")

#: A request with a mutation in flight. One per trunk, enforced by the schema.
INFLIGHT_STATES = ("reconciling", "verifying", "landing")

TERMINAL_STATES = ("landed", "already_landed", "handed_back", "refused", "cancelled")

LAND_SCHEMA = """
CREATE TABLE IF NOT EXISTS land_requests(
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  project_root TEXT NOT NULL,
  worktree_root TEXT NOT NULL,
  branch TEXT NOT NULL,
  requested_oid TEXT NOT NULL DEFAULT '',
  trunk_ref TEXT NOT NULL DEFAULT '',
  origin TEXT NOT NULL DEFAULT 'operator',
  origin_session_id TEXT NOT NULL DEFAULT '',
  origin_run_id TEXT NOT NULL DEFAULT '',
  correlation_id TEXT NOT NULL DEFAULT '',
  state TEXT NOT NULL DEFAULT 'queued',
  reason TEXT NOT NULL DEFAULT '',
  detail_json TEXT NOT NULL DEFAULT '{}',
  waiting_since REAL,
  reconciled_oid TEXT NOT NULL DEFAULT '',
  verified_oid TEXT NOT NULL DEFAULT '',
  verify_digest TEXT NOT NULL DEFAULT '',
  verify_attempts INTEGER NOT NULL DEFAULT 0,
  -- Which gate this request ran: '' before it was classified, 'full' for the
  -- repository's own verification command, 'docs_only' for the change set that was
  -- entirely documentation and therefore skipped it. Persisted rather than left in the
  -- event trail alone because a *skipped* gate must be visible wherever the row is
  -- drawn: a land nobody verified that reads identically to one that passed is exactly
  -- the silent cap this column exists to prevent.
  verify_gate TEXT NOT NULL DEFAULT '',
  trunk_before TEXT NOT NULL DEFAULT '',
  landed_oid TEXT NOT NULL DEFAULT '',
  handback_message_id TEXT NOT NULL DEFAULT '',
  -- How many armed replies this one request has spent. The consent a `request_land`
  -- carries is for the *answer to that request*, so it is denominated per request and
  -- claimed atomically here rather than inferred from the row's state: the state
  -- machine already allows only one terminal handback, and a cap that depends on
  -- another invariant staying true is not a cap.
  armed_replies INTEGER NOT NULL DEFAULT 0,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL,
  started_at REAL,
  finished_at REAL,
  revision INTEGER NOT NULL DEFAULT 1
);
CREATE UNIQUE INDEX IF NOT EXISTS land_requests_active
  ON land_requests(project_root, branch)
  WHERE state IN ('queued','waiting','reconciling','verifying','landing');
CREATE UNIQUE INDEX IF NOT EXISTS land_requests_inflight
  ON land_requests(project_root)
  WHERE state IN ('reconciling','verifying','landing');
CREATE INDEX IF NOT EXISTS land_requests_project
  ON land_requests(project_id, created_at DESC);
CREATE INDEX IF NOT EXISTS land_requests_queue
  ON land_requests(project_root, state, created_at);
CREATE INDEX IF NOT EXISTS land_requests_origin
  ON land_requests(origin, origin_session_id, created_at DESC);

CREATE TABLE IF NOT EXISTS land_events(
  id TEXT PRIMARY KEY,
  request_id TEXT NOT NULL,
  project_id TEXT NOT NULL,
  step TEXT NOT NULL,
  outcome TEXT NOT NULL,
  reason TEXT NOT NULL DEFAULT '',
  detail_json TEXT NOT NULL DEFAULT '{}',
  created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS land_events_request
  ON land_events(request_id, created_at);
CREATE INDEX IF NOT EXISTS land_events_project
  ON land_events(project_id, created_at DESC);

-- What a byte-identical gate did last time it passed, so a running one can honestly
-- say "step 3 of 7" rather than an opaque "verifying". Keyed by the *digest* rather
-- than by Project: a plan is a claim about a specific set of bytes, and a gate that
-- was edited must stop predicting rather than predict against the old script.
--
-- Written only after a run that PASSED. A failing gate stops at its first bad step
-- under `set -e`, so its step list is a prefix - recording that would shorten the
-- prediction permanently and make every later run look nearly finished.
CREATE TABLE IF NOT EXISTS land_verify_plans(
  project_root TEXT NOT NULL,
  digest TEXT NOT NULL,
  steps_json TEXT NOT NULL DEFAULT '[]',
  duration_ms REAL NOT NULL DEFAULT 0,
  observed_at REAL NOT NULL,
  PRIMARY KEY(project_root, digest)
);
"""


class LandConflict(Exception):
    """A row was claimed by another sweep, or the caller's view of it was stale."""


def _tune_connection(db: sqlite3.Connection) -> None:
    db.execute("PRAGMA synchronous=NORMAL")
    db.execute("PRAGMA busy_timeout=5000")
    db.execute("PRAGMA foreign_keys=ON")


def _loads(raw: Any) -> dict[str, Any]:
    try:
        value = json.loads(str(raw) or "{}")
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _row_to_request(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "project_id": str(row["project_id"]),
        "project_root": str(row["project_root"]),
        "worktree_root": str(row["worktree_root"]),
        "branch": str(row["branch"]),
        "requested_oid": str(row["requested_oid"] or ""),
        "trunk_ref": str(row["trunk_ref"] or ""),
        "origin": str(row["origin"] or "operator"),
        "origin_session_id": str(row["origin_session_id"] or ""),
        "origin_run_id": str(row["origin_run_id"] or ""),
        "correlation_id": str(row["correlation_id"] or ""),
        "state": str(row["state"]),
        "reason": str(row["reason"] or ""),
        "detail": _loads(row["detail_json"]),
        "waiting_since": row["waiting_since"],
        "reconciled_oid": str(row["reconciled_oid"] or ""),
        "verified_oid": str(row["verified_oid"] or ""),
        "verify_digest": str(row["verify_digest"] or ""),
        "verify_attempts": int(row["verify_attempts"] or 0),
        "verify_gate": str(row["verify_gate"] or ""),
        "trunk_before": str(row["trunk_before"] or ""),
        "landed_oid": str(row["landed_oid"] or ""),
        "handback_message_id": str(row["handback_message_id"] or ""),
        "armed_replies": int(row["armed_replies"] or 0),
        "created_at": float(row["created_at"]),
        "updated_at": float(row["updated_at"]),
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
        "revision": int(row["revision"]),
    }


def _row_to_event(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "request_id": str(row["request_id"]),
        "project_id": str(row["project_id"]),
        "step": str(row["step"]),
        "outcome": str(row["outcome"]),
        "reason": str(row["reason"] or ""),
        "detail": _loads(row["detail_json"]),
        "created_at": float(row["created_at"]),
    }


def _placeholders(values: tuple[str, ...]) -> str:
    return ",".join("?" for _ in values)


class LandStore:
    """SQLite store for land requests and their per-step audit trail."""

    _db: sqlite3.Connection

    def __init__(self, path: Path) -> None:
        self._path = path
        self._operation_lock = database_operation_lock(path)
        self._closed = False
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mux-land-db")
        self._executor.submit(self._connect).result()

    def _connect(self) -> None:
        with self._operation_lock:
            self._db = connect_or_quarantine(self._path, self._open)
            self._db.executescript(LAND_SCHEMA)
            self._migrate()
            write_schema_version(self._db, "land_queue", LAND_SCHEMA_VERSION)
            self._db.commit()

    def _columns(self, table: str) -> set[str]:
        return {
            str(row["name"]) for row in self._db.execute(f"PRAGMA table_info({table})").fetchall()
        }

    def _migrate(self) -> None:
        """Add columns a pre-existing database was created without.

        `CREATE TABLE IF NOT EXISTS` is a no-op against a table that already exists, so a
        column added to the schema above reaches a fresh install and no other. Backfilled
        to `''` - "this row was never classified" - rather than to `'full'`: every
        pre-migration land did run the full gate, but that is a fact about history rather
        than about the column, and a row that claims a classification nothing recorded is
        the wrong direction for a field whose whole job is making a skip visible.
        """
        columns = self._columns("land_requests")
        if columns and "verify_gate" not in columns:
            self._db.execute(
                "ALTER TABLE land_requests ADD COLUMN verify_gate TEXT NOT NULL DEFAULT ''"
            )
        if columns and "armed_replies" not in columns:
            # Zero for every pre-migration row, which is the truth: none of them could
            # have spent an armed reply, because nothing could arm one.
            self._db.execute(
                "ALTER TABLE land_requests ADD COLUMN armed_replies INTEGER NOT NULL DEFAULT 0"
            )

    def _open(self) -> sqlite3.Connection:
        db = sqlite3.connect(self._path, check_same_thread=False)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        _tune_connection(db)
        return db

    async def _run[T](self, fn: Callable[[], T]) -> T:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self._executor, run_sqlite_operation, self._db, self._operation_lock, fn
        )

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._executor.submit(self._db.close).result()
        self._executor.shutdown(wait=True)

    # -- requests -------------------------------------------------------------

    async def enqueue(
        self,
        *,
        project_id: str,
        project_root: str,
        worktree_root: str,
        branch: str,
        requested_oid: str,
        trunk_ref: str,
        origin: str = "operator",
        origin_session_id: str = "",
        origin_run_id: str = "",
        correlation_id: str = "",
        now: float | None = None,
    ) -> dict[str, Any]:
        """Claim a branch for landing, or raise if it is already claimed.

        The insert is the claim. `LandConflict` here means an active request for this
        branch already exists, which is the answer a repeat call should get rather than
        a second pipeline over one worktree.
        """
        moment = time.time() if now is None else now
        request_id = f"lnd_{uuid.uuid4().hex[:16]}"

        def op() -> dict[str, Any]:
            try:
                self._db.execute(
                    "INSERT INTO land_requests(id,project_id,project_root,worktree_root,branch,"
                    "requested_oid,trunk_ref,origin,origin_session_id,origin_run_id,"
                    "correlation_id,state,created_at,updated_at)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?,?,'queued',?,?)",
                    (
                        request_id,
                        project_id,
                        project_root,
                        worktree_root,
                        branch,
                        requested_oid,
                        trunk_ref,
                        origin,
                        origin_session_id,
                        origin_run_id,
                        correlation_id,
                        moment,
                        moment,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise LandConflict(f"{branch} already has an active land request") from exc
            self._db.commit()
            row = self._db.execute(
                "SELECT * FROM land_requests WHERE id=?", (request_id,)
            ).fetchone()
            return _row_to_request(row)

        return await self._run(op)

    async def get(self, request_id: str) -> dict[str, Any] | None:
        def op() -> dict[str, Any] | None:
            row = self._db.execute(
                "SELECT * FROM land_requests WHERE id=?", (request_id,)
            ).fetchone()
            return _row_to_request(row) if row else None

        return await self._run(op)

    async def active_for_branch(self, project_root: str, branch: str) -> dict[str, Any] | None:
        def op() -> dict[str, Any] | None:
            row = self._db.execute(
                "SELECT * FROM land_requests WHERE project_root=? AND branch=?"
                f" AND state IN ({_placeholders(ACTIVE_STATES)})",
                (project_root, branch, *ACTIVE_STATES),
            ).fetchone()
            return _row_to_request(row) if row else None

        return await self._run(op)

    async def inflight(self, project_root: str) -> dict[str, Any] | None:
        """The one request currently mutating this trunk, if any."""

        def op() -> dict[str, Any] | None:
            row = self._db.execute(
                "SELECT * FROM land_requests WHERE project_root=?"
                f" AND state IN ({_placeholders(INFLIGHT_STATES)})",
                (project_root, *INFLIGHT_STATES),
            ).fetchone()
            return _row_to_request(row) if row else None

        return await self._run(op)

    async def next_ready(self, project_root: str) -> dict[str, Any] | None:
        """The oldest request eligible to run on this trunk, in arrival order.

        A `waiting` row is eligible again on every sweep: it is holding for a busy
        worktree, and the whole point of the hold is that it lands once that clears.
        """

        def op() -> dict[str, Any] | None:
            row = self._db.execute(
                "SELECT * FROM land_requests WHERE project_root=?"
                " AND state IN ('queued','waiting') ORDER BY created_at LIMIT 1",
                (project_root,),
            ).fetchone()
            return _row_to_request(row) if row else None

        return await self._run(op)

    async def active_roots(self) -> list[str]:
        """Every trunk with work outstanding, so a sweep visits only those."""

        def op() -> list[str]:
            rows = self._db.execute(
                "SELECT DISTINCT project_root FROM land_requests"
                f" WHERE state IN ({_placeholders(ACTIVE_STATES)})",
                ACTIVE_STATES,
            ).fetchall()
            return [str(row["project_root"]) for row in rows]

        return await self._run(op)

    async def list_requests(
        self,
        *,
        project_id: str | None = None,
        project_root: str | None = None,
        include_terminal: bool = True,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        def op() -> list[dict[str, Any]]:
            conditions: list[str] = []
            params: list[Any] = []
            if project_id:
                conditions.append("project_id=?")
                params.append(project_id)
            if project_root:
                conditions.append("project_root=?")
                params.append(project_root)
            if not include_terminal:
                conditions.append(f"state IN ({_placeholders(ACTIVE_STATES)})")
                params.extend(ACTIVE_STATES)
            where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
            rows = self._db.execute(
                f"SELECT * FROM land_requests{where} ORDER BY created_at DESC LIMIT ?",
                (*params, max(1, min(int(limit), 500))),
            ).fetchall()
            return [_row_to_request(row) for row in rows]

        return await self._run(op)

    async def transition(
        self,
        request_id: str,
        *,
        expect: tuple[str, ...],
        state: str,
        reason: str = "",
        detail: dict[str, Any] | None = None,
        waiting_since: float | None = None,
        clear_waiting: bool = False,
        reconciled_oid: str | None = None,
        verified_oid: str | None = None,
        verify_digest: str | None = None,
        verify_gate: str | None = None,
        bump_verify_attempts: bool = False,
        trunk_before: str | None = None,
        landed_oid: str | None = None,
        handback_message_id: str | None = None,
        now: float | None = None,
    ) -> dict[str, Any]:
        """Move a request, refusing when it is not in one of the expected states.

        Conditional on `expect` rather than read-then-write: the queue worker and an
        operator cancel can both reach one row, and the loser has to find out.
        """
        if state not in LAND_STATES:
            raise ValueError(f"unknown land state: {state}")
        moment = time.time() if now is None else now

        def op() -> dict[str, Any]:
            assignments = ["state=?", "reason=?", "updated_at=?"]
            params: list[Any] = [state, reason, moment]
            if detail is not None:
                assignments.append("detail_json=?")
                params.append(json.dumps(detail, default=str))
            if clear_waiting:
                assignments.append("waiting_since=NULL")
            elif waiting_since is not None:
                assignments.append("waiting_since=?")
                params.append(waiting_since)
            for column, value in (
                ("reconciled_oid", reconciled_oid),
                ("verified_oid", verified_oid),
                ("verify_digest", verify_digest),
                ("verify_gate", verify_gate),
                ("trunk_before", trunk_before),
                ("landed_oid", landed_oid),
                ("handback_message_id", handback_message_id),
            ):
                if value is not None:
                    assignments.append(f"{column}=?")
                    params.append(value)
            if bump_verify_attempts:
                assignments.append("verify_attempts=verify_attempts+1")
            if state in INFLIGHT_STATES:
                assignments.append("started_at=COALESCE(started_at,?)")
                params.append(moment)
            if state in TERMINAL_STATES:
                assignments.append("finished_at=?")
                params.append(moment)
            assignments.append("revision=revision+1")
            try:
                cursor = self._db.execute(
                    f"UPDATE land_requests SET {','.join(assignments)}"
                    f" WHERE id=? AND state IN ({_placeholders(expect)})",
                    (*params, request_id, *expect),
                )
            except sqlite3.IntegrityError as exc:
                # The in-flight index refused: another request already owns this trunk.
                raise LandConflict(f"{request_id} cannot enter {state}: trunk busy") from exc
            if cursor.rowcount == 0:
                self._db.rollback()
                raise LandConflict(f"{request_id} was not in {expect}")
            self._db.commit()
            row = self._db.execute(
                "SELECT * FROM land_requests WHERE id=?", (request_id,)
            ).fetchone()
            return _row_to_request(row)

        return await self._run(op)

    async def restore(self, *, now: float | None = None) -> list[dict[str, Any]]:
        """Return in-flight rows to the queue after a daemon restart.

        A row left `reconciling`/`verifying`/`landing` is a step whose daemon died
        mid-flight. It goes back to `queued` rather than being resumed: every step is
        re-checked against the repository from scratch anyway, so re-running one is
        safe and guessing how far it got is not.
        """
        moment = time.time() if now is None else now

        def op() -> list[dict[str, Any]]:
            rows = self._db.execute(
                f"SELECT * FROM land_requests WHERE state IN ({_placeholders(INFLIGHT_STATES)})",
                INFLIGHT_STATES,
            ).fetchall()
            recovered = [_row_to_request(row) for row in rows]
            if recovered:
                self._db.execute(
                    "UPDATE land_requests SET state='queued',"
                    " reason='daemon restarted mid-step', updated_at=?, revision=revision+1"
                    f" WHERE state IN ({_placeholders(INFLIGHT_STATES)})",
                    (moment, *INFLIGHT_STATES),
                )
                self._db.commit()
            return recovered

        return await self._run(op)

    # -- audit ----------------------------------------------------------------

    async def record_event(
        self,
        *,
        request_id: str,
        project_id: str,
        step: str,
        outcome: str,
        reason: str = "",
        detail: dict[str, Any] | None = None,
        now: float | None = None,
    ) -> str:
        moment = time.time() if now is None else now
        event_id = f"lev_{uuid.uuid4().hex[:16]}"

        def op() -> str:
            self._db.execute(
                "INSERT INTO land_events(id,request_id,project_id,step,outcome,reason,"
                "detail_json,created_at) VALUES(?,?,?,?,?,?,?,?)",
                (
                    event_id,
                    request_id,
                    project_id,
                    step,
                    outcome,
                    reason,
                    json.dumps(detail or {}, default=str),
                    moment,
                ),
            )
            self._db.commit()
            return event_id

        return await self._run(op)

    async def events(self, request_id: str, *, limit: int = 200) -> list[dict[str, Any]]:
        def op() -> list[dict[str, Any]]:
            rows = self._db.execute(
                "SELECT * FROM land_events WHERE request_id=? ORDER BY created_at LIMIT ?",
                (request_id, max(1, min(int(limit), 1000))),
            ).fetchall()
            return [_row_to_event(row) for row in rows]

        return await self._run(op)

    # -- verification plans ---------------------------------------------------

    async def record_verify_plan(
        self,
        *,
        project_root: str,
        digest: str,
        steps: Sequence[str],
        duration_ms: float,
        now: float | None = None,
    ) -> None:
        """Record what a *passing* gate's steps were, for the next run to be measured against.

        Replaces rather than accumulates: the newest passing run of these exact bytes is
        the best available statement about them, and keeping a history would only raise
        the question of which entry to predict from.
        """
        if not digest or not steps:
            return
        moment = time.time() if now is None else now
        payload = json.dumps([str(step) for step in steps])

        def op() -> None:
            self._db.execute(
                "INSERT INTO land_verify_plans(project_root,digest,steps_json,duration_ms,"
                "observed_at) VALUES(?,?,?,?,?)"
                " ON CONFLICT(project_root,digest) DO UPDATE SET"
                " steps_json=excluded.steps_json, duration_ms=excluded.duration_ms,"
                " observed_at=excluded.observed_at",
                (project_root, digest, payload, float(duration_ms), moment),
            )
            self._db.commit()

        await self._run(op)

    async def verify_plan(self, project_root: str, digest: str) -> dict[str, Any] | None:
        """The recorded plan for these exact bytes, or `None`.

        `None` is the honest answer for a gate nobody has watched pass yet, and the
        caller renders it as a step number with no total rather than as a guess.
        """
        if not digest:
            return None

        def op() -> dict[str, Any] | None:
            row = self._db.execute(
                "SELECT * FROM land_verify_plans WHERE project_root=? AND digest=?",
                (project_root, digest),
            ).fetchone()
            if row is None:
                return None
            raw = json.loads(str(row["steps_json"]) or "[]")
            return {
                "steps": raw if isinstance(raw, list) else [],
                "duration_ms": float(row["duration_ms"] or 0.0),
                "observed_at": float(row["observed_at"]),
            }

        try:
            return await self._run(op)
        except json.JSONDecodeError:
            # A malformed plan degrades to "no plan", which renders as `step 3` with no
            # total. It must never degrade to a wrong total.
            return None

    async def origin_count_since(self, origin_session_id: str, since: float) -> int:
        """How many requests one session has made in a window, for the budget."""

        def op() -> int:
            row = self._db.execute(
                "SELECT COUNT(*) n FROM land_requests"
                " WHERE origin_session_id=? AND created_at>=?",
                (origin_session_id, since),
            ).fetchone()
            return int(row["n"] or 0)

        return await self._run(op)

    async def claim_armed_reply(self, request_id: str, *, cap: int) -> bool:
        """Spend one of this request's armed replies, or answer that none are left.

        A conditional UPDATE rather than a read-then-write, for the same reason every
        other transition here is one: two sweeps racing on one row must produce one
        claim, and a caller that loses must be told so rather than proceeding on a
        stale count.
        """
        if cap <= 0:
            return False

        def op() -> bool:
            cursor = self._db.execute(
                "UPDATE land_requests SET armed_replies=armed_replies+1"
                " WHERE id=? AND armed_replies<?",
                (request_id, int(cap)),
            )
            self._db.commit()
            return cursor.rowcount == 1

        return await self._run(op)

    async def open_origin_requests(
        self, session_ids: Sequence[str], since: float
    ) -> dict[str, dict[str, Any]]:
        """The live land request each of these sessions is currently awaiting.

        The land half of the bounded reply window (`auto-delivery.md`): a session that
        asked to land and is waiting for the pipeline's answer is the waiting half of an
        exchange it opened itself, not an untouched conversation. Derived from the
        request rows rather than tracked separately, exactly like the message window is
        derived from the queue, so it cannot disagree with the audit trail.

        ``since`` is measured against ``updated_at`` - the last step the pipeline
        actually recorded - so a queue that is moving holds the window and a row nothing
        has touched for the whole window does not. Terminal rows are excluded: the answer
        has already been written by then, and holding a grant open past it would be a
        window with no exchange in it.
        """
        ids = [str(session_id) for session_id in session_ids if str(session_id)]
        if not ids:
            return {}

        def op() -> dict[str, dict[str, Any]]:
            placeholders = ",".join("?" * len(ids))
            states = ",".join("?" * len(ACTIVE_STATES))
            rows = self._db.execute(
                "SELECT origin_session_id, id, branch, state, project_root, origin_run_id,"
                " MAX(updated_at) updated_at FROM land_requests"
                f" WHERE origin_session_id IN ({placeholders})"
                f" AND state IN ({states}) AND updated_at>=?"
                " GROUP BY origin_session_id",
                (*ids, *ACTIVE_STATES, since),
            ).fetchall()
            return {
                str(row["origin_session_id"]): {
                    "request_id": str(row["id"]),
                    "branch": str(row["branch"] or ""),
                    "state": str(row["state"]),
                    "project_root": str(row["project_root"] or ""),
                    "origin_run_id": str(row["origin_run_id"] or ""),
                    "updated_at": float(row["updated_at"] or 0.0),
                }
                for row in rows
            }

        return await self._run(op)
