"""One incremental reading of each conversation, shared by every human-facing reader.

The Transcript tab, the branch picker, read-aloud, and Copy reply all used to answer
their question by re-reading the native transcript and JSON-decoding every line of
it, up to a 64 MiB tail, on every request. The parse cache in front of that was keyed
on the file's watermark, and a working agent changes the watermark on every append,
so for exactly the sessions people look at it never hit: a 49 MB Claude transcript
cost up to 2.4 s per open, and two opens in nine hit the route's 5 s timeout and came
back as "unreadable".

This module keeps one index per conversation instead:

- **Decoded once, then extended.** The first read decodes the file; every later read
  decodes only the bytes appended since, from the last complete line. A partial final
  line is read provisionally and re-read once its newline lands.
- **Replacement-aware.** Before extending, the index checks that the file still
  starts and still reads, up to where it stopped, with the bytes it saw. Claude's
  cancel/revert rewrites a transcript in place and Windows leaves an open file's
  mtime frozen, so neither the size nor the timestamp is evidence of an append; a
  mismatch rebuilds from byte zero and bumps the index's ``generation``.
- **Compact.** Each record is reduced to the fields the projections read: text,
  linkage, turn markers, and tool *names and ids*. Tool inputs, which are 88-96% of
  a real conversation's view bytes, stay in the file; a tool block carries a
  reference to the record holding its input, which :func:`tool_calls` reads back on
  demand. Codex records no projection reads (reasoning, tool output, token counts)
  are recognised from their first bytes and never decoded at all.
- **Same projections, not new ones.** The live branch, the merged messages, the
  open-tool-call scan and the indexing parse are the functions in
  :mod:`transcript_view`, run over the compact records, so a full read and the index
  cannot disagree about what a conversation says.
- **Single-flight.** A per-index lock serialises refreshes, so concurrent readers of
  one conversation share one decode. The async entry point runs on a dedicated small
  executor, so a first build of a 300 MB rollout never queues behind, or starves, the
  default pool everything else uses.

Message identity is the existing ``message_id`` (``offset:N``, ``inherited:…``,
``record:…``), which is stable across appends. Positions in the merged list are the
``ordinal`` every reader now shows, so an ordinal no longer moves when the window does.

``seq`` counts changes to what a reader sees; each message carries the ``seq`` it last
changed at, which is what lets a client ask for "everything that changed since N" and
receive "replace from message X" rather than the whole conversation. ``generation``
counts the changes no delta can express - a rebuild, or a message that vanished or
moved - and a client holding an older generation is answered with a fresh tail.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import threading
import time
import uuid
from collections import OrderedDict
from collections.abc import Callable, Iterable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import codex_history
from . import transcript_view as tv
from .harness import conversation_store_path, transcript_dialect
from .opencode_store import conversation_records
from .opencode_store import conversation_watermark as store_watermark

log = logging.getLogger(__name__)

#: Bytes compared at the start of a file, and immediately before the index's read
#: position, to decide whether the file is still the one the index decoded.
SAMPLE_BYTES = 4096
#: A build or extend slower than this is a WARNING (`transcript_index_slow`).
SLOW_BUILD_SECONDS = 2.0
#: Estimated retained bytes across every index before the least recently used ones
#: are dropped. An estimate (text lengths plus a per-record overhead), not a
#: measurement: its job is to keep a fleet of long conversations bounded, not exact.
DEFAULT_BUDGET_BYTES = 192 * 1024 * 1024
MAX_INDEXES = 48
#: Fitted to `tracemalloc` on four real conversations (two Codex rollouts of 297 and
#: 85 MB, two Claude transcripts of 49 and 55 MB; 2026-10-12): 1,060-1,590 bytes per
#: kept record beyond three times its text, which the slim record, its memo and the
#: merged message each hold. Rounded up, so the estimate errs towards evicting early.
_RECORD_OVERHEAD_BYTES = 1600
_TEXT_COPIES = 3
_PROGRESS_EVERY_BYTES = 4 * 1024 * 1024

_OFFSET = tv._SOURCE_OFFSET_KEY
_END = tv._SOURCE_END_KEY
_MARKERS = (tv._ABANDONED_KEY, tv._TURN_ID_KEY, tv._TURN_COMPLETE_KEY)
_TOOL_KEYS = ("id", "tool_use_id", "name", "tool", "call_id", "callID")

#: Codex payload types a projection reads. Every other `response_item`/`event_msg`
#: payload (reasoning, tool output, token counts, exec deltas) reduces to nothing.
_CODEX_KEPT = frozenset(
    {
        "message",
        "function_call",
        "custom_tool_call",
        "user_message",
        "agent_message",
        "task_started",
        "task_complete",
        "thread_rolled_back",
    }
)
_CODEX_KINDS = frozenset({b"response_item", b"event_msg"})
#: One top-level scalar member (`"timestamp":"…"`, `"ordinal":12`). Scalars only: the
#: pattern never enters a nested value, so every key it reads is the record's own.
_SCALAR_MEMBER = rb'"[A-Za-z0-9_]+":(?:"[^"\\]*"|-?[0-9][0-9.eE+-]*|true|false|null)'
_CODEX_HEAD = re.compile(
    rb"\{(?:" + _SCALAR_MEMBER + rb",){0,8}"
    rb'"type":"([A-Za-z0-9_]+)"'
    rb"(?:,(?:" + _SCALAR_MEMBER + rb",){0,8}"
    rb'"payload":\{"type":"([A-Za-z0-9_]+)")?'
)

ToolRef = tuple[int, int, int, int]
"""``(segment, local start, local end, block index)``; ``-1`` names a Codex payload."""


class IndexBuilding(Exception):
    """A first read is still running; ``progress`` says how far it has got."""

    def __init__(self, progress: dict[str, Any]) -> None:
        super().__init__("the conversation is still being indexed")
        self.progress = progress


# --------------------------------------------------------------------------
# Record reduction


def codex_line_is_skippable(line: bytes) -> bool:
    """Whether a Codex line provably reduces to nothing, judged without decoding it.

    Codex writes ``{"timestamp":…,"ordinal":…,"type":KIND,"payload":{"type":TYPE,…}}``,
    compact (``ordinal`` arrived in 2026-10; older rollouts lack it). The pattern is
    anchored at the record's first byte and steps over top-level scalar members
    only, so what it reads is the record's own kind and its payload's own type,
    never a nested object's. A kind
    other than ``response_item``/``event_msg`` (token usage, turn context, world
    state, compaction, session metadata) or a payload type outside
    :data:`_CODEX_KEPT` is what :func:`_slim_codex` would reduce to nothing anyway.
    Anything else - a differently spaced writer, a payload whose first key is not
    ``type`` - is decoded, which is the safe direction: a skipped record that
    mattered would be conversation missing from the reader, while a decoded record
    that did not is a little wasted time.
    """
    match = _CODEX_HEAD.match(line)
    if match is None:
        return False
    kind, payload_type = match.group(1), match.group(2)
    if kind not in _CODEX_KINDS:
        return True
    if payload_type is None:
        return False
    return payload_type.decode("ascii") not in _CODEX_KEPT


def _tool_block(block: dict[str, Any], kind: str, ref: ToolRef) -> dict[str, Any]:
    slim: dict[str, Any] = {"type": kind, tv._TOOL_REF_KEY: ref}
    for key in _TOOL_KEYS:
        if key in block:
            slim[key] = block[key]
    return slim


def _slim_content(
    content: Any, tool_type: str, ref: Callable[[int], ToolRef], *, results: bool
) -> Any:
    if not isinstance(content, list):
        return content
    blocks: list[dict[str, Any]] = []
    for index, block in enumerate(content):
        if not isinstance(block, dict):
            continue
        kind = block.get("type")
        if kind in {"text", "input_text", "output_text"}:
            blocks.append({"type": kind, "text": block.get("text")})
        elif kind == tool_type:
            blocks.append(_tool_block(block, tool_type, ref(index)))
        elif results and kind == "tool_result":
            blocks.append({"type": "tool_result", "tool_use_id": block.get("tool_use_id")})
    return blocks


_CLAUDE_KEYS = (
    "type",
    "uuid",
    "parentUuid",
    "isSidechain",
    "isMeta",
    "interruptedMessageId",
    "timestamp",
)


def _slim_claude(event: dict[str, Any], ref: Callable[[int], ToolRef]) -> dict[str, Any] | None:
    slim = {key: event[key] for key in _CLAUDE_KEYS if key in event}
    origin = event.get("origin")
    if isinstance(origin, dict):
        slim["origin"] = {"kind": origin.get("kind")}
    message = event.get("message")
    if event.get("type") in {"user", "assistant"} and isinstance(message, dict):
        slim["message"] = {
            "role": message.get("role"),
            "content": _slim_content(message.get("content"), "tool_use", ref, results=True),
        }
    # A record with a uuid is a node of the conversation's DAG even when it says
    # nothing - it can be the newest record, which is the live branch's leaf.
    if not slim.get("uuid") and "message" not in slim and slim.get("type") != "attachment":
        return None
    return slim


def _slim_pi(event: dict[str, Any], ref: Callable[[int], ToolRef]) -> dict[str, Any] | None:
    if event.get("type") != "message":
        return None
    message = event.get("message") or {}
    if not isinstance(message, dict) or message.get("role") not in {"user", "assistant"}:
        return None
    return {
        "type": "message",
        "timestamp": event.get("timestamp"),
        "message": {
            "role": message.get("role"),
            "content": _slim_content(message.get("content"), "toolCall", ref, results=False),
        },
    }


def _slim_codex(event: dict[str, Any], ref: Callable[[int], ToolRef]) -> dict[str, Any] | None:
    kind = event.get("type")
    payload = event.get("payload")
    if kind not in {"response_item", "event_msg"} or not isinstance(payload, dict):
        return None
    payload_type = payload.get("type")
    if payload_type not in _CODEX_KEPT:
        return None
    slim_payload: dict[str, Any] = {"type": payload_type}
    if payload_type == "message":
        slim_payload["role"] = payload.get("role")
        slim_payload["content"] = _slim_content(
            payload.get("content"), "__none__", ref, results=False
        )
        if "phase" in payload:
            slim_payload["phase"] = payload["phase"]
    elif payload_type in tv._CODEX_TOOL_CALL_TYPES:
        for key in ("call_id", "id", "name"):
            if key in payload:
                slim_payload[key] = payload[key]
        slim_payload[tv._TOOL_REF_KEY] = ref(-1)
    else:
        for key in ("message", "text", "content", "turn_id", "num_turns", "role"):
            if key in payload:
                slim_payload[key] = payload[key]
    return {"type": kind, "timestamp": event.get("timestamp"), "payload": slim_payload}


_SLIMMERS: dict[
    str, Callable[[dict[str, Any], Callable[[int], ToolRef]], dict[str, Any] | None]
] = {
    "claude": _slim_claude,
    "pi": _slim_pi,
    "codex": _slim_codex,
}


def _text_size(value: Any) -> int:
    if isinstance(value, str):
        return len(value)
    if isinstance(value, list):
        return sum(_text_size(item) for item in value)
    if isinstance(value, dict):
        return sum(_text_size(item) for item in value.values())
    return 0


def _estimated_size(value: Any) -> int:
    """What keeping one record costs, by :data:`_RECORD_OVERHEAD_BYTES`' fit."""
    return _RECORD_OVERHEAD_BYTES + _TEXT_COPIES * _text_size(value)


