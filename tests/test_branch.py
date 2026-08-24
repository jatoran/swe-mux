from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from swe_mux import app_keys as keys
from swe_mux import server
from swe_mux.adapters import ClaudeAdapter
from swe_mux.server import _branch_source_id, branch_session, session_branch_points

from .support.claude_transcript import SIDECAR_NAME, read_records, write_source


@pytest.fixture(autouse=True)
def _fast_branch_timings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the settle/retry windows out of the suite's wall clock.

    The production values are sized for a real CLI (a sibling died 1.3s after spawn
    in the incident these guards exist for); the behaviour under test is the
    sequencing, which is identical at any scale.
    """
    monkeypatch.setattr(server, "BRANCH_SIBLING_SETTLE_SECONDS", 0.05)
    monkeypatch.setattr(server, "BRANCH_SIBLING_RETRY_BACKOFF_SECONDS", 0.0)


class FakeBus:
    def __init__(self) -> None:
        self.emitted: list[tuple[str, dict[str, Any]]] = []

    def emit_background(self, event_type: str, **payload: Any) -> None:
        self.emitted.append((event_type, payload))


class FakeAutomationStore:
    """The two things a branch asks the store: prior branches, and the run's title.

    Rows carry the real column names (`parent_run_id`, `agent_run_id`) rather than
    convenient short ones, because reading the wrong key is exactly the failure this
    stands in for: the naming rule silently degrades to "no title, no prior branches"
    if it does, and nothing else would notice.
    """

    def __init__(self, titles: dict[str, str] | None = None) -> None:
        self.edges: list[dict[str, Any]] = []
        self.titles = titles or {}

    async def add_lineage(
        self, parent: str, child: str, relation: str, metadata: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        edge = {
            "parent_run_id": parent,
            "child_run_id": child,
            "relation": relation,
            "metadata": metadata or {},
        }
        self.edges.append(edge)
        return edge

    async def lineage(self, run_id: str | None = None) -> list[dict[str, Any]]:
        return [
            edge
            for edge in self.edges
            if not run_id or run_id in {edge["parent_run_id"], edge["child_run_id"]}
        ]

    async def annotations(
        self,
        *,
        agent_run_ids: Any = None,
        tag: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        del limit
        wanted = set(agent_run_ids or [])
        return [
            {"agent_run_id": run_id, "content": title, "tags": [tag or "title"]}
            for run_id, title in self.titles.items()
            if not wanted or run_id in wanted
        ]


def _request(record: Any) -> Any:
    session = SimpleNamespace(record=record, pty=SimpleNamespace(write=lambda data: None))

    class SessionsStub:
        def __init__(self) -> None:
            self.sessions = {record.id: session}

        def resolve(self, identity: str) -> Any:
            return self.sessions[identity]

    return SimpleNamespace(
        app={keys.SESSIONS: SessionsStub()},
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


class BranchHarness:
    """A Claude pane over a real transcript on disk, with a stub session manager."""

    def __init__(
        self,
        tmp_path: Path,
        *,
        body: dict[str, Any] | None = None,
        titles: dict[str, str] | None = None,
    ) -> None:
        self.adapter = ClaudeAdapter("claude.exe")
        self.cwd = tmp_path / "project"
        self.cwd.mkdir(parents=True, exist_ok=True)
        self.transcripts = self.adapter.transcript_path("probe", self.cwd).parent
        self.source_path = write_source(self.transcripts)
        self.original = self.source_path.stem
        self.spawned: list[dict[str, Any]] = []
        self.stopped: list[str] = []
        # One entry per spawn attempt: the state its record reports while settling.
        self.spawn_states: list[str] = ["idle"]
        self.bus = FakeBus()
        self.store = FakeAutomationStore(titles)

        self.record = SimpleNamespace(
            id="pane-1", backend="claude", native_session_id=self.original,
            agent_run_id="run-original", project_id="default", name="device ownership",
            cwd=str(self.cwd), run_cwd=str(self.cwd), state="idle",
        )
        self.session = SimpleNamespace(
            record=self.record,
            agent_lifecycle_id=self.original,
            composer=SimpleNamespace(pending=False),
            transcript_path=self.source_path,
            pty=SimpleNamespace(write=self._refuse_write),
        )
        self.manager = SimpleNamespace(
            sessions={self.record.id: self.session},
            resolve=lambda _identity: self.session,
            adapters={"claude": self.adapter},
            spawn=self._spawn,
            stop=self._stop,
        )
        self.request = SimpleNamespace(
            app={
                keys.SESSIONS: self.manager,
                keys.EVENTS: self.bus,
                keys.AUTOMATION_STORE: self.store,
                keys.PROJECTS: SimpleNamespace(
                    projects={
                        "default": SimpleNamespace(
                            name="Main", root=str(self.cwd),
                            layout={"version": 2, "root": None}, layout_revision=0,
                        )
                    },
                    update=self._update,
                ),
            },
            match_info={"sid": self.record.id},
            can_read_body=body is not None,
            query={},
            json=self._body,
        )
        self._request_body = body or {}

    def _refuse_write(self, data: str) -> None:
        raise AssertionError(f"a transcript fork must not type into the pane: {data!r}")

    async def _body(self) -> dict[str, Any]:
        return self._request_body

    async def _spawn(self, **kwargs: Any) -> Any:
        self.spawned.append(kwargs)
        index = len(self.spawned) - 1
        state = self.spawn_states[min(index, len(self.spawn_states) - 1)]
        return SimpleNamespace(
            record=SimpleNamespace(
                id=f"pane-{index + 2}",
                state=state,
                exit_code=1 if state == "crashed" else None,
                agent_run_id=None,
                native_session_id=kwargs.get("resume_native_id"),
                snapshot=lambda: {},
            )
        )

    async def _stop(self, sid: str) -> None:
        self.stopped.append(sid)

    async def _update(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    async def points(self) -> dict[str, Any]:
        response = await session_branch_points(cast(Any, self.request))
        return cast(dict[str, Any], json.loads(response.body))

    async def run(self) -> Any:
        return await branch_session(cast(Any, self.request))

    def forked_records(self) -> list[dict[str, Any]]:
        conversation = self.spawned[-1]["resume_native_id"]
        return read_records(self.transcripts / f"{conversation}.jsonl")


async def test_branch_points_offer_both_cuts_and_say_which_are_available(
    tmp_path: Path,
) -> None:
    """Eligibility is per point, not per harness.

    The same conversation offers a legal cut after one reply and an illegal one after
    the next, purely because the second asked for a tool whose result had not yet
    arrived. A picker that could not say which is which would offer a branch that
    writes a conversation the provider rejects.
    """
    harness = BranchHarness(tmp_path)
    payload = await harness.points()

    assert payload["from_message"] is True
    assert payload["strategy"] == "transcript_fork"
    roles = [point["role"] for point in payload["points"]]
    assert roles == ["user", "assistant", "assistant", "user", "assistant"]
    # A user message is a thing to redo; an agent message is a thing to continue from.
    assert [point["default_mode"] for point in payload["points"]] == [
        "before", "after", "after", "before", "after",
    ]
    calling, answering = payload["points"][1], payload["points"][2]
    assert calling["modes"]["after"] == {"eligible": False, "reason": "unanswered_tool_calls"}
    assert answering["modes"]["after"] == {"eligible": True, "reason": None}
    # Nothing precedes the oldest point in the window, so there is no record to cut
    # after, and "before" is unavailable there rather than approximated with byte zero.
    assert payload["points"][0]["modes"]["before"]["reason"] == "outside_window"
    # `before` is answered from the *preceding* message, because that is the record
    # the cut lands on. The message after the tool-calling reply therefore reports the
    # same refusal the request would give, rather than offering a cut it would reject.
    assert payload["points"][2]["modes"]["before"] == {
        "eligible": False,
        "reason": "unanswered_tool_calls",
    }


async def test_branch_points_carry_the_words_each_point_is_recognised_by(
    tmp_path: Path,
) -> None:
    payload = await BranchHarness(tmp_path).points()
    assert [point["text"] for point in payload["points"]] == [
        "first prompt",
        "looking into it",
        "first answer",
        "second prompt",
        "second answer",
    ]


async def test_branching_before_a_prompt_replays_the_moment_it_was_about_to_be_sent(
    tmp_path: Path,
) -> None:
    """The whole point of the feature: the side quest is gone and the prompt comes back.

    The forked conversation ends at the reply that preceded the prompt, and the
    prompt's own text is handed back so it can be sent differently rather than
    retyped from memory.
    """
    harness = BranchHarness(tmp_path, body={})
    points = await harness.points()
    second_prompt = points["points"][3]
    harness._request_body = {"from_message_id": second_prompt["message_id"], "mode": "before"}

    response = await harness.run()

    assert response.status == 201
    body = json.loads(response.body)
    assert body["seed_text"] == "second prompt"
    assert body["fork"]["mode"] == "before"
    texts = [
        record["message"]["content"]
        for record in harness.forked_records()
        if record.get("type") == "user" and isinstance(record["message"]["content"], str)
    ]
    assert texts == ["first prompt"]


async def test_the_source_pane_is_not_touched_by_a_transcript_fork(tmp_path: Path) -> None:
    """Nothing is typed, nothing is rolled, and the source file is byte-identical.

    This is the difference from the CLI-mediated branch it replaces, which typed a
    slash command into a terminal the operator was holding and moved that pane onto a
    different conversation.
    """
    harness = BranchHarness(tmp_path, body={})
    before = harness.source_path.read_bytes()

    response = await harness.run()

    assert response.status == 201
    assert harness.source_path.read_bytes() == before
    assert harness.record.native_session_id == harness.original
    assert harness.record.agent_run_id == "run-original"
    # A fork resumes a conversation nothing has ever opened, so the sibling is a new
    # conversation with its own row rather than an inheritor of the source's run.
    assert harness.spawned[0]["resume_native_id"] != harness.original
    assert "adopt_run_id" not in harness.spawned[0]


@pytest.mark.parametrize("state", ["working", "awaiting", "crashed", "exited"])
async def test_a_transcript_fork_does_not_care_what_the_pane_is_doing(
    tmp_path: Path, state: str
) -> None:
    """The readiness gate belonged to typing into a terminal, and there is no typing.

    A pane mid-turn, waiting on an approval, or already exited forks exactly as well
    as an idle one, because the fork is a file the daemon writes rather than a request
    the CLI has to be in a state to answer.
    """
    harness = BranchHarness(tmp_path, body={})
    harness.record.state = state
    harness.session.composer = SimpleNamespace(pending=True)

    response = await harness.run()

    assert response.status == 201


async def test_a_point_with_an_unanswered_tool_call_is_refused(tmp_path: Path) -> None:
    harness = BranchHarness(tmp_path, body={})
    points = await harness.points()
    harness._request_body = {"from_message_id": points["points"][1]["message_id"], "mode": "after"}

    response = await harness.run()

    assert response.status == 409
    assert json.loads(response.body)["code"] == "unanswered_tool_calls"
    assert harness.spawned == []


async def test_a_branch_with_no_point_forks_from_the_end(tmp_path: Path) -> None:
    """The rail's one-click branch stays one click: no point means the latest one."""
    harness = BranchHarness(tmp_path, body={})

    response = await harness.run()

    assert response.status == 201
    fork = json.loads(response.body)["fork"]
    assert fork["mode"] == "after"
    assert len(harness.forked_records()) == len(read_records(harness.source_path)) - 1


