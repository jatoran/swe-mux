"""The Mux assistant: a conversational operator over the workspace (Phase 10.6).

Design rules this module enforces structurally rather than by prompt:

- **Fallback tier, never the reflex path.** The deterministic voice grammar and
  the fuzzy pass run in the client; only an utterance neither matched reaches
  this service. Nothing here sits in front of a spoken command.
- **The model never emits an identifier and never executes.** Tools take
  project/session *names*; `resolve_session`/`resolve_project` map them onto
  live entities and answer ambiguity with candidates. Every side effect goes
  through an existing daemon operation (prompt queue, spawn contract, PTY
  interrupt, graceful end) behind the trust policy below.
- **Trust is enforced daemon-side per action class.** Reads execute; UI
  navigation dispatches to the originating device; reversible mutations follow
  `assistant_trust_reversible` (auto / cancel_window / confirm); consequential
  mutations always require an explicit confirmation and that floor is not
  configurable.
- **Dialog state is daemon-owned.** History, pending actions, and their expiry
  live in SQLite, so any device resumes the same conversation and a dropped tab
  cannot orphan a half-confirmed action.
- **Freshness is computed by the system.** Every status figure in the context
  snapshot carries an age derived from the session record, never model-asserted.

Failures are typed ``AssistantError`` and never touch PTY, session, transcript,
history, or project state.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import sqlite3
import time
import uuid
from collections.abc import Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeVar

from .config import Config
from .event_bus import EventBus
from .leaf_names import suggest_folder_name, validate_leaf_name
from .openrouter import OpenRouterClient, OpenRouterError
from .path_identity import same_path
from .projects import RESERVED_PROJECT_FOLDER_NAMES
from .session_titles import generated_titles, record_display_name, record_run_id
from .sqlite_store import (
    connect_or_quarantine,
    database_operation_lock,
    run_sqlite_operation,
)
from .transcript_view import final_exchange, transcript_message_page
from .voice import speechify

if TYPE_CHECKING:
    from .automation_store import AutomationStore
    from .projects import ProjectManager
    from .prompt_queue import PromptQueueService
    from .session import Session, SessionManager

log = logging.getLogger(__name__)

T = TypeVar("T")

ASSISTANT_RULE_ID = "builtin:assistant"
# One user turn may drive at most this many model calls (the first, plus one per
# round of tool results). A loop that has not settled by then is a runaway.
#
# Six was too few for any request naming more than one target: "open three
# sessions and stage a note in each" needs a read, three spawns and a closing
# reply, and the turn ran out mid-way having said nothing about it (measured
# 2026-08-20). The ceiling is a runaway guard, not a work budget, so it is set
# where a plausible multi-target task fits and spend is bounded by the daily
# budget check that runs before every call. Rounds are also no longer spent
# blindly: the model is told how many remain and asked to batch independent
# calls into one round, and exhausting them is reported rather than silent.
MAX_MODEL_CALLS_PER_TURN = 14
# Below this many remaining rounds the model is told to stop starting new work
# and summarize, so a turn lands on a sentence rather than on the ceiling.
MODEL_CALL_WARNING_ROUNDS = 3
# Pending confirmations decay on their own; nothing should stay armed because a
# tab closed. Cancel-window actions execute much sooner (see CANCEL_WINDOW).
CONFIRM_TTL_SECONDS = 120.0
CANCEL_WINDOW_SECONDS = 6.0
# The cancel window has to outlast the operator *learning about it*. On screen
# that is instant and 6 s is generous; spoken it is not, because the card's line
# has to be synthesized and then read out. A device that starts announcing a
# scheduled card restarts the window from that moment, so the window measures
# reaction time rather than synthesis time. Bounded from creation either way: an
# announcement cannot hold an action armed indefinitely.
CANCEL_WINDOW_SPOKEN_SECONDS = 10.0
CANCEL_WINDOW_MAX_SECONDS = 30.0
# Action ids whose announcement has already extended their window. Bounded only
# so a very long-lived daemon cannot accumulate them; a dialog never has enough
# cards for the cap to be reached in practice.
ANNOUNCED_MEMORY = 512
# A model that re-proposes an action it already ran (or that is still pending)
# would double-write a note. Within this window an identical proposal is
# answered with the existing action instead of a second card.
DUPLICATE_ACTION_WINDOW_SECONDS = 300.0
# How much of the dialog's action ledger the model is shown each turn.
CONTEXT_ACTION_LIMIT = 12
# Streamed text is released at a sentence boundary, or at this many characters
# when the model writes prose that never reaches one.
STREAM_SENTENCE_MAX_CHARS = 220
_STREAM_SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+|\n+")
# Kinds where repeating an already-executed action is itself the damage. A
# second identical note append writes the paragraph twice; a second identical
# spawn is a thing operators genuinely ask for, so it stays unguarded.
DUPLICATE_GUARDED_KINDS = {
    "write_project_note",
    "create_project",
    "send_to_session",
}
UI_ACK_TIMEOUT_SECONDS = 8.0
MAX_TURN_TEXT_CHARS = 8_000
MAX_TOOL_RESULT_CHARS = 12_000
MAX_CONTEXT_SESSIONS = 80
MAX_CLIENT_COMMANDS = 400

ACTION_CLASS_READ = "read"
ACTION_CLASS_NAVIGATION = "navigation"
ACTION_CLASS_REVERSIBLE = "reversible"
ACTION_CLASS_CONSEQUENTIAL = "consequential"

# Kinds the operator's device executes (UI focus, terminal typing, pane-placed
# spawns). Their action rows carry the originating client's id so exactly one
# device acts — an untargeted broadcast would type into every mounted copy of
# the pane and spawn once per open workspace.
CLIENT_EXECUTED_KINDS = {
    "run_ui_command",
    "type_into_session",
    "submit_session_composer",
    "spawn_session",
}

# Tools that change something. Counted per turn so the speech-suppression rule
# can tell "one card and nothing else" — where the card says it all — from a
# turn that also did work the operator has no other way to hear about.
MUTATION_KINDS = {
    "write_project_note",
    "send_to_session",
    "spawn_session",
    "interrupt_session",
    "end_session",
    "type_into_session",
    "submit_session_composer",
    "create_project",
}


@dataclass
class _QueuedTurn:
    """What the operator said while a turn was still running."""

    turn_id: str
    text: str
    client_context: dict[str, Any]


def _round_budget(remaining: int) -> str:
    """The per-round budget line the model plans against.

    Without it a turn spends rounds blindly and simply stops mid-task. Naming
    the number is what lets the model batch, skip a re-read, or wind up early
    with a sentence instead of hitting the ceiling silently.
    """
    if remaining <= MODEL_CALL_WARNING_ROUNDS:
        return (
            f"Tool rounds remaining this turn: {remaining}. Start no new work. "
            "Finish or abandon what is in progress and reply now, stating plainly "
            "what is done and what is not."
        )
    return (
        f"Tool rounds remaining this turn: {remaining}. Batch independent calls "
        "into one response, and do not re-read anything a tool already returned."
    )

SYSTEM_PRIMER = """You are Mux, the operator's assistant inside swe-mux, a fleet manager for \
coding-agent terminal sessions (Claude Code, Codex, and others) organized into Projects.
You operate the workspace; you never write code and never run shell commands. When the \
operator asks for code work, route it: offer to queue a message to an existing session or \
spawn a new one. When the work belongs in a project that does not exist yet, create_project \
makes and registers a new folder (inside the operator's configured location) and \
spawn_session can then start an agent in it.

Speak the short-response protocol: answer first, one or two plain sentences, detail only on \
request. No markdown, no bullet lists, no file paths unless essential. At most one \
clarifying question. Every status figure you state must come from the workspace snapshot or \
a tool result, never from memory; include the provided age qualifiers when a reading is stale.

Use tools for anything you cannot answer from the snapshot. Refer to sessions and projects \
by their names exactly as the snapshot spells them. If a tool reports ambiguity, ask the \
operator to choose. UI commands (focus, open tabs) run on the device the operator is \
speaking through; if none is connected the tool will say so.

Notes are a stack, not a log: write_project_note defaults to the top, under the note's \
leading headings, and that is what "add", "jot", "note this down" and even "append" mean \
from an operator. Never pass where="end" unless they explicitly said the end or the bottom. \
When they name a place — a section, "under Release", "next to the Tailscale bit" — use \
`section`, or `after`/`before` with a unique anchor span, or `at_line` with a number read \
off the numbered note view you are given. That view carries the note's outline and its \
first lines; call read_project_note with `from_line` when the place they mean is further \
down, and read it before writing whenever the position has to be exact.

A turn has a limited number of tool rounds and you are told how many remain. Spend them on \
work, not on repetition: everything a tool already returned this turn is still in front of \
you, and the action ledger lists what earlier turns did, so do not read the same thing \
twice. When a request names several targets — three sessions, two notes — emit all the \
independent tool calls in one response rather than one per round. If the rounds run low, \
stop starting new work and say plainly what is done and what is not.

spawn_session has two prompt parameters and they are not interchangeable. stage_text \
leaves the prompt waiting in the new session's composer WITHOUT sending it, so the \
operator reviews and presses Enter — it is what "put this in the chat without sending it" \
means. seed_text SUBMITS the prompt: the new agent starts running it immediately, with no \
chance to review. When the operator asks for text staged, input, or left unsent, use \
stage_text and never seed_text. type_into_session also stages text, but only into a \
session whose terminal is already open on the operator's device, so it is the wrong tool \
immediately after a spawn.

Confirmation is not yours to restate. A mutating tool that returns pending_confirmation has \
already put a card in front of the operator and their device reads that card out, so do not \
repeat what the card says. Everything else is still yours to report: what you did, what you \
could not do, what is left. Never claim a pending action happened, and never let brevity \
swallow a partial result or a failure — "I opened two of the three and ran out of rounds" is \
required, not optional. mode "confirm" means it runs only if the operator agrees; mode \
"cancel_window" means it runs on its own shortly unless they stop it, so do not ask them to \
confirm it. A result carrying duplicate or already_done means the operator has seen or \
accepted this exact action already: say so in one short sentence and do not propose it again.
To stage text in an EXISTING session's input without sending it, use type_into_session — \
repeated calls append, nothing reaches the agent, and the session's terminal must be open \
on the operator's device (focus it first with run_ui_command if needed). \
submit_session_composer presses Enter on that staged text and always confirms. Prefer \
this pair over send_to_session when the operator says "type", "enter", or "without \
sending"; for a session that does not exist yet, spawn_session with stage_text does the \
same thing in one call."""


class AssistantError(RuntimeError):
    """Typed, user-visible assistant failure. Never affects any session state."""


SCHEMA = """
CREATE TABLE IF NOT EXISTS assistant_dialogs (
    id TEXT PRIMARY KEY,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    title TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS assistant_messages (
    id TEXT PRIMARY KEY,
    dialog_id TEXT NOT NULL,
    turn_id TEXT NOT NULL,
    created_at REAL NOT NULL,
    role TEXT NOT NULL,
    display TEXT NOT NULL,
    speech TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'done',
    error TEXT,
    model TEXT,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd REAL
);
CREATE INDEX IF NOT EXISTS idx_assistant_messages_dialog
    ON assistant_messages(dialog_id, created_at);
CREATE TABLE IF NOT EXISTS assistant_actions (
    id TEXT PRIMARY KEY,
    dialog_id TEXT NOT NULL,
    turn_id TEXT NOT NULL,
    created_at REAL NOT NULL,
    kind TEXT NOT NULL,
    class TEXT NOT NULL,
    restatement TEXT NOT NULL,
    arguments TEXT NOT NULL,
    status TEXT NOT NULL,
    expires_at REAL,
    resolved_at REAL,
    result TEXT
);
CREATE INDEX IF NOT EXISTS idx_assistant_actions_dialog
    ON assistant_actions(dialog_id, created_at);
"""


# Where a note write lands. `top` is the default and the only one an operator
# gets without asking for it by name: a note is a stack of things you thought
# of, so the newest belongs where it will be read first. `end` exists because
# "put this at the bottom of Future" is a real request, but it is never inferred
# — see `NOTE_WRITE_TOOL_DESCRIPTION` and `restate_action`, which say the word
# END on the card so an unrequested one is visible before it runs.
NOTE_WRITE_POSITIONS = ("top", "end", "after", "before", "at_line", "replace")

NOTE_WRITE_TOOL_DESCRIPTION = (
    "Write text into a project note. `where` defaults to `top`, which puts the text "
    "at the top of the note UNDER its leading headings — that is what an operator "
    "means by add, jot, note this down, and by append. Do NOT use `end` unless they "
    "explicitly asked for the end or the bottom of something. `section` writes under "
    "a named heading (with `top` or `end`); `after`/`before` sit beside a unique "
    "`anchor` span; `at_line` makes the text become a 1-indexed line from the "
    "numbered note view; `replace` swaps a unique `find` span. Reversible; may need "
    "confirmation."
)

_ATX_HEADING = re.compile(r"^ {0,3}(#{1,6})(?:\s|$)")
_CODE_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
# How much of the note travels in every turn's context, and the ceiling on one
# on-demand page. The early chunk is what makes an insert point choosable
# without a tool round trip; the outline is what makes the rest addressable.
NOTE_CONTEXT_LINES = 60
# How far into a note a level-1 heading still counts as a buried title rather
# than a section that follows an introduction. See `_stranded_title`.
NOTE_TITLE_SEARCH_LINES = 40
NOTE_CONTEXT_LINE_CHARS = 200
NOTE_OUTLINE_LIMIT = 40
NOTE_PAGE_MAX_LINES = 240
NOTE_PAGE_MAX_CHARS = 8_000


def _fence_state(line: str, marker: str | None) -> str | None:
    """Track fenced-code state across one line.

    A `#` inside a fenced block is not a heading, and a note that pastes a shell
    transcript is exactly where that bites: without this the anchor scanner
    would treat a comment line as the note's structure and insert into the
    middle of someone's code sample.
    """
    match = _CODE_FENCE.match(line)
    if match is None:
        return marker
    token = match.group(1)
    if marker is None:
        return token
    # A fence closes only on the same character, at least as long as the opener.
    if token[0] == marker[0] and len(token) >= len(marker):
        return None
    return marker


def note_headings(markdown: str) -> list[dict[str, Any]]:
    """Every ATX heading in a note body, with 1-indexed lines and levels."""
    out: list[dict[str, Any]] = []
    marker: str | None = None
    for index, line in enumerate(markdown.split("\n")):
        previous = marker
        marker = _fence_state(line, marker)
        if previous is not None or marker is not None:
            continue
        match = _ATX_HEADING.match(line)
        if match is None:
            continue
        out.append(
            {
                "line": index + 1,
                "level": len(match.group(1)),
                "text": line.strip().lstrip("#").strip(),
            }
        )
    return out


def _heading_levels(markdown: str) -> dict[int, int]:
    """0-indexed line -> heading level, for the scanners below."""
    return {int(item["line"]) - 1: int(item["level"]) for item in note_headings(markdown)}


def _stranded_title(lines: list[str], levels: dict[int, int], start: int) -> int | None:
    """The index of an H1 that earlier writes buried under orphaned text.

    A note whose body opens with prose usually has a lead paragraph to respect.
    But the note this feature exists for opens with three dictated items sitting
    *above* `# swe-mux Notes`, because the old `prepend` wrote to byte 0 — and if
    `top` respects that, every new write stacks on the damage forever.

    The discriminator is that nobody writes prose above their own H1 on purpose:
    a level-1 heading close to the start, with nothing but non-heading text above
    it, is a title that got buried rather than a section that follows an
    introduction. The level and distance bounds are what keep this from firing on
    an all-prose note whose only heading is a `## Later` near the bottom.
    """
    for index in range(start, min(len(lines), start + NOTE_TITLE_SEARCH_LINES)):
        level = levels.get(index)
        if level is None:
            continue
        return index if level == 1 else None
    return None


def _consume_heading_run(lines: list[str], levels: dict[int, int], start: int) -> int:
    """Index one past a contiguous run of headings beginning at or after `start`.

    "Contiguous" tolerates blank lines between headings and nothing else, which
    is what makes `# swe-mux Notes` / `## Unsorted` one preamble and a heading
    with a paragraph under it a section boundary. Returns `start` unchanged when
    the first non-blank line is not a heading and no buried title explains why —
    a note that genuinely opens with prose has no preamble to slot under, and
    inventing one would bury the operator's own lead paragraph.
    """
    index = start
    while index < len(lines) and not lines[index].strip():
        index += 1
    if index >= len(lines) or index not in levels:
        # Only at the document top: inside a section the range is already cut at
        # the next same-or-shallower heading, so there is no title to strand.
        buried = _stranded_title(lines, levels, index) if start == 0 else None
        if buried is None:
            return start
        index = buried
    last = index
    probe = index + 1
    while probe < len(lines):
        if not lines[probe].strip():
            probe += 1
            continue
        if probe in levels:
            last = probe
            probe += 1
            continue
        break
    return last + 1


def resolve_note_section(markdown: str, section: str) -> dict[str, Any]:
    """Locate one section by heading text; raises AssistantError on ambiguity.

    Exact (case-folded) matches win outright, so a note holding both `## Release`
    and `## Release Notes` still resolves "Release". Only when nothing matches
    exactly does a substring match apply, and a substring that hits twice is an
    ambiguity to answer rather than a coin flip — the same rule `replace`
    already enforces on its find string.
    """
    wanted = " ".join(section.split()).casefold()
    headings = note_headings(markdown)
    if not wanted:
        raise AssistantError("the section name must not be empty")
    if not headings:
        raise AssistantError("this note has no headings to target by section")
    exact = [item for item in headings if str(item["text"]).casefold() == wanted]
    matches = exact or [
        item for item in headings if wanted in str(item["text"]).casefold()
    ]
    if not matches:
        names = ", ".join(str(item["text"]) for item in headings[:NOTE_OUTLINE_LIMIT])
        raise AssistantError(f'no section named "{section[:60]}"; this note has: {names}')
    if len(matches) > 1:
        lines = ", ".join(f'"{item["text"]}" (line {item["line"]})' for item in matches[:6])
        raise AssistantError(
            f'"{section[:60]}" matches {len(matches)} headings: {lines} — name one exactly'
        )
    found = matches[0]
    total = len(markdown.split("\n"))
    level = int(found["level"])
    end = total
    for item in headings:
        if int(item["line"]) > int(found["line"]) and int(item["level"]) <= level:
            end = int(item["line"]) - 1
            break
    return {
        "heading_line": int(found["line"]),
        "text": str(found["text"]),
        "level": level,
        # 0-indexed half-open body range, excluding the heading line itself.
        "body_start": int(found["line"]),
        "body_end": end,
    }


def _unique_span(markdown: str, needle: str, label: str) -> int:
    count = markdown.count(needle)
    if count == 0:
        raise AssistantError(f'"{needle[:80]}" was not found in the note')
    if count > 1:
        raise AssistantError(
            f'"{needle[:80]}" appears {count} times; give a longer, unique span for {label}'
        )
    return markdown.index(needle)


def _splice(lines: list[str], index: int, text: str) -> list[str]:
    """Insert a paragraph at a line index, keeping exactly one blank line each side.

    The seam is normalized rather than the whole note: a dictated paragraph that
    glues onto the next one is the difference between a note and a wall, and
    reflowing anything further would rewrite text the operator did not touch.
    """
    block = text.strip().split("\n")
    before = lines[:index]
    after = lines[index:]
    while before and not before[-1].strip():
        before.pop()
    while after and not after[0].strip():
        after.pop(0)
    result: list[str] = []
    if before:
        result.extend(before)
        result.append("")
    result.extend(block)
    if after:
        result.append("")
        result.extend(after)
    return result


def apply_note_write(markdown: str, payload: dict[str, Any]) -> str:
    """Apply one note write; raises AssistantError on refusal.

    Pure so the transform is testable without a project on disk. The whole point
    of this function is that `top` means *the top an operator would point at* —
    under the note's leading heading run, not above the title — because a voice
    note that lands above `# swe-mux Notes` orphans the heading and reads as a
    bug every single time.
    """
    where = str(payload.get("where") or "top")
    text = str(payload.get("text") or "")
    section = str(payload.get("section") or "").strip()
    if where not in NOTE_WRITE_POSITIONS:
        raise AssistantError(f"unknown note write position {where}")
    if not text.strip() and where != "replace":
        raise AssistantError("the text must not be empty")
    if where == "replace":
        find = str(payload.get("find") or "")
        if not find:
            raise AssistantError("replace needs the text to find")
        _unique_span(markdown, find, "replace")
        return markdown.replace(find, text)

    lines = markdown.split("\n")
    if where in {"after", "before"}:
        anchor = str(payload.get("anchor") or "")
        if not anchor:
            raise AssistantError(f"{where} needs an `anchor` — a unique span to sit beside")
        offset = _unique_span(markdown, anchor, where)
        start_line = markdown.count("\n", 0, offset)
        end_line = start_line + anchor.count("\n")
        index = end_line + 1 if where == "after" else start_line
        return "\n".join(_splice(lines, index, text)).rstrip("\n") + "\n"

    if where == "at_line":
        try:
            line = int(payload.get("line") or 0)
        except (TypeError, ValueError):
            line = 0
        if line < 1:
            raise AssistantError("at_line needs a 1-indexed line number")
        # Deliberately exact, unlike every other position: the model picks this
        # number off the numbered view it was handed, so the text has to *become*
        # that line. Pick a blank-line boundary and the paragraph still breathes.
        index = min(line - 1, len(lines))
        lines[index:index] = text.split("\n")
        return "\n".join(lines).rstrip("\n") + "\n"

    levels = _heading_levels(markdown)
    if section:
        found = resolve_note_section(markdown, section)
        body_start = int(found["body_start"])
        body_end = int(found["body_end"])
        if where == "top":
            index = _consume_heading_run(lines[:body_end], levels, body_start)
        else:
            index = body_end
            while index > body_start and not lines[index - 1].strip():
                index -= 1
    elif where == "top":
        index = _consume_heading_run(lines, levels, 0)
    else:
        index = len(lines)
        while index > 0 and not lines[index - 1].strip():
            index -= 1
    return "\n".join(_splice(lines, index, text)).rstrip("\n") + "\n"


def note_outline(markdown: str) -> list[str]:
    """The note's headings as `line: ## text`, for context and error messages."""
    return [
        f"{item['line']}: {'#' * int(item['level'])} {item['text']}"
        for item in note_headings(markdown)[:NOTE_OUTLINE_LIMIT]
    ]


def note_page(
    markdown: str, *, from_line: int = 1, max_lines: int = NOTE_CONTEXT_LINES
) -> dict[str, Any]:
    """A line-numbered window onto a note body.

    Numbered because a position is only choosable if it is nameable: handed a
    numbered early chunk plus the outline, the model can pick `at_line` or a
    section without spending a tool round trip discovering the note's shape,
    and can ask for a later window when the early one is not where the text
    belongs.
    """
    lines = markdown.split("\n")
    total = len(lines)
    start = max(1, int(from_line or 1))
    count = max(1, min(int(max_lines or NOTE_CONTEXT_LINES), NOTE_PAGE_MAX_LINES))
    window = lines[start - 1 : start - 1 + count]
    rendered: list[str] = []
    budget = NOTE_PAGE_MAX_CHARS
    shown = 0
    for offset, line in enumerate(window):
        trimmed = line[:NOTE_CONTEXT_LINE_CHARS]
        if len(line) > NOTE_CONTEXT_LINE_CHARS:
            trimmed += "…"
        entry = f"{start + offset}: {trimmed}"
        if budget - len(entry) < 0:
            break
        budget -= len(entry) + 1
        rendered.append(entry)
        shown += 1
    return {
        "total_lines": total,
        "from_line": start,
        "to_line": start + shown - 1 if shown else start - 1,
        "more": start + shown - 1 < total,
        "numbered": "\n".join(rendered),
    }


class _SentenceStreamer:
    """Turns a stream of model text deltas into complete sentences.

    A delta is not a speakable unit and half a sentence read aloud is worse than
    waiting, so text is held until a boundary arrives. The boundary requires the
    whitespace *after* the terminator to have been received, which is what keeps
    "3.5" and "e.g." from being cut mid-token. `STREAM_SENTENCE_MAX_CHARS` is the
    backstop for prose that never punctuates: it bounds how long the first sound
    can be delayed, which is the whole point of streaming.
    """

    def __init__(self, emit: Callable[[str], Awaitable[None]]) -> None:
        self._emit = emit
        self.buffer = ""
        self.emitted = False

    async def feed(self, delta: str) -> None:
        self.buffer += delta
        # unsupervised-loop-ok: drains one buffer of already-received text and
        # shrinks it on every pass; bounded by the delta just appended.
        while True:
            match = _STREAM_SENTENCE_BREAK.search(self.buffer)
            if match is not None:
                head, self.buffer = self.buffer[: match.start()], self.buffer[match.end():]
            elif len(self.buffer) >= STREAM_SENTENCE_MAX_CHARS:
                cut = self.buffer.rfind(" ", 0, STREAM_SENTENCE_MAX_CHARS)
                if cut <= 0:
                    return
                head, self.buffer = self.buffer[:cut], self.buffer[cut:].lstrip()
            else:
                return
            head = head.strip()
            if head:
                self.emitted = True
                await self._emit(head)

    async def flush(self) -> None:
        """Release the unterminated tail once the model has stopped writing."""
        tail = self.buffer.strip()
        self.buffer = ""
        if tail:
            self.emitted = True
            await self._emit(tail)


def split_sentences(text: str) -> list[str]:
    """Sentence boundaries for the streaming event contract and TTS chunking."""
    cleaned = re.sub(r"\s+", " ", text).strip()
    if not cleaned:
        return []
    return [part for part in re.split(r"(?<=[.!?])\s+", cleaned) if part]


def speech_form(display: str) -> str:
    """The separately paced spoken form of a reply (dual-form contract)."""
    return speechify(display, 2_000)


class AssistantStore:
    """SQLite persistence for dialogs, messages, and actions.

    Same single-worker confinement as `VoiceStore`: every sqlite3 call runs on
    one executor thread so nothing blocks the event loop.
    """

    _db: sqlite3.Connection

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._path = path
        self._operation_lock = database_operation_lock(path)
        self._closed = False
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mux-assistant-db")
        self._executor.submit(self._connect).result()

    def _open(self) -> sqlite3.Connection:
        db = sqlite3.connect(self._path, check_same_thread=False)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=NORMAL")
        db.execute("PRAGMA busy_timeout=5000")
        return db

    def _connect(self) -> None:
        with self._operation_lock:
            self._db = connect_or_quarantine(self._path, self._open)
            self._db.executescript(SCHEMA)
            # A daemon restart killed any in-flight confirmation machinery, so
            # what is still pending in the table can never execute; expire it
            # rather than leaving authority-shaped rows around.
            self._db.execute(
                "UPDATE assistant_actions SET status='expired', resolved_at=? "
                "WHERE status IN ('pending','scheduled','dispatched','executing')",
                (time.time(),),
            )
            self._db.commit()

    async def _run(self, fn: Callable[[], T]) -> T:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self._executor, run_sqlite_operation, self._db, self._operation_lock, fn
        )

    async def create_dialog(self, title: str = "") -> dict[str, Any]:
        row = {
            "id": str(uuid.uuid4()),
            "created_at": time.time(),
            "updated_at": time.time(),
            "title": title[:200],
        }

        def op() -> None:
            self._db.execute(
                "INSERT INTO assistant_dialogs(id,created_at,updated_at,title) "
                "VALUES(:id,:created_at,:updated_at,:title)",
                row,
            )
            self._db.commit()

        await self._run(op)
        return row

    async def dialogs(self, limit: int = 20) -> list[dict[str, Any]]:
        def op() -> list[dict[str, Any]]:
            return [
                dict(row)
                for row in self._db.execute(
                    "SELECT * FROM assistant_dialogs ORDER BY updated_at DESC LIMIT ?",
                    (max(1, min(limit, 100)),),
                ).fetchall()
            ]

        return await self._run(op)

    async def dialog(self, dialog_id: str) -> dict[str, Any] | None:
        def op() -> dict[str, Any] | None:
            row = self._db.execute(
                "SELECT * FROM assistant_dialogs WHERE id=?", (dialog_id,)
            ).fetchone()
            return dict(row) if row else None

        return await self._run(op)

    async def touch_dialog(self, dialog_id: str, *, title: str | None = None) -> None:
        def op() -> None:
            if title is not None:
                self._db.execute(
                    "UPDATE assistant_dialogs SET updated_at=?, title=? WHERE id=? AND title=''",
                    (time.time(), title[:200], dialog_id),
                )
            self._db.execute(
                "UPDATE assistant_dialogs SET updated_at=? WHERE id=?", (time.time(), dialog_id)
            )
            self._db.commit()

        await self._run(op)

    async def add_message(self, row: dict[str, Any]) -> None:
        def op() -> None:
            self._db.execute(
                "INSERT INTO assistant_messages"
                "(id,dialog_id,turn_id,created_at,role,display,speech,status,error,"
                "model,input_tokens,output_tokens,cost_usd) VALUES("
                ":id,:dialog_id,:turn_id,:created_at,:role,:display,:speech,:status,"
                ":error,:model,:input_tokens,:output_tokens,:cost_usd)",
                row,
            )
            self._db.commit()

        await self._run(op)

    async def messages(self, dialog_id: str, limit: int = 200) -> list[dict[str, Any]]:
        def op() -> list[dict[str, Any]]:
            rows = self._db.execute(
                "SELECT * FROM assistant_messages WHERE dialog_id=? "
                "ORDER BY created_at DESC LIMIT ?",
                (dialog_id, max(1, min(limit, 500))),
            ).fetchall()
            return [dict(row) for row in reversed(rows)]

        return await self._run(op)

    async def add_action(self, row: dict[str, Any]) -> None:
        def op() -> None:
            self._db.execute(
                "INSERT INTO assistant_actions"
                "(id,dialog_id,turn_id,created_at,kind,class,restatement,arguments,"
                "status,expires_at,resolved_at,result) VALUES("
                ":id,:dialog_id,:turn_id,:created_at,:kind,:class,:restatement,"
                ":arguments,:status,:expires_at,:resolved_at,:result)",
                row,
            )
            self._db.commit()

        await self._run(op)

    async def action(self, action_id: str) -> dict[str, Any] | None:
        def op() -> dict[str, Any] | None:
            row = self._db.execute(
                "SELECT * FROM assistant_actions WHERE id=?", (action_id,)
            ).fetchone()
            return dict(row) if row else None

        return await self._run(op)

    async def actions(self, dialog_id: str, limit: int = 100) -> list[dict[str, Any]]:
        def op() -> list[dict[str, Any]]:
            rows = self._db.execute(
                "SELECT * FROM assistant_actions WHERE dialog_id=? "
                "ORDER BY created_at DESC LIMIT ?",
                (dialog_id, max(1, min(limit, 200))),
            ).fetchall()
            return [dict(row) for row in reversed(rows)]

        return await self._run(op)

    async def claim_action(self, action_id: str, from_statuses: tuple[str, ...]) -> bool:
        """Compare-and-set to `executing`; exactly one claimant may win.

        The cancel-window timer and an explicit confirm can race; whichever
        claims the row executes, and the loser reads the final status instead
        of running the mutation a second time.
        """

        def op() -> bool:
            placeholders = ",".join("?" for _ in from_statuses)
            cursor = self._db.execute(
                f"UPDATE assistant_actions SET status='executing' "
                f"WHERE id=? AND status IN ({placeholders})",
                (action_id, *from_statuses),
            )
            self._db.commit()
            return cursor.rowcount == 1

        return await self._run(op)

    async def extend_action_window(
        self, action_id: str, expires_at: float
    ) -> dict[str, Any] | None:
        """Push a scheduled action's deadline out; never in.

        Guarded in SQL rather than in the caller because the cancel-window timer
        reads this row on every wake: a deadline that could move backwards would
        let a late announcement execute an action the operator has not heard
        about yet.
        """

        def op() -> dict[str, Any] | None:
            self._db.execute(
                "UPDATE assistant_actions SET expires_at=? "
                "WHERE id=? AND status='scheduled' AND expires_at < ?",
                (expires_at, action_id, expires_at),
            )
            self._db.commit()
            row = self._db.execute(
                "SELECT * FROM assistant_actions WHERE id=?", (action_id,)
            ).fetchone()
            return dict(row) if row else None

        return await self._run(op)

    async def resolve_action(
        self, action_id: str, *, status: str, result: str | None = None
    ) -> dict[str, Any] | None:
        def op() -> dict[str, Any] | None:
            self._db.execute(
                "UPDATE assistant_actions SET status=?, resolved_at=?, result=? WHERE id=?",
                (status, time.time(), result, action_id),
            )
            self._db.commit()
            row = self._db.execute(
                "SELECT * FROM assistant_actions WHERE id=?", (action_id,)
            ).fetchone()
            return dict(row) if row else None

        return await self._run(op)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._executor.submit(self._db.close).result()
        self._executor.shutdown(wait=True)


