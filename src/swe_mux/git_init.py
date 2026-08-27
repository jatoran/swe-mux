"""First-time Git repository creation for a Project root.

The Git drawer tab is the one surface that reports on the repository behind a Project,
and on a Project whose folder is not a repository yet it had nothing to say but Git's
own ``fatal:``. This turns that dead end into the one action it implies: create the
repository, and write a starter ignore file chosen from what is actually in the folder.

Two boundaries this deliberately keeps:

* **Nothing is staged and no commit is made.** ``git init`` on a folder with work already
  in it is reversible - delete ``.git`` and the folder is exactly as it was - while a
  first commit that swept in a stray ``.env`` or a virtualenv is not, and no ignore file
  written from six filename probes is trustworthy enough to make that call for someone.
* **An existing ``.gitignore`` is never touched.** A folder can carry one long before it
  carries a repository, and it is the user's file either way.

The mutation itself goes through ``git_operations`` like every other daemon-owned Git
write, so a browser that disconnects mid-init cannot cancel it.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .bounded_subprocess import run_bounded
from .git_operations import run_git_mutation
from .project_files import ensure_project_local_data_ignored

log = logging.getLogger(__name__)

# `git init` is a handful of file creations. The worktree deadline is for operations that
# walk a dependency tree; giving this one the same 30 minutes would only mean a wedged
# call hangs the request that long.
GIT_INIT_TIMEOUT_SECONDS = 60.0
GIT_SETUP_READ_TIMEOUT_SECONDS = 4.0
GIT_SETUP_READ_LIMIT = 64 * 1024
ROOT_SWE_MUX_IGNORE_RULE = "/.swe-mux/"
ROOT_SWE_MUX_IGNORE_BLOCK = (
    "# Keep all swe-mux Project files local to this checkout.\n"
    f"{ROOT_SWE_MUX_IGNORE_RULE}\n"
)

# swe-mux's fallback default branch, used only where the user has expressed no preference
# of their own through `init.defaultBranch`.
DEFAULT_BRANCH = "main"

_HEADER = """\
# Written by swe-mux when this Project's repository was created.
# It is an ordinary file from here on - edit it freely; swe-mux never rewrites it.
"""

_ALWAYS = """\
# Secrets and local environment
.env
.env.*
!.env.example
*.pem
*.key

# Operating system
.DS_Store
Thumbs.db
desktop.ini

# Editors and tools
.idea/
*.swp
*~
"""

# Ordered so the generated file reads the way a hand-written one would: the language the
# folder is actually in, then the rest. Each entry is (marker filenames, section body).
_STACKS: tuple[tuple[tuple[str, ...], str], ...] = (
    (
        ("package.json", "package-lock.json", "pnpm-lock.yaml", "yarn.lock", "node_modules"),
        """\
# Node
node_modules/
dist/
build/
coverage/
.cache/
*.log
""",
    ),
    (
        ("pyproject.toml", "setup.py", "setup.cfg", "requirements.txt", "Pipfile", ".venv", "venv"),
        """\
# Python
__pycache__/
*.py[cod]
.venv/
venv/
*.egg-info/
.mypy_cache/
.pytest_cache/
.ruff_cache/
""",
    ),
    (("Cargo.toml",), "# Rust\n/target/\n"),
    (("go.mod",), "# Go\n/bin/\n"),
)


@dataclass(slots=True, frozen=True)
class RepositoryInitResult:
    root: str
    branch: str
    #: ``created`` when this wrote one, ``existing`` when the folder already had one.
    gitignore: str
    swe_mux_ignore: str


@dataclass(slots=True, frozen=True)
class RepositorySetupStatus:
    tracked: bool
    ignored: bool


def _detected_sections(root: Path) -> list[str]:
    sections: list[str] = []
    for markers, body in _STACKS:
        if any((root / marker).exists() for marker in markers):
            sections.append(body)
    return sections


def starter_gitignore(root: Path) -> str:
    """The ignore file this Project's folder would get, without writing anything."""

    return "\n".join([_HEADER, *_detected_sections(root), _ALWAYS])


def _write_gitignore(root: Path) -> str:
    target = root / ".gitignore"
    if target.exists():
        return "existing"
    # `newline=""` keeps the LF endings the literals above carry, rather than letting the
    # Windows text layer rewrite them into CRLF in a file Git is about to read.
    with open(target, "w", encoding="utf-8", newline="") as handle:
        handle.write(starter_gitignore(root))
    return "created"


class RepositoryInitError(RuntimeError):
    """A Git command refused, with Git's own message attached."""


