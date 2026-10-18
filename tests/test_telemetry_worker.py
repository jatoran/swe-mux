"""Canonical telemetry in the worker process: `DAEMON_LOAD_ISOLATION.md` item 21.

The item's done-when is that replaying one recorded event stream yields
identical ledger rows in and out of process, so that is the central test here:
the same stream - bus events from sessions the daemon knows and from one it does
not, parser signatures, and the scrubbed real OTLP captures from Claude and
Codex - goes once into ledgers in this process and once through a real worker
(`python -m swe_mux.worker`), and every row of every table of every ledger file
is compared, along with what the queries the routes make answer over them.

A ledger also stamps some columns from its own clock as it writes (when an
observation was received, when a period was dirtied); no two runs share those
even in one process, so they are compared as what they are - a wall-clock
reading taken during that run - rather than left out (`CLOCK_COLUMNS`).

The rest pins what makes the move safe: the wire keeps every input of an
evidence id exactly as the daemon would digest it, recording a batch twice
records it once, a worker that is away buffers rather than drops (and a full
buffer drops the oldest), a restarted worker is reopened before it records, and
a query nobody can answer is the 503 the transport maps it to.
"""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import json
import sqlite3
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import psutil
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from swe_mux import telemetry_service
from swe_mux.errors import TelemetryUnavailable
from swe_mux.event_bus import EventBus
from swe_mux.models import MuxEvent
from swe_mux.server import error_middleware
from swe_mux.telemetry_otlp import PARSER_VERSION, otlp_reduction
from swe_mux.telemetry_schema import canonical_json
from swe_mux.telemetry_service import (
    CanonicalTelemetryService,
    WorkerLedgers,
    adapt_result,
    decode_batch,
    decode_call,
    encode_batch,
    encode_call,
)
from swe_mux.worker_host import WorkerHost
from tests.support.settle import until

READY_SECONDS = 60.0
SETTLE_SECONDS = 30.0
START = 1_788_000_000.0
CAPTURES = Path(__file__).parent / "fixtures" / "telemetry"
CLAUDE_CAPTURE = CAPTURES / "otlp-claude-2.1.259.json"
CODEX_CAPTURE = CAPTURES / "otlp-codex-0.153.0.json"

#: Columns a ledger stamps from its own clock at write time, by table. Two runs of
#: one stream differ here even in one process; each value must instead be a
#: reading of the wall clock taken while its own run was writing. Found by running
#: the comparison with this empty: these and nothing else disagreed.
CLOCK_COLUMNS: dict[str, frozenset[str]] = {
    "telemetry_evidence": frozenset({"received_at"}),
    "ledger_segments": frozenset({"last_written_at"}),
    "parser_signatures": frozenset({"first_seen_at", "last_seen_at"}),
    "rollup_dirty_days": frozenset({"dirtied_at"}),
    "rollup_dirty_hours": frozenset({"dirtied_at"}),
}


@dataclass
class FakeConfig:
    data_dir: Path
    worker_process_enabled: bool = True
    log_level: str = "INFO"
    gil_switch_interval_ms: float = 5.0


@contextlib.asynccontextmanager
async def hosting(data_dir: Path, **kwargs: Any) -> AsyncIterator[WorkerHost]:
    host = WorkerHost(FakeConfig(data_dir), **kwargs)
    await host.start()
    try:
        assert await host.wait_ready(READY_SECONDS), host.health()
        yield host
    finally:
        await asyncio.wait_for(host.stop(), timeout=30)


# --- the stream ------------------------------------------------------------------


def known_session(session_id: str, backend: str, run_id: str) -> Any:
    record = SimpleNamespace(
        id=session_id,
        agent_run_id=run_id,
        agent_run_started_at=START,
        created_at=START,
        native_session_id=f"native-{session_id}",
        active_turn_id="turn-1",
        project_id="project-1",
        backend=backend,
        model="model-1",
    )
    return SimpleNamespace(record=record, transcript_path=Path(f"C:/provider/{session_id}.jsonl"))


