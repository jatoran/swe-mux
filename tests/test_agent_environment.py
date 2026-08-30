from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from swe_mux import agent_environment, path_identity
from swe_mux.agent_environment import (
    _project_scoped_mcp_tables,
    capture_config_baseline,
    clear_cache,
    discover_agent_environment,
)


@pytest.fixture(autouse=True)
def _isolated_cache():
    clear_cache()
    yield
    clear_cache()


def _section(payload: dict, section_id: str) -> dict:
    return next(section for section in payload["sections"] if section["id"] == section_id)


def _meta(item: dict) -> list[tuple[str, str]]:
    return [(entry["label"], entry["value"]) for entry in item["meta"]]


def _discover(
    backend: str,
    cwd: Path,
    *,
    args: list[str] | None = None,
    loaded_at: float = 2_000.0,
    run_started_at: float | None = None,
    baseline: dict[str, str] | None = None,
    refresh: bool = False,
) -> dict:
    return discover_agent_environment(
        backend=backend,
        cwd=cwd,
        executable="not-the-provider.exe",
        args=args or [],
        model=None,
        loaded_at=loaded_at,
        run_started_at=run_started_at if run_started_at is not None else loaded_at + 100,
        baseline=baseline,
        refresh=refresh,
    )


def _baseline(backend: str, cwd: Path, *, args: list[str] | None = None) -> dict[str, str]:
    return capture_config_baseline(backend=backend, cwd=cwd, args=args or [])


def _changed(payload: dict) -> set[str]:
    return {source["label"] for source in payload["sources"] if source["changed_after_start"]}


def test_codex_inventory_separates_scope_origin_and_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "codex-home"
    cwd = tmp_path / "repo"
    home.mkdir()
    (cwd / ".codex").mkdir(parents=True)
    monkeypatch.setenv("CODEX_HOME", str(home))
    (home / "config.toml").write_text(
        """
model = "gpt-user"
approval_policy = "on-request"
[features]
hooks = true
apps = false
[mcp_servers.docs]
url = "https://example.test/mcp?token=must-not-leak"
bearer_token_env_var = "SECRET_TOKEN"
[plugins."review@personal"]
enabled = false
""",
        encoding="utf-8",
    )
    (cwd / ".codex" / "config.toml").write_text(
        'sandbox_mode = "workspace-write"\n[features]\napps = true\n',
        encoding="utf-8",
    )

    payload = _discover(
        "codex",
        cwd,
        args=["-c", 'model="gpt-session"', "--disable", "hooks"],
    )

    policies = {item["name"]: item for item in _section(payload, "policies")["items"]}
    features = {item["name"]: item for item in _section(payload, "features")["items"]}
    mcp = _section(payload, "mcp")["items"]
    plugins = _section(payload, "plugins")["items"]
    assert policies["model"]["scope"] == "session"
    assert policies["model"]["meta"] == [{"label": "Value", "value": "gpt-session"}]
    assert policies["sandbox_mode"]["scope"] == "project"
    assert features["apps"]["state"] == "enabled"
    assert features["hooks"]["state"] == "disabled"
    assert mcp[0]["origin"] == "~/.codex/config.toml"
    assert mcp[0]["meta"][1]["value"] == "https://example.test/mcp"
    assert plugins[0]["state"] == "disabled"
    source_ids = {source["id"] for source in payload["sources"]}
    assert all(
        item["source_id"] is None or item["source_id"] in source_ids
        for section in payload["sections"]
        for item in section["items"]
    )
    serialized = json.dumps(payload)
    assert "must-not-leak" not in serialized
    assert "SECRET_TOKEN" not in serialized


def test_codex_profile_is_resolved_from_the_user_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "codex-home"
    cwd = tmp_path / "repo"
    home.mkdir()
    cwd.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(home))
    (home / "config.toml").write_text(
        'model = "base"\n[profiles.review]\nmodel = "profile"\napproval_policy = "never"\n',
        encoding="utf-8",
    )

    payload = _discover("codex", cwd, args=["--profile", "review"])

    policies = {item["name"]: item for item in _section(payload, "policies")["items"]}
    assert policies["model"]["meta"] == [{"label": "Value", "value": "profile"}]
    assert policies["model"]["origin"] == "Codex profile: review"
    assert policies["approval_policy"]["scope"] == "user"


