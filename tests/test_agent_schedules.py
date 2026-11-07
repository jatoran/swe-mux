"""Agent-authored schedules: what an agent may arm, what waits for a person, and how it fires.

Organised by the three places the rules are enforced:

- **The verdict** (`schedules.fires_per_day`, `schedules.agent_schedule_verdict`) - pure
  functions of the definition and the Project's `schedule_grant`.
- **Writing** (`schedule_authoring.ScheduleAuthoringService`) - the bounds persistence
  needs: lifetime, the per-Project cap, the hourly budget, duplicate suppression, and
  who may withdraw what.
- **Firing** (`scheduler.ScheduleService`) - the authority re-check, expiry, the
  attributed seed, and a resume of the author's own conversation delivered into the pane
  that already holds it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from swe_mux.agent_authority import AUTHORITY_FIELDS, resolve_authority
from swe_mux.config import Config
from swe_mux.project_files import (
    SCHEDULE_GRANTS,
    parse_project_config,
    serialize_project_config,
)
from swe_mux.prompt_queue import QueueError
from swe_mux.schedule_authoring import ScheduleAuthoringService
from swe_mux.schedule_store import ScheduleAuthor, ScheduleStore
from swe_mux.scheduler import ScheduleService, spec_from_row
from swe_mux.schedules import (
    FREQUENCY_COUNT_CEILING,
    ScheduleError,
    agent_schedule_verdict,
    fires_per_day,
    parse_spec,
)

from .support.claude_transcript import SOURCE_ID, write_source

# A fixed instant, so cron arithmetic and horizons are deterministic in the host's zone.
NOW = 1_800_000_000.0


def spawn_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "label": "Nightly check",
        "prompt": "Check the build.",
        "trigger_kind": "cron",
        "cron": "0 9 * * *",
    }
    body.update(overrides)
    return body


# ---- the verdict -------------------------------------------------------------


def test_frequency_is_measured_from_real_occurrences() -> None:
    once = parse_spec(spawn_body(trigger_kind="once", run_at=NOW + 600, cron=""), now=NOW)
    assert fires_per_day(once, now=NOW) == 1
    twice = parse_spec(spawn_body(cron="0 9,21 * * *"), now=NOW)
    assert fires_per_day(twice, now=NOW) == 2
    weekdays = parse_spec(spawn_body(cron="0 9 * * mon-fri"), now=NOW)
    assert fires_per_day(weekdays, now=NOW) == 1
    twelve_hours = parse_spec(
        spawn_body(trigger_kind="interval", interval_seconds=12 * 3600, cron=""), now=NOW
    )
    assert fires_per_day(twelve_hours, now=NOW) == 2
    eight_hours = parse_spec(
        spawn_body(trigger_kind="interval", interval_seconds=8 * 3600, cron=""), now=NOW
    )
    assert fires_per_day(eight_hours, now=NOW) == 3


def test_frequency_stops_counting_past_the_ceiling() -> None:
    # Walking every-minute cron across eight days to say 1440 would cost eleven thousand
    # evaluations to state the obvious; past the ceiling the answer is "more than that".
    busy = parse_spec(spawn_body(cron="* * * * *"), now=NOW)
    assert fires_per_day(busy, now=NOW) == FREQUENCY_COUNT_CEILING + 1
    every_five = parse_spec(
        spawn_body(trigger_kind="interval", interval_seconds=300, cron=""), now=NOW
    )
    assert fires_per_day(every_five, now=NOW) == FREQUENCY_COUNT_CEILING + 1


def test_the_verdict_at_each_level() -> None:
    once = parse_spec(spawn_body(trigger_kind="once", run_at=NOW + 600, cron=""), now=NOW)
    daily = parse_spec(spawn_body(), now=NOW)
    hourly = parse_spec(spawn_body(cron="0 * * * *"), now=NOW)

    def verdict(level: str, spec: Any, frequency: int, *, opted_in: bool = True) -> Any:
        return agent_schedule_verdict(
            level, spec, frequency=frequency, daily_limit=2, opted_in=opted_in
        )

    assert verdict("limited", once, 1).direct
    assert verdict("limited", daily, 1).direct
    held = verdict("limited", hourly, 24)
    assert not held.direct
    assert "24 times a day" in held.reason and "2 a day" in held.reason
    assert verdict("granted", hourly, 24).direct
    assert not verdict("draft", once, 1).direct
    # Not opted in collapses to draft whatever the level, the way an agent spawn does
    # when `session_control` is off: the request still has somewhere to go.
    assert not verdict("granted", once, 1, opted_in=False).direct
    # An unknown level fails closed.
    assert not verdict("bogus", once, 1).direct


def test_the_grant_field_is_three_levels_narrowest_first(tmp_path: Path) -> None:
    field_spec = AUTHORITY_FIELDS["schedule_grant"]
    assert field_spec.levels == ("draft", "limited", "granted") == SCHEDULE_GRANTS
    assert field_spec.builtin == "limited"
    assert field_spec.gated_by == "scheduled_runs"
    root = tmp_path / "repo"
    (root / ".swe-mux").mkdir(parents=True)
    (root / ".swe-mux" / "config.toml").write_text("version = 1\n", encoding="utf-8")
    config = Config(data_dir=tmp_path / "data")
    assert resolve_authority(config, root, "schedule_grant") == "limited"
    (root / ".swe-mux" / "config.toml").write_text("version = 1\nschedule_grant = [[[\n")
    # Corruption resolves to the narrow end, never to the permissive default.
    assert resolve_authority(config, root, "schedule_grant") == "draft"


def test_the_project_field_round_trips_and_refuses_an_unknown_level() -> None:
    parsed = parse_project_config(serialize_project_config({"schedule_grant": "granted"}))
    assert parsed["schedule_grant"] == "granted"
    with pytest.raises(ValueError):
        parse_project_config(b'version = 1\nschedule_grant = "sometimes"\n')


def test_the_new_definition_fields_are_validated() -> None:
    # A resume's model is its conversation's CLI's, so naming one is refused.
    with pytest.raises(ScheduleError) as failure:
        parse_spec(
            {
                "label": "x",
                "prompt": "y",
                "action": "resume",
                "target_run_id": "run_a",
                "trigger_kind": "once",
                "run_at": NOW + 600,
                "model": "opus",
            },
            now=NOW,
        )
    assert failure.value.code == "invalid_model"
    # `deliver` means nothing to a spawn, which opens a conversation nobody holds.
    with pytest.raises(ScheduleError) as failure:
        parse_spec(spawn_body(when_live="deliver"), now=NOW)
    assert failure.value.code == "invalid_when_live"
    # A past end date would disarm on the next sweep and read as a dead switch.
    with pytest.raises(ScheduleError) as failure:
        parse_spec(spawn_body(expires_at=NOW - 1), now=NOW)
    assert failure.value.code == "invalid_expires_at"
    with pytest.raises(ScheduleError):
        parse_spec(
            spawn_body(trigger_kind="once", cron="", run_at=NOW + 3600, expires_at=NOW + 60),
            now=NOW,
        )
    kept = parse_spec(spawn_body(expires_at=NOW + 86_400, model="opus"), now=NOW)
    assert kept.expires_at == NOW + 86_400 and kept.model == "opus"
    assert spec_from_row({**kept.as_dict(), "follow_ups": []}) == kept


# ---- fakes -------------------------------------------------------------------


@dataclass
class FakeRecord:
    id: str
    name: str = "worker"
    backend: str = "claude"
    project_id: str = "p1"
    project_scope_id: str = ""
    agent_run_id: str = ""
    state: str = "running"
    native_session_id: str = ""
    pid: int = 4242


@dataclass
class FakeSession:
    record: FakeRecord


@dataclass
class FakeProject:
    id: str
    root: str
    name: str


@dataclass
class FakeProjects:
    projects: dict[str, FakeProject]


class FakeAdapter:
    def resume_continues_conversation(self, recorded_cwd: str, target_cwd: str) -> bool:
        return True


class FakeSessions:
    def __init__(self) -> None:
        self.sessions: dict[str, FakeSession] = {}
        self.adapters = {"claude": FakeAdapter()}

    def add(self, record: FakeRecord) -> FakeSession:
        session = FakeSession(record)
        self.sessions[record.id] = session
        return session

    def conversation_holder(self, backend: str, native_id: str) -> None:
        return None


@dataclass
class FakeHistory:
    rows: dict[str, dict[str, Any]] = field(default_factory=dict)

    async def history_entry(self, run_id: str) -> dict[str, Any] | None:
        return self.rows.get(run_id)

    async def agent_runs_for_session(self, note_id: str) -> list[dict[str, Any]]:
        return []


@dataclass
class FakeAutomationStore:
    async def lineage(self, run_id: str | None = None) -> list[dict[str, Any]]:
        return []

    async def add_lineage(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return {}


@dataclass
class FakeEvents:
    emitted: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    async def emit(self, event_type: str, **payload: Any) -> None:
        self.emitted.append((event_type, payload))

    def actions(self) -> list[str]:
        return [
            str(payload.get("action"))
            for kind, payload in self.emitted
            if kind == "schedule_changed"
        ]


class World:
    """One daemon's worth of schedule plumbing over fakes, with a settable clock."""

    def __init__(self, tmp_path: Path) -> None:
        self.now = NOW
        self.store = ScheduleStore(tmp_path / "mux.db")
        self.config = Config(data_dir=tmp_path / "data")
        root_a = tmp_path / "repo-a"
        root_b = tmp_path / "repo-b"
        root_a.mkdir()
        root_b.mkdir()
        self.projects = FakeProjects(
            {
                "p1": FakeProject("p1", str(root_a), "Alpha"),
                "p2": FakeProject("p2", str(root_b), "Beta"),
            }
        )
        self.levels: dict[str, str] = {}
        self.opted_out: set[str] = set()
        self.sessions = FakeSessions()
        transcript = write_source(root_a)
        self.history = FakeHistory(
            {
                "run_me": {
                    "id": "run_me",
                    "native_id": SOURCE_ID,
                    "backend": "claude",
                    "name": "me",
                    "cwd": str(root_a),
                    "project_id": "p1",
                    "note_id": "",
                    "agent_run_seq": 0,
                    "spawned_at": NOW - 100,
                    "agent_visible": 1,
                    "auto_named": 0,
                    "transcript_path": str(transcript),
                    "final_context_pct": None,
                },
            }
        )
        self.events = FakeEvents()
        self.queued: list[dict[str, Any]] = []
        self.spawned: list[dict[str, Any]] = []
        self.caller = self.sessions.add(
            FakeRecord(
                id="pty-me", name="planner", agent_run_id="run_me", native_session_id=SOURCE_ID
            )
        )
        self.authoring = ScheduleAuthoringService(
            store=self.store,
            projects=self.projects,
            sessions=self.sessions,
            config=self.config,
            events=self.events,
            automation_gate=self._gate,
            schedule_grant=self._grant,
            history=self.history,
            enqueue=self._enqueue,
            clock=lambda: self.now,
        )
        self.scheduler = ScheduleService(
            store=self.store,
            projects=self.projects,
            sessions=self.sessions,
            config=self.config,
            events=self.events,
            automation_gate=self._gate,
            spawn_op=self._spawn,
            enqueue=self._enqueue,
            history=self.history,
            automation_store=FakeAutomationStore(),
            schedule_grant=self._grant,
            clock=lambda: self.now,
        )

    async def _gate(self, root: str) -> frozenset[str]:
        return frozenset() if root in self.opted_out else frozenset({"scheduled_runs"})

    def _grant(self, root: str) -> str:
        return self.levels.get(root, "limited")

    async def _enqueue(self, **kwargs: Any) -> dict[str, Any]:
        self.queued.append(kwargs)
        return {"id": f"m{len(self.queued)}"}

    async def _spawn(self, body: dict[str, Any]) -> Any:
        self.spawned.append(body)
        return self.sessions.add(FakeRecord(id=f"pty-new-{len(self.spawned)}", name="new"))

    def root(self, project_id: str) -> str:
        return self.projects.projects[project_id].root

    async def ask(self, **args: Any) -> dict[str, Any]:
        args.setdefault("prompt", "Check the build and report.")
        return await self.authoring.request(self.caller, args)


