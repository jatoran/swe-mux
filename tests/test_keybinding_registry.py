"""The registry and the app must describe each other, in both directions.

They had drifted both ways, silently, and each direction has its own symptom:

- **Implemented but unregistered** (about forty commands, including everything the
  Actions editor, the prompt library, the queue, the reload flows and the whole help
  topic family offer): the command works from the palette and cannot be bound to a
  chord or a gesture at all, because the shortcut editor and the gesture picker are
  both drawn from `KEYBINDING_COMMANDS`.
- **Registered but unimplemented** (`projects.open`, `pane.detach`, `pane.swapNext`,
  `stack.tabLeft`, `stack.tabRight`): the command appears in the shortcut editor,
  accepts a chord, and does nothing when pressed. `pane.detach` even had a button in
  the pane menu wired to it.

Neither is visible from either side alone, which is why this reads the frontend's own
source. It is a string-contract test in the same family as `test_launcher_names.py`:
cheap, total over the literal ids, and deliberately blind to the dynamic families,
which `is_command` matches by pattern instead.
"""

from __future__ import annotations

import re
from pathlib import Path

from swe_mux.keybindings import COMMAND_IDS, is_command

ROOT = Path(__file__).parents[1]
#: Where a `Command` object with a literal id can be constructed.
SOURCES = ("frontend/src/App.tsx", "frontend/src/fleetCommands.ts")

_LITERAL_ID = re.compile(r"""\bid:\s*'([a-z][A-Za-z0-9.]*)'""")

#: Ids that appear as `id: '…'` in a command source but are not commands: other
#: object literals in the same files use the same key. Listed rather than filtered
#: by a cleverer regex, because a wrong regex here fails open.
NOT_COMMANDS = frozenset({"left", "right", "up", "down"})

#: Registered ids the frontend implements through a generated family rather than a
#: literal, so this scan cannot see them. Each names the generator.
GENERATED = {
    # `paneDirectionOptions.flatMap` and `...Array.from({length: 9})`.
    *(f"pane.{verb}{direction}" for verb in ("focus", "swap", "resize", "moveTab")
      for direction in ("Left", "Right", "Up", "Down")),
    *(f"tab.activate({index})" for index in range(1, 10)),
    *(f"project.activate({index})" for index in range(1, 10)),
    # `focusRegions().map` and the palette-scope map.
    "focus.terminal", "focus.sidebar", "focus.drawer", "focus.tabBar", "focus.composer",
    "palette.commands", "palette.sessions", "palette.projects", "palette.files",
    # `(['copy','paste','selectAll','clear'] as const).map`.
    "terminal.copy", "terminal.paste", "terminal.selectAll", "terminal.clear",
    # `[['stack.tabLeft', …], ['stack.tabRight', …]].map`.
    "stack.tabLeft", "stack.tabRight",
    # `DRAWER_TABS.map` / `drawerSegments.ts`, keyed by tab and segment id.
    *(name for name in COMMAND_IDS if name.startswith("drawer.")),
    # Registered so a gesture or a chord can reach the mobile projection and the
    # settings section drawer, both of which App builds from constants.
    "settings.navToggle", "settings.navClose",
}


def _implemented_ids() -> set[str]:
    found: set[str] = set()
    for relative in SOURCES:
        text = (ROOT / relative).read_text(encoding="utf-8")
        found |= {match.group(1) for match in _LITERAL_ID.finditer(text)}
    return found - NOT_COMMANDS


def test_every_command_the_app_offers_can_be_bound_to_something() -> None:
    """A palette entry that no chord and no gesture can reach is half a command."""
    unbindable = sorted(name for name in _implemented_ids() if not is_command(name))
    assert unbindable == [], (
        "these commands exist in the frontend but are not in KEYBINDING_COMMANDS, so "
        "they cannot be bound to a chord or a mobile gesture: " + ", ".join(unbindable)
    )


def test_every_registered_command_is_implemented_somewhere() -> None:
    """A bindable id with nothing behind it accepts a chord and then does nothing."""
    implemented = _implemented_ids() | GENERATED
    missing = sorted(COMMAND_IDS - implemented)
    assert missing == [], (
        "these ids are registered as bindable but nothing in the frontend implements "
        "them, so binding one does nothing: " + ", ".join(missing)
    )


def test_the_generated_allowlist_stays_honest() -> None:
    """An entry here must name something the registry still has.

    Without this the allowlist becomes the place a retired id hides: it would keep
    `test_every_registered_command_is_implemented_somewhere` green for a command that
    no longer exists on either side.
    """
    assert not (GENERATED - COMMAND_IDS)
