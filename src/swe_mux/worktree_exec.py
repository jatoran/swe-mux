"""Shared resolution and bounded execution for repository-declared worktree commands.

Two commands live at this seam and they are deliberately the same shape: worktree
**bootstrap** (`worktree_setup.py`) and worktree **verification** (`worktree_verify.py`).
Both are declared by the repository, both run inside a checkout that is outside the
Project root, and both need an exit code plus bounded output rather than a terminal
pane - which is exactly why neither can be a Project Action
(`design/features/project-actions.md`: an action's cwd is bounded by the canonical
Project root and is deliberately denied the sibling-worktree widening spawns get, and
an action step becomes a one-shot terminal session).

What the two do *not* share is authority. Bootstrap runs once, for a tree a human just
asked to create, before anything starts in it. Verification runs repeatedly, on an
agent's request, so it carries an exact-content approval of its own
(`worktree_verify.ApprovalStore`). Keeping the mechanics here and the authority there
is what stops the second one inheriting the first one's licence by accident.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shlex
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from .spawn_contract import base_session_env
from .subprocess_flags import background_creation_flags, reap_process_tree

log = logging.getLogger(__name__)

CommandSource = Literal["project_config", "convention"]


@dataclass(frozen=True, slots=True)
class WorktreeCommand:
    argv: tuple[str, ...]
    display: str
    source: CommandSource


@dataclass(frozen=True, slots=True)
class CommandOutcome:
    """What a bounded run produced, with no interpretation of its meaning.

    `exit_code` is `None` only when no exit status exists to report - a timeout or a
    spawn failure. A caller must never read that as a zero: "the gate did not run" and
    "the gate passed" are the two answers that must not be conflated, which is the same
    rule `git.md` states for an unmeasured diff.
    """

    exit_code: int | None
    output: bytes
    truncated: bool
    duration_ms: float
    timed_out: bool = False
    error: str | None = None


def windows_bash() -> str | None:
    git = shutil.which("git")
    if git:
        candidate = Path(git).resolve().parent.parent / "bin" / "bash.exe"
        if candidate.is_file():
            return str(candidate)
    fallback = shutil.which("bash")
    if fallback and "system32" not in {part.casefold() for part in Path(fallback).parts}:
        return fallback
    return None


def convention_command(script: Path) -> WorktreeCommand | None:
    """The executable-script convention, resolved through its shebang on Windows."""
    if not script.is_file():
        return None
    if os.name != "nt":
        if not os.access(script, os.X_OK):
            return None
        return WorktreeCommand((str(script),), str(script.name), "convention")
    try:
        with script.open("r", encoding="utf-8") as handle:
            first_line = handle.readline(4096).strip()
    except (OSError, UnicodeDecodeError):
        return None
    if not first_line.startswith("#!"):
        return None
    shebang = shlex.split(first_line[2:].strip(), posix=True)
    if not shebang:
        return None
    interpreter = shebang[0]
    interpreter_args = shebang[1:]
    if Path(interpreter).name == "env" and interpreter_args:
        interpreter, interpreter_args = interpreter_args[0], interpreter_args[1:]
    resolved = (
        windows_bash()
        if Path(interpreter).name in {"bash", "bash.exe"}
        else shutil.which(interpreter)
    )
    if not resolved:
        return None
    return WorktreeCommand(
        (resolved, *interpreter_args, str(script)), str(script.name), "convention"
    )


def configured_command(command: str) -> WorktreeCommand:
    """A `[worktree]` config string, handed to the host's shell verbatim."""
    text = command.strip()
    if os.name == "nt":
        shell = os.environ.get("COMSPEC") or shutil.which("cmd.exe") or "cmd.exe"
        return WorktreeCommand((shell, "/d", "/s", "/c", text), text, "project_config")
    return WorktreeCommand(("/bin/sh", "-lc", text), text, "project_config")


