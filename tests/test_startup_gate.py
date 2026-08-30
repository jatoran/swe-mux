"""The daemon binds its listeners before it builds its runtime.

A start with 30 live sessions measured 226.6s, all of it before any listener
bound, and ~170s of it in two stretches that logged nothing at all. That made a
healthy-but-slow deploy indistinguishable from a hung one - which is how a 300s
health ceiling came to roll back a perfectly good bundle.

Two properties follow, and both are asserted here rather than assumed:

- the startup window is *reachable and legible* - health answers with the phase
  in flight, every other route is refused with the same answer rather than
  reaching for state that does not exist yet, and no phase can run for minutes
  without a log line;
- readiness is a real signal - `wait_runtime_ready` is what a caller that needs
  a built daemon waits on, and a build that fails stops the daemon instead of
  leaving it serving 503 forever.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from swe_mux import app_keys as keys
from swe_mux.config import Config
from swe_mux.server import (
    STARTUP_OPEN_PATHS,
    _database_integrity_loop,
    create_app,
    publish,
    startup_open,
    wait_runtime_ready,
)
from swe_mux.sqlite_store import (
    FULL_VERIFICATION_INTERVAL_SECONDS,
    NEAR_TERM_VERIFICATION_DELAY_SECONDS,
    ROUTINE_VERIFICATION_DELAY_SECONDS,
    VerificationControl,
    _light_integrity_problem,
    connect_or_quarantine,
    prefetch_database,
    prepare_database,
    read_verification_record,
    record_database_problem,
    record_database_verified,
    reset_integrity_cache,
    run_full_verification,
    verification_record_path,
    verify_database,
)
from swe_mux.startup_phases import UNNAMED_PHASE, StartupTimeline
from tests.support.settle import until

# --------------------------------------------------------------- the timeline


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_every_phase_is_logged_with_its_elapsed_time(caplog: Any) -> None:
    clock = FakeClock()
    ledger: list[str] = []
    timeline = StartupTimeline(
        logging.getLogger("startup-test"), ledger=ledger.append, clock=clock
    )
    with caplog.at_level(logging.INFO, logger="startup-test"):
        timeline.mark("stores")
        clock.advance(11.5)
        timeline.mark("supervisor-connect")
        clock.advance(0.4)
        total = timeline.finish("30 live session(s)")

    assert total == pytest.approx(11.9)
    lines = [record.getMessage() for record in caplog.records]
    assert "startup_phase name=stores elapsed=11.50s total=11.5s" in lines
    assert "startup_phase name=supervisor-connect elapsed=0.40s total=11.9s" in lines
    # A phase heavy enough to dominate a start is a WARNING, not an INFO line
    # buried among a hundred others.
    levels = {record.getMessage().split()[1]: record.levelno for record in caplog.records}
    assert levels["name=stores"] == logging.WARNING
    assert levels["name=supervisor-connect"] == logging.INFO
    # lifecycle.log carries the same transitions, which is what makes a long
    # wait read as progress from outside the process.
    assert any("startup phase stores took 11.5s" in entry for entry in ledger)
    assert any("daemon runtime ready in 11.9s" in entry for entry in ledger)


def test_an_in_flight_phase_is_reported_before_it_can_go_silent(caplog: Any) -> None:
    """The diagnosability bug was never the missing completion line.

    Both silent stretches were phases still *running*, so a timeline that only
    logged on completion would have reproduced the exact failure it exists to
    prevent.
    """
    clock = FakeClock()
    timeline = StartupTimeline(
        logging.getLogger("startup-test"), clock=clock, slow_phase_seconds=15.0
    )
    timeline.mark("database-integrity")
    clock.advance(14.0)
    assert timeline.overdue() is None
    clock.advance(2.0)
    with caplog.at_level(logging.WARNING, logger="startup-test"):
        assert timeline.report_overdue() is True
        # Reported once, then quiet again until the next window elapses.
        assert timeline.report_overdue() is False
        clock.advance(16.0)
        assert timeline.report_overdue() is True
    messages = [record.getMessage() for record in caplog.records]
    assert messages[0].startswith("startup_phase_running name=database-integrity elapsed=16.0s")
    assert messages[1].startswith("startup_phase_running name=database-integrity elapsed=32.0s")


def test_unnamed_work_between_phases_is_still_reported(caplog: Any) -> None:
    """Work nobody wrapped is exactly the work that went silent for 98s."""
    clock = FakeClock()
    timeline = StartupTimeline(
        logging.getLogger("startup-test"), clock=clock, slow_phase_seconds=15.0
    )
    timeline.mark("stores")
    clock.advance(1.0)
    timeline._close_open_phase()  # a phase ends; the next one has not started
    clock.advance(20.0)
    with caplog.at_level(logging.WARNING, logger="startup-test"):
        assert timeline.report_overdue() is True
    assert UNNAMED_PHASE in caplog.records[-1].getMessage()
    timeline.mark("next")
    assert [record.name for record in timeline._completed][:2] == ["stores", UNNAMED_PHASE]


def test_a_failed_build_records_which_phase_died() -> None:
    clock = FakeClock()
    timeline = StartupTimeline(logging.getLogger("startup-test"), clock=clock)
    timeline.mark("supervisor-connect")
    clock.advance(4.0)
    timeline.fail(RuntimeError("supervisor handshake failed"))

    snapshot = timeline.snapshot()
    assert snapshot["status"] == "failed"
    assert snapshot["error"] == "supervisor handshake failed"
    # The phase that was running when it died is the first thing anyone asks for.
    assert snapshot["phases"][-1] == {"name": "supervisor-connect", "seconds": 4.0}


def test_the_snapshot_stops_naming_a_phase_once_it_is_ready() -> None:
    clock = FakeClock()
    timeline = StartupTimeline(logging.getLogger("startup-test"), clock=clock)
    timeline.mark("stores")
    clock.advance(2.0)
    assert timeline.snapshot()["phase"] == "stores"
    timeline.finish()
    # A name left behind after readiness reads as a phase that never ended.
    assert timeline.snapshot()["phase"] is None
    assert timeline.ready is True


# ------------------------------------------------- the integrity probe's cost


def test_the_integrity_probe_answers_once_per_file(tmp_path: Path, monkeypatch: Any) -> None:
    """The measured root cause: `PRAGMA quick_check` ran once per *store*.

    It reads every page, so its cost is the size of the file - 11.5s against a
    2.73 GB `mux.db` - and eleven stores share that one file, which is ~126s of
    every start spent re-answering a question about a file that had not changed.
    """
    reset_integrity_cache()
    database = tmp_path / "mux.db"
    sqlite3.connect(database).close()
    probes: list[Path] = []
    real_problem = None

    def counting_probe(path: Path) -> None:
        probes.append(path)
        return real_problem

    monkeypatch.setattr("swe_mux.sqlite_store._integrity_problem", counting_probe)

    for _ in range(11):
        connect_or_quarantine(database, lambda: sqlite3.connect(database)).close()

    assert len(probes) == 1
    # And a caller may pay for it up front, by name and off the event loop. Since
    # 2026-08-30 that up-front payment is the *light* probe in every case: the
    # full walk is scheduled behind the ready daemon rather than run here.
    reset_integrity_cache()
    preparation = prepare_database(database)
    assert preparation.mode == "light"
    assert preparation.reason == "no passing full check is on record"
    assert preparation.full_check_owed
    assert preparation.problem is None
    assert preparation.seconds >= 0.0
    assert len(probes) == 1
    assert verify_database(database) is None
    assert len(probes) == 1


def test_a_quarantined_database_is_remembered_as_the_healthy_replacement(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """After a quarantine the file behind the path is a new one.

    Every later store used to re-probe it. Caching a stale "corrupt" verdict
    there would be the dangerous direction - it would quarantine the fresh file
    too - so the replacement is recorded explicitly.
    """
    reset_integrity_cache()
    database = tmp_path / "mux.db"
    database.write_bytes(b"this is not a database")
    verdicts = ["not a database"]

    monkeypatch.setattr(
        "swe_mux.sqlite_store._integrity_problem",
        lambda _path: verdicts.pop(0) if verdicts else pytest.fail("re-probed after quarantine"),
    )

    connect_or_quarantine(database, lambda: sqlite3.connect(database)).close()
    assert list(tmp_path.glob("mux.db.corrupt-*"))
    # The second store opens the replacement without probing it again, and
    # without being told the replacement is corrupt.
    assert verify_database(database) is None
    connect_or_quarantine(database, lambda: sqlite3.connect(database)).close()
    assert len(list(tmp_path.glob("mux.db.corrupt-*"))) == 1


# ----------------------------------------------- the conditional full check


def _populated_database(path: Path) -> None:
    """A real database large enough that its data pages are far from its schema."""
    db = sqlite3.connect(path)
    try:
        db.execute("CREATE TABLE payload(id INTEGER PRIMARY KEY, body TEXT)")
        db.executemany(
            "INSERT INTO payload(body) VALUES(?)", [("x" * 512,) for _ in range(2000)]
        )
        db.commit()
    finally:
        db.close()


def _corrupt_an_interior_page(path: Path) -> None:
    """Overwrite one data page, leaving the header and the schema intact."""
    probe = sqlite3.connect(path)
    try:
        page_size = int(probe.execute("PRAGMA page_size").fetchone()[0])
    finally:
        probe.close()
    with path.open("r+b") as handle:
        handle.seek(page_size * 10)
        handle.write(b"\xff" * page_size)


def test_the_startup_path_never_runs_the_full_check(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """The point of the change: no start pays the full-file read, for any reason.

    The full probe's cost is the size of the file - measured 77.67s against a
    cold 3.36 GB `mux.db`, 91% of that start - and it answers a question about
    the storage layer rather than about this start, so every start pays the
    milliseconds header-and-schema probe and schedules the rest.
    """
    reset_integrity_cache()
    database = tmp_path / "mux.db"
    sqlite3.connect(database).close()
    monkeypatch.setattr(
        "swe_mux.sqlite_store._integrity_problem",
        lambda *_args: pytest.fail("the full probe ran on the startup path"),
    )
    for kwargs in (
        {},  # no record at all
        {"predecessor_died_uncleanly": True},
        {"full_verification_interval_seconds": 0.0},
    ):
        reset_integrity_cache()
        preparation = prepare_database(database, **kwargs)  # type: ignore[arg-type]
        assert preparation.mode == "light"
        assert preparation.problem is None
        assert preparation.full_check_owed
    # And the light verdict feeds the same per-file cache, so no store re-probes.
    record_database_verified(database)
    reset_integrity_cache()
    assert not prepare_database(database).full_check_owed
    for _ in range(11):
        connect_or_quarantine(database, lambda: sqlite3.connect(database)).close()


def test_an_unclean_predecessor_death_buys_promptness_rather_than_a_blocked_start(
    tmp_path: Path,
) -> None:
    """The signal is kept; what it buys changed.

    Under `journal_mode=wal` with `synchronous=FULL` a process or OS crash
    cannot corrupt the file, so an unclean death is not evidence about the
    bytes. It is still the cheapest correlate of "something happened to this
    machine", so it moves the scheduled check to the front of the queue instead
    of onto the critical path.
    """
    reset_integrity_cache()
    database = tmp_path / "mux.db"
    sqlite3.connect(database).close()
    record_database_verified(database)

    clean = prepare_database(database)
    assert not clean.full_check_owed  # a fresh record, cleanly handed over

    reset_integrity_cache()
    unclean = prepare_database(database, predecessor_died_uncleanly=True)
    assert unclean.full_check_owed
    assert unclean.reason == "the previous daemon died uncleanly"
    assert unclean.full_check_delay_seconds == NEAR_TERM_VERIFICATION_DELAY_SECONDS
    # Prompt, but never as prompt as the ordinary schedule is patient.
    assert NEAR_TERM_VERIFICATION_DELAY_SECONDS < ROUTINE_VERIFICATION_DELAY_SECONDS


def test_the_settings_that_make_an_unclean_death_survivable_are_the_ones_in_force(
    tmp_path: Path,
) -> None:
    """The argument above rests on two pragmas; assert them rather than trust them.

    If `mux.db` were ever opened with `synchronous=OFF`, or out of WAL, a crash
    really could leave a torn file and deferring the check would be a
    weakening. This is the test that would go red first.
    """
    database = tmp_path / "mux.db"
    connection = connect_or_quarantine(database, lambda: sqlite3.connect(database))
    try:
        connection.execute("PRAGMA journal_mode=WAL").fetchone()
        assert str(connection.execute("PRAGMA journal_mode").fetchone()[0]).lower() == "wal"
        # 2 is FULL, SQLite's default. Anything lower is a deliberate durability
        # trade and invalidates the deferral argument in `sqlite_store`.
        assert int(connection.execute("PRAGMA synchronous").fetchone()[0]) == 2
    finally:
        connection.close()


def test_a_stale_record_owes_a_check_and_a_pass_renews_it(tmp_path: Path) -> None:
    reset_integrity_cache()
    database = tmp_path / "mux.db"
    _populated_database(database)
    record_database_verified(database, now=time.time() - 2 * FULL_VERIFICATION_INTERVAL_SECONDS)
    preparation = prepare_database(database)
    assert preparation.full_check_owed
    assert preparation.full_check_delay_seconds == ROUTINE_VERIFICATION_DELAY_SECONDS
    assert "beyond the" in preparation.reason
    # Running the check the start scheduled renews the record, so the next start
    # is back inside the window and owes nothing.
    result = run_full_verification(database)
    assert result.problem is None
    record_database_verified(database)
    reset_integrity_cache()
    assert not prepare_database(database).full_check_owed


def test_a_future_dated_record_is_distrusted(tmp_path: Path) -> None:
    """A clock rolled backwards must fail towards checking, not towards skipping."""
    reset_integrity_cache()
    database = tmp_path / "mux.db"
    sqlite3.connect(database).close()
    record_database_verified(database, now=time.time() + FULL_VERIFICATION_INTERVAL_SECONDS)
    preparation = prepare_database(database)
    assert preparation.full_check_owed
    assert "the clock moved" in preparation.reason
    # And a disabled window (interval 0) owes a check on every start.
    reset_integrity_cache()
    preparation = prepare_database(database, full_verification_interval_seconds=0.0)
    assert preparation.full_check_owed
    assert preparation.reason == "the verification interval is disabled"


def test_gross_corruption_is_quarantined_by_the_startup_probe(tmp_path: Path) -> None:
    """The light probe is not a free pass: a file that cannot serve its own
    schema is caught and quarantined before any store opens it, exactly the
    crash-loop class `connect_or_quarantine` was built for."""
    reset_integrity_cache()
    database = tmp_path / "mux.db"
    sqlite3.connect(database).close()
    record_database_verified(database)
    database.write_bytes(b"this is not a database at all")
    preparation = prepare_database(database)
    assert preparation.mode == "light"
    assert preparation.problem is not None
    connect_or_quarantine(database, lambda: sqlite3.connect(database)).close()
    assert list(tmp_path.glob("mux.db.corrupt-*"))


def test_deep_corruption_is_found_in_the_background_and_quarantined_next_start(
    tmp_path: Path,
) -> None:
    """A deliberately corrupted interior page, against the real probes.

    This is the remediation path the deferral creates, stated as assertions
    rather than left implicit. The light probe passes this file and the daemon
    serves; the background full check finds it and *records* the verdict rather
    than quarantining under live stores; the next start reads the record and
    quarantines before the first store connects.
    """
    reset_integrity_cache()
    database = tmp_path / "mux.db"
    _populated_database(database)
    _corrupt_an_interior_page(database)

    # 1. The start sees nothing and schedules the walk.
    assert _light_integrity_problem(database) is None
    preparation = prepare_database(database)
    assert preparation.mode == "light"
    assert preparation.problem is None
    assert preparation.full_check_owed

    # 2. The background walk finds it and records the verdict.
    result = run_full_verification(database)
    assert result.problem is not None
    record_database_problem(database, result.problem)
    assert read_verification_record(database).failed

    # 3. The next start acts on the record, before any store opens the file.
    reset_integrity_cache()
    next_start = prepare_database(database)
    assert next_start.mode == "quarantine"
    assert next_start.problem == result.problem
    connect_or_quarantine(database, lambda: sqlite3.connect(database)).close()
    assert list(tmp_path.glob("mux.db.corrupt-*"))

    # 4. The quarantine's replacement is recorded verified: this process created
    #    it, so the start after that owes nothing and quarantines nothing.
    assert read_verification_record(database).verified_at is not None
    reset_integrity_cache()
    healthy = prepare_database(database)
    assert healthy.mode == "light"
    assert healthy.problem is None
    assert not healthy.full_check_owed


def test_a_passing_background_check_never_leaves_a_failure_recorded(tmp_path: Path) -> None:
    """The record is the remediation trigger, so a stale failure would quarantine
    a healthy file. A pass must overwrite one."""
    database = tmp_path / "mux.db"
    _populated_database(database)
    record_database_problem(database, "quick_check: something was wrong once")
    assert read_verification_record(database).failed
    result = run_full_verification(database)
    assert result.problem is None
    record_database_verified(database)
    record = read_verification_record(database)
    assert not record.failed
    assert record.verified_at is not None


def test_the_prefetch_reads_the_file_and_changes_no_verdict(tmp_path: Path) -> None:
    """Prefetch is the 78s-to-12s half of the fix and must stay pure.

    It exists because a cold `quick_check` walks 4 KiB pages at queue depth 1 -
    measured 43 MB/s on an NVMe device rated in gigabytes per second - so
    reading the file once, sequentially, lets the walk run at its warm cost. It
    drops every byte, so it can only ever change what a verdict costs.
    """
    healthy = tmp_path / "healthy.db"
    _populated_database(healthy)
    assert prefetch_database(healthy) >= 0.0
    assert run_full_verification(healthy).problem is None

    corrupt = tmp_path / "corrupt.db"
    _populated_database(corrupt)
    _corrupt_an_interior_page(corrupt)
    assert run_full_verification(corrupt).problem is not None

    # A file it cannot read is a slower check, never a failed one.
    assert prefetch_database(tmp_path / "absent.db") == 0.0


def test_a_cancelled_full_check_is_not_a_corruption_verdict(tmp_path: Path) -> None:
    """Being asked to stop is not evidence about the file.

    `Connection.interrupt()` surfaces as an ordinary `DatabaseError`, so the
    naive reading of this path quarantines a healthy database every time the
    daemon shuts down mid-check.
    """
    database = tmp_path / "mux.db"
    _populated_database(database)
    control = VerificationControl()
    control.cancel()  # cancelled before it starts: the worst-timed case
    result = run_full_verification(database, control)
    assert result.cancelled
    assert result.problem is None
    # And the file is untouched, so an ordinary check still passes it.
    assert run_full_verification(database).problem is None


@pytest.mark.asyncio
async def test_the_background_loop_records_what_it_finds(tmp_path: Path, caplog: Any) -> None:
    """End to end through the daemon's own task, both verdicts."""
    healthy = tmp_path / "healthy.db"
    _populated_database(healthy)
    with caplog.at_level(logging.INFO, logger="swe_mux.server"):
        await _database_integrity_loop(healthy, 0.0, "a test asked for it")
    assert read_verification_record(healthy).verified_at is not None
    assert "background full check" in caplog.text

    corrupt = tmp_path / "corrupt.db"
    _populated_database(corrupt)
    _corrupt_an_interior_page(corrupt)
    caplog.clear()
    with caplog.at_level(logging.ERROR, logger="swe_mux.server"):
        await _database_integrity_loop(corrupt, 0.0, "a test asked for it")
    record = read_verification_record(corrupt)
    assert record.failed
    assert "FOUND A PROBLEM" in caplog.text


