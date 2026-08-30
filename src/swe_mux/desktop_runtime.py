"""Whether this interpreter can start the desktop shell.

One question, asked from three places that must agree: `swe_mux.desktop` before
it imports the toolkit, `routes/desktop_integration` when it reports what this
install can do, and `shortcuts` before it writes a link to a launcher.

**This module used to be much larger, and the reason it is not is worth keeping.**
Until 2026-08-30 `pystray` and `pywebview` lived in the `desktop` extra, so an
install could legitimately lack them, and ROADMAP Phase 24 built a pinned-wheel
store here to fetch them on a press (the same mechanism the voice closure still
uses, :mod:`swe_mux.wheel_closure`). Moving the two packages into base
`dependencies` deleted the condition that machinery existed for: every install
that has this code also has them, because they arrive from the same wheel's
metadata. A store that can never have anything to fetch is not a fallback, it is
a surface that reports on a state the packaging no longer permits - so it went,
and `pyproject.toml` carries the argument for the move.

What remains is the probe. It is still worth asking, because "declared" is not
"present": a `--no-deps` install, a partially-restored venv, or a Windows build
of Python that cannot load `pythonnet` all reach the tray with something
missing, and the caller's message should name the modules rather than surface a
raw `ImportError` from inside a toolkit.
"""

from __future__ import annotations

import importlib.util

#: What `swe_mux.desktop` must be able to import; probed with `find_spec` so the
#: question costs no import. `webview` is pywebview's import name. The rest of
#: pywebview's own closure (bottle, proxy-tools, pythonnet, clr-loader, six)
#: arrives with it as ordinary dependencies; probing the two entry-point packages
#: is what "can the desktop shell start" actually asks.
SHELL_MODULES = ("pystray", "webview")


def shell_importable() -> bool:
    """Whether the desktop shell's imports are findable in this interpreter."""
    return not missing_shell_modules()


def missing_shell_modules() -> tuple[str, ...]:
    """Which of :data:`SHELL_MODULES` this interpreter cannot find, in order.

    Named rather than counted because the caller's whole job is to tell someone
    what to reinstall, and "pystray, webview" and "webview" are different
    diagnoses: the first is an install that never got the Windows dependencies,
    the second is usually a pywebview that failed to bring `pythonnet` along.
    """
    missing: list[str] = []
    for name in SHELL_MODULES:
        try:
            found = importlib.util.find_spec(name) is not None
        except (ImportError, ValueError):
            found = False
        if not found:
            missing.append(name)
    return tuple(missing)
