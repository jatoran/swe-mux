# Launch checklist

The preconditions behind each step of [`GTM_ROADMAP.md`](GTM_ROADMAP.md), and the per-venue preflight.

Two kinds of item live here and they are marked differently.
A **blocker** means the step does not fire until it is true.
A **note** means it is worth doing and does not hold anything up.

Every repository-state fact below was checked against the GitHub API or the working tree on **2026-08-28** and carries the check that produced it, so a reader can re-run it rather than trust it.

## 1. Artifact and claim audit

No audience depends on this step, which is exactly why it gets skipped.
A visitor arriving from any venue lands here first.

### The claim half

Done 2026-08-28, recorded in [`GTM_ROADMAP.md`](GTM_ROADMAP.md) § The claim audit.

| Item | State | Kind |
|---|---|---|
| Session survival is claimed unconditionally, **and the default matches** | **Not yet true, and asserted rather than trusted.** The operator decided 2026-08-28 to flip `pty_supervisor_enabled` to `True` rather than qualify the sentence; the copy is written that way and the code change belongs to another branch. `site/tools/check.mjs` fails while the two disagree | **Blocker, and it is the one holding this branch.** It was the sharpest finding of the audit and it is now the last open item from it |
| No config key name appears in user-facing copy | Done. `pty_supervisor_enabled` is in `.docs/` only - not in a post, not in the README's feature list, not on the site | Blocker |
| No draft says "sessions never die" or "sessions survive everything" | Done. The Show HN alternate title and the Product Hunt alternate tagline both carried it and both are deleted | Blocker |
| No draft says "no server anywhere" or "zero servers" | Done. swe-mux is a local aiohttp daemon; the copy now says "no vendor-operated backend or relay" | Blocker |
| No draft says "fully local" unqualified | Done. Every instance now names what crosses the network and which switch governs it | Blocker |
| No draft says notifications fire only when an agent genuinely needs a human | Done. The five alert reasons and three suppression rules are named instead | Blocker |
| Control-plane copy names the opt-ins | Done. Automations, the land queue's four gates, and the model-backed features are all described as off | Blocker |
| "STT runs locally" names its configuration | Done, and the claim itself was **verified accurate**: both shipped engines decode on the host | Blocker |
| Version and artifact facts current | Done. 0.1.2 on PyPI, v0.1.2 carries an unsigned installer and a portable archive | Blocker |

### The artifact half

These are **repository settings, not files**, so no work package owns them and they will not arrive as part of anyone's branch.
They are the operator's to set.

| Item | State on 2026-08-28 | Kind | How it was checked |
|---|---|---|---|
| Repository description | Absent (`null`) | Blocker | `gh api repos/jatoran/swe-mux --jq .description` |
| Homepage URL set to `https://swemux.dev` | Absent (`null`) | Blocker | same call, `.homepage` |
| Topics | Empty list | Blocker | same call, `.topics` |
| Discussions enabled | **Disabled** | Blocker | same call, `.has_discussions` |
| Discussions categories: Ideas, Q&A, Show and tell | Do not exist; Discussions is off | Blocker | as above |
| `.github/ISSUE_TEMPLATE/` | **Absent** | Blocker | `ls .github/ISSUE_TEMPLATE` |
| `.github/ISSUE_TEMPLATE/config.yml` | Absent | Blocker | as above |
| `CODE_OF_CONDUCT.md` | Absent | Note | `gh api repos/jatoran/swe-mux/community/profile` |
| Pull request template | Absent | Note | as above |
| `LICENSE`, `CONTRIBUTING.md`, `README.md`, `SECURITY.md` | Present | - | as above |
| Community profile health | 57% | - | as above |

Suggested description, which is where the **tagline** belongs (a repository description is a name, not an explanation):

> Mission control for your coding-agent fleet. Self-hosted terminal multiplexer and control plane for Claude Code, Codex, and other agent CLIs.

Suggested topics: `ai-agents`, `claude-code`, `codex`, `terminal-multiplexer`, `agent-orchestration`, `self-hosted`, `developer-tools`, `pty`, `python`, `local-first`.

### The specific inconsistency to fix first

`README.md` routes feature requests to `https://github.com/jatoran/swe-mux/discussions/categories/ideas`, and `.github/DISCUSSION_TEMPLATE/ideas.yml` is written and committed against that category.
Discussions is disabled, so that link resolves to nothing.
The template's own header comment records the binding that makes this fragile: the filename **is** the category slug, and renaming the category silently stops the form being used.

Enabling Discussions and creating a category named so its slug is `ideas` makes the README true and activates a form that already exists.

## 2. Clean-machine testing

