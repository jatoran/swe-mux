"""An install from an older build upgrades in place instead of being replaced.

Phase 11 ("migration compatibility"). Every store here writes into one shared
`mux.db`, and every one of them migrates on connect, so the question this file
asks is the one no per-store test can: does *a whole database written by an
older build of this repository* still open, still hold its rows, and still take
a write afterwards?

The fixture is that database. It is the `.dump` of a `mux.db` created by running
the store constructors from a real revision (`tests/support/legacy_database.py`
records which, and how to move the baseline), so its schema, its index set and
its recorded `schema_versions` rows are that build's own output rather than a
reconstruction of it. `test_the_fixture_is_actually_older_than_this_build` is
what keeps that true: regenerate it from HEAD and this whole file quietly stops
testing anything, which is the failure mode a migration suite is most prone to.

What this does *not* replace: the per-store migration tests
(`test_prompt_queue.py`, `test_voice.py`, `test_automation_phase6.py`,
`test_operational_telemetry_phase2.py`, `test_scheduled_resume.py`,
`test_prompt_queue_policy_migration.py`, `test_history_metadata.py`). Those state
the exact pre-migration shape a specific column was added against, next to the
migration that adds it, and they are the right place to assert what a *value*
becomes. This file asserts the composition instead, and catches the one thing
none of them can see: a column added to a schema string with no migration
entry beside it, which reaches a fresh install and no existing one
(`.docs/technical/backend/sqlite.md` § Schema versions).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from swe_mux.assistant import AssistantStore
from swe_mux.automation_store import AUTOMATION_SCHEMA_VERSION, AutomationStore
from swe_mux.clipboard_store import ClipboardStore
from swe_mux.code_graph import CodeGraphStore
from swe_mux.history import HistoryIndex
from swe_mux.operational_telemetry import TELEMETRY_SCHEMA_VERSION, OperationalTelemetryStore
from swe_mux.prompt_queue import QUEUE_SCHEMA_VERSION, PromptQueueStore
from swe_mux.schedule_store import SCHEDULE_SCHEMA_VERSION, ScheduleStore
from swe_mux.session_recovery import SESSION_RECOVERY_SCHEMA_VERSION, SessionRecoveryStore
from swe_mux.status_timeline import STATUS_TIMELINE_SCHEMA_VERSION, StatusTimelineStore
from swe_mux.tier0_store import TIER0_SCHEMA_VERSION, Tier0Store
from swe_mux.voice import VOICE_SCHEMA_VERSION, VoiceStore
from tests.support.legacy_database import SEEDED_TABLES, load_legacy_database

# Every store `server.py` opens against `config.database_path`, and only those.
# `LandStore` is deliberately absent: it owns its own file
# (`land-queue.sqlite3`), so it is not part of what an upgrade does to `mux.db`.
DISCARDED_ON_UPGRADE = {
    # The one place an upgrade is *allowed* to destroy rows, and it is a decision
    # rather than an accident: a pre-schema-3 clip has no stream identity, so it
    # cannot be reassembled into the reply it was a segment of, and carrying it
    # forward shows one reply as several rows in reverse spoken order. Clips are
    # a regenerable cache under a byte cap, so `VoiceStore._migrate` discards
    # them - rows and audio both. Anything else emptying a table fails the test
    # above rather than quietly joining this list.
    "voice_clips": "VoiceStore._migrate discards pre-schema-3 clips (voice.md)",
}

CURRENT_SCHEMA_VERSIONS = {
    "automation": AUTOMATION_SCHEMA_VERSION,
    "prompt_queue": QUEUE_SCHEMA_VERSION,
    "schedules": SCHEDULE_SCHEMA_VERSION,
    "session_recovery": SESSION_RECOVERY_SCHEMA_VERSION,
    "status_timeline": STATUS_TIMELINE_SCHEMA_VERSION,
    "telemetry": TELEMETRY_SCHEMA_VERSION,
    "tier0": TIER0_SCHEMA_VERSION,
    "voice": VOICE_SCHEMA_VERSION,
}


async def _open_and_close_every_store(database: Path, data_dir: Path) -> None:
    """Connect today's whole `mux.db` store set, in `server.py`'s order.

    Order is not cosmetic: the stores share one file and one of them migrates a
    table another one indexes, so a set that opens correctly one at a time can
    still fail as a sequence. Closing each is what releases the worker thread and
    the file handle - Windows will not let a later test move a file this process
    still holds open.

    The message-search maintenance is part of the upgrade rather than an extra:
    `HistoryIndex` deliberately does only bounded schema work on the startup path
    and leaves rebuilding the FTS derivatives (and creating the index over the
    column it backfills) to the pass the daemon runs immediately afterwards. An
    upgrade measured before that pass is measured half-done.
    """
    history = HistoryIndex(database)
    stores: list[Any] = [
        history,
        OperationalTelemetryStore(database),
        Tier0Store(database),
        CodeGraphStore(database),
        SessionRecoveryStore(database, data_dir / "recovery"),
        StatusTimelineStore(database),
        AutomationStore(database),
        VoiceStore(database),
        PromptQueueStore(database),
        AssistantStore(database),
        ScheduleStore(database),
        # `persist=True` because the default skips the SQLite mirror entirely,
        # and a store that never touches the file cannot be said to migrate it.
        ClipboardStore(database, persist=True),
    ]
    await history.maintain_message_search_indexes()
    for store in stores:
        close = getattr(store, "close", None)
        if close is not None:
            close()


def _schema(database: Path) -> dict[str, set[str]]:
    """Every table's column set, which is what a migration is measured against."""
    db = sqlite3.connect(database)
    try:
        tables = [
            str(row[0])
            for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        ]
        return {
            table: {str(row[1]) for row in db.execute(f"PRAGMA table_info({table})").fetchall()}
            for table in tables
        }
    finally:
        db.close()


