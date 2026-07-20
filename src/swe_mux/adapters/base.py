from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True, slots=True)
class SpawnSpec:
    """A platform-neutral description of a process to run in a PTY.

    Adapters keep arguments structured.  Turning ``argv`` into the command-line
    representation required by a particular PTY belongs to the platform host.
    """

    executable: str
    argv: tuple[str, ...] = ()
    env: Mapping[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class SpawnOptions:
    cwd: Path
    exe: str | None = None
    args: list[str] = field(default_factory=list)
    session_id: str | None = None


class BackendAdapter(Protocol):
    name: str

    def spawn_spec(self, sid: str, opts: SpawnOptions) -> SpawnSpec: ...
    def resume_spec(self, native_id: str, opts: SpawnOptions) -> SpawnSpec: ...
    def transcript_path(self, native_id: str, cwd: Path) -> Path | None: ...
    def graceful_exit_keys(self) -> str: ...
    def recent_transcripts(self, cwd: Path, created_at: float) -> list[tuple[float, Path, str]]: ...
    async def await_transcript(
        self, native_id: str, cwd: Path, created_at: float, stop: asyncio.Event
    ) -> Path | None: ...
    def transcript_native_id(self, path: Path) -> str | None: ...
    def cleanup(self, session_id: str) -> None: ...
    def session_env(self, session_id: str) -> Mapping[str, str]: ...
    def configure(self, executable: str, args: list[str]) -> None: ...
    def media_reference(self, path: Path) -> str: ...