| Item | State | Kind |
|---|---|---|
| Install from the published wheel onto a machine with no checkout and no Node | **Not done** | Blocker for step 3 |
| Install from the **unsigned Windows installer** onto the same machine | **Not done.** Different failure surface: SmartScreen, the Start Menu entry, the run-at-login task, and the bundled supervisor | Blocker for step 3 |
| Every gap found is fixed, or written into the README as a known limit | - | Blocker for step 3 |
| The supervisor specifically: it starts, a session survives a daemon restart, and shutdown leaves no process behind | **Not done, and now a blocker rather than a note.** The default is being flipped on, so every new install spawns a supervisor on a machine nobody has tested ([`GTM_ROADMAP.md`](GTM_ROADMAP.md) § Open decisions) | Blocker |

Clean-install testing needs real isolation (Windows Sandbox or a Hyper-V VM); Docker cannot host a GUI Windows session.

**What CI does and does not cover here**, so this step is not skipped on a misreading: CI builds, validates and install-smokes the wheel on all three hosts, and the `live_daemon` tier starts a real daemon on Linux and Windows from the **source checkout** and proves it serves a shell session and exits cleanly. No CI job starts a daemon from a published artifact on any host.

## 3. The two-cohort beta

| Item | Kind |
|---|---|
| Step 2 passed, and every gap it found is fixed or written into the README as a known limit | Blocker |
| Issue templates live, so a participant has somewhere to put a report | Blocker |
| Cohort A's twenty-minute script written and fixed, so results are comparable across people ([`OUTREACH_TRACKER.md`](OUTREACH_TRACKER.md)) | Blocker |
| Cohort B's three check-in question sets written, and the two-week window scheduled | Blocker |
| The asks are differentiated: cohort A is asked for defects, cohort B is asked to use it | Blocker. Sending one ask to both is how the previous plan measured only the install |
| A reply is ready within a day of each response | Blocker in practice - a participant who waits three days does not send a second report |
| Recruitment does not draw both cohorts from the same pool | Note, and it matters at step 4: the niche launch should reach people the beta did not |

### The assets question, which is smaller than it was

| Item | State | Kind |
|---|---|---|
| Real screenshots on `swemux.dev` | **Done.** Nine real captures, taken in a synthetic installation with invented projects, all wired into the landing page as of this branch | - |
| PII-free capture environment | **Done.** `trailer/capture_env.py` plus `trailer/capture_site_shots.py`, whose leak scan refuses to write a file containing the host's home directory, account name, or git identity | - |
| Hero video, 60-90 seconds | **Done.** 68.9s, silent, published as a v0.1.2 release asset and referenced by the landing page with a local poster and `preload="none"`, so it costs nothing until a visitor presses play | - |
| Feature loops: the fleet starting, the phone alert, the evidence timeline, the land queue, the daemon reload | **Done.** Five committed under `site/img/`, all five on the page, each used once | - |
| `README.md` leads with the hero asset | `README.md` carries a `TODO(release)` marker where it goes. The asset now exists, so this is a paste rather than a shoot | Blocker for step 6 |
| Every committed asset is actually referenced by a page | **Done, and now gated.** `site/tools/check.mjs` walks `img/` and fails on any committed asset no page references, and names the hero release asset explicitly. This is the reverse of the check it already had, and it exists because the failure recurred twice: eight of nine screenshots sat unreferenced in the deploy root, then all five loops did | - |
| Desktop artifact | **Done.** v0.1.2 carries an unsigned Windows installer and a portable archive; `swemux.dev/#download` fills itself from the release manifest | - |
| **Signed** desktop installer | No signing certificate. Needs a purchase | Blocker for step 6 - see below |
| Capture scripts and scene notes kept beside the assets | **Done.** `trailer/SITE_SHOTS.md` | - |

## 4. The one niche launch

| Item | Kind |
|---|---|
| Step 3 complete, and each finding fixed or written into the README as a known limit | Blocker |
| **One venue chosen**, not four. r/ClaudeAI or the Claude Developers Discord, whichever the design partners came from least | Blocker |
| That venue's current rules read from its own sidebar in the week of posting | Blocker |
| Authorship disclosed in the post body regardless of whether the rules demand it | Blocker |
| Half a day free after posting | Blocker |
| The other three venues scheduled at step 7, a week or more apart, never the same text twice | Blocker |

### The Reddit rules gap, stated plainly

Reddit blocks automated fetching from this environment, so **no subreddit rule text in this repository was read from Reddit itself**.
Nothing here records what a rule says.
It records what to go and check, because a first post that breaks a self-promotion rule can cost the account rather than the post, and it does so in the venue that converts best.

Before posting to any subreddit, read its sidebar and its wiki rules page and confirm:

