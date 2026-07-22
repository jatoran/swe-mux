from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from .adapters.codex import codex_data_home
from .event_bus import EventBus
from .models import SessionState
from .session import Session

log = logging.getLogger(__name__)

OBSERVATION_SCHEMA_VERSION = "2"
PARSER_DEGRADE_MIN_EVENTS = 20
PARSER_DEGRADE_UNKNOWN_RATIO = 0.25

CLAUDE_KNOWN_RECORDS = {
    "ai-title",
    "assistant",
    "attachment",
    "file-history-delta",
    "file-history-snapshot",
    "last-prompt",
    "mode",
    "permission-mode",
    "queue-operation",
    "system",
    "user",
}

CODEX_KNOWN_OUTER_RECORDS = {
    "compacted",
    "inter_agent_communication_metadata",
    "session_meta",
    "turn_context",
    "world_state",
}

CODEX_KNOWN_PAYLOADS = {
    "agent_message",
    "apply_patch_approval_request",
    "context_compacted",
    "custom_tool_call",
    "custom_tool_call_output",
    "error",
    "exec_approval_request",
    "exec_command_begin",
    "exec_command_end",
    "exec_command_output_delta",
    "function_call",
    "function_call_output",
    "mcp_tool_call_end",
    "message",
    "patch_apply_end",
    "rate_limit",
    "rate_limited",
    "reasoning",
    "request_user_input",
    "sub_agent_activity",
    "task_complete",
    "task_started",
    "thread_goal_updated",
    "thread_rolled_back",
    "thread_settings_applied",
    "token_count",
    "turn_aborted",
    "user_message",
    "web_search_end",
}

# Local slash commands (/copy, /model, /resume, ...) are logged as user records
# but never reach the model, so they must not begin or sustain a root turn.
CLAUDE_LOCAL_COMMAND_PREFIXES = ("<command-", "<local-command-")
# Esc/interrupt writes a user record instead of any Stop hook or completion
# record; it is the only terminal evidence an aborted turn ever produces.
CLAUDE_INTERRUPT_PREFIX = "[Request interrupted by user"

# Records appended more than this long before observation attach are historical
# context (resume/promotion catch-up), not live activity.
HISTORICAL_TIMESTAMP_SLACK_SECONDS = 2.0
# An open turn found during catch-up counts as still running only when its last
# record is this recent; anything older is a stale artifact of an ended run.
CATCHUP_OPEN_TURN_WINDOW_SECONDS = 60.0
# A hook-initiated turn whose own submission turns out to be a local command is
# closed only when nothing else has happened in it and it began moments ago.
EMPTY_HOOK_TURN_WINDOW_SECONDS = 3.0

CLAUDE_CONTEXT_WINDOWS = {
    "claude-opus-4-8": 1_000_000,
    "claude-opus-4-7": 1_000_000,
    "claude-opus-4-6": 1_000_000,
    "claude-sonnet-5": 1_000_000,
    "claude-sonnet-4-6": 1_000_000,
    "claude-haiku-4-5": 200_000,
    "claude-haiku-4-5-20251001": 200_000,
}


def _publish_update(session: Session) -> None:
    if getattr(session, "observation_replay", False):
        return
    publish = getattr(session, "publish_update", None)
    if callable(publish):
        publish()


class _NullEventBus:
    """Swallows semantic events while historical transcript records are replayed."""

    async def emit(self, event_type: str, **payload: Any) -> None:
        del event_type, payload
        return None


_NULL_EVENTS: Any = _NullEventBus()


def _claude_user_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                return str(block.get("text") or "")
    return ""


def _is_local_command_text(text: str) -> bool:
    return text.lstrip().startswith(CLAUDE_LOCAL_COMMAND_PREFIXES)


def _is_interrupt_text(text: str) -> bool:
    return text.lstrip().startswith(CLAUDE_INTERRUPT_PREFIX)


def _event_timestamp(event: dict[str, Any]) -> float | None:
    raw = event.get("timestamp") or event.get("ts")
    if isinstance(raw, (int, float)):
        return float(raw)
    if isinstance(raw, str):
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    return None


_TOOL_TARGET_FIELDS = (
    "file_path",
    "path",
    "notebook_path",
    "filename",
    "command",
    "cmd",
    "pattern",
    "url",
)
_TOOL_CONTENT_FIELDS = ("new_string", "content", "contents", "new_source", "text", "patch")


def tool_call_evidence(tool_input: Any) -> tuple[str | None, str | None]:
    """Extract a normalized target and a parse-time content hash from a tool call.

    Runs at the adapter boundary while the native input is still in hand, so the
    hash is of the exact bytes the agent wrote — race-free, unlike reading the
    file back off disk after the event has queued. Native shapes never leave here.
    """
    if isinstance(tool_input, str):
        try:
            tool_input = json.loads(tool_input)
        except (json.JSONDecodeError, ValueError):
            return None, None
    if not isinstance(tool_input, dict):
        return None, None
    target: str | None = None
    for key in _TOOL_TARGET_FIELDS:
        value = tool_input.get(key)
        if isinstance(value, str) and value.strip():
            target = value.strip()[:512]
            break
    parts: list[str] = []
    for key in _TOOL_CONTENT_FIELDS:
        value = tool_input.get(key)
        if isinstance(value, str) and value:
            parts.append(value)
    edits = tool_input.get("edits")
    if isinstance(edits, list):
        for edit in edits:
            if isinstance(edit, dict) and isinstance(edit.get("new_string"), str):
                parts.append(edit["new_string"])
    content_hash = (
        hashlib.sha256("\x00".join(parts).encode("utf-8")).hexdigest() if parts else None
    )
    return target, content_hash


