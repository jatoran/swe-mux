"""Phase 3.5 status contract: per-state evidence predicates, the typed transition
ledger, the proven/inferred classification, watchdog decision thresholds, and the
fleet status-health bounds."""

from __future__ import annotations

from typing import Any, cast, get_args

import pytest

from swe_mux.models import AwaitingReason, SessionState
from swe_mux.session import (
    INFERRED_TRANSITION_SOURCES,
    SCREEN_CLASSIFIER_BLIND_SECONDS,
    STATE_EVIDENCE_SOURCES,
    STATE_WATCHDOG_AWAITING_RESUME_SECONDS,
    STATE_WATCHDOG_ENDED_STUCK_SECONDS,
    STATE_WATCHDOG_PTY_STUCK_SECONDS,
    STATE_WATCHDOG_STARTUP_DIALOG_SECONDS,
    STATUS_HEALTH_MAX_INFERRED_TERMINAL_RATIO,
    STATUS_HEALTH_MIN_TERMINALS_FOR_RATIO_ALARM,
    STATUS_HEALTH_STUCK_ACTIVE_SECONDS,
    fleet_status_health,
    note_classifier_blindness,
    pty_tail_appears_idle,
    pty_tail_state,
    pty_tail_waiting_on_background,
    startup_dialog_observation,
    transition_proof,
    watchdog_decision,
)
from tests.support.detection_replay import DetectionReplay, ReplaySession


def test_state_evidence_sources_cover_every_session_state() -> None:
    # The contract is total: every SessionState names the sources allowed to
    # set it, and no state is settable by an empty evidence set.
    assert set(STATE_EVIDENCE_SOURCES) == set(get_args(SessionState))
    for state, sources in STATE_EVIDENCE_SOURCES.items():
        assert sources, f"{state} has no allowed evidence sources"
    # Active states require provider evidence; the PTY may never invent work.
    assert "pty" not in STATE_EVIDENCE_SOURCES["working"]
    assert "pty" not in STATE_EVIDENCE_SOURCES["awaiting"]
    # Inferred recovery is confined to resolving an active state — idle (a turn
    # that ended without its marker), working (an approval the user already
    # answered) — plus exactly one inferred path into `awaiting`: the
    # startup-dialog rule, which may raise `awaiting(approval)` only on a
    # session no turn has ever run (trust/update dialogs block before turn one
    # while no proven source can ever report them). Lifecycle states stay
    # un-inferable.
    for state, sources in STATE_EVIDENCE_SOURCES.items():
        if state not in {"idle", "working", "awaiting"}:
            assert not (sources & INFERRED_TRANSITION_SOURCES), state
    assert STATE_EVIDENCE_SOURCES["working"] & INFERRED_TRANSITION_SOURCES == {"watchdog-pty"}
    assert STATE_EVIDENCE_SOURCES["awaiting"] & INFERRED_TRANSITION_SOURCES == {"watchdog-pty"}
    # The startup rule is the only way the screen may raise a block, and it is
    # gated on "no turn has ever run": with a turn history the same screen
    # changes nothing.
    assert (
        watchdog_decision(
            "idle",
            stalled_seconds=10_000,
            tail_verdict=None,
            pty_state="approval",
            startup_no_turn=True,
            startup_dialog_seconds=30.0,
        )
        == "startup_dialog_block"
    )
    assert (
        watchdog_decision(
            "idle",
            stalled_seconds=10_000,
            tail_verdict=None,
            pty_state="approval",
            startup_no_turn=False,
            startup_dialog_seconds=30.0,
        )
        == "none"
    )
    # ...and the only inferred path into `working` starts from `awaiting`.
    assert (
        watchdog_decision(
            "idle", stalled_seconds=10_000, tail_verdict=None, pty_state="working"
        )
        == "none"
    )


def test_transition_proof_classification() -> None:
    assert transition_proof("transcript") == "proven"
    assert transition_proof("hook") == "proven"
    assert transition_proof("pty") == "proven"
    assert transition_proof("daemon") == "proven"
    assert transition_proof("watchdog") == "inferred"
    assert transition_proof("watchdog-pty") == "inferred"
    # The startup-quiet fallback marks itself inferred explicitly.
    assert transition_proof("pty", inferred=True) == "inferred"


