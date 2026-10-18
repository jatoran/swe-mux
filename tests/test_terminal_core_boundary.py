"""The terminal core must stay hostable outside the daemon.

`terminal_core` is written to run in a terminal gateway process as well as in the
daemon (`development/DAEMON_LOAD_ISOLATION.md` items 24-28). That holds only while
it reaches the daemon through its host interface and imports nothing of the
daemon's own: one import of `session`, `observation` or a route module and the
gateway would load the whole daemon - and its state - to serve a keystroke.

Asserted on what importing it actually loads, in a fresh interpreter, rather than
on its import statements, because a module it is allowed to import could grow an
import of its own.
"""

from __future__ import annotations

import json
import subprocess
import sys

#: The leaf modules the core may load: parsers and pure decisions with no state of
#: the daemon's behind them, and the standard-library-only framing the gateway's
#: control protocol rides on.
ALLOWED = frozenset(
    {
        "swe_mux",
        "swe_mux.composer_input",
        "swe_mux.framed_ipc",
        "swe_mux.screen_mode",
        "swe_mux.scrollback",
        "swe_mux.terminal_arbitration",
    }
)


def test_importing_the_terminal_core_loads_none_of_the_daemon() -> None:
    probe = (
        "import json, sys, swe_mux.terminal_core; "
        "print(json.dumps(sorted(m for m in sys.modules if m.startswith('swe_mux'))))"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True, timeout=60
    )
    loaded = set(json.loads(result.stdout))
    outside = {
        module
        for module in loaded
        if module not in ALLOWED and not module.startswith("swe_mux.terminal_core")
    }
    assert outside == set()
    # And the probe saw the package at all, so an empty difference means something.
    assert "swe_mux.terminal_core.connection" in loaded
