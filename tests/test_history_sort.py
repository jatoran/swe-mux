"""The History listing's order, and the control that picks it.

`time_basis` used to scope the date range and nothing else, so the browser's control
appeared inert: toggling it re-ran the query and got the same `spawned_at DESC` page
back. It now orders the page, and the page's cursor is that order's own answer, so
"Load more" continues the list rather than resuming a different one.

The two halves of the basis are deliberately different expressions and the tests below
pin both. A *filter* may answer "no" for a row with no such timestamp, and must: a range
on last-message that admitted runs with no messages would not be a range on last-message.
An *order* may not, because every row on the page needs a position, so it falls back
through what is known rather than collecting unindexed conversations at one end.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from swe_mux.history import HistoryIndex
from swe_mux.models import SessionRecord

PROJECT = "project-id"
#: 2026-01-01T00:00:00Z. Every stamp below is an offset from it, in seconds, so the two
#: orders under test can be read off the fixture rather than worked out.
BASE = 1_767_225_600.0


def at(offset: float) -> str:
    minutes, seconds = divmod(int(offset), 60)
    return f"2026-01-01T00:{minutes:02d}:{seconds:02d}Z"


def record(identity: str, root: Path, *, spawned_at: float) -> SessionRecord:
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
    row.created_at = spawned_at
    row.agent_run_id = identity
    return row


def message(role: str, stamp: str) -> dict[str, Any]:
    return {"role": role, "ts": stamp, "content": [{"type": "text", "text": f"{role} said it"}]}


async def seeded(tmp_path: Path) -> HistoryIndex:
    """Two runs whose start order and activity order are exactly opposite.

    `young` began at 00:00 and was last spoken to at 00:40; `early-riser` began at 00:20
    and stopped at 00:30. So "session started" puts `early-riser` first and "last
    activity" puts `young` first, and any test below that cannot tell the two orders
    apart is not testing the order.
    """
    root = tmp_path / "project"
    root.mkdir()
    history = HistoryIndex(tmp_path / "mux.db")
    await history.session_started(record("early-riser", root, spawned_at=BASE + 1_100), None)
    await history.session_started(record("young", root, spawned_at=BASE - 100), None)
    await history.replace_history_messages(
        "early-riser",
        [message("user", at(1_200)), message("assistant", at(1_800))],
        mtime_ns=1,
        size=1,
    )
    await history.replace_history_messages(
        "young",
        [message("user", at(0)), message("assistant", at(2_400))],
        mtime_ns=1,
        size=1,
    )
    return history


async def with_silent_run(history: HistoryIndex, tmp_path: Path) -> None:
    """A run that never indexed a message, spawned between the other two's last words."""
    await history.session_started(
        record("silent", tmp_path / "project", spawned_at=BASE + 2_100), None
    )


async def test_the_default_order_is_last_activity_not_session_start(tmp_path: Path) -> None:
    history = await seeded(tmp_path)
    page = await history.history_page(project_id=PROJECT)
    assert [item["id"] for item in page["items"]] == ["young", "early-riser"]


async def test_the_basis_actually_reorders_the_page(tmp_path: Path) -> None:
    """The reported bug: toggling the control changed nothing at all."""
    history = await seeded(tmp_path)
    by_activity = await history.history_page(project_id=PROJECT, time_basis="last_message")
    by_start = await history.history_page(project_id=PROJECT, time_basis="started")
    assert [item["id"] for item in by_activity["items"]] == ["young", "early-riser"]
    assert [item["id"] for item in by_start["items"]] == ["early-riser", "young"]


async def test_a_run_with_no_messages_keeps_a_position_in_the_activity_order(
    tmp_path: Path,
) -> None:
    """A NULL sort key would collect every unindexed run at one end of the list."""
    history = await seeded(tmp_path)
    await with_silent_run(history, tmp_path)

    page = await history.history_page(project_id=PROJECT, time_basis="last_message")

    # In the middle, by the best stamp it has, rather than at whichever end NULL sorts to.
    assert [item["id"] for item in page["items"]] == ["young", "silent", "early-riser"]
    assert page["items"][1]["last_message_at"] is None


async def test_a_date_range_on_last_message_still_excludes_a_run_with_none(
    tmp_path: Path,
) -> None:
    """The order falls back; the filter does not. A range on last-message asks a question
    a run with no messages answers no to, whatever the order does with it."""
    history = await seeded(tmp_path)
    await with_silent_run(history, tmp_path)

    page = await history.history_page(
        project_id=PROJECT, time_basis="last_message", date_from=BASE, date_to=BASE + 3_000
    )

    assert [item["id"] for item in page["items"]] == ["young", "early-riser"]


async def test_paging_continues_the_list_it_started(tmp_path: Path) -> None:
    """The cursor is the ORDER BY's own answer. Keyed on `spawned_at` while the page was
    ordered by activity, "Load more" would skip and repeat rows instead of continuing."""
    history = await seeded(tmp_path)
    await with_silent_run(history, tmp_path)

    seen: list[str] = []
    cursor: str | None = None
    for _page in range(4):
        page = await history.history_page(project_id=PROJECT, limit=1, cursor=cursor)
        seen.extend(item["id"] for item in page["items"])
        cursor = page["next_cursor"]
        if cursor is None:
            break

    assert seen == ["young", "silent", "early-riser"]
    assert cursor is None


async def test_the_ordering_value_is_not_part_of_the_public_row(tmp_path: Path) -> None:
    history = await seeded(tmp_path)
    page = await history.history_page(project_id=PROJECT)
    assert not [key for key in page["items"][0] if key.startswith("_")]


async def test_an_unsearchable_query_matches_no_messages_rather_than_metadata(
    tmp_path: Path,
) -> None:
    """`???` has no word characters, so no indexed message can match it - `MATCH ''` is an
    FTS syntax error. Asking for User prompts used to fall through to a *metadata* search
    and answer with rows whose name or path contained the punctuation instead."""
    root = tmp_path / "project"
    root.mkdir(exist_ok=True)
    history = HistoryIndex(tmp_path / "mux.db")
    await history.session_started(record("???", root, spawned_at=100.0), None)

    assert (await history.history_page(query="???", search_scope="user"))["items"] == []
    assert (await history.history_page(query="???", search_scope="assistant"))["items"] == []
    # Metadata is a plain LIKE and handles punctuation, so the scopes that include it
    # still answer - which is what makes the message-scoped silence a scope rule rather
    # than an inability to search for the string at all.
    assert [item["id"] for item in (await history.history_page(query="???"))["items"]] == ["???"]
    assert [
        item["id"]
        for item in (await history.history_page(query="???", search_scope="metadata"))["items"]
    ] == ["???"]


async def test_an_unknown_basis_is_refused_rather_than_silently_ignored(
    tmp_path: Path,
) -> None:
    history = await seeded(tmp_path)
    with pytest.raises(ValueError):
        await history.history_page(time_basis="whenever")