@pytest.fixture
def world(tmp_path: Path) -> Any:
    made = World(tmp_path)
    yield made
    made.store.close()


# ---- writing -----------------------------------------------------------------


async def test_a_one_off_spawn_is_armed_and_records_its_author(world: World) -> None:
    result = await world.ask(in_minutes=30, reason="CI needs a second look")
    assert result["status"] == "scheduled"
    row = await world.store.get(result["schedule_id"])
    assert row is not None
    assert row["enabled"] is True
    assert row["approval_state"] == "granted"
    assert row["author_kind"] == "agent"
    assert row["author_session_id"] == "pty-me"
    assert row["author_run_id"] == "run_me"
    assert row["request_reason"] == "CI needs a second look"
    assert row["next_fire_at"] == pytest.approx(NOW + 1800)
    # The harness the model would be checked against is fixed onto the row.
    assert row["backend"] == "claude"
    # A one-off an agent parks is something it waits on, so a late wake still runs it.
    assert row["catch_up"] is True
    assert "created" in world.events.actions()


async def test_a_low_frequency_recurring_schedule_is_armed_and_expires(world: World) -> None:
    result = await world.ask(cron="0 9,21 * * *")
    assert result["status"] == "scheduled"
    row = await world.store.get(result["schedule_id"])
    assert row is not None
    assert row["fires_per_day"] == 2
    assert row["expires_at"] == pytest.approx(NOW + world.config.agent_schedule_max_days * 86_400)
    assert row["catch_up"] is False


