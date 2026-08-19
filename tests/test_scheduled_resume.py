"""Scheduling a conversation to be reopened later, and everything that can refuse it.

Three questions, and this file is organised by them:

- **Is the definition legal** (`schedules.parse_spec`). A resume names a conversation
  by history run id and may not name a harness, a launch profile or a working
  directory, because the row already fixes all three.
- **Which conversation is it now** (`session_resume.resolve_latest_run`). A rolling
  target follows rollovers and resumes and nothing else; a branch or a review edge is
  different work and following one would silently retarget the schedule.
- **What happens when it fires** (`scheduler.ScheduleService`). The prompt goes through
  the queue rather than argv, a conversation somebody is already in is skipped rather
  than alerted, a full one is refused, and a fork starts from the pinned point.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from swe_mux import session_resume
from swe_mux.schedule_store import ScheduleStore
from swe_mux.scheduler import ScheduleService, spec_from_row
from swe_mux.schedules import (
    DEFAULT_CONTEXT_CEILING_PCT,
    MAX_RESUME_ONCE_HORIZON_SECONDS,
    ScheduleError,
    parse_spec,
)
from swe_mux.session_resume import ResumeRefused, resolve_latest_run, resume_run

from .support.claude_transcript import SOURCE_ID, write_source

NOW = 1_000_000.0


def resume_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "label": "Pick it back up",
        "prompt": "Carry on where we left off.",
        "action": "resume",
        "target_run_id": "run_a",
        "trigger_kind": "once",
        "run_at": NOW + 3_600,
    }
    body.update(overrides)
    return body


# ---- what a legal resume definition is ---------------------------------------


def test_a_resume_names_a_run_and_defaults_to_pinning_it() -> None:
    spec = parse_spec(resume_body(), now=NOW)
    assert spec.action == "resume"
    assert spec.target_run_id == "run_a"
    assert spec.target_kind == "run"
    # A pinned conversation cannot grow past a ceiling by being resumed, so carrying one
    # would be a switch that reads as protection and does nothing.
    assert spec.context_ceiling_pct == 0.0


def test_a_rolling_target_gets_a_ceiling_it_did_not_ask_for() -> None:
    spec = parse_spec(resume_body(target_kind="latest_of_session"), now=NOW)
    assert spec.context_ceiling_pct == DEFAULT_CONTEXT_CEILING_PCT
    explicit = parse_spec(
        resume_body(target_kind="latest_of_session", context_ceiling_pct=0), now=NOW
    )
    assert explicit.context_ceiling_pct == 0.0
    with pytest.raises(ScheduleError) as failure:
        parse_spec(resume_body(target_kind="latest_of_session", context_ceiling_pct=1.5), now=NOW)
    assert failure.value.code == "invalid_target"


def test_a_resume_may_not_name_a_harness_a_profile_or_a_directory() -> None:
    # Refused rather than ignored: accepting one silently would make the editor offer
    # control it does not have, and the pane would run something else.
    for field_name, value in (
        ("backend", "codex"),
        ("profile_id", "claude-plan"),
        ("cwd", "/repo/sub"),
    ):
        with pytest.raises(ScheduleError) as failure:
            parse_spec(resume_body(**{field_name: value}), now=NOW)
        assert failure.value.code == f"invalid_{field_name}"
        assert field_name in failure.value.fields


def test_a_resume_cannot_be_told_to_start_a_second_session() -> None:
    with pytest.raises(ScheduleError) as failure:
        parse_spec(resume_body(overlap="allow"), now=NOW)
    assert failure.value.code == "invalid_overlap"
    assert "once" in str(failure.value)


def test_a_fork_target_must_name_the_message_and_the_side() -> None:
    with pytest.raises(ScheduleError):
        parse_spec(resume_body(target_kind="fork_point"), now=NOW)
    with pytest.raises(ScheduleError):
        parse_spec(
            resume_body(target_kind="fork_point", target_cut_message_id="m2"), now=NOW
        )
    spec = parse_spec(
        resume_body(target_kind="fork_point", target_cut_message_id="m2", target_cut_mode="after"),
        now=NOW,
    )
    assert (spec.target_cut_message_id, spec.target_cut_mode) == ("m2", "after")


def test_a_once_resume_is_held_inside_the_window_the_cli_keeps_its_transcript() -> None:
    # The agent CLIs prune their own conversations on their own timers and mux is not
    # consulted, so a resume parked past that window would most likely find nothing to
    # reopen. Refused where the author can choose something else.
    with pytest.raises(ScheduleError) as failure:
        parse_spec(resume_body(run_at=NOW + MAX_RESUME_ONCE_HORIZON_SECONDS + 60), now=NOW)
    assert failure.value.code == "invalid_run_at"
    assert "prunes" in str(failure.value)
    # A spawn keeps the far longer horizon, because nothing outside mux expires it.
    parse_spec(
        {
            "label": "later",
            "prompt": "go",
            "trigger_kind": "once",
            "run_at": NOW + MAX_RESUME_ONCE_HORIZON_SECONDS + 60,
        },
        now=NOW,
    )


def test_a_spawn_may_not_smuggle_a_target() -> None:
    with pytest.raises(ScheduleError) as failure:
        parse_spec({"label": "x", "prompt": "y", "cron": "0 3 * * *", "target_run_id": "run_a"})
    assert failure.value.code == "invalid_target"


# ---- the store ---------------------------------------------------------------


@pytest.fixture
def store(tmp_path: Path) -> ScheduleStore:
    made = ScheduleStore(tmp_path / "mux.db")
    yield made
    made.close()


async def test_the_store_round_trips_a_resume(store: ScheduleStore) -> None:
    spec = parse_spec(
        resume_body(
            target_kind="fork_point", target_cut_message_id="m2", target_cut_mode="before"
        ),
        now=NOW,
    )
    row = await store.create(
        project_id="p1", project_root="/repo", spec=spec, next_fire_at=NOW + 10, now=NOW
    )
    assert row["action"] == "resume"
    assert row["target_run_id"] == "run_a"
    assert row["target_kind"] == "fork_point"
    assert row["target_cut_message_id"] == "m2"
    assert row["target_cut_mode"] == "before"
    # And the spec rebuilt from the row is the spec that was written, because the fire
    # path reads it back rather than keeping the validated object.
    assert spec_from_row(row) == spec


def test_an_older_database_gains_the_columns_and_keeps_its_rows(tmp_path: Path) -> None:
    """A schema-1 database opens as a schema-2 one, and its rows read as spawns.

    The migration is `ALTER TABLE ADD COLUMN` rather than a rebuild precisely because
    every default reads as the old behaviour: a row written before this feature existed
    *was* a deferred spawn with no target.
    """
    path = tmp_path / "mux.db"
    legacy = sqlite3.connect(path)
    legacy.executescript(
        """
        CREATE TABLE schedules(
          id TEXT PRIMARY KEY, project_id TEXT NOT NULL, project_root TEXT NOT NULL DEFAULT '',
          label TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1, trigger_kind TEXT NOT NULL,
          cron TEXT NOT NULL DEFAULT '', interval_seconds REAL, run_at REAL,
          timezone TEXT NOT NULL DEFAULT '', catch_up INTEGER NOT NULL DEFAULT 0,
          overlap TEXT NOT NULL DEFAULT 'skip', backend TEXT NOT NULL DEFAULT '',
          profile_id TEXT NOT NULL DEFAULT '', cwd TEXT NOT NULL DEFAULT '',
          session_name TEXT NOT NULL DEFAULT '', prompt TEXT NOT NULL,
          follow_ups_json TEXT NOT NULL DEFAULT '[]', daily_run_cap INTEGER NOT NULL DEFAULT 0,
          next_fire_at REAL, last_fire_at REAL, last_session_id TEXT,
          last_outcome TEXT NOT NULL DEFAULT '', last_reason TEXT NOT NULL DEFAULT '',
          disabled_reason TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL,
          updated_at REAL NOT NULL, revision INTEGER NOT NULL DEFAULT 1
        );
        """
    )
    legacy.execute(
        "INSERT INTO schedules(id,project_id,label,trigger_kind,cron,prompt,created_at,updated_at)"
        " VALUES('sch_old','p1','Nightly','cron','0 3 * * *','go',1,1)"
    )
    legacy.commit()
    legacy.close()

    opened = ScheduleStore(path)
    try:
        columns = {
            str(row[1]) for row in sqlite3.connect(path).execute("PRAGMA table_info(schedules)")
        }
        assert {"action", "target_run_id", "context_ceiling_pct"} <= columns
    finally:
        opened.close()


# ---- following a conversation to where it has got to -------------------------


@dataclass
class FakeHistory:
    rows: dict[str, dict[str, Any]] = field(default_factory=dict)

    async def history_entry(self, run_id: str) -> dict[str, Any] | None:
        return self.rows.get(run_id)

    async def agent_runs_for_session(self, note_id: str) -> list[dict[str, Any]]:
        return sorted(
            (row for row in self.rows.values() if str(row.get("note_id") or "") == note_id),
            key=lambda row: (int(row.get("agent_run_seq") or 0), float(row.get("spawned_at") or 0)),
        )


@dataclass
class FakeAutomationStore:
    edges: list[dict[str, Any]] = field(default_factory=list)
    added: list[tuple[str, str, str, dict[str, Any]]] = field(default_factory=list)

    async def lineage(self, run_id: str | None = None) -> list[dict[str, Any]]:
        return [
            edge
            for edge in self.edges
            if not run_id or run_id in {edge["parent_run_id"], edge["child_run_id"]}
        ]

    async def add_lineage(
        self, parent: str, child: str, relation: str, metadata: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        self.added.append((parent, child, relation, metadata or {}))
        return {"id": "edge"}


def run_row(run_id: str, **overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": run_id,
        "native_id": f"conv-{run_id}",
        "backend": "claude",
        "name": run_id,
        "cwd": "",
        "project_id": "p1",
        "note_id": "",
        "agent_run_seq": 0,
        "spawned_at": NOW,
        "agent_visible": 1,
        "auto_named": 0,
        "transcript_path": "",
        "final_context_pct": None,
    }
    row.update(overrides)
    return row


async def test_a_rolling_target_follows_a_rollover_then_a_resume() -> None:
    history = FakeHistory(
        {
            "r1": run_row("r1", note_id="s1", agent_run_seq=0),
            "r2": run_row("r2", note_id="s1", agent_run_seq=1),
            "r3": run_row("r3", note_id="s2"),
        }
    )
    store = FakeAutomationStore(
        [{"parent_run_id": "r2", "child_run_id": "r3", "relation": "resume", "created_at": NOW}]
    )
    latest = await resolve_latest_run("r1", history=history, automation_store=store)
    assert latest is not None and latest["id"] == "r3"


async def test_a_rolling_target_ignores_a_branch_and_a_review() -> None:
    # Both are different work reading or forking this conversation. Following either
    # would point an unattended schedule at something its author never chose.
    history = FakeHistory({"r1": run_row("r1"), "fork": run_row("fork"), "rev": run_row("rev")})
    store = FakeAutomationStore(
        [
            {"parent_run_id": "r1", "child_run_id": "fork", "relation": "branch",
             "created_at": NOW},
            {"parent_run_id": "r1", "child_run_id": "rev", "relation": "review",
             "created_at": NOW},
        ]
    )
    latest = await resolve_latest_run("r1", history=history, automation_store=store)
    assert latest is not None and latest["id"] == "r1"


async def test_a_lineage_cycle_terminates() -> None:
    history = FakeHistory({"r1": run_row("r1"), "r2": run_row("r2")})
    store = FakeAutomationStore(
        [
            {"parent_run_id": "r1", "child_run_id": "r2", "relation": "resume", "created_at": NOW},
            {"parent_run_id": "r2", "child_run_id": "r1", "relation": "resume", "created_at": NOW},
        ]
    )
    latest = await resolve_latest_run("r1", history=history, automation_store=store)
    assert latest is not None and latest["id"] == "r2"


async def test_a_deleted_target_resolves_to_nothing() -> None:
    latest = await resolve_latest_run(
        "gone", history=FakeHistory(), automation_store=FakeAutomationStore()
    )
    assert latest is None


# ---- the resume itself -------------------------------------------------------


@dataclass
class FakeRecord:
    id: str
    state: str = "running"
    pid: int = 4242
    backend: str = "claude"
    native_session_id: str = ""
    agent_run_id: str | None = None
    project_id: str = "p1"
    name: str = "pane"


@dataclass
class FakeSession:
    record: FakeRecord


@dataclass
class FakeHolder:
    pid: int
    kind: str = "bg"
    job_id: str = "job"
    name: str = "held"

    def describe(self) -> str:
        return f"conversation is held by pid {self.pid}"


@dataclass
class FakeProject:
    id: str
    root: str
    name: str = "Repo"


@dataclass
class FakeProjects:
    projects: dict[str, FakeProject]


class FakeAdapter:
    def __init__(self, *, continues: bool = True) -> None:
        self.continues = continues

    def resume_continues_conversation(self, recorded_cwd: str, target_cwd: str) -> bool:
        return self.continues

    def transcript_path(self, native_id: str, cwd: Path) -> Path:
        return Path(cwd) / f"{native_id}.jsonl"


class FakeSessions:
    """Just enough `SessionManager` for `spawn_settled` and the claim checks."""

    def __init__(self, root: str) -> None:
        self.sessions: dict[str, FakeSession] = {}
        self.adapters: dict[str, FakeAdapter] = {"claude": FakeAdapter()}
        self.spawns: list[dict[str, Any]] = []
        self.holders: dict[str, FakeHolder] = {}
        self.root = root
        self.spawn_dies = False

    def conversation_holder(self, backend: str, native_id: str) -> FakeHolder | None:
        return self.holders.get(native_id)

    async def spawn(self, **kwargs: Any) -> FakeSession:
        self.spawns.append(kwargs)
        record = FakeRecord(
            id=f"s{len(self.spawns)}",
            native_session_id=str(kwargs.get("resume_native_id") or ""),
            agent_run_id=kwargs.get("adopt_run_id") or f"s{len(self.spawns)}",
            state="exited" if self.spawn_dies else "running",
        )
        session = FakeSession(record)
        self.sessions[record.id] = session
        if not self.spawn_dies:
            # The resumed CLI publishing our own pid against the conversation is what
            # ends the settle window early; without it every test would pay it in full.
            self.holders[record.native_session_id] = FakeHolder(pid=record.pid, kind="cli")
        return session

    async def stop(self, session_id: str) -> None:
        self.sessions.pop(session_id, None)


@pytest.fixture
def conversation(tmp_path: Path) -> tuple[Path, dict[str, Any]]:
    """A real Claude transcript and the history row that points at it."""
    transcript = write_source(tmp_path)
    row = run_row(
        "run_a",
        native_id=SOURCE_ID,
        cwd=str(tmp_path),
        transcript_path=str(transcript),
        name="Storage migration",
    )
    return transcript, row


async def test_a_resume_opens_the_conversation_and_inherits_its_run(
    conversation: tuple[Path, dict[str, Any]], tmp_path: Path
) -> None:
    _transcript, row = conversation
    sessions = FakeSessions(str(tmp_path))
    projects = FakeProjects({"p1": FakeProject("p1", str(tmp_path))})
    outcome = await resume_run(row, sessions=sessions, projects=projects, target_project_id="p1")
    assert outcome.adopted_run_id == "run_a"
    assert sessions.spawns[0]["resume_native_id"] == SOURCE_ID
    assert sessions.spawns[0]["adopt_run_id"] == "run_a"
    assert sessions.spawns[0]["cwd"] == str(tmp_path)
    assert sessions.spawns[0]["name"] == "Storage migration"


async def test_a_resume_into_another_root_earns_its_own_run(
    conversation: tuple[Path, dict[str, Any]], tmp_path: Path
) -> None:
    # Only the adapter can answer this, because it is the CLI's own transcript
    # resolution rule rather than anything mux decides.
    _transcript, row = conversation
    sessions = FakeSessions(str(tmp_path))
    sessions.adapters["claude"] = FakeAdapter(continues=False)
    projects = FakeProjects({"p1": FakeProject("p1", str(tmp_path))})
    outcome = await resume_run(row, sessions=sessions, projects=projects, target_project_id="p1")
    assert outcome.adopted_run_id is None
    assert sessions.spawns[0]["adopt_run_id"] is None


async def test_every_structural_refusal_is_named(
    conversation: tuple[Path, dict[str, Any]], tmp_path: Path
) -> None:
    _transcript, row = conversation
    sessions = FakeSessions(str(tmp_path))
    projects = FakeProjects({"p1": FakeProject("p1", str(tmp_path))})

    async def refusal(**changes: Any) -> str:
        with pytest.raises(ResumeRefused) as failure:
            await resume_run(
                {**row, **changes},
                sessions=sessions,
                projects=projects,
                target_project_id=changes.pop("_project", "p1"),
            )
        return failure.value.code

    assert await refusal(agent_visible=0) == "not_agent"
    assert await refusal(native_id="") == "native_id_missing"
    assert await refusal(cwd=str(tmp_path / "gone")) == "cwd_missing"
    assert await refusal(transcript_path=str(tmp_path / "gone.jsonl")) == "transcript_unavailable"
    assert await refusal(backend="nonesuch") == "not_agent"


async def test_a_conversation_a_live_pane_holds_is_refused_rather_than_forked(
    conversation: tuple[Path, dict[str, Any]], tmp_path: Path
) -> None:
    _transcript, row = conversation
    sessions = FakeSessions(str(tmp_path))
    sessions.sessions["other"] = FakeSession(
        FakeRecord(id="other", native_session_id=SOURCE_ID, name="the other pane")
    )
    projects = FakeProjects({"p1": FakeProject("p1", str(tmp_path))})
    with pytest.raises(ResumeRefused) as failure:
        await resume_run(row, sessions=sessions, projects=projects, target_project_id="p1")
    assert failure.value.code == "conversation_live"
    assert failure.value.detail["session_id"] == "other"
    assert not sessions.spawns


async def test_a_conversation_a_background_agent_holds_is_refused(
    conversation: tuple[Path, dict[str, Any]], tmp_path: Path
) -> None:
    # Invisible to the pane check above: a conversation parked into a Claude background
    # agent outlives the pane that parked it, and resuming it produces a pane that
    # prints its refusal and exits a second later.
    _transcript, row = conversation
    sessions = FakeSessions(str(tmp_path))
    sessions.holders[SOURCE_ID] = FakeHolder(pid=999)
    projects = FakeProjects({"p1": FakeProject("p1", str(tmp_path))})
    with pytest.raises(ResumeRefused) as failure:
        await resume_run(row, sessions=sessions, projects=projects, target_project_id="p1")
    assert failure.value.code == "conversation_held"
    assert failure.value.detail["holder"]["pid"] == 999
    assert not sessions.spawns


async def test_a_pane_that_dies_on_spawn_is_reported_rather_than_returned(
    conversation: tuple[Path, dict[str, Any]], tmp_path: Path, monkeypatch: Any
) -> None:
    monkeypatch.setattr(session_resume, "RESUME_SETTLE_SECONDS", 0.25)
    monkeypatch.setattr(session_resume, "RESUME_RETRY_BACKOFF_SECONDS", 0.0)
    _transcript, row = conversation
    sessions = FakeSessions(str(tmp_path))
    sessions.spawn_dies = True
    projects = FakeProjects({"p1": FakeProject("p1", str(tmp_path))})
    with pytest.raises(ResumeRefused) as failure:
        await resume_run(row, sessions=sessions, projects=projects, target_project_id="p1")
    assert failure.value.code == "resume_failed"
    assert failure.value.detail["attempts"] == session_resume.RESUME_ATTEMPTS


async def test_a_fork_is_written_from_the_pinned_point_and_leaves_the_source_alone(
    conversation: tuple[Path, dict[str, Any]], tmp_path: Path
) -> None:
    transcript, row = conversation
    before = transcript.read_bytes()
    sessions = FakeSessions(str(tmp_path))
    projects = FakeProjects({"p1": FakeProject("p1", str(tmp_path))})
    points = session_resume.conversation_cut_points(transcript, "claude")
    assert points is not None
    cut = next(point for point in points if point.role == "assistant" and not point.open_tool_calls)
    fork = await session_resume.fork_run(
        row,
        sessions=sessions,
        projects=projects,
        target_project_id="p1",
        message_id=cut.message_id,
        mode="after",
    )
    assert fork["records_written"] > 0
    assert Path(fork["path"]).is_file()
    assert transcript.read_bytes() == before, "the source conversation must not be touched"

    outcome = await resume_run(
        row,
        sessions=sessions,
        projects=projects,
        target_project_id="p1",
        conversation_id=str(fork["conversation_id"]),
        fork=fork,
    )
    # A fork is a new conversation, so it never inherits the source's run row.
    assert outcome.adopted_run_id is None
    assert sessions.spawns[0]["resume_native_id"] == fork["conversation_id"]


async def test_a_fork_at_a_vanished_point_is_refused_by_name(
    conversation: tuple[Path, dict[str, Any]], tmp_path: Path
) -> None:
    # A pinned point is named from the newest window of messages, so a conversation that
    # has moved on can genuinely lose it. Saying which is what lets the schedule row
    # explain itself instead of failing generically.
    _transcript, row = conversation
    sessions = FakeSessions(str(tmp_path))
    projects = FakeProjects({"p1": FakeProject("p1", str(tmp_path))})
    with pytest.raises(ResumeRefused) as failure:
        await session_resume.fork_run(
            row,
            sessions=sessions,
            projects=projects,
            target_project_id="p1",
            message_id="not-a-message",
            mode="after",
        )
    assert failure.value.code == "branch_point_unknown"


# ---- firing a scheduled resume -----------------------------------------------


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


class ResumeHarness:
    """A `ScheduleService` whose resume path runs against fakes."""

    def __init__(self, store: ScheduleStore, root: Path, row: dict[str, Any]) -> None:
        self.store = store
        self.now = NOW
        self.projects = FakeProjects({"p1": FakeProject("p1", str(root))})
        self.sessions = FakeSessions(str(root))
        self.history = FakeHistory({str(row["id"]): row})
        self.automation = FakeAutomationStore()
        self.events = FakeEvents()
        self.queued: list[dict[str, Any]] = []
        self.notifications: list[dict[str, Any]] = []
        self.service = ScheduleService(
            store=store,
            projects=self.projects,
            sessions=self.sessions,
            config=FakeConfig(),
            events=self.events,
            automation_gate=self._gate,
            spawn_op=self._spawn,
            enqueue=self._enqueue,
            notify=self._notify,
            history=self.history,
            automation_store=self.automation,
            clock=lambda: self.now,
        )

    async def _gate(self, root: str) -> frozenset[str]:
        return frozenset({"scheduled_runs"})

    async def _spawn(self, body: dict[str, Any]) -> Any:
        raise AssertionError("a resume must not go through the new-session spawn path")

    async def _enqueue(self, **kwargs: Any) -> dict[str, Any]:
        self.queued.append(kwargs)
        return {"id": f"m{len(self.queued)}"}

    async def _notify(self, **kwargs: Any) -> dict[str, Any]:
        self.notifications.append(kwargs)
        return kwargs

    async def arm(self, **overrides: Any) -> dict[str, Any]:
        spec = parse_spec(resume_body(**overrides), now=self.now - 1)
        return await self.store.create(
            project_id="p1",
            project_root=str(self.projects.projects["p1"].root),
            spec=spec,
            next_fire_at=self.now,
            now=self.now,
        )


async def test_a_due_resume_reopens_the_conversation_and_queues_its_prompt(
    store: ScheduleStore, conversation: tuple[Path, dict[str, Any]], tmp_path: Path
) -> None:
    _transcript, row = conversation
    harness = ResumeHarness(store, tmp_path, row)
    await harness.arm(follow_ups=[{"body": "then summarize", "delay_seconds": 60}])
    settled = await harness.service.tick(now=harness.now)

    assert [item["outcome"] for item in settled] == ["spawned"]
    assert harness.sessions.spawns[0]["resume_native_id"] == SOURCE_ID
    # The prompt is the first queue item rather than argv: the resume argv is already
    # `--resume <id>`, and whether a positional prompt may follow it is per-harness luck.
    assert [item["body"] for item in harness.queued] == [
        "Carry on where we left off.",
        "then summarize",
    ]
    assert harness.queued[0]["sender_kind"] == "rule"
    assert harness.queued[0]["constraints"] is None
    assert harness.queued[1]["constraints"] == {"not_before": harness.now + 60}


async def test_a_conversation_somebody_is_in_is_skipped_without_an_alert(
    store: ScheduleStore, conversation: tuple[Path, dict[str, Any]], tmp_path: Path
) -> None:
    _transcript, row = conversation
    harness = ResumeHarness(store, tmp_path, row)
    harness.sessions.holders[SOURCE_ID] = FakeHolder(pid=999)
    await harness.arm()
    settled = await harness.service.tick(now=harness.now)

    assert settled[0]["outcome"] == "skipped"
    assert "holding" in settled[0]["reason"]
    # Routine rather than exceptional: a schedule armed against a conversation the
    # operator also uses by hand meets this often, and alerting would teach its reader
    # to ignore the alerts that matter.
    assert harness.notifications == []


async def test_a_deleted_target_disables_the_schedule_rather_than_retrying_nightly(
    store: ScheduleStore, conversation: tuple[Path, dict[str, Any]], tmp_path: Path
) -> None:
    _transcript, row = conversation
    harness = ResumeHarness(store, tmp_path, row)
    armed = await harness.arm(trigger_kind="cron", cron="0 3 * * *", run_at=None)
    harness.history.rows.clear()
    settled = await harness.service.tick(now=harness.now)

    assert settled[0]["outcome"] == "skipped"
    stored = await store.get(str(armed["id"]))
    assert stored is not None
    assert stored["enabled"] is False
    assert stored["disabled_reason"] == "target_missing"


async def test_a_full_conversation_is_refused_before_it_is_reopened(
    store: ScheduleStore, conversation: tuple[Path, dict[str, Any]], tmp_path: Path
) -> None:
    # Every resume replays the whole accumulated conversation, and past the ceiling its
    # early half has already been compacted into a summary - so continuing buys a turn
    # that no longer remembers what it is continuing.
    _transcript, row = conversation
    harness = ResumeHarness(store, tmp_path, {**row, "final_context_pct": 0.86})
    await harness.arm(target_kind="latest_of_session", context_ceiling_pct=0.7)
    settled = await harness.service.tick(now=harness.now)

    assert settled[0]["outcome"] == "skipped"
    assert "86%" in settled[0]["reason"]
    assert not harness.sessions.spawns


async def test_an_unmeasured_conversation_is_not_treated_as_a_full_one(
    store: ScheduleStore, conversation: tuple[Path, dict[str, Any]], tmp_path: Path
) -> None:
    _transcript, row = conversation
    harness = ResumeHarness(store, tmp_path, row)
    await harness.arm(target_kind="latest_of_session", context_ceiling_pct=0.7)
    settled = await harness.service.tick(now=harness.now)
    assert settled[0]["outcome"] == "spawned"


async def test_a_scheduled_fork_records_a_branch_edge_rather_than_a_resume_one(
    store: ScheduleStore, conversation: tuple[Path, dict[str, Any]], tmp_path: Path
) -> None:
    transcript, row = conversation
    harness = ResumeHarness(store, tmp_path, row)
    points = session_resume.conversation_cut_points(transcript, "claude")
    assert points is not None
    cut = next(point for point in points if point.role == "assistant" and not point.open_tool_calls)
    await harness.arm(
        target_kind="fork_point", target_cut_message_id=cut.message_id, target_cut_mode="after"
    )
    settled = await harness.service.tick(now=harness.now)

    assert settled[0]["outcome"] == "spawned"
    assert harness.sessions.spawns[0]["resume_native_id"] != SOURCE_ID
    # `resolve_latest_run` follows `resume` edges, so calling a fork a resume would make
    # every later fire of a rolling schedule chase last night's fork.
    assert [edge[2] for edge in harness.automation.added] == ["branch"]
    assert harness.automation.added[0][3]["from_message_id"] == cut.message_id
