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
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from . import session_titles
from .automation_store import SCAN_SEARCH_SCAN_LIMIT
from .clipboard_store import looks_like_secret
from .code_graph import (
    DEFAULT_BLAST_HOPS,
    MAX_BLAST_HOPS,
    co_change_net,
)
from .deterministic_consumers import (
    PROJECT_FACT_WINDOW_SECONDS,
    build_doc_debt_map,
    build_provenance_edges,
    cached_doc_ownership,
    claim_match,
    detect_declared_vs_verified,
    is_test_path,
    normalize_target,
)
from .git_projects import ProjectIdentity
from .harness import agent_harnesses, is_agent_harness
from .land_queue import LandRefusal
from .mcp_contract import (
    CONFIGURATOR_READ_TOOL_NAMES,
    CONFIGURATOR_WRITE_TOOL_NAMES,
    READ_TOOL_NAMES,
    WRITE_TOOL_NAMES,
)
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
from .scan_consumers import (
    catch_me_up,
    project_record,
    search_scan_records,
)
from .scan_timeline import APPROACH_STATUS, HEARTBEAT_TRIGGERS, WORK_PHASES
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
# Phase 7.11 scan-timeline reads. A projected record measures ~730 bytes on the
# live store, so the page bound is what keeps a timeline read affordable - field
# selection alone is not enough. A whole 230-record run would be ~41k tokens.
SCAN_RECORDS_DEFAULT_LIMIT = 30
SCAN_RECORDS_MAX_LIMIT = 100
#: `detail:'full'` reads whole stored records, hashes and evidence included, so
#: it is bounded far tighter than the projection and requires explicit ids.
SCAN_FULL_MAX_RECORDS = 5
#: Records a digest rolls up. The digest is bounded by `catch_me_up` itself; this
#: bounds the *read* behind it so a pathological run cannot make one tool call a
#: 2000-row query.
SCAN_DIGEST_SCAN_LIMIT = 2000
SCAN_SEARCH_DEFAULT_LIMIT = 20
SCAN_SEARCH_MAX_LIMIT = 100
#: Projects one fleet-wide inventory read walks before it reports a truncation.
#: An inventory is a per-Project filesystem read, so the fleet form is bounded
#: the way every other tool is rather than being unbounded because it is a list.
FLEET_INVENTORY_MAX_PROJECTS = 25

# Phase 7.5 cross-session memory reads. Every result names the agent run it came
# from, low-confidence items are withheld from the agent (still counted for the
# human), and empty is preferred over a weak match (CP §7, ROADMAP 7.5).
MEMORY_MAX_RESULTS = 40
#: Below this a prior resolution is withheld: the experience corpus stores a
#: model-scored confidence, and a low-confidence fix an agent acts on is exactly
#: the "plausible but wrong" failure the precision gate exists to stop.
PRIOR_RESOLUTION_MIN_CONFIDENCE = 0.5
#: Below this a scan-derived dead end is withheld. A scan record's confidence is
#: the observer model's own 0..1 score for the turn it summarized.
DEAD_END_MIN_CONFIDENCE = 0.4
#: Tier 0 facts a single provenance query scans per Project before it stops. A
#: file's lineage lives in the file_read/file_write facts, so this bounds the
#: read the way every other tool is bounded rather than walking a run's history.
PROVENANCE_FACT_SCAN_LIMIT = 5000
#: The Tier 0 kinds that carry a file touch. The bare `file_write`/`file_read`
#: facts record the *intent* and carry no target; the `_result` variants carry
#: the path and the content hash, so both sets are consulted and the target
#: filter drops the null-target intents. These mirror `_WRITE_KINDS`/`_READ_KINDS`
#: in `deterministic_consumers`, which `build_provenance_edges` already uses.
PROVENANCE_WRITE_KINDS = frozenset({"file_write", "file_write_result"})
PROVENANCE_READ_KINDS = frozenset({"file_read", "file_read_result"})
#: The maps the enablement DAG resolves a Phase 7.5 tool against. A tool is a
#: read over the output an already-shipped consumer produces, so it is available
#: only where that consumer's per-Project opt-in is on (ROADMAP 7.5 exit
#: criteria). `prior_resolutions` reads the experience corpus, which no detector
#: gates today, so it earns its own consumer id.
MEMORY_TOOL_AUTOMATION = {
    "provenance": "provenance_graph",
    "verified_status": "declared_vs_verified",
    "dead_ends": "dead_end_memory",
    "prior_resolutions": "prior_resolutions",
    # `doc_debt` reads the same doc-ownership substrate the `doc-debt` detector
    # writes, so it is gated on that detector's own per-Project opt-in rather
    # than a new consumer id.
    "doc_debt": "doc_debt",
    # Phase 7.9 code-structure graph reads. All six gate on the one `code_graph`
    # consumer that maintains the graph they read.
    "blast_radius": "code_graph",
    "find_definition": "code_graph",
    "find_callers": "code_graph",
    "find_references": "code_graph",
    "code_context": "code_graph",
    "test_gap": "code_graph",
    # Phase 7.11 scan-timeline reads. `scan_timeline` gates on its own consumer
    # id rather than on the `scan_timeline` substrate: a distilled intent summary
    # is in some ways more revealing than the transcript excerpt behind it, so a
    # Project must be able to keep its timeline and still withhold it from
    # sibling agents. `scan_search` inherits the opt-in that already gates the
    # identical query on the human surface.
    "scan_timeline": "scan_reads",
    "scan_search": "semantic_history_search",
}

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


