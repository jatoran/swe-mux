"""PyInstaller runtime hook: satisfy ``import av`` with a stub (Phase 10.5).

The shipped PyAV wheel bundles an FFmpeg built with ``--enable-libx264
--enable-libx265``, which is GPL-2.0-or-later, so ``av`` is excluded from the
bundle entirely (``excludes=["av"]`` in ``swe_mux.spec``). faster-whisper still
executes ``import av`` at module import time for its ``decode_audio`` helper -
a function no swe-mux code path reaches, because ``voice.py`` hands validated
raw PCM straight to ``WhisperModel``.

This hook registers a stub module before any application import runs, so
``faster_whisper`` imports cleanly with no FFmpeg present. Any *use* of the
stub fails loudly: reaching a PyAV attribute means a code path started
depending on the removed decoder, which must fail at the call site rather than
resurrect the GPL closure silently.
"""

import sys
import types

_stub = types.ModuleType("av")
_stub.__doc__ = "swe-mux stub: PyAV is deliberately not bundled (GPL FFmpeg build)."


def _refuse(name: str):  # noqa: ANN202 - PyInstaller runtime hook, keep dependency-free
    raise RuntimeError(
        "PyAV is not bundled with swe-mux (its wheel ships a GPL FFmpeg build); "
        f"the attribute av.{name} is unavailable. Audio decoding must use the "
        "validated raw-PCM path in swe_mux.voice instead."
    )


_stub.__getattr__ = _refuse  # type: ignore[attr-defined]  # PEP 562 module fallback
sys.modules.setdefault("av", _stub)
