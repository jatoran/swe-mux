import asyncio
import json
import os
import shlex
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from swe_mux.adapters import (
    ClaudeAdapter,
    CodexAdapter,
    OmpAdapter,
    ShellAdapter,
    SpawnOptions,
    SpawnSpec,
)
from swe_mux.adapters.claude import _hook_command, encode_cwd
from swe_mux.adapters.codex import CODEX_LIFECYCLE_HOOK_EVENTS, codex_lifecycle_hook_args
from swe_mux.adapters.omp import materialize_mux_extension, omp_hook_path
from swe_mux.reconcile import inspect_claude, inspect_codex


def test_spawn_spec_defaults_are_safe() -> None:
    first = SpawnSpec("shell")
    second = SpawnSpec("shell")

    assert first.argv == ()
    assert first.env == {}
    assert first.env is not second.env


def test_omp_launch_and_resume_specs_preserve_structured_arguments(tmp_path: Path) -> None:
    adapter = OmpAdapter("custom-omp", ["--theme", "dark"])
    options = SpawnOptions(cwd=tmp_path, args=["--model", "fast"])

    assert adapter.spawn_spec("mux-id", options) == SpawnSpec(
        "custom-omp",
        ("--extension", str(omp_hook_path()), "--theme", "dark", "--model", "fast"),
        adapter._terminal_env("mux-id"),
    )
    assert adapter.resume_spec("native-id", options) == SpawnSpec(
        "custom-omp",
        (
            "--extension",
            str(omp_hook_path()),
            "--resume",
            "native-id",
            "--theme",
            "dark",
            "--model",
            "fast",
        ),
        adapter._terminal_env("native-id"),
    )
    assert adapter.graceful_exit_keys() == "\x03\x04"
    assert adapter.reports_conversation_rollover is True
    assert omp_hook_path().is_file()


def test_omp_session_extension_registers_mux_mcp_without_touching_user_config(
    tmp_path: Path,
) -> None:
    adapter = OmpAdapter(
        "omp",
        data_dir=tmp_path / "mux",
        data_home=tmp_path / "omp-home",
        mcp_url="http://127.0.0.1:8765/mcp",
    )
    spec = adapter.spawn_spec(
        "native",
        SpawnOptions(tmp_path, session_id="mux-session", mcp_token="session-secret"),
    )

    extension = Path(spec.argv[spec.argv.index("--extension") + 1])
    assert extension == tmp_path / "mux" / "omp-extensions" / "mux-session"
    assert (extension / "index.ts").read_bytes() == omp_hook_path().read_bytes()
    config = json.loads((extension / ".mcp.json").read_text(encoding="utf-8"))
    server = config["mcpServers"]["mux"]
    assert server == {
        "type": "http",
        "url": "http://127.0.0.1:8765/mcp",
        "headers": {"Authorization": "Bearer session-secret"},
    }
    assert not (tmp_path / "omp-home" / "mcp.json").exists()


def test_omp_mux_extension_does_not_replace_user_extensions(tmp_path: Path) -> None:
    mux_extension = materialize_mux_extension(tmp_path / "mux-extension")
    adapter = OmpAdapter("omp", data_dir=tmp_path / "data")
    spec = adapter.spawn_spec(
        "native",
        SpawnOptions(
            tmp_path,
            args=["--extension", str(tmp_path / "user-extension")],
            session_id="mux-session",
        ),
    )
    argv = list(spec.argv)
    assert argv.count("--extension") == 2
    assert str(tmp_path / "user-extension") in argv
    assert str(mux_extension) not in argv


