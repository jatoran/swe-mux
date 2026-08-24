"""Project-defined bootstrap sequencing for freshly-created Git worktrees."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from .worktree_exec import (
    WorktreeCommand,
    convention_command,
    resolve_worktree_command,
    run_bounded_command,
)

log = logging.getLogger(__name__)

SETUP_TIMEOUT_SECONDS = 30 * 60
MAX_SETUP_OUTPUT_BYTES = 2 * 1024 * 1024
SetupStatus = Literal["not_configured", "succeeded", "failed", "timed_out", "error"]

#: Retained under its historical name: the command shape is shared with worktree
#: verification and now lives in `worktree_exec`.
SetupCommand = WorktreeCommand


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


def _convention_command(script: Path) -> SetupCommand | None:
    return convention_command(script)


def resolve_setup_command(worktree: Path, project_values: dict[str, Any]) -> SetupCommand | None:
    return resolve_worktree_command(
        worktree,
        project_values,
        config_key="setup_command",
        script_name=".worktree-setup",
    )


async def run_worktree_setup(
    worktree: Path, project_values: dict[str, Any], *, project_id: str
) -> WorktreeSetupResult:
    command = resolve_setup_command(worktree, project_values)
    if command is None:
        return WorktreeSetupResult("not_configured")
    log.info(
        "worktree_setup_started project_id=%s path=%s source=%s",
        project_id,
        worktree,
        command.source,
    )
    outcome = await run_bounded_command(
        command,
        worktree,
        timeout_seconds=SETUP_TIMEOUT_SECONDS,
        output_limit=MAX_SETUP_OUTPUT_BYTES,
        label="setup",
    )
    if outcome.timed_out:
        log.warning(
            "worktree_setup_timed_out project_id=%s path=%s source=%s "
            "duration_ms=%.1f output_bytes=%d",
            project_id,
            worktree,
            command.source,
            outcome.duration_ms,
            len(outcome.output),
        )
        return WorktreeSetupResult(
            "timed_out",
            command.source,
            command.display,
            None,
            outcome.duration_ms,
            outcome.output,
            outcome.truncated,
            f"timed out after {SETUP_TIMEOUT_SECONDS}s",
        )
    if outcome.exit_code is None:
        log.warning(
            "worktree_setup_error project_id=%s path=%s source=%s duration_ms=%.1f",
            project_id,
            worktree,
            command.source,
            outcome.duration_ms,
        )
        return WorktreeSetupResult(
            "error",
            command.source,
            command.display,
            None,
            outcome.duration_ms,
            error=outcome.error,
        )
    status: SetupStatus = "succeeded" if outcome.exit_code == 0 else "failed"
    log.log(
        logging.INFO if outcome.exit_code == 0 else logging.WARNING,
        "worktree_setup_completed project_id=%s path=%s source=%s exit_code=%d "
        "duration_ms=%.1f output_bytes=%d truncated=%s",
        project_id,
        worktree,
        command.source,
        outcome.exit_code,
        outcome.duration_ms,
        len(outcome.output),
        outcome.truncated,
    )
    return WorktreeSetupResult(
        status,
        command.source,
        command.display,
        outcome.exit_code,
        outcome.duration_ms,
        outcome.output,
        outcome.truncated,
    )
