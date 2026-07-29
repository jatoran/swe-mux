from __future__ import annotations

import asyncio
import json
import re
import sqlite3
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any, TypeVar

from .git_projects import ProjectIdentity
from .models import MuxEvent, ProjectGroupRecord, ProjectRecord, SessionRecord
from .spawn_contract import infer_agent_executable_backend
from .sqlite_store import (
    connect_or_quarantine,
    database_operation_lock,
    run_sqlite_operation,
)
from .transcript_view import (
    TRANSCRIPT_PARSER_VERSION,
    parse_transcript,
    searchable_transcript_messages,
    transcript_time_summary,
)

T = TypeVar("T")

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
  sidebar_visible INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS history (
  id TEXT PRIMARY KEY, native_id TEXT NOT NULL, backend TEXT NOT NULL,
  name TEXT NOT NULL, cwd TEXT NOT NULL, project_id TEXT, note_id TEXT,
  spawned_at REAL NOT NULL, exited_at REAL, exit_reason TEXT,
  tokens_in INTEGER NOT NULL DEFAULT 0, tokens_out INTEGER NOT NULL DEFAULT 0,
  transcript_path TEXT, external INTEGER NOT NULL DEFAULT 0,
  executable TEXT, argv_json TEXT, pinned_attention INTEGER NOT NULL DEFAULT 0,
  shell_profile_id TEXT, agent_visible INTEGER NOT NULL DEFAULT 0,
  repository_id TEXT, project_label TEXT, project_root TEXT,
  final_state TEXT, context_window INTEGER, final_context_pct REAL,
  peak_context_pct REAL, model TEXT, measurement_source TEXT,
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
  ts TEXT, text TEXT NOT NULL, source_mtime_ns INTEGER NOT NULL,
  source_size INTEGER NOT NULL, parser_version INTEGER NOT NULL,
  UNIQUE(history_id, ordinal)
);
CREATE TABLE IF NOT EXISTS history_transcript_index (
  history_id TEXT PRIMARY KEY, source_mtime_ns INTEGER NOT NULL,
  source_size INTEGER NOT NULL, parser_version INTEGER NOT NULL,
  message_count INTEGER NOT NULL, indexed_at REAL NOT NULL
);
CREATE VIRTUAL TABLE IF NOT EXISTS history_messages_fts USING fts5(
  text, content='history_messages', content_rowid='id', tokenize='unicode61 remove_diacritics 2'
);
CREATE TRIGGER IF NOT EXISTS history_messages_ai AFTER INSERT ON history_messages BEGIN
  INSERT INTO history_messages_fts(rowid,text) VALUES(new.id,new.text);
END;
CREATE TRIGGER IF NOT EXISTS history_messages_ad AFTER DELETE ON history_messages BEGIN
  INSERT INTO history_messages_fts(history_messages_fts,rowid,text)
  VALUES('delete',old.id,old.text);
END;
CREATE TRIGGER IF NOT EXISTS history_messages_au AFTER UPDATE ON history_messages BEGIN
  INSERT INTO history_messages_fts(history_messages_fts,rowid,text)
  VALUES('delete',old.id,old.text);
  INSERT INTO history_messages_fts(rowid,text) VALUES(new.id,new.text);
END;
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
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id, ts);
CREATE INDEX IF NOT EXISTS idx_history_spawned ON history(spawned_at DESC);
CREATE INDEX IF NOT EXISTS idx_history_messages_history ON history_messages(history_id, ordinal);
CREATE INDEX IF NOT EXISTS idx_history_messages_source
  ON history_messages(history_id, source_mtime_ns, source_size, parser_version);