def test_transition_ledger_entries_are_complete_and_typed() -> None:
    session = ReplaySession("claude")
    assert session.transition(
        "working", "Read", source="transcript", evidence="tool_use_record"
    )
    session.clock.advance(3.5)
    assert session.transition(
        "idle", None, source="transcript", evidence="stop_reason=end_turn"
    )
    entries = [e for e in session.state_transitions if e["kind"] == "transition"]
    assert len(entries) == 2
    for entry in entries:
        for key in (
            "ts",
            "monotonic",
            "previous",
            "state",
            "detail",
            "awaiting_reason",
            "source",
            "priority",
            "evidence",
            "proof",
            "allowed_source",
            "seconds_in_previous",
        ):
            assert key in entry, f"ledger entry missing {key}"
    assert entries[0]["evidence"] == "tool_use_record"
    assert entries[1]["seconds_in_previous"] == pytest.approx(3.5)
    # Turn-terminal latency is measured from entering the active state.
    assert session.terminal_latencies[0]["seconds"] == pytest.approx(3.5)
    assert session.terminal_latencies[0]["proof"] == "proven"


def test_contract_violation_is_ledgered_and_counted_not_refused() -> None:
    session = ReplaySession("claude")
    # "pty" may never set working; the transition still applies (conservatively
    # refusing could strand a session) but is flagged for the corpus to catch.
    assert session.transition("working", None, source="pty", evidence="bogus")
    entry = [e for e in session.state_transitions if e["kind"] == "transition"][-1]
    assert entry["allowed_source"] is False
    assert session.status_health_counters["contract_violations"] == 1


def test_awaiting_reason_is_typed_and_cleared_on_leaving_awaiting() -> None:
    session = ReplaySession("claude")
    assert set(get_args(AwaitingReason)) == {"approval", "question", "elicitation", "rate_limit"}
    session.transition(
        "awaiting", "Bash", source="hook", awaiting_reason="approval", evidence="hook"
    )
    assert session.record.awaiting_reason == "approval"
    session.transition("working", None, source="hook", evidence="hook")
    assert session.record.awaiting_reason is None
    # A non-awaiting transition can never carry a sub-reason.
    session.transition("idle", None, source="hook", awaiting_reason="approval", evidence="hook")
    assert session.record.awaiting_reason is None


@pytest.mark.parametrize(
    ("state", "stalled", "verdict", "pty_state", "expected"),
    [
        # Below the ENDED-stuck threshold nothing fires, even with proof.
        ("working", STATE_WATCHDOG_ENDED_STUCK_SECONDS - 0.1, "ended", "idle", "none"),
        # At the threshold a proven-ended tail force-idles.
        ("working", STATE_WATCHDOG_ENDED_STUCK_SECONDS, "ended", "unknown", "force_idle_ended"),
        ("awaiting", STATE_WATCHDOG_ENDED_STUCK_SECONDS, "ended", "unknown", "force_idle_ended"),
        # unknown/open tails wait for the full PTY stall window.
        ("working", STATE_WATCHDOG_PTY_STUCK_SECONDS - 0.1, "open", "idle", "none"),
        ("working", STATE_WATCHDOG_PTY_STUCK_SECONDS, "open", "idle", "force_idle_pty"),
        ("working", STATE_WATCHDOG_PTY_STUCK_SECONDS, "unknown", "idle", "force_idle_pty"),
        # A busy CLI (esc to interrupt) can never be cut short.
        ("working", STATE_WATCHDOG_PTY_STUCK_SECONDS * 10, "open", "working", "none"),
        # A session parked at a real permission dialog is not "idle at the
        # prompt": the backstop must not force-idle it and hide the prompt.
        ("awaiting", STATE_WATCHDOG_PTY_STUCK_SECONDS * 10, "open", "approval", "none"),
        ("awaiting", STATE_WATCHDOG_PTY_STUCK_SECONDS * 10, "unknown", "unknown", "none"),
        # An answered prompt: the CLI is back to its working spinner.
        ("awaiting", STATE_WATCHDOG_AWAITING_RESUME_SECONDS, None, "working", "resume_working"),
        ("awaiting", STATE_WATCHDOG_AWAITING_RESUME_SECONDS, "open", "working", "resume_working"),
        # ...but not before the dialog has had time to finish painting.
        (
            "awaiting",
            STATE_WATCHDOG_AWAITING_RESUME_SECONDS - 0.1,
            None,
            "working",
            "none",
        ),
        # Resume never applies to a session that is already working.
        ("working", STATE_WATCHDOG_ENDED_STUCK_SECONDS, None, "working", "none"),
        # Non-active states are never touched.
        ("idle", STATE_WATCHDOG_PTY_STUCK_SECONDS * 10, "ended", "idle", "none"),
        ("exited", STATE_WATCHDOG_PTY_STUCK_SECONDS * 10, "ended", "idle", "none"),
    ],
)
def test_watchdog_decision_pins_thresholds(
    state: Any, stalled: float, verdict: str | None, pty_state: Any, expected: str
) -> None:
    assert (
        watchdog_decision(
            state,
            stalled_seconds=stalled,
            tail_verdict=verdict,
            pty_state=pty_state,
        )
        == expected
    )


