from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from swe_mux.project_context import (
    MAX_PROJECT_CONTEXT_BYTES,
    PROJECT_CONTEXT_PATH,
    ProjectContext,
    ProjectContextService,
)
from swe_mux.server import get_project_context, put_project_context


def context(root: Path) -> ProjectContext:
    return ProjectContext(project_id="project-1", project_root=str(root))


def test_project_context_starts_missing_and_is_created_blank(tmp_path: Path) -> None:
    service = ProjectContextService(resolve_session=None)
    missing = service.read(context(tmp_path))
    assert missing == {
        "project_id": "project-1",
        "path": PROJECT_CONTEXT_PATH,
        "exists": False,
        "revision": "missing",
        "markdown": "",
        "max_bytes": MAX_PROJECT_CONTEXT_BYTES,
        "generation_prompt": missing["generation_prompt"],
    }
    created = service.ensure(context(tmp_path))
    assert created["exists"] is True
    assert created["markdown"] == ""
    assert (tmp_path / PROJECT_CONTEXT_PATH).is_file()


def test_project_context_write_is_revision_checked_and_normalizes_newlines(
    tmp_path: Path,
) -> None:
    service = ProjectContextService(resolve_session=None)
    missing = service.read(context(tmp_path))
    saved = service.write(context(tmp_path), "# Project\r\n\r\nContext\r\n", missing["revision"])
    assert saved["markdown"] == "# Project\n\nContext\n"
    with pytest.raises(ValueError, match="changed externally"):
        service.write(context(tmp_path), "stale", missing["revision"])


def test_project_context_rejects_oversized_or_unsafe_storage(tmp_path: Path) -> None:
    service = ProjectContextService(resolve_session=None)
    with pytest.raises(ValueError, match="exceeds"):
        service.write(context(tmp_path), "x" * (MAX_PROJECT_CONTEXT_BYTES + 1), "missing")
    control = tmp_path / ".swe-mux"
    control.write_text("not a directory", encoding="utf-8")
    with pytest.raises(ValueError, match="unsafe"):
        service.read(context(tmp_path))


def test_existing_invalid_context_does_not_block_timeline_enablement(
    tmp_path: Path,
) -> None:
    target = tmp_path / PROJECT_CONTEXT_PATH
    target.parent.mkdir(parents=True)
    target.write_bytes(b"x" * (MAX_PROJECT_CONTEXT_BYTES + 1))
    service = ProjectContextService(resolve_session=None)
    result = service.ensure(context(tmp_path))
    assert result["exists"] is True
    assert result["revision"] == "unavailable"
    assert "exceeds" in result["error"]


@pytest.mark.asyncio
async def test_prompt_prefix_reads_only_the_project_context_file(tmp_path: Path) -> None:
    async def resolve(_session_id: str) -> ProjectContext:
        return context(tmp_path)

    (tmp_path / "README.md").write_text("do not harvest me", encoding="utf-8")
    service = ProjectContextService(resolve_session=resolve)
    assert await service.prompt_prefix("session-1") == ""
    service.write(context(tmp_path), "user context", "missing")
    assert await service.prompt_prefix("session-1") == "user context"


@pytest.mark.asyncio
async def test_project_context_http_surface_round_trips_the_fixed_file(tmp_path: Path) -> None:
    async def resolve(_session_id: str) -> None:
        return None

    project = SimpleNamespace(id="project-1", name="Project", root=str(tmp_path))
    service = ProjectContextService(resolve_session=resolve)
    emitted: list[str] = []

    class Events:
        async def emit(self, event: str, **_payload: object) -> None:
            emitted.append(event)

    def request(body: dict[str, str] | None = None) -> SimpleNamespace:
        async def payload() -> dict[str, str]:
            return body or {}

        return SimpleNamespace(
            match_info={"project_id": project.id},
            app={
                "projects": SimpleNamespace(projects={project.id: project}),
                "project_contexts": service,
                "events": Events(),
            },
            json=payload,
        )

    initial = json.loads((await get_project_context(request())).body)
    assert initial["revision"] == "missing"
    saved = json.loads(
        (
            await put_project_context(
                request({"markdown": "# Context\n", "revision": initial["revision"]})
            )
        ).body
    )
    assert saved["markdown"] == "# Context\n"
    assert emitted == ["project_context_changed"]
