# YouTube outreach

Target: small-to-mid AI-tooling channels that review agent workflows and dev tools (the ones covering claude-squad, herdr, Conductor and similar - build the actual list the week of launch by searching those names and noting who covered them).
Skip mega-channels; the hit rate is zero and the small channels' audiences convert better anyway.
Personalize the first line per channel or don't send it.

## Email template

**Subject:** Open-source agent-fleet tool your [recent video topic] audience would use

**Body:**

Hi [name] - your video on [specific video, one clause on why it's relevant] is why I'm writing.

I just open-sourced swe-mux (Apache 2.0): mission control for running many coding agents at once.
The demo-able moments, since that's what matters for video:

- Kill the daemon, rebuild the app, redeploy - every agent session survives on screen, scrollback intact
- A fleet view where each agent's real status (working / idle / needs-you) updates live
- Five agent branches landing themselves serially behind a test gate, with a conflict bouncing back to the agent that caused it
- Driving the whole fleet from a phone, then by voice ("which sessions need me")

90-second demo: [video URL]
Repo: github.com/[org]/swe-mux

If it's a fit, I'm glad to do a walkthrough call, give you a guided setup, or just answer questions async.
No expectations either way - the tool is free and so is this email.

## Rules

- One follow-up after a week, then stop.
- Offer early access to notable releases going forward if they cover it.
- Never pay for coverage; never ask for a script review.
