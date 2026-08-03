"""The live-session transcript endpoint behind the drawer's Transcript tab.

The point of this endpoint existing at all is that it only reads: the history
transcript route reindexes the run's searchable messages and loads its
annotations on every call, which a surface that refreshes on every turn must not
do. The tests below pin that, plus the rule that "nothing to show" is an ordinary
200 with a reason rather than an error.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from swe_mux.models import SessionRecord
from swe_mux.server import error_middleware, session_transcript


class SessionStub:
    def __init__(self, record: SessionRecord, transcript: Path | None) -> None:
        self.record = record
        self.transcript_path = transcript


class ManagerStub:
    def __init__(self, session: SessionStub) -> None:
        self.session = session

    def resolve(self, _sid: str) -> SessionStub:
        return self.session


def record(**overrides: Any) -> SessionRecord:
    base = {
        "id": "sess-1",
        "name": "one",
        "project_id": "proj-1",
        "backend": "claude",
        "native_session_id": "native-1",
        "cwd": "C:/nowhere",
        "exe": "claude.exe",
        "args": [],
    }
    return SessionRecord(**{**base, **overrides})


def build(session_record: SessionRecord, transcript: Path | None) -> web.Application:
    app = web.Application(middlewares=[error_middleware])
    app["sessions"] = ManagerStub(SessionStub(session_record, transcript))
    app.router.add_get("/api/sessions/{sid}/transcript", session_transcript)
    return app


def write_conversation(path: Path) -> Path:
    events = [
        {
            "type": "user",
            "timestamp": "2026-08-02T10:00:00Z",
            "origin": {"kind": "human"},
            "promptSource": "typed",
            "message": {"role": "user", "content": [{"type": "text", "text": "build it"}]},
        },
        {
            "type": "user",
            "timestamp": "2026-08-02T10:00:01Z",
            "isMeta": True,
            "message": {"role": "user", "content": [{"type": "text", "text": "skill body"}]},
        },
        {
            "type": "assistant",
            "timestamp": "2026-08-02T10:00:02Z",
            "message": {"role": "assistant", "content": [{"type": "text", "text": "built"}]},
        },
    ]
    path.write_text("\n".join(json.dumps(event) for event in events) + "\n", encoding="utf-8")
    return path


async def test_the_conversation_is_returned_with_machinery_counted(tmp_path: Path) -> None:
    transcript = write_conversation(tmp_path / "claude.jsonl")
    async with TestClient(TestServer(build(record(agent_run_id="run-1"), transcript))) as client:
        response = await client.get("/api/sessions/sess-1/transcript")
        assert response.status == 200
        body = await response.json()
    assert [(item["role"], item["text"]) for item in body["messages"]] == [
        ("user", "build it"),
        ("assistant", "built"),
    ]
    assert body["hidden"] == 1
    assert body["truncated"] is False
    assert body["reason"] is None
    assert body["agent_run_id"] == "run-1"


async def test_a_shell_pane_is_an_empty_reason_not_an_error(tmp_path: Path) -> None:
    """A passive reader has ordinary states where there is nothing to read.

    Answering 409 would make the tab render an error for a session that is simply
    not an agent, which is not a fault of anything.
    """
    async with TestClient(TestServer(build(record(backend="shell"), None))) as client:
        response = await client.get("/api/sessions/sess-1/transcript")
        assert response.status == 200
        body = await response.json()
    assert body == {
        "session_id": "sess-1",
        "agent_run_id": None,
        "backend": "shell",
        "observation_stale_since": None,
        "messages": [],
        "hidden": 0,
        "truncated": False,
        "reason": "not_agent",
    }


async def test_an_agent_with_no_transcript_yet_reports_that(tmp_path: Path) -> None:
    missing = tmp_path / "not-written-yet.jsonl"
    async with TestClient(TestServer(build(record(), missing))) as client:
        body = await (await client.get("/api/sessions/sess-1/transcript")).json()
    assert body["reason"] == "no_transcript"
    assert body["messages"] == []


async def test_a_stale_observation_link_is_reported_to_the_reader(tmp_path: Path) -> None:
    """The reader is where following the wrong transcript becomes plainly visible.

    Everywhere else a stale link shows up as odd telemetry; here it would present
    another conversation as this session's, so the doubt travels with the payload.
    """
    transcript = write_conversation(tmp_path / "claude.jsonl")
    session = record(observation_stale_since=1_770_000_000.0)
    async with TestClient(TestServer(build(session, transcript))) as client:
        body = await (await client.get("/api/sessions/sess-1/transcript")).json()
    assert body["observation_stale_since"] == 1_770_000_000.0


async def test_the_limit_is_bounded_and_rejects_nonsense(tmp_path: Path) -> None:
    transcript = write_conversation(tmp_path / "claude.jsonl")
    async with TestClient(TestServer(build(record(), transcript))) as client:
        body = await (await client.get("/api/sessions/sess-1/transcript?limit=1")).json()
        assert [item["text"] for item in body["messages"]] == ["built"]
        assert body["truncated"] is True
        # Absurd values clamp rather than allocating a conversation nobody has.
        body = await (await client.get("/api/sessions/sess-1/transcript?limit=99999")).json()
        assert body["truncated"] is False
        assert (await client.get("/api/sessions/sess-1/transcript?limit=soon")).status == 400
