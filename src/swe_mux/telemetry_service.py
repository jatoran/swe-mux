"""Batched EventBus adapter that keeps canonical telemetry SQLite work off the loop.

One dedicated worker thread owns every ledger call: live ingestion, the legacy
catch-up importer, direct native reconciliation, the rollup and sealing worker, and
every query the routes make. That single writer is what lets the storage core stay
synchronous and simple, and the batching is what keeps a busy fleet from paying one
commit per observation.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from pathlib import Path
from typing import Any

from .background_tasks import background
from .event_bus import EventBus
from .models import MuxEvent
from .telemetry_ledger import CanonicalTelemetryLedger
from .telemetry_queries import Filters

CANONICAL_TELEMETRY_LOOP = "canonical-telemetry"
CANONICAL_TELEMETRY_BACKFILL_LOOP = "canonical-telemetry-backfill"
CANONICAL_TELEMETRY_ROLLUP_LOOP = "canonical-telemetry-rollup"
CANONICAL_TELEMETRY_RECONCILE_LOOP = "canonical-telemetry-reconcile"
_INGEST_BATCH_SIZE = 256
_INGEST_BATCH_SECONDS = 0.05
#: Once the legacy streams are caught up, the legacy store keeps reconciling
#: transcripts on its own schedule, so the importers are asked again this often.
_BACKFILL_RECHECK_SECONDS = 300.0
#: Native stores are re-read past their watermarks this often; an unchanged
#: conversation costs one stat.
_RECONCILE_SECONDS = 300.0
_RECONCILE_INVENTORY = 2000

#: (backend, harness version, parser version, {(event name, recognised): count})
PendingSignatures = tuple[str, str | None, str, dict[tuple[str, bool], int]]

log = logging.getLogger(__name__)


class CanonicalTelemetryService:
    """Daemon adapter around `CanonicalTelemetryLedger`."""

    def __init__(self, root: Path) -> None:
        self.ledger = CanonicalTelemetryLedger(root)
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="telemetry-ledger")
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
        self._last_sealed_period: str | None = None
        self._last_sealed_at: float | None = None
        self._reconcile_summary: dict[str, Any] = {}
        self._storage: dict[str, Any] = {
            "bytes": 0,
            "segments": 0,
            "retention": "forever",
            "automatic_deletion": False,
        }
        self._schema: dict[str, Any] = {}
        self._last_error: str | None = None
        self._last_error_at: float | None = None

    def start(
        self,
        events: EventBus,
        *,
        sessions: Any,
        legacy_database: Path | None = None,
        history: Any = None,
    ) -> None:
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

    def _record_batch(
        self,
        batch: list[tuple[MuxEvent, Mapping[str, Any]]],
        signatures: list[PendingSignatures],
    ) -> int:
        try:
            for backend, harness_version, parser_version, counts in signatures:
                self.ledger.record_parser_signatures(
                    backend=backend,
                    harness_version=harness_version,
                    parser_version=parser_version,
                    signatures=counts,
                )
            return self.ledger.record_events(batch)
        except Exception as exc:
            self._last_error = f"{type(exc).__name__}: {exc}"[:400]
            self._last_error_at = time.time()
            raise

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
        result = self.ledger.dimensions_from_session(session)
        result["run_started_at"] = session.record.agent_run_started_at or session.record.created_at
        for key in ("model", "harness_version", "turn_id", "agent_id"):
            if event.payload.get(key) is not None:
                result[key] = event.payload[key]
        return result

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

    async def _consume(self) -> None:
        assert self._queue is not None
        loop = asyncio.get_running_loop()
        while True:
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
                    inserted = await loop.run_in_executor(
                        self._executor, self._record_batch, batch, signatures
                    )
                    self._accepted += inserted
            except Exception as exc:  # pragma: no cover - iteration normally absorbs this
                self._last_error = f"{type(exc).__name__}: {exc}"[:400]
                self._last_error_at = time.time()
                log.exception("canonical telemetry batch failed")
            finally:
                for _event in events:
                    self._queue.task_done()
            if stop_after_batch:
                await self._flush_signatures(loop)
                return

    async def _flush_signatures(self, loop: asyncio.AbstractEventLoop) -> None:
        signatures = self._pending_signatures
        self._pending_signatures = []
        if signatures:
            await loop.run_in_executor(self._executor, self._record_batch, [], signatures)

    async def _backfill(self) -> None:
        assert self._legacy_database is not None
        await asyncio.sleep(10)
        loop = asyncio.get_running_loop()
        importers = (
            ("history", self.ledger.import_legacy_runs_batch),
            ("status_turns", self.ledger.import_legacy_turns_batch),
            ("compactions", self.ledger.import_legacy_compactions_batch),
            ("tier0_tests", self.ledger.import_legacy_verifications_batch),
            ("tool_events", self.ledger.import_legacy_batch),
        )
        stream_index = 0
        while True:
            stream, importer = importers[stream_index]
            self._backfill_stream = stream
            with background.iteration(CANONICAL_TELEMETRY_BACKFILL_LOOP):
                result = await loop.run_in_executor(
                    self._executor,
                    partial(
                        importer,
                        self._legacy_database,
                        batch_size=250,
                    ),
                )
                self._backfilled += int(result["imported"])
                if result["completed"]:
                    stream_index += 1
            if stream_index < len(importers):
                await asyncio.sleep(0.25)
                continue
            # Every stream is caught up. The legacy store keeps reconciling
            # transcripts, so come back later and import whatever it appended.
            self._backfill_completed = True
            self._backfill_passes += 1
            self._backfill_last_pass_at = time.time()
            stream_index = 0
            await asyncio.sleep(_BACKFILL_RECHECK_SECONDS)

    async def _reconcile(self) -> None:
        """Read each native store past its watermark straight into the reducer."""

        await asyncio.sleep(60)
        loop = asyncio.get_running_loop()
        while True:
            with background.iteration(CANONICAL_TELEMETRY_RECONCILE_LOOP):
                rows = await self._history.telemetry_history_rows(_RECONCILE_INVENTORY)
                summary = await loop.run_in_executor(
                    self._executor, self.ledger.reconcile_native_rows, rows
                )
                self._reconcile_summary = {**summary, "at": time.time(), "inventory": len(rows)}
            await asyncio.sleep(_RECONCILE_SECONDS)

    async def _rollup(self) -> None:
        await asyncio.sleep(30)
        loop = asyncio.get_running_loop()
        with background.iteration(CANONICAL_TELEMETRY_ROLLUP_LOOP):
            self._schema = await loop.run_in_executor(self._executor, self.ledger.schema_status)
            if self._schema.get("drift"):
                log.warning(
                    "canonical telemetry schema drift in %s", ", ".join(self._schema["drift"])
                )
        while True:
            rebuilt: str | None = None
            with background.iteration(CANONICAL_TELEMETRY_ROLLUP_LOOP):
                # Rollups wait for the first catch-up pass so a day is not rebuilt
                # once per import batch; storage is reported regardless.
                if self._backfill_completed:
                    rebuilt = await loop.run_in_executor(
                        self._executor, self.ledger.rebuild_next_closed_day
                    )
                    if rebuilt is None:
                        rebuilt = await loop.run_in_executor(
                            self._executor, self.ledger.rebuild_next_closed_hour
                        )
                    if rebuilt is None:
                        sealed = await loop.run_in_executor(
                            self._executor, self.ledger.seal_next_segment
                        )
                        if sealed is not None:
                            self._last_sealed_period = str(sealed["period"])
                            self._last_sealed_at = float(sealed["sealed_at"])
                self._storage = await loop.run_in_executor(
                    self._executor, self.ledger.storage_status
                )
            if rebuilt is not None:
                await asyncio.sleep(0.25)
            elif not self._backfill_completed:
                await asyncio.sleep(5)
            else:
                await asyncio.sleep(60)

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
        await asyncio.to_thread(self._executor.shutdown, wait=True)

    def close(self) -> None:
        self.ledger.close()

    def health(self) -> dict[str, Any]:
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
            "storage": dict(self._storage),
            "schema": {
                key: self._schema[key]
                for key in ("version", "drift", "migrations")
                if key in self._schema
            },
            "queue_depth": self._queue.qsize() if self._queue is not None else 0,
            "running": bool(self._task and not self._task.done()),
            "last_error": self._last_error,
            "last_error_at": self._last_error_at,
        }

    async def _call(self, function: Any, /, *args: Any, **kwargs: Any) -> Any:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._executor, partial(function, *args, **kwargs))

    async def schema_status(self) -> dict[str, Any]:
        result: dict[str, Any] = await self._call(self.ledger.schema_status)
        self._schema = result
        return result

    async def reconcile_now(self) -> dict[str, Any]:
        """One reconciliation pass on demand (the audit tool and tests use this)."""

        if self._history is None:
            return {"scanned": 0, "skipped": 0, "errors": 0, "inserted": 0}
        rows = await self._history.telemetry_history_rows(_RECONCILE_INVENTORY)
        summary: dict[str, Any] = await self._call(self.ledger.reconcile_native_rows, rows)
        self._reconcile_summary = {**summary, "at": time.time(), "inventory": len(rows)}
        return self._reconcile_summary

    async def _windowed(
        self,
        function: Any,
        *,
        from_ts: float,
        to_ts: float,
        origin: str | None,
        filters: Filters | None,
        **extra: Any,
    ) -> dict[str, Any]:
        result: dict[str, Any] = await self._call(
            function, from_ts=from_ts, to_ts=to_ts, origin=origin, filters=filters, **extra
        )
        return result

    async def tool_summary(self, **scope: Any) -> dict[str, Any]:
        return await self._windowed(self.ledger.tool_summary, **scope)

    async def skill_summary(self, **scope: Any) -> dict[str, Any]:
        return await self._windowed(self.ledger.skill_summary, **scope)

    async def verification_summary(self, **scope: Any) -> dict[str, Any]:
        return await self._windowed(self.ledger.verification_summary, **scope)

    async def workload_summary(self, **scope: Any) -> dict[str, Any]:
        return await self._windowed(self.ledger.workload_summary, **scope)

    async def compaction_summary(self, **scope: Any) -> dict[str, Any]:
        return await self._windowed(self.ledger.compaction_summary, **scope)

    async def quality_summary(self, **scope: Any) -> dict[str, Any]:
        return await self._windowed(self.ledger.quality_summary, **scope)

    async def metric_summary(self, **scope: Any) -> dict[str, Any]:
        return await self._windowed(self.ledger.metric_summary, **scope)

    async def inefficiency_findings(self, **scope: Any) -> dict[str, Any]:
        return await self._windowed(self.ledger.inefficiency_findings, **scope)

    async def compare_cohorts(self, **scope: Any) -> dict[str, Any]:
        return await self._windowed(self.ledger.compare_cohorts, **scope)

    async def tool_page(self, **filters: Any) -> dict[str, Any]:
        result: dict[str, Any] = await self._call(self.ledger.tool_page, **filters)
        return result

    async def entity_page(self, **arguments: Any) -> dict[str, Any]:
        result: dict[str, Any] = await self._call(self.ledger.entity_page, **arguments)
        return result

    async def tool_audit(self, tool_call_id: str) -> dict[str, Any] | None:
        result: dict[str, Any] | None = await self._call(self.ledger.tool_audit, tool_call_id)
        return result

    async def run_audit(self, run_id: str) -> dict[str, Any] | None:
        result: dict[str, Any] | None = await self._call(self.ledger.run_audit, run_id)
        return result

    async def turn_audit(self, turn_id: str) -> dict[str, Any] | None:
        result: dict[str, Any] | None = await self._call(self.ledger.turn_audit, turn_id)
        return result

    async def review_finding(self, **arguments: Any) -> dict[str, Any]:
        result: dict[str, Any] = await self._call(self.ledger.review_finding, **arguments)
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
            self.ledger.shadow_comparison, self._legacy_database, from_ts=from_ts, to_ts=to_ts
        )
        return result

    async def export_page(self, **arguments: Any) -> dict[str, Any]:
        result: dict[str, Any] = await self._call(self.ledger.export_page, **arguments)
        return result

    async def parser_signatures(self) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = await self._call(self.ledger.parser_signatures)
        return result
