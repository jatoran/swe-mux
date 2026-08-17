"""Which host OS this daemon runs on, named once instead of at 43 call sites.

A 2026-08-16 scan found 43 ``sys.platform`` / ``os.name`` tests across 23 modules,
up from 34 across 21 eight days earlier. The count grows because the test is free
to write inline, and every inline copy is a place a future target has to be
remembered separately. This module exists so platform questions are asked through
one vocabulary and a new target is a change here plus its implementations, rather
than a search-and-hope across the tree.

It deliberately answers only *identity*. Capability questions - can this host
allocate a pseudoconsole, does it have a secret store, can it reveal a file - are
answered by the module that owns the capability, because a capability can be
absent on a supported platform (no WebView2, no libsecret) and conflating the two
is how a port starts claiming parity it does not have.
"""

from __future__ import annotations

import functools
import sys

IS_WINDOWS = sys.platform == "win32"
IS_MACOS = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")
IS_POSIX = not IS_WINDOWS


@functools.lru_cache(maxsize=1)
def running_under_wsl() -> bool:
    """Whether this Linux host is a WSL distribution rather than a native one.

    Read from the kernel release string, which carries `microsoft` on every WSL
    kernel. Not from `WSL_DISTRO_NAME`, because that is an environment variable a
    child can lose - and the question decides whether an executable is trustworthy,
    so it must not depend on env hygiene.

    Cached because it is reached from `which_real`, which runs on every executable
    resolution: without this, deciding whether a candidate is trustworthy would
    cost a `/proc` read each time.  The kernel does not change under a running
    process, so a process-lifetime answer is exact rather than merely convenient.
    """
    if not IS_LINUX:
        return False
    try:
        with open("/proc/sys/kernel/osrelease", encoding="utf-8", errors="replace") as handle:
            return "microsoft" in handle.read().casefold()
    except OSError:
        return False


def is_windows_interop_path(path: str) -> bool:
    """Whether a POSIX path names a Windows binary reached through WSL interop.

    Under WSL the whole Windows PATH is inherited, so a Windows agent CLI resolves
    from inside Linux and *runs*. That is the dangerous part: it produces a session
    that looks fine and is wrong in every way that matters - its working directory
    reports as the `wsl.localhost` UNC share, its transcript lands in the Windows home where
    no Linux path points, and the process joins no Linux process group, so cleanup
    cannot reach it.

    The signal is a DrvFs mount (`/mnt/<drive letter>/`), which is where WSL mounts
    Windows volumes, or a bare `.exe`. False on a native Linux host, where `/mnt`
    is an ordinary mount point and nothing about it is suspicious.
    """
    if not running_under_wsl():
        return False
    lowered = path.casefold()
    if lowered.endswith(".exe"):
        return True
    parts = path.split("/")
    return len(parts) > 2 and parts[1] == "mnt" and len(parts[2]) == 1


def platform_key() -> str:
    """Stable identifier for the host, matching ``LaunchProfile.platforms`` values.

    ``posix`` is the honest answer for a POSIX host that is neither Linux nor
    macOS (a BSD, say). Such a host is not a supported target, and naming it
    ``linux`` to make a check pass would be exactly the silent parity claim the
    cross-platform findings warn against.
    """
    if IS_WINDOWS:
        return "windows"
    if IS_MACOS:
        return "macos"
    if IS_LINUX:
        return "linux"
    return "posix"


def platform_label() -> str:
    """Human-facing platform name for diagnostics and the doctor report."""
    return {
        "windows": "Windows",
        "macos": "macOS",
        "linux": "Linux",
        "posix": "POSIX",
    }[platform_key()]


__all__ = [
    "IS_LINUX",
    "is_windows_interop_path",
    "running_under_wsl",
    "IS_MACOS",
    "IS_POSIX",
    "IS_WINDOWS",
    "platform_key",
    "platform_label",
]
