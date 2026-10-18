"""Batched EventBus adapter for the canonical telemetry ledger, in or out of process.

**Where the ledgers run.** With the worker process on (the default,
`worker_process_enabled`) the three ledgers live in the worker
(`telemetry_worker.py`, `DAEMON_LOAD_ISOLATION.md` item 21): the reducer, its
SQLite writes, the maintenance passes and every query run on that interpreter's
GIL instead of the daemon's, and an OTLP body crosses unparsed. The daemon keeps
everything that decides *when* - the bus subscription and its batching, the
loops with their pacing and load shedding, health - and calls the worker by
method name. With the worker off, `TelemetryLedgers` runs here exactly as it did
before the worker existed.

**One writer per telemetry root.** In worker mode the daemon never opens the
ledgers: a worker that is down means ingestion waits in a bounded buffer and a
query answers `TelemetryUnavailable` (503), never that the daemon opens the
files beside the worker. Two processes writing the same root could take the
catalog and a segment in opposite orders, which is the deadlock `LedgerShared`
exists to prevent within one process and cannot prevent across two.

**Replaying a batch is safe.** An observation's evidence id is a digest of its
session, type, source, sequence number and payload, inserted with `OR IGNORE`, so
a batch the worker committed and then died before acknowledging records nothing
twice when it is sent again.

**Inside one process,** three worker threads each own a ledger (and so its own
connections) over the same files: live ingestion; maintenance - the legacy
catch-up importer, direct native reconciliation, rollups, and sealing; and the
queries the routes make. They used to share one thread, so a seal's integrity
check of a multi-gigabyte month or a first reconciliation of a long transcript
held every live observation behind it for minutes. The ledgers share a
`LedgerShared` write lock, so writes still happen one transaction at a time and
every lock order stays inside one thread; a long read holds nothing. The
batching is what keeps a busy fleet from paying one commit per observation.
"""

from __future__ import annotations

import asyncio
import collections
import json
import logging
import time
from collections.abc import Iterable, Mapping
from functools import partial
from pathlib import Path
from typing import Any, Protocol

from .background_tasks import background
from .errors import TelemetryUnavailable
from .event_bus import EventBus
from .executor_accounting import AccountedThreadPoolExecutor
from .framed_ipc import RemoteError
from .models import MuxEvent
from .telemetry_ledger import CanonicalTelemetryLedger, LedgerShared
from .telemetry_queries import Filters

CANONICAL_TELEMETRY_LOOP = "canonical-telemetry"
CANONICAL_TELEMETRY_BACKFILL_LOOP = "canonical-telemetry-backfill"
CANONICAL_TELEMETRY_ROLLUP_LOOP = "canonical-telemetry-rollup"
CANONICAL_TELEMETRY_RECONCILE_LOOP = "canonical-telemetry-reconcile"
#: The worker service the ledgers run in (`worker.SERVICES`).
TELEMETRY_SERVICE = "telemetry"
_INGEST_BATCH_SIZE = 256
_INGEST_BATCH_SECONDS = 0.05
#: Once the legacy streams are caught up, the legacy store keeps reconciling
#: transcripts on its own schedule, so the importers are asked again this often.
_BACKFILL_RECHECK_SECONDS = 300.0
#: Native stores are re-read past their watermarks this often; an unchanged
#: conversation costs one stat.
_RECONCILE_SECONDS = 300.0
_RECONCILE_INVENTORY = 2000
#: A reconciliation pass at or above this is logged at WARNING. An incremental pass
#: reads what was appended in five minutes; one that takes this long re-read
#: something whole.
SLOW_RECONCILE_SECONDS = 30.0
#: Storage status walks the ledger directory and counts the rollup tables; it is
#: refreshed this often, and after every seal, rather than on every rollup tick.
_STORAGE_REFRESH_SECONDS = 600.0
#: Observations held in the daemon while no worker can take them. Past this the
#: oldest batches go, counted in `buffer_dropped`: a worker that never comes back
#: must not grow the daemon without bound, and the newest evidence is the most
#: useful to keep. At the load harness's rate this is about seven minutes.
BUFFER_EVENT_LIMIT = 50_000
#: How often a non-empty buffer is offered to the worker again while the bus is quiet.
BUFFER_RETRY_SECONDS = 1.0
#: How long a query waits for a worker that is starting before it answers 503.
QUERY_WAIT_SECONDS = 10.0
#: Generous on purpose: opening runs any schema migration, a seal integrity-checks a
#: whole month, and a reconciliation can re-read a long transcript. A call that
#: outlives these keeps running in the worker; only the daemon stops waiting.
OPEN_TIMEOUT_SECONDS = 600.0
MAINTENANCE_TIMEOUT_SECONDS = 3600.0
QUERY_TIMEOUT_SECONDS = 300.0
RECORD_TIMEOUT_SECONDS = 300.0

