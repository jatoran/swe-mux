"""What an agent's own process posts back: MCP calls and harness hooks."""

from __future__ import annotations

import json
import logging
import secrets
import time
from collections import deque
from pathlib import Path
from typing import Any

from aiohttp import web

from .. import (
    app_keys as keys,
)
from ..harness import (
    HARNESSES,
    descriptor,
)
from ..http_support import json_response
from ..mcp import McpAuthError, McpService
from ..observation import (
    apply_hook_observation,
    conversation_rollover_decision,
    foreign_conversation_hook_id,
    session_hook_event_scope,
)
from ..telemetry_otlp import PARSER_VERSION, otlp_reduction

log = logging.getLogger(__name__)


HOOK_RATE_WINDOW_SECONDS = 10.0


HOOK_RATE_LIMIT = 500


# Sweep rate-limit windows for sessions that no longer exist once the map grows
# past a size no live fleet reaches.
HOOK_WINDOW_SWEEP_AT = 256


# MCP tool-call budget per session. Generous for a deliberate agent, tight
# enough that a retry loop cannot pull the whole archive through the endpoint.
MCP_RATE_WINDOW_SECONDS = 60.0


MCP_RATE_LIMIT = 120


MCP_BODY_BYTES = 256 * 1024
OTLP_BODY_BYTES = 4 * 1024 * 1024