# --------------------------------------------------------------------------
# Snapshots


def _signature(message: dict[str, Any]) -> tuple[Any, ...]:
    return (
        message.get("message_id"),
        message.get("role"),
        message.get("ts"),
        message.get("text"),
        message.get("preceding_tool_calls"),
        message.get("phase"),
        message.get("turn_id"),
        message.get("turn_complete"),
        bool(message.get("abandoned")),
        tuple(str(tool.get("id")) for tool in message.get("preceding_tools") or ()),
    )


def public_tool(tool: dict[str, Any]) -> dict[str, Any]:
    """A tool call as a reader payload carries it: name and id, never the input."""
    return {"id": tool.get("id"), "name": tool.get("name")}


def public_message(message: dict[str, Any], ordinal: int) -> dict[str, Any]:
    """One merged message as the reader payload carries it, numbered by ``ordinal``."""
    result: dict[str, Any] = {"ordinal": ordinal}
    for key, value in message.items():
        if key in {_OFFSET, _END}:
            continue
        if key == "preceding_tools":
            result[key] = [public_tool(tool) for tool in value]
        else:
            result[key] = value
    return result


@dataclass(frozen=True, slots=True)
class ConversationSnapshot:
    """One immutable reading of a conversation, as of one refresh.

    ``messages`` are the merged reader messages in conversation order, with their
    byte spans and tool references still attached; :func:`public_message` is what
    leaves the daemon. ``live_messages`` is the merge over the live branch alone,
    which is what a fork may be cut at, and ``open_calls`` maps a record's end
    offset to the tool calls still unanswered there (``None`` for a dialect with no
    measured rule).
    """

    backend: str
    epoch: str
    generation: int
    seq: int
    messages: tuple[dict[str, Any], ...]
    stamps: tuple[int, ...]
    hidden: int
    trailing_tools: tuple[dict[str, Any], ...]
    open_calls: dict[int, int] | None
    live_messages: tuple[dict[str, Any], ...] | None
    positions: dict[str, int]
    #: The file behind each segment a tool reference names, as of this reading.
    sources: tuple[Path, ...] = ()
    memo: dict[Any, Any] = field(default_factory=dict, compare=False)

    @property
    def total(self) -> int:
        return len(self.messages)

    @property
    def token(self) -> str:
        """The public ``generation``: opaque, and never reused by another index.

        ``seq`` restarts when an index is rebuilt from nothing - after an eviction, or
        a daemon restart - so a bare counter would let a client holding ``3.7`` be
        told "nothing changed since 7" by a new index that happens to be at 7 too.
        The epoch is minted per index, so that client is answered with a reset.
        """
        return f"{self.epoch}.{self.generation}"

    @property
    def etag(self) -> str:
        return f"{self.token}.{self.seq}"

    def window(self, limit: int) -> dict[str, Any]:
        """The newest ``limit`` messages in the long-standing view shape.

        Memoised per snapshot, so two reads of an unchanged conversation return the
        same object - which, like every snapshot value, is shared and read-only.
        """
        key = ("window", limit)
        cached = self.memo.get(key)
        if cached is not None:
            return cached  # type: ignore[no-any-return]
        start = max(0, len(self.messages) - limit)
        selected = [public_message(self.messages[i], i) for i in range(start, len(self.messages))]
        view = {
            "messages": selected,
            "trailing_tool_calls": [public_tool(tool) for tool in self.trailing_tools],
            "hidden": self.hidden,
            "abandoned_messages": sum(1 for message in selected if message.get("abandoned")),
            "truncated": start > 0,
        }
        self.memo[key] = view
        return view

    def meta(self) -> dict[str, Any]:
        """Conversation-wide fields every page and delta carries."""
        return {
            "trailing_tool_calls": [public_tool(tool) for tool in self.trailing_tools],
            "hidden": self.hidden,
            "generation": self.token,
            "seq": self.seq,
            "total": len(self.messages),
        }

    def page(self, limit: int, before: str | None = None) -> dict[str, Any] | None:
        """``limit`` messages ending at the newest, or ending just before ``before``.

        ``None`` when ``before`` names no message this reading holds, which a client
        answers by starting again from the tail rather than guessing a position.
        """
        end = len(self.messages)
        if before is not None:
            found = self.positions.get(before)
            if found is None:
                return None
            end = found
        start = max(0, end - limit)
        selected = [public_message(self.messages[i], i) for i in range(start, end)]
        return {
            **self.meta(),
            "messages": selected,
            "abandoned_messages": sum(1 for message in selected if message.get("abandoned")),
            "has_older": start > 0,
            "truncated": start > 0,
            "delta": False,
            "replace_from": None,
            "page": "older" if before is not None else "tail",
        }

    def delta(self, since: int, oldest: str | None) -> dict[str, Any] | None:
        """Every message from the first one changed after ``since``, or ``None`` to reset.

        ``oldest`` is the oldest message the client holds: changes before it are not
        on the client's screen, so they are not resent. A client whose oldest message
        this reading no longer holds, or that claims a ``since`` this reading never
        reached, is answered with ``None`` (start again).
        """
        if since > self.seq:
            return None
        floor = 0
        if oldest is not None:
            found = self.positions.get(oldest)
            if found is None:
                return None
            floor = found
        first = next(
            (index for index in range(floor, len(self.stamps)) if self.stamps[index] > since),
            None,
        )
        selected = (
            [public_message(self.messages[i], i) for i in range(first, len(self.messages))]
            if first is not None
            else []
        )
        return {
            **self.meta(),
            "messages": selected,
            "abandoned_messages": sum(1 for message in selected if message.get("abandoned")),
            "has_older": floor > 0,
            "truncated": floor > 0,
            "delta": True,
            "replace_from": self.messages[first]["message_id"] if first is not None else None,
            "page": "delta",
        }


