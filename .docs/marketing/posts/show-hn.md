# Show HN

One shot. Tuesday-Thursday, 8-10am ET.
Link target: the GitHub repo (HN convention for Show HN of open source; the site is in the README's first line).
Be in the comments all day - answer everything, concede real criticism fast, never get defensive.

Rules, from the [Show HN guidelines](https://news.ycombinator.com/showhn.html) and the [site guidelines](https://news.ycombinator.com/newsguidelines.html), read 2026-08-28:

- Show HN is for "something you've made that other people can play with", and it must be easy to try "without barriers such as signups or emails". swe-mux qualifies and has no signup at all - worth one sentence in the text, because in this category that is unusual enough to be a point rather than a footnote.
- **"Please don't ask friends to upvote or comment. That's not ok on HN."** Not one person. It is detectable and it is the one unrecoverable mistake available here.
- Blog posts and other reading material are off topic for Show HN and go in as ordinary submissions. That is exactly the split this plan already uses: the engineering posts are separate submissions, never part of this one.
- "Please don't delete and repost." A weak result stands; it does not get retried.
- Own work is fine "part of the time", not as the account's primary use. Have ordinary participation on the account first.
- Do not editorialize the title.

## Title

Primary:

> Show HN: swe-mux - mission control for a fleet of coding agents

Alternates (pick by what the demo GIF leads with):

> Show HN: swe-mux - run parallel coding agents whose sessions never die

> Show HN: I built a control plane for running many coding agents at once

## Text (first comment, posted immediately by the submitter)

I run multiple coding agents all day (Claude Code, Codex, opencode) and got tired of the pile of terminals, dead sessions, and manually polling each one to see if it needed me.
swe-mux is the tool I built and have been living in for months, now open source under Apache 2.0.

The core ideas:

- A separate supervisor process owns the PTYs, so sessions survive daemon restarts, app rebuilds, and full redeploys.
  I regularly ship a new build of swe-mux from an agent session running inside swe-mux, and nothing dies.
- Status detection that's actually trustworthy (working / idle / awaiting-you / stuck), hardened against a regression corpus of captured real sessions.
  Notifications only fire when an agent genuinely needs a human.
- Agents work in parallel git worktrees, and a land queue merges finished branches: reconcile, run the verification gate, fast-forward-only onto trunk, one at a time.
  Conflicts and failed gates go back to the agent that owns the branch.
  An agent cannot approve its own gate - the verify command is human-approved as exact bytes.
- The whole fleet is operable from a phone over your tailnet (PWA + push), including by voice - STT runs locally.
- Local-only: no cloud, no accounts, no telemetry, SQLite on your disk.
  Updates are static files plus GitHub Releases; there is no server anywhere.

Honest caveats: it's Windows-first (my daily machine; Linux via source install, WSL bridge for agents inside WSL), and it's a lot of software - the tutorial covers the minimum and the rest is opt-in.

There are several tools in this space now (herdr, claude-squad, Vibe Kanban).
The bets swe-mux makes differently: sessions that survive everything, merge safety by construction rather than by trust, and the phone/voice path.

Happy to answer anything about the PTY supervisor split, the land queue's safety model, or what it's like to be babysat by your own tool while modifying it.

## Prepared comment answers

- **"Why Windows first?"** - It's my daily machine, and this category of tooling historically treats Windows as an afterthought. The platform seams are in and Linux runs from source; native shells are roadmapped and the roadmap is public.
- **"How is this different from herdr/claude-squad/etc?"** - Point at the differentiation honestly, credit them, no sniping. Session-preserving supervisor, land queue with the bytes-approval model, provenance, phone/voice.
- **"Electron?"** - No. Web UI served by a local daemon; the desktop shell is a WebView wrapper around it. The browser tab works identically.
- **"Does it phone home?"** - No. Nothing leaves the machine except the daily static version.json fetch, which is documented and disableable. [verify: it is disableable]
- **"AGPL would protect you better."** - Deliberate choice, documented in the repo: Apache 2.0 + DCO, no CLA, so the core can't be relicensed later - by anyone, including me.
