from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

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
    # A store-backed dialect's sample is the row content rather than a file line:
    # the message JSON opencode stores, plus one part. `_write_opencode_sample`
    # turns it into a real database so the reader is exercised against SQL and not
    # against a hand-built record it would never see in production.
    "opencode": {
        "message": {"role": "assistant"},
        "parts": [{"type": "text", "text": "hello"}],
    },
}


def _write_opencode_sample(root: Path, session_id: str, sample: dict[str, object]) -> None:
    """Create a minimal `opencode.db` holding one conversation.

    Only the columns the reader selects, because a fixture that mirrored the whole
    schema would drift from opencode's own migrations while proving nothing extra.
    """
    import json as _json
    import sqlite3

    root.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(root / "opencode.db")
    try:
        connection.execute(
            "CREATE TABLE session (id TEXT PRIMARY KEY, time_updated INTEGER NOT NULL)"
        )
        connection.execute(
            "CREATE TABLE message (id TEXT PRIMARY KEY, session_id TEXT NOT NULL,"
            " time_created INTEGER NOT NULL, data TEXT NOT NULL)"
        )
        connection.execute(
            "CREATE TABLE part (id TEXT PRIMARY KEY, message_id TEXT NOT NULL,"
            " session_id TEXT NOT NULL, data TEXT NOT NULL)"
        )
        connection.execute("INSERT INTO session VALUES (?, ?)", (session_id, 1_700_000_000_000))
        connection.execute(
            "INSERT INTO message VALUES (?, ?, ?, ?)",
            ("msg_1", session_id, 1_700_000_000_000, _json.dumps(sample["message"])),
        )
        for index, part in enumerate(sample["parts"]):  # type: ignore[union-attr]
            connection.execute(
                "INSERT INTO part VALUES (?, ?, ?, ?)",
                (f"prt_{index}", "msg_1", session_id, _json.dumps(part)),
            )
        connection.commit()
    finally:
        connection.close()


def test_every_dialect_has_a_reader_that_actually_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A declared dialect must parse a representative record into a message.

    This is the guard the harness abstraction was missing. `transcript_view`
    dispatched on the harness *name* with a silent `else: return None`, so
    adding pi produced an empty Transcript tab with nothing failing anywhere -
    no test, no type error, no log line. Exhaustiveness in the code catches a
    missing branch; this catches a branch that exists but does not work.

    Keyed on the dialect rather than the harness so a harness that reuses an
    existing reader costs nothing here, while a harness that introduces a new
    dialect cannot ship until its reader parses something. A dialect whose records
    live in a store is exercised the same way, against a real database, because the
    question is identical: does the declared reader actually read.
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
        sample = _DIALECT_SAMPLE_RECORD[dialect]
        if descriptor(backend).conversation_store_file is not None:
            root = tmp_path / dialect
            _write_opencode_sample(root, "ses_sample", sample)
            monkeypatch.setenv("OPENCODE_DATA_DIR", str(root))
            messages = parse_transcript(None, backend, native_id="ses_sample")
        else:
            path = tmp_path / f"{dialect}.jsonl"
            path.write_text(_json.dumps(sample) + "\n", encoding="utf-8")
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


def test_managed_providers_are_the_registry_set() -> None:
    """The closed `ManagedProvider` literal must equal what the descriptors declare.

    This is what earns `provider_accounts.py` its module-wide exemption in
    `test_harness_name_literals.py`. Every path in that module writes or reads an
    authentication file, so a name reaching it without a branch would fall into
    whichever `else` was written first. Declaring `provider_account_management` on a
    third harness fails here; widening the literal to satisfy it then fails
    `assert_never` in `_provider_profile` until every provider-shaped branch handles
    it.
    """
    from typing import get_args

    from swe_mux.provider_accounts import PROVIDERS, ManagedProvider

    assert set(get_args(ManagedProvider)) == set(provider_account_harnesses())
    assert set(PROVIDERS) == set(get_args(ManagedProvider))


