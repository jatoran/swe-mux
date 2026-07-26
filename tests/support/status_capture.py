"""Sanitized capture → golden-fixture pipeline (Phase 3.5).

Turns a real session's evidence stream (its native transcript, optionally the
hook events and `state-log` diagnostic) into a scrubbed detection fixture that
can be reviewed and promoted into ``tests/fixtures/detection/v1/`` as a
permanent regression test. Scrubbing keeps only the structural fields the
observer keys on: no prompt bodies, no tool arguments or outputs, no terminal
bytes, no native identifiers survive.

CLI (run from the repo root):

    uv run python tests/support/status_capture.py --backend claude \
        --transcript path/to/native.jsonl --description "what went wrong" \
        [--state-log state_log.json] [--edge-case name] --out fixture.json

The expected block (events, states, parser, delivery) is filled by replaying
the scrubbed steps through the deterministic harness; review it before
committing the fixture — the point of promotion is to pin today's *correct*
behavior, not to bless the bug being captured.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
from pathlib import Path
from typing import Any

SCRUBBED = "[scrubbed]"
# The two user-record prefixes the observer keys on; everything after the
# recognizable prefix is content and gets dropped.
INTERRUPT_PREFIX = "[Request interrupted by user"
LOCAL_COMMAND_MARKER = "<command-name>scrubbed</command-name>"
_CONTROL_BYTES = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def _clean(text: str) -> str:
    """Strip ANSI/control bytes from any string that survives scrubbing."""
    return _CONTROL_BYTES.sub("", text)


class _IdMap:
    """Stable remapping of opaque native ids to sequential placeholders."""

    def __init__(self, prefix: str) -> None:
        self.prefix = prefix
        self.seen: dict[str, str] = {}

    def __call__(self, native: Any) -> str | None:
        if not native:
            return None
        key = str(native)
        if key not in self.seen:
            self.seen[key] = f"{self.prefix}-{len(self.seen) + 1}"
        return self.seen[key]


def scrub_claude_record(record: dict[str, Any], ids: _IdMap) -> dict[str, Any] | None:
    record_type = str(record.get("type") or "")
    out: dict[str, Any] = {"type": record_type}
    if record.get("isSidechain") is True:
        out["isSidechain"] = True
    elif record_type in {"user", "assistant"}:
        out["isSidechain"] = False
    if record.get("isMeta") is True:
        out["isMeta"] = True
    if record_type == "system":
        subtype = record.get("subtype")
        if subtype:
            out["subtype"] = str(subtype)
        if record.get("durationMs") is not None:
            out["durationMs"] = record.get("durationMs")
        return out
    if record_type not in {"user", "assistant"}:
        # Bookkeeping records carry no turn signal beyond their type.
        return out
    message = record.get("message") or {}
    content = message.get("content")
    scrubbed_message: dict[str, Any] = {}
    if isinstance(content, str):
        text = content.lstrip()
        if text.startswith(INTERRUPT_PREFIX):
            scrubbed_message["content"] = INTERRUPT_PREFIX + "]"
        elif text.startswith(("<command-", "<local-command-")):
            scrubbed_message["content"] = LOCAL_COMMAND_MARKER
        else:
            scrubbed_message["content"] = SCRUBBED
    elif isinstance(content, list):
        blocks: list[dict[str, Any]] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            block_type = block.get("type")
            if block_type == "text":
                text = str(block.get("text") or "").lstrip()
                if text.startswith(INTERRUPT_PREFIX):
                    blocks.append({"type": "text", "text": INTERRUPT_PREFIX + "]"})
                else:
                    blocks.append({"type": "text", "text": SCRUBBED})
            elif block_type == "image":
                blocks.append({"type": "image"})
            elif block_type == "tool_use":
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": ids(block.get("id")) or "tool-1",
                        "name": _clean(str(block.get("name") or "tool")),
                        "input": {},
                    }
                )
            elif block_type == "tool_result":
                blocks.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": ids(block.get("tool_use_id")) or "tool-1",
                        "content": SCRUBBED,
                        "is_error": bool(block.get("is_error")),
                    }
                )
        scrubbed_message["content"] = blocks
    if message.get("stop_reason"):
        scrubbed_message["stop_reason"] = str(message["stop_reason"])
    if record_type == "assistant":
        usage = message.get("usage")
        if isinstance(usage, dict):
            scrubbed_message["usage"] = {
                key: int(value)
                for key, value in usage.items()
                if isinstance(value, (int, float))
            }
        if message.get("model"):
            scrubbed_message["model"] = _clean(str(message["model"]))
    out["message"] = scrubbed_message
    return out


_CODEX_KEEP_NUMERIC = ("exit_code", "duration_ms")


def scrub_codex_record(record: dict[str, Any], ids: _IdMap) -> dict[str, Any] | None:
    outer = str(record.get("type") or "")
    payload = record.get("payload")
    out: dict[str, Any] = {"type": outer}
    if not isinstance(payload, dict):
        return out
    payload_type = str(payload.get("type") or "")
    scrubbed: dict[str, Any] = {"type": payload_type} if payload_type else {}
    if outer == "session_meta":
        if payload.get("parent_thread_id"):
            scrubbed["parent_thread_id"] = "parent-1"
        scrubbed["id"] = "native-1"
        if payload.get("model"):
            scrubbed["model"] = _clean(str(payload["model"]))
    if payload.get("name"):
        scrubbed["name"] = _clean(str(payload["name"]))
    call_id = ids(payload.get("call_id") or payload.get("id"))
    if call_id and outer != "session_meta":
        scrubbed["call_id"] = call_id
    for key in _CODEX_KEEP_NUMERIC:
        if payload.get(key) is not None:
            scrubbed[key] = payload[key]
    if payload.get("success") is not None:
        scrubbed["success"] = bool(payload["success"])
    if payload.get("status"):
        scrubbed["status"] = _clean(str(payload["status"]))[:40]
    for key in ("output", "content", "result", "message", "arguments", "input"):
        if key in payload:
            scrubbed[key] = {} if key in {"arguments", "input"} else SCRUBBED
    out["payload"] = scrubbed
    return out


def scrub_hook_event(event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    kept: dict[str, Any] = {}
    for key in ("tool_name", "notification_type", "isSidechain", "is_sidechain", "reason"):
        if payload.get(key) is not None:
            kept[key] = (
                _clean(str(payload[key])) if isinstance(payload[key], str) else payload[key]
            )
    if "message" in payload:
        kept["message"] = SCRUBBED
    return {"kind": "hook", "event": event_type, "payload": kept}


def scrub_state_log(state_log: dict[str, Any]) -> list[dict[str, Any]]:
    """Reduce a captured state-log to its transition shape for fixture review."""
    shaped: list[dict[str, Any]] = []
    for entry in state_log.get("transitions") or []:
        if not isinstance(entry, dict) or entry.get("kind") != "transition":
            continue
        item = {
            "previous": entry.get("previous"),
            "state": entry.get("state"),
            "source": entry.get("source"),
            "proof": entry.get("proof"),
            "evidence": entry.get("evidence"),
        }
        if entry.get("awaiting_reason"):
            item["awaiting_reason"] = entry["awaiting_reason"]
        shaped.append(item)
    return shaped


def build_status_fixture(
    backend: str,
    description: str,
    transcript_records: list[dict[str, Any]],
    *,
    hook_events: list[tuple[str, dict[str, Any]]] | None = None,
    state_log: dict[str, Any] | None = None,
    edge_case: str | None = None,
) -> dict[str, Any]:
    """Build a scrubbed, replayable v1 manifest from captured evidence."""
    ids = _IdMap("tool")
    steps: list[dict[str, Any]] = []
    for event_type, payload in hook_events or []:
        steps.append(scrub_hook_event(event_type, dict(payload)))
    scrub = scrub_claude_record if backend == "claude" else scrub_codex_record
    for record in transcript_records:
        scrubbed = scrub(dict(record), ids)
        if scrubbed is not None:
            steps.append({"kind": "transcript", "record": scrubbed})
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "backend": backend,
        "description": description,
        "steps": steps,
        "expected": {},
    }
    if edge_case:
        manifest["edge_case"] = edge_case
    if state_log:
        manifest["captured_transitions"] = scrub_state_log(state_log)
    return manifest


async def fill_expected(manifest: dict[str, Any]) -> dict[str, Any]:
    """Replay the scrubbed steps and pin their outputs as the expected block."""
    from tests.support.detection_replay import DetectionReplay

    result = await DetectionReplay(manifest["backend"]).run(manifest)
    readiness = result["readiness"]
    manifest["expected"] = {
        "events": result["events"],
        "states": result["states"],
        "parser": result["parser"],
        "delivery_state": readiness["delivery_state"],
        "delivery_reason": readiness["reason"],
    }
    return manifest


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            records.append(item)
    return records


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", required=True, choices=["claude", "codex"])
    parser.add_argument("--transcript", required=True, type=Path)
    parser.add_argument("--description", required=True)
    parser.add_argument("--state-log", type=Path, default=None)
    parser.add_argument("--edge-case", default=None)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()

    state_log = (
        json.loads(args.state_log.read_text(encoding="utf-8")) if args.state_log else None
    )
    manifest = build_status_fixture(
        args.backend,
        args.description,
        _read_jsonl(args.transcript),
        state_log=state_log,
        edge_case=args.edge_case,
    )
    asyncio.run(fill_expected(manifest))
    args.out.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {args.out} ({len(manifest['steps'])} steps)")
    print("Review expected.states before promoting into tests/fixtures/detection/v1/.")


if __name__ == "__main__":
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
    main()
