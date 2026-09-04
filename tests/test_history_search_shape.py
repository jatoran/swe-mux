"""A history search asks its indexes a bounded number of questions.

The failure this pins is a query *shape*, and no timing assertion can pin it:
the same SQL is fast on a fixture and takes a minute on a real archive, so the
thing to assert is the plan and the statement count, both of which are the same
everywhere.

Measured 2026-09-04 on the 3.07 GB primary archive (5,735 conversations,
171,910 indexed messages), searching two words across one Project:

- the page query, correlated `EXISTS (... MATCH ...)`: 5.59 s; uncorrelated
  `IN (SELECT ...)`: 0.033 s
- the page's excerpts, one statement per result row: 57 ms x 51 = 2.9 s; one
  statement for the page: 52 ms

Both ran on the single history executor thread, so while they ran nothing else
in the daemon could read history: `/api/sessions` took 46 s and live agents'
hook posts took 50 s behind five searches typed in nineteen seconds.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer, make_mocked_request

from swe_mux import app_keys as keys
from swe_mux.history import BROWSER_SEARCH_BUDGET_MS, HistoryIndex
from swe_mux.models import SessionRecord
from swe_mux.routes.history import SearchAbandoned, _search_turn, list_history

PROJECT = "project-id"


def record(identity: str, root: Path) -> SessionRecord:
    row = SessionRecord(
        identity,
        identity,
        PROJECT,
        "claude",
        f"native-{identity}",
        str(root),
        "claude.exe",
        [],
        state="idle",
        project_label=root.name,
        project_root=str(root),
    )
    row.created_at = 1_767_225_600.0
    row.agent_run_id = identity
    return row


def message(role: str, text: str, second: int) -> dict[str, Any]:
    return {
        "role": role,
        "ts": f"2026-01-01T00:00:{second:02d}Z",
        "content": [{"type": "text", "text": text}],
    }


async def seeded(tmp_path: Path, *, conversations: int = 3, matches: int = 6) -> HistoryIndex:
    """Several conversations that all match, each with more matches than a page shows.

    Every message carries its own conversation's marker word, so an excerpt that
    escaped into a neighbouring row is visible as itself rather than as a count.
    """
    root = tmp_path / "project"
    root.mkdir(parents=True, exist_ok=True)
    history = HistoryIndex(tmp_path / "mux.db")
    for index in range(conversations):
        identity = f"run-{index}"
        await history.session_started(record(identity, root), None)
        await history.replace_history_messages(
            identity,
            [
                message(
                    "user" if ordinal % 2 == 0 else "assistant",
                    f"beacon sighting {identity} number {ordinal}",
                    ordinal,
                )
                for ordinal in range(matches)
            ],
            mtime_ns=1,
            size=1,
        )
    return history


async def statements_of(history: HistoryIndex, run: Any) -> tuple[Any, list[str]]:
    """Run `run()` and collect every SQL statement it actually executed.

    `set_trace_callback` reports statements with their parameters already bound,
    so each one can be handed straight back to `EXPLAIN QUERY PLAN`.
    """
    recorded: list[str] = []
    history._db.set_trace_callback(recorded.append)  # noqa: SLF001 - the connection under test
    try:
        result = await run()
    finally:
        history._db.set_trace_callback(None)  # noqa: SLF001
    return result, recorded


def own(statements: list[str]) -> int:
    """How many statements this module's code issued.

    FTS5 reports its own internal reads through the same callback, commented out
    with a leading `--`. Those scale with how much text is indexed, which is the
    thing a fixture cannot hold constant and is not what is under test here.
    """
    return len([sql for sql in statements if not sql.lstrip().startswith("--")])


async def plans_of(history: HistoryIndex, statements: list[str]) -> dict[str, str]:
    def op() -> dict[str, str]:
        plans = {}
        for statement in statements:
            if not statement.lstrip().upper().startswith("SELECT"):
                continue
            rows = history._db.execute(f"EXPLAIN QUERY PLAN {statement}").fetchall()  # noqa: SLF001
            plans[statement] = "\n".join(str(row["detail"]) for row in rows)
        return plans

    return await history._run(op)  # noqa: SLF001


async def test_a_search_scans_the_index_once_rather_than_once_per_conversation(
    tmp_path: Path,
) -> None:
    history = await seeded(tmp_path)
    try:
        page, statements = await statements_of(
            history, lambda: history.history_page(query="beacon", project_id=PROJECT)
        )
        assert len(page["items"]) == 3
        plans = await plans_of(history, statements)
        correlated = [sql for sql, plan in plans.items() if "CORRELATED" in plan]
        assert not correlated, f"a correlated subquery is back: {correlated}"
        # One statement carries the page's excerpts, whatever the page's size.
        assert sum("snippet(" in sql for sql in statements) == 1
    finally:
        history.close()


async def test_the_statement_count_does_not_grow_with_the_page(tmp_path: Path) -> None:
    """The N+1 this replaces was invisible on a small archive and quadratic on a real one."""
    small = await seeded(tmp_path / "small", conversations=2)
    large = await seeded(tmp_path / "large", conversations=12)
    try:
        _, few = await statements_of(
            small, lambda: small.history_page(query="beacon", project_id=PROJECT)
        )
        page, many = await statements_of(
            large, lambda: large.history_page(query="beacon", project_id=PROJECT)
        )
        assert len(page["items"]) == 12
        assert own(many) == own(few), "a statement per result row is back"
    finally:
        small.close()
        large.close()


async def test_each_row_keeps_its_own_excerpts_and_counts_a_fourth_match(
    tmp_path: Path,
) -> None:
    history = await seeded(tmp_path)
    try:
        page = await history.history_page(query="beacon", project_id=PROJECT)
        for item in page["items"]:
            assert len(item["matches"]) == 3, "three shown"
            assert item["match_count"] == 4, "a fourth is counted, the rest are not ranked"
            assert all(item["id"] in match["excerpt"] for match in item["matches"]), (
                "an excerpt from another conversation reached this row"
            )
            assert [match["ordinal"] for match in item["matches"]] == sorted(
                match["ordinal"] for match in item["matches"]
            )
    finally:
        history.close()


async def test_a_role_scoped_search_excerpts_only_that_role(tmp_path: Path) -> None:
    history = await seeded(tmp_path)
    try:
        page = await history.history_page(
            query="beacon", search_scope="assistant", project_id=PROJECT
        )
        assert page["items"]
        for item in page["items"]:
            assert {match["role"] for match in item["matches"]} == {"assistant"}
    finally:
        history.close()


async def test_the_like_fallback_has_the_same_shape(tmp_path: Path) -> None:
    """The fallback runs when the rebuildable indexes are not ready, which is exactly
    when the archive is largest and least able to afford a per-row statement."""
    history = await seeded(tmp_path)
    try:

        def unready() -> None:
            history._db.execute(  # noqa: SLF001
                "UPDATE history_message_search_maintenance SET ready=0"
            )
            history._db.commit()  # noqa: SLF001

        await history._run(unready)  # noqa: SLF001
        page, statements = await statements_of(
            history, lambda: history.history_page(query="beacon", project_id=PROJECT)
        )
        assert len(page["items"]) == 3
        assert all(item["match_count"] == 4 for item in page["items"])
        assert all(
            item["id"] in match["excerpt"]
            for item in page["items"]
            for match in item["matches"]
        )
        plans = await plans_of(history, statements)
        assert not [sql for sql, plan in plans.items() if "CORRELATED" in plan]
    finally:
        history.close()


# ------------------------------------------------------------------- the route


async def _no_annotations(**_kwargs: Any) -> list[Any]:
    return []


async def test_the_route_budgets_a_search_and_leaves_a_listing_alone() -> None:
    """A search is bounded because nothing else can stop it; a plain listing is
    index-driven and cursor-keyed, and a budget there could only refuse it."""
    seen: list[int | None] = []

    async def history_page(**kwargs: Any) -> dict[str, Any]:
        seen.append(kwargs["budget_ms"])
        return {"items": [], "next_cursor": None}

    async def refresh_time_summaries(_items: list[Any]) -> int:
        return 0

    app = web.Application()
    app[keys.HISTORY] = cast(
        Any,
        SimpleNamespace(
            history_page=history_page,
            refresh_time_summaries=refresh_time_summaries,
        ),
    )
    app[keys.CONFIG] = cast(Any, SimpleNamespace(history_limit=50))
    app[keys.AUTOMATION_STORE] = cast(Any, SimpleNamespace(annotations=_no_annotations))
    app[keys.SESSIONS] = cast(Any, SimpleNamespace(conversation_holders=dict))
    app.router.add_get("/api/history", list_history)
    async with TestClient(TestServer(app)) as client:
        assert (await client.get("/api/history?q=beacon")).status == 200
        assert (await client.get("/api/history")).status == 200
    assert seen == [BROWSER_SEARCH_BUDGET_MS, None]


async def test_a_queued_search_whose_reader_left_is_not_run() -> None:
    """aiohttp does not cancel a handler when its client disconnects, so a superseded
    search would otherwise still spend the executor thread it queued for."""
    app = web.Application()
    app[keys.HISTORY_SEARCH_GATE] = asyncio.Lock()
    gone = SimpleNamespace(is_closing=lambda: True, get_extra_info=lambda *_args: None)
    request = make_mocked_request("GET", "/api/history?q=beacon", app=app, transport=gone)
    with pytest.raises(SearchAbandoned):
        async with _search_turn(request, active=True):
            pytest.fail("the search ran for a reader that had gone")


async def test_a_listing_never_waits_on_the_search_gate() -> None:
    """The gate exists to serialize scans, not to make the browser's own opening
    listing queue behind whatever somebody else is searching for."""
    app = web.Application()
    gate = asyncio.Lock()
    app[keys.HISTORY_SEARCH_GATE] = gate
    await gate.acquire()
    try:
        request = make_mocked_request("GET", "/api/history", app=app)
        async with asyncio.timeout(2):
            async with _search_turn(request, active=False):
                pass
    finally:
        gate.release()
