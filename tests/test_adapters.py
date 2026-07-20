import json
import shlex
import time
from pathlib import Path

import pytest

from swe_mux.adapters import ClaudeAdapter, CodexAdapter, ShellAdapter, SpawnOptions, SpawnSpec
from swe_mux.adapters.claude import _hook_command, encode_cwd
from swe_mux.reconcile import inspect_claude, inspect_codex


def test_spawn_spec_defaults_are_safe() -> None:
    first = SpawnSpec("shell")
    second = SpawnSpec("shell")

    assert first.argv == ()
    assert first.env == {}
    assert first.env is not second.env


def test_provider_transcript_roots_follow_current_cli_path_rules(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cwd = tmp_path / "folder_with spaces"
    cwd.mkdir()
    assert "_" not in encode_cwd(cwd)
    assert " " not in encode_cwd(cwd)

    codex_home = tmp_path / "custom-codex"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    rollout = codex_home / "sessions" / "2026" / "rollout-test.jsonl"
    rollout.parent.mkdir(parents=True)
    rollout.write_text(
        json.dumps({"type": "session_meta", "payload": {"id": "root", "cwd": str(cwd)}})
        + "\n",
        encoding="utf-8",
    )
    assert CodexAdapter().recent_transcripts(cwd, time.time() - 2)[0][1] == rollout


def test_shell_preserves_explicit_executable_and_argv(tmp_path: Path) -> None:
    spec = ShellAdapter("powershell.exe").spawn_spec(
        "ignored",
        SpawnOptions(tmp_path, exe="cmd.exe", args=["/Q", "/K", "echo one & echo two"]),
    )

    assert spec == SpawnSpec("cmd.exe", ("/Q", "/K", "echo one & echo two"))
    assert ShellAdapter().resume_spec("ignored", SpawnOptions(tmp_path)).argv == ()


def test_claude_spawn_and_resume_are_structured(tmp_path: Path) -> None:
    adapter = ClaudeAdapter("claude.exe")
    opts = SpawnOptions(tmp_path, args=["--model", "sonnet 4"])

    assert adapter.spawn_spec("new-id", opts) == SpawnSpec(
        "claude.exe", ("--session-id", "new-id", "--model", "sonnet 4")
    )
    assert adapter.resume_spec("old-id", opts) == SpawnSpec(
        "claude.exe", ("--resume", "old-id", "--model", "sonnet 4")
    )


def test_claude_includes_generated_hook_settings_as_one_argument(tmp_path: Path) -> None:
    adapter = ClaudeAdapter(data_dir=tmp_path)

    spec = adapter.spawn_spec("new-id", SpawnOptions(tmp_path))

    assert spec.argv == (
        "--session-id",
        "new-id",
        "--settings",
        str(tmp_path / "claude-hooks.json"),
    )


def test_claude_session_hook_settings_are_isolated_and_cleaned(tmp_path: Path) -> None:
    adapter = ClaudeAdapter(data_dir=tmp_path)
    environment = adapter.session_env("mux-session")
    path = Path(environment["MUX_CLAUDE_SETTINGS"])

    assert path == tmp_path / "sessions" / "mux-session" / "claude-hooks.json"
    assert path.is_file()
    spec = adapter.spawn_spec("native", SpawnOptions(tmp_path, session_id="mux-session"))
    assert str(path) in spec.argv

    adapter.cleanup("mux-session")
    assert not path.parent.exists()


def test_claude_hook_command_is_bash_safe_for_windows_venv(tmp_path: Path) -> None:
    command = _hook_command("SessionStart", r"D:\PROJECTS\swe-mux\.venv\Scripts\python.exe")
    argv = shlex.split(command)
    assert argv == [
        "/d/PROJECTS/swe-mux/.venv/Scripts/python.exe",
        "-m",
        "swe_mux.hook_client",
        "SessionStart",
    ]
    assert "\\" not in command

    ClaudeAdapter(data_dir=tmp_path)
    settings = json.loads((tmp_path / "claude-hooks.json").read_text(encoding="utf-8"))
    generated = settings["hooks"]["SessionStart"][0]["hooks"][0]["command"]
    assert shlex.split(generated)[1:] == ["-m", "swe_mux.hook_client", "SessionStart"]
    assert not (tmp_path / "claude-hooks.json.tmp").exists()


def test_codex_spawn_resume_and_notify_are_structured(tmp_path: Path) -> None:
    adapter = CodexAdapter("codex.exe", notify=True)

    spawned = adapter.spawn_spec("ignored", SpawnOptions(tmp_path, args=["--model", "o3 pro"]))
    resumed = adapter.resume_spec("native-id", SpawnOptions(tmp_path))

    assert spawned.executable == "codex.exe"
    assert spawned.argv[0] == "-c"
    assert spawned.argv[1].startswith("notify=")
    assert spawned.argv[-2:] == ("--model", "o3 pro")
    assert resumed.argv[-2:] == ("resume", "native-id")


def test_codex_without_arguments_has_empty_argv(tmp_path: Path) -> None:
    assert CodexAdapter().spawn_spec("ignored", SpawnOptions(tmp_path)).argv == ()


def test_versioned_transcript_association_contracts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.delenv("CODEX_HOME", raising=False)
    cwd = tmp_path / "project"
    cwd.mkdir()
    claude = ClaudeAdapter()
    claude_path = claude.transcript_path("claude-fixture", cwd)
    claude_path.parent.mkdir(parents=True)
    fixture = Path(__file__).parent / "fixtures" / "transcripts" / "v1"
    claude_path.write_bytes((fixture / "claude.jsonl").read_bytes())

    codex_path = tmp_path / ".codex" / "sessions" / "2026" / "rollout-fixture.jsonl"
    codex_path.parent.mkdir(parents=True)
    lines = (fixture / "codex.jsonl").read_text(encoding="utf-8").splitlines()
    metadata = json.loads(lines[0])
    metadata["payload"]["cwd"] = str(cwd)
    codex_path.write_text("\n".join([json.dumps(metadata), *lines[1:]]) + "\n", encoding="utf-8")

    since = time.time() - 2
    assert claude.recent_transcripts(cwd, since)[0][2] == "claude-fixture"
    codex = CodexAdapter()
    assert codex.recent_transcripts(cwd, since)[0][2] == "codex-fixture"
    assert codex.transcript_native_id(codex_path) == "codex-fixture"


def test_subagent_transcripts_cannot_be_associated_as_root_sessions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.delenv("CODEX_HOME", raising=False)
    cwd = tmp_path / "project"
    cwd.mkdir()
    codex_root = tmp_path / ".codex" / "sessions" / "2026"
    codex_root.mkdir(parents=True)
    child = codex_root / "rollout-child.jsonl"
    child.write_text(
        json.dumps(
            {
                "type": "session_meta",
                "payload": {
                    "id": "child",
                    "cwd": str(cwd),
                    "parent_thread_id": "root",
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )

    codex = CodexAdapter()
    assert codex.transcript_native_id(child) is None
    assert codex.recent_transcripts(cwd, time.time() - 2) == []
    assert inspect_codex(child) is None

    claude_child = tmp_path / ".claude" / "projects" / "project" / "subagents" / "a.jsonl"
    claude_child.parent.mkdir(parents=True)
    claude_child.write_text(
        json.dumps(
            {
                "type": "assistant",
                "isSidechain": True,
                "cwd": str(cwd),
                "sessionId": "child",
                "message": {"content": []},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    assert inspect_claude(claude_child) is None
