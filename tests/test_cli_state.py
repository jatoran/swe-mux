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

from swe_mux.cli_state import CLI_STATE_SETTLE_SECONDS, CliStateMonitor
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
) -> Path:
    path = root / f"{pid}.json"
    path.write_text(
        json.dumps(
            {
                "sessionId": session_id,
                "cwd": cwd,
                "pid": pid,
                "procStart": "639211298070889210",
                "kind": "interactive",
                "name": "test",
                "status": status,
                "statusUpdatedAt": status_updated_at_ms,
                "updatedAt": status_updated_at_ms,
                "version": "2.1.220",
            }
        ),
        encoding="utf-8",
    )
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
