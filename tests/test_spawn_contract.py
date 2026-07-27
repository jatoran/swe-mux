from __future__ import annotations

from pathlib import Path

import pytest

from swe_mux.spawn_contract import (
    MAX_SPAWN_ENV,
    SpawnRequest,
    resolve_contained_cwd,
    scrub_claude_session_markers,
)


def test_scrub_drops_parent_claude_markers_but_keeps_user_configuration() -> None:
    environment = {
        "CLAUDECODE": "1",
        "claude_code_child_session": "1",  # Windows env is case-insensitive
        "CLAUDE_CODE_ENTRYPOINT": "cli",
        "CLAUDE_CODE_SESSION_ID": "abc",
        "CLAUDE_CODE_EXECPATH": r"C:\bin\claude.exe",
        "CLAUDE_PID": "123",
        "CLAUDE_EFFORT": "high",
        # Deliberate user configuration must pass through untouched.
        "CLAUDE_CODE_DISABLE_ADAPTIVE_THINKING": "1",
        "ANTHROPIC_API_KEY": "secret",
        "PATH": r"C:\Windows",
    }
    scrubbed = scrub_claude_session_markers(environment)
    assert "CLAUDECODE" not in scrubbed
    assert "claude_code_child_session" not in scrubbed
    assert "CLAUDE_CODE_ENTRYPOINT" not in scrubbed
    assert "CLAUDE_CODE_SESSION_ID" not in scrubbed
    assert "CLAUDE_CODE_EXECPATH" not in scrubbed
    assert "CLAUDE_PID" not in scrubbed and "CLAUDE_EFFORT" not in scrubbed
    assert scrubbed["CLAUDE_CODE_DISABLE_ADAPTIVE_THINKING"] == "1"
    assert scrubbed["ANTHROPIC_API_KEY"] == "secret"
    assert scrubbed["PATH"] == r"C:\Windows"


def test_spawn_contract_normalizes_structured_fields() -> None:
    request = SpawnRequest.parse(
        {"backend": "shell", "exe": "pwsh", "exe_args": ["-NoLogo"], "project_id": "dev"}
    )
    assert request.executable == "pwsh"
    assert request.argv == ("-NoLogo",)
    assert request.project_id == "dev"
    assert request.completion_mode == "interactive"


def test_spawn_contract_accepts_one_shot_shell_completion() -> None:
    request = SpawnRequest.parse(
        {"backend": "shell", "project_id": "dev", "completion_mode": "one_shot"}
    )
    assert request.completion_mode == "one_shot"


def test_spawn_contract_carries_a_working_directory_and_environment() -> None:
    request = SpawnRequest.parse(
        {
            "backend": "shell",
            "project_id": "dev",
            "cwd": " frontend ",
            "env": {"PORT": 45603, "DEBUG": True},
        }
    )
    assert request.cwd == "frontend"
    # Scalars are stringified here so every consumer sees the same shape.
    assert request.env == {"PORT": "45603", "DEBUG": "True"}


def test_spawn_contract_env_is_bounded() -> None:
    oversized = {f"K{index}": "1" for index in range(MAX_SPAWN_ENV + 1)}
    with pytest.raises(ValueError) as error:
        SpawnRequest.parse({"backend": "shell", "project_id": "dev", "env": oversized})
    assert "env" in error.value.args[0]


def test_contained_cwd_resolves_inside_and_refuses_outside(tmp_path: Path) -> None:
    root = tmp_path / "project"
    (root / "frontend").mkdir(parents=True)

    assert resolve_contained_cwd("frontend", root) == str(root / "frontend")
    assert resolve_contained_cwd(str(root / "frontend"), root) == str(root / "frontend")
    assert resolve_contained_cwd("", root) == str(root)
    # Traversal is caught after resolution, so ".." inside an absolute path is too.
    for escape in ("..", str(tmp_path), str(root / ".." / "elsewhere")):
        with pytest.raises(ValueError, match="must stay inside"):
            resolve_contained_cwd(escape, root)


@pytest.mark.parametrize(
    "body,field",
    [
        ({"backend": "claude", "profile_id": "pwsh"}, "profile_id"),
        ({"profile_id": "pwsh", "executable": "pwsh"}, "executable"),
        ({"argv": "--bad"}, "argv"),
        ({"backend": "shell"}, "project_id"),
        ({"backend": "shell", "project_id": "dev", "cwd": "   "}, "cwd"),
        ({"backend": "shell", "project_id": "dev", "cwd": 7}, "cwd"),
        ({"backend": "shell", "project_id": "dev", "env": ["PORT=1"]}, "env"),
        ({"backend": "shell", "project_id": "dev", "env": {"PORT": {"nested": 1}}}, "env"),
        ({"backend": "shell", "project_id": "dev", "env": {"A=B": "1"}}, "env"),
        ({"backend": "shell", "project_id": "dev", "env": {"": "1"}}, "env"),
        (
            {"backend": "shell", "project_id": "dev", "completion_mode": "eventually"},
            "completion_mode",
        ),
        ({"surprise": True}, "surprise"),
    ],
)
def test_spawn_contract_rejects_ambiguous_or_untyped_requests(
    body: dict[str, object], field: str
) -> None:
    with pytest.raises(ValueError) as error:
        SpawnRequest.parse(body)
    assert field in error.value.args[0]
