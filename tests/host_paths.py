"""Absolute paths that mean something on the host running the suite.

A test that hardcodes ``C:/repo`` is not testing Windows - it is testing string
handling with a string that is only a path on Windows. On Linux the same literal
is a *relative* path whose first component happens to contain a colon, so
`Path.resolve()` anchors it under the working directory and every containment or
identity assertion built on it becomes meaningless rather than merely failing.

Use these when a test needs "some absolute directory" as fixture data and the
platform is not the thing under test. When the platform *is* the thing under test,
mark the test for that host instead - a mocked platform proves nothing about it.
"""

from __future__ import annotations

import sys

# An absolute root that the host's path parser genuinely treats as absolute.
ABS_ROOT = "C:/repo" if sys.platform == "win32" else "/repo"
# A second, unrelated absolute root, for "this is a different checkout" cases.
OTHER_ABS_ROOT = "C:/other" if sys.platform == "win32" else "/other"


def abs_path(*parts: str) -> str:
    """An absolute path under `ABS_ROOT`, spelled with forward slashes."""
    return "/".join((ABS_ROOT, *parts))


__all__ = ["ABS_ROOT", "OTHER_ABS_ROOT", "abs_path"]
