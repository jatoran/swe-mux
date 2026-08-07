from __future__ import annotations

import json
import re
import threading
from collections import OrderedDict
from datetime import datetime
from pathlib import Path
from typing import Any

TRANSCRIPT_PARSER_VERSION = 3
_SOURCE_OFFSET_KEY = "__swe_mux_source_offset"


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


def _native_conversation_message(event: dict[str, Any], backend: str) -> dict[str, Any] | None:
    timestamp = event.get("timestamp")
    if backend == "claude":
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
        blocks = _blocks((event.get("message") or {}).get("content"))
    elif backend == "omp":
        if event.get("type") != "message":
            return None
        native_message = event.get("message") or {}
        role = native_message.get("role")
        if role not in {"user", "assistant"}:
            return None
        blocks = _blocks(native_message.get("content"))
    elif backend == "codex":
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
    else:
        return None
    if not any(block.get("type") == "text" and block.get("text") for block in blocks):
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
    path: Path, backend: str, *, head_bytes: int = 512 * 1024, tail_bytes: int = 8 * 1024 * 1024
) -> dict[str, Any]:
    """Read bounded transcript edges and return native conversational time metadata."""
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
                # byte offset is unique and stable for an append-only run, and
                # never leaves this module except as the opaque reader id below.
                event[_SOURCE_OFFSET_KEY] = offset
                events.append(event)
    return events


def parse_transcript(
    path: Path, backend: str, *, max_bytes: int | None = None
) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    events = read_transcript_events(path, max_bytes)
    codex_response_messages = backend == "codex" and any(
        event.get("type") == "response_item"
        and (event.get("payload") or {}).get("type") == "message"
        and (event.get("payload") or {}).get("role") in {"user", "assistant"}
        for event in events
    )
    for event in events:
        if backend == "claude":
            if message := _native_conversation_message(event, backend):
                messages.append(message)
        elif backend == "codex":
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
                                "name": payload.get("name") or "tool",
                                "input": payload.get("arguments") or payload.get("input"),
                            }
                        ],
                    }
                )
        elif backend == "omp":
            if message := _native_conversation_message(event, backend):
                messages.append(message)
    return messages


_CACHE_MAX = 32
_cache: OrderedDict[tuple[str, int, int, str, int | None], list[dict[str, Any]]] = OrderedDict()
_cache_lock = threading.Lock()


def parse_transcript_cached(
    path: Path, backend: str, *, max_bytes: int | None = None
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
    return parse_transcript_with_watermark(path, backend, max_bytes=max_bytes)[0]


def parse_transcript_with_watermark(
    path: Path, backend: str, *, max_bytes: int | None = None
) -> tuple[list[dict[str, Any]], int, int]:
    """``parse_transcript_cached`` plus the ``(mtime_ns, size)`` it is valid for.

    The watermark is stamped from the stat taken *before* the parse, so it can
    only describe the same bytes or fewer than the returned messages. A caller
    that persists it (the message index) therefore errs toward re-indexing after
    a concurrent append rather than recording "indexed up to here" for content it
    never saw — a poisoned watermark is trusted by every later ``unchanged``
    check, so the conservative direction is the only safe one.
    """
    stat = path.stat()
    key = (str(path), stat.st_mtime_ns, stat.st_size, backend, max_bytes)
    with _cache_lock:
        hit = _cache.get(key)
        if hit is not None:
            _cache.move_to_end(key)
            return hit, stat.st_mtime_ns, stat.st_size
    result = parse_transcript(path, backend, max_bytes=max_bytes)
    with _cache_lock:
        _cache[key] = result
        _cache.move_to_end(key)
        while len(_cache) > _CACHE_MAX:
            _cache.popitem(last=False)
    return result, stat.st_mtime_ns, stat.st_size


# --------------------------------------------------------------------------
# Conversation view
#
# What was *said*, for a human to read and copy: the user's turns and the
# agent's replies, with tool calls and CLI machinery removed. A separate
# reduction from `searchable_transcript_messages` because the two answer
# opposite questions — search wants recall over everything text-shaped, a
# reading column wants only the conversation.
#
# The hard part is not tool calls (Claude writes those as records with no text
# block, so the parse already drops them; Codex's synthesized `tool_use` blocks
# are dropped here). It is that both CLIs write their own machinery into the
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
) -> tuple[list[dict[str, Any]], int]:
    """``(kept, hidden_count)`` conversational text records, in file order."""
    kept: list[dict[str, Any]] = []
    hidden = 0
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
        message = _native_conversation_message(event, backend)
        if message is None:
            continue
        if backend == "codex" and codex_response_messages and event.get("type") != "response_item":
            continue
        text = _message_text(message.get("content"))
        if not text:
            hidden += 1
            continue
        if message["role"] == "user":
            machinery = (
                _claude_user_is_machinery(event, text)
                if backend == "claude"
                else text.startswith(_CODEX_MACHINERY_PREFIXES)
            )
            if machinery:
                hidden += 1
                continue
        kept.append(
            {
                "message_id": f"offset:{event[_SOURCE_OFFSET_KEY]}",
                "role": message["role"],
                "ts": message.get("ts"),
                "text": text,
            }
        )
    return kept, hidden