def restate_action(kind: str, arguments: dict[str, Any], *, spoken: bool = False) -> str:
    """One sentence naming exactly what a card would do.

    Two forms of the same statement. The written one carries a preview of the
    text being written, because the card is what the operator reads before
    confirming a note edit. The spoken one deliberately omits it: the preview is
    most of the characters, synthesis time tracks characters, and the operator
    hearing "append to the swe-mux project note" already knows which proposal
    the visible card describes. Reading a note body aloud to announce that a
    note is about to be written is the slowest possible way to say nothing new.
    """
    text = str(
        arguments.get("text") or arguments.get("stage_text") or arguments.get("seed_text") or ""
    )
    preview = "" if spoken else (
        f' "{text[:120]}{"…" if len(text) > 120 else ""}"' if text else ""
    )
    target = str(arguments.get("session") or arguments.get("project") or "")
    if kind == "send_to_session":
        mode = "send" if arguments.get("deliver") else "queue a draft"
        return f"{mode} to {target}:{preview}" if preview else f"{mode} to {target}"
    if kind == "spawn_session":
        backend = str(arguments.get("backend") or "default harness")
        # The card is where the operator learns whether the prompt will run or
        # wait: the two parameters differ in exactly that, so the card says it.
        if str(arguments.get("stage_text") or ""):
            return f"spawn a {backend} session in {target}, prompt staged unsent{preview}"
        if str(arguments.get("seed_text") or ""):
            return f"spawn a {backend} session in {target}, running the prompt{preview}"
        return f"spawn a {backend} session in {target}{preview}"
    if kind == "create_project":
        # The absolute path is the whole point of this card: the operator
        # confirms exactly what lands on disk, not a name they must resolve.
        # Spoken, the path is read back as a string of separators, so the name
        # and the git note carry it and the card shows where.
        name = str(arguments.get("name") or "")
        root = str(arguments.get("root") or "")
        where = "" if spoken else (f" at {root}" if root else "")
        git_note = (
            " and initialize an empty git repository" if arguments.get("git") else ""
        )
        revive = ""
        if arguments.get("restores"):
            revive = (
                f' (restores the removed project "{arguments["restores"]}" with '
                "its history and settings)"
            )
        return f'create the new project "{name}"{where}{git_note}{revive}'
    if kind == "write_project_note":
        note = str(arguments.get("note") or "primary")
        where = str(arguments.get("where") or "top")
        section = str(arguments.get("section") or "").strip()
        destination = f"the {target} project's {note} note"
        # The position is the one detail the spoken form keeps. It is what the
        # operator would have to undo by hand, and "at the very END" is exactly
        # the choice they need to hear in time to cancel it.
        if where == "replace":
            find = str(arguments.get("find") or "")[:80]
            if spoken:
                return f"replace a span in {destination}"
            return f'replace "{find}" in {destination} with{preview}'
        if where == "at_line":
            place = f"at line {arguments.get('line')} of {destination}"
        elif where in {"after", "before"}:
            anchor = str(arguments.get("anchor") or "")[:60]
            place = (
                f"{where} a span in {destination}"
                if spoken
                else f'{where} "{anchor}" in {destination}'
            )
        elif where == "end":
            place = (
                f"at the very END of {destination}'s {section} section"
                if section
                else f"at the very END of {destination}"
            )
        else:
            place = (
                f"at the top of {destination}'s {section} section"
                if section
                else f"at the top of {destination}"
            )
        return f"add {place}:{preview}" if preview else f"add {place}"
    # Retired kinds still sit in stored ledgers; a card the operator already
    # confirmed should not degrade into a bare tool name months later.
    if kind in {"append_project_note", "edit_project_note"}:
        return f"write to the {target} project note:{preview}" if preview else (
            f"write to the {target} project note"
        )
    if kind == "interrupt_session":
        return f"interrupt the agent in {target}"
    if kind == "end_session":
        return f"end the session {target}"
    if kind == "run_ui_command":
        return f"run the UI command: {arguments.get('command')}"
    if kind == "type_into_session":
        if spoken:
            return f"type into {target}'s composer without sending"
        return f"type into {target}'s composer without sending:{preview}"
    if kind == "submit_session_composer":
        return f"press Enter on {target}'s composer, sending its staged text"
    return kind


