from __future__ import annotations

import pytest

from swe_mux.keybindings import (
    DEFAULT_KEYBINDINGS,
    KEYBINDING_COMMANDS,
    keybinding_policy,
    normalize_binding,
)


def test_default_bindings_reference_valid_commands() -> None:
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


def test_pane_swap_is_available_for_custom_bindings() -> None:
    assert normalize_binding("ctrl+alt+x", "pane.swapNext") == (
        "ctrl+alt+x",
        "pane.swapNext",
    )


def test_keybinding_editor_metadata_exposes_commands_and_reserved_lists() -> None:
    commands = {command_id for command_id, _, _ in KEYBINDING_COMMANDS}
    policy = keybinding_policy()

    assert "projects.open" in commands
    assert "pane.stackNew" in commands
    assert "project.activate(9)" in commands
    assert "ctrl+w" in policy["browser_reserved"]
    assert "ctrl+c" in policy["terminal_reserved"]
