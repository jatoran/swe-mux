"""The screen classifier against current harnesses' real ConPTY byte streams.

The fixtures under tests/fixtures/pty_tails/ are raw ConPTY captures (scrubbed of
the capturing user's name) taken 2026-07-31 from the installed CLI. They pin the
two facts a synthesized screen cannot: the modern CLI never writes
"esc to interrupt" or "? for shortcuts" at all, and it positions dialog words at
absolute columns so no marker is a contiguous substring of the raw stream. The
classifier survives both — via the frame-recurring spinner ellipsis, the current
dialog/footer affordances, and cursor-movement-as-spacing normalization. If a CLI
update drifts the markers again, recapture with a scrubbed probe and extend the
marker tables; do not weaken these fixtures to synthesized text. The scrubbed omp
fixtures were captured from omp 17.2.10 on 2026-08-06.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from swe_mux.session import (
    AFTER_LAST_PROMPT_MARKER,
    OSC_PROGRESS,
    OSC_TITLE,
    WHOLE_TAIL,
    bottom_non_empty_lines,
    pty_tail_explain,
    pty_tail_state,
    pty_tail_waiting_on_background,
    screen_region_text,
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


def test_background_wait_reads_only_the_background_wait_screen() -> None:
    # Captured 2026-07-31: at turn end with a task still running, the current
    # CLI replaces its idle footer hint with "1 shell still running · check the
    # task status" — neither pre-2.x marker exists anywhere in the stream.
    for fixture in TAILS.glob("*.bin"):
        expected = fixture.name == "background-wait.bin"
        assert pty_tail_waiting_on_background(tail(fixture.name)) is expected, fixture.name


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


def test_cli_waiting_preserves_dialog_across_parallel_tui_redraws() -> None:
    value = tail("permission-dialog-parallel-redraw.txt")

    raw = pty_tail_explain(value, backend="claude")
    effective = pty_tail_explain(
        value,
        backend="claude",
        cli_state_status="waiting",
    )

    # Captured incident shape: raw append order puts spinner animation after a
    # dialog that remains visible in the rendered terminal cells.
    assert raw["outcome"] == "working"
    assert effective["screen_outcome"] == "working"
    assert effective["outcome"] == "approval"
    assert effective["outcome_source"] == "cli_state_waiting"
    assert pty_tail_state(
        value,
        backend="claude",
        cli_state_status="waiting",
    ) == "approval"
    # Once Claude leaves its dialog state, the later spinner is valid proof that
    # work resumed.
    assert pty_tail_state(
        value,
        backend="claude",
        cli_state_status="busy",
    ) == "working"


def test_window_titles_never_classify_a_frame() -> None:
    # The CLI rewrites the terminal title with the task description while
    # working; title text must not be read as screen content — neither an
    # ellipsis inside it nor a terminated marker phrase.
    title_only = "\x1b]0;⠐ Debugging paste truncation…\x07plain output"
    assert pty_tail_state(title_only) == "unknown"
    cut_mid_write = "❯ ready (shift+tab to cycle)\x1b]0;✳ Fixing tests…"
    assert pty_tail_state(cut_mid_write) == "idle"


def test_every_declared_screen_region_extracts_its_fixture() -> None:
    regions = {
        "whole_tail": WHOLE_TAIL,
        "bottom_non_empty_lines": bottom_non_empty_lines(2),
        "after_last_prompt_marker": AFTER_LAST_PROMPT_MARKER,
        "osc_title": OSC_TITLE,
        "osc_progress": OSC_PROGRESS,
    }
    cases = json.loads((TAILS / "regions.json").read_text(encoding="utf-8"))
    for case in cases:
        assert (
            screen_region_text(
                regions[case["region"]],
                case["tail"],
                osc_title=case.get("osc_title"),
                osc_progress=case.get("osc_progress"),
            )
            == case["expected"]
        )


def test_prose_outside_the_live_frame_cannot_report_background_waiting() -> None:
    value = tail("prose-false-positive.txt")
    assert pty_tail_state(value) == "idle"
    assert pty_tail_waiting_on_background(value) is False


def test_agent_owned_viewer_is_uninformative_and_explainable() -> None:
    value = tail("model-picker.txt")
    explanation = pty_tail_explain(value)
    assert explanation["outcome"] == "uninformative"
    assert pty_tail_state(value) == "uninformative"
    first = explanation["rules"][0]
    assert first["id"] == "viewer.model_picker"
    assert first["state"] == "uninformative"
    assert first["region"] == "bottom_non_empty_lines"
    assert first["region_lines"] == 12
    assert first["matched"] is True
    assert "Select a model" in first["preview"]
    assert "Enter to select - Esc to cancel" in first["preview"]


def test_omp_captured_idle_prompt_classifies() -> None:
    explanation = pty_tail_explain(tail("omp-idle.txt"), backend="omp")
    assert explanation["outcome"] == "idle"
    matched = [rule for rule in explanation["rules"] if rule["matched"]]
    assert any(rule["id"] == "idle.omp_prompt" for rule in matched)


def test_omp_idle_thinking_level_glyph_is_not_a_working_spinner() -> None:
    explanation = pty_tail_explain(tail("omp-idle-thinking-level.txt"), backend="omp")
    assert explanation["outcome"] == "idle"
    spinner = next(rule for rule in explanation["rules"] if rule["id"] == "working.spinner")
    assert spinner["applicable"] is False
    assert spinner["matched"] is False
    assert any(
        rule["id"] == "idle.omp_prompt" and rule["matched"]
        for rule in explanation["rules"]
    )


def test_omp_captured_model_picker_is_uninformative() -> None:
    explanation = pty_tail_explain(tail("omp-model-picker.txt"), backend="omp")
    assert explanation["outcome"] == "uninformative"
    matched = [rule for rule in explanation["rules"] if rule["matched"]]
    assert any(rule["id"] == "viewer.omp_model_picker" for rule in matched)


def test_omp_captured_session_tree_is_uninformative() -> None:
    explanation = pty_tail_explain(tail("omp-session-tree.txt"), backend="omp")
    assert explanation["outcome"] == "uninformative"
    matched = [rule for rule in explanation["rules"] if rule["matched"]]
    assert any(rule["id"] == "viewer.omp_session_tree" for rule in matched)
