# Reddit and Discord community posts

**Exactly one of these is the niche launch at step 4** ([`../GTM_ROADMAP.md`](../GTM_ROADMAP.md) § Launch sequencing) - r/ClaudeAI or the Claude Developers Discord, whichever the design partners came from least, so the post reaches people the beta did not.
The rest move to step 7 and are staggered there, a week or more apart.

The previous plan fired all four inside one week while also claiming at most one attention-day per week.
Do not restore that.

Each post is differentiated - never the same text twice.
Check each sub's self-promo rules the week of posting; flair as appropriate.

---

## r/ClaudeAI

**Title:**

> I built an open-source control plane for running many Claude Code sessions at once - it records what each one actually did, and lands their branches behind my test gate

**Body:**

I run several Claude Code sessions in parallel most days, and the tooling gap was never the agent.
It was that after an afternoon of parallel work I had five branches, five summaries written by the agents that produced them, and no independent account of what any of them had actually touched.

swe-mux is what I built for myself over the past months, now open source (Apache 2.0):

- **A deterministic record**: every file write hashed on the bytes actually written, every command with its exit class, test output parsed to the failing set. Commits carry which session and conversation produced them, split into committer and contributor. Read from the work, not from the agent's summary of it.
- **A land queue**: agents work in git worktrees, and finished branches get reconciled, run through the verification command whose exact bytes I approved, and fast-forwarded onto trunk one at a time. Conflicts and failed gates go back to the session that owns them. An agent cannot approve its own gate.
- **Real status per session** (working / ready / awaiting-you / blocked), built from Claude Code's hooks plus the transcript plus terminal detection, hardened against a corpus of captured real sessions, and conservative where the layers disagree.
- **A prompt queue** per session with gated auto-delivery, off by default.
- **Phone client** over Tailscale (PWA + push), voice control with speech-to-text that decodes on my own machine.
- **A separate supervisor owns the PTYs**, so sessions ride through daemon restarts and app updates with scrollback intact. It cannot survive its own death, so behind it cold session recovery brings sessions back as readable, resumable rows after a crash, a force close, or a power loss.

Worth saying plainly: **most of the control plane ships off.** Automations are per-Project opt-in, the land queue needs a switch plus an opt-in plus a grant plus an approved command, and the model-backed parts are off. A fresh install is quieter than that list.

It runs on your own machine - SQLite on your disk, no account, no telemetry, no backend I operate. Your Claude Code subscription and config are untouched.

Windows-first (Linux headless plus a browser; WSL bridge for Claude living inside WSL).
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

> swe-mux: open-source control plane for coding agents that runs entirely on your own machine - no accounts, speech-to-text decoded locally

**Body:**

Angle for this sub: locality and ownership.
Lead with: the data is SQLite on your disk, there is no telemetry and no account, the phone path is your own tailnet with no relay, speech-to-text decodes on the host in both shipped configurations (faster-whisper or Windows Speech Recognition), TTS can be local Kokoro, and even the update mechanism is a static manifest plus GitHub Releases - there is no backend this project operates.

**Get the two qualifications in before someone else does, because this sub will and will be right.**

1. The agent CLIs people mostly run (Claude Code, Codex) are cloud models. The *control plane* is what's local. Say it in the post.
2. "Fully local" is not the claim. swe-mux is itself a local HTTP daemon, and four optional capabilities reach the network when you turn them on: OpenRouter-compatible model calls with your key, web push through your browser vendor, the speech models downloaded once from Hugging Face, and experimental Edge TTS. Plus one daily static `version.json` fetch with no identifier, which is disableable. Naming all five is stronger here than a slogan, because this is the sub that checks.

[Adapt body from r/ClaudeAI; do not copy verbatim.]