def test_resume_working_is_confined_to_answered_approvals() -> None:
    # Approval is the only block whose dialog the tail classifier can recognize.
    # A Codex question or an elicitation shows neither marker, while redraw
    # history in the same tail still holds "esc to interrupt" from before the
    # block — resuming on that would hide a prompt the user has not answered,
    # which the design forbids outright.
    for reason in ("question", "elicitation", "rate_limit"):
        assert (
            watchdog_decision(
                "awaiting",
                stalled_seconds=STATE_WATCHDOG_AWAITING_RESUME_SECONDS,
                tail_verdict=None,
                pty_state="working",
                awaiting_reason=reason,
            )
            == "none"
        ), reason
    assert (
        watchdog_decision(
            "awaiting",
            stalled_seconds=STATE_WATCHDOG_AWAITING_RESUME_SECONDS,
            tail_verdict=None,
            pty_state="working",
            awaiting_reason="approval",
        )
        == "resume_working"
    )
    # An unset reason keeps the historical behavior (approval is the default).
    assert (
        watchdog_decision(
            "awaiting",
            stalled_seconds=STATE_WATCHDOG_AWAITING_RESUME_SECONDS,
            tail_verdict=None,
            pty_state="working",
        )
        == "resume_working"
    )


def test_pty_tail_idle_heuristic_branches() -> None:
    assert pty_tail_appears_idle("❯  ? for shortcuts") is True
    assert pty_tail_appears_idle("? FOR SHORTCUTS") is True
    # A later working spinner wins over an earlier idle hint.
    assert pty_tail_appears_idle("? for shortcuts ... esc to interrupt") is False
    # An unrecognized TUI reads as not-idle (fail-safe).
    assert pty_tail_appears_idle("some other prompt") is False
    assert pty_tail_appears_idle("") is False


def test_pty_tail_state_reads_the_latest_frame_not_mere_presence() -> None:
    # The retained tail holds redraw history: a session that showed a dialog and
    # then resumed still contains the dialog text, so only ordering can say what
    # is on screen now. Getting this wrong is what strands an answered approval.
    assert pty_tail_state("") == "unknown"
    assert pty_tail_state("nothing recognizable here") == "unknown"
    assert pty_tail_state("Do you want to proceed?\n❯ 1. Yes") == "approval"
    assert pty_tail_state("esc to interrupt") == "working"
    assert pty_tail_state("❯ ? for shortcuts") == "idle"
    # Dialog raised, then answered → the spinner is the live frame.
    assert (
        pty_tail_state("Do you want to proceed?\n❯ 1. Yes\nRunning… esc to interrupt")
        == "working"
    )
    # Turn finished after the dialog → back at the input prompt.
    assert (
        pty_tail_state("Do you want to proceed?\nesc to interrupt\n❯ ? for shortcuts")
        == "idle"
    )
    # A dialog raised *after* earlier work is still the live frame.
    assert (
        pty_tail_state("esc to interrupt\n? for shortcuts\nDo you want to run this command?")
        == "approval"
    )


