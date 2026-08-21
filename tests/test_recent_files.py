"""Recent-files parsing and folding (`swe_mux.recent_files`).

The Git calls themselves are exercised against a real repository at the bottom; everything
above works on captured Git output, because the ordering rules are what regress.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from swe_mux.recent_files import (
    RecentCandidate,
    merge_candidates,
    parse_log_paths,
    parse_status_paths,
    read_recent_files,
    unavailable,
)


def test_parse_status_reads_code_and_path() -> None:
    entries = parse_status_paths("?? new.txt\0 M src/app.py\0")
    assert [(item.path, item.status, item.origin) for item in entries] == [
        ("new.txt", "??", "working"),
        ("src/app.py", " M", "working"),
    ]
    assert all(item.committed_at is None for item in entries)


def test_parse_status_consumes_a_rename_source() -> None:
    """A rename spends the next record on the old path; only the new one exists."""
    entries = parse_status_paths("R  after.py\0before.py\0?? other.txt\0")
    assert [item.path for item in entries] == ["after.py", "other.txt"]


def test_parse_status_survives_a_path_holding_a_quote() -> None:
    """`-z` disables quoting, which is the whole reason it is used."""
    entries = parse_status_paths('?? odd"name.txt\0')
    assert [item.path for item in entries] == ['odd"name.txt']


def test_parse_status_ignores_short_and_empty_records() -> None:
    assert parse_status_paths("") == []
    assert parse_status_paths("\0 M \0") == []


def test_parse_log_dates_every_path_by_its_commit() -> None:
    payload = "\x02100\na.py\0b.py\0\0\x0250\nc.py\0"
    entries = parse_log_paths(payload)
    assert [(item.path, item.committed_at) for item in entries] == [
        ("a.py", 100.0),
        ("b.py", 100.0),
        ("c.py", 50.0),
    ]
    assert all(item.origin == "committed" for item in entries)


def test_parse_log_keeps_only_the_newest_touch_of_a_path() -> None:
    payload = "\x02200\na.py\0\0\x02100\na.py\0b.py\0"
    entries = parse_log_paths(payload)
    assert [(item.path, item.committed_at) for item in entries] == [
        ("a.py", 200.0),
        ("b.py", 100.0),
    ]


def test_parse_log_tolerates_an_unreadable_timestamp() -> None:
    entries = parse_log_paths("\x02notanumber\na.py\0")
    assert [(item.path, item.committed_at) for item in entries] == [("a.py", None)]


def test_merge_puts_the_working_tree_first() -> None:
    """An uncommitted edit is newer than any commit and carries no timestamp to sort by."""
    working = [RecentCandidate("dirty.py", "working", " M", None)]
    committed = [RecentCandidate("old.py", "committed", None, 100.0)]
    items = merge_candidates(working, committed)
    assert [item["path"] for item in items] == ["dirty.py", "old.py"]
    assert items[0]["origin"] == "working"
    assert items[1]["committed_at"] == 100.0


def test_merge_reports_a_path_once_from_the_working_tree() -> None:
    working = [RecentCandidate("both.py", "working", "M ", None)]
    committed = [RecentCandidate("both.py", "committed", None, 100.0)]
    items = merge_candidates(working, committed)
    assert [(item["path"], item["origin"]) for item in items] == [("both.py", "working")]


def test_merge_applies_the_limit() -> None:
    committed = [RecentCandidate(f"f{index}.py", "committed", None, 1.0) for index in range(50)]
    assert len(merge_candidates([], committed, limit=20)) == 20


def test_merge_re_roots_onto_the_project_and_drops_what_is_outside() -> None:
    """A Project can be a subdirectory of its repository; Git answers in repo coordinates."""
    committed = [
        RecentCandidate("app/src/a.py", "committed", None, 2.0),
        RecentCandidate("other/b.py", "committed", None, 1.0),
        RecentCandidate("app/", "committed", None, 1.0),
    ]
    items = merge_candidates([], committed, prefix="app/")
    assert [item["path"] for item in items] == ["src/a.py"]
    assert items[0]["name"] == "a.py"


def test_merge_honours_ignore_rules_and_existence() -> None:
    committed = [
        RecentCandidate("keep.py", "committed", None, 3.0),
        RecentCandidate("build/out.js", "committed", None, 2.0),
        RecentCandidate("deleted.py", "committed", None, 1.0),
    ]
    items = merge_candidates(
        [],
        committed,
        visible=lambda path: not path.startswith("build/"),
        exists=lambda path: path != "deleted.py",
    )
    assert [item["path"] for item in items] == ["keep.py"]


def test_unavailable_states_a_reason() -> None:
    payload = unavailable("This Project is not inside a Git repository.")
    assert payload["available"] is False
    assert payload["items"] == []
    assert "Git repository" in payload["reason"]


@pytest.mark.asyncio
async def test_read_recent_files_says_so_outside_a_repository(tmp_path: Path) -> None:
    payload = await read_recent_files(tmp_path)
    assert payload["available"] is False
    assert payload["items"] == []
    assert "not inside a Git repository" in payload["reason"]


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


@pytest.mark.asyncio
async def test_read_recent_files_reads_a_real_repository(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "committed.py").write_text("print(1)\n", encoding="utf-8")
    (repo / "ignored.log").write_text("noise\n", encoding="utf-8")
    _git(repo, "add", "committed.py", "ignored.log")
    _git(repo, "commit", "-q", "-m", "first")
    (repo / "dirty.py").write_text("print(2)\n", encoding="utf-8")

    payload = await read_recent_files(repo, ignore_patterns=["*.log"])
    assert payload["available"] is True
    paths = [item["path"] for item in payload["items"]]
    # Untracked-but-present leads; the committed file follows; the ignored one never appears.
    assert paths == ["dirty.py", "committed.py"]
    assert payload["items"][0]["origin"] == "working"
    assert payload["items"][1]["committed_at"] is not None


@pytest.mark.asyncio
async def test_read_recent_files_answers_a_repository_with_no_commits(tmp_path: Path) -> None:
    """`log` fails on an unborn HEAD; the working tree alone is still the honest answer."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "first.py").write_text("print(1)\n", encoding="utf-8")

    payload = await read_recent_files(repo)
    assert payload["available"] is True
    assert [item["path"] for item in payload["items"]] == ["first.py"]


@pytest.mark.asyncio
async def test_read_recent_files_re_roots_a_project_below_its_repository(tmp_path: Path) -> None:
    """A Project registered in a subdirectory speaks its own coordinates, not the repo's."""
    repo = tmp_path / "repo"
    (repo / "app" / "src").mkdir(parents=True)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "app" / "src" / "inside.py").write_text("print(1)\n", encoding="utf-8")
    (repo / "outside.py").write_text("print(2)\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "first")

    payload = await read_recent_files(repo / "app")
    assert [item["path"] for item in payload["items"]] == ["src/inside.py"]


@pytest.mark.asyncio
async def test_read_recent_files_drops_a_deleted_committed_path(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "gone.py").write_text("print(1)\n", encoding="utf-8")
    (repo / "stays.py").write_text("print(2)\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "first")
    _git(repo, "rm", "-q", "gone.py")
    _git(repo, "commit", "-q", "-m", "remove")

    payload = await read_recent_files(repo)
    assert [item["path"] for item in payload["items"]] == ["stays.py"]
