from __future__ import annotations

import json
from pathlib import Path

from swe_mux.adapters import build_agent_adapter
from swe_mux.adapters.base import SpawnOptions
from swe_mux.adapters.omp import session_header
from swe_mux.adapters.opencode import OpenCodeAdapter, materialize_mux_config
from swe_mux.adapters.pi import PiAdapter, parent_session_path, pi_session_dir_name
from swe_mux.harness import descriptor

PI_HEADER = (
    '{"type":"session","version":3,"id":"019fed18-c5e5-7a7e-89e0-bc5d96525bdd",'
    '"timestamp":"2026-08-10T19:14:11.557Z","cwd":"D:\\\\PROJECTS\\\\demo"}'
)


def test_pi_session_dir_encoding_matches_the_installed_cli() -> None:
    """Pinned to pi 0.74.2's observed output on Windows (measured 2026-08-10).

    pi always wraps the whole absolute path, never a home-relative short form.
    Getting this wrong does not raise: it silently looks in a directory that
    does not exist, so every pi session started under $HOME would go unbound.
    """
    assert (
        pi_session_dir_name(Path("D:/PROJECTS/swe-mux/.claude/worktrees/harness-pi-opencode"))
        == "--D--PROJECTS-swe-mux-.claude-worktrees-harness-pi-opencode--"
    )


def test_pi_encoding_does_not_shorten_paths_under_home() -> None:
    """The measured divergence from oh-my-pi.

    omp buckets a home-relative directory under a `-<relative>` scope name. pi
    does not, so the omp encoder cannot be reused here.
    """
    encoded = pi_session_dir_name(Path.home() / ".mux-probe" / "demo")
    assert encoded.startswith("--")
    assert encoded.endswith("--")
    assert ".mux-probe-demo" in encoded
    assert encoded != "-.mux-probe-demo"


def test_pi_reads_a_header_without_ompsy_title_slot(tmp_path: Path) -> None:
    path = tmp_path / "2026-08-10T19-14-11-557Z_019fed18.jsonl"
    path.write_text(PI_HEADER + "\n", encoding="utf-8")
    header = session_header(path)
    assert header is not None
    assert header["id"] == "019fed18-c5e5-7a7e-89e0-bc5d96525bdd"
    assert PiAdapter().transcript_native_id(path) == header["id"]


def test_pi_native_id_falls_back_to_the_file_name(tmp_path: Path) -> None:
    """A file whose header is not yet flushed is still strongly identified."""
    path = tmp_path / "2026-08-10T19-14-11-557Z_019fed18-c5e5.jsonl"
    path.write_text("", encoding="utf-8")
    assert PiAdapter().transcript_native_id(path) == "019fed18-c5e5"


def test_pi_parent_session_is_a_single_path_not_ompsy_chain() -> None:
    assert parent_session_path({"parentSession": "/tmp/prior.jsonl"}) == Path("/tmp/prior.jsonl")
    assert parent_session_path({"previousSessionFiles": ["/tmp/prior.jsonl"]}) is None
    assert parent_session_path(None) is None


def test_pi_resume_uses_session_not_the_interactive_picker(tmp_path: Path) -> None:
    """`--resume` is pi's interactive picker and would hang a mux pane."""
    adapter = PiAdapter(data_home=tmp_path)
    spec = adapter.resume_spec("abc123", SpawnOptions(cwd=tmp_path))
    assert "--session" in spec.argv
    assert spec.argv[spec.argv.index("--session") + 1] == "abc123"
    assert "--resume" not in spec.argv


def test_pi_spawn_injects_the_extension_once(tmp_path: Path) -> None:
    adapter = PiAdapter(data_home=tmp_path)
    spec = adapter.spawn_spec("sid", SpawnOptions(cwd=tmp_path))
    assert spec.argv[0] == "--extension"
    assert spec.argv[1].endswith("pi_mux_hook.ts")
    repeated = adapter.spawn_spec("sid", SpawnOptions(cwd=tmp_path, args=list(spec.argv)))
    assert repeated.argv.count("--extension") == 1


