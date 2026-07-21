from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from swe_mux.models import SessionRecord
from swe_mux.session import SessionManager


def agent_record(backend: str = "claude", cwd: str = ".") -> SessionRecord:
    return SessionRecord(
        "mux-id", "agent", "default", backend, "native-id", cwd, f"{backend}.exe", []
    )


def fake_manager() -> Any:
    manager = cast(Any, SessionManager.__new__(SessionManager))
    manager.sessions = {}
    return manager


def fake_agent_session(backend: str, transcript: Path | None, cwd: str = ".") -> Any:
    return cast(
        Any,
        SimpleNamespace(
            record=agent_record(backend, cwd),
            stop_event=asyncio.Event(),
            stopping=False,
            transcript_path=transcript,
            agent_lifecycle_id="lifecycle-id",
            agent_promoted_at=time.time() - 60,
            agent_exit_check_task=None,
            ignored_detection_runs=set(),
            tasks=set(),
        ),
    )


async def test_shell_prompt_after_agent_exit_demotes_quiescent_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("swe_mux.session.AGENT_EXIT_CHECK_INTERVAL_SECONDS", 0.01)
    transcript = tmp_path / "native.jsonl"
    transcript.write_text("{}\n", encoding="utf-8")
    stale = time.time() - 30
    os.utime(transcript, (stale, stale))
    manager = fake_manager()
    session = fake_agent_session("claude", transcript)
    demoted: list[tuple[str, str, str]] = []

    async def demote(sid: str, backend: str, native_id: str) -> None:
        demoted.append((sid, backend, native_id))

    manager.demote = demote
    await SessionManager._confirm_agent_exit(manager, session)

    assert demoted == [("mux-id", "claude", "lifecycle-id")]


async def test_shell_prompt_with_active_transcript_never_demotes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("swe_mux.session.AGENT_EXIT_CHECK_INTERVAL_SECONDS", 0.01)
    transcript = tmp_path / "native.jsonl"
    demoted: list[str] = []
    manager = fake_manager()
    session = fake_agent_session("codex", transcript)

    async def demote(sid: str, backend: str, native_id: str) -> None:
        demoted.append(sid)

    async def keep_fresh() -> None:
        for _ in range(30):
            transcript.write_text("{}\n", encoding="utf-8")
            await asyncio.sleep(0.005)

    manager.demote = demote
    await asyncio.gather(SessionManager._confirm_agent_exit(manager, session), keep_fresh())

    assert demoted == []


def test_prompt_probe_respects_promotion_grace_and_single_flight() -> None:
    manager = fake_manager()
    session = fake_agent_session("claude", None)
    session.agent_promoted_at = time.time()

    SessionManager._queue_agent_exit_check(manager, session)
    assert session.agent_exit_check_task is None

    session.agent_promoted_at = None
    SessionManager._queue_agent_exit_check(manager, session)
    assert session.agent_exit_check_task is None


async def test_prompt_probe_schedules_once_after_grace(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("swe_mux.session.AGENT_EXIT_CHECK_INTERVAL_SECONDS", 0.01)
    monkeypatch.setattr("swe_mux.session.AGENT_EXIT_CONFIRM_ATTEMPTS", 1)
    manager = fake_manager()
    session = fake_agent_session("claude", None)
    demoted: list[str] = []

    async def demote(sid: str, backend: str, native_id: str) -> None:
        demoted.append(sid)

    manager.demote = demote
    SessionManager._queue_agent_exit_check(manager, session)
    first = session.agent_exit_check_task
    assert first is not None
    SessionManager._queue_agent_exit_check(manager, session)
    assert session.agent_exit_check_task is first
    await asyncio.wait_for(first, timeout=2)
    assert demoted == ["mux-id"]


def _switch_fixture(tmp_path: Path) -> tuple[Any, Any, Path, Path]:
    current = tmp_path / "old.jsonl"
    current.write_text("{}\n", encoding="utf-8")
    stale = time.time() - 30
    os.utime(current, (stale, stale))
    fresh = tmp_path / "new.jsonl"
    fresh.write_text("{}\n", encoding="utf-8")
    manager = fake_manager()
    session = fake_agent_session("claude", current, cwd=str(tmp_path))
    session.adapter = SimpleNamespace(
        name="claude",
        recent_transcripts=lambda cwd, created_at: [
            (fresh.stat().st_mtime, fresh, "native-new"),
        ],
    )
    manager.sessions = {"mux-id": session}
    return manager, session, current, fresh


def test_transcript_switch_targets_fresh_unowned_transcript(tmp_path: Path) -> None:
    manager, session, current, fresh = _switch_fixture(tmp_path)
    assert SessionManager._transcript_switch_candidate(manager, session, current) == fresh


def test_transcript_switch_skips_while_current_transcript_active(tmp_path: Path) -> None:
    manager, session, current, _fresh = _switch_fixture(tmp_path)
    now = time.time()
    os.utime(current, (now, now))
    assert SessionManager._transcript_switch_candidate(manager, session, current) is None


def test_transcript_switch_never_steals_another_sessions_transcript(tmp_path: Path) -> None:
    manager, session, current, fresh = _switch_fixture(tmp_path)
    other = SimpleNamespace(record=agent_record("claude", str(tmp_path)), transcript_path=fresh)
    manager.sessions["other"] = other
    assert SessionManager._transcript_switch_candidate(manager, session, current) is None


def test_transcript_switch_ignores_explicitly_ended_runs(tmp_path: Path) -> None:
    manager, session, current, _fresh = _switch_fixture(tmp_path)
    session.ignored_detection_runs.add(("claude", "native-new"))
    assert SessionManager._transcript_switch_candidate(manager, session, current) is None


def test_transcript_switch_requires_actively_written_candidate(tmp_path: Path) -> None:
    manager, session, current, fresh = _switch_fixture(tmp_path)
    stale = time.time() - 30
    os.utime(fresh, (stale, stale))
    assert SessionManager._transcript_switch_candidate(manager, session, current) is None


def test_transcript_switch_blocked_by_any_sibling_in_same_cwd(tmp_path: Path) -> None:
    # A sibling agent in the same directory makes a fresh transcript ambiguous even
    # when the sibling does not own the candidate file: switching could still be
    # grabbing the sibling's just-created conversation. Never switch here.
    manager, session, current, _fresh = _switch_fixture(tmp_path)
    sibling = SimpleNamespace(
        record=agent_record("claude", str(tmp_path)),
        transcript_path=tmp_path / "unrelated.jsonl",
    )
    manager.sessions["sibling"] = sibling
    assert SessionManager._transcript_switch_candidate(manager, session, current) is None


def test_transcript_switch_allowed_when_sibling_is_in_a_different_cwd(tmp_path: Path) -> None:
    manager, session, current, fresh = _switch_fixture(tmp_path)
    other_dir = tmp_path / "other"
    elsewhere = SimpleNamespace(
        record=agent_record("claude", str(other_dir)),
        transcript_path=other_dir / "e.jsonl",
    )
    manager.sessions["elsewhere"] = elsewhere
    assert SessionManager._transcript_switch_candidate(manager, session, current) == fresh


def test_transcript_switch_ignores_ended_sibling_in_same_cwd(tmp_path: Path) -> None:
    manager, session, current, fresh = _switch_fixture(tmp_path)
    dead = agent_record("claude", str(tmp_path))
    dead.state = "exited"
    manager.sessions["dead"] = SimpleNamespace(record=dead, transcript_path=None)
    assert SessionManager._transcript_switch_candidate(manager, session, current) == fresh