def _empty_snapshot(backend: str, epoch: str) -> ConversationSnapshot:
    return ConversationSnapshot(
        backend=backend,
        epoch=epoch,
        generation=0,
        seq=0,
        messages=(),
        stamps=(),
        hidden=0,
        trailing_tools=(),
        open_calls={} if tv._OPEN_TOOL_SCANNERS.get(transcript_dialect(backend) or "") else None,
        live_messages=(),
        positions={},
    )


# --------------------------------------------------------------------------
# One conversation


@dataclass(slots=True)
class _Segment:
    """One file the conversation is made of.

    A Codex fork inherits a pinned prefix of its parent (``history_base``), so its
    conversation is the parent's first ``end`` bytes followed by its own file. Only
    the last segment grows; the others are fixed, and are re-checked by sample.
    """

    path: Path
    base: int
    end: int | None
    source_id: str | None
    head: bytes = b""
    tail: bytes = b""


def _read_range(path: Path, start: int, length: int) -> bytes:
    if length <= 0:
        return b""
    with path.open("rb") as handle:
        handle.seek(max(0, start))
        return handle.read(length)


class ConversationIndex:
    """The compact records of one conversation, kept current by appending."""

    def __init__(
        self,
        key: str,
        path: Path | None,
        backend: str,
        native_id: str | None,
        stats: _Stats,
        *,
        quiet: bool = False,
    ) -> None:
        self.key = key
        # A throwaway index (a sweep's) logs its builds at DEBUG unless they are slow:
        # a reconcile reads thousands, and one INFO line each would bury the log.
        self.quiet = quiet
        self.path = path
        self.backend = backend
        self.native_id = native_id
        self.dialect = transcript_dialect(backend)
        self.store = conversation_store_path(backend)
        self.lock = threading.Lock()
        self.stats = stats
        self.events: list[dict[str, Any]] = []
        self.provisional: dict[str, Any] | None = None
        self.segments: list[_Segment] = []
        self.offset = 0
        self.prefix = b""
        self.boundary = b""
        self.store_mark: tuple[int, int] | None = None
        self.epoch = uuid.uuid4().hex[:8]
        self.generation = 0
        self.seq = 0
        self.snapshot: ConversationSnapshot | None = None
        self.estimated_bytes = 0
        self.progress = (0, 0)
        self.sniffed = 0
        self.building = False
        self._parse: tuple[int, int, list[dict[str, Any]], int] | None = None
        self._filled: tuple[ConversationSnapshot, list[dict[str, Any]], int] | None = None

    @property
    def name(self) -> str:
        return self.path.name if self.path is not None else self.key

    @property
    def _build_counter(self) -> str:
        # Kept apart so a sweep cannot pass for live readers missing the index.
        return "sweep_builds" if self.quiet else "builds"

    # -- refresh ------------------------------------------------------------

    def refresh(self) -> ConversationSnapshot:
        """Bring the index up to date with the conversation and return its reading.

        Raises ``OSError`` exactly where a full read would have: a missing file, or a
        Codex inheritance chain that is cyclic or names a parent that is gone.
        """
        with self.lock:
            if self.store is not None:
                return self._refresh_store(self.store)
            if self.path is None:
                if self.snapshot is None:
                    self.snapshot = _empty_snapshot(self.backend, self.epoch)
                return self.snapshot
            return self._refresh_file(self.path)

    def _refresh_store(self, store: Path) -> ConversationSnapshot:
        mark = store_watermark(store, self.native_id or "")
        if self.snapshot is not None and mark == self.store_mark:
            self.stats.count("unchanged")
            return self.snapshot
        started = time.perf_counter()
        records = conversation_records(store, self.native_id or "", max_messages=None)
        # A store has no append contract to extend from; its records are small and
        # are re-read whole whenever the conversation's own watermark moves.
        self.events = [self._memoise(dict(record)) for record in records]
        self._filled = None
        self.estimated_bytes = sum(_estimated_size(event.get("parts")) for event in self.events)
        self.store_mark = mark
        self._recompute(rebuilt=False)
        self.stats.count(self._build_counter)
        self._log_timing("store", started, len(self.events), 0)
        assert self.snapshot is not None
        return self.snapshot

    def _refresh_file(self, path: Path) -> ConversationSnapshot:
        size = path.stat().st_size
        reason = None
        if not self.segments:
            reason = "first_read"
        elif size < self.offset:
            reason = "truncated"
        else:
            reason = self._changed_reason(path, size)
        started = time.perf_counter()
        if reason is not None:
            rebuilt = self.snapshot is not None
            self._build(path)
            self._recompute(rebuilt=rebuilt)
            self.stats.count("rebuilds" if rebuilt else self._build_counter)
            if rebuilt:
                log.info(
                    "transcript_index_rebuilt path=%s backend=%s reason=%s generation=%d",
                    path.name,
                    self.backend,
                    reason,
                    self.generation,
                )
            self._log_timing("build", started, len(self.events), self.offset)
            assert self.snapshot is not None
            return self.snapshot
        before_events, before_offset = len(self.events), self.offset
        before_provisional = self.provisional
        self._extend(path, size)
        appended = len(self.events) - before_events
        if appended or self.provisional != before_provisional or self.snapshot is None:
            self._recompute(rebuilt=False)
            self.stats.count("extends")
            self._log_timing("extend", started, appended, self.offset - before_offset)
        else:
            self.stats.count("unchanged")
        assert self.snapshot is not None
        return self.snapshot

    def _changed_reason(self, path: Path, size: int) -> str | None:
        """Why the file is no longer the one the index decoded, or ``None``."""
        for segment in self.segments[:-1]:
            assert segment.end is not None
            try:
                if segment.path.stat().st_size < segment.end:
                    return "parent_truncated"
                if (
                    _read_range(segment.path, 0, len(segment.head)) != segment.head
                    or _read_range(segment.path, segment.end - len(segment.tail), len(segment.tail))
                    != segment.tail
                ):
                    return "parent_changed"
            except OSError:
                return "parent_unreadable"
        if self.prefix and _read_range(path, 0, len(self.prefix)) != self.prefix:
            return "prefix_changed"
        if self.boundary and (
            _read_range(path, self.offset - len(self.boundary), len(self.boundary)) != self.boundary
        ):
            return "boundary_changed"
        del size
        return None

    def _build(self, path: Path) -> None:
        self.events = []
        self.provisional = None
        self.estimated_bytes = 0
        self.offset = 0
        self.prefix = b""
        self.boundary = b""
        self._parse = None
        self._filled = None
        self.building = True
        try:
            sources: list[tuple[Path, int]]
            if self.dialect == "codex":
                sources = codex_history.segments(path)
            else:
                sources = [(path, path.stat().st_size)]
            total = sum(length for _source, length in sources)
            self.progress = (0, total)
            self.segments = []
            base = 0
            for index, (source, length) in enumerate(sources):
                live = index == len(sources) - 1
                source_id = None
                if not live:
                    source_id = str(codex_history.metadata(source).get("id", source.name))
                segment = _Segment(source, base, None if live else length, source_id)
                self.segments.append(segment)
                if live:
                    self._extend(path, length)
                else:
                    self._decode_range(index, segment, 0, length, commit_partial=False)
                    segment.head = _read_range(source, 0, min(SAMPLE_BYTES, length))
                    segment.tail = _read_range(
                        source, max(0, length - SAMPLE_BYTES), min(SAMPLE_BYTES, length)
                    )
                base += length
        finally:
            self.building = False

    def _extend(self, path: Path, size: int) -> None:
        segment_index = len(self.segments) - 1
        segment = self.segments[segment_index]
        self.offset = self._decode_range(
            segment_index, segment, self.offset, size, commit_partial=True
        )
        self.prefix = _read_range(path, 0, min(SAMPLE_BYTES, self.offset))
        self.boundary = _read_range(
            path, max(0, self.offset - SAMPLE_BYTES), min(SAMPLE_BYTES, self.offset)
        )

    def _decode_range(
        self, segment_index: int, segment: _Segment, start: int, stop: int, *, commit_partial: bool
    ) -> int:
        """Decode complete lines in ``start..stop``; return the offset after the last one.

        With ``commit_partial`` an unterminated final line (a writer mid-append) is
        decoded as ``self.provisional`` and not committed, so it is read again once
        it is whole. A pinned parent segment stops at its boundary instead.
        """
        self.provisional = None if commit_partial else self.provisional
        position = start
        reported = position
        sniffed_before = self.sniffed
        lines = kept = 0
        with segment.path.open("rb") as handle:
            handle.seek(start)
            while position < stop:
                line = handle.readline()
                if not line:
                    break
                end = position + len(line)
                if not line.endswith(b"\n"):
                    if commit_partial and line.strip():
                        lines += 1
                        self.provisional = self._decode(line, segment_index, segment, position, end)
                    break
                if not commit_partial and end > stop:
                    break
                lines += 1
                event = self._decode(line, segment_index, segment, position, end)
                if event is not None:
                    self.events.append(event)
                    self.estimated_bytes += _estimated_size(event.get("message") or event)
                    kept += 1
                position = end
                if position - reported >= _PROGRESS_EVERY_BYTES:
                    done, total = self.progress
                    self.progress = (done + position - reported, total)
                    reported = position
        done, total = self.progress
        self.progress = (done + position - reported, total)
        sniffed = self.sniffed - sniffed_before
        # `lines_decoded` counts JSON decodes, the cost the index exists to avoid
        # repeating; `lines_sniffed` the Codex lines skipped without one.
        self.stats.add(
            lines_decoded=lines - sniffed,
            lines_sniffed=sniffed,
            records_kept=kept,
            bytes_read=position - start,
        )
        return position

    def _decode(
        self, line: bytes, segment_index: int, segment: _Segment, start: int, end: int
    ) -> dict[str, Any] | None:
        if self.dialect == "codex" and codex_line_is_skippable(line):
            self.sniffed += 1
            return None
        try:
            event = json.loads(line)
        except UnicodeDecodeError:
            try:
                event = json.loads(line.decode("utf-8", "replace"))
            except ValueError:
                return None
        except ValueError:
            return None
        if not isinstance(event, dict):
            return None
        slimmer = _SLIMMERS.get(self.dialect or "")
        if slimmer is None:
            return None

        def ref(block: int) -> ToolRef:
            return (segment_index, start, end, block)

        slim = slimmer(event, ref)
        if slim is None:
            return None
        slim[_OFFSET] = segment.base + start
        slim[_END] = segment.base + end
        if segment.source_id is not None:
            slim[codex_history.SOURCE_THREAD] = f"{segment.source_id}:{start}"
        return self._memoise(slim)

    def _memoise(self, event: dict[str, Any]) -> dict[str, Any]:
        message = tv._native_conversation_message(event, self.backend)
        event[tv._MEMO_KEY] = (
            tv._tool_call_details(event, self.backend),
            message,
            tv._message_text(message.get("content")) if message is not None else None,
        )
        return event

    # -- projection ---------------------------------------------------------

    def _current_events(self) -> list[dict[str, Any]]:
        return [*self.events, self.provisional] if self.provisional is not None else self.events

    def _recompute(self, *, rebuilt: bool) -> None:
        events = self._current_events()
        for event in events:
            for marker in _MARKERS:
                event.pop(marker, None)
        tv._mark_abandoned_records(events, self.backend)
        records, hidden, trailing = tv._conversation_records(events, self.backend)
        merged = tv._merge_assistant_segments(records)
        scanner = tv._OPEN_TOOL_SCANNERS.get(self.dialect or "")
        open_calls = scanner(events) if scanner is not None else None
        live = (
            tuple(tv._merge_assistant_segments([r for r in records if not r.get("abandoned")]))
            if scanner is not None
            else None
        )
        previous = self.snapshot
        upcoming = self.seq + 1
        stamps, structural = self._stamps(previous, merged, upcoming, rebuilt=rebuilt)
        if previous is not None and (rebuilt or structural):
            self.generation += 1
        if (
            previous is None
            or rebuilt
            or structural
            or upcoming in stamps
            or hidden != previous.hidden
            or [tool.get("id") for tool in trailing]
            != [tool.get("id") for tool in previous.trailing_tools]
        ):
            self.seq = upcoming
        self.snapshot = ConversationSnapshot(
            backend=self.backend,
            epoch=self.epoch,
            generation=self.generation,
            seq=self.seq,
            messages=tuple(merged),
            stamps=tuple(stamps),
            hidden=hidden,
            trailing_tools=tuple(trailing),
            open_calls=open_calls,
            live_messages=live,
            positions={str(message["message_id"]): index for index, message in enumerate(merged)},
            sources=tuple(segment.path for segment in self.segments),
        )
        self._parse = None
        self._drop_filled()

    @staticmethod
    def _stamps(
        previous: ConversationSnapshot | None,
        merged: list[dict[str, Any]],
        upcoming: int,
        *,
        rebuilt: bool,
    ) -> tuple[list[int], bool]:
        """Per-message change stamps against the previous reading, and whether it broke.

        A new or changed message is stamped ``upcoming``, the ``seq`` this reading
        takes if anything changed. A message that vanished, or an id that moved, is
        structural: no splice can express it, so every message is restamped.
        """
        if previous is None or rebuilt:
            return [upcoming] * len(merged), False
        old = previous.messages
        if len(merged) < len(old) or any(
            old[index]["message_id"] != merged[index]["message_id"] for index in range(len(old))
        ):
            return [upcoming] * len(merged), True
        stamps = [
            previous.stamps[index]
            if _signature(old[index]) == _signature(merged[index])
            else upcoming
            for index in range(len(old))
        ]
        stamps.extend([upcoming] * (len(merged) - len(old)))
        return stamps, False

    def parsed(self) -> tuple[list[dict[str, Any]], int, ConversationSnapshot]:
        """The indexing projection (`transcript_view.parse_transcript`) over this index.

        Tool blocks carry their reference instead of their input; the reading
        returned alongside is the one whose ``sources`` those references name.
        Computed on first ask per reading and kept until the next change.
        """
        with self.lock:
            snapshot = self.snapshot
            assert snapshot is not None
            if self._parse is not None and self._parse[:2] == (snapshot.generation, snapshot.seq):
                return self._parse[2], self._parse[3], snapshot
            messages, abandoned = tv._parse_events_counted(self._current_events(), self.backend)
            self._parse = (snapshot.generation, snapshot.seq, messages, abandoned)
            return messages, abandoned, snapshot

    def parsed_with_inputs(self, *, keep: bool) -> tuple[list[dict[str, Any]], int]:
        """:meth:`parsed` with every tool input read back - a full parse's exact output.

        ``keep`` holds the result until the conversation next changes, counted against
        the budget, for a reader a person is waiting on that may ask again (History's
        tool-call pages); that result is SHARED between such readers and must be
        treated as read-only, the contract the watermark-keyed parse cache it replaces
        already had. A sweep over many conversations passes ``False``, so it pins
        nothing and gets a list of its own.
        """
        messages, abandoned, reading = self.parsed()
        with self.lock:
            cached = self._filled
            if keep and cached is not None and cached[0] is reading:
                return cached[1], abandoned
        filled = fill_parse_inputs(messages, reading)
        if keep:
            with self.lock:
                if self.snapshot is reading:
                    self._drop_filled()
                    size = _estimated_size(filled)
                    self._filled = (reading, filled, size)
                    self.estimated_bytes += size
        return filled, abandoned

    def _drop_filled(self) -> None:
        if self._filled is not None:
            self.estimated_bytes -= self._filled[2]
            self._filled = None

    def _log_timing(self, kind: str, started: float, records: int, read: int) -> None:
        elapsed = time.perf_counter() - started
        self.stats.note_elapsed(elapsed)
        if elapsed >= SLOW_BUILD_SECONDS:
            self.stats.count("slow")
            log.warning(
                "transcript_index_slow kind=%s path=%s backend=%s records=%d bytes=%d "
                "elapsed_ms=%.0f",
                kind,
                self.name,
                self.backend,
                records,
                read,
                elapsed * 1000,
            )
        elif kind == "extend":
            log.debug(
                "transcript_index_extended path=%s records=%d bytes=%d elapsed_ms=%.1f",
                self.name,
                records,
                read,
                elapsed * 1000,
            )
        else:
            log.log(
                logging.DEBUG if self.quiet else logging.INFO,
                "transcript_index_built kind=%s path=%s backend=%s records=%d bytes=%d "
                "messages=%d estimated_bytes=%d elapsed_ms=%.0f",
                kind,
                self.name,
                self.backend,
                records,
                read,
                len(self.snapshot.messages) if self.snapshot is not None else 0,
                self.estimated_bytes,
                elapsed * 1000,
            )


