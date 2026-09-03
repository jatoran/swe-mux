"""Seed a scratch canonical telemetry ledger and time its dashboard queries.

The completion gate for the ledger is stated in calls, not rows per table: ten
million tool calls answered under 200 ms for the standard dashboard views and under
500 ms for a detail page. This script measures both on a ledger it builds itself,
so the number it prints is a property of this machine and this schema rather than
of whatever the live data directory happens to hold.

    uv run python tools/telemetry_benchmark.py --calls 200000 --days 30
    uv run python tools/telemetry_benchmark.py --calls 200000 --days 30 --keep

Seeding is the slow part (the reducer commits per batch); the timings are printed
twice - before any rollup, when every day is read from canonical entities, and
after every closed day has been rolled up.
"""

from __future__ import annotations

import argparse
import shutil
import statistics
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

from swe_mux.models import MuxEvent
from swe_mux.telemetry_ledger import CanonicalTelemetryLedger

TOOLS = ("Read", "Edit", "Bash", "Grep", "Glob", "mcp__mux__list_sessions", "Agent")
BACKENDS = ("claude", "codex", "omp")


def _seed(ledger: CanonicalTelemetryLedger, *, calls: int, days: int, batch: int) -> float:
    end = datetime.now(tz=UTC).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    start = end - days * 86400
    step = (end - start) / max(1, calls)
    started = time.perf_counter()
    items: list[tuple[MuxEvent, dict[str, object]]] = []
    for index in range(calls):
        ts = start + index * step
        backend = BACKENDS[index % len(BACKENDS)]
        run = f"run-{index // 200}"
        dims = {
            "session_id": f"session-{index // 200}",
            "run_id": run,
            "native_conversation_id": run,
            "turn_id": f"turn-{index // 8}",
            "agent_id": "root",
            "project_id": f"project-{index % 5}",
            "backend": backend,
            "model": f"{backend}-model-{index % 2}",
            "origin": "mux_owned",
            "source_locator": None,
            "run_started_at": ts,
        }
        tool = TOOLS[index % len(TOOLS)]
        call_id = f"call-{index}"
        items.append(
            (
                MuxEvent(ts, dims["session_id"], "transcript", "tool_use",
                         {"tool": tool, "call_id": call_id, "target": f"file-{index}.py"},
                         seq=index * 2),
                dims,
            )
        )
        items.append(
            (
                MuxEvent(ts + 0.5, dims["session_id"], "transcript", "tool_result",
                         {"tool": tool, "call_id": call_id, "success": index % 11 != 0,
                          "duration_ms": 20 + index % 900, "content_hash": f"hash-{index}"},
                         seq=index * 2 + 1),
                dims,
            )
        )
        if len(items) >= batch:
            ledger.record_events(items)
            items = []
    if items:
        ledger.record_events(items)
    return time.perf_counter() - started


def _time(label: str, function: object, *, repeat: int = 5) -> None:
    samples = []
    for _ in range(repeat):
        started = time.perf_counter()
        function()  # type: ignore[operator]
        samples.append((time.perf_counter() - started) * 1000)
    print(
        f"{label:<42} median {statistics.median(samples):8.1f} ms   "
        f"max {max(samples):8.1f} ms"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--calls", type=int, default=100_000)
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--batch", type=int, default=2000)
    parser.add_argument("--keep", action="store_true", help="leave the scratch ledger on disk")
    parser.add_argument("--root", type=Path, default=None, help="reuse an existing scratch ledger")
    arguments = parser.parse_args()

    root = arguments.root or Path(tempfile.mkdtemp(prefix="swe-mux-telemetry-bench-"))
    ledger = CanonicalTelemetryLedger(root)
    if arguments.root is None:
        seconds = _seed(ledger, calls=arguments.calls, days=arguments.days, batch=arguments.batch)
        print(f"seeded {arguments.calls:,} calls over {arguments.days} days in {seconds:.1f}s")
    now = time.time()
    week = {"from_ts": now - 7 * 86400, "to_ts": now}
    month = {"from_ts": now - arguments.days * 86400, "to_ts": now}

    def views(label: str) -> None:
        print(f"-- {label}")
        _time("tool summary, 7 days", lambda: ledger.tool_summary(**week))
        _time(f"tool summary, {arguments.days} days", lambda: ledger.tool_summary(**month))
        _time("workload summary, 7 days", lambda: ledger.workload_summary(**week))
        _time(f"workload summary, {arguments.days} days", lambda: ledger.workload_summary(**month))
        _time("quality summary, 7 days", lambda: ledger.quality_summary(**week))
        _time("inefficiencies, 7 days", lambda: ledger.inefficiency_findings(**week))
        _time("tool page (100), 7 days", lambda: ledger.tool_page(**week, limit=100))
        _time(
            "tool page (100) filtered, 7 days",
            lambda: ledger.tool_page(**week, limit=100, backend="claude", status="failed"),
        )
        _time(
            "export page (1000), 7 days",
            lambda: ledger.export_page(kind="tool_calls", limit=1000, **week),
        )

    views("raw (no closed-day rollups yet)")
    rolled = 0
    started = time.perf_counter()
    while ledger.rebuild_next_closed_day() is not None:
        rolled += 1
    print(f"rolled up {rolled} closed days in {time.perf_counter() - started:.1f}s")
    views("rolled up (closed days served from rollups)")
    print(f"storage: {ledger.storage_status()['bytes'] / 1024 / 1024:.1f} MiB under {root}")
    ledger.close()
    if not arguments.keep and arguments.root is None:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    main()
