"""Durable plugin registry and bounded invocation ledger."""

from __future__ import annotations

import asyncio
import builtins
import json
import sqlite3
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from .sqlite_store import (
    connect_or_quarantine,
    database_operation_lock,
    run_sqlite_operation,
    write_schema_version,
)

PLUGIN_SCHEMA_VERSION = 1
PLUGIN_LOG_LIMIT = 1000
PLUGIN_SCHEMA = """
CREATE TABLE IF NOT EXISTS plugins(
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  version TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 0,
  lifecycle TEXT NOT NULL,
  source_kind TEXT NOT NULL,
  source_ref TEXT NOT NULL DEFAULT '',
  resolved_ref TEXT NOT NULL DEFAULT '',
  root TEXT NOT NULL,
  manifest_path TEXT NOT NULL,
  manifest_digest TEXT NOT NULL,
  content_digest TEXT NOT NULL DEFAULT '',
  security_digest TEXT NOT NULL,
  approved_digest TEXT NOT NULL DEFAULT '',
  previous_root TEXT NOT NULL DEFAULT '',
  diagnostic TEXT NOT NULL DEFAULT '',
  installed_at REAL NOT NULL,
  updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS plugin_command_logs(
  id TEXT PRIMARY KEY,
  plugin_id TEXT NOT NULL,
  contribution_kind TEXT NOT NULL,
  contribution_id TEXT NOT NULL,
  invocation_source TEXT NOT NULL,
  correlation_id TEXT NOT NULL,
  context_json TEXT NOT NULL DEFAULT '{}',
  started_at REAL NOT NULL,
  finished_at REAL,
  outcome TEXT NOT NULL,
  exit_code INTEGER,
  duration_ms REAL,
  stdout TEXT NOT NULL DEFAULT '',
  stderr TEXT NOT NULL DEFAULT '',
  stdout_truncated INTEGER NOT NULL DEFAULT 0,
  stderr_truncated INTEGER NOT NULL DEFAULT 0,
  diagnostic TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS plugin_logs_plugin ON plugin_command_logs(plugin_id, started_at DESC);
CREATE TABLE IF NOT EXISTS plugin_settings(
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
"""


def _connect(path: Path) -> sqlite3.Connection:
    db = sqlite3.connect(path, check_same_thread=False)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA synchronous=NORMAL")
    db.execute("PRAGMA busy_timeout=5000")
    db.executescript(PLUGIN_SCHEMA)
    write_schema_version(db, "plugins", PLUGIN_SCHEMA_VERSION)
    db.commit()
    return db


def _plugin(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "name": str(row["name"]),
        "version": str(row["version"]),
        "enabled": bool(row["enabled"]),
        "lifecycle": str(row["lifecycle"]),
        "source_kind": str(row["source_kind"]),
        "source_ref": str(row["source_ref"]),
        "resolved_ref": str(row["resolved_ref"]),
        "root": str(row["root"]),
        "manifest_path": str(row["manifest_path"]),
        "manifest_digest": str(row["manifest_digest"]),
        "content_digest": str(row["content_digest"]),
        "security_digest": str(row["security_digest"]),
        "approved_digest": str(row["approved_digest"]),
        "previous_root": str(row["previous_root"]),
        "diagnostic": str(row["diagnostic"]),
        "installed_at": float(row["installed_at"]),
        "updated_at": float(row["updated_at"]),
    }


def _log(row: sqlite3.Row) -> dict[str, Any]:
    result = {key: row[key] for key in row.keys()}
    result["stdout_truncated"] = bool(result["stdout_truncated"])
    result["stderr_truncated"] = bool(result["stderr_truncated"])
    try:
        result["context"] = json.loads(str(result.pop("context_json")))
    except json.JSONDecodeError:
        result["context"] = {}
    return result


class PluginStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="plugin-store")
        self._lock = database_operation_lock(path)
        self._db = connect_or_quarantine(path, lambda: _connect(path))

    async def _run[T](self, operation: Callable[[], T]) -> T:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self._executor, lambda: run_sqlite_operation(self._db, self._lock, operation)
        )

    async def close(self) -> None:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(self._executor, self._db.close)
        self._executor.shutdown(wait=True)

    async def list(self) -> builtins.list[dict[str, Any]]:
        return await self._run(
            lambda: [
                _plugin(row)
                for row in self._db.execute("SELECT * FROM plugins ORDER BY name,id").fetchall()
            ]
        )

    async def get(self, plugin_id: str) -> dict[str, Any] | None:
        def op() -> dict[str, Any] | None:
            row = self._db.execute("SELECT * FROM plugins WHERE id=?", (plugin_id,)).fetchone()
            return _plugin(row) if row else None

        return await self._run(op)

    async def put(self, record: dict[str, Any]) -> dict[str, Any]:
        now = time.time()

        def op() -> dict[str, Any]:
            previous = self._db.execute(
                "SELECT installed_at FROM plugins WHERE id=?", (record["id"],)
            ).fetchone()
            self._db.execute(
                """INSERT INTO plugins(
                id,name,version,enabled,lifecycle,source_kind,source_ref,resolved_ref,
                root,manifest_path,manifest_digest,content_digest,security_digest,approved_digest,
                previous_root,diagnostic,installed_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                name=excluded.name,version=excluded.version,enabled=excluded.enabled,lifecycle=excluded.lifecycle,
                source_kind=excluded.source_kind,source_ref=excluded.source_ref,resolved_ref=excluded.resolved_ref,
                root=excluded.root,manifest_path=excluded.manifest_path,manifest_digest=excluded.manifest_digest,
                content_digest=excluded.content_digest,
                security_digest=excluded.security_digest,approved_digest=excluded.approved_digest,
                previous_root=excluded.previous_root,diagnostic=excluded.diagnostic,updated_at=excluded.updated_at""",
                (
                    record["id"],
                    record["name"],
                    record["version"],
                    int(record.get("enabled", False)),
                    record["lifecycle"],
                    record["source_kind"],
                    record.get("source_ref", ""),
                    record.get("resolved_ref", ""),
                    record["root"],
                    record["manifest_path"],
                    record["manifest_digest"],
                    record.get("content_digest", ""),
                    record["security_digest"],
                    record.get("approved_digest", ""),
                    record.get("previous_root", ""),
                    record.get("diagnostic", ""),
                    float(previous[0]) if previous else now,
                    now,
                ),
            )
            self._db.commit()
            row = self._db.execute("SELECT * FROM plugins WHERE id=?", (record["id"],)).fetchone()
            assert row is not None
            return _plugin(row)

        return await self._run(op)

    async def set_state(self, plugin_id: str, **changes: Any) -> dict[str, Any] | None:
        allowed = {
            "enabled",
            "lifecycle",
            "approved_digest",
            "diagnostic",
            "previous_root",
            "root",
            "manifest_path",
            "manifest_digest",
            "content_digest",
            "security_digest",
            "name",
            "version",
            "resolved_ref",
        }
        fields = [
            (key, int(value) if key == "enabled" else value)
            for key, value in changes.items()
            if key in allowed
        ]
        if not fields:
            return await self.get(plugin_id)

        def op() -> dict[str, Any] | None:
            sql = ",".join(f"{key}=?" for key, _ in fields) + ",updated_at=?"
            self._db.execute(
                f"UPDATE plugins SET {sql} WHERE id=?",
                (*[value for _, value in fields], time.time(), plugin_id),
            )
            self._db.commit()
            row = self._db.execute("SELECT * FROM plugins WHERE id=?", (plugin_id,)).fetchone()
            return _plugin(row) if row else None

        return await self._run(op)

    async def remove(self, plugin_id: str) -> dict[str, Any] | None:
        def op() -> dict[str, Any] | None:
            row = self._db.execute("SELECT * FROM plugins WHERE id=?", (plugin_id,)).fetchone()
            if row:
                self._db.execute("DELETE FROM plugins WHERE id=?", (plugin_id,))
                self._db.commit()
            return _plugin(row) if row else None

        return await self._run(op)

    async def execution_enabled(self) -> bool:
        def op() -> bool:
            row = self._db.execute(
                "SELECT value FROM plugin_settings WHERE key='execution_enabled'"
            ).fetchone()
            return row is None or str(row[0]).lower() == "true"

        return await self._run(op)

    async def set_execution_enabled(self, enabled: bool) -> None:
        def op() -> None:
            self._db.execute(
                "INSERT INTO plugin_settings(key,value) VALUES('execution_enabled',?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                ("true" if enabled else "false",),
            )
            self._db.commit()

        await self._run(op)

    async def log_started(self, record: dict[str, Any]) -> None:
        def op() -> None:
            self._db.execute(
                "INSERT INTO plugin_command_logs("
                "id,plugin_id,contribution_kind,contribution_id,invocation_source,"
                "correlation_id,context_json,started_at,outcome) VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    record["id"],
                    record["plugin_id"],
                    record["contribution_kind"],
                    record["contribution_id"],
                    record["invocation_source"],
                    record["correlation_id"],
                    json.dumps(record.get("context", {}), separators=(",", ":")),
                    record["started_at"],
                    "running",
                ),
            )
            self._db.commit()

        await self._run(op)

    async def log_finished(self, log_id: str, **result: Any) -> None:
        def op() -> None:
            self._db.execute(
                "UPDATE plugin_command_logs SET finished_at=?,outcome=?,exit_code=?,"
                "duration_ms=?,stdout=?,stderr=?,stdout_truncated=?,stderr_truncated=?,"
                "diagnostic=? WHERE id=?",
                (
                    time.time(),
                    result.get("outcome", "failed"),
                    result.get("exit_code"),
                    result.get("duration_ms"),
                    result.get("stdout", ""),
                    result.get("stderr", ""),
                    int(result.get("stdout_truncated", False)),
                    int(result.get("stderr_truncated", False)),
                    result.get("diagnostic", ""),
                    log_id,
                ),
            )
            self._db.execute(
                "DELETE FROM plugin_command_logs WHERE id IN (SELECT id FROM "
                "plugin_command_logs ORDER BY started_at DESC LIMIT -1 OFFSET ?)",
                (PLUGIN_LOG_LIMIT,),
            )
            self._db.commit()

        await self._run(op)

    async def logs(
        self, plugin_id: str | None = None, limit: int = 100
    ) -> builtins.list[dict[str, Any]]:
        bounded = max(1, min(int(limit), 500))

        def op() -> builtins.list[dict[str, Any]]:
            if plugin_id:
                rows = self._db.execute(
                    "SELECT * FROM plugin_command_logs WHERE plugin_id=? "
                    "ORDER BY started_at DESC LIMIT ?",
                    (plugin_id, bounded),
                ).fetchall()
            else:
                rows = self._db.execute(
                    "SELECT * FROM plugin_command_logs ORDER BY started_at DESC LIMIT ?", (bounded,)
                ).fetchall()
            return [_log(row) for row in rows]

        return await self._run(op)
