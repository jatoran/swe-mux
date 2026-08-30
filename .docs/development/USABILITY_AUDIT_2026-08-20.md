# Global usability audit: first use and overwhelm

## Purpose

The app-wide first-use audit `ROADMAP.md` Phase 15 schedules under "Global usability audit session".
It asks four questions of every surface with complex functionality, not voice alone:

- Where does a new user hit a wall?
- Which advanced features are invisible or intimidating?
- Where does a gated feature fail silently instead of explaining itself?
- What would give someone a great first ten minutes, on desktop and on the phone?

This is analysis. No product code changed. Every finding names the file and line that carries it, so each can be re-checked before it is acted on.

Audit basis: master `b15a9c6`, worktree `worktree-usability-audit`.

## Method

- Read `design/00_OVERVIEW.md`, `design/features/ui.md` in full, and the feature documents that own each surface a finding touches (`setting-links.md`, `automation-enablement.md`, `voice.md`, `assistant.md`, `project-actions.md`, `prompt-queue.md`).
- Read `development/NEW_USER_RELEASE_READINESS.md` and `ROADMAP.md` Phase 15 to avoid restating shipped or scheduled work.
- Read the frontend where the docs are thin or where a claim needed checking against what renders: `GuidedTutorial.tsx`, `tutorial.ts`, `HarnessSetup.tsx`, `AssistantPanel.tsx`, `ProjectRunMenu.tsx`, `Settings.tsx`, `settingsTabs.ts`, `settingTargets.ts`, `drawerTabs.ts`, `UtilityDrawer.tsx`, `ProviderAccounts.tsx`, `RailEditor.tsx`, `NotificationPushSettings.tsx`, `App.tsx`, `style.css`.
- Read the sibling Continuity project's UX design area (`D:\PROJECTS\continuity\.docs\design\principles.md`, `motion.md`, `defaults.md`, `features/tutorial.md`) for the comparison it invites: two products by the same author, one of which solved the reference-documentation problem generatively.
- Web research on onboarding and feature-richness, preferring primary sources over vendor content marketing.

## Scale of the surface being learned

Not a complaint, a measurement.
These numbers set the size of the learning problem any onboarding has to answer for.

| Thing | Count | Source |
|---|---|---|
| Registered commands (bindable, palette-searchable) | 106 | `src/swe_mux/keybindings.py` `KEYBINDING_COMMANDS` |
| Install config keys | 206 | `src/swe_mux/config.py` |
| Settings tabs | 17 | `frontend/src/settingsTabs.ts` |
| Settings sections (`<h3>`) in `Settings.tsx` alone | 58 | `frontend/src/Settings.tsx` |
| Side-panel tabs | 11 | `frontend/src/drawerTabs.ts` `DRAWER_TABS` |
| Registered side-panel segments and sections | 17 | `frontend/src/drawerSegments.ts` |
| Theme options | 30 | `frontend/src/theme.ts` `themeOptions` |
| Feature design documents | 48 | `.docs/design/features/` |
| Guided-tour steps a first-time user is walked through | 14 to 16 | `frontend/src/GuidedTutorial.tsx` |

The relevant published finding is Nielsen's: the complexity a design can sustain is bounded by how much the user is willing to learn, and the correct response is to simplify the *initial* experience rather than the product ([Feature Richness and User Engagement](https://www.nngroup.com/articles/feature-richness-and-user-engagement/)).
swe-mux is a high-engagement professional tool and is entitled to this much surface.
What it is not entitled to is spending a first-run user's whole attention budget before they have started work, which is what the current first run does.

## Research grounding

Four load-bearing results, each used by a specific finding.

