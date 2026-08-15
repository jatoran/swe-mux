from __future__ import annotations

import asyncio
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .config import Config
from .harness import enabled_backends
from .history import HistoryIndex
from .reconcile import ScanProgress, reconcile_external_history


@dataclass
class ScanJob:
    """State of the single user-triggered native-history scan.

    ``status`` moves ``idle`` -> ``running`` -> one of ``completed`` /
    ``cancelled`` / ``failed``. ``backends`` records which harnesses this run was
    scoped to, so the UI can name them honestly.
    """

    status: str = "idle"
    phase: str = "scanning"
    backends: list[str] = field(default_factory=list)
    scanned: int = 0
    processed: int = 0
    imported: int = 0
    started_at: float | None = None
    completed_at: float | None = None
    error: str | None = None
    cancel_requested: bool = False

    def payload(self) -> dict[str, Any]:
        return asdict(self)


class HistoryScanManager:
    """Runs one explicit, cancellable native-history reconcile at a time.

    The startup reconcile is silent and bounded; this is the user-triggered version
    for a first import, which a real machine (tens of thousands of transcripts) can
    make expensive, so it reports progress and can be cancelled. Only one runs at a
    time, and it is scoped to the harnesses the user has enabled - the same scope the
    startup scan uses.
    """

    def __init__(
        self, history: HistoryIndex, config: Config, *, home: Path | None = None
    ) -> None:
        self.history = history
        self.config = config
        # Production scans the real home; tests inject one so the scan is deterministic.
        self.home = home
        self.job = ScanJob()
        self._task: asyncio.Task[None] | None = None

    def status(self) -> dict[str, Any]:
        return self.job.payload()

    def start(self) -> dict[str, Any]:
        if self.job.status == "running":
            return self.job.payload()
        backends = enabled_backends(
            dict(self.config.harness_enabled), dict(self.config.harness_exe)
        )
        self.job = ScanJob(status="running", backends=list(backends), started_at=time.time())
        self._task = asyncio.create_task(self._run(self.job, backends), name="history-scan")
        return self.job.payload()

    def cancel(self) -> dict[str, Any]:
        if self.job.status == "running":
            self.job.cancel_requested = True
            self.job.phase = "cancelling"
        return self.job.payload()

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

    async def _run(self, job: ScanJob, backends: tuple[str, ...]) -> None:
        def on_progress(progress: ScanProgress) -> None:
            # A cancel request already moved the phase; leave that label in place.
            if job.phase != "cancelling":
                job.phase = progress.phase
            job.scanned = progress.scanned
            job.processed = progress.processed
            job.imported = progress.imported

        try:
            await reconcile_external_history(
                self.history,
                self.home,
                backends=backends,
                should_cancel=lambda: job.cancel_requested,
                on_progress=on_progress,
            )
            job.status = "cancelled" if job.cancel_requested else "completed"
            job.phase = job.status
        except asyncio.CancelledError:
            job.status = "cancelled"
            job.phase = "cancelled"
            raise
        except Exception as exc:  # surfaced through the status endpoint
            job.status = "failed"
            job.phase = "failed"
            job.error = str(exc)
        finally:
            job.completed_at = time.time()