#: The maintenance-ledger methods the service calls, and the only ones the worker runs.
MAINTENANCE_METHODS = frozenset(
    {
        "import_legacy_runs_batch",
        "import_legacy_turns_batch",
        "import_legacy_compactions_batch",
        "import_legacy_verifications_batch",
        "import_legacy_batch",
        "reconcile_native_rows",
        "storage_status",
        "schema_status",
        "rebuild_next_closed_day",
        "rebuild_next_closed_hour",
        "seal_next_segment",
    }
)
#: The query-ledger methods the routes reach, and the only ones the worker runs.
QUERY_METHODS = frozenset(
    {
        "schema_status",
        "tool_summary",
        "skill_summary",
        "verification_summary",
        "workload_summary",
        "compaction_summary",
        "quality_summary",
        "metric_summary",
        "inefficiency_findings",
        "compare_cohorts",
        "tool_page",
        "entity_page",
        "tool_audit",
        "run_audit",
        "turn_audit",
        "review_finding",
        "shadow_comparison",
        "export_page",
        "quota_usage_evidence",
        "quota_usage_coverage",
        "parser_signatures",
    }
)
#: Methods whose first positional argument is a path; it crosses as a string.
PATH_ARGUMENT_METHODS = frozenset(
    {
        "import_legacy_runs_batch",
        "import_legacy_turns_batch",
        "import_legacy_compactions_batch",
        "import_legacy_verifications_batch",
        "import_legacy_batch",
        "shadow_comparison",
    }
)
#: Event fields that, when an event carries them, override the session's own
#: dimensions for that observation.
_EVENT_DIMENSION_OVERRIDES = ("model", "harness_version", "turn_id", "agent_id")

#: (backend, harness version, parser version, {(event name, recognised): count})
PendingSignatures = tuple[str, str | None, str, dict[tuple[str, bool], int]]
Batch = list[tuple[MuxEvent, Mapping[str, Any]]]

log = logging.getLogger(__name__)


__all__ = [
    "CanonicalTelemetryService",
    "TelemetryLedgers",
    "TelemetryUnavailable",
    "WorkerLedgers",
]


def apply_event_dimensions(base: Mapping[str, Any], event: MuxEvent) -> dict[str, Any]:
    """A session's dimensions with the fields this event names for itself laid over them."""
    result = dict(base)
    for key in _EVENT_DIMENSION_OVERRIDES:
        if event.payload.get(key) is not None:
            result[key] = event.payload[key]
    return result


# --- the wire ---------------------------------------------------------------------
#
# What crosses to the worker. Events cross as their seven fields, encoded with
# `default=str` exactly as `telemetry_schema.canonical_json` encodes a payload for
# its digest, so an evidence id computed in the worker is the one this process
# would have computed. Results cross as JSON; the one caller that needs tuples back
# gets them (`adapt_result`).


def encode_batch(batch: Batch, signatures: Iterable[PendingSignatures]) -> bytes:
    return json.dumps(
        {
            "events": [
                [
                    event.ts,
                    event.session_id,
                    event.source,
                    event.type,
                    event.payload,
                    event.seq,
                    event.transient,
                ]
                for event, _ in batch
            ],
            "dims": [dict(dimensions) for _, dimensions in batch],
            "signatures": [
                [
                    backend,
                    harness_version,
                    parser_version,
                    [
                        [name, bool(recognised), count]
                        for (name, recognised), count in counts.items()
                    ],
                ]
                for backend, harness_version, parser_version, counts in signatures
            ],
        },
        default=str,
        separators=(",", ":"),
    ).encode("utf-8")


def decode_batch(payload: bytes) -> tuple[Batch, list[PendingSignatures]]:
    data = json.loads(payload or b"{}")
    events = [
        MuxEvent(
            float(ts),
            session_id,
            str(source),
            str(kind),
            event_payload if isinstance(event_payload, dict) else {},
            seq=int(seq),
            transient=bool(transient),
        )
        for ts, session_id, source, kind, event_payload, seq, transient in data.get("events") or []
    ]
    dimensions = data.get("dims") or []
    if len(dimensions) != len(events):
        raise ValueError("a telemetry batch carries one dimension set per event")
    signatures: list[PendingSignatures] = [
        (
            str(backend),
            harness_version,
            str(parser_version),
            {(str(name), bool(recognised)): int(count) for name, recognised, count in counts},
        )
        for backend, harness_version, parser_version, counts in data.get("signatures") or []
    ]
    return list(zip(events, dimensions, strict=True)), signatures


def encode_call(args: tuple[Any, ...], kwargs: Mapping[str, Any]) -> bytes:
    return json.dumps({"args": list(args), "kwargs": dict(kwargs)}, default=str).encode("utf-8")


def decode_call(payload: bytes, method: str) -> tuple[list[Any], dict[str, Any]]:
    data = json.loads(payload or b"{}")
    args = list(data.get("args") or [])
    kwargs = dict(data.get("kwargs") or {})
    if method in PATH_ARGUMENT_METHODS and args:
        args[0] = Path(args[0])
    return args, kwargs


def encode_result(result: Any) -> bytes:
    return json.dumps(result, default=str).encode("utf-8")


def adapt_result(method: str, result: Any) -> Any:
    """Restore the one shape JSON does not keep: `quota_usage_evidence`'s row tuples."""
    if method == "quota_usage_evidence" and isinstance(result, dict):
        return {key: [tuple(row) for row in rows] for key, rows in result.items()}
    return result


# --- where the ledgers run --------------------------------------------------------


