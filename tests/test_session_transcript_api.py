"""The live-session transcript endpoint behind the drawer's Transcript tab.

The point of this endpoint existing at all is that it only reads: the history
transcript route reindexes the run's searchable messages and loads its
annotations on every call, which a surface that refreshes on user messages and turn
boundaries must not do. The tests below pin that, plus the rule that "nothing to
show" is an ordinary 200 with a reason rather than an error.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from swe_mux import app_keys as keys
from swe_mux import conversation_index
from swe_mux.event_bus import EventBus
from swe_mux.models import SessionRecord
from swe_mux.routes.scan_timeline import session_transcript, session_transcript_tool_calls
from swe_mux.routes.sessions import regenerate_session_title
from swe_mux.server import error_middleware


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
    app[keys.SESSIONS] = ManagerStub(SessionStub(session_record, transcript))
    app[keys.EVENTS] = EventBus()
    app.router.add_get("/api/sessions/{sid}/transcript", session_transcript)
    app.router.add_get("/api/sessions/{sid}/transcript/tool-calls", session_transcript_tool_calls)
    app.router.add_post("/api/sessions/{sid}/title/regenerate", regenerate_session_title)
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
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "call-1",
                        "name": "Read",
                        "input": {"file_path": "README.md"},
                    }
                ],
            },
        },
        {
            "type": "assistant",
            "timestamp": "2026-08-02T10:00:03Z",
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
    # Named, not carried: inputs are read on demand from `/transcript/tool-calls`.
    assert body["messages"][-1]["preceding_tools"] == [{"id": "call-1", "name": "Read"}]
    assert body["trailing_tool_calls"] == []
    assert body["truncated"] is False
    assert body["has_older"] is False
    assert body["total"] == 2
    assert body["page"] == "tail"
    assert body["reset"] is False
    assert isinstance(body["generation"], str)
    assert isinstance(body["seq"], int)
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
        "trailing_tool_calls": [],
        "hidden": 0,
        "abandoned_messages": 0,
        "truncated": False,
        "reason": "not_agent",
        "generation": None,
        "seq": None,
        "total": 0,
        "has_older": False,
        "page": None,
        "delta": False,
        "replace_from": None,
        "reset": False,
    }


async def test_an_agent_with_no_transcript_yet_reports_that(tmp_path: Path) -> None:
    missing = tmp_path / "not-written-yet.jsonl"
    async with TestClient(TestServer(build(record(), missing))) as client:
        body = await (await client.get("/api/sessions/sess-1/transcript")).json()
    assert body["reason"] == "no_transcript"
    assert body["messages"] == []


async def test_remote_agent_transcript_is_unavailable_instead_of_stale(
    tmp_path: Path,
) -> None:
    transcript = write_conversation(tmp_path / "claude.jsonl")
    session = record(runtime_boundary="remote", remote_authority="example.test")
    async with TestClient(TestServer(build(session, transcript))) as client:
        body = await (await client.get("/api/sessions/sess-1/transcript")).json()

    assert body["reason"] == "agent_bridge_unavailable"
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
        assert body["has_older"] is True
        assert body["total"] == 2
        # Ordinals are positions in the whole conversation, not in the window.
        assert body["messages"][0]["ordinal"] == 1
        # Absurd values clamp rather than allocating a conversation nobody has.
        body = await (await client.get("/api/sessions/sess-1/transcript?limit=99999")).json()
        assert body["truncated"] is False
        assert (await client.get("/api/sessions/sess-1/transcript?limit=soon")).status == 400
        assert (await client.get("/api/sessions/sess-1/transcript?since=later")).status == 400


def append_events(path: Path, *events: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        for event in events:
            handle.write(json.dumps(event) + "\n")


def user_event(text: str, second: int) -> dict[str, Any]:
    return {
        "type": "user",
        "timestamp": f"2026-08-02T10:01:{second:02d}Z",
        "origin": {"kind": "human"},
        "message": {"role": "user", "content": [{"type": "text", "text": text}]},
    }


async def test_older_messages_page_backwards_from_a_cursor(tmp_path: Path) -> None:
    """A reader opens on the tail and walks backwards one page at a time.

    ``before`` names the oldest message the client holds; the answer is the page that
    ends just before it, numbered in the same whole-conversation ordinals.
    """
    transcript = write_conversation(tmp_path / "claude.jsonl")
    append_events(transcript, user_event("again", 1), user_event("and again", 2))
    async with TestClient(TestServer(build(record(), transcript))) as client:
        tail = await (await client.get("/api/sessions/sess-1/transcript?limit=2")).json()
        assert [item["text"] for item in tail["messages"]] == ["again", "and again"]
        assert tail["has_older"] is True
        cursor = tail["messages"][0]["message_id"]
        older = await (
            await client.get(f"/api/sessions/sess-1/transcript?limit=2&before={cursor}")
        ).json()
    assert older["page"] == "older"
    assert older["reset"] is False
    assert [item["text"] for item in older["messages"]] == ["build it", "built"]
    assert [item["ordinal"] for item in older["messages"]] == [0, 1]
    assert older["has_older"] is False
    assert older["generation"] == tail["generation"]


async def test_a_cursor_the_conversation_no_longer_holds_resets_to_the_tail(
    tmp_path: Path,
) -> None:
    transcript = write_conversation(tmp_path / "claude.jsonl")
    async with TestClient(TestServer(build(record(), transcript))) as client:
        body = await (
            await client.get("/api/sessions/sess-1/transcript?limit=1&before=offset:999999")
        ).json()
    assert body["reset"] is True
    assert body["page"] == "tail"
    assert [item["text"] for item in body["messages"]] == ["built"]


async def test_a_delta_carries_only_what_changed_since_the_client_s_reading(
    tmp_path: Path,
) -> None:
    transcript = write_conversation(tmp_path / "claude.jsonl")
    async with TestClient(TestServer(build(record(), transcript))) as client:
        first = await (await client.get("/api/sessions/sess-1/transcript")).json()
        oldest = first["messages"][0]["message_id"]
        query = f"since={first['seq']}&generation={first['generation']}&oldest={oldest}"
        unchanged = await (await client.get(f"/api/sessions/sess-1/transcript?{query}")).json()
        assert unchanged["delta"] is True
        assert unchanged["messages"] == []
        assert unchanged["replace_from"] is None

        append_events(transcript, user_event("next", 1))
        grown = await (await client.get(f"/api/sessions/sess-1/transcript?{query}")).json()
    assert grown["delta"] is True
    assert grown["reset"] is False
    assert [item["text"] for item in grown["messages"]] == ["next"]
    assert grown["replace_from"] == grown["messages"][0]["message_id"]
    assert grown["messages"][0]["ordinal"] == 2
    assert grown["total"] == 3
    assert grown["seq"] > first["seq"]
    assert grown["generation"] == first["generation"]


async def test_a_delta_from_another_generation_is_answered_with_a_fresh_tail(
    tmp_path: Path,
) -> None:
    """A rewritten file (Claude's cancel/revert) cannot be described as a splice.

    Nor can a reading from before the daemon restarted, whose counters a new index
    could reach again: the generation is minted per index, so it never matches.
    """
    transcript = write_conversation(tmp_path / "claude.jsonl")
    async with TestClient(TestServer(build(record(), transcript))) as client:
        first = await (await client.get("/api/sessions/sess-1/transcript")).json()
        stale = await (
            await client.get(
                f"/api/sessions/sess-1/transcript?since={first['seq']}&generation=elsewhere.0"
            )
        ).json()
        assert stale["reset"] is True
        assert stale["delta"] is False
        assert [item["text"] for item in stale["messages"]] == ["build it", "built"]

        # Rewrite in place with different, same-length content: a replacement.
        transcript.write_text(
            transcript.read_text(encoding="utf-8").replace("build it", "bUild it"),
            encoding="utf-8",
        )
        query = f"since={first['seq']}&generation={first['generation']}"
        rewritten = await (await client.get(f"/api/sessions/sess-1/transcript?{query}")).json()
    assert rewritten["generation"] != first["generation"]
    assert rewritten["reset"] is True
    assert [item["text"] for item in rewritten["messages"]] == ["bUild it", "built"]


async def test_an_unchanged_reading_revalidates_as_304(tmp_path: Path) -> None:
    transcript = write_conversation(tmp_path / "claude.jsonl")
    async with TestClient(TestServer(build(record(), transcript))) as client:
        first = await client.get("/api/sessions/sess-1/transcript")
        etag = first.headers["ETag"]
        assert first.headers["Cache-Control"] == "no-cache"
        again = await client.get("/api/sessions/sess-1/transcript", headers={"If-None-Match": etag})
        assert again.status == 304
        assert again.headers["ETag"] == etag
        assert await again.read() == b""
        # Another shape of request is another body, so it is another tag.
        other = await client.get(
            "/api/sessions/sess-1/transcript?limit=1", headers={"If-None-Match": etag}
        )
        assert other.status == 200

        append_events(transcript, user_event("next", 1))
        grown = await client.get("/api/sessions/sess-1/transcript", headers={"If-None-Match": etag})
        assert grown.status == 200
        assert grown.headers["ETag"] != etag


async def test_a_conversation_still_being_indexed_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A first read of a very long conversation answers "indexing", not "unreadable".

    The read is not abandoned at the deadline, so this is "ask again shortly"; the
    progress lets the tab say how far it has got.
    """
    transcript = write_conversation(tmp_path / "claude.jsonl")

    async def still_building(*_args: Any, **_kwargs: Any) -> Any:
        raise conversation_index.IndexBuilding(
            {"read_bytes": 10, "total_bytes": 40, "building": True}
        )

    monkeypatch.setattr(conversation_index, "snapshot_async", still_building)
    async with TestClient(TestServer(build(record(), transcript))) as client:
        body = await (await client.get("/api/sessions/sess-1/transcript")).json()
        calls = await (
            await client.get("/api/sessions/sess-1/transcript/tool-calls?trailing=1")
        ).json()
    assert body["reason"] == "indexing"
    assert body["progress"] == {"read_bytes": 10, "total_bytes": 40, "building": True}
    assert body["messages"] == []
    assert calls == {
        "calls": [],
        "reason": "indexing",
        "progress": {"read_bytes": 10, "total_bytes": 40, "building": True},
    }


async def test_tool_call_inputs_are_read_on_demand(tmp_path: Path) -> None:
    transcript = write_conversation(tmp_path / "claude.jsonl")
    append_events(
        transcript,
        {
            "type": "assistant",
            "timestamp": "2026-08-02T10:00:04Z",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": "call-2", "name": "Bash", "input": {"command": "ls"}}
                ],
            },
        },
    )
    async with TestClient(TestServer(build(record(), transcript))) as client:
        view = await (await client.get("/api/sessions/sess-1/transcript")).json()
        assert view["trailing_tool_calls"] == [{"id": "call-2", "name": "Bash"}]
        built = view["messages"][-1]["message_id"]
        before = await (
            await client.get(f"/api/sessions/sess-1/transcript/tool-calls?message_id={built}")
        ).json()
        trailing = await (
            await client.get("/api/sessions/sess-1/transcript/tool-calls?trailing=1")
        ).json()
        gone = await (
            await client.get("/api/sessions/sess-1/transcript/tool-calls?message_id=offset:7")
        ).json()
        assert (await client.get("/api/sessions/sess-1/transcript/tool-calls")).status == 400
    assert before == {
        "calls": [{"id": "call-1", "name": "Read", "input": {"file_path": "README.md"}}],
        "reason": None,
    }
    assert trailing == {
        "calls": [{"id": "call-2", "name": "Bash", "input": {"command": "ls"}}],
        "reason": None,
    }
    assert gone == {"calls": [], "reason": "message_unavailable"}


async def test_tool_calls_of_a_shell_pane_are_an_empty_reason(tmp_path: Path) -> None:
    async with TestClient(TestServer(build(record(backend="shell"), None))) as client:
        body = await (
            await client.get("/api/sessions/sess-1/transcript/tool-calls?trailing=1")
        ).json()
    assert body == {"calls": [], "reason": "not_agent"}


async def test_generated_title_regeneration_emits_a_forced_async_request() -> None:
    app = build(record(agent_run_id="run-1", auto_named=True), None)
    queue = app[keys.EVENTS].subscribe(name="title-regenerate-test")
    async with TestClient(TestServer(app)) as client:
        response = await client.post("/api/sessions/sess-1/title/regenerate")
        assert response.status == 202
        assert await response.json() == {"ok": True}
    event = await queue.get()
    assert (event.type, event.session_id, event.source) == (
        "title_regenerate_requested",
        "sess-1",
        "user",
    )
    assert event.payload["force_title"] is True


async def test_manually_named_session_cannot_regenerate_its_title() -> None:
    app = build(record(agent_run_id="run-1", auto_named=False), None)
    async with TestClient(TestServer(app)) as client:
        response = await client.post("/api/sessions/sess-1/title/regenerate")
    assert response.status == 400