async def test_a_busy_recurring_schedule_waits_for_a_person(world: World) -> None:
    result = await world.ask(cron="0 * * * *")
    assert result["status"] == "awaiting_approval"
    row = await world.store.get(result["schedule_id"])
    assert row is not None
    assert row["enabled"] is False
    assert row["next_fire_at"] is None
    assert row["approval_state"] == "pending"
    assert row["disabled_reason"] == "awaiting_approval"
    assert "24 times a day" in row["approval_reason"]
    assert "requested" in world.events.actions()


async def test_the_project_level_decides(world: World) -> None:
    world.levels[world.root("p1")] = "granted"
    assert (await world.ask(cron="0 * * * *"))["status"] == "scheduled"
    world.levels[world.root("p1")] = "draft"
    assert (await world.ask(in_minutes=10, prompt="another"))["status"] == "awaiting_approval"
    world.levels[world.root("p1")] = "granted"
    world.opted_out.add(world.root("p1"))
    held = await world.ask(in_minutes=20, prompt="third")
    assert held["status"] == "awaiting_approval"
    assert "opted into scheduled runs" in held["approval_reason"]


async def test_a_schedule_follows_its_target_projects_rules(world: World) -> None:
    world.levels[world.root("p2")] = "draft"
    result = await world.ask(in_minutes=15, project="Beta")
    assert result["status"] == "awaiting_approval"
    assert result["project_name"] == "Beta"


