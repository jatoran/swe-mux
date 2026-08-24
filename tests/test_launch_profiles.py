"""Launch profiles for agent harnesses.

A launch profile was a shell-only concept: one named executable/argv/environment
for `backend=shell`. The only per-harness argument list was the global
`harness_args`, so a Project could not offer "Claude" and "Claude (plan)" side by
side. These tests cover the generalization and, more importantly, the two refusals
that keep it safe: a profile may not set argv the adapter owns, and a profile may
not be applied to a backend it was not written for.
"""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from swe_mux import app_keys as keys
from swe_mux.adapters import ClaudeAdapter
from swe_mux.adapters.base import SpawnOptions
from swe_mux.config import Config, LaunchProfile
from swe_mux.event_bus import EventBus
from swe_mux.harness import reserved_launch_arg_conflict
from swe_mux.models import ProjectRecord
from swe_mux.profiles import resolve_agent_profile, resolve_profile
from swe_mux.routes.sessions import spawn_session


def agent_profile(
    profile_id: str = "claude-plan",
    *,
    backend: str = "claude",
    args: list[str] | None = None,
) -> LaunchProfile:
    return LaunchProfile(
        profile_id,
        "Claude (plan)",
        "",
        args if args is not None else ["--permission-mode", "plan"],
        {"CLAUDE_PROFILE": "plan"},
        marker="ag",
        capabilities=[],
        backend=backend,
    )


def shell_profile(profile_id: str = "pwsh") -> LaunchProfile:
    return LaunchProfile(profile_id, "PowerShell 7", "pwsh.exe", ["-NoLogo"], marker="ps7")


# --- resolution ---------------------------------------------------------------


def test_an_agent_profile_contributes_arguments_and_inherits_the_harness_executable() -> None:
    config = Config(shell_profiles=[agent_profile()], default_shell_profile="pwsh")

    resolved = resolve_agent_profile(config, "claude-plan", "claude")

    assert resolved.backend == "claude"
    assert resolved.argv == ("--permission-mode", "plan")
    assert resolved.env == {"CLAUDE_PROFILE": "plan"}
    # None rather than "": the caller falls back to `harness_exe`, and an empty
    # string would be handed to CreateProcess as a real (missing) executable.
    assert resolved.executable is None


def test_a_profile_is_refused_for_a_backend_it_was_not_written_for() -> None:
    config = Config(shell_profiles=[agent_profile(), shell_profile()])

    with pytest.raises(ValueError, match="starts claude, not codex"):
        resolve_agent_profile(config, "claude-plan", "codex")
    # And the reverse: the shell resolver builds an interactive command line, which
    # applied to an agent CLI starts neither one.
    with pytest.raises(ValueError, match="starts claude, not a shell"):
        resolve_profile(config, "claude-plan", Path.cwd())


@pytest.mark.skipif(os.name != "nt", reason="the platform gate only fires on Windows")
def test_a_profile_excluded_from_this_host_is_refused_like_a_shell_profile() -> None:
    """The same gate `resolve_profile` applies, applied to the same field.

    Kept identical rather than generalized: broadening it would refuse an existing
    `["windows"]` shell profile on Linux, which works today.
    """
    profile = agent_profile()
    profile.platforms = ["linux"]
    config = Config(shell_profiles=[profile])

    with pytest.raises(ValueError, match="unavailable on Windows"):
        resolve_agent_profile(config, "claude-plan", "claude")


def test_a_disabled_profile_is_refused_rather_than_silently_ignored() -> None:
    profile = agent_profile()
    profile.enabled = False
    config = Config(shell_profiles=[profile])

    with pytest.raises(ValueError, match="disabled"):
        resolve_agent_profile(config, "claude-plan", "claude")


