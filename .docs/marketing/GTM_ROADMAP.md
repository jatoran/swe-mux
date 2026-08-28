# Go-to-market roadmap

The plan for taking swe-mux from published-but-unannounced to known, written for one maintainer.

Nothing has been announced anywhere as of 2026-08-28.
The repository is public, `swe-mux` 0.1.0 is on PyPI, `swemux.dev` is live, CI runs on three hosts, and an X account exists at <https://x.com/swemux>.
No post, submission, or email has gone out.
That is a good position: every mistake below is still cheap, and the first impression has not been spent.

This document is the strategy and the sequence.
Its supports are [`LAUNCH_CHECKLIST.md`](LAUNCH_CHECKLIST.md) (what must be true before each beat fires), [`OUTREACH_TRACKER.md`](OUTREACH_TRACKER.md) (the tester ask and its tracker), and the per-venue copy in [`posts/`](posts/) and [`blog/`](blog/).

## Positioning

### The line

The tagline is fixed and used verbatim everywhere, unchanged from `README.md` and `site/index.html`:

> **swe-mux - mission control for your coding-agent fleet.**

Do not restate it in other words in any draft.
One string, everywhere, or the project reads as several projects.

### One sentence

swe-mux is a self-hosted control plane for the coding-agent CLIs you already run: a supervisor process owns their terminals so sessions outlive the daemon and the app, one status vocabulary tells you which agent actually needs a human, and finished worktree branches land behind a verification gate you approved - from a browser tab or from your phone.

### One paragraph

If you run one coding agent, the vendor's CLI is enough and swe-mux is overhead.
If you run five across three repositories, you have quietly taken a second job: keeping terminals alive, polling each pane to find out which agent is stuck versus thinking, retyping prompts into a session that was not ready for them, and hand-merging branches that all finish within the same hour.
swe-mux is that second job, made into software.
It does not replace the agents and does not proxy them - your CLIs keep talking to their own vendors under your own subscription, in real pseudoterminals, with their own transcripts untouched.
It replaces the pile of terminal windows around them, and it runs entirely on your machine: SQLite on your disk, no account, no telemetry, and no server the project operates anywhere.

### Who it is for

- Someone already running two or more agent CLIs concurrently, most days, and feeling the coordination cost rather than the capability cost.
- Someone who wants that fleet reachable away from the desk without handing terminal access to a third party.
- Someone on Windows, which this category has historically treated as an afterthought.

### Who it is not for

- Someone running a single agent in a single terminal. Say this out loud in the launch copy. It is the fastest way to stop the "this is over-engineered" comment, because for that reader it genuinely is.
- Teams wanting shared/hosted orchestration. There is no multi-user model and no server.
- Anyone who needs a signed installer today.

### The comparison, stated so it survives the comment thread

The rule: name the neighbours, credit what they do better, and claim only differences that are checkable from the repository.
A comparison that overclaims gets dismantled publicly, and the dismantling is what people remember.

