from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from dataclasses import dataclass
from typing import Any

from .background_tasks import background
from .event_bus import EventBus
from .git_monitor import GitPosition, read_commit_metadata, read_git_position
from .history import HistoryIndex
from .models import MuxEvent
from .session import Session, SessionManager

log = logging.getLogger(__name__)

_COMMAND_TOOL_MARKERS = ("bash", "shell", "exec", "command", "powershell", "terminal")
_GIT_COMMIT = re.compile(
    r"(?:^|[;&|()]|\r?\n)\s*"
    r"(?:git|git\.exe|\"[^\"]*[\\/]git(?:\.exe)?\")\s+"
    r"(?:(?:-c\s+\S+|--no-pager)\s+)*commit(?:\s|$)",
    re.IGNORECASE,
)
_UNSAFE_REPOSITORY_REDIRECT = re.compile(
    r"(?:^|\s)(?:-C|--git-dir|--work-tree)(?:\s|=)", re.IGNORECASE
)
_PENDING_LIMIT = 512
_PENDING_MAX_AGE_SECONDS = 3600.0
GIT_PROVENANCE_LOOP = "git-provenance"


@dataclass(slots=True, frozen=True)
class GitCommitCommand:
    relationship: str


@dataclass(slots=True)
class PendingCommit:
    session_id: str
    session_name: str
    agent_run_id: str | None
    project_id: str
    call_id: str
    position: GitPosition | None
    relationship: str
    started_at: float


def classify_git_commit_command(tool: str, command: str | None) -> GitCommitCommand | None:
    """Recognize only an explicit ordinary `git commit` invocation.

    Repository-redirection flags are rejected because resolving their shell quoting and
    environment safely would turn a provenance observer into another command interpreter.
    The checkout poller still records the resulting HEAD as an observation.
    """
    if not command or not any(marker in tool.casefold() for marker in _COMMAND_TOOL_MARKERS):
        return None
    if _UNSAFE_REPOSITORY_REDIRECT.search(command) or not _GIT_COMMIT.search(command):
        return None
    relationship = "rewrote" if re.search(r"(?:^|\s)--amend(?:\s|$)", command) else "created"
    return GitCommitCommand(relationship=relationship)


def _path_key(value: str) -> str:
    return os.path.normcase(os.path.normpath(value))