def _tool_names(session: Session) -> dict[str, str]:
    names = getattr(session, "tool_names", None)
    if names is None:
        names = {}
        session.tool_names = names
    return names


class JsonlTailer:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.offset = 0
        self.decoder = IncrementalJsonlDecoder()
        self.prefix: bytes | None = None
        # Content already present at attach is history (resume, promotion after
        # activity), not live agent behavior; events() labels it historical.
        try:
            self.initial_size = path.stat().st_size
        except OSError:
            self.initial_size = 0
        self._caught_up = self.initial_size == 0

    async def events(self, stop: asyncio.Event):  # type: ignore[no-untyped-def]
        """Yield records plus explicit replay-boundary markers.

        ``(None, True)`` starts a new historical snapshot after the provider
        truncates or replaces the file (Claude cancel/revert does this), and
        ``(None, False)`` ends catch-up.  The end marker is also emitted after all
        bytes that existed at initial attach have been decoded.
        """
        while not stop.is_set() and not self.path.exists():
            try:
                await asyncio.wait_for(stop.wait(), timeout=0.2)
            except TimeoutError:
                pass
        while not stop.is_set():
            try:
                size = self.path.stat().st_size
                reset = size < self.offset
                if not reset and self.offset and self.prefix is not None:
                    with self.path.open("rb") as handle:
                        current_prefix = handle.read(min(64, size))
                    compared = min(len(self.prefix), len(current_prefix))
                    reset = current_prefix[:compared] != self.prefix[:compared]
                if reset:
                    self.offset = 0
                    self.decoder.reset()
                    self.prefix = None
                    # Replacement content is a fresh historical snapshot.  Never
                    # compare its byte positions with the original attach size;
                    # doing so suppresses live records until the rewritten file
                    # grows past its former length.
                    self.initial_size = size
                    self._caught_up = False
                    yield None, True
                if size > self.offset:
                    with self.path.open("rb") as handle:
                        if self.prefix is None or len(self.prefix) < 64:
                            self.prefix = handle.read(min(64, size))
                        handle.seek(self.offset)
                        chunk = handle.read(size - self.offset)
                    self.offset = size
                    for position, item in self.decoder.feed_with_positions(chunk):
                        yield item, position <= self.initial_size
                if not self._caught_up and self.offset >= self.initial_size:
                    self._caught_up = True
                    yield None, False
            except FileNotFoundError:
                pass
            await asyncio.sleep(0.25)


class IncrementalJsonlDecoder:
    """Decode append-only JSONL chunks while retaining only an incomplete final line."""

    def __init__(self) -> None:
        self.partial = b""
        self.position = 0

    def reset(self) -> None:
        self.partial = b""
        self.position = 0

    def feed_with_positions(self, chunk: bytes) -> list[tuple[int, dict[str, Any]]]:
        """Return (byte offset after the record's line, record) for each complete line."""
        lines = (self.partial + chunk).split(b"\n")
        consumed = self.position
        self.partial = lines.pop()
        result: list[tuple[int, dict[str, Any]]] = []
        for line in lines:
            consumed += len(line) + 1
            if not line.strip():
                continue
            try:
                item = json.loads(line.decode("utf-8", "replace"))
            except json.JSONDecodeError:
                log.debug("skipping invalid transcript JSONL record")
                continue
            if isinstance(item, dict):
                result.append((consumed, item))
        self.position = consumed
        return result

    def feed(self, chunk: bytes) -> list[dict[str, Any]]:
        return [item for _position, item in self.feed_with_positions(chunk)]


async def observe_transcript(
    session: Session, path: Path, events: EventBus, stop: asyncio.Event
) -> None:
    session.record.parser_status = "watching"
    session.record.parser_schema_version = OBSERVATION_SCHEMA_VERSION
    session.record.parser_diagnostic = f"tailing {path.name}"
    _publish_update(session)
    tailer = JsonlTailer(path)
    attach_ts = time.time()
    replay_pending = tailer.initial_size > 0
    if replay_pending:
        session.observation_replay = True
    historical_seen = 0
    last_historical_ts: float | None = None
    try:
        async for event, byte_historical in tailer.events(stop):
            if event is None:
                if byte_historical:
                    replay_pending = True
                    session.observation_replay = True
                    historical_seen = 0
                    last_historical_ts = None
                    continue
                if replay_pending:
                    replay_pending = False
                    await _finish_transcript_catchup(
                        session, events, attach_ts, last_historical_ts, historical_seen
                    )
                continue
            ts = _event_timestamp(event)
            historical = byte_historical or (
                ts is not None and ts < attach_ts - HISTORICAL_TIMESTAMP_SLACK_SECONDS
            )
            if historical:
                # Harvest telemetry (tokens/context/model/tool correlation) from
                # history without emitting events or driving state; the replay
                # flag suppresses transitions and update fanout even when an
                # old-stamped record straggles in after catch-up finished.
                historical_seen += 1
                if ts is not None:
                    last_historical_ts = ts
                session.observation_replay = True
                try:
                    if session.record.backend == "claude":
                        await _claude(session, event, _NULL_EVENTS)
                    elif session.record.backend == "codex":
                        await _codex(session, event, _NULL_EVENTS)
                finally:
                    session.observation_replay = replay_pending
                continue
            if replay_pending:
                # A live record can share the first read with the historical tail;
                # resolve catch-up before it may drive state.
                replay_pending = False
                await _finish_transcript_catchup(
                    session, events, attach_ts, last_historical_ts, historical_seen
                )
            recognized, signature = classify_transcript_event(session.record.backend, event)
            if session.record.backend == "claude":
                await _claude(session, event, events)
            elif session.record.backend == "codex":
                await _codex(session, event, events)
            await _record_parser_observation(session, events, recognized, signature)
    finally:
        session.observation_replay = False


