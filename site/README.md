# swe-mux landing page

The public page, its argument, and the rules that keep it honest.
Read this before editing `index.html`.

```
site/
  index.html          the whole page: markup, inline CSS, inline JS, no build step
  img/                logo variants and screenshots
  tools/check.mjs     layout + behaviour gate (borrows Playwright from ../frontend)
  tools/contrast.py   recomputes the WCAG table from the stylesheet
  tools/logo.py       regenerates both wordmark variants from the source render
  tools/wordmark-source.png   the render logo.py keys; lives in tools/ so the
                              deploy root holds only deployable files
```

## Verify before you ship

```
node site/tools/check.mjs      # overflow at 4 widths x 2 themes, assets, install tabs, theme toggle
python site/tools/contrast.py  # every text token against both backgrounds
grep -rn data-todo site/       # unfilled placeholder URLs
```

`check.mjs` exits non-zero on failure and covers the things that have actually broken here before.
Both scripts are self-contained and take no arguments.

---

# 1. The argument

## What swe-mux is

An **agentic development environment** *and* an **agent control plane**.
Both halves are load-bearing and the page must say so above the fold.

- **The workbench.** Sessions, panes, tabs, notes, files, git, previews, a Run menu. Everything you look at. Table stakes, and every competitor has some of it.
- **The control plane.** Deterministic evidence, model-free detectors, incident ranking, an interrupt budget, a return path agents can read. Decides *what* you look at.

The connective argument, which is the actual moat: **the control plane is only buildable on top of the workbench.**
You cannot measure a fleet whose terminals, shell profiles, and input telemetry you do not own.
Fan-out estimation, breakpoint delivery, resumption lag, and race-free content hashes all require owning the PTY.

## Naming rules

**Never write "ACP" on the page.**
The acronym collides with Zed's Agent Client Protocol and IBM's Agent Communication Protocol, both active in this exact market.
Use the words.

**"ADE" may appear as a category word in prose, never as the identity.**
Naming yourself an ADE invites a feature-for-feature comparison against tools whose entire business is that category.

**Never state a harness count.**
Not "six harnesses", not "five agent CLIs", nowhere.
The registry keeps growing and any number is wrong the week after it ships.
Name the harnesses if it helps; count them never.
The four-per-day interrupt budget is a different thing and stays, because it is a real configured default.

## Differentiators, ranked by how hard they are to copy

Checked against the live market in August 2026: Herdr, Orca, cmux, Superset, AgentsRoom, JetBrains Air, and the ~162 tools in `awesome-agent-orchestrators`.

1. **Ground truth over self-report.** Everyone else's "what happened" is the transcript, which is the agent's own account. This has a deterministic fact substrate: content hashes at the adapter boundary, parsed test outcomes, git tree hashes, write-then-read lineage. **Nothing found in the market does this.**
2. **A theory of interruption.** Four channels split by cost-to-resolve, incident merging, a hard daily budget, mined demotion rules that expire, suppressed counts always shown. **No counterpart found.** Competitors give you a grid of tiles and let you sort it out.
3. **The inverse arrow.** Agents pull from the control plane through MCP: sibling status, prior resolutions, dead ends, provenance. Not an orchestrator pushing commands down to workers.
4. **A refusal to actuate.** Observers can never type, approve, or spawn. Structurally, not by policy.
5. **Mobile depth, not mobile existence.** See section 4.
6. **Commit-level git provenance.** Committer versus contributor, with confidence. No counterpart found.
7. **Cross-vendor history.** One search and one resume across every supported harness. The nearest competitor resumes two.
8. **Voice control.** Effectively absent from the category.
9. **Windows-first.** A large share of the desktop category is macOS-only. This is an asset, not an apology.
10. **Long-lived sessions.** Processes you live with for weeks, not tickets that open and close.

**Do not lead with worktree parallelism, diff review, or agent count.**
That is the commodity axis, it is their axis, and they win it: Superset claims 100+ agents and Orca claims 40+.

---

# 2. The hero

## Settled copy

> **Terminal multiplexer. Agent control plane. Full mobile parity.**
>
> *Infrastructure built on what agents do, rather than what they are.*

## Why the H1 is shaped that way

It is a spec line, not a promise, and the three sentences are deliberately co-equal.

