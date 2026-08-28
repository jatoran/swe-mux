# Reddit soft launch

Post these BEFORE Show HN, a day or two apart, and fix whatever they surface.
These communities forgive rough onboarding and will find the clean-machine failures.
Each post is differentiated - never the same text twice.
Check each sub's self-promo rules the week of posting; flair as appropriate.

---

## r/ClaudeAI

**Title:**

> I built an open-source control plane for running many Claude Code sessions at once - sessions survive restarts, branches land themselves, and it works from my phone

**Body:**

I run several Claude Code sessions in parallel most days, and the tooling gap was never the agent - it was everything around it: dead sessions after a restart, no idea which session actually needs me, and being the human merge queue for five worktree branches finishing at once.

swe-mux is what I built for myself over the past months, now open source (Apache 2.0):

- A supervisor process owns the PTYs, so every session survives daemon restarts and app updates - scrollback and all.
- Real status per session (working / idle / awaiting-you), built from Claude Code's hooks plus terminal detection, hardened against a corpus of captured real sessions.
  Push notifications only when a session genuinely needs you.
- A prompt queue per session with gated auto-delivery.
- Agents work in git worktrees; a land queue reconciles, runs your test gate, and fast-forwards trunk - one branch at a time, conflicts handed back to the session that owns them.
- Phone client over Tailscale (PWA + push), voice control with local Whisper.
- Everything local. No cloud, no accounts, no telemetry.

Windows-first (Linux from source; WSL bridge for Claude living inside WSL).
It also runs Codex and opencode side by side with Claude, which is genuinely useful for cross-checking.

Repo: github.com/jatoran/swe-mux - would honestly appreciate people trying the install on a clean machine and telling me where it falls over.

---

## r/ChatGPTCoding

**Title:**

> Open-sourced my setup for running Codex + Claude Code + opencode as one fleet: persistent sessions, safe parallel merging, phone control

**Body:**

Angle for this sub: multi-harness.
Same skeleton as the r/ClaudeAI post but lead with running Codex alongside Claude Code and opencode in one place - same status detection, same queue, same land pipeline regardless of vendor - and the cross-vendor history/review view.
Mention: your existing CLI configs and subscriptions are untouched; swe-mux runs the CLIs you already run in real terminals it owns.

[Adapt body from r/ClaudeAI; do not copy verbatim.]

---

## r/LocalLLaMA

**Title:**

> swe-mux: open-source, fully local mission control for coding agents - no cloud, no accounts, voice via local Whisper

**Body:**

Angle for this sub: locality and ownership.
Lead with: everything on your machine (SQLite, your filesystem), no telemetry, the phone path is your own tailnet with no relay, STT is local faster-whisper, TTS is local Kokoro, and even the update mechanism is static files - the project has zero servers.
The agents themselves are whatever CLIs you run; the control plane doesn't care whose model is behind them.
Be upfront that the agent CLIs people mostly run (Claude Code, Codex) are cloud models - the *control plane* is what's local - or the sub will make that point for you, less kindly.

[Adapt body from r/ClaudeAI; do not copy verbatim.]
