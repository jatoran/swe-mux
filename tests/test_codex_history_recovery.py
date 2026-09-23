from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from swe_mux import app_keys as keys
from swe_mux import codex_history
from swe_mux.event_bus import EventBus
from swe_mux.reply_snapshot import read_reply_snapshot
from swe_mux.routes import scan_timeline
from swe_mux.session import SessionManager, session_is_unwitnessed
from swe_mux.transcript_view import (
    conversation_view,
    conversation_view_cached,
    final_reply_text,
    parse_transcript,
)
from tests.test_conversation_rollover import agent_record, rollover_manager
from tests.test_session_transcript_api import ManagerStub, SessionStub, record

PARENT = "11111111-1111-4111-8111-111111111111"
CHILD = "22222222-2222-4222-8222-222222222222"


def event(kind: str, **payload: Any) -> dict[str, Any]:
    return {
        "type": "event_msg",
        "timestamp": datetime.now(UTC).isoformat(),
        "payload": {"type": kind, **payload},
    }


def message(role: str, text: str, phase: str | None = None) -> dict[str, Any]:
    return {
        "type": "response_item",
        "payload": {
            "type": "message",
            "role": role,
            "phase": phase,
            "content": [{"type": "output_text", "text": text}],
        },
    }


def turn(name: str) -> list[dict[str, Any]]:
    return [
        event("task_started", turn_id=name),
        message("user", f"question {name}"),
        message("assistant", f"progress {name}", "commentary"),
        message("assistant", f"answer {name}", "final_answer"),
        event("task_complete", turn_id=name),
    ]


def write(path: Path, events: list[dict[str, Any]]) -> Path:
    path.write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
    return path


def meta(native: str, **kwargs: Any) -> dict[str, Any]:
    return {"type": "session_meta", "payload": {"id": native, "source": "cli", **kwargs}}


def test_fork_inherits_only_pinned_parent_prefix_and_keeps_message_identity(tmp_path: Path) -> None:
    parent = write(tmp_path / f"rollout-{PARENT}.jsonl", [meta(PARENT), *turn("A")])
    boundary = parent.stat().st_size
    child = write(
        tmp_path / f"rollout-{CHILD}.jsonl",
        [
            meta(
                CHILD,
                history_mode="paginated",
                history_base={"thread_id": PARENT, "end_byte_offset": boundary},
            )
        ],
    )
    before = codex_history.revision(child)
    with parent.open("a", encoding="utf-8") as handle:
        handle.write("".join(json.dumps(e) + "\n" for e in turn("B")))
    assert codex_history.revision(child) == before
    assert final_reply_text(child, "codex") == "answer A"
    view = conversation_view(child, "codex")
    assert [m["text"] for m in view["messages"]] == ["question A", "progress A", "answer A"]
    assert len({m["message_id"] for m in view["messages"]}) == 3
    assert all(m["message_id"].startswith("inherited:") for m in view["messages"])


def test_nested_reference_and_bounded_pages_do_not_drop_inherited_history(tmp_path: Path) -> None:
    parent = write(tmp_path / f"rollout-{PARENT}.jsonl", [meta(PARENT), *turn("A")])
    child = write(
        tmp_path / f"rollout-{CHILD}.jsonl",
        [
            meta(
                CHILD, history_base={"thread_id": PARENT, "end_byte_offset": parent.stat().st_size}
            ),
            *turn("B"),
        ],
    )
    third = write(
        tmp_path / "third.jsonl",
        [meta("third", history_base={"thread_id": CHILD, "end_byte_offset": child.stat().st_size})],
    )
    assert final_reply_text(third, "codex") == "answer B"
    events, more, boundary = codex_history.page(third, direction="head", max_bytes=350)
    visited = list(events)
    while more:
        events, more, new_boundary = codex_history.page(
            third, direction="head", anchor=boundary, max_bytes=350
        )
        assert new_boundary > boundary
        visited.extend(events)
        boundary = new_boundary
    assert [
        e["payload"].get("turn_id") for e in visited if e["payload"].get("type") == "task_complete"
    ] == ["A", "B"]


def test_rewind_removes_turns_from_copy_and_search_but_marks_reader_history(tmp_path: Path) -> None:
    path = write(
        tmp_path / "conversation.jsonl",
        [meta(PARENT), *turn("A"), *turn("B"), event("thread_rolled_back", num_turns=1)],
    )
    assert final_reply_text(path, "codex") == "answer A"
    assert not any("answer B" in str(m) for m in parse_transcript(path, "codex"))
    assert any(
        m["text"] == "answer B" and m["abandoned"]
        for m in conversation_view(path, "codex")["messages"]
    )
    with path.open("a", encoding="utf-8") as handle:
        handle.write(
            "".join(
                json.dumps(e) + "\n" for e in [*turn("C"), event("thread_rolled_back", num_turns=1)]
            )
        )
    assert final_reply_text(path, "codex") == "answer A"


