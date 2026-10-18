"""Process inspection in the worker process: `DAEMON_LOAD_ISOLATION.md` item 20.

The daemon's `ProcessInspector` sampled every session's process tree on its own
interpreter: a parent-map snapshot of the whole machine, the whole socket table, a
psutil handle per descendant, every five seconds. All of it is C work that holds
the GIL, and it was the largest single holder of it after the terminal itself
(`DAEMON_LOAD_ISOLATION.md`, the 36-session profile). Here it runs on an instance
of that same class, in the worker, so the daemon's loop pays for one decoded delta
a pass instead of the pass.

**The sampler is the daemon's code, not a copy.** `ProcessService` feeds a
`ProcessInspector` the eight session-record fields a pass reads
(`processes.SESSION_FACT_FIELDS`) through a stand-in for the session manager, and
calls the same `_collect_all` the daemon calls when no worker is ready. There is
one sampling implementation and two processes that can run it.

**Nothing here is state the daemon cannot give back.** The daemon's `owned` is a
mirror, kept current from `encode_delta`'s output. A restarted worker starts empty,
refuses to sample until the daemon has loaded its mirror into it (`load`), and is
then exactly where the last one left off - minus the CPU baselines, so the first
pass after a restart reads 0% CPU, as the daemon's first pass after a start does.

**One lock serialises every inspector operation.** The pass runs in a thread so
the worker's loop keeps answering; a port-conflict description or an action that
read `owned` while that thread rebuilt it would see a dictionary changing size.
Once the service is started it is the worker's `host_scan_lock`, shared with the
ghost-window sweep, because two host walks on two threads convoy on one GIL.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

from .framed_ipc import RequestRefused
from .processes import (
    SESSION_FACT_FIELDS,
    ProcessInspector,
    listen_index_to_wire,
    owned_from_wire,
    owned_key,
    owned_to_wire,
)

if TYPE_CHECKING:
    from .worker import Reply, Worker

log = logging.getLogger(__name__)

SERVICE_NAME = "processes"


class SampledSessions:
    """What a sampling pass reads from `SessionManager`, rebuilt from the daemon's facts.

    `sessions` maps an id to an object whose `record` carries the fact fields,
    `supervisor.supervisor_pid` is the PTY supervisor's pid and `terminal_gateway_pid`
    the terminal gateway's: the attributes `ProcessInspector` reaches through its
    session manager while it samples.
    """

    def __init__(self) -> None:
        self.sessions: dict[str, SimpleNamespace] = {}
        self.supervisor = SimpleNamespace(supervisor_pid=None)
        self.terminal_gateway_pid: int | None = None

    def apply(self, facts: list[Any], supervisor_pid: Any, gateway_pid: Any = None) -> None:
        sessions: dict[str, SimpleNamespace] = {}
        for entry in facts:
            if not isinstance(entry, dict) or not isinstance(entry.get("id"), str):
                continue
            record = SimpleNamespace(**{name: entry.get(name) for name in SESSION_FACT_FIELDS})
            sessions[record.id] = SimpleNamespace(record=record)
        self.sessions = sessions
        self.supervisor.supervisor_pid = _pid_or_none(supervisor_pid)
        self.terminal_gateway_pid = _pid_or_none(gateway_pid)


def _pid_or_none(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


class DeltaEncoder:
    """What the daemon's mirror lacks after a pass, against what it was last sent.

    A process the daemon has never held goes whole; one it holds goes as the fields
    that changed. Every live process changes every pass (CPU, memory, last seen),
    and its command line - most of its bytes - never does, so the patch form keeps
    a pass's delta to a few dozen bytes per live process rather than a kilobyte.
    """

    def __init__(self) -> None:
        self.sent: dict[tuple[int, float], dict[str, Any]] = {}
        self.sent_diagnostics: list[dict[str, Any]] | None = None

    def reset(self, inspector: ProcessInspector) -> None:
        """The daemon now holds exactly what `inspector` does (it just loaded it)."""
        self.sent = {key: owned_to_wire(item) for key, item in inspector.owned.items()}
        self.sent_diagnostics = list(inspector._ownership_diagnostics)

    def encode(self, inspector: ProcessInspector) -> bytes:
        sent = self.sent
        current: dict[tuple[int, float], dict[str, Any]] = {}
        upserts: list[dict[str, Any]] = []
        patches: list[list[Any]] = []
        for key, item in inspector.owned.items():
            wire = owned_to_wire(item)
            current[key] = wire
            previous = sent.get(key)
            if previous is None:
                upserts.append(wire)
            elif previous != wire:
                changed = {
                    name: value for name, value in wire.items() if previous.get(name) != value
                }
                patches.append([key[0], key[1], changed])
        removed = [[pid, started] for pid, started in sent if (pid, started) not in current]
        self.sent = current
        body: dict[str, Any] = {
            "upserts": upserts,
            "patches": patches,
            "removed": removed,
            "daemon_resources": inspector._daemon_resources,
            "listen_index": listen_index_to_wire(inspector._listen_index),
            "system_cpu_pct": inspector._system_cpu_pct,
            "priority": inspector._local_priority_stats(),
        }
        diagnostics = list(inspector._ownership_diagnostics)
        if diagnostics != self.sent_diagnostics:
            body["diagnostics"] = diagnostics
            self.sent_diagnostics = diagnostics
        return json.dumps(body, separators=(",", ":")).encode("utf-8")


class ProcessService:
    """The worker's `processes` service: load, sample, act, port_conflict, unique_memory."""

    name = SERVICE_NAME

    def __init__(self) -> None:
        self.sessions = SampledSessions()
        # No event bus and no telemetry: the daemon emits listener events and
        # persists evidence from its mirror, exactly as it did from its own pass.
        self.inspector = ProcessInspector(
            cast(Any, self.sessions), cast(Any, None), telemetry=None
        )
        self.encoder = DeltaEncoder()
        self.loaded = False
        self.passes = 0
        self._lock = asyncio.Lock()
        self._worker: Worker | None = None

    async def start(self, worker: Worker) -> None:
        self._worker = worker
        # Shared with every other service that walks the host (`Worker.host_scan_lock`).
        self._lock = worker.host_scan_lock

    async def stop(self) -> None:
        self._worker = None

    async def handle(self, method: str, header: dict[str, Any], payload: bytes) -> Reply:
        if method == "load":
            return await self._load(payload)
        if method == "sample":
            return await self._sample(header, payload)
        if method == "act":
            return await self._act(header)
        if method == "port_conflict":
            return await self._port_conflict(header)
        if method == "unique_memory":
            return await self._unique_memory(payload)
        if method == "stats":
            return {
                "loaded": self.loaded,
                "passes": self.passes,
                "retained": len(self.inspector.owned),
            }
        raise RequestRefused(f"unknown method {self.name}.{method}")

    async def _load(self, payload: bytes) -> Reply:
        data = await asyncio.to_thread(json.loads, payload or b"{}")
        records = data.get("owned") if isinstance(data, dict) else None
        if not isinstance(records, list):
            raise RequestRefused("load needs an `owned` list")
        diagnostics = data.get("diagnostics")
        async with self._lock:
            owned = {}
            for record in records:
                if isinstance(record, dict):
                    item = owned_from_wire(record)
                    owned[owned_key(item)] = item
            self.inspector.owned = owned
            if isinstance(diagnostics, list):
                # Continued rather than restarted, so a worker restart does not
                # blank the ownership diagnostics the Processes surface shows.
                self.inspector._ownership_diagnostics = [
                    entry for entry in diagnostics if isinstance(entry, dict)
                ]
            self.encoder.reset(self.inspector)
            self.loaded = True
        log.info("process sampler loaded with %d retained process(es)", len(owned))
        return {"retained": len(owned)}

    async def _sample(self, header: dict[str, Any], payload: bytes) -> Reply:
        if not self.loaded:
            # A worker that restarted between the daemon's load and this pass: an
            # empty sampler would answer with a delta that forgets every process
            # the daemon holds, so it says so and the daemon loads it again.
            raise RequestRefused("the process sampler has not been loaded")
        request = await asyncio.to_thread(json.loads, payload or b"{}")
        if not isinstance(request, dict):
            raise RequestRefused("sample needs a request object")
        startup = bool(header.get("startup"))
        prune = bool(header.get("prune"))
        async with self._lock:
            self._configure(request)
            body = await asyncio.to_thread(self._pass, startup, prune)
            self.passes += 1
        return {"bytes": len(body)}, body

    def _configure(self, request: dict[str, Any]) -> None:
        inspector = self.inspector
        facts = request.get("sessions")
        self.sessions.apply(
            facts if isinstance(facts, list) else [],
            request.get("supervisor_pid"),
            request.get("gateway_pid"),
        )
        job_pids = request.get("job_pids")
        if isinstance(job_pids, dict):
            inspector._job_pids = {
                str(session_id): [int(pid) for pid in pids]
                for session_id, pids in job_pids.items()
                if isinstance(pids, list)
            }
        daemon_pid = request.get("daemon_pid")
        if isinstance(daemon_pid, int) and not isinstance(daemon_pid, bool):
            inspector.daemon_pid_override = daemon_pid
        grace = request.get("orphan_grace_seconds")
        if isinstance(grace, int | float) and not isinstance(grace, bool):
            inspector.orphan_grace_seconds = float(grace)
        priority = request.get("session_priority")
        if isinstance(priority, str):
            inspector.session_priority = priority

    def _pass(self, startup: bool, prune: bool) -> bytes:
        """One collection pass and its delta. Runs in a thread, under the lock."""
        inspector = self.inspector
        inspector._collect_all(startup)
        if prune:
            inspector._prune_retention(time.time())
        return self.encoder.encode(inspector)

    async def _act(self, header: dict[str, Any]) -> Reply:
        session_id = str(header.get("session_id") or "")
        pid = header.get("pid")
        action = str(header.get("action") or "")
        identity = header.get("identity_id")
        if not isinstance(pid, int) or isinstance(pid, bool):
            raise RequestRefused("act needs an integer `pid`")
        identity_id = identity if isinstance(identity, str) and identity else None
        async with self._lock:
            try:
                root_interrupt = await asyncio.to_thread(
                    self._act_sync, session_id, pid, action, identity_id
                )
            except ValueError as exc:
                # The daemon raises it again as the same ValueError, which is the
                # 400 the action route has always answered with.
                return {"refused": str(exc)}
        return {"root_interrupt": root_interrupt}

    def _act_sync(self, session_id: str, pid: int, action: str, identity_id: str | None) -> bool:
        inspector = self.inspector
        process, _ = inspector._owned_live(session_id, pid, identity_id)
        session = self.sessions.sessions.get(session_id)
        root_pid = session.record.pid if session is not None else None
        return inspector._deliver(process, pid, action, root_pid=root_pid)

    async def _port_conflict(self, header: dict[str, Any]) -> Reply:
        host = header.get("host")
        port = header.get("port")
        if not isinstance(host, str) or not isinstance(port, int) or isinstance(port, bool):
            raise RequestRefused("port_conflict needs a `host` and an integer `port`")
        session_id = str(header.get("session_id") or "")
        async with self._lock:
            conflict = await self.inspector.port_conflict(host, port, session_id=session_id)
        return {"conflict": conflict}

    async def _unique_memory(self, payload: bytes) -> Reply:
        pids = json.loads(payload or b"[]")
        if not isinstance(pids, list):
            raise RequestRefused("unique_memory needs a list of pids")
        wanted = [int(pid) for pid in pids if isinstance(pid, int) and not isinstance(pid, bool)]
        async with self._lock:
            result = await asyncio.to_thread(self.inspector._collect_unique_memory, wanted)
        body = json.dumps({str(pid): value for pid, value in result.items()})
        return {"count": len(result)}, body.encode("utf-8")


__all__ = ["SERVICE_NAME", "DeltaEncoder", "ProcessService", "SampledSessions"]
