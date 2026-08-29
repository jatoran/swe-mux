# Claude Developers Discord

**One of the two candidates for the single niche launch at step 4** ([`../GTM_ROADMAP.md`](../GTM_ROADMAP.md) § Launch sequencing); whichever of this and r/ClaudeAI is not used there moves to step 7.

Post in the showcase/community-projects channel (confirm the current channel name and self-promo rules first).
Short, one message, one GIF attached.
This is the audience most likely to actually install it same-day - watch the thread and fix what they hit.

## Message

Open-sourced the tool I've been running my Claude Code fleet in for months: **swe-mux** (Apache 2.0).

A local daemon + web UI whose job is answering "what did each of these actually do, and is it safe to merge":

- A deterministic record per session: file writes hashed on the bytes actually written, commands with exit classes, test output parsed to the failing set. Commits attributed to the session and conversation that produced them.
- Parallel worktrees + a land queue: reconcile, run the verification command whose exact bytes you approved, fast-forward trunk one branch at a time. Conflicts and failed gates go back to the owning session, and an agent can't approve its own gate.
- Per-session status (working / ready / needs-you / blocked) built on Claude Code's hooks + the transcript + terminal detection, conservative where the layers disagree.
- Prompt queue with gated auto-delivery, off by default.
- Phone client over Tailscale (PWA + push), voice with speech-to-text decoded on your own machine.
- A supervisor process can own the PTYs so sessions ride through daemon restarts and app updates - **it ships off**, it's a config edit plus a restart, and cold session recovery covers the default case.

Fair warning: most of the control plane is per-Project opt-in and ships off, so a fresh install is quieter than that list. Runs on your own machine, no telemetry, no backend I operate; also runs Codex/opencode side by side.

Windows-first, Linux headless plus a browser.
Repo: github.com/jatoran/swe-mux - install feedback very welcome, especially clean-machine failures.

[gif: fleet view with live statuses]
