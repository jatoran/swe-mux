from __future__ import annotations

import asyncio
from pathlib import Path

from .base import SpawnOptions, SpawnSpec


class ShellAdapter:
    name = "shell"
    reports_conversation_rollover = False
    # A shell has no conversation of its own; promotion adopts the agent's id.
    assigns_conversation_id = False
    resolves_transcript_by_cwd = False

    def __init__(self, default_exe: str = "powershell.exe") -> None:
        self.default_exe = default_exe

    def spawn_spec(self, sid: str, opts: SpawnOptions) -> SpawnSpec:
        del sid
        # Profile-owned argv is already resolved by the API. The adapter never
        # assumes that an arbitrary shell understands PowerShell flags.
        return SpawnSpec(opts.exe or self.default_exe, tuple(opts.args))

    def resume_spec(self, native_id: str, opts: SpawnOptions) -> SpawnSpec:
        del native_id
        return self.spawn_spec("", opts)

    def resume_continues_conversation(self, recorded_cwd: str, target_cwd: str) -> bool:
        # A shell owns no conversation, so there is none to continue. Resuming an
        # agent run into a shell is refused a layer up; this keeps the protocol total.
        del recorded_cwd, target_cwd
        return False

    def transcript_path(self, native_id: str, cwd: Path) -> None:
        del native_id, cwd
        return None

    def locate_transcript(self, native_id: str) -> None:
        del native_id
        return None

    def pending_transcript_path(self, session_id: str, current: Path) -> None:
        del session_id, current
        return None

    def graceful_exit_keys(self) -> str:
        return "exit\r"

    def recent_transcripts(self, cwd: Path, created_at: float) -> list[tuple[float, Path, str]]:
        del cwd, created_at
        return []

    async def await_transcript(
        self, native_id: str, cwd: Path, created_at: float, stop: asyncio.Event
    ) -> Path | None:
        del native_id, cwd, created_at, stop
        return None

    def transcript_native_id(self, path: Path) -> str | None:
        del path
        return None

    def model_context_window(self, provider: str, model: str) -> int:
        del provider, model
        return 0

    def cleanup(self, session_id: str) -> None:
        del session_id

    def session_env(self, session_id: str) -> dict[str, str]:
        del session_id
        return {}

    def configure(self, executable: str, args: list[str]) -> None:
        self.default_exe = executable
        del args

    def media_reference(self, path: Path) -> str:
        del path
        raise ValueError("clipboard images are supported only in registered agent sessions")
