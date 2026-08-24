"""The daemon's own diagnosability: what a log line carries, and what a 404 means.

Four separate gaps are pinned here, because each of them made a real incident
harder to read than it needed to be.

- **The sink dropped every `extra` field.** Call sites had been passing
  correlation data through `extra=` for a long time and the format string threw
  all of it away, so the instrumentation existed and produced nothing.
- **Nothing correlated a log line to the request that caused it.** Two
  concurrent requests interleaved in `daemon.log` with no way to separate them.
- **Every `KeyError` was a 404.** A deliberate "no such session" and an
  accidental dictionary miss inside a handler were reported identically - the
  bug got a confident 404, no log line, and no traceback.
- **A restart lost the writes that would have explained it, and reported a crash
  that never happened.** Both are properties of the *handoff* between two
  daemons, so both are tested as a handoff rather than as a single process.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
import threading
import time
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from swe_mux import lifecycle
from swe_mux.__main__ import wait_for_predecessor_exit
from swe_mux.errors import NotFound
from swe_mux.http_support import ACCESS_LOG_FORMAT, REQUEST_ID_HEADER, request_id
from swe_mux.logsetup import (
    DAEMON_LOG_NAME,
    MAX_EXTRA_VALUE_CHARS,
    CorrelationFilter,
    StructuredFormatter,
    bound_request_id,
    current_request_id,
    setup_daemon_logging,
    valid_request_id,
)
from swe_mux.server import correlation_middleware, error_middleware
from swe_mux.sqlite_store import (
    begin_shutdown_drain,
    database_operation_lock,
    drain_remaining_ms,
    end_shutdown_drain,
    is_locked_error,
    run_sqlite_operation,
)

# ------------------------------------------------------------------ the sink


def record(message: str = "hello", **fields: object) -> logging.LogRecord:
    made = logging.LogRecord(
        name="swe_mux.test", level=logging.INFO, pathname=__file__, lineno=1, msg=message,
        args=(), exc_info=None,
    )
    for name, value in fields.items():
        setattr(made, name, value)
    return made


def rendered(message: str = "hello", **fields: object) -> str:
    return StructuredFormatter("%(message)s").format(record(message, **fields))


def test_extra_fields_reach_the_sink_as_key_value_pairs() -> None:
    line = rendered("git_mutation_completed", operation="merge", git_code=0)
    assert line == "git_mutation_completed operation=merge git_code=0"


def test_a_record_without_extras_is_untouched() -> None:
    assert rendered("plain") == "plain"


def test_values_that_would_forge_a_field_boundary_are_quoted() -> None:
    line = rendered("x", diagnostic="fatal: not a git repository", empty="", equals="a=b")
    assert line == 'x diagnostic="fatal: not a git repository" empty="" equals="a=b"'
    # And the whole record stays one line, which is what makes the file parsable.
    assert "\n" not in rendered("x", output="first\nsecond")


def test_extras_round_trip_back_into_the_values_they_came_from() -> None:
    fields = {
        "root": r"D:\PROJECTS\swe mux",
        "code": 128,
        "ratio": 0.5,
        "ok": False,
        "missing": None,
    }
    line = rendered("event", **fields)
    parsed: dict[str, object] = {}
    for pair in line.split(" ", 1)[1].split(" "):
        if "=" not in pair or pair.startswith('"'):
            continue
        key, _, raw = pair.partition("=")
        parsed[key] = raw
    # The two unambiguous scalars survive verbatim; the Windows path is quoted
    # (it holds a space and a backslash) and decodes back to exactly itself.
    assert parsed["code"] == "128"
    assert parsed["ok"] == "false"
    quoted = line[line.index('root=') + len("root=") :]
    assert json.loads(quoted[: quoted.index('" ') + 1]) == fields["root"]


def test_a_structured_value_is_serialized_rather_than_repred() -> None:
    line = rendered("event", detail={"phase": "startup", "count": 2})
    assert '"{\\"phase\\":\\"startup\\",\\"count\\":2}"' in line


def test_an_oversized_value_is_truncated_rather_than_flooding_the_file() -> None:
    line = rendered("event", output="x" * (MAX_EXTRA_VALUE_CHARS * 3))
    assert len(line) < MAX_EXTRA_VALUE_CHARS + 100
    assert line.endswith('...')


def test_the_traceback_stays_after_the_fields_not_before_them() -> None:
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        import sys

        made = record("failed", session="s1")
        made.exc_info = sys.exc_info()
    line = StructuredFormatter("%(message)s").format(made)
    first, _, rest = line.partition("\n")
    assert first == "failed session=s1"
    assert "RuntimeError: boom" in rest


def test_formatting_a_record_twice_does_not_double_the_fields() -> None:
    formatter = StructuredFormatter("%(message)s")
    made = record("event", session="s1")
    assert formatter.format(made) == formatter.format(made) == "event session=s1"


def test_percent_style_arguments_still_interpolate_alongside_fields() -> None:
    made = logging.LogRecord(
        name="swe_mux.test", level=logging.INFO, pathname=__file__, lineno=1,
        msg="ready in %.1fs", args=(1.25,), exc_info=None,
    )
    made.session = "s1"
    assert StructuredFormatter("%(message)s").format(made) == "ready in 1.2s session=s1"


# ------------------------------------------------------------- correlation id


def test_the_filter_stamps_the_bound_request_id() -> None:
    correlation = CorrelationFilter()
    with bound_request_id("abc123"):
        made = record("event")
        correlation.filter(made)
        assert made.request_id == "abc123"  # type: ignore[attr-defined]
    outside = record("event")
    correlation.filter(outside)
    assert not hasattr(outside, "request_id")


def test_the_request_id_is_rendered_first_however_it_was_added() -> None:
    line = rendered("event", session="s1", request_id="abc123")
    assert line == "event request_id=abc123 session=s1"


def test_only_a_bounded_well_formed_inbound_id_is_adopted() -> None:
    assert valid_request_id("7f3c-1a.b:2")
    assert not valid_request_id("")
    assert not valid_request_id("a" * 65)
    assert not valid_request_id("has space")
    assert not valid_request_id("new\nline")


def test_the_access_format_carries_the_response_header() -> None:
    assert f"request_id=%{{{REQUEST_ID_HEADER}}}o" in ACCESS_LOG_FORMAT


def test_the_configured_daemon_writes_fields_and_ids_into_daemon_log(tmp_path: Path) -> None:
    """The property the other tests approximate, asserted against the real file.

    A filter on the wrong object, or a formatter on the wrong handler, would
    leave every unit test above passing and `daemon.log` exactly as it was.
    """
    root = logging.getLogger()
    access = logging.getLogger("aiohttp.access")
    saved_handlers, saved_level = list(root.handlers), root.level
    saved_access = list(access.handlers)
    root.handlers.clear()
    try:
        setup_daemon_logging(tmp_path, "INFO")
        with bound_request_id("trace-9"):
            logging.getLogger("swe_mux.session").info(
                "session_spawned", extra={"session": "s1", "harness": "claude"}
            )
        logging.getLogger("swe_mux.session").info("startup_phase", extra={"phase": "adopt"})
        for handler in root.handlers:
            handler.flush()
        written = (tmp_path / DAEMON_LOG_NAME).read_text(encoding="utf-8").splitlines()
    finally:
        for handler in (*root.handlers, *access.handlers[len(saved_access) :]):
            handler.close()
        root.handlers[:] = saved_handlers
        access.handlers[:] = saved_access
        root.setLevel(saved_level)
    assert written[0].endswith(
        "swe_mux.session: session_spawned request_id=trace-9 session=s1 harness=claude"
    )
    # Outside a request there is no id at all, rather than an empty one.
    assert written[1].endswith("swe_mux.session: startup_phase phase=adopt")


# ---------------------------------------------------- middleware, end to end

log = logging.getLogger("swe_mux.test_diagnosability")


@pytest.fixture
def correlated(caplog: pytest.LogCaptureFixture) -> pytest.LogCaptureFixture:
    """`caplog` wired the way the daemon wires `daemon.log`.

    The filter belongs on the handler, not on a logger: `Logger.handle` consults
    only the filters of the logger the call was made on, so one installed on
    root would stamp nothing a submodule logs. Attaching it to caplog's handler
    reproduces `rotating_handler` exactly, which is also what makes these
    assertions evidence about production rather than about the test.
    """
    correlation = CorrelationFilter()
    caplog.handler.addFilter(correlation)
    try:
        yield caplog
    finally:
        caplog.handler.removeFilter(correlation)


async def _ok(request: web.Request) -> web.Response:
    log.info("handler ran", extra={"path": request.path})
    return web.json_response({"seen": request_id(request)})


async def _deliberate_miss(request: web.Request) -> web.Response:
    raise NotFound("sess-abc-secret", kind="session")


async def _accidental_key_error(request: web.Request) -> web.Response:
    payload: dict[str, str] = {}
    return web.json_response({"value": payload["a key nobody wrote"]})


async def _accidental_type_error(request: web.Request) -> web.Response:
    return web.json_response({"value": len(4)})  # type: ignore[arg-type]


async def _bad_request(request: web.Request) -> web.Response:
    raise ValueError("limit must be a positive integer")


async def _refused(request: web.Request) -> web.Response:
    raise web.HTTPForbidden(text="nope")


async def _background(request: web.Request) -> web.Response:
    """A handler whose real work outlives its own response, as many do."""
    started = asyncio.Event()

    async def later() -> None:
        await asyncio.to_thread(log.info, "worker ran")
        log.info("task ran")
        started.set()

    task = asyncio.create_task(later())
    await started.wait()
    await task
    return web.json_response({"ok": True})


def build_app() -> web.Application:
    app = web.Application(middlewares=[correlation_middleware, error_middleware])
    app.router.add_get("/ok", _ok)
    app.router.add_get("/missing", _deliberate_miss)
    app.router.add_get("/bug/key", _accidental_key_error)
    app.router.add_get("/bug/type", _accidental_type_error)
    app.router.add_get("/bad", _bad_request)
    app.router.add_get("/refused", _refused)
    app.router.add_get("/background", _background)
    return app


async def client_for() -> TestClient:
    client = TestClient(TestServer(build_app()))
    await client.start_server()
    return client


@pytest.mark.asyncio
async def test_every_response_carries_a_request_id() -> None:
    client = await client_for()
    try:
        response = await client.get("/ok")
        assert response.status == 200
        stamped = response.headers[REQUEST_ID_HEADER]
        assert valid_request_id(stamped)
        # The handler saw the same id the caller was given, so a body that
        # quotes it and a header that carries it can never disagree.
        assert (await response.json())["seen"] == stamped
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_a_well_formed_inbound_id_is_adopted_and_a_bad_one_replaced() -> None:
    client = await client_for()
    try:
        adopted = await client.get("/ok", headers={REQUEST_ID_HEADER: "trace-0001"})
        assert adopted.headers[REQUEST_ID_HEADER] == "trace-0001"
        replaced = await client.get("/ok", headers={REQUEST_ID_HEADER: "not a valid id"})
        assert replaced.headers[REQUEST_ID_HEADER] != "not a valid id"
        assert valid_request_id(replaced.headers[REQUEST_ID_HEADER])
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_a_refusal_that_never_reaches_a_handler_is_correlated_too() -> None:
    client = await client_for()
    try:
        response = await client.get("/refused")
        assert response.status == 403
        assert valid_request_id(response.headers[REQUEST_ID_HEADER])
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_a_deliberate_not_found_is_a_404_that_does_not_echo_the_key(
    correlated: pytest.LogCaptureFixture,
) -> None:
    client = await client_for()
    try:
        with correlated.at_level(logging.DEBUG, logger="swe_mux.server"):
            response = await client.get("/missing")
        assert response.status == 404
        body = await response.json()
        assert body == {"error": "no such session", "code": "not_found", "kind": "session"}
        assert "sess-abc-secret" not in json.dumps(body)
        # Out of the body and into the log, with the request that asked for it.
        translated = [r for r in correlated.records if r.message.startswith("request_not_found")]
        assert translated and "sess-abc-secret" in translated[0].message
        assert "path=/missing" in translated[0].message
        assert getattr(translated[0], "request_id", "") == response.headers[REQUEST_ID_HEADER]
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_an_accidental_key_error_is_a_500_with_a_traceback(
    correlated: pytest.LogCaptureFixture,
) -> None:
    """The whole point of the typed exception: this used to be a silent 404."""
    client = await client_for()
    try:
        with correlated.at_level(logging.ERROR, logger="swe_mux.server"):
            response = await client.get("/bug/key")
        assert response.status == 500
        assert await response.json() == {"error": "internal server error"}
        failures = [r for r in correlated.records if r.levelno >= logging.ERROR]
        assert failures and failures[0].exc_info is not None
        assert "a key nobody wrote" in logging.Formatter().formatException(failures[0].exc_info)
        assert "path=/bug/key" in failures[0].message
        assert getattr(failures[0], "request_id", "") == response.headers[REQUEST_ID_HEADER]
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_a_bare_type_error_is_a_500_rather_than_a_400(
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = await client_for()
    try:
        with caplog.at_level(logging.ERROR, logger="swe_mux.server"):
            response = await client.get("/bug/type")
        assert response.status == 500
        assert any(r.exc_info is not None for r in caplog.records)
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_route_validation_still_answers_400_and_says_why(
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = await client_for()
    try:
        with caplog.at_level(logging.DEBUG, logger="swe_mux.server"):
            response = await client.get("/bad")
        assert response.status == 400
        assert (await response.json())["error"] == "limit must be a positive integer"
        rejected = [r for r in caplog.records if r.message.startswith("request_rejected")]
        assert rejected and "error_type=ValueError" in rejected[0].message
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_work_a_handler_spawns_stays_correlated_with_it(
    correlated: pytest.LogCaptureFixture,
) -> None:
    """A task and a thread the handler starts inherit the context, so both lines
    carry the id - which is what makes the *slow* half of a request findable."""
    client = await client_for()
    try:
        with correlated.at_level(logging.INFO, logger="swe_mux.test_diagnosability"):
            response = await client.get("/background")
        stamped = response.headers[REQUEST_ID_HEADER]
        by_message = {r.message: getattr(r, "request_id", "") for r in correlated.records}
        assert by_message.get("task ran") == stamped
        assert by_message.get("worker ran") == stamped
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_two_concurrent_requests_do_not_share_an_id() -> None:
    client = await client_for()
    try:
        first, second = await asyncio.gather(client.get("/ok"), client.get("/ok"))
        assert first.headers[REQUEST_ID_HEADER] != second.headers[REQUEST_ID_HEADER]
    finally:
        await client.close()


def test_the_id_does_not_leak_out_of_its_block() -> None:
    assert current_request_id() == ""
    with bound_request_id("outer"):
        with bound_request_id("inner"):
            assert current_request_id() == "inner"
        assert current_request_id() == "outer"
    assert current_request_id() == ""


# ------------------------------------------------ restart-overlap durability


def _store(path: Path) -> sqlite3.Connection:
    db = sqlite3.connect(path, check_same_thread=False)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA busy_timeout=200")
    db.execute("CREATE TABLE IF NOT EXISTS rows(value TEXT)")
    db.commit()
    return db


class _ForeignWriter:
    """A second connection holding the writer slot, as a successor daemon does.

    A separate `sqlite3.connect` rather than a second thread on the same
    connection, because the thing being reproduced is cross-*process*
    contention: the predecessor's per-file operation lock does not exist in the
    daemon that is starting up, so nothing in this process can serialize them.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._holding = threading.Event()
        self._release = threading.Event()
        self._thread = threading.Thread(target=self._hold, daemon=True)

    def _hold(self) -> None:
        db = sqlite3.connect(self._path)
        db.execute("BEGIN IMMEDIATE")
        db.execute("INSERT INTO rows(value) VALUES('successor')")
        self._holding.set()
        self._release.wait(30)
        db.commit()
        db.close()

    def __enter__(self) -> _ForeignWriter:
        self._thread.start()
        assert self._holding.wait(10), "the foreign writer never took the lock"
        return self

    def release_after(self, seconds: float) -> None:
        threading.Timer(seconds, self._release.set).start()

    def __exit__(self, *_: object) -> None:
        self._release.set()
        self._thread.join(10)


