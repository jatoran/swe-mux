from __future__ import annotations

import re

DEFAULT_KEYBINDINGS = {
    "ctrl+alt+t": "session.spawnShell",
    "ctrl+alt+o": "session.quickLaunch",
    "ctrl+alt+p": "palette.open",
    "ctrl+shift+f": "terminal.find",
    "ctrl+alt+h": "pane.splitHorizontal",
    "ctrl+alt+v": "pane.splitVertical",
    "ctrl+alt+z": "pane.zoom",
    "ctrl+alt+d": "pane.detach",
    "ctrl+alt+arrowright": "pane.next",
    "ctrl+alt+arrowleft": "pane.previous",
    "ctrl+alt+s": "settings.open",
    "ctrl+alt+n": "session.note",
    **{f"ctrl+alt+{index}": f"project.activate({index})" for index in range(1, 10)},
}

KEYBINDING_COMMANDS = (
    ("palette.open", "Open command palette", "view"),
    ("history.open", "Browse session history", "view"),
    ("history.openProject", "Browse selected project's session history", "view"),
    ("projects.open", "Browse project registry", "view"),
    ("settings.open", "Open Settings", "view"),
    ("settings.project", "Open current project settings", "view"),
    ("usage.open", "Open usage analytics", "view"),
    ("hooks.open", "Open hooks and notification settings", "view"),
    ("notifications.open", "Open notifications", "view"),
    ("notes.open", "Open current project note", "view"),
    ("processes.open", "Inspect session processes and previews", "view"),
    ("processes.project", "Inspect selected project's processes", "view"),
    ("prompts.openProject", "Open prompt library for selected project", "input"),
    ("session.spawnShell", "New terminal in current project", "session"),
    ("session.quickLaunch", "New terminal custom", "session"),
    ("session.open", "Open selected session", "session"),
    ("session.rename", "Rename selected session", "session"),
    ("session.kill", "Confirm-kill focused session", "session"),
    ("session.killImmediate", "Kill selected session immediately", "session"),
    ("session.pinAttention", "Toggle focused agent attention pin", "session"),
    ("session.copyId", "Copy selected session ID", "session"),
    ("session.copyCwd", "Copy selected working directory", "session"),
    ("session.reveal", "Reveal selected session directory", "session"),
    ("session.resume", "Resume selected agent session", "session"),
    ("session.broadcastMembership", "Toggle selected session broadcast", "session"),
    ("session.note", "Open selected session note", "notes"),
    ("notes.browse", "Browse session notes", "notes"),
    ("notes.browseProject", "Browse this project's session notes", "notes"),
    ("project.add", "Add project", "project"),
    ("project.create", "Create project", "project"),
    ("project.newTerminal", "New terminal in selected project", "project"),
    ("project.newTerminalCustom", "New custom terminal in selected project", "project"),
    ("project.rename", "Rename selected project", "project"),
    ("project.settings", "Open selected project settings", "project"),
    ("project.delete", "Delete selected project", "project"),
    ("project.note", "Open selected project note", "notes"),
    ("project.files", "Browse selected project files", "project"),
    ("pane.splitHorizontal", "Split focused pane right", "pane"),
    ("pane.splitVertical", "Split focused pane below", "pane"),
    ("pane.stackNew", "New terminal as tab", "pane"),
    ("pane.detach", "Detach focused pane", "pane"),
    ("pane.zoom", "Toggle focused pane zoom", "pane"),
    ("pane.next", "Focus next pane", "pane"),
    ("pane.previous", "Focus previous pane", "pane"),
    ("mobileTab.next", "Focus next tab (mobile)", "pane"),
    ("mobileTab.previous", "Focus previous tab (mobile)", "pane"),
    ("sidebar.open", "Open navigation sidebar", "view"),
    ("sidebar.close", "Close navigation sidebar", "view"),
    ("sidebar.toggle", "Toggle navigation sidebar", "view"),
    ("pane.swapNext", "Swap focused pane with next", "pane"),
    ("session.groupStack", "Stack selected and focused sessions", "pane"),
    ("session.openSplitHorizontal", "Open selected session in split right", "pane"),
    ("session.openSplitVertical", "Open selected session in split below", "pane"),
    ("session.customSplit", "New custom terminal in split", "pane"),
    ("stack.tabLeft", "Move focused tab left", "pane"),
    ("stack.tabRight", "Move focused tab right", "pane"),
    ("stack.dissolve", "Dissolve focused tab stack", "pane"),
    ("broadcast.toggle", "Toggle broadcast input", "input"),
    ("terminal.find", "Find in focused terminal", "terminal"),
    ("terminal.copy", "Copy from focused terminal", "terminal"),
    ("terminal.paste", "Paste into focused terminal", "terminal"),
    ("terminal.selectAll", "Select all in focused terminal", "terminal"),
    ("terminal.clear", "Clear focused terminal", "terminal"),
    *tuple(
        (f"project.activate({index})", f"Switch to project {index}", "project")
        for index in range(1, 10)
    ),
)

COMMAND_IDS = {command_id for command_id, _, _ in KEYBINDING_COMMANDS}

_PROJECT_COMMAND = re.compile(r"project\.activate\(([1-9])\)\Z")


def is_command(command_id: object) -> bool:
    """True when the id names a registered command (including project.activate(n))."""
    text = str(command_id)
    return text in COMMAND_IDS or bool(_PROJECT_COMMAND.fullmatch(text))


_MODIFIERS = {"ctrl", "shift", "alt", "meta"}
_INTERCEPT_MODIFIERS = {"ctrl", "alt", "meta"}
_BROWSER_RESERVED = {
    "alt+arrowleft",
    "alt+arrowright",
    "ctrl+d",
    "ctrl+f",
    "ctrl+h",
    "ctrl+j",
    "ctrl+l",
    "ctrl+n",
    "ctrl+p",
    "ctrl+r",
    "ctrl+s",
    "ctrl+t",
    "ctrl+tab",
    "ctrl+shift+n",
    "ctrl+shift+p",
    "ctrl+shift+tab",
    "ctrl+shift+t",
    "ctrl+w",
    "meta+d",
    "meta+f",
    "meta+l",
    "meta+n",
    "meta+p",
    "meta+r",
    "meta+s",
    "meta+t",
    "meta+w",
}
_TERMINAL_RESERVED = {
    "ctrl+a",
    "ctrl+c",
    "ctrl+d",
    "ctrl+e",
    "ctrl+enter",
    "ctrl+k",
    "ctrl+l",
    "ctrl+r",
    "ctrl+u",
    "ctrl+v",
    "ctrl+w",
    "ctrl+z",
}


def keybinding_policy() -> dict[str, object]:
    return {
        "browser_reserved": sorted(_BROWSER_RESERVED),
        "terminal_reserved": sorted(_TERMINAL_RESERVED),
        "rules": [
            "Use Ctrl, Alt, or Meta plus a non-modifier key.",
            "Shift alone is rejected so normal typing always reaches the terminal.",
            "Known browser and terminal shortcuts are reserved.",
            "One chord can be assigned to only one command.",
        ],
    }


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
    if key in _TERMINAL_RESERVED:
        raise ValueError("terminal-reserved chord")
    command_id = str(command)
    if command_id not in COMMAND_IDS and not _PROJECT_COMMAND.fullmatch(command_id):
        raise ValueError("unknown command id")
    return key, command_id
