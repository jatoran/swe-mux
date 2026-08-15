"""The `cli-state` corroboration layer: Claude's per-process side state.

Phase C of status v2: the poller reads `~/.claude/sessions/<pid>.json`
(verified shape 2026-07-31), matches files to sessions by conversation id,
counts settled status disagreements and deterministically observed nested
children — and never drives a SessionState transition.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from swe_mux.cli_state import (
    CLI_STATE_SETTLE_SECONDS,
    PARKED_MOVE_ATTEMPTS,
    CliStateMonitor,
)
from tests.support.detection_replay import ReplaySession

OWN = "11111111-2222-4333-8444-555566667777"
FOREIGN = "99999999-8888-4777-8666-555544443333"


def write_state(
    root: Path,
    pid: int,
    session_id: str,
    *,
    cwd: str,
    status: str = "idle",
    status_updated_at_ms: float = 0.0,
    kind: str = "interactive",
    parked_job_id: str | None = None,
    started_at_ms: float = 0.0,
    job_id: str | None = None,
) -> Path:
    path = root / f"{pid}.json"
    payload: dict[str, Any] = {
        "sessionId": session_id,
        "cwd": cwd,
        "pid": pid,
        "procStart": "639211298070889210",
        "startedAt": started_at_ms,
        "kind": kind,
        "name": "test",
        "status": status,
        "statusUpdatedAt": status_updated_at_ms,
        "updatedAt": status_updated_at_ms,
        "version": "2.1.220",
    }
    if job_id is not None:
        payload["jobId"] = job_id
    if parked_job_id is not None:
        payload["parkedJobId"] = parked_job_id
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def write_job(
    root: Path,
    job_id: str,
    session_id: str,
    *,
    cwd: str,
    state: str = "working",
    transcript: str | None = None,
) -> Path:
    """A background job as Claude 2.1.227 publishes it (`jobs/<id>/state.json`)."""
    path = root / job_id / "state.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "sessionId": session_id,
        "resumeSessionId": session_id,
        "cwd": cwd,
        "state": state,
        "template": "bg",
        "name": "parked work",
    }
    if transcript is not None:
        payload["linkScanPath"] = transcript
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def claude_session(cwd: str = ".") -> ReplaySession:
    session = ReplaySession("claude")
    session.record.native_session_id = OWN
    session.record.cwd = cwd
    session.record.run_cwd = cwd
    return session


def ledger(session: Any, kind: str) -> list[dict[str, Any]]:
    return [entry for entry in session.state_transitions if entry.get("kind") == kind]


def test_poll_parses_and_caches_by_stat(tmp_path: Path) -> None:
    monitor = CliStateMonitor(tmp_path)
    write_state(tmp_path, 100, OWN, cwd=str(tmp_path), status="busy", status_updated_at_ms=5000)
    (states,) = monitor.poll()
    assert (states.session_id, states.status, states.status_updated_at) == (OWN, "busy", 5.0)
    # Unparseable files are cached as absent, not retried into an error loop.
    bad = tmp_path / "101.json"
    bad.write_text("{not json", encoding="utf-8")
    assert len(monitor.poll()) == 1
    # A vanished file drops out of the cache.
    bad.unlink()
    (tmp_path / "100.json").unlink()
    assert monitor.poll() == []


def test_settled_disagreement_counts_once_per_standing_fact(tmp_path: Path) -> None:
    session = claude_session(str(tmp_path))
    session.record.state = "idle"
    now = session.clock.wall()
    session.last_state_change_ts = now - 60
    monitor = CliStateMonitor(tmp_path)
    write_state(
        tmp_path, 100, OWN, cwd=str(tmp_path), status="busy",
        status_updated_at_ms=(now - 60) * 1000,
    )
    states = monitor.poll()
    monitor.observe(states, [session], now)
    monitor.observe(states, [session], now + 5)
    monitor.observe(states, [session], now + 10)
    assert session.status_health_counters["cli_state_disagrees"] == 1
    (entry,) = ledger(session, "cli_state")
    assert entry["action"] == "status_disagrees"
    assert (entry["cli_status"], entry["mux_state"]) == ("busy", "idle")
    # The session's own file snapshot is surfaced for the state-log endpoint.
    assert session.cli_state is not None and session.cli_state["status"] == "busy"
    # A NEW status flip that still disagrees counts again.
    write_state(
        tmp_path, 100, OWN, cwd=str(tmp_path), status="busy",
        status_updated_at_ms=(now + 20) * 1000,
    )
    monitor.observe(monitor.poll(), [session], now + 120)
    assert session.status_health_counters["cli_state_disagrees"] == 2


def test_unsettled_or_agreeing_status_never_counts(tmp_path: Path) -> None:
    session = claude_session(str(tmp_path))
    session.record.state = "working"
    now = session.clock.wall()
    session.last_state_change_ts = now - 60
    monitor = CliStateMonitor(tmp_path)
    # Agreement: busy vs working.
    write_state(
        tmp_path, 100, OWN, cwd=str(tmp_path), status="busy",
        status_updated_at_ms=(now - 60) * 1000,
    )
    monitor.observe(monitor.poll(), [session], now)
    assert "cli_state_disagrees" not in session.status_health_counters
    # Disagreement inside the settle window: the flip races mux's transition.
    write_state(
        tmp_path, 100, OWN, cwd=str(tmp_path), status="idle",
        status_updated_at_ms=(now - CLI_STATE_SETTLE_SECONDS / 2) * 1000,
    )
    monitor.observe(monitor.poll(), [session], now)
    assert "cli_state_disagrees" not in session.status_health_counters
    # The comparison never drives state.
    assert session.record.state == "working"


def test_nested_child_is_observed_deterministically(tmp_path: Path) -> None:
    session = claude_session(str(tmp_path))
    now = session.clock.wall()
    session.record.agent_run_started_at = now - 300
    monitor = CliStateMonitor(tmp_path)
    write_state(
        tmp_path, 200, FOREIGN, cwd=str(tmp_path), status="busy",
        status_updated_at_ms=(now - 10) * 1000,
    )
    states = monitor.poll()
    monitor.observe(states, [session], now)
    monitor.observe(states, [session], now + 5)
    assert session.status_health_counters["nested_children_observed"] == 1
    (entry,) = ledger(session, "cli_state")
    assert entry["action"] == "nested_child_observed"
    assert entry["native_session_id"] == FOREIGN


def test_nested_child_attribution_requires_an_unambiguous_owner(tmp_path: Path) -> None:
    # Two live sessions in one cwd: a wrong nested-child count is worse than a
    # missed one, so ambiguity stands down.
    first = claude_session(str(tmp_path))
    second = claude_session(str(tmp_path))
    second.record.native_session_id = "22222222-3333-4444-8555-666677778888"
    now = first.clock.wall()
    monitor = CliStateMonitor(tmp_path)
    write_state(
        tmp_path, 200, FOREIGN, cwd=str(tmp_path), status="busy",
        status_updated_at_ms=(now - 10) * 1000,
    )
    monitor.observe(monitor.poll(), [first, second], now)
    assert "nested_children_observed" not in first.status_health_counters
    assert "nested_children_observed" not in second.status_health_counters


def test_a_live_siblings_file_is_never_a_nested_child(tmp_path: Path) -> None:
    session = claude_session(str(tmp_path))
    sibling = claude_session(str(tmp_path / "elsewhere"))
    sibling.record.native_session_id = FOREIGN
    now = session.clock.wall()
    monitor = CliStateMonitor(tmp_path)
    write_state(
        tmp_path, 200, FOREIGN, cwd=str(tmp_path), status="busy",
        status_updated_at_ms=(now - 10) * 1000,
    )
    monitor.observe(monitor.poll(), [session, sibling], now)
    assert "nested_children_observed" not in session.status_health_counters


def test_a_leftover_file_from_before_this_run_is_not_this_runs_child(tmp_path: Path) -> None:
    session = claude_session(str(tmp_path))
    now = session.clock.wall()
    session.record.agent_run_started_at = now - 10
    monitor = CliStateMonitor(tmp_path)
    write_state(
        tmp_path, 200, FOREIGN, cwd=str(tmp_path), status="idle",
        status_updated_at_ms=(now - 3600) * 1000,
    )
    monitor.observe(monitor.poll(), [session], now)
    assert "nested_children_observed" not in session.status_health_counters


# --- who holds a conversation right now ---------------------------------------
#
# A conversation opens once. The set of conversations a live CLI process already
# holds is therefore exactly the set of resumes that would exit instead of
# starting, and mux reads it to refuse such a resume rather than hand back a pane
# that is already gone.


def holder_monitor(
    tmp_path: Path, live: dict[int, float] | None = None
) -> tuple[CliStateMonitor, Path]:
    """A monitor whose only running processes are the pids in ``live``."""
    root = tmp_path / "sessions"
    root.mkdir(exist_ok=True)
    starts = dict(live or {})
    return CliStateMonitor(root, process_start=lambda pid: starts.get(pid)), root


def test_a_live_background_cli_holds_its_conversation(tmp_path: Path) -> None:
    monitor, root = holder_monitor(tmp_path, {100: 1000.0})
    write_state(
        root, 100, OWN, cwd=str(tmp_path), kind="bg", started_at_ms=1000_000, job_id="job1"
    )

    held = monitor.conversation_holder("claude", OWN)

    assert held is not None
    assert (held.pid, held.kind, held.job_id) == (100, "bg", "job1")
    assert held.is_background_agent
    # The refusal has to name the way back to the conversation, not just decline.
    assert "claude agents" in held.describe()


def test_a_state_file_whose_process_is_gone_holds_nothing(tmp_path: Path) -> None:
    # A CLI killed hard leaves its file behind. Treating that as a holder would
    # make a free conversation permanently unresumable, which is the one failure
    # this check must never cause.
    monitor, root = holder_monitor(tmp_path)
    write_state(root, 100, OWN, cwd=str(tmp_path), kind="bg", started_at_ms=1000_000)

    assert monitor.conversation_holder("claude", OWN) is None


def test_a_reused_pid_holds_nothing(tmp_path: Path) -> None:
    # The pid is alive, but it started long after the file was written, so it is
    # not the process the file describes.
    monitor, root = holder_monitor(tmp_path, {100: 9000.0})
    write_state(root, 100, OWN, cwd=str(tmp_path), kind="bg", started_at_ms=1000_000)

    assert monitor.conversation_holder("claude", OWN) is None


def test_the_cli_stamping_its_file_after_it_starts_still_holds(tmp_path: Path) -> None:
    # The file is always written after the process it describes exists (measured
    # 0.42-1.31 s behind live). Reading that lag as a mismatch would silently
    # disable the guard on every real session.
    monitor, root = holder_monitor(tmp_path, {100: 1000.0})
    write_state(root, 100, OWN, cwd=str(tmp_path), kind="bg", started_at_ms=1001_310)

    assert monitor.conversation_holder("claude", OWN) is not None


def test_a_harness_that_publishes_no_side_state_is_never_asked(tmp_path: Path) -> None:
    # The directory belongs to Claude. A Codex thread id that happened to collide
    # with a conversation id in it must not answer for Codex.
    monitor, root = holder_monitor(tmp_path, {100: 1000.0})
    write_state(root, 100, OWN, cwd=str(tmp_path), kind="bg", started_at_ms=1000_000)

    assert monitor.conversation_holder("codex", OWN) is None
    assert monitor.conversation_holder("claude", FOREIGN) is None


def test_holders_are_read_fresh_rather_than_from_the_poll_cache(tmp_path: Path) -> None:
    # Ownership is asked at the moment an operator resumes something, where a
    # five-second-old answer is a wrong answer.
    monitor, root = holder_monitor(tmp_path, {100: 1000.0})
    path = write_state(root, 100, OWN, cwd=str(tmp_path), kind="bg", started_at_ms=1000_000)
    monitor.poll()
    path.unlink()

    assert monitor.conversation_holders() == {}


# --- backgrounded conversations (Claude 2.1.227 `parkedJobId`) ----------------
#
# The pane keeps its spawn conversation id while every record lands in the job's
# own conversation, and no hook ever reports the move, so this layer is the only
# thing that can keep such a pane observable.
PARKED = "44444444-3333-4222-8111-000099998888"


def parked_monitor(tmp_path: Path) -> tuple[CliStateMonitor, Path, Path]:
    sessions_root = tmp_path / "sessions"
    jobs_root = tmp_path / "jobs"
    sessions_root.mkdir()
    jobs_root.mkdir()
    return CliStateMonitor(sessions_root, jobs_root), sessions_root, jobs_root


def test_a_parked_conversation_is_reported_as_a_move_to_follow(tmp_path: Path) -> None:
    session = claude_session(str(tmp_path))
    monitor, sessions_root, jobs_root = parked_monitor(tmp_path)
    write_state(sessions_root, 100, OWN, cwd=str(tmp_path), parked_job_id="job1")
    transcript = str(tmp_path / "parked.jsonl")
    write_job(jobs_root, "job1", PARKED, cwd=str(tmp_path), transcript=transcript)

    (move,) = monitor.observe(monitor.poll(), [session], session.clock.wall())

    assert (move.session_id, move.native_session_id) == (session.record.id, PARKED)
    # The job names the file it writes; the pane's cwd must not be re-guessed.
    assert move.transcript == transcript
    assert move.job_id == "job1"
    (entry,) = [
        item
        for item in ledger(session, "cli_state")
        if item["action"] == "conversation_parked"
    ]
    assert entry["native_session_id"] == PARKED


def test_following_the_move_stops_it_being_reported_again(tmp_path: Path) -> None:
    """Idempotence is the record itself, not bookkeeping that can drift."""
    session = claude_session(str(tmp_path))
    monitor, sessions_root, jobs_root = parked_monitor(tmp_path)
    write_state(sessions_root, 100, OWN, cwd=str(tmp_path), parked_job_id="job1")
    write_job(jobs_root, "job1", PARKED, cwd=str(tmp_path))
    assert monitor.observe(monitor.poll(), [session], session.clock.wall())

    session.record.native_session_id = PARKED

    assert monitor.observe(monitor.poll(), [session], session.clock.wall()) == []


def test_an_unadopted_move_is_retried_but_bounded(tmp_path: Path) -> None:
    """An unadoptable move must not thrash the observer once per poll forever."""
    session = claude_session(str(tmp_path))
    monitor, sessions_root, jobs_root = parked_monitor(tmp_path)
    write_state(sessions_root, 100, OWN, cwd=str(tmp_path), parked_job_id="job1")
    write_job(jobs_root, "job1", PARKED, cwd=str(tmp_path))

    yielded = [
        bool(monitor.observe(monitor.poll(), [session], session.clock.wall()))
        for _ in range(PARKED_MOVE_ATTEMPTS + 2)
    ]

    assert yielded == [True] * PARKED_MOVE_ATTEMPTS + [False, False]


def test_a_background_clis_own_file_never_moves_a_pane(tmp_path: Path) -> None:
    """Only the interactive CLI speaks for where the pane's conversation went."""
    session = claude_session(str(tmp_path))
    monitor, sessions_root, jobs_root = parked_monitor(tmp_path)
    write_state(sessions_root, 100, OWN, cwd=str(tmp_path), kind="bg", parked_job_id="job1")
    write_job(jobs_root, "job1", PARKED, cwd=str(tmp_path))

    assert monitor.observe(monitor.poll(), [session], session.clock.wall()) == []


