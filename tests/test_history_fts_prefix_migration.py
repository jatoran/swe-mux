"""An archive indexed before the prefix index gets it, through the batched rebuild.

An FTS5 table's prefix option is fixed when the table is created, so an existing
archive cannot be given one in place and a `delete-all` reset would rebuild the old
shape. The upgrade therefore has to drop and recreate the token table - and on the
development host's archive that is 199,020 messages, so it must happen behind the
ready daemon in committed batches, with search answering through the bounded LIKE
path meanwhile, exactly like every other reset of these derivative tables.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from swe_mux.history import HistoryIndex
from swe_mux.models import SessionRecord

PROJECT = "project-id"

_OLD_TOKEN_TABLE = (
    "CREATE VIRTUAL TABLE history_messages_fts USING fts5("
    "text, content='history_messages', content_rowid='id', "
    "tokenize='unicode61 remove_diacritics 2')"
)


def message(text: str, second: int) -> dict[str, Any]:
    return {
        "role": "user",
        "ts": f"2026-01-01T00:00:{second:02d}Z",
        "content": [{"type": "text", "text": text}],
    }


async def add_run(history: HistoryIndex, identity: str, root: Path) -> None:
    row = SessionRecord(
        identity, identity, PROJECT, "claude", f"native-{identity}", str(root), "c", []
    )
    row.agent_run_id = identity
    await history.session_started(row, None)
    await history.replace_history_messages(
        identity,
        [message(f"beacon sighting {identity} number {n}", n) for n in range(3)],
        mtime_ns=1,
        size=1,
    )


def token_table_definition(database: Path) -> str:
    connection = sqlite3.connect(database)
    try:
        row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE name='history_messages_fts'"
        ).fetchone()
        return str(row[0])
    finally:
        connection.close()


def downgrade_token_table(database: Path) -> None:
    """Put back the token table an earlier build created, fully populated and ready."""
    connection = sqlite3.connect(database)
    try:
        connection.execute("DROP TABLE history_messages_fts")
        connection.execute(_OLD_TOKEN_TABLE)
        connection.execute(
            "INSERT INTO history_messages_fts(history_messages_fts) VALUES('rebuild')"
        )
        connection.commit()
    finally:
        connection.close()


async def test_an_old_token_table_is_rebuilt_with_the_prefix_index(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    database = tmp_path / "mux.db"
    root = tmp_path / "project"
    root.mkdir()
    history = HistoryIndex(database)
    for identity in ("run-0", "run-1"):
        await add_run(history, identity, root)
    history.close()
    downgrade_token_table(database)
    assert "prefix" not in token_table_definition(database)

    with caplog.at_level(logging.WARNING, logger="swe_mux.history"):
        migrated = HistoryIndex(database)
    try:
        assert any("predate the current definition" in r.getMessage() for r in caplog.records)
        status = await migrated.message_search_status()
        # Scheduled, not done: startup must not rebuild an archive's worth of text.
        assert status["ready"] is False and status["reset_required"] is True

        # Search still answers, through the bounded fallback.
        fallback = await migrated.history_page(query="beacon", project_id=PROJECT)
        assert {item["id"] for item in fallback["items"]} == {"run-0", "run-1"}
        # A conversation indexed while the rebuild is pending is not lost by it.
        await add_run(migrated, "run-2", root)

        await migrated.maintain_message_search_indexes(batch_size=2)
        ready = await migrated.message_search_status()
        assert ready["ready"] is True and ready["reset_required"] is False

        def definition_and_integrity() -> str:
            migrated._db.execute(  # noqa: SLF001 - the rebuilt table under test
                "INSERT INTO history_messages_fts(history_messages_fts,rank) "
                "VALUES('integrity-check',1)"
            )
            migrated._db.rollback()  # noqa: SLF001
            row = migrated._db.execute(  # noqa: SLF001
                "SELECT sql FROM sqlite_master WHERE name='history_messages_fts'"
            ).fetchone()
            return str(row["sql"])

        assert "prefix='1 2 3'" in await migrated._run(definition_and_integrity)  # noqa: SLF001

        indexed = await migrated.history_page(query="beacon s", project_id=PROJECT)
        assert {item["id"] for item in indexed["items"]} == {"run-0", "run-1", "run-2"}
        assert all(item["match_count"] == 3 for item in indexed["items"])
        assert await migrated.history_message_matches("run-2", "sight")
    finally:
        migrated.close()

    # Once rebuilt, the next start finds nothing to do.
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="swe_mux.history"):
        reopened = HistoryIndex(database)
    try:
        assert not any("predate" in r.getMessage() for r in caplog.records)
        assert (await reopened.message_search_status())["ready"] is True
    finally:
        reopened.close()


async def test_a_fresh_install_starts_ready_with_the_prefix_index(tmp_path: Path) -> None:
    history = HistoryIndex(tmp_path / "mux.db")
    try:
        assert (await history.message_search_status())["ready"] is True
    finally:
        history.close()
    assert "prefix='1 2 3'" in token_table_definition(tmp_path / "mux.db")
