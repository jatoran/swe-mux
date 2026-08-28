# Claude Developers Discord

Post in the showcase/community-projects channel (confirm the current channel name and self-promo rules first).
Short, one message, one GIF attached.
This is the soft-launch audience most likely to actually install it same-day - watch the thread and fix what they hit.

## Message

Open-sourced the tool I've been running my Claude Code fleet in for months: **swe-mux** (Apache 2.0).

A local daemon + web UI that treats many sessions as one fleet:

- Sessions survive daemon restarts and app updates - a separate supervisor owns the PTYs
- Trustworthy per-session status (working / idle / needs-you) built on hooks + terminal detection; notifications only when a session actually needs a human
- Prompt queue with gated auto-delivery
- Parallel worktrees + a land queue: reconcile, run your test gate, fast-forward trunk, conflicts go back to the owning session
- Phone client over Tailscale (PWA + push), voice control with local Whisper
- Fully local, no telemetry; also runs Codex/opencode side by side

Windows-first, Linux from source.
Repo: github.com/jatoran/swe-mux - install feedback very welcome, especially clean-machine failures.

[gif: fleet view with live statuses]