**Do not join them with "and" or "with."**
A connective demotes whichever clause it introduces, and mobile is the one that kept getting demoted.
Periods hold all three at the same weight, and the line break puts mobile parity on its own line so it carries equal visual weight too.

## Why the sub-headline is what it is

It is an engineering claim rather than a marketing one.
The evidence layer keys on agent *behaviour* (file writes, commands, exit codes, test results, commits) rather than agent *identity* (which model, which harness, which version).
That is why the substrate does not rot as agents change, and why adding a harness is a descriptor plus an adapter instead of a redesign.
It states harness-neutrality and model-durability in one breath.

**Do not claim the control plane "never conflicts" with agent updates.**
swe-mux is genuinely not in the execution path, so it cannot break or slow an agent, and a new CLI feature works the day it ships.
But it does read transcripts, hook streams, and CLI state files, all of which drift, and the runbooks in `.docs/development/` record several incidents of exactly that.
Claim *not in the path* and *the substrate does not move*.
Both are true and checkable.
"Never conflicts" is neither.

## Above the fold

Hard limit: **kick, headline, one sub-headline line, one short paragraph, install callout, then the hero visual.**
Nothing else.
An earlier draft ran two full lede paragraphs plus two half-panels plus a connective paragraph before the first image, which buried everything under it.

The hero paragraph is one sentence of position and one clause list.
If a fact will not fit, it belongs to the section that owns it, not to the hero.

**The hero visual is one composite, full width:** the desktop workspace as a wide real screenshot, with a phone device shell overlapping its lower right corner showing the same workspace.
It is the only place on the page that shows the whole application at once, so it may include chrome.
Everywhere else the rule against full-window screenshots still holds.

## Rejected, so they are not re-proposed

Sub-headlines:

| Rejected | Why |
|---|---|
| "Agents don't need replacing." | Nobody is trying to replace them, including every competitor, all of whom run your CLIs on your subscription. The negation had no opponent. |
| "Agents don't need orchestrating." | False. Agents plainly do need coordinating, and swe-mux provides queueing, messaging, interrupts, and routing to do it with. The distinction is *who* coordinates, not whether. |
| "Agents don't need another agent above them." | Accurate and defensible, but it spends the line picking a fight instead of stating what the product is. |
| Anything counting agents. | Ties the pitch to a threshold the reader must accept before the line means anything. |

Headline directions:

| Rejected | Why |
|---|---|
| Problem-framed ("You cannot watch ten coding agents") | Lower energy than a capability claim, and it reads defensive next to Herdr's "Run them anywhere. Leave them running." |
| Verification-framed ("Your agents tell you they are done. swe-mux checks.") | Promises a feature that ships off, since the control plane is per-project opt-in, and "checks" overclaims what a declared-vs-verified detector does. It survives as a section header where there is context around it. |
| Persistence-framed ("Close the laptop. Keep the fleet.") | Herdr owns this ground and says it better. |
| Viral / provocative ("You stopped writing code six months ago") | Memorable, but the audience is buying a utility and the register fights the rest of the page. |

---

# 3. Page structure

01. The whole workspace, on your phone. *(real screenshot + claims, then mobile crops)*
02. Know which agent needs you. *(status detection, crops)*
03. Sessions that outlive the app itself. *(persistence and redeploy, crops)*
04. The workbench. *(rows, no images)*
05. Every harness, and every shell. *(rows, no images)*
06. Notes that are a real editor. *(crops)*
07. Git that knows which agent did it. *(crops)*
08. Drive it without touching it. *(voice and push, crops)*
09. The control plane. *(grouped rows + one crop pair)*
10. Also in the box. *(rows)*
11. Install.

## Section rules

**Lead every section title with the capability, never the pain.**
"The whole workspace, on your phone," not "The phone is not a companion app."
The competitive point still gets made, in the body, after the reader knows what they are being offered.
A title that opens on a problem makes the reader supply the doubt before you have supplied the answer.

**One numbering level only.**
Sections carry `01`..`11` and nothing inside a section is numbered.
An earlier version had section `02` containing items `01`..`08` and section `03` resuming at `09`, which reads as a broken outline.

**Rows, not cards, for anything without an image.**
Feature cards were sized for a screenshot each and there is no room for one per feature.
`.flat` rows carry a label column and a description and hold far more per screen.
Cards are gone; do not reintroduce them.

