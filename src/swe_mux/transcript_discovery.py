"""The native-conversation discovery walk, in the worker process when there is one.

`reconcile.scan_external_transcripts` walks every harness's conversation tree,
stats every file and decodes the head of the newest few thousand: 9.8 s of CPU,
all of it holding the GIL, at every daemon start on the development host, and
again on every "scan now", project backfill and onboarding preview
(`development/archive/DAEMON_LOAD_ISOLATION.md` item 22). It is bulk, non-interactive and
returns plain records, which is what the worker is for.

`TranscriptScanner` is the daemon's side: one `scan` with the same arguments and
result as `scan_external_transcripts_async`, answered by the worker's
`transcripts` service when a worker is ready and by the in-process walk when it
is not or when the call fails - the same function either way, so the two cannot
disagree. Progress crosses as notices and cancellation as a request, so a caller
that reports progress or stops a scan sees no difference.

The worker holds nothing between calls: a restart loses only the scans in flight,
and each of those falls back to the in-process walk.
"""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import json
import logging
import threading
import time
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .framed_ipc import RemoteError, RequestRefused
from .harness import require_backend
from .reconcile import (
    ExternalTranscript,
    scan_external_transcripts,
    scan_external_transcripts_async,
)

if TYPE_CHECKING:
    from .worker import Reply, Worker

log = logging.getLogger(__name__)

TRANSCRIPT_SERVICE = "transcripts"
#: A whole-tree walk on a cold, loaded host. Past it the daemon stops waiting, asks
#: the worker to stop, and walks in process instead.
SCAN_TIMEOUT_SECONDS = 600.0
#: How often a scan in flight checks its caller's cancellation predicate.
CANCEL_POLL_SECONDS = 0.2
#: Progress notices are sent at most this often per scan.
PROGRESS_INTERVAL_SECONDS = 0.25


# --- the wire ---------------------------------------------------------------------


def encode_transcripts(found: Iterable[ExternalTranscript]) -> bytes:
    return json.dumps(
        [
            [
                item.backend,
                item.native_id,
                item.cwd,
                item.created_at,
                str(item.path) if item.path is not None else None,
                item.mtime_ns,
                item.size,
            ]
            for item in found
        ],
        separators=(",", ":"),
    ).encode("utf-8")


def decode_transcripts(payload: bytes) -> list[ExternalTranscript]:
    return [
        ExternalTranscript(
            require_backend(str(backend)),
            str(native_id),
            str(cwd),
            float(created_at),
            Path(path) if path is not None else None,
            int(mtime_ns),
            int(size),
        )
        for backend, native_id, cwd, created_at, path, mtime_ns, size in json.loads(
            payload or b"[]"
        )
    ]


def _scan_arguments(
    home: Path | None,
    limit: int | None,
    roots: Iterable[str | Path] | None,
    backends: Iterable[str] | None,
) -> dict[str, Any]:
    return {
        "home": str(home) if home is not None else None,
        "limit": limit,
        "roots": [str(root) for root in roots] if roots is not None else None,
        "backends": sorted(backends) if backends is not None else None,
    }


# --- the worker's service ---------------------------------------------------------


class TranscriptDiscoveryService:
    """The worker's `transcripts` service: `scan` and `cancel`."""

    name = TRANSCRIPT_SERVICE

    def __init__(self) -> None:
        self._worker: Worker | None = None
        self._cancelled: dict[str, threading.Event] = {}

    async def start(self, worker: Worker) -> None:
        self._worker = worker

    async def stop(self) -> None:
        for event in self._cancelled.values():
            event.set()
        self._worker = None

    async def handle(self, method: str, header: dict[str, Any], payload: bytes) -> Reply:
        if method == "scan":
            return await self._scan(header)
        if method == "cancel":
            event = self._cancelled.get(str(header.get("scan_id") or ""))
            if event is not None:
                event.set()
            return {"cancelled": event is not None}
        raise RequestRefused(f"unknown method {self.name}.{method}")

    async def _scan(self, header: dict[str, Any]) -> Reply:
        scan_id = str(header.get("scan_id") or "")
        if not scan_id:
            raise RequestRefused("scan needs a `scan_id`")
        home = header.get("home")
        roots = header.get("roots")
        backends = header.get("backends")
        limit = header.get("limit")
        cancelled = threading.Event()
        self._cancelled[scan_id] = cancelled
        loop = asyncio.get_running_loop()
        last_sent = 0.0

        def progress(scanned: int) -> None:
            nonlocal last_sent
            now = time.monotonic()
            if now - last_sent < PROGRESS_INTERVAL_SECONDS:
                return
            last_sent = now
            loop.call_soon_threadsafe(self._notify_progress, scan_id, scanned)

        started = time.perf_counter()
        try:
            found = await asyncio.to_thread(
                scan_external_transcripts,
                Path(home) if isinstance(home, str) and home else None,
                limit=int(limit) if isinstance(limit, int) else None,
                roots=[str(root) for root in roots] if isinstance(roots, list) else None,
                backends=[str(name) for name in backends] if isinstance(backends, list) else None,
                should_cancel=cancelled.is_set,
                on_progress=progress,
            )
            body = await asyncio.to_thread(encode_transcripts, found)
        finally:
            self._cancelled.pop(scan_id, None)
        elapsed = time.perf_counter() - started
        log.info(
            "transcript discovery scan %s found=%d cancelled=%s elapsed_s=%.2f",
            scan_id,
            len(found),
            cancelled.is_set(),
            elapsed,
        )
        return {"found": len(found), "cancelled": cancelled.is_set()}, body

    def _notify_progress(self, scan_id: str, scanned: int) -> None:
        if self._worker is not None:
            self._worker.notify(self.name, "progress", {"scan_id": scan_id, "scanned": scanned})