def action_announcement(kind: str, arguments: dict[str, Any], status: str) -> str:
    """The spoken line for a card, built once by the daemon.

    It is built here rather than in the client because its wording is the trust
    policy talking: a `scheduled` card runs on its own and can only be stopped,
    a `pending` one runs only if the operator says so, and a client that mixed
    the two would tell the operator to confirm something that already happened.
    """
    speech = restate_action(kind, arguments, spoken=True).strip()
    line = f"{speech[0].upper()}{speech[1:]}" if speech else "An action is proposed"
    if status == "scheduled":
        return f"{line}. Say cancel to stop it."
    return f"{line}. Confirm or cancel?"


def action_snapshot(row: dict[str, Any]) -> dict[str, Any]:
    public = dict(row)
    try:
        public["arguments"] = json.loads(str(row.get("arguments") or "{}"))
    except ValueError:
        public["arguments"] = {}
    public["action_class"] = public.pop("class", None)
    kind = str(row.get("kind") or "")
    status = str(row.get("status") or "")
    # The spoken form travels with the card so the announcement the operator
    # hears and the card they see cannot drift apart, and so a client never has
    # to reconstruct trust-policy wording it does not own.
    public["announcement"] = (
        action_announcement(kind, public["arguments"], status)
        if status in {"pending", "scheduled"}
        else ""
    )
    return public


