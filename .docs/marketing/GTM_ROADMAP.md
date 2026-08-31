# Go-to-market roadmap

The plan for taking swe-mux from published-but-unannounced to known, written for one maintainer.

Nothing has been announced anywhere as of 2026-08-28.
The repository is public, `swe-mux` 0.1.2 is on PyPI, v0.1.2 on GitHub Releases carries an unsigned Windows installer and a portable archive beside the wheel, `swemux.dev` is live with real screenshots, CI runs on three hosts, and an X account exists at <https://x.com/swemux>.
No post, submission, or email has gone out.
That is a good position: every mistake below is still cheap, and the first impression has not been spent.

This document is the strategy and the sequence.
Its supports are [`LAUNCH_CHECKLIST.md`](LAUNCH_CHECKLIST.md) (what must be true before each beat fires), [`OUTREACH_TRACKER.md`](OUTREACH_TRACKER.md) (the two beta cohorts and their trackers), and the per-venue copy in [`posts/`](posts/) and [`blog/`](blog/).

## Positioning

### The line

Used verbatim everywhere the project explains itself:

> **For developers running multiple coding agents locally, swe-mux shows what each agent actually did and lands finished branches behind checks you approved.**

Do not restate it in other words in any draft.
One string, everywhere, or the project reads as several projects.

### The tagline is a tagline

> swe-mux - mission control for your coding-agent fleet.

This survives as a **brand tagline**: the repository description, a directory listing name, an X bio.
It is never the explanatory claim and never stands in for the line above.

### Why this centre, and not the four the project used to have

The project told at least four stories at once: "mission control for your coding-agent fleet", "terminal multiplexer / agent control plane / mobile parity", deterministic evidence and attention as the moat, and session persistence plus landing plus provenance plus mobile.
Consolidating them is the whole point of this section, and the choice of which one to keep is an argument about the market rather than about taste.

**Persistence, worktrees, mobile, Windows, and arbitrary-CLI support are commodity in this space now.**
Checked 2026-08-28: Orca advertises Windows, native mobile apps, parallel worktrees, persistent scrollback, account switching, and arbitrary CLI agents.
Herdr owns much of the persistent-runtime story with a far smaller install and a large head start on adoption.
Leading with any of those means leading with a row the reader can already tick against two other tools, and inviting a comparison that is not flattering on install size or platform count.

**What is hard to copy here is evidence, provenance, controlled interruption, and approved landing.**
Those rest on a corpus, a fact store, a ledger, and a git pipeline that took a long time to build and are not a weekend of work for a competitor.
That is where the line points.

The one to under-claim rather than over-claim is unchanged: **the phone and voice path is genuinely differentiating, and Orca ships native mobile apps.**
Say "installable PWA over your own tailnet with no relay", not "the only one with mobile".

### The five proof points, in this order

1. **One normalized view across agent CLIs.** One status vocabulary, one transcript reader, one history search, one account switcher, over every harness in the registry.
2. **Deterministic evidence rather than agent self-report.** Facts hashed on the exact bytes written, commit provenance split into committer and contributor, model-free detectors, and a status ledger readable hours later.
3. **Safe serialized landing.** Reconcile, run the repository's verification command, fast-forward only, one branch at a time. An agent cannot authorise the gate its own land runs.
4. **Full phone access over your own tailnet.** Terminals, git review, approvals, and local speech-to-text, with no relay and no swe-mux login.
5. **Durable terminals when supervision is enabled.** A separate supervisor process can own the pseudoterminals so sessions outlive a daemon restart and a full redeploy. It ships off; see § The claim audit.

### One paragraph

If you run one coding agent, the vendor's CLI is enough and swe-mux is overhead.
If you run five across three repositories, you have quietly taken a second job: polling each pane to find out which agent is stuck versus thinking, taking each agent's word for what it did, retyping prompts into a session that was not ready for them, and hand-merging branches that all finish within the same hour.
swe-mux is that second job, made into software.
It does not replace the agents and does not proxy them: your CLIs keep talking to their own vendors under your own subscription, in real pseudoterminals, with their own transcripts untouched.
What it adds is a record of what each of them actually did, taken from the bytes they wrote rather than from their account of the work, and a pipeline that lands a finished branch behind a command you approved.

## The claim audit

Run 2026-08-28 against the source rather than against the drafts, because the engineering documentation in this repository is markedly more precise than the marketing copy was, and the gap was all in one direction.

The rule being enforced, which [`README.md`](README.md) already stated: **a claim must be true of the shipped artifact, in its default configuration, on the day it posts.**

### Session survival: the finding, and the decision that answered it

**The finding.** `pty_supervisor_enabled` defaulted to `False` (`src/swe_mux/config.py`) while almost every draft led with sessions surviving restarts.
That was the sharpest overstatement in the set and the one a hostile reader finds in about ninety seconds, because the default is one grep away in a public repository.

**The decision, taken by the operator on 2026-08-28: flip the default rather than qualify the sentence.**
This reverses the position this package started from, which was to fix the copy and record the default as an open question.
The reasoning behind the reversal is that session survival is a headline capability rather than a power-user mode, and marketing that reads like a config manual is the wrong way to resolve a mismatch that the product can resolve instead.

So the copy is unconditional:

> Terminals are held by a supervisor process separate from the daemon and the UI, so a daemon restart or a full app rebuild leaves the agents working with their scrollback intact, and reconnecting replays only the bytes you missed.
> A supervisor cannot survive its own death, and that is the honest edge of the claim: a crash, a force close, or a power loss takes the processes with it.
> Cold session recovery is the layer behind that, bringing those sessions back as readable, resumable rows carrying what they last printed.

Three things about that are worth keeping.

**The conceded edge is not a hedge and must not be cut.** It is what stops the claim being absolute, and an absolute claim about process survival is the one a reader can disprove. It also happens to describe a real second layer, so it adds a capability rather than subtracting confidence.

**No config key name appears in user-facing copy.** The key is in this document and in `.docs/`; it is in no post, no README feature bullet, and nothing on the site.

**The copy does not land ahead of the default**, and that is asserted rather than remembered.
The default flip is a separate agent's change on a separate branch, and `site/tools/check.mjs` now fails if `index.html` states the claim unconditionally while `config.py` still reads `False`.
Two branches holding two halves of one truthful sentence is exactly the shape that ships a lie when somebody lands them in the wrong order.

### The other five

| Claim as written | Verdict | What it says now |
|---|---|---|
| "Sessions never die" | False as an absolute, and false as a default in either reading | Deleted. The Show HN alternate title carrying it is deleted too |
| "No server anywhere", "zero servers" | False. The product is a local aiohttp daemon; that is the whole architecture | "No vendor-operated backend or relay." Just as strong a line and survives the correction |
| "Fully local" | Misleading. The agent CLIs call cloud providers, and OpenRouter, web push, Hugging Face model downloads, the update check and Edge TTS all reach the network | "Runs on your own machine", followed by the list of what crosses the network and which switch governs it |
| "Notifications only fire when an agent genuinely needs a human" | Too absolute for a multi-signal detector with explicit `unknown` readings. Alerts also fire on plain turn completion, which is not "needs a human" | The five reasons named, the three suppression rules named, and the detector described as resolving ambiguity to a conservative prior rather than as being right |
| "STT runs locally" | **Accurate**, and it was worth checking. Both shipped engines decode on the host: faster-whisper (the default, `stt_engine = "whisper"`) and Windows Speech Recognition. There is no browser or cloud speech path | Kept, with the configuration named and the one-time Hugging Face model download stated |

