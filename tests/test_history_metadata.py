from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from swe_mux.history import HistoryIndex
from swe_mux.models import SessionRecord
from swe_mux.operational_telemetry import OperationalTelemetryStore


def _create_legacy_database(path: Path) -> None:
    db = sqlite3.connect(path)
    db.execute(
        """CREATE TABLE history (
        id TEXT PRIMARY KEY, native_id TEXT NOT NULL, backend TEXT NOT NULL,
        name TEXT NOT NULL, cwd TEXT NOT NULL, space_id TEXT,
        spawned_at REAL NOT NULL, exited_at REAL, exit_reason TEXT,
        tokens_in INTEGER NOT NULL DEFAULT 0, tokens_out INTEGER NOT NULL DEFAULT 0,
        transcript_path TEXT, external INTEGER NOT NULL DEFAULT 0
        )"""
    )
    db.execute(
        "INSERT INTO history(id,native_id,backend,name,cwd,space_id,spawned_at) "
        "VALUES('legacy','native','claude','Legacy','C:/old','default',1.0)"
    )
    db.commit()
    db.close()


async def test_history_migration_adds_metadata_without_losing_rows(tmp_path: Path) -> None:
    path = tmp_path / "mux.db"
    _create_legacy_database(path)

    history = HistoryIndex(path)
    row = await history.history_entry("legacy")

    assert row is not None
    assert row["name"] == "Legacy"
    assert row["executable"] is None
    assert row["argv_json"] is None
    assert row["pinned_attention"] == 0
    assert row["shell_profile_id"] is None
    assert row["note_id"] == "legacy"
    history.close()

    reopened = HistoryIndex(path)
    columns = {
        item["name"] for item in reopened._db.execute("PRAGMA table_info(history)").fetchall()
    }
    assert {
        "executable",
        "argv_json",
        "pinned_attention",
        "shell_profile_id",
        "compaction_count",
        "last_compaction_at",
        "compaction_capability",
        "compaction_confidence",
        "last_message_at",
        "last_message_role",
        "native_started_at",
        "time_summary_mtime_ns",
        "time_summary_size",
        "note_id",
    } <= columns
    assert await reopened.history_entry("legacy") is not None
    reopened.close()

    telemetry = OperationalTelemetryStore(path)
    operational_tables = {
        row[0]
        for row in telemetry._db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert {
        "process_evidence",
        "quota_samples",
        "quota_reset_events",
        "quota_attributions",
        "context_compactions",
        "tool_events",
        "transcript_telemetry_coverage",
    } <= operational_tables
    assert telemetry._db.execute("SELECT name FROM history WHERE id='legacy'").fetchone()
    telemetry.close()


async def test_plain_shell_history_is_provisional_and_removed_on_exit(tmp_path: Path) -> None:
    history = HistoryIndex(tmp_path / "mux.db")
    session = SessionRecord(
        "mux-id",
        "Initial",
        "default",
        "shell",
        "native-id",
        "C:/initial",
        "powershell.exe",
        ["-NoLogo"],
        state="running",
    )
    await history.session_started(session, None)

    initial = await history.history_entry(session.id)
    assert initial is not None
    assert initial["executable"] == "powershell.exe"
    assert json.loads(initial["argv_json"]) == ["-NoLogo"]
    assert initial["pinned_attention"] == 0

    session.name = "Renamed"
    session.cwd = "D:/moved"
    session.project_id = "work"
    session.exe = "pwsh.exe"
    session.args = ["-NoProfile", "-NoLogo"]
    session.pinned_attention = True
    session.shell_profile_id = "pwsh"
    session.broadcast = True
    await history.update_session_metadata(session)

    updated = await history.history_entry(session.id)
    assert updated is not None
    assert updated["name"] == "Renamed"
    assert updated["cwd"] == "D:/moved"
    assert updated["project_id"] == "work"
    assert updated["executable"] == "pwsh.exe"
    assert json.loads(updated["argv_json"]) == ["-NoProfile", "-NoLogo"]
    assert updated["pinned_attention"] == 1
    assert updated["shell_profile_id"] == "pwsh"
    assert "broadcast" not in updated

    session.name = "Final name"
    session.args = ["-NoProfile", "-NonInteractive"]
    session.last_activity_ts = 42.0
    await history.session_ended(session, "complete")
    final = await history.history_entry(session.id)
    assert final is None
    assert await history.history("Final") == []
    history.close()
