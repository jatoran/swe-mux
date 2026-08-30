"""The desktop shell closure, acquirable after install (ROADMAP Phase 24).

A PyPI install without the ``desktop`` extra has no tray and no native window,
and ``uv tool upgrade`` does not preserve an extra - so the recovery used to be
a reinstall the user has to know to spell. This store makes it a press, on the
mechanism the voice closure already proved (:mod:`swe_mux.wheel_closure`):
pinned URLs with pinned SHA-256s (:mod:`swe_mux.desktop_wheels`, generated from
``uv.lock``), fetched on an explicit act, unpacked into a data-dir site
directory.

Two facts measured on 2026-08-30 shape it, both correcting the phase's original
premise:

- The closure is **not** pure Python end to end - ``pillow`` and ``cffi`` are
  compiled and version-specific - but both are base-reachable (pillow through
  the preview pipeline, cffi through cryptography), so the *acquired* set is
  seven pure-Python distributions totalling ~2.4 MB on Windows.
- One of them, ``proxy-tools``, publishes **no wheel at all** and pywebview
  imports it unconditionally. It is pinned as an sdist and extracted under the
  extract-never-build rule (`wheel_closure._extract_sdist`); nothing from the
  archive is ever executed.

**Windows only, by absence.** There is no Linux or macOS desktop app by design
(`design/features/desktop-shell.md`), so off Windows the store reports
unsupported and the Settings surface draws nothing at all - absence, not
failure.

**The tray needs a restart, and the surface says so.** The voice closure
activates for a lazy import inside a running daemon; the desktop shell is
started by ``swe_mux.desktop.main`` in its own process, which calls
:func:`activate_for_desktop` before importing ``pystray``/``webview``. Acquiring
mid-run therefore means "installed - start (or restart) the swe-mux desktop app
to use it", and the status carries that instead of letting someone discover it.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

from .desktop_wheels import CLOSURE_DIGEST, SDISTS, wheels_for_this_interpreter
from .voice_models import ProgressCallback
from .wheel_closure import ClosureSpec, WheelClosureStore

#: What `swe_mux.desktop` must be able to import; probed with `find_spec` so the
#: question costs no import. `webview` is pywebview's import name. The other
#: acquired distributions (bottle, proxy-tools, pythonnet, clr-loader, six) are
#: pywebview's own imports and arrive in the same closure; probing the two
#: entry-point packages is what "can the desktop shell start" actually asks.
SHELL_MODULES = ("pystray", "webview")

#: What unpacking verifies the staged tree contains, by top-level import name.
#: Only names whose layout is certain from the wheels themselves; `pythonnet`'s
#: loader shim layout is version-dependent and is deliberately not probed - a
#: wrong guess here fails a good unpack, and `SHELL_MODULES` is the real gate.
REQUIRED_MODULES = ("bottle", "proxy_tools", "pystray", "six", "webview")

#: LGPL packages this acquired closure carries as a real distribution channel
#: beside the frozen bundle's `_internal/pystray/`; each copy carries its own
#: relink proof (`tests/test_license_audit.py` holds the per-copy rule).
RELINKABLE_LGPL = ("pystray",)


def shell_importable() -> bool:
    """Whether the desktop shell's imports are findable in this interpreter."""
    for name in SHELL_MODULES:
        try:
            if importlib.util.find_spec(name) is None:
                return False
        except (ImportError, ValueError):
            return False
    return True


_SPEC = ClosureSpec(
    label="desktop shell closure",
    slug="desktop-runtime",
    digest=CLOSURE_DIGEST,
    select=lambda: wheels_for_this_interpreter(),
    required_modules=REQUIRED_MODULES,
    regenerate_hint="packaging/generate_desktop_pins.py",
    importable=lambda: shell_importable(),
    probe=("webview", "__init__.py"),
    relinkable_lgpl=RELINKABLE_LGPL,
    sdists=SDISTS,
)


class DesktopRuntimeStore(WheelClosureStore):
    """The desktop shell closure's store; mechanism in `WheelClosureStore`."""

    def __init__(self, data_dir: Path) -> None:
        super().__init__(data_dir, _SPEC)

    def status(self) -> dict[str, Any]:
        # By absence, not failure - and checked BEFORE importability, unlike the
        # voice closure, deliberately: a source checkout on Linux with the extra
        # installed can run voice, but there is no desktop app off Windows for
        # any environment to run, so nothing here may report a pressable or
        # ready state there.
        if sys.platform != "win32":
            return {
                "status": "error",
                "source": None,
                "supported": False,
                "closure": CLOSURE_DIGEST,
                "distributions": 0,
                "total_bytes": 0,
                "downloaded_bytes": 0,
                "current_file": None,
                "error": "the desktop shell (tray and native window) exists only on Windows",
            }
        return super().status()

    def start_download(self, progress: ProgressCallback | None = None) -> bool:
        if sys.platform != "win32":
            return False
        return super().start_download(progress)


def activate_for_desktop(data_dir: Path) -> bool:
    """Put an acquired shell closure on ``sys.path`` for the desktop process.

    Called by ``swe_mux.desktop`` before it imports ``pystray``/``webview``, so
    an install that acquired the closure through Settings starts the tray on its
    next launch with no further steps. Inert when the environment already has
    the extra, and False - never an exception - when nothing is acquired: the
    caller's own ImportError path already says what to do.
    """
    return DesktopRuntimeStore(data_dir).activate()