def _native_tool_block(event: dict[str, Any], block: int) -> dict[str, Any] | None:
    """The native block a reference names inside its decoded record."""
    if block < 0:
        payload = event.get("payload")
        return payload if isinstance(payload, dict) else None
    message = event.get("message") or {}
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, list) or block >= len(content):
        return None
    item = content[block]
    return item if isinstance(item, dict) else None


_NATIVE_ID_KEYS = ("id", "call_id", "callID", "tool_use_id")


def _carries_id(block: dict[str, Any], expected: str | None) -> bool:
    """Whether ``block`` is the call a projection named ``expected``.

    Membership rather than one key, because the projections name a call by
    different keys (the reader's details prefer ``id``, the Codex parse prefers
    ``call_id``, and a Codex function call carries both). A block with no id at all,
    or an expectation that was synthesised from the record's position, cannot be
    contradicted and is accepted.
    """
    ids = {str(block[key]) for key in _NATIVE_ID_KEYS if block.get(key)}
    return not ids or not expected or expected in ids


def _read_tool_blocks(
    sources: tuple[Path, ...], wanted: Iterable[tuple[ToolRef, str | None]]
) -> dict[ToolRef, dict[str, Any]]:
    """The native block behind each reference, decoded from the record holding it.

    Records are grouped so each is read and decoded once however many of its blocks
    were asked for, and read in file order through one open handle per file. A
    reference is only answered when the block it lands on still carries the call id
    the reader showed: a file rewritten since the reading puts other bytes at that
    offset, and the honest answer then is no input rather than another call's.
    """
    by_record: dict[tuple[int, int, int], list[tuple[ToolRef, str | None]]] = {}
    for ref, expected in wanted:
        by_record.setdefault(ref[:3], []).append((ref, expected))
    result: dict[ToolRef, dict[str, Any]] = {}
    misses = 0
    handles: dict[int, Any] = {}
    try:
        for (segment_index, start, end), items in sorted(by_record.items()):
            event: Any = None
            if 0 <= segment_index < len(sources):
                try:
                    handle = handles.get(segment_index)
                    if handle is None:
                        handle = handles[segment_index] = sources[segment_index].open("rb")
                    handle.seek(start)
                    event = json.loads(handle.read(end - start))
                except (OSError, ValueError):
                    event = None
            for ref, expected in items:
                block = _native_tool_block(event, ref[3]) if isinstance(event, dict) else None
                if block is None or not _carries_id(block, expected):
                    misses += 1
                    continue
                result[ref] = block
    finally:
        for handle in handles.values():
            handle.close()
    if misses:
        REGISTRY.stats.count("tool_input_misses", misses)
        log.info("transcript_tool_input_unavailable count=%d", misses)
    return result


