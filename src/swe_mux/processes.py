from __future__ import annotations

import asyncio
import os
import signal
import time
import uuid
from contextlib import suppress
from dataclasses import asdict, dataclass, field
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from .background_tasks import background
from .event_bus import EventBus
from .operational_telemetry import command_hash, process_identity
from .session import Session, SessionManager, clear_standing_activity

try:
    import psutil
except ImportError:  # pragma: no cover - diagnostics cover an unsynchronized dev venv
    psutil = None

PROCESS_INSPECTOR_LOOP = "process-inspector"
# Creation times round-trip through float seconds on both sides; the existing
# ownership re-check uses the same tolerance.
_CREATE_TIME_TOLERANCE_SECONDS = 0.01
MAX_PROCESSES_PER_SESSION = 256
ENDED_RETENTION_SECONDS = 24 * 60 * 60.0
# How long a detected preview outlives its listener. Dev servers rebind constantly
# (a restart closes and reopens the socket), so a preview must survive that gap
# rather than vanish on every reload; a server that is actually stopped is reaped
# once the gap exceeds this.
PREVIEW_LISTENER_GRACE_SECONDS = 20.0
HIGH_MEMORY_BYTES = 2 * 1024 * 1024 * 1024
NO_OUTPUT_SECONDS = 300.0
# Constructing a psutil.Process is the single most expensive operation in a sampling
# pass (~10-16ms each on Windows, because identity validation queries the process a
# second time). Reconstructing one per descendant per tick cost ~900ms every 5s, which
# is mostly GIL-held C work: it starved the event loop and showed up as terminal input
# that lags and then catches up. Handles are therefore cached across passes and only
# the two genuinely per-tick attributes are re-read. Identity safety is preserved by
# other means -- see _sample_handle and _owned_live.
MAX_HANDLE_CACHE = 4096
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
    project_id: str | None = None
    agent_run_id: str | None = None
    identity_id: str | None = None
    command_hash: str = ""
    parent_lineage: list[dict[str, Any]] = field(default_factory=list)
    job_assignment: str = "unknown"
    evidence_state: str = "active"
    evidence_reason: str = "live_descendant_fingerprint_match"
    confidence: str = "high"
    first_seen: float | None = None
    last_seen: float | None = None
    last_verified_at: float | None = None
    exit_evidence: str | None = None
    inaccessible_count: int = 0
    startup_revalidated: bool = False
    # When the owning session was first observed to have ended. Stamped once so the
    # orphan grace measures from that moment; deriving it from last_seen could never
    # elapse, because last_seen is refreshed on every pass.
    root_ended_at: float | None = None

    def snapshot(self) -> dict[str, Any]:
        return asdict(self)


