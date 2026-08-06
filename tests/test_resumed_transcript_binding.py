"""A resumed pane binds the conversation it resumed, without waiting on an mtime.

Codex has no transcript path derivable from the cwd, so an unbound pane finds its
rollout by correlating recent files. A *resumed* pane cannot be found that way: the
rollout it continues is older than the pane, and on Windows an open rollout's mtime
stays frozen at creation while the file grows, so recency never catches up. The
observed result was a resumed pane that stayed unobserved for its whole life — no
transcript tab, no tokens, no context, and a history entry that reported no native
transcript and so could never be resumed again.

The conversation id is known here, and the rollout's own first record proves which
file answers to it, so binding needs no recency at all. These tests pin that, and
pin that the proof does not weaken the rule that two panes never share one file.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from swe_mux.adapters import CodexAdapter
from swe_mux.models import SessionRecord
from swe_mux.session import SessionManager

CONVERSATION = "019fca0c-a007-7e32-bbb3-bcd8e08f7c8b"
PANE = "bbbbbbbb-2222-4a7b-8c9d-0e1f2a3b4c5d"


def resumed_rollout(home: Path, cwd: Path, native_id: str) -> Path:
    """A rollout written by an earlier pane, with an mtime older than any resume."""
    directory = home / "sessions" / "2026" / "08" / "03"
    path = directory / f"rollout-2026-08-03T18-54-12-{native_id}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"type": "session_meta", "payload": {"id": native_id, "cwd": str(cwd)}}) + "\n",
        encoding="utf-8",
    )
    yesterday = time.time() - 86_400
    os.utime(path, (yesterday, yesterday))
    return path


def resumed_session(cwd: Path, *, native_id: str = CONVERSATION) -> Any:
    record = SessionRecord(
        PANE, "UI Updates", "default", "codex", native_id, str(cwd),
        "codex.exe", ["resume", native_id],
    )
    record.spawn_backend = "codex"
    record.spawn_native_session_id = native_id
    record.run_cwd = str(cwd)
    return cast(
        Any,
        SimpleNamespace(
            record=record,
            adapter=CodexAdapter(),
            transcript_path=None,
            ignored_detection_runs=set(),
        ),
    )


def manager_with(*sessions: Any) -> Any:
    manager = cast(Any, SessionManager.__new__(SessionManager))
    manager.sessions = {session.record.id: session for session in sessions}
    return manager


def test_a_resumed_pane_binds_the_rollout_its_id_owns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "codex"
    monkeypatch.setenv("CODEX_HOME", str(home))
    cwd = tmp_path / "project"
    cwd.mkdir()
    rollout = resumed_rollout(home, cwd, CONVERSATION)
    session = resumed_session(cwd)
    manager = manager_with(session)

    assert manager._named_conversation_transcript(session, cwd) == rollout
    # The same answer through the discovery loop, which is what the observer uses:
    # exact, so it binds everything rather than staying provisional.
    found = asyncio.run(
        SessionManager._await_owned_transcript(manager, session, asyncio.Event())
    )
    assert found == (rollout, False)


def test_a_fresh_pane_asks_nothing_about_its_placeholder_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A backend that mints its own id carries the mux session id until discovery.
    # "Where does conversation <mux id> live" has no truthful answer, and a file
    # that happened to answer to it would not be this pane's conversation.
    home = tmp_path / "codex"
    monkeypatch.setenv("CODEX_HOME", str(home))
    cwd = tmp_path / "project"
    cwd.mkdir()
    resumed_rollout(home, cwd, PANE)
    session = resumed_session(cwd, native_id=PANE)
    manager = manager_with(session)

    assert manager._named_conversation_transcript(session, cwd) is None


def test_a_conversation_a_live_pane_owns_is_never_taken_over(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Branch resumes a live conversation on purpose, so "the adapter can name this
    # file" must not become "this pane may tail it": two panes on one transcript is
    # the cross-attribution the identity invariant forbids.
    home = tmp_path / "codex"
    monkeypatch.setenv("CODEX_HOME", str(home))
    cwd = tmp_path / "project"
    cwd.mkdir()
    rollout = resumed_rollout(home, cwd, CONVERSATION)
    branching = resumed_session(cwd)
    owner = resumed_session(cwd)
    owner.record.id = "owner-pane"
    owner.transcript_path = rollout
    manager = manager_with(branching, owner)

    assert manager._named_conversation_transcript(branching, cwd) is None


def relocated_claude_session(tmp_path: Path) -> tuple[Any, Any, Path, Path]:
    """A Claude pane whose conversation moved into a worktree's project directory."""
    from swe_mux.adapters import ClaudeAdapter
    from swe_mux.adapters.claude import encode_cwd

    checkout = tmp_path / "repo"
    worktree = checkout / ".claude" / "worktrees" / "feature"
    worktree.mkdir(parents=True)
    projects = tmp_path / "claude" / "projects"
    spawn_path = projects / encode_cwd(checkout) / f"{PANE}.jsonl"
    spawn_path.parent.mkdir(parents=True)
    moved = projects / encode_cwd(worktree) / f"{PANE}.jsonl"
    moved.parent.mkdir(parents=True)
    moved.write_text("{}\n", encoding="utf-8")

    record = SessionRecord(
        PANE, "worktree agent", "default", "claude", PANE, str(checkout), "claude.exe", []
    )
    record.spawn_backend = "claude"
    record.spawn_native_session_id = PANE
    record.run_cwd = str(checkout)
    session = cast(
        Any,
        SimpleNamespace(
            record=record,
            adapter=ClaudeAdapter(),
            transcript_path=None,
            transcript_provisional=False,
            ignored_detection_runs=set(),
        ),
    )
    return manager_with(session), session, spawn_path, moved


