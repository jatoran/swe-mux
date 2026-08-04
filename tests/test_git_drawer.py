"""Repository-map file summaries and the read-only commit graph."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from swe_mux import server


def test_porcelain_v2_working_tree_parser_keeps_status_and_rename_source() -> None:
    output = (
        "1 .M N... 100644 100644 100644 aaaaaaa bbbbbbb frontend/src/GitTab.tsx\0"
        "? frontend/src/new.ts\0"
        "2 R. N... 100644 100644 100644 aaaaaaa bbbbbbb R100 new-name.ts\0"
        "old-name.ts\0"
    )
    assert server._parse_working_tree_changes(output) == [
        {"status": ".M", "path": "frontend/src/GitTab.tsx"},
        {"status": "??", "path": "frontend/src/new.ts"},
        {"status": "R.", "path": "new-name.ts", "old_path": "old-name.ts"},
    ]


def test_name_status_parser_keeps_branch_rename_direction() -> None:
    output = "M\0one.ts\0R100\0old-name.ts\0new-name.ts\0D\0gone.ts\0"
    assert server._parse_branch_changes(output) == [
        {"status": "M", "path": "one.ts"},
        {"status": "R100", "path": "new-name.ts", "old_path": "old-name.ts"},
        {"status": "D", "path": "gone.ts"},
    ]


@pytest.mark.asyncio
async def test_every_worktree_gets_local_and_trunk_relative_file_summaries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_git(cwd: str, *args: str) -> tuple[int, str]:
        if args[0] == "show-ref":
            return 0, ""
        if args[0] == "status":
            if cwd == "C:/repo":
                return 0, "? local.txt\0"
            return 0, ""
        if args[0] == "diff":
            if args[-1].endswith("refs/heads/agent/map"):
                return 0, "M\0frontend/src/GitTab.tsx\0A\0frontend/src/GitGraph.tsx\0"
            return 0, ""
        raise AssertionError((cwd, args))

    monkeypatch.setattr(server, "_git", fake_git)
    items: list[dict[str, Any]] = [
        {"worktree": "C:/repo", "branch": "refs/heads/master"},
        {"worktree": "C:/wt/map", "branch": "refs/heads/agent/map"},
        {"worktree": "C:/bare", "bare": True},
    ]
    await server._annotate_worktree_changes("C:/repo", "master", items)

    assert items[0]["working_tree"]["total"] == 1
    assert items[0]["branch_delta"]["total"] == 0
    assert items[1]["working_tree"]["total"] == 0
    assert items[1]["branch_delta"]["total"] == 2
    assert items[1]["branch_delta"]["files"][1]["path"] == "frontend/src/GitGraph.tsx"
    assert "working_tree" not in items[2]


@pytest.mark.asyncio
async def test_missing_trunk_does_not_hide_local_files_or_claim_a_branch_delta(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_git(cwd: str, *args: str) -> tuple[int, str]:
        del cwd
        if args[0] == "show-ref":
            return 1, ""
        if args[0] == "status":
            return 0, "? local.txt\0"
        raise AssertionError(args)

    monkeypatch.setattr(server, "_git", fake_git)
    items: list[dict[str, Any]] = [
        {"worktree": "C:/repo", "branch": "refs/heads/agent/map"}
    ]
    await server._annotate_worktree_changes("C:/repo", "master", items)
    assert items[0]["working_tree"]["total"] == 1
    assert "branch_delta" not in items[0]


def test_graph_parser_keeps_git_lanes_refs_and_connector_rows() -> None:
    first = "\0".join(
        [
            "*   ",
            "a" * 40,
            f"{'b' * 40} {'c' * 40}",
            "HEAD -> master, tag: v1",
            "Ada",
            "1700000000",
            "Merge the map",
        ]
    )
    second = "\0".join(
        [
            "| * ",
            "b" * 40,
            "d" * 40,
            "agent/map",
            "Lin",
            "1690000000",
            "Draw the graph",
        ]
    )
    lines, has_more = server._parse_graph_lines(
        f"{first}\n|\\  \n{second}\n|/\n", limit=2
    )
    assert has_more is False
    assert [line["kind"] for line in lines] == [
        "commit",
        "connector",
        "commit",
        "connector",
    ]
    assert lines[0]["refs"] == ["HEAD", "master", "tag: v1"]
    assert lines[0]["parents"] == ["b" * 40, "c" * 40]
    assert lines[1]["graph"] == "|\\  "


def test_graph_parser_uses_one_extra_commit_only_as_the_more_marker() -> None:
    def record(graph: str, oid: str) -> str:
        return "\0".join([graph, oid, "", "", "Ada", "1700000000", oid])

    lines, has_more = server._parse_graph_lines(
        f"{record('* ', 'one')}\n| \n{record('* ', 'two')}\n", limit=1
    )
    assert has_more is True
    assert [line["oid"] for line in lines if line["kind"] == "commit"] == ["one"]


@pytest.mark.asyncio
async def test_empty_repository_has_an_empty_graph_not_an_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_git(cwd: str, *args: str) -> tuple[int, str]:
        del cwd
        assert args == ("rev-list", "--all", "--max-count=1")
        return 0, ""

    monkeypatch.setattr(server, "_git", fake_git)
    response = await server.git_graph(
        SimpleNamespace(query={"cwd": "C:/repo", "limit": "80"})
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

    monkeypatch.setattr(server, "_git", unexpected)
    response = await server.git_graph(
        SimpleNamespace(query={"cwd": "C:/repo", "limit": "201"})
    )
    assert response.status == 400
