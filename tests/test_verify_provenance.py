"""Where the verification gate's bytes came from, against real repositories.

Driven through real `git` for the same reason the land queue's own tests are: the whole
argument for letting some unapproved bytes run is a claim about what git reports for a
branch, and a fake that agreed with the implementation would prove nothing about either.

The case every test here exists to protect is the last one. A branch can arrive from
someone else now, and a Project that lets its own agents change the gate must still not
execute a contributor's script unattended.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from swe_mux.git_monitor import read_git
from swe_mux.verify_provenance import (
    VerifyProvenance,
    read_verify_provenance,
    verify_bypass_allowed,
)

pytestmark = pytest.mark.anyio

SCRIPT = ".worktree-verify"
OPERATOR = "operator@example.invalid"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], text=True, capture_output=True, check=True
    ).stdout.strip()


def write_script(repo: Path, body: str) -> None:
    (repo / SCRIPT).write_text(
        f"#!/usr/bin/env bash\n{body}\n", encoding="utf-8", newline="\n"
    )


def commit_script(repo: Path, body: str, message: str, *, author: str = OPERATOR) -> None:
    write_script(repo, body)
    git(repo, "add", SCRIPT)
    git(repo, "-c", f"user.email={author}", "-c", "user.name=Someone", "commit", "-m", message)


@pytest.fixture
def trunk(tmp_path: Path) -> Path:
    repo = tmp_path / "trunk"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.name", "Operator")
    git(repo, "config", "user.email", OPERATOR)
    commit_script(repo, "exit 0", "add verification")
    return repo


def add_worktree(trunk_root: Path, name: str) -> Path:
    path = trunk_root.parent / name
    git(trunk_root, "worktree", "add", "-b", f"worktree-{name}", str(path))
    return path


async def provenance(worktree: Path, trunk_root: Path, *, source: str = "convention",
                     trunk_ref: str = "main") -> VerifyProvenance:
    return await read_verify_provenance(
        git=read_git,
        worktree_root=str(worktree),
        project_root=str(trunk_root),
        source=source,
        script_name=SCRIPT,
        trunk_ref=trunk_ref,
    )


async def test_a_configured_command_is_this_machines_and_has_no_history(trunk: Path) -> None:
    """`.swe-mux/config.toml` is git-ignored per-machine state, so no branch carries one."""
    worktree = add_worktree(trunk, "alpha")
    result = await provenance(worktree, trunk, source="project_config")
    assert result.verdict == "project_config"
    assert result.trusted


async def test_an_uncommitted_edit_could_only_have_been_written_here(trunk: Path) -> None:
    worktree = add_worktree(trunk, "alpha")
    write_script(worktree, "echo edited\nexit 0")
    result = await provenance(worktree, trunk)
    assert result.verdict == "uncommitted"
    assert result.trusted
    assert SCRIPT in result.reason


async def test_an_untracked_script_reads_the_same_way(tmp_path: Path) -> None:
    """A repository whose gate has never been committed is still a local file."""
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.email", OPERATOR)
    (repo / "other.txt").write_text("x\n", encoding="utf-8")
    git(repo, "add", "other.txt")
    git(repo, "-c", f"user.email={OPERATOR}", "-c", "user.name=Operator", "commit", "-m", "init")
    write_script(repo, "exit 0")
    result = await provenance(repo, repo)
    assert result.verdict == "uncommitted"
    assert result.trusted


async def test_a_branch_that_left_the_gate_alone_is_judged_on_who_wrote_that_copy(
    trunk: Path,
) -> None:
    worktree = add_worktree(trunk, "alpha")
    (worktree / "alpha.txt").write_text("alpha\n", encoding="utf-8")
    git(worktree, "add", "alpha.txt")
    git(worktree, "-c", f"user.email={OPERATOR}", "-c", "user.name=Operator",
        "commit", "-m", "unrelated work")
    result = await provenance(worktree, trunk)
    assert result.verdict == "local_author"
    assert result.trusted
    assert "in this repository" in result.reason


async def test_a_trunk_this_machine_never_wrote_is_not_trusted_by_position(
    tmp_path: Path,
) -> None:
    """The hole "the trunk already carries it" would open, and why it stays closed.

    A Project can be a clone of somebody else's repository. There the trunk's own
    `.worktree-verify` is a stranger's script that nobody here ever read, and trusting
    it because of where it sits would execute it unattended on the first land.
    """
    repo = tmp_path / "theirs"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.name", "Operator")
    git(repo, "config", "user.email", OPERATOR)
    commit_script(repo, "echo upstream\nexit 0", "their gate",
                  author="upstream@example.invalid")
    worktree = add_worktree(repo, "alpha")
    (worktree / "alpha.txt").write_text("alpha\n", encoding="utf-8")
    git(worktree, "add", "alpha.txt")
    git(worktree, "-c", f"user.email={OPERATOR}", "-c", "user.name=Operator",
        "commit", "-m", "my work")
    result = await provenance(worktree, repo)
    assert result.verdict == "foreign_author"
    assert not result.trusted
    assert result.authors == ("upstream@example.invalid",)


async def test_a_gate_edited_by_this_machines_identity_is_local(trunk: Path) -> None:
    worktree = add_worktree(trunk, "alpha")
    commit_script(worktree, "echo more\nexit 0", "widen the gate")
    result = await provenance(worktree, trunk)
    assert result.verdict == "local_author"
    assert result.trusted
    assert result.authors == (OPERATOR,)
    assert "on this branch" in result.reason


async def test_a_gate_edited_by_anyone_else_still_presents_for_approval(trunk: Path) -> None:
    """The case a public repository made real, and the reason the grant is not enough."""
    worktree = add_worktree(trunk, "alpha")
    commit_script(worktree, "echo theirs\nexit 0", "helpful change",
                  author="stranger@example.invalid")
    result = await provenance(worktree, trunk)
    assert result.verdict == "foreign_author"
    assert not result.trusted
    assert result.authors == ("stranger@example.invalid",)
    assert "stranger@example.invalid" in result.reason


async def test_one_foreign_commit_among_local_ones_is_enough_to_refuse(trunk: Path) -> None:
    """Every commit that touched it has to be local; the newest one deciding would let a
    stranger's line survive under a local commit made afterwards."""
    worktree = add_worktree(trunk, "alpha")
    commit_script(worktree, "echo theirs\nexit 0", "theirs",
                  author="stranger@example.invalid")
    commit_script(worktree, "echo theirs\necho mine\nexit 0", "mine")
    result = await provenance(worktree, trunk)
    assert result.verdict == "foreign_author"
    assert result.authors == ("stranger@example.invalid",)


