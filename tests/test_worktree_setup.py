from __future__ import annotations

import sys
from pathlib import Path

import pytest

from swe_mux import worktree_setup
from swe_mux.worktree_setup import SetupCommand, WorktreeSetupResult


def test_configured_setup_command_takes_precedence(tmp_path: Path) -> None:
    command = worktree_setup.resolve_setup_command(
        tmp_path, {"worktree": {"setup_command": "uv sync"}}
    )

    assert command is not None
    assert command.source == "project_config"
    assert command.display == "uv sync"


@pytest.mark.asyncio
async def test_setup_success_captures_output_for_session_scrollback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        worktree_setup,
        "resolve_setup_command",
        lambda _path, _values: SetupCommand(
            (sys.executable, "-c", "print('dependencies ready')"),
            "test setup",
            "project_config",
        ),
    )

    result = await worktree_setup.run_worktree_setup(tmp_path, {}, project_id="project-1")

    assert result.status == "succeeded"
    terminal = result.terminal_output()
    assert b"dependencies ready" in terminal
    assert b"setup completed" in terminal


@pytest.mark.asyncio
async def test_setup_failure_is_nonfatal_and_marks_tree_unbootstrapped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        worktree_setup,
        "resolve_setup_command",
        lambda _path, _values: SetupCommand(
            (sys.executable, "-c", "print('install failed'); raise SystemExit(7)"),
            "test setup",
            "convention",
        ),
    )

    result = await worktree_setup.run_worktree_setup(tmp_path, {}, project_id="project-1")

    assert result.status == "failed"
    assert result.exit_code == 7
    assert b"install failed" in result.terminal_output()
    assert b"not bootstrapped" in result.terminal_output()


def test_setup_error_terminal_message_is_actionable() -> None:
    result = WorktreeSetupResult("error", error="interpreter missing")

    assert b"interpreter missing" in result.terminal_output()
    assert b"not bootstrapped" in result.terminal_output()
