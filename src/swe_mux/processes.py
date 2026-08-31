from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import signal
import sys
import time
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from aiohttp import ClientError, ClientSession, ClientTimeout

from .background_tasks import background
from .errors import NotFound
from .event_bus import EventBus
from .harness import is_agent_harness
from .operational_telemetry import command_hash, process_identity
from .preview_store import PreviewStore
from .session import Session, SessionManager, clear_standing_activity

try:
    import psutil
except ImportError:  # pragma: no cover - diagnostics cover an unsynchronized dev venv
    psutil = None

log = logging.getLogger(__name__)

PROCESS_INSPECTOR_LOOP = "process-inspector"
PROCESS_ATTRIBUTION_VERSION = 2
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
PREVIEW_PROBE_INITIAL_RETRY_SECONDS = 5.0
PREVIEW_PROBE_MAX_RETRY_SECONDS = 300.0
PREVIEW_PROBE_CONCURRENCY = 8
PREVIEW_PROBE_PREFIX_BYTES = 4096
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
# `swe-mux.exe -m swe_mux.<module>` is a helper an agent session spawned inside its
# own tree -- hook_client runs on every PreToolUse/PostToolUse. It shares the app's
# image with the shell and the daemon and must stay session-owned, so the image test
# below excludes it. `packaging/redeploy_desktop.py` draws exactly the same line
# before killing anything, for the same reason.
HELPER_MODULE_FLAG = "-m"
HELPER_MODULE_PREFIX = "swe_mux."
# How far up the parent chain an infrastructure ancestor is looked for. The real
# depth is one (shell -> daemon); the bound is here so a corrupt parent map cannot
# make this walk unbounded.
MAX_INFRASTRUCTURE_ANCESTORS = 8
# How often the machine is scanned for processes running our own image. Attribution
# never waits on this -- a claim is tested directly, on the pid being claimed -- so
# the scan exists only to *enumerate* swe-mux for the runtime footer, where a shell
# the daemon has no live link to would otherwise be reported as nothing at all.
# Constructing a psutil.Process is the most expensive operation in this file, so a
# whole-machine pass is priced accordingly: once a minute against a long-lived shell,
# not once per five-second tick.
OWN_IMAGE_SCAN_SECONDS = 60.0
PREVIEW_LOOPBACK_HOSTS = {"127.0.0.1", "::1"}
# A wildcard bind means "every local interface", which necessarily includes loopback:
# a server on 0.0.0.0:5173 really is reachable at 127.0.0.1:5173. Most dev servers
# bind the wildcard by default, so report those listeners at their loopback address.
# This deliberately does not relax PREVIEW_LOOPBACK_HOSTS above: the address the
# bridge dials stays a literal loopback one. A bind to a single non-loopback address
# is left alone, because it genuinely is not reachable on loopback.
WILDCARD_LOOPBACK_HOSTS = {"0.0.0.0": "127.0.0.1", "::": "::1", "::ffff:0.0.0.0": "127.0.0.1"}
LISTENER_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}


def own_executable() -> str | None:
    """The executable file identifying swe-mux itself, when there is one.

    Only the frozen desktop app has one. Its shell, its daemon and its
    `--daemon-child` successors are all the *same file* on disk, which makes an
    exact path match a statement about identity rather than about a name: nothing
    else on the machine is running `dist/swe-mux/swe-mux.exe`.

    Running from source there is deliberately no such file. `sys.executable` is
    then the venv's `python.exe`, which every agent-spawned Python on the machine
    also runs, and reserving on that would strip real session processes out of the
    fleet. A dev daemon therefore keeps to the descendant walk alone.
    """
    if not bool(getattr(sys, "frozen", False)):
        return None
    try:
        return str(Path(sys.executable).resolve())
    except (OSError, ValueError):  # pragma: no cover - resolve() on a live exe
        return None


