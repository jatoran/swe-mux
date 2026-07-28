from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from swe_mux.models import SessionRecord
from swe_mux.session import Session, SessionManager


def agent_record(backend: str = "claude", cwd: str = ".") -> SessionRecord:
    record = SessionRecord(
        "mux-id", "agent", "default", backend, "native-id", cwd, f"{backend}.exe", []
    )
    record.spawn_backend = "shell"
    record.spawn_native_session_id = record.id
    return record


def fake_manager() -> Any:
    manager = cast(Any, SessionManager.__new__(SessionManager))
    manager.sessions = {}
    return manager


def fake_agent_session(backend: str, transcript: Path | None, cwd: str = ".") -> Any:
    return cast(
        Any,
        SimpleNamespace(
            record=agent_record(backend, cwd),
            stop_event=asyncio.Event(),
            stopping=False,
            transcript_path=transcript,
            agent_lifecycle_id="lifecycle-id",
            agent_promoted_at=time.time() - 60,
            agent_exit_check_task=None,
            ignored_detection_runs=set(),
            tasks=set(),
        ),
    )


async def test_shell_prompt_after_agent_exit_demotes_quiescent_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("swe_mux.session.AGENT_EXIT_CHECK_INTERVAL_SECONDS", 0.01)
    transcript = tmp_path / "native.jsonl"
    transcript.write_text("{}\n", encoding="utf-8")
    stale = time.time() - 30
    os.utime(transcript, (stale, stale))
    manager = fake_manager()
    session = fake_agent_session("claude", transcript)
    demoted: list[tuple[str, str, str]] = []

    async def demote(sid: str, backend: str, native_id: str) -> None:
        demoted.append((sid, backend, native_id))

    manager.demote = demote
    await SessionManager._confirm_agent_exit(manager, session)

    assert demoted == [("mux-id", "claude", "lifecycle-id")]


async def test_shell_prompt_with_active_transcript_never_demotes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("swe_mux.session.AGENT_EXIT_CHECK_INTERVAL_SECONDS", 0.01)
    transcript = tmp_path / "native.jsonl"
    demoted: list[str] = []
    manager = fake_manager()
    session = fake_agent_session("codex", transcript)

    async def demote(sid: str, backend: str, native_id: str) -> None:
        demoted.append(sid)

    async def keep_fresh() -> None:
        for _ in range(30):
            transcript.write_text("{}\n", encoding="utf-8")
            await asyncio.sleep(0.005)

    manager.demote = demote
    await asyncio.gather(SessionManager._confirm_agent_exit(manager, session), keep_fresh())

    assert demoted == []


def test_prompt_probe_respects_promotion_grace_and_single_flight() -> None:
    manager = fake_manager()
    session = fake_agent_session("claude", None)
    session.agent_promoted_at = time.time()

    SessionManager._queue_agent_exit_check(manager, session)
    assert session.agent_exit_check_task is None

    session.agent_promoted_at = None
    SessionManager._queue_agent_exit_check(manager, session)
    assert session.agent_exit_check_task is None


