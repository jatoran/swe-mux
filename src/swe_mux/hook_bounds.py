"""Hook bodies bounded before they leave the hook client.

The daemon refuses a hook body over `HOOK_BODY_LIMIT`, and parses every body it
accepts on its event loop. Bodies averaged 50 KiB across the 2026-10-16 fleet, almost
all of it a tool's output or a written file's contents, and the oversized ones were
refused outright: 1,688 lost events in 2.5 hours, each resent twice more for nothing.
None of the daemon's readers needs those bytes whole. What they need from them is
computed here, over the whole value, before it is cut:

- a tool result's size and digest (`output`), which canonical telemetry records;
- a tool call's normalized target and content hash (`tool_evidence`), which Tier 0
  and the loop detector fingerprint on (`tool_evidence.tool_call_evidence`);
- the size and digest of any other text field that had to be cut (`fields`).

Those travel in the body under `BOUNDS_KEY`, and the daemon prefers them to anything
it could compute from what arrived. The input of an event an approval is decided on
is never cut: a rule matched against the head of a command could approve what its
tail does.

Standard library only: the hook client runs as a fresh interpreter for every hook.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from .tool_evidence import tool_call_evidence

#: The largest hook body the daemon accepts.
HOOK_BODY_LIMIT = 256 * 1024
#: What the client aims under, leaving room for the envelope and for escaping.
HOOK_BODY_BUDGET = 192 * 1024
#: Characters of a cut text field that are kept.
FIELD_CAP = 16 * 1024
#: The smallest cap `bound_hook_payload` will halve down to before giving up.
MIN_FIELD_CAP = 1024
#: Characters of a tool result that travel after it is measured.
OUTPUT_HEAD_CHARS = 2048
#: Where the client puts what it computed before cutting.
BOUNDS_KEY = "_mux_bounded"

#: Events whose `tool_input` is reported rather than decided on, and may be cut.
TOOL_REPORT_EVENTS = frozenset({"PreToolUse", "PostToolUse", "PostToolUseFailure"})
#: The field holding a tool result's content, per event.
OUTPUT_FIELDS = {"PostToolUse": "tool_response", "PostToolUseFailure": "error"}
#: Top-level fields that identify the call or locate the conversation. Never cut.
PROTECTED_FIELDS = frozenset(
    {
        "tool_input",
        "tool_name",
        "tool_use_id",
        "session_id",
        "transcript_path",
        "cwd",
        "hook_event_name",
        "source",
        "permission_mode",
        "agent_id",
        "agent_type",
        BOUNDS_KEY,
    }
)


def hook_output_text(value: Any) -> str:
    """A tool result as the text its size and digest are measured over."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def text_metrics(text: str) -> dict[str, Any]:
    encoded = text.encode("utf-8", "replace")
    return {
        "chars": len(text),
        "bytes": len(encoded),
        "sha256": hashlib.sha256(encoded).hexdigest() if encoded else None,
    }


def encoded_body(event: str, payload: dict[str, Any], **envelope: Any) -> bytes:
    """The body the client posts, exactly as it posts it."""
    return json.dumps({"event": event, **envelope, "payload": payload}).encode()


def _cut(value: Any, cap: int) -> Any:
    """``value`` with every string longer than ``cap`` cut to its head; same object if none."""
    if isinstance(value, str):
        return value[:cap] if len(value) > cap else value
    if isinstance(value, dict):
        changed = False
        result: dict[Any, Any] = {}
        for key, item in value.items():
            cut = _cut(item, cap)
            changed = changed or cut is not item
            result[key] = cut
        return result if changed else value
    if isinstance(value, list):
        items = [_cut(item, cap) for item in value]
        if any(cut is not item for cut, item in zip(items, value, strict=True)):
            return items
    return value


