"""The native-conversation discovery walk in the worker: `DAEMON_LOAD_ISOLATION.md` item 22.

The walk moved for its cost, not its behaviour, so the central test is that a scan
answered by a real worker (`python -m swe_mux.worker`) returns exactly the records
a scan in this process returns over the same home - every harness's layout, with
and without a scope. The rest pins the plumbing a caller relies on: progress
arrives as it did, a cancelled caller stops the worker walking, and anything that
goes wrong with the worker walks here instead.
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from swe_mux import transcript_discovery
from swe_mux.framed_ipc import RemoteError
from swe_mux.reconcile import ExternalTranscript, scan_external_transcripts_async
from swe_mux.transcript_discovery import (
    TRANSCRIPT_SERVICE,
    TranscriptDiscoveryService,
    TranscriptScanner,
    decode_transcripts,
    encode_transcripts,
)
from swe_mux.worker_host import WorkerHost
from tests.support.settle import until
from tests.test_conversation_discovery import (
    CWD,
    _write_claude,
    _write_codex,
    _write_opencode,
    _write_pi_dialect,
)

READY_SECONDS = 60.0


@dataclass
class FakeConfig:
    data_dir: Path
    worker_process_enabled: bool = True
    log_level: str = "INFO"
    gil_switch_interval_ms: float = 5.0


@contextlib.asynccontextmanager
async def hosting(data_dir: Path) -> AsyncIterator[WorkerHost]:
    host = WorkerHost(FakeConfig(data_dir), restart_initial=0.1)
    await host.start()
    try:
        assert await host.wait_ready(READY_SECONDS), host.health()
        yield host
    finally:
        await asyncio.wait_for(host.stop(), timeout=30)


@pytest.fixture
def home(tmp_path: Path) -> Path:
    """A home holding conversations for every registered harness, some of them twice."""
    root = tmp_path / "home"
    for index in range(3):
        _write_claude(root, f"claude-{index}", CWD)
        _write_codex(root, f"codex-{index}", CWD)
    _write_pi_dialect(root / ".omp" / "agent", "omp-1", CWD)
    _write_pi_dialect(root / ".pi" / "agent", "pi-1", CWD)
    _write_opencode(root, "ses_one", CWD)
    return root


def ordered(found: list[ExternalTranscript]) -> list[ExternalTranscript]:
    return sorted(found, key=lambda item: (item.backend, item.native_id))


# --- the wire ---------------------------------------------------------------------


def test_the_wire_keeps_every_field_of_a_record() -> None:
    records = [
        ExternalTranscript(
            "claude",
            "id-1",
            "D:\\PROJECTS\\demo",
            1_788_000_000.123456,
            Path("C:/x/y.jsonl"),
            17,
            42,
        ),
        ExternalTranscript("opencode", "ses_1", "/home/u/demo", 0.0, None, 1_700_000_001_000, 0),
    ]
    assert decode_transcripts(encode_transcripts(records)) == records
    assert decode_transcripts(b"") == []


# --- a real worker ----------------------------------------------------------------


async def test_a_scan_in_the_worker_finds_exactly_what_a_scan_here_finds(
    tmp_path: Path, home: Path
) -> None:
    here = await scan_external_transcripts_async(home, limit=None)
    scoped_here = await scan_external_transcripts_async(
        home, limit=None, roots=[CWD], backends=["claude", "opencode"]
    )
    assert {item.backend for item in here} == {"claude", "codex", "omp", "pi", "opencode"}

    async with hosting(tmp_path / "worker-data") as host:
        scanner = TranscriptScanner()
        scanner.attach_worker(host)
        progress: list[int] = []
        remote = await scanner.scan(home, limit=None, on_progress=progress.append)
        scoped_remote = await scanner.scan(
            home, limit=None, roots=[CWD], backends=["claude", "opencode"]
        )
        stats = scanner.stats()

    assert ordered(remote) == ordered(here)
    assert ordered(scoped_remote) == ordered(scoped_here)
    assert stats["remote_scans"] == 2 and stats["local_scans"] == 0, stats
    assert stats["remote_failures"] == 0 and stats["last_remote_seconds"] is not None


# --- no worker --------------------------------------------------------------------


class AbsentWorker:
    ready = False

    def on_notice(self, service: str, handler: Any) -> None:
        return None

    async def wait_ready(self, seconds: float) -> bool:
        return False

    async def call(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("nothing calls a worker that is not ready")


class FailingWorker(AbsentWorker):
    ready = True

    async def call(self, *args: Any, **kwargs: Any) -> Any:
        raise RemoteError("transcripts.scan exploded", {"ok": False})


@pytest.mark.parametrize("worker", [None, AbsentWorker(), FailingWorker()])
async def test_with_no_usable_worker_the_walk_runs_here(home: Path, worker: Any) -> None:
    scanner = TranscriptScanner()
    if worker is not None:
        scanner.attach_worker(worker)
    found = await scanner.scan(home, limit=None)
    assert ordered(found) == ordered(await scan_external_transcripts_async(home, limit=None))
    stats = scanner.stats()
    assert stats["local_scans"] == 1 and stats["remote_scans"] == 0
    assert stats["remote_failures"] == (1 if isinstance(worker, FailingWorker) else 0)
    assert await scanner.wait_for_worker(0.01) is False


# --- progress and cancellation ----------------------------------------------------


class RecordingWorker:
    """The worker side's `notify`, collected."""

    def __init__(self) -> None:
        self.notices: list[tuple[str, str, dict[str, Any]]] = []

    def notify(
        self, service: str, method: str, fields: dict[str, Any], payload: bytes = b""
    ) -> None:
        self.notices.append((service, method, dict(fields)))


