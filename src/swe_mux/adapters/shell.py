from __future__ import annotations

import asyncio
from pathlib import Path

from .base import SpawnOptions, SpawnSpec


class ShellAdapter:
    name = "shell"

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

    def transcript_path(self, native_id: str, cwd: Path) -> None:
        del native_id, cwd
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
        raise ValueError("clipboard images are supported only in Claude and Codex sessions")
