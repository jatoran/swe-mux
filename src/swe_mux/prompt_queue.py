"""Phase 4: the persistent manual prompt queue.

Durable, ordered messages a user stages against a target agent run. Delivery is
always an explicit user act through one typed operation (`send_next`); nothing
in this module fires on a timer or delivers autonomously. The storage model is
mailbox-shaped so later senders (Phase 5 mailbox/agent messages, the
control-plane queue-draft channel) can be added without a migration into an
orchestration framework.

Load-bearing rules, from `development/ROADMAP.md` Phase 4:

- Messages key to stable agent-run identity, not the pane. A queue item binds
  to the target's ``agent_run_id`` at enqueue (or to the first run the session
  ever gets, for items staged against a still-starting session) and is never
  re-bound: a replaced or ended run strands the item, visibly, rather than
  silently retargeting a successor conversation.
- Strict head-of-line: later items may be armed in advance, but an earlier
  pending (draft/armed/blocked/delivering) item blocks their delivery until it
  is sent, cancelled, or explicitly skipped.
- The exact body shown is the body delivered: edits increment ``revision`` and
  the send operation validates against the revision the user last saw. There
  is no hidden rendered variant.
- Delivery audit (`queue_deliveries`) records attempt/result and the readiness
  evidence — never the prompt text. Prompt bodies live in `queue_messages`
  only.
- The sender model carries ``sender_kind``/provenance rich enough for the
  control-plane queue-draft channel (`CONTROL_PLANE_ROADMAP.md` §13) on day
  one: an observer-authored draft persists its originating rule id, fact
  fingerprints, and a typed action payload, and is inert until a human arms
  and sends it. The HTTP surface only ever creates ``sender_kind="user"``.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
import uuid
from collections.abc import Callable, Collection
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, TypeVar

from .background_tasks import background
from .harness import delivers_prompts_through_pty
from .sqlite_store import (
    connect_or_quarantine,
    database_operation_lock,
    run_sqlite_operation,
    write_schema_version,
)

T = TypeVar("T")

QUEUE_SCHEMA_VERSION = 4
QUEUE_EVENT_LOOP = "prompt-queue-events"

# States exactly per the roadmap. `delivering` is transient but persisted so a
# daemon death mid-send is distinguishable from one that never started.
PENDING_STATES = ("draft", "armed", "blocked")
ACTIVE_STATES = (*PENDING_STATES, "delivering")
TERMINAL_STATES = ("sent", "failed", "cancelled", "stranded", "deleted")
ALL_STATES = (*ACTIVE_STATES, *TERMINAL_STATES)

# Phase 5 generalizes the sender model (`ROADMAP.md` Phase 5, "Human/device
# mailbox"): a message records *which kind of actor authored it*, and the
# daemon derives that from the transport rather than trusting a client claim —
# `user` is a loopback browser/CLI act, `remote_user` an authenticated remote
# device, `agent` an `mux.notify` call attributed to its MCP token, `rule` a
# deterministic observer, `queue_draft` the control-plane draft channel (§13).
SENDER_KINDS = ("user", "remote_user", "rule", "agent", "queue_draft")
# Sender kinds a human is behind. Only these may be created armed by their
# author, and only these are eligible for user-authored auto-delivery.
HUMAN_SENDER_KINDS = frozenset({"user", "remote_user"})
CANCEL_KINDS = ("cancelled", "skipped", "revoked", "expired")

# The auto-delivery policy table keys per-session rows by session id and keeps
# one reserved row for daemon-wide state (the emergency pause).
AUTO_POLICY_GLOBAL = "*"

MAX_BODY_CHARS = 500_000
HISTORY_LIMIT = 200
# A scheduled send is still a send: a horizon keeps "in 30 days" from becoming
# "whenever this daemon happens to be running in 2030".
MAX_SCHEDULE_HORIZON_SECONDS = 30 * 86400

# Delivery bytes mirror the browser's live-session path (`noteSelection.ts`):
# a multi-line body sent unwrapped would submit at every newline, so the text
# is wrapped in bracketed paste with newlines as CR, and the submit is a
# separate write after the same settle delay the browser uses.
BRACKETED_PASTE_START = "\x1b[200~"
BRACKETED_PASTE_END = "\x1b[201~"
SUBMIT_SEQUENCE = "\r"
SUBMIT_DELAY_SECONDS = 0.18

# Blocked/unknown readiness can be overridden by an explicit, per-send user
# confirmation — except for the protections the roadmap forbids bypassing:
# approval/Q&A prompts (text typed at an approval dialog can *answer* it) and
# target identity/liveness (those strand instead). Everything else (working,
# operator recently typed, alternate screen, unknown evidence) is the user's
# call, exactly as the send-to-agent dialog allowed before the queue owned it.
NON_OVERRIDABLE_REASONS = frozenset(
    {"session_ended", "not_live_agent_run", "approval_required", "awaiting_user_input"}
)
PROTECTED_AWAITING_REASONS = frozenset({"approval", "question", "elicitation"})

# New-session seeds: bodies at or under this bound ride the agent CLI's argv
# (one Windows command line, ~32,767 chars shared with the exe path and
# flags); anything larger is staged to a file inside the workspace and seeded
# with a short reader prompt, which also removes quoting inflation.
ARGV_SEED_MAX_CHARS = 20_000
SEED_DIR_NAME = "seeds"
SEED_RETENTION_SECONDS = 14 * 86400

QUEUE_SCHEMA = """
CREATE TABLE IF NOT EXISTS queue_messages (
  id TEXT PRIMARY KEY,
  target_session_id TEXT NOT NULL,
  target_agent_run_id TEXT,
  target_backend TEXT,
  target_label TEXT,
  project_id TEXT,
  position INTEGER NOT NULL,
  state TEXT NOT NULL,
  body TEXT NOT NULL,
  revision INTEGER NOT NULL DEFAULT 1,
  sender_kind TEXT NOT NULL DEFAULT 'user',
  sender_id TEXT,
  sender_label TEXT,
  origin_session_id TEXT,
  correlation_id TEXT,
  thread_id TEXT,
  chain_depth INTEGER NOT NULL DEFAULT 0,
  origin_json TEXT,
  payload_json TEXT,
  constraints_json TEXT,
  blocked_reasons_json TEXT,
  stranded_reason TEXT,
  cancel_kind TEXT,
  retargeted_from_json TEXT,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL,
  edited_at REAL,
  armed_at REAL,
  sent_at REAL,
  deleted_at REAL
);
CREATE INDEX IF NOT EXISTS idx_queue_messages_target
  ON queue_messages(target_session_id, position);
