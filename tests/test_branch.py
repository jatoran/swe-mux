from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any, cast

from swe_mux.server import branch_session


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
