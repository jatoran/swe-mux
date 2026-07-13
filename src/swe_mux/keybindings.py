from __future__ import annotations

import re

DEFAULT_KEYBINDINGS = {
    "ctrl+alt+t": "session.spawnShell",
    "ctrl+alt+o": "session.quickLaunch",
    "ctrl+shift+p": "palette.open",
    "ctrl+shift+f": "terminal.find",
    "ctrl+alt+h": "pane.splitHorizontal",
    "ctrl+alt+v": "pane.splitVertical",
    "ctrl+alt+z": "pane.zoom",
    "ctrl+alt+d": "pane.detach",
    "ctrl+alt+arrowright": "pane.next",
    "ctrl+alt+arrowleft": "pane.previous",
    "ctrl+alt+s": "settings.open",
    **{f"ctrl+alt+{index}": f"space.activate({index})" for index in range(1, 10)},
}

COMMAND_IDS = {
    "palette.open", "session.spawnShell", "session.quickLaunch", "space.create",
    "history.open", "settings.open", "terminal.find", "pane.splitHorizontal",
    "pane.splitVertical", "pane.detach", "pane.zoom", "pane.next", "pane.previous",
    "pane.swapNext",
    "broadcast.toggle", "terminal.copy", "terminal.paste", "terminal.pasteImage",
    "terminal.selectAll", "terminal.clear", "session.kill", "session.killImmediate",
    "session.pinAttention",
    "session.open", "session.rename", "session.copyId", "session.copyCwd",
    "session.openSplitHorizontal", "session.openSplitVertical", "session.reveal",
    "session.worktreeCreate", "session.worktreesManage", "session.customSplit",
    "session.broadcastMembership", "session.resume", "space.newTerminal",
    "space.newTerminalCustom", "space.rename", "space.settings", "space.delete",
    "notes.open", "processes.open",
}

_SPACE_COMMAND = re.compile(r"space\.activate\(([1-9])\)\Z")
_MODIFIERS = {"ctrl", "shift", "alt", "meta"}
_INTERCEPT_MODIFIERS = {"ctrl", "alt", "meta"}
_BROWSER_RESERVED = {"ctrl+w", "ctrl+t", "ctrl+n", "meta+w", "meta+t", "meta+n"}


def normalize_binding(chord: object, command: object) -> tuple[str, str]:
    key = str(chord).lower().replace(" ", "")
    parts = key.split("+")
    if len(parts) < 2 or not parts[-1] or parts[-1] in _MODIFIERS:
        raise ValueError("bindings require a modifier and non-modifier key")
    modifiers = set(parts[:-1])
    if not modifiers <= _MODIFIERS:
        raise ValueError("binding contains an unknown modifier")
    if not modifiers & _INTERCEPT_MODIFIERS:
        raise ValueError("bindings require Ctrl, Alt, or Meta; Shift alone shadows typing")
    if len(parts[:-1]) != len(modifiers):
        raise ValueError("binding contains a duplicate modifier")
    if key in _BROWSER_RESERVED:
        raise ValueError("browser-reserved chord")
    command_id = str(command)
    if command_id not in COMMAND_IDS and not _SPACE_COMMAND.fullmatch(command_id):
        raise ValueError("unknown command id")
    return key, command_id
