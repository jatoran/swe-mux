from __future__ import annotations

import io
from pathlib import Path

import pytest
from PIL import Image

from swe_mux.config import DEFAULT_PROJECT_IGNORE_PATTERNS, WORKTREE_IGNORE_PATTERNS
from swe_mux.git_projects import ProjectIdentity, rebase_identity
from swe_mux.project_files import (
    DEFAULT_NOTE_STORAGE_ID,
    SEARCH_MAX_FILES,
    ObservationsUnreadableError,
    ProjectConfigConflict,
    ProjectFileRevisionConflict,
    ProjectImageUnavailable,
    ProjectResourceExists,
    append_observation,
    create_project_resource,
    effective_project_ignores,
    ignored_project_path,
    initialize_note,
    list_project_directories,
    list_project_directory,
    merge_project_config,
    normalized_project_values,
    note_exists,
    parse_project_config,
    read_note,
    read_observations,
    read_project_config,
    read_project_file,
    read_project_image_content,
    search_project_files,
    serialize_project_config,
    write_note,
    write_observations,
    write_project_config,
)

from .host_paths import ABS_ROOT, abs_path


def test_project_config_round_trips_worktree_setup_command() -> None:
    values = {"worktree": {"setup_command": "uv sync && npm ci"}}

    assert parse_project_config(serialize_project_config(values)) == values


def test_project_config_rejects_unknown_worktree_fields() -> None:
    with pytest.raises(ValueError, match="unknown worktree fields"):
        parse_project_config(b'version = 1\n[worktree]\nscript = "bad"\n')


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


async def test_a_field_scoped_write_leaves_every_other_field_alone(tmp_path: Path) -> None:
    # The defect this exists for: three sections of one panel own disjoint keys in one
    # file, so each one's write invalidated the other two's cached revision and the
    # operator's second edit was refused until they closed and reopened the panel.
    await write_project_config(
        tmp_path,
        {
            "automations": {"raw_store": True},
            "session_control_grant": "draft",
            "worktree": {"setup_command": "uv sync", "verify_command": "./verify"},
        },
        "missing",
    )

    # The authority table's write names one field and says what it believed that field
    # held. The automations it never mentioned are untouched, and no revision was involved.
    after_grant = await merge_project_config(
        tmp_path, {"session_control_grant": "granted"}, {"session_control_grant": "draft"}
    )
    assert after_grant["values"]["session_control_grant"] == "granted"
    assert after_grant["values"]["automations"] == {"raw_store": True}

    # And the opt-in table's write, composed against the file as it stood *before* the
    # authority change, still succeeds: nothing it named has moved.
    after_optins = await merge_project_config(
        tmp_path,
        {"automations": {"raw_store": True, "tier0": True}},
        {"automations": {"raw_store": True}},
    )
    assert after_optins["values"]["automations"] == {"raw_store": True, "tier0": True}
    assert after_optins["values"]["session_control_grant"] == "granted"
    # The land queue's field lives in the same table as the panel's setup command and
    # survives a write that named neither.
    assert after_optins["values"]["worktree"] == {
        "setup_command": "uv sync",
        "verify_command": "./verify",
    }


async def test_a_field_that_moved_underneath_conflicts_by_name(tmp_path: Path) -> None:
    await write_project_config(tmp_path, {"approval_ceiling": "wait"}, "missing")
    await merge_project_config(
        tmp_path, {"approval_ceiling": "allowlisted"}, {"approval_ceiling": "wait"}
    )

    # A second editor still believing "wait" is a real collision, and is refused by the
    # name of the field rather than by "the file changed" - which is the whole point:
    # the caller can say what it would overwrite.
    with pytest.raises(ProjectConfigConflict) as conflict:
        await merge_project_config(
            tmp_path, {"approval_ceiling": "allow_all"}, {"approval_ceiling": "wait"}
        )
    assert conflict.value.fields == ["approval_ceiling"]
    assert conflict.value.current["values"]["approval_ceiling"] == "allowlisted"
    # The refusal changed nothing.
    assert (await read_project_config(tmp_path))["values"]["approval_ceiling"] == "allowlisted"