def test_a_job_in_another_directory_is_not_this_panes_conversation(tmp_path: Path) -> None:
    session = claude_session(str(tmp_path))
    monitor, sessions_root, jobs_root = parked_monitor(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    write_state(sessions_root, 100, OWN, cwd=str(tmp_path), parked_job_id="job1")
    write_job(jobs_root, "job1", PARKED, cwd=str(elsewhere))

    assert monitor.observe(monitor.poll(), [session], session.clock.wall()) == []


def test_a_missing_job_leaves_the_pane_where_it_is(tmp_path: Path) -> None:
    session = claude_session(str(tmp_path))
    monitor, sessions_root, _ = parked_monitor(tmp_path)
    write_state(sessions_root, 100, OWN, cwd=str(tmp_path), parked_job_id="gone")

    assert monitor.observe(monitor.poll(), [session], session.clock.wall()) == []
    assert session.cli_state is not None


def test_a_conversation_this_pane_retired_is_not_a_nested_child(tmp_path: Path) -> None:
    """After the move the interactive CLI's file still names the retired id."""
    session = claude_session(str(tmp_path))
    now = session.clock.wall()
    session.record.agent_run_started_at = now - 300
    monitor, sessions_root, _ = parked_monitor(tmp_path)
    write_state(
        sessions_root, 100, OWN, cwd=str(tmp_path), status="idle",
        status_updated_at_ms=(now - 10) * 1000,
    )
    session.record.native_session_id = PARKED
    session.ignored_detection_runs.add(("claude", OWN))

    monitor.observe(monitor.poll(), [session], now)

    assert "nested_children_observed" not in session.status_health_counters
