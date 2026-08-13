"""The mux MCP surface (roadmap Phase 4.5 reads, Phase 5 writes; CP §7.5).

A streamable-HTTP MCP endpoint hosted in the daemon - never the supervisor
(CP §7.3). Ten situational-awareness read tools over machinery that already exists and two
bounded write tools added in Phase 5: `notify` (a message into another
session's queue) and `request_spawn` (an inert draft; it starts nothing).

MCP is transport, not authority (CP §7.1): every tool is a thin caller over the
same typed daemon operations the browser routes use. No tool implements
delivery, and none can write to a PTY directly — a notify becomes an ordinary
queue item subject to head-of-line order, receiver readiness, and (by default)
human arming.

Load-bearing properties:

- **Caller identity is injected, never claimed** (CP §7.4). The bearer token is
  minted at spawn, carried in the session env, and recovered from supervisor
  meta across daemon restarts. No tool has a sender parameter.
- **Scope is the caller's Project.** Every tool filters to it; a target outside
  it answers "not found" rather than confirming existence.
- **Return nothing over a weak match.** Empty results are acceptable;
  plausible-but-wrong teaches an agent to stop calling (CP §7).
- **Bounded and redacted output.** Transcript reads are byte- and
  message-capped; any message or excerpt that looks credential-shaped is
  replaced, reusing the clipboard secret gate.
- **Same-host callers are fully trusted** (boundary decision 2026-07-28,
  re-affirmed for Phase 5 on 2026-07-29): the token scopes reads, attributes
  calls, and bounds *well-behaved* callers; it is not an authorization
  boundary, because a same-user process on the same host can reach the
  un-tokened HTTP surface directly no matter what this endpoint checks. The
  compensating design is that these tools grant strictly *less* authority than
  the browser already has — no delivery, no spawn, no PTY write — so a
  compromised agent gains nothing here it did not already have.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import secrets
from pathlib import Path
from typing import Any

from .clipboard_store import looks_like_secret
from .git_projects import ProjectIdentity
from .harness import agent_harnesses
from .project_files import (
    DEFAULT_NOTE_STORAGE_ID,
    project_note_summaries,
    read_note,
)
from .prompt_queue import QueueError
from .transcript_view import (
    conversation_is_readable,
    transcript_message_page,
)

log = logging.getLogger(__name__)

MCP_PROTOCOL_VERSION = "2025-06-18"
_SUPPORTED_PROTOCOL_VERSIONS = {"2024-11-05", "2025-03-26", "2025-06-18"}

# Bounds. A tool call must never pull an unbounded transcript into an agent's
# context; these mirror the automation slice service's budget.
TRANSCRIPT_MAX_BYTES = 512 * 1024


TRANSCRIPT_MAX_MESSAGES = 200
TRANSCRIPT_DEFAULT_MESSAGES = 50
LIST_MAX_SESSIONS = 100
SEARCH_MAX_LIMIT = 50
PARSE_TIMEOUT_SECONDS = 2.0
NOTE_MAX_BYTES = 512 * 1024
RUN_PROMPT_CHECKPOINT_PREFIX = "run-prompt:"

_REDACTED = "[redacted: credential-shaped content withheld by mux]"

# The retryable-error contract (CP §7.3): a daemon restart kills the TCP
# connection mid-call, which every MCP client already treats as retryable. The
# one server-visible aftermath is a token the daemon no longer knows — either
# the session ended or it predates MCP identity — and that must be typed
# clearly enough that an agent does not retry forever.
_UNKNOWN_TOKEN = (
    "unknown MCP token: this session has ended, or it was spawned before the "
    "mux MCP surface existed. If the daemon just restarted, live sessions keep "
    "their token — do not retry with this one."
)


def session_summary(record: Any, *, display_name: str | None = None) -> dict[str, Any]:
    """Explicit field allowlist for a session record.

    Never `record.snapshot()`: snapshots carry `spawn_env` (a raw environment
    dict) and anything a future field adds. An allowlist fails closed.
    """
    return {
        "session_id": record.id,
        "name": record.name,
        "display_name": display_name or record.name,
        "backend": record.backend,
        "state": record.state,
        "state_detail": record.state_detail,
        "awaiting_reason": record.awaiting_reason,
        "idle_reason": record.idle_reason,
        "model": record.model,
        "cwd": record.cwd,
        "project_id": record.project_id,
        "project_label": record.project_label,
        "agent_run_id": record.agent_run_id,
        # How many times this session's conversation has been replaced in place
        # (`/clear`, `/new`). Without it a caller that remembers a sibling's
        # agent_run_id cannot tell "a different session" from "the same session,
        # a conversation later" — and the second one means the agent it is talking
        # about has no memory of what it was told about.
        "agent_run_seq": record.agent_run_seq,
        "native_session_id": record.native_session_id,
        "created_at": record.created_at,
        "last_activity_ts": record.last_activity_ts,
        "tokens_in": record.tokens_in,
        "tokens_out": record.tokens_out,
        "context_pct": record.context_pct,
        "runtime_boundary": getattr(record, "runtime_boundary", "local"),
        "remote_authority": getattr(record, "remote_authority", None),
        "remote_transport_state": getattr(record, "remote_transport_state", None),
    }


def _history_summary(
    row: dict[str, Any], *, display_name: str | None = None
) -> dict[str, Any]:
    return {
        "session_id": row.get("id"),
        "name": row.get("name"),
        "display_name": display_name or row.get("name"),
        "backend": row.get("backend"),
        "state": row.get("final_state"),
        "model": row.get("model"),
        "cwd": row.get("cwd"),
        "project_id": row.get("project_id"),
        "project_label": row.get("project_label"),
        "agent_run_id": row.get("id"),
        "agent_run_seq": int(row.get("agent_run_seq") or 0),
        "native_session_id": row.get("native_id"),
        "spawned_at": row.get("spawned_at"),
        "exited_at": row.get("exited_at"),
        "last_message_at": row.get("last_message_at"),
        "tokens_in": row.get("tokens_in"),
        "tokens_out": row.get("tokens_out"),
        "ended": True,
    }


def _redact(text: str) -> str:
    return _REDACTED if text and looks_like_secret(text) else text


def _encode_cursor(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_cursor(value: str) -> dict[str, Any]:
    try:
        raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
        payload = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid transcript cursor") from exc
    if not isinstance(payload, dict) or payload.get("v") != 1:
        raise ValueError("invalid transcript cursor")
    return payload


def _bounded_utf8(text: str, limit: int) -> tuple[str, bool]:
    raw = text.encode("utf-8")
    if len(raw) <= limit:
        return text, False
    return raw[:limit].decode("utf-8", "ignore"), True


TOOLS: list[dict[str, Any]] = [
    {
        "name": "list_sessions",
        "description": (
            "List sessions in your Project: every live one (any backend), and "
            "optionally recently ended agent sessions. Status fields are the "
            "same ones the mux UI shows."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "include_ended": {
                    "type": "boolean",
                    "description": "Also list recently ended agent sessions (default false)",
                },
                "limit": {
                    "type": "integer",
                    "description": (
                        f"Maximum sessions returned (default 25, max {LIST_MAX_SESSIONS})"
                    ),
                },
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "get_session",
        "description": (
            "Status and metadata for one session in your Project, by session id "
            "or exact backend/display name. Live sessions report current state; ended ones "
            "report their final state."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "Session id or exact backend/display name",
                },
            },
            "required": ["session_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "read_transcript",
        "description": (
            "A bounded, pageable head or tail of one agent run's conversation. "
            "Every message names its agent_run_id and sequence; cursors cannot "
            "cross a conversation rollover. System/meta records are excluded "
            "unless explicitly requested."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "Session id or exact backend/display name",
                },
                "max_messages": {
                    "type": "integer",
                    "description": (
                        f"Messages in this page (default {TRANSCRIPT_DEFAULT_MESSAGES}, "
                        f"max {TRANSCRIPT_MAX_MESSAGES})"
                    ),
                },
                "from": {
                    "type": "string",
                    "enum": ["head", "tail"],
                    "description": "Read from the beginning or end (default tail)",
                },
                "cursor": {
                    "type": "string",
                    "description": "Opaque continuation cursor from a previous result",
                },
                "include_system": {
                    "type": "boolean",
                    "description": "Include system/meta records (default false)",
                },
            },
            "required": ["session_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "notify",
        "description": (
            "Send a short message to another agent session in your Project. It "
            "enters that session's prompt queue and waits: it never interrupts "
            "an active turn, never answers an approval prompt, and by default "
            "lands as an inert draft a human must approve. Use it to hand off "
            "or to flag something the other session needs; do not use it to "
            "issue instructions you would not want a human to read first."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "target": {
                    "type": "string",
                    "description": (
                        "Session id or exact backend/display name of the receiving session"
                    ),
                },
                "body": {"type": "string", "description": "The message text"},
                "reason": {
                    "type": "string",
                    "description": "Short note on why you are sending it (kept as provenance)",
                },
                "correlation_id": {
                    "type": "string",
                    "description": (
                        "Optional idempotency key: retrying with the same value "
                        "returns the original message instead of a duplicate"
                    ),
                },
            },
            "required": ["target", "body"],
            "additionalProperties": False,
        },
    },
    {
        "name": "request_spawn",
        "description": (
            "Ask the human to start a new agent session in your Project with a "
            "prompt you supply. This starts nothing: it writes an inert draft "
            "into the Fleet Queue, and a person decides. Use it "
            "when work should continue in a separate session."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "The prompt the new session would be seeded with",
                },
                "backend": {
                    "type": "string",
                    "enum": list(agent_harnesses()),
                    "description": "Preferred agent CLI (defaults to yours)",
                },
                "name": {"type": "string", "description": "Suggested session name"},
                "reason": {"type": "string", "description": "Why a separate session is warranted"},
            },
            "required": ["prompt"],
            "additionalProperties": False,
        },
    },
    {
        "name": "search_history",
        "description": (
            "Full-text search over your Project's archived agent conversations, "
            "with match excerpts. Returns nothing rather "
            "than weak matches."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "FTS query text"},
                "scope": {
                    "type": "string",
                    "enum": ["all", "user", "assistant", "metadata"],
                    "description": "Which side of the conversation to search (default all)",
                },
                "limit": {
                    "type": "integer",
                    "description": f"Maximum entries (default 10, max {SEARCH_MAX_LIMIT})",
                },
                "cursor": {
                    "type": "string",
                    "description": "Opaque pagination cursor from a previous result",
                },
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    {
        "name": "memory_sources",
        "description": (
            "List the root instruction and learned-memory sources available to "
            "agent harnesses for your Project. Source ids are opaque and may be "
            "passed to read_memory. Unsupported providers are reported honestly."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
    {
        "name": "read_memory",
        "description": (
            "Read one bounded Project instruction or learned-memory source from "
            "memory_sources by its opaque source id."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "source_id": {
                    "type": "string",
                    "description": "Opaque source id returned by memory_sources",
                },
            },
            "required": ["source_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "message_status",
        "description": (
            "Read the current delivery outcome of one message you sent with "
            "notify. Only the attributed sender can read it."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "message_id": {"type": "string", "description": "Message id from notify"},
            },
            "required": ["message_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "project_notes",
        "description": (
            "List the human-authored Project notes available to your session. "
            "This is read-only and never includes another Project or the global Scratchpad."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
    {
        "name": "read_project_note",
        "description": (
            "Read one bounded Project note by the opaque note id returned by project_notes."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "note_id": {
                    "type": "string",
                    "description": "Opaque note id returned by project_notes",
                },
            },
            "required": ["note_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "spawn_requests",
        "description": (
            "List the status of spawn requests attributed to your session. "
            "This is read-only; approval remains a human Fleet Queue action."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
]


class McpAuthError(Exception):
    """Raised when no live session owns the presented bearer token."""


class McpService:
    """The tool implementations, one thin layer over existing services."""

    def __init__(
        self,
        sessions: Any,
        history: Any,
        messaging: Any = None,
        automation_store: Any = None,
        agent_context: Any = None,
        projects: Any = None,
    ) -> None:
        self.sessions = sessions
        self.history = history
        # Phase 5 write tools. Absent (tests, minimal wiring) the tools still
        # list but answer that they are unavailable — never a partial write.
        self.messaging = messaging
        self.automation_store = automation_store
        self.agent_context = agent_context
        self.projects = projects
        self.calls = 0
        self.denied = 0
        self.writes = 0

    # ------------------------------------------------------------ identity

    def resolve_caller(self, authorization: str | None) -> Any:
        """The session that owns this bearer token, or raise.

        Linear scan with constant-time comparison per candidate: the fleet is
        tens of sessions, and a dict keyed by token would make the token a
        dict key (log-friendly, heap-dumpable) for no measurable win.
        """
        header = (authorization or "").strip()
        token = header[7:].strip() if header.lower().startswith("bearer ") else ""
        if token:
            for session in self.sessions.sessions.values():
                held = getattr(session, "mcp_token", "")
                if held and secrets.compare_digest(held, token):
                    return session
        self.denied += 1
        raise McpAuthError(_UNKNOWN_TOKEN)

    @staticmethod
    def _scope(caller: Any) -> tuple[str, str]:
        """(project_id, project_scope_id) the caller may read within."""
        record = caller.record
        return (str(record.project_id or ""), str(record.project_scope_id or ""))

    def _in_scope(self, caller: Any, record: Any) -> bool:
        project_id, scope_id = self._scope(caller)
        if project_id:
            return str(record.project_id or "") == project_id
        # Ungrouped caller: fall back to the git project identity.
        return bool(scope_id) and str(record.project_scope_id or "") == scope_id

    async def _generated_titles(self, run_ids: set[str]) -> dict[str, str]:
        """Latest generated UI title for each requested run."""
        if not run_ids or self.automation_store is None:
            return {}
        annotations = await self.automation_store.annotations(tag="title", limit=1000)
        titles: dict[str, str] = {}
        for annotation in annotations:
            run_id = str(annotation.get("agent_run_id") or "")
            title = str(annotation.get("content") or "").strip()
            if run_id in run_ids and title and run_id not in titles:
                titles[run_id] = title
        return titles

    @staticmethod
    def _record_run_id(record: Any) -> str:
        return str(record.agent_run_id or record.id)

    @staticmethod
    def _row_run_id(row: dict[str, Any]) -> str:
        return str(row.get("agent_run_id") or row.get("id") or "")

    def _record_display_name(self, record: Any, titles: dict[str, str]) -> str:
        generated = titles.get(self._record_run_id(record))
        if getattr(record, "auto_named", True) and generated:
            return generated
        return str(record.name)

    def _row_display_name(self, row: dict[str, Any], titles: dict[str, str]) -> str:
        generated = titles.get(self._row_run_id(row))
        if bool(row.get("auto_named", 1)) and generated:
            return generated
        return str(row.get("name") or "")

    async def _live_display_names(self, sessions: list[Any]) -> dict[str, str]:
        titles = await self._generated_titles(
            {self._record_run_id(session.record) for session in sessions}
        )
        return {
            session.record.id: self._record_display_name(session.record, titles)
            for session in sessions
        }

    async def _resolve_live(self, caller: Any, identity: str) -> tuple[Any, str]:
        """Resolve an id, backend name, or UI display name without weak matches."""
        scoped = [
            session
            for session in self.sessions.sessions.values()
            if self._in_scope(caller, session.record)
        ]
        by_id = [
            session
            for session in scoped
            if session.record.id == identity
            or str(session.record.agent_run_id or "") == identity
        ]
        if len(by_id) == 1:
            names = await self._live_display_names(by_id)
            return by_id[0], names[by_id[0].record.id]

        names = await self._live_display_names(scoped)
        matches = [
            session
            for session in scoped
            if session.record.name == identity or names[session.record.id] == identity
        ]
        if len(matches) != 1:
            raise KeyError(identity)
        return matches[0], names[matches[0].record.id]

    async def _history_display_names(
        self, rows: list[dict[str, Any]]
    ) -> dict[str, str]:
        titles = await self._generated_titles({self._row_run_id(row) for row in rows})
        return {
            str(row.get("id") or ""): self._row_display_name(row, titles) for row in rows
        }

    async def _resolve_history(
        self, caller: Any, identity: str
    ) -> tuple[dict[str, Any], str]:
        row = await self.history.history_entry(identity)
        if row and self._history_row_in_scope(caller, row):
            names = await self._history_display_names([row])
            return row, names[str(row.get("id") or "")]

        project_id, _scope_id = self._scope(caller)
        page = await self.history.history_page(
            project_id=project_id or "__ungrouped__",
            limit=LIST_MAX_SESSIONS,
        )
        rows = [
            item
            for item in page.get("items", [])
            if self._history_row_in_scope(caller, item)
        ]
        names = await self._history_display_names(rows)
        matches = [
            item
            for item in rows
            if item.get("name") == identity
            or names[str(item.get("id") or "")] == identity
        ]
        if len(matches) != 1:
            raise KeyError(identity)
        match = matches[0]
        return match, names[str(match.get("id") or "")]

    # --------------------------------------------------------------- tools

    async def list_sessions(self, caller: Any, args: dict[str, Any]) -> dict[str, Any]:
        limit = max(1, min(int(args.get("limit") or 25), LIST_MAX_SESSIONS))
        scoped = [
            session
            for session in self.sessions.sessions.values()
            if self._in_scope(caller, session.record)
        ][:limit]
        display_names = await self._live_display_names(scoped)
        live = [
            {
                **session_summary(
                    session.record,
                    display_name=display_names[session.record.id],
                ),
                "ended": False,
            }
            for session in scoped
        ]
        for item in live:
            if item["session_id"] == caller.record.id:
                item["you"] = True
        result: dict[str, Any] = {"sessions": live}
        if bool(args.get("include_ended")):
            project_id, _scope_id = self._scope(caller)
            page = await self.history.history_page(
                project_id=project_id or "__ungrouped__",
                limit=limit,
            )
            live_ids = {item["session_id"] for item in live}
            ended_rows = [
                row for row in page.get("items", []) if row.get("id") not in live_ids
            ][:limit]
            ended_names = await self._history_display_names(ended_rows)
            result["ended_sessions"] = [
                _history_summary(
                    row,
                    display_name=ended_names[str(row.get("id") or "")],
                )
                for row in ended_rows
            ]
        return result

    async def get_session(self, caller: Any, args: dict[str, Any]) -> dict[str, Any]:
        identity = str(args.get("session_id") or "").strip()
        if not identity:
            raise ValueError("session_id is required")
        try:
            session, display_name = await self._resolve_live(caller, identity)
        except KeyError:
            session = None
        if session is not None:
            result = {
                **session_summary(session.record, display_name=display_name),
                "ended": False,
            }
            result["run_brief"] = await self._run_brief(
                run_id=self._record_run_id(session.record),
                path=session.transcript_path,
                backend=str(session.record.backend or ""),
                native_id=str(session.record.native_session_id or "") or None,
                opening_request=getattr(session, "first_user_prompt", None),
            )
            if session.record.id == caller.record.id:
                reader = getattr(self.history, "agent_runs_for_session", None)
                rows = await reader(caller.record.id) if callable(reader) else []
                current_run = self._record_run_id(caller.record)
                result["superseded_runs"] = [
                    {**_history_summary(row), "own_superseded_run": True}
                    for row in rows
                    if self._row_run_id(row) != current_run
                ]
            return result
        row, display_name = await self._resolve_history(caller, identity)
        result = _history_summary(row, display_name=display_name)
        raw = row.get("transcript_path")
        result["run_brief"] = await self._run_brief(
            run_id=self._row_run_id(row),
            path=Path(str(raw)) if raw else None,
            backend=str(row.get("backend") or ""),
            native_id=str(row.get("native_id") or "") or None,
        )
        return result

    def _history_row_in_scope(self, caller: Any, row: dict[str, Any]) -> bool:
        project_id, scope_id = self._scope(caller)
        if project_id:
            return str(row.get("project_id") or "") == project_id
        return bool(scope_id) and str(row.get("project_scope_id") or "") == scope_id

    async def _run_brief(
        self,
        *,
        run_id: str,
        path: Path | None,
        backend: str,
        native_id: str | None,
        opening_request: str | None = None,
    ) -> dict[str, Any]:
        titles = await self._generated_titles({run_id})
        opening = str(opening_request or "").strip()
        checkpoint_reader = getattr(self.automation_store, "checkpoint", None)
        if not opening and callable(checkpoint_reader):
            checkpoint = await checkpoint_reader(f"{RUN_PROMPT_CHECKPOINT_PREFIX}{run_id}")
            opening = str((checkpoint or {}).get("text") or "").strip()
        if not opening and conversation_is_readable(path, backend, native_id):
            try:
                page = await asyncio.wait_for(
                    asyncio.to_thread(
                        transcript_message_page,
                        path,
                        backend,
                        direction="head",
                        anchor=None,
                        max_bytes=TRANSCRIPT_MAX_BYTES,
                        max_messages=8,
                        include_system=False,
                        native_id=native_id,
                    ),
                    timeout=PARSE_TIMEOUT_SECONDS,
                )
            except (OSError, TimeoutError):
                page = {"messages": []}
            opening = next(
                (
                    str(item.get("text") or "")
                    for item in page["messages"]
                    if item.get("role") == "user"
                ),
                "",
            )
        bounded, truncated = _bounded_utf8(opening, TRANSCRIPT_MAX_BYTES)
        return {
            "pinned_title": titles.get(run_id),
            "opening_request": _redact(bounded),
            "opening_request_truncated": truncated,
        }

    async def read_transcript(self, caller: Any, args: dict[str, Any]) -> dict[str, Any]:
        identity = str(args.get("session_id") or "").strip()
        if not identity:
            raise ValueError("session_id is required")
        max_messages = max(
            1,
            min(
                int(args.get("max_messages") or TRANSCRIPT_DEFAULT_MESSAGES),
                TRANSCRIPT_MAX_MESSAGES,
            ),
        )
        cursor_text = str(args.get("cursor") or "").strip()
        cursor = _decode_cursor(cursor_text) if cursor_text else None
        direction = str(args.get("from") or (cursor or {}).get("from") or "tail")
        if direction not in {"head", "tail"}:
            raise ValueError("from must be head or tail")
        include_system = bool(args.get("include_system", False))
        if cursor is not None:
            if "from" in args and cursor.get("from") != direction:
                raise ValueError("transcript cursor direction does not match from")
            if (
                "include_system" in args
                and bool(cursor.get("include_system")) != include_system
            ):
                raise ValueError(
                    "transcript cursor system-record setting does not match include_system"
                )
            direction = str(cursor.get("from") or "")
            include_system = bool(cursor.get("include_system"))
        path: Path | None = None
        backend = ""
        own_superseded_run = False
        try:
            session, _display_name = await self._resolve_live(caller, identity)
        except KeyError:
            session = None
        if session is not None:
            resolved_session_id = session.record.id
            run_id = self._record_run_id(session.record)
            run_seq = int(session.record.agent_run_seq or 0)
            boundary = getattr(session.record, "runtime_boundary", "local")
            if boundary != "local":
                return {
                    "session_id": resolved_session_id,
                    "agent_run_id": run_id,
                    "agent_run_seq": run_seq,
                    "own_superseded_run": False,
                    "from": direction,
                    "include_system": include_system,
                    "messages": [],
                    "note": "agent bridge unavailable across the terminal boundary",
                    "capability": "agent-bridge-unavailable",
                    "reason": (
                        "remote_terminal_boundary"
                        if boundary == "remote"
                        else "terminal_boundary_unknown"
                    ),
                }
            path = session.transcript_path
            backend = session.record.backend
            native_id = session.record.native_session_id
        else:
            row, _display_name = await self._resolve_history(caller, identity)
            raw = row.get("transcript_path")
            path = Path(raw) if raw else None
            backend = str(row.get("backend") or "")
            native_id = str(row.get("native_id") or "") or None
            resolved_session_id = str(row.get("id") or identity)
            run_id = self._row_run_id(row)
            run_seq = int(row.get("agent_run_seq") or 0)
            own_superseded_run = bool(
                str(row.get("note_id") or "") == str(caller.record.id)
                and run_id != self._record_run_id(caller.record)
            )
        if cursor is not None and str(cursor.get("run") or "") != run_id:
            raise ValueError("transcript cursor belongs to a different agent run")
        if not conversation_is_readable(path, backend, native_id):
            return {
                "session_id": resolved_session_id,
                "agent_run_id": run_id,
                "agent_run_seq": run_seq,
                "own_superseded_run": own_superseded_run,
                "from": direction,
                "include_system": include_system,
                "messages": [],
                "note": "no transcript available",
            }
        try:
            page = await asyncio.wait_for(
                asyncio.to_thread(
                    transcript_message_page,
                    path,
                    backend,
                    direction=direction,
                    anchor=(cursor or {}).get("anchor"),
                    max_bytes=TRANSCRIPT_MAX_BYTES,
                    max_messages=max_messages,
                    include_system=include_system,
                    native_id=native_id,
                ),
                timeout=PARSE_TIMEOUT_SECONDS,
            )
        except (OSError, TimeoutError):
            # Never a partial or fabricated result (CP §7.3): a parse that did
            # not finish is reported as exactly that, and the agent may retry.
            raise RuntimeError(
                "transient: transcript read did not complete; retry"
            ) from None
        next_cursor = None
        if page.get("next_anchor") is not None:
            next_cursor = _encode_cursor(
                {
                    "v": 1,
                    "run": run_id,
                    "from": direction,
                    "include_system": include_system,
                    "anchor": page["next_anchor"],
                }
            )
        messages = list(page.get("messages") or [])
        return {
            "session_id": resolved_session_id,
            "agent_run_id": run_id,
            "agent_run_seq": run_seq,
            "own_superseded_run": own_superseded_run,
            "from": direction,
            "include_system": include_system,
            "message_count": len(messages),
            "next_cursor": next_cursor,
            "messages": [
                {
                    "ordinal": index,
                    "message_id": item.get("message_id"),
                    "role": item.get("role"),
                    "ts": item.get("ts"),
                    "text": _redact(str(item.get("text") or "")),
                    "agent_run_id": run_id,
                    "agent_run_seq": run_seq,
                }
                for index, item in enumerate(messages)
            ],
        }

    async def search_history(self, caller: Any, args: dict[str, Any]) -> dict[str, Any]:
        query = str(args.get("query") or "").strip()
        if not query:
            raise ValueError("query is required")
        scope = str(args.get("scope") or "all")
        if scope not in {"all", "user", "assistant", "metadata"}:
            raise ValueError(f"unknown scope: {scope}")
        limit = max(1, min(int(args.get("limit") or 10), SEARCH_MAX_LIMIT))
        project_id, _scope_id = self._scope(caller)
        page = await self.history.history_page(
            query=query,
            search_scope=scope,
            project_id=project_id or "__ungrouped__",
            limit=limit,
            cursor=str(args.get("cursor") or "") or None,
        )
        items = []
        rows = list(page.get("items", []))
        display_names = await self._history_display_names(rows)
        for row in rows:
            summary = _history_summary(
                row,
                display_name=display_names[str(row.get("id") or "")],
            )
            summary["ended"] = row.get("final_state") not in (None, "", "running")
            matches = row.get("matches") or []
            summary["matches"] = [
                {
                    "role": match.get("role"),
                    "ts": match.get("ts"),
                    "excerpt": _redact(str(match.get("excerpt") or "")),
                }
                for match in matches
            ]
            summary["match_count"] = row.get("match_count")
            items.append(summary)
        return {"entries": items, "next_cursor": page.get("next_cursor")}

    def _caller_project(self, caller: Any) -> Any:
        project_id = str(caller.record.project_id or "")
        project = (
            self.projects.projects.get(project_id)
            if project_id and self.projects is not None
            else None
        )
        if project is None:
            raise ValueError("the caller has no registered Project context")
        return project

    def _context_service(self) -> Any:
        if self.agent_context is None:
            raise RuntimeError(
                "transient: the agent context service is not available on this daemon"
            )
        return self.agent_context

    async def memory_sources(self, caller: Any, _args: dict[str, Any]) -> dict[str, Any]:
        project = self._caller_project(caller)
        inventory = await asyncio.to_thread(
            self._context_service().inventory,
            project.id,
            project.name,
            project.root,
        )
        sources = [
            *inventory["instructions"]["items"],
            *inventory["global_instructions"]["items"],
            *[
                item
                for provider in inventory["providers"]
                for item in provider["items"]
            ],
        ]
        return {
            "project": inventory["project"],
            "sources": sources,
            "providers": [
                {
                    "id": provider["id"],
                    "label": provider["label"],
                    "status": provider["status"],
                    "detail": provider.get("detail"),
                    "item_count": provider["item_count"],
                    "truncated": provider["truncated"],
                }
                for provider in inventory["providers"]
            ],
        }

    async def read_memory(self, caller: Any, args: dict[str, Any]) -> dict[str, Any]:
        source_id = str(args.get("source_id") or "").strip()
        if not source_id:
            raise ValueError("source_id is required")
        project = self._caller_project(caller)
        result = await asyncio.to_thread(
            self._context_service().read_source,
            project.root,
            source_id,
        )
        content = str(result["text"])
        redacted = bool(content and looks_like_secret(content))
        return {
            "project": {"id": project.id, "name": project.name},
            "source": result["source"],
            "text": _REDACTED if redacted else content,
            "redacted": redacted,
        }

    async def project_notes(self, caller: Any, _args: dict[str, Any]) -> dict[str, Any]:
        project = self._caller_project(caller)
        items = await asyncio.to_thread(
            project_note_summaries,
            project.root,
            default_note_id=project.id,
            default_title=f"{project.name} notes",
            migrate_legacy=False,
        )
        return {
            "project": {"id": project.id, "name": project.name},
            "notes": [
                {
                    "note_id": item["note_id"],
                    "title": item["title"],
                    "created_at": item["created_at"],
                    "updated_at": item["updated_at"],
                    "bytes": item["bytes"],
                    "revision": item["revision"],
                    "excerpt": _redact(str(item.get("excerpt") or "")),
                    "origin_session_id": item.get("origin_session_id"),
                }
                for item in items
            ],
        }

    async def read_project_note(
        self, caller: Any, args: dict[str, Any]
    ) -> dict[str, Any]:
        note_id = str(args.get("note_id") or "").strip()
        if not note_id:
            raise ValueError("note_id is required")
        project = self._caller_project(caller)
        inventory = await self.project_notes(caller, {})
        summary = next(
            (item for item in inventory["notes"] if item["note_id"] == note_id),
            None,
        )
        if summary is None:
            raise QueueError(
                "unknown_note", "no such note in your Project", status=404
            )
        storage_id = DEFAULT_NOTE_STORAGE_ID if note_id == project.id else note_id
        identity = ProjectIdentity(project.id, project.name, project.root, "registered")
        note = await read_note(
            project.root,
            storage_id,
            default_title=str(summary["title"]),
            project=identity,
        )
        if not note.get("exists"):
            raise QueueError(
                "unknown_note", "no such note in your Project", status=404
            )
        markdown, truncated = _bounded_utf8(str(note.get("markdown") or ""), NOTE_MAX_BYTES)
        redacted = bool(markdown and looks_like_secret(markdown))
        return {
            "project": {"id": project.id, "name": project.name},
            "note": summary,
            "markdown": _REDACTED if redacted else markdown,
            "truncated": truncated,
            "redacted": redacted,
        }

    async def message_status(self, caller: Any, args: dict[str, Any]) -> dict[str, Any]:
        message_id = str(args.get("message_id") or "").strip()
        if not message_id:
            raise ValueError("message_id is required")
        return dict(await self._messaging().message_status(caller, message_id))

    async def spawn_requests(
        self, caller: Any, _args: dict[str, Any]
    ) -> dict[str, Any]:
        return dict(await self._messaging().spawn_requests(caller))

    # ----------------------------------------------------------- write tools

    def _messaging(self) -> Any:
        if self.messaging is None:
            raise RuntimeError(
                "transient: the mux messaging service is not available on this daemon"
            )
        return self.messaging

    async def notify(self, caller: Any, args: dict[str, Any]) -> dict[str, Any]:
        """`mux.notify`: a caller over the Phase 5 A→B queue operation.

        Every bound (allowlist, size, budget, chain depth, cycle detection,
        receiver readiness, kill switch) lives in the daemon operation, not
        here — that is the whole point of MCP being transport and not authority
        (`CONTROL_PLANE_ROADMAP.md` §7.1). The sender is the token's session;
        there is no sender argument to forge.
        """
        self.writes += 1
        target = str(args.get("target") or "")
        if target:
            try:
                target_session, _display_name = await self._resolve_live(caller, target)
            except KeyError:
                pass
            else:
                target = target_session.record.id
        result = await self._messaging().notify(
            caller,
            target=target,
            body=str(args.get("body") or ""),
            reason=str(args.get("reason") or ""),
            correlation_id=str(args.get("correlation_id") or "") or None,
        )
        return dict(result)

    async def request_spawn(self, caller: Any, args: dict[str, Any]) -> dict[str, Any]:
        """`mux.requestSpawn`: a draft producer. It starts nothing."""
        self.writes += 1
        result = await self._messaging().request_spawn(
            caller,
            prompt=str(args.get("prompt") or ""),
            backend=str(args.get("backend") or ""),
            name=str(args.get("name") or ""),
            reason=str(args.get("reason") or ""),
        )
        return dict(result)

    # ------------------------------------------------------------ protocol

    async def dispatch_tool(self, caller: Any, name: str, args: dict[str, Any]) -> Any:
        self.calls += 1
        log.info(
            "MCP tool call tool=%s caller_session=%s project=%s",
            name,
            caller.record.id,
            caller.record.project_id,
        )
        handlers = {
            "list_sessions": self.list_sessions,
            "get_session": self.get_session,
            "read_transcript": self.read_transcript,
            "search_history": self.search_history,
            "memory_sources": self.memory_sources,
            "read_memory": self.read_memory,
            "project_notes": self.project_notes,
            "read_project_note": self.read_project_note,
            "message_status": self.message_status,
            "spawn_requests": self.spawn_requests,
            "notify": self.notify,
            "request_spawn": self.request_spawn,
        }
        handler = handlers.get(name)
        if handler is None:
            raise ValueError(f"unknown tool: {name}")
        return await handler(caller, args)

    async def handle_rpc(self, caller: Any, message: dict[str, Any]) -> dict[str, Any] | None:
        """One JSON-RPC message → response object, or None for notifications."""
        method = str(message.get("method") or "")
        message_id = message.get("id")
        if method.startswith("notifications/"):
            return None
        if message_id is None:
            return None

        def ok(result: Any) -> dict[str, Any]:
            return {"jsonrpc": "2.0", "id": message_id, "result": result}

        def error(code: int, text: str) -> dict[str, Any]:
            return {"jsonrpc": "2.0", "id": message_id, "error": {"code": code, "message": text}}

        raw_params = message.get("params")
        params: dict[str, Any] = raw_params if isinstance(raw_params, dict) else {}
        if method == "initialize":
            requested = str(params.get("protocolVersion") or "")
            version = (
                requested
                if requested in _SUPPORTED_PROTOCOL_VERSIONS
                else MCP_PROTOCOL_VERSION
            )
            return ok(
                {
                    "protocolVersion": version,
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "mux", "version": "0.1.0"},
                        "instructions": (
                            "Visibility into your swe-mux Project: sibling sessions, "
                            "their live status and run briefs, pageable transcripts, "
                            "archived conversation search, Project notes, message and "
                            "spawn-request status, and exact Agent Context source reads. Results "
                        "are scoped to your Project; an empty "
                        "result means nothing relevant exists. Two bounded write "
                        "tools exist: `notify` puts a message into another "
                        "session's prompt queue (it waits for that session's "
                        "readiness and, by default, for a human to approve it), "
                            "and `request_spawn` drafts a new-session request in the "
                            "Fleet Queue for a human to approve. It starts nothing."
                    ),
                }
            )
        if method == "ping":
            return ok({})
        if method == "tools/list":
            return ok({"tools": TOOLS})
        if method == "tools/call":
            name = str(params.get("name") or "")
            raw_arguments = params.get("arguments")
            arguments: dict[str, Any] = (
                raw_arguments if isinstance(raw_arguments, dict) else {}
            )
            try:
                result = await self.dispatch_tool(caller, name, arguments)
            except QueueError as exc:
                # A refused write is a *result*, not a protocol error: the
                # agent needs the typed code to decide whether to adapt (too
                # large, budget spent) or stop (disabled, cycle).
                return ok(
                    {
                        "content": [
                            {
                                "type": "text",
                                "text": json.dumps(
                                    {"error": exc.code, "message": str(exc), **exc.payload},
                                    default=str,
                                ),
                            }
                        ],
                        "isError": True,
                    }
                )
            except KeyError:
                # Scope miss and true miss answer identically: not found.
                return ok(
                    {
                        "content": [
                            {
                                "type": "text",
                                "text": "no such session in your Project",
                            }
                        ],
                        "isError": True,
                    }
                )
            except (ValueError, TypeError) as exc:
                return error(-32602, str(exc))
            except RuntimeError as exc:
                return ok({"content": [{"type": "text", "text": str(exc)}], "isError": True})
            return ok(
                {
                    "content": [
                        {"type": "text", "text": json.dumps(result, default=str)}
                    ],
                    "isError": False,
                }
            )
        return error(-32601, f"method not found: {method}")

    def status(self) -> dict[str, Any]:
        return {"calls": self.calls, "denied": self.denied, "writes": self.writes}
