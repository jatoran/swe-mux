"""A conversation that moved is found again, and the row is repaired where it broke.

The CLI owns these files and moves them: Claude re-homes a conversation into the project
slug for a new working directory when a session enters or leaves a native worktree, tells
mux through its hook, and mux follows - correctly, at that moment. What the history row
keeps afterwards is that moment's address, and if the file moves once more on the way out
the row is wrong from then on with nothing left to notice.

Measured on the development host on 2026-09-02 before this existed: 301 of 1420 mux-owned
agent rows named a file that was not there, 131 of them naming a `--claude-worktrees-`
slug, and all 132 recoverable ones still on disk under their own conversation id. Their
transcripts refused to open, Resume refused them while blaming the CLI's pruning, and 97
of them carried a message index frozen part-way with no watermark that could ever thaw.

These tests pin the resolution, the write-back, and the three surfaces that were reading
the dead string - plus the two refusals that must survive it: a conversation genuinely
pruned is still gone, and a located file that cannot be read is not a repair.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from swe_mux import app_keys as keys
from swe_mux.adapters import ClaudeAdapter
from swe_mux.event_bus import EventBus
from swe_mux.history import HistoryIndex
from swe_mux.models import SessionRecord
from swe_mux.routes.history import history_transcript
from swe_mux.routes.scan_timeline import session_transcript
from swe_mux.server import error_middleware
from swe_mux.session import SessionManager
from swe_mux.session_resume import ResumeRefused, resume_run
from swe_mux.transcript_repair import locate_conversation, resolve_row_transcript

CONVERSATION = "3ff0ea70-7377-4564-ad85-266f7579bcac"
RUN = CONVERSATION
PROJECT = "proj-1"


def write_conversation(path: Path) -> Path:
    """Two turns, enough for the parser to call it a conversation."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            json.dumps(record)
            for record in (
                {
                    "type": "user",
                    "timestamp": "2026-09-01T10:00:00Z",
                    "origin": {"kind": "human"},
                    "promptSource": "typed",
                    "message": {"role": "user", "content": [{"type": "text", "text": "land it"}]},
                },
                {
                    "type": "assistant",
                    "timestamp": "2026-09-01T10:00:04Z",
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": "landed"}],
                    },
                },
            )
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def relocated(tmp_path: Path) -> tuple[ClaudeAdapter, Path, Path]:
    """A conversation recorded under a worktree slug and living under the repo's own.

    The exact shape every recoverable row on the development host had.
    """
    home = tmp_path / ".claude"
    projects = home / "projects"
    recorded = projects / "D--PROJECTS-stellar-matter--claude-worktrees-sm-m3-logistics" / (
        f"{CONVERSATION}.jsonl"
    )
    actual = write_conversation(projects / "D--PROJECTS-stellar-matter" / f"{CONVERSATION}.jsonl")
    adapter = ClaudeAdapter(data_home_resolver=lambda: home)
    return adapter, recorded, actual


def row_for(recorded: Path, *, cwd: Path) -> dict[str, Any]:
    return {
        "id": RUN,
        "native_id": CONVERSATION,
        "backend": "claude",
        "agent_visible": 1,
        "cwd": str(cwd),
        "transcript_path": str(recorded),
        "project_id": PROJECT,
    }


async def seeded_history(tmp_path: Path, recorded: Path, cwd: Path) -> HistoryIndex:
    history = HistoryIndex(tmp_path / "mux.db")
    record = SessionRecord(
        RUN, "sm-m3-logistics", PROJECT, "claude", CONVERSATION, str(cwd), "claude.exe", []
    )
    await history.session_started(record, str(recorded))
    return history


# --------------------------------------------------------------------- the resolver


async def test_a_readable_row_is_answered_without_searching_for_it(tmp_path: Path) -> None:
    """The search is the expensive answer, so a row that is fine must never pay for it."""
    adapter, _recorded, actual = relocated(tmp_path)
    searched: list[str] = []
    adapter.locate_transcript = lambda native_id: searched.append(native_id)  # type: ignore[assignment,func-returns-value,method-assign]
    row = row_for(actual, cwd=tmp_path)

    located = await resolve_row_transcript(row, adapters={"claude": adapter})

    assert located.readable and located.path == actual
    assert not located.repaired
    assert searched == []


