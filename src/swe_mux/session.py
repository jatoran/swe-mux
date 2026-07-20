from __future__ import annotations

import asyncio
import builtins
import logging
import secrets
import time
import uuid
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .adapters import BackendAdapter, SpawnOptions
from .event_bus import EventBus
from .git_projects import ProjectIdentity, resolve_project
from .history import HistoryIndex
from .models import GitState, SessionRecord, SessionState
from .pty_host import PtyHost
from .runtime_cwd import Osc7Parser, local_directory_from_osc7
from .win_jobobj import ReaperJob

log = logging.getLogger(__name__)


class ScrollbackBuffer:
    def __init__(self, max_bytes: int) -> None:
        self.max_bytes = max_bytes
        self._chunks: deque[bytes] = deque()
        self._size = 0
        self._written = 0

    def append(self, data: bytes) -> None:
        self._written += len(data)
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

    @property
    def position(self) -> int:
        return self._written

    def bytes_since(self, position: int) -> builtins.bytes:
        retained = self.bytes()
        retained_start = self._written - len(retained)
        if position >= self._written:
            return b""
        if position <= retained_start:
            return retained
        return retained[position - retained_start :]


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
        startup_started_at: float | None = None,
    ) -> None:
        self.record, self.pty, self.adapter = record, pty, adapter
        self.scrollback = ScrollbackBuffer(max_scrollback)
        self.subscribers: set[PtySubscriber] = set()
        self.tasks: set[asyncio.Task[Any]] = set()
        self.stopping = False
        self.stop_event = asyncio.Event()
        self.hook_secret = hook_secret
        self.ownership_job = ownership_job
        self.startup_started_at = startup_started_at or time.perf_counter()
        self.startup_measurement_task: asyncio.Task[Any] | None = None
        self.registration_task: asyncio.Task[Any] | None = None
        self.attachments_seen = 0
        # Exactly one browser connection may write terminal-generated responses
        # and user keystrokes. Without this, two attached xterms both answer
        # device-status queries and the duplicate response appears at the prompt.
        self.input_owner: str | None = None
        self.revision = 0
        self.state_source_priority = -1
        self.agent_stop_event = asyncio.Event()
        self.observer_task: asyncio.Task[Any] | None = None
        self.transcript_path: Path | None = None
        self.detection_task: asyncio.Task[Any] | None = None
        # The launcher-generated lifecycle id remains stable even when an
        # adapter later discovers and records a different native transcript id.
        # Demotion must match this token so Codex can return to its parent shell.
        self.agent_lifecycle_id: str | None = None
        # Detection is a fallback for agents launched without the mux shim. Once a
        # native run has explicitly exited, its still-recent transcript must not
        # immediately promote the containing shell again. An explicit launcher
        # promotion may reuse the same native id (for example, resume).
        self.ignored_detection_runs: set[tuple[str, str]] = set()
        self.osc7 = Osc7Parser()
        self.cwd_debounce_task: asyncio.Task[Any] | None = None
        self.cwd_switches: deque[float] = deque()
        self.cwd_telemetry_dropped = 0
        self.last_input_event_ts = 0.0
        self.last_input_report_ts = 0.0
        self.input_revision = 0
        self.terminal_mode: str | None = None
        self.terminal_mode_updated_at = 0.0
        self.observation_state: dict[str, Any] = {
            "root_turn_active": False,
            "root_completion_seen": False,
            "codex_scope": "root",
        }
        self.output_window: deque[tuple[float, int]] = deque()
        # Native transcripts report tool results by opaque invocation id.  Keep
        # this correlation in memory only; normalized events expose the stable
        # tool name without persisting backend-specific transcript identifiers.
        self.tool_names: dict[str, str] = {}

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

    def transition(self, state: SessionState, detail: str | None, *, source: str) -> bool:
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
        project_id: str,
        exe: str | None = None,
        args: list[str] | None = None,
        resume_native_id: str | None = None,
        shell_profile_id: str | None = None,
        profile_env: dict[str, str] | None = None,
        project_label: str | None = None,
        project: ProjectIdentity | None = None,
        startup_started_at: float | None = None,
        startup_timing_ms: dict[str, float] | None = None,
    ) -> Session:
        startup_started_at = startup_started_at or time.perf_counter()
        startup_timing_ms = dict(startup_timing_ms or {})
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
            project_id,
            backend,
            native_id,
            str(resolved_cwd),
            spawn_spec.executable,
            list(spawn_spec.argv),
            shell_profile_id=shell_profile_id,
            auto_named=name is None,
            state="running" if backend == "shell" else "starting",
            startup_timing_ms=startup_timing_ms,
        )
        if project is None:
            project_started_at = time.perf_counter()
            project = await resolve_project(resolved_cwd)
            startup_timing_ms["project_resolution"] = round(
                (time.perf_counter() - project_started_at) * 1000, 1
            )
        record.repository_id = project.id
        record.project_label = project_label or project.label
        record.project_root = project.root
        record.project_scope_id = project.id
        record.repo_group_id = project.repo_group_id
        record.spawn_cwd = str(resolved_cwd)
        record.spawn_project_scope_id = project.id
        record.spawn_repo_group_id = project.repo_group_id
        record.spawn_project_label = record.project_label
        record.spawn_project_root = project.root
        record.runtime_cwd = str(resolved_cwd)
        if backend in {"claude", "codex"}:
            record.agent_run_id = sid
            record.agent_run_started_at = record.created_at
            record.run_cwd = str(resolved_cwd)
            record.run_project_scope_id = project.id
            record.run_repo_group_id = project.repo_group_id
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
        pty_started_at = time.perf_counter()
        # winpty/ConPTY process creation is synchronous and can be slow when Windows
        # security scanning or a shell profile is busy. Keep it off the aiohttp loop
        # so the UI, event stream, and other terminals stay responsive meanwhile.
        pty.prepare()
        await asyncio.to_thread(pty.spawn)
        startup_timing_ms["pty_spawn"] = round((time.perf_counter() - pty_started_at) * 1000, 1)
        record.pid = pty.pid
        record.process_job_assignment = pty.reaper_assignment
        registration_started_at = time.perf_counter()
        ownership_job: ReaperJob | None = None
        ownership_error: str | None = None
        create_child = getattr(self.reaper, "create_child", None)
        if create_child:
            try:
                ownership_job = create_child()
                ownership_job.assign(record.pid)
                record.process_job_assignment += ";nested_session_job_assigned"
            except OSError as exc:
                ownership_error = str(exc)
                record.process_job_assignment += f";nested_session_job_failed:{ownership_error}"
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
            startup_started_at,
        )
        self.sessions[sid] = session
        transcript = adapter.transcript_path(native_id, resolved_cwd)
        # The PTY is usable now. Durable history/event registration shares SQLite
        # with transcript reconciliation and can occasionally queue behind a large
        # import. Never hide an already-running shell behind that bookkeeping.
        registration_task = asyncio.create_task(
            self._persist_spawn_registration(
                session,
                project,
                str(transcript) if transcript else None,
                ownership_error,
                registration_started_at,
            ),
            name=f"register-{sid}",
        )
        session.registration_task = registration_task
        session.tasks.add(registration_task)
        registration_task.add_done_callback(session.tasks.discard)
        session.tasks.add(asyncio.create_task(self._fanout(session), name=f"fanout-{sid}"))
        session.tasks.add(asyncio.create_task(self._ticker(session), name=f"ticker-{sid}"))
        if backend in {"claude", "codex"}:
            record.parser_status = "waiting"
            self._start_observer(session, transcript)
        elif backend == "shell":
            self._start_detection(session)
        startup_timing_ms["registration"] = round(
            (time.perf_counter() - registration_started_at) * 1000, 1
        )
        startup_timing_ms["server_ready"] = round(
            (time.perf_counter() - startup_started_at) * 1000, 1
        )
        return session

    async def _persist_spawn_registration(
        self,
        session: Session,
        project: ProjectIdentity,
        transcript: str | None,
        ownership_error: str | None,
        started_at: float,
    ) -> None:
        """Persist spawn metadata after the live session is already attachable."""

        try:
            await self.history.register_project_scope(project)
            await self.history.session_started(session.record, transcript)
            await self.events.emit(
                "session_spawned",
                session_id=session.record.id,
                backend=session.record.backend,
                name=session.record.name,
                project_scope_id=session.record.project_scope_id,
                repo_group_id=session.record.repo_group_id,
            )
            if ownership_error:
                await self.events.emit(
                    "process_ownership_degraded",
                    session_id=session.record.id,
                    source="process",
                    error=ownership_error,
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            # A persistence failure is operationally important, but it must not
            # tear down the PTY or strand the browser's optimistic session tab.
            log.exception("session spawn registration failed: %s", session.record.id)
        finally:
            session.record.startup_timing_ms["durable_registration"] = round(
                (time.perf_counter() - started_at) * 1000,
                1,
            )
            session.publish_update()

    @staticmethod
    async def _await_registration(session: Session) -> None:
        task = getattr(session, "registration_task", None)
        if task is not None and task is not asyncio.current_task() and not task.done():
            await asyncio.shield(task)

    def _start_observer(self, session: Session, transcript: Path | None) -> None:
        if session.observer_task and not session.observer_task.done():
            session.agent_stop_event.set()
            session.observer_task.cancel()
        session.agent_stop_event = asyncio.Event()
        session.transcript_path = transcript
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
        scan_cursor = session.scrollback.position
        detection_started_at = time.time()
        agent_names = [name for name in self.adapters if name != "shell"]
        max_name_len = max((len(name) for name in agent_names), default=0)
        seen_names: set[str] = set()
        carry = b""
        while not session.stop_event.is_set() and session.pty.isalive():
            if session.record.backend != "shell":
                return
            # Scan only output produced since the previous poll, accumulating which
            # agent names have appeared. A plain shell may never launch an agent, so
            # re-joining and re-lowercasing the whole retained scrollback twice a
            # second is pure waste; the carry tail catches a name split across the
            # poll boundary, and remembering names keeps the sticky wait-for-transcript
            # behavior even after the echoed command scrolls out of the window.
            current = session.scrollback.position
            if current > scan_cursor:
                haystack = (carry + session.scrollback.bytes_since(scan_cursor)).lower()
                scan_cursor = current
                for name in agent_names:
                    if name.encode() in haystack:
                        seen_names.add(name)
                carry = haystack[-(max_name_len - 1) :] if max_name_len > 1 else b""
            # Full-screen CLIs redraw the echoed command quickly and ANSI can split
            # prompt text, so exact ``> claude`` matching is not reliable. Only
            # output and native transcript activity created during this detection
            # pass may promote the shell; retained output from an ended agent must
            # never identify a new run.
            launched = [self.adapters[name] for name in seen_names]
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
                    Path(session.record.runtime_cwd or session.record.cwd),
                    detection_started_at,
                )
                if (adapter.name, native_id) not in session.ignored_detection_runs
            ]
            if candidates:
                _, backend, path, native_id = max(candidates)
                await self._begin_agent_run(session)
                session.adapter = self.adapters[backend]
                session.pty.graceful_exit = session.adapter.graceful_exit_keys()
                session.record.backend = backend
                session.record.native_session_id = native_id
                session.record.state = "starting"
                session.record.parser_status = "waiting"
                session.record.parser_diagnostic = None
                session.record.parser_events_seen = 0
                session.record.parser_unknown_events = 0
                session.record.parser_unknown_signatures = {}
                session.record.parser_schema_version = None
                session.observation_state = {
                    "root_turn_active": False,
                    "root_completion_seen": False,
                    "codex_scope": "root",
                }
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

    async def promote(
        self, sid: str, backend: str, native_id: str, launch_cwd: str | None = None
    ) -> Session:
        if backend not in {"claude", "codex"}:
            raise ValueError(f"cannot promote session to {backend}")
        session = self.resolve(sid)
        if session.record.backend == backend and session.agent_lifecycle_id == native_id:
            return session
        if session.record.backend == backend and session.record.native_session_id == native_id:
            session.agent_lifecycle_id = native_id
            return session
        session.ignored_detection_runs.discard((backend, native_id))
        session.agent_lifecycle_id = native_id
        await self._begin_agent_run(session, launch_cwd)
        adapter = self.adapters[backend]
        session.adapter = adapter
        session.pty.graceful_exit = adapter.graceful_exit_keys()
        session.record.backend = backend
        session.record.native_session_id = native_id
        session.record.state = "starting"
        session.record.parser_status = "waiting"
        session.record.parser_diagnostic = None
        session.record.parser_events_seen = 0
        session.record.parser_unknown_events = 0
        session.record.parser_unknown_signatures = {}
        session.record.parser_schema_version = None
        session.observation_state = {
            "root_turn_active": False,
            "root_completion_seen": False,
            "codex_scope": "root",
        }
        session.record.state_detail = None
        session.state_source_priority = -1
        if session.record.auto_named:
            session.record.name = Path(session.record.run_cwd or session.record.cwd).name or backend
        session.publish_update()
        transcript = adapter.transcript_path(
            native_id, Path(session.record.run_cwd or session.record.cwd)
        )
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
        await self._await_registration(session)
        if session.record.backend != backend:
            return session
        lifecycle_id = session.agent_lifecycle_id or session.record.native_session_id
        if lifecycle_id != native_id:
            return session
        observed_native_id = session.record.native_session_id
        session.ignored_detection_runs.add((backend, native_id))
        session.ignored_detection_runs.add((backend, observed_native_id))
        await self.history.update_agent_summary(session.record)
        await self.history.agent_run_ended(session.record, "agent_exit")
        session.agent_stop_event.set()
        if session.observer_task and not session.observer_task.done():
            session.observer_task.cancel()
            await asyncio.gather(session.observer_task, return_exceptions=True)
        session.observer_task = None
        session.agent_lifecycle_id = None
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
        session.record.parser_unknown_events = 0
        session.record.parser_unknown_signatures = {}
        session.record.parser_schema_version = None
        session.observation_state = {
            "root_turn_active": False,
            "root_completion_seen": False,
            "codex_scope": "root",
        }
        session.record.agent_run_id = None
        session.record.agent_run_started_at = None
        session.record.run_cwd = None
        session.record.run_project_scope_id = None
        session.record.run_repo_group_id = None
        session.record.repository_id = session.record.spawn_project_scope_id
        session.record.project_label = session.record.spawn_project_label
        session.record.project_root = session.record.spawn_project_root
        session.record.project_scope_id = session.record.spawn_project_scope_id
        session.record.repo_group_id = session.record.spawn_repo_group_id
        session.state_source_priority = -1
        if session.record.auto_named:
            session.record.name = f"shell-{session.record.id[:6]}"
        session.publish_update()
        await self.events.emit(
            "backend_demoted",
            session_id=session.record.id,
            source="daemon",
            backend=backend,
            native_session_id=observed_native_id,
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
                Path(session.record.run_cwd or session.record.cwd),
                session.record.created_at,
                stop_event,
            )
        if path:
            session.transcript_path = path
            native_id = adapter.transcript_native_id(path)
            if native_id:
                session.record.native_session_id = native_id
            await self._await_registration(session)
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
            timing_changed = False
            if "first_output" not in session.record.startup_timing_ms:
                first_output_at = getattr(session.pty, "first_output_at", None)
                session.record.startup_timing_ms["first_output"] = round(
                    ((first_output_at or time.perf_counter()) - session.startup_started_at) * 1000,
                    1,
                )
                timing_changed = True
            session.output_window.append((session.record.last_activity_ts, len(chunk)))
            while (
                session.output_window
                and session.record.last_activity_ts - session.output_window[0][0] > 60
            ):
                session.output_window.popleft()
            prompt_uris = session.osc7.feed(chunk)
            if prompt_uris and "first_prompt" not in session.record.startup_timing_ms:
                session.record.startup_timing_ms["first_prompt"] = round(
                    (time.perf_counter() - session.startup_started_at) * 1000, 1
                )
                timing_changed = True
            for uri in prompt_uris:
                self._queue_runtime_cwd(session, uri)
            session.scrollback.append(chunk)
            session.publish_output(chunk)
            if timing_changed:
                session.publish_update()
            if prompt_uris:
                self._schedule_startup_measurement(session, "first_prompt")

    def _schedule_startup_measurement(self, session: Session, milestone: str) -> None:
        if session.startup_measurement_task is not None:
            return
        task = asyncio.create_task(
            self.events.emit(
                "session_startup_measured",
                session_id=session.record.id,
                source="daemon",
                milestone=milestone,
                backend=session.record.backend,
                shell_profile_id=session.record.shell_profile_id,
                timing_ms=dict(session.record.startup_timing_ms),
            ),
            name=f"startup-measurement-{session.record.id}",
        )
        session.startup_measurement_task = task
        session.tasks.add(task)
        task.add_done_callback(session.tasks.discard)

    def _queue_runtime_cwd(self, session: Session, uri: str) -> None:
        path = local_directory_from_osc7(uri)
        if path is None:
            self._drop_runtime_cwd(session)
            return
        now = time.monotonic()
        while session.cwd_switches and now - session.cwd_switches[0] >= 60:
            session.cwd_switches.popleft()
        if len(session.cwd_switches) >= 12:
            self._drop_runtime_cwd(session)
            return
        if session.cwd_debounce_task and not session.cwd_debounce_task.done():
            session.cwd_debounce_task.cancel()
        task = asyncio.create_task(
            self._accept_runtime_cwd(session, path), name=f"cwd-telemetry-{session.record.id}"
        )
        session.cwd_debounce_task = task
        session.tasks.add(task)

    async def _accept_runtime_cwd(self, session: Session, path: Path) -> None:
        try:
            await asyncio.sleep(1.25)
            if session.stop_event.is_set() or not path.is_dir():
                return
            value = str(path)
            if session.record.runtime_cwd_live and session.record.runtime_cwd == value:
                return
            now = time.monotonic()
            while session.cwd_switches and now - session.cwd_switches[0] >= 60:
                session.cwd_switches.popleft()
            if len(session.cwd_switches) >= 12:
                self._drop_runtime_cwd(session)
                return
            project = await resolve_project(path)
            known = await self.history.project_scope(project.id)
            session.cwd_switches.append(now)
            session.record.runtime_cwd = value
            session.record.runtime_cwd_live = True
            session.record.runtime_cwd_source = "osc7"
            session.record.runtime_cwd_updated_at = time.time()
            session.record.runtime_project_scope_id = project.id if known else None
            session.record.git = GitState()
            session.publish_update()
            await self.events.emit(
                "runtime_cwd_changed",
                session_id=session.record.id,
                source="pty",
                cwd=value,
                project_scope_id=session.record.runtime_project_scope_id,
                dropped=session.cwd_telemetry_dropped,
            )
        except asyncio.CancelledError:
            raise

    @staticmethod
    def _drop_runtime_cwd(session: Session) -> None:
        session.cwd_telemetry_dropped = min(session.cwd_telemetry_dropped + 1, 1_000_000)
        session.record.runtime_cwd_dropped = session.cwd_telemetry_dropped

    async def _begin_agent_run(self, session: Session, launch_cwd: str | None = None) -> None:
        """Capture immutable ownership for one agent invocation."""
        await self._await_registration(session)
        if session.record.agent_run_id:
            return
        cwd = Path(
            launch_cwd
            or session.record.runtime_cwd
            or session.record.spawn_cwd
            or session.record.cwd
        )
        if not cwd.is_dir():
            cwd = Path(session.record.spawn_cwd or session.record.cwd)
        project = await resolve_project(cwd)
        await self.history.register_project_scope(project)
        session.record.agent_run_id = str(uuid.uuid4())
        session.record.agent_run_started_at = time.time()
        session.record.run_cwd = str(cwd.resolve())
        session.record.run_project_scope_id = project.id
        session.record.run_repo_group_id = project.repo_group_id
        session.record.tokens_in = 0
        session.record.tokens_out = 0
        session.record.context_window = 0
        session.record.context_pct = 0
        session.record.context_peak_pct = 0
        session.record.model = None
        session.record.measurement_source = None
        if launch_cwd:
            session.record.runtime_cwd = session.record.run_cwd
            session.record.runtime_cwd_live = True
            session.record.runtime_cwd_source = "agent-launcher"
            session.record.runtime_cwd_updated_at = time.time()
            session.record.runtime_project_scope_id = project.id
        # Compatibility fields describe the active authoritative owner. Explicit
        # spawn/runtime/run fields remove the old ambiguity for new clients.
        session.record.repository_id = project.id
        session.record.project_label = project.label
        session.record.project_root = project.root
        session.record.project_scope_id = project.id
        session.record.repo_group_id = project.repo_group_id

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
        self._schedule_startup_measurement(session, "session_end")
        if session.startup_measurement_task:
            await asyncio.gather(session.startup_measurement_task, return_exceptions=True)
        await self._await_registration(session)
        session.agent_stop_event.set()
        if session.cwd_debounce_task and not session.cwd_debounce_task.done():
            session.cwd_debounce_task.cancel()
        if session.record.agent_run_id:
            await self.history.update_agent_summary(session.record)
            await self.history.agent_run_ended(session.record, reason)
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
