"""Where a recorded conversation actually is, once its recorded path stopped being true.

A history row keeps ``transcript_path`` as though it were durable, and for most of a
conversation's life it is. It stops being true when the CLI moves the file. Claude
re-homes a conversation into the project slug for its new working directory when a
session enters or leaves a native worktree, reports the move through its hook, and mux
follows it - correctly, and with the file's own first record proving the identity
(`SessionManager._staged_transcript_relocation`). What mux then wrote down was a fact
about *that moment*, and the file moved again afterwards.

Measured on the development host on 2026-09-02: 301 of 1420 mux-owned agent rows named
a file that was not there. 131 of those named a `--claude-worktrees-` slug, and every
one of them was still on disk under its own conversation id in the repository's own
slug. Their symptom is not subtle - the transcript refuses to open, Resume refuses with
``transcript_unavailable`` and blames the CLI's pruning, and the row's message index
freezes wherever it got to, because the watermark describes a file nobody can read.

The repair is a search by conversation id rather than a second guess from a cwd, and it
is safe for exactly the reason `Adapter.locate_transcript` is safe: mux dictates the
conversation id at spawn (``--session-id``), so a file named after it is this
conversation's by construction and cannot be a sibling's. It costs a directory probe per
project slug - measured 9 ms over 448 slugs - which is why every caller here asks only
after the recorded path has already missed, never as part of the ordinary read.

Two rules for anything added to this module:

- **A located path is verified before it is believed.** Not every adapter searches;
  Codex's `locate_transcript` computes, so an unchecked answer would replace one dead
  path with another and record that as a repair.
- **The repair is written down.** A read that silently healed itself would fix the
  symptom for the caller in front of it and leave the next surface - Resume, Branch, the
  reindexer - reading the same dead row. Writing it back is what makes one miss enough.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .transcript_view import conversation_is_readable

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class TranscriptLocation:
    """Whether a conversation can be read right now, and from where."""

    #: True when the conversation can be read - from a file, or from a harness's own
    #: store, where ``path`` is meaningless and stays ``None``.
    readable: bool
    #: The file to read, or ``None`` for a store-backed harness or an unreadable one.
    path: Path | None = None
    #: True when the recorded path had moved and this call wrote the new one down.
    repaired: bool = False
    #: The dead path a repair replaced, for the log line and the event.
    previous: str = ""


async def locate_conversation(adapter: Any, native_id: str, backend: str) -> Path | None:
    """The file this conversation is in now, found by its id, or ``None``.

    Verified rather than trusted: `Adapter.locate_transcript` is a search for Claude and
    a computation for Codex, and the computation can answer with a path that is not
    there. Off the event loop because a search is a directory probe per project slug.
    """
    if not native_id or adapter is None:
        return None
    locate = getattr(adapter, "locate_transcript", None)
    if not callable(locate):
        return None
    try:
        found = await asyncio.to_thread(locate, native_id)
    except OSError:
        return None
    if not isinstance(found, Path):
        return None
    if not await asyncio.to_thread(conversation_is_readable, found, backend, native_id):
        return None
    return found


async def resolve_row_transcript(
    row: dict[str, Any],
    *,
    adapters: Any,
    history: Any = None,
    events: Any = None,
) -> TranscriptLocation:
    """Where this history row's conversation can be read, repairing a moved one.

    Mutates ``row['transcript_path']`` on a repair so a caller that goes on to hand the
    row to `session_resume.resumable_refusal` - or to a fork - is working from the same
    answer this returned, rather than from the dead string it arrived with.

    ``history`` and ``events`` are optional so this is callable from a context that has
    neither, but a caller holding the store is expected to pass it: an unwritten repair
    heals one read and leaves every other surface refusing the same row.
    """
    backend = str(row.get("backend") or "")
    native_id = str(row.get("native_id") or "")
    recorded = str(row.get("transcript_path") or "")
    path = Path(recorded) if recorded else None
    if await asyncio.to_thread(conversation_is_readable, path, backend, native_id):
        return TranscriptLocation(True, path)
    found = await locate_conversation(adapters.get(backend), native_id, backend)
    if found is None:
        return TranscriptLocation(False, None)
    row_id = str(row.get("id") or "")
    row["transcript_path"] = str(found)
    if history is not None and row_id:
        # The watermark is cleared with the path: it measured a file this row can no
        # longer see, so leaving it would let the next reindex conclude that nothing
        # changed and keep the message index frozen at whatever it reached.
        await history.repair_transcript_path(row_id, str(found))
    log.info(
        "history row %s transcript relocated: %s -> %s (found by conversation id %s)",
        row_id or "<unknown>",
        recorded or "<unrecorded>",
        found,
        native_id,
    )
    if events is not None:
        await events.emit(
            "history_transcript_repaired",
            source="daemon",
            agent_run_id=row_id,
            backend=backend,
            native_session_id=native_id,
            previous=recorded,
            path=str(found),
        )
    return TranscriptLocation(True, found, repaired=True, previous=recorded)
