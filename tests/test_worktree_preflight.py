from __future__ import annotations

import json
import os
import tomllib
from pathlib import Path

from swe_mux.adapters import ClaudeAdapter, CodexAdapter, ShellAdapter, SpawnOptions


def test_claude_preflight_clones_trust_and_local_permissions(tmp_path: Path) -> None:
    home = tmp_path / "home"
    project = tmp_path / "repo"
    worktree = tmp_path / "worktrees" / "repo" / "worktree-feature"
    worktree.mkdir(parents=True)
    settings = project / ".claude" / "settings.local.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(
        json.dumps({"permissions": {"allow": ["Read(./**)"], "deny": ["Bash(rm:*)"]}}),
        encoding="utf-8",
    )
    home.mkdir()
    source_key = project.resolve().as_posix()
    (home / ".claude.json").write_text(
        json.dumps({"projects": {source_key: {"hasTrustDialogAccepted": True, "example": 1}}}),
        encoding="utf-8",
    )
    adapter = ClaudeAdapter(
        data_home_resolver=lambda: home / ".claude",
        user_home_resolver=lambda: home,
    )

    adapter.preflight_worktree(project, worktree)

    payload = json.loads((home / ".claude.json").read_text(encoding="utf-8"))
    target_key = worktree.resolve().as_posix()
    assert "\\" not in target_key
    assert payload["projects"][target_key] == {
        "hasTrustDialogAccepted": True,
        "example": 1,
    }
    copied = json.loads((worktree / ".claude" / "settings.local.json").read_text(encoding="utf-8"))
    assert copied["permissions"]["allow"] == ["Read(./**)"]


def test_claude_preflight_does_not_replace_existing_target_trust(tmp_path: Path) -> None:
    home = tmp_path / "home"
    project = tmp_path / "repo"
    worktree = tmp_path / "worktree"
    home.mkdir()
    worktree.mkdir()
    target_key = worktree.resolve().as_posix()
    original = {"hasTrustDialogAccepted": False, "custom": "keep"}
    (home / ".claude.json").write_text(
        json.dumps({"projects": {target_key: original}}), encoding="utf-8"
    )
    adapter = ClaudeAdapter(user_home_resolver=lambda: home)

    adapter.preflight_worktree(project, worktree)

    payload = json.loads((home / ".claude.json").read_text(encoding="utf-8"))
    assert payload["projects"][target_key] == original


def test_codex_preflight_updates_runtime_codex_home_atomically(tmp_path: Path) -> None:
    data_home = tmp_path / "codex-home"
    data_home.mkdir()
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    key = os.path.normcase(str(worktree.resolve()))
    (data_home / "config.toml").write_text(
        f'[projects.{json.dumps(key)}]\ntrust_level = "untrusted"\n', encoding="utf-8"
    )
    adapter = CodexAdapter(data_home_resolver=lambda: data_home)

    adapter.preflight_worktree(tmp_path / "repo", worktree)

    parsed = tomllib.loads((data_home / "config.toml").read_text(encoding="utf-8"))
    assert parsed["projects"][key]["trust_level"] == "trusted"


def test_worktree_access_arguments_are_adapter_owned(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    worktree = tmp_path / "worktree"
    root.mkdir()
    worktree.mkdir()
    options = SpawnOptions(worktree, worktree_project_root=root)

    claude = ClaudeAdapter().spawn_spec("session", options)
    codex = CodexAdapter().spawn_spec("session", options)

    assert claude.argv[:4] == ("--session-id", "session", "--add-dir", str(root.resolve()))
    assert "sandbox_workspace_write.writable_roots" in " ".join(codex.argv)
    assert ShellAdapter().worktree_spawn_args(root) == ()
