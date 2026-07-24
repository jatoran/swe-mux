from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any, cast

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
