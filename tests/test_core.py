from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

import swe_mux.pty_host as pty_host_module
from swe_mux import agent_launcher
from swe_mux.adapters import ClaudeAdapter, ShellAdapter, SpawnOptions
from swe_mux.agent_launcher import _claude, _codex
from swe_mux.config import Config
from swe_mux.event_bus import EventBus
from swe_mux.history import HistoryIndex
from swe_mux.launchers import create_agent_shims, resolve_codex_pty_command, resolve_command
from swe_mux.models import ProjectRecord, SessionRecord
from swe_mux.pty_host import PtyHost, create_pty, merge_environment
from swe_mux.reconcile import reconcile_external_history
from swe_mux.server import (
    SESSION_MEDIA_TTL_SECONDS,
    cleanup_expired_session_media,
    hook_event_payload,
    session_media_directory,
    validate_session_media,
)
from swe_mux.session import terminal_exit_outcome
from swe_mux.shim_paths import is_mux_shim, path_without_shim_dirs
from swe_mux.transcript_view import parse_transcript


def test_adapters_keep_executable_and_arguments_structured(tmp_path: Path) -> None:
    opts = SpawnOptions(tmp_path, args=["--dangerously-skip-permissions"])
    spec = ClaudeAdapter("claude.exe").spawn_spec("native-id", opts)
    assert spec.executable == "claude.exe"
    assert spec.argv == ("--session-id", "native-id", "--dangerously-skip-permissions")

    shell_spec = ShellAdapter("powershell.exe").spawn_spec("ignored", SpawnOptions(tmp_path))
    assert shell_spec.executable == "powershell.exe"
    assert shell_spec.argv == ()


def test_pty_host_reports_root_exit_status_only_after_exit() -> None:
    class FakePty:
        def __init__(self, alive: bool, status: int) -> None:
            self.alive = alive
            self.status = status
            self.cancelled = False

        def isalive(self) -> bool:
            return self.alive

        def get_exitstatus(self) -> int:
            return self.status

        def cancel_io(self) -> None:
            self.cancelled = True

    host = PtyHost("cmd.exe", [])
    host._pty = FakePty(True, 7)  # type: ignore[assignment]
    assert host.exit_status() is None
    with pytest.raises(RuntimeError, match="live pseudoconsole"):
        host.release()
    ended = FakePty(False, 7)
    host._pty = ended  # type: ignore[assignment]
    assert host.exit_status() == 7
    host.release()
    assert ended.cancelled
    assert host._pty is None


def test_pty_host_reaps_a_delayed_replacement_console_host(monkeypatch: pytest.MonkeyPatch) -> None:
    host = PtyHost("cmd.exe", [])
    host._root_started_at = 100.0
    host._console_host_pid = 10
    host._console_host_started_at = 100.0
    killed: list[tuple[int, float]] = []

    def kill_console_host(pid: int, started_at: float) -> bool:
        killed.append((pid, started_at))
        return pid == 20

    monkeypatch.setattr(host, "_kill_console_host", kill_console_host)
    monkeypatch.setattr(pty_host_module, "_console_host_children", lambda: {20: 100.1})
    try:
        host._reap_console_host()
    finally:
        pty_host_module._CLAIMED_CONSOLE_HOSTS.clear()

    assert killed == [(10, 100.0), (20, 100.1)]


def test_one_shot_terminal_exit_outcomes_preserve_failures() -> None:
    assert terminal_exit_outcome(
        "one_shot", stopping=False, exit_code=0, reason="process_exit"
    ) == ("exited", "completed", "exit code 0")
    assert terminal_exit_outcome(
        "one_shot", stopping=False, exit_code=7, reason="process_exit"
    ) == ("crashed", "process_exit", "exit code 7")
    # Quitting an interactive agent is a clean or interrupt-driven exit, not a
    # crash: double Ctrl+C / clean quit (0), POSIX interrupt (130), and Windows
    # STATUS_CONTROL_C_EXIT all resolve to "exited".
    for clean_code in (0, 130, 0xC000013A, None):
        assert terminal_exit_outcome(
            "interactive", stopping=False, exit_code=clean_code, reason="process_exit"
        ) == ("exited", "process_exit", None)
    # A genuine abnormal exit code still surfaces as a crash.
    assert terminal_exit_outcome(
        "interactive", stopping=False, exit_code=1, reason="process_exit"
    ) == ("crashed", "process_exit", None)