### What was checked and found already accurate

Recorded so the next audit does not re-derive it.

- **The update check.** `README.md`'s description of the daily static `version.json` fetch, its lack of any identifier, and `update_check_enabled` all match `config.py`.
- **The land queue's safety model.** Fast-forward-only, the exact-bytes gate approval, an agent being unable to approve its own gate, and conflicts and failed gates returning to the branch's agent are all as the drafts describe them.
- **Provenance.** The committer/contributor split from deterministic capture is real and is not the agent's self-report.
- **The platform claims.** They already stopped at the right place: the wheel installs and the CLI runs on all three hosts in CI, and no CI job starts a daemon from a published artifact anywhere.
- **"Nothing runs on a Project that did not opt in."** True. Exactly one automation is default-on (`session_control`), and the registry enforces that a default-on automation must read nothing, run nothing, and spend nothing.

### The rest of the control plane, stated the way the supervisor now is

Automations are per-Project opt-in and ship off.
The land queue needs the install-wide switch, the Project opt-in, a `land_grant` raised from its default of `draft`, and a verification command - approved by a human, or written on this machine in a Project that left `land_verify_grant` at its default.
The scan timeline, the attention observers, the assistant, and read-aloud all ship off.

Copy implying any of them is on is wrong, and correcting it costs the drafts a real amount of impact.
The honest way to buy that impact back is a clearly labelled **recommended fleet setup** in onboarding, which is proposed in § Open decisions and is not built.
Until it is, no draft describes it.

### Who it is for

- Someone already running two or more agent CLIs concurrently, most days, and feeling the coordination cost rather than the capability cost.
- Someone who wants that fleet reachable away from the desk without handing terminal access to a third party.
- Someone on Windows, which this category has historically treated as an afterthought.

### Who it is not for

- Someone running a single agent in a single terminal. Say this out loud in the launch copy. It is the fastest way to stop the "this is over-engineered" comment, because for that reader it genuinely is.
- Teams wanting shared/hosted orchestration. There is no multi-user model and no vendor-operated backend.
- Anyone who needs a **signed** installer today. There is an installer as of v0.1.2, and it is unsigned.

### The comparison, stated so it survives the comment thread

The rule: name the neighbours, credit what they do better, and claim only differences that are checkable from the repository.
A comparison that overclaims gets dismantled publicly, and the dismantling is what people remember.

