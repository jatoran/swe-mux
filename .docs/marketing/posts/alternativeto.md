# Directory listings

## AlternativeTo

Create the listing post-launch (they want live, downloadable software).

**Name:** swe-mux

**Short description:**

Open-source mission control for AI coding-agent fleets.
A local daemon runs your agent CLIs (Claude Code, Codex, opencode and others) and records what each one actually did: file writes hashed on the bytes written, commands with their exit class, and commit-level provenance.
Finished worktree branches land through a fast-forward-only merge queue behind a verification command you approved.
Adds one status vocabulary across CLIs, prompt queues, optional supervisor-owned persistent terminals, and phone and voice operation over Tailscale.
Runs on your own machine: no account, no telemetry, no vendor backend.
Apache 2.0.

The name field takes the tagline; this field takes the sentences, so the positioning line belongs in the swe-mux entry's own description above rather than being compressed into the listing name.

**License:** Open Source (Apache-2.0)

**Platforms:** Windows (installer and PyPI); Linux (PyPI, headless plus a browser)

**Tags:** ai-coding-agents, terminal-multiplexer, developer-tools, self-hosted, agent-orchestration

**List as alternative to:** herdr, Orca, Conductor, claude-squad, Vibe Kanban, tmux (weak link, but it's the discovery path people actually search)

## selfh.st

Submit to their apps directory post-launch (they index self-hosted software, open- and closed-source, and pull licence and activity data from the git APIs automatically).

**The submission mechanism could not be verified in this session** - `selfh.st` returns 403 to automated fetching, so neither the apps-about page nor the directory itself was read. The earlier version of this file asserted "a GitHub PR against their data repo"; that is unverified and should not be acted on. Read <https://selfh.st/apps-about/> in a browser and follow whatever route it names.

Reuse the AlternativeTo short description; their format wants a one-liner:

> swe-mux - Self-hosted control plane for AI coding agents with deterministic change records, commit provenance, a verification-gated merge queue, and a mobile PWA client.