def tool_inputs(
    reading: ConversationSnapshot, tools: Iterable[dict[str, Any]]
) -> list[dict[str, Any]]:
    """``[{id, name, input}]`` for tool calls of ``reading``, inputs read from the file.

    A call whose input could not be read back keeps its name with ``input: None``.
    """
    tools = list(tools)
    blocks = _read_tool_blocks(
        reading.sources,
        [
            (tool[tv._TOOL_REF_KEY], str(tool.get("id") or "") or None)
            for tool in tools
            if tv._TOOL_REF_KEY in tool
        ],
    )
    result: list[dict[str, Any]] = []
    for tool in tools:
        value = tool.get("input")
        if tv._TOOL_REF_KEY in tool:
            block = blocks.get(tool[tv._TOOL_REF_KEY])
            value = tv.native_tool_input(block) if block is not None else None
        result.append({"id": tool.get("id"), "name": tool.get("name"), "input": value})
    return result


def fill_parse_inputs(
    messages: list[dict[str, Any]], reading: ConversationSnapshot
) -> list[dict[str, Any]]:
    """The indexing parse with every ``tool_use`` input read back: a full parse's output.

    Fresh dicts throughout, so a caller may keep or change the result without
    touching the index's shared records. A block whose input can no longer be read
    (the file was rewritten since ``reading``) carries ``input: None``.
    """
    wanted = [
        (block[tv._TOOL_REF_KEY], str(block.get("id") or "") or None)
        for message in messages
        for block in message.get("content") or []
        if isinstance(block, dict) and tv._TOOL_REF_KEY in block
    ]
    natives = _read_tool_blocks(reading.sources, wanted) if wanted else {}
    filled: list[dict[str, Any]] = []
    for message in messages:
        content = message.get("content")
        if not isinstance(content, list):
            filled.append(dict(message))
            continue
        blocks: list[Any] = []
        for block in content:
            if not isinstance(block, dict) or tv._TOOL_REF_KEY not in block:
                blocks.append(block)
                continue
            ref = block[tv._TOOL_REF_KEY]
            native = natives.get(ref)
            value = None
            if native is not None:
                # The same rule the full parse applies to the same block.
                value = tv.codex_call_input(native) if ref[3] < 0 else tv.native_tool_input(native)
            blocks.append(
                {**{k: v for k, v in block.items() if k != tv._TOOL_REF_KEY}, "input": value}
            )
        filled.append({**message, "content": blocks})
    return filled


