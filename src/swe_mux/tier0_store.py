from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
import time
import uuid
from collections.abc import Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, TypeVar

from .event_bus import EventBus
from .models import MuxEvent
from .sqlite_store import database_operation_lock, run_sqlite_operation

T = TypeVar("T")

TIER0_SCHEMA_VERSION = 1

# Deterministic, no-model facts. Every derived control-plane feature reads from
# this. `source_seq` is the pointer back into the immutable event log / raw store
# so a fact can always be rehydrated to its origin. See CONTROL_PLANE_IDEAS.md §5.

SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS tier0_facts (
  id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL,
  agent_run_id TEXT,
  project_id TEXT,
  kind TEXT NOT NULL,
  target TEXT,
  content_hash TEXT,
  fingerprint TEXT,
  detail_json TEXT NOT NULL DEFAULT '{}',
  source_seq INTEGER,
  source_ref TEXT,
  created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tier0_session ON tier0_facts(session_id,created_at DESC);
CREATE INDEX IF NOT EXISTS idx_tier0_kind ON tier0_facts(kind,created_at DESC);
CREATE INDEX IF NOT EXISTS idx_tier0_hash ON tier0_facts(content_hash);
CREATE INDEX IF NOT EXISTS idx_tier0_fingerprint ON tier0_facts(session_id,fingerprint);
CREATE INDEX IF NOT EXISTS idx_tier0_retention ON tier0_facts(created_at);
"""

# Event types that carry a deterministic fact worth recording. Everything else is
# ignored so the capture path stays cheap.
_CAPTURED_EVENTS = {"tool_use", "tool_result", "git_changed", "context_compacted"}
# Keys that commonly hold the target of a tool action across normalized payloads.
_TARGET_KEYS = ("path", "file", "file_path", "command", "cmd", "url", "target")
# Bounded detail so a runaway payload cannot bloat a row.
_MAX_DETAIL_BYTES = 4096
_MAX_TARGET_CHARS = 512


def _fact_from_event(event: MuxEvent) -> dict[str, Any] | None:
    """Extract one deterministic fact from a normalized event, or None.

    Pure and side-effect free so it is trivially testable. Content hashing of
    files is deliberately not done here — reading file bytes on the event path is
    the one heavy operation flagged in the design; the provenance builder computes
    hashes off-loop. This records the fact and a source pointer.
    """
    if event.type not in _CAPTURED_EVENTS:
        return None
    payload = event.payload or {}
    kind = event.type
    if event.type == "tool_use":
        tool = str(payload.get("tool") or payload.get("name") or "tool")
        kind = _classify_tool(tool)
    elif event.type == "git_changed":
        kind = "git"
    elif event.type == "context_compacted":
        kind = "compaction"
    # The adapter emits a normalized target/content hash (observation.tool_call_evidence);
    # fall back to scanning common keys for events that predate it.
    target: str | None = None
    explicit_target = payload.get("target")
    if isinstance(explicit_target, str) and explicit_target.strip():
        target = explicit_target.strip()[:_MAX_TARGET_CHARS]
    else:
        for key in _TARGET_KEYS:
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                target = value.strip()[:_MAX_TARGET_CHARS]
                break
    content_hash = payload.get("content_hash")
    if not isinstance(content_hash, str) or not content_hash:
        content_hash = None
    detail = json.dumps(payload, default=str)[:_MAX_DETAIL_BYTES]
    fingerprint = _fingerprint(event.type, kind, target, payload, content_hash)
    return {
        "session_id": event.session_id or "",
        "kind": kind,
        "target": target,
        "content_hash": content_hash,
        "fingerprint": fingerprint,
        "detail_json": detail,
        "source_seq": event.seq or None,
    }


def _classify_tool(tool: str) -> str:
    name = tool.strip().casefold()
    if any(token in name for token in ("write", "edit", "create", "patch", "apply")):
        return "file_write"
    if any(token in name for token in ("read", "cat", "view", "open")):
        return "file_read"
    if any(token in name for token in ("bash", "shell", "exec", "run", "command", "terminal")):
        return "command"
    if any(token in name for token in ("test", "pytest", "jest")):
        return "test"
    return "tool"


def _fingerprint(
    event_type: str,
    kind: str,
    target: str | None,
    payload: dict[str, Any],
    content_hash: str | None = None,
) -> str:
    """A canonical action fingerprint for loop detection.

    Strips volatile detail (timestamps, exact output) and keys on the semantic
    shape of the action: what kind, against what target, with what exit class,
    and — when the adapter captured it — the content written. Identical repeated
    edits share a fingerprint (a loop signal); changed content differs (progress).
    """
    exit_class = ""
    for key in ("exit_code", "exit", "status", "success"):
        if key in payload:
            exit_class = str(payload[key])
            break
    basis = "\x1f".join(
        (event_type, kind, (target or "").casefold(), exit_class, content_hash or "")
    )
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]


class Tier0Store:
    """Durable deterministic fact capture (Tier 0 substrate)."""

    def __init__(self, path: Path, *, retention_days: int = 30) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.retention_days = retention_days
        self._operation_lock = database_operation_lock(path)
        self._closed = False
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mux-tier0-db")
        self._executor.submit(self._connect).result()
        self._event_task: asyncio.Task[None] | None = None
        self._event_queue: asyncio.Queue[MuxEvent] | None = None
        self._event_bus: EventBus | None = None
        self._resolve_enabled: Callable[[str], Awaitable[bool]] | None = None

    def _connect(self) -> None:
        with self._operation_lock:
            self._db = sqlite3.connect(self.path, check_same_thread=False)
            self._db.row_factory = sqlite3.Row
            self._db.execute("PRAGMA synchronous=NORMAL")
            self._db.execute("PRAGMA busy_timeout=5000")
            self._db.executescript(SCHEMA)
            self._db.execute(f"PRAGMA user_version={TIER0_SCHEMA_VERSION}")
            self._db.commit()

    async def _run(self, fn: Callable[[], T]) -> T:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self._executor, run_sqlite_operation, self._db, self._operation_lock, fn
        )

    async def record_fact(
        self,
        *,
        session_id: str,
        kind: str,
        target: str | None = None,
        content_hash: str | None = None,
        fingerprint: str | None = None,
        detail: dict[str, Any] | None = None,
        source_seq: int | None = None,
        source_ref: str | None = None,
        agent_run_id: str | None = None,
        project_id: str | None = None,
        created_at: float | None = None,
    ) -> str:
        fact_id = uuid.uuid4().hex
        detail_json = json.dumps(detail or {}, default=str)[:_MAX_DETAIL_BYTES]
        ts = created_at if created_at is not None else time.time()

        def op() -> str:
            self._db.execute(
                "INSERT INTO tier0_facts(id,session_id,agent_run_id,project_id,kind,target,"
                "content_hash,fingerprint,detail_json,source_seq,source_ref,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    fact_id,
                    session_id,
                    agent_run_id,
                    project_id,
                    kind,
                    target,
                    content_hash,
                    fingerprint,
                    detail_json,
                    source_seq,
                    source_ref,
                    ts,
                ),
            )
            self._db.commit()
            return fact_id

        return await self._run(op)

    async def record_from_event(
        self, event: MuxEvent, *, agent_run_id: str | None = None, project_id: str | None = None
    ) -> str | None:
        fact = _fact_from_event(event)
        if fact is None or not fact["session_id"]:
            return None
        return await self.record_fact(
            session_id=fact["session_id"],
            kind=fact["kind"],
            target=fact["target"],
            content_hash=fact["content_hash"],
            fingerprint=fact["fingerprint"],
            detail=json.loads(fact["detail_json"]) if fact["detail_json"] else None,
            source_seq=fact["source_seq"],
            agent_run_id=agent_run_id,
            project_id=project_id,
            created_at=event.ts,
        )

    async def recent_facts(self, session_id: str, *, limit: int = 200) -> list[dict[str, Any]]:
        def op() -> list[dict[str, Any]]:
            rows = self._db.execute(
                "SELECT * FROM tier0_facts WHERE session_id=? ORDER BY created_at DESC LIMIT ?",
                (session_id, max(1, min(limit, 2000))),
            ).fetchall()
            return [dict(row) for row in rows]

        return await self._run(op)

    async def prune(self, *, retention_days: int | None = None) -> int:
        days = retention_days if retention_days is not None else self.retention_days
        cutoff = time.time() - max(1, days) * 86400

        def op() -> int:
            cursor = self._db.execute("DELETE FROM tier0_facts WHERE created_at<?", (cutoff,))
            self._db.commit()
            return cursor.rowcount

        return await self._run(op)

    def start(
        self, events: EventBus, *, resolve_enabled: Callable[[str], Awaitable[bool]]
    ) -> None:
        """Begin gated capture. `resolve_enabled` decides per session whether the
        owning project opted Tier 0 in — the enablement gate (CONTROL_PLANE_IDEAS §8)."""
        self._event_bus = events
        self._resolve_enabled = resolve_enabled
        self._event_queue = events.subscribe()
        self._event_task = asyncio.create_task(self._consume_events(), name="tier0-capture")

    async def _consume_events(self) -> None:
        assert self._event_queue is not None
        assert self._resolve_enabled is not None
        while True:
            event = await self._event_queue.get()
            try:
                if event.type in _CAPTURED_EVENTS and event.session_id:
                    if await self._resolve_enabled(event.session_id):
                        await self.record_from_event(event)
            except Exception:  # noqa: BLE001 - capture must never break the event loop
                pass
            finally:
                self._event_queue.task_done()

    async def stop(self) -> None:
        if self._event_task:
            self._event_task.cancel()
            await asyncio.gather(self._event_task, return_exceptions=True)
            self._event_task = None
        if self._event_bus and self._event_queue:
            self._event_bus.unsubscribe(self._event_queue)
        self._event_bus = None
        self._event_queue = None
        self._resolve_enabled = None

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._executor.submit(self._db.close).result()
        self._executor.shutdown(wait=True)