@pytest.mark.parametrize(
    "backend,args,token",
    [
        ("claude", ["--session-id", "abc"], "--session-id"),
        ("claude", ["--settings", "mine.json"], "--settings"),
        ("claude", ["--resume"], "--resume"),
        ("claude", ["--mcp-config=other.json"], "--mcp-config"),
        ("codex", ["resume"], "resume"),
        ("codex", ["-c", 'notify=["me"]'], "notify="),
        ("codex", ["-c", "mcp_servers.mux.url=http://x"], "mcp_servers.mux."),
        ("pi", ["--session", "x"], "--session"),
        ("opencode", ["--session", "x"], "--session"),
        ("omp", ["--resume"], "--resume"),
    ],
)
def test_mux_owned_argv_is_refused_at_the_spawn_boundary(
    backend: str, args: list[str], token: str
) -> None:
    """The refusal is the point: a profile setting these fails silently otherwise.

    A profile carrying its own `--settings` replaces the file holding this pane's
    hook identity. The CLI still runs and the pane still looks healthy, but nothing
    ever reports a turn, so the session is unobserved for the rest of its life.
    """
    assert reserved_launch_arg_conflict(backend, args) == token
    config = Config(shell_profiles=[agent_profile("p", backend=backend, args=args)])

    with pytest.raises(ValueError, match="builds"):
        resolve_agent_profile(config, "p", backend)


def test_codex_keeps_its_own_config_overrides_available() -> None:
    """`-c` is reserved by key, not by flag: Codex takes arbitrary config pairs."""
    assert reserved_launch_arg_conflict("codex", ["-c", "model_reasoning_effort=high"]) is None
    config = Config(
        shell_profiles=[
            agent_profile("codex-hard", backend="codex", args=["-c", "model_reasoning_effort=high"])
        ]
    )

    resolved = resolve_agent_profile(config, "codex-hard", "codex")

    assert resolved.argv == ("-c", "model_reasoning_effort=high")


# --- composition --------------------------------------------------------------


def test_the_three_argument_slots_compose_least_specific_first(tmp_path: Path) -> None:
    """harness_args, then the profile, then whatever the launch itself asked for.

    The adapter already concatenated `default_args` before `opts.args`, so the
    profile only has to prepend into the second slot. Asserted through the real
    adapter rather than by inspecting the spawn body, because the ordering claim is
    about the command line the CLI finally receives.
    """
    adapter = ClaudeAdapter(
        default_exe="claude.exe", data_dir=tmp_path, default_args=["--global"]
    )

    spec = adapter.spawn_spec(
        "sid",
        SpawnOptions(cwd=tmp_path, args=["--from-profile", "--from-request"], session_id="sid"),
    )

    argv = list(spec.argv)
    assert argv.index("--global") < argv.index("--from-profile") < argv.index("--from-request")


async def test_a_launch_profile_reaches_an_agent_spawn_and_records_its_identity(
    tmp_path: Path,
) -> None:
    config = Config(shell_profiles=[agent_profile(), shell_profile()])
    captured: list[dict[str, Any]] = []

    async def spawn(**kwargs: Any) -> Any:
        captured.append(kwargs)
        return SimpleNamespace(record=SimpleNamespace(snapshot=lambda: kwargs))

    request = _spawn_request(
        tmp_path,
        config,
        spawn,
        {"backend": "claude", "project_id": "default", "profile_id": "claude-plan"},
    )

    await spawn_session(cast(Any, request))

    assert captured[0]["backend"] == "claude"
    assert captured[0]["args"] == ["--permission-mode", "plan"]
    assert captured[0]["extra_env"] == {}
    assert captured[0]["profile_env"] == {"CLAUDE_PROFILE": "plan"}
    # The record keeps which profile produced the session, so history can answer
    # "which launcher was this?" later. The column keeps its historical name.
    assert captured[0]["shell_profile_id"] == "claude-plan"


