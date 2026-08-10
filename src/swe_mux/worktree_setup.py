"""Project-defined bootstrap sequencing for freshly-created Git worktrees."""

from __future__ import annotations

import asyncio
import logging
import os
import shlex
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from .spawn_contract import base_session_env
from .subprocess_flags import background_creation_flags, reap_process_tree

log = logging.getLogger(__name__)

SETUP_TIMEOUT_SECONDS = 30 * 60
MAX_SETUP_OUTPUT_BYTES = 2 * 1024 * 1024
SetupStatus = Literal["not_configured", "succeeded", "failed", "timed_out", "error"]


@dataclass(frozen=True, slots=True)
class SetupCommand:
    argv: tuple[str, ...]
    display: str
    source: Literal["project_config", "convention"]


@dataclass(frozen=True, slots=True)
class WorktreeSetupResult:
    status: SetupStatus
    source: str | None = None
    command: str | None = None
    exit_code: int | None = None
    duration_ms: float = 0.0
    output: bytes = b""
    output_truncated: bool = False
    error: str | None = None

    @property
    def failed(self) -> bool:
        return self.status in {"failed", "timed_out", "error"}

    def public_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "source": self.source,
            "command": self.command,
            "exit_code": self.exit_code,
            "duration_ms": round(self.duration_ms, 1),
            "output_truncated": self.output_truncated,
            "error": self.error,
        }

    def terminal_output(self) -> bytes:
        if self.status == "not_configured":
            return b""
        heading = f"\r\n[swe-mux] Worktree setup: {self.command or self.source}\r\n".encode()
        output = self.output.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
        output = output.replace(b"\n", b"\r\n")
        if output and not output.endswith(b"\r\n"):
            output += b"\r\n"
        if self.output_truncated:
            output += b"[swe-mux] Setup output was truncated.\r\n"
        if self.status == "succeeded":
            ending = (
                f"[swe-mux] Worktree setup completed in {self.duration_ms / 1000:.1f}s.\r\n\r\n"
            )
        else:
            detail = self.error or (
                f"exit code {self.exit_code}" if self.exit_code is not None else self.status
            )
            ending = (
                f"[swe-mux] Worktree setup failed ({detail}).\r\n"
                "[swe-mux] This tree is not bootstrapped. "
                "Fix setup before building or testing.\r\n\r\n"
            )
        return heading + output + ending.encode()


def _windows_bash() -> str | None:
    git = shutil.which("git")
    if git:
        candidate = Path(git).resolve().parent.parent / "bin" / "bash.exe"
        if candidate.is_file():
            return str(candidate)
    fallback = shutil.which("bash")
    if fallback and "system32" not in {part.casefold() for part in Path(fallback).parts}:
        return fallback
    return None


def _convention_command(script: Path) -> SetupCommand | None:
    if not script.is_file():
        return None
    if os.name != "nt":
        if not os.access(script, os.X_OK):
            return None
        return SetupCommand((str(script),), str(script.name), "convention")
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
        _windows_bash()
        if Path(interpreter).name in {"bash", "bash.exe"}
        else shutil.which(interpreter)
    )
    if not resolved:
        return None
    return SetupCommand((resolved, *interpreter_args, str(script)), str(script.name), "convention")


def resolve_setup_command(worktree: Path, project_values: dict[str, Any]) -> SetupCommand | None:
    worktree_config = project_values.get("worktree")
    if isinstance(worktree_config, dict):
        configured = worktree_config.get("setup_command")
        if isinstance(configured, str) and configured.strip():
            command = configured.strip()
            if os.name == "nt":
                shell = os.environ.get("COMSPEC") or shutil.which("cmd.exe") or "cmd.exe"
                return SetupCommand((shell, "/d", "/s", "/c", command), command, "project_config")
            return SetupCommand(("/bin/sh", "-lc", command), command, "project_config")
    return _convention_command(worktree / ".worktree-setup")


async def _bounded_output(stream: asyncio.StreamReader) -> tuple[bytes, bool]:
    half = MAX_SETUP_OUTPUT_BYTES // 2
    prefix = bytearray()
    tail = bytearray()
    total = 0
    while chunk := await stream.read(64 * 1024):
        total += len(chunk)
        if len(prefix) < half:
            take = min(half - len(prefix), len(chunk))
            prefix.extend(chunk[:take])
            chunk = chunk[take:]
        if chunk:
            tail.extend(chunk)
            if len(tail) > half:
                del tail[: len(tail) - half]
    truncated = total > MAX_SETUP_OUTPUT_BYTES
    if not truncated:
        return bytes(prefix + tail), False
    return bytes(prefix) + b"\n[swe-mux] ... setup output omitted ...\n" + bytes(tail), True


async def run_worktree_setup(
    worktree: Path, project_values: dict[str, Any], *, project_id: str
) -> WorktreeSetupResult:
    command = resolve_setup_command(worktree, project_values)
    if command is None:
        return WorktreeSetupResult("not_configured")
    started = time.perf_counter()
    log.info(
        "worktree_setup_started project_id=%s path=%s source=%s",
        project_id,
        worktree,
        command.source,
    )
    process: asyncio.subprocess.Process | None = None
    output_task: asyncio.Task[tuple[bytes, bool]] | None = None
    try:
        process = await asyncio.create_subprocess_exec(
            *command.argv,
            cwd=str(worktree),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=base_session_env(os.environ, "shell"),
            creationflags=background_creation_flags(),
        )
        assert process.stdout is not None
        output_task = asyncio.create_task(_bounded_output(process.stdout))
        try:
            exit_code = await asyncio.wait_for(process.wait(), SETUP_TIMEOUT_SECONDS)
        except TimeoutError:
            await reap_process_tree(process)
            output, truncated = await output_task
            duration = (time.perf_counter() - started) * 1000
            log.warning(
                "worktree_setup_timed_out project_id=%s path=%s source=%s "
                "duration_ms=%.1f output_bytes=%d",
                project_id,
                worktree,
                command.source,
                duration,
                len(output),
            )
            return WorktreeSetupResult(
                "timed_out",
                command.source,
                command.display,
                None,
                duration,
                output,
                truncated,
                f"timed out after {SETUP_TIMEOUT_SECONDS}s",
            )
        output, truncated = await output_task
        duration = (time.perf_counter() - started) * 1000
        status: SetupStatus = "succeeded" if exit_code == 0 else "failed"
        level = logging.INFO if exit_code == 0 else logging.WARNING
        log.log(
            level,
            "worktree_setup_completed project_id=%s path=%s source=%s exit_code=%d "
            "duration_ms=%.1f output_bytes=%d truncated=%s",
            project_id,
            worktree,
            command.source,
            exit_code,
            duration,
            len(output),
            truncated,
        )
        return WorktreeSetupResult(
            status, command.source, command.display, exit_code, duration, output, truncated
        )
    except asyncio.CancelledError:
        if process is not None and process.returncode is None:
            await reap_process_tree(process)
        if output_task is not None:
            output_task.cancel()
            await asyncio.gather(output_task, return_exceptions=True)
        raise
    except Exception as exc:  # noqa: BLE001 - setup failure must degrade to session creation
        if process is not None and process.returncode is None:
            await reap_process_tree(process)
        duration = (time.perf_counter() - started) * 1000
        log.warning(
            "worktree_setup_error project_id=%s path=%s source=%s error_type=%s duration_ms=%.1f",
            project_id,
            worktree,
            command.source,
            type(exc).__name__,
            duration,
        )
        return WorktreeSetupResult(
            "error", command.source, command.display, None, duration, error=str(exc)
        )
