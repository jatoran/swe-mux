# Show HN

**Step 6, and its precondition is activation rather than assets** ([`../GTM_ROADMAP.md`](../GTM_ROADMAP.md) § Launch sequencing).
Repeat users have to exist before this fires: the front page cannot be retried, and spending it on a tool people install once and abandon proves that in public, durably.

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

> Show HN: swe-mux - see what each coding agent actually did, and land it behind your checks

Alternate:

> Show HN: I built a control plane for running many coding agents at once

**Deleted, and do not restore it:** "run parallel coding agents whose sessions never die".
Sessions are not immortal, and the supervisor that makes them durable ships off.
That title is the fastest possible way to have the top comment be a link to `config.py`.

## Text (first comment, posted immediately by the submitter)

I run multiple coding agents all day (Claude Code, Codex, opencode) and the thing that actually got hard was not keeping them alive - it was answering "what did each of these do, and is it safe to merge?" eight times an afternoon.
swe-mux is the tool I built and have been living in for months, now open source under Apache 2.0.

The core ideas:

- **A record taken from the work rather than from the agent's report of it.** Every file write hashed on the exact bytes written, every command with its exit class, test output parsed to the failing set, git operations, tool calls, each with a pointer back to the moment it happened. Commits are attributed to the session and conversation that produced them, split into committer and contributor.
- **Agents work in parallel git worktrees, and a land queue merges finished branches**: reconcile, run the verification gate, fast-forward-only onto trunk, one at a time. Fast-forward-only is the load-bearing part - Git refuses it on divergence and refuses to overwrite local changes, so the trunk step cannot lose work by construction rather than by the pipeline being clever. Conflicts and failed gates go back to the agent that owns the branch, and an agent cannot approve its own gate: the verify command is human-approved as exact bytes, and editing it invalidates the approval.
- **Status detection that is conservative rather than confident** (working / ready / awaiting-you / blocked), built from harness hooks, the transcript, the terminal and the CLI's own state, hardened against a regression corpus of captured real sessions, with every transition in a durable ledger you can read hours later. Ambiguous evidence resolves to the prior; there are explicit unknown states and they stay unknown.
- **The whole fleet is operable from a phone over your tailnet** (PWA + push), including by voice. Speech-to-text decodes on your own machine in both shipped configurations - faster-whisper, or Windows Speech Recognition. No cloud speech path.
- **A separate supervisor process can own the PTYs**, so sessions survive daemon restarts, app rebuilds and full redeploys; I ship new builds of swe-mux from an agent session running inside swe-mux. **This ships off** (`pty_supervisor_enabled` defaults to false) because a supervisor update reaps every live session and I have not validated it on machines that aren't mine. With it off, cold session recovery brings sessions back as readable, resumable rows rather than losing them.
- **It runs on your own machine** - SQLite on your disk, no account, no telemetry, and no backend or relay I operate, updates included (static manifest plus GitHub Releases). It is not a tool with no network in it: your CLIs talk to their own vendors under your subscription, and OpenRouter, web push, Hugging Face model downloads and Edge TTS are each optional and off until you turn them on. The one request swe-mux makes for itself is a daily static `version.json` fetch with no identifier on it, and it is disableable.

Honest caveats, up front:

- It's **Windows-first** (my daily machine). Linux runs headless plus a browser; macOS is implemented but its CI leg isn't required to pass yet, so I won't claim it.
- It's **a lot of software**, and the tutorial covers the minimum.
- **Almost all of the control plane ships off.** Automations are per-Project opt-in, the land queue needs a switch plus an opt-in plus a grant plus an approved command, and the model-backed pieces are off. That's deliberate and it means a fresh install is quieter than this list.
- The Windows installer is **unsigned**, so SmartScreen warns. PyPI avoids that.

There are several tools in this space now (herdr, Orca, claude-squad, Vibe Kanban), and several of them do persistence, worktrees, mobile and Windows as well as or better than I do.
The bets swe-mux makes differently are the evidence layer, the provenance, and merge safety by construction rather than by trust.

Happy to answer anything about the land queue's safety model, the status corpus, or what it's like to be babysat by your own tool while modifying it.

## Prepared comment answers

- **"Why Windows first?"** - It's my daily machine, and this category of tooling historically treats Windows as an afterthought. The platform seams are in and Linux runs headless plus a browser; the roadmap is public.
- **"How is this different from herdr/Orca/claude-squad?"** - Credit them, no sniping, and concede the commodity axes immediately: persistence, worktrees, mobile, Windows and arbitrary CLI support are table stakes now and several of them are ahead on install size and platform count. The differences worth defending are the deterministic evidence layer, commit provenance, the approved-bytes land gate, and the interrupt budget.
- **"So sessions don't actually survive by default?"** - Correct, and I'd rather say it than have it found. It's a config edit plus a restart. The reason it's off is that a supervisor update reaps every live session, so it is the component I am least willing to change, and it hasn't been validated on a machine that isn't mine. Cold session recovery covers the default case: the sessions come back readable and resumable.
- **"Electron?"** - No. Web UI served by a local daemon; the desktop shell is a WebView wrapper around it. The browser tab works identically.
- **"Does it phone home?"** - No. One daily fetch of a static `version.json`, identical for every install, no query string or identifier, and `update_check_enabled` turns it off. Verified against `config.py`.
- **"You say local but the agents call Anthropic."** - Yes, and the post says so. The agent CLIs are yours and talk to their vendors under your subscription; what's local is the control plane, the data, and the absence of anything I operate in between.
- **"AGPL would protect you better."** - Deliberate choice, documented in the repo: Apache 2.0 + DCO, no CLA, so the core can't be relicensed later - by anyone, including me.
