"""The History reader opens on the message index and parses nothing on the way.

Measured 2026-10-12 on the development host before this existed: opening a 6.9 MiB
Claude conversation took 1,462 ms and shipped 1.36 MB, a 48.7 MiB Codex rollout took
1,329 ms and shipped 4.82 MB, and the browser drew nothing - Resume included - until the
whole parse came back. The text it was waiting for was already in `history_messages`,
readable in 2-9 ms.

These tests pin the replacement: `/detail` answers from the row and the index, and
schedules a background reindex instead of parsing when the index is stale; `/messages`
pages the index by ordinal; tool calls are the one read that still parses, and only
when asked for; and the whole-transcript route stops rewriting an index that is
already current.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from swe_mux import app_keys as keys
from swe_mux import history as history_module
from swe_mux import routes
from swe_mux.event_bus import EventBus
from swe_mux.history import HISTORY_MESSAGES_PAGE_MAX, HistoryIndex
from swe_mux.models import SessionRecord
from swe_mux.routes.history import (
    history_detail,
    history_matches,
    history_messages,
    history_notes,
    history_tool_calls,
    history_transcript,
)
from swe_mux.server import error_middleware
from swe_mux.transcript_view import ParsedConversation

RUN = "run-reader"
PROJECT = "proj-reader"
TURNS = 30


def claude_records(turns: int) -> list[dict[str, Any]]:
    """A conversation of ``turns`` exchanges, each three parsed messages long.

    Ordinal ``3n`` is the question, ``3n+1`` the answer with a Read call, and ``3n+2``
    an assistant message that is nothing but a Bash call. The third one is what the
    index leaves out (it has no text), so the indexed ordinals have gaps exactly where
    a tool-only message sits - the case the reader's tool-call merge exists for.
    """
    records: list[dict[str, Any]] = []
    for turn in range(turns):
        stamp = f"2026-09-01T10:{turn // 60:02d}:{turn % 60:02d}Z"
        records.append(
            {
                "type": "user",
                "timestamp": stamp,
                "message": {
                    "role": "user",
                    "content": [{"type": "text", "text": f"question {turn} about lanterns"}],
                },
            }
        )
        records.append(
            {
                "type": "assistant",
                "timestamp": stamp,
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "text", "text": f"answer {turn}"},
                        {
                            "type": "tool_use",
                            "id": f"toolu_read_{turn}",
                            "name": "Read",
                            "input": {"file_path": f"src/file_{turn}.py"},
                        },
                    ],
                },
            }
        )
        records.append(
            {
                "type": "assistant",
                "timestamp": stamp,
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": f"toolu_bash_{turn}",
                            "name": "Bash",
                            "input": {"command": f"echo {turn}"},
                        }
                    ],
                },
            }
        )
    return records


def write_conversation(path: Path, turns: int = TURNS) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(record) for record in claude_records(turns)) + "\n",
        encoding="utf-8",
    )
    return path


async def seeded(tmp_path: Path, *, turns: int = TURNS) -> tuple[HistoryIndex, Path]:
    transcript = write_conversation(tmp_path / "conversation.jsonl", turns)
    history = HistoryIndex(tmp_path / "mux.db")
    record = SessionRecord(
        RUN, "reader", PROJECT, "claude", "native-reader", str(tmp_path), "c", []
    )
    record.agent_run_id = RUN
    await history.session_started(record, str(transcript))
    return history, transcript


async def _annotations(**kwargs: Any) -> list[dict[str, Any]]:
    if kwargs.get("tag") == "title":
        return [
            {
                "id": "title-1",
                "agent_run_id": RUN,
                "content": "Lantern questions",
                "evidence_json": json.dumps([{"kind": "title_lifecycle", "stability": "settled"}]),
                "provenance": "model",
                "cost_usd": 0.01,
            }
        ]
    return [{"id": "note-1", "agent_run_id": RUN, "tag": "turn-summary", "content": "a note"}]


async def _scan_records(**_kwargs: Any) -> list[dict[str, Any]]:
    return [{"id": "scan-1", "agent_run_id": RUN, "t0": 1.0, "t1": 2.0, "summary": "scanned"}]


def reader_app(history: HistoryIndex) -> web.Application:
    app = web.Application(middlewares=[error_middleware])
    app[keys.HISTORY] = history
    app[keys.EVENTS] = EventBus()
    app[keys.SESSIONS] = cast(Any, SimpleNamespace(adapters={}, conversation_holders=dict))
    app[keys.AUTOMATION_STORE] = cast(
        Any, SimpleNamespace(annotations=_annotations, scan_records=_scan_records)
    )
    app.router.add_get("/api/history/{sid}/transcript", history_transcript)
    app.router.add_get("/api/history/{sid}/detail", history_detail)
    app.router.add_get("/api/history/{sid}/messages", history_messages)
    app.router.add_get("/api/history/{sid}/matches", history_matches)
    app.router.add_get("/api/history/{sid}/notes", history_notes)
    app.router.add_get("/api/history/{sid}/tool-calls", history_tool_calls)
    return app


def forbid_parsing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make any transcript parse on this path fail the test that reached it."""

    def refuse(*_args: Any, **_kwargs: Any) -> ParsedConversation:
        raise AssertionError("the reader parsed a transcript on a path that must not")

    monkeypatch.setattr(history_module, "read_history_conversation", refuse)
    # The reader behind that seam (`conversation_index`), in case a path reaches it
    # without going through the seam.
    monkeypatch.setattr(history_module, "indexed_parse", refuse)
    monkeypatch.setattr("swe_mux.routes.history.read_history_conversation", refuse)


