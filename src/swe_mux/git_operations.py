from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from .bounded_subprocess import LANE_INTERACTIVE, run_bounded

log = logging.getLogger(__name__)

# Worktree add/remove can traverse dependency trees containing tens of thousands of files.
# The four-second Git observation deadline is intentionally unsuitable for these mutations.
GIT_MUTATION_TIMEOUT_SECONDS = 30 * 60.0
#: A mutation's output is a few lines; a merge with thousands of conflicts is still well
#: under this, and the cap is what keeps a runaway `git` from holding the daemon's memory.
GIT_MUTATION_OUTPUT_LIMIT = 1024 * 1024


@dataclass(slots=True, frozen=True)
class GitMutationResult:
    code: int
    output: str
    timed_out: bool = False


_active_mutations: set[asyncio.Task[GitMutationResult]] = set()


async def _execute_git_mutation(
    cwd: str,
    *args: str,
    operation: str,
    operation_id: str,
    timeout_seconds: float,
) -> GitMutationResult:
    log.info(
        "git_mutation_started operation=%s operation_id=%s cwd=%s timeout_seconds=%.0f",
        operation,
        operation_id,
        cwd,
        timeout_seconds,
    )
    try:
        # The interactive lane: a person pressed the button this runs for, and a
        # poller's spawn held by a saturated disk must not hold this one too.
        outcome = await run_bounded(
            ("git", "-C", cwd, *args),
            label=f"git-mutation:{operation}",
            timeout_seconds=timeout_seconds,
            output_limit=GIT_MUTATION_OUTPUT_LIMIT,
            operation_id=operation_id,
            lane=LANE_INTERACTIVE,
        )
    except OSError as exc:
        log.warning(
            "git_mutation_spawn_failed operation=%s operation_id=%s cwd=%s error_type=%s",
            operation,
            operation_id,
            cwd,
            type(exc).__name__,
        )
        return GitMutationResult(1, str(exc))
    if outcome.timed_out:
        log.warning(
            "git_mutation_timed_out operation=%s operation_id=%s cwd=%s timeout_seconds=%.0f",
            operation,
            operation_id,
            cwd,
            timeout_seconds,
        )
        return GitMutationResult(
            124,
            f"git mutation timed out after {timeout_seconds:g}s",
            timed_out=True,
        )
    code = outcome.exit_code or 0
    output = outcome.stdout if code == 0 else outcome.stderr or outcome.stdout
    result = GitMutationResult(code, output.decode("utf-8", "replace").strip())
    log.log(
        logging.INFO if result.code == 0 else logging.WARNING,
        "git_mutation_completed operation=%s operation_id=%s cwd=%s git_code=%s truncated=%s",
        operation,
        operation_id,
        cwd,
        result.code,
        outcome.stdout_truncated or outcome.stderr_truncated,
    )
    return result


async def run_git_mutation(
    cwd: str,
    *args: str,
    operation: str,
    operation_id: str,
    timeout_seconds: float = GIT_MUTATION_TIMEOUT_SECONDS,
) -> GitMutationResult:
    """Run a daemon-owned Git mutation independently of the requesting client.

    Shielding the worker means a browser disconnect cannot cancel Git after it has
    started changing repository state. Daemon shutdown still cancels and reaps the
    worker through the bounded runner's cancellation path.
    """

    task = asyncio.create_task(
        _execute_git_mutation(
            cwd,
            *args,
            operation=operation,
            operation_id=operation_id,
            timeout_seconds=timeout_seconds,
        ),
        name=f"git-mutation:{operation}:{operation_id}",
    )
    _active_mutations.add(task)
    task.add_done_callback(_active_mutations.discard)
    return await asyncio.shield(task)
