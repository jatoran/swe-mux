from __future__ import annotations

import functools
import os
import shutil
from pathlib import Path

from .harness import agent_harnesses
from .host_platform import IS_WINDOWS, is_windows_interop_path

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
    # Stat before reading, and memoize on the result. On Windows the extension gate
    # above already rejects almost every candidate for free; on POSIX it cannot,
    # because a shim is deliberately extensionless - so every probe of a directory
    # that has no shim used to cost a real open(). Measured on a WSL PATH (106
    # entries, 95 of them DrvFs paths under /mnt) that put `path_without_shim_dirs`
    # at ~0.7s per call, and `which_real` calls it up to twice per lookup.
    try:
        info = candidate.stat()
    except OSError:
        return False
    return _read_marker(str(candidate), info.st_mtime_ns, info.st_size)


@functools.lru_cache(maxsize=1024)
def _read_marker(path: str, mtime_ns: int, size: int) -> bool:
    """Whether the file at ``path`` carries the shim marker, keyed by its identity.

    `mtime_ns` and `size` are part of the key rather than decoration: a rewritten
    shim (a data-dir change, an upgrade) must not keep answering from the cache.
    """
    try:
        with open(path, "rb") as handle:
            return SHIM_MARKER.encode("utf-8") in handle.read(_SHIM_READ_LIMIT)
    except OSError:
        return False


def path_without_shim_dirs(environ_path: str | None = None) -> str:
    """The search PATH with every directory that holds mux agent shims removed."""
    raw = environ_path if environ_path is not None else os.environ.get("PATH", "")
    return _filtered_path(raw, os.environ.get("MUX_SHIM_DIR") or "")


@functools.lru_cache(maxsize=32)
def _filtered_path(raw: str, shim_dir: str) -> str:
    """The filtered PATH, cached on the inputs that determine it.

    A PATH scan is O(entries x harnesses) filesystem probes and the answer only
    changes when PATH or the shim directory does, yet it is asked on every
    executable resolution. Keying the cache on both inputs keeps it correct across
    a data-dir change without anyone having to remember to invalidate it.
    """
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


def clear_caches() -> None:
    """Drop both memoizations. For tests, and for a deliberate re-scan.

    Ordinary correctness does not depend on this: the PATH scan is keyed on the
    PATH and shim directory, and the marker read on the file's mtime and size, so
    a shim that is rewritten or a PATH that changes invalidates itself.
    """
    _filtered_path.cache_clear()
    _read_marker.cache_clear()


def _usable(resolved: str | None) -> bool:
    """Whether a resolution is something this host can actually launch as an agent.

    Two rejections, and both exist because the alternative is a session that looks
    healthy and is not. A mux shim would invoke itself in a loop. A Windows binary
    reached through WSL interop *runs* - and then writes its transcript into the
    Windows home, reports a `wsl.localhost` working directory, and sits outside
    every Linux process group, so mux observes none of it and cannot clean it up.
    """
    return bool(resolved) and not is_mux_shim(resolved or "") and not is_windows_interop_path(
        resolved or ""
    )


def which_real(command: str) -> str | None:
    """``shutil.which`` that never resolves to a shim or a foreign-platform binary."""
    resolved = shutil.which(command)
    if _usable(resolved):
        return resolved
    resolved = shutil.which(command, path=path_without_shim_dirs())
    if _usable(resolved):
        return resolved
    return None
