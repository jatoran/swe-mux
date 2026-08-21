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
  Sizes come from CSS rather than `1em`, because these surfaces run a 9-10 px font that would render an em-sized icon unreadably small.
- `harnessIcons.tsx` is the per-harness mark (`harnessMark`), read by the sidebar row's provider prefix, the session tab strip, the account switcher, and the Run menu's launch rows.
  A harness with no drawing falls back to its initial, which is all a browser can honestly say about a harness its daemon added and it does not know.
  The single source is load-bearing rather than tidy: a per-surface copy that knows only the harnesses with provider accounts draws `oh-my-pi` and `opencode` identically, on the surfaces whose job is telling two panes apart.
- `noteRailIcons.ts` builds its shapes imperatively, because the note editor's rail is outside the Preact tree.