async def test_the_fork_owns_its_sidecar_files(tmp_path: Path) -> None:
    harness = BranchHarness(tmp_path, body={})
    response = await harness.run()
    assert response.status == 201
    conversation = harness.spawned[0]["resume_native_id"]
    assert (harness.transcripts / conversation / SIDECAR_NAME).is_file()
    assert json.loads(response.body)["fork"]["attachments_copied"] == 1


async def test_a_branch_records_where_it_was_cut(tmp_path: Path) -> None:
    """The fork point outlives the request that made it.

    Without the edge, a branch is indistinguishable from two unrelated conversations
    that happen to share a prefix, and nothing can draw the tree.
    """
    harness = BranchHarness(tmp_path, body={})
    points = await harness.points()
    chosen = points["points"][2]
    harness._request_body = {"from_message_id": chosen["message_id"], "mode": "after"}

    await harness.run()

    assert len(harness.store.edges) == 1
    edge = harness.store.edges[0]
    assert edge["parent_run_id"] == "run-original"
    assert edge["relation"] == "branch"
    assert edge["metadata"]["from_message_id"] == chosen["message_id"]
    assert edge["metadata"]["mode"] == "after"
    assert edge["metadata"]["source_conversation_id"] == harness.original


async def test_a_branch_is_named_after_the_conversation_it_came_from(tmp_path: Path) -> None:
    """The source's **display** name, not its `name` field.

    Those differ for exactly the sessions worth branching: a session nobody renamed
    shows its generated title while `name` is still the spawn default, so reading the
    raw field called the branch `claude-6vried branch` for a conversation the operator
    knows as "Update ABC".
    """
    harness = BranchHarness(tmp_path, body={}, titles={"run-original": "Update ABC"})

    response = await harness.run()

    assert response.status == 201
    assert harness.spawned[0]["name"] == "B1-Update ABC"


