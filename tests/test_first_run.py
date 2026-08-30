"""The first-run shortcut offer: asked once, and only when there is a question."""

from __future__ import annotations

import json
from pathlib import Path

from swe_mux import first_run


def _offer(data_dir: Path, **overrides: object) -> bool:
    kwargs: dict[str, object] = {
        "data_dir": data_dir,
        "frozen": False,
        "start_menu_present": False,
        "windows": True,
    }
    kwargs.update(overrides)
    return first_run.should_offer(**kwargs)  # type: ignore[arg-type]


def test_a_fresh_wheel_install_on_windows_is_asked(tmp_path: Path) -> None:
    """The case the whole module exists for.

    `pip`/`uv` write launchers and stop, so nothing has offered this person a
    Start Menu entry and nothing will unless the app does it here.
    """
    assert _offer(tmp_path) is True


def test_it_is_never_asked_twice_whatever_the_answer_was(tmp_path: Path) -> None:
    """A `no` is exactly as durable as a `yes`.

    The tray is long-lived and restarted often, so an offer that only remembered
    acceptance would re-ask every launch of an app the person has already told
    to stop asking - which is worse than never offering.
    """
    first_run.record_answer(tmp_path, accepted=False)
    assert _offer(tmp_path) is False
    assert first_run.read_state(tmp_path) == first_run.FirstRunState(
        asked=True, accepted=False
    )

    other = tmp_path / "second"
    other.mkdir()
    first_run.record_answer(other, accepted=True)
    assert _offer(other) is False
    assert first_run.read_state(other).accepted is True


def test_a_frozen_install_is_never_asked(tmp_path: Path) -> None:
    """The installer wrote them; offering to write them again reads as confusion."""
    assert _offer(tmp_path, frozen=True) is False


def test_an_existing_start_menu_entry_ends_the_question(tmp_path: Path) -> None:
    """Whoever wrote it, there is nothing left to ask about.

    Note this is checked *before* the marker, so someone who ran `mux
    install-shortcut` by hand is never asked - the marker records answers, not
    the state of the Start Menu.
    """
    assert _offer(tmp_path, start_menu_present=True) is False
    assert not first_run.marker_path(tmp_path).exists()


def test_nothing_is_offered_off_windows(tmp_path: Path) -> None:
    """Shell links are a Windows concept; elsewhere there is no offer to make."""
    assert _offer(tmp_path, windows=False) is False


def test_an_unreadable_marker_reads_as_unasked(tmp_path: Path) -> None:
    """A corrupt marker costs one dialog; raising here would cost the tray.

    Every failure mode of this file is a truncated or hand-edited JSON blob, and
    the app must start regardless.
    """
    first_run.marker_path(tmp_path).write_text("{not json", encoding="utf-8")
    assert first_run.read_state(tmp_path).asked is False
    assert _offer(tmp_path) is True

    first_run.marker_path(tmp_path).write_text("[]", encoding="utf-8")
    assert first_run.read_state(tmp_path).asked is False


def test_the_marker_is_readable_by_a_person_reading_a_bug_report(tmp_path: Path) -> None:
    """JSON with the answer in it, not a touch-file: "when and what" is the point."""
    first_run.record_answer(tmp_path, accepted=True)
    payload = json.loads(first_run.marker_path(tmp_path).read_text(encoding="utf-8"))
    assert payload == {"asked": True, "accepted": True}


def test_the_offer_does_not_include_a_desktop_icon() -> None:
    """An unasked-for desktop icon is how a dialog teaches people to click no.

    Start Menu and run-at-login are both about *reachability* - the two things a
    wheel install genuinely cannot give you. A desktop icon is a preference, and
    it stays one press away in Settings rather than arriving unrequested.
    """
    assert first_run.OFFER_SLOTS == ("start-menu", "startup")


def test_the_offer_text_says_it_will_not_ask_again_and_how_to_undo_it() -> None:
    """Both promises are load-bearing: one is why a `no` is safe to give, the
    other is why a `yes` is."""
    assert "not ask again" in first_run.OFFER_TEXT
    assert "install-shortcut --remove" in first_run.OFFER_TEXT
