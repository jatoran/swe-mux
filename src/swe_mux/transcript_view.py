from __future__ import annotations

import json
import logging
import re
import threading
from collections import OrderedDict, defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, assert_never

from .harness import conversation_store_path, transcript_dialect
from .opencode_store import TAIL_MESSAGE_LIMIT, conversation_record_page, conversation_records
from .opencode_store import conversation_watermark as store_watermark

log = logging.getLogger(__name__)

# Bumped to 4 when abandoned-branch records stopped being indexed as conversation
# (`_mark_abandoned_records`). Every indexed transcript reparses once on the next
# touch, because the watermark carries this number.
TRANSCRIPT_PARSER_VERSION = 4
_SOURCE_OFFSET_KEY = "__swe_mux_source_offset"
_SOURCE_END_KEY = "__swe_mux_source_end"
# Set on a record the conversation's own linkage proves is off the live branch.
# Private to this module: readers see the public `abandoned` flag on a message.
_ABANDONED_KEY = "__swe_mux_abandoned"


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


def _tool_input(value: Any) -> Any:
    """Preserve native tool input, decoding JSON argument strings when possible."""
    if not isinstance(value, str):
        return value
    try:
        decoded = json.loads(value)
    except (TypeError, ValueError):
        return value
    return decoded


def _message_blocks(content: Any, native_tool_type: str) -> list[dict[str, Any]]:
    """Conversation text and tool inputs only, never results or private reasoning."""
    blocks: list[dict[str, Any]] = []
    for item in _blocks(content):
        kind = item.get("type")
        if kind == "text" and item.get("text"):
            blocks.append({"type": "text", "text": item["text"]})
            continue
        if kind != native_tool_type:
            continue
        blocks.append(
            {
                "type": "tool_use",
                "id": item.get("id") or item.get("tool_use_id"),
                "name": item.get("name") or item.get("tool") or "tool",
                "input": _tool_input(item.get("input", item.get("arguments"))),
            }
        )
    return blocks


def _opencode_blocks(parts: Any) -> list[dict[str, Any]]:
    """Renderable blocks from one opencode message's parts, in stored order.

    Only two part types are conversation: ``text`` is what was said, and ``tool`` is
    what was done. ``reasoning`` is deliberately excluded, matching every other
    dialect here, which renders the reply rather than the model's private working.
    ``step-start`` and ``step-finish`` are streaming bookkeeping, and ``patch``
    duplicates a tool result that the tool part already carries.
    """
    blocks: list[dict[str, Any]] = []
    for item in _blocks(parts):
        kind = item.get("type")
        if kind == "text" and item.get("text"):
            blocks.append({"type": "text", "text": item["text"]})
        elif kind == "tool":
            state = item.get("state")
            blocks.append(
                {
                    "type": "tool_use",
                    "id": item.get("id") or item.get("callID") or item.get("call_id"),
                    "name": item.get("tool") or "tool",
                    "input": state.get("input") if isinstance(state, dict) else None,
                }
            )
    return blocks


def _native_conversation_message(event: dict[str, Any], backend: str) -> dict[str, Any] | None:
    timestamp = event.get("timestamp")
    # Dispatch on the record dialect, not the harness name. This function used to
    # key on the name with a silent `else: return None`, so adding pi produced an
    # empty Transcript tab with nothing failing anywhere — the reader simply had
    # no branch for it. `assert_never` below makes the next harness a compile
    # error instead of an empty tab.
    dialect = transcript_dialect(backend)
    if dialect is None:
        return None
    if dialect == "claude":
        role = event.get("type")
        if role not in {"user", "assistant"}:
            return None
        # Claude writes subagent turns into the root transcript tagged
        # ``isSidechain``. They are another agent's conversation, so they are not
        # root messages: indexing them pollutes history search, and letting them
        # win min/max makes a subagent's clock the run's first/last message time.
        # The live observer already special-cases them; this is the same rule on
        # the parse path.
        if event.get("isSidechain") is True:
            return None
        blocks = _message_blocks(
            (event.get("message") or {}).get("content"),
            native_tool_type="tool_use",
        )
    elif dialect == "pi":
        # oh-my-pi and upstream pi write the same message record.
        if event.get("type") != "message":
            return None
        native_message = event.get("message") or {}
        role = native_message.get("role")
        if role not in {"user", "assistant"}:
            return None
        blocks = _message_blocks(native_message.get("content"), native_tool_type="toolCall")
    elif dialect == "codex":  # noqa: SIM114 - distinct dialects, distinct shapes
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
    elif dialect == "opencode":
        # A record projected from `opencode.db` rows: the stored message JSON plus
        # its stored parts (`opencode_store.conversation_records`). One record is
        # one message, so unlike Codex there is no second pass for tool calls.
        if event.get("type") != "message":
            return None
        native_message = event.get("message") or {}
        role = native_message.get("role")
        if role not in {"user", "assistant"}:
            return None
        blocks = _opencode_blocks(event.get("parts"))
    else:
        assert_never(dialect)
    if not blocks:
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
    path: Path | None,
    backend: str,
    *,
    head_bytes: int = 512 * 1024,
    tail_bytes: int = 8 * 1024 * 1024,
    native_id: str | None = None,
) -> dict[str, Any]:
    """Read bounded transcript edges and return native conversational time metadata.

    A store-backed conversation has indexed edges rather than byte ranges, so it
    reads its first and last messages directly instead of decoding a head and tail
    slice. The returned shape is identical, including the watermark pair, which for
    such a harness is opencode's ``(time_updated, message_count)`` rather than a
    file stat (see :func:`conversation_watermark` for why a stat cannot serve).
    """
    store = conversation_store_path(backend)
    if store is not None:
        identity, first_mark, second_mark = conversation_watermark(path, backend, native_id)
        del identity
        edges = [
            message
            for record in conversation_records(store, native_id or "", max_messages=None)
            if (message := _native_conversation_message(record, backend)) is not None
            and _timestamp_key(message.get("ts")) is not None
        ]
        def stamp(item: dict[str, Any]) -> float:
            return _timestamp_key(item.get("ts")) or 0

        first_message = min(edges, key=stamp, default=None)
        last_message = max(edges, key=stamp, default=None)
        return {
            "native_started_ts": first_message.get("ts") if first_message else None,
            "last_message_ts": last_message.get("ts") if last_message else None,
            "last_message_role": last_message.get("role") if last_message else None,
            "mtime_ns": first_mark,
            "size": second_mark,
        }
    if path is None:
        return {
            "native_started_ts": None,
            "last_message_ts": None,
            "last_message_role": None,
            "mtime_ns": 0,
            "size": 0,
        }
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


def _claude_tool_use_ids(event: dict[str, Any]) -> set[str]:
    return {
        str(block["id"])
        for block in _blocks((event.get("message") or {}).get("content"))
        if block.get("type") == "tool_use" and block.get("id")
    }


