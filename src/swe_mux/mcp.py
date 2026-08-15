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
- **Scope defaults to the caller's Project and widens only on request.** Every
  tool takes a `project` argument: omitted means the caller's own Project,
  `"fleet"` means every Project, and a Project name or id means that one. A
  target outside the requested scope answers "not found", and the refusal names
  the argument that would have found it — a default that cannot be discovered is
  a default an agent reads as a prohibition.
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
import hashlib
import json
import logging
import math
import secrets
from datetime import datetime
from pathlib import Path
from typing import Any

from .clipboard_store import looks_like_secret
from .git_projects import ProjectIdentity
from .harness import agent_harnesses, is_agent_harness
from .mcp_contract import READ_TOOL_NAMES, WRITE_TOOL_NAMES
from .project_actions import project_actions_schema
from .project_files import (
    DEFAULT_NOTE_STORAGE_ID,
    project_note_summaries,
    read_note,
)
from .project_scope import (
    ProjectScope,
    own_scope,
    record_scope,
    resolve_project_scope,
    row_scope,
    split_qualified_target,
)
from .prompt_queue import QueueError
from .transcript_view import (
    conversation_is_readable,
    transcript_message_page,
)

log = logging.getLogger(__name__)

MCP_PROTOCOL_VERSION = "2025-06-18"
_SUPPORTED_PROTOCOL_VERSIONS = {"2024-11-05", "2025-03-26", "2025-06-18"}

# Bounds. Search keeps the corpus server-side and returns small retrieval hits.
# Full transcript browsing remains available when explicitly requested.
TRANSCRIPT_MAX_BYTES = 512 * 1024
TRANSCRIPT_MAX_MESSAGES = 200
TRANSCRIPT_DEFAULT_MESSAGES = 12
TRANSCRIPT_DEFAULT_OUTPUT_BYTES = 32 * 1024
HIT_DEFAULT_OUTPUT_BYTES = 16 * 1024
LIST_MAX_SESSIONS = 25
LIST_HISTORY_SCAN_LIMIT = 100
LIST_MAX_BYTES = 32 * 1024
SEARCH_DEFAULT_LIMIT = 8
SEARCH_MAX_LIMIT = 50
SEARCH_DEFAULT_OUTPUT_BYTES = 16 * 1024
SEARCH_MAX_OUTPUT_BYTES = 64 * 1024
PARSE_TIMEOUT_SECONDS = 2.0
NOTE_MAX_BYTES = 512 * 1024
RUN_PROMPT_CHECKPOINT_PREFIX = "run-prompt:"
#: Projects one fleet-wide inventory read walks before it reports a truncation.
#: An inventory is a per-Project filesystem read, so the fleet form is bounded
#: the way every other tool is rather than being unbounded because it is a list.
FLEET_INVENTORY_MAX_PROJECTS = 25

#: The `project` argument, identical on every tool that has one. Written once so
#: the wording an agent reads cannot drift between tools.
_PROJECT_ARG = {
    "type": "string",
    "description": (
        "Which Project to read: omit for your own (the default), "
        '"fleet" for every Project, or a Project name or id for one other Project'
    ),
}

# list_sessions ordering. The caller first, then its own Project, then anything a
# widened call added: a fleet listing must not push a caller's own siblings off
# the first page.
_LIST_RANK_YOU = 0
_LIST_RANK_LIVE_OWN = 1
_LIST_RANK_LIVE_OTHER = 2
_LIST_RANK_ENDED_OWN = 3
_LIST_RANK_ENDED_OTHER = 4

_NOT_FOUND = (
    "no such session in your Project. Pass project:\"fleet\" to address every "
    'Project, or project:"<name>" for one other Project.'
)

_REDACTED = "[redacted: credential-shaped content withheld by mux]"

# The most terminal output one `get_session` call may return. Sized against a
# failing test run's tail, which is the case this exists for, and well under the
# 512 KiB read cap the transcript surface uses: a caller wanting more of a shell's
# history wants the pane, not a tool result.
MAX_SESSION_OUTPUT_BYTES = 64 * 1024

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
        # How this session ends and how it ended. Without them a caller that ran a
        # Project Action could see the session leave `running` and had no way to
        # learn whether the command succeeded, which made running one pointless.
        # `one_shot` distinguishes a task that is meant to finish from an
        # interactive pane, so `exit_code: null` reads as "still running" rather
        # than as "no result".
        "completion_mode": getattr(record, "completion_mode", "interactive"),
        "exit_code": getattr(record, "exit_code", None),
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


def _session_list_summary(
    record: Any, *, display_name: str | None = None
) -> dict[str, Any]:
    """Compact fleet entry; callers use get_session for full metadata."""
    return {
        "session_id": record.id,
        "name": record.name,
        "display_name": display_name or record.name,
        "backend": record.backend,
        "state": record.state,
        "state_detail": record.state_detail,
        "awaiting_reason": record.awaiting_reason,
        "model": record.model,
        "agent_run_id": record.agent_run_id,
        "agent_run_seq": record.agent_run_seq,
        "last_activity_ts": record.last_activity_ts,
        "runtime_boundary": getattr(record, "runtime_boundary", "local"),
        # Present on every entry, not only fleet-wide ones: a caller that pages
        # a widened list must be able to tell which Project each row came from,
        # and a caller that later widens must not see the shape of a row change.
        "project_id": record.project_id,
        "project_label": record.project_label,
        "ended": False,
    }


def _history_list_summary(
    row: dict[str, Any], *, display_name: str | None = None
) -> dict[str, Any]:
    return {
        "session_id": row.get("id"),
        "name": row.get("name"),
        "display_name": display_name or row.get("name"),
        "backend": row.get("backend"),
        "state": row.get("final_state"),
        "model": row.get("model"),
        "agent_run_id": row.get("id"),
        "agent_run_seq": int(row.get("agent_run_seq") or 0),
        "last_activity_ts": row.get("last_message_at") or row.get("exited_at"),
        "project_id": row.get("project_id"),
        "project_label": row.get("project_label"),
        "ended": True,
    }


def _redact(text: str) -> str:
    return _REDACTED if text and looks_like_secret(text) else text


