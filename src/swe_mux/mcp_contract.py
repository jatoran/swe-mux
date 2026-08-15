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
)


def claude_read_permissions() -> list[str]:
    """Claude permission rules for the read-only mux MCP tools."""
    return [f"mcp__mux__{name}" for name in READ_TOOL_NAMES]