def test_conversation_discovery_is_declared_for_every_harness() -> None:
    """Finding a harness's past conversations is a capability, not an accident.

    The scanner used to hold a hardcoded two-vendor tuple, so omp, pi, and opencode
    were silently undiscoverable: their conversations existed on disk, were readable,
    and never reached History, with nothing reporting it. Declaring the axis makes a
    refusal visible; leaving it undeclared made an absence invisible.

    A harness may legitimately answer `None`, which states that mux does not index
    its past conversations. What it may not do is not answer.
    """
    from swe_mux.reconcile import _inspector

    for name, harness in HARNESSES.items():
        discovery = harness.conversation_discovery
        assert discovery is not None, (
            f"{name} does not declare how its past conversations are discovered; "
            f"declare `conversation_discovery`, using `None` with a stated reason if "
            f"mux deliberately does not index them"
        )
        if discovery.store:
            # A store discovery queries rows, so it needs the store to be named and
            # must declare no filesystem layout.
            assert harness.conversation_store_file is not None, name
            assert discovery.subdirectory is None and discovery.pattern is None, name
            continue
        # A filesystem discovery needs somewhere to look and a reader for what it
        # finds. A dialect with no inspector would walk the tree and discard it all.
        assert discovery.subdirectory and discovery.pattern, name
        assert harness.transcript_dialect is not None, name
        assert _inspector(name) is not None, name


def test_generated_frontend_registry_seed_is_current() -> None:
    """The browser's pre-snapshot seed must be generated, not hand-maintained.

    A hand-written copy is a second registry. The one this replaced had opencode two
    capability levels below its descriptor with measurement reported as absent, and
    pi missing its `pty` state source, because nothing compared the two.
    """
    from swe_mux.harness import FRONTEND_REGISTRY_SEED_PATH, frontend_registry_seed_source

    seed = Path(__file__).resolve().parent.parent.joinpath(*FRONTEND_REGISTRY_SEED_PATH)
    assert seed.is_file(), f"{seed} is missing; run packaging/generate_frontend_registry.py"
    assert seed.read_text(encoding="utf-8") == frontend_registry_seed_source(), (
        "the generated frontend harness registry is stale; run "
        "`uv run python packaging/generate_frontend_registry.py`"
    )


def test_every_browser_read_trait_travels_in_the_public_payload() -> None:
    """A per-harness trait the browser needs must be published, not recompiled there.

    The failure this guards is not a wrong value but a missing one: a trait absent
    from the payload has to be hardcoded in a frontend module, which is how a harness
    name ended up compiled into the terminal renderer and the command rail and then
    drifted from the descriptor that owned it.
    """
    payload = public_harness_registry()
    items = {item["name"]: item for item in payload["harnesses"]}  # type: ignore[index]
    required_capabilities = {
        "observed",
        "transcript",
        "measurement",
        "lifecycle_hooks",
        "pty_delivery",
        "external_usage",
        "provider_accounts",
        "repaints_scrollback",
        "assigns_conversation_id",
        "resolves_transcript_by_cwd",
        "branch",
        "webgl_unsafe",
        "owns_scroll_viewport",
        "width_envelope",
        "min_desktop_columns",
        "suppresses_late_color_response",
        "touch_scroll_rows_per_report",
        "touch_scroll_report_interval_ms",
    }
    for name, harness in HARNESSES.items():
        item = items[name]
        assert set(item["capabilities"]) == required_capabilities, name  # type: ignore[arg-type]
        assert item["cli_name"] == harness.script_base_name
        assert item["resume_argv"] == list(harness.resume_argv)
        assert item["skill_invocation_prefix"] == harness.skill_invocation_prefix


