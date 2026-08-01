"""Alternate-screen tracking read off the PTY stream.

This is the daemon's own copy of a fact the browser used to be the only source
of, and delivery readiness now treats a *contradiction* of the expected screen
as a block — so a parser that missed a toggle would silently make a session
undeliverable, and one that invented a toggle would do the opposite.
"""

from __future__ import annotations

from swe_mux.screen_mode import BracketedPasteParser, ScreenModeParser


def test_nothing_is_claimed_until_the_child_says_something() -> None:
    parser = ScreenModeParser()
    assert parser.feed(b"hello world\r\n") is None
    assert parser.mode is None


def test_entering_and_leaving_the_alternate_screen() -> None:
    parser = ScreenModeParser()
    assert parser.feed(b"\x1b[?1049h\x1b[2J") == "alternate"
    assert parser.feed(b"drawing") == "alternate"
    assert parser.feed(b"\x1b[?1049l") == "normal"


def test_the_last_toggle_in_a_chunk_wins() -> None:
    """A single read can contain a whole enter/leave cycle."""
    parser = ScreenModeParser()
    assert parser.feed(b"\x1b[?1049h" + b"x" * 64 + b"\x1b[?1049l" + b"\x1b[?1049h") == "alternate"


def test_the_older_spellings_count() -> None:
    for sequence, expected in ((b"\x1b[?47h", "alternate"), (b"\x1b[?1047h", "alternate")):
        parser = ScreenModeParser()
        assert parser.feed(sequence) == expected
    parser = ScreenModeParser()
    parser.feed(b"\x1b[?1047h")
    assert parser.feed(b"\x1b[?1047l") == "normal"


def test_a_toggle_split_across_two_reads_is_still_seen() -> None:
    """PTY chunk boundaries fall wherever the read happened to end."""
    for cut in range(1, len(b"\x1b[?1049h")):
        parser = ScreenModeParser()
        sequence = b"\x1b[?1049h"
        parser.feed(b"before" + sequence[:cut])
        assert parser.feed(sequence[cut:] + b"after") == "alternate", cut


def test_a_split_older_spelling_is_still_seen() -> None:
    parser = ScreenModeParser()
    parser.feed(b"\x1b[?4")
    assert parser.feed(b"7h") == "alternate"


def test_other_private_modes_are_not_screen_switches() -> None:
    """`?25l` (hide cursor) and friends share the introducer and mean nothing here."""
    parser = ScreenModeParser()
    assert parser.feed(b"\x1b[?25l\x1b[?2004h\x1b[?1006h") is None
    # A retained partial must not fuse with the next chunk into a false toggle.
    assert parser.feed(b"1049h") is None
    assert parser.feed(b"\x1b[?1049h") == "alternate"


def test_the_carry_stays_bounded() -> None:
    parser = ScreenModeParser()
    parser.feed(b"\x1b[?" + b"9" * 400)
    assert len(parser._tail) <= 16


def test_bracketed_paste_is_unknown_until_the_child_says_so() -> None:
    parser = BracketedPasteParser()
    assert parser.feed(b"hello world\r\n") is None
    assert parser.enabled is None


def test_bracketed_paste_enable_and_disable() -> None:
    parser = BracketedPasteParser()
    assert parser.feed(b"\x1b[?2004h") is True
    assert parser.feed(b"typing") is True
    assert parser.feed(b"\x1b[?2004l") is False


def test_the_last_bracketed_paste_toggle_in_a_chunk_wins() -> None:
    parser = BracketedPasteParser()
    assert parser.feed(b"\x1b[?2004h" + b"x" * 64 + b"\x1b[?2004l") is False


def test_a_bracketed_paste_toggle_split_across_reads_is_still_seen() -> None:
    for cut in range(1, len(b"\x1b[?2004h")):
        parser = BracketedPasteParser()
        sequence = b"\x1b[?2004h"
        parser.feed(b"before" + sequence[:cut])
        assert parser.feed(sequence[cut:] + b"after") is True, cut


def test_other_private_modes_are_not_bracketed_paste() -> None:
    parser = BracketedPasteParser()
    assert parser.feed(b"\x1b[?25l\x1b[?1049h\x1b[?1006h") is None
    assert parser.feed(b"2004h") is None
    assert parser.feed(b"\x1b[?2004h") is True


def test_the_bracketed_paste_carry_stays_bounded() -> None:
    parser = BracketedPasteParser()
    parser.feed(b"\x1b[?" + b"9" * 400)
    assert len(parser._tail) <= 16
