# Launch checklist

The preconditions behind each stage of [`GTM_ROADMAP.md`](GTM_ROADMAP.md), and the per-venue preflight.

Two kinds of item live here and they are marked differently.
A **blocker** means the stage does not fire until it is true.
A **note** means it is worth doing and does not hold anything up.

Every repository-state fact below was checked against the GitHub API or the working tree on **2026-08-28** and carries the check that produced it, so a reader can re-run it rather than trust it.

## Stage 0 - Repository hygiene

No audience depends on this stage, which is exactly why it gets skipped.
A visitor arriving from any venue lands here first.

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

The description, homepage, topics, and Discussions switch are **repository settings, not files**, so no work package owns them and they will not arrive as part of anyone's branch.
They are the operator's to set.

Suggested description, matching the tagline exactly:

> Mission control for your coding-agent fleet. Self-hosted terminal multiplexer and control plane for Claude Code, Codex, and other agent CLIs.

Suggested topics: `ai-agents`, `claude-code`, `codex`, `terminal-multiplexer`, `agent-orchestration`, `self-hosted`, `developer-tools`, `pty`, `python`, `local-first`.

### The specific inconsistency to fix first

`README.md` routes feature requests to `https://github.com/jatoran/swe-mux/discussions/categories/ideas`, and `.github/DISCUSSION_TEMPLATE/ideas.yml` is written and committed against that category.
Discussions is disabled, so that link resolves to nothing.
The template's own header comment records the binding that makes this fragile: the filename **is** the category slug, and renaming the category silently stops the form being used.

Enabling Discussions and creating a category named so its slug is `ideas` makes the README true and activates a form that already exists.

## Stage 1 - Assets and clean-machine trial

| Item | State | Kind |
|---|---|---|
| Real screenshots on `swemux.dev` | `site/img/` carries deliberate placeholders; real captures leaked project names and were pulled | Blocker for Stages 3-5 |
| PII-free capture environment | Planned, not built. A second daemon with its own data dir and port plus synthetic projects is sufficient; no VM needed | Blocker for the above |
| Hero video, 60-90 seconds | Does not exist | Blocker for Stages 4-5, and for YouTube outreach entirely |
| Feature GIFs: orchestrator fan-out, land queue landing branches, phone and voice, status board, session-preserving redeploy | Do not exist | Blocker for Stage 4 |
| `README.md` leads with the hero asset | `README.md` carries a `TODO(release)` marker where it goes | Blocker for Stage 4 |
| Clean-machine install trial from the published wheel | Not done | Blocker for Stage 2 |
| Signed desktop installer | No release artifact exists; the v0.1.0 release page carries the wheel, the sdist, and `version.json` | **Not a blocker.** A PyPI-only launch is defensible if the copy says so plainly. `blog/01-launch.md` already carries a `[verify]` marker on exactly this sentence |
| Capture scripts and scene notes kept beside the assets | Not applicable yet | Note - do it while capturing, not after |

Clean-install testing needs real isolation (Windows Sandbox or a Hyper-V VM); Docker cannot host a GUI Windows session.
Capture does not - the isolated second daemon is enough for screenshots and recordings.

## Stage 2 - Quiet trial

| Item | Kind |
|---|---|
| Stage 1 clean-machine trial passed, and every gap it found is fixed or written into the README as a known limit | Blocker |
| Issue templates live, so a tester has somewhere to put a report | Blocker |
| The ask names bug reports rather than adoption ([`OUTREACH_TRACKER.md`](OUTREACH_TRACKER.md)) | Blocker |
| A reply is ready within a day of each response | Blocker in practice - a tester who waits three days does not send a second report |

## Stage 3 - Soft launch

| Item | Kind |
|---|---|
| Stage 2 defects fixed | Blocker |
| Real screenshots on the site | Blocker |
| Discussions live and the README's links resolving | Blocker |
| Each subreddit's current rules read from its own sidebar in the week of posting | Blocker, per venue |
| A differentiated draft per venue, never the same text twice | Blocker |
| Authorship disclosed in the post body regardless of whether the rules demand it | Blocker |
| Half a day free after posting | Blocker |

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

## Stage 4 - Show HN

| Item | Kind |
|---|---|
| Everything in Stages 1-3 | Blocker |
| A week of soft-launch fixes landed, and week 4 spent posting nothing | Blocker |
| A whole working day free, Tuesday to Thursday | Blocker |
| Title final, no editorializing, no superlative | Blocker |
| First comment drafted and ready to paste immediately after submission | Blocker |
| Prepared answers ready for the five predictable questions ([`posts/show-hn.md`](posts/show-hn.md)) | Blocker |
| Nobody asked to upvote or comment. Not one person | Blocker - it is against the guidelines and it is detectable |
| The X thread queued for the same morning | Note |
| Every `[verify]` marker in every draft re-measured against the shipped artifact | Blocker |

Show HN requires "something you've made that other people can play with", easy to try "without barriers such as signups or emails".
swe-mux clears this and has no signup at all; one sentence saying so is worth including, because the absence of a signup is unusual enough in this category to be a point rather than a footnote.

## Stage 5 - Product Hunt

| Item | Kind |
|---|---|
| Show HN happened, and at least a week has passed | Blocker |
| The listing rewritten around what the HN thread proved resonated, not what the draft guessed | Blocker |
| Hero video first in the gallery | Blocker |
| Nobody asked to upvote. Asking people to visit and comment is permitted; asking for votes is not, and paid or coordinated voting is detected and penalized | Blocker |
| Scheduled for 12:01am PT | Note |
| A day free | Blocker |

## Stage 6 - Slow burn

| Item | Kind |
|---|---|
| Blog posts published on `swemux.dev/blog` first, cross-posts carrying the canonical URL back | Blocker per post |
| At most one aggregator submission per fortnight | Blocker |
| Never two aggregator submissions back to back after one lands | Blocker |
| lobste.rs invite in hand, and some non-swe-mux activity on the account | Blocker for lobste.rs only |
| Each awesome list's current rules re-read the week of submission | Blocker per list |
| `awesome-claude-code` 14-day eligibility floor cleared (first commit 2026-08-16, so 2026-08-30) | Blocker for that list only |

## Cross-cutting: things that must be true of every draft before it posts

- Every claim is true of the shipped artifact on the day it posts, not of the roadmap.
- Every `[verify]` marker has been re-measured, not assumed.
- The positioning line is the one string, verbatim.
- The platform claims match `.github/workflows/ci.yml` rather than a prose summary. Specifically: CI builds and install-smokes the wheel on all three hosts, but **no CI job on any host starts a daemon**, so no draft may say a platform is verified working end to end. Windows is the only platform that proves the product running.
- No em dashes.
- No real names, no operator identity, no personal paths, no screenshots carrying either.
