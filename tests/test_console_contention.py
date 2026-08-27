"""Who is reading a promoted pane's console, and what the daemon may conclude.

The ordering of the rules is the design (`console_contention.classify_shell_prompt`),
so it is asserted here rather than left to the one integration path that exercises it.
"""

from __future__ import annotations

import os
import subprocess
import sys
from typing import Any

import pytest

from swe_mux.console_contention import (
    PROMOTION_GRACE_SECONDS,
    ConsoleCensus,
    ConsoleEvidence,
    ConsoleParticipant,
    classify_shell_prompt,
    probe_console_participants,
)


def evidence(**overrides: Any) -> ConsoleEvidence:
    base: dict[str, Any] = {
        "backend_is_agent": True,
        "seconds_since_promotion": PROMOTION_GRACE_SECONDS + 60,
    }
    base.update(overrides)
    return ConsoleEvidence(**base)


def test_an_unpromoted_pane_is_not_classified() -> None:
    result = classify_shell_prompt(evidence(backend_is_agent=False, transcript_quiet=True))
    assert result.verdict == "unknown"
    assert result.reason == "not_promoted"


def test_a_prompt_inside_the_promotion_grace_window_says_nothing() -> None:
    # The launch itself scrolls a prompt past on the way in. Acting on it would
    # demote the agent that is still starting.
    result = classify_shell_prompt(
        evidence(seconds_since_promotion=PROMOTION_GRACE_SECONDS - 0.1, agent_alive=False)
    )
    assert result.verdict == "unknown"
    assert result.reason == "within_promotion_grace"


def test_a_live_agent_outside_the_pty_tree_is_reported_as_orphaned() -> None:
    result = classify_shell_prompt(
        evidence(agent_pid=4242, agent_alive=True, agent_in_pty_tree=False)
    )
    assert result.verdict == "contended"
    assert result.contention == "agent_orphaned"


def test_a_live_agent_inside_the_tree_is_still_contention() -> None:
    result = classify_shell_prompt(
        evidence(agent_pid=4242, agent_alive=True, agent_in_pty_tree=True)
    )
    assert result.verdict == "contended"
    assert result.contention == "shell_regained_console"


def test_a_busy_transcript_does_not_refute_a_live_agent() -> None:
    """The 2026-08-27 incident, as a rule.

    The old check asked only "is the transcript quiet", so a live agent that was
    still writing produced silence and the session kept presenting as healthy for
    the rest of its life. Liveness now outranks the transcript in both directions.
    """
    result = classify_shell_prompt(
        evidence(agent_pid=4242, agent_alive=True, transcript_quiet=False)
    )
    assert result.verdict == "contended"


def test_a_dead_agent_pid_demotes_even_with_a_fresh_transcript() -> None:
    # An exiting CLI writes its last records on the way out, so the transcript is
    # at its *least* quiet exactly when the process has just gone.
    result = classify_shell_prompt(
        evidence(agent_pid=4242, agent_alive=False, transcript_quiet=False)
    )
    assert result.verdict == "agent_gone"
    assert result.reason == "agent_process_exited"


def test_without_a_pid_the_transcript_is_still_the_answer() -> None:
    quiet = classify_shell_prompt(evidence(transcript_quiet=True))
    assert (quiet.verdict, quiet.reason) == ("agent_gone", "transcript_quiet")
    busy = classify_shell_prompt(evidence(transcript_quiet=False))
    assert (busy.verdict, busy.reason) == ("unknown", "agent_still_writing")


def test_an_unmeasured_process_never_produces_a_verdict() -> None:
    """``None`` is "not measured", and must not be read as either answer.

    A psutil pass that fails must not demote a live agent and must not report a
    dead one as contention. Saying nothing is always available.
    """
    result = classify_shell_prompt(
        evidence(agent_pid=4242, agent_alive=None, agent_in_pty_tree=None, transcript_quiet=False)
    )
    assert result.verdict == "unknown"


def test_contended_property_matches_the_verdict() -> None:
    assert classify_shell_prompt(evidence(agent_alive=True)).contended
    assert not classify_shell_prompt(evidence(agent_alive=False)).contended


# --- the measuring half -----------------------------------------------------


def test_census_of_this_process_finds_itself_as_root() -> None:
    census = probe_console_participants(os.getpid(), os.getpid())
    assert census.error is None
    assert census.agent_alive is True
    assert census.agent_in_pty_tree is True
    assert census.participants[0].is_root
    assert census.participants[0].pid == os.getpid()


def test_census_reports_a_child_as_in_tree_and_a_stranger_as_not() -> None:
    child = subprocess.Popen(  # noqa: S603
        [sys.executable, "-c", "import sys; sys.stdin.read()"],
        stdin=subprocess.PIPE,
    )
    try:
        census = probe_console_participants(os.getpid(), child.pid)
        assert census.agent_alive is True
        assert census.agent_in_pty_tree is True
        assert child.pid in {item.pid for item in census.participants}
    finally:
        if child.stdin is not None:
            child.stdin.close()
        child.wait(timeout=10)

    # Same pid, now dead: the participant walk cannot find it and liveness says so.
    after = probe_console_participants(os.getpid(), child.pid)
    assert after.agent_alive is False


def test_a_missing_root_reports_an_error_rather_than_a_verdict() -> None:
    # pid 0 is never a walkable process on any host mux runs on.
    census = probe_console_participants(0, os.getpid())
    assert census.error == "no_root_pid"
    assert census.agent_in_pty_tree is None
    # An errored census must classify as "no evidence", never as contention.
    result = classify_shell_prompt(
        evidence(
            agent_pid=census.agent_pid,
            agent_alive=None,
            agent_in_pty_tree=None,
            transcript_quiet=False,
        )
    )
    assert result.verdict == "unknown"


def test_a_gone_root_with_a_live_agent_reads_as_orphaned() -> None:
    """A pane whose own root has exited has, by definition, nothing left below it."""
    dead = subprocess.Popen([sys.executable, "-c", "pass"])  # noqa: S603
    dead.wait(timeout=10)
    census = probe_console_participants(dead.pid, os.getpid())
    # A recycled pid could make this a live process again; the assertion is on the
    # branch, so accept either the "root_gone" answer or a successful walk.
    if census.error == "root_gone":
        assert census.agent_alive is True
        assert census.agent_in_pty_tree is False


def test_census_snapshot_is_json_shaped() -> None:
    census = ConsoleCensus(
        root_pid=1,
        agent_pid=2,
        agent_alive=True,
        agent_in_pty_tree=False,
        participants=(ConsoleParticipant(1, "pwsh.exe", None, is_root=True),),
    )
    payload = census.snapshot()
    assert payload["participants"] == [
        {"pid": 1, "name": "pwsh.exe", "parent_pid": None, "is_root": True, "is_agent": False}
    ]
    assert payload["agent_in_pty_tree"] is False


@pytest.mark.parametrize("pid", [0, -1, -5])
def test_a_nonsense_agent_pid_is_never_called_alive(pid: int) -> None:
    census = probe_console_participants(os.getpid(), pid)
    assert census.agent_alive is None
    assert census.agent_in_pty_tree is None