# ------------------------------------------------------------------------ /detail


async def test_detail_opens_on_the_row_and_reindexes_in_the_background(tmp_path: Path) -> None:
    history, _transcript = await seeded(tmp_path)
    try:
        async with TestClient(TestServer(reader_app(history))) as client:
            first = await (await client.get(f"/api/history/{RUN}/detail")).json()
            # Unindexed: the header still has everything it draws, and the answer says a
            # reindex is under way rather than making the reader wait for one.
            assert first["entry"]["id"] == RUN
            assert first["transcript"]["available"] is True
            assert first["transcript"]["indexed"] is False
            assert first["transcript"]["indexing"] is True
            assert "watermark" not in first["transcript"]
            history.wait_for_background(timeout=10)
            second = await (await client.get(f"/api/history/{RUN}/detail")).json()
        state = second["transcript"]
        assert state["indexed"] is True and state["current"] is True
        assert state["indexing"] is False
        assert state["message_count"] == 2 * TURNS, "the tool-only messages are not text"
        assert state["abandoned_messages"] == 0
        assert state["tool_calls"] == 2 * TURNS
    finally:
        history.close()


async def test_a_current_index_answers_detail_without_parsing_or_scheduling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    history, transcript = await seeded(tmp_path)
    try:
        assert (await history.index_transcript(RUN, transcript, "claude"))[0] == "indexed"
        forbid_parsing(monkeypatch)
        async with TestClient(TestServer(reader_app(history))) as client:
            response = await client.get(f"/api/history/{RUN}/detail")
            assert response.status == 200
            body = await response.json()
        assert body["transcript"]["current"] is True
        assert body["transcript"]["indexing"] is False
        assert not history.transcript_reindexing(RUN)
    finally:
        history.close()


async def test_detail_ships_a_slim_row(tmp_path: Path) -> None:
    """No argv, and only the title's stability out of the whole annotation row."""
    history, transcript = await seeded(tmp_path)
    try:
        await history.index_transcript(RUN, transcript, "claude")
        async with TestClient(TestServer(reader_app(history))) as client:
            body = await (await client.get(f"/api/history/{RUN}/detail")).json()
        entry = body["entry"]
        assert "argv_json" not in entry
        assert entry["generated_title"] == "Lantern questions"
        assert entry["generated_title_annotation"] == {"stability": "settled"}
    finally:
        history.close()