class RepositorySetupError(RepositoryInitError):
    """A typed refusal from the optional whole-directory ignore operation."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


async def _read_git(root: str, *args: str, operation_id: str) -> tuple[int, bytes]:
    try:
        outcome = await run_bounded(
            ["git", "-C", root, "--no-optional-locks", *args],
            label="git setup status",
            timeout_seconds=GIT_SETUP_READ_TIMEOUT_SECONDS,
            output_limit=GIT_SETUP_READ_LIMIT,
            cwd=root,
            operation_id=operation_id,
        )
    except OSError as exc:
        raise RepositoryInitError(f"could not start Git: {exc}") from exc
    if outcome.timed_out:
        raise RepositoryInitError("Git setup status timed out")
    if outcome.truncated:
        raise RepositoryInitError("Git setup status exceeded its output limit")
    return int(outcome.exit_code or 0), outcome.stdout


async def repository_setup_status(root: str, *, operation_id: str) -> RepositorySetupStatus:
    """Report whether ignoring all of ``.swe-mux`` is safe and still actionable."""

    tracked_code, tracked_output = await _read_git(
        root, "ls-files", "-z", "--", ".swe-mux", operation_id=operation_id
    )
    if tracked_code:
        raise RepositoryInitError("Git could not inspect tracked swe-mux files")
    ignore_code, _ = await _read_git(
        root,
        "check-ignore",
        "-q",
        "--no-index",
        "--",
        ".swe-mux/config.toml",
        operation_id=operation_id,
    )
    if ignore_code not in {0, 1}:
        raise RepositoryInitError("Git could not inspect swe-mux ignore rules")
    return RepositorySetupStatus(tracked=bool(tracked_output), ignored=ignore_code == 0)


def _append_root_swe_mux_ignore(root: Path, *, operation_id: str) -> None:
    target = root / ".gitignore"
    if target.is_symlink() or (target.exists() and not target.is_file()):
        raise RepositoryInitError("the Project's .gitignore is not a safe regular file")
    try:
        current = target.read_bytes() if target.exists() else b""
    except OSError as exc:
        raise RepositoryInitError(f"could not read the Project's .gitignore: {exc}") from exc
    newline = b"\r\n" if b"\r\n" in current else b"\n"
    block = ROOT_SWE_MUX_IGNORE_BLOCK.replace("\n", newline.decode("ascii")).encode("ascii")
    separator = b"" if not current or current.endswith((b"\n", b"\r")) else newline
    updated = current + separator + block
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=root, prefix=".gitignore.swe-mux-", suffix=".tmp", delete=False
        ) as handle:
            temporary = Path(handle.name)
            handle.write(updated)
            handle.flush()
            os.fsync(handle.fileno())
        if target.exists():
            shutil.copymode(target, temporary)
        os.replace(temporary, target)
        temporary = None
    except OSError as exc:
        raise RepositoryInitError(f"could not update the Project's .gitignore: {exc}") from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    log.info(
        "repository_swe_mux_ignored operation_id=%s root=%s gitignore=%s",
        operation_id,
        root,
        target,
    )


async def ignore_swe_mux(root: str, *, operation_id: str) -> str:
    """Append the root-anchored whole-directory rule after rechecking Git state."""

    status = await repository_setup_status(root, operation_id=operation_id)
    if status.tracked:
        raise RepositorySetupError(
            "swe_mux_tracked",
            "swe-mux files are already tracked; an ignore rule would not untrack them"
        )
    if status.ignored:
        return "already_ignored"
    await asyncio.shield(
        asyncio.to_thread(_append_root_swe_mux_ignore, Path(root), operation_id=operation_id)
    )
    return "added"


async def _resolved_branch(root: str, operation_id: str) -> str:
    result = await run_git_mutation(
        root,
        "symbolic-ref",
        "--short",
        "HEAD",
        operation="repository_init_branch",
        operation_id=operation_id,
        timeout_seconds=GIT_INIT_TIMEOUT_SECONDS,
    )
    # A brand-new repository has an unborn HEAD, which `symbolic-ref` still resolves;
    # only a detached one fails, and that cannot be the shape of a repository this
    # function just created.
    return result.output.strip() if not result.code else ""


async def initialize_repository(
    root: str, *, operation_id: str, ignore_swe_mux_files: bool = False
) -> RepositoryInitResult:
    """Create a repository at ``root`` and give it a starter ignore file.

    Raises ``RepositoryInitError`` if Git refuses. The ignore file is written only after
    Git succeeds, so a refused init leaves the folder untouched.
    """

    configured = await run_git_mutation(
        root,
        "config",
        "--get",
        "init.defaultBranch",
        operation="repository_init_default_branch",
        operation_id=operation_id,
        timeout_seconds=GIT_INIT_TIMEOUT_SECONDS,
    )
    # An explicit `init.defaultBranch` is the user's own answer, so plain `git init` is
    # the right call there. Otherwise name the branch, because Git's own fallback is
    # version-dependent and prints a paragraph of advice about it.
    user_named = not configured.code and bool(configured.output.strip())
    args = ("init",) if user_named else ("init", "-b", DEFAULT_BRANCH)
    result = await run_git_mutation(
        root,
        *args,
        operation="repository_init",
        operation_id=operation_id,
        timeout_seconds=GIT_INIT_TIMEOUT_SECONDS,
    )
    if result.code and len(args) > 1:
        # `-b` predates neither Git 2.28 nor any host swe-mux supports on paper, but the
        # cost of being wrong is a Project that cannot be initialized at all.
        result = await run_git_mutation(
            root,
            "init",
            operation="repository_init_unnamed",
            operation_id=operation_id,
            timeout_seconds=GIT_INIT_TIMEOUT_SECONDS,
        )
    if result.code:
        raise RepositoryInitError(result.output or "git init failed")
    try:
        await asyncio.to_thread(ensure_project_local_data_ignored, Path(root))
    except (OSError, ValueError) as exc:
        raise RepositoryInitError(f"could not initialize swe-mux ignore rules: {exc}") from exc
    gitignore = await asyncio.to_thread(_write_gitignore, Path(root))
    swe_mux_ignore = (
        await ignore_swe_mux(root, operation_id=operation_id)
        if ignore_swe_mux_files
        else "not_requested"
    )
    branch = await _resolved_branch(root, operation_id)
    log.info(
        "repository_initialized operation_id=%s root=%s branch=%s gitignore=%s "
        "swe_mux_ignore=%s",
        operation_id,
        root,
        branch,
        gitignore,
        swe_mux_ignore,
    )
    return RepositoryInitResult(
        root=root,
        branch=branch or DEFAULT_BRANCH,
        gitignore=gitignore,
        swe_mux_ignore=swe_mux_ignore,
    )
