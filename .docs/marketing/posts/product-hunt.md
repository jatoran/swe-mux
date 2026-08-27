# Product Hunt

Launch after Show HN, using whatever the HN thread proved resonates.
Schedule for 12:01am PT; the day is a 24-hour vote window.

## Listing

**Name:** swe-mux

**Tagline** (60 chars max):

> Mission control for your coding-agent fleet

Alternate:

> Run parallel coding agents that never lose a session

**Topics:** Developer Tools, Open Source, Artificial Intelligence, GitHub

**Description:**

swe-mux runs your coding agents - Claude Code, Codex, opencode - as a fleet.
A supervisor process owns every terminal session, so nothing dies when the daemon restarts or the app updates.
Trustworthy per-agent status (working / idle / needs-you) drives notifications that only fire when a human is actually needed.
Agents work in parallel git worktrees and a land queue merges their branches safely: verification gate, fast-forward-only, conflicts handed back to the agent.
Operate everything from your phone over Tailscale, including by voice.
Local-only: no cloud, no accounts, no telemetry.
Open source, Apache 2.0.

**Gallery:** hero video first, then the feature GIFs (orchestrator fan-out, land queue, phone/voice, status board).

## Maker's first comment

I've been running multiple coding agents daily for a long time, and the missing piece was never the agents - it was the layer above them.
Something that keeps sessions alive through restarts, tells me honestly which agent needs me, merges their parallel work without me being the merge queue, and lets me do the twenty minutes of actual human judgment per day from wherever I am.

So I built it, lived in it for months, and open-sourced it.

A few opinions baked in that I'll defend in the comments:

- Local-only, all the way down to updates (static manifest + GitHub Releases; there is no server).
- Merge safety comes from construction, not trust: the trunk only ever fast-forwards, and an agent can't approve its own verification gate.
- Your phone is a real client, not a status page.

Windows-first today, Linux from source, more platforms on the public roadmap.
Tell me where it breaks.
