"""A hand-built Claude transcript with every shape a fork has to reason about.

Written as a builder rather than a checked-in file so a test can say which shape it
cares about in the assertion itself. The record set mirrors what a real transcript
holds: a turn that calls a tool and one that does not, the housekeeping records
Claude interleaves, a queued prompt, and an oversized tool result persisted to the
conversation's own directory.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

SOURCE_ID = "aaaaaaaa-1111-4a7b-8c9d-0e1f2a3b4c5d"
SIDECAR_NAME = "tool-results/b4gq99nca.txt"
SIDECAR_BODY = "the oversized tool output the transcript points at\n"


def _user(uuid: str, parent: str | None, text: str, session: str) -> dict[str, Any]:
    return {
        "parentUuid": parent,
        "isSidechain": False,
        "type": "user",
        "message": {"role": "user", "content": text},
        "uuid": uuid,
        "timestamp": "2026-08-17T12:00:00.000Z",
        "origin": {"kind": "human"},
        "sessionId": session,
    }


def _assistant(
    uuid: str, parent: str | None, text: str, session: str, tool: str | None = None
) -> dict[str, Any]:
    content: list[dict[str, Any]] = [{"type": "text", "text": text}]
    if tool:
        content.append({"type": "tool_use", "id": tool, "name": "Bash", "input": {"command": "ls"}})
    return {
        "parentUuid": parent,
        "isSidechain": False,
        "type": "assistant",
        "message": {"role": "assistant", "content": content},
        "uuid": uuid,
        "timestamp": "2026-08-17T12:00:01.000Z",
        "sessionId": session,
    }


def _tool_result(uuid: str, parent: str, tool: str, session: str, sidecar: Path) -> dict[str, Any]:
    return {
        "parentUuid": parent,
        "isSidechain": False,
        "type": "user",
        "message": {
            "role": "user",
            "content": [
                {
                    "tool_use_id": tool,
                    "type": "tool_result",
                    "content": f"Full output saved to: {sidecar}\n\nPreview (first 2KB):\nok",
                }
            ],
        },
        "uuid": uuid,
        "timestamp": "2026-08-17T12:00:02.000Z",
        "toolUseResult": {"persistedOutputPath": str(sidecar)},
        "sessionId": session,
    }


def write_source(directory: Path, session: str = SOURCE_ID) -> Path:
    """Write `<session>.jsonl` plus its sidecar directory, and return the transcript.

    The conversation, in order:

    ``u1`` prompt, ``a1`` reply that calls a tool, ``r1`` the tool's result,
    ``a2`` the reply that follows it, ``u2`` a second prompt, ``a3`` its reply.
    Interleaved: a `mode` record, an `ai-title`, a `last-prompt` naming ``a2``, and a
    `queue-operation` holding a prompt the operator queued but never sent.
    """
    directory.mkdir(parents=True, exist_ok=True)
    sidecar_dir = directory / session
    sidecar = sidecar_dir / "tool-results" / "b4gq99nca.txt"
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    sidecar.write_text(SIDECAR_BODY, encoding="utf-8")
    records: list[dict[str, Any]] = [
        {"type": "mode", "mode": "normal", "sessionId": session},
        _user("u1", None, "first prompt", session),
        _assistant("a1", "u1", "looking into it", session, tool="toolu_1"),
        _tool_result("r1", "a1", "toolu_1", session, sidecar),
        _assistant("a2", "r1", "first answer", session),
        {"type": "ai-title", "aiTitle": "Investigate the thing", "sessionId": session},
        {"type": "last-prompt", "lastPrompt": "first prompt", "leafUuid": "a2",
         "sessionId": session},
        {"type": "queue-operation", "operation": "enqueue", "content": "do not inherit me",
         "sessionId": session},
        _user("u2", "a2", "second prompt", session),
        _assistant("a3", "u2", "second answer", session),
    ]
    path = directory / f"{session}.jsonl"
    path.write_bytes(
        b"".join(
            json.dumps(record, ensure_ascii=False).encode("utf-8") + b"\n" for record in records
        )
    )
    return path


def read_records(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
