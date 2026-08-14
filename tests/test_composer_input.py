"""The unsent-composer estimate: what each write does to it, and what clears it."""

from __future__ import annotations

from swe_mux.composer_input import (
    ComposerState,
    classify_composer_write,
    clear_composer,
    note_composer_write,
)
from tests.support.detection_replay import ReplaySession

NOW = 1_770_000_000.0


def test_typing_marks_the_composer_pending_once() -> None:
    state = ComposerState()
    assert note_composer_write(state, "h", NOW) == "pending"
    assert state.since == NOW
    # The crossing is the reportable fact, not the keystroke: everything after
    # the first character is already-known state.
    assert note_composer_write(state, "ello", NOW + 1) is None
    assert state.since == NOW


def test_submitting_clears_it() -> None:
    state = ComposerState()
    note_composer_write(state, "ship it", NOW)
    assert note_composer_write(state, "\r", NOW + 5) == "cleared"
    assert not state.pending
    assert state.since == 0.0


def test_a_frame_carrying_text_and_a_return_is_a_submit() -> None:
    state = ComposerState()
    assert note_composer_write(state, "ship it\r", NOW) is None
    assert not state.pending


def test_discard_keys_clear_it() -> None:
    for key in ("\x03", "\x1b", "\x15"):
        state = ComposerState()
        note_composer_write(state, "half a thought", NOW)
        assert note_composer_write(state, key, NOW + 1) == "cleared", key


def test_erasing_everything_typed_clears_it() -> None:
    # The common false positive this exists to prevent: a word typed and then
    # deleted must not leave a mark standing until the next turn.
    state = ComposerState()
    note_composer_write(state, "oops", NOW)
    assert note_composer_write(state, "\x7f\x7f\x7f", NOW + 1) is None
    assert note_composer_write(state, "\x7f", NOW + 2) == "cleared"
    assert not state.pending


def test_erasing_past_empty_does_not_go_negative() -> None:
    state = ComposerState()
    note_composer_write(state, "ab", NOW)
    note_composer_write(state, "\x7f" * 10, NOW + 1)
    assert state.chars == 0
    assert note_composer_write(state, "x", NOW + 2) == "pending"


def test_navigation_and_mode_keys_compose_nothing() -> None:
    state = ComposerState()
    for keys in ("\x1b[A", "\x1b[B", "\x1b[1;5C", "\x1b[Z", "\t", "\x1bOP"):
        assert note_composer_write(state, keys, NOW) is None, keys
    assert not state.pending


def test_a_bracketed_paste_is_composed_text_even_with_newlines() -> None:
    # Bracketed paste exists so a multi-line paste does not run. Reading its
    # newlines as a submit would zero the count on the largest thing anyone
    # ever puts in a composer.
    state = ComposerState()
    paste = "\x1b[200~first line\nsecond line\n\x1b[201~"
    assert note_composer_write(state, paste, NOW) == "pending"
    assert state.chars == len("first line\nsecond line\n")


def test_a_paste_split_across_frames_never_submits() -> None:
    state = ComposerState()
    assert note_composer_write(state, "\x1b[200~first line\n", NOW) == "pending"
    assert note_composer_write(state, "second line\n\x1b[201~", NOW + 1) is None
    assert state.pending


def test_an_osc_title_write_composes_nothing() -> None:
    state = ComposerState()
    assert note_composer_write(state, "\x1b]0;a window title\x07", NOW) is None
    assert not state.pending


def test_classification_reports_the_edit_size() -> None:
    write = classify_composer_write("abc\x7f")
    assert (write.kind, write.typed, write.erased) == ("edit", 3, 1)
    assert classify_composer_write("").kind == "none"
    assert classify_composer_write("\x1b[200~x\x1b[201~\r").kind == "submit"


def test_a_turn_starting_empties_the_composer_whatever_submitted_it() -> None:
    # A queue delivery, a voice send, and a keystroke are the same bytes to the
    # PTY, but only the input handler sees all of them. The state funnel is the
    # backstop: a turn cannot open with text still sitting in the composer.
    session = ReplaySession("claude")
    session.composer = ComposerState()
    note_composer_write(session.composer, "half typed", NOW)
    session.transition("working", None, source="hook", evidence="hook:UserPromptSubmit")
    assert not session.composer.pending
    cleared = [entry for entry in session.state_transitions if entry.get("kind") == "composer"]
    assert [entry["action"] for entry in cleared] == ["cleared"]


def test_typing_during_a_turn_survives_the_tools_that_turn_runs() -> None:
    # Claude accepts typing mid-turn and queues it, so text written while the
    # agent works is genuinely unsent. Every tool call is a same-state detail
    # update through the funnel, and clearing on those erased the operator's
    # half-written follow-up at the next tool the agent happened to run.
    session = ReplaySession("claude")
    session.composer = ComposerState()
    session.transition("working", None, source="hook", evidence="hook:UserPromptSubmit")
    note_composer_write(session.composer, "actually, also check", NOW)
    session.transition("working", "Read", source="hook", evidence="hook:PreToolUse")
    session.transition("working", "Bash", source="hook", evidence="hook:PreToolUse")
    assert session.composer.pending
    # The turn ending does not empty it either: the text is still sitting there.
    session.transition("idle", None, source="hook", evidence="hook:Stop")
    assert session.composer.pending


def test_an_ended_session_has_no_composer_to_report() -> None:
    session = ReplaySession("claude")
    session.composer = ComposerState()
    note_composer_write(session.composer, "half typed", NOW)
    session.transition("exited", None, source="daemon", evidence="process:exit")
    assert not session.composer.pending


def test_clear_composer_reports_whether_anything_was_standing() -> None:
    state = ComposerState()
    assert clear_composer(state) is False
    note_composer_write(state, "x", NOW)
    assert clear_composer(state) is True
    assert state.since == 0.0