def _load_detail(fact: dict[str, Any]) -> dict[str, Any]:
    """Parse a Tier 0 fact's `detail_json`, which arrives as a raw JSON string."""
    raw = fact.get("detail_json")
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(str(raw or "{}"))
    except (json.JSONDecodeError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _subsystem_matches(needle: str, targets: list[str], *haystacks: str) -> bool:
    """Whether a subsystem hint matches a scan record.

    A scan record has no subsystem field, only the tier-0 target paths its turn
    touched, so a subsystem is matched as a case-folded substring of any of
    those paths, or of the record's own free text (the intent or the dead-end
    note itself). The hint is deliberately generous: a caller asking about
    "delivery" wants the records that touched `delivery_readiness.py` as much as
    the ones that named delivery in prose.
    """
    folded = needle.strip().casefold()
    if not folded:
        return True
    if any(folded in str(target).casefold() for target in targets):
        return True
    return any(folded in text.casefold() for text in haystacks if text)


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
            "unless explicitly requested. Turns the conversation branched away "
            "from - a retry, a rewind - are excluded too and counted in "
            "abandoned_messages. "
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
            "enters that session's prompt queue and waits for that session to be "
            "ready: it never answers an approval prompt, and by default "
            "lands as an inert draft a human must approve. Use it to hand off "
            "or to flag something the other session needs; do not use it to "
            "issue instructions you would not want a human to read first. "
            "You may reply to a session that messaged you: pass its session id "
            "as the target and the reply continues the same bounded exchange. "
            "The result reports how many messages that exchange has left, and "
            "whether anything will actually deliver it - if it says nothing "
            "will, say so rather than waiting silently for a reply. "
            "Pass dry_run:true first when it matters whether the message will "
            "actually reach the target: it runs every check and reports the same "
            "verdict without staging anything, so an unreachable peer is a choice "
            "rather than something you discover afterwards. If a message you did "
            "stage turns out to be unwanted, withdraw it with revoke_message. "
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
                "envelope": {
                    "type": "string",
                    "enum": ["full", "compact", "bare"],
                    "description": (
                        "How much mux says about you on top of your text. "
                        '"compact" (the usual default) names you, says whether a '
                        "human reviewed the message, and gives the reply route. "
                        '"full" adds the whole standing-grant statement - use it '
                        "when you are asking the other session to do something it "
                        'might otherwise take as its operator speaking. "bare" '
                        "sends your text alone, indistinguishable from a person "
                        "typing, which suits a clean hand-off and is wrong for an "
                        "instruction. The target Project sets the minimum: you may "
                        "always disclose more than it asks and never less, and the "
                        "result reports what was actually used."
                    ),
                },
                "delivery": {
                    "type": "string",
                    "enum": ["when_idle", "now"],
                    "description": (
                        'When to deliver. "when_idle" (the default) waits for the '
                        "target to finish what it is doing and be at its prompt. "
                        '"now" also allows delivery into a turn that is already '
                        "running, which is what you want for something the other "
                        "session should know before it finishes - a correction, a "
                        "changed constraint, work you have just taken over. It "
                        "does not stop the turn: the CLI buffers your text and "
                        "takes it at the turn boundary, so what you buy is arriving "
                        "sooner, not preemption. To actually stop a turn, use "
                        "interrupt. Refused where the target's Project switched "
                        "mid-turn delivery off (it is granted by default) or the "
                        "target session opted out; the refusal says which, and "
                        "sending without this argument always works."
                    ),
                },
                "dry_run": {
                    "type": "boolean",
                    "description": (
                        "Check without sending. Runs every bound - target, size, "
                        "budgets, backlog, relay depth, exchange budget, mid-turn "
                        "gates - and reports whether the message would arrive armed "
                        "and whether anything would deliver it, staging nothing and "
                        "spending no budget. Use it before a hand-off that matters."
                    ),
                },
            },
            "required": ["target", "body"],
            "additionalProperties": False,
        },
    },
    {
        "name": "revoke_message",
        "description": (
            "Withdraw a message you sent with notify that has not been delivered "
            "yet. Only the attributed sender can revoke, and only a message still "
            "waiting in the target's queue: once it has been delivered the text is "
            "in someone else's terminal and cannot be taken back. Use it when you "
            "reached the target another way and the queued copy would arrive out of "
            "context."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "message_id": {"type": "string", "description": "Message id from notify"},
                "reason": {
                    "type": "string",
                    "description": "Short note on why you withdrew it (kept as provenance)",
                },
            },
            "required": ["message_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "request_spawn",
        "description": (
            "Start a new agent session with a prompt you supply, in your Project "
            "or another registered one. By default this starts nothing: it writes "
            "an inert draft into the Fleet Queue and a person decides. A Project "
            "an operator has granted agent spawn creates the session directly "
            "instead, inside a per-origin budget, and the result carries its live "
            "session id - watch it with get_session/read_transcript and end it "
            "with end_session when its work is done. Use it when work should "
            "continue in a separate session, and only when a human asked for it. "
            "The session belongs to your Project unless you name another one."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "The prompt the new session is seeded with",
                },
                "project": {
                    "type": "string",
                    "description": (
                        "Project the session belongs to (name or id; defaults to "
                        'yours). A spawn has one Project, so "fleet" is not '
                        "accepted here. Direct spawn needs that Project to have "
                        "granted agent spawn; otherwise it drafts."
                    ),
                },
                "backend": {
                    "type": "string",
                    "enum": list(agent_harnesses()),
                    "description": "Preferred agent CLI (defaults to yours)",
                },
                "name": {"type": "string", "description": "Suggested session name"},
                "reason": {"type": "string", "description": "Why a separate session is warranted"},
                "correlation_id": {
                    "type": "string",
                    "description": (
                        "Idempotency key; a retry with the same value does not "
                        "spawn twice"
                    ),
                },
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
    {
        "name": "provenance",
        "description": (
            "Who touched a file, at what content hash, and what ran against it, "
            "across every session in the Project. Returns cross-session "
            "read-after-write edges from the deterministic fact record (session B "
            "wrote hash X to F; session A later read it) and the git commits that "
            "carry it. It reports lineage, not blame: it never says one session "
            "caused another's failure. Every edge names the agent run it came "
            "from, and one of your own earlier runs is labelled as such rather "
            "than blended into the present. Empty means no cross-session lineage "
            "for that file in scope. Needs the Provenance graph automation enabled "
            "for the Project."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "file": {
                    "type": "string",
                    "description": "Path (repo-relative or absolute) to trace",
                },
                "project": _PROJECT_ARG,
            },
            "required": ["file"],
            "additionalProperties": False,
        },
    },
    {
        "name": "verified_status",
        "description": (
            "Is a claim actually tested against the current code, or only "
            "declared done? Give the claim text (\"the auth bug is fixed\") and "
            "this reads the run's own test facts and answers with one of "
            "'claims done · tests ran · tests passed', 'tests ran · tests "
            "failed', or 'tests not run · nothing verified' - never a bare check "
            "mark. Defaults to your own current run; pass session_id to check "
            "another agent's run, and the result names whose run it read. Needs "
            "the Declared vs verified automation enabled for the Project."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "claim": {
                    "type": "string",
                    "description": "The done/fixed/works claim to check",
                },
                "session_id": {
                    "type": "string",
                    "description": (
                        "Session id or exact name whose run to check; omit or "
                        "use 'self' for your own current run"
                    ),
                },
                "project": _PROJECT_ARG,
            },
            "required": ["claim"],
            "additionalProperties": False,
        },
    },
    {
        "name": "prior_resolutions",
        "description": (
            "Has this exact error been fixed before, with a verified resolution? "
            "Matches on the normalized error signature (equality, never a "
            "substring guess), so a near-miss returns nothing rather than a "
            "plausible-but-wrong fix. Give the raw error text. Results carry the "
            "recorded resolution, the run it came from, and a confidence score; "
            "low-confidence matches are withheld and only counted. Defaults to "
            'your own Project (the precision gate wants same-Project); pass '
            'project:"fleet" to widen. Needs the Prior resolutions automation '
            "enabled for the Project."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "error": {
                    "type": "string",
                    "description": "The raw error message or signature to look up",
                },
                "project": _PROJECT_ARG,
            },
            "required": ["error"],
            "additionalProperties": False,
        },
    },
    {
        "name": "dead_ends",
        "description": (
            "Approaches a subsystem's runs tried, abandoned, and why, from the "
            "scan timeline. Give a subsystem hint (a path fragment or module "
            "name) and this returns the run-scoped records whose approach was "
            "abandoned or failed with a recorded dead-end note, so you do not "
            "re-walk a path a sibling already found closed. A conversation "
            "rollover is not an abandonment; only an approach dropped within a "
            "run counts. Every record names its run, and low-confidence records "
            "are withheld. Needs the Dead-end memory automation enabled for the "
            "Project."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "subsystem": {
                    "type": "string",
                    "description": (
                        "A path fragment, module, or file name to match dead ends "
                        "against; omit for every dead end in scope"
                    ),
                },
                "project": _PROJECT_ARG,
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "doc_debt",
        "description": (
            "Which docs owe an update for the source files this Project changed "
            "recently? Returns {doc, changed_files} pairs re-derived from each "
            "doc's 'Key files' section: a doc that lists a changed source file "
            "owes an update, unless that doc was itself edited in the same window. "
            "Blind spot: a source file no doc lists in a 'Key files' section owns "
            "no doc, so an empty result is not proof the docs are current - it can "
            "also mean the changed files are undocumented. Defaults to your own "
            'Project; pass project:"fleet" or a Project name to widen. Needs the '
            "Doc-debt ledger automation enabled for the Project."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": _PROJECT_ARG,
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "scan_timeline",
        "description": (
            "What has a session actually been doing? Reads the distilled "
            "behavioral timeline of one agent run - phases, intents, claims, "
            "blockers and touched paths - instead of paging its raw conversation. "
            "detail:'digest' (the default) is a few phase-structured bullets plus "
            "the current blocker, and is the whole answer to 'is this session "
            "healthy'. detail:'records' returns the compact per-window rows, "
            "newest first; detail:'full' expands the named record_ids. "
            "To monitor a session, poll with since_t1 set to the newest t1 you "
            "have already seen: it returns only what is new. "
            "Every result states whether scanning is on, when it last ran and why "
            "it stopped, so a budget-stopped scanner is never readable as a quiet "
            "session. "
            "Each row carries messages_seen (how thin the window behind the "
            "judgement was) and repaired_fields (which fields were coerced rather "
            "than asserted by the model) - read both before trusting a label. "
            "To zoom into a window, take its t0/t1 and agent_run_id and call "
            "search_history with run_ids, message_after and message_before, then "
            "read_transcript on a hit. "
            'Omit session_id or use "self" for yourself; pass project:"fleet" or a '
            "Project name to read a session outside your Project. Needs the Agent "
            "scan-timeline reads automation enabled for that session's Project."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": (
                        "Session id, agent run id, or exact backend/display name; "
                        'omit or "self" for the caller. An ended session is readable.'
                    ),
                },
                "project": _PROJECT_ARG,
                "detail": {
                    "type": "string",
                    "enum": ["digest", "records", "full"],
                    "description": "digest (default), records, or full",
                },
                "record_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        f"Required for detail:'full'; at most {SCAN_FULL_MAX_RECORDS} ids"
                    ),
                },
                "since_t1": {
                    "type": ["number", "string"],
                    "description": (
                        "Exclusive cursor: only records whose t1 is newer than this"
                    ),
                },
                "until_t1": {
                    "type": ["number", "string"],
                    "description": "Inclusive upper bound on t1",
                },
                "work_phase": {
                    "type": "string",
                    "enum": sorted(WORK_PHASES),
                    "description": "Only records in this work phase",
                },
                "approach_status": {
                    "type": "string",
                    "enum": sorted(APPROACH_STATUS),
                    "description": (
                        "Only records carrying this run-level verdict. Narrow-window "
                        "records omit the field entirely and match nothing here."
                    ),
                },
                "blocked_only": {
                    "type": "boolean",
                    "description": "Only records whose blocked_on is not 'none'",
                },
                "target": {
                    "type": "string",
                    "description": "Only records whose touched paths contain this fragment",
                },
                "exclude_heartbeat": {
                    "type": "boolean",
                    "description": (
                        "Drop periodic heartbeat scans, keeping only event-driven ones"
                    ),
                },
                "limit": {
                    "type": "integer",
                    "description": (
                        f"Records in this page (default {SCAN_RECORDS_DEFAULT_LIMIT}, "
                        f"max {SCAN_RECORDS_MAX_LIMIT})"
                    ),
                },
                "oldest_first": {
                    "type": "boolean",
                    "description": (
                        "Return the page oldest-first; the default is newest-first"
                    ),
                },
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "scan_search",
        "description": (
            "Search what runs were *doing*, across conversations. Resolves a query "
            "against distilled scan summaries, intents and touched paths rather "
            "than raw transcript text, so it finds a run by what it was working on "
            "even when the words never appeared verbatim. All query terms must "
            "match. Each hit names its agent_run_id and its t0/t1 window; pass "
            "those to scan_timeline for that run's spine, or to search_history "
            "(run_ids + message_after/message_before) to reach the raw messages. "
            'Defaults to your own Project; pass project:"fleet" or a Project name '
            "to widen. Needs the Semantic history search automation enabled for the "
            "Project."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search terms; all must match a record",
                },
                "project": _PROJECT_ARG,
                "agent_run_id": {
                    "type": "string",
                    "description": "Restrict the search to one agent run",
                },
                "limit": {
                    "type": "integer",
                    "description": (
                        f"Maximum hits (default {SCAN_SEARCH_DEFAULT_LIMIT}, "
                        f"max {SCAN_SEARCH_MAX_LIMIT})"
                    ),
                },
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    {
        "name": "blast_radius",
        "description": (
            "What can a change to this file reach? Returns the reverse callers "
            "(who imports or calls it, hop-ordered), the git co-change net (files "
            "repeatedly committed with it), the covering tests among the reachable "
            "set, and the docs that own it. The static reverse set is a LOWER "
            "BOUND: callers reached through getattr, dict dispatch, decorators, "
            "dependency injection, or dynamic imports are not shown, so an empty "
            "result is not proof a change is safe - the co-change net is the recall "
            'net for those. Defaults to your own Project; pass project:"fleet" or a '
            "Project name to widen. Needs the Code-structure graph automation "
            "enabled for the Project."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "file": {
                    "type": "string",
                    "description": "Path (repo-relative or absolute) to trace",
                },
                "hops": {
                    "type": "integer",
                    "description": f"Reverse-dependency hops to walk (1-{MAX_BLAST_HOPS})",
                },
                "project": _PROJECT_ARG,
            },
            "required": ["file"],
            "additionalProperties": False,
        },
    },
    {
        "name": "find_definition",
        "description": (
            "Where is this symbol defined? Returns the file, qualified name, kind, "
            "and line for every definition matching the name (leaf name or "
            "qualname). This is the precise structural answer instead of grepping "
            'for "def name". Needs the Code-structure graph automation enabled for '
            "the Project."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Symbol name (leaf like 'run' or qualname like 'Foo.run')",
                },
                "project": _PROJECT_ARG,
            },
            "required": ["name"],
            "additionalProperties": False,
        },
    },
    {
        "name": "find_callers",
        "description": (
            "Who calls into this file (or a specific symbol in it)? Returns the "
            "(file, symbol) pairs whose calls resolve here import-aware, so a "
            "same-named symbol in an unrelated module is not a false caller. A "
            "LOWER BOUND - dynamic dispatch is not shown - so unresolved callers of "
            "the same name are reported separately. This removes the expensive "
            "who-calls-this traversal, not the cheap exact-string grep. Needs the "
            "Code-structure graph automation enabled for the Project."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "file": {
                    "type": "string",
                    "description": "Path (repo-relative or absolute) whose callers to find",
                },
                "symbol": {
                    "type": "string",
                    "description": "Optional symbol in the file; omit for any symbol",
                },
                "project": _PROJECT_ARG,
            },
            "required": ["file"],
            "additionalProperties": False,
        },
    },
    {
        "name": "find_references",
        "description": (
            "Every call or reference to a symbol in this file - the precise "
            "structural neighborhood, not a grep. A lower bound over static edges. "
            "Needs the Code-structure graph automation enabled for the Project."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "file": {
                    "type": "string",
                    "description": "Path (repo-relative or absolute)",
                },
                "symbol": {
                    "type": "string",
                    "description": "Optional symbol in the file; omit for any symbol",
                },
                "project": _PROJECT_ARG,
            },
            "required": ["file"],
            "additionalProperties": False,
        },
    },
    {
        "name": "code_context",
        "description": (
            "A compact structural neighborhood for context packing: for each file, "
            "its key symbol signatures, what it imports, and its direct callers - "
            "instead of you reading whole files. Ranked and token-budgeted. Needs "
            "the Code-structure graph automation enabled for the Project."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "files": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Files (repo-relative or absolute) to pack context for",
                },
                "project": _PROJECT_ARG,
            },
            "required": ["files"],
            "additionalProperties": False,
        },
    },
    {
        "name": "test_gap",
        "description": (
            "Which recently-changed files have no covering test in their blast "
            "radius? Surfaces changed code a test never reaches. A LOWER BOUND: a "
            "test that exercises the code through dynamic dispatch is invisible "
            "here, so a listed file is a candidate, not a proof of missing "
            'coverage. Defaults to your own Project; pass project:"fleet" to widen. '
            "Needs the Code-structure graph automation enabled for the Project."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": _PROJECT_ARG,
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "interrupt",
        "description": (
            "Stop another agent session's current turn. The session keeps living "
            "- its conversation and terminal survive - and the work the turn was "
            "doing is discarded. Use it when a sibling is wedged in a loop. It is "
            "refused unless the target is safe to interrupt right now (never lands "
            "in an approval prompt or a menu). By default this is not granted: the "
            "call writes an inert request a human approves, and the result says "
            "so. Cannot target your own session. "
            'Your own Project is the default; pass project:"fleet" or a Project '
            "name to reach another."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "target": {
                    "type": "string",
                    "description": "Session id or exact name to interrupt",
                },
                "reason": {
                    "type": "string",
                    "description": "Why you are interrupting (recorded, shown to a human)",
                },
                "correlation_id": {
                    "type": "string",
                    "description": (
                        "Idempotency key; a retry with the same value does "
                        "not act twice"
                    ),
                },
                "project": _PROJECT_ARG,
            },
            "required": ["target"],
            "additionalProperties": False,
        },
    },
    {
        "name": "end_session",
        "description": (
            "End an agent session: it goes away. Allowed against yourself, which "
            "is the ordinary case for a finished worker - you end before your "
            "final turn is lost, and your record stays readable through "
            "list_sessions(include_ended), get_session, and history. Ending "
            "another session first tries the harness's own graceful exit, then a "
            "hard stop, and the end is recorded as agent-initiated. By default "
            "this is not granted: the call writes an inert request a human "
            "approves. The session that hosts the daemon and non-agent panes are "
            'never valid targets. Pass project:"fleet" or a Project name to reach '
            "another Project."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "target": {
                    "type": "string",
                    "description": "Session id or exact name to end; 'self' ends your own session",
                },
                "reason": {
                    "type": "string",
                    "description": "Why you are ending it (recorded, shown to a human)",
                },
                "correlation_id": {
                    "type": "string",
                    "description": (
                        "Idempotency key; a retry with the same value does "
                        "not act twice"
                    ),
                },
                "project": _PROJECT_ARG,
            },
            "required": ["target"],
            "additionalProperties": False,
        },
    },
    {
        "name": "request_land",
        "description": (
            "Ask for your worktree branch to be landed on its Project's trunk. "
            "This enqueues a request and performs nothing itself. The daemon then "
            "merges the trunk into your branch, runs the repository's own "
            "verification command, and fast-forwards the trunk - in that order, one "
            "branch at a time, and only while your worktree is clean and no session "
            "in it is mid-turn. A merge conflict or a failed verification comes back "
            "to you as a message naming what stopped it, and your worktree is left "
            "exactly as it was: nothing is committed for you and no conflict is "
            "resolved for you. By default this is not granted, and the call writes "
            "an inert request a human approves. Call it from a session whose cwd is "
            "the worktree you want landed."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": "Why the branch is ready (recorded, shown to a human)",
                },
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "request_verify",
        "description": (
            "Ask for your worktree branch to be verified WITHOUT being landed. "
            "The daemon merges the trunk into your branch and runs the "
            "repository's own verification command, in that order, and stops "
            "there: no trunk moves. The verdict comes back to you as a message - "
            "a failure names what broke and carries the output tail, a pass says "
            "so. A pass is recorded against the exact content it ran over, so a "
            "later request_land of that same content skips the gate instead of "
            "spending it twice; if the trunk moves in between, the merge produces "
            "different content and the gate runs again, which is correct rather "
            "than a miss. Use this instead of running the full suite yourself: "
            "your own run cannot be reused, because only a run this queue "
            "executed counts. Iterate with targeted tests and let this be the "
            "full gate. Call it from a session whose cwd is the worktree you "
            "want verified."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": "Why you are asking (recorded, shown to a human)",
                },
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "watch_session",
        "description": (
            "Be told when another session stops working, instead of polling for "
            "it. Arms a one-shot watch and returns immediately, having delivered "
            "nothing. Exactly one message then enters YOUR prompt queue, "
            "whichever of these happens first: the session leaves working for a "
            "settled state and holds it (idle or awaiting - the notice says "
            "which, because 'awaiting' means blocked on a person and not "
            "finished), the session ends, or your timeout elapses. The timeout "
            "notice is not an error, it is the guarantee: a session that hangs "
            "can never leave you waiting with no message at all. A settle is "
            "measured as a working -> settled edge that holds, so a session that "
            "is already idle when you ask will be answered by the timeout, and a "
            "session idling with its own subagents still running is not counted "
            "as settled. The notice arrives as a queue item under the ordinary "
            "delivery contract, so it reaches you between turns rather than "
            "mid-turn. It is the bounded answer to the watch you armed, so it is "
            "staged armed rather than as an inert draft - but armed is not "
            "delivered, and your own auto-delivery grant and the ordinary queue "
            "gates still decide whether it reaches you without a person. "
            "Bounded: a few watches per session, one per target, and "
            "they die with your session or a daemon restart. "
            'Your own Project is the default; pass project:"fleet" or a Project '
            "name to watch a session in another."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "target": {
                    "type": "string",
                    "description": (
                        "Session id or exact name to watch; cannot be your own "
                        "session"
                    ),
                },
                "timeout_minutes": {
                    "type": "integer",
                    "description": (
                        "How long to wait before the failsafe notice fires "
                        "anyway (default 30). Set it to roughly how long you "
                        "expect the work to take, not to the longest you could "
                        "tolerate: an early timeout notice tells you the state, "
                        "and you can watch again."
                    ),
                },
                "project": _PROJECT_ARG,
            },
            "required": ["target"],
            "additionalProperties": False,
        },
    },
]

