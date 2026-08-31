"""Shipped keymap presets: data files, not code.

Each preset is one JSON document under `assets/keymaps/`, so adding one is a data
edit rather than a Python edit and a user can write their own without touching
this package. That is Warp's model and it is the right one for something whose
whole value is that it is a table of somebody else's muscle memory.

Three ideas hold the set together.

**Every preset includes the same leader tree.** `leader-tree.json` binds all ~200
commands under one prefix, and every preset pulls it in under Ctrl+Shift+Space.
So choosing "tmux" adds tmux's prefix and its letters; it never takes away the
route to a command tmux has no opinion about. A preset that replaced the tree
would have to re-invent a mnemonic for every surface swe-mux has and nothing
else does.

**A preset's own prefix is separate from the tree's leader.** They would collide
otherwise, and the collision is not hypothetical: tmux's `prefix p` is "previous
window" while the tree's `leader p` opens the pane group, and one of the two
would have to lose its whole subtree. Two prefixes cost one extra chord to
remember and keep both intact.

**A preset states what it costs.** swe-mux is an *outer* shell, so any chord it
claims is claimed from whatever runs inside a pane. The tmux, VS Code, Vim and
Emacs presets each carry a `warning` naming exactly what they take, and the
picker shows it before the choice rather than after.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from .keybindings import Rule, normalize_rule

log = logging.getLogger(__name__)

KEYMAPS_DIR = Path(__file__).resolve().parent / "assets" / "keymaps"

#: What a fresh install lands on. Chosen rather than inherited: everything before
#: this shipped defaulted to Ctrl+Alt, which AltGr produces on most non-US
#: layouts, so 24 of 26 default chords fired while the user was typing.
DEFAULT_PRESET = "swemux"

#: Documents that exist only to be included by a preset and are never offered as
#: a choice of their own.
_FRAGMENTS = frozenset({"leader-tree"})

#: The token a fragment writes where the including preset's leader chord goes.
_LEADER_TOKEN = "leader"


@dataclass(frozen=True)
class Preset:
    """One shipped keymap, already validated."""

    id: str
    title: str
    description: str
    leader: str
    rules: tuple[Rule, ...]
    prefix: str = ""
    prefix_alternates: tuple[str, ...] = ()
    warning: str = ""

    def summary(self) -> dict[str, Any]:
        """Everything the picker draws, without the rule list."""
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "leader": self.leader,
            "prefix": self.prefix,
            "prefix_alternates": list(self.prefix_alternates),
            "warning": self.warning,
            "bindings": len(self.rules),
        }


def _read(name: str) -> dict[str, Any]:
    path = KEYMAPS_DIR / f"{name}.json"
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:  # pragma: no cover - a packaging fault, not a user one
        raise ValueError(f"keymap {name!r} is not installed at {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"keymap {name!r} is not valid JSON: {exc}") from exc
    if not isinstance(document, dict):
        raise ValueError(f"keymap {name!r} must be an object")
    return document


def _substitute_leader(keys: str, leader: str) -> str:
    """Replace a fragment's `leader` first chord with the preset's own."""
    chords = keys.split(" ")
    if chords and chords[0] == _LEADER_TOKEN:
        return " ".join([leader, *chords[1:]])
    return keys


def _document_rules(document: dict[str, Any], leader: str, *, source: str) -> list[Rule]:
    """Every rule in one document, map form first and list form after it.

    Order is the whole of precedence (later wins), so the map form is for the
    common case and the list form is where a rule needs a `host`, a `platform`,
    a `when` or a note - which is also why it comes last and can correct the map.
    """
    rules: list[Rule] = []
    bindings = document.get("bindings") or {}
    if not isinstance(bindings, dict):
        raise ValueError(f"{source}: `bindings` must be an object")
    for keys, command in bindings.items():
        rules.append(
            normalize_rule({"keys": _substitute_leader(str(keys), leader), "command": command})
        )
    extra = document.get("rules") or []
    if not isinstance(extra, list):
        raise ValueError(f"{source}: `rules` must be a list")
    for raw in extra:
        if not isinstance(raw, dict):
            raise ValueError(f"{source}: every entry in `rules` must be an object")
        rules.append(
            normalize_rule({**raw, "keys": _substitute_leader(str(raw.get("keys", "")), leader)})
        )
    return rules


def _load(name: str) -> Preset:
    document = _read(name)
    leader = str(document.get("leader") or "ctrl+shift+space")
    rules: list[Rule] = []
    for included in document.get("include") or []:
        fragment = _read(str(included))
        rules.extend(_document_rules(fragment, leader, source=f"{name} <- {included}"))
    rules.extend(_document_rules(document, leader, source=name))
    return Preset(
        id=str(document.get("id") or name),
        title=str(document.get("title") or name),
        description=str(document.get("description") or ""),
        leader=leader,
        rules=tuple(rules),
        prefix=str(document.get("prefix") or ""),
        prefix_alternates=tuple(str(item) for item in document.get("prefix_alternates") or ()),
        warning=str(document.get("warning") or ""),
    )


@lru_cache(maxsize=1)
def presets() -> dict[str, Preset]:
    """Every offerable preset, keyed by id.

    A malformed document is logged and skipped rather than taken down the whole
    daemon with it: a preset is data, one bad file must not stop the other four
    from being choosable, and the log line is what makes the absence findable.
    """
    found: dict[str, Preset] = {}
    if not KEYMAPS_DIR.is_dir():  # pragma: no cover - packaging fault
        log.error("keymap presets are missing from the install at %s", KEYMAPS_DIR)
        return found
    for path in sorted(KEYMAPS_DIR.glob("*.json")):
        name = path.stem
        if name in _FRAGMENTS:
            continue
        try:
            preset = _load(name)
        except ValueError:
            log.exception("keymap preset %s failed to load and is not offered", name)
            continue
        found[preset.id] = preset
        log.debug("keymap preset %s loaded with %d rules", preset.id, len(preset.rules))
    return found


def preset_ids() -> tuple[str, ...]:
    return tuple(presets())


def preset_summaries() -> list[dict[str, Any]]:
    return [preset.summary() for preset in presets().values()]


def preset_rules(preset_id: str) -> list[Rule]:
    """The rule list for one preset, or ValueError naming the unknown id."""
    preset = presets().get(preset_id)
    if preset is None:
        raise ValueError(f"unknown keymap preset {preset_id!r}")
    return list(preset.rules)


def default_rules() -> list[Rule]:
    """What an install with no keybindings document of its own dispatches on."""
    try:
        return preset_rules(DEFAULT_PRESET)
    except ValueError:  # pragma: no cover - packaging fault
        log.error("the default keymap preset %s is not installed", DEFAULT_PRESET)
        return []
