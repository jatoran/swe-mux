from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from swe_mux import server
from swe_mux.adapters import ClaudeAdapter
from swe_mux.server import _branch_source_id, branch_session


@pytest.fixture(autouse=True)
def _fast_branch_timings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the settle/retry windows out of the suite's wall clock.

    The production values are sized for a real CLI (a sibling died 1.3s after spawn
    in the incident these guards exist for); the behaviour under test is the
    sequencing, which is identical at any scale.
    """
    monkeypatch.setattr(server, "BRANCH_SIBLING_SETTLE_SECONDS", 0.05)
    monkeypatch.setattr(server, "BRANCH_SIBLING_RETRY_BACKOFF_SECONDS", 0.0)
    monkeypatch.setattr(server, "BRANCH_RELEASE_TIMEOUT_SECONDS", 0.3)
    monkeypatch.setattr(server, "BRANCH_FORK_TIMEOUT_SECONDS", 2.0)


class FakeBus:
    """Enough of `EventBus` for the branch endpoint: fan-out and background emits."""

    def __init__(self) -> None:
        self.queues: list[asyncio.Queue[Any]] = []
        self.emitted: list[tuple[str, dict[str, Any]]] = []
        self.subscriptions = 0

    def subscribe(self, *, name: str = "anonymous") -> asyncio.Queue[Any]:
        queue: asyncio.Queue[Any] = asyncio.Queue()
        self.queues.append(queue)
        self.subscriptions += 1
        return queue

    def unsubscribe(self, queue: asyncio.Queue[Any]) -> None:
        if queue in self.queues:
            self.queues.remove(queue)

    def emit_background(self, event_type: str, **payload: Any) -> None:
        self.emitted.append((event_type, payload))

    def session_start(self, session_id: str, transcript_path: str) -> None:
        event = SimpleNamespace(
            session_id=session_id,
            type="SessionStart",
            payload={"transcript_path": transcript_path},
        )
        for queue in self.queues:
            queue.put_nowait(event)


def _request(record: Any) -> Any:
    session = SimpleNamespace(record=record, pty=SimpleNamespace(write=lambda data: None))

    class SessionsStub:
        def __init__(self) -> None:
            self.sessions = {record.id: session}

        def resolve(self, identity: str) -> Any:
            return self.sessions[identity]

    return SimpleNamespace(
        app={"sessions": SessionsStub()},
        match_info={"sid": record.id},
        can_read_body=False,
    )


async def test_branch_rejects_non_agent_sessions() -> None:
    record = SimpleNamespace(
        id="sh1", backend="shell", native_session_id="sh1",
        project_id="default", name="shell", cwd=".", state="idle",
    )
    response = await branch_session(cast(Any, _request(record)))
    assert response.status == 422
    assert json.loads(response.body)["code"] == "not_agent"


@pytest.mark.parametrize(
    ("state", "pending", "code"),
    [
        ("working", False, "source_busy"),
        ("awaiting", False, "source_busy"),
        ("crashed", False, "source_not_live"),
        ("idle", True, "source_composer_dirty"),
    ],
)
async def test_branch_refuses_a_pane_that_is_not_ready_for_a_slash_command(
    state: str, pending: bool, code: str
) -> None:
    """`/branch` is typed into somebody else's terminal, so it owes a readiness check.

    Mid-turn the command lands in a CLI that is not reading commands; with an approval
    up it answers the dialog; with unsent text in the composer it is appended to that
    text and the pair is submitted as a prompt.
    """
    record = SimpleNamespace(
        id="m1", backend="claude", native_session_id="m1",
        project_id="default", name="claude", cwd=".", state=state,
    )
    request = _request(record)
    request.app["sessions"].sessions["m1"].composer = SimpleNamespace(pending=pending)
    response = await branch_session(cast(Any, request))
    assert response.status == 409
    assert json.loads(response.body)["code"] == code


def test_branch_source_accepts_claude_native_id_equal_to_mux_id() -> None:
    # A fresh Claude session's native id equals its mux id (spawned via
    # --session-id); that is a valid transcript stem, not "missing".
    record = SimpleNamespace(id="m1", backend="claude", native_session_id="m1", cwd=".")
    source = SimpleNamespace(record=record, agent_lifecycle_id=None)
    assert _branch_source_id(source) == "m1"


def test_branch_source_prefers_lifecycle_anchor_over_cross_attributed_native_id() -> None:
    # If the observer latched onto a sibling's transcript, native_session_id is
    # wrong but the lifecycle anchor still holds the real conversation id.
    record = SimpleNamespace(id="m2", backend="claude", native_session_id="sibling-x", cwd=".")
    source = SimpleNamespace(record=record, agent_lifecycle_id="real-2")
    assert _branch_source_id(source) == "real-2"


def test_branch_source_none_for_codex_without_detected_rollout() -> None:
    record = SimpleNamespace(id="c3", backend="codex", native_session_id="c3", cwd=".")
    source = SimpleNamespace(record=record, agent_lifecycle_id=None)
    assert _branch_source_id(source) is None


class BranchHarness:
    """A Claude pane wired to a fake CLI, event bus, and session manager."""

    def __init__(self, tmp_path: Path, *, bus: FakeBus | None = None) -> None:
        self.adapter = ClaudeAdapter("claude.exe")
        self.cwd = tmp_path / "project"
        self.cwd.mkdir(exist_ok=True)
        self.original = "aaaaaaaa-1111-4a7b-8c9d-0e1f2a3b4c5d"
        self.forked = "cccccccc-3333-4a7b-8c9d-0e1f2a3b4c5d"
        self.transcripts = self.adapter.transcript_path(self.original, self.cwd).parent
        self.transcripts.mkdir(parents=True, exist_ok=True)
        (self.transcripts / f"{self.original}.jsonl").write_text("{}\n", encoding="utf-8")
        self.bus = bus
        self.rolled: list[dict[str, Any]] = []
        self.spawned: list[dict[str, Any]] = []
        self.stopped: list[str] = []
        # One entry per spawn attempt: the state its record reports while settling.
        self.spawn_states: list[str] = ["idle"]
        self.writes: list[str] = []
        self.fork_files: list[str] = [self.forked]
        self.report_fork: str | None = self.forked

        self.record = SimpleNamespace(
            id="pane-1", backend="claude", native_session_id=self.original,
            agent_run_id="run-original", project_id="default", name="device ownership",
            cwd=str(self.cwd), run_cwd=str(self.cwd), state="idle",
        )
        self.session = SimpleNamespace(
            record=self.record,
            agent_lifecycle_id=self.original,
            composer=SimpleNamespace(pending=False),
            pty=SimpleNamespace(write=self._on_write),
        )
        self.manager = SimpleNamespace(
            sessions={self.record.id: self.session},
            resolve=lambda _identity: self.session,
            adapters={"claude": self.adapter},
            roll_agent_conversation=self._roll,
            spawn=self._spawn,
            stop=self._stop,
        )
        app: dict[str, Any] = {
            "sessions": self.manager,
            "projects": SimpleNamespace(
                projects={
                    "default": SimpleNamespace(
                        name="Main", root=str(self.cwd),
                        layout={"version": 2, "root": None}, layout_revision=0,
                    )
                },
                update=self._update,
            ),
        }
        if bus is not None:
            app["events"] = bus
        self.request = SimpleNamespace(
            app=app, match_info={"sid": self.record.id}, can_read_body=False
        )

    def _on_write(self, data: str) -> None:
        """What the CLI does with `/branch`: new transcript(s), then a delayed report."""
        self.writes.append(data)
        assert data == "/branch\r"
        for stem in self.fork_files:
            (self.transcripts / f"{stem}.jsonl").write_text("{}\n", encoding="utf-8")
        if self.bus is not None and self.report_fork is not None:
            self.bus.session_start(
                self.record.id, str(self.transcripts / f"{self.report_fork}.jsonl")
            )

    async def _roll(self, sid: str, **kwargs: Any) -> bool:
        self.rolled.append({"sid": sid, **kwargs})
        return True

    async def _spawn(self, **kwargs: Any) -> Any:
        self.spawned.append(kwargs)
        index = len(self.spawned) - 1
        state = self.spawn_states[min(index, len(self.spawn_states) - 1)]
        return SimpleNamespace(
            record=SimpleNamespace(
                id=f"pane-{index + 2}", state=state, exit_code=1 if state == "crashed" else None,
                snapshot=lambda: {},
            )
        )

    async def _stop(self, sid: str) -> None:
        self.stopped.append(sid)

    async def _update(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    async def run(self) -> Any:
        return await branch_session(cast(Any, self.request))


async def test_a_claude_branch_hands_the_original_conversation_to_the_sibling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The sibling continues the original conversation, so it inherits its run.

    `/branch` moves the *source* pane onto a fresh conversation and frees the
    original, which the sibling then reopens. Opening a second row there showed one
    conversation as two entries over one file. The inheritance is only sound once the
    source pane has let go of the run, so the confirmed fork id is applied to it
    first.
    """
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    harness = BranchHarness(tmp_path, bus=FakeBus())

    response = await harness.run()

    assert response.status == 201
    # The source pane is retired onto the conversation the fork actually created,
    # rather than keeping the original id until some hook happens to report it.
    assert harness.rolled == [
        {"sid": "pane-1", "native_id": harness.forked, "reason": "branched", "source": "branch"}
    ]
    assert harness.spawned[0]["resume_native_id"] == harness.original
    assert harness.spawned[0]["adopt_run_id"] == "run-original"
    assert harness.stopped == []


