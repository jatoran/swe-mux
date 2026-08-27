# Bluesky and LinkedIn

## Bluesky

Same day as the X thread.
Bluesky threads work, but a single strong post with the video travels further there.

**Post:**

Open-sourced the tool I run my coding-agent fleet in: swe-mux.

Sessions that survive restarts (a supervisor owns the PTYs), honest per-agent status, parallel git worktrees with a merge queue behind your test gate, and the whole thing runs from your phone - by voice if you want.

Fully local. No cloud, no accounts, no telemetry. Apache 2.0.

github.com/[org]/swe-mux
[video: hero demo]

## LinkedIn

One post, launch day.
Professional register of the same voice: blunt conclusion, zero corporate filler, no emoji walls, no "thrilled to announce."

**Post:**

I open-sourced swe-mux today - mission control for running many AI coding agents at once.

The short version of why it exists: coding agents changed how much one person can build, but the tooling assumes you run one at a time.
Run eight and you inherit a new job - babysitting terminals, guessing which agent is stuck, and hand-merging parallel branches.
swe-mux is that job, automated: sessions that survive restarts, trustworthy per-agent status, a merge queue that lands agent branches behind your test suite, and a phone client so the few minutes of human judgment per day can happen anywhere.

It's local-only by design - no cloud, no accounts, no telemetry - and Apache 2.0.

Repo: github.com/[org]/swe-mux
Write-ups on the engineering (the session-preserving runtime, the merge-safety model) are on swemux.dev.
