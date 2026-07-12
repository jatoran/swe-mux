from __future__ import annotations

import json
from pathlib import Path

import pytest

from swe_mux.adapters import ClaudeAdapter, ShellAdapter, SpawnOptions
from swe_mux.agent_launcher import _claude, _codex
from swe_mux.config import Config
from swe_mux.event_bus import EventBus
from swe_mux.history import HistoryIndex
from swe_mux.launchers import create_agent_shims
from swe_mux.models import SessionRecord, SpaceRecord
from swe_mux.pty_host import merge_environment
from swe_mux.reconcile import reconcile_external_history
from swe_mux.server import hook_event_payload
from swe_mux.transcript_view import parse_transcript


def test_adapters_keep_executable_out_of_cmdline(tmp_path: Path) -> None:
    opts = SpawnOptions(tmp_path, args=["--dangerously-skip-permissions"])
    exe, cmdline = ClaudeAdapter("claude.exe").spawn_cmdline("native-id", opts)
    assert exe == "claude.exe"
    assert cmdline == "--session-id native-id --dangerously-skip-permissions"

    shell_exe, shell_cmdline = ShellAdapter("powershell.exe").spawn_cmdline(
        "ignored", SpawnOptions(tmp_path)
    )
    assert shell_exe == "powershell.exe"
    assert shell_cmdline == "-NoLogo"


async def test_history_and_event_bus_persist_contract(tmp_path: Path) -> None:
    history = HistoryIndex(tmp_path / "mux.db")
    space = SpaceRecord("default", "Main", 0)
    await history.ensure_default_space(space)
    assert [item.id for item in await history.list_spaces()] == ["default"]

    session = SessionRecord(
        "mux-id",
        "test",
        "default",
        "shell",
        "native-id",
        str(tmp_path),
        "powershell.exe",
        [],
        state="running",
    )
    await history.session_started(session, None)
    bus = EventBus(history.append_event)
    subscriber = bus.subscribe()
    emitted = await bus.emit("session_spawned", session_id=session.id, name=session.name)
    assert await subscriber.get() == emitted

    rows = await history.history("test")
    assert rows[0]["native_id"] == "native-id"
    events = await history.events(session_id=session.id)
    assert events[0]["type"] == "session_spawned"
    assert events[0]["payload"] == {"name": "test"}
    history.close()


def test_agent_launchers_inject_mux_wiring(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MUX_CLAUDE_EXE", "real-claude.exe")
    monkeypatch.setenv("MUX_CODEX_EXE", "real-codex.exe")
    monkeypatch.setenv("MUX_CLAUDE_SETTINGS", str(tmp_path / "hooks.json"))
    exe, args, native_id = _claude([])
    assert exe == "real-claude.exe"
    assert args[:2] == ["--settings", str(tmp_path / "hooks.json")]
    assert "--session-id" in args
    assert native_id

    exe, args, native_id = _codex(["resume", "codex-native"])
    assert exe == "real-codex.exe"
    assert args[:2] == ["-c", args[1]]
    assert "notify=" in args[1]
    assert args[-2:] == ["resume", "codex-native"]
    assert native_id == "codex-native"

    cfg = Config(data_dir=tmp_path)
    env = create_agent_shims(cfg, tmp_path / "hooks.json")
    assert (tmp_path / "bin" / "claude.cmd").is_file()
    assert env["PATH"].startswith(str(tmp_path / "bin"))


def test_windows_environment_override_is_case_insensitive() -> None:
    merged = merge_environment(
        {"Path": r"C:\Windows", "TEMP": r"C:\Temp"},
        {"PATH": r"C:\mux\bin;C:\Windows", "Mux_Session_Id": "one"},
    )
    assert merged["PATH"].startswith(r"C:\mux\bin")
    assert "Path" not in merged
    assert len([key for key in merged if key.casefold() == "path"]) == 1


def test_official_claude_hook_envelope_cannot_shadow_mux_metadata() -> None:
    payload = hook_event_payload(
        {
            "session_id": "claude-native",
            "hook_event_name": "UserPromptSubmit",
            "cwd": r"D:\project",
            "source": "untrusted",
        }
    )
    assert payload == {
        "hook_event_name": "UserPromptSubmit",
        "cwd": r"D:\project",
    }


async def test_external_history_reconciliation_and_codex_view(tmp_path: Path) -> None:
    home = tmp_path / "home"
    claude = home / ".claude" / "projects" / "project" / "claude-id.jsonl"
    claude.parent.mkdir(parents=True)
    claude.write_text(
        json.dumps(
            {
                "type": "user",
                "sessionId": "claude-id",
                "cwd": str(tmp_path / "repo"),
                "timestamp": "2026-01-02T03:04:05Z",
                "message": {"content": "hello"},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    codex = home / ".codex" / "sessions" / "2026" / "rollout-test.jsonl"
    codex.parent.mkdir(parents=True)
    codex.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "type": "session_meta",
                        "timestamp": "2026-01-03T03:04:05Z",
                        "payload": {"id": "codex-id", "cwd": str(tmp_path / "repo")},
                    }
                ),
                json.dumps(
                    {
                        "type": "event_msg",
                        "payload": {"type": "agent_message", "message": "done"},
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )
    history = HistoryIndex(tmp_path / "mux.db")
    assert await reconcile_external_history(history, home) == 2
    rows = await history.history()
    assert {row["native_id"] for row in rows} == {"claude-id", "codex-id"}
    assert all(row["external"] == 1 for row in rows)
    messages = parse_transcript(codex, "codex")
    assert messages[0]["content"][0]["text"] == "done"
    history.close()
