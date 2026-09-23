"""A root Codex replacement must survive kill/resume without admitting children."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import psutil
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from swe_mux import app_keys as keys
from swe_mux import codex_process_identity as identity
from swe_mux import hook_client
from swe_mux.event_bus import EventBus
from swe_mux.history import HistoryIndex
from swe_mux.observation import conversation_rollover_decision, resolve_conversation_rollover
from swe_mux.routes.agent_ingress import hook_ingress
from swe_mux.session import SessionManager
from tests.test_conversation_rollover import CLEARED, ORIGINAL, agent_record, rollover_manager


class Process:
    def __init__(self, pid: int, name: str, started: float, parent: Process | None = None):
        self.pid = pid
        self.label = name
        self.started = started
        self.ancestor = parent

    def name(self) -> str:
        return self.label

    def create_time(self) -> float:
        return self.started

    def parent(self) -> Process | None:
        return self.ancestor


@pytest.fixture
def processes(monkeypatch: pytest.MonkeyPatch) -> dict[int, Process]:
    root = Process(100, "cmd.exe", 10.0)
    codex = Process(101, "codex.exe", 11.0, root)
    shell = Process(102, "pwsh.exe", 12.0, codex)
    helper = Process(103, "swemux-exec.exe", 13.0, shell)
    tree = {p.pid: p for p in (root, codex, shell, helper)}

    def lookup(pid: int) -> Process:
        if pid not in tree:
            raise psutil.NoSuchProcess(pid)
        return tree[pid]

    monkeypatch.setattr(identity, "psutil", SimpleNamespace(Process=lookup, Error=psutil.Error))
    monkeypatch.setattr(identity, "os", SimpleNamespace(getpid=lambda: 103))
    return tree


@pytest.fixture
def start_payload(tmp_path: Path) -> dict[str, Any]:
    path = tmp_path / "replacement.jsonl"
    path.write_text(
        json.dumps(
            {
                "type": "session_meta",
                "payload": {"id": CLEARED, "source": "cli"},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return {
        "session_id": CLEARED,
        "source": "startup",
        "cwd": str(tmp_path),
        "transcript_path": str(path),
        identity.PROCESS_FIELD: {"pid": 101, "started_at": 11.0},
    }


def test_capture_uses_nearest_codex_and_survives_helper_exit(
    processes: dict[int, Process],
    start_payload: dict[str, Any],
) -> None:
    captured = identity.capture_codex_process()
    assert captured == {"pid": 101, "started_at": 11.0}
    processes.pop(103)
    start_payload[identity.PROCESS_FIELD] = captured
    assert identity.verify_codex_root_process(start_payload, 100, 10.0).verified


def test_open_file_binding_excludes_native_subagents_and_nested_clis(
    tmp_path: Path,
    processes: dict[int, Process],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root_path = tmp_path / f"rollout-{CLEARED}.jsonl"
    child_path = tmp_path / f"rollout-{ORIGINAL}.jsonl"
    root_path.write_text(
        json.dumps({"type": "session_meta", "payload": {"id": CLEARED, "source": "cli"}}) + "\n",
        encoding="utf-8",
    )
    child_path.write_text(
        json.dumps(
            {
                "type": "session_meta",
                "payload": {
                    "id": ORIGINAL,
                    "source": {"subagent": "guardian"},
                    "parent_thread_id": CLEARED,
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        Process,
        "children",
        lambda self: [processes[101]] if self.pid == 100 else [processes[102]],
        raising=False,
    )
    monkeypatch.setattr(
        Process,
        "open_files",
        lambda self: [SimpleNamespace(path=str(p)) for p in (root_path, child_path)],
        raising=False,
    )
    assert identity.owned_rollout(100, 10.0, tmp_path) == root_path
    assert identity.owned_rollout(100, 9.0, tmp_path) is None
    # Another root conversation held open is ambiguous, even in this process.
    child_path.write_text(
        json.dumps({"type": "session_meta", "payload": {"id": ORIGINAL, "source": "cli"}}) + "\n",
        encoding="utf-8",
    )
    assert identity.owned_rollout(100, 10.0, tmp_path) is None


def test_direct_codex_root_is_accepted(
    processes: dict[int, Process],
    start_payload: dict[str, Any],
) -> None:
    assert identity.verify_codex_root_process(start_payload, 101, 11.0).verified


def test_nested_cli_cannot_steal_its_parents_conversation(
    processes: dict[int, Process],
    start_payload: dict[str, Any],
) -> None:
    nested = Process(104, "codex.exe", 12.5, processes[102])
    processes[104] = nested
    processes[103].ancestor = nested
    captured = identity.capture_codex_process()
    assert captured == {"pid": 104, "started_at": 12.5}
    start_payload[identity.PROCESS_FIELD] = captured
    assert (
        identity.verify_codex_root_process(start_payload, 100, 10.0).reason
        == "nested_codex_process"
    )


@pytest.mark.parametrize(
    "pid,started,reason",
    [
        (100, 9.0, "pty_process_identity_changed"),
        (200, 10.0, "pty_ancestry_unverified"),
        (100, None, "process_identity_invalid"),
    ],
)
def test_pty_ownership_must_be_proven(
    processes: dict[int, Process],
    start_payload: dict[str, Any],
    pid: int,
    started: float | None,
    reason: str,
) -> None:
    assert identity.verify_codex_root_process(start_payload, pid, started).reason == reason


@pytest.mark.parametrize(
    "reported",
    [
        None,
        {},
        {"pid": True, "started_at": 11.0},
        {"pid": 101, "started_at": float("nan")},
        {"pid": 101, "started_at": -1},
        {"pid": 101, "started_at": 10.0},
        {"pid": 999, "started_at": 11.0},
    ],
)
def test_missing_dead_reused_or_invalid_process_never_authorizes_rollover(
    processes: dict[int, Process],
    start_payload: dict[str, Any],
    reported: Any,
) -> None:
    start_payload[identity.PROCESS_FIELD] = reported
    assert not identity.verify_codex_root_process(start_payload, 100, 10.0).verified


def test_recycled_parent_link_is_refused(
    processes: dict[int, Process],
    start_payload: dict[str, Any],
) -> None:
    processes[100].started = 20.0
    assert (
        identity.verify_codex_root_process(start_payload, 100, 20.0).reason
        == "pty_ancestry_unverified"
    )


@pytest.mark.parametrize(
    "meta",
    [
        {"id": CLEARED, "source": {"subagent": {"thread_spawn": {"parent_thread_id": ORIGINAL}}}},
        {"id": ORIGINAL, "source": "cli"},
        {"id": CLEARED},
    ],
)
def test_shared_process_does_not_authorize_a_subagent_or_mismatched_transcript(
    processes: dict[int, Process],
    start_payload: dict[str, Any],
    meta: dict[str, Any],
) -> None:
    Path(start_payload["transcript_path"]).write_text(
        json.dumps({"type": "session_meta", "payload": meta}) + "\n",
        encoding="utf-8",
    )
    assert not identity.verify_codex_root_process(start_payload, 100, 10.0).verified


@pytest.mark.parametrize(
    "contents",
    ["", "{", "[]", "x" * (identity.MAX_META_BYTES + 1)],
    ids=["empty", "torn", "non-object", "oversized"],
)
def test_unreadable_metadata_fails_closed(
    processes: dict[int, Process],
    start_payload: dict[str, Any],
    contents: str,
) -> None:
    Path(start_payload["transcript_path"]).write_text(contents, encoding="utf-8")
    assert not identity.verify_codex_root_process(start_payload, 100, 10.0).verified


def test_hook_client_overwrites_payload_claim_and_spools_process_identity(
    processes: dict[int, Process],
    start_payload: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    start_payload[identity.PROCESS_FIELD] = {"pid": 999, "started_at": 1}
    spool = tmp_path / "hook.jsonl"
    monkeypatch.setenv("MUX_HOOK_URL", "http://127.0.0.1:8765/api/hooks/test")
    monkeypatch.setenv("MUX_HOOK_SECRET", "test-secret")
    monkeypatch.setenv("MUX_HOOK_SPOOL", str(spool))
    monkeypatch.setattr(hook_client.sys, "argv", ["hook_client", "SessionStart"])
    monkeypatch.setattr(hook_client, "_read_payload", lambda: json.dumps(start_payload))
    posted: list[dict[str, Any]] = []

    def post(_url: str, _secret: str, body: bytes) -> bool:
        posted.append(json.loads(body))
        return False

    monkeypatch.setattr(hook_client, "_post", post)
    hook_client.main()
    saved = json.loads(spool.read_text(encoding="utf-8"))
    assert saved["payload"][identity.PROCESS_FIELD] == {"pid": 101, "started_at": 11.0}
    assert saved["payload"] == posted[0]["payload"]


@pytest.mark.parametrize("via_spool", [False, True])
async def test_replacement_updates_run_transcript_and_completion_before_resume(
    processes: dict[int, Process],
    start_payload: dict[str, Any],
    tmp_path: Path,
    via_spool: bool,
) -> None:
    record = agent_record(backend="codex", cwd=str(tmp_path))
    record.pid, record.root_started_at, record.state = 100, 10.0, "idle"
    manager, session = rollover_manager(record)
    session.agent_lifecycle_id = ORIGINAL
    manager.events = EventBus()
    manager._stop_observer = AsyncMock()
    manager._start_observer = lambda *_: None
    manager.maybe_heal_from_own_conversation_hook = AsyncMock(return_value=False)
    manager.note_hook_cwd = lambda *_: None
    manager.note_hook_transcript_path = lambda *_: None
    old_run = record.agent_run_id

    if via_spool:
        manager.hook_spool_dir = tmp_path
        spool = tmp_path / f"{record.id}.jsonl"
        spool.write_text(
            json.dumps(
                {
                    "event": "SessionStart",
                    "payload": start_payload,
                    "spooled_at": time.time(),
                }
            )
            + "\n",
            encoding="utf-8",
        )
        os.utime(spool, (time.time() - 10, time.time() - 10))
        await SessionManager._drain_hook_spool(manager, session)
    else:
        app = web.Application()
        app[keys.SESSIONS] = manager
        app[keys.EVENTS] = manager.events
        app[keys.AUTOMATION] = SimpleNamespace(note_native_hook=lambda _: None)
        app[keys.HOOK_INGRESS_WINDOWS] = {}
        app.router.add_post("/api/hooks/{sid}", hook_ingress)
        async with TestClient(TestServer(app)) as client:
            for event, payload in (
                ("SessionStart", start_payload),
                ("UserPromptSubmit", {"session_id": CLEARED, "prompt": "Evaluate the roadmap"}),
                ("Stop", {"session_id": CLEARED}),
            ):
                response = await client.post(
                    f"/api/hooks/{record.id}",
                    json={"event": event, "payload": payload},
                    headers={"X-Mux-Hook-Secret": "secret"},
                )
                assert response.status == 200, await response.text()

    assert record.native_session_id == CLEARED
    assert record.agent_run_id != old_run
    assert record.agent_run_seq == 1
    assert session.agent_lifecycle_id == CLEARED
    assert session.transcript_path == Path(start_payload["transcript_path"])
    manager.history.agent_run_ended.assert_awaited_once()
    assert manager.history.session_promoted.await_args.args[1] == start_payload["transcript_path"]
    assert record.state == "idle"
    assert any(
        e["kind"] == "codex_conversation_identity_checked" and e["verified"]
        for e in session.state_transitions
    )
    # A delayed startup from the abandoned attempt cannot undo the replacement.
    delayed = {**start_payload, "session_id": ORIGINAL}
    assert (
        conversation_rollover_decision(
            session,
            "SessionStart",
            delayed,
            codex_root_process_verified=True,
        ).refusal_reason
        == "retired_conversation"
    )


async def test_killed_run_persists_the_successor_for_history_resume(
    processes: dict[int, Process],
    start_payload: dict[str, Any],
    tmp_path: Path,
) -> None:
    record = agent_record(backend="codex", cwd=str(tmp_path))
    record.pid, record.root_started_at = 100, 10.0
    manager, session = rollover_manager(record)
    previous_run = record.agent_run_id
    history = HistoryIndex(tmp_path / "history.db")
    manager.history = history
    try:
        await history.session_promoted(record, str(tmp_path / "failed.jsonl"))
        decision = await resolve_conversation_rollover(
            session,
            "SessionStart",
            start_payload,
            EventBus(),
        )
        assert decision.roll_to == CLEARED
        await manager._apply_conversation_rollover(
            session,
            native_id=decision.roll_to,
            transcript=Path(start_payload["transcript_path"]),
            reason="conversation_rolled",
            source="startup",
        )
        await history.agent_run_ended(record, "killed")
        row = await history.history_entry(record.agent_run_id)
        assert row is not None
        assert row["native_id"] == CLEARED
        assert row["transcript_path"] == start_payload["transcript_path"]
        assert row["exit_reason"] == "killed"
        retired = await history.history_entry(previous_run)
        assert retired is not None and retired["native_id"] == ORIGINAL
        assert retired["exit_reason"] == "conversation_rolled"
    finally:
        history.close()


def test_process_proof_does_not_override_claude_or_cwd_or_subagent_guards(tmp_path: Path) -> None:
    manager, session = rollover_manager(agent_record(cwd=str(tmp_path)))
    del manager
    payload = {"source": "startup", "session_id": CLEARED, "cwd": str(tmp_path)}
    assert (
        conversation_rollover_decision(
            session,
            "SessionStart",
            payload,
            codex_root_process_verified=True,
        ).refusal_reason
        == "foreign_process_startup"
    )
    session.record.backend = "codex"
    payload["cwd"] = str(tmp_path / "other")
    assert (
        conversation_rollover_decision(
            session,
            "SessionStart",
            payload,
            codex_root_process_verified=True,
        ).refusal_reason
        == "cwd_mismatch"
    )
    payload["agent_id"] = CLEARED
    assert (
        conversation_rollover_decision(
            session,
            "SessionStart",
            payload,
            codex_root_process_verified=True,
        ).roll_to
        is None
    )