async def _finish_transcript_catchup(
    session: Session,
    events: EventBus,
    attach_ts: float,
    last_historical_ts: float | None,
    historical_seen: int,
) -> None:
    """Resolve the live state once pre-existing transcript content is consumed.

    A turn left open by history counts as running only when its newest record is
    recent (promotion moments after the user submitted); an old open turn is a
    stale artifact of an ended run and the session is simply ready for input.
    """
    session.observation_replay = False
    state = _observation_state(session)
    open_turn = bool(state.get("root_turn_active"))
    recent = (
        last_historical_ts is not None
        and attach_ts - last_historical_ts < CATCHUP_OPEN_TURN_WINDOW_SECONDS
    )
    state["root_turn_active"] = False
    state["root_completion_seen"] = False
    if not historical_seen:
        # An empty replacement is still authoritative cancel/revert evidence.
        if session.record.state in {"working", "awaiting"}:
            await _transition(session, events, "idle", source="transcript", force=True)
        return
    if hasattr(session, "state_source_priority"):
        # A complete provider snapshot is a new observation boundary.  It may
        # reconcile a turn whose higher-priority completion hook never fired.
        session.state_source_priority = -1
    if open_turn and recent:
        await _begin_root_turn(session, events, source="transcript")
    else:
        await _transition(session, events, "idle", source="transcript")
    _publish_update(session)


def classify_transcript_event(backend: str, event: dict[str, Any]) -> tuple[bool, str]:
    outer = str(event.get("type") or "<missing>")
    if backend == "claude":
        return outer in CLAUDE_KNOWN_RECORDS, f"claude:{outer}"
    if backend == "codex":
        payload = event.get("payload")
        payload_type = str(payload.get("type") or "") if isinstance(payload, dict) else ""
        signature = f"codex:{outer}:{payload_type or '<none>'}"
        return (
            outer in CODEX_KNOWN_OUTER_RECORDS or payload_type in CODEX_KNOWN_PAYLOADS,
            signature,
        )
    return False, f"{backend}:{outer}"


# Trailing records the provider appends after a turn (naming, mode markers,
# prompt echoes) are not turn activity and must be skipped when judging the tail.
_CLAUDE_TAIL_IGNORED = {
    "ai-title",
    "attachment",
    "file-history-delta",
    "file-history-snapshot",
    "last-prompt",
    "mode",
    "permission-mode",
    "queue-operation",
}
# Tail bytes read to judge turn state without loading a multi-megabyte transcript.
TRANSCRIPT_TAIL_BYTES = 131_072


def transcript_tail_turn_state(backend: str, path: Path) -> str:
    """Classify the transcript tail as ``ended``, ``open`` or ``unknown``.

    Used by the quiescence watchdog to decide whether a stuck "working" session
    has actually finished. ``open`` means a tool or turn is still in flight, so a
    long-running tool call is never mistaken for a hang. Only ``ended`` is proof
    the turn is over; ``unknown`` means the tail carries no decisive signal.
    """
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            if size > TRANSCRIPT_TAIL_BYTES:
                handle.seek(size - TRANSCRIPT_TAIL_BYTES)
                handle.readline()  # discard the partial line at the seek point
            raw = handle.read()
    except OSError:
        return "unknown"
    records: list[dict[str, Any]] = []
    for line in raw.split(b"\n"):
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line.decode("utf-8", "replace"))
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            records.append(item)
    if backend == "claude":
        return _claude_tail_state(records)
    if backend == "codex":
        return _codex_tail_state(records)
    return "unknown"


