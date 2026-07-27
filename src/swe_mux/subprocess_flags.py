from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import subprocess
from typing import Any

import psutil

log = logging.getLogger(__name__)


def background_creation_flags() -> int:
    """Keep daemon-owned console programs invisible in a windowed Windows build."""
    if os.name != "nt":
        return 0
    return int(getattr(subprocess, "CREATE_NO_WINDOW", 0))


async def reap_process_tree(
    process: asyncio.subprocess.Process, *, timeout_seconds: float = 10.0
) -> None:
    """Kill a spawned CLI and all of its descendants, then reap without hanging.

    ``process.kill()`` alone kills only the direct child. On Windows the direct
    child is typically a ``cmd.exe`` wrapper whose real workers (node, codex,
    git helpers) survive it, keep the inherited stdio pipes open, and wait on a
    stdin this daemon still holds — so a bare ``await process.wait()`` (or
    ``communicate()``) after the kill deadlocks: each side waits on the other,
    forever. Kill the whole tree before reaping, and bound the reap so a race
    can at worst leak a process instead of wedging the caller.

    Never raises; safe to call from ``finally`` blocks.
    """
    descendants: list[psutil.Process] = []
    with contextlib.suppress(psutil.NoSuchProcess, psutil.AccessDenied, OSError):
        descendants = psutil.Process(process.pid).children(recursive=True)
    with contextlib.suppress(ProcessLookupError, OSError):
        process.kill()
    for child in descendants:
        with contextlib.suppress(psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            child.kill()
    try:
        await asyncio.wait_for(process.wait(), timeout_seconds)
    except TimeoutError:
        log.warning(
            "subprocess tree rooted at pid %d did not reap within %.0fs; "
            "abandoning it to avoid wedging the caller",
            process.pid,
            timeout_seconds,
        )
    if descendants:
        # Wait for the grandchildren too: callers may delete files the tree
        # still holds open (a temp CODEX_HOME), and on Windows a just-killed
        # process releases its handles slightly after the kill call returns.
        with contextlib.suppress(Exception):
            await asyncio.to_thread(psutil.wait_procs, descendants, timeout_seconds)


def popen_outside_job(
    command: list[str], *, creationflags: int = 0, **kwargs: Any
) -> subprocess.Popen[Any]:
    """Spawn a child that must outlive any Job object this process is inside.

    A daemon/tray/supervisor relaunched from a shell inside a session inherits
    that session's kill-on-close Job; when the session is later removed, the
    Job closes and Windows silently terminates the relaunched processes.
    CREATE_BREAKAWAY_FROM_JOB detaches the child (the session jobs set
    JOB_OBJECT_LIMIT_BREAKAWAY_OK to permit it). Falls back to a plain spawn
    when breakaway is denied — jobs created before BREAKAWAY_OK shipped, or
    third-party jobs — so the launch itself never fails over this.
    """
    breakaway = int(getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0)) if os.name == "nt" else 0
    if breakaway:
        try:
            return subprocess.Popen(command, creationflags=creationflags | breakaway, **kwargs)
        except OSError as exc:
            log.warning(
                "CREATE_BREAKAWAY_FROM_JOB denied (%s); spawning inside the current "
                "Job — the child may be terminated when that Job closes",
                exc,
            )
    return subprocess.Popen(command, creationflags=creationflags, **kwargs)
