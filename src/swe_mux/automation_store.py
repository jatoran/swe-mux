from __future__ import annotations

import asyncio
import hashlib
import json
import re
import sqlite3
import time
import uuid
from collections.abc import Callable, Sequence
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

AUTOMATION_SCHEMA_VERSION = 8

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
    # Ranked attention items and the behaviour samples mined from them. Both are
    # derived from annotations and notifications that outlive them, so neither
    # earns the durable window: an item is a routing decision about a moment.
    "attention_items",
    "attention_feedback",
)
_DURABLE_PRUNE_TABLES = (
    "automation_annotations",
    "experience_entries",
    "session_lineage",
    "scan_timeline_records",
    "scan_timeline_boundaries",
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
  cost_usd REAL, latency_ms INTEGER, provider_name TEXT, finish_reason TEXT,
  response_content_type TEXT, response_content_length INTEGER,
  http_status INTEGER, retryable INTEGER, error TEXT, response_excerpt TEXT,
  created_at REAL NOT NULL,
  completed_at REAL
);
CREATE TABLE IF NOT EXISTS automation_checkpoints (
  key TEXT PRIMARY KEY, value_json TEXT NOT NULL, updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS automation_budget_ledger (
  id TEXT PRIMARY KEY, day TEXT NOT NULL, rule_id TEXT NOT NULL,
  project_id TEXT, agent_run_id TEXT,
  requested_model TEXT, input_tokens INTEGER NOT NULL, output_tokens INTEGER NOT NULL,
  cost_usd REAL NOT NULL, observer_call_id TEXT, created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_budget_day_rule
  ON automation_budget_ledger(day,rule_id);
CREATE INDEX IF NOT EXISTS idx_budget_project_run
  ON automation_budget_ledger(day,project_id,agent_run_id);
CREATE TABLE IF NOT EXISTS automation_notifications (
  id TEXT PRIMARY KEY, agent_run_id TEXT, session_id TEXT, rule_id TEXT,
  kind TEXT NOT NULL, title TEXT NOT NULL, message TEXT NOT NULL,
  severity TEXT NOT NULL, evidence_json TEXT NOT NULL,
  read_at REAL, created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_notifications_unread
  ON automation_notifications(read_at,created_at DESC);
CREATE TABLE IF NOT EXISTS attention_items (
  id TEXT PRIMARY KEY, incident_key TEXT NOT NULL UNIQUE,
  project_id TEXT, session_id TEXT, agent_run_id TEXT,
  incident_class TEXT NOT NULL, kinds_json TEXT NOT NULL,
  title TEXT NOT NULL, summary TEXT NOT NULL, action TEXT,
  channel TEXT NOT NULL, cost_to_resolve TEXT NOT NULL,
  score REAL NOT NULL, confidence REAL NOT NULL,
  evidence_json TEXT NOT NULL, contributions INTEGER NOT NULL DEFAULT 1,
  narration TEXT, narration_status TEXT NOT NULL DEFAULT 'none',
  suppressed_reason TEXT, state TEXT NOT NULL DEFAULT 'open',
  budget_day TEXT, delivered_at REAL, resolved_at REAL,
  created_at REAL NOT NULL, updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_attention_state
  ON attention_items(state,created_at DESC);
CREATE INDEX IF NOT EXISTS idx_attention_channel
  ON attention_items(channel,created_at DESC);
CREATE INDEX IF NOT EXISTS idx_attention_run
  ON attention_items(agent_run_id,created_at DESC);
CREATE INDEX IF NOT EXISTS idx_attention_budget
  ON attention_items(budget_day,created_at DESC);
CREATE TABLE IF NOT EXISTS attention_feedback (
  id TEXT PRIMARY KEY, item_id TEXT NOT NULL, incident_class TEXT NOT NULL,
  channel TEXT NOT NULL, action TEXT NOT NULL, latency_seconds REAL,
  created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_attention_feedback_class
  ON attention_feedback(incident_class,created_at DESC);
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
CREATE TABLE IF NOT EXISTS scan_timeline_runs (
  agent_run_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, project_id TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 0, enabled_at REAL, disabled_at REAL,
  last_scan_at REAL, last_source_ts REAL, updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_scan_runs_session
  ON scan_timeline_runs(session_id,updated_at DESC);
CREATE TABLE IF NOT EXISTS scan_timeline_records (
  id TEXT PRIMARY KEY, session_id TEXT NOT NULL, agent_run_id TEXT NOT NULL,
  project_id TEXT NOT NULL, t0 REAL NOT NULL, t1 REAL NOT NULL,
  trigger TEXT NOT NULL, record_json TEXT NOT NULL, input_hash TEXT NOT NULL,
  requested_model TEXT NOT NULL, resolved_model TEXT, generation_id TEXT,
  input_tokens INTEGER NOT NULL DEFAULT 0, output_tokens INTEGER NOT NULL DEFAULT 0,
  cost_usd REAL NOT NULL DEFAULT 0, created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_scan_records_session
  ON scan_timeline_records(session_id,created_at ASC);
CREATE INDEX IF NOT EXISTS idx_scan_records_run
  ON scan_timeline_records(agent_run_id,created_at ASC);
CREATE INDEX IF NOT EXISTS idx_scan_records_project
  ON scan_timeline_records(project_id,created_at ASC);
CREATE TABLE IF NOT EXISTS scan_timeline_boundaries (
  id TEXT PRIMARY KEY, session_id TEXT NOT NULL, previous_run_id TEXT NOT NULL,
  next_run_id TEXT NOT NULL, reason TEXT NOT NULL, created_at REAL NOT NULL,
  UNIQUE(session_id,previous_run_id,next_run_id)
);
CREATE INDEX IF NOT EXISTS idx_scan_boundaries_session
  ON scan_timeline_boundaries(session_id,created_at ASC);
CREATE TABLE IF NOT EXISTS scan_timeline_metrics (
  id INTEGER PRIMARY KEY CHECK(id=1), record_reads INTEGER NOT NULL DEFAULT 0,
  rehydrations INTEGER NOT NULL DEFAULT 0
);
INSERT OR IGNORE INTO scan_timeline_metrics(id,record_reads,rehydrations) VALUES(1,0,0);
-- A full-session scan is a multi-minute job whose outcome is the only record of
-- which parts of a conversation were reached. Holding it in process memory made
-- a daemon restart report `idle` for a job that had actually stopped half way,
-- with no reason and no chunk counts.
CREATE TABLE IF NOT EXISTS scan_timeline_backfills (
  agent_run_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, project_id TEXT NOT NULL,
  state TEXT NOT NULL, processed_chunks INTEGER NOT NULL DEFAULT 0,
  total_chunks INTEGER NOT NULL DEFAULT 0, created_records INTEGER NOT NULL DEFAULT 0,
  failed_chunks INTEGER NOT NULL DEFAULT 0, skipped_chunks INTEGER NOT NULL DEFAULT 0,
  reason TEXT, started_at REAL NOT NULL, updated_at REAL NOT NULL, completed_at REAL
);
CREATE INDEX IF NOT EXISTS idx_scan_backfills_session
  ON scan_timeline_backfills(session_id,updated_at DESC);
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
        observer_calls = self._columns("automation_observer_calls")
        observer_diagnostics = {
            "provider_name": "TEXT",
            "finish_reason": "TEXT",
            "response_content_type": "TEXT",
            "response_content_length": "INTEGER",
            "http_status": "INTEGER",
            "retryable": "INTEGER",
            # A response the provider billed for but we refused to store used to
            # leave only the exception text behind, so "the model returned
            # something invalid" could never be turned into "*this* is what it
            # returned". Bounded, and only written on a failure path.
            "response_excerpt": "TEXT",
        }
        for column, sql_type in observer_diagnostics.items():
            if observer_calls and column not in observer_calls:
                self._db.execute(
                    f"ALTER TABLE automation_observer_calls ADD COLUMN {column} {sql_type}"
                )
        budget_ledger = self._columns("automation_budget_ledger")
        for column in ("project_id", "agent_run_id"):
            if budget_ledger and column not in budget_ledger:
                self._db.execute(f"ALTER TABLE automation_budget_ledger ADD COLUMN {column} TEXT")

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

    @staticmethod
    def _annotation_filter(
        *,
        agent_run_id: str | None,
        agent_run_ids: Sequence[str] | None,
        project_id: str | None,
        tag: str | None,
        since: float | None,
    ) -> tuple[str, list[Any]] | None:
        """Build the shared WHERE clause the Findings reads filter on.

        Returns ``None`` when the filter can match nothing by construction — an
        empty ``agent_run_ids`` set, which is a session scope resolved to zero
        runs. That is a genuine "no findings", distinct from an unfiltered read,
        so both callers short-circuit to an empty result rather than emitting a
        malformed ``IN ()``.

        ``session_id`` is deliberately not a predicate here even though the
        column exists: only the declared-vs-verified detector populates it, so a
        session scope is resolved to the session's run ids by the caller and
        matched through ``agent_run_ids``. A project-anchored finding with a null
        run (doc-debt, provenance) is therefore absent from session scope by
        construction, which is the behaviour the UI exclusion notice explains.
        """
        if agent_run_ids is not None and not agent_run_ids:
            return None
        sql = ""
        args: list[Any] = []
        if agent_run_id:
            sql += " AND agent_run_id=?"
            args.append(agent_run_id)
        if agent_run_ids is not None:
            placeholders = ",".join("?" for _ in agent_run_ids)
            sql += f" AND agent_run_id IN ({placeholders})"
            args.extend(agent_run_ids)
        if project_id:
            sql += " AND project_id=?"
            args.append(project_id)
        if tag:
            sql += " AND tag=?"
            args.append(tag)
        if since is not None:
            sql += " AND created_at>=?"
            args.append(since)
        return sql, args

    async def annotations(
        self,
        *,
        agent_run_id: str | None = None,
        agent_run_ids: Sequence[str] | None = None,
        project_id: str | None = None,
        tag: str | None = None,
        since: float | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        built = self._annotation_filter(
            agent_run_id=agent_run_id,
            agent_run_ids=agent_run_ids,
            project_id=project_id,
            tag=tag,
            since=since,
        )
        if built is None:
            return []
        clause, args = built
        sql = "SELECT * FROM automation_annotations WHERE 1=1" + clause
        sql += " ORDER BY created_at DESC LIMIT ?"
        args = [*args, max(1, min(limit, 1000))]

        def op() -> list[dict[str, Any]]:
            rows = self._db.execute(sql, args).fetchall()
            return [dict(row) for row in rows]

        return await self._run(op)

    async def annotation_tag_counts(
        self,
        *,
        agent_run_id: str | None = None,
        agent_run_ids: Sequence[str] | None = None,
        project_id: str | None = None,
        since: float | None = None,
    ) -> dict[str, int]:
        """Per-tag totals in the current scope, ignoring the tag chip itself.

        The Findings pane draws its chips from this so "no findings" reads apart
        from "buried under provenance edges": the counts honour project, session
        (via ``agent_run_ids``), and ``since``, but never the selected tag, which
        would otherwise collapse every chip to its own count.
        """
        built = self._annotation_filter(
            agent_run_id=agent_run_id,
            agent_run_ids=agent_run_ids,
            project_id=project_id,
            tag=None,
            since=since,
        )
        if built is None:
            return {}
        clause, args = built
        sql = (
            "SELECT tag, COUNT(*) AS total FROM automation_annotations "
            "WHERE 1=1" + clause + " GROUP BY tag"
        )

        def op() -> dict[str, int]:
            rows = self._db.execute(sql, args).fetchall()
            return {str(row["tag"]): int(row["total"]) for row in rows}

        return await self._run(op)

    async def annotation(self, identity: str) -> dict[str, Any] | None:
        """One annotation by id, for a consumer that only holds an `annotation_created`."""

        def op() -> dict[str, Any] | None:
            row = self._db.execute(
                "SELECT * FROM automation_annotations WHERE id=?", (identity,)
            ).fetchone()
            return dict(row) if row else None

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
        provider_name: str | None = None,
        finish_reason: str | None = None,
        response_content_type: str | None = None,
        response_content_length: int | None = None,
        http_status: int | None = None,
        retryable: bool | None = None,
        error: str | None = None,
        response_excerpt: str | None = None,
    ) -> None:
        def op() -> None:
            self._db.execute(
                "UPDATE automation_observer_calls SET status=?,resolved_model=?,generation_id=?,"
                "input_tokens=?,output_tokens=?,cost_usd=?,latency_ms=?,provider_name=?,"
                "finish_reason=?,response_content_type=?,response_content_length=?,"
                "http_status=?,retryable=?,error=?,response_excerpt=?,completed_at=? "
                "WHERE id=?",
                (
                    status,
                    resolved_model,
                    generation_id,
                    input_tokens,
                    output_tokens,
                    cost_usd,
                    latency_ms,
                    provider_name,
                    finish_reason,
                    response_content_type,
                    response_content_length,
                    http_status,
                    None if retryable is None else int(retryable),
                    error,
                    response_excerpt,
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
        project_id: str | None = None,
        agent_run_id: str | None = None,
    ) -> None:
        def op() -> None:
            self._db.execute(
                "INSERT INTO automation_budget_ledger"
                "(id,day,rule_id,project_id,agent_run_id,requested_model,input_tokens,"
                "output_tokens,cost_usd,observer_call_id,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    str(uuid.uuid4()),
                    time.strftime("%Y-%m-%d", time.gmtime()),
                    rule_id,
                    project_id,
                    agent_run_id,
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

    async def spend(
        self,
        *,
        rule_id: str | None = None,
        project_id: str | None = None,
        agent_run_id: str | None = None,
    ) -> dict[str, float | int]:
        sql = (
            "SELECT COALESCE(SUM(input_tokens+output_tokens),0) tokens,"
            "COALESCE(SUM(cost_usd),0) cost FROM automation_budget_ledger WHERE day=?"
        )
        args: list[Any] = [time.strftime("%Y-%m-%d", time.gmtime())]
        if rule_id:
            sql += " AND rule_id=?"
            args.append(rule_id)
        if project_id:
            sql += " AND project_id=?"
            args.append(project_id)
        if agent_run_id:
            sql += " AND agent_run_id=?"
            args.append(agent_run_id)

        def op() -> dict[str, float | int]:
            row = self._db.execute(sql, args).fetchone()
            return {"tokens": int(row["tokens"]), "cost_usd": float(row["cost"])}

        return await self._run(op)

    async def set_scan_run_enabled(
        self,
        *,
        agent_run_id: str,
        session_id: str,
        project_id: str,
        enabled: bool,
    ) -> dict[str, Any]:
        """Persist authorization for exactly one provider conversation.

        A successor run has a different primary key and therefore starts disabled.
        This is the storage-level guarantee behind the per-run toggle.
        """
        now = time.time()

        def op() -> dict[str, Any]:
            self._db.execute(
                "INSERT INTO scan_timeline_runs"
                "(agent_run_id,session_id,project_id,enabled,enabled_at,disabled_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?) ON CONFLICT(agent_run_id) DO UPDATE SET "
                "session_id=excluded.session_id,project_id=excluded.project_id,"
                "enabled=excluded.enabled,enabled_at=CASE WHEN excluded.enabled=1 "
                "THEN COALESCE(scan_timeline_runs.enabled_at,excluded.enabled_at) "
                "ELSE scan_timeline_runs.enabled_at END,"
                "disabled_at=CASE WHEN excluded.enabled=0 THEN excluded.disabled_at ELSE NULL END,"
                "updated_at=excluded.updated_at",
                (
                    agent_run_id,
                    session_id,
                    project_id,
                    int(enabled),
                    now if enabled else None,
                    None if enabled else now,
                    now,
                ),
            )
            self._db.commit()
            row = self._db.execute(
                "SELECT * FROM scan_timeline_runs WHERE agent_run_id=?", (agent_run_id,)
            ).fetchone()
            return dict(row)

        return await self._run(op)

    async def scan_run(self, agent_run_id: str) -> dict[str, Any] | None:
        def op() -> dict[str, Any] | None:
            row = self._db.execute(
                "SELECT * FROM scan_timeline_runs WHERE agent_run_id=?", (agent_run_id,)
            ).fetchone()
            return dict(row) if row else None

        return await self._run(op)

    async def save_scan_record(
        self,
        *,
        session_id: str,
        agent_run_id: str,
        project_id: str,
        t0: float,
        t1: float,
        trigger: str,
        record: dict[str, Any],
        input_hash: str,
        requested_model: str,
        resolved_model: str | None,
        generation_id: str | None,
        input_tokens: int,
        output_tokens: int,
        cost_usd: float,
    ) -> dict[str, Any]:
        identity = str(uuid.uuid4())
        created = time.time()
        encoded = json.dumps(record, separators=(",", ":"), ensure_ascii=False)

        def op() -> None:
            self._db.execute(
                "INSERT INTO scan_timeline_records"
                "(id,session_id,agent_run_id,project_id,t0,t1,trigger,record_json,input_hash,"
                "requested_model,resolved_model,generation_id,input_tokens,output_tokens,cost_usd,"
                "created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    identity,
                    session_id,
                    agent_run_id,
                    project_id,
                    t0,
                    t1,
                    trigger,
                    encoded,
                    input_hash,
                    requested_model,
                    resolved_model,
                    generation_id,
                    input_tokens,
                    output_tokens,
                    cost_usd,
                    created,
                ),
            )
            self._db.execute(
                "UPDATE scan_timeline_runs SET last_scan_at=?,"
                "last_source_ts=MAX(COALESCE(last_source_ts,?),?),updated_at=? "
                "WHERE agent_run_id=?",
                (created, t1, t1, created, agent_run_id),
            )
            self._db.commit()

        await self._run(op)
        return {"id": identity, **record, "created_at": created}

    async def scan_records(
        self,
        *,
        session_id: str | None = None,
        agent_run_id: str | None = None,
        project_id: str | None = None,
        since_t1: float | None = None,
        until_t1: float | None = None,
        triggers: Sequence[str] | None = None,
        exclude_triggers: Sequence[str] | None = None,
        work_phase: str | None = None,
        approach_status: str | None = None,
        blocked_only: bool = False,
        target_fragment: str | None = None,
        newest_first: bool = False,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        """Scan records for one scope, filtered in SQL rather than after the read.

        The semantic predicates read `record_json` through `json_extract`
        deliberately: filtering a decoded page in Python would make `limit` mean
        "rows scanned" rather than "rows returned", so a blocked-only page would
        silently come back short of a full page and a caller could not tell that
        from the end of the run.

        `since_t1` is exclusive so it composes as a monitoring cursor: feeding
        back the newest `t1` a caller has already seen returns strictly newer
        records and never repeats the boundary one.

        `newest_first` is what a bounded read wants and the default (oldest
        first) is what the derivations in `scan_consumers` require, so the
        default ordering is unchanged.
        """
        sql = "SELECT * FROM scan_timeline_records WHERE 1=1"
        args: list[Any] = []
        for field, value in (
            ("session_id", session_id),
            ("agent_run_id", agent_run_id),
            ("project_id", project_id),
        ):
            if value:
                sql += f" AND {field}=?"
                args.append(value)
        if since_t1 is not None:
            sql += " AND t1>?"
            args.append(float(since_t1))
        if until_t1 is not None:
            sql += " AND t1<=?"
            args.append(float(until_t1))
        if triggers:
            wanted = sorted({str(item) for item in triggers})
            sql += f" AND trigger IN ({','.join('?' for _ in wanted)})"
            args.extend(wanted)
        if exclude_triggers:
            unwanted = sorted({str(item) for item in exclude_triggers})
            sql += f" AND trigger NOT IN ({','.join('?' for _ in unwanted)})"
            args.extend(unwanted)
        if work_phase:
            sql += " AND json_extract(record_json,'$.work_phase')=?"
            args.append(str(work_phase))
        if approach_status:
            sql += " AND json_extract(record_json,'$.approach_status')=?"
            args.append(str(approach_status))
        if blocked_only:
            # `none` is the schema's "not blocked"; NULL is a record that predates
            # the field or had it withheld. Neither is a live blocker.
            sql += (
                " AND COALESCE(json_extract(record_json,'$.blocked_on'),'none')"
                " NOT IN ('','none')"
            )
        if target_fragment:
            # `target` is a JSON array, so the substring test runs against its
            # serialized form. A path fragment cannot collide with the array
            # punctuation, which is the only other thing in that text.
            sql += " AND lower(COALESCE(json_extract(record_json,'$.target'),'')) LIKE ?"
            args.append(f"%{str(target_fragment).casefold()}%")
        order = "DESC" if newest_first else "ASC"
        sql += f" ORDER BY t0 {order},created_at {order} LIMIT ?"
        args.append(max(1, min(limit, 2000)))

        def op() -> list[dict[str, Any]]:
            rows = self._db.execute(sql, args).fetchall()
            return [{**dict(row), **json.loads(str(row["record_json"]))} for row in rows]

        return await self._run(op)

    async def scan_record(self, record_id: str) -> dict[str, Any] | None:
        def op() -> dict[str, Any] | None:
            row = self._db.execute(
                "SELECT * FROM scan_timeline_records WHERE id=?", (record_id,)
            ).fetchone()
            return {**dict(row), **json.loads(str(row["record_json"]))} if row else None

        return await self._run(op)

    async def add_scan_boundary(
        self,
        *,
        session_id: str,
        previous_run_id: str,
        next_run_id: str,
        reason: str,
        created_at: float | None = None,
    ) -> None:
        def op() -> None:
            self._db.execute(
                "INSERT OR IGNORE INTO scan_timeline_boundaries"
                "(id,session_id,previous_run_id,next_run_id,reason,created_at) "
                "VALUES(?,?,?,?,?,?)",
                (
                    str(uuid.uuid4()),
                    session_id,
                    previous_run_id,
                    next_run_id,
                    reason,
                    created_at or time.time(),
                ),
            )
            self._db.commit()

        await self._run(op)

    async def scan_boundaries(self, session_id: str) -> list[dict[str, Any]]:
        def op() -> list[dict[str, Any]]:
            rows = self._db.execute(
                "SELECT * FROM scan_timeline_boundaries WHERE session_id=? ORDER BY created_at ASC",
                (session_id,),
            ).fetchall()
            return [dict(row) for row in rows]

        return await self._run(op)

    async def scan_project_spend(self, project_id: str) -> dict[str, float | int]:
        return await self.spend(rule_id="builtin:scan-timeline", project_id=project_id)

    async def scan_run_spend(self, agent_run_id: str) -> dict[str, float | int]:
        return await self.spend(rule_id="builtin:scan-timeline", agent_run_id=agent_run_id)

    async def save_scan_backfill(self, state: dict[str, Any]) -> None:
        """Upsert one full-session scan job, so its outcome survives a restart."""
        now = time.time()

        def op() -> None:
            self._db.execute(
                "INSERT INTO scan_timeline_backfills"
                "(agent_run_id,session_id,project_id,state,processed_chunks,total_chunks,"
                "created_records,failed_chunks,skipped_chunks,reason,started_at,updated_at,"
                "completed_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(agent_run_id) DO UPDATE SET "
                "session_id=excluded.session_id,project_id=excluded.project_id,"
                "state=excluded.state,processed_chunks=excluded.processed_chunks,"
                "total_chunks=excluded.total_chunks,created_records=excluded.created_records,"
                "failed_chunks=excluded.failed_chunks,skipped_chunks=excluded.skipped_chunks,"
                "reason=excluded.reason,started_at=excluded.started_at,"
                "updated_at=excluded.updated_at,completed_at=excluded.completed_at",
                (
                    str(state["agent_run_id"]),
                    str(state["session_id"]),
                    str(state["project_id"]),
                    str(state["state"]),
                    int(state.get("processed_chunks") or 0),
                    int(state.get("total_chunks") or 0),
                    int(state.get("created_records") or 0),
                    int(state.get("failed_chunks") or 0),
                    int(state.get("skipped_chunks") or 0),
                    state.get("reason"),
                    float(state.get("started_at") or now),
                    now,
                    state.get("completed_at"),
                ),
            )
            self._db.commit()

        await self._run(op)

    async def scan_backfill(self, agent_run_id: str) -> dict[str, Any] | None:
        def op() -> dict[str, Any] | None:
            row = self._db.execute(
                "SELECT * FROM scan_timeline_backfills WHERE agent_run_id=?", (agent_run_id,)
            ).fetchone()
            return dict(row) if row else None

        return await self._run(op)

    async def interrupt_running_scan_backfills(self) -> int:
        """Close out jobs whose daemon died mid-run.

        Nothing else can, and a row left at `running` reads as a live job that
        will never make progress, which is worse than an honest `partial`.
        """
        now = time.time()

        def op() -> int:
            cursor = self._db.execute(
                "UPDATE scan_timeline_backfills SET state='partial',"
                "reason=COALESCE(reason,'the daemon stopped before the scan finished'),"
                "updated_at=?,completed_at=COALESCE(completed_at,?) WHERE state='running'",
                (now, now),
            )
            self._db.commit()
            return int(cursor.rowcount or 0)

        return await self._run(op)

    async def note_scan_record_read(self, *, rehydrated: bool) -> dict[str, float | int]:
        def op() -> dict[str, float | int]:
            self._db.execute(
                "UPDATE scan_timeline_metrics SET record_reads=record_reads+1,"
                "rehydrations=rehydrations+? WHERE id=1",
                (int(rehydrated),),
            )
            self._db.commit()
            row = self._db.execute(
                "SELECT record_reads,rehydrations FROM scan_timeline_metrics WHERE id=1"
            ).fetchone()
            reads = int(row["record_reads"])
            rehydrations = int(row["rehydrations"])
            return {
                "record_reads": reads,
                "rehydrations": rehydrations,
                "rehydration_rate": rehydrations / reads if reads else 0.0,
            }

        return await self._run(op)

    async def scan_metrics(self) -> dict[str, float | int]:
        def op() -> dict[str, float | int]:
            row = self._db.execute(
                "SELECT record_reads,rehydrations FROM scan_timeline_metrics WHERE id=1"
            ).fetchone()
            reads = int(row["record_reads"])
            rehydrations = int(row["rehydrations"])
            return {
                "record_reads": reads,
                "rehydrations": rehydrations,
                "rehydration_rate": rehydrations / reads if reads else 0.0,
            }

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

    async def mark_all_notifications(self, read: bool) -> int:
        """Read/unread every notification at once, returning how many changed.

        The attention inbox is otherwise append-only until the retention window ages
        a row out, so a run of records the user has judged irrelevant can only be
        cleared one click at a time. This is that clear.
        """

        def op() -> int:
            if read:
                cursor = self._db.execute(
                    "UPDATE automation_notifications SET read_at=? WHERE read_at IS NULL",
                    (time.time(),),
                )
            else:
                cursor = self._db.execute(
                    "UPDATE automation_notifications SET read_at=NULL WHERE read_at IS NOT NULL"
                )
            self._db.commit()
            return int(cursor.rowcount)

        return await self._run(op)

    async def upsert_attention_item(
        self,
        *,
        incident_key: str,
        project_id: str | None,
        session_id: str | None,
        agent_run_id: str | None,
        incident_class: str,
        kinds: list[str],
        title: str,
        summary: str,
        action: str | None,
        channel: str,
        cost_to_resolve: str,
        score: float,
        confidence: float,
        evidence: list[dict[str, Any]],
        suppressed_reason: str | None = None,
        budget_day: str | None = None,
        delivered_at: float | None = None,
    ) -> dict[str, Any]:
        """Create one ranked incident, or fold a further finding into the existing one.

        The incident is the unit of interruption: several detectors reporting the
        same underlying event share one ``incident_key`` and therefore one budget
        slot. A merge deliberately does **not** re-route the incident — the channel
        and the budget slot were decided when it first appeared, and re-deciding on
        every contributing finding is how one event becomes four interruptions.
        """
        identity = str(uuid.uuid4())
        now = time.time()

        def op() -> dict[str, Any]:
            row = self._db.execute(
                "SELECT * FROM attention_items WHERE incident_key=?", (incident_key,)
            ).fetchone()
            if row is not None:
                merged_kinds = sorted({*json.loads(row["kinds_json"]), *kinds})
                merged_evidence = [*json.loads(row["evidence_json"]), *evidence][-200:]
                self._db.execute(
                    "UPDATE attention_items SET kinds_json=?,evidence_json=?,title=?,"
                    "summary=?,action=?,score=?,confidence=?,contributions=contributions+1,"
                    "updated_at=? WHERE id=?",
                    (
                        json.dumps(merged_kinds, separators=(",", ":")),
                        json.dumps(merged_evidence, separators=(",", ":")),
                        title,
                        summary,
                        action,
                        max(float(row["score"]), score),
                        max(float(row["confidence"]), confidence),
                        now,
                        row["id"],
                    ),
                )
                self._db.commit()
                updated = self._db.execute(
                    "SELECT * FROM attention_items WHERE id=?", (row["id"],)
                ).fetchone()
                return {**_attention_row(updated), "created": False}
            self._db.execute(
                "INSERT INTO attention_items"
                "(id,incident_key,project_id,session_id,agent_run_id,incident_class,"
                "kinds_json,title,summary,action,channel,cost_to_resolve,score,confidence,"
                "evidence_json,contributions,narration,narration_status,suppressed_reason,"
                "state,budget_day,delivered_at,resolved_at,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,NULL,'none',?,'open',?,?,NULL,?,?)",
                (
                    identity,
                    incident_key,
                    project_id,
                    session_id,
                    agent_run_id,
                    incident_class,
                    json.dumps(sorted(set(kinds)), separators=(",", ":")),
                    title,
                    summary,
                    action,
                    channel,
                    cost_to_resolve,
                    score,
                    confidence,
                    json.dumps(evidence[-200:], separators=(",", ":")),
                    suppressed_reason,
                    budget_day,
                    delivered_at,
                    now,
                    now,
                ),
            )
            self._db.commit()
            created_row = self._db.execute(
                "SELECT * FROM attention_items WHERE id=?", (identity,)
            ).fetchone()
            return {**_attention_row(created_row), "created": True}

        return await self._run(op)

    async def attention_items(
        self,
        *,
        state: str | None = None,
        channel: str | None = None,
        since: float | None = None,
        include_suppressed: bool = True,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM attention_items WHERE 1=1"
        args: list[Any] = []
        if state:
            sql += " AND state=?"
            args.append(state)
        if channel:
            sql += " AND channel=?"
            args.append(channel)
        if since is not None:
            sql += " AND created_at>=?"
            args.append(since)
        if not include_suppressed:
            sql += " AND suppressed_reason IS NULL"
        sql += " ORDER BY score DESC, created_at DESC LIMIT ?"
        args.append(max(1, min(limit, 1000)))

        def op() -> list[dict[str, Any]]:
            return [_attention_row(row) for row in self._db.execute(sql, args).fetchall()]

        return await self._run(op)

    async def attention_item_by_key(self, incident_key: str) -> dict[str, Any] | None:
        def op() -> dict[str, Any] | None:
            row = self._db.execute(
                "SELECT * FROM attention_items WHERE incident_key=?", (incident_key,)
            ).fetchone()
            return _attention_row(row) if row else None

        return await self._run(op)

    async def attention_item(self, item_id: str) -> dict[str, Any] | None:
        def op() -> dict[str, Any] | None:
            row = self._db.execute(
                "SELECT * FROM attention_items WHERE id=?", (item_id,)
            ).fetchone()
            return _attention_row(row) if row else None

        return await self._run(op)

    async def update_attention_item(
        self,
        item_id: str,
        *,
        channel: str | None = None,
        state: str | None = None,
        narration: str | None = None,
        narration_status: str | None = None,
        suppressed_reason: str | None = None,
        delivered_at: float | None = None,
        resolved_at: float | None = None,
    ) -> dict[str, Any] | None:
        assignments: list[str] = []
        args: list[Any] = []
        for column, value in (
            ("channel", channel),
            ("state", state),
            ("narration", narration),
            ("narration_status", narration_status),
            ("suppressed_reason", suppressed_reason),
            ("delivered_at", delivered_at),
            ("resolved_at", resolved_at),
        ):
            if value is not None:
                assignments.append(f"{column}=?")
                args.append(value)
        if not assignments:
            return await self.attention_item(item_id)
        assignments.append("updated_at=?")
        args.extend([time.time(), item_id])

        def op() -> dict[str, Any] | None:
            self._db.execute(
                f"UPDATE attention_items SET {','.join(assignments)} WHERE id=?", args
            )
            self._db.commit()
            row = self._db.execute(
                "SELECT * FROM attention_items WHERE id=?", (item_id,)
            ).fetchone()
            return _attention_row(row) if row else None

        return await self._run(op)

    async def attention_interrupts_used(self, day: str) -> int:
        """Interrupt slots already spent on one calendar day, counted per incident."""

        def op() -> int:
            row = self._db.execute(
                "SELECT COUNT(*) count FROM attention_items "
                "WHERE budget_day=? AND delivered_at IS NOT NULL",
                (day,),
            ).fetchone()
            return int(row["count"])

        return await self._run(op)

    async def record_attention_feedback(
        self,
        *,
        item_id: str,
        incident_class: str,
        channel: str,
        action: str,
        latency_seconds: float | None,
    ) -> dict[str, Any]:
        identity = str(uuid.uuid4())
        created = time.time()

        def op() -> None:
            self._db.execute(
                "INSERT INTO attention_feedback"
                "(id,item_id,incident_class,channel,action,latency_seconds,created_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (identity, item_id, incident_class, channel, action, latency_seconds, created),
            )
            self._db.commit()

        await self._run(op)
        return {
            "id": identity,
            "item_id": item_id,
            "incident_class": incident_class,
            "channel": channel,
            "action": action,
            "latency_seconds": latency_seconds,
            "created_at": created,
        }

    async def attention_feedback_stats(self, since: float) -> list[dict[str, Any]]:
        """Per class/channel act-versus-dismiss counts, the input to rule mining."""

        def op() -> list[dict[str, Any]]:
            rows = self._db.execute(
                "SELECT incident_class,channel,action,COUNT(*) count,"
                "AVG(latency_seconds) mean_latency FROM attention_feedback "
                "WHERE created_at>=? GROUP BY incident_class,channel,action",
                (since,),
            ).fetchall()
            return [dict(row) for row in rows]

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

    async def spend_breakdown(self, *, days: int = 7) -> dict[str, Any]:
        """Observer spend split by the automation that caused it.

        The dashboard could only ever answer "what did automation cost today" in total, which
        is the one number that cannot be acted on: turning something off requires knowing
        which something. This groups the same ledger `spend()` reads, so the per-rule figures
        add up to the headline exactly rather than approximating it from a truncated sample of
        recent calls.

        The ledger is the source rather than the call log because a call that failed after the
        provider billed for its input still writes a ledger row, and a breakdown that omitted
        those would under-report precisely the automation worth turning off.
        """
        today = time.strftime("%Y-%m-%d", time.gmtime())
        window = max(1, min(int(days), 365))
        start = time.strftime("%Y-%m-%d", time.gmtime(time.time() - (window - 1) * 86400))

        def op() -> dict[str, Any]:
            rows = self._db.execute(
                "SELECT rule_id,"
                "COUNT(*) calls,"
                "COALESCE(SUM(input_tokens+output_tokens),0) tokens,"
                "COALESCE(SUM(cost_usd),0) cost,"
                "COALESCE(SUM(CASE WHEN day=? THEN 1 ELSE 0 END),0) today_calls,"
                "COALESCE(SUM(CASE WHEN day=? THEN input_tokens+output_tokens ELSE 0 END),0)"
                " today_tokens,"
                "COALESCE(SUM(CASE WHEN day=? THEN cost_usd ELSE 0 END),0) today_cost,"
                "MAX(created_at) last_at "
                "FROM automation_budget_ledger WHERE day>=? "
                "GROUP BY rule_id ORDER BY cost DESC,calls DESC",
                (today, today, today, start),
            ).fetchall()
            models = self._db.execute(
                "SELECT rule_id,requested_model,COUNT(*) calls "
                "FROM automation_budget_ledger WHERE day>=? AND requested_model IS NOT NULL "
                "GROUP BY rule_id,requested_model ORDER BY calls DESC",
                (start,),
            ).fetchall()
            by_rule: dict[str, list[str]] = {}
            for row in models:
                by_rule.setdefault(str(row["rule_id"]), []).append(str(row["requested_model"]))
            return {
                "rules": [
                    {
                        "rule_id": str(row["rule_id"]),
                        "calls": int(row["calls"]),
                        "tokens": int(row["tokens"]),
                        "cost_usd": float(row["cost"]),
                        "today_calls": int(row["today_calls"]),
                        "today_tokens": int(row["today_tokens"]),
                        "today_cost_usd": float(row["today_cost"]),
                        "models": by_rule.get(str(row["rule_id"]), []),
                        "last_at": float(row["last_at"] or 0),
                    }
                    for row in rows
                ]
            }

        result = await self._run(op)
        rules: list[dict[str, Any]] = result["rules"]
        result["days"] = window
        result["start_day"] = start
        result["today"] = today
        result["totals"] = {
            key: sum(rule[key] for rule in rules)
            for key in ("calls", "tokens", "today_calls", "today_tokens")
        } | {
            key: round(sum(rule[key] for rule in rules), 6)
            for key in ("cost_usd", "today_cost_usd")
        }
        return result

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

    async def checkpoints_with_prefix(self, prefix: str) -> list[tuple[str, dict[str, Any]]]:
        """Every checkpoint under a namespace, for work that must survive a restart.

        The daemon restarts on every reload and redeploy while its sessions keep
        running, so anything scheduled only in memory is lost there. A namespace
        scan is how that work is found again on the way back up.
        """
        pattern = prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"

        def op() -> list[tuple[str, dict[str, Any]]]:
            rows = self._db.execute(
                "SELECT key,value_json FROM automation_checkpoints "
                "WHERE key LIKE ? ESCAPE '\\' ORDER BY key",
                (pattern,),
            ).fetchall()
            items: list[tuple[str, dict[str, Any]]] = []
            for row in rows:
                try:
                    value = json.loads(row["value_json"])
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict):
                    items.append((str(row["key"]), value))
            return items

        return await self._run(op)

    async def clear_checkpoint(self, key: str) -> None:
        def op() -> None:
            self._db.execute("DELETE FROM automation_checkpoints WHERE key=?", (key,))
            self._db.commit()

        await self._run(op)

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
            self._db.execute("DELETE FROM automation_checkpoints WHERE updated_at<?", (cutoff,))
            self._db.commit()

        await self._run(op)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._executor.submit(self._db.close).result()
        self._executor.shutdown(wait=True)


def _attention_row(row: sqlite3.Row) -> dict[str, Any]:
    """Decode one stored incident, keeping the JSON columns as their parsed shape."""
    item = dict(row)
    item["kinds"] = json.loads(item.pop("kinds_json"))
    item["evidence"] = json.loads(item.pop("evidence_json"))
    return item


def _normalize_error(value: str) -> str:
    value = re.sub(r"[A-Fa-f0-9]{8,}", "#", value.casefold())
    value = re.sub(r"\d+", "#", value)
    return " ".join(value.split())[:2000]