async def test_a_conversation_whose_file_is_gone_still_opens_on_its_index(
    tmp_path: Path,
) -> None:
    """The indexed text is still the conversation; Resume refuses on its own."""
    history, transcript = await seeded(tmp_path)
    try:
        await history.index_transcript(RUN, transcript, "claude")
        transcript.unlink()
        async with TestClient(TestServer(reader_app(history))) as client:
            detail = await client.get(f"/api/history/{RUN}/detail")
            assert detail.status == 200
            state = (await detail.json())["transcript"]
            page = await (await client.get(f"/api/history/{RUN}/messages")).json()
            tools = await client.get(f"/api/history/{RUN}/tool-calls")
            assert tools.status == 409
            assert (await tools.json())["code"] == "transcript_unavailable"
        assert state["available"] is False
        assert state["indexed"] is True and state["indexing"] is False
        assert len(page["messages"]) == 2 * TURNS
    finally:
        history.close()


async def test_an_unknown_row_is_a_404_on_every_reader_route(tmp_path: Path) -> None:
    history, _transcript = await seeded(tmp_path)
    try:
        async with TestClient(TestServer(reader_app(history))) as client:
            for route in ("detail", "messages", "matches?q=x", "notes", "tool-calls"):
                response = await client.get(f"/api/history/missing/{route}")
                assert response.status == 404, route
    finally:
        history.close()


# ---------------------------------------------------------------------- /messages


async def test_messages_page_through_the_index_by_ordinal(tmp_path: Path) -> None:
    history, transcript = await seeded(tmp_path)
    try:
        await history.index_transcript(RUN, transcript, "claude")
        async with TestClient(TestServer(reader_app(history))) as client:

            async def page(query: str) -> dict[str, Any]:
                response = await client.get(f"/api/history/{RUN}/messages?{query}")
                assert response.status == 200, query
                return cast(dict[str, Any], await response.json())

            opening = await page("limit=10")
            ordinals = [item["ordinal"] for item in opening["messages"]]
            # The parse's own indices, gaps included where the tool-only messages sit.
            assert ordinals == [0, 1, 3, 4, 6, 7, 9, 10, 12, 13]
            assert opening["has_more_before"] is False and opening["has_more_after"] is True
            assert opening["messages"][0] == {
                "ordinal": 0,
                "role": "user",
                "ts": "2026-09-01T10:00:00Z",
                "text": "question 0 about lanterns",
            }
            following = await page("limit=10&after=13")
            assert following["messages"][0]["ordinal"] == 15
            assert following["has_more_before"] is True

            last_ordinal = 3 * (TURNS - 1) + 1
            tail = await page(f"limit=4&before={last_ordinal + 1}")
            assert [item["ordinal"] for item in tail["messages"]] == [
                last_ordinal - 4,
                last_ordinal - 3,
                last_ordinal - 1,
                last_ordinal,
            ]
            assert tail["has_more_after"] is False

            # Half the page before the anchor and the rest from it, the anchor included.
            centred = await page("limit=6&around=30")
            assert [item["ordinal"] for item in centred["messages"]] == [25, 27, 28, 30, 31, 33]
            assert centred["has_more_before"] and centred["has_more_after"]

            beyond = await page(f"after={last_ordinal}")
            assert beyond == {"messages": [], "has_more_before": False, "has_more_after": False}

            bad = await client.get(f"/api/history/{RUN}/messages?after=soon")
            assert bad.status == 400
    finally:
        history.close()


async def test_a_page_request_is_clamped_to_the_ceiling(tmp_path: Path) -> None:
    history, transcript = await seeded(tmp_path, turns=HISTORY_MESSAGES_PAGE_MAX)
    try:
        await history.index_transcript(RUN, transcript, "claude")
        page = await history.history_messages_page(RUN, limit=HISTORY_MESSAGES_PAGE_MAX * 10)
        assert len(page["messages"]) == HISTORY_MESSAGES_PAGE_MAX
        assert page["has_more_after"] is True
        assert len((await history.history_messages_page(RUN, limit=0))["messages"]) == 1
    finally:
        history.close()


