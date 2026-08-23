"""A history search is bounded, and says so instead of hanging.

The failure this pins is not slowness, it is unboundedness. `search_history`
holds the single history executor thread while it runs, so a search that never
finishes takes every other history read with it: measured 2026-08-23 on a 2.79 GB
database, `/api/sessions` timed out for minutes while `/api/health` still answered
instantly, and the chat sat on "running search_history" with no way to stop it.
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from swe_mux.history import HistoryIndex, HistorySearchBudgetExceeded


async def test_a_budgeted_search_returns_normally_when_it_fits(tmp_path: Path) -> None:
    history = HistoryIndex(tmp_path / "mux.db")
    try:
        page = await history.history_page(query="anything", limit=5, budget_ms=4_000)
        assert "items" in page
        # An empty archive is the trivial case; the point is that passing a
        # budget does not change the shape of an ordinary answer.
        assert page["items"] == []
    finally:
        history.close()


async def test_an_overrunning_search_raises_instead_of_holding_the_thread(
    tmp_path: Path,
) -> None:
    history = HistoryIndex(tmp_path / "mux.db")
    try:
        # A budget of 0 clamps to the 50 ms floor, and the recursive CTE below
        # runs far longer than that - so this exercises the real mechanism (the
        # SQLite progress handler aborting a running statement) rather than a
        # pre-flight check that never enters the engine.
        def op() -> None:
            with history._deadline(0):  # noqa: SLF001 - the guard under test
                history._db.execute(  # noqa: SLF001
                    "WITH RECURSIVE spin(n) AS ("
                    "  SELECT 1 UNION ALL SELECT n+1 FROM spin WHERE n < 80000000"
                    ") SELECT count(*) FROM spin"
                ).fetchone()

        started = time.monotonic()
        with pytest.raises(HistorySearchBudgetExceeded) as raised:
            await history._run(op)  # noqa: SLF001
        elapsed = time.monotonic() - started
        # Bounded in fact, not just in intention: the statement is aborted mid
        # flight, which is the whole difference from a timeout that fires while
        # the query keeps running and keeps holding the lock.
        assert elapsed < 5, f"the deadline did not abort the statement ({elapsed:.1f}s)"
        assert "budget" in str(raised.value)
        assert raised.value.budget_ms == 0
    finally:
        history.close()


async def test_the_database_is_usable_immediately_after_a_budget_abort(
    tmp_path: Path,
) -> None:
    """The lock and the progress handler are both released.

    An abort that left the handler installed would make every later query on
    this connection die instantly, turning one slow search into a permanently
    broken history - strictly worse than the hang it replaced.
    """
    history = HistoryIndex(tmp_path / "mux.db")
    try:

        def op() -> None:
            with history._deadline(0):  # noqa: SLF001
                history._db.execute(  # noqa: SLF001
                    "WITH RECURSIVE spin(n) AS ("
                    "  SELECT 1 UNION ALL SELECT n+1 FROM spin WHERE n < 80000000"
                    ") SELECT count(*) FROM spin"
                ).fetchone()

        with pytest.raises(HistorySearchBudgetExceeded):
            await history._run(op)  # noqa: SLF001

        page = await history.history_page(query="anything", limit=5)
        assert page["items"] == []
    finally:
        history.close()
