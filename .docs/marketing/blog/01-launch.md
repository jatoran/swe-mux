# swe-mux: what each agent actually did, and a safe way to land it

*Launch post. Canonical announcement; everything else links here. Cross-post to dev.to and Hashnode with canonical URL set to swemux.dev.*

---

I run a lot of coding agents.
Claude Code, Codex, opencode, several at once, most of the day.
The tooling for running *one* agent is fine.
The tooling for running eight of them across five projects is a pile of terminal windows, and a nagging question you cannot answer: what did each of those actually do, and is it safe to merge?

swe-mux is what I built instead.
It has been my daily driver for months, and today it's open source under Apache 2.0.

**For developers running multiple coding agents locally, swe-mux shows what each agent actually did and lands finished branches behind checks you approved.**

[video: 90-second hero demo]

## What it is

A local daemon that owns your agent sessions, and a web UI that is the control plane for all of them.

- **A record taken from the work, not from the agent's account of it.** Every file write hashed on the exact bytes written, every command with its exit class, test output parsed down to the failing set, git operations, tool calls - each keeping a pointer back to the moment it happened. If you have ever watched an agent confidently report tests passing that it never ran, this is the part that answers it.
- **Commit-level provenance.** Which session and which conversation produced a commit, split into committer and contributor, from that same deterministic capture. "Which agent wrote this" has an answer.
- **Parallel work that lands safely.** Agents work in git worktrees. A land queue reconciles each finished branch, runs the verification command whose exact bytes you approved, and fast-forwards the trunk - one branch at a time, conflicts and failures handed back to the agent that owns them. An agent cannot approve its own gate.
- **Real status, not vibes.** swe-mux knows whether each agent is working, ready, awaiting your approval, or blocked - built from harness hooks, the transcript, the terminal, and the CLI's own state, hardened against a corpus of captured real sessions, with every transition in a durable ledger.
- **A prompt queue with rules.** Stage prompts per session; they deliver when the agent is actually ready. Auto-delivery is gated, capped, and off by default.
- **Your phone is a first-class client.** Over your tailnet, as a PWA, with push notifications. Voice works too: speech-to-text decodes on your own machine, with a wake word, so you can send a prompt or check the fleet without touching a keyboard.
- **Terminals that outlive the app.** A separate supervisor process owns the PTYs, so a daemon restart or a full app redeploy leaves every session running, scrollback intact. Behind it, cold session recovery covers what a supervisor cannot survive - its own crash, a force close, a power loss - by bringing those sessions back readable and resumable.
- **Agents can see each other, within limits.** An MCP surface lets a session read fleet status, watch a sibling until it settles, and request messages or new sessions - with a human approving anything that crosses a boundary.

## What ships off, because you should know before you install it

This is the part I would rather you read here than discover.

- **Every automation is per-Project opt-in and ships off**, with one exception: a permission gate that reads nothing, runs nothing, and spends nothing.
- **The land queue needs four things** before an agent can trigger one: the install-wide switch, the Project's opt-in, an authority level raised from its default of "a human approves the request", and an approved verification command. Running it yourself needs the last of those.
- **The model-backed pieces ship off**: the behaviour timeline, the attention observers, the assistant. So does read aloud.

That is a safety posture I would defend, and it means a fresh install is quieter than the feature list. Turning things on is a few minutes; being surprised by what was already running would be worse.

## What it is not

- **Not a cloud service.** It runs on your own machine: SQLite on your disk, no account, no telemetry, and no backend or relay this project operates. It is not a tool with no network in it: your agent CLIs talk to their own vendors under your own subscription, and four optional features reach out when you turn them on - model calls through an OpenRouter-compatible endpoint with your key, web push through your browser vendor, the on-device speech models downloaded once from Hugging Face, and experimental Edge TTS. swe-mux itself makes one request on its own behalf: a daily fetch of a static `version.json`, identical for every install, carrying no identifier, and disableable.
- **Not a wrapper.** It runs the CLIs you already run, in real terminals it owns. Your existing workflow, config, and subscriptions are untouched.
- **Not magic.** It is a lot of software. The tutorial covers the minimum; the rest is opt-in as you need it.

## Platform honesty

Windows-first today - that's the machine I live on, and it's the platform this class of tool usually ignores.
Linux runs headless plus a browser; a WSL bridge covers agents living inside WSL.
CI installs the wheel and runs the CLI on all three hosts, and starts a real daemon from the source checkout on Linux and Windows, but nothing starts a daemon from a published artifact anywhere, so Windows is the only platform I will claim proves the product running.
macOS is implemented and its CI leg runs the whole suite, but that leg is not yet required to pass. Treat it as unproven.

## Install

```
uv tool install swe-mux
mux doctor
```

On Windows, `uv tool install "swe-mux[desktop]"` instead, for the native window and the tray icon.
`pipx` works the same way if you don't run uv.

There is also a **Windows installer** and a portable archive on the releases page, from v0.1.2 onward.
It is not code signed, so SmartScreen warns on first run and you have to choose to continue; signing is planned and needs a certificate.
The PyPI install avoids that prompt entirely.

Verified 2026-08-28 against the published wheel by running each command into a throwaway environment.

## Why open source

I built this for myself and it's better with more hands on it.
Apache 2.0, DCO sign-off, no CLA - the core can't be relicensed out from under you, which is deliberate.

Repo: github.com/jatoran/swe-mux
Site and docs: swemux.dev

If you run more than one agent, try it and tell me where it breaks.
