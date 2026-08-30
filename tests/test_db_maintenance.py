"""Maintenance that needs exclusive ownership of `mux.db`.

`VACUUM` and a cross-file table move cannot run against a live daemon, so they
run in the successor's startup window instead - the one moment the daemon owns
the file and the predecessor has already exited. That is a deliberate exception
to "nothing that is not needed to serve the first request runs on this path",
and these are the assertions that keep the exception narrow: it happens only
when an operator asked for it, it never stops the daemon starting, and it never
leaves a standing request behind.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from swe_mux.config import Config
from swe_mux.db_maintenance import (
    OPERATION_REBUILD_TRIGRAM,
    OPERATION_VACUUM,
    VACUUM_PAGE_SIZE,
    MaintenanceRequest,
    clear_request,
    describe,
    maintenance_summary,
    read_request,
    request_path,
    run_maintenance,
    write_request,
)
from swe_mux.server import _run_pending_maintenance
from swe_mux.sqlite_store import VerificationControl


def _database_with_slack(path: Path) -> None:
    """A database with real free pages, so a VACUUM has something to reclaim."""
    db = sqlite3.connect(path)
    try:
        db.execute("CREATE TABLE payload(id INTEGER PRIMARY KEY, body TEXT)")
        db.executemany(
            "INSERT INTO payload(body) VALUES(?)", [("x" * 2048,) for _ in range(4000)]
        )
        db.commit()
        db.execute("DELETE FROM payload WHERE id % 4 != 0")
        db.commit()
    finally:
        db.close()


def _trigram_database(path: Path) -> None:
    db = sqlite3.connect(path)
    try:
        db.execute("CREATE TABLE history_messages(id INTEGER PRIMARY KEY, text TEXT)")
        db.execute(
            "CREATE VIRTUAL TABLE history_messages_trigram USING fts5("
            "text, content='history_messages', content_rowid='id', "
            "tokenize='trigram case_sensitive 0')"
        )
        db.execute(
            "CREATE TRIGGER history_messages_trigram_ai AFTER INSERT ON history_messages BEGIN "
            "INSERT INTO history_messages_trigram(rowid,text) VALUES(new.id,new.text); END"
        )
        db.executemany(
            "INSERT INTO history_messages(text) VALUES(?)",
            [(f"message number {n} with searchable body",) for n in range(500)],
        )
        db.commit()
    finally:
        db.close()


# ------------------------------------------------------------------ the request


def test_a_request_round_trips_and_is_consumed(tmp_path: Path) -> None:
    assert read_request(tmp_path) is None
    assert maintenance_summary(tmp_path) == {"pending": False}
    write_request(tmp_path, (OPERATION_VACUUM,))
    request = read_request(tmp_path)
    assert request is not None
    assert request.operations == (OPERATION_VACUUM,)
    assert request.backup is True
    assert maintenance_summary(tmp_path)["pending"] is True
    clear_request(tmp_path)
    assert read_request(tmp_path) is None
    # Clearing an absent request is not an error - both the success and the
    # failure path call it, and one of them may arrive twice.
    clear_request(tmp_path)


@pytest.mark.parametrize(
    "payload",
    ['{"operations": "vacuum"}', '["vacuum"]', "not json at all", '{"nope": 1}'],
)
def test_an_unreadable_request_is_not_a_request(tmp_path: Path, payload: str) -> None:
    """This fails towards doing nothing, which is the opposite of the integrity
    record's rule and deliberately so: an unreadable verification record means
    re-check, because checking is cheap and safe, while acting on an unparseable
    maintenance request means rewriting a database on the strength of a file this
    process could not read."""
    request_path(tmp_path).write_text(payload, encoding="utf-8")
    assert read_request(tmp_path) is None


def test_an_unknown_operation_does_nothing_at_all(tmp_path: Path) -> None:
    """A closed set: an unrecognised operation must not be silently dropped and
    then reported as a completed pass."""
    database = tmp_path / "mux.db"
    _database_with_slack(database)
    before = database.stat().st_size
    result = run_maintenance(
        database, MaintenanceRequest(operations=("vacuum", "reticulate"), requested_at=0.0)
    )
    assert result.error is not None
    assert "reticulate" in result.error
    assert result.performed == []
    assert database.stat().st_size == before
    assert not list(tmp_path.glob("*.pre-compact"))


# -------------------------------------------------------------- the operations


def test_vacuum_reclaims_space_and_sets_the_page_size(tmp_path: Path) -> None:
    database = tmp_path / "mux.db"
    _database_with_slack(database)
    result = run_maintenance(
        database, MaintenanceRequest(operations=(OPERATION_VACUUM,), requested_at=0.0)
    )
    assert result.error is None
    assert result.performed == [OPERATION_VACUUM]
    assert result.bytes_reclaimed > 0
    assert result.bytes_after < result.bytes_before
    probe = sqlite3.connect(database)
    try:
        assert int(probe.execute("PRAGMA page_size").fetchone()[0]) == VACUUM_PAGE_SIZE
        # The point of reclaiming is that the rows survive it.
        assert probe.execute("SELECT COUNT(*) FROM payload").fetchone()[0] == 1000
    finally:
        probe.close()


def test_the_backup_is_a_copy_and_the_original_still_works(tmp_path: Path) -> None:
    """A rename would leave the daemon with no database if the process died
    between it and the rewrite."""
    database = tmp_path / "mux.db"
    _database_with_slack(database)
    result = run_maintenance(
        database, MaintenanceRequest(operations=(OPERATION_VACUUM,), requested_at=0.0)
    )
    assert result.backup_path is not None
    assert result.backup_path.exists()
    assert database.exists()
    backup = sqlite3.connect(result.backup_path)
    try:
        assert backup.execute("SELECT COUNT(*) FROM payload").fetchone()[0] == 1000
    finally:
        backup.close()


def test_backup_can_be_declined(tmp_path: Path) -> None:
    database = tmp_path / "mux.db"
    _database_with_slack(database)
    result = run_maintenance(
        database,
        MaintenanceRequest(operations=(OPERATION_VACUUM,), requested_at=0.0, backup=False),
    )
    assert result.error is None
    assert result.backup_path is None
    assert not list(tmp_path.glob("*.pre-compact"))


def test_rebuilding_the_trigram_index_drops_it_and_its_triggers(tmp_path: Path) -> None:
    """`history.py` owns the schema and recreates both on its next open, so this
    drops rather than redefines - a second definition here would be a second
    place to keep in step, and the copy is what drifts."""
    database = tmp_path / "mux.db"
    _trigram_database(database)
    result = run_maintenance(
        database,
        MaintenanceRequest(operations=(OPERATION_REBUILD_TRIGRAM,), requested_at=0.0),
    )
    assert result.error is None
    assert result.performed == [OPERATION_REBUILD_TRIGRAM]
    probe = sqlite3.connect(database)
    try:
        names = {
            row[0]
            for row in probe.execute("SELECT name FROM sqlite_master WHERE name LIKE '%trigram%'")
        }
        assert names == set()
        # The source of truth is untouched, which is what makes the rebuild safe.
        assert probe.execute("SELECT COUNT(*) FROM history_messages").fetchone()[0] == 500
    finally:
        probe.close()


def test_a_missing_trigram_index_is_skipped_not_failed(tmp_path: Path) -> None:
    """The ordinary case on a fresh install."""
    database = tmp_path / "mux.db"
    _database_with_slack(database)
    result = run_maintenance(
        database,
        MaintenanceRequest(operations=(OPERATION_REBUILD_TRIGRAM,), requested_at=0.0),
    )
    assert result.error is None
    assert result.performed == []
    assert result.skipped == [OPERATION_REBUILD_TRIGRAM]


def test_a_cancelled_pass_leaves_the_database_usable(tmp_path: Path) -> None:
    database = tmp_path / "mux.db"
    _trigram_database(database)
    control = VerificationControl()
    control.cancel()
    result = run_maintenance(
        database,
        MaintenanceRequest(
            operations=(OPERATION_REBUILD_TRIGRAM, OPERATION_VACUUM), requested_at=0.0
        ),
        control,
    )
    assert OPERATION_VACUUM in result.skipped
    probe = sqlite3.connect(database)
    try:
        assert probe.execute("SELECT COUNT(*) FROM history_messages").fetchone()[0] == 500
    finally:
        probe.close()


def test_describe_says_what_happened(tmp_path: Path) -> None:
    database = tmp_path / "mux.db"
    _database_with_slack(database)
    result = run_maintenance(
        database, MaintenanceRequest(operations=(OPERATION_VACUUM,), requested_at=0.0)
    )
    line = describe(result)
    assert "vacuum" in line
    assert "GB reclaimed" in line


# ------------------------------------------------------- the daemon's own path


@pytest.mark.asyncio
async def test_no_request_means_the_phase_costs_a_failed_file_read(tmp_path: Path) -> None:
    config = Config(data_dir=tmp_path)
    _database_with_slack(config.database_path)
    before = config.database_path.stat().st_size
    await _run_pending_maintenance(config)
    assert config.database_path.stat().st_size == before


@pytest.mark.asyncio
async def test_the_daemon_performs_and_consumes_a_request(tmp_path: Path, caplog: Any) -> None:
    config = Config(data_dir=tmp_path)
    _database_with_slack(config.database_path)
    write_request(tmp_path, (OPERATION_VACUUM,))
    await _run_pending_maintenance(config)
    assert read_request(tmp_path) is None
    assert "database maintenance did vacuum" in caplog.text
    probe = sqlite3.connect(config.database_path)
    try:
        assert probe.execute("SELECT COUNT(*) FROM payload").fetchone()[0] == 1000
    finally:
        probe.close()


@pytest.mark.asyncio
async def test_a_failing_pass_still_consumes_the_request(tmp_path: Path) -> None:
    """A standing request would turn one bad start into every start being slow."""
    config = Config(data_dir=tmp_path)
    config.database_path.write_bytes(b"this is not a database")
    write_request(tmp_path, (OPERATION_VACUUM,))
    await _run_pending_maintenance(config)
    assert read_request(tmp_path) is None


@pytest.mark.asyncio
async def test_a_failing_pass_never_stops_the_daemon(tmp_path: Path) -> None:
    """A daemon that will not start because a compaction failed is strictly worse
    than one that starts on an uncompacted database and says so."""
    config = Config(data_dir=tmp_path)
    # No database at all: every operation must decline rather than raise.
    write_request(tmp_path, (OPERATION_VACUUM, OPERATION_REBUILD_TRIGRAM))
    await _run_pending_maintenance(config)  # must not raise
    assert read_request(tmp_path) is None


@pytest.mark.asyncio
async def test_a_rewrite_discards_the_stale_integrity_verdict(tmp_path: Path) -> None:
    """The recorded verdict describes bytes that no longer exist after a VACUUM."""
    from swe_mux.sqlite_store import record_database_verified, verification_record_path

    config = Config(data_dir=tmp_path)
    _database_with_slack(config.database_path)
    record_database_verified(config.database_path)
    assert verification_record_path(config.database_path).exists()
    write_request(tmp_path, (OPERATION_VACUUM,))
    await _run_pending_maintenance(config)
    assert not verification_record_path(config.database_path).exists()


def test_the_archive_path_is_a_sibling_of_the_database(tmp_path: Path) -> None:
    config = Config(data_dir=tmp_path)
    assert config.archive_database_path.parent == config.database_path.parent
    assert config.archive_database_path != config.database_path
    assert config.archive_database_path.name == "mux-archive.db"


def test_the_request_is_json_a_human_can_read(tmp_path: Path) -> None:
    """The operator is told this file exists and may need to delete it by hand."""
    write_request(tmp_path, (OPERATION_VACUUM,), backup=False)
    raw = json.loads(request_path(tmp_path).read_text(encoding="utf-8"))
    assert raw["operations"] == ["vacuum"]
    assert raw["backup"] is False
    assert isinstance(raw["requested_at"], float)