def _claude_tool_result_ids(event: dict[str, Any]) -> set[str]:
    return {
        str(block["tool_use_id"])
        for block in _blocks((event.get("message") or {}).get("content"))
        if block.get("type") == "tool_result" and block.get("tool_use_id")
    }


def _claude_live_uuids(events: list[dict[str, Any]]) -> set[str] | None:
    """The record uuids still on this conversation's live branch, or ``None``.

    A Claude transcript is an append-only **DAG**, not a list. ``parentUuid`` names
    the record a record answers, and every retry, every ``/rewind``, and every
    resend after a failed turn appends a *new sibling* under the same parent rather
    than replacing anything. The abandoned attempt stays in the file forever. Read
    in file order - which is what every reader here did before this function
    existed - a session that was resent eight times through an outage shows the
    same prompt eight times, and history indexes eight copies of it.

    The live branch is the ancestry of the newest record: the file is append-only,
    so the last record is by construction on the branch that is still being
    written, and the ancestors of that record are exactly the nodes whose subtree
    contains it.

    Ancestry alone is not the whole live set, and assuming it was would be the
    worse bug. A parallel tool batch is written as assistant TU₁ → TU₂ → … and each
    ``tool_result`` is parented to *the record whose call it answers*, so every
    result but the last is a sibling hanging off an ancestor rather than a link in
    the chain. Subagent (``isSidechain``) turns hang off their spawning record the
    same way. Both are live conversation, so both are walked back in below -
    matched by tool-use id for results, so an abandoned branch's own results can
    never be adopted by a live parent.

    ``None`` when no record carries a uuid, which is how a dialect or a transcript
    old enough to predate the field says it cannot answer the question. Callers
    treat that as "every record is live", which is the behaviour that predates this.

    Bounded reads are supported without a special case, and degrade in the safe
    direction. Whatever window it is handed, the newest record *in that window* is
    its leaf: for a whole file or a trailing slice that is the real leaf, and for a
    head slice it resolves every branch that closes inside the window and simply
    fails to notice one that does not. Failing to mark is a record shown that could
    have been folded; the opposite would be conversation hidden from its reader.
    """
    nodes: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for event in events:
        identifier = event.get("uuid")
        if isinstance(identifier, str) and identifier:
            nodes[identifier] = event
            order.append(identifier)
    if not order:
        return None
    live: set[str] = set()
    cursor: Any = order[-1]
    while isinstance(cursor, str) and cursor in nodes and cursor not in live:
        live.add(cursor)
        cursor = nodes[cursor].get("parentUuid")
    # Every tool call the live branch actually made. A parallel batch writes each
    # call in its own record, and all but the last of those records are ancestors
    # of the one that continues the chain, so the ids are all known by now.
    live_calls: set[str] = set()
    for identifier in live:
        live_calls |= _claude_tool_use_ids(nodes[identifier])
    children: dict[str, list[str]] = defaultdict(list)
    for identifier in order:
        parent = nodes[identifier].get("parentUuid")
        if isinstance(parent, str):
            children[parent].append(identifier)

    def continues_live(event: dict[str, Any]) -> bool:
        if event.get("isSidechain") is True:
            return True
        if event.get("type") == "attachment":
            return True
        return bool(_claude_tool_result_ids(event) & live_calls)

    queue = list(live)
    while queue:
        for child in children.get(queue.pop(), ()):
            if child in live or not continues_live(nodes[child]):
                continue
            live.add(child)
            queue.append(child)
    return live


# Per dialect, because "what links one record to another" is the harness's own
# record shape. A dialect absent here has no linkage this reader knows how to
# follow, so its records are all live - the behaviour that predates branch
# awareness - rather than classified by a rule nobody measured on it.
_LIVE_BRANCH_READERS: dict[str, Callable[[list[dict[str, Any]]], set[str] | None]] = {
    "claude": _claude_live_uuids,
}


def _mark_abandoned_records(events: list[dict[str, Any]], backend: str) -> int:
    """Stamp every off-live-branch record in ``events`` and return how many.

    Mutates the records in place, which is safe because they are decoded fresh by
    the reader that produced them and never shared with the native file.
    """
    reader = _LIVE_BRANCH_READERS.get(transcript_dialect(backend) or "")
    if reader is None:
        return 0
    live = reader(events)
    if live is None:
        return 0
    abandoned = 0
    for event in events:
        identifier = event.get("uuid")
        if isinstance(identifier, str) and identifier and identifier not in live:
            event[_ABANDONED_KEY] = True
            abandoned += 1
    if abandoned:
        log.debug(
            "transcript branch records off live path backend=%s records=%d of %d",
            backend,
            abandoned,
            len(events),
        )
    return abandoned


def read_transcript_events(path: Path, max_bytes: int | None = None) -> list[dict[str, Any]]:
    """Every JSON object record in the file, or in its trailing ``max_bytes``.

    Shared by the indexing parse and the human-facing conversation view so the
    two can never disagree about which bytes a transcript is made of.
    """
    events: list[dict[str, Any]] = []
    with path.open("rb") as handle:
        size = handle.seek(0, 2)
        start = 0 if max_bytes is None else max(0, size - max_bytes)
        handle.seek(start)
        if start:
            # The bounded tail normally begins inside a record. Discard it, as
            # before, then retain the absolute byte offset of each complete line.
            handle.readline()
        while line := handle.readline():
            offset = handle.tell() - len(line)
            try:
                event = json.loads(line.decode("utf-8", "replace"))
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict):
                # Native ids are not guaranteed on every provider/version. A
                # byte offset is unique and stable for an append-only run. It
                # leaves this module twice and only twice: as the opaque reader
                # id below, and as a `CutPoint` span for the fork writer.
                event[_SOURCE_OFFSET_KEY] = offset
                event[_SOURCE_END_KEY] = handle.tell()
                events.append(event)
    return events


def _file_event_page(
    path: Path,
    *,
    direction: str,
    anchor: int | None,
    max_bytes: int,
) -> tuple[list[dict[str, Any]], bool, int]:
    """Read one raw-record window from either edge of an append-only file."""
    size = path.stat().st_size
    with path.open("rb") as handle:
        if direction == "head":
            start = max(0, min(int(anchor or 0), size))
            handle.seek(start)
            data = handle.read(max_bytes + 1)
            if len(data) > max_bytes:
                cut = data.rfind(b"\n", 0, max_bytes + 1)
                if cut < 0:
                    # A single oversized native record is not useful to a bounded
                    # reader. Skip it without retaining its body in the result.
                    handle.seek(start)
                    handle.readline()
                    end = handle.tell()
                    data = b""
                else:
                    end = start + cut + 1
                    data = data[: cut + 1]
            else:
                end = start + len(data)
            window_start = start
            has_more = end < size
            next_boundary = end
        else:
            end = max(0, min(int(anchor if anchor is not None else size), size))
            start = max(0, end - max_bytes)
            handle.seek(start)
            data = handle.read(end - start)
            if start:
                split = data.find(b"\n")
                if split < 0:
                    return [], start > 0, start
                data = data[split + 1 :]
                start += split + 1
            window_start = start
            has_more = start > 0
            next_boundary = start

    events: list[dict[str, Any]] = []
    offset = window_start
    for line in data.splitlines(keepends=True):
        line_end = offset + len(line)
        try:
            event = json.loads(line.decode("utf-8", "replace"))
        except json.JSONDecodeError:
            offset = line_end
            continue
        if isinstance(event, dict):
            event[_SOURCE_OFFSET_KEY] = offset
            event[_SOURCE_END_KEY] = line_end
            events.append(event)
        offset = line_end
    return events, has_more, next_boundary