- **The paradox of the active user.** Users start immediately and do not read documentation, even though reading would save them time. Onboarding must therefore assume nobody is studying ([NN/g](https://www.nngroup.com/articles/paradox-of-the-active-user/), summarizing Carroll and Rosson).
- **Push tutorials underperform pull help.** Intrusive tutorials "interrupt users, don't necessarily improve task performance, and are quickly forgotten"; help works when it is triggered by a signal that the user needs it now. The stated guidance is to make help dismissible *and re-accessible later* ([Onboarding Tutorials vs. Contextual Help](https://www.nngroup.com/articles/onboarding-tutorials/)).
- **Chained coach marks read as complexity.** Multi-step chains of overlay tips make an app "appear overly complicated and daunting" and get dismissed faster; single, contextual, one-at-a-time hints work ([Instructional Overlays and Coach Marks](https://www.nngroup.com/articles/mobile-instructional-overlay/)).
- **Complex applications need in-context help, not upfront tutorials.** For expert tools the recommendation is "abbreviated, in-context help and guidance within the application" rather than forcing a tutorial ([10 Usability Heuristics Applied to Complex Applications](https://www.nngroup.com/articles/usability-heuristics-complex-applications/)).

The Continuity comparison is the practical version of the last point.
Continuity opens a read-only tutorial *buffer* on first launch, generated deterministically from its own feature documents and default keymap, re-openable at any time via the `help.tutorial` command, and drift-checked in CI (`D:\PROJECTS\continuity\.docs\design\features\tutorial.md`).
It is a reference the user can return to, not a walk they get one shot at.
Continuity's `principles.md` also states the rule swe-mux's first run breaks: "A surface that opens (overlay, palette, modal) must always offer a clear way out."

## Findings, ranked by first-impression impact against effort

Ranking is impact-first.
The effort column separates **quick polish** from **needs design**, as the charter asks.

| # | Finding | Impact | Effort |
|---|---|---|---|
| 1 | The guided tour strands with no way forward on mobile | Blocking | Quick polish |
| 2 | The tour demands a real provider login at step 5 and cannot be skipped | Blocking | Quick polish |
| 3 | Two first-run surfaces render simultaneously, wrong one on top | High | Quick polish |
| 4 | With no harness enabled, Run silently drops every agent row | High | Quick polish (hand off) |
| 5 | No help surface exists, and the one tour is one-shot and unsearchable | High | Needs design |
| 6 | The assistant's off-state names a Settings tab that does not exist | Medium | Quick polish (hand off) |
| 7 | Tour copy describes a menu the app no longer has | Medium | Quick polish |
| 8 | The empty workspace stage is the biggest thing on screen and holds no control | Medium | Quick polish |
| 9 | Run shows worktree and action-authoring machinery on day one | Medium | Needs design |
| 10 | The Voice tab is eight subsystems, one of which is not voice | Medium | Needs design |
| 11 | On a phone, eleven features are eleven unlabelled glyphs | Medium | Quick polish |
| 12 | Naming drift: one row calls the side panel a "utility drawer" | Low | Quick polish |

---

### 1. The guided tour strands with no way forward on mobile

**What happens.**
Tour step `resources` is click-gated on a Notes control:

```
frontend/src/GuidedTutorial.tsx:73
{id:'resources', ..., selectors:['[data-tutorial="project-notes"]'],
 action:{kind:'click',selectors:['[data-tutorial="project-notes"]'],hint:'Open Notes'}}
```

Exactly two elements ever carry `data-tutorial="project-notes"`:

- the desktop launcher rail (`frontend/src/App.tsx:6669`), rendered only under `!mobileWorkspace && !clipboardOpen` (`App.tsx:6660`);
- a tab button inside an open side panel (`frontend/src/UtilityDrawer.tsx:561`).

On a phone with the side panel closed, neither exists.
A step carrying an `action` renders its hint *instead of* a Next button:

```
frontend/src/GuidedTutorial.tsx:153
{step.action ? <em>{step.action.hint}</em> : <button class="primary" …>Next</button>}
```

The only remaining control is `Exit tour ×`.
The tour is 14 steps on a phone and dies at step 10.

The same failure is reachable on desktop: both hosts filter by `drawerTabVisible` / `visibleDrawerTabs`, so a user who has hidden the Notes tab removes the anchor there too.

**Why it matters.**
`navigateTutorial` already knows how to clear the way for a mobile step - it opens the navigation sidebar for `projects`, `features`, and `feature-menu` (`App.tsx:3344`, `App.tsx:3359`) - and simply does not do it for `resources`.
The in-code comment at `GuidedTutorial.tsx:66-72` correctly states the invariant ("an absent anchor strands the tour") and then records the mobile case as a known exception rather than fixing it.

**Fix.**

- Quick: in `navigateTutorial`, open the side panel when `step === 'resources'` on the mobile layout, the way the sidebar is already opened for other steps.
- Also quick, and worth doing regardless: give every action-gated step a visible `Skip this step` control beside the hint. A tour whose only escape is abandonment violates the reversibility rule Continuity states explicitly and NN/g's "make help easy to dismiss".
- Add a renderer test asserting that each action step's selector resolves on both layouts.

---

### 2. The tour demands a real provider login at step 5 and cannot be skipped

**What happens.**
Step `accounts` is event-gated on an actual saved credential:

```
frontend/src/GuidedTutorial.tsx:57
{id:'accounts', ..., action:{kind:'event',gate:{action:'account-saved'},
 hint:'Sign in + save, or save the current login'}}
```

`navigateTutorial` opens Settings → Accounts for it (`App.tsx:3352`).
Both offered paths mutate real state: `sign in + save` runs `claude auth login --claudeai` or `codex login` on the daemon host, and `save current login` captures an existing one (`frontend/src/ProviderAccounts.tsx:235`, `:242`).
The step has an `action`, so it has no Next button.

A user who has not installed a CLI, has not decided which provider to use, or simply wants to look before authenticating has exactly one way past step 5 of 14: Exit.

**Why it matters.**
It contradicts the app's own onboarding copy.
The first-run harness panel tells the user that CLI login is a *later* step, after creating a Project:

```
frontend/src/HarnessSetup.tsx:96
"Next, after this: create a Project for a folder, sign in to each agent CLI …, then start a session."
```

The tour then makes login mandatory before the Run step it promised would come next.
This is the paradox of the active user in its literal form: the user wants to get started, and the tour requires an account setup detour with no way through.

**Fix.**
Same as finding 1: a `Skip this step` control on every event-gated step, and the account step in particular should offer `I'll do this later`, which is a legitimate answer.
The spotlight and the gate can stay for the user who does want to do it now.

---

### 3. Two first-run surfaces render simultaneously, wrong one on top

**What happens.**
On a genuinely fresh install both first-run surfaces are live at once:

- `tutorialOpen` initialises synchronously from `localStorage` (`App.tsx:630`), so it is `true` on the first paint.
- `harnessSetupNeeded` becomes `true` after the config fetch resolves (`App.tsx:1636`).
- Both render, unordered (`App.tsx:7064` and `App.tsx:7096`).

Layering puts the harness dialog over the tour:

```
frontend/src/style.css:1287  .tutorial-layer{ … z-index:120 …}
frontend/src/style.css:1288  .tutorial-layer.centered{background:#030504d6;backdrop-filter:blur(7px)}
frontend/src/style.css:1304  .harness-setup-backdrop{ … z-index:140 … background:color-mix(in srgb,#000 62%,transparent)}
```

The very first screen of the product is therefore: the harness dialog, on top of a blurred-and-dimmed app, dimmed a second time, with an invisible tour card underneath it.
Closing the harness dialog reveals a tour the user was never told had started.

**Why it matters.**
This is the first frame of the product and it reads as two dialogs fighting.
Both surfaces are individually well-written; the sequencing is simply absent.

**Fix.**
Quick: suppress `tutorialOpen` while `harnessSetupNeeded` is true, and have `HarnessSetup`'s `onDone` / `onConfigureMore` decide whether the tour starts.
The `onConfigureMore` path should not start it at all, because that user just chose to configure manually.

---

### 4. With no harness enabled, Run silently drops every agent row

**What happens.**
The Run menu builds its agent rows from enabled harnesses only:

```
frontend/src/ProjectRunMenu.tsx:37   const harnesses=promptDeliveryHarnesses()
frontend/src/harnessRegistry.ts:305 promptDeliveryHarnesses = () => installed
  .filter(h => h.capabilities.pty_delivery && harnessEnabled(h.name))
```

With none enabled - the outcome of pressing `Skip` on the first-run panel, or of a machine where detection found nothing - the `NEW SESSION` section renders `Shell` and `Custom terminal…` and nothing else.
No statement, no reason, no link.

The single reason to install swe-mux is invisible, and the surface reads as "this build does not do that" rather than "this is off".

**Why it matters.**
It is precisely the invariant `design/features/setting-links.md` exists to enforce - "A gated surface never renders as merely empty" - applied to a surface the coverage table does not list.
The switch already exists as a deep-linkable control (`harness_enabled`, Settings → Harnesses), so the pattern is available.

**Fix and handoff.**
Add a `harnesses.enable` target to `frontend/src/settingTargets.ts` and a `.setting-gate` block in the `NEW SESSION` section when `harnesses.length === 0`.
This belongs to the **in-flight gated-feature in-context enablement session**; it is recorded here rather than acted on so that session can pick it up.

---

### 5. No help surface exists, and the one tour is one-shot and unsearchable

**What happens.**
There is no help command, no reference surface, and no documentation link anywhere in the frontend.
A search across `frontend/src` for `help.` commands, `Help` labels, and outbound documentation links returns nothing.
The single reference-shaped artefact in the product is the spoken-command catalog (`frontend/src/VoiceCommandsButton.tsx`, `voiceCommandReference.ts`), reachable only from a voice surface.

The guided tour, which is the entire orientation, is reachable exactly once and then only from one buried control:

```
frontend/src/Settings.tsx:1125
<div class="settings-tutorial-reset">…<button onClick={()=>requestClose('tutorial')}>Reset &amp; run tutorial</button></div>
```

It is not a registered command.
Every one of the 11 side-panel tabs generates palette commands and voice phrases (`design/features/ui.md`, drawer segment registry), and the one surface a lost user actually needs is the one surface that has neither.

Measured against the scale table: 106 commands, 206 config keys, 17 settings tabs, 58 settings sections, 11 tabs plus 17 segments, and no reference of any kind.

**Why it matters.**
This is the finding with the largest gap between current state and available solution, because the solution already exists in the sibling repository.
Continuity generates a read-only tutorial buffer from `.docs/design/features/*.md` intros plus its default keymap, opens it on first launch, keeps it re-openable via `help.tutorial`, and fails CI when it drifts from the docs.
swe-mux has 48 feature documents that already open with a "What it is" paragraph and a 106-entry command registry with human-readable descriptions and categories - the exact two inputs Continuity's generator consumes.

It is also what the published guidance points at: contextual, re-accessible, in-context help outperforms an upfront tutorial for complex applications.

**Fix.**

- Quick, and worth doing immediately regardless of the larger piece: register the tour as a command (`tutorial.start`) so it appears in the palette, is bindable, and gets a voice phrase. One entry in `KEYBINDING_COMMANDS` plus one command in `App.tsx`.
- **Needs design:** a generated reference surface. Feature-document intros plus the command registry plus the default keybindings, rendered as a read-only Markdown resource the workspace can already host (swe-mux opens Markdown in panes and in the side panel today), reachable by command and voice, drift-checked in CI the way Continuity's is.
- This is the single highest-leverage item in this report for a user past minute ten.

---

### 6. The assistant's off-state names a Settings tab that does not exist

**What happens.**

```
frontend/src/AssistantPanel.tsx:201
<p>The assistant is off. Enable it in Settings → Assistant to converse with the fleet.</p>
```

There is no Assistant tab.
`settingsTabs` has 17 entries and none is named Assistant (`frontend/src/settingsTabs.ts:17-35`), and `tabForSection` falls through unknown names to General (`settingsTabs.ts:85-89`).
The switch actually lives in Settings → **Voice**, as section 4 of 8:

```
frontend/src/Settings.tsx:1563  <h3>Mux assistant</h3>
frontend/src/Settings.tsx:1564  <label class="check" data-setting="assistant_enabled">…
```

The control carries a `data-setting` id, so it is already deep-linkable, but there is no `assistant.*` entry in `frontend/src/settingTargets.ts` and the panel uses plain prose rather than `SettingLink`.

**Why it matters.**
`setting-links.md` states the rule this breaks: "Naming a switch obliges linking to it. Prose that says 'turn on X in Y' without a control is the defect this feature exists to remove."
Here the prose does not merely lack a link, it names the wrong destination.

The Settings search does recover the user - the index is derived from the same JSX, so typing "assistant" finds the control - but that is a recovery path, not the stated one.

**Fix and handoff.**
Add an `assistant.enabled` target pointing at `section: 'Voice'`, `setting: 'assistant_enabled'`, and replace the prose with a `SettingLink`.
Relevant to the **in-flight assistant-panel-into-the-top-bar session**, which will re-render this off-state, and to the **gated-feature enablement session**, which owns the pattern.
Finding 10 argues the section should not stay inside Voice at all.

---

### 7. Tour copy describes a menu the app no longer has

**What happens.**
The `feature-menu` step tells the user what they are looking at:

```
frontend/src/GuidedTutorial.tsx:75
<p><strong>Utilities</strong> holds the viewers — History, notes, every running process,
 prompt templates, the fleet queue, usage and notifications. …</p>
```

The `Utilities` group no longer exists.
The viewers are flat rows now, and the comment sitting directly above them in `App.tsx` records the removal and its reasoning (`App.tsx:6955-6963`).
Processes, bandwidth, storage, and token spend were also consolidated into a single **Resources** row (`App.tsx:6969-6973`), so "every running process" and "usage" no longer name anything in that menu either.

The keyboard shortcuts in the same step are correct and verified (`ctrl+alt+p`, `ctrl+alt+t`, `ctrl+alt+arrowleft/right` in `src/swe_mux/keybindings.py:13-22`).

**Adjacent documentation drift**, worth one commit alongside: `design/features/ui.md` refers to the side panel as having fourteen tabs and a `Panels · N of 14` group (`ui.md:1708`, `ui.md:1713`) and to "the same twelve icons" (`ui.md:2088`, `ui.md:2095`).
`DRAWER_TABS` has 11 entries and the label is computed from `DRAWER_TABS.length` (`App.tsx:6929`), so the rendered string is already correct and only the document is stale.

**Why it matters.**
The step is a coach mark pointing at an open menu.
Describing an open menu incorrectly is worse than describing nothing, because the user assumes they have misread the screen.

**Fix.**
Quick: rewrite the paragraph to the menu's current two halves - the viewers, then the things you configure - and correct the four stale counts in `ui.md`.
Consider a cheap test that each surface the tour names by word (`Utilities`, `Resources`, `Notes`) resolves to something rendered.

---

### 8. The empty workspace stage is the biggest thing on screen and holds no control

**What happens.**

```
frontend/src/App.tsx:6687
<div class="stack-active empty-stage">
  <div class="hero-terminal" aria-hidden="true">&gt;_</div>
  <h1>Your Project workspace.</h1>
  <p>Run a terminal, or open a note, a file, or a preview to begin. Files and notes live in the side panel.</p>
</div>
```

It names four actions and offers none.
The only affordance actually attached to that region is a right-click menu (`App.tsx:6683` → `App.tsx:6901`, containing `New terminal` and `New terminal custom…`), which is invisible and unreachable on touch.

Compare the sidebar's own empty state, which gets this right - it *is* the button:

```
frontend/src/App.tsx:6438
<button data-tutorial="empty-project" class="empty-project-cta" onClick={()=>openProjectsManager()}>
  <strong>Create your first Project</strong><small>Open Projects to add a canonical folder.</small></button>
```

**Why it matters.**
An empty state is the cheapest progressive-disclosure moment a product gets: the screen is otherwise blank, so the one action that fills it can be shown without competing with anything.
Run is present in the top rail and the collapsed rail, so this is a matter of a few pixels of travel rather than a dead end - but the largest element on a new user's screen is inert, and it is inert on the phone too.

**Fix.**
Quick: put the same Run trigger inside the stage, plus one secondary control for opening a note.
Both commands already exist; this is placement, not new behaviour.

---

### 9. Run shows worktree and action-authoring machinery on day one

**What happens.**
Every open of the Run menu renders, unconditionally:

```
frontend/src/ProjectRunMenu.tsx:245  ISOLATED CHECKOUT → New worktree session…
frontend/src/ProjectRunMenu.tsx:259  No Project tasks found.
frontend/src/ProjectRunMenu.tsx:260  AUTHOR → New Project Action…  .swe-mux/actions.toml
```

For a first-time user, three of the sections in the app's primary launcher are for concepts they do not yet have: Git worktrees, imported repository tasks, and a TOML action manifest.
"No Project tasks found." is a bare empty state that never says what a Project task is or where one would come from.

**Why it matters.**
This is the launcher: it is opened on the first minute and on every minute after.
The app menu already solves exactly this shape with `MenuGroup`, which the Maintenance section uses (`App.tsx:6984`) precisely because those rows are rarely wanted.

**Fix.**
Needs a small design call, because Run's row inventory is a considered design (`ui.md` argues at length for its marks and its ordering).
The proposal: fold `ISOLATED CHECKOUT` and `AUTHOR` into one `MenuGroup`, and make the empty-tasks line state its own concept once ("Project tasks are imported from VS Code tasks, package scripts, or `.swe-mux/actions.toml`").
The `MenuGroup` flyout behaviour and the touch accordion fallback are already built.

---

### 10. The Voice tab is eight subsystems, one of which is not voice

**What happens.**
`Settings.tsx` renders the Voice tab as a single `<section>` with eight headings:

```
Read aloud (TTS) · Spoken summary · Storage and dictation · Mux assistant ·
Spoken command latency · Wake words and commands · Command reference · Mobile voice
```

Three problems compound:

- **`Mux assistant` is not voice.** It is a conversational fleet operator with its own model, budget, trust policy, round budget, and dialog memory (`Settings.tsx:1563-1571`). Its own panel points at a nonexistent Assistant tab because that is where a reader would expect it (finding 6).
- **`Storage and dictation` mixes an audio cache limit with the STT master switch** (`Settings.tsx:1553-1554`), so the switch that turns the microphone on is filed under storage.
- **`Spoken command latency` is a diagnostic instrument**, complete with a reset control (`Settings.tsx:1573-1575`), sitting among first-use settings. Diagnostics has its own tab.

`design/features/ui.md` states the rule this breaks in its own words: "**One tab names one subsystem.**"

**Why it matters.**
Voice is the tab a new user is most likely to open out of curiosity and least likely to leave with a working configuration, because it presents read-aloud, dictation, an LLM assistant, a latency instrument, a wake-word editor, a command reference, and phone network setup as one undifferentiated pass.

**Fix.**
Needs design, and it interacts with two in-flight items.

- Split `Mux assistant` into its own tab under the Agents group, next to Harnesses and Prompt queue. That also gives finding 6 a correct destination and gives the assistant-in-the-top-bar work a place to link to.
- Move `Spoken command latency` to Diagnostics.
- Rename `Storage and dictation` and lift the STT master switch to lead the dictation section, so the switch precedes its own settings.
- Phase 15's scheduled **global TTS master switch** lands in this tab; sequencing that work after the split avoids re-editing it.

---

### 11. On a phone, eleven features are eleven unlabelled glyphs

**What happens.**
`drawer_tab_display` defaults to `icon` (`src/swe_mux/config.py:548`).
A phone user opens the side panel and is shown eleven icons.
Their names live in `title` and `aria-label` attributes (`UtilityDrawer.tsx:566-567`) - a hover tooltip and a screen-reader name.
Touch raises neither.
Long-press opens the tab's own context menu (hide / choose panels), not its name.

The desktop is fine: the launcher rail and the tab strip both have hover, so the tooltip is one pointer-rest away.
The phone has no equivalent, and the phone is where a user is most likely to be exploring rather than working.

The setting is also not split by device class, unlike chrome scale, which is (`ui.md`, chrome-scale section).
A phone user who wants labels turns them on for the desktop drawer as well.

**Why it matters.**
Those eleven tabs are the product's entire feature index outside the app menu.
Recognition beats recall, and an icon with no reachable name supports neither.

**Fix.**
Quick, and there are two defensible versions:

- Default `drawer_tab_display` to `title` on the mobile layout, resolving through the same `(max-width:760px)` breakpoint the chrome-scale setting already uses; or
- split the setting by device class the way `ui_scale` is split, and default the mobile half to `title`.

The second is more work and more correct.
Either is a small, high-return change for the phone's first ten minutes.

---

### 12. Naming drift: one row calls the side panel a "utility drawer"

**What happens.**
User-facing text is consistently "side panel": every command label (`App.tsx:4892-4950`), every tooltip (`App.tsx:6923`, `:6932`, `:6942`), every announcement (`App.tsx:877`, `:961`), the mobile placeholder (`App.tsx:5839`), and Settings → Appearance → "Visible panels".

One row disagrees:

```
frontend/src/App.tsx:6950
<button role="menuitem" …>Collapse utility drawer</button>
```

**Fix.**
Quick: rename to `Collapse side panel`.
"Utility drawer" is the correct internal name and should stay in the code, the documents, and the module names; it should not reach a menu row.

---

## Quick polish, consolidated

Each of these is a bounded change with no design question outstanding.

1. Open the side panel for the tour's `resources` step on the mobile layout (`App.tsx` `navigateTutorial`). Finding 1.
2. Add a `Skip this step` control beside the hint on every action-gated tour step. Findings 1 and 2.
3. Suppress the tour while the first-run harness panel is up; start it from that panel's completion. Finding 3.
4. Rewrite the `feature-menu` tour paragraph to match the current app menu. Finding 7.
5. Correct the four stale side-panel tab counts in `design/features/ui.md`. Finding 7.
6. Register `tutorial.start` as a command so the tour is in the palette, bindable, and speakable. Finding 5.
7. Put a Run trigger inside the empty workspace stage. Finding 8.
8. Rename `Collapse utility drawer` to `Collapse side panel`. Finding 12.
9. Default side-panel tabs to titles on the mobile layout. Finding 11.
10. Add a `harnesses.enable` setting target and a gate notice in Run's `NEW SESSION` section. Finding 4, hand off.
11. Add an `assistant.enabled` setting target and replace the assistant panel's prose with a `SettingLink`. Finding 6, hand off.

## Needs design, consolidated

1. **A generated in-app reference** built from the 48 feature-document intros and the 106-entry command registry, opened as a read-only Markdown resource, reachable by command and voice, drift-checked in CI. Finding 5. This is the largest single improvement available and the pattern is proven in the sibling repository.
2. **Progressive disclosure in the Run menu**: advanced sections behind a `MenuGroup`, and an empty-tasks line that teaches its own concept. Finding 9.
3. **Splitting the Voice tab**, with `Mux assistant` promoted to its own tab under Agents and the latency instrument moved to Diagnostics. Finding 10. Sequence before Phase 15's global TTS master switch.
4. **Rethinking the tour's shape**, once findings 1 to 3 have made the existing one survivable. The published evidence is that a 14-step chained walkthrough on first login is the weakest form of onboarding available, and that behaviour-triggered contextual hints outperform it. swe-mux already owns the two mechanisms this would need: `settingReveal.ts` can scroll-and-flash any marked control, and `RailEditor.tsx:383` demonstrates the right shape of a dismissible first-open orientation callout in plain words. A version of the first run that teaches Project, Run, and the side panel in three contextual moments, and leaves everything else to the generated reference, would be shorter, harder to strand, and cheaper to keep accurate.

## What a great first ten minutes looks like

### Desktop

- **Minute 0.** One first-run surface, not two. The harness panel, alone, correctly sequenced. It already says the right things: what mux injects per session, that both injections are per-session and removable, and what the next steps are.
- **Minute 1.** Create a Project. The existing form is good: mode strip, prefilled folder name, the exact canonical root shown, setup commands collapsed and unchecked.
- **Minute 2.** Run. The launcher shows the agents you enabled and a Shell, and says so plainly if you enabled none. Worktrees and action authoring are one fold away, not in the first screenful.
- **Minutes 3 to 8.** Work. Contextual hints appear at most one at a time, at the moment they are needed - the first time a pane is split, the first time an agent finishes a turn off-screen.
- **Minute 9.** The user wonders what the eleven icons on the right are. They open the reference from the palette, read the side-panel section, and close it. Nothing was memorized, and nothing had to be.
- **Not in minute 10.** Provider login, unless they chose it. Automation opt-ins. The Voice tab. Worktrees. Action manifests. Themes.

### Phone

- **The arrival is already solved** and should not be re-litigated: `NEW_USER_RELEASE_READINESS.md` records real Tailscale connection state, cause-pointing next-step text, the phone DNS checklist, the QR and the Connect-a-phone modal, and the Windows firewall inspect and repair as shipped.
- **What is not solved is the first screen after arrival.** Today it is a re-run of the desktop tour, from `localStorage` and therefore per device, which strands at step 10 (finding 1).
- The phone should get either no tour or a genuinely mobile one: swipe for the panels, the Run button, the microphone, and where the tabs are. Four things, not fourteen.
- The side panel should open with readable tab names (finding 11), because a phone has no hover and this is the device where exploration actually happens.
- Everything else the phone needs is already there and is good: the mirrored edge toggles, the two-box quota, the Talk toggle's three honest states, the peek toggle for a keyboard-covered agent screen.

## Deliberately not recommended

Recorded so this report does not duplicate or contradict work already moving.

| Area | Why not recommended here |
|---|---|
| Assistant panel placement | An in-flight session is consolidating it into the top bar. Finding 6 is about the off-state's wording and link target, which that work will re-render and should absorb. |
| A general gated-feature in-context enablement pass | An in-flight session owns it. Findings 4 and 6 are handed to it as two specific instances the `setting-links.md` coverage table does not currently list. |
| Note-insert placement | An in-flight session owns it. |
| A global TTS master switch | Already scheduled in `ROADMAP.md` Phase 15. Finding 10 only asks that the Voice-tab split be sequenced before it. |
| Remote and phone-connection onboarding | Shipped per `NEW_USER_RELEASE_READINESS.md` and verified against the code. |
| First-use download gates, neutral defaults, prerequisite checklist, diagnostics export, `swemux doctor` | Shipped per `NEW_USER_RELEASE_READINESS.md` and `ROADMAP.md` Phase 7. |
| Reducing the feature surface | Not the finding. The surface is appropriate for a high-engagement professional tool. The problem is the first-use path across it, not its size. |

## Sources

- [Paradox of the Active User](https://www.nngroup.com/articles/paradox-of-the-active-user/) - Nielsen Norman Group, summarizing Carroll and Rosson's IBM User Interface Institute studies.
- [Onboarding Tutorials vs. Contextual Help](https://www.nngroup.com/articles/onboarding-tutorials/) - Nielsen Norman Group.
- [Instructional Overlays and Coach Marks for Mobile Apps](https://www.nngroup.com/articles/mobile-instructional-overlay/) - Nielsen Norman Group.
- [Feature Richness and User Engagement](https://www.nngroup.com/articles/feature-richness-and-user-engagement/) - Nielsen Norman Group.
- [10 Usability Heuristics Applied to Complex Applications](https://www.nngroup.com/articles/usability-heuristics-complex-applications/) - Nielsen Norman Group.
- Continuity design area, sibling repository: `D:\PROJECTS\continuity\.docs\design\principles.md`, `motion.md`, `defaults.md`, `features/tutorial.md`.

## Relates to

- `development/ROADMAP.md` Phase 15, "Global usability audit session" - the item this report closes.
- `development/NEW_USER_RELEASE_READINESS.md` - the fresh-machine plan whose shipped items this report deliberately does not restate.
- `design/features/setting-links.md` - the invariant findings 4 and 6 are instances of, and the coverage table they should join.
- `design/features/ui.md` - the browser-shell contract, including the "one tab names one subsystem" rule finding 10 cites and the stale tab counts finding 7 records.
