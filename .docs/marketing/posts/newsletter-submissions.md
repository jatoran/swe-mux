# Newsletter submissions

Submit week 1, after the launch assets exist.
Each outlet gets its own blurb at its own length; none of them gets the launch post pasted in.

## Console.dev

Weekly, roughly 2-3 reviewed tools plus 5-6 beta releases per issue, published Thursdays.
Their format is a short description plus what they call "interesting" about it.
They favor tools with strong engineering opinions.

**Read the [selection criteria](https://console.dev/selection-criteria) before writing the submission**, because one of them is a hard gate: they list only early access, alpha, or beta releases, and "any GA or stable releases are not eligible" - the release must be pre-1.0 or carry a beta/preview label somewhere discoverable. swe-mux at **0.1.0** clears this, so say the version plainly in the submission. Their other questions are whether the primary user is a developer, whether an individual can self-serve without talking to anyone, whether it belongs in a regular-use toolset, and whether it makes developers better. The self-serve question is a strength here rather than a hurdle: there is no signup at all, which is worth stating rather than leaving them to discover.

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

Submission needs an account (<https://changelog.com/news/submit>), and the profile is used for attribution.
Their guidelines welcome your own work - "if your fellow devs will find it interesting, submit it" - and explicitly reject how-to guides, tutorials, and "commercial products/services".
swe-mux is free and Apache-2.0 so it is not excluded, but that exclusion is the reason to pitch the **engineering story** rather than the product, which is what the draft below already does.
"Keep it positive. Keep it hacker."

**Subject:** An agent-fleet runtime where sessions survive their own redeploy

**Body:**

Hi - I open-sourced swe-mux (Apache 2.0), a control plane for running fleets of coding agents.
Two angles that might fit Changelog News beyond "new tool":

1. The runtime redeploys itself without killing sessions - a near-frozen supervisor owns the PTYs, and I routinely ship new builds from an agent session running inside the app being replaced.
   Write-up: [blog 02 URL]
2. The merge-safety model for letting agents land their own branches: fast-forward-only trunk, human-approved gate commands hashed by content so an agent can't approve its own gate.
   Write-up: [blog 03 URL]

Repo: github.com/jatoran/swe-mux - happy to provide anything else useful.
