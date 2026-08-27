"""The single definition of swe-mux's ``av`` stub (Phase 10.5 / Phase 11).

PyAV's wheel bundles an FFmpeg built with ``--enable-libx264 --enable-libx265``,
which is GPL-2.0-or-later. Nothing in swe-mux uses it: ``av`` reaches the closure
only because ``faster_whisper/audio.py`` executes a module-level ``import av``
for its ``decode_audio`` helper, and no swe-mux call site reaches that helper -
``voice.py`` builds a float32 array from int16 PCM taken from a validated WAV
header and hands it straight to ``WhisperModel``.

So ``av`` is dropped rather than replaced, in both artifacts:

- The **frozen bundle** excludes it (``excludes=["av"]`` in ``swe_mux.spec``) and
  runs ``packaging/rthook_av_stub.py`` before any application import.
- The **wheel** drops it from the resolved closure with a ``[tool.uv]``
  ``override-dependencies`` entry in ``pyproject.toml``.

Both artifacts therefore run ``faster_whisper`` with no PyAV present, which only
works because :func:`install` has already put this stub in ``sys.modules``.
That installation is the load-bearing part, so it lives here **once** and both
entry points call it: a second copy of the stub is the thing that would drift,
and the frozen path and the source path disagreeing about it is exactly the
class of bug that shows up as "STT works in dev and not in the app".

Any *use* of the stub fails loudly. Reaching a PyAV attribute means a code path
started depending on the removed decoder, and that must fail at the call site
rather than resurrect the GPL closure silently by re-adding the dependency.
"""

from __future__ import annotations

import sys
import types

MODULE_NAME = "av"

_MESSAGE = (
    "PyAV is not installed with swe-mux (its wheel ships a GPL FFmpeg build); "
    "the attribute av.{name} is unavailable. Audio decoding must use the "
    "validated raw-PCM path in swe_mux.voice instead."
)


def _refuse(name: str) -> object:
    # Dunders answer as an ordinary missing attribute. Python itself probes a
    # module for `__file__`, `__path__`, `__all__`, and friends - `repr()` of a
    # module does, which means a log line, a traceback, or a debugger would
    # otherwise raise from inside the stub and bury the real failure. Only a
    # genuine PyAV attribute (`av.open`, `av.audio`, `av.error`) is a code path
    # that started needing the removed decoder, and only that fails loudly.
    if name.startswith("__") and name.endswith("__"):
        raise AttributeError(f"module {MODULE_NAME!r} has no attribute {name!r}")
    raise RuntimeError(_MESSAGE.format(name=name))


def build() -> types.ModuleType:
    """A module object that satisfies ``import av`` and refuses every use."""
    stub = types.ModuleType(MODULE_NAME)
    stub.__doc__ = "swe-mux stub: PyAV is deliberately absent (GPL FFmpeg build)."
    # PEP 562 module-level fallback, consulted only after normal attribute
    # lookup fails - so `__name__`, `__spec__`, and the other module dunders
    # keep working and only real PyAV attributes raise.
    stub.__getattr__ = _refuse  # type: ignore[method-assign]
    return stub


def install() -> types.ModuleType:
    """Register the stub as ``av`` unless a real PyAV is already imported.

    Idempotent, and deliberately ``setdefault``: a developer environment that
    still has the real PyAV installed must not have an already-imported module
    swapped out from under it. Everything else - a wheel install, the frozen
    bundle, CI - has no ``av`` at all, so the stub wins the name and
    ``faster_whisper`` imports cleanly.

    Call this **before** importing ``faster_whisper``, which imports ``av`` at
    module scope.
    """
    sys.modules.setdefault(MODULE_NAME, build())
    return sys.modules[MODULE_NAME]
