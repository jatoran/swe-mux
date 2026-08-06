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


def _bash_launch(tool_use_id: str, *, background: bool = True, **extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {"command": "uv run muxd --config harness.toml"}
    if background:
        payload["run_in_background"] = True
    payload.update(extra)
    return {
        "type": "assistant",
        "isSidechain": False,
        "message": {
            "content": [
                {"type": "tool_use", "id": tool_use_id, "name": "Bash", "input": payload}
            ]
        },
    }


def _bash_result(tool_use_id: str, text: str) -> dict[str, Any]:
    return {
        "type": "user",
        "isSidechain": False,
        "message": {
            "content": [
                {"type": "tool_result", "tool_use_id": tool_use_id, "content": text}
            ]
        },
    }


def _notification_body(task_id: str, tool_use_id: str, status: str = "completed") -> str:
    return (
        f"<task-notification>\n<task-id>{task_id}</task-id>\n"
        f"<tool-use-id>{tool_use_id}</tool-use-id>\n<status>{status}</status>\n"
        "</task-notification>"
    )


async def test_a_queued_completion_closes_a_background_launch() -> None:
    # A background shell that finishes while its session is between turns has no
    # turn to be announced into, so the CLI queues the notification instead of
    # writing a user record. Reading only the user form is what left a finished
    # shell holding the annotation for its full 30-minute TTL — measured live
    # 2026-08-06, with the proof of completion sitting in the transcript the
    # whole time in a record type the extractors never looked at.
    replay = DetectionReplay("claude")
    session = replay.session
    await replay.step({"kind": "transcript", "ts_offset": 0, "record": _bash_launch("toolu_a")})
    await replay.step(
        {
            "kind": "transcript",
            "ts_offset": 1,
            "record": _bash_result(
                "toolu_a", "Command running in background with ID: bqueued1."
            ),
        }
    )
    (activity,) = session.record.standing_activity
    assert (activity.kind, activity.count) == ("background_tasks", 1)

    await replay.step(
        {
            "kind": "transcript",
            "ts_offset": 2,
            "record": {
                "type": "queue-operation",
                "operation": "enqueue",
                "content": _notification_body("bqueued1", "toolu_a"),
            },
        }
    )
    assert session.record.standing_activity == []
    assert ledger_entries(session)[-1]["evidence"] == "transcript:task_notification"


async def test_every_completion_carrier_closes_and_only_the_first_counts() -> None:
    # One completion is announced up to three times: the `queue-operation`
    # enqueue when the task finishes, its `attachment` mirror, and the `remove`
    # when it reaches the model. Whichever arrives first closes; the rest must be
    # no-ops, or the untracked-open path would decrement a count that other,
    # genuinely-running tasks own.
    replay = DetectionReplay("claude")
    session = replay.session
    now = replay.clock.wall()
    set_standing_activity(
        session,
        "background_tasks",
        source="transcript",
        evidence="transcript:Bash:run_in_background",
        expires_at=now + 1800,
        count=3,
        now=now,
    )
    body = _notification_body("bdup", "toolu_dup")
    carriers: list[dict[str, Any]] = [
        {"type": "queue-operation", "operation": "enqueue", "content": body},
        {"type": "attachment", "attachment": {"type": "queued_command", "prompt": body}},
        {"type": "queue-operation", "operation": "remove", "content": body},
        {"type": "user", "isSidechain": False, "message": {"content": body}},
    ]
    for offset, record in enumerate(carriers):
        await replay.step({"kind": "transcript", "ts_offset": offset, "record": record})
    (activity,) = session.record.standing_activity
    assert activity.count == 2


async def test_a_timeout_promoted_shell_is_tracked_from_its_result() -> None:
    # A foreground Bash that outruns its timeout is moved to the background by
    # the CLI. Its input carries no `run_in_background` at all, so nothing would
    # ever open it — while its later completion, naming the same tool_use id,
    # would still decrement a count it never contributed to.
    replay = DetectionReplay("claude")
    session = replay.session
    await replay.step(
        {
            "kind": "transcript",
            "ts_offset": 0,
            "record": _bash_launch("toolu_slow", background=False),
        }
    )
    assert session.record.standing_activity == []
    await replay.step(
        {
            "kind": "transcript",
            "ts_offset": 1,
            "record": _bash_result(
                "toolu_slow",
                "Command did not complete within its 90s timeout and was moved to the "
                "background (ID: bpromoted).",
            ),
        }
    )
    (activity,) = session.record.standing_activity
    assert (activity.kind, activity.count) == ("background_tasks", 1)
    assert activity.evidence == "transcript:Bash:background_result"
    # And its completion closes it exactly once, like any other launch.
    await replay.step(
        {
            "kind": "transcript",
            "ts_offset": 2,
            "record": {
                "type": "queue-operation",
                "operation": "enqueue",
                "content": _notification_body("bpromoted", "toolu_slow"),
            },
        }
    )
    assert session.record.standing_activity == []


async def test_the_annotation_names_what_it_thinks_is_running() -> None:
    # A count alone is unfalsifiable from the outside: "1 background task" on a
    # session with nothing running looks exactly like a correct reading, so the
    # failure these sources are prone to is the one the UI cannot show. The
    # launch's own description is what makes the claim checkable.
    replay = DetectionReplay("claude")
    session = replay.session
    await replay.step(
        {
            "kind": "transcript",
            "ts_offset": 0,
            "record": _bash_launch("toolu_1", description="Restart the harness daemon"),
        }
    )
    (activity,) = session.record.standing_activity
    assert activity.detail == "Restart the harness daemon"
    # A second launch says so rather than silently hiding behind the first.
    await replay.step(
        {
            "kind": "transcript",
            "ts_offset": 1,
            "record": _bash_launch("toolu_2", description="Tail the build"),
        }
    )
    (activity,) = session.record.standing_activity
    assert (activity.count, activity.detail) == (2, "Tail the build (+1 more)")
    # Closing the newest falls back to the one still open, never a stale name.
    await replay.step(
        {
            "kind": "transcript",
            "ts_offset": 2,
            "record": {
                "type": "queue-operation",
                "operation": "enqueue",
                "content": _notification_body("b2", "toolu_2"),
            },
        }
    )
    (activity,) = session.record.standing_activity
    assert (activity.count, activity.detail) == (1, "Restart the harness daemon")


async def test_the_pty_footer_cannot_reopen_a_closed_background_annotation() -> None:
    # The screen is a 32 KiB append-only window of redraw traffic, so the footer
    # drawn while a task genuinely ran is still matchable minutes after it
    # finished. Measured live 2026-08-06: the transcript positively closed the
    # annotation and this reading re-added it 29 s later with a fresh 30-minute
    # TTL, after which nothing but that TTL could clear it again. Corroboration
    # was always this tier's stated role; creating when absent is what quietly
    # made it a source.
    replay = DetectionReplay("claude")
    session = replay.session
    await replay.step({"kind": "transcript", "ts_offset": 0, "record": _bash_launch("toolu_p")})
    await replay.step(
        {
            "kind": "transcript",
            "ts_offset": 1,
            "record": _bash_result("toolu_p", "Command running in background with ID: bpty."),
        }
    )
    await replay.step(
        {
            "kind": "transcript",
            "ts_offset": 2,
            "record": {
                "type": "queue-operation",
                "operation": "enqueue",
                "content": _notification_body("bpty", "toolu_p"),
            },
        }
    )
    assert session.record.standing_activity == []

    # The footer is still in the retained screen when the next turn ends.
    await replay.step(
        {"kind": "pty_tail", "data": "✻ churned for 4s · 1 shell still running\n"}
    )
    await replay.step(
        {
            "kind": "transcript",
            "ts_offset": 3,
            "record": {
                "type": "assistant",
                "isSidechain": False,
                "message": {"content": [{"type": "text", "text": "done"}]},
            },
        }
    )
    await replay.step(
        {
            "kind": "transcript",
            "ts_offset": 4,
            "record": {"type": "system", "subtype": "turn_duration", "durationMs": 1200},
        }
    )
    assert session.record.standing_activity == []
    assert session.record.state == "idle"


async def test_the_pty_footer_still_refreshes_an_open_background_annotation() -> None:
    # Demoting the tier to refresh-only must not make it inert: the CLI's own
    # footer is what keeps a genuinely long-running task's annotation alive when
    # the transcript has nothing further to say about it.
    replay = DetectionReplay("claude")
    session = replay.session
    await replay.step({"kind": "transcript", "ts_offset": 0, "record": _bash_launch("toolu_r")})
    (opened,) = session.record.standing_activity
    first_expiry = opened.expires_at
    await replay.step(
        {"kind": "pty_tail", "data": "✻ churned for 4s · 1 shell still running\n"}
    )
    await replay.step({"kind": "timer", "seconds": 120.0})
    await replay.step(
        {
            "kind": "transcript",
            "ts_offset": 130,
            "record": {"type": "system", "subtype": "turn_duration", "durationMs": 1200},
        }
    )
    (activity,) = session.record.standing_activity
    assert activity.source == "pty"
    assert first_expiry is not None and activity.expires_at is not None
    assert activity.expires_at > first_expiry
    # Refresh, not replacement: the count and the name the transcript owns stand.
    assert activity.count == 1
    assert session.record.idle_reason == "waiting_on_background"


def _clear_request(session: Any, body: Any) -> Any:
    from types import SimpleNamespace

    class SessionsStub:
        def resolve(self, identity: str) -> Any:
            return session

    async def json_body() -> Any:
        if isinstance(body, Exception):
            raise body
        return body

    return SimpleNamespace(
        app={"sessions": SessionsStub()},
        match_info={"sid": "replay-session"},
        json=json_body,
    )


async def test_a_manual_clear_retracts_annotations_and_nothing_else() -> None:
    # Every annotation source is evidence about work the daemon cannot observe
    # directly, so any of them can be left holding a claim the user can see is
    # false. Without this the only exit is a 30-minute TTL.
    import json as _json

    from swe_mux.server import clear_session_standing_activity

    session = ReplaySession("claude")
    session.record.state = "idle"
    session.record.idle_reason = "waiting_on_background"
    now = session.clock.wall()
    session.observation_state = {
        "background_open": {"toolu_x": "bx"},
        "background_labels": {"toolu_x": "Restart the harness daemon"},
    }
    session.publish_update = lambda: None  # type: ignore[method-assign]
    set_standing_activity(
        session, "background_tasks", source="pty", evidence="e", count=1, now=now
    )
    set_standing_activity(session, "loop", source="transcript", evidence="e", now=now)

    response = await clear_session_standing_activity(
        _clear_request(session, {"kind": "background_tasks"})
    )
    payload = _json.loads(response.text)
    assert payload["cleared"] is True
    assert [a.kind for a in session.record.standing_activity] == ["loop"]
    # The run-scoped launch bookkeeping goes with it, so a later duplicate
    # completion cannot decrement an annotation that has nothing to do with it.
    assert session.observation_state["background_open"] == {}
    assert session.observation_state["background_labels"] == {}
    # Annotations are not states: this may retract, never assert or transition.
    assert session.record.state == "idle"
    assert session.record.idle_reason == "waiting_on_background"
    assert ledger_entries(session)[-1]["evidence"] == "manual"

    # No body clears the whole set; a second call is an honest no-op.
    response = await clear_session_standing_activity(_clear_request(session, ValueError()))
    assert _json.loads(response.text)["cleared"] is True
    assert session.record.standing_activity == []
    response = await clear_session_standing_activity(_clear_request(session, {}))
    assert _json.loads(response.text)["cleared"] is False


def test_the_manual_clear_is_reachable_from_the_ui() -> None:
    # An escape hatch only the API knows about is not an escape hatch: the whole
    # point is that the user, who can see the annotation is wrong, can retract it.
    root = Path(__file__).parents[1]
    app = (root / "frontend" / "src" / "App.tsx").read_text(encoding="utf-8")
    assert "/standing-activity/clear" in app
    assert "session.clearStandingActivity" in app
    # Offered only where there is something to clear, and gated on the same
    # badge set the user is looking at.
    assert "activityBadges(contextMenu.session).length>0" in app


async def test_a_manual_clear_rejects_an_unknown_kind() -> None:
    import pytest

    from swe_mux.server import clear_session_standing_activity

    session = ReplaySession("claude")
    with pytest.raises(ValueError, match="unknown standing-activity kind"):
        await clear_session_standing_activity(_clear_request(session, {"kind": "nonsense"}))


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
    # annotation once no live descendant *could be that task* — and never while
    # one still could, which is what makes a false clear structurally impossible.
    #
    # The candidate test is deliberately not a descendant count. It used to be
    # ("exactly one descendant, the CLI root"), and that gate is unreachable on a
    # real session: a Claude CLI that has opened a file holds a language server
    # and one with a stdio MCP server holds that too, so the count is never 1 and
    # the clear had never fired on the live fleet. Age against the annotation
    # separates them without matching on names that drift every CLI release.
    from types import SimpleNamespace

    from swe_mux.processes import ProcessInspector

    session = ReplaySession("claude")
    now = session.clock.wall()
    session.record.pid = 100
    opened = now - 60
    set_standing_activity(
        session,
        "background_tasks",
        source="transcript",
        evidence="transcript:Bash:run_in_background",
        expires_at=now + 1800,
        count=1,
        since=opened,
        now=now,
    )
    inspector = ProcessInspector.__new__(ProcessInspector)
    inspector.sessions = SimpleNamespace(sessions={"replay-session": session})
    root = SimpleNamespace(session_id="replay-session", pid=100, started_at=now - 3600)
    # A helper the CLI started long before the annotation: cannot be its task.
    lsp = SimpleNamespace(session_id="replay-session", pid=101, started_at=now - 3000)
    # A process that appeared with the launch: could be.
    task = SimpleNamespace(session_id="replay-session", pid=102, started_at=opened + 1)
    # Ownership readable but start time not: uncertainty counts as task-capable.
    opaque = SimpleNamespace(session_id="replay-session", pid=103, started_at=None)

    # Something that could be the task still runs: nothing is cleared.
    inspector._fast_clear_background_annotations([root, lsp, task], now)
    assert [a.kind for a in session.record.standing_activity] == ["background_tasks"]
    inspector._fast_clear_background_annotations([root, opaque], now)
    assert [a.kind for a in session.record.standing_activity] == ["background_tasks"]
    # A fresh annotation is not refuted by a pass that may have raced the spawn.
    young = ReplaySession("claude")
    young.record.pid = 100
    set_standing_activity(
        young, "background_tasks", source="transcript", evidence="e", now=now
    )
    inspector.sessions = SimpleNamespace(sessions={"replay-session": young})
    inspector._fast_clear_background_annotations([root], now)
    assert [a.kind for a in young.record.standing_activity] == ["background_tasks"]
    # The root plus long-lived helpers only: the task is gone, so is the claim.
    inspector.sessions = SimpleNamespace(sessions={"replay-session": session})
    inspector._fast_clear_background_annotations([root, lsp], now)
    assert session.record.standing_activity == []
    assert session.state_transitions[-1]["evidence"] == "process:no_task_descendants"
    # No descendants at all means the CLI root itself is gone; session exit owns
    # that transition and clears the whole set with it.
    stranded = ReplaySession("claude")
    stranded.record.pid = 100
    set_standing_activity(
        stranded,
        "background_tasks",
        source="transcript",
        evidence="e",
        since=opened,
        now=now,
    )
    inspector.sessions = SimpleNamespace(sessions={"replay-session": stranded})
    inspector._fast_clear_background_annotations([], now)
    assert [a.kind for a in stranded.record.standing_activity] == ["background_tasks"]


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
