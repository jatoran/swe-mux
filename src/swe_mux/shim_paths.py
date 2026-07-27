from __future__ import annotations

import os
import shutil
from pathlib import Path

# Written into every ~/.mux/bin/{claude,codex}.cmd shim; the marker both routes
# the launch and identifies a file as one of our shims during resolution.
SHIM_MARKER = "swe_mux.agent_launcher"
SHIM_NAMES = ("claude.cmd", "codex.cmd")


def is_mux_shim(path: str | os.PathLike[str]) -> bool:
    """True when *path* is one of swe-mux's own agent shims.

    A daemon relaunched from inside a session inherits that session's PATH with
    the shim directory prepended, so name-based resolution ("codex") can land on
    the shim itself. Anything that resolves an agent executable for launching
    must reject these, or the shim ends up invoking itself in a loop.
    """
    candidate = Path(path)
    if candidate.suffix.casefold() not in {".cmd", ".bat"}:
        return False
    try:
        return SHIM_MARKER in candidate.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False


def path_without_shim_dirs(environ_path: str | None = None) -> str:
    """The search PATH with every directory that holds mux agent shims removed."""
    raw = environ_path if environ_path is not None else os.environ.get("PATH", "")
    shim_dir = os.environ.get("MUX_SHIM_DIR")
    kept: list[str] = []
    for entry in raw.split(os.pathsep):
        if not entry:
            continue
        if shim_dir and Path(entry) == Path(shim_dir):
            continue
        if any(is_mux_shim(Path(entry) / name) for name in SHIM_NAMES):
            continue
        kept.append(entry)
    return os.pathsep.join(kept)


def which_real(command: str) -> str | None:
    """``shutil.which`` that never resolves to a swe-mux agent shim."""
    resolved = shutil.which(command)
    if resolved and not is_mux_shim(resolved):
        return resolved
    resolved = shutil.which(command, path=path_without_shim_dirs())
    if resolved and not is_mux_shim(resolved):
        return resolved
    return None
