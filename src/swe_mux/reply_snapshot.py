"""Content selection and revision evidence for the Copy reply action."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .transcript_view import conversation_view, conversation_watermark, is_completed_reply


def read_reply_snapshot(path: Path | None, backend: str, native_id: str) -> dict[str, Any]:
    # Parse fresh for an explicit copy. The transcript drawer's opportunistic
    # cache is not a correctness boundary for a clipboard operation.
    before = conversation_watermark(path, backend, native_id)
    view = conversation_view(path, backend, native_id=native_id)
    after = conversation_watermark(path, backend, native_id)
    if before != after:
        raise OSError("The conversation changed while reading its reply. Try Copy again.")
    selected = next(
        (m for m in reversed(view["messages"]) if is_completed_reply(m)),
        None,
    )
    if selected is None:
        raise OSError("No completed assistant answer is available yet.")
    revision = hashlib.sha256(json.dumps([after, view], sort_keys=True).encode()).hexdigest()
    return {
        "text": selected["text"],
        "message_id": selected["message_id"],
        "turn_id": selected.get("turn_id"),
        "phase": selected.get("phase"),
        "revision": revision,
        "selection_reason": "latest_surviving_completed_answer",
        "source_watermark": after,
    }