async def test_a_project_default_applies_when_the_launch_names_no_profile(
    tmp_path: Path,
) -> None:
    config = Config(shell_profiles=[agent_profile()])
    captured: list[dict[str, Any]] = []

    async def spawn(**kwargs: Any) -> Any:
        captured.append(kwargs)
        return SimpleNamespace(record=SimpleNamespace(snapshot=lambda: kwargs))

    request = _spawn_request(
        tmp_path,
        config,
        spawn,
        {"backend": "claude", "project_id": "default"},
        default_agent_profiles={"claude": "claude-plan"},
    )

    await spawn_session(cast(Any, request))

    assert captured[0]["args"] == ["--permission-mode", "plan"]
    assert captured[0]["shell_profile_id"] == "claude-plan"


async def test_a_broken_project_default_degrades_to_a_diagnostic_not_a_failed_spawn(
    tmp_path: Path,
) -> None:
    """A stale id in a shared repository file must not stop every session starting.

    This is the one place the resolution failure is tolerated, and only because it
    is a *default*. An explicitly requested `profile_id` still raises, which the
    next test asserts.
    """
    config = Config(shell_profiles=[agent_profile()])
    captured: list[dict[str, Any]] = []

    async def spawn(**kwargs: Any) -> Any:
        captured.append(kwargs)
        return SimpleNamespace(record=SimpleNamespace(snapshot=lambda: kwargs))

    request = _spawn_request(
        tmp_path,
        config,
        spawn,
        {"backend": "claude", "project_id": "default"},
        default_agent_profiles={"claude": "deleted-long-ago"},
    )
    events = request.app[keys.EVENTS].subscribe(name="test")

    await spawn_session(cast(Any, request))

    assert captured[0]["args"] == []
    assert captured[0]["shell_profile_id"] is None
    emitted = [events.get_nowait() for _ in range(events.qsize())]
    assert [event.type for event in emitted] == ["project_launch_profile_unavailable"]
    assert emitted[0].payload["profile_id"] == "deleted-long-ago"


async def test_an_explicitly_requested_missing_profile_still_refuses(tmp_path: Path) -> None:
    config = Config(shell_profiles=[agent_profile()])

    async def spawn(**kwargs: Any) -> Any:  # pragma: no cover - must never run
        raise AssertionError("a refused profile must not spawn anything")

    request = _spawn_request(
        tmp_path,
        config,
        spawn,
        {"backend": "claude", "project_id": "default", "profile_id": "deleted-long-ago"},
    )

    with pytest.raises(ValueError, match="unknown launch profile"):
        await spawn_session(cast(Any, request))


async def test_a_requested_model_replaces_the_profiles_and_precedes_the_seed_prompt(
    tmp_path: Path,
) -> None:
    """The fourth thing a launch can carry, and it *replaces* rather than appends.

    A profile pinning `--model` is supported, so a request naming its own model has
    to win outright: two `--model` flags on one command line is a per-CLI coin toss
    and the promised precedence would be a hope. The seed prompt stays last, because
    it is the positional the flags come before.
    """
    config = Config(
        shell_profiles=[agent_profile("claude-sonnet", args=["--model", "sonnet", "--verbose"])]
    )
    captured: list[dict[str, Any]] = []

    async def spawn(**kwargs: Any) -> Any:
        captured.append(kwargs)
        return SimpleNamespace(record=SimpleNamespace(snapshot=lambda: kwargs))

    request = _spawn_request(
        tmp_path,
        config,
        spawn,
        {
            "backend": "claude",
            "project_id": "default",
            "profile_id": "claude-sonnet",
            "model": "opus 5",
            "seed_text": "fix the tests",
        },
    )

    await spawn_session(cast(Any, request))

    args = captured[0]["args"]
    assert "sonnet" not in args
    assert args[:3] == ["--verbose", "--model", "claude-opus-5"]
    assert args[-1] == "fix the tests"


