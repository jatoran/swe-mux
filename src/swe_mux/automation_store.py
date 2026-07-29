from __future__ import annotations

import asyncio
import hashlib
import json
import re
import sqlite3
import time
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, TypeVar

from .sqlite_store import (
    connect_or_quarantine,
    database_operation_lock,
    run_sqlite_operation,
    write_schema_version,
)

T = TypeVar("T")

AUTOMATION_SCHEMA_VERSION = 3

# Floor for the second retention window (see `AutomationStore.prune`). Derived
# knowledge outlives the operational trail that produced it.
DURABLE_RETENTION_MIN_DAYS = 365
# Every growing table is named here. `automation_model_cache` is deliberately
# absent: CHECK(id=1) already bounds it to a single row. `project_cards` is
# absent for the same reason — one row per registered project, replaced in
# place, and it is a cache whose validity is decided by its source fingerprint
# rather than by age (see `project_card.py`).
_OPERATIONAL_PRUNE_TABLES = (
    "automation_firings",
    "automation_action_results",
    "automation_observer_calls",
    "automation_notifications",
    "automation_budget_ledger",
    "observer_batches",
)
_DURABLE_PRUNE_TABLES = (
    "automation_annotations",
    "experience_entries",
    "session_lineage",
)

AUTOMATION_SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS automation_annotations (
  id TEXT PRIMARY KEY, agent_run_id TEXT, project_id TEXT, session_id TEXT,
  tag TEXT NOT NULL, content TEXT NOT NULL, source_event_seq INTEGER,
  evidence_json TEXT, dedupe_key TEXT,
  rule_id TEXT, rule_revision TEXT, provenance TEXT NOT NULL,
  requested_model TEXT, resolved_model TEXT, generation_id TEXT,
  input_tokens INTEGER NOT NULL DEFAULT 0, output_tokens INTEGER NOT NULL DEFAULT 0,
  cost_usd REAL, confidence REAL, created_at REAL NOT NULL,
  CHECK(agent_run_id IS NOT NULL OR project_id IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS idx_annotations_run
  ON automation_annotations(agent_run_id,created_at DESC);
CREATE INDEX IF NOT EXISTS idx_annotations_project
  ON automation_annotations(project_id,created_at DESC);
CREATE INDEX IF NOT EXISTS idx_annotations_tag
  ON automation_annotations(tag,created_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_annotations_dedupe
  ON automation_annotations(dedupe_key);
CREATE TABLE IF NOT EXISTS automation_firings (
  id TEXT PRIMARY KEY, event_seq INTEGER NOT NULL, event_type TEXT NOT NULL,
  agent_run_id TEXT, session_id TEXT, rule_id TEXT NOT NULL,
  rule_revision TEXT NOT NULL, chain_id TEXT NOT NULL, chain_depth INTEGER NOT NULL,
  status TEXT NOT NULL, shadow INTEGER NOT NULL DEFAULT 0,
  condition_trace_json TEXT NOT NULL, error TEXT, created_at REAL NOT NULL,
  completed_at REAL, UNIQUE(event_seq,rule_id,rule_revision)
);
CREATE INDEX IF NOT EXISTS idx_firings_rule
  ON automation_firings(rule_id,created_at DESC);
CREATE TABLE IF NOT EXISTS automation_action_results (
  id TEXT PRIMARY KEY, firing_id TEXT NOT NULL, action_index INTEGER NOT NULL,
  kind TEXT NOT NULL, status TEXT NOT NULL, detail_json TEXT NOT NULL,
  error TEXT, created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS automation_observer_calls (
  id TEXT PRIMARY KEY, firing_id TEXT NOT NULL, rule_id TEXT NOT NULL,
  status TEXT NOT NULL, requested_model TEXT, resolved_model TEXT,
  generation_id TEXT, input_hash TEXT NOT NULL, input_bytes INTEGER NOT NULL,
  input_tokens INTEGER NOT NULL DEFAULT 0, output_tokens INTEGER NOT NULL DEFAULT 0,
  cost_usd REAL, latency_ms INTEGER, error TEXT, created_at REAL NOT NULL,
  completed_at REAL
);
CREATE TABLE IF NOT EXISTS automation_checkpoints (
  key TEXT PRIMARY KEY, value_json TEXT NOT NULL, updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS automation_budget_ledger (
  id TEXT PRIMARY KEY, day TEXT NOT NULL, rule_id TEXT NOT NULL,
  requested_model TEXT, input_tokens INTEGER NOT NULL, output_tokens INTEGER NOT NULL,
  cost_usd REAL NOT NULL, observer_call_id TEXT, created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_budget_day_rule
  ON automation_budget_ledger(day,rule_id);
CREATE TABLE IF NOT EXISTS automation_notifications (
  id TEXT PRIMARY KEY, agent_run_id TEXT, session_id TEXT, rule_id TEXT,
  kind TEXT NOT NULL, title TEXT NOT NULL, message TEXT NOT NULL,
  severity TEXT NOT NULL, evidence_json TEXT NOT NULL,
  read_at REAL, created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_notifications_unread
  ON automation_notifications(read_at,created_at DESC);
CREATE TABLE IF NOT EXISTS automation_model_cache (
  id INTEGER PRIMARY KEY CHECK(id=1), models_json TEXT NOT NULL,
  fetched_at REAL NOT NULL, error TEXT
);
CREATE TABLE IF NOT EXISTS project_cards (
  project_id TEXT PRIMARY KEY, project_root TEXT NOT NULL,
  fingerprint TEXT NOT NULL, card_json TEXT NOT NULL,
  schema_version INTEGER NOT NULL, requested_model TEXT, resolved_model TEXT,
  input_tokens INTEGER NOT NULL DEFAULT 0, output_tokens INTEGER NOT NULL DEFAULT 0,
  cost_usd REAL, created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS session_lineage (
  id TEXT PRIMARY KEY, parent_run_id TEXT NOT NULL, child_run_id TEXT NOT NULL,
  relation TEXT NOT NULL, metadata_json TEXT NOT NULL, created_at REAL NOT NULL,
  UNIQUE(parent_run_id,child_run_id,relation)
);
CREATE TABLE IF NOT EXISTS experience_entries (
  id TEXT PRIMARY KEY, project_scope_id TEXT, backend TEXT,
  error_fingerprint TEXT NOT NULL, error_summary TEXT NOT NULL,
  resolution_summary TEXT NOT NULL, source_run_id TEXT NOT NULL,
  confidence REAL, created_at REAL NOT NULL,
  UNIQUE(error_fingerprint,source_run_id)
);
CREATE INDEX IF NOT EXISTS idx_experience_fingerprint
  ON experience_entries(error_fingerprint,created_at DESC);
CREATE TABLE IF NOT EXISTS observer_batches (
  id TEXT PRIMARY KEY, kind TEXT NOT NULL, status TEXT NOT NULL,
  selection_json TEXT NOT NULL, preview_json TEXT NOT NULL,
  calls INTEGER NOT NULL DEFAULT 0, tokens INTEGER NOT NULL DEFAULT 0,
  cost_usd REAL NOT NULL DEFAULT 0, error TEXT,
  created_at REAL NOT NULL, completed_at REAL
);
CREATE INDEX IF NOT EXISTS idx_action_firing
  ON automation_action_results(firing_id);
CREATE INDEX IF NOT EXISTS idx_observer_firing
  ON automation_observer_calls(firing_id);
CREATE INDEX IF NOT EXISTS idx_observer_created
  ON automation_observer_calls(created_at);
CREATE INDEX IF NOT EXISTS idx_lineage_child
  ON session_lineage(child_run_id);
"""


def _tune_connection(db: sqlite3.Connection) -> None:
    """Per-connection pragmas: NORMAL sync (crash-safe under WAL, no per-commit fsync)."""
    db.execute("PRAGMA synchronous=NORMAL")
    db.execute("PRAGMA busy_timeout=5000")
    db.execute("PRAGMA cache_size=-16000")
    db.execute("PRAGMA mmap_size=268435456")


class AutomationStore:
    """SQLite store whose every operation runs on one dedicated worker thread.

    The connection is created on, and only ever touched by, a single-worker
    executor thread. That worker serializes DB work in submission order, so each
    method's statements run atomically without an ``asyncio.Lock`` and never block
    the aiohttp event loop.
    """

    _db: sqlite3.Connection

    def __init__(self, path: Path) -> None:
        self._path = path
        self._operation_lock = database_operation_lock(path)
        self._closed = False
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mux-automation-db")
        self._executor.submit(self._connect).result()

    def _connect(self) -> None:
        # Confined to the single worker thread (queries via _run, close via the
        # executor), so there is never concurrent access. check_same_thread=False
        # additionally tolerates benign cross-thread introspection (tests reading
        # ``_db`` directly, a fallback close) without weakening that guarantee.
        with self._operation_lock:
            self._db = connect_or_quarantine(self._path, self._open)
            # Migrate first: the schema script creates indexes over columns a
            # legacy table does not have yet, so running it against an
            # un-migrated database fails before the migration can fix it.
            self._migrate_schema()
            self._db.executescript(AUTOMATION_SCHEMA)
            write_schema_version(self._db, "automation", AUTOMATION_SCHEMA_VERSION)
            self._db.commit()

    def _open(self) -> sqlite3.Connection:
        db = sqlite3.connect(self._path, check_same_thread=False)
        db.row_factory = sqlite3.Row
        _tune_connection(db)
        return db

    def _columns(self, table: str) -> set[str]:
        return {
            str(row["name"]) for row in self._db.execute(f"PRAGMA table_info({table})").fetchall()
        }

    def _migrate_schema(self) -> None:
        """Bring a database created by an older build up to the current schema.

        ``CREATE TABLE IF NOT EXISTS`` no-ops on an existing table, so without a
        migration path a new column exists only in fresh databases and every
        upgrade-in-place fails on the first insert that names it. This store had
        no migration mechanism at all; it does now, and every schema change after
        this one belongs here.
        """
        annotations = self._columns("automation_annotations")
        if annotations and "project_id" not in annotations:
            # `agent_run_id` has to become nullable so a project-scoped detector
            # (doc debt has no run to anchor to) can write a finding, and ALTER
            # cannot relax NOT NULL — so this one is a rebuild rather than a
            # column add. Gated on the column being absent, therefore one-shot.
            self._db.execute("ALTER TABLE automation_annotations RENAME TO annotations_legacy")
            self._db.executescript(AUTOMATION_SCHEMA)
            self._db.execute(
                "INSERT INTO automation_annotations"
                "(id,agent_run_id,session_id,tag,content,source_event_seq,rule_id,"
                "rule_revision,provenance,requested_model,resolved_model,generation_id,"
                "input_tokens,output_tokens,cost_usd,confidence,created_at) "
                "SELECT id,agent_run_id,session_id,tag,content,source_event_seq,rule_id,"
                "rule_revision,provenance,requested_model,resolved_model,generation_id,"
                "input_tokens,output_tokens,cost_usd,confidence,created_at "
                "FROM annotations_legacy"
            )
            self._db.execute("DROP TABLE annotations_legacy")

    async def _run(self, fn: Callable[[], T]) -> T:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self._executor, run_sqlite_operation, self._db, self._operation_lock, fn
        )

    async def create_firing(
        self,
        *,
        event_seq: int,
        event_type: str,
        agent_run_id: str | None,
        session_id: str | None,
        rule_id: str,
        rule_revision: str,
        chain_id: str,
        chain_depth: int,
        shadow: bool,
        trace: list[dict[str, Any]],
    ) -> str | None:
        identity = str(uuid.uuid4())

        def op() -> str | None:
            try:
                self._db.execute(
                    "INSERT INTO automation_firings"
                    "(id,event_seq,event_type,agent_run_id,session_id,rule_id,rule_revision,"
                    "chain_id,chain_depth,status,shadow,condition_trace_json,created_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,'running',?,?,?)",
                    (
                        identity,
                        event_seq,
                        event_type,
                        agent_run_id,
                        session_id,
                        rule_id,
                        rule_revision,
                        chain_id,
                        chain_depth,
                        int(shadow),
                        json.dumps(trace, separators=(",", ":")),
                        time.time(),
                    ),
                )
                self._db.commit()
            except sqlite3.IntegrityError:
                self._db.rollback()
                return None
            return identity

        return await self._run(op)

    async def finish_firing(self, firing_id: str, status: str, error: str | None = None) -> None:
        def op() -> None:
            self._db.execute(
                "UPDATE automation_firings SET status=?,error=?,completed_at=? WHERE id=?",
                (status, error, time.time(), firing_id),
            )
            self._db.commit()

        await self._run(op)

    async def action_result(
        self,
        firing_id: str,
        index: int,
        kind: str,
        status: str,
        detail: dict[str, Any],
        error: str | None = None,
    ) -> None:
        def op() -> None:
            self._db.execute(
                # Explicit column lists everywhere: a positional INSERT breaks the
                # moment a column is added, and the redeploy flow keeps a
                # roll-back-able previous bundle that would then hit "table has N
                # columns but N-1 values were supplied" on every write.
                "INSERT INTO automation_action_results"
                "(id,firing_id,action_index,kind,status,detail_json,error,created_at) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (
                    str(uuid.uuid4()),
                    firing_id,
                    index,
                    kind,
                    status,
                    json.dumps(detail, separators=(",", ":")),
                    error,
                    time.time(),
                ),
            )
            self._db.commit()

        await self._run(op)

    async def create_annotation(
        self,
        *,
        agent_run_id: str | None = None,
        project_id: str | None = None,
        session_id: str | None = None,
        tag: str,
        content: str,
        source_event_seq: int | None = None,
        evidence: list[dict[str, Any]] | None = None,
        dedupe_key: str | None = None,
        rule_id: str | None = None,
        rule_revision: str | None = None,
        provenance: str,
        requested_model: str | None = None,
        resolved_model: str | None = None,
        generation_id: str | None = None,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cost_usd: float | None = None,
        confidence: float | None = None,
    ) -> dict[str, Any]:
        """Persist one annotation, anchored to a run *or* a project.

        Two anchors because deterministic consumers need both shapes: a loop
        finding belongs to one agent run, while doc debt is a property of the
        project and has no run to attach to. ``evidence`` is a list, not a single
        `source_event_seq`, because a finding whose case rests on a *set* of facts
        (a fingerprint repeated three times) is not traceable through one pointer.
        ``dedupe_key`` makes a re-running detector idempotent: a conflicting write
        returns the existing row tagged ``duplicate`` instead of a second finding.
        """
        if not agent_run_id and not project_id:
            raise ValueError("an annotation must be anchored to an agent run or a project")
        identity = str(uuid.uuid4())
        created = time.time()
        evidence_json = (
            json.dumps(evidence, separators=(",", ":")) if evidence is not None else None
        )
        values = (
            identity,
            agent_run_id,
            project_id,
            session_id,
            tag,
            content,
            source_event_seq,
            evidence_json,
            dedupe_key,
            rule_id,
            rule_revision,
            provenance,
            requested_model,
            resolved_model,
            generation_id,
            input_tokens,
            output_tokens,
            cost_usd,
            confidence,
            created,
        )

        def op() -> dict[str, Any] | None:
            try:
                self._db.execute(
                    "INSERT INTO automation_annotations"
                    "(id,agent_run_id,project_id,session_id,tag,content,source_event_seq,"
                    "evidence_json,dedupe_key,rule_id,rule_revision,provenance,requested_model,"
                    "resolved_model,generation_id,input_tokens,output_tokens,cost_usd,"
                    "confidence,created_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    values,
                )
                self._db.commit()
            except sqlite3.IntegrityError:
                # Expected-duplicate path: roll back before returning so the
                # connection never keeps the writer slot on an open transaction.
                self._db.rollback()
                row = (
                    self._db.execute(
                        "SELECT * FROM automation_annotations WHERE dedupe_key=?", (dedupe_key,)
                    ).fetchone()
                    if dedupe_key is not None
                    else None
                )
                if row is None:
                    raise
                return {**dict(row), "duplicate": True}
            return None

        if (existing := await self._run(op)) is not None:
            return existing
        return {
            "id": identity,
            "agent_run_id": agent_run_id,
            "project_id": project_id,
            "session_id": session_id,
            "tag": tag,
            "content": content,
            "source_event_seq": source_event_seq,
            "evidence_json": evidence_json,
            "dedupe_key": dedupe_key,
            "rule_id": rule_id,
            "rule_revision": rule_revision,
            "provenance": provenance,
            "requested_model": requested_model,
            "resolved_model": resolved_model,
            "generation_id": generation_id,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost_usd": cost_usd,
            "confidence": confidence,
            "created_at": created,
        }

    async def annotations(
        self,
        *,
        agent_run_id: str | None = None,
        project_id: str | None = None,
        tag: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM automation_annotations WHERE 1=1"
        args: list[Any] = []
        if agent_run_id:
            sql += " AND agent_run_id=?"
            args.append(agent_run_id)
        if project_id:
            sql += " AND project_id=?"
            args.append(project_id)
        if tag:
            sql += " AND tag=?"
            args.append(tag)
        sql += " ORDER BY created_at DESC LIMIT ?"
        args.append(max(1, min(limit, 1000)))

        def op() -> list[dict[str, Any]]:
            rows = self._db.execute(sql, args).fetchall()
            return [dict(row) for row in rows]

        return await self._run(op)

    async def recent_annotation(
        self, agent_run_id: str, tag: str, since: float
    ) -> dict[str, Any] | None:
        def op() -> dict[str, Any] | None:
            row = self._db.execute(
                "SELECT * FROM automation_annotations WHERE agent_run_id=? AND tag=? "
                "AND created_at>=? ORDER BY created_at DESC LIMIT 1",
                (agent_run_id, tag, since),
            ).fetchone()
            return dict(row) if row else None

        return await self._run(op)

    async def observer_started(
        self,
        *,
        firing_id: str,
        rule_id: str,
        model: str,
        input_hash: str,
        input_bytes: int,
    ) -> str:
        identity = str(uuid.uuid4())

        def op() -> None:
            self._db.execute(
                "INSERT INTO automation_observer_calls"
                "(id,firing_id,rule_id,status,requested_model,input_hash,input_bytes,created_at) "
                "VALUES(?,?,?,'running',?,?,?,?)",
                (identity, firing_id, rule_id, model, input_hash, input_bytes, time.time()),
            )
            self._db.commit()

        await self._run(op)
        return identity

    async def observer_finished(
        self,
        call_id: str,
        *,
        status: str,
        resolved_model: str | None = None,
        generation_id: str | None = None,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cost_usd: float | None = None,
        latency_ms: int | None = None,
        error: str | None = None,
    ) -> None:
        def op() -> None:
            self._db.execute(
                "UPDATE automation_observer_calls SET status=?,resolved_model=?,generation_id=?,"
                "input_tokens=?,output_tokens=?,cost_usd=?,latency_ms=?,error=?,completed_at=? "
                "WHERE id=?",
                (
                    status,
                    resolved_model,
                    generation_id,
                    input_tokens,
                    output_tokens,
                    cost_usd,
                    latency_ms,
                    error,
                    time.time(),
                    call_id,
                ),
            )
            self._db.commit()

        await self._run(op)

    async def add_spend(
        self,
        *,
        rule_id: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cost_usd: float,
        call_id: str,
    ) -> None:
        def op() -> None:
            self._db.execute(
                "INSERT INTO automation_budget_ledger"
                "(id,day,rule_id,requested_model,input_tokens,output_tokens,cost_usd,"
                "observer_call_id,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    str(uuid.uuid4()),
                    time.strftime("%Y-%m-%d", time.gmtime()),
                    rule_id,
                    model,
                    input_tokens,
                    output_tokens,
                    cost_usd,
                    call_id,
                    time.time(),
                ),
            )
            self._db.commit()

        await self._run(op)

    async def reconcile_spend(self, call_id: str, cost_usd: float) -> None:
        def op() -> None:
            self._db.execute(
                "UPDATE automation_observer_calls SET cost_usd=? WHERE id=?",
                (cost_usd, call_id),
            )
            self._db.execute(
                "UPDATE automation_budget_ledger SET cost_usd=? WHERE observer_call_id=?",
                (cost_usd, call_id),
            )
            self._db.commit()

        await self._run(op)

    async def spend(self, *, rule_id: str | None = None) -> dict[str, float | int]:
        sql = (
            "SELECT COALESCE(SUM(input_tokens+output_tokens),0) tokens,"
            "COALESCE(SUM(cost_usd),0) cost FROM automation_budget_ledger WHERE day=?"
        )
        args: list[Any] = [time.strftime("%Y-%m-%d", time.gmtime())]
        if rule_id:
            sql += " AND rule_id=?"
            args.append(rule_id)

        def op() -> dict[str, float | int]:
            row = self._db.execute(sql, args).fetchone()
            return {"tokens": int(row["tokens"]), "cost_usd": float(row["cost"])}

        return await self._run(op)

    async def project_card(self, project_id: str) -> dict[str, Any] | None:
        """The cached card row for one project, whatever its validity.

        Validity is the caller's decision, not the store's: a row is only usable
        when its `fingerprint` still matches the project's current documentation
        (`project_card.py`). Returning the row regardless keeps that rule in one
        place instead of splitting it across a query.
        """

        def op() -> dict[str, Any] | None:
            row = self._db.execute(
                "SELECT * FROM project_cards WHERE project_id=?", (project_id,)
            ).fetchone()
            return dict(row) if row else None

        return await self._run(op)

    async def save_project_card(
        self,
        *,
        project_id: str,
        project_root: str,
        fingerprint: str,
        card: dict[str, Any],
        schema_version: int,
        requested_model: str | None,
        resolved_model: str | None,
        input_tokens: int,
        output_tokens: int,
        cost_usd: float | None,
    ) -> None:
        """Replace one project's card. One row per project, never appended to."""
        payload = json.dumps(card, separators=(",", ":"), default=str)

        def op() -> None:
            self._db.execute(
                "INSERT OR REPLACE INTO project_cards"
                "(project_id,project_root,fingerprint,card_json,schema_version,requested_model,"
                "resolved_model,input_tokens,output_tokens,cost_usd,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    project_id,
                    project_root,
                    fingerprint,
                    payload,
                    schema_version,
                    requested_model,
                    resolved_model,
                    input_tokens,
                    output_tokens,
                    cost_usd,
                    time.time(),
                ),
            )
            self._db.commit()

        await self._run(op)

    async def observer_call_count(self, since: float, *, rule_id: str | None = None) -> int:
        sql = "SELECT COUNT(*) count FROM automation_observer_calls WHERE created_at>=?"
        args: list[Any] = [since]
        if rule_id:
            sql += " AND rule_id=?"
            args.append(rule_id)

        def op() -> int:
            row = self._db.execute(sql, args).fetchone()
            return int(row["count"])

        return await self._run(op)

    async def notify(
        self,
        *,
        agent_run_id: str | None,
        session_id: str | None,
        rule_id: str | None,
        kind: str,
        title: str,
        message: str,
        severity: str = "info",
        evidence: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        identity = str(uuid.uuid4())
        created = time.time()

        def op() -> None:
            self._db.execute(
                "INSERT INTO automation_notifications"
                "(id,agent_run_id,session_id,rule_id,kind,title,message,severity,"
                "evidence_json,read_at,created_at) VALUES(?,?,?,?,?,?,?,?,?,NULL,?)",
                (
                    identity,
                    agent_run_id,
                    session_id,
                    rule_id,
                    kind,
                    title,
                    message,
                    severity,
                    json.dumps(evidence or [], separators=(",", ":")),
                    created,
                ),
            )
            self._db.commit()

        await self._run(op)
        return {
            "id": identity,
            "agent_run_id": agent_run_id,
            "session_id": session_id,
            "rule_id": rule_id,
            "kind": kind,
            "title": title,
            "message": message,
            "severity": severity,
            "evidence": evidence or [],
            "created_at": created,
        }

    async def notifications(
        self, *, unread: bool = False, limit: int = 200
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM automation_notifications"
        if unread:
            sql += " WHERE read_at IS NULL"
        sql += " ORDER BY created_at DESC LIMIT ?"

        def op() -> list[dict[str, Any]]:
            rows = self._db.execute(sql, (max(1, min(limit, 1000)),)).fetchall()
            return [{**dict(row), "evidence": json.loads(row["evidence_json"])} for row in rows]

        return await self._run(op)

    async def mark_notification(self, identity: str, read: bool) -> bool:
        def op() -> bool:
            cursor = self._db.execute(
                "UPDATE automation_notifications SET read_at=? WHERE id=?",
                (time.time() if read else None, identity),
            )
            self._db.commit()
            return bool(cursor.rowcount)

        return await self._run(op)

    async def firings(
        self, *, rule_id: str | None = None, limit: int = 200
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM automation_firings"
        args: list[Any] = []
        if rule_id:
            sql += " WHERE rule_id=?"
            args.append(rule_id)
        sql += " ORDER BY created_at DESC LIMIT ?"
        args.append(max(1, min(limit, 1000)))

        def op() -> list[dict[str, Any]]:
            rows = self._db.execute(sql, args).fetchall()
            return [
                {**dict(row), "condition_trace": json.loads(row["condition_trace_json"])}
                for row in rows
            ]

        return await self._run(op)

    async def action_results(
        self, *, firing_id: str | None = None, limit: int = 200
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM automation_action_results"
        args: list[Any] = []
        if firing_id:
            sql += " WHERE firing_id=?"
            args.append(firing_id)
        sql += " ORDER BY created_at DESC LIMIT ?"
        args.append(max(1, min(limit, 1000)))

        def op() -> list[dict[str, Any]]:
            rows = self._db.execute(sql, args).fetchall()
            return [{**dict(row), "detail": json.loads(row["detail_json"])} for row in rows]

        return await self._run(op)

    async def observer_calls(
        self, *, firing_id: str | None = None, limit: int = 200
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM automation_observer_calls"
        args: list[Any] = []
        if firing_id:
            sql += " WHERE firing_id=?"
            args.append(firing_id)
        sql += " ORDER BY created_at DESC LIMIT ?"
        args.append(max(1, min(limit, 1000)))

        def op() -> list[dict[str, Any]]:
            rows = self._db.execute(sql, args).fetchall()
            return [dict(row) for row in rows]

        return await self._run(op)

    async def dashboard(self) -> dict[str, Any]:
        def op() -> dict[str, Any]:
            calls = self._db.execute(
                "SELECT status,COUNT(*) count FROM automation_observer_calls GROUP BY status"
            ).fetchall()
            annotations = self._db.execute(
                "SELECT tag,COUNT(*) count FROM automation_annotations GROUP BY tag"
            ).fetchall()
            unread = self._db.execute(
                "SELECT COUNT(*) count FROM automation_notifications WHERE read_at IS NULL"
            ).fetchone()
            return {
                "observer_calls": {row["status"]: row["count"] for row in calls},
                "annotations": {row["tag"]: row["count"] for row in annotations},
                "unread_notifications": int(unread["count"]),
            }

        result = await self._run(op)
        result["spend_today"] = await self.spend()
        return result

    async def cache_models(self, models: list[dict[str, Any]], error: str | None = None) -> None:
        def op() -> None:
            self._db.execute(
                "INSERT INTO automation_model_cache"
                "(id,models_json,fetched_at,error) VALUES(1,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET models_json=excluded.models_json,"
                "fetched_at=excluded.fetched_at,error=excluded.error",
                (json.dumps(models, separators=(",", ":")), time.time(), error),
            )
            self._db.commit()

        await self._run(op)

    async def record_model_error(self, error: str) -> None:
        """Retain the last successful catalog timestamp while surfacing refresh failure."""

        def op() -> None:
            row = self._db.execute("SELECT id FROM automation_model_cache WHERE id=1").fetchone()
            if row:
                self._db.execute(
                    "UPDATE automation_model_cache SET error=? WHERE id=1", (error[:1000],)
                )
            else:
                self._db.execute(
                    "INSERT INTO automation_model_cache"
                    "(id,models_json,fetched_at,error) VALUES(1,'[]',0,?)",
                    (error[:1000],),
                )
            self._db.commit()

        await self._run(op)

    async def model_cache(self) -> dict[str, Any]:
        def op() -> dict[str, Any]:
            row = self._db.execute("SELECT * FROM automation_model_cache WHERE id=1").fetchone()
            if not row:
                return {"models": [], "fetched_at": None, "error": None, "stale": True}
            fetched = float(row["fetched_at"])
            return {
                "models": json.loads(row["models_json"]),
                "fetched_at": fetched,
                "error": row["error"],
                "stale": time.time() - fetched > 24 * 3600,
            }

        return await self._run(op)

    async def set_checkpoint(self, key: str, value: dict[str, Any]) -> None:
        def op() -> None:
            self._db.execute(
                "INSERT INTO automation_checkpoints(key,value_json,updated_at) VALUES(?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,"
                "updated_at=excluded.updated_at",
                (key, json.dumps(value, separators=(",", ":")), time.time()),
            )
            self._db.commit()

        await self._run(op)

    async def checkpoint(self, key: str) -> dict[str, Any] | None:
        def op() -> dict[str, Any] | None:
            row = self._db.execute(
                "SELECT value_json FROM automation_checkpoints WHERE key=?", (key,)
            ).fetchone()
            return json.loads(row["value_json"]) if row else None

        return await self._run(op)

    async def add_lineage(
        self,
        parent_run_id: str,
        child_run_id: str,
        relation: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        identity = str(uuid.uuid4())
        created = time.time()

        def op() -> dict[str, Any]:
            self._db.execute(
                "INSERT OR IGNORE INTO session_lineage VALUES(?,?,?,?,?,?)",
                (
                    identity,
                    parent_run_id,
                    child_run_id,
                    relation,
                    json.dumps(metadata or {}, separators=(",", ":")),
                    created,
                ),
            )
            self._db.commit()
            row = self._db.execute(
                "SELECT * FROM session_lineage WHERE parent_run_id=? AND child_run_id=? "
                "AND relation=?",
                (parent_run_id, child_run_id, relation),
            ).fetchone()
            assert row is not None
            return {
                "id": row["id"],
                "parent_run_id": row["parent_run_id"],
                "child_run_id": row["child_run_id"],
                "relation": row["relation"],
                "metadata": json.loads(row["metadata_json"]),
                "created_at": row["created_at"],
            }

        return await self._run(op)

    async def lineage(self, run_id: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM session_lineage"
        args: tuple[Any, ...] = ()
        if run_id:
            sql += " WHERE parent_run_id=? OR child_run_id=?"
            args = (run_id, run_id)
        sql += " ORDER BY created_at"

        def op() -> list[dict[str, Any]]:
            rows = self._db.execute(sql, args).fetchall()
            result: list[dict[str, Any]] = []
            for row in rows:
                item = dict(row)
                item["metadata"] = json.loads(item.pop("metadata_json"))
                result.append(item)
            return result

        return await self._run(op)

    async def add_experience(
        self,
        *,
        project_scope_id: str | None,
        backend: str,
        error: str,
        resolution: str,
        source_run_id: str,
        confidence: float | None,
    ) -> dict[str, Any]:
        fingerprint = hashlib.sha256(_normalize_error(error).encode()).hexdigest()[:24]
        identity = str(uuid.uuid4())
        created = time.time()

        def op() -> None:
            self._db.execute(
                "INSERT OR REPLACE INTO experience_entries VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    identity,
                    project_scope_id,
                    backend,
                    fingerprint,
                    error[:2000],
                    resolution[:4000],
                    source_run_id,
                    confidence,
                    created,
                ),
            )
            self._db.commit()

        await self._run(op)
        return {
            "id": identity,
            "project_scope_id": project_scope_id,
            "backend": backend,
            "error_fingerprint": fingerprint,
            "error_summary": error[:2000],
            "resolution_summary": resolution[:4000],
            "source_run_id": source_run_id,
            "confidence": confidence,
            "created_at": created,
        }

    async def experiences(
        self,
        *,
        query: str = "",
        project_scope_id: str | None = None,
        error: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Browse experiences, or retrieve by exact normalized error signature.

        `error` is the retrieval path a `prior-resolution` annotation must use:
        substring matching a raw 240-character tool detail against a short
        model-written summary practically never hits, and when it does the match
        is coincidental. A usually-wrong prior resolution poisons trust in the
        whole annotation surface, so this side is equality on the fingerprint.
        """
        if error is not None:
            fingerprint = hashlib.sha256(_normalize_error(error).encode()).hexdigest()[:24]
            sql = "SELECT * FROM experience_entries WHERE error_fingerprint=?"
            args: list[Any] = [fingerprint]
        else:
            sql = (
                "SELECT * FROM experience_entries WHERE "
                "(error_summary LIKE ? OR resolution_summary LIKE ?)"
            )
            args = [f"%{query}%", f"%{query}%"]
        if project_scope_id:
            sql += " AND project_scope_id=?"
            args.append(project_scope_id)
        sql += " ORDER BY created_at DESC LIMIT ?"
        args.append(max(1, min(limit, 500)))

        def op() -> list[dict[str, Any]]:
            rows = self._db.execute(sql, args).fetchall()
            return [dict(row) for row in rows]

        return await self._run(op)

    async def create_batch(self, kind: str, selection: list[str]) -> str:
        identity = str(uuid.uuid4())

        def op() -> None:
            self._db.execute(
                "INSERT INTO observer_batches"
                "(id,kind,status,selection_json,preview_json,created_at) "
                "VALUES(?,?,'running',?,'[]',?)",
                (identity, kind, json.dumps(selection), time.time()),
            )
            self._db.commit()

        await self._run(op)
        return identity

    async def finish_batch(
        self,
        identity: str,
        *,
        status: str,
        preview: list[dict[str, Any]],
        calls: int,
        tokens: int,
        cost_usd: float,
        error: str | None = None,
    ) -> None:
        def op() -> None:
            self._db.execute(
                "UPDATE observer_batches SET status=?,preview_json=?,calls=?,tokens=?,"
                "cost_usd=?,error=?,completed_at=? WHERE id=?",
                (
                    status,
                    json.dumps(preview, separators=(",", ":")),
                    calls,
                    tokens,
                    cost_usd,
                    error,
                    time.time(),
                    identity,
                ),
            )
            self._db.commit()

        await self._run(op)

    async def batches(self, limit: int = 50) -> list[dict[str, Any]]:
        def op() -> list[dict[str, Any]]:
            rows = self._db.execute(
                "SELECT * FROM observer_batches ORDER BY created_at DESC LIMIT ?",
                (max(1, min(limit, 200)),),
            ).fetchall()
            result: list[dict[str, Any]] = []
            for row in rows:
                item = dict(row)
                item["run_ids"] = json.loads(item.pop("selection_json"))
                item["preview"] = json.loads(item.pop("preview_json"))
                result.append(item)
            return result

        return await self._run(op)

    async def prune(
        self, retention_days: int, *, durable_retention_days: int | None = None
    ) -> None:
        """Age out automation rows.

        Every table with unbounded growth is covered. Six of them previously had
        no DELETE anywhere in the codebase, so on a supervisor-preserved daemon
        (weeks of uptime is the design goal) they grew without any bound at all.

        Two windows, because the tables are not the same kind of data:
        operational records (firings, action results, observer calls,
        notifications, spend ledger, batches, rule checkpoints) age out on the
        configured automation window, while derived knowledge a user reads long
        after the run — run-note annotations, learned resolutions, branch lineage
        — is kept for a deliberately longer one.
        """
        now = time.time()
        durable_days = (
            durable_retention_days
            if durable_retention_days is not None
            else max(retention_days, DURABLE_RETENTION_MIN_DAYS)
        )
        cutoff = now - max(1, retention_days) * 86400
        durable_cutoff = now - max(1, durable_days) * 86400

        def op() -> None:
            for table in _OPERATIONAL_PRUNE_TABLES:
                self._db.execute(f"DELETE FROM {table} WHERE created_at<?", (cutoff,))
            for table in _DURABLE_PRUNE_TABLES:
                self._db.execute(f"DELETE FROM {table} WHERE created_at<?", (durable_cutoff,))
            # Checkpoint keys embed the agent run id, so every run leaves rows
            # behind permanently; they stamp updated_at rather than created_at.
            self._db.execute(
                "DELETE FROM automation_checkpoints WHERE updated_at<?", (cutoff,)
            )
            self._db.commit()

        await self._run(op)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._executor.submit(self._db.close).result()
        self._executor.shutdown(wait=True)


def _normalize_error(value: str) -> str:
    value = re.sub(r"[A-Fa-f0-9]{8,}", "#", value.casefold())
    value = re.sub(r"\d+", "#", value)
    return " ".join(value.split())[:2000]