def calls_at(reading: ConversationSnapshot, message_id: str | None) -> list[dict[str, Any]] | None:
    """The tool calls before one message of ``reading`` with their inputs, or the trailing ones.

    ``message_id=None`` asks for the calls after the newest message. ``None`` when the
    message is not in this reading (it moved, or the client is stale). Results are
    never included.
    """
    if message_id is None:
        return tool_inputs(reading, reading.trailing_tools)
    position = reading.positions.get(message_id)
    if position is None:
        return None
    return tool_inputs(reading, reading.messages[position].get("preceding_tools") or ())


# --------------------------------------------------------------------------
# The registry


class _Stats:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.counters: dict[str, int] = {}
        self.worst_seconds = 0.0

    def count(self, name: str, amount: int = 1) -> None:
        with self._lock:
            self.counters[name] = self.counters.get(name, 0) + amount

    def add(self, **amounts: int) -> None:
        with self._lock:
            for name, amount in amounts.items():
                self.counters[name] = self.counters.get(name, 0) + amount

    def note_elapsed(self, elapsed: float) -> None:
        with self._lock:
            self.worst_seconds = max(self.worst_seconds, elapsed)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {**self.counters, "worst_seconds": round(self.worst_seconds, 3)}


def _key(path: Path | None, backend: str, native_id: str | None) -> str:
    store = conversation_store_path(backend)
    if store is not None:
        return f"{backend}|store:{store}#{native_id or ''}"
    if path is None:
        return f"{backend}|none"
    return f"{backend}|{os.path.normcase(os.path.abspath(str(path)))}"


