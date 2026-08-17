"""Writing a forked conversation: a new native transcript holding a prefix of another.

Branching used to ask the CLI to fork itself. That made every branch a negotiation
with a live process: the daemon typed a slash command into a terminal a human was
holding, waited for the fork to appear, waited again for the source process to let
go of the conversation it had just left, and retried the sibling spawn around the
race between those two. It could only ever fork from *now*, it mutated the pane the
operator was looking at, and it worked for exactly the harnesses whose CLI happened
to offer the command.

Forking at the transcript layer removes all of that. mux reads the source
conversation, writes a **new** conversation file containing the records up to a
chosen point, and resumes that. Nothing is typed anywhere, the source file is opened
read-only and never written, and the cut can land at any message rather than only at
the end. A session that is mid-turn, or that has already exited, forks exactly as
well as an idle one, because none of them is asked to participate.

Measured on real CLIs before it was built (2026-08-17): Claude 2.1.233, Codex 0.147.0
and oh-my-pi 17.2.10 each resumed a hand-written prefix conversation and answered
from the truncated context, with the source file byte-identical afterwards. Only
Claude is implemented here; the other two are why the seam below is a registry rather
than an `if`.

What this module does **not** do is decide *where* a cut is legal. That is a reading
question about a transcript's own shape, and it lives with the reader
(`transcript_view.conversation_cut_points`). This module is handed a byte offset and
writes what the offset names.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .harness import transcript_dialect

log = logging.getLogger(__name__)

# A source transcript larger than this is refused rather than forked. The writer
# reads and re-serializes every record before the cut, so cost is linear in the
# prefix; the cap exists because a pathological conversation (a 550 MB Codex rollout
# exists in the wild, and a Claude title loop once produced 57 MB of junk in one
# file) must fail as a stated refusal rather than as a stalled request.
FORK_MAX_SOURCE_BYTES = 256 * 1024 * 1024


class ForkUnsupported(Exception):
    """No fork writer is implemented for this harness."""


class ForkRefused(Exception):
    """The fork was not attempted, and the reason is the operator's to see."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class ForkPlan:
    """Everything the writer needs, with no reference to a live session.

    ``target_path`` is supplied rather than derived because only the adapter knows
    where a harness keeps a conversation, and it is deliberately computed from the
    working directory the *new pane* will run in. Claude resolves a conversation
    from its cwd, so a fork written beside a source file the cwd no longer maps to
    is a fork the CLI cannot open.
    """

    backend: str
    source_path: Path
    source_conversation_id: str
    fork_conversation_id: str
    target_path: Path
    cut_offset: int
    title_marker: str


@dataclass(frozen=True, slots=True)
class ForkOutcome:
    """What the writer actually produced, for the log line and the branch event."""

    conversation_id: str
    path: Path
    records_written: int
    records_dropped: int
    attachments_copied: int
    bytes_written: int


def fork_supported(backend: object) -> bool:
    """Whether mux can write a forked conversation for this harness."""
    return transcript_dialect(backend) in _FORK_WRITERS


def mint_conversation_id(backend: object) -> str:
    """A fresh conversation id in the shape this harness names conversations with."""
    dialect = transcript_dialect(backend)
    minter = _ID_MINTERS.get(dialect or "")
    if minter is None:
        raise ForkUnsupported(f"no fork writer for {backend!r}")
    return minter()


def write_fork(plan: ForkPlan) -> ForkOutcome:
    """Write ``plan``'s prefix as a new conversation, leaving the source untouched.

    Raises ``ForkUnsupported`` for a harness with no writer and ``ForkRefused``
    for a source this writer will not act on. Every other failure is an ordinary
    ``OSError``: the caller reports it as the fork failing, which it is.
    """
    dialect = transcript_dialect(plan.backend)
    writer = _FORK_WRITERS.get(dialect or "")
    if writer is None:
        raise ForkUnsupported(f"no fork writer for {plan.backend!r}")
    try:
        size = plan.source_path.stat().st_size
    except OSError as exc:
        raise ForkRefused(
            "source_unreadable", f"the conversation file is unreadable: {exc}"
        ) from exc
    if size > FORK_MAX_SOURCE_BYTES:
        raise ForkRefused(
            "source_too_large",
            f"the conversation is {size // (1024 * 1024)} MB, past the "
            f"{FORK_MAX_SOURCE_BYTES // (1024 * 1024)} MB fork limit",
        )
    if plan.cut_offset <= 0:
        raise ForkRefused("empty_prefix", "there is nothing before that point to fork")
    if plan.target_path.exists():
        raise ForkRefused("fork_id_taken", "a conversation already answers to the new id")
    outcome = writer(plan)
    log.info(
        "fork wrote backend=%s source=%s fork=%s cut=%d records=%d dropped=%d "
        "attachments=%d bytes=%d path=%s",
        plan.backend,
        plan.source_conversation_id,
        outcome.conversation_id,
        plan.cut_offset,
        outcome.records_written,
        outcome.records_dropped,
        outcome.attachments_copied,
        outcome.bytes_written,
        outcome.path,
    )
    return outcome


