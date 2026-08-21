"""Honest progress for a running verification gate.

Every test here is about one property: nothing is reported that was not observed. The
tempting failure mode is the opposite one - a plausible total, a percentage, a smooth
bar - and each of those makes a running gate *less* trustworthy than the opaque
"verifying" it replaced, because a wrong number is acted on and an absent one is not.
"""

from __future__ import annotations

from swe_mux.verify_progress import (
    MAX_TRACKED_STEPS,
    VerifyProgress,
    plan_matches,
    sanitize_plan,
)


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_a_step_marker_starts_a_step_and_names_it() -> None:
    progress = VerifyProgress(clock=Clock())
    progress.feed(b"\n=== pytest ===\n....\n=== ruff ===\nAll checks passed\n")
    assert progress.observed_steps() == ("pytest", "ruff")
    snapshot = progress.snapshot()
    assert snapshot["step_index"] == 2
    assert snapshot["step_name"] == "ruff"


def test_a_marker_split_across_two_chunks_is_still_one_marker() -> None:
    """The gate's output arrives in 64 KiB reads, which land wherever they land."""
    progress = VerifyProgress(clock=Clock())
    progress.feed(b"\n=== py")
    progress.feed(b"test ===\nrunning\n")
    assert progress.observed_steps() == ("pytest",)


def test_pytest_own_section_rules_are_not_steps() -> None:
    """The pattern is the gate's `step()` helper, not "a line with equals signs".

    A looser rule would report a failing suite's own section headers as verification
    steps, which is the exact moment the reading has to stay trustworthy.
    """
    progress = VerifyProgress(clock=Clock())
    progress.feed(
        b"=========================== FAILURES ===========================\n"
        b"==== short test summary info ====\n"
        b"=== == ===\n"
    )
    assert progress.observed_steps() == ()
    assert progress.snapshot()["step_index"] == 0


def test_with_no_plan_there_is_no_total() -> None:
    """A step number is a fact; a total nobody measured is not."""
    progress = VerifyProgress(clock=Clock())
    progress.feed(b"=== pytest ===\n")
    snapshot = progress.snapshot()
    assert snapshot["step_index"] == 1
    assert snapshot["expected_step_count"] is None
    assert snapshot["expected_steps"] == []


def test_a_plan_from_an_identical_run_supplies_the_total() -> None:
    progress = VerifyProgress(expected_steps=("pytest", "ruff", "mypy"), clock=Clock())
    progress.feed(b"=== pytest ===\n")
    assert progress.snapshot()["expected_step_count"] == 3


def test_a_run_that_overruns_its_plan_stops_predicting() -> None:
    """Never "step 4 of 3". The plan was wrong, so it is withdrawn rather than stretched."""
    progress = VerifyProgress(expected_steps=("pytest", "ruff"), clock=Clock())
    progress.feed(b"=== pytest ===\n=== ruff ===\n=== mypy ===\n")
    snapshot = progress.snapshot()
    assert snapshot["step_index"] == 3
    assert snapshot["beyond_plan"] is True
    assert snapshot["expected_step_count"] is None
    assert snapshot["expected_steps"] == []


def test_elapsed_time_is_measured_per_run_and_per_step() -> None:
    clock = Clock()
    progress = VerifyProgress(clock=clock)
    clock.advance(30)
    progress.feed(b"=== pytest ===\n")
    clock.advance(175)
    progress.feed(b"=== ruff ===\n")
    clock.advance(3)
    snapshot = progress.snapshot()
    assert snapshot["elapsed_ms"] == 208_000.0
    assert snapshot["step_elapsed_ms"] == 3_000.0
    assert snapshot["completed_steps"] == [{"name": "pytest", "duration_ms": 175_000.0}]


def test_a_gate_that_announces_nothing_reports_lines_instead() -> None:
    """The honest fallback: evidence of movement, explicitly not progress toward an end."""
    progress = VerifyProgress(clock=Clock())
    progress.feed(b"one\ntwo\nthree\n")
    snapshot = progress.snapshot()
    assert snapshot["step_index"] == 0
    assert snapshot["lines"] == 3


def test_no_snapshot_field_is_a_percentage() -> None:
    """A gate whose steps take 175s and 3s has no honest denominator for a proportion."""
    progress = VerifyProgress(expected_steps=("a", "b"), clock=Clock())
    progress.feed(b"=== a ===\nwork\n")
    snapshot = progress.snapshot()
    assert not any("percent" in key or "fraction" in key or "ratio" in key for key in snapshot)
    for value in snapshot.values():
        assert not (isinstance(value, float) and 0.0 < value < 1.0)


def test_finishing_closes_the_open_step_and_flushes_a_partial_line() -> None:
    clock = Clock()
    progress = VerifyProgress(clock=clock)
    progress.feed(b"=== pytest ===\ntrailing without a newline")
    clock.advance(12)
    progress.finish()
    snapshot = progress.snapshot()
    assert snapshot["finished"] is True
    assert snapshot["completed_steps"] == [{"name": "pytest", "duration_ms": 12_000.0}]
    assert snapshot["lines"] == 2
    # Time stops at the finish rather than continuing to run against the wall clock.
    clock.advance(600)
    assert progress.snapshot()["elapsed_ms"] == 12_000.0


def test_step_names_are_bounded_and_a_marker_loop_cannot_grow_the_structure() -> None:
    progress = VerifyProgress(clock=Clock())
    for index in range(MAX_TRACKED_STEPS + 20):
        progress.feed(f"=== step {index} ===\n".encode())
    assert len(progress.steps) == MAX_TRACKED_STEPS
    # The count is still honest about how many were seen.
    assert progress.snapshot()["step_index"] == MAX_TRACKED_STEPS + 20


def test_an_enormous_line_is_dropped_rather_than_buffered() -> None:
    progress = VerifyProgress(clock=Clock())
    progress.feed(b"x" * (200 * 1024))
    progress.feed(b"\n=== pytest ===\n")
    assert progress.observed_steps() == ("pytest",)


def test_feeding_undecodable_bytes_never_raises() -> None:
    """This runs inside the pipe reader; a throw here would strand the gate's own output."""
    progress = VerifyProgress(clock=Clock())
    progress.feed(b"\xff\xfe\x00binary\n=== pytest ===\n")
    assert progress.observed_steps() == ("pytest",)


def test_a_malformed_stored_plan_degrades_to_no_plan() -> None:
    """A wrong total is the one failure this whole reading exists to avoid."""
    assert sanitize_plan(["pytest", "ruff"]) == ["pytest", "ruff"]
    assert sanitize_plan(["pytest", 7]) == []
    assert sanitize_plan(["pytest", ""]) == []
    assert sanitize_plan([]) == []
    assert len(sanitize_plan([f"s{index}" for index in range(500)])) == MAX_TRACKED_STEPS


def test_plan_matches_compares_the_whole_walk() -> None:
    assert plan_matches(["a", "b"], ["a", "b"]) is True
    assert plan_matches(["a"], ["a", "b"]) is False
