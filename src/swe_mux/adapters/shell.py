from __future__ import annotations

import subprocess
from pathlib import Path

from .base import SpawnOptions


class ShellAdapter:
    name = "shell"

    def __init__(self, default_exe: str = "powershell.exe") -> None:
        self.default_exe = default_exe

    def spawn_cmdline(self, sid: str, opts: SpawnOptions) -> tuple[str, str | None]:
        del sid
        # A bare PowerShell startup banner adds noise without conveying session
        # state. Explicit args remain fully user-controlled for other shells.
        args = subprocess.list2cmdline(opts.args or ["-NoLogo"])
        return opts.exe or self.default_exe, args

    def resume_cmdline(self, native_id: str, opts: SpawnOptions) -> tuple[str, str | None]:
        del native_id
        return self.spawn_cmdline("", opts)

    def transcript_path(self, native_id: str, cwd: Path) -> None:
        del native_id, cwd
        return None

    def graceful_exit_keys(self) -> str:
        return "exit\r"
