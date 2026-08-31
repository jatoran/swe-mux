# Frontend: layout, drawer, and chrome

Index: `../packages.md`.
Design: `../../../design/features/workspace-layout.md`, `../../../design/features/ui.md`.

## Layout algebra

`layout.ts` - parse, migrate, and the pure stack/split/leaf transforms.

## Mobile projection

`mobileWorkspace.ts` - pure flatten, select, and adjacent-close rules, with no persistence.

## Horizontal overflow rails

`RailScroller.tsx` (exporting `OverflowRail`), `railOverflow.ts`, `wheelScroll.ts`, `PaneRunTrigger.tsx`

Shared endpoint detection, passive arrow glows, wheel translation, and separate selection/focus reveal triggers for workspace tabs, utility tabs, and the terminal Action rail.
Callers retain tablist semantics and drag targets.
Native touch and trackpad scrolling remains the default; keyboard-translated Action rails opt into explicit pointer scrolling plus IME-focus preservation, so the first focused drag cannot be lost to visual-viewport arbitration.
The pan begins on any child, with no button excluded: `railOverflow.ts` exports `RAIL_PAN_SLOP_PX` as the one travel threshold both the pan and `railKeyRepeat.ts` decide on, and the click a real drag suppresses is what settles whether the button it started on activated.
Edge glows are `aria-hidden` spans with `pointer-events:none`; they indicate hidden content but never page or capture input.
Desktop workspace rails append `PaneRunTrigger.tsx` after the ordered tab shells.
The trigger opens the shared Project Run menu and receives the drag preview's terminal order, so it cannot move between tabs while a reorder is previewed.
App assigns each pane trigger a stable `pane:<stack-id>` identity and focuses the pane's active view before opening the menu.

## Pinned rail and overflow popover

`RailStrip.tsx`, `RailOverflowPopover.tsx`, `railOverlayPlacement.ts`, `railClearance.ts`, plus `railOverlayBox` / `railPopoverClosingCommand` in `railOverflow.ts`

`RailStrip.tsx` is one Action rail row.
Every configured chip remains in `OverflowRail`; no measurement twin or fit split exists.
A fixed-width drawer control lives in trailing furniture outside the scroller on every row, including empty and non-overflowing rows.
The drawer's small count is `chips.length`, so it describes the complete row and does not change with viewport width.
Opening it clones the complete chip list into `RailOverflowPopover` as a wrapping grid.
The same trailing cluster anchors the panel, giving all rows one trailing-edge placement.
Command-rail glow wedges are positioned relative to each row's `OverflowRail` wrapper, which is the scroller's own box: `.terminal-action-rows>.rail-row>.overflow-rail` is `position:relative` with `overflow:visible`, so the wedge sits on the strip's edge while its glow still bleeds past it.
Positioned from `.rail-row` instead, the right wedge had to name the trailing cluster's width in a `calc`, and could only ever be right for one cluster width, so on any row whose cluster was wider the wedge was drawn past its own strip, over furniture that answers to no tap.
`RailStrip` takes no message prop at all, which is why every row's cluster is now the same width and the wedges align: the trailing cluster is `flex:0 0 auto`, so anything living beside the drawer control takes its width straight out of the scrolling strip, and the selection readout that used to sit there was capped in `vw` - it swallowed a pane narrower than the cap whole.
Terminal messages go to `.terminal-clip-toast` over the buffer instead, where the cap is a percentage of the pane.