"""


def _fts_query(value: str) -> str:
    """Create a literal prefix-token FTS query; never expose raw FTS syntax."""
    tokens = re.findall(r"\w+", value, flags=re.UNICODE)
    return " AND ".join(f'"{token.replace(chr(34), chr(34) * 2)}"*' for token in tokens)


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

    def _open(self) -> sqlite3.Connection:
        db = sqlite3.connect(self._path, check_same_thread=False)
        db.row_factory = sqlite3.Row
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
            "repository_id": "ALTER TABLE history ADD COLUMN repository_id TEXT",
            "project_label": "ALTER TABLE history ADD COLUMN project_label TEXT",
            "project_root": "ALTER TABLE history ADD COLUMN project_root TEXT",
            "final_state": "ALTER TABLE history ADD COLUMN final_state TEXT",
            "context_window": "ALTER TABLE history ADD COLUMN context_window INTEGER",
            "final_context_pct": "ALTER TABLE history ADD COLUMN final_context_pct REAL",
            "peak_context_pct": "ALTER TABLE history ADD COLUMN peak_context_pct REAL",
            "model": "ALTER TABLE history ADD COLUMN model TEXT",
            "measurement_source": "ALTER TABLE history ADD COLUMN measurement_source TEXT",
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
                "UPDATE history SET agent_visible=1 WHERE backend IN ('claude','codex') "
                f"AND (exit_reason IS NULL OR exit_reason NOT IN ({_QUARANTINE_REASON_SQL}))"
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
        self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_history_agent_project "
            "ON history(agent_visible,project_id,spawned_at DESC)"
        )
        self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_history_agent_filters "
            "ON history(agent_visible,backend,project_id,external,spawned_at DESC)"
        )
        # Project-scope filters/joins (history list, projects sidebar, scope counts)
        # otherwise fall back to full-table scans.
        self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_history_scope "
            "ON history(project_scope_id,spawned_at DESC)"
        )
        self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_artifacts_scope ON artifacts(project_scope_id)"
        )

    async def list_projects(self) -> list[ProjectRecord]:
        def op() -> list[ProjectRecord]:
            rows = self._db.execute("SELECT * FROM projects ORDER BY position,name").fetchall()
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
                    resource_open_mode=row["resource_open_mode"],
                    sidebar_visible=bool(row["sidebar_visible"]),
                )
                for row in rows
            ]

        return await self._run(op)

    async def upsert_project(self, project: ProjectRecord) -> None:
        def op() -> None:
            self._db.execute(
                "INSERT INTO projects(id,name,root,position,group_id,layout_json,default_backend,"
                "layout_revision,default_profile_id,resource_open_mode,sidebar_visible) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET name=excluded.name,root=excluded.root,"
                "position=excluded.position,group_id=excluded.group_id,"
                "layout_json=excluded.layout_json,default_backend=excluded.default_backend,"
                "layout_revision=excluded.layout_revision,"
                "default_profile_id=excluded.default_profile_id,"
                "resource_open_mode=excluded.resource_open_mode,"
                "sidebar_visible=excluded.sidebar_visible",
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
                ),
            )
            self._db.commit()

        await self._run(op)

    async def reorder_projects(self, ordered_ids: list[str]) -> None:
        """Replace every Project position in one transaction.

        The manager validates optimistic ordering before this write. Keeping the
        replacement atomic prevents readers from observing duplicate or partial
        positions while multiple rows move.
        """

        def op() -> None:
            rows = self._db.execute("SELECT id FROM projects").fetchall()
            current_ids = {str(row["id"]) for row in rows}
            if len(ordered_ids) != len(current_ids) or set(ordered_ids) != current_ids:
                raise ValueError("project order must contain every registered project once")
            with self._db:
                self._db.executemany(
                    "UPDATE projects SET position=? WHERE id=?",
                    [(position, project_id) for position, project_id in enumerate(ordered_ids)],
                )

        await self._run(op)

    async def delete_project(self, project_id: str) -> None:
        def op() -> None:
            self._db.execute("DELETE FROM projects WHERE id=?", (project_id,))
            self._db.commit()

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

    async def session_started(self, session: SessionRecord, transcript: str | None) -> None:
        def op() -> None:
            self._db.execute(
                "INSERT OR REPLACE INTO history"
                "(id,native_id,backend,name,cwd,project_id,note_id,spawned_at,"
                "tokens_in,tokens_out,transcript_path,executable,argv_json,"
                "pinned_attention,shell_profile_id,agent_visible,repository_id,project_label,"
                "project_root,context_window,final_context_pct,peak_context_pct,model,"
                "measurement_source,project_scope_id,repo_group_id,auto_named) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    session.id,
                    session.native_session_id,
                    session.backend,
                    session.name,
                    session.cwd,
                    session.project_id,
                    session.id,
                    session.created_at,
                    session.tokens_in,
                    session.tokens_out,
                    transcript,
                    session.exe,
                    json.dumps(session.args),
                    int(session.pinned_attention),
                    session.shell_profile_id,
                    int(session.backend in {"claude", "codex"}),
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
                ),
            )
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
                "name=?,cwd=?,project_id=?,executable=?,argv_json=?,pinned_attention=?,"
                "shell_profile_id=?,final_state=?,context_window=COALESCE(?,context_window),"
                "final_context_pct=COALESCE(?,final_context_pct),"
                "peak_context_pct=COALESCE(?,peak_context_pct),model=COALESCE(?,model),"
                "measurement_source=COALESCE(?,measurement_source),auto_named=? WHERE id=?",
                (
                    session.last_activity_ts,
                    reason,
                    session.tokens_in,
                    session.tokens_out,
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
                    session.model,
                    session.measurement_source,
                    int(session.auto_named),
                    row_id,
                ),
            )
            self._db.commit()

        await self._run(op)

    async def update_agent_summary(self, session: SessionRecord) -> None:
        if not session.context_window:
            return

        def op() -> None:
            self._db.execute(
                "UPDATE history SET tokens_in=?,tokens_out=?,context_window=?,"
                "final_context_pct=?,peak_context_pct=?,model=?,measurement_source=? "
                "WHERE id=? AND agent_visible=1",
                (
                    session.tokens_in,
                    session.tokens_out,
                    session.context_window,
                    session.context_pct,
                    session.context_peak_pct,
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
                "(id,native_id,backend,name,cwd,project_id,note_id,spawned_at,tokens_in,tokens_out,"
                "transcript_path,executable,argv_json,pinned_attention,shell_profile_id,"
                "agent_visible,repository_id,project_label,project_root,context_window,"
                "final_context_pct,peak_context_pct,model,measurement_source,"
                "project_scope_id,repo_group_id,auto_named) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET native_id=excluded.native_id,"
                "backend=excluded.backend,name=excluded.name,cwd=excluded.cwd,"
                "project_id=excluded.project_id,transcript_path=excluded.transcript_path,"
                "note_id=excluded.note_id,"
                "agent_visible=1,repository_id=excluded.repository_id,"
                "project_label=excluded.project_label,project_root=excluded.project_root,"
                "project_scope_id=excluded.project_scope_id,repo_group_id=excluded.repo_group_id,"
                "auto_named=excluded.auto_named",
                (
                    run_id,
                    session.native_session_id,
                    session.backend,
                    session.name,
                    session.run_cwd or session.cwd,
                    session.project_id,
                    session.id,
                    session.agent_run_started_at or session.created_at,
                    session.tokens_in,
                    session.tokens_out,
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
                "FROM history WHERE agent_visible=1 AND backend IN ('claude','codex')"
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
                "final_state=?,context_window=COALESCE(?,context_window),"
                "final_context_pct=COALESCE(?,final_context_pct),"
                "peak_context_pct=COALESCE(?,peak_context_pct),model=COALESCE(?,model),"
                "measurement_source=COALESCE(?,measurement_source) WHERE id=? AND agent_visible=1",
                (
                    time.time(),
                    reason,
                    session.tokens_in,
                    session.tokens_out,
                    "idle" if reason == "agent_exit" else session.state,
                    session.context_window or None,
                    session.context_pct if session.context_window else None,
                    session.context_peak_pct if session.context_window else None,
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
                    "final_context_pct,peak_context_pct,tokens_in,tokens_out,model,"
                    "measurement_source,project_scope_id,repo_group_id,"
                    "transcript_mtime_ns,transcript_size) "
                    "VALUES(?,?,?,?,?,?,?,?,?,1,1,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
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
                    "tokens_out=excluded.tokens_out,model=excluded.model,"
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
        def op() -> dict[tuple[str, str], str]:
            rows = self._db.execute(
                "SELECT id,backend,native_id,external FROM history WHERE agent_visible=1 "
                "ORDER BY external ASC"
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
                    "(history_id,ordinal,role,ts,text,source_mtime_ns,source_size,parser_version) "
                    "VALUES(?,?,?,?,?,?,?,?)",
                    [
                        (
                            history_id,
                            int(item["ordinal"]),
                            str(item["role"]),
                            None if item.get("ts") is None else str(item["ts"]),
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
            transcript = item.get("transcript_path")
            if not transcript or item.get("backend") not in {"claude", "codex"}:
                return None
            path = Path(str(transcript))
            try:
                stat = await asyncio.to_thread(path.stat)
            except OSError:
                return None
            if (
                item.get("time_summary_mtime_ns") == stat.st_mtime_ns
                and item.get("time_summary_size") == stat.st_size
            ):
                return None
            try:
                async with semaphore:
                    summary = await asyncio.to_thread(
                        transcript_time_summary, path, str(item["backend"])
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
        self, history_id: str, transcript_path: str | Path, backend: str, *, force: bool = False
    ) -> tuple[str, int]:
        """Index one native transcript without blocking the event loop."""
        path = Path(transcript_path)
        stat = await asyncio.to_thread(path.stat)
        watermark = (stat.st_mtime_ns, stat.st_size, TRANSCRIPT_PARSER_VERSION)

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
        messages = await asyncio.to_thread(parse_transcript, path, backend)
        count = await self.replace_history_messages(
            history_id,
            messages,
            mtime_ns=stat.st_mtime_ns,
            size=stat.st_size,
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

    async def history(
        self, query: str = "", backend: str | None = None, limit: int = 200
    ) -> list[dict[str, Any]]:
        # Skip the leading-wildcard LIKE (which no index can serve and which runs
        # per row) for an empty query; the empty-query `%%` matched every row
        # anyway, so omitting the clause is behaviourally identical.
        sql = "SELECT * FROM history WHERE agent_visible=1 AND backend IN ('claude','codex')"
        args: list[Any] = []
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
            return [dict(r) for r in rows]

        return await self._run(op)

    async def update_project_label(self, project_id: str, label: str) -> None:
        def op() -> None:
            self._db.execute(
                "UPDATE history SET project_label=? WHERE project_scope_id=?",
                (label, project_id),
            )
            self._db.commit()

        await self._run(op)

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
        sql = (
            "SELECT h.* FROM history h WHERE h.agent_visible=1 AND h.backend IN ('claude','codex')"
        )
        args: list[Any] = []
        match_query = _fts_query(query)
        if query:
            metadata_sql = "(h.name LIKE ? OR h.cwd LIKE ? OR COALESCE(h.project_label,'') LIKE ?)"
            metadata_args = [f"%{query}%", f"%{query}%", f"%{query}%"]
            message_sql = (
                "EXISTS (SELECT 1 FROM history_messages hm "
                "JOIN history_messages_fts ON history_messages_fts.rowid=hm.id "
                "WHERE hm.history_id=h.id AND history_messages_fts MATCH ?"
            )
            message_args: list[Any] = [match_query]
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
            items = [dict(row) for row in page]
            if query and match_query:
                role = search_scope if search_scope in {"user", "assistant"} else None
                for item in items:
                    match_args: list[Any] = [match_query, item["id"]]
                    role_sql = ""
                    if role:
                        role_sql = " AND hm.role=?"
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
                    item["matches"] = [dict(match) for match in matches[:3]]
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

        def op() -> list[dict[str, Any]]:
            args: list[Any] = [match_query, history_id]
            role_sql = ""
            if search_scope in {"user", "assistant"}:
                role_sql = " AND hm.role=?"
                args.append(search_scope)
            args.append(max(1, min(limit, 2000)))
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

        return await self._run(op)

    async def history_projects(self) -> list[dict[str, Any]]:
        def op() -> list[dict[str, Any]]:
            rows = self._db.execute(
                "SELECT h.project_id,COALESCE(MAX(p.name),'Unassigned') AS label,"
                "MAX(p.root) AS root,COUNT(*) AS sessions,MAX(h.spawned_at) AS last_activity "
                "FROM history h LEFT JOIN projects p ON p.id=h.project_id "
                "WHERE h.agent_visible=1 AND h.backend IN ('claude','codex') "
                "GROUP BY h.project_id ORDER BY last_activity DESC"
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
            return dict(row) if row else None

        return await self._run(op)

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
