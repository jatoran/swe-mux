from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

from .config import Config
from .harness import agent_harnesses
from .shim_paths import path_without_shim_dirs, which_real


def resolve_command(command: str) -> str:
    """Resolve configured commands, including npm-style PATHEXT shims.

    Older configs commonly contain ``codex.exe`` even though npm installs
    ``codex.cmd``. If the explicit value is not found, retry its basename so
    Windows PATHEXT can select the installed command. Resolution never returns
    one of our own ``~/.mux/bin`` agent shims: a daemon whose PATH inherited a
    session's shim directory would otherwise wire ``MUX_*_EXE`` back at the
    shim and every launch would recurse through itself.
    """
    resolved = which_real(command)
    if resolved:
        return resolved
    path = Path(command)
    if path.suffix.casefold() == ".exe" and path.parent == Path("."):
        resolved = which_real(path.stem)
        if resolved:
            return resolved
    return command


def resolve_codex_pty_command(
    command: str, *, windows: bool | None = None
) -> tuple[str, tuple[str, ...]]:
    """Resolve Codex to a ConPTY-safe executable plus immutable argv prefix.

    npm exposes Codex as a batch shim on Windows. ConPTY cannot execute that shim
    directly, and routing JSON-valued notify configuration through ``cmd.exe``
    corrupts its quoting. Launch the npm package's JS entrypoint with Node instead.
    """
    resolved = resolve_command(command)
    if windows is None:
        windows = os.name == "nt"
    path = Path(resolved)
    if not windows or path.suffix.casefold() not in {".cmd", ".bat"}:
        return resolved, ()
    codex_js = path.parent / "node_modules" / "@openai" / "codex" / "bin" / "codex.js"
    if codex_js.is_file():
        bundled_node = path.parent / "node.exe"
        node = str(bundled_node) if bundled_node.is_file() else shutil.which("node")
        if node:
            return node, (str(codex_js),)
    return resolved, ()


def create_agent_shims(
    config: Config,
    claude_settings: Path | None,
    claude_mcp_config: Path | None = None,
) -> dict[str, str]:
    bin_dir = config.data_dir / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    for backend in agent_harnesses():
        path = bin_dir / f"{backend}.cmd"
        path.write_text(
            f'@echo off\r\n"{sys.executable}" -m swe_mux.agent_launcher {backend} %*\r\n',
            encoding="utf-8",
        )
    result = {
        # Strip inherited shim directories (ours or a stale data dir's) before
        # prepending, so sessions see exactly one shim dir at the front.
        "PATH": f"{bin_dir}{os.pathsep}{path_without_shim_dirs()}",
        "MUX_SHIM_DIR": str(bin_dir),
        "MUX_OMP_EXTENSION_ROOT": str(config.data_dir / "omp-extensions"),
    }
    for backend in agent_harnesses():
        prefix = f"MUX_{backend.upper().replace('-', '_')}"
        result[f"{prefix}_EXE"] = resolve_command(config.harness_exe[backend])
        result[f"{prefix}_ARGS"] = json.dumps(config.harness_args[backend])
    if claude_settings:
        result["MUX_CLAUDE_SETTINGS"] = str(claude_settings)
    if claude_mcp_config:
        result["MUX_CLAUDE_MCP_CONFIG"] = str(claude_mcp_config)
    return result
