from __future__ import annotations

import asyncio
import difflib
import hashlib
import logging
import os
import re
import stat
import time
from collections import OrderedDict
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Literal, NotRequired, TypedDict

from .subprocess_flags import background_creation_flags, reap_process_tree

log = logging.getLogger(__name__)

GIT_TIMEOUT_SECONDS = 4.0
# Concurrent git subprocesses per overview. The reads are independent per-worktree
# queries, so the bound is process-spawn pressure rather than correctness; at 4, a
# 25-worktree map cost ~0.7s of pure spawn serialization per request (measured
# 2026-08-22), and 12 takes the same map to roughly a quarter of that on the
# 16-core reference host without saturating it.
GIT_CONCURRENCY = 12
GIT_CHANGE_FILE_LIMIT = 200
GIT_UNTRACKED_MEASURE_MAX_BYTES = 16 * 1024 * 1024
GIT_DIFF_MAX_BYTES = 1024 * 1024
GIT_DIFF_MAX_LINES = 10_000
GIT_COMPARE_REF_MAX_CHARS = 200
GIT_COMPARE_CANDIDATE_LIMIT = 200
# A commit message is prose written to be read, so the whole of it is served rather than a
# subject line - the expanded commit row is the one surface with room for it. The cap is a
# guard against a pathological commit (a generated changelog, a squashed import), not a
# display budget: no message a human wrote comes near it.
GIT_COMMIT_MESSAGE_MAX_CHARS = 16_384
_OID = re.compile(r"[0-9a-fA-F]{40,64}\Z")
_UNMERGED = frozenset({"DD", "AU", "UD", "UA", "DU", "AA", "UU"})

GitScope = Literal["unstaged", "staged", "conflicted", "branch", "commit"]


class GitReviewError(ValueError):
    def __init__(self, code: str, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


class GitFileChange(TypedDict, total=False):
    path: str
    old_path: str
    status: str
    additions: int | None
    deletions: int | None
    binary: bool
    submodule: bool
    current_exists: bool


class GitChangeSummary(TypedDict):
    total: int
    additions: int
    deletions: int
    binary_files: int
    files: list[GitFileChange]
    truncated: bool
    #: Present and true only under `detail=summary`: the counts are real and the file
    #: list was withheld. A reader must be able to tell that from an empty change set.
    files_omitted: NotRequired[bool]


class GitComparisonRef(TypedDict):
    """Which ref a repository is compared against, and how that was decided.

    Separate from `GitComparison` because two callers need the answer at very
    different costs: the drawer also wants the bounded selector candidate list,
    while the session monitor polls every checkout on a cadence and must not run
    `for-each-ref` to learn a name it already had. One resolution, two callers,
    so the sidebar and the drawer can never disagree about the base.
    """

    ref: str | None
    display: str | None
    source: str
    available: bool
    reason: str | None


class GitComparison(GitComparisonRef):
    candidates: list[str]


@dataclass(slots=True, frozen=True)
class GitResult:
    code: int
    stdout: bytes
    stderr: bytes
    timed_out: bool = False
    too_large: bool = False

    @property
    def message(self) -> str:
        return (self.stderr or self.stdout).decode("utf-8", "replace").strip()


def _log_result(
    operation: str,
    started: float,
    *,
    project_id: str,
    repository: str,
    result: str,
    worktree: str | None = None,
    scope: str | None = None,
    ref: str | None = None,
    oid: str | None = None,
    path: str | None = None,
    count: int | None = None,
    truncated: bool | None = None,
) -> None:
    # Identifiers and result metadata only. Patch bodies and file contents are never logged.
    log.info(
        "git_review operation=%s project_id=%s repository=%s worktree=%s scope=%s "
        "ref=%s oid=%s path=%s result=%s count=%s truncated=%s duration_ms=%.1f",
        operation,
        project_id,
        repository,
        worktree or "",
        scope or "",
        ref or "",
        oid or "",
        path or "",
        result,
        "" if count is None else count,
        "" if truncated is None else truncated,
        (time.monotonic() - started) * 1000,
    )


async def _run_git_bytes(
    cwd: str | Path,
    *args: str,
    timeout_seconds: float = GIT_TIMEOUT_SECONDS,
) -> GitResult:
    try:
        process = await asyncio.create_subprocess_exec(
            "git",
            # Read-only by contract (this module performs no Git mutations), and this
            # is what makes that true of the *repository* rather than only of the
            # caller's intent: `status` and `diff` refresh the index and write it back
            # whenever a tracked file's mtime has moved, taking `.git/index.lock` to do
            # it. See `git_monitor._git` for the measurement and the stranded-lock
            # failure that causes. Output is unaffected.
            "--no-optional-locks",
            "-C",
            str(cwd),
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            creationflags=background_creation_flags(),
        )
    except OSError as exc:
        return GitResult(1, b"", str(exc).encode("utf-8", "replace"))
    try:
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout_seconds)
        except TimeoutError:
            await reap_process_tree(process)
            return GitResult(124, b"", b"git command timed out", timed_out=True)
        return GitResult(process.returncode or 0, stdout, stderr)
    finally:
        if process.returncode is None:
            await reap_process_tree(process)