async def test_a_moved_conversation_is_found_and_written_back(tmp_path: Path) -> None:
    adapter, recorded, actual = relocated(tmp_path)
    history = await seeded_history(tmp_path, recorded, tmp_path)
    events = EventBus()
    subscriber = events.subscribe()
    row = row_for(recorded, cwd=tmp_path)

    located = await resolve_row_transcript(
        row, adapters={"claude": adapter}, history=history, events=events
    )

    assert located.readable and located.repaired
    assert located.path == actual
    assert located.previous == str(recorded)
    # The row the caller holds, and the row everything else will read, agree.
    assert row["transcript_path"] == str(actual)
    stored = await history.history_entry(RUN)
    assert stored is not None and stored["transcript_path"] == str(actual)
    emitted = await subscriber.get()
    assert emitted.type == "history_transcript_repaired"
    assert emitted.payload["previous"] == str(recorded)


async def test_a_repair_clears_the_watermark_that_described_the_dead_file(
    tmp_path: Path,
) -> None:
    """A stale cursor is worse than none: it lets the next pass conclude nothing changed."""
    adapter, recorded, actual = relocated(tmp_path)
    history = await seeded_history(tmp_path, recorded, tmp_path)
    await history.replace_history_messages(
        RUN,
        [
            {
                "role": "user",
                "ts": "2026-09-01T10:00:00Z",
                "content": [{"type": "text", "text": "land it"}],
            }
        ],
        mtime_ns=1_234,
        size=99,
    )
    assert (await history.history_entry(RUN) or {})["time_summary_size"] == 99

    await resolve_row_transcript(
        row_for(recorded, cwd=tmp_path), adapters={"claude": adapter}, history=history
    )

    stored = await history.history_entry(RUN) or {}
    assert stored["transcript_path"] == str(actual)
    assert stored["time_summary_mtime_ns"] is None
    assert stored["transcript_size"] is None
    # The cursor that described the dead file is gone, so the next open reindexes...
    assert not history._db.execute(
        "SELECT 1 FROM history_transcript_index WHERE history_id=?", (RUN,)
    ).fetchall()
    # ...while the messages stay: same conversation, new address, still worth finding.
    assert history._db.execute(
        "SELECT COUNT(*) FROM history_messages WHERE history_id=?", (RUN,)
    ).fetchone()[0] == 1


async def test_a_conversation_that_is_really_gone_is_still_gone(tmp_path: Path) -> None:
    adapter, recorded, actual = relocated(tmp_path)
    actual.unlink()
    row = row_for(recorded, cwd=tmp_path)

    located = await resolve_row_transcript(row, adapters={"claude": adapter})

    assert not located.readable and located.path is None
    assert row["transcript_path"] == str(recorded)


async def test_a_located_path_that_cannot_be_read_is_not_a_repair(tmp_path: Path) -> None:
    """Codex's `locate_transcript` computes rather than searches, so it can answer with
    a path that is not there. Recording that would swap one dead string for another."""
    adapter, recorded, actual = relocated(tmp_path)
    invented = actual.with_name("invented.jsonl")
    adapter.locate_transcript = lambda native_id: invented  # type: ignore[assignment,method-assign]
    row = row_for(recorded, cwd=tmp_path)

    located = await resolve_row_transcript(row, adapters={"claude": adapter})

    assert not located.readable
    assert row["transcript_path"] == str(recorded)


async def test_locate_ignores_a_harness_that_keeps_no_file(tmp_path: Path) -> None:
    del tmp_path
    assert await locate_conversation(None, CONVERSATION, "shell") is None
    assert await locate_conversation(SimpleNamespace(), CONVERSATION, "opencode") is None


# ------------------------------------------------------------------- the surfaces


