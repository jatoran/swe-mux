# X launch thread

Post the morning of Show HN.
Every tweet with a claim gets a clip; the thread is a highlight reel, not an essay.
After launch, the cadence is one clip-per-feature post every few days - each feature GIF is a standalone post with two sentences.

## Launch thread

**1/**
I've spent months running a fleet of coding agents from one screen - and from my phone.
Today the tool is open source.

swe-mux: mission control for your coding-agent fleet.
Apache 2.0, fully local.
[video: hero demo]

**2/**
The foundation: sessions that don't die.
A supervisor process owns the PTYs, so daemon restarts, rebuilds, even full app redeploys - every agent session survives, scrollback intact.
I ship new builds of swe-mux from an agent running *inside* swe-mux.
[gif: redeploy with live sessions]

**3/**
It knows which agent actually needs you.
Working / idle / awaiting-input / stuck, detected from harness signals + the terminal, regression-tested against captured real sessions.
Notifications fire on "needs a human," not on "spinner moved."
[gif: status board]

**4/**
Parallel agents, safe merging.
Each agent works in its own git worktree.
A land queue reconciles each finished branch, runs YOUR test gate, and fast-forwards trunk - one at a time.
Conflicts go back to the agent that owns them.
An agent cannot approve its own gate.
[gif: land queue landing branches]

**5/**
The whole fleet runs from my phone.
Tailnet + PWA + push.
And voice: local Whisper STT, wake word, fleet queries and prompts spoken instead of typed.
No relay server. Nothing leaves your machines.
[video: phone + voice clip]

**6/**
Local-only is the whole design:
- SQLite on your disk
- no cloud, no accounts, no telemetry
- updates = static manifest + GitHub Releases
The project runs zero servers. There is nothing to breach and nothing to shut down.

**7/**
Open source, Apache 2.0, DCO (no CLA - the core can't be relicensed, ever, by anyone).
Windows-first, Linux from source, roadmap is public.

Repo: github.com/jatoran/swe-mux
Site: swemux.dev

Break it and tell me how.

## Follow-up clip posts (bank these, one every 2-4 days)

- Orchestrator session fanning out sub-sessions into worktrees
- Voice: "which sessions need me" answered out loud
- A conflict bouncing back to its agent and the agent resolving it
- Approving a permission request from a phone notification
- The provenance view: which agent wrote which commit
- Redeploy triggered from inside a session of the app being redeployed