def test_pi_sends_no_omp_display_knobs(tmp_path: Path) -> None:
    """pi 0.74.2 reads none of omp's `PI_*` tuning; sending it would be cargo cult."""
    adapter = PiAdapter(data_home=tmp_path)
    assert adapter.session_env("sid") == {}
    assert adapter.spawn_spec("sid", SpawnOptions(cwd=tmp_path)).env == {}


def test_pi_unknown_model_window_is_zero_not_a_guess(tmp_path: Path) -> None:
    adapter = PiAdapter("nonexistent-pi", data_home=tmp_path)
    assert adapter.model_context_window("openrouter", "who/knows") == 0


def _pi_catalog(root: Path) -> Path:
    """Lay out an installed pi's bundled pi-ai catalog under a fake npm prefix."""
    pkg = root / "node_modules" / "@earendil-works" / "pi-coding-agent"
    dist = pkg / "node_modules" / "@earendil-works" / "pi-ai" / "dist"
    dist.mkdir(parents=True)
    return dist


def test_pi_reads_the_json_catalog_layout(tmp_path: Path) -> None:
    """pi >= 0.75 ships per-provider JSON; models.generated.js is only imports.

    The layout changed between 0.74.2 and 0.84.1 and the old JS-regex reader
    silently produced an empty catalog, which renders as 0% context used - which
    the codebase already documents as indistinguishable from a fresh
    conversation rather than obviously broken.
    """
    dist = _pi_catalog(tmp_path)
    (dist / "models.generated.js").write_text(
        'import { OPENAI_MODELS } from "./providers/openai.models.js";\n', encoding="utf-8"
    )
    data = dist / "providers" / "data"
    data.mkdir(parents=True)
    (data / "openai.json").write_text(
        json.dumps(
            {
                "openai-responses": {
                    "gpt-x": {
                        "id": "gpt-x",
                        "provider": "openai",
                        "contextWindow": 272000,
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    adapter = PiAdapter(str(tmp_path / "pi.cmd"), data_home=tmp_path)
    assert adapter.model_context_window("openai", "gpt-x") == 272000


def test_pi_reads_the_legacy_js_catalog_layout(tmp_path: Path) -> None:
    """pi <= 0.74 kept the whole catalog in one generated JS object literal."""
    dist = _pi_catalog(tmp_path)
    (dist / "models.generated.js").write_text(
        'export const MODELS = {\n'
        '  "openai": {\n'
        '    "gpt-old": {\n'
        '      id: "gpt-old",\n'
        '      provider: "openai",\n'
        "      cost: { input: 1 },\n"
        "      contextWindow: 128000,\n"
        "    },\n"
        "  },\n"
        "};\n",
        encoding="utf-8",
    )
    adapter = PiAdapter(str(tmp_path / "pi.cmd"), data_home=tmp_path)
    assert adapter.model_context_window("openai", "gpt-old") == 128000


def test_pi_model_window_falls_back_to_the_id_when_providers_agree(tmp_path: Path) -> None:
    """A subscription-backed provider id the catalog never heard of still resolves."""
    dist = _pi_catalog(tmp_path)
    (dist / "models.generated.js").write_text("", encoding="utf-8")
    data = dist / "providers" / "data"
    data.mkdir(parents=True)
    (data / "a.json").write_text(
        json.dumps(
            {
                "g": {
                    "solo": {"id": "solo", "provider": "one", "contextWindow": 100},
                    "split": {"id": "split", "provider": "one", "contextWindow": 100},
                }
            }
        ),
        encoding="utf-8",
    )
    (data / "b.json").write_text(
        json.dumps({"g": {"split": {"id": "split", "provider": "two", "contextWindow": 900}}}),
        encoding="utf-8",
    )
    adapter = PiAdapter(str(tmp_path / "pi.cmd"), data_home=tmp_path)
    # One provider offers it, so the id alone identifies the window.
    assert adapter.model_context_window("never-heard-of-it", "solo") == 100
    # Providers disagree, so refuse rather than render a confidently wrong
    # percentage - the live catalog really does disagree by 4x on gpt-5.6-sol.
    assert adapter.model_context_window("never-heard-of-it", "split") == 0


def test_opencode_config_adds_a_layer_and_never_replaces_the_config_dir(
    tmp_path: Path,
) -> None:
    """The plugin arrives through OPENCODE_CONFIG, which opencode *merges*.

    `OPENCODE_CONFIG_DIR` would replace the user's config directory, hiding
    their agents, commands, and auth behind a mirror mux would have to maintain.
    """
    plugin = tmp_path / "plugin.js"
    plugin.write_text("export const MuxHook = async () => ({})\n", encoding="utf-8")
    config = materialize_mux_config(tmp_path / "session", plugin)

    payload = json.loads(config.read_text(encoding="utf-8"))
    assert list(payload["plugin"]) == [(tmp_path / "session" / "mux-plugin.js").as_posix()]
    # Forward slashes only: the value is a module specifier, and a Windows
    # backslash would be eaten as a JSON escape.
    assert "\\" not in payload["plugin"][0]
    assert (tmp_path / "session" / "mux-plugin.js").exists()

    adapter = OpenCodeAdapter(data_home=tmp_path, data_dir=tmp_path)
    env = adapter.spawn_spec("sid", SpawnOptions(cwd=tmp_path)).env
    assert set(env) == {"OPENCODE_CONFIG"}
    assert "OPENCODE_CONFIG_DIR" not in env


def test_opencode_forfeits_the_plugin_rather_than_the_pane(tmp_path: Path) -> None:
    """With nowhere to write a config, the session must still launch."""
    adapter = OpenCodeAdapter(data_home=tmp_path, data_dir=None)
    spec = adapter.spawn_spec("sid", SpawnOptions(cwd=tmp_path))
    assert spec.env == {}
    assert spec.executable == "opencode"


def test_opencode_reports_no_transcript_because_it_has_none(tmp_path: Path) -> None:
    adapter = OpenCodeAdapter(data_home=tmp_path)
    assert adapter.transcript_path("ses_1", tmp_path) is None
    assert adapter.locate_transcript("ses_1") is None
    assert adapter.recent_transcripts(tmp_path, 0.0) == []
    assert adapter.database_path() == tmp_path / "opencode.db"


def test_opencode_resume_addresses_the_session_by_id(tmp_path: Path) -> None:
    adapter = OpenCodeAdapter(data_home=tmp_path, data_dir=tmp_path)
    spec = adapter.resume_spec("ses_abc", SpawnOptions(cwd=tmp_path))
    assert spec.argv[:2] == ("--session", "ses_abc")


def test_registry_builds_both_adapters_from_their_descriptors(tmp_path: Path) -> None:
    pi = build_agent_adapter(
        descriptor("pi"),
        executable="pi",
        args=[],
        data_dir=tmp_path,
        mcp_url="http://127.0.0.1:8765/mcp",
    )
    opencode = build_agent_adapter(
        descriptor("opencode"),
        executable="opencode",
        args=[],
        data_dir=tmp_path,
        mcp_url="http://127.0.0.1:8765/mcp",
    )
    assert isinstance(pi, PiAdapter)
    assert isinstance(opencode, OpenCodeAdapter)
    assert pi.name == "pi"
    assert opencode.name == "opencode"


NODE_SHIM = """@ECHO off
GOTO start
:find_dp0
SET dp0=%~dp0
EXIT /b
:start
SETLOCAL
CALL :find_dp0

IF EXIST "%dp0%\\node.exe" (
  SET "_prog=%dp0%\\node.exe"
) ELSE (
  SET "_prog=node"
  SET PATHEXT=%PATHEXT:;.JS;=;%
)

endLocal & goto #_undefined_# 2>NUL || title %COMSPEC% & "%_prog%"  """ + (
    '"%dp0%\\node_modules\\pkg\\dist\\cli.js" %*\n'
)

BINARY_SHIM = """@ECHO off
GOTO start
:find_dp0
SET dp0=%~dp0
EXIT /b
:start
SETLOCAL
CALL :find_dp0
"%dp0%\\node_modules\\pkg\\bin\\tool.exe"   %*
"""


def _write_shim(root: Path, name: str, body: str, target: Path) -> Path:
    shim = root / name
    shim.write_text(body, encoding="utf-8")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("", encoding="utf-8")
    return shim


def test_npm_binary_shim_resolves_to_the_real_executable(tmp_path: Path) -> None:
    """ConPTY cannot execute a `.cmd`; spawning one is what closed the pane."""
    from swe_mux.launchers import resolve_npm_shim_pty_command

    target = tmp_path / "node_modules" / "pkg" / "bin" / "tool.exe"
    shim = _write_shim(tmp_path, "tool.cmd", BINARY_SHIM, target)
    assert resolve_npm_shim_pty_command(str(shim), windows=True) == (str(target), ())


def test_npm_node_shim_resolves_to_node_plus_entrypoint(tmp_path: Path) -> None:
    from swe_mux.launchers import resolve_npm_shim_pty_command

    target = tmp_path / "node_modules" / "pkg" / "dist" / "cli.js"
    shim = _write_shim(tmp_path, "tool.cmd", NODE_SHIM, target)
    node = tmp_path / "node.exe"
    node.write_text("", encoding="utf-8")
    assert resolve_npm_shim_pty_command(str(shim), windows=True) == (
        str(node),
        (str(target),),
    )


def test_npm_shim_resolution_passes_through_a_real_executable(tmp_path: Path) -> None:
    from swe_mux.launchers import resolve_npm_shim_pty_command

    exe = tmp_path / "already.exe"
    exe.write_text("", encoding="utf-8")
    assert resolve_npm_shim_pty_command(str(exe), windows=True) == (str(exe), ())


def test_npm_shim_resolution_leaves_an_unreadable_shim_alone(tmp_path: Path) -> None:
    """An unrecognized shim falls through rather than guessing a target."""
    from swe_mux.launchers import resolve_npm_shim_pty_command

    shim = tmp_path / "weird.cmd"
    shim.write_text("@echo off\r\necho nothing here\r\n", encoding="utf-8")
    assert resolve_npm_shim_pty_command(str(shim), windows=True) == (str(shim), ())


def test_pi_spawn_applies_the_resolver_prefix_before_its_own_flags(tmp_path: Path) -> None:
    adapter = PiAdapter(
        "pi",
        data_home=tmp_path,
        command_resolver=lambda _exe: ("node.exe", ("cli.js",)),
    )
    spec = adapter.spawn_spec("sid", SpawnOptions(cwd=tmp_path))
    assert spec.executable == "node.exe"
    # The entrypoint must precede --extension or node would treat the flag as its own.
    assert spec.argv[0] == "cli.js"
    assert spec.argv[1] == "--extension"


def test_opencode_resume_applies_the_resolver_prefix_before_session(tmp_path: Path) -> None:
    adapter = OpenCodeAdapter(
        "opencode",
        data_home=tmp_path,
        command_resolver=lambda _exe: ("opencode.exe", ()),
    )
    spec = adapter.resume_spec("ses_abc", SpawnOptions(cwd=tmp_path))
    assert spec.executable == "opencode.exe"
    assert spec.argv[:2] == ("--session", "ses_abc")


def test_pi_and_omp_resolve_separate_default_homes() -> None:
    """They share `PI_CODING_AGENT_DIR`, but their defaults must not collide."""
    assert descriptor("pi").data_home() != descriptor("omp").data_home()
    assert descriptor("pi").data_home().parts[-2:] == (".pi", "agent")
    assert descriptor("omp").data_home().parts[-2:] == (".omp", "agent")