def _page_events(
    path: Path,
    backend: str,
    *,
    direction: str,
    anchor: int | None,
    max_bytes: int,
) -> tuple[list[dict[str, Any]], bool, int]:
    """``_file_event_page`` with this window's off-live-branch records marked.

    The branch test runs per window rather than per file on purpose: a paging
    reader must answer from the bytes it read, and reading the whole conversation
    to classify one page would defeat the paging.
    """
    events, has_more, boundary = _file_event_page(
        path, direction=direction, anchor=anchor, max_bytes=max_bytes
    )
    _mark_abandoned_records(events, backend)
    return events, has_more, boundary


def _raw_message_text(content: Any) -> str:
    return "\n".join(
        str(block.get("text") or "")
        for block in _blocks(content)
        if block.get("type") in {"text", "input_text", "output_text"}
        and block.get("text")
    ).strip()


def _meta_record(event: dict[str, Any], backend: str) -> dict[str, Any] | None:
    """A human-readable system/meta record, excluding tool traffic."""
    dialect = transcript_dialect(backend)
    if dialect is None:
        return None
    role = "meta"
    text = ""
    if dialect == "claude":
        native = event.get("message") or {}
        native_role = str(native.get("role") or event.get("type") or "")
        if native_role in {"system", "developer"}:
            role = "system"
            text = _raw_message_text(native.get("content"))
        elif event.get("isMeta") is True or event.get("interruptedMessageId") is not None:
            text = _raw_message_text(native.get("content"))
        elif isinstance(event.get("origin"), dict) and event["origin"].get("kind") != "human":
            text = _raw_message_text(native.get("content"))
    elif dialect == "codex":
        payload = event.get("payload") or {}
        if event.get("type") == "response_item" and payload.get("type") == "message":
            native_role = str(payload.get("role") or "")
            if native_role in {"system", "developer"}:
                role = "system"
                text = _raw_message_text(payload.get("content"))
        elif event.get("type") == "event_msg":
            candidate = payload.get("message") or payload.get("text") or payload.get("content")
            text = _raw_message_text(candidate)
            if not text and isinstance(candidate, str):
                text = candidate.strip()
            if text and not text.startswith(_CODEX_MACHINERY_PREFIXES):
                text = ""
    elif dialect == "pi":
        native = event.get("message") or {}
        if native.get("role") in {"system", "developer"}:
            role = "system"
            text = _raw_message_text(native.get("content"))
    elif dialect == "opencode":
        native = event.get("message") or {}
        if native.get("role") in {"system", "developer"}:
            role = "system"
            text = _raw_message_text(event.get("parts"))
    else:
        assert_never(dialect)
    if not text:
        return None
    return {
        "message_id": _record_identity(event),
        "role": role,
        "ts": event.get("timestamp"),
        "text": text,
        "_source_start": event.get(_SOURCE_OFFSET_KEY),
        "_source_end": event.get(_SOURCE_END_KEY),
        "_source_time": event.get("__swe_mux_page_time"),
        "_source_id": event.get("__swe_mux_page_id"),
    }


def _page_records(
    events: list[dict[str, Any]], backend: str, *, include_system: bool
) -> tuple[list[dict[str, Any]], int]:
    """``(records, abandoned_count)`` for one page window.

    The paged reader serves machines - the MCP transcript surface and the
    assistant - so it projects the live branch and reports the size of what it
    left out, rather than marking records the way the human reader does.
    """
    records: list[dict[str, Any]] = []
    abandoned = 0
    dialect = transcript_dialect(backend)
    if dialect is None:
        return records, abandoned
    codex_response_messages = dialect == "codex" and any(
        event.get("type") == "response_item"
        and (event.get("payload") or {}).get("type") == "message"
        and (event.get("payload") or {}).get("role") in {"user", "assistant"}
        for event in events
    )
    for event in events:
        message = _native_conversation_message(event, backend)
        text = _message_text(message.get("content")) if message is not None else ""
        if event.get(_ABANDONED_KEY):
            # Counted as messages rather than records: the caller is told how much
            # conversation it is not seeing, not how many attachments and tool
            # results the abandoned branch happened to carry with it.
            if text:
                abandoned += 1
            continue
        machinery = False
        if message is not None and message.get("role") == "user":
            if dialect == "claude":
                machinery = _claude_user_is_machinery(event, text)
            elif dialect == "codex":
                machinery = text.startswith(_CODEX_MACHINERY_PREFIXES)
            elif dialect == "pi":
                machinery = False
            elif dialect == "opencode":
                machinery = False
            else:
                assert_never(dialect)
        duplicate = bool(
            dialect == "codex"
            and codex_response_messages
            and event.get("type") != "response_item"
        )
        if (
            message is not None
            and text
            and not machinery
            and not duplicate
            and not (
                message.get("role") == "assistant"
                and text.casefold() in _ASSISTANT_ACKNOWLEDGEMENTS
            )
        ):
            records.append(
                {
                    "message_id": _record_identity(event),
                    "role": message["role"],
                    "ts": message.get("ts"),
                    "text": text,
                    "_source_start": event.get(_SOURCE_OFFSET_KEY),
                    "_source_end": event.get(_SOURCE_END_KEY),
                    "_source_time": event.get("__swe_mux_page_time"),
                    "_source_id": event.get("__swe_mux_page_id"),
                }
            )
        elif include_system and not duplicate:
            meta = _meta_record(event, backend)
            if meta is not None:
                records.append(meta)
    return records, abandoned


