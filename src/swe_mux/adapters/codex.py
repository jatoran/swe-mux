from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from .base import SpawnOptions


class CodexAdapter:
    name = "codex"

    def __init__(self, default_exe: str = "codex.exe", notify: bool = False) -> None:
        self.default_exe = default_exe
        self.notify_program = (
            [sys.executable, "-m", "swe_mux.hook_client", "codex_notify"] if notify else None
        )

    def _args(self, args: list[str]) -> list[str]:
        if not self.notify_program:
            return args
        return ["-c", f"notify={json.dumps(self.notify_program)}", *args]

    def spawn_cmdline(self, sid: str, opts: SpawnOptions) -> tuple[str, str | None]:
        del sid
        args = self._args(opts.args)
        return opts.exe or self.default_exe, subprocess.list2cmdline(args) if args else None

    def resume_cmdline(self, native_id: str, opts: SpawnOptions) -> tuple[str, str]:
        return opts.exe or self.default_exe, subprocess.list2cmdline(
            self._args(["resume", native_id, *opts.args])
        )

    def transcript_path(self, native_id: str, cwd: Path) -> None:
        del native_id, cwd
        return None

    def graceful_exit_keys(self) -> str:
        return "/exit\r"
