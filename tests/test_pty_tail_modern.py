"""The screen classifier against the current Claude Code CLI's real byte streams.

The fixtures under tests/fixtures/pty_tails/ are raw ConPTY captures (scrubbed of
the capturing user's name) taken 2026-07-31 from the installed CLI. They pin the
two facts a synthesized screen cannot: the modern CLI never writes
"esc to interrupt" or "? for shortcuts" at all, and it positions dialog words at
absolute columns so no marker is a contiguous substring of the raw stream. The
classifier survives both — via the frame-recurring spinner ellipsis, the current
dialog/footer affordances, and cursor-movement-as-spacing normalization. If a CLI
update drifts the markers again, recapture with a scrubbed probe and extend the
marker tables; do not weaken these fixtures to synthesized text.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from swe_mux.session import (
    pty_tail_state,
    pty_tail_waiting_on_background,
)

TAILS = Path(__file__).parent / "fixtures" / "pty_tails"


def tail(name: str) -> str:
    # The same decode the daemon applies to ScrollbackBuffer.tail_bytes.
    return (TAILS / name).read_bytes().decode("utf-8", "replace")


@pytest.mark.parametrize(
    ("fixture", "expected"),
    [
        # Workspace-trust dialog: blocks the session, says nothing like "do you
        # want to", and draws "Enter to confirm · Esc to cancel" one
        # column-positioned word at a time.
        ("trust-dialog.bin", "approval"),
        # Busy turn: spinner phrases ("✶ Envisioning…") recur per animation
        # frame; no interrupt hint exists anywhere in the stream.
        ("working-spinner.bin", "working"),
        # Permission dialog over a live turn: spinner frames are still in the
        # retained tail, but the dialog is drawn after them and wins.
        ("permission-dialog.bin", "approval"),
        # Idle prompt: the permission-mode footer "(shift+tab to cycle)" is the
        # stable fragment; "? for shortcuts" no longer exists.
        ("idle-footer.bin", "idle"),
    ],
)
def test_current_cli_screens_classify(fixture: str, expected: str) -> None:
    assert pty_tail_state(tail(fixture)) == expected


def test_current_cli_screens_never_read_as_background_wait() -> None:
    for fixture in TAILS.glob("*.bin"):
        assert pty_tail_waiting_on_background(tail(fixture.name)) is False


def test_legacy_markers_still_classify() -> None:
    # Pre-2.x CLIs draw these contiguous hints; upgrading the fleet must not
    # orphan a session still running an old CLI.
    assert pty_tail_state("Editing… (esc to interrupt)") == "working"
    assert pty_tail_state("❯ try something\n? for shortcuts") == "idle"
    assert pty_tail_state("Do you want to make this edit?\n❯ 1. Yes\n  2. No") == "approval"


def test_ordering_still_decides_after_normalization() -> None:
    # A dialog raised over a busy turn: spinner frames precede the dialog text,
    # so the dialog's affordances must win despite the ellipsis being present.
    busy_then_dialog = (
        "✶ Envisioning… ✻ Envisioning… Do you want to proceed?\n"
        "❯ 1. Yes\n  2. No\nEsc to cancel · Tab to amend"
    )
    assert pty_tail_state(busy_then_dialog) == "approval"
    # And an approved dialog followed by fresh spinner frames reads working.
    dialog_then_busy = (
        "Do you want to proceed?\nEsc to cancel · Tab to amend\n✻ Herding… ✽ Herding…"
    )
    assert pty_tail_state(dialog_then_busy) == "working"


def test_window_titles_never_classify_a_frame() -> None:
    # The CLI rewrites the terminal title with the task description while
    # working; title text must not be read as screen content — neither an
    # ellipsis inside it nor a terminated marker phrase.
    title_only = "\x1b]0;⠐ Debugging paste truncation…\x07plain output"
    assert pty_tail_state(title_only) == "unknown"
    cut_mid_write = "❯ ready (shift+tab to cycle)\x1b]0;✳ Fixing tests…"
    assert pty_tail_state(cut_mid_write) == "idle"