#: The configurator agent's tools, listed only to a session the daemon launched
#: as one (`SessionRecord.configurator`). Kept as a separate list rather than a
#: flag on entries in `TOOLS` so that the ordinary surface is still exactly one
#: readable array, and so nothing has to remember to filter: a session that is
#: not a configurator sees `TOOLS`, unchanged and unfiltered, as it always did.
CONFIGURATOR_TOOLS: list[dict[str, Any]] = [
    {
        "name": "configurator_capabilities",
        "description": (
            "This swe-mux install's generated inventory, and which Project this "
            "session is standing in. Carries the harness registry with live "
            "detection, the automation dependency graph with each entry's full "
            "transitive requirement set and whether it can spend money, the MCP "
            "surface, the registered Projects with yours marked, and whether "
            "this install has a source checkout you could edit. Ask for the "
            "`settings` section to get every install-wide setting with its "
            "current value, default, restart requirement, and the constraint its "
            "own validator enforces - it is 197 rows, so it is omitted by "
            "default and `settings_query` narrows it. Every part is derived from "
            "the code that enforces it, so it cannot drift from the truth: read "
            "it instead of guessing what a setting is called or what values it "
            "accepts. Secrets are reported as <set>/<unset> and never by value."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "sections": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Which sections to return: install, settings, "
                        "project_settings, harnesses, automations, mcp_tools, "
                        "guides, projects. Omit for everything except `settings`, "
                        "which is 197 rows and is left out unless you name it."
                    ),
                },
                "settings_query": {
                    "type": "string",
                    "description": (
                        "Substring filter on setting names, applied when the "
                        "`settings` section is returned. Use it: 'theme', "
                        "'voice', 'budget' narrow 197 rows to a handful."
                    ),
                },
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "configurator_guide",
        "description": (
            "The guides that ship with this build, explaining how swe-mux is "
            "meant to be configured and why. Call with no argument for the "
            "index (id, title, summary); call with an id for that guide's full "
            "text. Start with `orientation` if the operator is new. These are "
            "design rationale, not a manual: they are what tells you that an "
            "empty analysis panel is usually an opt-in nobody has switched on "
            "rather than a bug."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {
                    "type": "string",
                    "description": "Guide id from the index; omit to get the index",
                }
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "configurator_diagnostics",
        "description": (
            "This install's health report: prerequisite checks, remote-access "
            "and firewall state, supervisor and background-loop health, harness "
            "detection, and the observation-freshness rows that nothing else "
            "exposes. Each check carries a severity separating an unavailable "
            "optional feature from a safety-critical failure. Read it before "
            "agreeing that something is broken, and before proposing a fix for "
            "a symptom whose cause is listed here."
        ),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "configurator_apply_settings",
        "description": (
            "Change install-wide settings. Takes a `changes` object of setting "
            "name to new value, exactly as `configurator_capabilities` names "
            "them. It runs the same validated path the Settings panel uses: "
            "every value is checked before anything is written, so an invalid "
            "batch changes nothing and comes back naming the offending fields. "
            "The result reports which settings applied immediately and which "
            "need a daemon restart to take effect - tell the operator which, "
            "and never restart the daemon on your own initiative. Ask before "
            "calling this; a settings change is the operator's decision and you "
            "are advising it."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "changes": {
                    "type": "object",
                    "description": (
                        "Setting name to new value. Names and accepted values "
                        "come from `configurator_capabilities`."
                    ),
                }
            },
            "required": ["changes"],
            "additionalProperties": False,
        },
    },
    {
        "name": "configurator_device_settings",
        "description": (
            "The per-device UI settings - the command rail, sounds, alerts, push "
            "notifications, drawer tabs, sidebar rows, the file tree. A different "
            "store from install-wide config, and where most 'change how the UI is "
            "arranged' questions actually live. "
            "With no arguments it answers the index: which profiles hold which "
            "domains, how large each is, and which two the daemon interprets "
            "rather than storing verbatim. Name a `domain` to get its document "
            "plus the `digest` a write must present. "
            "For `commandRail` it also returns a resolved reading: every row with "
            "its items' labels, the exact path an edit would name, and every "
            "per-Project override resolved to its Project NAME with yours marked "
            "- never read an override as this Project's merely because it is the "
            "only one present. The rail lives in one document under the `desktop` "
            "profile and carries both device layouts inside it."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "domain": {
                    "type": "string",
                    "description": (
                        "alerts, sounds, notifications, commandRail, fileTree, "
                        "drawerTabs, or sessionRows. Omit for the index."
                    ),
                },
                "profile": {
                    "type": "string",
                    "description": (
                        "desktop or mobile. Omit and the right one is chosen - "
                        "notably `commandRail` is always under `desktop`."
                    ),
                },
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "configurator_edit_device_settings",
        "description": (
            "Change one per-device UI settings domain with path-scoped "
            "operations. Read the domain first and pass back the `digest` you "
            "read, so an edit the operator made in between is refused rather "
            "than silently discarded. "
            "Operations, applied in order and all-or-nothing: `set` "
            "{path, value}; `remove` {path}; `remove_values` {path, values[]} to "
            "take named entries out of an array regardless of order; `insert` "
            "{path, value, after|before|index}. "
            "Paths are JSON Pointer with one addition: `[key=value]` selects the "
            "element of an array whose field matches, so name a row by its id "
            "(`/layouts/mobile/strip/[id=row-2]/items`) rather than by position. "
            "Never resend a whole document: nothing in the daemon can validate an "
            "opaque domain, so anything replaced wholesale is yours to get right, "
            "while an operation cannot lose what it did not name. The previous "
            "file is kept beside itself on every write. "
            "Default to the GLOBAL scope: an unqualified request to change the "
            "rail means `/layouts`, not a per-Project override under `/projects`."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "domain": {"type": "string", "description": "The domain to edit"},
                "profile": {
                    "type": "string",
                    "description": "desktop or mobile; omit for the domain's own store",
                },
                "operations": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "Ordered operations; all apply or none do",
                },
                "expect_digest": {
                    "type": "string",
                    "description": (
                        "The digest from the read this edit was composed against. "
                        "Omitting it skips the concurrency check; only do that "
                        "when you have just read and are the only writer."
                    ),
                },
            },
            "required": ["domain", "operations"],
            "additionalProperties": False,
        },
    },
    {
        "name": "configurator_project_settings",
        "description": (
            "One Project's own committed configuration (`.swe-mux/config.toml`) "
            "and what it resolves to: the automation opt-ins with the ones that "
            "are actually *effective* separated from the ones still blocked by a "
            "dependency, the agent authority grants, the worktree commands. "
            "This is where the answer to 'why is this panel empty' lives - a "
            "consumer switched on without its substrate is inert, not broken. "
            "Defaults to the Project this session is in; name another to read it. "
            "The file is committed and shared with everyone who clones that "
            "repository, which is why some fields are refused outright."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {
                    "type": "string",
                    "description": "Project id or name; omit for this session's own",
                }
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "configurator_apply_project_settings",
        "description": (
            "Change one Project's committed configuration. Takes a `changes` "
            "object of field to value, merged over what is there; validated "
            "against the closed project field set, which refuses this daemon's "
            "own authority keys (token, host, port, command) outright rather "
            "than ignoring them. Revision-guarded, so an edit made elsewhere in "
            "between is a refusal rather than a clobber. "
            "Defaults to this session's Project; name another explicitly. "
            "Say out loud that this file is committed: turning an automation on "
            "here turns it on for everyone who clones that repository."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {
                    "type": "string",
                    "description": "Project id or name; omit for this session's own",
                },
                "changes": {
                    "type": "object",
                    "description": (
                        "Field to value, merged over the current file. Names come "
                        "from `configurator_project_settings`."
                    ),
                },
            },
            "required": ["changes"],
            "additionalProperties": False,
        },
    },
]

_DECLARED_TOOL_NAMES = {str(tool["name"]) for tool in TOOLS}
assert _DECLARED_TOOL_NAMES == set(READ_TOOL_NAMES) | set(WRITE_TOOL_NAMES)
_CONFIGURATOR_TOOL_NAMES = frozenset(CONFIGURATOR_READ_TOOL_NAMES) | frozenset(
    CONFIGURATOR_WRITE_TOOL_NAMES
)
_DECLARED_CONFIGURATOR_NAMES = {str(tool["name"]) for tool in CONFIGURATOR_TOOLS}
assert _DECLARED_CONFIGURATOR_NAMES == _CONFIGURATOR_TOOL_NAMES
# The two families must not overlap: a name in both would be gated by whichever
# check ran first, which is exactly the kind of authority question that must not
# depend on statement order.
assert not (_DECLARED_TOOL_NAMES & _CONFIGURATOR_TOOL_NAMES)
_READ_ONLY_TOOL_NAMES = frozenset(READ_TOOL_NAMES) | frozenset(CONFIGURATOR_READ_TOOL_NAMES)
for _tool in (*TOOLS, *CONFIGURATOR_TOOLS):
    # The read/write split is the single source for both the Claude permission
    # allowlist and these annotations, so a tool cannot be auto-allowed while
    # advertising itself as a write. `watch_session` is the one entry whose
    # placement needed an argument rather than being obvious; it is recorded in
    # `mcp_contract.py` beside the name.
    _read_only = str(_tool["name"]) in _READ_ONLY_TOOL_NAMES
    _tool["annotations"] = {
        "readOnlyHint": _read_only,
        "destructiveHint": False,
        "idempotentHint": _read_only,
        "openWorldHint": False,
    }


def tools_for(caller: Any) -> list[dict[str, Any]]:
    """The tool list this caller is allowed to see.

    Listing is the same gate as calling, deliberately. A tool advertised and then
    refused teaches an agent that the surface lies to it, and the refusal arrives
    only after it has already planned around the capability.
    """
    if bool(getattr(getattr(caller, "record", None), "configurator", False)):
        return [*TOOLS, *CONFIGURATOR_TOOLS]
    return TOOLS


