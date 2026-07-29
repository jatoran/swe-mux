"""Unlanded-commit counts for worktree rows.

The point of this measurement is that unlanded agent work should be *visible* rather than
something you have to remember to check. That makes the failure mode asymmetric: reporting
zero when we could not measure reads as "nothing at risk" and is far worse than reporting
nothing at all, so every unmeasurable case must yield an absent count, not a zero.
"""

from __future__ import annotations

from typing import Any

import pytest

from swe_mux import server


class FakeGit:
    """Records argv and replays canned (code, output) pairs keyed by subcommand."""

    def __init__(self, **replies: tuple[int, str]) -> None:
        self.replies = replies
        self.calls: list[tuple[str, ...]] = []

    async def __call__(self, cwd: str, *args: str) -> tuple[int, str]:
        self.calls.append(args)
        return self.replies.get(args[0], (0, ""))


AHEAD_BEHIND = (
    0,
    "refs/heads/master 0 0\n"
    "refs/heads/integration 0 0\n"
    "refs/heads/agent/done 0 4\n"
    "refs/heads/agent/busy 3 1\n",
)


@pytest.mark.asyncio
async def test_counts_report_commits_the_trunk_does_not_have(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeGit(**{"show-ref": (0, ""), "for-each-ref": AHEAD_BEHIND})
    monkeypatch.setattr(server, "_git", fake)
    counts = await server.unlanded_branch_counts("C:/repo")
    assert counts["refs/heads/agent/busy"] == 3
    assert counts["refs/heads/agent/done"] == 0
    assert counts["refs/heads/master"] == 0


@pytest.mark.asyncio
async def test_a_missing_trunk_yields_no_counts_rather_than_zeros(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeGit(**{"show-ref": (1, ""), "for-each-ref": AHEAD_BEHIND})
    monkeypatch.setattr(server, "_git", fake)
    assert await server.unlanded_branch_counts("C:/repo") == {}
    # Cheap guard first: never pay for for-each-ref when the trunk does not exist.
    assert not any(call[0] == "for-each-ref" for call in fake.calls)


@pytest.mark.asyncio
async def test_a_failed_for_each_ref_yields_no_counts(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeGit(**{"show-ref": (0, ""), "for-each-ref": (128, "fatal: bad revision")})
    monkeypatch.setattr(server, "_git", fake)
    assert await server.unlanded_branch_counts("C:/repo") == {}


@pytest.mark.asyncio
async def test_a_timeout_yields_no_counts(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeGit(**{"show-ref": (0, ""), "for-each-ref": (124, "")})
    monkeypatch.setattr(server, "_git", fake)
    assert await server.unlanded_branch_counts("C:/repo") == {}


@pytest.mark.asyncio
async def test_unresolved_atom_lines_are_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    # Older git, or a ref the atom cannot compare, emits the refname with no numbers.
    fake = FakeGit(
        **{
            "show-ref": (0, ""),
            "for-each-ref": (0, "refs/heads/agent/a\nrefs/heads/agent/b 2 0\n"),
        }
    )
    monkeypatch.setattr(server, "_git", fake)
    counts = await server.unlanded_branch_counts("C:/repo")
    assert counts == {"refs/heads/agent/b": 2}


@pytest.mark.asyncio
async def test_an_unsafe_trunk_name_is_refused_without_running_git(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeGit(**{"show-ref": (0, ""), "for-each-ref": AHEAD_BEHIND})
    monkeypatch.setattr(server, "_git", fake)
    for hostile in ("--upload-pack=evil", "a b", "x;y", "", "..\\..\\etc"):
        assert await server.unlanded_branch_counts("C:/repo", hostile) == {}
    assert fake.calls == []


@pytest.mark.asyncio
async def test_rows_are_annotated_only_where_the_branch_was_measured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeGit(**{"show-ref": (0, ""), "for-each-ref": AHEAD_BEHIND})
    monkeypatch.setattr(server, "_git", fake)
    items: list[dict[str, Any]] = [
        {"worktree": "C:/repo", "branch": "refs/heads/master"},
        {"worktree": "C:/wt/busy", "branch": "refs/heads/agent/busy"},
        {"worktree": "C:/wt/done", "branch": "refs/heads/agent/done"},
        {"worktree": "C:/wt/detached", "detached": True},
        {"worktree": "C:/wt/unknown", "branch": "refs/heads/agent/never-heard-of"},
    ]
    await server._annotate_unlanded("C:/repo", server.DEFAULT_AGENT_TRUNK, items)
    assert items[0]["unlanded"] == 0
    assert items[1]["unlanded"] == 3
    assert items[2]["unlanded"] == 0
    assert "unlanded" not in items[3], "a detached worktree has no branch to measure"
    assert "unlanded" not in items[4], "an unmeasured branch must not be reported as 0"


@pytest.mark.asyncio
async def test_no_git_call_when_nothing_has_a_branch(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeGit(**{"show-ref": (0, ""), "for-each-ref": AHEAD_BEHIND})
    monkeypatch.setattr(server, "_git", fake)
    items: list[dict[str, Any]] = [{"worktree": "C:/wt/a", "detached": True}]
    await server._annotate_unlanded("C:/repo", server.DEFAULT_AGENT_TRUNK, items)
    assert fake.calls == []