def transcript_message_page(
    path: Path | None,
    backend: str,
    *,
    direction: str,
    anchor: dict[str, Any] | None,
    max_bytes: int,
    max_messages: int,
    include_system: bool = False,
    native_id: str | None = None,
) -> dict[str, Any]:
    """A bounded, stable page of readable records from one native conversation.

    ``abandoned_messages`` is how many messages in this window belonged to a
    branch the conversation left and are therefore not in ``messages``. Reported
    rather than merely omitted: a page whose count is nine when it holds two
    messages is the reader's evidence that the conversation was retried, not that
    the page is broken.
    """
    if direction not in {"head", "tail"}:
        raise ValueError("transcript direction must be head or tail")
    store = conversation_store_path(backend)
    if store is not None:
        store_anchor = None
        if anchor is not None:
            if anchor.get("kind") != "store":
                raise ValueError("transcript cursor does not match this conversation store")
            store_anchor = (int(anchor["time"]), str(anchor["id"]))
        events, has_more = conversation_record_page(
            store,
            native_id or "",
            direction=direction,
            anchor=store_anchor,
            limit=TAIL_MESSAGE_LIMIT,
        )
        boundary: dict[str, Any] | None = None
    else:
        if path is None:
            return {
                "messages": [],
                "next_anchor": None,
                "has_more": False,
                "abandoned_messages": 0,
            }
        file_anchor = None
        if anchor is not None:
            if anchor.get("kind") != "file":
                raise ValueError("transcript cursor does not match this conversation file")
            file_anchor = int(anchor["offset"])
        events, has_more, offset = _page_events(
            path,
            backend,
            direction=direction,
            anchor=file_anchor,
            max_bytes=max_bytes,
        )
        boundary = {"kind": "file", "offset": offset}

    records, abandoned = _page_records(events, backend, include_system=include_system)
    selected = records[:max_messages] if direction == "head" else records[-max_messages:]
    trimmed = len(selected) < len(records)
    if store is not None:
        edge = selected[-1] if direction == "head" and selected else None
        if direction == "tail" and selected:
            edge = selected[0]
        if edge is None and events:
            edge_event = events[-1] if direction == "head" else events[0]
            boundary = {
                "kind": "store",
                "time": int(edge_event.get("__swe_mux_page_time") or 0),
                "id": str(edge_event.get("__swe_mux_page_id") or ""),
            }
        elif edge is not None:
            boundary = {
                "kind": "store",
                "time": int(edge.get("_source_time") or 0),
                "id": str(edge.get("_source_id") or ""),
            }
    elif trimmed and selected:
        edge = selected[-1] if direction == "head" else selected[0]
        boundary = {
            "kind": "file",
            "offset": int(
                (
                    edge.get("_source_end")
                    if direction == "head"
                    else edge.get("_source_start")
                )
                or 0
            ),
        }
    more = bool(trimmed or has_more)
    return {
        "messages": [
            {key: value for key, value in record.items() if not key.startswith("_source_")}
            for record in selected
        ],
        "next_anchor": boundary if more else None,
        "has_more": more,
        "abandoned_messages": abandoned,
    }


def conversation_is_readable(
    path: Path | None, backend: str, native_id: str | None = None
) -> bool:
    """Whether this conversation can be read right now.

    The one predicate every caller that used to write ``path.is_file()`` should ask
    instead. A store-backed harness has no path to test, so the file test answered
    "nothing to read" for every opencode session and no Transcript tab, copy-reply,
    or history reindex ever ran for one.

    For a store the test is that the conversation is actually in it, which also
    rejects the placeholder id a session carries before its CLI reports a real one.
    """
    store = conversation_store_path(backend)
    if store is not None:
        return bool(native_id) and store_watermark(store, native_id or "") is not None
    return path is not None and path.is_file()


def conversation_events(
    path: Path | None,
    backend: str,
    *,
    max_bytes: int | None = None,
    native_id: str | None = None,
) -> list[dict[str, Any]]:
    """Records for one conversation, however this harness happens to store it.

    The single reading boundary. A file-backed harness reads lines from ``path``; a
    store-backed one reads rows keyed by ``native_id`` and never looks at ``path``,
    which is ``None`` for it because there is no file to name.

    Both produce the same record stream, so every consumer above this line stays
    identical. That is the whole point: opencode had no Transcript tab, no history
    search over its replies, and no conversational time metadata purely because the
    readers took a path, and adding a second pipeline would have doubled the
    surface every future harness has to satisfy.
    """
    store = conversation_store_path(backend)
    if store is not None:
        # A bounded tail is expressed in messages here. A database conversation has
        # no byte offsets to seek to, and translating a byte budget into rows would
        # be inventing a number rather than honouring one.
        limit = None if max_bytes is None else TAIL_MESSAGE_LIMIT
        return conversation_records(store, native_id or "", max_messages=limit)
    if path is None:
        return []
    events = read_transcript_events(path, max_bytes)
    _mark_abandoned_records(events, backend)
    return events


def parse_transcript(
    path: Path | None,
    backend: str,
    *,
    max_bytes: int | None = None,
    native_id: str | None = None,
) -> list[dict[str, Any]]:
    """The conversation this transcript *is*, as a flat message list.

    The indexing and machine-reading projection: everything downstream of this
    treats what it gets as things that were said. Records the conversation
    abandoned were never said to anybody - the provider was never sent them, and
    the CLI stops showing them the moment they are branched away from - so they
    are dropped here rather than annotated. Keeping them is what put eight copies
    of one outage-retried prompt into history search
    (see :func:`_claude_live_uuids`). The human-facing reader
    (:func:`conversation_view`) keeps and marks them instead, because a person
    looking at a conversation is entitled to see that a branch happened.
    """
    return _parse_transcript_counted(
        path, backend, max_bytes=max_bytes, native_id=native_id
    )[0]


def _parse_transcript_counted(
    path: Path | None,
    backend: str,
    *,
    max_bytes: int | None = None,
    native_id: str | None = None,
) -> tuple[list[dict[str, Any]], int]:
    """``parse_transcript`` plus how many messages it dropped as abandoned.

    The count exists because the drop is otherwise unobservable downstream: a
    reader handed the live conversation cannot tell a session that ran once from
    one that was retried eight times through an outage, and a surface that says
    "nine messages are not shown" is the difference between a fix and a
    disappearance.
    """
    messages: list[dict[str, Any]] = []
    abandoned = 0
    events = conversation_events(path, backend, max_bytes=max_bytes, native_id=native_id)
    codex_response_messages = backend == "codex" and any(
        event.get("type") == "response_item"
        and (event.get("payload") or {}).get("type") == "message"
        and (event.get("payload") or {}).get("role") in {"user", "assistant"}
        for event in events
    )
    dialect = transcript_dialect(backend)
    for event in events:
        if event.get(_ABANDONED_KEY):
            if _native_conversation_message(event, backend) is not None:
                abandoned += 1
            continue
        if dialect == "claude" or dialect == "pi":
            # Both dialects put the whole message in one record, so the shared
            # extractor is the entire parse. Only codex splits tool calls out.
            if message := _native_conversation_message(event, backend):
                messages.append(message)
        elif dialect == "codex":
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
                                "id": payload.get("call_id") or payload.get("id"),
                                "name": payload.get("name") or "tool",
                                "input": _tool_input(
                                    payload.get("arguments") or payload.get("input")
                                ),
                            }
                        ],
                    }
                )
        elif dialect == "opencode":
            # One record is one message, with its tool calls already inside it, so
            # the shared extractor is the entire parse.
            if message := _native_conversation_message(event, backend):
                messages.append(message)
        elif dialect is None:
            # No parseable records exist for this backend (a shell).
            break
        else:
            assert_never(dialect)
    return messages, abandoned


