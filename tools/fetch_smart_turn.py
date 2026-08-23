"""Fetch the Smart Turn v3 weights for the browser turn-detection lab.

The model is pipecat-ai/smart-turn-v3, BSD-2-Clause, about 8.3 MB as int8 ONNX.
It is NOT committed: an 8 MB binary does not belong in git history, and the lab
it feeds (`frontend/smart-turn-lab.html`) is an experiment rather than part of
the shipped app. Run this once per checkout that wants to open the lab.

    uv run python tools/fetch_smart_turn.py

Re-running is free - an existing file with the right size is left alone.
"""
from __future__ import annotations

import hashlib
import pathlib
import sys
import urllib.request

MODEL = "smart-turn-v3.2-cpu.onnx"
URL = f"https://huggingface.co/pipecat-ai/smart-turn-v3/resolve/main/{MODEL}"
#: Deliberately NOT under `frontend/public/`. Vite copies that directory wholesale
#: into `src/swe_mux/static`, so a model living there would add 8 MB to every
#: production build and to the frozen desktop bundle - for a page that is not even
#: an entry point in production. The lab reaches it with a `?url` import instead,
#: which the dev server resolves and a production build never sees.
DESTINATION = pathlib.Path(__file__).resolve().parent.parent / "frontend" / "models" / MODEL
#: Verified 2026-08-22 against the HuggingFace copy. A weights file that silently
#: changed underneath the golden vector in `frontend/test/smartTurnFeatures.test.ts`
#: would move every probability the lab reports with nothing to say it had.
EXPECTED_BYTES = 8_679_182


def main() -> int:
    if DESTINATION.exists() and DESTINATION.stat().st_size == EXPECTED_BYTES:
        print(f"already present: {DESTINATION}")
        return 0
    DESTINATION.parent.mkdir(parents=True, exist_ok=True)
    print(f"downloading {URL}")
    with urllib.request.urlopen(URL) as response:  # noqa: S310 - fixed, literal https URL
        payload = response.read()
    digest = hashlib.sha256(payload).hexdigest()
    DESTINATION.write_bytes(payload)
    print(f"wrote {DESTINATION} ({len(payload)} bytes, sha256 {digest[:16]}…)")
    if len(payload) != EXPECTED_BYTES:
        print(
            f"WARNING: expected {EXPECTED_BYTES} bytes. The upstream weights may have "
            "changed; re-check the lab's probabilities before trusting them.",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
