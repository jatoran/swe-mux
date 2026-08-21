from __future__ import annotations

from pathlib import Path

import pytest

from swe_mux.spawn_contract import (
    MAX_SPAWN_ENV,
    MAX_SPAWN_MODEL_CHARS,
    SpawnRequest,
    apply_spawn_model,
    base_session_env,
    resolve_contained_cwd,
    resolve_listed_cwd,
    resolve_spawn_model,
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
        "CLAUDE_JOB_DIR": r"C:\Users\dev\.claude\jobs\ea1e4fd9",
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
    # An inherited job dir makes the CLI adopt that background job's identity
    # (name, shared scratch dir) in every pane, so it must never pass through.
    assert "CLAUDE_JOB_DIR" not in scrubbed
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


def test_spawn_contract_carries_seed_and_stage_text_separately() -> None:
    seeded = SpawnRequest.parse(
        {"backend": "claude", "project_id": "dev", "seed_text": "run the tests"}
    )
    assert seeded.seed_text == "run the tests"
    assert seeded.stage_text is None
    staged = SpawnRequest.parse(
        {"backend": "claude", "project_id": "dev", "stage_text": "review this first"}
    )
    assert staged.stage_text == "review this first"
    assert staged.seed_text is None


@pytest.mark.parametrize(
    ("body", "field"),
    [
        ({"backend": "claude", "project_id": "dev", "stage_text": "   "}, "stage_text"),
        ({"backend": "claude", "project_id": "dev", "stage_text": 7}, "stage_text"),
        (
            {"backend": "claude", "project_id": "dev", "stage_text": "x" * 500_001},
            "stage_text",
        ),
        # One runs the prompt, the other deliberately does not; both together
        # would run one prompt with another parked on top of it.
        (
            {
                "backend": "claude",
                "project_id": "dev",
                "seed_text": "run this",
                "stage_text": "stage this",
            },
            "stage_text",
        ),
    ],
)
def test_spawn_contract_rejects_bad_stage_text(body: dict[str, object], field: str) -> None:
    with pytest.raises(ValueError) as error:
        SpawnRequest.parse(body)
    assert field in error.value.args[0]


def test_spawn_contract_carries_a_requested_model() -> None:
    request = SpawnRequest.parse(
        {"backend": "claude", "project_id": "dev", "model": "  opus  "}
    )
    assert request.model == "opus"
    assert SpawnRequest.parse({"backend": "claude", "project_id": "dev"}).model is None


@pytest.mark.parametrize(
    "body",
    [
        {"backend": "claude", "project_id": "dev", "model": "   "},
        {"backend": "claude", "project_id": "dev", "model": 7},
        {
            "backend": "claude",
            "project_id": "dev",
            "model": "x" * (MAX_SPAWN_MODEL_CHARS + 1),
        },
        # A shell has no model to choose, and the contract knows that much without
        # the Project defaults the spawn handler resolves.
        {"backend": "shell", "project_id": "dev", "model": "opus"},
    ],
)
def test_spawn_contract_rejects_a_bad_model(body: dict[str, object]) -> None:
    with pytest.raises(ValueError) as error:
        SpawnRequest.parse(body)
    assert "model" in error.value.args[0]


def test_a_model_resolves_to_the_spelling_its_cli_is_given() -> None:
    """Speech arrives as words; the CLI is handed one canonical token.

    Both directions matter: an alias the CLI accepts is passed through untouched,
    and a spoken family-plus-version becomes the harness's own id, so the card the
    operator confirms and the argv the CLI receives say the same thing.
    """
    assert resolve_spawn_model("claude", "Opus") == "opus"
    assert resolve_spawn_model("claude", "opus 5") == "claude-opus-5"
    assert resolve_spawn_model("claude", "claude opus 5") == "claude-opus-5"
    assert resolve_spawn_model("codex", "GPT-5.1-Codex") == "gpt-5.1-codex"


def test_a_model_meant_for_another_harness_is_refused_by_name() -> None:
    """The failure this exists to prevent: `codex --model opus`, a dead pane.

    Codex declares no aliases precisely because it has none, so a Claude family
    name reaching it is recognizable as wrong before anything spawns - and the
    refusal names what Codex does take, so the next attempt can be right.
    """
    with pytest.raises(ValueError) as error:
        resolve_spawn_model("codex", "opus")
    message = error.value.args[0]["model"]
    assert "does not recognize" in message and "gpt-" in message


def test_a_harness_with_no_measured_model_argument_refuses_and_says_where_to_set_one() -> None:
    for harness in ("omp", "pi", "opencode"):
        with pytest.raises(ValueError) as error:
            resolve_spawn_model(harness, "anything")
        assert "launch profile" in error.value.args[0]["model"]


def test_a_model_that_would_read_as_a_flag_is_never_accepted() -> None:
    """The value becomes an argv token beside a flag; it must not become a flag."""
    for hostile in ("--dangerously-skip-permissions", "-m", "--model", "--settings x"):
        with pytest.raises(ValueError):
            resolve_spawn_model("claude", hostile)


def test_the_requests_model_replaces_the_one_the_earlier_slots_set() -> None:
    """"The request wins" has to be a fact, not two flags and a coin toss.

    Both spellings a profile could have used are replaced: the flag-with-value
    pair, and Codex's generic `-c model=…` config override, whose introducing
    flag would otherwise be left dangling with no value behind it.
    """
    assert apply_spawn_model(
        "claude", ["--model", "sonnet", "--verbose"], "opus"
    ) == ["--verbose", "--model", "opus"]
    assert apply_spawn_model("claude", ["--model=sonnet"], "opus") == ["--model", "opus"]
    assert apply_spawn_model(
        "codex", ["-c", "model=gpt-4", "--sandbox", "read-only"], "gpt-5.1"
    ) == ["--sandbox", "read-only", "--model", "gpt-5.1"]
    # A neighbouring config key that merely starts the same way is left alone.
    assert apply_spawn_model(
        "codex", ["-c", "model_reasoning_effort=high"], "gpt-5.1"
    ) == ["-c", "model_reasoning_effort=high", "--model", "gpt-5.1"]


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
