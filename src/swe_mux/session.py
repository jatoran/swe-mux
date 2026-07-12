from __future__ import annotations

import asyncio
import json
import secrets
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Any

from .adapters import BackendAdapter, SpawnOptions
from .adapters.claude import encode_cwd
from .event_bus import EventBus
from .history import HistoryIndex
from .models import SessionRecord
from .pty_host import PtyHost
from .win_jobobj import ReaperJob


class ScrollbackBuffer:
    def __init__(self, max_bytes: int) -> None:
        self.max_bytes = max_bytes
        self._chunks: deque[bytes] = deque()
        self._size = 0

    def append(self, data: bytes) -> None:
        self._chunks.append(data)
        self._size += len(data)
        while self._size > self.max_bytes and self._chunks:
            self._size -= len(self._chunks.popleft())

    def bytes(self) -> bytes:
        return b"".join(self._chunks)


class Session:
    def __init__(
        self,
        record: SessionRecord,
        pty: PtyHost,
        adapter: BackendAdapter,
        max_scrollback: int,
        hook_secret: str,
    ) -> None:
        self.record, self.pty, self.adapter = record, pty, adapter
        self.scrollback = ScrollbackBuffer(max_scrollback)
        self.subscribers: set[asyncio.Queue[bytes]] = set()
        self.tasks: set[asyncio.Task[Any]] = set()
        self.stopping = False
        self.stop_event = asyncio.Event()
        self.hook_secret = hook_secret
        # Exactly one browser connection may write terminal-generated responses
        # and user keystrokes. Without this, two attached xterms both answer
        # device-status queries and the duplicate response appears at the prompt.
        self.input_owner: str | None = None

    def subscribe(self) -> asyncio.Queue[bytes]:
        queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=1024)
        self.subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[bytes]) -> None:
        self.subscribers.discard(queue)