def known_sessions() -> dict[str, Any]:
    return {
        "session-1": known_session("session-1", "claude", "run-1"),
        "session-2": known_session("session-2", "codex", "run-2"),
    }


def capture_bodies(path: Path) -> list[bytes]:
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(loaded, list) and loaded
    return [json.dumps(payload).encode("utf-8") for payload in loaded]


#: (type, session, source, payload): a fleet's worth of evidence kinds in a fixed order.
STREAM: list[tuple[str, str, str, dict[str, Any]]] = [
    ("agent_run_started", "session-1", "control-plane", {}),
    ("turn_started", "session-1", "transcript", {"turn_id": "turn-1", "turn_epoch": 1}),
    (
        "tool_use",
        "session-1",
        "transcript",
        {"tool": "Bash", "call_id": "call-1", "target": "pytest -q", "content_hash": "h1"},
    ),
    (
        "tool_result",
        "session-1",
        "transcript",
        {
            "tool": "Bash",
            "call_id": "call-1",
            "success": False,
            "duration_ms": 1250,
            "detail": "2 failed, 40 passed",
            "test_outcome": {"framework": "pytest", "passed": 40, "failed": 2},
        },
    ),
    ("approval_needed", "session-1", "hook", {"call_id": "call-2", "tool": "Edit"}),
    ("approval_resolved", "session-1", "hook", {"call_id": "call-2", "tool": "Edit"}),
    (
        "tool_use",
        "session-1",
        "transcript",
        # A value JSON cannot hold, which both sides encode with `default=str`.
        {"tool": "Edit", "call_id": "call-2", "path": Path("src/app.py"), "model": "model-2"},
    ),
    (
        "tool_result",
        "session-1",
        "transcript",
        {"tool": "Edit", "call_id": "call-2", "success": True, "duration_ms": 12},
    ),
    ("skill_invoked", "session-1", "transcript", {"skill": "review", "call_id": "call-3"}),
    (
        "context_compacted",
        "session-1",
        "transcript",
        {"compaction_id": "c-1", "pre_tokens": 150000, "post_tokens": 30000},
    ),
    ("turn_started", "session-2", "transcript", {"turn_id": "turn-9", "turn_epoch": 1}),
    (
        "tool_use",
        "session-2",
        "transcript",
        {"tool": "exec_command", "call_id": "codex-1", "target": ("uv", "run", "pytest")},
    ),
    # A session this daemon does not know: its dimensions come from the payload alone.
    (
        "tool_use",
        "stray-session",
        "transcript",
        {
            "tool": "Read",
            "call_id": "stray-1",
            "agent_run_id": "stray-run",
            "backend": "codex",
            "project_id": "project-2",
        },
    ),
    ("turn_ended", "session-1", "transcript", {"turn_id": "turn-1", "turn_epoch": 1}),
    ("agent_run_ended", "session-1", "control-plane", {"reason": "user"}),
]
#: Emitted after the OTLP captures. In process, a body that reduces to no events
#: leaves its parser signatures waiting for the next batch (the worker records
#: them with the body), so the queries are asked once one more batch has gone.
EPILOGUE: list[tuple[str, str, str, dict[str, Any]]] = [
    ("turn_ended", "session-2", "transcript", {"turn_id": "turn-9", "turn_epoch": 1}),
]


async def settle(service: CanonicalTelemetryService) -> None:
    """Every observation handed to the service so far has been delivered or buffered."""
    queue = service._queue
    assert queue is not None
    await asyncio.wait_for(queue.join(), SETTLE_SECONDS)


