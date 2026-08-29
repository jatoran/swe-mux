# X launch thread

Post the morning of Show HN.
Every tweet with a claim gets a clip; the thread is a highlight reel, not an essay.
After launch, the cadence is one clip-per-feature post every few days - each feature GIF is a standalone post with two sentences.

## Launch thread

**1/**
I've spent months running a fleet of coding agents from one screen - and from my phone.
Today the tool is open source.

For developers running multiple coding agents locally, swe-mux shows what each agent actually did and lands finished branches behind checks you approved.
Apache 2.0.
[video: hero demo]

**2/**
The core: a record taken from the work, not from the agent's account of it.
Every file write hashed on the bytes actually written. Every command with its exit class. Test output parsed down to the failing set.
Because "I ran the tests" is a claim, and the exit code is a fact.
[gif: the evidence view]

**3/**
Every commit carries which session and which conversation produced it, split into committer and contributor.
After an afternoon of eight parallel agents, "who wrote this line" has an answer that nobody had to remember.
[gif: provenance in the commit log]

**4/**
Parallel agents, safe merging.
Each agent works in its own git worktree.
A land queue reconciles each finished branch, runs the verification command whose exact bytes YOU approved, and fast-forwards trunk - one at a time.
Fast-forward-only means the trunk step can't lose work by construction, not by being clever.
An agent cannot approve its own gate.
[gif: land queue landing branches]

**5/**
It knows which agent needs you, and it is honest about when it doesn't.
Working / ready / awaiting-input / blocked, from harness hooks + transcript + terminal + the CLI's own state, regression-tested against captured real sessions.
Where the layers disagree it goes conservative instead of confident.
[gif: status board]

**6/**
The whole fleet runs from my phone.
Tailnet + PWA + push. No relay, no swe-mux login.
And voice: speech-to-text decoded on my own machine, wake word, fleet queries and prompts spoken instead of typed.
[video: phone + voice clip]

**7/**
Where it runs:
- SQLite on your disk, no account, no telemetry
- no backend or relay I operate, updates included (static manifest + GitHub Releases)
- your CLIs talk to their own vendors under your own subscription
Not a tool with no network in it - the optional bits that reach out are named in the README, and each is off until you turn it on.

**8/**
Two things I'd rather you hear from me.
Session survival needs a config switch (`pty_supervisor_enabled`) - it ships off, because updating that supervisor reaps every live session.
And most of the control plane is per-Project opt-in and off. A fresh install is quieter than this thread.

**9/**
Open source, Apache 2.0, DCO (no CLA - the core can't be relicensed, ever, by anyone).
Windows-first, Linux headless plus a browser, roadmap is public.

Repo: github.com/jatoran/swe-mux
Site: swemux.dev

Break it and tell me how.

## Follow-up clip posts (bank these, one every 2-4 days)

- The provenance view: which agent wrote which commit
- An agent's own summary beside the recorded facts, where the two disagree
- A conflict bouncing back to its agent and the agent resolving it
- Orchestrator session fanning out sub-sessions into worktrees
- Voice: "which sessions need me" answered out loud
- Approving a permission request from a phone notification
- Redeploy triggered from inside a session of the app being redeployed (with the supervisor on, and say so in the post)