class TelemetryLedgers:
    """The three ledgers over one telemetry root, each on the thread that owns it.

    Runs in the daemon when the worker process is off, and in the worker
    (`telemetry_worker.py`) when it is on: one implementation either way.
    """

    def __init__(self, root: Path) -> None:
        shared = LedgerShared()
        #: Live ingestion's ledger; only `ingest_executor` calls into it while running.
        self.ingest = CanonicalTelemetryLedger(root, shared=shared)
        self.maintenance = CanonicalTelemetryLedger(root, shared=shared)
        self.queries = CanonicalTelemetryLedger(root, shared=shared)
        self.ingest_executor = AccountedThreadPoolExecutor(
            max_workers=1, thread_name_prefix="telemetry-ledger"
        )
        self.maintenance_executor = AccountedThreadPoolExecutor(
            max_workers=1, thread_name_prefix="telemetry-maintenance"
        )
        self.query_executor = AccountedThreadPoolExecutor(
            max_workers=1, thread_name_prefix="telemetry-query"
        )
        self.last_error: str | None = None
        self.last_error_at: float | None = None

    def record_batch(self, batch: Batch, signatures: list[PendingSignatures]) -> int:
        """Signatures, then the batch, on the ingestion thread. Raises what SQLite raises."""
        try:
            for backend, harness_version, parser_version, counts in signatures:
                self.ingest.record_parser_signatures(
                    backend=backend,
                    harness_version=harness_version,
                    parser_version=parser_version,
                    signatures=counts,
                )
            return self.ingest.record_events(batch)
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"[:400]
            self.last_error_at = time.time()
            raise

    async def record(self, batch: Batch, signatures: list[PendingSignatures]) -> int:
        loop = asyncio.get_running_loop()
        inserted: int = await loop.run_in_executor(
            self.ingest_executor, self.record_batch, batch, signatures
        )
        return inserted

    async def maintain(self, method: str, /, *args: Any, **kwargs: Any) -> Any:
        if method not in MAINTENANCE_METHODS:
            raise LookupError(f"not a maintenance method: {method}")
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self.maintenance_executor, partial(getattr(self.maintenance, method), *args, **kwargs)
        )

    async def query(self, method: str, /, *args: Any, **kwargs: Any) -> Any:
        if method not in QUERY_METHODS:
            raise LookupError(f"not a query method: {method}")
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self.query_executor, partial(getattr(self.queries, method), *args, **kwargs)
        )

    def counters(self) -> dict[str, Any]:
        return {
            "last_seal_seconds": self.maintenance.last_seal_seconds,
            "seals": self.maintenance.seals,
            "seal_invalidations": self.ingest.seal_invalidations
            + self.maintenance.seal_invalidations,
            "rejected_observations": self.ingest.rejected_observations
            + self.maintenance.rejected_observations,
            "last_error": self.last_error,
            "last_error_at": self.last_error_at,
        }

    async def shutdown(self) -> None:
        for executor in (self.ingest_executor, self.maintenance_executor, self.query_executor):
            await asyncio.to_thread(executor.shutdown, wait=True)

    def close(self) -> None:
        for ledger in (self.ingest, self.maintenance, self.queries):
            ledger.close()


class _Backend(Protocol):
    async def record(self, batch: Batch, signatures: list[PendingSignatures]) -> int: ...

    async def maintain(self, method: str, /, *args: Any, **kwargs: Any) -> Any: ...

    async def query(self, method: str, /, *args: Any, **kwargs: Any) -> Any: ...

    def counters(self) -> dict[str, Any]: ...

    async def shutdown(self) -> None: ...

    def close(self) -> None: ...