def test_claude_inventory_finds_hooks_agents_mcp_and_documented_builtins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "claude-home"
    cwd = tmp_path / "repo"
    (home / "agents").mkdir(parents=True)
    (cwd / ".claude").mkdir(parents=True)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home))
    (home / "settings.json").write_text(
        json.dumps(
            {
                "permissions": {"deny": ["Bash(rm *)"]},
                "hooks": {
                    "PostToolUse": [
                        {
                            "matcher": "Edit|Write",
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": (
                                        'node "/tools/format.js" --api-key hidden '
                                        "--endpoint https://user:hidden@example.test/ingest"
                                    ),
                                    "timeout": 12,
                                }
                            ],
                        }
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    (cwd / ".mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "local": {
                        "command": "python",
                        "args": ["server.py", "--secret", "hidden"],
                        "env": {"TOKEN": "hidden"},
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    (home / "agents" / "reviewer.md").write_text(
        "---\nname: reviewer\ndescription: Reviews changes\nmodel: sonnet\ntools: Read,Grep\n---\n",
        encoding="utf-8",
    )

    payload = _discover("claude", cwd)

    hooks = _section(payload, "hooks")["items"]
    agents = _section(payload, "agents")["items"]
    tools = {item["name"]: item for item in _section(payload, "tools")["items"]}
    mcp = _section(payload, "mcp")["items"]
    # The row names the script the hook runs; the event is the group heading, and
    # every remaining argument stays out of the payload.
    assert hooks[0]["name"] == "format.js"
    assert hooks[0]["group"] == "PostToolUse"
    assert hooks[0]["owner"] == ""
    assert hooks[0]["meta"] == [
        {"label": "Runs", "value": "/tools/format.js"},
        {"label": "Program", "value": "node"},
        {"label": "Matcher", "value": "Edit|Write"},
        {"label": "Timeout", "value": "12s"},
    ]
    assert any(item["name"] == "reviewer" and item["scope"] == "user" for item in agents)
    assert any(item["name"] == "Explore" and item["scope"] == "built_in" for item in agents)
    assert tools["Bash"]["state"] == "restricted"
    assert mcp[0]["meta"][-1] == {"label": "Executable", "value": "python"}
    source_ids = {source["id"] for source in payload["sources"]}
    assert all(
        item["source_id"] is None or item["source_id"] in source_ids
        for section in payload["sections"]
        for item in section["items"]
    )
    serialized = json.dumps(payload)
    assert "hidden" not in serialized
    assert "example.test" not in serialized
    assert "--api-key" not in serialized


def test_omp_inventory_reads_native_mcp_and_documents_xdev_tools(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "omp-home"
    cwd = tmp_path / "repo"
    extension = tmp_path / "mux-extension"
    home.mkdir()
    (cwd / ".omp").mkdir(parents=True)
    extension.mkdir()
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(home))
    (home / "mcp.json").write_text(
        json.dumps({"mcpServers": {"user-docs": {"url": "https://docs.test/mcp"}}}),
        encoding="utf-8",
    )
    (extension / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"mux": {"url": "http://127.0.0.1:8765/mcp"}}}),
        encoding="utf-8",
    )

    payload = _discover("omp", cwd, args=["--extension", str(extension)])

    mcp = {item["name"]: item for item in _section(payload, "mcp")["items"]}
    tools = {item["name"]: item for item in _section(payload, "tools")["items"]}
    assert set(mcp) == {"mux", "user-docs"}
    assert mcp["mux"]["scope"] == "session"
    assert len(tools) == 31
    assert "[xd://]" in tools["ast_edit"]["description"]
    assert "[xd://]" not in tools["read"]["description"]