async def test_history_and_event_bus_persist_contract(tmp_path: Path) -> None:
    history = HistoryIndex(tmp_path / "mux.db")
    project = ProjectRecord("default", "Main", str(tmp_path), 0)
    await history.upsert_project(project)
    assert [item.id for item in await history.list_projects()] == ["default"]

    session = SessionRecord(
        "mux-id",
        "test",
        "default",
        "claude",
        "native-id",
        str(tmp_path),
        "claude.exe",
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
    # The shims read the spawning session's environment; running this suite
    # inside a mux session would otherwise leak its MCP identity/args in and
    # prepend --mcp-config / mcp_servers overrides to the assertions below.
    for leaked in (
        "MUX_MCP_TOKEN",
        "MUX_MCP_URL",
        "MUX_CLAUDE_MCP_CONFIG",
        "MUX_CLAUDE_ARGS",
        "MUX_CODEX_ARGS",
    ):
        monkeypatch.delenv(leaked, raising=False)
    exe, args, native_id = _claude([])
    assert exe == "real-claude.exe"
    assert args[:2] == ["--settings", str(tmp_path / "hooks.json")]
    assert "--session-id" in args
    assert native_id

    exe, args, native_id = _codex(["resume", "codex-native"])
    assert exe == "real-codex.exe"
    assert args[:2] == ["-c", args[1]]
    assert "notify=" in args[1]
    assert args[2:6] == [
        "-c",
        'tui.alternate_screen="never"',
        "-c",
        "tui.raw_output_mode=true",
    ]
    assert args[-2:] == ["resume", "codex-native"]
    assert native_id == "codex-native"

    cfg = Config(data_dir=tmp_path)
    env = create_agent_shims(cfg, tmp_path / "hooks.json")
    assert (tmp_path / "bin" / "claude.cmd").is_file()
    assert env["PATH"].startswith(str(tmp_path / "bin"))
    assert env["MUX_SHIM_DIR"] == str(tmp_path / "bin")


@pytest.mark.parametrize(
    "argv",
    [
        ["--continue"],
        ["-c"],
        ["--resume"],
        ["-r"],
        ["-r", "some search term"],
        ["--continue", "--fork-session"],
    ],
)
def test_claude_shim_never_injects_a_session_id_over_a_resume(argv: list[str]) -> None:
    # The CLI hard-rejects `--session-id` alongside continue/resume, so injecting
    # one turns an ordinary `claude --continue` into an exit-1 launch. And a
    # value-less `--resume` (or `-r <search term>`) has no id to capture: reading
    # the next token blindly filed a flag or a prompt as the conversation id.
    _, args, native_id = _claude(list(argv))
    assert "--session-id" not in args
    assert native_id == ""


def test_claude_shim_captures_an_explicit_resume_conversation_id() -> None:
    native = "0f9c8b7a-1234-4321-8888-abcdefabcdef"
    for flag in ("--resume", "-r"):
        _, args, native_id = _claude([flag, native])
        assert native_id == native
        assert "--session-id" not in args


def test_agent_launcher_demotes_terminal_when_agent_exits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, str] | tuple[str, list[str]]] = []
    monkeypatch.setattr(sys, "argv", ["launcher", "claude"])
    monkeypatch.setattr(
        agent_launcher,
        "_claude",
        lambda args: ("claude.exe", ["--session-id", "native"], "native"),
    )
    monkeypatch.setattr(
        agent_launcher,
        "_promote",
        lambda backend, native_id: calls.append(("promote", backend, native_id)),
    )
    monkeypatch.setattr(
        agent_launcher,
        "_demote",
        lambda backend, native_id: calls.append(("demote", backend, native_id)),
    )
    monkeypatch.setattr(
        agent_launcher.subprocess,
        "call",
        lambda command: calls.append(("exec", command)) or 7,
    )
    monkeypatch.setattr(agent_launcher.shutil, "which", lambda command: command)

    with pytest.raises(SystemExit, match="7"):
        agent_launcher.main()

    assert calls == [
        ("promote", "claude", "native"),
        ("exec", ["claude.exe", "--session-id", "native"]),
        ("demote", "claude", "native"),
    ]


def test_command_resolution_falls_back_from_exe_to_windows_shim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "swe_mux.launchers.shutil.which",
        lambda command, path=None: r"C:\npm\codex.cmd" if command == "codex" else None,
    )
    assert resolve_command("codex.exe") == r"C:\npm\codex.cmd"


def test_codex_pty_resolution_bypasses_the_npm_batch_shim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    npm = tmp_path / "npm"
    shim = npm / "codex.cmd"
    script = npm / "node_modules" / "@openai" / "codex" / "bin" / "codex.js"
    node = npm / "node.exe"
    script.parent.mkdir(parents=True)
    shim.write_text("@echo off", encoding="utf-8")
    script.write_text("// fixture", encoding="utf-8")
    node.write_bytes(b"fixture")
    monkeypatch.setattr(
        "swe_mux.launchers.shutil.which",
        lambda command, path=None: str(shim) if command == "codex" else None,
    )

    assert resolve_codex_pty_command("codex.exe", windows=True) == (
        str(node),
        (str(script),),
    )


