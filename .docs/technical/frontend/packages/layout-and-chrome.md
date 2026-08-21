# Frontend: layout, drawer, and chrome

Index: `../packages.md`.
Design: `../../../design/features/workspace-layout.md`, `../../../design/features/ui.md`.

## Layout algebra

`layout.ts` - parse, migrate, and the pure stack/split/leaf transforms.

## Mobile projection

`mobileWorkspace.ts` - pure flatten, select, and adjacent-close rules, with no persistence.

## Horizontal overflow rails

`RailScroller.tsx` (exporting `OverflowRail`), `railOverflow.ts`, `wheelScroll.ts`

Shared endpoint detection, overlay fade chevrons, tab-boundary paging, wheel translation, and separate selection/focus reveal triggers for workspace tabs, utility tabs, and the terminal Action rail.
Callers retain tablist semantics and drag targets.
Native touch and trackpad scrolling remains the default; keyboard-translated Action rails opt into explicit pointer scrolling plus IME-focus preservation, so the first focused drag cannot be lost to visual-viewport arbitration.
The pan begins on any child, with no button excluded: `railOverflow.ts` exports `RAIL_PAN_SLOP_PX` as the one travel threshold both the pan and `railKeyRepeat.ts` decide on, and the click a real drag suppresses is what settles whether the button it started on activated.

## Pinned rail and overflow popover

`RailStrip.tsx`, `RailOverflowPopover.tsx`, `railOverlayPlacement.ts`, plus `railFitCount` / `railOverlayBox` / `railPopoverClosingCommand` in `railOverflow.ts`

`RailStrip.tsx` is one Action rail row: it measures every chip, splits the row into what fits and a trailing `+N` chip, and hands the remainder to a popover scoped to that row.
It wraps `OverflowRail` rather than replacing it — a split row does not overflow, but that component still owns the touch-drag click suppression, `holdSoftKeyboard`, wheel translation, and focus reveal, and a pixel of measurement error wants somewhere to go.
The measurement is a **hidden twin**: a `visibility:hidden`, `pointer-events:none`, `width:max-content` copy of the full chip list, cloned (`cloneElement`) so one vnode never occupies two places in the tree.
The twin exists because the alternative — render everything for a frame, measure, collapse — flashes on every re-measure, and re-measures here are frequent: a chip's own label changes under it (End session arms to `Confirm ✓`, an auto-labelled prompt resolves its title).
It is a *sibling* of the scroller and not a child, because an absolutely-positioned child still counts toward `scrollWidth` and would report permanent overflow to the component drawing the edge chevrons.
`railFitCount` is the pure split, and its two edges are the ones a reviewer should check: a row that fits returns its full count and reserves nothing (so a fitting row is byte-identical to the pre-split rail), and once anything overflows the `+N` chip's width is spent before the first chip is placed (so the count can legitimately be zero).
The chip's width is fixed in CSS as `--rail-more-width` and read back from the same variable, so the reservation and the chip cannot disagree.
Only elements marked `data-rail-fixed` are reserved out of the budget - the gear, never the status readout, which shrinks and ellipsises instead so a transient `Copied` cannot move every chip beside it.
The `+N` chip is reserved separately by the split itself and lives *in* the trailing cluster rather than after the last pinned chip, which is what gives it one place on every row: the cluster is `flex:1 1 auto` with `justify-content:flex-end`, so it absorbs the slack the split leaves and everything in it sits on the rail's trailing edge.
`RailStrip` also owns the gear now, because the gear rides that cluster; `TerminalPane` passes `status` and `onConfigure` and owns only what pressing it does.

`railOverlayPlacement.ts` is the DOM half of placing **every** command-rail overlay - the popover and each drop-up - and exists for two soft-keyboard bugs that presented as one.
`railOverlayView()` reads the *visual* viewport, because `interactive-widget=resizes-visual` keeps the layout viewport at full height and every bound drawn against `innerHeight` therefore describes a rectangle running behind the keyboard.
`fixedContainingBlock()` walks for the nearest ancestor that establishes a containing block for fixed descendants - `.terminal-surface`, once `.soft-keyboard-open` transforms it - and `railOverlayCss()` subtracts its origin, so the numbers the pure `railOverlayBox` produces keep meaning screen coordinates whether or not the keyboard is up.
Portalling the overlays to the body would fix the same thing and take the rail's chip styling with them, which is why the block is measured instead.
`watchRailOverlayPlacement()` is the listener set both components share: `visualViewport` resize and scroll on top of window resize and capture-phase scroll, since the keyboard's open and close fire nothing else and the rail is itself a horizontal scroller.

