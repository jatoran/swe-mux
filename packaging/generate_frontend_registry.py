"""Write the browser's harness-registry seed from the descriptor registry.

The seed is what the frontend renders with before the daemon's first snapshot
arrives. Keeping it generated is the whole point: a hand-maintained copy is a
second registry, and the one that existed had drifted from the descriptors it was
supposed to mirror with nothing comparing them.

Run after changing `HARNESSES` or `public_harness_registry`:

    uv run python packaging/generate_frontend_registry.py

`tests/test_harness_registry.py` fails while the checked-in file is stale, so this
never has to be remembered on its own.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from swe_mux.harness import (  # noqa: E402
    FRONTEND_REGISTRY_SEED_PATH,
    frontend_registry_seed_source,
)


def main() -> int:
    target = Path(__file__).resolve().parent.parent.joinpath(*FRONTEND_REGISTRY_SEED_PATH)
    source = frontend_registry_seed_source()
    # Written with an explicit newline so a Windows checkout does not turn the
    # repository's LF endings into CRLF and report the whole file as changed.
    if target.exists() and target.read_text(encoding="utf-8") == source:
        print(f"{target} already current")
        return 0
    target.write_text(source, encoding="utf-8", newline="\n")
    print(f"wrote {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
