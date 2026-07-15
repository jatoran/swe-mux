from __future__ import annotations

import json
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Any


def _blocks(content: Any) -> list[dict[str, Any]]:
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    if isinstance(content, list):
        return [item for item in content if isinstance(item, dict)]
    return []


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
    for line in text.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if backend == "claude":
            event_type = event.get("type")
            if event_type not in {"user", "assistant"}:
                continue
            message = event.get("message") or {}
            blocks = _blocks(message.get("content"))
            if blocks:
                messages.append(
                    {"role": event_type, "ts": event.get("timestamp"), "content": blocks}
                )
        elif backend == "codex":
            payload = event.get("payload") or {}
            payload_type = payload.get("type")
            if payload_type in {"user_message", "agent_message"}:
                role = "user" if payload_type == "user_message" else "assistant"
                content = payload.get("message") or payload.get("text") or payload.get("content")
                blocks = _blocks(content)
                if blocks:
                    messages.append({"role": role, "ts": event.get("timestamp"), "content": blocks})
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
