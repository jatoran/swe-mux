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

import sys

IS_WINDOWS = sys.platform == "win32"
IS_MACOS = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")
IS_POSIX = not IS_WINDOWS


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
    "IS_MACOS",
    "IS_POSIX",
    "IS_WINDOWS",
    "platform_key",
    "platform_label",
]
