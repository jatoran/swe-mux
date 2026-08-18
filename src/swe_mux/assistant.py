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
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeVar

from .config import Config
from .event_bus import EventBus
from .openrouter import OpenRouterClient, OpenRouterError
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
    from .project import ProjectManager
    from .prompt_queue import PromptQueueService
    from .session import Session, SessionManager

log = logging.getLogger(__name__)

T = TypeVar("T")

ASSISTANT_RULE_ID = "builtin:assistant"
# One user turn may drive at most this many model calls (the first, plus one per
# round of tool results). A loop that has not settled by then is a runaway.
MAX_MODEL_CALLS_PER_TURN = 6
# Pending confirmations decay on their own; nothing should stay armed because a
# tab closed. Cancel-window actions execute much sooner (see CANCEL_WINDOW).
CONFIRM_TTL_SECONDS = 120.0
CANCEL_WINDOW_SECONDS = 6.0
UI_ACK_TIMEOUT_SECONDS = 8.0
MAX_TURN_TEXT_CHARS = 8_000
MAX_TOOL_RESULT_CHARS = 12_000
MAX_CONTEXT_SESSIONS = 80
MAX_CLIENT_COMMANDS = 400

ACTION_CLASS_READ = "read"
ACTION_CLASS_NAVIGATION = "navigation"
ACTION_CLASS_REVERSIBLE = "reversible"
ACTION_CLASS_CONSEQUENTIAL = "consequential"

SYSTEM_PRIMER = """You are Mux, the operator's assistant inside swe-mux, a fleet manager for \
coding-agent terminal sessions (Claude Code, Codex, and others) organized into Projects.
You operate the workspace; you never write code and never run shell commands. When the \
operator asks for code work, route it: offer to queue a message to an existing session or \
spawn a new one.

Speak the short-response protocol: answer first, one or two plain sentences, detail only on \
request. No markdown, no bullet lists, no file paths unless essential. At most one \
clarifying question. Every status figure you state must come from the workspace snapshot or \
a tool result, never from memory; include the provided age qualifiers when a reading is stale.

Use tools for anything you cannot answer from the snapshot. Refer to sessions and projects \
by their names exactly as the snapshot spells them. Mutating tools may return \
pending_confirmation: tell the operator what was proposed and how to confirm (say confirm, \
or the card in the panel); never claim a pending action already happened. If a tool reports \
ambiguity, ask the operator to choose. UI commands (focus, open tabs) run on the device the \
operator is speaking through; if none is connected the tool will say so."""


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


