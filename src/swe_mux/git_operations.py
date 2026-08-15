from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from .subprocess_flags import background_creation_flags, reap_process_tree

log = logging.getLogger(__name__)

# Worktree add/remove can traverse dependency trees containing tens of thousands of files.
# The four-second Git observation deadline is intentionally unsuitable for these mutations.
GIT_MUTATION_TIMEOUT_SECONDS = 30 * 60.0


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
        process = await asyncio.create_subprocess_exec(
            "git",
            "-C",
            cwd,
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            creationflags=background_creation_flags(),
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

    try:
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=timeout_seconds
            )
        except TimeoutError:
            await reap_process_tree(process)
            log.warning(
                "git_mutation_timed_out operation=%s operation_id=%s cwd=%s "
                "timeout_seconds=%.0f",
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
        output = stdout if process.returncode == 0 else stderr or stdout
        result = GitMutationResult(
            process.returncode or 0,
            output.decode("utf-8", "replace").strip(),
        )
        log.log(
            logging.INFO if result.code == 0 else logging.WARNING,
            "git_mutation_completed operation=%s operation_id=%s cwd=%s git_code=%s",
            operation,
            operation_id,
            cwd,
            result.code,
        )
        return result
    finally:
        if process.returncode is None:
            await reap_process_tree(process)


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
    worker through the process helper's ``finally`` block.
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
