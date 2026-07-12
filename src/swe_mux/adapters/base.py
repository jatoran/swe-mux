from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


@dataclass(slots=True)
class SpawnOptions:
    cwd: Path
    exe: str | None = None
    args: list[str] = field(default_factory=list)


class BackendAdapter(Protocol):
    name: str

    def spawn_cmdline(self, sid: str, opts: SpawnOptions) -> tuple[str, str | None]: ...
    def resume_cmdline(self, native_id: str, opts: SpawnOptions) -> tuple[str, str | None]: ...
    def transcript_path(self, native_id: str, cwd: Path) -> Path | None: ...
    def graceful_exit_keys(self) -> str: ...
