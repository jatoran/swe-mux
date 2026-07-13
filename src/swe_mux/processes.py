from __future__ import annotations

import asyncio
import signal
import time
import uuid
from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from .event_bus import EventBus
from .session import Session, SessionManager

try:
    import psutil
except ImportError:  # pragma: no cover - diagnostics cover an unsynchronized dev venv
    psutil = None

MAX_PROCESSES_PER_SESSION = 256
ENDED_RETENTION_SECONDS = 300.0
HIGH_MEMORY_BYTES = 2 * 1024 * 1024 * 1024
NO_OUTPUT_SECONDS = 300.0
PREVIEW_LOOPBACK_HOSTS = {"127.0.0.1", "::1"}


@dataclass(slots=True)
class OwnedProcess:
    pid: int
    parent_pid: int | None
    session_id: str
    executable: str
    command: str
    started_at: float | None
    exited_at: float | None
    cpu_pct: float
    memory_bytes: int
    listeners: list[dict[str, Any]]
    conditions: list[str]

    def snapshot(self) -> dict[str, Any]:
        return asdict(self)


class ProcessInspector:
    def __init__(self, sessions: SessionManager, events: EventBus, cadence: float = 2.0) -> None:
        self.sessions = sessions
        self.events = events
        self.cadence = cadence
        self.owned: dict[tuple[int, float], OwnedProcess] = {}
        self._task: asyncio.Task[None] | None = None
        self._listeners: set[tuple[str, int, str]] = set()

    @property
    def available(self) -> bool:
        return psutil is not None

    def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="process-inspector")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

    async def _run(self) -> None:
        while True:
            await asyncio.sleep(self.cadence)
            await self.reconcile()

    async def reconcile(self) -> None:
        if psutil is None:
            return
        snapshots = await asyncio.to_thread(self._collect_all)
        now = time.time()
        previous_listeners = self._listeners
        current_listeners = {
            (process.session_id, int(listener["port"]), str(listener["host"]))
            for process in snapshots
            for listener in process.listeners
        }
        self._listeners = current_listeners
        for session_id, port, host in current_listeners - previous_listeners:
            await self.events.emit(
                "listener_detected",
                session_id=session_id,
                source="process",
                host=host,
                port=port,
            )
        for session_id, port, host in previous_listeners - current_listeners:
            await self.events.emit(
                "listener_closed",
                session_id=session_id,
                source="process",
                host=host,
                port=port,
            )
        self.owned = {
            key: process
            for key, process in self.owned.items()
            if process.exited_at is None or now - process.exited_at < ENDED_RETENTION_SECONDS
        }

    def _collect_all(self) -> list[OwnedProcess]:
        result: list[OwnedProcess] = []
        seen: set[tuple[int, float]] = set()
        for session in self.sessions.sessions.values():
            for item in self._collect_session(session):
                key = (item.pid, item.started_at or 0.0)
                seen.add(key)
                self.owned[key] = item
                result.append(item)
        now = time.time()
        for key, item in self.owned.items():
            if key not in seen and item.exited_at is None:
                item.exited_at = now
                item.cpu_pct = 0.0
                item.listeners = []
        return result

    def _collect_session(self, session: Session) -> list[OwnedProcess]:
        if psutil is None or session.record.pid <= 0:
            return []
        try:
            root = psutil.Process(session.record.pid)
            processes = [root, *root.children(recursive=True)][:MAX_PROCESSES_PER_SESSION]
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return []
        result: list[OwnedProcess] = []
        for process in processes:
            try:
                with process.oneshot():
                    pid = process.pid
                    parent_pid = process.ppid()
                    executable = process.name()
                    command = " ".join(process.cmdline())[:1000]
                    started_at = process.create_time()
                    cpu = process.cpu_percent(interval=None)
                    memory = process.memory_info().rss
                listeners = self._listeners_for(process)
            except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
                continue
            conditions: list[str] = []
            if cpu >= 90:
                conditions.append("high_cpu")
            if memory >= HIGH_MEMORY_BYTES:
                conditions.append("high_memory")
            if (
                listeners
                and time.time() - session.record.last_activity_ts >= NO_OUTPUT_SECONDS
            ):
                conditions.append("no_pty_output")
            result.append(
                OwnedProcess(
                    pid,
                    parent_pid,
                    session.record.id,
                    executable,
                    command,
                    started_at,
                    None,
                    round(cpu, 1),
                    memory,
                    listeners,
                    conditions,
                )
            )
        return result

    def _listeners_for(self, process: Any) -> list[dict[str, Any]]:
        if psutil is None:
            return []
        listeners: list[dict[str, Any]] = []
        try:
            connections = process.net_connections(kind="inet")
        except (psutil.AccessDenied, psutil.NoSuchProcess, OSError):
            return listeners
        for connection in connections:
            if connection.status != psutil.CONN_LISTEN or not connection.laddr:
                continue
            host = str(connection.laddr.ip)
            port = int(connection.laddr.port)
            listeners.append(
                {
                    "host": host,
                    "port": port,
                    "loopback": host in {"127.0.0.1", "::1", "localhost"},
                    "url": f"http://{'[' + host + ']' if ':' in host else host}:{port}/",
                }
            )
        return listeners

    async def snapshot(self, session_id: str) -> dict[str, Any]:
        if session_id not in self.sessions.sessions:
            raise KeyError(session_id)
        if not self.available:
            return {
                "available": False,
                "diagnostic": "psutil is not installed in the active environment",
                "session_id": session_id,
                "processes": [],
            }
        await asyncio.to_thread(self._collect_all)
        processes = [
            item.snapshot()
            for item in self.owned.values()
            if item.session_id == session_id
        ]
        processes.sort(key=lambda item: (item["exited_at"] is not None, item["pid"]))
        return {
            "available": True,
            "session_id": session_id,
            "processes": processes[:MAX_PROCESSES_PER_SESSION],
        }

    def _owned_live(self, session_id: str, pid: int) -> tuple[Any, OwnedProcess]:
        if psutil is None:
            raise ValueError("process inspection is unavailable")
        matches = [
            item
            for item in self.owned.values()
            if item.session_id == session_id and item.pid == pid and item.exited_at is None
        ]
        if len(matches) != 1:
            raise ValueError("process is not owned by this session")
        item = matches[0]
        try:
            process = psutil.Process(pid)
            if abs(process.create_time() - (item.started_at or 0)) > 0.01:
                raise ValueError("process identity changed")
        except psutil.NoSuchProcess as exc:
            raise ValueError("process no longer exists") from exc
        return process, item

    async def act(self, session_id: str, pid: int, action: str) -> dict[str, Any]:
        await asyncio.to_thread(self._collect_all)
        process, _ = self._owned_live(session_id, pid)
        session = self.sessions.sessions[session_id]
        if action == "interrupt":
            if pid == session.record.pid:
                session.pty.write(b"\x03")
            else:
                await asyncio.to_thread(process.send_signal, signal.SIGINT)
        elif action == "terminate":
            await asyncio.to_thread(process.terminate)
        elif action == "terminate_tree":
            children = await asyncio.to_thread(process.children, recursive=True)
            owned_pids = {
                item.pid
                for item in self.owned.values()
                if item.session_id == session_id and item.exited_at is None
            }
            for child in reversed(children):
                if child.pid in owned_pids:
                    child.terminate()
            process.terminate()
        else:
            raise ValueError("action must be interrupt, terminate, or terminate_tree")
        await self.events.emit(
            "process_action",
            session_id=session_id,
            source="user",
            pid=pid,
            action=action,
        )
        return await self.snapshot(session_id)