# ----------------------------------------------------------------------------- claude


# Claude records that must not survive a fork, and why each one is different from
# "not worth copying".
#
# `queue-operation` is a prompt the operator queued against the *source* pane. A
# fork inheriting it would deliver somebody's queued message into a conversation
# they had not yet decided to have, which is the one failure mode a branch must not
# have. Dropping it is the whole reason this list is not simply empty.
_CLAUDE_DROPPED_TYPES = frozenset({"queue-operation"})

# Claude records that name a message by uuid. Kept only when that message is in the
# fork, because the alternative is a checkpoint or a recalled prompt pointing at a
# turn this conversation never had.
_CLAUDE_MESSAGE_REFERENCES: dict[str, tuple[str, ...]] = {
    "last-prompt": ("leafUuid",),
    "file-history-snapshot": ("messageId",),
    "file-history-delta": ("messageId", "snapshotMessageId"),
}

# Where a Claude record carries a conversation's display name. Marked rather than
# copied, because a fork and its source sharing a title is what the CLI's
# name-collision resolver exists to break, and on 2026-08-14 it broke it by
# appending generated suffixes in a loop that wrote ~57 MB of duplicate title
# records into each of two transcripts. Owning the fork's title means never
# handing that resolver a clash to solve.
_CLAUDE_TITLE_FIELDS: dict[str, tuple[str, ...]] = {
    "ai-title": ("aiTitle",),
    "custom-title": ("customTitle", "title"),
    "agent-name": ("agentName", "name"),
}


def _mint_claude_conversation_id() -> str:
    return str(uuid.uuid4())


def _claude_sidecar_dir(transcript: Path, conversation_id: str) -> Path:
    """Where Claude persists this conversation's oversized tool outputs."""
    return transcript.parent / conversation_id


# What follows an absolute reference to the conversation's own directory: the
# separator, then the relative path, stopping at the first character a generated
# sidecar name cannot contain. The references are embedded in prose ("Full output
# saved to: <path>\n\nPreview…"), so the terminator matters as much as the match.
_SIDECAR_TAIL = re.compile(r'[\\/]([^\s"\'<>|*?]+)')
_PATH_SEPARATOR = re.compile(r"[\\/]")
# A sidecar lives one directory deep (`tool-results/<name>`). Bounding the walk
# keeps a path that ran into adjacent prose from being read as a deep tree.
_SIDECAR_MAX_DEPTH = 2


def _rewrite_sidecar_references(
    value: Any, roots: tuple[str, ...], fork_root: str, found: set[str]
) -> Any:
    """Repoint the source conversation's sidecar directory at the fork's, and note it.

    Claude stores a tool result too large to inline as a file under the
    conversation's own directory and refers to it by **absolute path**, in
    `persistedOutputPath` and again inside the human-readable result text. A fork
    that copies those records verbatim reads out of the source conversation's
    directory: it works, right up until that conversation is cleaned up, and then a
    branch made weeks earlier starts failing to open its own tool output. Rewriting
    the reference and copying the file is what makes a fork independent of the
    conversation it came from.

    Both spellings of the directory are matched because the CLI writes the native
    one and its own Bash surface writes the POSIX one, and a fork that repointed only
    one would leave half its references aimed at a conversation it no longer belongs
    to. Walks the decoded record rather than the raw line, so JSON escaping cannot
    make a path match or miss by accident.
    """
    if isinstance(value, str):
        for root in roots:
            index = value.find(root)
            while index != -1:
                tail = _SIDECAR_TAIL.match(value, index + len(root))
                if tail:
                    parts = _PATH_SEPARATOR.split(tail.group(1))[:_SIDECAR_MAX_DEPTH]
                    if parts and parts[-1]:
                        found.add("/".join(parts))
                index = value.find(root, index + len(root))
            value = value.replace(root, fork_root)
        return value
    if isinstance(value, list):
        return [_rewrite_sidecar_references(item, roots, fork_root, found) for item in value]
    if isinstance(value, dict):
        return {
            key: _rewrite_sidecar_references(item, roots, fork_root, found)
            for key, item in value.items()
        }
    return value