def _merge_assistant_runs(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Fold an agent's turn back into one message.

    Both CLIs split a reply across several records whenever a tool call
    interrupts it, so one answer arrives as narration, tool, narration, tool,
    conclusion. With the tool records gone those fragments are one thing the
    agent said, and copying a fragment is never what the reader wanted. The
    timestamp kept is the fragment that started the turn.
    """
    merged: list[dict[str, Any]] = []
    for record in records:
        previous = merged[-1] if merged else None
        if previous and previous["role"] == "assistant" and record["role"] == "assistant":
            previous["text"] = f"{previous['text']}\n\n{record['text']}"
            continue
        merged.append(dict(record))
    return merged


def conversation_view(
    path: Path, backend: str, *, limit: int = CONVERSATION_DEFAULT_LIMIT
) -> dict[str, Any]:
    """The readable conversation in a transcript, newest ``limit`` messages.

    ``truncated`` covers both bounds that can drop older messages: the message
    limit, and the byte cap that keeps one pathological transcript (a 550 MB
    Codex rollout exists in the wild) from stalling a request. Ordinals number
    the returned window for display. ``message_id`` is the stable identity for
    state that must survive appends which move that window.
    """
    size = path.stat().st_size
    max_bytes = CONVERSATION_MAX_BYTES if size > CONVERSATION_MAX_BYTES else None
    records, hidden = _conversation_records(read_transcript_events(path, max_bytes), backend)
    messages = _merge_assistant_runs(records)
    truncated = max_bytes is not None
    if len(messages) > limit:
        messages = messages[-limit:]
        truncated = True
    return {
        "messages": [{"ordinal": index, **message} for index, message in enumerate(messages)],
        "hidden": hidden,
        "truncated": truncated,
    }


_conversation_cache: OrderedDict[tuple[str, int, int, str, int], dict[str, Any]] = OrderedDict()
_conversation_cache_lock = threading.Lock()
_CONVERSATION_CACHE_MAX = 8


def conversation_view_cached(
    path: Path, backend: str, *, limit: int = CONVERSATION_DEFAULT_LIMIT
) -> dict[str, Any]:
    """``conversation_view`` keyed on (path, mtime_ns, size, backend, limit).

    A live agent invalidates this on every append, which is correct and is why
    the cap above exists. It pays for the other case: switching drawer tabs on
    an idle session re-requests the same view, and re-parsing a finished
    conversation to produce a byte-identical answer is pure waste. The returned
    dict is SHARED and must be treated as read-only.
    """
    stat = path.stat()
    key = (str(path), stat.st_mtime_ns, stat.st_size, backend, limit)
    with _conversation_cache_lock:
        hit = _conversation_cache.get(key)
        if hit is not None:
            _conversation_cache.move_to_end(key)
            return hit
    result = conversation_view(path, backend, limit=limit)
    with _conversation_cache_lock:
        _conversation_cache[key] = result
        _conversation_cache.move_to_end(key)
        while len(_conversation_cache) > _CONVERSATION_CACHE_MAX:
            _conversation_cache.popitem(last=False)
    return result


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