async def test_a_field_scoped_write_removes_with_none_and_refuses_the_unwritable(
    tmp_path: Path,
) -> None:
    await write_project_config(
        tmp_path, {"preferred_backend": "shell", "ignore_patterns": [".cache"]}, "missing"
    )
    cleared = await merge_project_config(
        tmp_path, {"preferred_backend": None}, {"preferred_backend": "shell"}
    )
    assert "preferred_backend" not in cleared["values"]
    assert cleared["values"]["ignore_patterns"] == [".cache"]

    with pytest.raises(ValueError, match="unknown project fields"):
        await merge_project_config(tmp_path, {"token": "forbidden"}, {})

    # Merging into a file this process could not parse would write the caller's fields
    # over contents nobody read - discarding whatever the operator was fixing by hand.
    (tmp_path / ".swe-mux" / "config.toml").write_text("version = 1\nnot toml", encoding="utf-8")
    with pytest.raises(ValueError, match="cannot be parsed"):
        await merge_project_config(tmp_path, {"preferred_backend": "shell"}, {})


def test_a_base_is_compared_as_the_file_would_store_it() -> None:
    # Raw comparison would call these three pairs changes, and each false conflict is a
    # refusal the operator cannot act on. They are compared after the round trip the
    # writer and reader actually perform, so the format decides rather than a second
    # opinion about the format.
    assert normalized_project_values({"ignore_patterns": []}) == normalized_project_values({})
    assert normalized_project_values({"preferred_backend": ""}) == normalized_project_values({})
    assert normalized_project_values(
        {"scan_timeline_daily_budget_usd": 5}
    ) == normalized_project_values({})
    # And a value that really is stored still compares as itself.
    assert normalized_project_values({"notification_sounds_enabled": False}) == {
        "notification_sounds_enabled": False
    }


async def test_a_base_that_normalizes_away_does_not_conflict(tmp_path: Path) -> None:
    await write_project_config(tmp_path, {"session_control_grant": "draft"}, "missing")
    written = await merge_project_config(
        tmp_path, {"ignore_patterns": [".cache"]}, {"ignore_patterns": []}
    )
    assert written["values"]["ignore_patterns"] == [".cache"]


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

    bled = await read_note(inner, DEFAULT_NOTE_STORAGE_ID)
    assert Path(bled["path"]) == outer / ".swe-mux" / "notes" / "project.md"

    identity = ProjectIdentity("inner-project", "app", str(inner), "registered")
    scoped = await read_note(inner, DEFAULT_NOTE_STORAGE_ID, project=identity)
    assert Path(scoped["path"]) == inner / ".swe-mux" / "notes" / "project.md"

    written = await write_note(
        inner, DEFAULT_NOTE_STORAGE_ID, "# App\n", "missing", project=identity
    )
    assert Path(written["path"]).is_file()
    assert not (outer / ".swe-mux" / "notes" / "project.md").exists()


async def test_rebase_identity_reanchors_only_the_root_not_the_repo_group() -> None:
    outer = ProjectIdentity("outer", "worktree", ABS_ROOT, "git-worktree", "grp-id", "grp")
    inner = rebase_identity(outer, abs_path("packages", "app"))
    assert inner.root == str(Path(abs_path("packages", "app")).resolve())
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


async def test_control_request_round_trips_with_its_fields(tmp_path: Path) -> None:
    # Phase 7.6: a drafted interrupt/end must read back with the target and action
    # a human needs to approve it. The kind allowlist and the request field
    # allowlist both have to know about control_request, or the draft is stored
    # empty and the approval path has nothing to act on (caught live 2026-08-16).
    request = {
        "action": "interrupt",
        "target_session_id": "sess-b",
        "target_name": "sessionB",
        "reason": "wedged in a loop",
        "from_session": "sess-a",
        "status": "pending",
    }
    result = await append_observation(
        tmp_path, "sessionA asks to interrupt sessionB",
        kind="control_request", request=request,
    )
    listing = await read_observations(tmp_path)
    item = next(o for o in listing["observations"] if o["id"] == result["appended_id"])
    assert item["kind"] == "control_request"
    assert item["request"]["action"] == "interrupt"
    assert item["request"]["target_session_id"] == "sess-b"
    assert item["request"]["target_name"] == "sessionB"
    assert item["request"]["status"] == "pending"


