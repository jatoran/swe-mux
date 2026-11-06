"""The bindable command registry, and the rules that map keystrokes onto it.

Chord *syntax* and what a host can deliver live in `keychords.py`; the shipped
keymaps live in `keymaps.py`. This module owns three things.

**The registry.** Every command a chord, a gesture, or the palette may name. It
is a closed list and an id never disappears from it: `_COMMAND_MIGRATIONS`
carries a retired id forward forever, because a keybindings file is durable and
can be arbitrarily old, and an unmigrated id is rejected rather than quietly
ignored. `tests/test_keybinding_registry.py` reads the frontend's own command
literals and fails when the two drift - which they had, in both directions: about
forty commands the palette offered could not be bound to anything, and five ids
here (`projects.open`, `pane.detach`, `pane.swapNext`, `stack.tabLeft`,
`stack.tabRight`) named nothing the app implemented, so binding one did nothing.

**The rule list.** A binding is a rule rather than a map entry, because one
command wants several chords (a leader path *and* a flat chord), one chord wants
different commands on different platforms, and a chord's meaning can depend on
what is focused. The shape is VS Code's, deliberately: `keys`, `command`, plus
optional `host`, `platform`, and `when`. Later rules win, so a user's rules are
appended after a preset's and a `command` of `""` erases whatever a chord had.

**Resolution.** `resolve()` turns the rule list plus a host descriptor into the
flat `sequence -> command` map the frontend dispatches on. It runs *here* rather
than in the browser for the reason the experience-tier assignment does: a
browser-computed answer would be a second copy of the policy, and the copy is
what drifts. The frontend says which host it is (`GET /api/keybindings?host=…`)
and gets an answer computed once.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .keychords import (
    HOSTS,
    PLATFORMS,
    normalize_sequence,
    sequence_warnings,
)
from .ui_messages import ui_message

KEYBINDINGS_FILE_VERSION = 3

# --------------------------------------------------------------------------- registry

#: Groups the leader tree and the Settings list are drawn from. The letter is the
#: leader's second keystroke in every shipped preset that has a leader, so the
#: mnemonic is the same one the which-key overlay prints.
COMMAND_GROUPS: tuple[tuple[str, str, str], ...] = (
    ("pane", "p", ui_message("keybinding.group.pane")),
    ("session", "s", ui_message("keybinding.group.session")),
    ("project", "w", ui_message("keybinding.group.project")),
    ("view", "v", ui_message("keybinding.group.view")),
    ("notes", "n", ui_message("keybinding.group.notes")),
    ("terminal", "t", ui_message("keybinding.group.terminal")),
    ("input", "i", ui_message("keybinding.group.input")),
    ("clipboard", "c", ui_message("keybinding.group.clipboard")),
    ("git", "g", ui_message("keybinding.group.git")),
    ("history", "h", ui_message("keybinding.group.history")),
    ("voice", "x", ui_message("keybinding.group.voice")),
)

CATEGORIES = tuple(name for name, _, _ in COMMAND_GROUPS)

KEYBINDING_COMMANDS: tuple[tuple[str, str, str], ...] = (
    # ---- the palette, and its scoped openings ------------------------------
    # One palette, four doors. The scoped ids exist because a fleet UI's most
    # common navigation is "go to that session", which a command-only palette
    # could not answer at all: `searchCommands` scores label/id/category, so a
    # session's name was not in the haystack.
    ("palette.open", ui_message("keybinding.command.palette.open"), "view"),
    ("palette.commands", ui_message("keybinding.command.palette.commands"), "view"),
    ("palette.sessions", ui_message("keybinding.command.palette.sessions"), "view"),
    ("palette.projects", ui_message("keybinding.command.palette.projects"), "view"),
    ("palette.files", ui_message("keybinding.command.palette.files"), "view"),
    ("nav.back", ui_message("keybinding.command.nav.back"), "view"),
    # ---- regions: putting the keyboard somewhere ---------------------------
    # The gap that made "navigate the whole UI" impossible before: the sidebar
    # could be *opened* without being focused, and the drawer's tab could be
    # stepped without focus ever entering the drawer. Every region the keyboard
    # can own now has a command that puts it there.
    ("focus.terminal", ui_message("keybinding.command.focus.terminal"), "view"),
    ("focus.sidebar", ui_message("keybinding.command.focus.sidebar"), "view"),
    ("focus.drawer", ui_message("keybinding.command.focus.drawer"), "view"),
    ("focus.tabBar", ui_message("keybinding.command.focus.tabBar"), "view"),
    ("focus.composer", ui_message("keybinding.command.focus.composer"), "input"),
    ("focus.next", ui_message("keybinding.command.focus.next"), "view"),
    ("focus.previous", ui_message("keybinding.command.focus.previous"), "view"),
    # ---- views -------------------------------------------------------------
    ("history.open", ui_message("keybinding.command.history.open"), "history"),
    ("history.openProject", ui_message("keybinding.command.history.openProject"), "history"),
    ("settings.open", ui_message("keybinding.command.settings.open"), "view"),
    ("plugins.open", ui_message("keybinding.command.plugins.open"), "view"),
    ("usage.open", ui_message("keybinding.command.usage.open"), "view"),
    ("usage.quota", ui_message("keybinding.command.usage.quota"), "view"),
    ("networkUsage.open", ui_message("keybinding.command.networkUsage.open"), "view"),
    ("storageUsage.open", ui_message("keybinding.command.storageUsage.open"), "view"),
    ("hooks.open", ui_message("keybinding.command.hooks.open"), "view"),
    ("notifications.open", ui_message("keybinding.command.notifications.open"), "view"),
    ("resources.open", ui_message("keybinding.command.resources.open"), "view"),
    ("fleetActivity.open", ui_message("keybinding.command.fleetActivity.open"), "view"),
    ("processes.open", ui_message("keybinding.command.processes.open"), "view"),
    ("processes.project", ui_message("keybinding.command.processes.project"), "view"),
    ("processes.all", ui_message("keybinding.command.processes.all"), "view"),
    ("preview.file", ui_message("keybinding.command.preview.file"), "view"),
    ("help.open", ui_message("keybinding.command.help.open"), "view"),
    ("tutorial.start", ui_message("keybinding.command.tutorial.start"), "view"),
    ("configurator.open", ui_message("keybinding.command.configurator.open"), "view"),
    ("actions.configure", ui_message("keybinding.command.actions.configure"), "view"),
    ("settings.navToggle", ui_message("keybinding.command.settings.navToggle"), "view"),
    ("settings.navClose", ui_message("keybinding.command.settings.navClose"), "view"),
    # Session-preserving reload flows, bindable so a wedged UI has a keyboard route.
    ("daemon.reload", ui_message("keybinding.command.daemon.reload"), "view"),
    ("app.redeploy", ui_message("keybinding.command.app.redeploy"), "view"),
    ("ui.reload", ui_message("keybinding.command.ui.reload"), "view"),
    # ---- notes -------------------------------------------------------------
    ("notes.scratchpad", ui_message("keybinding.command.notes.scratchpad"), "notes"),
    ("notes.open", ui_message("keybinding.command.notes.open"), "notes"),
    ("notes.browse", ui_message("keybinding.command.notes.browse"), "notes"),
    ("notes.browseProject", ui_message("keybinding.command.notes.browseProject"), "notes"),
    ("note.find", ui_message("keybinding.command.note.find"), "notes"),
    ("note.outline", ui_message("keybinding.command.note.outline"), "notes"),
    ("note.outlinePeek", ui_message("keybinding.command.note.outlinePeek"), "notes"),
    # ---- prompts, queue, delivery -----------------------------------------
    ("prompts.open", ui_message("keybinding.command.prompts.open"), "input"),
    ("prompts.openProject", ui_message("keybinding.command.prompts.openProject"), "input"),
    ("prompts.new", ui_message("keybinding.command.prompts.new"), "input"),
    ("queue.open", ui_message("keybinding.command.queue.open"), "input"),
    ("queue.fleet", ui_message("keybinding.command.queue.fleet"), "input"),
    ("queue.fleetProject", ui_message("keybinding.command.queue.fleetProject"), "input"),
    ("autodelivery.pause", ui_message("keybinding.command.autodelivery.pause"), "input"),
    ("broadcast.toggle", ui_message("keybinding.command.broadcast.toggle"), "input"),
    # ---- sessions ----------------------------------------------------------
    ("session.spawnShell", ui_message("keybinding.command.session.spawnShell"), "session"),
    ("session.quickLaunch", ui_message("keybinding.command.session.quickLaunch"), "session"),
    ("session.open", ui_message("keybinding.command.session.open"), "session"),
    ("session.rename", ui_message("keybinding.command.session.rename"), "session"),
    ("session.kill", ui_message("keybinding.command.session.kill"), "session"),
    ("session.killImmediate", ui_message("keybinding.command.session.killImmediate"), "session"),
    ("session.clearEnded", ui_message("keybinding.command.session.clearEnded"), "session"),
    ("session.pinAttention", ui_message("keybinding.command.session.pinAttention"), "session"),
    ("session.standDown", ui_message("keybinding.command.session.standDown"), "session"),
    (
        "session.clearStandingActivity",
        ui_message("keybinding.command.session.clearStandingActivity"),
        "session",
    ),
    ("session.copyId", ui_message("keybinding.command.session.copyId"), "session"),
    ("session.copyCwd", ui_message("keybinding.command.session.copyCwd"), "session"),
    ("session.reveal", ui_message("keybinding.command.session.reveal"), "session"),
    ("session.resume", ui_message("keybinding.command.session.resume"), "session"),
    ("session.resumeInactive", ui_message("keybinding.command.session.resumeInactive"), "session"),
    ("session.restartAgent", ui_message("keybinding.command.session.restartAgent"), "session"),
    ("session.resumeLater", ui_message("keybinding.command.session.resumeLater"), "session"),
    ("session.relaunch", ui_message("keybinding.command.session.relaunch"), "session"),
    ("session.restartCold", ui_message("keybinding.command.session.restartCold"), "session"),
    (
        "session.regenerateTitle",
        ui_message("keybinding.command.session.regenerateTitle"),
        "session",
    ),
    ("session.toggleRead", ui_message("keybinding.command.session.toggleRead"), "session"),
    (
        "session.broadcastMembership",
        ui_message("keybinding.command.session.broadcastMembership"),
        "session",
    ),
    # Stepping sessions inside a Project, which numbered Project shortcuts could not do.
    ("session.nextInProject", ui_message("keybinding.command.session.nextInProject"), "session"),
    (
        "session.previousInProject",
        ui_message("keybinding.command.session.previousInProject"),
        "session",
    ),
    ("session.approveOnce", ui_message("keybinding.command.session.approveOnce"), "session"),
    ("session.approvals.wait", ui_message("keybinding.command.session.approvals.wait"), "session"),
    (
        "session.approvals.allowlisted",
        ui_message("keybinding.command.session.approvals.allowlisted"),
        "session",
    ),
    (
        "session.approvals.allowAll",
        ui_message("keybinding.command.session.approvals.allowAll"),
        "session",
    ),
    # ---- projects ----------------------------------------------------------
    ("project.add", ui_message("keybinding.command.project.add"), "project"),
    ("project.create", ui_message("keybinding.command.project.create"), "project"),
    ("project.newTerminal", ui_message("keybinding.command.project.newTerminal"), "project"),
    (
        "project.newTerminalCustom",
        ui_message("keybinding.command.project.newTerminalCustom"),
        "project",
    ),
    ("project.next", ui_message("keybinding.command.project.next"), "project"),
    ("project.previous", ui_message("keybinding.command.project.previous"), "project"),
    ("project.rename", ui_message("keybinding.command.project.rename"), "project"),
    ("project.reveal", ui_message("keybinding.command.project.reveal"), "project"),
    ("project.moveUp", ui_message("keybinding.command.project.moveUp"), "project"),
    ("project.moveDown", ui_message("keybinding.command.project.moveDown"), "project"),
    ("project.settings", ui_message("keybinding.command.project.settings"), "project"),
    ("project.delete", ui_message("keybinding.command.project.delete"), "project"),
    ("project.files", ui_message("keybinding.command.project.files"), "project"),
    # ---- panes and tabs ----------------------------------------------------
    ("pane.splitHorizontal", ui_message("keybinding.command.pane.splitHorizontal"), "pane"),
    ("pane.splitVertical", ui_message("keybinding.command.pane.splitVertical"), "pane"),
    ("pane.stackNew", ui_message("keybinding.command.pane.stackNew"), "pane"),
    ("pane.detach", ui_message("keybinding.command.pane.detach"), "pane"),
    ("pane.close", ui_message("keybinding.command.pane.close"), "pane"),
    ("pane.zoom", ui_message("keybinding.command.pane.zoom"), "pane"),
    ("pane.next", ui_message("keybinding.command.pane.next"), "pane"),
    ("pane.previous", ui_message("keybinding.command.pane.previous"), "pane"),
    # Directional movement, the vocabulary a grid actually needs. `pane.next`
    # alone is unusable past two panes, and it is the first thing anyone
    # arriving from tmux or vim reaches for.
    ("pane.focusLeft", ui_message("keybinding.command.pane.focusLeft"), "pane"),
    ("pane.focusRight", ui_message("keybinding.command.pane.focusRight"), "pane"),
    ("pane.focusUp", ui_message("keybinding.command.pane.focusUp"), "pane"),
    ("pane.focusDown", ui_message("keybinding.command.pane.focusDown"), "pane"),
    ("pane.swapLeft", ui_message("keybinding.command.pane.swapLeft"), "pane"),
    ("pane.swapRight", ui_message("keybinding.command.pane.swapRight"), "pane"),
    ("pane.swapUp", ui_message("keybinding.command.pane.swapUp"), "pane"),
    ("pane.swapDown", ui_message("keybinding.command.pane.swapDown"), "pane"),
    ("pane.swapNext", ui_message("keybinding.command.pane.swapNext"), "pane"),
    # Named after where the divider goes, which is tmux's `resize-pane -L/-R/-U/-D`.
    # "grow my pane" cannot be said without the reader knowing which side of a split
    # they are on, and they cannot see that.
    ("pane.resizeLeft", ui_message("keybinding.command.pane.resizeLeft"), "pane"),
    ("pane.resizeRight", ui_message("keybinding.command.pane.resizeRight"), "pane"),
    ("pane.resizeUp", ui_message("keybinding.command.pane.resizeUp"), "pane"),
    ("pane.resizeDown", ui_message("keybinding.command.pane.resizeDown"), "pane"),
    ("pane.moveTabLeft", ui_message("keybinding.command.pane.moveTabLeft"), "pane"),
    ("pane.moveTabRight", ui_message("keybinding.command.pane.moveTabRight"), "pane"),
    ("pane.moveTabUp", ui_message("keybinding.command.pane.moveTabUp"), "pane"),
    ("pane.moveTabDown", ui_message("keybinding.command.pane.moveTabDown"), "pane"),
    ("tab.next", ui_message("keybinding.command.tab.next"), "pane"),
    ("tab.previous", ui_message("keybinding.command.tab.previous"), "pane"),
    ("mobileTab.next", ui_message("keybinding.command.mobileTab.next"), "pane"),
    ("mobileTab.previous", ui_message("keybinding.command.mobileTab.previous"), "pane"),
    ("stack.tabLeft", ui_message("keybinding.command.stack.tabLeft"), "pane"),
    ("stack.tabRight", ui_message("keybinding.command.stack.tabRight"), "pane"),
    ("stack.dissolve", ui_message("keybinding.command.stack.dissolve"), "pane"),
    ("session.groupStack", ui_message("keybinding.command.session.groupStack"), "pane"),
    (
        "session.openSplitHorizontal",
        ui_message("keybinding.command.session.openSplitHorizontal"),
        "pane",
    ),
    (
        "session.openSplitVertical",
        ui_message("keybinding.command.session.openSplitVertical"),
        "pane",
    ),
    ("session.customSplit", ui_message("keybinding.command.session.customSplit"), "pane"),
    # ---- chrome ------------------------------------------------------------
    ("sidebar.open", ui_message("keybinding.command.sidebar.open"), "view"),
    ("sidebar.close", ui_message("keybinding.command.sidebar.close"), "view"),
    ("sidebar.toggle", ui_message("keybinding.command.sidebar.toggle"), "view"),
    ("sidebar.search", ui_message("keybinding.command.sidebar.search"), "view"),
    ("menu.toggle", ui_message("keybinding.command.menu.toggle"), "view"),
    # ---- terminal ----------------------------------------------------------
    ("terminal.find", ui_message("keybinding.command.terminal.find"), "terminal"),
    ("terminal.copy", ui_message("keybinding.command.terminal.copy"), "clipboard"),
    ("terminal.paste", ui_message("keybinding.command.terminal.paste"), "clipboard"),
    ("terminal.selectAll", ui_message("keybinding.command.terminal.selectAll"), "clipboard"),
    ("terminal.clear", ui_message("keybinding.command.terminal.clear"), "clipboard"),
    (
        "terminal.keyboardToggle",
        ui_message("keybinding.command.terminal.keyboardToggle"),
        "terminal",
    ),
    ("keyboard.dismiss", ui_message("keybinding.command.keyboard.dismiss"), "view"),
    # ---- the side panel ----------------------------------------------------
    ("drawer.toggle", ui_message("keybinding.command.drawer.toggle"), "view"),
    ("drawer.open", ui_message("keybinding.command.drawer.open"), "view"),
    ("drawer.close", ui_message("keybinding.command.drawer.close"), "view"),
    ("drawer.peekActions", ui_message("keybinding.command.drawer.peekActions"), "input"),
    ("drawer.actions", ui_message("keybinding.command.drawer.actions"), "input"),
    ("drawer.queue", ui_message("keybinding.command.drawer.queue"), "input"),
    ("drawer.transcript", ui_message("keybinding.command.drawer.transcript"), "terminal"),
    ("drawer.activity", ui_message("keybinding.command.drawer.activity"), "terminal"),
    ("drawer.agent", ui_message("keybinding.command.drawer.agent"), "view"),
    ("drawer.files", ui_message("keybinding.command.drawer.files"), "view"),
    ("drawer.notes", ui_message("keybinding.command.drawer.notes"), "notes"),
    ("drawer.git", ui_message("keybinding.command.drawer.git"), "git"),
    ("drawer.processes", ui_message("keybinding.command.drawer.processes"), "view"),
    ("drawer.schedule", ui_message("keybinding.command.drawer.schedule"), "view"),
    ("drawer.notifications", ui_message("keybinding.command.drawer.notifications"), "view"),
    (
        "drawer.activity.timeline",
        ui_message("keybinding.command.drawer.activity.timeline"),
        "terminal",
    ),
    ("drawer.activity.findings", ui_message("keybinding.command.drawer.activity.findings"), "view"),
    ("drawer.activity.changes", ui_message("keybinding.command.drawer.activity.changes"), "view"),
    ("drawer.agent.config", ui_message("keybinding.command.drawer.agent.config"), "view"),
    ("drawer.agent.tools", ui_message("keybinding.command.drawer.agent.tools"), "view"),
    (
        "drawer.agent.instructions",
        ui_message("keybinding.command.drawer.agent.instructions"),
        "view",
    ),
    ("drawer.actions.quick", ui_message("keybinding.command.drawer.actions.quick"), "input"),
    ("drawer.actions.skills", ui_message("keybinding.command.drawer.actions.skills"), "input"),
    ("drawer.actions.prompts", ui_message("keybinding.command.drawer.actions.prompts"), "input"),
    (
        "drawer.actions.clipboard",
        ui_message("keybinding.command.drawer.actions.clipboard"),
        "clipboard",
    ),
    ("drawer.git.map", ui_message("keybinding.command.drawer.git.map"), "git"),
    ("drawer.git.log", ui_message("keybinding.command.drawer.git.log"), "git"),
    ("drawer.git.provenance", ui_message("keybinding.command.drawer.git.provenance"), "git"),
    ("drawer.resetLayout", ui_message("keybinding.command.drawer.resetLayout"), "view"),
    ("drawer.next", ui_message("keybinding.command.drawer.next"), "view"),
    ("drawer.previous", ui_message("keybinding.command.drawer.previous"), "view"),
    ("drawer.moveLeft", ui_message("keybinding.command.drawer.moveLeft"), "view"),
    ("drawer.moveRight", ui_message("keybinding.command.drawer.moveRight"), "view"),
    ("drawer.moveUp", ui_message("keybinding.command.drawer.moveUp"), "view"),
    ("drawer.moveDown", ui_message("keybinding.command.drawer.moveDown"), "view"),
    ("clipboard.open", ui_message("keybinding.command.clipboard.open"), "clipboard"),
    ("clipboard.clear", ui_message("keybinding.command.clipboard.clear"), "clipboard"),
    # ---- voice -------------------------------------------------------------
    ("voice.toggleTalk", ui_message("keybinding.command.voice.toggleTalk"), "voice"),
    ("voice.toggleTargetPin", ui_message("keybinding.command.voice.toggleTargetPin"), "voice"),
    ("voice.setup", ui_message("keybinding.command.voice.setup"), "voice"),
    ("voice.cycleMode", ui_message("keybinding.command.voice.cycleMode"), "voice"),
    ("voice.panelModeNext", ui_message("keybinding.command.voice.panelModeNext"), "voice"),
    ("voice.panelModePrevious", ui_message("keybinding.command.voice.panelModePrevious"), "voice"),
    ("voice.dockExpand", ui_message("keybinding.command.voice.dockExpand"), "voice"),
    ("voice.dockCollapse", ui_message("keybinding.command.voice.dockCollapse"), "voice"),
    ("voice.playHeld", ui_message("keybinding.command.voice.playHeld"), "voice"),
    ("voice.speak", ui_message("keybinding.command.voice.speak"), "voice"),
    ("voice.query", ui_message("keybinding.command.voice.query"), "voice"),
    ("voice.fleetStatus", ui_message("keybinding.command.voice.fleetStatus"), "voice"),
    ("voice.fleetStatusDetail", ui_message("keybinding.command.voice.fleetStatusDetail"), "voice"),
    ("voice.autoplayDevice", ui_message("keybinding.command.voice.autoplayDevice"), "voice"),
    ("voice.approval.prepare", ui_message("keybinding.command.voice.approval.prepare"), "voice"),
    ("voice.approval.confirm", ui_message("keybinding.command.voice.approval.confirm"), "voice"),
    ("voice.approval.cancel", ui_message("keybinding.command.voice.approval.cancel"), "voice"),
    ("assistant.toggle", ui_message("keybinding.command.assistant.toggle"), "voice"),
    (
        "assistant.newConversation",
        ui_message("keybinding.command.assistant.newConversation"),
        "voice",
    ),
    # ---- numbered activation ----------------------------------------------
    *tuple(
        (
            f"project.activate({index})",
            ui_message("keybinding.command.projectNumber", index=index),
            "project",
        )
        for index in range(1, 10)
    ),
    *tuple(
        (f"tab.activate({index})", ui_message("keybinding.command.tabNumber", index=index), "pane")
        for index in range(1, 10)
    ),
)

COMMAND_IDS = {command_id for command_id, _, _ in KEYBINDING_COMMANDS}

_INDEXED_COMMAND = re.compile(r"(project\.activate|tab\.activate)\(([1-9])\)\Z")
#: Dynamic command families the app generates per session, Project, drawer tab or
#: help topic. They are bindable by exact id but are not enumerated here, because
#: the set changes with the user's own data.
_DYNAMIC_COMMAND = re.compile(
    r"\A(?:"
    r"session\.(?:attach|requestKill)\([^)]+\)"
    r"|(?:session|project)\.focus:[^\s]+"
    r"|session\.spawn:[^\s]+"
    r"|drawer\.show:[^\s]+"
    r"|terminal\.railVoice:[^\s]+"
    r"|help\.topic\.[A-Za-z0-9_.-]+"
    r")\Z"
)

#: A binding a user already made must survive a surface being folded into
#: another. Entries stay forever; an unmigrated id is rejected outright rather
#: than quietly ignored, so retiring a surface without a row here turns a working
#: keybinding into a validation error on a file the user never touched.
_COMMAND_MIGRATIONS = {
    "drawer.resetTabs": "drawer.resetLayout",
    "project.note": "notes.open",
    "session.note": "notes.open",
    "drawer.commands": "drawer.actions",
    "drawer.prompts": "drawer.actions.prompts",
    "drawer.clipboard": "drawer.actions.clipboard",
    "drawer.insight": "drawer.activity",
    "drawer.timeline": "drawer.activity.timeline",
    "drawer.changemap": "drawer.activity.changes",
    "drawer.context": "drawer.agent.instructions",
    "drawer.git.land": "drawer.git.map",
    # `projects.open` was registered here and implemented nowhere, so any binding
    # made to it did nothing at all. The registry that opens is `project.create`.
    "projects.open": "project.create",
}


def migrate_command(command_id: object) -> str:
    return _COMMAND_MIGRATIONS.get(str(command_id), str(command_id))


def is_command(command_id: object) -> bool:
    """True when the id names a registered or generated command."""
    text = migrate_command(command_id)
    return (
        text in COMMAND_IDS
        or bool(_INDEXED_COMMAND.fullmatch(text))
        or bool(_DYNAMIC_COMMAND.match(text))
    )


# --------------------------------------------------------------------------- when

#: Context flags a rule may test. Closed, and the grammar over them is
#: deliberately tiny: a `&&`-joined list of optionally `!`-negated flags, with no
#: `||`, no parentheses and no comparisons. That is a total function the frontend
#: evaluates per keystroke, it covers every case a keymap here needs, and it
#: cannot grow into a second expression language nobody can validate.
WHEN_FLAGS = (
    "terminalFocused",
    "editorFocused",
    "inputFocused",
    "overlayOpen",
    "paletteOpen",
    "drawerFocused",
    "sidebarFocused",
    "settingsOpen",
    "mobile",
    "desktop",
    "zoomed",
    "multiplePanes",
    "multipleTabs",
    "hasSelection",
    "agentFocused",
)

_WHEN_TERM = re.compile(r"\A!?[A-Za-z][A-Za-z0-9]*\Z")


def normalize_when(expression: object) -> str:
    """Canonicalize a `when` clause, or ValueError naming the bad term."""
    raw = str(expression or "").strip()
    if not raw:
        return ""
    terms = [term.strip() for term in raw.split("&&")]
    if any(not term for term in terms):
        raise ValueError(ui_message("keybindings.90c7312324"))
    for term in terms:
        if not _WHEN_TERM.fullmatch(term):
            raise ValueError(ui_message("keybindings.42759d06f2", value1=repr(term)))
        flag = term[1:] if term.startswith("!") else term
        if flag not in WHEN_FLAGS:
            raise ValueError(ui_message("keybindings.079f39660a", value1=repr(flag)))
    return " && ".join(terms)


# --------------------------------------------------------------------------- rules


@dataclass(frozen=True)
class Rule:
    """One binding. `host`/`platform` of None means "everywhere"."""

    keys: str
    command: str
    host: str | None = None
    platform: str | None = None
    when: str = ""
    #: Free-text note carried into the UI; presets use it for the one sentence a
    #: reader needs about a chord that costs them something.
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"keys": self.keys, "command": self.command}
        if self.host:
            payload["host"] = self.host
        if self.platform:
            payload["platform"] = self.platform
        if self.when:
            payload["when"] = self.when
        if self.note:
            payload["note"] = self.note
        return payload

    def applies_to(self, host: str, platform: str) -> bool:
        return (self.host in (None, host)) and (self.platform in (None, platform))


def normalize_rule(raw: object) -> Rule:
    """One validated rule, or ValueError. `command: ""` is a legal erasure."""
    if not isinstance(raw, dict):
        raise ValueError(ui_message("keybindings.718410f78e"))
    keys = normalize_sequence(raw.get("keys", ""))
    command = str(raw.get("command", ""))
    if command:
        command = migrate_command(command)
        if not is_command(command):
            raise ValueError(ui_message("keybindings.51216fb7c3", value1=repr(command)))
    host = raw.get("host") or None
    if host is not None:
        host = str(host)
        if host not in HOSTS:
            raise ValueError(ui_message("keybindings.0817254d33", value1=repr(host)))
    platform = raw.get("platform") or None
    if platform is not None:
        platform = str(platform)
        if platform not in PLATFORMS:
            raise ValueError(ui_message("keybindings.cccf6ad3d0", value1=repr(platform)))
    return Rule(
        keys=keys,
        command=command,
        host=host,
        platform=platform,
        when=normalize_when(raw.get("when")),
        note=str(raw.get("note") or ""),
    )


@dataclass
class Resolution:
    """What a host actually gets, and everything that was dropped on the way."""

    #: `sequence -> [{command, when}]`, most specific last. The frontend walks the
    #: list in reverse and takes the first entry whose `when` holds, so a scoped
    #: rule and an unscoped fallback can share one chord.
    bindings: dict[str, list[dict[str, str]]] = field(default_factory=dict)
    #: Rules this host cannot receive at all, with the reason, so the UI can say
    #: "works in the desktop app" rather than silently showing a dead chord.
    undeliverable: list[dict[str, Any]] = field(default_factory=list)
    #: Rules that work but take a meaning the user already has.
    contested: list[dict[str, Any]] = field(default_factory=list)

    def flat(self) -> dict[str, str]:
        """The unconditional map, for callers that do not evaluate `when`."""
        return {
            keys: entries[-1]["command"]
            for keys, entries in self.bindings.items()
            if entries and not entries[-1]["when"]
        }


def resolve(
    rules: list[Rule],
    *,
    host: str,
    platform: str,
    unreachable: frozenset[str] | set[str] | None = None,
) -> Resolution:
    """Rules to what this host dispatches on.

    Later rules win, which is what makes appending the user's rules after a
    preset's the whole of "override". An empty command erases the chord, so a
    preset binding can be deleted without editing the preset.

    `unreachable` is what this device *measured* about its own browser, which
    overrides the shipped table wherever the two disagree.
    """
    result = Resolution()
    for rule in rules:
        if not rule.applies_to(host, platform):
            continue
        if not rule.command:
            result.bindings.pop(rule.keys, None)
            continue
        warnings = sequence_warnings(
            rule.keys, host=host, platform=platform, unreachable=unreachable
        )
        blocked = [warning for warning in warnings if warning.severity == "blocked"]
        if blocked:
            result.undeliverable.append(
                {
                    "keys": rule.keys,
                    "command": rule.command,
                    "warnings": [warning.as_dict() for warning in blocked],
                }
            )
            continue
        if warnings:
            result.contested.append(
                {
                    "keys": rule.keys,
                    "command": rule.command,
                    "warnings": [warning.as_dict() for warning in warnings],
                }
            )
        entries = result.bindings.setdefault(rule.keys, [])
        # One `when` per chord: a repeat replaces rather than stacks, so a user
        # rewriting a preset's rule leaves one answer instead of two.
        for index, existing in enumerate(entries):
            if existing["when"] == rule.when:
                entries[index] = {"command": rule.command, "when": rule.when}
                break
        else:
            entries.append({"command": rule.command, "when": rule.when})
        entries.sort(key=lambda entry: bool(entry["when"]))
    # A chord cannot both fire and arm. `ctrl+b p` as a leaf under a preset that
    # also binds `ctrl+b p n` is ambiguous by construction, and resolving it in
    # favour of the leaf would delete the whole subtree behind it silently. The
    # longer binding wins and the leaf is reported, which is the only outcome
    # where nothing disappears without saying so.
    arming = prefixes([Rule(keys=keys, command="?") for keys in result.bindings])
    for keys in sorted(result.bindings.keys() & arming):
        for entry in result.bindings.pop(keys):
            result.undeliverable.append(
                {
                    "keys": keys,
                    "command": entry["command"],
                    "warnings": [
                        {
                            "severity": "blocked",
                            "scope": "sequence",
                            "message": ui_message("keybindings.b2de4e4fcf"),
                        }
                    ],
                }
            )
    return result


def parse_document(raw: object) -> tuple[str, list[Rule], dict[str, str]]:
    """One `keybindings.json` into (preset id, rules, rejected).

    A rule that no longer validates is *reported* rather than dropped silently
    and never fails the read: a keybindings file can be arbitrarily old, and the
    reader is the Settings panel, which needs to say what it ignored.
    """
    if not isinstance(raw, dict):
        raise ValueError(ui_message("keybindings.73306f5eed"))
    preset = str(raw.get("preset") or "")
    entries = raw.get("rules")
    if entries is None:
        entries = []
    if not isinstance(entries, list):
        raise ValueError(ui_message("keybindings.113eda3c51"))
    rules: list[Rule] = []
    rejected: dict[str, str] = {}
    for index, entry in enumerate(entries):
        try:
            rules.append(normalize_rule(entry))
        except ValueError as exc:
            label = str(entry.get("keys")) if isinstance(entry, dict) else f"rule {index}"
            rejected[label] = str(exc)
    return preset, rules, rejected


def document_for(preset: str, rules: list[Rule]) -> dict[str, Any]:
    """The bytes a save writes. `preset` records what the rules were derived from.

    Recording the preset (and the file version) is what lets a later release seed
    new defaults without an accumulating `V<N>_DEFAULT_KEYBINDINGS` constant per
    release, which is how the previous format did it and could only ever grow.
    """
    return {
        "version": KEYBINDINGS_FILE_VERSION,
        "preset": preset,
        "rules": [rule.as_dict() for rule in rules],
    }


def prefixes(rules: list[Rule]) -> set[str]:
    """Every chord that only ever begins a longer sequence.

    The leader engine needs this to know that a chord should arm rather than
    fire, and the capability report needs it to explain why an unbound-looking
    chord is not free.
    """
    found: set[str] = set()
    for rule in rules:
        chords = rule.keys.split(" ")
        for index in range(1, len(chords)):
            found.add(" ".join(chords[:index]))
    return found
