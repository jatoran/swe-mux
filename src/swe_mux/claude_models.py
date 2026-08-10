"""Context-window sizes for Claude models.

A leaf module with no swe_mux imports, so both the live observer and the history
reconciler resolve a model the same way. They kept private copies of this table
before, which is how one of them could be right while the other was stale.

The exact table is the fast path; `claude_context_window` falls back to the
model *family* for anything unlisted. That fallback is the point: a hard-coded
table always lags the next release, and a missing entry does not degrade to
"unknown" — it degrades to a **zero** context window, which reads downstream as
0% context used. A session on a brand-new model silently showed no context
pressure at all until someone noticed and added a row.
"""

from __future__ import annotations

#: Exact ids, including dated snapshots. Consulted before any family rule.
CLAUDE_CONTEXT_WINDOWS: dict[str, int] = {
    "claude-opus-5": 1_000_000,
    "claude-opus-4-8": 1_000_000,
    "claude-opus-4-7": 1_000_000,
    "claude-opus-4-6": 1_000_000,
    "claude-fable-5": 1_000_000,
    "claude-sonnet-5": 1_000_000,
    "claude-sonnet-4-6": 1_000_000,
    "claude-haiku-4-5": 200_000,
    "claude-haiku-4-5-20251001": 200_000,
}

#: Family prefix -> window, longest prefix wins. Ordered widest-known-family
#: last so a future `claude-haiku-…` does not inherit the million-token default.
_FAMILY_WINDOWS: tuple[tuple[str, int], ...] = (
    ("claude-haiku", 200_000),
    ("claude-opus", 1_000_000),
    ("claude-sonnet", 1_000_000),
    ("claude-fable", 1_000_000),
    ("claude-mythos", 1_000_000),
)


def claude_context_window(model: str | None) -> int:
    """Context window for `model`, or 0 when it is not a recognizable Claude id.

    A `[1m]`-style suffix and a dated snapshot both resolve through the family
    rule rather than needing their own row.
    """
    if not model:
        return 0
    name = model.strip().lower()
    exact = CLAUDE_CONTEXT_WINDOWS.get(name)
    if exact:
        return exact
    for prefix, window in sorted(_FAMILY_WINDOWS, key=lambda item: -len(item[0])):
        if name.startswith(prefix):
            return window
    return 0