class SessionManager:
    def __init__(
        self,
        adapters: dict[str, BackendAdapter],
        reaper: ReaperJob,
        history: HistoryIndex,
        events: EventBus,
        max_scrollback: int,
        ingress_url: str,
        child_env: dict[str, str] | None = None,
    ) -> None:
        self.adapters, self.reaper, self.history, self.events = adapters, reaper, history, events
        self.max_scrollback = max_scrollback
        self.ingress_url = ingress_url.rstrip("/")
        self.child_env = child_env or {}
        self.sessions: dict[str, Session] = {}

    async def spawn(
        self,
        *,
        backend: str,
        name: str | None,
        cwd: str | None,
        space_id: str,
        exe: str | None = None,
        args: list[str] | None = None,
        resume_native_id: str | None = None,
    ) -> Session:
        if backend not in self.adapters:
            raise ValueError(f"unknown backend: {backend}")
        sid = str(uuid.uuid4())
        native_id = resume_native_id or sid
        resolved_cwd = Path(cwd or Path.cwd()).resolve()
        if not resolved_cwd.is_dir():
            raise ValueError(f"cwd does not exist: {resolved_cwd}")
        adapter = self.adapters[backend]
        opts = SpawnOptions(resolved_cwd, exe, args or [])
        appname, cmdline = (
            adapter.resume_cmdline(native_id, opts)
            if resume_native_id
            else adapter.spawn_cmdline(native_id, opts)
        )
        record = SessionRecord(
            sid,
            name or f"{backend}-{sid[:6]}",
            space_id,
            backend,
            native_id,
            str(resolved_cwd),
            appname,
            args or [],
            auto_named=name is None,
            state="running" if backend == "shell" else "starting",
        )
        hook_secret = secrets.token_urlsafe(24)
        pty = PtyHost(
            appname,
            cmdline,
            str(resolved_cwd),
            reaper=self.reaper,
            graceful_exit=adapter.graceful_exit_keys(),
            env_extra={
                **self.child_env,
                "MUX_SESSION_ID": sid,
                "MUX_HOOK_URL": f"{self.ingress_url}/api/hooks/{sid}",
                "MUX_PROMOTE_URL": f"{self.ingress_url}/api/sessions/{sid}/promote",
                "MUX_HOOK_SECRET": hook_secret,
            },
        )
        session = Session(record, pty, adapter, self.max_scrollback, hook_secret)
        pty.spawn()
        record.pid = pty.pid
        self.sessions[sid] = session
        transcript = adapter.transcript_path(native_id, resolved_cwd)
        await self.history.session_started(record, str(transcript) if transcript else None)
        await self.events.emit("session_spawned", session_id=sid, backend=backend, name=record.name)
        session.tasks.add(asyncio.create_task(self._fanout(session), name=f"fanout-{sid}"))
        session.tasks.add(asyncio.create_task(self._ticker(session), name=f"ticker-{sid}"))
        if backend in {"claude", "codex"}:
            session.tasks.add(
                asyncio.create_task(self._observe(session, transcript), name=f"observe-{sid}")
            )
        elif backend == "shell":
            session.tasks.add(
                asyncio.create_task(self._detect_nested_agent(session), name=f"detect-{sid}")
            )
        return session

    async def _detect_nested_agent(self, session: Session) -> None:
        claude_dir = Path.home() / ".claude" / "projects" / encode_cwd(session.record.cwd)
        codex_root = Path.home() / ".codex" / "sessions"
        while not session.stop_event.is_set() and session.pty.isalive():
            if session.record.backend != "shell":
                return
            terminal_text = session.scrollback.bytes().lower()
            # Full-screen CLIs redraw the echoed command quickly and ANSI can split
            # prompt text, so exact ``> claude`` matching is not reliable. Native
            # transcript cwd/time matching below is the ownership guard.
            claude_launched = b"claude" in terminal_text
            codex_launched = b"codex" in terminal_text
            if not claude_launched and not codex_launched:
                try:
                    await asyncio.wait_for(session.stop_event.wait(), timeout=0.5)
                except TimeoutError:
                    pass
                continue
            candidates: list[tuple[float, str, Path, str]] = []
            if claude_launched and claude_dir.exists():
                for path in claude_dir.glob("*.jsonl"):
                    modified = path.stat().st_mtime
                    if modified + 2 >= session.record.created_at:
                        candidates.append((modified, "claude", path, path.stem))
            if codex_launched and codex_root.exists():
                codex_paths = sorted(
                    codex_root.glob("**/rollout-*.jsonl"),
                    key=lambda item: item.stat().st_mtime,
                    reverse=True,
                )[:10]
                for path in codex_paths:
                    modified = path.stat().st_mtime
                    if modified + 2 < session.record.created_at:
                        continue
                    try:
                        first = json.loads(
                            path.open("r", encoding="utf-8", errors="replace").readline()
                        )
                        payload = first.get("payload") or {}
                        same_cwd = (
                            str(Path(payload.get("cwd", "")).resolve()).lower()
                            == session.record.cwd.lower()
                        )
                        if same_cwd and payload.get("id"):
                            candidates.append((modified, "codex", path, str(payload["id"])))
                    except (OSError, json.JSONDecodeError):
                        pass
            if candidates:
                _, backend, path, native_id = max(candidates)
                session.record.backend = backend
                session.record.native_session_id = native_id
                session.record.state = "starting"
                await self.history.session_promoted(session.record, str(path))
                await self.events.emit(
                    "backend_detected",
                    session_id=session.record.id,
                    backend=backend,
                    native_session_id=native_id,
                )
                await self._observe(session, path)
                return
            try:
                await asyncio.wait_for(session.stop_event.wait(), timeout=0.5)
            except TimeoutError:
                pass

    async def promote(self, sid: str, backend: str, native_id: str) -> Session:
        if backend not in {"claude", "codex"}:
            raise ValueError(f"cannot promote session to {backend}")
        session = self.resolve(sid)
        if session.record.backend == backend and session.record.native_session_id == native_id:
            return session
        adapter = self.adapters[backend]
        session.adapter = adapter
        session.pty.graceful_exit = adapter.graceful_exit_keys()
        session.record.backend = backend
        session.record.native_session_id = native_id
        session.record.state = "starting"
        session.record.state_detail = None
        if session.record.auto_named:
            session.record.name = Path(session.record.cwd).name or backend
        transcript = adapter.transcript_path(native_id, Path(session.record.cwd))
        await self.history.session_promoted(session.record, str(transcript) if transcript else "")
        await self.events.emit(
            "backend_detected",
            session_id=session.record.id,
            source="daemon",
            backend=backend,
            native_session_id=native_id,
        )
        task = asyncio.create_task(self._observe(session, transcript), name=f"observe-{sid}")
        session.tasks.add(task)
        return session

    async def _observe(self, session: Session, transcript: Path | None) -> None:
        from .observation import find_codex_transcript, observe_transcript

        path = transcript
        if session.record.backend == "codex":
            path = await find_codex_transcript(
                session.record.cwd, session.record.created_at, session.stop_event
            )
        if path:
            if session.record.backend == "codex":
                try:
                    first = json.loads(
                        path.open("r", encoding="utf-8", errors="replace").readline()
                    )
                    payload = first.get("payload") or {}
                    if payload.get("id"):
                        session.record.native_session_id = str(payload["id"])
                except (OSError, json.JSONDecodeError):
                    pass
            await self.history.session_promoted(session.record, str(path))
            await observe_transcript(session, path, self.events, session.stop_event)

    async def _fanout(self, session: Session) -> None:
        while True:
            chunk = await session.pty.output_queue.get()
            if chunk == b"":
                if not session.stopping:
                    await self._mark_ended(session, "process_exit")
                for queue in tuple(session.subscribers):
                    try:
                        queue.put_nowait(b"")
                    except asyncio.QueueFull:
                        pass
                return
            session.record.last_activity_ts = time.time()
            session.scrollback.append(chunk)
            for queue in tuple(session.subscribers):
                try:
                    queue.put_nowait(chunk)
                except asyncio.QueueFull:
                    pass

    async def _ticker(self, session: Session) -> None:
        while not session.stopping and session.record.state not in {"exited", "crashed"}:
            await asyncio.sleep(1)
            if not session.pty.isalive():
                await self._mark_ended(session, "process_exit")
                return

    async def _mark_ended(self, session: Session, reason: str) -> None:
        if session.record.state in {"exited", "crashed"}:
            return
        session.record.state = "exited" if session.stopping else "crashed"
        session.record.last_activity_ts = time.time()
        await self.history.session_ended(session.record, reason)
        await self.events.emit(
            "session_exited" if session.stopping else "session_crashed",
            session_id=session.record.id,
            source="pty",
            reason=reason,
        )

    async def stop(self, sid: str) -> None:
        session = self.sessions[sid]
        session.stopping = True
        session.stop_event.set()
        await asyncio.to_thread(session.pty.stop)
        await self._mark_ended(session, "killed")

    async def shutdown(self) -> None:
        await asyncio.gather(
            *(
                self.stop(sid)
                for sid, s in self.sessions.items()
                if s.record.state not in {"exited", "crashed"}
            ),
            return_exceptions=True,
        )

    def resolve(self, identity: str) -> Session:
        if identity in self.sessions:
            return self.sessions[identity]
        matches = [s for s in self.sessions.values() if s.record.name == identity]
        if len(matches) != 1:
            raise KeyError(identity)
        return matches[0]
