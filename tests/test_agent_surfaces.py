"""The agent-surface model: two capability maps, one env value, one gate.

What these pin: the defaults (MCP and CLI on, absent-key semantics), the
canonical `MUX_SURFACES` spelling, the endpoint gate that makes "neither" a
state the daemon enforces (ROADMAP Phase 23 W4), and the two advisory
incoherences worth warning about - a skill with no capability behind it, and a
mute CLI capability nothing ever advertises.
"""

from __future__ import annotations

from types import SimpleNamespace

from swe_mux.agent_surfaces import (
    coherence_warnings,
    harness_surfaces,
    surface_gate,
    surfaces_env_value,
)
from swe_mux.harness import HARNESSES


def config(
    mcp: dict[str, bool] | None = None,
    cli: dict[str, bool] | None = None,
    skill: dict[str, bool] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        harness_mcp_enabled=mcp or {},
        harness_cli_enabled=cli or {},
        harness_skill_enabled=skill or {},
    )


def test_an_empty_config_grants_both_capabilities_to_every_agent_harness() -> None:
    fresh = config()
    for name in HARNESSES:
        assert harness_surfaces(fresh, name) == ("mcp", "cli")
        assert surfaces_env_value(fresh, name) == "mcp,cli"


def test_a_shell_holds_no_fleet_surfaces() -> None:
    assert harness_surfaces(config(), "shell") == ()
    assert surfaces_env_value(config(), "shell") == ""


def test_each_map_narrows_only_its_own_surface() -> None:
    both_off = config(mcp={"claude": False}, cli={"claude": False})
    assert surfaces_env_value(both_off, "claude") == ""
    assert surfaces_env_value(config(mcp={"claude": False}), "claude") == "cli"
    assert surfaces_env_value(config(cli={"claude": False}), "claude") == "mcp"
    # Another harness is untouched by claude's entries.
    assert surfaces_env_value(both_off, "codex") == "mcp,cli"


def test_the_gate_refuses_only_the_neither_state() -> None:
    gate = surface_gate(config(mcp={"claude": False}, cli={"claude": False}))
    assert gate("claude") is False
    assert gate("codex") is True
    # Non-agent backends were never surface-gated; refusing them here would
    # change an unrelated contract.
    assert gate("shell") is True


def test_a_skill_with_no_capability_behind_it_warns() -> None:
    incoherent = config(
        mcp={"claude": False}, cli={"claude": False}, skill={"claude": True}
    )
    warnings = coherence_warnings(incoherent)
    assert [w.code for w in warnings] == ["skill_without_capability"]
    assert warnings[0].backend == "claude"
    assert "fail" in warnings[0].message


def test_a_cli_only_surface_with_no_skill_warns_that_nothing_advertises_it() -> None:
    mute = config(mcp={"pi": False})
    warnings = [w for w in coherence_warnings(mute) if w.backend == "pi"]
    assert [w.code for w in warnings] == ["cli_without_instruction"]
    # The skill is exactly what clears it: the CLI becomes discoverable.
    taught = config(mcp={"pi": False}, skill={"pi": True})
    assert [w for w in coherence_warnings(taught) if w.backend == "pi"] == []


def test_the_coherent_combinations_are_silent() -> None:
    assert coherence_warnings(config()) == []
    assert coherence_warnings(config(skill={"claude": True})) == []
    off_entirely = config(
        mcp={name: False for name in HARNESSES},
        cli={name: False for name in HARNESSES},
    )
    assert coherence_warnings(off_entirely) == []