def test_omp_hooks_section_lists_the_injected_extension(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """OMP has no hooks table for the config scan; its lifecycle wiring is the
    ``--extension`` package mux injects. "Hooks: 0" for a session whose every
    status transition is hook-sourced reads as "mux is not wired in", so the
    section must surface the extension, owner-chipped by content marker."""
    home = tmp_path / "omp-home"
    cwd = tmp_path / "repo"
    mux_extension = tmp_path / "omp-data" / "omp-extensions" / "session-1"
    user_extension = tmp_path / "my-extension"
    home.mkdir()
    (cwd / ".omp").mkdir(parents=True)
    mux_extension.mkdir(parents=True)
    user_extension.mkdir()
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(home))
    (mux_extension / "index.ts").write_text(
        'const url = process.env.MUX_HOOK_URL;\nexport default function () {}\n',
        encoding="utf-8",
    )
    (user_extension / "index.ts").write_text(
        "export default function () {}\n", encoding="utf-8"
    )

    payload = _discover(
        "omp",
        cwd,
        args=["--extension", str(mux_extension), "--extension", str(user_extension)],
    )

    hooks = _section(payload, "hooks")["items"]
    assert [item["owner"] for item in hooks[:2]] == ["swe_mux", ""]
    mine, theirs = hooks[0], hooks[1]
    assert mine["name"] == "index.ts"
    assert mine["state"] == "configured"
    assert mine["scope"] == "session"
    assert mine["group"] == "Extension (in-process)"
    assert any(meta["label"] == "Reports" for meta in mine["meta"])
    assert theirs["name"] == "my-extension"
    assert theirs["state"] == "configured"
    assert not any(meta["label"] == "Reports" for meta in theirs["meta"])


@pytest.mark.skipif(sys.platform != "win32", reason="Windows hook command paths")
def test_hooks_group_by_event_and_mark_the_ones_swe_mux_installs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "codex-home"
    cwd = tmp_path / "repo"
    home.mkdir()
    cwd.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(home))
    (home / "hooks.json").write_text(
        json.dumps(
            {
                "hooks": {
                    # Deliberately out of lifecycle order in the file.
                    "Stop": [{"hooks": [{"type": "command", "command": "notify-send done"}]}],
                    "SessionStart": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": (
                                        "powershell -NoProfile -File "
                                        '"C:\\tools\\state.ps1" session'
                                    ),
                                },
                                {
                                    "type": "command",
                                    "command": (
                                        "if [ -f '/opt/vendor/agent-hook.cmd' ]; "
                                        "then '/opt/vendor/agent-hook.cmd'; fi"
                                    ),
                                },
                            ]
                        }
                    ],
                    "TotallyMadeUpEvent": [
                        {"hooks": [{"type": "command", "command": "python -m my.reporter"}]}
                    ],
                }
            }
        ),
        encoding="utf-8",
    )

    payload = _discover(
        "codex",
        cwd,
        args=[
            "-c",
            'hooks.SessionStart=[{ hooks = [{ type = "command", '
            'command = "python -m swe_mux.hook_client SessionStart", timeout = 15 }] }]',
        ],
    )

    section = _section(payload, "hooks")
    rows = [(item["group"], item["name"], item["owner"]) for item in section["items"]]
    # Lifecycle order across sources, unknown events last; swe-mux's own handler is
    # distinguishable from the user's inside the same event.
    assert rows == [
        ("SessionStart", "state.ps1", ""),
        ("SessionStart", "agent-hook.cmd", ""),
        ("SessionStart", "swe_mux.hook_client", "swe_mux"),
        ("Stop", "notify-send", ""),
        ("TotallyMadeUpEvent", "my.reporter", ""),
    ]
    facts = {item["name"]: dict(_meta(item)) for item in section["items"]}
    assert facts["state.ps1"] == {"Runs": "C:\\tools\\state.ps1", "Program": "powershell"}
    # An inline shell body reports the script it guards, never the snippet itself.
    assert facts["agent-hook.cmd"] == {
        "Runs": "/opt/vendor/agent-hook.cmd",
        "Program": "inline shell",
    }
    assert facts["swe_mux.hook_client"] == {
        "Runs": "swe_mux.hook_client",
        "Program": "python",
        "Timeout": "15s",
    }
    # No identifiable script: the program alone, and the arguments stay withheld.
    assert facts["notify-send"] == {"Runs": "notify-send (arguments withheld)"}


