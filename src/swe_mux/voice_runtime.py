"""The on-device speech *code* closure, as a first-use asset rather than a bundle.

ROADMAP Phase 21, Workstream D. Three stores in :mod:`swe_mux.voice_models`
already acquire the speech *weights* on an explicit press. This one acquires the
libraries that read them - spaCy, thinc, blis, onnxruntime, CTranslate2,
faster-whisper, tokenizers, misaki, num2words and their closure - because they
were 277 MB of the desktop bundle's 400 MB and both speech features are off by
default, so every new user downloaded and scanned them for a capability most
never enable.

The acquisition mechanism itself - pinned URLs with pinned SHA-256s, fetched on
an explicit act, unpacked into one directory that goes on ``sys.path`` - lives
in :mod:`swe_mux.wheel_closure` since ROADMAP Phase 24 needed a second closure
(the desktop shell); the three load-bearing properties and their reasons are
documented there. The pins are generated from ``uv.lock``
(:mod:`swe_mux.voice_wheels`), so the closure this downloads is the closure this
repository resolved, audited and locked.

What stays *here* is everything voice-specific:

**Two capabilities, not one.** Read-aloud needs the Kokoro engine and its
phonemizer; dictation needs faster-whisper and its runtime. A readiness check
that answers "no" about a stack that works is wrong whether or not the partial
case is common (split 2026-08-29 after exactly that shipped twice), so
:func:`closure_importable` and :meth:`VoiceRuntimeStore.ready` take the
capability while acquisition stays one press over one closure.

**The LGPL relink condition is proven where the copy lives.** ``num2words`` is
LGPL-2.1 and ``misaki.en`` imports it at module scope. swe-mux no longer
distributes it - the bytes travel from PyPI to the user - and what remains true,
asserted on every unpack rather than assumed, is that the copy which lands is
readable ``.py`` source a recipient can replace. Both halves are checked: the
bundle must not carry it (``build_desktop.verify_bundle_contents``) and the
acquired tree must carry it as source.
"""

from __future__ import annotations

import importlib.util
import sys  # noqa: F401 - tests observe activation through `voice_runtime.sys.path`
from pathlib import Path

from .voice_wheels import CLOSURE_DIGEST, total_bytes, wheels_for_this_interpreter
from .wheel_closure import (
    STATES,
    ClosureAcquisitionError,
    ClosureSpec,
    WheelClosureStore,
    _extract_wheel,
    _file_verified,
    verify_relinkable,
)

__all__ = [
    "CAPABILITY_MODULES",
    "REQUIRED_MODULES",
    "RELINKABLE_LGPL",
    "STATES",
    "VoiceRuntimeError",
    "VoiceRuntimeStore",
    "closure_importable",
    "total_bytes",
]

#: The same exception the shared store raises, under the name every existing
#: caller catches. An alias rather than a subclass so `except VoiceRuntimeError`
#: and the store's own raises stay one type.
VoiceRuntimeError = ClosureAcquisitionError

# The modules each capability must be able to find, **per capability**, because
# they are two capabilities and not one (see the module docstring). Probed with
# `find_spec`, which locates a top-level package without importing it: importing
# `spacy` costs a second and this question is asked on every status read. Every
# entry is a *top-level* name for that reason - `find_spec("a.b")` imports `a`,
# and a probe with an import in it is not a probe.
CAPABILITY_MODULES: dict[str, tuple[str, ...]] = {
    "read-aloud": ("misaki", "num2words", "numpy", "onnxruntime", "spacy", "thinc"),
    "dictation": ("ctranslate2", "faster_whisper", "numpy", "tokenizers"),
}

#: Everything the store acquires, and what unpacking verifies it built. The
#: union rather than either half: one press acquires one closure.
REQUIRED_MODULES = tuple(sorted(set().union(*CAPABILITY_MODULES.values())))

#: LGPL packages the acquired closure carries, which must land as replaceable
#: source. Kept in agreement with `license_audit.ALLOWLIST` and
#: `build_desktop.RELINKABLE_LGPL` by `tests/test_license_audit.py`.
RELINKABLE_LGPL = ("num2words",)


def closure_importable(capability: str | None = None) -> bool:
    """Whether the modules are already findable in this interpreter.

    True for a source checkout synced with ``--extra voice-local`` and for any
    environment that installed the extra, which is why this is checked first
    everywhere: the store is for installs that do not have the closure, and it
    must be inert for the ones that do.

    `capability` narrows the question to one of :data:`CAPABILITY_MODULES`;
    `None` asks about the whole closure and is what *acquisition* wants.
    """
    names = CAPABILITY_MODULES[capability] if capability else REQUIRED_MODULES
    for name in names:
        try:
            if importlib.util.find_spec(name) is None:
                return False
        except (ImportError, ValueError):
            # A namespace package with a broken parent, or a `__spec__` that is
            # None on a partially-initialised module. Either way, not usable.
            return False
    return True


# The two callables resolve this module's globals at call time (lambdas, not
# references) so a test that monkeypatches `voice_runtime.closure_importable`
# or `voice_runtime.wheels_for_this_interpreter` is honoured by a store built
# afterwards - the seam every existing test was written against.
_SPEC = ClosureSpec(
    label="voice runtime",
    slug="voice-runtime",
    digest=CLOSURE_DIGEST,
    select=lambda: wheels_for_this_interpreter(),
    required_modules=REQUIRED_MODULES,
    regenerate_hint="packaging/generate_voice_pins.py",
    importable=lambda: closure_importable(),
    probe=("misaki", "__init__.py"),
    relinkable_lgpl=RELINKABLE_LGPL,
)


class VoiceRuntimeStore(WheelClosureStore):
    """The voice closure's store; mechanism in :class:`WheelClosureStore`."""

    def __init__(self, data_dir: Path) -> None:
        super().__init__(data_dir, _SPEC)

    def ready(self, capability: str | None = None) -> bool:
        """Whether `capability` can run right now, activating the tree if needed.

        `activate()` is called for its effect and its answer discarded: it asks
        about the whole closure, and a partially-provisioned environment that
        can run one capability is a working environment for that capability.
        """
        self.activate()
        return closure_importable(capability)


def _verify_relinkable(site: Path) -> None:
    """The voice closure's relink proof, kept under its historical name.

    `tests/test_voice_runtime.py` and `tests/test_license_audit.py` exercise it
    here, where the obligation is declared, while the mechanism lives with the
    store both closures share.
    """
    verify_relinkable(site, RELINKABLE_LGPL)


#: Re-exported under their historical names for the tests that pin their
#: behaviour; the implementations moved to `wheel_closure` with the store.
_extract_wheel = _extract_wheel
_file_verified = _file_verified
