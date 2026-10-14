"""The global options that make a Git query read-only for the repository it reads.

Every read-only Git call in swe-mux puts these before its subcommand, through this one
definition, so no caller can carry half of them. A monitor must not mutate what it
monitors (`.docs/technical/backend/packages/runtime-rules.md`).

`--no-optional-locks` stops `git status` from refreshing the index and writing it back.
It does **not** stop `git diff`. Porcelain `diff` refreshes the index whenever a tracked
file's stat moved while its content did not (`diff.autoRefreshIndex`, on by default), and
it takes `.git/index.lock` *before* that refresh, which in a large repository is a
multi-second stat of every tracked file. A diff killed by its timeout during that refresh
leaves an empty `index.lock` behind, and every Git write in that repository then fails for
every agent until a person removes it.

Measured 2026-10-13 in a scratch repository with one stat-dirty file and one edited file:
`git --no-optional-locks diff --numstat HEAD` rewrote `.git/index`; adding
`-c diff.autoRefreshIndex=false` did not, and the numstat output was identical (a file
whose content is unchanged is not a line in either). The same day the git monitor's
4-second diffstat of a 17 MB-index checkout timed out one second after it had created
`index.lock`, and that empty lock blocked the repository for hours.
`tests/test_git_phase4.py` reproduces both halves against a real repository.
"""

from __future__ import annotations

READ_ONLY_GIT_OPTIONS: tuple[str, ...] = (
    "--no-optional-locks",
    "-c",
    "diff.autoRefreshIndex=false",
)