def test_source_drift_reports_content_and_skill_drift_stays_relative_to_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "codex-home"
    cwd = tmp_path / "repo"
    home.mkdir()
    cwd.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(home))
    config = home / "config.toml"
    config.write_text('model = "gpt"\n', encoding="utf-8")
    baseline = _baseline("codex", cwd)
    config.write_text('model = "gpt-5"\n', encoding="utf-8")
    os.utime(config, (3_000.0, 3_000.0))
    skill = home / "skills" / "late" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("---\nname: late\ndescription: late skill\n---\n", encoding="utf-8")
    os.utime(skill, (3_000.0, 3_000.0))

    payload = _discover("codex", cwd, loaded_at=2_000.0, baseline=baseline)

    skill_item = _section(payload, "skills")["items"][0]
    assert _changed(payload) == {"~/.codex/config.toml"}
    assert payload["config_baseline"] == "captured"
    # Skills are still dated against the CLI generation: their files are
    # user-authored and do not churn the way a CLI's own state file does.
    assert skill_item["state"] == "restart_required"
    assert payload["runtime"]["loaded_at"] == 2_000.0
    assert payload["runtime"]["run_started_at"] == 2_100.0


def test_rewriting_a_source_with_identical_content_is_not_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole reason this stopped being an mtime comparison.

    swe-mux rewrites its own generated config on every daemon start and Claude
    rewrites `~/.claude.json` continuously; both used to mark every live session
    as drifted within seconds of starting.
    """
    home = tmp_path / "codex-home"
    cwd = tmp_path / "repo"
    home.mkdir()
    cwd.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(home))
    config = home / "config.toml"
    config.write_text('model = "gpt"\napproval_policy = "on-request"\n', encoding="utf-8")
    baseline = _baseline("codex", cwd)

    os.utime(config, (9_000.0, 9_000.0))
    untouched = _discover("codex", cwd, baseline=baseline, refresh=True)
    # Re-serialized with the keys in the other order: same configuration, so
    # the same answer.
    config.write_text('approval_policy = "on-request"\nmodel = "gpt"\n', encoding="utf-8")
    reordered = _discover("codex", cwd, baseline=baseline, refresh=True)

    assert _changed(untouched) == set()
    assert _changed(reordered) == set()


def test_unread_bookkeeping_in_a_source_is_not_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`~/.claude.json` is Claude's state file, and this is why it was so loud."""
    home = tmp_path / "claude-home"
    user_home = tmp_path / "user"
    cwd = tmp_path / "repo"
    for path in (home, user_home, cwd):
        path.mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: user_home))
    state = user_home / ".claude.json"
    state.write_text(json.dumps({"numStartups": 4, "mcpServers": {"api": {"url": ""}}}), "utf-8")
    baseline = _baseline("claude", cwd)

    state.write_text(
        json.dumps({"numStartups": 5, "lastCost": 1.25, "mcpServers": {"api": {"url": ""}}}),
        encoding="utf-8",
    )
    quiet = _discover("claude", cwd, baseline=baseline, refresh=True)
    state.write_text(
        json.dumps({"numStartups": 6, "mcpServers": {"api": {"url": "https://elsewhere/mcp"}}}),
        encoding="utf-8",
    )
    real = _discover("claude", cwd, baseline=baseline, refresh=True)

    assert _changed(quiet) == set()
    assert _changed(real) == {"~/.claude.json"}
    changed_rows = [
        item["name"] for item in _section(real, "mcp")["items"] if item["changed_after_start"]
    ]
    assert changed_rows == ["api"]