def test_agent_launcher_runs_batch_commands_through_comspec(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []
    monkeypatch.setenv("COMSPEC", r"C:\Windows\System32\cmd.exe")
    monkeypatch.setattr(
        agent_launcher.subprocess, "call", lambda command: calls.append(command) or 0
    )

    assert agent_launcher._launch(r"C:\npm\codex.cmd", ["--version"]) == 0
    assert calls[0][:4] == [r"C:\Windows\System32\cmd.exe", "/d", "/s", "/c"]
    assert "codex.cmd" in calls[0][4]


def test_agent_launcher_bypasses_npm_batch_for_structured_codex_args(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    npm = tmp_path / "npm"
    shim = npm / "codex.cmd"
    script = npm / "node_modules" / "@openai" / "codex" / "bin" / "codex.js"
    node = npm / "node.exe"
    script.parent.mkdir(parents=True)
    shim.write_text("@echo off", encoding="utf-8")
    script.write_text("// fixture", encoding="utf-8")
    node.write_bytes(b"fixture")
    calls: list[list[str]] = []
    monkeypatch.setattr(
        agent_launcher.subprocess, "call", lambda command: calls.append(command) or 0
    )

    notify = 'notify=["python.exe", "-m", "swe_mux.hook_client"]'
    assert agent_launcher._launch(str(shim), ["-c", notify]) == 0
    assert calls == [[str(node), str(script), "-c", notify]]


def _write_mux_shim(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    shim = directory / "codex.cmd"
    shim.write_text(
        '@echo off\r\n"swe-mux.exe" -m swe_mux.agent_launcher codex %*\r\n', encoding="utf-8"
    )
    return shim


def test_shim_detection_filters_only_mux_shim_directories(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mux_shim = _write_mux_shim(tmp_path / "mux-bin")
    npm = tmp_path / "npm"
    npm.mkdir()
    npm_shim = npm / "codex.cmd"
    npm_shim.write_text("@echo off", encoding="utf-8")
    monkeypatch.delenv("MUX_SHIM_DIR", raising=False)

    assert is_mux_shim(mux_shim)
    assert not is_mux_shim(npm_shim)
    joined = f"{mux_shim.parent}{os.pathsep}{npm}"
    assert path_without_shim_dirs(joined) == str(npm)


def test_agent_launcher_escapes_a_poisoned_shim_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # MUX_CODEX_EXE ends up pointing at the mux's own shim when the daemon that
    # wired it had ~/.mux/bin on PATH; launching must re-resolve to the real
    # CLI instead of recursing shim -> swe-mux.exe -> shim.
    mux_shim = _write_mux_shim(tmp_path / "mux-bin")
    npm = tmp_path / "npm"
    real = npm / "codex.cmd"
    script = npm / "node_modules" / "@openai" / "codex" / "bin" / "codex.js"
    node = npm / "node.exe"
    script.parent.mkdir(parents=True)
    real.write_text("@echo off", encoding="utf-8")
    script.write_text("// fixture", encoding="utf-8")
    node.write_bytes(b"fixture")
    monkeypatch.delenv("MUX_SHIM_DIR", raising=False)
    monkeypatch.setenv("PATH", f"{mux_shim.parent}{os.pathsep}{npm}")
    searched: list[str | None] = []

    def fake_which(command: str, path: str | None = None) -> str | None:
        searched.append(path)
        if path is None and command == str(mux_shim):
            return str(mux_shim)
        if command == "codex" and path is not None and str(mux_shim.parent) not in path:
            return str(real)
        return None

    monkeypatch.setattr(agent_launcher.shutil, "which", fake_which)
    calls: list[list[str]] = []
    monkeypatch.setattr(
        agent_launcher.subprocess, "call", lambda command: calls.append(command) or 0
    )

    assert agent_launcher._launch(str(mux_shim), ["--version"]) == 0
    assert calls == [[str(node), str(script), "--version"]]


def test_agent_launcher_refuses_the_shim_when_no_real_cli_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mux_shim = _write_mux_shim(tmp_path / "mux-bin")
    monkeypatch.delenv("MUX_SHIM_DIR", raising=False)
    monkeypatch.setenv("PATH", str(mux_shim.parent))
    monkeypatch.setattr(
        agent_launcher.shutil,
        "which",
        lambda command, path=None: str(mux_shim) if path is None else None,
    )
    monkeypatch.setattr(
        agent_launcher.subprocess,
        "call",
        lambda command: pytest.fail("must not spawn the mux shim"),
    )

    with pytest.raises(SystemExit, match="refusing to relaunch the mux shim"):
        agent_launcher._launch(str(mux_shim), ["login"])


async def test_reap_process_tree_kills_grandchildren_and_never_hangs(tmp_path: Path) -> None:
    # The deadlock shape: cmd.exe wrapper -> long-lived worker inheriting our
    # pipes. Killing only cmd leaves the worker holding stdout open, and a bare
    # process.wait() never returns. reap_process_tree must take the whole tree
    # down and return promptly.
    import psutil

    from swe_mux.subprocess_flags import background_creation_flags, reap_process_tree

    script = tmp_path / "sleeper.py"
    script.write_text("import time\ntime.sleep(60)\n", encoding="utf-8")
    if os.name == "nt":
        command = [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", sys.executable, str(script)]
    else:
        command = ["/bin/sh", "-c", f'"{sys.executable}" "{script}"']
    process = await asyncio.create_subprocess_exec(
        *command,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        creationflags=background_creation_flags(),
    )
    descendants: list[psutil.Process] = []
    for _ in range(100):
        try:
            descendants = psutil.Process(process.pid).children(recursive=True)
        except psutil.NoSuchProcess:
            break
        if any("python" in child.name().casefold() for child in descendants):
            break
        await asyncio.sleep(0.1)
    assert any("python" in child.name().casefold() for child in descendants)

    await asyncio.wait_for(reap_process_tree(process, timeout_seconds=10), 20)

    assert process.returncode is not None
    _gone, alive = psutil.wait_procs(descendants, timeout=5)
    assert not alive


def test_agent_shim_env_strips_inherited_shim_directories(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stale = _write_mux_shim(tmp_path / "stale-bin").parent
    other = tmp_path / "other"
    other.mkdir()
    cfg = Config(data_dir=tmp_path)
    bin_dir = tmp_path / "bin"
    sep = os.pathsep
    monkeypatch.delenv("MUX_SHIM_DIR", raising=False)
    monkeypatch.setenv("PATH", f"{bin_dir}{sep}{stale}{sep}{other}")

    env = create_agent_shims(cfg, None)
    assert env["PATH"] == f"{bin_dir}{sep}{other}"


def test_windows_environment_override_is_case_insensitive() -> None:
    merged = merge_environment(
        {"Path": r"C:\Windows", "TEMP": r"C:\Temp"},
        {"PATH": r"C:\mux\bin;C:\Windows", "Mux_Session_Id": "one"},
    )
    assert merged["PATH"].startswith(r"C:\mux\bin")
    assert "Path" not in merged
    assert len([key for key in merged if key.casefold() == "path"]) == 1


def test_conpty_creation_retries_only_the_private_pyo3_panic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class PanicException(BaseException):
        pass

    PanicException.__module__ = "pyo3_runtime"
    sentinel = object()
    attempts = 0

    def create(**_kwargs: int) -> object:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise PanicException("transient ConPTY initialization failure")
        return sentinel

    monkeypatch.setattr("swe_mux.pty_host.winpty.PTY", create)
    monkeypatch.setattr("swe_mux.pty_host.time.sleep", lambda _seconds: None)

    assert create_pty(120, 30) is sentinel
    assert attempts == 2


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


def test_clipboard_media_validation_is_typed_and_signature_checked() -> None:
    assert validate_session_media("image/png", b"\x89PNG\r\n\x1a\ncontent") == ".png"
    assert validate_session_media("image/webp", b"RIFF0000WEBPcontent") == ".webp"
    with pytest.raises(ValueError, match="supported clipboard image types"):
        validate_session_media("image/svg+xml", b"<svg/>")
    with pytest.raises(ValueError, match="does not match"):
        validate_session_media("image/png", b"not-png")


def test_clipboard_media_directory_cannot_escape_its_session(tmp_path: Path) -> None:
    directory = session_media_directory(tmp_path, "session-a")
    assert directory == (tmp_path / "media" / "session-a").resolve()
    with pytest.raises(ValueError, match="media identity"):
        session_media_directory(tmp_path, "../another-session")


def test_clipboard_media_cleanup_removes_only_expired_session_files(tmp_path: Path) -> None:
    import os

    directory = session_media_directory(tmp_path, "session-a")
    directory.mkdir(parents=True)
    expired = directory / "expired.png"
    current = directory / "current.png"
    expired.write_bytes(b"old")
    current.write_bytes(b"new")
    now = 2_000_000_000.0
    os.utime(expired, (now - SESSION_MEDIA_TTL_SECONDS - 1,) * 2)
    os.utime(current, (now,) * 2)

    assert cleanup_expired_session_media(tmp_path, now) == 1
    assert not expired.exists()
    assert current.exists()


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