async def test_lifetime_and_horizon_are_bounded(world: World) -> None:
    max_days = world.config.agent_schedule_max_days
    with pytest.raises(QueueError) as failure:
        await world.ask(cron="0 9 * * *", expires_in_days=max_days + 1)
    assert failure.value.code == "invalid_expiry"
    with pytest.raises(QueueError) as failure:
        await world.ask(in_minutes=(max_days + 1) * 1440)
    assert failure.value.code == "beyond_horizon"
    with pytest.raises(QueueError) as failure:
        await world.ask(at="2001-01-01T09:00")
    assert failure.value.code == "too_soon"
    shorter = await world.ask(cron="0 9 * * *", expires_in_days=3)
    assert shorter["expires_at"]["epoch"] == pytest.approx(NOW + 3 * 86_400)


async def test_exactly_one_trigger_is_required(world: World) -> None:
    for args in ({}, {"in_minutes": 5, "cron": "0 9 * * *"}):
        with pytest.raises(QueueError) as failure:
            await world.ask(**args)
        assert failure.value.code == "invalid_trigger"


async def test_a_retry_returns_the_schedule_it_already_made(world: World) -> None:
    first = await world.ask(cron="0 9 * * *")
    again = await world.ask(cron="0 9 * * *", label="renamed")
    assert again["status"] == "already_scheduled"
    assert again["schedule_id"] == first["schedule_id"]
    assert len(await world.store.list_schedules()) == 1


