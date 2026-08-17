"""Trigger arithmetic, validation, and the fire guards for scheduled runs."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from swe_mux import automation_registry as registry
from swe_mux.schedule_store import ScheduleConflict, ScheduleStore
from swe_mux.scheduler import MISSED_GRACE_SECONDS, ScheduleService, spec_from_row
from swe_mux.schedules import (
    ScheduleError,
    ScheduleSpec,
    first_occurrence,
    next_occurrence,
    occurrence_key,
    parse_cron,
    parse_spec,
    resolve_wall,
)

NY = "America/New_York"


def at(zone: str, text: str) -> float:
    """Epoch seconds for a wall-clock time in a named zone."""
    return datetime.fromisoformat(text).replace(tzinfo=ZoneInfo(zone)).timestamp()


def wall_text(zone: str, epoch: float) -> str:
    return datetime.fromtimestamp(epoch, ZoneInfo(zone)).strftime("%Y-%m-%d %H:%M")


# ---- cron parsing ------------------------------------------------------------


def test_cron_fields_parse_lists_ranges_steps_and_names() -> None:
    expression = parse_cron("0,30 9-17/4 * jan-mar mon")
    assert expression.minutes == (0, 30)
    assert expression.hours == (9, 13, 17)
    assert expression.months == (1, 2, 3)
    assert expression.days_of_week == (1,)


def test_cron_rejects_wrong_field_count_and_out_of_range() -> None:
    with pytest.raises(ScheduleError) as too_few:
        parse_cron("0 3 *")
    assert too_few.value.code == "invalid_cron"
    with pytest.raises(ScheduleError):
        parse_cron("0 24 * * *")
    with pytest.raises(ScheduleError):
        parse_cron("0 3 * * notaday")


def test_cron_sunday_is_zero_or_seven() -> None:
    assert parse_cron("0 3 * * 7").days_of_week == (0,)
    assert parse_cron("0 3 * * sun").days_of_week == (0,)


def test_cron_day_fields_union_when_both_restricted() -> None:
    """The Vixie rule: restricted dom *or* dow matches, not their intersection."""
    expression = parse_cron("0 9 1 * mon")
    wall = resolve_wall(NY)
    # 2026-06-01 is a Monday, 2026-06-08 a Monday, 2026-07-01 a Wednesday.
    first = next_occurrence(
        ScheduleSpec(label="l", prompt="p", cron=expression.source, timezone=NY),
        at(NY, "2026-05-31 12:00"),
    )
    assert first is not None
    assert wall_text(NY, first) == "2026-06-01 09:00"
    second = next_occurrence(
        ScheduleSpec(label="l", prompt="p", cron=expression.source, timezone=NY), first
    )
    assert second is not None
    assert wall_text(NY, second) == "2026-06-08 09:00"
    assert wall.wall(second).hour == 9


def test_unsatisfiable_cron_returns_no_occurrence() -> None:
    spec = ScheduleSpec(label="l", prompt="p", cron="30 2 30 2 *", timezone=NY)
    assert next_occurrence(spec, at(NY, "2026-01-01 00:00")) is None


# ---- daylight saving ---------------------------------------------------------


def test_daily_job_keeps_its_wall_clock_hour_across_a_dst_shift() -> None:
    """A 09:00 job is 09:00 local on both sides of the transition.

    This is the reason the module works in wall time: the two fires are 23 hours
    apart in real seconds, and any fixed-offset arithmetic would drift by an hour
    twice a year.
    """
    spec = ScheduleSpec(label="l", prompt="p", cron="0 9 * * *", timezone=NY)
    # US DST begins 2026-03-08.
    before = next_occurrence(spec, at(NY, "2026-03-07 12:00"))
    assert before is not None and wall_text(NY, before) == "2026-03-08 09:00"
    after = next_occurrence(spec, before)
    assert after is not None and wall_text(NY, after) == "2026-03-09 09:00"
    assert after - before == 24 * 3600


def test_spring_forward_gap_fires_once_rather_than_being_skipped() -> None:
    """02:30 does not exist on the transition day; the job still runs that day."""
    spec = ScheduleSpec(label="l", prompt="p", cron="30 2 * * *", timezone=NY)
    fire = next_occurrence(spec, at(NY, "2026-03-08 00:00"))
    assert fire is not None
    # It resolves to the equivalent instant just after the jump, i.e. 03:30 local.
    assert wall_text(NY, fire) == "2026-03-08 03:30"
    following = next_occurrence(spec, fire)
    assert following is not None and wall_text(NY, following) == "2026-03-09 02:30"


def test_fall_back_repeat_hour_fires_exactly_once() -> None:
    """01:30 happens twice on 2026-11-01; only the first instant is an occurrence."""
    spec = ScheduleSpec(label="l", prompt="p", cron="30 1 * * *", timezone=NY)
    fire = next_occurrence(spec, at(NY, "2026-10-31 12:00"))
    assert fire is not None
    following = next_occurrence(spec, fire)
    assert following is not None
    # The next fire is the following day, not the second pass through 01:30.
    assert wall_text(NY, following) == "2026-11-02 01:30"
    assert following - fire > 24 * 3600


# ---- spec validation ---------------------------------------------------------


def base_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "label": "Nightly health check",
        "prompt": "Check the app's health and report.",
        "trigger_kind": "cron",
        "cron": "0 3 * * *",
    }
    body.update(overrides)
    return body


def test_parse_spec_accepts_a_complete_definition() -> None:
    spec = parse_spec(
        base_body(
            timezone=NY,
            catch_up=True,
            overlap="allow",
            daily_run_cap=3,
            follow_ups=[{"body": "Summarize", "delay_seconds": 60}, "And stop"],
        )
    )
    assert spec.cron == "0 3 * * *"
    assert spec.catch_up is True
    assert [item.body for item in spec.follow_ups] == ["Summarize", "And stop"]
    assert spec.follow_ups[0].delay_seconds == 60


def test_parse_spec_rejects_a_shell_backend() -> None:
    """A scheduled run starts an agent; a shell has no prompt to seed."""
    with pytest.raises(ScheduleError) as exc:
        parse_spec(base_body(backend="shell"))
    assert exc.value.code == "invalid_backend"


def test_parse_spec_bounds_the_interval_and_the_horizon() -> None:
    with pytest.raises(ScheduleError) as fast:
        parse_spec(base_body(trigger_kind="interval", interval_seconds=30))
    assert fast.value.code == "invalid_interval"
    with pytest.raises(ScheduleError) as distant:
        parse_spec(base_body(trigger_kind="once", run_at=4_102_444_800.0), now=0.0)
    assert distant.value.code == "invalid_run_at"


def test_parse_spec_rejects_an_unknown_timezone_and_too_many_follow_ups() -> None:
    with pytest.raises(ScheduleError) as zone:
        parse_spec(base_body(timezone="Mars/Olympus"))
    assert zone.value.code == "invalid_timezone"
    with pytest.raises(ScheduleError) as many:
        parse_spec(base_body(follow_ups=[f"m{index}" for index in range(21)]))
    assert many.value.code == "invalid_follow_ups"


def test_interval_waits_a_full_interval_rather_than_firing_on_save() -> None:
    spec = parse_spec(base_body(trigger_kind="interval", interval_seconds=3600))
    assert first_occurrence(spec, now=1000.0) == 1000.0 + 3600


def test_once_in_the_past_has_no_occurrence() -> None:
    spec = ScheduleSpec(label="l", prompt="p", trigger_kind="once", run_at=100.0)
    assert first_occurrence(spec, now=200.0) is None


def test_occurrence_key_is_stable_within_a_minute() -> None:
    assert occurrence_key(1_800_000_030.0) == occurrence_key(1_800_000_059.0)
    assert occurrence_key(1_800_000_030.0) != occurrence_key(1_800_000_090.0)


# ---- registry ----------------------------------------------------------------


def test_scheduled_runs_is_a_dependency_free_consumer() -> None:
    automation = registry.REGISTRY["scheduled_runs"]
    assert automation.kind == registry.CONSUMER
    assert automation.requires == ()
    assert automation.implemented
    assert registry.resolve({"scheduled_runs"}).is_enabled("scheduled_runs")


# ---- store -------------------------------------------------------------------


@pytest.fixture
def store(tmp_path: Path) -> ScheduleStore:
    created = ScheduleStore(tmp_path / "mux.db")
    yield created
    created.close()


async def test_store_round_trips_a_definition(store: ScheduleStore) -> None:
    spec = parse_spec(base_body(follow_ups=["then this"]))
    row = await store.create(
        project_id="p1", project_root="/repo", spec=spec, next_fire_at=100.0, now=10.0
    )
    assert row["label"] == "Nightly health check"
    assert row["follow_ups"] == [{"body": "then this", "delay_seconds": 0.0, "armed": False}]
    assert await store.list_schedules("p1") == [row]
    assert await store.list_schedules("other") == []
    assert spec_from_row(row).cron == "0 3 * * *"


async def test_store_replace_honours_the_revision(store: ScheduleStore) -> None:
    row = await store.create(
        project_id="p1", project_root="/repo", spec=parse_spec(base_body()), next_fire_at=1.0
    )
    stale = await store.replace(
        str(row["id"]),
        spec=parse_spec(base_body(label="Renamed")),
        next_fire_at=2.0,
        revision=int(row["revision"]) + 5,
    )
    assert stale is None
    fresh = await store.replace(
        str(row["id"]),
        spec=parse_spec(base_body(label="Renamed")),
        next_fire_at=2.0,
        revision=int(row["revision"]),
    )
    assert fresh is not None and fresh["label"] == "Renamed"


async def test_store_due_only_returns_armed_and_ready(store: ScheduleStore) -> None:
    ready = await store.create(
        project_id="p1", project_root="/repo", spec=parse_spec(base_body()), next_fire_at=50.0
    )
    await store.create(
        project_id="p1", project_root="/repo", spec=parse_spec(base_body()), next_fire_at=500.0
    )
    paused = await store.create(
        project_id="p1", project_root="/repo", spec=parse_spec(base_body()), next_fire_at=10.0
    )
    await store.set_enabled(str(paused["id"]), False)
    due = await store.due(100.0)
    assert [item["id"] for item in due] == [str(ready["id"])]


async def test_one_occurrence_can_only_be_claimed_once(store: ScheduleStore) -> None:
    """The restart-safety guarantee: the claim, not an in-memory flag, is the lock."""
    row = await store.create(
        project_id="p1", project_root="/repo", spec=parse_spec(base_body()), next_fire_at=50.0
    )
    await store.claim_run(
        schedule_id=str(row["id"]), project_id="p1", fire_key="600", due_at=600.0
    )
    with pytest.raises(ScheduleConflict):
        await store.claim_run(
            schedule_id=str(row["id"]), project_id="p1", fire_key="600", due_at=600.0
        )


# ---- the service -------------------------------------------------------------


@dataclass
class FakeRecord:
    id: str
    state: str = "running"


@dataclass
class FakeSession:
    record: FakeRecord


@dataclass
class FakeProject:
    id: str
    root: str
    name: str = "Repo"


@dataclass
class FakeProjects:
    projects: dict[str, FakeProject]


@dataclass
class FakeSessions:
    sessions: dict[str, FakeSession] = field(default_factory=dict)


@dataclass
class FakeEvents:
    emitted: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    async def emit(self, event_type: str, **payload: Any) -> None:
        self.emitted.append((event_type, payload))


@dataclass
class FakeConfig:
    scheduled_runs_enabled: bool = True
    scheduled_runs_max_concurrent: int = 3
    scheduled_runs_poll_seconds: float = 5.0


class Harness:
    """A `ScheduleService` wired to fakes, with a controllable clock."""

    def __init__(self, store: ScheduleStore, *, enabled: frozenset[str] | None = None) -> None:
        self.store = store
        self.now = 1_000_000.0
        self.projects = FakeProjects({"p1": FakeProject("p1", "/repo")})
        self.sessions = FakeSessions()
        self.events = FakeEvents()
        self.config = FakeConfig()
        self.enabled = enabled if enabled is not None else frozenset({"scheduled_runs"})
        self.spawned: list[dict[str, Any]] = []
        self.queued: list[dict[str, Any]] = []
        self.spawn_error: Exception | None = None
        self.notifications: list[dict[str, Any]] = []
        self.service = ScheduleService(
            store=store,
            projects=self.projects,
            sessions=self.sessions,
            config=self.config,
            events=self.events,
            automation_gate=self._gate,
            spawn_op=self._spawn,
            enqueue=self._enqueue,
            notify=self._notify,
            clock=lambda: self.now,
        )

    async def _gate(self, root: str) -> frozenset[str]:
        return self.enabled

    async def _spawn(self, body: dict[str, Any]) -> FakeSession:
        if self.spawn_error is not None:
            raise self.spawn_error
        self.spawned.append(body)
        session = FakeSession(FakeRecord(f"s{len(self.spawned)}"))
        self.sessions.sessions[session.record.id] = session
        return session

    async def _enqueue(self, **kwargs: Any) -> dict[str, Any]:
        self.queued.append(kwargs)
        return {"id": f"m{len(self.queued)}"}

    async def _notify(self, **kwargs: Any) -> dict[str, Any]:
        self.notifications.append(kwargs)
        return kwargs

    async def schedule(self, **overrides: Any) -> dict[str, Any]:
        spec = parse_spec(base_body(**overrides))
        return await self.store.create(
            project_id="p1",
            project_root="/repo",
            spec=spec,
            next_fire_at=self.now,
            now=self.now,
        )


async def test_a_due_schedule_spawns_and_queues_its_follow_ups(store: ScheduleStore) -> None:
    harness = Harness(store)
    row = await harness.schedule(
        backend="claude",
        session_name="health",
        follow_ups=[{"body": "Now summarize", "delay_seconds": 120}],
    )
    settled = await harness.service.tick(now=harness.now)
    assert [item["outcome"] for item in settled] == ["spawned"]
    assert harness.spawned == [
        {
            "project_id": "p1",
            "seed_text": "Check the app's health and report.",
            "backend": "claude",
            "name": "health",
        }
    ]
    assert harness.queued[0]["target_session_id"] == "s1"
    assert harness.queued[0]["sender_kind"] == "rule"
    assert harness.queued[0]["constraints"] == {"not_before": harness.now + 120}
    stored = await store.get(str(row["id"]))
    assert stored is not None
    assert stored["last_outcome"] == "spawned"
    assert stored["last_session_id"] == "s1"
    # The window moved on rather than staying due.
    assert float(stored["next_fire_at"]) > harness.now


async def test_a_sweep_never_fires_one_occurrence_twice(store: ScheduleStore) -> None:
    harness = Harness(store)
    await harness.schedule()
    await harness.service.tick(now=harness.now)
    # Re-arm the same occurrence the way a crashed daemon's stale row would.
    schedules = await store.list_schedules("p1")
    await store.set_next_fire(str(schedules[0]["id"]), harness.now)
    await harness.service.tick(now=harness.now)
    assert len(harness.spawned) == 1


async def test_project_opt_out_blocks_the_fire(store: ScheduleStore) -> None:
    harness = Harness(store, enabled=frozenset())
    await harness.schedule()
    settled = await harness.service.tick(now=harness.now)
    assert [item["outcome"] for item in settled] == ["skipped"]
    assert "opted into" in settled[0]["reason"]
    assert not harness.spawned


async def test_install_switch_blocks_every_fire(store: ScheduleStore) -> None:
    harness = Harness(store)
    harness.config.scheduled_runs_enabled = False
    await harness.schedule()
    settled = await harness.service.tick(now=harness.now)
    assert settled[0]["outcome"] == "skipped"
    assert not harness.spawned


async def test_overlap_skip_refuses_while_the_previous_session_lives(
    store: ScheduleStore,
) -> None:
    harness = Harness(store)
    row = await harness.schedule()
    await harness.service.tick(now=harness.now)
    await store.set_next_fire(str(row["id"]), harness.now + 60)
    harness.now += 60
    settled = await harness.service.tick(now=harness.now)
    assert settled[0]["outcome"] == "skipped"
    assert "still live" in settled[0]["reason"]
    # Once that session ends, the next window starts a new one.
    harness.sessions.sessions["s1"].record.state = "exited"
    await store.set_next_fire(str(row["id"]), harness.now + 60)
    harness.now += 60
    settled = await harness.service.tick(now=harness.now)
    assert settled[0]["outcome"] == "spawned"


async def test_overlap_allow_starts_a_second_session(store: ScheduleStore) -> None:
    harness = Harness(store)
    row = await harness.schedule(overlap="allow")
    await harness.service.tick(now=harness.now)
    await store.set_next_fire(str(row["id"]), harness.now + 60)
    harness.now += 60
    settled = await harness.service.tick(now=harness.now)
    assert settled[0]["outcome"] == "spawned"
    assert len(harness.spawned) == 2


async def test_concurrency_ceiling_bounds_unattended_sessions(store: ScheduleStore) -> None:
    harness = Harness(store)
    harness.config.scheduled_runs_max_concurrent = 1
    await harness.schedule(label="one")
    await harness.service.tick(now=harness.now)
    second = await harness.schedule(label="two", overlap="allow")
    await store.set_next_fire(str(second["id"]), harness.now)
    settled = await harness.service.tick(now=harness.now)
    assert [item["outcome"] for item in settled] == ["skipped"]
    assert "already running" in settled[0]["reason"]


async def test_a_missed_window_is_recorded_rather_than_replayed(store: ScheduleStore) -> None:
    harness = Harness(store)
    row = await harness.schedule()
    await store.set_next_fire(str(row["id"]), harness.now - MISSED_GRACE_SECONDS - 60)
    settled = await harness.service.tick(now=harness.now)
    assert settled[0]["outcome"] == "missed"
    assert not harness.spawned


async def test_catch_up_replays_a_missed_window_once(store: ScheduleStore) -> None:
    harness = Harness(store)
    row = await harness.schedule(catch_up=True)
    await store.set_next_fire(str(row["id"]), harness.now - MISSED_GRACE_SECONDS - 60)
    settled = await harness.service.tick(now=harness.now)
    assert settled[0]["outcome"] == "spawned"
    assert len(harness.spawned) == 1
    # The replay does not queue the whole backlog: the window advanced past now.
    stored = await store.get(str(row["id"]))
    assert stored is not None and float(stored["next_fire_at"]) > harness.now


async def test_a_failed_spawn_is_recorded_and_alerts(store: ScheduleStore) -> None:
    harness = Harness(store)
    harness.spawn_error = RuntimeError("no such profile")
    await harness.schedule()
    settled = await harness.service.tick(now=harness.now)
    assert settled[0]["outcome"] == "failed"
    assert "no such profile" in settled[0]["reason"]
    assert harness.notifications[0]["kind"] == "schedule-failed"
    assert harness.notifications[0]["severity"] == "warn"


async def test_a_once_schedule_disables_itself_after_firing(store: ScheduleStore) -> None:
    harness = Harness(store)
    spec = parse_spec(
        base_body(trigger_kind="once", run_at=harness.now + 10, cron=""), now=harness.now
    )
    row = await store.create(
        project_id="p1",
        project_root="/repo",
        spec=spec,
        next_fire_at=harness.now + 10,
        now=harness.now,
    )
    harness.now += 10
    settled = await harness.service.tick(now=harness.now)
    assert settled[0]["outcome"] == "spawned"
    stored = await store.get(str(row["id"]))
    assert stored is not None
    assert stored["enabled"] is False
    assert stored["next_fire_at"] is None


async def test_run_now_fires_without_moving_the_window(store: ScheduleStore) -> None:
    harness = Harness(store)
    row = await harness.schedule()
    await store.set_next_fire(str(row["id"]), harness.now + 3600)
    run = await harness.service.run_now(str(row["id"]))
    assert run is not None and run["outcome"] == "spawned"
    assert run["origin"] == "manual"
    stored = await store.get(str(row["id"]))
    assert stored is not None and float(stored["next_fire_at"]) == harness.now + 3600


async def test_run_now_still_honours_the_project_opt_in(store: ScheduleStore) -> None:
    harness = Harness(store, enabled=frozenset())
    row = await harness.schedule()
    run = await harness.service.run_now(str(row["id"]))
    assert run is not None and run["outcome"] == "skipped"
    assert not harness.spawned


async def test_restore_leaves_an_interval_alone_and_repairs_a_missing_fire(
    store: ScheduleStore,
) -> None:
    harness = Harness(store)
    interval = await store.create(
        project_id="p1",
        project_root="/repo",
        spec=parse_spec(base_body(trigger_kind="interval", interval_seconds=21_600)),
        next_fire_at=harness.now + 20_000,
        now=harness.now,
    )
    unarmed = await store.create(
        project_id="p1",
        project_root="/repo",
        spec=parse_spec(base_body()),
        next_fire_at=None,
        now=harness.now,
    )
    await harness.service.restore()
    kept = await store.get(str(interval["id"]))
    assert kept is not None and float(kept["next_fire_at"]) == harness.now + 20_000
    repaired = await store.get(str(unarmed["id"]))
    assert repaired is not None and repaired["next_fire_at"] is not None