def _claude_tail_state(records: list[dict[str, Any]]) -> str:
    for event in reversed(records):
        event_type = event.get("type")
        if event_type == "system":
            subtype = str(event.get("subtype") or "")
            if subtype in {"turn_duration", "stop_hook_summary"}:
                return "ended"
            continue
        if event_type == "assistant":
            message = event.get("message") or {}
            content = message.get("content")
            blocks = content if isinstance(content, list) else []
            has_tool_use = any(
                isinstance(block, dict) and block.get("type") == "tool_use" for block in blocks
            )
            has_text = (
                isinstance(content, str)
                and bool(content)
                or any(isinstance(block, dict) and block.get("type") == "text" for block in blocks)
            )
            if message.get("stop_reason") == "end_turn":
                return "ended"
            if has_tool_use:
                return "open"
            if message.get("stop_reason") == "tool_use":
                return "open"
            return "ended" if has_text else "unknown"
        if event_type == "user":
            content = event.get("message", {}).get("content") if event.get("message") else None
            text = _claude_user_text(content)
            if event.get("isMeta") is True or _is_local_command_text(text):
                continue
            if _is_interrupt_text(text):
                return "ended"
            # A plain prompt or a tool result both mean the model still owes a
            # response — the turn is open.
            return "open"
        # Metadata / bookkeeping records carry no turn signal; keep scanning.
        if event_type in _CLAUDE_TAIL_IGNORED:
            continue
    return "unknown"


def _codex_tail_state(records: list[dict[str, Any]]) -> str:
    for event in reversed(records):
        payload = event.get("payload") or {}
        payload_type = payload.get("type") if isinstance(payload, dict) else None
        if payload_type in {"task_complete", "turn_aborted", "thread_rolled_back"}:
            return "ended"
        if payload_type in {
            "task_started",
            "user_message",
            "function_call",
            "custom_tool_call",
            "exec_command_begin",
            "function_call_output",
            "custom_tool_call_output",
            "exec_command_end",
            "patch_apply_end",
            "mcp_tool_call_end",
            "web_search_end",
            "exec_approval_request",
            "apply_patch_approval_request",
            "request_user_input",
        }:
            return "open"
    return "unknown"


async def _record_parser_observation(
    session: Session,
    events: EventBus,
    recognized: bool,
    signature: str,
) -> None:
    record = session.record
    previous_status = record.parser_status
    if recognized:
        record.parser_events_seen += 1
    else:
        record.parser_unknown_events += 1
        signatures = record.parser_unknown_signatures
        signatures[signature] = signatures.get(signature, 0) + 1
        if len(signatures) > 20:
            least = min(signatures, key=lambda item: (signatures[item], item))
            signatures.pop(least, None)
    total = record.parser_events_seen + record.parser_unknown_events
    unknown_ratio = record.parser_unknown_events / total if total else 0.0
    if total >= PARSER_DEGRADE_MIN_EVENTS and unknown_ratio >= PARSER_DEGRADE_UNKNOWN_RATIO:
        record.parser_status = "degraded"
        record.parser_diagnostic = (
            f"schema v{OBSERVATION_SCHEMA_VERSION}: {record.parser_unknown_events}/{total} "
            f"unrecognized transcript records ({unknown_ratio:.0%})"
        )
    elif record.parser_events_seen:
        record.parser_status = "ready"
        record.parser_diagnostic = (
            f"schema v{OBSERVATION_SCHEMA_VERSION}: {record.parser_events_seen}/{total} "
            f"recognized ({unknown_ratio:.0%} unknown)"
        )
    if record.parser_status != previous_status or not recognized:
        _publish_update(session)
    if record.parser_status == "degraded" and previous_status != "degraded":
        await events.emit(
            "capability_degraded",
            session_id=record.id,
            source="transcript",
            capability="semantic_transcript",
            minimum="semantic",
            reason=record.parser_diagnostic,
            schema_version=OBSERVATION_SCHEMA_VERSION,
            unknown_ratio=unknown_ratio,
            unknown_signatures=dict(record.parser_unknown_signatures),
        )


def _observation_state(session: Session) -> dict[str, Any]:
    state = getattr(session, "observation_state", None)
    if state is None:
        state = {
            "root_turn_active": False,
            "root_completion_seen": False,
            "codex_scope": "root",
            "closed_by_transcript": False,
        }
        session.observation_state = state
    return state


def _transcript_authoritative(session: Session) -> bool:
    """True once the ordered transcript observer has proven itself healthy.

    Hooks are an unordered, retried side channel — each event is a separate POST
    with its own backoff, so a late ``PreToolUse``/``PostToolUse`` can land after
    the transcript's end-of-turn and strand a finished session as "working".
    Once the parser is ``ready`` the transcript is the authoritative, in-order
    record of turn boundaries and tool activity, so hooks must not drive that
    state; while it is still warming up (``watching``, no recognized records yet)
    or has ``degraded``, hooks remain the fallback that keeps state moving.
    """
    return getattr(session.record, "parser_status", "") == "ready"


async def _begin_root_turn(session: Session, events: EventBus, *, source: str) -> None:
    state = _observation_state(session)
    if source == "transcript":
        # Ordered, in-band evidence of new work supersedes the prior close.
        state["closed_by_transcript"] = False
    elif state.get("closed_by_transcript") and _transcript_authoritative(session):
        # The transcript already closed this turn; a later, unordered hook must
        # not reopen "working" on a session that is really waiting for input.
        # Only fresh transcript activity (above) starts the next turn.
        return
    if (
        not state["root_turn_active"]
        and session.record.state not in {"working", "awaiting"}
        and hasattr(session, "state_source_priority")
    ):
        # Priority arbitrates conflicting evidence within one turn, not forever.
        # A new root turn lets the best currently available tier take ownership
        # when a hook or transcript source has gone missing.
        session.state_source_priority = -1
    await _transition(session, events, "working", source=source)
    if state["root_turn_active"]:
        return
    state["root_turn_active"] = True
    state["root_completion_seen"] = False
    state["turn_started_at"] = time.time()
    state["turn_saw_activity"] = False
    await events.emit("turn_started", session_id=session.record.id, source=source, scope="root")


