from __future__ import annotations

import asyncio
import secrets
import time
import uuid
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .adapters import BackendAdapter, SpawnOptions
from .event_bus import EventBus
from .history import HistoryIndex
from .models import SessionRecord, SessionState
from .projects import resolve_project
from .pty_host import PtyHost
from .win_jobobj import ReaperJob


class ScrollbackBuffer:
    def __init__(self, max_bytes: int) -> None:
        self.max_bytes = max_bytes
        self._chunks: deque[bytes] = deque()
        self._size = 0

    def append(self, data: bytes) -> None:
        if self.max_bytes <= 0:
            self._chunks.clear()
            self._size = 0
            return
        if len(data) >= self.max_bytes:
            self._chunks.clear()
            self._chunks.append(data[-self.max_bytes :])
            self._size = self.max_bytes
            return
        self._chunks.append(data)
        self._size += len(data)
        excess = self._size - self.max_bytes
        while excess > 0 and self._chunks:
            first = self._chunks[0]
            if len(first) <= excess:
                self._chunks.popleft()
                self._size -= len(first)
                excess -= len(first)
            else:
                self._chunks[0] = first[excess:]
                self._size -= excess
                excess = 0

    def bytes(self) -> bytes:
        return b"".join(self._chunks)


@dataclass(eq=False, slots=True)
class PtySubscriber:
    queue: asyncio.Queue[bytes | dict[str, Any]]
    resync_pending: bool = False
    dropped_bytes: int = 0
    dropped_chunks: int = 0
    exit_pending: dict[str, Any] | None = None