**Section 05 leads with the shell, not the harness.**
swe-mux is a terminal multiplexer before it is anything else: any shell, any TUI, any CLI runs in a real pseudoterminal exactly as it does outside.
A harness the registry does not know still works perfectly, you just do not get the layer on top.
Say that first, then describe what normalization adds for the ones it does know.
Getting this backwards makes the product sound like it only supports a fixed list.

## Removed sections

"What it will not do" and "Not for you if" were cut.
The boundaries they carried (never actuates, per-project opt-in, nothing leaves the machine, fails closed) are now stated inside the control plane intro and the relevant rows, where they land as design facts rather than as a wall of disclaimers.

The spec strip under the hero was also cut.
Every fact on it is stated somewhere it means more: platforms in the install callout, harnesses in section 05, the privacy claims in sections 01 and 10.

---

# 4. Mobile is top-of-page, but the claim is depth

Mobile gets its own section immediately after the hero, before the workbench and before the control plane.
It is not a responsive-CSS footnote; it is a product position.

**Do not claim to be the only one with a phone client.**
That is false.
AgentsRoom is mobile-first with native iOS and Android, Orca ships both stores, cmux has an iOS beta, and a dozen others in the orchestrator list have some mobile story.

**The defensible claim is parity versus companion**, and it holds because their own marketing concedes it.
Orca's phone client "watches live agent status" and AgentsRoom's own page calls its apps companions rather than full-featured replicas.
swe-mux has no feature that exists on desktop and not on the phone.
Verified: the only desktop-only code paths are hidden terminal pre-warming, the collapsed sidebar rail, and keyboard chords a phone cannot produce.

**The second defensible claim is no relay.**
AgentsRoom brokers phone-to-desktop through their own E2EE relay.
swe-mux reaches the machine over the user's own tailnet with no third party in the path at all.

The structural argument stays: one session attached from several devices, exactly one writer, one arbitrated size, and the phone renders a projection of the same workspace tree rather than a second layout.

---

# 5. Voice and language

- **Direct, factual, load-bearing.** Every sentence should carry a fact, a mechanism, or a boundary. If a sentence could appear on any competitor's page, cut it.
- **Short.** A feature is a title plus at most two sentences. If it needs a third, it is two features, or it belongs in a flat row.
- **Name the mechanism, not the benefit.** "Content hashes computed at the adapter boundary, never by reading the file back" beats "reliable change tracking".
- **Second person, present tense.** "You approve the file's exact bytes", not "users are prompted to approve".
- **State limits plainly and early.** Honest limits buy credibility for the claims.
- **Cite when a number is doing work.** One clause, no footnote apparatus.
- **No marketing register.** Banned: seamless, powerful, effortless, unleash, supercharge, revolutionize, game-changing, blazing fast, "the future of".
- **No LLM tells.** No reflexive "it's not just X, it's Y", no rule-of-three padding, no rhetorical questions, no exclamation marks, no "in today's world".
- **Never use the em dash.** Use a plain dash with spaces, or restructure the sentence.

---

# 6. Visual system

TUI, because the product is a terminal multiplexer and the app already looks like this.
Match the app rather than inventing a brand.

## Type: two faces, split by job

**Monospace carries identity:** headings, the kick line, nav, section labels, tags, row labels, install commands, and all code.
**A system sans carries prose:** hero lede, section intros, row descriptions, claims, captions, notes, footer.
Neither face is loaded from a network; both are system stacks.

This overrides the original "monospace everywhere, body copy included" rule, which was wrong for this page's length.
Monospace kills word-shape recognition, and by the time the page carried eleven sections and thirty-eight description rows it was genuinely tiring to read.
Identity was never in the body copy.
It is in the headline, the labels, and the hairline grid, all of which stayed mono.

Body is 15.5px sans at 1.62.
Inline `code` inside sans prose is stepped to `0.915em`, because monospace renders optically larger at the same pixel size.

## Colour

Dark by default, light fully supported.
Light is a complete token override under `:root[data-theme="light"]`, not a filter.

