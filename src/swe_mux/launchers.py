from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

from .config import Config


def create_agent_shims(config: Config, claude_settings: Path | None) -> dict[str, str]:
    bin_dir = config.data_dir / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    for backend in ("claude", "codex"):
        path = bin_dir / f"{backend}.cmd"
        path.write_text(
            f'@echo off\r\n"{sys.executable}" -m swe_mux.agent_launcher {backend} %*\r\n',
            encoding="utf-8",
        )
    claude_exe = shutil.which(config.claude_exe) or config.claude_exe
    codex_exe = shutil.which(config.codex_exe) or config.codex_exe
    result = {
        "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
        "MUX_CLAUDE_EXE": claude_exe,
        "MUX_CODEX_EXE": codex_exe,
    }
    if claude_settings:
        result["MUX_CLAUDE_SETTINGS"] = str(claude_settings)
    return result
