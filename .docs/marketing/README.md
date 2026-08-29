# Marketing

The plan, and the drafts it sequences.

Start at [`GTM_ROADMAP.md`](GTM_ROADMAP.md): positioning, the eight-step launch sequence with a precondition and a success signal for each, the venues ranked for this project, the blog order, the beta design, the calendar, and the metrics.
Its supports are [`LAUNCH_CHECKLIST.md`](LAUNCH_CHECKLIST.md) (what must be true before each beat fires, with the repository state that was actually measured) and [`OUTREACH_TRACKER.md`](OUTREACH_TRACKER.md) (the two beta cohorts, their asks, and the trackers).

`posts/` and `blog/` are the copy bank: one draft per venue.
These are working drafts in the operator's voice: blunt, concrete, no corporate filler, no hype vocabulary.
Numbers marked `[verify]` must be re-measured against the release build before anything ships.
Every `[gif]` / `[video]` marker maps to an asset from the Phase 11 demo-environment work, and none of those assets exists yet.
Nine real screenshots do exist and are on the site; the video and GIF work is what is outstanding.

**This directory is public.** Everything here, including commit messages, is readable by a competitor and by a prospective user. Write it for both.

## Official accounts

| Channel | Address | State |
|---|---|---|
| Repository | <https://github.com/jatoran/swe-mux> | Public, Apache-2.0 |
| Site | <https://swemux.dev> | Live, served from `site/` |
| PyPI | <https://pypi.org/project/swe-mux/> | 0.1.2 |
| GitHub Releases | <https://github.com/jatoran/swe-mux/releases> | v0.1.2 carries the wheel, the sdist, an unsigned Windows installer, and a portable archive |
| X | <https://x.com/swemux> | Exists, nothing posted. **The site footer does link it** as of 2026-08-27 |

Nothing has been announced anywhere as of 2026-08-28.

## Positioning

### The line

Primary, used verbatim everywhere the project explains itself:

> **For developers running multiple coding agents locally, swe-mux shows what each agent actually did and lands finished branches behind checks you approved.**

One string, everywhere, or the project reads as several projects.
Do not restate it in other words in any draft.

### The five proof points, in this order

Each is checkable from the repository, which is the only reason any of them is here.

1. **One normalized view across agent CLIs.** One status vocabulary, one transcript reader, one history search, one account switcher, over Claude Code, Codex, opencode and the rest of the registry.
2. **Deterministic evidence rather than agent self-report.** Tier 0 facts hashed on the exact bytes written, commit provenance split into committer and contributor, model-free detectors, and a durable status ledger you can read hours later.
3. **Safe serialized landing.** Reconcile, run the verification command whose exact bytes a human approved, fast-forward only, one branch at a time. An agent cannot approve the gate its own land runs.
4. **Full phone access over your own tailnet.** An installable PWA with no relay and no swe-mux login: terminals, git review, approvals, and local speech-to-text.
5. **Durable terminals when supervision is enabled.** A separate supervisor process can own the pseudoterminals so sessions outlive a daemon restart and a full app redeploy. It ships **off**; see the honest phrasing below.

### The tagline, which is not the positioning line

> swe-mux - mission control for your coding-agent fleet.

This is a **brand tagline only**: a repository description, a listing name, an X bio.
It is never the explanatory claim, and it never stands in for the positioning line above.
The reason is worth recording, because it is the reason the positioning moved at all: "fleet", "control plane", and "mission control" are generic in this category now, and the named neighbours already use them.

### Why the positioning points where it does

Persistence, worktrees, mobile, Windows, and arbitrary-CLI support are **commodity** in this space as of 2026-08-28.
Orca advertises Windows, mobile, parallel worktrees, persistent scrollback, account switching, and arbitrary CLI agents.
Herdr owns much of the persistent-runtime story and has an enormous head start on adoption.
Leading with any of those is leading with a row the reader can already tick for two other tools.

What is hard to copy here is **evidence, provenance, controlled interruption, and approved landing**.
Those are the four the positioning points at, and they are the four the drafts lead with.

## Inventory

