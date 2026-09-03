"""Schema, migrations, and pure helpers for the canonical telemetry ledger.

The ledger is two kinds of SQLite file: one catalog and one segment per UTC month.
Both are created from the full current schema and then walked through
`CATALOG_MIGRATIONS` / `SEGMENT_MIGRATIONS`, so a file written by an older daemon
gains exactly the additive columns and tables the current code reads, and a fresh
file records the current version without re-applying anything. `CREATE TABLE IF NOT
EXISTS` alone cannot do that: it never adds a column to a table that already exists,
which is how a redeploy would otherwise start failing on the first `INSERT`.

A migration step is either a SQL statement (an `ADD COLUMN` is checked against the
table first, so it is idempotent) or a callable, for the two shapes SQL alone cannot
express additively: recreating a derived rollup table whose key changed, and
backfilling a new column from existing ones.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from .harness import HARNESSES

LEDGER_SCHEMA_VERSION = 4

_SOURCE_RANK = {
    "otel": 400,
    "provider_otel": 400,
    "transcript": 300,
    "store": 300,
    "hook": 200,
    "reconciled_transcript": 100,
    "legacy": 50,
}

#: What a result's source rank says about the quality of the evidence behind a
#: canonical tool call. Stored on the row and carried into every rollup, so a
#: dashboard can be cut by it as exactly as by backend.
EVIDENCE_QUALITIES = ("native", "transcript", "hook", "reconciled", "legacy", "none")


def evidence_quality_for(result_rank: int | None, result_source: str | None) -> str:
    if result_source is None:
        return "none"
    rank = int(result_rank or 0)
    if rank >= 400:
        return "native"
    if rank >= 300:
        return "transcript"
    if rank >= 200:
        return "hook"
    if rank >= 100:
        return "reconciled"
    return "legacy"


_TOOL_ROLLUP_COLUMNS = """
  backend TEXT NOT NULL,
  model TEXT NOT NULL,
  project_id TEXT NOT NULL,
  origin TEXT NOT NULL,
  invocation_layer TEXT NOT NULL,
  family TEXT NOT NULL,
  operation TEXT NOT NULL,
  transport TEXT NOT NULL,
  raw_name TEXT NOT NULL,
  status TEXT NOT NULL,
  evidence_quality TEXT NOT NULL,
  calls INTEGER NOT NULL,
  duration_count INTEGER NOT NULL,
  duration_ms REAL NOT NULL,
  input_bytes INTEGER NOT NULL,
  output_bytes INTEGER NOT NULL,
  approval_wait_count INTEGER NOT NULL DEFAULT 0,
  approval_wait_ms REAL NOT NULL DEFAULT 0,
  PRIMARY KEY(
    {bucket},backend,model,project_id,origin,invocation_layer,family,operation,
    transport,raw_name,status,evidence_quality
  )
"""
TOOL_DAILY_TABLE = (
    "CREATE TABLE IF NOT EXISTS tool_daily (\n  day TEXT NOT NULL,"
    + _TOOL_ROLLUP_COLUMNS.format(bucket="day")
    + ")"
)
TOOL_HOURLY_TABLE = (
    "CREATE TABLE IF NOT EXISTS tool_hourly (\n  hour TEXT NOT NULL,"
    + _TOOL_ROLLUP_COLUMNS.format(bucket="hour")
    + ")"
)

_WORKLOAD_ROLLUP_COLUMNS = """
  backend TEXT NOT NULL,
  model TEXT NOT NULL,
  project_id TEXT NOT NULL,
  origin TEXT NOT NULL,
  runs INTEGER NOT NULL DEFAULT 0,
  ended_runs INTEGER NOT NULL DEFAULT 0,
  wall_duration_count INTEGER NOT NULL DEFAULT 0,
  wall_duration_s REAL NOT NULL DEFAULT 0,
  input_tokens INTEGER NOT NULL DEFAULT 0,
  output_tokens INTEGER NOT NULL DEFAULT 0,
  cache_read_tokens INTEGER NOT NULL DEFAULT 0,
  cache_write_tokens INTEGER NOT NULL DEFAULT 0,
  final_context_count INTEGER NOT NULL DEFAULT 0,
  final_context_sum REAL NOT NULL DEFAULT 0,
  peak_context_count INTEGER NOT NULL DEFAULT 0,
  peak_context_sum REAL NOT NULL DEFAULT 0,
  turns INTEGER NOT NULL DEFAULT 0,
  completed_turns INTEGER NOT NULL DEFAULT 0,
  turn_duration_count INTEGER NOT NULL DEFAULT 0,
  turn_duration_ms REAL NOT NULL DEFAULT 0,
  model_tool_calls INTEGER NOT NULL DEFAULT 0,
  runtime_tool_calls INTEGER NOT NULL DEFAULT 0,
  completed_tool_calls INTEGER NOT NULL DEFAULT 0,
  failed_tool_calls INTEGER NOT NULL DEFAULT 0,
  tool_duration_count INTEGER NOT NULL DEFAULT 0,
  tool_duration_ms REAL NOT NULL DEFAULT 0,
  approval_wait_count INTEGER NOT NULL DEFAULT 0,
  approval_wait_ms REAL NOT NULL DEFAULT 0,
  model_requests INTEGER NOT NULL DEFAULT 0,
  model_request_failures INTEGER NOT NULL DEFAULT 0,
  model_wait_count INTEGER NOT NULL DEFAULT 0,
  model_wait_ms REAL NOT NULL DEFAULT 0,
  request_input_tokens INTEGER NOT NULL DEFAULT 0,
  request_output_tokens INTEGER NOT NULL DEFAULT 0,
  request_cache_read_tokens INTEGER NOT NULL DEFAULT 0,
  request_cache_write_tokens INTEGER NOT NULL DEFAULT 0,
  approval_events INTEGER NOT NULL DEFAULT 0,
  stall_events INTEGER NOT NULL DEFAULT 0,
  subagent_events INTEGER NOT NULL DEFAULT 0,
  verifications INTEGER NOT NULL DEFAULT 0,
  successful_verifications INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY({bucket},backend,model,project_id,origin)