async def test_prompt_probe_schedules_once_after_grace(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("swe_mux.session.AGENT_EXIT_CHECK_INTERVAL_SECONDS", 0.01)
    monkeypatch.setattr("swe_mux.session.AGENT_EXIT_CONFIRM_ATTEMPTS", 1)
    manager = fake_manager()
    session = fake_agent_session("claude", None)
    demoted: list[str] = []

    async def demote(sid: str, backend: str, native_id: str) -> None:
        demoted.append(sid)

    manager.demote = demote
    SessionManager._queue_agent_exit_check(manager, session)
    first = session.agent_exit_check_task
    assert first is not None
    SessionManager._queue_agent_exit_check(manager, session)
    assert session.agent_exit_check_task is first
    await asyncio.wait_for(first, timeout=2)
    assert demoted == ["mux-id"]


def _switch_fixture(tmp_path: Path) -> tuple[Any, Any, Path, Path]:
    current = tmp_path / "old.jsonl"
    current.write_text("{}\n", encoding="utf-8")
    stale = time.time() - 30
    os.utime(current, (stale, stale))
    fresh = tmp_path / "new.jsonl"
    fresh.write_text("{}\n", encoding="utf-8")
    manager = fake_manager()
    session = fake_agent_session("claude", current, cwd=str(tmp_path))
    session.adapter = SimpleNamespace(
        name="claude",
        recent_transcripts=lambda cwd, created_at: [
            (fresh.stat().st_mtime, fresh, "native-new"),
        ],
    )
    manager.sessions = {"mux-id": session}
    return manager, session, current, fresh


def test_transcript_switch_targets_fresh_unowned_transcript(tmp_path: Path) -> None:
    manager, session, current, fresh = _switch_fixture(tmp_path)
    assert SessionManager._transcript_switch_candidate(manager, session, current) == fresh


def test_transcript_switch_skips_while_current_transcript_active(tmp_path: Path) -> None:
    manager, session, current, _fresh = _switch_fixture(tmp_path)
    now = time.time()
    os.utime(current, (now, now))
    assert SessionManager._transcript_switch_candidate(manager, session, current) is None


def test_transcript_switch_never_steals_another_sessions_transcript(tmp_path: Path) -> None:
    manager, session, current, fresh = _switch_fixture(tmp_path)
    other = SimpleNamespace(record=agent_record("claude", str(tmp_path)), transcript_path=fresh)
    manager.sessions["other"] = other
    assert SessionManager._transcript_switch_candidate(manager, session, current) is None


def test_transcript_switch_ignores_explicitly_ended_runs(tmp_path: Path) -> None:
    manager, session, current, _fresh = _switch_fixture(tmp_path)
    session.ignored_detection_runs.add(("claude", "native-new"))
    assert SessionManager._transcript_switch_candidate(manager, session, current) is None


def test_transcript_switch_requires_actively_written_candidate(tmp_path: Path) -> None:
    manager, session, current, fresh = _switch_fixture(tmp_path)
    stale = time.time() - 30
    os.utime(fresh, (stale, stale))
    assert SessionManager._transcript_switch_candidate(manager, session, current) is None


def test_transcript_switch_blocked_by_any_sibling_in_same_cwd(tmp_path: Path) -> None:
    # A sibling agent in the same directory makes a fresh transcript ambiguous even
    # when the sibling does not own the candidate file: switching could still be
    # grabbing the sibling's just-created conversation. Never switch here.
    manager, session, current, _fresh = _switch_fixture(tmp_path)
    sibling = SimpleNamespace(
        record=agent_record("claude", str(tmp_path)),
        transcript_path=tmp_path / "unrelated.jsonl",
    )
    manager.sessions["sibling"] = sibling
    assert SessionManager._transcript_switch_candidate(manager, session, current) is None


def test_transcript_switch_allowed_when_sibling_is_in_a_different_cwd(tmp_path: Path) -> None:
    manager, session, current, fresh = _switch_fixture(tmp_path)
    other_dir = tmp_path / "other"
    elsewhere = SimpleNamespace(
        record=agent_record("claude", str(other_dir)),
        transcript_path=other_dir / "e.jsonl",
    )
    manager.sessions["elsewhere"] = elsewhere
    assert SessionManager._transcript_switch_candidate(manager, session, current) == fresh


def test_transcript_switch_ignores_ended_sibling_in_same_cwd(tmp_path: Path) -> None:
    manager, session, current, fresh = _switch_fixture(tmp_path)
    dead = agent_record("claude", str(tmp_path))
    dead.state = "exited"
    manager.sessions["dead"] = SimpleNamespace(record=dead, transcript_path=None)
    assert SessionManager._transcript_switch_candidate(manager, session, current) == fresh


def _lifecycle_manager(record: SessionRecord) -> tuple[Any, Session]:
    pty = SimpleNamespace(graceful_exit="", isalive=lambda: True)
    shell = SimpleNamespace(name="shell", graceful_exit_keys=lambda: "exit\r")
    codex = SimpleNamespace(name="codex", graceful_exit_keys=lambda: "/exit\r")
    session = Session(record, cast(Any, pty), cast(Any, codex), 32, "secret")
    manager = cast(Any, SessionManager.__new__(SessionManager))
    manager.sessions = {record.id: session}
    manager.adapters = {"shell": shell, "codex": codex}
    manager.events = SimpleNamespace(emit=AsyncMock())
    return manager, session


async def test_direct_agent_ignores_nested_cli_promotion_and_demotion(tmp_path: Path) -> None:
    record = SessionRecord(
        "root",
        "codex-root",
        "default",
        "codex",
        "root-native",
        str(tmp_path),
        "node.exe",
        [r"C:\npm\node_modules\@openai\codex\bin\codex.js"],
    )
    record.spawn_backend = "codex"
    record.spawn_native_session_id = "root"
    record.agent_run_id = record.id
    manager, session = _lifecycle_manager(record)

    promoted = await manager.promote(record.id, "codex", "child-probe")
    demoted = await manager.demote(record.id, "codex", "child-probe")

    assert promoted is demoted is session
    assert record.backend == "codex"
    assert record.native_session_id == "root-native"
    assert record.agent_run_id == record.id
    assert session.agent_lifecycle_id is None
    reasons = [
        call.kwargs["reason"] for call in manager.events.emit.await_args_list
    ]
    assert reasons == ["root_agent_owns_pty", "root_agent_owns_pty"]


async def test_promoted_shell_ignores_a_second_nested_agent_lifecycle(tmp_path: Path) -> None:
    record = SessionRecord(
        "shell",
        "active",
        "default",
        "codex",
        "root-agent",
        str(tmp_path),
        "pwsh.exe",
        [],
    )
    record.spawn_backend = "shell"
    record.spawn_native_session_id = record.id
    record.agent_run_id = "run-id"
    manager, session = _lifecycle_manager(record)
    session.agent_lifecycle_id = "root-launcher"

    unchanged = await manager.promote(record.id, "codex", "child-launcher")

    assert unchanged is session
    assert record.backend == "codex"
    assert record.native_session_id == "root-agent"
    assert session.agent_lifecycle_id == "root-launcher"
    manager.events.emit.assert_awaited_once()
    assert manager.events.emit.await_args.kwargs["reason"] == "agent_run_already_active"


def test_adoption_repairs_provider_and_sibling_transcript_contamination(
    tmp_path: Path,
) -> None:
    sibling_path = tmp_path / "claude-sibling.jsonl"
    sibling_path.write_text("{}\n", encoding="utf-8")
    codex_path = tmp_path / "codex-root.jsonl"
    codex_path.write_text("{}\n", encoding="utf-8")
    root = SessionRecord(
        "root",
        "shell-root",
        "default",
        "claude",
        "claude-sibling",
        str(tmp_path),
        "node.exe",
        [r"C:\npm\node_modules\@openai\codex\bin\codex.js"],
    )
    root.agent_run_id = "false-run"
    root.model = "claude-opus"
    root.tokens_in = 100
    sibling = SessionRecord(
        "sibling",
        "claude-sibling",
        "default",
        "claude",
        "claude-sibling",
        str(tmp_path),
        "claude.exe",
        ["--session-id", "claude-sibling"],
    )
    sibling.agent_run_id = sibling.id
    manager = fake_manager()
    manager.adapters = {
        "codex": SimpleNamespace(
            name="codex",
            recent_transcripts=lambda *_: [(time.time(), codex_path, "codex-native")],
            transcript_native_id=lambda path: (
                "codex-native" if Path(path) == codex_path else None
            ),
        )
    }
    records = {"root": root, "sibling": sibling}
    metas = {
        "root": {"transcript_path": str(sibling_path)},
        "sibling": {"transcript_path": str(sibling_path)},
    }
    for record in records.values():
        manager._ensure_spawn_identity(record)

    transcript, bad_run_id, previous = manager._reconcile_adopted_root_identity(
        root, metas["root"], records, metas
    )

    assert root.spawn_backend == "codex"
    assert root.backend == "codex"
    assert root.native_session_id == "codex-native"
    assert root.agent_run_id == root.id
    assert root.name == "codex-root"
    assert root.state == "starting"
    assert root.model is None
    assert root.tokens_in == 0
    assert transcript == codex_path
    assert bad_run_id == "false-run"
    assert previous == {
        "backend": "claude",
        "native_session_id": "claude-sibling",
        "transcript_path": str(sibling_path),
    }


def test_adoption_refuses_ambiguous_unowned_transcripts(tmp_path: Path) -> None:
    first = tmp_path / "first.jsonl"
    second = tmp_path / "second.jsonl"
    record = SessionRecord(
        "root",
        "codex-root",
        "default",
        "codex",
        "unknown",
        str(tmp_path),
        "codex.exe",
        [],
    )
    record.spawn_backend = "codex"
    record.spawn_native_session_id = record.id
    manager = fake_manager()
    manager.adapters = {
        "codex": SimpleNamespace(
            recent_transcripts=lambda *_: [
                (2.0, second, "second"),
                (1.0, first, "first"),
            ],
            transcript_native_id=lambda _path: None,
        )
    }

    assert manager._adoption_transcript(
        record, {}, {record.id: record}, {record.id: {}}
    ) is None


def test_adoption_demotes_a_shell_run_claiming_a_live_sibling(
    tmp_path: Path,
) -> None:
    sibling_path = tmp_path / "sibling.jsonl"
    shell = SessionRecord(
        "shell",
        "wrong-agent",
        "default",
        "claude",
        "sibling-native",
        str(tmp_path),
        "pwsh.exe",
        [],
    )
    shell.spawn_backend = "shell"
    shell.spawn_native_session_id = shell.id
    shell.agent_run_id = "false-run"
    sibling = SessionRecord(
        "sibling",
        "claude-sibling",
        "default",
        "claude",
        "sibling-native",
        str(tmp_path),
        "claude.exe",
        ["--session-id", "sibling-native"],
    )
    sibling.agent_run_id = sibling.id
    manager = fake_manager()
    records = {shell.id: shell, sibling.id: sibling}
    metas = {
        shell.id: {"transcript_path": str(sibling_path)},
        sibling.id: {"transcript_path": str(sibling_path)},
    }
    for record in records.values():
        manager._ensure_spawn_identity(record)

    transcript, bad_run_id, previous = manager._reconcile_adopted_root_identity(
        shell, metas[shell.id], records, metas
    )

    assert transcript is None
    assert bad_run_id == "false-run"
    assert previous is not None
    assert shell.backend == "shell"
    assert shell.native_session_id == shell.id
    assert shell.agent_run_id is None
    assert shell.state == "running"
    assert shell.parser_status == "not_applicable"


def test_owned_transcript_filter_excludes_live_sibling(tmp_path: Path) -> None:
    own_path = tmp_path / "own.jsonl"
    sibling_path = tmp_path / "sibling.jsonl"
    record = agent_record("codex", str(tmp_path))
    record.native_session_id = "own"
    manager, session = _lifecycle_manager(record)
    sibling_record = agent_record("codex", str(tmp_path))
    sibling_record.id = "sibling"
    sibling_record.native_session_id = "sibling"
    manager.sessions["sibling"] = SimpleNamespace(
        record=sibling_record, transcript_path=sibling_path
    )

    candidates = manager._unclaimed_transcripts(
        session,
        [
            (2.0, sibling_path, "sibling"),
            (1.0, own_path, "own"),
        ],
    )

    assert candidates == [(1.0, own_path, "own")]


async def test_supervisor_adoption_repairs_legacy_identity_and_persists_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    codex_path = tmp_path / "codex.jsonl"
    codex_path.write_text("{}\n", encoding="utf-8")
    sibling_path = tmp_path / "sibling.jsonl"
    sibling_path.write_text("{}\n", encoding="utf-8")
    root = SessionRecord(
        "root",
        "shell-root",
        "default",
        "claude",
        "sibling-native",
        str(tmp_path),
        "node.exe",
        [r"C:\npm\node_modules\@openai\codex\bin\codex.js"],
        state="idle",
    )
    root.agent_run_id = "false-run"
    sibling = SessionRecord(
        "sibling",
        "claude-sibling",
        "default",
        "claude",
        "sibling-native",
        str(tmp_path),
        "claude.exe",
        ["--session-id", "sibling-native"],
        state="idle",
    )
    sibling.agent_run_id = sibling.id
    infos = [
        {
            "sid": root.id,
            "meta": {
                "record": root.snapshot(),
                "transcript_path": str(sibling_path),
                "agent_lifecycle_id": "child-probe",
            },
        },
        {
            "sid": sibling.id,
            "meta": {
                "record": sibling.snapshot(),
                "transcript_path": str(sibling_path),
            },
        },
    ]

    class Host:
        graceful_exit = ""

        def prepare(self) -> None:
            pass

        def isalive(self) -> bool:
            return True

    class Client:
        initial_sessions = infos

        def __init__(self) -> None:
            self.metadata: dict[str, dict[str, Any]] = {}

        async def subscribe(self, _host: Host) -> tuple[dict[str, Any], bytes]:
            return {"position": 0}, b""

        def queue_meta(self, sid: str, meta: dict[str, Any]) -> None:
            self.metadata[sid] = meta

        def unregister_host(self, _host: Host) -> None:
            pass

        def notify(self, _payload: dict[str, Any]) -> None:
            pass

    client = Client()
    manager = cast(Any, SessionManager.__new__(SessionManager))
    manager.supervisor = client
    manager.sessions = {}
    manager.max_scrollback = 128
    manager.adapters = {
        "shell": SimpleNamespace(name="shell"),
        "codex": SimpleNamespace(
            name="codex",
            recent_transcripts=lambda *_: [(time.time(), codex_path, "codex-native")],
            transcript_native_id=lambda path: (
                "codex-native" if Path(path) == codex_path else None
            ),
        ),
        "claude": SimpleNamespace(
            name="claude",
            recent_transcripts=lambda *_: [],
            transcript_native_id=lambda path: Path(path).stem,
        ),
    }
    manager.history = SimpleNamespace(
        quarantine_misattributed_agent_run=AsyncMock(),
        session_promoted=AsyncMock(),
        reopen_agent_run=AsyncMock(),
    )
    manager.events = SimpleNamespace(emit=AsyncMock())
    observed: list[tuple[str, Path | None]] = []
    manager._start_observer = lambda session, path: observed.append((session.record.id, path))

    async def no_background_work(_session: Any) -> None:
        return None

    manager._fanout = no_background_work
    manager._ticker = no_background_work
    monkeypatch.setattr("swe_mux.session.host_for_adoption", lambda *_: Host())

    adopted = await manager.adopt_supervisor_sessions()

    assert adopted == 2
    revived = manager.sessions[root.id]
    assert revived.record.backend == "codex"
    assert revived.record.native_session_id == "codex-native"
    assert revived.record.spawn_backend == "codex"
    assert revived.record.agent_run_id == root.id
    assert revived.transcript_path == codex_path
    assert revived.agent_lifecycle_id is None
    assert revived.agent_promoted_at is None
    assert client.metadata[root.id]["record"]["backend"] == "codex"
    manager.history.quarantine_misattributed_agent_run.assert_awaited_once_with(
        "false-run", "root_identity_reconciled"
    )
    manager.history.reopen_agent_run.assert_awaited_once_with(root.id)
    assert (root.id, codex_path) in observed


# ---- transcript adoption / switch: non-mux writers ---------------------------


def test_transcript_switch_refuses_a_transcript_this_pty_did_not_write(
    tmp_path: Path,
) -> None:
    """A VS Code Claude extension or a plain-terminal `claude` in the same repo
    writes into the same shared per-cwd directory. Adopting it rekeys this
    session's native id and streams the outsider's status and tokens as its own.
    """
    manager, session, current, _fresh = _switch_fixture(tmp_path)
    # This session's PTY has been silent since well before the candidate appeared,
    # so its CLI cannot be the one writing it.
    session.record.last_activity_ts = time.time() - 600
    assert SessionManager._transcript_switch_candidate(manager, session, current) is None


def test_transcript_switch_allows_a_transcript_this_pty_was_active_for(
    tmp_path: Path,
) -> None:
    manager, session, current, fresh = _switch_fixture(tmp_path)
    session.record.last_activity_ts = time.time()
    assert SessionManager._transcript_switch_candidate(manager, session, current) == fresh


def test_transcript_switch_blocked_by_an_unpromoted_shell_launching_this_backend(
    tmp_path: Path,
) -> None:
    """A shim-less `claude` in a sibling shell races detection.

    The shell's 0.5s detection loop usually wins the claim, but when it does not,
    this session adopts the new CLI's transcript and the shell never promotes.
    """
    manager, session, current, _fresh = _switch_fixture(tmp_path)
    shell = SimpleNamespace(
        record=agent_record("shell", str(tmp_path)),
        transcript_path=None,
        pending_agent_backends={"claude"},
    )
    manager.sessions["shell"] = shell
    assert SessionManager._transcript_switch_candidate(manager, session, current) is None


def test_transcript_switch_ignores_a_plain_shell_with_no_agent_launch(
    tmp_path: Path,
) -> None:
    """A shell that has never echoed an agent name is not a blocking sibling."""
    manager, session, current, fresh = _switch_fixture(tmp_path)
    shell = SimpleNamespace(
        record=agent_record("shell", str(tmp_path)),
        transcript_path=None,
        pending_agent_backends=set(),
    )
    manager.sessions["shell"] = shell
    assert SessionManager._transcript_switch_candidate(manager, session, current) == fresh


def test_sole_candidate_is_refused_when_the_spawn_named_the_conversation(
    tmp_path: Path,
) -> None:
    """Claude is spawned with `--session-id <uuid>`; nothing else is ours.

    Taking the single-unclaimed-candidate fallback here binds the session to an
    unmanaged CLI's conversation and permanently rekeys native_session_id.
    """
    outsider = tmp_path / "outsider.jsonl"
    outsider.write_text("{}\n", encoding="utf-8")
    session = fake_agent_session("claude", None, cwd=str(tmp_path))
    session.record.native_session_id = "123e4567-e89b-12d3-a456-426614174000"
    assert (
        SessionManager._may_adopt_sole_candidate(session, outsider, time.time() - 60) is False
    )


def test_sole_candidate_is_accepted_for_a_shim_less_promotion(tmp_path: Path) -> None:
    """Without an injected id the fallback is the only route to the transcript."""
    own = tmp_path / "own.jsonl"
    own.write_text("{}\n", encoding="utf-8")
    session = fake_agent_session("claude", None, cwd=str(tmp_path))
    session.record.native_session_id = session.record.id  # mux id, not a uuid
    assert SessionManager._may_adopt_sole_candidate(session, own, time.time() - 60) is True


def test_sole_candidate_predating_the_run_is_refused(tmp_path: Path) -> None:
    """A conversation that already existed cannot have been started by this run."""
    older = tmp_path / "older.jsonl"
    older.write_text("{}\n", encoding="utf-8")
    session = fake_agent_session("codex", None, cwd=str(tmp_path))
    session.record.native_session_id = session.record.id
    # Started an hour after the file was created.
    assert (
        SessionManager._may_adopt_sole_candidate(session, older, time.time() + 3600) is False
    )
