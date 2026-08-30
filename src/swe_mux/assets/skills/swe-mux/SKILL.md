---
name: swe-mux
description: "Observe and coordinate with the swe-mux agent fleet this session runs inside: sibling sessions, live status, transcripts, history search, Project notes, bounded messages. Use only when the user names swe-mux or mux, or asks about sibling sessions, the fleet, or another agent's work. Do not use merely because a task could benefit from delegation or a background terminal. Requires MUX_SESSION_ID."
metadata:
  managed-by: swe-mux
---

# swe-mux

swe-mux runs coding agents in real terminals and gives every session a bounded view of its
fleet: sibling sessions and their live status, transcripts, indexed history search, Project
notes, and message queues a human can supervise.

Before acting on anything here, verify this agent is inside a swe-mux pane:

```bash
test -n "${MUX_SESSION_ID:-}"
```

If that check fails, say you are not running inside swe-mux and stop. Do not inspect or
drive swe-mux sessions from outside one.

## Prefer the MCP tools

When this session's tool list includes tools from the `mux` MCP server, use them for
everything fleet-shaped. They carry this session's identity, scope answers to this
session's Project unless asked to widen, and their descriptions are the current contract -
trust them over anything remembered or written here. Read session ids, Project names, and
message ids out of returned JSON rather than predicting them, and treat an empty result as
"nothing relevant exists in that scope", not as an error.

Two states that mislead: `idle` alone does not mean a session finished (a session awaiting
input and one idle behind background work render identically), and a message you stage is
delivered later under the receiving session's own rules, not when your call returns.

## Without MCP: the CLI is read-only for you

Some harnesses cannot attach MCP tools. The `swemux` CLI (alias `mux`) then offers
read-only visibility - list sessions, history, projects, a diagnostics report. Run
`swemux --help` for the current commands rather than relying on any list written here, and
pass `--json` when output will be parsed; the exit codes are part of the contract, so
scripts branch on them, never on prose.

Treat every CLI command that acts on a session or the daemon as operator surface, not
yours: sending input, killing or spawning sessions, and reloading or updating the daemon
bypass the queues, approvals, and provenance that make agent-to-agent actions reviewable.
Do not run those unless the human operator explicitly asked for exactly that.
