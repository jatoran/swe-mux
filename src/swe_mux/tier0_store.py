from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import sqlite3
import time
import uuid
from collections.abc import Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

from .event_bus import EventBus
from .models import MuxEvent
from .sqlite_store import (
    connect_or_quarantine,
    database_operation_lock,
    run_sqlite_operation,
    write_schema_version,
)

log = logging.getLogger(__name__)

T = TypeVar("T")

TIER0_SCHEMA_VERSION = 1

# Deterministic, no-model facts. Every derived control-plane feature reads from
# this. `source_seq` is the pointer back into the immutable event log / raw store
# so a fact can always be rehydrated to its origin. See CONTROL_PLANE_ROADMAP.md §5.

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
-- The two shapes the Phase 3.7 consumers query: one agent run's facts (loop,
-- declared-vs-verified) and one project's facts across sessions (provenance).
CREATE INDEX IF NOT EXISTS idx_tier0_run ON tier0_facts(agent_run_id,created_at);
CREATE INDEX IF NOT EXISTS idx_tier0_project ON tier0_facts(project_id,created_at);
"""

# Event types that carry a deterministic fact worth recording. Everything else is
# ignored so the capture path stays cheap.
_CAPTURED_EVENTS = {"tool_use", "tool_result", "git_changed", "context_compacted"}
# Keys that commonly hold the target of a tool action across normalized payloads.
_TARGET_KEYS = ("path", "file", "file_path", "command", "cmd", "url", "target")
# Bounded detail so a runaway payload cannot bloat a row. The bound is applied to
# the payload *values*, never to the serialized JSON: truncating the serialization
# yields a string that cannot be parsed back, which silently destroys the fact.
_MAX_DETAIL_BYTES = 4096
_MAX_DETAIL_VALUE_CHARS = 1024
_MAX_TARGET_CHARS = 512
# Payload keys that are structure, not free-form detail, and must survive bounding.
_PROTECTED_DETAIL_KEYS = ("tool", "success", "exit_code", "test_outcome", "scope", "call_id")


@dataclass(frozen=True, slots=True)
class Tier0Context:
    """Ownership context resolved for a session at capture time.

    Returned by the enablement resolver instead of a bare bool so the capture path
    can stamp the run/project a fact belongs to. Per-project and per-run queries
    are the entire point of the substrate; NULL columns make them impossible.
    """

    agent_run_id: str | None = None
    project_id: str | None = None


def _truncate_text(value: str, limit: int) -> str:
    """Bound a string while keeping both ends.

    Test and build output carries its verdict at the end, so a head-only slice
    discards the part a Tier 0 consumer needs.
    """
    if len(value) <= limit:
        return value
    reserve = 48
    head = max(0, (limit - reserve) // 2)
    tail = max(0, limit - reserve - head)
    dropped = len(value) - head - tail
    marker = f"...[{dropped} chars truncated]..."
    if not tail:
        return (value[:head] + marker)[:limit]
    return (value[:head] + marker + value[-tail:])[:limit]


def _bounded_value(value: Any, limit: int) -> Any:
    if isinstance(value, str):
        return _truncate_text(value, limit)
    if value is None or isinstance(value, bool | int | float):
        return value
    try:
        encoded = json.dumps(value, default=str)
    except (TypeError, ValueError):
        return _truncate_text(str(value), limit)
    if len(encoded) <= limit:
        return value
    return _truncate_text(encoded, limit)


def bound_detail_payload(
    payload: dict[str, Any], *, max_bytes: int = _MAX_DETAIL_BYTES
) -> dict[str, Any]:
    """Return a payload whose JSON serialization is bounded and always valid.

    Bounds individual values first, then drops whole keys widest-first if the
    result is still over budget, recording what was dropped. The returned dict is
    JSON-encodable by construction, so `detail_json` can always be parsed back —
    the property the previous serialize-then-slice approach silently violated.
    """
    bounded = {
        str(key): _bounded_value(value, _MAX_DETAIL_VALUE_CHARS) for key, value in payload.items()
    }
    dropped: list[str] = []
    while len(json.dumps(bounded, default=str)) > max_bytes and bounded:
        candidates = [
            key
            for key in bounded
            if key not in {"_truncated", "_dropped_keys"} and key not in _PROTECTED_DETAIL_KEYS
        ]
        if not candidates:
            candidates = [key for key in bounded if key not in {"_truncated", "_dropped_keys"}]
        if not candidates:
            break
        widest = max(candidates, key=lambda key: len(json.dumps(bounded[key], default=str)))
        bounded.pop(widest)
        dropped.append(widest)
        bounded["_truncated"] = True
        bounded["_dropped_keys"] = dropped[:16]
    return bounded


def _fact_from_event(event: MuxEvent) -> dict[str, Any] | None:
    """Extract one deterministic fact from a normalized event, or None.

    Pure and side-effect free so it is trivially testable. Content hashing of
    files is deliberately not done here — reading file bytes on the event path is
    the one heavy operation flagged in the design; the adapter boundary hashes
    what the agent actually wrote or read (`observation.tool_call_evidence` /
    `observation.tool_result_evidence`). This records the fact and a source pointer.
    """
    if event.type not in _CAPTURED_EVENTS:
        return None
    payload = event.payload or {}
    kind = event.type
    tool: str | None = None
    raw_outcome = payload.get("test_outcome")
    test_outcome = raw_outcome if isinstance(raw_outcome, dict) else None
    if event.type == "tool_use":
        tool = str(payload.get("tool") or payload.get("name") or "tool")
        kind = _classify_tool(tool)
    elif event.type == "tool_result":
        tool = str(payload.get("tool") or payload.get("name") or "tool")
        # A result is a distinct fact class from the action that produced it, and
        # it must not collapse onto one kind for every tool: `_result` keeps the
        # documented vocabulary while preserving the action's classification.
        kind = "test_result" if test_outcome else f"{_classify_tool(tool)}_result"
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
    # The state component distinguishes otherwise-identical actions by the thing
    # that actually signals progress: which tests still fail, which working tree
    # the commit was observed against.
    if test_outcome:
        state = _test_signature(test_outcome)
    elif event.type == "git_changed":
        state = str(payload.get("dirty_hash") or "")
    else:
        state = ""
    fingerprint = _fingerprint(
        event.type,
        kind,
        target,
        payload,
        content_hash,
        tool=tool,
        state=state,
    )
    return {
        "session_id": event.session_id or "",
        "kind": kind,
        "target": target,
        "content_hash": content_hash,
        "fingerprint": fingerprint,
        "detail": bound_detail_payload(payload),
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


def _exit_class(payload: dict[str, Any]) -> str:
    """Canonical exit class for a fingerprint.

    A key that is present but None carries no information: treating `exit_code:
    None` as the literal class "None" collapsed every result — success and failure
    alike — onto a single fingerprint.
    """
    for key in ("exit_code", "exit", "status"):
        value = payload.get(key)
        if value is not None and str(value) != "":
            return str(value)
    success = payload.get("success")
    if success is not None:
        return "ok" if success else "fail"
    return ""


def _test_signature(test_outcome: dict[str, Any]) -> str:
    """Fingerprint component for a test result: the failing set, order-independent.

    This is what the loop detector's no-progress gate reads — two runs whose
    failing-test set is identical share a fingerprint; a shrinking set does not.
    """
    failing = test_outcome.get("failing_tests")
    names = sorted(str(item) for item in failing) if isinstance(failing, list) else []
    digest = hashlib.sha256("\x1f".join(names).encode("utf-8")).hexdigest()[:16]
    failed = test_outcome.get("failed") or 0
    errors = test_outcome.get("errors") or 0
    return f"{failed}:{errors}:{digest}"


def _fingerprint(
    event_type: str,
    kind: str,
    target: str | None,
    payload: dict[str, Any],
    content_hash: str | None = None,
    *,
    tool: str | None = None,
    state: str = "",
) -> str:
    """A canonical action fingerprint for loop detection.

    Strips volatile detail (timestamps, exact output) and keys on the semantic
    shape of the action: what tool, of what kind, against what target, with what
    exit class, and — when the adapter captured it — the content written or read.
    Identical repeated edits share a fingerprint (a loop signal); changed content
    differs (progress). `state` carries the progress signal for facts whose
    repetition is only meaningful against it (failing-test set, dirty tree).
    """
    basis = "\x1f".join(
        (
            event_type,
            kind,
            (tool or "").casefold(),
            (target or "").casefold(),
            _exit_class(payload),
            content_hash or "",
            state,
        )
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
        self._resolve_context: Callable[[str], Awaitable[Tier0Context | None]] | None = None
        # Capture is a durable-evidence path: a silent drop is indistinguishable
        # from "nothing happened", so failures are counted and surfaced.
        self._captured = 0
        self._dropped = 0
        self._last_error: str | None = None
        self._last_error_ts: float | None = None

    def _open(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, check_same_thread=False)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA synchronous=NORMAL")
        db.execute("PRAGMA busy_timeout=5000")
        return db

    def _connect(self) -> None:
        with self._operation_lock:
            self._db = connect_or_quarantine(self.path, self._open)
            self._db.executescript(SCHEMA)
            # Per-store row, not PRAGMA user_version: that pragma is per *file*
            # and several stores share mux.db, so each one stamping it overwrote
            # the others' version on every startup.
            write_schema_version(self._db, "tier0", TIER0_SCHEMA_VERSION)
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
        detail_json = json.dumps(bound_detail_payload(detail or {}), default=str)
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
            detail=fact["detail"],
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

    async def facts_for_run(
        self,
        agent_run_id: str,
        *,
        since: float | None = None,
        until: float | None = None,
        limit: int = 2000,
    ) -> list[dict[str, Any]]:
        """Facts owned by one agent run, oldest first.

        Run-scoped, not session-scoped: a resumed, promoted, or branched session
        keeps its session id across runs, so a loop or verification query keyed on
        the session would mix a previous run's evidence into this one's case.
        """

        def op() -> list[dict[str, Any]]:
            sql = "SELECT * FROM tier0_facts WHERE agent_run_id=? AND created_at>=?"
            args: list[Any] = [agent_run_id, since or 0.0]
            if until is not None:
                sql += " AND created_at<=?"
                args.append(until)
            sql += " ORDER BY created_at ASC LIMIT ?"
            args.append(max(1, min(limit, 5000)))
            rows = self._db.execute(sql, args).fetchall()
            return [dict(row) for row in rows]

        return await self._run(op)

    async def facts_for_project(
        self, project_id: str, *, since: float | None = None, limit: int = 2000
    ) -> list[dict[str, Any]]:
        """Facts across every session of one project, oldest first.

        The cross-session view the provenance graph needs; a per-session query
        cannot see "another session wrote this file".
        """

        def op() -> list[dict[str, Any]]:
            rows = self._db.execute(
                "SELECT * FROM tier0_facts WHERE project_id=? AND created_at>=? "
                "ORDER BY created_at ASC LIMIT ?",
                (project_id, since or 0.0, max(1, min(limit, 5000))),
            ).fetchall()
            return [dict(row) for row in rows]

        return await self._run(op)

    async def write_facts_for_project(
        self,
        project_id: str,
        *,
        since: float,
        until: float,
        limit: int = 2000,
    ) -> list[dict[str, Any]]:
        """Targeted file writes across one project's sessions, oldest first.

        The commit-attribution query: which sessions wrote the files a commit
        contains. Only the tool-call fact is returned — it is the one that carries
        the target and the hash of what was written; the paired result fact records
        that the call succeeded and carries neither.
        """

        def op() -> list[dict[str, Any]]:
            rows = self._db.execute(
                "SELECT * FROM tier0_facts WHERE project_id=? AND kind='file_write' "
                "AND target IS NOT NULL AND target!='' AND created_at>=? AND created_at<=? "
                "ORDER BY created_at ASC LIMIT ?",
                (project_id, since, until, max(1, min(limit, 5000))),
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
        self,
        events: EventBus,
        *,
        resolve_context: Callable[[str], Awaitable[Tier0Context | None]],
    ) -> None:
        """Begin gated capture.

        `resolve_context` decides per session whether the owning project opted
        Tier 0 in (the enablement gate, CONTROL_PLANE_ROADMAP §8) and, when it
        did, returns the run/project the fact belongs to. Returning None means
        "not enabled".
        """
        self._event_bus = events
        self._resolve_context = resolve_context
        self._event_queue = events.subscribe(name="tier0-capture")
        self._event_task = asyncio.create_task(self._consume_events(), name="tier0-capture")

    def capture_stats(self) -> dict[str, Any]:
        """Capture health for diagnostics: silent fact loss must be observable."""
        return {
            "captured": self._captured,
            "dropped": self._dropped,
            "last_error": self._last_error,
            "last_error_ts": self._last_error_ts,
            "running": bool(self._event_task and not self._event_task.done()),
        }

    async def _consume_events(self) -> None:
        assert self._event_queue is not None
        assert self._resolve_context is not None
        # unsupervised-loop-ok: guarded in-place with its own dropped-fact counter
        # and last-error field, surfaced as tier0_capture in the same diagnostic.
        while True:
            event = await self._event_queue.get()
            try:
                if event.type in _CAPTURED_EVENTS and event.session_id:
                    context = await self._resolve_context(event.session_id)
                    if context is not None:
                        recorded = await self.record_from_event(
                            event,
                            agent_run_id=context.agent_run_id,
                            project_id=context.project_id,
                        )
                        if recorded:
                            self._captured += 1
            except Exception as exc:  # noqa: BLE001 - capture must never break the event loop
                self._dropped += 1
                self._last_error = f"{type(exc).__name__}: {exc}"[:400]
                self._last_error_ts = time.time()
                # Loud on the first loss of each daemon lifetime, then rate-limited:
                # a persistent failure must not turn the log into the failure.
                if self._dropped == 1 or self._dropped % 100 == 0:
                    log.exception("tier 0 capture dropped a fact (%d total)", self._dropped)
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
        self._resolve_context = None

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._executor.submit(self._db.close).result()
        self._executor.shutdown(wait=True)