async def send_otlp(
    service: CanonicalTelemetryService, session: Any, body: bytes, *, forward: bool
) -> None:
    """What the OTLP route does with one body, in the mode the service is in."""
    if forward:
        assert await service.ingest_otlp(session, body) is not None
        return
    reduction = otlp_reduction(
        json.loads(body), session_id=session.record.id, backend=session.record.backend
    )
    service.note_parser_signatures(
        backend=str(session.record.backend),
        harness_version=reduction.harness_version,
        parser_version=PARSER_VERSION,
        signatures=reduction.signatures,
    )
    assert all(service.enqueue_provider_event(event) for event in reduction.events)
    await settle(service)


SCOPE: dict[str, Any] = {
    "from_ts": START - 86_400,
    "to_ts": START + 86_400,
    "origin": None,
    "filters": {},
}


async def ask(service: CanonicalTelemetryService) -> dict[str, Any]:
    """What the routes would serve over these ledgers, and how a bad argument is refused."""
    answers: dict[str, Any] = {
        "tool_summary": await service.tool_summary(**SCOPE),
        "workload_summary": await service.workload_summary(**SCOPE),
        "skill_summary": await service.skill_summary(**SCOPE),
        "verification_summary": await service.verification_summary(**SCOPE),
        "compaction_summary": await service.compaction_summary(**SCOPE),
        "quality_summary": await service.quality_summary(**SCOPE),
        "metric_summary": await service.metric_summary(**SCOPE),
        "compare_cohorts": await service.compare_cohorts(split="model", **SCOPE),
        "tool_calls": await service.entity_page(kind="tool_calls", limit=500, **SCOPE),
        "runs": await service.entity_page(kind="runs", limit=500, **SCOPE),
        "run_audit": await service.run_audit("run-1"),
        "missing_run": await service.run_audit("no-such-run"),
        "quota_usage_evidence": await service.quota_usage_evidence(
            "claude", START - 86_400, START + 86_400
        ),
        "quota_usage_coverage": await service.quota_usage_coverage("claude"),
        "parser_signatures": await service.parser_signatures(),
    }
    with pytest.raises(ValueError) as refused:
        await service.compare_cohorts(split="not-a-dimension", **SCOPE)
    answers["refusal"] = str(refused.value)
    return answers


async def replay(service: CanonicalTelemetryService, *, forward: bool) -> dict[str, Any]:
    sessions = known_sessions()
    sequence = itertools.count(1)
    ticks = itertools.count()
    bus = EventBus(lambda _event: next(sequence), clock=lambda: START + next(ticks))
    service.start(bus, sessions=SimpleNamespace(sessions=sessions))
    try:
        service.note_parser_signatures(
            backend="claude",
            harness_version="2.1.259",
            parser_version=PARSER_VERSION,
            signatures={("claude_code.api_request", True): 3, ("claude_code.renamed", False): 1},
        )
        for event_type, session_id, source, payload in STREAM:
            await bus.emit(event_type, session_id=session_id, source=source, **payload)
        await settle(service)
        for session_id, capture in (("session-1", CLAUDE_CAPTURE), ("session-2", CODEX_CAPTURE)):
            for body in capture_bodies(capture):
                await send_otlp(service, sessions[session_id], body, forward=forward)
        for event_type, session_id, source, payload in EPILOGUE:
            await bus.emit(event_type, session_id=session_id, source=source, **payload)
        await settle(service)
        return await ask(service)
    finally:
        await service.stop()
        service.close()


# --- comparing ledgers -------------------------------------------------------------


def ledger_files(root: Path) -> list[Path]:
    return [root / "catalog.sqlite3", *sorted((root / "segments").glob("*.sqlite3"))]


def dump(root: Path) -> dict[str, tuple[list[str], list[tuple[Any, ...]]]]:
    """Every table of every ledger file: its columns, and its rows in a stable order."""
    result: dict[str, tuple[list[str], list[tuple[Any, ...]]]] = {}
    for path in ledger_files(root):
        connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        try:
            tables = [
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' "
                    "AND name NOT LIKE 'sqlite_%' ORDER BY name"
                )
            ]
            for table in tables:
                info = connection.execute(f'PRAGMA table_info("{table}")')
                columns = [str(row[1]) for row in info]
                rows = connection.execute(f'SELECT * FROM "{table}"').fetchall()
                result[f"{path.name}:{table}"] = (columns, sorted(rows, key=repr))
        finally:
            connection.close()
    return result


