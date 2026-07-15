from __future__ import annotations

import asyncio
import time
from collections import deque
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from swe_mux.config import Config, ShellProfile
from swe_mux.event_bus import EventBus
from swe_mux.history import HistoryIndex
from swe_mux.meta_hooks import HookRule, MetaHookEngine
from swe_mux.models import MuxEvent, SessionRecord
from swe_mux.profiles import resolve_profile
from swe_mux.project_files import read_note, write_note
from swe_mux.runtime_cwd import Osc7Parser, local_directory_from_osc7
from swe_mux.session import ScrollbackBuffer, Session, SessionManager


def test_osc7_parser_handles_fragmentation_and_rejects_remote_or_missing_paths(
    tmp_path: Path,
) -> None:
    parser = Osc7Parser()
    uri = tmp_path.as_uri().encode()
    assert parser.feed(b"before\x1b") == []
    assert parser.feed(b"]7;" + uri[:5]) == []
    assert parser.feed(uri[5:] + b"\x07after") == [tmp_path.as_uri()]
    assert local_directory_from_osc7(tmp_path.as_uri()) == tmp_path.resolve()
    assert local_directory_from_osc7("file://attacker.example/tmp") is None
    assert local_directory_from_osc7((tmp_path / "missing").as_uri()) is None


def test_shell_profile_cwd_integration_is_explicit_and_process_local(tmp_path: Path) -> None:
    executable = tmp_path / "pwsh.exe"
    executable.write_bytes(b"fixture")
    plain = ShellProfile("plain", "Plain", str(executable), ["-NoLogo"])
    integrated = ShellProfile(
        "live", "Live", str(executable), ["-NoLogo"], cwd_integration=True
    )
    config = Config(
        shell_profiles=[plain, integrated], default_shell_profile=plain.id
    )
    assert resolve_profile(config, plain.id, tmp_path).argv == ("-NoLogo",)
    wrapped = resolve_profile(config, integrated.id, tmp_path)
    assert "-Command" in wrapped.argv
    assert "cwd-osc7" in wrapped.capabilities
    script = wrapped.argv[-1]
    assert "$([char]27)]7;" in script
    assert "`e]7;" not in script


@pytest.mark.asyncio
async def test_multiple_agent_runs_in_one_shell_keep_distinct_history_owners(
    tmp_path: Path,
) -> None:
    history = HistoryIndex(tmp_path / "mux.db")
    record = SessionRecord(
        "pty", "shell", "default", "shell", "pty", str(tmp_path), "pwsh", []
    )
    record.spawn_cwd = str(tmp_path)
    record.spawn_project_scope_id = "spawn-scope"
    await history.session_started(record, None)

    record.backend = "claude"
    record.native_session_id = "native-1"
    record.agent_run_id = "run-1"
    record.agent_run_started_at = 10
    record.run_cwd = str(tmp_path / "one")
    record.run_project_scope_id = "scope-one"
    record.project_scope_id = "scope-one"
    await history.session_promoted(record, "one.jsonl")
    await history.agent_run_ended(record, "agent_exit")

    record.backend = "codex"
    record.native_session_id = "native-2"
    record.agent_run_id = "run-2"
    record.agent_run_started_at = 20
    record.run_cwd = str(tmp_path / "two")
    record.run_project_scope_id = "scope-two"
    record.project_scope_id = "scope-two"
    await history.session_promoted(record, "two.jsonl")

    rows = await history.history()
    assert {row["id"] for row in rows} == {"run-1", "run-2"}
    assert {row["project_scope_id"] for row in rows} == {"scope-one", "scope-two"}
    history.close()


def test_hook_scope_is_authoritative_and_payload_cannot_spoof_it(tmp_path: Path) -> None:
    record = SessionRecord(
        "pty", "shell", "default", "shell", "pty", str(tmp_path), "pwsh", []
    )
    record.spawn_project_scope_id = "trusted-spawn"
    record.runtime_project_scope_id = "untrusted-runtime"
    session = SimpleNamespace(record=record)
    engine = MetaHookEngine(
        tmp_path / "hooks.toml",
        EventBus(),
        cast(Any, SimpleNamespace(sessions={"pty": session})),
    )
    event = MuxEvent(
        1, "pty", "pty", "command_failed", {"project_scope_id": "spoofed"}
    )
    assert engine._matches(HookRule({"project_scope_id": "trusted-spawn"}, {}), event)
    assert not engine._matches(HookRule({"project_scope_id": "spoofed"}, {}), event)
    record.backend = "claude"
    record.agent_run_id = "run"
    record.run_project_scope_id = "trusted-run"
    assert engine._matches(HookRule({"project_scope_id": "trusted-run"}, {}), event)


@pytest.mark.asyncio
async def test_project_note_has_one_canonical_project_path(tmp_path: Path) -> None:
    first = await read_note(tmp_path, "projects", "scope-id")
    assert Path(first["path"]) == tmp_path / ".swe-mux" / "notes" / "project.md"
    saved = await write_note(tmp_path, "projects", "scope-id", "project context", "missing")
    assert saved["markdown"] == "project context"