def _write(db: sqlite3.Connection, path: Path) -> None:
    class Store:
        def record_event(self) -> None:
            def op() -> None:
                db.execute("INSERT INTO rows(value) VALUES('predecessor')")
                db.commit()

            run_sqlite_operation(db, database_operation_lock(path), op)

    Store().record_event()


def test_a_write_lost_to_a_foreign_lock_is_loud(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The D2 finding: ten writes vanished across a restart and said nothing."""
    path = tmp_path / "mux.db"
    db = _store(path)
    try:
        with _ForeignWriter(path):
            with caplog.at_level(logging.ERROR, logger="swe_mux.sqlite_store"):
                with pytest.raises(sqlite3.OperationalError) as caught:
                    _write(db, path)
        assert is_locked_error(caught.value)
        lost = [r for r in caplog.records if r.message.startswith("sqlite_write_lost")]
        assert lost, "a dropped write must name itself"
        # And it names *which* write, which is what the incident needed.
        assert getattr(lost[0], "sqlite_operation", "") == "Store.record_event"
        assert getattr(lost[0], "sqlite_draining", None) is False
    finally:
        db.close()


def test_the_shutdown_drain_waits_the_lock_out_instead_of_losing_the_write(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path = tmp_path / "mux.db"
    db = _store(path)
    begin_shutdown_drain(10.0)
    try:
        with _ForeignWriter(path) as foreign:
            foreign.release_after(0.75)
            with caplog.at_level(logging.ERROR, logger="swe_mux.sqlite_store"):
                _write(db, path)
        rows = {row[0] for row in db.execute("SELECT value FROM rows")}
        assert rows == {"successor", "predecessor"}
        assert not [r for r in caplog.records if r.message.startswith("sqlite_write_lost")]
    finally:
        end_shutdown_drain()
        db.close()


def test_the_drain_budget_is_bounded_and_reported() -> None:
    assert drain_remaining_ms() == 0
    begin_shutdown_drain(5.0)
    try:
        remaining = drain_remaining_ms()
        assert 0 < remaining <= 5000
    finally:
        end_shutdown_drain()
    assert drain_remaining_ms() == 0


def test_a_non_lock_operational_error_is_not_mistaken_for_one() -> None:
    # `history.py` reports an interrupted query this way, and a store reports a
    # missing table this way; neither is a lock and neither may be waited on.
    assert not is_locked_error(sqlite3.OperationalError("no such table: history"))
    assert not is_locked_error(ValueError("locked"))
    assert is_locked_error(sqlite3.OperationalError("database is locked"))


def test_the_successor_waits_for_a_live_predecessor_and_not_for_a_dead_one(
    tmp_path: Path,
) -> None:
    dead = 4_000_000_000  # far outside any real pid range
    (tmp_path / lifecycle.HEARTBEAT_NAME).write_text(
        json.dumps({"pid": dead, "started_at": 1.0, "heartbeat_at": 2.0, "clean_exit": False}),
        encoding="utf-8",
    )
    started = time.monotonic()
    wait_for_predecessor_exit(tmp_path, timeout_seconds=5.0)
    assert time.monotonic() - started < 1.0

    # A live pid is waited for, and the wait is bounded rather than a hang.
    lifecycle.daemon_started(tmp_path, logging.getLogger("test"))
    record = lifecycle.read_heartbeat(tmp_path) or {}
    record["pid"] = os.getppid()  # a real, live, foreign pid
    (tmp_path / lifecycle.HEARTBEAT_NAME).write_text(json.dumps(record), encoding="utf-8")
    started = time.monotonic()
    wait_for_predecessor_exit(tmp_path, timeout_seconds=0.5)
    assert 0.4 < time.monotonic() - started < 5.0


# -------------------------------------------- planned-restart lifecycle truth


def read_ledger(tmp_path: Path) -> str:
    path = tmp_path / lifecycle.LEDGER_NAME
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _predecessor_record(tmp_path: Path, **overrides: object) -> None:
    """Leave behind what a terminated predecessor leaves: no clean exit, pid gone."""
    record = lifecycle.read_heartbeat(tmp_path) or {}
    record.update({"pid": 4_000_000_000, "clean_exit": False, **overrides})
    (tmp_path / lifecycle.HEARTBEAT_NAME).write_text(json.dumps(record), encoding="utf-8")


def test_a_planned_handoff_is_not_reported_as_a_crash(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """39 false crash reports in one log; a warning right 0% of the time."""
    lifecycle.daemon_started(tmp_path, logging.getLogger("test"))
    lifecycle.planned_handoff(tmp_path, "detach")
    # The redeploy terminates the predecessor here, before its clean-exit write.
    _predecessor_record(tmp_path)

    with caplog.at_level(logging.DEBUG, logger="test"):
        lifecycle.daemon_started(tmp_path, logging.getLogger("test"))
    assert not any("died without a clean shutdown" in r.message for r in caplog.records)
    handoff = [r for r in caplog.records if "planned detach handoff" in r.message]
    assert handoff and handoff[0].levelno == logging.INFO
    assert "planned detach handoff" in read_ledger(tmp_path)


def test_an_unannounced_death_is_still_reported_as_one(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    lifecycle.daemon_started(tmp_path, logging.getLogger("test"))
    _predecessor_record(tmp_path)
    with caplog.at_level(logging.WARNING, logger="test"):
        lifecycle.daemon_started(tmp_path, logging.getLogger("test"))
    crash = [r for r in caplog.records if "died without a clean shutdown" in r.message]
    assert crash and crash[0].levelno == logging.WARNING


def test_the_successors_own_record_carries_no_inherited_plan(tmp_path: Path) -> None:
    """Otherwise one planned restart would excuse every crash after it."""
    lifecycle.daemon_started(tmp_path, logging.getLogger("test"))
    lifecycle.planned_handoff(tmp_path, "detach")
    _predecessor_record(tmp_path)
    lifecycle.daemon_started(tmp_path, logging.getLogger("test"))
    record = lifecycle.read_heartbeat(tmp_path)
    assert record is not None
    assert record["pid"] == os.getpid()
    assert record["planned_intent"] is None


def test_a_planned_handoff_does_not_clobber_a_live_successors_record(
    tmp_path: Path,
) -> None:
    """The predecessor may learn its intent after the successor has started."""
    successor = {
        "pid": os.getppid(),  # a real, live, foreign pid
        "started_at": 1.0,
        "heartbeat_at": 2.0,
        "clean_exit": False,
    }
    (tmp_path / lifecycle.HEARTBEAT_NAME).write_text(json.dumps(successor), encoding="utf-8")
    lifecycle.planned_handoff(tmp_path, "detach")
    record = lifecycle.read_heartbeat(tmp_path)
    assert record is not None and record["pid"] == os.getppid()
    assert "planned_intent" not in record
    # The ledger still records that this process was asked to hand off, because
    # that is a fact about this process and not about the record's owner.
    assert "planned detach handoff requested" in read_ledger(tmp_path)


def test_a_clean_exit_still_suppresses_everything(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    lifecycle.daemon_started(tmp_path, logging.getLogger("test"))
    lifecycle.planned_handoff(tmp_path, "detach")
    lifecycle.daemon_clean_exit(tmp_path, "detach")
    _predecessor_record(tmp_path, clean_exit=True)
    with caplog.at_level(logging.DEBUG, logger="test"):
        lifecycle.daemon_started(tmp_path, logging.getLogger("test"))
    assert not any("died without a clean shutdown" in r.message for r in caplog.records)
    assert not any("planned detach handoff" in r.message for r in caplog.records)