async def test_branch_waits_for_the_cli_to_report_the_fork_before_reopening_the_original(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The CLI's report names the fork when the directory listing cannot.

    Two transcripts appearing at once makes "which file is the fork" a guess, and the
    listing rightly declines to make one. The source pane's own `SessionStart` is not
    a guess, so the branch still completes — which also proves the release wait runs
    before the sibling is spawned, since the id it supplies is required to spawn one.
    """
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    bus = FakeBus()
    harness = BranchHarness(tmp_path, bus=bus)
    # A second agent starts in this cwd in the same instant.
    harness.fork_files = [harness.forked, "dddddddd-4444-4a7b-8c9d-0e1f2a3b4c5d"]

    response = await harness.run()

    assert response.status == 201
    assert harness.rolled[0]["native_id"] == harness.forked
    assert harness.spawned[0]["adopt_run_id"] == "run-original"
    assert [name for name, _ in bus.emitted] == ["session_branched"]
    assert bus.emitted[0][1]["release"] == "reported"


async def test_branch_refuses_rather_than_opening_a_second_row_on_one_conversation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unidentifiable fork must not become two rows over one file.

    Without the roll the source pane keeps claiming the original, so resuming it in a
    sibling shows one conversation twice and indexes its file twice. The pane really is
    branched by then, so the honest answer is to say so and leave the original in
    History rather than to open something broken.
    """
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    harness = BranchHarness(tmp_path, bus=FakeBus())
    harness.fork_files = [harness.forked, "dddddddd-4444-4a7b-8c9d-0e1f2a3b4c5d"]
    harness.report_fork = None  # the CLI never reports; nothing can name the fork

    response = await harness.run()

    assert response.status == 409
    assert json.loads(response.body)["code"] == "branch_id_unresolved"
    assert harness.spawned == []
    assert harness.rolled == []


async def test_branch_retries_a_sibling_that_exits_on_spawn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The resume is verified by running, not predicted by a signal.

    Live on 2026-08-14 the sibling exited 1 about a second after spawn because the
    source process had not finished releasing the conversation, and the operator was
    handed a grey pane. A retry is the right response: the cause is a race, and the
    next attempt is further from it.
    """
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    harness = BranchHarness(tmp_path, bus=FakeBus())
    harness.spawn_states = ["crashed", "idle"]

    response = await harness.run()

    assert response.status == 201
    assert len(harness.spawned) == 2
    # The pane that died is taken back out of the world rather than left attached.
    assert harness.stopped == ["pane-2"]
    assert json.loads(response.body)["session"] == {}


async def test_branch_reports_a_sibling_that_never_comes_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A pane that will not stay up is removed and named, not attached and left grey."""
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    harness = BranchHarness(tmp_path, bus=FakeBus())
    harness.spawn_states = ["crashed"]

    response = await harness.run()

    assert response.status == 503
    body = json.loads(response.body)
    assert body["code"] == "branch_sibling_failed"
    assert body["attempts"] == server.BRANCH_SIBLING_ATTEMPTS
    assert len(harness.spawned) == server.BRANCH_SIBLING_ATTEMPTS
    assert len(harness.stopped) == server.BRANCH_SIBLING_ATTEMPTS


async def test_branch_proceeds_when_the_cli_never_reports_but_the_fork_is_unambiguous(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A missing report degrades to the old behaviour rather than blocking the branch.

    The release wait exists to make the first spawn attempt the one that works; the
    retry is what guarantees correctness. So a hook that never arrives costs latency,
    not the feature.
    """
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    bus = FakeBus()
    harness = BranchHarness(tmp_path, bus=bus)
    harness.report_fork = None

    response = await harness.run()

    assert response.status == 201
    assert harness.rolled[0]["native_id"] == harness.forked
    assert bus.emitted[0][1]["release"] == "timeout"


async def test_a_fork_that_never_lands_names_the_turn_that_swallowed_the_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The readiness gate is only as current as status detection.

    Live on 2026-08-14 a turn had begun but still read `idle` when the gate ran, so the
    command was written into a CLI that was thinking and was simply ignored. Nothing is
    damaged by that — no sibling is spawned and no identity is rolled — but "try again"
    alone leaves the operator with no idea what happened or when to retry.
    """
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    harness = BranchHarness(tmp_path, bus=FakeBus())
    harness.fork_files = []  # the CLI is busy; `/branch` goes nowhere
    harness.report_fork = None

    def start_a_turn(data: str) -> None:
        harness.writes.append(data)
        harness.record.state = "working"

    harness.session.pty.write = start_a_turn

    response = await harness.run()

    assert response.status == 504
    body = json.loads(response.body)
    assert body["code"] == "branch_timeout"
    assert body["source_state"] == "working"
    assert "the agent started a turn" in body["error"]
    assert harness.spawned == []
    assert harness.rolled == []


async def test_branch_rejects_codex_before_native_id_is_known() -> None:
    # Codex's native id is a placeholder equal to the mux id until its first
    # rollout is written; branching then would resume nothing.
    record = SimpleNamespace(
        id="cx1", backend="codex", native_session_id="cx1",
        project_id="default", name="codex", cwd=".", state="idle",
    )
    response = await branch_session(cast(Any, _request(record)))
    assert response.status == 409
    assert json.loads(response.body)["code"] == "native_id_missing"
