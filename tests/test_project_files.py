from __future__ import annotations

from pathlib import Path

import pytest

from swe_mux.git_projects import ProjectIdentity, rebase_identity
from swe_mux.project_files import (
    ObservationsUnreadableError,
    append_observation,
    effective_project_ignores,
    ignored_project_path,
    initialize_note,
    list_project_directories,
    list_project_directory,
    note_exists,
    parse_project_config,
    read_note,
    read_observations,
    read_project_config,
    search_project_files,
    write_note,
    write_observations,
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


async def test_explicit_project_identity_keeps_a_nested_project_out_of_its_worktree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A Project registered *inside* a larger worktree: Git discovery answers
    # "which worktree contains this path", which is the wrong question once a
    # route already knows the owning Project. Without the explicit identity every
    # derived path (notes, config, observations) lands in the enclosing Project.
    outer = tmp_path / "worktree"
    inner = outer / "packages" / "app"
    inner.mkdir(parents=True)

    async def resolve_to_outer(_cwd: str | Path) -> ProjectIdentity:
        return ProjectIdentity("outer-scope", "worktree", str(outer), "git-worktree", "grp", "g")

    monkeypatch.setattr("swe_mux.project_files.resolve_project", resolve_to_outer)

    bled = await read_note(inner, "projects", "inner-project")
    assert Path(bled["path"]) == outer / ".swe-mux" / "notes" / "project.md"

    identity = ProjectIdentity("inner-project", "app", str(inner), "registered")
    scoped = await read_note(inner, "projects", "inner-project", project=identity)
    assert Path(scoped["path"]) == inner / ".swe-mux" / "notes" / "project.md"

    written = await write_note(
        inner, "projects", "inner-project", "# App\n", "missing", project=identity
    )
    assert Path(written["path"]).is_file()
    assert not (outer / ".swe-mux" / "notes" / "project.md").exists()


async def test_rebase_identity_reanchors_only_the_root_not_the_repo_group() -> None:
    outer = ProjectIdentity("outer", "worktree", r"C:\repo", "git-worktree", "grp-id", "grp")
    inner = rebase_identity(outer, r"C:\repo\packages\app")
    assert inner.root == str(Path(r"C:\repo\packages\app").resolve())
    assert inner.id != outer.id
    # Repository-group metadata still describes the real worktree.
    assert (inner.repo_group_id, inner.repo_group_label) == ("grp-id", "grp")
    # A root that already matches is returned untouched.
    assert rebase_identity(outer, outer.root) is outer


async def test_corrupt_observations_are_refused_rather_than_clobbered(tmp_path: Path) -> None:
    # observations.json is project-owned and hand-editable, so it can acquire
    # merge-conflict markers. Reading it as an empty list means the next captured
    # note rewrites the file with one item and silently destroys the rest.
    inbox = tmp_path / ".swe-mux" / "observations.json"
    inbox.parent.mkdir(parents=True)
    inbox.write_text("<<<<<<< HEAD\n{not json}\n", encoding="utf-8")

    listing = await read_observations(tmp_path)
    assert listing["status"] == "malformed"
    assert listing["observations"] == []

    with pytest.raises(ObservationsUnreadableError):
        await append_observation(tmp_path, "a note worth keeping")
    with pytest.raises(ObservationsUnreadableError):
        await write_observations(tmp_path, [], listing["revision"])
    # Nothing was written over the user's file.
    assert inbox.read_text(encoding="utf-8").startswith("<<<<<<<")


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


async def test_session_note_is_lazily_initialized_and_never_overwritten(tmp_path: Path) -> None:
    note = await initialize_note(tmp_path, "sessions", "terminal-123")
    path = Path(note["path"])
    assert path == tmp_path / ".swe-mux" / "notes" / "sessions" / "terminal-123.md"
    assert note["exists"]
    assert note["markdown"] == ""
    assert note_exists(tmp_path, "sessions", "terminal-123")

    saved = await write_note(
        tmp_path, "sessions", "terminal-123", "# Durable session context\n", note["revision"]
    )
    reopened = await initialize_note(tmp_path, "sessions", "terminal-123")
    assert reopened["markdown"] == saved["markdown"]
    assert reopened["revision"] == saved["revision"]

    unsafe = await initialize_note(tmp_path, "sessions", "external:provider/run")
    assert Path(unsafe["path"]).parent == path.parent
    assert Path(unsafe["path"]).name.startswith("id-")


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


def test_batch_listing_returns_each_folder_and_omits_missing(tmp_path: Path) -> None:
    (tmp_path / "src" / "swe_mux").mkdir(parents=True)
    (tmp_path / "src" / "main.py").write_text("x", encoding="utf-8")
    (tmp_path / "docs").mkdir()

    # A saved tree references the root, two live folders, a folder that was
    # deleted, and a path that is now a file. Only the live folders come back.
    result = list_project_directories(
        tmp_path,
        ["", "src", "src/swe_mux", "gone", "src/main.py"],
    )
    directories = result["directories"]
    assert set(directories) == {"", "src", "src/swe_mux"}
    assert [item["name"] for item in directories["src"]["items"]] == ["swe_mux", "main.py"]


def test_batch_listing_dedupes_repeated_paths(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    result = list_project_directories(tmp_path, ["src", "src", ""])
    assert set(result["directories"]) == {"", "src"}


def test_project_search_matches_names_contents_and_respects_ignores(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "node_modules" / "pkg").mkdir(parents=True)
    (tmp_path / "src" / "widget.ts").write_text("export const value = 1\n", encoding="utf-8")
    (tmp_path / "src" / "helper.ts").write_text(
        "// uses the widget value\nconst x = 2\n", encoding="utf-8"
    )
    (tmp_path / "notes.md").write_text("nothing relevant here\n", encoding="utf-8")
    (tmp_path / "node_modules" / "pkg" / "widget.js").write_text("widget", encoding="utf-8")
    (tmp_path / "blob.bin").write_bytes(b"widget\x00binary")
    patterns = effective_project_ignores(tmp_path, ["node_modules"])

    names = search_project_files(tmp_path, "widget", mode="names", ignore_patterns=patterns)
    assert [item["path"] for item in names["items"]] == ["src/widget.ts"]

    contents = search_project_files(tmp_path, "widget", mode="contents", ignore_patterns=patterns)
    hit = next(item for item in contents["items"] if item["path"] == "src/helper.ts")
    assert hit["match"] == "content" and hit["line"] == 1
    assert hit["snippet"] == "// uses the widget value"
    # The binary file contains the needle but must never surface as a content match.
    assert all(item["path"] != "blob.bin" for item in contents["items"])

    both = search_project_files(tmp_path, "widget", mode="both", ignore_patterns=patterns)
    paths = [item["path"] for item in both["items"]]
    assert paths == ["src/widget.ts", "src/helper.ts"]  # name match sorts before content match
    assert not search_project_files(tmp_path, "", mode="both", ignore_patterns=patterns)["items"]