| Tool | What it is | Where it is genuinely ahead | What swe-mux does that it does not |
|---|---|---|---|
| **tmux** | The general-purpose terminal multiplexer that keeps shells alive | Universality, decades of hardening, zero-cost ubiquity, remote-over-SSH by default | Nothing about tmux understands what a coding agent is doing. swe-mux is not a better multiplexer; it is a multiplexer that knows the difference between an agent working, an agent waiting on you, and an agent stuck |
| **herdr** ([herdrdev/herdr](https://github.com/herdrdev/herdr), Apache-2.0, ~33.2k stars, checked 2026-08-28) | "The runtime your coding agents live on" - one Rust binary, background runtime owning agent terminals, working/blocked/idle pane marks, agents drive it over a CLI and socket API | The closest neighbour and ahead on most axes: three-platform support, a single static binary, reattach from any terminal or SSH, an enormous head start in adoption, and a far smaller install | The land queue (herdr does not merge anything), commit-level provenance, a browser and phone client rather than a terminal one, and voice. Windows is beta there and is the proving platform here |
| **Orca** ([stablyai/orca](https://github.com/stablyai/orca), MIT) | Agentic development environment, desktop on three platforms plus native iOS and Android apps and a headless `orca serve` | Real native mobile apps rather than a PWA, three-platform desktop, GitHub and Linear integration, remote SSH worktrees. It also advertises Windows, parallel worktrees, persistent scrollback, account switching, and arbitrary CLI agents, which is why none of those is a differentiator here any more | The land queue and the approved-bytes gate, deterministic evidence and commit provenance, and a posture with no relay of any kind |
| **Conductor** (<https://conductor.build>) | "Run parallel Claude Code, Codex, and Cursor agents in isolated workspaces" - Mac, closed source | A focused, polished single-purpose product with a review workflow, and it is not trying to be nine things | Open source, not Mac-only, and everything the workspace layer adds beyond parallel-worktrees-with-review |
| **Warp** (<https://www.warp.dev>) | Now an agent platform: an open-source terminal plus Warp Factories, cloud fleets of agents defined as code, with evals and benchmarking | Funding, a real cloud product, evaluation infrastructure, and a terminal that is excellent on its own | Different business entirely. Warp sells the cloud that runs the fleet; swe-mux operates no backend, and the fleet is on hardware you own |
| **claude-squad**, **cmux**, **vibe-kanban**, **agent-manager** and the rest of [awesome-agent-orchestrators](https://github.com/andyrewlee/awesome-agent-orchestrators) (191 entries, checked 2026-08-28) | Mostly narrower: a TUI, a Kanban board, a worktree spawner | Simplicity. Each is one afternoon to understand, and several are one binary | swe-mux is not simpler and should never claim to be. It is what you reach for after one of those stops scaling |

The four differences to lead with, because each is checkable in the repository rather than a matter of taste, and because none of them is a row a neighbour already ticks:

1. **Deterministic evidence.** Facts hashed on the exact bytes an agent wrote, commands with their exit class, test output parsed down to the failing set. What the agent did, not what it said it did.
2. **Commit-level provenance.** Which session and conversation produced a commit, split into committer and contributor, from deterministic capture rather than from the agent's account of its own work.
3. **Approved landing.** Reconcile, run the repository's verification command, fast-forward-only onto trunk, one branch at a time. Fast-forward-only is what makes it safe for a machine: Git refuses it on divergence and refuses to overwrite local changes, so the trunk step cannot lose work by construction. An agent cannot authorise the gate its own land runs: approving bytes is a human act, and the one authority that runs an unapproved gate is the operator's standing decision about their own machine's edits, which never covers a gate another author put on the branch.
4. **Controlled interruption.** Findings merge into incidents and route by what they cost you to resolve, under a hard daily interrupt budget with an hourly burst limit, and a held-back item stays counted and visible with the reason it was suppressed.

The session-preserving split is a fifth and is **demoted rather than dropped**, for two reasons: it is off by default, and the persistent-runtime story is the one a neighbour most credibly claims.
It is still the best engineering post in the set (`blog/02`), which is a different job from being the lead claim.

The one to under-claim rather than over-claim: **the phone and voice path is genuinely differentiating, and Orca ships native mobile apps.**
Say "installable PWA over your own tailnet with no relay", not "the only one with mobile".

### The honest weaknesses, stated before someone else states them

Every one of these will surface in a comment thread.
Conceding them in the post costs nothing and buys the thread.

- **Windows-first is unusual here and costs adoption.** Windows is the only platform that proves the product running from what a user installs. CI install-smokes the wheel and starts a source-checkout daemon on all three hosts, but no CI job starts one from a published artifact and no macOS desktop artifact exists.
- **It is a lot of software.** The surface is large and the tutorial covers a small slice of it. "Not magic, it is a lot of software" is already in the launch draft; keep it.
- **Almost all of it is off until you turn it on.** This is the concession the claim audit added, and it is a real one: a fresh install has no automations, no land queue opt-in, no behaviour timeline, no attention ranking, no read-aloud, and the daemon owns the PTYs rather than a supervisor. That is defensible as a safety posture and indefensible as a surprise, so the copy says it and the onboarding proposal in § Open decisions exists to shorten the distance.
- **No signed desktop installer.** The installer exists as of v0.1.2 and is unsigned, so SmartScreen warns on first run for anyone who is not installing from PyPI.
- **One maintainer.** Say the support expectation out loud rather than implying an SLA.
- **The `voice-local` extra was uninstallable for the whole life of 0.1.0**, because the published wheel declared `en-core-web-sm`, which is on no index ([`../development/DEPENDENCY_AUDIT_2026-08-28.md`](../development/DEPENDENCY_AUDIT_2026-08-28.md) § 4). Fixed in 0.1.1 by moving it to an unpublished dependency group that an installed copy acquires on an explicit press. Kept in this list because it is the kind of thing a diligence scan surfaces and it is better conceded than discovered: the honest line is that the first published release had a broken optional extra and the repair shipped the same week.

## Launch sequencing

Eight steps, and **exactly one community launch before Show HN.**
Each states the precondition that must be true before it fires and the signal that says it worked.

Two things changed from the six-stage version, and both are corrections rather than refinements.

The old plan claimed at most one attention-day per week and then scheduled four community launches inside one week, which is not a schedule, it is four half-days pretending to be one.
And it treated Product Hunt as a committed beat while ranking it tenth by expected value.
A beat that is tenth on the list does not get a whole attention day reserved for it in advance; it gets a condition.

### 1. Artifact and claim audit

**Precondition:** none. Cheapest work in the plan and the only step with no dependency.

Two halves.
The **claim** half is done as of 2026-08-28 and is recorded in § The claim audit.
The **artifact** half is repository hygiene, which is settings rather than files: description, homepage, topics, Discussions, issue templates.
Verified against the GitHub API on 2026-08-28: no description, no topics, no homepage, Discussions **disabled**, no issue templates, no code of conduct, community profile at 57%.
`README.md` links to `discussions/categories/ideas` as the route for feature requests, and that link resolves to nothing today.

Full list and owners: [`LAUNCH_CHECKLIST.md`](LAUNCH_CHECKLIST.md) § 1.

**Success signal:** a stranger landing on the repository can tell what it is in five seconds, every route the README promises resolves, and no sentence anywhere describes a capability that a fresh install does not have.

### 2. Clean-machine testing

**Precondition:** step 1.

Install from the published artifacts onto a machine with no checkout, and record every place it falls over.
This has never been done, and the plan should stop implying that CI covers it: CI proves the wheel installs and the CLI runs on three hosts, and the `live_daemon` tier proves a daemon starts from a source checkout on two of them. Neither proves what a stranger gets.

Both install paths, because they fail differently: `uv tool install swe-mux`, and the unsigned Windows installer from the v0.1.2 release page, which is where SmartScreen enters the conversation.

**Success signal:** someone who is not the maintainer reaches a running session on their own machine without being told anything that is not in the README, on both paths.

### 3. The two-cohort beta, two weeks

**Precondition:** step 2. The install works on a machine that is not the development host.

This is the largest change in the plan and § The beta explains why.
The short version: the old trial asked people to spend twenty minutes finding install defects, which is usability testing and answers nothing about whether anyone wants to keep using this.

- **Clean-install testers**, 5 to 10 people, one scripted twenty-minute install and first session.
- **Design partners**, 5 to 10 actual parallel-agent users, two weeks, running multiple sessions, using status, and landing at least one worktree branch.

Both run under direct outreach only, no public post.
Asks, scripts, check-in cadence and trackers: [`OUTREACH_TRACKER.md`](OUTREACH_TRACKER.md).

**Success signal:** the install cohort's completion rate and its abandonment points are known, and at least half the design partners used it on three separate days.
The second half is the one that decides whether step 6 happens at all.

### 4. One niche launch

**Precondition:** step 3 complete, and each finding either fixed or written into the README as a known limit.

**One venue, not four.** Either r/ClaudeAI or the Claude Developers Discord, whichever the design partners came from least, so the post reaches people the beta did not.
Drafts in [`posts/reddit-soft-launch.md`](posts/reddit-soft-launch.md) and [`posts/discord-claude-developers.md`](posts/discord-claude-developers.md).

The sibling venues (r/ChatGPTCoding, r/LocalLLaMA, and whichever of the two above was not used) move to step 7 and are staggered there.
They were never worth an attention day each before the main beat, and grouping them into one week was the plan's least defensible line.

**Success signal:** installs reported by people you did not invite, and at least one of them comes back a second time.
Not upvotes, and not bug reports alone: a bug report proves an install, not a use.

### 5. Fix what it finds

**Precondition:** step 4.

No posting at all.
This is the step most likely to be skipped and it is the reason the launch has anything to launch.

**Success signal:** every finding from steps 3 and 4 is closed or documented, and the two cohorts' abandonment points specifically have been addressed rather than noted.

### 6. Show HN, only after activation is demonstrated

**Precondition:** everything above, **plus demonstrated activation**: repeat users exist, at least one design partner is still running it after the beta ended without being asked to, and the hero video is live.
A whole working day free, Tuesday to Thursday, roughly 8-10am ET.

Activation as a precondition is new and is the point of the resequence.
Show HN cannot be retried, and sending the front page at a tool that people install once and abandon spends the one distribution moment this project gets for free on proving that.

Submit the repository (Show HN convention for open source; the site is in the README's first line), post the prepared text as the first comment immediately, and then be in the thread all day.
The X launch thread goes out the same morning so the two reinforce rather than compete; Bluesky and LinkedIn the same day, single posts, no thread.

**Success signal:** front page at any point during the day, and **installs that turn into repeat use** in the following weeks.
Not issue count: see § Metrics for why that criterion was removed.

### 7. Engineering articles and awesome-list submissions

**Precondition:** none of the above blocks it, and none of it is time-critical.

Engineering posts on a roughly fortnightly rhythm, awesome-list and directory submissions, newsletter submissions, YouTube outreach, and the sibling community venues deferred from step 4.
This is where durable traffic comes from, and it is the part a solo maintainer can actually sustain indefinitely.

### 8. Product Hunt, conditional

**Precondition:** step 6 happened, at least a week has passed, **and earlier evidence shows interest beyond the terminal-tool audience.**

That last clause is the whole change.
Product Hunt is ranked tenth by expected value in this document's own venue table, and the previous plan still reserved a full attention day for it in week 7.
It is now conditional and unscheduled.

The evidence that would trigger it: meaningful traffic or installs from a non-developer-tooling referrer, requests from people who are not already running agent CLIs, or a newsletter pickup that reached a general audience.
Absent that, this step does not happen and nothing is lost, because the audience there skews away from people who will install a Python daemon or accept a SmartScreen warning.

If it does fire, rewrite the listing around whatever the HN thread proved, not around what the draft guessed.

**Success signal:** comments and maker replies rather than upvote count.
Product Hunt's own guidance is explicit that you may not ask for upvotes, and the ranking weights engagement over raw votes.

### The gates that hold the sequence

Two, and both are stated rather than worked, because neither is an agent's to do.

- **A signed Windows installer before the main public beat (step 6).**
  The installer exists as of v0.1.2 and is unsigned.
  Asking strangers to click through SmartScreen, or alternatively to install Python plus uv plus an extra plus the WebView2 Runtime, is avoidable conversion loss at exactly the moment the traffic is unrepeatable.
  Signing needs a certificate the operator has to buy, so this is a gate to state, not work to schedule.
  PyPI plus an unsigned installer is fine for steps 3 and 4, where every participant was invited and can be told.
- **A hero video before step 6, and before any YouTube outreach at all.**
  Unchanged from the previous plan and still true.
  The capture environment now exists (`trailer/capture_env.py`), so this is recording rather than building.

## Venues, ranked by expected value for this specific project

Ranked on: does the audience already run coding-agent CLIs, does the format suit a large local tool with no hosted demo, and what does one hour spent there return.

| Rank | Venue | Why here | Cost |
|---|---|---|---|
| 1 | **Show HN** | Highest ceiling, exactly the audience, and the engineering substance rewards a thread that goes deep | One full day of presence, one shot |
| 2 | **r/ClaudeAI + Claude Developers Discord** | Highest install-conversion per reader. These people have Claude Code open right now | Low, and repeatable across the sibling subs |
| 3 | **Awesome-list entries** | Durable, compounding, cheap, and `awesome-agent-orchestrators` is precisely this category with 191 entries and no swe-mux in it | An hour each, mostly waiting |
| 4 | **Engineering blog posts, submitted to HN and lobste.rs** | The best-quality inbound this project can generate, and the raw material already exists as post-mortems | Half a day each to write |
| 5 | **r/selfhosted** | Large, and the runs-on-your-own-machine, no-vendor-backend, Tailscale story is native to it rather than a stretch | Low |
| 6 | **X (<https://x.com/swemux>)** | Where the multi-agent-orchestration conversation lives; short clips of specific moments outperform announcements | Ongoing, low per post, needs clips to exist |
| 7 | **Newsletters (Console.dev, TLDR AI, Changelog News)** | One submission, potentially thousands of the right readers, no ongoing cost | An hour total |
| 8 | **r/commandline, r/opensource, r/codex, r/vibecoding** | Each is a real but narrower slice; the licensing angle is unusually strong in r/opensource | Low each |
| 9 | **lobste.rs** | Small but unusually high-quality readership; needs an invite and rewards engineering content only | Blocked on sourcing an invite |
| 10 | **Product Hunt** | Real traffic, poor fit for a self-hosted developer tool with no hosted demo, and the audience skews away from people who will install a Python daemon. **Conditional and unscheduled** (step 8): its rank is the reason | A day, if it fires at all |
| 11 | **YouTube outreach** | Highest variance. A single small-channel video can outperform everything above it, and most emails get nothing | An hour per personalized email |
| 12 | **Directories (AlternativeTo, selfh.st)** | Slow trickle, near-zero effort, occasionally the top referrer a year later | Half an hour each |

### Hacker News

- **Audience:** the best available for this project, and the one most likely to read the design docs.
- **Format:** Show HN, linking the repository. Post the prepared text as the first comment immediately.
- **Rules that matter** ([Show HN guidelines](https://news.ycombinator.com/showhn.html), [site guidelines](https://news.ycombinator.com/newsguidelines.html)): Show HN is for "something you've made that other people can play with", so it must be runnable and it must be easy to try "without barriers such as signups or emails" - swe-mux qualifies and has no signup at all, which is worth one sentence. "Please don't ask friends to upvote or comment. That's not ok on HN." Blog posts and reading material are explicitly off topic for Show HN and go in as ordinary submissions - which is exactly the split the blog plan already uses. "Please don't delete and repost." Own-work submissions are fine "part of the time", not as the primary use of the account.
- **Timing:** Tuesday to Thursday, 8-10am ET.
- **Failure mode:** posting and then leaving. An unanswered thread dies. The second failure mode is defensiveness - the correct response to a real criticism is to concede it in one sentence and say what you would do about it. The third is a title that oversells; HN punishes that specifically.

### Reddit

- **Audience:** r/ClaudeAI and the Claude Developers Discord convert best. r/LocalLLaMA cares about locality and will be sharp about the fact that the agent CLIs themselves call cloud models - concede that in the post, before someone says it less kindly. r/selfhosted cares about the zero-server posture. r/programming takes engineering posts as link submissions and never the announcement.
- **Format:** native text posts with the differentiated copy already drafted; link submissions only in r/programming.
- **Rules:** **not verified from primary sources in this session** - Reddit blocks automated fetching, so no rule text below was read from Reddit itself. Treat this as a checklist to read against the live sidebar the week of posting, not as a record of the rules: whether self-promotion is allowed at all, whether it requires a specific flair, whether there is a weekly self-promotion thread that is the only permitted route, whether an account-age or karma floor applies, and whether authorship must be disclosed in the post body. Disclose authorship regardless of whether a rule demands it. Post to one subreddit at a time and never the same text twice.
- **Timing:** weekday mornings US time, spaced two to three days apart.
- **Failure mode:** a first post that breaks a self-promotion rule. In several of these subreddits that costs the account, not just the post, and it is unrecoverable in the venue that converts best.

### lobste.rs

- **Audience:** small, high-signal, and disproportionately likely to read a design document.
- **Format:** the engineering posts only, with the `authored by` box ticked.
- **Rules** ([lobste.rs/about](https://lobste.rs/about)): "self-promo should be less than a quarter of one's stories and comments", so the account needs non-swe-mux activity first. On-topic means content that will "improve the reader's next program" or "deepen their understanding of their last program". Entrepreneurship, company news, and launch announcements are explicitly off topic. Invites are required and new users cannot send them for their first 70 days.
- **Timing:** with the second engineering post, not the first, and not before an invite is in hand.
- **Failure mode:** submitting the launch post or the homepage. It will be flagged as marketing, correctly, and the account starts in a hole.

### X

- **Account:** <https://x.com/swemux> is the official account. **The site footer does not link it yet** and should - that is site chrome and belongs to whoever owns `site/`, not to this package.
- **Audience:** the multi-agent-orchestration conversation is genuinely on X, and it is the target demographic.
- **Format:** the launch thread on Show HN morning, then one clip-per-feature post every two to four days. Every claim gets a clip; a claim without a clip is a tweet nobody stops for.
- **Rules:** no venue rules to break here. The constraint is entirely that the account has no audience yet, so the launch thread's reach on day one will be close to zero on its own and will be carried by the HN and Reddit traffic clicking through.
- **Failure mode:** treating X as a launch channel rather than a compounding one. It is the second: the value accrues over months of clips, not on launch day.

### Product Hunt

- **Audience:** real, and mostly not this project's. Self-hosted developer tooling with no hosted demo underperforms there relative to the effort.
- **Format:** hero video first in the gallery, then feature GIFs. Self-hunting is normal and carries no penalty.
- **Rules** ([Product Hunt launch guide](https://www.producthunt.com/launch)): you may not ask anyone to upvote. You may ask people to visit, comment, and give feedback. Coordinated voting and paid upvotes are detected and penalized. Ranking weights engagement - comments, maker replies, time on page - over raw upvotes.
- **Timing:** **conditional, and not on the calendar** (step 8). 12:01am PT, at least a week after Show HN, and only if there is evidence of interest beyond the terminal-tool audience.
- **Failure mode:** asking for upvotes in a group chat and getting the launch penalized. The second is launching it before Show HN and burning the assets on the weaker venue. The third, and the one this plan actually made, is reserving an attention day in advance for the venue it ranks tenth.

### Developer Discords and Slacks

- **Audience:** the Claude Developers Discord is the single highest same-day-install audience available.
- **Format:** one message in the showcase/community-projects channel, one GIF, then stay in the thread.
- **Rules:** confirm the current channel and its self-promotion policy before posting. Most such servers permit one showcase post and treat a second as spam.
- **Timing:** it is one of the two candidates for the single niche launch at step 4. Whichever of the two is not used there moves to step 7.
- **Failure mode:** posting in a general channel instead of the designated one.

### YouTube and short video

- **Audience:** small-to-mid AI-tooling channels covering agent workflows. Skip the large channels; the hit rate is zero and the small channels convert better.
- **Format:** a personalized first line naming a specific recent video, then four demo-able moments, then a 90-second video link. If the first line is not personalized, do not send it.
- **Rules:** none formal. One follow-up after a week, then stop. Never pay for coverage and never ask for script approval.
- **Timing:** week 1 onward, after the video exists.
- **Failure mode:** sending before there is a video to watch. There is nothing to cover without one.

### Newsletters

- **Console.dev** publishes weekly and features 2-3 reviewed tools plus 5-6 beta releases. Its [selection criteria](https://console.dev/selection-criteria) matter here: it only lists **early access, alpha, or beta** releases, explicitly excluding GA or stable ones, and it asks whether an individual developer can self-serve without talking to anyone. swe-mux at 0.1.0 qualifies on the version axis and answers the self-serve question better than most entries, since there is no signup at all. Say "0.1.0" plainly in the submission.
- **TLDR AI** takes tips through the address on tldr.tech. One compressed factual paragraph in their house style.
- **Changelog News** ([submission guidelines](https://changelog.com/news/submit)) requires an account, welcomes your own work, and explicitly rejects how-to guides, tutorials, and "commercial products/services". swe-mux is free and Apache-2.0 so it is not excluded, but the pitch must lead with the engineering story rather than the product - which is what the existing draft already does.
- **Failure mode:** sending the launch post to all three. Each gets its own blurb at its own length.

## Awesome lists and directories

Slow-burn, durable, cheap, and the single best effort-to-traffic ratio in this plan.
Treat it as a channel with a schedule, not an afterthought.

Every list's rules, its exact submission mechanism, and the entry text to submit are in [`posts/awesome-list-prs.md`](posts/awesome-list-prs.md) and [`posts/alternativeto.md`](posts/alternativeto.md), both rewritten 2026-08-28 against the lists as they actually stand.

Two findings from that pass that change the plan:

- **Two of the four lists do not take pull requests at all.** `hesreallyhim/awesome-claude-code` takes a web issue form and warns that submitting any other way risks an interaction restriction; `e2b-dev/awesome-ai-agents` takes a Google Form. The previous draft's "PR body template" was wrong for both.
- **`hesreallyhim/awesome-claude-code` has a hard eligibility floor** of 14 days since first commit on the default branch plus continuing activity, or 100 stars. The public repository's first commit is 2026-08-16, so the 14-day floor clears on 2026-08-30. Submitting before that gets closed automatically.

Ranked by expected value:

1. **[andyrewlee/awesome-agent-orchestrators](https://github.com/andyrewlee/awesome-agent-orchestrators)** - the exact category, curated rather than exhaustive, 191 entries, actively maintained, and swe-mux is not on it. Every named neighbour except Conductor and Warp already is. Highest fit of any list.
2. **[hesreallyhim/awesome-claude-code](https://github.com/hesreallyhim/awesome-claude-code)** - by far the largest audience of people who run the primary harness. Note the maintainer's own stated position: getting on the list is a poor promotional strategy and a good consequence of already having users. Submit after the niche launch, not before.
3. **[awesome-selfhosted/awesome-selfhosted](https://github.com/awesome-selfhosted/awesome-selfhosted)** - enormous, and a legitimate fit. Post-launch only, once a release history exists.
4. **[e2b-dev/awesome-ai-agents](https://github.com/e2b-dev/awesome-ai-agents)** - broadest reach and weakest fit. It lists autonomous agents, and swe-mux is not one. Submit, expect nothing, and do not build any plan around it.
5. **AlternativeTo and selfh.st** - directories rather than lists. Cheap, slow, occasionally the top referrer a year later.

**Deliberately not submitted: the awesome-MCP-server lists.** swe-mux does expose an MCP surface, but it is a per-session, token-gated surface the daemon offers to agents it is already running, not a server anyone installs on its own. Listing it there would be a category error, would be rejected or miscategorized, and would spend credibility on a list that cannot send a single relevant user. Recorded here so the next person does not re-derive it.

## The beta

### What the old one tested, and why that was the wrong question

The previous plan ran one cohort: five to fifteen people, invited directly, asked to spend twenty minutes finding install defects.

That is **usability testing of the install path**, and it is worth doing.
What it cannot answer is whether anybody wants to keep using this, which is the question that decides whether step 6 is worth firing at all.
A trial whose success criterion is "three distinct install-path defects found" is satisfied perfectly by ten people who install it, hit three bugs, and never open it again.

So it splits into two cohorts with two different questions.

### Cohort A: clean-install testers

**5 to 10 people. One session each, twenty minutes, scripted.**

Recruit for platform coverage and for not having seen the project, not for enthusiasm.
The script is fixed so the results are comparable: install, start the daemon, register a Project, reach one running agent session, stop.

Measured:

- **Completion rate.** How many of them reached a running agent session at all.
- **Time to first session.** Wall clock from starting the install to the first agent prompt.
- **Where people abandon.** The specific step, named. This is the output that matters; a completion rate with no abandonment point is a number with nothing to do.

This is the cohort that runs on both install paths, because the unsigned installer and the PyPI route fail at different places and only one of them involves SmartScreen.

### Cohort B: design partners

**5 to 10 people who already run multiple coding agents. Two weeks.**

Not scripted, and explicitly not a bug hunt.
The ask is to use it the way they already work, and specifically to do three things at least once: run multiple sessions at the same time, use the status column to decide where to look, and land at least one worktree branch through the queue.

Those three are named because they are the three the positioning line rests on.
A design partner who never lands a branch has not tested the claim.

Measured:

- **How many used it on three separate days.** The single best proxy available for "this survived contact with an actual workflow", and the one number that gates step 6.
- **Which capability became habitual.** Asked directly, per partner, in their own words. If the answers do not cluster, the positioning is wrong and the line above needs to move again.
- **Whether they came back after the novelty.** One check-in a week after the two weeks end, asking only whether they still have it running and whether they opened it since.

### How any of this is measured without telemetry

**Telemetry is deliberately absent and stays absent.**
That is a design decision the plan does not get to quietly recover, so the measurement is consent-shaped by construction.

Two mechanisms, both of which the participant chooses to send:

- **Structured check-ins.** Cohort A gets one, at the end of the session. Cohort B gets three: day 3, day 10, and one week after the end. Each is a fixed short list of questions so the answers are comparable across people, and each is a message the participant writes and sends, not a payload anything collects. The question sets are in [`OUTREACH_TRACKER.md`](OUTREACH_TRACKER.md).
- **A locally generated adoption summary the participant chooses to share.** Everything cohort B's numbers need is already on the participant's own disk: `swemux doctor --export` carries the diagnostics bundle, and the durable status ledger, the land-queue event trail, and the usage history are all local SQLite. A summary the operator can read is a summary the participant can generate, read, and decide to send, which is exactly the posture the bug form already takes with the diagnostics export. **This is a proposal, not a feature**: no such summary command exists, and § Open decisions records it as a decision rather than as a plan. Until it does, cohort B is measured entirely by the structured check-ins.

Asks, scripts, question sets, and both trackers are in [`OUTREACH_TRACKER.md`](OUTREACH_TRACKER.md).
No real people are named there; the categories are described by role.

## Open decisions

Recorded here rather than acted on, because each one is a change to the product that a copy edit is not allowed to make on its own.

### Should the PTY supervisor default to on? - **Decided: yes. Now a dependency, not a decision.**

**Decided by the operator 2026-08-28.** The default flips to `True`; the copy is unconditional; the code change belongs to a separate agent and this package does not make it.

Kept here rather than deleted because the requirements below did not go away when the decision was taken - they moved from being reasons to hesitate to being **what the flip owes before it lands**, and this package's copy is blocked on them either way.
The risk the original hesitation named is real and unchanged: it is a runtime-architecture change for every install, it interacts with the supervisor-update rules in the root `CLAUDE.md` (updating the supervisor reaps every live session), and clean-machine validation has not happened, so nobody knows what the supervisor does on a machine that is not this one.

**What flipping it would require first, in order:**

1. **Clean-machine testing** (step 2), with the supervisor on, on a machine with no checkout. Its spawn path, its discovery file, and its socket have only ever run here.
2. **Failure testing.** What a user sees when the supervisor cannot start, when it dies while the daemon lives, and when a daemon finds a supervisor from an older build. The daemon already falls back to in-process spawning and logs it; the question is whether a person can tell, not whether the code copes.
3. **A redeploy and update story.** A supervisor in the path means a release that bumps `PROTOCOL_VERSION` ends every live session, and `swemux update --install` already refuses such a release for exactly that reason. Defaulting the supervisor on makes that refusal something every user meets rather than something the operator meets.
4. **A resolution for the `live_daemon` orphan assertion, decided before the flip rather than after CI goes red.**
   `tests/test_live_daemon.py::test_the_muxd_entry_point_starts_serves_and_stops_without_orphans` collects every descendant of the daemon while it is up and then asserts none of them survives shutdown.
   A supervisor is by design a descendant that survives, so with the default on that test fails on Ubuntu and Windows CI - correctly reporting a behaviour change rather than a bug.
   The assertion cannot simply be relaxed: it is the only thing in the suite that proves `muxd` takes its children with it, and a version that tolerates any survivor stops detecting the leak it exists for.
   What it has to become is a check that distinguishes **the** supervisor, identified rather than assumed, from everything else, and still fails on any other survivor - which means the test needs a way to learn the supervisor's pid from the daemon rather than inferring it from what is left alive, because "the process still running is the one that was supposed to be" is the reasoning the test exists to refuse.
   That is a real piece of test design and it is the reason this decision is not a one-line change.

**None of the four is done, and the copy is already written as though the flip has happened.**
That is a deliberate, bounded exposure rather than an oversight, and it is held closed by two things.

The ordering rule: **the copy does not land ahead of the default.**
`site/tools/check.mjs` asserts it - `index.html` stating the claim unconditionally while `config.py` reads `False` is a gate failure, with a message naming both directions of the fix.
So this branch is red until the flip lands, on purpose, and going green is the signal that the two halves agree rather than a thing anyone has to remember to check.

The fallback: if the flip turns out to be unsafe, the strong claim does not ship and the qualified version returns.
That version is not lost - it is in this file's history and its shape is the block in `README.md` § Supervision, which is the one to restore.

### Should onboarding offer a "recommended fleet setup"?

**The finding that raised it:** correcting the control-plane copy costs the drafts real impact, because a fresh install genuinely has almost none of it on.
The gap between "what swe-mux can do" and "what swe-mux does when you install it" is wide, and the copy is now obliged to say so.

**The proposal.** One clearly labelled step in onboarding that turns on a named set of capabilities in a single press, showing exactly what it enables and what each one costs, with everything reachable individually afterwards through the surfaces that already own it.
That is the honest way to close the gap: it makes the configured install easy to reach rather than making the fresh install sound like the configured one.

**Why it is recorded rather than built.** It is a product change with a real design question inside it: which capabilities belong in the recommended set, given that some of them spend money and the enablement graph has dependency edges. It also has to respect the existing rule that a grant may only ever turn something on while exactly one editor may turn it off.
Naming it here is worth more than guessing at it, and no draft may describe it until it exists.

### Should there be a shareable local adoption summary?

**The finding that raised it:** the beta needs numbers and the project has no telemetry, by design.

**The proposal.** A command that reads what is already on the user's own disk and writes a short summary they can read and choose to send: days used, sessions run, branches landed, capabilities enabled.
The precedent is `swemux doctor --export`, which the bug form already asks for on exactly these terms.

**Why it is recorded rather than built.** It is small, but it is a new surface that reports on a person's usage, and the whole posture of this project is that such a thing is something a person hands over rather than something that is collected. Getting that boundary right is the work, not the SQL.
Until it exists, the beta is measured by structured check-ins alone, which is slower and is honest.

## Blog cadence

This project's advantage is that the raw material already exists.
`.docs/development/` and the root `CLAUDE.md` are post-mortems of real bugs written at the time, with the measurements still attached.
Technical posts that teach something transferable are what earn a following for a project like this; a changelog entry earns nothing.

The selection rule: **write the post where the finding is interesting to someone who will never install swe-mux.**
That is what makes a post travel, and it is also what makes it survive a hostile aggregator thread.

### The first three, in order

1. **"How I rebuild and redeploy my agent runtime without killing a single session"** ([`blog/02-session-preserving-runtime.md`](blog/02-session-preserving-runtime.md)), launch week.
   It backs the Show HN claim that leads the post, it is the most visually demonstrable thing here, and the transferable lesson - the thing that restarts often must not own the sessions - applies to anyone building a long-running local runtime. It also carries three Windows findings (job objects inherit; a bound port is not a ready daemon; antivirus holds locks exactly when you want to rename directories) that are useful independently.
2. **"A dependency's declared license does not describe what its wheel ships"** - **not yet drafted**, week 2-3.
   The strongest unwritten post in the repository. PyAV declares BSD-3-Clause and links GPL x264/x265; sherpa-onnx declares Apache-2.0 and statically links espeak-ng. The generalizable finding is that metadata-based license auditing - which is what essentially every tool in the ecosystem does - cannot see either of these, so the check has to have two halves: one reading the resolved closure's metadata, and one reading the built artifact tree by artifact name. It reaches every open-source maintainer and every person who has ever run a dependency-diligence scan, which is a far larger audience than agent tooling. It is fully verifiable from `packaging/license_audit.py` and `THIRD-PARTY-NOTICES.md`. And critically for the sequencing: **it needs no demo assets**, so it can ship while the video work is still in flight.
3. **"Knowing when an AI coding agent is actually done"** ([`blog/04-status-detection.md`](blog/04-status-detection.md)), week 4-5.
   Every reader in this category has this problem and most have solved it badly. The corpus argument - detection logic without a captured regression corpus is astrology - is the part that will get argued about, which is what you want. It is the keystone-problem post.

Why not the land queue first, given it is the most distinctive claim: it is the most product-shaped of the four and reads best once the reader already knows what swe-mux is.
It is post four, not post five.

### The backlog, ranked

4. **"Letting agents land their own branches without trusting them"** ([`blog/03-land-queue.md`](blog/03-land-queue.md)) - safety by construction rather than by model alignment. Strong, and the strongest r/programming candidate after 02.
5. **"Nine CI failures, none of which reproduced on my machine"** - **not drafted.** The first public shared-runner CI run failed nine ways at once and every one was an environment difference: mypy inheriting the host platform so it asked a different question per leg; three separate bugs hiding behind each other because a step that never ran is not a step that passes; Windows timer resolution differing by two orders of magnitude between a host that raises it to 1ms and a bare runner at 15.625ms; and an assertion on `psutil`'s reading of a process's cwd that was simply false on the runner while the code under test was correct. The transferable rule - when CI fails and the local gate passes, the environment is the hypothesis, and evidence beats a patch - is worth the post on its own.
6. **"A broken assertion at module scope deleted half my test suite and the run still exited green"** - **not drafted.** A `*.test.ts` asserting at module scope threw during import, the aggregator stopped importing there, every later suite silently never registered, and the summary still read `# fail 0`. Measured: 2042 tests became 1047, all "passing", and only the exit code told the truth. Short, alarming, universally applicable to anyone with a hand-rolled test aggregator, and it ends with the fix - a test that fails the gate for any file that does not register.
7. **"Running my coding-agent fleet from my phone"** ([`blog/05-phone-fleet.md`](blog/05-phone-fleet.md)) - lighter, clip-heavy, r/selfhosted crosslink.
8. **"Shipping a desktop app in 2026 without running a server"** ([`blog/06-no-server-updates.md`](blog/06-no-server-updates.md)) - post-launch, and it needs the code-signing decision made before it can be honest.

### Rhythm

One post every two weeks, published on `swemux.dev/blog` first and cross-posted to dev.to and Hashnode with the canonical URL pointing back.
Submit at most one to an aggregator per fortnight.
If one lands well, do **not** submit the next one immediately - back-to-back submissions from one account read as farming and get treated accordingly.

## Feedback and issue funnels

Traffic arriving at a repository with no templates and no triage plan is traffic wasted, and it arrives all at once.
Everything here must exist **before step 3**, not before step 6, because the beta cohorts are the first people who need somewhere to put a report.

### The routes

| What | Where | State today |
|---|---|---|
| Bugs | GitHub Issues, `bug_report.yml` form | **No issue templates exist** |
| Feature requests | GitHub Discussions → Ideas, `ideas.yml` form | Form is written and committed; **Discussions is disabled**, so the README's link 404s |
| Questions | GitHub Discussions → Q&A | Category does not exist yet |
| Security | `SECURITY.md` | Exists |
| Anything else | `config.yml` contact links | Does not exist |

### What the bug form must ask for

The project already ships the two things that make a bug report actionable, and neither is discoverable without being asked for:

- `swemux doctor` output - the read-only health report covering daemon, supervisor, frontend build, detected agent CLIs, tailnet listener, and background loops.
- `swemux doctor --export` - the full diagnostics bundle (config, remote, firewall, logs) as JSON.

Both commands verified against `src/swe_mux/cli.py` on 2026-08-28.
Ask for the first as a required field and the second as optional, and say in the form that the export contains paths and configuration so the reporter should read it before pasting.
That framing is what makes attaching it consent-shaped rather than telemetry, which is the whole posture of the project.

Also required: platform and version, install method (`uv tool`, `pipx`, `pip`, source, frozen app), which agent CLI, and what was expected versus what happened.
Keep it to six fields. Every extra required field is a person who closes the tab.

### Feature voting

Discussions → Ideas with a thumbs-up as the vote is already the designed mechanism, the form already exists and already asks for the problem rather than the solution, and `swemux.dev/roadmap/` already has a "deliberately not on the roadmap" section that gives a cheap honest answer to a whole class of request.
The only missing piece is that Discussions is off.
Turning it on is a settings change, not a code change, and it unblocks the README as written.

### Triage plan for one person

- **Launch weeks (steps 3 through 6):** first response within 48 hours on everything. This is the promise that converts a drive-by reporter into a returning one, and it is only affordable for a bounded number of weeks. Budget it as part of the attention day, not as extra.
- **After:** weekly triage pass. Say so in `CONTRIBUTING.md` rather than implying an SLA that will be missed.
- **Labels, minimal:** `bug`, `install`, `platform:windows`, `platform:linux`, `platform:macos`, `harness:<name>`, `needs-info`, `wontfix-by-design`. The last one exists so a boundary can be closed with a link to the roadmap section rather than a re-argued paragraph.
- **The rule worth keeping:** close with a reason and a link, never with silence. A silently closed issue costs more reputation than an unanswered one.

## Cadence calendar

The constraint that should shape everything: **one person, and presence does not scale.**

Where AI assistance changes the math: drafting copy, adapting one post's angle for another audience, writing the blog posts from existing design docs, preparing comment answers in advance, keeping the tracker current.
All of that is close to free now, which is why this plan can afford twelve venues and eight blog posts.

Where it changes nothing: sitting in an HN thread for six hours answering strangers, being in a Discord thread while someone's install fails, doing a call with a YouTube channel, reading a diagnostic bundle at 11pm.
That presence is most of what makes a launch work, it cannot be delegated, and it is the actual budget.

**The rule that follows: at most one attention-day per week.**
Never fire two attention-hungry beats in the same week.
Show HN day is a working day gone; plan nothing else for it.

### Weekly rhythm, sustainable indefinitely

| Slot | Work | Delegable to AI |
|---|---|---|
| Mon, 30 min | Triage pass: new issues, Discussions, mentions | Drafting replies, yes. Deciding, no |
| Wed, 2 hours | Blog writing, alternating weeks | Mostly yes, from existing design docs |
| Fri, 30 min | One clip post to X, one directory or awesome-list submission | Yes |
| One weekday, when a beat is scheduled | The attention day | No |

### Monthly rhythm

- One engineering post published and, if it is strong, submitted to exactly one aggregator.
- One awesome-list or directory submission worked to completion.
- One metrics read against the thresholds below, and a written decision: continue, change approach, or stop buying reach.
- Roadmap page refreshed against the most-voted open ideas, since the README promises that the roadmap is drawn from them.

### The first nine weeks, concretely

One beat per week at most, and the beta is two weeks because it is two weeks.

| Week | Step | Beat | Attention cost |
|---|---|---|---|
| 0 | 1 | Repository hygiene: description, homepage, topics, Discussions, issue templates. Hero video capture begins | Low, but it is real work |
| 1 | 2 | Clean-machine testing, both install paths. Fix what it finds | Medium |
| 2 | 3 | Beta opens: cohort A's scripted sessions run, cohort B's two weeks start | Medium, spread |
| 3 | 3 | Cohort B day-3 check-ins. Fix cohort A's abandonment points while B is still running | Medium |
| 4 | 3 | Cohort B day-10 check-ins, then the two weeks close | Low |
| 5 | 4 | **One niche launch**, one venue, and be in the thread | One half-day |
| 6 | 5 | Fix what it found. No posting at all | Low |
| 7 | 6 | **Show HN + X thread + Bluesky + LinkedIn, one morning** - only if activation cleared | One full day |
| 8 | 7 | Blog 02 published, submitted to r/programming. Awesome-agent-orchestrators PR | Half a day |
| 9 | 7 | Cohort B's one-week-after check-in reads. Blog: the licensing post. Awesome-claude-code issue form | Half a day |

Week 6 posting nothing is deliberate and is the part most likely to get skipped.
It is the week the niche launch's findings get fixed, and skipping it means arriving at Show HN with known defects.

Product Hunt appears nowhere in this table on purpose.
It is step 8, it is conditional, and the condition is evidence that does not exist yet.

## Metrics

### Named as vanity, and watched anyway because they are free

GitHub stars, HN points, Product Hunt upvotes, X impressions, star-per-day spikes.
None of them indicates that a single person is running the software.
Stars in particular: this category has neighbours at 33k stars, and a star count read as market position will produce exactly the wrong decision.

### "Ten issues after Show HN" is not a success criterion, and has been removed

It was one, and it was measuring the wrong thing in a way that would have produced a confidently wrong decision.

**Issues select for failure.**
A tool that ten people install and abandon after hitting a bug each generates ten issues.
A tool that ten people install and quietly use every day generates none.
Those are opposite outcomes and the metric scores the first one higher, which is exactly backwards for a project whose actual risk is that people try it once and stop.

Issue count stays on the watch list as a **proxy for reach and for install-path roughness**, which is what it honestly measures.
It is not a success criterion for any step.

### What actually measures adoption, and the honest limit

swe-mux has **no telemetry by design**, so almost every conventional adoption metric is unavailable by construction.
That is a deliberate trade and the plan should not quietly try to recover it.

The four that matter, in order:

- **Activated installs.** Someone reached a running agent session. Measurable in the beta cohorts by their check-ins, and not measurable in the wild at all, which is the honest limit and the reason the beta exists.
- **Repeat users.** Someone used it on three or more separate days. The single most informative number in this plan and the one that gates step 6.
- **Successful landings.** Someone other than the maintainer put a branch through the land queue. It is the deepest point in the product a person can reach, so one of these is worth a great deal more than a hundred stars.
- **People returning with a second report or a contribution.** Not the first report, the second. A first report is a stranger; a second is a user.

What remains measurable without asking anyone:

- **PyPI download counts** (pypistats). Noisy - mirrors and CI inflate it - so read the trend and the shape, not the number.
- **GitHub release asset downloads.** Much cleaner than PyPI, because nothing automated pulls an installer. Available from v0.1.2 onward, which is the first release carrying desktop artifacts.
- **GitHub traffic** (clones and unique visitors, 14-day rolling window). Sample it weekly or it is lost.
- **Discussions ideas with more than one voter.** Proves at least two people wanted the same thing.
- **Unsolicited mentions** anywhere.

### Named as vanity, and watched anyway because they are free

GitHub stars, HN points, Product Hunt upvotes, X impressions, star-per-day spikes, and raw issue count.
None of them indicates that a single person is running the software.
Stars in particular: this category has neighbours at 33k stars, and a star count read as market position will produce exactly the wrong decision.

### Thresholds

| Step | Success | Signal to change approach |
|---|---|---|
| 3, cohort A | 7 of 10 reach a running session, and every abandonment point is named | Fewer than half complete - the install is worse than believed, and nothing downstream is worth doing until it is fixed |
| 3, cohort B | Half or more used it on three separate days, and at least two landed a branch | Everyone installs and nobody returns in week two - the problem is the product's value, not its onboarding, and the positioning line is what to revisit |
| 4 | Installs reported by uninvited people, and at least one returns a second time | Attention with no installs - the copy is describing something people do not want to try, which is a positioning finding rather than a product one |
| 6 | Front page at any point; **activated installs and repeat users** in the following weeks | Front page and no repeat use - the front page was spent, and the thing to fix is the first week of use rather than the next channel |
| Week 9 | 3+ people who have come back a second time; 1+ successful landing by a non-maintainer | No recurring non-maintainer participant - **stop buying reach and fix the first run** |
| Month 6 | A contributor who is not the maintainer; a request the roadmap adopts | Nothing but stars - the project has an audience and no users, which is a worse position than no audience |

The week-9 row is the one to take seriously.
Reach is the cheap half of this plan and activation is the expensive half, and it is very easy to keep spending on the cheap half because it produces visible numbers.

## The single biggest risk

**Spending the one-shot beat before the product can absorb it.**

The asset half of this risk has largely closed since the plan was written: the site carries real screenshots, v0.1.2 publishes an installer and a portable archive, and three install commands have been run into throwaway environments.
What is left is smaller and harder: no video, an unsigned installer, no clean-machine validation, Discussions still off, and no repository description.

But the risk that replaced it is the more serious one, and it is the reason activation is now a precondition rather than a hoped-for outcome.
**Show HN cannot be retried**, the front page is the only distribution moment this project gets for free, and a front page spent on a tool that people install once and abandon does not merely waste the moment - it produces a specific, durable, wrong conclusion in public about a product whose actual problem was its first week of use.

The temptation the plan exists to resist is that the copy is written, the repository is public, and the artifacts now exist, so the beat feels available.
It is available.
It is worth what the beta says it is worth, and the beta has not run.
