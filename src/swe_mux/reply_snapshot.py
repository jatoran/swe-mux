"""Content selection and revision evidence for the Copy reply action."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .conversation_index import snapshot as conversation_snapshot
from .transcript_view import conversation_watermark, is_completed_reply


def read_reply_snapshot(path: Path | None, backend: str, native_id: str) -> dict[str, Any]:
    """The latest surviving completed answer, read fresh and bracketed by watermarks.

    "Fresh" means the conversation's index is refreshed here, in this call: it stats
    the file, re-checks that the bytes it decoded are still the file's bytes, and
    decodes whatever was appended. That is the same evidence a re-parse produced,
    without paying for the conversation's whole history on every copy. The watermark
    pair around the refresh is what proves nothing was written while it ran.
    """
    before = conversation_watermark(path, backend, native_id)
    reading = conversation_snapshot(path, backend, native_id)
    after = conversation_watermark(path, backend, native_id)
    if before != after:
        raise OSError("The conversation changed while reading its reply. Try Copy again.")
    selected = next(
        (m for m in reversed(reading.messages) if is_completed_reply(m)),
        None,
    )
    if selected is None:
        raise OSError("No completed assistant answer is available yet.")
    # The file's watermark plus what was selected from it: the same bytes and the
    # same answer give the same revision in any process, whatever the index's history.
    revision = hashlib.sha256(
        json.dumps(
            [after, selected["message_id"], selected.get("turn_id"), selected["text"]],
            sort_keys=True,
        ).encode()
    ).hexdigest()
    return {
        "text": selected["text"],
        "message_id": selected["message_id"],
        "turn_id": selected.get("turn_id"),
        "phase": selected.get("phase"),
        "revision": revision,
        "selection_reason": "latest_surviving_completed_answer",
        "source_watermark": after,
    }
