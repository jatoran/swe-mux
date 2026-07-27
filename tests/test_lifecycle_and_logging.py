"""Death forensics (lifecycle ledger/heartbeat) and rolling-log plumbing."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from swe_mux import lifecycle
from swe_mux.config import load_config
from swe_mux.logsetup import current_log_level, normalize_level, set_log_level
from swe_mux.server import get_log_level, put_log_level
from swe_mux.subprocess_flags import popen_outside_job


def read_ledger(tmp_path: Path) -> str:
    path = tmp_path / lifecycle.LEDGER_NAME
    return path.read_text(encoding="utf-8") if path.exists() else ""


def test_daemon_start_writes_heartbeat_and_ledger(tmp_path: Path) -> None:
    lifecycle.daemon_started(tmp_path, logging.getLogger("test"))
    record = lifecycle.read_heartbeat(tmp_path)
    assert record is not None
    assert record["pid"] == os.getpid()
    assert record["clean_exit"] is False
    assert f"daemon pid {os.getpid()} started" in read_ledger(tmp_path)


def test_unclean_predecessor_death_is_reported(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # A record from a pid that no longer exists and never wrote a clean exit is
    # exactly what an external kill (Job close, taskkill) leaves behind.
    dead_pid = 4_000_000_000  # far outside any real pid range
    (tmp_path / lifecycle.HEARTBEAT_NAME).write_text(
        json.dumps(
            {"pid": dead_pid, "started_at": 1.0, "heartbeat_at": 2.0, "clean_exit": False}
        ),
        encoding="utf-8",
    )
    with caplog.at_level(logging.WARNING, logger="test"):
        lifecycle.daemon_started(tmp_path, logging.getLogger("test"))
    assert any("died without a clean shutdown" in r.message for r in caplog.records)
    assert "died without a clean shutdown" in read_ledger(tmp_path)
    record = lifecycle.read_heartbeat(tmp_path)
    assert record is not None and record["pid"] == os.getpid()


def test_clean_exit_suppresses_the_death_report(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    lifecycle.daemon_started(tmp_path, logging.getLogger("test"))
    lifecycle.daemon_clean_exit(tmp_path, "detach")
    record = lifecycle.read_heartbeat(tmp_path)
    assert record is not None
    assert record["clean_exit"] is True and record["intent"] == "detach"
    # Fake the pid being gone: a clean-exit record must never be reported.
    record["pid"] = 4_000_000_000
    (tmp_path / lifecycle.HEARTBEAT_NAME).write_text(json.dumps(record), encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="test"):
        lifecycle.daemon_started(tmp_path, logging.getLogger("test"))
    assert not any("died without a clean shutdown" in r.message for r in caplog.records)


def test_exiting_daemon_does_not_clobber_a_live_successor(tmp_path: Path) -> None:
    # The successor's record belongs to a live pid that is not ours; the
    # predecessor's heartbeat/clean-exit writes must leave it alone.
    successor_pid = os.getppid()  # a real, live, foreign pid
    successor = {
        "pid": successor_pid,
        "started_at": 1.0,
        "heartbeat_at": 2.0,
        "clean_exit": False,
    }
    (tmp_path / lifecycle.HEARTBEAT_NAME).write_text(json.dumps(successor), encoding="utf-8")
    lifecycle.heartbeat(tmp_path)
    lifecycle.daemon_clean_exit(tmp_path, "quit")
    record = lifecycle.read_heartbeat(tmp_path)
    assert record is not None
    assert record["pid"] == successor_pid
    assert record["clean_exit"] is False


def test_ledger_rotates_instead_of_growing_forever(tmp_path: Path) -> None:
    path = tmp_path / lifecycle.LEDGER_NAME
    path.write_bytes(b"x" * (lifecycle.LEDGER_MAX_BYTES + 1))
    lifecycle.ledger(tmp_path, "after rotation")
    assert path.with_suffix(".log.1").exists()
    assert "after rotation" in path.read_text(encoding="utf-8")


def test_log_level_normalization() -> None:
    assert normalize_level(" debug ") == "DEBUG"
    with pytest.raises(ValueError):
        normalize_level("verbose")


def test_set_log_level_applies_to_the_root_logger() -> None:
    root = logging.getLogger()
    before = root.level
    try:
        assert set_log_level("debug") == "DEBUG"
        assert root.level == logging.DEBUG
        assert current_log_level() == "DEBUG"
    finally:
        root.setLevel(before)


async def test_log_level_endpoint_roundtrip() -> None:
    app = web.Application()
    app.add_routes(
        [
            web.get("/api/debug/log-level", get_log_level),
            web.post("/api/debug/log-level", put_log_level),
        ]
    )
    root = logging.getLogger()
    before = root.level
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        response = await client.post("/api/debug/log-level", json={"level": "debug"})
        assert response.status == 200
        assert (await response.json())["level"] == "DEBUG"
        response = await client.get("/api/debug/log-level")
        assert (await response.json())["level"] == "DEBUG"
    finally:
        await client.close()
        root.setLevel(before)


def test_config_rejects_unknown_log_level(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('log_level = "silly"\n', encoding="utf-8")
    with pytest.raises(ValueError):
        load_config(path)


def test_popen_outside_job_spawns_and_falls_back(tmp_path: Path) -> None:
    # Whichever branch runs (breakaway granted or denied → fallback), the child
    # must spawn and complete normally.
    process = popen_outside_job(
        [sys.executable, "-c", "print('ok')"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        cwd=str(tmp_path),
    )
    output, _ = process.communicate(timeout=30)
    assert process.returncode == 0
    assert b"ok" in output