@dataclass(frozen=True, slots=True)
class ParsedConversation:
    """One parse of a conversation, and what that parse is valid for.

    ``abandoned`` is how many conversational messages the parse dropped because
    the conversation had branched away from them. It travels with the messages
    rather than being recoverable from them, since a dropped message leaves
    nothing behind to count.
    """

    messages: list[dict[str, Any]]
    abandoned: int
    mtime_ns: int
    size: int


_CACHE_MAX = 32
_cache: OrderedDict[tuple[str, int, int, str, int | None], tuple[list[dict[str, Any]], int]] = (
    OrderedDict()
)
_cache_lock = threading.Lock()


def parse_transcript_cached(
    path: Path | None,
    backend: str,
    *,
    max_bytes: int | None = None,
    native_id: str | None = None,
) -> list[dict[str, Any]]:
    """Cached ``parse_transcript`` keyed on (path, mtime_ns, size, backend, max_bytes).

    The same transcript is parsed several times per turn (fleet claim-check,
    titler/summarizer observers, the history transcript view). This bounded LRU
    collapses the repeated whole-file read + per-line JSON parse into one.

    Safe to call from both the event loop and ``asyncio.to_thread`` workers: a
    single lock guards the map, but the parse itself runs OUTSIDE the lock so
    parses of distinct files never serialize. ``path.stat()`` raising ``OSError``
    on a missing/locked file propagates exactly as ``parse_transcript``'s read
    would; the cache never swallows it. The returned list is SHARED across
    callers and MUST be treated as read-only.

    Size is part of the key, not just mtime: Windows mtime granularity is a timer
    tick, so a transcript appended twice inside one tick keeps the same
    ``st_mtime_ns`` and a mtime-only key would serve the pre-append parse.
    """
    return parse_transcript_with_watermark(
        path, backend, max_bytes=max_bytes, native_id=native_id
    ).messages


def conversation_watermark(
    path: Path | None, backend: str, native_id: str | None = None
) -> tuple[str, int, int]:
    """``(identity, first, second)``: what a parse of this conversation is valid for.

    For a file that is its path plus ``(mtime_ns, size)``. For a store-backed
    conversation it is the database and conversation id plus opencode's own
    ``(time_updated, message_count)``.

    The store pair is deliberately not a file stat. The database file's mtime
    describes every conversation in it at once, and Windows freezes an open file's
    mtime at creation, so neither could tell whether *this* conversation changed.
    Both halves of opencode's pair are needed: ``time_updated`` alone misses a
    second message written inside the same millisecond, and the count alone misses
    an edit to a row, which a completing streamed message is.

    A conversation the store does not hold answers ``(identity, 0, 0)``, which
    behaves as an empty conversation rather than as an error.
    """
    store = conversation_store_path(backend)
    if store is not None:
        pair = store_watermark(store, native_id or "")
        return f"{store}#{native_id or ''}", *(pair or (0, 0))
    if path is None:
        return f"{backend}#none", 0, 0
    stat = path.stat()
    return str(path), stat.st_mtime_ns, stat.st_size


def parse_transcript_with_watermark(
    path: Path | None,
    backend: str,
    *,
    max_bytes: int | None = None,
    native_id: str | None = None,
) -> ParsedConversation:
    """``parse_transcript_cached`` plus the watermark it is valid for.

    The watermark is stamped *before* the parse, so it can only describe the same
    content or less than the returned messages. A caller that persists it (the
    message index) therefore errs toward re-indexing after a concurrent write
    rather than recording "indexed up to here" for content it never saw — a
    poisoned watermark is trusted by every later ``unchanged`` check, so the
    conservative direction is the only safe one.
    """
    identity, first, second = conversation_watermark(path, backend, native_id)
    key = (identity, first, second, backend, max_bytes)
    with _cache_lock:
        hit = _cache.get(key)
        if hit is not None:
            _cache.move_to_end(key)
            return ParsedConversation(hit[0], hit[1], first, second)
    messages, abandoned = _parse_transcript_counted(
        path, backend, max_bytes=max_bytes, native_id=native_id
    )
    with _cache_lock:
        _cache[key] = (messages, abandoned)
        _cache.move_to_end(key)
        while len(_cache) > _CACHE_MAX:
            _cache.popitem(last=False)
    return ParsedConversation(messages, abandoned, first, second)


# --------------------------------------------------------------------------
# Conversation view
#
# What was *said*, for a human to read and copy: the user's turns and the
# agent's replies, with tool calls and CLI machinery removed. A separate
# reduction from `searchable_transcript_messages` because the two answer
# opposite questions — search wants recall over everything text-shaped, a
# reading column wants only the conversation.
#
# Tool calls are not shown, but they are not forgotten either: they are counted,
# because tool activity is what separates one thing the agent said from the next.
# A reply arrives as narration, tool, narration, tool, conclusion, and every
# record in between is dropped here, so without an explicit count the surviving
# fragments become adjacent and nothing can tell a continuous message from two
# unrelated ones. That is what `preceding_tool_calls` carries.
#
# The other hard part is that both CLIs write their own machinery into the
# transcript as `user` records: slash-command expansions, skill bodies, shell
# escapes, interrupt markers, environment blocks. Rendered verbatim those bury
# the handful of things the human actually typed.
#
# **Every rule below fails open.** A record is hidden only on positive evidence
# that it is machinery; an unrecognised shape is shown. Leaking a
# `<local-command-stdout>` into the column is a blemish, whereas hiding a
# message the user wrote is the surface lying about the conversation, and a
# CLI that renames a field must not be able to silently empty the view.

CONVERSATION_MAX_BYTES = 64 * 1024 * 1024
CONVERSATION_DEFAULT_LIMIT = 200
CONVERSATION_MAX_LIMIT = 1000

# Claude appends these to a genuine prompt rather than replacing it, so they are
# stripped from the text instead of hiding the message that carries them.
_SYSTEM_REMINDER = re.compile(r"<system-reminder>.*?</system-reminder>", re.DOTALL)

# Wrapper tags Claude writes as `user` records. Only consulted when the record
# carries no `origin` (older CLI builds), since `origin.kind` is authoritative.
_CLAUDE_MACHINERY_PREFIXES = (
    "<command-name>",
    "<command-message>",
    "<command-args>",
    "<local-command-stdout>",
    "<local-command-stderr>",
    "<local-command-caveat>",
    "<bash-input>",
    "<bash-stdout>",
    "<bash-stderr>",
    "<task-notification>",
    "<system-reminder>",
    "[Request interrupted",
)

# Codex has no per-record provenance, so its machinery is recognised by shape.
# `<environment_context>` is the harness reporting cwd/shell/date; `<skill>` is a
# skill body injected mid-conversation. The opening `# AGENTS.md instructions`
# block is deliberately NOT here: it is the instruction set the run was given,
# and seeing it at the top of the conversation is wanted.
_CODEX_MACHINERY_PREFIXES = ("<environment_context>", "<skill>")

# Codex names a tool invocation in the payload type. Kept in step with the
# `function_call`/`custom_tool_call` pair `parse_transcript` synthesizes blocks
# for, so the two reductions cannot disagree about what a tool call is.
_CODEX_TOOL_CALL_TYPES = frozenset({"function_call", "custom_tool_call"})

