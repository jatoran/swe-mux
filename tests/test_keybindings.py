"""The keymap: chord syntax, rule resolution, and what each host can deliver."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from swe_mux.config import Config
from swe_mux.keybindings import (
    COMMAND_IDS,
    KEYBINDING_COMMANDS,
    Rule,
    document_for,
    is_command,
    migrate_command,
    normalize_rule,
    normalize_when,
    parse_document,
    prefixes,
    resolve,
)
from swe_mux.keychords import (
    altgr_hazard,
    chord_policy,
    deliverable,
    normalize_chord,
    normalize_sequence,
    sequence_label,
    sequence_warnings,
    token_for_code,
)
from swe_mux.keymaps import DEFAULT_PRESET, preset_rules, preset_summaries
from swe_mux.routes.settings import _keybindings_payload

# ------------------------------------------------------------------ chord syntax


@pytest.mark.parametrize(
    ("code", "token"),
    [
        ("KeyA", "a"),
        ("KeyZ", "z"),
        ("Digit0", "0"),
        ("Digit9", "9"),
        ("Minus", "-"),
        ("BracketLeft", "["),
        ("Slash", "/"),
        ("Space", "space"),
        ("ArrowLeft", "arrowleft"),
        ("F12", "f12"),
        ("Numpad5", "numpad5"),
        ("ControlLeft", None),
        ("", None),
        ("Unrecognised", None),
    ],
)
def test_a_code_maps_to_exactly_one_token(code: str, token: str | None) -> None:
    assert token_for_code(code) == token


def test_modifiers_are_ordered_and_deduplicated() -> None:
    assert normalize_chord("SHIFT + Ctrl + K", require_modifier=True) == "ctrl+shift+k"
    with pytest.raises(ValueError, match="duplicate modifier"):
        normalize_chord("ctrl+ctrl+k", require_modifier=True)
    with pytest.raises(ValueError, match="unknown modifier"):
        normalize_chord("hyper+k", require_modifier=True)


def test_only_the_first_chord_of_a_sequence_needs_an_intercept_modifier() -> None:
    """The whole practical argument for a prefix key, asserted rather than assumed.

    A leader costs one interceptable chord and everything after it is a plain key
    the browser and the window manager never compete for. If a later chord had to
    carry a modifier too, 200 commands would need 200 reservations again.
    """
    assert normalize_sequence("ctrl+shift+space p n") == "ctrl+shift+space p n"
    with pytest.raises(ValueError, match="first chord"):
        normalize_sequence("p n")
    with pytest.raises(ValueError, match="first chord"):
        normalize_sequence("shift+p")


def test_a_function_key_may_lead_a_binding_unmodified() -> None:
    """F1 shadows nothing anybody types, which is why VS Code Web uses it."""
    assert normalize_sequence("f1") == "f1"
    with pytest.raises(ValueError, match="first chord"):
        normalize_sequence("escape")


def test_a_sequence_is_capped() -> None:
    assert normalize_sequence("ctrl+x p f") == "ctrl+x p f"
    with pytest.raises(ValueError, match="at most 3"):
        normalize_sequence("ctrl+x p f g")


def test_a_chord_names_a_key_rather_than_a_character() -> None:
    """There is no `+` token: the key a US layout prints `+` on is `=`."""
    assert normalize_chord("ctrl+=", require_modifier=True) == "ctrl+="
    with pytest.raises(ValueError, match="unknown key"):
        normalize_chord("ctrl++", require_modifier=True)


def test_labels_follow_the_platform() -> None:
    assert sequence_label("ctrl+shift+space p", platform="win") == "Ctrl+Shift+Space P"
    # Apple's documented order is ⌃⌥⇧⌘, so the label is not the storage order.
    assert sequence_label("ctrl+shift+alt+meta+p", platform="mac") == "⌃⌥⇧⌘P"


# ------------------------------------------------------------------ delivery


def test_altgr_hazard_is_exactly_ctrl_plus_alt() -> None:
    """The bug the old defaults shipped: 24 of 26 were `ctrl+alt+<key>`.

    Windows and X11 synthesise Ctrl+Alt for AltGr, so every one of them fired
    while a German, French, Polish, Spanish or Nordic user typed a character.
    """
    assert altgr_hazard("ctrl+alt+n")
    assert altgr_hazard("ctrl+shift+alt+n")
    assert not altgr_hazard("ctrl+shift+n")
    assert not altgr_hazard("ctrl+alt+meta+n")


def test_no_shipped_preset_binds_a_ctrl_alt_chord() -> None:
    for summary in preset_summaries():
        for rule in preset_rules(summary["id"]):
            for chord in rule.keys.split(" "):
                assert not altgr_hazard(chord), f"{summary['id']} binds {rule.keys}"


def test_the_browser_keeps_some_chords_and_the_desktop_app_does_not() -> None:
    assert not deliverable("ctrl+t", host="browser", platform="win")
    assert deliverable("ctrl+t", host="desktop", platform="win")


def test_a_contested_chord_is_offered_with_its_cost_rather_than_refused() -> None:
    """The correction that motivated the rewrite.

    `ctrl+f` was refused as browser-reserved while `Settings.tsx` was calling
    `preventDefault` on Ctrl+F successfully in the same browser. A chord the page
    receives is bindable; it just costs the user the browser's own meaning.
    """
    warnings = sequence_warnings("ctrl+f", host="browser", platform="win")
    assert deliverable("ctrl+f", host="browser", platform="win")
    assert any(
        warning.scope == "browser" and warning.severity == "contested" for warning in warnings
    )


def test_the_linux_desktop_takes_the_chords_the_old_defaults_used() -> None:
    for chord in ("ctrl+alt+t", "ctrl+alt+arrowleft", "ctrl+alt+arrowright"):
        assert not deliverable(chord, host="browser", platform="linux")
        assert deliverable(chord, host="browser", platform="win")


def test_only_the_leading_chord_is_judged_against_the_host() -> None:
    """A leader makes an unreachable chord reachable as a second keystroke."""
    assert not deliverable("ctrl+w", host="browser", platform="win")
    assert deliverable("ctrl+shift+space ctrl+w", host="browser", platform="win")


def test_ui_scale_chords_are_the_one_remaining_hard_refusal() -> None:
    for chord in chord_policy()["application_reserved"]:  # type: ignore[index]
        assert not deliverable(str(chord), host="desktop", platform="win")


# ------------------------------------------------------------------ rules


def test_a_rule_validates_its_command_host_platform_and_when() -> None:
    rule = normalize_rule({
        "keys": "CTRL+SHIFT+K", "command": "palette.open",
        "host": "desktop", "when": "!terminalFocused",
    })
    assert rule == Rule(
        keys="ctrl+shift+k", command="palette.open", host="desktop", when="!terminalFocused"
    )
    with pytest.raises(ValueError, match="unknown command id"):
        normalize_rule({"keys": "ctrl+shift+k", "command": "not.a.command"})
    with pytest.raises(ValueError, match="unknown host"):
        normalize_rule({"keys": "ctrl+shift+k", "command": "palette.open", "host": "phone"})
    with pytest.raises(ValueError, match="unknown platform"):
        normalize_rule({"keys": "ctrl+shift+k", "command": "palette.open", "platform": "bsd"})


def test_the_when_grammar_is_a_closed_conjunction() -> None:
    assert normalize_when("terminalFocused&&!paletteOpen") == "terminalFocused && !paletteOpen"
    assert normalize_when(None) == ""
    with pytest.raises(ValueError, match="unknown when flag"):
        normalize_when("somethingElse")
    with pytest.raises(ValueError, match="malformed when term"):
        normalize_when("terminalFocused || paletteOpen")


def test_later_rules_win_and_an_empty_command_erases() -> None:
    rules = [
        normalize_rule({"keys": "ctrl+shift+k", "command": "palette.open"}),
        normalize_rule({"keys": "ctrl+shift+k", "command": "settings.open"}),
        normalize_rule({"keys": "ctrl+shift+j", "command": "history.open"}),
        normalize_rule({"keys": "ctrl+shift+j", "command": ""}),
    ]
    resolved = resolve(rules, host="desktop", platform="win")
    assert resolved.flat() == {"ctrl+shift+k": "settings.open"}


def test_a_host_or_platform_scoped_rule_reaches_only_that_host() -> None:
    rules = [
        normalize_rule({"keys": "ctrl+t", "command": "session.spawnShell", "host": "desktop"}),
        normalize_rule({"keys": "meta+shift+p", "command": "palette.open", "platform": "mac"}),
    ]
    assert resolve(rules, host="desktop", platform="win").flat() == {"ctrl+t": "session.spawnShell"}
    assert resolve(rules, host="browser", platform="win").flat() == {}
    # Stored in one fixed modifier order so a chord has exactly one spelling; the
    # macOS reader is still shown ⌘⇧P, which is Apple's own order (`_LABEL_ORDER`).
    assert resolve(rules, host="browser", platform="mac").flat() == {"shift+meta+p": "palette.open"}
    assert sequence_label("shift+meta+p", platform="mac") == "⇧⌘P"


def test_two_scopes_share_one_chord_and_the_specific_one_sorts_last() -> None:
    rules = [
        normalize_rule({"keys": "ctrl+shift+f", "command": "note.find", "when": "editorFocused"}),
        normalize_rule({"keys": "ctrl+shift+f", "command": "terminal.find"}),
    ]
    entries = resolve(rules, host="desktop", platform="win").bindings["ctrl+shift+f"]
    assert [entry["command"] for entry in entries] == ["terminal.find", "note.find"]


def test_a_chord_cannot_both_fire_and_arm() -> None:
    """Resolving in favour of the leaf would delete the subtree behind it silently."""
    rules = [
        normalize_rule({"keys": "ctrl+b p", "command": "tab.previous"}),
        normalize_rule({"keys": "ctrl+b p n", "command": "pane.next"}),
    ]
    resolved = resolve(rules, host="desktop", platform="win")
    assert "ctrl+b p" not in resolved.bindings
    assert resolved.bindings["ctrl+b p n"][0]["command"] == "pane.next"
    assert any(item["keys"] == "ctrl+b p" for item in resolved.undeliverable)


def test_prefixes_are_every_chord_that_only_ever_arms() -> None:
    rules = [normalize_rule({"keys": "ctrl+x p f", "command": "palette.files"})]
    assert prefixes(rules) == {"ctrl+x", "ctrl+x p"}


# ------------------------------------------------------------------ registry


def test_a_retired_command_id_still_resolves() -> None:
    """A keybindings file is durable and can be arbitrarily old."""
    for retired, survivor in {
        "drawer.resetTabs": "drawer.resetLayout",
        "drawer.git.land": "drawer.git.map",
        # Registered and implemented nowhere before 2026-08-30, so a binding made
        # to it did nothing at all; the registry that opens is `project.create`.
        "projects.open": "project.create",
    }.items():
        assert retired not in COMMAND_IDS
        assert migrate_command(retired) == survivor
        assert is_command(retired)


def test_generated_command_families_are_bindable_by_exact_id() -> None:
    for command in (
        "project.activate(3)",
        "tab.activate(7)",
        "session.focus:abc-123",
        "drawer.show:git",
        "help.topic.keybindings",
    ):
        assert is_command(command)
    assert not is_command("session.focus:")
    assert not is_command("project.activate(0)")


def test_the_navigation_vocabulary_a_grid_needs_exists() -> None:
    """`pane.next` alone is unusable past two panes; this is the gap it left."""
    for direction in ("Left", "Right", "Up", "Down"):
        assert f"pane.focus{direction}" in COMMAND_IDS
        assert f"pane.swap{direction}" in COMMAND_IDS
        assert f"pane.resize{direction}" in COMMAND_IDS
        assert f"pane.moveTab{direction}" in COMMAND_IDS
    for command in (
        "focus.terminal", "focus.sidebar", "focus.drawer", "focus.tabBar", "focus.composer",
        "focus.next", "focus.previous",
        "session.nextInProject", "session.previousInProject",
        "palette.commands", "palette.sessions", "palette.projects", "palette.files",
        "pane.close",
    ):
        assert command in COMMAND_IDS


def test_every_registered_category_has_a_group() -> None:
    from swe_mux.keybindings import CATEGORIES

    assert {category for _, _, category in KEYBINDING_COMMANDS} <= set(CATEGORIES)


# ------------------------------------------------------------------ presets


def test_every_shipped_preset_loads_and_resolves_on_every_host() -> None:
    summaries = preset_summaries()
    assert {summary["id"] for summary in summaries} == {"swemux", "tmux", "vscode", "vim", "emacs"}
    for summary in summaries:
        rules = preset_rules(str(summary["id"]))
        assert rules
        for host in ("desktop", "browser"):
            for platform in ("win", "mac", "linux"):
                resolved = resolve(rules, host=host, platform=platform)
                assert resolved.bindings
                # A preset that ships an undeliverable binding is a preset with a
                # dead chord in it; every one that cannot reach a host is scoped
                # away from that host in the JSON instead.
                assert resolved.undeliverable == [], f"{summary['id']} on {host}/{platform}"


def test_every_preset_carries_the_shared_leader_tree() -> None:
    """Choosing tmux adds tmux; it never removes the route to something tmux has
    no opinion about."""
    for summary in preset_summaries():
        rules = preset_rules(str(summary["id"]))
        keys = {rule.keys for rule in rules}
        assert "ctrl+shift+space p n" in keys, summary["id"]
        assert "ctrl+shift+space g m" in keys, summary["id"]


def test_the_default_keymap_reaches_almost_every_command() -> None:
    """The point of a leader, measured rather than asserted in prose.

    A flat map would need one reservation per command; the tree needs one for all of
    them. What is left out is left out on purpose and is listed here, so a command
    that quietly loses its chord fails this instead of being discovered by a user.
    """
    resolved = resolve(preset_rules(DEFAULT_PRESET), host="desktop", platform="win")
    reached = {entry["command"] for entries in resolved.bindings.values() for entry in entries}
    assert COMMAND_IDS - reached == {
        # A fourth chord level, and each is one keystroke from its own tab.
        "drawer.actions.prompts", "drawer.actions.quick", "drawer.actions.skills",
        "drawer.activity.changes", "drawer.activity.findings", "drawer.activity.timeline",
        "drawer.agent.config", "drawer.agent.instructions", "drawer.agent.tools",
        # Gestures, on surfaces with no keyboard.
        "mobileTab.next", "mobileTab.previous", "settings.navToggle", "settings.navClose",
        # Clicking the row is what this is.
        "session.open",
    }


def test_a_preset_that_takes_something_says_so() -> None:
    by_id = {summary["id"]: summary for summary in preset_summaries()}
    assert by_id["swemux"]["warning"] == ""
    for name in ("tmux", "vscode", "vim", "emacs"):
        assert by_id[name]["warning"], name
    assert "Ctrl+A" in str(by_id["tmux"]["warning"])


def test_the_default_preset_is_what_an_install_with_no_document_gets(tmp_path: Path) -> None:
    payload = _keybindings_payload(Config(data_dir=tmp_path), host="browser", platform="win")
    assert payload["preset"] == DEFAULT_PRESET
    assert payload["resolved"]["ctrl+shift+p"] == [{"command": "palette.open", "when": ""}]


# ------------------------------------------------------------------ documents


def test_a_document_round_trips_through_its_own_serializer(tmp_path: Path) -> None:
    rules = [normalize_rule({"keys": "ctrl+shift+k", "command": "palette.open", "host": "desktop"})]
    document = document_for("custom", rules)
    (tmp_path / "keybindings.json").write_text(json.dumps(document), encoding="utf-8")
    written = (tmp_path / "keybindings.json").read_text(encoding="utf-8")
    preset, parsed, rejected = parse_document(json.loads(written))
    assert (preset, parsed, rejected) == ("custom", rules, {})


def test_a_rule_this_build_cannot_use_is_reported_not_fatal(tmp_path: Path) -> None:
    (tmp_path / "keybindings.json").write_text(
        json.dumps({
            "version": 3,
            "preset": "custom",
            "rules": [
                {"keys": "ctrl+shift+k", "command": "palette.open"},
                {"keys": "ctrl+0", "command": "settings.open"},
                {"keys": "ctrl+shift+j", "command": "gone.forever"},
            ],
        }),
        encoding="utf-8",
    )
    payload = _keybindings_payload(Config(data_dir=tmp_path), host="browser", platform="win")
    assert payload["resolved"] == {"ctrl+shift+k": [{"command": "palette.open", "when": ""}]}
    assert set(payload["rejected"]) == {"ctrl+0", "ctrl+shift+j"}


def test_the_payload_is_resolved_for_the_host_that_asked(tmp_path: Path) -> None:
    (tmp_path / "keybindings.json").write_text(
        json.dumps({
            "version": 3,
            "preset": "custom",
            "rules": [{"keys": "ctrl+t", "command": "session.spawnShell", "host": "desktop"}],
        }),
        encoding="utf-8",
    )
    config = Config(data_dir=tmp_path)
    assert _keybindings_payload(config, host="desktop", platform="win")["resolved"]
    assert _keybindings_payload(config, host="browser", platform="win")["resolved"] == {}