def transcript_app(history: HistoryIndex, adapter: ClaudeAdapter) -> web.Application:
    app = web.Application(middlewares=[error_middleware])
    app[keys.HISTORY] = history
    app[keys.EVENTS] = EventBus()
    app[keys.SESSIONS] = cast(
        Any,
        SimpleNamespace(adapters={"claude": adapter}, conversation_holders=dict),
    )
    app[keys.AUTOMATION_STORE] = cast(
        Any,
        SimpleNamespace(
            annotations=_none_list,
            scan_records=_none_list,
        ),
    )
    app.router.add_get("/api/history/{sid}/transcript", history_transcript)
    return app


async def _none_list(**_kwargs: Any) -> list[Any]:
    return []


async def test_the_transcript_route_opens_a_conversation_that_moved(tmp_path: Path) -> None:
    """The reported bug, end to end: 'that session's transcript doesn't exist' for a
    conversation sitting one project slug away."""
    adapter, recorded, actual = relocated(tmp_path)
    history = await seeded_history(tmp_path, recorded, tmp_path)
    async with TestClient(TestServer(transcript_app(history, adapter))) as client:
        response = await client.get(f"/api/history/{RUN}/transcript")
        assert response.status == 200
        body = await response.json()
    assert [message["role"] for message in body["messages"]] == ["user", "assistant"]
    assert body["entry"]["transcript_path"] == str(actual)


async def test_the_transcript_route_still_refuses_one_that_is_gone(tmp_path: Path) -> None:
    adapter, recorded, actual = relocated(tmp_path)
    actual.unlink()
    history = await seeded_history(tmp_path, recorded, tmp_path)
    async with TestClient(TestServer(transcript_app(history, adapter))) as client:
        response = await client.get(f"/api/history/{RUN}/transcript")
        assert response.status == 409
        assert (await response.json())["code"] == "transcript_unavailable"


async def test_resume_no_longer_refuses_a_conversation_that_only_moved(
    tmp_path: Path,
) -> None:
    """Resume shares the refusal with the reader, so it shared the wrong diagnosis too.

    Stopped at the *next* guard rather than run: this test is about which refusal the
    row earns, and spawning a real pane is `test_resume_run_adoption`'s job.
    """
    adapter, recorded, _actual = relocated(tmp_path)
    history = await seeded_history(tmp_path, recorded, tmp_path)
    sessions = cast(
        Any,
        SimpleNamespace(adapters={"claude": adapter}, history=history, events=None, sessions={}),
    )
    projects = cast(Any, SimpleNamespace(projects={}))

    with pytest.raises(ResumeRefused) as refused:
        await resume_run(
            row_for(recorded, cwd=tmp_path),
            sessions=sessions,
            projects=projects,
            target_project_id=PROJECT,
        )

    assert refused.value.code == "target_project_missing"
    assert (await history.history_entry(RUN) or {})["transcript_path"] != str(recorded)


async def test_resume_still_refuses_a_conversation_the_cli_pruned(tmp_path: Path) -> None:
    adapter, recorded, actual = relocated(tmp_path)
    actual.unlink()
    sessions = cast(
        Any, SimpleNamespace(adapters={"claude": adapter}, history=None, events=None, sessions={})
    )
    projects = cast(Any, SimpleNamespace(projects={}))

    with pytest.raises(ResumeRefused) as refused:
        await resume_run(
            row_for(recorded, cwd=tmp_path),
            sessions=sessions,
            projects=projects,
            target_project_id=PROJECT,
        )

    assert refused.value.code == "transcript_unavailable"


def session_app(session: Any) -> web.Application:
    app = web.Application(middlewares=[error_middleware])
    app[keys.SESSIONS] = cast(Any, SimpleNamespace(resolve=lambda _sid: session))
    app[keys.EVENTS] = EventBus()
    app.router.add_get("/api/sessions/{sid}/transcript", session_transcript)
    return app


def ended_session(adapter: ClaudeAdapter, recorded: Path, cwd: Path, *, state: str) -> Any:
    record = SessionRecord(
        RUN, "sm-m3-logistics", PROJECT, "claude", CONVERSATION, str(cwd), "claude.exe", [],
        state=state,
    )
    record.agent_run_id = RUN
    return SimpleNamespace(record=record, adapter=adapter, transcript_path=recorded)


