from __future__ import annotations

from itertools import count
from pathlib import Path

from swe_mux.storage_usage import (
    ProjectFootprintTarget,
    StorageUsage,
    _classify,
    _walk_size,
)


def _write(path: Path, size: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)


def test_classify_buckets() -> None:
    assert _classify("mux.db") == "database"
    assert _classify("mux.db-wal") == "database"
    assert _classify("webview") == "webview"
    assert _classify("access.log") == "logs"
    assert _classify("access.log.3") == "logs"
    assert _classify("daemon.log") == "logs"
    assert _classify("worktrees") == "worktrees"
    assert _classify("config.toml") == "other"


def test_walk_size_sums_nested_files(tmp_path: Path) -> None:
    _write(tmp_path / "a.bin", 10)
    _write(tmp_path / "sub" / "b.bin", 25)
    _write(tmp_path / "sub" / "deep" / "c.bin", 5)
    total, files = _walk_size(tmp_path)
    assert total == 40
    assert files == 3


def test_walk_size_missing_dir_is_zero(tmp_path: Path) -> None:
    assert _walk_size(tmp_path / "nope") == (0, 0)


def test_snapshot_reports_buckets_and_projects(tmp_path: Path) -> None:
    data_dir = tmp_path / ".mux"
    _write(data_dir / "mux.db", 1000)
    _write(data_dir / "mux.db-wal", 200)
    _write(data_dir / "daemon.log", 50)
    _write(data_dir / "access.log.1", 50)
    _write(data_dir / "webview" / "EBWebView" / "cache.bin", 4000)
    _write(data_dir / "config.toml", 7)

    project_root = tmp_path / "proj"
    _write(project_root / ".swe-mux" / "config.toml", 300)
    _write(project_root / ".swe-mux" / "notes" / "note.md", 120)
    empty_root = tmp_path / "empty"
    empty_root.mkdir()

    projects = [
        ProjectFootprintTarget(id="p1", label="Proj", root=str(project_root)),
        ProjectFootprintTarget(id="p2", label="Empty", root=str(empty_root)),
    ]
    storage = StorageUsage(data_dir, lambda: projects)
    report = storage.snapshot()

    assert report["cached"] is False
    assert report["data_dir"] == str(data_dir)

    global_section = report["global"]
    assert global_section["present"] is True
    assert global_section["total_bytes"] == 1000 + 200 + 50 + 50 + 4000 + 7
    buckets = {item["name"]: item for item in global_section["buckets"]}
    assert buckets["database"]["bytes"] == 1200
    assert buckets["database"]["files"] == 2
    assert buckets["webview"]["bytes"] == 4000
    assert buckets["logs"]["bytes"] == 100
    assert buckets["other"]["bytes"] == 7
    # Buckets are sorted largest first.
    assert [item["name"] for item in global_section["buckets"]][0] == "webview"

    projects_section = report["projects"]
    assert projects_section["total_bytes"] == 420
    items = {item["project_id"]: item for item in projects_section["items"]}
    assert items["p1"]["bytes"] == 420
    assert items["p1"]["files"] == 2
    assert items["p1"]["present"] is True
    assert items["p2"]["present"] is False
    assert items["p2"]["bytes"] == 0
    # Largest project first.
    assert projects_section["items"][0]["project_id"] == "p1"


def test_snapshot_missing_data_dir_reports_absent(tmp_path: Path) -> None:
    storage = StorageUsage(tmp_path / "gone", lambda: [])
    report = storage.snapshot()
    assert report["global"]["present"] is False
    assert report["global"]["total_bytes"] == 0
    assert report["projects"]["items"] == []


def test_snapshot_uses_ttl_cache_then_forces() -> None:
    ticks = count(start=0, step=1)
    calls = {"n": 0}

    def projects() -> list[ProjectFootprintTarget]:
        calls["n"] += 1
        return []

    # A monotonic clock that advances 1s per read keeps the first result inside
    # the TTL window for the second (cached) call.
    storage = StorageUsage(
        Path("nonexistent-data-dir"),
        projects,
        ttl=100.0,
        clock=lambda: next(ticks),
    )
    first = storage.snapshot()
    assert first["cached"] is False
    second = storage.snapshot()
    assert second["cached"] is True
    # The cached read did not re-walk, so the projects callable ran once.
    assert calls["n"] == 1
    third = storage.snapshot(force=True)
    assert third["cached"] is False
    assert calls["n"] == 2