class ConversationIndexRegistry:
    """Every live index, bounded by an estimated byte budget, least recently used first."""

    def __init__(
        self, *, budget_bytes: int = DEFAULT_BUDGET_BYTES, max_indexes: int = MAX_INDEXES
    ) -> None:
        self.budget_bytes = budget_bytes
        self.max_indexes = max_indexes
        self.stats = _Stats()
        # Re-entrant: `snapshot_async` creates the executor while it holds the lock.
        self._lock = threading.RLock()
        self._indexes: OrderedDict[str, ConversationIndex] = OrderedDict()
        self._inflight: dict[str, Future[ConversationSnapshot]] = {}
        self._executor: ThreadPoolExecutor | None = None

    def peek(
        self, path: Path | None, backend: str, native_id: str | None
    ) -> ConversationIndex | None:
        """The index already held for a conversation, without creating or touching one."""
        with self._lock:
            return self._indexes.get(_key(path, backend, native_id))

    def index_for(
        self, path: Path | None, backend: str, native_id: str | None
    ) -> ConversationIndex:
        key = _key(path, backend, native_id)
        with self._lock:
            index = self._indexes.get(key)
            if index is None:
                index = ConversationIndex(key, path, backend, native_id, self.stats)
                self._indexes[key] = index
            self._indexes.move_to_end(key)
            return index

    def snapshot(
        self, path: Path | None, backend: str, native_id: str | None = None
    ) -> ConversationSnapshot:
        """Refresh and read one conversation, in the calling thread."""
        index = self.index_for(path, backend, native_id)
        snapshot = index.refresh()
        self._evict(keep=index.key)
        return snapshot

    def _executor_for(self) -> ThreadPoolExecutor:
        with self._lock:
            if self._executor is None:
                self._executor = ThreadPoolExecutor(
                    max_workers=2, thread_name_prefix="transcript-index"
                )
            return self._executor

    async def snapshot_async(
        self,
        path: Path | None,
        backend: str,
        native_id: str | None = None,
        *,
        wait_seconds: float,
    ) -> ConversationSnapshot:
        """Refresh off the loop, joining a refresh of the same conversation in flight.

        Raises :class:`IndexBuilding` when ``wait_seconds`` passes first. The refresh is
        not abandoned: it keeps running and fills the index, so the next ask is fast
        rather than a second timeout over the same work.
        """
        index = self.index_for(path, backend, native_id)
        with self._lock:
            future = self._inflight.get(index.key)
            if future is None or future.done():
                future = self._executor_for().submit(self._refresh_and_evict, index)
                self._inflight[index.key] = future
            else:
                self.stats.count("joined")
        try:
            return await asyncio.wait_for(asyncio.shield(asyncio.wrap_future(future)), wait_seconds)
        except TimeoutError:
            read, total = index.progress
            raise IndexBuilding(
                {"read_bytes": read, "total_bytes": total, "building": index.building}
            ) from None

    def _refresh_and_evict(self, index: ConversationIndex) -> ConversationSnapshot:
        snapshot = index.refresh()
        self._evict(keep=index.key)
        return snapshot

    def _evict(self, *, keep: str) -> None:
        with self._lock:
            total = sum(index.estimated_bytes for index in self._indexes.values())
            while self._indexes and (
                total > self.budget_bytes or len(self._indexes) > self.max_indexes
            ):
                key, index = next(iter(self._indexes.items()))
                if key == keep:
                    if len(self._indexes) == 1:
                        break
                    self._indexes.move_to_end(key)
                    continue
                del self._indexes[key]
                total -= index.estimated_bytes
                self.stats.count("evictions")
                log.info(
                    "transcript_index_evicted path=%s estimated_bytes=%d",
                    index.name,
                    index.estimated_bytes,
                )

    def status(self) -> dict[str, Any]:
        """Counters for `/api/diagnostics/background` (`transcript_index`)."""
        with self._lock:
            indexes = list(self._indexes.values())
        return {
            **self.stats.snapshot(),
            "indexes": len(indexes),
            "estimated_bytes": sum(index.estimated_bytes for index in indexes),
            "budget_bytes": self.budget_bytes,
            "building": [
                {
                    "path": index.name,
                    "read_bytes": index.progress[0],
                    "total_bytes": index.progress[1],
                }
                for index in indexes
                if index.building
            ],
        }

    def clear(self) -> None:
        with self._lock:
            self._indexes.clear()
            self._inflight.clear()

    def close(self) -> None:
        """Drop every index and stop the executor; a later async read starts a new one."""
        with self._lock:
            executor, self._executor = self._executor, None
            self._indexes.clear()
            self._inflight.clear()
        if executor is not None:
            executor.shutdown(wait=True, cancel_futures=True)


