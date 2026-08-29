# YouTube outreach

**Step 7 (the slow burn), and only after the hero video exists** - there is nothing to cover without one.

Target: small-to-mid AI-tooling channels that review agent workflows and dev tools (the ones covering claude-squad, herdr, Conductor and similar - build the actual list the week of launch by searching those names and noting who covered them).
Skip mega-channels; the hit rate is zero and the small channels' audiences convert better anyway.
Personalize the first line per channel or don't send it.

## Email template

**Subject:** Open-source agent-fleet tool your [recent video topic] audience would use

**Body:**

Hi [name] - your video on [specific video, one clause on why it's relevant] is why I'm writing.

I just open-sourced swe-mux (Apache 2.0). For developers running multiple coding agents locally, swe-mux shows what each agent actually did and lands finished branches behind checks you approved.
The demo-able moments, since that's what matters for video:

- An agent's own summary of its turn beside the recorded facts - file writes hashed on the bytes actually written, commands with their exit codes - in the cases where the two disagree
- Five agent branches landing themselves serially behind a test gate, with a conflict bouncing back to the agent that caused it
- A commit log where every commit names the session and conversation that produced it
- A fleet view where each agent's status (working / ready / needs-you / blocked) updates live
- Driving the whole fleet from a phone, then by voice ("which sessions need me")
- Rebuilding and redeploying the app while agent sessions keep running on screen (this one needs the supervisor switched on, which is worth saying in the video rather than glossing)

90-second demo: [video URL]
Repo: github.com/jatoran/swe-mux

If it's a fit, I'm glad to do a walkthrough call, give you a guided setup, or just answer questions async.
No expectations either way - the tool is free and so is this email.

## Rules

- One follow-up after a week, then stop.
- Offer early access to notable releases going forward if they cover it.
- Never pay for coverage; never ask for a script review.