async def test_project_note_round_trips_and_detects_external_edits(tmp_path: Path) -> None:
    missing = await read_note(tmp_path, DEFAULT_NOTE_STORAGE_ID)
    assert missing["revision"] == "missing"
    saved = await write_note(
        tmp_path, DEFAULT_NOTE_STORAGE_ID, "# Plan\n\nKeep this local.\n", "missing"
    )
    path = Path(saved["path"])
    assert path == tmp_path / ".swe-mux" / "notes" / "project.md"
    assert "# Plan" in path.read_text(encoding="utf-8")
    assert (path.parent / ".gitignore").read_text(encoding="utf-8") == "*\n"

    path.write_text(path.read_text(encoding="utf-8") + "external\n", encoding="utf-8")
    with pytest.raises(ValueError, match="changed externally"):
        await write_note(tmp_path, DEFAULT_NOTE_STORAGE_ID, "overwrite", saved["revision"])


async def test_generic_note_is_initialized_once_and_never_overwritten(tmp_path: Path) -> None:
    note = await initialize_note(tmp_path, "durable-note", "Durable context")
    path = Path(note["path"])
    assert path == tmp_path / ".swe-mux" / "notes" / "items" / "durable-note.md"
    assert note["exists"]
    assert note["markdown"] == ""
    assert note_exists(tmp_path, "durable-note")

    saved = await write_note(tmp_path, "durable-note", "# Durable context\n", note["revision"])
    reopened = await initialize_note(tmp_path, "durable-note", "Different title")
    assert reopened["markdown"] == saved["markdown"]
    assert reopened["revision"] == saved["revision"]

    unsafe = await initialize_note(tmp_path, "external:provider/run", "External context")
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
    assert names["scanned_files"] == 4
    assert names["scanned_bytes"] == 0

    contents = search_project_files(tmp_path, "widget", mode="contents", ignore_patterns=patterns)
    hit = next(item for item in contents["items"] if item["path"] == "src/helper.ts")
    assert hit["match"] == "content" and hit["line"] == 1
    assert hit["snippet"] == "// uses the widget value"
    # The binary file contains the needle but must never surface as a content match.
    assert all(item["path"] != "blob.bin" for item in contents["items"])
    assert contents["scanned_files"] == 4
    assert contents["scanned_bytes"] > 0

    both = search_project_files(tmp_path, "widget", mode="both", ignore_patterns=patterns)
    paths = [item["path"] for item in both["items"]]
    assert paths == ["src/widget.ts", "src/helper.ts"]  # name match sorts before content match
    empty = search_project_files(tmp_path, "", mode="both", ignore_patterns=patterns)
    assert not empty["items"]
    assert empty["scanned_files"] == 0
    assert empty["scanned_bytes"] == 0


def test_conventional_worktree_roots_are_hidden_without_hiding_agent_config(
    tmp_path: Path,
) -> None:
    """`.claude/worktrees` goes; `.claude/settings.json` stays.

    The patterns are path-shaped rather than bare names precisely so this holds: a person
    browses their agent config, and hiding the whole `.claude` folder to be rid of the
    checkouts inside it would cost them that.
    """
    (tmp_path / ".claude" / "worktrees" / "feature" / "src").mkdir(parents=True)
    (tmp_path / ".claude" / "worktrees" / "feature" / "src" / "app.py").write_text(
        "widget", encoding="utf-8"
    )
    (tmp_path / ".claude" / "agents").mkdir()
    (tmp_path / ".claude" / "settings.json").write_text("{}", encoding="utf-8")
    (tmp_path / ".worktrees" / "bare").mkdir(parents=True)
    (tmp_path / ".worktrees" / "bare" / "widget.py").write_text("x", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "widget.py").write_text("x", encoding="utf-8")

    patterns = list(DEFAULT_PROJECT_IGNORE_PATTERNS)
    assert all(pattern in patterns for pattern in WORKTREE_IGNORE_PATTERNS)

    claude = list_project_directory(tmp_path, ".claude", ignore_patterns=patterns, pruned_paths=())
    assert [item["name"] for item in claude["items"]] == ["agents", "settings.json"]

    found = search_project_files(
        tmp_path, "widget", mode="both", ignore_patterns=patterns, pruned_paths=()
    )
    assert [item["path"] for item in found["items"]] == ["src/widget.py"]


