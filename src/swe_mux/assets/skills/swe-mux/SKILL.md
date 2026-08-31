---
name: swe-mux
description: "Observe and coordinate with the swe-mux agent fleet this session runs inside: sibling sessions, live status, transcripts, history search, Project notes, bounded messages, settle waits. Use only when the user names swe-mux or mux, or asks about sibling sessions, the fleet, or another agent's work. Do not use merely because a task could benefit from delegation or a background terminal. Requires MUX_SESSION_ID."
metadata:
  managed-by: swe-mux
---

# swe-mux

swe-mux runs coding agents in real terminals and gives every session a bounded view of its
fleet: sibling sessions and their live status, transcripts, indexed history search, Project
notes, message queues a human can supervise, and bounded acts - messaging a sibling, waiting
for one to settle, requesting a spawn or a land.

Before acting on anything here, verify this agent is inside a swe-mux pane:

```bash
test -n "${MUX_SESSION_ID:-}"
```

If that check fails, say you are not running inside swe-mux and stop. Do not inspect or
drive swe-mux sessions from outside one.

## Which surface this session holds

The same capabilities travel on two transports, and `MUX_SURFACES` in this environment says
which this session has: `mcp,cli`, `mcp`, `cli`, or empty for neither (an operator switched
fleet access off for this harness - respect that and stop). An unset variable means an
older daemon; treat it as `mcp,cli`.

- **Prefer the `mux` MCP tools when they are in your tool list.** They carry this session's
  identity implicitly, need no shell quoting, and their descriptions are the current
  contract - trust them over anything remembered or written here.
- **Otherwise, with `cli` in `MUX_SURFACES`, use the CLI's agent mode.** `swemux agent
  tools` lists exactly the tools this session may call - the same tools, same authority
  checks, same budgets as MCP - and `swemux agent call <tool> key=value ...` calls one
  (`key:=value` for JSON-typed values, `--input` for a whole JSON object). A typed refusal
  prints the tool's own JSON and exits 1. Run `swemux --help` for the command surface, and
  read session ids, Project names, and message ids out of returned JSON rather than
  predicting them; treat an empty result as "nothing relevant exists in that scope", not as
  an error.

Two states that mislead: `idle` alone does not mean a session finished (a session awaiting
input and one idle behind background work render identically), and a message you stage is
delivered later under the receiving session's own rules, not when your call returns. To be
told when a sibling stops working, arm a watch; to block briefly on it, use the await tool -
its timeout is a normal, re-callable result, not an error.

## Operator commands are not yours

Every other `swemux` command acts as the human operator - sending raw input, killing or
spawning sessions, reloading or updating the daemon - and bypasses the queues, approvals,
and provenance that make agent-to-agent actions reviewable. The daemon refuses the
session-acting ones from an agent pane and names the agent surface instead. Do not run them
unless the human operator explicitly asked for exactly that.
