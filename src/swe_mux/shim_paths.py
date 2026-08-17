from __future__ import annotations

import os
import shutil
from pathlib import Path

from .harness import agent_harnesses
from .host_platform import IS_WINDOWS

# Written into every ~/.mux/bin/{claude,codex} shim; the marker both routes
# the launch and identifies a file as one of our shims during resolution.
SHIM_MARKER = "swe_mux.agent_launcher"
# Windows needs the `.cmd` extension to be executable through PATHEXT. POSIX needs
# the *absence* of one, because `claude` is what the user types and what every
# harness-detection call looks for; a `claude.sh` would simply never be found.
SHIM_SUFFIX = ".cmd" if IS_WINDOWS else ""
SHIM_NAMES = tuple(f"{name}{SHIM_SUFFIX}" for name in agent_harnesses())
# A shim is two lines. Reading more than this would mean the candidate is not one,
# and the cap is what keeps `is_mux_shim` from slurping an arbitrary binary that
# happens to sit at an agent's name on PATH.
_SHIM_READ_LIMIT = 4096


def is_mux_shim(path: str | os.PathLike[str]) -> bool:
    """True when *path* is one of swe-mux's own agent shims.

    A daemon relaunched from inside a session inherits that session's PATH with
    the shim directory prepended, so name-based resolution ("codex") can land on
    the shim itself. Anything that resolves an agent executable for launching
    must reject these, or the shim ends up invoking itself in a loop.

    The extension gate is per-platform and had to become one. It previously
    accepted only `.cmd`/`.bat`, which is correct on Windows and wrong everywhere
    else: a POSIX shim is deliberately extensionless, so every shim would have
    read as a real CLI. `harness.detect_installation` calls `which_real`, so the
    visible symptom would have been every harness reported as installed on Linux,
    and every launch recursing into the shim - the exact trap this guard exists
    to prevent, reintroduced by the port.
    """
    candidate = Path(path)
    suffix = candidate.suffix.casefold()
    if IS_WINDOWS:
        if suffix not in {".cmd", ".bat"}:
            return False
    elif suffix not in {"", ".sh"}:
        # A POSIX shim carries no extension. `.sh` is accepted because a user or a
        # migration could reasonably have named one that way; anything else (a
        # `.py`, a binary with a suffix) is not ours.
        return False
    try:
        with candidate.open("rb") as handle:
            head = handle.read(_SHIM_READ_LIMIT)
    except OSError:
        return False
    return SHIM_MARKER.encode("utf-8") in head


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
