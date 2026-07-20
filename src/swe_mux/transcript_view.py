from __future__ import annotations

import json
import threading
from collections import OrderedDict
from datetime import datetime
from pathlib import Path
from typing import Any

TRANSCRIPT_PARSER_VERSION = 2


def _blocks(content: Any) -> list[dict[str, Any]]:
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    if isinstance(content, list):
        return [item for item in content if isinstance(item, dict)]
    return []


def _codex_blocks(content: Any) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for item in _blocks(content):
        kind = item.get("type")
        if kind in {"text", "input_text", "output_text"} and item.get("text"):
            blocks.append({"type": "text", "text": item["text"]})
    return blocks


def _native_conversation_message(event: dict[str, Any], backend: str) -> dict[str, Any] | None:
    timestamp = event.get("timestamp")
    if backend == "claude":
        role = event.get("type")
        if role not in {"user", "assistant"}:
            return None
        blocks = _blocks((event.get("message") or {}).get("content"))
    else:
        payload = event.get("payload") or {}
        payload_type = payload.get("type")
        if event.get("type") == "response_item" and payload_type == "message":
            role = payload.get("role")
            if role not in {"user", "assistant"}:
                return None
            blocks = _codex_blocks(payload.get("content"))
        elif event.get("type") == "event_msg" and payload_type in {
            "user_message",
            "agent_message",
        }:
            role = "user" if payload_type == "user_message" else "assistant"
            content = payload.get("message") or payload.get("text") or payload.get("content")
            blocks = _blocks(content)
        else:
            return None
    if not any(block.get("type") == "text" and block.get("text") for block in blocks):
        return None
    return {"role": role, "ts": timestamp, "content": blocks}


def _timestamp_key(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        stamp = float(value)
        return stamp / 1000 if stamp > 10_000_000_000 else stamp
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    try:
        stamp = float(text)
    except ValueError:
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    return stamp / 1000 if stamp > 10_000_000_000 else stamp


def transcript_time_summary(
    path: Path, backend: str, *, head_bytes: int = 512 * 1024, tail_bytes: int = 8 * 1024 * 1024
) -> dict[str, Any]:
    """Read bounded transcript edges and return native conversational time metadata."""
    stat = path.stat()
    with path.open("rb") as handle:
        head = handle.read(head_bytes)
        handle.seek(max(0, stat.st_size - tail_bytes))
        tail = handle.read(tail_bytes)
    if stat.st_size > tail_bytes:
        _, _, tail = tail.partition(b"\n")

    def events(raw: bytes) -> list[dict[str, Any]]:
        parsed: list[dict[str, Any]] = []
        for line in raw.decode("utf-8", "replace").splitlines():
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                parsed.append(item)
        return parsed

    edge_messages = [
        message
        for event in [*events(head), *events(tail)]
        if (message := _native_conversation_message(event, backend)) is not None
        and _timestamp_key(message.get("ts")) is not None
    ]
    first = (
        min(edge_messages, key=lambda message: _timestamp_key(message.get("ts")) or 0)
        if edge_messages
        else None
    )
    last = (
        max(edge_messages, key=lambda message: _timestamp_key(message.get("ts")) or 0)
        if edge_messages
        else None
    )
    return {
        "native_started_ts": first.get("ts") if first else None,
        "last_message_ts": last.get("ts") if last else None,
        "last_message_role": last.get("role") if last else None,
        "mtime_ns": stat.st_mtime_ns,
        "size": stat.st_size,
    }


def parse_transcript(
    path: Path, backend: str, *, max_bytes: int | None = None
) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    if max_bytes is None:
        text = path.read_text(encoding="utf-8", errors="replace")
    else:
        with path.open("rb") as handle:
            size = handle.seek(0, 2)
            handle.seek(max(0, size - max_bytes))
            raw = handle.read(max_bytes)
        if size > max_bytes:
            _, _, raw = raw.partition(b"\n")
        text = raw.decode("utf-8", "replace")
    events: list[dict[str, Any]] = []
    for line in text.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            events.append(event)
    codex_response_messages = backend == "codex" and any(
        event.get("type") == "response_item"
        and (event.get("payload") or {}).get("type") == "message"
        and (event.get("payload") or {}).get("role") in {"user", "assistant"}
        for event in events
    )
    for event in events:
        if backend == "claude":
            if message := _native_conversation_message(event, backend):
                messages.append(message)
        elif backend == "codex":
            payload = event.get("payload") or {}
            payload_type = payload.get("type")
            if event.get("type") == "response_item" and payload_type == "message":
                if message := _native_conversation_message(event, backend):
                    messages.append(message)
            elif not codex_response_messages and payload_type in {"user_message", "agent_message"}:
                if message := _native_conversation_message(event, backend):
                    messages.append(message)
            elif payload_type in {"function_call", "custom_tool_call"}:
                messages.append(
                    {
                        "role": "assistant",
                        "ts": event.get("timestamp"),
                        "content": [
                            {
                                "type": "tool_use",
                                "name": payload.get("name") or "tool",
                                "input": payload.get("arguments") or payload.get("input"),
                            }
                        ],
                    }
                )
    return messages


_CACHE_MAX = 32
_cache: OrderedDict[tuple[str, int, str, int | None], list[dict[str, Any]]] = OrderedDict()
_cache_lock = threading.Lock()


def parse_transcript_cached(
    path: Path, backend: str, *, max_bytes: int | None = None
) -> list[dict[str, Any]]:
    """Cached ``parse_transcript`` keyed on (path, mtime_ns, backend, max_bytes).

    The same transcript is parsed several times per turn (fleet claim-check,
    titler/summarizer observers, the history transcript view). This bounded LRU
    collapses the repeated whole-file read + per-line JSON parse into one.

    Safe to call from both the event loop and ``asyncio.to_thread`` workers: a
    single lock guards the map, but the parse itself runs OUTSIDE the lock so
    parses of distinct files never serialize. ``path.stat()`` raising ``OSError``
    on a missing/locked file propagates exactly as ``parse_transcript``'s read
    would; the cache never swallows it. The returned list is SHARED across
    callers and MUST be treated as read-only.
    """
    mtime_ns = path.stat().st_mtime_ns
    key = (str(path), mtime_ns, backend, max_bytes)
    with _cache_lock:
        hit = _cache.get(key)
        if hit is not None:
            _cache.move_to_end(key)
            return hit
    result = parse_transcript(path, backend, max_bytes=max_bytes)
    with _cache_lock:
        _cache[key] = result
        _cache.move_to_end(key)
        while len(_cache) > _CACHE_MAX:
            _cache.popitem(last=False)
    return result


def searchable_transcript_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Reduce parsed native messages to local, rebuildable search records."""
    searchable: list[dict[str, Any]] = []
    for ordinal, message in enumerate(messages):
        role = str(message.get("role") or "")
        if role not in {"user", "assistant"}:
            continue
        text = "\n".join(
            str(block.get("text") or "").strip()
            for block in message.get("content") or []
            if isinstance(block, dict) and block.get("type") == "text" and block.get("text")
        ).strip()
        if text:
            searchable.append(
                {"ordinal": ordinal, "role": role, "ts": message.get("ts"), "text": text}
            )
    return searchable