async def test_trailing_completion_record_does_not_blink_working() -> None:
    """A transcript end_turn landing after a hook Stop closed the turn must not
    re-enter working for an instant (the residual status blink)."""
    replay = DetectionReplay("claude")
    await replay.step({"kind": "hook", "event": "UserPromptSubmit", "payload": {}})
    assert replay.session.record.state == "working"
    await replay.step({"kind": "hook", "event": "Stop", "payload": {}})
    assert replay.session.record.state == "idle"
    await replay.step(
        {
            "kind": "transcript",
            "record": {
                "type": "assistant",
                "isSidechain": False,
                "message": {
                    "content": [{"type": "text", "text": "done"}],
                    "stop_reason": "end_turn",
                },
            },
        }
    )
    states = [e for e in replay.session.state_transitions if e["kind"] == "transition"]
    assert [e["state"] for e in states] == ["working", "idle"]
    assert replay.session.record.state == "idle"


async def _approval_then(replay: DetectionReplay, tool: str = "Bash") -> None:
    """Drive a session to a hook-raised approval over an authoritative transcript."""
    await replay.step(
        {
            "kind": "transcript",
            "ts_offset": 0,
            "record": {"type": "user", "isSidechain": False, "message": {"content": "go"}},
        }
    )
    await replay.step(
        {
            "kind": "transcript",
            "ts_offset": 0,
            "record": {
                "type": "assistant",
                "isSidechain": False,
                "message": {
                    "content": [{"type": "tool_use", "id": "t1", "name": tool, "input": {}}],
                    "stop_reason": "tool_use",
                },
            },
        }
    )
    assert replay.session.record.parser_status == "ready"
    await replay.step(
        {"kind": "hook", "event": "PermissionRequest", "payload": {"tool_name": tool}}
    )
    assert replay.session.record.state == "awaiting"


async def test_answered_approval_does_not_outlive_the_block() -> None:
    """The reported defect, reproduced from a real session's ledger.

    A hook raises `awaiting` at priority 2; every record proving the agent
    resumed arrives on the transcript at priority 1, and `_begin_root_turn`
    does not release arbitration while the state is active. Live, that showed
    "awaiting approval" for 558 seconds of real work — through two further
    approval prompts — and only corrected when the turn's forced close landed.
    """
    replay = DetectionReplay("claude")
    await _approval_then(replay)
    replay.clock.advance(12)  # the user reads the prompt and approves

    await replay.step(
        {
            "kind": "transcript",
            "ts_offset": 0,
            "record": {
                "type": "user",
                "isSidechain": False,
                "message": {
                    "content": [
                        {"type": "tool_result", "tool_use_id": "t1", "content": "ok"}
                    ]
                },
            },
        }
    )
    assert replay.session.record.state == "working"
    assert replay.session.record.awaiting_reason is None
    entry = [e for e in replay.session.state_transitions if e["kind"] == "transition"][-1]
    assert entry["evidence"] == "resumed_after_awaiting:tool_result_record"
    assert entry["proof"] == "proven"