class ProcessInspector:
    def __init__(
        self,
        sessions: SessionManager,
        events: EventBus,
        cadence: float = 5.0,
        *,
        telemetry: Any | None = None,
        orphan_grace_seconds: float = 15.0,
    ) -> None:
        self.sessions = sessions
        self.events = events
        self.cadence = cadence
        self.telemetry = telemetry
        self.orphan_grace_seconds = orphan_grace_seconds
        self.owned: dict[tuple[int, float], OwnedProcess] = {}
        self._task: asyncio.Task[None] | None = None
        self._listeners: set[tuple[str, int, str]] = set()
        self._cpu_samples: dict[tuple[int, float], tuple[float, float]] = {}
        self._sample_lock = asyncio.Lock()
        self._last_collect = 0.0
        self._last_persist = 0.0
        # Live psutil.Process handles reused across passes, keyed by pid.
        self._handles: dict[int, tuple[Any, int]] = {}
        # (name, command, command_hash) per pid. A live process never renames itself and
        # its command line is fixed at exec, so this is read once per handle rather than
        # per pass; cmdline() is a remote-PEB read and was ~40% of the per-process cost.
        self._static: dict[int, tuple[str, str, str]] = {}
        # pid -> child pids, rebuilt once per pass from a single system-wide parent map.
        self._children_by_pid: dict[int, list[int]] = {}
        # pid -> parent pid from that same map, used to detect pid reuse for free.
        self._parents: dict[int, int] = {}
        self._daemon_resources: dict[str, Any] = {
            "pid": os.getpid(),
            "processes": 0,
            "cpu_pct": 0.0,
            "memory_bytes": 0,
            "listeners": 0,
            "connections": 0,
            "members": [],
        }

    async def restore(self) -> None:
        """Load bounded durable fingerprints before startup revalidation."""
        if self.telemetry is None:
            return
        for item in await self.telemetry.process_candidates():
            started = float(item.get("creation_time") or 0)
            if not started:
                continue
            # An already-exited fingerprint can never become live again, and its
            # durable record stays in process_evidence regardless. Restoring it
            # would only republish a previous daemon run's dead processes into
            # the live fleet. Only candidates that might still be running are
            # worth revalidating.
            if item.get("exited_at"):
                continue
            process = OwnedProcess(
                pid=int(item["pid"]),
                parent_pid=item.get("parent_pid"),
                session_id=str(item["session_id"]),
                executable=str(item.get("executable") or "unavailable"),
                command="",
                started_at=started,
                exited_at=item.get("exited_at"),
                cpu_pct=0,
                memory_bytes=0,
                listeners=[],
                conditions=[],
                project_id=item.get("project_id"),
                agent_run_id=item.get("agent_run_id"),
                identity_id=str(item["identity_id"]),
                command_hash=str(item.get("command_hash") or ""),
                parent_lineage=list(item.get("parent_lineage") or []),
                job_assignment=str(item.get("job_assignment") or "unknown"),
                evidence_state=str(item.get("state") or "stale"),
                evidence_reason=str(item.get("reason") or "restored_evidence"),
                confidence=str(item.get("confidence") or "low"),
                first_seen=item.get("first_seen"),
                last_seen=item.get("last_seen"),
                last_verified_at=item.get("last_verified_at"),
                exit_evidence=item.get("exit_evidence"),
                inaccessible_count=int(item.get("inaccessible_count") or 0),
            )
            self.owned[(process.pid, started)] = process
        if self.available:
            await self.reconcile(startup=True)

    @property
    def available(self) -> bool:
        return psutil is not None

    def start(self) -> None:
        self._task = background.start(PROCESS_INSPECTOR_LOOP, self._run)

    async def stop(self) -> None:
        await background.stop(PROCESS_INSPECTOR_LOOP)
        self._task = None

    async def _run(self) -> None:
        while True:
            await asyncio.sleep(self.cadence)
            with background.iteration(PROCESS_INSPECTOR_LOOP):
                await self.reconcile()

    async def reconcile(self, *, startup: bool = False) -> None:
        if psutil is None:
            return
        async with self._sample_lock:
            snapshots = await asyncio.to_thread(self._collect_all, startup)
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
        self._fast_clear_background_annotations(snapshots, now)
        if self.telemetry is not None and (
            startup or now - self._last_persist >= max(10.0, self.cadence * 2)
        ):
            await self.telemetry.record_process_observations(
                [item.snapshot() for item in self.owned.values()]
            )
            self._last_persist = now

    def live_listeners(self) -> set[tuple[str, int, str]]:
        """`(session_id, port, host)` for every listener seen by the last reconcile."""
        return set(self._listeners)

    # Grace between an annotation appearing and the process tree being trusted to
    # refute it: a launch's child may not exist yet on the pass that races it.
    BACKGROUND_FAST_CLEAR_MIN_AGE_SECONDS = 15.0

    def _fast_clear_background_annotations(self, snapshots: list[Any], now: float) -> None:
        """A vanished process cannot still be working — the strongest *clear*.

        A `background_tasks` annotation whose session has no live descendant
        besides the CLI root itself is cleared immediately instead of waiting
        for its TTL. Never the reverse: descendants alone open nothing (an MCP
        server child is not a background task), and a session with any extra
        descendant is left to transcript evidence and the TTL — which also makes
        a false clear structurally impossible while a task's process runs.
        """
        live_counts: dict[str, int] = {}
        for item in snapshots:
            live_counts[item.session_id] = live_counts.get(item.session_id, 0) + 1
        for session in self.sessions.sessions.values():
            # getattr-guarded like the rest of the inspector: tests drive it
            # with lightweight record stand-ins.
            record = session.record
            if getattr(record, "backend", None) not in {"claude", "codex"}:
                continue
            if getattr(record, "state", None) in {"exited", "crashed"}:
                continue
            standing = getattr(record, "standing_activity", None) or []
            annotation = next((a for a in standing if a.kind == "background_tasks"), None)
            if annotation is None:
                continue
            if now - annotation.since < self.BACKGROUND_FAST_CLEAR_MIN_AGE_SECONDS:
                continue
            if live_counts.get(record.id, 0) != 1:
                # 0 means the root itself is gone (session exit owns that);
                # >1 means something still runs under the CLI.
                continue
            if clear_standing_activity(
                session, "background_tasks", evidence="process:descendants_zero", now=now
            ):
                observation_state = getattr(session, "observation_state", None)
                if isinstance(observation_state, dict):
                    observation_state.get("background_open", {}).clear()
                session.publish_update()

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

    def _refresh_tree(self) -> None:
        """Rebuild the parent/child index from one system-wide snapshot.

        ``Process.children(recursive=True)`` takes its own snapshot of every process on
        the machine, so calling it once per session root re-walked the whole table N
        times per tick (~16ms each). One shared map serves every root and the daemon
        tree. ``_ppid_map`` is psutil-private; if it is ever withdrawn the walk falls
        back to ``children(recursive=True)`` via ``_descendants`` returning ``None``.
        """
        self._children_by_pid = {}
        self._parents = {}
        ppid_map = getattr(psutil, "_ppid_map", None)
        if ppid_map is None:
            return
        try:
            table = dict(ppid_map())
        except Exception:  # pragma: no cover - defensive around a private API
            return
        children: dict[int, list[int]] = {}
        for child, parent in table.items():
            children.setdefault(parent, []).append(child)
        self._children_by_pid = children
        self._parents = table

    def _tree_handles(self, root_pid: int, limit: int) -> list[Any]:
        """Cached psutil handles for ``root_pid`` and its real descendants, root first.

        **A raw parent map is not a process tree.** Windows never clears a dead
        parent's pid from a child's ppid field and recycles pids aggressively, so the
        map contains parent links that were never real: a long-lived process keeps
        pointing at a pid that now belongs to something else entirely. Walking it
        naively does not merely add a stray row — it can splice two unrelated trees
        together. One such link made the PTY supervisor look like a descendant of one
        session, and because the supervisor parents *every* session, that session
        absorbed the whole fleet while the others reported zero processes.

        `Process.children(recursive=True)` guards this by rejecting any descendant that
        predates the root, and this walk must reproduce that rule exactly rather than
        trust the map. An excluded pid is not traversed either: everything under a link
        that was never real is equally not ours.
        """
        if psutil is None:
            return []
        root = self._handle(root_pid)
        if root is None:
            return []
        if not self._children_by_pid:
            try:
                return [root, *root.children(recursive=True)][:limit]
            except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
                return [root]
        try:
            root_started = float(root.create_time())
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            return [root]
        handles: list[Any] = [root]
        seen: set[int] = {root_pid}
        stack: list[int] = list(self._children_by_pid.get(root_pid, ()))
        while stack and len(handles) < limit:
            pid = stack.pop()
            if pid in seen:
                continue
            seen.add(pid)
            handle = self._handle(pid)
            if handle is None:
                continue
            try:
                started = float(handle.create_time())
            except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
                continue
            if started < root_started:
                continue
            handles.append(handle)
            stack.extend(self._children_by_pid.get(pid, ()))
        return handles

    def _handle(self, pid: int) -> Any | None:
        """Return a cached handle for ``pid``, constructing one only when unseen.

        A cached handle memoizes its creation time, so a pid recycled onto a different
        process would keep the old identity. The parent map read this pass closes that
        window at no cost: Windows never changes a live process's parent pid, so a
        changed parent means the pid was recycled and the handle is rebuilt.
        """
        if psutil is None:
            return None
        cached = self._handles.get(pid)
        parent = self._parents.get(pid)
        if cached is not None:
            handle, captured_parent = cached
            if parent is None or parent == captured_parent:
                return handle
            self._forget(pid)
        try:
            handle = psutil.Process(pid)
            captured_parent = parent if parent is not None else int(handle.ppid())
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            return None
        if len(self._handles) < MAX_HANDLE_CACHE:
            self._handles[pid] = (handle, captured_parent)
        return handle

    def _forget(self, pid: int) -> None:
        self._handles.pop(pid, None)
        self._static.pop(pid, None)

    def _identity(self, handle: Any, pid: int) -> tuple[str, str, str]:
        """`(name, command, command_hash)` for a handle, read once and then reused."""
        cached = self._static.get(pid)
        if cached is not None:
            return cached
        if psutil is None:
            return (f"PID {pid}", "", "")
        failed = False
        try:
            name = str(handle.name())
        except (AttributeError, psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            name = f"PID {pid}"
            failed = True
        try:
            command = " ".join(str(part) for part in handle.cmdline())[:1000]
        except (AttributeError, psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            command = ""
            failed = True
        entry = (name, command, command_hash(command))
        # Only a complete read is worth keeping. Caching a placeholder produced by a
        # transient AccessDenied would pin it for the life of the handle, where the
        # previous per-pass read would have recovered on the very next tick. A process
        # that genuinely reports an empty command line does not raise and still caches.
        if not failed and len(self._static) < MAX_HANDLE_CACHE:
            self._static[pid] = entry
        return entry

    def _collect_all(self, startup: bool = False) -> list[OwnedProcess]:
        result: list[OwnedProcess] = []
        seen: set[tuple[int, float]] = set()
        # Enumerate the whole OS socket table once and bucket by owning PID. Calling
        # net_connections() per process would rescan the entire table for every one of
        # potentially hundreds of descendants each tick.
        conn_map = self._connections_by_pid()
        self._refresh_tree()
        for session in self.sessions.sessions.values():
            for item in self._collect_session(session, conn_map):
                key = (item.pid, item.started_at or 0.0)
                seen.add(key)
                self.owned[key] = item
                result.append(item)
        attributed_pids = {item.pid for item in result}
        attributed_pids.update(item.pid for item in self.owned.values() if item.exited_at is None)
        self._daemon_resources = self._collect_daemon_resources(
            attributed_pids, seen, conn_map
        )
        now = time.time()
        self._revalidate_unseen(seen, conn_map, now, startup)
        self._cpu_samples = {
            key: sample for key, sample in self._cpu_samples.items() if key in seen
        }
        # A handle for a pid that left the tree is dead weight and, worse, would still
        # answer with its memoized identity if that pid were later recycled. Dropping it
        # here means every pid re-enters through _handle's fresh construction.
        live_pids = {pid for pid, _ in seen}
        self._handles = {pid: entry for pid, entry in self._handles.items() if pid in live_pids}
        self._static = {pid: entry for pid, entry in self._static.items() if pid in live_pids}
        self._last_collect = time.monotonic()
        return result

    def _collect_daemon_resources(
        self,
        attributed_pids: set[int],
        seen: set[tuple[int, float]],
        conn_map: dict[int, list[Any]] | None = None,
    ) -> dict[str, Any]:
        """Sample daemon/infrastructure processes not already owned by a session.

        Session descendants are children of the daemon too. Excluding their PIDs keeps
        the resource footer additive without counting the same process twice. Detailed
        members let the fleet inspector explain that aggregate without granting session
        process controls over swe-mux itself.
        """
        empty = {
            "pid": os.getpid(),
            "processes": 0,
            "cpu_pct": 0.0,
            "memory_bytes": 0,
            "listeners": 0,
            "connections": 0,
            "members": [],
        }
        if psutil is None or not hasattr(psutil, "Process"):
            return empty
        candidates = self._tree_handles(os.getpid(), MAX_PROCESSES_PER_SESSION)
        if not candidates:
            return empty
        sampled_at = time.monotonic()
        process_count = 0
        cpu_total = 0.0
        memory_total = 0
        listener_total = 0
        connection_total = 0
        members: list[dict[str, Any]] = []
        sampled_pids: set[int] = set()
        for process in candidates:
            pid = int(process.pid)
            if pid in sampled_pids or (pid != os.getpid() and pid in attributed_pids):
                continue
            sampled_pids.add(pid)
            try:
                with process.oneshot():
                    started_at = float(process.create_time())
                    cpu_times = process.cpu_times()
                    memory = int(process.memory_info().rss)
                sample_key = (pid, started_at)
                total_cpu = float(cpu_times.user + cpu_times.system)
                previous = self._cpu_samples.get(sample_key)
                cpu = (
                    max(0.0, (total_cpu - previous[1]) / (sampled_at - previous[0]) * 100)
                    if previous and sampled_at > previous[0]
                    else 0.0
                )
                self._cpu_samples[sample_key] = (sampled_at, total_cpu)
                seen.add(sample_key)
            except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
                continue
            parent_pid = self._parents.get(pid)
            if parent_pid is None:
                try:
                    parent_pid = int(process.ppid())
                except (AttributeError, psutil.NoSuchProcess, psutil.AccessDenied, OSError):
                    parent_pid = None
            executable, command, _ = self._identity(process, pid)
            listeners, connections = self._network_from(pid, conn_map or {})
            conditions: list[str] = []
            if cpu >= 90:
                conditions.append("high_cpu")
            if memory >= HIGH_MEMORY_BYTES:
                conditions.append("high_memory")
            process_count += 1
            cpu_total += cpu
            memory_total += memory
            listener_total += len(listeners)
            connection_total += len(connections)
            members.append(
                {
                    "pid": pid,
                    "parent_pid": parent_pid,
                    "executable": executable,
                    "command": command,
                    "started_at": started_at,
                    "cpu_pct": round(cpu, 1),
                    "memory_bytes": memory,
                    "listeners": listeners,
                    "connections": connections,
                    "conditions": conditions,
                }
            )
        members.sort(key=lambda item: (item["pid"] != os.getpid(), item["pid"]))
        return {
            "pid": os.getpid(),
            "processes": process_count,
            "cpu_pct": round(cpu_total, 1),
            "memory_bytes": memory_total,
            "listeners": listener_total,
            "connections": connection_total,
            "members": members,
        }

    def _revalidate_unseen(
        self,
        seen: set[tuple[int, float]],
        conn_map: dict[int, list[Any]],
        now: float,
        startup: bool,
    ) -> None:
        if psutil is None:
            return
        process_factory = getattr(psutil, "Process", None)
        for key, item in list(self.owned.items()):
            if key in seen or item.exited_at is not None:
                continue
            if process_factory is None:
                item.exited_at = now
                item.evidence_state = "exited"
                item.evidence_reason = "process_no_longer_in_descendant_walk"
                item.exit_evidence = "not_observed"
                continue
            try:
                process = process_factory(item.pid)
                created = float(process.create_time())
            except psutil.NoSuchProcess:
                item.exited_at = now
                item.evidence_state = "exited"
                item.evidence_reason = "process_no_longer_exists"
                item.exit_evidence = "no_such_process"
                item.last_verified_at = now
                item.cpu_pct = 0
                item.listeners = []
                item.connections = []
                continue
            except (psutil.AccessDenied, OSError):
                item.evidence_state = "stale" if startup else "inaccessible"
                item.evidence_reason = (
                    "startup_fingerprint_unverifiable"
                    if startup
                    else "fingerprint_revalidation_access_denied"
                )
                item.confidence = "low"
                item.inaccessible_count += 1
                item.last_verified_at = now
                item.startup_revalidated = startup
                continue
            if abs(created - float(item.started_at or 0)) > 0.01:
                item.exited_at = now
                item.evidence_state = "stale"
                item.evidence_reason = "pid_reused_creation_time_mismatch"
                item.exit_evidence = "pid_reused"
                item.confidence = "high"
                item.last_verified_at = now
                item.startup_revalidated = startup
                continue
            try:
                with process.oneshot():
                    item.parent_pid = process.ppid()
                    item.executable = process.name()
                    current_command = " ".join(process.cmdline())[:1000]
                    item.memory_bytes = process.memory_info().rss
                if current_command:
                    item.command = current_command
                    item.command_hash = command_hash(current_command)
                item.listeners, item.connections = self._network_from(item.pid, conn_map)
            except (psutil.AccessDenied, psutil.NoSuchProcess, OSError):
                item.inaccessible_count += 1
            session = self.sessions.sessions.get(item.session_id)
            root_ended = session is None or session.record.state in {"exited", "crashed"}
            if not root_ended:
                item.root_ended_at = None
            elif item.root_ended_at is None:
                # Stamp once, on the first pass that observes the root ended. The
                # last_seen fallback is only meaningful at that instant: it still holds
                # the previous pass's value (or the previous daemon run's, after a
                # restore), because this loop refreshes it further down. Re-deriving it
                # every pass -- the original behaviour -- made ended_at track now, so the
                # grace could never elapse and a real orphan stayed "grace pending"
                # forever instead of escalating to suspected_orphan.
                item.root_ended_at = (
                    float(session.record.last_activity_ts)
                    if session is not None
                    else float(item.last_seen or item.first_seen or now)
                )
            ended_at = item.root_ended_at if item.root_ended_at is not None else now
            if root_ended and now - ended_at >= self.orphan_grace_seconds:
                item.evidence_state = "suspected_orphan"
                item.evidence_reason = "survived_root_session_grace_with_matching_fingerprint"
                item.confidence = "high" if item.command_hash else "medium"
                item.conditions = sorted(set([*item.conditions, "suspected_orphan"]))
            else:
                item.evidence_state = "escaped"
                item.evidence_reason = (
                    "root_session_ended_grace_pending"
                    if root_ended
                    else "matching_process_outside_current_descendant_walk"
                )
                item.confidence = "medium"
                item.conditions = sorted(set([*item.conditions, "escaped_job_tree"]))
            item.last_seen = now
            item.last_verified_at = now
            item.startup_revalidated = startup

    def _collect_unique_memory(self, pids: list[int]) -> dict[int, int]:
        """Unique set size per pid: memory that ending the process would really return.

        ``memory_bytes`` is RSS, which on Windows is the working set and therefore counts
        every shared page once per process mapping it -- the loader image, shared
        libraries, copy-on-write pages. Summing it across a session tree overstates the
        fleet's real footprint substantially (measured ~3.3 GiB summed RSS against ~2.0
        GiB summed USS for the same processes). USS is the honest number but costs about
        200x an RSS read because it walks each working set, so it is never sampled on the
        reconcile cadence -- only when a client opens a view that shows it.
        """
        if psutil is None:
            return {}
        result: dict[int, int] = {}
        for pid in pids:
            cached = self._handles.get(pid)
            process = cached[0] if cached is not None else None
            if process is None:
                try:
                    process = psutil.Process(pid)
                except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
                    continue
            try:
                result[pid] = int(process.memory_full_info().uss)
            except (AttributeError, psutil.NoSuchProcess, psutil.AccessDenied, OSError):
                continue
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
        # Ended sessions stay listed (the user keeps them for scrollback) with
        # record.pid never cleared. Walking that pid is the one place the
        # PID+creation-time discipline used to be skipped: Windows recycles pids
        # aggressively, so an unrelated tree would be attributed to the dead
        # session as high-confidence evidence, with terminate offered on it.
        if session.record.state in {"exited", "crashed"}:
            return []
        processes = self._tree_handles(session.record.pid, MAX_PROCESSES_PER_SESSION)
        if not processes:
            return []
        # The root's creation time was captured at spawn; a mismatch means this
        # pid now belongs to someone else. Sessions adopted from an older
        # supervisor have no reference and fall back to pid-only, as before.
        expected_start = session.record.root_started_at
        if expected_start is not None:
            try:
                actual_start = float(processes[0].create_time())
            except Exception:  # noqa: BLE001 - psutil raises provider-specific errors
                return []
            if abs(actual_start - expected_start) > _CREATE_TIME_TOLERANCE_SECONDS:
                return []
        result: list[OwnedProcess] = []
        for process in processes:
            pid = int(process.pid)
            # Only cpu_times and memory_info actually move between passes. Name and
            # command line are fixed for the life of a process, so they come from the
            # identity cache instead of a per-tick remote-PEB read.
            executable, command, identity_hash = self._identity(process, pid)
            try:
                with process.oneshot():
                    started_at = float(process.create_time())
                    cpu_times = process.cpu_times()
                    memory = int(process.memory_info().rss)
                parent_pid = self._parents.get(pid)
                if parent_pid is None:
                    parent_pid = int(process.ppid())
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
                self._forget(pid)
                continue
            conditions: list[str] = []
            if cpu >= 90:
                conditions.append("high_cpu")
            if memory >= HIGH_MEMORY_BYTES:
                conditions.append("high_memory")
            if listeners and time.time() - session.record.last_activity_ts >= NO_OUTPUT_SECONDS:
                conditions.append("no_pty_output")
            existing = self.owned.get((pid, started_at))
            observed = OwnedProcess(
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
                project_id=session.record.project_id,
                agent_run_id=session.record.agent_run_id,
                identity_id=process_identity(session.record.id, pid, started_at),
                command_hash=identity_hash,
                job_assignment=session.record.process_job_assignment,
                first_seen=existing.first_seen if existing else time.time(),
                last_seen=time.time(),
                last_verified_at=time.time(),
                startup_revalidated=existing.startup_revalidated if existing else False,
            )
            result.append(observed)
        by_pid = {item.pid: item for item in result}
        for item in result:
            lineage: list[dict[str, Any]] = []
            parent = item.parent_pid
            visited: set[int] = set()
            while parent and parent in by_pid and parent not in visited and len(lineage) < 32:
                visited.add(parent)
                ancestor = by_pid[parent]
                lineage.append({"pid": ancestor.pid, "creation_time": ancestor.started_at})
                parent = ancestor.parent_pid
            item.parent_lineage = lineage
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

    async def snapshot(
        self, session_id: str, *, force: bool = False, include_ended: bool = False
    ) -> dict[str, Any]:
        if session_id not in self.sessions.sessions and not any(
            item.session_id == session_id for item in self.owned.values()
        ):
            raise KeyError(session_id)
        if not self.available:
            session = self.sessions.sessions.get(session_id)
            record = session.record if session else None
            return {
                "available": False,
                "diagnostic": "psutil is not installed in the active environment",
                "session_id": session_id,
                "project_scope_id": record.trusted_scope_id if record else None,
                "repo_group_id": (
                    record.run_repo_group_id
                    if record and record.agent_run_id
                    else record.spawn_repo_group_id
                    if record
                    else None
                ),
                "processes": [],
            }
        await self._ensure_sampled(force=force)
        processes = [
            item.snapshot()
            for item in self.owned.values()
            if item.session_id == session_id and (include_ended or item.exited_at is None)
        ]
        processes.sort(key=lambda item: (item["exited_at"] is not None, item["pid"]))
        live_session = self.sessions.sessions.get(session_id)
        record = live_session.record if live_session else None
        evidence = next(
            (item for item in self.owned.values() if item.session_id == session_id), None
        )
        return {
            "available": True,
            "session_id": session_id,
            "project_id": record.project_id
            if record
            else evidence.project_id
            if evidence
            else None,
            "project_scope_id": record.trusted_scope_id if record else None,
            "repo_group_id": (
                record.run_repo_group_id
                if record and record.agent_run_id
                else record.spawn_repo_group_id
                if record
                else None
            ),
            "processes": processes[:MAX_PROCESSES_PER_SESSION],
        }

    async def snapshot_all(
        self, *, include_ended: bool = False, unique_memory: bool = False
    ) -> dict[str, Any]:
        """Return one coherently sampled process tree for every live mux session.

        Ended records are excluded by default. They carry no available action,
        are already absent from every total, and their durable history lives in
        `process_evidence`; including them would only grow a polled payload with
        rows the operator cannot act on.

        ``unique_memory`` adds per-process and total USS. It is opt-in because it is
        ~200x the cost of the RSS already sampled, so only user-opened views ask.
        """
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
                "daemon": self._daemon_resources,
            }
        await self._ensure_sampled()
        groups: list[dict[str, Any]] = []
        all_processes: list[dict[str, Any]] = []
        session_ids = list(self.sessions.sessions)
        session_ids.extend(
            sorted({item.session_id for item in self.owned.values()} - set(self.sessions.sessions))
        )
        for session_id in session_ids:
            session = self.sessions.sessions.get(session_id)
            processes = [
                item.snapshot()
                for item in self.owned.values()
                if item.session_id == session_id and (include_ended or item.exited_at is None)
            ]
            processes.sort(key=lambda item: (item["exited_at"] is not None, item["pid"]))
            # A session whose processes have all ended contributes nothing to act
            # on. Keep live sessions listed even while empty so the operator can
            # still see the session itself; drop dead sessions entirely. A
            # survivor (escaped/suspected_orphan) is never ended, so a session
            # holding one still appears.
            if not processes and session is None:
                continue
            all_processes.extend(processes)
            groups.append(
                {
                    "session_id": session_id,
                    "project_id": (
                        session.record.project_id
                        if session
                        else next(
                            (
                                item.project_id
                                for item in self.owned.values()
                                if item.session_id == session_id
                            ),
                            None,
                        )
                    ),
                    "project_scope_id": session.record.trusted_scope_id if session else None,
                    "repo_group_id": (
                        session.record.run_repo_group_id
                        if session and session.record.agent_run_id
                        else session.record.spawn_repo_group_id
                        if session
                        else None
                    ),
                    "processes": processes[:MAX_PROCESSES_PER_SESSION],
                }
            )
        live = [item for item in all_processes if item["exited_at"] is None]
        daemon = self._daemon_resources
        totals: dict[str, Any] = {
            "processes": len(live),
            "cpu_pct": round(sum(float(item["cpu_pct"]) for item in live), 1),
            "memory_bytes": sum(int(item["memory_bytes"]) for item in live),
            "listeners": sum(len(item["listeners"]) for item in live),
            "connections": sum(len(item["connections"]) for item in live),
        }
        if unique_memory:
            daemon_members = list(daemon.get("members") or [])
            pids = [int(item["pid"]) for item in live]
            pids.extend(int(item["pid"]) for item in daemon_members)
            unique_by_pid = await asyncio.to_thread(self._collect_unique_memory, pids)
            for item in all_processes:
                item["memory_unique_bytes"] = unique_by_pid.get(int(item["pid"]))
            daemon = dict(daemon)
            daemon["members"] = [
                {**item, "memory_unique_bytes": unique_by_pid.get(int(item["pid"]))}
                for item in daemon_members
            ]
            daemon["memory_unique_bytes"] = sum(
                unique_by_pid.get(int(item["pid"]), 0) for item in daemon_members
            )
            totals["memory_unique_bytes"] = sum(
                unique_by_pid.get(int(item["pid"]), 0) for item in live
            )
        return {
            "available": True,
            "sessions": groups,
            "daemon": daemon,
            "totals": totals,
        }

    def _owned_live(
        self, session_id: str, pid: int, identity_id: str | None = None
    ) -> tuple[Any, OwnedProcess]:
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
        if identity_id and item.identity_id != identity_id:
            raise ValueError("process fingerprint changed; refresh before acting")
        try:
            process = psutil.Process(pid)
            if abs(process.create_time() - (item.started_at or 0)) > 0.01:
                raise ValueError("process identity changed")
        except psutil.NoSuchProcess as exc:
            raise ValueError("process no longer exists") from exc
        except (psutil.AccessDenied, OSError) as exc:
            raise ValueError("process fingerprint cannot be revalidated") from exc
        return process, item

    async def act(
        self,
        session_id: str,
        pid: int,
        action: str,
        *,
        identity_id: str | None = None,
    ) -> dict[str, Any]:
        await self._ensure_sampled(force=True)
        process, _ = self._owned_live(session_id, pid, identity_id)
        session = self.sessions.sessions.get(session_id)
        if action == "interrupt":
            if session and pid == session.record.pid:
                session.pty.write(b"\x03")
            elif os.name == "nt":
                # psutil rejects SIGINT on Windows, so this action was unusable
                # for every non-root descendant on the primary platform. A console
                # child in its own group takes CTRL_BREAK; anything else has no
                # interrupt at all, and saying so beats a raw psutil ValueError.
                try:
                    await asyncio.to_thread(
                        process.send_signal, getattr(signal, "CTRL_BREAK_EVENT", signal.SIGTERM)
                    )
                except (OSError, ValueError, psutil.Error) as exc:
                    raise ValueError(
                        "this process cannot be interrupted on Windows; "
                        "use terminate instead"
                    ) from exc
            else:
                await asyncio.to_thread(process.send_signal, signal.SIGINT)
        elif action == "terminate":
            await asyncio.to_thread(process.terminate)
        elif action == "terminate_tree":
            children = await asyncio.to_thread(process.children, recursive=True)
            # Descendants of a fingerprint-verified root are part of the tree by
            # construction. Matching them against the owned set by *raw pid* let a
            # child that respawned since the last sample (a dev server restarting)
            # survive "terminate tree" while the user believed it was gone — and
            # let a recycled pid be matched on nothing but its number.
            for child in reversed(children):
                with suppress(psutil.Error, OSError):
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
        return await self.snapshot(session_id, force=True)


@dataclass(slots=True)
class PreviewRegistration:
    id: str
    session_id: str
    project_id: str
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

    @staticmethod
    def _endpoint_key(url: str) -> tuple[str, str, int]:
        parsed = urlsplit(url)
        return (
            parsed.scheme,
            parsed.hostname or "",
            parsed.port or (443 if parsed.scheme == "https" else 80),
        )

    def _existing_endpoint(
        self, project_id: str, scheme: str, host: str, port: int
    ) -> PreviewRegistration | None:
        return next(
            (
                item
                for item in self.items.values()
                if item.project_id == project_id
                and self._endpoint_key(item.url) == (scheme, host, port)
            ),
            None,
        )

    def _record_detected(
        self, session: Any, url: str, *, host: str, port: int
    ) -> PreviewRegistration:
        parsed = urlsplit(url)
        existing = self._existing_endpoint(session.record.project_id, parsed.scheme, host, port)
        if existing is not None:
            # Endpoint identity is project-wide. If it was first opened from a
            # terminal that merely printed another service's URL, retain its stable
            # Preview id but move ownership to the session that actually listens.
            existing.session_id = session.record.id
            existing.project_id = session.record.project_id
            existing.source = "detected"
            existing.project_scope_id = getattr(
                session.record,
                "trusted_scope_id",
                getattr(session.record, "project_scope_id", None),
            )
            existing.repo_group_id = getattr(session.record, "repo_group_id", None)
            self._listener_seen[existing.id] = time.time()
            return existing
        item = PreviewRegistration(
            str(uuid.uuid4()),
            session.record.id,
            session.record.project_id,
            url,
            host,
            port,
            "detected",
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

    async def ensure_detected(self, project_id: str | None = None) -> None:
        """Register live project listeners without opening workspace tabs.

        Registrations are routing identities, not layout state. Keeping every live
        project service registered lets one sandboxed Preview safely reach another
        through mux while the user still chooses which service tabs to open.
        """
        snapshot_all = getattr(self.inspector, "snapshot_all", None)
        if snapshot_all is None:
            return
        snapshot = await snapshot_all()
        for group in snapshot.get("sessions", []):
            if project_id is not None and group.get("project_id") != project_id:
                continue
            session = self.sessions.sessions.get(str(group.get("session_id") or ""))
            if session is None:
                continue
            endpoints: dict[tuple[str, int], tuple[str, str]] = {}
            for process in group.get("processes", []):
                for listener in process.get("listeners", []):
                    if listener.get("loopback") is not True:
                        continue
                    host = str(listener.get("host") or "")
                    port = int(listener.get("port") or 0)
                    url = str(listener.get("url") or "")
                    if host not in PREVIEW_LOOPBACK_HOSTS or not 1 <= port <= 65535 or not url:
                        continue
                    scheme = urlsplit(url).scheme
                    key = (scheme, port)
                    current = endpoints.get(key)
                    if current is None or (host == "127.0.0.1" and current[0] != host):
                        endpoints[key] = (host, url)
            for (_, port), (host, url) in endpoints.items():
                self._record_detected(session, url, host=host, port=port)

    def routes_for_project(self, project_id: str) -> dict[str, str]:
        routes: dict[str, str] = {}
        for item in self.items.values():
            if item.project_id != project_id:
                continue
            parsed = urlsplit(item.url)
            host = parsed.hostname or ""
            displayed_host = f"[{host}]" if ":" in host else host
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
            default_port = (parsed.scheme == "http" and port == 80) or (
                parsed.scheme == "https" and port == 443
            )
            origin = f"{parsed.scheme}://{displayed_host}"
            if not default_port:
                origin += f":{port}"
            routes[origin] = f"/preview/{item.id}/"
        return routes

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
        # Force one coherent process sample, then attribute this endpoint across the
        # whole Project. A frontend terminal often prints its backend URL; the URL
        # still belongs to the backend session, not whichever terminal was clicked.
        owner = None
        project_sessions = [
            candidate
            for candidate in self.sessions.sessions.values()
            if candidate.record.project_id == session.record.project_id
        ]
        project_sessions.sort(key=lambda candidate: candidate.record.id != session.record.id)
        for index, candidate in enumerate(project_sessions):
            snapshot = await self.inspector.snapshot(candidate.record.id, force=index == 0)
            if any(
                listener["port"] == port
                and listener.get("host") == host
                and listener.get("loopback") is True
                for process in snapshot["processes"]
                for listener in process["listeners"]
            ):
                owner = candidate
                break
        if owner is None and not approved:
            raise ValueError("preview listener is not owned by this session; approval is required")
        if owner is not None:
            return self._record_detected(owner, normalized_url, host=host, port=port)
        existing = self._existing_endpoint(session.record.project_id, parsed.scheme, host, port)
        if existing:
            return existing
        item = PreviewRegistration(
            str(uuid.uuid4()),
            session.record.id,
            session.record.project_id,
            normalized_url,
            host,
            port,
            "user-approved",
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
        await self.ensure_detected()
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