@pytest.mark.skipif(shutil.which("bun") is None, reason="bun is required to load an OMP extension")
def test_omp_hook_sequences_retries_and_reconnects_without_reordering() -> None:
    hook_uri = json.dumps(omp_hook_path().as_uri())
    script = f"""
      import factory from {hook_uri};
      process.env.MUX_HOOK_URL = "http://127.0.0.1:9/hook";
      process.env.MUX_HOOK_SECRET = "secret";
      const handlers = new Map();
      const bodies = [];
      let calls = 0;
      globalThis.fetch = async (_url, init) => {{
        bodies.push(JSON.parse(String(init.body)));
        calls += 1;
        if (calls === 1) throw new Error("simulated reconnect");
        return new Response("{{}}", {{ status: 200 }});
      }};
      factory({{
        on(name, handler) {{ handlers.set(name, handler); }},
        logger: {{ warn(message) {{ throw new Error(message); }} }},
      }});
      const context = {{
        cwd: "D:/repo",
        sessionManager: {{
          getSessionId() {{ return "native-1"; }},
          getSessionFile() {{ return "D:/sessions/native-1.jsonl"; }},
        }},
      }};
      await handlers.get("session_start")({{ type: "session_start" }}, context);
      await handlers.get("before_agent_start")(
        {{ type: "before_agent_start", prompt: "test" }}, context
      );
      await handlers.get("session_switch")(
        {{ type: "session_switch", reason: "resume" }}, context
      );
      const sequences = bodies.map((body) => body.sequence);
      if (JSON.stringify(sequences) !== JSON.stringify([1, 1, 2, 3])) {{
        throw new Error(`unexpected sequences: ${{JSON.stringify(sequences)}}`);
      }}
      if (!handlers.has("tool_approval_requested") || !handlers.has("tool_approval_resolved")) {{
        throw new Error("approval hooks were not registered");
      }}
    """
    result = subprocess.run(
        [shutil.which("bun") or "bun", "-e", script],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def _write_omp_session(
    path: Path,
    native_id: str,
    cwd: Path,
    *,
    previous: list[Path] | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    title = json.dumps({"type": "title", "v": 1}).encode()
    title_slot = title.ljust(255, b" ") + b"\n"
    header = {
        "type": "session",
        "version": 3,
        "id": native_id,
        "timestamp": "2026-08-06T12:00:00.000Z",
        "cwd": str(cwd),
    }
    if previous:
        header["previousSessionFiles"] = [str(item) for item in previous]
    path.write_bytes(title_slot + json.dumps(header).encode() + b"\n")


def _write_omp_breadcrumb(
    adapter: OmpAdapter,
    session_id: str,
    cwd: Path,
    transcript: Path,
    *,
    fresh: bool = False,
) -> None:
    path = (
        adapter.data_home
        / "terminal-sessions"
        / f"apple-swe-mux-{session_id}"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    suffix = "fresh\n" if fresh else ""
    path.write_text(f"{cwd}\n{transcript}\n{suffix}", encoding="utf-8")


def test_omp_breadcrumb_binds_a_materialized_or_fresh_transcript(tmp_path: Path) -> None:
    home = tmp_path / "omp-home"
    cwd = tmp_path / "repo"
    adapter = OmpAdapter(data_home=home)
    transcript = home / "sessions" / "bucket" / "stamp_native-1.jsonl"

    assert adapter.session_env("mux-1") == {"TERM_SESSION_ID": "swe-mux-mux-1"}
    _write_omp_breadcrumb(adapter, "mux-1", cwd, transcript, fresh=True)
    assert adapter.transcript_path("mux-1", cwd) == transcript
    assert adapter.pending_transcript_path("mux-1", tmp_path / "old.jsonl") == transcript
    assert adapter.transcript_native_id(transcript) == "native-1"

    _write_omp_session(transcript, "native-1", cwd)
    assert adapter.transcript_path("mux-1", cwd) == transcript
    assert adapter.pending_transcript_path("mux-1", tmp_path / "old.jsonl") is None
    assert adapter.transcript_native_id(transcript) == "native-1"


def test_omp_follows_previous_session_file_after_move(tmp_path: Path) -> None:
    home = tmp_path / "omp-home"
    cwd = tmp_path / "repo"
    adapter = OmpAdapter(data_home=home)
    previous = home / "sessions" / "old" / "stamp_native-2.jsonl"
    moved = home / "sessions" / "new" / "stamp_native-2.jsonl"
    _write_omp_session(moved, "native-2", cwd, previous=[previous])
    _write_omp_breadcrumb(adapter, "mux-2", cwd, previous)

    assert adapter.transcript_path("mux-2", cwd) == moved
    assert adapter.locate_transcript("native-2") == moved


def test_omp_recent_transcripts_supports_current_and_hashed_buckets(tmp_path: Path) -> None:
    home = tmp_path / "omp-home"
    cwd = tmp_path / "repo"
    cwd.mkdir()
    adapter = OmpAdapter(data_home=home)
    current_dir, hashed_dir = adapter._candidate_dirs(cwd)
    current = current_dir / "stamp_current.jsonl"
    hashed = hashed_dir / "stamp_hashed.jsonl"
    _write_omp_session(current, "current", cwd)
    _write_omp_session(hashed, "hashed", cwd)

    recent = adapter.recent_transcripts(cwd, time.time())
    assert {(path, native_id) for _, path, native_id in recent} == {
        (current, "current"),
        (hashed, "hashed"),
    }


@pytest.mark.asyncio
async def test_omp_await_transcript_accepts_fresh_unmaterialized_boundary(
    tmp_path: Path,
) -> None:
    home = tmp_path / "omp-home"
    cwd = tmp_path / "repo"
    adapter = OmpAdapter(data_home=home)
    transcript = home / "sessions" / "bucket" / "stamp_native-3.jsonl"
    _write_omp_breadcrumb(adapter, "mux-3", cwd, transcript, fresh=True)

    assert (
        await adapter.await_transcript("mux-3", cwd, time.time(), asyncio.Event())
        == transcript
    )


def test_second_claude_family_adapter_keeps_paths_and_shim_names_isolated(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "mux"
    claude_home = tmp_path / ".claude"
    compatible_home = tmp_path / ".compatible"
    claude = ClaudeAdapter(data_dir=data_dir, data_home_resolver=lambda: claude_home)
    compatible = ClaudeAdapter(
        "compatible.exe",
        data_dir,
        name="compatible",
        config_dir_name=".compatible",
        script_base_name="compatible",
        data_home_resolver=lambda: compatible_home,
    )

    assert claude.settings_path == data_dir / "claude-hooks.json"
    assert compatible.settings_path == data_dir / "compatible-hooks.json"
    assert claude.shim_name == "claude.cmd"
    assert compatible.shim_name == "compatible.cmd"
    assert claude.transcript_path("same", tmp_path).is_relative_to(claude_home)
    assert compatible.transcript_path("same", tmp_path).is_relative_to(compatible_home)


def test_codex_family_uses_injected_data_home_and_rollout_prefix(tmp_path: Path) -> None:
    cwd = tmp_path / "repo"
    cwd.mkdir()
    rollout = tmp_path / "custom-home" / "sessions" / "2026" / "custom-thread-id.jsonl"
    rollout.parent.mkdir(parents=True)
    rollout.write_text(
        json.dumps(
            {
                "type": "session_meta",
                "payload": {"id": "thread-id", "cwd": str(cwd)},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    adapter = CodexAdapter(
        data_home_resolver=lambda: tmp_path / "custom-home",
        rollout_file_prefix="custom-",
    )

    assert adapter.transcript_path("thread-id", cwd) == rollout


def test_locate_transcript_finds_a_conversation_whose_cwd_moved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # `transcript_path` answers "where would this conversation live if the CLI were
    # standing in `cwd`", which stops being true the moment it moves: entering a
    # native worktree relocates the whole file into the new directory's slug.
    # Searching by conversation id is safe where an mtime scan is not, because mux
    # dictates that id at spawn - a file named after it is this session's by
    # construction.
    monkeypatch.setattr("swe_mux.adapters.claude.claude_data_home", lambda: tmp_path)
    native = "3763350c-df0c-4f85-9e93-7f73ffba2c07"
    spawn_root = tmp_path / "projects" / encode_cwd(tmp_path / "repo")
    worktree_root = tmp_path / "projects" / encode_cwd(tmp_path / "repo" / "wt")
    for root in (spawn_root, worktree_root):
        root.mkdir(parents=True)
    (spawn_root / "unrelated.jsonl").write_text("{}\n", encoding="utf-8")
    moved = worktree_root / f"{native}.jsonl"
    moved.write_text("{}\n", encoding="utf-8")
    adapter = ClaudeAdapter()

    assert adapter.transcript_path(native, tmp_path / "repo") == spawn_root / f"{native}.jsonl"
    assert adapter.locate_transcript(native) == moved
    assert adapter.locate_transcript("00000000-0000-4000-8000-000000000000") is None
    assert adapter.locate_transcript("") is None


def test_locate_transcript_prefers_the_longest_of_two_same_named_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Claude moves the file rather than copying it, so two should never coexist. If
    # they ever do, the live one is the longest - a conversation only grows.
    # Deliberately not the newest mtime: Windows leaves an open file's timestamp
    # frozen at creation, so "newest" can name the dead one.
    monkeypatch.setattr("swe_mux.adapters.claude.claude_data_home", lambda: tmp_path)
    native = "3763350c-df0c-4f85-9e93-7f73ffba2c07"
    stale = tmp_path / "projects" / "a-slug"
    live = tmp_path / "projects" / "b-slug"
    for root in (stale, live):
        root.mkdir(parents=True)
    (stale / f"{native}.jsonl").write_text("{}\n", encoding="utf-8")
    longest = live / f"{native}.jsonl"
    longest.write_text('{"one":1}\n{"two":2}\n', encoding="utf-8")
    os.utime(longest, (0, 0))

    assert ClaudeAdapter().locate_transcript(native) == longest


def test_only_a_cwd_resolved_backend_can_relocate_a_transcript() -> None:
    # The flag gates the hook-reported relocation path. Codex addresses a rollout by
    # thread id in a date tree, so its file never moves when the pane's cwd does,
    # and a differing path from that backend is a report about another conversation.
    assert ClaudeAdapter().resolves_transcript_by_cwd is True
    assert CodexAdapter().resolves_transcript_by_cwd is False
    assert ShellAdapter().resolves_transcript_by_cwd is False
    assert ShellAdapter().locate_transcript("anything") is None


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


def test_claude_mcp_config_never_directly_precedes_positional_args(tmp_path: Path) -> None:
    # `--mcp-config` is variadic in the Claude CLI: it keeps consuming tokens
    # until the next flag, so a trailing seed prompt placed right after it is
    # read as a second config path and the CLI exits ("MCP config file not
    # found: <prompt>"). Discovered live during Phase 4 seed verification.
    adapter = ClaudeAdapter(data_dir=tmp_path, mcp_url="http://127.0.0.1:1/mcp")
    spec = adapter.spawn_spec(
        "new-id", SpawnOptions(tmp_path, args=["Reply with the single word OK."])
    )
    argv = list(spec.argv)
    assert "--mcp-config" in argv and argv[-1] == "Reply with the single word OK."
    following = argv[argv.index("--mcp-config") + 2]
    assert following.startswith("--"), argv


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
    hook_values = [value for value in spawned.argv if value.startswith("hooks.")]
    assert len(hook_values) == 9
    assert any(value.startswith("hooks.SessionStart=") for value in hook_values)
    assert any(value.startswith("hooks.UserPromptSubmit=") for value in hook_values)
    assert all(
        "command_windows" in value and "swe_mux.hook_client" in value for value in hook_values
    )
    assert 'tui.alternate_screen="never"' in spawned.argv
    assert not any("tui.raw_output_mode" in value for value in spawned.argv)
    assert spawned.argv[-2:] == ("--model", "o3 pro")
    assert resumed.argv[-2:] == ("resume", "native-id")


def test_codex_lifecycle_hooks_are_stable_and_preserve_explicit_event_config() -> None:
    first = codex_lifecycle_hook_args()
    second = codex_lifecycle_hook_args()

    assert first == second
    assert len(first) == len(CODEX_LIFECYCLE_HOOK_EVENTS) * 2
    assert all(first[index] == "-c" for index in range(0, len(first), 2))

    explicit = codex_lifecycle_hook_args(["-c", "hooks.SessionStart=[]"])
    values = explicit[1::2]
    assert not any(value.startswith("hooks.SessionStart=") for value in values)
    assert any(value.startswith("hooks.UserPromptSubmit=") for value in values)


def test_codex_defaults_to_scrollback_safe_tui(tmp_path: Path) -> None:
    assert CodexAdapter().spawn_spec("ignored", SpawnOptions(tmp_path)).argv == (
        "-c",
        'tui.alternate_screen="never"',
    )


def test_codex_leaves_raw_output_mode_to_the_cli(tmp_path: Path) -> None:
    """Mux pins the screen buffer, never the transcript renderer.

    Forcing `tui.raw_output_mode=true` cost Codex panes their colour, their tool-output
    folding, and any visual break between working and answering, for no behaviour mux
    reads. Nothing about the scrollback contract needs it, so a user who wants raw
    output asks the CLI for it.
    """
    spec = CodexAdapter().spawn_spec("ignored", SpawnOptions(tmp_path))

    assert not any("tui.raw_output_mode" in value for value in spec.argv)


def test_codex_explicit_tui_config_overrides_scrollback_defaults(tmp_path: Path) -> None:
    adapter = CodexAdapter(
        default_args=["--config", 'tui.alternate_screen="always"'],
    )

    spec = adapter.spawn_spec(
        "ignored",
        SpawnOptions(tmp_path, args=["--config=tui.raw_output_mode=true"]),
    )

    assert spec.argv == (
        "--config",
        'tui.alternate_screen="always"',
        "--config=tui.raw_output_mode=true",
    )


def test_codex_command_resolver_prepends_a_conpty_safe_launcher(tmp_path: Path) -> None:
    adapter = CodexAdapter(
        "codex.exe",
        notify=True,
        command_resolver=lambda _command: ("node.exe", (r"C:\npm\codex.js",)),
    )

    spec = adapter.spawn_spec("ignored", SpawnOptions(tmp_path))

    assert spec.executable == "node.exe"
    assert spec.argv[0] == r"C:\npm\codex.js"
    assert spec.argv[1] == "-c"
    assert spec.argv[2].startswith("notify=")


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


def _rollout(root: Path, name: str, payload: dict[str, object]) -> Path:
    path = root / f"rollout-{name}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"type": "session_meta", "payload": payload}) + "\n", encoding="utf-8"
    )
    return path


def test_a_resumed_codex_conversation_is_found_by_id_not_recency(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`codex resume` appends to the rollout its thread id already owns.

    So a resumed pane's transcript is a file older than the pane, which the recency
    window cannot see — and on Windows a live rollout's mtime can stay frozen at
    creation while it grows, so waiting for recency to catch up is not a fallback
    either. That combination left every resumed Codex pane unobserved.
    """
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    cwd = tmp_path / "project"
    cwd.mkdir()
    sessions = tmp_path / "codex" / "sessions" / "2026" / "08" / "03"
    resumed = _rollout(
        sessions, "2026-08-03T18-54-12-019fca0c", {"id": "019fca0c", "cwd": str(cwd)}
    )
    child = _rollout(
        sessions,
        "2026-08-03T19-02-40-019fca77",
        {"id": "019fca77", "cwd": str(cwd), "parent_thread_id": "019fca0c"},
    )
    yesterday = time.time() - 86_400
    for path in (resumed, child):
        os.utime(path, (yesterday, yesterday))
    adapter = CodexAdapter()

    assert adapter.recent_transcripts(cwd, time.time()) == []
    assert adapter.transcript_path("019fca0c", cwd) == resumed
    # A fresh pane carries the mux id as a placeholder: no rollout answers to it,
    # and a child thread is not a root conversation.
    assert adapter.transcript_path("11111111-2222-4333-8444-555555555555", cwd) is None
    assert adapter.transcript_path("019fca77", cwd) is None


def test_resume_continuation_follows_each_clis_own_transcript_rule(tmp_path: Path) -> None:
    """Whether a resume continues the conversation is the adapter's answer.

    Claude resolves transcripts through the encoded working directory, so a resume
    under another root writes a different file: a different conversation. Codex
    resolves by thread id and reopens the same rollout wherever the pane runs.
    """
    root = tmp_path / "project"
    other = tmp_path / "elsewhere"
    root.mkdir()
    other.mkdir()
    claude = ClaudeAdapter("claude.exe")

    assert claude.resume_continues_conversation(str(root), str(root)) is True
    assert claude.resume_continues_conversation(str(root), str(other)) is False
    if os.path.normcase("A") == "a":
        # Where the platform folds case, both spellings name one transcript
        # directory, so refusing to continue there would fork on a spelling.
        assert claude.resume_continues_conversation(str(root), str(root).upper()) is True
    assert CodexAdapter().resume_continues_conversation(str(root), str(other)) is True
    assert ShellAdapter().resume_continues_conversation(str(root), str(root)) is False
