"""Phase 14: the docs-only classifier, against real `git diff --raw -z` output.

The parser half is driven from bytes a real Git produced rather than from strings this
test invented, because the whole reason the raw form was chosen over `--name-status` is
that it carries the file modes - and a fixture that agrees with the parser would prove
nothing about what Git actually emits for a submodule or a symlink.

The classifier half is exercised directly, because it is a pure total function and its
interesting cases (an unreadable diff, a mode nobody has seen) are ones no repository
can be persuaded to produce on demand.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from swe_mux.land_classify import (
    ChangeEntry,
    classify_change_set,
    is_documentation_path,
    parse_raw_change_set,
    read_change_set,
)

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args], text=True, capture_output=True, check=True
    )
    return result.stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-b", "main")
    git(root, "config", "user.name", "Test User")
    git(root, "config", "user.email", "test@example.invalid")
    (root / "README.md").write_text("start\n", encoding="utf-8")
    git(root, "add", "README.md")
    git(root, "commit", "-m", "initial")
    return root


def write(repo_root: Path, relative: str, text: str) -> None:
    path = repo_root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def commit_all(repo_root: Path, message: str) -> str:
    git(repo_root, "add", "-A")
    git(repo_root, "commit", "-m", message)
    return git(repo_root, "rev-parse", "HEAD")


# -- the allowlist ------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "README.md",
        "CLAUDE.md",
        ".docs/design/features/land-queue.md",
        ".docs/technical/backend/packages/git-and-landing.md",
        "docs/guide.md",
        "frontend/src/notes.md",
        "README.MD",
        ".docs/design/diagram.png",
        "docs/assets/flow.svg",
    ],
)
def test_documentation_paths_are_documentation(path: str) -> None:
    assert is_documentation_path(path)


@pytest.mark.parametrize(
    "path",
    [
        # The obvious half.
        "src/swe_mux/land_queue.py",
        "frontend/src/GitLandBar.tsx",
        ".worktree-verify",
        "pyproject.toml",
        ".gitattributes",
        # An asset that is only documentation *inside* a documentation tree: this one
        # may be a fixture a test compares bytes against.
        "frontend/public/logo.png",
        "tests/fixtures/screen.png",
        # A script that happens to live in a documentation tree is the doubt case, and
        # doubt is not documentation.
        ".docs/tools/build.py",
        ".docs/Makefile",
        # A tree that merely starts with the same letters, and one nested elsewhere.
        ".docsystem/notes.txt",
        "vendor/docs/build.sh",
        # Not root-anchored: the prefix rule is about the repository's own tree.
        "frontend/.docs/chart.png",
        # Bytes that did not decode cannot be matched against an allowlist honestly.
        "src/caf\ufffd.md",
        # An extensionless file, and a dotfile whose "extension" is its whole name.
        "Makefile",
        ".mdrc",
        "",
    ],
)
def test_everything_else_is_not_documentation(path: str) -> None:
    assert not is_documentation_path(path)


# -- parsing ------------------------------------------------------------------


async def test_a_documentation_only_change_set_skips_the_gate(repo: Path) -> None:
    base = git(repo, "rev-parse", "HEAD")
    write(repo, ".docs/design/features/land-queue.md", "words\n")
    write(repo, "README.md", "start\nmore\n")
    tip = commit_all(repo, "docs")
    choice = classify_change_set(await read_change_set(str(repo), base, tip))
    assert choice.gate == "docs_only"
    assert choice.skips_verification
    assert choice.path_count == 2
    assert choice.disqualifying == ()
    assert "2 changed path(s) are documentation" in choice.reason


async def test_one_source_file_among_the_docs_runs_the_full_gate(repo: Path) -> None:
    base = git(repo, "rev-parse", "HEAD")
    write(repo, ".docs/design/features/land-queue.md", "words\n")
    write(repo, "src/swe_mux/land_queue.py", "x = 1\n")
    tip = commit_all(repo, "mixed")
    choice = classify_change_set(await read_change_set(str(repo), base, tip))
    assert choice.gate == "full"
    assert choice.disqualifying == ("src/swe_mux/land_queue.py",)
    assert "1 of 2 changed path(s) are not documentation" in choice.reason


async def test_deleting_documentation_stays_documentation(repo: Path) -> None:
    write(repo, ".docs/old.md", "retired\n")
    base = commit_all(repo, "add a doc")
    (repo / ".docs" / "old.md").unlink()
    tip = commit_all(repo, "remove a doc")
    choice = classify_change_set(await read_change_set(str(repo), base, tip))
    assert choice.gate == "docs_only"


async def test_deleting_a_source_file_runs_the_full_gate(repo: Path) -> None:
    write(repo, "src/thing.py", "x = 1\n")
    base = commit_all(repo, "add a module")
    (repo / "src" / "thing.py").unlink()
    tip = commit_all(repo, "remove a module")
    choice = classify_change_set(await read_change_set(str(repo), base, tip))
    assert choice.gate == "full"
    assert choice.disqualifying == ("src/thing.py",)


async def test_a_rename_runs_the_full_gate_even_between_two_documents(repo: Path) -> None:
    """The one case where both paths are documentation and the answer is still the gate.

    A rename is the shape most likely to be reported differently by a differently
    configured Git, and three minutes is a cheap price for never having to reason about
    which of those forms arrived.
    """
    write(repo, ".docs/before.md", "a" * 200 + "\n")
    base = commit_all(repo, "add a doc")
    (repo / ".docs" / "before.md").rename(repo / ".docs" / "after.md")
    tip = commit_all(repo, "rename a doc")
    entries = await read_change_set(str(repo), base, tip)
    assert entries is not None
    assert [entry.status for entry in entries] == ["R"]
    choice = classify_change_set(entries)
    assert choice.gate == "full"
    assert choice.disqualifying[0].startswith("a rename: .docs/before.md -> .docs/after.md")


async def test_a_submodule_is_never_documentation(tmp_path: Path, repo: Path) -> None:
    """Read from a real gitlink, so the `160000` mode is Git's rather than the test's."""
    inner = tmp_path / "inner"
    inner.mkdir()
    git(inner, "init", "-b", "main")
    git(inner, "config", "user.name", "Test User")
    git(inner, "config", "user.email", "test@example.invalid")
    (inner / "README.md").write_text("inner\n", encoding="utf-8")
    git(inner, "add", "README.md")
    git(inner, "commit", "-m", "inner initial")

    base = git(repo, "rev-parse", "HEAD")
    git(repo, "-c", "protocol.file.allow=always", "submodule", "add", str(inner), "vendor")
    tip = commit_all(repo, "add a submodule")
    entries = await read_change_set(str(repo), base, tip)
    assert entries is not None
    assert any("160000" in (entry.src_mode, entry.dst_mode) for entry in entries)
    choice = classify_change_set(entries)
    assert choice.gate == "full"


def test_a_symlink_named_like_a_document_is_not_one() -> None:
    """Parsed from the exact bytes Git emits for a `120000`, which Windows cannot make."""
    entries = parse_raw_change_set(
        ":000000 120000 0000000 e69de29 A\0.docs/link.md\0"
    )
    assert entries is not None
    choice = classify_change_set(entries)
    assert choice.gate == "full"
    assert choice.disqualifying == ("a symlink: .docs/link.md",)


def test_making_a_document_executable_runs_the_full_gate() -> None:
    entries = parse_raw_change_set(
        ":100644 100755 aaaaaaa bbbbbbb M\0.docs/run.md\0"
    )
    assert entries is not None
    assert classify_change_set(entries).gate == "full"


# -- fail-closed --------------------------------------------------------------


def test_an_unreadable_change_set_runs_the_full_gate() -> None:
    choice = classify_change_set(None)
    assert choice.gate == "full"
    assert "could not be read" in choice.reason


def test_an_empty_change_set_runs_the_full_gate() -> None:
    """Empty is not evidence. A branch with nothing to land settles before the gate."""
    choice = classify_change_set(())
    assert choice.gate == "full"
    assert "empty" in choice.reason


@pytest.mark.parametrize(
    "payload",
    [
        # A combined diff, which a merge commit produces and this parser never reads.
        "::100644 100644 100644 aaa bbb ccc MM\0src/thing.py\0",
        # A record with no path.
        ":100644 100644 aaa bbb M\0",
        # A rename with only one of its two paths.
        ":100644 100644 aaa bbb R100\0.docs/before.md\0",
        # Not a raw record at all - `--name-status` output, say.
        "M\0.docs/a.md\0",
        # A metadata chunk with the wrong number of fields.
        ":100644 100644 aaa M\0.docs/a.md\0",
        # An empty path where a path was promised.
        ":100644 100644 aaa bbb M\0\0",
    ],
)
def test_an_unparseable_form_is_not_classified(payload: str) -> None:
    assert parse_raw_change_set(payload) is None
    assert classify_change_set(parse_raw_change_set(payload)).gate == "full"


async def test_a_missing_revision_reads_as_unreadable(repo: Path) -> None:
    head = git(repo, "rev-parse", "HEAD")
    assert await read_change_set(str(repo), "", head) is None
    assert await read_change_set(str(repo), head, head) is None
    assert await read_change_set(str(repo), "0" * 40, head) is None


def test_an_unknown_status_letter_is_not_classified() -> None:
    entries = (ChangeEntry("100644", "100644", "X", (".docs/a.md",)),)
    choice = classify_change_set(entries)
    assert choice.gate == "full"
    assert choice.disqualifying == ("a `X` change: .docs/a.md",)


def test_the_recorded_sample_is_bounded() -> None:
    entries = tuple(
        ChangeEntry("000000", "100644", "A", (f".docs/{index:03d}.md",)) for index in range(120)
    )
    choice = classify_change_set(entries)
    assert choice.gate == "docs_only"
    assert choice.path_count == 120
    assert len(choice.paths) == 40
