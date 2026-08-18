"""Naming the two ends of a lineage edge, and keeping the message a branch was cut at."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from swe_mux.automation_store import AutomationStore
from swe_mux.models import SessionRecord
from swe_mux.server import BRANCH_CUT_EXCERPT_CHARS, _branch_cut_excerpt, list_lineage


class HistoryNamingStub:
    def __init__(self, *rows: dict[str, object]) -> None:
        self.rows = list(rows)

    async def history_naming_rows(self, ids: object) -> dict[str, dict[str, object]]:
        wanted = set(ids or [])
        return {
            str(row["id"]): {
                "id": row["id"],
                "note_id": None,
                "name": row.get("name", ""),
                "auto_named": row.get("auto_named", 1),
            }
            for row in self.rows
            if row["id"] in wanted
        }


async def test_lineage_names_both_ends_the_way_every_other_surface_does(
    tmp_path: Path,
) -> None:
    """A lineage edge names two *runs*, and only the daemon can resolve them.

    Each end is in one of three states and the browser can see none of them: a live
    session's display name comes from the session manager, an ended one's from its
    History row, and a deleted one has neither. Left undecorated the section printed
    raw ids, which is what it did until this existed.
    """
    store = AutomationStore(tmp_path / "mux.db")
    await store.add_lineage("run-source", "run-live", "branch", {"mode": "after"})
    await store.add_lineage("run-source", "run-ended", "branch", {"mode": "before"})
    await store.add_lineage("run-source", "run-deleted", "branch", {})
    await store.create_annotation(
        agent_run_id="run-live",
        session_id="pty-live",
        tag="title",
        content="Update ABC",
        source_event_seq=1,
        rule_id="builtin.titler",
        rule_revision="r1",
        provenance="openrouter_observer",
    )
    live = SessionRecord(
        "pty-live",
        "claude-6vried",
        "default",
        "claude",
        "native-live",
        str(tmp_path),
        "claude",
        [],
    )
    live.agent_run_id = "run-live"
    app = web.Application()
    app["automation_store"] = store
    app["sessions"] = SimpleNamespace(sessions={"pty-live": SimpleNamespace(record=live)})
    app["history"] = HistoryNamingStub(
        {"id": "run-source", "name": "Update ABC", "auto_named": 0},
        {"id": "run-ended", "name": "B2-Update ABC", "auto_named": 0},
    )
    app.router.add_get("/lineage", list_lineage)

    async with TestClient(TestServer(app)) as client:
        response = await client.get("/lineage?run_id=run-source")
        items = (await response.json())["items"]
    store.close()

    by_child = {item["child_run_id"]: item for item in items}
    # A live pane is named by the session manager, and its generated title wins while
    # the session is auto-named - the same rule the sidebar applies. The raw
    # `claude-6vried` this record still carries is exactly what the section used to
    # show instead.
    assert by_child["run-live"]["child"] == {
        "name": "Update ABC",
        "live": True,
        "known": True,
        "session_id": "pty-live",
    }
    # An ended one is named by its History row, where a rename outranks a title.
    assert by_child["run-ended"]["child"] == {
        "name": "B2-Update ABC",
        "live": False,
        "known": True,
    }
    # A deleted row is reported as unknown rather than dropped: the edge still records
    # that the fork happened, and removing it would silently reshape the lineage.
    assert by_child["run-deleted"]["child"] == {"name": "", "live": False, "known": False}
    # Both ends are resolved, not just the far one.
    assert by_child["run-live"]["parent"]["name"] == "Update ABC"


async def test_an_empty_lineage_costs_no_reads() -> None:
    """The decoration must not turn "this run has no relatives" into two queries."""

    class Exploding:
        async def history_naming_rows(self, ids: object) -> dict[str, dict[str, object]]:
            raise AssertionError("an empty lineage must not read History")

    app = web.Application()
    app["automation_store"] = SimpleNamespace(lineage=_no_edges)
    app["sessions"] = SimpleNamespace(sessions={})
    app["history"] = Exploding()
    app.router.add_get("/lineage", list_lineage)

    async with TestClient(TestServer(app)) as client:
        response = await client.get("/lineage?run_id=run-alone")
        assert (await response.json())["items"] == []


async def _no_edges(run_id: str | None = None) -> list[dict[str, object]]:
    del run_id
    return []


def test_a_branch_edge_keeps_the_message_it_was_cut_at() -> None:
    """Kept with the branch rather than resolved from `cut_offset` on demand.

    The only reader is a human asking where a conversation came from, weeks later, by
    which time the parent transcript may have been compacted, relocated by a cwd
    change, or deleted outright - and re-reading a whole conversation to render one
    line is the wrong shape even when it is still there. Bounded, because a lineage row
    must never become a second copy of a conversation in a table that is not a
    transcript store.
    """
    assert _branch_cut_excerpt("  two\n\nlines  ") == "two lines"
    excerpt = _branch_cut_excerpt("x" * (BRANCH_CUT_EXCERPT_CHARS + 50))
    assert len(excerpt) == BRANCH_CUT_EXCERPT_CHARS
    assert excerpt.endswith("…")
    assert _branch_cut_excerpt("") == ""
