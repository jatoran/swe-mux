"""Operator tools: modules a person runs, that nothing in the daemon imports.

A module under `swe_mux/` that no other module imports reads as dead code, and
the 2026-08-23 quality audit read `git_provenance_backfill.py` exactly that way
(F28). It is not dead - it is a one-shot historical migration an operator runs
by hand - but nothing in the layout said so, and "no callers" is otherwise a
reliable signal worth keeping reliable.

So the distinction is expressed in the tree rather than in a comment someone has
to find: anything here is invoked as `python -m swe_mux.tools.<name>`, never
imported by the running daemon, and free to be slow, interactive, and one-shot.

It stays inside the package rather than moving to the repository's top-level
`tools/` directory, because these modules import swe-mux internals. Inside the
package they keep the import graph, `python -m`, mypy's strict pass, ruff, and
the test suite; outside it they would need a `sys.path` bootstrap and would fall
out of all four - a 1700-line module silently leaving the type checker is a
worse outcome than the one being fixed.
"""

from __future__ import annotations