class GitProvenanceService:
    """Correlate checkout HEAD transitions with the session evidence that saw them."""

    def __init__(
        self,
        history: HistoryIndex,
        sessions: SessionManager,
        events: EventBus,
    ) -> None:
        self.history = history
        self.sessions = sessions
        self.events = events
        self._queue: asyncio.Queue[MuxEvent] | None = None
        self._task: asyncio.Task[None] | None = None
        self._pending: dict[tuple[str, str], PendingCommit] = {}
        self._captured = 0
        self._dropped = 0
        self._last_error: str | None = None
        self._last_error_ts: float | None = None

    def start(self) -> None:
        self._queue = self.events.subscribe(name="git-provenance")
        self._task = background.start(GIT_PROVENANCE_LOOP, self._consume)

    async def stop(self) -> None:
        if self._queue is not None:
            self.events.unsubscribe(self._queue)
        if self._task is not None:
            await background.stop(GIT_PROVENANCE_LOOP)
        self._queue = None
        self._task = None
        self._pending.clear()

    def status(self) -> dict[str, Any]:
        return {
            "running": bool(self._task and not self._task.done()),
            "captured": self._captured,
            "dropped": self._dropped,
            "pending": len(self._pending),
            "last_error": self._last_error,
            "last_error_ts": self._last_error_ts,
        }

    async def _consume(self) -> None:
        assert self._queue is not None
        while True:
            event = await self._queue.get()
            with background.iteration(GIT_PROVENANCE_LOOP):
                try:
                    await self.handle_event(event)
                except Exception as exc:
                    self._dropped += 1
                    self._last_error = f"{type(exc).__name__}: {exc}"
                    self._last_error_ts = time.time()
                    raise

    async def handle_event(self, event: MuxEvent) -> None:
        if not event.session_id:
            return
        if event.type == "tool_use":
            await self._note_tool_use(event)
        elif event.type == "tool_result":
            await self._note_tool_result(event)
        elif event.type == "git_changed":
            await self._note_git_change(event)

    def _session(self, session_id: str) -> Session | None:
        return self.sessions.sessions.get(session_id)

    async def _checkout_session_count(self, root: str) -> int:
        key = _path_key(root)
        count = 0
        unresolved: dict[str, str] = {}
        for session in self.sessions.sessions.values():
            record = session.record
            if record.state == "exited":
                continue
            if record.git.root:
                if _path_key(record.git.root) == key:
                    count += 1
                continue
            cwd = record.git_cwd
            if cwd:
                unresolved.setdefault(_path_key(cwd), cwd)
        if unresolved:
            positions = await asyncio.gather(
                *(read_git_position(cwd) for cwd in unresolved.values())
            )
            for position in positions:
                if position is not None and _path_key(position.root) == key:
                    count += 1
        return max(1, count)

    async def _position(self, session: Session) -> GitPosition | None:
        git = session.record.git
        if git.root and git.head:
            return GitPosition(git.root, git.head)
        return await read_git_position(session.record.git_cwd)

    def _trim_pending(self, now: float) -> None:
        for key, pending in tuple(self._pending.items()):
            if now - pending.started_at > _PENDING_MAX_AGE_SECONDS:
                self._pending.pop(key, None)
        while len(self._pending) >= _PENDING_LIMIT:
            self._pending.pop(next(iter(self._pending)))

    async def _note_tool_use(self, event: MuxEvent) -> None:
        payload = event.payload or {}
        call_id = str(payload.get("call_id") or "")
        command = classify_git_commit_command(
            str(payload.get("tool") or ""),
            payload.get("target") if isinstance(payload.get("target"), str) else None,
        )
        session = self._session(event.session_id or "")
        if not call_id or command is None or session is None:
            return
        now = time.time()
        self._trim_pending(now)
        self._pending[(session.record.id, call_id)] = PendingCommit(
            session_id=session.record.id,
            session_name=session.record.name,
            agent_run_id=session.record.agent_run_id or None,
            project_id=session.record.project_id,
            call_id=call_id,
            position=await self._position(session),
            relationship=command.relationship,
            started_at=now,
        )

    async def _note_tool_result(self, event: MuxEvent) -> None:
        payload = event.payload or {}
        call_id = str(payload.get("call_id") or "")
        pending = self._pending.pop((event.session_id or "", call_id), None)
        if pending is None or payload.get("success") is not True:
            return
        session = self._session(pending.session_id)
        if session is None:
            return
        current = await read_git_position(session.record.git_cwd)
        if current is None:
            return
        if pending.position is not None:
            if _path_key(pending.position.root) != _path_key(current.root):
                return
            if pending.position.head == current.head:
                return
        shared = await self._checkout_session_count(current.root) > 1
        await self._record(
            session,
            worktree_root=current.root,
            commit_oid=current.head,
            previous_head=pending.position.head if pending.position else None,
            relationship=pending.relationship,
            confidence="ambiguous" if shared else "exact",
            ambiguous=shared,
            source="session_tool",
            source_event_seq=event.seq or None,
            tool_call_id=call_id,
            evidence_rank=30 if shared else 50,
            observed_at=event.ts,
            session_name=pending.session_name,
            agent_run_id=pending.agent_run_id,
            project_id=pending.project_id,
        )

    async def _note_git_change(self, event: MuxEvent) -> None:
        payload = event.payload or {}
        head = payload.get("head")
        previous_head = payload.get("previous_head")
        git = payload.get("git")
        root = git.get("root") if isinstance(git, dict) else None
        if not isinstance(head, str) or not head:
            return
        if not isinstance(previous_head, str) or not previous_head:
            return
        if not isinstance(root, str) or not root:
            return
        if head == previous_head:
            return
        session = self._session(event.session_id or "")
        if session is None:
            return
        shared = await self._checkout_session_count(root) > 1
        await self._record(
            session,
            worktree_root=root,
            commit_oid=head,
            previous_head=previous_head,
            relationship="observed",
            confidence="ambiguous" if shared else "correlated",
            ambiguous=shared,
            source="git_monitor",
            source_event_seq=event.seq or None,
            tool_call_id=None,
            evidence_rank=10 if shared else 20,
            observed_at=event.ts,
        )

    async def _record(
        self,
        session: Session,
        *,
        worktree_root: str,
        commit_oid: str,
        previous_head: str | None,
        relationship: str,
        confidence: str,
        ambiguous: bool,
        source: str,
        source_event_seq: int | None,
        tool_call_id: str | None,
        evidence_rank: int,
        observed_at: float,
        session_name: str | None = None,
        agent_run_id: str | None = None,
        project_id: str | None = None,
    ) -> None:
        metadata = await read_commit_metadata(worktree_root, commit_oid)
        record = session.record
        effective_name = session_name or record.name
        effective_run_id = (
            agent_run_id if session_name is not None else record.agent_run_id or None
        )
        effective_project_id = project_id or record.project_id
        item = await self.history.record_git_provenance(
            session_id=record.id,
            session_name=effective_name,
            agent_run_id=effective_run_id,
            project_id=effective_project_id,
            worktree_root=worktree_root,
            commit_oid=commit_oid,
            parent_oids=metadata.parents if metadata else (),
            subject=metadata.subject if metadata else "",
            committed_at=metadata.committed_at if metadata else None,
            previous_head=previous_head,
            relationship=relationship,
            confidence=confidence,
            ambiguous=ambiguous,
            source=source,
            source_event_seq=source_event_seq,
            tool_call_id=tool_call_id,
            evidence_rank=evidence_rank,
            observed_at=observed_at,
        )
        self._captured += 1
        log.info(
            "git provenance recorded session=%s run=%s commit=%s relationship=%s confidence=%s",
            record.id,
            effective_run_id or "shell",
            commit_oid[:12],
            item["relationship"],
            item["confidence"],
        )
        await self.events.emit(
            "git_provenance_changed",
            session_id=record.id,
            source="daemon",
            project_id=effective_project_id,
            agent_run_id=effective_run_id,
            commit_oid=commit_oid,
            relationship=item["relationship"],
            confidence=item["confidence"],
        )