def resolve_worktree_command(
    worktree: Path,
    project_values: dict[str, Any],
    *,
    config_key: str,
    script_name: str,
) -> WorktreeCommand | None:
    """The explicit `[worktree]` override, else the executable-script convention."""
    worktree_config = project_values.get("worktree")
    if isinstance(worktree_config, dict):
        configured = worktree_config.get(config_key)
        if isinstance(configured, str) and configured.strip():
            return configured_command(configured)
    return convention_command(worktree / script_name)


async def bounded_output(
    stream: asyncio.StreamReader, limit: int, *, label: str,
    on_chunk: Callable[[bytes], None] | None = None,
) -> tuple[bytes, bool]:
    """Read a stream to EOF, retaining its head and tail within `limit` bytes.

    `on_chunk` observes the bytes as they arrive and changes nothing about them: this is
    the only place a caller can watch a long command make progress, because everything
    downstream sees the capture once, after the process has already exited. An observer
    that raises is a bug in the observer, so it is contained here rather than allowed to
    abandon the pipe mid-read - a half-read pipe would block the process it is draining.
    """
    half = limit // 2
    prefix = bytearray()
    tail = bytearray()
    total = 0
    while chunk := await stream.read(64 * 1024):
        if on_chunk is not None:
            try:
                on_chunk(chunk)
            except Exception:  # noqa: BLE001 - watching output must not stop reading it
                log.debug("worktree_command_observer_failed label=%s", label)
        total += len(chunk)
        if len(prefix) < half:
            take = min(half - len(prefix), len(chunk))
            prefix.extend(chunk[:take])
            chunk = chunk[take:]
        if chunk:
            tail.extend(chunk)
            if len(tail) > half:
                del tail[: len(tail) - half]
    truncated = total > limit
    if not truncated:
        return bytes(prefix + tail), False
    omitted = f"\n[swe-mux] ... {label} output omitted ...\n".encode()
    return bytes(prefix) + omitted + bytes(tail), True


async def run_bounded_command(
    command: WorktreeCommand,
    cwd: Path,
    *,
    timeout_seconds: float,
    output_limit: int,
    label: str,
    env: dict[str, str] | None = None,
    on_chunk: Callable[[bytes], None] | None = None,
) -> CommandOutcome:
    """Run one repository-declared command and report exactly what it did.

    The command's argv reaches the OS as given and its exit status is returned as
    given. Nothing here pipes, filters, or post-processes it: a gate command trimmed
    inside its own pipeline has already shipped a failing suite green in this
    repository once, and the fix was to stop touching the status, not to touch it more
    carefully.
    """
    loop = asyncio.get_running_loop()
    started = loop.time()
    process: asyncio.subprocess.Process | None = None
    output_task: asyncio.Task[tuple[bytes, bool]] | None = None

    def elapsed() -> float:
        return (loop.time() - started) * 1000

    try:
        process = await asyncio.create_subprocess_exec(
            *command.argv,
            cwd=str(cwd),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=env if env is not None else base_session_env(os.environ, "shell"),
            creationflags=background_creation_flags(),
        )
        assert process.stdout is not None
        output_task = asyncio.create_task(
            bounded_output(process.stdout, output_limit, label=label, on_chunk=on_chunk)
        )
        try:
            exit_code = await asyncio.wait_for(process.wait(), timeout_seconds)
        except TimeoutError:
            await reap_process_tree(process)
            output, truncated = await output_task
            return CommandOutcome(
                None,
                output,
                truncated,
                elapsed(),
                timed_out=True,
                error=f"timed out after {timeout_seconds:g}s",
            )
        output, truncated = await output_task
        return CommandOutcome(exit_code, output, truncated, elapsed())
    except asyncio.CancelledError:
        if process is not None and process.returncode is None:
            await reap_process_tree(process)
        if output_task is not None:
            output_task.cancel()
            await asyncio.gather(output_task, return_exceptions=True)
        raise
    except Exception as exc:  # noqa: BLE001 - a failed run is a reported outcome, not a fault
        if process is not None and process.returncode is None:
            await reap_process_tree(process)
        log.warning(
            "worktree_command_error label=%s cwd=%s source=%s error_type=%s",
            label,
            cwd,
            command.source,
            type(exc).__name__,
        )
        return CommandOutcome(None, b"", False, elapsed(), error=str(exc))