`railClearance.ts` keeps app-level floating messages off that rail.
`.interaction-hud`, `.notification-toast`, and `.toast-stack` are pinned to the viewport's bottom-right corner, which on a maximised window is precisely where the rail is; the latter two take pointer events, so the overlap stole taps rather than only obscuring chips.
Each `TerminalPane` registers its rail element, a single `ResizeObserver` (watching every registered rail plus `document.body`, since splitting a pane moves a sibling's rail without resizing it) recomputes on a coalesced frame, and `railClearancePx` publishes `--rail-clearance` on the root element for the stylesheet to add to each message's `bottom`.
Only a rail whose bottom edge reaches the viewport's counts, within two pixels of subpixel slack: the upper pane of a top/bottom split has a rail nowhere near that corner, and lifting a toast by its height would strand the toast in mid-air.
With several qualifying rails the tallest wins, which over-lifts a message sitting over a shorter one rather than leaving it covered.
The number cannot be a constant - rail height is the configured row count times `--rail-row-h`, one of three density steps with a separate set of mobile values - which is the whole reason it is measured.
`test/railClearance.test.ts` pins the arithmetic and holds the list of bottom-anchored selectors, so a new floating message that forgets the variable fails there rather than in someone's split pane.

`railOverlayPlacement.ts` is the DOM half of placing **every** command-rail overlay - the popover and each drop-up - and exists for two soft-keyboard bugs that presented as one.
`railOverlayView()` reads the *visual* viewport, because `interactive-widget=resizes-visual` keeps the layout viewport at full height and every bound drawn against `innerHeight` therefore describes a rectangle running behind the keyboard.
`fixedContainingBlock()` walks for the nearest ancestor that establishes a containing block for fixed descendants - `.terminal-surface`, once `.soft-keyboard-open` transforms it - and `railOverlayCss()` subtracts its origin, so the numbers the pure `railOverlayBox` produces keep meaning screen coordinates whether or not the keyboard is up.
Portalling the overlays to the body would fix the same thing and take the rail's chip styling with them, which is why the block is measured instead.
`watchRailOverlayPlacement()` is the listener set both components share: `visualViewport` resize and scroll on top of window resize and capture-phase scroll, since the keyboard's open and close fire nothing else and the rail is itself a horizontal scroller.

`railOverlayBox` is the pure geometry, and the two rules worth knowing are both device-class rules: below 760px an overlay takes half the screen and goes to the screen's trailing edge whatever opened it, while above it keeps its trigger's edge and its own width cap (`RAIL_POPOVER_MAX_WIDTH_PX` for a wrap grid, `RAIL_DROPUP_MAX_WIDTH_PX` for a list).
It returns the panel's bottom *edge* rather than a CSS inset, which is what makes the containing-block conversion checkable rather than implied.

`RailOverflowPopover.tsx` is the panel, and it is deliberately not a `RailDropup`: a drop-up is a picker that closes on selection, this is the rail folded, and a rail does not close when you press a key on it.
The panel has no header.
Its close control is an absolutely positioned top-right overlay, and its bottom-right footer control closes the panel before opening the full Configure Actions modal.
Three consequences are behaviours rather than details, and all three are covered by `test/renderer/rail-overflow.spec.ts`:
a drop-up opened from a chip inside it is outside its DOM, so the pointer that opens one is exempted from outside-dismissal;
Escape reaches both listeners on `window`, where `stopPropagation` stops neither and this one is registered first, so the panel stands aside while any `.rail-dropup` exists;
and `railPopoverClosingCommand` folds the panel for the selections that navigate away (a drawer tab, a drawer section, the prompt library), listened for on the `mux:command` bus so a drop-up's sticky exit and a voice command fold it the same way a chip does.
A fourth consequence is not the panel's own code: it renders *inside* `.terminal-action-rail`, which is a mobile gesture region, and its grid scrolls vertically, which is that region's swipe.
`mobileGestures.ts` names it in `GESTURE_SHADOWING_SELECTORS` so a touch that begins in it is dropped by the recognizer entirely - not merely denied a region, which would hand it to the workspace slots and change tabs behind the open panel.
Portalling it out would settle that too, and would cost the chips inside it the pulse `.terminal-action-rail`'s own `onClick` gives them, which is why the veto is by name.
The glass opacity is one CSS variable at `:root`, `--rail-glass`, shared with every drop-up, and it is measured rather than chosen: `test/railGlassContrast.test.ts` composites it over a white and a black terminal for every theme in the stylesheet and requires it both to cost no theme its 4.5:1 and to stay under 95%, so a later contrast "fix" cannot pass by going opaque.
The *single-layer* composition sets the number - a drop-up row is transparent over its panel where a popover chip has its own background - so testing only the chip would have let the drop-ups ship at an opacity that fails.

## The dropdown

`Dropdown.tsx`, `dropdownOptions.ts`, `dropdownPlacement.ts`, `projectOptions.ts`

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
It also owns `filterDropdownOptions`/`dropdownMatchRank`, the rank ladder the `filter` prop searches on: exact, then prefix, then substring of the label, then `detail`, then `title`, with ties keeping the caller's order because that order is the one the list was built to be read in.

The `filter` prop puts a search box at the top of the open list, for the lists a person searches by name rather than scans - every Project picker, and anything else that grows without bound.
It is opt-in rather than automatic on a row count: a box that appeared on its own once a Project was added would move the first row under the finger between two visits.
It is the same control and not a second one.
The filter narrows `options` into the rows on screen and every behaviour above - the opening scroll, the arrow walk, the press-slop guard - runs over those rows unchanged; type-ahead stands down while it is on, because two mechanisms competing for one highlight is worse than either alone.
The panel takes a second shape only when it is on, because an `<input>` is not a legal child of `role="listbox"`: the panel becomes a shell holding an ARIA 1.2 combobox over a `.dropdown-rows` listbox, while staying the positioned, scrolling element either way.
Without the prop the DOM is exactly what every existing surface and spec already has, which is the point - the capability cost the other fifty call sites nothing.

`projectOptions.ts` is where a Project list gets its order.
`compareProjectNames` is locale- and numeric-aware (so `phase-9` precedes `phase-10`), `byProjectName` copies rather than sorting a prop in place, and `projectDropdownOptions` builds the rows with the root path as `detail` - which the filter also searches, so two checkouts of one repo stay reachable by typing even though they share a name.
Every Project list outside the sidebar goes through it.
**The sidebar is the deliberate exception and uses none of it**: its order is the operator's own drag arrangement, which is the whole point of it.

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

`drawerLayout.ts`, `drawerTransient.ts`, `drawerVisibility.ts`, `UtilityDrawer.tsx`, `drawerTabs.ts`, `DrawerViewTabs.tsx`,
`drawerNotes.ts`, `noteTabs.ts`, `sidebarResize.ts`, and feature-named tab bodies

- `drawerLayout.ts` owns the JSX-free device-local recursive tree and per-Project presentation algebra.
- `drawerTransient.ts` derives a non-serialized Project-bound display override for momentary rail navigation.
- `drawerVisibility.ts` is the single JSX-free answer to whether a tab is drawn.
  It holds structural availability and the user's global device-local hidden set apart, refuses to hide the last tab, and treats an explicitly named hidden tab as a peek.
  Every strip, the desktop launcher rail, and the Settings mirror read it rather than repeating the rule.
- `App.tsx` owns migration, atomic persistence, independent drawer-tab and right-rail display preferences, transient-state lifetime, and collapse routing.
- `UtilityDrawer.tsx` owns desktop recursion plus the flat mobile projection, tab long-press, and singleton body dispatch, delegating rail overflow mechanics to `OverflowRail`.
- `UtilityDrawer.tsx` omits pane headings for Notes, Files, Actions, Git, Activity, and Agent, while preserving the header contract for Transcript, Schedule, Alerts, Queue, and Processes.
- `DrawerViewTabs.tsx` owns the full-width secondary-rail markup, roving tab stop, arrow-key selection, and shared Actions-derived presentation.
- `drawerTabs.ts` is the tab registry.
- `drawerNotes.ts` remembers the selected Notes sub-tab per Project.
- `noteTabs.ts` owns deterministic tab ordering, deletion fallback, and the per-Project note count behind the last-note delete guard.
- Notes alone keeps one hidden inactive workspace, and Processes consumes App's fleet snapshot without another poll.

## Drawer segments and sections

`drawerSegments.ts`, `DrawerSegmentControl.tsx`, `DrawerViewTabs.tsx`

The drawer's second axis: what a tab is *showing*, once a tab shows more than one thing.
Two kinds are deliberately kept apart.
A **segment** is a mutually exclusive view (Activity, Agent, Git).
A **section** is a co-visible region of one scroller reached by scroll-and-flash (Actions), which is what let a high-frequency insert surface like Clipboard be folded in at all.

`drawerSegments.ts` stays JSX-free and unit-testable like `drawerTabs.ts`, so availability is a predicate over a small context of booleans (`hasTranscript`, `isAgentSession`) rather than over a `Session`.
`resolveDrawerSegment` falls back to the first available segment.

`DrawerSegmentControl.tsx` adapts registered segments into `DrawerViewTabs.tsx`.
`ActionsTab.tsx` reads its registered section labels into the same component while retaining its device-local catalog selection.
Unavailable segments are **omitted rather than disabled**, because a greyed-out "Timeline" promises a surface that does not exist, and a tab with one available segment draws no control at all.
The drawer rail uses `.drawer-view-tabs`; Resources and Automation retain `.segmented-tabs` because they are modal-scale controls rather than utility-drawer chrome.

The registry exists so `App.tsx` can generate a palette command and a voice phrase per segment and per section: without it, folding a tab into a segment would delete the surface's command *and* its spoken navigation.
`RETIRED_DRAWER_SEGMENTS` is the same obligation in reverse, and is a row rather than a deletion.
A retired id names the live segment that absorbed it, `App.tsx` generates its command and phrases from that list too so "open Land" keeps answering, `migratedDrawerSegment` moves a *stored* selection onto it rather than letting `resolveDrawerSegment` fall through to the tab's first segment, and `keybindings.py` migrates the command id - where an unmigrated one is rejected rather than ignored.
Section targets reuse `settingReveal.ts` under a `drawer.<tab>.<id>` namespace rather than growing a parallel mechanism.

## Appearance

`index.html`, `theme.ts`, `ThemePicker.tsx`, `uiScale.ts`, `style.css`

Pure config to root custom properties.

- `index.html` declares the pre-script `only dark` default and Dark Reader opt-out.
- `theme.ts` owns the selectable catalog, xterm palette tokens, preview-color projection, and document presentation metadata.
  `applyTheme` derives light/dark browser treatment from the effective canvas and atomically updates the root scheme, `color-scheme` metadata, and `theme-color`; every theme preview therefore moves native controls and browser chrome with the app palette and reverts through the same path.
- `ThemePicker.tsx` owns the keyboard-accessible fixed-column swatch listbox.
  It is the design `Dropdown` is modelled on and stays its own component: every row carries a swatch strip, and highlighting one *applies* the theme without committing it, which is a preview contract no generic picker has.
- `uiScale.ts` owns `--ui-scale`, per-device-class resolution, discrete step movement, fixed keyboard classification, and high-resolution wheel accumulation.
- `style.css` owns shared theme-derived chrome, including compact scrollbars and the one `--check-size` rule that sizes every native checkbox and radio.
  Every fixed palette declares `color-scheme: only light|dark`, forbidding user-agent auto-transformation without opting out of forced-colors accessibility.
  That rule is fixed px, not `--ui-scale`, because that property multiplies type and the rows holding a line of type, never glyph-sized controls.
  A container rule shaped `<panel> input { width/height … }` must exclude `[type=checkbox]`, or it stretches the ticks inside that panel into full-width text-field boxes.
- `App.tsx` is the one scale controller: it captures fixed scale inputs before browser and xterm handling, updates chrome and the numeric xterm prop together, reports through `InteractionHud`, and debounces persistence outside the Settings draft.

Neither theme nor scale code reads rendered styles back.
The stylesheet is the only consumer of the *property* but not the only consumer of the *value*: `applyUiScale` returns the resolved step and `watchUiScaleProfile` reports it on a device-class flip, because xterm owns its font and has to be handed a number (`scaledFontSize`) rather than inheriting a custom property.

## Icon sets

`railIcons.tsx`, `harnessIcons.tsx`, `noteRailIcons.ts`

The app's stroke marks, kept in shared modules so one concept is one drawing wherever it appears.

- `railIcons.tsx` holds the Action rail marks, the `DRAWER_TAB_ICONS` map every drawer tab must appear in, and the menu marks the app menu, the sidebar menu, and the Project and session context menus read - including their `MenuGroup` headers.
  It is also where an icon-only control inside a tab takes its mark from, such as the transcript reader's per-message Copy and Select (`CopyIcon`, `CheckIcon`, `SelectTextIcon`).
  A mark is added here rather than inline so a concept drawn in more than one menu (History, Groups, Settings, Prompts) stays one drawing.
  That is also why a redrawn tab mark replaces its component rather than adding one beside it: `CommandKeyIcon`, `NotePencilIcon`, and `QueueClockIcon` are read by both the drawer map and the menu rows, so the two cannot drift.
  Sizes come from CSS rather than `1em`, because these surfaces run a 9-10 px font that would render an em-sized icon unreadably small.
  The set's one live constraint is that Activity and Processes are both traces on the same rail, and are kept apart by silhouette (a bare squiggle against a framed monitor on a stand) rather than by any detail that survives 34px and not 17px.
- `harnessIcons.tsx` is the per-harness mark (`harnessMark`), read by the sidebar row's provider prefix, the session tab strip, the account switcher, and the Run menu's launch rows.
  A harness with no drawing falls back to its initial, which is all a browser can honestly say about a harness its daemon added and it does not know.
  The single source is load-bearing rather than tidy: a per-surface copy that knows only the harnesses with provider accounts draws `oh-my-pi` and `opencode` identically, on the surfaces whose job is telling two panes apart.
- `noteRailIcons.ts` builds its shapes imperatively, because the note editor's rail is outside the Preact tree.