def split_clock(
    tables: dict[str, tuple[list[str], list[tuple[Any, ...]]]],
) -> tuple[dict[str, list[tuple[Any, ...]]], dict[str, list[Any]]]:
    """Each table's rows without its clock columns, and the clock values set aside."""
    rows: dict[str, list[tuple[Any, ...]]] = {}
    clocks: dict[str, list[Any]] = {}
    for name, (columns, table_rows) in tables.items():
        clock = CLOCK_COLUMNS.get(name.split(":", 1)[1], frozenset())
        kept = [index for index, column in enumerate(columns) if column not in clock]
        stamped = [index for index, column in enumerate(columns) if column in clock]
        rows[name] = sorted((tuple(row[i] for i in kept) for row in table_rows), key=repr)
        for index in stamped:
            clocks[f"{name}.{columns[index]}"] = [row[index] for row in table_rows]
    return rows, clocks


def differing_columns(
    local: dict[str, tuple[list[str], list[tuple[Any, ...]]]],
    worker: dict[str, tuple[list[str], list[tuple[Any, ...]]]],
) -> dict[str, list[str]]:
    """Which columns of which tables disagree, to name them when the rows do."""
    found: dict[str, list[str]] = {}
    for name in sorted(set(local) & set(worker)):
        columns, local_rows = local[name]
        _, worker_rows = worker[name]
        if len(local_rows) != len(worker_rows):
            found[name] = [f"{len(local_rows)} rows here, {len(worker_rows)} in the worker"]
            continue
        differ = [
            column
            for index, column in enumerate(columns)
            if sorted(repr(row[index]) for row in local_rows)
            != sorted(repr(row[index]) for row in worker_rows)
        ]
        if differ:
            found[name] = differ
    return found


CLOCK_KEYS = frozenset().union(*CLOCK_COLUMNS.values())


def without_clock(answer: Any, readings: list[float]) -> Any:
    """A JSON answer with every clock column's value moved into `readings`."""
    if isinstance(answer, dict):
        kept: dict[str, Any] = {}
        for key, value in answer.items():
            if key in CLOCK_KEYS and (value is None or isinstance(value, int | float)):
                if value is not None:
                    readings.append(float(value))
                continue
            kept[key] = without_clock(value, readings)
        return kept
    if isinstance(answer, list):
        return [without_clock(item, readings) for item in answer]
    return answer


def answer_differences(local: Any, worker: Any, path: str = "") -> list[str]:
    """The paths at which two JSON answers disagree, with both values."""
    if isinstance(local, dict) and isinstance(worker, dict):
        found: list[str] = []
        for key in sorted(set(local) | set(worker), key=str):
            found += answer_differences(local.get(key), worker.get(key), f"{path}.{key}")
        return found
    if isinstance(local, list) and isinstance(worker, list) and len(local) == len(worker):
        found = []
        for index, (left, right) in enumerate(zip(local, worker, strict=True)):
            found += answer_differences(left, right, f"{path}[{index}]")
        return found
    return [] if local == worker else [f"{path}: {local!r} here, {worker!r} in the worker"]


def assert_clock_readings(clocks: dict[str, list[Any]], started: float, ended: float) -> None:
    for column, values in clocks.items():
        for value in values:
            if value is None:
                continue
            assert started - 1 <= float(value) <= ended + 1, (
                f"{column}={value} is not a clock reading from its own run "
                f"[{started:.3f}, {ended:.3f}]"
            )


# --- the wire ---------------------------------------------------------------------


