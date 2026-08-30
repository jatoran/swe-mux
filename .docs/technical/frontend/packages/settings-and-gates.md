# Frontend: Settings, setting links, grants, budgets, and models

Index: `../packages.md`.
Design: `../../../design/features/setting-links.md`, `../../../design/features/budgets.md`, `../../../design/features/ui.md`.

## Settings

`Settings.tsx`, `settingsTabs.ts`, `settingsDraft.ts`, `settingsSave.ts`, `settingsSearch.ts`,
`fuzzyText.ts`, `AutomationPolicyView.tsx`, `HarnessSetup.tsx`, `WslBridgePanel.tsx`, `wslBridge.ts`

Global non-automation options only, including the machine-wide worktree root.
Global and per-Project automation policy belongs to the Automation workspace.
Other per-Project options belong to `ProjectsManager.tsx`.
The lifecycle is an explicit draft, save, and discard.

### One save, one request, one apply

Save posts the config delta and the keybindings map together to `POST /api/settings/apply` (`design/interfaces.md`), which commits both or neither.
The pair of requests it replaced could half-succeed, and the panel's single catch reported every failure as "invalid · nothing was changed" - a claim about the daemon's disk.
`settingsSave.ts` is where that message is now derived rather than assumed: it is browser-free and reads the `committed` array off the error body, so the three outcomes read differently, and a request that never came back says the outcome is unknown instead of guessing in the reassuring direction.
That module is separate from the panel for the usual reason - the rule is worth asserting without mounting `Settings.tsx`.

`adoptConfig` is the one place a config becomes authoritative in this panel.
The open, save, and restore-defaults paths each spelled the same chain out, and had already drifted: open applied the theme and skipped the note-editor, chrome-scale, and rail-density applications, so a value written from another device reached the draft and never the document.
The three device previews are idempotent (`App.applyConfig` re-applies the same set on every daemon `configuration_changed`), which is what makes one function safe for all three callers.

Restore defaults raises a confirmation before it writes: it rewrites the whole saved configuration on one click, is not staged behind Save, and cannot be undone.
The dialog is its own dismiss level (`settings-reset-confirm`), so back and Escape reach it before the panel, and it is registered in an effect - a renderer spec must wait for focus to land inside it before pressing Escape, or the key reaches the panel's level instead.
`test/renderer/settings-save.spec.ts` covers both paths against a harness that can be told to refuse (`?fail=reset`, `?fail=apply`) and records every request the panel made.

### Search and ranking

Search indexes the vnode tree of every tab: `Settings.tsx` renders each tab through one id-taking function so unmounted tabs can be built without effects, which keeps the index self-maintaining rather than a second list to update.
Ranking is not local to it.
`fuzzyText.ts` owns the ladder - exact name, name prefix, word in name, substring, secondary text, then a span-bounded subsequence pass - and the sidebar filter scores by the same one, so a name typed in either box resolves the same way.

An entry records its enclosing headings as a `path` (`<h3>` at level 1, `<h4>` at level 2) rather than as one nearest-heading string.
A single slot is claimed by whichever heading rendered last, which is why every keyboard-shortcut row used to place itself as "Input · view" - its category - having overwritten the section it belongs to.
Two rules make the path hold, and each fixes a defect the other does not.
Levels are positional, so opening one closes every deeper one and a new `<h3>` cannot inherit the previous block's `<h4>`.
And a heading is closed by the end of its `<section>`, which is what stops a group's `<h4>` from following the walk out and claiming the block's later controls - the "Reserved shortcut policy" disclosure filed itself under the last category rendered above it.
`<strong>` emits an entry without claiming a level: it marks a labelled block inside a section rather than opening one.
`section` is the joined path, and is what result identity de-duplicates on, so two same-named controls in different groups are two results.

`settingsBreadcrumb` (in `settingsTabs.ts`, not here) turns a path into the line a result row shows.
It lives with the page mapping because that is the fact it needs: a page is named only when a heading does not already name it, so Input's pages - which *are* its `<h3>`s - are not said twice, while Voice's several-headings-per-page grouping is.
The index itself stays free of the tab registry and knows nothing about which tabs paginate.

### `settingsTabs.ts` - the navigation model

Browser-free: the tabs and their four contiguous groups, declared subpages, deep-link name resolution, legacy id migration, and the remembered tab and per-tab remembered page.
It is split out of the component because none of it needs a renderer, and the rules that decide *where a setting is* are worth asserting without mounting a panel of this size.
A group is a run of the array rather than a declared membership, and `tabForSection` matches a tab's own label before consulting its alias table, so neither can drift the way a hand-maintained heading-to-tab map does.