def _bounded(
    event: str, payload: dict[str, Any], cap: int | None, *, other_fields: bool
) -> dict[str, Any]:
    """A copy carrying the output's metrics and head, with fields over ``cap`` cut.

    ``cap=None`` measures and cuts the output alone.
    """
    bounded = dict(payload)
    notes: dict[str, Any] = {}
    output_field = OUTPUT_FIELDS.get(event)
    if output_field is not None and output_field in bounded:
        # Measured whatever its size, so the daemon never serializes and hashes a
        # tool's output on its event loop: here it costs a process nobody waits on.
        # Once measured, nothing in the daemon reads the output itself, so only its
        # head travels, for a person reading the body; the rest was parsed on the
        # loop for nothing.
        text = hook_output_text(bounded[output_field])
        notes["output"] = text_metrics(text)
        head = OUTPUT_HEAD_CHARS if cap is None else min(cap, OUTPUT_HEAD_CHARS)
        if len(text) > head:
            bounded[output_field] = text[:head]
    if cap is None:
        if not notes:
            return payload
        bounded[BOUNDS_KEY] = notes
        return bounded
    if event in TOOL_REPORT_EVENTS and "tool_input" in bounded:
        original = bounded["tool_input"]
        cut = _cut(original, cap)
        if cut is not original:
            notes["tool_evidence"] = list(tool_call_evidence(original))
            bounded["tool_input"] = cut
    fields: dict[str, Any] = {}
    for key, value in payload.items() if other_fields else ():
        if key in PROTECTED_FIELDS or key == output_field:
            continue
        if isinstance(value, str) and len(value) > cap:
            fields[key] = text_metrics(value)
            bounded[key] = value[:cap]
    if fields:
        notes["fields"] = fields
    if notes:
        bounded[BOUNDS_KEY] = notes
    return bounded


def bound_hook_payload(event: str, payload: dict[str, Any], **envelope: Any) -> dict[str, Any]:
    """``payload`` as it should be posted: measured, and cut to fit `HOOK_BODY_BUDGET`.

    A tool result is always measured and sent as its head (`OUTPUT_HEAD_CHARS`). A
    body that then fits is otherwise unchanged, which is nearly every hook. Otherwise
    the tool result and a reported tool input are cut to `FIELD_CAP` characters; if
    that is not enough, every other unprotected text field is too, and the cap
    halves until the body fits. A body
    that still does not (an approval request's input, which is never cut, can be
    larger than the budget on its own) goes as small as it can, and the daemon's
    refusal of it is final rather than retried.
    """

    def fits(candidate: dict[str, Any]) -> bool:
        return len(encoded_body(event, candidate, **envelope)) <= HOOK_BODY_BUDGET

    measured = _bounded(event, payload, None, other_fields=False)
    if fits(measured):
        return measured
    cap = FIELD_CAP
    bounded = _bounded(event, payload, cap, other_fields=False)
    if fits(bounded):
        return bounded
    bounded = _bounded(event, payload, cap, other_fields=True)
    while not fits(bounded) and cap > MIN_FIELD_CAP:
        cap //= 2
        bounded = _bounded(event, payload, cap, other_fields=True)
    return bounded


def client_bounds(payload: dict[str, Any]) -> dict[str, Any]:
    """What the client measured before cutting, validated; empty from an older client.

    The values come from the same authenticated hook that carried the payload, so
    they are as trustworthy as the payload itself; they are checked only for shape,
    so a malformed note is ignored rather than recorded.
    """
    notes = payload.get(BOUNDS_KEY)
    if not isinstance(notes, dict):
        return {}
    result: dict[str, Any] = {}
    output = notes.get("output")
    if _valid_metrics(output):
        result["output"] = output
    evidence = notes.get("tool_evidence")
    if (
        isinstance(evidence, list)
        and len(evidence) == 3
        and all(item is None or isinstance(item, str) for item in evidence)
    ):
        result["tool_evidence"] = (evidence[0], evidence[1], evidence[2])
    fields = notes.get("fields")
    if isinstance(fields, dict):
        valid = {
            str(key): value
            for key, value in fields.items()
            if isinstance(key, str) and _valid_metrics(value)
        }
        if valid:
            result["fields"] = valid
    return result


def _valid_metrics(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    chars, size, digest = value.get("chars"), value.get("bytes"), value.get("sha256")
    return (
        isinstance(chars, int)
        and not isinstance(chars, bool)
        and chars >= 0
        and isinstance(size, int)
        and not isinstance(size, bool)
        and size >= 0
        and (
            digest is None
            or (
                isinstance(digest, str)
                and len(digest) == 64
                and all(character in "0123456789abcdef" for character in digest)
            )
        )
    )