@dataclass(slots=True)
class PreviewRegistration:
    id: str
    session_id: str
    space_id: str
    url: str
    host: str
    port: int
    source: str
    created_at: float
    viewport: str = "responsive"

    def snapshot(self) -> dict[str, Any]:
        return asdict(self)


class PreviewRegistry:
    def __init__(self, inspector: ProcessInspector, sessions: SessionManager) -> None:
        self.inspector = inspector
        self.sessions = sessions
        self.items: dict[str, PreviewRegistration] = {}

    async def register(
        self, session_id: str, url: str, *, approved: bool = False
    ) -> PreviewRegistration:
        session = self.sessions.resolve(session_id)
        try:
            parsed = urlsplit(url)
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
        except ValueError as exc:
            raise ValueError("preview URL has an invalid port") from exc
        host = parsed.hostname or ""
        if parsed.scheme not in {"http", "https"}:
            raise ValueError("preview URL must use HTTP or HTTPS")
        if host not in PREVIEW_LOOPBACK_HOSTS:
            raise ValueError("preview destination must be a literal loopback address")
        if parsed.username or parsed.password:
            raise ValueError("preview URL cannot contain credentials")
        if parsed.fragment:
            raise ValueError("preview URL cannot contain a fragment")
        if parsed.query:
            raise ValueError("preview registration URL cannot contain a query")
        if not 1 <= port <= 65535:
            raise ValueError("preview URL has an invalid port")
        netloc = f"[{host}]:{port}" if ":" in host else f"{host}:{port}"
        normalized_url = urlunsplit(
            (parsed.scheme, netloc, parsed.path.rstrip("/") + "/", "", "")
        )
        snapshot = await self.inspector.snapshot(session.record.id)
        detected = any(
            listener["port"] == port
            and listener.get("host") == host
            and listener.get("loopback") is True
            for process in snapshot["processes"]
            for listener in process["listeners"]
        )
        if not detected and not approved:
            raise ValueError("preview listener is not owned by this session; approval is required")
        item = PreviewRegistration(
            str(uuid.uuid4()),
            session.record.id,
            session.record.space_id,
            normalized_url,
            host,
            port,
            "detected" if detected else "user-approved",
            time.time(),
        )
        self.items[item.id] = item
        return item

    def remove(self, preview_id: str) -> None:
        if preview_id not in self.items:
            raise KeyError(preview_id)
        del self.items[preview_id]

    async def list(self, session_id: str | None = None) -> dict[str, Any]:
        candidates: list[dict[str, Any]] = []
        if session_id:
            snapshot = await self.inspector.snapshot(session_id)
            candidates = [
                {"session_id": session_id, **listener}
                for process in snapshot["processes"]
                for listener in process["listeners"]
                if listener["loopback"]
            ]
        return {
            "items": [
                item.snapshot()
                for item in self.items.values()
                if not session_id or item.session_id == session_id
            ],
            "candidates": candidates,
        }
