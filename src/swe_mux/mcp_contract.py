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
    "message_status",
    "spawn_requests",
)

WRITE_TOOL_NAMES = (
    "notify",
    "request_spawn",
)


def claude_read_permissions() -> list[str]:
    """Claude permission rules for the read-only mux MCP tools."""
    return [f"mcp__mux__{name}" for name in READ_TOOL_NAMES]
