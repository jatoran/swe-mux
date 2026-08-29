# The hero video, and the loops cut from it

One workflow, sixty to seventy-five seconds, no voiceover and no music, assumed to autoplay
muted.
`trailer/capture_hero.py` records it and `trailer/encode_hero.py` cuts it, so a UI change means
re-running two commands rather than reconstructing what the film was supposed to show.

## What this replaces, and why

`trailer/storyboard.md` is a 2:12 feature montage and it is **not** what to build.
A montage of twenty features reinforces the product's worst marketing problem: swe-mux reads as
an enormous pile of things rather than as one reason to switch.
Anyone can list features; the montage proves nothing a screenshot grid does not.

Two more things this deliberately does not do:

- **Orchestrator fan-out is not the lead visual.** Several competitors own that axis and own it
  with bigger numbers. Three agents starting is the *setup* here, over in fourteen seconds, not
  the payoff.
- **Nothing is narrated.** Every claim in the film is a thing the UI says about itself while it
  happens. If a beat needs a caption to land, the beat is wrong.

The distinctive story is the other three: **evidence** of what an agent actually did,
**controlled interruption** rather than a firehose, and **approved landing** rather than a merge
button.

## The six beats

Timings are the target cut, not the recording length; the takes are longer on purpose so the cut
has somewhere to go.

| # | beat | take | ~s | what carries it |
|---|---|---|---|---|
| 1 | Three agents start, in worktrees | `hero-fleet` | 14 | Three sessions appear under two Projects, each in its own worktree, each with a model tag and a timer. Prompts arrive through the daemon's input route, so the recording shows status moving with nobody typing. |
| 2 | Leave the desk | `hero-fleet` (tail) | 6 | The same frame, held, while the timers run and the first agent turns over to done. The "leaving" is the cut to a phone; there is no way to film a chair. |
| 3 | One useful notification | `hero-phone` | 12 | The phone's Alerts panel, one item with **its reason**, and a held-back digest beside it. Then a tap through to the session that raised it. The claim is *one* interruption, so the frame has to show the suppressed ones too. |
| 4 | Inspect the evidence | `hero-evidence` | 14 | Activity → Timeline on a real agent run: phase-labelled behavioural records, a dead end, a blocked badge, the budget line. This is the beat the film exists for. |
| 5 | Land through the approved gate | `hero-land` | 14 | The Git Map landing strip: the gate named and **Approved**, then queued → reconciling → verifying → landed, and the branch row changing under it. |
| 6 | Reload while the rest keep running | `hero-reload` | 14 | A counter printing sequence numbers in one pane, three agent sessions alive beside it. Menu → Maintenance → *Reload daemon (keep sessions)*. The panes go quiet, come back, and the counter is a few hundred ticks further on - the same process, not a new one. |

Order matters and it is the order above: setup, absence, one interruption, evidence, decision,
continuity.

The through-line is a single worktree: an agent reviews `harbor-ui`'s `legend-focus-order`
worktree in beat 1, and that worktree's branch is the one landed in beat 5.
`atlas-api` was the obvious candidate and it does not work, for a reason worth keeping written
down: its trunk checkout carries a *seeded uncommitted change* to `src/limits.py` - the thing that
makes its `git status` pane worth photographing at all - and `rate-limit-ingest` touches the same
file, so the fast-forward correctly refuses with "your local changes would be overwritten".
Measured on 2026-08-28: the branch reconciled, passed the gate, and was then refused at the
landing step.
The refusal is the product being right, and filming it would be filming a failure; discarding the
seeded change to get around it would break the still shots that depend on it.
`harbor-ui`'s trunk is clean and its branch is one ahead and one behind, so the counts have
something to say in both directions.

## The loops are frames of the film

`encode_loops.py` cuts every committed loop out of a hero take, and that indirection is the
point: the loops on `swemux.dev` cannot drift from what the hero video shows, because they are
the same footage.
Re-record a beat and both re-cut from it.

| loop | take | geometry | length | bytes |
|---|---|---|---|---|
| `site/img/loop-fleet.mp4` | `hero-fleet` | 1280x666 | 15.8s | 144,079 |
| `site/img/loop-mobile.mp4` | `hero-phone` | 402x874 | 13.9s | 176,010 |
| `site/img/loop-evidence.mp4` | `hero-evidence` | 1280x666 | 11.6s | 311,888 |
| `site/img/loop-land.mp4` | `hero-land` | 1280x720 | 13.6s | 113,166 |
| `site/img/loop-restart.mp4` | `hero-reload` | 1280x720 | 18.6s | 166,647 |

911,790 bytes for the five together, and `encode_loops.py` fails rather than writing any single
one over a megabyte.
Muted H.264 MP4 rather than GIF: an animated GIF of a dark 1080p UI is an order of magnitude
heavier at worse quality, and `<video autoplay muted loop playsinline>` is what a page should
use for this.
The two 1280x666 loops are 666 rather than 720 tall because their source is cropped - see the
redaction note below.