class WorkerLedgers:
    """The ledgers in the worker process, reached by method name.

    Opened in each worker the first time it is used (`WorkerHost.generation`
    says which worker that is), because a restarted worker starts with nothing
    open. A worker that is not there raises `TelemetryUnavailable`; one that fails
    a call raises `RuntimeError`; a query that refuses its arguments raises the
    same `ValueError` it raises in process.
    """

    def __init__(self, worker: Any, root: Path) -> None:
        self._worker = worker
        self._root = root
        self._opened_generation: int | None = None
        self._open_lock = asyncio.Lock()
        self._counters: dict[str, Any] = {
            "last_seal_seconds": None,
            "seals": 0,
            "seal_invalidations": 0,
            "rejected_observations": 0,
            "last_error": None,
            "last_error_at": None,
        }
        self.opens = 0

    async def _ensure_open(self, wait_seconds: float) -> None:
        worker = self._worker
        if not worker.ready and wait_seconds > 0:
            await worker.wait_ready(wait_seconds)
        if not worker.ready:
            raise TelemetryUnavailable("the worker process is not running")
        if self._opened_generation == worker.generation:
            return
        async with self._open_lock:
            generation = worker.generation
            if self._opened_generation == generation:
                return
            reply, _ = await self._send(
                "open", {"root": str(self._root)}, b"", timeout_seconds=OPEN_TIMEOUT_SECONDS
            )
            self._opened_generation = generation
            self.opens += 1
            log.info("canonical telemetry ledgers opened in worker generation %s", generation)

    async def _send(
        self, method: str, header: Mapping[str, Any], payload: bytes, *, timeout_seconds: float
    ) -> tuple[dict[str, Any], bytes]:
        from .worker_host import WorkerUnavailable

        try:
            reply, body = await self._worker.call(
                TELEMETRY_SERVICE, method, header, payload, timeout_seconds=timeout_seconds
            )
        except WorkerUnavailable as exc:
            raise TelemetryUnavailable(str(exc)) from exc
        except RemoteError as exc:
            if "not been opened" in exc.error:
                # A worker that restarted between the open and this call.
                self._opened_generation = None
                raise TelemetryUnavailable("the worker restarted; reopening its ledgers") from exc
            raise RuntimeError(f"canonical telemetry in the worker failed: {exc.error}") from exc
        counters = reply.get("counters")
        if isinstance(counters, dict):
            self._counters = counters
        return reply, body

    async def _call(
        self,
        method: str,
        header: Mapping[str, Any],
        payload: bytes,
        *,
        wait: float,
        timeout_seconds: float,
    ) -> tuple[dict[str, Any], bytes]:
        await self._ensure_open(wait)
        return await self._send(method, header, payload, timeout_seconds=timeout_seconds)

    async def record(self, batch: Batch, signatures: list[PendingSignatures]) -> int:
        payload = await asyncio.to_thread(encode_batch, batch, signatures)
        reply, _ = await self._call(
            "record", {}, payload, wait=0.0, timeout_seconds=RECORD_TIMEOUT_SECONDS
        )
        return int(reply.get("inserted") or 0)

    async def _method(
        self,
        kind: str,
        method: str,
        args: tuple[Any, ...],
        kwargs: Mapping[str, Any],
        *,
        wait: float,
        timeout_seconds: float,
    ) -> Any:
        payload = await asyncio.to_thread(encode_call, args, kwargs)
        reply, body = await self._call(
            kind, {"method": method}, payload, wait=wait, timeout_seconds=timeout_seconds
        )
        refusal = reply.get("value_error")
        if isinstance(refusal, str):
            raise ValueError(refusal)
        result = await asyncio.to_thread(json.loads, body) if body else None
        return adapt_result(method, result)

    async def maintain(self, method: str, /, *args: Any, **kwargs: Any) -> Any:
        return await self._method(
            "maintain",
            method,
            args,
            kwargs,
            wait=0.0,
            timeout_seconds=MAINTENANCE_TIMEOUT_SECONDS,
        )

    async def query(self, method: str, /, *args: Any, **kwargs: Any) -> Any:
        return await self._method(
            "query",
            method,
            args,
            kwargs,
            wait=QUERY_WAIT_SECONDS,
            timeout_seconds=QUERY_TIMEOUT_SECONDS,
        )

    async def otlp(
        self, *, session_id: str, backend: str, dimensions: Mapping[str, Any], body: bytes
    ) -> dict[str, Any]:
        """Parse, reduce and record one OTLP body in the worker; raises ValueError if malformed."""
        reply, _ = await self._call(
            "otlp",
            {"session_id": session_id, "backend": backend, "dims": dict(dimensions)},
            body,
            wait=0.0,
            timeout_seconds=RECORD_TIMEOUT_SECONDS,
        )
        refusal = reply.get("bad_request")
        if isinstance(refusal, str):
            raise ValueError(refusal)
        return reply

    def counters(self) -> dict[str, Any]:
        return dict(self._counters)

    async def shutdown(self) -> None:
        worker = self._worker
        if self._opened_generation is None or not worker.ready:
            return
        try:
            await self._send("close", {}, b"", timeout_seconds=30.0)
        except (TelemetryUnavailable, RuntimeError, TimeoutError) as exc:
            log.warning("could not close the worker's telemetry ledgers: %s", exc)
        self._opened_generation = None

    def close(self) -> None:
        return None


class _LocalBackend:
    """`TelemetryLedgers` in this process, behind the same surface as `WorkerLedgers`."""

    def __init__(self, ledgers: TelemetryLedgers) -> None:
        self.ledgers = ledgers

    async def record(self, batch: Batch, signatures: list[PendingSignatures]) -> int:
        return await self.ledgers.record(batch, signatures)

    async def maintain(self, method: str, /, *args: Any, **kwargs: Any) -> Any:
        return await self.ledgers.maintain(method, *args, **kwargs)

    async def query(self, method: str, /, *args: Any, **kwargs: Any) -> Any:
        return await self.ledgers.query(method, *args, **kwargs)

    def counters(self) -> dict[str, Any]:
        return self.ledgers.counters()

    async def shutdown(self) -> None:
        await self.ledgers.shutdown()

    def close(self) -> None:
        self.ledgers.close()


