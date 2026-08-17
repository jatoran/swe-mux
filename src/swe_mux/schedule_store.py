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
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from .schedules import FollowUp, ScheduleSpec
from .sqlite_store import (
    connect_or_quarantine,
    database_operation_lock,
    run_sqlite_operation,
    write_schema_version,
)

SCHEDULE_SCHEMA_VERSION = 1

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
  revision INTEGER NOT NULL DEFAULT 1
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

# Outcomes a run row can settle on. `started` is the transient claim state, and a
# row left in it is a fire the daemon did not live long enough to finish - which
# is worth seeing rather than silently repairing.
OUTCOMES = ("started", "spawned", "skipped", "failed", "missed")


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
        self._executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="mux-schedule-db"
        )
        self._executor.submit(self._connect).result()

    def _connect(self) -> None:
        with self._operation_lock:
            self._db = connect_or_quarantine(self._path, self._open)
            self._db.executescript(SCHEDULE_SCHEMA)
            write_schema_version(self._db, "schedules", SCHEDULE_SCHEMA_VERSION)
            self._db.commit()

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
    ) -> dict[str, Any]:
        moment = time.time() if now is None else now
        schedule_id = f"sch_{uuid.uuid4().hex[:16]}"

        def op() -> dict[str, Any]:
            self._db.execute(
                "INSERT INTO schedules(id,project_id,project_root,label,enabled,trigger_kind,"
                "cron,interval_seconds,run_at,timezone,catch_up,overlap,backend,profile_id,"
                "cwd,session_name,prompt,follow_ups_json,daily_run_cap,next_fire_at,"
                "created_at,updated_at,revision)"
                " VALUES(?,?,?,?,1,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)",
                (
                    schedule_id,
                    project_id,
                    project_root,
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
                    moment,
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
    ) -> dict[str, Any] | None:
        """Rewrite a definition. A stale `revision` loses the race and returns None."""
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
                schedule_id,
            ]
            clause = ""
            if revision is not None:
                clause = " AND revision=?"
                params.append(revision)
            cursor = self._db.execute(
                "UPDATE schedules SET label=?,trigger_kind=?,cron=?,interval_seconds=?,run_at=?,"
                "timezone=?,catch_up=?,overlap=?,backend=?,profile_id=?,cwd=?,session_name=?,"
                "prompt=?,follow_ups_json=?,daily_run_cap=?,next_fire_at=?,updated_at=?,"
                "revision=revision+1,disabled_reason=''"
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
    ) -> dict[str, Any] | None:
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
                self._db.execute(
                    "UPDATE schedules SET last_fire_at=?,last_outcome=?,last_reason=?,"
                    "last_session_id=?,updated_at=? WHERE id=?",
                    (
                        float(row["started_at"]),
                        outcome,
                        reason,
                        session_id,
                        moment,
                        str(row["schedule_id"]),
                    ),
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

        Counts only `spawned`: a skip or a failure cost nothing and must not
        consume the budget that exists to bound what actually starts.
        """

        def op() -> int:
            row = self._db.execute(
                "SELECT COUNT(*) AS total FROM schedule_runs WHERE schedule_id=?"
                " AND started_at>=? AND outcome IN ('spawned','started')",
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
