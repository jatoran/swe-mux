"""Measure what canonical telemetry ingestion costs the daemon's event loop.

The ledger's contract is that capture never runs on the loop that serves PTYs and
HTTP: every SQLite call goes through the service's worker threads (ingestion has
its own), and the loop only batches queue items. This script proves it with
numbers rather than by reading the code: it runs the real service on an event
loop, drives observations at it, and samples loop lag with a 5 ms ticker the
whole time - the same kind of ticker the daemon's stall watchdog uses. It prints
the worst and the p99 lag alongside the number of observations accepted and
dropped, and the CPU this process spent.

    uv run python tools/telemetry_ingest_latency.py --events 50000
    uv run python tools/telemetry_ingest_latency.py --events 50000 --rate 2000
    uv run python tools/telemetry_ingest_latency.py --events 50000 --rate 2000 --control
    uv run python tools/telemetry_ingest_latency.py --events 50000 --rate 2000 --worker

`--rate` paces the producer at that many observations per second; without it the
producer floods the bus as fast as the loop can construct events, which measures
the producer at least as much as the consumer. `--control` runs the identical
producer with no ledger subscribed, so the lag ingestion itself adds is the
difference between a paced run and its control. A bus queue that fills drops on
the floor by design; every drop is counted and the count is printed.

`--worker` runs the ledgers where the daemon runs them by default: in a real
worker process (`DAEMON_LOAD_ISOLATION.md` item 21), spawned under a scratch data
directory exactly as the daemon spawns its own. Then `cpu_seconds` is what this
process - the daemon's stand-in - still spends on ingestion, and
`worker_cpu_seconds` is what moved out of it; against `--control` the first is
the producer's own cost plus batching and the wire.
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import psutil

from swe_mux.event_bus import EventBus
from swe_mux.telemetry_service import CanonicalTelemetryService
from swe_mux.worker_host import WorkerHost


async def measure(
    events_to_send: int, tick_ms: float, *, rate: float, control: bool, worker: bool
) -> dict[str, float | int | str]:
    root = Path(tempfile.mkdtemp(prefix="swe-mux-ingest-latency-"))
    record = SimpleNamespace(
        id="session-1",
        agent_run_id="run-1",
        agent_run_started_at=time.time(),
        created_at=time.time(),
        native_session_id="native-1",
        active_turn_id="turn-1",
        project_id="project-1",
        backend="claude",
        model="model-1",
        tokens_in=0,
        tokens_out=0,
        tokens_cache_read=0,
        tokens_cache_write=0,
        cost_usd=0.0,
        context_pct=None,
        context_peak_pct=None,
        measurement_source=None,
        turn_epoch=1,
    )
    session = SimpleNamespace(record=record, transcript_path=None)
    manager = SimpleNamespace(sessions={"session-1": session})
    bus = EventBus()
    host: WorkerHost | None = None
    if worker and not control:
        config: Any = SimpleNamespace(
            data_dir=root / "data",
            worker_process_enabled=True,
            log_level="WARNING",
            gil_switch_interval_ms=5.0,
        )
        host = WorkerHost(config)
        await host.start()
        if not await host.wait_ready(60.0):
            raise SystemExit(f"the worker never became ready: {host.health()}")
    service: CanonicalTelemetryService | None = None
    if not control:
        service = CanonicalTelemetryService(root / "telemetry", in_worker=host is not None)
        if host is not None:
            service.attach_worker(host)
        service.start(bus, sessions=manager)
    worker_process = psutil.Process(host.pid) if host is not None and host.pid else None
    worker_cpu_before = sum(worker_process.cpu_times()[:2]) if worker_process else 0.0

    lags: list[float] = []
    stop = asyncio.Event()

    async def ticker() -> None:
        interval = tick_ms / 1000
        while not stop.is_set():
            before = time.perf_counter()
            await asyncio.sleep(interval)
            lags.append((time.perf_counter() - before - interval) * 1000)

    ticker_task = asyncio.create_task(ticker())
    cpu_started = time.process_time()
    started = time.perf_counter()
    # Paced: a burst every 10 ms sized to the rate. Unpaced: yield every 500.
    burst = max(1, int(rate / 100)) if rate > 0 else 500
    for index in range(events_to_send):
        await bus.emit(
            "tool_use" if index % 2 == 0 else "tool_result",
            session_id="session-1",
            source="transcript",
            tool="Read",
            call_id=f"call-{index // 2}",
            success=True,
            duration_ms=3,
        )
        if index % burst == burst - 1:
            if rate > 0:
                due = started + (index + 1) / rate
                await asyncio.sleep(max(0.0, due - time.perf_counter()))
            else:
                await asyncio.sleep(0)
    sent = time.perf_counter() - started
    if service is not None:
        # Drain: wait until the service has taken everything the bus kept and the
        # worker, if there is one, has nothing waiting for it. The service exposes
        # readings rather than an event, so this is a poll.
        while (  # noqa: ASYNC110
            service.health()["queue_depth"] > 0 or service.health()["buffered_events"] > 0
        ):
            await asyncio.sleep(0.05)
    drained = time.perf_counter() - started
    stop.set()
    await ticker_task
    accepted = 0
    provider_dropped = 0
    buffer_dropped = 0
    if service is not None:
        await service.stop()
        service.close()
        health = service.health()
        accepted = int(health["accepted"])
        provider_dropped = int(health["provider_dropped"])
        buffer_dropped = int(health["buffer_dropped"])
    cpu_seconds = time.process_time() - cpu_started
    worker_cpu = sum(worker_process.cpu_times()[:2]) - worker_cpu_before if worker_process else 0.0
    if host is not None:
        await host.stop()
    dropped = bus.drop_stats()["dropped"].get("canonical-telemetry", 0)
    lags.sort()
    mode = (
        "control (no ledger subscribed)"
        if control
        else "ledgers in a worker process"
        if host is not None
        else "ledgers in this process"
    )
    return {
        "mode": mode,
        "rate_per_second": rate if rate > 0 else "unpaced",
        "events_sent": events_to_send,
        "accepted": accepted,
        "bus_dropped_for_ledger": int(dropped),
        "provider_dropped": provider_dropped,
        "buffer_dropped": buffer_dropped,
        "send_seconds": round(sent, 3),
        "drain_seconds": round(drained, 3),
        "cpu_seconds": round(cpu_seconds, 3),
        "worker_cpu_seconds": round(worker_cpu, 3),
        "loop_lag_samples": len(lags),
        "loop_lag_p50_ms": round(statistics.median(lags), 3) if lags else 0.0,
        "loop_lag_p99_ms": round(lags[max(0, int(len(lags) * 0.99) - 1)], 3) if lags else 0.0,
        "loop_lag_max_ms": round(max(lags), 3) if lags else 0.0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--events", type=int, default=20000)
    parser.add_argument("--tick-ms", type=float, default=5.0)
    parser.add_argument(
        "--rate", type=float, default=0.0, help="observations per second (0 = unpaced flood)"
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--control", action="store_true", help="same producer, no ledger subscribed")
    mode.add_argument(
        "--worker", action="store_true", help="the ledgers in a real worker process"
    )
    arguments = parser.parse_args()
    result = asyncio.run(
        measure(
            arguments.events,
            arguments.tick_ms,
            rate=arguments.rate,
            control=arguments.control,
            worker=arguments.worker,
        )
    )
    for key, value in result.items():
        print(f"{key:<26} {value}")


if __name__ == "__main__":
    main()
