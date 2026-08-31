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

KEYBINDINGS_FILE_VERSION = 3

# --------------------------------------------------------------------------- registry

#: Groups the leader tree and the Settings list are drawn from. The letter is the
#: leader's second keystroke in every shipped preset that has a leader, so the
#: mnemonic is the same one the which-key overlay prints.
COMMAND_GROUPS: tuple[tuple[str, str, str], ...] = (
    ("pane", "p", "Panes and tabs"),
    ("session", "s", "Sessions"),
    ("project", "w", "Projects"),
    ("view", "v", "Views and dialogs"),
    ("notes", "n", "Notes"),
    ("terminal", "t", "Terminal"),
    ("input", "i", "Input and queue"),
    ("clipboard", "c", "Clipboard"),
    ("git", "g", "Git"),
    ("history", "h", "History"),
    ("voice", "x", "Voice"),
)

CATEGORIES = tuple(name for name, _, _ in COMMAND_GROUPS)

KEYBINDING_COMMANDS: tuple[tuple[str, str, str], ...] = (
    # ---- the palette, and its scoped openings ------------------------------
    # One palette, four doors. The scoped ids exist because a fleet UI's most
    # common navigation is "go to that session", which a command-only palette
    # could not answer at all: `searchCommands` scores label/id/category, so a
    # session's name was not in the haystack.
    ("palette.open", "Open command palette", "view"),
    ("palette.commands", "Palette: commands", "view"),
    ("palette.sessions", "Palette: jump to a session", "view"),
    ("palette.projects", "Palette: jump to a Project", "view"),
    ("palette.files", "Palette: open a file in this Project", "view"),
    ("nav.back", "Back (close one overlay level, then step back a tab)", "view"),
    # ---- regions: putting the keyboard somewhere ---------------------------
    # The gap that made "navigate the whole UI" impossible before: the sidebar
    # could be *opened* without being focused, and the drawer's tab could be
    # stepped without focus ever entering the drawer. Every region the keyboard
    # can own now has a command that puts it there.
    ("focus.terminal", "Focus the terminal grid", "view"),
    ("focus.sidebar", "Focus the Projects sidebar", "view"),
    ("focus.drawer", "Focus the side panel", "view"),
    ("focus.tabBar", "Focus the focused pane's tab bar", "view"),
    ("focus.composer", "Focus the message composer", "input"),
    ("focus.next", "Focus the next UI region", "view"),
    ("focus.previous", "Focus the previous UI region", "view"),
    # ---- views -------------------------------------------------------------
    ("history.open", "Browse session history", "history"),
    ("history.openProject", "Browse selected project's session history", "history"),
    ("settings.open", "Open Settings", "view"),
    ("usage.open", "Open usage analytics", "view"),
    ("usage.quota", "Open provider quota windows", "view"),
    ("networkUsage.open", "Open bandwidth usage", "view"),
    ("storageUsage.open", "Open storage usage", "view"),
    ("hooks.open", "Open Automation dashboard", "view"),
    ("notifications.open", "Open notifications", "view"),
    ("resources.open", "Open resources (processes, bandwidth, storage, fleet activity)", "view"),
    ("fleetActivity.open", "Open fleet activity telemetry", "view"),
    ("processes.open", "Inspect session processes and previews", "view"),
    ("processes.project", "Inspect selected project's processes", "view"),
    ("processes.all", "Open the unified process viewer", "view"),
    ("preview.file", "Preview the focused HTML file in a pane", "view"),
    ("help.open", "Open help (how swe-mux works)", "view"),
    ("tutorial.start", "Take the guided tour", "view"),
    ("configurator.open", "Ask an agent about this install", "view"),
    ("actions.configure", "Configure Actions", "view"),
    ("settings.navToggle", "Toggle Settings sections (narrow layout)", "view"),
    ("settings.navClose", "Close Settings sections (narrow layout)", "view"),
    # Session-preserving reload flows, bindable so a wedged UI has a keyboard route.
    ("daemon.reload", "Reload the daemon, keeping sessions", "view"),
    ("app.redeploy", "Rebuild and redeploy the app, keeping sessions", "view"),
    ("ui.reload", "Reload the UI", "view"),
    # ---- notes -------------------------------------------------------------
    ("notes.scratchpad", "Open global Scratchpad", "notes"),
    ("notes.open", "Open current project's notes", "notes"),
    ("notes.browse", "Browse all notes", "notes"),
    ("notes.browseProject", "Browse this project's notes", "notes"),
    ("note.find", "Find in focused note", "notes"),
    ("note.outline", "Jump to a heading in the focused note", "notes"),
    ("note.outlinePeek", "Toggle the pinned heading outline overlay", "notes"),
    # ---- prompts, queue, delivery -----------------------------------------
    ("prompts.open", "Open the prompt library", "input"),
    ("prompts.openProject", "Open prompt library for selected project", "input"),
    ("prompts.new", "New prompt template", "input"),
    ("queue.open", "Open the prompt queue for the focused session", "input"),
    ("queue.fleet", "Open the fleet queue", "input"),
    ("queue.fleetProject", "Open the fleet queue for selected project", "input"),
    ("autodelivery.pause", "Pause or resume all auto-delivery", "input"),
    ("broadcast.toggle", "Toggle broadcast input", "input"),
    # ---- sessions ----------------------------------------------------------
    ("session.spawnShell", "New terminal in current project", "session"),
    ("session.quickLaunch", "New terminal custom", "session"),
    ("session.open", "Open selected session", "session"),
    ("session.rename", "Rename selected session", "session"),
    ("session.kill", "Confirm-kill focused session", "session"),
    ("session.killImmediate", "Kill selected session immediately", "session"),
    ("session.clearEnded", "Remove every ended session here", "session"),
    ("session.pinAttention", "Toggle focused agent attention pin", "session"),
    ("session.standDown", "Stand down the focused agent's attention", "session"),
    ("session.clearStandingActivity", "Clear standing activity on the focused session", "session"),
    ("session.copyId", "Copy selected session ID", "session"),
    ("session.copyCwd", "Copy selected working directory", "session"),
    ("session.reveal", "Reveal selected session directory", "session"),
    ("session.resume", "Resume selected agent session", "session"),
    ("session.resumeInactive", "Resume an inactive agent session", "session"),
    ("session.resumeLater", "Schedule a resume of the selected agent", "session"),
    ("session.relaunch", "Relaunch the selected session", "session"),
    ("session.restartCold", "Restart the selected cold session", "session"),
    ("session.regenerateTitle", "Regenerate the selected session's title", "session"),
    ("session.toggleRead", "Mark the selected session read or unread", "session"),
    ("session.broadcastMembership", "Toggle selected session broadcast", "session"),
    # Stepping sessions inside a Project, which numbered Project shortcuts could not do.
    ("session.nextInProject", "Focus the next session in this Project", "session"),
    ("session.previousInProject", "Focus the previous session in this Project", "session"),
    ("session.approveOnce", "Approve the focused session's pending request once", "session"),
    ("session.approvals.wait", "Approvals: ask every time", "session"),
    ("session.approvals.allowlisted", "Approvals: auto-approve allowlisted requests", "session"),
    ("session.approvals.allowAll", "Approvals: auto-approve everything", "session"),
    # ---- projects ----------------------------------------------------------
    ("project.add", "Add project", "project"),
    ("project.create", "Manage projects", "project"),
    ("project.newTerminal", "New terminal in selected project", "project"),
    ("project.newTerminalCustom", "New custom terminal in selected project", "project"),
    ("project.next", "Focus the next Project (sidebar order)", "project"),
    ("project.previous", "Focus the previous Project (sidebar order)", "project"),
    ("project.rename", "Rename selected project", "project"),
    ("project.reveal", "Reveal selected project in the file manager", "project"),
    ("project.moveUp", "Move selected Project up", "project"),
    ("project.moveDown", "Move selected Project down", "project"),
    ("project.settings", "Open selected project settings", "project"),
    ("project.delete", "Remove selected project from swe-mux", "project"),
    ("project.files", "Browse selected project files", "project"),
    # ---- panes and tabs ----------------------------------------------------
    ("pane.splitHorizontal", "Split focused pane right", "pane"),
    ("pane.splitVertical", "Split focused pane below", "pane"),
    ("pane.stackNew", "New terminal as tab", "pane"),
    ("pane.detach", "Detach the focused tab into its own pane", "pane"),
    ("pane.close", "Close the focused pane", "pane"),
    ("pane.zoom", "Toggle focused pane zoom", "pane"),
    ("pane.next", "Focus next pane", "pane"),
    ("pane.previous", "Focus previous pane", "pane"),
    # Directional movement, the vocabulary a grid actually needs. `pane.next`
    # alone is unusable past two panes, and it is the first thing anyone
    # arriving from tmux or vim reaches for.
    ("pane.focusLeft", "Focus the pane to the left", "pane"),
    ("pane.focusRight", "Focus the pane to the right", "pane"),
    ("pane.focusUp", "Focus the pane above", "pane"),
    ("pane.focusDown", "Focus the pane below", "pane"),
    ("pane.swapLeft", "Swap the focused pane with the one to its left", "pane"),
    ("pane.swapRight", "Swap the focused pane with the one to its right", "pane"),
    ("pane.swapUp", "Swap the focused pane with the one above", "pane"),
    ("pane.swapDown", "Swap the focused pane with the one below", "pane"),
    ("pane.swapNext", "Swap focused pane with next", "pane"),
    # Named after where the divider goes, which is tmux's `resize-pane -L/-R/-U/-D`.
    # "grow my pane" cannot be said without the reader knowing which side of a split
    # they are on, and they cannot see that.
    ("pane.resizeLeft", "Move the pane divider left", "pane"),
    ("pane.resizeRight", "Move the pane divider right", "pane"),
    ("pane.resizeUp", "Move the pane divider up", "pane"),
    ("pane.resizeDown", "Move the pane divider down", "pane"),
    ("pane.moveTabLeft", "Move focused tab to the pane on the left", "pane"),
    ("pane.moveTabRight", "Move focused tab to the pane on the right", "pane"),
    ("pane.moveTabUp", "Move focused tab to the pane above", "pane"),
    ("pane.moveTabDown", "Move focused tab to the pane below", "pane"),
    ("tab.next", "Focus next workspace tab", "pane"),
    ("tab.previous", "Focus previous workspace tab", "pane"),
    ("mobileTab.next", "Focus next tab (mobile)", "pane"),
    ("mobileTab.previous", "Focus previous tab (mobile)", "pane"),
    ("stack.tabLeft", "Move focused tab left within its pane", "pane"),
    ("stack.tabRight", "Move focused tab right within its pane", "pane"),
    ("stack.dissolve", "Dissolve focused tab stack", "pane"),
    ("session.groupStack", "Stack selected and focused sessions", "pane"),
    ("session.openSplitHorizontal", "Open selected session in split right", "pane"),
    ("session.openSplitVertical", "Open selected session in split below", "pane"),
    ("session.customSplit", "New custom terminal in split", "pane"),
    # ---- chrome ------------------------------------------------------------
    ("sidebar.open", "Open navigation sidebar", "view"),
    ("sidebar.close", "Close navigation sidebar", "view"),
    ("sidebar.toggle", "Toggle navigation sidebar", "view"),
    ("sidebar.search", "Filter Projects and sessions", "view"),
    ("menu.toggle", "Toggle the swe-mux menu", "view"),
    # ---- terminal ----------------------------------------------------------
    ("terminal.find", "Find in focused terminal", "terminal"),
    ("terminal.copy", "Copy from focused terminal", "clipboard"),
    ("terminal.paste", "Paste into focused terminal", "clipboard"),
    ("terminal.selectAll", "Select all in focused terminal", "clipboard"),
    ("terminal.clear", "Clear focused terminal", "clipboard"),
    (
        "terminal.keyboardToggle",
        "Hide the on-screen keyboard (read/select mode in a focused terminal)",
        "terminal",
    ),
    ("keyboard.dismiss", "Hide the on-screen keyboard", "view"),
    # ---- the side panel ----------------------------------------------------
    ("drawer.toggle", "Side panel: toggle, reopening the last tab used", "view"),
    ("drawer.open", "Side panel: open", "view"),
    ("drawer.close", "Side panel: close", "view"),
    ("drawer.peekActions", "Side panel: peek the actions tab", "input"),
    ("drawer.actions", "Side panel: always actions, skills, prompts, and clipboard", "input"),
    ("drawer.queue", "Side panel: always prompt queue", "input"),
    ("drawer.transcript", "Side panel: always this session's transcript", "terminal"),
    ("drawer.activity", "Side panel: always this session's activity", "terminal"),
    ("drawer.agent", "Side panel: always this session's agent setup", "view"),
    ("drawer.files", "Side panel: always project files", "view"),
    ("drawer.notes", "Side panel: always project notes", "notes"),
    ("drawer.git", "Side panel: always project Git status", "git"),
    ("drawer.processes", "Side panel: always project processes", "view"),
    ("drawer.schedule", "Side panel: always scheduled runs", "view"),
    ("drawer.notifications", "Side panel: always notifications", "view"),
    ("drawer.activity.timeline", "Side panel: always the scan timeline", "terminal"),
    ("drawer.activity.findings", "Side panel: always findings", "view"),
    ("drawer.activity.changes", "Side panel: always the change map", "view"),
    ("drawer.agent.config", "Side panel: always agent configuration", "view"),
    ("drawer.agent.tools", "Side panel: always agent tools and extensions", "view"),
    ("drawer.agent.instructions", "Side panel: always agent instructions and memory", "view"),
    ("drawer.actions.quick", "Side panel: always quick actions", "input"),
    ("drawer.actions.skills", "Side panel: always skills", "input"),
    ("drawer.actions.prompts", "Side panel: always prompt templates", "input"),
    ("drawer.actions.clipboard", "Side panel: always clipboard history", "clipboard"),
    ("drawer.git.map", "Side panel: always the Git worktree map", "git"),
    ("drawer.git.log", "Side panel: always the Git commit log", "git"),
    ("drawer.git.provenance", "Side panel: always Git commit provenance", "git"),
    ("drawer.resetLayout", "Reset side panel layout", "view"),
    ("drawer.next", "Side panel: focus next tab in pane", "view"),
    ("drawer.previous", "Side panel: focus previous tab in pane", "view"),
    ("drawer.moveLeft", "Side panel: move focused tab left", "view"),
    ("drawer.moveRight", "Side panel: move focused tab right", "view"),
    ("drawer.moveUp", "Side panel: move focused tab up", "view"),
    ("drawer.moveDown", "Side panel: move focused tab down", "view"),
    ("clipboard.open", "Side panel: always clipboard history (rail Clip button)", "clipboard"),
    ("clipboard.clear", "Clear unpinned clipboard history", "clipboard"),
    # ---- voice -------------------------------------------------------------
    ("voice.toggleTalk", "Toggle hands-free conversation", "voice"),
    ("voice.toggleTargetPin", "Toggle voice dictation target pin", "voice"),
    ("voice.setup", "Set up voice (guided)", "voice"),
    ("voice.cycleMode", "Cycle the voice panel mode", "voice"),
    ("voice.panelModeNext", "Next voice panel mode", "voice"),
    ("voice.panelModePrevious", "Previous voice panel mode", "voice"),
    ("voice.dockExpand", "Expand the voice dock", "voice"),
    ("voice.dockCollapse", "Collapse the voice dock", "voice"),
    ("voice.playHeld", "Play the held voice reply", "voice"),
    ("voice.speak", "Speak the focused session's latest reply", "voice"),
    ("voice.query", "Ask a spoken question", "voice"),
    ("voice.fleetStatus", "Speak fleet status", "voice"),
    ("voice.fleetStatusDetail", "Speak detailed fleet status", "voice"),
    ("voice.autoplayDevice", "Claim spoken replies for this device", "voice"),
    ("voice.approval.prepare", "Prepare the spoken approval", "voice"),
    ("voice.approval.confirm", "Confirm the spoken approval", "voice"),
    ("voice.approval.cancel", "Cancel the spoken approval", "voice"),
    ("assistant.toggle", "Toggle the assistant panel", "voice"),
    ("assistant.newConversation", "Start a new assistant conversation", "voice"),
    # ---- numbered activation ----------------------------------------------
    *tuple(
        (f"project.activate({index})", f"Switch to project {index}", "project")
        for index in range(1, 10)
    ),
    *tuple(
        (f"tab.activate({index})", f"Focus workspace tab {index}", "pane")
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
        raise ValueError("empty term in a when clause")
    for term in terms:
        if not _WHEN_TERM.fullmatch(term):
            raise ValueError(f"malformed when term {term!r}")
        flag = term[1:] if term.startswith("!") else term
        if flag not in WHEN_FLAGS:
            raise ValueError(f"unknown when flag {flag!r}")
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
        raise ValueError("a binding rule must be an object")
    keys = normalize_sequence(raw.get("keys", ""))
    command = str(raw.get("command", ""))
    if command:
        command = migrate_command(command)
        if not is_command(command):
            raise ValueError(f"unknown command id {command!r}")
    host = raw.get("host") or None
    if host is not None:
        host = str(host)
        if host not in HOSTS:
            raise ValueError(f"unknown host {host!r}")
    platform = raw.get("platform") or None
    if platform is not None:
        platform = str(platform)
        if platform not in PLATFORMS:
            raise ValueError(f"unknown platform {platform!r}")
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
                            "message": "another binding continues from this chord, "
                            "so it arms the next keystroke instead of firing",
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
        raise ValueError("keybindings.json must contain an object")
    preset = str(raw.get("preset") or "")
    entries = raw.get("rules")
    if entries is None:
        entries = []
    if not isinstance(entries, list):
        raise ValueError("keybindings.json `rules` must be a list")
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