async def _finish_root_turn(
    session: Session,
    events: EventBus,
    *,
    source: str,
    outcome: str = "completed",
    force: bool = False,
    **payload: Any,
) -> None:
    state = _observation_state(session)
    if source == "transcript":
        # Latch the transcript's authoritative turn boundary so a late, unordered
        # hook cannot reopen "working" afterward (set before the early-return so a
        # trailing turn_duration after a hook Stop still arms the latch).
        state["closed_by_transcript"] = True
    if not state["root_turn_active"] and state.get("root_completion_seen"):
        return
    # A provider-native completion belongs to the active root turn and is a
    # stronger boundary than the source that observed its start.  This lets the
    # transcript close a hook-started turn when Stop was missed.
    force = force or (
        source == "transcript"
        and (bool(state["root_turn_active"]) or session.record.state in {"working", "awaiting"})
    )
    state["root_turn_active"] = False
    state["root_completion_seen"] = True
    if outcome == "completed":
        await _transition(session, events, "idle", source=source, force=force)
        await events.emit(
            "turn_ended",
            session_id=session.record.id,
            source=source,
            scope="root",
            outcome=outcome,
            **payload,
        )
    else:
        await _transition(session, events, "idle", outcome, source=source, force=force)
        await events.emit(
            "turn_aborted",
            session_id=session.record.id,
            source=source,
            scope="root",
            outcome=outcome,
            **payload,
        )


async def _complete_empty_hook_turn(session: Session, events: EventBus) -> None:
    """Close a just-started turn whose submission turned out to be a local command.

    UserPromptSubmit can fire before the daemon can know the prompt is a local
    command. Such a turn never reaches the model, so no Stop/completion evidence
    will ever arrive; without this it stays "working" forever. A genuinely active
    turn is protected by the recency and no-activity guards.
    """
    state = _observation_state(session)
    if not state.get("root_turn_active") or state.get("turn_saw_activity"):
        return
    started = float(state.get("turn_started_at") or 0.0)
    if time.time() - started > EMPTY_HOOK_TURN_WINDOW_SECONDS:
        return
    await _finish_root_turn(session, events, source="transcript", force=True)


def hook_event_scope(event_type: str, payload: dict[str, Any]) -> str:
    if event_type in {"SubagentStart", "SubagentStop"}:
        return "subagent"
    if payload.get("isSidechain") is True or payload.get("is_sidechain") is True:
        return "subagent"
    if payload.get("agent_id") and event_type not in {"SessionStart", "SessionEnd"}:
        return "subagent"
    return "root"


async def apply_hook_observation(
    session: Session,
    event_type: str,
    payload: dict[str, Any],
    events: EventBus,
) -> None:
    scope = hook_event_scope(event_type, payload)
    if scope == "subagent":
        await events.emit(
            "subagent_activity",
            session_id=session.record.id,
            source="hook",
            scope="subagent",
            kind=event_type,
        )
        return

    # Tool-activity and turn-start hooks only drive state as a fallback: when the
    # transcript observer is authoritative it already records the same boundaries
    # in order, and a late/reordered hook would only race it (reopening a finished
    # turn as "working"). Turn-end, approval, and notification hooks below stay
    # live because they carry signals the transcript lacks or delivers later.
    if event_type == "SessionStart":
        await _transition(session, events, "idle", source="hook")
    elif event_type in {"UserPromptSubmit", "turn_started", "task_started"}:
        if _transcript_authoritative(session):
            return
        await _begin_root_turn(session, events, source="hook")
    elif event_type == "PreToolUse":
        if _transcript_authoritative(session):
            return
        await _begin_root_turn(session, events, source="hook")
        _observation_state(session)["turn_saw_activity"] = True
        tool = str(payload.get("tool_name") or payload.get("name") or "tool")
        await _transition(session, events, "working", tool, source="hook")
        await events.emit(
            "tool_use",
            session_id=session.record.id,
            source="hook",
            scope="root",
            tool=tool,
        )
    elif event_type in {"PostToolUse", "PostToolUseFailure"}:
        if _transcript_authoritative(session):
            return
        await _transition(session, events, "working", source="hook")
    elif event_type in {"PermissionRequest", "approval_needed", "approval-requested"}:
        tool = str(payload.get("tool_name") or payload.get("message") or "approval")
        await _transition(session, events, "awaiting", tool, source="hook")
        await events.emit(
            "approval_needed",
            session_id=session.record.id,
            source="hook",
            scope="root",
            kind="approval",
            detail=tool,
        )
    elif event_type == "Notification":
        notification = str(payload.get("notification_type") or "")
        if notification == "idle_prompt":
            # "Claude is waiting for your input" fires once the turn is over and the
            # agent is back at the prompt. That is "ready", not a blocking approval:
            # bucketing it as awaiting shows a phantom approval on a finished agent.
            # Treat it as a turn boundary (also recovers a missed Stop), but never
            # clobber a genuine approval the user has not yet acted on.
            if session.record.state != "awaiting":
                await _finish_root_turn(session, events, source="hook")
        elif notification in {"permission_prompt", "elicitation_dialog"}:
            kind = "approval" if notification == "permission_prompt" else "input"
            detail = str(payload.get("message") or notification)
            await _transition(session, events, "awaiting", detail, source="hook")
            await events.emit(
                "approval_needed",
                session_id=session.record.id,
                source="hook",
                scope="root",
                kind=kind,
                detail=detail,
            )
        elif notification in {"rate_limit", "rate_limited"}:
            await _transition(session, events, "awaiting", "rate_limit", source="hook")
            await events.emit(
                "rate_limited",
                session_id=session.record.id,
                source="hook",
                scope="root",
            )
    elif event_type in {"Stop", "turn_ended", "agent-turn-complete", "task_complete"}:
        await _finish_root_turn(session, events, source="hook")
    elif event_type == "SessionEnd":
        await _finish_root_turn(
            session,
            events,
            source="hook",
            outcome=str(payload.get("reason") or "session_ended"),
            force=True,
        )


