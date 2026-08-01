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