async def test_the_service_walks_until_told_to_stop_and_reports_progress_on_the_way(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(transcript_discovery, "PROGRESS_INTERVAL_SECONDS", 0.0)
    started = threading.Event()

    def slow_walk(home: Any, **kwargs: Any) -> list[ExternalTranscript]:
        count = 0
        while not kwargs["should_cancel"]():
            count += 1
            kwargs["on_progress"](count)
            started.set()
            threading.Event().wait(0.01)
        return [ExternalTranscript("claude", "partial", CWD, 0.0, None)]

    monkeypatch.setattr(transcript_discovery, "scan_external_transcripts", slow_walk)
    service = TranscriptDiscoveryService()
    recorder = RecordingWorker()
    await service.start(recorder)  # type: ignore[arg-type]
    scan = asyncio.ensure_future(service.handle("scan", {"scan_id": "s1"}, b""))
    await until(started.is_set, what="the walk started")
    await until(lambda: len(recorder.notices) >= 2, what="progress notices")
    assert await service.handle("cancel", {"scan_id": "s1"}, b"") == {"cancelled": True}
    reply = await asyncio.wait_for(scan, timeout=10)
    assert isinstance(reply, tuple)
    header, body = reply
    assert header == {"found": 1, "cancelled": True}
    assert [item.native_id for item in decode_transcripts(body)] == ["partial"]
    assert all(
        notice[:2] == (TRANSCRIPT_SERVICE, "progress") and notice[2]["scan_id"] == "s1"
        for notice in recorder.notices
    )
    assert await service.handle("cancel", {"scan_id": "s1"}, b"") == {"cancelled": False}


class ScriptedWorker:
    """A host whose scan runs until it is cancelled, sending progress meanwhile."""

    ready = True

    def __init__(self) -> None:
        self.handler: Any = None
        self.cancelled = asyncio.Event()
        self.calls: list[str] = []

    def on_notice(self, service: str, handler: Any) -> None:
        self.handler = handler

    async def wait_ready(self, seconds: float) -> bool:
        return True

    async def call(
        self, service: str, method: str, fields: dict[str, Any], *args: Any, **kwargs: Any
    ) -> tuple[dict[str, Any], bytes]:
        self.calls.append(method)
        if method == "cancel":
            self.cancelled.set()
            return {"cancelled": True}, b""
        self.handler("progress", {"scan_id": fields["scan_id"], "scanned": 7}, b"")
        await self.cancelled.wait()
        partial = [ExternalTranscript("codex", "partial", CWD, 0.0, None)]
        return {"found": 1, "cancelled": True}, encode_transcripts(partial)


async def test_a_caller_that_cancels_stops_the_worker_and_sees_its_progress() -> None:
    worker = ScriptedWorker()
    scanner = TranscriptScanner()
    scanner.attach_worker(worker)
    progress: list[int] = []
    stop = {"now": False}

    async def cancel_soon() -> None:
        await until(lambda: progress == [7], what="progress forwarded")
        stop["now"] = True

    canceller = asyncio.ensure_future(cancel_soon())
    found = await asyncio.wait_for(
        scanner.scan(should_cancel=lambda: stop["now"], on_progress=progress.append), timeout=10
    )
    await canceller
    assert [item.native_id for item in found] == ["partial"]
    assert worker.calls == ["scan", "cancel"]


async def test_a_cancelled_task_still_tells_the_worker_to_stop() -> None:
    worker = ScriptedWorker()
    scanner = TranscriptScanner()
    scanner.attach_worker(worker)
    task = asyncio.ensure_future(scanner.scan())
    await until(lambda: worker.calls == ["scan"], what="the scan reached the worker")
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await until(worker.cancelled.is_set, what="the worker was told to stop")
