from __future__ import annotations

from pathlib import Path

import pytest

from swe_mux.spawn_contract import (
    MAX_SPAWN_ENV,
    SpawnRequest,
    base_session_env,
    resolve_contained_cwd,
    resolve_listed_cwd,
    scrub_claude_session_markers,
)


def _listed(*paths: Path) -> dict[str, str]:
    """Mimic `_listed_worktree_paths`: casefolded resolved path -> reported path."""
    return {str(p.resolve()).casefold(): str(p) for p in paths}


def test_listed_cwd_admits_a_registered_worktree_outside_the_project_root(
    tmp_path: Path,
) -> None:
    # The whole point: a sibling worktree is outside the root, so containment refuses it
    # and the allow-list is the only thing that can let a session in.
    root = tmp_path / "repo"
    worktree = tmp_path / ".worktrees" / "repo" / "agent-a"
    worktree.mkdir(parents=True)
    root.mkdir()

    with pytest.raises(ValueError):
        resolve_contained_cwd(str(worktree), root)

    assert resolve_listed_cwd(str(worktree), _listed(worktree)) == str(worktree.resolve())


def test_listed_cwd_refuses_a_path_git_never_reported(tmp_path: Path) -> None:
    registered = tmp_path / ".worktrees" / "repo" / "agent-a"
    registered.mkdir(parents=True)
    elsewhere = tmp_path / "somewhere-else"
    elsewhere.mkdir()

    with pytest.raises(ValueError):
        resolve_listed_cwd(str(elsewhere), _listed(registered))


def test_listed_cwd_refuses_relative_values(tmp_path: Path) -> None:
    # Relative paths only mean something against a root, and this entry point has none;
    # resolving them against the process cwd would be a silent escape.
    registered = tmp_path / "agent-a"
    registered.mkdir()
    with pytest.raises(ValueError):
        resolve_listed_cwd("agent-a", _listed(registered))


def test_listed_cwd_refuses_an_empty_allow_list(tmp_path: Path) -> None:
    target = tmp_path / "agent-a"
    target.mkdir()
    with pytest.raises(ValueError):
        resolve_listed_cwd(str(target), {})


def test_listed_cwd_matches_case_insensitively(tmp_path: Path) -> None:
    # Windows paths arrive with inconsistent drive/segment casing.
    registered = tmp_path / "AgentA"
    registered.mkdir()
    allowed = _listed(registered)
    assert resolve_listed_cwd(str(registered).upper(), allowed) == str(registered.resolve())


def test_listed_cwd_does_not_admit_a_subdirectory_of_a_worktree(tmp_path: Path) -> None:
    # Only the worktree root is admitted. A subdirectory is not something git reported,
    # so it must go through the containment check against that worktree instead.
    registered = tmp_path / "agent-a"
    (registered / "frontend").mkdir(parents=True)
    with pytest.raises(ValueError):
        resolve_listed_cwd(str(registered / "frontend"), _listed(registered))


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


def test_base_session_env_drops_no_color_for_agents_but_keeps_it_for_shells() -> None:
    environment = {
        "NO_COLOR": "1",  # ambient pollution: daemon relaunched inside an agent session
        "FORCE_COLOR": "3",
        "CLICOLOR_FORCE": "1",
        "CLAUDECODE": "1",  # always scrubbed, regardless of backend
        "PATH": r"C:\Windows",
    }
    # Agent panes force colour, so an inherited NO_COLOR (which Codex obeys over
    # CLICOLOR_FORCE) is removed; parent-Claude markers go too.
    for agent in ("claude", "codex", "omp"):
        env = base_session_env(environment, agent)
        assert "NO_COLOR" not in env
        assert "FORCE_COLOR" not in env
        assert "CLICOLOR_FORCE" not in env
        assert "CLAUDECODE" not in env
        assert env["PATH"] == r"C:\Windows"
    # A plain shell keeps honouring an inherited NO_COLOR (no-color.org).
    shell = base_session_env(environment, "shell")
    assert shell["NO_COLOR"] == "1"
    assert "FORCE_COLOR" not in shell
    assert "CLICOLOR_FORCE" not in shell
    assert "CLAUDECODE" not in shell


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


def test_an_agent_backend_may_now_carry_a_launch_profile() -> None:
    """The contract stopped refusing this when launch profiles gained a backend.

    The refusal moved rather than disappearing: `resolve_agent_profile` is what
    rejects a profile whose backend does not match, because only it can see the
    profile. Parsing cannot, so parsing must not pretend to.
    """
    request = SpawnRequest.parse(
        {"backend": "claude", "project_id": "dev", "profile_id": "claude-plan"}
    )
    assert request.profile_id == "claude-plan"
    assert request.backend == "claude"
