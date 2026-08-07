"""The mux MCP surface (roadmap Phase 4.5 reads, Phase 5 writes; CP §7.5).

A streamable-HTTP MCP endpoint hosted in the daemon — never the supervisor
(CP §7.3). Four read tools over machinery that already exists (live session
listing, session status, bounded transcript read, history search) and two
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
import json
import secrets
from pathlib import Path
from typing import Any

from .clipboard_store import looks_like_secret
from .harness import agent_harnesses
from .prompt_queue import QueueError
from .transcript_view import parse_transcript_cached, searchable_transcript_messages

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


def session_summary(record: Any) -> dict[str, Any]:
    """Explicit field allowlist for a session record.

    Never `record.snapshot()`: snapshots carry `spawn_env` (a raw environment
    dict) and anything a future field adds. An allowlist fails closed.
    """
    return {
        "session_id": record.id,
        "name": record.name,
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
    }


def _history_summary(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "session_id": row.get("id"),
        "name": row.get("name"),
        "backend": row.get("backend"),
        "state": row.get("final_state"),
        "model": row.get("model"),
        "cwd": row.get("cwd"),
        "project_id": row.get("project_id"),
        "project_label": row.get("project_label"),
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
            "or exact name. Live sessions report current state; ended ones "
            "report their final state."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {"type": "string", "description": "Session id or exact name"},
            },
            "required": ["session_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "read_transcript",
        "description": (
            "The tail of a session's conversation transcript (role, timestamp, "
            "text), bounded. Works for live and recently ended agent sessions "
            "in your Project."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {"type": "string", "description": "Session id or exact name"},
                "max_messages": {
                    "type": "integer",
                    "description": (
                        f"Messages from the tail (default {TRANSCRIPT_DEFAULT_MESSAGES}, "
                        f"max {TRANSCRIPT_MAX_MESSAGES})"
                    ),
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
                    "description": "Session id or exact name of the receiving session",
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
            "into the Project's observation inbox, and a person decides. Use it "
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
]


class McpAuthError(Exception):
    """Raised when no live session owns the presented bearer token."""


class McpService:
    """The tool implementations, one thin layer over existing services."""

    def __init__(self, sessions: Any, history: Any, messaging: Any = None) -> None:
        self.sessions = sessions
        self.history = history
        # Phase 5 write tools. Absent (tests, minimal wiring) the tools still
        # list but answer that they are unavailable — never a partial write.
        self.messaging = messaging
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

    # --------------------------------------------------------------- tools

    async def list_sessions(self, caller: Any, args: dict[str, Any]) -> dict[str, Any]:
        limit = max(1, min(int(args.get("limit") or 25), LIST_MAX_SESSIONS))
        live = [
            {**session_summary(session.record), "ended": False}
            for session in self.sessions.sessions.values()
            if self._in_scope(caller, session.record)
        ][:limit]
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
            result["ended_sessions"] = [
                _history_summary(row)
                for row in page.get("items", [])
                if row.get("id") not in live_ids
            ][:limit]
        return result

    async def get_session(self, caller: Any, args: dict[str, Any]) -> dict[str, Any]:
        identity = str(args.get("session_id") or "").strip()
        if not identity:
            raise ValueError("session_id is required")
        try:
            session = self.sessions.resolve(identity)
        except KeyError:
            session = None
        if session is not None:
            if not self._in_scope(caller, session.record):
                raise KeyError(identity)
            return {**session_summary(session.record), "ended": False}
        row = await self.history.history_entry(identity)
        if not row or not self._history_row_in_scope(caller, row):
            raise KeyError(identity)
        return _history_summary(row)

    def _history_row_in_scope(self, caller: Any, row: dict[str, Any]) -> bool:
        project_id, scope_id = self._scope(caller)
        if project_id:
            return str(row.get("project_id") or "") == project_id
        return bool(scope_id) and str(row.get("project_scope_id") or "") == scope_id

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
        path: Path | None = None
        backend = ""
        try:
            session = self.sessions.resolve(identity)
        except KeyError:
            session = None
        if session is not None:
            if not self._in_scope(caller, session.record):
                raise KeyError(identity)
            path = session.transcript_path
            backend = session.record.backend
        else:
            row = await self.history.history_entry(identity)
            if not row or not self._history_row_in_scope(caller, row):
                raise KeyError(identity)
            raw = row.get("transcript_path")
            path = Path(raw) if raw else None
            backend = str(row.get("backend") or "")
        if path is None or not Path(path).is_file():
            return {"session_id": identity, "messages": [], "note": "no transcript available"}
        try:
            messages = await asyncio.wait_for(
                asyncio.to_thread(
                    parse_transcript_cached,
                    Path(path),
                    backend,
                    max_bytes=TRANSCRIPT_MAX_BYTES,
                ),
                timeout=PARSE_TIMEOUT_SECONDS,
            )
        except (OSError, TimeoutError):
            # Never a partial or fabricated result (CP §7.3): a parse that did
            # not finish is reported as exactly that, and the agent may retry.
            raise RuntimeError(
                "transient: transcript read did not complete; retry"
            ) from None
        searchable = searchable_transcript_messages(messages)[-max_messages:]
        return {
            "session_id": identity,
            "message_count": len(searchable),
            "truncated_to_tail": True,
            "messages": [
                {
                    "ordinal": item.get("ordinal"),
                    "role": item.get("role"),
                    "ts": item.get("ts"),
                    "text": _redact(str(item.get("text") or "")),
                }
                for item in searchable
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
        for row in page.get("items", []):
            summary = _history_summary(row)
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
        result = await self._messaging().notify(
            caller,
            target=str(args.get("target") or ""),
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
        handlers = {
            "list_sessions": self.list_sessions,
            "get_session": self.get_session,
            "read_transcript": self.read_transcript,
            "search_history": self.search_history,
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
                        "their live status, transcripts, and archived conversation "
                        "search. Results are scoped to your Project; an empty "
                        "result means nothing relevant exists. Two bounded write "
                        "tools exist: `notify` puts a message into another "
                        "session's prompt queue (it waits for that session's "
                        "readiness and, by default, for a human to approve it), "
                        "and `request_spawn` drafts a new-session request for a "
                        "human to approve — it starts nothing."
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
