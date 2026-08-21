"""Shared declarations for the closed mux MCP capability surface."""

from __future__ import annotations

READ_TOOL_NAMES = (
    "list_sessions",
    "get_session",
    "read_transcript",
    "search_history",
    "memory_sources",
    "read_memory",
    "project_notes",
    "read_project_note",
    "project_actions",
    "message_status",
    "spawn_requests",
    # Phase 7.5 cross-session memory reads (CP §6.1-6.3, §6.10). Deterministic
    # queries over Tier 0 facts, the git-provenance ledger, the experience
    # corpus, and the scan timeline. Each is per-project opt-in through the
    # enablement DAG and returns nothing in preference to a weak match.
    "provenance",
    "verified_status",
    "prior_resolutions",
    "dead_ends",
    # Phase 7.10: which docs owe an update for a Project's recent source changes,
    # re-derived from each doc's "Key files" section and gated on the doc-debt
    # detector's own per-Project opt-in.
    "doc_debt",
    # Phase 7.11: the scan timeline as an agent-readable surface. `scan_timeline`
    # is session-scoped and gated on that session's Project opting into
    # `scan_reads`; `scan_search` is the already-shipped semantic query over
    # distilled records, gated on `semantic_history_search`. Reads only - no scan
    # or backfill is reachable through MCP, because a scan spends the human's
    # gated budget.
    "scan_timeline",
    "scan_search",
    # Phase 7.9 code-structure graph reads (deterministic, model-free). Pull-only:
    # the agent consults them on its own initiative, nothing is pushed. Each is
    # gated on the per-Project `code_graph` opt-in and returns empty rather than a
    # low-confidence guess; static reverse-caller sets are labelled a lower bound.
    "blast_radius",
    "find_definition",
    "find_callers",
    "find_references",
    "code_context",
    "test_gap",
    # Session-settle watches. Declared a read, and the reasoning is worth
    # stating because the tool does eventually cause a message: it reads a
    # target's state and nothing else, and the one write it matures into is a
    # fixed daemon-authored template into the *caller's own* prompt queue. It
    # addresses nobody, actuates nothing, spends nothing, and re-arming returns
    # the watch that already exists - so it grants strictly less than
    # `list_sessions` polled in a loop, which is exactly what it replaces.
    # Permission-gating it would put an approval prompt in front of the
    # monitoring call an orchestrator makes most often, buying nothing.
    "watch_session",
)

WRITE_TOOL_NAMES = (
    "notify",
    "request_spawn",
    # `run_action` is a write in the sense that matters here: it starts a process.
    # Its authority comes from the exact-bytes approval a human already gave the
    # task file that defines the action, so it can only run commands the user has
    # seen and approved. An agent authoring a new action changes that file's
    # fingerprint, which un-trusts it, so an agent cannot approve its own command.
    "run_action",
    # Phase 7.6 session control (CP §7.6, §16). The first tools that act on a
    # running agent. Kept as two tools with different blast radii, each defaulting
    # to a per-Project `draft` grant: the call writes an inert approval request
    # and a human is what acts, until the Project raises the grant to `granted`.
    "interrupt",
    "end_session",
    # Phase 14: ask for this worktree's branch to be landed. A request, not the
    # action - it enqueues, and the daemon's fixed git vocabulary is what runs. Like
    # interrupt/end it defaults to a per-Project `draft` grant, so the call writes an
    # inert request a human approves. Deliberately session-scoped with no target
    # argument: an agent lands the checkout it is working in, never another one.
    "request_land",
)


def claude_read_permissions() -> list[str]:
    """Claude permission rules for the read-only mux MCP tools."""
    return [f"mcp__mux__{name}" for name in READ_TOOL_NAMES]
