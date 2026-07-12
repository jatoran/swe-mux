from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path
from typing import Any

from .models import MuxEvent, SessionRecord, SpaceRecord

SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS spaces (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, position INTEGER NOT NULL,
  layout_json TEXT, default_cwd TEXT, default_backend TEXT
);
CREATE TABLE IF NOT EXISTS history (
  id TEXT PRIMARY KEY, native_id TEXT NOT NULL, backend TEXT NOT NULL,
  name TEXT NOT NULL, cwd TEXT NOT NULL, space_id TEXT,
  spawned_at REAL NOT NULL, exited_at REAL, exit_reason TEXT,
  tokens_in INTEGER NOT NULL DEFAULT 0, tokens_out INTEGER NOT NULL DEFAULT 0,
  transcript_path TEXT, external INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS events (
  seq INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, session_id TEXT,
  source TEXT NOT NULL, type TEXT NOT NULL, payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS links (
  session_a TEXT NOT NULL, session_b TEXT NOT NULL, mode TEXT NOT NULL,
  created_at REAL NOT NULL, PRIMARY KEY(session_a, session_b)
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
        self._db.commit()
        self._lock = asyncio.Lock()

    async def ensure_default_space(self, space: SpaceRecord) -> None:
        async with self._lock:
            self._db.execute(
                "INSERT OR IGNORE INTO spaces(id,name,position,layout_json) VALUES(?,?,?,?)",
                (space.id, space.name, space.position, json.dumps(space.layout)),
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
            )
            for r in rows
        ]

    async def upsert_space(self, space: SpaceRecord) -> None:
        async with self._lock:
            self._db.execute(
                "INSERT INTO spaces VALUES(?,?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET name=excluded.name, "
                "position=excluded.position, layout_json=excluded.layout_json, "
                "default_cwd=excluded.default_cwd, "
                "default_backend=excluded.default_backend",
                (
                    space.id,
                    space.name,
                    space.position,
                    json.dumps(space.layout),
                    space.default_cwd,
                    space.default_backend,
                ),
            )
            self._db.commit()

    async def delete_space(self, space_id: str) -> None:
        async with self._lock:
            self._db.execute("DELETE FROM spaces WHERE id=?", (space_id,))
            self._db.commit()

    async def session_started(self, session: SessionRecord, transcript: str | None) -> None:
        async with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO history"
                "(id,native_id,backend,name,cwd,space_id,spawned_at,"
                "tokens_in,tokens_out,transcript_path) VALUES(?,?,?,?,?,?,?,?,?,?)",
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
                ),
            )
            self._db.commit()

    async def session_ended(self, session: SessionRecord, reason: str) -> None:
        async with self._lock:
            self._db.execute(
                "UPDATE history SET exited_at=?,exit_reason=?,tokens_in=?,tokens_out=? WHERE id=?",
                (
                    session.last_activity_ts,
                    reason,
                    session.tokens_in,
                    session.tokens_out,
                    session.id,
                ),
            )
            self._db.commit()

    async def session_promoted(self, session: SessionRecord, transcript: str) -> None:
        async with self._lock:
            self._db.execute(
                "DELETE FROM history WHERE external=1 AND backend=? AND native_id=?",
                (session.backend, session.native_session_id),
            )
            self._db.execute(
                "UPDATE history SET native_id=?,backend=?,name=?,transcript_path=? WHERE id=?",
                (
                    session.native_session_id,
                    session.backend,
                    session.name,
                    transcript,
                    session.id,
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
                    "(id,native_id,backend,name,cwd,spawned_at,transcript_path,external) "
                    "VALUES(?,?,?,?,?,?,?,1) ON CONFLICT(id) DO UPDATE SET "
                    "name=excluded.name,cwd=excluded.cwd,spawned_at=excluded.spawned_at,"
                    "transcript_path=excluded.transcript_path",
                    (row_id, native_id, backend, name, cwd, spawned_at, transcript_path),
                )
                self._db.commit()

    async def append_event(self, event: MuxEvent) -> None:
        async with self._lock:
            self._db.execute(
                "INSERT INTO events(ts,session_id,source,type,payload_json) VALUES(?,?,?,?,?)",
                (event.ts, event.session_id, event.source, event.type, json.dumps(event.payload)),
            )
            self._db.commit()

    async def history(
        self, query: str = "", backend: str | None = None, limit: int = 200
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM history WHERE (name LIKE ? OR cwd LIKE ?)"
        args: list[Any] = [f"%{query}%", f"%{query}%"]
        if backend:
            sql += " AND backend=?"
            args.append(backend)
        sql += " ORDER BY spawned_at DESC LIMIT ?"
        args.append(limit)
        async with self._lock:
            rows = self._db.execute(sql, args).fetchall()
        return [dict(r) for r in rows]

    async def history_entry(self, session_id: str) -> dict[str, Any] | None:
        async with self._lock:
            row = self._db.execute("SELECT * FROM history WHERE id=?", (session_id,)).fetchone()
        return dict(row) if row else None

    async def events(
        self, since: float = 0, session_id: str | None = None, limit: int = 500
    ) -> list[dict[str, Any]]:
        sql = "SELECT seq,ts,session_id,source,type,payload_json FROM events WHERE ts>?"
        args: list[Any] = [since]
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
