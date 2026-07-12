from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _blocks(content: Any) -> list[dict[str, Any]]:
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    if isinstance(content, list):
        return [item for item in content if isinstance(item, dict)]
    return []


def parse_transcript(path: Path, backend: str) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
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
                    messages.append(
                        {"role": role, "ts": event.get("timestamp"), "content": blocks}
                    )
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
