"""Measure what canonical telemetry ingestion costs the daemon's event loop.

The ledger's contract is that capture never runs on the loop that serves PTYs and
HTTP: every SQLite call goes through one worker thread, and the loop only batches
queue items. This script proves it with numbers rather than by reading the code:
it runs the real service on an event loop, floods it with observations at a rate
no fleet reaches, and samples loop lag with a 5 ms ticker the whole time - the same
kind of ticker the daemon's stall watchdog uses. It prints the worst and the p99 lag
alongside the number of observations accepted and dropped.

    uv run python tools/telemetry_ingest_latency.py --events 50000
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

from swe_mux.event_bus import EventBus
from swe_mux.telemetry_service import CanonicalTelemetryService


async def measure(events_to_send: int, tick_ms: float) -> dict[str, float | int]:
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
    service = CanonicalTelemetryService(root / "telemetry")
    bus = EventBus()
    service.start(bus, sessions=manager)

    lags: list[float] = []
    stop = asyncio.Event()

    async def ticker() -> None:
        interval = tick_ms / 1000
        while not stop.is_set():
            before = time.perf_counter()
            await asyncio.sleep(interval)
            lags.append((time.perf_counter() - before - interval) * 1000)

    ticker_task = asyncio.create_task(ticker())
    started = time.perf_counter()
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
        if index % 500 == 0:
            await asyncio.sleep(0)
    sent = time.perf_counter() - started
    # Drain: wait until the service has accepted everything the bus kept.
    while service.health()["queue_depth"] > 0:
        await asyncio.sleep(0.05)
    drained = time.perf_counter() - started
    stop.set()
    await ticker_task
    await service.stop()
    service.close()
    health = service.health()
    dropped = bus.drop_stats()["dropped"].get("canonical-telemetry", 0)
    lags.sort()
    return {
        "events_sent": events_to_send,
        "accepted": int(health["accepted"]),
        "bus_dropped_for_ledger": int(dropped),
        "provider_dropped": int(health["provider_dropped"]),
        "send_seconds": round(sent, 3),
        "drain_seconds": round(drained, 3),
        "loop_lag_samples": len(lags),
        "loop_lag_p50_ms": round(statistics.median(lags), 3) if lags else 0.0,
        "loop_lag_p99_ms": round(lags[int(len(lags) * 0.99) - 1], 3) if lags else 0.0,
        "loop_lag_max_ms": round(max(lags), 3) if lags else 0.0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--events", type=int, default=20000)
    parser.add_argument("--tick-ms", type=float, default=5.0)
    arguments = parser.parse_args()
    result = asyncio.run(measure(arguments.events, arguments.tick_ms))
    for key, value in result.items():
        print(f"{key:<26} {value}")


if __name__ == "__main__":
    main()
