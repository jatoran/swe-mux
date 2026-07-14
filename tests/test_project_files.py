from __future__ import annotations

from pathlib import Path

import pytest

from swe_mux.project_files import (
    parse_project_config,
    read_note,
    read_project_config,
    resolve_project_default_cwd,
    safe_note_filename,
    search_notes,
    write_note,
    write_project_config,
)


async def test_project_config_is_explicit_versioned_and_conflict_safe(tmp_path: Path) -> None:
    missing = await read_project_config(tmp_path)
    assert missing["status"] == "missing"
    assert not (tmp_path / ".swe-mux").exists()

    saved = await write_project_config(
        tmp_path,
        {"default_cwd": "src", "default_shell_profile": "pwsh", "notes_enabled": True},
        "missing",
    )
    assert saved["values"]["default_shell_profile"] == "pwsh"
    assert (tmp_path / ".swe-mux" / "config.toml").is_file()
    with pytest.raises(ValueError, match="changed externally"):
        await write_project_config(tmp_path, {}, "missing")
    with pytest.raises(ValueError, match="unknown project fields"):
        await write_project_config(tmp_path, {"token": "forbidden"}, saved["revision"])


async def test_notes_round_trip_as_markdown_and_detect_external_edits(tmp_path: Path) -> None:
    missing = await read_note(tmp_path, "sessions", "session:unsafe")
    assert missing["revision"] == "missing"
    saved = await write_note(
        tmp_path, "sessions", "session:unsafe", "# Plan\n\nKeep this local.\n", "missing"
    )
    path = Path(saved["path"])
    assert path.parent == tmp_path / ".swe-mux" / "notes" / "sessions"
    assert "# Plan" in path.read_text(encoding="utf-8")
    assert ":" not in path.name

    path.write_text(path.read_text(encoding="utf-8") + "external\n", encoding="utf-8")
    with pytest.raises(ValueError, match="changed externally"):
        await write_note(
            tmp_path, "sessions", "session:unsafe", "overwrite", saved["revision"]
        )
    results = await search_notes(tmp_path, "external")
    assert results[0]["kind"] == "sessions"


def test_note_filename_mapping_is_stable_and_traversal_safe() -> None:
    assert safe_note_filename("normal-id") == "normal-id"
    assert safe_note_filename("../../outside") == safe_note_filename("../../outside")
    assert "/" not in safe_note_filename("../../outside")


def test_project_default_cwd_must_remain_relative() -> None:
    with pytest.raises(ValueError, match="relative"):
        parse_project_config(b'version = 1\ndefault_cwd = "../outside"\n')
    with pytest.raises(ValueError, match="relative"):
        parse_project_config(b'version = 1\ndefault_cwd = "C:/outside"\n')


@pytest.mark.parametrize(
    "field",
    ["token", "bind", "host", "port", "data_dir", "hooks", "executable", "command"],
)
def test_project_config_rejects_executable_and_privileged_fields(field: str) -> None:
    value = "8765" if field == "port" else '"malicious"'
    with pytest.raises(ValueError, match="forbidden project fields"):
        parse_project_config(f"version = 1\n{field} = {value}\n".encode())


def test_project_default_cwd_cannot_escape_through_a_symlink(tmp_path: Path) -> None:
    project = tmp_path / "project"
    outside = tmp_path / "outside"
    project.mkdir()
    outside.mkdir()
    link = project / "linked"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are unavailable on this machine")
    with pytest.raises(ValueError, match="outside"):
        resolve_project_default_cwd(project, "linked")