async def test_a_dry_run_writes_nothing_and_says_what_would_happen(world: World) -> None:
    preview = await world.ask(cron="0 * * * *", dry_run=True)
    assert preview["status"] == "dry_run"
    assert preview["would"] == "file for approval"
    assert preview["fires_per_day"] == 24
    assert len(preview["next_fires"]) == 3
    assert await world.store.list_schedules() == []


async def test_the_standing_cap_and_the_hourly_budget(world: World) -> None:
    world.config.agent_schedules_per_project = 2
    await world.ask(in_minutes=10, prompt="one")
    await world.ask(in_minutes=20, prompt="two")
    with pytest.raises(QueueError) as failure:
        await world.ask(in_minutes=30, prompt="three")
    assert failure.value.code == "schedule_cap_reached"
    assert len(failure.value.payload["standing"]) == 2

    world.config.agent_schedules_per_project = 50
    world.config.agent_schedule_hourly_budget = 3
    await world.ask(in_minutes=40, prompt="four")
    with pytest.raises(QueueError) as failure:
        await world.ask(in_minutes=50, prompt="five")
    assert failure.value.code == "origin_budget_exhausted"
    # Counted from the table, so an hour later the budget has come back.
    world.now += 3601
    await world.ask(in_minutes=60, prompt="six")


async def test_install_off_refuses_rather_than_filing(world: World) -> None:
    world.config.scheduled_runs_enabled = False
    with pytest.raises(QueueError) as failure:
        await world.ask(in_minutes=10)
    assert failure.value.code == "scheduled_runs_disabled"


async def test_resuming_yourself_pins_your_conversation_and_delivers_by_default(
    world: World,
) -> None:
    once = await world.ask(action="resume", in_minutes=120, prompt="Pick the migration back up")
    row = await world.store.get(once["schedule_id"])
    assert row is not None
    assert row["action"] == "resume"
    assert row["target_run_id"] == "run_me"
    assert row["target_kind"] == "run"
    assert row["when_live"] == "deliver"
    assert once["target"]["is_you"] is True
    recurring = await world.ask(target="self", cron="0 9 * * *", prompt="Morning check-in")
    stored = await world.store.get(recurring["schedule_id"])
    assert stored is not None and stored["target_kind"] == "latest_of_session"


async def test_a_resume_runs_in_its_conversations_own_project(world: World) -> None:
    with pytest.raises(QueueError) as failure:
        await world.ask(action="resume", in_minutes=30, project="Beta")
    assert failure.value.code == "project_mismatch"


