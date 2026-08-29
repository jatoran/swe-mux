# Product Hunt

**This is step 8, it is conditional, and it is not on the calendar** ([`../GTM_ROADMAP.md`](../GTM_ROADMAP.md) § Launch sequencing).

The condition: **earlier evidence shows interest beyond the terminal-tool audience.**
Traffic or installs from a non-developer-tooling referrer, requests from people who are not already running agent CLIs, or a newsletter pickup that reached a general audience.
Absent that, this does not happen and nothing is lost.

The reason is arithmetic rather than distaste: this document's own venue ranking puts Product Hunt tenth by expected value, and the previous plan still reserved a full attention day for it in week 7 of an eight-week schedule that also claimed at most one attention-day per week.
A venue ranked tenth gets a condition, not a reservation.

If it fires: after Show HN, using whatever the HN thread proved resonates.
Schedule for 12:01am PT; the day is a 24-hour vote window.

**Never ask anyone to upvote.** Product Hunt's own [launch guidance](https://www.producthunt.com/launch) prohibits it, coordinated and paid voting is detected and penalized, and ranking weights engagement - comments, maker replies, time on page - over raw vote count. Asking people to visit, comment, try it, and give honest feedback is permitted and is the effective version anyway. Self-hunting is normal and carries no penalty, so there is no reason to find a third-party hunter.

## Listing

**Name:** swe-mux

**Tagline** (60 chars max):

> Mission control for your coding-agent fleet

This is the one place the brand tagline is the right string: a 60-character listing name is not where an explanatory sentence fits.
Everywhere with room for a sentence uses the positioning line instead.

**Deleted, and do not restore it:** "Run parallel coding agents that never lose a session."
Sessions are not immortal and the supervisor that makes them durable ships off.

**Topics:** Developer Tools, Open Source, Artificial Intelligence, GitHub

**Description:**

swe-mux runs your coding agents - Claude Code, Codex, opencode - as a fleet, and its job is answering what each one actually did.
Every file write is hashed on the bytes actually written, every command carries its exit class, and each commit is attributed to the session and conversation that produced it. That record comes from the work, not from the agent's summary of it.
Agents work in parallel git worktrees, and a land queue merges their branches safely: reconcile, run the verification command whose exact bytes you approved, fast-forward-only onto trunk, one at a time, conflicts handed back to the agent. An agent cannot approve its own gate.
Per-agent status (working / ready / needs-you / blocked) is built from four evidence layers and stays conservative where they disagree.
Operate everything from your phone over Tailscale, including by voice, with speech-to-text decoded on your own machine.
Runs on your own machine: no account, no telemetry, no backend the project operates.
Open source, Apache 2.0.

**Gallery:** hero video first, then the feature GIFs (orchestrator fan-out, land queue, phone/voice, status board).

## Maker's first comment

I've been running multiple coding agents daily for a long time, and the missing piece was never the agents - it was that at the end of an afternoon I had five branches and five summaries written by the agents that produced them.

So I built the layer that records what actually happened instead, lived in it for months, and open-sourced it.

A few opinions baked in that I'll defend in the comments:

- **Evidence beats self-report.** The record is read from the bytes written and the exit codes returned, not from the agent's account of its own work.
- **Merge safety comes from construction, not trust:** the trunk only ever fast-forwards, so the step cannot lose work whatever the pipeline believes, and an agent can't approve its own verification gate.
- **It runs on your machine** and there is no backend I operate, updates included (static manifest + GitHub Releases). The agent CLIs still call their own vendors under your subscription; the control plane is what's local.
- **Almost everything is off by default**, per Project, including session survival. That is a safety posture and it means a fresh install is quieter than the feature list.
- Your phone is a real client, not a status page.

Windows-first today, Linux headless plus a browser, more platforms on the public roadmap.
The Windows installer is unsigned, so SmartScreen warns; PyPI avoids that.
Tell me where it breaks.