class CanonicalTelemetryService:
    """Daemon adapter around the canonical telemetry ledgers, wherever they run."""

    def __init__(self, root: Path, *, in_worker: bool = False) -> None:
        self.root = root
        self.in_worker = in_worker
        self._local: TelemetryLedgers | None = None
        self._backend: _Backend | None = None
        self._worker_ledgers: WorkerLedgers | None = None
        if not in_worker:
            ledgers = TelemetryLedgers(root)
            self._local = ledgers
            self._backend = _LocalBackend(ledgers)
            # The in-process ledgers by their old names, which tests and tools reach.
            self.ledger = ledgers.ingest
            self._maintenance = ledgers.maintenance
            self._queries = ledgers.queries
            self._maintenance_executor = ledgers.maintenance_executor
        self._events: EventBus | None = None
        self._queue: asyncio.Queue[MuxEvent | None] | None = None
        self._sessions: Any = None
        self._history: Any = None
        self._task: asyncio.Task[None] | None = None
        self._backfill_task: asyncio.Task[None] | None = None
        self._rollup_task: asyncio.Task[None] | None = None
        self._reconcile_task: asyncio.Task[None] | None = None
        self._legacy_database: Path | None = None
        self._accepted = 0
        self._backfilled = 0
        self._backfill_completed = False
        self._backfill_stream = "tool_events"
        self._backfill_passes = 0
        self._backfill_last_pass_at: float | None = None
        self._provider_dropped = 0
        self._provider_batches = 0
        self._pending_signatures: list[PendingSignatures] = []
        # Batches waiting for a worker, oldest first, and the observations they hold.
        self._buffer: collections.deque[tuple[Batch, list[PendingSignatures]]] = (
            collections.deque()
        )
        self._buffered_events = 0
        self._buffer_dropped = 0
        self._worker_unavailable = 0
        self._last_sealed_period: str | None = None
        self._last_sealed_at: float | None = None
        self._reconcile_summary: dict[str, Any] = {}
        self._storage_refreshed_at: float | None = None
        self._storage: dict[str, Any] = {
            "bytes": 0,
            "segments": 0,
            "retention": "forever",
            "automatic_deletion": False,
        }
        self._schema: dict[str, Any] = {}
        self._last_error: str | None = None
        self._last_error_at: float | None = None

    def attach_worker(self, worker: Any) -> None:
        """Reach the ledgers in `worker` (a `WorkerHost`); required in worker mode."""
        if not self.in_worker:
            return
        self._worker_ledgers = WorkerLedgers(worker, self.root)
        self._backend = self._worker_ledgers

    @property
    def forwards_otlp(self) -> bool:
        """Whether an OTLP body goes to the worker unparsed (`ingest_otlp`)."""
        return self._worker_ledgers is not None

    def _require_backend(self) -> _Backend:
        backend = self._backend
        if backend is None:
            raise RuntimeError("canonical telemetry runs in the worker but none was attached")
        return backend

    def start(
        self,
        events: EventBus,
        *,
        sessions: Any,
        legacy_database: Path | None = None,
        history: Any = None,
    ) -> None:
        self._require_backend()
        self._events = events
        self._sessions = sessions
        self._history = history
        self._queue = events.subscribe(name=CANONICAL_TELEMETRY_LOOP)  # type: ignore[assignment]
        self._task = background.start(CANONICAL_TELEMETRY_LOOP, self._consume)
        self._legacy_database = legacy_database
        if legacy_database is not None:
            self._backfill_task = background.start(
                CANONICAL_TELEMETRY_BACKFILL_LOOP, self._backfill
            )
        else:
            self._backfill_completed = True
        self._rollup_task = background.start(CANONICAL_TELEMETRY_ROLLUP_LOOP, self._rollup)
        if history is not None:
            self._reconcile_task = background.start(
                CANONICAL_TELEMETRY_RECONCILE_LOOP, self._reconcile
            )

    def session_dimensions(self, session: Any) -> dict[str, Any]:
        """One session's dimensions, before any event lays its own fields over them."""
        result = CanonicalTelemetryLedger.dimensions_from_session(session)
        result["run_started_at"] = session.record.agent_run_started_at or session.record.created_at
        return result

    def _dimensions(self, event: MuxEvent) -> Mapping[str, Any]:
        session = self._sessions.sessions.get(event.session_id) if event.session_id else None
        if session is None:
            return {
                "session_id": event.session_id,
                "run_id": event.payload.get("agent_run_id") or event.session_id,
                "native_conversation_id": event.payload.get("native_session_id"),
                "turn_id": event.payload.get("turn_id"),
                "turn_ordinal": event.payload.get("turn_epoch"),
                "agent_id": event.payload.get("agent_id") or "root",
                "project_id": event.payload.get("project_id"),
                "backend": event.payload.get("backend") or "unknown",
                "model": event.payload.get("model"),
                "harness_version": event.payload.get("harness_version"),
                "origin": "mux_owned",
                "source_locator": event.payload.get("source_locator"),
            }
        return apply_event_dimensions(self.session_dimensions(session), event)

    def enqueue_provider_event(self, event: MuxEvent) -> bool:
        """Queue an already privacy-reduced provider event without awaiting SQLite."""

        if self._queue is None:
            self._provider_dropped += 1
            return False
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            self._provider_dropped += 1
            if self._provider_dropped == 1 or self._provider_dropped % 1000 == 0:
                log.error(
                    "canonical telemetry provider ingress dropped an event (%d total)",
                    self._provider_dropped,
                )
            return False
        return True

    def note_parser_signatures(
        self,
        *,
        backend: str,
        harness_version: str | None,
        parser_version: str,
        signatures: Mapping[tuple[str, bool], int],
    ) -> None:
        """Remember which provider event names a batch carried; flushed with the next batch."""

        self._provider_batches += 1
        if signatures:
            self._pending_signatures.append(
                (backend, harness_version, parser_version, dict(signatures))
            )

    async def ingest_otlp(self, session: Any, body: bytes) -> dict[str, int] | None:
        """Hand one OTLP body to the worker unparsed: `{accepted, rejected}`, or None.

        None means the caller parses it here, as it does with no worker: either
        the ledgers run in process, or the worker is away and the reduced events
        wait in the buffer like any other observation. A malformed body raises the
        same `ValueError` either way.
        """
        ledgers = self._worker_ledgers
        if ledgers is None:
            return None
        try:
            reply = await ledgers.otlp(
                session_id=str(session.record.id),
                backend=str(session.record.backend),
                dimensions=self.session_dimensions(session),
                body=body,
            )
        except TelemetryUnavailable:
            self._worker_unavailable += 1
            return None
        self._provider_batches += 1
        inserted = int(reply.get("inserted") or 0)
        self._accepted += inserted
        return {"accepted": int(reply.get("events") or 0), "rejected": 0}

    async def _consume(self) -> None:
        assert self._queue is not None
        loop = asyncio.get_running_loop()
        while True:
            if self._buffer:
                # Offer what is waiting again even while the bus is quiet.
                try:
                    first = await asyncio.wait_for(self._queue.get(), BUFFER_RETRY_SECONDS)
                except TimeoutError:
                    await self._flush_buffer()
                    continue
            else:
                first = await self._queue.get()
            if first is None:
                self._queue.task_done()
                await self._flush_signatures(loop)
                return
            await asyncio.sleep(_INGEST_BATCH_SECONDS)
            events = [first]
            stop_after_batch = False
            while len(events) < _INGEST_BATCH_SIZE:
                try:
                    item = self._queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                if item is None:
                    self._queue.task_done()
                    stop_after_batch = True
                    break
                events.append(item)
            batch = [(event, self._dimensions(event)) for event in events]
            signatures = self._pending_signatures
            self._pending_signatures = []
            try:
                with background.iteration(CANONICAL_TELEMETRY_LOOP):
                    await self._deliver(batch, signatures)
            except Exception as exc:  # pragma: no cover - iteration normally absorbs this
                self._note_error(exc)
                log.exception("canonical telemetry batch failed")
            finally:
                for _event in events:
                    self._queue.task_done()
            if stop_after_batch:
                await self._flush_signatures(loop)
                return

    def _note_error(self, exc: BaseException) -> None:
        self._last_error = f"{type(exc).__name__}: {exc}"[:400]
        self._last_error_at = time.time()

    async def _deliver(self, batch: Batch, signatures: list[PendingSignatures]) -> None:
        """Record one batch, behind anything already waiting; buffer it if no worker can."""
        backend = self._require_backend()
        if self._buffer and not await self._flush_buffer():
            self._hold(batch, signatures)
            return
        try:
            self._accepted += await backend.record(batch, signatures)
        except TelemetryUnavailable:
            self._worker_unavailable += 1
            self._hold(batch, signatures)
        except Exception as exc:
            self._note_error(exc)
            raise

    async def _flush_buffer(self) -> bool:
        """Send waiting batches oldest first; whether the buffer emptied."""
        backend = self._require_backend()
        while self._buffer:
            batch, signatures = self._buffer[0]
            try:
                inserted = await backend.record(batch, signatures)
            except TelemetryUnavailable:
                return False
            except Exception as exc:
                # A batch the worker cannot record is not retried forever.
                self._note_error(exc)
                log.exception("canonical telemetry dropped a buffered batch it could not record")
                inserted = 0
            self._buffer.popleft()
            self._buffered_events -= len(batch)
            self._accepted += inserted
        return True

    def _hold(self, batch: Batch, signatures: list[PendingSignatures]) -> None:
        if not batch and not signatures:
            return
        self._buffer.append((batch, signatures))
        self._buffered_events += len(batch)
        while self._buffered_events > BUFFER_EVENT_LIMIT and len(self._buffer) > 1:
            dropped, _ = self._buffer.popleft()
            self._buffered_events -= len(dropped)
            self._buffer_dropped += len(dropped)
            if self._buffer_dropped == len(dropped) or self._buffer_dropped % 10_000 < len(dropped):
                log.error(
                    "canonical telemetry buffer full with no worker; dropped %d observation(s) "
                    "(%d total)",
                    len(dropped),
                    self._buffer_dropped,
                )

    async def _flush_signatures(self, loop: asyncio.AbstractEventLoop) -> None:
        signatures = self._pending_signatures
        self._pending_signatures = []
        if signatures or self._buffer:
            try:
                await self._deliver([], signatures)
            except Exception:
                log.exception("canonical telemetry could not record its last signatures")

    async def _maintain(self, method: str, /, *args: Any, **kwargs: Any) -> Any:
        """Run one maintenance-ledger method, on its own thread wherever the ledgers live."""

        return await self._require_backend().maintain(method, *args, **kwargs)

    async def _backfill(self) -> None:
        assert self._legacy_database is not None
        await asyncio.sleep(10)
        importers = (
            ("history", "import_legacy_runs_batch"),
            ("status_turns", "import_legacy_turns_batch"),
            ("compactions", "import_legacy_compactions_batch"),
            ("tier0_tests", "import_legacy_verifications_batch"),
            ("tool_events", "import_legacy_batch"),
        )
        stream_index = 0
        while True:
            stream, importer = importers[stream_index]
            self._backfill_stream = stream
            unavailable = False
            with background.iteration(CANONICAL_TELEMETRY_BACKFILL_LOOP):
                try:
                    result = await self._maintain(importer, self._legacy_database, batch_size=250)
                except TelemetryUnavailable:
                    # Not a fault: the worker is away, and its next incarnation picks
                    # up where the importer's own watermark says.
                    unavailable = True
                else:
                    self._backfilled += int(result["imported"])
                    if result["completed"]:
                        stream_index += 1
            if unavailable:
                await background.pace(CANONICAL_TELEMETRY_BACKFILL_LOOP, 5.0, deferrable=True)
                continue
            if stream_index < len(importers):
                await background.pace(CANONICAL_TELEMETRY_BACKFILL_LOOP, 0.25, deferrable=True)
                continue
            # Every stream is caught up. The legacy store keeps reconciling
            # transcripts, so come back later and import whatever it appended.
            self._backfill_completed = True
            self._backfill_passes += 1
            self._backfill_last_pass_at = time.time()
            stream_index = 0
            await background.pace(
                CANONICAL_TELEMETRY_BACKFILL_LOOP, _BACKFILL_RECHECK_SECONDS, deferrable=True
            )

    async def _reconcile(self) -> None:
        """Read each native store past its watermark straight into the reducer."""

        await asyncio.sleep(60)
        while True:
            with background.iteration(CANONICAL_TELEMETRY_RECONCILE_LOOP):
                try:
                    await self._reconcile_pass()
                except TelemetryUnavailable:
                    log.debug("canonical telemetry reconciliation skipped: no worker")
            await background.pace(
                CANONICAL_TELEMETRY_RECONCILE_LOOP, _RECONCILE_SECONDS, deferrable=True
            )

    async def _reconcile_pass(self) -> dict[str, Any]:
        rows = await self._history.telemetry_history_rows(_RECONCILE_INVENTORY)
        started = time.perf_counter()
        summary: dict[str, Any] = await self._maintain("reconcile_native_rows", rows)
        elapsed = round(time.perf_counter() - started, 3)
        self._reconcile_summary = {
            **summary,
            "at": time.time(),
            "inventory": len(rows),
            "elapsed_s": elapsed,
        }
        log.log(
            logging.WARNING if elapsed >= SLOW_RECONCILE_SECONDS else logging.DEBUG,
            "canonical telemetry reconciliation scanned=%d skipped=%d errors=%d inserted=%d "
            "in %.1fs",
            summary["scanned"],
            summary["skipped"],
            summary["errors"],
            summary["inserted"],
            elapsed,
            extra={"elapsed_s": elapsed, "inventory": len(rows)},
        )
        return self._reconcile_summary

    async def _refresh_storage(self, *, force: bool = False) -> None:
        now = time.monotonic()
        refreshed = self._storage_refreshed_at
        if not force and refreshed is not None and now - refreshed < _STORAGE_REFRESH_SECONDS:
            return
        self._storage = await self._maintain("storage_status")
        self._storage_refreshed_at = now

    async def _rollup(self) -> None:
        await asyncio.sleep(30)
        # Read once, as before; asked again only while no worker can answer, and a
        # read that fails for any other reason is a fault the rollups go on past.
        waiting_for_worker = True
        while waiting_for_worker:
            waiting_for_worker = False
            with background.iteration(CANONICAL_TELEMETRY_ROLLUP_LOOP):
                try:
                    self._schema = await self._maintain("schema_status")
                except TelemetryUnavailable:
                    waiting_for_worker = True
                else:
                    if self._schema.get("drift"):
                        log.warning(
                            "canonical telemetry schema drift in %s",
                            ", ".join(self._schema["drift"]),
                        )
            if waiting_for_worker:
                await background.pace(CANONICAL_TELEMETRY_ROLLUP_LOOP, 5.0, deferrable=True)
        while True:
            rebuilt: str | None = None
            with background.iteration(CANONICAL_TELEMETRY_ROLLUP_LOOP):
                try:
                    # Rollups wait for the first catch-up pass so a day is not rebuilt
                    # once per import batch; storage is reported regardless.
                    sealed = None
                    if self._backfill_completed:
                        rebuilt = await self._maintain("rebuild_next_closed_day")
                        if rebuilt is None:
                            rebuilt = await self._maintain("rebuild_next_closed_hour")
                        if rebuilt is None:
                            sealed = await self._maintain("seal_next_segment")
                            if sealed is not None:
                                self._last_sealed_period = str(sealed["period"])
                                self._last_sealed_at = float(sealed["sealed_at"])
                    await self._refresh_storage(force=sealed is not None)
                except TelemetryUnavailable:
                    log.debug("canonical telemetry rollup skipped: no worker")
            cadence = 0.25 if rebuilt is not None else 5.0 if not self._backfill_completed else 60.0
            await background.pace(CANONICAL_TELEMETRY_ROLLUP_LOOP, cadence, deferrable=True)

    async def stop(self) -> None:
        for name, attribute in (
            (CANONICAL_TELEMETRY_ROLLUP_LOOP, "_rollup_task"),
            (CANONICAL_TELEMETRY_BACKFILL_LOOP, "_backfill_task"),
            (CANONICAL_TELEMETRY_RECONCILE_LOOP, "_reconcile_task"),
        ):
            if getattr(self, attribute) is not None:
                await background.stop(name)
                setattr(self, attribute, None)
        if self._queue is not None and self._events is not None:
            self._events.unsubscribe(self._queue)  # type: ignore[arg-type]
            await self._queue.put(None)
        if self._task is not None:
            await self._task
        self._task = None
        if self._buffered_events:
            log.warning(
                "canonical telemetry stopped with %d observation(s) no worker took",
                self._buffered_events,
            )
        if self._backend is not None:
            await self._backend.shutdown()

    def close(self) -> None:
        if self._backend is not None:
            self._backend.close()

    def health(self) -> dict[str, Any]:
        counters = self._backend.counters() if self._backend is not None else {}
        ledgers = self._worker_ledgers
        return {
            "accepted": self._accepted,
            "backfilled": self._backfilled,
            "backfill_completed": self._backfill_completed,
            "backfill_stream": self._backfill_stream,
            "backfill_passes": self._backfill_passes,
            "backfill_last_pass_at": self._backfill_last_pass_at,
            "provider_batches": self._provider_batches,
            "provider_dropped": self._provider_dropped,
            "reconciliation": dict(self._reconcile_summary),
            "last_sealed_period": self._last_sealed_period,
            "last_sealed_at": self._last_sealed_at,
            "last_seal_seconds": counters.get("last_seal_seconds"),
            "seals": counters.get("seals", 0),
            "seal_invalidations": counters.get("seal_invalidations", 0),
            "rejected_observations": counters.get("rejected_observations", 0),
            "storage": dict(self._storage),
            "storage_refreshed_at": self._storage_refreshed_at,
            "schema": {
                key: self._schema[key]
                for key in ("version", "drift", "migrations")
                if key in self._schema
            },
            "queue_depth": self._queue.qsize() if self._queue is not None else 0,
            "running": bool(self._task and not self._task.done()),
            # Where the ledgers run, and what waits for them while they cannot.
            "in_worker": self.in_worker,
            "buffered_events": self._buffered_events,
            "buffer_dropped": self._buffer_dropped,
            "worker_unavailable": self._worker_unavailable,
            "worker_opens": ledgers.opens if ledgers is not None else 0,
            "last_error": self._last_error or counters.get("last_error"),
            "last_error_at": self._last_error_at or counters.get("last_error_at"),
        }

    async def _call(self, method: str, /, *args: Any, **kwargs: Any) -> Any:
        """Run one query-ledger method on the query thread, wherever the ledgers live."""

        return await self._require_backend().query(method, *args, **kwargs)

    async def schema_status(self) -> dict[str, Any]:
        # A route asks for this; it reads schema signatures and must not wait out a
        # seal on the maintenance thread.
        result: dict[str, Any] = await self._call("schema_status")
        self._schema = result
        return result

    async def reconcile_now(self) -> dict[str, Any]:
        """One reconciliation pass on demand (the audit tool and tests use this)."""

        if self._history is None:
            return {"scanned": 0, "skipped": 0, "errors": 0, "inserted": 0}
        return await self._reconcile_pass()

    async def _windowed(
        self,
        method: str,
        *,
        from_ts: float,
        to_ts: float,
        origin: str | None,
        filters: Filters | None,
        **extra: Any,
    ) -> dict[str, Any]:
        result: dict[str, Any] = await self._call(
            method, from_ts=from_ts, to_ts=to_ts, origin=origin, filters=filters, **extra
        )
        return result

    async def tool_summary(self, **scope: Any) -> dict[str, Any]:
        return await self._windowed("tool_summary", **scope)

    async def skill_summary(self, **scope: Any) -> dict[str, Any]:
        return await self._windowed("skill_summary", **scope)

    async def verification_summary(self, **scope: Any) -> dict[str, Any]:
        return await self._windowed("verification_summary", **scope)

    async def workload_summary(self, **scope: Any) -> dict[str, Any]:
        return await self._windowed("workload_summary", **scope)

    async def compaction_summary(self, **scope: Any) -> dict[str, Any]:
        return await self._windowed("compaction_summary", **scope)

    async def quality_summary(self, **scope: Any) -> dict[str, Any]:
        return await self._windowed("quality_summary", **scope)

    async def metric_summary(self, **scope: Any) -> dict[str, Any]:
        return await self._windowed("metric_summary", **scope)

    async def inefficiency_findings(self, **scope: Any) -> dict[str, Any]:
        return await self._windowed("inefficiency_findings", **scope)

    async def compare_cohorts(self, **scope: Any) -> dict[str, Any]:
        return await self._windowed("compare_cohorts", **scope)

    async def tool_page(self, **filters: Any) -> dict[str, Any]:
        result: dict[str, Any] = await self._call("tool_page", **filters)
        return result

    async def entity_page(self, **arguments: Any) -> dict[str, Any]:
        result: dict[str, Any] = await self._call("entity_page", **arguments)
        return result

    async def tool_audit(self, tool_call_id: str) -> dict[str, Any] | None:
        result: dict[str, Any] | None = await self._call("tool_audit", tool_call_id)
        return result

    async def run_audit(self, run_id: str) -> dict[str, Any] | None:
        result: dict[str, Any] | None = await self._call("run_audit", run_id)
        return result

    async def turn_audit(self, turn_id: str) -> dict[str, Any] | None:
        result: dict[str, Any] | None = await self._call("turn_audit", turn_id)
        return result

    async def review_finding(self, **arguments: Any) -> dict[str, Any]:
        result: dict[str, Any] = await self._call("review_finding", **arguments)
        return result

    async def shadow_comparison(self, *, from_ts: float, to_ts: float) -> dict[str, Any]:
        if self._legacy_database is None:
            return {
                "from": from_ts,
                "to": to_ts,
                "runs": 0,
                "pairs": 0,
                "classes": {},
                "examples": [],
                "interpretation": "no legacy database is attached to this daemon",
            }
        result: dict[str, Any] = await self._call(
            "shadow_comparison", self._legacy_database, from_ts=from_ts, to_ts=to_ts
        )
        return result

    async def export_page(self, **arguments: Any) -> dict[str, Any]:
        result: dict[str, Any] = await self._call("export_page", **arguments)
        return result

    async def quota_usage_evidence(
        self, backend: str, from_ts: float, to_ts: float
    ) -> dict[str, list[tuple[Any, ...]]]:
        result: dict[str, list[tuple[Any, ...]]] = await self._call(
            "quota_usage_evidence", backend, from_ts, to_ts
        )
        return result

    async def quota_usage_coverage(self, backend: str | None = None) -> float | None:
        result: float | None = await self._call("quota_usage_coverage", backend)
        return result

    async def parser_signatures(self) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = await self._call("parser_signatures")
        return result
