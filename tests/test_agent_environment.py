from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from swe_mux.agent_environment import clear_cache, discover_agent_environment


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
        refresh=refresh,
    )


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


def test_source_and_skill_drift_are_relative_to_cli_process_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "codex-home"
    cwd = tmp_path / "repo"
    home.mkdir()
    cwd.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(home))
    config = home / "config.toml"
    config.write_text('model = "gpt"\n', encoding="utf-8")
    os.utime(config, (3_000.0, 3_000.0))
    skill = home / "skills" / "late" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("---\nname: late\ndescription: late skill\n---\n", encoding="utf-8")
    os.utime(skill, (3_000.0, 3_000.0))

    payload = _discover("codex", cwd, loaded_at=2_000.0)

    source = next(item for item in payload["sources"] if item["label"] == "~/.codex/config.toml")
    skill_item = _section(payload, "skills")["items"][0]
    assert source["changed_after_start"] is True
    assert skill_item["state"] == "restart_required"
    assert payload["runtime"]["loaded_at"] == 2_000.0
    assert payload["runtime"]["run_started_at"] == 2_100.0


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
    with pytest.raises(ValueError, match="only for Claude and Codex"):
        _discover("shell", tmp_path)