def test_the_wire_keeps_every_input_of_an_evidence_id() -> None:
    """Session, type, source, sequence and payload digest survive exactly.

    Those five are what the ledger digests into an evidence id, so an observation
    the worker records carries the id this process would have given it - which is
    what makes a resent batch a no-op rather than a duplicate.
    """
    payload = {
        "path": Path("src/app.py"),
        "argv": ("uv", "run", "pytest"),
        "nested": {"b": 2, "a": [1.5, None, True]},
        "ratio": 0.1 + 0.2,
        "big": 2**62,
        "text": "caf\u00e9 \u2603",
    }
    event = MuxEvent(START + 0.123456789, "session-1", "transcript", "tool_use", payload, seq=42)
    stray = MuxEvent(START, None, "daemon", "turn_started", {}, seq=7, transient=True)
    dimensions = {"session_id": "session-1", "run_started_at": START, "locator": Path("x/y")}
    signatures = [("claude", "2.1.259", PARSER_VERSION, {("a.b", True): 3, ("c.d", False): 1})]

    batch, decoded_signatures = decode_batch(
        encode_batch([(event, dimensions), (stray, {})], signatures)
    )

    (decoded, decoded_dimensions), (decoded_stray, _) = batch
    assert (decoded.session_id, decoded.type, decoded.source, decoded.seq) == (
        "session-1",
        "tool_use",
        "transcript",
        42,
    )
    assert decoded.ts == event.ts
    assert canonical_json(decoded.payload) == canonical_json(payload)
    assert decoded_dimensions == json.loads(json.dumps(dimensions, default=str))
    assert (decoded_stray.session_id, decoded_stray.transient, decoded_stray.seq) == (
        None,
        True,
        7,
    )
    assert decoded_signatures == signatures


def test_a_batch_carries_one_dimension_set_per_event() -> None:
    event = MuxEvent(START, "session-1", "transcript", "tool_use", {}, seq=1)
    encoded = json.loads(encode_batch([(event, {})], []))
    encoded["dims"] = []
    with pytest.raises(ValueError, match="one dimension set per event"):
        decode_batch(json.dumps(encoded).encode())


def test_a_call_crosses_with_its_path_restored_and_quota_rows_come_back_as_tuples() -> None:
    args, kwargs = decode_call(
        encode_call((Path("C:/data/mux.db"),), {"batch_size": 250}), "import_legacy_batch"
    )
    assert args == [Path("C:/data/mux.db")] and kwargs == {"batch_size": 250}
    args, _ = decode_call(encode_call(("run-1",), {}), "run_audit")
    assert args == ["run-1"], "only the methods that take a path get one back"

    rows = {"requests": [[1.0, "run-1", "run-1", 2.0, 0.1]], "turns": []}
    assert adapt_result("quota_usage_evidence", rows) == {
        "requests": [(1.0, "run-1", "run-1", 2.0, 0.1)],
        "turns": [],
    }
    assert adapt_result("tool_summary", {"rows": [[1, 2]]}) == {"rows": [[1, 2]]}


# --- no worker ----------------------------------------------------------------------


