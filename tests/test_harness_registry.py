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
    replay_needs_repaint,
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
        # A harness is `observed` when *any* state source reports, so the
        # requirement is per-source rather than per-level: only a harness that
        # declares it reads a transcript owes a transcript classifier. opencode
        # reaches `observed` on hooks alone and keeps its conversations as rows
        # in `opencode.db`, so demanding one of it would force either a dead
        # branch or a false capability claim.
        if "transcript" in harness.state_sources:
            assert harness.name in TRANSCRIPT_CLASSIFIER_BACKENDS
        if harness.level >= HarnessLevel.observed:
            assert harness.state_sources
        if harness.level >= HarnessLevel.hooked:
            assert harness.adapter_family in HOOK_INSTALLER_FAMILIES


_DIALECT_SAMPLE_RECORD: dict[str, dict[str, object]] = {
    "claude": {
        "type": "assistant",
        "message": {"content": [{"type": "text", "text": "hello"}]},
    },
    "codex": {
        "type": "response_item",
        "payload": {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "hello"}],
        },
    },
    "pi": {
        "type": "message",
        "message": {"role": "assistant", "content": [{"type": "text", "text": "hello"}]},
    },
}


def test_every_dialect_has_a_reader_that_actually_reads(tmp_path: Path) -> None:
    """A declared dialect must parse a representative record into a message.

    This is the guard the harness abstraction was missing. `transcript_view`
    dispatched on the harness *name* with a silent `else: return None`, so
    adding pi produced an empty Transcript tab with nothing failing anywhere -
    no test, no type error, no log line. Exhaustiveness in the code catches a
    missing branch; this catches a branch that exists but does not work.

    Keyed on the dialect rather than the harness so a harness that reuses an
    existing reader costs nothing here, while a harness that introduces a new
    dialect cannot ship until its reader parses something.
    """
    import json as _json

    from swe_mux.transcript_view import parse_transcript

    dialects = {
        harness.transcript_dialect
        for harness in HARNESSES.values()
        if harness.transcript_dialect is not None
    }
    assert dialects, "no harness declares a transcript dialect"
    for dialect in sorted(dialects):
        assert dialect in _DIALECT_SAMPLE_RECORD, (
            f"dialect {dialect!r} has no sample record in this test; a new dialect "
            f"must add one so its reader is proven to work"
        )
        # Pick any harness declaring the dialect: the reader is chosen by
        # dialect, so which harness carries it must not matter.
        backend = next(
            name
            for name, harness in HARNESSES.items()
            if harness.transcript_dialect == dialect
        )
        path = tmp_path / f"{dialect}.jsonl"
        path.write_text(_json.dumps(_DIALECT_SAMPLE_RECORD[dialect]) + "\n", encoding="utf-8")
        messages = parse_transcript(path, backend)
        assert messages, f"{backend} ({dialect}) parsed no message from its own record shape"
        assert messages[0]["role"] == "assistant"


def test_native_id_shape_is_declared_per_harness_and_anchored() -> None:
    """Conversation-id shape is a harness deviation, not a global constant.

    A single hardcoded UUID pattern refused every opencode id (`ses_<base62>`)
    and returned without a word, so those sessions stayed on their placeholder
    id forever: no conversation identity, no history row, no resume. Nothing
    logged it, because a validation filter that rejects is indistinguishable
    from one that was never reached.
    """
    from swe_mux.harness import native_id_matches

    uuid = "019fedd9-76cf-7196-a2f4-a84d9eaadf27"
    assert native_id_matches("opencode", "ses_012e2258bffeDxM8MvkzWOVEOx")
    assert not native_id_matches("opencode", uuid)
    for name, harness in HARNESSES.items():
        if harness.name == "opencode":
            continue
        assert native_id_matches(name, uuid), name
        assert not native_id_matches(name, "ses_abc"), name

    # Anchored, because the value reaches file paths and database keys.
    assert not native_id_matches("opencode", "ses_../../evil")
    assert not native_id_matches("opencode", f"prefix ses_{'a' * 12} suffix")
    assert not native_id_matches("claude", f"junk{uuid}junk")
    # An unregistered backend never validates.
    assert not native_id_matches("shell", uuid)
    assert not native_id_matches(None, uuid)


def test_harnesses_without_a_dialect_declare_no_transcript_evidence() -> None:
    """`transcript_dialect=None` and transcript evidence are contradictory."""
    for harness in HARNESSES.values():
        if harness.transcript_dialect is None:
            assert "transcript" not in harness.state_sources, harness.name
            assert harness.measurement_source != "transcript", harness.name
            assert harness.transcript is None, harness.name
        else:
            # A declared dialect is only meaningful if something reads it.
            assert harness.transcript is not None, harness.name


def test_delivery_etiquette_is_complete() -> None:
    expected = {"submission", "root_completion", "screen"}
    assert all(set(harness.delivery_etiquette()) == expected for harness in HARNESSES.values())


def test_registry_accessors_preserve_declaration_order() -> None:
    assert agent_harnesses() == tuple(HARNESSES)
    assert AGENT_BACKENDS == frozenset(HARNESSES)
    assert descriptor("claude") is HARNESSES["claude"]
    # opencode reaches `managed` on hooks plus database measurements: the tier is
    # derived from the two capability axes, not from having a transcript.
    assert harnesses_at_least("managed") == ("claude", "codex", "omp", "pi", "opencode")


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


def test_only_alternate_screen_harnesses_need_a_repaint_after_a_windowed_replay() -> None:
    """The third repaint question, and the one the other two both answered wrongly.

    `repaints_scrollback` is False for Claude and `needs_resize_repaint` only fires on a
    geometry change, so a Claude pane whose bounded replay reconstructed to a partial
    frame had no repair at all until the user resized the window by hand.
    """
    assert replay_needs_repaint("claude") is True
    assert replay_needs_repaint("codex") is False
    assert replay_needs_repaint("omp") is False
    assert replay_needs_repaint("shell") is False
    assert replay_needs_repaint(None) is False
    assert {name for name in HARNESSES if replay_needs_repaint(name)} == {
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
