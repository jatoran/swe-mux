# Newsletter submissions

Submit at step 7 (the slow burn), after the launch assets exist and after the main beat.
Each outlet gets its own blurb at its own length; none of them gets the launch post pasted in.

## Console.dev

Weekly, roughly 2-3 reviewed tools plus 5-6 beta releases per issue, published Thursdays.
Their format is a short description plus what they call "interesting" about it.
They favor tools with strong engineering opinions.

**Read the [selection criteria](https://console.dev/selection-criteria) before writing the submission**, because one of them is a hard gate: they list only early access, alpha, or beta releases, and "any GA or stable releases are not eligible" - the release must be pre-1.0 or carry a beta/preview label somewhere discoverable. swe-mux at **0.1.2** clears this, so say the version plainly in the submission. Their other questions are whether the primary user is a developer, whether an individual can self-serve without talking to anyone, whether it belongs in a regular-use toolset, and whether it makes developers better. The self-serve question is a strength here rather than a hurdle: there is no signup at all, which is worth stating rather than leaving them to discover.

**Description (~50 words):**

For developers running multiple coding agents locally, swe-mux shows what each agent actually did and lands finished branches behind checks you approved.
A local daemon that records file writes, commands and commits deterministically, normalizes per-agent status across CLIs, and merges parallel worktree branches through a fast-forward-only gate.
Operable from a phone. Apache 2.0, 0.1.2.

**What's interesting:**

The record is read from the work rather than from the agent: file writes hashed on the exact bytes written, commands with their exit class, commits attributed to the session and conversation that produced them.
Merge safety is by construction rather than by trust: trunk only ever fast-forwards, so the step cannot lose work whatever the pipeline believes, and an agent cannot approve its own verification gate because the approval is a digest over the bytes it just moved.
Distribution has no backend either: static version manifest plus GitHub Releases.

## TLDR AI

Tips/submission address on tldr.tech.
One paragraph, written like their items (compressed, factual):

**Blurb:**

swe-mux (GitHub Repo): Open-source control plane for running many coding agents at once - Claude Code, Codex, opencode - that records what each one actually did (file writes hashed on the bytes written, commands with exit classes, commit-level provenance) and lands their parallel worktree branches through a fast-forward-only merge queue behind a human-approved test gate.
Per-agent status normalized across CLIs, a phone client, and speech-to-text decoded on your own machine.
Runs on your machine with no vendor backend; Apache 2.0.

## Changelog News

Submission needs an account (<https://changelog.com/news/submit>), and the profile is used for attribution.
Their guidelines welcome your own work - "if your fellow devs will find it interesting, submit it" - and explicitly reject how-to guides, tutorials, and "commercial products/services".
swe-mux is free and Apache-2.0 so it is not excluded, but that exclusion is the reason to pitch the **engineering story** rather than the product, which is what the draft below already does.
"Keep it positive. Keep it hacker."

**Subject:** Letting coding agents land their own branches without trusting a word they say

**Body:**

Hi - I open-sourced swe-mux (Apache 2.0), a control plane for running fleets of coding agents.
Two angles that might fit Changelog News beyond "new tool":

1. The merge-safety model for letting agents land their own branches: fast-forward-only trunk, so the step cannot lose work whatever the pipeline believes, and gate commands approved by content digest so an agent cannot approve the gate its own land runs.
   Write-up: [blog 03 URL]
2. A runtime that redeploys itself without killing sessions - a near-frozen supervisor owns the PTYs, and I routinely ship new builds from an agent session running inside the app being replaced. The mode ships off by default, which is itself part of the argument: the component that makes sessions durable is the one whose update destroys them.
   Write-up: [blog 02 URL]

Repo: github.com/jatoran/swe-mux - happy to provide anything else useful.