@pytest.mark.asyncio
async def test_runtime_cwd_switch_rate_limit_prevents_poll_target_churn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def immediate_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", immediate_sleep)
    record = SessionRecord(
        "pty", "shell", "default", "shell", "pty", str(tmp_path), "pwsh", []
    )
    fake = SimpleNamespace(
        record=record,
        stop_event=asyncio.Event(),
        cwd_switches=deque([time.monotonic()] * 12),
        cwd_telemetry_dropped=0,
        publish_update=lambda: None,
    )
    manager = cast(Any, SessionManager.__new__(SessionManager))
    await manager._accept_runtime_cwd(fake, tmp_path)
    assert record.runtime_cwd_live is False
    assert record.runtime_cwd_dropped == 1


@pytest.mark.asyncio
async def test_fallback_detection_ignores_a_native_run_that_already_exited(
    tmp_path: Path,
) -> None:
    transcript = tmp_path / "native-ended.jsonl"
    transcript.write_text("{}\n", encoding="utf-8")
    record = SessionRecord(
        "pty", "shell", "default", "shell", "pty", str(tmp_path), "pwsh", []
    )

    class Pty:
        checks = 0

        def isalive(self) -> bool:
            self.checks += 1
            return self.checks == 1

    adapter = SimpleNamespace(
        name="claude",
        recent_transcripts=lambda *_: [(time.time(), transcript, "native-ended")],
    )
    session = SimpleNamespace(
        record=record,
        pty=Pty(),
        stop_event=asyncio.Event(),
        scrollback=SimpleNamespace(
            position=0, bytes_since=lambda _position: b"PS> claude"
        ),
        ignored_detection_runs={("claude", "native-ended")},
    )
    manager = cast(Any, SessionManager.__new__(SessionManager))
    manager.adapters = {"shell": SimpleNamespace(name="shell"), "claude": adapter}

    await manager._detect_nested_agent(session)

    assert record.backend == "shell"
    assert record.agent_run_id is None


@pytest.mark.asyncio
async def test_fallback_detection_ignores_agent_names_retained_before_demotion(
    tmp_path: Path,
) -> None:
    record = SessionRecord(
        "pty", "shell", "default", "shell", "pty", str(tmp_path), "pwsh", []
    )
    scrollback = ScrollbackBuffer(128)
    scrollback.append(b"old Claude output mentioned codex\r\nPS> ")

    class Pty:
        checks = 0

        def isalive(self) -> bool:
            self.checks += 1
            return self.checks == 1

    adapter = SimpleNamespace(
        name="codex",
        recent_transcripts=lambda *_: (_ for _ in ()).throw(
            AssertionError("retained output must not trigger transcript detection")
        ),
    )
    session = SimpleNamespace(
        record=record,
        pty=Pty(),
        stop_event=asyncio.Event(),
        scrollback=scrollback,
        ignored_detection_runs=set(),
    )
    manager = cast(Any, SessionManager.__new__(SessionManager))
    manager.adapters = {"shell": SimpleNamespace(name="shell"), "codex": adapter}

    await manager._detect_nested_agent(session)

    assert record.backend == "shell"
    assert record.state == "starting"


@pytest.mark.asyncio
async def test_codex_demotes_by_stable_launcher_id_after_native_id_discovery(
    tmp_path: Path,
) -> None:
    record = SessionRecord(
        "pty", "codex", "default", "codex", "codex-native", str(tmp_path), "pwsh", []
    )
    record.agent_run_id = "run"
    record.run_cwd = str(tmp_path)
    pty = SimpleNamespace(graceful_exit="", isalive=lambda: True)
    codex = SimpleNamespace(graceful_exit_keys=lambda: "exit\r")
    shell = SimpleNamespace(graceful_exit_keys=lambda: "exit\r")
    session = Session(record, cast(Any, pty), cast(Any, codex), 32, "secret")
    session.agent_lifecycle_id = "launcher-token"

    manager = cast(Any, SessionManager.__new__(SessionManager))
    manager.sessions = {record.id: session}
    manager.adapters = {"shell": shell, "codex": codex}
    manager.history = SimpleNamespace(
        update_agent_summary=AsyncMock(), agent_run_ended=AsyncMock()
    )
    manager.events = SimpleNamespace(emit=AsyncMock())
    manager._start_detection = lambda _session: None

    unchanged = await manager.demote(record.id, "codex", "stale-launcher")
    assert unchanged.record.backend == "codex"

    demoted = await manager.demote(record.id, "codex", "launcher-token")
    assert demoted.record.backend == "shell"
    assert demoted.record.state == "running"
    assert demoted.record.agent_run_id is None
    assert demoted.agent_lifecycle_id is None
    assert ("codex", "codex-native") in demoted.ignored_detection_runs