| Tool | What it is | Where it is genuinely ahead | What swe-mux does that it does not |
|---|---|---|---|
| **tmux** | The general-purpose terminal multiplexer that keeps shells alive | Universality, decades of hardening, zero-cost ubiquity, remote-over-SSH by default | Nothing about tmux understands what a coding agent is doing. swe-mux is not a better multiplexer; it is a multiplexer that knows the difference between an agent working, an agent waiting on you, and an agent stuck |
| **herdr** ([herdrdev/herdr](https://github.com/herdrdev/herdr), Apache-2.0, ~33.2k stars, checked 2026-08-28) | "The runtime your coding agents live on" - one Rust binary, background runtime owning agent terminals, working/blocked/idle pane marks, agents drive it over a CLI and socket API | The closest neighbour and ahead on most axes: three-platform support, a single static binary, reattach from any terminal or SSH, an enormous head start in adoption, and a far smaller install | The land queue (herdr does not merge anything), commit-level provenance, a browser and phone client rather than a terminal one, and voice. Windows is beta there and is the proving platform here |
| **Orca** ([stablyai/orca](https://github.com/stablyai/orca), MIT) | Agentic development environment, desktop on three platforms plus native iOS and Android apps and a headless `orca serve` | Real native mobile apps rather than a PWA, three-platform desktop, GitHub and Linear integration, remote SSH worktrees | The land queue and the approved-bytes gate, the session-preserving redeploy, provenance, and a local-only posture with no relay of any kind |
| **Conductor** (<https://conductor.build>) | "Run parallel Claude Code, Codex, and Cursor agents in isolated workspaces" - Mac, closed source | A focused, polished single-purpose product with a review workflow, and it is not trying to be nine things | Open source, not Mac-only, and everything the workspace layer adds beyond parallel-worktrees-with-review |
| **Warp** (<https://www.warp.dev>) | Now an agent platform: an open-source terminal plus Warp Factories, cloud fleets of agents defined as code, with evals and benchmarking | Funding, a real cloud product, evaluation infrastructure, and a terminal that is excellent on its own | Different business entirely. Warp sells the cloud that runs the fleet; swe-mux has no server, and the fleet is on hardware you own |
| **claude-squad**, **cmux**, **vibe-kanban**, **agent-manager** and the rest of [awesome-agent-orchestrators](https://github.com/andyrewlee/awesome-agent-orchestrators) (191 entries, checked 2026-08-28) | Mostly narrower: a TUI, a Kanban board, a worktree spawner | Simplicity. Each is one afternoon to understand, and several are one binary | swe-mux is not simpler and should never claim to be. It is what you reach for after one of those stops scaling |

The four differences to lead with, because each is checkable in the repository rather than a matter of taste:

1. **The session-preserving split.** A separate supervisor owns the pseudoterminals, so daemon restarts, app rebuilds, and full frozen-app redeploys leave every session running with its scrollback. New builds of swe-mux ship from an agent session running inside swe-mux.
2. **The land queue.** Reconcile, run the verification command whose exact bytes a human approved, fast-forward-only onto trunk, one branch at a time. Fast-forward-only is what makes it safe for a machine: Git refuses it on divergence and refuses to overwrite local changes, so the trunk step cannot lose work by construction. An agent cannot approve the gate its own land runs.
3. **Commit-level provenance.** Which session and conversation produced a commit, split into committer and contributor, from deterministic capture rather than from the agent's account of its own work.
4. **The phone is a client, not a status page.** An installable PWA over your own tailnet, no relay and no swe-mux login, with terminals, git review, approvals, and local speech-to-text.

The one to under-claim rather than over-claim: **the phone and voice path is genuinely differentiating, and Orca ships native mobile apps.**
Say "installable PWA over your own tailnet with no relay", not "the only one with mobile".

### The honest weaknesses, stated before someone else states them

Every one of these will surface in a comment thread.
Conceding them in the post costs nothing and buys the thread.

- **Windows-first is unusual here and costs adoption.** Windows is the only platform proven end to end; CI install-smokes the wheel on all three hosts but no CI job on any host starts a daemon, and macOS is still `continue-on-error`.
- **It is a lot of software.** The surface is large and the tutorial covers a small slice of it. "Not magic, it is a lot of software" is already in the launch draft; keep it.
- **No signed desktop installer yet**, so an unsigned frozen executable means SmartScreen friction for anyone who is not installing from PyPI.
- **One maintainer.** Say the support expectation out loud rather than implying an SLA.
- **`pip install swe-mux[voice-local]` does not install at all.** Measured 2026-08-28 ([`../development/DEPENDENCY_AUDIT_2026-08-28.md`](../development/DEPENDENCY_AUDIT_2026-08-28.md) § 4): the published wheel declares `en-core-web-sm`, which is on no index, so both pip and uv refuse the extra outright. Concede this as a packaging bug rather than as the `av` caveat it was previously written up as - the `av>=11` residue is real in principle but currently unreachable, because nothing resolves at all. Fixing the extra is what activates it, so both are owed before this bullet can be softened. Do not let a diligence scan surface either one first.

## Launch sequencing

Six stages.
Each states the precondition that must be true before it fires and the signal that says it worked.
The staging exists because the two irreversible beats (Show HN, Product Hunt) are one-shot, and a first impression against placeholder screenshots and no binary converts badly and cannot be retried.

### Stage 0 - Make the repository survivable

**Precondition:** none. This is the only stage with no dependency, and it is the cheapest work in the whole plan.

The repository is public and currently answers a visitor badly.
Verified against the GitHub API on 2026-08-28: no description, no topics, no homepage, Discussions **disabled**, no issue templates, no code of conduct, community profile at 57%.
`README.md` already links to `discussions/categories/ideas` as the route for feature requests, and that link resolves to nothing today.

Full list and owners: [`LAUNCH_CHECKLIST.md`](LAUNCH_CHECKLIST.md) § Stage 0.

**Success signal:** a stranger landing on the repository can tell what it is in five seconds, and every route the README promises resolves.

### Stage 1 - Assets and the clean-machine trial

**Precondition:** Stage 0 done.

Three things block every public beat and none of them are marketing work:

- **Real screenshots.** `site/` ships deliberate placeholder images because real captures leaked project names. The fix is the PII-free capture environment already planned - a second daemon with its own data dir and port, plus synthetic projects.
- **A hero video and a small set of feature GIFs.** There is no video or GIF anywhere yet. Every venue below assumes one; several are worth skipping entirely without one.
- **A clean-machine trial.** Not done. Install from the published wheel onto a machine with no checkout, and record every place it falls over.

A signed desktop installer is a fourth item, and it is the only one that may be deferred: a PyPI-only launch is defensible if the copy says so plainly.
A launch with placeholder screenshots is not defensible at all.

**Success signal:** someone who is not the maintainer installs from PyPI on their own machine and reaches a running session without being told anything that is not in the README.

### Stage 2 - The quiet trial (5 to 15 people, invited directly)

**Precondition:** Stage 1. The install works on a machine that is not the development host.

Direct outreach only.
No public post.
The ask is explicitly for bug reports and honest reactions, not adoption, because that framing roughly doubles the response rate and completely changes what comes back.
Template and categories: [`OUTREACH_TRACKER.md`](OUTREACH_TRACKER.md).

This stage exists to spend the embarrassing failures on people who will not screenshot them.

**Success signal:** at least five people install it, and at least three distinct install-path or first-run defects are found and fixed.
If nobody hits anything, the trial was too small or too friendly - widen it before believing the number.

### Stage 3 - Niche soft launch

**Precondition:** Stage 2 defects fixed, and each defect either fixed or written down in the README as a known limit.

Venues, one at a time, two or three days apart: r/ClaudeAI, r/ChatGPTCoding, r/LocalLLaMA, and the Claude Developers Discord.
Never the same text twice; drafts in [`posts/reddit-soft-launch.md`](posts/reddit-soft-launch.md) and [`posts/discord-claude-developers.md`](posts/discord-claude-developers.md) are already differentiated by audience.

These communities forgive rough onboarding, are full of people who already run the CLIs, and will find the clean-machine failures the trial missed.

**Success signal:** unsolicited bug reports from people you did not invite.
That is the signal, not upvotes.
Zero bug reports after a post that got attention means the post reached readers but not installers - the copy is describing something people do not want to try, and that is a positioning finding, not a product one.

### Stage 4 - The main beat: Show HN, with the X thread the same morning

**Precondition:** Stages 1 through 3 complete, a week of soft-launch fixes landed, real screenshots on the site, the hero video live, and a whole working day free.

One shot.
Tuesday to Thursday, roughly 8-10am ET.
Submit the repository (Show HN convention for open source; the site is in the README's first line), post the prepared text as the first comment immediately, and then be in the thread all day.

The X launch thread goes out the same morning so the two reinforce rather than compete.
Bluesky and LinkedIn the same day, single posts, no thread.

**Success signal:** front page at any point during the day, and 10 or more issues opened in the following week.
Points are vanity; the front page is binary and the issues are the thing that persists.

### Stage 5 - Product Hunt

**Precondition:** Show HN has happened and its thread has been mined for what actually resonated. At least a week later, so the two beats do not share an attention day.

Rewrite the listing around whatever the HN thread proved, not around what the draft guessed.

**Success signal:** comments and maker replies rather than upvote count.
Product Hunt's own guidance is explicit that you may not ask for upvotes, and the ranking weights engagement over raw votes.

### Stage 6 - The slow burn, indefinitely

**Precondition:** none of the above blocks it, and none of it is time-critical.

Engineering posts on a roughly fortnightly rhythm, awesome-list and directory submissions, newsletter submissions, YouTube outreach.
This is where durable traffic comes from, and it is the part a solo maintainer can actually sustain.

## Venues, ranked by expected value for this specific project

Ranked on: does the audience already run coding-agent CLIs, does the format suit a large local tool with no hosted demo, and what does one hour spent there return.

| Rank | Venue | Why here | Cost |
|---|---|---|---|
| 1 | **Show HN** | Highest ceiling, exactly the audience, and the engineering substance rewards a thread that goes deep | One full day of presence, one shot |
| 2 | **r/ClaudeAI + Claude Developers Discord** | Highest install-conversion per reader. These people have Claude Code open right now | Low, and repeatable across the sibling subs |
| 3 | **Awesome-list entries** | Durable, compounding, cheap, and `awesome-agent-orchestrators` is precisely this category with 191 entries and no swe-mux in it | An hour each, mostly waiting |
| 4 | **Engineering blog posts, submitted to HN and lobste.rs** | The best-quality inbound this project can generate, and the raw material already exists as post-mortems | Half a day each to write |
| 5 | **r/selfhosted** | Large, and the local-only/zero-server/Tailscale story is native to it rather than a stretch | Low |
| 6 | **X (<https://x.com/swemux>)** | Where the multi-agent-orchestration conversation lives; short clips of specific moments outperform announcements | Ongoing, low per post, needs clips to exist |
| 7 | **Newsletters (Console.dev, TLDR AI, Changelog News)** | One submission, potentially thousands of the right readers, no ongoing cost | An hour total |
| 8 | **r/commandline, r/opensource, r/codex, r/vibecoding** | Each is a real but narrower slice; the licensing angle is unusually strong in r/opensource | Low each |
| 9 | **lobste.rs** | Small but unusually high-quality readership; needs an invite and rewards engineering content only | Blocked on sourcing an invite |
| 10 | **Product Hunt** | Real traffic, poor fit for a self-hosted developer tool with no hosted demo, and the audience skews away from people who will install a Python daemon | A day |
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
- **Timing:** 12:01am PT, at least a week after Show HN.
- **Failure mode:** asking for upvotes in a group chat and getting the launch penalized. The second failure mode is launching it before Show HN and burning the assets on the weaker venue.

### Developer Discords and Slacks

- **Audience:** the Claude Developers Discord is the single highest same-day-install audience available.
- **Format:** one message in the showcase/community-projects channel, one GIF, then stay in the thread.
- **Rules:** confirm the current channel and its self-promotion policy before posting. Most such servers permit one showcase post and treat a second as spam.
- **Timing:** with the Reddit soft launch, not with Show HN.
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
2. **[hesreallyhim/awesome-claude-code](https://github.com/hesreallyhim/awesome-claude-code)** - by far the largest audience of people who run the primary harness. Note the maintainer's own stated position: getting on the list is a poor promotional strategy and a good consequence of already having users. Submit after the soft launch, not before.
3. **[awesome-selfhosted/awesome-selfhosted](https://github.com/awesome-selfhosted/awesome-selfhosted)** - enormous, and a legitimate fit. Post-launch only, once a release history exists.
4. **[e2b-dev/awesome-ai-agents](https://github.com/e2b-dev/awesome-ai-agents)** - broadest reach and weakest fit. It lists autonomous agents, and swe-mux is not one. Submit, expect nothing, and do not build any plan around it.
5. **AlternativeTo and selfh.st** - directories rather than lists. Cheap, slow, occasionally the top referrer a year later.

**Deliberately not submitted: the awesome-MCP-server lists.** swe-mux does expose an MCP surface, but it is a per-session, token-gated surface the daemon offers to agents it is already running, not a server anyone installs on its own. Listing it there would be a category error, would be rejected or miscategorized, and would spend credibility on a list that cannot send a single relevant user. Recorded here so the next person does not re-derive it.

## Direct outreach for testers

The goal at this stage is **bug reports and honest reactions, not adoption**, and the ask must say so.
"Would you try my tool" gets a polite yes and no install.
"I need someone to install this on a clean machine and tell me where it falls over, I expect it to fall over" gets an install, because it offers the recipient a defined and finite job with an obvious end.

Categories, the specific ask for each, and the templates are in [`OUTREACH_TRACKER.md`](OUTREACH_TRACKER.md), which also holds the tracker table.
No real people are named there; the categories are described by role.

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
Everything here must exist **before Stage 3**, not before Stage 4, because the soft launch is the first traffic.

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

- `mux doctor` output - the read-only health report covering daemon, supervisor, frontend build, detected agent CLIs, tailnet listener, and background loops.
- `mux doctor --export` - the full diagnostics bundle (config, remote, firewall, logs) as JSON.

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

- **Launch weeks (Stages 3-5):** first response within 48 hours on everything. This is the promise that converts a drive-by reporter into a returning one, and it is only affordable for a bounded number of weeks. Budget it as part of the attention day, not as extra.
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

### The first eight weeks, concretely

| Week | Beat | Attention cost |
|---|---|---|
| 0 | Stage 0 repository hygiene, Stage 1 assets begin | Low, but it is real work |
| 1 | Clean-machine trial, hero video capture | Low |
| 2 | Stage 2 quiet trial: send the outreach, then fix what comes back | Medium, spread |
| 3 | Soft launch: r/ClaudeAI, then Discord, then r/ChatGPTCoding, then r/LocalLLaMA, spaced | One half-day per post |
| 4 | Fix what the soft launch found. No posting at all | Low |
| 5 | **Show HN + X thread + Bluesky + LinkedIn, one morning** | One full day |
| 6 | Blog 02 published, submitted to r/programming. Awesome-agent-orchestrators PR | Half a day |
| 7 | Product Hunt. Newsletter submissions | One day |
| 8 | Blog: the licensing post. Awesome-claude-code issue form | Half a day |

Week 4 posting nothing is deliberate and is the part most likely to get skipped.
It is the week the soft-launch findings get fixed, and skipping it means arriving at Show HN with known defects.

## Metrics

### Named as vanity, and watched anyway because they are free

GitHub stars, HN points, Product Hunt upvotes, X impressions, star-per-day spikes.
None of them indicates that a single person is running the software.
Stars in particular: this category has neighbours at 33k stars, and a star count read as market position will produce exactly the wrong decision.

### What actually measures adoption, and the honest limit

swe-mux has **no telemetry by design**, so almost every conventional adoption metric is unavailable by construction.
That is a deliberate trade and the plan should not quietly try to recover it.
What remains measurable:

- **PyPI download counts** (pypistats). Noisy - mirrors and CI inflate it - so read the trend and the shape, not the number.
- **GitHub release asset downloads**, once a desktop artifact exists. Much cleaner than PyPI, because nothing automated pulls it.
- **GitHub traffic** (clones and unique visitors, 14-day rolling window). Sample it weekly or it is lost.
- **Issues opened by someone who is not the maintainer.** The single best proxy for real use in this whole list.
- **Discussions ideas with more than one voter.** Proves at least two people wanted the same thing.
- **Unsolicited mentions** anywhere.

### Thresholds

| Stage | Success | Signal to change approach |
|---|---|---|
| Quiet trial | 5+ install, 3+ distinct defects found | Fewer than 3 install - the ask is wrong, or the install is worse than believed |
| Soft launch | Unsolicited bug reports from uninvited people; 5+ installs reported | Attention but zero bug reports - the copy is describing something people do not want to try |
| Show HN | Front page at any point; 10+ issues in the following week | Front page but no issues - the site or the install is where people stop, not the pitch |
| Week 8 | 1+ issue per week from a non-maintainer; 3+ people who have reported twice | No recurring non-maintainer participant - the problem is onboarding, not reach. **Stop buying reach and fix the first run** |
| Month 6 | A contributor who is not the maintainer; a request the roadmap adopts | Nothing but stars - the project has an audience and no users, which is a worse position than no audience |

The week-8 row is the one to take seriously.
Reach is the cheap half of this plan and onboarding is the expensive half, and it is very easy to keep spending on the cheap half because it produces visible numbers.

## The single biggest risk

**Spending the one-shot beat before the artifact can absorb it.**

Show HN cannot be retried, the front page is the only distribution moment this project gets for free, and today it would send a few thousand developers to a site with placeholder screenshots, no video, no installer, an unverified install path, a README linking to a Discussions page that does not exist, and a repository with no description.
Every one of those is fixable in under two weeks.
None of them is fixable after the traffic has come and gone.

The temptation the plan exists to resist is that the copy is already written and the repository is already public, so the beat feels available now.
It is available now.
It is worth roughly a tenth of what it will be worth in three weeks.
