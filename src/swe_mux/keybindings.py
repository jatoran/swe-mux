from __future__ import annotations

import re

KEYBINDINGS_FILE_VERSION = 2
V2_DEFAULT_KEYBINDINGS = {
    "ctrl+tab": "tab.next",
    "ctrl+shift+tab": "tab.previous",
}

DEFAULT_KEYBINDINGS = {
    **V2_DEFAULT_KEYBINDINGS,
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
    "ctrl+alt+n": "notes.open",
    **{f"ctrl+alt+{index}": f"project.activate({index})" for index in range(1, 10)},
}

KEYBINDING_COMMANDS = (
    ("palette.open", "Open command palette", "view"),
    ("history.open", "Browse session history", "view"),
    ("history.openProject", "Browse selected project's session history", "view"),
    ("projects.open", "Browse project registry", "view"),
    ("settings.open", "Open Settings", "view"),
    ("usage.open", "Open usage analytics", "view"),
    ("hooks.open", "Open hooks and notification settings", "view"),
    ("notifications.open", "Open notifications", "view"),
    ("notes.scratchpad", "Open global Scratchpad", "view"),
    ("notes.open", "Open current project's notes", "view"),
    ("processes.open", "Inspect session processes and previews", "view"),
    ("processes.project", "Inspect selected project's processes", "view"),
    ("prompts.openProject", "Open prompt library for selected project", "input"),
    ("observations.open", "Open selected project's observation inbox", "input"),
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
    ("notes.browse", "Browse all notes", "notes"),
    ("notes.browseProject", "Browse this project's notes", "notes"),
    ("project.add", "Add project", "project"),
    ("project.create", "Create project", "project"),
    ("project.newTerminal", "New terminal in selected project", "project"),
    ("project.newTerminalCustom", "New custom terminal in selected project", "project"),
    ("project.rename", "Rename selected project", "project"),
    ("project.settings", "Open selected project settings", "project"),
    ("project.delete", "Delete selected project", "project"),
    ("project.files", "Browse selected project files", "project"),
    ("pane.splitHorizontal", "Split focused pane right", "pane"),
    ("pane.splitVertical", "Split focused pane below", "pane"),
    ("pane.stackNew", "New terminal as tab", "pane"),
    ("pane.detach", "Detach focused pane", "pane"),
    ("pane.zoom", "Toggle focused pane zoom", "pane"),
    ("pane.next", "Focus next pane", "pane"),
    ("pane.previous", "Focus previous pane", "pane"),
    ("tab.next", "Focus next workspace tab", "pane"),
    ("tab.previous", "Focus previous workspace tab", "pane"),
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
    # Ctrl+F already reaches the focused note from inside the editor itself, so this is
    # here for the palette, a gesture, and anyone who wants a different chord — not
    # because the feature needs a binding to be usable.
    ("note.find", "Find in focused note", "view"),
    # Same shape: reachable from the pane header and the editor's own command rail, so this
    # exists for the palette, a gesture, and anyone who wants a chord for it.
    ("note.outline", "Jump to a heading in the focused note", "view"),
    ("terminal.copy", "Copy from focused terminal", "terminal"),
    ("terminal.paste", "Paste into focused terminal", "terminal"),
    ("terminal.selectAll", "Select all in focused terminal", "terminal"),
    ("terminal.clear", "Clear focused terminal", "terminal"),
    # Read/select mode: the touch control that keeps the on-screen keyboard down.
    # Bindable like any other command so it can drive a gesture or the palette.
    # It carries the default two-finger swipe-down gesture, so it also lowers a
    # keyboard held up outside a terminal (a note editor, a form field) rather than
    # toggling a mode on a terminal the mobile workspace is not showing.
    (
        "terminal.keyboardToggle",
        "Hide the on-screen keyboard (read/select mode in a focused terminal)",
        "terminal",
    ),
    # The same dismissal with no terminal mode behind it, for anyone who wants the
    # gesture to only ever put the keyboard away.
    ("keyboard.dismiss", "Hide the on-screen keyboard", "view"),
    # The right-edge utility drawer (default two-finger swipe-left gesture on
    # touch; an always-visible icon rail on desktop). `drawer.toggle` reopens the
    # last tab; the per-tab ids open one directly and close it if it is showing.
    # These labels are what the gesture/shortcut pickers show, and picking between
    # them is the whole decision a user makes there — so each one says whether it
    # restores the last tab or forces its own. Binding a per-tab id to the gesture
    # you open the panel with is what makes the panel "forget" which tab you left
    # it on; it is obeying the binding, not losing state.
    ("drawer.toggle", "Side panel: toggle, reopening the last tab used", "view"),
    ("drawer.clipboard", "Side panel: always clipboard history", "clipboard"),
    ("drawer.commands", "Side panel: always session commands", "terminal"),
    ("drawer.prompts", "Side panel: always prompt templates", "input"),
    ("drawer.queue", "Side panel: always prompt queue", "input"),
    ("drawer.transcript", "Side panel: always this session's transcript", "terminal"),
    ("drawer.files", "Side panel: always project files", "view"),
    ("drawer.notes", "Side panel: always project notes", "view"),
    ("drawer.context", "Side panel: always agent context", "view"),
    ("drawer.git", "Side panel: always project Git status", "view"),
    ("drawer.processes", "Side panel: always project processes", "view"),
    ("drawer.notifications", "Side panel: always notifications", "view"),
    ("drawer.resetLayout", "Reset side panel layout", "view"),
    ("drawer.next", "Side panel: focus next tab in pane", "view"),
    ("drawer.previous", "Side panel: focus previous tab in pane", "view"),
    ("drawer.moveLeft", "Side panel: move focused tab left", "view"),
    ("drawer.moveRight", "Side panel: move focused tab right", "view"),
    ("drawer.moveUp", "Side panel: move focused tab up", "view"),
    ("drawer.moveDown", "Side panel: move focused tab down", "view"),
    ("clipboard.open", "Side panel: always clipboard history (rail Clip button)", "clipboard"),
    ("clipboard.clear", "Clear unpinned clipboard history", "clipboard"),
    # Conversation capture is workspace-level. These commands make the visible mic
    # and target-pin controls optional gesture/keybinding destinations too.
    ("voice.toggleTalk", "Toggle hands-free conversation", "voice"),
    ("voice.toggleTargetPin", "Toggle voice dictation target pin", "voice"),
    *tuple(
        (f"project.activate({index})", f"Switch to project {index}", "project")
        for index in range(1, 10)
    ),
)

COMMAND_IDS = {command_id for command_id, _, _ in KEYBINDING_COMMANDS}

_PROJECT_COMMAND = re.compile(r"project\.activate\(([1-9])\)\Z")
_COMMAND_MIGRATIONS = {
    "drawer.resetTabs": "drawer.resetLayout",
    "project.note": "notes.open",
    "session.note": "notes.open",
}


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
    "ctrl+shift+n",
    "ctrl+shift+p",
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
_DESKTOP_ONLY = {
    "ctrl+tab",
    "ctrl+shift+tab",
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
        "desktop_only": sorted(_DESKTOP_ONLY),
        "terminal_reserved": sorted(_TERMINAL_RESERVED),
        "rules": [
            "Use Ctrl, Alt, or Meta plus a non-modifier key.",
            "Shift alone is rejected so normal typing always reaches the terminal.",
            "Known browser and terminal shortcuts are reserved.",
            "Desktop-only chords work in the desktop app; an ordinary browser keeps them.",
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
    command_id = _COMMAND_MIGRATIONS.get(str(command), str(command))
    if command_id not in COMMAND_IDS and not _PROJECT_COMMAND.fullmatch(command_id):
        raise ValueError("unknown command id")
    return key, command_id
