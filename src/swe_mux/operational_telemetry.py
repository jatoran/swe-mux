from __future__ import annotations

import asyncio
import hashlib
import json
import math
import sqlite3
import time
import uuid
from bisect import bisect_left
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, TypeVar

from .event_bus import EventBus
from .models import MuxEvent
from .sqlite_store import database_operation_lock, run_sqlite_operation

T = TypeVar("T")

TELEMETRY_SCHEMA_VERSION = 2
TOOL_PARSER_VERSION = "phase2-v1"
TOOL_PARSER_VERSIONS = {
    "claude": f"claude-{TOOL_PARSER_VERSION}",
    "codex": f"codex-{TOOL_PARSER_VERSION}",
}
CODEX_KNOWN_OUTER_RECORDS = {
    "compacted",
    "inter_agent_communication_metadata",
    "session_meta",
    "turn_context",
    "world_state",
}
CODEX_KNOWN_PAYLOADS = {
    "agent_message",
    "apply_patch_approval_request",
    "context_compacted",
    "custom_tool_call",
    "custom_tool_call_output",
    "error",
    "exec_approval_request",
    "exec_command_begin",
    "exec_command_end",
    "exec_command_output_delta",
    "function_call",
    "function_call_output",
    "mcp_tool_call_end",
    "message",
    "patch_apply_end",
    "rate_limit",
    "rate_limited",
    "reasoning",
    "request_user_input",
    "sub_agent_activity",
    "task_complete",
    "task_started",
    "thread_goal_updated",
    "thread_rolled_back",
    "thread_settings_applied",
    "token_count",
    "turn_aborted",
    "user_message",
    "web_search_end",
}
RESET_EXPECTED_TOLERANCE_SECONDS = 15 * 60
RESET_UNEXPECTED_MIN_TIME_TO_EXPECTED_SECONDS = 60 * 60
RESET_CONFIRMATION_MIN_SECONDS = 5 * 60
RESET_CONFIRMATION_MAX_SECONDS = 45 * 60
RESET_UNEXPECTED_MIN_DROP_PERCENT = 20.0
RESET_UNEXPECTED_MAX_AFTER_PERCENT = 10.0

SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS process_evidence (
  identity_id TEXT PRIMARY KEY,
  pid INTEGER NOT NULL,
  creation_time REAL NOT NULL,
  session_id TEXT NOT NULL,
  agent_run_id TEXT,
  project_id TEXT,
  executable TEXT,
  command_hash TEXT NOT NULL,
  parent_pid INTEGER,
  parent_lineage_json TEXT NOT NULL DEFAULT '[]',
  job_assignment TEXT NOT NULL,
  state TEXT NOT NULL,
  reason TEXT NOT NULL,
  confidence TEXT NOT NULL,
  first_seen REAL NOT NULL,
  last_seen REAL NOT NULL,
  last_verified_at REAL,
  exited_at REAL,
  exit_evidence TEXT,
  inaccessible_count INTEGER NOT NULL DEFAULT 0,
  startup_revalidated INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_process_evidence_owner
  ON process_evidence(session_id,state,last_seen DESC);
CREATE INDEX IF NOT EXISTS idx_process_evidence_retention
  ON process_evidence(last_seen,state);

CREATE TABLE IF NOT EXISTS quota_samples (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  provider TEXT NOT NULL,
  account_id TEXT NOT NULL,
  sampled_at REAL NOT NULL,
  status TEXT NOT NULL,
  session_used REAL,
  weekly_used REAL,
  session_reset_at REAL,
  weekly_reset_at REAL,
  source TEXT,
  freshness TEXT NOT NULL,
  raw_precision INTEGER NOT NULL DEFAULT 0,
  error TEXT,
  account_active INTEGER NOT NULL,
  auth_state TEXT NOT NULL,
  UNIQUE(provider,account_id,sampled_at)
);
CREATE INDEX IF NOT EXISTS idx_quota_samples_account
  ON quota_samples(provider,account_id,sampled_at DESC);
CREATE TABLE IF NOT EXISTS quota_sample_rollups (
  provider TEXT NOT NULL,
  account_id TEXT NOT NULL,
  day TEXT NOT NULL,
  samples INTEGER NOT NULL,
  errors INTEGER NOT NULL,
  session_min REAL,
  session_max REAL,
  session_first REAL,
  session_last REAL,
  weekly_min REAL,
  weekly_max REAL,
  weekly_first REAL,
  weekly_last REAL,
  PRIMARY KEY(provider,account_id,day)
);

CREATE TABLE IF NOT EXISTS quota_reset_events (
  id TEXT PRIMARY KEY,
  provider TEXT NOT NULL,
  account_id TEXT NOT NULL,
  window TEXT NOT NULL,
  before_sample_id INTEGER NOT NULL,
  after_sample_id INTEGER NOT NULL,
  confirmation_sample_id INTEGER,
  before_value REAL NOT NULL,
  after_value REAL NOT NULL,
  expected_reset_at REAL,
  observed_at REAL NOT NULL,
  classification TEXT NOT NULL,
  confidence TEXT NOT NULL,
  confirmed INTEGER NOT NULL DEFAULT 0,
  suppression_reason TEXT,
  created_at REAL NOT NULL,
  confirmed_at REAL,
  review_status TEXT,
  reviewed_at REAL
);
CREATE INDEX IF NOT EXISTS idx_quota_resets_recent
  ON quota_reset_events(observed_at DESC);
CREATE INDEX IF NOT EXISTS idx_quota_resets_pending
  ON quota_reset_events(provider,account_id,window,confirmed,observed_at DESC);

CREATE TABLE IF NOT EXISTS quota_attributions (
  sample_id INTEGER NOT NULL,
  window TEXT NOT NULL,
  provider TEXT NOT NULL,
  account_id TEXT NOT NULL,
  interval_start REAL NOT NULL,
  interval_end REAL NOT NULL,
  quota_delta REAL NOT NULL,
  correlated_estimate REAL NOT NULL,
  correlated_low REAL NOT NULL,
  correlated_high REAL NOT NULL,
  external_estimate REAL NOT NULL,
  external_low REAL NOT NULL,
  external_high REAL NOT NULL,
  confidence TEXT NOT NULL,
  sample_gap_seconds REAL NOT NULL,
  concurrent_sessions INTEGER NOT NULL,
  provider_lag_seconds REAL NOT NULL,
  allocations_json TEXT NOT NULL,
  caveats_json TEXT NOT NULL,
  PRIMARY KEY(sample_id,window)
);
CREATE INDEX IF NOT EXISTS idx_quota_attribution_recent
  ON quota_attributions(interval_end DESC);

CREATE TABLE IF NOT EXISTS context_compactions (
  id TEXT PRIMARY KEY,
  event_seq INTEGER,
  session_id TEXT NOT NULL,
  agent_run_id TEXT,
  project_id TEXT,
  backend TEXT NOT NULL,
  model TEXT,
  observed_at REAL NOT NULL,
  source TEXT NOT NULL,
  capability TEXT NOT NULL,
  confidence TEXT NOT NULL,
  parser_version TEXT NOT NULL,
  UNIQUE(session_id,observed_at,source)
);
CREATE INDEX IF NOT EXISTS idx_context_compactions_session
  ON context_compactions(session_id,observed_at DESC);

CREATE TABLE IF NOT EXISTS tool_events (
  id TEXT PRIMARY KEY,
  event_seq INTEGER,
  source_identity TEXT NOT NULL UNIQUE,
  session_id TEXT NOT NULL,
  agent_run_id TEXT,
  project_id TEXT,
  backend TEXT NOT NULL,
  model TEXT,
  observed_at REAL NOT NULL,
  source TEXT NOT NULL,
  kind TEXT NOT NULL,
  raw_tool TEXT NOT NULL,
  taxonomy TEXT NOT NULL,
  success INTEGER,
  exit_code INTEGER,
  duration_ms REAL,
  parser_version TEXT NOT NULL,
  explicit_skill TEXT
);
CREATE INDEX IF NOT EXISTS idx_tool_events_metrics
  ON tool_events(observed_at,backend,project_id,taxonomy);

CREATE TABLE IF NOT EXISTS transcript_telemetry_coverage (
  session_id TEXT PRIMARY KEY,
  backend TEXT NOT NULL,
  project_id TEXT,
  transcript_path_hash TEXT,
  transcript_mtime_ns INTEGER,
  transcript_size INTEGER,
  parser_version TEXT NOT NULL,
  status TEXT NOT NULL,
  recognized_records INTEGER NOT NULL,
  unknown_records INTEGER NOT NULL,
  tool_events INTEGER NOT NULL,
  skill_events INTEGER NOT NULL,
  compaction_events INTEGER NOT NULL,
  reconciled_at REAL NOT NULL,
  diagnostic TEXT
);
"""


def process_identity(session_id: str, pid: int, creation_time: float) -> str:
    material = f"{session_id}\0{pid}\0{creation_time:.6f}".encode()
    return hashlib.sha256(material).hexdigest()


def command_hash(command: str) -> str:
    return hashlib.sha256(command.encode("utf-8", "replace")).hexdigest()


def normalize_tool(raw: str) -> str:
    value = raw.strip().lower().replace("-", "_").replace(" ", "_")
    if value in {"bash", "shell", "shell_command", "exec_command", "powershell"}:
        return "shell"
    if value in {"read", "read_file", "get_file", "view_image"}:
        return "read"
    if value in {"write", "write_file", "edit", "edit_file", "apply_patch", "patch"}:
        return "edit"
    if "search" in value or value in {"grep", "glob", "find", "rg"}:
        return "search"
    if value in {"agent", "task", "spawn_agent", "send_message", "wait_agent"}:
        return "agent"
    if value in {"web", "web_search", "web_fetch", "browser"}:
        return "web"
    if value in {"skill", "invoke_skill"}:
        return "skill"
    if value.startswith("mcp") or value == "mcp_tool":
        return "mcp"
    return "unmapped"


def _precision(*values: float | None) -> int:
    result = 0
    for value in values:
        if value is None:
            continue
        rendered = f"{value:.8f}".rstrip("0").rstrip(".")
        result = max(result, len(rendered.partition(".")[2]))
    return result


def _classify_quota_drop(
    *,
    previous_sampled_at: float,
    observed_at: float,
    expected_reset_at: float | None,
    before: float,
    after: float,
) -> tuple[str, str | None]:
    """Classify a downward quota movement with a false-positive-averse boundary.

    An unexpected reset is an alert, not merely a discontinuity. It therefore requires
    positive evidence that the provider's advertised reset is still well in the future,
    a large drop, and a reset-like low floor. Missing/ambiguous timing fails closed.
    """
    if expected_reset_at is not None and (
        previous_sampled_at - RESET_EXPECTED_TOLERANCE_SECONDS
        <= expected_reset_at
        <= observed_at + RESET_EXPECTED_TOLERANCE_SECONDS
    ):
        # Polling observes state, not the exact instant it changed. If the advertised
        # boundary fell between two samples, a later observation is still scheduled.
        return "scheduled", None
    if expected_reset_at is None:
        return "uncertain", "missing_expected_reset"
    seconds_to_expected = expected_reset_at - observed_at
    if seconds_to_expected <= 0:
        return "uncertain", "expected_reset_precedes_observation"
    if seconds_to_expected < RESET_UNEXPECTED_MIN_TIME_TO_EXPECTED_SECONDS:
        return "uncertain", "too_close_to_expected_reset"
    if before - after < RESET_UNEXPECTED_MIN_DROP_PERCENT:
        return "uncertain", "movement_below_reset_threshold"
    if after > RESET_UNEXPECTED_MAX_AFTER_PERCENT:
        return "uncertain", "value_above_reset_floor"
    return "unexpected", None


class OperationalTelemetryStore:
    """Durable, bounded evidence store for Phase 2 operational telemetry."""

    def __init__(
        self,
        path: Path,
        *,
        retention_days: int = 180,
        process_retention_days: int = 30,
    ) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._operation_lock = database_operation_lock(path)
        self.retention_days = retention_days
        self.process_retention_days = process_retention_days
        self._closed = False
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mux-telemetry-db")
        self._executor.submit(self._connect).result()
        self._event_task: asyncio.Task[None] | None = None
        self._reconcile_task: asyncio.Task[None] | None = None
        self._event_queue: asyncio.Queue[MuxEvent] | None = None
        self._event_bus: EventBus | None = None
        self.sessions: Any = None
        self.history: Any = None

    def _connect(self) -> None:
        with self._operation_lock:
            self._db = sqlite3.connect(self.path, check_same_thread=False)
            self._db.row_factory = sqlite3.Row
            self._db.execute("PRAGMA synchronous=NORMAL")
            self._db.execute("PRAGMA busy_timeout=5000")
            self._db.execute("PRAGMA cache_size=-12000")
            self._db.executescript(SCHEMA)
            self._migrate_schema()
            self._repair_legacy_reset_classifications()
            self._db.execute(f"PRAGMA user_version={TELEMETRY_SCHEMA_VERSION}")
            self._db.commit()

    def _migrate_schema(self) -> None:
        reset_columns = {
            str(row["name"])
            for row in self._db.execute("PRAGMA table_info(quota_reset_events)").fetchall()
        }
        if "review_status" not in reset_columns:
            self._db.execute("ALTER TABLE quota_reset_events ADD COLUMN review_status TEXT")
        if "reviewed_at" not in reset_columns:
            self._db.execute("ALTER TABLE quota_reset_events ADD COLUMN reviewed_at REAL")

    def _repair_legacy_reset_classifications(self) -> None:
        """Re-evaluate old unexpected rows under the hardened high-precision policy."""
        rows = self._db.execute(
            "SELECT r.*,b.sampled_at AS previous_sampled_at FROM quota_reset_events r "
            "LEFT JOIN quota_samples b ON b.id=r.before_sample_id "
            "WHERE r.classification='unexpected'"
        ).fetchall()
        for row in rows:
            classification: str
            suppression: str | None
            if row["previous_sampled_at"] is None:
                classification, suppression = "uncertain", "missing_boundary_sample"
            else:
                classification, suppression = _classify_quota_drop(
                    previous_sampled_at=float(row["previous_sampled_at"]),
                    observed_at=float(row["observed_at"]),
                    expected_reset_at=(
                        float(row["expected_reset_at"])
                        if row["expected_reset_at"] is not None
                        else None
                    ),
                    before=float(row["before_value"]),
                    after=float(row["after_value"]),
                )
            if classification == "unexpected":
                continue
            confirmed = int(classification == "scheduled")
            self._db.execute(
                "UPDATE quota_reset_events SET classification=?,confidence=?,confirmed=?,"
                "suppression_reason=?,confirmation_sample_id=NULL,confirmed_at=? WHERE id=?",
                (
                    classification,
                    "high" if confirmed else "low",
                    confirmed,
                    suppression,
                    row["observed_at"] if confirmed else None,
                    row["id"],
                ),
            )

    async def _run(self, fn: Callable[[], T]) -> T:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self._executor, run_sqlite_operation, self._db, self._operation_lock, fn
        )

    def start(self, events: EventBus, *, sessions: Any, history: Any) -> None:
        self.sessions = sessions
        self.history = history
        self._event_bus = events
        self._event_queue = events.subscribe()
        self._event_task = asyncio.create_task(self._consume_events(), name="operational-telemetry")
        self._reconcile_task = asyncio.create_task(
            self._reconcile_loop(), name="historical-operational-telemetry"
        )

    async def stop(self) -> None:
        if self._event_task:
            self._event_task.cancel()
            await asyncio.gather(self._event_task, return_exceptions=True)
            self._event_task = None
        if self._reconcile_task:
            self._reconcile_task.cancel()
            await asyncio.gather(self._reconcile_task, return_exceptions=True)
            self._reconcile_task = None
        if self._event_bus and self._event_queue:
            self._event_bus.unsubscribe(self._event_queue)
        self._event_bus = None
        self._event_queue = None

    async def _consume_events(self) -> None:
        assert self._event_queue is not None
        while True:
            event = await self._event_queue.get()
            try:
                if event.type == "context_compacted":
                    await self.record_compaction(event)
                elif event.type in {"tool_use", "tool_result", "skill_invoked"}:
                    await self.record_tool_event(event)
            finally:
                self._event_queue.task_done()

    async def _reconcile_loop(self) -> None:
        await asyncio.sleep(5)
        while True:
            await self.prune(process_retention_days=self.process_retention_days)
            await self.reconcile_transcripts()
            await asyncio.sleep(60 * 60)

    async def reconcile_transcripts(self, *, limit: int = 2000) -> dict[str, int]:
        if self.history is None:
            return {"scanned": 0, "skipped": 0, "errors": 0}
        rows = await self.history.telemetry_history_rows(limit)
        summary = {"scanned": 0, "skipped": 0, "errors": 0}
        for row in rows:
            path = Path(str(row.get("transcript_path") or ""))
            try:
                stat = await asyncio.to_thread(path.stat)
            except OSError:
                await self._record_coverage_error(row, "transcript_unavailable")
                summary["errors"] += 1
                continue
            if await self._coverage_current(
                str(row["id"]), str(row["backend"]), stat.st_mtime_ns, stat.st_size
            ):
                summary["skipped"] += 1
                continue
            try:
                scan = await asyncio.to_thread(
                    scan_native_telemetry,
                    path,
                    str(row["backend"]),
                    str(row["id"]),
                    row.get("project_id"),
                    row.get("model"),
                )
                await self._persist_transcript_scan(row, stat.st_mtime_ns, stat.st_size, scan)
                summary["scanned"] += 1
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                await self._record_coverage_error(row, str(exc)[:500])
                summary["errors"] += 1
        return summary

    async def _coverage_current(
        self, session_id: str, backend: str, mtime_ns: int, size: int
    ) -> bool:
        def op() -> bool:
            row = self._db.execute(
                "SELECT transcript_mtime_ns,transcript_size,parser_version,status "
                "FROM transcript_telemetry_coverage WHERE session_id=?",
                (session_id,),
            ).fetchone()
            return bool(
                row
                and row["transcript_mtime_ns"] == mtime_ns
                and row["transcript_size"] == size
                and row["parser_version"]
                == TOOL_PARSER_VERSIONS.get(backend, f"{backend}-{TOOL_PARSER_VERSION}")
                and row["status"] == "ready"
            )

        return await self._run(op)

    async def _record_coverage_error(self, row: dict[str, Any], diagnostic: str) -> None:
        def op() -> None:
            self._db.execute(
                "INSERT INTO transcript_telemetry_coverage"
                "(session_id,backend,project_id,parser_version,status,recognized_records,"
                "unknown_records,tool_events,skill_events,compaction_events,reconciled_at,"
                "diagnostic) VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(session_id) DO UPDATE SET "
                "status=excluded.status,reconciled_at=excluded.reconciled_at,"
                "diagnostic=excluded.diagnostic",
                (
                    row["id"],
                    row["backend"],
                    row.get("project_id"),
                    TOOL_PARSER_VERSIONS.get(
                        str(row["backend"]), f"{row['backend']}-{TOOL_PARSER_VERSION}"
                    ),
                    "error",
                    0,
                    0,
                    0,
                    0,
                    0,
                    time.time(),
                    diagnostic,
                ),
            )
            self._db.commit()

        await self._run(op)

    async def _persist_transcript_scan(
        self,
        row: dict[str, Any],
        mtime_ns: int,
        size: int,
        scan: dict[str, Any],
    ) -> None:
        path_hash = hashlib.sha256(str(row["transcript_path"]).encode()).hexdigest()

        def op() -> None:
            # Pull possible live duplicates once, then batch the transcript inserts.
            # The previous per-tool SELECT made a large transcript hold swe-mux's
            # shared SQLite writer slot for tens of seconds, delaying terminal spawn.
            tool_items = scan["tools"]
            live_tools: dict[tuple[str, str], list[float]] = {}
            if tool_items:
                first_at = min(float(item["observed_at"]) for item in tool_items) - 2
                last_at = max(float(item["observed_at"]) for item in tool_items) + 2
                for live in self._db.execute(
                    "SELECT kind,raw_tool,observed_at FROM tool_events WHERE session_id=? "
                    "AND source!='reconciled_transcript' AND observed_at BETWEEN ? AND ?",
                    (row["id"], first_at, last_at),
                ).fetchall():
                    live_tools.setdefault((live["kind"], live["raw_tool"]), []).append(
                        float(live["observed_at"])
                    )
                for observed in live_tools.values():
                    observed.sort()

            def duplicates_live_tool(item: dict[str, Any]) -> bool:
                observed = live_tools.get((item["kind"], item["raw_tool"]), [])
                target = float(item["observed_at"])
                index = bisect_left(observed, target - 2)
                return index < len(observed) and observed[index] <= target + 2

            tool_rows = [
                (
                    hashlib.sha256(item["source_identity"].encode()).hexdigest(),
                    None,
                    item["source_identity"],
                    row["id"],
                    row["id"],
                    row.get("project_id"),
                    row["backend"],
                    row.get("model"),
                    item["observed_at"],
                    "reconciled_transcript",
                    item["kind"],
                    item["raw_tool"],
                    normalize_tool(item["raw_tool"]),
                    item.get("success"),
                    item.get("exit_code"),
                    item.get("duration_ms"),
                    TOOL_PARSER_VERSIONS.get(
                        str(row["backend"]), f"{row['backend']}-{TOOL_PARSER_VERSION}"
                    ),
                    item.get("explicit_skill"),
                )
                for item in tool_items
                if not duplicates_live_tool(item)
            ]
            if tool_rows:
                self._db.executemany(
                    "INSERT OR IGNORE INTO tool_events"
                    "(id,event_seq,source_identity,session_id,agent_run_id,project_id,backend,model,"
                    "observed_at,source,kind,raw_tool,taxonomy,success,exit_code,duration_ms,"
                    "parser_version,explicit_skill) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    tool_rows,
                )

            compaction_items = scan["compactions"]
            live_compactions: list[float] = []
            if compaction_items:
                first_at = min(float(item["observed_at"]) for item in compaction_items) - 2
                last_at = max(float(item["observed_at"]) for item in compaction_items) + 2
                live_compactions = sorted(
                    float(live["observed_at"])
                    for live in self._db.execute(
                        "SELECT observed_at FROM context_compactions WHERE session_id=? "
                        "AND source!='reconciled_transcript' AND observed_at BETWEEN ? AND ?",
                        (row["id"], first_at, last_at),
                    ).fetchall()
                )

            def duplicates_live_compaction(item: dict[str, Any]) -> bool:
                target = float(item["observed_at"])
                index = bisect_left(live_compactions, target - 2)
                return index < len(live_compactions) and live_compactions[index] <= target + 2

            compaction_rows = [
                (
                    hashlib.sha256(item["source_identity"].encode()).hexdigest(),
                    None,
                    row["id"],
                    row["id"],
                    row.get("project_id"),
                    row["backend"],
                    row.get("model"),
                    item["observed_at"],
                    "reconciled_transcript",
                    "explicit_native",
                    "high",
                    TOOL_PARSER_VERSIONS.get(
                        str(row["backend"]), f"{row['backend']}-{TOOL_PARSER_VERSION}"
                    ),
                )
                for item in compaction_items
                if not duplicates_live_compaction(item)
            ]
            if compaction_rows:
                self._db.executemany(
                    "INSERT OR IGNORE INTO context_compactions"
                    "(id,event_seq,session_id,agent_run_id,project_id,backend,model,observed_at,"
                    "source,capability,confidence,parser_version) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    compaction_rows,
                )
            self._db.execute(
                "INSERT INTO transcript_telemetry_coverage"
                "(session_id,backend,project_id,transcript_path_hash,transcript_mtime_ns,"
                "transcript_size,parser_version,status,recognized_records,unknown_records,"
                "tool_events,skill_events,compaction_events,reconciled_at,diagnostic) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(session_id) DO UPDATE SET "
                "project_id=excluded.project_id,transcript_path_hash=excluded.transcript_path_hash,"
                "transcript_mtime_ns=excluded.transcript_mtime_ns,"
                "transcript_size=excluded.transcript_size,parser_version=excluded.parser_version,"
                "status=excluded.status,recognized_records=excluded.recognized_records,"
                "unknown_records=excluded.unknown_records,tool_events=excluded.tool_events,"
                "skill_events=excluded.skill_events,compaction_events=excluded.compaction_events,"
                "reconciled_at=excluded.reconciled_at,diagnostic=excluded.diagnostic",
                (
                    row["id"],
                    row["backend"],
                    row.get("project_id"),
                    path_hash,
                    mtime_ns,
                    size,
                    TOOL_PARSER_VERSIONS.get(
                        str(row["backend"]), f"{row['backend']}-{TOOL_PARSER_VERSION}"
                    ),
                    "ready",
                    scan["recognized"],
                    scan["unknown"],
                    len(scan["tools"]),
                    sum(bool(item.get("explicit_skill")) for item in scan["tools"]),
                    len(scan["compactions"]),
                    time.time(),
                    scan.get("diagnostic"),
                ),
            )
            self._db.commit()

        await self._run(op)
        await self.history.set_context_compaction_summary(
            str(row["id"]),
            len(scan["compactions"]),
            max((item["observed_at"] for item in scan["compactions"]), default=None),
            "explicit_native" if scan["compactions"] else None,
            "high" if scan["compactions"] else None,
        )

    def _session_dimensions(self, session_id: str) -> dict[str, Any]:
        session = self.sessions.sessions.get(session_id) if self.sessions else None
        if not session:
            return {
                "agent_run_id": None,
                "project_id": None,
                "backend": "unknown",
                "model": None,
            }
        record = session.record
        return {
            "agent_run_id": record.agent_run_id,
            "project_id": record.project_id,
            "backend": record.backend,
            "model": record.model,
        }

    async def record_compaction(self, event: MuxEvent) -> None:
        if not event.session_id or event.payload.get("scope", "root") != "root":
            return
        dimensions = self._session_dimensions(event.session_id)
        backend = str(event.payload.get("backend") or dimensions["backend"])
        capability = str(event.payload.get("capability") or "explicit_native")
        confidence = str(event.payload.get("confidence") or "high")
        parser_version = f"{backend}:{event.payload.get('parser_version') or TOOL_PARSER_VERSION}"
        event_id = hashlib.sha256(
            f"{event.session_id}\0{event.ts:.6f}\0{event.source}\0compaction".encode()
        ).hexdigest()

        def op() -> bool:
            cursor = self._db.execute(
                "INSERT OR IGNORE INTO context_compactions"
                "(id,event_seq,session_id,agent_run_id,project_id,backend,model,observed_at,"
                "source,capability,confidence,parser_version) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    event_id,
                    event.seq or None,
                    event.session_id,
                    dimensions["agent_run_id"],
                    dimensions["project_id"],
                    backend,
                    dimensions["model"],
                    event.ts,
                    event.source,
                    capability,
                    confidence,
                    parser_version,
                ),
            )
            self._db.commit()
            return bool(cursor.rowcount)

        inserted = await self._run(op)
        if not inserted:
            return
        session = self.sessions.sessions.get(event.session_id) if self.sessions else None
        if session:
            session.record.compaction_count += 1
            session.record.last_compaction_at = event.ts
            session.record.compaction_capability = capability
            session.record.compaction_confidence = confidence
            session.publish_update()
        if self.history:
            await self.history.record_context_compaction(
                event.session_id, event.ts, capability, confidence
            )

    async def record_tool_event(self, event: MuxEvent) -> None:
        if not event.session_id or event.payload.get("scope", "root") != "root":
            return
        dimensions = self._session_dimensions(event.session_id)
        raw_tool = str(
            event.payload.get("tool") or ("skill" if event.type == "skill_invoked" else "tool")
        )
        call_id = str(event.payload.get("call_id") or "")
        explicit_skill = event.payload.get("skill") if event.type == "skill_invoked" else None
        # Native call IDs are stable. Hook-only events have no ID, so the two-second
        # semantic bucket mirrors EventBus cross-source deduplication without merging
        # repeated calls from one source.
        source_identity = (
            f"native:{event.session_id}:{event.type}:{call_id}"
            if call_id
            else f"{event.session_id}:{event.type}:{event.source}:{event.seq or event.ts:.6f}"
        )
        event_id = hashlib.sha256(source_identity.encode()).hexdigest()
        success = event.payload.get("success")
        exit_code = event.payload.get("exit_code")
        duration = event.payload.get("duration_ms")

        def op() -> None:
            self._db.execute(
                "INSERT OR IGNORE INTO tool_events"
                "(id,event_seq,source_identity,session_id,agent_run_id,project_id,backend,model,"
                "observed_at,source,kind,raw_tool,taxonomy,success,exit_code,duration_ms,"
                "parser_version,explicit_skill) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    event_id,
                    event.seq or None,
                    source_identity,
                    event.session_id,
                    dimensions["agent_run_id"],
                    dimensions["project_id"],
                    str(event.payload.get("backend") or dimensions["backend"]),
                    dimensions["model"],
                    event.ts,
                    event.source,
                    event.type,
                    raw_tool,
                    normalize_tool(raw_tool),
                    None if success is None else int(bool(success)),
                    int(exit_code) if isinstance(exit_code, int) else None,
                    float(duration) if isinstance(duration, (int, float)) else None,
                    (
                        f"{dimensions['backend']}:"
                        f"{event.payload.get('parser_version') or TOOL_PARSER_VERSION}"
                    ),
                    str(explicit_skill) if explicit_skill else None,
                ),
            )
            self._db.commit()

        await self._run(op)

    async def record_process_observations(self, observations: list[dict[str, Any]]) -> None:
        if not observations:
            return

        def op() -> None:
            for item in observations:
                started = float(item.get("started_at") or 0)
                if not started:
                    continue
                identity_id = str(
                    item.get("identity_id")
                    or process_identity(str(item["session_id"]), int(item["pid"]), started)
                )
                now = float(item.get("last_seen") or time.time())
                first = float(item.get("first_seen") or now)
                self._db.execute(
                    "INSERT INTO process_evidence"
                    "(identity_id,pid,creation_time,session_id,agent_run_id,project_id,executable,"
                    "command_hash,parent_pid,parent_lineage_json,job_assignment,state,reason,"
                    "confidence,first_seen,last_seen,last_verified_at,exited_at,exit_evidence,"
                    "inaccessible_count,startup_revalidated) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(identity_id) DO UPDATE SET parent_pid=excluded.parent_pid,"
                    "parent_lineage_json=excluded.parent_lineage_json,executable=excluded.executable,"
                    "command_hash=excluded.command_hash,job_assignment=excluded.job_assignment,"
                    "state=excluded.state,reason=excluded.reason,confidence=excluded.confidence,"
                    "last_seen=excluded.last_seen,last_verified_at=excluded.last_verified_at,"
                    "exited_at=excluded.exited_at,exit_evidence=excluded.exit_evidence,"
                    "inaccessible_count=excluded.inaccessible_count,"
                    "startup_revalidated=excluded.startup_revalidated",
                    (
                        identity_id,
                        int(item["pid"]),
                        started,
                        str(item["session_id"]),
                        item.get("agent_run_id"),
                        item.get("project_id"),
                        item.get("executable"),
                        str(
                            item.get("command_hash") or command_hash(str(item.get("command") or ""))
                        ),
                        item.get("parent_pid"),
                        json.dumps(item.get("parent_lineage") or []),
                        str(item.get("job_assignment") or "unknown"),
                        str(item.get("evidence_state") or "active"),
                        str(item.get("evidence_reason") or "live_descendant_fingerprint_match"),
                        str(item.get("confidence") or "high"),
                        first,
                        now,
                        item.get("last_verified_at") or now,
                        item.get("exited_at"),
                        item.get("exit_evidence"),
                        int(item.get("inaccessible_count") or 0),
                        int(bool(item.get("startup_revalidated"))),
                    ),
                )
            self._db.commit()

        await self._run(op)

    async def process_candidates(self) -> list[dict[str, Any]]:
        def op() -> list[dict[str, Any]]:
            rows = self._db.execute(
                "SELECT * FROM process_evidence ORDER BY last_seen DESC LIMIT 4096"
            ).fetchall()
            result = []
            for row in rows:
                item = dict(row)
                item["parent_lineage"] = json.loads(item.pop("parent_lineage_json") or "[]")
                result.append(item)
            return result

        return await self._run(op)

    async def record_quota_sample(
        self,
        *,
        provider: str,
        account_id: str,
        quota: dict[str, Any],
        sampled_at: float,
        account_active: bool,
        auth_state: str,
    ) -> dict[str, Any]:
        session_value = quota.get("session")
        weekly_value = quota.get("weekly")
        session: dict[str, Any] = session_value if isinstance(session_value, dict) else {}
        weekly: dict[str, Any] = weekly_value if isinstance(weekly_value, dict) else {}
        session_used = _finite(session.get("used_percent"))
        weekly_used = _finite(weekly.get("used_percent"))
        status = str(quota.get("status") or "error")
        freshness = "fresh" if status == "ready" else status

        def op() -> tuple[int, list[dict[str, Any]]]:
            cursor = self._db.execute(
                "INSERT OR IGNORE INTO quota_samples"
                "(provider,account_id,sampled_at,status,session_used,weekly_used,session_reset_at,"
                "weekly_reset_at,source,freshness,raw_precision,error,account_active,auth_state) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    provider,
                    account_id,
                    sampled_at,
                    status,
                    session_used,
                    weekly_used,
                    _finite(session.get("resets_at")),
                    _finite(weekly.get("resets_at")),
                    quota.get("source"),
                    freshness,
                    _precision(session_used, weekly_used),
                    quota.get("error"),
                    int(account_active),
                    auth_state,
                ),
            )
            if cursor.rowcount == 0:
                row = self._db.execute(
                    "SELECT id FROM quota_samples WHERE provider=? AND account_id=? "
                    "AND sampled_at=?",
                    (provider, account_id, sampled_at),
                ).fetchone()
                sample_id = int(row["id"])
            else:
                sample_id = int(cursor.lastrowid or 0)
            generated = self._detect_resets_and_attribution(sample_id)
            self._db.commit()
            return sample_id, generated

        sample_id, generated = await self._run(op)
        if self._event_bus:
            for item in generated:
                if item.get("confirmed") and item.get("classification") == "unexpected":
                    await self._event_bus.emit(
                        "unexpected_quota_reset",
                        source="operational_telemetry",
                        reset_id=item["id"],
                        provider=provider,
                        account_id=account_id,
                        window=item["window"],
                        confidence=item["confidence"],
                    )
        return {"sample_id": sample_id, "reset_events": generated}

    def _detect_resets_and_attribution(self, sample_id: int) -> list[dict[str, Any]]:
        current = self._db.execute(
            "SELECT * FROM quota_samples WHERE id=?", (sample_id,)
        ).fetchone()
        if not current:
            return []
        previous = self._db.execute(
            "SELECT * FROM quota_samples WHERE provider=? AND account_id=? AND id<? "
            "ORDER BY sampled_at DESC,id DESC LIMIT 1",
            (current["provider"], current["account_id"], sample_id),
        ).fetchone()
        if not previous or float(current["sampled_at"]) <= float(previous["sampled_at"]):
            return []
        generated: list[dict[str, Any]] = []
        for window in ("session", "weekly"):
            before = previous[f"{window}_used"]
            after = current[f"{window}_used"]
            if before is None or after is None:
                continue
            delta = float(after) - float(before)
            if delta > 0:
                self._record_attribution(current, previous, window, delta)
            pending = self._db.execute(
                "SELECT * FROM quota_reset_events WHERE provider=? AND account_id=? AND window=? "
                "AND classification='unexpected' AND confirmed=0 AND suppression_reason IS NULL "
                "ORDER BY observed_at DESC LIMIT 1",
                (current["provider"], current["account_id"], window),
            ).fetchone()
            pending_handled = False
            if pending:
                pending_handled = True
                age = float(current["sampled_at"]) - float(pending["observed_at"])
                stable_low = float(after) <= float(pending["after_value"]) + max(
                    2.0, int(current["raw_precision"] == 0)
                )
                candidate_sample = self._db.execute(
                    "SELECT * FROM quota_samples WHERE id=?", (pending["after_sample_id"],)
                ).fetchone()
                same_candidate_identity = bool(
                    candidate_sample
                    and int(current["account_active"]) == int(candidate_sample["account_active"])
                    and current["auth_state"] == candidate_sample["auth_state"]
                )
                expected = pending["expected_reset_at"]
                before_expected_boundary = bool(
                    expected is not None
                    and float(current["sampled_at"])
                    < float(expected) - RESET_EXPECTED_TOLERANCE_SECONDS
                )
                can_confirm = (
                    current["freshness"] == "fresh"
                    and candidate_sample is not None
                    and candidate_sample["freshness"] == "fresh"
                    and RESET_CONFIRMATION_MIN_SECONDS <= age <= RESET_CONFIRMATION_MAX_SECONDS
                    and stable_low
                    and float(after) <= RESET_UNEXPECTED_MAX_AFTER_PERCENT
                    and same_candidate_identity
                    and before_expected_boundary
                )
                if can_confirm:
                    self._db.execute(
                        "UPDATE quota_reset_events SET confirmed=1,confirmation_sample_id=?,"
                        "confirmed_at=?,confidence='high' WHERE id=?",
                        (sample_id, current["sampled_at"], pending["id"]),
                    )
                    item = dict(pending)
                    item.update(
                        confirmed=1,
                        confirmation_sample_id=sample_id,
                        confirmed_at=current["sampled_at"],
                        confidence="high",
                    )
                    generated.append(item)
                elif (
                    age > RESET_CONFIRMATION_MAX_SECONDS
                    or not stable_low
                    or not same_candidate_identity
                    or (expected is not None and not before_expected_boundary)
                ):
                    if age > RESET_CONFIRMATION_MAX_SECONDS:
                        reason = "confirmation_expired"
                    elif not stable_low:
                        reason = "value_rebounded"
                    elif not same_candidate_identity:
                        reason = "confirmation_identity_changed"
                    else:
                        reason = "scheduled_boundary_reached"
                    self._db.execute(
                        "UPDATE quota_reset_events SET suppression_reason=? WHERE id=?",
                        (reason, pending["id"]),
                    )
            drop = float(before) - float(after)
            if drop <= 0 or pending_handled:
                continue
            expected = previous[f"{window}_reset_at"]
            fresh = current["freshness"] == previous["freshness"] == "fresh"
            same_auth = (
                int(current["account_active"]) == int(previous["account_active"])
                and current["auth_state"] == previous["auth_state"]
            )
            suppression = None
            if not fresh:
                suppression = "stale_or_error_sample"
            elif not same_auth:
                suppression = "account_or_auth_transition"
            if suppression:
                classification = "uncertain"
            else:
                classification, suppression = _classify_quota_drop(
                    previous_sampled_at=float(previous["sampled_at"]),
                    observed_at=float(current["sampled_at"]),
                    expected_reset_at=float(expected) if expected is not None else None,
                    before=float(before),
                    after=float(after),
                )
            confirmed = int(classification == "scheduled" and not suppression)
            confidence = "high" if confirmed else "low" if suppression else "medium"
            reset_id = str(uuid.uuid4())
            self._db.execute(
                "INSERT INTO quota_reset_events"
                "(id,provider,account_id,window,before_sample_id,after_sample_id,before_value,"
                "after_value,expected_reset_at,observed_at,classification,confidence,confirmed,"
                "suppression_reason,created_at,confirmed_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    reset_id,
                    current["provider"],
                    current["account_id"],
                    window,
                    previous["id"],
                    current["id"],
                    before,
                    after,
                    expected,
                    current["sampled_at"],
                    classification,
                    confidence,
                    confirmed,
                    suppression,
                    time.time(),
                    current["sampled_at"] if confirmed else None,
                ),
            )
            generated.append(
                {
                    "id": reset_id,
                    "provider": current["provider"],
                    "account_id": current["account_id"],
                    "window": window,
                    "classification": classification,
                    "confidence": confidence,
                    "confirmed": confirmed,
                    "suppression_reason": suppression,
                }
            )
        return generated

    def _record_attribution(
        self, current: sqlite3.Row, previous: sqlite3.Row, window: str, delta: float
    ) -> None:
        start, end = float(previous["sampled_at"]), float(current["sampled_at"])
        try:
            rows = self._db.execute(
                "SELECT h.id,h.project_id,h.model,h.tokens_in,h.tokens_out,"
                "COUNT(e.seq) activity_events FROM history h JOIN events e ON e.session_id=h.id "
                "WHERE h.backend=? AND e.ts>? AND e.ts<=? AND e.type IN "
                "('turn_started','turn_ended','turn_aborted','tool_use','tool_result') "
                "GROUP BY h.id,h.project_id,h.model,h.tokens_in,h.tokens_out",
                (current["provider"], start, end),
            ).fetchall()
        except sqlite3.OperationalError:
            # The store is independently migratable/testable. A quota sample must
            # remain durable even when legacy history tables are not present yet.
            rows = []
        total_tokens = sum(
            max(0, int(row["tokens_in"] or 0) + int(row["tokens_out"] or 0)) for row in rows
        )
        allocations: list[dict[str, Any]] = []
        for row in rows:
            tokens = max(0, int(row["tokens_in"] or 0) + int(row["tokens_out"] or 0))
            weight = tokens / total_tokens if total_tokens else 1 / max(1, len(rows))
            allocations.append(
                {
                    "session_id": row["id"],
                    "project_id": row["project_id"],
                    "model": row["model"],
                    "activity_events": row["activity_events"],
                    "native_tokens": tokens or None,
                    "share_of_correlated_estimate": round(weight, 6),
                    "quota_percent_estimate": round(delta * weight, 6),
                }
            )
        active = bool(rows) and int(current["account_active"]) and int(previous["account_active"])
        correlated = delta if active else 0.0
        # Identity cannot be proven from subscription percentages. The full delta
        # therefore remains inside both honest ranges even when the point estimate is
        # distributed using explicit native token evidence.
        correlated_low, correlated_high = (0.0, delta if rows else 0.0)
        external = max(0.0, delta - correlated)
        external_low, external_high = (0.0, delta)
        gap = end - start
        caveats = ["provider_quota_weighting_unknown", "shared_account_identity_unprovable"]
        if len(rows) > 1:
            caveats.append("concurrent_mux_sessions")
        if gap > 30 * 60:
            caveats.append("large_sample_gap")
        if not rows:
            caveats.append("no_mux_activity_in_interval")
        confidence = "medium" if len(rows) == 1 and total_tokens and gap <= 30 * 60 else "low"
        self._db.execute(
            "INSERT OR REPLACE INTO quota_attributions"
            "(sample_id,window,provider,account_id,interval_start,interval_end,quota_delta,"
            "correlated_estimate,correlated_low,correlated_high,external_estimate,external_low,"
            "external_high,confidence,sample_gap_seconds,concurrent_sessions,provider_lag_seconds,"
            "allocations_json,caveats_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                current["id"],
                window,
                current["provider"],
                current["account_id"],
                start,
                end,
                delta,
                correlated,
                correlated_low,
                correlated_high,
                external,
                external_low,
                external_high,
                confidence,
                gap,
                len(rows),
                min(gap, 15 * 60),
                json.dumps(allocations),
                json.dumps(caveats),
            ),
        )

    async def latest_quota_by_account(self) -> dict[str, dict[str, Any]]:
        def op() -> dict[str, dict[str, Any]]:
            rows = self._db.execute(
                "SELECT q.* FROM quota_samples q JOIN "
                "(SELECT provider,account_id,MAX(sampled_at) sampled_at FROM quota_samples "
                "GROUP BY provider,account_id) latest ON latest.provider=q.provider "
                "AND latest.account_id=q.account_id AND latest.sampled_at=q.sampled_at"
            ).fetchall()
            result: dict[str, dict[str, Any]] = {}
            for row in rows:
                result[str(row["account_id"])] = _quota_public(dict(row))
            return result

        return await self._run(op)

    async def snapshot(
        self, *, provider: str | None = None, account_id: str | None = None, limit: int = 200
    ) -> dict[str, Any]:
        limit = max(1, min(limit, 1000))

        def op() -> dict[str, Any]:
            filters = []
            args: list[Any] = []
            if provider:
                filters.append("provider=?")
                args.append(provider)
            if account_id:
                filters.append("account_id=?")
                args.append(account_id)
            where = " WHERE " + " AND ".join(filters) if filters else ""
            samples = [
                _quota_public(dict(row))
                for row in self._db.execute(
                    f"SELECT * FROM quota_samples{where} ORDER BY sampled_at DESC LIMIT ?",
                    (*args, limit),
                ).fetchall()
            ]
            resets = [
                dict(row)
                for row in self._db.execute(
                    f"SELECT * FROM quota_reset_events{where} ORDER BY observed_at DESC LIMIT ?",
                    (*args, limit),
                ).fetchall()
            ]
            attributions = []
            for row in self._db.execute(
                f"SELECT * FROM quota_attributions{where} ORDER BY interval_end DESC LIMIT ?",
                (*args, limit),
            ).fetchall():
                item = dict(row)
                item["allocations"] = json.loads(item.pop("allocations_json"))
                item["caveats"] = json.loads(item.pop("caveats_json"))
                attributions.append(item)
            tool_rows = self._db.execute(
                "SELECT backend,COALESCE(model,'unknown') model,COALESCE(project_id,'') project_id,"
                "session_id,taxonomy,raw_tool,COUNT(*) events,"
                "SUM(CASE WHEN kind='tool_use' THEN 1 ELSE 0 END) uses,"
                "SUM(CASE WHEN kind='tool_result' AND success=0 THEN 1 ELSE 0 END) errors,"
                "AVG(duration_ms) average_duration_ms FROM tool_events "
                "GROUP BY backend,COALESCE(model,'unknown'),COALESCE(project_id,''),"
                "session_id,taxonomy,raw_tool "
                "ORDER BY events DESC LIMIT ?",
                (limit,),
            ).fetchall()
            skill_rows = self._db.execute(
                "SELECT explicit_skill,backend,COALESCE(project_id,'') project_id,COUNT(*) uses,"
                "MAX(observed_at) last_used_at FROM tool_events WHERE explicit_skill IS NOT NULL "
                "GROUP BY explicit_skill,backend,COALESCE(project_id,'') "
                "ORDER BY uses DESC LIMIT ?",
                (limit,),
            ).fetchall()
            compactions = self._db.execute(
                "SELECT session_id,backend,COALESCE(project_id,'') project_id,COUNT(*) count,"
                "MAX(observed_at) last_compaction_at,MAX(capability) capability,"
                "MIN(confidence) confidence FROM context_compactions "
                "GROUP BY session_id,backend,COALESCE(project_id,'') "
                "ORDER BY last_compaction_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
            coverage = self._db.execute(
                "SELECT * FROM transcript_telemetry_coverage ORDER BY reconciled_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return {
                "schema_version": TELEMETRY_SCHEMA_VERSION,
                "interpretation": "observational_correlation_only",
                "quota": {
                    "samples": samples,
                    "resets": resets,
                    "attributions": attributions,
                    "rollups": [
                        dict(row)
                        for row in self._db.execute(
                            f"SELECT * FROM quota_sample_rollups{where} ORDER BY day DESC LIMIT ?",
                            (*args, limit),
                        ).fetchall()
                    ],
                },
                "tools": {
                    "metrics": [dict(row) for row in tool_rows],
                    "skills": [dict(row) for row in skill_rows],
                    "unknown_or_unmapped": sum(
                        int(row["events"]) for row in tool_rows if row["taxonomy"] == "unmapped"
                    ),
                    "parser_version": TOOL_PARSER_VERSION,
                    "parser_versions": TOOL_PARSER_VERSIONS,
                    "coverage": [dict(row) for row in coverage],
                },
                "compactions": [dict(row) for row in compactions],
            }

        return await self._run(op)

    async def reset_summary(self) -> dict[str, Any]:
        def op() -> dict[str, Any]:
            row = self._db.execute(
                "SELECT * FROM quota_reset_events WHERE classification='unexpected' "
                "AND confirmed=1 AND suppression_reason IS NULL AND review_status IS NULL "
                "ORDER BY confirmed_at DESC LIMIT 1"
            ).fetchone()
            count = self._db.execute(
                "SELECT COUNT(*) count FROM quota_reset_events WHERE classification='unexpected' "
                "AND confirmed=1 AND suppression_reason IS NULL AND review_status IS NULL"
            ).fetchone()["count"]
            return {"count": count, "latest": dict(row) if row else None}

        return await self._run(op)

    async def review_quota_reset(self, reset_id: str, resolution: str) -> dict[str, Any]:
        if resolution not in {"manual_usage", "discarded"}:
            raise ValueError("resolution must be manual_usage or discarded")

        def op() -> dict[str, Any]:
            row = self._db.execute(
                "SELECT * FROM quota_reset_events WHERE id=?", (reset_id,)
            ).fetchone()
            if row is None:
                raise KeyError(reset_id)
            if resolution == "manual_usage" and row["provider"] != "codex":
                raise ValueError("manual_usage is only valid for Codex quota evidence")
            reviewed_at = time.time()
            self._db.execute(
                "UPDATE quota_reset_events SET review_status=?,reviewed_at=? WHERE id=?",
                (resolution, reviewed_at, reset_id),
            )
            self._db.commit()
            reviewed = self._db.execute(
                "SELECT * FROM quota_reset_events WHERE id=?", (reset_id,)
            ).fetchone()
            return dict(reviewed)

        return await self._run(op)

    async def prune(self, *, process_retention_days: int = 30) -> dict[str, int]:
        now = time.time()
        quota_cutoff = now - self.retention_days * 86400
        process_cutoff = now - process_retention_days * 86400

        def op() -> dict[str, int]:
            old = self._db.execute(
                "SELECT provider,account_id,date(sampled_at,'unixepoch') day,COUNT(*) samples,"
                "SUM(CASE WHEN status!='ready' THEN 1 ELSE 0 END) errors,"
                "MIN(session_used) session_min,MAX(session_used) session_max,"
                "(SELECT session_used FROM quota_samples q2 WHERE q2.provider=q.provider "
                "AND q2.account_id=q.account_id AND date(q2.sampled_at,'unixepoch')="
                "date(q.sampled_at,'unixepoch') ORDER BY q2.sampled_at LIMIT 1) session_first,"
                "(SELECT session_used FROM quota_samples q2 WHERE q2.provider=q.provider "
                "AND q2.account_id=q.account_id AND date(q2.sampled_at,'unixepoch')="
                "date(q.sampled_at,'unixepoch') ORDER BY q2.sampled_at DESC LIMIT 1) session_last,"
                "MIN(weekly_used) weekly_min,MAX(weekly_used) weekly_max,"
                "(SELECT weekly_used FROM quota_samples q2 WHERE q2.provider=q.provider "
                "AND q2.account_id=q.account_id AND date(q2.sampled_at,'unixepoch')="
                "date(q.sampled_at,'unixepoch') ORDER BY q2.sampled_at LIMIT 1) weekly_first,"
                "(SELECT weekly_used FROM quota_samples q2 WHERE q2.provider=q.provider "
                "AND q2.account_id=q.account_id AND date(q2.sampled_at,'unixepoch')="
                "date(q.sampled_at,'unixepoch') ORDER BY q2.sampled_at DESC LIMIT 1) weekly_last "
                "FROM quota_samples q WHERE sampled_at<? GROUP BY provider,account_id,day",
                (quota_cutoff,),
            ).fetchall()
            for row in old:
                self._db.execute(
                    "INSERT OR REPLACE INTO quota_sample_rollups VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    tuple(row),
                )
            deleted_samples = self._db.execute(
                "DELETE FROM quota_samples WHERE sampled_at<?", (quota_cutoff,)
            ).rowcount
            deleted_processes = self._db.execute(
                "DELETE FROM process_evidence WHERE last_seen<? AND state NOT IN "
                "('active','escaped','suspected_orphan')",
                (process_cutoff,),
            ).rowcount
            self._db.execute("DELETE FROM tool_events WHERE observed_at<?", (quota_cutoff,))
            self._db.execute("DELETE FROM context_compactions WHERE observed_at<?", (quota_cutoff,))
            self._db.execute("DELETE FROM quota_reset_events WHERE observed_at<?", (quota_cutoff,))
            self._db.execute("DELETE FROM quota_attributions WHERE interval_end<?", (quota_cutoff,))
            rollup_cutoff = time.strftime(
                "%Y-%m-%d",
                time.gmtime(now - min(3650, self.retention_days * 10) * 86400),
            )
            self._db.execute("DELETE FROM quota_sample_rollups WHERE day<?", (rollup_cutoff,))
            try:
                self._db.execute(
                    "DELETE FROM transcript_telemetry_coverage WHERE session_id NOT IN "
                    "(SELECT id FROM history)"
                )
            except sqlite3.OperationalError:
                pass
            self._db.commit()
            return {"quota_samples": deleted_samples, "processes": deleted_processes}

        return await self._run(op)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._executor.submit(self._db.close).result()
        self._executor.shutdown(wait=True)


def scan_native_telemetry(
    path: Path,
    backend: str,
    session_id: str,
    project_id: str | None,
    model: str | None,
) -> dict[str, Any]:
    """Parse explicit telemetry from a bounded native JSONL transcript.

    Prompt and result bodies are never retained. Files above 32 MiB are scanned from
    the tail so startup work stays bounded; coverage reports that limitation.
    """
    del project_id, model
    size = path.stat().st_size
    tail_bytes = 32 * 1024 * 1024
    truncated = size > tail_bytes
    records: list[tuple[int, dict[str, Any]]] = []
    with path.open("rb") as handle:
        if truncated:
            handle.seek(size - tail_bytes)
            handle.readline()
        for index, raw_line in enumerate(handle):
            if not raw_line.strip():
                continue
            try:
                item = json.loads(raw_line.decode("utf-8", "replace"))
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                records.append((index, item))
    tools: list[dict[str, Any]] = []
    compactions: list[dict[str, Any]] = []
    recognized = 0
    unknown = 0
    names: dict[str, str] = {}
    base_time = path.stat().st_mtime

    def observed(record: dict[str, Any], index: int) -> float:
        value = record.get("timestamp") or record.get("created_at")
        if isinstance(value, (int, float)):
            return float(value) / 1000 if float(value) > 10_000_000_000 else float(value)
        if isinstance(value, str):
            try:
                from datetime import datetime

                return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
            except ValueError:
                pass
        return base_time + index / 1_000_000

    for index, record in records:
        when = observed(record, index)
        if backend == "claude":
            outer = str(record.get("type") or "")
            if outer not in {
                "assistant",
                "user",
                "system",
                "ai-title",
                "attachment",
                "file-history-delta",
                "file-history-snapshot",
                "last-prompt",
                "mode",
                "permission-mode",
                "queue-operation",
            }:
                unknown += 1
                continue
            recognized += 1
            message = record.get("message") if isinstance(record.get("message"), dict) else {}
            content = message.get("content") if isinstance(message, dict) else []
            if isinstance(content, list):
                for block_index, block in enumerate(content):
                    if not isinstance(block, dict):
                        continue
                    kind = block.get("type")
                    if kind == "tool_use":
                        call_id = str(block.get("id") or f"line-{index}-{block_index}")
                        name = str(block.get("name") or "tool")
                        names[call_id] = name
                        skill = None
                        claude_input = block.get("input")
                        if name.lower() == "skill" and isinstance(claude_input, dict):
                            candidate = claude_input.get("skill") or claude_input.get("name")
                            skill = candidate.strip()[:200] if isinstance(candidate, str) else None
                        tools.append(
                            {
                                "source_identity": f"native:{session_id}:tool_use:{call_id}",
                                "observed_at": when,
                                "kind": "tool_use",
                                "raw_tool": name,
                                "explicit_skill": skill,
                            }
                        )
                    elif kind == "tool_result":
                        call_id = str(block.get("tool_use_id") or f"line-{index}-{block_index}")
                        tools.append(
                            {
                                "source_identity": f"native:{session_id}:tool_result:{call_id}",
                                "observed_at": when,
                                "kind": "tool_result",
                                "raw_tool": names.get(call_id, "tool"),
                                "success": int(not bool(block.get("is_error"))),
                            }
                        )
            subtype = str(record.get("subtype") or "")
            if outer == "system" and subtype in {
                "compact_boundary",
                "context_compacted",
                "compaction",
            }:
                compactions.append(
                    {
                        "source_identity": f"native:{session_id}:compaction:{index}",
                        "observed_at": when,
                    }
                )
        elif backend == "codex":
            payload_value = record.get("payload")
            codex_payload: dict[str, Any] = payload_value if isinstance(payload_value, dict) else {}
            payload_type = str(codex_payload.get("type") or "")
            outer = str(record.get("type") or "")
            known = payload_type in CODEX_KNOWN_PAYLOADS or outer in CODEX_KNOWN_OUTER_RECORDS
            if not known:
                unknown += 1
                continue
            recognized += 1
            if payload_type in {"function_call", "custom_tool_call"}:
                call_id = str(
                    codex_payload.get("call_id") or codex_payload.get("id") or f"line-{index}"
                )
                name = str(codex_payload.get("name") or "tool")
                names[call_id] = name
                explicit_skill = codex_payload.get("skill") or codex_payload.get("skill_name")
                tools.append(
                    {
                        "source_identity": f"native:{session_id}:tool_use:{call_id}",
                        "observed_at": when,
                        "kind": "tool_use",
                        "raw_tool": name,
                        "explicit_skill": (
                            explicit_skill.strip()[:200]
                            if name.lower() == "skill" and isinstance(explicit_skill, str)
                            else None
                        ),
                    }
                )
            elif payload_type in {
                "function_call_output",
                "custom_tool_call_output",
                "exec_command_end",
            }:
                call_id = str(
                    codex_payload.get("call_id") or codex_payload.get("id") or f"line-{index}"
                )
                exit_code = codex_payload.get("exit_code")
                result_success = not bool(codex_payload.get("is_error")) and exit_code in {
                    None,
                    0,
                }
                tools.append(
                    {
                        "source_identity": f"native:{session_id}:tool_result:{call_id}",
                        "observed_at": when,
                        "kind": "tool_result",
                        "raw_tool": names.get(call_id, str(codex_payload.get("name") or "tool")),
                        "success": int(result_success),
                        "exit_code": exit_code if isinstance(exit_code, int) else None,
                        "duration_ms": _finite(codex_payload.get("duration_ms")),
                    }
                )
            elif payload_type in {"patch_apply_end", "mcp_tool_call_end", "web_search_end"}:
                name = {
                    "patch_apply_end": "apply_patch",
                    "mcp_tool_call_end": "mcp_tool",
                    "web_search_end": "web_search",
                }[payload_type]
                status = str(codex_payload.get("status") or "")
                explicit_success = codex_payload.get("success")
                tools.append(
                    {
                        "source_identity": (
                            f"native:{session_id}:tool_result:{payload_type}:{index}"
                        ),
                        "observed_at": when,
                        "kind": "tool_result",
                        "raw_tool": name,
                        "success": int(
                            bool(explicit_success)
                            if explicit_success is not None
                            else status not in {"failed", "error"}
                        ),
                        "duration_ms": _finite(codex_payload.get("duration_ms")),
                    }
                )
            if payload_type == "context_compacted" or outer == "compacted":
                compactions.append(
                    {
                        "source_identity": f"native:{session_id}:compaction:{index}",
                        "observed_at": when,
                    }
                )
        else:
            unknown += 1
    return {
        "tools": tools,
        "compactions": compactions,
        "recognized": recognized,
        "unknown": unknown,
        "diagnostic": "tail_32_mib_only" if truncated else None,
    }


def _finite(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    result = float(value)
    return result if math.isfinite(result) else None


def _quota_public(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row.get("id"),
        "provider": row.get("provider"),
        "account_id": row.get("account_id"),
        "sampled_at": row.get("sampled_at"),
        "status": row.get("status"),
        "session": (
            {
                "used_percent": row.get("session_used"),
                "resets_at": row.get("session_reset_at"),
                "window_minutes": 300,
            }
            if row.get("session_used") is not None
            else None
        ),
        "weekly": (
            {
                "used_percent": row.get("weekly_used"),
                "resets_at": row.get("weekly_reset_at"),
                "window_minutes": 10080,
            }
            if row.get("weekly_used") is not None
            else None
        ),
        "source": row.get("source"),
        "freshness": row.get("freshness"),
        "raw_precision": row.get("raw_precision"),
        "error": row.get("error"),
        "active": bool(row.get("account_active")),
        "auth_state": row.get("auth_state"),
        "refreshed_at": row.get("sampled_at") if row.get("status") == "ready" else None,
        "attempted_at": row.get("sampled_at"),
    }