def is_session_helper_command(command: str) -> bool:
    """Whether `command` is the app image re-invoked as a session-owned helper."""
    parts = command.split()
    return any(
        flag == HELPER_MODULE_FLAG and module.startswith(HELPER_MODULE_PREFIX)
        for flag, module in zip(parts, parts[1:], strict=False)
    )


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
    # Stable ownership provenance. Evidence state/reason may change as a process
    # leaves the current walk; these fields preserve how ownership was last
    # established instead of overwriting the forensic answer with "escaped".
    attribution_version: int = PROCESS_ATTRIBUTION_VERSION
    attribution_source: str = "parent_walk"
    last_attributed_at: float | None = None
    last_job_confirmed_at: float | None = None
    # When the owning session was first observed to have ended. Stamped once so the
    # orphan grace measures from that moment; deriving it from last_seen could never
    # elapse, because last_seen is refreshed on every pass.
    root_ended_at: float | None = None

    def snapshot(self) -> dict[str, Any]:
        result = asdict(self)
        result["server_eligible"] = self.server_eligible()
        return result

    def server_eligible(self) -> bool:
        """Whether listeners may be presented as servers owned by this session."""
        return (
            self.exited_at is None
            and self.attribution_version >= PROCESS_ATTRIBUTION_VERSION
            and self.attribution_source in {"session_root", "parent_walk", "job_membership"}
            and self.evidence_state
            in {"active", "escaped", "suspected_orphan"}
        )


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
        self._system_cpu_times: tuple[float, float] | None = None
        self._system_cpu_pct: float | None = None
        self._sample_lock = asyncio.Lock()
        self._last_collect = 0.0
        self._last_persist = 0.0
        # Live psutil.Process handles reused across passes, keyed by pid.
        self._handles: dict[int, tuple[Any, int]] = {}
        # (name, command, command_hash) per pid. A live process never renames itself and
        # its command line is fixed at exec, so this is read once per handle rather than
        # per pass; cmdline() is a remote-PEB read and was ~40% of the per-process cost.
        self._static: dict[int, tuple[str, str, str]] = {}
        # The executable this daemon is running, when that identifies swe-mux itself,
        # and the per-pid answer to "is this that same file, run as infrastructure".
        # Reading exe() is a remote handle open, so it is cached like the rest of a
        # process's fixed identity: a live pid never changes its image.
        self._own_executable = own_executable()
        self._own_image: dict[int, bool] = {}
        self._own_image_pids: set[int] = set()
        self._own_image_scanned_at = 0.0
        # pid -> child pids, rebuilt once per pass from a single system-wide parent map.
        self._children_by_pid: dict[int, list[int]] = {}
        # pid -> parent pid from that same map, used to detect pid reuse for free.
        self._parents: dict[int, int] = {}
        # session_id -> pids in that session's Win32 job, refreshed before each
        # collection. Reaches detached descendants the parent walk cannot; see
        # _tree_handles and SessionManager.job_process_ids.
        self._job_pids: dict[str, list[int]] = {}
        # Bounded, deduplicated diagnostics for ownership decisions. These are
        # intentionally command-free and also go to the durable rolling daemon log.
        self._ownership_diagnostics: list[dict[str, Any]] = []
        self._ownership_diagnostic_keys: set[tuple[Any, ...]] = set()
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
                attribution_version=int(item.get("attribution_version") or 1),
                attribution_source=str(item.get("attribution_source") or "legacy"),
                last_attributed_at=item.get("last_attributed_at"),
                last_job_confirmed_at=item.get("last_job_confirmed_at"),
            )
            key = (process.pid, started)
            existing = self.owned.get(key)
            if existing is None or float(process.last_seen or 0) > float(existing.last_seen or 0):
                self.owned[key] = process
        if self.available:
            await self.reconcile(startup=True)

    @property
    def available(self) -> bool:
        return psutil is not None

    def _diagnose_once(
        self,
        kind: str,
        key: tuple[Any, ...],
        *,
        level: int = logging.WARNING,
        **detail: Any,
    ) -> None:
        diagnostic_key = (kind, *key)
        if diagnostic_key in self._ownership_diagnostic_keys:
            return
        if len(self._ownership_diagnostic_keys) >= 1024:
            self._ownership_diagnostic_keys.clear()
        self._ownership_diagnostic_keys.add(diagnostic_key)
        entry = {"ts": time.time(), "kind": kind, **detail}
        self._ownership_diagnostics.append(entry)
        self._ownership_diagnostics = self._ownership_diagnostics[-100:]
        fields = " ".join(f"{name}={value}" for name, value in detail.items())
        log.log(level, "process ownership diagnostic kind=%s %s", kind, fields)

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

    async def _refresh_job_pids(self) -> None:
        """Re-read per-session job membership before a collection pass.

        Kept off the sampling thread because the supervisor-owned half is an
        RPC. One request per pass covers every session, so this does not scale
        with fleet size; a failure leaves the previous map in place rather than
        blanking attribution on a single dropped response.
        """
        source = getattr(self.sessions, "job_process_ids", None)
        if source is None:
            return
        try:
            self._job_pids = await source()
        except Exception:  # noqa: BLE001 - attribution must never break sampling
            log.debug("job pid refresh failed; keeping previous map", exc_info=True)

    async def reconcile(self, *, startup: bool = False) -> None:
        if psutil is None:
            return
        await self._refresh_job_pids()
        async with self._sample_lock:
            snapshots = await asyncio.to_thread(self._collect_all, startup)
        now = time.time()
        # Escaped processes with current provenance remain owned. They are absent
        # from the downward walk by definition, but their live listener must agree
        # across the API, event stream, and Preview lifetime logic.
        snapshots = [
            item
            for item in self.owned.values()
            if item.exited_at is None
            and item.attribution_version >= PROCESS_ATTRIBUTION_VERSION
        ]
        previous_listeners = self._listeners
        current_listeners = {
            (process.session_id, int(listener["port"]), str(listener["host"]))
            for process in snapshots
            if process.server_eligible()
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

    @staticmethod
    def _could_be_background_task(item: Any, root_pid: int | None, threshold: float) -> bool:
        """Whether one live descendant could be the task the annotation names.

        Two exclusions, both structural rather than name-matched:

        - The CLI root itself, which is the process the descendants hang off.
        - Anything that was **already running when the annotation opened**. A
          background task's process starts when the launch that opened the
          annotation runs, so a descendant older than the annotation cannot be
          it - that is what separates a task from the CLI's own long-lived
          children (language servers, stdio MCP servers, console hosts) without
          matching on their names, which would drift with every CLI release.

        An unreadable start time counts as task-capable. Every uncertainty here
        has to fall that way: refusing to clear leaves the TTL in charge, while a
        wrong clear retracts a true "an agent is still working" the user is
        relying on.
        """
        if root_pid is not None and getattr(item, "pid", None) == root_pid:
            return False
        started = getattr(item, "started_at", None)
        if not isinstance(started, int | float):
            return True
        return float(started) >= threshold

    def _fast_clear_background_annotations(self, snapshots: list[Any], now: float) -> None:
        """A vanished process cannot still be working — the strongest *clear*.

        A `background_tasks` annotation whose session has no live descendant that
        *could be that task* is cleared immediately instead of waiting out its
        30-minute TTL. Never the reverse: descendants alone open nothing (an MCP
        server child is not a background task).

        The candidate test used to be "the session has exactly one descendant,
        the CLI root". That is the right intent and an unreachable gate: a Claude
        session that has opened a file holds a language server, and one with a
        stdio MCP server holds that too, so real sessions carry 4-10 permanent
        children and the count was never 1. Measured on the live fleet
        2026-08-06, the one positive clear that does not depend on the transcript
        had therefore never fired on any session that could run a background task
        at all. `_could_be_background_task` replaces the count with a per-process
        question that those helpers answer "no" to by construction.
        """
        live: dict[str, list[Any]] = {}
        for item in snapshots:
            live.setdefault(item.session_id, []).append(item)
        for session in self.sessions.sessions.values():
            # getattr-guarded like the rest of the inspector: tests drive it
            # with lightweight record stand-ins.
            record = session.record
            if not is_agent_harness(getattr(record, "backend", None)):
                continue
            if getattr(record, "state", None) in {"exited", "crashed"}:
                continue
            standing = getattr(record, "standing_activity", None) or []
            annotation = next((a for a in standing if a.kind == "background_tasks"), None)
            if annotation is None:
                continue
            if now - annotation.since < self.BACKGROUND_FAST_CLEAR_MIN_AGE_SECONDS:
                continue
            descendants = live.get(record.id) or []
            if not descendants:
                # The root itself is gone; session exit owns that transition and
                # clears the whole annotation set with it.
                continue
            root_pid = getattr(record, "pid", None)
            # The same grace absorbs the launch/observation race in the other
            # direction: a task process can be a moment older than the record
            # that opened the annotation.
            threshold = annotation.since - self.BACKGROUND_FAST_CLEAR_MIN_AGE_SECONDS
            if any(
                self._could_be_background_task(item, root_pid, threshold)
                for item in descendants
            ):
                continue
            if clear_standing_activity(
                session, "background_tasks", evidence="process:no_task_descendants", now=now
            ):
                observation_state = getattr(session, "observation_state", None)
                if isinstance(observation_state, dict):
                    # The launch bookkeeping described the annotation just
                    # cleared. `background_closed` deliberately survives: it is
                    # what keeps a duplicate completion from decrementing a
                    # later annotation that has nothing to do with it.
                    observation_state.get("background_open", {}).clear()
                    observation_state.get("background_labels", {}).clear()
                session.publish_update()

    async def _ensure_sampled(self, *, force: bool = False) -> None:
        """Collect a fresh sample only when the cached one is stale.

        The background loop already refreshes every ``cadence`` seconds, so HTTP read
        paths (session snapshot, fleet view, preview candidates) can reuse that sample
        instead of forcing a full descendant re-walk plus socket enumeration on every
        poll. ``force`` is used by ownership-sensitive actions that require live state.
        """
        if force or time.monotonic() - self._last_collect >= self.cadence:
            await self._refresh_job_pids()
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

        **``_parents`` is the only permitted source of a parent pid in a sampling
        pass.** ``Process.ppid()`` on Windows is ``ppid_map()[pid]``: it rebuilds this
        exact table, for every call, and unlike name/cmdline/memory it is not memoized
        by ``oneshot()``. One unguarded call site was enough to make sampling
        O(processes²) — measured 2026-08-05 with py-spy against the live daemon, it was
        **45.2% of all samples** while ``process-inspector`` ticked only every ~6.5 s,
        which is also why iteration-count instrumentation never showed it. Read this
        map; fall back to ``ppid()`` only for a pid younger than the last refresh.
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

    def _tree_handles(
        self, root_pid: int, limit: int, *, stop_pids: set[int] | None = None
    ) -> list[Any]:
        """Cached psutil handles for ``root_pid`` and its real descendants, root first.

        ``stop_pids`` are included but never traversed. The PTY supervisor is the one
        that matters: it parents *every* live session, so a walk that descends through
        it absorbs the whole fleet. The docstring below records that happening once
        through a recycled parent link; it can also happen through a real one, because
        a freshly spawned supervisor genuinely is a child of the daemon until the next
        daemon restart leaves it parentless. Reserving the supervisor as swe-mux's own
        is right; reserving everything it hosts is not.

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
        stop = stop_pids or set()
        if not self._children_by_pid:
            try:
                walked = [root, *root.children(recursive=True)]
            except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
                return [root]
            return self._prune_stopped(walked, root_pid, stop)[:limit]
        try:
            root_started = float(root.create_time())
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            return [root]
        handles: list[Any] = [root]
        seen: set[int] = {root_pid}
        # Carry the actual parent's creation time down every edge. Comparing only
        # with the root misses the common Windows failure where a long-lived child
        # retains a dead parent pid and that pid is later recycled by a newer,
        # unrelated descendant of the session.
        stack: list[tuple[int, int, float]] = (
            []
            if root_pid in stop
            else [
                (pid, root_pid, root_started)
                for pid in self._children_by_pid.get(root_pid, ())
            ]
        )
        while stack and len(handles) < limit:
            pid, parent_pid, parent_started = stack.pop()
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
            if started + _CREATE_TIME_TOLERANCE_SECONDS < parent_started:
                self._diagnose_once(
                    "causally_impossible_parent_edge",
                    (root_pid, parent_pid, pid, parent_started, started),
                    root_pid=root_pid,
                    parent_pid=parent_pid,
                    child_pid=pid,
                    parent_started_at=parent_started,
                    child_started_at=started,
                )
                continue
            handles.append(handle)
            if pid in stop:
                continue
            stack.extend(
                (child_pid, pid, started)
                for child_pid in self._children_by_pid.get(pid, ())
            )
        return handles

    @staticmethod
    def _prune_stopped(walked: list[Any], root_pid: int, stop: set[int]) -> list[Any]:
        """Drop everything strictly below a ``stop`` pid from a psutil-walked subtree.

        Only reached when ``psutil._ppid_map`` has been withdrawn and the walk fell
        back to ``children(recursive=True)``, which has no boundary of its own. The
        subtree is already in hand, so its own parent links are enough to rebuild it;
        no system-wide snapshot is taken.
        """
        if not stop:
            return walked
        parents: dict[int, int] = {}
        for handle in walked:
            try:
                parents[int(handle.pid)] = int(handle.ppid())
            except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
                continue
        kept: list[Any] = []
        for handle in walked:
            pid = int(handle.pid)
            cursor = parents.get(pid)
            below = False
            # Bounded by the subtree's own size; a cycle cannot outlast it.
            for _ in range(len(walked)):
                if cursor is None or cursor == root_pid:
                    break
                if cursor in stop:
                    below = True
                    break
                cursor = parents.get(cursor)
            if not below:
                kept.append(handle)
        return kept

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
        self._own_image.pop(pid, None)

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

    def _supervisor_pid(self) -> int | None:
        """The PTY supervisor this daemon is driving, when there is one.

        The supervisor is swe-mux's own process but is not reachable from the daemon:
        it is spawned to break away and it survives daemon restarts, so within a
        restart or two it is parentless. The daemon knows it by pid regardless.
        """
        client = getattr(self.sessions, "supervisor", None)
        pid = getattr(client, "supervisor_pid", None)
        if not isinstance(pid, int) or pid <= 0:
            return None
        return pid

    def _is_own_image(self, pid: int) -> bool:
        """Whether `pid` is running swe-mux's own executable as infrastructure.

        This is the test an ancestor walk cannot replace. A `reload-daemon` spawns
        the successor daemon as a child of the *outgoing* one, whose pid is dead
        within the second, so from the second restart onward there is no live chain
        upward to the desktop shell at all - and the shell is precisely the process
        that a redeploy run from inside a session leaves attributed to that session.
        Identity survives that; lineage does not.
        """
        if self._own_executable is None or psutil is None:
            return False
        cached = self._own_image.get(pid)
        if cached is not None:
            return cached
        handle = self._handle(pid)
        answer = False
        if handle is not None:
            try:
                image = str(Path(handle.exe()).resolve())
            except (
                AttributeError,
                psutil.NoSuchProcess,
                psutil.AccessDenied,
                OSError,
                ValueError,
            ):
                # An unreadable image is not ours as far as anything here can tell.
                # That is the safe direction for attribution and the unsafe one for
                # the action gate, which is why the gate is not the only guard: a
                # process swe-mux cannot even open is one it cannot signal either.
                image = ""
            if image and image == self._own_executable:
                _, command, _ = self._identity(handle, pid)
                answer = not is_session_helper_command(command)
        self._own_image[pid] = answer
        return answer

    def _scan_own_image_pids(self) -> set[int]:
        """Every live pid running our own executable, refreshed on a slow cadence.

        Only the enumeration needs this. Reservation asks `_is_own_image` about the
        pid in front of it and is never delayed by the cadence; what the scan adds is
        the ability to *find* a desktop shell nothing points at, so the runtime footer
        can report it. `process_iter` is filtered on the image's file name first,
        because that is the cheap half of the identity and the exact-path check that
        decides the answer then runs on a handful of processes rather than on every
        one the machine has.
        """
        if self._own_executable is None or psutil is None:
            return set()
        now = time.monotonic()
        # A pid that has left the system parent table is gone, and waiting out the
        # cadence would keep enumerating it - or, once Windows recycles it, something
        # else entirely. Losing one is therefore the signal to look again now.
        fresh = self._own_image_pids and (
            not self._parents or self._own_image_pids <= self._parents.keys()
        )
        if fresh and now - self._own_image_scanned_at < OWN_IMAGE_SCAN_SECONDS:
            return self._own_image_pids
        iterator = getattr(psutil, "process_iter", None)
        if iterator is None:
            return self._own_image_pids
        wanted = Path(self._own_executable).name.casefold()
        found: set[int] = set()
        try:
            for process in iterator(["pid", "name"]):
                try:
                    name = str((process.info or {}).get("name") or "")
                except (psutil.NoSuchProcess, psutil.AccessDenied, OSError, AttributeError):
                    continue
                if name.casefold() != wanted:
                    continue
                pid = int(process.pid)
                if self._is_own_image(pid):
                    found.add(pid)
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            return self._own_image_pids
        self._own_image_pids = found
        self._own_image_scanned_at = now
        return found

    def _infrastructure_handles(self) -> list[Any]:
        """Handles for every process that is swe-mux itself, this daemon included.

        Three sources, because no one of them sees all of it. The descendant walk
        finds what this daemon started. The ancestor walk finds the desktop shell
        that started this daemon, while that link is still live. The slow image scan
        finds the shell after a `reload-daemon` has broken that link, and brings its
        WebView2 host with it.

        Enumeration and reservation are deliberately separate. A claim is answered by
        `_is_own_image` on the pid being claimed, immediately and regardless of when
        this list was last built; the list only decides what the runtime footer can
        see and report.

        The supervisor is added by pid and made a traversal boundary in the same
        breath: it is ours, everything it hosts is the fleet's.
        """
        if psutil is None:
            return []
        # The action gate calls this outside a sampling pass, where the map may be
        # from the previous tick or absent entirely. Refreshing only when it is empty
        # keeps the per-tick path at one snapshot and still lets the gate stand on its
        # own: it must not be correct only because something else ran first.
        if not self._children_by_pid:
            self._refresh_tree()
        supervisor = self._supervisor_pid()
        stop = {supervisor} if supervisor is not None else set()
        handles: list[Any] = []
        seen: set[int] = set()

        def add_tree(root_pid: int) -> None:
            for handle in self._tree_handles(root_pid, MAX_PROCESSES_PER_SESSION, stop_pids=stop):
                pid = int(handle.pid)
                if pid in seen:
                    continue
                seen.add(pid)
                handles.append(handle)

        add_tree(os.getpid())
        # Deliberately no creation-time check on these edges, unlike every downward
        # one. There, a recycled parent pid splices in a process that is not ours; here
        # the edge is only ever followed to a pid the image test has already declared
        # ours, and a recycled pid running our own executable is swe-mux whether or not
        # it is really this daemon's parent. The check could not change an answer.
        cursor = self._parents.get(os.getpid())
        for _ in range(MAX_INFRASTRUCTURE_ANCESTORS):
            if cursor is None or cursor in seen or not self._is_own_image(cursor):
                break
            add_tree(cursor)
            cursor = self._parents.get(cursor)
        for pid in sorted(self._scan_own_image_pids()):
            if pid not in seen:
                add_tree(pid)
        if supervisor is not None and supervisor not in seen:
            handle = self._handle(supervisor)
            if handle is not None:
                seen.add(supervisor)
                handles.append(handle)
        return handles

    def _fingerprints(self, handles: list[Any]) -> set[tuple[int, float]]:
        result: set[tuple[int, float]] = set()
        for process in handles:
            try:
                result.add((int(process.pid), float(process.create_time())))
            except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
                continue
        return result

    def _is_infrastructure(self, key: tuple[int, float], base: set[tuple[int, float]]) -> bool:
        """Whether a fingerprint is swe-mux's own and must never be session-owned.

        `base` is the enumerated topology; the image test then answers for a pid the
        enumeration has not reached. Keeping the second half as a predicate rather
        than folding it into `base` is what makes it cheap: it is asked only about
        pids some session is actually claiming, never about the machine.
        """
        return key in base or self._is_own_image(key[0])

    def _has_live_sessions(self) -> bool:
        """Whether any session could own a process this pass.

        `getattr`-guarded like the rest of the inspector, which tests drive with
        lightweight record stand-ins rather than real sessions.
        """
        for session in self.sessions.sessions.values():
            if getattr(session.record, "state", None) not in {"exited", "crashed"}:
                return True
        return False

    def _sample_system_cpu(self) -> None:
        """Update normalized whole-machine CPU usage from cumulative OS counters."""
        cpu_times = getattr(psutil, "cpu_times", None)
        if cpu_times is None:
            return
        try:
            sample = cpu_times()
            total = sum(float(value) for value in sample)
            # Linux includes guest time in user/nice, so counting the guest fields
            # again inflates the denominator. I/O wait is idle capacity, matching
            # psutil.cpu_percent's cross-platform definition of system utilization.
            total -= float(getattr(sample, "guest", 0.0))
            total -= float(getattr(sample, "guest_nice", 0.0))
            busy = total - float(getattr(sample, "idle", 0.0))
            busy -= float(getattr(sample, "iowait", 0.0))
        except (OSError, TypeError, ValueError):
            log.debug("system CPU sample unavailable", exc_info=True)
            return
        previous = self._system_cpu_times
        self._system_cpu_times = (total, busy)
        if previous is None:
            return
        total_delta = total - previous[0]
        if total_delta <= 0:
            return
        busy_delta = max(0.0, busy - previous[1])
        self._system_cpu_pct = round(min(100.0, busy_delta / total_delta * 100), 1)

    def _collect_all(self, startup: bool = False) -> list[OwnedProcess]:
        self._sample_system_cpu()
        # An empty fleet is not a cheap pass, it is a pass with nothing to answer. The
        # two most expensive things here run before any session is consulted: the whole
        # OS socket table (`_connections_by_pid`) and the system-wide parent map
        # (`_refresh_tree`). With no live sessions to attribute a process to and no
        # retained rows to retire, every one of those bytes is spent deciding nothing.
        # Measured on an idle daemon: 166 passes, ~20ms each, and `_refresh_tree` was
        # 40.8% of the profile of a daemon holding 0.7% of a core, which is to say most
        # of what it was doing at all.
        #
        # `startup` still runs: the first pass establishes the daemon's own
        # infrastructure fingerprints, which later passes exclude from session
        # attribution. Retained rows still run, because a process outliving its session
        # has to be seen exiting.
        if not startup and not self.owned and not self._has_live_sessions():
            return []
        result: list[OwnedProcess] = []
        session_seen: set[tuple[int, float]] = set()
        daemon_seen: set[tuple[int, float]] = set()
        # Enumerate the whole OS socket table once and bucket by owning PID. Calling
        # net_connections() per process would rescan the entire table for every one of
        # potentially hundreds of descendants each tick.
        conn_map = self._connections_by_pid()
        self._refresh_tree()
        daemon_candidates = (
            self._infrastructure_handles()
            if psutil is not None and hasattr(psutil, "Process")
            else []
        )
        infrastructure = self._fingerprints(daemon_candidates)
        now = time.time()
        # Iterating the retained rows rather than the topology is what lets the image
        # test reach a row the enumeration cannot: a desktop shell a redeploy left
        # attributed to a since-ended session is no longer produced by any walk, only
        # revalidated, so a topology-only sweep never revisits it.
        for key in list(self.owned):
            existing = self.owned.get(key)
            if existing is None or existing.exited_at is not None:
                continue
            if not self._is_infrastructure(key, infrastructure):
                continue
            self._diagnose_once(
                "persisted_session_claimed_infrastructure",
                (*key, existing.session_id),
                pid=key[0],
                creation_time=key[1],
                session_id=existing.session_id,
            )
            self._invalidate_attribution(key, now, "reserved_infrastructure_fingerprint")

        claims: dict[tuple[int, float], list[OwnedProcess]] = {}
        for session in self.sessions.sessions.values():
            for item in self._collect_session(session, conn_map):
                key = (item.pid, item.started_at or 0.0)
                claims.setdefault(key, []).append(item)

        source_rank = {"session_root": 3, "job_membership": 2, "parent_walk": 1}
        for key, candidates in claims.items():
            if self._is_infrastructure(key, infrastructure):
                self._diagnose_once(
                    "session_claimed_infrastructure",
                    key,
                    pid=key[0],
                    creation_time=key[1],
                    session_ids=sorted(item.session_id for item in candidates),
                )
                self._invalidate_attribution(key, now, "reserved_infrastructure_fingerprint")
                continue
            highest = max(source_rank.get(item.attribution_source, 0) for item in candidates)
            winners = [
                item
                for item in candidates
                if source_rank.get(item.attribution_source, 0) == highest
            ]
            if len(winners) != 1:
                self._diagnose_once(
                    "ambiguous_session_ownership",
                    (*key, *(sorted(item.session_id for item in candidates))),
                    pid=key[0],
                    creation_time=key[1],
                    session_ids=sorted(item.session_id for item in candidates),
                    sources=sorted(item.attribution_source for item in candidates),
                )
                self._invalidate_attribution(key, now, "ambiguous_session_ownership")
                continue
            winner = winners[0]
            if len(candidates) > 1:
                self._diagnose_once(
                    "session_ownership_conflict_resolved",
                    (*key, winner.session_id),
                    level=logging.INFO,
                    pid=key[0],
                    creation_time=key[1],
                    owner_session_id=winner.session_id,
                    rejected_session_ids=sorted(
                        item.session_id for item in candidates if item is not winner
                    ),
                    source=winner.attribution_source,
                )
            session_seen.add(key)
            self.owned[key] = winner
            result.append(winner)
        attributed_pids = {item.pid for item in result}
        self._daemon_resources = self._collect_daemon_resources(
            attributed_pids, daemon_seen, conn_map, candidates=daemon_candidates
        )
        self._revalidate_unseen(session_seen, conn_map, now, startup)
        sampled = session_seen | daemon_seen
        self._cpu_samples = {
            key: sample for key, sample in self._cpu_samples.items() if key in sampled
        }
        # A handle for a pid that left the tree is dead weight and, worse, would still
        # answer with its memoized identity if that pid were later recycled. Dropping it
        # here means every pid re-enters through _handle's fresh construction.
        live_pids = {pid for pid, _ in sampled}
        self._handles = {pid: entry for pid, entry in self._handles.items() if pid in live_pids}
        self._static = {pid: entry for pid, entry in self._static.items() if pid in live_pids}
        self._own_image = {
            pid: answer for pid, answer in self._own_image.items() if pid in live_pids
        }
        self._last_collect = time.monotonic()
        return result

    def _collect_daemon_resources(
        self,
        attributed_pids: set[int],
        seen: set[tuple[int, float]],
        conn_map: dict[int, list[Any]] | None = None,
        *,
        candidates: list[Any] | None = None,
    ) -> dict[str, Any]:
        """Sample daemon/infrastructure processes not already owned by a session.

        Session descendants are children of the daemon too. Excluding their PIDs keeps
        the resource footer additive without counting the same process twice. Detailed
        members let the fleet inspector explain that aggregate without granting session
        process controls over swe-mux itself.

        "Infrastructure" here is the whole of swe-mux, not the daemon's descendants:
        the desktop shell and its WebView2 host, and the supervisor, are ours and were
        previously reported as nothing at all. The footer read `processes: 1` on a
        frozen app whose shell alone held 69 MiB - an undercount of its own footprint,
        from the same descendant-only definition that let a session claim the shell.
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
        candidates = candidates or self._infrastructure_handles()
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

    def _invalidate_attribution(
        self, key: tuple[int, float], now: float, reason: str
    ) -> None:
        """Retire a live process claim without asserting that the OS process exited."""
        item = self.owned.get(key)
        if item is None or item.exited_at is not None:
            return
        item.exited_at = now
        item.evidence_state = "stale"
        item.evidence_reason = reason
        item.exit_evidence = "ownership_rejected"
        item.confidence = "high"
        item.last_verified_at = now
        item.listeners = []
        item.connections = []
        item.conditions = sorted(set([*item.conditions, "ownership_rejected"]))

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
            # Version 1 trusted only root-relative time. It could therefore retain
            # an unrelated listener forever after a recycled intermediate pid
            # spliced that process into one sample. Current tree/job evidence has
            # already replaced every legitimate live row with version 2; anything
            # still unseen here is uncorroborated legacy ownership.
            if item.attribution_version < PROCESS_ATTRIBUTION_VERSION:
                self._diagnose_once(
                    "legacy_attribution_retired",
                    (item.session_id, item.pid, item.started_at),
                    level=logging.INFO,
                    session_id=item.session_id,
                    pid=item.pid,
                    creation_time=item.started_at,
                    prior_state=item.evidence_state,
                )
                self._invalidate_attribution(key, now, "legacy_attribution_uncorroborated")
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
                    # `_parents` first, and `oneshot()` is not the reason. On Windows
                    # `Process.ppid()` is `ppid_map()[pid]` — a *whole system* parent-table
                    # snapshot per call — and it carries no `@memoize_when_activated`, so
                    # `oneshot()` does not cache it the way it caches name/cmdline/memory.
                    # Calling it per tracked process made each pass O(processes²) against a
                    # map `_refresh_tree` already built once for this pass. The fallback is
                    # for a pid that appeared after that refresh, where one snapshot is the
                    # right price rather than the default one.
                    item.parent_pid = self._parents.get(item.pid)
                    if item.parent_pid is None:
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

    def _job_handles(self, session_id: str, walked: list[Any]) -> list[Any]:
        """Handles for job members the parent walk could not reach.

        The walk can only descend through *live* parents. Windows neither
        re-parents an orphan nor clears the dead pid from its ppid field, so a
        descendant whose intermediate parent has exited is permanently
        unreachable from the root -- and that is the normal outcome for anything
        an agent starts detached, which is the only way Codex's one-shot shell
        tool can leave a server running. The listener is real, owned, and dies
        with the session; only the *evidence path* was missing.

        Job membership is not a weaker substitute for the creation-time-guarded
        walk, it is a stronger claim: a process is in a job only by having been
        spawned inside it, and Windows removes a pid from the list the instant
        it exits, so a recycled pid cannot appear here by coincidence. That is
        why these handles are not re-filtered against the root's creation time
        the way mapped children are -- there is no stale-link failure mode to
        guard against.

        Children of a job member are themselves job members, so nothing needs
        traversing; the list is already transitive.
        """
        pids = self._job_pids.get(session_id)
        if not pids:
            return []
        known = {int(handle.pid) for handle in walked}
        extra: list[Any] = []
        for pid in pids:
            if pid in known:
                continue
            known.add(pid)
            handle = self._handle(pid)
            if handle is None:
                continue
            extra.append(handle)
        return extra

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
        # Only now that the root is fingerprint-verified is job membership safe to
        # trust: the job handle is keyed to this session, so if the root turned out
        # to be a recycled pid its job would describe someone else's tree.
        job_only = self._job_handles(session.record.id, processes)
        processes = [*processes, *job_only][:MAX_PROCESSES_PER_SESSION]
        job_only_pids = {int(handle.pid) for handle in job_only}
        result: list[OwnedProcess] = []
        for process in processes:
            pid = int(process.pid)
            observed_at = time.time()
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
            attribution_source = (
                "session_root"
                if pid == session.record.pid
                else "job_membership"
                if pid in job_only_pids
                else "parent_walk"
            )
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
                first_seen=existing.first_seen if existing else observed_at,
                last_seen=observed_at,
                last_verified_at=observed_at,
                startup_revalidated=existing.startup_revalidated if existing else False,
                attribution_version=PROCESS_ATTRIBUTION_VERSION,
                attribution_source=attribution_source,
                last_attributed_at=observed_at,
                last_job_confirmed_at=(
                    observed_at
                    if attribution_source == "job_membership"
                    else existing.last_job_confirmed_at
                    if existing
                    else None
                ),
            )
            if pid in job_only_pids:
                # Same state and confidence as a walked descendant -- the process
                # is live and provably this session's -- but the reason names the
                # evidence, so an operator seeing a parentless row in the tree can
                # tell "detached, job-owned" from "lineage not sampled".
                observed.evidence_reason = "live_job_object_member"
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
            raise NotFound(session_id, kind="session")
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
                "ownership_diagnostics": list(self._ownership_diagnostics),
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
            "ownership_diagnostics": list(self._ownership_diagnostics),
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
                "system_cpu_pct": None,
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
                "ownership_diagnostics": list(self._ownership_diagnostics),
            }
        await self._ensure_sampled()
        groups: list[dict[str, Any]] = []
        all_processes: list[dict[str, Any]] = []
        # One pass over the owned processes, not one per session. The projection is
        # unchanged: `by_session` reproduces the per-session filter, and
        # `project_of_session` the first-match fallback the group used, both in the
        # same `self.owned` iteration order the comprehensions walked.
        by_session: dict[str, list[dict[str, Any]]] = {}
        project_of_session: dict[str, str | None] = {}
        for owned in self.owned.values():
            project_of_session.setdefault(owned.session_id, owned.project_id)
            if include_ended or owned.exited_at is None:
                by_session.setdefault(owned.session_id, []).append(owned.snapshot())
        session_ids = list(self.sessions.sessions)
        session_ids.extend(sorted(set(project_of_session) - set(self.sessions.sessions)))
        for session_id in session_ids:
            session = self.sessions.sessions.get(session_id)
            processes = by_session.get(session_id, [])
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
                        else project_of_session.get(session_id)
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
            "system_cpu_pct": self._system_cpu_pct,
            "sessions": groups,
            "daemon": daemon,
            "totals": totals,
            "ownership_diagnostics": list(self._ownership_diagnostics),
        }

    async def snapshot_summary_all(self) -> dict[str, Any]:
        """Return the fields used by always-mounted resource and process-watch surfaces.

        Full ownership evidence, process identity, parent lineage, connections, and daemon
        members belong to the explicitly opened inspector. Re-sending them on the app's
        background watch interval made an idle remote browser consume more bandwidth than an
        active terminal.
        """

        snapshot = await self.snapshot_all()
        if not snapshot.get("available"):
            return snapshot
        process_fields = {
            "pid",
            "command",
            "exited_at",
            "cpu_pct",
            "memory_bytes",
            "listeners",
            "server_eligible",
        }
        sessions = []
        for group in snapshot["sessions"]:
            sessions.append(
                {
                    "session_id": group["session_id"],
                    "project_id": group.get("project_id"),
                    "processes": [
                        {key: value for key, value in process.items() if key in process_fields}
                        for process in group["processes"]
                    ],
                }
            )
        daemon = dict(snapshot["daemon"])
        daemon.pop("members", None)
        return {
            "available": True,
            "system_cpu_pct": snapshot.get("system_cpu_pct"),
            "sessions": sessions,
            "daemon": daemon,
            "totals": snapshot["totals"],
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
        if item.attribution_version < PROCESS_ATTRIBUTION_VERSION:
            raise ValueError("process ownership evidence is obsolete; refresh before acting")
        # Deliberately re-derived here rather than read off the row's evidence. This
        # is the last gate before a signal, and it has to hold even when attribution
        # was wrong: a mislabelled desktop shell offered "Terminate tree" is the UI
        # closing itself, which no amount of correct escalation elsewhere excuses.
        if self._is_infrastructure(
            (item.pid, float(item.started_at or 0)),
            self._fingerprints(self._infrastructure_handles()),
        ):
            raise ValueError("process is swe-mux itself, not session-owned")
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


def preview_id(project_id: str, scheme: str, host: str, port: int) -> str:
    """The routing identity of one preview endpoint, stable across daemon restarts.

    A preview id is not decoration: it is the path segment of the proxy route
    (`/preview/<id>/`), which is how a phone reaches a dev server over the
    tailnet, and PreviewPane offers a button to copy that URL. Minting it from
    `uuid4` made every such URL die on any daemon restart - a redeploy, a
    "Reload daemon", a crash - because the registry is rebuilt from scratch and
    the still-running server is re-detected under a fresh id. The server was
    never the casualty; the route to it was.

    Derived from the endpoint identity the registry already dedupes on
    (`_existing_endpoint`), so re-detecting the same server reproduces the same
    id. Session id is deliberately not in the material: ownership legitimately
    moves between sessions in the same Project (a frontend terminal often prints
    the backend's URL), and the existing code already reassigns it while keeping
    the id.
    """
    # chr(0) rather than a printable separator: a project id or host containing
    # the delimiter would otherwise let two different endpoints hash alike.
    material = chr(0).join((project_id, scheme, host, str(port)))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


def static_preview_id(project_id: str, worktree: str, doc_root: str, entry: str) -> str:
    """The routing identity of one static document preview.

    Same contract as ``preview_id`` and for the same reason: the id is the path
    segment of ``/preview/<id>/``, which is the URL a phone opens over the
    tailnet, so re-previewing the same file must reproduce it exactly. Derived
    from what the registry dedupes a static preview on - the Project, the exact
    worktree, the served directory, and the entry file within it - and from
    nothing that legitimately changes underneath it. There is no session in the
    material because a static preview has no owning session at all.
    """
    material = chr(0).join(("static", project_id, worktree, doc_root, entry))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


def static_preview_url(doc_root: str, entry: str) -> str:
    """The `file://` identity a static preview is displayed and titled by.

    Not a destination anything fetches - the daemon reads the bytes itself. It
    exists because every surface above the registry titles a preview by its
    ``url``, and "the absolute path of the document" is the honest answer to what
    a static preview points at.
    """
    absolute = Path(doc_root, entry).as_posix()
    return f"file:///{absolute.lstrip('/')}"


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
    # Every live listener needs a routing identity, but only browser-facing or
    # explicitly opened endpoints belong in navigation.
    listed: bool = True
    # ``loopback`` proxies to a session-owned development server; ``static``
    # serves a directory of the Project checkout from the daemon itself. The two
    # share every surface above the fetch - the proxy route, the sidebar row, the
    # workspace leaf, capture, external open - and differ only in where the bytes
    # come from, which is why this is a field rather than a second registry.
    kind: str = "loopback"
    # A loopback preview is known by host:port. A static one has neither, so its
    # file name is the only thing that identifies it on a tab or a sidebar row.
    label: str = ""
    # Static only: the absolute directory served, and the entry file relative to
    # it. Serving the directory rather than the single file is what makes a
    # page's own ``./style.css`` and ``../assets/x.png`` resolve.
    doc_root: str = ""
    entry: str = ""
    # Static only: the same directory expressed relative to the checkout root
    # ("" when it *is* the root). The Project file watcher speaks in checkout
    # relative paths, so without this the browser would have to subtract one
    # absolute path from another across two path syntaxes to know which change
    # events belong to this preview.
    doc_root_relative: str = ""
    # Static only: the exact worktree root the doc root was resolved inside, or
    # "" for the Project root. Without it a preview opened from a worktree file
    # tab would silently serve the primary checkout's copy of the same path.
    worktree: str = ""

    def snapshot(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class PreviewProbeResult:
    browser_preview: bool
    status: int | None
    content_type: str
    reason: str


def classify_preview_response(
    status: int, content_type: str, prefix: bytes, location: str = ""
) -> PreviewProbeResult:
    """Classify a bounded HTTP response as browser navigation or service plumbing."""
    media_type = content_type.partition(";")[0].strip().casefold()
    if 300 <= status < 400 and location:
        target = urlsplit(location)
        if not target.scheme and not target.netloc:
            return PreviewProbeResult(True, status, media_type, "relative_redirect")
    if not 200 <= status < 300:
        return PreviewProbeResult(False, status, media_type, f"http_status_{status}")
    if media_type in {"text/html", "application/xhtml+xml"}:
        return PreviewProbeResult(True, status, media_type, "html_content_type")
    if b"<html" in prefix.lstrip().lower()[:1024]:
        return PreviewProbeResult(True, status, media_type, "html_signature")
    return PreviewProbeResult(False, status, media_type, f"content_type_{media_type or 'missing'}")


async def probe_browser_preview(url: str) -> PreviewProbeResult:
    """Read only enough of one loopback endpoint to decide if it is browser-facing."""
    timeout = ClientTimeout(total=1.5, sock_connect=0.5, sock_read=1.0)
    try:
        async with ClientSession(timeout=timeout) as client:
            async with client.get(
                url,
                allow_redirects=False,
                headers={
                    "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.1",
                    "User-Agent": "swe-mux-preview-detector/1",
                },
            ) as response:
                prefix = await response.content.read(PREVIEW_PROBE_PREFIX_BYTES)
                return classify_preview_response(
                    response.status,
                    response.headers.get("Content-Type", ""),
                    prefix,
                    response.headers.get("Location", ""),
                )
    except TimeoutError:
        return PreviewProbeResult(False, None, "", "timeout")
    except (ClientError, OSError) as exc:
        return PreviewProbeResult(False, None, "", type(exc).__name__)


class PreviewRegistry:
    def __init__(
        self,
        inspector: ProcessInspector,
        sessions: SessionManager,
        *,
        preview_probe: Callable[[str], Awaitable[PreviewProbeResult]] | None = None,
        store: PreviewStore | None = None,
    ) -> None:
        self.inspector = inspector
        self.sessions = sessions
        self.items: dict[str, PreviewRegistration] = {}
        self._listener_seen: dict[str, float] = {}
        self._preview_probe = preview_probe or probe_browser_preview
        self._preview_probe_state: dict[str, tuple[str, int, float]] = {}
        # Optional so every test that only exercises detection can omit it: a
        # detected preview is rediscovered and needs nothing from disk.
        self._store = store
        if store is not None:
            self._restore(store)

    def _restore(self, store: PreviewStore) -> None:
        """Bring approved previews back from the mirror.

        Their whole reason for existing is that mux cannot find them again on its
        own, so without this a redeploy - or an ordinary "Reload daemon" - silently
        cost the user every preview they had added by hand.
        """
        for record in store.load():
            try:
                item = PreviewRegistration(**record)
            except TypeError as exc:
                log.warning("skipping unrestorable preview %s (%s)", record.get("id"), exc)
                continue
            # Detection owns its own entries and re-creates them within a poll.
            # Restoring one would only race that with a stale session id.
            if item.source == "detected":
                continue
            self.items[item.id] = item
        if self.items:
            log.info("restored %d approved preview(s)", len(self.items))

    def _persist(self) -> None:
        """Mirror the approved set. Called on every change to it, never on detection."""
        if self._store is None:
            return
        self._store.save(
            [item.snapshot() for item in self.items.values() if item.source != "detected"]
        )

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
        that it stopped. Those stay until removed explicitly. Static previews are
        covered by the same rule for a stronger reason: they have no listener to
        observe at all, so there is no absence that could ever mean anything.
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
                self._preview_probe_state.pop(item.id, None)
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
                and item.kind == "loopback"
                and self._endpoint_key(item.url) == (scheme, host, port)
            ),
            None,
        )

    def _record_detected(
        self, session: Any, url: str, *, host: str, port: int, listed: bool = True
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
            existing.listed = existing.listed or listed
            if existing.listed:
                self._preview_probe_state.pop(existing.id, None)
            self._listener_seen[existing.id] = time.time()
            return existing
        item = PreviewRegistration(
            preview_id(session.record.project_id, parsed.scheme, host, port),
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
        item.listed = listed
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
        probe_targets: list[tuple[PreviewRegistration, str]] = []
        now = time.monotonic()
        for group in snapshot.get("sessions", []):
            if project_id is not None and group.get("project_id") != project_id:
                continue
            session = self.sessions.sessions.get(str(group.get("session_id") or ""))
            if session is None:
                continue
            endpoints: dict[tuple[str, int], tuple[str, str, str]] = {}
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
                        process_identity = str(
                            process.get("identity_id")
                            or f"{process.get('pid')}:{process.get('started_at')}"
                        )
                        endpoints[key] = (host, url, process_identity)
            for (_, port), (host, url, process_identity) in endpoints.items():
                item = self._record_detected(session, url, host=host, port=port, listed=False)
                if item.listed:
                    continue
                previous = self._preview_probe_state.get(item.id)
                if previous is not None and previous[0] == process_identity and now < previous[2]:
                    continue
                attempts = (
                    previous[1] + 1
                    if previous is not None and previous[0] == process_identity
                    else 1
                )
                retry = min(
                    PREVIEW_PROBE_INITIAL_RETRY_SECONDS * (2 ** min(attempts - 1, 6)),
                    PREVIEW_PROBE_MAX_RETRY_SECONDS,
                )
                self._preview_probe_state[item.id] = (process_identity, attempts, now + retry)
                probe_targets.append((item, url))

        semaphore = asyncio.Semaphore(PREVIEW_PROBE_CONCURRENCY)

        async def classify(item: PreviewRegistration, url: str) -> None:
            try:
                async with semaphore:
                    result = await self._preview_probe(url)
            except Exception:
                log.exception(
                    "preview classification failed session_id=%s project_id=%s "
                    "preview_id=%s url=%s",
                    item.session_id,
                    item.project_id,
                    item.id,
                    url,
                )
                return
            if result.browser_preview:
                item.listed = True
                self._preview_probe_state.pop(item.id, None)
                log.info(
                    "preview auto-listed session_id=%s project_id=%s preview_id=%s url=%s "
                    "status=%s content_type=%s reason=%s",
                    item.session_id,
                    item.project_id,
                    item.id,
                    url,
                    result.status,
                    result.content_type,
                    result.reason,
                )
            else:
                log.debug(
                    "preview candidate retained session_id=%s project_id=%s preview_id=%s url=%s "
                    "status=%s content_type=%s reason=%s",
                    item.session_id,
                    item.project_id,
                    item.id,
                    url,
                    result.status,
                    result.content_type,
                    result.reason,
                )

        if probe_targets:
            await asyncio.gather(*(classify(item, url) for item, url in probe_targets))

    def routes_for_project(self, project_id: str) -> dict[str, str]:
        routes: dict[str, str] = {}
        for item in self.items.values():
            if item.project_id != project_id:
                continue
            # A static preview has no origin to map: its `file://` url names bytes
            # on disk, not a service another preview could dial.
            if item.kind != "loopback":
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
            preview_id(session.record.project_id, parsed.scheme, host, port),
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
        # This is the preview nothing will ever rediscover, so the mirror is
        # written before the caller is told it exists.
        self._persist()
        return item

    def register_static(
        self,
        *,
        project_id: str,
        doc_root: str,
        entry: str,
        doc_root_relative: str = "",
        worktree: str = "",
        label: str = "",
        project_scope_id: str | None = None,
        repo_group_id: str | None = None,
    ) -> PreviewRegistration:
        """Register a directory of the Project checkout as a browser-facing Preview.

        No process, no port, no owning session. A static preview exists because a
        document in the repository is worth looking at rendered, and the whole
        point of routing it through the Preview registry rather than inventing a
        second viewer is that everything above the fetch already works: the
        `/preview/<id>/` route a phone can open over the tailnet, the sidebar row,
        the workspace leaf with its viewport presets and capture, external open.

        The caller resolves and validates the paths, because it is the layer that
        knows which Project and which worktree the request is scoped to. Both
        arrive absolute and already proven to be inside that checkout.

        Re-registering the same document is idempotent by id, which is what makes
        "preview this file" safe to press twice: it reactivates the registration
        that already exists instead of minting a rival one on a new URL.
        """
        identity = static_preview_id(project_id, worktree, doc_root, entry)
        existing = self.items.get(identity)
        if existing is not None:
            existing.label = label or existing.label
            existing.listed = True
            self._persist()
            return existing
        item = PreviewRegistration(
            identity,
            # Deliberately unowned. A static preview is Project-scoped like the
            # file browser it is opened from, and tying it to whichever session
            # happened to be focused would make it disappear when that session
            # ended - taking a document that is still perfectly readable with it.
            "",
            project_id,
            static_preview_url(doc_root, entry),
            "",
            0,
            "static",
            time.time(),
            project_scope_id,
            repo_group_id,
            kind="static",
            label=label or entry,
            doc_root=doc_root,
            entry=entry,
            doc_root_relative=doc_root_relative,
            worktree=worktree,
        )
        self.items[identity] = item
        # Nothing rediscovers a static preview - there is no listener to poll - so
        # the mirror is written before the caller is told it exists, exactly as
        # for a user-approved one.
        self._persist()
        return item

    def remove(self, preview_id: str) -> None:
        if preview_id not in self.items:
            raise NotFound(preview_id, kind="preview")
        removed = self.items.pop(preview_id)
        self._listener_seen.pop(preview_id, None)
        self._preview_probe_state.pop(preview_id, None)
        # Removing an approved preview is a decision that must outlive the daemon,
        # or the next restart would bring back the one the user just deleted.
        if removed.source != "detected":
            self._persist()

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
                if item.listed and (not session_id or item.session_id == session_id)
            ],
            "candidates": candidates,
        }