def test_a_relocated_conversation_is_rebound_across_a_daemon_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Every discovery path reads the directory the *spawn* cwd names, so a session
    # whose CLI entered a worktree had nothing left to adopt after a restart: the
    # mirrored path was dead and the recency scan was looking in the wrong place.
    # It came back permanently unobserved, and silently - a missing file is exactly
    # what the staleness guard cannot read a timestamp from.
    monkeypatch.setattr(
        "swe_mux.adapters.claude.claude_data_home", lambda: tmp_path / "claude"
    )
    manager, session, spawn_path, moved = relocated_claude_session(tmp_path)

    assert not spawn_path.exists()
    found = asyncio.run(
        SessionManager._await_owned_transcript(manager, session, asyncio.Event())
    )
    # Exact, not provisional: the file is named after the conversation id mux
    # dictated at spawn, so it is this pane's by construction.
    assert found == (moved, False)


def test_a_relocated_conversation_a_sibling_owns_is_not_taken(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "swe_mux.adapters.claude.claude_data_home", lambda: tmp_path / "claude"
    )
    manager, session, _spawn_path, moved = relocated_claude_session(tmp_path)
    owner = SimpleNamespace(
        record=SessionRecord(
            "owner-pane", "owner", "default", "claude", PANE, str(tmp_path), "claude.exe", []
        ),
        transcript_path=moved,
    )
    owner.record.state = "running"
    manager.sessions["owner-pane"] = owner

    assert (
        manager._relocated_conversation_transcript(
            session.adapter, "claude", PANE, *manager._live_transcript_claims(session)
        )
        is None
    )


def test_a_conversation_this_pane_has_disowned_is_not_rebound(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A demoted or rolled-away conversation is recorded as ignored for this pane.
    # Naming its file must not walk that back.
    home = tmp_path / "codex"
    monkeypatch.setenv("CODEX_HOME", str(home))
    cwd = tmp_path / "project"
    cwd.mkdir()
    resumed_rollout(home, cwd, CONVERSATION)
    session = resumed_session(cwd)
    session.ignored_detection_runs.add(("codex", CONVERSATION))
    manager = manager_with(session)

    assert manager._named_conversation_transcript(session, cwd) is None