async def test_a_repository_with_no_identity_trusts_no_author(trunk: Path) -> None:
    """Not an error and not a hole: with nothing to recognise, every edit is presented.

    The identity is stubbed rather than unset in the repository, because `git config
    --get` walks up to the machine's global config - which on a developer's host is
    exactly where the answer usually lives, and is why this reads the operator's real
    identity rather than only a per-repo override.
    """
    worktree = add_worktree(trunk, "alpha")
    commit_script(worktree, "echo more\nexit 0", "edit")

    async def anonymous(cwd: str, *args: str, **kwargs: object) -> tuple[int, str]:
        if args[:1] == ("config",):
            return 1, ""
        return await read_git(cwd, *args, **kwargs)  # type: ignore[arg-type]

    result = await read_verify_provenance(
        git=anonymous,
        worktree_root=str(worktree),
        project_root=str(trunk),
        source="convention",
        script_name=SCRIPT,
        trunk_ref="main",
    )
    assert result.verdict == "foreign_author"
    assert not result.trusted
    assert "user.email" in result.reason


async def test_an_unusable_trunk_ref_falls_back_to_who_last_touched_it(trunk: Path) -> None:
    """No range to ask over is not the same as no answer: the weaker question still names
    a foreign author, which is the one this check exists to catch."""
    worktree = add_worktree(trunk, "alpha")
    commit_script(worktree, "echo more\nexit 0", "edit", author="stranger@example.invalid")
    result = await provenance(worktree, trunk, trunk_ref="")
    assert result.verdict == "foreign_author"
    assert result.authors == ("stranger@example.invalid",)


async def test_git_that_cannot_answer_is_untrusted_rather_than_permissive(
    trunk: Path,
) -> None:
    """This runs to *widen* authority, so it fails closed."""
    worktree = add_worktree(trunk, "alpha")

    async def broken(_cwd: str, *_args: str, **_kwargs: object) -> tuple[int, str]:
        return 128, "fatal: not a git repository"

    result = await read_verify_provenance(
        git=broken,
        worktree_root=str(worktree),
        project_root=str(trunk),
        source="convention",
        script_name=SCRIPT,
        trunk_ref="main",
    )
    assert result.verdict == "unknown"
    assert not result.trusted


def test_both_halves_are_required_and_neither_is_redundant() -> None:
    local = VerifyProvenance("local_author", True, "written here")
    foreign = VerifyProvenance("foreign_author", False, "written elsewhere")
    assert verify_bypass_allowed("granted", local)
    # The switch without the provenance is the unattended-execution path.
    assert not verify_bypass_allowed("granted", foreign)
    # The provenance without the switch is a Project that asked to be asked.
    assert not verify_bypass_allowed("draft", local)
    assert not verify_bypass_allowed("", local)