**One crop in this rig is a redaction and must survive every re-record.** The beats that film
real claude sessions carry the CLI's statusline, and that statusline renders the operator's
actual 5-hour and weekly subscription spend as digits *inside a terminal cell grid*.
`capture_site_shots.scan_for_leaks` reads the DOM and cannot see it.
`STATUSLINE_CROP` in `encode_loops.py` removes the band it lives in, from `loop-fleet`,
`loop-evidence`, and the corresponding hero beats; `encode_loops.py --frames` and
`encode_hero.py --frames` exist so the removal is checked by eye on the *encoded* file rather
than assumed from the geometry.
The phone beat needs no crop for a different reason worth knowing: at 402 CSS px the statusline
truncates before it reaches those figures.
The height of that band is the CLI's to change, not ours, so re-check it rather than trusting
the number.

## What the film does not claim

- **Beat 6 is a daemon reload, not the full desktop redeploy.** The redeploy is a multi-minute
  PyInstaller rebuild that cannot be filmed in a capture environment and must never be run from
  one. The reload makes exactly the same claim - the sessions outlive the process that serves
  them, because a separate supervisor owns the PTYs - and it is the half that is honest to shoot.
  Say "reload", never "rebuild", in any caption written over this beat.
- **The branch landed in beat 5 was not authored on camera.** Its commit is part of the synthetic
  repository's seeded history. The agent in that worktree did a short read-only review. No frame
  asserts otherwise, and none should be captioned to.
- **The agents' answers are real.** They are short read-only prompts against invented
  repositories, sent through the app's own input route and answered by a real CLI. Nothing is
  pasted into a scrollback.
- **Beat 5 does not show a progress bar, because there is not one to show.** In a real checkout
  `.worktree-verify` is about a minute of pytest and the strip sits on *Verifying* long enough to
  read. The synthetic repositories are four files, so the whole pipeline - reconcile, gate,
  fast-forward - finishes in under a second and the queue and verify states flash past between
  two paints. What beat 5 therefore shows is the part that is legible and is also the actual
  claim: the gate **named** (`.worktree-verify`), **Approved**, "agent-initiated landing - you
  approve each one", and then the branch row flipping from *N ahead* to *landed*, with the
  finished count going up. Do not fix this by putting a `sleep` in the verify script. A gate that
  pretends to work is the one thing this whole environment exists not to do; if a slower beat is
  wanted, give the synthetic repository real work worth verifying.

## Known cosmetic artifacts

The same class as the ones `SITE_SHOTS.md` lists, and for the same reason - written down so the
next person does not spend a take chasing them.

- The sidebar's quota chips read **stale**, and after a daemon reload **error**. The poll dials a
  provider with the fixture credential and fails; `provider_quota_poll_minutes = 1440` stops it
  recurring mid-shoot but cannot stop the first attempt, and a reload starts that clock again. The
  percentages themselves are the seeded fixture and are correct.
- Beats recorded after beat 6 show the reload's successor daemon, so uptimes reset and the chips
  are in whichever state that poll left them. Record in order if the chips matter to a frame.
- The claude CLI writes `projects/`, `history.jsonl`, `backups/` and `cache/` into a checkout it
  is pointed at. They are untracked and never in frame, but they will show up in a `git status`
  pane if a beat runs one in that directory.

## Where it lives

**Not in the repository.** `.git` is already 119 MB and a committed binary is permanent, and a
hero video is the one asset that gets re-cut most often - every re-cut would be another copy
kept forever. `encode_hero.py` writes to `trailer/out/`, which is gitignored.

Host it as a **GitHub Release asset** on the release the site documents, and point `site/` at that
URL. A release asset is versioned with the thing it advertises, is served from a CDN, costs the
repository nothing, and can be replaced without rewriting history. The alternative - a branch or
an LFS pointer - puts the bytes back in the clone.

The short loops are the opposite case and *are* committed: each is well under a megabyte, they
are referenced from the page directly, and a page that has to reach a release asset to render its
own section is a page that renders broken while that fetch fails.

## Running it

The environment, its isolation, and its rules are `SITE_SHOTS.md`; everything there applies here
unchanged. From the repository root, with the operator's daemon left alone:

```
uv run python trailer/capture_env.py up --claude-config
uv run python trailer/capture_env.py agent-run
uv run --with playwright python trailer/capture_hero.py
uv run python trailer/encode_hero.py
uv run python trailer/encode_loops.py --frames
uv run python trailer/capture_env.py down
```

`agent-run` is what gives beat 4 a real transcript to draw a Timeline from; without it that beat
records the opt-in screen instead of the records.
Record one beat while iterating on it: `capture_hero.py hero-land`.

Each beat is also the source of one committed loop, which is the point of shooting them this way
rather than separately - the loops on the page are frames of the film, so a UI change cannot make
the two disagree. `encode_loops.py` names which take each loop cuts from.
