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
    async def fake_graph(
        project_id: str,
        cwd: str,
        limit: int,
        *,
        grep: str = "",
        author: str = "",
        regex: bool = False,
    ) -> dict[str, object]:
        assert project_id == "p"
        assert cwd == "C:/repo"
        assert limit == 80
        # No search asked for, so none is passed on: an empty `--grep` would ask Git to
        # match every commit against nothing and would silently drop the lane drawing.
        assert (grep, author, regex) == ("", "", False)
        return {"lines": [], "limit": limit, "has_more": False, "filtered": False}

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
    assert json.loads(response.body) == {
        "lines": [],
        "limit": 80,
        "has_more": False,
        "filtered": False,
    }


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


def _overview_request(query: dict[str, str], headers: dict[str, str] | None = None) -> object:
    return SimpleNamespace(
        query=query,
        headers=headers or {},
        app={
            "projects": SimpleNamespace(
                projects={
                    "p": SimpleNamespace(id="p", root="C:/repo", git_compare_ref=None)
                }
            )
        },
    )


@pytest.mark.asyncio
async def test_the_overview_is_conditional_and_answers_304_to_its_own_etag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The first conditional request in this daemon, and the one that earns it.

    Every client refetches this on any session's five-second dirty tick, and the great
    majority of those answers are byte-identical to the one the client already holds.
    """

    async def overview(*args: object, **kwargs: object) -> dict[str, object]:
        del args, kwargs
        return {"worktrees": [], "detail": "full"}

    monkeypatch.setattr(server.git_review, "shared_worktree_overview", overview)
    first = await server.list_worktrees(_overview_request({"project_id": "p"}))
    assert first.status == 200
    etag = first.headers["ETag"]
    # `no-cache` is "revalidate before every use", not "do not store": without it a
    # browser never sends `If-None-Match` at all and the conditional never happens.
    assert first.headers["Cache-Control"] == "no-cache"

    again = await server.list_worktrees(
        _overview_request({"project_id": "p"}, {"If-None-Match": etag})
    )
    assert again.status == 304
    assert again.headers["ETag"] == etag
    # Weak comparison, because the tag is weak: a client library that strips `W/` names
    # the same reading and must not silently stop matching.
    stripped = await server.list_worktrees(
        _overview_request({"project_id": "p"}, {"If-None-Match": etag.removeprefix("W/")})
    )
    assert stripped.status == 304


@pytest.mark.asyncio
async def test_the_two_readings_do_not_share_an_etag(monkeypatch: pytest.MonkeyPatch) -> None:
    """A summary and a full reading are different answers and must not collide."""

    async def overview(*args: object, **kwargs: object) -> dict[str, object]:
        del args, kwargs
        return {
            "worktrees": [
                {
                    "worktree": "C:/repo",
                    "unstaged": {
                        "total": 1,
                        "additions": 1,
                        "deletions": 0,
                        "binary_files": 0,
                        "files": [{"path": "a.txt", "status": "M"}],
                        "truncated": False,
                    },
                }
            ],
            "detail": "full",
        }

    monkeypatch.setattr(server.git_review, "shared_worktree_overview", overview)
    full = await server.list_worktrees(_overview_request({"project_id": "p"}))
    summary = await server.list_worktrees(
        _overview_request({"project_id": "p", "detail": "summary"})
    )
    assert full.headers["ETag"] != summary.headers["ETag"]
    body = json.loads(summary.body)
    assert body["detail"] == "summary"
    assert body["worktrees"][0]["unstaged"]["files"] == []
    assert body["worktrees"][0]["unstaged"]["files_omitted"] is True
    # A client holding the summary must not be told the full reading is unchanged.
    crossed = await server.list_worktrees(
        _overview_request({"project_id": "p"}, {"If-None-Match": summary.headers["ETag"]})
    )
    assert crossed.status == 200


@pytest.mark.asyncio
async def test_an_unknown_detail_is_refused_before_git_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def unexpected(*args: object, **kwargs: object) -> dict[str, object]:
        del args, kwargs
        raise AssertionError("overview must reject the request before Git runs")

    monkeypatch.setattr(server.git_review, "shared_worktree_overview", unexpected)
    with pytest.raises(git_review.GitReviewError) as error:
        await server.list_worktrees(_overview_request({"project_id": "p", "detail": "brief"}))
    assert error.value.code == "invalid_parameters"


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
