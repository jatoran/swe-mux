"""Seed a scratch canonical telemetry ledger and time its dashboard queries.

The completion gate for the ledger is stated in calls, not rows per table: ten
million tool calls answered under 200 ms for the standard dashboard views and under
500 ms for a detail page. This script measures both on a ledger it builds itself,
so the number it prints is a property of this machine and this schema rather than
of whatever the live data directory happens to hold.

    uv run python tools/telemetry_benchmark.py --calls 200000 --days 30
    uv run python tools/telemetry_benchmark.py --calls 10000000 --fast-seed --root D:/bench
    uv run python tools/telemetry_benchmark.py --root D:/bench --reuse

Two seeding paths. The default drives every observation through the reducer, the
way the daemon does, and is the right measurement of what ingestion costs; it
commits per batch and manages a few thousand calls a second. `--fast-seed` writes
the entity rows the queries read straight into the segments with `executemany` -
no evidence rows, no evidence links, no entity locations - which is what makes ten
million calls a few minutes rather than an afternoon. The aggregate and page
timings are identical between the two, because both read the same tables through
the same indexes; only the audit drill-down, which walks evidence, answers nothing
on a fast-seeded ledger.

The timings are printed twice - before any rollup, when every day is read from
canonical entities, and after every closed day and hour has been rolled up.
"""

from __future__ import annotations

import argparse
import shutil
import statistics
import tempfile
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from swe_mux.models import MuxEvent
from swe_mux.telemetry_ledger import CanonicalTelemetryLedger, classify_tool
from swe_mux.telemetry_schema import canonical_json, digest, period_of

TOOLS = ("Read", "Edit", "Bash", "Grep", "Glob", "mcp__mux__list_sessions", "Agent")
BACKENDS = ("claude", "codex", "omp")
CALLS_PER_RUN = 200
CALLS_PER_TURN = 8