| | dark | light |
|---|---|---|
| `--bg` | `#0e1016` | `#fbfbfd` |
| `--panel` | `#151821` | `#ffffff` |
| `--panel-2` | `#1b1f2a` | `#f4f5f9` |
| `--line` / `--line-2` | `#242a38` / `#353d51` | `#e6e8f0` / `#cbd0de` |
| `--fg` / `--fg-2` / `--fg-3` | `#dfe5f0` / `#adb7cc` / `#828da6` | `#161923` / `#414963` / `#626a83` |
| `--fg-4` | `#626d88` | `#8a92a8` |
| `--green` | `#8fdb6f` | `#3f7a12` |
| `--cyan` | `#6fd8cb` | `#0b7a72` |
| `--orange` | `#e8b56b` | `#8a5806` |
| `--red` | `#f87f95` | `#b0243c` |

**Green is the brand accent. Cyan marks workbench surfaces. Orange marks control-plane surfaces.**
Keep that mapping consistent.

**Every text token must clear WCAG AA (4.5:1) against both `--bg` and `--panel`.**
Measured, not assumed: run `tools/contrast.py`, which reads the tokens straight out of the stylesheet.
Every token currently passes except `--fg-4`, which sits at roughly 3.0 to 3.7 and is therefore **restricted to borders and inert markers, never body text**.
The sole remaining text use is the `soon` marker beside MACOS, which is deliberately the quietest thing on the page.
A previous palette had `--fg-4` at 2.5:1 and used it for the kick line, the OS labels, and every placeholder tag.

**Every colour must be a token.**
A hard-coded rgba survives the theme switch and breaks in one direction only.
That is how the placeholder hatch fills nearly shipped invisible on light; they now read `var(--hatch)`.

**The theme choice must not flash.**
A small script in `<head>`, before the stylesheet, reads `localStorage` then `prefers-color-scheme` and stamps `data-theme` on the root.
Toggling updates the `color-scheme` meta too, so form controls and scrollbars follow.

## Layout

- **Square corners, 1px hairlines, no shadows, no gradients, no rounded cards.**
- **Grid seams instead of gaps.** Panel grids use a 1px background line showing through, which reads as a TUI panel grid.
- **Structure over decoration.** Numbered section rules, `[01]` style indices, `▸` and `[x]` markers, a blinking block cursor after the headline. The wordmark and the inline GitHub mark are the only two pieces of imagery in the chrome; there are no decorative icons.
- **Self-contained.** One HTML file, inline CSS, inline JS, no external fonts, no CDN, no analytics, no third-party requests of any kind.
- **The page must never scroll horizontally.** `tools/check.mjs` asserts `scrollWidth === clientWidth` at 360, 390, 768, and 1440, in both themes. Grid tracks need `minmax(min(Npx, 100%), 1fr)`, and any flex item that should shrink needs `min-width: 0`, because `overflow-x: auto` alone will not do it.

---

# 7. Components

## Top bar

Brand left, section anchors centre (horizontally scrollable, fading on the right edge only), then a utility group behind a hairline divider: **docs, blog, GitHub, colour-scheme toggle**.
The GitHub mark is inline SVG, because nothing on this page loads from a third-party host.
Below 720px the utility links go icon-only.

The bar carried a `multiplexer · control plane · full mobile` descriptor next to the brand and it was cut.
The H1 says the same thing forty pixels below it, and at 1280 it pushed the section nav into the utility group.

## Install callout

Sits directly under the hero paragraph, because it is the first thing a convinced reader wants and it should not require a scroll.

**Capped at 780px, not full width.**
A full-bleed install box reads as a code block rather than a control, and the header row only needs about 500px before it stops wrapping.
The per-method note sits **outside and below** the box, so the bordered panel contains only the interactive parts: tabs, platforms, command, copy.

Method tabs on the left of the header row, platform indicators right-aligned on the same row.
**Selecting a method lights the platforms it targets**, which makes the row informative rather than decorative.
The command, its `$` prompt, and the note all swap with the tab.

**MACOS is deliberately never lit and carries a `soon` marker.**
Roadmap Phase 10 has every Linux box checked and macOS open, so lighting it would claim something the build does not support, and leaving it dark with no explanation reads as a bug.
When macOS is verified: remove the `soon` span, add `macos` to the `curl`, `uv`, and `source` entries in the install map, and update the assertion in `tools/check.mjs` that currently fails if macOS is lit.

## Wordmark and favicon

`img/logo.png` and `img/logo-light.png`, both 640px wide with transparent grounds, regenerated from `tools/wordmark-source.png` by `tools/logo.py`.