def test_drift_is_untracked_without_a_baseline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No snapshot means no answer, and saying so beats claiming nothing moved."""
    home = tmp_path / "codex-home"
    cwd = tmp_path / "repo"
    home.mkdir()
    cwd.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(home))
    (home / "config.toml").write_text('model = "gpt"\n', encoding="utf-8")
    os.utime(home / "config.toml", (9_000.0, 9_000.0))

    payload = _discover("codex", cwd, loaded_at=2_000.0)

    assert payload["config_baseline"] == "unavailable"
    assert _changed(payload) == set()


def test_a_source_the_baseline_never_saw_is_not_reported_as_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Source ids are path digests, so a moved working directory resolves new ones.

    Calling those "changed since load" would recreate the false alarm the
    baseline replaced, on every session whose agent changed directory.
    """
    home = tmp_path / "codex-home"
    first = tmp_path / "repo"
    second = tmp_path / "other"
    home.mkdir()
    (first / ".codex").mkdir(parents=True)
    (second / ".codex").mkdir(parents=True)
    monkeypatch.setenv("CODEX_HOME", str(home))
    (home / "config.toml").write_text('model = "gpt"\n', encoding="utf-8")
    (first / ".codex" / "config.toml").write_text('model = "first"\n', encoding="utf-8")
    (second / ".codex" / "config.toml").write_text('model = "second"\n', encoding="utf-8")
    baseline = _baseline("codex", first)

    payload = _discover("codex", second, baseline=baseline)

    assert _changed(payload) == set()
    assert payload["config_baseline"] == "captured"


def test_a_plugin_manifest_edit_reaches_the_declaring_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A manifest is read but never listed, so its content rides the registry."""
    home = tmp_path / "claude-home"
    user_home = tmp_path / "user"
    cwd = tmp_path / "repo"
    install = tmp_path / "plugins" / "demo"
    for path in (home, user_home, cwd):
        path.mkdir()
    (install / ".claude-plugin").mkdir(parents=True)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: user_home))
    registry = home / "plugins" / "installed_plugins.json"
    registry.parent.mkdir(parents=True)
    registry.write_text(
        json.dumps({"plugins": {"demo@market": [{"installPath": str(install)}]}}),
        encoding="utf-8",
    )
    manifest = install / ".claude-plugin" / "plugin.json"
    manifest.write_text(json.dumps({"version": "1.0.0", "author": "a"}), encoding="utf-8")
    baseline = _baseline("claude", cwd)

    manifest.write_text(json.dumps({"version": "1.0.0", "author": "b"}), encoding="utf-8")
    payload = _discover("claude", cwd, baseline=baseline, refresh=True)

    assert _changed(payload) == {"Claude plugin registry"}


def test_cached_scan_requires_explicit_refresh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "codex-home"
    cwd = tmp_path / "repo"
    home.mkdir()
    cwd.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(home))
    config = home / "config.toml"
    config.write_text("[features]\nhooks = true\n", encoding="utf-8")
    first = _discover("codex", cwd)
    config.write_text("[features]\nhooks = false\n", encoding="utf-8")
    cached = _discover("codex", cwd)
    fresh = _discover("codex", cwd, refresh=True)
    assert _section(first, "features")["items"][0]["state"] == "enabled"
    assert _section(cached, "features")["items"][0]["state"] == "enabled"
    assert _section(fresh, "features")["items"][0]["state"] == "disabled"


def test_new_conversation_run_does_not_reuse_stale_runtime_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "codex-home"
    cwd = tmp_path / "repo"
    home.mkdir()
    cwd.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(home))

    first = _discover("codex", cwd, run_started_at=2_100.0)
    second = _discover("codex", cwd, run_started_at=2_200.0)

    assert first["runtime"]["run_started_at"] == 2_100.0
    assert second["runtime"]["run_started_at"] == 2_200.0


def test_shell_backend_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="registered agent sessions"):
        _discover("shell", tmp_path)


# `~/.claude.json` keeps one `projects` entry per directory Claude has ever run
# in, and reading that map used to be the most expensive thing the inventory did.
# Every key was handed to `same_path`, which stats both sides - and a key can name
# anywhere the user has ever worked, including a provider that is not there. On
# the development host that map had 183 entries, one of them a UNC path into a
# stopped WSL distro, and this file's Claude inventory test cost 367.7s because of
# it. The tests below pin both halves of the fix: entries that carry no server are
# dropped before any path is compared, and what remains is matched on the strings
# before the filesystem is asked.


def _project_map(cwd: Path, *, extra: int = 200) -> dict[str, object]:
    """A realistic map: mostly stale keys with empty tables, one live entry."""
    projects: dict[str, object] = {
        f"/gone/project-{index}": {"mcpServers": {}, "history": []} for index in range(extra)
    }
    projects["//no-such-host/no-such-share/elsewhere"] = {"mcpServers": {}}
    projects[str(cwd).replace(os.sep, "/")] = {"mcpServers": {"local": {"command": "python"}}}
    return projects


def test_the_project_map_finds_this_directorys_servers(tmp_path: Path) -> None:
    tables = _project_scoped_mcp_tables(_project_map(tmp_path), tmp_path)
    assert tables == [({"local": {"command": "python"}}, "local")]


def test_entries_carrying_no_server_are_dropped_before_any_path_is_compared(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An empty table contributes no rows, so comparing its key is pure cost.

    Dropping it first is what takes the paths this has to consider from 183 to 2
    on the host the defect was measured on, and it is behaviour-preserving: the
    previous code appended the empty table and then iterated it to nothing.
    """
    compared: list[str] = []

    def recording(left: str, right: object) -> bool:
        compared.append(left)
        return path_identity.same_path_lexically(left, right)

    monkeypatch.setattr(agent_environment, "same_path_lexically", recording)
    projects = _project_map(tmp_path)
    tables = _project_scoped_mcp_tables(projects, tmp_path)

    assert tables == [({"local": {"command": "python"}}, "local")]
    assert len(projects) == 202
    assert compared == [str(tmp_path).replace(os.sep, "/")]