def action_snapshot(row: dict[str, Any]) -> dict[str, Any]:
    public = dict(row)
    try:
        public["arguments"] = json.loads(str(row.get("arguments") or "{}"))
    except ValueError:
        public["arguments"] = {}
    public["action_class"] = public.pop("class", None)
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
        note_append: Callable[[str, str], Awaitable[dict[str, Any]]] | None = None,
        note_list: Callable[[str], Awaitable[list[dict[str, Any]]]] | None = None,
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
        self.note_append = note_append
        self.diagnostic: str | None = None
        # One turn at a time per dialog; concurrent turns would interleave the
        # message log and race the pending-action state.
        self._dialog_locks: dict[str, asyncio.Lock] = {}
        self._turn_tasks: dict[str, asyncio.Task[None]] = {}
        self._interrupts: set[str] = set()
        # UI actions wait here for the originating device's acknowledgement.
        self._ui_acks: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._window_tasks: dict[str, asyncio.Task[None]] = {}

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
            raise AssistantError("a turn is already running in this dialog")
        turn_id = str(uuid.uuid4())
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
            self._run_turn(dialog_id, turn_id, body, client_context or {}),
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

    def _turn_finished(self, dialog_id: str, task: asyncio.Task[None]) -> None:
        if self._turn_tasks.get(dialog_id) is task:
            self._turn_tasks.pop(dialog_id, None)
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            log.error("assistant turn task failed dialog=%s", dialog_id, exc_info=error)

    def interrupt(self, dialog_id: str) -> bool:
        """Stop the running turn after its current step; nothing external is undone."""
        self._interrupts.add(dialog_id)
        task = self._turn_tasks.get(dialog_id)
        if task is not None and not task.done():
            task.cancel()
            return True
        return False

    async def stop(self) -> None:
        for task in [*self._turn_tasks.values(), *self._window_tasks.values()]:
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
            if record.model:
                entry["model"] = record.model
            rows.append(entry)
            if len(rows) >= MAX_CONTEXT_SESSIONS:
                break
        return {"projects": projects, "sessions": rows, "captured_at": now}

    async def _context_message(self, client_context: dict[str, Any]) -> str:
        snapshot = await self.fleet_snapshot()
        focused = str(client_context.get("focused_session_id") or "")
        focused_name = None
        if focused:
            session = self.sessions.sessions.get(focused)
            if session is not None:
                names = await self._display_names([session])
                focused_name = names.get(session.record.id, session.record.name)
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
                "Read one of a project's notes; omit `note` for the primary note.",
                {
                    "project": project_property,
                    "note": {
                        "type": "string",
                        "description": "The note's title from list_project_notes",
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
                "append_project_note",
                "Append text to a project's primary note. Reversible; may need confirmation.",
                {"project": project_property, "text": {"type": "string"}},
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
                "spawn_session",
                "Start a new agent session in a project, optionally with a seed prompt.",
                {
                    "project": project_property,
                    "backend": {
                        "type": "string",
                        "description": (
                            "Harness name, e.g. claude or codex; omit for the project default"
                        ),
                    },
                    "seed_text": {"type": "string"},
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
        if kind in {"append_project_note", "spawn_session"}:
            return ACTION_CLASS_REVERSIBLE
        if kind == "send_to_session":
            return (
                ACTION_CLASS_CONSEQUENTIAL
                if bool(arguments.get("deliver"))
                else ACTION_CLASS_REVERSIBLE
            )
        return ACTION_CLASS_CONSEQUENTIAL

    def _restate(self, kind: str, arguments: dict[str, Any]) -> str:
        text = str(arguments.get("text") or arguments.get("seed_text") or "")
        preview = f' "{text[:120]}{"…" if len(text) > 120 else ""}"' if text else ""
        target = str(arguments.get("session") or arguments.get("project") or "")
        if kind == "send_to_session":
            mode = "send" if arguments.get("deliver") else "queue a draft"
            return f"{mode} to {target}:{preview}"
        if kind == "spawn_session":
            backend = str(arguments.get("backend") or "default harness")
            return f"spawn a {backend} session in {target}{preview}"
        if kind == "append_project_note":
            return f"append to the {target} project note:{preview}"
        if kind == "interrupt_session":
            return f"interrupt the agent in {target}"
        if kind == "end_session":
            return f"end the session {target}"
        if kind == "run_ui_command":
            return f"run the UI command: {arguments.get('command')}"
        return kind

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
        self, dialog_id: str, turn_id: str, kind: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        """Execute one tool call under the trust policy; returns the tool result."""
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

    def _schedule_window(self, row: dict[str, Any]) -> None:
        async def run() -> None:
            try:
                await asyncio.sleep(CANCEL_WINDOW_SECONDS)
                if not await self.store.claim_action(str(row["id"]), ("scheduled",)):
                    return  # confirmed or cancelled first; the claimant owns it
                current = await self.store.action(str(row["id"]))
                if current is not None:
                    await self._execute_mutation_row(current)
            finally:
                self._window_tasks.pop(str(row["id"]), None)

        self._window_tasks[str(row["id"])] = asyncio.create_task(
            run(), name=f"assistant-window-{row['id']}"
        )

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
        if kind in {"send_to_session", "interrupt_session", "end_session"}:
            session, candidates = await self.resolve_session(str(arguments.get("session") or ""))
            if session is None:
                return {"error": "session did not resolve", "candidates": candidates}
            arguments["session"] = session.record.name
        if kind in {"spawn_session", "append_project_note"}:
            project, candidates = self.resolve_project(str(arguments.get("project") or ""))
            if project is None:
                return {"error": "project did not resolve", "candidates": candidates}
            arguments["project"] = project.name
        text = arguments.get("text") or arguments.get("seed_text")
        if kind in {"send_to_session", "append_project_note"} and not str(text or "").strip():
            return {"error": "text must not be empty"}
        return None

    async def _execute_mutation_row(self, row: dict[str, Any]) -> dict[str, Any]:
        try:
            arguments = json.loads(str(row.get("arguments") or "{}"))
        except ValueError:
            arguments = {}
        try:
            result = await self._execute_mutation(str(row["kind"]), arguments)
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

    async def _execute_mutation(self, kind: str, arguments: dict[str, Any]) -> dict[str, Any]:
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
            body: dict[str, Any] = {"project_id": project.id}
            backend = str(arguments.get("backend") or "").strip()
            if backend:
                body["backend"] = backend
            elif project.default_backend:
                body["backend"] = project.default_backend
            seed = str(arguments.get("seed_text") or "").strip()
            if seed:
                body["seed_text"] = seed
            session = await self.spawn_op(body)
            return {"spawned": True, "session": session.record.name}
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
        if kind == "append_project_note":
            project, _candidates = self.resolve_project(str(arguments.get("project") or ""))
            if project is None:
                raise AssistantError("the target project no longer exists")
            if self.note_append is None:
                raise AssistantError("note writing is not wired on this daemon")
            note = await self.note_append(project.id, str(arguments.get("text") or ""))
            return {"appended": True, "note": note.get("title"), "bytes": note.get("bytes")}
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
            return {
                "title": note.get("title"),
                "markdown": str(note.get("markdown") or "")[:8_000],
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
        row = await self._record_action(
            dialog_id, turn_id, "run_ui_command", ACTION_CLASS_NAVIGATION,
            {"command": command}, "dispatched",
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
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]], turn_id: str, step: int
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
            {"role": "system", "content": await self._context_message(client_context)},
        ]
        for item in history:
            if item["turn_id"] == turn_id and item["role"] == "user":
                continue  # re-appended below as the closing message
            if item["role"] in {"user", "assistant"} and str(item["display"]).strip():
                messages.append({"role": item["role"], "content": str(item["display"])})
        messages.append({"role": "user", "content": text})
        tools = self._tool_definitions()
        totals = {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "calls": 0}
        spoken_parts: list[str] = []
        sentence_index = 0
        message_id = str(uuid.uuid4())
        for step in range(MAX_MODEL_CALLS_PER_TURN):
            if dialog_id in self._interrupts:
                raise asyncio.CancelledError
            turn = await self._model_call(messages, tools, turn_id, step)
            totals["input_tokens"] += turn.input_tokens
            totals["output_tokens"] += turn.output_tokens
            totals["cost_usd"] += float(turn.cost_usd or 0)
            totals["calls"] += 1
            if turn.content.strip():
                spoken_parts.append(turn.content.strip())
                for sentence in split_sentences(turn.content):
                    await self.events.emit(
                        "assistant_sentence",
                        source="assistant",
                        dialog_id=dialog_id,
                        turn_id=turn_id,
                        message_id=message_id,
                        index=sentence_index,
                        display=sentence,
                        speech=speech_form(sentence),
                    )
                    sentence_index += 1
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
                    "append_project_note", "send_to_session",
                    "spawn_session", "interrupt_session", "end_session", "run_ui_command",
                }
                if name in known:
                    try:
                        result = await self._run_tool(dialog_id, turn_id, name, arguments)
                    except AssistantError as exc:
                        result = {"error": str(exc)[:500]}
                else:
                    result = {"error": f"unknown tool {name}"}
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
        display = "\n\n".join(spoken_parts).strip()
        if not display:
            display = "Done." if totals["calls"] else ""
        speech = speech_form(display)
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
            "cost=%.5f elapsed=%.0fms",
            dialog_id, turn_id, totals["calls"], totals["input_tokens"],
            totals["output_tokens"], totals["cost_usd"], elapsed_ms,
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
            usage={**totals, "elapsed_ms": elapsed_ms},
        )

