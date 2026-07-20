from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, TypeVar

from .git_projects import ProjectIdentity
from .models import MuxEvent, ProjectGroupRecord, ProjectRecord, SessionRecord
from .sqlite_store import database_operation_lock, run_sqlite_operation

T = TypeVar("T")

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
  name TEXT NOT NULL, cwd TEXT NOT NULL, project_id TEXT,
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
  transcript_mtime_ns INTEGER, transcript_size INTEGER
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
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id, ts);
CREATE INDEX IF NOT EXISTS idx_history_spawned ON history(spawned_at DESC);
"""


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
            self._db = sqlite3.connect(self._path, check_same_thread=False)
            self._db.row_factory = sqlite3.Row
            _tune_connection(self._db)
            self._db.executescript(SCHEMA)
            self._migrate_schema()
            self._db.commit()

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
        }
        for column, statement in migrations.items():
            if column not in columns:
                self._db.execute(statement)
        self._db.execute("UPDATE history SET agent_visible=1 WHERE backend IN ('claude','codex')")
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
                "(id,native_id,backend,name,cwd,project_id,spawned_at,"
                "tokens_in,tokens_out,transcript_path,executable,argv_json,"
                "pinned_attention,shell_profile_id,agent_visible,repository_id,project_label,"
                "project_root,context_window,final_context_pct,peak_context_pct,model,"
                "measurement_source,project_scope_id,repo_group_id,auto_named) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    session.id,
                    session.native_session_id,
                    session.backend,
                    session.name,
                    session.cwd,
                    session.project_id,
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
        """Persist mutable metadata from a live session as one atomic update."""

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
                    session.id,
                ),
            )
            self._db.commit()

        await self._run(op)

    async def session_ended(self, session: SessionRecord, reason: str) -> None:
        def op() -> None:
            row = self._db.execute(
                "SELECT agent_visible FROM history WHERE id=?", (session.id,)
            ).fetchone()
            if row and not row["agent_visible"]:
                self._db.execute("DELETE FROM history WHERE id=?", (session.id,))
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
                    session.id,
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
                "(id,native_id,backend,name,cwd,project_id,spawned_at,tokens_in,tokens_out,"
                "transcript_path,executable,argv_json,pinned_attention,shell_profile_id,"
                "agent_visible,repository_id,project_label,project_root,context_window,"
                "final_context_pct,peak_context_pct,model,measurement_source,"
                "project_scope_id,repo_group_id,auto_named) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET native_id=excluded.native_id,"
                "backend=excluded.backend,name=excluded.name,cwd=excluded.cwd,"
                "project_id=excluded.project_id,transcript_path=excluded.transcript_path,"
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
                    "(id,native_id,backend,name,cwd,spawned_at,transcript_path,external,"
                    "agent_visible,repository_id,project_label,project_root,context_window,"
                    "final_context_pct,peak_context_pct,tokens_in,tokens_out,model,"
                    "measurement_source,project_scope_id,repo_group_id,"
                    "transcript_mtime_ns,transcript_size) "
                    "VALUES(?,?,?,?,?,?,?,1,1,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(id) DO UPDATE SET "
                    "name=excluded.name,cwd=excluded.cwd,spawned_at=excluded.spawned_at,"
                    "transcript_path=excluded.transcript_path,repository_id=excluded.repository_id,"
                    "project_label=excluded.project_label,project_root=excluded.project_root,"
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
        backend: str | None = None,
        state: str | None = None,
        project_id: str | None = None,
        external: bool | None = None,
        date_from: float | None = None,
        date_to: float | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        limit = max(1, min(limit, 200))
        sql = "SELECT * FROM history WHERE agent_visible=1 AND backend IN ('claude','codex')"
        args: list[Any] = []
        if query:
            sql += " AND (name LIKE ? OR cwd LIKE ? OR COALESCE(project_label,'') LIKE ?)"
            args += [f"%{query}%", f"%{query}%", f"%{query}%"]
        filters = {"backend": backend, "final_state": state}
        for column, value in filters.items():
            if value:
                sql += f" AND {column}=?"
                args.append(value)
        if project_id == "__ungrouped__":
            sql += " AND project_id IS NULL"
        elif project_id:
            sql += " AND project_id=?"
            args.append(project_id)
        if external is not None:
            sql += " AND external=?"
            args.append(int(external))
        if date_from is not None:
            sql += " AND spawned_at>=?"
            args.append(date_from)
        if date_to is not None:
            sql += " AND spawned_at<=?"
            args.append(date_to)
        if cursor:
            stamp, row_id = cursor.split(":", 1)
            sql += " AND (spawned_at<? OR (spawned_at=? AND id<?))"
            args.extend([float(stamp), float(stamp), row_id])
        sql += " ORDER BY spawned_at DESC,id DESC LIMIT ?"
        args.append(limit + 1)

        def op() -> dict[str, Any]:
            rows = self._db.execute(sql, args).fetchall()
            has_more = len(rows) > limit
            page = rows[:limit]
            items = [dict(row) for row in page]
            next_cursor = (
                f"{page[-1]['spawned_at']}:{page[-1]['id']}" if has_more and page else None
            )
            return {"items": items, "next_cursor": next_cursor}

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
            cursor = self._db.execute(
                "DELETE FROM history WHERE id=? AND agent_visible=1", (session_id,)
            )
            self._db.commit()
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

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._executor.submit(self._db.close).result()
        self._executor.shutdown(wait=True)
