from __future__ import annotations

from pathlib import Path

import pytest

from swe_mux.git_projects import ProjectIdentity
from swe_mux.project_files import (
    effective_project_ignores,
    ignored_project_path,
    list_project_directory,
    parse_project_config,
    read_note,
    read_project_config,
    write_note,
    write_project_config,
)


async def test_project_config_is_explicit_versioned_and_conflict_safe(tmp_path: Path) -> None:
    missing = await read_project_config(tmp_path)
    assert missing["status"] == "missing"
    assert not (tmp_path / ".swe-mux").exists()

    saved = await write_project_config(
        tmp_path,
        {"default_shell_profile": "pwsh", "ignore_patterns": [".local-cache", "*.bak"]},
        "missing",
    )
    assert saved["values"]["default_shell_profile"] == "pwsh"
    assert saved["values"]["ignore_patterns"] == [".local-cache", "*.bak"]
    assert (tmp_path / ".swe-mux" / "config.toml").is_file()
    with pytest.raises(ValueError, match="changed externally"):
        await write_project_config(tmp_path, {}, "missing")
    with pytest.raises(ValueError, match="unknown project fields"):
        await write_project_config(tmp_path, {"token": "forbidden"}, saved["revision"])


async def test_project_config_reuses_a_resolved_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = ProjectIdentity("project-id", "project", str(tmp_path), "cwd")

    async def unexpected_resolution(_cwd: str | Path) -> ProjectIdentity:
        raise AssertionError("project identity should be reused")

    monkeypatch.setattr("swe_mux.project_files.resolve_project", unexpected_resolution)

    result = await read_project_config(tmp_path, project=project)

    assert result["status"] == "missing"
    assert result["project"]["id"] == "project-id"


async def test_project_note_round_trips_and_detects_external_edits(tmp_path: Path) -> None:
    missing = await read_note(tmp_path, "projects", "project-id")
    assert missing["revision"] == "missing"
    saved = await write_note(
        tmp_path, "projects", "project-id", "# Plan\n\nKeep this local.\n", "missing"
    )
    path = Path(saved["path"])
    assert path == tmp_path / ".swe-mux" / "notes" / "project.md"
    assert "# Plan" in path.read_text(encoding="utf-8")

    path.write_text(path.read_text(encoding="utf-8") + "external\n", encoding="utf-8")
    with pytest.raises(ValueError, match="changed externally"):
        await write_note(tmp_path, "projects", "project-id", "overwrite", saved["revision"])


@pytest.mark.parametrize(
    "field",
    ["token", "bind", "host", "port", "data_dir", "hooks", "executable", "command"],
)
def test_project_config_rejects_executable_and_privileged_fields(field: str) -> None:
    value = "8765" if field == "port" else '"malicious"'
    with pytest.raises(ValueError, match="forbidden project fields"):
        parse_project_config(f"version = 1\n{field} = {value}\n".encode())


def test_project_file_tree_combines_global_and_project_ignore_patterns(tmp_path: Path) -> None:
    (tmp_path / ".swe-mux").mkdir()
    (tmp_path / ".swe-mux" / "config.toml").write_text(
        'version = 1\nignore_patterns = ["private", "*.secret"]\n', encoding="utf-8"
    )
    for name in ("src", "node_modules", "private"):
        (tmp_path / name).mkdir()
    (tmp_path / "visible.txt").write_text("ok", encoding="utf-8")
    (tmp_path / "token.secret").write_text("hidden", encoding="utf-8")

    patterns = effective_project_ignores(tmp_path, ["node_modules", ".swe-mux"])
    listing = list_project_directory(tmp_path, ignore_patterns=patterns)

    assert [item["name"] for item in listing["items"]] == ["src", "visible.txt"]
    assert ignored_project_path("nested/node_modules/pkg/index.js", patterns)
    assert ignored_project_path("nested/private/key.txt", patterns)
    assert not ignored_project_path("src/main.py", patterns)
