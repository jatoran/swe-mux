from __future__ import annotations

import asyncio
import signal
import time
import uuid
from dataclasses import asdict, dataclass, field
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
# How long a detected preview outlives its listener. Dev servers rebind constantly
# (a restart closes and reopens the socket), so a preview must survive that gap
# rather than vanish on every reload; a server that is actually stopped is reaped
# once the gap exceeds this.
PREVIEW_LISTENER_GRACE_SECONDS = 20.0
HIGH_MEMORY_BYTES = 2 * 1024 * 1024 * 1024
NO_OUTPUT_SECONDS = 300.0
PREVIEW_LOOPBACK_HOSTS = {"127.0.0.1", "::1"}
# A wildcard bind means "every local interface", which necessarily includes loopback:
# a server on 0.0.0.0:5173 really is reachable at 127.0.0.1:5173. Most dev servers
# bind the wildcard by default, so report those listeners at their loopback address.
# This deliberately does not relax PREVIEW_LOOPBACK_HOSTS above: the address the
# bridge dials stays a literal loopback one. A bind to a single non-loopback address
# is left alone, because it genuinely is not reachable on loopback.
WILDCARD_LOOPBACK_HOSTS = {"0.0.0.0": "127.0.0.1", "::": "::1", "::ffff:0.0.0.0": "127.0.0.1"}
LISTENER_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}


