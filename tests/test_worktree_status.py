"""Neutral comparison-ref status semantics for worktree rows."""

from __future__ import annotations

from swe_mux.git_review import change_summary


def test_unmeasured_file_statistics_remain_null_not_zero() -> None:
    summary = change_summary(
        [
            {
                "path": "unknown.txt",
                "status": "M",
                "additions": None,
                "deletions": None,
                "binary": False,
                "submodule": False,
            }
        ]
    )
    assert summary["files"][0]["additions"] is None
    assert summary["files"][0]["deletions"] is None
    assert summary["additions"] == 0
    assert summary["deletions"] == 0


def test_text_and_binary_aggregates_remain_distinct() -> None:
    summary = change_summary(
        [
            {
                "path": "code.py",
                "status": "M",
                "additions": 4,
                "deletions": 2,
                "binary": False,
                "submodule": False,
            },
            {
                "path": "image.png",
                "status": "M",
                "additions": None,
                "deletions": None,
                "binary": True,
                "submodule": False,
            },
        ]
    )
    assert summary["additions"] == 4
    assert summary["deletions"] == 2
    assert summary["binary_files"] == 1