- Whether self-promotion is permitted at all, or only in a designated weekly thread.
- Whether a specific flair is required, and which.
- Whether an account-age or comment-karma floor applies.
- Whether authorship must be disclosed in the post body.
- Whether there is a cooldown between self-promotional posts.
- Whether link posts and text posts are treated differently, which matters for r/programming, where the engineering posts go as links and the announcement never goes at all.

If any of these cannot be established from the sidebar, ask the moderators before posting.
A modmail costs a day; a ban costs the venue.

## 5. Fix what it finds

| Item | Kind |
|---|---|
| Every finding from steps 3 and 4 closed or documented | Blocker |
| The cohorts' named abandonment points specifically addressed, not merely recorded | Blocker |
| No posting at all during this step | Blocker, and the one most likely to be skipped |

## 6. Show HN

| Item | Kind |
|---|---|
| Everything in steps 1 through 5 | Blocker |
| **Activation demonstrated**: repeat users exist, and at least one design partner is still running it after the beta ended without being asked to | **Blocker, and it is the new one.** The front page cannot be retried |
| Hero video live | Blocker |
| **A signed Windows installer** | Blocker - see below |
| A whole working day free, Tuesday to Thursday | Blocker |
| Title final, no editorializing, no superlative | Blocker |
| First comment drafted and ready to paste immediately after submission | Blocker |
| Prepared answers ready for the predictable questions ([`posts/show-hn.md`](posts/show-hn.md)), including "so sessions don't actually survive by default?" | Blocker |
| Nobody asked to upvote or comment. Not one person | Blocker - it is against the guidelines and it is detectable |
| The X thread queued for the same morning | Note |
| Every `[verify]` marker in every draft re-measured against the shipped artifact | Blocker |

Show HN requires "something you've made that other people can play with", easy to try "without barriers such as signups or emails".
swe-mux clears this and has no signup at all; one sentence saying so is worth including, because the absence of a signup is unusual enough in this category to be a point rather than a footnote.

### The signing gate, stated rather than scheduled

The installer exists as of v0.1.2 and is **unsigned**, so SmartScreen warns on first run.

That is fine for steps 3 and 4, where every participant was invited and can be told what to expect.
It is avoidable conversion loss at step 6, where the traffic is unrepeatable and the alternative on offer is "install Python, then uv, then an extra, then the WebView2 Runtime".

Signing needs a certificate the operator has to buy.
**This is a gate to state, not work to schedule**, and it is recorded here so that firing step 6 without it is a decision somebody made rather than a thing that happened.

## 7. Slow burn

| Item | Kind |
|---|---|
| Blog posts published on `swemux.dev/blog` first, cross-posts carrying the canonical URL back | Blocker per post |
| At most one aggregator submission per fortnight | Blocker |
| Never two aggregator submissions back to back after one lands | Blocker |
| The community venues deferred from step 4 posted one at a time, a week or more apart | Blocker |
| lobste.rs invite in hand, and some non-swe-mux activity on the account | Blocker for lobste.rs only |
| Each awesome list's current rules re-read the week of submission | Blocker per list |
| `awesome-claude-code` 14-day eligibility floor cleared (first commit 2026-08-16, so 2026-08-30) | Blocker for that list only |
| Hero video exists before any YouTube outreach | Blocker for that channel only |

## 8. Product Hunt, conditional

| Item | Kind |
|---|---|
| **Evidence of interest beyond the terminal-tool audience** | **The condition.** Absent it, this step does not happen and nothing is lost |
| Step 6 happened, and at least a week has passed | Blocker |
| The listing rewritten around what the HN thread proved resonated, not what the draft guessed | Blocker |
| Hero video first in the gallery | Blocker |
| Nobody asked to upvote. Asking people to visit and comment is permitted; asking for votes is not, and paid or coordinated voting is detected and penalized | Blocker |
| Scheduled for 12:01am PT | Note |
| A day free | Blocker |

What counts as the evidence: traffic or installs from a non-developer-tooling referrer, requests from people who are not already running agent CLIs, or a newsletter pickup that reached a general audience.

## Cross-cutting: things that must be true of every draft before it posts

- **Every claim is true of the shipped artifact, in its default configuration, on the day it posts.** The default-configuration clause is what the 2026-08-28 audit added and it is the one that moved the most copy.
- Every `[verify]` marker has been re-measured, not assumed.
- The positioning line is the one string, verbatim. The tagline is a tagline and never substitutes for it.
- The platform claims match `.github/workflows/ci.yml` rather than a prose summary. Specifically: CI builds and install-smokes the wheel on all three hosts, and the `live_daemon` tier starts a daemon from the **source checkout** on Linux and Windows, but **no CI job starts a daemon from a published artifact**, so no draft may say a platform is verified working end to end from what a user installs. Windows is the only platform that proves the product running.
- No em dashes.
- No real names, no operator identity, no personal paths, no screenshots carrying either.
