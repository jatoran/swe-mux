"""One `--version` probe behind two surfaces.

There used to be two implementations with different timeouts, different TTLs, and
different resolution, so the same binary could be probed twice in one request and
answer differently. These pin the seam that replaced them: one subprocess per TTL
per resolved executable, shared by both callers, with each caller's own
presentation of the bytes left exactly as it was.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from swe_mux import agent_environment, cli_version, harness

CLAUDE_EXE = str(Path("C:/tools/claude.cmd") if Path("C:/").exists() else Path("/opt/bin/claude"))


@pytest.fixture(autouse=True)
def _clean_cache() -> None:
    cli_version.clear_cache()


@pytest.fixture
def probed(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Resolve every claude-ish name to one executable and record each probe of it."""
    runs: list[str] = []

    def fake_which_real(command: str) -> str | None:
        return CLAUDE_EXE if "claude" in command.casefold() else None

    def fake_run(resolved: str) -> cli_version.CliVersion:
        runs.append(resolved)
        return cli_version.CliVersion(resolved, 0, "1.2.3 (Claude Code)")

    monkeypatch.setattr("swe_mux.shim_paths.which_real", fake_which_real)
    monkeypatch.setattr(cli_version, "_run", fake_run)
    return runs


def test_a_second_probe_inside_the_ttl_runs_no_subprocess(probed: list[str]) -> None:
    first = cli_version.probe("claude")
    second = cli_version.probe("claude")

    assert first is not None and second is not None
    assert first.output == second.output == "1.2.3 (Claude Code)"
    assert probed == [CLAUDE_EXE]


def test_refresh_bypasses_the_cache_and_reprobes(probed: list[str]) -> None:
    cli_version.probe("claude")
    cli_version.probe("claude", refresh=True)

    assert probed == [CLAUDE_EXE, CLAUDE_EXE]


def test_an_expired_entry_reprobes(probed: list[str], monkeypatch: pytest.MonkeyPatch) -> None:
    clock = [1000.0]
    monkeypatch.setattr(cli_version.time, "monotonic", lambda: clock[0])

    cli_version.probe("claude")
    clock[0] += cli_version.CACHE_TTL_SECONDS - 1
    cli_version.probe("claude")
    assert probed == [CLAUDE_EXE]

    clock[0] += 2
    cli_version.probe("claude")
    assert probed == [CLAUDE_EXE, CLAUDE_EXE]


def test_clear_cache_forces_the_next_probe(probed: list[str]) -> None:
    cli_version.probe("claude")
    cli_version.clear_cache()
    cli_version.probe("claude")

    assert probed == [CLAUDE_EXE, CLAUDE_EXE]


def test_the_two_call_sites_share_one_probe_of_one_binary(probed: list[str]) -> None:
    """The DRY win: the registry and the inventory no longer each pay a subprocess."""
    registry = harness.probe_cli_version("claude")
    inventory = agent_environment.probe_cli_version("claude", CLAUDE_EXE)

    assert probed == [CLAUDE_EXE]
    # And each keeps the shape its consumers already depend on: the registry
    # compares a token against a tested bound, the inventory shows a person the
    # CLI's own line and uses it as part of an MCP catalog cache key.
    assert registry == "1.2.3"
    assert inventory == "1.2.3 (Claude Code)"


def test_an_unresolvable_name_answers_none_without_spawning(probed: list[str]) -> None:
    assert cli_version.probe("definitely-not-installed") is None
    assert cli_version.probe("   ") is None
    assert probed == []


def test_resolution_still_refuses_a_mux_shim(monkeypatch: pytest.MonkeyPatch) -> None:
    """`which_real`, never `shutil.which`: probing the shim would invoke the shim."""
    ran: list[str] = []
    monkeypatch.setattr("swe_mux.shim_paths.which_real", lambda command: None)
    monkeypatch.setattr(
        cli_version,
        "_run",
        lambda resolved: ran.append(resolved) or cli_version.CliVersion(resolved, 0, "x"),
    )

    assert harness.probe_cli_version("claude") is None
    assert agent_environment.probe_cli_version("claude", CLAUDE_EXE) is None
    assert ran == []


def test_a_nonzero_exit_is_a_version_to_the_registry_and_not_to_the_inventory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The one difference the two surfaces keep, and why each keeps it."""
    monkeypatch.setattr(
        "swe_mux.shim_paths.which_real",
        lambda command: CLAUDE_EXE if "claude" in command.casefold() else None,
    )
    monkeypatch.setattr(
        cli_version,
        "_run",
        lambda resolved: cli_version.CliVersion(resolved, 1, "2.0.0 (Claude Code)"),
    )

    # A CLI that prints its version and exits nonzero has still told the registry
    # its version; the inventory refuses it because a fingerprint drawn from a
    # failed run would change on every failure.
    assert harness.probe_cli_version("claude") == "2.0.0"
    assert agent_environment.probe_cli_version("claude", CLAUDE_EXE) is None


def test_a_banner_with_no_parseable_version_falls_back_to_its_first_line(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "swe_mux.shim_paths.which_real",
        lambda command: CLAUDE_EXE if "claude" in command.casefold() else None,
    )
    monkeypatch.setattr(
        cli_version,
        "_run",
        lambda resolved: cli_version.CliVersion(resolved, 0, "nightly build\nsecond line"),
    )

    assert harness.probe_cli_version("claude") == "nightly build"


def test_the_inventory_refuses_a_binary_the_session_did_not_call_its_harness(
    probed: list[str],
) -> None:
    assert agent_environment.probe_cli_version("claude", "/usr/bin/curl") is None
    assert probed == []


def test_agent_environment_clear_cache_also_clears_the_shared_probe(
    probed: list[str],
) -> None:
    agent_environment.probe_cli_version("claude", CLAUDE_EXE)
    agent_environment.clear_cache()
    agent_environment.probe_cli_version("claude", CLAUDE_EXE)

    assert probed == [CLAUDE_EXE, CLAUDE_EXE]