async def _transition(
    session: Session,
    events: EventBus,
    state: SessionState,
    detail: str | None = None,
    *,
    source: str = "transcript",
    force: bool = False,
) -> bool:
    if getattr(session, "observation_replay", False):
        return False
    if force and hasattr(session, "state_source_priority"):
        # Interrupt/abort evidence exists only in the transcript; hooks never
        # deliver it, so it may reclaim authority from an earlier hook state.
        session.state_source_priority = -1
    previous = session.record.state
    transition = getattr(session, "transition", None)
    if callable(transition):
        accepted = transition(state, detail, source=source)
        if not accepted:
            return False
    else:
        session.record.state = state
        session.record.state_detail = detail
        _publish_update(session)
    if previous != state:
        await events.emit(
            "state_changed",
            session_id=session.record.id,
            source=source,
            previous=previous,
            state=state,
            detail=detail,
        )
    return True


async def _claude(session: Session, event: dict[str, Any], events: EventBus) -> None:
    event_type = event.get("type")
    message = event.get("message") or {}
    if event.get("isSidechain") is True:
        block_types = [
            str(block.get("type") or "unknown")
            for block in message.get("content") or []
            if isinstance(block, dict)
        ]
        await events.emit(
            "subagent_activity",
            session_id=session.record.id,
            source="transcript",
            scope="subagent",
            kind=str(event_type or "activity"),
            block_types=sorted(set(block_types)),
        )
        return
    if event_type == "user":
        content = message.get("content")
        text = _claude_user_text(content)
        if event.get("isMeta") is True or _is_local_command_text(text):
            await _complete_empty_hook_turn(session, events)
            return
        if _is_interrupt_text(text):
            await _finish_root_turn(
                session, events, source="transcript", outcome="interrupted", force=True
            )
            return
        has_tool_result = isinstance(content, list) and any(
            block.get("type") == "tool_result" for block in content if isinstance(block, dict)
        )
        if isinstance(content, str) or (
            isinstance(content, list)
            and any(
                block.get("type") in {"text", "image"}
                for block in content
                if isinstance(block, dict)
            )
            and not has_tool_result
        ):
            await _begin_root_turn(session, events, source="transcript")
        elif has_tool_result:
            session.record.state_detail = None
            await _begin_root_turn(session, events, source="transcript")
            _observation_state(session)["turn_saw_activity"] = True
            for block in content if isinstance(content, list) else []:
                if not isinstance(block, dict) or block.get("type") != "tool_result":
                    continue
                tool_use_id = str(block.get("tool_use_id") or "")
                tool = _tool_names(session).pop(tool_use_id, "tool")
                result_content = block.get("content")
                if isinstance(result_content, list):
                    detail = " ".join(
                        str(item.get("text") or "")
                        for item in result_content
                        if isinstance(item, dict) and item.get("type") == "text"
                    )
                else:
                    detail = str(result_content or "")
                await events.emit(
                    "tool_result",
                    session_id=session.record.id,
                    source="transcript",
                    scope="root",
                    tool=tool,
                    call_id=tool_use_id or None,
                    success=not bool(block.get("is_error")),
                    exit_code=None,
                    detail=detail[:4000],
                )
                if tool in {"Agent", "Task"}:
                    await events.emit(
                        "subagent_activity",
                        session_id=session.record.id,
                        source="transcript",
                        scope="subagent",
                        kind="completed",
                    )
    elif event_type == "assistant":
        await _begin_root_turn(session, events, source="transcript")
        _observation_state(session)["turn_saw_activity"] = True
        has_text = False
        content = message.get("content") or []
        if isinstance(content, str):
            has_text = bool(content)
            content = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                has_text = True
            elif isinstance(block, dict) and block.get("type") == "tool_use":
                name = str(block.get("name") or "tool")
                tool_use_id = str(block.get("id") or "")
                if tool_use_id:
                    _tool_names(session)[tool_use_id] = name
                target, content_hash = tool_call_evidence(block.get("input"))
                await _transition(session, events, "working", name)
                await events.emit(
                    "tool_use",
                    session_id=session.record.id,
                    source="transcript",
                    scope="root",
                    tool=name,
                    call_id=tool_use_id or None,
                    target=target,
                    content_hash=content_hash,
                    parser_version=OBSERVATION_SCHEMA_VERSION,
                )
                if name.lower() == "skill" and isinstance(block.get("input"), dict):
                    skill = block["input"].get("skill") or block["input"].get("name")
                    if isinstance(skill, str) and skill.strip():
                        await events.emit(
                            "skill_invoked",
                            session_id=session.record.id,
                            source="transcript",
                            scope="root",
                            backend="claude",
                            tool=name,
                            call_id=tool_use_id or None,
                            skill=skill.strip()[:200],
                            parser_version=OBSERVATION_SCHEMA_VERSION,
                        )
                if name in {"Agent", "Task"}:
                    await events.emit(
                        "subagent_activity",
                        session_id=session.record.id,
                        source="transcript",
                        scope="subagent",
                        kind="started",
                    )
        usage = message.get("usage") or {}
        if usage:
            session.record.tokens_in = sum(
                int(usage.get(key, 0))
                for key in (
                    "input_tokens",
                    "cache_read_input_tokens",
                    "cache_creation_input_tokens",
                )
            )
            session.record.tokens_out = int(usage.get("output_tokens", 0))
            model = str(message.get("model") or "")
            window = CLAUDE_CONTEXT_WINDOWS.get(model, 0)
            session.record.context_window = window
            session.record.context_pct = min(1, session.record.tokens_in / window) if window else 0
            session.record.context_peak_pct = max(
                session.record.context_peak_pct, session.record.context_pct
            )
            session.record.model = model or session.record.model
            session.record.measurement_source = "claude-transcript"
            _publish_update(session)
        # Interactive Claude normally appends a turn_duration system record, but
        # print/non-interactive mode can finish at the final assistant message.
        # A text response with end_turn is authoritative; tool-use messages are
        # deliberately left working until their result or a later completion.
        if message.get("stop_reason") == "end_turn" and has_text:
            await _finish_root_turn(session, events, source="transcript")
    elif event_type == "system":
        subtype = str(event.get("subtype") or "")
        if subtype == "turn_duration":
            await _finish_root_turn(
                session,
                events,
                source="transcript",
                duration_ms=event.get("durationMs"),
            )
        elif subtype in {"compact_boundary", "context_compacted", "compaction"}:
            await events.emit(
                "context_compacted",
                session_id=session.record.id,
                source="transcript",
                scope="root",
                backend="claude",
                capability="explicit_native",
                confidence="high",
                parser_version=OBSERVATION_SCHEMA_VERSION,
            )


