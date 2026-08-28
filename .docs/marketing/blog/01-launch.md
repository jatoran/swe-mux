# swe-mux: mission control for your coding-agent fleet

*Launch post. Canonical announcement; everything else links here. Cross-post to dev.to and Hashnode with canonical URL set to swemux.dev.*

---

I run a lot of coding agents.
Claude Code, Codex, opencode, several at once, most of the day.
The tooling for running *one* agent is fine.
The tooling for running eight of them across five projects is a pile of terminal windows, lost sessions, and you personally polling each one to see if it finished or is sitting on a permission prompt.

swe-mux is what I built instead.
It has been my daily driver for months, and today it's open source under Apache 2.0.

[video: 90-second hero demo]

## What it is

A local daemon that owns your agent sessions, and a web UI that is the control plane for all of them.

- **Sessions that don't die.** A separate supervisor process owns the PTYs.
  Restart the daemon, rebuild the app, redeploy a new version - every session survives, scrollback intact.
  This is the foundation everything else sits on, and it's the thing I refuse to live without now.
- **Real status, not vibes.** swe-mux knows whether each agent is working, idle, awaiting your input, or stuck - detected from the actual terminal and the harness's own signals, hardened against a corpus of captured real sessions.
  Notifications fire when an agent genuinely needs you, not when a spinner redraws.
- **A prompt queue with rules.** Stage prompts per session; they deliver when the agent is actually ready.
  Auto-delivery is gated, capped, and off by default.
- **Parallel work that lands safely.** Agents work in git worktrees.
  A land queue reconciles each finished branch, runs your verification gate, and fast-forwards the trunk - one branch at a time, conflicts and failures handed back to the agent that owns them.
  An agent cannot approve its own gate.
- **Provenance.** Per-session change maps and commit attribution, so "which agent wrote this" has an answer.
- **Your phone is a first-class client.** Over your tailnet, as a PWA, with push notifications.
  Voice control works: local Whisper transcription, wake word, and you can send a prompt or check the fleet without touching a keyboard.
- **Agents can see each other, within limits.** An MCP surface lets a session read fleet status, watch a sibling until it settles, and request messages or new sessions - with a human approving anything that crosses a boundary.

## What it is not

- Not a cloud service. Everything is local: SQLite, your filesystem, your tailnet. No accounts, no telemetry, no server anywhere.
- Not a wrapper. It runs the CLIs you already run, in real terminals it owns. Your existing workflow, config, and subscriptions are untouched.
- Not magic. It is a lot of software. The tutorial covers the minimum; the rest is opt-in as you need it.

## Platform honesty

Windows-first today - that's the machine I live on, and it's the platform this class of tool usually ignores.
Linux runs via the source install; a WSL bridge covers agents living inside WSL.
Native Linux/macOS desktop shells are on the roadmap, and the roadmap is public.

## Install

```
uv tool install swe-mux
mux doctor
```

On Windows, `uv tool install "swe-mux[desktop]"` instead, for the native window and the tray icon.
`pipx` works the same way if you don't run uv.

Verified 2026-08-28 against the published 0.1.0 wheel by running each command into a throwaway environment.
[verify: whether a signed desktop artifact exists by the time this posts - the v0.1.0 release page carries only the wheel, the sdist, and `version.json`, so "grab the desktop app from the releases page" is not yet a sentence this post can contain]

## Why open source

I built this for myself and it's better with more hands on it.
Apache 2.0, DCO sign-off, no CLA - the core can't be relicensed out from under you, which is deliberate.

Repo: github.com/[org]/swe-mux
Site and docs: swemux.dev

If you run more than one agent, try it and tell me where it breaks.
