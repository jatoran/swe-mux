# Frontend: Settings, setting links, grants, budgets, and models

Index: `../packages.md`.
Design: `../../../design/features/setting-links.md`, `../../../design/features/budgets.md`, `../../../design/features/ui.md`.

## Settings

`Settings.tsx`, `settingsTabs.ts`, `settingsDraft.ts`, `settingsSearch.ts`, `fuzzyText.ts`,
`HarnessSetup.tsx`, `WslBridgePanel.tsx`, `wslBridge.ts`

Global options only, including the machine-wide worktree root.
Per-Project options belong to `ProjectsManager.tsx`.
The lifecycle is an explicit draft, save, and discard.

### Search and ranking

Search indexes the vnode tree of every tab: `Settings.tsx` renders each tab through one id-taking function so unmounted tabs can be built without effects, which keeps the index self-maintaining rather than a second list to update.
Ranking is not local to it.
`fuzzyText.ts` owns the ladder - exact name, name prefix, word in name, substring, secondary text, then a span-bounded subsequence pass - and the sidebar filter scores by the same one, so a name typed in either box resolves the same way.

### `settingsTabs.ts` - the navigation model

Browser-free: the tabs and their four contiguous groups, deep-link name resolution, legacy id migration, the remembered tab and per-tab remembered section, and the section-rail id numbering.
It is split out of the component because none of it needs a renderer, and the rules that decide *where a setting is* are worth asserting without mounting a panel of this size.
A group is a run of the array rather than a declared membership, and `tabForSection` matches a tab's own label before consulting its alias table, so neither can drift the way a hand-maintained heading-to-tab map does.

### Section rails

The in-tab section rail is derived from the `<h3>`s a tab rendered, watched by a `MutationObserver` so child panels that paint after their fetch still appear.
It is scroll anchors rather than sub-tabs precisely so nothing unmounts, which is what keeps the search index, `Ctrl`+`F`, and the single Save transaction whole.
A long tab draws one `<section>` per `<h3>` rather than one section holding all of them - the rail is satisfied either way, but the borders between concerns come from the boxes.
A section whose body is *reference* rather than a control folds that body behind a `<details class="settings-disclosure">` with the heading left outside, so the rail entry, the index, and the scroll-spy all survive the fold.
Settings → Voice is the worked case, pinned by `test/renderer/voice-settings.spec.ts`.

### Harnesses and the WSL bridge

Settings → Harnesses is the per-harness surface: enable toggle, detected path, executable, default args, width envelope, and the native-history reconcile and scan controls.
It reads detection off the `/api/harnesses` payload and edits `harness_enabled`.
`HarnessSetup.tsx` is the once-only first-run panel, gated daemon-side by `harness_setup_complete`.
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

`settingTargets.ts` is the browser-free catalogue: which of the two switch-owning overlays a target lives in, the Settings section, and the `data-setting` id of the control.
The two overlays are Settings, for everything install-wide including the global automation switches, and the Projects registry, for per-Project opt-ins.
The Automation dashboard owns no switch and is a link source only.

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
`GrantGate.tsx` is the block a surface that cannot function draws - statement, consequence, one row per scope naming where the change is written, the dependency closure it drags in, whether it can cost money, then one button.
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
Its `readiness.reason` is the daemon's own sentence and is rendered verbatim wherever a model-backed switch goes inert - the Projects registry, and any `GrantGate` over a `needs_llm` switch, which discloses it beside `spends` and links to Settings → Accounts rather than offering a button.
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

`ModelPicker.tsx` renders name, then id and price on one meta line.
Its `required` flag suppresses the clear-the-setting row for a **pinned** model the daemon rejects when blank.
`ProjectPicker.tsx` borrows the `model-picker` CSS block, so a rule added for the meta line must be scoped to `.model-picker-meta`.

`modelRouting.ts` is the browser-free table of which feature calls which model, the routed/override/pinned distinction that decides what a blank value means, one-level fallback resolution, and `customProviderOverride` - because a custom endpoint serves one model and invalidates every row at once, so the index reports what will actually be requested rather than ids nothing will ask for.
The summary is an index, never a second editor: two controls writing one config key is how a panel starts disagreeing with itself.

## Guided onboarding

`GuidedTutorial.tsx`, `tutorial.ts` - action gates, coach-mark geometry, product-event matching, and device-local completion.
