"""Chord syntax: what a keystroke is called, and what a browser or a desktop can deliver.

Three ideas live here, and they are separated from the command registry
(`keybindings.py`) because every one of them is about the *keyboard* rather than
about swe-mux.

**A chord names a physical key, not a character.** Tokens are derived from
`KeyboardEvent.code` rather than `KeyboardEvent.key`, because `key` is what the
active layout produced: a Dvorak user who records `ctrl+shift+k` has pressed the
key QWERTY calls `v`, and a saved binding that means "the key labelled K here"
lands somewhere else on every other layout. `key` is still what the *label* is
drawn from, which is why `chord_label` exists and why it takes the browser's
reading rather than inventing one. The same choice makes shifted punctuation
expressible at all: `Ctrl+Shift+5` reports `key='%'`, so a `key`-derived chord
could never match a table written in unshifted terms - which is exactly the
shape tmux's `prefix %` needs.

**A sequence is up to three chords.** One prefix key is what makes 140 commands
reachable without 140 reservations: the leader needs a single chord the host will
deliver, and every keystroke after it is a plain keypress that no browser and no
window manager ever sees as a shortcut. Only the *first* chord must carry an
intercept modifier, for the same reason - later chords are read while the leader
is armed, so they cannot shadow typing.

**Delivery is measured, not decreed.** The tables below say which chords a host
is *expected* to swallow, and they are wrong somewhere by construction: this
module's predecessor refused `ctrl+f` as browser-reserved while `Settings.tsx`
was intercepting `Ctrl+F` successfully in the same browser. So a chord is never
refused for being reserved. It is accepted and reported, with a per-host verdict
and a reason, and a real measurement taken in the live host
(`frontend/src/hostKeyboardProbe.ts`) overrides the table for that host. The one
thing still refused outright is the fixed UI-scale chords, which are application
controls rather than a guess about somebody else's software.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# --------------------------------------------------------------------------- hosts

#: Where the frontend is running. The Windows desktop shell disables browser
#: accelerator keys entirely (pywebview, `design/features/desktop-shell.md`), so it
#: delivers chords no browser tab will. There is no macOS or Linux shell, which is
#: why `linux` and `mac` in `PLATFORMS` imply `browser` today.
HOSTS = ("desktop", "browser")
PLATFORMS = ("win", "mac", "linux")

MODIFIERS = ("ctrl", "shift", "alt", "meta")
#: A first chord needs one of these. Shift alone shadows ordinary typing.
INTERCEPT_MODIFIERS = frozenset({"ctrl", "alt", "meta"})

#: The longest sequence anything may bind. tmux and VS Code both stop at two;
#: Zellij's unlock-first preset uses three (`Ctrl+g p n`), which is the deepest
#: shape any preset here needs and therefore the cap.
MAX_SEQUENCE = 3

# --------------------------------------------------------------------------- keys

_LETTERS = tuple("abcdefghijklmnopqrstuvwxyz")
_DIGITS = tuple("0123456789")
_FUNCTION = tuple(f"f{index}" for index in range(1, 25))
#: The one class of key that may lead a binding unmodified. A function key
#: produces no character, so binding `F1` shadows nothing a user types - which is
#: exactly why VS Code Web uses it for the palette where `Ctrl+Shift+P` is
#: contested. Escape and the arrows are deliberately NOT in here: Escape belongs
#: to the dismiss stack and the arrows belong to whatever is running in the pane.
FUNCTION_KEYS = frozenset(_FUNCTION)
#: `code` spellings that are not simply the token upper-cased.
_PUNCTUATION = {
    "Minus": "-",
    "Equal": "=",
    "BracketLeft": "[",
    "BracketRight": "]",
    "Backslash": "\\",
    "Semicolon": ";",
    "Quote": "'",
    "Backquote": "`",
    "Comma": ",",
    "Period": ".",
    "Slash": "/",
    "IntlBackslash": "intlbackslash",
    "IntlRo": "intlro",
    "IntlYen": "intlyen",
}
_NAMED = (
    "space",
    "enter",
    "tab",
    "escape",
    "backspace",
    "delete",
    "insert",
    "home",
    "end",
    "pageup",
    "pagedown",
    "arrowleft",
    "arrowright",
    "arrowup",
    "arrowdown",
    "capslock",
    "contextmenu",
    "pause",
    "printscreen",
    "scrolllock",
)
_NUMPAD = (
    *tuple(f"numpad{digit}" for digit in _DIGITS),
    "numpadadd",
    "numpadsubtract",
    "numpadmultiply",
    "numpaddivide",
    "numpaddecimal",
    "numpadenter",
    "numpadequal",
)

#: Every key a binding may name. Closed on purpose: an unknown token is a typo or
#: a layout the tokenizer does not understand, and both are better refused at the
#: edit than silently never firing.
KEY_TOKENS: frozenset[str] = frozenset(
    (*_LETTERS, *_DIGITS, *_FUNCTION, *_PUNCTUATION.values(), *_NAMED, *_NUMPAD)
)


def token_for_code(code: str) -> str | None:
    """The canonical token for a `KeyboardEvent.code`, or None when unmappable.

    Mirrored by `frontend/src/keys.ts`; `tests/test_keychords.py` asserts the two
    tables agree by reading the TypeScript source, because a tokenizer that
    disagrees with its recorder produces bindings that can never fire.
    """
    if not code:
        return None
    if code in _PUNCTUATION:
        return _PUNCTUATION[code]
    if len(code) == 4 and code.startswith("Key") and code[3].isalpha():
        return code[3].lower()
    if len(code) == 6 and code.startswith("Digit") and code[5].isdigit():
        return code[5]
    lowered = code.lower()
    if lowered in KEY_TOKENS:
        return lowered
    return None


# --------------------------------------------------------------------------- chords

_CHORD_SPLIT = re.compile(r"\s+")


def normalize_chord(text: object, *, require_modifier: bool) -> str:
    """One canonical chord, or ValueError naming what is wrong with it."""
    raw = str(text).strip().lower().replace(" ", "")
    if not raw:
        raise ValueError("empty chord")
    # Chords name physical keys, so there is no `+` token: the key a US layout
    # prints `+` on is `=`, which is what `token_for_code` returns for `Equal`.
    parts = raw.split("+")
    key = parts[-1]
    modifiers = parts[:-1]
    if key in MODIFIERS:
        raise ValueError("a chord must end in a key, not a modifier")
    if key not in KEY_TOKENS:
        raise ValueError(f"unknown key {key!r}")
    seen = set(modifiers)
    if len(seen) != len(modifiers):
        raise ValueError("duplicate modifier")
    unknown = seen - set(MODIFIERS)
    if unknown:
        raise ValueError(f"unknown modifier {sorted(unknown)[0]!r}")
    if require_modifier and not seen & INTERCEPT_MODIFIERS and key not in FUNCTION_KEYS:
        raise ValueError(
            "the first chord needs Ctrl, Alt, or Meta, or a function key; "
            "Shift alone shadows typing"
        )
    ordered = [name for name in MODIFIERS if name in seen]
    return "+".join([*ordered, key])


def normalize_sequence(text: object) -> str:
    """Canonicalize a whole binding: one to three space-separated chords.

    Only the first chord must carry an intercept modifier. Everything after it is
    read while the sequence is already armed, so a bare letter there is a
    deliberate mnemonic (`leader p n`) rather than a key that shadows typing.
    """
    raw = str(text).strip()
    if not raw:
        raise ValueError("empty binding")
    chords = [chunk for chunk in _CHORD_SPLIT.split(raw) if chunk]
    if len(chords) > MAX_SEQUENCE:
        raise ValueError(f"a binding may chain at most {MAX_SEQUENCE} chords")
    normalized = [
        normalize_chord(chord, require_modifier=index == 0) for index, chord in enumerate(chords)
    ]
    # The one hard refusal, and it applies at every position rather than only the
    # first: the UI-scale handler is a global listener, so a scale chord reached as
    # the second half of a sequence still competes with a fixed application control.
    reserved = next((chord for chord in normalized if chord in APPLICATION_RESERVED), None)
    if reserved is not None:
        raise ValueError(f"{reserved} is a fixed UI-scale control and cannot be rebound")
    return " ".join(normalized)


def sequence_chords(sequence: str) -> tuple[str, ...]:
    return tuple(chunk for chunk in _CHORD_SPLIT.split(sequence.strip()) if chunk)


#: The order modifiers are *drawn* in, which is not the order they are stored in.
#: Storage uses one fixed order so a chord has exactly one spelling; a reader is
#: shown their own platform's convention - Apple documents ⌃⌥⇧⌘, and Windows and
#: Linux write Ctrl+Shift+Alt.
_LABEL_ORDER = {
    "win": ("ctrl", "shift", "alt", "meta"),
    "linux": ("ctrl", "shift", "alt", "meta"),
    "mac": ("ctrl", "alt", "shift", "meta"),
}


def chord_label(chord: str, *, platform: str = "win") -> str:
    """How a chord is spelled to a reader on `platform`."""
    parts = chord.split("+")
    key = parts[-1]
    held = set(parts[:-1])
    modifiers = [name for name in _LABEL_ORDER.get(platform, _LABEL_ORDER["win"]) if name in held]
    names = {
        "ctrl": "⌃" if platform == "mac" else "Ctrl",
        "shift": "⇧" if platform == "mac" else "Shift",
        "alt": "⌥" if platform == "mac" else "Alt",
        "meta": "⌘" if platform == "mac" else "Win",
    }
    pretty = {
        "arrowleft": "←",
        "arrowright": "→",
        "arrowup": "↑",
        "arrowdown": "↓",
        "pageup": "PgUp",
        "pagedown": "PgDn",
        "escape": "Esc",
        "space": "Space",
        "enter": "Enter",
        "tab": "Tab",
        "delete": "Del",
        "backspace": "Backspace",
    }
    bare = key.upper() if len(key) == 1 or key in FUNCTION_KEYS else key.title()
    label = pretty.get(key) or bare
    joiner = "" if platform == "mac" else "+"
    return joiner.join([*(names[name] for name in modifiers), label])


def sequence_label(sequence: str, *, platform: str = "win") -> str:
    return " ".join(chord_label(chord, platform=platform) for chord in sequence_chords(sequence))


# --------------------------------------------------------------------------- policy

#: Chords the browser handles itself and never dispatches to the page, so no
#: amount of `preventDefault` reaches them. Narrow on purpose: this list used to
#: carry every chord a browser *reacts* to, which refused a large, usable part of
#: the keyboard. Anything a page receives belongs in `BROWSER_CONTESTED` instead.
BROWSER_UNREACHABLE = frozenset(
    {
        "ctrl+t",
        "ctrl+n",
        "ctrl+w",
        "ctrl+shift+t",
        "ctrl+shift+n",
        "ctrl+shift+w",
        "ctrl+shift+q",
        # Chrome and Edge take these for devtools, the bookmark bar, tab search,
        # the profile switcher and a hard reload before the page is consulted.
        "ctrl+shift+i",
        "ctrl+shift+j",
        "ctrl+shift+c",
        "ctrl+shift+b",
        "ctrl+shift+o",
        "ctrl+shift+m",
        "ctrl+shift+a",
        "ctrl+shift+r",
        "ctrl+shift+d",
        "ctrl+shift+y",
        "ctrl+tab",
        "ctrl+shift+tab",
        "ctrl+pageup",
        "ctrl+pagedown",
        "ctrl+shift+pageup",
        "ctrl+shift+pagedown",
        "ctrl+l",
        "ctrl+shift+delete",
        "alt+f4",
        "f11",
        "f12",
        *(f"ctrl+{digit}" for digit in "123456789"),
        # macOS Safari/Chrome window and tab management, which is Cmd rather than Ctrl.
        "meta+t",
        "meta+n",
        "meta+w",
        "meta+q",
        "meta+l",
        "meta+shift+t",
        "meta+shift+n",
        "meta+shift+w",
        *(f"meta+{digit}" for digit in "123456789"),
    }
)

#: Chords a browser *does* dispatch: the page receives the keydown and
#: `preventDefault` suppresses the browser's own reaction. Bindable, and worth
#: saying out loud because the browser's meaning is the one the user knows.
BROWSER_CONTESTED = {
    "ctrl+f": "browser find",
    "ctrl+s": "save page",
    "ctrl+d": "bookmark",
    "ctrl+p": "print",
    "ctrl+h": "history",
    "ctrl+j": "downloads",
    "ctrl+o": "open file",
    "ctrl+u": "view source",
    "ctrl+g": "find next",
    "ctrl+r": "reload",
    "ctrl+k": "search bar (Firefox)",
    "ctrl+shift+p": "private window (Firefox)",
    "f1": "browser help",
    "f3": "find next",
    "f5": "reload",
    "f6": "focus address bar",
}

#: Window-manager and desktop-environment grabs, per platform. An application
#: never sees these: the compositor takes them first.
WM_RESERVED: dict[str, dict[str, str]] = {
    "linux": {
        "ctrl+alt+t": "opens a terminal on GNOME and most KDE setups",
        "ctrl+alt+arrowleft": "switches workspace on GNOME and KDE",
        "ctrl+alt+arrowright": "switches workspace on GNOME and KDE",
        "ctrl+alt+arrowup": "switches workspace on GNOME and KDE",
        "ctrl+alt+arrowdown": "switches workspace on GNOME and KDE",
        "ctrl+alt+delete": "log out",
        "alt+f2": "run a command",
        "alt+f4": "close window",
        "alt+tab": "switch window",
        **{f"meta+{letter}": "Super is the desktop's own modifier" for letter in _LETTERS},
    },
    "win": {
        "ctrl+alt+delete": "secure attention sequence",
        "ctrl+shift+escape": "Task Manager",
        "alt+tab": "switch window",
        "alt+f4": "close window",
        "alt+space": "window menu",
        **{f"meta+{letter}": "the Windows key belongs to the shell" for letter in _LETTERS},
    },
    "mac": {
        "meta+space": "Spotlight",
        "meta+tab": "switch application",
        "ctrl+space": "switch input source",
        "ctrl+arrowleft": "Mission Control spaces",
        "ctrl+arrowright": "Mission Control spaces",
        "ctrl+arrowup": "Mission Control",
        "ctrl+arrowdown": "Application windows",
        "meta+shift+3": "screenshot",
        "meta+shift+4": "screenshot",
        "meta+shift+5": "screen recording",
    },
}

#: Fixed application controls. The only hard refusal left: browser zoom
#: suppression and the app's own chrome scale both ride these, so a saved binding
#: here would compete with a control rather than with somebody else's software.
APPLICATION_RESERVED = frozenset({"ctrl+0", "ctrl+-", "ctrl+=", "ctrl+shift+="})

#: What the shell inside a terminal means by these. Never refused - a binding
#: scoped away from the terminal (`when: !terminalFocused`) is perfectly sound -
#: but always reported, because taking one costs the user something real.
TERMINAL_RESERVED = {
    "ctrl+a": "start of line",
    "ctrl+c": "interrupt",
    "ctrl+d": "end of file",
    "ctrl+e": "end of line",
    "ctrl+k": "kill to end of line",
    "ctrl+l": "clear",
    "ctrl+r": "reverse search",
    "ctrl+u": "kill line",
    "ctrl+v": "literal next",
    "ctrl+w": "delete word",
    "ctrl+z": "suspend",
    "ctrl+enter": "the agent newline chord",
}


@dataclass(frozen=True)
class ChordWarning:
    """One thing worth saying about a chord, and how bad it is."""

    #: `blocked` means the host cannot deliver it at all; `contested` means it
    #: works but takes a meaning the user already has.
    severity: str
    scope: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return {"severity": self.severity, "scope": self.scope, "message": self.message}


def altgr_hazard(chord: str) -> bool:
    """True for a chord AltGr produces on international layouts.

    Windows and X11 both synthesise Ctrl+Alt for AltGr, so `ctrl+alt+n` fires
    while a German, French, Polish, Spanish or Nordic user types a character on
    their own keyboard. This is why no default binding in this project uses
    Ctrl+Alt, and why one that does is reported wherever it is chosen.
    """
    parts = set(chord.split("+")[:-1])
    return "ctrl" in parts and "alt" in parts and "meta" not in parts


def chord_warnings(
    chord: str,
    *,
    host: str,
    platform: str,
    unreachable: frozenset[str] | set[str] | None = None,
) -> list[ChordWarning]:
    """Everything worth telling a reader about one chord on one host.

    `unreachable` overrides the shipped browser table with what a real host
    measured (`frontend/src/hostKeyboardProbe.ts`). Passing it is how a chord this
    table wrongly refuses becomes bindable on the browser that proved it works, and
    how one it wrongly allows stops being offered on the browser that proved it does
    not - without either answer being hard-coded from a guess.
    """
    found: list[ChordWarning] = []
    blocked_in_browser = BROWSER_UNREACHABLE if unreachable is None else unreachable
    if chord in APPLICATION_RESERVED:
        found.append(
            ChordWarning("blocked", "application", "a fixed UI-scale control uses this chord")
        )
    if host == "browser" and chord in blocked_in_browser:
        found.append(
            ChordWarning(
                "blocked",
                "browser",
                "the browser handles this itself and never passes it to the page; "
                "it works in the desktop app",
            )
        )
    wm = WM_RESERVED.get(platform, {})
    if chord in wm:
        found.append(ChordWarning("blocked", "platform", f"the desktop takes this: {wm[chord]}"))
    if host == "browser" and chord in BROWSER_CONTESTED:
        found.append(
            ChordWarning(
                "contested", "browser", f"also the browser's {BROWSER_CONTESTED[chord]}"
            )
        )
    if chord in TERMINAL_RESERVED:
        found.append(
            ChordWarning(
                "contested",
                "terminal",
                f"a shell reads this as {TERMINAL_RESERVED[chord]}; "
                "scope the binding away from the terminal to keep both",
            )
        )
    if altgr_hazard(chord):
        found.append(
            ChordWarning(
                "contested",
                "layout",
                "AltGr emits Ctrl+Alt on most non-US layouts, so this fires while typing",
            )
        )
    return found


def sequence_warnings(
    sequence: str,
    *,
    host: str,
    platform: str,
    unreachable: frozenset[str] | set[str] | None = None,
) -> list[ChordWarning]:
    """Warnings for a whole binding.

    Only the leading chord is judged against the host and the window manager: a
    later chord is read while the sequence is armed, so nothing else is competing
    for it. That asymmetry is the entire practical argument for prefix keys and
    is asserted in `tests/test_keybindings.py`.
    """
    chords = sequence_chords(sequence)
    if not chords:
        return []
    return chord_warnings(chords[0], host=host, platform=platform, unreachable=unreachable)


def deliverable(
    sequence: str,
    *,
    host: str,
    platform: str,
    unreachable: frozenset[str] | set[str] | None = None,
) -> bool:
    """False when some layer below swe-mux takes the binding's first chord."""
    return not any(
        warning.severity == "blocked"
        for warning in sequence_warnings(
            sequence, host=host, platform=platform, unreachable=unreachable
        )
    )


def chord_policy() -> dict[str, object]:
    """The whole table, for the Settings reader and for the probe to compare against."""
    return {
        "hosts": list(HOSTS),
        "platforms": list(PLATFORMS),
        "max_sequence": MAX_SEQUENCE,
        "browser_unreachable": sorted(BROWSER_UNREACHABLE),
        "browser_contested": dict(sorted(BROWSER_CONTESTED.items())),
        "wm_reserved": {
            platform: dict(sorted(entries.items())) for platform, entries in WM_RESERVED.items()
        },
        "application_reserved": sorted(APPLICATION_RESERVED),
        "terminal_reserved": dict(sorted(TERMINAL_RESERVED.items())),
        "rules": [
            "A binding is one to three chords; only the first needs Ctrl, Alt, or Meta.",
            "Chords name physical keys, so a binding means the same thing on every layout.",
            "Nothing is refused for being reserved except the fixed UI-scale chords; "
            "everything else is accepted and reported per host.",
            "Ctrl+Alt is AltGr on most non-US layouts and is avoided by every shipped preset.",
            "The desktop app receives chords a browser tab keeps for itself.",
        ],
    }
