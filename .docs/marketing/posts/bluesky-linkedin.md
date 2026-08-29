# Bluesky and LinkedIn

## Bluesky

Same day as the X thread.
Bluesky threads work, but a single strong post with the video travels further there.

**Post:**

Open-sourced the tool I run my coding-agent fleet in: swe-mux.

For developers running multiple coding agents locally, swe-mux shows what each agent actually did and lands finished branches behind checks you approved.

A deterministic record read from the work rather than the agent's summary of it, commit-level provenance, a merge queue that only ever fast-forwards trunk, honest per-agent status, and the whole thing runs from your phone - by voice if you want.

Runs on your own machine. No account, no telemetry, no backend I operate. Apache 2.0.

github.com/jatoran/swe-mux
[video: hero demo]

## LinkedIn

One post, launch day.
Professional register of the same voice: blunt conclusion, zero corporate filler, no emoji walls, no "thrilled to announce."

**Post:**

I open-sourced swe-mux today.

For developers running multiple coding agents locally, swe-mux shows what each agent actually did and lands finished branches behind checks you approved.

The short version of why it exists: coding agents changed how much one person can build, but the tooling assumes you run one at a time.
Run eight and you inherit a new job - and the hardest part of that job is not babysitting terminals, it is that at the end of the afternoon you have five branches and five summaries written by the agents that produced them.
swe-mux replaces the summaries with a record: file writes hashed on the bytes actually written, commands with their exit classes, commits attributed to the session that made them.
Then it lands the finished branches one at a time behind the verification command you approved, and hands conflicts back to the agent that caused them.

It runs on your own machine - no account, no telemetry, no backend I operate - and it's Apache 2.0.

Repo: github.com/jatoran/swe-mux
Write-ups on the engineering (the session-preserving runtime, the merge-safety model) are on swemux.dev.