| File | Venue | When |
|---|---|---|
| `GTM_ROADMAP.md` | The plan | Read first |
| `LAUNCH_CHECKLIST.md` | Preconditions per stage | Before each beat |
| `OUTREACH_TRACKER.md` | The two beta cohorts | Steps 2-3, before any public post |
| `blog/01-launch.md` | swemux.dev/blog + dev.to + Hashnode | Launch day |
| `blog/02-session-preserving-runtime.md` | Blog; submit to HN + lobste.rs + r/programming | Week 1-2 |
| `blog/03-land-queue.md` | Blog; submit to HN + r/programming | Week 2-3 |
| `blog/04-status-detection.md` | Blog; submit to HN | Week 3-4 |
| `blog/05-phone-fleet.md` | Blog; r/selfhosted crosslink | Week 4+ |
| `blog/06-no-server-updates.md` | Blog | Post-launch |
| `posts/show-hn.md` | Hacker News | After the design-partner beta demonstrates activation |
| `posts/product-hunt.md` | Product Hunt | **Conditional.** Only if earlier evidence shows interest beyond the terminal-tool audience |
| `posts/reddit-soft-launch.md` | r/ClaudeAI, r/ChatGPTCoding, r/LocalLLaMA | The one niche launch, and its siblings later |
| `posts/reddit-tier2.md` | r/programming, r/commandline, r/selfhosted, r/opensource, r/vibecoding, r/codex | Staggered |
| `posts/discord-claude-developers.md` | Claude Developers Discord | The alternative venue for the one niche launch |
| `posts/lobsters.md` | lobste.rs (invite required) | With blog 02 |
| `posts/x-thread.md` | X launch thread + clip cadence | Show HN day |
| `posts/bluesky-linkedin.md` | Bluesky, LinkedIn | Show HN day |
| `posts/awesome-list-prs.md` | The four awesome lists (two take a PR, two do not) | Post-launch |
| `posts/newsletter-submissions.md` | Console.dev, TLDR AI, Changelog News | Post-launch |
| `posts/youtube-outreach.md` | AI-tooling channels | Post-launch, after the video exists |
| `posts/alternativeto.md` | AlternativeTo, selfh.st | Post-launch |

## Rules

- One positioning line, verbatim, everywhere. The tagline is a tagline and never substitutes for it.
- **Every claim must be true of the shipped artifact, in its default configuration, on the day it posts.**
  This is the rule the 2026-08-28 claim audit was run against, and the one that moved the most copy.
  A capability that ships behind a switch is described with the switch, or it is not described.
- Engineering posts get submitted to aggregators; the announcement does not (except Show HN, which is the one sanctioned announcement).
- Never post the same text to two subreddits; each draft here is already differentiated by audience.
- Platform claims are read out of `.github/workflows/ci.yml`, not out of a prose summary. CI install-smokes the wheel on all three hosts, and the `live_daemon` tier starts a daemon from the source checkout on Linux and Windows, but no CI job starts a daemon from a published artifact on any host, so no draft may say a platform is verified working end to end from what a user installs.
- Nothing here names a real person, carries the operator's identity or personal paths, or reproduces a screenshot containing either.
- Rules for a venue are recorded with the source that was read and the date it was read. A rule nobody checked is written down as unchecked rather than as a rule.

## The three sentences every draft shares

These are the paragraphs the claim audit rewrote.
Copy them rather than paraphrasing them, and shorten by deleting clauses rather than by softening qualifiers.

### Supervision

> Terminals can be owned by a supervisor process separate from the daemon and the UI, so a daemon restart or a full app redeploy leaves the agents working with their scrollback intact.
> That mode ships **off**: `pty_supervisor_enabled` defaults to `false`, and turning it on is an edit to `config.toml` in the data directory plus a daemon restart.
> With it off, the daemon owns the pseudoterminals and a restart ends them; cold session recovery, which is on by default, brings those sessions back as readable, resumable rows carrying their last scrollback rather than losing them silently.

Never write "sessions never die", "sessions survive everything", or any sentence that presents survival as what happens out of the box.

### Where it runs, and what crosses the network

> swe-mux runs on your own machine and the project operates no backend and no relay: your data is SQLite on your disk, there is no swe-mux account, and nothing reports usage anywhere.
> It is not a tool with no network in it.
> The agent CLIs talk to their own vendors under your own subscription, and four optional capabilities reach out when you turn them on: model calls through an OpenRouter-compatible endpoint with your key, web push through your browser vendor, the on-device speech models downloaded once from Hugging Face, and experimental Edge TTS.
> swe-mux itself makes exactly one request on its own behalf, a daily fetch of a static `version.json` that is identical for every install and carries no identifier, and `update_check_enabled` turns it off.

Never write "no server anywhere" or "zero servers": swe-mux is a local aiohttp daemon and saying otherwise invites the correction in public.
"No vendor-operated backend or relay" is the true version and is just as good a line.
Never write "fully local" unqualified.

### The control plane's defaults

> Nothing in the control plane runs on a Project that did not opt in.
> Automations are per-Project opt-in and every one of them ships off, with a single exception (`session_control`, a permission gate that reads nothing, runs nothing, and spends nothing).
> The land queue needs four separate things before an agent can trigger one: the install-wide switch, the Project's opt-in, a `land_grant` raised from its default of `draft`, and a verification command whose exact bytes a human approved.
> The model-backed capabilities - the scan timeline, the attention observers, the assistant - and read-aloud all ship off.

A draft that describes the control plane as running is describing a configured install, not a fresh one.
The honest way to close that gap is a clearly labelled **recommended fleet setup** in onboarding that turns a named set of these on in one press, which is proposed in [`GTM_ROADMAP.md`](GTM_ROADMAP.md) § Open decisions and is not built.
Do not describe it as though it exists.