@pytest.mark.asyncio
async def test_cancelling_the_background_loop_records_nothing_and_returns_promptly(
    tmp_path: Path,
) -> None:
    """The shutdown trap, asserted.

    `asyncio.to_thread` does not cancel its worker: the loop joins it at
    `shutdown_default_executor`, after the daemon has already reported a clean
    stop, so a walk that ignored cancellation would read as up to 78s of
    unexplained silence. And a cancelled walk must not be mistaken for a
    corruption verdict - recording one would quarantine a healthy database on
    the next start because the user closed the app.
    """
    database = tmp_path / "mux.db"
    _populated_database(database)
    task = asyncio.create_task(_database_integrity_loop(database, 60.0, "a test asked for it"))
    await until(lambda: not task.done() and task.get_name() is not None)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    # Nothing was written on either side of the verdict.
    assert not verification_record_path(database).exists()
    # And the database it abandoned is still perfectly readable.
    assert run_full_verification(database).problem is None


# ------------------------------------------------------------ the bound socket


def test_publish_writes_into_a_frozen_application() -> None:
    """Pins the one line coupled to an aiohttp internal.

    The daemon publishes handles after its runner has started, which aiohttp
    deprecates. If an upgrade moves `_state`, this fails here rather than
    silently dropping every runtime handle into a warning nobody reads.
    """
    app = web.Application()
    app.freeze()
    publish(app, {keys.HISTORY: "a handle"})
    assert app[keys.HISTORY] == "a handle"


