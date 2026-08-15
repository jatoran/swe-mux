from __future__ import annotations

import asyncio
import json
import logging
import re
import sqlite3
import time
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any, TypeVar

from .git_projects import ProjectIdentity
from .harness import AGENT_BACKENDS, has_observable_transcript, is_agent_harness
from .models import MuxEvent, ProjectGroupRecord, ProjectRecord, SessionRecord
from .spawn_contract import infer_agent_executable_backend
from .sqlite_store import (
    connect_or_quarantine,
    database_operation_lock,
    run_sqlite_operation,
)
from .transcript_view import (
    TRANSCRIPT_PARSER_VERSION,
    conversation_is_readable,
    conversation_watermark,
    parse_transcript,
    searchable_transcript_messages,
    transcript_time_summary,
)

T = TypeVar("T")
log = logging.getLogger(__name__)
_AGENT_BACKEND_ARGS = tuple(sorted(AGENT_BACKENDS))
_AGENT_BACKEND_SQL = ",".join("?" for _ in _AGENT_BACKEND_ARGS)


def _public_history_row(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    raw = item.pop("provider_account_hashes_json", "{}")
    try:
        hashes = json.loads(raw or "{}")
    except (TypeError, json.JSONDecodeError):
        hashes = {}
    item["provider_account_hashes"] = (
        {str(provider): str(account_hash) for provider, account_hash in hashes.items()}
        if isinstance(hashes, dict)
        else {}
    )
    return item

# Exit reasons that mark a run as deliberately hidden because it was proven to be
# a cross-attribution artifact. No migration or backfill may make these visible
# again; only an explicit repair path may.
QUARANTINE_EXIT_REASONS = (
    "root_identity_reconciled",
    "historical_provider_collision_reconciled",
)
_QUARANTINE_REASON_SQL = ",".join(f"'{reason}'" for reason in QUARANTINE_EXIT_REASONS)

SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS project_groups (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, position INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS projects (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, root TEXT NOT NULL UNIQUE,
  position INTEGER NOT NULL, group_id TEXT, layout_json TEXT,
  default_backend TEXT, layout_revision INTEGER NOT NULL DEFAULT 0,
  default_profile_id TEXT, resource_open_mode TEXT,
  git_compare_ref TEXT, default_agent_profiles_json TEXT,
  sidebar_visible INTEGER NOT NULL DEFAULT 1,
  created_at REAL NOT NULL DEFAULT 0,
  last_used_at REAL NOT NULL DEFAULT 0,
  deleted_at REAL
);
CREATE TABLE IF NOT EXISTS history (
  id TEXT PRIMARY KEY, native_id TEXT NOT NULL, backend TEXT NOT NULL,
  name TEXT NOT NULL, cwd TEXT NOT NULL, project_id TEXT, note_id TEXT,
  agent_run_seq INTEGER NOT NULL DEFAULT 0,
  spawned_at REAL NOT NULL, exited_at REAL, exit_reason TEXT,
  tokens_in INTEGER NOT NULL DEFAULT 0, tokens_out INTEGER NOT NULL DEFAULT 0,
  tokens_cache_read INTEGER NOT NULL DEFAULT 0,
  tokens_cache_write INTEGER NOT NULL DEFAULT 0,
  cost_usd REAL NOT NULL DEFAULT 0,
  transcript_path TEXT, external INTEGER NOT NULL DEFAULT 0,
  executable TEXT, argv_json TEXT, pinned_attention INTEGER NOT NULL DEFAULT 0,
  shell_profile_id TEXT, agent_visible INTEGER NOT NULL DEFAULT 0,
  repository_id TEXT, project_label TEXT, project_root TEXT,
  final_state TEXT, context_window INTEGER, final_context_pct REAL,
  peak_context_pct REAL, provider TEXT, provider_account_hashes_json TEXT NOT NULL DEFAULT '{}',
  model TEXT, measurement_source TEXT,
  compaction_count INTEGER NOT NULL DEFAULT 0, last_compaction_at REAL,
  compaction_capability TEXT, compaction_confidence TEXT,
  project_scope_id TEXT, repo_group_id TEXT,
  auto_named INTEGER NOT NULL DEFAULT 1,
  transcript_mtime_ns INTEGER, transcript_size INTEGER,
  native_started_at REAL, last_message_at REAL, last_message_role TEXT,
  time_summary_mtime_ns INTEGER, time_summary_size INTEGER
);
CREATE TABLE IF NOT EXISTS history_messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  history_id TEXT NOT NULL, ordinal INTEGER NOT NULL, role TEXT NOT NULL,
  ts TEXT, ts_epoch REAL, text TEXT NOT NULL, source_mtime_ns INTEGER NOT NULL,
  source_size INTEGER NOT NULL, parser_version INTEGER NOT NULL,
  UNIQUE(history_id, ordinal)
);
CREATE TABLE IF NOT EXISTS history_transcript_index (
  history_id TEXT PRIMARY KEY, source_mtime_ns INTEGER NOT NULL,
  source_size INTEGER NOT NULL, parser_version INTEGER NOT NULL,
  message_count INTEGER NOT NULL, indexed_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
  seq INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, session_id TEXT,
  source TEXT NOT NULL, type TEXT NOT NULL, payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS links (
  session_a TEXT NOT NULL, session_b TEXT NOT NULL, mode TEXT NOT NULL,
  created_at REAL NOT NULL, PRIMARY KEY(session_a, session_b)
);
CREATE TABLE IF NOT EXISTS repo_groups (
  id TEXT PRIMARY KEY, label TEXT NOT NULL, source TEXT NOT NULL,
  created_at REAL NOT NULL, last_activity REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS project_scopes (
  id TEXT PRIMARY KEY, root TEXT NOT NULL UNIQUE, label TEXT NOT NULL,
  source TEXT NOT NULL, repo_group_id TEXT, hidden INTEGER NOT NULL DEFAULT 0,
  created_at REAL NOT NULL, last_activity REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS artifacts (
  id TEXT PRIMARY KEY, kind TEXT NOT NULL, owner_type TEXT NOT NULL,
  owner_id TEXT NOT NULL, owner_label TEXT, project_scope_id TEXT NOT NULL,
  relative_path TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL,
  placement_acknowledged_scope_id TEXT,
  UNIQUE(kind,owner_type,owner_id)
);
CREATE TABLE IF NOT EXISTS git_provenance (
  id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL,
  session_name TEXT NOT NULL,
  agent_run_id TEXT NOT NULL DEFAULT '',
  project_id TEXT NOT NULL,
  worktree_root TEXT NOT NULL,
  commit_oid TEXT NOT NULL,
  parent_oids_json TEXT NOT NULL DEFAULT '[]',
  subject TEXT NOT NULL DEFAULT '',
  committed_at REAL,
  previous_head TEXT,
  relationship TEXT NOT NULL,
  confidence TEXT NOT NULL,
  ambiguous INTEGER NOT NULL DEFAULT 0,
  source TEXT NOT NULL,
  source_event_seq INTEGER,
  tool_call_id TEXT,
  evidence_rank INTEGER NOT NULL,
  observed_at REAL NOT NULL,
  updated_at REAL NOT NULL,
  UNIQUE(session_id,agent_run_id,worktree_root,commit_oid)
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id, ts);
CREATE INDEX IF NOT EXISTS idx_history_spawned ON history(spawned_at DESC);
CREATE INDEX IF NOT EXISTS idx_history_messages_history ON history_messages(history_id, ordinal);
CREATE INDEX IF NOT EXISTS idx_history_messages_source
  ON history_messages(history_id, source_mtime_ns, source_size, parser_version);
CREATE INDEX IF NOT EXISTS idx_git_provenance_project
  ON git_provenance(project_id, observed_at DESC);
CREATE INDEX IF NOT EXISTS idx_git_provenance_session
  ON git_provenance(session_id, observed_at DESC);
CREATE INDEX IF NOT EXISTS idx_git_provenance_run
  ON git_provenance(agent_run_id, observed_at DESC);
CREATE INDEX IF NOT EXISTS idx_git_provenance_commit
  ON git_provenance(project_id, commit_oid);
"""

_MESSAGE_SEARCH_TABLES = ("history_messages_fts", "history_messages_trigram")
_GIT_PROVENANCE_UPSERT = (
    "INSERT INTO git_provenance("
    "id,session_id,session_name,agent_run_id,project_id,worktree_root,commit_oid,"
    "parent_oids_json,subject,committed_at,previous_head,relationship,confidence,"
    "ambiguous,source,source_event_seq,tool_call_id,evidence_rank,observed_at,updated_at"
    ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
    "ON CONFLICT(session_id,agent_run_id,worktree_root,commit_oid) DO UPDATE SET "
    "session_name=excluded.session_name,project_id=excluded.project_id,"
    "parent_oids_json=CASE WHEN excluded.parent_oids_json!='[]' "
    "THEN excluded.parent_oids_json ELSE git_provenance.parent_oids_json END,"
    "subject=CASE WHEN excluded.subject!='' "
    "THEN excluded.subject ELSE git_provenance.subject END,"
    "committed_at=COALESCE(excluded.committed_at,git_provenance.committed_at),"
    "previous_head=CASE WHEN excluded.evidence_rank>=git_provenance.evidence_rank "
    "THEN excluded.previous_head ELSE git_provenance.previous_head END,"
    "relationship=CASE WHEN excluded.evidence_rank>=git_provenance.evidence_rank "
    "THEN excluded.relationship ELSE git_provenance.relationship END,"
    "confidence=CASE WHEN excluded.evidence_rank>=git_provenance.evidence_rank "
    "THEN excluded.confidence ELSE git_provenance.confidence END,"
    "ambiguous=CASE WHEN excluded.evidence_rank>=git_provenance.evidence_rank "
    "THEN excluded.ambiguous ELSE git_provenance.ambiguous END,"
    "source=CASE WHEN excluded.evidence_rank>=git_provenance.evidence_rank "
    "THEN excluded.source ELSE git_provenance.source END,"
    "source_event_seq=CASE WHEN excluded.evidence_rank>=git_provenance.evidence_rank "
    "THEN excluded.source_event_seq ELSE git_provenance.source_event_seq END,"
    "tool_call_id=CASE WHEN excluded.evidence_rank>=git_provenance.evidence_rank "
    "THEN excluded.tool_call_id ELSE git_provenance.tool_call_id END,"
    "evidence_rank=MAX(git_provenance.evidence_rank,excluded.evidence_rank),"
    "observed_at=MIN(git_provenance.observed_at,excluded.observed_at),"
    "updated_at=MAX(git_provenance.updated_at,excluded.updated_at)"
)
_MESSAGE_SEARCH_TRIGGERS = (
    "history_messages_ai",
    "history_messages_ad",
    "history_messages_au",
    "history_messages_trigram_ai",
    "history_messages_trigram_ad",
    "history_messages_trigram_au",
)
_MESSAGE_SEARCH_STATE_SCHEMA = """
CREATE TABLE IF NOT EXISTS history_message_search_maintenance (
  id INTEGER PRIMARY KEY CHECK(id=1),
  target_max_id INTEGER NOT NULL,
  cursor_id INTEGER NOT NULL,
  reset_required INTEGER NOT NULL,
  ready INTEGER NOT NULL,
  updated_at REAL NOT NULL,
  last_error TEXT
);
"""
_MESSAGE_SEARCH_INDEX_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS history_messages_fts USING fts5(
  text, content='history_messages', content_rowid='id', tokenize='unicode61 remove_diacritics 2'
);
CREATE VIRTUAL TABLE IF NOT EXISTS history_messages_trigram USING fts5(
  text, content='history_messages', content_rowid='id', tokenize='trigram case_sensitive 0'
);
"""
_MESSAGE_SEARCH_TRIGGER_SCHEMA = """
CREATE TRIGGER history_messages_ai AFTER INSERT ON history_messages BEGIN
  INSERT INTO history_messages_fts(rowid,text) VALUES(new.id,new.text);
END;
CREATE TRIGGER history_messages_ad AFTER DELETE ON history_messages
WHEN (SELECT ready OR old.id<=cursor_id OR old.id>target_max_id
      FROM history_message_search_maintenance WHERE id=1) BEGIN
  INSERT INTO history_messages_fts(history_messages_fts,rowid,text)
  VALUES('delete',old.id,old.text);
END;
CREATE TRIGGER history_messages_au AFTER UPDATE OF text ON history_messages
WHEN (SELECT ready OR old.id<=cursor_id OR old.id>target_max_id
      FROM history_message_search_maintenance WHERE id=1) BEGIN
  INSERT INTO history_messages_fts(history_messages_fts,rowid,text)
  VALUES('delete',old.id,old.text);
  INSERT INTO history_messages_fts(rowid,text) VALUES(new.id,new.text);
END;
CREATE TRIGGER history_messages_trigram_ai AFTER INSERT ON history_messages BEGIN
  INSERT INTO history_messages_trigram(rowid,text) VALUES(new.id,new.text);
END;
CREATE TRIGGER history_messages_trigram_ad AFTER DELETE ON history_messages
WHEN (SELECT ready OR old.id<=cursor_id OR old.id>target_max_id
      FROM history_message_search_maintenance WHERE id=1) BEGIN
  INSERT INTO history_messages_trigram(history_messages_trigram,rowid,text)
  VALUES('delete',old.id,old.text);
END;
CREATE TRIGGER history_messages_trigram_au AFTER UPDATE OF text ON history_messages
WHEN (SELECT ready OR old.id<=cursor_id OR old.id>target_max_id
      FROM history_message_search_maintenance WHERE id=1) BEGIN
  INSERT INTO history_messages_trigram(history_messages_trigram,rowid,text)
  VALUES('delete',old.id,old.text);
  INSERT INTO history_messages_trigram(rowid,text) VALUES(new.id,new.text);
END;
"""


def _fts_query(value: str, mode: str = "all_terms") -> str:
    """Create a literal FTS query; never expose raw FTS syntax."""
    tokens = re.findall(r"\w+", value, flags=re.UNICODE)
    escaped = [token.replace(chr(34), chr(34) * 2) for token in tokens]
    if mode == "phrase":
        return f'"{" ".join(escaped)}"' if escaped else ""
    separator = " OR " if mode == "any_terms" else " AND "
    return separator.join(f'"{token}"*' for token in escaped)


def _trigram_query(value: str) -> str:
    """Literal substring query for the FTS5 trigram index."""
    text = value.strip()
    if len(text) < 3:
        return ""
    return f'"{text.replace(chr(34), chr(34) * 2)}"'


def _like_pattern(value: str) -> str:
    """Literal case-insensitive LIKE pattern with wildcard characters escaped."""
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _like_query_predicate(
    value: str, mode: str, *, column: str = "hm.text"
) -> tuple[str, list[str]]:
    """Bounded-search fallback while rebuildable FTS indexes are unavailable."""
    if mode in {"phrase", "substring"}:
        return f"{column} LIKE ? ESCAPE '\\'", [_like_pattern(value)]
    tokens = re.findall(r"\w+", value, flags=re.UNICODE)
    if not tokens:
        return "0", []
    separator = " OR " if mode == "any_terms" else " AND "
    return (
        "(" + separator.join(f"{column} LIKE ? ESCAPE '\\'" for _ in tokens) + ")",
        [_like_pattern(token) for token in tokens],
    )


def _search_excerpt(text: str, query: str, max_chars: int = 480) -> str:
    """Return a bounded literal window centered on the first useful match."""
    collapsed = re.sub(r"\s+", " ", text).strip()
    if len(collapsed) <= max_chars:
        return collapsed
    folded = collapsed.casefold()
    needles = [query.strip(), *re.findall(r"\w+", query, flags=re.UNICODE)]
    positions = [folded.find(needle.casefold()) for needle in needles if needle.strip()]
    found = [position for position in positions if position >= 0]
    center = min(found) if found else 0
    start = max(0, center - max_chars // 3)
    end = min(len(collapsed), start + max_chars)
    start = max(0, end - max_chars)
    prefix = "... " if start else ""
    suffix = " ..." if end < len(collapsed) else ""
    return f"{prefix}{collapsed[start:end].strip()}{suffix}"


def _message_timestamp(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        stamp = float(value)
        return stamp / 1000 if stamp > 10_000_000_000 else stamp
    if isinstance(value, str) and value.strip():
        text = value.strip()
        try:
            stamp = float(text)
        except ValueError:
            try:
                return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
            except ValueError:
                return None
        return stamp / 1000 if stamp > 10_000_000_000 else stamp
    return None


def _string_map(value: Any) -> dict[str, str]:
    """A stored JSON object read back as a string map, or empty on anything else.

    Fails to an empty map rather than raising: this column is read while listing
    Projects at startup, and one row written by a newer build (or hand-edited) must
    not stop the sidebar from loading.
    """
    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        parsed = json.loads(value)
    except ValueError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    return {str(key): str(item) for key, item in parsed.items() if isinstance(item, str)}


def _tune_connection(db: sqlite3.Connection) -> None:
    """Apply per-connection SQLite pragmas that bound write-latency and cache cost.

    ``journal_mode=WAL`` is set in the schema (it persists in the database file);
    ``synchronous`` and the cache/mmap settings are per-connection and must be set
    on every open. ``synchronous=NORMAL`` is crash-safe under WAL and removes the
    per-commit fsync that otherwise stalls the event loop on every write.
    """
    db.execute("PRAGMA synchronous=NORMAL")
    db.execute("PRAGMA busy_timeout=5000")
    db.execute("PRAGMA cache_size=-16000")
    db.execute("PRAGMA mmap_size=268435456")


class HistoryIndex:
    """SQLite index whose every operation runs on one dedicated worker thread.

    All ``sqlite3`` access is confined to a single-worker executor so that queries
    and commits never run on the aiohttp event loop. The one worker serializes DB
    work in submission order, which preserves per-method atomicity without a lock.
    """

    _db: sqlite3.Connection

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._path = path
        self._operation_lock = database_operation_lock(path)
        self._closed = False
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mux-history-db")
        # Create and initialise the connection on the worker thread so the
        # connection object keeps strict thread affinity for its whole lifetime.
        self._executor.submit(self._connect).result()

    def _connect(self) -> None:
        # Confined to the single worker thread (queries via _run, close via the
        # executor), so there is never concurrent access. check_same_thread=False
        # additionally tolerates benign cross-thread introspection (tests reading
        # ``_db`` directly, a fallback close) without weakening that guarantee.
        with self._operation_lock:
            self._db = connect_or_quarantine(self._path, self._open)
            self._db.executescript(SCHEMA)
            self._migrate_schema()
            self._db.commit()
            try:
                self._initialize_message_search_schema()
            except Exception:
                # Search indexes are rebuildable derivatives. A failed optional
                # migration must not make the terminal daemon unavailable.
                self._db.rollback()
                log.exception(
                    "history message search schema initialization failed; "
                    "daemon startup will continue with bounded LIKE search"
                )

    def _open(self) -> sqlite3.Connection:
        db = sqlite3.connect(self._path, check_same_thread=False)
        db.row_factory = sqlite3.Row
        db.create_function("mux_message_timestamp", 1, _message_timestamp, deterministic=True)
        _tune_connection(db)
        return db

    async def _run(self, fn: Callable[[], T]) -> T:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self._executor, run_sqlite_operation, self._db, self._operation_lock, fn
        )

    def _migrate_schema(self) -> None:
        """Apply additive migrations to databases created by earlier releases."""
        columns = {row["name"] for row in self._db.execute("PRAGMA table_info(history)").fetchall()}
        migrations = {
            "executable": "ALTER TABLE history ADD COLUMN executable TEXT",
            "argv_json": "ALTER TABLE history ADD COLUMN argv_json TEXT",
            "pinned_attention": (
                "ALTER TABLE history ADD COLUMN pinned_attention INTEGER NOT NULL DEFAULT 0"
            ),
            "shell_profile_id": "ALTER TABLE history ADD COLUMN shell_profile_id TEXT",
            "agent_visible": (
                "ALTER TABLE history ADD COLUMN agent_visible INTEGER NOT NULL DEFAULT 0"
            ),
            "project_id": "ALTER TABLE history ADD COLUMN project_id TEXT",
            "note_id": "ALTER TABLE history ADD COLUMN note_id TEXT",
            "agent_run_seq": (
                "ALTER TABLE history ADD COLUMN agent_run_seq INTEGER NOT NULL DEFAULT 0"
            ),
            "repository_id": "ALTER TABLE history ADD COLUMN repository_id TEXT",
            "project_label": "ALTER TABLE history ADD COLUMN project_label TEXT",
            "project_root": "ALTER TABLE history ADD COLUMN project_root TEXT",
            "final_state": "ALTER TABLE history ADD COLUMN final_state TEXT",
            "context_window": "ALTER TABLE history ADD COLUMN context_window INTEGER",
            "final_context_pct": "ALTER TABLE history ADD COLUMN final_context_pct REAL",
            "peak_context_pct": "ALTER TABLE history ADD COLUMN peak_context_pct REAL",
            "model": "ALTER TABLE history ADD COLUMN model TEXT",
            "provider": "ALTER TABLE history ADD COLUMN provider TEXT",
            "provider_account_hashes_json": (
                "ALTER TABLE history ADD COLUMN provider_account_hashes_json "
                "TEXT NOT NULL DEFAULT '{}'"
            ),
            "measurement_source": "ALTER TABLE history ADD COLUMN measurement_source TEXT",
            "tokens_cache_read": (
                "ALTER TABLE history ADD COLUMN tokens_cache_read INTEGER NOT NULL DEFAULT 0"
            ),
            "tokens_cache_write": (
                "ALTER TABLE history ADD COLUMN tokens_cache_write INTEGER NOT NULL DEFAULT 0"
            ),
            "cost_usd": "ALTER TABLE history ADD COLUMN cost_usd REAL NOT NULL DEFAULT 0",
            "compaction_count": (
                "ALTER TABLE history ADD COLUMN compaction_count INTEGER NOT NULL DEFAULT 0"
            ),
            "last_compaction_at": "ALTER TABLE history ADD COLUMN last_compaction_at REAL",
            "compaction_capability": ("ALTER TABLE history ADD COLUMN compaction_capability TEXT"),
            "compaction_confidence": ("ALTER TABLE history ADD COLUMN compaction_confidence TEXT"),
            "project_scope_id": "ALTER TABLE history ADD COLUMN project_scope_id TEXT",
            "repo_group_id": "ALTER TABLE history ADD COLUMN repo_group_id TEXT",
            "auto_named": ("ALTER TABLE history ADD COLUMN auto_named INTEGER NOT NULL DEFAULT 1"),
            "transcript_mtime_ns": "ALTER TABLE history ADD COLUMN transcript_mtime_ns INTEGER",
            "transcript_size": "ALTER TABLE history ADD COLUMN transcript_size INTEGER",
            "last_message_at": "ALTER TABLE history ADD COLUMN last_message_at REAL",
            "last_message_role": "ALTER TABLE history ADD COLUMN last_message_role TEXT",
            "native_started_at": "ALTER TABLE history ADD COLUMN native_started_at REAL",
            "time_summary_mtime_ns": (
                "ALTER TABLE history ADD COLUMN time_summary_mtime_ns INTEGER"
            ),
            "time_summary_size": "ALTER TABLE history ADD COLUMN time_summary_size INTEGER",
        }
        message_summary_added = (
            "last_message_at" not in columns
            or "last_message_role" not in columns
            or "native_started_at" not in columns
        )
        agent_visible_added = "agent_visible" not in columns
        for column, statement in migrations.items():
            if column not in columns:
                self._db.execute(statement)
        if "agent_run_seq" not in columns:
            # Existing mux-owned rollover rows share ``note_id``. Their spawn
            # order reconstructs the sequence without inspecting transcripts.
            self._db.execute(
                "UPDATE history SET agent_run_seq=(SELECT COUNT(*) FROM history earlier "
                "WHERE earlier.note_id=history.note_id AND earlier.external=0 "
                "AND (earlier.spawned_at<history.spawned_at OR "
                "(earlier.spawned_at=history.spawned_at AND earlier.id<history.id))) "
                "WHERE external=0"
            )
        self._db.execute("UPDATE history SET note_id=id WHERE note_id IS NULL")
        if message_summary_added:
            rows = self._db.execute(
                "SELECT h.id,first.ts AS first_ts,last.ts AS last_ts,last.role FROM history h "
                "LEFT JOIN history_messages first ON first.id=(SELECT earliest.id "
                "FROM history_messages earliest WHERE earliest.history_id=h.id "
                "ORDER BY earliest.ordinal LIMIT 1) "
                "LEFT JOIN history_messages last ON last.id=(SELECT latest.id "
                "FROM history_messages latest WHERE latest.history_id=h.id "
                "ORDER BY latest.ordinal DESC LIMIT 1)"
            ).fetchall()
            self._db.executemany(
                "UPDATE history SET native_started_at=?,last_message_at=?,last_message_role=? "
                "WHERE id=?",
                [
                    (
                        _message_timestamp(row["first_ts"]),
                        _message_timestamp(row["last_ts"]),
                        row["role"],
                        row["id"],
                    )
                    for row in rows
                ],
            )
        if agent_visible_added:
            # One-shot backfill for rows written before the column existed. It
            # must never run again: quarantine sets agent_visible=0 and leaves the
            # row otherwise intact, so an unconditional backfill resurrected every
            # misattributed run on every daemon start — silently undoing the
            # cross-attribution repair layer within one session-preserving reload.
            self._db.execute(
                f"UPDATE history SET agent_visible=1 WHERE backend IN ({_AGENT_BACKEND_SQL}) "
                f"AND (exit_reason IS NULL OR exit_reason NOT IN ({_QUARANTINE_REASON_SQL}))",
                _AGENT_BACKEND_ARGS,
            )
        self._db.execute(
            "DELETE FROM history WHERE backend='shell' AND agent_visible=0 AND "
            "(transcript_path IS NULL OR transcript_path='')"
        )
        artifact_columns = {
            row["name"] for row in self._db.execute("PRAGMA table_info(artifacts)").fetchall()
        }
        if "placement_acknowledged_scope_id" not in artifact_columns:
            self._db.execute(
                "ALTER TABLE artifacts ADD COLUMN placement_acknowledged_scope_id TEXT"
            )
        if "owner_label" not in artifact_columns:
            self._db.execute("ALTER TABLE artifacts ADD COLUMN owner_label TEXT")
        project_columns = {
            row["name"] for row in self._db.execute("PRAGMA table_info(projects)").fetchall()
        }
        if "sidebar_visible" not in project_columns:
            self._db.execute(
                "ALTER TABLE projects ADD COLUMN sidebar_visible INTEGER NOT NULL DEFAULT 1"
            )
        if "created_at" not in project_columns:
            self._db.execute("ALTER TABLE projects ADD COLUMN created_at REAL NOT NULL DEFAULT 0")
            # Registration was never dated before this column, so the earliest session
            # ever spawned in the Project is the closest evidence the database holds.
            # One that never ran one keeps 0 and is read as unknown by every consumer.
            self._db.execute(
                "UPDATE projects SET created_at=COALESCE("
                "(SELECT MIN(h.spawned_at) FROM history h WHERE h.project_id=projects.id),0)"
            )
        if "last_used_at" not in project_columns:
            self._db.execute(
                "ALTER TABLE projects ADD COLUMN last_used_at REAL NOT NULL DEFAULT 0"
            )
            # Older databases have no exact prompt-submit record. A non-imported
            # session start is the closest durable evidence of explicit prior use
            # and avoids resetting every upgraded sidebar to manual tie order.
            self._db.execute(
                "UPDATE projects SET last_used_at=COALESCE("
                "(SELECT MAX(h.spawned_at) FROM history h WHERE h.project_id=projects.id "
                "AND h.external=0),0)"
            )
        if "deleted_at" not in project_columns:
            self._db.execute("ALTER TABLE projects ADD COLUMN deleted_at REAL")

        if "git_compare_ref" not in project_columns:
            self._db.execute("ALTER TABLE projects ADD COLUMN git_compare_ref TEXT")
        if "default_agent_profiles_json" not in project_columns:
            # One column rather than one per harness: the set of harnesses is a
            # registry, and a schema that had to change whenever a harness was added
            # would make adding one a database migration.
            self._db.execute(
                "ALTER TABLE projects ADD COLUMN default_agent_profiles_json TEXT"
            )
        self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_history_agent_project "
            "ON history(agent_visible,project_id,spawned_at DESC)"
        )
        self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_history_agent_filters "
            "ON history(agent_visible,backend,project_id,external,spawned_at DESC)"
        )
        # The sidebar's "recently active" ordering groups every history row by
        # Project on each projects payload; without this it is a full-table scan.
        self._db.execute("CREATE INDEX IF NOT EXISTS idx_history_project ON history(project_id)")
        # Project-scope filters/joins (history list, projects sidebar, scope counts)
        # otherwise fall back to full-table scans.
        self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_history_scope "
            "ON history(project_scope_id,spawned_at DESC)"
        )
        self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_artifacts_scope ON artifacts(project_scope_id)"
        )

    def _drop_message_search_triggers(self) -> None:
        for trigger in _MESSAGE_SEARCH_TRIGGERS:
            self._db.execute(f"DROP TRIGGER IF EXISTS {trigger}")

    def _create_message_search_triggers(self) -> None:
        self._drop_message_search_triggers()
        self._db.executescript(_MESSAGE_SEARCH_TRIGGER_SCHEMA)

    def _initialize_message_search_schema(self) -> None:
        """Install only bounded search schema work on the startup path.

        Existing FTS indexes may predate their content or be half-created by an
        interrupted migration. They are never read until post-startup maintenance
        has reset and repopulated them in committed batches.
        """
        existing_tables = {
            str(row["name"])
            for row in self._db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name IN (?,?)",
                _MESSAGE_SEARCH_TABLES,
            )
        }
        message_columns = {
            str(row["name"])
            for row in self._db.execute("PRAGMA table_info(history_messages)")
        }
        self._drop_message_search_triggers()
        if "ts_epoch" not in message_columns:
            self._db.execute("ALTER TABLE history_messages ADD COLUMN ts_epoch REAL")
        self._db.executescript(_MESSAGE_SEARCH_STATE_SCHEMA)
        self._db.executescript(_MESSAGE_SEARCH_INDEX_SCHEMA)
        state = self._db.execute(
            "SELECT target_max_id,cursor_id,reset_required,ready "
            "FROM history_message_search_maintenance WHERE id=1"
        ).fetchone()
        if state is None:
            target = int(
                self._db.execute("SELECT COALESCE(MAX(id),0) FROM history_messages").fetchone()[0]
            )
            indexes_are_new = len(existing_tables) == 0
            ready = int(target == 0 and indexes_are_new)
            self._db.execute(
                "INSERT INTO history_message_search_maintenance"
                "(id,target_max_id,cursor_id,reset_required,ready,updated_at,last_error) "
                "VALUES(1,?,?,?,?,?,NULL)",
                (target, target if ready else 0, int(not ready), ready, time.time()),
            )
            if ready:
                self._db.execute(
                    "CREATE INDEX IF NOT EXISTS idx_history_messages_time "
                    "ON history_messages(history_id,ts_epoch)"
                )
        elif existing_tables != set(_MESSAGE_SEARCH_TABLES):
            target = int(
                self._db.execute("SELECT COALESCE(MAX(id),0) FROM history_messages").fetchone()[0]
            )
            self._db.execute(
                "UPDATE history_message_search_maintenance SET target_max_id=?,cursor_id=0,"
                "reset_required=1,ready=0,updated_at=?,last_error=NULL WHERE id=1",
                (target, time.time()),
            )
        self._create_message_search_triggers()
        self._db.commit()

    def _message_search_state(self) -> dict[str, Any]:
        try:
            row = self._db.execute(
                "SELECT target_max_id,cursor_id,reset_required,ready,updated_at,last_error "
                "FROM history_message_search_maintenance WHERE id=1"
            ).fetchone()
        except sqlite3.Error:
            row = None
        if row is None:
            return {
                "ready": False,
                "target_max_id": 0,
                "cursor_id": 0,
                "reset_required": True,
                "last_error": "search schema unavailable",
            }
        return {
            "ready": bool(row["ready"]),
            "target_max_id": int(row["target_max_id"]),
            "cursor_id": int(row["cursor_id"]),
            "reset_required": bool(row["reset_required"]),
            "updated_at": float(row["updated_at"]),
            "last_error": row["last_error"],
        }

    async def message_search_status(self) -> dict[str, Any]:
        return await self._run(self._message_search_state)

    def _reset_message_search_indexes(self) -> dict[str, Any]:
        state = self._message_search_state()
        if not state["reset_required"]:
            return state
        # The original token index was introduced without rebuilding rows that
        # already existed. The first trigram migration had the same exposure.
        # Reset both rebuildable indexes together instead of trying to infer which
        # one is inconsistent from SQLite's generic SQLITE_CORRUPT_VTAB message.
        self._drop_message_search_triggers()
        try:
            for table in _MESSAGE_SEARCH_TABLES:
                self._db.execute(f"INSERT INTO {table}({table}) VALUES('delete-all')")
            self._db.commit()
        except sqlite3.Error:
            self._db.rollback()
            log.warning(
                "history message FTS reset command failed; recreating derivative tables",
                exc_info=True,
            )
            for table in _MESSAGE_SEARCH_TABLES:
                self._db.execute(f"DROP TABLE IF EXISTS {table}")
            self._db.executescript(_MESSAGE_SEARCH_INDEX_SCHEMA)
        target = int(
            self._db.execute("SELECT COALESCE(MAX(id),0) FROM history_messages").fetchone()[0]
        )
        self._create_message_search_triggers()
        ready = int(target == 0)
        self._db.execute(
            "UPDATE history_message_search_maintenance SET target_max_id=?,cursor_id=?,"
            "reset_required=0,ready=?,updated_at=?,last_error=NULL WHERE id=1",
            (target, target if ready else 0, ready, time.time()),
        )
        self._db.commit()
        return self._message_search_state()

    def _maintain_message_search_batch(self, batch_size: int) -> dict[str, Any]:
        state = self._message_search_state()
        if state["reset_required"]:
            return self._reset_message_search_indexes()
        if state["ready"]:
            return state
        cursor = int(state["cursor_id"])
        target = int(state["target_max_id"])
        rows = self._db.execute(
            "SELECT id,ts,text FROM history_messages WHERE id>? AND id<=? "
            "ORDER BY id LIMIT ?",
            (cursor, target, batch_size),
        ).fetchall()
        next_cursor = int(rows[-1]["id"]) if rows else target
        with self._db:
            if rows:
                records = [(int(row["id"]), str(row["text"])) for row in rows]
                self._db.executemany(
                    "INSERT INTO history_messages_fts(rowid,text) VALUES(?,?)", records
                )
                self._db.executemany(
                    "INSERT INTO history_messages_trigram(rowid,text) VALUES(?,?)", records
                )
                self._db.executemany(
                    "UPDATE history_messages SET ts_epoch=? WHERE id=?",
                    [(_message_timestamp(row["ts"]), int(row["id"])) for row in rows],
                )
            ready = int(next_cursor >= target)
            self._db.execute(
                "UPDATE history_message_search_maintenance SET cursor_id=?,ready=?,"
                "updated_at=?,last_error=NULL WHERE id=1",
                (next_cursor, ready, time.time()),
            )
        return self._message_search_state()

    def _create_message_time_index(self) -> None:
        self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_history_messages_time "
            "ON history_messages(history_id,ts_epoch)"
        )
        self._db.commit()

    async def maintain_message_search_indexes(self, *, batch_size: int = 250) -> None:
        """Repair and populate message-search derivatives after daemon startup."""
        bounded_batch = max(1, min(batch_size, 1000))
        started = time.monotonic()
        batches = 0
        try:
            state = await self._run(self._message_search_state)
            if state["ready"]:
                return
            log.warning(
                "history message search maintenance started target=%d cursor=%d reset=%s "
                "batch_size=%d",
                state["target_max_id"],
                state["cursor_id"],
                state["reset_required"],
                bounded_batch,
            )
            while not state["ready"]:
                state = await self._run(
                    lambda: self._maintain_message_search_batch(bounded_batch)
                )
                batches += 1
                if batches % 50 == 0 and not state["ready"]:
                    log.info(
                        "history message search maintenance progress cursor=%d target=%d "
                        "batches=%d",
                        state["cursor_id"],
                        state["target_max_id"],
                        batches,
                    )
                await asyncio.sleep(0)
            try:
                await self._run(self._create_message_time_index)
            except sqlite3.Error:
                log.exception("history message timestamp index creation failed")
            log.info(
                "history message search maintenance completed rows=%d batches=%d duration_s=%.1f",
                state["target_max_id"],
                batches,
                time.monotonic() - started,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.exception("history message search maintenance failed; bounded LIKE search remains")
            error_message = str(exc)

            def record_failure() -> None:
                try:
                    self._db.execute(
                        "UPDATE history_message_search_maintenance SET last_error=?,updated_at=? "
                        "WHERE id=1",
                        (error_message, time.time()),
                    )
                    self._db.commit()
                except sqlite3.Error:
                    self._db.rollback()

            await self._run(record_failure)

    async def list_projects(self) -> list[ProjectRecord]:
        def op() -> list[ProjectRecord]:
            rows = self._db.execute(
                "SELECT * FROM projects WHERE deleted_at IS NULL ORDER BY position,name"
            ).fetchall()
            return [
                ProjectRecord(
                    id=row["id"],
                    name=row["name"],
                    root=row["root"],
                    position=row["position"],
                    group_id=row["group_id"],
                    layout=json.loads(row["layout_json"]) if row["layout_json"] else None,
                    default_backend=row["default_backend"],
                    layout_revision=row["layout_revision"],
                    default_profile_id=row["default_profile_id"],
                    default_agent_profiles=_string_map(row["default_agent_profiles_json"]),
                    git_compare_ref=row["git_compare_ref"],
                    resource_open_mode=row["resource_open_mode"],
                    sidebar_visible=bool(row["sidebar_visible"]),
                    created_at=float(row["created_at"] or 0.0),
                    last_used_at=float(row["last_used_at"] or 0.0),
                )
                for row in rows
            ]

        return await self._run(op)

    async def removed_project_for_root(self, root: str) -> ProjectRecord | None:
        """Return the most recently removed Project registered at ``root``."""

        def op() -> ProjectRecord | None:
            row = self._db.execute(
                "SELECT * FROM projects WHERE deleted_at IS NOT NULL AND root=? COLLATE NOCASE "
                "ORDER BY deleted_at DESC LIMIT 1",
                (root,),
            ).fetchone()
            if row is None:
                return None
            return ProjectRecord(
                id=row["id"],
                name=row["name"],
                root=row["root"],
                position=row["position"],
                group_id=row["group_id"],
                layout=json.loads(row["layout_json"]) if row["layout_json"] else None,
                default_backend=row["default_backend"],
                layout_revision=row["layout_revision"],
                default_profile_id=row["default_profile_id"],
                default_agent_profiles=_string_map(row["default_agent_profiles_json"]),
                git_compare_ref=row["git_compare_ref"],
                resource_open_mode=row["resource_open_mode"],
                sidebar_visible=bool(row["sidebar_visible"]),
                created_at=float(row["created_at"] or 0.0),
                last_used_at=float(row["last_used_at"] or 0.0),
            )

        return await self._run(op)

    async def project_last_activity(self) -> dict[str, float]:
        """Most recent evidence of work per Project id, epoch seconds.

        Feeds the sidebar's "recently active" ordering, which has to rank Projects
        whose sessions are all long gone — live state alone would sort every idle
        Project into one indistinguishable block at the bottom. The inner scalar
        ``MAX`` picks the latest of the three stamps a row can carry: a still-running
        session has no ``exited_at``, and only agent backends write ``last_message_at``,
        so ``spawned_at`` is the floor that always exists.
        """

        def op() -> dict[str, float]:
            rows = self._db.execute(
                "SELECT project_id,MAX(MAX(COALESCE(last_message_at,0),"
                "COALESCE(exited_at,0),spawned_at)) AS last_activity FROM history "
                "WHERE project_id IS NOT NULL AND project_id!='' GROUP BY project_id"
            ).fetchall()
            return {str(row["project_id"]): float(row["last_activity"] or 0.0) for row in rows}

        return await self._run(op)

    async def upsert_project(self, project: ProjectRecord) -> None:
        def op() -> None:
            self._db.execute(
                "INSERT INTO projects(id,name,root,position,group_id,layout_json,default_backend,"
                "layout_revision,default_profile_id,resource_open_mode,sidebar_visible,"
                "created_at,last_used_at,git_compare_ref,default_agent_profiles_json,deleted_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,NULL) "
                "ON CONFLICT(id) DO UPDATE SET name=excluded.name,root=excluded.root,"
                "position=excluded.position,group_id=excluded.group_id,"
                "layout_json=excluded.layout_json,default_backend=excluded.default_backend,"
                "layout_revision=excluded.layout_revision,"
                "default_profile_id=excluded.default_profile_id,"
                "resource_open_mode=excluded.resource_open_mode,"
                "sidebar_visible=excluded.sidebar_visible,created_at=excluded.created_at,"
                "last_used_at=excluded.last_used_at,"
                "git_compare_ref=excluded.git_compare_ref,"
                "default_agent_profiles_json=excluded.default_agent_profiles_json,deleted_at=NULL",
                (
                    project.id,
                    project.name,
                    project.root,
                    project.position,
                    project.group_id,
                    json.dumps(project.layout),
                    project.default_backend,
                    project.layout_revision,
                    project.default_profile_id,
                    project.resource_open_mode,
                    int(project.sidebar_visible),
                    project.created_at,
                    project.last_used_at,
                    project.git_compare_ref,
                    json.dumps(project.default_agent_profiles)
                    if project.default_agent_profiles
                    else None,
                ),
            )
            self._db.commit()

        await self._run(op)

    async def set_project_last_used(self, project_id: str, used_at: float) -> None:
        """Advance one Project's explicit-use timestamp without touching other fields."""

        def op() -> None:
            cursor = self._db.execute(
                "UPDATE projects SET last_used_at=MAX(last_used_at,?) WHERE id=?",
                (used_at, project_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(project_id)
            self._db.commit()

        await self._run(op)

    async def reorder_projects(self, ordered_ids: list[str]) -> None:
        """Replace every Project position in one transaction.

        The manager validates optimistic ordering before this write. Keeping the
        replacement atomic prevents readers from observing duplicate or partial
        positions while multiple rows move.
        """

        def op() -> None:
            rows = self._db.execute(
                "SELECT id FROM projects WHERE deleted_at IS NULL"
            ).fetchall()
            current_ids = {str(row["id"]) for row in rows}
            if len(ordered_ids) != len(current_ids) or set(ordered_ids) != current_ids:
                raise ValueError("project order must contain every registered project once")
            with self._db:
                self._db.executemany(
                    "UPDATE projects SET position=? WHERE id=?",
                    [(position, project_id) for position, project_id in enumerate(ordered_ids)],
                )

        await self._run(op)

    async def remove_project(self, project_id: str, *, removed_at: float) -> None:
        """Remove an active registration while retaining its historical identity."""

        def op() -> None:
            with self._db:
                cursor = self._db.execute(
                    "UPDATE projects SET deleted_at=? WHERE id=? AND deleted_at IS NULL",
                    (removed_at, project_id),
                )
                if cursor.rowcount != 1:
                    raise KeyError(project_id)

        await self._run(op)

    async def project_session_ids(self, project_id: str) -> list[str]:
        def op() -> list[str]:
            return [
                row["id"]
                for row in self._db.execute(
                    "SELECT id FROM history WHERE project_id=?", (project_id,)
                ).fetchall()
            ]

        return await self._run(op)

    async def project_history_counts(self) -> dict[str, int]:
        """Visible conversation count per Project, including removed Projects."""

        def op() -> dict[str, int]:
            rows = self._db.execute(
                "SELECT project_id,COUNT(*) AS count FROM history WHERE project_id IS NOT NULL "
                f"AND agent_visible=1 AND backend IN ({_AGENT_BACKEND_SQL}) GROUP BY project_id",
                _AGENT_BACKEND_ARGS,
            ).fetchall()
            return {str(row["project_id"]): int(row["count"]) for row in rows}

        return await self._run(op)

    async def list_project_groups(self) -> list[ProjectGroupRecord]:
        def op() -> list[ProjectGroupRecord]:
            return [
                ProjectGroupRecord(row["id"], row["name"], row["position"])
                for row in self._db.execute(
                    "SELECT * FROM project_groups ORDER BY position,name"
                ).fetchall()
            ]

        return await self._run(op)

    async def upsert_project_group(self, group: ProjectGroupRecord) -> None:
        def op() -> None:
            self._db.execute(
                "INSERT INTO project_groups(id,name,position) VALUES(?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET name=excluded.name,position=excluded.position",
                (group.id, group.name, group.position),
            )
            self._db.commit()

        await self._run(op)

    async def delete_project_group(self, group_id: str) -> None:
        def op() -> None:
            self._db.execute("UPDATE projects SET group_id=NULL WHERE group_id=?", (group_id,))
            self._db.execute("DELETE FROM project_groups WHERE id=?", (group_id,))
            self._db.commit()

        await self._run(op)

    def _insert_session_row(
        self, session: SessionRecord, transcript: str | None, row_id: str
    ) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO history"
            "(id,native_id,backend,name,cwd,project_id,note_id,agent_run_seq,spawned_at,"
            "tokens_in,tokens_out,tokens_cache_read,tokens_cache_write,cost_usd,"
            "transcript_path,executable,argv_json,"
            "pinned_attention,shell_profile_id,agent_visible,repository_id,project_label,"
            "project_root,context_window,final_context_pct,peak_context_pct,model,"
            "measurement_source,project_scope_id,repo_group_id,auto_named,"
            "provider,provider_account_hashes_json) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                row_id,
                session.native_session_id,
                session.backend,
                session.name,
                session.cwd,
                session.project_id,
                session.id,
                session.agent_run_seq,
                session.created_at,
                session.tokens_in,
                session.tokens_out,
                session.tokens_cache_read,
                session.tokens_cache_write,
                session.cost_usd,
                transcript,
                session.exe,
                json.dumps(session.args),
                int(session.pinned_attention),
                session.shell_profile_id,
                int(is_agent_harness(session.backend)),
                session.repository_id,
                session.project_label,
                session.project_root,
                session.context_window or None,
                session.context_pct if session.context_window else None,
                session.context_peak_pct if session.context_window else None,
                session.model,
                session.measurement_source,
                session.project_scope_id or session.repository_id,
                session.repo_group_id,
                int(session.auto_named),
                session.provider,
                json.dumps(session.provider_account_hashes, sort_keys=True),
            ),
        )

    async def session_started(self, session: SessionRecord, transcript: str | None) -> None:
        def op() -> None:
            self._insert_session_row(session, transcript, session.id)
            self._db.commit()

        await self._run(op)

    async def resume_agent_run(self, session: SessionRecord, transcript: str | None) -> None:
        """Reopen the row of a conversation a freshly spawned pane resumed.

        Claude's ``--resume`` appends to the same transcript under the same
        conversation id, so the pane continues an agent run that already has a
        row. Opening a second one would index one file twice, show one
        conversation as two entries, and leave the first entry's totals moving
        after its own pane exited. What the conversation owns — its start, its
        note, its totals, its transcript watermark — is therefore preserved, and
        only what the new PTY genuinely changes is refreshed.
        """
        run_id = session.agent_run_id or session.id

        def op() -> None:
            row = self._db.execute(
                "SELECT agent_visible FROM history WHERE id=?", (run_id,)
            ).fetchone()
            if row is None:
                # The row was deleted between the resume request and this write.
                # Every later write for this pane keys on the run id, so open one
                # there rather than leaving the pane unable to record anything.
                self._insert_session_row(session, transcript, run_id)
            elif row["agent_visible"]:
                self._db.execute(
                    "UPDATE history SET exited_at=NULL,exit_reason=NULL,final_state=NULL,"
                    "name=?,cwd=?,project_id=?,executable=?,argv_json=?,pinned_attention=?,"
                    "shell_profile_id=?,auto_named=?,agent_run_seq=COALESCE(agent_run_seq,?),"
                    "transcript_path=COALESCE(?,transcript_path),repository_id=?,"
                    "project_label=?,project_root=?,project_scope_id=?,repo_group_id=? "
                    "WHERE id=?",
                    (
                        session.name,
                        session.run_cwd or session.cwd,
                        session.project_id,
                        session.exe,
                        json.dumps(session.args),
                        int(session.pinned_attention),
                        session.shell_profile_id,
                        int(session.auto_named),
                        session.agent_run_seq,
                        transcript,
                        session.repository_id,
                        session.project_label,
                        session.project_root,
                        session.project_scope_id or session.repository_id,
                        session.repo_group_id,
                        run_id,
                    ),
                )
            # A quarantined row is an audit record of misattribution. Resuming
            # does not resurrect it, and overwriting it would destroy the
            # evidence it exists to keep.
            self._db.commit()

        await self._run(op)

    async def update_session_metadata(self, session: SessionRecord) -> None:
        """Persist mutable metadata from a live session as one atomic update.

        Keyed to the *current* agent run's row, not the pane's first one: history
        is one row per conversation, and after a rollover the row keyed by the
        mux session id is a retired conversation. A rename made while looking at
        the current conversation must land on the current conversation — renaming
        the first one instead is how a custom title ended up resuming a
        conversation the user never named.
        """

        def op() -> None:
            self._db.execute(
                "UPDATE history SET name=?,cwd=?,project_id=?,executable=?,argv_json=?,"
                "pinned_attention=?,shell_profile_id=?,auto_named=? WHERE id=?",
                (
                    session.name,
                    session.cwd,
                    session.project_id,
                    session.exe,
                    json.dumps(session.args),
                    int(session.pinned_attention),
                    session.shell_profile_id,
                    int(session.auto_named),
                    session.agent_run_id or session.id,
                ),
            )
            self._db.commit()

        await self._run(op)

    async def session_ended(self, session: SessionRecord, reason: str) -> None:
        # Same keying as update_session_metadata: the exit closes the pane's
        # *current* conversation row. Earlier rows were already closed by
        # agent_run_ended when their conversations rolled.
        row_id = session.agent_run_id or session.id

        def op() -> None:
            row = self._db.execute(
                "SELECT agent_visible FROM history WHERE id=?", (row_id,)
            ).fetchone()
            if row and not row["agent_visible"]:
                self._db.execute("DELETE FROM history WHERE id=?", (row_id,))
                self._db.commit()
                return
            self._db.execute(
                "UPDATE history SET exited_at=?,exit_reason=?,tokens_in=?,tokens_out=?,"
                "tokens_cache_read=?,tokens_cache_write=?,cost_usd=?,"
                "name=?,cwd=?,project_id=?,executable=?,argv_json=?,pinned_attention=?,"
                "shell_profile_id=?,final_state=?,context_window=COALESCE(?,context_window),"
                "final_context_pct=COALESCE(?,final_context_pct),"
                "peak_context_pct=COALESCE(?,peak_context_pct),provider=COALESCE(?,provider),"
                "provider_account_hashes_json=?,model=COALESCE(?,model),"
                "measurement_source=COALESCE(?,measurement_source),auto_named=? WHERE id=?",
                (
                    session.last_activity_ts,
                    reason,
                    session.tokens_in,
                    session.tokens_out,
                    session.tokens_cache_read,
                    session.tokens_cache_write,
                    session.cost_usd,
                    session.name,
                    session.cwd,
                    session.project_id,
                    session.exe,
                    json.dumps(session.args),
                    int(session.pinned_attention),
                    session.shell_profile_id,
                    session.state,
                    session.context_window or None,
                    session.context_pct if session.context_window else None,
                    session.context_peak_pct if session.context_window else None,
                    session.provider,
                    json.dumps(session.provider_account_hashes, sort_keys=True),
                    session.model,
                    session.measurement_source,
                    int(session.auto_named),
                    row_id,
                ),
            )
            self._db.commit()

        await self._run(op)

    async def update_agent_summary(self, session: SessionRecord) -> None:
        if not (
            session.context_window
            or session.tokens_in
            or session.tokens_out
            or session.tokens_cache_read
            or session.tokens_cache_write
            or session.cost_usd
            or session.provider
            or session.provider_account_hashes
        ):
            return

        def op() -> None:
            self._db.execute(
                "UPDATE history SET tokens_in=?,tokens_out=?,tokens_cache_read=?,"
                "tokens_cache_write=?,cost_usd=?,context_window=?,"
                "final_context_pct=?,peak_context_pct=?,provider=?,"
                "provider_account_hashes_json=?,model=?,measurement_source=? "
                "WHERE id=? AND agent_visible=1",
                (
                    session.tokens_in,
                    session.tokens_out,
                    session.tokens_cache_read,
                    session.tokens_cache_write,
                    session.cost_usd,
                    session.context_window,
                    session.context_pct,
                    session.context_peak_pct,
                    session.provider,
                    json.dumps(session.provider_account_hashes, sort_keys=True),
                    session.model,
                    session.measurement_source,
                    session.agent_run_id or session.id,
                ),
            )
            self._db.commit()

        await self._run(op)

    async def session_promoted(self, session: SessionRecord, transcript: str) -> None:
        def op() -> None:
            self._db.execute(
                "DELETE FROM history WHERE external=1 AND backend=? AND native_id=?",
                (session.backend, session.native_session_id),
            )
            run_id = session.agent_run_id or session.id
            self._db.execute(
                "INSERT INTO history"
                "(id,native_id,backend,name,cwd,project_id,note_id,agent_run_seq,spawned_at,tokens_in,tokens_out,"
                "tokens_cache_read,tokens_cache_write,cost_usd,"
                "transcript_path,executable,argv_json,pinned_attention,shell_profile_id,"
                "agent_visible,repository_id,project_label,project_root,context_window,"
                "final_context_pct,peak_context_pct,model,measurement_source,"
                "project_scope_id,repo_group_id,auto_named,provider,"
                "provider_account_hashes_json) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET native_id=excluded.native_id,"
                "backend=excluded.backend,name=excluded.name,cwd=excluded.cwd,"
                "project_id=excluded.project_id,transcript_path=excluded.transcript_path,"
                "note_id=excluded.note_id,"
                "agent_visible=1,repository_id=excluded.repository_id,"
                "project_label=excluded.project_label,project_root=excluded.project_root,"
                "project_scope_id=excluded.project_scope_id,repo_group_id=excluded.repo_group_id,"
                "auto_named=excluded.auto_named,agent_run_seq=excluded.agent_run_seq,"
                "provider=excluded.provider,"
                "provider_account_hashes_json=excluded.provider_account_hashes_json",
                (
                    run_id,
                    session.native_session_id,
                    session.backend,
                    session.name,
                    session.run_cwd or session.cwd,
                    session.project_id,
                    session.id,
                    session.agent_run_seq,
                    session.agent_run_started_at or session.created_at,
                    session.tokens_in,
                    session.tokens_out,
                    session.tokens_cache_read,
                    session.tokens_cache_write,
                    session.cost_usd,
                    transcript,
                    session.exe,
                    json.dumps(session.args),
                    int(session.pinned_attention),
                    session.shell_profile_id,
                    1,
                    session.repository_id,
                    session.project_label,
                    session.project_root,
                    session.context_window or None,
                    session.context_pct if session.context_window else None,
                    session.context_peak_pct if session.context_window else None,
                    session.model,
                    session.measurement_source,
                    session.project_scope_id,
                    session.repo_group_id,
                    int(session.auto_named),
                    session.provider,
                    json.dumps(session.provider_account_hashes, sort_keys=True),
                ),
            )
            self._db.commit()

        await self._run(op)

    async def reopen_agent_run(self, run_id: str) -> None:
        """Clear a false terminal marker after live root identity is repaired."""

        def op() -> None:
            self._db.execute(
                "UPDATE history SET exited_at=NULL,exit_reason=NULL,final_state=NULL "
                "WHERE id=? AND agent_visible=1",
                (run_id,),
            )
            self._db.commit()

        await self._run(op)

    async def quarantine_misattributed_agent_run(self, run_id: str, reason: str) -> None:
        """Hide observer data proven to belong to another live root session.

        The row remains as an internal audit record, but its copied transcript
        messages and indexing cursor are removed so sibling content can no
        longer surface through history search or be incrementally re-indexed.
        """

        def op() -> None:
            with self._db:
                self._db.execute("DELETE FROM history_messages WHERE history_id=?", (run_id,))
                self._db.execute(
                    "DELETE FROM history_transcript_index WHERE history_id=?", (run_id,)
                )
                self._db.execute(
                    "UPDATE history SET agent_visible=0,exited_at=?,exit_reason=?,"
                    "final_state='crashed',transcript_path=NULL WHERE id=?",
                    (time.time(), reason, run_id),
                )

        await self._run(op)

    async def reset_run_transcript_copy(self, run_id: str) -> None:
        """Drop a run's copied messages and index cursor after its transcript rebound.

        Used when live identity reconciliation repairs a run row in place: the
        messages copied from the wrong (sibling's) transcript must not keep
        surfacing through history search, and the stale watermark would otherwise
        prevent the correct file from being indexed from the start.
        """

        def op() -> None:
            with self._db:
                self._db.execute("DELETE FROM history_messages WHERE history_id=?", (run_id,))
                self._db.execute(
                    "DELETE FROM history_transcript_index WHERE history_id=?", (run_id,)
                )

        await self._run(op)

    async def reconcile_historical_provider_collisions(
        self,
    ) -> list[tuple[str, str, str]]:
        """Hide legacy false runs after their owning live session is gone.

        Repair requires three independent proofs: the retained executable names
        a different provider, its note owner is the canonical row for that root
        provider, and another canonical row owns the claimed native id or
        transcript. Legitimate nested agents start from a shell executable and
        therefore cannot satisfy the first proof.
        """

        def arguments(row: sqlite3.Row) -> list[str]:
            try:
                value = json.loads(row["argv_json"] or "[]")
            except (TypeError, ValueError):
                return []
            return [str(item) for item in value] if isinstance(value, list) else []

        def op() -> list[tuple[str, str, str]]:
            rows = self._db.execute(
                "SELECT id,native_id,backend,note_id,transcript_path,executable,argv_json "
                f"FROM history WHERE agent_visible=1 AND backend IN ({_AGENT_BACKEND_SQL})",
                _AGENT_BACKEND_ARGS,
            ).fetchall()
            by_id = {str(row["id"]): row for row in rows}
            repairs: list[tuple[str, str, str]] = []
            now = time.time()
            with self._db:
                for row in rows:
                    run_id = str(row["id"])
                    note_id = str(row["note_id"] or "")
                    claimed_backend = str(row["backend"])
                    root_backend = infer_agent_executable_backend(
                        row["executable"], arguments(row)
                    )
                    if (
                        root_backend is None
                        or root_backend == claimed_backend
                        or not note_id
                        or run_id == note_id
                    ):
                        continue
                    root = by_id.get(note_id)
                    if root is None or str(root["backend"]) != root_backend:
                        continue
                    claimed_native = str(row["native_id"] or "")
                    claimed_transcript = str(row["transcript_path"] or "")
                    owner = next(
                        (
                            candidate
                            for candidate in rows
                            if str(candidate["id"]) != run_id
                            and str(candidate["id"]) == str(candidate["note_id"] or "")
                            and str(candidate["backend"]) == claimed_backend
                            and (
                                (
                                    bool(claimed_native)
                                    and str(candidate["native_id"] or "") == claimed_native
                                )
                                or (
                                    bool(claimed_transcript)
                                    and str(candidate["transcript_path"] or "")
                                    == claimed_transcript
                                )
                            )
                        ),
                        None,
                    )
                    if owner is None:
                        continue
                    self._db.execute(
                        "DELETE FROM history_messages WHERE history_id=?", (run_id,)
                    )
                    self._db.execute(
                        "DELETE FROM history_transcript_index WHERE history_id=?",
                        (run_id,),
                    )
                    self._db.execute(
                        "UPDATE history SET agent_visible=0,exited_at=?,"
                        "exit_reason='historical_provider_collision_reconciled',"
                        "final_state='crashed',transcript_path=NULL WHERE id=?",
                        (now, run_id),
                    )
                    repairs.append((note_id, run_id, str(root["id"])))
            return repairs

        return await self._run(op)

    async def agent_run_ended(self, session: SessionRecord, reason: str) -> None:
        run_id = session.agent_run_id
        if not run_id:
            return

        def op() -> None:
            self._db.execute(
                "UPDATE history SET exited_at=?,exit_reason=?,tokens_in=?,tokens_out=?,"
                "tokens_cache_read=?,tokens_cache_write=?,cost_usd=?,"
                "final_state=?,context_window=COALESCE(?,context_window),"
                "final_context_pct=COALESCE(?,final_context_pct),"
                "peak_context_pct=COALESCE(?,peak_context_pct),provider=COALESCE(?,provider),"
                "provider_account_hashes_json=?,model=COALESCE(?,model),"
                "measurement_source=COALESCE(?,measurement_source) WHERE id=? AND agent_visible=1",
                (
                    time.time(),
                    reason,
                    session.tokens_in,
                    session.tokens_out,
                    session.tokens_cache_read,
                    session.tokens_cache_write,
                    session.cost_usd,
                    "idle" if reason == "agent_exit" else session.state,
                    session.context_window or None,
                    session.context_pct if session.context_window else None,
                    session.context_peak_pct if session.context_window else None,
                    session.provider,
                    json.dumps(session.provider_account_hashes, sort_keys=True),
                    session.model,
                    session.measurement_source,
                    run_id,
                ),
            )
            self._db.commit()

        await self._run(op)

    async def upsert_external(
        self,
        *,
        row_id: str,
        native_id: str,
        backend: str,
        name: str,
        cwd: str,
        spawned_at: float,
        transcript_path: str,
        project_id: str | None = None,
        repository_id: str | None = None,
        project_label: str | None = None,
        project_root: str | None = None,
        project_scope_id: str | None = None,
        repo_group_id: str | None = None,
        context_window: int | None = None,
        final_context_pct: float | None = None,
        peak_context_pct: float | None = None,
        tokens_in: int = 0,
        tokens_out: int = 0,
        tokens_cache_read: int = 0,
        tokens_cache_write: int = 0,
        cost_usd: float = 0.0,
        provider: str | None = None,
        provider_account_hashes: dict[str, str] | None = None,
        model: str | None = None,
        measurement_source: str | None = None,
        mtime_ns: int | None = None,
        size: int | None = None,
    ) -> None:
        """Index a native CLI transcript without claiming ownership of the file."""

        def op() -> None:
            exists = self._db.execute(
                "SELECT 1 FROM history WHERE backend=? AND native_id=? AND external=0",
                (backend, native_id),
            ).fetchone()
            if not exists:
                self._db.execute(
                    "INSERT INTO history"
                    "(id,native_id,backend,name,cwd,project_id,note_id,spawned_at,transcript_path,external,"
                    "agent_visible,repository_id,project_label,project_root,context_window,"
                    "final_context_pct,peak_context_pct,tokens_in,tokens_out,"
                    "tokens_cache_read,tokens_cache_write,cost_usd,provider,"
                    "provider_account_hashes_json,model,"
                    "measurement_source,project_scope_id,repo_group_id,"
                    "transcript_mtime_ns,transcript_size) "
                    "VALUES(?,?,?,?,?,?,?,?,?,1,1,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(id) DO UPDATE SET "
                    "name=excluded.name,cwd=excluded.cwd,spawned_at=excluded.spawned_at,"
                    "project_id=COALESCE(excluded.project_id,history.project_id),"
                    "note_id=COALESCE(history.note_id,excluded.note_id),"
                    "transcript_path=excluded.transcript_path,repository_id=excluded.repository_id,"
                    # A row already assigned to a canonical Project keeps that
                    # Project's label/root. Startup reconcile re-derives them from
                    # Git and supplies no project_id, so without this guard every
                    # changed external row loses its backfill-assigned, most-
                    # specific-root attribution back to the enclosing worktree.
                    "project_label=CASE WHEN history.project_id IS NOT NULL "
                    "AND excluded.project_id IS NULL THEN history.project_label "
                    "ELSE excluded.project_label END,"
                    "project_root=CASE WHEN history.project_id IS NOT NULL "
                    "AND excluded.project_id IS NULL THEN history.project_root "
                    "ELSE excluded.project_root END,"
                    "context_window=excluded.context_window,"
                    "final_context_pct=excluded.final_context_pct,"
                    "peak_context_pct=excluded.peak_context_pct,tokens_in=excluded.tokens_in,"
                    "tokens_out=excluded.tokens_out,tokens_cache_read=excluded.tokens_cache_read,"
                    "tokens_cache_write=excluded.tokens_cache_write,cost_usd=excluded.cost_usd,"
                    "provider=excluded.provider,"
                    "provider_account_hashes_json=excluded.provider_account_hashes_json,"
                    "model=excluded.model,"
                    "measurement_source=excluded.measurement_source,"
                    "project_scope_id=excluded.project_scope_id,repo_group_id=excluded.repo_group_id,"
                    "transcript_mtime_ns=excluded.transcript_mtime_ns,"
                    "transcript_size=excluded.transcript_size",
                    (
                        row_id,
                        native_id,
                        backend,
                        name,
                        cwd,
                        project_id,
                        row_id,
                        spawned_at,
                        transcript_path,
                        repository_id,
                        project_label,
                        project_root,
                        context_window,
                        final_context_pct,
                        peak_context_pct,
                        tokens_in,
                        tokens_out,
                        tokens_cache_read,
                        tokens_cache_write,
                        cost_usd,
                        provider,
                        json.dumps(provider_account_hashes or {}, sort_keys=True),
                        model,
                        measurement_source,
                        project_scope_id or repository_id,
                        repo_group_id,
                        mtime_ns,
                        size,
                    ),
                )
                self._db.commit()

        await self._run(op)

    async def note_owner_labels(self, project_id: str) -> dict[str, dict[str, Any]]:
        """Map every note identity this Project has recorded to its owning run.

        Notes outlive their history rows (shell rows are pruned, agent rows can be
        deleted), so this only enriches a filesystem listing and never bounds it.
        """

        def op() -> dict[str, dict[str, Any]]:
            rows = self._db.execute(
                "SELECT note_id,name,backend,spawned_at,exited_at FROM history "
                "WHERE project_id=? AND note_id IS NOT NULL ORDER BY spawned_at ASC",
                (project_id,),
            ).fetchall()
            return {
                str(row["note_id"]): {
                    "name": row["name"],
                    "backend": row["backend"],
                    "spawned_at": row["spawned_at"],
                    "exited_at": row["exited_at"],
                }
                for row in rows
            }

        return await self._run(op)

    async def session_note_owned(self, project_id: str, note_id: str) -> bool:
        def op() -> bool:
            return bool(
                self._db.execute(
                    "SELECT 1 FROM history WHERE project_id=? AND note_id=? LIMIT 1",
                    (project_id, note_id),
                ).fetchone()
            )

        return await self._run(op)

    async def native_history_ids(self) -> dict[tuple[str, str], str]:
        """The row that owns each conversation's transcript, one per (backend, id).

        Ordered all the way down to the id, because this decides *which* row a
        reconcile indexes a file into. Ordered only by `external`, the winner among
        several internal rows for one conversation was whatever SQLite returned
        first, so a conversation with duplicate rows (the resume bug this ordering
        outlived) had its messages and timestamps land on an arbitrary one and hop
        between them across restarts — one entry showing the conversation and its
        twin showing nothing, with no rule about which. Earliest row wins: it is the
        conversation's own, the one a resume now inherits.
        """

        def op() -> dict[tuple[str, str], str]:
            rows = self._db.execute(
                "SELECT id,backend,native_id,external FROM history WHERE agent_visible=1 "
                "ORDER BY external ASC, spawned_at ASC, id ASC"
            ).fetchall()
            result: dict[tuple[str, str], str] = {}
            for row in rows:
                result.setdefault((str(row["backend"]), str(row["native_id"])), str(row["id"]))
            return result

        return await self._run(op)

    async def assign_native_project(
        self,
        backend: str,
        native_id: str,
        *,
        project_id: str,
        project_label: str,
        project_root: str,
    ) -> str | None:
        """Attach a *discovered* native run to one registered Project.

        A scan may only claim rows that have no canonical owner yet. `ORDER BY
        external ASC` prefers the mux-owned canonical row for a native id, so
        without the ownership guard a scan of Project A reassigns the history of a
        session that ran under nested Project B — the run's Project is decided at
        spawn and is not a scan's to change.
        """

        def op() -> str | None:
            row = self._db.execute(
                "SELECT id,project_id,external FROM history WHERE backend=? AND native_id=? "
                "ORDER BY external ASC LIMIT 1",
                (backend, native_id),
            ).fetchone()
            if not row:
                return None
            owner = str(row["project_id"] or "")
            if owner and owner != project_id and not int(row["external"] or 0):
                return None
            self._db.execute(
                "UPDATE history SET project_id=?,project_label=?,project_root=? WHERE id=?",
                (project_id, project_label, project_root, row["id"]),
            )
            self._db.commit()
            return str(row["id"])

        return await self._run(op)

    async def message_index_watermarks(self) -> dict[str, tuple[int, int, int]]:
        def op() -> dict[str, tuple[int, int, int]]:
            rows = self._db.execute(
                "SELECT history_id,source_mtime_ns AS mtime,source_size AS size,"
                "parser_version AS parser FROM history_transcript_index"
            ).fetchall()
            return {
                str(row["history_id"]): (int(row["mtime"]), int(row["size"]), int(row["parser"]))
                for row in rows
            }

        return await self._run(op)

    async def replace_history_messages(
        self,
        history_id: str,
        messages: list[dict[str, Any]],
        *,
        mtime_ns: int,
        size: int,
        parser_version: int = TRANSCRIPT_PARSER_VERSION,
    ) -> int:
        records = searchable_transcript_messages(messages)
        timestamped = [
            (stamp, record)
            for record in records
            if (stamp := _message_timestamp(record.get("ts"))) is not None
        ]
        first_message = (
            min(timestamped, key=lambda item: (item[0], item[1]["ordinal"]))
            if timestamped
            else None
        )
        last_message = (
            max(timestamped, key=lambda item: (item[0], item[1]["ordinal"]))
            if timestamped
            else None
        )
        native_started_at = first_message[0] if first_message is not None else None
        last_message_at = last_message[0] if last_message is not None else None
        last_message_role = (
            str(last_message[1]["role"]) if last_message is not None else None
        )

        def op() -> int:
            with self._db:
                self._db.execute("DELETE FROM history_messages WHERE history_id=?", (history_id,))
                self._db.executemany(
                    "INSERT INTO history_messages"
                    "(history_id,ordinal,role,ts,ts_epoch,text,source_mtime_ns,source_size,"
                    "parser_version) VALUES(?,?,?,?,?,?,?,?,?)",
                    [
                        (
                            history_id,
                            int(item["ordinal"]),
                            str(item["role"]),
                            None if item.get("ts") is None else str(item["ts"]),
                            _message_timestamp(item.get("ts")),
                            str(item["text"]),
                            mtime_ns,
                            size,
                            parser_version,
                        )
                        for item in records
                    ],
                )
                self._db.execute(
                    "INSERT INTO history_transcript_index"
                    "(history_id,source_mtime_ns,source_size,parser_version,"
                    "message_count,indexed_at) "
                    "VALUES(?,?,?,?,?,?) ON CONFLICT(history_id) DO UPDATE SET "
                    "source_mtime_ns=excluded.source_mtime_ns,source_size=excluded.source_size,"
                    "parser_version=excluded.parser_version,message_count=excluded.message_count,"
                    "indexed_at=excluded.indexed_at",
                    (history_id, mtime_ns, size, parser_version, len(records), time.time()),
                )
                self._db.execute(
                    "UPDATE history SET native_started_at=?,last_message_at=?,last_message_role=?,"
                    "time_summary_mtime_ns=?,time_summary_size=? WHERE id=?",
                    (
                        native_started_at,
                        last_message_at,
                        last_message_role,
                        mtime_ns,
                        size,
                        history_id,
                    ),
                )
            return len(records)

        return await self._run(op)

    async def refresh_time_summaries(self, items: list[dict[str, Any]]) -> int:
        """Refresh bounded first/last-message metadata for changed visible transcripts."""
        semaphore = asyncio.Semaphore(4)

        async def inspect(item: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]] | None:
            backend = str(item.get("backend") or "")
            if not has_observable_transcript(backend):
                return None
            transcript = item.get("transcript_path")
            path = Path(str(transcript)) if transcript else None
            native_id = str(item.get("native_id") or "") or None
            if not conversation_is_readable(path, backend, native_id):
                return None
            try:
                _identity, first, second = await asyncio.to_thread(
                    conversation_watermark, path, backend, native_id
                )
            except OSError:
                return None
            if (
                item.get("time_summary_mtime_ns") == first
                and item.get("time_summary_size") == second
            ):
                return None
            try:
                async with semaphore:
                    summary = await asyncio.to_thread(
                        transcript_time_summary, path, backend, native_id=native_id
                    )
            except OSError:
                return None
            return item, summary

        refreshed = [
            result
            for result in await asyncio.gather(*(inspect(item) for item in items))
            if result is not None
        ]
        if not refreshed:
            return 0

        def op() -> None:
            with self._db:
                self._db.executemany(
                    "UPDATE history SET native_started_at=?,last_message_at=?,"
                    "last_message_role=?,time_summary_mtime_ns=?,time_summary_size=? WHERE id=?",
                    [
                        (
                            _message_timestamp(summary["native_started_ts"]),
                            _message_timestamp(summary["last_message_ts"]),
                            summary["last_message_role"],
                            summary["mtime_ns"],
                            summary["size"],
                            item["id"],
                        )
                        for item, summary in refreshed
                    ],
                )

        await self._run(op)
        for item, summary in refreshed:
            item["native_started_at"] = _message_timestamp(summary["native_started_ts"])
            item["last_message_at"] = _message_timestamp(summary["last_message_ts"])
            item["last_message_role"] = summary["last_message_role"]
            item["time_summary_mtime_ns"] = summary["mtime_ns"]
            item["time_summary_size"] = summary["size"]
        return len(refreshed)

    async def index_transcript(
        self,
        history_id: str,
        transcript_path: str | Path | None,
        backend: str,
        *,
        force: bool = False,
        native_id: str | None = None,
    ) -> tuple[str, int]:
        """Index one native conversation without blocking the event loop.

        ``transcript_path`` is ``None`` for a harness that keeps conversations in a
        store; ``native_id`` names the conversation there. The stored watermark
        columns keep their names but hold whatever pair identifies that harness's
        conversation state, which for a store is its own updated-time and message
        count rather than a file stat (see ``transcript_view.conversation_watermark``).
        """
        path = Path(transcript_path) if transcript_path is not None else None
        _identity, first, second = await asyncio.to_thread(
            conversation_watermark, path, backend, native_id
        )
        watermark = (first, second, TRANSCRIPT_PARSER_VERSION)

        def current_watermark() -> tuple[int, int, int] | None:
            row = self._db.execute(
                "SELECT source_mtime_ns,source_size,parser_version "
                "FROM history_transcript_index WHERE history_id=?",
                (history_id,),
            ).fetchone()
            return (
                (int(row["source_mtime_ns"]), int(row["source_size"]), int(row["parser_version"]))
                if row
                else None
            )

        if not force and await self._run(current_watermark) == watermark:
            return "unchanged", 0
        messages = await asyncio.to_thread(parse_transcript, path, backend, native_id=native_id)
        count = await self.replace_history_messages(
            history_id,
            messages,
            mtime_ns=first,
            size=second,
        )
        return "indexed", count

    async def external_watermarks(self) -> dict[str, tuple[int, int]]:
        """Return ``{transcript_path: (mtime_ns, size)}`` for indexed external rows.

        Used by reconcile to skip re-reading native transcripts whose stat is
        unchanged. Only ``external=1`` rows with a recorded watermark are
        returned, so a live-session row can never cause a reconcile skip and a
        legacy row (NULL watermark) always re-parses once to backfill.
        """

        def op() -> dict[str, tuple[int, int]]:
            rows = self._db.execute(
                "SELECT transcript_path,transcript_mtime_ns,transcript_size FROM history "
                "WHERE external=1 AND transcript_path IS NOT NULL "
                "AND transcript_mtime_ns IS NOT NULL"
            ).fetchall()
            return {
                r["transcript_path"]: (r["transcript_mtime_ns"], r["transcript_size"]) for r in rows
            }

        return await self._run(op)

    async def append_event(self, event: MuxEvent) -> int:
        def op() -> int:
            cursor = self._db.execute(
                "INSERT INTO events(ts,session_id,source,type,payload_json) VALUES(?,?,?,?,?)",
                (event.ts, event.session_id, event.source, event.type, json.dumps(event.payload)),
            )
            sequence = int(cursor.lastrowid or 0)
            if sequence % 100 == 0:
                self._db.execute(
                    "DELETE FROM events WHERE ts<? OR seq<=?",
                    (event.ts - 90 * 86400, max(0, sequence - 100_000)),
                )
            self._db.commit()
            return sequence

        return await self._run(op)

    async def record_git_provenance(
        self,
        *,
        session_id: str,
        session_name: str,
        agent_run_id: str | None,
        project_id: str,
        worktree_root: str,
        commit_oid: str,
        parent_oids: tuple[str, ...] = (),
        subject: str = "",
        committed_at: float | None = None,
        previous_head: str | None = None,
        relationship: str,
        confidence: str,
        ambiguous: bool,
        source: str,
        source_event_seq: int | None = None,
        tool_call_id: str | None = None,
        evidence_rank: int,
        observed_at: float | None = None,
    ) -> dict[str, Any]:
        """Insert one durable association, promoting weaker evidence in place."""
        timestamp = observed_at if observed_at is not None else time.time()
        run_id = agent_run_id or ""
        row_id = uuid.uuid4().hex
        parents_json = json.dumps(parent_oids)

        def op() -> dict[str, Any]:
            self._db.execute(
                _GIT_PROVENANCE_UPSERT,
                (
                    row_id,
                    session_id,
                    session_name[:200],
                    run_id,
                    project_id,
                    worktree_root,
                    commit_oid.lower(),
                    parents_json,
                    subject[:512],
                    committed_at,
                    previous_head.lower() if previous_head else None,
                    relationship,
                    confidence,
                    int(ambiguous),
                    source,
                    source_event_seq,
                    tool_call_id,
                    evidence_rank,
                    timestamp,
                    timestamp,
                ),
            )
            self._db.commit()
            row = self._db.execute(
                "SELECT * FROM git_provenance WHERE session_id=? AND agent_run_id=? "
                "AND worktree_root=? AND commit_oid=?",
                (session_id, run_id, worktree_root, commit_oid.lower()),
            ).fetchone()
            assert row is not None
            return self._public_git_provenance(row)

        return await self._run(op)

    async def record_git_provenance_batch(
        self, records: list[dict[str, Any]]
    ) -> int:
        """Upsert an explicit provenance import in one bounded transaction."""
        if not records:
            return 0
        if len(records) > 1000:
            raise ValueError("git provenance batch exceeds 1000 rows")
        timestamp = time.time()

        def op() -> int:
            try:
                self._db.execute("BEGIN IMMEDIATE")
                for record in records:
                    observed_at = float(record.get("observed_at") or timestamp)
                    self._db.execute(
                        _GIT_PROVENANCE_UPSERT,
                        (
                            str(record.get("id") or uuid.uuid4().hex),
                            str(record["session_id"]),
                            str(record["session_name"])[:200],
                            str(record.get("agent_run_id") or ""),
                            str(record["project_id"]),
                            str(record["worktree_root"]),
                            str(record["commit_oid"]).lower(),
                            json.dumps(tuple(record.get("parent_oids") or ())),
                            str(record.get("subject") or "")[:512],
                            record.get("committed_at"),
                            (
                                str(record["previous_head"]).lower()
                                if record.get("previous_head")
                                else None
                            ),
                            str(record["relationship"]),
                            str(record["confidence"]),
                            int(bool(record.get("ambiguous"))),
                            str(record["source"]),
                            record.get("source_event_seq"),
                            record.get("tool_call_id"),
                            int(record["evidence_rank"]),
                            observed_at,
                            timestamp,
                        ),
                    )
                self._db.commit()
            except Exception:
                self._db.rollback()
                raise
            return len(records)

        return await self._run(op)

    @staticmethod
    def _public_git_provenance(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        try:
            parents = json.loads(item.pop("parent_oids_json", "[]") or "[]")
        except (TypeError, json.JSONDecodeError):
            parents = []
        item["parent_oids"] = [str(value) for value in parents] if isinstance(parents, list) else []
        item["agent_run_id"] = item.get("agent_run_id") or None
        item["ambiguous"] = bool(item.get("ambiguous"))
        item.pop("evidence_rank", None)
        return item

    async def git_provenance(
        self,
        *,
        project_id: str,
        session_id: str | None = None,
        agent_run_id: str | None = None,
        commit_oids: list[str] | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        clauses = ["project_id=?"]
        args: list[Any] = [project_id]
        if session_id:
            clauses.append("session_id=?")
            args.append(session_id)
        if agent_run_id:
            clauses.append("agent_run_id=?")
            args.append(agent_run_id)
        if commit_oids:
            normalized = list(dict.fromkeys(oid.lower() for oid in commit_oids))[:500]
            clauses.append(f"commit_oid IN ({','.join('?' for _ in normalized)})")
            args.extend(normalized)
        bounded_limit = max(1, min(limit, 500))

        def op() -> list[dict[str, Any]]:
            rows = self._db.execute(
                "SELECT * FROM git_provenance WHERE "
                + " AND ".join(clauses)
                + " ORDER BY observed_at DESC,id LIMIT ?",
                (*args, bounded_limit),
            ).fetchall()
            return [self._public_git_provenance(row) for row in rows]

        return await self._run(op)

    async def history(
        self, query: str = "", backend: str | None = None, limit: int = 200
    ) -> list[dict[str, Any]]:
        # Skip the leading-wildcard LIKE (which no index can serve and which runs
        # per row) for an empty query; the empty-query `%%` matched every row
        # anyway, so omitting the clause is behaviourally identical.
        sql = f"SELECT * FROM history WHERE agent_visible=1 AND backend IN ({_AGENT_BACKEND_SQL})"
        args: list[Any] = list(_AGENT_BACKEND_ARGS)
        if query:
            sql += " AND (name LIKE ? OR cwd LIKE ? OR COALESCE(project_label,'') LIKE ?)"
            args += [f"%{query}%", f"%{query}%", f"%{query}%"]
        if backend:
            sql += " AND backend=?"
            args.append(backend)
        sql += " ORDER BY spawned_at DESC LIMIT ?"
        args.append(limit)

        def op() -> list[dict[str, Any]]:
            rows = self._db.execute(sql, args).fetchall()
            return [_public_history_row(r) for r in rows]

        return await self._run(op)

    async def update_project_label(self, project_id: str, label: str) -> None:
        def op() -> None:
            self._db.execute(
                "UPDATE history SET project_label=? WHERE project_scope_id=?",
                (label, project_id),
            )
            self._db.commit()

        await self._run(op)

    async def search_history_index(
        self,
        *,
        query: str = "",
        search_scope: str = "all",
        include_metadata: bool = True,
        query_mode: str = "hybrid",
        project_id: str | None = None,
        backends: tuple[str, ...] = (),
        states: tuple[str, ...] = (),
        title_query: str = "",
        generated_query_run_ids: tuple[str, ...] = (),
        generated_title_run_ids: tuple[str, ...] = (),
        run_ids: tuple[str, ...] | None = None,
        session_after: float | None = None,
        session_before: float | None = None,
        message_after: float | None = None,
        message_before: float | None = None,
        order: str = "relevance",
        offset: int = 0,
        limit: int = 8,
        max_per_session: int = 2,
    ) -> dict[str, Any]:
        """Search indexed messages as compact, globally ranked retrieval hits.

        Native transcripts remain authoritative. This method reads only the
        rebuildable index so filtering, ranking, and excerpt extraction happen
        before any transcript text crosses the MCP boundary.
        """
        if search_scope not in {"all", "user", "assistant", "metadata"}:
            raise ValueError("history search scope must be all, user, assistant, or metadata")
        if query_mode not in {"hybrid", "all_terms", "any_terms", "phrase", "substring"}:
            raise ValueError(
                "history query mode must be hybrid, all_terms, any_terms, phrase, or substring"
            )
        if order not in {"relevance", "recent"}:
            raise ValueError("history search order must be relevance or recent")
        offset = max(0, offset)
        limit = max(1, min(limit, 50))
        max_per_session = max(1, min(max_per_session, 5))
        requested = offset + limit + 1
        candidate_limit = min(2000, max(80, requested * 10))

        def history_filters(alias: str = "h") -> tuple[list[str], list[Any]]:
            clauses = [
                f"{alias}.agent_visible=1",
                f"{alias}.backend IN ({_AGENT_BACKEND_SQL})",
            ]
            args: list[Any] = list(_AGENT_BACKEND_ARGS)
            if project_id == "__ungrouped__":
                clauses.append(f"{alias}.project_id IS NULL")
            elif project_id:
                clauses.append(f"{alias}.project_id=?")
                args.append(project_id)
            if backends:
                clauses.append(f"{alias}.backend IN ({','.join('?' for _ in backends)})")
                args.extend(backends)
            if states:
                ordinary = tuple(state for state in states if state != "running")
                state_parts: list[str] = []
                if ordinary:
                    state_parts.append(
                        f"{alias}.final_state IN ({','.join('?' for _ in ordinary)})"
                    )
                    args.extend(ordinary)
                if "running" in states:
                    state_parts.append(
                        f"({alias}.final_state IS NULL OR {alias}.final_state='' "
                        f"OR {alias}.final_state='running')"
                    )
                clauses.append(f"({' OR '.join(state_parts)})")
            if run_ids is not None:
                if not run_ids:
                    clauses.append("0")
                else:
                    clauses.append(f"{alias}.id IN ({','.join('?' for _ in run_ids)})")
                    args.extend(run_ids)
            session_time = f"COALESCE({alias}.native_started_at,{alias}.spawned_at)"
            if session_after is not None:
                clauses.append(f"{session_time}>=?")
                args.append(session_after)
            if session_before is not None:
                clauses.append(f"{session_time}<?")
                args.append(session_before)
            if title_query:
                title_parts = [f"{alias}.name LIKE ? ESCAPE '\\'"]
                title_args: list[Any] = [_like_pattern(title_query)]
                if generated_title_run_ids:
                    title_parts.append(
                        f"({alias}.auto_named=1 AND {alias}.id IN "
                        f"({','.join('?' for _ in generated_title_run_ids)}))"
                    )
                    title_args.extend(generated_title_run_ids)
                clauses.append(f"({' OR '.join(title_parts)})")
                args.extend(title_args)
            return clauses, args

        def message_filters() -> tuple[list[str], list[Any]]:
            clauses, args = history_filters()
            if search_scope in {"user", "assistant"}:
                clauses.append("hm.role=?")
                args.append(search_scope)
            else:
                clauses.append("hm.role IN ('user','assistant')")
            message_time = "COALESCE(hm.ts_epoch,mux_message_timestamp(hm.ts))"
            if message_after is not None:
                clauses.append(f"{message_time}>=?")
                args.append(message_after)
            if message_before is not None:
                clauses.append(f"{message_time}<?")
                args.append(message_before)
            return clauses, args

        def message_rows(table: str, fts_query: str) -> list[sqlite3.Row]:
            clauses, args = message_filters()
            sql = (
                "SELECT h.*,hm.ordinal AS match_ordinal,hm.role AS match_role,"
                "hm.ts AS match_ts,COALESCE(hm.ts_epoch,mux_message_timestamp(hm.ts)) "
                "AS match_ts_epoch,hm.text AS match_text,"
                "hm.source_mtime_ns AS match_mtime_ns,hm.source_size AS match_size,"
                "hm.parser_version AS match_parser_version,"
                f"bm25({table}) AS search_rank FROM {table} "
                f"JOIN history_messages hm ON hm.id={table}.rowid "
                "JOIN history h ON h.id=hm.history_id "
                f"WHERE {table} MATCH ? AND {' AND '.join(clauses)} "
                f"ORDER BY bm25({table}),hm.history_id,hm.ordinal LIMIT ?"
            )
            return self._db.execute(sql, [fts_query, *args, candidate_limit]).fetchall()

        def recent_message_rows() -> list[sqlite3.Row]:
            clauses, args = message_filters()
            sql = (
                "SELECT h.*,hm.ordinal AS match_ordinal,hm.role AS match_role,"
                "hm.ts AS match_ts,COALESCE(hm.ts_epoch,mux_message_timestamp(hm.ts)) "
                "AS match_ts_epoch,hm.text AS match_text,"
                "hm.source_mtime_ns AS match_mtime_ns,hm.source_size AS match_size,"
                "hm.parser_version AS match_parser_version,0.0 AS search_rank "
                "FROM history_messages hm JOIN history h ON h.id=hm.history_id "
                f"WHERE {' AND '.join(clauses)} ORDER BY "
                "COALESCE(hm.ts_epoch,mux_message_timestamp(hm.ts),0) DESC,"
                "hm.history_id DESC,hm.ordinal DESC LIMIT ?"
            )
            return self._db.execute(sql, [*args, candidate_limit]).fetchall()

        def literal_substring_rows() -> list[sqlite3.Row]:
            clauses, args = message_filters()
            sql = (
                "SELECT h.*,hm.ordinal AS match_ordinal,hm.role AS match_role,"
                "hm.ts AS match_ts,COALESCE(hm.ts_epoch,mux_message_timestamp(hm.ts)) "
                "AS match_ts_epoch,hm.text AS match_text,"
                "hm.source_mtime_ns AS match_mtime_ns,hm.source_size AS match_size,"
                "hm.parser_version AS match_parser_version,0.0 AS search_rank "
                "FROM history_messages hm JOIN history h ON h.id=hm.history_id "
                f"WHERE hm.text LIKE ? ESCAPE '\\' AND {' AND '.join(clauses)} "
                "ORDER BY hm.history_id,hm.ordinal LIMIT ?"
            )
            return self._db.execute(
                sql, [_like_pattern(query), *args, candidate_limit]
            ).fetchall()

        def fallback_message_rows() -> list[sqlite3.Row]:
            clauses, args = message_filters()
            fallback_mode = "all_terms" if query_mode == "hybrid" else query_mode
            predicate, query_args = _like_query_predicate(query, fallback_mode)
            sql = (
                "SELECT h.*,hm.ordinal AS match_ordinal,hm.role AS match_role,"
                "hm.ts AS match_ts,COALESCE(hm.ts_epoch,mux_message_timestamp(hm.ts)) "
                "AS match_ts_epoch,hm.text AS match_text,"
                "hm.source_mtime_ns AS match_mtime_ns,hm.source_size AS match_size,"
                "hm.parser_version AS match_parser_version,0.0 AS search_rank "
                "FROM history_messages hm JOIN history h ON h.id=hm.history_id "
                f"WHERE {predicate} AND {' AND '.join(clauses)} "
                "ORDER BY hm.history_id,hm.ordinal LIMIT ?"
            )
            return self._db.execute(
                sql, [*query_args, *args, candidate_limit]
            ).fetchall()

        def metadata_rows() -> list[sqlite3.Row]:
            clauses, args = history_filters()
            if query:
                pattern = _like_pattern(query)
                metadata_parts = [
                    "h.name LIKE ? ESCAPE '\\'",
                    "h.cwd LIKE ? ESCAPE '\\'",
                    "COALESCE(h.project_label,'') LIKE ? ESCAPE '\\'",
                ]
                metadata_args: list[Any] = [pattern, pattern, pattern]
                if generated_query_run_ids:
                    metadata_parts.append(
                        "(h.auto_named=1 AND h.id IN "
                        f"({','.join('?' for _ in generated_query_run_ids)}))"
                    )
                    metadata_args.extend(generated_query_run_ids)
                clauses.append(f"({' OR '.join(metadata_parts)})")
                args.extend(metadata_args)
            sql = (
                "SELECT h.* FROM history h WHERE "
                f"{' AND '.join(clauses)} ORDER BY h.spawned_at DESC,h.id DESC LIMIT ?"
            )
            return self._db.execute(sql, [*args, candidate_limit]).fetchall()

        def op() -> dict[str, Any]:
            ranked: dict[tuple[str, int | None, str], dict[str, Any]] = {}
            candidate_truncated = False
            search_state = self._message_search_state()

            if query and search_scope != "metadata":
                if not search_state["ready"]:
                    rows = fallback_message_rows()
                    candidate_truncated = candidate_truncated or len(rows) == candidate_limit
                    for position, row in enumerate(rows, 1):
                        item = _public_history_row(row)
                        key = (str(item["id"]), int(item["match_ordinal"]), "message")
                        item["relevance"] = 1.0 / (60 + position)
                        ranked[key] = item
                else:
                    ordinary_mode = query_mode if query_mode != "hybrid" else "all_terms"
                    if ordinary_mode != "substring":
                        ordinary = _fts_query(query, ordinary_mode)
                        rows = message_rows("history_messages_fts", ordinary) if ordinary else []
                        candidate_truncated = candidate_truncated or len(rows) == candidate_limit
                        for position, row in enumerate(rows, 1):
                            item = _public_history_row(row)
                            key = (str(item["id"]), int(item["match_ordinal"]), "message")
                            item["relevance"] = 1.0 / (60 + position)
                            ranked[key] = item
                    trigram = _trigram_query(query)
                    if query_mode in {"hybrid", "substring"} and trigram:
                        rows = message_rows("history_messages_trigram", trigram)
                        candidate_truncated = candidate_truncated or len(rows) == candidate_limit
                        for position, row in enumerate(rows, 1):
                            item = _public_history_row(row)
                            key = (str(item["id"]), int(item["match_ordinal"]), "message")
                            if key in ranked:
                                ranked[key]["relevance"] += 0.85 / (60 + position)
                            else:
                                item["relevance"] = 0.85 / (60 + position)
                                ranked[key] = item
                    elif query_mode == "substring":
                        # FTS5's trigram tokenizer cannot match one- or two-character
                        # literals. Keep explicit substring mode complete with a
                        # bounded LIKE fallback for those rare short queries.
                        rows = literal_substring_rows()
                        candidate_truncated = candidate_truncated or len(rows) == candidate_limit
                        for position, row in enumerate(rows, 1):
                            item = _public_history_row(row)
                            key = (str(item["id"]), int(item["match_ordinal"]), "message")
                            item["relevance"] = 0.85 / (60 + position)
                            ranked[key] = item

            filter_requests_messages = (
                not include_metadata
                or search_scope in {"user", "assistant"}
                or message_after is not None
                or message_before is not None
            )
            if not query and filter_requests_messages:
                rows = recent_message_rows()
                candidate_truncated = candidate_truncated or len(rows) == candidate_limit
                for row in rows:
                    item = _public_history_row(row)
                    key = (str(item["id"]), int(item["match_ordinal"]), "message")
                    item["relevance"] = 0.0
                    ranked[key] = item

            search_metadata = include_metadata and search_scope in {"all", "metadata"} and (
                (bool(query) and message_after is None and message_before is None)
                or search_scope == "metadata"
            )
            include_filter_only = not query and not filter_requests_messages
            if search_metadata or include_filter_only:
                rows = metadata_rows()
                candidate_truncated = candidate_truncated or len(rows) == candidate_limit
                folded = query.casefold()
                generated_ids = set(generated_query_run_ids)
                for row in rows:
                    item = _public_history_row(row)
                    run_id = str(item["id"])
                    metadata_key = (run_id, None, "metadata" if query else "session")
                    title_match = folded and (
                        folded in str(item.get("name") or "").casefold()
                        or run_id in generated_ids
                    )
                    item.update(
                        {
                            "match_ordinal": None,
                            "match_role": "metadata" if query else None,
                            "match_ts": None,
                            "match_ts_epoch": None,
                            "match_text": "",
                            "match_mtime_ns": None,
                            "match_size": None,
                            "match_parser_version": None,
                            "relevance": (0.05 if title_match else 0.02)
                            if query
                            else 0.0,
                        }
                    )
                    ranked[metadata_key] = item

            values = list(ranked.values())
            if order == "recent" or not query:
                values.sort(
                    key=lambda item: (
                        float(
                            item.get("match_ts_epoch")
                            or item.get("last_message_at")
                            or item.get("spawned_at")
                            or 0
                        ),
                        str(item.get("id") or ""),
                        int(item.get("match_ordinal") or -1),
                    ),
                    reverse=True,
                )
            else:
                values.sort(
                    key=lambda item: (
                        float(item.get("relevance") or 0),
                        float(item.get("match_ts_epoch") or item.get("spawned_at") or 0),
                        str(item.get("id") or ""),
                    ),
                    reverse=True,
                )

            per_session: dict[str, int] = {}
            diverse: list[dict[str, Any]] = []
            for item in values:
                run_id = str(item.get("id") or "")
                count = per_session.get(run_id, 0)
                if count >= max_per_session:
                    continue
                per_session[run_id] = count + 1
                text = str(item.pop("match_text", "") or "")
                item["excerpt"] = _search_excerpt(text, query) if text else ""
                item["match_kind"] = (
                    "message" if item.get("match_ordinal") is not None else "metadata"
                )
                item.pop("search_rank", None)
                diverse.append(item)

            page = diverse[offset : offset + limit]
            has_more = candidate_truncated or len(diverse) > offset + len(page)
            return {
                "items": page,
                "has_more": has_more,
                "candidate_truncated": candidate_truncated,
                "search_index_ready": bool(search_state["ready"]),
            }

        return await self._run(op)

    async def history_message_window(
        self,
        history_id: str,
        ordinal: int,
        *,
        watermark: tuple[int, int, int],
        before: int,
        after: int,
    ) -> dict[str, Any]:
        """Read a small indexed conversation window around one search hit."""

        def op() -> dict[str, Any]:
            current = self._db.execute(
                "SELECT source_mtime_ns,source_size,parser_version "
                "FROM history_transcript_index WHERE history_id=?",
                (history_id,),
            ).fetchone()
            if current is None:
                return {"stale": True, "messages": []}
            actual = (
                int(current["source_mtime_ns"]),
                int(current["source_size"]),
                int(current["parser_version"]),
            )
            if actual != watermark:
                return {"stale": True, "messages": [], "watermark": actual}
            anchor = self._db.execute(
                "SELECT ordinal,role,ts,text FROM history_messages "
                "WHERE history_id=? AND ordinal=?",
                (history_id, ordinal),
            ).fetchone()
            if anchor is None:
                return {"stale": True, "messages": [], "watermark": actual}
            earlier = self._db.execute(
                "SELECT ordinal,role,ts,text FROM history_messages "
                "WHERE history_id=? AND ordinal<? ORDER BY ordinal DESC LIMIT ?",
                (history_id, ordinal, before),
            ).fetchall()
            later = self._db.execute(
                "SELECT ordinal,role,ts,text FROM history_messages "
                "WHERE history_id=? AND ordinal>? ORDER BY ordinal LIMIT ?",
                (history_id, ordinal, after),
            ).fetchall()
            rows = [*reversed(earlier), anchor, *later]
            return {
                "stale": False,
                "watermark": actual,
                "messages": [dict(row) for row in rows],
            }

        return await self._run(op)

    async def history_page(
        self,
        *,
        query: str = "",
        search_scope: str = "all",
        backend: str | None = None,
        state: str | None = None,
        project_id: str | None = None,
        external: bool | None = None,
        date_from: float | None = None,
        date_to: float | None = None,
        time_basis: str = "started",
        cursor: str | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        limit = max(1, min(limit, 200))
        if search_scope not in {"all", "user", "assistant", "metadata"}:
            raise ValueError("history search scope must be all, user, assistant, or metadata")
        if time_basis not in {"started", "last_message"}:
            raise ValueError("history time basis must be started or last_message")
        search_index_ready = bool((await self.message_search_status())["ready"])
        sql = (
            "SELECT h.* FROM history h WHERE h.agent_visible=1 "
            f"AND h.backend IN ({_AGENT_BACKEND_SQL})"
        )
        args: list[Any] = list(_AGENT_BACKEND_ARGS)
        match_query = _fts_query(query)
        if query:
            metadata_sql = "(h.name LIKE ? OR h.cwd LIKE ? OR COALESCE(h.project_label,'') LIKE ?)"
            metadata_args = [f"%{query}%", f"%{query}%", f"%{query}%"]
            if search_index_ready:
                message_sql = (
                    "EXISTS (SELECT 1 FROM history_messages hm "
                    "JOIN history_messages_fts ON history_messages_fts.rowid=hm.id "
                    "WHERE hm.history_id=h.id AND history_messages_fts MATCH ?"
                )
                message_args: list[Any] = [match_query]
            else:
                predicate, message_args = _like_query_predicate(query, "all_terms")
                message_sql = (
                    "EXISTS (SELECT 1 FROM history_messages hm WHERE hm.history_id=h.id AND "
                    f"{predicate}"
                )
            if search_scope in {"user", "assistant"}:
                message_sql += " AND hm.role=?"
                message_args.append(search_scope)
            message_sql += ")"
            if search_scope == "metadata" or not match_query:
                sql += f" AND {metadata_sql}"
                args.extend(metadata_args)
            elif search_scope == "all":
                sql += f" AND ({metadata_sql} OR {message_sql})"
                args.extend(metadata_args + message_args)
            else:
                sql += f" AND {message_sql}"
                args.extend(message_args)
        filters = {"backend": backend, "final_state": state}
        for column, value in filters.items():
            if value:
                sql += f" AND h.{column}=?"
                args.append(value)
        if project_id == "__ungrouped__":
            sql += " AND h.project_id IS NULL"
        elif project_id:
            sql += " AND h.project_id=?"
            args.append(project_id)
        if external is not None:
            sql += " AND h.external=?"
            args.append(int(external))
        time_column = (
            "CASE WHEN h.external=1 THEN h.native_started_at "
            "ELSE COALESCE(h.native_started_at,h.spawned_at) END"
            if time_basis == "started"
            else "h.last_message_at"
        )
        if date_from is not None:
            sql += f" AND {time_column}>=?"
            args.append(date_from)
        if date_to is not None:
            sql += f" AND {time_column}<=?"
            args.append(date_to)
        if cursor:
            stamp, row_id = cursor.split(":", 1)
            sql += " AND (h.spawned_at<? OR (h.spawned_at=? AND h.id<?))"
            args.extend([float(stamp), float(stamp), row_id])
        sql += " ORDER BY h.spawned_at DESC,h.id DESC LIMIT ?"
        args.append(limit + 1)

        def op() -> dict[str, Any]:
            rows = self._db.execute(sql, args).fetchall()
            has_more = len(rows) > limit
            page = rows[:limit]
            items = [_public_history_row(row) for row in page]
            if query and match_query:
                role = search_scope if search_scope in {"user", "assistant"} else None
                for item in items:
                    match_args: list[Any]
                    role_sql = ""
                    if role:
                        role_sql = " AND hm.role=?"
                    if search_index_ready:
                        match_args = [match_query, item["id"]]
                        if role:
                            match_args.append(role)
                        matches = self._db.execute(
                            "SELECT hm.ordinal,hm.role,hm.ts,"
                            "snippet(history_messages_fts,0,'','',' … ',24) AS excerpt "
                            "FROM history_messages hm JOIN history_messages_fts "
                            "ON history_messages_fts.rowid=hm.id "
                            "WHERE history_messages_fts MATCH ? AND hm.history_id=?"
                            f"{role_sql} ORDER BY bm25(history_messages_fts),hm.ordinal LIMIT 4",
                            match_args,
                        ).fetchall()
                        public_matches = [dict(match) for match in matches[:3]]
                    else:
                        predicate, query_args = _like_query_predicate(query, "all_terms")
                        match_args = [*query_args, item["id"]]
                        if role:
                            match_args.append(role)
                        matches = self._db.execute(
                            "SELECT hm.ordinal,hm.role,hm.ts,hm.text FROM history_messages hm "
                            f"WHERE {predicate} AND hm.history_id=?{role_sql} "
                            "ORDER BY hm.ordinal LIMIT 4",
                            match_args,
                        ).fetchall()
                        public_matches = [
                            {
                                "ordinal": match["ordinal"],
                                "role": match["role"],
                                "ts": match["ts"],
                                "excerpt": _search_excerpt(str(match["text"]), query),
                            }
                            for match in matches[:3]
                        ]
                    item["matches"] = public_matches
                    item["match_count"] = len(matches) if len(matches) < 4 else 4
            next_cursor = (
                f"{page[-1]['spawned_at']}:{page[-1]['id']}" if has_more and page else None
            )
            return {"items": items, "next_cursor": next_cursor}

        return await self._run(op)

    async def history_message_matches(
        self, history_id: str, query: str, search_scope: str = "all", limit: int = 500
    ) -> list[dict[str, Any]]:
        match_query = _fts_query(query)
        if not match_query or search_scope == "metadata":
            return []
        if search_scope not in {"all", "user", "assistant"}:
            raise ValueError("history search scope must be all, user, assistant, or metadata")
        search_index_ready = bool((await self.message_search_status())["ready"])

        def op() -> list[dict[str, Any]]:
            role_sql = ""
            if search_scope in {"user", "assistant"}:
                role_sql = " AND hm.role=?"
            bounded_limit = max(1, min(limit, 2000))
            if search_index_ready:
                args: list[Any] = [match_query, history_id]
                if search_scope in {"user", "assistant"}:
                    args.append(search_scope)
                args.append(bounded_limit)
                rows = self._db.execute(
                    "SELECT hm.ordinal,hm.role,hm.ts,"
                    "snippet(history_messages_fts,0,'','',' … ',24) AS excerpt "
                    "FROM history_messages hm JOIN history_messages_fts "
                    "ON history_messages_fts.rowid=hm.id "
                    "WHERE history_messages_fts MATCH ? AND hm.history_id=?"
                    f"{role_sql} ORDER BY hm.ordinal LIMIT ?",
                    args,
                ).fetchall()
                return [dict(row) for row in rows]
            predicate, query_args = _like_query_predicate(query, "all_terms")
            fallback_args: list[Any] = [*query_args, history_id]
            if search_scope in {"user", "assistant"}:
                fallback_args.append(search_scope)
            fallback_args.append(bounded_limit)
            rows = self._db.execute(
                "SELECT hm.ordinal,hm.role,hm.ts,hm.text FROM history_messages hm "
                f"WHERE {predicate} AND hm.history_id=?{role_sql} "
                "ORDER BY hm.ordinal LIMIT ?",
                fallback_args,
            ).fetchall()
            return [
                {
                    "ordinal": row["ordinal"],
                    "role": row["role"],
                    "ts": row["ts"],
                    "excerpt": _search_excerpt(str(row["text"]), query),
                }
                for row in rows
            ]

        return await self._run(op)

    async def history_projects(self) -> list[dict[str, Any]]:
        def op() -> list[dict[str, Any]]:
            rows = self._db.execute(
                "SELECT h.project_id,COALESCE(MAX(p.name),'Unassigned') AS label,"
                "MAX(p.root) AS root,MAX(p.deleted_at) AS removed_at,"
                "COUNT(*) AS sessions,MAX(h.spawned_at) AS last_activity "
                "FROM history h LEFT JOIN projects p ON p.id=h.project_id "
                f"WHERE h.agent_visible=1 AND h.backend IN ({_AGENT_BACKEND_SQL}) "
                "GROUP BY h.project_id ORDER BY last_activity DESC",
                _AGENT_BACKEND_ARGS,
            ).fetchall()
            return [dict(row) for row in rows]

        return await self._run(op)

    async def workload_telemetry(self, since: float = 0) -> dict[str, Any]:
        """Observational run aggregates; these are not causal benchmark claims."""

        def op() -> dict[str, Any]:
            rows = self._db.execute(
                "WITH event_metrics AS (SELECT session_id,"
                "SUM(CASE WHEN type='turn_ended' THEN 1 ELSE 0 END) turns,"
                "SUM(CASE WHEN type='stalled' THEN 1 ELSE 0 END) stalls,"
                "SUM(CASE WHEN type='approval_needed' THEN 1 ELSE 0 END) approvals,"
                "SUM(CASE WHEN type='tool_result' AND json_extract(payload_json,'$.success')=1 "
                "AND (lower(COALESCE(json_extract(payload_json,'$.tool'),'')) LIKE '%test%' "
                "OR lower(COALESCE(json_extract(payload_json,'$.tool'),'')) LIKE '%pytest%' "
                "OR lower(COALESCE(json_extract(payload_json,'$.tool'),'')) LIKE '%vitest%' "
                "OR lower(COALESCE(json_extract(payload_json,'$.tool'),'')) LIKE '%check%') "
                "THEN 1 ELSE 0 END) completion_evidence FROM events WHERE ts>=? "
                "GROUP BY session_id) "
                "SELECT h.backend,COALESCE(h.model,'unknown') model,COUNT(*) runs,"
                "SUM(CASE WHEN exited_at IS NOT NULL THEN 1 ELSE 0 END) ended_runs,"
                "AVG(CASE WHEN exited_at IS NOT NULL THEN exited_at-spawned_at END) "
                "average_duration_s,SUM(tokens_in) tokens_in,SUM(tokens_out) tokens_out,"
                "AVG(final_context_pct) average_final_context_pct,"
                "AVG(peak_context_pct) average_peak_context_pct,"
                "SUM(COALESCE(event_metrics.turns,0)) turn_count,"
                "SUM(COALESCE(event_metrics.stalls,0)) stall_count,"
                "SUM(COALESCE(event_metrics.approvals,0)) approval_count,"
                "SUM(COALESCE(event_metrics.completion_evidence,0)) completion_evidence_count,"
                "SUM(CASE WHEN COALESCE(event_metrics.completion_evidence,0)>0 THEN 1 ELSE 0 END) "
                "completion_evidence_runs FROM history h LEFT JOIN event_metrics "
                "ON event_metrics.session_id=h.id WHERE h.agent_visible=1 AND h.spawned_at>=? "
                "GROUP BY h.backend,COALESCE(h.model,'unknown') ORDER BY runs DESC",
                (since, since),
            ).fetchall()
            events = self._db.execute(
                "SELECT type,COUNT(*) count FROM events WHERE ts>=? AND type IN "
                "('turn_started','turn_ended','approval_needed','stalled','runaway',"
                "'claim_unverified','context_pressure') GROUP BY type",
                (since,),
            ).fetchall()
            dimensions: list[dict[str, Any]] = []
            for row in rows:
                item = dict(row)
                runs = max(1, int(item["runs"]))
                item["turns_per_run"] = float(item["turn_count"] or 0) / runs
                item["stalls_per_run"] = float(item["stall_count"] or 0) / runs
                item["approvals_per_run"] = float(item["approval_count"] or 0) / runs
                dimensions.append(item)
            return {
                "since": since,
                "dimensions": dimensions,
                "event_counts": {row["type"]: row["count"] for row in events},
                "interpretation": "observational_correlation_only",
            }

        return await self._run(op)

    async def delete_history_entry(self, session_id: str) -> bool:
        def op() -> bool:
            with self._db:
                self._db.execute(
                    "DELETE FROM git_provenance WHERE agent_run_id=?", (session_id,)
                )
                self._db.execute("DELETE FROM history_messages WHERE history_id=?", (session_id,))
                self._db.execute(
                    "DELETE FROM history_transcript_index WHERE history_id=?", (session_id,)
                )
                cursor = self._db.execute(
                    "DELETE FROM history WHERE id=? AND agent_visible=1", (session_id,)
                )
            return bool(cursor.rowcount)

        return await self._run(op)

    async def history_entry(self, session_id: str) -> dict[str, Any] | None:
        def op() -> dict[str, Any] | None:
            row = self._db.execute("SELECT * FROM history WHERE id=?", (session_id,)).fetchone()
            return _public_history_row(row) if row else None

        return await self._run(op)

    async def agent_runs_for_session(self, session_id: str) -> list[dict[str, Any]]:
        """Visible mux-owned runs belonging to one persistent terminal session."""

        def op() -> list[dict[str, Any]]:
            rows = self._db.execute(
                "SELECT * FROM history WHERE note_id=? AND agent_visible=1 AND external=0 "
                "ORDER BY COALESCE(agent_run_seq,0),spawned_at,id",
                (session_id,),
            ).fetchall()
            return [_public_history_row(row) for row in rows]

        return await self._run(op)

    def _duplicate_conversation_groups(self) -> list[list[sqlite3.Row]]:
        """Visible mux-owned rows of every conversation that has more than one.

        Oldest row first within a group: that is the conversation's own row, the one
        `native_history_ids` hands the transcript to and the one a resume inherits.
        External (discovered) rows are excluded — one is *expected* alongside a mux
        row until reconciliation replaces it, and an external row owns no run.
        """
        groups = self._db.execute(
            "SELECT backend,native_id FROM history WHERE agent_visible=1 AND external=0 "
            "GROUP BY backend,native_id HAVING count(*)>1 ORDER BY backend,native_id"
        ).fetchall()
        return [
            self._db.execute(
                "SELECT * FROM history WHERE agent_visible=1 AND external=0 AND backend=? "
                "AND native_id=? ORDER BY spawned_at ASC, id ASC",
                (str(group["backend"]), str(group["native_id"])),
            ).fetchall()
            for group in groups
        ]

    def _message_counts(self, row_ids: list[str]) -> dict[str, int]:
        if not row_ids:
            return {}
        placeholders = ",".join("?" * len(row_ids))
        rows = self._db.execute(
            "SELECT history_id,count(*) AS total FROM history_messages "
            f"WHERE history_id IN ({placeholders}) GROUP BY history_id",
            tuple(row_ids),
        ).fetchall()
        return {str(row["history_id"]): int(row["total"]) for row in rows}

    async def duplicate_conversation_rows(self) -> list[dict[str, Any]]:
        """Report conversations whose history is split across several rows.

        One conversation is meant to be one entry. Several rows for one
        ``(backend, native_id)`` means something opened a second row over one
        transcript file, which shows the conversation twice in the list, indexes its
        messages twice in the search index, and leaves the reconciler free to move
        the content between them.
        """

        def op() -> list[dict[str, Any]]:
            report: list[dict[str, Any]] = []
            for rows in self._duplicate_conversation_groups():
                counts = self._message_counts([str(row["id"]) for row in rows])
                report.append(
                    {
                        "backend": str(rows[0]["backend"]),
                        "native_id": str(rows[0]["native_id"]),
                        "keeper": str(rows[0]["id"]),
                        "rows": [
                            {
                                "id": str(row["id"]),
                                "name": str(row["name"]),
                                "spawned_at": row["spawned_at"],
                                "exit_reason": row["exit_reason"],
                                "transcript_path": row["transcript_path"] or "",
                                "indexed_messages": counts.get(str(row["id"]), 0),
                            }
                            for row in rows
                        ],
                    }
                )
            return report

        return await self._run(op)

    async def merge_duplicate_conversation_rows(
        self, *, live_run_ids: frozenset[str] = frozenset(), dry_run: bool = True
    ) -> dict[str, Any]:
        """Fold each conversation's duplicate rows back into its own single entry.

        Repair, not deletion: what the duplicates measured belongs to the
        conversation, so the keeper takes the latest observation (a resumed pane's
        totals *are* the conversation's current totals), the user's rename if a later
        pane carried one, the widest native timestamp span, and the last pane's
        terminal markers. Only then are the duplicates and their rebuildable message
        copies removed. Native transcripts are never touched, and a quarantined row
        is never a candidate: it is an audit record of proven misattribution.

        A group with a *live* duplicate is skipped rather than merged. That pane is
        still writing to its row, and stranding its writes to tidy the list is the
        wrong trade; it merges once the pane exits.

        ``dry_run`` reports exactly what would change and writes nothing.
        """

        def op() -> dict[str, Any]:
            merged: list[dict[str, Any]] = []
            skipped: list[dict[str, Any]] = []
            for rows in self._duplicate_conversation_groups():
                keeper, *surplus = rows
                keeper_id = str(keeper["id"])
                live = [str(row["id"]) for row in surplus if str(row["id"]) in live_run_ids]
                if live:
                    skipped.append(
                        {
                            "backend": str(keeper["backend"]),
                            "native_id": str(keeper["native_id"]),
                            "keeper": keeper_id,
                            "reason": "live_run",
                            "live_rows": live,
                        }
                    )
                    continue
                updates = self._merged_conversation_values(
                    rows, keeper_live=keeper_id in live_run_ids
                )
                counts = self._message_counts([str(row["id"]) for row in rows])
                donor = self._message_donor(keeper_id, surplus, counts)
                record = {
                    "backend": str(keeper["backend"]),
                    "native_id": str(keeper["native_id"]),
                    "keeper": keeper_id,
                    "removed": [str(row["id"]) for row in surplus],
                    "messages_moved_from": donor,
                    "updated": dict(updates),
                }
                if not dry_run:
                    with self._db:
                        if updates:
                            assignments = ",".join(f"{column}=?" for column in updates)
                            self._db.execute(
                                f"UPDATE history SET {assignments} WHERE id=?",
                                (*updates.values(), keeper_id),
                            )
                        if donor is not None:
                            # Moved rather than reparsed: the copy is already correct,
                            # and the watermark moves with it so nothing re-reads a
                            # multi-megabyte transcript to learn what it already knew.
                            self._db.execute(
                                "UPDATE history_messages SET history_id=? WHERE history_id=?",
                                (keeper_id, donor),
                            )
                            self._db.execute(
                                "UPDATE history_transcript_index SET history_id=? "
                                "WHERE history_id=?",
                                (keeper_id, donor),
                            )
                        for row in surplus:
                            row_id = str(row["id"])
                            self._db.execute(
                                "DELETE FROM history_messages WHERE history_id=?", (row_id,)
                            )
                            self._db.execute(
                                "DELETE FROM history_transcript_index WHERE history_id=?",
                                (row_id,),
                            )
                            self._db.execute("DELETE FROM history WHERE id=?", (row_id,))
                merged.append(record)
            return {"dry_run": dry_run, "merged": merged, "skipped": skipped}

        return await self._run(op)

    @staticmethod
    def _merged_conversation_values(
        rows: list[sqlite3.Row], *, keeper_live: bool
    ) -> dict[str, Any]:
        """What the keeper row has to learn from the duplicates it absorbs."""
        keeper, *surplus = rows
        updates: dict[str, Any] = {}
        # A rename made in a later pane is the name the user chose for this
        # conversation; an auto title on the keeper is only a placeholder.
        if keeper["auto_named"]:
            renamed = next((row for row in surplus if not row["auto_named"]), None)
            if renamed is not None:
                updates["name"] = str(renamed["name"])
                updates["auto_named"] = 0
        if not keeper["transcript_path"]:
            found = next((row["transcript_path"] for row in surplus if row["transcript_path"]), "")
            if found:
                updates["transcript_path"] = found
        starts = [row["native_started_at"] for row in rows if row["native_started_at"] is not None]
        if starts and min(starts) != keeper["native_started_at"]:
            updates["native_started_at"] = min(starts)
        latest = max(
            (row for row in rows if row["last_message_at"] is not None),
            key=lambda row: float(row["last_message_at"]),
            default=None,
        )
        if latest is not None and latest["last_message_at"] != keeper["last_message_at"]:
            updates["last_message_at"] = latest["last_message_at"]
            updates["last_message_role"] = latest["last_message_role"]
        # Token and context figures are cumulative in the transcript, so the last
        # pane to observe the conversation holds its current numbers.
        observed = next(
            (row for row in reversed(rows) if row["context_window"] or row["tokens_in"]), None
        )
        if observed is not None and str(observed["id"]) != str(keeper["id"]):
            for column in (
                "tokens_in",
                "tokens_out",
                "tokens_cache_read",
                "tokens_cache_write",
                "cost_usd",
                "context_window",
                "final_context_pct",
                "peak_context_pct",
                "provider",
                "provider_account_hashes_json",
                "model",
                "measurement_source",
            ):
                updates[column] = observed[column]
        # Compactions are counted per row as each pane observes them. `max`, not a
        # sum: a resumed pane reads the conversation's existing records as historical,
        # and adding its count to theirs would report the same compaction twice.
        compactions = max(int(row["compaction_count"] or 0) for row in rows)
        if compactions != int(keeper["compaction_count"] or 0):
            updates["compaction_count"] = compactions
            evidence = max(
                (row for row in rows if row["last_compaction_at"] is not None),
                key=lambda row: float(row["last_compaction_at"]),
                default=None,
            )
            if evidence is not None:
                updates["last_compaction_at"] = evidence["last_compaction_at"]
                updates["compaction_capability"] = evidence["compaction_capability"]
                updates["compaction_confidence"] = evidence["compaction_confidence"]
        if not keeper_live:
            # The conversation ended when its last pane did, which is what the live
            # path also records: a resumed pane's exit rewrites the row it inherited.
            # The keeper's own markers describe only its first pane, so they are
            # replaced rather than merely filled in. A live keeper keeps its open
            # markers — writing an exit onto a row a pane is still using would report
            # a running conversation as finished.
            closed = max(
                (row for row in rows if row["exited_at"] is not None),
                key=lambda row: float(row["exited_at"]),
                default=None,
            )
            if closed is not None and closed["exited_at"] != keeper["exited_at"]:
                updates["exited_at"] = closed["exited_at"]
                updates["exit_reason"] = closed["exit_reason"]
                updates["final_state"] = closed["final_state"]
        return updates

    @staticmethod
    def _message_donor(
        keeper_id: str, surplus: list[sqlite3.Row], counts: dict[str, int]
    ) -> str | None:
        """The duplicate whose indexed messages the keeper should take over.

        Only when the keeper has none of its own: the reconciler indexed one file
        into whichever row it picked, so the conversation's searchable text can sit
        entirely on a duplicate that is about to be deleted.
        """
        if counts.get(keeper_id):
            return None
        best = max(surplus, key=lambda row: counts.get(str(row["id"]), 0), default=None)
        if best is None or not counts.get(str(best["id"])):
            return None
        return str(best["id"])

    async def record_context_compaction(
        self,
        session_id: str,
        observed_at: float,
        capability: str,
        confidence: str,
    ) -> None:
        """Persist only explicit provider-native compaction evidence."""

        def op() -> None:
            self._db.execute(
                "UPDATE history SET compaction_count=compaction_count+1,last_compaction_at=?,"
                "compaction_capability=?,compaction_confidence=? WHERE id=?",
                (observed_at, capability, confidence, session_id),
            )
            self._db.commit()

        await self._run(op)

    async def set_context_compaction_summary(
        self,
        session_id: str,
        count: int,
        last_observed_at: float | None,
        capability: str | None,
        confidence: str | None,
    ) -> None:
        def op() -> None:
            self._db.execute(
                "UPDATE history SET compaction_count=?,last_compaction_at=?,"
                "compaction_capability=?,compaction_confidence=? WHERE id=?",
                (count, last_observed_at, capability, confidence, session_id),
            )
            self._db.commit()

        await self._run(op)

    async def telemetry_history_rows(self, limit: int = 2000) -> list[dict[str, Any]]:
        """Bounded transcript inventory used by provider-versioned telemetry reconciliation."""

        def op() -> list[dict[str, Any]]:
            rows = self._db.execute(
                "SELECT id,backend,project_id,model,transcript_path,transcript_mtime_ns,"
                "transcript_size FROM history WHERE agent_visible=1 "
                "AND transcript_path IS NOT NULL "
                "AND transcript_path!='' ORDER BY spawned_at DESC LIMIT ?",
                (max(1, min(limit, 10000)),),
            ).fetchall()
            return [dict(row) for row in rows]

        return await self._run(op)

    async def register_project_scope(self, project: ProjectIdentity) -> dict[str, Any]:
        """Persist a concrete scope only after an operation actually uses it."""
        now = time.time()

        def op() -> dict[str, Any]:
            if project.repo_group_id:
                self._db.execute(
                    "INSERT INTO repo_groups(id,label,source,created_at,last_activity) "
                    "VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
                    "label=excluded.label,last_activity=excluded.last_activity",
                    (
                        project.repo_group_id,
                        project.repo_group_label or project.label,
                        project.source,
                        now,
                        now,
                    ),
                )
            self._db.execute(
                "INSERT INTO project_scopes"
                "(id,root,label,source,repo_group_id,created_at,last_activity) "
                "VALUES(?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET root=excluded.root,"
                "label=excluded.label,repo_group_id=excluded.repo_group_id,"
                "last_activity=excluded.last_activity",
                (
                    project.id,
                    project.root,
                    project.label,
                    project.source,
                    project.repo_group_id,
                    now,
                    now,
                ),
            )
            self._db.commit()
            row = self._db.execute(
                "SELECT * FROM project_scopes WHERE id=?", (project.id,)
            ).fetchone()
            return dict(row)

        return await self._run(op)

    async def project_scope(self, scope_id: str) -> dict[str, Any] | None:
        def op() -> dict[str, Any] | None:
            row = self._db.execute(
                "SELECT s.*,g.label AS repo_group_label FROM project_scopes s "
                "LEFT JOIN repo_groups g ON g.id=s.repo_group_id WHERE s.id=?",
                (scope_id,),
            ).fetchone()
            return dict(row) if row else None

        return await self._run(op)

    async def project_scopes(self, *, include_hidden: bool = False) -> list[dict[str, Any]]:
        hidden = "" if include_hidden else "WHERE s.hidden=0"

        def op() -> list[dict[str, Any]]:
            # Aggregate counts once per table via grouped derived tables (index-assisted)
            # instead of two correlated subqueries per scope row (O(scopes x rows)).
            rows = self._db.execute(
                f"SELECT s.*,g.label AS repo_group_label,"  # noqa: S608 -- fixed clause
                "COALESCE(hc.n,0) AS history_count,COALESCE(ac.n,0) AS artifact_count "
                "FROM project_scopes s "
                "LEFT JOIN repo_groups g ON g.id=s.repo_group_id "
                "LEFT JOIN (SELECT project_scope_id,COUNT(*) n FROM history "
                "GROUP BY project_scope_id) hc ON hc.project_scope_id=s.id "
                "LEFT JOIN (SELECT project_scope_id,COUNT(*) n FROM artifacts "
                "GROUP BY project_scope_id) ac ON ac.project_scope_id=s.id "
                f"{hidden} ORDER BY s.last_activity DESC,s.label"
            ).fetchall()
            return [dict(row) for row in rows]

        return await self._run(op)

    async def set_project_hidden(self, scope_id: str, hidden: bool) -> bool:
        def op() -> bool:
            cursor = self._db.execute(
                "UPDATE project_scopes SET hidden=? WHERE id=?", (int(hidden), scope_id)
            )
            self._db.commit()
            return bool(cursor.rowcount)

        return await self._run(op)

    async def project_blockers(self, scope_id: str) -> dict[str, int]:
        def op() -> dict[str, int]:
            row = self._db.execute(
                "SELECT (SELECT COUNT(*) FROM history WHERE project_scope_id=?) history,"
                "(SELECT COUNT(*) FROM artifacts WHERE project_scope_id=?) artifacts",
                (scope_id, scope_id),
            ).fetchone()
            return dict(row)

        return await self._run(op)

    async def forget_project_scope(self, scope_id: str) -> dict[str, Any]:
        blockers = await self.project_blockers(scope_id)
        if any(blockers.values()):
            return {"forgotten": False, "blockers": blockers}

        def op() -> bool:
            cursor = self._db.execute("DELETE FROM project_scopes WHERE id=?", (scope_id,))
            self._db.commit()
            return bool(cursor.rowcount)

        forgotten = await self._run(op)
        return {"forgotten": forgotten, "blockers": blockers}

    async def bind_artifact(
        self,
        *,
        artifact_id: str,
        kind: str,
        owner_type: str,
        owner_id: str,
        owner_label: str | None = None,
        project_scope_id: str,
        relative_path: str,
    ) -> dict[str, Any]:
        now = time.time()

        def op() -> dict[str, Any]:
            self._db.execute(
                "INSERT INTO artifacts(id,kind,owner_type,owner_id,owner_label,project_scope_id,"
                "relative_path,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(kind,owner_type,owner_id) "
                "DO UPDATE SET updated_at=excluded.updated_at,"
                "owner_label=COALESCE(excluded.owner_label,artifacts.owner_label)",
                (
                    artifact_id,
                    kind,
                    owner_type,
                    owner_id,
                    owner_label,
                    project_scope_id,
                    relative_path,
                    now,
                    now,
                ),
            )
            self._db.commit()
            row = self._db.execute(
                "SELECT * FROM artifacts WHERE kind=? AND owner_type=? AND owner_id=?",
                (kind, owner_type, owner_id),
            ).fetchone()
            return dict(row)

        return await self._run(op)

    async def artifacts(self, scope_id: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM artifacts"
        args: tuple[Any, ...] = ()
        if scope_id:
            sql += " WHERE project_scope_id=?"
            args = (scope_id,)
        sql += " ORDER BY updated_at DESC"

        def op() -> list[dict[str, Any]]:
            rows = self._db.execute(sql, args).fetchall()
            return [dict(row) for row in rows]

        return await self._run(op)

    async def delete_artifact_binding(self, artifact_id: str) -> bool:
        """Remove only the mux index entry; the user-owned file is never deleted."""

        def op() -> bool:
            cursor = self._db.execute("DELETE FROM artifacts WHERE id=?", (artifact_id,))
            self._db.commit()
            return bool(cursor.rowcount)

        return await self._run(op)

    async def move_artifact_scope(
        self, artifact_id: str, scope_id: str, relative_path: str
    ) -> bool:
        def op() -> bool:
            cursor = self._db.execute(
                "UPDATE artifacts SET project_scope_id=?,relative_path=?,updated_at=?,"
                "placement_acknowledged_scope_id=NULL WHERE id=?",
                (scope_id, relative_path, time.time(), artifact_id),
            )
            self._db.commit()
            return bool(cursor.rowcount)

        return await self._run(op)

    async def acknowledge_artifact_placement(self, artifact_id: str, anchor_scope_id: str) -> bool:
        def op() -> bool:
            cursor = self._db.execute(
                "UPDATE artifacts SET placement_acknowledged_scope_id=?,updated_at=? WHERE id=?",
                (anchor_scope_id, time.time(), artifact_id),
            )
            self._db.commit()
            return bool(cursor.rowcount)

        return await self._run(op)

    async def events(
        self,
        since: float = 0,
        session_id: str | None = None,
        limit: int = 500,
        after_seq: int = 0,
    ) -> list[dict[str, Any]]:
        sql = "SELECT seq,ts,session_id,source,type,payload_json FROM events WHERE ts>? AND seq>?"
        args: list[Any] = [since, after_seq]
        if session_id:
            sql += " AND session_id=?"
            args.append(session_id)
        sql += " ORDER BY seq LIMIT ?"
        args.append(limit)

        def op() -> list[dict[str, Any]]:
            rows = self._db.execute(sql, args).fetchall()
            return [{**dict(r), "payload": json.loads(r["payload_json"])} for r in rows]

        return await self._run(op)

    async def recent_events(
        self, *, session_id: str | None = None, limit: int = 500
    ) -> tuple[list[dict[str, Any]], bool]:
        """The NEWEST retained events, oldest-first, plus a truncation flag.

        `events()` walks forward from a cursor, which is right for resuming a
        known position but wrong for a cold open: with no cursor it returns the
        oldest rows in a 90-day/100k-row table. A client reconnecting after a gap
        needs the tail, and needs to know when the tail does not cover the gap.
        """
        sql = "SELECT seq,ts,session_id,source,type,payload_json FROM events"
        args: list[Any] = []
        if session_id:
            sql += " WHERE session_id=?"
            args.append(session_id)
        # One extra row distinguishes "exactly filled the window" from "there is
        # more history than the window carries".
        sql += " ORDER BY seq DESC LIMIT ?"
        args.append(max(1, limit) + 1)

        def op() -> tuple[list[dict[str, Any]], bool]:
            rows = self._db.execute(sql, args).fetchall()
            truncated = len(rows) > limit
            kept = rows[:limit]
            kept.reverse()
            return (
                [{**dict(r), "payload": json.loads(r["payload_json"])} for r in kept],
                truncated,
            )

        return await self._run(op)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._executor.submit(self._db.close).result()
        self._executor.shutdown(wait=True)