"""
WORKLOAD_DAILY_TABLE = (
    "CREATE TABLE IF NOT EXISTS workload_daily (\n  day TEXT NOT NULL,"
    + _WORKLOAD_ROLLUP_COLUMNS.format(bucket="day")
    + ")"
)
WORKLOAD_HOURLY_TABLE = (
    "CREATE TABLE IF NOT EXISTS workload_hourly (\n  hour TEXT NOT NULL,"
    + _WORKLOAD_ROLLUP_COLUMNS.format(bucket="hour")
    + ")"
)

#: Field-completeness counts per harness version, rolled up so the quality readout
#: costs one indexed read per group rather than a scan of every call in the window.
#: The fields are the ones `quality_summary` reports, in its order.
QUALITY_FIELDS = (
    "calls",
    "with_request",
    "with_result",
    "with_provider_result",
    "with_duration",
    "with_input_hash",
    "with_executed_input_hash",
    "with_output_hash",
    "with_output_size",
    "with_harness_version",
    "with_approval_wait",
    "truncated_outputs",
    "runtime_parent_unavailable",
    "other_family",
)
_QUALITY_ROLLUP_COLUMNS = (
    "\n  backend TEXT NOT NULL,\n  harness_version TEXT NOT NULL,\n  model TEXT NOT NULL,"
    "\n  project_id TEXT NOT NULL,\n  origin TEXT NOT NULL,"
    + "".join(f"\n  {field} INTEGER NOT NULL DEFAULT 0," for field in QUALITY_FIELDS)
    + "\n  PRIMARY KEY({bucket},backend,harness_version,model,project_id,origin)\n"
)
QUALITY_DAILY_TABLE = (
    "CREATE TABLE IF NOT EXISTS quality_daily (\n  day TEXT NOT NULL,"
    + _QUALITY_ROLLUP_COLUMNS.format(bucket="day")
    + ")"
)
QUALITY_HOURLY_TABLE = (
    "CREATE TABLE IF NOT EXISTS quality_hourly (\n  hour TEXT NOT NULL,"
    + _QUALITY_ROLLUP_COLUMNS.format(bucket="hour")
    + ")"
)

CATALOG_SCHEMA = f"""
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS ledger_meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ledger_segments (
  period TEXT PRIMARY KEY,
  relative_path TEXT NOT NULL UNIQUE,
  first_observed_at REAL,
  last_observed_at REAL,
  evidence_rows INTEGER NOT NULL DEFAULT 0,
  sealed_at REAL,
  sha256 TEXT
);
CREATE TABLE IF NOT EXISTS rollup_dirty_days (
  day TEXT PRIMARY KEY,
  dirtied_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS rollup_dirty_hours (
  hour TEXT PRIMARY KEY,
  dirtied_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS entity_locations (
  entity_kind TEXT NOT NULL,
  entity_id TEXT NOT NULL,
  period TEXT NOT NULL,
  PRIMARY KEY(entity_kind,entity_id)
);
CREATE TABLE IF NOT EXISTS legacy_imports (
  source_id TEXT PRIMARY KEY,
  source_path TEXT NOT NULL,
  cursor_rowid INTEGER NOT NULL DEFAULT 0,
  imported_rows INTEGER NOT NULL DEFAULT 0,
  completed INTEGER NOT NULL DEFAULT 0,
  updated_at REAL NOT NULL
);
{TOOL_DAILY_TABLE};
CREATE INDEX IF NOT EXISTS idx_tool_daily_window
  ON tool_daily(day,origin,backend,project_id);
{TOOL_HOURLY_TABLE};
CREATE INDEX IF NOT EXISTS idx_tool_hourly_window
  ON tool_hourly(hour,origin,backend,project_id);
{WORKLOAD_DAILY_TABLE};
{WORKLOAD_HOURLY_TABLE};
{QUALITY_DAILY_TABLE};
{QUALITY_HOURLY_TABLE};
CREATE TABLE IF NOT EXISTS skill_daily (
  day TEXT NOT NULL,
  backend TEXT NOT NULL,
  model TEXT NOT NULL,
  project_id TEXT NOT NULL,
  origin TEXT NOT NULL,
  skill_name TEXT NOT NULL,
  invocation_trigger TEXT NOT NULL,
  skill_source TEXT NOT NULL,
  skill_scope TEXT NOT NULL,
  invocations INTEGER NOT NULL,
  PRIMARY KEY(
    day,backend,model,project_id,origin,skill_name,invocation_trigger,skill_source,skill_scope
  )
);
CREATE TABLE IF NOT EXISTS verification_daily (
  day TEXT NOT NULL,
  backend TEXT NOT NULL,
  model TEXT NOT NULL,
  project_id TEXT NOT NULL,
  origin TEXT NOT NULL,
  framework TEXT NOT NULL,
  verifications INTEGER NOT NULL,
  successful INTEGER NOT NULL,
  passed INTEGER NOT NULL,
  failed INTEGER NOT NULL,
  errors INTEGER NOT NULL,
  skipped INTEGER NOT NULL,
  PRIMARY KEY(day,backend,model,project_id,origin,framework)
);
CREATE TABLE IF NOT EXISTS compaction_daily (
  day TEXT NOT NULL,
  backend TEXT NOT NULL,
  model TEXT NOT NULL,
  project_id TEXT NOT NULL,
  origin TEXT NOT NULL,
  trigger TEXT NOT NULL,
  count INTEGER NOT NULL,
  failures INTEGER NOT NULL,
  duration_count INTEGER NOT NULL,
  duration_ms REAL NOT NULL,
  token_count INTEGER NOT NULL,
  tokens_reclaimed INTEGER NOT NULL,
  PRIMARY KEY(day,backend,model,project_id,origin,trigger)
);
CREATE TABLE IF NOT EXISTS rollup_days (
  day TEXT PRIMARY KEY,
  rebuilt_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS rollup_hours (
  hour TEXT PRIMARY KEY,
  rebuilt_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS segment_seals (
  period TEXT NOT NULL,
  sealed_at REAL NOT NULL,
  sha256 TEXT NOT NULL,
  invalidated_at REAL,
  invalidation_reason TEXT,
  PRIMARY KEY(period,sealed_at)
);
CREATE TABLE IF NOT EXISTS metric_checkpoints (
  series_id TEXT PRIMARY KEY,
  start_time_unix_nano TEXT,
  value INTEGER NOT NULL,
  observed_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS parser_signatures (
  backend TEXT NOT NULL,
  harness_version TEXT NOT NULL,
  parser_version TEXT NOT NULL,
  event_name TEXT NOT NULL,
  recognized INTEGER NOT NULL,
  occurrences INTEGER NOT NULL DEFAULT 0,
  first_seen_at REAL NOT NULL,
  last_seen_at REAL NOT NULL,
  PRIMARY KEY(backend,harness_version,parser_version,event_name)
);
CREATE TABLE IF NOT EXISTS finding_reviews (
  finding_key TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  verdict TEXT NOT NULL,
  note TEXT,
  reviewed_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS native_reconciliations (
  run_id TEXT PRIMARY KEY,
  backend TEXT NOT NULL,
  source_locator TEXT,
  watermark_first INTEGER,
  watermark_second INTEGER,
  parser_version TEXT NOT NULL,
  status TEXT NOT NULL,
  recognized INTEGER NOT NULL DEFAULT 0,
  unknown INTEGER NOT NULL DEFAULT 0,
  tool_events INTEGER NOT NULL DEFAULT 0,
  skill_events INTEGER NOT NULL DEFAULT 0,
  compaction_events INTEGER NOT NULL DEFAULT 0,
  inserted INTEGER NOT NULL DEFAULT 0,
  diagnostic TEXT,
  reconciled_at REAL NOT NULL
);
"""

SEGMENT_SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS segment_meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS telemetry_evidence (
  evidence_id TEXT PRIMARY KEY,
  observed_at REAL NOT NULL,
  received_at REAL NOT NULL,
  event_type TEXT NOT NULL,
  source_kind TEXT NOT NULL,
  source_version TEXT,
  backend TEXT NOT NULL,
  project_id TEXT,
  model TEXT,
  origin TEXT NOT NULL,
  session_id TEXT NOT NULL,
  run_id TEXT NOT NULL,
  turn_id TEXT,
  native_id TEXT,
  source_locator TEXT,
  payload_sha256 TEXT NOT NULL,
  payload_bytes INTEGER NOT NULL,
  privacy_class TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ledger_evidence_time
  ON telemetry_evidence(observed_at,evidence_id);
CREATE INDEX IF NOT EXISTS idx_ledger_evidence_run
  ON telemetry_evidence(run_id,observed_at);
CREATE INDEX IF NOT EXISTS idx_ledger_evidence_event
  ON telemetry_evidence(event_type,observed_at);

CREATE TABLE IF NOT EXISTS telemetry_runs (
  run_id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL,
  native_conversation_id TEXT,
  parent_run_id TEXT,
  launch_tool_call_id TEXT,
  project_id TEXT,
  backend TEXT NOT NULL,
  harness_version TEXT,
  origin TEXT NOT NULL,
  source_locator TEXT,
  started_at REAL NOT NULL,
  ended_at REAL,
  end_reason TEXT,
  initial_model TEXT,
  final_model TEXT,
  input_tokens INTEGER NOT NULL DEFAULT 0,
  output_tokens INTEGER NOT NULL DEFAULT 0,
  cache_read_tokens INTEGER NOT NULL DEFAULT 0,
  cache_write_tokens INTEGER NOT NULL DEFAULT 0,
  cost_usd REAL,
  final_context_pct REAL,
  peak_context_pct REAL,
  measurement_source TEXT,
  first_evidence_id TEXT NOT NULL,
  last_evidence_id TEXT NOT NULL,
  started_at_source TEXT
);
CREATE INDEX IF NOT EXISTS idx_ledger_runs_time
  ON telemetry_runs(started_at,backend,project_id,origin);
CREATE INDEX IF NOT EXISTS idx_ledger_runs_session
  ON telemetry_runs(session_id,started_at);

CREATE TABLE IF NOT EXISTS telemetry_turns (
  turn_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  native_turn_id TEXT,
  agent_id TEXT NOT NULL,
  session_id TEXT NOT NULL,
  project_id TEXT,
  backend TEXT NOT NULL,
  origin TEXT NOT NULL,
  harness_version TEXT,
  ordinal INTEGER,
  trigger TEXT NOT NULL DEFAULT 'unknown',
  started_at REAL NOT NULL,
  finished_at REAL,
  status TEXT NOT NULL,
  duration_ms REAL,
  model TEXT,
  first_evidence_id TEXT NOT NULL,
  last_evidence_id TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ledger_turns_run
  ON telemetry_turns(run_id,started_at);
CREATE INDEX IF NOT EXISTS idx_ledger_turns_time
  ON telemetry_turns(started_at,turn_id);

CREATE TABLE IF NOT EXISTS telemetry_model_requests (
  model_request_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  turn_id TEXT,
  agent_id TEXT NOT NULL,
  session_id TEXT NOT NULL,
  project_id TEXT,
  backend TEXT NOT NULL,
  model TEXT,
  origin TEXT NOT NULL,
  native_request_id TEXT,
  query_source TEXT,
  started_at REAL,
  finished_at REAL NOT NULL,
  duration_ms REAL,
  success INTEGER,
  attempts INTEGER,
  input_tokens INTEGER,
  output_tokens INTEGER,
  cache_read_tokens INTEGER,
  cache_write_tokens INTEGER,
  cost_usd REAL,
  error_type TEXT,
  error_sha256 TEXT,
  evidence_count INTEGER NOT NULL,
  first_token_ms REAL,
  reasoning_tokens INTEGER,
  client_request_id TEXT,
  endpoint TEXT,
  effort TEXT,
  UNIQUE(run_id,agent_id,native_request_id)
);
CREATE INDEX IF NOT EXISTS idx_ledger_model_requests_time
  ON telemetry_model_requests(finished_at,backend,project_id,model);
CREATE INDEX IF NOT EXISTS idx_ledger_model_requests_run
  ON telemetry_model_requests(run_id,turn_id,finished_at);

CREATE TABLE IF NOT EXISTS telemetry_compactions (
  compaction_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  turn_id TEXT,
  agent_id TEXT NOT NULL,
  session_id TEXT NOT NULL,
  project_id TEXT,
  backend TEXT NOT NULL,
  model TEXT,
  origin TEXT NOT NULL,
  native_compaction_id TEXT,
  observed_at REAL NOT NULL,
  trigger TEXT,
  success INTEGER,
  duration_ms REAL,
  tokens_before INTEGER,
  tokens_after INTEGER,
  capability TEXT,
  confidence TEXT,
  evidence_count INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ledger_compactions_time
  ON telemetry_compactions(observed_at,backend,project_id);
CREATE INDEX IF NOT EXISTS idx_ledger_compactions_run
  ON telemetry_compactions(run_id,observed_at);

CREATE TABLE IF NOT EXISTS telemetry_tool_calls (
  tool_call_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  turn_id TEXT,
  agent_id TEXT NOT NULL,
  session_id TEXT NOT NULL,
  project_id TEXT,
  backend TEXT NOT NULL,
  model TEXT,
  origin TEXT NOT NULL,
  harness_version TEXT,
  parent_tool_call_id TEXT,
  parent_status TEXT,
  native_call_id TEXT,
  invocation_layer TEXT NOT NULL,
  raw_name TEXT NOT NULL,
  family TEXT NOT NULL,
  operation TEXT NOT NULL,
  transport TEXT NOT NULL,
  server_name TEXT,
  tool_name TEXT NOT NULL,
  proposed_at REAL,
  started_at REAL,
  finished_at REAL,
  status TEXT NOT NULL,
  success INTEGER,
  error_type TEXT,
  exit_code INTEGER,
  duration_ms REAL,
  approval_wait_ms REAL,
  input_bytes INTEGER,
  output_bytes INTEGER,
  input_chars INTEGER,
  output_chars INTEGER,
  input_sha256 TEXT,
  output_sha256 TEXT,
  input_measurement TEXT NOT NULL,
  output_measurement TEXT NOT NULL,
  executed_input_bytes INTEGER,
  executed_input_chars INTEGER,
  executed_input_sha256 TEXT,
  executed_input_measurement TEXT NOT NULL,
  executed_input_source TEXT,
  target_preview TEXT,
  target_sha256 TEXT,
  request_source TEXT,
  request_rank INTEGER NOT NULL DEFAULT 0,
  result_source TEXT,
  result_rank INTEGER NOT NULL DEFAULT 0,
  status_source TEXT,
  duration_source TEXT,
  error_source TEXT,
  output_source TEXT,
  normalization_version INTEGER NOT NULL,
  evidence_count INTEGER NOT NULL DEFAULT 0,
  output_truncated INTEGER,
  provider_sequence INTEGER,
  native_conversation_id TEXT,
  tool_namespace TEXT,
  error_sha256 TEXT,
  evidence_quality TEXT NOT NULL DEFAULT 'none',
  approval_requested_at REAL,
  UNIQUE(run_id,agent_id,invocation_layer,native_call_id)
);
CREATE INDEX IF NOT EXISTS idx_ledger_tools_time
  ON telemetry_tool_calls(started_at,finished_at,tool_call_id);
CREATE INDEX IF NOT EXISTS idx_ledger_tools_run
  ON telemetry_tool_calls(run_id,turn_id,started_at);
CREATE INDEX IF NOT EXISTS idx_ledger_tools_dimensions
  ON telemetry_tool_calls(backend,project_id,family,operation,transport,status,started_at);

CREATE TABLE IF NOT EXISTS telemetry_skill_invocations (
  skill_invocation_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  turn_id TEXT,
  agent_id TEXT NOT NULL,
  session_id TEXT NOT NULL,
  project_id TEXT,
  backend TEXT NOT NULL,
  model TEXT,
  origin TEXT NOT NULL,
  native_invocation_id TEXT,
  skill_name TEXT NOT NULL,
  skill_revision TEXT,
  skill_source TEXT,
  skill_scope TEXT,
  plugin_id TEXT,
  plugin_version TEXT,
  invocation_trigger TEXT NOT NULL,
  activated_at REAL NOT NULL,
  evidence_quality TEXT NOT NULL,
  occurrences INTEGER NOT NULL,
  evidence_count INTEGER NOT NULL DEFAULT 1,
  UNIQUE(run_id,agent_id,native_invocation_id,skill_name)
);
CREATE INDEX IF NOT EXISTS idx_ledger_skills_time
  ON telemetry_skill_invocations(activated_at,skill_name);
CREATE INDEX IF NOT EXISTS idx_ledger_skills_run
  ON telemetry_skill_invocations(run_id,turn_id,activated_at);

CREATE TABLE IF NOT EXISTS telemetry_verifications (
  verification_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  turn_id TEXT,
  tool_call_id TEXT,
  project_id TEXT,
  backend TEXT NOT NULL,
  model TEXT,
  origin TEXT NOT NULL,
  framework TEXT NOT NULL,
  passed INTEGER,
  failed INTEGER,
  errors INTEGER,
  skipped INTEGER,
  successful INTEGER,
  started_at REAL,
  finished_at REAL NOT NULL,
  outcome_sha256 TEXT NOT NULL,
  parser_version TEXT,
  evidence_id TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ledger_verifications_time
  ON telemetry_verifications(finished_at,framework,successful);
CREATE INDEX IF NOT EXISTS idx_ledger_verifications_run
  ON telemetry_verifications(run_id,finished_at);

CREATE TABLE IF NOT EXISTS telemetry_provider_metrics (
  metric_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  session_id TEXT NOT NULL,
  agent_id TEXT NOT NULL,
  project_id TEXT,
  backend TEXT NOT NULL,
  model TEXT,
  origin TEXT NOT NULL,
  harness_version TEXT,
  metric_name TEXT NOT NULL,
  kind TEXT NOT NULL,
  temporality TEXT NOT NULL,
  attributes_json TEXT NOT NULL,
  count INTEGER,
  sum REAL,
  min REAL,
  max REAL,
  started_at REAL,
  observed_at REAL NOT NULL,
  evidence_id TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ledger_metrics_time
  ON telemetry_provider_metrics(observed_at,metric_name);
CREATE INDEX IF NOT EXISTS idx_ledger_metrics_run
  ON telemetry_provider_metrics(run_id,metric_name);

CREATE TABLE IF NOT EXISTS telemetry_entity_evidence (
  entity_kind TEXT NOT NULL,
  entity_id TEXT NOT NULL,
  evidence_id TEXT NOT NULL,
  contribution TEXT NOT NULL,
  precedence_rank INTEGER NOT NULL,
  conflict INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY(entity_kind,entity_id,evidence_id,contribution)
);
CREATE INDEX IF NOT EXISTS idx_ledger_entity_evidence
  ON telemetry_entity_evidence(evidence_id);

CREATE TABLE IF NOT EXISTS telemetry_call_repeats (
  run_id TEXT NOT NULL,
  agent_id TEXT NOT NULL,
  raw_name TEXT NOT NULL,
  input_sha256 TEXT NOT NULL,
  backend TEXT NOT NULL,
  model TEXT,
  project_id TEXT,
  origin TEXT NOT NULL,
  invocation_layer TEXT NOT NULL,
  family TEXT NOT NULL,
  repeats INTEGER NOT NULL,
  first_at REAL NOT NULL,
  last_at REAL NOT NULL,
  PRIMARY KEY(run_id,agent_id,raw_name,input_sha256)
);
CREATE INDEX IF NOT EXISTS idx_ledger_call_repeats_time
  ON telemetry_call_repeats(last_at,repeats);
"""

#: One row per (run, agent, tool, input hash): how many times that exact input ran.
#: Maintained on every tool-call write and read by the repeated-call finding, so the
#: finding costs the rows that repeated rather than a scan of every call in the window.
CALL_REPEATS_SELECT = (
    "SELECT run_id,agent_id,raw_name,COALESCE(executed_input_sha256,input_sha256) input_sha256,"
    "MIN(backend),MIN(model),MIN(project_id),MIN(origin),MIN(invocation_layer),MIN(family),"
    "COUNT(*),MIN(started_at),MAX(started_at) FROM telemetry_tool_calls "
    "WHERE COALESCE(executed_input_sha256,input_sha256) IS NOT NULL"
)
CALL_REPEATS_INSERT = (
    "INSERT INTO telemetry_call_repeats(run_id,agent_id,raw_name,input_sha256,backend,model,"
    "project_id,origin,invocation_layer,family,repeats,first_at,last_at) "
)

MigrationStep = str | Callable[[sqlite3.Connection], None]


def _recreate_rollups(connection: sqlite3.Connection) -> None:
    """Version 3 changed the tool rollup key (evidence quality joined it).

    Rollups are derived, so the old rows are dropped and every day that had one is
    dirtied again; the rollup worker rebuilds them from the entities, which is the
    only source of truth here.
    """

    now = time.time()
    connection.execute("DROP TABLE IF EXISTS tool_daily")
    connection.execute(TOOL_DAILY_TABLE)
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_tool_daily_window "
        "ON tool_daily(day,origin,backend,project_id)"
    )
    connection.execute(
        # `WHERE true` disambiguates the upsert clause after INSERT ... SELECT.
        "INSERT INTO rollup_dirty_days(day,dirtied_at) SELECT day,? FROM rollup_days "
        "WHERE true ON CONFLICT(day) DO UPDATE SET dirtied_at=excluded.dirtied_at",
        (now,),
    )
    connection.execute("DELETE FROM rollup_days")


def _backfill_evidence_quality(connection: sqlite3.Connection) -> None:
    connection.execute(
        "UPDATE telemetry_tool_calls SET evidence_quality=CASE "
        "WHEN result_source IS NULL THEN 'none' "
        "WHEN result_rank>=400 THEN 'native' "
        "WHEN result_rank>=300 THEN 'transcript' "
        "WHEN result_rank>=200 THEN 'hook' "
        "WHEN result_rank>=100 THEN 'reconciled' "
        "ELSE 'legacy' END"
    )
    connection.execute(
        "UPDATE telemetry_runs SET started_at_source='unknown' WHERE started_at_source IS NULL"
    )


def _dirty_rolled_buckets(connection: sqlite3.Connection) -> None:
    """Version 4 added quality rollups beside the tool and workload ones.

    Every day and hour that already has a rollup is dirtied again so the worker
    builds the new tables from the entities; nothing existing is dropped.
    """

    now = time.time()
    connection.execute(
        "INSERT INTO rollup_dirty_days(day,dirtied_at) SELECT day,? FROM rollup_days "
        "WHERE true ON CONFLICT(day) DO UPDATE SET dirtied_at=excluded.dirtied_at",
        (now,),
    )
    connection.execute(
        "INSERT INTO rollup_dirty_hours(hour,dirtied_at) SELECT hour,? FROM rollup_hours "
        "WHERE true ON CONFLICT(hour) DO UPDATE SET dirtied_at=excluded.dirtied_at",
        (now,),
    )


def _backfill_call_repeats(connection: sqlite3.Connection) -> None:
    """Version 4 keeps per-run repeat counts beside the calls; derive them once."""

    connection.execute("DELETE FROM telemetry_call_repeats")
    connection.execute(
        CALL_REPEATS_INSERT + CALL_REPEATS_SELECT + " GROUP BY 1,2,3,4"
    )


#: Additive statements per target version. Each `ADD COLUMN` is checked against
#: `PRAGMA table_info` before it runs, so a file created from the current schema
#: (which already carries the column) and a file created by an older daemon both
#: end at the same shape. Version 1 is the shape shipped on 2026-09-02; the
#: tables that were added in later versions are covered by the `IF NOT EXISTS`
#: schema text and need no statement here.
CATALOG_MIGRATIONS: dict[int, tuple[MigrationStep, ...]] = {
    2: (),
    3: (_recreate_rollups,),
    4: (_dirty_rolled_buckets,),
}
SEGMENT_MIGRATIONS: dict[int, tuple[MigrationStep, ...]] = {
    2: (
        "ALTER TABLE telemetry_tool_calls ADD COLUMN output_truncated INTEGER",
        "ALTER TABLE telemetry_tool_calls ADD COLUMN provider_sequence INTEGER",
        "ALTER TABLE telemetry_tool_calls ADD COLUMN native_conversation_id TEXT",
        "ALTER TABLE telemetry_tool_calls ADD COLUMN tool_namespace TEXT",
        "ALTER TABLE telemetry_tool_calls ADD COLUMN error_sha256 TEXT",
        "ALTER TABLE telemetry_model_requests ADD COLUMN first_token_ms REAL",
        "ALTER TABLE telemetry_model_requests ADD COLUMN reasoning_tokens INTEGER",
        "ALTER TABLE telemetry_model_requests ADD COLUMN client_request_id TEXT",
        "ALTER TABLE telemetry_model_requests ADD COLUMN endpoint TEXT",
        "ALTER TABLE telemetry_model_requests ADD COLUMN effort TEXT",
    ),
    3: (
        "ALTER TABLE telemetry_tool_calls ADD COLUMN evidence_quality TEXT NOT NULL DEFAULT 'none'",
        "ALTER TABLE telemetry_tool_calls ADD COLUMN approval_requested_at REAL",
        "ALTER TABLE telemetry_runs ADD COLUMN started_at_source TEXT",
        _backfill_evidence_quality,
    ),
    4: (_backfill_call_repeats,),
}


class LedgerSchemaError(RuntimeError):
    """A ledger file is newer than this daemon knows how to read."""


def _column_names(connection: sqlite3.Connection, table: str) -> set[str]:
    return {
        str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
    }


def _apply_step(connection: sqlite3.Connection, step: MigrationStep) -> bool:
    """Run one migration step, skipping an `ADD COLUMN` that already applies."""

    if callable(step):
        step(connection)
        return True
    tokens = step.split()
    if len(tokens) >= 6 and tokens[:2] == ["ALTER", "TABLE"] and tokens[3:5] == ["ADD", "COLUMN"]:
        table, column = tokens[2], tokens[5]
        if column in _column_names(connection, table):
            return False
    connection.execute(step)
    return True


def _read_version(connection: sqlite3.Connection, table: str) -> int:
    row = connection.execute(
        f"SELECT value FROM {table} WHERE key='schema_version'"
    ).fetchone()
    if row is None:
        return 0
    try:
        return int(row[0])
    except (TypeError, ValueError):
        return 0


def _write_version(connection: sqlite3.Connection, table: str, version: int) -> None:
    connection.execute(
        f"INSERT INTO {table}(key,value) VALUES('schema_version',?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (str(version),),
    )


def migrate(
    connection: sqlite3.Connection,
    *,
    schema: str,
    meta_table: str,
    migrations: dict[int, tuple[MigrationStep, ...]],
    kind: str,
) -> dict[str, Any]:
    """Bring one ledger file to `LEDGER_SCHEMA_VERSION`, additively and idempotently.

    Returns what happened so the caller can log it: the version found, the version
    written, and the steps that actually ran. A file stamped with a version this
    code does not know is refused rather than read with the wrong expectations.
    """

    connection.executescript(schema)
    found = _read_version(connection, meta_table)
    if found > LEDGER_SCHEMA_VERSION:
        raise LedgerSchemaError(
            f"{kind} schema version {found} is newer than supported "
            f"{LEDGER_SCHEMA_VERSION}"
        )
    # A file with no version row predates versioning (the 2026-09-02 shape) or was
    # just created from the current schema. The fresh file is told apart by having
    # no rows to migrate: its callables would otherwise dirty nothing and drop an
    # empty table, which is harmless but reads as a migration in the log.
    fresh = found == 0 and _is_fresh(connection, kind)
    applied: list[str] = []
    if not fresh:
        for version in range(max(found, 1) + 1, LEDGER_SCHEMA_VERSION + 1):
            for step in migrations.get(version, ()):
                if _apply_step(connection, step):
                    applied.append(step if isinstance(step, str) else step.__name__)
    _write_version(connection, meta_table, LEDGER_SCHEMA_VERSION)
    connection.commit()
    return {"found": found, "version": LEDGER_SCHEMA_VERSION, "applied": applied}


def _is_fresh(connection: sqlite3.Connection, kind: str) -> bool:
    table = "ledger_segments" if kind == "catalog" else "telemetry_evidence"
    row = connection.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone()
    return row is None


def migrate_catalog(connection: sqlite3.Connection) -> dict[str, Any]:
    return migrate(
        connection,
        schema=CATALOG_SCHEMA,
        meta_table="ledger_meta",
        migrations=CATALOG_MIGRATIONS,
        kind="catalog",
    )


def migrate_segment(connection: sqlite3.Connection) -> dict[str, Any]:
    return migrate(
        connection,
        schema=SEGMENT_SCHEMA,
        meta_table="segment_meta",
        migrations=SEGMENT_MIGRATIONS,
        kind="segment",
    )


def schema_signature(connection: sqlite3.Connection) -> str:
    """A digest of every table and index definition, for drift diagnostics.

    Two files with the same signature will answer the same SQL identically; a
    daemon comparing a live file's signature with its own expectation can tell an
    incomplete migration from a healthy one without inspecting columns by hand.
    """

    # Introspected rather than read from `sqlite_master.sql`: an `ADD COLUMN` appends
    # after a table's UNIQUE clause while a fresh CREATE lists it before, so the
    # statement text of two identical shapes differs and the columns do not.
    parts: list[str] = []
    tables = connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' "
        "ORDER BY name"
    ).fetchall()
    for (table,) in tables:
        columns = connection.execute(f"PRAGMA table_info({table})").fetchall()
        for column in sorted(columns, key=lambda item: str(item[1])):
            parts.append(
                f"column {table}.{column[1]} {str(column[2]).upper()} "
                f"notnull={column[3]} default={column[4]} pk={column[5]}"
            )
        for index in connection.execute(f"PRAGMA index_list({table})").fetchall():
            name, unique, origin = str(index[1]), int(index[2]), str(index[3])
            indexed = [
                str(row[2])
                for row in connection.execute(f"PRAGMA index_info({name})").fetchall()
            ]
            label = name if origin == "c" else f"{origin}:{','.join(indexed)}"
            parts.append(f"index {table} {label} unique={unique} columns={','.join(indexed)}")
    text = "\n".join(sorted(parts))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def expected_signature(kind: str) -> str:
    """The signature a fresh, fully migrated file of this kind carries."""

    connection = sqlite3.connect(":memory:")
    try:
        if kind == "catalog":
            migrate_catalog(connection)
        else:
            migrate_segment(connection)
        return schema_signature(connection)
    finally:
        connection.close()


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8", "replace")


def digest(value: bytes | str) -> str:
    data = value.encode("utf-8", "replace") if isinstance(value, str) else value
    return hashlib.sha256(data).hexdigest()


def period_of(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=UTC).strftime("%Y-%m")


def day_of(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=UTC).strftime("%Y-%m-%d")


def hour_of(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=UTC).strftime("%Y-%m-%dT%H")


def source_rank(source: str) -> int:
    return _SOURCE_RANK.get(source, 10)


def turn_entity_id(run_id: str, native_turn_id: str | None, ordinal: int | None) -> str | None:
    identity = native_turn_id or (f"ordinal:{ordinal}" if ordinal is not None else None)
    return digest(f"{run_id}\0{identity}") if identity is not None else None


def content_metrics(value: Any) -> tuple[int | None, int | None, str | None]:
    if value is None:
        return None, None, None
    if isinstance(value, bytes):
        return len(value.decode("utf-8", "replace")), len(value), digest(value)
    if isinstance(value, str):
        encoded = value.encode("utf-8", "replace")
        return len(value), len(encoded), digest(encoded)
    encoded = canonical_json(value)
    return len(encoded.decode("utf-8", "replace")), len(encoded), digest(encoded)


def _mcp_parts(value: str) -> tuple[str | None, str]:
    parts = value.split("__", 2)
    if len(parts) == 3 and parts[0] == "mcp":
        return parts[1] or None, parts[2] or "unknown"
    return None, value


def classify_tool(raw: str, *, backend: str, source: str) -> dict[str, str | None]:
    """Return independent tool dimensions without replacing the provider name."""

    value = raw.strip()
    normalized = value.casefold().replace("-", "_").replace(" ", "_") or "unknown"
    server, tool = _mcp_parts(normalized)
    transport = "mcp" if server else "native"
    harness = HARNESSES.get(backend)
    code_mode_tool = harness.code_mode_tool if harness is not None else None
    if code_mode_tool is not None and normalized == code_mode_tool and source != "hook":
        transport = "code_mode"
    elif normalized in {"bash", "shell", "shell_command", "exec_command", "powershell"}:
        transport = "shell"

    if tool in {"read", "read_file", "get_file", "view_image", "read_transcript"}:
        family, operation = "read", tool
    elif tool in {"write", "write_file", "edit", "edit_file", "apply_patch", "patch"}:
        family, operation = "file", "write"
    elif "search" in tool or tool in {"grep", "glob", "find", "rg"}:
        family, operation = "search", "search"
    elif tool in {"agent", "task", "spawn_agent", "request_spawn"}:
        family, operation = "agent", "spawn"
    elif tool in {"send_message", "notify", "followup_task"}:
        family, operation = "agent", "message"
    elif tool in {"wait", "wait_agent", "write_stdin", "monitor"}:
        family, operation = "agent", "wait"
    elif tool in {"skill", "invoke_skill"}:
        family, operation = "skill", "activate"
    elif tool in {"bash", "shell", "shell_command", "exec_command", "powershell", "exec"}:
        family, operation = "shell", "execute"
    elif tool in {"update_plan", "taskcreate", "taskupdate"}:
        family, operation = "planning", "update"
    elif tool in {"web", "web_search", "web_fetch", "browser", "webrun"}:
        family, operation = "web", "request"
    elif server:
        family, operation = "integration", tool
    else:
        family, operation = "other", tool
    return {
        "family": family,
        "operation": operation,
        "transport": transport,
        "server": server,
        "tool": tool,
    }


TOOL_FAMILIES = (
    "read",
    "file",
    "search",
    "agent",
    "skill",
    "shell",
    "planning",
    "web",
    "integration",
    "other",
)