### Separate pages and the sidebar as sole navigation

`settingsSubpages` declares the pages of the genuinely long tabs (Accounts, Prompt queue, Input, Voice) before those tabs mount; every other tab is one scrolling column.
`settingsSubpageId` maps related implementation headings to the user-facing page that owns them.
The sidebar is the only in-tab navigation: the desktop column and the mobile slide-in drawer both list a paged tab's pages below it, and the *active* unpaged tab's rendered sections as scroll anchors (from `SECTION_RAIL_MIN` sections, scroll-spy highlighted).
The content pane carries no duplicate row of page links.
Arriving on a tab by any route expands its links; only the chevron collapses them, and never automatically.
Clicking the tab navigates to its remembered page.
Only the selected page is visible, but search and deep links select its owner before calling `revealSetting`, so hidden pages remain addressable.
Reference bodies may still fold inside a page behind `<details class="settings-disclosure">`.
Settings → Voice is the worked case, pinned by `test/renderer/voice-settings.spec.ts`; the sidebar and page behavior is pinned by `test/renderer/settings-layout.spec.ts`.

### Harnesses and the WSL bridge

Settings → Harnesses is the per-harness surface: enable toggle, detected path, executable, default args, width envelope, and the native-history reconcile and scan controls.
It reads detection off the `/api/harnesses` payload and edits `harness_enabled`.
`HarnessSetup.tsx` is the once-only first-run panel, gated daemon-side by `harness_setup_complete`.
It leads the first run and the guided tour waits behind it; `firstRunSurface` in `tutorial.ts` owns that ordering for both, so neither render site decides it alone.
The enablement filter itself lives in `harnessRegistry.ts` (`setHarnessEnablement`, `harnessEnabled`, `allBackendNames`, `allHarnessesIncludingDisabled`) and is a launcher filter only, never touching display, transcript, or history surfaces.

`WslBridgePanel.tsx` (decisions in `wslBridge.ts`) is the WSL agent bridge setup surface, shaped like the firewall panel beside it because it solves the same shape of problem: read status, state the blocker in a sentence, offer exactly the action that clears it.
It exists because the bridge fails *silently* - an agent inside a distribution that cannot reach the daemon starts and answers perfectly while mux sees none of it - so the panel must go looking rather than wait to be told.
`wslBridge.ts` owns the blocker ordering, and the order is the advice: the firewall is named before the install, because an install that could never phone home fixes nothing.
Probing is a button, not an effect, because inspecting a distribution starts it.

## Setting links and grant gates

`settingTargets.ts`, `settingReveal.ts`, `SettingLink.tsx`, `grants.ts`, `GrantGate.tsx`,
`projectAutomations.ts`, `installSwitches.ts`, `llmProvider.ts`

The path from a surface that cannot work to the switch that would make it work.

### Links

`settingTargets.ts` is the browser-free catalogue: which owning surface a target lives in, the Settings section when relevant, and the `data-setting` id of the control.
The destinations are Settings for ordinary install configuration, Automation for global and per-Project control-plane policy, and the Projects registry for other Project policy.
`App.openSettingTarget` converts Automation targets into the correct workspace view, Project overlay, and reveal id.

`settingReveal.ts` is the arrival.
It opens any `<details>` above the control, then waits for the control to exist *and* to have a layout box, because both destinations render theirs behind a fetch and some behind two.
It centres the control rather than using `block:'nearest'`, which would park it under the sticky header both panels carry inside their scroller.
It then flashes it with the class the settings search's own jump uses and focuses it - except a text field on a coarse pointer, where the keyboard would cover what the link was pointing at.

`SettingLink.tsx` is the single link control.
It dispatches `mux:open-setting` rather than taking a navigation prop, because these links live at the bottom of surfaces several components deep and threading a callback through each of them is how partial coverage happens.
`App.tsx` owns the routing (`openSettingTarget`), since opening one overlay means closing whichever other is up.

### Grants

A link alone is not enough: following one opens an overlay and leaves the reader to find the control, flip it, save a staged draft, close, reopen the drawer and reselect the tab - twice for the scan timeline, whose Project permission is a second gate behind the install switch.

