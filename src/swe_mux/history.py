from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from .models import MuxEvent, SessionRecord, SpaceRecord
from .projects import ProjectIdentity, project_scope_id

SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS spaces (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, position INTEGER NOT NULL,
  layout_json TEXT, default_cwd TEXT, default_backend TEXT,
  layout_revision INTEGER NOT NULL DEFAULT 0,
  anchor_mode TEXT NOT NULL DEFAULT 'auto', anchor_project_scope_id TEXT,
  anchor_revision INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS history (
  id TEXT PRIMARY KEY, native_id TEXT NOT NULL, backend TEXT NOT NULL,
  name TEXT NOT NULL, cwd TEXT NOT NULL, space_id TEXT,
  spawned_at REAL NOT NULL, exited_at REAL, exit_reason TEXT,
  tokens_in INTEGER NOT NULL DEFAULT 0, tokens_out INTEGER NOT NULL DEFAULT 0,
  transcript_path TEXT, external INTEGER NOT NULL DEFAULT 0,
  executable TEXT, argv_json TEXT, pinned_attention INTEGER NOT NULL DEFAULT 0,
  shell_profile_id TEXT, agent_visible INTEGER NOT NULL DEFAULT 0,
  project_id TEXT, project_label TEXT, project_root TEXT,
  final_state TEXT, context_window INTEGER, final_context_pct REAL,
  peak_context_pct REAL, model TEXT, measurement_source TEXT,
  project_scope_id TEXT, repo_group_id TEXT
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


class HistoryIndex:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(SCHEMA)
        self._migrate_schema()
        self._db.commit()
        self._lock = asyncio.Lock()

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
            "project_label": "ALTER TABLE history ADD COLUMN project_label TEXT",
            "project_root": "ALTER TABLE history ADD COLUMN project_root TEXT",
            "final_state": "ALTER TABLE history ADD COLUMN final_state TEXT",
            "context_window": "ALTER TABLE history ADD COLUMN context_window INTEGER",
            "final_context_pct": "ALTER TABLE history ADD COLUMN final_context_pct REAL",
            "peak_context_pct": "ALTER TABLE history ADD COLUMN peak_context_pct REAL",
            "model": "ALTER TABLE history ADD COLUMN model TEXT",
            "measurement_source": "ALTER TABLE history ADD COLUMN measurement_source TEXT",
            "project_scope_id": "ALTER TABLE history ADD COLUMN project_scope_id TEXT",
            "repo_group_id": "ALTER TABLE history ADD COLUMN repo_group_id TEXT",
        }
        for column, statement in migrations.items():
            if column not in columns:
                self._db.execute(statement)
        self._db.execute("UPDATE history SET agent_visible=1 WHERE backend IN ('claude','codex')")
        self._db.execute(
            "DELETE FROM history WHERE backend='shell' AND agent_visible=0 AND "
            "(transcript_path IS NULL OR transcript_path='')"
        )
        space_columns = {
            row["name"] for row in self._db.execute("PRAGMA table_info(spaces)").fetchall()
        }
        if "layout_revision" not in space_columns:
            self._db.execute(
                "ALTER TABLE spaces ADD COLUMN layout_revision INTEGER NOT NULL DEFAULT 0"
            )
        if "default_profile_id" not in space_columns:
            self._db.execute("ALTER TABLE spaces ADD COLUMN default_profile_id TEXT")
        if "anchor_mode" not in space_columns:
            self._db.execute(
                "ALTER TABLE spaces ADD COLUMN anchor_mode TEXT NOT NULL DEFAULT 'auto'"
            )
        if "anchor_project_scope_id" not in space_columns:
            self._db.execute("ALTER TABLE spaces ADD COLUMN anchor_project_scope_id TEXT")
        if "anchor_revision" not in space_columns:
            self._db.execute(
                "ALTER TABLE spaces ADD COLUMN anchor_revision INTEGER NOT NULL DEFAULT 0"
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
        self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_history_agent_project "
            "ON history(agent_visible,project_id,spawned_at DESC)"
        )
        self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_history_agent_filters "
            "ON history(agent_visible,backend,space_id,external,spawned_at DESC)"
        )
        # Earlier releases used repository identity as project_id. Preserve it as
        # the repo grouping while creating concrete scope identities from roots.
        legacy = self._db.execute(
            "SELECT DISTINCT project_id,project_label,project_root FROM history "
            "WHERE project_root IS NOT NULL AND project_root<>''"
        ).fetchall()
        now = time.time()
        for row in legacy:
            scope_id = project_scope_id(row["project_root"])
            is_git_scope = (Path(row["project_root"]) / ".git").exists()
            repo_group_id = (
                row["project_id"] if is_git_scope and row["project_id"] != scope_id else None
            )
            if repo_group_id:
                self._db.execute(
                    "INSERT OR IGNORE INTO repo_groups(id,label,source,created_at,last_activity) "
                    "VALUES(?,?,?,?,?)",
                    (repo_group_id, row["project_label"] or "Repository", "legacy", now, now),
                )
            self._db.execute(
                "INSERT OR IGNORE INTO project_scopes"
                "(id,root,label,source,repo_group_id,created_at,last_activity) "
                "VALUES(?,?,?,?,?,?,?)",
                (
                    scope_id,
                    row["project_root"],
                    row["project_label"] or Path(row["project_root"]).name,
                    "migration",
                    repo_group_id,
                    now,
                    now,
                ),
            )
            self._db.execute(
                "UPDATE history SET project_scope_id=?,repo_group_id=? WHERE project_root=?",
                (scope_id, repo_group_id, row["project_root"]),
            )

    async def ensure_default_space(self, space: SpaceRecord) -> None:
        async with self._lock:
            self._db.execute(
                "INSERT OR IGNORE INTO spaces(id,name,position,layout_json,layout_revision) "
                "VALUES(?,?,?,?,?)",
                (space.id, space.name, space.position, json.dumps(space.layout), 0),
            )
            self._db.commit()

    async def list_spaces(self) -> list[SpaceRecord]:
        async with self._lock:
            rows = self._db.execute("SELECT * FROM spaces ORDER BY position,name").fetchall()
        return [
            SpaceRecord(
                r["id"],
                r["name"],
                r["position"],
                json.loads(r["layout_json"]) if r["layout_json"] else None,
                r["default_cwd"],
                r["default_backend"],
                r["layout_revision"],
                r["default_profile_id"],
                r["anchor_mode"],
                r["anchor_project_scope_id"],
                r["anchor_revision"],
            )
            for r in rows
        ]

    async def upsert_space(self, space: SpaceRecord) -> None:
        async with self._lock:
            self._db.execute(
                "INSERT INTO spaces(id,name,position,layout_json,default_cwd,default_backend,"
                "layout_revision,default_profile_id,anchor_mode,anchor_project_scope_id,"
                "anchor_revision) VALUES(?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET name=excluded.name, "
                "position=excluded.position, layout_json=excluded.layout_json, "
                "default_cwd=excluded.default_cwd, "
                "default_backend=excluded.default_backend, "
                "layout_revision=excluded.layout_revision, "
                "default_profile_id=excluded.default_profile_id, "
                "anchor_mode=excluded.anchor_mode, "
                "anchor_project_scope_id=excluded.anchor_project_scope_id, "
                "anchor_revision=excluded.anchor_revision",
                (
                    space.id,
                    space.name,
                    space.position,
                    json.dumps(space.layout),
                    space.default_cwd,
                    space.default_backend,
                    space.layout_revision,
                    space.default_profile_id,
                    space.anchor_mode,
                    space.anchor_project_scope_id,
                    space.anchor_revision,
                ),
            )
            self._db.commit()

    async def delete_space(self, space_id: str) -> None:
        async with self._lock:
            self._db.execute("UPDATE history SET space_id='default' WHERE space_id=?", (space_id,))
            self._db.execute("DELETE FROM spaces WHERE id=?", (space_id,))
            self._db.commit()

    async def session_started(self, session: SessionRecord, transcript: str | None) -> None:
        async with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO history"
                "(id,native_id,backend,name,cwd,space_id,spawned_at,"
                "tokens_in,tokens_out,transcript_path,executable,argv_json,"
                "pinned_attention,shell_profile_id,agent_visible,project_id,project_label,"
                "project_root,context_window,final_context_pct,peak_context_pct,model,"
                "measurement_source,project_scope_id,repo_group_id) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    session.id,
                    session.native_session_id,
                    session.backend,
                    session.name,
                    session.cwd,
                    session.space_id,
                    session.created_at,
                    session.tokens_in,
                    session.tokens_out,
                    transcript,
                    session.exe,
                    json.dumps(session.args),
                    int(session.pinned_attention),
                    session.shell_profile_id,
                    int(session.backend in {"claude", "codex"}),
                    session.project_id,
                    session.project_label,
                    session.project_root,
                    session.context_window or None,
                    session.context_pct if session.context_window else None,
                    session.context_peak_pct if session.context_window else None,
                    session.model,
                    session.measurement_source,
                    session.project_scope_id or session.project_id,
                    session.repo_group_id,
                ),
            )
            self._db.commit()

    async def update_session_metadata(self, session: SessionRecord) -> None:
        """Persist mutable metadata from a live session as one atomic update."""
        async with self._lock:
            self._db.execute(
                "UPDATE history SET name=?,cwd=?,space_id=?,executable=?,argv_json=?,"
                "pinned_attention=?,shell_profile_id=? WHERE id=?",
                (
                    session.name,
                    session.cwd,
                    session.space_id,
                    session.exe,
                    json.dumps(session.args),
                    int(session.pinned_attention),
                    session.shell_profile_id,
                    session.id,
                ),
            )
            self._db.commit()

    async def session_ended(self, session: SessionRecord, reason: str) -> None:
        async with self._lock:
            row = self._db.execute(
                "SELECT agent_visible FROM history WHERE id=?", (session.id,)
            ).fetchone()
            if row and not row["agent_visible"]:
                self._db.execute("DELETE FROM history WHERE id=?", (session.id,))
                self._db.commit()
                return
            self._db.execute(
                "UPDATE history SET exited_at=?,exit_reason=?,tokens_in=?,tokens_out=?,"
                "name=?,cwd=?,space_id=?,executable=?,argv_json=?,pinned_attention=?,"
                "shell_profile_id=?,final_state=?,context_window=COALESCE(?,context_window),"
                "final_context_pct=COALESCE(?,final_context_pct),"
                "peak_context_pct=COALESCE(?,peak_context_pct),model=COALESCE(?,model),"
                "measurement_source=COALESCE(?,measurement_source) WHERE id=?",
                (
                    session.last_activity_ts,
                    reason,
                    session.tokens_in,
                    session.tokens_out,
                    session.name,
                    session.cwd,
                    session.space_id,
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
                    session.id,
                ),
            )
            self._db.commit()

    async def update_agent_summary(self, session: SessionRecord) -> None:
        if not session.context_window:
            return
        async with self._lock:
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

    async def session_promoted(self, session: SessionRecord, transcript: str) -> None:
        async with self._lock:
            self._db.execute(
                "DELETE FROM history WHERE external=1 AND backend=? AND native_id=?",
                (session.backend, session.native_session_id),
            )
            run_id = session.agent_run_id or session.id
            self._db.execute(
                "INSERT INTO history"
                "(id,native_id,backend,name,cwd,space_id,spawned_at,tokens_in,tokens_out,"
                "transcript_path,executable,argv_json,pinned_attention,shell_profile_id,"
                "agent_visible,project_id,project_label,project_root,context_window,"
                "final_context_pct,peak_context_pct,model,measurement_source,"
                "project_scope_id,repo_group_id) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET native_id=excluded.native_id,"
                "backend=excluded.backend,name=excluded.name,cwd=excluded.cwd,"
                "space_id=excluded.space_id,transcript_path=excluded.transcript_path,"
                "agent_visible=1,project_id=excluded.project_id,"
                "project_label=excluded.project_label,project_root=excluded.project_root,"
                "project_scope_id=excluded.project_scope_id,repo_group_id=excluded.repo_group_id",
                (
                    run_id,
                    session.native_session_id,
                    session.backend,
                    session.name,
                    session.run_cwd or session.cwd,
                    session.space_id,
                    session.agent_run_started_at or session.created_at,
                    session.tokens_in,
                    session.tokens_out,
                    transcript,
                    session.exe,
                    json.dumps(session.args),
                    int(session.pinned_attention),
                    session.shell_profile_id,
                    1,
                    session.project_id,
                    session.project_label,
                    session.project_root,
                    session.context_window or None,
                    session.context_pct if session.context_window else None,
                    session.context_peak_pct if session.context_window else None,
                    session.model,
                    session.measurement_source,
                    session.project_scope_id,
                    session.repo_group_id,
                ),
            )
            self._db.commit()

    async def agent_run_ended(self, session: SessionRecord, reason: str) -> None:
        run_id = session.agent_run_id
        if not run_id:
            return
        async with self._lock:
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
    ) -> None:
        """Index a native CLI transcript without claiming ownership of the file."""
        async with self._lock:
            exists = self._db.execute(
                "SELECT 1 FROM history WHERE backend=? AND native_id=? AND external=0",
                (backend, native_id),
            ).fetchone()
            if not exists:
                self._db.execute(
                    "INSERT INTO history"
                    "(id,native_id,backend,name,cwd,spawned_at,transcript_path,external,"
                    "agent_visible,project_id,project_label,project_root,context_window,"
                    "final_context_pct,peak_context_pct,tokens_in,tokens_out,model,"
                    "measurement_source,project_scope_id,repo_group_id) "
                    "VALUES(?,?,?,?,?,?,?,1,1,?,?,?,?,?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(id) DO UPDATE SET "
                    "name=excluded.name,cwd=excluded.cwd,spawned_at=excluded.spawned_at,"
                    "transcript_path=excluded.transcript_path,project_id=excluded.project_id,"
                    "project_label=excluded.project_label,project_root=excluded.project_root,"
                    "context_window=excluded.context_window,"
                    "final_context_pct=excluded.final_context_pct,"
                    "peak_context_pct=excluded.peak_context_pct,tokens_in=excluded.tokens_in,"
                    "tokens_out=excluded.tokens_out,model=excluded.model,"
                    "measurement_source=excluded.measurement_source,"
                    "project_scope_id=excluded.project_scope_id,repo_group_id=excluded.repo_group_id",
                    (
                        row_id,
                        native_id,
                        backend,
                        name,
                        cwd,
                        spawned_at,
                        transcript_path,
                        project_id,
                        project_label,
                        project_root,
                        context_window,
                        final_context_pct,
                        peak_context_pct,
                        tokens_in,
                        tokens_out,
                        model,
                        measurement_source,
                        project_scope_id or project_id,
                        repo_group_id,
                    ),
                )
                self._db.commit()

    async def append_event(self, event: MuxEvent) -> int:
        async with self._lock:
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

    async def history(
        self, query: str = "", backend: str | None = None, limit: int = 200
    ) -> list[dict[str, Any]]:
        sql = (
            "SELECT * FROM history WHERE agent_visible=1 AND backend IN ('claude','codex') "
            "AND (name LIKE ? OR cwd LIKE ? OR COALESCE(project_label,'') LIKE ?)"
        )
        args: list[Any] = [f"%{query}%", f"%{query}%", f"%{query}%"]
        if backend:
            sql += " AND backend=?"
            args.append(backend)
        sql += " ORDER BY spawned_at DESC LIMIT ?"
        args.append(limit)
        async with self._lock:
            rows = self._db.execute(sql, args).fetchall()
        return [dict(r) for r in rows]

    async def update_project_label(self, project_id: str, label: str) -> None:
        async with self._lock:
            self._db.execute(
                "UPDATE history SET project_label=? WHERE project_scope_id=?",
                (label, project_id),
            )
            self._db.commit()

    async def history_page(
        self,
        *,
        query: str = "",
        backend: str | None = None,
        project: str | None = None,
        state: str | None = None,
        space: str | None = None,
        external: bool | None = None,
        date_from: float | None = None,
        date_to: float | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        limit = max(1, min(limit, 200))
        sql = (
            "SELECT * FROM history WHERE agent_visible=1 AND backend IN ('claude','codex') "
            "AND (name LIKE ? OR cwd LIKE ? OR COALESCE(project_label,'') LIKE ?)"
        )
        args: list[Any] = [f"%{query}%", f"%{query}%", f"%{query}%"]
        filters = {
            "backend": backend,
            "final_state": state,
            "space_id": space,
        }
        for column, value in filters.items():
            if value:
                sql += f" AND {column}=?"
                args.append(value)
        if project == "__ungrouped__":
            sql += " AND project_scope_id IS NULL"
        elif project:
            sql += " AND project_scope_id=?"
            args.append(project)
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
        async with self._lock:
            rows = self._db.execute(sql, args).fetchall()
        has_more = len(rows) > limit
        page = rows[:limit]
        items = [dict(row) for row in page]
        next_cursor = f"{page[-1]['spawned_at']}:{page[-1]['id']}" if has_more and page else None
        return {"items": items, "next_cursor": next_cursor}

    async def history_projects(self) -> list[dict[str, Any]]:
        async with self._lock:
            rows = self._db.execute(
                "SELECT project_scope_id AS project_id,"
                "COALESCE(MAX(project_label),'Ungrouped') AS label,"
                "MAX(project_root) AS root,COUNT(*) AS sessions,MAX(spawned_at) AS last_activity "
                "FROM history WHERE agent_visible=1 AND backend IN ('claude','codex') "
                "GROUP BY project_scope_id ORDER BY last_activity DESC"
            ).fetchall()
        return [dict(row) for row in rows]

    async def delete_history_entry(self, session_id: str) -> bool:
        async with self._lock:
            cursor = self._db.execute(
                "DELETE FROM history WHERE id=? AND agent_visible=1", (session_id,)
            )
            self._db.commit()
        return bool(cursor.rowcount)

    async def history_entry(self, session_id: str) -> dict[str, Any] | None:
        async with self._lock:
            row = self._db.execute("SELECT * FROM history WHERE id=?", (session_id,)).fetchone()
        return dict(row) if row else None

    async def register_project_scope(self, project: ProjectIdentity) -> dict[str, Any]:
        """Persist a concrete scope only after an operation actually uses it."""
        now = time.time()
        async with self._lock:
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

    async def project_scope(self, scope_id: str) -> dict[str, Any] | None:
        async with self._lock:
            row = self._db.execute(
                "SELECT s.*,g.label AS repo_group_label FROM project_scopes s "
                "LEFT JOIN repo_groups g ON g.id=s.repo_group_id WHERE s.id=?",
                (scope_id,),
            ).fetchone()
        return dict(row) if row else None

    async def project_scopes(self, *, include_hidden: bool = False) -> list[dict[str, Any]]:
        hidden = "" if include_hidden else "WHERE s.hidden=0"
        async with self._lock:
            rows = self._db.execute(
                f"SELECT s.*,g.label AS repo_group_label,"  # noqa: S608 -- fixed clause
                "(SELECT COUNT(*) FROM history h WHERE h.project_scope_id=s.id) AS history_count,"
                "(SELECT COUNT(*) FROM artifacts a WHERE a.project_scope_id=s.id) "
                "AS artifact_count "
                "FROM project_scopes s LEFT JOIN repo_groups g ON g.id=s.repo_group_id "
                f"{hidden} ORDER BY s.last_activity DESC,s.label"
            ).fetchall()
        return [dict(row) for row in rows]

    async def set_project_hidden(self, scope_id: str, hidden: bool) -> bool:
        async with self._lock:
            cursor = self._db.execute(
                "UPDATE project_scopes SET hidden=? WHERE id=?", (int(hidden), scope_id)
            )
            self._db.commit()
        return bool(cursor.rowcount)

    async def project_blockers(self, scope_id: str) -> dict[str, int]:
        async with self._lock:
            row = self._db.execute(
                "SELECT (SELECT COUNT(*) FROM history WHERE project_scope_id=?) history,"
                "(SELECT COUNT(*) FROM artifacts WHERE project_scope_id=?) artifacts",
                (scope_id, scope_id),
            ).fetchone()
        return dict(row)

    async def forget_project_scope(self, scope_id: str) -> dict[str, Any]:
        blockers = await self.project_blockers(scope_id)
        if any(blockers.values()):
            return {"forgotten": False, "blockers": blockers}
        async with self._lock:
            cursor = self._db.execute("DELETE FROM project_scopes WHERE id=?", (scope_id,))
            self._db.commit()
        return {"forgotten": bool(cursor.rowcount), "blockers": blockers}

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
        async with self._lock:
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

    async def artifacts(self, scope_id: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM artifacts"
        args: tuple[Any, ...] = ()
        if scope_id:
            sql += " WHERE project_scope_id=?"
            args = (scope_id,)
        sql += " ORDER BY updated_at DESC"
        async with self._lock:
            rows = self._db.execute(sql, args).fetchall()
        return [dict(row) for row in rows]

    async def delete_artifact_binding(self, artifact_id: str) -> bool:
        """Remove only the mux index entry; the user-owned file is never deleted."""
        async with self._lock:
            cursor = self._db.execute("DELETE FROM artifacts WHERE id=?", (artifact_id,))
            self._db.commit()
        return bool(cursor.rowcount)

    async def move_artifact_scope(
        self, artifact_id: str, scope_id: str, relative_path: str
    ) -> bool:
        async with self._lock:
            cursor = self._db.execute(
                "UPDATE artifacts SET project_scope_id=?,relative_path=?,updated_at=?,"
                "placement_acknowledged_scope_id=NULL WHERE id=?",
                (scope_id, relative_path, time.time(), artifact_id),
            )
            self._db.commit()
        return bool(cursor.rowcount)

    async def acknowledge_artifact_placement(self, artifact_id: str, anchor_scope_id: str) -> bool:
        async with self._lock:
            cursor = self._db.execute(
                "UPDATE artifacts SET placement_acknowledged_scope_id=?,updated_at=? WHERE id=?",
                (anchor_scope_id, time.time(), artifact_id),
            )
            self._db.commit()
        return bool(cursor.rowcount)

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
        async with self._lock:
            rows = self._db.execute(sql, args).fetchall()
        return [{**dict(r), "payload": json.loads(r["payload_json"])} for r in rows]

    def close(self) -> None:
        self._db.close()