def _write_claude_fork(plan: ForkPlan) -> ForkOutcome:
    """Stream the prefix into a new `<conversation id>.jsonl`, transforming as it goes.

    Streaming rather than parse-then-write: a long conversation is tens of megabytes
    of JSON and holding every record as Python objects to filter a handful of them
    costs an order of magnitude more memory than the file itself.

    The transformations are all one rule each. Every record's `sessionId` becomes the
    fork's, because a record claiming the source conversation inside the fork's file
    is two conversations disagreeing about which one it is. Sidecar paths are
    repointed and the files copied. A record naming a message the fork does not
    contain is dropped. A queued prompt is dropped. Titles are marked so the fork and
    its source are distinguishable in the CLI's own picker, which is also what keeps
    Claude's name-collision resolver from treating them as a clash to break.
    """
    source_dir = _claude_sidecar_dir(plan.source_path, plan.source_conversation_id)
    fork_dir = _claude_sidecar_dir(plan.target_path, plan.fork_conversation_id)
    roots = (str(source_dir), source_dir.as_posix())
    kept_uuids: set[str] = set()
    sidecar_names: set[str] = set()
    written = 0
    dropped = 0
    temporary = plan.target_path.with_name(
        f".{plan.target_path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    )
    plan.target_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with plan.source_path.open("rb") as handle, temporary.open("wb") as out:
            while line := handle.readline():
                if handle.tell() > plan.cut_offset:
                    break
                text = line.decode("utf-8", "replace")
                record = _decode_record(text)
                if record is None:
                    dropped += 1
                    continue
                transformed = _transform_claude_record(
                    record,
                    plan,
                    kept_uuids=kept_uuids,
                    # The conversation id appears in every reference to the source's
                    # own directory and in `sessionId`, so its absence proves the
                    # record needs no rewriting at all. A containment test on the raw
                    # line rather than on the decoded record, because re-serializing
                    # every record to ask the question costs more than the rewrite.
                    mentions_source=plan.source_conversation_id in text,
                    roots=roots,
                    fork_root=str(fork_dir),
                    sidecar_names=sidecar_names,
                )
                if transformed is None:
                    dropped += 1
                    continue
                out.write(json.dumps(transformed, ensure_ascii=False).encode("utf-8"))
                out.write(b"\n")
                written += 1
        copied = _copy_sidecars(source_dir, fork_dir, sidecar_names)
        size = temporary.stat().st_size
        os.replace(temporary, plan.target_path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return ForkOutcome(
        conversation_id=plan.fork_conversation_id,
        path=plan.target_path,
        records_written=written,
        records_dropped=dropped,
        attachments_copied=copied,
        bytes_written=size,
    )


def _decode_record(text: str) -> dict[str, Any] | None:
    try:
        record = json.loads(text)
    except json.JSONDecodeError:
        return None
    return record if isinstance(record, dict) else None


def _transform_claude_record(
    record: dict[str, Any],
    plan: ForkPlan,
    *,
    kept_uuids: set[str],
    mentions_source: bool,
    roots: tuple[str, ...],
    fork_root: str,
    sidecar_names: set[str],
) -> dict[str, Any] | None:
    kind = str(record.get("type") or "")
    if kind in _CLAUDE_DROPPED_TYPES:
        return None
    references = _CLAUDE_MESSAGE_REFERENCES.get(kind)
    if references and not all(
        str(record.get(field) or "") in kept_uuids
        for field in references
        if record.get(field) is not None
    ):
        return None
    if mentions_source:
        rewritten = _rewrite_sidecar_references(record, roots, fork_root, sidecar_names)
        assert isinstance(rewritten, dict)
        record = rewritten
        if record.get("sessionId"):
            record["sessionId"] = plan.fork_conversation_id
    for field in _CLAUDE_TITLE_FIELDS.get(kind, ()):
        if isinstance(record.get(field), str) and record[field]:
            record[field] = f"{record[field]} {plan.title_marker}"
    identity = record.get("uuid")
    if isinstance(identity, str) and identity:
        kept_uuids.add(identity)
    return record


def _copy_sidecars(source_dir: Path, fork_dir: Path, names: set[str]) -> int:
    """Copy the referenced oversized tool outputs into the fork's own directory.

    Best effort per file and deliberately so: a sidecar Claude's own housekeeping has
    already removed is a stale reference the source conversation shares, not a reason
    to fail a fork that is otherwise complete.
    """
    copied = 0
    for name in sorted(names):
        source = source_dir.joinpath(*Path(name).parts)
        if not source.is_file():
            continue
        target = fork_dir.joinpath(*Path(name).parts)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        except OSError as exc:
            log.warning("fork could not copy sidecar %s: %s", name, exc)
            continue
        copied += 1
    return copied


# The seam. A harness becomes forkable by adding its dialect here and a matching
# scanner in `transcript_view._OPEN_TOOL_SCANNERS`; nothing in the server, the API or
# the browser changes. Keyed on the record dialect rather than the harness name
# because two harnesses can share one format - pi and oh-my-pi already do - and a
# name-keyed table would need an entry per harness for one implementation.
_FORK_WRITERS: dict[str, Callable[[ForkPlan], ForkOutcome]] = {
    "claude": _write_claude_fork,
}

_ID_MINTERS: dict[str, Callable[[], str]] = {
    "claude": _mint_claude_conversation_id,
}