`railOverlayBox` is the pure geometry, and the two rules worth knowing are both device-class rules: below 760px an overlay takes half the screen and goes to the screen's trailing edge whatever opened it, while above it keeps its trigger's edge and its own width cap (`RAIL_POPOVER_MAX_WIDTH_PX` for a wrap grid, `RAIL_DROPUP_MAX_WIDTH_PX` for a list).
It returns the panel's bottom *edge* rather than a CSS inset, which is what makes the containing-block conversion checkable rather than implied.

`RailOverflowPopover.tsx` is the panel, and it is deliberately not a `RailDropup`: a drop-up is a picker that closes on selection, this is the rail folded, and a rail does not close when you press a key on it.
Three consequences are behaviours rather than details, and all three are covered by `test/renderer/rail-overflow.spec.ts`:
a drop-up opened from a chip inside it is outside its DOM, so the pointer that opens one is exempted from outside-dismissal;
Escape reaches both listeners on `window`, where `stopPropagation` stops neither and this one is registered first, so the panel stands aside while any `.rail-dropup` exists;
and `railPopoverClosingCommand` folds the panel for the selections that navigate away (a drawer tab, a drawer section, the prompt library), listened for on the `mux:command` bus so a drop-up's sticky exit and a voice command fold it the same way a chip does.
The glass opacity is one CSS variable at `:root`, `--rail-glass`, shared with every drop-up, and it is measured rather than chosen: `test/railGlassContrast.test.ts` composites it over a white and a black terminal for every theme in the stylesheet and requires it both to cost no theme its 4.5:1 and to stay under 95%, so a later contrast "fix" cannot pass by going opaque.
The *single-layer* composition sets the number - a drop-up row is transparent over its panel where a popover chip has its own background - so testing only the chip would have let the drop-ups ship at an opacity that fails.

## The dropdown

`Dropdown.tsx`, `dropdownOptions.ts`, `dropdownPlacement.ts`

The app's one picker, and the only one any surface should reach for.
There is no `<select>` left in `frontend/src`; `frontend/test/settingsCoverage.test.ts`'s siblings and `test/renderer/dropdown.spec.ts` are what hold that.

It is custom on the phone as well as on the desktop, which is the part worth defending because the platform control is usually the right answer there.
Here it is not: a `<select>` on a phone is a system sheet or wheel that borrows none of the app's palette and covers the surface the choice is being made against, and having one implementation is what makes "the list opens where you left it" true on both at once rather than three times over.

Three behaviours are the reason the component exists rather than a stylesheet, and all three were reported against the account-settings model picker:

- **A scroll gesture scrolls.** Choosing happens on `click`, never on `pointerdown`, with `DROPDOWN_PRESS_SLOP_PX` behind it for the slow drag a browser still delivers a click for. A picker that commits on the press selects whatever the finger landed on, which is how the model list changed the model whenever it was scrolled.
- **It opens at the value in force**, centred, via `dropdownScrollTop(..., 'centre')`. On a long list that is the difference between a position in a catalogue and a list that happens to start here.
- **Order is the data's business.** The component renders `options` as given, so a list with a meaningful order (severity, recency, first-parent) keeps it; the model catalog is sorted A-Z where it is built, not here.

`dropdownOptions.ts` is the keyboard, written down because a native select shipped all of it for free and a custom listbox that loses any of it is a downgrade.
Arrows wrap at both ends and step over disabled rows; `Home` and `End` jump.
Type-ahead prefers a prefix over a substring across the whole list, cycles through equal matches, and reads one repeated letter as "the next one" rather than as a two-letter prefix.

