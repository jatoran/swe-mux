"""Finding conversations every harness wrote outside mux.

Retroactive discovery is what puts a CLI's own past sessions into History, so they
can be searched and resumed alongside the ones mux launched. It is a per-harness
capability like any other, and it used to be answered by a hardcoded two-vendor
tuple: omp, pi, and opencode wrote conversations that existed on disk, were
readable, and never reached History, with nothing reporting the gap.

These tests exercise the scanner against real on-disk layouts under an injected home,
so a harness that stops being discovered fails here rather than silently indexing
nothing.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from swe_mux.harness import HARNESSES
from swe_mux.reconcile import (
    ExternalTranscript,
    discover_store_conversations,
    scan_external_transcripts,
    summarize_transcript,
)

CWD = "D:\\PROJECTS\\demo"


def _write_pi_dialect(root: Path, native_id: str, cwd: str) -> Path:
    """One oh-my-pi or pi session file: a `{"type":"session"}` header, then entries."""
    bucket = root / "sessions" / "demo-bucket"
    bucket.mkdir(parents=True, exist_ok=True)
    path = bucket / f"2026-08-11T00-00-00-000Z_{native_id}.jsonl"
    path.write_text(
        json.dumps(
            {
                "type": "session",
                "id": native_id,
                "cwd": cwd,
                "timestamp": "2026-08-11T00:00:00.000Z",
                "version": "1",
            }
        )
        + "\n"
        + json.dumps(
            {
                "type": "message",
                "message": {"role": "assistant", "content": [{"type": "text", "text": "hi"}]},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def _write_claude(home: Path, native_id: str, cwd: str) -> Path:
    directory = home / ".claude" / "projects" / "encoded-demo"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{native_id}.jsonl"
    path.write_text(
        json.dumps(
            {
                "type": "user",
                "sessionId": native_id,
                "cwd": cwd,
                "timestamp": "2026-08-11T00:00:00.000Z",
                "message": {"content": [{"type": "text", "text": "hello"}]},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def _write_codex(home: Path, native_id: str, cwd: str) -> Path:
    directory = home / ".codex" / "sessions" / "2026" / "08"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"rollout-2026-08-11-{native_id}.jsonl"
    path.write_text(
        json.dumps(
            {
                "type": "session_meta",
                "timestamp": "2026-08-11T00:00:00.000Z",
                "payload": {"id": native_id, "cwd": cwd},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def _write_opencode(home: Path, native_id: str, cwd: str, *, parent: str | None = None) -> Path:
    root = home / ".local" / "share" / "opencode"
    root.mkdir(parents=True, exist_ok=True)
    path = root / "opencode.db"
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS session (id TEXT PRIMARY KEY, parent_id TEXT,"
            " directory TEXT NOT NULL, time_created INTEGER NOT NULL,"
            " time_updated INTEGER NOT NULL, cost REAL DEFAULT 0,"
            " tokens_input INTEGER DEFAULT 0, tokens_output INTEGER DEFAULT 0,"
            " tokens_reasoning INTEGER DEFAULT 0, tokens_cache_read INTEGER DEFAULT 0,"
            " tokens_cache_write INTEGER DEFAULT 0, model TEXT, agent TEXT, title TEXT)"
        )
        connection.execute(
            "INSERT OR REPLACE INTO session"
            " (id, parent_id, directory, time_created, time_updated, cost,"
            " tokens_input, tokens_output, model, title)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                native_id,
                parent,
                cwd,
                1_700_000_000_000,
                1_700_000_001_000,
                0.25,
                111,
                222,
                json.dumps({"id": "gpt-5.6-sol", "providerID": "openai"}),
                "a title",
            ),
        )
        connection.commit()
    finally:
        connection.close()
    return path


@pytest.fixture
def populated_home(tmp_path: Path) -> Path:
    """A home holding one conversation for every registered harness."""
    home = tmp_path / "home"
    _write_claude(home, "claude-1", CWD)
    _write_codex(home, "codex-1", CWD)
    _write_pi_dialect(home / ".omp" / "agent", "omp-1", CWD)
    _write_pi_dialect(home / ".pi" / "agent", "pi-1", CWD)
    _write_opencode(home, "ses_one", CWD)
    return home


def test_every_registered_harness_is_discovered(populated_home: Path) -> None:
    """The regression this file exists for: three harnesses found nothing.

    Asserted against the registry rather than a fixed list, so a harness added later
    is required to be discoverable (or to declare that it is not) instead of quietly
    joining the set that is never scanned.
    """
    found = scan_external_transcripts(populated_home)
    by_backend = {item.backend: item for item in found}
    expected = {
        name
        for name, harness in HARNESSES.items()
        if harness.conversation_discovery is not None
    }
    assert set(by_backend) == expected
    assert by_backend["claude"].native_id == "claude-1"
    assert by_backend["codex"].native_id == "codex-1"
    assert by_backend["omp"].native_id == "omp-1"
    assert by_backend["pi"].native_id == "pi-1"
    assert by_backend["opencode"].native_id == "ses_one"
    assert all(item.cwd == CWD for item in found)


def test_the_two_pi_forks_are_told_apart_by_where_they_were_found(
    populated_home: Path,
) -> None:
    """Their headers are identical, so the directory is the only discriminator.

    oh-my-pi and pi share a record dialect and share the inspector that reads it. The
    header does not name which fork wrote it, so attributing by the data home is what
    keeps one fork's conversations out of the other's history.
    """
    found = {item.backend: item for item in scan_external_transcripts(populated_home)}
    assert found["omp"].path is not None
    assert found["pi"].path is not None
    assert ".omp" in str(found["omp"].path)
    assert ".pi" in str(found["pi"].path)


def test_a_store_conversation_is_discovered_without_a_path(populated_home: Path) -> None:
    """There is no file, so `path` is None and the native id is the whole address."""
    found = discover_store_conversations("opencode", populated_home)
    assert [item.native_id for item in found] == ["ses_one"]
    assert found[0].path is None
    assert found[0].cwd == CWD
    # The watermark slot carries the store's own updated-time, not a file stat.
    assert found[0].mtime_ns == 1_700_000_001_000


def test_a_store_subagent_is_not_discovered_as_its_own_conversation(tmp_path: Path) -> None:
    """A child row is part of a conversation, not one of its own.

    The same rule the file-backed harnesses apply by hand: Claude skips `isSidechain`
    records and Codex skips a rollout with `parent_thread_id`. Here it is a `WHERE`
    clause, and getting it wrong would list every subagent in History.
    """
    home = tmp_path / "home"
    _write_opencode(home, "ses_root", CWD)
    _write_opencode(home, "ses_child", CWD, parent="ses_root")
    found = discover_store_conversations("opencode", home)
    assert [item.native_id for item in found] == ["ses_root"]


def test_store_measurements_come_from_the_session_row(populated_home: Path) -> None:
    """Exact figures, not a parse: the harness maintains its own running totals."""
    summary = summarize_transcript(None, "opencode", "ses_one", populated_home)
    assert summary["tokens_in"] == 111
    assert summary["tokens_out"] == 222
    assert summary["cost_usd"] == 0.25
    assert summary["model"] == "gpt-5.6-sol"
    assert summary["provider"] == "openai"
    assert summary["measurement_source"] == "opencode-database"
    # A conversation the store does not hold publishes nothing rather than zeroes.
    absent = summarize_transcript(None, "opencode", "ses_absent", populated_home)
    assert absent["measurement_source"] is None
    assert absent["tokens_in"] == 0


def test_an_empty_home_discovers_nothing_and_does_not_raise(tmp_path: Path) -> None:
    assert scan_external_transcripts(tmp_path / "empty") == []
    assert discover_store_conversations("opencode", tmp_path / "empty") == []


def test_a_discovered_record_keys_history_by_backend_and_conversation() -> None:
    """The row id must not depend on a path, which a store conversation lacks."""
    with_path = ExternalTranscript("omp", "n-1", CWD, 1.0, Path("a.jsonl"))
    without_path = ExternalTranscript("omp", "n-1", CWD, 1.0, None)
    assert with_path.row_id == without_path.row_id
    assert ExternalTranscript("pi", "n-1", CWD, 1.0, None).row_id != with_path.row_id
