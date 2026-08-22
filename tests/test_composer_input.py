"""The unsent-composer estimate: what each write does to it, and what clears it."""

from __future__ import annotations

from swe_mux.composer_input import (
    BRACKETED_PASTE_END,
    BRACKETED_PASTE_START,
    DEFAULT_NEWLINE_KEYS,
    ComposerState,
    classify_composer_write,
    clear_composer,
    composer_insertion,
    note_composer_write,
)
from swe_mux.harness import HARNESSES, composer_insertion_rules
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
    # Ctrl+C is universal; Ctrl+U is the default a harness gets when it declares
    # nothing, and is what the shells mux drives implement.
    for key in ("\x03", "\x15"):
        state = ComposerState()
        note_composer_write(state, "half a thought", NOW)
        assert note_composer_write(state, key, NOW + 1) == "cleared", key


def test_the_harness_declares_what_else_clears_its_composer() -> None:
    # The measured Claude case, which the old fixed key set got wrong in both
    # directions: its clear is a double Esc, and Ctrl+U there kills only a line.
    state = ComposerState()
    note_composer_write(state, "half a thought", NOW, "\x1b\x1b")
    assert note_composer_write(state, "\x1b\x1b", NOW + 1, "\x1b\x1b") == "cleared"

    standing = ComposerState()
    note_composer_write(standing, "half a thought", NOW, "\x1b\x1b")
    # Ctrl+U is not this harness's clear, so it must not zero the estimate. A
    # false "empty" is what lets a gate that reads this let something through.
    assert note_composer_write(standing, "\x15", NOW + 1, "\x1b\x1b") is None
    assert standing.pending

    # A bare Esc is not a clear anywhere: it is also the first byte of every
    # cursor key, and it did nothing to a real Claude draft.
    bare = ComposerState()
    note_composer_write(bare, "half a thought", NOW, "\x1b\x1b")
    assert note_composer_write(bare, "\x1b", NOW + 1, "\x1b\x1b") is None
    assert bare.pending


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


# ------------------------------------------------- the composer newline key


def test_the_composer_newline_key_is_composed_text_and_not_a_submit() -> None:
    # ESC+CR is not a control sequence the escape stripper matches, so its bare
    # CR used to survive and classify the whole write as a submit. That is a
    # false "empty" over a standing draft, and it fired on the rail's own
    # Markdown divider button, which has always sent exactly these bytes.
    state = ComposerState()
    note_composer_write(state, "first line", NOW)
    assert note_composer_write(state, DEFAULT_NEWLINE_KEYS, NOW + 1) is None
    assert state.pending
    assert state.chars == len("first line") + 1


def test_a_whole_markdown_divider_leaves_the_draft_standing() -> None:
    state = ComposerState()
    note_composer_write(state, "a paragraph", NOW)
    break_ = DEFAULT_NEWLINE_KEYS
    divider = f"{break_}{break_}---{break_}{break_}"
    assert note_composer_write(state, divider, NOW + 1) is None
    assert state.pending
    assert state.chars == len("a paragraph") + 4 + len("---")


def test_a_harness_that_declares_no_newline_key_keeps_the_old_reading() -> None:
    # Passing "" turns the recognition off rather than guessing, and a bare CR
    # is then what it has always been.
    assert classify_composer_write("\x1b\r", newline_keys="").kind == "submit"


def test_a_real_carriage_return_is_still_a_submit() -> None:
    state = ComposerState()
    note_composer_write(state, "ship it", NOW)
    assert note_composer_write(state, "\r", NOW + 1) == "cleared"


# --------------------------------------------------- building an insertion


def test_an_insertion_is_a_bracketed_paste_with_cr_line_breaks() -> None:
    assert composer_insertion("a\r\nb\rc\nd") == (
        f"{BRACKETED_PASTE_START}a\rb\rc\rd{BRACKETED_PASTE_END}"
    )


def test_a_leading_newline_is_lifted_into_keys_where_a_paste_would_submit() -> None:
    """The measured Codex defect, and the shape of the repair.

    Measured 2026-08-22 against Codex v0.149.0 over a real pseudoterminal with
    `PREVIOUSTEXT` unsent in the composer: a paste with interior newlines is a
    three-line draft, while the same paste with one leading CR submits the draft
    and pastes the rest. The live "Tree" template begins with a newline, which is
    how a prompt button came to send someone's half-typed draft to the model.
    """
    tree = "\nWork in a separate worktree. Do not land it and do not redeploy."
    body = tree.lstrip("\n")
    lifted = composer_insertion(tree, lift_leading_newline=True)
    assert lifted == (
        f"{DEFAULT_NEWLINE_KEYS}{BRACKETED_PASTE_START}{body}{BRACKETED_PASTE_END}"
    )
    # And the untouched reading, for a harness that has no such defect.
    assert composer_insertion(tree) == (
        f"{BRACKETED_PASTE_START}\r{body}{BRACKETED_PASTE_END}"
    )


def test_every_leading_newline_is_lifted_and_interior_ones_are_not() -> None:
    assert composer_insertion("\n\n\nx", lift_leading_newline=True) == (
        f"{DEFAULT_NEWLINE_KEYS * 3}{BRACKETED_PASTE_START}x{BRACKETED_PASTE_END}"
    )
    assert composer_insertion("a\nb", lift_leading_newline=True) == (
        f"{BRACKETED_PASTE_START}a\rb{BRACKETED_PASTE_END}"
    )


def test_text_that_is_only_newlines_produces_keys_and_no_empty_paste() -> None:
    assert composer_insertion("\n\n", lift_leading_newline=True) == DEFAULT_NEWLINE_KEYS * 2
    assert composer_insertion("", lift_leading_newline=True) == ""


def test_an_insertion_never_opens_with_a_bare_newline_on_any_harness() -> None:
    # The invariant the builder exists to hold: the first byte written is never a
    # newline, because that is the one byte the paste wrapper cannot protect.
    tree = "\n\nWork in a separate worktree."
    for name in [*HARNESSES, "shell", "not-a-harness"]:
        newline_keys, lift = composer_insertion_rules(name)
        payload = composer_insertion(
            tree, newline_keys=newline_keys, lift_leading_newline=lift
        )
        assert not payload.startswith(("\r", "\n")), name
        if lift:
            assert payload.startswith(newline_keys), name
            assert not payload.startswith(BRACKETED_PASTE_START), name


def test_only_codex_is_declared_as_submitting_on_a_leading_newline() -> None:
    # A measured fact, so the registry says which harness it was measured on.
    # Everything else keeps the bytes mux has always sent it.
    submits = {name for name, rules in HARNESSES.items() if rules.paste_leading_newline_submits}
    assert submits == {"codex"}


def test_a_lifted_insertion_reads_back_as_composed_text_not_a_submit() -> None:
    # The builder and the classifier are the two halves of the same fact, and
    # this is the seam where they used to disagree: the lifted ESC+CR would have
    # zeroed the very estimate the write was supposed to raise.
    state = ComposerState()
    note_composer_write(state, "half typed", NOW)
    newline_keys, lift = composer_insertion_rules("codex")
    payload = composer_insertion(
        "\nappended", newline_keys=newline_keys, lift_leading_newline=lift
    )
    assert note_composer_write(state, payload, NOW + 1, newline_keys=newline_keys) is None
    assert state.pending
    assert state.chars == len("half typed") + 1 + len("appended")