async def test_messages_never_parse(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    history, transcript = await seeded(tmp_path)
    try:
        await history.index_transcript(RUN, transcript, "claude")
        forbid_parsing(monkeypatch)
        async with TestClient(TestServer(reader_app(history))) as client:
            for query in ("", "after=10", "before=40", "around=30"):
                response = await client.get(f"/api/history/{RUN}/messages?{query}")
                assert response.status == 200, query
    finally:
        history.close()


# -------------------------------------------------------- /matches, /notes, /tool-calls


async def test_matches_and_notes_load_beside_the_conversation(tmp_path: Path) -> None:
    history, transcript = await seeded(tmp_path)
    try:
        await history.index_transcript(RUN, transcript, "claude")
        async with TestClient(TestServer(reader_app(history))) as client:
            matches = await (
                await client.get(f"/api/history/{RUN}/matches?q=question+1&scope=user")
            ).json()
            notes = await (await client.get(f"/api/history/{RUN}/notes")).json()
            nothing = await (await client.get(f"/api/history/{RUN}/matches")).json()
        assert matches["matches"]
        assert {match["role"] for match in matches["matches"]} == {"user"}
        assert all(match["ordinal"] % 3 == 0 for match in matches["matches"])
        assert notes["annotations"][0]["id"] == "note-1"
        assert notes["scan_records"][0]["id"] == "scan-1"
        assert nothing == {"matches": []}
    finally:
        history.close()


async def test_tool_calls_arrive_by_ordinal_range_with_their_inputs(tmp_path: Path) -> None:
    history, transcript = await seeded(tmp_path)
    try:
        async with TestClient(TestServer(reader_app(history))) as client:
            response = await client.get(f"/api/history/{RUN}/tool-calls?from=3&to=8")
            assert response.status == 200
            body = await response.json()
        stat = transcript.stat()
        assert (body["mtime_ns"], body["size"]) == (stat.st_mtime_ns, stat.st_size)
        assert [item["ordinal"] for item in body["messages"]] == [4, 5, 7, 8]
        assert body["messages"][0] == {
            "ordinal": 4,
            "role": "assistant",
            "ts": "2026-09-01T10:00:01Z",
            "has_text": True,
            "tool_calls": [
                {"id": "toolu_read_1", "name": "Read", "input": {"file_path": "src/file_1.py"}}
            ],
        }
        # A message that is nothing but a call is how the reader learns where to insert
        # a row the index never held.
        assert body["messages"][1]["has_text"] is False
        assert body["messages"][1]["tool_calls"][0]["name"] == "Bash"
    finally:
        history.close()


# ------------------------------------------------------------------ /transcript


async def test_the_whole_transcript_route_leaves_a_current_index_alone(tmp_path: Path) -> None:
    """It used to delete and rewrite every indexed message of the run on every open."""
    history, transcript = await seeded(tmp_path)
    try:
        await history.index_transcript(RUN, transcript, "claude")

        def message_ids() -> list[int]:
            return [
                int(row[0])
                for row in history._db.execute(  # noqa: SLF001 - the index under test
                    "SELECT id FROM history_messages WHERE history_id=? ORDER BY id", (RUN,)
                )
            ]

        before = await history._run(message_ids)  # noqa: SLF001
        async with TestClient(TestServer(reader_app(history))) as client:
            response = await client.get(f"/api/history/{RUN}/transcript")
            assert response.status == 200
            body = await response.json()
        assert len(body["messages"]) == 3 * TURNS
        assert body["abandoned_messages"] == 0
        assert await history._run(message_ids) == before, "a current index was rewritten"  # noqa: SLF001
    finally:
        history.close()


async def test_the_whole_transcript_route_still_indexes_a_stale_conversation(
    tmp_path: Path,
) -> None:
    history, transcript = await seeded(tmp_path)
    try:
        await history.index_transcript(RUN, transcript, "claude")
        write_conversation(transcript, TURNS + 2)
        async with TestClient(TestServer(reader_app(history))) as client:
            assert (await client.get(f"/api/history/{RUN}/transcript")).status == 200
        state = await history.transcript_index_state(
            RUN, transcript, "claude", None, readable=True, schedule=False
        )
        assert state["current"] is True
        assert state["message_count"] == 2 * (TURNS + 2)
    finally:
        history.close()


# ------------------------------------------------------------ background reindex


async def test_a_reindex_runs_once_however_often_it_is_asked_for(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    history, transcript = await seeded(tmp_path)
    release = threading.Event()
    calls: list[str] = []
    real = history_module.read_history_conversation

    def gated(*args: Any, **kwargs: Any) -> ParsedConversation:
        calls.append(str(args[0]))
        assert release.wait(10), "the test never released the parse"
        return real(*args, **kwargs)

    monkeypatch.setattr(history_module, "read_history_conversation", gated)
    try:
        assert history.schedule_transcript_reindex(RUN, transcript, "claude", None)
        # Single flight: a reader polling while the parse runs joins it.
        for _ in range(5):
            assert history.schedule_transcript_reindex(RUN, transcript, "claude", None)
            state = await history.transcript_index_state(
                RUN, transcript, "claude", None, readable=True
            )
            assert state["indexing"] is True
        release.set()
        history.wait_for_background(timeout=10)
        assert len(calls) == 1
        assert not history.transcript_reindexing(RUN)
        stored = await history._run(  # noqa: SLF001
            lambda: history._transcript_index_row(RUN)  # noqa: SLF001
        )
        assert stored is not None
        assert stored["abandoned_messages"] == 0 and stored["tool_calls"] == 2 * TURNS

        # A conversation still being written goes stale at once; the cooldown is what
        # stops a polling reader from reparsing it on every poll.
        write_conversation(transcript, TURNS + 1)
        assert history.schedule_transcript_reindex(RUN, transcript, "claude", None) is False
        state = await history.transcript_index_state(RUN, transcript, "claude", None, readable=True)
        assert state["current"] is False and state["indexing"] is False
        assert len(calls) == 1

        monkeypatch.setattr(history_module, "REINDEX_COOLDOWN_SECONDS", 0.0)
        assert history.schedule_transcript_reindex(RUN, transcript, "claude", None)
        history.wait_for_background(timeout=10)
        assert len(calls) == 2
    finally:
        release.set()
        history.close()


async def test_an_index_without_an_abandoned_count_is_reindexed_once(tmp_path: Path) -> None:
    """How the count reaches conversations indexed before it existed."""
    history, transcript = await seeded(tmp_path)
    try:
        await history.index_transcript(RUN, transcript, "claude")

        def forget_count() -> None:
            with history._db:  # noqa: SLF001
                history._db.execute(  # noqa: SLF001
                    "UPDATE history_transcript_index SET abandoned_messages=NULL,tool_calls=NULL"
                )

        await history._run(forget_count)  # noqa: SLF001
        state = await history.transcript_index_state(RUN, transcript, "claude", None, readable=True)
        assert state["current"] is True and state["indexing"] is True
        history.wait_for_background(timeout=10)
        state = await history.transcript_index_state(RUN, transcript, "claude", None, readable=True)
        assert state["abandoned_messages"] == 0 and state["tool_calls"] == 2 * TURNS
        assert state["indexing"] is False
    finally:
        history.close()


async def test_close_drains_background_work_before_the_database(tmp_path: Path) -> None:
    history, transcript = await seeded(tmp_path)
    assert history.schedule_transcript_reindex(RUN, transcript, "claude", None)
    history.close()
    assert history.schedule_transcript_reindex(RUN, transcript, "claude", None) is False
    assert not history.transcript_reindexing(RUN)


def test_every_reader_route_is_registered_two_segments_deep() -> None:
    """A bare `/api/history/{sid}` GET would shadow single-segment static paths."""
    paths = {
        route.path
        for module in routes.ORDER
        for route in module.ROUTES
        if route.method == "GET" and route.path.startswith("/api/history/{sid}")
    }
    for name in ("detail", "messages", "matches", "notes", "tool-calls", "transcript"):
        assert f"/api/history/{{sid}}/{name}" in paths, name
    assert "/api/history/{sid}" not in paths