async def test_a_renamed_conversation_keeps_the_name_its_owner_chose(tmp_path: Path) -> None:
    """A rename outranks a generated title, and the branch inherits the rename."""
    harness = BranchHarness(tmp_path, body={}, titles={"run-original": "Update ABC"})
    harness.record.auto_named = False
    harness.record.name = "device ownership"

    await harness.run()

    assert harness.spawned[0]["name"] == "B1-device ownership"


async def test_each_branch_of_one_conversation_gets_its_own_number(tmp_path: Path) -> None:
    harness = BranchHarness(tmp_path, body={}, titles={"run-original": "Update ABC"})
    await harness.run()
    await harness.run()
    assert [item["name"] for item in harness.spawned] == ["B1-Update ABC", "B2-Update ABC"]


async def test_branching_a_branch_replaces_the_number_rather_than_stacking_it(
    tmp_path: Path,
) -> None:
    """Otherwise a tree three deep is called `B1-B2-B1-Update ABC`.

    The subject is what the operator recognises; only the newest ordinal is worth the
    width, and the daemon never reads the name back.
    """
    harness = BranchHarness(tmp_path, body={}, titles={"run-original": "B2-Update ABC"})
    await harness.run()
    assert harness.spawned[0]["name"] == "B1-Update ABC"