async def test_unanswered_approval_survives_everything_that_could_hide_it() -> None:
    """Hiding a real prompt is worse than showing a stale one.

    Nothing short of proof may clear an awaiting: not the tool_use record that
    triggered it arriving late, not a parallel tool finishing while the dialog
    is up, and not an idle_prompt notification.
    """
    replay = DetectionReplay("claude")
    await _approval_then(replay, tool="Edit")
    await replay.step(
        {"kind": "pty_tail", "data": "Do you want to make this edit to app.py?\n❯ 1. Yes"}
    )

    # The record that caused the prompt, observed after it (transcript polling
    # lags the hook's immediate POST).
    await replay.step(
        {
            "kind": "transcript",
            "ts_offset": -5,
            "record": {
                "type": "assistant",
                "isSidechain": False,
                "message": {
                    "content": [{"type": "tool_use", "id": "t9", "name": "Edit", "input": {}}],
                    "stop_reason": "tool_use",
                },
            },
        }
    )
    assert replay.session.record.state == "awaiting"

    # A parallel tool completing is not an answer to this prompt.
    replay.clock.advance(10)
    await replay.step(
        {
            "kind": "transcript",
            "ts_offset": 0,
            "record": {
                "type": "user",
                "isSidechain": False,
                "message": {
                    "content": [{"type": "tool_result", "tool_use_id": "t8", "content": "ok"}]
                },
            },
        }
    )
    assert replay.session.record.state == "awaiting"
    assert replay.session.record.awaiting_reason == "approval"

    # Neither is a notification that the CLI is waiting for input.
    await replay.step(
        {"kind": "hook", "event": "Notification", "payload": {"notification_type": "idle_prompt"}}
    )
    assert replay.session.record.state == "awaiting"

    # ...and the PTY backstop must not force-idle a session parked at a dialog,
    # which would hide the prompt entirely.
    replay.clock.advance(600)
    await replay.step({"kind": "watchdog"})
    assert replay.session.record.state == "awaiting"
    assert replay.session.watchdog_recoveries == 0


async def test_idle_prompt_clears_awaiting_only_with_screen_proof() -> None:
    replay = DetectionReplay("claude")
    await _approval_then(replay)
    # Screen is back at the input prompt: the dialog is provably gone, which is
    # exactly what the notification claims.
    await replay.step({"kind": "pty_tail", "data": "❯ ? for shortcuts"})
    await replay.step(
        {"kind": "hook", "event": "Notification", "payload": {"notification_type": "idle_prompt"}}
    )
    assert replay.session.record.state == "idle"
    assert replay.session.record.awaiting_reason is None


async def test_rate_limit_maps_to_awaiting_rate_limit() -> None:
    codex = DetectionReplay("codex")
    await codex.step(
        {"kind": "transcript", "record": {"type": "event_msg", "payload": {"type": "task_started"}}}
    )
    await codex.step(
        {"kind": "transcript", "record": {"type": "event_msg", "payload": {"type": "rate_limited"}}}
    )
    assert codex.session.record.state == "awaiting"
    assert codex.session.record.awaiting_reason == "rate_limit"

    claude = DetectionReplay("claude")
    await claude.step({"kind": "hook", "event": "UserPromptSubmit", "payload": {}})
    await claude.step(
        {
            "kind": "hook",
            "event": "Notification",
            "payload": {"notification_type": "rate_limit"},
        }
    )
    assert claude.session.record.state == "awaiting"
    assert claude.session.record.awaiting_reason == "rate_limit"


async def test_compaction_records_never_disturb_turn_state() -> None:
    replay = DetectionReplay("claude")
    await replay.step(
        {
            "kind": "transcript",
            "record": {"type": "user", "isSidechain": False, "message": {"content": "go"}},
        }
    )
    assert replay.session.record.state == "working"
    await replay.step(
        {"kind": "transcript", "record": {"type": "system", "subtype": "compact_boundary"}}
    )
    assert replay.session.record.state == "working"
    compacted = [e for e in replay.normalized if e["type"] == "context_compacted"]
    assert len(compacted) == 1


def _health_stub(
    *,
    proven: int = 0,
    inferred: int = 0,
    state: str = "idle",
    seconds_in_state: float = 0.0,
    seconds_since_evidence: float | None = None,
    violations: int = 0,
) -> ReplaySession:
    session = ReplaySession("claude")
    session.record.state = state  # type: ignore[assignment]
    counters = session.status_health_counters
    counters["terminal_proven"] = proven
    counters["terminal_inferred"] = inferred
    if violations:
        counters["contract_violations"] = violations
    session.last_state_change_ts = session.clock.wall() - seconds_in_state
    session.last_evidence_ts = session.clock.wall() - (
        seconds_in_state if seconds_since_evidence is None else seconds_since_evidence
    )
    return session


