from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from swe_mux import session_titles


class _Store:
    """An annotation store that records how it was asked, not just what it answered."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.calls: list[list[str]] = []

    async def annotations(self, **kwargs: Any) -> list[dict[str, Any]]:
        requested = list(kwargs.get("agent_run_ids") or [])
        self.calls.append(requested)
        assert kwargs.get("tag") == "title"
        return [row for row in self.rows if str(row["agent_run_id"]) in set(requested)]


def test_a_generated_title_names_a_session_only_while_it_is_auto_named() -> None:
    titles = {"run-1": "Fix the parser"}
    auto = SimpleNamespace(id="s", agent_run_id="run-1", name="claude-0e7d93", auto_named=True)
    renamed = SimpleNamespace(id="s", agent_run_id="run-1", name="release prep", auto_named=False)

    assert session_titles.record_display_name(auto, titles) == "Fix the parser"
    assert session_titles.record_display_name(renamed, titles) == "release prep"
    assert session_titles.record_display_name(auto, {}) == "claude-0e7d93"


def test_rows_apply_the_same_rule_to_sqlite_integers() -> None:
    titles = {"run-1": "Fix the parser"}
    auto = {"id": "run-1", "name": "claude-0e7d93", "auto_named": 1}
    renamed = {"id": "run-1", "name": "release prep", "auto_named": 0}
    # A row written before the column existed is auto-named, not un-named.
    legacy = {"id": "run-1", "name": "claude-0e7d93"}

    assert session_titles.row_display_name(auto, titles) == "Fix the parser"
    assert session_titles.row_display_name(renamed, titles) == "release prep"
    assert session_titles.row_display_name(legacy, titles) == "Fix the parser"


def test_the_run_id_falls_back_to_the_session_id_the_history_row_is_keyed_by() -> None:
    assert session_titles.record_run_id(SimpleNamespace(id="s", agent_run_id="run-1")) == "run-1"
    assert session_titles.record_run_id(SimpleNamespace(id="s", agent_run_id=None)) == "s"
    assert session_titles.row_run_id({"id": "s", "agent_run_id": ""}) == "s"
    assert session_titles.row_run_id({}) == ""


async def test_titles_are_looked_up_by_id_rather_than_swept_off_the_newest() -> None:
    """The lookup must not depend on how recently a run was titled.

    Git provenance names sessions across the whole life of a repository, and a window of
    the newest N annotations would render every older run as never having been titled.
    """
    store = _Store(
        [
            {"agent_run_id": "run-1", "content": "Newest title"},
            {"agent_run_id": "run-1", "content": "Older title"},
            {"agent_run_id": "run-2", "content": "  "},
            {"agent_run_id": "unasked", "content": "Not requested"},
        ]
    )

    titles = await session_titles.generated_titles(store, {"run-1", "run-2", ""})

    assert store.calls == [["run-1", "run-2"]]
    # The store orders newest first, so the first hit per run is the current title.
    assert titles == {"run-1": "Newest title"}


async def test_a_large_id_set_is_chunked_below_the_sqlite_variable_ceiling() -> None:
    ids = {f"run-{index}" for index in range(1000)}
    store = _Store([{"agent_run_id": "run-999", "content": "Land the migration"}])

    titles = await session_titles.generated_titles(store, ids)

    assert len(store.calls) == 3
    assert all(len(call) <= 400 for call in store.calls)
    assert titles == {"run-999": "Land the migration"}


async def test_no_store_and_no_ids_are_answered_without_a_query() -> None:
    store = _Store([])
    assert await session_titles.generated_titles(store, set()) == {}
    assert await session_titles.generated_titles(None, {"run-1"}) == {}
    assert store.calls == []
