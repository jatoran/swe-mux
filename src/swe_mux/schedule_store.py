"""Persistence for scheduled runs: the definitions and their run history.

**Machine-local by construction.** These rows live in the daemon's own database
under the data directory, never in the Project's committed
`.swe-mux/config.toml`. A schedule that travelled with the repository would arm
itself in every clone and every worktree the moment someone opened it, which is
the same boundary Project Action trust already draws by keeping approval in the
data directory. The Project *opt-in* (`scheduled_runs`) stays portable and is
inert on its own: a clone inherits permission to run schedules and has none.

Structure follows `PromptQueueStore`: one dedicated worker thread, statements
executed in submission order, state transitions written as conditional UPDATEs
so a stale caller loses a race instead of corrupting state.

The uniqueness index on `(schedule_id, fire_key)` is the load-bearing one. It is
what makes a fire idempotent: a daemon that dies between spawning a session and
recording the outcome cannot spawn the same occurrence again on restart, because
the run row is written *before* the spawn and the insert is what claims the
occurrence.

**Every row records its author** (schema 3). A person's schedule is ``author_kind =
'user'``; an agent's carries the session, run and name that asked, and an
``approval_state`` saying how it came to be armed: ``granted`` (the Project's
`schedule_grant` let the agent arm it), ``pending`` (a person has to decide),
``approved``/``dismissed`` (a person decided). The pending row *is* the approval
request - there is no second copy of the definition anywhere for the two to drift
between - and a dismissed or cancelled one is kept, disarmed, so the audit of what an
agent asked for survives the decision.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .executor_accounting import AccountedThreadPoolExecutor
from .schedules import FollowUp, ScheduleSpec
from .sqlite_store import (
    connect_or_quarantine,
    database_operation_lock,
    run_sqlite_operation,
    write_schema_version,
)

SCHEDULE_SCHEMA_VERSION = 3

SCHEDULE_SCHEMA = """
CREATE TABLE IF NOT EXISTS schedules(
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  project_root TEXT NOT NULL DEFAULT '',
  label TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1,
  trigger_kind TEXT NOT NULL,
  cron TEXT NOT NULL DEFAULT '',
  interval_seconds REAL,
  run_at REAL,
  timezone TEXT NOT NULL DEFAULT '',
  catch_up INTEGER NOT NULL DEFAULT 0,
  overlap TEXT NOT NULL DEFAULT 'skip',
  backend TEXT NOT NULL DEFAULT '',
  profile_id TEXT NOT NULL DEFAULT '',
  cwd TEXT NOT NULL DEFAULT '',
  session_name TEXT NOT NULL DEFAULT '',
  prompt TEXT NOT NULL,
  follow_ups_json TEXT NOT NULL DEFAULT '[]',
  daily_run_cap INTEGER NOT NULL DEFAULT 0,
  next_fire_at REAL,
  last_fire_at REAL,
  last_session_id TEXT,
  last_outcome TEXT NOT NULL DEFAULT '',
  last_reason TEXT NOT NULL DEFAULT '',
  disabled_reason TEXT NOT NULL DEFAULT '',
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL,
  revision INTEGER NOT NULL DEFAULT 1,
  action TEXT NOT NULL DEFAULT 'spawn',
  target_run_id TEXT NOT NULL DEFAULT '',
  target_kind TEXT NOT NULL DEFAULT 'run',
  target_cut_message_id TEXT NOT NULL DEFAULT '',
  target_cut_mode TEXT NOT NULL DEFAULT '',
  context_ceiling_pct REAL NOT NULL DEFAULT 0,
  model TEXT NOT NULL DEFAULT '',
  expires_at REAL,
  when_live TEXT NOT NULL DEFAULT 'skip',
  author_kind TEXT NOT NULL DEFAULT 'user',
  author_session_id TEXT NOT NULL DEFAULT '',
  author_run_id TEXT NOT NULL DEFAULT '',
  author_name TEXT NOT NULL DEFAULT '',
  author_project_id TEXT NOT NULL DEFAULT '',
  request_reason TEXT NOT NULL DEFAULT '',
  approval_state TEXT NOT NULL DEFAULT '',
  approval_reason TEXT NOT NULL DEFAULT '',
  decided_by TEXT NOT NULL DEFAULT '',
  decided_at REAL,
  fires_per_day INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS schedules_project ON schedules(project_id);
CREATE INDEX IF NOT EXISTS schedules_due ON schedules(enabled, next_fire_at);

CREATE TABLE IF NOT EXISTS schedule_runs(
  id TEXT PRIMARY KEY,
  schedule_id TEXT NOT NULL,
  project_id TEXT NOT NULL,
  fire_key TEXT NOT NULL,
  due_at REAL,
  started_at REAL NOT NULL,
  finished_at REAL,
  outcome TEXT NOT NULL DEFAULT 'started',
  reason TEXT NOT NULL DEFAULT '',
  session_id TEXT,
  origin TEXT NOT NULL DEFAULT 'timer',
  queued_messages INTEGER NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX IF NOT EXISTS schedule_runs_occurrence
  ON schedule_runs(schedule_id, fire_key);
CREATE INDEX IF NOT EXISTS schedule_runs_recent ON schedule_runs(schedule_id, started_at DESC);
CREATE INDEX IF NOT EXISTS schedule_runs_project ON schedule_runs(project_id, started_at DESC);
"""

# Added by schema version 2, when a schedule stopped being only a deferred spawn.
# Applied as ``ALTER TABLE ... ADD COLUMN`` rather than a table rebuild because every
# one of them has a default that reads as the old behaviour: an existing row is a
# ``spawn`` with no target, which is exactly what it was.
_ADDED_COLUMNS: tuple[tuple[str, str], ...] = (
    ("action", "TEXT NOT NULL DEFAULT 'spawn'"),
    ("target_run_id", "TEXT NOT NULL DEFAULT ''"),
    ("target_kind", "TEXT NOT NULL DEFAULT 'run'"),
    ("target_cut_message_id", "TEXT NOT NULL DEFAULT ''"),
    ("target_cut_mode", "TEXT NOT NULL DEFAULT ''"),
    ("context_ceiling_pct", "REAL NOT NULL DEFAULT 0"),
    # Schema 3: authorship, approval, and the three definition fields agents needed.
    # Every default reads as the old behaviour - a person's schedule, already armed,
    # with no end date and a resume that skips an open conversation - so an existing
    # row is exactly what it was.
    ("model", "TEXT NOT NULL DEFAULT ''"),
    ("expires_at", "REAL"),
    ("when_live", "TEXT NOT NULL DEFAULT 'skip'"),
    ("author_kind", "TEXT NOT NULL DEFAULT 'user'"),
    ("author_session_id", "TEXT NOT NULL DEFAULT ''"),
    ("author_run_id", "TEXT NOT NULL DEFAULT ''"),
    ("author_name", "TEXT NOT NULL DEFAULT ''"),
    ("author_project_id", "TEXT NOT NULL DEFAULT ''"),
    ("request_reason", "TEXT NOT NULL DEFAULT ''"),
    ("approval_state", "TEXT NOT NULL DEFAULT ''"),
    ("approval_reason", "TEXT NOT NULL DEFAULT ''"),
    ("decided_by", "TEXT NOT NULL DEFAULT ''"),
    ("decided_at", "REAL"),
    ("fires_per_day", "INTEGER NOT NULL DEFAULT 0"),
)

# Indexes over schema-3 columns. Created after `_migrate` rather than in the schema
# script, because the script runs first and an older database does not have the
# columns yet.
_ADDED_INDEXES = (
    "CREATE INDEX IF NOT EXISTS schedules_author ON schedules(author_session_id, created_at)",
    "CREATE INDEX IF NOT EXISTS schedules_approval ON schedules(approval_state, project_id)",
)

# Outcomes a run row can settle on. `started` is the transient claim state, and a
# row left in it is a fire the daemon did not live long enough to finish - which
# is worth seeing rather than silently repairing. `delivered` is a resume whose
# conversation was already open, so its prompt was queued into that pane instead of
# opening a second one (`when_live = 'deliver'`).
OUTCOMES = ("started", "spawned", "delivered", "skipped", "failed", "missed")

AUTHOR_USER = "user"
AUTHOR_AGENT = "agent"
#: `approval_state` values. Empty for a person's own schedule, which needs none.
APPROVAL_GRANTED = "granted"
APPROVAL_PENDING = "pending"
APPROVAL_APPROVED = "approved"
APPROVAL_DISMISSED = "dismissed"
#: `disabled_reason` values this store and its callers write and branch on.
DISABLED_AWAITING_APPROVAL = "awaiting_approval"
DISABLED_DISMISSED = "dismissed"
DISABLED_CANCELLED = "cancelled"
DISABLED_EXPIRED = "expired"


@dataclass(frozen=True, slots=True)
class ScheduleAuthor:
    """Who wrote a schedule. The default is a person, which is every pre-schema-3 row."""

    kind: str = AUTHOR_USER
    session_id: str = ""
    run_id: str = ""
    name: str = ""
    project_id: str = ""
    #: The agent's own account of why, shown to the person who approves it.
    reason: str = ""


class ScheduleConflict(Exception):
    """The occurrence was already claimed by another sweep or another daemon."""


def _tune_connection(db: sqlite3.Connection) -> None:
    db.execute("PRAGMA synchronous=NORMAL")
    db.execute("PRAGMA busy_timeout=5000")
    db.execute("PRAGMA foreign_keys=ON")


def _follow_ups_json(follow_ups: tuple[FollowUp, ...]) -> str:
    return json.dumps([item.as_dict() for item in follow_ups])


def _row_to_schedule(row: sqlite3.Row) -> dict[str, Any]:
    try:
        follow_ups = json.loads(str(row["follow_ups_json"]) or "[]")
    except json.JSONDecodeError:
        follow_ups = []
    return {
        "id": str(row["id"]),
        "project_id": str(row["project_id"]),
        "project_root": str(row["project_root"] or ""),
        "label": str(row["label"]),
        "enabled": bool(row["enabled"]),
        "trigger_kind": str(row["trigger_kind"]),
        "cron": str(row["cron"] or ""),
        "interval_seconds": row["interval_seconds"],
        "run_at": row["run_at"],
        "timezone": str(row["timezone"] or ""),
        "catch_up": bool(row["catch_up"]),
        "overlap": str(row["overlap"] or "skip"),
        "backend": str(row["backend"] or ""),
        "profile_id": str(row["profile_id"] or ""),
        "cwd": str(row["cwd"] or ""),
        "session_name": str(row["session_name"] or ""),
        "prompt": str(row["prompt"]),
        "follow_ups": follow_ups,
        "daily_run_cap": int(row["daily_run_cap"] or 0),
        "next_fire_at": row["next_fire_at"],
        "last_fire_at": row["last_fire_at"],
        "last_session_id": row["last_session_id"],
        "last_outcome": str(row["last_outcome"] or ""),
        "last_reason": str(row["last_reason"] or ""),
        "disabled_reason": str(row["disabled_reason"] or ""),
        "created_at": float(row["created_at"]),
        "updated_at": float(row["updated_at"]),
        "revision": int(row["revision"]),
        "action": str(row["action"] or "spawn"),
        "target_run_id": str(row["target_run_id"] or ""),
        "target_kind": str(row["target_kind"] or "run"),
        "target_cut_message_id": str(row["target_cut_message_id"] or ""),
        "target_cut_mode": str(row["target_cut_mode"] or ""),
        "context_ceiling_pct": float(row["context_ceiling_pct"] or 0.0),
        "model": str(row["model"] or ""),
        "expires_at": row["expires_at"],
        "when_live": str(row["when_live"] or "skip"),
        "author_kind": str(row["author_kind"] or AUTHOR_USER),
        "author_session_id": str(row["author_session_id"] or ""),
        "author_run_id": str(row["author_run_id"] or ""),
        "author_name": str(row["author_name"] or ""),
        "author_project_id": str(row["author_project_id"] or ""),
        "request_reason": str(row["request_reason"] or ""),
        "approval_state": str(row["approval_state"] or ""),
        "approval_reason": str(row["approval_reason"] or ""),
        "decided_by": str(row["decided_by"] or ""),
        "decided_at": row["decided_at"],
        "fires_per_day": int(row["fires_per_day"] or 0),
    }


def _row_to_run(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "schedule_id": str(row["schedule_id"]),
        "project_id": str(row["project_id"]),
        "fire_key": str(row["fire_key"]),
        "due_at": row["due_at"],
        "started_at": float(row["started_at"]),
        "finished_at": row["finished_at"],
        "outcome": str(row["outcome"]),
        "reason": str(row["reason"] or ""),
        "session_id": row["session_id"],
        "origin": str(row["origin"] or "timer"),
        "queued_messages": int(row["queued_messages"] or 0),
    }


class ScheduleStore:
    """SQLite store for schedule definitions and run history."""

    _db: sqlite3.Connection

    def __init__(self, path: Path) -> None:
        self._path = path
        self._operation_lock = database_operation_lock(path)
        self._closed = False
        self._executor = AccountedThreadPoolExecutor(
            max_workers=1, thread_name_prefix="mux-schedule-db"
        )
        self._executor.submit(self._connect).result()

    def _connect(self) -> None:
        with self._operation_lock:
            self._db = connect_or_quarantine(self._path, self._open)
            self._db.executescript(SCHEDULE_SCHEMA)
            self._migrate()
            write_schema_version(self._db, "schedules", SCHEDULE_SCHEMA_VERSION)
            self._db.commit()

    def _migrate(self) -> None:
        """Add columns a newer version introduced, idempotently.

        Read from `PRAGMA table_info` rather than from a recorded version, so a
        database that was created by a newer build and then opened by an older one and
        back again still ends up with every column exactly once.
        """
        present = {
            str(row["name"]) for row in self._db.execute("PRAGMA table_info(schedules)")
        }
        for column, declaration in _ADDED_COLUMNS:
            if column not in present:
                self._db.execute(f"ALTER TABLE schedules ADD COLUMN {column} {declaration}")
        for statement in _ADDED_INDEXES:
            self._db.execute(statement)

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

    # -- definitions ----------------------------------------------------------

    async def create(
        self,
        *,
        project_id: str,
        project_root: str,
        spec: ScheduleSpec,
        next_fire_at: float | None,
        now: float | None = None,
        author: ScheduleAuthor | None = None,
        enabled: bool = True,
        disabled_reason: str = "",
        approval_state: str = "",
        approval_reason: str = "",
        fires_per_day: int = 0,
    ) -> dict[str, Any]:
        moment = time.time() if now is None else now
        schedule_id = f"sch_{uuid.uuid4().hex[:16]}"
        who = author or ScheduleAuthor()

        def op() -> dict[str, Any]:
            self._db.execute(
                "INSERT INTO schedules(id,project_id,project_root,label,enabled,trigger_kind,"
                "cron,interval_seconds,run_at,timezone,catch_up,overlap,backend,profile_id,"
                "cwd,session_name,prompt,follow_ups_json,daily_run_cap,next_fire_at,"
                "created_at,updated_at,revision,action,target_run_id,target_kind,"
                "target_cut_message_id,target_cut_mode,context_ceiling_pct,"
                "model,expires_at,when_live,author_kind,author_session_id,author_run_id,"
                "author_name,author_project_id,request_reason,approval_state,"
                "approval_reason,disabled_reason,fires_per_day)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?,?,?,?,?,?,"
                "?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    schedule_id,
                    project_id,
                    project_root,
                    spec.label,
                    int(enabled),
                    spec.trigger_kind,
                    spec.cron,
                    spec.interval_seconds,
                    spec.run_at,
                    spec.timezone,
                    int(spec.catch_up),
                    spec.overlap,
                    spec.backend,
                    spec.profile_id,
                    spec.cwd,
                    spec.session_name,
                    spec.prompt,
                    _follow_ups_json(spec.follow_ups),
                    spec.daily_run_cap,
                    next_fire_at,
                    moment,
                    moment,
                    spec.action,
                    spec.target_run_id,
                    spec.target_kind,
                    spec.target_cut_message_id,
                    spec.target_cut_mode,
                    spec.context_ceiling_pct,
                    spec.model,
                    spec.expires_at,
                    spec.when_live,
                    who.kind,
                    who.session_id,
                    who.run_id,
                    who.name,
                    who.project_id,
                    who.reason,
                    approval_state,
                    approval_reason,
                    disabled_reason,
                    int(fires_per_day),
                ),
            )
            self._db.commit()
            row = self._db.execute(
                "SELECT * FROM schedules WHERE id=?", (schedule_id,)
            ).fetchone()
            return _row_to_schedule(row)

        return await self._run(op)

    async def replace(
        self,
        schedule_id: str,
        *,
        spec: ScheduleSpec,
        next_fire_at: float | None,
        revision: int | None = None,
        now: float | None = None,
        fires_per_day: int = 0,
    ) -> dict[str, Any] | None:
        """Rewrite a definition. A stale `revision` loses the race and returns None.

        This is a person's edit, and it changes what an agent-authored row means. One
        an agent armed under its grant becomes ``approved``: a person has now read and
        rewritten it, so it stops being re-judged against the Project's level at every
        fire. One still awaiting a decision stays awaiting it - editing a request is not
        approving it - and keeps the disabled reason that says so.
        """
        moment = time.time() if now is None else now

        def op() -> dict[str, Any] | None:
            params: list[Any] = [
                spec.label,
                spec.trigger_kind,
                spec.cron,
                spec.interval_seconds,
                spec.run_at,
                spec.timezone,
                int(spec.catch_up),
                spec.overlap,
                spec.backend,
                spec.profile_id,
                spec.cwd,
                spec.session_name,
                spec.prompt,
                _follow_ups_json(spec.follow_ups),
                spec.daily_run_cap,
                next_fire_at,
                moment,
                spec.action,
                spec.target_run_id,
                spec.target_kind,
                spec.target_cut_message_id,
                spec.target_cut_mode,
                spec.context_ceiling_pct,
                spec.model,
                spec.expires_at,
                spec.when_live,
                int(fires_per_day),
                schedule_id,
            ]
            clause = ""
            if revision is not None:
                clause = " AND revision=?"
                params.append(revision)
            # SET expressions read the row as it was before this statement, so the two
            # CASEs below see the old approval state, not each other's result.
            cursor = self._db.execute(
                "UPDATE schedules SET label=?,trigger_kind=?,cron=?,interval_seconds=?,run_at=?,"
                "timezone=?,catch_up=?,overlap=?,backend=?,profile_id=?,cwd=?,session_name=?,"
                "prompt=?,follow_ups_json=?,daily_run_cap=?,"
                "next_fire_at=CASE WHEN approval_state='pending' THEN NULL ELSE ? END,"
                "updated_at=?,"
                "action=?,target_run_id=?,target_kind=?,target_cut_message_id=?,"
                "target_cut_mode=?,context_ceiling_pct=?,model=?,expires_at=?,when_live=?,"
                "fires_per_day=?,"
                "approval_state=CASE WHEN approval_state='granted' THEN 'approved'"
                " ELSE approval_state END,"
                "disabled_reason=CASE WHEN approval_state='pending' THEN disabled_reason"
                " ELSE '' END,"
                "revision=revision+1"
                f" WHERE id=?{clause}",
                params,
            )
            if not cursor.rowcount:
                self._db.commit()
                return None
            self._db.commit()
            row = self._db.execute(
                "SELECT * FROM schedules WHERE id=?", (schedule_id,)
            ).fetchone()
            return _row_to_schedule(row) if row else None

        return await self._run(op)

    async def set_enabled(
        self,
        schedule_id: str,
        enabled: bool,
        *,
        next_fire_at: float | None = None,
        reason: str = "",
        now: float | None = None,
    ) -> dict[str, Any] | None:
        moment = time.time() if now is None else now

        def op() -> dict[str, Any] | None:
            self._db.execute(
                "UPDATE schedules SET enabled=?,next_fire_at=?,disabled_reason=?,updated_at=?,"
                "revision=revision+1 WHERE id=?",
                (int(enabled), next_fire_at, reason, moment, schedule_id),
            )
            self._db.commit()
            row = self._db.execute(
                "SELECT * FROM schedules WHERE id=?", (schedule_id,)
            ).fetchone()
            return _row_to_schedule(row) if row else None

        return await self._run(op)

    async def set_next_fire(
        self, schedule_id: str, next_fire_at: float | None, *, now: float | None = None
    ) -> None:
        moment = time.time() if now is None else now

        def op() -> None:
            self._db.execute(
                "UPDATE schedules SET next_fire_at=?,updated_at=? WHERE id=?",
                (next_fire_at, moment, schedule_id),
            )
            self._db.commit()

        await self._run(op)

    async def set_approval(
        self,
        schedule_id: str,
        *,
        expected: tuple[str, ...],
        state: str,
        enabled: bool,
        next_fire_at: float | None,
        disabled_reason: str = "",
        decided_by: str = "",
        approval_reason: str | None = None,
        now: float | None = None,
    ) -> dict[str, Any] | None:
        """Move an agent-authored row between approval states, conditionally.

        Conditional on the state the caller read (`expected`), so two people deciding
        the same request at once - the desktop and a phone - cannot both win: the
        loser gets None and answers "already decided" instead of re-arming a schedule
        the other one dismissed.
        """
        moment = time.time() if now is None else now
        placeholders = ",".join("?" for _ in expected)

        def op() -> dict[str, Any] | None:
            assignments = (
                "approval_state=?,enabled=?,next_fire_at=?,disabled_reason=?,updated_at=?,"
                "revision=revision+1"
            )
            params: list[Any] = [state, int(enabled), next_fire_at, disabled_reason, moment]
            if decided_by:
                assignments += ",decided_by=?,decided_at=?"
                params.extend([decided_by, moment])
            if approval_reason is not None:
                assignments += ",approval_reason=?"
                params.append(approval_reason)
            params.append(schedule_id)
            params.extend(expected)
            cursor = self._db.execute(
                f"UPDATE schedules SET {assignments} WHERE id=?"
                f" AND approval_state IN ({placeholders})",
                params,
            )
            self._db.commit()
            if not cursor.rowcount:
                return None
            row = self._db.execute(
                "SELECT * FROM schedules WHERE id=?", (schedule_id,)
            ).fetchone()
            return _row_to_schedule(row) if row else None

        return await self._run(op)

    async def expire_due(self, now: float) -> list[dict[str, Any]]:
        """Disarm every armed schedule whose end date has passed, and return them.

        Run by the sweep before it looks for due fires, so a schedule cannot fire in
        the same tick it expired in, and so an expired row stops reading as armed
        when it expires rather than at whatever its next fire would have been.
        """

        def op() -> list[dict[str, Any]]:
            rows = self._db.execute(
                "SELECT id FROM schedules WHERE enabled=1 AND expires_at IS NOT NULL"
                " AND expires_at<=?",
                (now,),
            ).fetchall()
            ids = [str(row["id"]) for row in rows]
            for schedule_id in ids:
                self._db.execute(
                    "UPDATE schedules SET enabled=0,next_fire_at=NULL,disabled_reason=?,"
                    "updated_at=?,revision=revision+1 WHERE id=?",
                    (DISABLED_EXPIRED, now, schedule_id),
                )
            self._db.commit()
            if not ids:
                return []
            placeholders = ",".join("?" for _ in ids)
            refreshed = self._db.execute(
                f"SELECT * FROM schedules WHERE id IN ({placeholders})", ids
            ).fetchall()
            return [_row_to_schedule(row) for row in refreshed]

        return await self._run(op)

    async def agent_created_since(self, session_id: str, since: float) -> int:
        """Agent schedules one session has written since an instant, in any state.

        Counted from the table rather than from memory, so the hourly budget survives
        the daemon restarts that routinely happen inside it. Every state counts,
        dismissed and cancelled included: the budget bounds asking, and a request a
        person had to dismiss was still a request they had to read.
        """

        def op() -> int:
            row = self._db.execute(
                "SELECT COUNT(*) AS total FROM schedules WHERE author_kind=?"
                " AND author_session_id=? AND created_at>=?",
                (AUTHOR_AGENT, session_id, since),
            ).fetchone()
            return int(row["total"] if row else 0)

        return await self._run(op)

    async def active_agent_schedules(self, project_id: str) -> list[dict[str, Any]]:
        """Agent-authored rows in one Project that are armed or waiting on a person.

        What the per-Project standing cap counts: a schedule an agent wrote and nobody
        has yet disarmed. Paused, dismissed, cancelled, expired and completed rows do
        not count, because none of them can start anything.
        """

        def op() -> list[dict[str, Any]]:
            rows = self._db.execute(
                "SELECT * FROM schedules WHERE project_id=? AND author_kind=?"
                " AND (enabled=1 OR approval_state=?) ORDER BY created_at",
                (project_id, AUTHOR_AGENT, APPROVAL_PENDING),
            ).fetchall()
            return [_row_to_schedule(row) for row in rows]

        return await self._run(op)

    async def authored_by(self, session_id: str, run_id: str = "") -> list[dict[str, Any]]:
        """Every row one agent session (or the conversation it is running) wrote.

        Matched on the session *or* the run, because both are the same author: a pane
        that resumed its conversation has a new session id and the same run, and a
        pane that rolled its conversation has the same session id and a new run.
        """

        def op() -> list[dict[str, Any]]:
            if run_id:
                rows = self._db.execute(
                    "SELECT * FROM schedules WHERE author_kind=? AND"
                    " (author_session_id=? OR author_run_id=?) ORDER BY created_at",
                    (AUTHOR_AGENT, session_id, run_id),
                ).fetchall()
            else:
                rows = self._db.execute(
                    "SELECT * FROM schedules WHERE author_kind=? AND author_session_id=?"
                    " ORDER BY created_at",
                    (AUTHOR_AGENT, session_id),
                ).fetchall()
            return [_row_to_schedule(row) for row in rows]

        return await self._run(op)

    async def approval_requests(
        self, *, project_id: str | None = None, decided_since: float | None = None
    ) -> list[dict[str, Any]]:
        """Agent schedules waiting on a person, plus the ones decided recently.

        The Fleet Queue reads this. Recent decisions are included so a card does not
        vanish the instant it is pressed on another device, which reads as the
        approval having failed.
        """

        def op() -> list[dict[str, Any]]:
            clauses = ["(approval_state=?"]
            params: list[Any] = [APPROVAL_PENDING]
            if decided_since is not None:
                clauses[0] += " OR (approval_state IN (?,?) AND decided_at>=?)"
                params.extend([APPROVAL_APPROVED, APPROVAL_DISMISSED, decided_since])
            clauses[0] += ")"
            if project_id:
                clauses.append("project_id=?")
                params.append(project_id)
            rows = self._db.execute(
                "SELECT * FROM schedules WHERE author_kind='agent' AND "
                + " AND ".join(clauses)
                + " ORDER BY created_at DESC LIMIT 200",
                params,
            ).fetchall()
            return [_row_to_schedule(row) for row in rows]

        return await self._run(op)

    async def delete(self, schedule_id: str) -> bool:
        def op() -> bool:
            cursor = self._db.execute("DELETE FROM schedules WHERE id=?", (schedule_id,))
            self._db.execute("DELETE FROM schedule_runs WHERE schedule_id=?", (schedule_id,))
            self._db.commit()
            return bool(cursor.rowcount)

        return await self._run(op)

    async def get(self, schedule_id: str) -> dict[str, Any] | None:
        def op() -> dict[str, Any] | None:
            row = self._db.execute(
                "SELECT * FROM schedules WHERE id=?", (schedule_id,)
            ).fetchone()
            return _row_to_schedule(row) if row else None

        return await self._run(op)

    async def list_schedules(self, project_id: str | None = None) -> list[dict[str, Any]]:
        def op() -> list[dict[str, Any]]:
            if project_id:
                rows = self._db.execute(
                    "SELECT * FROM schedules WHERE project_id=? ORDER BY created_at",
                    (project_id,),
                ).fetchall()
            else:
                rows = self._db.execute(
                    "SELECT * FROM schedules ORDER BY project_id, created_at"
                ).fetchall()
            return [_row_to_schedule(row) for row in rows]

        return await self._run(op)

    async def due(self, now: float) -> list[dict[str, Any]]:
        def op() -> list[dict[str, Any]]:
            rows = self._db.execute(
                "SELECT * FROM schedules WHERE enabled=1 AND next_fire_at IS NOT NULL"
                " AND next_fire_at<=? ORDER BY next_fire_at",
                (now,),
            ).fetchall()
            return [_row_to_schedule(row) for row in rows]

        return await self._run(op)

    # -- runs -----------------------------------------------------------------

    async def claim_run(
        self,
        *,
        schedule_id: str,
        project_id: str,
        fire_key: str,
        due_at: float | None,
        origin: str = "timer",
        now: float | None = None,
    ) -> dict[str, Any]:
        """Insert the run row that claims one occurrence.

        Raises `ScheduleConflict` when the occurrence already has a row. That is
        the whole restart-safety story: the claim is written before anything is
        spawned, so a duplicate sweep loses at the index rather than at a
        best-effort in-memory guard that a restart erases.
        """
        moment = time.time() if now is None else now
        run_id = f"run_{uuid.uuid4().hex[:16]}"

        def op() -> dict[str, Any]:
            try:
                self._db.execute(
                    "INSERT INTO schedule_runs(id,schedule_id,project_id,fire_key,due_at,"
                    "started_at,outcome,origin) VALUES(?,?,?,?,?,?,'started',?)",
                    (run_id, schedule_id, project_id, fire_key, due_at, moment, origin),
                )
            except sqlite3.IntegrityError as exc:
                self._db.rollback()
                raise ScheduleConflict(fire_key) from exc
            self._db.commit()
            row = self._db.execute(
                "SELECT * FROM schedule_runs WHERE id=?", (run_id,)
            ).fetchone()
            return _row_to_run(row)

        return await self._run(op)

    async def finish_run(
        self,
        run_id: str,
        *,
        outcome: str,
        reason: str = "",
        session_id: str | None = None,
        queued_messages: int = 0,
        now: float | None = None,
        track_session: bool = True,
    ) -> dict[str, Any] | None:
        """Settle a run row and mirror its outcome onto the schedule.

        `track_session` is False for a resume that delivered into a pane it did not
        start: the run row still names that pane, but `last_session_id` is what the
        overlap and concurrency guards count as *this schedule's* live session, and a
        pane somebody else opened is not one.
        """
        moment = time.time() if now is None else now

        def op() -> dict[str, Any] | None:
            self._db.execute(
                "UPDATE schedule_runs SET outcome=?,reason=?,session_id=?,queued_messages=?,"
                "finished_at=? WHERE id=?",
                (outcome, reason, session_id, queued_messages, moment, run_id),
            )
            row = self._db.execute(
                "SELECT * FROM schedule_runs WHERE id=?", (run_id,)
            ).fetchone()
            if row is not None:
                tracked = "last_session_id=?," if track_session else ""
                params: list[Any] = [float(row["started_at"]), outcome, reason]
                if track_session:
                    params.append(session_id)
                params.extend([moment, str(row["schedule_id"])])
                self._db.execute(
                    "UPDATE schedules SET last_fire_at=?,last_outcome=?,last_reason=?,"
                    f"{tracked}updated_at=? WHERE id=?",
                    params,
                )
            self._db.commit()
            return _row_to_run(row) if row is not None else None

        return await self._run(op)

    async def runs(
        self, schedule_id: str | None = None, *, project_id: str | None = None, limit: int = 20
    ) -> list[dict[str, Any]]:
        bounded = max(1, min(int(limit), 200))

        def op() -> list[dict[str, Any]]:
            if schedule_id:
                rows = self._db.execute(
                    "SELECT * FROM schedule_runs WHERE schedule_id=? ORDER BY started_at DESC"
                    " LIMIT ?",
                    (schedule_id, bounded),
                ).fetchall()
            elif project_id:
                rows = self._db.execute(
                    "SELECT * FROM schedule_runs WHERE project_id=? ORDER BY started_at DESC"
                    " LIMIT ?",
                    (project_id, bounded),
                ).fetchall()
            else:
                rows = self._db.execute(
                    "SELECT * FROM schedule_runs ORDER BY started_at DESC LIMIT ?",
                    (bounded,),
                ).fetchall()
            return [_row_to_run(row) for row in rows]

        return await self._run(op)

    async def runs_since(self, schedule_id: str, since: float) -> int:
        """Spawned runs for one schedule since an instant, for the daily cap.

        Counts what started a turn - `spawned`, and `delivered` (a prompt queued into
        a conversation that was already open still makes an agent work). A skip or a
        failure cost nothing and must not consume the budget that exists to bound
        what actually runs.
        """

        def op() -> int:
            row = self._db.execute(
                "SELECT COUNT(*) AS total FROM schedule_runs WHERE schedule_id=?"
                " AND started_at>=? AND outcome IN ('spawned','delivered','started')",
                (schedule_id, since),
            ).fetchone()
            return int(row["total"] if row else 0)

        return await self._run(op)

    async def active_sessions(self) -> list[str]:
        """Session ids the most recent spawn of each schedule produced."""

        def op() -> list[str]:
            rows = self._db.execute(
                "SELECT last_session_id FROM schedules WHERE last_session_id IS NOT NULL"
            ).fetchall()
            return [str(row["last_session_id"]) for row in rows]

        return await self._run(op)

    async def prune(self, retention_days: int) -> None:
        cutoff = time.time() - max(1, int(retention_days)) * 86_400

        def op() -> None:
            self._db.execute("DELETE FROM schedule_runs WHERE started_at<?", (cutoff,))
            self._db.commit()

        await self._run(op)
