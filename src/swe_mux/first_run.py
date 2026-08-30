"""The one moment a wheel install can be offered its shortcuts.

A wheel cannot create a Start Menu entry. `pip` and `uv` write launchers into a
scripts directory and stop, with no hook that runs afterwards (`shortcuts.py`
opens with the same fact, because it is the same gap seen from the other side).
So `mux install-shortcut` exists - and is a command nobody runs, because nobody
knows it is there. The result is an install whose only route in is a name on
`PATH`, on the platform where `PATH` is least likely to be right.

The first successful start of the desktop shell is the only moment left. The
person is present, the app demonstrably works, and they have just done the thing
the offer is about to make unnecessary. This module decides whether to ask; the
tray does the asking, because a message box needs a Windows process with a
message loop and this has to stay testable everywhere.

Three rules, and each is a way the offer could become a nuisance instead:

**It is asked once, ever.** The marker is written whichever way the person
answers, and a `no` is as durable as a `yes`. An offer that returns is a worse
version of no offer at all, and this one runs at *every* start of a long-lived
tray.

**It is not asked when the answer is already known.** A frozen install got its
shortcuts from the installer, so it never qualifies; neither does an install
that already has a Start Menu entry, whoever wrote it. Those are not "already
answered yes" - they are cases where there was never a question.

**It never blocks the app.** The decision is a file read and the ask happens off
the thread that owns the window, so a person who ignores the dialog for an hour
has a working swe-mux for that hour.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .host_platform import IS_WINDOWS

#: One marker file, beside the other install-lifecycle state in the data dir.
#: JSON rather than a touch-file because "when, and what they said" is worth
#: having in a bug report, and an empty file could not carry it.
FIRST_RUN_NAME = "desktop-first-run.json"

#: Slots a `yes` writes. The Desktop icon is deliberately not among them: it is
#: the one shortcut people have opinions about, it is the easiest of the three to
#: create later, and an unasked-for desktop icon is exactly the behaviour that
#: teaches someone to click `no` on every future dialog this project shows.
OFFER_SLOTS: tuple[str, ...] = ("start-menu", "startup")

OFFER_TITLE = "swe-mux"
OFFER_TEXT = (
    "Add swe-mux to the Start Menu, and start it when you sign in?\n"
    "\n"
    "It will start minimised to the notification area, so the browser UI and "
    "your agent sessions are there whenever you want them - with no terminal "
    "and nothing to launch.\n"
    "\n"
    "You can change this later from the tray menu (Start with Windows) or from "
    "Settings, and `mux install-shortcut --remove` takes back everything this "
    "writes.\n"
    "\n"
    "swe-mux will not ask again either way."
)


@dataclass(frozen=True, slots=True)
class FirstRunState:
    """What the marker records, and the only thing that reads it."""

    asked: bool
    accepted: bool | None = None

    def as_dict(self) -> dict[str, object]:
        return {"asked": self.asked, "accepted": self.accepted}


def marker_path(data_dir: Path) -> Path:
    return data_dir / FIRST_RUN_NAME


def read_state(data_dir: Path) -> FirstRunState:
    """The recorded answer, or "never asked" for anything unreadable.

    A corrupt or truncated marker reads as unasked rather than raising: the cost
    of being asked a second time is one dialog, and the cost of an exception here
    is a tray that will not start.
    """
    try:
        raw = json.loads(marker_path(data_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return FirstRunState(asked=False)
    if not isinstance(raw, dict):
        return FirstRunState(asked=False)
    accepted = raw.get("accepted")
    return FirstRunState(
        asked=bool(raw.get("asked")),
        accepted=accepted if isinstance(accepted, bool) else None,
    )


def record_answer(data_dir: Path, *, accepted: bool) -> None:
    """Persist the answer, so this is asked once per install and not once per start."""
    state = FirstRunState(asked=True, accepted=accepted)
    data_dir.mkdir(parents=True, exist_ok=True)
    marker_path(data_dir).write_text(
        json.dumps(state.as_dict(), indent=2) + "\n", encoding="utf-8"
    )


def should_offer(
    *,
    data_dir: Path,
    frozen: bool,
    start_menu_present: bool,
    windows: bool = IS_WINDOWS,
) -> bool:
    """Whether this start is the one that asks. Pure, so every branch is testable.

    Every input is passed rather than probed for the reason the whole
    `shortcuts` module is written that way: the Windows behaviour has to be
    assertable from any host, and a platform-conditional branch whose other side
    is never exercised is how this repository has been bitten before.
    """
    if not windows:
        return False
    if frozen:
        # The installer wrote them, and offering to write what is already there
        # reads as the app not knowing its own state.
        return False
    if start_menu_present:
        return False
    return not read_state(data_dir).asked
