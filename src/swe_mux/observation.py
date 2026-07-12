from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from .event_bus import EventBus
from .session import Session

log = logging.getLogger(__name__)

CLAUDE_CONTEXT_WINDOWS = {
    "claude-opus-4-8": 1_000_000,
    "claude-opus-4-7": 1_000_000,
    "claude-opus-4-6": 1_000_000,
    "claude-sonnet-5": 1_000_000,
    "claude-sonnet-4-6": 1_000_000,
    "claude-haiku-4-5": 200_000,
    "claude-haiku-4-5-20251001": 200_000,
}


class JsonlTailer:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.offset = 0
        self.partial = b""

    async def events(self, stop: asyncio.Event):  # type: ignore[no-untyped-def]
        while not stop.is_set() and not self.path.exists():
            try:
                await asyncio.wait_for(stop.wait(), timeout=0.2)
            except TimeoutError:
                pass
        while not stop.is_set():
            try:
                size = self.path.stat().st_size
                if size < self.offset:
                    self.offset = 0
                if size > self.offset:
                    with self.path.open("rb") as handle:
                        handle.seek(self.offset)
                        chunk = handle.read(size - self.offset)
                    self.offset = size
                    lines = (self.partial + chunk).split(b"\n")
                    self.partial = lines.pop()
                    for line in lines:
                        if not line.strip():
                            continue
                        try:
                            yield json.loads(line.decode("utf-8", "replace"))
                        except json.JSONDecodeError:
                            log.debug("skipping invalid transcript line in %s", self.path)
            except FileNotFoundError:
                pass
            await asyncio.sleep(0.25)


async def observe_transcript(
    session: Session, path: Path, events: EventBus, stop: asyncio.Event
) -> None:
    async for event in JsonlTailer(path).events(stop):
        if session.record.backend == "claude":
            await _claude(session, event, events)
        elif session.record.backend == "codex":
            await _codex(session, event, events)


async def _transition(
    session: Session, events: EventBus, state: str, detail: str | None = None
) -> None:
    previous = session.record.state
    session.record.state = state  # type: ignore[assignment]
    session.record.state_detail = detail
    if previous != state:
        await events.emit(
            "state_changed",
            session_id=session.record.id,
            source="transcript",
            previous=previous,
            state=state,
            detail=detail,
        )


async def _claude(session: Session, event: dict[str, Any], events: EventBus) -> None:
    event_type = event.get("type")
    message = event.get("message") or {}
    if event_type == "user":
        content = message.get("content")
        if isinstance(content, str):
            await _transition(session, events, "working")
            await events.emit("turn_started", session_id=session.record.id, source="transcript")
        elif isinstance(content, list) and any(
            block.get("type") == "tool_result" for block in content if isinstance(block, dict)
        ):
            session.record.state_detail = None
            await _transition(session, events, "working")
    elif event_type == "assistant":
        await _transition(session, events, "working")
        has_text = False
        for block in message.get("content") or []:
            if isinstance(block, dict) and block.get("type") == "text":
                has_text = True
            elif isinstance(block, dict) and block.get("type") == "tool_use":
                name = str(block.get("name") or "tool")
                await _transition(session, events, "working", name)
                await events.emit(
                    "tool_use", session_id=session.record.id, source="transcript", tool=name
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
        # Interactive Claude normally appends a turn_duration system record, but
        # print/non-interactive mode can finish at the final assistant message.
        # A text response with end_turn is authoritative; tool-use messages are
        # deliberately left working until their result or a later completion.
        if message.get("stop_reason") == "end_turn" and has_text:
            await _transition(session, events, "idle")
            await events.emit(
                "turn_ended", session_id=session.record.id, source="transcript"
            )
    elif event_type == "system" and event.get("subtype") == "turn_duration":
        await _transition(session, events, "idle")
        await events.emit(
            "turn_ended",
            session_id=session.record.id,
            source="transcript",
            duration_ms=event.get("durationMs"),
        )


async def _codex(session: Session, event: dict[str, Any], events: EventBus) -> None:
    payload = event.get("payload") or {}
    outer_type, payload_type = event.get("type"), payload.get("type")
    if outer_type == "session_meta" and payload.get("id"):
        session.record.native_session_id = str(payload["id"])
        await _transition(session, events, "idle")
    if payload_type in {"task_started", "user_message"}:
        await _transition(session, events, "working")
        await events.emit("turn_started", session_id=session.record.id, source="transcript")
    elif payload_type in {"task_complete", "turn_aborted"}:
        await _transition(session, events, "idle")
        await events.emit("turn_ended", session_id=session.record.id, source="transcript")
    elif payload_type in {"function_call", "custom_tool_call"}:
        name = str(payload.get("name") or "tool")
        await _transition(session, events, "working", name)
        await events.emit("tool_use", session_id=session.record.id, source="transcript", tool=name)
    elif payload_type in {
        "exec_approval_request",
        "apply_patch_approval_request",
        "request_user_input",
    }:
        detail = "input" if payload_type == "request_user_input" else "approval"
        await _transition(session, events, "awaiting", detail)
        await events.emit(
            "approval_needed", session_id=session.record.id, source="transcript", kind=detail
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


async def find_codex_transcript(cwd: str, created_at: float, stop: asyncio.Event) -> Path | None:
    root = Path.home() / ".codex" / "sessions"
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
                    str(Path(payload.get("cwd", "")).resolve()).lower()
                    == str(Path(cwd).resolve()).lower()
                ):
                    return path
            except (OSError, json.JSONDecodeError):
                continue
        await asyncio.sleep(0.5)
    return None
