# Reddit tier 2

Staggered over the weeks after launch, each riding a specific blog post or angle.
Rule: r/programming gets engineering posts as link submissions, never the announcement.

---

## r/programming

Link submissions of the engineering posts, title = the post title, no editorializing:

1. "How I rebuild and redeploy my agent runtime without killing a single session" (blog 02)
2. "Letting agents land their own branches without trusting them" (blog 03)
3. "Knowing when an AI coding agent is actually done" (blog 04)

One at a time, a week or more apart.
If one lands, do not immediately submit the next - it reads as farming.

---

## r/commandline

**Title:**

> swe-mux - a multiplexer for AI coding agents: the daemon owns the PTYs, sessions survive everything, and there's a real web/phone frontend

**Body (short):**

If tmux is how you keep shells alive, swe-mux is that idea rebuilt for coding agents: a supervisor process owns the terminal sessions, a daemon adds status detection, prompt queues, and a merge pipeline on top, and the client is a web UI (desktop or phone) instead of a terminal.
Runs Claude Code, Codex, opencode and friends in real PTYs it owns.
Open source, Apache 2.0, local-only.
Windows-first, which I know is unusual in this room; Linux runs from source.

---

## r/selfhosted

**Title:**

> swe-mux - self-hosted control plane for AI coding agents: local daemon, SQLite, Tailscale for remote, zero external services

**Body (short):**

Everything runs on your machine: the daemon, the data (SQLite), the web UI, speech-to-text, text-to-speech.
Remote access is your own tailnet via Tailscale Serve - no relay, no account with me, no telemetry.
Even updates are static files + GitHub Releases; the project operates zero servers.
Phone works as a PWA with push notifications.
Apache 2.0.

(Check r/selfhosted's rules on dev posts the week of; use their required flair.)

---

## r/opensource

**Title:**

> swe-mux (Apache 2.0): mission control for coding-agent fleets - and some deliberate licensing choices

**Body (short):**

Launched my agent-fleet control plane as open source.
Beyond the tool itself, some choices this sub might find interesting: Apache 2.0 with DCO instead of a CLA, specifically so the core can never be relicensed - by contributors or by me; a license gate in CI that audits the *shipped artifacts* for copyleft payloads, not just declared metadata (two dependencies declared permissive licenses and shipped GPL binaries in their wheels - we replaced them); and generated third-party notices that fail the build when stale.

---

## r/vibecoding

**Title:**

> If you're running more than one agent at a time, this is the cockpit I built for it (open source)

**Body (short):**

Casual register, lead with the video.
The pitch: stop babysitting terminals - one screen shows every agent, what it's doing, and which one needs you; prompts queue and deliver when the agent's ready; finished branches merge themselves behind your test suite; and the whole thing runs from your phone, including by voice.
Free, open source, local.

---

## r/codex

**Title:**

> Running Codex sessions as a managed fleet: persistent PTYs, real status, parallel worktrees that land safely (open source)

**Body (short):**

Codex-specific angle: swe-mux treats Codex as a first-class harness - session spawn/resume, status detection tuned for Codex's dialogs and its structured events, account/quota visibility, and cross-vendor review (run Codex and Claude on the same problem, compare in one history view).
Worktree parallelism plus the land queue means several Codex sessions ship branches concurrently without you doing merge duty.