class AbsentWorker:
    """A `WorkerHost` whose worker never comes up."""

    ready = False
    generation = 0

    async def wait_ready(self, seconds: float) -> bool:
        return False

    async def call(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("nothing calls a worker that is not ready")


def absent_service(tmp_path: Path) -> CanonicalTelemetryService:
    service = CanonicalTelemetryService(tmp_path / "telemetry", in_worker=True)
    service.attach_worker(AbsentWorker())
    return service


def tool_batch(start: int, count: int) -> telemetry_service.Batch:
    return [
        (
            MuxEvent(START + i, "session-1", "transcript", "tool_use", {"call_id": f"c{i}"}, seq=i),
            {"session_id": "session-1"},
        )
        for i in range(start, start + count)
    ]


async def test_with_no_worker_observations_wait_and_the_oldest_go_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(telemetry_service, "BUFFER_EVENT_LIMIT", 5)
    service = absent_service(tmp_path)
    for start in (0, 2, 4, 6):
        await service._deliver(tool_batch(start, 2), [])
    health = service.health()
    assert (health["buffered_events"], health["buffer_dropped"]) == (4, 4)
    assert health["worker_unavailable"] == 1, "behind a waiting buffer, later batches just queue"
    kept = [event.payload["call_id"] for batch, _ in service._buffer for event, _ in batch]
    assert kept == ["c4", "c5", "c6", "c7"], "the newest evidence is what is kept"
    # One batch larger than the whole limit is still kept rather than emptied out.
    service._buffer.clear()
    service._buffered_events = 0
    await service._deliver(tool_batch(10, 7), [])
    assert service.health()["buffered_events"] == 7
    assert not (tmp_path / "telemetry").exists(), "the daemon never opens the ledgers itself"


async def test_a_query_with_no_worker_is_a_503_that_says_why(tmp_path: Path) -> None:
    service = absent_service(tmp_path)
    with pytest.raises(TelemetryUnavailable):
        await service.run_audit("run-1")
    assert service.forwards_otlp
    session = known_session("session-1", "claude", "run-1")
    assert await service.ingest_otlp(session, b"{}") is None, "the route parses it instead"
    assert service.health()["worker_unavailable"] == 1

    async def handler(request: web.Request) -> web.Response:
        await service.run_audit("run-1")
        return web.Response(text="unreachable")

    app = web.Application(middlewares=[error_middleware])
    app.router.add_get("/audit", handler)
    async with TestClient(TestServer(app)) as client:
        response = await client.get("/audit")
        assert response.status == 503
        body = await response.json()
    assert body["code"] == "telemetry_unavailable"


def test_worker_mode_needs_a_worker_attached(tmp_path: Path) -> None:
    service = CanonicalTelemetryService(tmp_path / "telemetry", in_worker=True)
    assert not service.forwards_otlp
    with pytest.raises(RuntimeError, match="none was attached"):
        service.start(EventBus(), sessions=SimpleNamespace(sessions={}))


# --- in and out of process ----------------------------------------------------------


async def test_replaying_one_stream_yields_identical_ledgers_in_and_out_of_process(
    tmp_path: Path,
) -> None:
    local_root = tmp_path / "local" / "telemetry"
    worker_root = tmp_path / "worker" / "telemetry"

    started_local = time.time()
    local_answers = await replay(CanonicalTelemetryService(local_root), forward=False)
    ended_local = time.time()

    async with hosting(tmp_path / "worker-data", restart_initial=0.1) as host:
        service = CanonicalTelemetryService(worker_root, in_worker=True)
        service.attach_worker(host)
        assert service.forwards_otlp
        started_worker = time.time()
        worker_answers = await replay(service, forward=True)
        ended_worker = time.time()
        health = service.health()
    assert health["in_worker"] is True and health["worker_opens"] == 1
    assert health["buffered_events"] == 0 and health["worker_unavailable"] == 0
    assert not (tmp_path / "worker-data" / "telemetry").exists(), (
        "the worker wrote the root it was opened at, and nowhere else"
    )

    local_tables, worker_tables = dump(local_root), dump(worker_root)
    assert sorted(local_tables) == sorted(worker_tables), "the same files and tables"
    local_rows, local_clocks = split_clock(local_tables)
    worker_rows, worker_clocks = split_clock(worker_tables)
    assert sum(len(rows) for rows in local_rows.values()) > 50, "the stream recorded something"
    assert local_rows == worker_rows, (
        "the ledgers differ in and out of process; columns that disagree: "
        f"{differing_columns(local_tables, worker_tables)}"
    )
    assert sorted(local_clocks) == sorted(worker_clocks)
    assert_clock_readings(local_clocks, started_local, ended_local)
    assert_clock_readings(worker_clocks, started_worker, ended_worker)

    # A route serves the JSON of what it is given, so that is what must agree -
    # clock readings apart, which are held to their own runs as the rows were.
    local_readings: list[float] = []
    worker_readings: list[float] = []
    differences = answer_differences(
        without_clock(json.loads(json.dumps(local_answers, default=str)), local_readings),
        without_clock(json.loads(json.dumps(worker_answers, default=str)), worker_readings),
    )
    assert not differences, "\n".join(differences)
    assert len(local_readings) == len(worker_readings)
    assert_clock_readings({"answers": local_readings}, started_local, ended_local)
    assert_clock_readings({"answers": worker_readings}, started_worker, ended_worker)
    assert worker_answers["quota_usage_evidence"] == local_answers["quota_usage_evidence"], (
        "quota attribution reads these rows as tuples in both modes"
    )
    assert worker_answers["refusal"].startswith("split must be one of")


async def test_recording_a_batch_twice_records_it_once(tmp_path: Path) -> None:
    """The worker can die after committing a batch and before acknowledging it."""
    async with hosting(tmp_path / "worker-data") as host:
        ledgers = WorkerLedgers(host, tmp_path / "telemetry")
        dimensions = {"session_id": "session-1", "run_id": "run-1", "backend": "claude"}
        events: telemetry_service.Batch = [
            (
                MuxEvent(
                    START + index,
                    "session-1",
                    "transcript",
                    "tool_use",
                    {"call_id": f"c{index}"},
                    seq=index,
                ),
                dimensions,
            )
            for index in range(1, 6)
        ]
        first = await ledgers.record(events, [])
        again = await ledgers.record(events, [])
        assert (first, again) == (5, 0)
        assert ledgers.opens == 1
        with pytest.raises(ValueError):
            await ledgers.otlp(session_id="session-1", backend="claude", dimensions={}, body=b"{")
        with pytest.raises(ValueError, match="resourceLogs or resourceMetrics"):
            await ledgers.otlp(
                session_id="session-1", backend="claude", dimensions={}, body=b'{"other":1}'
            )
        with pytest.raises(RuntimeError, match="not a query method"):
            await ledgers.query("drop_everything")
        await ledgers.shutdown()


async def test_a_worker_that_goes_away_is_buffered_for_and_its_successor_reopened(
    tmp_path: Path,
) -> None:
    sessions = known_sessions()
    sequence = itertools.count(1)
    bus = EventBus(lambda _event: next(sequence))
    # Slow enough to restart that observations reliably arrive while it is away.
    async with hosting(tmp_path / "worker-data", restart_initial=2.0) as host:
        service = CanonicalTelemetryService(tmp_path / "telemetry", in_worker=True)
        service.attach_worker(host)
        service.start(bus, sessions=SimpleNamespace(sessions=sessions))
        try:
            await bus.emit("tool_use", session_id="session-1", source="transcript", call_id="a")
            await settle(service)
            assert service.health()["accepted"] == 1

            first = host.pid
            assert first is not None
            psutil.Process(first).kill()
            await until(lambda: not host.ready, seconds=READY_SECONDS, what="the worker gone")
            await bus.emit("tool_use", session_id="session-1", source="transcript", call_id="b")
            await bus.emit("tool_use", session_id="session-1", source="transcript", call_id="c")
            await settle(service)
            held = service.health()
            assert held["buffered_events"] == 2, held
            assert held["worker_unavailable"] >= 1
            assert not host.ready

            # A query asked meanwhile waits for the worker that is starting
            # (`QUERY_WAIT_SECONDS`) rather than answering 503 at once.
            signatures = await service.parser_signatures()
            assert host.ready and isinstance(signatures, list)

            await until(
                lambda: service.health()["buffered_events"] == 0,
                seconds=READY_SECONDS,
                what="the buffer delivered to the next worker",
            )
            health = service.health()
            assert host.pid != first
            assert health["worker_opens"] == 2, "the successor was opened before it recorded"
            assert health["accepted"] == 3 and health["buffer_dropped"] == 0
            audit = await service.run_audit("run-1")
            assert audit is not None
        finally:
            await service.stop()
            service.close()