async def test_a_session_with_no_conversation_cannot_resume_itself(world: World) -> None:
    world.caller.record.agent_run_id = ""
    with pytest.raises(QueueError) as failure:
        await world.ask(action="resume", in_minutes=30)
    assert failure.value.code == "no_conversation"


async def test_only_the_author_may_cancel(world: World) -> None:
    mine = await world.ask(cron="0 9 * * *")
    other_caller = FakeSession(FakeRecord(id="pty-other", agent_run_id="run_other"))
    with pytest.raises(QueueError) as failure:
        await world.authoring.cancel(other_caller, mine["schedule_id"])
    assert failure.value.code == "not_yours"
    person = await world.authoring.create_for_person(
        world.projects.projects["p1"], spawn_body()
    )
    with pytest.raises(QueueError) as failure:
        await world.authoring.cancel(world.caller, person["id"])
    assert failure.value.code == "not_yours"

    cancelled = await world.authoring.cancel(world.caller, mine["schedule_id"], "done")
    assert cancelled["status"] == "cancelled"
    row = await world.store.get(mine["schedule_id"])
    assert row is not None and row["enabled"] is False and row["disabled_reason"] == "cancelled"
    # The same author, after its pane resumed into a new session, still owns it.
    resumed = FakeSession(FakeRecord(id="pty-resumed", agent_run_id="run_me"))
    pending = await world.ask(cron="0 * * * *", prompt="hourly")
    withdrawn = await world.authoring.cancel(resumed, pending["schedule_id"])
    assert withdrawn["state"] == "dismissed"


async def test_approval_arms_it_tells_the_author_and_cannot_win_twice(world: World) -> None:
    pending = await world.ask(cron="0 * * * *")
    approved = await world.authoring.decide(pending["schedule_id"], "approve", decided_by="user")
    assert approved["enabled"] is True
    assert approved["approval_state"] == "approved"
    assert approved["decided_by"] == "user"
    assert approved["next_fire_at"] is not None
    notice = world.queued[-1]
    assert notice["target_session_id"] == "pty-me"
    assert notice["armed"] is True
    assert notice["solicited_by"] == pending["schedule_id"]
    assert "approved" in notice["body"]
    with pytest.raises(QueueError) as failure:
        await world.authoring.decide(pending["schedule_id"], "dismiss", decided_by="user")
    assert failure.value.code == "already_decided"


async def test_dismissal_keeps_the_record_and_frees_the_slot(world: World) -> None:
    world.config.agent_schedules_per_project = 1
    pending = await world.ask(cron="0 * * * *")
    dismissed = await world.authoring.decide(pending["schedule_id"], "dismiss", decided_by="user")
    assert dismissed["enabled"] is False
    assert dismissed["approval_state"] == "dismissed"
    assert await world.store.get(pending["schedule_id"]) is not None
    await world.ask(in_minutes=10, prompt="fits now")


async def test_an_approved_one_off_whose_moment_passed_fires_next(world: World) -> None:
    world.levels[world.root("p1")] = "draft"
    pending = await world.ask(in_minutes=5)
    world.now += 3600
    approved = await world.authoring.decide(pending["schedule_id"], "approve", decided_by="user")
    assert approved["next_fire_at"] == pytest.approx(world.now)


async def test_listing_reports_the_rules_and_filters_to_yours(world: World) -> None:
    await world.ask(cron="0 9 * * *")
    await world.authoring.create_for_person(world.projects.projects["p1"], spawn_body())
    listing = await world.authoring.list_for_agent(world.caller, {})
    assert len(listing["schedules"]) == 2
    mine = await world.authoring.list_for_agent(world.caller, {"mine": True})
    assert [item["yours"] for item in mine["schedules"]] == [True]
    policy = listing["policy"]
    assert policy["schedule_grant"] == "limited"
    assert policy["daily_fire_limit"] == world.config.agent_schedule_daily_fire_limit
    assert policy["slots_left"] == world.config.agent_schedules_per_project - 1


