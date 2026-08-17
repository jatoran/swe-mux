"""Runs abandoned by a shutdown that never got to record an end.

Nothing used to close these. A hard crash - the machine, the app, or the daemon
and its PTY owner together - left every running session's row with
``exited_at IS NULL`` for the life of the database, so those runs kept reading as
in-progress in History and in everything that aggregates open runs, on every
later boot. The startup sweep runs after both recovery paths have claimed what
they can, so the only rows it may touch are the ones with nothing behind them.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from swe_mux.history import HistoryIndex
from swe_mux.models import SessionRecord


def record(sid: str, backend: str = "shell") -> SessionRecord:
    return SessionRecord(sid, sid, "project", cast(Any, backend), sid, "C:/repo", "cmd.exe", [])


async def open_runs(history: HistoryIndex) -> set[str]:
    def op() -> set[str]:
        return {
            str(row["id"])
            for row in history._db.execute(
                "SELECT id FROM history WHERE exited_at IS NULL"
            ).fetchall()
        }

    return await history._run(op)


async def test_the_sweep_closes_abandoned_runs_and_spares_live_ones(tmp_path: Path) -> None:
    history = HistoryIndex(tmp_path / "mux.db")
    try:
        for sid in ("abandoned", "still-running"):
            await history.session_started(record(sid, "claude"), f"C:/t/{sid}.jsonl")
        assert await open_runs(history) == {"abandoned", "still-running"}

        assert await history.close_orphaned_runs({"still-running"}) == 1
        assert await open_runs(history) == {"still-running"}

        row = await history.history_entry("abandoned")
        assert row is not None
        assert row["final_state"] == "crashed"
        assert row["exit_reason"] == "crashed"
        # Dated from the row's own last known activity, not from now: dating a
        # crash at the moment somebody restarted the app would stretch the run by
        # however long the machine was off.
        assert row["exited_at"] == row["spawned_at"]
    finally:
        history.close()


async def test_the_sweep_is_idempotent(tmp_path: Path) -> None:
    history = HistoryIndex(tmp_path / "mux.db")
    try:
        await history.session_started(record("abandoned", "claude"), "C:/t/a.jsonl")
        assert await history.close_orphaned_runs(set()) == 1
        assert await history.close_orphaned_runs(set()) == 0
    finally:
        history.close()


async def test_external_history_is_never_swept(tmp_path: Path) -> None:
    """Imported rows describe conversations mux never ran, so "no end recorded"
    says nothing about a process this daemon owned."""
    history = HistoryIndex(tmp_path / "mux.db")
    try:

        def op() -> None:
            history._db.execute(
                "INSERT INTO history(id,native_id,backend,name,cwd,spawned_at,external) "
                "VALUES('imported','n','claude','Imported','C:/repo',1.0,1)"
            )
            history._db.commit()

        await history._run(op)
        assert await history.close_orphaned_runs(set()) == 0
        assert await open_runs(history) == {"imported"}
    finally:
        history.close()


async def test_a_running_shells_row_survives_a_restart(tmp_path: Path) -> None:
    """The boot-time prune used to delete every unpromoted shell row
    unconditionally, which erased the durable record of exactly the shells a
    post-crash recovery reads - and made shell crash forensics impossible.
    """
    path = tmp_path / "mux.db"
    history = HistoryIndex(path)
    try:
        await history.session_started(record("running-shell"), None)
        await history.session_started(record("finished-shell"), None)
        await history.session_ended(record("finished-shell"), "exited")
    finally:
        history.close()

    reopened = HistoryIndex(path)
    try:
        assert await reopened.history_entry("running-shell") is not None
        assert await reopened.history_entry("finished-shell") is None
    finally:
        reopened.close()
