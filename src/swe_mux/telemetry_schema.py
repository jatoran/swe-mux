"""Schema, migrations, and pure helpers for the canonical telemetry ledger.

The ledger is two kinds of SQLite file: one catalog and one segment per UTC month.
Both are created from the full current schema and then walked through
`CATALOG_MIGRATIONS` / `SEGMENT_MIGRATIONS`, so a file written by an older daemon
gains exactly the additive columns and tables the current code reads, and a fresh
file records the current version without re-applying anything. `CREATE TABLE IF NOT
EXISTS` alone cannot do that: it never adds a column to a table that already exists,
which is how a redeploy would otherwise start failing on the first `INSERT`.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from typing import Any

from .harness import HARNESSES

LEDGER_SCHEMA_VERSION = 2

_SOURCE_RANK = {
    "otel": 400,
    "provider_otel": 400,
    "transcript": 300,
    "store": 300,
    "hook": 200,
    "reconciled_transcript": 100,
    "legacy": 50,
}

CATALOG_SCHEMA = """
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
CREATE TABLE IF NOT EXISTS tool_daily (
  day TEXT NOT NULL,
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
  calls INTEGER NOT NULL,
  duration_count INTEGER NOT NULL,
  duration_ms REAL NOT NULL,
  input_bytes INTEGER NOT NULL,
  output_bytes INTEGER NOT NULL,
  PRIMARY KEY(
    day,backend,model,project_id,origin,invocation_layer,family,operation,
    transport,raw_name,status
  )
);
CREATE INDEX IF NOT EXISTS idx_tool_daily_window
  ON tool_daily(day,origin,backend,project_id);
CREATE TABLE IF NOT EXISTS workload_daily (
  day TEXT NOT NULL,
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
  PRIMARY KEY(day,backend,model,project_id,origin)
);
CREATE TABLE IF NOT EXISTS rollup_days (
  day TEXT PRIMARY KEY,
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
  last_evidence_id TEXT NOT NULL
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
"""

#: Additive statements per target version. Each `ADD COLUMN` is checked against
#: `PRAGMA table_info` before it runs, so a file created from the current schema
#: (which already carries the column) and a file created by an older daemon both
#: end at the same shape. Version 1 is the shape shipped on 2026-09-02; the
#: tables that were added in later versions are covered by the `IF NOT EXISTS`
#: schema text and need no statement here.
CATALOG_MIGRATIONS: dict[int, tuple[str, ...]] = {
    2: (),
}
SEGMENT_MIGRATIONS: dict[int, tuple[str, ...]] = {
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
}


class LedgerSchemaError(RuntimeError):
    """A ledger file is newer than this daemon knows how to read."""


def _column_names(connection: sqlite3.Connection, table: str) -> set[str]:
    return {
        str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
    }


def _apply_statement(connection: sqlite3.Connection, statement: str) -> bool:
    """Run one migration statement, skipping an `ADD COLUMN` that already applies."""

    tokens = statement.split()
    if len(tokens) >= 6 and tokens[:2] == ["ALTER", "TABLE"] and tokens[3:5] == ["ADD", "COLUMN"]:
        table, column = tokens[2], tokens[5]
        if column in _column_names(connection, table):
            return False
    connection.execute(statement)
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
    migrations: dict[int, tuple[str, ...]],
    kind: str,
) -> dict[str, Any]:
    """Bring one ledger file to `LEDGER_SCHEMA_VERSION`, additively and idempotently.

    Returns what happened so the caller can log it: the version found, the version
    written, and the statements that actually ran. A file stamped with a version this
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
    # just created from the current schema; both walk every migration, and the
    # column checks make that a no-op for the fresh file.
    applied: list[str] = []
    for version in range(max(found, 1) + 1, LEDGER_SCHEMA_VERSION + 1):
        for statement in migrations.get(version, ()):
            if _apply_statement(connection, statement):
                applied.append(statement)
    _write_version(connection, meta_table, LEDGER_SCHEMA_VERSION)
    connection.commit()
    return {"found": found, "version": LEDGER_SCHEMA_VERSION, "applied": applied}


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

    rows = connection.execute(
        "SELECT type,name,sql FROM sqlite_master WHERE sql IS NOT NULL "
        "AND name NOT LIKE 'sqlite_%' ORDER BY type,name"
    ).fetchall()
    text = "\n".join(f"{row[0]} {row[1]} {' '.join(str(row[2]).split())}" for row in rows)
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