async def test_a_dead_panes_reader_finds_the_conversation_that_moved(tmp_path: Path) -> None:
    """The observer that would have followed the file died with the process."""
    adapter, recorded, _actual = relocated(tmp_path)
    session = ended_session(adapter, recorded, tmp_path, state="exited")
    async with TestClient(TestServer(session_app(session))) as client:
        body = await (await client.get(f"/api/sessions/{RUN}/transcript")).json()
    assert body["reason"] is None
    assert [message["role"] for message in body["messages"]] == ["user", "assistant"]


async def test_a_live_panes_reader_does_not_search_on_every_poll(tmp_path: Path) -> None:
    """An agent that has not written its first record yet is the ordinary early state of
    every agent session, and its observer is already resolving the binding."""
    adapter, recorded, _actual = relocated(tmp_path)
    searched: list[str] = []
    adapter.locate_transcript = lambda native_id: searched.append(native_id)  # type: ignore[assignment,func-returns-value,method-assign]
    session = ended_session(adapter, recorded, tmp_path, state="running")
    async with TestClient(TestServer(session_app(session))) as client:
        body = await (await client.get(f"/api/sessions/{RUN}/transcript")).json()
    assert body["reason"] == "no_transcript"
    assert searched == []


# ------------------------------------------------------------- settling at the end


def manager_with(history: HistoryIndex | None, adapter: ClaudeAdapter) -> Any:
    manager = cast(Any, SessionManager.__new__(SessionManager))
    manager.history = history
    manager.adapters = {"claude": adapter}
    manager.events = EventBus()
    return manager


async def test_session_end_settles_the_address_the_conversation_finished_at(
    tmp_path: Path,
) -> None:
    """The one moment it stops moving. After this there is no follower left to notice."""
    adapter, recorded, actual = relocated(tmp_path)
    history = await seeded_history(tmp_path, recorded, tmp_path)
    session = ended_session(adapter, recorded, tmp_path, state="exited")

    settled = await manager_with(history, adapter)._settle_run_transcript(session)

    assert settled == actual
    # The pane is dead but still readable, and its reader tab reads this field.
    assert session.transcript_path == actual
    assert (await history.history_entry(RUN) or {})["transcript_path"] == str(actual)


async def test_a_shell_end_settles_nothing_and_reads_no_row(tmp_path: Path) -> None:
    adapter, recorded, _actual = relocated(tmp_path)
    session = ended_session(adapter, recorded, tmp_path, state="exited")
    session.record.backend = "shell"

    # `history=None` would raise if the shell path touched the store at all.
    assert await manager_with(None, adapter)._settle_run_transcript(session) == recorded


# ----------------------------------------------------------------- the scan's repair


async def test_the_history_scan_repairs_the_row_it_walked_past(tmp_path: Path) -> None:
    """The scanner *finds* these files. It used to see a mux-owned row and drop the
    answer on the floor, which is why 132 rows stayed broken with the file right there."""
    _adapter, recorded, actual = relocated(tmp_path)
    history = await seeded_history(tmp_path, recorded, tmp_path)

    await history.upsert_external(
        row_id="scan-would-have-used-this",
        native_id=CONVERSATION,
        backend="claude",
        name="sm-m3-logistics",
        cwd=str(tmp_path),
        spawned_at=1.0,
        transcript_path=str(actual),
    )

    stored = await history.history_entry(RUN) or {}
    assert stored["transcript_path"] == str(actual)
    assert stored["external"] == 0
    # Still exactly one row for this conversation: repairing must never mint a second.
    assert await history.history_entry("scan-would-have-used-this") is None


async def test_the_history_scan_leaves_a_healthy_row_alone(tmp_path: Path) -> None:
    _adapter, _recorded, actual = relocated(tmp_path)
    history = await seeded_history(tmp_path, actual, tmp_path)
    elsewhere = write_conversation(actual.with_name(f"{CONVERSATION}.copy.jsonl"))

    await history.upsert_external(
        row_id="scan-row",
        native_id=CONVERSATION,
        backend="claude",
        name="sm-m3-logistics",
        cwd=str(tmp_path),
        spawned_at=1.0,
        transcript_path=str(elsewhere),
    )

    assert (await history.history_entry(RUN) or {})["transcript_path"] == str(actual)