async def _codex(session: Session, event: dict[str, Any], events: EventBus) -> None:
    payload = event.get("payload") or {}
    outer_type, payload_type = event.get("type"), payload.get("type")
    state = _observation_state(session)
    if outer_type == "session_meta":
        if payload.get("parent_thread_id"):
            state["codex_scope"] = "subagent"
            await events.emit(
                "subagent_activity",
                session_id=session.record.id,
                source="transcript",
                scope="subagent",
                kind="transcript_attached",
            )
            return
        state["codex_scope"] = "root"
        native_id = payload.get("id") or payload.get("session_id")
        if native_id:
            session.record.native_session_id = str(native_id)
        session.record.model = str(payload.get("model") or "") or session.record.model
        await _transition(session, events, "idle")
    if state.get("codex_scope") == "subagent":
        await events.emit(
            "subagent_activity",
            session_id=session.record.id,
            source="transcript",
            scope="subagent",
            kind=str(payload_type or outer_type or "activity"),
        )
        return
    if payload_type in {"task_started", "user_message"}:
        await _begin_root_turn(session, events, source="transcript")
    elif payload_type == "task_complete":
        await _finish_root_turn(
            session,
            events,
            source="transcript",
            turn_id=payload.get("turn_id"),
            duration_ms=payload.get("duration_ms"),
        )
    elif payload_type == "turn_aborted":
        await _finish_root_turn(
            session,
            events,
            source="transcript",
            outcome=str(payload.get("reason") or "aborted"),
            turn_id=payload.get("turn_id"),
            duration_ms=payload.get("duration_ms"),
        )
    elif payload_type == "thread_rolled_back":
        await _finish_root_turn(
            session,
            events,
            source="transcript",
            outcome="rolled_back",
            force=True,
        )
    elif payload_type in {"function_call", "custom_tool_call"}:
        name = str(payload.get("name") or "tool")
        call_id = str(payload.get("call_id") or payload.get("id") or "")
        if call_id:
            _tool_names(session)[call_id] = name
        target, content_hash = tool_call_evidence(
            payload.get("arguments") or payload.get("input")
        )
        await _begin_root_turn(session, events, source="transcript")
        await _transition(session, events, "working", name)
        await events.emit(
            "tool_use",
            session_id=session.record.id,
            source="transcript",
            scope="root",
            tool=name,
            call_id=call_id or None,
            target=target,
            content_hash=content_hash,
            parser_version=OBSERVATION_SCHEMA_VERSION,
        )
        explicit_skill = payload.get("skill") or payload.get("skill_name")
        if name.lower() == "skill" and isinstance(explicit_skill, str) and explicit_skill.strip():
            await events.emit(
                "skill_invoked",
                session_id=session.record.id,
                source="transcript",
                scope="root",
                backend="codex",
                tool=name,
                call_id=call_id or None,
                skill=explicit_skill.strip()[:200],
                parser_version=OBSERVATION_SCHEMA_VERSION,
            )
    elif payload_type in {
        "function_call_output",
        "custom_tool_call_output",
        "exec_command_end",
    }:
        call_id = str(payload.get("call_id") or payload.get("id") or "")
        tool = _tool_names(session).pop(call_id, str(payload.get("name") or "tool"))
        exit_code_value = payload.get("exit_code")
        try:
            exit_code = int(exit_code_value) if exit_code_value is not None else None
        except (TypeError, ValueError):
            exit_code = None
        success = not bool(payload.get("is_error")) and exit_code in {None, 0}
        detail = str(
            payload.get("output")
            or payload.get("content")
            or payload.get("result")
            or payload.get("message")
            or ""
        )
        await events.emit(
            "tool_result",
            session_id=session.record.id,
            source="transcript",
            scope="root",
            tool=tool,
            call_id=call_id or None,
            success=success,
            exit_code=exit_code,
            duration_ms=payload.get("duration_ms"),
            parser_version=OBSERVATION_SCHEMA_VERSION,
            detail=detail[:4000],
        )
    elif payload_type in {"patch_apply_end", "mcp_tool_call_end", "web_search_end"}:
        tool = {
            "patch_apply_end": "apply_patch",
            "mcp_tool_call_end": "mcp_tool",
            "web_search_end": "web_search",
        }[str(payload_type)]
        success_value = payload.get("success")
        status = str(payload.get("status") or "")
        success = (
            bool(success_value) if success_value is not None else status not in {"failed", "error"}
        )
        await events.emit(
            "tool_result",
            session_id=session.record.id,
            source="transcript",
            scope="root",
            tool=tool,
            call_id=str(payload.get("call_id") or payload.get("id") or "") or None,
            success=success,
            exit_code=None,
            duration_ms=payload.get("duration_ms"),
            parser_version=OBSERVATION_SCHEMA_VERSION,
            detail=status[:4000],
        )
    elif payload_type in {
        "exec_approval_request",
        "apply_patch_approval_request",
        "request_user_input",
    }:
        detail = "input" if payload_type == "request_user_input" else "approval"
        await _transition(session, events, "awaiting", detail)
        await events.emit(
            "approval_needed",
            session_id=session.record.id,
            source="transcript",
            scope="root",
            kind=detail,
        )
    elif payload_type in {"rate_limit", "rate_limited"}:
        await _transition(session, events, "awaiting", "rate_limit")
        await events.emit(
            "rate_limited",
            session_id=session.record.id,
            source="transcript",
            scope="root",
        )
    elif payload_type == "sub_agent_activity":
        await events.emit(
            "subagent_activity",
            session_id=session.record.id,
            source="transcript",
            scope="subagent",
            kind=str(payload.get("kind") or "activity"),
            depth=len(payload.get("agent_path") or []),
        )
    elif payload_type == "context_compacted" or outer_type == "compacted":
        await events.emit(
            "context_compacted",
            session_id=session.record.id,
            source="transcript",
            scope="root",
            backend="codex",
            capability="explicit_native",
            confidence="high",
            parser_version=OBSERVATION_SCHEMA_VERSION,
        )
    elif payload_type == "token_count":
        info = payload.get("info") or payload
        total = info.get("total_token_usage") or {}
        current = info.get("last_token_usage") or total
        session.record.tokens_in = int(total.get("input_tokens", session.record.tokens_in))
        session.record.tokens_out = int(total.get("output_tokens", session.record.tokens_out))
        window = int(info.get("model_context_window") or 0)
        session.record.context_window = window
        current_input = int(current.get("input_tokens") or 0)
        session.record.context_pct = min(1, current_input / window) if window else 0
        session.record.context_peak_pct = max(
            session.record.context_peak_pct, session.record.context_pct
        )
        session.record.model = str(info.get("model") or "") or session.record.model
        session.record.measurement_source = "codex-transcript"
        _publish_update(session)


async def find_codex_transcript(cwd: str, created_at: float, stop: asyncio.Event) -> Path | None:
    root = codex_data_home() / "sessions"
    while not stop.is_set():
        candidates = (
            sorted(
                root.glob("**/rollout-*.jsonl"), key=lambda path: path.stat().st_mtime, reverse=True
            )
            if root.exists()
            else []
        )
        for path in candidates[:20]:
            if path.stat().st_mtime + 2 < created_at:
                continue
            try:
                first = path.open("r", encoding="utf-8", errors="replace").readline()
                event = json.loads(first)
                payload = event.get("payload") or {}
                if (
                    not payload.get("parent_thread_id")
                    and str(Path(payload.get("cwd", "")).resolve()).lower()
                    == str(Path(cwd).resolve()).lower()
                ):
                    return path
            except (OSError, json.JSONDecodeError):
                continue
        await asyncio.sleep(0.5)
    return None