# A provider control operation appends a synthetic assistant record after the
# real turn. It is not something the agent said: left in place it is the last
# thing the reader sees and the tail of every copied reply.
_ASSISTANT_ACKNOWLEDGEMENTS = frozenset({"no response requested."})


def _tool_call_details(event: dict[str, Any], backend: str) -> list[dict[str, Any]]:
    """Tool names and inputs this record carries, without results or telemetry.

    Native call ids are retained when available. The record identity plus block
    position is the stable fallback for UI keys. Unknown shapes return no detail
    rather than inventing metadata.
    """
    dialect = transcript_dialect(backend)
    native: list[dict[str, Any]] = []
    if dialect == "claude":
        if event.get("type") != "assistant" or event.get("isSidechain") is True:
            return []
        native = [
            block
            for block in _blocks((event.get("message") or {}).get("content"))
            if block.get("type") == "tool_use"
        ]
    elif dialect == "pi":
        if event.get("type") != "message":
            return []
        message = event.get("message") or {}
        if message.get("role") != "assistant":
            return []
        native = [
            block for block in _blocks(message.get("content")) if block.get("type") == "toolCall"
        ]
    elif dialect == "codex":
        if event.get("type") != "response_item":
            return []
        payload = event.get("payload") or {}
        if payload.get("type") not in _CODEX_TOOL_CALL_TYPES:
            return []
        native = [payload]
    elif dialect == "opencode":
        if event.get("type") != "message":
            return []
        if (event.get("message") or {}).get("role") != "assistant":
            return []
        native = [part for part in _blocks(event.get("parts")) if part.get("type") == "tool"]
    elif dialect is None:
        return []
    else:
        assert_never(dialect)

    details: list[dict[str, Any]] = []
    record_id = _record_identity(event)
    for index, item in enumerate(native):
        state = item.get("state")
        name = str(item.get("name") or item.get("tool") or "").strip()
        if not name:
            continue
        native_id = item.get("id") or item.get("call_id") or item.get("callID")
        tool_input = (
            state.get("input")
            if isinstance(state, dict)
            else item.get("input", item.get("arguments"))
        )
        details.append(
            {
                "id": str(native_id or f"{record_id}:tool:{index}"),
                "name": name,
                "input": _tool_input(tool_input),
            }
        )
    return details


def _message_text(blocks: Any) -> str:
    """The text a human would have read, with Claude's reminder spans removed."""
    parts = [
        str(block.get("text") or "")
        for block in blocks or []
        if isinstance(block, dict) and block.get("type") == "text" and block.get("text")
    ]
    return _SYSTEM_REMINDER.sub("", "\n".join(parts)).strip()


def _claude_user_is_machinery(event: dict[str, Any], text: str) -> bool:
    """Whether a Claude ``user`` record is CLI machinery rather than a human turn.

    Claude stamps provenance on the record itself: a typed prompt carries
    ``origin: {"kind": "human"}`` alongside ``promptSource``, while the CLI's own
    injections either carry a different ``origin.kind`` (``task-notification``),
    set ``isMeta`` (skill bodies, local-command caveats), or name the message
    they interrupted. Reading those fields beats matching on wrapper tags, which
    is why the tag list is only the fallback for records that predate them.
    """
    if event.get("isMeta") is True:
        return True
    if event.get("interruptedMessageId") is not None:
        return True
    origin = event.get("origin")
    if isinstance(origin, dict) and origin.get("kind"):
        return str(origin["kind"]) != "human"
    return text.startswith(_CLAUDE_MACHINERY_PREFIXES)


def _conversation_records(
    events: list[dict[str, Any]], backend: str
) -> tuple[list[dict[str, Any]], int, list[dict[str, Any]]]:
    """``(kept, hidden_count, trailing_tools)`` in native record order.

    Each kept record carries the tool names and inputs since the previous one.
    Tool activity inside a record counts as coming *after* its text, which is the
    order OMP writes it: one record holds the narration and the calls that
    narration introduces. Calls after the last message are returned separately.

    A record the conversation branched away from is kept and marked ``abandoned``
    rather than dropped. This is the one projection a person reads directly, and a
    branch is something that happened to their conversation: silently deleting the
    seven identical prompts an outage produced would leave them unable to tell a
    retry storm from a transcript the reader is mangling. The indexing projection
    (:func:`parse_transcript`) drops them, because nothing downstream of it is a
    reader. Tool calls never cross the boundary in either direction: an abandoned
    turn's calls belong to the branch that made them, so they are discarded rather
    than credited to whichever live message happens to follow.
    """
    kept: list[dict[str, Any]] = []
    hidden = 0
    pending_tools: list[dict[str, Any]] = []
    pending_abandoned = False
    dialect = transcript_dialect(backend)
    # Same precedence rule the indexing parse uses: when a Codex rollout carries
    # current `response_item/message` records, its legacy `event_msg` copies are
    # duplicates of them.
    codex_response_messages = backend == "codex" and any(
        event.get("type") == "response_item"
        and (event.get("payload") or {}).get("type") == "message"
        and (event.get("payload") or {}).get("role") in {"user", "assistant"}
        for event in events
    )
    for event in events:
        abandoned = bool(event.get(_ABANDONED_KEY))
        if abandoned != pending_abandoned:
            pending_tools = []
            pending_abandoned = abandoned
        tools = _tool_call_details(event, backend)
        message = _native_conversation_message(event, backend)
        if message is None:
            pending_tools.extend(tools)
            continue
        if backend == "codex" and codex_response_messages and event.get("type") != "response_item":
            pending_tools.extend(tools)
            continue
        text = _message_text(message.get("content"))
        if not text:
            if not tools:
                hidden += 1
            pending_tools.extend(tools)
            continue
        if message["role"] == "user":
            # Each dialect hides a different kind of non-user record. Codex's
            # prefix test used to be the `else` for everything that was not
            # Claude, so it was silently applied to the pi dialect too — a pi or
            # omp user message that happened to start with a Codex machinery
            # prefix would vanish from the transcript. The pi dialect wraps no
            # machinery into user records at all, so it tests nothing.
            if dialect == "claude":
                machinery = _claude_user_is_machinery(event, text)
            elif dialect == "codex":
                machinery = text.startswith(_CODEX_MACHINERY_PREFIXES)
            else:
                machinery = False
            if machinery:
                hidden += 1
                pending_tools.extend(tools)
                continue
        elif text.casefold() in _ASSISTANT_ACKNOWLEDGEMENTS:
            hidden += 1
            pending_tools.extend(tools)
            continue
        record = {
            "message_id": _record_identity(event),
            "role": message["role"],
            "ts": message.get("ts"),
            "text": text,
            "preceding_tool_calls": len(pending_tools),
            "preceding_tools": pending_tools,
            # The byte span this message occupies in a file-backed transcript,
            # `None` for a store-backed one. Only `conversation_cut_points`
            # keeps them; the reader payload strips them, because a span is a
            # writer's coordinate rather than something to render.
            _SOURCE_OFFSET_KEY: event.get(_SOURCE_OFFSET_KEY),
            _SOURCE_END_KEY: event.get(_SOURCE_END_KEY),
        }
        if abandoned:
            record["abandoned"] = True
        kept.append(record)
        pending_tools = tools
    # Calls left over from an abandoned branch trail nothing a reader is shown.
    return kept, hidden, [] if pending_abandoned else pending_tools


