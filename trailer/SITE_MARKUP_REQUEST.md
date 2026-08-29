# Markup the loops need, for whoever owns `site/index.html`

Five loop files now exist under `site/img/` and **nothing references them**.
`site/` is the Pages deploy root, so they are already served; they are simply not on the page.

This file is a request rather than a patch on purpose: `site/index.html`, `site/tools/`, and
`site/README.md` were being edited by another pass while these were produced, and two agents
editing one file is how a copy pass loses a paragraph.
Everything below is exact and can be applied verbatim.

## What exists

| file | geometry | length | bytes | what it shows |
|---|---|---|---|---|
| `img/loop-fleet.mp4` | 1280x666 | 15.8s | 144,079 | three agents starting in three worktrees, status moving with nobody typing |
| `img/loop-mobile.mp4` | 402x874 | 13.9s | 176,010 | the phone: one alert with its reason and the held-back digest, then the session behind it |
| `img/loop-evidence.mp4` | 1280x666 | 11.6s | 311,888 | Activity → Timeline: phase-labelled records, a dead end, a blocked badge, the budget line |
| `img/loop-land.mp4` | 1280x720 | 13.6s | 113,166 | the landing strip: gate named and approved, then the branch landing |
| `img/loop-restart.mp4` | 1280x720 | 18.6s | 166,647 | a counter running through a daemon reload, same sequence numbers on both sides |

No audio track at all, so `muted` is a formality rather than a mute.
`trailer/HERO.md` records which take each one is cut from and how to re-cut them.

## 1. One CSS rule, beside the existing `figure.shot img`

`figure.shot img` at what is currently line 379 sets `display: block; width: 100%; height: auto`.
A `<video>` in the same figure needs the same, and it is one selector:

```css
figure.shot img, figure.shot video { display: block; width: 100%; height: auto; }
```

## 2. The element, wherever a loop belongs

Every attribute below is load-bearing.
`playsinline` is what stops iOS Safari taking the video fullscreen on play; `muted` is what makes
`autoplay` permitted at all; `preload="metadata"` keeps five of these from costing a megabyte on
first paint; and `width`/`height` are what keep `site/tools/check.mjs` able to reason about
layout, exactly as they do for the stills.

```html
<figure class="shot">
  <video src="img/loop-land.mp4" width="1280" height="720"
         autoplay muted loop playsinline preload="metadata"
         aria-label="The Git drawer's landing strip taking a branch through an approved
                     verification gate: the gate is named and marked Approved, the branch is
                     queued, and its row changes to landed."></video>
  <figcaption>...</figcaption>
</figure>
```

`aria-label` rather than `alt`, because `<video>` has no `alt`.
A decorative loop beside prose that already makes the same claim should instead be
`aria-hidden="true"` with no label, rather than labelled twice.

## 3. Where each one goes, as a suggestion rather than a requirement

The copy pass owns the page's argument; this is only what each file is fit for.

- `loop-restart.mp4` and `loop-evidence.mp4` are the two worth putting near the top. They are the
  claims no competitor's page makes, and both are legible at a glance.
- `loop-mobile.mp4` belongs in the `#mobile` section, and it is a **replacement candidate** for
  the placeholder `img/mobile-session.webp` currently at line 611 - whose `alt` still reads
  "Placeholder panel ... standing in for a screenshot that has not been taken yet", and whose
  caption still says "Placeholder". At 402x874 it is the same shape as that slot.
- `loop-land.mp4` belongs wherever the landing/verification gate is described.
- `loop-fleet.mp4` is the weakest of the five as a lead visual and that is deliberate:
  orchestrator fan-out is the axis competitors already own, and `trailer/HERO.md` argues it should
  be setup rather than payoff here. It works as a secondary illustration.

## 4. The hero video is not in the repository, and needs a URL

`trailer/encode_hero.py` produces a 68.9-second, 4,380,652-byte MP4 at `trailer/out/hero.mp4`,
which is gitignored.
It should be uploaded as a **GitHub Release asset** and referenced by that URL, for the reasons in
`trailer/HERO.md` § "Where it lives" - `.git` is already 119 MB, a committed binary is permanent,
and the hero is the asset most likely to be re-cut.

When that URL exists, the hero element wants a poster so the section is not blank while 4 MB
arrives, and should **not** autoplay - a 69-second film is something a visitor chooses:

```html
<video src="https://github.com/jatoran/swe-mux/releases/download/vX.Y.Z/swe-mux-hero.mp4"
       poster="img/desktop-workspace.webp" width="1920" height="1080"
       controls muted loop playsinline preload="none"></video>
```

The five short loops stay committed rather than joining it, and that asymmetry is deliberate: they
are referenced inline, and a page that must reach a release asset to render its own section
renders broken for as long as that fetch is failing.