def test_fleet_status_health_alarm_bounds() -> None:
    now = ReplaySession("claude").clock.wall()
    healthy = fleet_status_health([_health_stub(proven=100, inferred=2)], now=now)
    assert healthy["alarm"] is False
    assert healthy["terminals"]["inferred_ratio"] == pytest.approx(2 / 102, abs=1e-4)

    # Ratio alarm only fires with enough terminals to be meaningful.
    small = fleet_status_health([_health_stub(proven=2, inferred=2)], now=now)
    assert small["alarm"] is False

    minimum = STATUS_HEALTH_MIN_TERMINALS_FOR_RATIO_ALARM
    ratio_breach = fleet_status_health(
        [_health_stub(proven=minimum, inferred=int(minimum * 0.2))], now=now
    )
    assert ratio_breach["alarm"] is True
    assert "inferred_terminal_ratio_exceeded" in ratio_breach["alarm_reasons"]
    assert (
        ratio_breach["terminals"]["inferred_ratio"]
        > STATUS_HEALTH_MAX_INFERRED_TERMINAL_RATIO
    )

    # A long turn is NOT stuck: tools keep landing evidence even though the
    # visible state stays "working" for many minutes. An elapsed-time-only
    # bound alarmed on every healthy long turn in the live fleet.
    long_turn = fleet_status_health(
        [
            _health_stub(
                state="working",
                seconds_in_state=STATUS_HEALTH_STUCK_ACTIVE_SECONDS * 3,
                seconds_since_evidence=20.0,
            )
        ],
        now=now,
    )
    assert long_turn["alarm"] is False
    assert long_turn["stuck_sessions"] == []

    # Silence is the real signal: no observation of any kind for the window.
    stuck = fleet_status_health(
        [
            _health_stub(
                state="working",
                seconds_in_state=STATUS_HEALTH_STUCK_ACTIVE_SECONDS + 1,
                seconds_since_evidence=STATUS_HEALTH_STUCK_ACTIVE_SECONDS + 1,
            )
        ],
        now=now,
    )
    assert stuck["alarm"] is True
    assert "session_stuck_active" in stuck["alarm_reasons"]
    assert stuck["stuck_sessions"] == ["replay-session"]

    violated = fleet_status_health([_health_stub(violations=1)], now=now)
    assert violated["alarm"] is True
    assert "status_contract_violation" in violated["alarm_reasons"]

    # Shell sessions are outside the agent status contract.
    shell = ReplaySession("claude")
    shell.record.backend = "shell"
    assert fleet_status_health([shell], now=now)["sessions"] == []


def test_background_wait_is_an_idle_sub_reason_not_a_state() -> None:
    # `✻ Waiting for N background tasks to finish` means the turn genuinely ended
    # — the composer accepts input and delivery is safe — while the agent is
    # going to wake itself. Rendering that as a plain "ready · turn complete" is
    # true and misleading at once, so it becomes an idle sub-reason.
    assert pty_tail_waiting_on_background("✻ Waiting for 2 background tasks to finish") is True
    # The current CLI's shape, captured in `background-wait.bin`. The noun varies
    # (`shell`, `monitor`); the count is what makes it a footer.
    assert pty_tail_waiting_on_background("✻ churned for 4s · 1 shell still running") is True
    assert pty_tail_waiting_on_background("✻ crunched for 36s · 1 monitor still running") is True
    assert pty_tail_waiting_on_background("· 2 shells still running · check the tasks") is True
    # A live turn is `working`, never a background wait.
    assert (
        pty_tail_waiting_on_background("Waiting for tasks\nthinking… (esc to interrupt)") is False
    )
    assert pty_tail_waiting_on_background("❯ ? for shortcuts") is False
    assert pty_tail_waiting_on_background("") is False
    # Prose is not a footer. The screen this is read from is 32 KiB of redraw
    # traffic that also carries the user's prompts, the agent's replies, and any
    # tool output, so a marker arbitrary English can satisfy is not evidence -
    # `background-wait.bin` itself contains a prompt reading "wait for it, then
    # say done". Requiring a count is what separates the two.
    assert pty_tail_waiting_on_background("running a background task…") is False
    assert pty_tail_waiting_on_background("the daemon is still running") is False
    assert pty_tail_waiting_on_background("I am waiting for the build to finish") is False
    assert pty_tail_waiting_on_background("checked whether the harness is still running") is False
    # The state itself is unchanged: this never invents or blocks a state.
    assert pty_tail_state("✻ Waiting for 2 background tasks to finish") == "unknown"


