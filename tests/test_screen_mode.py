"""Alternate-screen tracking read off the PTY stream.

This is the daemon's own copy of a fact the browser used to be the only source
of, and delivery readiness now treats a *contradiction* of the expected screen
as a block — so a parser that missed a toggle would silently make a session
undeliverable, and one that invented a toggle would do the opposite.
"""

from __future__ import annotations

from swe_mux.screen_mode import (
    STICKY_PRIVATE_MODES,
    BracketedPasteParser,
    ScreenModeParser,
    StickyModeParser,
)


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


# The startup sequence measured from a real Claude Code session on 2026-08-14. Every one
# of these modes appears exactly once, inside the first 130 bytes, and never again.
CLAUDE_STARTUP = (
    b"\x1b[1t\x1b[c\x1b[?1004h\x1b[?9001h\x1b[?25h\x1b[?2004h\x1b[?2031h"
    b"\x1b[?1049h\x1b[?1000h\x1b[?1002h\x1b[?1003h\x1b[?1006h"
)


def test_sticky_modes_are_unknown_until_the_child_sets_them() -> None:
    parser = StickyModeParser()
    assert parser.enabled == {}
    assert parser.preamble(b"") == b""


def test_a_claude_startup_leaves_every_reporting_mode_restatable() -> None:
    parser = StickyModeParser()
    parser.feed(CLAUDE_STARTUP)
    # A window from deep in the session mentions none of them, so all of them come back.
    assert parser.preamble(b"conversation output\n" * 50) == (
        b"\x1b[?1000h\x1b[?1002h\x1b[?1003h\x1b[?1004h\x1b[?1006h\x1b[?2031h"
    )


def test_each_sticky_mode_is_tracked_on_its_own() -> None:
    # A child that asked for button-event tracking and not any-event tracking gets back
    # exactly what it asked for; inventing 1003 would have it reporting bare motion.
    parser = StickyModeParser()
    parser.feed(b"\x1b[?1002h\x1b[?1006h")
    assert parser.preamble(b"") == b"\x1b[?1002h\x1b[?1006h"
    parser.feed(b"\x1b[?1002l")
    assert parser.preamble(b"") == b"\x1b[?1006h"


def test_a_combined_set_sequence_sets_every_mode_it_names() -> None:
    parser = StickyModeParser()
    parser.feed(b"\x1b[?1000;1006h")
    assert parser.preamble(b"") == b"\x1b[?1000h\x1b[?1006h"


def test_a_window_that_carries_a_mode_speaks_for_it() -> None:
    # The window's own toggle is the child's most recent word. Restating over it would
    # be the daemon contradicting the child.
    parser = StickyModeParser()
    parser.feed(b"\x1b[?1000h\x1b[?1006h")
    assert parser.preamble(b"\x1b[?1000l") == b"\x1b[?1006h"


def test_a_sticky_toggle_split_across_reads_is_still_seen() -> None:
    sequence = b"\x1b[?1000;1002;1003;1006h"
    for cut in range(1, len(sequence)):
        parser = StickyModeParser()
        parser.feed(sequence[:cut])
        parser.feed(sequence[cut:] + b"after")
        assert parser.enabled.get(1006) is True, cut


def test_untracked_private_modes_are_ignored() -> None:
    parser = StickyModeParser()
    parser.feed(b"\x1b[?25l\x1b[?1049h\x1b[?2004h\x1b[?9001h")
    assert parser.preamble(b"") == b""


def test_the_sticky_carry_stays_bounded() -> None:
    parser = StickyModeParser()
    parser.feed(b"\x1b[?" + b"9" * 400)
    assert len(parser._tail) <= 40


def test_the_mouse_group_is_declared_sticky() -> None:
    # The regression this exists for: without these four a phone drag has nothing to
    # forward and dies against an alternate screen with no scrollback.
    for mode in (1000, 1002, 1003, 1006):
        assert mode in STICKY_PRIVATE_MODES
