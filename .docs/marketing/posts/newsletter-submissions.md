# Newsletter submissions

Submit week 1, after the launch assets exist.
Each outlet gets its own blurb at its own length; none of them gets the launch post pasted in.

## Console.dev

They feature two dev tools a week and have a submission form (console.dev - "Submit a tool").
Their format is a short description plus what they call "interesting" about it.
They favor tools with strong engineering opinions.

**Description (~50 words):**

swe-mux is mission control for coding-agent fleets: a local daemon that owns agent terminal sessions (they survive restarts and updates), detects per-agent status reliably, queues prompts, and merges parallel agent branches behind a verification gate.
Operable from a phone, including by voice.
Local-only, zero servers, Apache 2.0.

**What's interesting:**

The process architecture: a near-frozen supervisor owns the PTYs so the daemon and app can be rebuilt and redeployed - from inside a running session - without killing any session.
Merge safety is by construction: trunk only ever fast-forwards, and an agent cannot approve its own verification gate.
Even distribution is serverless: static version manifest plus GitHub Releases.

## TLDR AI

Tips/submission address on tldr.tech.
One paragraph, written like their items (compressed, factual):

**Blurb:**

swe-mux (GitHub Repo): Open-source "mission control" for running many coding agents at once - Claude Code, Codex, opencode - with terminal sessions that survive restarts, trustworthy per-agent status, gated prompt queues, and a merge queue that lands parallel agent branches behind a test gate.
Fully local with a phone/voice client; Apache 2.0.

## Changelog News

Pitch email to their news submission address; they favor a story over a product.

**Subject:** An agent-fleet runtime where sessions survive their own redeploy

**Body:**

Hi - I open-sourced swe-mux (Apache 2.0), a control plane for running fleets of coding agents.
Two angles that might fit Changelog News beyond "new tool":

1. The runtime redeploys itself without killing sessions - a near-frozen supervisor owns the PTYs, and I routinely ship new builds from an agent session running inside the app being replaced.
   Write-up: [blog 02 URL]
2. The merge-safety model for letting agents land their own branches: fast-forward-only trunk, human-approved gate commands hashed by content so an agent can't approve its own gate.
   Write-up: [blog 03 URL]

Repo: github.com/[org]/swe-mux - happy to provide anything else useful.