@dataclass(slots=True)
class _ParseFlight:
    """One in-progress transcript parse, and the deadline that belongs to it.

    The deadline is a property of the *flight*, not of whoever is waiting on it:
    a caller that arrives late waits for what is left of the one parse, and a
    caller that is told to retry does not restart the clock by asking again.
    """

    signature: str
    task: asyncio.Task[dict[str, Any]]
    deadline: float


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
        tier0: Any = None,
        automation_gate: Any = None,
        session_control: Any = None,
        code_graph: Any = None,
        land_queue: Any = None,
        scan_timeline_service: Any = None,
        session_watch: Any = None,
        configurator: Any = None,
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
        # Phase 7.5 memory reads. Tier 0 is the deterministic fact store; the
        # gate is the per-project enablement closure (an async `(root) ->
        # frozenset[str]`). Absent either, the memory tools answer `unsupported`
        # rather than a fake empty. The experience corpus, scan records, and git
        # provenance are reached through `automation_store` and `history`, which
        # are already injected above.
        self.tier0 = tier0
        self.automation_gate = automation_gate
        # Phase 7.6 session control. Absent (tests, minimal wiring) the tools list
        # but answer unavailable - never a partial actuation.
        self.session_control = session_control
        # Phase 7.9 code-structure graph store. Absent it, the structural reads
        # answer `unsupported` (the substrate is not running), never a fake empty.
        self.code_graph = code_graph
        # Phase 14 land queue. Absent it, `request_land` and `request_verify` answer
        # unavailable rather than half-enqueueing - the rule the write tools follow.
        self.land_queue = land_queue
        # Phase 7.11 scan timeline. Only its `liveness` block is read from here -
        # the records come from the store - so absent it a scan read still
        # answers, and says the liveness block is unavailable rather than
        # reporting a stopped scanner as a quiet one.
        self.scan_timeline_service = scan_timeline_service
        # Session-settle watches. Absent it, `watch_session` answers unavailable
        # rather than arming nothing and reporting success - a watch that was
        # never armed is the exact silence the tool exists to remove.
        self.session_watch = session_watch
        # The configurator family's backing service (`configurator.py`). Absent
        # it, the tools answer unavailable rather than a fake empty inventory -
        # an agent told "no settings exist" would confidently advise nonsense,
        # which is worse than being told the surface is not wired.
        self.configurator = configurator
        self.calls = 0
        self.denied = 0
        self.writes = 0
        # One transcript parse in flight per transcript (F24). Keyed by path,
        # cleared when the worker thread actually finishes - not when a caller
        # gives up waiting for it.
        self._transcript_flights: dict[str, _ParseFlight] = {}
        self.parse_timeouts = 0
        self.parse_refusals = 0
        self.tool_stats: dict[str, dict[str, int]] = {}
        # Retrieval-outcome measurement for the Phase 7.5 memory tools (ROADMAP
        # 7.5): per tool, how often it returned something, returned empty, and how
        # many low-confidence items it withheld. A tool that only ever returns
        # empty is a defect to fix, not a feature to leave running - this is the
        # signal that surfaces it.
        self.memory_outcomes: dict[str, dict[str, int]] = {}

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
        return await session_titles.generated_titles(self.automation_store, run_ids)

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
        return session_titles.record_run_id(record)

    @staticmethod
    def _row_run_id(row: dict[str, Any]) -> str:
        return session_titles.row_run_id(row)

    def _record_display_name(self, record: Any, titles: dict[str, str]) -> str:
        return session_titles.record_display_name(record, titles)

    def _row_display_name(self, row: dict[str, Any], titles: dict[str, str]) -> str:
        return session_titles.row_display_name(row, titles)

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
        # Per-item size accounting (F24). The previous shape re-serialized the
        # entire result - every session item included - once per item popped,
        # which is quadratic in the page exactly when the page is at its largest.
        # Each item is measured once here; only the small envelope (whose `count`
        # and `next_cursor` do change as the tail is trimmed) is re-measured.
        prefix_bytes = [0]
        for _key, _kind, item in selected:
            prefix_bytes.append(
                prefix_bytes[-1] + len(json.dumps(item, ensure_ascii=False).encode("utf-8"))
            )
        live_count = sum(1 for _key, kind, _item in selected if kind == "live")
        ended_count = len(selected) - live_count
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
            result: dict[str, Any] = {
                "sessions": [],
                "count": len(selected),
                "has_more": has_more,
                "next_cursor": next_cursor,
                **self._scope_envelope(scope, hidden),
            }
            if include_ended:
                result["ended_sessions"] = []
            # `json.dumps` puts ", " between array elements by default, so a
            # populated array costs its items plus two bytes per gap. That makes
            # this an exact size, not an estimate - `test_mcp.py` pins it.
            gaps = 2 * (max(0, live_count - 1) + max(0, ended_count - 1))
            total = (
                len(json.dumps(result, ensure_ascii=False).encode("utf-8"))
                + prefix_bytes[len(selected)]
                + gaps
            )
            if total <= LIST_MAX_BYTES:
                result["sessions"] = [
                    item for _key, kind, item in selected if kind == "live"
                ]
                if include_ended:
                    result["ended_sessions"] = [
                        item for _key, kind, item in selected if kind == "ended"
                    ]
                return result
            _key, popped_kind, _item = selected.pop()
            if popped_kind == "live":
                live_count -= 1
            else:
                ended_count -= 1

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
                page = await self._transcript_page(
                    path,
                    backend,
                    direction="head",
                    anchor=None,
                    max_messages=8,
                    include_system=False,
                    native_id=native_id,
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

    def _retire_flight(self, key: str, flight: _ParseFlight) -> None:
        """Drop a finished flight, and never leave its exception unretrieved."""
        if self._transcript_flights.get(key) is flight:
            self._transcript_flights.pop(key, None)
        if not flight.task.cancelled():
            # The caller that started this parse may already have given up, in
            # which case nobody will ever await the task; reading the exception
            # here keeps that from surfacing as an asyncio "never retrieved"
            # warning with no owner attached to it.
            flight.task.exception()

    async def _transcript_page(
        self,
        path: Path | None,
        backend: str,
        *,
        direction: str,
        anchor: Any,
        max_messages: int,
        include_system: bool,
        native_id: str | None,
    ) -> dict[str, Any]:
        """One bounded transcript parse, single-flight per transcript (F24).

        The old shape was `wait_for(to_thread(...))`: a slow parse blew the
        timeout, the caller was told "retry", and the worker thread kept running
        in the shared default executor. Every retry added another. A transcript
        pathological enough to time out once is pathological every time, so the
        advice to retry was itself the amplifier.

        Two rules replace it. The flight lives until the *thread* finishes, so a
        retry joins the running parse instead of starting a second one; and the
        deadline belongs to the flight, so a chain of retries cannot each buy a
        fresh `PARSE_TIMEOUT_SECONDS` of the same work.

        Raises `TimeoutError` when the flight's deadline passes with no result,
        and whatever the parser raised (`OSError`) otherwise - both of which the
        callers already translate.
        """
        loop = asyncio.get_running_loop()
        key = str(path)
        signature = _query_signature(
            {
                "backend": backend,
                "direction": direction,
                "anchor": anchor,
                "max_messages": max_messages,
                "include_system": include_system,
                "native_id": native_id or "",
            }
        )
        flight = self._transcript_flights.get(key)
        if flight is not None and flight.task.done():
            self._retire_flight(key, flight)
            flight = None
        if flight is not None and flight.signature != signature:
            # A different page of a transcript that is already being parsed. Two
            # threads over one pathological file is the stacking this exists to
            # prevent, so this asks for a retry *without* starting work.
            self.parse_refusals += 1
            log.warning(
                "transcript parse refused, another page of the same transcript "
                "is still parsing path=%s overdue=%.1fs",
                key,
                max(0.0, loop.time() - flight.deadline),
            )
            raise TimeoutError("another read of this transcript is still running")
        if flight is None:
            task = asyncio.ensure_future(
                asyncio.to_thread(
                    transcript_message_page,
                    path,
                    backend,
                    direction=direction,
                    anchor=anchor,
                    max_bytes=TRANSCRIPT_MAX_BYTES,
                    max_messages=max_messages,
                    include_system=include_system,
                    native_id=native_id,
                )
            )
            flight = _ParseFlight(signature, task, loop.time() + PARSE_TIMEOUT_SECONDS)
            self._transcript_flights[key] = flight
            started = flight
            task.add_done_callback(lambda _t: self._retire_flight(key, started))
        remaining = flight.deadline - loop.time()
        if remaining <= 0:
            self.parse_timeouts += 1
            raise TimeoutError("transcript parse deadline already passed")
        try:
            # Shielded: a caller giving up must not cancel the parse everyone
            # else is waiting on, and cancelling would not stop the thread anyway.
            return await asyncio.wait_for(asyncio.shield(flight.task), timeout=remaining)
        except TimeoutError:
            self.parse_timeouts += 1
            log.warning(
                "transcript parse exceeded its deadline path=%s timeout=%.1fs",
                key,
                PARSE_TIMEOUT_SECONDS,
            )
            raise

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
            page = await self._transcript_page(
                path,
                backend,
                direction=direction,
                anchor=(cursor or {}).get("anchor"),
                max_messages=max_messages,
                include_system=include_system,
                native_id=native_id,
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
            # Messages in this window that belong to a branch the conversation
            # left - a retried turn, a `/rewind`. They are not in `messages`
            # because they are not what the conversation says, and the count is
            # here so a reader can tell a retried run from a short one.
            "abandoned_messages": int(page.get("abandoned_messages") or 0),
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
        except KeyError as exc:
            # The catalog is re-read inside the runner, so an action can vanish
            # between the ownership check above and the run. Typed here because
            # `handle_rpc` no longer treats a bare KeyError as "not found" (F24)
            # - and this one genuinely is.
            raise QueueError(
                "unknown_action",
                f"action {action_id!r} is no longer declared by "
                f"{owner.name!r}; call project_actions again.",
                status=404,
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

    # --------------------------------------------------- memory reads (7.5)

    def _record_memory_outcome(
        self, tool: str, *, returned: int, suppressed: int
    ) -> None:
        """Count what a memory read produced, so a dead tool is visible."""
        stats = self.memory_outcomes.setdefault(
            tool, {"calls": 0, "returned": 0, "empty": 0, "suppressed": 0}
        )
        stats["calls"] += 1
        if returned > 0:
            stats["returned"] += 1
        else:
            stats["empty"] += 1
        stats["suppressed"] += suppressed

    async def _caller_run_ids(self, caller: Any) -> tuple[str, set[str]]:
        """The caller's current run id, and every run id the caller has owned.

        A retrieved memory that came from one of the caller's own superseded
        runs must be labelled as such rather than blended into the present: after
        a `/clear` the agent has no memory of the work its predecessor run did,
        so an unlabelled result from that run reads as its own recollection
        (ROADMAP 7.5). Superseded runs are read from history, the same source
        `get_session` uses for a caller's own superseded-run list.
        """
        current = self._record_run_id(caller.record)
        owned = {current}
        reader = getattr(self.history, "agent_runs_for_session", None)
        if callable(reader):
            for row in await reader(caller.record.id):
                run_id = self._row_run_id(row)
                if run_id:
                    owned.add(run_id)
        return current, owned

    @staticmethod
    def _run_attribution(
        agent_run_id: str, current_run: str, owned_runs: set[str]
    ) -> dict[str, Any]:
        """Name the run a memory came from, and its relation to the caller."""
        run = str(agent_run_id or "")
        if not run:
            return {"agent_run_id": None, "run_relation": "unknown"}
        if run == current_run:
            return {"agent_run_id": run, "run_relation": "your_current_run"}
        if run in owned_runs:
            return {
                "agent_run_id": run,
                "run_relation": "your_earlier_run",
                "superseded": True,
                "note": (
                    "your own earlier run, before a /clear or /new; you have no "
                    "memory of the work it did, so treat it as a sibling's"
                ),
            }
        return {"agent_run_id": run, "run_relation": "sibling_run"}

    def _project_scope_id(self, project: Any) -> str | None:
        """The git-identity scope id for a Project, borrowed from a live session.

        The experience corpus is keyed by `project_scope_id`, which a registered
        Project object does not carry; a live session in the Project does. The
        caller is always a live session in its own Project, so the default
        (own-Project) path always resolves; a widened call resolves only when the
        named Project has a live session, and returns `None` (no filter) rather
        than guessing otherwise.
        """
        for session in self.sessions.sessions.values():
            if str(getattr(session.record, "project_id", "") or "") == str(project.id):
                scope_id = str(getattr(session.record, "project_scope_id", "") or "")
                if scope_id:
                    return scope_id
        return None

    async def _memory_scope(
        self, caller: Any, args: dict[str, Any], tool_name: str
    ) -> tuple[ProjectScope, list[Any], list[Any], bool]:
        """Resolve scope and the Projects opted into one memory tool.

        Fails with an explicit typed code rather than a fake empty: `unsupported`
        when the daemon does not run the substrate at all, `disabled` when no
        Project in scope has opted the backing automation in. An agent that
        cannot tell "off" from "nothing here" either stops calling or trusts a
        silence it should not (ROADMAP 7.5).
        """
        automation_id = MEMORY_TOOL_AUTOMATION[tool_name]
        if self.tier0 is None or self.automation_gate is None:
            raise QueueError(
                "unsupported",
                f"{tool_name} needs the control-plane memory substrate, which "
                "this daemon does not run.",
                status=503,
            )
        scope = self._requested_scope(caller, args)
        projects, truncated = self._scoped_projects(caller, args)
        enabled: list[Any] = []
        disabled: list[Any] = []
        for project in projects:
            ids = await self.automation_gate(str(project.root))
            (enabled if automation_id in ids else disabled).append(project)
        if not enabled:
            label = automation_id.replace("_", " ")
            raise QueueError(
                "disabled",
                f"the '{label}' automation is not enabled for {scope.label}. "
                "Enable it in the Project's automation settings for this tool to "
                "read anything.",
                status=409,
                automation=automation_id,
            )
        return scope, enabled, disabled, truncated

    @staticmethod
    def _disabled_note(disabled: list[Any], automation_id: str) -> dict[str, Any]:
        if not disabled:
            return {}
        return {
            "not_opted_in": [
                {"id": str(project.id), "name": str(project.name)}
                for project in disabled
            ],
            "not_opted_in_note": (
                f"{len(disabled)} Project(s) in scope have not enabled "
                f"'{automation_id.replace('_', ' ')}' and were not read."
            ),
        }

    async def provenance(self, caller: Any, args: dict[str, Any]) -> dict[str, Any]:
        """`mux.provenance(file)`: file lineage across sessions (CP §6.1).

        Lineage, never blame. It reports that session B wrote a hash to a file
        and session A later read it, and the tests those runs ran; it never
        asserts that B caused A to fail. Ambiguous edges (another write landed
        between the reported write and the read) are withheld from the result and
        only counted, because an uncertain writer is exactly the weak match the
        precision gate exists to suppress.
        """
        target_arg = str(args.get("file") or "").strip()
        if not target_arg:
            raise ValueError("file is required")
        scope, projects, disabled, _truncated = await self._memory_scope(
            caller, args, "provenance"
        )
        current_run, owned = await self._caller_run_ids(caller)
        touches: list[dict[str, Any]] = []
        edges: list[dict[str, Any]] = []
        tests: list[dict[str, Any]] = []
        suppressed = 0
        for project in projects:
            root = str(project.root)
            normalized = normalize_target(target_arg, root)
            if not normalized:
                continue
            facts = await self.tier0.facts_for_project(
                str(project.id), limit=PROVENANCE_FACT_SCAN_LIMIT
            )
            run_by_fact = {
                str(fact.get("id")): str(fact.get("agent_run_id") or "")
                for fact in facts
            }
            writer_runs: set[str] = set()
            for fact in facts:
                if normalize_target(fact.get("target"), root) != normalized:
                    continue
                kind = str(fact.get("kind") or "")
                is_write = kind in PROVENANCE_WRITE_KINDS
                is_read = kind in PROVENANCE_READ_KINDS
                if not (is_write or is_read):
                    continue
                run = str(fact.get("agent_run_id") or "")
                if is_write:
                    writer_runs.add(run)
                touches.append(
                    {
                        "action": "write" if is_write else "read",
                        "session_id": str(fact.get("session_id") or ""),
                        "run": self._run_attribution(run, current_run, owned),
                        "content_hash": fact.get("content_hash"),
                        "at": fact.get("created_at"),
                        "project_id": str(project.id),
                    }
                )
            for edge in build_provenance_edges(facts, project_root=root):
                if edge.target != normalized:
                    continue
                if edge.ambiguous:
                    suppressed += 1
                    continue
                edges.append(
                    {
                        "target": edge.target,
                        "content_hash": edge.writer_content_hash,
                        "writer": self._run_attribution(
                            run_by_fact.get(edge.writer_fact_id, ""),
                            current_run,
                            owned,
                        ),
                        "writer_session_id": edge.writer_session_id,
                        "written_at": edge.written_at,
                        "reader": self._run_attribution(
                            run_by_fact.get(edge.reader_fact_id, ""),
                            current_run,
                            owned,
                        ),
                        "reader_session_id": edge.reader_session_id,
                        "read_at": edge.read_at,
                        "project_id": str(project.id),
                    }
                )
            for fact in facts:
                if str(fact.get("kind") or "") != "test_result":
                    continue
                if str(fact.get("agent_run_id") or "") not in writer_runs:
                    continue
                detail = _load_detail(fact)
                tests.append(
                    {
                        "run": self._run_attribution(
                            str(fact.get("agent_run_id") or ""), current_run, owned
                        ),
                        "outcome": detail.get("test_outcome"),
                        "target": fact.get("target"),
                        "at": fact.get("created_at"),
                        "project_id": str(project.id),
                    }
                )
        touches.sort(key=lambda item: item.get("at") or 0.0)
        self._record_memory_outcome(
            "provenance", returned=len(touches) + len(edges), suppressed=suppressed
        )
        return {
            "file": target_arg,
            "touches": touches[:MEMORY_MAX_RESULTS],
            "cross_session_edges": edges[:MEMORY_MAX_RESULTS],
            "tests": tests[:MEMORY_MAX_RESULTS],
            "ambiguous_suppressed": suppressed,
            "note": (
                "Lineage only: a write followed by a read is not a cause of a "
                "failure. Ambiguous edges are withheld and counted."
            ),
            **self._disabled_note(disabled, "provenance_graph"),
            **self._scope_envelope(scope),
        }

    async def verified_status(
        self, caller: Any, args: dict[str, Any]
    ) -> dict[str, Any]:
        """`mux.verifiedStatus(claim)`: tested, or only declared done (CP §6.3)."""
        claim = str(args.get("claim") or "").strip()
        if not claim:
            raise ValueError("claim is required")
        scope, projects, disabled, _truncated = await self._memory_scope(
            caller, args, "verified_status"
        )
        current_run, owned = await self._caller_run_ids(caller)
        identity = str(args.get("session_id") or "self").strip() or "self"
        run_id = ""
        session_id = ""
        try:
            session, _display = await self._resolve_live(caller, identity, scope)
        except KeyError:
            session = None
        if session is not None:
            run_id = self._record_run_id(session.record)
            session_id = str(session.record.id)
        else:
            try:
                row, _display = await self._resolve_history(caller, identity, scope)
            except KeyError as exc:
                raise QueueError(
                    "unknown_target",
                    f"no session {identity!r} in {scope.label} to check the claim "
                    "against.",
                    status=404,
                ) from exc
            run_id = self._row_run_id(row)
            session_id = str(row.get("id") or "")
        facts = await self.tier0.facts_for_run(run_id) if run_id else []
        finding = detect_declared_vs_verified(claim, facts)
        checked = {
            "session_id": session_id,
            "run": self._run_attribution(run_id, current_run, owned),
        }
        if finding is not None:
            result: dict[str, Any] = {
                "declared": finding.declared,
                "tests_ran": finding.tests_ran,
                "tests_passed": finding.tests_passed,
                "verified": bool(finding.tests_ran and finding.tests_passed),
                "claim": finding.claim,
                "status": finding.content,
                "evidence": finding.evidence[:MEMORY_MAX_RESULTS],
            }
        elif claim_match(claim) is not None:
            # `detect_declared_vs_verified` returns None for three different
            # things, and only one of them is a green run. Which one it was is
            # decided here, from the run's own test facts — reporting "verified"
            # for a run that captured no test facts would be the exact collapse
            # the three-way split exists to prevent.
            ran = [fact for fact in facts if str(fact.get("kind") or "") == "test_result"]
            result = {
                "declared": True,
                "tests_ran": bool(ran),
                "tests_passed": bool(ran),
                "verified": bool(ran),
                "claim": claim[:240],
                "status": (
                    "claims done · tests ran · tests passed"
                    if ran
                    else "claims done · no test facts recorded for this run · "
                    "unverifiable from here, which is a statement about capture "
                    "rather than about the claim"
                ),
            }
        else:
            result = {
                "declared": False,
                "tests_ran": False,
                "tests_passed": False,
                "verified": False,
                "claim": claim[:240],
                "status": "no done/fixed/works claim detected in the text",
            }
        result["checked"] = checked
        result.update(self._disabled_note(disabled, "declared_vs_verified"))
        result.update(self._scope_envelope(scope))
        self._record_memory_outcome(
            "verified_status",
            returned=1 if result.get("declared") else 0,
            suppressed=0,
        )
        return result

    async def prior_resolutions(
        self, caller: Any, args: dict[str, Any]
    ) -> dict[str, Any]:
        """`mux.priorResolutions(error)`: a verified fix for this error (CP §6.10).

        Equality on the normalized error signature, never a substring: a
        usually-wrong prior resolution poisons trust in the whole surface, so a
        near-miss returns nothing. Low-confidence matches are withheld and only
        counted.
        """
        error = str(args.get("error") or "").strip()
        if not error:
            raise ValueError("error is required")
        scope, projects, disabled, _truncated = await self._memory_scope(
            caller, args, "prior_resolutions"
        )
        current_run, owned = await self._caller_run_ids(caller)
        results: list[dict[str, Any]] = []
        suppressed = 0
        seen: set[str] = set()
        scope_ids: list[str | None]
        if scope.fleet:
            scope_ids = [None]
        else:
            scope_ids = [self._project_scope_id(project) for project in projects]
        for scope_id in scope_ids:
            rows = await self.automation_store.experiences(
                error=error, project_scope_id=scope_id, limit=MEMORY_MAX_RESULTS
            )
            for row in rows:
                identity = str(row.get("id") or "")
                if identity in seen:
                    continue
                seen.add(identity)
                raw_conf = row.get("confidence")
                confidence = (
                    float(raw_conf) if isinstance(raw_conf, int | float) else 0.0
                )
                if confidence < PRIOR_RESOLUTION_MIN_CONFIDENCE:
                    suppressed += 1
                    continue
                results.append(
                    {
                        "resolution": _redact(str(row.get("resolution_summary") or "")),
                        "error_summary": _redact(str(row.get("error_summary") or "")),
                        "confidence": confidence,
                        "backend": row.get("backend"),
                        "source_run": self._run_attribution(
                            str(row.get("source_run_id") or ""), current_run, owned
                        ),
                        "recorded_at": row.get("created_at"),
                        "project_scope_id": row.get("project_scope_id"),
                    }
                )
        results.sort(key=lambda item: item.get("confidence") or 0.0, reverse=True)
        self._record_memory_outcome(
            "prior_resolutions", returned=len(results), suppressed=suppressed
        )
        return {
            "error": error[:2000],
            "resolutions": results[:MEMORY_MAX_RESULTS],
            "low_confidence_suppressed": suppressed,
            "note": (
                "Matched on the exact normalized error signature. Empty means no "
                "verified prior fix for this signature in scope."
            ),
            **self._disabled_note(disabled, "prior_resolutions"),
            **self._scope_envelope(scope),
        }

    async def dead_ends(self, caller: Any, args: dict[str, Any]) -> dict[str, Any]:
        """`mux.deadEnds(subsystem)`: approaches tried and abandoned (CP §6.2).

        A scan record is already run-scoped, and a conversation rollover writes a
        boundary rather than a record, so filtering to records whose approach was
        abandoned or failed within their run structurally excludes `/clear` from
        counting as an abandonment. Low-confidence records are withheld.
        """
        subsystem = str(args.get("subsystem") or "").strip()
        scope, projects, disabled, _truncated = await self._memory_scope(
            caller, args, "dead_ends"
        )
        current_run, owned = await self._caller_run_ids(caller)
        results: list[dict[str, Any]] = []
        suppressed = 0
        for project in projects:
            records = await self.automation_store.scan_records(
                project_id=str(project.id), limit=2000
            )
            for record in records:
                status = str(record.get("approach_status") or "")
                dead = str(record.get("dead_end") or "").strip()
                if status not in {"abandoned", "failed"} or not dead:
                    continue
                targets = [str(item) for item in (record.get("target") or [])]
                intent = str(record.get("intent") or "")
                summary = str(record.get("summary") or "")
                if subsystem and not _subsystem_matches(
                    subsystem, targets, dead, intent, summary
                ):
                    continue
                raw_conf = record.get("confidence")
                confidence = (
                    float(raw_conf) if isinstance(raw_conf, int | float) else 0.0
                )
                if confidence < DEAD_END_MIN_CONFIDENCE:
                    suppressed += 1
                    continue
                results.append(
                    {
                        "dead_end": _redact(dead),
                        "approach_status": status,
                        "intent": _redact(intent),
                        "summary": _redact(summary),
                        "targets": targets[:20],
                        "confidence": confidence,
                        "run": self._run_attribution(
                            str(record.get("agent_run_id") or ""), current_run, owned
                        ),
                        "at": record.get("t1") or record.get("created_at"),
                        "project_id": str(project.id),
                    }
                )
        results.sort(key=lambda item: item.get("at") or 0.0, reverse=True)
        self._record_memory_outcome(
            "dead_ends", returned=len(results), suppressed=suppressed
        )
        return {
            "subsystem": subsystem or None,
            "dead_ends": results[:MEMORY_MAX_RESULTS],
            "low_confidence_suppressed": suppressed,
            "note": (
                "Approaches abandoned or failed within a run. A conversation "
                "rollover is not counted as an abandonment."
            ),
            **self._disabled_note(disabled, "dead_end_memory"),
            **self._scope_envelope(scope),
        }

    async def doc_debt(self, caller: Any, args: dict[str, Any]) -> dict[str, Any]:
        """`mux.docDebt()`: which docs owe an update for recent source changes.

        Re-derived from each doc's "Key files" section (`build_doc_ownership`
        inverted to `doc -> changed files`), never scraped from the doc-debt
        annotation, whose `content` is a human sentence and whose flat lists carry
        no per-doc mapping. Same window and rules as the detector that writes the
        annotation: a 24h Project fact window, and a doc edited inside it is not
        counted, because the debt was paid as it was incurred.
        """
        scope, projects, disabled, _truncated = await self._memory_scope(
            caller, args, "doc_debt"
        )
        since = time.time() - PROJECT_FACT_WINDOW_SECONDS
        results: list[dict[str, Any]] = []
        for project in projects:
            root = str(project.root)
            ownership = await asyncio.to_thread(
                cached_doc_ownership, Path(root) / ".docs"
            )
            if not ownership:
                continue
            facts = await self.tier0.facts_for_project(str(project.id), since=since)
            per_doc = build_doc_debt_map(facts, ownership, project_root=root)
            for doc, changed in per_doc.items():
                results.append(
                    {
                        "doc": doc,
                        "changed_files": list(changed[:MEMORY_MAX_RESULTS]),
                        "project_id": str(project.id),
                    }
                )
        results.sort(key=lambda item: len(item.get("changed_files") or []), reverse=True)
        self._record_memory_outcome("doc_debt", returned=len(results), suppressed=0)
        return {
            "docs": results[:MEMORY_MAX_RESULTS],
            "note": (
                "Each doc owes an update for the listed changed files. Empty is "
                "not proof the docs are current: a file no doc lists in a 'Key "
                "files' section owns no doc and produces no debt."
            ),
            **self._disabled_note(disabled, "doc_debt"),
            **self._scope_envelope(scope),
        }

    # --------------------------------------------- scan timeline reads (7.11)

    async def _scan_target(
        self, caller: Any, args: dict[str, Any], tool_name: str
    ) -> tuple[ProjectScope, str, str, Any]:
        """Resolve a session-scoped scan read: scope, session id, run id, session.

        Session-scoped, so the gate is the **target session's** Project rather
        than the caller's scoped Project set: `_memory_scope` answers "which of
        the Projects I am allowed to see opted in", which is the right question
        for a Project-wide read and the wrong one for a read that names one
        session in one Project.

        An ended session resolves through history, because its records outlive
        it and "what did that finished sibling do" is the read this tool exists
        for. The returned session is `None` when it has ended.
        """
        automation_id = MEMORY_TOOL_AUTOMATION[tool_name]
        if self.automation_store is None or self.automation_gate is None:
            raise QueueError(
                "unsupported",
                f"{tool_name} needs the scan-timeline substrate, which this "
                "daemon does not run.",
                status=503,
            )
        scope = self._requested_scope(caller, args)
        identity = str(args.get("session_id") or "self").strip() or "self"
        session: Any = None
        try:
            session, _display = await self._resolve_live(caller, identity, scope)
        except KeyError:
            session = None
        if session is not None:
            session_id = str(session.record.id)
            run_id = str(session.record.agent_run_id or "")
            project_id = str(session.record.project_id or "")
        else:
            row, _display = await self._resolve_history(caller, identity, scope)
            # A history row's `id` is the agent run id and its `note_id` is the
            # session the run belonged to; scan records are keyed by the latter.
            run_id = str(row.get("id") or "")
            session_id = str(row.get("note_id") or "") or run_id
            project_id = str(row.get("project_id") or "")
        project = (
            self.projects.projects.get(project_id)
            if project_id and self.projects is not None
            else None
        )
        if project is None:
            raise QueueError(
                "disabled",
                f"that session belongs to no registered Project, so {tool_name} "
                "has no per-Project opt-in to read it under.",
                status=409,
                automation=automation_id,
            )
        enabled = await self.automation_gate(str(project.root))
        if automation_id not in enabled:
            label = automation_id.replace("_", " ")
            raise QueueError(
                "disabled",
                f"the '{label}' automation is not enabled for Project "
                f"'{project.name}'. Enable it in that Project's automation "
                "settings for this tool to read anything.",
                status=409,
                automation=automation_id,
            )
        return scope, session_id, run_id, session

    async def scan_timeline(self, caller: Any, args: dict[str, Any]) -> dict[str, Any]:
        """`mux.scanTimeline`: one session's distilled behavioral spine.

        A read, never a trigger. Scanning spends the human's gated budget under
        caps they set in Settings, so no scan or backfill is reachable here; an
        agent that could start scans could exhaust every Project's daily budget
        on the host.

        Every result carries the enablement/liveness block, because a scanner
        stopped by a cap and a session that is simply quiet both present as an
        empty tail and only one of them is worth acting on.
        """
        scope, session_id, run_id, _session = await self._scan_target(
            caller, args, "scan_timeline"
        )
        detail = str(args.get("detail") or "digest").strip() or "digest"
        if detail not in {"digest", "records", "full"}:
            raise ValueError("detail must be digest, records, or full")
        # An out-of-range filter value must be refused, not answered with an
        # empty page. The inputSchema declares these enums, but relying on the
        # client to enforce them would make a typo indistinguishable from "no
        # records are in that phase" - which is the same silent-empty failure
        # the whole surface exists to avoid.
        work_phase = self._enum_arg(args, "work_phase", WORK_PHASES)
        approach_status = self._enum_arg(args, "approach_status", APPROACH_STATUS)
        service = self.scan_timeline_service
        liveness = (
            await service.liveness(session_id, agent_run_id=run_id)
            if service is not None
            else {
                # Never an all-false block: a reader must not mistake "this
                # daemon cannot tell you" for "the scanner is off".
                "available": False,
                "note": (
                    "This daemon does not run the scan-timeline service, so "
                    "whether scanning is live cannot be reported here."
                ),
            }
        )
        result: dict[str, Any] = {
            "session_id": session_id,
            "agent_run_id": run_id or None,
            "detail": detail,
            "scan_state": liveness,
            **self._scope_envelope(scope),
        }
        if detail == "full":
            record_ids = _string_list(
                args.get("record_ids"), "record_ids", maximum=SCAN_FULL_MAX_RECORDS
            )
            if not record_ids:
                raise ValueError("detail:'full' requires record_ids")
            records = []
            for record_id in record_ids:
                record = await self.automation_store.scan_record(record_id)
                # Scoped to the session that was resolved and gated, so a record
                # id borrowed from another Project reads as absent rather than
                # as a way around the opt-in.
                if record is None or str(record.get("session_id") or "") != session_id:
                    continue
                records.append(self._redact_scan_record(record))
            result["records"] = records
            result["note"] = (
                "Whole stored records for the ids that belong to this session. "
                "Rehydrating the source messages is deliberately not available "
                "here: it reparses a transcript, and that cost does not belong "
                "behind a list read."
            )
            self._record_memory_outcome(
                "scan_timeline", returned=len(records), suppressed=0
            )
            return result

        if detail == "digest":
            if not run_id:
                result["digest"] = None
                result["note"] = (
                    "This session has no agent run to summarize, so there is no "
                    "spine to roll up."
                )
                self._record_memory_outcome("scan_timeline", returned=0, suppressed=0)
                return result
            records = await self.automation_store.scan_records(
                agent_run_id=run_id, limit=SCAN_DIGEST_SCAN_LIMIT
            )
            digest = catch_me_up(records, run_id)
            for key in ("claims", "progress"):
                digest[key] = [_redact(str(item)) for item in digest.get(key) or []]
            if digest.get("current_blocker"):
                blocker = dict(digest["current_blocker"])
                blocker["summary"] = _redact(str(blocker.get("summary") or ""))
                digest["current_blocker"] = blocker
            result["digest"] = digest
            result["note"] = (
                "The current run's phases, claims and blocker. Ask for "
                "detail:'records' when you need per-window rows, and note that "
                "the digest keeps the most recent phase segments - "
                "phase_segments_omitted says how many earlier ones were dropped."
            )
            self._record_memory_outcome(
                "scan_timeline", returned=len(records), suppressed=0
            )
            return result

        limit = max(
            1,
            min(
                int(args.get("limit") or SCAN_RECORDS_DEFAULT_LIMIT),
                SCAN_RECORDS_MAX_LIMIT,
            ),
        )
        newest_first = not bool(args.get("oldest_first", False))
        rows = await self.automation_store.scan_records(
            session_id=session_id,
            since_t1=_parse_time_bound(args.get("since_t1"), "since_t1"),
            until_t1=_parse_time_bound(args.get("until_t1"), "until_t1"),
            exclude_triggers=(
                sorted(HEARTBEAT_TRIGGERS) if args.get("exclude_heartbeat") else None
            ),
            work_phase=work_phase,
            approach_status=approach_status,
            blocked_only=bool(args.get("blocked_only", False)),
            target_fragment=str(args.get("target") or "") or None,
            newest_first=newest_first,
            limit=limit,
        )
        records = [self._redact_projection(project_record(row)) for row in rows]
        result["records"] = records
        # The cursor to feed back on the next poll. Taken from the page rather
        # than from the newest record in the store, so a filtered poll advances
        # only past what it actually returned.
        stamps = [float(item.get("t1") or 0.0) for item in records]
        result["next_since_t1"] = max(stamps) if stamps else args.get("since_t1")
        result["page_is_full"] = len(records) >= limit
        result["note"] = (
            "Compact per-window rows. evidence_refs, tier0_fact_ids, prompt "
            "hashes and the observer model are omitted; messages_seen and "
            "repaired_fields are kept because they are what lets you calibrate "
            "how much a label is worth. An absent approach_status means the "
            "record withheld a run-level verdict, not that it decided 'unknown'. "
            "Poll with since_t1=next_since_t1 to see only what is new."
        )
        self._record_memory_outcome(
            "scan_timeline", returned=len(records), suppressed=0
        )
        return result

    @staticmethod
    def _enum_arg(
        args: dict[str, Any], name: str, allowed: frozenset[str]
    ) -> str | None:
        """One optional enum-valued filter, refused rather than silently empty."""
        value = str(args.get(name) or "").strip()
        if not value:
            return None
        if value not in allowed:
            raise ValueError(f"{name} must be one of: {', '.join(sorted(allowed))}")
        return value

    @staticmethod
    def _redact_projection(projected: dict[str, Any]) -> dict[str, Any]:
        for key in ("intent", "summary", "dead_end"):
            if projected.get(key):
                projected[key] = _redact(str(projected[key]))
        projected["targets"] = [_redact(str(item)) for item in projected["targets"]]
        return projected

    @staticmethod
    def _redact_scan_record(record: dict[str, Any]) -> dict[str, Any]:
        expanded = {
            key: value for key, value in record.items() if key != "record_json"
        }
        for key in ("intent", "claim", "user_ask", "summary", "dead_end"):
            if expanded.get(key):
                expanded[key] = _redact(str(expanded[key]))
        if isinstance(expanded.get("target"), list):
            expanded["target"] = [_redact(str(item)) for item in expanded["target"]]
        return expanded

    async def scan_search(self, caller: Any, args: dict[str, Any]) -> dict[str, Any]:
        """`mux.scanSearch(query)`: find runs by what they were doing.

        An exposure of the query the human surface already runs, not a new
        capability: it resolves against distilled `summary`/`intent`/`target`
        records rather than raw transcript, so it finds a run by its work even
        when the words never appeared verbatim.
        """
        query = str(args.get("query") or "").strip()
        if not query:
            raise ValueError("query is required")
        if self.automation_store is None:
            raise QueueError(
                "unsupported",
                "scan_search needs the scan-timeline substrate, which this daemon "
                "does not run.",
                status=503,
            )
        scope, projects, disabled, scope_truncated = await self._memory_scope(
            caller, args, "scan_search"
        )
        limit = max(
            1,
            min(int(args.get("limit") or SCAN_SEARCH_DEFAULT_LIMIT), SCAN_SEARCH_MAX_LIMIT),
        )
        run_filter = str(args.get("agent_run_id") or "").strip()
        current_run, owned = await self._caller_run_ids(caller)
        hits: list[dict[str, Any]] = []
        #: Projects whose history is longer than one search reads. Named rather
        #: than counted so the answer says *where* it stopped short.
        truncated_projects: list[str] = []
        for project in projects:
            page = await self.automation_store.scan_search_page(
                project_id=str(project.id),
                agent_run_id=run_filter,
                limit=SCAN_SEARCH_SCAN_LIMIT,
            )
            if page.truncated:
                truncated_projects.append(str(project.name))
            for match in search_scan_records(page.records, query, limit=limit):
                match["snippet"] = _redact(str(match.get("snippet") or ""))
                match["targets"] = [
                    _redact(str(item)) for item in match.get("targets") or []
                ]
                match["run"] = self._run_attribution(
                    str(match.get("agent_run_id") or ""), current_run, owned
                )
                hits.append(match)
        hits.sort(key=lambda item: float(item.get("t1") or 0.0), reverse=True)
        hits = hits[:limit]
        self._record_memory_outcome("scan_search", returned=len(hits), suppressed=0)
        result: dict[str, Any] = {
            "query": query,
            "results": hits,
            "note": (
                "Matches over distilled scan records, not raw transcript; all "
                "query terms must appear. Take a hit's agent_run_id for "
                "scan_timeline, or its agent_run_id plus t0/t1 for "
                "search_history(run_ids, message_after, message_before) to reach "
                "the raw messages."
            ),
            **self._covered_projects(projects, scope_truncated),
            **self._disabled_note(disabled, "semantic_history_search"),
            **self._scope_envelope(scope),
        }
        if truncated_projects:
            # The search read the newest `SCAN_SEARCH_SCAN_LIMIT` records and
            # there are older ones. Said out loud, because an empty result over a
            # truncated read means "not in the recent history", not "never
            # happened", and an agent cannot tell those apart from hits alone.
            result["records_truncated"] = True
            result["records_truncated_note"] = (
                f"Searched the newest {SCAN_SEARCH_SCAN_LIMIT} scan records per "
                f"Project; {', '.join(sorted(truncated_projects))} has more "
                "history than that. Narrow with agent_run_id to reach older work."
            )
        return result

    # ------------------------------------------------ code graph reads (7.9)

    #: Static reverse-caller results are always a lower bound; the blind spots are
    #: named in every result so an agent never reads an empty set as "safe".
    _GRAPH_BLIND_SPOTS = (
        "Static analysis only — a lower bound. Callers reached through getattr, "
        "dict dispatch, decorators, dependency injection, or dynamic imports are "
        "not shown. The co-change net is the recall net for those."
    )

    def _require_code_graph(self, tool_name: str) -> None:
        if self.code_graph is None:
            raise QueueError(
                "unsupported",
                f"{tool_name} needs the code-structure graph, which this daemon "
                "does not run.",
                status=503,
            )

    @staticmethod
    def _is_test_path(path: str) -> bool:
        """Convention-based test classification, shared with the consumers (F26).

        Kept as a thin alias so both readers here - `blast_radius`'s covering
        tests and `test_gap`'s suppression - move together with the one
        definition in `deterministic_consumers`.
        """
        return is_test_path(path)

    async def _git_rows(self, project_id: str) -> tuple[list[dict[str, Any]], str]:
        """Provenance rows for the co-change net, and why they may be missing.

        Returns `(rows, unavailable_reason)`. An empty list with an empty reason
        is a project with no recorded commits; an empty list *with* a reason is a
        read that did not happen. Collapsing the two was the fail-silent half of
        this path: `blast_radius` reported "no co-changed files", which reads as
        evidence of a narrow change, when it had actually learned nothing.
        """
        reader = getattr(self.history, "git_provenance", None)
        if reader is None:
            return [], "provenance_reader_unavailable"
        try:
            rows = await reader(project_id=project_id, limit=500)
        except Exception as exc:  # noqa: BLE001 - fail soft, never fail silent
            log.warning(
                "blast_radius co-change read failed project_id=%s error=%s: %s",
                project_id,
                type(exc).__name__,
                str(exc)[:200],
            )
            return [], "provenance_read_failed"
        return list(rows or []), ""

    async def _owning_docs(self, project_root: str, identity: str) -> list[str]:
        """Docs that own one source file, from the shared ownership cache.

        `cached_doc_ownership`, not `build_doc_ownership`: this used to reparse
        the whole docs tree on every `blast_radius` call while the
        deterministic-consumer loop kept an identical map cached beside it (F22).
        """
        ownership = await asyncio.to_thread(
            cached_doc_ownership, Path(project_root) / ".docs"
        )
        docs: set[str] = set()
        for source_path, owners in (ownership or {}).items():
            if normalize_target(source_path, project_root) == identity:
                docs.update(str(d) for d in owners)
        return sorted(docs)

    async def blast_radius(self, caller: Any, args: dict[str, Any]) -> dict[str, Any]:
        """`mux.blastRadius(file)`: everything a change to a file can reach.

        Reverse callers (who imports/calls it, hop-ordered), the git co-change net
        (files repeatedly committed with it — the recall net for dynamic edges),
        covering tests among the reachable set, and the docs that own it. The
        static reverse set is a lower bound and says so; empty is never proof a
        change is safe.

        Every entry carries `co_change_available`. False means the git
        provenance read did not happen (`co_change_unavailable_reason` names
        which way), so `co_changed_files` being empty says nothing at all - the
        distinction the silent empty list used to erase.
        """
        self._require_code_graph("blast_radius")
        target_arg = str(args.get("file") or "").strip()
        if not target_arg:
            raise ValueError("file is required")
        hops = max(1, min(int(args.get("hops") or DEFAULT_BLAST_HOPS), MAX_BLAST_HOPS))
        scope, projects, disabled, _t = await self._memory_scope(caller, args, "blast_radius")
        results: list[dict[str, Any]] = []
        for project in projects:
            root = str(project.root)
            identity = normalize_target(target_arg, root)
            if identity is None:
                continue
            pid = str(project.id)
            reverse = await self.code_graph.reverse_dependents(pid, identity, hops=hops)
            callers = [
                {"path": node.path, "hop": node.hop, "via": node.via} for node in reverse
            ]
            git_rows, co_change_unavailable = await self._git_rows(pid)
            co_changed = co_change_net(git_rows, identity, project_root=root)
            covering_tests = sorted({c["path"] for c in callers if self._is_test_path(c["path"])})
            owning_docs = await self._owning_docs(root, identity)
            # A project whose co-change read failed is still reported: "nothing
            # co-changes with this file" and "the co-change net could not be
            # read" are opposite answers, and only one of them is safe to act on.
            if not (callers or co_changed or owning_docs or co_change_unavailable):
                continue
            entry: dict[str, Any] = {
                "file": identity,
                "project_id": pid,
                "callers": callers[:MEMORY_MAX_RESULTS],
                "co_changed_files": [
                    {"path": p, "shared_commits": n}
                    for p, n in co_changed[:MEMORY_MAX_RESULTS]
                ],
                "co_change_available": not co_change_unavailable,
                "covering_tests": covering_tests,
                "owning_docs": owning_docs,
                "has_no_covering_test": not covering_tests,
            }
            if co_change_unavailable:
                entry["co_change_unavailable_reason"] = co_change_unavailable
            results.append(entry)
        returned = sum(len(r["callers"]) + len(r["co_changed_files"]) for r in results)
        self._record_memory_outcome("blast_radius", returned=returned, suppressed=0)
        note = self._GRAPH_BLIND_SPOTS
        if any(not entry["co_change_available"] for entry in results):
            note += (
                " The git co-change net could not be read for at least one "
                "project (see co_change_unavailable_reason), so an empty "
                "co_changed_files there is unknown, not empty."
            )
        return {
            "blast_radius": results,
            "note": note,
            **self._disabled_note(disabled, "code_graph"),
            **self._scope_envelope(scope),
        }

    async def find_definition(self, caller: Any, args: dict[str, Any]) -> dict[str, Any]:
        """`mux.findDefinition(name)`: where a symbol is defined, by leaf or qualname."""
        self._require_code_graph("find_definition")
        name = str(args.get("name") or "").strip()
        if not name:
            raise ValueError("name is required")
        scope, projects, disabled, _t = await self._memory_scope(caller, args, "find_definition")
        results: list[dict[str, Any]] = []
        for project in projects:
            for row in await self.code_graph.definitions(str(project.id), name):
                results.append({**row, "project_id": str(project.id)})
        self._record_memory_outcome("find_definition", returned=len(results), suppressed=0)
        return {
            "definitions": results[:MEMORY_MAX_RESULTS],
            **self._disabled_note(disabled, "code_graph"),
            **self._scope_envelope(scope),
        }

    async def find_callers(self, caller: Any, args: dict[str, Any]) -> dict[str, Any]:
        """`mux.findCallers(file[, symbol])`: the (file, symbol) pairs that call
        a symbol, resolved import-aware. A lower bound — dynamic dispatch is not
        shown, so unresolved same-name callers are reported separately."""
        self._require_code_graph("find_callers")
        file_arg = str(args.get("file") or "").strip()
        if not file_arg:
            raise ValueError("file is required")
        symbol = str(args.get("symbol") or "").strip() or None
        scope, projects, disabled, _t = await self._memory_scope(caller, args, "find_callers")
        results: list[dict[str, Any]] = []
        unresolved: list[dict[str, Any]] = []
        for project in projects:
            root = str(project.root)
            identity = normalize_target(file_arg, root)
            if identity is None:
                continue
            pid = str(project.id)
            for row in await self.code_graph.callers_of_symbol(pid, identity, symbol):
                results.append({**row, "project_id": pid})
            if symbol:
                for row in await self.code_graph.unresolved_callers_by_name(pid, symbol):
                    unresolved.append({**row, "project_id": pid})
        self._record_memory_outcome("find_callers", returned=len(results), suppressed=0)
        return {
            "callers": results[:MEMORY_MAX_RESULTS],
            "unresolved_same_name_callers": unresolved[:MEMORY_MAX_RESULTS],
            "note": self._GRAPH_BLIND_SPOTS,
            **self._disabled_note(disabled, "code_graph"),
            **self._scope_envelope(scope),
        }

    async def find_references(self, caller: Any, args: dict[str, Any]) -> dict[str, Any]:
        """`mux.findReferences(file[, symbol])`: every call or reference to a
        symbol in a file — the precise structural neighborhood, not a grep."""
        self._require_code_graph("find_references")
        file_arg = str(args.get("file") or "").strip()
        if not file_arg:
            raise ValueError("file is required")
        symbol = str(args.get("symbol") or "").strip() or None
        scope, projects, disabled, _t = await self._memory_scope(caller, args, "find_references")
        results: list[dict[str, Any]] = []
        for project in projects:
            root = str(project.root)
            identity = normalize_target(file_arg, root)
            if identity is None:
                continue
            for row in await self.code_graph.references_to(str(project.id), identity, symbol):
                results.append({**row, "project_id": str(project.id)})
        self._record_memory_outcome("find_references", returned=len(results), suppressed=0)
        return {
            "references": results[:MEMORY_MAX_RESULTS],
            "note": self._GRAPH_BLIND_SPOTS,
            **self._disabled_note(disabled, "code_graph"),
            **self._scope_envelope(scope),
        }

    async def code_context(self, caller: Any, args: dict[str, Any]) -> dict[str, Any]:
        """`mux.codeContext(files)`: a compact structural neighborhood for context
        packing — each file's key symbols, what it imports, and its direct callers,
        instead of the agent reconstructing it by reading whole files."""
        self._require_code_graph("code_context")
        raw = args.get("files")
        files = [raw] if isinstance(raw, str) else list(raw or [])
        files = [str(f).strip() for f in files if str(f).strip()]
        if not files:
            raise ValueError("files is required")
        scope, projects, disabled, _t = await self._memory_scope(caller, args, "code_context")
        results: list[dict[str, Any]] = []
        for project in projects:
            root = str(project.root)
            pid = str(project.id)
            for file_arg in files[:MEMORY_MAX_RESULTS]:
                identity = normalize_target(file_arg, root)
                if identity is None:
                    continue
                symbols = await self.code_graph.symbols_in(pid, identity)
                if not symbols:
                    continue
                imports = await self.code_graph.imports_of(pid, identity)
                callers = await self.code_graph.callers_of_symbol(pid, identity)
                results.append(
                    {
                        "file": identity,
                        "project_id": pid,
                        "symbols": symbols[:MEMORY_MAX_RESULTS],
                        "imports": imports[:MEMORY_MAX_RESULTS],
                        "callers": [
                            {"src_path": c["src_path"], "src_symbol": c.get("src_symbol")}
                            for c in callers[:MEMORY_MAX_RESULTS]
                        ],
                    }
                )
        self._record_memory_outcome("code_context", returned=len(results), suppressed=0)
        return {
            "context": results,
            "note": self._GRAPH_BLIND_SPOTS,
            **self._disabled_note(disabled, "code_graph"),
            **self._scope_envelope(scope),
        }

    async def test_gap(self, caller: Any, args: dict[str, Any]) -> dict[str, Any]:
        """`mux.testGap()`: recently changed files whose blast radius contains no
        covering test — changed code a test never reaches. A lower bound: a test
        that exercises the code through dynamic dispatch is invisible here."""
        self._require_code_graph("test_gap")
        scope, projects, disabled, _t = await self._memory_scope(caller, args, "test_gap")
        since = time.time() - PROJECT_FACT_WINDOW_SECONDS
        results: list[dict[str, Any]] = []
        for project in projects:
            root = str(project.root)
            pid = str(project.id)
            facts = await self.tier0.facts_for_project(pid, since=since)
            changed: set[str] = set()
            for fact in facts:
                if fact.get("kind") not in PROVENANCE_WRITE_KINDS:
                    continue
                identity = normalize_target(fact.get("target"), root)
                if identity and not self._is_test_path(identity):
                    changed.add(identity)
            for identity in sorted(changed):
                reverse = await self.code_graph.reverse_dependents(pid, identity, hops=2)
                reachable = {node.path for node in reverse} | {identity}
                if any(self._is_test_path(p) for p in reachable):
                    continue
                results.append({"file": identity, "project_id": pid})
        self._record_memory_outcome("test_gap", returned=len(results), suppressed=0)
        return {
            "untested_changes": results[:MEMORY_MAX_RESULTS],
            "note": (
                "Changed files whose static blast radius contains no test file. "
                "A lower bound: a test reaching the code through dynamic dispatch "
                "is not visible, so a listed file is a candidate, not a proof."
            ),
            **self._disabled_note(disabled, "code_graph"),
            **self._scope_envelope(scope),
        }

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
        dry_run = bool(args.get("dry_run"))
        # A dry run stages nothing, so it is not a write. Counting it as one
        # would make "check before you send" look like extra authority spent.
        if not dry_run:
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
            delivery=str(args.get("delivery") or "when_idle"),
            envelope=str(args.get("envelope") or "") or None,
            dry_run=dry_run,
        )
        return dict(result)

    async def revoke_message(self, caller: Any, args: dict[str, Any]) -> dict[str, Any]:
        """`mux.revoke_message`: withdraw one undelivered message the caller sent.

        Attribution and the revocable states both live in the daemon operation.
        There is no target argument and no sender argument: the token names the
        session, and the message names its own target.
        """
        self.writes += 1
        result = await self._messaging().revoke(
            caller,
            str(args.get("message_id") or ""),
            str(args.get("reason") or ""),
        )
        return dict(result)

    async def request_spawn(self, caller: Any, args: dict[str, Any]) -> dict[str, Any]:
        """`mux.requestSpawn`: draft a session request, or (where the target
        Project granted it) create the session directly.

        Which one happens is the target Project's `spawn_grant`, resolved inside
        the session-control service - never here. Since 2026-08-25 the default
        is `granted` - agents spawn directly, inside a per-origin budget - and a
        Project lowers its grant to `draft` (or switches the `session_control`
        automation off) to get the inert draft a human approves.
        """
        self.writes += 1
        prompt = str(args.get("prompt") or "")
        backend = str(args.get("backend") or "")
        name = str(args.get("name") or "")
        reason = str(args.get("reason") or "")
        project = str(args.get("project") or "")
        if self.session_control is not None:
            result = await self.session_control.spawn(
                caller,
                prompt=prompt,
                backend=backend,
                name=name,
                reason=reason,
                correlation_id=str(args.get("correlation_id") or "") or None,
                project=project,
            )
        else:
            result = await self._messaging().request_spawn(
                caller,
                prompt=prompt,
                backend=backend,
                name=name,
                reason=reason,
                project=project,
            )
        return dict(result)

    def _control(self) -> Any:
        if self.session_control is None:
            raise RuntimeError(
                "transient: the session-control service is not available on this daemon"
            )
        return self.session_control

    async def interrupt(self, caller: Any, args: dict[str, Any]) -> dict[str, Any]:
        """`mux.interrupt`: a caller over the readiness-gated interrupt operation.

        Every bound (grant, scope, readiness, budget, cycle, idempotency, kill
        switch) lives in the daemon operation, not here. The actor is the token's
        session; there is no sender argument to forge.
        """
        self.writes += 1
        result = await self._control().interrupt(
            caller,
            target=str(args.get("target") or ""),
            reason=str(args.get("reason") or ""),
            correlation_id=str(args.get("correlation_id") or "") or None,
            project=str(args.get("project") or ""),
        )
        return dict(result)

    async def end_session(self, caller: Any, args: dict[str, Any]) -> dict[str, Any]:
        """`mux.end_session`: a caller over the graceful-end operation."""
        self.writes += 1
        result = await self._control().end_session(
            caller,
            target=str(args.get("target") or ""),
            reason=str(args.get("reason") or ""),
            correlation_id=str(args.get("correlation_id") or "") or None,
            project=str(args.get("project") or ""),
        )
        return dict(result)

    async def request_verify(self, caller: Any, args: dict[str, Any]) -> dict[str, Any]:
        """`mux.request_verify`: run the approved gate on the caller's branch, and stop.

        A separate tool rather than a flag on `request_land`, because the two ask
        for different acts with different blast radii: one ends by moving a
        repository's trunk and one cannot move anything. A flag would make the
        dangerous call the default spelling of the safe one, and would put both
        under the grant that exists for the trunk.

        It inherits `request_land`'s scoping unchanged, and for the same reason:
        no target argument, so the worktree comes from the caller's own live cwd.
        """
        return await self._enqueue_land(caller, args, kind="verify")

    async def request_land(self, caller: Any, args: dict[str, Any]) -> dict[str, Any]:
        """`mux.request_land`: enqueue a land of the caller's own worktree branch.

        Deliberately has no target argument. An agent lands the checkout it is
        working in and no other, so the worktree is read from the caller's own live
        cwd rather than accepted from the call - there is nothing here to forge.
        Every bound (install switch, Project opt-in, grant, budget, preconditions,
        the git vocabulary itself) lives in the daemon service; this is a caller.
        """
        return await self._enqueue_land(caller, args, kind="land")

    async def _enqueue_land(
        self, caller: Any, args: dict[str, Any], *, kind: str
    ) -> dict[str, Any]:
        """The caller both land-queue tools are, with the kind they asked for.

        Shared so the by-construction scoping is written once: a second copy of "read
        the worktree off the caller's own record" is a second chance to accept one from
        the arguments instead.
        """
        self.writes += 1
        if self.land_queue is None:
            raise QueueError(
                "unavailable",
                "the land queue is not available on this daemon.",
                status=503,
            )
        record = caller.record
        worktree_root = str(getattr(record, "git_cwd", "") or "")
        if not worktree_root:
            raise QueueError(
                "no_worktree",
                "this session has no working directory to land.",
                status=409,
            )
        project = None
        if self.projects is not None:
            project = self.projects.projects.get(str(record.project_id or ""))
        if project is None:
            raise QueueError(
                "no_project",
                "this session is not owned by a registered Project.",
                status=409,
            )
        try:
            result = await self.land_queue.request(
                project_id=str(project.id),
                project_root=str(project.root),
                worktree_root=worktree_root,
                kind=kind,
                origin="agent",
                origin_session_id=str(record.id),
                origin_run_id=str(getattr(record, "agent_run_id", "") or ""),
                reason=str(args.get("reason") or ""),
            )
        except LandRefusal as refusal:
            # The service's refusals are the ordinary answers here — the branch is
            # already on the trunk, another request holds it, a precondition failed
            # — and every one of them is something the agent can act on. Both HTTP
            # routes already translate them to a typed 409; without this the same
            # refusal reached the agent as `500 internal server error`, which says
            # nothing and reads as a daemon bug rather than as an answer.
            raise QueueError(refusal.code, refusal.message, status=409) from refusal
        return dict(result)

    async def watch_session(self, caller: Any, args: dict[str, Any]) -> dict[str, Any]:
        """`mux.watch_session`: arm a one-shot settle watch on another session.

        A read that matures into one bounded message. The target is only read;
        the notice is a fixed daemon template addressed to the caller's own
        prompt queue, staged armed because the watch is the consent for it
        (`land-queue.md`, `agent-messaging.md`). Every bound (install switch,
        scope, per-watcher ceiling, timeout ceiling, the settle rule, and the
        arming rule) lives in the daemon service, and this is a caller (CP §7.1).
        """
        if self.session_watch is None:
            raise QueueError(
                "unavailable",
                "session watches are not available on this daemon.",
                status=503,
            )
        result = await self.session_watch.watch(
            caller,
            target=str(args.get("target") or ""),
            timeout_minutes=args.get("timeout_minutes"),
            project=str(args.get("project") or ""),
        )
        return dict(result)

    # ------------------------------------------------------- configurator

    def _configurator_service(self) -> Any:
        if self.configurator is None:
            raise QueueError(
                "unavailable",
                "the configurator surface is not available on this daemon.",
                status=503,
            )
        return self.configurator

    @staticmethod
    def _caller_session(caller: Any) -> dict[str, Any]:
        """Where the caller is standing, for every configurator answer.

        The one fact a configurator launched into somebody else's Project cannot
        derive and repeatedly needs: with two dozen Projects registered, every
        per-Project override it meets belongs to one of the others until proven
        otherwise, and "it is the only one I can see" is not that proof.
        """
        record = getattr(caller, "record", None)
        return {
            "session_id": str(getattr(record, "id", "") or ""),
            "project_id": str(getattr(record, "project_id", "") or ""),
            "project_name": str(getattr(record, "project_label", "") or ""),
            "cwd": str(getattr(record, "run_cwd", "") or getattr(record, "cwd", "") or ""),
        }

    async def configurator_capabilities(
        self, caller: Any, args: dict[str, Any]
    ) -> dict[str, Any]:
        """`mux.configurator_capabilities`: this install's generated inventory."""
        from .configurator import DEFAULT_MANIFEST_SECTIONS

        raw = args.get("sections")
        if raw is not None and (
            not isinstance(raw, list) or not all(isinstance(item, str) for item in raw)
        ):
            raise ValueError("sections must be an array of section names")
        return dict(
            await self._configurator_service().capabilities(
                session=self._caller_session(caller),
                sections=tuple(raw) if raw else DEFAULT_MANIFEST_SECTIONS,
                settings_query=str(args.get("settings_query") or ""),
            )
        )

    async def configurator_device_settings(
        self, caller: Any, args: dict[str, Any]
    ) -> dict[str, Any]:
        """`mux.configurator_device_settings`: the per-device UI store, read."""
        return dict(
            await self._configurator_service().device_settings(
                profile=str(args.get("profile") or ""),
                domain=str(args.get("domain") or ""),
                session_project_id=self._caller_session(caller)["project_id"],
            )
        )

    async def configurator_edit_device_settings(
        self, caller: Any, args: dict[str, Any]
    ) -> dict[str, Any]:
        """`mux.configurator_edit_device_settings`: path-scoped UI settings write.

        The shape checks live here and every bound lives in the store and in
        `settings_patch`: this refuses only what it can refuse without knowing a
        schema, which is the same division the install-wide write already keeps
        with `update_config`.
        """
        domain = str(args.get("domain") or "").strip()
        if not domain:
            raise ValueError("domain is required")
        operations = args.get("operations")
        if not isinstance(operations, list) or not operations:
            raise ValueError("operations must be a non-empty array")
        self.writes += 1
        return dict(
            await self._configurator_service().edit_device_settings(
                profile=str(args.get("profile") or ""),
                domain=domain,
                operations=operations,
                expect_digest=str(args.get("expect_digest") or ""),
            )
        )

    async def configurator_project_settings(
        self, caller: Any, args: dict[str, Any]
    ) -> dict[str, Any]:
        """`mux.configurator_project_settings`: one Project's committed config."""
        requested = str(args.get("project") or "").strip()
        return dict(
            await self._configurator_service().project_settings(
                requested or self._caller_session(caller)["project_id"]
            )
        )

    async def configurator_apply_project_settings(
        self, caller: Any, args: dict[str, Any]
    ) -> dict[str, Any]:
        """`mux.configurator_apply_project_settings`: a Project's committed config, written."""
        changes = args.get("changes")
        if not isinstance(changes, dict):
            raise ValueError("changes must be an object of field name to new value")
        if not changes:
            raise ValueError("changes must name at least one field")
        requested = str(args.get("project") or "").strip()
        self.writes += 1
        return dict(
            await self._configurator_service().apply_project_settings(
                project=requested or self._caller_session(caller)["project_id"],
                changes=dict(changes),
            )
        )

    async def configurator_guide(self, caller: Any, args: dict[str, Any]) -> dict[str, Any]:
        """`mux.configurator_guide`: the index, or one shipped guide's text.

        Served straight from the module rather than through the service: the
        guides are files in this build with no runtime state behind them, so a
        daemon wired without a configurator service can still answer them.
        """
        from .configurator import guide_index, read_guide

        requested = str(args.get("id") or "").strip()
        if not requested:
            return {"guides": guide_index()}
        try:
            text = await asyncio.to_thread(read_guide, requested)
        except KeyError as exc:
            # `errors.NotFound.__str__` is the message that names the catalog;
            # `args[0]` is only the id the caller already sent.
            raise ValueError(str(exc)) from exc
        return {"id": requested, "text": text}

    async def configurator_diagnostics(
        self, caller: Any, args: dict[str, Any]
    ) -> dict[str, Any]:
        """`mux.configurator_diagnostics`: the daemon's own health report."""
        return dict(await self._configurator_service().diagnostics())

    async def configurator_apply_settings(
        self, caller: Any, args: dict[str, Any]
    ) -> dict[str, Any]:
        """`mux.configurator_apply_settings`: a validated install-wide settings write.

        Every bound lives in `update_config`, which is what the Settings panel
        calls too, so this tool grants no authority the panel does not already
        have and cannot skip a check by arriving through MCP. The only rules
        enforced here are the shape of the argument and the refusal to accept an
        empty batch - a write that changes nothing should say so rather than
        report a successful save.
        """
        service = self._configurator_service()
        changes = args.get("changes")
        if not isinstance(changes, dict):
            raise ValueError("changes must be an object of setting name to new value")
        if not changes:
            raise ValueError("changes must name at least one setting")
        self.writes += 1
        return dict(await service.apply_settings(dict(changes)))

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
            "provenance": self.provenance,
            "verified_status": self.verified_status,
            "prior_resolutions": self.prior_resolutions,
            "dead_ends": self.dead_ends,
            "doc_debt": self.doc_debt,
            "scan_timeline": self.scan_timeline,
            "scan_search": self.scan_search,
            "blast_radius": self.blast_radius,
            "find_definition": self.find_definition,
            "find_callers": self.find_callers,
            "find_references": self.find_references,
            "code_context": self.code_context,
            "test_gap": self.test_gap,
            "watch_session": self.watch_session,
            "notify": self.notify,
            "revoke_message": self.revoke_message,
            "request_spawn": self.request_spawn,
            "run_action": self.run_action,
            "interrupt": self.interrupt,
            "end_session": self.end_session,
            "request_land": self.request_land,
            "request_verify": self.request_verify,
        }
        if name in _CONFIGURATOR_TOOL_NAMES:
            # The same gate `tools_for` applies to listing. Phrased as "unknown
            # tool" rather than "not permitted" because to every session but a
            # configurator that is the literal truth: the tool was never
            # advertised, and naming a capability that exists elsewhere would
            # only invite an agent to look for a way to reach it.
            if not bool(getattr(getattr(caller, "record", None), "configurator", False)):
                raise ValueError(f"unknown tool: {name}")
            handlers.update(
                {
                    "configurator_capabilities": self.configurator_capabilities,
                    "configurator_guide": self.configurator_guide,
                    "configurator_diagnostics": self.configurator_diagnostics,
                    "configurator_apply_settings": self.configurator_apply_settings,
                    "configurator_device_settings": self.configurator_device_settings,
                    "configurator_edit_device_settings": self.configurator_edit_device_settings,
                    "configurator_project_settings": self.configurator_project_settings,
                    "configurator_apply_project_settings": (
                        self.configurator_apply_project_settings
                    ),
                }
            )
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
                    "serverInfo": {"name": "mux", "version": "0.1.2"},
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
                            "readiness and, by default, for a human to approve it; "
                            "pass `dry_run` to see whether anything would deliver it "
                            "before you stage it, and `revoke_message` to withdraw one "
                            "that has not been delivered), "
                            "and `request_spawn` drafts a new-session request in the "
                            "Fleet Queue for a human to approve. It starts nothing."
                            + (
                                " This session was launched as the swe-mux "
                                "configurator, so it also holds the "
                                "`configurator_*` tools: a generated inventory of "
                                "this install's settings, harnesses, and "
                                "automations, the shipped configuration guides, "
                                "the health report, and one validated write that "
                                "changes install-wide settings."
                                if bool(
                                    getattr(
                                        getattr(caller, "record", None), "configurator", False
                                    )
                                )
                                else ""
                            )
                    ),
                }
            )
        if method == "ping":
            return ok({})
        if method == "tools/list":
            return ok({"tools": tools_for(caller)})
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
            except ScopeMiss as exc:
                # `ScopeMiss`, not bare `KeyError` (F24). Scope miss and true
                # miss answer identically: not found, with text naming the
                # argument that widens the search, because a default an agent
                # cannot discover reads as a prohibition. An *accidental*
                # KeyError - a handler indexing a dict that has no such key -
                # is a defect, and it now falls through to the internal-error
                # path below instead of impersonating this answer.
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
            except AmbiguousIdentity as exc:
                # A `ValueError` already, and answered as one; named here so the
                # typed pair stays visible beside the miss it is not.
                return error(-32602, str(exc))
            except (ValueError, TypeError) as exc:
                return error(-32602, str(exc))
            except RuntimeError as exc:
                return ok({"content": [{"type": "text", "text": str(exc)}], "isError": True})
            except Exception:  # noqa: BLE001 - a handler defect is not a tool result
                # Everything typed has been answered above, so reaching here
                # means the tool broke. Report it as an internal error with the
                # traceback in the log rather than as a plausible-looking
                # "not found" the agent would act on.
                log.exception("MCP tool raised an unhandled error tool=%s", name)
                return error(-32603, f"internal error in tool {name}")
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
            # Transcript-parse health. `in_flight` above zero at rest, or
            # `refusals` climbing, means one transcript is parsing pathologically
            # slowly - which used to present only as agents being told to retry.
            "transcript_parses": {
                "in_flight": len(self._transcript_flights),
                "timeouts": self.parse_timeouts,
                "refusals": self.parse_refusals,
            },
            "tools": {name: dict(values) for name, values in sorted(self.tool_stats.items())},
            # Retrieval-outcome measurement (ROADMAP 7.5): a memory tool whose
            # `empty` count dominates its `calls` is returning nothing useful and
            # is a defect to fix rather than a feature to leave running.
            "memory_outcomes": {
                name: dict(values)
                for name, values in sorted(self.memory_outcomes.items())
            },
        }
