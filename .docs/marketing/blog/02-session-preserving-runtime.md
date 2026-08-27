# How I rebuild and redeploy my agent runtime without killing a single session

*Engineering post. The strongest aggregator candidate: submit to HN, lobste.rs, r/programming. Standalone - assumes no knowledge of swe-mux.*

---

Here's the problem that made me restructure the whole system: I develop the tool inside the tool.
My coding agents run inside swe-mux, and some of those agents are working *on* swe-mux.
Every backend change used to mean restarting the daemon, and restarting the daemon meant every PTY died with it.
Eight agents mid-task, gone, because I fixed a typo in a route handler.

That's not a papercut, it's a design failure.
So the process model is now built around one rule: **the thing that restarts often must not be the thing that owns the sessions.**

## The split

Two processes:

- A **PTY supervisor** that owns every terminal session.
  It is deliberately boring: spawn a PTY, pump bytes, keep scrollback, answer a small message protocol.
  It changes rarely and is treated as near-frozen, because a supervisor update is the one thing that genuinely costs every live session.
- The **daemon**: HTTP/WebSocket server, status detection, queues, automation, all the logic that actually evolves.
  It connects to the supervisor, and it can die and come back whenever it wants.

`POST /api/daemon/restart` restarts the daemon in place.
Sessions don't notice.
This one endpoint changed how fast I can iterate more than anything else in the project.

[gif: daemon restart with eight live sessions, scrollback intact]

## Redeploying the frozen app, staged

The desktop build is a frozen bundle, which raises the stakes: a redeploy replaces the executable, not just the code.
The redeploy is staged so a bad build can't take the running app down with it:

1. Build the new bundle into a staging directory while the old app keeps running.
2. Only after a successful build, stop the old app and swap directories.
3. Health-check the new one.
   If it never turns healthy, roll back to the previous bundle automatically and keep the bad one for autopsy.

The supervisor - and every session it owns - rides through the whole thing.
I trigger this from a button in the UI, sometimes from my phone, sometimes *from an agent session running inside the app being replaced*.
That last one felt illegal the first time it worked.

## The Windows details that will bite you

A few things I learned the expensive way:

- **Job objects inherit.** If the daemon is relaunched from inside a session's process tree, it joins that session's job object and dies when the session is killed.
  The relaunch has to break out deliberately.
- **File locks make swaps fail late.** Antivirus scanning a fresh bundle holds locks exactly when you want to rename directories.
  Retries with backoff, and a fallback that relaunches the old bundle rather than leaving nothing running.
- **A bound port is not a ready daemon.** Health has to report readiness, not reachability, or every consumer (tray, redeploy wait, browser reload) declares victory during startup and acts on a half-built runtime.

## What this buys

Iteration speed, obviously.
But also trust: I stopped hesitating before restarting things, because restarts stopped being destructive.
When your infrastructure makes the safe action cheap, you take it constantly.

swe-mux is open source (Apache 2.0): github.com/[org]/swe-mux.
The full design doc for this mechanism ships in the repo.
