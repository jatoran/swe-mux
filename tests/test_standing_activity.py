"""Standing-activity annotations (status v2 Phase A): model + plumbing.

Annotations are the fifth status axis — an armed loop, a cron schedule,
background tasks, live subagents — and never states: SessionState,
awaiting_reason, and delivery are untouched by every operation here. These
tests pin the add/refresh/expire/clear discipline, the non-transition ledger
entries, the run-scope clears at lifecycle seams, and snapshot round-tripping
across daemon restarts.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from swe_mux.models import SessionRecord, StandingActivity
from swe_mux.session import (
    clear_all_standing_activity,
    clear_standing_activity,
    expire_standing_activity,
    set_standing_activity,
)
from tests.support.detection_replay import DetectionReplay, ReplaySession

from .test_conversation_rollover import CLEARED, agent_record, rollover_manager


def ledger_entries(session: Any) -> list[dict[str, Any]]:
    return [
        entry for entry in session.state_transitions if entry.get("kind") == "standing_activity"
    ]


# ----------------------------------------------------------------- set/refresh


def test_adding_an_annotation_ledgers_and_reports_change() -> None:
    session = ReplaySession("claude")
    changed = set_standing_activity(
        session,
        "loop",
        source="transcript",
        evidence="transcript:ScheduleWakeup",
        expires_at=session.clock.wall() + 1500,
        detail="watching CI run",
        now=session.clock.wall(),
    )
    assert changed is True
    (activity,) = session.record.standing_activity
    assert activity.kind == "loop"
    assert activity.since == session.clock.wall()
    (entry,) = ledger_entries(session)
    assert entry["action"] == "added"
    assert entry["activity"] == "loop"
    assert entry["evidence"] == "transcript:ScheduleWakeup"


def test_a_pure_ttl_refresh_is_silent() -> None:
    # Subagent/background evidence renews at tool-record cadence; ledgering or
    # fanning out every renewal would bury the entries that matter.
    session = ReplaySession("claude")
    now = session.clock.wall()
    set_standing_activity(
        session, "subagents", source="hook", evidence="hook:SubagentStart", count=2,
        expires_at=now + 120, now=now,
    )
    changed = set_standing_activity(
        session, "subagents", source="hook", evidence="hook:SubagentStart", count=2,
        expires_at=now + 180, now=now + 60,
    )
    assert changed is False
    (activity,) = session.record.standing_activity
    assert activity.expires_at == now + 180
    assert activity.since == now  # refresh keeps the original arm time
    assert len(ledger_entries(session)) == 1  # only the add


def test_a_count_change_ledgers_an_update() -> None:
    session = ReplaySession("claude")
    now = session.clock.wall()
    set_standing_activity(
        session, "subagents", source="hook", evidence="hook:SubagentStart", count=1, now=now
    )
    changed = set_standing_activity(
        session, "subagents", source="hook", evidence="hook:SubagentStart", count=3, now=now
    )
    assert changed is True
    assert session.record.standing_activity[0].count == 3
    assert [entry["action"] for entry in ledger_entries(session)] == ["added", "updated"]


def test_annotations_compose_across_kinds() -> None:
    session = ReplaySession("claude")
    now = session.clock.wall()
    set_standing_activity(session, "loop", source="transcript", evidence="e", now=now)
    set_standing_activity(session, "background_tasks", source="transcript", evidence="e", now=now)
    set_standing_activity(session, "subagents", source="hook", evidence="e", now=now)
    assert {a.kind for a in session.record.standing_activity} == {
        "loop",
        "background_tasks",
        "subagents",
    }


# ----------------------------------------------------------------- clear/expire


def test_positive_clear_ledgers_removal() -> None:
    session = ReplaySession("claude")
    now = session.clock.wall()
    set_standing_activity(session, "loop", source="transcript", evidence="armed", now=now)
    assert clear_standing_activity(session, "loop", evidence="ScheduleWakeup:stop", now=now)
    assert session.record.standing_activity == []
    assert ledger_entries(session)[-1]["action"] == "removed"
    assert ledger_entries(session)[-1]["evidence"] == "ScheduleWakeup:stop"
    # Clearing what is not set is a no-op, not an error.
    assert not clear_standing_activity(session, "loop", evidence="again", now=now)


def test_expiry_drops_only_past_due_annotations_and_counts() -> None:
    session = ReplaySession("claude")
    now = session.clock.wall()
    set_standing_activity(
        session, "loop", source="transcript", evidence="e", expires_at=now + 100, now=now
    )
    set_standing_activity(
        session, "subagents", source="hook", evidence="e", expires_at=now + 500, now=now
    )
    set_standing_activity(session, "cron", source="transcript", evidence="e", now=now)

    assert expire_standing_activity(session, now=now + 50) == []
    expired = expire_standing_activity(session, now=now + 120)
    assert expired == ["loop"]
    # A None expiry means "until positively cleared" — it never decays.
    remaining = {a.kind for a in session.record.standing_activity}
    assert remaining == {"subagents", "cron"}
    assert session.status_health_counters["standing_activity_expired"] == 1
    assert ledger_entries(session)[-1]["action"] == "expired"


def test_clear_all_ledgers_each_annotation() -> None:
    session = ReplaySession("claude")
    now = session.clock.wall()
    set_standing_activity(session, "loop", source="transcript", evidence="e", now=now)
    set_standing_activity(session, "cron", source="transcript", evidence="e", now=now)
    assert clear_all_standing_activity(session, evidence="conversation_rolled:clear", now=now)
    assert session.record.standing_activity == []
    removed = [entry for entry in ledger_entries(session) if entry["action"] == "removed"]
    assert {entry["activity"] for entry in removed} == {"loop", "cron"}
    assert all(entry["evidence"] == "conversation_rolled:clear" for entry in removed)
    assert not clear_all_standing_activity(session, evidence="again", now=now)


# ------------------------------------------------------------ axis separation


def test_annotations_never_touch_state_or_awaiting_or_idle_reason() -> None:
    session = ReplaySession("claude")
    session.record.state = "idle"
    session.record.awaiting_reason = None
    session.record.idle_reason = None
    now = session.clock.wall()
    set_standing_activity(session, "loop", source="transcript", evidence="e", now=now)
    expire_standing_activity(session, now=now)
    clear_standing_activity(session, "loop", evidence="e", now=now)
    assert session.record.state == "idle"
    assert session.record.awaiting_reason is None
    assert session.record.idle_reason is None
    # Annotation entries are non-transition ledger entries: nothing here may
    # count as a state transition or as terminal evidence.
    assert "transitions" not in session.status_health_counters


# ------------------------------------------------------------- lifecycle seams


async def test_conversation_rollover_clears_annotations(tmp_path: Path) -> None:
    record = agent_record(cwd=str(tmp_path))
    manager, session = rollover_manager(record)
    set_standing_activity(session, "loop", source="transcript", evidence="armed")
    set_standing_activity(session, "subagents", source="hook", evidence="e", count=2)

    rolled = await manager._apply_conversation_rollover(
        session,
        native_id=CLEARED,
        transcript=tmp_path / f"{CLEARED}.jsonl",
        reason="conversation_rolled",
        source="clear",
    )

    assert rolled is True
    assert record.standing_activity == []
    removed = [entry for entry in ledger_entries(session) if entry["action"] == "removed"]
    assert {entry["activity"] for entry in removed} == {"loop", "subagents"}
    assert all(entry["evidence"] == "conversation_rolled:clear" for entry in removed)


def test_provider_observation_reset_drops_annotations() -> None:
    # The adoption-repair paths reset a bare record (no Session, no ledger);
    # annotations belong to the identity being repaired away.
    from swe_mux.session import SessionManager

    record = agent_record()
    record.standing_activity.append(
        StandingActivity(kind="cron", source="transcript", evidence="e", since=1.0)
    )
    SessionManager._reset_provider_observation(record)
    assert record.standing_activity == []


# --------------------------------------------------------------- serialization


def test_snapshot_carries_and_round_trips_annotations() -> None:
    record = agent_record()
    snapshot = record.snapshot()
    assert snapshot["standing_activity"] == []

    record.standing_activity.append(
        StandingActivity(
            kind="loop",
            source="transcript",
            evidence="transcript:ScheduleWakeup",
            since=100.0,
            expires_at=1720.0,
            detail="watching CI run",
        )
    )
    snapshot = record.snapshot()
    assert snapshot["standing_activity"] == [
        {
            "kind": "loop",
            "source": "transcript",
            "evidence": "transcript:ScheduleWakeup",
            "since": 100.0,
            "expires_at": 1720.0,
            "count": 1,
            "detail": "watching CI run",
        }
    ]
    restored = SessionRecord.from_snapshot(snapshot)
    assert restored.standing_activity == record.standing_activity


def test_adoption_tolerates_schema_drift() -> None:
    record = agent_record()
    snapshot = record.snapshot()
    # An older daemon's snapshot has no field at all.
    snapshot.pop("standing_activity")
    assert SessionRecord.from_snapshot(snapshot).standing_activity == []
    # A newer daemon's snapshot may carry unknown per-annotation keys; a
    # malformed item is dropped rather than poisoning the whole adoption.
    snapshot["standing_activity"] = [
        {
            "kind": "cron",
            "source": "transcript",
            "evidence": "transcript:CronCreate",
            "since": 5.0,
            "future_field": True,
        },
        {"kind": "loop"},  # missing required fields
        "not-a-dict",
    ]
    restored = SessionRecord.from_snapshot(snapshot)
    (activity,) = restored.standing_activity
    assert activity.kind == "cron"
    assert activity.evidence == "transcript:CronCreate"


# ------------------------------------------------------ Claude signal extraction

OWN_CONVERSATION = "11111111-2222-4333-8444-555566667777"
FOREIGN_CONVERSATION = "99999999-8888-4777-8666-555544443333"


async def test_foreign_subagent_hook_never_counts() -> None:
    # A nested child CLI inherits the hook wiring; its SubagentStart names the
    # child's conversation. The foreign filter runs before annotation
    # extraction, so the child's fleet never counts as this session's.
    replay = DetectionReplay("claude")
    replay.session.record.native_session_id = OWN_CONVERSATION
    await replay.step(
        {
            "kind": "hook",
            "event": "SubagentStart",
            "payload": {"session_id": FOREIGN_CONVERSATION, "agent_id": "child-probe"},
        }
    )
    assert replay.session.record.standing_activity == []
    await replay.step(
        {
            "kind": "hook",
            "event": "SubagentStart",
            "payload": {"session_id": OWN_CONVERSATION, "agent_id": "own-agent"},
        }
    )
    (activity,) = replay.session.record.standing_activity
    assert (activity.kind, activity.count, activity.source) == ("subagents", 1, "hook")


async def test_subagent_hooks_own_the_count_over_the_transcript_fallback() -> None:
    # Once a lifecycle hook has arrived this run, a Task tool_use launch only
    # refreshes recency — double-counting one subagent from two tiers is the
    # cross-source failure mode this split exists to prevent.
    replay = DetectionReplay("claude")
    replay.session.record.native_session_id = OWN_CONVERSATION
    await replay.step(
        {
            "kind": "hook",
            "event": "SubagentStart",
            "payload": {"session_id": OWN_CONVERSATION, "agent_id": "agent-1"},
        }
    )
    await replay.step(
        {
            "kind": "transcript",
            "ts_offset": 1,
            "record": {
                "type": "assistant",
                "isSidechain": False,
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "toolu_t1",
                            "name": "Task",
                            "input": {"description": "helper", "prompt": "go"},
                        }
                    ]
                },
            },
        }
    )
    (activity,) = replay.session.record.standing_activity
    assert activity.count == 1


async def test_trailing_transcript_records_never_reopen_a_hook_cleared_fleet() -> None:
    # Observed live 2026-07-31: hooks counted the subagent up and down, then the
    # transcript's slower channel delivered the Task tool_result and sidechain
    # records afterward — re-creating the annotation the hooks had already
    # cleared. Refresh-tier sources must refresh only, never re-open.
    replay = DetectionReplay("claude")
    replay.session.record.native_session_id = OWN_CONVERSATION
    await replay.step(
        {
            "kind": "hook",
            "event": "SubagentStart",
            "payload": {"session_id": OWN_CONVERSATION, "agent_id": "agent-1"},
        }
    )
    await replay.step(
        {
            "kind": "hook",
            "event": "SubagentStop",
            "payload": {"session_id": OWN_CONVERSATION, "agent_id": "agent-1"},
        }
    )
    assert replay.session.record.standing_activity == []
    launch = {
        "type": "assistant",
        "isSidechain": False,
        "message": {
            "content": [
                {
                    "type": "tool_use",
                    "id": "toolu_t1",
                    "name": "Task",
                    "input": {"description": "helper", "prompt": "go"},
                }
            ]
        },
    }
    sidechain = {
        "type": "assistant",
        "isSidechain": True,
        "message": {"content": [{"type": "text", "text": "trailing"}]},
    }
    completion = {
        "type": "user",
        "isSidechain": False,
        "message": {
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "toolu_t1",
                    "content": [{"type": "text", "text": "hi"}],
                }
            ]
        },
    }
    for offset, record in ((1, launch), (2, sidechain), (3, completion)):
        await replay.step({"kind": "transcript", "ts_offset": offset, "record": record})
        assert replay.session.record.standing_activity == []


def _subagent_tool_hook(event: str = "PreToolUse") -> dict[str, Any]:
    """A subagent-scoped tool hook: the stream a live background agent emits."""
    return {
        "kind": "hook",
        "event": event,
        "payload": {
            "session_id": OWN_CONVERSATION,
            "agent_id": "agent-1",
            "tool_name": "Bash",
        },
    }


async def test_subagent_tool_hooks_keep_the_fleet_alive_without_transcript_evidence() -> None:
    # Measured live 2026-08-02: a background subagent writes NOTHING into the
    # root transcript (16 minutes of agent work, zero isSidechain records), so
    # its tool-hook stream is the only recency evidence there is. Dropping it
    # let the TTL expire the annotation ~2 minutes in while agents kept
    # working, and the session rendered a bare "ready · turn complete".
    replay = DetectionReplay("claude")
    replay.session.record.native_session_id = OWN_CONVERSATION
    await replay.step(
        {
            "kind": "hook",
            "event": "SubagentStart",
            "payload": {"session_id": OWN_CONVERSATION, "agent_id": "agent-1"},
        }
    )
    # Past the original TTL, but the agent's tool hooks kept refreshing.
    await replay.step({"kind": "timer", "seconds": 100})
    await replay.step(_subagent_tool_hook())
    await replay.step({"kind": "timer", "seconds": 100})
    await replay.step({"kind": "watchdog"})
    (activity,) = replay.session.record.standing_activity
    assert (activity.kind, activity.count) == ("subagents", 1)
    # Silence on every channel still decays it: liveness is recency, not a latch.
    await replay.step({"kind": "timer", "seconds": 130})
    await replay.step({"kind": "watchdog"})
    assert replay.session.record.standing_activity == []


async def test_a_stop_straggler_never_reopens_but_live_activity_heals_a_zeroed_count() -> None:
    # Hooks are unordered and retried: the stopped agent's last PostToolUse can
    # land seconds after its SubagentStop, and re-opening on it would flap a
    # correctly cleared annotation for a full TTL (the hook-channel twin of the
    # trailing-transcript rule). But a *live* agent keeps streaming tool hooks,
    # so activity past the grace window re-creates the annotation a lone
    # under-counted SubagentStop had zeroed.
    replay = DetectionReplay("claude")
    replay.session.record.native_session_id = OWN_CONVERSATION
    await replay.step(
        {
            "kind": "hook",
            "event": "SubagentStart",
            "payload": {"session_id": OWN_CONVERSATION, "agent_id": "agent-1"},
        }
    )
    await replay.step(
        {
            "kind": "hook",
            "event": "SubagentStop",
            "payload": {"session_id": OWN_CONVERSATION, "agent_id": "agent-1"},
        }
    )
    assert replay.session.record.standing_activity == []
    # The straggler: tool hook 2 s after the stop stays refresh-only.
    await replay.step({"kind": "timer", "seconds": 2})
    await replay.step(_subagent_tool_hook("PostToolUse"))
    assert replay.session.record.standing_activity == []
    # A second agent is genuinely still running: its stream continues past the
    # grace window and heals the annotation at count 1.
    await replay.step({"kind": "timer", "seconds": 11})
    await replay.step(_subagent_tool_hook())
    (activity,) = replay.session.record.standing_activity
    assert (activity.kind, activity.count, activity.source) == ("subagents", 1, "hook")
    assert activity.evidence == "hook:subagent:PreToolUse"


async def test_background_close_without_tracked_open_decrements_the_annotation() -> None:
    # A daemon restart loses the open-launch map while the adopted snapshot
    # still carries the annotation; the completion notification must still
    # count it down rather than leaving it to TTL decay.
    replay = DetectionReplay("claude")
    session = replay.session
    now = replay.clock.wall()
    set_standing_activity(
        session,
        "background_tasks",
        source="transcript",
        evidence="transcript:Bash:run_in_background",
        expires_at=now + 1800,
        count=2,
        now=now,
    )
    await replay.step(
        {
            "kind": "transcript",
            "ts_offset": 0,
            "record": {
                "type": "user",
                "isSidechain": False,
                "message": {
                    "content": (
                        "<task-notification>\n<task-id>blost1</task-id>\n"
                        "<tool-use-id>toolu_lost1</tool-use-id>\n<status>completed</status>\n"
                        "</task-notification>"
                    )
                },
            },
        }
    )
    (activity,) = session.record.standing_activity
    assert activity.count == 1
    await replay.step(
        {
            "kind": "transcript",
            "ts_offset": 1,
            "record": {
                "type": "user",
                "isSidechain": False,
                "message": {
                    "content": (
                        "<task-notification>\n<task-id>blost2</task-id>\n"
                        "<tool-use-id>toolu_lost2</tool-use-id>\n<status>completed</status>\n"
                        "</task-notification>"
                    )
                },
            },
        }
    )
    assert session.record.standing_activity == []


async def test_codex_subagent_activity_arms_and_ttl_decays() -> None:
    # Without a lifecycle hook, recency is the fallback truth: any activity
    # record opens/refreshes the annotation at count 1 and the TTL clears it.
    replay = DetectionReplay("codex")
    await replay.step(
        {
            "kind": "transcript",
            "ts_offset": 0,
            "record": {"type": "event_msg", "payload": {"type": "task_started"}},
        }
    )
    await replay.step(
        {
            "kind": "transcript",
            "ts_offset": 1,
            "record": {
                "type": "event_msg",
                "payload": {"type": "sub_agent_activity", "kind": "started", "agent_path": ["a"]},
            },
        }
    )
    (activity,) = replay.session.record.standing_activity
    assert (activity.kind, activity.count, activity.evidence) == (
        "subagents",
        1,
        "transcript:sub_agent_activity",
    )
    await replay.step({"kind": "timer", "seconds": 300.0})
    await replay.step({"kind": "watchdog"})
    assert replay.session.record.standing_activity == []
    assert replay.session.status_health_counters["standing_activity_expired"] == 1


async def test_codex_trailing_subagent_record_does_not_reopen_after_hook_stop() -> None:
    replay = DetectionReplay("codex")
    await replay.step(
        {
            "kind": "hook",
            "event": "SubagentStart",
            "payload": {"session_id": "native-replay", "agent_id": "agent-1"},
        }
    )
    await replay.step(
        {
            "kind": "hook",
            "event": "SubagentStop",
            "payload": {"session_id": "native-replay", "agent_id": "agent-1"},
        }
    )
    assert replay.session.record.standing_activity == []

    await replay.step(
        {
            "kind": "transcript",
            "ts_offset": 1,
            "record": {
                "type": "event_msg",
                "payload": {"type": "sub_agent_activity", "kind": "stopped"},
            },
        }
    )
    assert replay.session.record.standing_activity == []


async def test_process_tree_fast_clears_background_tasks() -> None:
    # A vanished process cannot still be working: the inspector clears the
    # annotation the moment the CLI has no live descendants — and never while
    # anything still runs under it, which also spares MCP-server children.
    from types import SimpleNamespace

    from swe_mux.processes import ProcessInspector

    session = ReplaySession("claude")
    now = session.clock.wall()
    set_standing_activity(
        session,
        "background_tasks",
        source="transcript",
        evidence="transcript:Bash:run_in_background",
        expires_at=now + 1800,
        count=1,
        since=now - 60,
        now=now,
    )
    inspector = ProcessInspector.__new__(ProcessInspector)
    inspector.sessions = SimpleNamespace(sessions={"replay-session": session})
    root = SimpleNamespace(session_id="replay-session")
    child = SimpleNamespace(session_id="replay-session")

    # Root plus a live child: something still runs, nothing is cleared.
    inspector._fast_clear_background_annotations([root, child], now)
    assert [a.kind for a in session.record.standing_activity] == ["background_tasks"]
    # A fresh annotation is not refuted by a pass that may have raced the spawn.
    young = ReplaySession("claude")
    set_standing_activity(
        young, "background_tasks", source="transcript", evidence="e", now=now
    )
    inspector.sessions = SimpleNamespace(sessions={"replay-session": young})
    inspector._fast_clear_background_annotations([root], now)
    assert [a.kind for a in young.record.standing_activity] == ["background_tasks"]
    # Root only, annotation past the grace window: fast-clear.
    inspector.sessions = SimpleNamespace(sessions={"replay-session": session})
    inspector._fast_clear_background_annotations([root], now)
    assert session.record.standing_activity == []
    assert (
        session.state_transitions[-1]["evidence"] == "process:descendants_zero"
    )


# ------------------------------------------------------------- replay harness


async def test_watchdog_step_expires_annotations_in_replay() -> None:
    # The harness mirrors _watchdog_check_session: the TTL sweep runs before
    # any other watchdog rule, so a fixture can pin annotation decay.
    replay = DetectionReplay("claude")
    now = replay.clock.wall()
    set_standing_activity(
        replay.session, "loop", source="transcript", evidence="e", expires_at=now + 60, now=now
    )
    await replay.step({"kind": "timer", "seconds": 30.0})
    await replay.step({"kind": "watchdog"})
    assert [a.kind for a in replay.session.record.standing_activity] == ["loop"]
    await replay.step({"kind": "timer", "seconds": 31.0})
    await replay.step(
        {"kind": "watchdog", "expect_standing": []}
    )
    assert replay.session.record.standing_activity == []
    assert replay.standing_checkpoints[-1]["actual"] == []
    assert replay.session.status_health_counters["standing_activity_expired"] == 1