async def test_an_explicit_name_still_wins(tmp_path: Path) -> None:
    harness = BranchHarness(
        tmp_path, body={"name": "spike"}, titles={"run-original": "Update ABC"}
    )
    await harness.run()
    assert harness.spawned[0]["name"] == "spike"


async def test_a_sibling_that_never_comes_up_is_removed_and_named(tmp_path: Path) -> None:
    """A pane that will not stay up is removed and reported, not attached and left grey."""
    harness = BranchHarness(tmp_path, body={})
    harness.spawn_states = ["crashed"]

    response = await harness.run()

    assert response.status == 503
    body = json.loads(response.body)
    assert body["code"] == "branch_sibling_failed"
    # A fork resumes a conversation nothing else has ever opened, so there is no
    # release to race and no attempt for a retry to be further from.
    assert body["attempts"] == 1
    assert harness.stopped == ["pane-2"]
    assert harness.store.edges == []


async def test_the_branch_event_says_where_the_cut_landed(tmp_path: Path) -> None:
    harness = BranchHarness(tmp_path, body={})
    await harness.run()
    assert [name for name, _ in harness.bus.emitted] == ["session_branched"]
    payload = harness.bus.emitted[0][1]
    assert payload["strategy"] == "transcript_fork"
    assert payload["original"] == harness.original
    assert payload["mode"] == "after"
    assert payload["records_written"] > 0


async def test_a_harness_that_can_only_fork_from_now_refuses_a_point(tmp_path: Path) -> None:
    """Silently ignoring the point would present a choice the daemon does not honour."""
    harness = BranchHarness(tmp_path, body={"from_message_id": "offset:0"})
    harness.record.backend = "codex"
    harness.record.native_session_id = "01a00602-e70a-7a01-88e1-2fbebd4300ee"
    harness.session.agent_lifecycle_id = harness.record.native_session_id

    response = await harness.run()

    assert response.status == 422
    assert json.loads(response.body)["code"] == "branch_point_unsupported"


async def test_a_harness_with_no_points_says_so_rather_than_offering_none(
    tmp_path: Path,
) -> None:
    harness = BranchHarness(tmp_path)
    harness.record.backend = "codex"
    harness.record.native_session_id = "01a00602-e70a-7a01-88e1-2fbebd4300ee"
    harness.session.agent_lifecycle_id = harness.record.native_session_id

    payload = await harness.points()

    assert payload["from_message"] is False
    assert payload["reason"] == "strategy_has_no_points"
    assert payload["points"] == []
