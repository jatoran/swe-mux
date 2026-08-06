from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from swe_mux.adapters import ClaudeAdapter
from swe_mux.server import _branch_source_id, branch_session


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
        project_id="default", name="shell", cwd=".",
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


async def test_a_claude_branch_hands_the_original_conversation_to_the_sibling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The sibling continues the original conversation, so it inherits its run.

    `/branch` moves the *source* pane onto a fresh conversation and freezes the
    original, which the sibling then reopens. Opening a second row there showed one
    conversation as two entries over one file. The inheritance is only sound once the
    source pane has let go of the run, so the confirmed fork id is applied to it
    first.
    """
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    adapter = ClaudeAdapter("claude.exe")
    cwd = tmp_path / "project"
    cwd.mkdir()
    original = "aaaaaaaa-1111-4a7b-8c9d-0e1f2a3b4c5d"
    forked = "cccccccc-3333-4a7b-8c9d-0e1f2a3b4c5d"
    transcripts = adapter.transcript_path(original, cwd).parent
    transcripts.mkdir(parents=True)
    (transcripts / f"{original}.jsonl").write_text("{}\n", encoding="utf-8")
    record = SimpleNamespace(
        id="pane-1", backend="claude", native_session_id=original, agent_run_id="run-original",
        project_id="default", name="device ownership", cwd=str(cwd), run_cwd=str(cwd),
    )
    rolled: list[dict[str, Any]] = []
    spawned: list[dict[str, Any]] = []

    def branch_now(data: str) -> None:
        # What the CLI does with `/branch`: a new transcript, and the original frozen.
        assert data == "/branch\r"
        (transcripts / f"{forked}.jsonl").write_text("{}\n", encoding="utf-8")

    async def roll_agent_conversation(sid: str, **kwargs: Any) -> bool:
        rolled.append({"sid": sid, **kwargs})
        return True

    async def spawn(**kwargs: Any) -> Any:
        spawned.append(kwargs)
        return SimpleNamespace(
            record=SimpleNamespace(id="pane-2", snapshot=lambda: {}),
        )

    async def update(*_args: Any, **_kwargs: Any) -> None:
        return None

    session = SimpleNamespace(
        record=record, agent_lifecycle_id=original, pty=SimpleNamespace(write=branch_now)
    )
    manager = SimpleNamespace(
        sessions={record.id: session},
        resolve=lambda _identity: session,
        adapters={"claude": adapter},
        roll_agent_conversation=roll_agent_conversation,
        spawn=spawn,
    )
    request = SimpleNamespace(
        app={
            "sessions": manager,
            "projects": SimpleNamespace(
                projects={
                    "default": SimpleNamespace(
                        name="Main",
                        root=str(cwd),
                        layout={"version": 2, "root": None},
                        layout_revision=0,
                    )
                },
                update=update,
            ),
        },
        match_info={"sid": record.id},
        can_read_body=False,
    )

    response = await branch_session(cast(Any, request))

    assert response.status == 201
    # The source pane is retired onto the conversation the fork actually created,
    # rather than keeping the original id until some hook happens to report it.
    assert rolled == [
        {"sid": "pane-1", "native_id": forked, "reason": "branched", "source": "branch"}
    ]
    assert spawned[0]["resume_native_id"] == original
    assert spawned[0]["adopt_run_id"] == "run-original"


async def test_branch_rejects_codex_before_native_id_is_known() -> None:
    # Codex's native id is a placeholder equal to the mux id until its first
    # rollout is written; branching then would resume nothing.
    record = SimpleNamespace(
        id="cx1", backend="codex", native_session_id="cx1",
        project_id="default", name="codex", cwd=".",
    )
    response = await branch_session(cast(Any, _request(record)))
    assert response.status == 409
    assert json.loads(response.body)["code"] == "native_id_missing"