`dropdownPlacement.ts` makes two decisions that let one component drop into twenty surfaces.
The list is portalled to `document.body` and positioned `fixed`, because a replacement rendered in place is clipped by every `overflow:auto` panel in the app - the Settings scroller, the drawer, a modal body, the Git map - where the native popup was clipped by nothing.
That also disposes of the transformed-ancestor trap `railOverlayPlacement.ts` documents: nothing between the body and the list is transformable, so `fixedContainingBlock` has no work to do here and is deliberately not called.
The other half of that module *is* reused - `railOverlayView()` for what "visible" means with a soft keyboard up, and `watchRailOverlayPlacement()` for the events that move an overlay out from under itself - because the keyboard bug is the same bug.
The list opens below and flips above only when below cannot hold it, measured against the visual viewport, so a control near the fold flips instead of unrolling behind the keys.

Three integration points are easy to break and are held by tests.
Every rule in `style.css` that styled a `select` names `.dropdown-trigger` beside it, so each surface's own width, height, and density still apply to the control that replaced it - a class beats the `button` rules those surfaces also carry.
`settingsSearch.ts` harvests the `options` prop, because the choices moved from `<option>` children into a prop and "Tokyo Night" would otherwise be text nowhere in the tree.
And `test/renderer/dropdown.ts` is the Playwright stand-in for `selectOption`: the rows are never inside the trigger's container, so one helper knows about the portal instead of twelve specs.

## Rail density

`railDensity.ts`

The per-device-class `Comfortable | Compact | Dense` choice, and nothing else.
Structurally a twin of `uiScale.ts`: two `Config` fields resolved through the same `(max-width:760px)` device-class breakpoint, a `watch…Profile` that re-resolves when a window crosses it, and a default that writes nothing at all.
The numbers live in `style.css` as one variable group per step (gap, chip height, chip padding, container padding, row height, overflow-chip width), because they are six lengths that have to move together and because the mobile group is a second set rather than a scaled desktop one — a phone's Comfortable chip is a 44px touch target, not a multiple of 27.
What crosses the boundary is one `data-rail-density` attribute on the root element; Comfortable removes it, so an opted-out device renders the stylesheet's own `:root` values and is indistinguishable from a build without the feature.

## Utility drawer

`drawerLayout.ts`, `drawerTransient.ts`, `drawerVisibility.ts`, `UtilityDrawer.tsx`, `drawerTabs.ts`,
`drawerNotes.ts`, `noteTabs.ts`, `sidebarResize.ts`, and feature-named tab bodies

- `drawerLayout.ts` owns the JSX-free device-local recursive tree and per-Project presentation algebra.
- `drawerTransient.ts` derives a non-serialized Project-bound display override for momentary rail navigation.
- `drawerVisibility.ts` is the single JSX-free answer to whether a tab is drawn.
  It holds structural availability and the user's global device-local hidden set apart, refuses to hide the last tab, and treats an explicitly named hidden tab as a peek.
  Every strip, the desktop launcher rail, and the Settings mirror read it rather than repeating the rule.
- `App.tsx` owns migration, atomic persistence, independent drawer-tab and right-rail display preferences, transient-state lifetime, and collapse routing.
- `UtilityDrawer.tsx` owns desktop recursion plus the flat mobile projection, tab long-press, and singleton body dispatch, delegating rail overflow mechanics to `OverflowRail`.
- `drawerTabs.ts` is the tab registry.
- `drawerNotes.ts` remembers the selected Notes sub-tab per Project.
- `noteTabs.ts` owns deterministic tab ordering, deletion fallback, and the per-Project note count behind the last-note delete guard.
- Notes alone keeps one hidden inactive workspace, and Processes consumes App's fleet snapshot without another poll.

## Drawer segments and sections

`drawerSegments.ts`, `DrawerSegmentControl.tsx`

The drawer's second axis: what a tab is *showing*, once a tab shows more than one thing.
Two kinds are deliberately kept apart.
A **segment** is a mutually exclusive view (Activity, Agent, Git).
A **section** is a co-visible region of one scroller reached by scroll-and-flash (Actions), which is what let a high-frequency insert surface like Clipboard be folded in at all.

`drawerSegments.ts` stays JSX-free and unit-testable like `drawerTabs.ts`, so availability is a predicate over a small context of booleans (`hasTranscript`, `isAgentSession`) rather than over a `Session`.
`resolveDrawerSegment` falls back to the first available segment.

