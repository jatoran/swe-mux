"""Repository-map file summaries and the read-only commit graph."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from swe_mux import git_review, server


@pytest.mark.asyncio
async def test_empty_repository_has_an_empty_graph_not_an_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_graph(project_id: str, cwd: str, limit: int) -> dict[str, object]:
        assert project_id == "p"
        assert cwd == "C:/repo"
        assert limit == 80
        return {"lines": [], "limit": limit, "has_more": False}

    monkeypatch.setattr(server.git_review, "git_graph", fake_graph)
    response = await server.git_graph(
        SimpleNamespace(
            query={"project_id": "p", "limit": "80"},
            app={
                "projects": SimpleNamespace(
                    projects={"p": SimpleNamespace(id="p", root="C:/repo")}
                )
            },
        )
    )
    assert response.status == 200
    assert json.loads(response.body) == {"lines": [], "limit": 80, "has_more": False}


@pytest.mark.asyncio
async def test_graph_limit_is_bounded_before_git_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def unexpected(*args: object, **kwargs: object) -> tuple[int, str]:
        del args, kwargs
        raise AssertionError("Git must not run for an invalid limit")

    monkeypatch.setattr(server.git_review, "git_graph", unexpected)
    response = await server.git_graph(
        SimpleNamespace(
            query={"project_id": "p", "limit": "201"},
            app={
                "projects": SimpleNamespace(
                    projects={"p": SimpleNamespace(id="p", root="C:/repo")}
                )
            },
        )
    )
    assert response.status == 400


@pytest.mark.asyncio
@pytest.mark.parametrize("extra", ["cwd", "ref", "path", "parent"])
async def test_overview_rejects_browser_supplied_git_parameters(
    extra: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def unexpected(*args: object, **kwargs: object) -> dict[str, object]:
        del args, kwargs
        raise AssertionError("overview must reject the request before Git runs")

    monkeypatch.setattr(server.git_review, "worktree_overview", unexpected)
    request = SimpleNamespace(
        query={"project_id": "p", extra: "injected"},
        app={
            "projects": SimpleNamespace(
                projects={
                    "p": SimpleNamespace(
                        id="p", root="C:/repo", git_compare_ref=None
                    )
                }
            )
        },
    )
    with pytest.raises(git_review.GitReviewError) as error:
        await server.list_worktrees(request)
    assert error.value.code == "invalid_parameters"


@pytest.mark.asyncio
async def test_diff_rejects_extraneous_parameters_before_delegating(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def unexpected(*args: object, **kwargs: object) -> dict[str, object]:
        del args, kwargs
        raise AssertionError("diff must reject the request before Git runs")

    monkeypatch.setattr(server.git_review, "patch_snapshot", unexpected)
    request = SimpleNamespace(
        query={
            "project_id": "p",
            "scope": "unstaged",
            "worktree": "C:/repo",
            "path": "tracked.txt",
            "cwd": "C:/other",
        },
        app={
            "projects": SimpleNamespace(
                projects={
                    "p": SimpleNamespace(
                        id="p", root="C:/repo", git_compare_ref=None
                    )
                }
            )
        },
    )
    with pytest.raises(git_review.GitReviewError) as error:
        await server.git_diff(request)
    assert error.value.code == "invalid_parameters"