def test_conversation_id_argv_is_declared_rather_than_matched() -> None:
    """Recovering a conversation id from argv is the harness's own CLI grammar.

    Matching flag strings in the consumer recognized Claude and Codex only, so an
    omp, pi, or opencode pane spawned as a resume kept its placeholder id and nothing
    anywhere reported that it had.
    """
    from swe_mux.harness import native_id_from_args, resume_command

    assert native_id_from_args("claude", ["--session-id", "abc"]) == "abc"
    assert native_id_from_args("claude", ["--resume", "abc"]) == "abc"
    assert native_id_from_args("codex", ["resume", "abc"]) == "abc"
    assert native_id_from_args("omp", ["--resume", "abc"]) == "abc"
    assert native_id_from_args("pi", ["--session", "abc"]) == "abc"
    assert native_id_from_args("opencode", ["--session", "ses_1"]) == "ses_1"
    # A flag with no value after it, an unrelated argv, a shell, and a non-harness
    # all decline rather than inventing an id.
    assert native_id_from_args("pi", ["--session"]) is None
    assert native_id_from_args("pi", ["--model", "x"]) is None
    assert native_id_from_args("shell", ["--session", "abc"]) is None
    assert native_id_from_args(None, ["--session", "abc"]) is None

    # Only a harness that dictates the id declares spawn argv for one.
    for name, harness in HARNESSES.items():
        assert bool(harness.spawn_id_argv) == harness.assigns_conversation_id, name
        assert harness.resume_argv, name
        assert resume_command(name, "abc") == " ".join(
            (harness.script_base_name, *harness.resume_argv, "abc")
        )
    assert resume_command("shell", "abc") is None
    assert resume_command("claude", "") is None


def test_a_command_line_is_matched_to_a_harness_from_the_registry() -> None:
    """Recognizing an already-launched agent must cover every harness.

    The hardcoded form knew two vendors and answered ``None`` for the rest, which is
    a silent answer: the history repair that catches a row whose claimed backend
    disagrees with the executable actually launched simply never ran for them.
    """
    from swe_mux.harness import harness_for_command

    for name, harness in HARNESSES.items():
        base = harness.script_base_name
        assert harness_for_command(base, []) == name
        assert harness_for_command(f"C:/tools/{base}.cmd", []) == name
        assert harness_for_command(f"/usr/bin/{base.upper()}.EXE", []) == name
        if harness.npm_entrypoint is not None:
            package, suffix = harness.npm_entrypoint
            assert harness_for_command("node", [f"/x/node_modules/{package}bin{suffix}"]) == name

    assert harness_for_command("pwsh.exe", []) is None
    assert harness_for_command("", []) is None
    assert harness_for_command("node", ["/x/node_modules/@other/thing/cli.js"]) is None


def test_instruction_files_are_declared_for_every_harness_that_reads_one() -> None:
    """Root instruction discovery is a declared per-harness fact.

    Measured 2026-08-11 against the installed CLIs: oh-my-pi, pi, and opencode all
    read a root ``AGENTS.md``, and each keeps its user-level copy somewhere its
    config directory does not predict. Agent Context listed instruction sources for
    Claude and Codex only, so a change to a shared ``AGENTS.md`` looked as though it
    reached one harness when it reached four.
    """
    from swe_mux.harness import instruction_harnesses

    assert instruction_harnesses() == tuple(HARNESSES)
    assert descriptor("claude").instruction_file_name == "CLAUDE.md"
    assert {
        descriptor(name).instruction_file_name for name in HARNESSES if name != "claude"
    } == {"AGENTS.md"}
    # Every user-level path is distinct, which is why it is declared rather than
    # derived from `config_dir_name`.
    parts = [descriptor(name).global_instruction_parts for name in HARNESSES]
    assert all(item is not None for item in parts)
    assert len(set(parts)) == len(parts)
    for name, harness in HARNESSES.items():
        # A harness that names a file must say where the global copy lives, and the
        # two must agree on the filename.
        if harness.instruction_file_name is None:
            assert harness.global_instruction_parts is None, name
        else:
            assert harness.global_instruction_parts is not None, name
            assert harness.global_instruction_parts[-1] == harness.instruction_file_name, name


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
