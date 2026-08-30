"""The frozen console client's entry point (`packaging/swe_mux_cli.spec`).

`raise SystemExit(main())`, not a bare `main()`, and the difference is the whole
CLI contract. `swe_mux.cli.main` *returns* its exit code - 3 for an unreachable
daemon, 5 for an ambiguous name, 6 for not found - because `[project.scripts]`
builds a launcher that does `sys.exit(main())` around it. A frozen entry that
merely calls it prints the same error and exits 0, so every script branching on
`mux`'s documented exit codes silently takes the success path. Measured on the
first build of this bundle: `swemux ls --url http://127.0.0.1:1` printed "cannot
reach the mux daemon" and exited 0.

`supervisor_entry.py` is deliberately not written this way; its `main` returns
None and the supervisor has no exit-code contract.
"""

from __future__ import annotations

from swe_mux.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