async def test_a_model_the_resolved_harness_cannot_take_refuses_the_spawn(
    tmp_path: Path,
) -> None:
    """The backstop under the assistant's card: no pane is ever started to die.

    The refusal is here rather than only in the assistant because the backend is
    only fully resolved at this layer - the Project record and its committed
    configuration both feed the chain - so this is the last point that knows which
    CLI would actually receive the flag.
    """
    config = Config(shell_profiles=[agent_profile()])

    async def spawn(**kwargs: Any) -> Any:  # pragma: no cover - must never run
        raise AssertionError("a refused model must not spawn anything")

    request = _spawn_request(
        tmp_path,
        config,
        spawn,
        {"backend": "codex", "project_id": "default", "model": "opus"},
    )

    with pytest.raises(ValueError, match="does not recognize"):
        await spawn_session(cast(Any, request))


# --- configuration ------------------------------------------------------------


def test_configuration_refuses_an_agent_profile_that_sets_reserved_argv(tmp_path: Path) -> None:
    from swe_mux.config import _validate

    config = Config(
        data_dir=tmp_path,
        shell_profiles=[
            shell_profile(),
            agent_profile("bad", args=["--settings", "mine.json"]),
        ],
        default_shell_profile="pwsh",
    )

    with pytest.raises(ValueError) as error:
        _validate(config)

    assert "shell_profiles.1.args" in error.value.args[0]


def test_the_global_terminal_default_cannot_name_an_agent_profile(tmp_path: Path) -> None:
    """Otherwise every plain `New terminal` in the product becomes unspawnable."""
    from swe_mux.config import _validate

    config = Config(
        data_dir=tmp_path,
        shell_profiles=[agent_profile()],
        default_shell_profile="claude-plan",
    )

    with pytest.raises(ValueError) as error:
        _validate(config)

    assert "default_shell_profile" in error.value.args[0]


def test_an_agent_profile_may_omit_its_executable_but_a_shell_profile_may_not(
    tmp_path: Path,
) -> None:
    from swe_mux.config import _validate

    _validate(
        Config(
            data_dir=tmp_path,
            shell_profiles=[shell_profile(), agent_profile()],
            default_shell_profile="pwsh",
        )
    )

    bare_shell = shell_profile("bare")
    bare_shell.executable = ""
    with pytest.raises(ValueError) as error:
        _validate(
            Config(
                data_dir=tmp_path,
                shell_profiles=[bare_shell],
                default_shell_profile="bare",
            )
        )
    assert "shell_profiles.0" in error.value.args[0]


def test_shell_only_fields_are_refused_on_an_agent_profile(tmp_path: Path) -> None:
    """`resolve_profile`'s WSL translation and PowerShell wrapper assume a shell.

    An agent launch never reaches either, so accepting the fields would store a
    setting that silently does nothing.
    """
    from swe_mux.config import _validate

    profile = agent_profile()
    profile.cwd_strategy = "wsl"
    profile.cwd_integration = True

    with pytest.raises(ValueError) as error:
        _validate(
            Config(
                data_dir=tmp_path,
                shell_profiles=[shell_profile(), profile],
                default_shell_profile="pwsh",
            )
        )

    assert "shell_profiles.1.cwd_strategy" in error.value.args[0]
    assert "shell_profiles.1.cwd_integration" in error.value.args[0]


def _spawn_request(
    tmp_path: Path,
    config: Config,
    spawn: Any,
    body: dict[str, Any],
    *,
    default_agent_profiles: dict[str, str] | None = None,
) -> Any:
    project = ProjectRecord("default", "Main", str(tmp_path), 0)
    project.default_agent_profiles = dict(default_agent_profiles or {})
    app = {
        keys.CONFIG: config,
        keys.EVENTS: EventBus(),
        keys.SESSIONS: SimpleNamespace(spawn=spawn),
        keys.PROJECTS: SimpleNamespace(projects={"default": project}),
    }

    class Request:
        def __init__(self) -> None:
            self.app = app

        async def json(self) -> dict[str, Any]:
            return body

    return Request()