`grants.ts` is the catalogue of which targets a surface may *switch on* in place, keyed by the same ids so one gate renders both the button and the owner's link, with the switch's key **derived** from the target (`grantKey`) rather than restated.
`GrantGate.tsx` is the block a surface with a compound dependency draws - statement, consequence, one row per scope naming where the change is written, the dependency closure it drags in, whether it can cost money, then one button.
`CompactGrantFlag` is the smaller one-switch shape used by Alerts, Clipboard, Queue, Schedule, and Read aloud.
It keeps the disabled state, immediate consequence, enable action, and Details link in one status row.
`GrantButton` is its inline form for prose on a working surface that names a switch in passing.
A gate renders only while the surface is inert and disappears when it is not, which keeps a Project-wide control from becoming a standing fixture in a session-scoped pane.

Three properties carry the safety argument for a write reachable from a drawer pane, and the daemon enforces each independently (`src/swe_mux/grants.py`):

- A grant is **additive only**, so withdrawal stays with the owning editor and "one owner per switch" survives having many granters.
- The keys are **allowlisted** closed sets, validated against `Config` and `PROJECT_CONFIG_FIELDS` at import.
- A mixed-scope grant is **one request**, because sequencing an install write and a Project write from the browser would mean two revisions and a half-granted state whenever the second lost.

### Reading switch state

`projectAutomations.ts` is the cached per-Project opt-in read that lets a consumer surface tell *off* from *quiet*, dropped on the daemon's `project_configuration_changed` rather than on a timer.
`installSwitches.ts` is the install counterpart - same cache, same three-valued answer, because rendering an unreadable switch as "off" is the same lie one layer down.

`llmProvider.ts` is the third read of that shape, for the switch that is a **value** and therefore never grantable: which model endpoint the install talks to and whether it is proven.
Its `readiness.reason` is the daemon's own sentence and is rendered verbatim wherever a model-backed switch goes inert - Automation Project policy, and any `GrantGate` over a `needs_llm` switch, which discloses it beside `spends` and links to Settings → Accounts rather than offering a button.
Four not-ready states (`no_key`, `no_endpoint`/`no_model`, `unverified`, `endpoint_changed`) need four different next actions, so a surface that paraphrased into "not configured" would misdirect three times out of four.

## Budget controls

`BudgetControl.tsx`, with `Budget`/`BudgetMode`/`BudgetVerdict` in `types.ts`

One control for every spending cap, wherever the setting lives, the same way `ModelPicker` is one control for every model setting.
Contract: `../../../design/features/budgets.md`.

- It draws the mode first, because the mode decides whether the two figures under it mean anything.
- An axis the mode does not enforce stays editable and is labelled unenforced rather than disabled or cleared, so trying the other unit is reversible.
- Switching into a mode seeds any axis it starts enforcing from that control's own ceiling, because a mode change that left the newly enforced axis empty would save a budget the daemon rejects, naming a field the operator never saw.
- The cost-blind warning is drawn on a strict `reportsCost === false`: `undefined` means an older daemon did not say, and absent knowledge must not render as an accusation against a working endpoint.
- `data-setting` comes from the `name` prop, so `settingTargets.test.ts` matches the `<BudgetControl name="…">` call site as well as a literal mark.

## Model selection

`ModelPicker.tsx`, `modelFilter.ts`, `modelPricing.ts`, `modelRouting.ts`, `ModelRoutingSummary.tsx`

One control for every OpenRouter model setting, wherever the setting lives.

`modelFilter.ts` owns the catalog option shape and the rank ladder over id and name.
Its price and capacity fields are optional, because a synthesized placeholder for a configured id the catalog no longer knows has neither.

`modelPricing.ts` owns the per-token to per-**million** conversion and the four values that are not prices and must never render as one: absent renders as nothing rather than `$0.00`, a wholly zero pair as `free`, negative as `variable`, and below the last printable digit as `<$0.001`.
Nothing fetches: `OpenRouterClient.models()` already caches `prompt_price`/`completion_price`/`context_length` and `GET /api/automation/provider` serves them verbatim, so pricing is a typing and rendering concern only.
`modelCachingLabel` reads the catalog's `cache_read_price`/`cache_write_price` for the same reason a provider list is not used: the list goes stale the week a new provider appears and the pricing does not.
Its three answers are distinct - no read price is "this model does not cache" (so a 0% hit rate on it is correct rather than a fault), a write price above input is a premium that only pays when the prefix is read back, and no write price beside a read price is a free write.

`ModelPicker.tsx` renders name, then id and price on one meta line.
Its `required` flag suppresses the clear-the-setting row for a **pinned** model the daemon rejects when blank.
The `model-picker` CSS block is written so a list with no second cell can borrow it, which is why a rule added for the meta line must be scoped to `.model-picker-meta`.
There used to be a second borrower, `ProjectPicker.tsx`; it is gone, and the registry's Project picker is now `Dropdown` with `filter`, so the app has exactly one filtering picker for lists it does own.

