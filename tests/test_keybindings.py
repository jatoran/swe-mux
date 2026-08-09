from __future__ import annotations

import json
from pathlib import Path

import pytest

from swe_mux.config import Config
from swe_mux.keybindings import (
    DEFAULT_KEYBINDINGS,
    KEYBINDING_COMMANDS,
    keybinding_policy,
    normalize_binding,
)
from swe_mux.server import _keybindings_payload


def test_default_bindings_reference_valid_commands() -> None:
    assert DEFAULT_KEYBINDINGS["ctrl+tab"] == "tab.next"
    assert DEFAULT_KEYBINDINGS["ctrl+shift+tab"] == "tab.previous"
    assert DEFAULT_KEYBINDINGS["ctrl+alt+h"] == "pane.splitHorizontal"
    assert DEFAULT_KEYBINDINGS["ctrl+alt+v"] == "pane.splitVertical"
    for chord, command in DEFAULT_KEYBINDINGS.items():
        assert normalize_binding(chord, command) == (chord, command)


@pytest.mark.parametrize(
    ("chord", "message"),
    [
        ("a", "require a modifier"),
        ("shift+a", "Shift alone shadows typing"),
        ("ctrl+w", "browser-reserved"),
        ("ctrl+c", "terminal-reserved"),
        # Ctrl+Enter is the agent newline chord, so a command must not shadow it.
        ("ctrl+enter", "terminal-reserved"),
        ("ctrl+shift+p", "browser-reserved"),
        ("ctrl+ctrl+x", "duplicate modifier"),
        ("ctrl+hyper+x", "unknown modifier"),
    ],
)
def test_binding_validation_protects_terminal_and_browser_input(chord: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        normalize_binding(chord, "palette.open")


def test_binding_validation_rejects_unknown_commands() -> None:
    with pytest.raises(ValueError, match="unknown command id"):
        normalize_binding("ctrl+alt+x", "not.a.command")


def test_legacy_drawer_reset_binding_migrates() -> None:
    assert normalize_binding("ctrl+alt+x", "drawer.resetTabs") == (
        "ctrl+alt+x",
        "drawer.resetLayout",
    )


def test_pane_swap_is_available_for_custom_bindings() -> None:
    assert normalize_binding("ctrl+alt+x", "pane.swapNext") == (
        "ctrl+alt+x",
        "pane.swapNext",
    )


@pytest.mark.parametrize(
    ("chord", "command"),
    [("ctrl+tab", "tab.next"), ("ctrl+shift+tab", "tab.previous")],
)
def test_desktop_tab_bindings_are_mappable(chord: str, command: str) -> None:
    assert normalize_binding(chord, command) == (chord, command)


def test_keybinding_editor_metadata_exposes_commands_and_reserved_lists() -> None:
    commands = {command_id for command_id, _, _ in KEYBINDING_COMMANDS}
    policy = keybinding_policy()

    assert "projects.open" in commands
    assert "pane.stackNew" in commands
    assert "drawer.moveLeft" in commands
    assert "drawer.queue" in commands
    assert "voice.toggleTalk" in commands
    assert "voice.toggleTargetPin" in commands
    assert "project.activate(9)" in commands
    assert "ctrl+w" in policy["browser_reserved"]
    assert policy["desktop_only"] == ["ctrl+shift+tab", "ctrl+tab"]
    assert "ctrl+c" in policy["terminal_reserved"]


def test_version_one_custom_bindings_gain_desktop_tab_defaults(tmp_path: Path) -> None:
    (tmp_path / "keybindings.json").write_text(
        json.dumps(
            {
                "version": 1,
                "replace_defaults": True,
                "bindings": {"ctrl+alt+p": "palette.open"},
            }
        ),
        encoding="utf-8",
    )

    bindings = _keybindings_payload(Config(data_dir=tmp_path))["bindings"]

    assert bindings == {
        "ctrl+alt+p": "palette.open",
        "ctrl+tab": "tab.next",
        "ctrl+shift+tab": "tab.previous",
    }


def test_version_two_custom_bindings_preserve_cleared_tab_defaults(tmp_path: Path) -> None:
    (tmp_path / "keybindings.json").write_text(
        json.dumps({"version": 2, "replace_defaults": True, "bindings": {}}),
        encoding="utf-8",
    )

    assert _keybindings_payload(Config(data_dir=tmp_path))["bindings"] == {}
