"""Read opencode conversations out of ``opencode.db``.

opencode is the one registered harness that keeps its conversation in a database
rather than an append-only file. Everything else in mux that reads a conversation
reads bytes from a path, which is why opencode shipped with no Transcript tab, no
history search over its replies, and no conversational time metadata: there was no
file for those readers to open, and the absence was declared rather than bridged.

This module is the bridge. It projects opencode's ``message`` and ``part`` rows
into the same *record stream* shape the file-backed harnesses produce, so
``transcript_view`` can dispatch on a dialect exactly as it does for the others
instead of growing a second reading pipeline.

Three properties this reader deliberately keeps:

- **Read-only, WAL-safe, and never `immutable`.** opencode runs in WAL mode, so a
  reader neither blocks its writer nor risks corrupting it. `immutable` would pin
  the snapshot and hide every update a live session is still making, which is the
  opposite of what a transcript view needs.
- **Root messages only.** A subagent runs in its own ``session`` row carrying
  ``parent_id``, so selecting by session id already excludes it. That matches the
  rule the other dialects enforce by hand (Claude's ``isSidechain`` filter): a
  subagent's turns are another conversation, and indexing them pollutes history
  search and lets a child's clock win the run's first/last message time.
- **No inferred freshness.** The watermark is opencode's own ``time_updated`` plus
  the message count, never a file mtime. Windows freezes an open file's mtime at
  creation, and the database file's mtime describes every session at once, so
  neither could tell one conversation's staleness.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

# The bound a caller expresses as "give me the tail" becomes a message count here,
# because a database conversation has no byte offsets to seek to. Sized to cover a
# long working session while keeping a bounded read: opencode writes one row per
# message rather than one per streamed delta, so a few hundred rows is a very long
# conversation rather than a few minutes of one.
TAIL_MESSAGE_LIMIT = 400


def _connect(database: Path) -> sqlite3.Connection | None:
    if not database.is_file():
        return None
    try:
        connection = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)
    except sqlite3.Error:
        return None
    connection.row_factory = sqlite3.Row
    return connection


def _json_object(value: Any) -> dict[str, Any]:
    if not isinstance(value, str) or not value:
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def conversation_watermark(database: Path, session_id: str) -> tuple[int, int] | None:
    """``(time_updated, message_count)`` for one conversation, or ``None``.

    The store-backed analogue of a file's ``(mtime_ns, size)``: the pair a parse
    result stays valid for. Both halves are needed. ``time_updated`` alone misses a
    message written inside the same millisecond, and a count alone misses an edit
    to an existing row, which opencode does when a streaming assistant message
    completes.

    ``None`` means the conversation is not in this database, which callers must
    treat as "nothing to read" rather than as an empty conversation.
    """
    if not session_id:
        return None
    connection = _connect(database)
    if connection is None:
        return None
    try:
        row = connection.execute(
            "SELECT s.time_updated AS updated,"
            " (SELECT count(*) FROM message m WHERE m.session_id = s.id) AS messages"
            " FROM session s WHERE s.id = ?",
            (session_id,),
        ).fetchone()
    except sqlite3.Error:
        return None
    finally:
        connection.close()
    if row is None:
        return None
    updated = row["updated"] if isinstance(row["updated"], int) else 0
    messages = row["messages"] if isinstance(row["messages"], int) else 0
    return updated, messages


def _as_int(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _as_float(value: Any) -> float:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0.0


def _model_field(value: Any, key: str) -> str | None:
    """opencode stores the model as a JSON blob: ``{id, providerID, variant}``."""
    parsed = _json_object(value).get(key)
    return parsed if isinstance(parsed, str) and parsed else None


def session_measurements(database: Path, session_id: str) -> dict[str, Any] | None:
    """Exact token, cost, and model figures for one conversation.

    opencode maintains these on the ``session`` row itself, so measurement is a
    single indexed read rather than a parse: no tailer, no byte offsets, and none of
    the Windows frozen-mtime hazard that dogs file-based liveness, because nothing
    here infers freshness from a timestamp.

    ``None`` when the row is absent or unreadable, which the caller must treat as
    "no measurement available" rather than as zeroes: a published zero is
    indistinguishable from a genuinely empty conversation.

    Lives here rather than on the adapter because two readers need it - the live
    session's measurement refresh, and the retroactive discovery that summarizes a
    conversation mux never ran.
    """
    if not session_id:
        return None
    connection = _connect(database)
    if connection is None:
        return None
    try:
        row = connection.execute(
            "SELECT cost, tokens_input, tokens_output, tokens_reasoning,"
            " tokens_cache_read, tokens_cache_write, model, agent, title"
            " FROM session WHERE id = ?",
            (session_id,),
        ).fetchone()
    except sqlite3.Error:
        return None
    finally:
        connection.close()
    if row is None:
        return None
    return {
        "tokens_in": _as_int(row["tokens_input"]),
        "tokens_out": _as_int(row["tokens_output"]),
        "tokens_cache_read": _as_int(row["tokens_cache_read"]),
        "tokens_cache_write": _as_int(row["tokens_cache_write"]),
        "tokens_reasoning": _as_int(row["tokens_reasoning"]),
        "cost_usd": _as_float(row["cost"]),
        "model": _model_field(row["model"], "id"),
        "provider": _model_field(row["model"], "providerID"),
        "agent": row["agent"] or None,
        "title": row["title"] or None,
    }


def conversation_records(
    database: Path, session_id: str, *, max_messages: int | None = None
) -> list[dict[str, Any]]:
    """One record per message, newest last, with that message's parts attached.

    The record is a faithful projection rather than an interpretation: the stored
    message JSON and the stored part JSON travel intact under ``message`` and
    ``parts``, and only the envelope is mux's. Reading the conversation is
    ``transcript_view``'s job, and keeping the projection dumb is what lets that
    reader be checked against real opencode rows.

    ``max_messages`` bounds the read to the most recent N messages while preserving
    chronological order, the way a bounded tail read does for a file.
    """
    if not session_id:
        return []
    connection = _connect(database)
    if connection is None:
        return []
    try:
        if max_messages is None:
            rows = connection.execute(
                "SELECT id, time_created, data FROM message"
                " WHERE session_id = ? ORDER BY time_created, id",
                (session_id,),
            ).fetchall()
        else:
            # Take the newest N, then restore chronological order. Ordering the
            # whole conversation and slicing in Python would read every row, which
            # is the cost the bound exists to avoid.
            rows = list(
                reversed(
                    connection.execute(
                        "SELECT id, time_created, data FROM message"
                        " WHERE session_id = ? ORDER BY time_created DESC, id DESC LIMIT ?",
                        (session_id, max(1, max_messages)),
                    ).fetchall()
                )
            )
        if not rows:
            return []
        message_ids = [str(row["id"]) for row in rows]
        placeholders = ",".join("?" * len(message_ids))
        parts = connection.execute(
            f"SELECT message_id, id, data FROM part WHERE message_id IN ({placeholders})"
            " ORDER BY message_id, id",
            message_ids,
        ).fetchall()
    except sqlite3.Error:
        # A schema opencode has since changed, or a database being rewritten
        # underneath the read. Both mean "no records available now", which the
        # Transcript tab renders as empty rather than as an error, exactly as an
        # unreadable transcript file does.
        return []
    finally:
        connection.close()

    grouped: dict[str, list[dict[str, Any]]] = {}
    for part in parts:
        payload = _json_object(part["data"])
        if payload:
            grouped.setdefault(str(part["message_id"]), []).append(payload)

    records: list[dict[str, Any]] = []
    for row in rows:
        message = _json_object(row["data"])
        if not message:
            continue
        identifier = str(row["id"])
        records.append(
            {
                "type": "message",
                "id": identifier,
                # opencode stamps milliseconds. The row's own column is used rather
                # than the nested `time.created`, because the column is indexed and
                # is what the ordering above was done on.
                "timestamp": row["time_created"],
                "message": message,
                "parts": grouped.get(identifier, []),
            }
        )
    return records


def conversation_record_page(
    database: Path,
    session_id: str,
    *,
    direction: str,
    anchor: tuple[int, str] | None = None,
    limit: int = TAIL_MESSAGE_LIMIT,
) -> tuple[list[dict[str, Any]], bool]:
    """One bounded page of stored messages plus whether more exist that way.

    ``anchor`` is the exclusive ``(time_created, id)`` edge from the previous
    page. The pair is stable under appends and disambiguates messages written in
    the same millisecond. Rows are always returned in chronological order.
    """
    if not session_id or direction not in {"head", "tail"}:
        return [], False
    connection = _connect(database)
    if connection is None:
        return [], False
    bounded = max(1, min(int(limit), TAIL_MESSAGE_LIMIT))
    conditions = ["session_id = ?"]
    parameters: list[Any] = [session_id]
    if anchor is not None:
        operator = ">" if direction == "head" else "<"
        conditions.append(
            f"(time_created {operator} ? OR (time_created = ? AND id {operator} ?))"
        )
        parameters.extend([int(anchor[0]), int(anchor[0]), str(anchor[1])])
    order = "ASC" if direction == "head" else "DESC"
    try:
        rows = connection.execute(
            f"SELECT id, time_created, data FROM message WHERE {' AND '.join(conditions)}"
            f" ORDER BY time_created {order}, id {order} LIMIT ?",
            (*parameters, bounded + 1),
        ).fetchall()
        has_more = len(rows) > bounded
        rows = rows[:bounded]
        if direction == "tail":
            rows = list(reversed(rows))
        if not rows:
            return [], False
        message_ids = [str(row["id"]) for row in rows]
        placeholders = ",".join("?" * len(message_ids))
        parts = connection.execute(
            f"SELECT message_id, id, data FROM part WHERE message_id IN ({placeholders})"
            " ORDER BY message_id, id",
            message_ids,
        ).fetchall()
    except sqlite3.Error:
        return [], False
    finally:
        connection.close()

    grouped: dict[str, list[dict[str, Any]]] = {}
    for part in parts:
        payload = _json_object(part["data"])
        if payload:
            grouped.setdefault(str(part["message_id"]), []).append(payload)
    records: list[dict[str, Any]] = []
    for row in rows:
        message = _json_object(row["data"])
        if not message:
            continue
        identifier = str(row["id"])
        records.append(
            {
                "type": "message",
                "id": identifier,
                "timestamp": row["time_created"],
                "message": message,
                "parts": grouped.get(identifier, []),
                "__swe_mux_page_time": int(row["time_created"] or 0),
                "__swe_mux_page_id": identifier,
            }
        )
    return records, has_more


__all__ = [
    "TAIL_MESSAGE_LIMIT",
    "conversation_record_page",
    "conversation_records",
    "conversation_watermark",
    "session_measurements",
]
