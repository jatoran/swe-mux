"""Hook-spool replay identity, staleness, and the terminal latch.

The spool is the durable fallback for terminal hook events whose POST failed. It
is keyed by mux session id, which is stable across demote/re-promote and across
the session's death, so replay needs guards it did not have:

- a spooled `SessionEnd` arriving after PTY EOF resurrected a dead session to idle
- a stale `Stop` from turn N force-idled live turn N+1
- `demote()` never unlinked the spool, so a leftover replayed into the next promotion
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

from swe_mux.event_bus import EventBus
from swe_mux.models import SessionRecord
from swe_mux.session import Session, SessionManager, apply_state_transition


def _record(state: str = "working") -> SessionRecord:
    return SessionRecord(
        "sid",
        "agent",
        "default",
        "claude",
        "native",
        ".",
        "claude.exe",
        [],
        state=state,  # type: ignore[arg-type]
    )


def _session(record: SessionRecord) -> Session:
    pty = cast(Any, SimpleNamespace(graceful_exit="", isalive=lambda: True))
    session = Session(record, pty, cast(Any, SimpleNamespace()), 32, "secret")
    session.record.agent_run_id = "run-2"
    session.record.agent_run_started_at = 1_000.0
    session.observation_state = {
        "root_turn_active": True,
        "root_completion_seen": False,
        "turn_started_at": 2_000.0,
    }
    return session


def _manager(spool_dir: Path) -> Any:
    manager = cast(Any, SessionManager.__new__(SessionManager))
    manager.hook_spool_dir = spool_dir
    manager.events = EventBus()
    spool_dir.mkdir(parents=True, exist_ok=True)
    return manager


def _write_spool(spool_dir: Path, entries: list[dict[str, Any]]) -> Path:
    path = spool_dir / "sid.jsonl"
    path.write_bytes(b"".join(json.dumps(entry).encode() + b"\n" for entry in entries))
    # The drain refuses to consume a file the shim might still be appending to.
    import os

    stale = time.time() - 10
    os.utime(path, (stale, stale))
    return path


# ---- terminal latch ----------------------------------------------------------


def test_a_hook_cannot_resurrect_an_exited_session() -> None:
    record = _record(state="exited")
    session = _session(record)
    assert (
        apply_state_transition(
            session, "idle", None, source="hook", evidence="SessionEnd", force=True
        )
        is False
    )
    assert record.state == "exited"
    refusals = [
        entry for entry in session.state_transitions if entry.get("kind") == "transition_refused"
    ]
    assert refusals and refusals[-1]["reason"] == "terminal_latch"


def test_the_daemon_may_still_move_a_session_out_of_a_terminal_state() -> None:
    record = _record(state="crashed")
    session = _session(record)
    assert (
        apply_state_transition(session, "running", None, source="daemon", evidence="relaunch")
        is True
    )
    assert record.state == "running"


def test_a_transition_into_a_terminal_state_is_never_blocked() -> None:
    record = _record(state="working")
    session = _session(record)
    assert apply_state_transition(session, "exited", None, source="daemon") is True
    assert record.state == "exited"


# ---- spool staleness ---------------------------------------------------------


async def test_stale_terminal_event_is_discarded_not_replayed(tmp_path: Path) -> None:
    """A Stop spooled during turn N must not close turn N+1."""
    manager = _manager(tmp_path / "hook-spool")
    session = _session(_record())
    _write_spool(
        manager.hook_spool_dir,
        [{"event": "Stop", "payload": {}, "spooled_at": 1_999.0}],
    )

    await manager._drain_hook_spool(session)

    kinds = [entry.get("kind") for entry in session.state_transitions]
    assert "hook_spool_discarded" in kinds
    assert "hook_spool_replay" not in kinds
    assert session.record.state == "working"


async def test_fresh_terminal_event_is_replayed(tmp_path: Path) -> None:
    manager = _manager(tmp_path / "hook-spool")
    session = _session(_record())
    _write_spool(
        manager.hook_spool_dir,
        [{"event": "Stop", "payload": {}, "spooled_at": 2_001.0}],
    )

    await manager._drain_hook_spool(session)

    kinds = [entry.get("kind") for entry in session.state_transitions]
    assert "hook_spool_replay" in kinds
    assert session.record.state == "idle"


async def test_unstamped_legacy_entries_still_replay(tmp_path: Path) -> None:
    """A spool written by an older shim carries no timestamp; it is not dropped."""
    manager = _manager(tmp_path / "hook-spool")
    session = _session(_record())
    _write_spool(manager.hook_spool_dir, [{"event": "Stop", "payload": {}}])

    await manager._drain_hook_spool(session)

    assert "hook_spool_replay" in [
        entry.get("kind") for entry in session.state_transitions
    ]


async def test_drain_discards_the_spool_for_a_dead_session(tmp_path: Path) -> None:
    """The shim can recreate the spool after _mark_ended unlinked it."""
    manager = _manager(tmp_path / "hook-spool")
    session = _session(_record(state="exited"))
    path = _write_spool(
        manager.hook_spool_dir,
        [{"event": "SessionEnd", "payload": {}, "spooled_at": time.time()}],
    )

    await manager._drain_hook_spool(session)

    assert not path.exists()
    assert session.record.state == "exited"
    assert "hook_spool_replay" not in [
        entry.get("kind") for entry in session.state_transitions
    ]


async def test_consumed_spool_is_removed_and_a_later_append_survives(tmp_path: Path) -> None:
    manager = _manager(tmp_path / "hook-spool")
    session = _session(_record())
    path = _write_spool(
        manager.hook_spool_dir,
        [{"event": "Stop", "payload": {}, "spooled_at": 2_001.0}],
    )

    await manager._drain_hook_spool(session)

    # Consumed cleanly: neither the live spool nor the in-progress copy remains,
    # so a shim append after this point starts a fresh file.
    assert not path.exists()
    assert not (path.parent / f"{path.name}.consuming").exists()


def test_discard_removes_both_the_live_and_in_progress_spool(tmp_path: Path) -> None:
    manager = _manager(tmp_path / "hook-spool")
    path = manager.hook_spool_dir / "sid.jsonl"
    path.write_bytes(b"{}\n")
    consuming = path.parent / f"{path.name}.consuming"
    consuming.write_bytes(b"{}\n")

    manager.discard_hook_spool("sid")

    assert not path.exists()
    assert not consuming.exists()