def _seed(ledger: CanonicalTelemetryLedger, *, calls: int, days: int, batch: int) -> float:
    end = datetime.now(tz=UTC).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    start = end - days * 86400
    step = (end - start) / max(1, calls)
    started = time.perf_counter()
    items: list[tuple[MuxEvent, dict[str, object]]] = []
    for index in range(calls):
        ts = start + index * step
        backend = BACKENDS[index % len(BACKENDS)]
        run = f"run-{index // CALLS_PER_RUN}"
        dims = {
            "session_id": f"session-{index // CALLS_PER_RUN}",
            "run_id": run,
            "native_conversation_id": run,
            "turn_id": f"turn-{index // CALLS_PER_TURN}",
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


_TOOL_COLUMNS = (
    "tool_call_id,run_id,turn_id,agent_id,session_id,project_id,backend,model,origin,"
    "native_call_id,invocation_layer,raw_name,family,operation,transport,server_name,"
    "tool_name,proposed_at,started_at,finished_at,status,success,duration_ms,"
    "approval_wait_ms,input_bytes,output_bytes,input_sha256,output_sha256,"
    "input_measurement,output_measurement,executed_input_measurement,request_source,"
    "request_rank,result_source,result_rank,status_source,duration_source,"
    "normalization_version,evidence_count,native_conversation_id,evidence_quality"
)
_RUN_COLUMNS = (
    "run_id,session_id,native_conversation_id,project_id,backend,origin,started_at,ended_at,"
    "end_reason,initial_model,final_model,first_evidence_id,last_evidence_id,started_at_source"
)
_TURN_COLUMNS = (
    "turn_id,run_id,native_turn_id,agent_id,session_id,project_id,backend,origin,ordinal,"
    "trigger,started_at,finished_at,status,duration_ms,model,first_evidence_id,last_evidence_id"
)


def _seed_direct(ledger: CanonicalTelemetryLedger, *, calls: int, days: int, batch: int) -> float:
    """Write the same population as `_seed`, as entity rows, without the reducer.

    Every row carries the provenance columns a reducer-written row would
    (`transcript` request and result at rank 300, `evidence_quality` derived from
    that rank, a run start declared rather than estimated), so the quality view
    and the evidence-quality filter read the same answer either way.
    """

    end = datetime.now(tz=UTC).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    start = end - days * 86400
    step = (end - start) / max(1, calls)
    classified = {
        (tool, backend): classify_tool(tool, backend=backend, source="transcript")
        for tool in TOOLS
        for backend in BACKENDS
    }
    started = time.perf_counter()
    connections: dict[str, Any] = {}
    stamps_by_period: dict[str, list[float]] = {}
    tool_rows: list[tuple[Any, ...]] = []
    run_rows: list[tuple[Any, ...]] = []
    turn_rows: list[tuple[Any, ...]] = []
    current_period: str | None = None

    def connection_for(period: str) -> Any:
        connection = connections.get(period)
        if connection is None:
            connection = ledger._segment(period)
            # Scratch data: durability against a crash mid-seed is not a property
            # anyone needs here, and the fsync per commit is most of the cost.
            connection.execute("PRAGMA synchronous=OFF")
            connections[period] = connection
        return connection

    def flush(period: str) -> None:
        connection = connection_for(period)
        placeholders = ",".join("?" for _ in _TOOL_COLUMNS.split(","))
        connection.executemany(
            f"INSERT INTO telemetry_tool_calls({_TOOL_COLUMNS}) VALUES({placeholders})",
            tool_rows,
        )
        if run_rows:
            connection.executemany(
                f"INSERT OR IGNORE INTO telemetry_runs({_RUN_COLUMNS}) "
                f"VALUES({','.join('?' for _ in _RUN_COLUMNS.split(','))})",
                run_rows,
            )
        if turn_rows:
            connection.executemany(
                f"INSERT OR IGNORE INTO telemetry_turns({_TURN_COLUMNS}) "
                f"VALUES({','.join('?' for _ in _TURN_COLUMNS.split(','))})",
                turn_rows,
            )
        connection.commit()
        tool_rows.clear()
        run_rows.clear()
        turn_rows.clear()

    for index in range(calls):
        ts = start + index * step
        period = period_of(ts)
        if current_period is not None and (period != current_period or len(tool_rows) >= batch):
            flush(current_period)
        current_period = period
        stamps_by_period.setdefault(period, []).append(ts)
        backend = BACKENDS[index % len(BACKENDS)]
        run_index = index // CALLS_PER_RUN
        run_id = f"run-{run_index}"
        session_id = f"session-{run_index}"
        turn_index = index // CALLS_PER_TURN
        turn_id = digest(f"{run_id}\0turn-{turn_index}")
        project_id = f"project-{index % 5}"
        model = f"{backend}-model-{index % 2}"
        tool = TOOLS[index % len(TOOLS)]
        call_id = f"call-{index}"
        shape = classified[(tool, backend)]
        success = index % 11 != 0
        tool_rows.append(
            (
                digest(f"{run_id}\0root\0model\0{call_id}"),
                run_id,
                turn_id,
                "root",
                session_id,
                project_id,
                backend,
                model,
                "mux_owned",
                call_id,
                "model",
                tool,
                shape["family"],
                shape["operation"],
                shape["transport"],
                shape["server"],
                shape["tool"],
                ts,
                ts,
                ts + 0.5,
                "succeeded" if success else "failed",
                int(success),
                20 + index % 900,
                None if index % 50 else 1500.0 + index % 7000,
                None,
                None,
                None,
                f"hash-{index}",
                "unknown",
                "full_hash_size_unknown",
                "unknown",
                "transcript",
                300,
                "transcript",
                300,
                "transcript",
                "transcript",
                3,
                2,
                run_id,
                "transcript",
            )
        )
        if index % CALLS_PER_RUN == 0:
            run_end = min(end, ts + CALLS_PER_RUN * step)
            run_rows.append(
                (
                    run_id,
                    session_id,
                    run_id,
                    project_id,
                    backend,
                    "mux_owned",
                    ts,
                    run_end,
                    "agent_run_ended",
                    model,
                    model,
                    "seed",
                    "seed",
                    "declared",
                )
            )
        if index % CALLS_PER_TURN == 0:
            turn_end = ts + CALLS_PER_TURN * step
            turn_rows.append(
                (
                    turn_id,
                    run_id,
                    f"turn-{turn_index}",
                    "root",
                    session_id,
                    project_id,
                    backend,
                    "mux_owned",
                    turn_index,
                    "user",
                    ts,
                    turn_end,
                    "completed",
                    (turn_end - ts) * 1000,
                    model,
                    "seed",
                    "seed",
                )
            )
    if current_period is not None and tool_rows:
        flush(current_period)
    for connection in connections.values():
        connection.execute("PRAGMA synchronous=FULL")
    now = time.time()
    for period, stamps in stamps_by_period.items():
        ledger._catalog.execute(
            "UPDATE ledger_segments SET first_observed_at=?,last_observed_at=?,"
            "evidence_rows=evidence_rows+? WHERE period=?",
            (min(stamps), max(stamps), len(stamps), period),
        )
    # One stamp per hour is enough to dirty every day and hour the calls touched.
    hourly = {(stamp // 3600) * 3600 for stamps in stamps_by_period.values() for stamp in stamps}
    ledger._mark_dirty(hourly, now)
    ledger._catalog.commit()
    return time.perf_counter() - started


def _figures(summary: Any) -> Any:
    """A summary without its `coverage` block, which says how it was answered.

    Lists are sorted by their canonical JSON: a summary orders its groups by
    `(-calls, raw_name)`, and two groups that tie on both are ordered by whichever
    span produced them first, which is the one thing the two paths may legitimately
    do differently.
    """

    if isinstance(summary, dict):
        return {key: _figures(value) for key, value in summary.items() if key != "coverage"}
    if isinstance(summary, list):
        return sorted((_figures(item) for item in summary), key=canonical_json)
    return summary


def _time(label: str, function: Callable[[], object], *, repeat: int = 5) -> None:
    samples = []
    for _ in range(repeat):
        started = time.perf_counter()
        function()
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
    parser.add_argument(
        "--fast-seed",
        action="store_true",
        help="insert entity rows directly (no evidence) instead of driving the reducer",
    )
    parser.add_argument("--keep", action="store_true", help="leave the scratch ledger on disk")
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="where the scratch ledger lives (default: a temp dir)",
    )
    parser.add_argument(
        "--reuse", action="store_true", help="time the ledger already under --root; seed nothing"
    )
    arguments = parser.parse_args()
    if arguments.reuse and arguments.root is None:
        parser.error("--reuse needs --root")

    root = arguments.root or Path(tempfile.mkdtemp(prefix="swe-mux-telemetry-bench-"))
    ledger = CanonicalTelemetryLedger(root)
    if not arguments.reuse:
        seed = _seed_direct if arguments.fast_seed else _seed
        seconds = seed(ledger, calls=arguments.calls, days=arguments.days, batch=arguments.batch)
        path = "direct rows" if arguments.fast_seed else "the reducer"
        print(
            f"seeded {arguments.calls:,} calls over {arguments.days} days through {path} "
            f"in {seconds:.1f}s ({arguments.calls / max(seconds, 1e-9):,.0f} calls/s)"
        )
    now = time.time()
    day = {"from_ts": now - 86400, "to_ts": now}
    week = {"from_ts": now - 7 * 86400, "to_ts": now}
    month = {"from_ts": now - arguments.days * 86400, "to_ts": now}

    def views(label: str) -> None:
        print(f"-- {label}")
        _time("tool summary, 24 hours", lambda: ledger.tool_summary(**day))
        _time("tool summary, 7 days", lambda: ledger.tool_summary(**week))
        _time(f"tool summary, {arguments.days} days", lambda: ledger.tool_summary(**month))
        _time(
            "tool summary, 7 days, backend+status",
            lambda: ledger.tool_summary(
                **week, filters={"backend": "claude", "status": "failed"}
            ),
        )
        _time("workload summary, 24 hours", lambda: ledger.workload_summary(**day))
        _time("workload summary, 7 days", lambda: ledger.workload_summary(**week))
        _time(f"workload summary, {arguments.days} days", lambda: ledger.workload_summary(**month))
        _time("skill summary, 7 days", lambda: ledger.skill_summary(**week))
        _time("verification summary, 7 days", lambda: ledger.verification_summary(**week))
        _time("quality summary, 7 days", lambda: ledger.quality_summary(**week))
        _time("inefficiencies, 7 days", lambda: ledger.inefficiency_findings(**week))
        _time("cohorts by model, 7 days", lambda: ledger.compare_cohorts(**week, split="model"))
        _time("tool page (100), 24 hours", lambda: ledger.tool_page(**day, limit=100))
        _time("tool page (100), 7 days", lambda: ledger.tool_page(**week, limit=100))
        _time(
            "tool page (100) filtered, 7 days",
            lambda: ledger.tool_page(**week, limit=100, backend="claude", status="failed"),
        )
        _time("runs page (100), 7 days", lambda: ledger.entity_page(kind="runs", **week))
        _time(
            "export page (1000), 7 days",
            lambda: ledger.export_page(kind="tool_calls", limit=1000, **week),
        )

    views("raw (no closed-day rollups yet)")
    before = {
        "tools": _figures(ledger.tool_summary(**week)),
        "workload": _figures(ledger.workload_summary(**week)),
        "tools_month": _figures(ledger.tool_summary(**month)),
    }
    rolled_days = 0
    rolled_hours = 0
    started = time.perf_counter()
    while ledger.rebuild_next_closed_day() is not None:
        rolled_days += 1
    day_seconds = time.perf_counter() - started
    started = time.perf_counter()
    while ledger.rebuild_next_closed_hour() is not None:
        rolled_hours += 1
    hour_seconds = time.perf_counter() - started
    print(
        f"rolled up {rolled_days} closed days in {day_seconds:.1f}s and "
        f"{rolled_hours} closed hours in {hour_seconds:.1f}s"
    )
    views("rolled up (closed days and hours served from rollups)")
    after = {
        "tools": _figures(ledger.tool_summary(**week)),
        "workload": _figures(ledger.workload_summary(**week)),
        "tools_month": _figures(ledger.tool_summary(**month)),
    }
    # The two paths must agree on every figure and differ only on `coverage`,
    # which `_figures` strips; a fast-seeded ledger that disagreed here would be
    # timing a population the daemon never produces.
    for name in before:
        if before[name] != after[name]:
            raise SystemExit(f"rolled-up and raw answers differ for {name}")
    print("rolled-up and raw answers agree on every figure (7-day tools, workload; monthly tools)")
    print(f"storage: {ledger.storage_status()['bytes'] / 1024 / 1024:.1f} MiB under {root}")
    ledger.close()
    if not arguments.keep and not arguments.reuse and arguments.root is None:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    main()