# --- the daemon's side ------------------------------------------------------------


class TranscriptScanner:
    """`scan_external_transcripts_async`, in the worker when one is ready."""

    def __init__(self) -> None:
        self._worker: Any = None
        self._progress: dict[str, Callable[[int], None]] = {}
        self._ids = itertools.count(1)
        self.remote_scans = 0
        self.local_scans = 0
        self.remote_failures = 0
        self.last_failure = ""
        self.last_remote_seconds: float | None = None

    def attach_worker(self, worker: Any) -> None:
        """Send scans to `worker` (a `WorkerHost`) whenever it is ready."""
        self._worker = worker
        worker.on_notice(TRANSCRIPT_SERVICE, self._on_notice)

    async def wait_for_worker(self, seconds: float) -> bool:
        """Wait up to `seconds` for a worker to take scans; whether one is ready."""
        worker = self._worker
        if worker is None:
            return False
        return bool(await worker.wait_ready(seconds))

    def stats(self) -> dict[str, Any]:
        return {
            "worker_attached": self._worker is not None,
            "remote_scans": self.remote_scans,
            "local_scans": self.local_scans,
            "remote_failures": self.remote_failures,
            "last_failure": self.last_failure,
            "last_remote_seconds": self.last_remote_seconds,
        }

    async def scan(
        self,
        home: Path | None = None,
        *,
        limit: int | None = 2000,
        roots: Iterable[str | Path] | None = None,
        backends: Iterable[str] | None = None,
        should_cancel: Callable[[], bool] | None = None,
        on_progress: Callable[[int], None] | None = None,
    ) -> list[ExternalTranscript]:
        worker = self._worker
        if worker is not None and worker.ready:
            try:
                return await self._remote(
                    worker,
                    _scan_arguments(home, limit, roots, backends),
                    should_cancel=should_cancel,
                    on_progress=on_progress,
                )
            except Exception as exc:
                if not _falls_back(exc):
                    raise
                self._note_failure(exc)
        self.local_scans += 1
        return await scan_external_transcripts_async(
            home,
            limit=limit,
            roots=roots,
            backends=backends,
            should_cancel=should_cancel,
            on_progress=on_progress,
        )

    def _note_failure(self, exc: BaseException) -> None:
        self.remote_failures += 1
        self.last_failure = f"{type(exc).__name__}: {exc}"[:400]
        log.warning("transcript discovery in the worker failed; walking here: %s", exc)

    async def _remote(
        self,
        worker: Any,
        arguments: dict[str, Any],
        *,
        should_cancel: Callable[[], bool] | None,
        on_progress: Callable[[int], None] | None,
    ) -> list[ExternalTranscript]:
        scan_id = f"scan-{next(self._ids)}"
        if on_progress is not None:
            self._progress[scan_id] = on_progress
        started = time.perf_counter()
        call = asyncio.ensure_future(
            worker.call(
                TRANSCRIPT_SERVICE,
                "scan",
                {**arguments, "scan_id": scan_id},
                timeout_seconds=SCAN_TIMEOUT_SECONDS,
            )
        )
        cancel_sent = False
        try:
            while not call.done():
                await asyncio.wait({call}, timeout=CANCEL_POLL_SECONDS)
                if not cancel_sent and should_cancel is not None and should_cancel():
                    cancel_sent = True
                    await self._cancel(worker, scan_id)
            _reply, body = call.result()
        except asyncio.CancelledError:
            # The worker keeps walking until it is told; tell it, without waiting.
            call.cancel()
            asyncio.get_running_loop().create_task(self._cancel(worker, scan_id))
            raise
        except TimeoutError:
            await self._cancel(worker, scan_id)
            raise
        finally:
            self._progress.pop(scan_id, None)
        found = await asyncio.to_thread(decode_transcripts, body)
        self.remote_scans += 1
        self.last_remote_seconds = round(time.perf_counter() - started, 3)
        return found

    async def _cancel(self, worker: Any, scan_id: str) -> None:
        with contextlib.suppress(Exception):
            await worker.call(
                TRANSCRIPT_SERVICE, "cancel", {"scan_id": scan_id}, timeout_seconds=5.0
            )

    def _on_notice(self, method: str, header: dict[str, Any], payload: bytes) -> None:
        if method != "progress":
            return
        callback = self._progress.get(str(header.get("scan_id") or ""))
        scanned = header.get("scanned")
        if callback is not None and isinstance(scanned, int):
            callback(scanned)


def _falls_back(exc: BaseException) -> bool:
    """Whether a remote scan's failure means "walk here instead" rather than a bug.

    Imported late so the worker, which runs this module's service, does not load
    the daemon's worker host.
    """
    from .worker_host import WorkerUnavailable

    return isinstance(exc, WorkerUnavailable | RemoteError | OSError)


__all__ = [
    "TRANSCRIPT_SERVICE",
    "TranscriptDiscoveryService",
    "TranscriptScanner",
    "decode_transcripts",
    "encode_transcripts",
]