async def _run_patch(
    cwd: str | Path,
    *args: str,
    timeout_seconds: float = GIT_TIMEOUT_SECONDS,
) -> GitResult:
    """Read patch stdout incrementally and stop the child before an oversized allocation."""
    try:
        process = await asyncio.create_subprocess_exec(
            "git",
            # Read-only by contract (this module performs no Git mutations), and this
            # is what makes that true of the *repository* rather than only of the
            # caller's intent: `status` and `diff` refresh the index and write it back
            # whenever a tracked file's mtime has moved, taking `.git/index.lock` to do
            # it. See `git_monitor._git` for the measurement and the stranded-lock
            # failure that causes. Output is unaffected.
            "--no-optional-locks",
            "-C",
            str(cwd),
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            creationflags=background_creation_flags(),
        )
    except OSError as exc:
        return GitResult(1, b"", str(exc).encode("utf-8", "replace"))

    async def collect() -> GitResult:
        assert process.stdout is not None
        assert process.stderr is not None
        stderr_reader = process.stderr

        async def drain_stderr() -> bytes:
            kept = bytearray()
            # unsupervised-loop-ok: bounded reader owned by one Git subprocess.
            while chunk := await stderr_reader.read(16 * 1024):
                if len(kept) < 64 * 1024:
                    kept.extend(chunk[: 64 * 1024 - len(kept)])
            return bytes(kept)

        stderr_task = asyncio.create_task(drain_stderr())
        try:
            chunks: list[bytes] = []
            total = 0
            lines = 0
            too_large = False
            # unsupervised-loop-ok: bounded reader owned by one Git subprocess.
            while True:
                chunk = await process.stdout.read(64 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                lines += chunk.count(b"\n")
                if total > GIT_DIFF_MAX_BYTES or lines > GIT_DIFF_MAX_LINES:
                    too_large = True
                    await reap_process_tree(process)
                    break
                chunks.append(chunk)
            if process.returncode is None:
                await process.wait()
            stderr = await stderr_task
            return GitResult(
                process.returncode or 0,
                b"" if too_large else b"".join(chunks),
                stderr,
                too_large=too_large,
            )
        finally:
            if not stderr_task.done():
                stderr_task.cancel()
                await asyncio.gather(stderr_task, return_exceptions=True)

    try:
        try:
            return await asyncio.wait_for(collect(), timeout=timeout_seconds)
        except TimeoutError:
            await reap_process_tree(process)
            return GitResult(124, b"", b"git command timed out", timed_out=True)
    finally:
        if process.returncode is None:
            await reap_process_tree(process)


def _require_success(result: GitResult, operation: str, *, not_found: bool = False) -> bytes:
    if result.timed_out:
        raise GitReviewError("git_timeout", f"Git timed out while {operation}", 504)
    if result.code:
        raise GitReviewError(
            "git_not_found" if not_found else "git_error",
            result.message or f"Git failed while {operation}",
            404 if not_found else 400,
        )
    return result.stdout


async def repository_identity(project_root: str | Path) -> tuple[str, str]:
    root_result, common_result = await asyncio.gather(
        _run_git_bytes(project_root, "rev-parse", "--show-toplevel"),
        _run_git_bytes(project_root, "rev-parse", "--git-common-dir"),
    )
    # "This folder is not a repository yet" is a state the Git tab offers an action for
    # (`git_init.initialize_repository`), so it has to arrive as its own code rather than
    # as Git's generic `fatal:`. Narrow on purpose: 128 is Git's fatal exit, so a missing
    # binary (1) and a timeout (124) fall through, and requiring the folder to exist with
    # no `.git` of its own keeps a corrupt or unreadable repository out of it - offering
    # to initialize one of those would reinitialize a repository the user still has.
    if (
        root_result.code == 128
        and Path(project_root).is_dir()
        and not (Path(project_root) / ".git").exists()
    ):
        raise GitReviewError(
            "not_git_repository", "Project folder is not a Git repository", 404
        )
    root = (
        _require_success(root_result, "resolving the repository").decode("utf-8", "replace").strip()
    )
    common_raw = (
        _require_success(common_result, "resolving the Git common directory")
        .decode("utf-8", "replace")
        .strip()
    )
    if not root:
        raise GitReviewError("not_git_repository", "Project is not inside a Git repository")
    common = Path(common_raw)
    if not common.is_absolute():
        common = Path(root) / common
    return str(Path(root).resolve()), str(common.resolve())


def parse_worktrees(output: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    current: dict[str, Any] = {}
    for line in [*output.splitlines(), ""]:
        if not line:
            if current:
                items.append(current)
                current = {}
        elif " " in line:
            key, value = line.split(" ", 1)
            current[key] = value
        else:
            current[line] = True
    return items


async def listed_worktrees(repository: str | Path) -> list[dict[str, Any]]:
    result = await _run_git_bytes(repository, "worktree", "list", "--porcelain")
    return parse_worktrees(
        _require_success(result, "listing repository worktrees").decode("utf-8", "replace")
    )


async def validate_worktree_root(repository: str | Path, requested: str) -> str:
    if not requested or not Path(requested).is_absolute():
        raise GitReviewError("invalid_worktree", "worktree must be an absolute path")
    try:
        resolved = str(Path(requested).resolve())
    except OSError as exc:
        raise GitReviewError("invalid_worktree", f"worktree is unavailable: {exc}") from exc
    for item in await listed_worktrees(repository):
        value = item.get("worktree")
        if isinstance(value, str) and os.path.normcase(
            str(Path(value).resolve())
        ) == os.path.normcase(resolved):
            return value
    raise GitReviewError(
        "worktree_not_found",
        "path is not an exact worktree root for this Project repository",
        404,
    )


def validate_relative_path(value: str) -> str:
    if not value or "\x00" in value or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise GitReviewError("invalid_path", "path is empty or contains control characters")
    normalized = value.replace("\\", "/")
    path = PurePosixPath(normalized)
    if (
        path.is_absolute()
        or normalized.startswith("/")
        or re.match(r"^[A-Za-z]:/", normalized)
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise GitReviewError(
            "invalid_path", "path must be a repository-relative path without traversal"
        )
    return path.as_posix()


async def _ref_resolves(repository: str, ref: str) -> bool:
    if (
        not ref
        or len(ref) > GIT_COMPARE_REF_MAX_CHARS
        or any(ord(char) < 32 or ord(char) == 127 for char in ref)
        or ref.startswith("-")
    ):
        return False
    checked = await _run_git_bytes(repository, "check-ref-format", "--branch", ref)
    if checked.code:
        return False
    resolved = await _run_git_bytes(repository, "rev-parse", "--verify", f"{ref}^{{commit}}")
    return resolved.code == 0 and bool(resolved.stdout.strip())


async def comparison_candidates(repository: str) -> list[str]:
    result = await _run_git_bytes(
        repository,
        "for-each-ref",
        "--format=%(refname:short)",
        "refs/heads/",
        "refs/remotes/",
    )
    if result.code:
        return []
    candidates: list[str] = []
    for line in result.stdout.decode("utf-8", "replace").splitlines():
        candidate = line.strip()
        if not candidate or candidate.endswith("/HEAD") or candidate in candidates:
            continue
        candidates.append(candidate)
        if len(candidates) >= GIT_COMPARE_CANDIDATE_LIMIT:
            break
    return candidates


async def resolve_comparison_ref(repository: str, override: str | None) -> GitComparisonRef:
    """Auto (or overridden) comparison ref for one repository, without candidates.

    An explicit override that no longer resolves stays visibly unavailable rather
    than silently falling back to the inferred ref: a comparison against a base
    the user did not choose is worse than no comparison at all.
    """
    if override is not None:
        if await _ref_resolves(repository, override):
            return {
                "ref": override,
                "display": override,
                "source": "project_override",
                "available": True,
                "reason": None,
            }
        return {
            "ref": None,
            "display": override,
            "source": "project_override",
            "available": False,
            "reason": "configured comparison ref is invalid or no longer resolves to a commit",
        }

    origin = await _run_git_bytes(
        repository, "symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD"
    )
    origin_ref = origin.stdout.decode("utf-8", "replace").strip()
    if not origin.code and origin_ref and await _ref_resolves(repository, origin_ref):
        return {
            "ref": origin_ref,
            "display": origin_ref,
            "source": "origin_head",
            "available": True,
            "reason": None,
        }

    remotes_result = await _run_git_bytes(repository, "remote")
    remotes = remotes_result.stdout.decode("utf-8", "replace").splitlines()
    if not remotes_result.code and "origin" not in remotes and len(remotes) == 1:
        remote_ref_result = await _run_git_bytes(
            repository,
            "symbolic-ref",
            "--quiet",
            "--short",
            f"refs/remotes/{remotes[0]}/HEAD",
        )
        remote_ref = remote_ref_result.stdout.decode("utf-8", "replace").strip()
        if (
            not remote_ref_result.code
            and remote_ref
            and await _ref_resolves(repository, remote_ref)
        ):
            return {
                "ref": remote_ref,
                "display": remote_ref,
                "source": "single_remote_head",
                "available": True,
                "reason": None,
            }

    for fallback in ("main", "master"):
        if await _ref_resolves(repository, fallback):
            return {
                "ref": fallback,
                "display": fallback,
                "source": "local_fallback",
                "available": True,
                "reason": None,
            }
    return {
        "ref": None,
        "display": None,
        "source": "none",
        "available": False,
        "reason": "no symbolic remote default or local main/master ref resolves",
    }


async def infer_comparison(repository: str, override: str | None) -> GitComparison:
    candidates, resolved = await asyncio.gather(
        comparison_candidates(repository),
        resolve_comparison_ref(repository, override),
    )
    return {**resolved, "candidates": candidates}


def parse_name_status(data: bytes) -> list[GitFileChange]:
    tokens = data.decode("utf-8", "replace").split("\0")
    files: list[GitFileChange] = []
    index = 0
    while index < len(tokens):
        status_code = tokens[index]
        index += 1
        if not status_code or index >= len(tokens):
            continue
        first = tokens[index]
        index += 1
        if not first:
            continue
        item: GitFileChange = {
            "path": first,
            "status": status_code,
            "additions": None,
            "deletions": None,
            "binary": False,
            "submodule": False,
        }
        if status_code[:1] in {"R", "C"} and index < len(tokens) and tokens[index]:
            item["old_path"] = first
            item["path"] = tokens[index]
            index += 1
        files.append(item)
    return files


def parse_numstat(data: bytes) -> dict[str, tuple[int | None, int | None, bool, str | None]]:
    tokens = data.decode("utf-8", "replace").split("\0")
    result: dict[str, tuple[int | None, int | None, bool, str | None]] = {}
    index = 0
    while index < len(tokens):
        record = tokens[index]
        index += 1
        if not record:
            continue
        parts = record.split("\t", 2)
        if len(parts) != 3:
            continue
        added, deleted, path = parts
        old_path: str | None = None
        if not path and index + 1 < len(tokens):
            old_path = tokens[index]
            path = tokens[index + 1]
            index += 2
        if not path:
            continue
        binary = added == "-" or deleted == "-"
        result[path] = (
            None if binary or not added.isdigit() else int(added),
            None if binary or not deleted.isdigit() else int(deleted),
            binary,
            old_path,
        )
    return result


def _apply_numstat(
    files: list[GitFileChange],
    stats: dict[str, tuple[int | None, int | None, bool, str | None]],
) -> list[GitFileChange]:
    for item in files:
        measured = stats.get(item["path"])
        if measured is None:
            continue
        additions, deletions, binary, old_path = measured
        item["additions"] = additions
        item["deletions"] = deletions
        item["binary"] = binary
        if old_path and "old_path" not in item:
            item["old_path"] = old_path
    return files


def parse_raw_submodules(data: bytes) -> set[str]:
    """Return destination paths whose old or new raw-diff mode is a Git link."""
    tokens = data.decode("utf-8", "replace").split("\0")
    result: set[str] = set()
    index = 0
    while index < len(tokens):
        metadata = tokens[index]
        index += 1
        if not metadata.startswith(":") or index >= len(tokens):
            continue
        fields = metadata[1:].split()
        if len(fields) < 5:
            continue
        status_code = fields[4]
        first = tokens[index]
        index += 1
        path = first
        if status_code[:1] in {"R", "C"} and index < len(tokens):
            path = tokens[index]
            index += 1
        if path and (fields[0] == "160000" or fields[1] == "160000"):
            result.add(path)
    return result


def _apply_submodules(files: list[GitFileChange], paths: set[str]) -> list[GitFileChange]:
    for item in files:
        if item["path"] in paths:
            item["submodule"] = True
    return files


def change_summary(files: list[GitFileChange]) -> GitChangeSummary:
    return {
        "total": len(files),
        "additions": sum(item["additions"] or 0 for item in files),
        "deletions": sum(item["deletions"] or 0 for item in files),
        "binary_files": sum(bool(item["binary"]) for item in files),
        "files": files[:GIT_CHANGE_FILE_LIMIT],
        "truncated": len(files) > GIT_CHANGE_FILE_LIMIT,
    }


def parse_porcelain_v2(
    data: bytes,
) -> tuple[list[GitFileChange], list[GitFileChange], list[GitFileChange]]:
    tokens = data.decode("utf-8", "replace").split("\0")
    staged: list[GitFileChange] = []
    unstaged: list[GitFileChange] = []
    conflicted: list[GitFileChange] = []
    index = 0
    while index < len(tokens):
        record = tokens[index]
        index += 1
        if not record:
            continue
        kind = record[:1]
        old_path: str | None = None
        submodule = False
        if kind == "?":
            path = record[2:]
            if path:
                unstaged.append(
                    {
                        "path": path,
                        "status": "??",
                        "additions": None,
                        "deletions": None,
                        "binary": False,
                        "submodule": False,
                    }
                )
            continue
        if kind == "1":
            fields = record.split(" ", 8)
            if len(fields) != 9:
                continue
            xy, submodule, path = fields[1], fields[2] != "N...", fields[8]
        elif kind == "2":
            fields = record.split(" ", 9)
            if len(fields) != 10:
                continue
            xy, submodule, path = fields[1], fields[2] != "N...", fields[9]
            if index < len(tokens):
                old_path = tokens[index] or None
                index += 1
        elif kind == "u":
            fields = record.split(" ", 10)
            if len(fields) != 11:
                continue
            xy, path = fields[1], fields[10]
            submodule = fields[2] != "N..."
        else:
            continue
        if not path:
            continue
        base: GitFileChange = {
            "path": path,
            "status": xy,
            "additions": None,
            "deletions": None,
            "binary": False,
            "submodule": submodule,
        }
        if old_path:
            base["old_path"] = old_path
        if kind == "u" or xy in _UNMERGED or "U" in xy:
            conflicted.append(base)
            continue
        if len(xy) >= 1 and xy[0] not in {".", " "}:
            staged.append({**base, "status": xy[0]})
        if len(xy) >= 2 and xy[1] not in {".", " "}:
            unstaged.append({**base, "status": xy[1]})
    return staged, unstaged, conflicted


def _measure_untracked(
    worktree: str, item: GitFileChange, remaining_bytes: int
) -> int:
    if remaining_bytes <= 0:
        return 0
    root = Path(worktree).resolve()
    target = root.joinpath(*PurePosixPath(item["path"]).parts)
    try:
        target_stat = target.lstat()
        resolved = target.resolve()
        if stat.S_ISLNK(target_stat.st_mode) or not resolved.is_relative_to(root):
            return 0
        read_limit = min(GIT_DIFF_MAX_BYTES, remaining_bytes)
        if not stat.S_ISREG(target_stat.st_mode) or target_stat.st_size > read_limit:
            return 0
        with resolved.open("rb") as handle:
            data = handle.read(read_limit + 1)
        if len(data) > read_limit:
            return 0
        if b"\0" in data:
            item["binary"] = True
            return len(data)
        item["additions"] = data.count(b"\n") + int(bool(data) and not data.endswith(b"\n"))
        item["deletions"] = 0
        return len(data)
    except OSError:
        return 0


async def _diff_summary(worktree: str, revision_args: list[str]) -> GitChangeSummary | None:
    name_task = _run_git_bytes(
        worktree,
        "diff",
        "--no-ext-diff",
        "--no-textconv",
        "--no-color",
        "--find-renames",
        "--name-status",
        "-z",
        *revision_args,
    )
    stat_task = _run_git_bytes(
        worktree,
        "diff",
        "--no-ext-diff",
        "--no-textconv",
        "--no-color",
        "--find-renames",
        "--numstat",
        "-z",
        *revision_args,
    )
    raw_task = _run_git_bytes(
        worktree,
        "diff",
        "--no-ext-diff",
        "--no-textconv",
        "--no-color",
        "--find-renames",
        "--raw",
        "-z",
        *revision_args,
    )
    name_result, stat_result, raw_result = await asyncio.gather(name_task, stat_task, raw_task)
    if name_result.code or stat_result.code or raw_result.code:
        return None
    return change_summary(
        _apply_submodules(
            _apply_numstat(
                parse_name_status(name_result.stdout), parse_numstat(stat_result.stdout)
            ),
            parse_raw_submodules(raw_result.stdout),
        )
    )


def _needs_line_counts(files: list[GitFileChange]) -> bool:
    """Whether any of these files has line counts `git diff --numstat` could supply.

    Untracked files are measured from their own bytes and never appear in a diff, so a
    change set that is only untracked files has nothing to ask `diff` about. A *clean*
    scope has nothing at all. Both used to spawn a `git diff` anyway - two per worktree,
    unconditionally, on a Map that at fifty checkouts is mostly clean ones.
    """
    return any(item["status"] != "??" for item in files)


async def _local_summaries(
    worktree: str,
    status_result: GitResult | None = None,
) -> tuple[GitChangeSummary | None, GitChangeSummary | None, GitChangeSummary | None]:
    """The three working-tree summaries, from one `status` read.

    `status_result` lets a caller that already ran the status hand it in rather than
    have it run twice: the overview needs the same bytes to decide whether its memo of
    this checkout is still valid, and running `status` for the memo and again for the
    summaries would spend the process the memo exists to save.
    """
    if status_result is None:
        status_result = await _run_git_bytes(
            worktree, "status", "--porcelain=v2", "-z", "--untracked-files=all"
        )
    if status_result.code:
        return None, None, None
    staged, unstaged, conflicted = parse_porcelain_v2(status_result.stdout)
    stat_tasks: dict[str, asyncio.Task[GitResult]] = {}
    if _needs_line_counts(staged):
        stat_tasks["staged"] = asyncio.create_task(
            _run_git_bytes(
                worktree,
                "diff",
                "--cached",
                "--no-ext-diff",
                "--no-textconv",
                "--no-color",
                "--find-renames",
                "--numstat",
                "-z",
            )
        )
    if _needs_line_counts(unstaged):
        stat_tasks["unstaged"] = asyncio.create_task(
            _run_git_bytes(
                worktree,
                "diff",
                "--no-ext-diff",
                "--no-textconv",
                "--no-color",
                "--find-renames",
                "--numstat",
                "-z",
            )
        )
    if stat_tasks:
        await asyncio.gather(*stat_tasks.values())
    staged_stats = stat_tasks["staged"].result() if "staged" in stat_tasks else None
    unstaged_stats = stat_tasks["unstaged"].result() if "unstaged" in stat_tasks else None
    if staged_stats is not None and not staged_stats.code:
        _apply_numstat(staged, parse_numstat(staged_stats.stdout))
    if unstaged_stats is not None and not unstaged_stats.code:
        _apply_numstat(unstaged, parse_numstat(unstaged_stats.stdout))
    remaining_untracked_bytes = GIT_UNTRACKED_MEASURE_MAX_BYTES
    # Only returned files need content-derived line counts. Inspecting every path before
    # applying the response limit turns a damaged ignore file into an unbounded filesystem
    # crawl, exactly when the Map needs to remain available for recovery.
    for item in unstaged[:GIT_CHANGE_FILE_LIMIT]:
        if item["status"] == "??":
            measured = await asyncio.to_thread(
                _measure_untracked, worktree, item, remaining_untracked_bytes
            )
            remaining_untracked_bytes -= measured
            if remaining_untracked_bytes <= 0:
                break
    return change_summary(unstaged), change_summary(staged), change_summary(conflicted)


async def head_commit_dates(
    repository: str | Path, oids: Sequence[str]
) -> dict[str, int]:
    """Committer date (Unix seconds) of each given commit, keyed by the oid asked for.

    This is how "when was this worktree last active" is answered, and the alternative
    is a trap rather than a shortcut: **the worktree directory's mtime is not usable
    on Windows.** Windows freezes ``st_mtime`` on a file while a handle is open, so a
    checkout an agent is actively working in reports a timestamp hours stale - every
    Win32 API agrees with it, and a naive repro says it does not happen. The busiest
    tree would sort as the most dormant one, which is exactly backwards.

    The branch tip's committer date has no such problem: it is recorded in the commit
    object, comes from the shared object database, and needs no access to the worktree
    directory at all - so a locked, prunable, or unreachable checkout still reports it.

    One batched ``git show``: the oids come from Git's own worktree list, so a partial
    read means real repository damage rather than an ordinary miss, and the caller
    treats every absent oid as unmeasured.
    """
    unique = list(dict.fromkeys(oid for oid in oids if _OID.fullmatch(oid or "")))
    if not unique:
        return {}
    result = await _run_git_bytes(repository, "show", "-s", "--format=%H %ct", *unique)
    dates: dict[str, int] = {}
    for line in result.stdout.decode("utf-8", "replace").splitlines():
        oid, _, stamp = line.strip().partition(" ")
        if not stamp.isdigit():
            continue
        dates[oid.lower()] = int(stamp)
    if result.code:
        log.warning(
            "git_review head_commit_dates partial repository=%s asked=%d got=%d stderr=%s",
            repository,
            len(unique),
            len(dates),
            result.stderr.decode("utf-8", "replace").strip()[:200],
        )
    # Keyed back by the spelling the caller passed, so a short-vs-full or cased oid
    # still finds its row.
    return {oid: dates[oid.lower()] for oid in unique if oid.lower() in dates}


#: (worktree, head, comparison oid) -> (ahead/behind counts, branch delta).
#:
#: The Map's expensive half, and the half that can be memoized *exactly*. Both readings
#: are commit-to-commit - `rev-list --left-right --count <ref>...HEAD` and a diff over
#: the same range - so nothing in a working tree can change either one. Given the same
#: two commits they are the same answer, and re-deriving it costs five `git`
#: subprocesses per checkout.
#:
#: That is the whole reason this is keyed on two object IDs rather than on time. At the
#: fifty worktrees this Project reached, the overview was spawning four hundred
#: processes per request and retaining nothing between them; an unattended checkout is
#: never polled, so a TTL would have been guessing about the one case it exists for.
#: Object IDs do not go stale - a tree whose HEAD has not moved has not moved.
_branch_memo: OrderedDict[
    tuple[str, str, str], tuple[dict[str, int] | None, GitChangeSummary | None]
] = OrderedDict()

#: Bounded well above any real fleet: one entry per (checkout, HEAD, base), so a
#: fifty-worktree Project churning branches still keeps every live tree memoized.
BRANCH_MEMO_LIMIT = 512


def reset_overview_cache() -> None:
    """Drop every memoized branch reading. For tests and daemon restart."""
    _branch_memo.clear()


def _memoized_branch(
    key: tuple[str, str, str],
) -> tuple[dict[str, int] | None, GitChangeSummary | None] | None:
    cached = _branch_memo.get(key)
    if cached is None:
        return None
    _branch_memo.move_to_end(key)
    return cached


def _memoize_branch(
    key: tuple[str, str, str],
    counts: dict[str, int] | None,
    delta: GitChangeSummary | None,
) -> None:
    # A reading Git refused to give is never memoized: a locked index or an interrupted
    # command would otherwise pin "unavailable" onto a checkout that is fine, until its
    # HEAD happened to move.
    if counts is None and delta is None:
        return
    _branch_memo[key] = (counts, delta)
    _branch_memo.move_to_end(key)
    while len(_branch_memo) > BRANCH_MEMO_LIMIT:
        _branch_memo.popitem(last=False)


def _same_path(listed: str, normalized: str) -> bool:
    """Whether a `git worktree list` path names the same directory as a normalized one.

    Resolved and case-folded, because Windows says both `D:\\PROJECTS` and `d:\\projects`
    for the same directory and a caller's spelling is whatever their client had.
    """
    try:
        candidate = os.path.normcase(os.path.normpath(str(Path(listed).resolve())))
    except OSError:
        candidate = os.path.normcase(os.path.normpath(listed))
    return candidate == normalized


async def _comparison_oid(repository: str, ref: str | None) -> str:
    """The commit a comparison ref names right now, or `''`.

    One process for the whole overview rather than one per worktree: every checkout is
    compared against the same base, and it is the *base moving* that has to invalidate
    fifty memoized branch readings at once.
    """
    if not ref:
        return ""
    result = await _run_git_bytes(repository, "rev-parse", "--verify", f"{ref}^{{commit}}")
    if result.code:
        return ""
    return result.stdout.decode("ascii", "replace").strip()


def _summary_view(summary: GitChangeSummary | None) -> GitChangeSummary | None:
    """The same summary with its file list withheld.

    Map rows render counts; the file list is read on expand and nowhere else. Serving
    up to four lists of two hundred records per worktree to draw a badge is the
    payload's real cost, and it is one the compression on the way out cannot recover
    (`network_usage.py`): gzip makes the bytes smaller, not absent.
    """
    if summary is None:
        return None
    withheld: GitChangeSummary = {**summary, "files": [], "files_omitted": True}
    return withheld


def summarize_overview(payload: dict[str, Any]) -> dict[str, Any]:
    """`detail=summary`: the overview with every per-file list withheld."""
    return {
        **payload,
        "detail": "summary",
        "worktrees": [
            {
                **row,
                **{
                    scope: _summary_view(row.get(scope))
                    for scope in ("unstaged", "staged", "conflicted", "branch_delta")
                },
            }
            for row in payload.get("worktrees", [])
        ],
    }


async def worktree_overview(
    project_id: str, project_root: str, compare_override: str | None, only: str | None = None
) -> dict[str, Any]:
    """Every listed worktree, or - with `only` - exactly one of them.

    `only` is what makes `detail=summary` usable rather than merely smaller: the Map
    ships counts, and a row that is expanded asks for its own file lists. Measuring one
    checkout is one checkout's worth of Git, which is the whole point of asking for one.
    """
    started = time.monotonic()
    repository, common_dir = await repository_identity(project_root)
    comparison = await infer_comparison(repository, compare_override)
    items = await listed_worktrees(repository)
    wanted: str | None = None
    if only is not None:
        try:
            wanted = os.path.normcase(os.path.normpath(str(Path(only).resolve())))
        except OSError:
            wanted = os.path.normcase(os.path.normpath(only))
        # A path Git does not list is not a worktree of this repository, whatever it is
        # on disk. Refused rather than measured, because the caller supplied it.
        if not any(
            isinstance(listed := item.get("worktree"), str)
            and _same_path(listed, wanted)
            for item in items
        ):
            raise GitReviewError("worktree_not_found", "unknown worktree for this Project", 404)
    compare_oid = await _comparison_oid(repository, comparison["ref"])
    semaphore = asyncio.Semaphore(GIT_CONCURRENCY)
    reused = 0

    # Tip dates are read for *every* listed tree, including the ones below that return
    # unmeasured: the commit object is in the shared database, so a locked or prunable
    # checkout can still say when its branch last moved, and a reader ordering trees by
    # activity should not have the damaged ones silently sink to the bottom.
    tip_dates = await head_commit_dates(
        repository,
        [head for item in items if isinstance(head := item.get("HEAD"), str)],
    )

    def unmeasured(row: dict[str, Any]) -> dict[str, Any]:
        row.update(
            comparison_counts=None,
            unstaged=None,
            staged=None,
            conflicted=None,
            branch_delta=None,
        )
        return row

    def _keep(item: dict[str, Any]) -> bool:
        if wanted is None:
            return True
        listed = item.get("worktree")
        return isinstance(listed, str) and _same_path(listed, wanted)

    async def measure(index: int, item: dict[str, Any]) -> dict[str, Any]:
        nonlocal reused
        row = dict(item)
        # Read off the position in the *full* listing even when only one row is being
        # served: `main` is "the first tree Git lists", and a single-row read that
        # renumbered from its own filtered list would call every checkout the main one.
        row["main"] = index == 0
        head = row.get("HEAD")
        # Explicit `None` rather than an absent key: "unmeasured" and "unborn branch"
        # both have to be distinguishable from "the field is not served yet".
        row["head_committed_at"] = tip_dates.get(head) if isinstance(head, str) else None
        worktree = row.get("worktree")
        if (
            not isinstance(worktree, str)
            or row.get("bare")
            or "prunable" in row
        ):
            return unmeasured(row)
        async with semaphore:
            top_level_result = await _run_git_bytes(worktree, "rev-parse", "--show-toplevel")
            if top_level_result.code:
                return unmeasured(row)
            reported_root = top_level_result.stdout.decode("utf-8", "replace").strip()
            try:
                exact_root = os.path.normcase(os.path.normpath(str(Path(worktree).resolve())))
                reported_exact_root = os.path.normcase(
                    os.path.normpath(str(Path(reported_root).resolve()))
                )
            except OSError:
                return unmeasured(row)
            if reported_exact_root != exact_root:
                log.warning(
                    "git_review worktree_identity_mismatch project_id=%s listed=%s reported=%s",
                    project_id,
                    worktree,
                    reported_root,
                )
                return unmeasured(row)
            # The branch half is keyed on two object IDs and is therefore either exactly
            # right or absent - never stale. The local half is read live every time,
            # because `status --porcelain=v2` carries no worktree blob hash: an edit
            # that leaves a file `.M` produces byte-identical status output with
            # different line counts, so a fingerprint taken from it would go quietly
            # wrong about the one number the row shows.
            memo_key = (exact_root, str(head or ""), compare_oid)
            compares = bool(comparison["available"] and comparison["ref"] and head)
            memoized = _memoized_branch(memo_key) if compares and compare_oid else None
            counts_task: asyncio.Task[GitResult] | None = None
            branch_task: asyncio.Task[GitChangeSummary | None] | None = None
            if compares and memoized is None:
                ref = comparison["ref"]
                counts_task = asyncio.create_task(
                    _run_git_bytes(worktree, "rev-list", "--left-right", "--count", f"{ref}...HEAD")
                )
                branch_task = asyncio.create_task(_diff_summary(worktree, [f"{ref}...HEAD"]))
            status_result = await _run_git_bytes(
                worktree, "status", "--porcelain=v2", "-z", "--untracked-files=all"
            )
            unstaged, staged, conflicted = await _local_summaries(worktree, status_result)
            row["unstaged"] = unstaged
            row["staged"] = staged
            row["conflicted"] = conflicted
            row["comparison_counts"] = None
            row["branch_delta"] = None
            if memoized is not None:
                reused += 1
                row["comparison_counts"], row["branch_delta"] = memoized
                return row
            counts: dict[str, int] | None = None
            if counts_task is not None:
                counts_result = await counts_task
                parts = counts_result.stdout.decode("ascii", "replace").replace("\t", " ").split()
                if (
                    not counts_result.code
                    and len(parts) == 2
                    and all(part.isdigit() for part in parts)
                ):
                    counts = {"behind": int(parts[0]), "ahead": int(parts[1])}
            delta = await branch_task if branch_task is not None else None
            row["comparison_counts"] = counts
            row["branch_delta"] = delta
            if compares and compare_oid:
                _memoize_branch(memo_key, counts, delta)
        return row

    worktrees = await asyncio.gather(
        *(measure(index, item) for index, item in enumerate(items) if _keep(item))
    )
    _log_result(
        "overview",
        started,
        project_id=project_id,
        repository=repository,
        ref=comparison["ref"],
        # How much of this answer was memoized, so "the Map got slow again" is a
        # question the log can answer rather than one that needs a profiler.
        result=f"ok reused={reused}/{len(worktrees)}",
        count=len(worktrees),
        truncated=any(
            bool(row.get(scope, {}).get("truncated"))
            for row in worktrees
            for scope in ("unstaged", "staged", "conflicted", "branch_delta")
            if isinstance(row.get(scope), dict)
        ),
    )
    return {
        "repository": {"root": repository, "common_dir": common_dir},
        "comparison": comparison,
        "worktrees": worktrees,
        # Stated rather than implied: a client that asked for `summary` and a client
        # that asked for nothing get different payloads, and a row that renders "0
        # files" is otherwise indistinguishable from one whose list was withheld.
        "detail": "full",
    }


_inflight_worktree_overviews: dict[
    tuple[str, str, str | None, str | None], asyncio.Task[dict[str, Any]]
] = {}


async def shared_worktree_overview(
    project_id: str, project_root: str, compare_override: str | None, only: str | None = None
) -> dict[str, Any]:
    """Share one in-flight Map computation across clients and refreshes.

    `only` is part of the key rather than folded away: a single-worktree read and a
    whole-Project read are different answers, and joining one onto the other would hand
    a row expansion the full inventory or - worse - hand the Map one row.
    """

    key = (project_id, project_root, compare_override, only)
    task = _inflight_worktree_overviews.get(key)
    if task is None or task.done():
        task = asyncio.create_task(
            worktree_overview(project_id, project_root, compare_override, only),
            name=f"git-overview:{project_id}",
        )
        _inflight_worktree_overviews[key] = task

        def forget(finished: asyncio.Task[dict[str, Any]]) -> None:
            if _inflight_worktree_overviews.get(key) is finished:
                _inflight_worktree_overviews.pop(key, None)
            if not finished.cancelled():
                finished.exception()

        task.add_done_callback(forget)
    else:
        log.info(
            "git_review overview_joined project_id=%s repository=%s",
            project_id,
            project_root,
        )
    return await asyncio.shield(task)


async def validate_commit(repository: str, oid: str) -> tuple[str, list[str]]:
    if not _OID.fullmatch(oid):
        raise GitReviewError("invalid_oid", "commit must be a full Git object ID")
    result = await _run_git_bytes(repository, "rev-list", "--parents", "-n", "1", oid)
    line = (
        _require_success(result, "resolving the commit", not_found=True)
        .decode("ascii", "replace")
        .strip()
    )
    fields = line.split()
    if not fields or fields[0].casefold() != oid.casefold():
        raise GitReviewError("commit_not_found", "commit no longer exists", 404)
    return fields[0], fields[1:]


def _selected_parent(parents: list[str], requested: str | None) -> str | None:
    if not parents:
        if requested:
            raise GitReviewError("invalid_parent", "an initial commit has no parent")
        return None
    selected = requested or parents[0]
    if not _OID.fullmatch(selected) or selected not in parents:
        raise GitReviewError("invalid_parent", "parent is not attached to the selected commit")
    return selected


async def commit_changes(
    project_id: str, project_root: str, oid: str, requested_parent: str | None
) -> dict[str, Any]:
    started = time.monotonic()
    repository, _common = await repository_identity(project_root)
    commit, parents = await validate_commit(repository, oid)
    parent = _selected_parent(parents, requested_parent)
    if parent is None:
        common = [
            "diff-tree",
            "--root",
            "--no-commit-id",
            "-r",
            "--no-ext-diff",
            "--no-textconv",
            "--no-color",
            "--find-renames",
        ]
        name_args = [*common, "--name-status", "-z", commit]
        stat_args = [*common, "--numstat", "-z", commit]
        raw_args = [*common, "--raw", "-z", commit]
        parent_label = "initial commit"
    else:
        common = [
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            "--no-color",
            "--find-renames",
        ]
        name_args = [*common, "--name-status", "-z", parent, commit]
        stat_args = [*common, "--numstat", "-z", parent, commit]
        raw_args = [*common, "--raw", "-z", parent, commit]
        parent_label = (
            "vs first parent" if len(parents) > 1 and parent == parents[0] else f"vs {parent[:8]}"
        )
    name_result, stat_result, raw_result, message_result = await asyncio.gather(
        _run_git_bytes(repository, *name_args),
        _run_git_bytes(repository, *stat_args),
        _run_git_bytes(repository, *raw_args),
        _run_git_bytes(repository, "log", "-1", "--format=%B", commit),
    )
    _require_success(name_result, "measuring commit files", not_found=True)
    _require_success(stat_result, "measuring commit statistics", not_found=True)
    _require_success(raw_result, "measuring commit entry modes", not_found=True)
    _require_success(message_result, "reading the commit message", not_found=True)
    summary = change_summary(
        _apply_submodules(
            _apply_numstat(
                parse_name_status(name_result.stdout), parse_numstat(stat_result.stdout)
            ),
            parse_raw_submodules(raw_result.stdout),
        )
    )
    project_path = Path(project_root)
    for file_change in summary["files"]:
        file_change["current_exists"] = (project_path / file_change["path"]).is_file()
    _log_result(
        "commit_changes",
        started,
        project_id=project_id,
        repository=repository,
        oid=commit,
        ref=parent,
        result="ok",
        count=summary["total"],
        truncated=summary["truncated"],
    )
    message = message_result.stdout.decode("utf-8", "replace").strip("\n")
    return {
        "commit": commit,
        "parent": parent,
        "parents": parents,
        "parent_label": parent_label,
        "message": message[:GIT_COMMIT_MESSAGE_MAX_CHARS],
        "summary": summary,
    }


#: The longest search pattern accepted, so an accidental paste cannot become a `git log`
#: argument of unbounded size. Far above any real query.
GIT_GRAPH_SEARCH_MAX_CHARS = 500


async def git_graph(
    project_id: str,
    project_root: str,
    limit: int,
    *,
    grep: str = "",
    author: str = "",
    regex: bool = False,
) -> dict[str, Any]:
    """A bounded commit graph, or - with a pattern - a bounded commit *search*.

    Searching is Git's own `--grep`/`--author`, not a filter over what was already
    fetched: the reason to search a log is to reach the commit that is *not* in the
    first eighty, and a client-side filter over a bounded page can only ever hide rows
    it already had.

    **`--graph` is dropped while filtering, deliberately.** Git draws lanes for a
    contiguous walk; over a filtered subset the ASCII it emits connects commits that
    have no such relationship, which is a picture of a DAG that does not exist. A
    filtered row therefore carries a bare node and no lanes, and the payload says so.

    Patterns are case-insensitive, and literal unless `regex` is asked for. That is the
    safe direction for a search box: `.` and `*` are ordinary characters in a commit
    subject, and a reader typing one means it.
    """
    started = time.monotonic()
    repository, _common = await repository_identity(project_root)
    grep = grep.strip()[:GIT_GRAPH_SEARCH_MAX_CHARS]
    author = author.strip()[:GIT_GRAPH_SEARCH_MAX_CHARS]
    filtering = bool(grep or author)
    probe = await _run_git_bytes(repository, "rev-list", "--all", "--max-count=1")
    _require_success(probe, "reading the commit graph")
    if not probe.stdout.strip():
        _log_result(
            "graph",
            started,
            project_id=project_id,
            repository=repository,
            result="ok",
            count=0,
            truncated=False,
        )
        return {"lines": [], "limit": limit, "has_more": False, "filtered": filtering}
    marker = "%x00%H%x00%P%x00%D%x00%an%x00%at%x00%s"
    search_args: list[str] = []
    if filtering:
        search_args.append("--regexp-ignore-case")
        if not regex:
            # Applies to `--grep`, `--author`, and `--committer` alike, which is why one
            # flag covers both fields.
            search_args.append("--fixed-strings")
        if grep:
            search_args.append(f"--grep={grep}")
        if author:
            search_args.append(f"--author={author}")
    result = await _run_git_bytes(
        repository,
        "log",
        *([] if filtering else ["--graph"]),
        "--date-order",
        "--decorate=short",
        "--all",
        *search_args,
        f"--max-count={limit + 1}",
        f"--format={marker}",
    )
    output = _require_success(result, "reading the commit graph").decode("utf-8", "replace")
    lines: list[dict[str, Any]] = []
    commits = 0
    has_more = False
    for raw in output.splitlines():
        if "\0" not in raw:
            if commits <= limit and raw:
                lines.append({"kind": "connector", "graph": raw})
            continue
        fields = raw.split("\0")
        if len(fields) != 7:
            continue
        if commits >= limit:
            has_more = True
            break
        graph, oid, parents, decorations, author, timestamp, subject = fields
        refs: list[str] = []
        for raw_ref in decorations.split(", "):
            label = raw_ref.strip()
            if label.startswith("HEAD -> "):
                refs.extend(["HEAD", label.removeprefix("HEAD -> ")])
            elif label:
                refs.append(label)
        lines.append(
            {
                "kind": "commit",
                # A bare node while filtering: `--graph` was not asked for, so there is
                # no prefix, and the row still has to draw *something* where every other
                # row draws its node.
                "graph": "* " if filtering else graph,
                "oid": oid,
                "parents": parents.split(),
                "refs": refs,
                "author": author,
                "committed_at": int(timestamp) if timestamp.isdigit() else 0,
                "subject": subject,
            }
        )
        commits += 1
    _log_result(
        "graph",
        started,
        project_id=project_id,
        repository=repository,
        result="ok",
        count=sum(line.get("kind") == "commit" for line in lines),
        truncated=has_more,
    )
    return {"lines": lines, "limit": limit, "has_more": has_more, "filtered": filtering}


async def _untracked_patch(worktree: str, path: str) -> GitResult:
    root = Path(worktree).resolve()
    target = root.joinpath(*PurePosixPath(path).parts)
    try:
        target_stat = target.lstat()
        resolved = target.resolve()
        if stat.S_ISLNK(target_stat.st_mode) or not resolved.is_relative_to(root):
            return GitResult(1, b"", b"untracked file escapes the worktree")
        if not stat.S_ISREG(target_stat.st_mode) or target_stat.st_size > GIT_DIFF_MAX_BYTES:
            return GitResult(0, b"", b"", too_large=target_stat.st_size > GIT_DIFF_MAX_BYTES)
        data = resolved.read_bytes()
    except OSError as exc:
        return GitResult(1, b"", str(exc).encode("utf-8", "replace"))
    if b"\0" in data:
        return GitResult(0, b"", b"")
    text = data.decode("utf-8", "replace").splitlines(keepends=True)
    patch = "".join(
        difflib.unified_diff([], text, fromfile="/dev/null", tofile=f"b/{path}", lineterm="\n")
    ).encode("utf-8")
    too_large = len(patch) > GIT_DIFF_MAX_BYTES or patch.count(b"\n") > GIT_DIFF_MAX_LINES
    return GitResult(0, b"" if too_large else patch, b"", too_large=too_large)


async def patch_snapshot(
    *,
    project_id: str,
    project_root: str,
    compare_override: str | None,
    scope: GitScope,
    path: str,
    worktree: str | None,
    commit: str | None,
    requested_parent: str | None,
    expected_head: str | None = None,
    expected_patch_hash: str | None = None,
) -> dict[str, Any]:
    started = time.monotonic()
    repository, _common = await repository_identity(project_root)
    relative_path = validate_relative_path(path)
    comparison_ref: str | None = None
    head_oid: str | None = None
    parent: str | None = None
    actual_commit: str | None = None
    output_worktree: str | None = None
    binary = False
    additions: int | None = None
    deletions: int | None = None
    old_path: str | None = None

    if scope == "commit":
        if worktree is not None or commit is None:
            raise GitReviewError(
                "invalid_scope_parameters", "commit scope requires commit and forbids worktree"
            )
        actual_commit, parents = await validate_commit(repository, commit)
        parent = _selected_parent(parents, requested_parent)
        if parent is None:
            args = [
                "show",
                "--root",
                "--format=",
                "--no-ext-diff",
                "--no-textconv",
                "--no-color",
                "--find-renames",
                actual_commit,
                "--",
                relative_path,
            ]
        else:
            args = [
                "diff",
                "--no-ext-diff",
                "--no-textconv",
                "--no-color",
                "--find-renames",
                parent,
                actual_commit,
                "--",
                relative_path,
            ]
        patch_result = await _run_patch(repository, *args)
        changes = await commit_changes(project_id, project_root, actual_commit, parent)
        matched = next(
            (item for item in changes["summary"]["files"] if item["path"] == relative_path), None
        )
    else:
        if commit is not None or requested_parent is not None or worktree is None:
            raise GitReviewError(
                "invalid_scope_parameters", "local scopes require worktree and forbid commit/parent"
            )
        exact_worktree = await validate_worktree_root(repository, worktree)
        output_worktree = exact_worktree
        head_result = await _run_git_bytes(exact_worktree, "rev-parse", "--verify", "HEAD")
        head_oid = (
            head_result.stdout.decode("ascii", "replace").strip() if not head_result.code else None
        )
        if expected_head is not None and expected_head != head_oid:
            raise GitReviewError("stale_snapshot", "working tree HEAD changed", 409)
        if scope == "unstaged":
            untracked = await _run_git_bytes(
                exact_worktree,
                "ls-files",
                "--others",
                "--exclude-standard",
                "-z",
                "--",
                relative_path,
            )
            if not untracked.code and relative_path in untracked.stdout.decode(
                "utf-8", "replace"
            ).split("\0"):
                patch_result = await _untracked_patch(exact_worktree, relative_path)
            else:
                patch_result = await _run_patch(
                    exact_worktree,
                    "diff",
                    "--no-ext-diff",
                    "--no-textconv",
                    "--no-color",
                    "--find-renames",
                    "--",
                    relative_path,
                )
        elif scope == "staged":
            patch_result = await _run_patch(
                exact_worktree,
                "diff",
                "--cached",
                "--no-ext-diff",
                "--no-textconv",
                "--no-color",
                "--find-renames",
                "--",
                relative_path,
            )
        elif scope == "conflicted":
            patch_result = await _run_patch(
                exact_worktree,
                "diff",
                "--cc",
                "--no-ext-diff",
                "--no-textconv",
                "--no-color",
                "--",
                relative_path,
            )
        else:
            comparison = await infer_comparison(repository, compare_override)
            if not comparison["available"] or not comparison["ref"]:
                raise GitReviewError(
                    "comparison_unavailable",
                    comparison["reason"] or "comparison ref unavailable",
                    404,
                )
            comparison_ref = comparison["ref"]
            patch_result = await _run_patch(
                exact_worktree,
                "diff",
                "--no-ext-diff",
                "--no-textconv",
                "--no-color",
                "--find-renames",
                f"{comparison_ref}...HEAD",
                "--",
                relative_path,
            )
        local = await worktree_overview(project_id, project_root, compare_override)
        row = next(
            (
                item
                for item in local["worktrees"]
                if os.path.normcase(str(Path(item.get("worktree", "")).resolve()))
                == os.path.normcase(str(Path(exact_worktree).resolve()))
            ),
            None,
        )
        summary_name = "branch_delta" if scope == "branch" else scope
        summary = row.get(summary_name) if isinstance(row, dict) else None
        matched = (
            next((item for item in summary.get("files", []) if item["path"] == relative_path), None)
            if isinstance(summary, dict)
            else None
        )

    if patch_result.timed_out:
        raise GitReviewError("git_timeout", "Git timed out while generating the patch", 504)
    if patch_result.code:
        raise GitReviewError(
            "patch_not_found", patch_result.message or "file comparison is unavailable", 404
        )
    if matched:
        additions = matched.get("additions")
        deletions = matched.get("deletions")
        binary = bool(matched.get("binary"))
        old_path = matched.get("old_path")
    patch_bytes = patch_result.stdout
    patch_hash = hashlib.sha256(patch_bytes).hexdigest()
    if expected_patch_hash is not None and expected_patch_hash != patch_hash:
        raise GitReviewError("stale_snapshot", "file patch changed", 409)
    patch_text = (
        None
        if binary or patch_result.too_large or not patch_bytes
        else patch_bytes.decode("utf-8", "replace")
    )
    result: dict[str, Any] = {
        "scope": scope,
        "path": relative_path,
        "worktree": output_worktree,
        "commit": actual_commit,
        "parent": parent,
        "comparison_ref": comparison_ref,
        "head_oid": head_oid,
        "patch_sha256": patch_hash,
        "patch": patch_text,
        "binary": binary,
        "too_large": patch_result.too_large,
        "unavailable_reason": None
        if patch_text is not None or binary or patch_result.too_large
        else "no textual patch is available",
        "additions": additions,
        "deletions": deletions,
    }
    if old_path:
        result["old_path"] = old_path
    _log_result(
        "patch",
        started,
        project_id=project_id,
        repository=repository,
        worktree=worktree,
        scope=scope,
        ref=comparison_ref or parent,
        oid=actual_commit or head_oid,
        path=relative_path,
        result="too_large" if patch_result.too_large else "ok",
        count=0 if patch_text is None else patch_text.count("\n"),
        truncated=patch_result.too_large,
    )
    return result


class GitBranchPaths(TypedDict):
    """Every repository-relative path a checkout has changed against its base.

    Deliberately not a ``GitChangeSummary``: that truncates at
    ``GIT_CHANGE_FILE_LIMIT`` because it feeds a file list a human reads, while
    this feeds a graph query that must either cover the branch or say it did not.
    """

    ref: str | None
    base: str | None
    paths: list[str]
    truncated: bool


#: Paths one branch may contribute to a change map. Far above any branch a person
#: reviews, and a hard stop before a mass rename ships tens of thousands of seeds
#: into a bounded subgraph query.
GIT_BRANCH_PATH_LIMIT = 2000


async def branch_changed_paths(
    worktree_root: str, compare_override: str | None
) -> GitBranchPaths | None:
    """What this checkout has changed since it diverged from its comparison base.

    The answer a worktree-per-branch fleet actually wants, and the one neither
    Tier 0 facts nor ``git status`` can give: facts expire on a time window and on
    a conversation rollover, and ``status`` forgets a change the moment it is
    committed. Diffing the working tree against the **merge base** covers
    committed, staged, and unstaged work in one read, and stays correct when the
    base advances underneath the branch — diffing against the ref itself would
    report inbound commits as this branch's deletions.

    Untracked files are read separately because a diff cannot see them, and a file
    a branch has only just created is exactly the one worth drawing.

    Returns None when no comparison base resolves, never an empty list: "this
    branch changed nothing" and "we could not tell" are different answers and only
    one of them should render as a blank map.
    """
    resolved = await resolve_comparison_ref(worktree_root, compare_override)
    ref = resolved["ref"]
    if not ref:
        return None
    ref_result, head_result = await asyncio.gather(
        _run_git_bytes(worktree_root, "rev-parse", "--verify", f"{ref}^{{commit}}"),
        _run_git_bytes(worktree_root, "rev-parse", "--verify", "HEAD^{commit}"),
    )
    if ref_result.code or head_result.code:
        return None
    ref_oid = ref_result.stdout.decode("ascii", "replace").strip()
    head = head_result.stdout.decode("ascii", "replace").strip()
    base_result = await _run_git_bytes(worktree_root, "merge-base", ref_oid, head)
    base = base_result.stdout.decode("ascii", "replace").strip()
    if base_result.code or not base:
        log.debug("git_review branch_paths no_merge_base root=%s ref=%s", worktree_root, ref)
        return None
    tracked, untracked = await asyncio.gather(
        _run_git_bytes(
            worktree_root, "diff", "--no-ext-diff", "--name-only", "-z", "--find-renames", base
        ),
        _run_git_bytes(worktree_root, "ls-files", "--others", "--exclude-standard", "-z"),
    )
    if tracked.code:
        return None
    paths: list[str] = []
    seen: set[str] = set()
    truncated = False
    sources = (tracked.stdout, b"" if untracked.code else untracked.stdout)
    for chunk in sources:
        for raw in chunk.decode("utf-8", "replace").split("\0"):
            value = raw.strip()
            if not value or value in seen:
                continue
            if len(paths) >= GIT_BRANCH_PATH_LIMIT:
                truncated = True
                break
            seen.add(value)
            paths.append(value)
    return {"ref": ref, "base": base, "paths": paths, "truncated": truncated}
