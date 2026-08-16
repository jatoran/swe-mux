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
)


def claude_read_permissions() -> list[str]:
    """Claude permission rules for the read-only mux MCP tools."""
    return [f"mcp__mux__{name}" for name in READ_TOOL_NAMES]
