"""Adding the mid-turn receiver switch to an install that is already running.

The per-run defaults are written once, when a run is granted. A column added
afterwards therefore reads as "opted out" on every conversation that was already
live - the feature looks dead on the whole fleet until each one happens to roll
over, which from the operator's side is indistinguishable from it being broken.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from swe_mux.prompt_queue import AUTO_POLICY_GLOBAL, PromptQueueStore

_LEGACY_POLICY_TABLE = """
CREATE TABLE queue_auto_policy (
  session_id TEXT PRIMARY KEY,
  enabled INTEGER NOT NULL DEFAULT 0,
  agent_run_id TEXT,
  accept_agent_messages INTEGER NOT NULL DEFAULT 0,
  expires_at REAL,
  max_sends INTEGER NOT NULL DEFAULT 0,
  sends_used INTEGER NOT NULL DEFAULT 0,
  paused INTEGER NOT NULL DEFAULT 0,
  disabled_reason TEXT,
  enabled_at REAL,
  updated_at REAL NOT NULL,
  updated_by TEXT
);
CREATE TABLE queue_messages (
  id TEXT PRIMARY KEY, target_session_id TEXT NOT NULL, target_agent_run_id TEXT,
  target_backend TEXT, target_label TEXT, project_id TEXT, position INTEGER NOT NULL,
  state TEXT NOT NULL, body TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1,
  sender_kind TEXT NOT NULL DEFAULT 'user', sender_id TEXT, origin_json TEXT,
  payload_json TEXT, constraints_json TEXT, blocked_reasons_json TEXT,
  stranded_reason TEXT, cancel_kind TEXT, retargeted_from_json TEXT,
  created_at REAL NOT NULL, updated_at REAL NOT NULL, edited_at REAL,
  armed_at REAL, sent_at REAL
);
"""


@pytest.mark.asyncio
async def test_live_grants_carry_the_per_run_default_onto_the_new_column(
    tmp_path: Path,
) -> None:
    path = tmp_path / "queue.db"
    legacy = sqlite3.connect(path)
    legacy.executescript(_LEGACY_POLICY_TABLE)
    rows = [
        # A live conversation with the ordinary per-run defaults.
        ("live", 1, 1, None),
        # Auto-delivery on, but this run said no to agent messages at all.
        ("no-agent-messages", 1, 0, None),
        # A conversation the operator switched off.
        ("opted-out", 0, 1, "disabled by user"),
        # The reserved emergency-pause row, which is not a conversation.
        (AUTO_POLICY_GLOBAL, 0, 0, None),
    ]
    for session_id, enabled, accepts, reason in rows:
        legacy.execute(
            "INSERT INTO queue_auto_policy(session_id,enabled,accept_agent_messages,"
            "disabled_reason,updated_at) VALUES(?,?,?,?,1)",
            (session_id, enabled, accepts, reason),
        )
    legacy.commit()
    legacy.close()

    store = PromptQueueStore(path)
    try:
        live = await store.auto_policy("live")
        assert live is not None and live["accept_agent_interjections"] == 1
        # Everything that said something is left saying it.
        for session_id in ("no-agent-messages", "opted-out", AUTO_POLICY_GLOBAL):
            row = await store.auto_policy(session_id)
            assert row is not None, session_id
            assert row["accept_agent_interjections"] == 0, session_id
    finally:
        store.close()


@pytest.mark.asyncio
async def test_a_fresh_database_needs_no_backfill(tmp_path: Path) -> None:
    """The column default stays 0: a row inserted by an opt-out, and the pause
    row, must not read as "on" because the DDL said so."""
    store = PromptQueueStore(tmp_path / "queue.db")
    try:
        await store.set_auto_policy("s1", enabled=0, disabled_reason="disabled by user")
        row = await store.auto_policy("s1")
        assert row is not None and row["accept_agent_interjections"] == 0
    finally:
        store.close()
