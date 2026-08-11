from __future__ import annotations

import json
import re
import threading
from collections import OrderedDict
from datetime import datetime
from pathlib import Path
from typing import Any, assert_never

from .harness import conversation_store_path, transcript_dialect
from .opencode_store import TAIL_MESSAGE_LIMIT, conversation_records
from .opencode_store import conversation_watermark as store_watermark

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
        blocks = _blocks((event.get("message") or {}).get("content"))
    elif dialect == "pi":
        # oh-my-pi and upstream pi write the same message record.
        if event.get("type") != "message":
            return None
        native_message = event.get("message") or {}
        role = native_message.get("role")
        if role not in {"user", "assistant"}:
            return None
        blocks = _blocks(native_message.get("content"))
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
    if not any(block.get("type") == "text" and block.get("text") for block in blocks):
        # A turn that only ran tools is still a turn. Codex reaches the same
        # outcome by appending its `function_call` records as their own messages in
        # `parse_transcript`; opencode carries them inside the message they belong
        # to, so the message survives on its blocks instead.
        if dialect != "opencode" or not blocks:
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
    return read_transcript_events(path, max_bytes)


def parse_transcript(
    path: Path | None,
    backend: str,
    *,
    max_bytes: int | None = None,
    native_id: str | None = None,
) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    events = conversation_events(path, backend, max_bytes=max_bytes, native_id=native_id)
    codex_response_messages = backend == "codex" and any(
        event.get("type") == "response_item"
        and (event.get("payload") or {}).get("type") == "message"
        and (event.get("payload") or {}).get("role") in {"user", "assistant"}
        for event in events
    )
    dialect = transcript_dialect(backend)
    for event in events:
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
                                "name": payload.get("name") or "tool",
                                "input": payload.get("arguments") or payload.get("input"),
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
    return messages


_CACHE_MAX = 32
_cache: OrderedDict[tuple[str, int, int, str, int | None], list[dict[str, Any]]] = OrderedDict()
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
    )[0]


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
) -> tuple[list[dict[str, Any]], int, int]:
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
            return hit, first, second
    result = parse_transcript(path, backend, max_bytes=max_bytes, native_id=native_id)
    with _cache_lock:
        _cache[key] = result
        _cache.move_to_end(key)
        while len(_cache) > _CACHE_MAX:
            _cache.popitem(last=False)
    return result, first, second


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


def _tool_calls(event: dict[str, Any], backend: str) -> int:
    """How many tool invocations this record makes.

    Calls only, never their results. A result cannot occur without the call that
    produced it, so counting both would double every number, and the two are not
    reliably paired anyway once a run is interrupted mid-tool.

    Claude and OMP name the call in a content block (which OMP may put in the
    same record as the text that introduces it, so a record can both be a message
    and end a segment); Codex names it in the payload type. An unrecognised shape
    counts zero, which merges a turn exactly as it does today rather than cutting
    a reply at a boundary that is not there.
    """
    dialect = transcript_dialect(backend)
    if dialect == "claude":
        if event.get("type") != "assistant" or event.get("isSidechain") is True:
            return 0
        blocks = _blocks((event.get("message") or {}).get("content"))
        return sum(1 for block in blocks if block.get("type") == "tool_use")
    if dialect == "pi":
        if event.get("type") != "message":
            return 0
        native = event.get("message") or {}
        if native.get("role") != "assistant":
            return 0
        return sum(1 for block in _blocks(native.get("content")) if block.get("type") == "toolCall")
    if dialect == "codex":
        if event.get("type") != "response_item":
            return 0
        return 1 if (event.get("payload") or {}).get("type") in _CODEX_TOOL_CALL_TYPES else 0
    if dialect == "opencode":
        # Tool calls are parts of the message they belong to, so the count is per
        # record rather than one record per call.
        if event.get("type") != "message":
            return 0
        if (event.get("message") or {}).get("role") != "assistant":
            return 0
        return sum(1 for part in _blocks(event.get("parts")) if part.get("type") == "tool")
    if dialect is None:
        return 0
    assert_never(dialect)


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
    """``(kept, hidden_count)`` conversational text records, in file order.

    Each kept record carries the number of tool calls made since the previous one
    in ``preceding_tool_calls``. Tool activity inside a record counts as coming
    *after* its text, which is the order OMP writes it: one record holds the
    narration and the calls that narration introduces.
    """
    kept: list[dict[str, Any]] = []
    hidden = 0
    pending_tools = 0
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
        tools = _tool_calls(event, backend)
        message = _native_conversation_message(event, backend)
        if message is None:
            pending_tools += tools
            continue
        if backend == "codex" and codex_response_messages and event.get("type") != "response_item":
            pending_tools += tools
            continue
        text = _message_text(message.get("content"))
        if not text:
            hidden += 1
            pending_tools += tools
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
                pending_tools += tools
                continue
        elif text.casefold() in _ASSISTANT_ACKNOWLEDGEMENTS:
            hidden += 1
            pending_tools += tools
            continue
        kept.append(
            {
                "message_id": _record_identity(event),
                "role": message["role"],
                "ts": message.get("ts"),
                "text": text,
                "preceding_tool_calls": pending_tools,
            }
        )
        pending_tools = tools
    return kept, hidden


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
    """
    merged: list[dict[str, Any]] = []
    for record in records:
        previous = merged[-1] if merged else None
        if (
            previous
            and previous["role"] == "assistant"
            and record["role"] == "assistant"
            and not record["preceding_tool_calls"]
        ):
            previous["text"] = f"{previous['text']}\n\n{record['text']}"
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
    """
    # The byte cap applies to a file; a store-backed conversation is bounded by the
    # message limit the reader already applies, so it asks for the whole record set
    # and lets the window below do the trimming.
    size = path.stat().st_size if path is not None else 0
    max_bytes = CONVERSATION_MAX_BYTES if size > CONVERSATION_MAX_BYTES else None
    records, hidden = _conversation_records(
        conversation_events(path, backend, max_bytes=max_bytes, native_id=native_id), backend
    )
    messages = _merge_assistant_segments(records)
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