def hook_event_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Remove hook-owned envelope keys that collide with EventBus metadata.

    ``source`` is the collision that mattered: the CLI uses it for *why the
    session started* (`startup` / `resume` / `clear` / `compact`) while the
    EventBus uses it for *which channel observed this*. Dropping it lost the one
    field that explains a conversation rollover, so it is preserved under
    ``start_source`` instead.
    """
    kept = {
        key: value
        for key, value in payload.items()
        if key not in {"session_id", "source", "event_type", "scope"}
    }
    start_source = payload.get("source")
    if isinstance(start_source, str) and start_source:
        kept["start_source"] = start_source
    return kept


_HOOK_EVENT_TYPES = {event for harness in HARNESSES.values() for event in harness.hook_events} | {
    # Compatibility events emitted by older Codex notify integrations and test
    # probes. Native hook catalogs live on the descriptors above.
    "turn_started",
    "turn_ended",
    "agent-turn-complete",
    "approval_needed",
    "approval-requested",
    "task_started",
    "task_complete",
    "rate_limit",
    "rate_limited",
}


_NORMALIZED_HOOK_EVENT_TYPES = {
    "turn_started",
    "turn_ended",
    "approval_needed",
    "task_started",
    "task_complete",
    "approval_resolved",
    "context_compacted",
    "rate_limit",
    "rate_limited",
}


# Hooks whose event necessarily wrote records into the *root* transcript: a prompt
# was submitted, a tool ran, a turn stopped. Only these date the "the CLI ran a turn
# and none of it landed in the file we are following" evidence that marks observation
# stale (`_note_transcript_staleness`).
#
# Deliberately excludes `Notification` — its most common form, `idle_prompt`, fires
# ~60 s *after* a turn ends to report that the agent is waiting, so it is guaranteed
# to arrive with no accompanying transcript activity. Including it marked every
# healthy idle agent in the fleet as stale (8 false positives across 4 sessions on
# the first live pass, and zero true ones). Also excludes `SessionStart`/`SessionEnd`
# (lifecycle, not turn content) and the subagent hooks, whose records go to sidechain
# files rather than the root transcript.
#
# `agent-turn-complete` is Codex's raw turn-end notify and MUST be here. Lifecycle
# hooks can be disabled, untrusted, or unavailable, so a `/new` behind a sibling
# that cannot be ruled out can still be invisible. This set is tested against the
# *raw* event type, so omitting the compatibility notify made
# `_note_transcript_staleness` unreachable for the one backend it was written for.
# Verified live: a Codex pane rolled by `/new` kept reporting the abandoned
# conversation as live, with its retired token counts, for 200s while
# `last_turn_hook_ts` stayed unset.
_TRANSCRIPT_BACKED_HOOK_EVENTS = {
    "UserPromptSubmit",
    "PreToolUse",
    "PostToolUse",
    "PostToolUseFailure",
    "Stop",
    "agent-turn-complete",
    "turn_started",
    "turn_ended",
    "task_started",
    "task_complete",
}


async def mcp_endpoint(request: web.Request) -> web.Response:
    """The mux MCP v0 endpoint: JSON-RPC over streamable HTTP (`mcp.py`).

    Same-host trust boundary per the 2026-07-28 decision (`ROADMAP.md` Phase
    4.5): loopback-only like hook ingress, bearer token as caller *identity*
    and Project read scope. Read-only end to end — the service exposes no tool
    that can enqueue, deliver, spawn, or write to a PTY.
    """
    if request.content_length is not None and request.content_length > MCP_BODY_BYTES:
        raise web.HTTPRequestEntityTooLarge(
            max_size=MCP_BODY_BYTES, actual_size=request.content_length
        )
    peer = request.transport.get_extra_info("peername") if request.transport else None
    host = peer[0] if peer else ""
    if host not in {"127.0.0.1", "::1"}:
        raise web.HTTPForbidden(text="the mux MCP endpoint is loopback-only")
    service: McpService = request.app[keys.MCP]
    try:
        caller = service.resolve_caller(request.headers.get("Authorization"))
    except McpAuthError as exc:
        return json_response({"error": str(exc)}, 401)
    now = time.monotonic()
    windows: dict[str, deque[float]] = request.app[keys.MCP_RATE_WINDOWS]
    if len(windows) > HOOK_WINDOW_SWEEP_AT:
        live = request.app[keys.SESSIONS].sessions
        for stale in [sid for sid in windows if sid not in live]:
            windows.pop(stale, None)
    window = windows.setdefault(caller.record.id, deque())
    while window and now - window[0] >= MCP_RATE_WINDOW_SECONDS:
        window.popleft()
    if len(window) >= MCP_RATE_LIMIT:
        raise web.HTTPTooManyRequests(
            text="MCP call rate limit exceeded", headers={"Retry-After": "5"}
        )
    window.append(now)
    try:
        message = json.loads(await request.read())
    except ValueError:
        return json_response(
            {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}},
            400,
        )
    if not isinstance(message, dict):
        # JSON-RPC batching was removed in protocol 2025-06-18; no client we
        # target sends it.
        return json_response(
            {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32600, "message": "expected a single JSON-RPC object"},
            },
            400,
        )
    response = await service.handle_rpc(caller, message)
    if response is None:
        return web.Response(status=202)
    return json_response(response)


async def hook_ingress(request: web.Request) -> web.Response:
    if request.content_length is not None and request.content_length > 256 * 1024:
        raise web.HTTPRequestEntityTooLarge(max_size=256 * 1024, actual_size=request.content_length)
    peer = request.transport.get_extra_info("peername") if request.transport else None
    host = peer[0] if peer else ""
    if host not in {"127.0.0.1", "::1"}:
        raise web.HTTPForbidden(text="hook ingress is loopback-only")
    sid = request.match_info["sid"]
    session = request.app[keys.SESSIONS].resolve(sid)
    if session.record.state in {"exited", "crashed"}:
        raise web.HTTPGone(text="hook session has ended")
    supplied = request.headers.get("X-Mux-Hook-Secret", "")
    if not secrets.compare_digest(supplied, session.hook_secret):
        raise web.HTTPForbidden(text="invalid hook secret")
    boundary = getattr(session.record, "runtime_boundary", "local")
    if boundary != "local":
        return json_response(
            {
                "error": "hook ingress is unavailable across a non-local terminal boundary",
                "code": "agent_bridge_unavailable",
                "capability": "agent-bridge-unavailable",
                "reason": (
                    "remote_terminal_boundary"
                    if boundary == "remote"
                    else "terminal_boundary_unknown"
                ),
                "boundary": boundary,
            },
            409,
        )
    now = time.monotonic()
    windows: dict[str, deque[float]] = request.app[keys.HOOK_INGRESS_WINDOWS]
    if len(windows) > HOOK_WINDOW_SWEEP_AT:
        # One entry plus up to HOOK_RATE_LIMIT timestamps per session that ever
        # received a hook, retained for the daemon's (weeks-long) lifetime.
        live = request.app[keys.SESSIONS].sessions
        for stale in [sid for sid in windows if sid not in live]:
            windows.pop(stale, None)
    window = windows.setdefault(session.record.id, deque())
    while window and now - window[0] >= HOOK_RATE_WINDOW_SECONDS:
        window.popleft()
    if len(window) >= HOOK_RATE_LIMIT:
        raise web.HTTPTooManyRequests(
            text="hook event burst limit exceeded", headers={"Retry-After": "1"}
        )
    window.append(now)
    raw = await request.read()
    if len(raw) > 256 * 1024:
        raise web.HTTPRequestEntityTooLarge(max_size=256 * 1024, actual_size=len(raw))
    body = json.loads(raw)
    if not isinstance(body, dict):
        raise ValueError("hook body must be a JSON object")
    event_type = str(body.get("event") or body.get("type") or "hook")
    if event_type not in _HOOK_EVENT_TYPES:
        raise ValueError("unsupported hook event type")
    payload = body.get("payload") or {}
    if not isinstance(payload, dict):
        raise ValueError("hook payload must be a JSON object")
    hook_source = str(body.get("source") or "")[:64]
    sequence_value = body.get("sequence")
    sequence: int | None = None
    sequence_state = session.observation_state.setdefault("hook_sequences", {})
    if not isinstance(sequence_state, dict):
        sequence_state = {}
        session.observation_state["hook_sequences"] = sequence_state
    if sequence_value is not None:
        if isinstance(sequence_value, bool) or not isinstance(sequence_value, int):
            raise ValueError("hook sequence must be a positive integer")
        if sequence_value < 1 or not hook_source:
            raise ValueError("sequenced hooks require a source and positive sequence")
        sequence = sequence_value
        previous_sequence = sequence_state.get(hook_source)
        if isinstance(previous_sequence, int) and sequence <= previous_sequence:
            duplicate_count = session.observation_state.get("hook_sequence_duplicates", 0)
            session.observation_state["hook_sequence_duplicates"] = (
                duplicate_count + 1 if isinstance(duplicate_count, int) else 1
            )
            session.state_transitions.append(
                {
                    "ts": time.time(),
                    "kind": "hook_sequence_duplicate",
                    "source": hook_source,
                    "previous": previous_sequence,
                    "sequence": sequence,
                }
            )
            return json_response({"ok": True, "ignored": "duplicate_or_stale_sequence"})
        if isinstance(previous_sequence, int) and sequence > previous_sequence + 1:
            session.state_transitions.append(
                {
                    "ts": time.time(),
                    "kind": "hook_sequence_gap",
                    "source": hook_source,
                    "previous": previous_sequence,
                    "sequence": sequence,
                }
            )
    elif descriptor(session.record.backend).hook_ordering_guarantee:
        raise ValueError("this harness requires sequenced hook events")
    event_payload = hook_event_payload(payload)
    # Session-aware: a harness that runs subagents as separate threads emits
    # events that only *name* a child thread, and the payload alone cannot tell
    # one of those from the root's own.
    scope = session_hook_event_scope(session, event_type, payload)
    # An in-CLI `/clear` replaces the conversation under a live PTY: the CLI
    # reports its new id here, and that ends the current agent run. Rolled before
    # the observation below so the SessionStart transition lands on the new run.
    # Claude blocks on this POST, so a failure must not break the user's `/clear`
    # — but it must not degrade to the old silent-swap behaviour either. Failing
    # closed marks observation stale, which is exactly true: we know the
    # conversation moved and we did not manage to follow it.
    decision = conversation_rollover_decision(session, event_type, payload)
    if decision.roll_to is not None:
        reported_transcript = (
            str(payload.get("transcript_path") or "")
            if descriptor(session.record.backend).reports_transcript_path
            else ""
        )
        try:
            await request.app[keys.SESSIONS].roll_agent_conversation(
                session.record.id,
                native_id=decision.roll_to,
                reason="conversation_rolled",
                source=str(payload.get("source") or "hook"),
                # The CLI names the file it is now writing; deriving it from the
                # session cwd instead re-guesses what the hook already proves.
                transcript=Path(reported_transcript) if reported_transcript else None,
            )
        except Exception:
            log.exception(
                "conversation rollover failed for session %s; marking observation stale",
                session.record.id,
            )
            session.record.observation_stale_since = time.time()
            session.observation_stale_reason = "rollover_adoption_failed"
            session.record.observation_diagnostic = (
                "the CLI reported a new conversation that could not be adopted"
            )
            session.state_transitions.append(
                {
                    "ts": time.time(),
                    "kind": "observation_liveness_lost",
                    "reason": "rollover_unadoptable",
                }
            )
            session.publish_update()
    elif decision.refused is not None:
        session.state_transitions.append(
            {
                "ts": time.time(),
                "kind": "conversation_rollover_refused",
                "native_session_id": decision.refused,
                "reason": decision.refusal_reason,
            }
        )
        await request.app[keys.EVENTS].emit(
            "conversation_rollover_refused",
            session_id=session.record.id,
            source="hook",
            backend=session.record.backend,
            native_session_id=decision.refused,
            reason=decision.refusal_reason,
        )
        # A refused SessionStart belongs to another process generation. It is
        # diagnostic evidence only and must not continue into binding, liveness,
        # automation, or status observation. Continuing used to let a Codex
        # startup emitted around compaction force the active root session idle.
        return json_response({"ok": True, "ignored": "foreign_conversation"})
    # The session's own spawn conversation speaking while the record is bound
    # elsewhere is proof the identity was stolen (a nested child rolled it away);
    # heal before the foreign check below would discard the evidence.
    await request.app[keys.SESSIONS].maybe_heal_from_own_conversation_hook(session, payload)
    if foreign_conversation_hook_id(session, payload) is None:
        # Only this session's own conversation counts as evidence: a nested
        # child's hooks must not refresh liveness, date staleness, or reach the
        # event bus as this session's activity.
        session.last_hook_ts = time.time()
        if event_type in _TRANSCRIPT_BACKED_HOOK_EVENTS and scope != "subagent":
            # Root scope only. `last_turn_hook_ts` means "the CLI ran a turn
            # whose records must have landed in the transcript we follow", and a
            # *subagent's* tool call is not that: a background subagent writes
            # nothing into the root transcript. Counting its PreToolUse/PostToolUse
            # stream here made a session waiting on background agents look like a
            # conversation that had been replaced — a quiet root transcript plus
            # a "turn" hook is exactly `_note_transcript_staleness`'s trigger —
            # so `observation_stale_since` false-fired, revoking transcript
            # authority and painting the status line's staleness warning on a
            # perfectly healthy session (measured live 2026-08-02, 666s).
            session.last_turn_hook_ts = session.last_hook_ts
        # Where the CLI says it is standing, and which file it says it is writing.
        # Both are only meaningful for a hook that speaks for this session's own
        # conversation, which is why they live inside this branch: a nested child
        # inherits the hook wiring, and letting its readings through would move a
        # session's cwd and its observation onto a conversation it does not own.
        # Only staged here — the ingress must return fast, because Claude blocks the
        # user's turn on this POST.
        request.app[keys.SESSIONS].note_hook_cwd(session, payload)
        request.app[keys.SESSIONS].note_hook_transcript_path(session, payload)
        request.app[keys.AUTOMATION].note_native_hook(session.record.id)
        if event_type not in _NORMALIZED_HOOK_EVENT_TYPES:
            await request.app[keys.EVENTS].emit(
                event_type,
                session_id=session.record.id,
                source="hook",
                scope=scope,
                **event_payload,
            )
    hook_decision = await apply_hook_observation(
        session, event_type, payload, request.app[keys.EVENTS]
    )
    if sequence is not None:
        sequence_state = session.observation_state.setdefault("hook_sequences", {})
        if isinstance(sequence_state, dict):
            sequence_state[hook_source] = sequence
    if hook_decision is not None:
        # Relayed verbatim to the shim, which prints it for the CLI to read. The
        # harness-specific shape is composed here (where the registry lives)
        # rather than in the shim, which runs as a fresh interpreter for every
        # hook and imports nothing from the package.
        return json_response({"ok": True, "hookSpecificOutput": hook_decision})
    return json_response({"ok": True})


async def telemetry_otlp_logs(request: web.Request) -> web.Response:
    """Session-authenticated local OTLP/JSON ingress with immediate content reduction."""

    if request.content_length is not None and request.content_length > OTLP_BODY_BYTES:
        raise web.HTTPRequestEntityTooLarge(
            max_size=OTLP_BODY_BYTES, actual_size=request.content_length
        )
    peer = request.transport.get_extra_info("peername") if request.transport else None
    host = peer[0] if peer else ""
    if host not in {"127.0.0.1", "::1"}:
        raise web.HTTPForbidden(text="telemetry ingress is loopback-only")
    session = request.app[keys.SESSIONS].resolve(request.match_info["sid"])
    supplied = request.headers.get("X-Mux-Hook-Secret", "")
    if not secrets.compare_digest(supplied, session.hook_secret):
        raise web.HTTPForbidden(text="invalid telemetry secret")
    try:
        payload = await request.json()
        reduction = otlp_reduction(
            payload,
            session_id=session.record.id,
            backend=session.record.backend,
        )
    except (ValueError, json.JSONDecodeError) as exc:
        raise web.HTTPBadRequest(text=str(exc)) from None
    service = request.app[keys.CANONICAL_TELEMETRY]
    # The raw body is dropped here. What survives is the reduced events and a
    # count of which provider event names arrived, so a renamed attribute shows
    # up as drift in the quality view rather than as a quietly emptier ledger.
    service.note_parser_signatures(
        backend=str(session.record.backend),
        harness_version=reduction.harness_version,
        parser_version=PARSER_VERSION,
        signatures=reduction.signatures,
    )
    accepted = sum(service.enqueue_provider_event(event) for event in reduction.events)
    rejected = len(reduction.events) - accepted
    return json_response(
        {
            "partialSuccess": {
                "rejectedLogRecords": rejected,
                "errorMessage": "ingress queue full" if rejected else "",
            }
        }
    )


ROUTES: tuple[web.RouteDef, ...] = (
    web.post("/api/hooks/{sid}", hook_ingress),
    web.post("/api/telemetry/otlp/{sid}/v1/logs", telemetry_otlp_logs),
    web.post("/mcp", mcp_endpoint),
)