class AssistantService:
    """Dialog turns, the tool bridge, and the trust policy."""

    def __init__(
        self,
        config: Config,
        events: EventBus,
        sessions: SessionManager,
        projects: ProjectManager,
        store: AssistantStore,
        automation_store: AutomationStore,
        provider: OpenRouterClient,
        *,
        prompt_queue: PromptQueueService,
        spawn_op: Callable[[dict[str, Any]], Awaitable[Any]],
        interrupt_op: Callable[[Session], Awaitable[Any]],
        end_op: Callable[[Session, str], Awaitable[Any]],
        history_search: Callable[..., Awaitable[dict[str, Any]]] | None = None,
        note_read: Callable[[str, str | None], Awaitable[dict[str, Any]]] | None = None,
        note_list: Callable[[str], Awaitable[list[dict[str, Any]]]] | None = None,
        note_write: Callable[
            [str, str | None, dict[str, Any]], Awaitable[dict[str, Any]]
        ] | None = None,
        create_project_op: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]] | None = None,
    ) -> None:
        self.config = config
        self.events = events
        self.sessions = sessions
        self.projects = projects
        self.store = store
        self.automation_store = automation_store
        self.provider = provider
        self.prompt_queue = prompt_queue
        self.spawn_op = spawn_op
        self.interrupt_op = interrupt_op
        self.end_op = end_op
        self.history_search = history_search
        self.note_read = note_read
        self.note_list = note_list
        self.note_write = note_write
        self.create_project_op = create_project_op
        self.diagnostic: str | None = None
        # One turn at a time per dialog; concurrent turns would interleave the
        # message log and race the pending-action state.
        self._dialog_locks: dict[str, asyncio.Lock] = {}
        self._turn_tasks: dict[str, asyncio.Task[None]] = {}
        self._interrupts: set[str] = set()
        # UI actions wait here for the originating device's acknowledgement.
        self._ui_acks: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._window_tasks: dict[str, asyncio.Task[None]] = {}
        # Cards whose spoken announcement has already moved their cancel window.
        self._announced: set[str] = set()
        # At most one waiting turn per dialog, holding what the operator said
        # while a turn was running. Never a list: consecutive arrivals merge,
        # because two breaths of one thought are one request.
        self._queued: dict[str, _QueuedTurn] = {}
        self._queue_starters: set[asyncio.Task[None]] = set()

    # ------------------------------------------------------------------ status

    async def status(self) -> dict[str, Any]:
        spend = await self.automation_store.spend(rule_id=ASSISTANT_RULE_ID)
        return {
            "enabled": self.config.assistant_enabled,
            "model": self.config.assistant_model,
            "daily_budget_usd": self.config.assistant_daily_budget_usd,
            "spend_today": spend,
            "trust_reversible": self.config.assistant_trust_reversible,
            "diagnostic": self.diagnostic,
        }

    # ------------------------------------------------------------- turn intake

    def turn_running(self, dialog_id: str) -> bool:
        task = self._turn_tasks.get(dialog_id)
        return task is not None and not task.done()

    def turn_queued(self, turn_id: str) -> bool:
        """Whether this turn is waiting behind a running one rather than running.

        Read once, right after intake, so the caller can tell the operator their
        words were accepted-but-waiting instead of answered. A turn that starts
        between the two calls simply reports False, which is equally true.
        """
        return any(item.turn_id == turn_id for item in self._queued.values())

    async def start_turn(
        self, dialog_id: str, text: str, client_context: dict[str, Any] | None
    ) -> str:
        """Validate, record the user message, and run the turn in the background.

        The response carries only the turn id; everything else arrives over the
        event stream so every connected device renders the same turn.
        """
        if not self.config.assistant_enabled:
            raise AssistantError("the assistant is disabled; enable it in Settings → Assistant")
        body = text.strip()
        if not body or len(body) > MAX_TURN_TEXT_CHARS:
            raise AssistantError(f"a turn must contain 1-{MAX_TURN_TEXT_CHARS} characters")
        dialog = await self.store.dialog(dialog_id)
        if dialog is None:
            raise AssistantError("unknown dialog")
        if self.turn_running(dialog_id):
            return await self._queue_turn(dialog_id, body, client_context or {})
        return await self.start_turn_now(
            dialog_id, str(uuid.uuid4()), body, client_context or {}
        )

    async def start_turn_now(
        self, dialog_id: str, turn_id: str, body: str, client_context: dict[str, Any]
    ) -> str:
        """Record the user message and run the turn, under an id chosen already.

        Split out so a queued turn keeps the id its `assistant_turn_queued`
        event already announced: the client rendered the operator's words under
        that id and must not see them appear a second time under another.
        """
        await self.store.add_message(
            {
                "id": str(uuid.uuid4()),
                "dialog_id": dialog_id,
                "turn_id": turn_id,
                "created_at": time.time(),
                "role": "user",
                "display": body,
                "speech": "",
                "status": "done",
                "error": None,
                "model": None,
                "input_tokens": 0,
                "output_tokens": 0,
                "cost_usd": None,
            }
        )
        await self.store.touch_dialog(dialog_id, title=body[:80])
        self._interrupts.discard(dialog_id)
        task = asyncio.create_task(
            self._run_turn(dialog_id, turn_id, body, client_context),
            name=f"assistant-turn-{turn_id}",
        )
        self._turn_tasks[dialog_id] = task
        task.add_done_callback(lambda done: self._turn_finished(dialog_id, done))
        await self.events.emit(
            "assistant_turn_started",
            source="assistant",
            dialog_id=dialog_id,
            turn_id=turn_id,
            text=body,
        )
        return turn_id

    async def _queue_turn(
        self, dialog_id: str, body: str, client_context: dict[str, Any]
    ) -> str:
        """Hold an utterance that arrived while a turn was running.

        Refusing it was the old behaviour and the client had nowhere to put the
        refusal, so speaking over the assistant simply lost what you said. It is
        also why one sentence could end up split across two dialogs: the first
        fragment was refused here and the rest opened a new conversation.

        Consecutive arrivals coalesce into one waiting turn rather than becoming
        several, because someone completing a thought in two breaths means one
        request, and answering the first half before the second exists is the
        failure the brainstorm hold already exists to avoid.
        """
        waiting = self._queued.get(dialog_id)
        if waiting is not None:
            merged = f"{waiting.text} {body}".strip()[:MAX_TURN_TEXT_CHARS]
            waiting.text = merged
            waiting.client_context = client_context or waiting.client_context
            await self.events.emit(
                "assistant_turn_queued", source="assistant", dialog_id=dialog_id,
                turn_id=waiting.turn_id, text=merged, merged=True,
            )
            log.info(
                "assistant turn merged into the waiting one dialog=%s turn=%s chars=%d",
                dialog_id, waiting.turn_id, len(merged),
            )
            return waiting.turn_id
        turn_id = str(uuid.uuid4())
        self._queued[dialog_id] = _QueuedTurn(turn_id, body, client_context)
        await self.events.emit(
            "assistant_turn_queued", source="assistant", dialog_id=dialog_id,
            turn_id=turn_id, text=body, merged=False,
        )
        log.info(
            "assistant turn queued behind a running one dialog=%s turn=%s",
            dialog_id, turn_id,
        )
        return turn_id

    def _turn_finished(self, dialog_id: str, task: asyncio.Task[None]) -> None:
        if self._turn_tasks.get(dialog_id) is task:
            self._turn_tasks.pop(dialog_id, None)
        # Drained even when the turn was cancelled or failed: an interrupted
        # turn is usually interrupted *by* the operator saying the thing that is
        # now waiting, so that is exactly when it must run.
        if self._queued.get(dialog_id) is not None:
            self._start_queued(dialog_id)
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            log.error("assistant turn task failed dialog=%s", dialog_id, exc_info=error)

    def _start_queued(self, dialog_id: str) -> None:
        waiting = self._queued.pop(dialog_id, None)
        if waiting is None:
            return

        async def run() -> None:
            try:
                await self.start_turn_now(
                    dialog_id, waiting.turn_id, waiting.text, waiting.client_context
                )
            except (AssistantError, OpenRouterError) as exc:
                log.warning(
                    "assistant queued turn could not start dialog=%s turn=%s error=%s",
                    dialog_id, waiting.turn_id, str(exc)[:200],
                )
                await self.events.emit(
                    "assistant_turn_failed", source="assistant", dialog_id=dialog_id,
                    turn_id=waiting.turn_id, error=str(exc)[:500],
                )

        task = asyncio.create_task(run(), name=f"assistant-queued-{waiting.turn_id}")
        self._queue_starters.add(task)
        task.add_done_callback(self._queue_starters.discard)

    def interrupt(self, dialog_id: str) -> bool:
        """Stop the running turn after its current step; nothing external is undone."""
        self._interrupts.add(dialog_id)
        task = self._turn_tasks.get(dialog_id)
        if task is not None and not task.done():
            task.cancel()
            return True
        return False

    async def stop(self) -> None:
        # Waiting turns are dropped rather than started into a shutting-down
        # service; a restart expires every pending action anyway.
        self._queued.clear()
        for task in [
            *self._turn_tasks.values(), *self._window_tasks.values(),
            *self._queue_starters,
        ]:
            task.cancel()
        pending = [*self._turn_tasks.values(), *self._window_tasks.values()]
        self._turn_tasks.clear()
        self._window_tasks.clear()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    # --------------------------------------------------------------- resolution

    def resolve_project(self, reference: str) -> tuple[Any | None, list[str]]:
        needle = reference.strip().casefold()
        if not needle:
            return None, []
        records = list(self.projects.projects.values())
        exact = [
            record
            for record in records
            if record.id == reference or record.name.casefold() == needle
        ]
        if len(exact) == 1:
            return exact[0], []
        partial = [record for record in records if needle in record.name.casefold()]
        if len(partial) == 1:
            return partial[0], []
        return None, [record.name for record in (exact or partial)][:6]

    def _live_sessions(self) -> list[Session]:
        return [
            session
            for session in self.sessions.sessions.values()
            if session.record.state not in {"exited", "crashed"} and not session.record.cold
        ]

    async def _display_names(self, sessions: list[Session]) -> dict[str, str]:
        """Display name per session id — the same rule every UI surface applies.

        A generated title wins only while the session is still auto-named; the
        model sees these names in the snapshot, so resolution must accept them
        too, or the assistant quotes a name it then cannot act on.
        """
        run_ids = {record_run_id(session.record) for session in sessions}
        try:
            titles = await generated_titles(self.automation_store, run_ids)
        except Exception:  # noqa: BLE001 - a title lookup failure must not fail a turn
            log.warning("assistant title lookup failed", exc_info=True)
            titles = {}
        return {
            session.record.id: record_display_name(session.record, titles)
            for session in sessions
        }

    async def resolve_session(self, reference: str) -> tuple[Session | None, list[str]]:
        """Resolve by id, spawn name, or display title — exact first, then unique substring."""
        needle = reference.strip().casefold()
        if not needle:
            return None, []
        live = self._live_sessions()
        display = await self._display_names(live)

        def names(session: Session) -> set[str]:
            return {
                session.record.name.casefold(),
                display.get(session.record.id, "").casefold(),
            }

        exact = [
            session
            for session in live
            if session.record.id == reference or needle in names(session)
        ]
        if len(exact) == 1:
            return exact[0], []
        partial = [
            session
            for session in live
            if any(needle in name for name in names(session) if name)
        ]
        if len(partial) == 1:
            return partial[0], []
        return None, [
            f"{display.get(session.record.id, session.record.name)} "
            f"(project {self._project_name(session.record.project_id)})"
            for session in (exact or partial)
        ][:6]

    def _project_name(self, project_id: str | None) -> str:
        if not project_id:
            return "none"
        record = self.projects.projects.get(project_id)
        return record.name if record else "unknown"

    # ----------------------------------------------------------------- context

    @staticmethod
    def _age(now: float, since: float | None) -> str | None:
        if not since:
            return None
        seconds = max(0.0, now - since)
        if seconds < 90:
            return f"{int(seconds)}s"
        if seconds < 5400:
            return f"{int(seconds / 60)}m"
        return f"{seconds / 3600:.1f}h"

    async def fleet_snapshot(self) -> dict[str, Any]:
        """The compact read model every turn carries.

        Assembled from the same session records the UI renders — including the
        same display-name rule, so the assistant never quotes a spawn id at a
        session the operator knows by its generated title. Ages are computed
        here so no freshness claim is ever the model's own.
        """
        now = time.time()
        projects = [
            {"name": record.name, "id": record.id}
            for record in self.projects.ordered_projects()
        ]
        live = self._live_sessions()
        cold = [
            session
            for session in self.sessions.sessions.values()
            if session.record.cold
        ]
        display = await self._display_names([*live, *cold])
        rows: list[dict[str, Any]] = []
        for session in [*live, *cold]:
            record = session.record
            entry: dict[str, Any] = {
                "name": display.get(record.id, record.name),
                "project": self._project_name(record.project_id),
                "backend": record.backend,
                "state": record.state,
                "state_age": self._age(now, record.state_since or None),
            }
            if record.awaiting_reason:
                entry["awaiting"] = record.awaiting_reason
            if record.idle_reason:
                entry["idle_reason"] = record.idle_reason
            if record.cold:
                entry["cold"] = True
            if record.turn_started_at:
                entry["turn_running_for"] = self._age(now, record.turn_started_at)
            if record.running_work_since:
                # The same blind spot the sidebar had. A harness that dispatches
                # background agents ends its root turn to hand off, so `state:
                # idle` with no `turn_running_for` is the shape of a session an
                # hour into a request — and answering "how long has that been
                # going" from `state_age` alone reports the hand-off instead.
                entry["running_work_for"] = self._age(now, record.running_work_since)
            if record.model:
                entry["model"] = record.model
            rows.append(entry)
            if len(rows) >= MAX_CONTEXT_SESSIONS:
                break
        return {"projects": projects, "sessions": rows, "captured_at": now}

    async def _action_ledger(self, dialog_id: str) -> str:
        """What this dialog has already proposed and what became of it.

        The message log alone cannot answer that: a confirmation is a button or
        a spoken word, never a turn, so nothing in the transcript records that
        the operator said yes. Without this the model reads its own last message
        ("say confirm") as still-unanswered and proposes the write a second
        time, which for a note means the paragraph lands twice.
        """
        # Fetched wider than it is shown, then filtered, then trimmed: reads and
        # in-flight dispatches are the bulk of a busy dialog's ledger, and
        # trimming first would leave the mutations the model needs off the end.
        rows = await self.store.actions(dialog_id, limit=CONTEXT_ACTION_LIMIT * 6)
        if not rows:
            return ""
        now = time.time()
        lines: list[str] = []
        for row in rows:
            status = str(row["status"])
            if status == "dispatched":
                continue
            if str(row["class"]) in {ACTION_CLASS_READ, ACTION_CLASS_NAVIGATION}:
                continue
            age = max(0, int(now - float(row["created_at"])))
            detail = str(row["restatement"])[:160]
            if status in {"pending", "scheduled"}:
                lines.append(f"- awaiting the operator ({age}s ago): {detail}")
            else:
                lines.append(f"- {status} ({age}s ago): {detail}")
        if not lines:
            return ""
        return (
            "Actions already proposed in this conversation (system-computed; an "
            "`executed` line means it is done and must not be run again, and an "
            "`awaiting` line must not be proposed again):\n"
            + "\n".join(lines[-CONTEXT_ACTION_LIMIT:])
        )

    async def _note_context(self, project: Any) -> str:
        """The primary note's outline and opening lines, numbered, for one project.

        Carried every turn rather than fetched on demand because the common
        request — "jot this down" — is one tool call only if the model already
        knows the note's shape. Without it the model either burns a round trip
        reading the note or writes blind, and writing blind is how text ends up
        above the note's own title. Scoped to the focused session's project, or
        to the only project when there is exactly one; guessing among several
        would hand the model an outline for a note the operator did not mean.
        """
        if self.note_read is None:
            return ""
        if project is None:
            ordered = self.projects.ordered_projects()
            if len(ordered) != 1:
                return ""
            project = ordered[0]
        try:
            note = await self.note_read(project.id, None)
        except Exception:  # noqa: BLE001 - context must never fail a turn
            log.debug("note context read failed", exc_info=True)
            return ""
        if note.get("error") or not str(note.get("markdown") or "").strip():
            return ""
        markdown = str(note["markdown"])
        page = note_page(markdown, from_line=1, max_lines=NOTE_CONTEXT_LINES)
        outline = note_outline(markdown)
        lines = [
            f"The {project.name} project's primary note "
            f'("{note.get("title")}", {page["total_lines"]} lines), as numbered lines. '
            "Line numbers are the ones write_project_note's at_line takes; "
            "read_project_note with from_line pages further down.",
        ]
        if outline:
            lines.append("Outline: " + " | ".join(outline))
        lines.append(page["numbered"])
        if page["more"]:
            lines.append(
                f"… lines {page['to_line'] + 1}-{page['total_lines']} not shown; "
                f"read_project_note from_line={page['to_line'] + 1} for more."
            )
        return "\n".join(lines)

    async def _context_message(
        self, client_context: dict[str, Any], dialog_id: str = ""
    ) -> str:
        snapshot = await self.fleet_snapshot()
        focused = str(client_context.get("focused_session_id") or "")
        focused_name = None
        focused_project = None
        if focused:
            session = self.sessions.sessions.get(focused)
            if session is not None:
                names = await self._display_names([session])
                focused_name = names.get(session.record.id, session.record.name)
                focused_project = self.projects.projects.get(session.record.project_id)
        commands = client_context.get("commands")
        command_lines: list[str] = []
        if isinstance(commands, list):
            for item in commands[:MAX_CLIENT_COMMANDS]:
                if isinstance(item, dict) and item.get("label"):
                    command_lines.append(str(item["label"])[:80])
        parts = [
            "Workspace snapshot (system-computed, ages relative to now):",
            json.dumps(snapshot, ensure_ascii=False),
        ]
        if focused_name:
            parts.append(f"The operator's focused session is: {focused_name}")
        note_view = await self._note_context(focused_project)
        if note_view:
            parts.append(note_view)
        parts.append(
            "run_ui_command executes on the operator's device through the workspace's "
            "deterministic spoken grammar. Reliable command shapes: 'open project "
            "<name>', 'open session <name>' (a name from the snapshot), 'go to next "
            "session', 'open the <Notes|Queue|Git|Transcript|Actions|Insight> tab', "
            "'list voice commands', 'fleet status'. Prefer these shapes over free "
            "paraphrase."
        )
        if command_lines:
            parts.append(
                "Additional UI command labels available on the operator's device: "
                + "; ".join(command_lines)
            )
        if dialog_id:
            ledger = await self._action_ledger(dialog_id)
            if ledger:
                parts.append(ledger)
        return "\n".join(parts)

    # ------------------------------------------------------------------- tools

    def _tool_definitions(self) -> list[dict[str, Any]]:
        def tool(
            name: str, description: str, properties: dict[str, Any], required: list[str]
        ) -> dict[str, Any]:
            return {
                "type": "function",
                "function": {
                    "name": name,
                    "description": description,
                    "parameters": {
                        "type": "object",
                        "properties": properties,
                        "required": required,
                        "additionalProperties": False,
                    },
                },
            }

        session_property = {"type": "string", "description": "The session's name from the snapshot"}
        project_property = {"type": "string", "description": "The project's name from the snapshot"}
        return [
            tool(
                "session_detail",
                "Full status detail for one session plus its latest prompt and reply.",
                {"session": session_property},
                ["session"],
            ),
            tool(
                "read_transcript",
                "The last N messages of a session's conversation, oldest first.",
                {
                    "session": session_property,
                    "messages": {"type": "integer", "minimum": 1, "maximum": 30},
                },
                ["session"],
            ),
            tool(
                "search_history",
                "Search every archived conversation across all harnesses.",
                {
                    "query": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 10},
                },
                ["query"],
            ),
            tool(
                "list_project_notes",
                "List a project's notes: titles, sizes, and update times.",
                {"project": project_property},
                ["project"],
            ),
            tool(
                "read_project_note",
                "Read one of a project's notes as numbered lines, plus its heading "
                "outline. Omit `note` for the primary note; pass `from_line` to page "
                "further down a long note.",
                {
                    "project": project_property,
                    "note": {
                        "type": "string",
                        "description": "The note's title from list_project_notes",
                    },
                    "from_line": {
                        "type": "integer",
                        "minimum": 1,
                        "description": "1-indexed first line to return (default 1)",
                    },
                    "max_lines": {
                        "type": "integer",
                        "minimum": 1,
                        "description": f"Lines to return, up to {NOTE_PAGE_MAX_LINES}",
                    },
                },
                ["project"],
            ),
            tool(
                "list_queue",
                "Prompt-queue state: per-session pending counts, or one session's "
                "queued messages when `session` is given.",
                {"session": {**session_property, "description": "Optional session name"}},
                [],
            ),
            tool(
                "write_project_note",
                NOTE_WRITE_TOOL_DESCRIPTION,
                {
                    "project": project_property,
                    "note": {
                        "type": "string",
                        "description": "Note title; omit for the primary note",
                    },
                    "where": {
                        "type": "string",
                        "enum": list(NOTE_WRITE_POSITIONS),
                        "description": (
                            "Default top. Use end ONLY when the operator said the "
                            "end or the bottom."
                        ),
                    },
                    "section": {
                        "type": "string",
                        "description": (
                            "Heading to write under, from the note outline; "
                            "combines with top or end"
                        ),
                    },
                    "line": {
                        "type": "integer",
                        "minimum": 1,
                        "description": "For at_line: the line the text becomes",
                    },
                    "anchor": {
                        "type": "string",
                        "description": "For after/before: a unique span to sit beside",
                    },
                    "find": {
                        "type": "string",
                        "description": "For replace: a unique span to replace",
                    },
                    "text": {"type": "string"},
                },
                ["project", "text"],
            ),
            tool(
                "send_to_session",
                "Queue a message to a session. deliver=false stages an inert draft the "
                "operator releases; deliver=true arms it for delivery when the session is "
                "ready (never mid-turn).",
                {
                    "session": session_property,
                    "text": {"type": "string"},
                    "deliver": {"type": "boolean"},
                },
                ["session", "text"],
            ),
            tool(
                "create_project",
                "Create a brand-new project: makes one new folder (named from `name`) "
                "inside the operator's configured new-project location, then registers "
                "it. Use before spawn_session when the operator wants work in a project "
                "that does not exist yet. Setup commands never run here. Reversible; "
                "may need confirmation. Fails with guidance when no new-project "
                "location is configured.",
                {
                    "name": {
                        "type": "string",
                        "description": (
                            "The project's name; the folder name is derived from it "
                            "deterministically (spaces become hyphens)"
                        ),
                    },
                    "git": {
                        "type": "boolean",
                        "description": (
                            "Also initialize an empty git repository (no commits are "
                            "made). Recommended when coding sessions will work there."
                        ),
                    },
                },
                ["name"],
            ),
            tool(
                "spawn_session",
                "Start a new agent session in a project — empty, with a prompt it "
                "immediately runs (seed_text), or with a prompt staged in its "
                "composer but not sent (stage_text).",
                {
                    "project": project_property,
                    "backend": {
                        "type": "string",
                        "description": (
                            "Harness name, e.g. claude or codex; omit for the project default"
                        ),
                    },
                    # This parameter was first documented (2026-08-20) as staging
                    # without sending, after reading its summary instead of tracing
                    # its delivery path; the seed is an argv prompt the CLI runs.
                    # Three sessions were spawned with their prompts already
                    # submitted while the operator asked for them left unsent. The
                    # description now states what the code does, and stage_text
                    # below is the real stage-without-send path.
                    "seed_text": {
                        "type": "string",
                        "description": (
                            "A first prompt the new agent starts RUNNING immediately "
                            "— this submits it, with no chance for the operator to "
                            "review. Use stage_text instead whenever they want the "
                            "text left unsent. Omit both for an empty session."
                        ),
                    },
                    "stage_text": {
                        "type": "string",
                        "description": (
                            "Text left waiting in the new session's composer WITHOUT "
                            "sending it, so the operator can review, edit, and press "
                            "Enter themselves. This is what 'put this in the chat "
                            "without sending it' means. Cannot be combined with "
                            "seed_text."
                        ),
                    },
                },
                ["project"],
            ),
            tool(
                "interrupt_session",
                "Send an interrupt (Ctrl-C) to a session's agent. Always requires confirmation.",
                {"session": session_property},
                ["session"],
            ),
            tool(
                "end_session",
                "Gracefully end a session. Always requires confirmation.",
                {"session": session_property},
                ["session"],
            ),
            tool(
                "run_ui_command",
                "Run a UI command (focus a session or project, open a drawer tab, open a "
                "panel) on the operator's current device, by its label.",
                {
                    "command": {
                        "type": "string",
                        "description": "The command label or a close paraphrase",
                    }
                },
                ["command"],
            ),
            tool(
                "type_into_session",
                "Type text into a session's input composer on the operator's device "
                "WITHOUT sending it. Repeated calls append to what is already staged "
                "(include your own joining space or newline); nothing reaches the "
                "agent until the operator presses Enter or submit_session_composer "
                "runs. The session's terminal must be open on the operator's device — "
                "focus it with run_ui_command first if needed.",
                {"session": session_property, "text": {"type": "string"}},
                ["session", "text"],
            ),
            tool(
                "submit_session_composer",
                "Press Enter on a session's composer, sending whatever text is staged "
                "there (e.g. by type_into_session). Always requires confirmation.",
                {"session": session_property},
                ["session"],
            ),
        ]

    @staticmethod
    def _classify(kind: str, arguments: dict[str, Any]) -> str:
        if kind in {
            "session_detail",
            "read_transcript",
            "search_history",
            "read_project_note",
            "list_project_notes",
            "list_queue",
        }:
            return ACTION_CLASS_READ
        if kind == "run_ui_command":
            return ACTION_CLASS_NAVIGATION
        # Typing unsent text is reversible on its face (the operator can clear
        # the composer, and nothing is delivered); submitting the composer is a
        # send and falls through to the consequential floor below. Creating a
        # project is reversible the same way spawning a session is: removal is a
        # registration tombstone that deletes nothing on disk, and the folder the
        # tool minted inside the configured parent is empty.
        if kind in {
            "write_project_note", "spawn_session", "type_into_session",
            "create_project",
        }:
            return ACTION_CLASS_REVERSIBLE
        if kind == "send_to_session":
            return (
                ACTION_CLASS_CONSEQUENTIAL
                if bool(arguments.get("deliver"))
                else ACTION_CLASS_REVERSIBLE
            )
        return ACTION_CLASS_CONSEQUENTIAL

    @staticmethod
    def _restate(kind: str, arguments: dict[str, Any], *, spoken: bool = False) -> str:
        return restate_action(kind, arguments, spoken=spoken)

    # ----------------------------------------------------------- action ledger

    async def _record_action(
        self,
        dialog_id: str,
        turn_id: str,
        kind: str,
        action_class: str,
        arguments: dict[str, Any],
        status: str,
        *,
        expires_at: float | None = None,
        result: str | None = None,
    ) -> dict[str, Any]:
        row = {
            "id": str(uuid.uuid4()),
            "dialog_id": dialog_id,
            "turn_id": turn_id,
            "created_at": time.time(),
            "kind": kind,
            "class": action_class,
            "restatement": self._restate(kind, arguments),
            "arguments": json.dumps(arguments, ensure_ascii=False),
            "status": status,
            "expires_at": expires_at,
            "resolved_at": None,
            "result": result,
        }
        await self.store.add_action(row)
        await self._emit_action(row)
        return row

    async def _emit_action(self, row: dict[str, Any]) -> None:
        await self.events.emit(
            "assistant_action", source="assistant", **action_snapshot(row)
        )

    async def _finish_action(
        self, action_id: str, *, status: str, result: str | None = None
    ) -> dict[str, Any] | None:
        row = await self.store.resolve_action(action_id, status=status, result=result)
        if row is not None:
            await self._emit_action(row)
        return row

    # ------------------------------------------------------------ tool running

    async def _run_tool(
        self,
        dialog_id: str,
        turn_id: str,
        kind: str,
        arguments: dict[str, Any],
        client_id: str = "",
    ) -> dict[str, Any]:
        """Execute one tool call under the trust policy; returns the tool result."""
        if client_id and kind in CLIENT_EXECUTED_KINDS:
            # Persisted with the action so a later confirm still targets the
            # device the turn came from, not every connected workspace.
            arguments["client_id"] = client_id[:64]
        action_class = self._classify(kind, arguments)
        if action_class == ACTION_CLASS_READ:
            result = await self._execute_read(kind, arguments)
            await self._record_action(
                dialog_id, turn_id, kind, action_class, arguments, "executed",
                result=json.dumps(result, ensure_ascii=False)[:2_000],
            )
            return result
        if action_class == ACTION_CLASS_NAVIGATION:
            return await self._dispatch_ui(dialog_id, turn_id, arguments)
        # Mutations: resolve targets *before* recording a pending action, so a
        # name that does not resolve is an ambiguity answer, never a pending card.
        resolution_error = await self._preflight_mutation(kind, arguments)
        if resolution_error is not None:
            return resolution_error
        # Resolved arguments are the fingerprint, so two differently-worded
        # proposals for the same write collide here rather than becoming two
        # cards and, for a note, two copies of the same paragraph.
        duplicate = await self._duplicate_action(dialog_id, kind, arguments)
        if duplicate is not None:
            return self._duplicate_result(duplicate)
        policy = (
            "confirm"
            if action_class == ACTION_CLASS_CONSEQUENTIAL
            else self.config.assistant_trust_reversible
        )
        if policy == "auto":
            row = await self._record_action(
                dialog_id, turn_id, kind, action_class, arguments, "executing"
            )
            return await self._execute_mutation_row(row)
        if policy == "cancel_window":
            expires = time.time() + CANCEL_WINDOW_SECONDS
            row = await self._record_action(
                dialog_id, turn_id, kind, action_class, arguments, "scheduled",
                expires_at=expires,
            )
            self._schedule_window(row)
            return {
                "pending_confirmation": True,
                "mode": "cancel_window",
                "action_id": row["id"],
                "restatement": row["restatement"],
                "executes_in_seconds": CANCEL_WINDOW_SECONDS,
                "note": "The action runs automatically unless the operator cancels it.",
            }
        expires = time.time() + CONFIRM_TTL_SECONDS
        row = await self._record_action(
            dialog_id, turn_id, kind, action_class, arguments, "pending", expires_at=expires,
        )
        return {
            "pending_confirmation": True,
            "mode": "confirm",
            "action_id": row["id"],
            "restatement": row["restatement"],
            "expires_in_seconds": CONFIRM_TTL_SECONDS,
            "note": "Nothing happens unless the operator confirms.",
        }

    @staticmethod
    def _action_fingerprint(kind: str, arguments: dict[str, Any]) -> str:
        """Identity of a proposal: its kind plus its resolved arguments.

        `client_id` is excluded because it names the device that asked, not the
        thing being done — the same write proposed from a phone and a desktop is
        one write.
        """
        payload = {
            key: value for key, value in arguments.items() if key != "client_id"
        }
        return f"{kind}:{json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)}"

    async def _duplicate_action(
        self, dialog_id: str, kind: str, arguments: dict[str, Any]
    ) -> dict[str, Any] | None:
        """The prior action this proposal repeats, if there is one.

        Two different failures produce the same duplicate. A card the operator
        has not answered yet, re-proposed because the model was asked again —
        that is always wrong, for every kind, since the operator would then have
        two cards for one intent and answering either leaves the other armed.
        And a write the operator already confirmed, re-proposed because a spoken
        "confirm" the closed grammar did not recognize reached the model as a
        turn — that one is only guarded for kinds where repetition is itself the
        damage (a note appended twice), because spawning two identical sessions
        or staging composer text twice are things an operator legitimately asks
        for.
        """
        fingerprint = self._action_fingerprint(kind, arguments)
        now = time.time()
        for row in reversed(await self.store.actions(dialog_id, limit=40)):
            if str(row["kind"]) != kind:
                continue
            status = str(row["status"])
            if status in {"executed", "executing"}:
                if kind not in DUPLICATE_GUARDED_KINDS:
                    continue
                if now - float(row["created_at"]) > DUPLICATE_ACTION_WINDOW_SECONDS:
                    continue
            elif status not in {"pending", "scheduled"}:
                continue
            try:
                prior = json.loads(str(row["arguments"] or "{}"))
            except ValueError:
                continue
            if not isinstance(prior, dict):
                continue
            if self._action_fingerprint(kind, prior) == fingerprint:
                return row
        return None

    @staticmethod
    def _duplicate_result(row: dict[str, Any]) -> dict[str, Any]:
        status = str(row["status"])
        restatement = str(row["restatement"])
        if status in {"pending", "scheduled"}:
            log.info(
                "assistant duplicate proposal suppressed action=%s kind=%s status=%s",
                row["id"], row["kind"], status,
            )
            return {
                "pending_confirmation": True,
                "duplicate": True,
                "action_id": row["id"],
                "restatement": restatement,
                "note": (
                    "This exact action is already waiting for the operator. Say so "
                    "briefly; do not propose it again."
                ),
            }
        log.info(
            "assistant duplicate execution suppressed action=%s kind=%s",
            row["id"], row["kind"],
        )
        return {
            "already_done": True,
            "action_id": row["id"],
            "restatement": restatement,
            "note": (
                "This exact action already ran in this conversation. Tell the "
                "operator it is done; do not run it again."
            ),
        }

    def _schedule_window(self, row: dict[str, Any]) -> None:
        """Execute a scheduled action when its deadline passes.

        The deadline is re-read from the row on every wake rather than baked
        into one sleep, because a device that announces the card aloud pushes it
        out (`announce_action`). Waking early and looping is the only way that
        extension can be honoured without a second timer racing this one.
        """
        action_id = str(row["id"])

        async def run() -> None:
            try:
                # unsupervised-loop-ok: one timer for one scheduled action,
                # bounded by CANCEL_WINDOW_MAX_SECONDS from its creation.
                while True:
                    current = await self.store.action(action_id)
                    if current is None or current["status"] != "scheduled":
                        return  # confirmed, cancelled, or already claimed
                    remaining = float(current.get("expires_at") or 0) - time.time()
                    if remaining <= 0:
                        break
                    await asyncio.sleep(min(remaining, CANCEL_WINDOW_MAX_SECONDS))
                if not await self.store.claim_action(action_id, ("scheduled",)):
                    return  # confirmed or cancelled first; the claimant owns it
                current = await self.store.action(action_id)
                if current is not None:
                    log.info(
                        "assistant cancel window elapsed action=%s kind=%s",
                        action_id, current["kind"],
                    )
                    await self._execute_mutation_row(current)
            finally:
                self._window_tasks.pop(action_id, None)

        self._window_tasks[action_id] = asyncio.create_task(
            run(), name=f"assistant-window-{action_id}"
        )

    async def announce_action(self, action_id: str) -> dict[str, Any]:
        """A device reports it has begun speaking this card's announcement.

        Only scheduled actions care: their window is the operator's whole
        opportunity to object, and it must not be spent on synthesizing the
        sentence that tells them there is something to object to.

        **Exactly once per action, and that is a structural requirement rather
        than tidiness.** Extending re-emits the card so its countdown stays
        honest, and a device announces a card when it sees one — so an extension
        that could happen twice is a loop: emit, announce, extend, emit. It ran
        in production (2026-08-20): 80 extensions ~25 ms apart, each spawning its
        own speech clip, which then played for minutes after the operator had
        closed the microphone. The `_announced` set is the cut. In-memory is the
        correct lifetime: a restart expires every scheduled action anyway, so
        there is nothing left to re-announce.

        A client that never calls this simply keeps the original window.
        """
        row = await self.store.action(action_id)
        if row is None:
            raise AssistantError("unknown action")
        if action_id in self._announced:
            # Logged rather than silently absorbed: a client announcing a card
            # twice is the shape of the loop this guard exists to stop, and a
            # future regression should be visible in the log instead of only in
            # the operator's ears.
            log.warning(
                "assistant card announced again action=%s kind=%s status=%s; "
                "the cancel window moves once per card",
                action_id, row["kind"], row["status"],
            )
            return {"extended": False, "action": action_snapshot(row)}
        if row["status"] != "scheduled":
            return {"extended": False, "action": action_snapshot(row)}
        self._announced.add(action_id)
        if len(self._announced) > ANNOUNCED_MEMORY:
            self._announced = set(list(self._announced)[-ANNOUNCED_MEMORY:])
        ceiling = float(row["created_at"]) + CANCEL_WINDOW_MAX_SECONDS
        deadline = min(time.time() + CANCEL_WINDOW_SPOKEN_SECONDS, ceiling)
        updated = await self.store.extend_action_window(action_id, deadline)
        if updated is None:
            raise AssistantError("unknown action")
        extended = float(updated.get("expires_at") or 0) > float(row.get("expires_at") or 0)
        if extended:
            await self._emit_action(updated)
            log.info(
                "assistant cancel window extended action=%s kind=%s remaining=%.1fs",
                action_id, updated["kind"],
                float(updated.get("expires_at") or 0) - time.time(),
            )
        return {"extended": extended, "action": action_snapshot(updated)}

    async def confirm_action(self, action_id: str) -> dict[str, Any]:
        # Cancel any cancel-window timer *first*, then re-read: the timer may
        # have fired between the caller's view of the card and this request,
        # and executing a second time is the one thing a confirm must not do.
        window = self._window_tasks.pop(action_id, None)
        if window is not None:
            window.cancel()
            await asyncio.gather(window, return_exceptions=True)
        row = await self.store.action(action_id)
        if row is None:
            raise AssistantError("unknown action")
        if row["status"] == "executed":
            return {"result": {"already_executed": True}, "action": action_snapshot(row)}
        if row["status"] not in {"pending", "scheduled"}:
            raise AssistantError(f"the action is already {row['status']}")
        expires = row.get("expires_at")
        if row["status"] == "pending" and expires and float(expires) < time.time():
            await self._finish_action(action_id, status="expired")
            raise AssistantError("the confirmation expired; ask again")
        if not await self.store.claim_action(action_id, ("pending", "scheduled")):
            final = await self.store.action(action_id)
            raise AssistantError(
                f"the action is already {(final or row)['status']}"
            )
        result = await self._execute_mutation_row(row)
        final = await self.store.action(action_id)
        return {"result": result, "action": action_snapshot(final or row)}

    async def cancel_action(self, action_id: str) -> dict[str, Any]:
        window = self._window_tasks.pop(action_id, None)
        if window is not None:
            window.cancel()
            await asyncio.gather(window, return_exceptions=True)
        row = await self.store.action(action_id)
        if row is None:
            raise AssistantError("unknown action")
        if row["status"] not in {"pending", "scheduled"}:
            raise AssistantError(f"the action is already {row['status']}")
        if not await self.store.claim_action(action_id, ("pending", "scheduled")):
            final = await self.store.action(action_id)
            raise AssistantError(f"the action is already {(final or row)['status']}")
        final = await self._finish_action(action_id, status="cancelled")
        return {"action": action_snapshot(final or row)}

    # --------------------------------------------------------------- execution

    async def _preflight_mutation(
        self, kind: str, arguments: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Resolve names now so ambiguity is answered before anything pends."""
        if kind in {
            "send_to_session", "interrupt_session", "end_session",
            "type_into_session", "submit_session_composer",
        }:
            session, candidates = await self.resolve_session(str(arguments.get("session") or ""))
            if session is None:
                return {"error": "session did not resolve", "candidates": candidates}
            # Rewritten to the display name so the restatement the operator
            # confirms names the session the way their screen does.
            names = await self._display_names([session])
            arguments["session"] = names.get(session.record.id, session.record.name)
        if kind in {"spawn_session", "write_project_note"}:
            project, candidates = self.resolve_project(str(arguments.get("project") or ""))
            if project is None:
                return {"error": "project did not resolve", "candidates": candidates}
            arguments["project"] = project.name
        if kind == "spawn_session" and (
            str(arguments.get("seed_text") or "").strip()
            and str(arguments.get("stage_text") or "").strip()
        ):
            return {
                "error": (
                    "seed_text and stage_text cannot be combined: seed_text runs the "
                    "prompt, stage_text leaves it unsent — pick the one the operator asked for"
                )
            }
        if kind == "create_project":
            refusal = await self._preflight_create_project(arguments)
            if refusal is not None:
                return refusal
        if kind == "write_project_note":
            where = str(arguments.get("where") or "top")
            if where not in NOTE_WRITE_POSITIONS:
                return {"error": f"where must be one of {', '.join(NOTE_WRITE_POSITIONS)}"}
            if where == "at_line" and int(arguments.get("line") or 0) < 1:
                return {"error": "at_line needs a 1-indexed line number"}
            if where in {"after", "before"} and not str(arguments.get("anchor") or ""):
                return {"error": f"{where} needs an anchor — a unique span to sit beside"}
            if where == "replace" and not str(arguments.get("find") or ""):
                return {"error": "replace needs the text to find"}
            if where != "replace" and not str(arguments.get("text") or "").strip():
                return {"error": "text must not be empty"}
            if str(arguments.get("section") or "").strip() and where not in {"top", "end"}:
                return {
                    "error": (
                        "section only combines with where=top or where=end; "
                        f"{where} already names its own position"
                    )
                }
        text = arguments.get("text") or arguments.get("seed_text")
        if kind in {
            "send_to_session", "type_into_session",
        } and not str(text or "").strip():
            return {"error": "text must not be empty"}
        return None

    async def _preflight_create_project(
        self, arguments: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Resolve a spoken name to the exact folder that would be created.

        Everything that can be answered before a card pends is answered here: the
        parent comes from configuration only (the model never supplies a path, so
        this tool cannot create a folder anywhere else on disk), the folder leaf
        is the deterministic normalization of the name, and the absolute result is
        stamped into the arguments so the restatement the operator confirms shows
        exactly what lands on disk - including whether it revives a tombstoned
        project's identity.
        """
        # These two are preflight-owned outputs, never model inputs: a stray
        # "root" cannot smuggle a path past the name-only contract, and a stray
        # "restores" cannot make the card claim a revival that is not real.
        arguments.pop("root", None)
        arguments.pop("restores", None)
        name = str(arguments.get("name") or "").strip()
        if not name:
            return {"error": "the project needs a name"}
        parent_setting = str(self.config.new_project_parent or "").strip()
        if not parent_setting:
            return {
                "error": "no new-project location is configured; ask the operator to "
                "set Settings → Projects → New project location first"
            }
        parent = Path(parent_setting).expanduser()
        if not parent.is_dir():
            return {
                "error": f"the configured new-project location does not exist: {parent}"
                " - ask the operator to fix Settings → Projects → New project location"
            }
        folder = suggest_folder_name(name)
        try:
            validate_leaf_name(
                folder,
                label="project folder name",
                reserved_names=RESERVED_PROJECT_FOLDER_NAMES,
            )
        except ValueError as exc:
            return {"error": f'"{name}" does not make a usable folder name: {exc}'}
        target = (parent / folder).resolve()
        for record in self.projects.projects.values():
            if same_path(record.root, target):
                return {
                    "error": f'that folder is already registered as the project '
                    f'"{record.name}"'
                }
        try:
            if target.exists():
                if not target.is_dir():
                    return {"error": f"{target} already exists and is not a folder"}
                if any(target.iterdir()):
                    # Adopting existing work from a chat message is the add-existing
                    # flow's job, where a human is looking at the folder.
                    return {
                        "error": f"{target} already exists and is not empty; register "
                        "it with the Add project dialog instead"
                    }
        except OSError as exc:
            return {"error": f"cannot inspect {target}: {exc}"}
        arguments["name"] = name
        arguments["root"] = str(target)
        removed = await self.projects.history.removed_project_for_root(str(target))
        if removed is not None:
            arguments["restores"] = removed.name
        return None

    async def _execute_mutation_row(self, row: dict[str, Any]) -> dict[str, Any]:
        try:
            arguments = json.loads(str(row.get("arguments") or "{}"))
        except ValueError:
            arguments = {}
        try:
            result = await self._execute_mutation(str(row["kind"]), arguments, row)
        except (AssistantError, OpenRouterError) as exc:
            await self._finish_action(str(row["id"]), status="failed", result=str(exc)[:500])
            return {"error": str(exc)[:500]}
        except Exception as exc:  # noqa: BLE001 - the ledger must record any failure
            log.exception("assistant action failed action=%s kind=%s", row["id"], row["kind"])
            await self._finish_action(str(row["id"]), status="failed", result=str(exc)[:500])
            return {"error": f"the action failed: {str(exc)[:300]}"}
        await self._finish_action(
            str(row["id"]), status="executed",
            result=json.dumps(result, ensure_ascii=False)[:2_000],
        )
        return result

    async def _execute_mutation(
        self, kind: str, arguments: dict[str, Any], row: dict[str, Any]
    ) -> dict[str, Any]:
        if kind in {"type_into_session", "submit_session_composer"}:
            session, _candidates = await self.resolve_session(str(arguments.get("session") or ""))
            if session is None:
                raise AssistantError("the target session is no longer live")
            # The mounted pane owns PTY writes (bracketed paste, replay,
            # ownership claims), so the operator's device performs this and
            # reports back — the daemon never types into a PTY directly.
            # `target_session_id`, not `session_id`: the latter is a first-class
            # MuxEvent field the bus lifts out of the payload.
            outcome = await self._dispatch_client(
                row, {"target_session_id": session.record.id}
            )
            if kind == "type_into_session":
                return {
                    "typed": True,
                    "session": session.record.name,
                    "note": "staged in the composer, not sent",
                    "detail": outcome.get("detail"),
                }
            return {"submitted": True, "session": session.record.name}
        if kind == "send_to_session":
            session, _candidates = await self.resolve_session(str(arguments.get("session") or ""))
            if session is None:
                raise AssistantError("the target session is no longer live")
            message = await self.prompt_queue.enqueue(
                target_session_id=session.record.id,
                body=str(arguments.get("text") or ""),
                armed=bool(arguments.get("deliver")),
                sender_kind="user",
                sender_label="Mux assistant",
            )
            return {
                "queued": True,
                "armed": bool(message.get("armed")),
                "message_id": message.get("id"),
                "state": message.get("state"),
            }
        if kind == "spawn_session":
            project, _candidates = self.resolve_project(str(arguments.get("project") or ""))
            if project is None:
                raise AssistantError("the target project no longer exists")
            backend = str(arguments.get("backend") or "").strip() or (
                project.default_backend or ""
            )
            seed = str(arguments.get("seed_text") or "").strip()
            stage = str(arguments.get("stage_text") or "").strip()
            if str(arguments.get("client_id") or ""):
                # The operator's device spawns through its own launch path, so
                # the new session opens as a tab in the currently active pane
                # instead of the layout reconciler's default new pane. No
                # daemon fallback on failure: a lost acknowledgement plus a
                # daemon retry would spawn the session twice. The backend is
                # fully resolved here — the frontend may not name harnesses.
                # Staging still happens daemon-side (the client passes
                # stage_text back on its spawn request), so it needs no pane.
                outcome = await self._dispatch_client(
                    row,
                    {
                        "project_id": project.id,
                        "backend": backend or self.config.default_backend,
                        "seed_text": seed or None,
                        "stage_text": stage or None,
                    },
                )
                return {"spawned": True, "detail": outcome.get("detail") or "spawned"}
            body: dict[str, Any] = {"project_id": project.id}
            if backend:
                body["backend"] = backend
            if seed:
                body["seed_text"] = seed
            if stage:
                body["stage_text"] = stage
            session = await self.spawn_op(body)
            result: dict[str, Any] = {"spawned": True, "session": session.record.name}
            if stage:
                # Said explicitly so the model reports it truthfully: the text is
                # parked in the composer and nothing has been submitted.
                result["staged"] = "the text is in the composer, unsent"
            elif seed:
                result["submitted"] = "the agent is running the seed prompt"
            return result
        if kind == "create_project":
            if self.create_project_op is None:
                raise AssistantError("project creation is not wired on this daemon")
            # The op re-registers through the ordinary registration path; the
            # arguments carry the preflight-resolved absolute root, so what
            # executes is exactly what the confirmed card restated.
            return await self.create_project_op(dict(arguments))
        if kind == "interrupt_session":
            session, _candidates = await self.resolve_session(str(arguments.get("session") or ""))
            if session is None:
                raise AssistantError("the target session is no longer live")
            await self.interrupt_op(session)
            return {"interrupted": True, "session": session.record.name}
        if kind == "end_session":
            session, _candidates = await self.resolve_session(str(arguments.get("session") or ""))
            if session is None:
                raise AssistantError("the target session is no longer live")
            await self.end_op(session, "assistant")
            return {"ended": True, "session": session.record.name}
        if kind == "write_project_note":
            project, _candidates = self.resolve_project(str(arguments.get("project") or ""))
            if project is None:
                raise AssistantError("the target project no longer exists")
            if self.note_write is None:
                raise AssistantError("note writing is not wired on this daemon")
            note = await self.note_write(
                project.id, str(arguments.get("note") or "") or None, dict(arguments)
            )
            if note.get("error"):
                raise AssistantError(str(note["error"]))
            return {
                "written": True,
                "note": note.get("title"),
                "where": str(arguments.get("where") or "top"),
                "bytes": note.get("bytes"),
            }
        raise AssistantError(f"unknown mutation {kind}")

    async def _execute_read(self, kind: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if kind == "session_detail":
            session, candidates = await self.resolve_session(str(arguments.get("session") or ""))
            if session is None:
                return {"error": "session did not resolve", "candidates": candidates}
            record = session.record
            now = time.time()
            display = await self._display_names([session])
            detail: dict[str, Any] = {
                "name": display.get(record.id, record.name),
                "spawn_name": record.name,
                "project": self._project_name(record.project_id),
                "backend": record.backend,
                "state": record.state,
                "state_age": self._age(now, record.state_since or None),
                "awaiting": record.awaiting_reason,
                "idle_reason": record.idle_reason,
                "model": record.model,
                "cwd": record.trusted_cwd,
                "git_branch": record.git.branch,
                "git_dirty_files": record.git.dirty,
                "tokens_in": record.tokens_in,
                "tokens_out": record.tokens_out,
                "context_pct": record.context_pct,
            }
            if session.transcript_path and session.transcript_path.exists():
                prompt, reply = await asyncio.to_thread(
                    final_exchange,
                    session.transcript_path,
                    record.backend,
                    native_id=record.native_session_id,
                )
                detail["last_prompt"] = prompt[:1_500]
                detail["last_reply"] = reply[:4_000]
            return detail
        if kind == "read_transcript":
            session, candidates = await self.resolve_session(str(arguments.get("session") or ""))
            if session is None:
                return {"error": "session did not resolve", "candidates": candidates}
            if not session.transcript_path or not session.transcript_path.exists():
                return {"error": "the session has no readable transcript"}
            count = max(1, min(int(arguments.get("messages") or 10), 30))
            # Bound to a narrowed local: `session` is reassigned in later
            # branches, which widens the closure capture back to Optional.
            target = session
            page = await asyncio.to_thread(
                lambda: transcript_message_page(
                    target.transcript_path,
                    target.record.backend,
                    direction="tail",
                    anchor=None,
                    max_bytes=512 * 1024,
                    max_messages=count,
                    native_id=target.record.native_session_id,
                )
            )
            messages = [
                {
                    "role": item.get("role"),
                    "text": str(item.get("text") or "")[:2_000],
                }
                for item in page.get("messages", [])
            ]
            return {"messages": messages}
        if kind == "search_history":
            if self.history_search is None:
                return {"error": "history search is not wired on this daemon"}
            page = await self.history_search(
                query=str(arguments.get("query") or ""),
                limit=max(1, min(int(arguments.get("limit") or 5), 10)),
            )
            items = [
                {
                    "name": item.get("name"),
                    "backend": item.get("backend"),
                    "project": item.get("project_label") or item.get("project_id"),
                    "started_at": item.get("started_at"),
                    "summary": str(item.get("summary") or "")[:400],
                }
                for item in page.get("items", [])
            ]
            return {"items": items}
        if kind == "list_project_notes":
            project, candidates = self.resolve_project(str(arguments.get("project") or ""))
            if project is None:
                return {"error": "project did not resolve", "candidates": candidates}
            if self.note_list is None:
                return {"error": "note listing is not wired on this daemon"}
            items = await self.note_list(project.id)
            return {
                "notes": [
                    {
                        "title": item.get("title"),
                        "bytes": item.get("bytes"),
                        "updated_at": item.get("updated_at"),
                    }
                    for item in items
                ]
            }
        if kind == "read_project_note":
            project, candidates = self.resolve_project(str(arguments.get("project") or ""))
            if project is None:
                return {"error": "project did not resolve", "candidates": candidates}
            if self.note_read is None:
                return {"error": "note reading is not wired on this daemon"}
            note = await self.note_read(project.id, str(arguments.get("note") or "") or None)
            if note.get("error"):
                return {"error": str(note["error"]), "candidates": note.get("candidates") or []}
            markdown = str(note.get("markdown") or "")
            try:
                from_line = max(1, int(arguments.get("from_line") or 1))
            except (TypeError, ValueError):
                from_line = 1
            try:
                max_lines = int(arguments.get("max_lines") or NOTE_PAGE_MAX_LINES)
            except (TypeError, ValueError):
                max_lines = NOTE_PAGE_MAX_LINES
            page = note_page(markdown, from_line=from_line, max_lines=max_lines)
            # Numbered, not raw: a position is only choosable if it is nameable,
            # and the outline travels with every page so a later window is still
            # addressable by section without paging back to the top.
            return {
                "title": note.get("title"),
                "outline": note_outline(markdown),
                **page,
            }
        if kind == "list_queue":
            reference = str(arguments.get("session") or "").strip()
            if reference:
                session, candidates = await self.resolve_session(reference)
                if session is None:
                    return {"error": "session did not resolve", "candidates": candidates}
                view = await self.prompt_queue.target_view(session.record.id)
                return {
                    "messages": [
                        {
                            "state": item.get("state"),
                            "armed": item.get("armed"),
                            "sender": item.get("sender_label") or item.get("sender_kind"),
                            "body": str(item.get("body") or "")[:300],
                            "created_at": item.get("created_at"),
                        }
                        for item in view.get("messages", [])[:20]
                    ],
                    "pending": view.get("pending"),
                }
            rows = await self.prompt_queue.summary()
            live = {session.record.id for session in self._live_sessions()}
            display = await self._display_names(self._live_sessions())
            return {
                "targets": [
                    {
                        "session": display.get(
                            str(row.get("target_session_id")), row.get("label")
                        ),
                        "project": self._project_name(str(row.get("project_id") or "")),
                        "pending": row.get("pending"),
                        "blocked": row.get("blocked"),
                        "stranded": row.get("stranded"),
                        "live": str(row.get("target_session_id")) in live,
                    }
                    for row in rows
                    if int(row.get("pending") or 0)
                    or int(row.get("blocked") or 0)
                    or int(row.get("stranded") or 0)
                ][:30]
            }
        raise AssistantError(f"unknown read tool {kind}")

    async def _dispatch_ui(
        self, dialog_id: str, turn_id: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        """Hand a UI command to the originating device and wait for its report.

        The daemon cannot run these: focus, drawers, and panels are per-device
        UI state. The device resolves the label against the live command
        registry (the same resolver spoken phrases use) and reports back.
        """
        command = str(arguments.get("command") or "").strip()
        if not command:
            return {"error": "command must not be empty"}
        recorded: dict[str, Any] = {"command": command}
        if arguments.get("client_id"):
            recorded["client_id"] = str(arguments["client_id"])
        row = await self._record_action(
            dialog_id, turn_id, "run_ui_command", ACTION_CLASS_NAVIGATION,
            recorded, "dispatched",
            expires_at=time.time() + UI_ACK_TIMEOUT_SECONDS,
        )
        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._ui_acks[str(row["id"])] = future
        try:
            outcome = await asyncio.wait_for(future, timeout=UI_ACK_TIMEOUT_SECONDS)
        except TimeoutError:
            await self._finish_action(
                str(row["id"]), status="failed", result="no device acknowledged"
            )
            return {
                "error": "no connected device acknowledged the UI command; the operator "
                "may not have a workspace open"
            }
        finally:
            self._ui_acks.pop(str(row["id"]), None)
        status = "executed" if outcome.get("ok") else "failed"
        await self._finish_action(
            str(row["id"]), status=status,
            result=json.dumps(outcome, ensure_ascii=False)[:1_000],
        )
        return outcome

    async def _dispatch_client(
        self, row: dict[str, Any], extra: dict[str, Any]
    ) -> dict[str, Any]:
        """Hand an already-claimed mutation to the originating device.

        The action row keeps its persisted status ('executing'); only a
        synthetic `dispatched` event carries the work to the client, stamped
        with the row's client_id so exactly one device acts. Failure and
        timeout raise, so `_execute_mutation_row` records the row as failed.
        """
        action_id = str(row["id"])
        payload = {**action_snapshot(row), "status": "dispatched", **extra}
        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._ui_acks[action_id] = future
        try:
            await self.events.emit("assistant_action", source="assistant", **payload)
            outcome = await asyncio.wait_for(future, timeout=UI_ACK_TIMEOUT_SECONDS)
        except TimeoutError:
            raise AssistantError(
                "no connected device acknowledged; the operator's workspace with the "
                "session's terminal must be open"
            ) from None
        finally:
            self._ui_acks.pop(action_id, None)
        if not outcome.get("ok"):
            raise AssistantError(
                str(outcome.get("detail") or "the device could not run the action")[:400]
            )
        return outcome

    def report_ui_result(self, action_id: str, outcome: dict[str, Any]) -> bool:
        """The originating device's acknowledgement for a dispatched UI action."""
        future = self._ui_acks.get(action_id)
        if future is None or future.done():
            return False
        future.set_result(
            {
                "ok": bool(outcome.get("ok")),
                "detail": str(outcome.get("detail") or "")[:400],
                "candidates": [str(item)[:80] for item in outcome.get("candidates") or []][:6],
            }
        )
        return True

    # ------------------------------------------------------------- the turn loop

    async def _run_turn(
        self, dialog_id: str, turn_id: str, text: str, client_context: dict[str, Any]
    ) -> None:
        lock = self._dialog_locks.setdefault(dialog_id, asyncio.Lock())
        async with lock:
            try:
                await self._run_turn_inner(dialog_id, turn_id, text, client_context)
            except asyncio.CancelledError:
                await self.events.emit(
                    "assistant_turn_failed", source="assistant",
                    dialog_id=dialog_id, turn_id=turn_id, error="interrupted",
                )
            except (AssistantError, OpenRouterError) as exc:
                message = str(exc)[:500] or exc.__class__.__name__
                self.diagnostic = message
                log.warning(
                    "assistant turn failed dialog=%s turn=%s error=%s",
                    dialog_id, turn_id, message,
                )
                await self.store.add_message(
                    {
                        "id": str(uuid.uuid4()),
                        "dialog_id": dialog_id,
                        "turn_id": turn_id,
                        "created_at": time.time(),
                        "role": "assistant",
                        "display": "",
                        "speech": "",
                        "status": "failed",
                        "error": message,
                        "model": self.config.assistant_model,
                        "input_tokens": 0,
                        "output_tokens": 0,
                        "cost_usd": None,
                    }
                )
                await self.events.emit(
                    "assistant_turn_failed", source="assistant",
                    dialog_id=dialog_id, turn_id=turn_id, error=message,
                )

    async def _budget_check(self) -> None:
        spend = await self.automation_store.spend(rule_id=ASSISTANT_RULE_ID)
        if float(spend["cost_usd"]) >= self.config.assistant_daily_budget_usd:
            raise AssistantError(
                "the assistant's daily budget is exhausted; raise it in Settings → Assistant"
            )

    async def _model_call(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        turn_id: str,
        step: int,
        on_content: Callable[[str], Awaitable[None]] | None = None,
    ) -> Any:
        await self._budget_check()
        model = self.config.assistant_model
        encoded = json.dumps(messages, ensure_ascii=False).encode()
        call_id = await self.automation_store.observer_started(
            firing_id=f"assistant:{turn_id}:{step}",
            rule_id=ASSISTANT_RULE_ID,
            model=model,
            input_hash=hashlib.sha256(encoded).hexdigest(),
            input_bytes=len(encoded),
        )
        try:
            turn = await self.provider.complete_tools(
                model=model,
                messages=messages,
                tools=tools,
                max_tokens=self.config.assistant_max_output_tokens,
                on_content=on_content,
            )
        except asyncio.CancelledError:
            await self.automation_store.observer_finished(
                call_id, status="cancelled", error="cancelled"
            )
            raise
        except OpenRouterError as exc:
            await self.automation_store.observer_finished(
                call_id, status="failed", error=str(exc)[:1000]
            )
            raise
        await self.automation_store.observer_finished(
            call_id,
            status="completed",
            resolved_model=turn.resolved_model,
            generation_id=turn.generation_id,
            input_tokens=turn.input_tokens,
            output_tokens=turn.output_tokens,
            cost_usd=turn.cost_usd,
            latency_ms=turn.latency_ms,
        )
        await self.automation_store.add_spend(
            rule_id=ASSISTANT_RULE_ID,
            model=turn.resolved_model,
            input_tokens=turn.input_tokens,
            output_tokens=turn.output_tokens,
            cost_usd=turn.cost_usd or 0,
            call_id=call_id,
        )
        return turn

    async def _run_turn_inner(
        self, dialog_id: str, turn_id: str, text: str, client_context: dict[str, Any]
    ) -> None:
        started = time.perf_counter()
        history = await self.store.messages(
            dialog_id, limit=self.config.assistant_context_messages
        )
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PRIMER},
            {
                "role": "system",
                "content": await self._context_message(client_context, dialog_id),
            },
        ]
        for item in history:
            if item["turn_id"] == turn_id and item["role"] == "user":
                continue  # re-appended below as the closing message
            if item["role"] in {"user", "assistant"} and str(item["display"]).strip():
                messages.append({"role": item["role"], "content": str(item["display"])})
        messages.append({"role": "user", "content": text})
        client_id = str(client_context.get("client_id") or "")[:64]
        tools = self._tool_definitions()
        totals = {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "calls": 0}
        display_parts: list[str] = []
        spoken_parts: list[str] = []
        sentence_index = 0
        message_id = str(uuid.uuid4())
        # Counted rather than flagged, because the rule they feed is narrow: the
        # model's prose is suppressed only when a single card *is* the whole
        # outcome of the turn. Suppressing it whenever any card opened also
        # swallowed "I opened two of the three" — new information the operator
        # has no other way to hear.
        cards_opened = 0
        mutations_executed = 0
        suppress_speech = False

        async def emit_sentence(sentence: str) -> None:
            nonlocal sentence_index
            await self.events.emit(
                "assistant_sentence",
                source="assistant",
                dialog_id=dialog_id,
                turn_id=turn_id,
                message_id=message_id,
                index=sentence_index,
                display=sentence,
                speech="" if suppress_speech else speech_form(sentence),
                speech_suppressed=suppress_speech,
            )
            sentence_index += 1

        # One system line, kept at the end of the prompt and replaced each round
        # rather than appended, so the model always sees exactly one budget and
        # the prompt does not grow a stack of stale ones.
        budget_note: dict[str, Any] | None = None
        exhausted = False
        for step in range(MAX_MODEL_CALLS_PER_TURN):
            if dialog_id in self._interrupts:
                raise asyncio.CancelledError
            # Sentences are released as the model writes them, so the device can
            # begin speaking the answer while the rest is still generating. The
            # split happens here rather than in the client because a delta is not
            # a sentence and half a sentence is not speakable.
            streamer = _SentenceStreamer(emit_sentence)
            turn = await self._model_call(
                messages, tools, turn_id, step,
                streamer.feed if self.config.assistant_stream_replies else None,
            )
            totals["input_tokens"] += turn.input_tokens
            totals["output_tokens"] += turn.output_tokens
            totals["cost_usd"] += float(turn.cost_usd or 0)
            totals["calls"] += 1
            if turn.content.strip():
                display_parts.append(turn.content.strip())
                if not suppress_speech:
                    spoken_parts.append(turn.content.strip())
                if streamer.emitted:
                    # Streaming already published every complete sentence; only
                    # the unterminated tail is left. Re-emitting from `content`
                    # here would duplicate the whole reply.
                    await streamer.flush()
                else:
                    for sentence in split_sentences(turn.content):
                        await emit_sentence(sentence)
            else:
                await streamer.flush()
            if not turn.tool_calls:
                break
            messages.append(turn.message)
            for call in turn.tool_calls:
                function = call.get("function") or {}
                name = str(function.get("name") or "")
                call_ref = str(call.get("id") or uuid.uuid4())
                try:
                    arguments = json.loads(str(function.get("arguments") or "{}"))
                    if not isinstance(arguments, dict):
                        raise ValueError("arguments must be an object")
                except ValueError:
                    arguments = {}
                await self.events.emit(
                    "assistant_tool_status", source="assistant",
                    dialog_id=dialog_id, turn_id=turn_id, tool=name, status="running",
                )
                known = {
                    "session_detail", "read_transcript", "search_history",
                    "read_project_note", "list_project_notes", "list_queue",
                    "write_project_note", "send_to_session",
                    "spawn_session", "interrupt_session", "end_session", "run_ui_command",
                    "type_into_session", "submit_session_composer", "create_project",
                }
                if name in known:
                    try:
                        result = await self._run_tool(
                            dialog_id, turn_id, name, arguments, client_id
                        )
                    except AssistantError as exc:
                        result = {"error": str(exc)[:500]}
                else:
                    result = {"error": f"unknown tool {name}"}
                if result.get("pending_confirmation"):
                    if not result.get("duplicate"):
                        cards_opened += 1
                elif name in MUTATION_KINDS and not result.get("error"):
                    mutations_executed += 1
                # A single card, and nothing else done, is the only case where
                # the card says everything the turn has to say.
                suppress_speech = cards_opened == 1 and mutations_executed == 0
                await self.events.emit(
                    "assistant_tool_status", source="assistant",
                    dialog_id=dialog_id, turn_id=turn_id, tool=name,
                    status="failed" if result.get("error") else "done",
                )
                payload = json.dumps(result, ensure_ascii=False)
                if len(payload) > MAX_TOOL_RESULT_CHARS:
                    payload = payload[:MAX_TOOL_RESULT_CHARS] + '… (truncated)"'
                messages.append(
                    {"role": "tool", "tool_call_id": call_ref, "content": payload}
                )
            remaining = MAX_MODEL_CALLS_PER_TURN - step - 1
            if remaining <= 0:
                # The model asked for more work and there is no round left to do
                # it in. Saying nothing here is what made a half-finished turn
                # indistinguishable from a finished one.
                exhausted = True
                break
            if budget_note is not None:
                messages.remove(budget_note)
            budget_note = {"role": "system", "content": _round_budget(remaining)}
            messages.append(budget_note)
        if exhausted:
            notice = (
                "I ran out of tool rounds for this turn, so some of what you asked "
                "for is not done. Say continue and I will pick up where I stopped."
            )
            display_parts.append(notice)
            spoken_parts.append(notice)
            log.warning(
                "assistant turn exhausted its rounds dialog=%s turn=%s calls=%d "
                "cards=%d mutations=%d",
                dialog_id, turn_id, totals["calls"], cards_opened, mutations_executed,
            )
            # Deliberately not gated on `suppress_speech`: a turn that stopped
            # early is the one thing the operator must hear about, whatever else
            # the turn did.
            await self.events.emit(
                "assistant_sentence",
                source="assistant",
                dialog_id=dialog_id,
                turn_id=turn_id,
                message_id=message_id,
                index=sentence_index,
                display=notice,
                speech=speech_form(notice),
                speech_suppressed=False,
            )
            sentence_index += 1
        display = "\n\n".join(display_parts).strip()
        if not display:
            display = "Done." if totals["calls"] else ""
            if not suppress_speech and display:
                spoken_parts.append(display)
        spoken = "\n\n".join(spoken_parts).strip()
        # The turn's speech is what should still be *heard*, not everything that
        # was said: a client that speaks the streamed sentences already played
        # the unsuppressed part, and one that only knows this event must not be
        # handed the card's paraphrase.
        speech = speech_form(spoken) if spoken else ""
        await self.store.add_message(
            {
                "id": message_id,
                "dialog_id": dialog_id,
                "turn_id": turn_id,
                "created_at": time.time(),
                "role": "assistant",
                "display": display,
                "speech": speech,
                "status": "done",
                "error": None,
                "model": self.config.assistant_model,
                "input_tokens": totals["input_tokens"],
                "output_tokens": totals["output_tokens"],
                "cost_usd": totals["cost_usd"],
            }
        )
        await self.store.touch_dialog(dialog_id)
        elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
        log.info(
            "assistant turn complete dialog=%s turn=%s calls=%d in=%d out=%d "
            "cost=%.5f elapsed=%.0fms sentences=%d cards=%d mutations=%d "
            "suppressed=%s exhausted=%s",
            dialog_id, turn_id, totals["calls"], totals["input_tokens"],
            totals["output_tokens"], totals["cost_usd"], elapsed_ms,
            sentence_index, cards_opened, mutations_executed,
            suppress_speech, exhausted,
        )
        self.diagnostic = None
        await self.events.emit(
            "assistant_turn_done",
            source="assistant",
            dialog_id=dialog_id,
            turn_id=turn_id,
            message_id=message_id,
            display=display,
            speech=speech,
            speech_suppressed=suppress_speech,
            sentence_count=sentence_index,
            # True when the turn stopped on the round ceiling with work still
            # asked for. The client shows it; nothing may report such a turn as
            # complete.
            exhausted=exhausted,
            usage={**totals, "elapsed_ms": elapsed_ms},
        )

