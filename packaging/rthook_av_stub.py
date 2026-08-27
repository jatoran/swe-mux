"""PyInstaller runtime hook: satisfy ``import av`` with a stub (Phase 10.5).

The shipped PyAV wheel bundles an FFmpeg built with ``--enable-libx264
--enable-libx265``, which is GPL-2.0-or-later, so ``av`` is excluded from the
bundle entirely (``excludes=["av"]`` in ``swe_mux.spec``). faster-whisper still
executes ``import av`` at module import time for its ``decode_audio`` helper -
a function no swe-mux code path reaches, because ``voice.py`` hands validated
raw PCM straight to ``WhisperModel``.

This hook registers the stub before any application import runs. The stub
itself lives in ``swe_mux.av_stub`` rather than here, because the wheel needs
exactly the same object installed at exactly the same point (``av`` is dropped
from the wheel's resolved closure too, by a ``[tool.uv]``
``override-dependencies`` entry), and two copies of a module that must behave
identically in the frozen app and in a source install is the thing that drifts.
``swe_mux.av_stub`` documents why any *use* of the stub raises.

The hook is kept as the frozen app's entry point rather than being deleted in
favour of the call sites in ``voice.py``: it runs before *any* application
import, so a new module that imports ``faster_whisper`` at module scope cannot
lose the race, and a failure here is a loud startup crash rather than a silent
resurrection of the GPL closure.
"""

from swe_mux.av_stub import install

install()