def _record_identity(event: dict[str, Any]) -> str:
    """A stable id for one record, for state that must survive appends.

    A file-backed record is identified by its byte offset, which is unique and
    stable for an append-only run. A store-backed record carries the harness's own
    primary key, which is strictly better: it survives compaction and rewriting,
    neither of which an offset does. Falling back to the offset keeps every existing
    id byte-identical, so persisted references do not move.
    """
    offset = event.get(_SOURCE_OFFSET_KEY)
    if offset is not None:
        return f"offset:{offset}"
    identifier = event.get("id")
    return f"record:{identifier}" if identifier else "record:unknown"


def _merge_assistant_segments(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Fold each *segment* of an agent's turn into one message.

    A provider splits a reply across several records for two unrelated reasons,
    and they need opposite treatment. Streaming splits one continuous message on
    no boundary at all, and stitching those back together is required — half a
    sentence is never what the reader wanted. A tool call splits a turn on a real
    boundary: the narration that introduces a tool is a different thing from the
    conclusion that follows it, and gluing the two together is what made the copy
    button hand back "I'll investigate…" on top of the answer.

    So records merge only across the first kind of split, which is exactly the
    records with no tool calls in front of them. The timestamp kept is the
    fragment that started the segment.

    A branch boundary is a third kind of split and never merges: an abandoned
    fragment and the live reply that replaced it are two different attempts at the
    same turn, and gluing them together would read as one self-contradicting
    message with no way to see the seam.
    """
    merged: list[dict[str, Any]] = []
    for record in records:
        previous = merged[-1] if merged else None
        if (
            previous
            and previous["role"] == "assistant"
            and record["role"] == "assistant"
            and not record["preceding_tool_calls"]
            and bool(previous.get("abandoned")) == bool(record.get("abandoned"))
        ):
            previous["text"] = f"{previous['text']}\n\n{record['text']}"
            # The span grows with the text. A fork cutting "after this reply" has to
            # land past the last fragment folded in here; cutting at the first
            # fragment's end would truncate the reply the reader was shown.
            previous[_SOURCE_END_KEY] = record[_SOURCE_END_KEY]
            continue
        merged.append(dict(record))
    return merged


def conversation_view(
    path: Path | None,
    backend: str,
    *,
    limit: int = CONVERSATION_DEFAULT_LIMIT,
    native_id: str | None = None,
) -> dict[str, Any]:
    """The readable conversation in a transcript, newest ``limit`` messages.

    ``truncated`` covers both bounds that can drop older messages: the message
    limit, and the byte cap that keeps one pathological transcript (a 550 MB
    Codex rollout exists in the wild) from stalling a request. Ordinals number
    the returned window for display. ``message_id`` is the stable identity for
    state that must survive appends which move that window.

    ``preceding_tool_calls`` is how much work happened between a message and the
    one before it. Zero for everything a human typed and for the first message of
    a turn; non-zero wherever a reply resumed after tool use. It is the count of
    what is *not* shown, in the same spirit as ``hidden``: a reader is never left
    to infer that two paragraphs written twenty tool calls apart were one thought.

    ``abandoned`` marks a message the conversation branched away from, and
    ``abandoned_messages`` counts them in the returned window. They are ordinary
    messages in every other respect, including their ordinals, because the reader
    folds them rather than removing them.
    """
    # The byte cap applies to a file; a store-backed conversation is bounded by the
    # message limit the reader already applies, so it asks for the whole record set
    # and lets the window below do the trimming.
    size = path.stat().st_size if path is not None else 0
    max_bytes = CONVERSATION_MAX_BYTES if size > CONVERSATION_MAX_BYTES else None
    records, hidden, trailing_tools = _conversation_records(
        conversation_events(path, backend, max_bytes=max_bytes, native_id=native_id), backend
    )
    messages = _merge_assistant_segments(records)
    truncated = max_bytes is not None
    if len(messages) > limit:
        messages = messages[-limit:]
        truncated = True
    return {
        "messages": [
            {"ordinal": index, **_without_source_span(message)}
            for index, message in enumerate(messages)
        ],
        "trailing_tool_calls": trailing_tools,
        "hidden": hidden,
        "abandoned_messages": sum(1 for message in messages if message.get("abandoned")),
        "truncated": truncated,
    }


def _without_source_span(message: dict[str, Any]) -> dict[str, Any]:
    """One reader message with its byte span removed."""
    return {
        key: value
        for key, value in message.items()
        if key not in {_SOURCE_OFFSET_KEY, _SOURCE_END_KEY}
    }


@dataclass(frozen=True, slots=True)
class CutPoint:
    """One message a fork could be cut at, and what cutting there would leave.

    ``source_end`` is the byte offset one past the last record this message
    occupies, which is the only offset a fork is ever cut at: cutting on a record
    boundary the reader can name is what keeps a fork's leaf a real conversational
    record rather than whichever housekeeping line happened to follow it.

    ``open_tool_calls`` is how many tool invocations are still unanswered at that
    boundary. Non-zero means the cut is illegal rather than merely untidy: a
    conversation whose last assistant turn asked for a tool and never received the
    result is rejected by the provider outright, so a fork made there would not
    load at all.
    """

    message_id: str
    ordinal: int
    role: str
    ts: Any
    source_start: int
    source_end: int
    open_tool_calls: int


def conversation_cut_points(
    path: Path | None,
    backend: str,
    *,
    limit: int = CONVERSATION_DEFAULT_LIMIT,
    native_id: str | None = None,
) -> list[CutPoint] | None:
    """Where a fork of this conversation could be cut, newest ``limit`` messages.

    ``None`` when this dialect has no measured rule for what leaves a tool call
    unanswered. That is a declared absence rather than a permissive default: a
    caller that cannot tell a legal cut from an illegal one must refuse to fork,
    not guess and write a conversation the provider will reject.

    The window bounds only which cuts can be *named*, never what a fork contains.
    Offsets are absolute, so a fork cut inside a bounded tail still carries the
    whole conversation from its first byte.

    Messages on an abandoned branch are not offered. Cutting at one would in fact
    write a loadable fork - the prefix simply ends on that branch - but the picker
    exists to name a moment in the conversation, and a session resent eight times
    through an outage would name the same moment eight identical times. A reader
    who wants an abandoned branch back is asking the CLI's own resume, not mux's.
    """
    scanner = _OPEN_TOOL_SCANNERS.get(transcript_dialect(backend) or "")
    if scanner is None or path is None:
        return None
    size = path.stat().st_size
    max_bytes = CONVERSATION_MAX_BYTES if size > CONVERSATION_MAX_BYTES else None
    events = conversation_events(path, backend, max_bytes=max_bytes, native_id=native_id)
    open_calls = scanner(events)
    records, _hidden, _trailing = _conversation_records(events, backend)
    live = [record for record in records if not record.get("abandoned")]
    messages = _merge_assistant_segments(live)[-limit:]
    points: list[CutPoint] = []
    for ordinal, message in enumerate(messages):
        start = message.get(_SOURCE_OFFSET_KEY)
        end = message.get(_SOURCE_END_KEY)
        if start is None or end is None:
            continue
        points.append(
            CutPoint(
                message_id=str(message["message_id"]),
                ordinal=ordinal,
                role=str(message["role"]),
                ts=message.get("ts"),
                source_start=int(start),
                source_end=int(end),
                open_tool_calls=open_calls.get(int(end), 0),
            )
        )
    return points


def resolve_cut_offset(
    points: list[CutPoint], message_id: str, mode: str
) -> tuple[int, CutPoint] | tuple[None, str]:
    """The byte offset a fork cut at ``message_id``/``mode`` lands on, or a refusal code.

    Every cut lands on some message's end offset, never on an arbitrary byte: cutting
    *before* a message means cutting after the one that precedes it. That is what
    keeps a fork's last record a real conversational record rather than whichever
    housekeeping line the CLI happened to write next, and it is why "before the oldest
    message this window can name" is refused instead of approximated with byte zero.

    Lives with the reader rather than with the writer for the reason stated at the top
    of `transcript_fork.py`: where a cut is *legal* is a question about a transcript's
    own shape, and the writer is handed an offset. It is shared rather than duplicated
    because the interactive branch picker and a scheduled fork-and-resume must decide a
    cut identically - a schedule that fired on a rule the picker would have refused is
    an unattended session opened on a conversation the provider rejects.
    """
    index = next((i for i, point in enumerate(points) if point.message_id == message_id), None)
    if index is None:
        return None, "branch_point_unknown"
    if mode == "after":
        point = points[index]
        if point.open_tool_calls:
            return None, "unanswered_tool_calls"
        return point.source_end, point
    if index == 0:
        return None, "outside_window"
    previous = points[index - 1]
    if previous.open_tool_calls:
        return None, "unanswered_tool_calls"
    return previous.source_end, points[index]


def _claude_open_tool_calls(events: list[dict[str, Any]]) -> dict[int, int]:
    """How many Claude tool calls are unanswered at each record boundary.

    A call is opened by a ``tool_use`` block in an assistant record and closed by
    the ``tool_result`` block naming it, which Claude writes as the next user
    record. Subagent records are counted in the same set on purpose: their pairs
    are self-contained, so they open and close within the span they occupy and
    never leave a boundary looking dirty that is not.

    A ``tool_result`` whose ``tool_use`` predates a bounded read closes nothing and
    is ignored, which is why discarding an unknown id is silent rather than an
    error.

    A call made on a branch the conversation abandoned is not counted, because
    nothing will ever answer it: an outage or an interrupt that lands between a
    ``tool_use`` and its result leaves that id open for the rest of the file, and
    counting it would mark *every* later boundary dirty and quietly retire
    branching for the life of the conversation. Results are still read from
    abandoned records, so a call this reader did see in a bounded window still
    closes, and the discard stays silent for the ones it did not.
    """
    open_ids: set[str] = set()
    by_offset: dict[int, int] = {}
    for event in events:
        abandoned = bool(event.get(_ABANDONED_KEY))
        for block in _blocks((event.get("message") or {}).get("content")):
            kind = block.get("type")
            if kind == "tool_result":
                open_ids.discard(str(block.get("tool_use_id") or ""))
            elif kind == "tool_use" and block.get("id") and not abandoned:
                open_ids.add(str(block["id"]))
        end = event.get(_SOURCE_END_KEY)
        if end is not None:
            by_offset[int(end)] = len(open_ids)
    return by_offset


# Per dialect, because "what leaves a tool call unanswered" is the provider's rule
# and not a shape shared across harnesses. A dialect absent here is a dialect mux
# cannot yet fork: `conversation_cut_points` returns `None` rather than assuming
# every boundary is clean.
_OPEN_TOOL_SCANNERS: dict[str, Callable[[list[dict[str, Any]]], dict[int, int]]] = {
    "claude": _claude_open_tool_calls,
}


_conversation_cache: OrderedDict[tuple[str, int, int, str, int], dict[str, Any]] = OrderedDict()
_conversation_cache_lock = threading.Lock()
_CONVERSATION_CACHE_MAX = 8


def conversation_view_cached(
    path: Path | None,
    backend: str,
    *,
    limit: int = CONVERSATION_DEFAULT_LIMIT,
    native_id: str | None = None,
) -> dict[str, Any]:
    """``conversation_view`` keyed on the conversation's watermark and the limit.

    A live agent invalidates this on every append, which is correct and is why
    the cap above exists. It pays for the other case: switching drawer tabs on
    an idle session re-requests the same view, and re-parsing a finished
    conversation to produce a byte-identical answer is pure waste. The returned
    dict is SHARED and must be treated as read-only.
    """
    identity, first, second = conversation_watermark(path, backend, native_id)
    key = (identity, first, second, backend, limit)
    with _conversation_cache_lock:
        hit = _conversation_cache.get(key)
        if hit is not None:
            _conversation_cache.move_to_end(key)
            return hit
    result = conversation_view(path, backend, limit=limit, native_id=native_id)
    with _conversation_cache_lock:
        _conversation_cache[key] = result
        _conversation_cache.move_to_end(key)
        while len(_conversation_cache) > _CONVERSATION_CACHE_MAX:
            _conversation_cache.popitem(last=False)
    return result


def final_exchange(
    path: Path | None, backend: str, *, native_id: str | None = None
) -> tuple[str, str]:
    """``(prompt, reply)``: the agent's latest reply and what it was answering.

    Deliberately the same reduction the reader tab renders, and not a second walk
    with its own idea of where a reply starts. "Copy reply copies the last agent
    message in the Transcript tab" is then the whole specification, true by
    construction rather than by two implementations agreeing, and the reader is
    where a doubt about what was copied gets settled.

    Either half is ``""`` when the conversation does not have it yet. Sharing the
    cached view is also why this is cheap enough to call on the turn boundary
    that prefetches the clipboard: the tab has usually just paid for it.
    """
    prompt = ""
    reply = ""
    view = conversation_view_cached(path, backend, native_id=native_id)
    for message in reversed(view["messages"]):
        if not reply:
            if message["role"] == "assistant":
                reply = str(message["text"])
            continue
        if message["role"] == "user":
            prompt = str(message["text"])
            break
    return prompt, reply


def final_reply_text(path: Path | None, backend: str, *, native_id: str | None = None) -> str:
    """The agent's latest reply: its newest assistant segment, or ``""``."""
    return final_exchange(path, backend, native_id=native_id)[1]


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