def _indexes(database: Path) -> set[str]:
    db = sqlite3.connect(database)
    try:
        return {
            str(row[0])
            for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        }
    finally:
        db.close()


def _recorded_versions(database: Path) -> dict[str, int]:
    db = sqlite3.connect(database)
    try:
        return {
            str(row[0]): int(row[1])
            for row in db.execute("SELECT store, version FROM schema_versions").fetchall()
        }
    finally:
        db.close()


def _row_counts(database: Path, tables: tuple[str, ...]) -> dict[str, int]:
    db = sqlite3.connect(database)
    try:
        return {
            table: int(db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in tables
        }
    finally:
        db.close()


async def _upgraded(tmp_path: Path) -> Path:
    """The fixture, opened once by every current store: an upgraded install."""
    data_dir = tmp_path / "upgraded"
    database = load_legacy_database(data_dir / "mux.db")
    await _open_and_close_every_store(database, data_dir)
    return database


async def _fresh(tmp_path: Path) -> Path:
    """The same store set against an empty file: a new install."""
    data_dir = tmp_path / "fresh"
    database = data_dir / "mux.db"
    database.parent.mkdir(parents=True, exist_ok=True)
    await _open_and_close_every_store(database, data_dir)
    return database


def test_the_fixture_is_actually_older_than_this_build(tmp_path: Path) -> None:
    """The guard that keeps every other test in this file meaningful.

    A fixture regenerated from HEAD would pass all of them while proving
    nothing, and nothing about the file would look wrong. So: at least one store
    in it must record a version *behind* today's, and it must not be at today's
    numbers across the board.
    """
    recorded = _recorded_versions(load_legacy_database(tmp_path / "as-shipped" / "mux.db"))
    assert recorded, "the fixture records no schema versions at all"
    behind = {
        store: (version, CURRENT_SCHEMA_VERSIONS[store])
        for store, version in recorded.items()
        if store in CURRENT_SCHEMA_VERSIONS and version < CURRENT_SCHEMA_VERSIONS[store]
    }
    assert behind, (
        "the fixture is at the current schema version for every store it records "
        f"({recorded}), so it is not an old database and this suite is vacuous. "
        "Regenerate it from an older revision - see tests/support/legacy_database.py."
    )


async def test_an_old_database_opens_rather_than_being_quarantined(tmp_path: Path) -> None:
    """The destructive outcome this exists to rule out.

    `connect_or_quarantine` renames a file it cannot read aside as `.corrupt-<ts>`
    and starts fresh. That is right for a corrupt file and catastrophic for a
    merely old one, and the two are indistinguishable to a user: the app comes
    up, and their history is gone.
    """
    database = await _upgraded(tmp_path)
    quarantined = sorted(database.parent.glob("*.corrupt-*"))
    assert not quarantined, f"an old but healthy database was quarantined: {quarantined}"
    assert database.is_file()


async def test_every_column_a_fresh_install_has_reaches_an_upgraded_one(tmp_path: Path) -> None:
    """The ratchet: a new column with no migration beside it fails here.

    `CREATE TABLE IF NOT EXISTS` no-ops against an existing table, so a column
    added to a schema string alone reaches a fresh install and no other - and
    the first symptom is an `INSERT` naming it on a user's machine, months later.
    Comparing the two databases makes that a build-time failure with no
    inventory to maintain: whatever the schema declares tomorrow is what an
    upgrade is required to produce.
    """
    upgraded = _schema(await _upgraded(tmp_path))
    fresh = _schema(await _fresh(tmp_path))

    missing_tables = sorted(set(fresh) - set(upgraded))
    assert not missing_tables, (
        f"tables a fresh install has and an upgraded one does not: {missing_tables}. "
        "A new table needs `CREATE TABLE IF NOT EXISTS` in the store's schema script, "
        "which reaches an existing database on the next connect."
    )
    missing_columns = {
        table: sorted(columns - upgraded[table])
        for table, columns in fresh.items()
        if columns - upgraded[table]
    }
    assert not missing_columns, (
        f"columns a fresh install has and an upgraded one does not: {missing_columns}. "
        "Add them to that store's `PRAGMA table_info` migration - a column in the schema "
        "string alone reaches new installs only (.docs/technical/backend/sqlite.md)."
    )


async def test_every_index_a_fresh_install_has_reaches_an_upgraded_one(tmp_path: Path) -> None:
    """Same ratchet for indexes, and it is not the same failure.

    `CREATE INDEX IF NOT EXISTS` does reach an existing database, so the risk is
    the reverse one: an index over a column the migration adds must run *after*
    that migration, or it raises `no such column` and takes the store's
    construction - and the daemon's startup - down with it. That failure is only
    visible against a database that predates the column, which is this fixture.
    """
    missing = sorted(_indexes(await _fresh(tmp_path)) - _indexes(await _upgraded(tmp_path)))
    assert not missing, (
        f"indexes a fresh install has and an upgraded one does not: {missing}. "
        "If the index covers a migrated column, its `CREATE INDEX` has to run after "
        "`_migrate()` rather than inside the schema script."
    )


async def test_rows_written_by_the_old_build_survive_the_upgrade(tmp_path: Path) -> None:
    """Migrating forward, not starting over.

    Row counts rather than values: what a backfilled column *becomes* is the
    per-store tests' question, and the one asked here is whether the row is
    still there at all - which is exactly what a table rebuild
    (`automation_annotations` was one) can quietly get wrong.
    """
    before = _row_counts(load_legacy_database(tmp_path / "before" / "mux.db"), SEEDED_TABLES)
    after = _row_counts(await _upgraded(tmp_path), SEEDED_TABLES)
    assert before == {table: 1 for table in SEEDED_TABLES}, (
        f"the fixture does not carry the rows this test reads: {before}"
    )
    carried = tuple(table for table in SEEDED_TABLES if table not in DISCARDED_ON_UPGRADE)
    lost = {table: after[table] for table in carried if after[table] != before[table]}
    assert not lost, (
        f"rows were lost by the upgrade: {lost} (from {before}). A migration that "
        "rebuilds a table has to copy it; one that destroys rows is a decision, and it "
        f"belongs in DISCARDED_ON_UPGRADE with its reason - see {sorted(DISCARDED_ON_UPGRADE)}."
    )


@pytest.mark.parametrize("table", sorted(DISCARDED_ON_UPGRADE))
async def test_a_documented_discard_is_still_the_thing_it_documents(
    table: str, tmp_path: Path
) -> None:
    """The exemption above, kept honest.

    An exemption that outlives the behaviour it excuses is how a second, silent
    data loss gets in: the table is already on the list, so nothing complains
    when a different migration starts emptying it. If this fails because the
    rows now survive, delete the entry rather than widening it.
    """
    after = _row_counts(await _upgraded(tmp_path), (table,))
    assert after[table] == 0, (
        f"{table} rows now survive the upgrade, so DISCARDED_ON_UPGRADE no longer "
        f"describes what happens ({DISCARDED_ON_UPGRADE[table]}). Remove the entry."
    )


async def test_the_upgraded_database_takes_a_write_naming_every_migrated_column(
    tmp_path: Path,
) -> None:
    """Openable is not usable, and the difference is where the failure shows up.

    `add_spend` names `cached_tokens`, `cache_write_tokens`, `cache_discount_usd`,
    `cost_known`, `project_id` and `agent_run_id` - six columns added to
    `automation_budget_ledger` by four separate migrations. Against an
    unmigrated database this raises `no such column`, which is precisely the
    error an upgraded install would hit on its first automation run.
    """
    database = await _upgraded(tmp_path)
    store = AutomationStore(database)
    try:
        await store.add_spend(
            rule_id="rule-after-upgrade",
            model="test/model",
            input_tokens=11,
            output_tokens=7,
            cost_usd=None,
            call_id="call-after-upgrade",
        )
    finally:
        store.close()

    db = sqlite3.connect(database)
    try:
        row = db.execute(
            "SELECT cost_known, cached_tokens, cache_write_tokens, cache_discount_usd "
            "FROM automation_budget_ledger WHERE rule_id=?",
            ("rule-after-upgrade",),
        ).fetchone()
    finally:
        db.close()
    assert row is not None, "the write succeeded but wrote nothing"
    assert row[0] == 0, "cost_usd=None must record as unmeasured, not as a known zero"


async def test_schema_versions_move_forward_to_the_current_constants(tmp_path: Path) -> None:
    """The recorded version is how two installs are told apart, so it has to move."""
    recorded = _recorded_versions(await _upgraded(tmp_path))
    stale = {
        store: (recorded.get(store), expected)
        for store, expected in CURRENT_SCHEMA_VERSIONS.items()
        if recorded.get(store) != expected
    }
    assert not stale, (
        f"stores whose recorded version did not reach the current constant: {stale}. "
        "Each store stamps `schema_versions` on connect; a store that migrated without "
        "stamping leaves an install indistinguishable from the build before it."
    )