def test_progress_and_uncompleted_final_never_replace_completed_answer(tmp_path: Path) -> None:
    path = write(tmp_path / "conversation.jsonl", [*turn("A"), *turn("B")[:-1]])
    assert read_reply_snapshot(path, "codex", PARENT)["text"] == "answer A"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event("task_complete", turn_id="B")) + "\n")
    reply = read_reply_snapshot(path, "codex", PARENT)
    assert reply["text"] == "answer B"
    assert reply["turn_id"] == "B"
    assert reply["phase"] == "final_answer"


def test_legacy_rollback_counts_each_user_once(tmp_path: Path) -> None:
    path = write(
        tmp_path / "legacy.jsonl",
        [
            message("user", "A"),
            event("user_message", message="A"),
            message("assistant", "answer A"),
            message("user", "B"),
            event("user_message", message="B"),
            message("assistant", "answer B"),
            event("thread_rolled_back", num_turns=1),
        ],
    )
    assert final_reply_text(path, "codex") == "answer A"


def test_same_size_rewrite_with_frozen_mtime_invalidates_view(tmp_path: Path) -> None:
    path = write(tmp_path / "conversation.jsonl", turn("A"))
    stamp = path.stat()
    first = conversation_view_cached(path, "codex")
    write(path, turn("B"))
    os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    assert conversation_view_cached(path, "codex") != first
    assert final_reply_text(path, "codex") == "answer B"


def test_missing_or_cyclic_inheritance_is_explicit(tmp_path: Path) -> None:
    path = write(
        tmp_path / f"rollout-{PARENT}.jsonl",
        [meta(PARENT, history_base={"thread_id": PARENT, "end_byte_offset": 1})],
    )
    with pytest.raises(OSError, match="Cyclic"):
        conversation_view(path, "codex")
    write(path, [meta(PARENT, history_base={"thread_id": CHILD, "end_byte_offset": 1})])
    with pytest.raises(OSError, match="unavailable"):
        conversation_view(path, "codex")


async def test_active_candidate_survives_frozen_mtime_but_not_sibling_ambiguity(
    tmp_path: Path,
) -> None:
    candidate = write(tmp_path / "candidate.jsonl", turn("B"))
    now = time.time()
    os.utime(candidate, (now - 100, now - 100))
    session = SimpleNamespace(
        record=SimpleNamespace(
            backend="codex",
            agent_run_started_at=now - 200,
            created_at=now - 200,
            run_cwd=str(tmp_path),
            cwd=str(tmp_path),
        ),
        adapter=SimpleNamespace(name="codex", transcript_last_write_ts=codex_history.last_write),
        ignored_detection_runs=set(),
    )
    manager = SessionManager.__new__(SessionManager)
    manager.sessions = {}
    manager._transcript_last_write_ts = lambda *_: now - 100  # type: ignore[method-assign]
    manager._pending_agent_launch_sibling = lambda *_: False  # type: ignore[method-assign]
    manager._session_could_have_written = lambda *_: True  # type: ignore[method-assign]
    manager._unresolved_transcript_sibling = lambda *_: False  # type: ignore[method-assign]

    async def candidates(*_: Any) -> list[tuple[float, Path, str]]:
        return [(now - 100, candidate, CHILD)]

    manager._recent_transcripts = candidates  # type: ignore[method-assign]
    assert await manager._transcript_switch_candidate(session, tmp_path / "old") == candidate
    manager._unresolved_transcript_sibling = lambda *_: True  # type: ignore[method-assign]
    assert await manager._transcript_switch_candidate(session, tmp_path / "old") is None


def test_empty_provisional_file_does_not_disable_submit_guarded_pty_recovery() -> None:
    session = SimpleNamespace(
        record=SimpleNamespace(backend="codex", parser_events_seen=0),
        transcript_path=Path("setup.jsonl"),
        transcript_provisional=True,
        last_hook_ts=0,
    )
    assert session_is_unwitnessed(session)
    session.record.parser_events_seen = 4
    assert not session_is_unwitnessed(session)