async def test_state_changed_carries_what_the_notification_path_filters_on() -> None:
    """`state_changed` is what "the agent is waiting for your input" is raised from,
    so every field that decides whether to interrupt a human has to be on it.

    It used to carry neither `idle_reason` nor the standing axis, which made both
    suppression rules in push.py and sessionSounds.ts unreachable: they read fields
    that only ever rode `turn_ended`, a category mobile has off by default. The
    result was a phone buzzing "ready" for turns whose agent was still working.
    """
    from swe_mux.event_bus import EventBus
    from swe_mux.observation import _transition
    from swe_mux.push import classify_notification
    from swe_mux.session import set_standing_activity

    session = cast(Any, ReplaySession("claude"))
    session.record.state = "working"
    events = EventBus()
    queue = events.subscribe()
    set_standing_activity(session, "subagents", source="hook", evidence="hook:SubagentStart")
    await _transition(
        session, events, "idle", source="transcript", evidence="stop_reason=end_turn"
    )
    emitted = queue.get_nowait()
    assert emitted.type == "state_changed"
    assert emitted.payload["standing"] == ["subagents"]
    assert emitted.payload["previous"] == "working"
    # The end of this contract is the consumer: a turn end with subagents still
    # running must not become a lock-screen alert.
    assert classify_notification(emitted) is None


def test_idle_reason_is_ledgered_and_cleared_by_leaving_idle() -> None:
    session = ReplaySession("claude")
    assert session.transition(
        "idle", None, source="transcript", evidence="end_turn", idle_reason="waiting_on_background"
    )
    assert session.record.idle_reason == "waiting_on_background"
    entry = [e for e in session.state_transitions if e["kind"] == "transition"][-1]
    assert entry["idle_reason"] == "waiting_on_background"
    # The self-wake back into a turn clears it, like awaiting_reason.
    assert session.transition("working", None, source="transcript", evidence="tool_use_record")
    assert session.record.idle_reason is None
    # ...and an ordinary idle does not inherit the previous sub-reason.
    assert session.transition("idle", None, source="transcript", evidence="end_turn")
    assert session.record.idle_reason is None


# ---- status v2 Phase C: startup dialogs and the classifier-drift self-check


TRUST_DIALOG = (
    "Accessing workspace: C:/scratch/repo\n"
    "Quick safety check: Is this a project you created or one you trust?\n"
    "❯ 1. Yes, I trust this folder\n  2. No, exit\nEnter to confirm · Esc to cancel"
)


async def test_startup_dialog_blocks_and_clears_from_the_screen_alone() -> None:
    # G7: the trust dialog blocks the session while hook-sourced idle wins the
    # display and typed input lands in the dialog. The watchdog raises
    # awaiting(approval) after a sustained approval screen on a session no turn
    # has ever run, and the same rule un-blocks when the screen moves on.
    replay = DetectionReplay("claude")
    await replay.step({"kind": "hook", "event": "SessionStart", "payload": {}})
    assert replay.session.record.state == "idle"
    await replay.step({"kind": "pty_tail", "data": TRUST_DIALOG})
    await replay.step({"kind": "watchdog"})
    # First read only starts the sustain clock: a repaint cannot raise a block.
    assert replay.session.record.state == "idle"
    await replay.step(
        {"kind": "timer", "seconds": STATE_WATCHDOG_STARTUP_DIALOG_SECONDS + 2.0}
    )
    await replay.step({"kind": "watchdog"})
    assert replay.session.record.state == "awaiting"
    assert replay.session.record.awaiting_reason == "approval"
    assert replay.readiness.evaluate(replay.session)["delivery_state"] == "blocked"
    # The user answered: the screen shows the idle footer again.
    await replay.step({"kind": "pty_tail", "data": "❯ accept edits on (shift+tab to cycle)"})
    await replay.step({"kind": "watchdog"})
    assert replay.session.record.state == "idle"
    assert replay.session.record.awaiting_reason is None