def test_a_worktree_git_names_is_pruned_even_where_no_pattern_covers_it(
    tmp_path: Path,
) -> None:
    """`git worktree add ./scratch` is legal and no static pattern will ever name it."""
    (tmp_path / "scratch" / "src").mkdir(parents=True)
    (tmp_path / "scratch" / "src" / "widget.py").write_text("x", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "widget.py").write_text("x", encoding="utf-8")
    patterns = list(DEFAULT_PROJECT_IGNORE_PATTERNS)

    listing = list_project_directory(tmp_path, ignore_patterns=patterns, pruned_paths=["scratch"])
    assert [item["name"] for item in listing["items"]] == ["src"]

    found = search_project_files(
        tmp_path, "widget", mode="names", ignore_patterns=patterns, pruned_paths=["scratch"]
    )
    assert [item["path"] for item in found["items"]] == ["src/widget.py"]

    # The batch listing resolves the prune set once for the whole fan-out; every folder in
    # it must still get the answer.
    batch = list_project_directories(
        tmp_path, ["", "scratch"], ignore_patterns=patterns, pruned_paths=["scratch"]
    )
    assert [item["name"] for item in batch["directories"][""]["items"]] == ["src"]


def test_the_search_spends_its_file_budget_on_shallow_paths_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The defect a depth-first walk had: a full budget and none of the real answers.

    `os.walk` descends the first-sorted subtree to the bottom, so a dot-directory holding a
    vendored or duplicated tree consumes every file the search was allowed to look at and
    the walk returns before reaching `src/`. What came back was not a short list of matches
    but a *wrong* one, indistinguishable from a complete answer.
    """
    monkeypatch.setattr("swe_mux.project_files.SEARCH_MAX_FILES", 6)
    deep = tmp_path / ".vendor" / "copy" / "src"
    deep.mkdir(parents=True)
    for index in range(20):
        (deep / f"widget{index}.py").write_text("x", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "widget.py").write_text("x", encoding="utf-8")

    found = search_project_files(
        tmp_path, "widget", mode="names", ignore_patterns=[".git"], pruned_paths=()
    )
    assert "src/widget.py" in [item["path"] for item in found["items"]]


def test_a_truncated_search_says_which_bound_bit_and_where_it_stopped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """"Refine to narrow" is false advice against the file budget, so the two are distinct."""
    monkeypatch.setattr("swe_mux.project_files.SEARCH_MAX_FILES", 3)
    (tmp_path / "deep").mkdir()
    for index in range(10):
        (tmp_path / "deep" / f"widget{index}.py").write_text("x", encoding="utf-8")

    exhausted = search_project_files(
        tmp_path, "widget", mode="names", ignore_patterns=[".git"], pruned_paths=()
    )
    assert exhausted["truncated"] is True
    assert exhausted["truncated_reason"] == "files"
    assert exhausted["stopped_at"] == "deep"

    monkeypatch.setattr("swe_mux.project_files.SEARCH_MAX_FILES", SEARCH_MAX_FILES)
    capped = search_project_files(
        tmp_path,
        "widget",
        mode="names",
        ignore_patterns=[".git"],
        pruned_paths=(),
        limit=4,
    )
    assert capped["truncated"] is True
    assert capped["truncated_reason"] == "results"
    assert capped["stopped_at"] is None

    complete = search_project_files(
        tmp_path, "widget", mode="names", ignore_patterns=[".git"], pruned_paths=()
    )
    assert complete["truncated"] is False
    assert complete["truncated_reason"] is None


def test_project_file_inspection_classifies_delimited_text_and_bounds_reads(
    tmp_path: Path,
) -> None:
    csv_file = tmp_path / "report.csv"
    csv_file.write_text('name,detail\nalpha,"one, two"\n', encoding="utf-8")
    payload = read_project_file(tmp_path, "report.csv")
    assert payload["status"] == "ready"
    assert payload["presentation"] == {"kind": "delimited", "delimiter": ","}
    assert payload["text"].startswith("name,detail")

    oversized = tmp_path / "huge.txt"
    with oversized.open("wb") as handle:
        handle.seek(2 * 1024 * 1024)
        handle.write(b"x")
    refused = read_project_file(tmp_path, "huge.txt")
    assert refused["status"] == "too-large"
    assert refused["revision"] == "unavailable"
    assert "text" not in refused


def test_project_image_inspection_and_content_are_allowlisted_and_revision_pinned(
    tmp_path: Path,
) -> None:
    target = tmp_path / "preview.png"
    buffer = io.BytesIO()
    Image.new("RGBA", (12, 7), (10, 20, 30, 255)).save(buffer, format="PNG")
    image_bytes = buffer.getvalue()
    target.write_bytes(image_bytes)

    payload = read_project_file(tmp_path, "preview.png")
    assert payload["status"] == "viewable"
    assert payload["presentation"] == {
        "kind": "image",
        "mime": "image/png",
        "width": 12,
        "height": 7,
        "frames": 1,
    }
    content, repeated = read_project_image_content(tmp_path, "preview.png", payload["revision"])
    assert content == image_bytes
    assert repeated["revision"] == payload["revision"]

    target.write_bytes(image_bytes + b"changed")
    with pytest.raises(ProjectFileRevisionConflict):
        read_project_image_content(tmp_path, "preview.png", payload["revision"])
    with pytest.raises(ProjectImageUnavailable):
        read_project_image_content(tmp_path, "preview.txt", payload["revision"])


def test_project_image_rejects_extension_mismatch_and_decoded_pixel_bombs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    buffer = io.BytesIO()
    Image.new("RGB", (10, 10), "white").save(buffer, format="PNG")
    (tmp_path / "mismatch.jpg").write_bytes(buffer.getvalue())
    mismatch = read_project_file(tmp_path, "mismatch.jpg")
    assert mismatch["status"] == "unsupported"
    assert mismatch["presentation"]["reason"] == "image-signature-extension-mismatch"

    (tmp_path / "large.png").write_bytes(buffer.getvalue())
    monkeypatch.setattr("swe_mux.project_files.PROJECT_IMAGE_MAX_PIXELS", 50)
    refused = read_project_file(tmp_path, "large.png")
    assert refused["status"] == "unsupported"
    assert refused["presentation"]["reason"] == "image-pixel-limit"


def test_project_resource_creation_is_exclusive_and_parent_scoped(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    created_file = create_project_resource(tmp_path, "src", "widget.ts", "file")
    created_folder = create_project_resource(tmp_path, "src", "components", "directory")

    assert created_file == {
        "name": "widget.ts",
        "path": "src/widget.ts",
        "parent": "src",
        "kind": "file",
        "size": 0,
    }
    assert (tmp_path / "src" / "widget.ts").read_bytes() == b""
    assert created_folder == {
        "name": "components",
        "path": "src/components",
        "parent": "src",
        "kind": "directory",
        "size": None,
    }
    assert (tmp_path / "src" / "components").is_dir()

    with pytest.raises(ProjectResourceExists):
        create_project_resource(tmp_path, "src", "widget.ts", "file")
    with pytest.raises(ProjectResourceExists):
        create_project_resource(tmp_path, "src", "components", "directory")
    assert (tmp_path / "src" / "widget.ts").read_bytes() == b""


@pytest.mark.parametrize(
    "name",
    [
        "",
        ".",
        "..",
        "../escape",
        "nested/file",
        "nested\\file",
        "CON",
        "con.txt",
        "CON .txt",
        "COM¹.txt",
        "LPT³",
        "bad.",
        "bad ",
        ".git",
        ".swe-mux",
        "bad:name",
        "bad\x00name",
        "a" * 256,
    ],
)
def test_project_resource_creation_rejects_unsafe_leaf_names(tmp_path: Path, name: str) -> None:
    with pytest.raises(ValueError):
        create_project_resource(tmp_path, "", name, "file")
    assert list(tmp_path.iterdir()) == []


def test_project_resource_creation_requires_a_safe_existing_parent(tmp_path: Path) -> None:
    (tmp_path / "ordinary.txt").write_text("keep", encoding="utf-8")
    (tmp_path / ".swe-mux").mkdir()

    with pytest.raises(ValueError):
        create_project_resource(tmp_path, "missing", "child.txt", "file")
    with pytest.raises(ValueError):
        create_project_resource(tmp_path, "ordinary.txt", "child.txt", "file")
    with pytest.raises(ValueError):
        create_project_resource(tmp_path, "../outside", "child.txt", "file")
    with pytest.raises(ValueError):
        create_project_resource(tmp_path, ".swe-mux", "child.txt", "file")
    with pytest.raises(ValueError):
        create_project_resource(tmp_path, "\0", "child.txt", "file")
    assert (tmp_path / "ordinary.txt").read_text(encoding="utf-8") == "keep"
    assert not (tmp_path / "missing").exists()