class Session:
    def __init__(
        self,
        record: SessionRecord,
        pty: PtyHost,
        adapter: BackendAdapter,
        max_scrollback: int,
        hook_secret: str,
        ownership_job: ReaperJob | None = None,
    ) -> None:
        self.record, self.pty, self.adapter = record, pty, adapter
        self.scrollback = ScrollbackBuffer(max_scrollback)
        self.subscribers: set[PtySubscriber] = set()
        self.tasks: set[asyncio.Task[Any]] = set()
        self.stopping = False
        self.stop_event = asyncio.Event()
        self.hook_secret = hook_secret
        self.ownership_job = ownership_job
        # Exactly one browser connection may write terminal-generated responses
        # and user keystrokes. Without this, two attached xterms both answer
        # device-status queries and the duplicate response appears at the prompt.
        self.input_owner: str | None = None
        self.revision = 0
        self.state_source_priority = -1
        self.agent_stop_event = asyncio.Event()
        self.observer_task: asyncio.Task[Any] | None = None
        self.detection_task: asyncio.Task[Any] | None = None

    def subscribe(self, maxsize: int = 1024) -> PtySubscriber:
        subscriber = PtySubscriber(asyncio.Queue(maxsize=maxsize))
        self.subscribers.add(subscriber)
        return subscriber

    def replay_and_subscribe(
        self,
    ) -> tuple[dict[str, Any], int, bytes, PtySubscriber]:
        """Atomically snapshot replay bytes and register for subsequent output.

        This method has no await points, so the single event-loop fanout task cannot
        append output between the snapshot and subscription. A new attachment therefore
        neither misses nor duplicates the boundary chunk.
        """
        subscriber = self.subscribe()
        return self.record.snapshot(), self.revision, self.scrollback.bytes(), subscriber

    def _schedule_resync(self, subscriber: PtySubscriber, rejected: bytes | None = None) -> None:
        if rejected is not None:
            subscriber.dropped_bytes += len(rejected)
            subscriber.dropped_chunks += 1
        while not subscriber.queue.empty():
            queued = subscriber.queue.get_nowait()
            if isinstance(queued, bytes):
                subscriber.dropped_bytes += len(queued)
                subscriber.dropped_chunks += 1
        if not subscriber.resync_pending:
            subscriber.resync_pending = True
            subscriber.queue.put_nowait({"type": "resync"})

    def publish_output(self, data: bytes) -> None:
        for subscriber in tuple(self.subscribers):
            if subscriber.resync_pending:
                subscriber.dropped_bytes += len(data)
                subscriber.dropped_chunks += 1
                continue
            try:
                subscriber.queue.put_nowait(data)
            except asyncio.QueueFull:
                self._schedule_resync(subscriber, data)

    def publish_update(self) -> None:
        self.revision += 1
        frame = {
            "type": "update",
            "snapshot": self.record.snapshot(),
            "revision": self.revision,
        }
        for subscriber in tuple(self.subscribers):
            if subscriber.resync_pending:
                continue
            try:
                subscriber.queue.put_nowait(frame)
            except asyncio.QueueFull:
                self._schedule_resync(subscriber)

    def publish_exit(self, reason: str) -> None:
        self.revision += 1
        frame = {
            "type": "exit",
            "snapshot": self.record.snapshot(),
            "revision": self.revision,
            "reason": reason,
        }
        for subscriber in tuple(self.subscribers):
            if subscriber.resync_pending:
                subscriber.exit_pending = frame
                continue
            try:
                subscriber.queue.put_nowait(frame)
            except asyncio.QueueFull:
                self._schedule_resync(subscriber)
                subscriber.exit_pending = frame

    def transition(
        self, state: SessionState, detail: str | None, *, source: str
    ) -> bool:
        """Apply a state transition only when its observation source is authoritative."""
        priority = {"pty": 0, "transcript": 1, "hook": 2}.get(source, 0)
        if priority < self.state_source_priority:
            return False
        changed = self.record.state != state or self.record.state_detail != detail
        self.state_source_priority = priority
        if not changed:
            return False
        self.record.state = state
        self.record.state_detail = detail
        self.publish_update()
        return True

    def take_resync(
        self, subscriber: PtySubscriber
    ) -> tuple[int, int, bytes, dict[str, Any], int, dict[str, Any] | None]:
        """Capture a deterministic recovery boundary without yielding the event loop."""
        dropped_bytes = subscriber.dropped_bytes
        dropped_chunks = subscriber.dropped_chunks
        replay = self.scrollback.bytes()
        snapshot = self.record.snapshot()
        revision = self.revision
        exit_frame = subscriber.exit_pending
        subscriber.resync_pending = False
        subscriber.dropped_bytes = 0
        subscriber.dropped_chunks = 0
        subscriber.exit_pending = None
        return dropped_bytes, dropped_chunks, replay, snapshot, revision, exit_frame

    def unsubscribe(self, subscriber: PtySubscriber) -> None:
        self.subscribers.discard(subscriber)


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
        shell_profile_id: str | None = None,
        profile_env: dict[str, str] | None = None,
        project_label: str | None = None,
    ) -> Session:
        if backend not in self.adapters:
            raise ValueError(f"unknown backend: {backend}")
        sid = str(uuid.uuid4())
        native_id = resume_native_id or sid
        resolved_cwd = Path(cwd or Path.cwd()).resolve()
        if not resolved_cwd.is_dir():
            raise ValueError(f"cwd does not exist: {resolved_cwd}")
        adapter = self.adapters[backend]
        opts = SpawnOptions(resolved_cwd, exe, args or [], sid)
        spawn_spec = (
            adapter.resume_spec(native_id, opts)
            if resume_native_id
            else adapter.spawn_spec(native_id, opts)
        )
        record = SessionRecord(
            sid,
            name or f"{backend}-{sid[:6]}",
            space_id,
            backend,
            native_id,
            str(resolved_cwd),
            spawn_spec.executable,
            list(spawn_spec.argv),
            shell_profile_id=shell_profile_id,
            auto_named=name is None,
            state="running" if backend == "shell" else "starting",
        )
        project = await resolve_project(resolved_cwd)
        record.project_id = project.id
        record.project_label = project_label or project.label
        record.project_root = project.root
        hook_secret = secrets.token_urlsafe(24)
        pty = PtyHost(
            spawn_spec.executable,
            spawn_spec.argv,
            str(resolved_cwd),
            reaper=self.reaper,
            graceful_exit=adapter.graceful_exit_keys(),
            env_extra={
                **self.child_env,
                **{
                    key: value
                    for candidate in self.adapters.values()
                    for key, value in candidate.session_env(sid).items()
                },
                **spawn_spec.env,
                **(profile_env or {}),
                "MUX_SESSION_ID": sid,
                "MUX_HOOK_URL": f"{self.ingress_url}/api/hooks/{sid}",
                "MUX_PROMOTE_URL": f"{self.ingress_url}/api/sessions/{sid}/promote",
                "MUX_DEMOTE_URL": f"{self.ingress_url}/api/sessions/{sid}/demote",
                "MUX_HOOK_SECRET": hook_secret,
            },
        )
        pty.spawn()
        record.pid = pty.pid
        ownership_job: ReaperJob | None = None
        ownership_error: str | None = None
        create_child = getattr(self.reaper, "create_child", None)
        if create_child:
            try:
                ownership_job = create_child()
                ownership_job.assign(record.pid)
            except OSError as exc:
                ownership_error = str(exc)
                if ownership_job:
                    ownership_job.close()
                ownership_job = None
        session = Session(
            record,
            pty,
            adapter,
            self.max_scrollback,
            hook_secret,
            ownership_job,
        )
        self.sessions[sid] = session
        transcript = adapter.transcript_path(native_id, resolved_cwd)
        await self.history.session_started(record, str(transcript) if transcript else None)
        await self.events.emit("session_spawned", session_id=sid, backend=backend, name=record.name)
        if ownership_error:
            await self.events.emit(
                "process_ownership_degraded",
                session_id=sid,
                source="process",
                error=ownership_error,
            )
        session.tasks.add(asyncio.create_task(self._fanout(session), name=f"fanout-{sid}"))
        session.tasks.add(asyncio.create_task(self._ticker(session), name=f"ticker-{sid}"))
        if backend in {"claude", "codex"}:
            record.parser_status = "waiting"
            self._start_observer(session, transcript)
        elif backend == "shell":
            self._start_detection(session)
        return session

    def _start_observer(self, session: Session, transcript: Path | None) -> None:
        if session.observer_task and not session.observer_task.done():
            session.agent_stop_event.set()
            session.observer_task.cancel()
        session.agent_stop_event = asyncio.Event()
        task = asyncio.create_task(
            self._observe(session, transcript, session.agent_stop_event),
            name=f"observe-{session.record.id}",
        )
        session.observer_task = task
        session.tasks.add(task)

    def _start_detection(self, session: Session) -> None:
        if session.detection_task and not session.detection_task.done():
            session.detection_task.cancel()
        task = asyncio.create_task(
            self._detect_nested_agent(session), name=f"detect-{session.record.id}"
        )
        session.detection_task = task
        session.tasks.add(task)

    async def _detect_nested_agent(self, session: Session) -> None:
        while not session.stop_event.is_set() and session.pty.isalive():
            if session.record.backend != "shell":
                return
            terminal_text = session.scrollback.bytes().lower()
            # Full-screen CLIs redraw the echoed command quickly and ANSI can split
            # prompt text, so exact ``> claude`` matching is not reliable. Native
            # transcript cwd/time matching below is the ownership guard.
            launched = [
                adapter for name, adapter in self.adapters.items()
                if name != "shell" and name.encode() in terminal_text
            ]
            if not launched:
                try:
                    await asyncio.wait_for(session.stop_event.wait(), timeout=0.5)
                except TimeoutError:
                    pass
                continue
            candidates = [
                (modified, adapter.name, path, native_id)
                for adapter in launched
                for modified, path, native_id in adapter.recent_transcripts(
                    Path(session.record.cwd), session.record.created_at
                )
            ]
            if candidates:
                _, backend, path, native_id = max(candidates)
                session.adapter = self.adapters[backend]
                session.pty.graceful_exit = session.adapter.graceful_exit_keys()
                session.record.backend = backend
                session.record.native_session_id = native_id
                session.record.state = "starting"
                session.record.parser_status = "waiting"
                session.record.parser_diagnostic = None
                session.record.parser_events_seen = 0
                session.publish_update()
                await self.history.session_promoted(session.record, str(path))
                await self.events.emit(
                    "backend_detected",
                    session_id=session.record.id,
                    backend=backend,
                    native_session_id=native_id,
                )
                self._start_observer(session, path)
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
        session.record.parser_status = "waiting"
        session.record.parser_diagnostic = None
        session.record.parser_events_seen = 0
        session.record.state_detail = None
        session.state_source_priority = -1
        if session.record.auto_named:
            session.record.name = Path(session.record.cwd).name or backend
        session.publish_update()
        transcript = adapter.transcript_path(native_id, Path(session.record.cwd))
        await self.history.session_promoted(session.record, str(transcript) if transcript else "")
        await self.events.emit(
            "backend_detected",
            session_id=session.record.id,
            source="daemon",
            backend=backend,
            native_session_id=native_id,
        )
        self._start_observer(session, transcript)
        return session

    async def demote(self, sid: str, backend: str, native_id: str) -> Session:
        session = self.resolve(sid)
        if (
            session.record.backend != backend
            or session.record.native_session_id != native_id
        ):
            return session
        await self.history.update_agent_summary(session.record)
        session.agent_stop_event.set()
        if session.observer_task and not session.observer_task.done():
            session.observer_task.cancel()
            await asyncio.gather(session.observer_task, return_exceptions=True)
        session.observer_task = None
        session.adapter = self.adapters["shell"]
        session.pty.graceful_exit = session.adapter.graceful_exit_keys()
        session.record.backend = "shell"
        session.record.native_session_id = session.record.id
        session.record.state = "running"
        session.record.state_detail = None
        session.record.context_window = 0
        session.record.context_pct = 0
        session.record.parser_status = "not_applicable"
        session.record.parser_diagnostic = None
        session.record.parser_events_seen = 0
        session.state_source_priority = -1
        if session.record.auto_named:
            session.record.name = f"shell-{session.record.id[:6]}"
        session.publish_update()
        await self.events.emit(
            "backend_demoted",
            session_id=session.record.id,
            source="daemon",
            backend=backend,
            native_session_id=native_id,
        )
        self._start_detection(session)
        return session

    async def _observe(
        self, session: Session, transcript: Path | None, stop_event: asyncio.Event
    ) -> None:
        from .observation import observe_transcript

        adapter = session.adapter
        path = transcript
        if path is None or not path.exists():
            path = await adapter.await_transcript(
                session.record.native_session_id,
                Path(session.record.cwd),
                session.record.created_at,
                stop_event,
            )
        if path:
            native_id = adapter.transcript_native_id(path)
            if native_id:
                session.record.native_session_id = native_id
            await self.history.session_promoted(session.record, str(path))
            await observe_transcript(session, path, self.events, stop_event)

    async def _fanout(self, session: Session) -> None:
        while True:
            chunk = await session.pty.output_queue.get()
            if chunk == b"":
                if not session.stopping:
                    await self._mark_ended(session, "process_exit")
                return
            session.record.last_activity_ts = time.time()
            session.scrollback.append(chunk)
            session.publish_output(chunk)

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
        session.agent_stop_event.set()
        for adapter in self.adapters.values():
            adapter.cleanup(session.record.id)
        if session.ownership_job:
            session.ownership_job.close()
            session.ownership_job = None
        await self.history.session_ended(session.record, reason)
        session.publish_exit(reason)
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
