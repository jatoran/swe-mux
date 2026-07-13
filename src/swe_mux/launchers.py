from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

from .config import Config


def resolve_command(command: str) -> str:
    """Resolve configured commands, including npm-style PATHEXT shims.

    Older configs commonly contain ``codex.exe`` even though npm installs
    ``codex.cmd``. If the explicit value is not found, retry its basename so
    Windows PATHEXT can select the installed command.
    """
    resolved = shutil.which(command)
    if resolved:
        return resolved
    path = Path(command)
    if path.suffix.casefold() == ".exe" and path.parent == Path("."):
        resolved = shutil.which(path.stem)
        if resolved:
            return resolved
    return command


def create_agent_shims(config: Config, claude_settings: Path | None) -> dict[str, str]:
    bin_dir = config.data_dir / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    for backend in ("claude", "codex"):
        path = bin_dir / f"{backend}.cmd"
        path.write_text(
            f'@echo off\r\n"{sys.executable}" -m swe_mux.agent_launcher {backend} %*\r\n',
            encoding="utf-8",
        )
    claude_exe = resolve_command(config.claude_exe)
    codex_exe = resolve_command(config.codex_exe)
    result = {
        "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
        "MUX_SHIM_DIR": str(bin_dir),
        "MUX_CLAUDE_EXE": claude_exe,
        "MUX_CODEX_EXE": codex_exe,
        "MUX_CLAUDE_ARGS": json.dumps(config.claude_args),
        "MUX_CODEX_ARGS": json.dumps(config.codex_args),
    }
    if claude_settings:
        result["MUX_CLAUDE_SETTINGS"] = str(claude_settings)
    return result