def test_only_health_and_the_app_shell_serve_during_startup() -> None:
    assert startup_open("/api/health") is True
    assert startup_open("/assets/index-abc123.css") is True
    assert startup_open("/icons/icon-192.png") is True
    # A route that needs a runtime handle must not be reached before there is one.
    assert startup_open("/api/sessions") is False
    assert startup_open("/api/projects") is False
    assert startup_open("/events") is False
    assert startup_open("/pty/abc") is False
    assert "/api/health" in STARTUP_OPEN_PATHS


async def test_the_daemon_answers_its_phase_while_the_runtime_is_still_building(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """The whole point: a client during startup gets an answer, not a refusal."""
    release = asyncio.Event()
    real_prepare = prepare_database

    def blocking_prepare(path: Path, **kwargs: Any) -> Any:
        # Runs inside `asyncio.to_thread`, so waiting here holds the build at a
        # named phase while leaving the event loop free to serve - which is the
        # property that makes a staged health answer possible at all.
        asyncio.run_coroutine_threadsafe(release.wait(), loop).result(timeout=10)
        return real_prepare(path, **kwargs)

    monkeypatch.setattr("swe_mux.server.prepare_database", blocking_prepare)
    loop = asyncio.get_running_loop()
    # `reconcile_external_history=False` keeps this in-process daemon off the
    # developer's real `~/.claude/projects`: the startup scan is on by default,
    # reads the real user home, and has nothing to do with the startup gate.
    app = create_app(
        Config(
            data_dir=tmp_path / "data",
            pty_supervisor_enabled=False,
            reconcile_external_history=False,
        )
    )
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        # The build reaches `database-integrity` through earlier phases that do
        # real work off the loop (`static-precompress` reads and CRCs the whole
        # served frontend tree), so the phase this test holds open is not
        # necessarily the one in flight the instant the listener opens. Waiting
        # for the condition rather than asserting on a race is the rule CLAUDE.md
        # records: a fixed sleep here would redden the gate over machine load.
        timeline: StartupTimeline = app[keys.STARTUP]
        await until(
            lambda: timeline.snapshot()["phase"] == "database-integrity",
            what="the build to reach database-integrity",
        )
        health = await client.get("/api/health")
        assert health.status == 503
        payload = await health.json()
        assert payload["ok"] is False
        assert payload["status"] == "starting"
        assert payload["phase"] == "database-integrity"

        # Every other route is refused with the same answer rather than a 500
        # from a handler reaching for a handle that does not exist yet.
        refused = await client.get("/api/sessions")
        assert refused.status == 503
        assert refused.headers["Retry-After"]
        body = await refused.json()
        assert body["code"] == "daemon_starting"
        assert body["phase"] == "database-integrity"

        loop.call_soon_threadsafe(release.set)
        await wait_runtime_ready(app)

        ready = await client.get("/api/health")
        assert ready.status == 200
        ready_payload = await ready.json()
        assert ready_payload["ok"] is True
        assert ready_payload["status"] == "ready"
        # The phases that made up the start are readable from the answer itself.
        assert {item["name"] for item in ready_payload["phases"]} >= {
            "database-integrity",
            "stores",
            "projects",
        }
        assert (await client.get("/api/sessions")).status == 200
    finally:
        await client.close()


async def test_a_build_that_fails_stops_the_daemon(tmp_path: Path, monkeypatch: Any) -> None:
    """A half-alive daemon serving 503 forever is worse than the crash it replaced.

    While the build ran inline, an exception propagated out of `AppRunner.setup()`
    and the process died - which the desktop shell and the redeploy script both
    already handle. The background build has to preserve that ending.
    """
    monkeypatch.setattr(
        "swe_mux.server.prepare_database",
        lambda _path, **_kwargs: (_ for _ in ()).throw(RuntimeError("disk is on fire")),
    )
    stop_event = asyncio.Event()
    app = create_app(
        Config(data_dir=tmp_path / "data", pty_supervisor_enabled=False),
        desktop_shutdown_event=stop_event,
    )
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        with pytest.raises(RuntimeError, match="disk is on fire"):
            await wait_runtime_ready(app)
        assert stop_event.is_set()
        health = await client.get("/api/health")
        assert health.status == 503
        payload = await health.json()
        # A probe reads a reason rather than an indefinite stall.
        assert payload["status"] == "failed"
        assert payload["error"] == "disk is on fire"
    finally:
        await client.close()


async def test_shutdown_during_startup_tears_down_a_partial_runtime(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """Teardown must survive a runtime that was never finished being built.

    The build is a task now, so a shutdown can arrive mid-construction. A
    teardown that assumed every handle existed would raise on the first missing
    one and leak everything after it.
    """
    started = asyncio.Event()
    hold = asyncio.Event()
    real_prepare = prepare_database

    async def slow_projects(self: Any) -> None:
        started.set()
        await hold.wait()

    monkeypatch.setattr("swe_mux.projects.ProjectManager.start", slow_projects)
    app = create_app(Config(data_dir=tmp_path / "data", pty_supervisor_enabled=False))
    client = TestClient(TestServer(app))
    await client.start_server()
    await asyncio.wait_for(started.wait(), timeout=10)
    # The stores exist; nothing after `projects` does. Closing here exercises
    # exactly the partial state.
    assert app.get(keys.HISTORY) is None
    await client.close()
    assert real_prepare is prepare_database