async def test_startup_dialog_rule_stands_down_once_any_turn_has_run() -> None:
    replay = DetectionReplay("claude")
    session = replay.session
    await replay.step({"kind": "hook", "event": "SessionStart", "payload": {}})
    await replay.step(
        {
            "kind": "transcript",
            "ts_offset": 0,
            "record": {"type": "user", "isSidechain": False, "message": {"content": "hi"}},
        }
    )
    await replay.step(
        {
            "kind": "transcript",
            "ts_offset": 1,
            "record": {"type": "system", "subtype": "turn_duration", "durationMs": 900},
        }
    )
    assert session.record.state == "idle"
    # A dialog-looking screen mid-session (a /config menu, a real permission
    # prompt raced ahead of its hook) changes nothing through this rule.
    await replay.step({"kind": "pty_tail", "data": TRUST_DIALOG})
    await replay.step({"kind": "watchdog"})
    await replay.step({"kind": "timer", "seconds": STATE_WATCHDOG_STARTUP_DIALOG_SECONDS * 3})
    await replay.step({"kind": "watchdog"})
    assert session.record.state == "idle"
    no_turn, seconds = startup_dialog_observation(session, "approval", session.clock.wall())
    assert no_turn is False
    assert seconds is None


async def test_classifier_blindness_counts_once_per_blind_window() -> None:
    # G6: a witnessed session continuously working while every screen read
    # returns "unknown" is the marker-drift failure mode. The self-check counts
    # it (once per window) and never changes state.
    replay = DetectionReplay("claude")
    session = replay.session
    await replay.step(
        {
            "kind": "transcript",
            "ts_offset": 0,
            "record": {"type": "user", "isSidechain": False, "message": {"content": "build"}},
        }
    )
    assert session.record.state == "working"
    await replay.step({"kind": "pty_tail", "data": "a redesigned TUI with no known markers"})
    await replay.step({"kind": "watchdog"})
    assert "screen_classifier_blind" not in session.status_health_counters
    await replay.step({"kind": "timer", "seconds": SCREEN_CLASSIFIER_BLIND_SECONDS + 10.0})
    await replay.step({"kind": "watchdog"})
    assert session.status_health_counters["screen_classifier_blind"] == 1
    assert session.record.state == "working"
    await replay.step({"kind": "watchdog"})
    assert session.status_health_counters["screen_classifier_blind"] == 1
    entry = [e for e in session.state_transitions if e.get("kind") == "screen_classifier_blind"]
    assert len(entry) == 1
    # A readable screen ends the window; a later blind window counts again.
    await replay.step({"kind": "pty_tail", "data": "thinking…"})
    await replay.step({"kind": "watchdog"})
    await replay.step({"kind": "pty_tail", "data": "another unreadable frame"})
    await replay.step({"kind": "watchdog"})
    await replay.step({"kind": "timer", "seconds": SCREEN_CLASSIFIER_BLIND_SECONDS + 10.0})
    await replay.step({"kind": "watchdog"})
    assert session.status_health_counters["screen_classifier_blind"] == 2


def test_classifier_blindness_never_reads_an_unwitnessed_session() -> None:
    # An unwitnessed session's state CAME from the screen; blindness there is
    # definitionally impossible and the counter must not fire.
    session = ReplaySession("codex")
    session.record.state = "working"
    now = session.clock.wall()
    assert note_classifier_blindness(session, "unknown", now) is False
    assert note_classifier_blindness(session, "unknown", now + 500) is False
    assert "screen_classifier_blind" not in session.status_health_counters


def test_classifier_blind_fleet_alarm_needs_two_sessions() -> None:
    one = ReplaySession("claude")
    one.status_health_counters["screen_classifier_blind"] = 1
    now = one.clock.wall()
    single = fleet_status_health([one], now=now)
    assert single["classifier_blind_sessions"] == ["replay-session"]
    assert "screen_classifier_blind" not in single["alarm_reasons"]
    two = ReplaySession("claude")
    two.status_health_counters["screen_classifier_blind"] = 2
    both = fleet_status_health([one, two], now=now)
    assert "screen_classifier_blind" in both["alarm_reasons"]
    assert both["consolidation_counters"]["screen_classifier_blind"] == 3
