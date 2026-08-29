"""Approving a drafted spawn starts the session the card described.

The failure this pins is the quiet one at the end of a long chain: a field
accepted on the MCP call, stored on the row, rendered on the card, and then
dropped by the approval handler. The human agrees to "an opus session", an
ordinary one starts, and nothing anywhere reports a difference - the approval is
the only step where a dropped field is invisible to *both* parties, because the
agent that asked has moved on and the human has no reason to re-check.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from swe_mux import app_keys as keys
from swe_mux.config import Config
from swe_mux.models import ProjectRecord
from swe_mux.project_files import append_observation, read_observations
from swe_mux.routes import observations as observation_routes
from swe_mux.routes import sessions as session_routes


class _Events:
    def __init__(self) -> None:
        self.emitted: list[tuple[str, dict[str, Any]]] = []

    async def emit(self, name: str, **payload: Any) -> None:
        self.emitted.append((name, payload))


def _request(root: Path, observation_id: str, project: ProjectRecord) -> Any:
    async def json_body() -> dict[str, Any]:
        return {"decision": "approve"}

    return SimpleNamespace(
        app={
            keys.PROJECTS: SimpleNamespace(projects={"p1": project}),
            keys.CONFIG: Config(data_dir=root / "data"),
            keys.EVENTS: _Events(),
        },
        match_info={"project_id": "p1", "observation_id": observation_id},
        json=json_body,
        headers={},
        # Sender provenance is read off the transport rather than the body, so a
        # stub has to have one. `None` is the local-console case.
        transport=None,
    )


async def _draft(root: Path, project: ProjectRecord, **fields: Any) -> str:
    result = await append_observation(
        root,
        "Spawn request from worker",
        kind="spawn_request",
        request={"status": "pending", "from_session": "s1", **fields},
    )
    return str(result["appended_id"])


@pytest.mark.asyncio
async def test_approving_starts_the_session_on_the_model_the_card_promised(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "work"
    root.mkdir()
    project = ProjectRecord(id="p1", name="Work", root=str(root), position=0)
    observation_id = await _draft(
        root, project, prompt="run the migration", backend="claude", model="claude-opus-5"
    )
    bodies: list[dict[str, Any]] = []

    async def fake_spawn(_app: Any, body: dict[str, Any]) -> Any:
        bodies.append(body)
        return SimpleNamespace(
            record=SimpleNamespace(id="new-1", snapshot=lambda: {"id": "new-1"})
        )

    monkeypatch.setattr(session_routes, "_spawn_from_body", fake_spawn)
    await observation_routes.decide_observation_request(
        cast(Any, _request(root, observation_id, project))
    )
    assert bodies[0]["model"] == "claude-opus-5"
    assert bodies[0]["seed_text"] == "run the migration"
    stored = await read_observations(root)
    assert stored["observations"][0]["request"]["status"] == "approved"


@pytest.mark.asyncio
async def test_approving_a_request_that_named_no_model_carries_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The key is absent rather than empty: `--model ''` on a command line is a
    worse outcome than the default the Project already resolves to."""
    root = tmp_path / "work"
    root.mkdir()
    project = ProjectRecord(id="p1", name="Work", root=str(root), position=0)
    observation_id = await _draft(root, project, prompt="run it", backend="claude")
    bodies: list[dict[str, Any]] = []

    async def fake_spawn(_app: Any, body: dict[str, Any]) -> Any:
        bodies.append(body)
        return SimpleNamespace(
            record=SimpleNamespace(id="new-1", snapshot=lambda: {"id": "new-1"})
        )

    monkeypatch.setattr(session_routes, "_spawn_from_body", fake_spawn)
    await observation_routes.decide_observation_request(
        cast(Any, _request(root, observation_id, project))
    )
    assert "model" not in bodies[0]