**Two files, not one file plus a CSS filter.**
The light theme needs the letterforms to become ink *and* the trapezoid to darken to the light-theme green.
No single filter does both without wrecking one of them.
The variants are swapped by `data-theme` in CSS, so the toggle keeps working.

**Keying gotcha, recorded because it cost a pass.**
The source render carries film-grain noise.
Background pixels top out at a max-channel of 21 while glyph pixels reach 255, so keying against the *sampled* background left every speck faintly opaque and `getbbox()` returned the entire frame.
The black point is set at 22, just above the measured noise ceiling.
If the logo is ever re-rendered, re-measure that ceiling rather than assuming 22.

Alpha comes from the strongest channel rather than luminance, which keeps the green mark fully opaque instead of fading it for being darker than the white letters.
Verified by compositing both variants over dark, light, and a hostile mid-grey: no halos.

**The favicon is the wordmark's trapezoid alone**, inline SVG on a transparent ground so it reads on both a light and a dark browser tab strip.

---

# 8. Images

## Screenshot rules

- **Every image surface is a desktop and mobile pair.** A desktop crop with no mobile counterpart is incomplete.
- **Show the feature, not the application.** Crop to the panel, the list, the menu, the badge row. A full-window screenshot of the whole UI teaches nothing at page width and is the single most common mistake here. The one exception is the hero composite.
- **Desktop crops are roughly 3:2. Mobile crops are roughly 9:16.** Not full phone screens.
- **The crop must contain the claim.** The attention inbox shot has to show the budget line and a suppressed item with its reason, because that is the argument.
- **An empty panel is not a screenshot of a feature.** A shot of the attention inbox reading "Nothing is ranked" is a picture of the feature switched off.
- **Real application, never mockups**, and say so on the page.
- **Scrub before publishing.** Live screenshots carry real project names, file paths, branch names, transcript text, account labels, and quota numbers.

Diagrams carry their own placeholder class (`.vis`) separate from screenshot crops (`.crop`), because they are illustrations to be drawn rather than shots to be taken.
Two are specified so far: the workbench split tree, and the control plane's escalating-cost stack with a human gate on every arrow leaving it.

## Current status

`img/mobile-session.webp` is wired into the page and is the only screenshot that passes.
It is naturally cropped, on-claim, and shows only swe-mux's own repository.

The other eight captures are **not usable as-is** and are not referenced by the page.
The five `desktop-*` files are full-window shots, which this document forbids.
All of them need scrubbing: they show unrelated project names in the sidebar and competitor checkout directories (`.tmp-herdr/`, `.tmp-omp/`) in the file tree.
`desktop-alerts.webp` additionally shows the attention inbox with zero records.

The page keeps dashed placeholders wherever a real image is not yet usable, and each placeholder states exactly what its replacement must contain.

---

# 9. Facts to re-verify before editing

Each of these was wrong on this page at some point.

- **Four interrupts a day, two an hour.** `attention_daily_interrupt_budget` defaults to 4.
- **Windows-first, Linux supported, macOS unverified.** Roadmap Phase 10 has every Linux box checked and the macOS box open. The page claimed "Windows only, WSL is not a supported host" long after the WSL bridge shipped.
- **Every supported harness has conversation discovery and resume**, including the store-backed one. Confirmed in `harness.py` and `adapters/`.
- **swe-mux finds hung processes, it does not kill them.** Suspected orphans are never terminated automatically. Do not write copy promising automatic cleanup.
- **The queue waits on a readiness gate and a stability window**, not on a binary "done" signal.
- **Mobile parity is real.** Re-check before repeating it: the claim is that nothing functional is desktop-only.

---

# 10. Open before launch

- **Fill the `data-todo` placeholders.** The docs, blog, and repository URLs are all `href="#"`. Grep `data-todo`.
- **Replace the install commands.** `get.swe-mux.dev` does not exist. The `source` flow is the only real one today, and its full version is section 11.
  The clone URL no longer says `REPLACE`: it resolves to `github.com/jatoran/swe-mux`, in the hero command and in the footer, alongside the license links added in Phase 10.5.
  If the project is ever published under an organisation rather than that account, both places and the footer's two license links change together.
- **Re-shoot and scrub the screenshots.** See section 8.
- **Draw the two diagrams.** See section 8.
