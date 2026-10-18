"""The canonical telemetry ledgers in the worker process: `DAEMON_LOAD_ISOLATION.md` item 21.

The daemon's `CanonicalTelemetryService` keeps the bus subscription, the batching
and the loops; this service holds the `TelemetryLedgers` they drive, so the
reducer, every SQLite write, the maintenance passes and the queries run on the
worker's GIL. It is the same `TelemetryLedgers` the daemon runs when the worker
is off: one implementation, two processes that can host it.

**Opened, never assumed open.** A restarted worker holds nothing, so every call
but `open` is refused until the daemon has opened the ledgers in this worker,
and the daemon reopens them whenever the worker's generation changes. The
ledgers' durable state is their own files, which is why a restart loses nothing
the daemon cannot resend: a batch it did not see acknowledged is sent again,
and recording it twice records nothing twice.

**This process writes the telemetry root and nothing else.** `mux.db` is read
here (the legacy importers open it read-only) and written only by the daemon.

**OTLP arrives unparsed.** The body is parsed and reduced here, not on the
daemon's loop, and recorded as one batch with its parser signatures; the reply
says how many events it reduced to and how many were new.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .framed_ipc import RequestRefused
from .telemetry_service import (
    MAINTENANCE_METHODS,
    QUERY_METHODS,
    TELEMETRY_SERVICE,
    Batch,
    PendingSignatures,
    TelemetryLedgers,
    apply_event_dimensions,
    decode_batch,
    decode_call,
    encode_result,
)

if TYPE_CHECKING:
    from .worker import Reply, Worker

log = logging.getLogger(__name__)


class TelemetryWorkerService:
    """The worker's `telemetry` service: open, record, maintain, query, otlp, close."""

    name = TELEMETRY_SERVICE

    def __init__(self) -> None:
        self.ledgers: TelemetryLedgers | None = None
        self.root: Path | None = None
        self._open_lock = asyncio.Lock()

    async def start(self, worker: Worker) -> None:
        return None

    async def stop(self) -> None:
        await self._close()

    async def handle(self, method: str, header: dict[str, Any], payload: bytes) -> Reply:
        if method == "open":
            return await self._open(header)
        if method == "close":
            await self._close()
            return {}
        ledgers = self.ledgers
        if ledgers is None:
            raise RequestRefused("the telemetry ledgers have not been opened in this worker")
        if method == "record":
            batch, signatures = await asyncio.to_thread(decode_batch, payload)
            inserted = await ledgers.record(batch, signatures)
            return {"inserted": inserted, "counters": ledgers.counters()}
        if method in {"maintain", "query"}:
            return await self._method(ledgers, method, header, payload)
        if method == "otlp":
            return await self._otlp(ledgers, header, payload)
        raise RequestRefused(f"unknown method {self.name}.{method}")

    async def _open(self, header: dict[str, Any]) -> Reply:
        root = header.get("root")
        if not isinstance(root, str) or not root:
            raise RequestRefused("open needs the telemetry `root`")
        path = Path(root)
        async with self._open_lock:
            if self.ledgers is not None and self.root == path:
                return {"counters": self.ledgers.counters()}
            await self._close()
            # Opening migrates every file it touches, which can take a while on an
            # old data directory; the loop keeps answering meanwhile.
            self.ledgers = await asyncio.to_thread(TelemetryLedgers, path)
            self.root = path
        log.info("canonical telemetry ledgers open at %s", path)
        return {"counters": self.ledgers.counters()}

    async def _close(self) -> None:
        ledgers, self.ledgers = self.ledgers, None
        self.root = None
        if ledgers is None:
            return
        await ledgers.shutdown()
        ledgers.close()
        log.info("canonical telemetry ledgers closed")

    async def _method(
        self, ledgers: TelemetryLedgers, kind: str, header: dict[str, Any], payload: bytes
    ) -> Reply:
        name = str(header.get("method") or "")
        allowed = MAINTENANCE_METHODS if kind == "maintain" else QUERY_METHODS
        if name not in allowed:
            raise RequestRefused(f"not a {kind} method: {name!r}")
        args, kwargs = await asyncio.to_thread(decode_call, payload, name)
        call = ledgers.maintain if kind == "maintain" else ledgers.query
        try:
            result = await call(name, *args, **kwargs)
        except ValueError as exc:
            # The daemon raises it again as the same ValueError, which is the 400
            # every telemetry route answers a bad argument with.
            return {"value_error": str(exc), "counters": ledgers.counters()}
        body = await asyncio.to_thread(encode_result, result)
        return {"counters": ledgers.counters()}, body

    async def _otlp(
        self, ledgers: TelemetryLedgers, header: dict[str, Any], payload: bytes
    ) -> Reply:
        from .telemetry_otlp import PARSER_VERSION, otlp_reduction

        session_id = str(header.get("session_id") or "")
        backend = str(header.get("backend") or "unknown")
        base = header.get("dims")
        dimensions = base if isinstance(base, dict) else {}

        def reduce() -> Any:
            return otlp_reduction(json.loads(payload), session_id=session_id, backend=backend)

        try:
            reduction = await asyncio.to_thread(reduce)
        except ValueError as exc:
            # json.JSONDecodeError is a ValueError; both are the caller's 400.
            return {"bad_request": str(exc), "counters": ledgers.counters()}
        batch: Batch = [
            (event, apply_event_dimensions(dimensions, event)) for event in reduction.events
        ]
        signatures: list[PendingSignatures] = []
        if reduction.signatures:
            signatures.append(
                (backend, reduction.harness_version, PARSER_VERSION, dict(reduction.signatures))
            )
        inserted = await ledgers.record(batch, signatures)
        return {
            "events": len(reduction.events),
            "inserted": inserted,
            "harness_version": reduction.harness_version,
            "counters": ledgers.counters(),
        }


__all__ = ["TelemetryWorkerService"]