def test_no_recorded_project_path_is_stated_when_one_matches_on_its_spelling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Opening the Agent tab is documented to probe nothing.

    A stat against a recorded path is a probe, and it is the one that blocked:
    `os.path.exists` on `//wsl.localhost/<distro>` with the distro stopped was
    measured at 80.1 seconds. A directory the CLI is running in matches its own
    recorded spelling, so nothing has to be stat'ed to find it.
    """

    def refuse(path: object) -> os.stat_result:
        raise AssertionError(f"the inventory stat'ed a recorded project path: {path}")

    monkeypatch.setattr(path_identity, "_stat", refuse)
    tables = _project_scoped_mcp_tables(_project_map(tmp_path), tmp_path)
    assert tables == [({"local": {"command": "python"}}, "local")]


def test_the_filesystem_is_still_asked_when_no_spelling_matches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The lexical pass may only add answers, never remove one.

    A recorded key can name this directory through a symlink or a junction and
    share no components with it, and only the filesystem knows. So a sweep that
    matched nothing escalates - bounded per provider by `path_identity`, but it
    escalates.
    """
    projects: dict[str, object] = {
        "/gone/empty": {"mcpServers": {}},
        "/an/alias/of/this/directory": {"mcpServers": {"aliased": {"command": "python"}}},
        "/gone/other": {"mcpServers": {"other": {"command": "python"}}},
    }
    asked: list[str] = []

    def matching(left: str, right: object) -> bool:
        asked.append(left)
        return left == "/an/alias/of/this/directory"

    monkeypatch.setattr(agent_environment, "same_path", matching)
    tables = _project_scoped_mcp_tables(projects, tmp_path)

    assert tables == [({"aliased": {"command": "python"}}, "local")]
    # Only the entries that carry a server, and the empty one is not among them.
    assert asked == ["/an/alias/of/this/directory", "/gone/other"]
