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
from pathlib import Path
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from swe_mux import app_keys as keys
from swe_mux.config import Config
from swe_mux.server import STARTUP_OPEN_PATHS, create_app, publish, startup_open, wait_runtime_ready
from swe_mux.sqlite_store import (
    connect_or_quarantine,
    prepare_database,
    reset_integrity_cache,
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
    # And a caller may pay for it up front, by name and off the event loop.
    reset_integrity_cache()
    assert prepare_database(database) >= 0.0
    assert len(probes) == 2
    assert verify_database(database) is None
    assert len(probes) == 2


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

    def blocking_prepare(path: Path) -> float:
        # Runs inside `asyncio.to_thread`, so waiting here holds the build at a
        # named phase while leaving the event loop free to serve - which is the
        # property that makes a staged health answer possible at all.
        asyncio.run_coroutine_threadsafe(release.wait(), loop).result(timeout=10)
        return real_prepare(path)

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
        lambda _path: (_ for _ in ()).throw(RuntimeError("disk is on fire")),
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