async def test_a_person_editing_an_agent_schedule_vouches_for_it(world: World) -> None:
    armed = await world.ask(cron="0 9 * * *")
    spec = parse_spec(spawn_body(cron="0 10 * * *"), now=NOW)
    replaced = await world.store.replace(
        armed["schedule_id"], spec=spec, next_fire_at=NOW + 60, now=NOW, fires_per_day=1
    )
    assert replaced is not None and replaced["approval_state"] == "approved"
    # Editing a pending request is not approving it.
    pending = await world.ask(cron="0 * * * *", prompt="hourly")
    edited = await world.store.replace(
        pending["schedule_id"], spec=spec, next_fire_at=NOW + 60, now=NOW, fires_per_day=1
    )
    assert edited is not None
    assert edited["approval_state"] == "pending"
    assert edited["disabled_reason"] == "awaiting_approval"
    assert edited["next_fire_at"] is None


async def test_the_mcp_tools_are_transport_over_the_service(world: World) -> None:
    import json

    from swe_mux.mcp import McpService

    service = McpService(world.sessions, world.history, schedule_authoring=world.authoring)
    made = await service.dispatch_tool(
        world.caller, "request_schedule", {"prompt": "Look again.", "in_minutes": 45}
    )
    assert made["status"] == "scheduled"
    listed = await service.dispatch_tool(world.caller, "list_schedules", {"mine": True})
    assert [item["schedule_id"] for item in listed["schedules"]] == [made["schedule_id"]]
    # A refusal is a typed result the agent can act on, not a protocol fault.
    refused = await service.handle_rpc(
        world.caller,
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "request_schedule", "arguments": {"prompt": "x"}},
        },
    )
    assert refused is not None and refused["result"]["isError"] is True
    assert json.loads(refused["result"]["content"][0]["text"])["error"] == "invalid_trigger"
    cancelled = await service.dispatch_tool(
        world.caller, "cancel_schedule", {"schedule_id": made["schedule_id"]}
    )
    assert cancelled["status"] == "cancelled"
    # With no authoring service wired, the tools say so rather than half-writing.
    bare = McpService(world.sessions, world.history)
    unavailable = await bare.handle_rpc(
        world.caller,
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "list_schedules", "arguments": {}},
        },
    )
    assert unavailable is not None and unavailable["result"]["isError"] is True


# ---- firing ------------------------------------------------------------------


async def due(world: World, schedule_id: str) -> None:
    await world.store.set_next_fire(schedule_id, world.now, now=world.now)


async def test_an_agent_spawn_fires_with_an_attributed_seed(world: World) -> None:
    result = await world.ask(in_minutes=30, prompt="Check the build.")
    world.now += 1800
    settled = await world.scheduler.tick(now=world.now)
    assert [item["outcome"] for item in settled] == ["spawned"]
    seed = world.spawned[0]["seed_text"]
    assert seed.startswith("[swe-mux] This session was started by the schedule")
    assert result["schedule_id"] in seed
    assert '"planner"' in seed
    assert seed.endswith("Check the build.")


async def test_lowering_the_level_turns_an_armed_schedule_back_into_a_request(
    world: World,
) -> None:
    world.levels[world.root("p1")] = "granted"
    armed = await world.ask(cron="0 * * * *")
    world.levels[world.root("p1")] = "limited"
    await due(world, armed["schedule_id"])
    settled = await world.scheduler.tick(now=world.now)
    assert settled[0]["outcome"] == "skipped"
    assert "approval" in settled[0]["reason"]
    assert world.spawned == []
    row = await world.store.get(armed["schedule_id"])
    assert row is not None
    assert row["approval_state"] == "pending"
    assert row["enabled"] is False


async def test_an_approved_schedule_is_not_re_judged(world: World) -> None:
    pending = await world.ask(cron="0 * * * *")
    await world.authoring.decide(pending["schedule_id"], "approve", decided_by="user")
    await due(world, pending["schedule_id"])
    settled = await world.scheduler.tick(now=world.now)
    assert settled[0]["outcome"] == "spawned"


