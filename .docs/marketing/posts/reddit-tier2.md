# Reddit tier 2

**Step 7 (the slow burn)**, staggered over the weeks after the main beat, each riding a specific blog post or angle.
This now also holds the venues the old plan crammed into one soft-launch week: r/ChatGPTCoding, r/LocalLLaMA, and whichever of r/ClaudeAI and the Claude Developers Discord was not used for the single niche launch at step 4.
Their drafts stay in [`reddit-soft-launch.md`](reddit-soft-launch.md); only their timing moved.

Rule: r/programming gets engineering posts as link submissions, never the announcement.
One venue at a time, a week or more apart.

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

> swe-mux - a multiplexer for AI coding agents: real PTYs, one status vocabulary across CLIs, and a web/phone frontend instead of a terminal client

**Body (short):**

If tmux is how you keep shells alive, swe-mux is that idea rebuilt for coding agents: the daemon owns real pseudoterminals (real signals, real Unicode widths, real bracketed paste, anything that runs in a terminal runs unchanged), and adds status detection, prompt queues, a deterministic record of what each agent wrote, and a merge pipeline on top. The client is a web UI, desktop or phone, instead of a terminal.
A separate supervisor process owns the PTYs so sessions outlive a daemon restart; it is treated as near-frozen, because updating it is the one act that reaps every live session.
Runs Claude Code, Codex, opencode and friends unchanged.
Open source, Apache 2.0, runs on your own machine.
Windows-first, which I know is unusual in this room; Linux runs headless plus a browser.

---

## r/selfhosted

**Title:**

> swe-mux - self-hosted control plane for AI coding agents: local daemon, SQLite, Tailscale for remote, no vendor backend

**Body (short):**

Everything I operate runs on your machine: the daemon, the data (SQLite), the web UI, speech-to-text, text-to-speech.
Remote access is your own tailnet via Tailscale Serve - no relay, no account with me, no telemetry.
Even updates are static files + GitHub Releases; there is no backend this project operates.
Phone works as a PWA with push notifications.
Apache 2.0.

Be precise here rather than sloganeering, because this sub reads carefully: swe-mux *is* a local HTTP daemon, which is the whole architecture. What does not exist is anything I run in between. And the optional bits that do reach the network are worth listing outright - OpenRouter-compatible model calls with your key, web push through your browser vendor, the speech models downloaded once from Hugging Face, experimental Edge TTS, and one daily static `version.json` check with no identifier on it. Each is off until you turn it on, and the last one is disableable.

(Check r/selfhosted's rules on dev posts the week of; use their required flair.)

---

## r/opensource

**Title:**

> swe-mux (Apache 2.0): mission control for coding-agent fleets - and some deliberate licensing choices

**Body (short):**

Launched my agent-fleet control plane as open source.
Beyond the tool itself, some choices this sub might find interesting: Apache 2.0 with DCO instead of a CLA, specifically so the core can never be relicensed - by contributors or by me; a license gate in CI with two halves, one reading the resolved closure's metadata and one reading the *built artifact tree* by payload name, because a dependency's declared license does not describe what its wheel ships (PyAV declares BSD-3-Clause and links GPL x264/x265; sherpa-onnx declares Apache-2.0 and statically links espeak-ng - neither is in the shipped bundle, and metadata-based auditing cannot see either one); and generated third-party notices that fail the build when stale.

---

## r/vibecoding

**Title:**

> If you're running more than one agent at a time, this is the cockpit I built for it (open source)

**Body (short):**

Casual register, lead with the video.
The pitch: one screen shows every agent, what it's doing, and - the part that matters after the novelty wears off - what it actually changed, taken from the files and commands rather than from its own summary. Prompts queue and deliver when the agent's ready; finished branches merge themselves behind your test suite, one at a time; and the whole thing runs from your phone, including by voice.
Free, open source, runs on your own machine.

---

## r/codex

**Title:**

> Running Codex sessions as a managed fleet: real status, a record of what each one changed, and parallel worktrees that land safely (open source)

**Body (short):**

Codex-specific angle: swe-mux treats Codex as a first-class harness - session spawn/resume, status detection tuned for Codex's dialogs and its structured events, account/quota visibility, and cross-vendor review (run Codex and Claude on the same problem, compare in one history view).
Every write and command is captured deterministically and every commit is attributed to the session that produced it, so comparing two harnesses on the same problem is a comparison of what they did rather than of what they each said they did.
Worktree parallelism plus the land queue means several Codex sessions ship branches concurrently without you doing merge duty.