REGISTRY = ConversationIndexRegistry()


def snapshot(path: Path | None, backend: str, native_id: str | None = None) -> ConversationSnapshot:
    """The current reading of one conversation, refreshed in the calling thread."""
    return REGISTRY.snapshot(path, backend, native_id)


async def snapshot_async(
    path: Path | None, backend: str, native_id: str | None = None, *, wait_seconds: float
) -> ConversationSnapshot:
    return await REGISTRY.snapshot_async(path, backend, native_id, wait_seconds=wait_seconds)


def status() -> dict[str, Any]:
    return REGISTRY.status()


def tool_calls(
    path: Path | None,
    backend: str,
    native_id: str | None = None,
    *,
    message_id: str | None,
) -> list[dict[str, Any]] | None:
    """:func:`calls_at` over a freshly refreshed reading, in the calling thread."""
    return calls_at(snapshot(path, backend, native_id), message_id)


def indexed_parse(
    path: Path | None, backend: str, *, native_id: str | None = None, shared: bool = True
) -> tv.ParsedConversation:
    """``parse_transcript_with_watermark``'s exact output, served from the index.

    The indexing projection - abandoned records dropped, ordinals being positions in
    this list - with every ``tool_use`` input read back from the record holding it,
    so a caller cannot tell it from a full parse except by how long it took. The
    watermark is stamped before the refresh, the same conservative direction
    :func:`transcript_view.parse_transcript_with_watermark` takes.

    ``shared`` reads through the registry and keeps the result for the next ask,
    which is right for a person waiting on one conversation. A sweep over many
    passes ``False``: it reuses an index a reader already holds, and otherwise reads
    through a throwaway one, so walking thousands of conversations neither evicts the
    indexes live readers depend on nor pins anything after it moves on.
    """
    _identity, first, second = tv.conversation_watermark(path, backend, native_id)
    index: ConversationIndex | None
    if shared:
        index = REGISTRY.index_for(path, backend, native_id)
    else:
        index = REGISTRY.peek(path, backend, native_id) or ConversationIndex(
            _key(path, backend, native_id), path, backend, native_id, REGISTRY.stats, quiet=True
        )
    index.refresh()
    messages, abandoned = index.parsed_with_inputs(keep=shared)
    return tv.ParsedConversation(messages, abandoned, first, second)


def parsed_tool_calls(
    path: Path | None,
    backend: str,
    *,
    native_id: str | None = None,
    ordinals: Iterable[int],
) -> dict[int, list[dict[str, Any]]]:
    """``{ordinal: [{id, name, input}]}`` for the parse-list messages named.

    Reads only those messages' inputs back, by the same rule the full parse applies.
    """
    index = REGISTRY.index_for(path, backend, native_id)
    index.refresh()
    messages, _abandoned, reading = index.parsed()
    wanted = sorted({ordinal for ordinal in ordinals if 0 <= ordinal < len(messages)})
    filled = fill_parse_inputs([messages[ordinal] for ordinal in wanted], reading)
    return {
        ordinal: [
            {"id": block.get("id"), "name": block.get("name"), "input": block.get("input")}
            for block in message.get("content") or []
            if isinstance(block, dict) and block.get("type") == "tool_use"
        ]
        for ordinal, message in zip(wanted, filled, strict=True)
    }


def reset() -> None:
    """Drop every index. For tests, and for a caller that knows the files moved."""
    REGISTRY.clear()