It stays a filtering combobox rather than becoming a `Dropdown`, because the catalog is hundreds of entries and a list is not how anyone finds one of those.
It borrows three rules from that component all the same, and they are the three defects reported against it.
Choosing happens on `click` with `DROPDOWN_PRESS_SLOP_PX` behind it, so a scroll gesture that starts on a row scrolls instead of selecting.
`dropdownScrollTop(..., 'centre')` runs on open, so the list arrives at the model in force.
And `limit` no longer bounds the unfiltered list, so the configured model is present for that scroll to find.
The catalog's *order* is the daemon's, not this control's - `sorted_model_catalog` in `automation_store.py` sorts it A-Z as it leaves the cache, so an install fixed by that change never has to press Refresh.

`modelRouting.ts` is the browser-free table of which feature calls which model, the routed/override/pinned distinction that decides what a blank value means, one-level fallback resolution, and `customProviderOverride` - because a custom endpoint serves one model and invalidates every row at once, so the index reports what will actually be requested rather than ids nothing will ask for.
The summary is an index, never a second editor: two controls writing one config key is how a panel starts disagreeing with itself.

## Guided onboarding

`GuidedTutorial.tsx`, `tutorial.ts` - action gates, coach-mark geometry, product-event matching, and device-local completion.

Three of `tutorial.ts`'s exports are pure decisions that `App.tsx` reads rather than restates, which is what makes them testable without mounting the shell (`test/tutorial.test.ts`).
`mobileTutorialChrome(step)` is the single table of which collapsed panel a step's anchor sits behind on a phone; the notes step is behind the **side panel** and every other gated one behind the navigation sidebar, and confusing the two is what stranded the tour at step 10 of 14.
`firstRunSurface(state)` returns the one first-run surface that may render - `'harness' | 'tutorial' | 'none'` - so the harness panel and the tour cannot both be live, which they were on every fresh install until 2026-08-28.
`TutorialStepId` moved here from `GuidedTutorial.tsx` (which re-exports it) so those two can be plain functions in a JSX-free module.

Every action step renders a skip beside its hint.
That is not a convenience: a gate the user cannot satisfy - a hidden Notes tab, an uninstalled provider CLI - otherwise leaves abandoning the tour as the only way past it.
`test/renderer/tutorial-steps.spec.ts` walks the whole tour at a phone and a desktop viewport over a page carrying **none** of its anchors, which is every step's worst case at once, and asserts it still reaches Finish.

The tour's second-to-last step is a hand-off rather than a lesson: it anchors on the sidebar footer's configurator control and says what pressing it does.
Two minutes cannot explain an install, and ending on the one surface that can answer the questions the tour did not cover is worth more than one more feature tour stop.

The Git Map's per-Project `.swe-mux` setup callout is not part of this tutorial.
Its completion and global suppression are daemon configuration because a repository decision made on one device must not reappear on another.
Settings -> Git owns the global switch and decision reset.

## Configurator launcher

`configurator.ts` - the launcher's presentation logic, and deliberately almost nothing else.
The daemon resolves which harness, which Project, and what the opening prompt says, because each answer depends on facts the browser does not hold (live CLI detection, whether this install has a source checkout, the current health report).

What is left here is genuinely presentational and is unit-tested for it:

- `launchState` decides whether the control is pressable and what it says. A missing agent CLI and a missing Project are **separate blockers with separate sentences** - one is a prerequisite to install, the other a step to take in the app, and the wrong message sends the operator somewhere that cannot help.
- A `null` options payload (unanswered or failed) is **pressable with a neutral label**. A launcher that greys itself out because a status request is in flight reads as broken, and the daemon refuses cleanly on the press.
- `opensChooser` puts the harness menu on right-click / shift / alt, and only when more than one agent is available: a plain press launching the default is the whole value of the control, and a menu offering one row is worse than no menu.
- `launchBody` omits an unnamed harness rather than sending it blank, so the daemon re-resolves against live detection.

The launch itself reuses `spawnTerminal` in `App.tsx` with only the route swapped.
The optimistic pane, the focus, and the layout write are identical for every launch, and a second placement path is exactly the thing that drifts.
It does **not** record the harness as `mux.lastBackend`: the operator picked a conversation about swe-mux, not a launch preference.

Design: `../../../design/features/configurator.md`.