`DrawerSegmentControl.tsx` is the single control every segmented tab draws, in the same place, from the same registry.
Unavailable segments are **omitted rather than disabled**, because a greyed-out "Timeline" promises a surface that does not exist, and a tab with one available segment draws no control at all.
It paints from one shared `.segmented-tabs` rule that the Resources dialog and the Automation dashboard also use, so "these are views of this surface" reads as the same statement everywhere.

The registry exists so `App.tsx` can generate a palette command and a voice phrase per segment and per section: without it, folding a tab into a segment would delete the surface's command *and* its spoken navigation.
`RETIRED_DRAWER_SEGMENTS` is the same obligation in reverse, and is a row rather than a deletion.
A retired id names the live segment that absorbed it, `App.tsx` generates its command and phrases from that list too so "open Land" keeps answering, `migratedDrawerSegment` moves a *stored* selection onto it rather than letting `resolveDrawerSegment` fall through to the tab's first segment, and `keybindings.py` migrates the command id - where an unmigrated one is rejected rather than ignored.
Section targets reuse `settingReveal.ts` under a `drawer.<tab>.<id>` namespace rather than growing a parallel mechanism.

## Appearance

`theme.ts`, `ThemePicker.tsx`, `uiScale.ts`, `style.css`

Pure config to root custom properties.

- `theme.ts` owns the selectable catalog, xterm palette tokens, and preview-color projection.
- `ThemePicker.tsx` owns the keyboard-accessible fixed-column swatch listbox.
  It is the design `Dropdown` is modelled on and stays its own component: every row carries a swatch strip, and highlighting one *applies* the theme without committing it, which is a preview contract no generic picker has.
- `uiScale.ts` owns `--ui-scale`, per-device-class resolution, discrete step movement, fixed keyboard classification, and high-resolution wheel accumulation.
- `style.css` owns shared theme-derived chrome, including compact scrollbars and the one `--check-size` rule that sizes every native checkbox and radio.
  That rule is fixed px, not `--ui-scale`, because that property multiplies type and the rows holding a line of type, never glyph-sized controls.
  A container rule shaped `<panel> input { width/height … }` must exclude `[type=checkbox]`, or it stretches the ticks inside that panel into full-width text-field boxes.
- `App.tsx` is the one scale controller: it captures fixed scale inputs before browser and xterm handling, updates chrome and the numeric xterm prop together, reports through `InteractionHud`, and debounces persistence outside the Settings draft.

Neither theme nor scale code reads rendered styles back.
The stylesheet is the only consumer of the *property* but not the only consumer of the *value*: `applyUiScale` returns the resolved step and `watchUiScaleProfile` reports it on a device-class flip, because xterm owns its font and has to be handed a number (`scaledFontSize`) rather than inheriting a custom property.

## Icon sets

`railIcons.tsx`, `harnessIcons.tsx`, `noteRailIcons.ts`

The app's stroke marks, kept in shared modules so one concept is one drawing wherever it appears.

- `railIcons.tsx` holds the Action rail marks, the `DRAWER_TAB_ICONS` map every drawer tab must appear in, and the menu marks the app menu, the sidebar menu, and the Project and session context menus read - including their `MenuGroup` headers.
  A mark is added here rather than inline so a concept drawn in more than one menu (History, Groups, Settings, Prompts) stays one drawing.
  That is also why a redrawn tab mark replaces its component rather than adding one beside it: `CommandKeyIcon`, `NotePencilIcon`, and `QueueClockIcon` are read by both the drawer map and the menu rows, so the two cannot drift.
  Sizes come from CSS rather than `1em`, because these surfaces run a 9-10 px font that would render an em-sized icon unreadably small.
  The set's one live constraint is that Activity and Processes are both traces on the same rail, and are kept apart by silhouette (a bare squiggle against a framed monitor on a stand) rather than by any detail that survives 34px and not 17px.
- `harnessIcons.tsx` is the per-harness mark (`harnessMark`), read by the sidebar row's provider prefix, the session tab strip, the account switcher, and the Run menu's launch rows.
  A harness with no drawing falls back to its initial, which is all a browser can honestly say about a harness its daemon added and it does not know.
  The single source is load-bearing rather than tidy: a per-surface copy that knows only the harnesses with provider accounts draws `oh-my-pi` and `opencode` identically, on the surfaces whose job is telling two panes apart.
- `noteRailIcons.ts` builds its shapes imperatively, because the note editor's rail is outside the Preact tree.