def _encode_cursor(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_cursor(value: str, *, label: str = "transcript") -> dict[str, Any]:
    try:
        raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
        payload = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid {label} cursor") from exc
    if not isinstance(payload, dict) or payload.get("v") != 1:
        raise ValueError(f"invalid {label} cursor")
    return payload


def _bounded_utf8(text: str, limit: int) -> tuple[str, bool]:
    raw = text.encode("utf-8")
    if len(raw) <= limit:
        return text, False
    return raw[:limit].decode("utf-8", "ignore"), True


def _parse_time_bound(value: Any, label: str) -> float | None:
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        raise ValueError(f"{label} must be an epoch timestamp or ISO-8601 date-time")
    if isinstance(value, (int, float)):
        stamp = float(value)
    elif isinstance(value, str):
        text = value.strip()
        try:
            stamp = float(text)
        except ValueError:
            try:
                stamp = datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
            except ValueError as exc:
                raise ValueError(
                    f"{label} must be an epoch timestamp or ISO-8601 date-time"
                ) from exc
    else:
        raise ValueError(f"{label} must be an epoch timestamp or ISO-8601 date-time")
    if not math.isfinite(stamp):
        raise ValueError(f"{label} must be finite")
    return stamp


def _string_list(value: Any, label: str, *, maximum: int = 50) -> tuple[str, ...]:
    if value in (None, ""):
        return ()
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(f"{label} must be an array of strings")
    cleaned = tuple(dict.fromkeys(item.strip() for item in value if item.strip()))
    if len(cleaned) > maximum:
        raise ValueError(f"{label} accepts at most {maximum} values")
    return cleaned


def _query_signature(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:20]


def _bounded_message_texts(
    messages: list[dict[str, Any]], max_output_bytes: int
) -> tuple[list[dict[str, Any]], bool, int]:
    """Keep every selected message while sharing a hard text budget fairly."""
    if not messages:
        return [], False, 0
    output: list[dict[str, Any]] = []
    truncated = False
    used = 0
    for index, message in enumerate(messages):
        remaining_messages = len(messages) - index
        quota = max(0, (max_output_bytes - used) // remaining_messages)
        text, cut = _bounded_utf8(str(message.get("text") or ""), quota)
        item = {**message, "text": text, "text_truncated": cut}
        output.append(item)
        truncated = truncated or cut
        used += len(text.encode("utf-8"))
    return output, truncated, used


TOOLS: list[dict[str, Any]] = [
    {
        "name": "list_sessions",
        "description": (
            "List sessions in your Project: every live one (any backend), and "
            "optionally recently ended agent sessions. Results are compact, "
            "bounded, searchable, and pageable; use get_session for details. "
            'Your own Project is the default; pass project:"fleet" to list every '
            "Project, or a Project name to list one other. The result reports how "
            "many live sessions the default hid."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": _PROJECT_ARG,
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
                "query": {
                    "type": "string",
                    "description": "Case-insensitive id, name, backend, model, or run filter",
                },
                "cursor": {
                    "type": "string",
                    "description": "Opaque continuation cursor from a previous result",
                },
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "get_session",
        "description": (
            "Status and metadata for one session in your Project, by session id "
            "or exact backend/display name. Omit session_id or use 'self' for "
            "the caller. Live sessions report current state; ended ones report their final state. "
            "Every result carries completion_mode and exit_code, so a one-shot task "
            "reports whether its command succeeded. Pass output_bytes to read the tail "
            "of a shell or task session's terminal output, which is where a failing "
            "command's error text is. "
            'Pass project:"fleet" or a Project name to reach a session outside your Project.'
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "Session id or exact backend/display name",
                },
                "output_bytes": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": MAX_SESSION_OUTPUT_BYTES,
                    "description": (
                        "Read this many bytes from the end of a shell or task session's "
                        f"terminal output (max {MAX_SESSION_OUTPUT_BYTES}). Ignored for an "
                        "agent session, whose conversation is read with read_transcript."
                    ),
                },
                "project": _PROJECT_ARG,
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "read_transcript",
        "description": (
            "Read a small window around a search_history hit, or a bounded, pageable "
            "head or tail of one agent run's conversation. Prefer hit_id after search "
            "so unrelated transcript text never enters context. "
            "Omit session_id or use 'self' for the caller. Supply agent_run_id "
            "to read one of the caller's superseded runs unambiguously. "
            "Every message names its agent_run_id and sequence; cursors cannot "
            "cross a conversation rollover. System/meta records are excluded "
            "unless explicitly requested. "
            'Pass project:"fleet" or a Project name to read a conversation outside '
            "your Project, including one a widened search_history found."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "Session id or exact backend/display name",
                },
                "project": _PROJECT_ARG,
                "agent_run_id": {
                    "type": "string",
                    "description": (
                        "Exact current run id, or one of the caller's superseded run ids"
                    ),
                },
                "hit_id": {
                    "type": "string",
                    "description": (
                        "Opaque message hit returned by search_history; reads around that hit"
                    ),
                },
                "before": {
                    "type": "integer",
                    "description": "Messages before a hit (default 1, max 20)",
                },
                "after": {
                    "type": "integer",
                    "description": "Messages after a hit (default 2, max 20)",
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
                "max_output_bytes": {
                    "type": "integer",
                    "description": (
                        "Maximum message text returned "
                        f"(default {TRANSCRIPT_DEFAULT_OUTPUT_BYTES}, "
                        f"max {TRANSCRIPT_MAX_BYTES})"
                    ),
                },
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "notify",
        "description": (
            "Send a short message to another agent session. It "
            "enters that session's prompt queue and waits: it never interrupts "
            "an active turn, never answers an approval prompt, and by default "
            "lands as an inert draft a human must approve. Use it to hand off "
            "or to flag something the other session needs; do not use it to "
            "issue instructions you would not want a human to read first. "
            "You may reply to a session that messaged you: pass its session id "
            "as the target and the reply continues the same bounded exchange. "
            "The result reports how many messages that exchange has left. "
            "Your own Project is the default. To reach a session in another "
            'Project, pass project:"fleet" or the Project name, or address the '
            'target as "Project name/session name". The receiver is told which '
            "Project you sent from."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "target": {
                    "type": "string",
                    "description": (
                        "Session id, exact backend/display name, or "
                        '"Project name/session name" for another Project'
                    ),
                },
                "project": {
                    "type": "string",
                    "description": (
                        "Which Project the target is in: omit for your own, "
                        '"fleet" for every Project, or a Project name or id'
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
            "Ask the human to start a new agent session with a "
            "prompt you supply. This starts nothing: it writes an inert draft "
            "into the Fleet Queue, and a person decides. Use it "
            "when work should continue in a separate session. The session would "
            "belong to your Project unless you name another one."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "The prompt the new session would be seeded with",
                },
                "project": {
                    "type": "string",
                    "description": (
                        "Project the requested session would belong to "
                        "(name or id; defaults to yours). A request has one "
                        'Project, so "fleet" is not accepted here.'
                    ),
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
            "Context-efficient search over your Project's indexed agent conversations. "
            "Filtering and relevance ranking happen server-side; results are compact message "
            "excerpts with opaque hit ids. Pass a hit id to read_transcript for nearby messages. "
            'Searches your own Project unless you pass project:"fleet" or a Project '
            "name. Widen it deliberately: a fleet search ranks conversations from "
            "unrelated repositories against each other."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Literal search text; may be omitted for filter-only browsing",
                },
                "project": _PROJECT_ARG,
                "scope": {
                    "type": "string",
                    "enum": ["all", "user", "assistant", "metadata"],
                    "description": "Which side of the conversation to search (default all)",
                },
                "query_mode": {
                    "type": "string",
                    "enum": ["hybrid", "all_terms", "any_terms", "phrase", "substring"],
                    "description": (
                        "Matching strategy (default hybrid: token-prefix plus substring)"
                    ),
                },
                "roles": {
                    "type": "array",
                    "items": {"type": "string", "enum": ["user", "assistant"]},
                    "description": "Message roles to include; clearer replacement for scope",
                },
                "title_query": {
                    "type": "string",
                    "description": "Require the raw or generated session title to contain text",
                },
                "backends": {
                    "type": "array",
                    "items": {"type": "string", "enum": list(agent_harnesses())},
                },
                "states": {"type": "array", "items": {"type": "string"}},
                "run_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Restrict a follow-up search to exact agent run ids",
                },
                "session_after": {
                    "type": ["string", "number"],
                    "description": "Inclusive session-start boundary (ISO-8601 or epoch seconds)"
                },
                "session_before": {
                    "type": ["string", "number"],
                    "description": "Exclusive session-start boundary (ISO-8601 or epoch seconds)"
                },
                "message_after": {
                    "type": ["string", "number"],
                    "description": "Inclusive matching-message boundary (ISO-8601 or epoch seconds)"
                },
                "message_before": {
                    "type": ["string", "number"],
                    "description": "Exclusive matching-message boundary (ISO-8601 or epoch seconds)"
                },
                "order": {
                    "type": "string",
                    "enum": ["relevance", "recent"],
                    "description": "Default relevance when query is present, otherwise recent",
                },
                "detail": {
                    "type": "string",
                    "enum": ["compact", "full"],
                    "description": "Compact by default; full adds operational run metadata",
                },
                "max_hits_per_session": {
                    "type": "integer",
                    "description": "Result diversity cap per conversation (default 2, max 5)",
                },
                "max_output_bytes": {
                    "type": "integer",
                    "description": (
                        f"Aggregate result budget (default {SEARCH_DEFAULT_OUTPUT_BYTES}, "
                        f"max {SEARCH_MAX_OUTPUT_BYTES})"
                    ),
                },
                "limit": {
                    "type": "integer",
                    "description": (
                        f"Maximum hits (default {SEARCH_DEFAULT_LIMIT}, max {SEARCH_MAX_LIMIT})"
                    ),
                },
                "cursor": {
                    "type": "string",
                    "description": "Opaque pagination cursor from a previous result",
                },
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "memory_sources",
        "description": (
            "List the root instruction and learned-memory sources available to "
            "agent harnesses for your Project. Source ids are opaque and may be "
            "passed to read_memory. Unsupported providers are reported honestly. "
            'Pass project:"fleet" or a Project name for another Project\'s sources; '
            "every source names the Project it came from."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"project": _PROJECT_ARG},
            "additionalProperties": False,
        },
    },
    {
        "name": "read_memory",
        "description": (
            "Read one bounded Project instruction or learned-memory source from "
            "memory_sources by its opaque source id. Source ids are unique to a "
            "Project: name the Project the source came from, or pass "
            'project:"fleet" to search every Project for it.'
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "source_id": {
                    "type": "string",
                    "description": "Opaque source id returned by memory_sources",
                },
                "project": _PROJECT_ARG,
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
            "This is read-only and never includes the global Scratchpad. Your own "
            'Project is the default; pass project:"fleet" or a Project name for '
            "another Project's notes."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"project": _PROJECT_ARG},
            "additionalProperties": False,
        },
    },
    {
        "name": "read_project_note",
        "description": (
            "Read one bounded Project note by the opaque note id returned by "
            "project_notes. Note ids are unique to a Project: name the Project the "
            'note came from, or pass project:"fleet" to search every Project for it.'
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "note_id": {
                    "type": "string",
                    "description": "Opaque note id returned by project_notes",
                },
                "project": _PROJECT_ARG,
            },
            "required": ["note_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "project_actions",
        "description": (
            "List the runnable tasks a Project declares: native .swe-mux/actions.toml "
            "actions, imported .vscode/tasks.json tasks, and root package.json scripts. "
            "Each entry names its source file, its steps, its declared inputs, and "
            "whether a human has approved that file's exact current bytes. Only an "
            "approved action can be started with run_action. "
            "Pass include_schema:true to also receive the complete authoring reference "
            "for .swe-mux/actions.toml, which is what you need to write or edit one. "
            'Your own Project is the default; pass project:"fleet" or a Project name '
            "for another Project's actions."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "include_schema": {
                    "type": "boolean",
                    "description": (
                        "Include the .swe-mux/actions.toml authoring reference in the result"
                    ),
                },
                "project": _PROJECT_ARG,
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "run_action",
        "description": (
            "Start one Project Action that a human has already approved. Each step "
            "opens as an ordinary terminal session in that Project; the result names "
            "the session ids, and get_session reports each one's exit_code and, with "
            "output_bytes, its terminal output. "
            "This grants no new authority: it can only run a command whose exact bytes "
            "a human approved, and editing a task file un-approves it, so you cannot "
            "approve a command you wrote. An unapproved action refuses with "
            "trust_required and names the file a human must review. "
            "Supply inputs for any action that declares them."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "action_id": {
                    "type": "string",
                    "description": "Action id from project_actions",
                },
                "inputs": {
                    "type": "object",
                    "description": "Values for the action's declared inputs, by input id",
                    "additionalProperties": {"type": "string"},
                },
                "project": _PROJECT_ARG,
            },
            "required": ["action_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "spawn_requests",
        "description": (
            "List the status of spawn requests attributed to your session. "
            "This is read-only; approval remains a human Fleet Queue action. "
            'Requests you drafted into another Project need project:"fleet" or '
            "that Project's name."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"project": _PROJECT_ARG},
            "additionalProperties": False,
        },
    },
]

_DECLARED_TOOL_NAMES = {str(tool["name"]) for tool in TOOLS}
assert _DECLARED_TOOL_NAMES == set(READ_TOOL_NAMES) | set(WRITE_TOOL_NAMES)
for _tool in TOOLS:
    _read_only = str(_tool["name"]) in READ_TOOL_NAMES
    _tool["annotations"] = {
        "readOnlyHint": _read_only,
        "destructiveHint": False,
        "idempotentHint": _read_only,
        "openWorldHint": False,
    }


class McpAuthError(Exception):
    """Raised when no live session owns the presented bearer token."""


class ScopeMiss(KeyError):
    """No session matched under the scope this call asked for.

    A `KeyError` so every existing "not found" path keeps working, with a
    message that names the argument that would widen the search. A scope miss
    and a true miss still answer identically — the hint is generic and confirms
    nothing about what exists elsewhere.
    """

    def __init__(self, message: str = _NOT_FOUND) -> None:
        super().__init__(message)
        self.message = message


class AmbiguousIdentity(ValueError):
    """More than one session matched a name.

    Only reachable once a call widens past one Project: two Projects may each
    hold a session called "backend". Answering "not found" there is a lie the
    caller cannot act on, so the candidates are named instead.
    """

    def __init__(self, identity: str, candidates: list[str]) -> None:
        listed = ", ".join(candidates)
        super().__init__(
            f'"{identity}" matches {len(candidates)} sessions in this scope; '
            f"repeat the call with one of these session ids: {listed}"
        )
        self.identity = identity
        self.candidates = candidates


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
        project_actions: Any = None,
        action_runner: Any = None,
    ) -> None:
        self.sessions = sessions
        self.history = history
        # Phase 5 write tools. Absent (tests, minimal wiring) the tools still
        # list but answer that they are unavailable — never a partial write.
        self.messaging = messaging
        self.automation_store = automation_store
        self.agent_context = agent_context
        self.projects = projects
        self.project_action_service = project_actions
        # A callable rather than the aiohttp application: starting an action needs
        # the spawn handler, the event bus, and the config, and MCP is a transport
        # over the daemon's own operations rather than a second implementation of
        # them. Injected so this module stays free of the HTTP layer.
        self.action_runner = action_runner
        self.calls = 0
        self.denied = 0
        self.writes = 0
        self.tool_stats: dict[str, dict[str, int]] = {}

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
        """(project_id, project_scope_id) of the caller's own Project."""
        return record_scope(caller.record)

    def _requested_scope(self, caller: Any, args: dict[str, Any]) -> ProjectScope:
        """The scope one tool call asked for; the caller's own Project by default.

        An unknown or ambiguous Project name raises `ValueError`, which the RPC
        layer returns as an invalid-argument error with the known names in it.
        Answering "empty" there would teach an agent that the Project holds
        nothing, which is a different and worse thing to be told.
        """
        return resolve_project_scope(args.get("project"), caller.record, self.projects)

    @staticmethod
    def _in_scope(scope: ProjectScope, record: Any) -> bool:
        return scope.admits(*record_scope(record))

    @staticmethod
    def _scope_envelope(scope: ProjectScope, hidden: int = -1) -> dict[str, Any]:
        """What this result covered, and what a wider call would add.

        An agent reads results far more often than it reads a tool schema, so
        the widening lives in the answer as well as in the description. `hidden`
        is a live-session count; -1 means the tool did not measure one.
        """
        envelope: dict[str, Any] = {"project_scope": scope.requested}
        if hidden > 0 and not scope.fleet:
            envelope["live_sessions_in_other_projects"] = hidden
            envelope["scope_note"] = (
                f"{hidden} live session(s) outside {scope.label} are not in this "
                'result. Repeat the call with project:"fleet" for every Project, '
                'or project:"<name>" for one.'
            )
        return envelope

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

    async def _matching_generated_title_ids(
        self, project_id: str, query: str
    ) -> tuple[str, ...]:
        """Run ids whose latest generated title contains a literal query."""
        folded = query.strip().casefold()
        if not folded or self.automation_store is None:
            return ()
        kwargs: dict[str, Any] = {"tag": "title", "limit": 1000}
        if project_id:
            kwargs["project_id"] = project_id
        annotations = await self.automation_store.annotations(**kwargs)
        latest: dict[str, str] = {}
        for annotation in annotations:
            run_id = str(annotation.get("agent_run_id") or "")
            title = str(annotation.get("content") or "").strip()
            if run_id and title and run_id not in latest:
                latest[run_id] = title
        return tuple(run_id for run_id, title in latest.items() if folded in title.casefold())

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

    async def _resolve_live(
        self, caller: Any, identity: str, scope: ProjectScope
    ) -> tuple[Any, str]:
        """Resolve an id, backend name, or UI display name without weak matches."""
        scoped = [
            session
            for session in self.sessions.sessions.values()
            if self._in_scope(scope, session.record)
        ]
        if identity == "self":
            # The caller is always its own scope, whatever `project` asked for:
            # a widened call must not lose the ability to name itself.
            matches = [
                session
                for session in self.sessions.sessions.values()
                if session.record.id == caller.record.id
            ]
            if len(matches) != 1:
                raise ScopeMiss()
            names = await self._live_display_names(matches)
            return matches[0], names[matches[0].record.id]
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
        if len(matches) > 1:
            # Two Projects may hold a session of the same name. Naming the
            # candidates is the only answer the caller can act on.
            raise AmbiguousIdentity(
                identity, sorted(session.record.id for session in matches)
            )
        if not matches:
            raise ScopeMiss()
        return matches[0], names[matches[0].record.id]

    async def _history_display_names(
        self, rows: list[dict[str, Any]]
    ) -> dict[str, str]:
        titles = await self._generated_titles({self._row_run_id(row) for row in rows})
        return {
            str(row.get("id") or ""): self._row_display_name(row, titles) for row in rows
        }

    @staticmethod
    def _history_project_filter(scope: ProjectScope) -> str | None:
        """The `project_id` a history query needs for this scope.

        `None` means every Project. `"__ungrouped__"` keeps the pre-existing
        behaviour for a caller that belongs to no registered Project: the query
        returns the Project-less rows and `_history_row_in_scope` narrows them to
        the caller's git Project identity.
        """
        if scope.fleet:
            return None
        return scope.project_id or "__ungrouped__"

    async def _resolve_history(
        self, caller: Any, identity: str, scope: ProjectScope
    ) -> tuple[dict[str, Any], str]:
        row = await self.history.history_entry(identity)
        if row and self._history_row_in_scope(scope, row):
            names = await self._history_display_names([row])
            return row, names[str(row.get("id") or "")]

        page = await self.history.history_page(
            project_id=self._history_project_filter(scope),
            limit=LIST_HISTORY_SCAN_LIMIT,
        )
        rows = [
            item
            for item in page.get("items", [])
            if self._history_row_in_scope(scope, item)
        ]
        names = await self._history_display_names(rows)
        matches = [
            item
            for item in rows
            if item.get("name") == identity
            or names[str(item.get("id") or "")] == identity
        ]
        if len(matches) > 1:
            raise AmbiguousIdentity(
                identity, sorted(str(item.get("id") or "") for item in matches)
            )
        if not matches:
            raise ScopeMiss()
        match = matches[0]
        return match, names[str(match.get("id") or "")]

    # --------------------------------------------------------------- tools

    async def list_sessions(self, caller: Any, args: dict[str, Any]) -> dict[str, Any]:
        scope = self._requested_scope(caller, args)
        limit = max(1, min(int(args.get("limit") or 25), LIST_MAX_SESSIONS))
        include_ended = bool(args.get("include_ended"))
        query = str(args.get("query") or "").strip().casefold()
        after: tuple[int, str, str] | None = None
        if args.get("cursor"):
            cursor = _decode_cursor(str(args["cursor"]), label="session-list")
            if (
                cursor.get("kind") != "sessions"
                or bool(cursor.get("include_ended")) != include_ended
                or str(cursor.get("query") or "") != query
                or str(cursor.get("project") or "") != scope.requested
            ):
                raise ValueError("session-list cursor does not match this query")
            raw_after = cursor.get("after")
            if not isinstance(raw_after, list) or len(raw_after) != 3:
                raise ValueError("invalid session-list cursor")
            after = (int(raw_after[0]), str(raw_after[1]), str(raw_after[2]))

        own = own_scope(caller.record)
        scoped: list[Any] = []
        hidden = 0
        for session in self.sessions.sessions.values():
            if self._in_scope(scope, session.record):
                scoped.append(session)
            elif not own.admits(*record_scope(session.record)):
                # Only count what widening would actually add. A session the
                # caller's own Project already holds is never "hidden", and a
                # narrowed call to one other Project should not advertise the
                # caller's own siblings back to it.
                hidden += 1
        display_names = await self._live_display_names(scoped)
        candidates: list[tuple[tuple[int, str, str], str, dict[str, Any]]] = []
        for session in scoped:
            item = _session_list_summary(
                session.record,
                display_name=display_names[session.record.id],
            )
            if item["session_id"] == caller.record.id:
                item["you"] = True
            searchable = "\n".join(
                str(item.get(field) or "")
                for field in (
                    "session_id",
                    "name",
                    "display_name",
                    "backend",
                    "model",
                    "agent_run_id",
                )
            ).casefold()
            if query and query not in searchable:
                continue
            key = (
                _LIST_RANK_YOU
                if item.get("you")
                else _LIST_RANK_LIVE_OWN
                if own.admits(*record_scope(session.record))
                else _LIST_RANK_LIVE_OTHER,
                str(item.get("display_name") or "").casefold(),
                str(item["session_id"]),
            )
            candidates.append((key, "live", item))

        if include_ended:
            page = await self.history.history_page(
                project_id=self._history_project_filter(scope),
                limit=LIST_HISTORY_SCAN_LIMIT,
            )
            live_run_ids = {self._record_run_id(session.record) for session in scoped}
            ended_rows = [
                row
                for row in page.get("items", [])
                if self._history_row_in_scope(scope, row)
                and self._row_run_id(row) not in live_run_ids
            ]
            ended_names = await self._history_display_names(ended_rows)
            for row in ended_rows:
                item = _history_list_summary(
                    row,
                    display_name=ended_names[str(row.get("id") or "")],
                )
                searchable = "\n".join(
                    str(item.get(field) or "")
                    for field in (
                        "session_id",
                        "name",
                        "display_name",
                        "backend",
                        "model",
                        "agent_run_id",
                    )
                ).casefold()
                if query and query not in searchable:
                    continue
                key = (
                    _LIST_RANK_ENDED_OWN
                    if own.admits(*row_scope(row))
                    else _LIST_RANK_ENDED_OTHER,
                    str(item.get("display_name") or "").casefold(),
                    str(item["session_id"]),
                )
                candidates.append((key, "ended", item))

        candidates.sort(key=lambda entry: entry[0])
        if after is not None:
            candidates = [entry for entry in candidates if entry[0] > after]
        selected = candidates[:limit]
        while selected:
            has_more = len(candidates) > len(selected)
            next_cursor = (
                _encode_cursor(
                    {
                        "v": 1,
                        "kind": "sessions",
                        "include_ended": include_ended,
                        "query": query,
                        "project": scope.requested,
                        "after": list(selected[-1][0]),
                    }
                )
                if has_more
                else None
            )
            live = [item for _key, kind, item in selected if kind == "live"]
            ended = [item for _key, kind, item in selected if kind == "ended"]
            result: dict[str, Any] = {
                "sessions": live,
                "count": len(selected),
                "has_more": has_more,
                "next_cursor": next_cursor,
                **self._scope_envelope(scope, hidden),
            }
            if include_ended:
                result["ended_sessions"] = ended
            if len(json.dumps(result, ensure_ascii=False).encode("utf-8")) <= LIST_MAX_BYTES:
                return result
            selected.pop()

        result = {
            "sessions": [],
            "count": 0,
            "has_more": bool(candidates),
            "next_cursor": None,
            **self._scope_envelope(scope, hidden),
        }
        if include_ended:
            result["ended_sessions"] = []
        return result

    async def get_session(self, caller: Any, args: dict[str, Any]) -> dict[str, Any]:
        scope = self._requested_scope(caller, args)
        identity = str(args.get("session_id") or "self").strip() or "self"
        try:
            session, display_name = await self._resolve_live(caller, identity, scope)
        except KeyError:
            session = None
        if session is not None:
            result = {
                **session_summary(session.record, display_name=display_name),
                "ended": False,
                **self._scope_envelope(scope),
            }
            result.update(self._session_output(session, args))
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
        row, display_name = await self._resolve_history(caller, identity, scope)
        result = {
            **_history_summary(row, display_name=display_name),
            **self._scope_envelope(scope),
        }
        if args.get("output_bytes") is not None:
            # A removed session (or one from before a daemon restart) has no
            # scrollback: the ring lives with the Session object, not in history.
            # Said explicitly, because silently dropping the field leaves the caller
            # unable to tell "the task printed nothing" from "I did not read it".
            result.update(
                {
                    "output": "",
                    "output_available": False,
                    "output_note": (
                        "This session is no longer live, and terminal output is not "
                        "retained after removal. Read output while the task session "
                        "is still open."
                    ),
                }
            )
        raw = row.get("transcript_path")
        result["run_brief"] = await self._run_brief(
            run_id=self._row_run_id(row),
            path=Path(str(raw)) if raw else None,
            backend=str(row.get("backend") or ""),
            native_id=str(row.get("native_id") or "") or None,
        )
        return result

    def _session_output(self, session: Any, args: dict[str, Any]) -> dict[str, Any]:
        """The tail of a shell session's terminal output, when the caller asked.

        Only for shells and tasks. An agent session's conversation is
        `read_transcript`, and returning its raw PTY bytes here would hand back a
        differential frame stream that reads as gibberish and costs a lot of context.

        Redacted through the same `looks_like_secret` gate every other excerpt uses.
        The bytes are whatever the command printed, so a task that echoes a token is
        exactly the case this gate exists for.
        """
        requested = args.get("output_bytes")
        if requested is None:
            return {}
        if is_agent_harness(session.record.backend):
            return {
                "output": "",
                "output_available": False,
                "output_note": (
                    "This is an agent session; use read_transcript for its conversation."
                ),
            }
        limit = max(1, min(int(requested), MAX_SESSION_OUTPUT_BYTES))
        try:
            raw = session.scrollback.tail_bytes(limit)
        except (AttributeError, OSError, ValueError):
            return {"output": "", "output_available": False, "output_note": "output unavailable"}
        text = raw.decode("utf-8", "replace")
        lines = [
            _REDACTED if looks_like_secret(line) else line for line in text.splitlines()
        ]
        return {
            "output": "\n".join(lines),
            "output_available": True,
            "output_bytes": len(raw),
            "output_truncated": len(raw) >= limit,
        }

    @staticmethod
    def _history_row_in_scope(scope: ProjectScope, row: dict[str, Any]) -> bool:
        return scope.admits(*row_scope(row))

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
        scope = self._requested_scope(caller, args)
        hit_id = str(args.get("hit_id") or "").strip()
        if hit_id:
            incompatible = {
                "session_id",
                "agent_run_id",
                "cursor",
                "from",
                "max_messages",
                "include_system",
            }
            if incompatible.intersection(args):
                raise ValueError(
                    "hit_id cannot be combined with session, cursor, direction, or system options"
                )
            hit = _decode_cursor(hit_id, label="history hit")
            if hit.get("kind") != "history-hit":
                raise ValueError("invalid history hit")
            # A hit carries the Project it came from. Reading it back needs a
            # scope that admits that Project — the hit does not widen the call
            # by itself, so a fleet search followed by a default read still has
            # to say `project` again rather than crossing silently.
            if not scope.admits(
                str(hit.get("project") or ""), str(hit.get("scope") or "")
            ):
                raise ScopeMiss(
                    "that search hit belongs to another Project. Repeat this read "
                    'with the same project argument the search used ("fleet" or '
                    "the Project name)."
                )
            run_id = str(hit.get("run") or "")
            hit_row, display_name = await self._resolve_history(caller, run_id, scope)
            try:
                ordinal = int(hit["ordinal"])
                watermark = (
                    int(hit["mtime"]),
                    int(hit["size"]),
                    int(hit["parser"]),
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError("invalid history hit") from exc
            before = max(0, min(int(args.get("before", 1)), 20))
            after = max(0, min(int(args.get("after", 2)), 20))
            max_output_bytes = max(
                1024,
                min(
                    int(args.get("max_output_bytes") or HIT_DEFAULT_OUTPUT_BYTES),
                    TRANSCRIPT_MAX_BYTES,
                ),
            )
            window = await self.history.history_message_window(
                run_id,
                ordinal,
                watermark=watermark,
                before=before,
                after=after,
            )
            if window.get("stale"):
                raise RuntimeError(
                    "stale_hit: this transcript changed after search; run search_history again"
                )
            messages = [
                {
                    "ordinal": int(item["ordinal"]),
                    "role": item.get("role"),
                    "ts": item.get("ts"),
                    "text": _redact(str(item.get("text") or "")),
                    "is_match": int(item["ordinal"]) == ordinal,
                    "agent_run_id": run_id,
                    "agent_run_seq": int(hit_row.get("agent_run_seq") or 0),
                }
                for item in window.get("messages", [])
            ]
            bounded, truncated, returned_bytes = _bounded_message_texts(
                messages, max_output_bytes
            )
            return {
                "session_id": str(hit_row.get("id") or run_id),
                "agent_run_id": run_id,
                "agent_run_seq": int(hit_row.get("agent_run_seq") or 0),
                "display_name": display_name,
                "around_hit": True,
                "match_ordinal": ordinal,
                "message_count": len(bounded),
                "returned_text_bytes": returned_bytes,
                "content_truncated": truncated,
                "messages": bounded,
                **self._scope_envelope(scope),
            }

        identity = str(args.get("session_id") or "self").strip() or "self"
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
        max_output_bytes = max(
            1024,
            min(
                int(args.get("max_output_bytes") or TRANSCRIPT_DEFAULT_OUTPUT_BYTES),
                TRANSCRIPT_MAX_BYTES,
            ),
        )
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
        requested_run_id = str(args.get("agent_run_id") or "").strip()
        session = None
        row: dict[str, Any] | None = None
        if requested_run_id:
            session, _display_name = await self._resolve_live(caller, identity, scope)
            current_run_id = self._record_run_id(session.record)
            if requested_run_id != current_run_id:
                if session.record.id != caller.record.id:
                    raise ScopeMiss(
                        "a superseded run is readable only through the session "
                        "that owns it"
                    )
                candidate = await self.history.history_entry(requested_run_id)
                if (
                    candidate is None
                    # The caller's own history, checked against the caller's own
                    # Project: a call that widened to somewhere else must not
                    # lose access to the run it is standing in.
                    or not self._history_row_in_scope(own_scope(caller.record), candidate)
                    or not bool(candidate.get("agent_visible", 1))
                    or str(candidate.get("note_id") or "") != str(caller.record.id)
                    or self._row_run_id(candidate) != requested_run_id
                ):
                    raise ScopeMiss("no such run on this session")
                row = candidate
                session = None
        else:
            try:
                session, _display_name = await self._resolve_live(caller, identity, scope)
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
                    **self._scope_envelope(scope),
                }
            path = session.transcript_path
            backend = session.record.backend
            native_id = session.record.native_session_id
        else:
            if row is None:
                row, _display_name = await self._resolve_history(caller, identity, scope)
            raw = row.get("transcript_path")
            path = Path(raw) if raw else None
            backend = str(row.get("backend") or "")
            native_id = str(row.get("native_id") or "") or None
            run_id = self._row_run_id(row)
            run_seq = int(row.get("agent_run_seq") or 0)
            own_superseded_run = bool(
                str(row.get("note_id") or "") == str(caller.record.id)
                and run_id != self._record_run_id(caller.record)
            )
            resolved_session_id = (
                str(caller.record.id)
                if own_superseded_run
                else str(row.get("id") or identity)
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
                **self._scope_envelope(scope),
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
        messages = [
            {
                "ordinal": index,
                "message_id": item.get("message_id"),
                "role": item.get("role"),
                "ts": item.get("ts"),
                "text": _redact(str(item.get("text") or "")),
                "agent_run_id": run_id,
                "agent_run_seq": run_seq,
            }
            for index, item in enumerate(page.get("messages") or [])
        ]
        bounded, truncated, returned_bytes = _bounded_message_texts(
            messages, max_output_bytes
        )
        return {
            "session_id": resolved_session_id,
            "agent_run_id": run_id,
            "agent_run_seq": run_seq,
            "own_superseded_run": own_superseded_run,
            "from": direction,
            "include_system": include_system,
            "message_count": len(bounded),
            "returned_text_bytes": returned_bytes,
            "content_truncated": truncated,
            "next_cursor": next_cursor,
            "messages": bounded,
            **self._scope_envelope(scope),
        }

    async def search_history(self, caller: Any, args: dict[str, Any]) -> dict[str, Any]:
        query = str(args.get("query") or "").strip()
        scope = str(args.get("scope") or "all")
        if scope not in {"all", "user", "assistant", "metadata"}:
            raise ValueError(f"unknown scope: {scope}")
        roles = _string_list(args.get("roles"), "roles", maximum=2)
        roles_provided = bool(roles)
        if roles:
            unknown_roles = set(roles) - {"user", "assistant"}
            if unknown_roles:
                raise ValueError(f"unknown roles: {', '.join(sorted(unknown_roles))}")
            if scope != "all":
                raise ValueError("roles cannot be combined with the legacy scope filter")
            scope = roles[0] if len(roles) == 1 else "all"
        query_mode = str(args.get("query_mode") or "hybrid")
        if query_mode not in {"hybrid", "all_terms", "any_terms", "phrase", "substring"}:
            raise ValueError(f"unknown query_mode: {query_mode}")
        backends = _string_list(args.get("backends"), "backends")
        unknown_backends = set(backends) - set(agent_harnesses())
        if unknown_backends:
            raise ValueError(f"unknown backends: {', '.join(sorted(unknown_backends))}")
        states = _string_list(args.get("states"), "states")
        run_ids_value = args.get("run_ids")
        run_ids = (
            _string_list(run_ids_value, "run_ids", maximum=100)
            if run_ids_value is not None
            else None
        )
        title_query = str(args.get("title_query") or "").strip()
        session_after = _parse_time_bound(args.get("session_after"), "session_after")
        session_before = _parse_time_bound(args.get("session_before"), "session_before")
        message_after = _parse_time_bound(args.get("message_after"), "message_after")
        message_before = _parse_time_bound(args.get("message_before"), "message_before")
        for lower, upper, label in (
            (session_after, session_before, "session"),
            (message_after, message_before, "message"),
        ):
            if lower is not None and upper is not None and lower >= upper:
                raise ValueError(f"{label}_after must be earlier than {label}_before")
        if not query and not any(
            (
                title_query,
                backends,
                states,
                run_ids_value is not None,
                session_after is not None,
                session_before is not None,
                message_after is not None,
                message_before is not None,
            )
        ):
            raise ValueError("query or at least one filter is required")
        if scope == "metadata" and (message_after is not None or message_before is not None):
            raise ValueError("message time boundaries cannot be used with metadata-only search")
        order = str(args.get("order") or ("relevance" if query else "recent"))
        if order not in {"relevance", "recent"}:
            raise ValueError(f"unknown order: {order}")
        detail = str(args.get("detail") or "compact")
        if detail not in {"compact", "full"}:
            raise ValueError(f"unknown detail: {detail}")
        limit = max(
            1, min(int(args.get("limit") or SEARCH_DEFAULT_LIMIT), SEARCH_MAX_LIMIT)
        )
        max_output_bytes = max(
            2048,
            min(
                int(args.get("max_output_bytes") or SEARCH_DEFAULT_OUTPUT_BYTES),
                SEARCH_MAX_OUTPUT_BYTES,
            ),
        )
        max_per_session = max(1, min(int(args.get("max_hits_per_session") or 2), 5))
        project_scope = self._requested_scope(caller, args)
        project_filter = self._history_project_filter(project_scope)
        # The generated-title lookup treats "" as every Project. That is safe at
        # any scope because the ids it returns only widen a *title* match inside
        # the query, which `project_filter` has already narrowed to the Projects
        # this call may read.
        title_project_id = project_scope.project_id
        normalized = {
            "query": query,
            "scope": scope,
            "project": project_scope.requested,
            "roles_provided": roles_provided,
            "query_mode": query_mode,
            "title_query": title_query,
            "backends": backends,
            "states": states,
            "run_ids": run_ids,
            "session_after": session_after,
            "session_before": session_before,
            "message_after": message_after,
            "message_before": message_before,
            "order": order,
            "detail": detail,
            "limit": limit,
            "max_output_bytes": max_output_bytes,
            "max_per_session": max_per_session,
        }
        signature = _query_signature(normalized)
        offset = 0
        cursor_text = str(args.get("cursor") or "").strip()
        if cursor_text:
            cursor = _decode_cursor(cursor_text, label="history search")
            if cursor.get("kind") != "history-search" or cursor.get("sig") != signature:
                raise ValueError("history search cursor does not match this query")
            offset = max(0, int(cursor.get("offset") or 0))
        generated_query_ids = await self._matching_generated_title_ids(
            title_project_id, query
        )
        generated_title_ids = await self._matching_generated_title_ids(
            title_project_id, title_query
        )
        page = await self.history.search_history_index(
            query=query,
            search_scope=scope,
            include_metadata=not roles_provided,
            query_mode=query_mode,
            project_id=project_filter,
            backends=backends,
            states=states,
            title_query=title_query,
            generated_query_run_ids=generated_query_ids,
            generated_title_run_ids=generated_title_ids,
            run_ids=run_ids,
            session_after=session_after,
            session_before=session_before,
            message_after=message_after,
            message_before=message_before,
            order=order,
            offset=offset,
            limit=limit,
            max_per_session=max_per_session,
        )
        rows = list(page.get("items") or [])
        display_names = await self._history_display_names(rows)
        hits: list[dict[str, Any]] = []
        returned_bytes = 0
        output_truncated = False
        own = own_scope(caller.record)
        for row in rows:
            run_id = str(row.get("id") or "")
            ordinal = row.get("match_ordinal")
            hit_id = None
            # The hit names the row's own Project, not the caller's, so
            # read_transcript can tell whether reading it back crosses a
            # boundary. Rows indexed before the Project columns existed carry
            # neither, and fall back to the caller's own scope as before.
            hit_project, hit_scope = row_scope(row)
            if not hit_project and not hit_scope:
                hit_project, hit_scope = own.project_id, own.scope_id
            if ordinal is not None:
                hit_id = _encode_cursor(
                    {
                        "v": 1,
                        "kind": "history-hit",
                        "project": hit_project,
                        "scope": hit_scope,
                        "run": run_id,
                        "ordinal": int(ordinal),
                        "mtime": int(row.get("match_mtime_ns") or 0),
                        "size": int(row.get("match_size") or 0),
                        "parser": int(row.get("match_parser_version") or 0),
                    }
                )
            hit: dict[str, Any] = {
                "hit_id": hit_id,
                "agent_run_id": run_id,
                "title": display_names[run_id],
                # Which Project produced the hit. A compact fleet result is
                # unreadable without it, and a caller that widens later must not
                # see the hit shape change.
                "project_label": row.get("project_label"),
                "backend": row.get("backend"),
                "role": row.get("match_role"),
                "timestamp": row.get("match_ts"),
                "excerpt": _redact(str(row.get("excerpt") or "")),
                "match_kind": row.get("match_kind"),
            }
            if detail == "full":
                hit.update(_history_summary(row, display_name=display_names[run_id]))
                hit["match_ordinal"] = ordinal
                hit["relevance"] = row.get("relevance")
            encoded_size = len(json.dumps(hit, default=str).encode("utf-8"))
            if hits and returned_bytes + encoded_size > max_output_bytes:
                output_truncated = True
                break
            if not hits and encoded_size > max_output_bytes:
                excerpt, cut = _bounded_utf8(str(hit["excerpt"]), max_output_bytes // 2)
                hit["excerpt"] = excerpt
                hit["excerpt_truncated"] = cut
                encoded_size = len(json.dumps(hit, default=str).encode("utf-8"))
                output_truncated = cut
            hits.append(hit)
            returned_bytes += encoded_size
        consumed = len(hits)
        has_more = bool(page.get("has_more")) or consumed < len(rows)
        next_cursor = (
            _encode_cursor(
                {
                    "v": 1,
                    "kind": "history-search",
                    "sig": signature,
                    "offset": offset + consumed,
                }
            )
            if has_more and consumed
            else None
        )
        return {
            "hits": hits,
            "hit_count": len(hits),
            "returned_bytes": returned_bytes,
            "truncated": output_truncated or bool(page.get("candidate_truncated")),
            "next_cursor": next_cursor,
            "search_index_ready": bool(page.get("search_index_ready", True)),
            **self._scope_envelope(project_scope),
        }

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

    def _scoped_projects(self, caller: Any, args: dict[str, Any]) -> tuple[list[Any], bool]:
        """The Projects one inventory read covers, and whether the list was cut.

        A fleet inventory is a per-Project filesystem read, so it is bounded like
        every other tool rather than left unbounded because it is a list. The
        caller's own Project always comes first, so a truncated fleet read still
        answers the question a default read would have.
        """
        scope = self._requested_scope(caller, args)
        if not scope.fleet:
            if scope.project_id and self.projects is not None:
                project = self.projects.projects.get(scope.project_id)
                if project is not None:
                    return [project], False
            return [self._caller_project(caller)], False
        if self.projects is None:
            return [self._caller_project(caller)], False
        own_id = str(caller.record.project_id or "")
        ordered = sorted(
            self.projects.projects.values(),
            key=lambda project: (str(project.id) != own_id, str(project.name).casefold()),
        )
        if not ordered:
            return [self._caller_project(caller)], False
        return ordered[:FLEET_INVENTORY_MAX_PROJECTS], len(ordered) > (
            FLEET_INVENTORY_MAX_PROJECTS
        )

    def _context_service(self) -> Any:
        if self.agent_context is None:
            raise RuntimeError(
                "transient: the agent context service is not available on this daemon"
            )
        return self.agent_context

    @staticmethod
    def _covered_projects(projects: list[Any], truncated: bool) -> dict[str, Any]:
        envelope: dict[str, Any] = {
            "projects": [
                {"id": str(project.id), "name": str(project.name)}
                for project in projects
            ]
        }
        if truncated:
            envelope["projects_truncated"] = True
        return envelope

    async def memory_sources(self, caller: Any, args: dict[str, Any]) -> dict[str, Any]:
        scope = self._requested_scope(caller, args)
        projects, truncated = self._scoped_projects(caller, args)
        sources: list[dict[str, Any]] = []
        providers: list[dict[str, Any]] = []
        unreadable: list[dict[str, str]] = []
        primary: dict[str, Any] | None = None
        for project in projects:
            try:
                inventory = await asyncio.to_thread(
                    self._context_service().inventory,
                    project.id,
                    project.name,
                    project.root,
                )
            except (OSError, ValueError) as exc:
                if len(projects) == 1:
                    raise
                # One unreachable Project root (a disconnected drive, a deleted
                # checkout) must not blank a fleet-wide inventory, and must not
                # be silently missing from it either.
                unreadable.append({"project_id": str(project.id), "error": str(exc)})
                continue
            if primary is None:
                primary = inventory["project"]
            owner = {"project_id": str(project.id), "project_name": str(project.name)}
            sources.extend(
                {**item, **owner}
                for item in (
                    *inventory["instructions"]["items"],
                    *inventory["global_instructions"]["items"],
                    *[
                        entry
                        for provider in inventory["providers"]
                        for entry in provider["items"]
                    ],
                )
            )
            providers.extend(
                {
                    "id": provider["id"],
                    "label": provider["label"],
                    "status": provider["status"],
                    "detail": provider.get("detail"),
                    "item_count": provider["item_count"],
                    "truncated": provider["truncated"],
                    **owner,
                }
                for provider in inventory["providers"]
            )
        result: dict[str, Any] = {
            "sources": sources,
            "providers": providers,
            **self._covered_projects(projects, truncated),
            **self._scope_envelope(scope),
        }
        if unreadable:
            result["unreadable_projects"] = unreadable
        if len(projects) == 1 and primary is not None:
            # One Project covered keeps the original single-Project shape.
            result["project"] = primary
        return result

    async def read_memory(self, caller: Any, args: dict[str, Any]) -> dict[str, Any]:
        source_id = str(args.get("source_id") or "").strip()
        if not source_id:
            raise ValueError("source_id is required")
        scope = self._requested_scope(caller, args)
        projects, _truncated = self._scoped_projects(caller, args)
        failure: Exception | None = None
        for project in projects:
            try:
                result = await asyncio.to_thread(
                    self._context_service().read_source,
                    project.root,
                    source_id,
                )
            except ValueError as exc:
                # A source id belongs to one Project. Under `project:"fleet"`
                # this walks Projects until one owns it, which is what makes a
                # fleet-wide memory_sources listing actionable.
                failure = exc
                continue
            content = str(result["text"])
            redacted = bool(content and looks_like_secret(content))
            return {
                "project": {"id": project.id, "name": project.name},
                "source": result["source"],
                "text": _REDACTED if redacted else content,
                "redacted": redacted,
                **self._scope_envelope(scope),
            }
        if len(projects) == 1 and failure is not None:
            raise failure
        raise ValueError(
            f'no Project in {scope.label} owns the source "{source_id}"; read it '
            "from the Project memory_sources listed it under"
        )

    async def project_notes(self, caller: Any, args: dict[str, Any]) -> dict[str, Any]:
        scope = self._requested_scope(caller, args)
        projects, truncated = self._scoped_projects(caller, args)
        notes: list[dict[str, Any]] = []
        unreadable: list[dict[str, str]] = []
        for project in projects:
            try:
                items = await asyncio.to_thread(
                    project_note_summaries,
                    project.root,
                    default_note_id=project.id,
                    default_title=f"{project.name} notes",
                    migrate_legacy=False,
                )
            except (OSError, ValueError) as exc:
                if len(projects) == 1:
                    raise
                unreadable.append({"project_id": str(project.id), "error": str(exc)})
                continue
            notes.extend(
                {
                    "note_id": item["note_id"],
                    "title": item["title"],
                    "created_at": item["created_at"],
                    "updated_at": item["updated_at"],
                    "bytes": item["bytes"],
                    "revision": item["revision"],
                    "excerpt": _redact(str(item.get("excerpt") or "")),
                    "origin_session_id": item.get("origin_session_id"),
                    "project_id": str(project.id),
                    "project_name": str(project.name),
                }
                for item in items
            )
        result: dict[str, Any] = {
            "notes": notes,
            **self._covered_projects(projects, truncated),
            **self._scope_envelope(scope),
        }
        if unreadable:
            result["unreadable_projects"] = unreadable
        if len(projects) == 1:
            result["project"] = {"id": projects[0].id, "name": projects[0].name}
        return result

    async def project_actions(self, caller: Any, args: dict[str, Any]) -> dict[str, Any]:
        scope = self._requested_scope(caller, args)
        projects, truncated = self._scoped_projects(caller, args)
        service = self.project_action_service
        if service is None:
            raise QueueError(
                "unavailable",
                "Project Actions are not available on this daemon.",
                status=503,
            )
        actions: list[dict[str, Any]] = []
        catalogs: list[dict[str, Any]] = []
        for project in projects:
            catalog = await asyncio.to_thread(service.catalog, project.root)
            snapshot = catalog.snapshot()
            catalogs.append(
                {
                    "project_id": str(project.id),
                    "project_name": str(project.name),
                    "trusted": snapshot["trusted"],
                    "files": snapshot["files"],
                    "diagnostics": snapshot["diagnostics"],
                }
            )
            actions.extend(
                {**item, "project_id": str(project.id), "project_name": str(project.name)}
                for item in snapshot["actions"]
            )
        result: dict[str, Any] = {
            "actions": actions,
            "catalogs": catalogs,
            # Stated in the result and not only in the tool description: an agent
            # reads answers far more often than schemas, and an untrusted action is
            # otherwise indistinguishable from a broken one.
            "note": (
                "Only an action whose source file a human has approved can be started "
                "with run_action. Editing a task file un-approves it."
            ),
            **self._covered_projects(projects, truncated),
            **self._scope_envelope(scope),
        }
        if args.get("include_schema"):
            result["schema"] = project_actions_schema()
        return result

    async def run_action(self, caller: Any, args: dict[str, Any]) -> dict[str, Any]:
        action_id = str(args.get("action_id") or "").strip()
        if not action_id:
            raise ValueError("action_id is required")
        raw_inputs = args.get("inputs") or {}
        if not isinstance(raw_inputs, dict) or not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in raw_inputs.items()
        ):
            raise ValueError("inputs must be a map of input id to string value")
        if self.action_runner is None or self.project_action_service is None:
            raise QueueError(
                "unavailable",
                "Project Actions are not available on this daemon.",
                status=503,
            )
        scope = self._requested_scope(caller, args)
        projects, _truncated = self._scoped_projects(caller, args)
        owner = None
        for project in projects:
            catalog = await asyncio.to_thread(
                self.project_action_service.catalog, project.root
            )
            if any(item.id == action_id for item in catalog.actions):
                owner = project
                break
        if owner is None:
            raise QueueError(
                "unknown_action",
                f"no action {action_id!r} in {scope.label}. Call project_actions to "
                "list what this Project declares.",
                status=404,
            )
        try:
            payload, _status = await self.action_runner(owner, action_id, dict(raw_inputs))
        except PermissionError as exc:
            # Typed rather than raised as a protocol fault, and it names the file a
            # human has to look at. An agent that cannot tell "refused" from "broken"
            # retries blindly or stops calling.
            raise QueueError(
                "trust_required",
                f"{exc} Ask the operator to approve it in the Project Run menu.",
                status=409,
            ) from exc
        except ValueError as exc:
            raise ValueError(str(exc)) from exc
        return {
            "action_id": action_id,
            "project_id": str(owner.id),
            "project_name": str(owner.name),
            "sessions": [
                {
                    "session_id": item.get("id"),
                    "name": item.get("name"),
                    "state": item.get("state"),
                }
                for item in payload["sessions"]
            ],
            "errors": payload["errors"],
            "inputs": payload["inputs"],
            "note": (
                "Each step runs as its own session. Call get_session with a session_id "
                "for its exit_code, and add output_bytes to read its terminal output."
            ),
            **self._scope_envelope(scope),
        }

    async def read_project_note(
        self, caller: Any, args: dict[str, Any]
    ) -> dict[str, Any]:
        note_id = str(args.get("note_id") or "").strip()
        if not note_id:
            raise ValueError("note_id is required")
        scope = self._requested_scope(caller, args)
        projects, _truncated = self._scoped_projects(caller, args)
        inventory = await self.project_notes(caller, args)
        by_project = {str(project.id): project for project in projects}
        summary = next(
            (item for item in inventory["notes"] if item["note_id"] == note_id),
            None,
        )
        project = by_project.get(str((summary or {}).get("project_id") or ""))
        if summary is None or project is None:
            raise QueueError(
                "unknown_note",
                f"no such note in {scope.label}. Note ids belong to one Project: "
                'name that Project, or pass project:"fleet".',
                status=404,
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
                "unknown_note", f"no such note in {scope.label}", status=404
            )
        markdown, truncated = _bounded_utf8(str(note.get("markdown") or ""), NOTE_MAX_BYTES)
        redacted = bool(markdown and looks_like_secret(markdown))
        return {
            "project": {"id": project.id, "name": project.name},
            "note": summary,
            "markdown": _REDACTED if redacted else markdown,
            "truncated": truncated,
            "redacted": redacted,
            **self._scope_envelope(scope),
        }

    async def message_status(self, caller: Any, args: dict[str, Any]) -> dict[str, Any]:
        message_id = str(args.get("message_id") or "").strip()
        if not message_id:
            raise ValueError("message_id is required")
        return dict(await self._messaging().message_status(caller, message_id))

    async def spawn_requests(
        self, caller: Any, args: dict[str, Any]
    ) -> dict[str, Any]:
        return dict(
            await self._messaging().spawn_requests(caller, project=args.get("project"))
        )

    # ----------------------------------------------------------- write tools

    def _messaging(self) -> Any:
        if self.messaging is None:
            raise RuntimeError(
                "transient: the mux messaging service is not available on this daemon"
            )
        return self.messaging

    async def _notify_target(
        self, caller: Any, target: str, requested_project: str
    ) -> tuple[str, str]:
        """Map a target to (session id, the `project` the write should carry).

        Only display names need resolving here: the messaging service resolves
        ids and backend names itself, but generated UI titles live in the
        annotation store this service reads. The resolution is advisory — the
        write re-checks the scope it is handed, so this cannot widen anything.
        """
        if not target:
            return target, requested_project
        scope = self._requested_scope(caller, {"project": requested_project})
        try:
            session, _display_name = await self._resolve_live(caller, target, scope)
        except AmbiguousIdentity as exc:
            raise QueueError(
                "ambiguous_target", str(exc), status=409, candidates=exc.candidates
            ) from exc
        except KeyError:
            pass
        else:
            return session.record.id, requested_project
        # "Project name/session name" — tried only after the plain form, so a
        # session whose name contains a slash still resolves as itself.
        qualifier, name = split_qualified_target(target)
        if not qualifier or requested_project:
            return target, requested_project
        try:
            qualified = self._requested_scope(caller, {"project": qualifier})
            session, _display_name = await self._resolve_live(caller, name, qualified)
        except AmbiguousIdentity as exc:
            raise QueueError(
                "ambiguous_target", str(exc), status=409, candidates=exc.candidates
            ) from exc
        except (KeyError, ValueError):
            return target, requested_project
        return session.record.id, qualified.requested

    async def notify(self, caller: Any, args: dict[str, Any]) -> dict[str, Any]:
        """`mux.notify`: a caller over the Phase 5 A→B queue operation.

        Every bound (scope, allowlist, size, budget, chain depth, cycle
        detection, receiver readiness, kill switch) lives in the daemon
        operation, not here — that is the whole point of MCP being transport and
        not authority (`CONTROL_PLANE_ROADMAP.md` §7.1). The sender is the
        token's session; there is no sender argument to forge.
        """
        self.writes += 1
        target, project = await self._notify_target(
            caller,
            str(args.get("target") or ""),
            str(args.get("project") or ""),
        )
        result = await self._messaging().notify(
            caller,
            target=target,
            body=str(args.get("body") or ""),
            reason=str(args.get("reason") or ""),
            correlation_id=str(args.get("correlation_id") or "") or None,
            project=project,
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
            project=str(args.get("project") or ""),
        )
        return dict(result)

    # ------------------------------------------------------------ protocol

    async def dispatch_tool(self, caller: Any, name: str, args: dict[str, Any]) -> Any:
        self.calls += 1
        stats = self.tool_stats.setdefault(
            name, {"calls": 0, "response_bytes": 0, "truncated_results": 0}
        )
        stats["calls"] += 1
        log.info(
            "MCP tool call tool=%s caller_session=%s project=%s requested_project=%s",
            name,
            caller.record.id,
            caller.record.project_id,
            str(args.get("project") or "self"),
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
            "project_actions": self.project_actions,
            "message_status": self.message_status,
            "spawn_requests": self.spawn_requests,
            "notify": self.notify,
            "request_spawn": self.request_spawn,
            "run_action": self.run_action,
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
                            "Visibility into your swe-mux fleet: sibling sessions, "
                            "their live status and run briefs, pageable transcripts, "
                            "archived conversation search, Project notes, message and "
                            "spawn-request status, and exact Agent Context source reads. "
                            "Results default to your own Project, and every tool takes a "
                            '`project` argument that widens that: "fleet" for every '
                            "Project, or a Project name or id for one other. Sessions in "
                            "other Projects are reachable — you just have to ask for them. "
                            "An empty result means nothing relevant exists in the scope you "
                            "asked for. Two bounded write "
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
            except KeyError as exc:
                # Scope miss and true miss answer identically: not found. The
                # text names the argument that widens the search, because a
                # default an agent cannot discover reads as a prohibition.
                return ok(
                    {
                        "content": [
                            {
                                "type": "text",
                                "text": str(getattr(exc, "message", "") or _NOT_FOUND),
                            }
                        ],
                        "isError": True,
                    }
                )
            except (ValueError, TypeError) as exc:
                return error(-32602, str(exc))
            except RuntimeError as exc:
                return ok({"content": [{"type": "text", "text": str(exc)}], "isError": True})
            encoded = json.dumps(result, default=str)
            stats = self.tool_stats.setdefault(
                name, {"calls": 0, "response_bytes": 0, "truncated_results": 0}
            )
            stats["response_bytes"] += len(encoded.encode("utf-8"))
            if bool(result.get("truncated")) or bool(result.get("content_truncated")):
                stats["truncated_results"] += 1
            log.info(
                "MCP tool result tool=%s caller_session=%s response_bytes=%s truncated=%s",
                name,
                caller.record.id,
                len(encoded.encode("utf-8")),
                bool(result.get("truncated")) or bool(result.get("content_truncated")),
            )
            return ok(
                {
                    "content": [
                        {"type": "text", "text": encoded}
                    ],
                    "isError": False,
                }
            )
        return error(-32601, f"method not found: {method}")

    def status(self) -> dict[str, Any]:
        return {
            "calls": self.calls,
            "denied": self.denied,
            "writes": self.writes,
            "tools": {name: dict(values) for name, values in sorted(self.tool_stats.items())},
        }
