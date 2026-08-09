from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from swe_mux.harness import (
    AGENT_BACKENDS,
    HARNESSES,
    HarnessLevel,
    agent_harnesses,
    delivers_prompts_through_pty,
    descriptor,
    external_usage_harnesses,
    harnesses_at_least,
    has_observable_transcript,
    is_agent_harness,
    needs_resize_repaint,
    provider_account_harnesses,
    public_harness_registry,
    reports_lifecycle_hooks,
)


def test_registry_keys_match_descriptor_names() -> None:
    assert HARNESSES
    assert all(key == harness.name for key, harness in HARNESSES.items())


def test_observed_harnesses_declare_normalized_events() -> None:
    assert all(
        harness.normalized_events
        for harness in HARNESSES.values()
        if harness.level >= HarnessLevel.observed
    )


def test_observed_and_hooked_harnesses_have_runtime_integrations() -> None:
    from swe_mux.adapters import HOOK_INSTALLER_FAMILIES
    from swe_mux.observation import TRANSCRIPT_CLASSIFIER_BACKENDS

    for harness in HARNESSES.values():
        if harness.level >= HarnessLevel.observed:
            assert harness.name in TRANSCRIPT_CLASSIFIER_BACKENDS
        if harness.level >= HarnessLevel.hooked:
            assert harness.adapter_family in HOOK_INSTALLER_FAMILIES


def test_delivery_etiquette_is_complete() -> None:
    expected = {"submission", "root_completion", "screen"}
    assert all(set(harness.delivery_etiquette()) == expected for harness in HARNESSES.values())


def test_registry_accessors_preserve_declaration_order() -> None:
    assert agent_harnesses() == tuple(HARNESSES)
    assert AGENT_BACKENDS == frozenset(HARNESSES)
    assert descriptor("claude") is HARNESSES["claude"]
    assert harnesses_at_least("managed") == ("claude", "codex", "omp")


def test_capability_queries_fail_closed_for_non_harnesses() -> None:
    assert is_agent_harness("claude")
    assert has_observable_transcript("codex")
    assert delivers_prompts_through_pty("claude")
    assert reports_lifecycle_hooks("codex")

    assert not is_agent_harness("shell")
    assert not has_observable_transcript("shell")
    assert not delivers_prompts_through_pty("shell")
    assert not reports_lifecycle_hooks("shell")


def test_agent_and_managed_provider_consumers_share_the_registry(tmp_path: Path) -> None:
    from swe_mux import session, voice
    from swe_mux.config import Config
    from swe_mux.event_bus import EventBus
    from swe_mux.provider_accounts import PROVIDERS
    from swe_mux.usage import UsageManager

    assert session.AGENT_BACKENDS is AGENT_BACKENDS
    assert voice.has_observable_transcript is has_observable_transcript
    assert PROVIDERS == provider_account_harnesses() == ("claude", "codex")
    usage = UsageManager(Config(data_dir=tmp_path), EventBus())
    assert tuple(usage.states) == external_usage_harnesses() == ("claude", "codex")


def test_agent_shims_cover_the_registry_and_keep_the_content_guard(tmp_path: Path) -> None:
    from swe_mux.config import Config
    from swe_mux.launchers import create_agent_shims
    from swe_mux.shim_paths import is_mux_shim

    env = create_agent_shims(Config(data_dir=tmp_path), None)

    for harness in agent_harnesses():
        shim = tmp_path / "bin" / f"{harness}.cmd"
        prefix = f"MUX_{harness.upper().replace('-', '_')}"
        assert shim.is_file()
        assert is_mux_shim(shim)
        assert f"{prefix}_EXE" in env
        assert f"{prefix}_ARGS" in env


def test_current_harness_capabilities_match_measured_sources() -> None:
    claude = descriptor("claude")
    codex = descriptor("codex")
    omp = descriptor("omp")

    assert (claude.executable, claude.default_args) == ("claude.exe", ())
    assert (codex.executable, codex.default_args) == ("codex.exe", ())
    assert claude.state_sources == ("transcript", "hook", "pty", "cli_state")
    assert codex.state_sources == ("transcript", "hook", "pty")
    assert claude.hook_ordering_guarantee is False
    assert codex.hook_ordering_guarantee is False
    assert omp.display_name == "oh-my-pi"
    assert omp.executable == "omp"
    assert omp.level == HarnessLevel.managed
    assert omp.state_sources == ("hook", "transcript", "pty")
    assert omp.measurement_source == "transcript"
    assert omp.assigns_conversation_id is False
    assert omp.resolves_transcript_by_cwd is True
    assert omp.hook_ordering_guarantee is True
    assert omp.provider_account_management is False
    # The renderer trait: alternate-screen Claude never rewrites scrollback, while
    # Codex reflows its transcript on resize and OMP repaints its tail continuously,
    # so `auto` keeps those two on the DOM renderer.
    assert claude.repaints_scrollback is False
    assert codex.repaints_scrollback is True
    assert omp.repaints_scrollback is True


def test_only_alternate_screen_harnesses_need_a_repaint_after_a_resize() -> None:
    """The two repaint traits answer opposite questions and must not be conflated.

    `repaints_scrollback` asks whether a harness floods the ring and can therefore
    replay to an empty-looking screen; `needs_resize_repaint` asks whether it can
    repair its own screen after a width change. Claude is False for the first and True
    for the second, which is exactly the pairing a single flag would have lost.
    """
    assert needs_resize_repaint("claude") is True
    assert needs_resize_repaint("codex") is False
    assert needs_resize_repaint("omp") is False
    # Shells and unknown names are not agent screens and are never pulsed.
    assert needs_resize_repaint("shell") is False
    assert needs_resize_repaint(None) is False
    assert {name for name in HARNESSES if needs_resize_repaint(name)} == {
        name for name, harness in HARNESSES.items() if harness.screen == "alternate"
    }


def test_harness_level_is_derived_from_capability_axes() -> None:
    base = descriptor("claude")

    assert replace(base, state_sources=(), measurement_source="none").level == (
        HarnessLevel.launchable
    )
    assert replace(base, state_sources=(), measurement_source="transcript").level == (
        HarnessLevel.identified
    )
    assert replace(base, state_sources=("transcript",), measurement_source="none").level == (
        HarnessLevel.observed
    )
    assert replace(base, state_sources=("hook",), measurement_source="none").level == (
        HarnessLevel.hooked
    )
    assert replace(
        base,
        state_sources=("hook", "transcript"),
        measurement_source="transcript",
    ).level == HarnessLevel.managed


def test_public_registry_exposes_frontend_capability_gates() -> None:
    payload = public_harness_registry()
    assert payload["version"] == 1
    items = {item["name"]: item for item in payload["harnesses"]}  # type: ignore[index]
    assert set(items) == set(HARNESSES)
    assert items["claude"]["display_name"] == "Claude Code"
    assert items["codex"]["level"] == "managed"
    assert items["omp"]["level"] == "managed"
    assert items["claude"]["capabilities"]["transcript"] is True
    assert items["claude"]["capabilities"]["repaints_scrollback"] is False
    assert items["omp"]["capabilities"]["repaints_scrollback"] is True