CREATE INDEX IF NOT EXISTS idx_queue_messages_state
  ON queue_messages(state, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_queue_messages_sender
  ON queue_messages(sender_kind, sender_id, created_at DESC);
-- Retry-safe correlation (`ROADMAP.md` Phase 5, mailbox): a sender that
-- retries the same logical message reuses its correlation id and gets the
-- original row back instead of a second copy in the target's queue.
CREATE UNIQUE INDEX IF NOT EXISTS idx_queue_messages_correlation
  ON queue_messages(sender_kind, sender_id, correlation_id)
  WHERE correlation_id IS NOT NULL;
-- A relay thread is the exchange itself, and it is *not* the correlation id:
-- correlation is a per-sender idempotency key, so a second message from the
-- same sender in the same exchange would dedup into the first. `thread_id` is
-- assigned by the daemon at the head of a chain and inherited by every message
-- that continues it, which is what makes the turn bound countable.
CREATE INDEX IF NOT EXISTS idx_queue_messages_thread
  ON queue_messages(thread_id, created_at)
  WHERE thread_id IS NOT NULL;
CREATE TABLE IF NOT EXISTS queue_deliveries (
  id TEXT PRIMARY KEY,
  message_id TEXT NOT NULL,
  idempotency_key TEXT,
  revision INTEGER NOT NULL,
  target_session_id TEXT NOT NULL,
  target_agent_run_id TEXT,
  delivery_state TEXT,
  reasons_json TEXT,
  confirmed INTEGER NOT NULL DEFAULT 0,
  initiator TEXT NOT NULL DEFAULT 'user',
  outcome TEXT NOT NULL,
  error TEXT,
  bytes INTEGER,
  created_at REAL NOT NULL,
  completed_at REAL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_queue_deliveries_idempotency
  ON queue_deliveries(idempotency_key) WHERE idempotency_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_queue_deliveries_message
  ON queue_deliveries(message_id, created_at DESC);
-- Phase 5 runtime state, deliberately not in config.toml: the emergency pause
-- and every per-session opt-in must be flippable instantly, survive a restart,
-- and stay independent of file writes and provider availability.
CREATE TABLE IF NOT EXISTS queue_auto_policy (
  session_id TEXT PRIMARY KEY,
  enabled INTEGER NOT NULL DEFAULT 0,
  agent_run_id TEXT,
  accept_agent_messages INTEGER NOT NULL DEFAULT 0,
  expires_at REAL,
  max_sends INTEGER NOT NULL DEFAULT 0,
  sends_used INTEGER NOT NULL DEFAULT 0,
  paused INTEGER NOT NULL DEFAULT 0,
  disabled_reason TEXT,
  enabled_at REAL,
  updated_at REAL NOT NULL,
  updated_by TEXT
);
-- Proving-period instrumentation: the promotion criteria are quantitative, so
-- the counts behind them are persisted rather than recomputed from logs.
CREATE TABLE IF NOT EXISTS queue_auto_counters (
  name TEXT PRIMARY KEY,
  value REAL NOT NULL DEFAULT 0,
  updated_at REAL
);
"""

_MESSAGE_JSON_FIELDS = (
    ("origin_json", "origin"),
    ("payload_json", "payload"),
    ("constraints_json", "constraints"),
    ("blocked_reasons_json", "blocked_reasons"),
    ("retargeted_from_json", "retargeted_from"),
)


class QueueError(Exception):
    """Typed failure of a queue operation, mapped to an HTTP status by handlers."""

    def __init__(self, code: str, message: str, *, status: int = 409, **payload: Any) -> None:
        super().__init__(message)
        self.code = code
        self.status = status
        self.payload = payload


def _tune_connection(db: sqlite3.Connection) -> None:
    db.execute("PRAGMA synchronous=NORMAL")
    db.execute("PRAGMA busy_timeout=5000")


def _row_to_message(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    for column, key in _MESSAGE_JSON_FIELDS:
        raw = item.pop(column, None)
        item[key] = json.loads(raw) if raw else None
    return item


def _dumps(value: Any) -> str | None:
    return json.dumps(value, separators=(",", ":")) if value is not None else None


class PromptQueueStore:
    """SQLite store on one dedicated worker thread (the `AutomationStore` pattern).

    Every method's statements run atomically in submission order on a single
    executor thread; state transitions are conditional UPDATEs so a stale
    caller loses the race instead of corrupting order or double-delivering.
    """

    _db: sqlite3.Connection

    def __init__(self, path: Path) -> None:
        self._path = path
        self._operation_lock = database_operation_lock(path)
        self._closed = False
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mux-queue-db")
        self._executor.submit(self._connect).result()

    def _connect(self) -> None:
        with self._operation_lock:
            self._db = connect_or_quarantine(self._path, self._open)
            # Migrate before the schema script: it creates indexes over columns
            # a v1 table does not have yet, so running it first fails before
            # the migration could add them.
            self._migrate_schema()
            self._db.executescript(QUEUE_SCHEMA)
            write_schema_version(self._db, "prompt_queue", QUEUE_SCHEMA_VERSION)
            self._db.commit()

    def _migrate_schema(self) -> None:
        """Upgrade queue tables in place through the current schema version.

        ``CREATE TABLE IF NOT EXISTS`` no-ops on an existing table, so a plain
        column add in the schema script would only ever reach fresh databases
        and every upgrade-in-place would fail on the first insert naming it.
        """
        columns = {
            str(row["name"])
            for row in self._db.execute("PRAGMA table_info(queue_messages)").fetchall()
        }
        if not columns:
            return
        additions = (
            ("sender_label", "TEXT"),
            ("origin_session_id", "TEXT"),
            ("correlation_id", "TEXT"),
            ("thread_id", "TEXT"),
            ("chain_depth", "INTEGER NOT NULL DEFAULT 0"),
            ("deleted_at", "REAL"),
        )
        for name, declaration in additions:
            if name not in columns:
                self._db.execute(
                    f"ALTER TABLE queue_messages ADD COLUMN {name} {declaration}"
                )
        delivery_columns = {
            str(row["name"])
            for row in self._db.execute("PRAGMA table_info(queue_deliveries)").fetchall()
        }
        if delivery_columns and "initiator" not in delivery_columns:
            # Who pressed send is audit-load-bearing once a controller can:
            # every pre-Phase-5 attempt was a human act, hence the default.
            self._db.execute(
                "ALTER TABLE queue_deliveries ADD COLUMN initiator TEXT NOT NULL DEFAULT 'user'"
            )
        self._db.commit()

    def _open(self) -> sqlite3.Connection:
        db = sqlite3.connect(self._path, check_same_thread=False)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        _tune_connection(db)
        return db

    async def _run(self, fn: Callable[[], T]) -> T:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self._executor, run_sqlite_operation, self._db, self._operation_lock, fn
        )

    # -- ordering helpers (worker thread only) --------------------------------

    def _renumber(self, target_session_id: str, ordered_ids: list[str] | None = None) -> None:
        """Assign gap-free positions across all visible messages for a target.

        Sent/terminal items keep their place in the visible queue. Deleted
        tombstones retain no visible position and are excluded from ordering.
        """
        if ordered_ids is None:
            rows = self._db.execute(
                "SELECT id FROM queue_messages WHERE target_session_id=? AND state!='deleted'"
                " ORDER BY position",
                (target_session_id,),
            ).fetchall()
            ordered_ids = [str(row["id"]) for row in rows]
        for index, message_id in enumerate(ordered_ids):
            self._db.execute(
                "UPDATE queue_messages SET position=? WHERE id=?", (index, message_id)
            )

    # -- message lifecycle ----------------------------------------------------

    async def create_message(
        self,
        *,
        message_id: str | None = None,
        target_session_id: str,
        target_agent_run_id: str | None,
        target_backend: str | None,
        target_label: str | None,
        project_id: str | None,
        body: str,
        armed: bool,
        sender_kind: str,
        sender_id: str | None,
        sender_label: str | None = None,
        origin_session_id: str | None = None,
        correlation_id: str | None = None,
        thread_id: str | None = None,
        chain_depth: int = 0,
        origin: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
        constraints: dict[str, Any] | None = None,
        insert_after: str | None = None,
    ) -> dict[str, Any]:
        identity = str(message_id or uuid.uuid4())
        now = time.time()
        state = "armed" if armed else "draft"

        def op() -> dict[str, Any]:
            if correlation_id:
                # Retry-safe correlation: the same logical message re-sent
                # returns the row it already created, never a duplicate.
                existing = self._db.execute(
                    "SELECT * FROM queue_messages WHERE sender_kind=? AND"
                    " IFNULL(sender_id,'')=IFNULL(?,'') AND correlation_id=?",
                    (sender_kind, sender_id, correlation_id),
                ).fetchone()
                if existing is not None:
                    return {**_row_to_message(existing), "deduplicated": True}
            rows = self._db.execute(
                "SELECT id FROM queue_messages WHERE target_session_id=? AND state!='deleted'"
                " ORDER BY position",
                (target_session_id,),
            ).fetchall()
            ordered = [str(row["id"]) for row in rows]
            if insert_after is not None:
                if insert_after not in ordered:
                    raise QueueError(
                        "unknown_anchor", "insert_after names no message in this queue", status=400
                    )
                ordered.insert(ordered.index(insert_after) + 1, identity)
            else:
                ordered.append(identity)
            self._db.execute(
                "INSERT INTO queue_messages"
                "(id,target_session_id,target_agent_run_id,target_backend,target_label,"
                "project_id,position,state,body,revision,sender_kind,sender_id,sender_label,"
                "origin_session_id,correlation_id,thread_id,chain_depth,origin_json,"
                "payload_json,constraints_json,created_at,updated_at,armed_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,1,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    identity,
                    target_session_id,
                    target_agent_run_id,
                    target_backend,
                    target_label,
                    project_id,
                    len(ordered) - 1,
                    state,
                    body,
                    sender_kind,
                    sender_id,
                    sender_label,
                    origin_session_id,
                    correlation_id,
                    thread_id,
                    max(0, int(chain_depth)),
                    _dumps(origin),
                    _dumps(payload),
                    _dumps(constraints),
                    now,
                    now,
                    now if armed else None,
                ),
            )
            self._renumber(target_session_id, ordered)
            self._db.commit()
            row = self._db.execute(
                "SELECT * FROM queue_messages WHERE id=?", (identity,)
            ).fetchone()
            assert row is not None
            return _row_to_message(row)

        return await self._run(op)

    async def message(self, message_id: str) -> dict[str, Any] | None:
        def op() -> dict[str, Any] | None:
            row = self._db.execute(
                "SELECT * FROM queue_messages WHERE id=?", (message_id,)
            ).fetchone()
            return _row_to_message(row) if row else None

        return await self._run(op)

    async def messages_for_target(self, target_session_id: str) -> dict[str, Any]:
        def op() -> dict[str, Any]:
            rows = self._db.execute(
                "SELECT * FROM queue_messages WHERE target_session_id=? AND state!='deleted' "
                "ORDER BY position LIMIT ?",
                (target_session_id, HISTORY_LIMIT + 64),
            ).fetchall()
            messages = [_row_to_message(row) for row in rows]
            pending = sum(1 for item in messages if item["state"] in ACTIVE_STATES)
            return {"messages": messages, "pending": pending}

        return await self._run(op)

    async def summary(self) -> list[dict[str, Any]]:
        """Per-target aggregates for rail chips and stranded-queue discovery."""

        def op() -> list[dict[str, Any]]:
            rows = self._db.execute(
                "SELECT target_session_id, MAX(target_label) label, MAX(project_id) project_id,"
                " MAX(target_backend) backend,"
                " SUM(CASE WHEN state IN ('draft','armed','blocked','delivering') THEN 1 ELSE 0"
                " END) pending,"
                " SUM(CASE WHEN state='blocked' THEN 1 ELSE 0 END) blocked,"
                " SUM(CASE WHEN state='stranded' THEN 1 ELSE 0 END) stranded,"
                " SUM(CASE WHEN state='failed' THEN 1 ELSE 0 END) failed,"
                " COUNT(*) total, MAX(updated_at) updated_at"
                " FROM queue_messages WHERE state!='deleted' GROUP BY target_session_id"
                " ORDER BY updated_at DESC",
            ).fetchall()
            return [dict(row) for row in rows]

        return await self._run(op)

    async def edit_body(self, message_id: str, revision: int, body: str) -> dict[str, Any]:
        """Edit a pending item's body; each edit increments revision.

        A blocked item's refusal evidence is stale after an edit, so it
        returns to armed/draft (by whether it was ever armed) with its blocked
        reasons cleared. Sent/delivering items are immutable.
        """
        now = time.time()

        def op() -> dict[str, Any]:
            row = self._db.execute(
                "SELECT * FROM queue_messages WHERE id=?", (message_id,)
            ).fetchone()
            if row is None:
                raise QueueError("not_found", "no such queue message", status=404)
            if row["state"] not in PENDING_STATES:
                raise QueueError(
                    "immutable_state",
                    f"a {row['state']} message cannot be edited",
                    state=row["state"],
                )
            if int(row["revision"]) != revision:
                raise QueueError(
                    "revision_conflict",
                    "the message changed since you last saw it",
                    revision=int(row["revision"]),
                )
            next_state = row["state"]
            if next_state == "blocked":
                next_state = "armed" if row["armed_at"] is not None else "draft"
            self._db.execute(
                "UPDATE queue_messages SET body=?, revision=revision+1, state=?,"
                " blocked_reasons_json=NULL, edited_at=?, updated_at=? WHERE id=?",
                (body, next_state, now, now, message_id),
            )
            self._db.commit()
            fresh = self._db.execute(
                "SELECT * FROM queue_messages WHERE id=?", (message_id,)
            ).fetchone()
            assert fresh is not None
            return _row_to_message(fresh)

        return await self._run(op)

    async def set_armed(self, message_id: str, armed: bool) -> dict[str, Any]:
        now = time.time()

        def op() -> dict[str, Any]:
            row = self._db.execute(
                "SELECT * FROM queue_messages WHERE id=?", (message_id,)
            ).fetchone()
            if row is None:
                raise QueueError("not_found", "no such queue message", status=404)
            if row["state"] not in PENDING_STATES:
                raise QueueError(
                    "immutable_state",
                    f"a {row['state']} message cannot be re-armed",
                    state=row["state"],
                )
            state = "armed" if armed else "draft"
            self._db.execute(
                "UPDATE queue_messages SET state=?, armed_at=?, blocked_reasons_json=NULL,"
                " updated_at=? WHERE id=?",
                (state, now if armed else None, now, message_id),
            )
            self._db.commit()
            fresh = self._db.execute(
                "SELECT * FROM queue_messages WHERE id=?", (message_id,)
            ).fetchone()
            assert fresh is not None
            return _row_to_message(fresh)

        return await self._run(op)

    async def move_message(self, message_id: str, *, after: str | None) -> dict[str, Any]:
        """Reorder a pending message: place it after ``after`` (None = front)."""
        now = time.time()

        def op() -> dict[str, Any]:
            row = self._db.execute(
                "SELECT * FROM queue_messages WHERE id=?", (message_id,)
            ).fetchone()
            if row is None:
                raise QueueError("not_found", "no such queue message", status=404)
            if row["state"] not in PENDING_STATES:
                raise QueueError(
                    "immutable_state",
                    f"a {row['state']} message cannot be reordered",
                    state=row["state"],
                )
            target = str(row["target_session_id"])
            rows = self._db.execute(
                "SELECT id FROM queue_messages WHERE target_session_id=? AND state!='deleted'"
                " ORDER BY position",
                (target,),
            ).fetchall()
            ordered = [str(item["id"]) for item in rows]
            ordered.remove(message_id)
            if after is None:
                ordered.insert(0, message_id)
            else:
                if after not in ordered:
                    raise QueueError(
                        "unknown_anchor", "after names no message in this queue", status=400
                    )
                ordered.insert(ordered.index(after) + 1, message_id)
            self._renumber(target, ordered)
            self._db.execute(
                "UPDATE queue_messages SET updated_at=? WHERE id=?", (now, message_id)
            )
            self._db.commit()
            fresh = self._db.execute(
                "SELECT * FROM queue_messages WHERE id=?", (message_id,)
            ).fetchone()
            assert fresh is not None
            return _row_to_message(fresh)

        return await self._run(op)

    async def cancel(self, message_id: str, *, kind: str) -> dict[str, Any]:
        """Cancel or explicitly skip a pending or stranded message."""
        now = time.time()

        def op() -> dict[str, Any]:
            row = self._db.execute(
                "SELECT * FROM queue_messages WHERE id=?", (message_id,)
            ).fetchone()
            if row is None:
                raise QueueError("not_found", "no such queue message", status=404)
            if row["state"] not in (*PENDING_STATES, "stranded"):
                raise QueueError(
                    "immutable_state",
                    f"a {row['state']} message cannot be cancelled",
                    state=row["state"],
                )
            self._db.execute(
                "UPDATE queue_messages SET state='cancelled', cancel_kind=?, updated_at=?"
                " WHERE id=?",
                (kind, now, message_id),
            )
            self._db.commit()
            fresh = self._db.execute(
                "SELECT * FROM queue_messages WHERE id=?", (message_id,)
            ).fetchone()
            assert fresh is not None
            return _row_to_message(fresh)

        return await self._run(op)

    async def delete_message(self, message_id: str) -> dict[str, Any]:
        """Erase and hide a message while retaining a retry-suppression tombstone.

        The content-free row preserves correlation identity and delivery audit
        linkage. A delivering item cannot be deleted because its PTY write may
        already be underway.
        """
        now = time.time()

        def op() -> dict[str, Any]:
            row = self._db.execute(
                "SELECT * FROM queue_messages WHERE id=?", (message_id,)
            ).fetchone()
            if row is None:
                raise QueueError("not_found", "no such queue message", status=404)
            if row["state"] == "delivering":
                raise QueueError(
                    "delivery_in_progress",
                    "a delivering message cannot be deleted",
                    status=409,
                    state="delivering",
                )
            result = {
                "id": str(row["id"]),
                "target_session_id": str(row["target_session_id"]),
                "previous_state": str(row["state"]),
                "sender_kind": str(row["sender_kind"]),
                "already_deleted": row["state"] == "deleted",
            }
            if row["state"] == "deleted":
                return result
            self._db.execute(
                "UPDATE queue_messages SET state='deleted', body='', origin_json=NULL,"
                " payload_json=NULL, constraints_json=NULL, blocked_reasons_json=NULL,"
                " stranded_reason=NULL, cancel_kind=NULL, retargeted_from_json=NULL,"
                " deleted_at=?, updated_at=? WHERE id=?",
                (now, now, message_id),
            )
            self._renumber(str(row["target_session_id"]))
            self._db.commit()
            return result

        return await self._run(op)

    async def retarget(
        self,
        message_id: str,
        *,
        target_session_id: str,
        target_agent_run_id: str | None,
        target_backend: str | None,
        target_label: str | None,
        project_id: str | None,
    ) -> dict[str, Any]:
        """Move a stranded message to a new target, explicitly, as a draft at the tail.

        Only stranded items may retarget — a live queue item aimed at the
        wrong session is an edit/cancel problem, and the daemon never
        retargets anything on its own.
        """
        now = time.time()

        def op() -> dict[str, Any]:
            row = self._db.execute(
                "SELECT * FROM queue_messages WHERE id=?", (message_id,)
            ).fetchone()
            if row is None:
                raise QueueError("not_found", "no such queue message", status=404)
            if row["state"] != "stranded":
                raise QueueError(
                    "immutable_state",
                    "only stranded messages can be retargeted",
                    state=row["state"],
                )
            provenance = {
                "session_id": row["target_session_id"],
                "agent_run_id": row["target_agent_run_id"],
                "label": row["target_label"],
                "stranded_reason": row["stranded_reason"],
                "retargeted_at": now,
            }
            position_row = self._db.execute(
                "SELECT COALESCE(MAX(position)+1,0) next FROM queue_messages"
                " WHERE target_session_id=?",
                (target_session_id,),
            ).fetchone()
            self._db.execute(
                "UPDATE queue_messages SET target_session_id=?, target_agent_run_id=?,"
                " target_backend=?, target_label=?, project_id=?, position=?, state='draft',"
                " stranded_reason=NULL, blocked_reasons_json=NULL, armed_at=NULL,"
                " retargeted_from_json=?, updated_at=? WHERE id=?",
                (
                    target_session_id,
                    target_agent_run_id,
                    target_backend,
                    target_label,
                    project_id,
                    int(position_row["next"]),
                    _dumps(provenance),
                    now,
                    message_id,
                ),
            )
            self._db.commit()
            fresh = self._db.execute(
                "SELECT * FROM queue_messages WHERE id=?", (message_id,)
            ).fetchone()
            assert fresh is not None
            return _row_to_message(fresh)

        return await self._run(op)

    async def bind_run(self, message_id: str, agent_run_id: str) -> None:
        """Bind an unbound message to the first run its target session got."""

        def op() -> None:
            self._db.execute(
                "UPDATE queue_messages SET target_agent_run_id=? WHERE id=?"
                " AND target_agent_run_id IS NULL",
                (agent_run_id, message_id),
            )
            self._db.commit()

        await self._run(op)

    # -- delivery -------------------------------------------------------------

    async def claim_for_delivery(
        self,
        message_id: str,
        revision: int,
        idempotency_key: str | None,
        *,
        initiator: str = "user",
    ) -> dict[str, Any]:
        """Atomically claim the queue head for delivery.

        One transaction re-checks existence, pending state, revision, and the
        strict head-of-line rule, then flips the item to ``delivering`` and
        opens the audit row. A repeated idempotency key returns the recorded
        outcome instead of a second claim — that is the no-duplicate-delivery
        guarantee across retried HTTP calls and daemon restarts.
        """
        now = time.time()
        delivery_id = str(uuid.uuid4())

        def op() -> dict[str, Any]:
            if idempotency_key:
                existing = self._db.execute(
                    "SELECT * FROM queue_deliveries WHERE idempotency_key=?",
                    (idempotency_key,),
                ).fetchone()
                if existing is not None:
                    return {"status": "duplicate", "delivery": dict(existing)}
            row = self._db.execute(
                "SELECT * FROM queue_messages WHERE id=?", (message_id,)
            ).fetchone()
            if row is None:
                raise QueueError("not_found", "no such queue message", status=404)
            if row["state"] not in PENDING_STATES:
                raise QueueError(
                    "invalid_state",
                    f"a {row['state']} message cannot be delivered",
                    state=row["state"],
                )
            if int(row["revision"]) != revision:
                raise QueueError(
                    "revision_conflict",
                    "the message changed since you last saw it",
                    revision=int(row["revision"]),
                )
            blocker = self._db.execute(
                "SELECT id, state FROM queue_messages WHERE target_session_id=? AND position<?"
                " AND state IN ('draft','armed','blocked','delivering')"
                " ORDER BY position LIMIT 1",
                (row["target_session_id"], row["position"]),
            ).fetchone()
            if blocker is not None:
                raise QueueError(
                    "head_of_line_blocked",
                    "an earlier pending message must be sent, cancelled, or skipped first",
                    blocking_message_id=str(blocker["id"]),
                    blocking_state=str(blocker["state"]),
                )
            self._db.execute(
                "UPDATE queue_messages SET state='delivering', updated_at=? WHERE id=?",
                (now, message_id),
            )
            self._db.execute(
                "INSERT INTO queue_deliveries"
                "(id,message_id,idempotency_key,revision,target_session_id,"
                "target_agent_run_id,confirmed,initiator,outcome,created_at) "
                "VALUES(?,?,?,?,?,?,0,?,'pending',?)",
                (
                    delivery_id,
                    message_id,
                    idempotency_key,
                    revision,
                    row["target_session_id"],
                    row["target_agent_run_id"],
                    initiator,
                    now,
                ),
            )
            self._db.commit()
            return {
                "status": "claimed",
                "delivery_id": delivery_id,
                "message": _row_to_message(row),
            }

        return await self._run(op)

    async def finalize_delivery(
        self,
        delivery_id: str,
        message_id: str,
        *,
        outcome: str,
        message_state: str,
        delivery_state: str | None = None,
        reasons: list[str] | None = None,
        confirmed: bool = False,
        error: str | None = None,
        byte_count: int | None = None,
        blocked_reasons: list[str] | None = None,
        stranded_reason: str | None = None,
        cancel_kind: str | None = None,
    ) -> dict[str, Any]:
        now = time.time()

        def op() -> dict[str, Any]:
            # A refused attempt released its idempotency key: the key exists to
            # prevent duplicate *deliveries*, and a refusal never wrote the PTY —
            # pinning it would make the confirm retry replay the refusal forever.
            # Sent and failed attempts keep theirs (failed may have written).
            self._db.execute(
                "UPDATE queue_deliveries SET outcome=?, delivery_state=?, reasons_json=?,"
                " confirmed=?, error=?, bytes=?, completed_at=?,"
                " idempotency_key=CASE WHEN ?='refused' THEN NULL ELSE idempotency_key END"
                " WHERE id=?",
                (
                    outcome,
                    delivery_state,
                    _dumps(reasons),
                    int(confirmed),
                    error,
                    byte_count,
                    now,
                    outcome,
                    delivery_id,
                ),
            )
            self._db.execute(
                "UPDATE queue_messages SET state=?, blocked_reasons_json=?, stranded_reason=?,"
                " cancel_kind=COALESCE(?,cancel_kind),"
                " sent_at=CASE WHEN ?='sent' THEN ? ELSE sent_at END, updated_at=? WHERE id=?",
                (
                    message_state,
                    _dumps(blocked_reasons),
                    stranded_reason,
                    cancel_kind,
                    message_state,
                    now,
                    now,
                    message_id,
                ),
            )
            self._db.commit()
            fresh = self._db.execute(
                "SELECT * FROM queue_messages WHERE id=?", (message_id,)
            ).fetchone()
            assert fresh is not None
            return _row_to_message(fresh)

        return await self._run(op)

    async def deliveries(self, message_id: str) -> list[dict[str, Any]]:
        def op() -> list[dict[str, Any]]:
            rows = self._db.execute(
                "SELECT * FROM queue_deliveries WHERE message_id=? ORDER BY created_at DESC"
                " LIMIT 50",
                (message_id,),
            ).fetchall()
            result = []
            for row in rows:
                item = dict(row)
                raw = item.pop("reasons_json", None)
                item["reasons"] = json.loads(raw) if raw else None
                result.append(item)
            return result

        return await self._run(op)

    # -- Phase 5: constraints, mailbox, relay bounds --------------------------

    async def set_constraints(
        self, message_id: str, constraints: dict[str, Any] | None
    ) -> dict[str, Any]:
        """Replace a pending message's delivery constraints (schedule/expiry).

        A schedule is a property of the queue item, never a timer held by a
        sender's UI (`ROADMAP.md` Phase 5): a browser timer dies with the tab
        and a private daemon timer would be a second, unaudited delivery path.
        Constraints do not change the body, so they do not bump ``revision`` —
        the revision contract is about the text the user saw being the text
        delivered.
        """
        now = time.time()

        def op() -> dict[str, Any]:
            row = self._db.execute(
                "SELECT * FROM queue_messages WHERE id=?", (message_id,)
            ).fetchone()
            if row is None:
                raise QueueError("not_found", "no such queue message", status=404)
            if row["state"] not in PENDING_STATES:
                raise QueueError(
                    "immutable_state",
                    f"a {row['state']} message cannot be rescheduled",
                    state=row["state"],
                )
            self._db.execute(
                "UPDATE queue_messages SET constraints_json=?, updated_at=? WHERE id=?",
                (_dumps(constraints or None), now, message_id),
            )
            self._db.commit()
            fresh = self._db.execute(
                "SELECT * FROM queue_messages WHERE id=?", (message_id,)
            ).fetchone()
            assert fresh is not None
            return _row_to_message(fresh)

        return await self._run(op)

    async def expired_pending(self, now: float) -> list[dict[str, Any]]:
        """Pending items whose ``constraints.expires_at`` has passed."""

        def op() -> list[dict[str, Any]]:
            rows = self._db.execute(
                "SELECT * FROM queue_messages WHERE state IN ('draft','armed','blocked')"
                " AND constraints_json IS NOT NULL",
            ).fetchall()
            due: list[dict[str, Any]] = []
            for row in rows:
                item = _row_to_message(row)
                constraints = item.get("constraints") or {}
                expires_at = constraints.get("expires_at")
                if isinstance(expires_at, int | float) and now >= float(expires_at):
                    due.append(item)
            return due

        return await self._run(op)

    async def mailbox(
        self,
        *,
        author: str = "all",
        role: str | None = None,
        project_id: str | None = None,
        target_session_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Application-wide message list filtered by authorship and target.

        ``role`` keeps the old ``inbox``/``outbox`` callers compatible. Those
        names were misleading because the partition has always been based on
        authorship, not direction relative to a Project or session.
        """
        if role is not None:
            author = {"inbox": "non_human", "outbox": "human"}.get(role, role)
        if author == "non_human":
            kinds = tuple(kind for kind in SENDER_KINDS if kind not in HUMAN_SENDER_KINDS)
        elif author == "human":
            kinds = tuple(sorted(HUMAN_SENDER_KINDS))
        else:
            kinds = SENDER_KINDS

        def op() -> list[dict[str, Any]]:
            placeholders = ",".join("?" for _ in kinds)
            conditions = ["state!='deleted'", f"sender_kind IN ({placeholders})"]
            parameters: list[Any] = list(kinds)
            if project_id:
                conditions.append("project_id=?")
                parameters.append(project_id)
            if target_session_id:
                conditions.append("target_session_id=?")
                parameters.append(target_session_id)
            rows = self._db.execute(
                f"SELECT * FROM queue_messages WHERE {' AND '.join(conditions)}"
                " ORDER BY created_at DESC LIMIT ?",
                (*parameters, max(1, min(limit, 500))),
            ).fetchall()
            return [_row_to_message(row) for row in rows]

        return await self._run(op)

    async def mailbox_targets(self) -> list[dict[str, Any]]:
        """Targets represented in retained mailbox rows, including ended sessions."""

        def op() -> list[dict[str, Any]]:
            rows = self._db.execute(
                "SELECT target_session_id, MAX(target_label) label, MAX(project_id) project_id,"
                " MAX(updated_at) updated_at FROM queue_messages WHERE state!='deleted'"
                " GROUP BY target_session_id"
                " ORDER BY updated_at DESC"
            ).fetchall()
            return [dict(row) for row in rows]

        return await self._run(op)

    async def sender_message_count(
        self, sender_kind: str, sender_id: str, since: float
    ) -> int:
        """How many messages one origin has staged since ``since`` (per-origin budget)."""

        def op() -> int:
            row = self._db.execute(
                "SELECT COUNT(*) n FROM queue_messages WHERE sender_kind=? AND sender_id=?"
                " AND created_at>=?",
                (sender_kind, sender_id, since),
            ).fetchone()
            return int(row["n"]) if row else 0

        return await self._run(op)

    async def pending_from_sender_kind(
        self, target_session_id: str, sender_kind: str
    ) -> int:
        """Outstanding pending items of one sender kind aimed at a target."""

        def op() -> int:
            row = self._db.execute(
                "SELECT COUNT(*) n FROM queue_messages WHERE target_session_id=?"
                " AND sender_kind=? AND state IN ('draft','armed','blocked','delivering')",
                (target_session_id, sender_kind),
            ).fetchone()
            return int(row["n"]) if row else 0

        return await self._run(op)

    async def inbound_relay_context(
        self, session_id: str, agent_run_id: str | None, peer_id: str | None = None
    ) -> dict[str, Any]:
        """The relay thread a session's next message to ``peer_id`` continues.

        Chain depth, the cycle path, and the thread identity are derived from
        the queue itself — the message rows already record who sent what to
        whom — so no separate relay-state table can drift out of sync with the
        audit trail.

        Which inbound message to inherit from is a policy question, not an
        arbitrary pick. When ``peer_id`` has messaged this session, the answer
        is *their most recent message*: that is the thread a reply belongs to.
        Otherwise the answer is the deepest live chain, because a message to a
        new session extends whichever chain travelled furthest to get here.
        Preferring the peer is what keeps one unrelated deep chain from wedging
        every other conversation a session is in.
        """

        def op() -> dict[str, Any]:
            # The run filter belongs in SQL: applying it after `LIMIT 1` lets a
            # row from a previous run mask the current run's real context and
            # silently report an unrelayed session.
            rows = self._db.execute(
                "SELECT * FROM queue_messages WHERE target_session_id=? AND sender_kind='agent'"
                " AND state='sent' AND (? IS NULL OR target_agent_run_id IS NULL"
                " OR target_agent_run_id=?) ORDER BY sent_at DESC LIMIT 100",
                (session_id, agent_run_id or None, agent_run_id or None),
            ).fetchall()
            items = [_row_to_message(row) for row in rows]
            chosen: dict[str, Any] | None = None
            if peer_id:
                chosen = next(
                    (item for item in items if str(item.get("sender_id") or "") == peer_id),
                    None,
                )
            if chosen is None and items:
                chosen = max(items, key=lambda item: int(item.get("chain_depth") or 0))
            if chosen is None:
                return {"depth": 0, "path": [], "thread_id": None, "from_session": None}
            origin = chosen.get("origin") or {}
            path = origin.get("path") if isinstance(origin, dict) else None
            return {
                "depth": int(chosen.get("chain_depth") or 0),
                "path": [str(entry) for entry in path] if isinstance(path, list) else [],
                "thread_id": str(chosen.get("thread_id") or "") or None,
                "from_session": str(chosen.get("sender_id") or "") or None,
            }

        return await self._run(op)

    async def thread_message_count(self, thread_id: str) -> int:
        """Agent messages staged in one relay thread, deleted rows excluded.

        This is the volume bound on a back-and-forth. Chain depth cannot serve
        that purpose once replies are allowed: a two-party exchange never
        reaches a new session, so its depth is constant no matter how long the
        two of them keep talking.
        """

        def op() -> int:
            row = self._db.execute(
                "SELECT COUNT(*) n FROM queue_messages WHERE thread_id=?"
                " AND sender_kind='agent' AND state!='deleted'",
                (thread_id,),
            ).fetchone()
            return int(row["n"]) if row else 0

        return await self._run(op)

    # -- Phase 5: auto-delivery policy and proving-period counters ------------

    async def auto_policy(self, session_id: str) -> dict[str, Any] | None:
        def op() -> dict[str, Any] | None:
            row = self._db.execute(
                "SELECT * FROM queue_auto_policy WHERE session_id=?", (session_id,)
            ).fetchone()
            return dict(row) if row else None

        return await self._run(op)

    async def auto_policies(
        self, session_ids: Collection[str] | None = None
    ) -> list[dict[str, Any]]:
        """Policy rows, optionally restricted to the given session ids.

        Rows are never deleted (an explicit opt-out or a failed-delivery hold must
        survive), so the table accumulates one row per session ever granted. The
        controller polls every second and only live sessions can be delivered to,
        so it passes their ids here rather than paying for the whole history —
        measured at 106 rows scanned per tick with 12 live sessions before the
        filter existed.
        """

        def op() -> list[dict[str, Any]]:
            query = "SELECT * FROM queue_auto_policy WHERE session_id!=?"
            params: list[str] = [AUTO_POLICY_GLOBAL]
            if session_ids is not None:
                if not session_ids:
                    return []
                placeholders = ",".join("?" * len(session_ids))
                query += f" AND session_id IN ({placeholders})"
                params.extend(session_ids)
            rows = self._db.execute(query + " ORDER BY updated_at DESC", params).fetchall()
            return [dict(row) for row in rows]

        return await self._run(op)

    async def set_auto_policy(self, session_id: str, **fields: Any) -> dict[str, Any]:
        """Upsert one policy row; only the named columns change."""
        allowed = {
            "enabled",
            "agent_run_id",
            "accept_agent_messages",
            "expires_at",
            "max_sends",
            "sends_used",
            "paused",
            "disabled_reason",
            "enabled_at",
            "updated_by",
        }
        unknown = set(fields) - allowed
        if unknown:
            raise QueueError(
                "invalid_policy", f"unknown policy fields: {sorted(unknown)}", status=400
            )
        now = time.time()

        def op() -> dict[str, Any]:
            self._db.execute(
                "INSERT INTO queue_auto_policy(session_id,updated_at) VALUES(?,?)"
                " ON CONFLICT(session_id) DO NOTHING",
                (session_id, now),
            )
            if fields:
                assignments = ", ".join(f"{name}=?" for name in fields)
                self._db.execute(
                    f"UPDATE queue_auto_policy SET {assignments}, updated_at=? WHERE session_id=?",
                    (*fields.values(), now, session_id),
                )
            else:
                self._db.execute(
                    "UPDATE queue_auto_policy SET updated_at=? WHERE session_id=?",
                    (now, session_id),
                )
            self._db.commit()
            row = self._db.execute(
                "SELECT * FROM queue_auto_policy WHERE session_id=?", (session_id,)
            ).fetchone()
            assert row is not None
            return dict(row)

        return await self._run(op)

    async def consume_auto_send(self, session_id: str, max_sends: int) -> bool:
        """Atomically take one auto-send slot; False when the budget is spent.

        The reservation happens before the PTY write, so a crash mid-send can
        only ever *lose* a slot, never let the cap be exceeded.
        """
        now = time.time()

        def op() -> bool:
            cursor = self._db.execute(
                "UPDATE queue_auto_policy SET sends_used=sends_used+1, updated_at=?"
                " WHERE session_id=? AND enabled=1 AND (?<=0 OR sends_used<?)",
                (now, session_id, max_sends, max_sends),
            )
            self._db.commit()
            return bool(cursor.rowcount)

        return await self._run(op)

    async def release_auto_send(self, session_id: str) -> None:
        """Give back a reserved slot when the attempt never reached the PTY."""
        now = time.time()

        def op() -> None:
            self._db.execute(
                "UPDATE queue_auto_policy SET sends_used=MAX(0,sends_used-1), updated_at=?"
                " WHERE session_id=?",
                (now, session_id),
            )
            self._db.commit()

        await self._run(op)

    async def reset_auto_sends(self, session_id: str) -> None:
        """A human send resets the consecutive-auto-send count.

        The cap exists to bound *unattended* runs; a manual delivery is direct
        evidence the user is at the keyboard. Never inserts a policy row — a
        session with no opt-in has nothing to reset.
        """
        now = time.time()

        def op() -> None:
            self._db.execute(
                "UPDATE queue_auto_policy SET sends_used=0, updated_at=? WHERE session_id=?",
                (now, session_id),
            )
            self._db.commit()

        await self._run(op)

    async def bump_counter(self, name: str, delta: float = 1.0) -> None:
        now = time.time()

        def op() -> None:
            self._db.execute(
                "INSERT INTO queue_auto_counters(name,value,updated_at) VALUES(?,?,?)"
                " ON CONFLICT(name) DO UPDATE SET value=value+excluded.value,"
                " updated_at=excluded.updated_at",
                (name, delta, now),
            )
            self._db.commit()

        await self._run(op)

    async def set_counter(self, name: str, value: float) -> None:
        now = time.time()

        def op() -> None:
            self._db.execute(
                "INSERT INTO queue_auto_counters(name,value,updated_at) VALUES(?,?,?)"
                " ON CONFLICT(name) DO UPDATE SET value=excluded.value,"
                " updated_at=excluded.updated_at",
                (name, value, now),
            )
            self._db.commit()

        await self._run(op)

    async def counters(self) -> dict[str, float]:
        def op() -> dict[str, float]:
            rows = self._db.execute("SELECT name, value FROM queue_auto_counters").fetchall()
            return {str(row["name"]): float(row["value"]) for row in rows}

        return await self._run(op)

    # -- stranding and reconciliation ----------------------------------------

    def _close_open_deliveries(self, message_id: str, error: str, now: float) -> None:
        """Worker-thread helper: no attempt row may stay ``pending`` forever."""
        self._db.execute(
            "UPDATE queue_deliveries SET outcome='failed', error=?, completed_at=?"
            " WHERE message_id=? AND outcome='pending'",
            (error, now, message_id),
        )

    async def fail_interrupted(self, message_id: str, error: str) -> dict[str, Any] | None:
        """Mark a ``delivering`` item failed (restart caught it mid-send)."""
        now = time.time()

        def op() -> dict[str, Any] | None:
            cursor = self._db.execute(
                "UPDATE queue_messages SET state='failed', stranded_reason=?, updated_at=?"
                " WHERE id=? AND state='delivering'",
                (error, now, message_id),
            )
            if cursor.rowcount:
                self._close_open_deliveries(message_id, error, now)
            self._db.commit()
            if not cursor.rowcount:
                return None
            fresh = self._db.execute(
                "SELECT * FROM queue_messages WHERE id=?", (message_id,)
            ).fetchone()
            return _row_to_message(fresh) if fresh else None

        return await self._run(op)

    async def strand_pending(
        self, target_session_id: str, reason: str
    ) -> list[dict[str, Any]]:
        """Strand every pending item for an ended/replaced target.

        A ``delivering`` item is different: its PTY write may or may not have
        landed, so it becomes ``failed`` (requiring user reconciliation), never
        a silently re-sendable pending item.
        """
        now = time.time()

        def op() -> list[dict[str, Any]]:
            rows = self._db.execute(
                "SELECT id, state FROM queue_messages WHERE target_session_id=?"
                " AND state IN ('draft','armed','blocked','delivering')",
                (target_session_id,),
            ).fetchall()
            changed: list[str] = []
            for row in rows:
                if row["state"] == "delivering":
                    error = f"delivery interrupted: {reason}"
                    self._db.execute(
                        "UPDATE queue_messages SET state='failed', updated_at=?,"
                        " stranded_reason=? WHERE id=?",
                        (now, error, row["id"]),
                    )
                    self._close_open_deliveries(str(row["id"]), error, now)
                else:
                    self._db.execute(
                        "UPDATE queue_messages SET state='stranded', stranded_reason=?,"
                        " updated_at=? WHERE id=?",
                        (reason, now, row["id"]),
                    )
                changed.append(str(row["id"]))
            if changed:
                self._db.commit()
            result: list[dict[str, Any]] = []
            for message_id in changed:
                fresh = self._db.execute(
                    "SELECT * FROM queue_messages WHERE id=?", (message_id,)
                ).fetchone()
                if fresh is not None:
                    result.append(_row_to_message(fresh))
            return result

        return await self._run(op)

    async def pending_targets(self) -> list[dict[str, Any]]:
        def op() -> list[dict[str, Any]]:
            rows = self._db.execute(
                "SELECT * FROM queue_messages"
                " WHERE state IN ('draft','armed','blocked','delivering')",
            ).fetchall()
            return [_row_to_message(row) for row in rows]

        return await self._run(op)

    async def prune(self, retention_days: int) -> None:
        """Age out terminal-state messages and their audit rows.

        Pending items (including stranded's pending siblings) never age out —
        only completed history does. Stranded items are terminal but must stay
        visible/exportable, so they get the same window as sent ones rather
        than an early cut.
        """
        cutoff = time.time() - max(1, retention_days) * 86400

        def op() -> None:
            rows = self._db.execute(
                "SELECT id FROM queue_messages WHERE state IN ('sent','failed','cancelled',"
                "'stranded','deleted') AND updated_at<?",
                (cutoff,),
            ).fetchall()
            ids = [str(row["id"]) for row in rows]
            for message_id in ids:
                self._db.execute("DELETE FROM queue_messages WHERE id=?", (message_id,))
                self._db.execute(
                    "DELETE FROM queue_deliveries WHERE message_id=?", (message_id,)
                )
            self._db.execute("DELETE FROM queue_deliveries WHERE created_at<?", (cutoff,))
            self._db.commit()

        await self._run(op)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._executor.submit(self._db.close).result()
        self._executor.shutdown(wait=True)


class PromptQueueService:
    """The typed daemon operations every queue client calls.

    The daemon owns ordering, revision checks, readiness, identity, and audit
    (`CONTROL_PLANE_ROADMAP.md` §7.1); the browser is just one caller, and the
    Phase 5 MCP tools become other callers of these same methods.
    """

    def __init__(
        self,
        store: PromptQueueStore,
        sessions: Any,
        events: Any,
        readiness: Any,
        write_operator_input: Callable[[Any, str], None],
        *,
        submit_delay: float = SUBMIT_DELAY_SECONDS,
    ) -> None:
        self.store = store
        self.sessions = sessions
        self.events = events
        self.readiness = readiness
        self._write = write_operator_input
        self._submit_delay = submit_delay
        self._queue: asyncio.Queue[Any] | None = None

    # -- lifecycle ------------------------------------------------------------

    async def start(self) -> None:
        await self._reconcile_startup()
        self._queue = self.events.subscribe(name="prompt-queue")
        background.start(QUEUE_EVENT_LOOP, self._consume)

    async def stop(self) -> None:
        await background.stop(QUEUE_EVENT_LOOP)
        if self._queue is not None:
            self.events.unsubscribe(self._queue)
            self._queue = None

    async def _consume(self) -> None:
        assert self._queue is not None
        while True:
            event = await self._queue.get()
            with background.iteration(QUEUE_EVENT_LOOP):
                if event.type in {"session_exited", "session_crashed"} and event.session_id:
                    await self._strand(event.session_id, "target session ended")
                elif event.type == "backend_demoted" and event.session_id:
                    await self._strand(
                        event.session_id, "target agent run ended (demoted to shell)"
                    )
                elif event.type == "agent_conversation_rolled" and event.session_id:
                    # An in-CLI `/clear` wipes the conversation the item was
                    # written for while the session itself lives on. Delivering
                    # into the successor is exactly the silent retarget the
                    # bind-on-first-run rule forbids, and before the run boundary
                    # existed this was the one way to get it.
                    await self._strand(
                        event.session_id, "target agent conversation was replaced"
                    )

    async def _reconcile_startup(self) -> None:
        """After adoption, strand pending items whose target did not survive.

        Sessions normally survive a daemon restart via the PTY supervisor; a
        target that is gone, ended, or running a different agent run than the
        one an item bound to is stranded here rather than silently retargeted.
        Items caught mid-``delivering`` become ``failed``: whether the PTY
        write landed is unknowable, and guessing either way risks a duplicate
        or lost delivery.
        """
        pending = await self.store.pending_targets()
        by_target: dict[str, list[dict[str, Any]]] = {}
        for item in pending:
            by_target.setdefault(str(item["target_session_id"]), []).append(item)
        for target_id, items in by_target.items():
            session = self.sessions.sessions.get(target_id)
            if session is None:
                await self._strand(target_id, "target session did not survive restart")
                continue
            record = session.record
            if record.state in {"exited", "crashed"}:
                await self._strand(target_id, "target session ended")
                continue
            for item in items:
                bound = item.get("target_agent_run_id")
                current = record.agent_run_id
                if bound and current and bound != current:
                    await self._strand(target_id, "target agent run was replaced")
                    break
                if item["state"] == "delivering":
                    await self.store.fail_interrupted(
                        str(item["id"]),
                        "delivery interrupted by daemon restart; verify the terminal",
                    )
                    await self._emit_updated(item["id"], target_id, "failed")

    async def _strand(self, target_session_id: str, reason: str) -> None:
        changed = await self.store.strand_pending(target_session_id, reason)
        for item in changed:
            await self._emit_updated(item["id"], target_session_id, str(item["state"]))

    async def _emit_updated(
        self, message_id: str, target_session_id: str, state: str
    ) -> None:
        summary = await self.store.messages_for_target(target_session_id)
        self.events.emit_background(
            "queue_updated",
            session_id=target_session_id,
            message_id=str(message_id),
            state=state,
            pending=int(summary["pending"]),
        )

    # -- target resolution ----------------------------------------------------

    def _live_target(self, target_session_id: str) -> Any:
        session = self.sessions.sessions.get(target_session_id)
        if session is None:
            raise QueueError("unknown_target", "no such session", status=404)
        record = session.record
        if not delivers_prompts_through_pty(record.backend):
            raise QueueError(
                "not_agent_target",
                "queues target agent sessions only (a shell would execute a paste)",
                status=400,
            )
        return session

    # -- typed operations -----------------------------------------------------

    async def enqueue(
        self,
        *,
        message_id: str | None = None,
        target_session_id: str,
        body: str,
        armed: bool = False,
        insert_after: str | None = None,
        sender_kind: str = "user",
        sender_id: str | None = None,
        sender_label: str | None = None,
        origin_session_id: str | None = None,
        correlation_id: str | None = None,
        thread_id: str | None = None,
        chain_depth: int = 0,
        origin: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
        constraints: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not body or len(body) > MAX_BODY_CHARS:
            raise QueueError(
                "invalid_body",
                f"body must contain 1–{MAX_BODY_CHARS} characters",
                status=400,
            )
        if sender_kind not in SENDER_KINDS:
            raise QueueError("invalid_sender", "unknown sender kind", status=400)
        constraints = normalize_constraints(constraints)
        # Only a human's own message may be staged armed by its author. An
        # agent-authored message may still *arrive* armed, but that is the
        # receiving session's standing policy granting it (see
        # `agent_messaging.py`), never the sender's claim.
        if sender_kind not in HUMAN_SENDER_KINDS and armed and sender_kind != "agent":
            armed = False
        session = self._live_target(target_session_id)
        record = session.record
        if record.state in {"exited", "crashed"}:
            raise QueueError("target_ended", "the target session has ended")
        message = await self.store.create_message(
            message_id=message_id,
            target_session_id=target_session_id,
            target_agent_run_id=record.agent_run_id or None,
            target_backend=record.backend,
            target_label=record.name,
            project_id=record.project_id or None,
            body=body,
            armed=armed,
            sender_kind=sender_kind,
            sender_id=sender_id,
            sender_label=sender_label,
            origin_session_id=origin_session_id,
            correlation_id=correlation_id,
            thread_id=thread_id,
            chain_depth=chain_depth,
            origin=origin,
            payload=payload,
            constraints=constraints,
            insert_after=insert_after,
        )
        if not message.get("deduplicated"):
            await self._emit_updated(
                message["id"], target_session_id, str(message["state"])
            )
        return message

    async def set_constraints(
        self, message_id: str, constraints: dict[str, Any] | None
    ) -> dict[str, Any]:
        """Schedule/expire a pending item (Phase 5 time-based delivery)."""
        message = await self.store.set_constraints(
            message_id, normalize_constraints(constraints)
        )
        await self._emit_updated(
            message_id, str(message["target_session_id"]), str(message["state"])
        )
        return message

    async def expire_due(self) -> list[dict[str, Any]]:
        """Cancel pending items whose expiry passed. Called by the auto controller."""
        now = time.time()
        expired: list[dict[str, Any]] = []
        for item in await self.store.expired_pending(now):
            message = await self.store.cancel(str(item["id"]), kind="expired")
            await self._emit_updated(
                str(item["id"]), str(item["target_session_id"]), str(message["state"])
            )
            expired.append(message)
        return expired

    async def edit(self, message_id: str, *, revision: int, body: str) -> dict[str, Any]:
        if not body or len(body) > MAX_BODY_CHARS:
            raise QueueError(
                "invalid_body",
                f"body must contain 1–{MAX_BODY_CHARS} characters",
                status=400,
            )
        message = await self.store.edit_body(message_id, revision, body)
        await self._emit_updated(
            message_id, str(message["target_session_id"]), str(message["state"])
        )
        return message

    async def set_armed(self, message_id: str, armed: bool) -> dict[str, Any]:
        message = await self.store.set_armed(message_id, armed)
        await self._emit_updated(
            message_id, str(message["target_session_id"]), str(message["state"])
        )
        return message

    async def move(self, message_id: str, *, after: str | None) -> dict[str, Any]:
        message = await self.store.move_message(message_id, after=after)
        await self._emit_updated(
            message_id, str(message["target_session_id"]), str(message["state"])
        )
        return message

    async def cancel(self, message_id: str, *, kind: str = "cancelled") -> dict[str, Any]:
        if kind not in CANCEL_KINDS:
            raise QueueError(
                "invalid_cancel_kind",
                f"kind must be one of {', '.join(CANCEL_KINDS)}",
                status=400,
            )
        message = await self.store.cancel(message_id, kind=kind)
        await self._emit_updated(
            message_id, str(message["target_session_id"]), str(message["state"])
        )
        return message

    async def delete(self, message_id: str) -> dict[str, Any]:
        result = await self.store.delete_message(message_id)
        if not result["already_deleted"]:
            await self._emit_updated(
                message_id, str(result["target_session_id"]), "deleted"
            )
        return result

    async def retarget(self, message_id: str, *, target_session_id: str) -> dict[str, Any]:
        session = self._live_target(target_session_id)
        record = session.record
        if record.state in {"exited", "crashed"}:
            raise QueueError("target_ended", "the target session has ended")
        message = await self.store.retarget(
            message_id,
            target_session_id=target_session_id,
            target_agent_run_id=record.agent_run_id or None,
            target_backend=record.backend,
            target_label=record.name,
            project_id=record.project_id or None,
        )
        await self._emit_updated(message_id, target_session_id, str(message["state"]))
        return message

    async def send_next(
        self,
        message_id: str,
        *,
        revision: int,
        idempotency_key: str | None = None,
        confirm: bool = False,
        initiator: str = "user",
    ) -> dict[str, Any]:
        """The one typed delivery operation: "send next now".

        Atomically re-checks pending state, revision, and head-of-line in the
        claim; then delivery constraints (schedule/expiry), target liveness,
        run identity, and delivery readiness immediately before the PTY write.
        Blocked or unknown readiness requires ``confirm=True``, and even then
        the protected reasons (approval/Q&A, identity, ended target) are never
        overridable.

        ``initiator`` records *who pressed send* in the audit row. The Phase 5
        auto-delivery controller is the only non-human caller and it never
        passes ``confirm=True``: an override is a human act by construction.
        """
        if initiator != "user" and confirm:
            raise QueueError(
                "confirm_requires_user",
                "only a human act may override a not-safe delivery",
                status=400,
            )
        claim = await self.store.claim_for_delivery(
            message_id, revision, idempotency_key, initiator=initiator
        )
        if claim["status"] == "duplicate":
            recorded = claim["delivery"]
            return {
                "status": "duplicate",
                "outcome": recorded["outcome"],
                "delivery_id": recorded["id"],
                "message_id": recorded["message_id"],
            }
        delivery_id = str(claim["delivery_id"])
        message = claim["message"]
        target_id = str(message["target_session_id"])

        async def refuse(
            code: str,
            error: str,
            *,
            message_state: str,
            delivery_state: str | None = None,
            reasons: list[str] | None = None,
            stranded_reason: str | None = None,
            cancel_kind: str | None = None,
            protected: bool = False,
            **payload: Any,
        ) -> QueueError:
            await self.store.finalize_delivery(
                delivery_id,
                message_id,
                outcome="refused",
                message_state=message_state,
                delivery_state=delivery_state,
                reasons=reasons,
                error=error,
                blocked_reasons=reasons if message_state == "blocked" else None,
                stranded_reason=stranded_reason,
                cancel_kind=cancel_kind,
            )
            await self._emit_updated(message_id, target_id, message_state)
            return QueueError(
                code,
                error,
                reasons=reasons or [],
                protected=protected,
                state=message_state,
                **payload,
            )

        # Delivery constraints are properties of the item, checked here so the
        # manual and automatic paths cannot diverge (`ROADMAP.md` Phase 5).
        constraints = message.get("constraints") or {}
        now = time.time()
        expires_at = constraints.get("expires_at")
        if isinstance(expires_at, int | float) and now >= float(expires_at):
            raise await refuse(
                "delivery_expired",
                "this message expired before it was sent",
                message_state="cancelled",
                cancel_kind="expired",
                protected=True,
            )
        not_before = constraints.get("not_before")
        if isinstance(not_before, int | float) and now < float(not_before) and not confirm:
            # Not a block: the item keeps its current state and its schedule.
            # Only a human "send now" (confirm) overrides the clock.
            raise await refuse(
                "delivery_not_due",
                "this message is scheduled for later; send now to override",
                message_state=str(message["state"]),
                not_before=float(not_before),
            )

        session = self.sessions.sessions.get(target_id)
        if session is None or session.record.state in {"exited", "crashed"}:
            raise await refuse(
                "target_ended",
                "the target session has ended; the message is stranded",
                message_state="stranded",
                stranded_reason="target session ended",
                protected=True,
            )
        record = session.record
        bound = message.get("target_agent_run_id")
        current = record.agent_run_id
        if bound and current and bound != current:
            raise await refuse(
                "target_run_replaced",
                "the target agent run was replaced; the message is stranded",
                message_state="stranded",
                stranded_reason="target agent run was replaced",
                protected=True,
            )
        if not bound and current:
            # Bind-on-first-run: an item staged against a still-starting
            # session belongs to the first run that session gets. Never re-bound.
            await self.store.bind_run(message_id, current)

        evaluation = self.readiness.evaluate(session)
        delivery_state = str(evaluation["delivery_state"])
        reasons = [str(reason) for reason in evaluation.get("reasons", [])]
        protected_reasons = sorted(set(reasons) & NON_OVERRIDABLE_REASONS)
        if record.state == "awaiting" and record.awaiting_reason in PROTECTED_AWAITING_REASONS:
            protected_reasons.append(f"awaiting_{record.awaiting_reason}")
        if protected_reasons:
            raise await refuse(
                "delivery_protected",
                "delivery is blocked by a protection that cannot be overridden",
                message_state="blocked",
                delivery_state=delivery_state,
                reasons=protected_reasons,
                protected=True,
            )
        confirmed = False
        if delivery_state != "safe":
            if not confirm:
                raise await refuse(
                    "delivery_not_safe",
                    f"delivery readiness is {delivery_state}; confirm to send anyway",
                    message_state="blocked",
                    delivery_state=delivery_state,
                    reasons=reasons,
                )
            confirmed = True

        body = str(message["body"])
        data = paste_payload(body)
        byte_count = len(data.encode("utf-8")) + len(SUBMIT_SEQUENCE)
        try:
            self._write(session, data)
            await asyncio.sleep(self._submit_delay)
            if session.record.state in {"exited", "crashed"}:
                raise RuntimeError("the target session ended during delivery")
            self._write(session, SUBMIT_SEQUENCE)
            # Who asked, recorded at the only moment it is knowable. The CLI is
            # about to fire a submit hook indistinguishable from one a person
            # typed, and the transcript will record the prompt identically —
            # authorship exists here and nowhere downstream. The observer reads
            # this to decide whether the submit refreshes `last_human_prompt_at`.
            session.queue_delivery_mark = (
                time.time(),
                str(message["sender_kind"]) in HUMAN_SENDER_KINDS,
            )
        except Exception as exc:
            await self.store.finalize_delivery(
                delivery_id,
                message_id,
                outcome="failed",
                message_state="failed",
                delivery_state=delivery_state,
                reasons=reasons,
                confirmed=confirmed,
                error=str(exc),
                byte_count=byte_count,
            )
            await self._emit_updated(message_id, target_id, "failed")
            self.events.emit_background(
                "queue_delivery",
                session_id=target_id,
                message_id=message_id,
                outcome="failed",
                delivery_state=delivery_state,
                confirmed=confirmed,
                initiator=initiator,
                bytes=byte_count,
            )
            raise QueueError(
                "delivery_failed", f"delivery failed: {exc}", status=502
            ) from exc
        final = await self.store.finalize_delivery(
            delivery_id,
            message_id,
            outcome="sent",
            message_state="sent",
            delivery_state=delivery_state,
            reasons=reasons,
            confirmed=confirmed,
            byte_count=byte_count,
        )
        await self._emit_updated(message_id, target_id, "sent")
        if initiator == "user":
            # Attention resets the unattended-run budget (`auto_delivery.py`).
            await self.store.reset_auto_sends(target_id)
        self.events.emit_background(
            "queue_delivery",
            session_id=target_id,
            message_id=message_id,
            outcome="sent",
            delivery_state=delivery_state,
            confirmed=confirmed,
            initiator=initiator,
            bytes=byte_count,
        )
        return {
            "status": "sent",
            "delivery_id": delivery_id,
            "confirmed": confirmed,
            "delivery_state": delivery_state,
            "initiator": initiator,
            "message": final,
        }

    # -- read surfaces --------------------------------------------------------

    async def target_view(self, target_session_id: str) -> dict[str, Any]:
        view = await self.store.messages_for_target(target_session_id)
        session = self.sessions.sessions.get(target_session_id)
        view["target"] = {
            "session_id": target_session_id,
            "live": bool(
                session is not None
                and session.record.state not in {"exited", "crashed"}
            ),
            "agent_run_id": session.record.agent_run_id if session else None,
            "label": session.record.name if session else None,
            "state": session.record.state if session else None,
        }
        return view

    async def summary(self) -> list[dict[str, Any]]:
        rows = await self.store.summary()
        for row in rows:
            session = self.sessions.sessions.get(str(row["target_session_id"]))
            row["live"] = bool(
                session is not None and session.record.state not in {"exited", "crashed"}
            )
            if session is not None:
                row["label"] = session.record.name
        return rows

    async def export_target(
        self, target_session_id: str, *, redact_secrets: bool
    ) -> dict[str, Any]:
        """Exportable snapshot of one queue; secrets excluded by user choice."""
        from .clipboard_store import looks_like_secret

        view = await self.store.messages_for_target(target_session_id)
        messages = []
        for item in view["messages"]:
            body = str(item["body"])
            if redact_secrets and looks_like_secret(body):
                item = {**item, "body": "[redacted: credential-shaped content]", "redacted": True}
            messages.append(item)
        return {"target_session_id": target_session_id, "messages": messages}


def normalize_constraints(constraints: Any) -> dict[str, Any] | None:
    """Validate and bound a message's delivery constraints.

    ``not_before`` and ``expires_at`` are absolute epoch seconds — the daemon's
    clock, not the browser's, is authoritative, and a horizon bound keeps a
    typo from parking an item in the queue for a decade. ``delay_seconds`` is
    accepted as a convenience and resolved to ``not_before`` here so exactly
    one representation is ever persisted.
    """
    if not constraints:
        return None
    if not isinstance(constraints, dict):
        raise QueueError("invalid_constraints", "constraints must be an object", status=400)
    result: dict[str, Any] = {}
    now = time.time()
    delay = constraints.get("delay_seconds")
    not_before = constraints.get("not_before")
    if not_before is None and isinstance(delay, int | float):
        not_before = now + float(delay)
    for key, value in (("not_before", not_before), ("expires_at", constraints.get("expires_at"))):
        if value is None:
            continue
        if not isinstance(value, int | float):
            raise QueueError(
                "invalid_constraints", f"{key} must be epoch seconds", status=400
            )
        moment = float(value)
        if moment > now + MAX_SCHEDULE_HORIZON_SECONDS:
            raise QueueError(
                "invalid_constraints",
                f"{key} is further out than the {MAX_SCHEDULE_HORIZON_SECONDS // 86400}-day"
                " scheduling horizon",
                status=400,
            )
        result[key] = moment
    if (
        "not_before" in result
        and "expires_at" in result
        and result["expires_at"] <= result["not_before"]
    ):
        raise QueueError(
            "invalid_constraints", "expires_at must be after not_before", status=400
        )
    return result or None


def schedule_status(message: dict[str, Any], now: float) -> str:
    """``due`` | ``scheduled`` | ``expired`` for one message, from its constraints."""
    constraints = message.get("constraints") or {}
    expires_at = constraints.get("expires_at")
    if isinstance(expires_at, int | float) and now >= float(expires_at):
        return "expired"
    not_before = constraints.get("not_before")
    if isinstance(not_before, int | float) and now < float(not_before):
        return "scheduled"
    return "due"


def paste_payload(message: str) -> str:
    """Bracketed-paste wrapper with newlines as CR — what xterm writes for a real paste."""
    normalized = message.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\r")
    return f"{BRACKETED_PASTE_START}{normalized}{BRACKETED_PASTE_END}"


def stage_seed_argv(cwd: str, text: str) -> str:
    """Turn a new-session seed body into a safe argv prompt.

    Bodies over the argv bound are written into the workspace
    (``.swe-mux/seeds/``, gitignored) and seeded with a short reader prompt —
    staged *inside* the project so both agent CLIs can read it without leaving
    their workspace. Old seed files are pruned opportunistically.
    """
    prompt = text if not text.startswith("-") else f" {text}"
    if len(text) <= ARGV_SEED_MAX_CHARS:
        return prompt
    seed_dir = Path(cwd) / ".swe-mux" / SEED_DIR_NAME
    seed_dir.mkdir(parents=True, exist_ok=True)
    ignore = seed_dir / ".gitignore"
    if not ignore.exists():
        ignore.write_text("*\n", encoding="utf-8")
    cutoff = time.time() - SEED_RETENTION_SECONDS
    for stale in seed_dir.glob("seed-*.md"):
        try:
            if stale.stat().st_mtime < cutoff:
                stale.unlink()
        except OSError:
            continue
    name = f"seed-{int(time.time())}-{uuid.uuid4().hex[:8]}.md"
    path = seed_dir / name
    path.write_text(text, encoding="utf-8")
    relative = path.relative_to(Path(cwd))
    return (
        f"Read the file {relative.as_posix()} and follow its contents as your"
        " instructions, exactly as if I had typed them here."
    )
