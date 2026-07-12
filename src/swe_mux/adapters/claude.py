from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from .base import SpawnOptions


def encode_cwd(cwd: Path | str) -> str:
    return str(Path(cwd).resolve()).replace(":", "-").replace("\\", "-").replace("/", "-")


class ClaudeAdapter:
    name = "claude"

    def __init__(self, default_exe: str = "claude.exe", data_dir: Path | None = None) -> None:
        self.default_exe = default_exe
        self.settings_path = self._write_hook_settings(data_dir) if data_dir else None

    def _write_hook_settings(self, data_dir: Path) -> Path:
        path = data_dir / "claude-hooks.json"
        hooks: dict[str, list[dict[str, object]]] = {}
        for event in (
            "SessionStart",
            "UserPromptSubmit",
            "PreToolUse",
            "PostToolUse",
            "PostToolUseFailure",
            "PermissionRequest",
            "Notification",
            "Stop",
            "SessionEnd",
        ):
            command = subprocess.list2cmdline([sys.executable, "-m", "swe_mux.hook_client", event])
            hooks[event] = [{"hooks": [{"type": "command", "command": command}]}]
        path.write_text(json.dumps({"hooks": hooks}, indent=2), encoding="utf-8")
        return path

    def _args(self, action: str, native_id: str, opts: SpawnOptions) -> list[str]:
        args = [action, native_id]
        if self.settings_path:
            args.extend(["--settings", str(self.settings_path)])
        return [*args, *opts.args]

    def spawn_cmdline(self, sid: str, opts: SpawnOptions) -> tuple[str, str]:
        return opts.exe or self.default_exe, subprocess.list2cmdline(
            self._args("--session-id", sid, opts)
        )

    def resume_cmdline(self, native_id: str, opts: SpawnOptions) -> tuple[str, str]:
        return opts.exe or self.default_exe, subprocess.list2cmdline(
            self._args("--resume", native_id, opts)
        )

    def transcript_path(self, native_id: str, cwd: Path) -> Path:
        return Path.home() / ".claude" / "projects" / encode_cwd(cwd) / f"{native_id}.jsonl"

    def graceful_exit_keys(self) -> str:
        return "/exit\r"