async def test_reply_endpoint_rejects_rollover_during_parse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = write(tmp_path / "conversation.jsonl", turn("A"))
    session = SessionStub(
        record(backend="codex", native_session_id=PARENT, agent_run_id="run-a"), path
    )
    original = scan_timeline.read_reply_snapshot

    def racing(*args: Any) -> dict[str, Any]:
        result = original(*args)
        session.record.native_session_id = CHILD
        session.record.agent_run_id = "run-b"
        return result

    monkeypatch.setattr(scan_timeline, "read_reply_snapshot", racing)
    app = web.Application()
    app[keys.SESSIONS] = ManagerStub(session)
    app.router.add_get("/reply", scan_timeline.session_last_reply)
    # Production resolves by route sid; use the same shape here.
    app.router.add_get("/api/sessions/{sid}/last-reply", scan_timeline.session_last_reply)
    async with TestClient(TestServer(app)) as client:
        response = await client.get("/api/sessions/sess-1/last-reply")
        assert response.status == 409
        assert (await response.json())["code"] == "conversation_changed"


async def test_reply_endpoint_reports_selected_revision_and_receipt_without_content(
    tmp_path: Path,
) -> None:
    path = write(tmp_path / "conversation.jsonl", turn("A"))
    session = SessionStub(
        record(backend="codex", native_session_id=PARENT, agent_run_id="run-a"), path
    )
    app = web.Application()
    app[keys.SESSIONS] = ManagerStub(session)
    app[keys.EVENTS] = EventBus()
    app.router.add_get("/api/sessions/{sid}/last-reply", scan_timeline.session_last_reply)
    app.router.add_post("/api/sessions/{sid}/reply-copy", scan_timeline.session_reply_copy)
    async with TestClient(TestServer(app)) as client:
        response = await client.get("/api/sessions/sess-1/last-reply")
        assert response.status == 200
        body = await response.json()
        assert body["native_session_id"] == PARENT
        assert body["text"] == "answer A"
        assert len(body["revision"]) == 64
        receipt = await client.post(
            "/api/sessions/sess-1/reply-copy", json={**body, "outcome": "copied"}
        )
        assert receipt.status == 200


@pytest.mark.parametrize("provisional", [False, True])
async def test_process_owned_rewind_rebinds_observer_in_place(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    provisional: bool,
) -> None:
    from swe_mux import observation

    original = write(tmp_path / f"rollout-{PARENT}.jsonl", [meta(PARENT)])
    replacement = write(tmp_path / f"rollout-{CHILD}.jsonl", [meta(CHILD), *turn("new")])
    rec = agent_record(backend="codex", cwd=str(tmp_path), native_id=PARENT)
    rec.pid, rec.root_started_at = 123, 10.0
    manager, session = rollover_manager(rec)
    session.adapter.assigns_conversation_id = False
    session.adapter.transcript_native_id = lambda path: codex_history.metadata(path)["id"]
    session.adapter.process_transcript_path = lambda *_: replacement
    stop = asyncio.Event()
    seen: list[Path] = []

    async def observe(current: Any, path: Path, _events: Any, _stop: Any) -> None:
        seen.append(path)
        if path == replacement:
            stop.set()
        else:
            await stop.wait()

    async def tick(*_: Any) -> None:
        await asyncio.sleep(0)

    async def locate(*_: Any) -> tuple[Path, bool]:
        return original, provisional

    monkeypatch.setattr(observation, "observe_transcript", observe)
    monkeypatch.setattr(manager, "_await_switch_tick", tick)
    monkeypatch.setattr(manager, "_await_owned_transcript", locate)
    monkeypatch.setattr(manager, "_transcript_last_write_ts", lambda *_: 1.0)
    await asyncio.wait_for(manager._observe(session, None, stop), timeout=5)
    assert rec.native_session_id == CHILD
    assert session.transcript_path == replacement
    assert not session.transcript_provisional
    assert replacement in seen
    # A guessed initial file never owned a run; a proven conversation did.
    assert rec.agent_run_seq == (0 if provisional else 1)


async def test_process_proof_cannot_take_a_live_siblings_transcript(tmp_path: Path) -> None:
    path = write(tmp_path / f"rollout-{CHILD}.jsonl", [meta(CHILD)])
    manager, session = rollover_manager(agent_record(backend="codex", cwd=str(tmp_path)))
    session.record.pid, session.record.root_started_at = 123, 10.0
    session.adapter.process_transcript_path = lambda *_: path
    session.adapter.transcript_native_id = lambda _: CHILD
    sibling = SimpleNamespace(
        record=agent_record(backend="codex", native_id=CHILD), transcript_path=path
    )
    manager.sessions["sibling"] = sibling
    assert await manager._process_owned_transcript(session) is None
    assert (
        session.observation_state["process_transcript_probe"]
        == "identity_unavailable_or_owned_elsewhere"
    )