def listener_record(host: str, port: int) -> dict[str, Any]:
    """Describe one listening socket at the address a client can actually reach."""
    resolved = WILDCARD_LOOPBACK_HOSTS.get(host, host)
    return {
        "host": resolved,
        "port": port,
        "loopback": resolved in LISTENER_LOOPBACK_HOSTS,
        "url": f"http://{'[' + resolved + ']' if ':' in resolved else resolved}:{port}/",
    }


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
    connections: list[dict[str, Any]] = field(default_factory=list)

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
        self._cpu_samples: dict[tuple[int, float], tuple[float, float]] = {}
        self._sample_lock = asyncio.Lock()
        self._last_collect = 0.0

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
        async with self._sample_lock:
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

    def live_listeners(self) -> set[tuple[str, int, str]]:
        """`(session_id, port, host)` for every listener seen by the last reconcile."""
        return set(self._listeners)

    async def _ensure_sampled(self, *, force: bool = False) -> None:
        """Collect a fresh sample only when the cached one is stale.

        The background loop already refreshes every ``cadence`` seconds, so HTTP read
        paths (session snapshot, fleet view, preview candidates) can reuse that sample
        instead of forcing a full descendant re-walk plus socket enumeration on every
        poll. ``force`` is used by ownership-sensitive actions that require live state.
        """
        async with self._sample_lock:
            if force or time.monotonic() - self._last_collect >= self.cadence:
                await asyncio.to_thread(self._collect_all)

    def _collect_all(self) -> list[OwnedProcess]:
        result: list[OwnedProcess] = []
        seen: set[tuple[int, float]] = set()
        # Enumerate the whole OS socket table once and bucket by owning PID. Calling
        # net_connections() per process would rescan the entire table for every one of
        # potentially hundreds of descendants each tick.
        conn_map = self._connections_by_pid()
        for session in self.sessions.sessions.values():
            for item in self._collect_session(session, conn_map):
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
                item.connections = []
        self._cpu_samples = {
            key: sample for key, sample in self._cpu_samples.items() if key in seen
        }
        self._last_collect = time.monotonic()
        return result

    def _connections_by_pid(self) -> dict[int, list[Any]]:
        if psutil is None:
            return {}
        try:
            connections = psutil.net_connections(kind="inet")
        except (psutil.AccessDenied, psutil.NoSuchProcess, OSError):
            return {}
        grouped: dict[int, list[Any]] = {}
        for connection in connections:
            if connection.pid is None or not connection.laddr:
                continue
            grouped.setdefault(connection.pid, []).append(connection)
        return grouped

    def _collect_session(
        self, session: Session, conn_map: dict[int, list[Any]]
    ) -> list[OwnedProcess]:
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
                    cpu_times = process.cpu_times()
                    memory = process.memory_info().rss
                sample_key = (pid, started_at)
                sampled_at = time.monotonic()
                total_cpu = float(cpu_times.user + cpu_times.system)
                previous = self._cpu_samples.get(sample_key)
                cpu = (
                    max(0.0, (total_cpu - previous[1]) / (sampled_at - previous[0]) * 100)
                    if previous and sampled_at > previous[0]
                    else 0.0
                )
                self._cpu_samples[sample_key] = (sampled_at, total_cpu)
                listeners, connections = self._network_from(pid, conn_map)
            except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
                continue
            conditions: list[str] = []
            if cpu >= 90:
                conditions.append("high_cpu")
            if memory >= HIGH_MEMORY_BYTES:
                conditions.append("high_memory")
            if listeners and time.time() - session.record.last_activity_ts >= NO_OUTPUT_SECONDS:
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
                    connections,
                )
            )
        return result

    def _network_for(self, process: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        if psutil is None:
            return [], []
        listeners: list[dict[str, Any]] = []
        established: list[dict[str, Any]] = []
        try:
            connections = process.net_connections(kind="inet")
        except (psutil.AccessDenied, psutil.NoSuchProcess, OSError):
            return listeners, established
        for connection in connections:
            if not connection.laddr:
                continue
            if connection.status == psutil.CONN_LISTEN:
                listeners.append(
                    listener_record(str(connection.laddr.ip), int(connection.laddr.port))
                )
            elif connection.status == psutil.CONN_ESTABLISHED and connection.raddr:
                established.append(
                    {
                        "local_host": str(connection.laddr.ip),
                        "local_port": int(connection.laddr.port),
                        "remote_host": str(connection.raddr.ip),
                        "remote_port": int(connection.raddr.port),
                    }
                )
        return listeners, established

    def _network_from(
        self, pid: int, conn_map: dict[int, list[Any]]
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        if psutil is None:
            return [], []
        listeners: list[dict[str, Any]] = []
        established: list[dict[str, Any]] = []
        for connection in conn_map.get(pid, ()):
            if not connection.laddr:
                continue
            if connection.status == psutil.CONN_LISTEN:
                listeners.append(
                    listener_record(str(connection.laddr.ip), int(connection.laddr.port))
                )
            elif connection.status == psutil.CONN_ESTABLISHED and connection.raddr:
                established.append(
                    {
                        "local_host": str(connection.laddr.ip),
                        "local_port": int(connection.laddr.port),
                        "remote_host": str(connection.raddr.ip),
                        "remote_port": int(connection.raddr.port),
                    }
                )
        return listeners, established

    def _listeners_for(self, process: Any) -> list[dict[str, Any]]:
        return self._network_for(process)[0]

    async def snapshot(self, session_id: str, *, force: bool = False) -> dict[str, Any]:
        if session_id not in self.sessions.sessions:
            raise KeyError(session_id)
        if not self.available:
            record = self.sessions.sessions[session_id].record
            return {
                "available": False,
                "diagnostic": "psutil is not installed in the active environment",
                "session_id": session_id,
                "project_scope_id": record.trusted_scope_id,
                "repo_group_id": (
                    record.run_repo_group_id if record.agent_run_id else record.spawn_repo_group_id
                ),
                "processes": [],
            }
        await self._ensure_sampled(force=force)
        processes = [
            item.snapshot() for item in self.owned.values() if item.session_id == session_id
        ]
        processes.sort(key=lambda item: (item["exited_at"] is not None, item["pid"]))
        session = self.sessions.sessions[session_id].record
        return {
            "available": True,
            "session_id": session_id,
            "project_scope_id": session.trusted_scope_id,
            "repo_group_id": (
                session.run_repo_group_id if session.agent_run_id else session.spawn_repo_group_id
            ),
            "processes": processes[:MAX_PROCESSES_PER_SESSION],
        }

    async def snapshot_all(self) -> dict[str, Any]:
        """Return one coherently sampled process tree for every live mux session."""
        if not self.available:
            return {
                "available": False,
                "diagnostic": "psutil is not installed in the active environment",
                "sessions": [],
                "totals": {
                    "processes": 0,
                    "cpu_pct": 0.0,
                    "memory_bytes": 0,
                    "listeners": 0,
                    "connections": 0,
                },
            }
        await self._ensure_sampled()
        groups: list[dict[str, Any]] = []
        all_processes: list[dict[str, Any]] = []
        for session_id, session in self.sessions.sessions.items():
            processes = [
                item.snapshot() for item in self.owned.values() if item.session_id == session_id
            ]
            processes.sort(key=lambda item: (item["exited_at"] is not None, item["pid"]))
            all_processes.extend(processes)
            groups.append(
                {
                    "session_id": session_id,
                    "space_id": session.record.space_id,
                    "project_scope_id": session.record.trusted_scope_id,
                    "repo_group_id": (
                        session.record.run_repo_group_id
                        if session.record.agent_run_id
                        else session.record.spawn_repo_group_id
                    ),
                    "processes": processes[:MAX_PROCESSES_PER_SESSION],
                }
            )
        live = [item for item in all_processes if item["exited_at"] is None]
        return {
            "available": True,
            "sessions": groups,
            "totals": {
                "processes": len(live),
                "cpu_pct": round(sum(float(item["cpu_pct"]) for item in live), 1),
                "memory_bytes": sum(int(item["memory_bytes"]) for item in live),
                "listeners": sum(len(item["listeners"]) for item in live),
                "connections": sum(len(item["connections"]) for item in live),
            },
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
        await self._ensure_sampled(force=True)
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
    project_scope_id: str | None = None
    repo_group_id: str | None = None
    viewport: str = "responsive"

    def snapshot(self) -> dict[str, Any]:
        return asdict(self)


class PreviewRegistry:
    def __init__(self, inspector: ProcessInspector, sessions: SessionManager) -> None:
        self.inspector = inspector
        self.sessions = sessions
        self.items: dict[str, PreviewRegistration] = {}
        self._listener_seen: dict[str, float] = {}

    def prune(self, now: float | None = None) -> list[PreviewRegistration]:
        """Drop detected previews whose server has stopped listening.

        A preview is registered against a listener this session owns, so when that
        listener goes away for good the preview points at nothing and must stop
        occupying a tab and a sidebar row. Liveness is only rechecked here, never
        assumed: a listener still up refreshes its timestamp, and only an absence
        longer than PREVIEW_LISTENER_GRACE_SECONDS reaps it, so a restarting dev
        server keeps its tab.

        User-approved previews are never reaped. mux could not attribute that
        listener to the session in the first place (it may be in WSL, Docker, or
        another process tree), so its absence from the owned set is not evidence
        that it stopped. Those stay until removed explicitly.
        """
        moment = time.time() if now is None else now
        live = self.inspector.live_listeners()
        removed: list[PreviewRegistration] = []
        for item in list(self.items.values()):
            if item.source != "detected":
                continue
            if (item.session_id, item.port, item.host) in live:
                self._listener_seen[item.id] = moment
                continue
            last_seen = self._listener_seen.setdefault(item.id, moment)
            if moment - last_seen >= PREVIEW_LISTENER_GRACE_SECONDS:
                del self.items[item.id]
                self._listener_seen.pop(item.id, None)
                removed.append(item)
        return removed

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
        normalized_url = urlunsplit((parsed.scheme, netloc, parsed.path.rstrip("/") + "/", "", ""))
        # Force a live sample: a listener launched moments ago must be detectable for
        # ownership auto-approval rather than waiting on the cached sampling interval.
        snapshot = await self.inspector.snapshot(session.record.id, force=True)
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
            getattr(
                session.record,
                "trusted_scope_id",
                getattr(session.record, "project_scope_id", None),
            ),
            getattr(session.record, "repo_group_id", None),
        )
        self.items[item.id] = item
        self._listener_seen[item.id] = time.time()
        return item

    def remove(self, preview_id: str) -> None:
        if preview_id not in self.items:
            raise KeyError(preview_id)
        del self.items[preview_id]
        self._listener_seen.pop(preview_id, None)

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