async def test_an_expired_schedule_is_disarmed_and_does_not_fire(world: World) -> None:
    armed = await world.ask(cron="0 9 * * *", expires_in_days=1)
    world.now += 2 * 86_400
    await due(world, armed["schedule_id"])
    settled = await world.scheduler.tick(now=world.now)
    assert settled == []
    row = await world.store.get(armed["schedule_id"])
    assert row is not None and row["enabled"] is False and row["disabled_reason"] == "expired"
    assert "expired" in world.events.actions()


async def test_resuming_yourself_while_open_delivers_into_your_pane(world: World) -> None:
    result = await world.ask(action="resume", in_minutes=60, prompt="Check CI again.")
    world.now += 3600
    settled = await world.scheduler.tick(now=world.now)
    assert settled[0]["outcome"] == "delivered"
    assert settled[0]["session_id"] == "pty-me"
    message = world.queued[-1]
    assert message["target_session_id"] == "pty-me"
    # The conversation asked for this itself, so it is armed on its own request.
    assert message["armed"] is True
    assert message["sender_kind"] == "rule"
    assert message["solicited_by"] == result["schedule_id"]
    assert message["body"].startswith("[swe-mux] Scheduled message: you set up the schedule")
    assert message["body"].endswith("Check CI again.")
    row = await world.store.get(result["schedule_id"])
    # A pane this schedule did not open never becomes its own live session.
    assert row is not None and row["last_session_id"] is None
    assert world.spawned == []


async def test_a_delivery_still_respects_the_skip_policy(world: World) -> None:
    result = await world.ask(action="resume", in_minutes=60, when_live="skip")
    world.now += 3600
    settled = await world.scheduler.tick(now=world.now)
    assert settled[0]["outcome"] == "skipped"
    assert all(item.get("solicited_by") != result["schedule_id"] for item in world.queued)


async def test_resuming_another_conversation_is_attributed_to_its_author(world: World) -> None:
    # The target is open in somebody else's pane, so the prompt is delivered there as
    # the authoring agent's message - armed only on that conversation's own grant.
    world.caller.record.native_session_id = ""
    world.caller.record.agent_run_id = "run_planner"
    world.history.rows["run_planner"] = {**world.history.rows["run_me"], "id": "run_planner"}
    world.sessions.add(
        FakeRecord(id="pty-owner", agent_run_id="run_me", native_session_id=SOURCE_ID)
    )
    result = await world.ask(action="resume", target="run_me", in_minutes=60, prompt="Status?")
    world.now += 3600
    settled = await world.scheduler.tick(now=world.now)
    assert settled[0]["outcome"] == "delivered"
    message = world.queued[-1]
    assert message["target_session_id"] == "pty-owner"
    assert message["sender_kind"] == "agent"
    assert message["sender_id"] == "pty-me"
    assert "solicited_by" not in message
    assert "It is not from the operator" in message["body"]
    assert result["target"]["is_you"] is False


async def test_a_persons_resume_stays_exactly_as_it_was(world: World) -> None:
    person = await world.store.create(
        project_id="p1",
        project_root=world.root("p1"),
        spec=parse_spec(
            {
                "label": "mine",
                "prompt": "carry on",
                "action": "resume",
                "target_run_id": "run_me",
                "trigger_kind": "once",
                "run_at": NOW + 60,
                "when_live": "deliver",
            },
            now=NOW,
        ),
        next_fire_at=NOW,
        now=NOW,
        author=ScheduleAuthor(),
    )
    settled = await world.scheduler.tick(now=world.now)
    assert settled[0]["outcome"] == "delivered"
    message = world.queued[-1]
    assert message["sender_kind"] == "rule"
    assert message["armed"] is False
    assert message["body"] == "carry on"
    assert person["author_kind"] == "user"
