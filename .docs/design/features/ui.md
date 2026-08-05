# Browser shell and interaction

## What it is

The Project-first browser shell around the mixed-view workspace: persistent app identity,
active-Project navigation, provider/resource status, viewport overlays, settings, focus, and
responsive controls.

## Desktop chrome and sidebar

- Scrollable surfaces share one compact scrollbar treatment: a 7 px interaction gutter with
  an inset rounded thumb, transparent track, theme-derived muted colour, and stronger hover /
  active feedback. Compact horizontal rails may hide their scrollbar when another overflow
  affordance is present.
- A persistent top rail places `swe_mux`, sidebar collapse, and daemon activity above the
  sidebar column. Workspace tabs are not global top-rail state; every pane renders its own tab
  strip beside that rail.
- The sidebar is pointer/keyboard resizable from 190-480 px and collapsible.
  Dragging its divider below 150 px previews collapse, and reversing the same drag past 170 px reopens it before release.
  The separate thresholds prevent state chatter near the boundary.
  Width and collapse state are device-local browser preferences, not Project layout state.
- On mobile, selecting either a Project row or a session row closes the navigation overlay. Project
  selection closes it before restoring that Project's remembered active view, including when no
  valid remembered view exists.
- The sidebar shows only Projects marked for active navigation.
  A Project row is followed directly by its layout/session rows; notes do not appear in the tree.
- **Project rows carry no `Note` / `Files` chips.**
  Both surfaces live in the utility drawer and the Project context menu exposes `Notes…` and `Browse files…` for another Project.
- Projects sit in sections: one per Group, plus the ungrouped remainder headed `PROJECTS`, which
  behaves as a section in every respect rather than as a pinned leftover. A section header does
  two jobs, and they compose rather than competing: press-and-move reorders it, press-and-release
  folds it. Drag-versus-click is settled by the drag swallowing the click it ends with — the same
  resolution a Project row already uses for drag-versus-select, so there is one rule to learn
  rather than two. The header's only button is `✎`, which renames the Group.
- **A sidebar toolbar above the tree owns sort and fold-everything**, because both act on the
  whole tree rather than on one section, and because a control inside a scrolling list is a
  control you have to scroll back to. It holds two buttons:
  - `⊟`/`⊞` folds or unfolds **every Project row and every section** in one click. It offers
    Expand only once nothing on screen is left to collapse — a half-folded tree still reads as
    untidy, so the next click should finish the job rather than undo it.
  - `⇅` sorts, covering both levels: flat items sort the Projects (Manual, Recently active, Name
    A→Z / Z→A, Newest / Oldest first), and a `Sort Groups` group sorts the sections themselves
    (Manual, Recently active, Name A→Z / Z→A). It is labelled for Groups because that is the word
    the rest of the app uses; PROJECTS is ordered alongside them, which the item's tooltip says
    outright since the label cannot. A `MenuGroup` because the common case should not pay for the
    rarer one. The button highlights while either level is sorted, and its tooltip carries both
    modes, which otherwise have no always-visible cue.
- Project sort is one global mode, applied inside every section. It was per section once, on the
  theory that a Group might be a hand-arranged shortlist while another is a long alphabetical
  pile; in practice it was set the same everywhere and cost a `⇅` on every header. Placing
  anything by hand puts that level back on Manual, because a hand-placed row that the next render
  re-sorts away reads as a broken drag.
- The sidebar cannot delete a Group. The `×` that did sat one pixel from the fold toggle and
  dissolved a Group on a stray click; a Group is emptied instead — reassign its Projects (Projects
  registry, or a Project menu's Group select) and it stops rendering, since a Group with no
  Projects in it is not a section.
- A folded section shows a live-session count and a state dot for the strongest agent state
  inside it, in the collapsed rail's colours so one folded thing never speaks a different visual
  language from another. The dot exists because a count alone would let an agent waiting on
  approval vanish behind the fold.
- Sort modes, fold state (Projects and sections), and the ungrouped section's slot among the
  Groups are device-local; Group order itself is shared. Behavior detail lives in `projects.md`.
- Only a tabbed pane indents its sessions in the sidebar, and it does so because it draws the
  bracket that explains the indent. Split branches are siblings at the same depth: the sidebar is
  a session list, not a pane-geometry diagram, and indenting per split produced unexplained
  nesting that deepened with every split. Indentation is therefore at most one level.
- The bracket is drawn per branch and stops at the first and last session rows' centres. A
  cluster-height spine would dangle past the last session row whenever that session also has a
  note or preview row beneath it.
- The bracket is the only marker of tab membership. Rows carried a per-row glyph saying the
  same thing, which repeated on every row of a group the bracket already encloses and competed
  with the backend glyph and broadcast flag for the name's line.
- Agent attention edges (`viewing`, `unread`) sit on the row's right and are inset vertically.
  On the left they shared a gutter with the tree's connector lines, so a row marker read as a
  branch, and consecutive marked rows merged into one long rule that looked like a stray spine.
  The left gutter belongs to the tree alone.
- The active-Project header and each Project row expose **Run**. Its compact menu contains new
  Claude/Codex/shell/custom-terminal launchers followed by trusted Project Actions; it is a
  launch surface, not persistent sidebar grouping.
- Run is the only always-present launcher, since tab strips carry no new-tab button
  (`workspace-layout.md`). The header Run is styled as an accent chip rather than a faint label,
  and because it has no room in the 40 px collapsed header column, the collapsed rail carries an
  equivalent `▶` button. Mobile's toolbar Run is the same surface.
- `projects` opens the viewport-level Projects manager, which lists configured visible and
  hidden Projects. A Project must exist before terminal actions are enabled.
- Separate Claude and Codex rows and owned CPU/RSS status remain pinned at the sidebar bottom.
  Account/resource popovers render through the viewport overlay layer, so a narrow or collapsed
  sidebar cannot clip them.
- That status block is pinned in the mobile drawer too, at touch height. The toolbar's quota
  chips answer "how much is left" in a glance a drawer cannot give; the drawer rows answer
  "on which account, and what is this machine doing" — the selected account per provider, its
  5-hour and weekly windows, and owned CPU/RSS. Both open the same popover, so the drawer is a
  second door to account switching and quota management rather than a separate feature. Mobile
  previously hid the block, which left the phone with no way to see or change the active
  account outside Settings.
- Collapsing the desktop sidebar leaves a rail rather than a dead strip. Every visible Project
  retains a scrollable chip: hyphenated names use the first character plus the first character
  after the first hyphen; other names use their first two alphanumeric characters. The chip edge
  shows the strongest live state (approval, working, ready, or ordinary running), while a separate
  dot preserves unread-agent output even during another activity state. The active Project keeps
  an explicit selection border. Status, menu, and Projects controls remain pinned beneath the
  chip list; every status indicator opens the same popover as its expanded counterpart.
- Each quota chip stacks the provider's own mark above its usage. The mark is the only thing
  identifying the row, so it keeps full contrast while the numbers carry the shared
  ok/warn/critical banding. Providers render in the same order as everywhere else.
- The **collapsed desktop rail** has room for one number, and that number is weekly: the 5-hour
  session window churns constantly, and `fable` is a sub-window of one provider's plan rather
  than a measure comparable across providers, so weekly is the one worth a permanent glance.
- The **mobile toolbar** has the width for all of them and shows every window the provider
  reports — `5h/weekly[/fable]`, so Claude reads `90/80/74` and Codex, which has no 5-hour
  window today, reads `—/74`. A window the provider omits keeps its slot as `—` rather than
  collapsing, because there are no percent signs to anchor on and it is a number's *position*
  that says which window it is. The signs are dropped for width: every value here is a
  percentage, so the sign distinguishes nothing while costing a third of the chip. Each window
  is banded on its own, so the chip says *which* one is hot; the chip's border takes the worst
  of them. The weekly reset countdown stays on a second line beneath — "22% used" answers a
  different question from "and it clears in 4d12h", and a phone has no hover tooltip to reach
  the second one. The chip's tooltip (and so its accessible name) names every window and says
  the countdown is the weekly one, since the chip itself shows unlabelled numbers.
- A band always describes the digits actually printed, not the value behind them: a rounded `90`
  colours as 90 even when the true reading is 89.6, or the colour would contradict the number
  beside it at exactly the threshold people watch for.
- The resource chip reports RAM rather than CPU, since a percentage that moves every sample is
  not worth a permanent glance, and abbreviates it (`3.2G`) to fit the strip.
- Popover direction is independent of the condensed trigger, so a rail anchored at the bottom of
  the window still opens upward.
- Git state is Project/session metadata. Worktrees have no first-class sidebar row, creation
  modal, or workspace ownership; the drawer's Git tab is their only surface (`git.md`).

## Menus and overlays

- Scope follows the menu that opened a surface, never a hidden mode. The app menu's unlabeled
  lead block opens History, Notes, Process fleet, prompt library, clipboard history,
  usage, and notifications across every Project; right-clicking a Project row opens the same
  surfaces under `BROWSE THIS PROJECT`, prefiltered to it. Right-clicking empty sidebar space is
  the no-Project case and matches the app menu.
- The app menu holds **nothing that acts on a single Project**. Per-Project actions — the
  observation inbox, Project settings, files, notes — live on the Project itself: right-click a
  sidebar row, or tap the Project title in the mobile top bar (both open the same menu). The
  lead block is therefore deliberately unlabeled; a `BROWSE ALL PROJECTS` heading described
  neither the clipboard nor notifications, and the old `CURRENT PROJECT` section duplicated the
  Project menu one level away from the Project it acted on.
- Starting work is the Run menu's job alone (active-Project header, every Project row, mobile
  rail). Neither the app menu nor the Project context menu carries "New terminal": Run already
  offers the same backends plus the Project's imported tasks, and a second door only split the
  affordance.
- Broadcast input stays in the app menu because it is an app-wide input mode, not a Project
  action; membership in the broadcast set is per session, toggled from a session's own menu.
- A menu section with more than a couple of rarely-used entries collapses behind a `MenuGroup`
  (`MenuGroup.tsx`), which any `.context-menu` can host. On a hover-capable wide pointer it
  opens a side flyout after a short intent delay and closes on a delay so the diagonal traverse
  survives; on touch or a narrow viewport it is an inline accordion, because the menu is already
  the width of the screen and there is no hover to open a flyout with. An accordion on desktop
  was rejected outright: expanding in place reflows the rows under the cursor, so hovering the
  next row to collapse the group moves the target out from under the pointer. Items render as
  buttons inside a `.context-menu` subtree positioned directly after their header in document
  order, so the menu-wide arrow-key walk steps through them where a reader expects. Only one
  group is expanded at a time, and closing the menu collapses it.
- A prefiltered surface always exposes its scope as a visible, clearable control, so a Project
  entry point narrows the same browser rather than opening a different one. The prompt library is
  the deliberate exception: its Project argument adds that Project's templates to the global set
  rather than filtering, so opening it "unscoped" would remove templates. The app menu therefore
  still passes the active Project there.
- Context menus are source-aware. Terminal-only operations never appear on resource tabs;
  obsolete focused-terminal, detach/remove-from-group, Project-note, pane-swap, and pane-header
  minimize/close actions are absent.
- Opening tab actions is non-activating. Desktop right-click and mobile long-press target the
  named tab without changing the pane-active tab, focused view, or active terminal; normal
  click/tap remains the activation gesture.
- **No context menu reorders or reshapes anything, on any platform.** Open-in-split,
  new-terminal-in-split, new-custom-terminal-in-split, stack-with-focused, dissolve-stack, and
  move-tab are absent from the session menu on every source (sidebar row, tab, pane bar / `⋯`),
  from the tab menu, and from the mobile long-press menus. They answer "how is the workspace
  laid out", which is not the question any of those menus is opened to answer, and the
  direction rows pushed Rename and Kill below the fold in all of them. Layout is **drag**
  (direct manipulation, with split/tab-bar drop previews) or the **command palette** — every
  action has a registry command (`session.openSplit*`, `pane.split*`, `pane.moveTab*`,
  `session.groupStack`, `stack.dissolve`, `session.customSplit`), so each stays searchable and
  bindable to a key. `pane.moveTab*` exists *because* the row was removed: drag covers moving a
  tab between panes by pointer, and without those four commands there would be no keyboard
  route to it and nothing to bind.
- Touch has neither of those routes, so mobile simply does not reorder: the rail follows the
  layout projection, and the device-local permutation the touch row used to write was deleted
  along with it rather than left orphaned (see `workspace-layout.md` § mobile projection).
- The **resource** menu is the one exception that keeps a directional row — `Open in split`,
  because a note or an opened file has no tab to drag until it is already in a pane.
  It uses a non-clickable label with directional arrow buttons, enabling only directions valid
  for the current split tree.
- Pointer-anchored menus are re-fitted to the viewport after they mount, not merely clamped at
  open time: the inline coordinates are seeded from rough height guesses, and content that lands
  later (a Project's imported task list) can make the box much taller than the guess. The Run
  menu also caps its own rendered height before lifting it, because a CSS `max-height` is measured
  against the viewport rather than the menu's own top — a Project with a long VS Code task list,
  opened from a sidebar row halfway down a phone screen, would otherwise be viewport-tall and
  still hang off the bottom, clipping its last entries with no way to scroll to them. Capped, the
  menu scrolls internally beneath its sticky header, and it re-fits on resize/rotation.
- Account, resource-usage, context, and command popovers are viewport-anchored. Settings,
  Projects, transcript review, and confirmation dialogs use the modal layer. Opening a child
  dialog from Projects must place it above the manager, never beneath it.
- Every full-screen dialog layer stacks above all persistent chrome: the mobile toolbar, mobile
  nav toggle, desktop top bar, and context menus. Chrome painting over a dialog does not merely
  look wrong, it silently swallows taps on the dialog's own header, which is where close and
  primary actions live. On phones the sticky mobile toolbar previously covered the Projects
  registry header, so `+ Add project` rendered but could not be tapped.
- Add project is one form with an `Existing folder` / `Create new folder` mode strip. Create
  mode asks for a parent plus a folder name (prefilled from the Project name until edited) and
  shows the exact canonical root it will register. Optional setup commands sit in a collapsed
  `<details>` and start unchecked, so the common path stays name, folder, Enter.
- Backdrop clicks close Settings. Dirty settings first open an in-app Save/Discard decision;
  interaction with that confirmation is inside the modal boundary and cannot also trigger the
  Settings backdrop.
- The app menu's `Maintenance` group (a `MenuGroup`; mirrored in the sidebar context menu and
  command palette as `daemon.reload`/`app.redeploy`/`ui.reload`) exposes the session-preserving
  reloads on every device, including mobile. "Reload daemon (keep sessions)" posts
  `/api/daemon/restart`, shows a blocking wait overlay while the daemon is down, and reloads
  the page when the successor answers health; the server refuses (409, surfaced as a toast)
  when no PTY supervisor is attached so the action can never silently kill sessions.
  "Rebuild + redeploy app (keep sessions)" confirms, posts `/api/daemon/redeploy` (staged
  frozen-app rebuild; the only reload that reaches the frozen bundle's own assets), and shows
  a blocking overlay through the multi-minute build/swap/relaunch: while the old daemon still
  serves it polls `GET /api/daemon/redeploy` so an early build failure surfaces as an error
  toast with the log tail (the running app is untouched); after the daemon drops it polls
  health for up to 8 minutes and reloads when the new (or rolled-back) app answers. "Reload
  UI" is a plain page reload for picking up freshly built frontend assets.

## Settings contract

- Form changes remain local drafts until explicit Save. Save state is visible as
  dirty/saving/saved, and a background refresh cannot reset the selected settings section.
- Opening loads one `GET /api/settings/bundle` (config, rules, keybindings, profiles,
  projects, automation, provider, usage, project config) instead of nine per-section GETs,
  so a high-RTT client (phone over Tailscale) pays a single round trip. The panel chrome —
  header, tab rail, footer — renders immediately with a placeholder content area; tabs are
  selectable before data lands. `config` is required; other parts degrade to null with the
  reason under `errors`, except `automation_rules`/`keybindings`, whose absence blocks the
  form because Save writes them back unconditionally. Remote and voice status stay separate
  non-blocking fetches.
- The panel header carries a search box that reaches every setting in every tab, including
  tabs that are not mounted. Picking a result switches to its tab, scrolls the control into
  view, and flashes it; `Ctrl`/`Cmd`+`F` focuses the box while the panel is open, arrows and
  Enter drive the result list, and Escape unwinds the list before it closes the panel.
- That index is derived from the same JSX that renders the form, so a setting added or renamed
  in `Settings.tsx` is searchable with nothing else to declare. Each tab's markup comes from one
  function taking the tab id, and the index walks the *vnode* tree it returns rather than the
  DOM: building vnodes allocates plain objects, so an unmounted tab can be indexed without
  running effects or child-component bodies. A component vnode is a function reference rather
  than markup, so what `<AccountSettings/>` renders is invisible to that walk; a tab that has
  been on screen is therefore also indexed from its live DOM, by the same rules, and kept for
  the page session. Every tab additionally carries an entry for its own name, so one never
  goes missing entirely. Labels, headings, buttons, option labels, placeholders, and the help
  paragraph following a control are all matched on; the index is rebuilt when a search begins
  or when the config it came from changes, never per keystroke.
- The footer carries only draft state: status, Cancel, Save. Whole-config actions — reveal the
  config directory, export a sanitized copy, restore defaults — live in a General-tab block,
  because a footer repeats under every tab and so implied a per-tab scope none of them have
  (restoring defaults rewrites the entire saved config immediately, outside the draft/Save
  cycle). It also kept Cancel/Save in a horizontally scrolling footer on phones. Per-section
  resets that genuinely are scoped — gesture defaults, shortcut defaults, the command rail —
  stay with their own section.
- Keyboard shortcuts distinguish browser-reserved chords from desktop-only chords. WebView2
  releases the latter to the app, while an ordinary browser keeps its own tab/window behavior;
  Settings exposes both categories and accepts `Ctrl+Tab` / `Ctrl+Shift+Tab` as mappable desktop
  inputs. Modified Tab chords never enter focus traps, drawer-tab traversal, or editor indentation.
- Notes configures the shared Markdown editor behind every note and Markdown file: spellcheck,
  Markdown rendering, `Tab`, typography, the touch command rail, and the editor's own shortcut
  policy and per-chord overrides (`project-resources.md`). The chord table is enumerated from
  the editor package rather than hand-listed, so it cannot drift from what the editor binds.
- Appearance exposes **chrome scale**, one multiplier on the size of every surface, stored
  **separately for desktop and mobile**. Both default to `1.0`, so installing the build that
  added it changes nothing on screen; it only moves when the user moves it.
  - It is a *scale* and not a font size on purpose. All chrome type is already one number —
    `style.css` forces `font-size:var(--ui-font-size)` onto every element inside `.app-shell`
    that is not the terminal, with an `!important` that beats the ~165 per-rule font sizes
    written before it — so exposing that number alone is a two-line change. It is also the
    version that wrecks the layout: text grows inside rows and bars whose heights are fixed
    px, so labels clip and the two-line session row overflows. `--ui-scale` therefore
    multiplies `--ui-font-size` *and* the row/bar heights that hold a line of chrome text, and
    a row grows with the text in it.
  - The line between what scales and what does not is **what clips**, not what looks big:
    - A fixed `height` or a px `grid-template-rows` track clips its contents, so every one
      of them on a text-bearing surface scales — rows, bars, tab strips, menu items, form
      controls — as do the fixed `width`s of menus and popovers (a label truncates instead)
      and the three px `line-height`s.
    - `min-height` is a *floor*, and growing text already pushes past a floor, so the ~187
      of them are left alone. That is also why the 44 px touch minimums need no exception
      written for them: they bind only while the scaled content is smaller than a thumb.
    - Overlays positioned under a bar (`inset:42px 0 0`, `height:calc(100dvh - 42px)`) scale
      that offset in lockstep with the bar, or they overlap it or leave a gap.
    - Padding, gaps, borders, radii, and the SVG icon sizes (px "never in `em`", for the
      reason in *Utility drawer*) do not scale. None are type-derived, and holding them
      still is what keeps a larger interface from also becoming a sparser one. Nor do
      panel *maxima* (`height:min(760px, 100dvh - 42px)`) — those are viewport-bound — or
      widths the user already drags (the sidebar, the docked drawer).
    - The steps stop at 1.4 because past it the fixed values start to crowd.
  - **The terminal follows the scale too, but not through CSS.** xterm owns its font and
    derives the cell grid from it, so `TerminalPane` is handed the scale as a number and
    multiplies its own base size by it. Two decimals, matching `--ui-font-size` exactly:
    that `calc()` does no rounding, and rounding to whole pixels would make 1.1 and 1.25
    render identical terminal type while the chrome beside them moved. A bigger font fits
    fewer columns and rows in the same box, so this pane proposes a smaller grid, and that
    proposal is what cross-device arbitration reduces — intended, and no different from
    resizing the window. The alternative is a device rendering a grid whose type its owner
    just said they cannot read. It is applied live, in its own effect: font size is
    assignable on a running terminal, and putting the scale in the construction effect's
    deps would dispose the terminal and replay the whole buffer to change a number.
  - **A popover portalled to the body carries `ui-portal`**, which is in the normalizer's
    selector list alongside `.app-shell *`. The account and resource popovers render through
    `createPortal` so a narrow or collapsed sidebar cannot clip them, which also put them
    outside the only rule that overrides per-element px sizes — they stayed at a fixed
    7.5–9 px at every setting while the chrome around them moved. The class is the contract;
    a contract test fails any `createPortal(…, document.body)` root that omits it.
  - A corollary worth stating, because it has been violated twice: **a px `font-size` written
    in a rule under `.app-shell` does nothing.** The normalizer's `!important` beats it at any
    specificity. Chrome type is one number; a surface that wants emphasis uses colour, weight
    is already fixed at 600, and anything else needs the normalizer changed rather than
    worked around.
  - Because every conversion is `<n>px` → `calc(<n>px*var(--ui-scale))`, substituting
    `--ui-scale: 1` back into the stylesheet reproduces the pre-feature file exactly. That
    is the check to re-run if this is ever extended: the default must stay inert.
  - Steps are discrete (`0.9 / 1.0 / 1.1 / 1.25 / 1.4`, validated against the same list in
    `config.py` and `uiScale.ts`) rather than a free number. There is no useful difference
    between 1.13 and 1.15, only a way to land on a value that looks broken, and a hand-edited
    `config.toml` carrying one falls back to `1.0` rather than rendering at it.
  - The split is by device class because the same UI is driven from a desktop browser and a
    phone and one number cannot say "the phone is too small but the desktop is fine". A window
    resolves its value through the same `(max-width:760px)` breakpoint as the mobile workspace
    projection and the device-class settings profiles, and re-resolves when that breakpoint
    flips, so a desktop window dragged narrow adopts the mobile scale live. Both values are
    editable from either device — sizing the phone from the desktop is the point, since the
    phone is the harder device to type on — and the panel says which of the two the window
    you are looking at is currently using.
  - Excluded from it entirely: only the note editor, whose typography is its own
    `--continuity-*` setting under Notes. The terminal was excluded at first — it has its own
    font size and its cell grid feeds cross-device viewport arbitration
    (`features/terminal-input.md`) — but leaving the largest surface in the window at a fixed
    size while everything around it grew is not what the setting is asked for, and the
    arbitration consequence turned out to be the correct behaviour rather than the objection.
- Terminals exposes `auto | webgl | dom` renderer selection. `auto` preserves accelerated WebGL
  on desktop with automatic DOM fallback; mobile and Codex terminals always use DOM regardless
  of the preference so their scrollback remains stable.
- The WebGL addon is constructed with `preserveDrawingBuffer: true`, and that is load-bearing
  rather than a tuning choice. `WebglRenderer._updateModel` skips any cell whose code, fg, bg
  and ext match its model, so a frame re-uploads only what changed and every other pixel is
  assumed to still be in the drawing buffer. Under the default `false` the browser may discard
  that buffer as soon as the canvas stops being composited, which is what a warm pane behind
  another tab is (`.pane-warm` is `display:none`). The pane then returns with only the changed
  cells drawn, and dragging a selection over the gaps repaints them — the "it draws once I
  highlight it" symptom. Nothing fires when a compositor drops a buffer, so an event-driven
  repair cannot cover this and the assumption is what has to go.
- Repaints are still repaired on the events that *are* observable (pane shown, intersection,
  `visibilitychange`, `pageshow`, window focus, replay end, context loss), plus one confirmation
  pass a settle later. The terminal's memo boundary compares pane visibility so a tab-only
  transition cannot swallow the show event before it reaches the retained xterm instance.
  Pane restoration also forces xterm's renderer-dimension path after the fit: both FitAddon and
  public `term.resize` return early when the cell grid is unchanged, even though a renderer
  returning from `display:none` can still hold a stale pixel surface in the upper-left part of
  its host. `reflowVisibleTerminalRenderer` temporarily toggles and restores the public,
  non-geometric `customGlyphs` option; xterm treats that option as renderer-invalidating and
  invokes `handleResize` without changing the grid or sending a PTY resize frame. The explicit
  Resize action uses the same repair. The confirmation is surface-only — atlas clear and
  refresh, never a refit — because a fit is
  `term.resize` plus a pseudoconsole resize plus a full CLI repaint, and none of that is what a
  lost paint needs. xterm's `RenderService` fires `onRender` whether or not the renderer drew
  anything, so a dropped paint is invisible to the app and is never retried by the library;
  assuming a single redraw landed is what left panes half-drawn.
- The daemon reports the host PTY to the browser as `pty_windows` in `/api/config`, and every
  terminal is constructed with it as xterm's `windowsPty`. A browser cannot detect ConPTY, and
  xterm needs it to know that lines are hard-wrapped without a wrap flag: below ConPTY build
  21376 it must disable reflow, or resizing a pane rewraps and rewrites the whole scrollback.
  It is passed at construction, never assigned afterwards — the option installs a parser-level
  wrapped-line heuristic, so bytes written before it was set would keep the wrong flags.
- Close, Escape, backdrop click, and navigation away all share the Save/Discard guard when a
  draft is dirty. Shell executable/profile paths deliberately use this explicit flow rather
  than per-keystroke persistence.
- Multiline ignore inputs preserve Enter/newlines in draft state. Save trims entries and removes
  blank lines before sending normalized patterns.
- Notification sounds preview immediately after browser audio unlock. Bundled choices are
  intentionally restrained; volume, per-event enablement and sound selection, quiet hours, and
  test playback are device preferences. A custom upload joins the same previewable library and
  does not replace existing event assignments.
- Agents carries the auto-delivery master switch and its bounds (`auto-delivery.md`). It is a
  bounds editor, not a schedule: when the switch is on, each new Claude/Codex conversation gets
  a bounded default-on grant that can be turned off in its Queue panel. The runtime state it
  governs — the emergency pause and conversation grants/overrides — stays in
  SQLite behind the queue pane, outside the draft/Save cycle.
- General exposes **Reset & run tutorial**. Starting it shares the ordinary Settings
  Save/Discard guard, so replay never silently loses a dirty draft.

## Guided first-run tutorial

- A versioned device-local completion marker opens the tutorial on the first browser/WebView
  visit. Finish and **Exit tour** both suppress later automatic runs; Settings reset removes the
  marker and starts immediately.
- The action-driven walkthrough covers the real Projects registry and creation form,
  provider-native Claude/Codex login or current-login capture, Run menu, shell launch, pane/tab
  lifetime, second-tab creation, tab movement, pane-edge splitting, Project notes, menu browsers,
  and keyboard shortcuts. Replay with an existing Project opens it instead of forcing a duplicate.
- The notes step is anchored on the utility rail's persistent **Notes** button.
  It opens the Project-owned collection and teaches note creation independently of sessions.
- Highlighted product controls replace **Next** for action steps. Transparent blockers leave only
  the spotlight opening and tutorial card interactive; Project creation, account save, terminal
  launch, and layout drops advance only after their ordinary operation reports success.
- Both the Run step and the second-tab step drive the real Run menu, since tab strips have no
  new-tab button to point at; the second-tab step's spotlight returns to Run.
- Run requires opening the actual menu and selecting Shell. Account setup requires either
  **sign in + save** or **save current login** to complete successfully. Failures remain on the
  current step with the owning feature's normal error surface.
- Drag coaching is gesture-aware. Before movement, only the source tab is marked. After the
  pointer gesture crosses the real five-pixel drag threshold, the spotlight moves to the tab bar
  or a right-edge split zone; the native insertion/split preview remains visible and unobscured.
  Only a completed tab-bar or pane-edge drop advances its matching step.
- Narrow/mobile replay replaces desktop drag/split actions with the unified-tab-rail explanation;
  mobile intentionally cannot perform or persist desktop pane geometry.
- Resize, scroll, responsive changes, folder-picker transitions, and drag phase changes recompute
  coach-mark geometry without persisting tutorial geometry. Escape and the visible header exit
  work on every step; progress, reduced-motion styling, and the mobile bottom sheet remain.
- Completion is browser-local only. No daemon config, Project file, account metadata, or layout
  field records tutorial state.

## Focus and responsive behavior

- Focus is device-local and URL-addressable by Project/session. Reload prefers a valid URL
  target, then remembered focus, then a visible fallback.
- Focus naming a view this Project's layout does not hold is treated as stale and replaced with
  the first pane's active tab. That is right for a view that was closed elsewhere and wrong for
  one that has not arrived yet, and both look identical at that instant. So a flow whose pane
  the *daemon* creates — history resume, Branch, second opinion — **requests** focus rather than
  setting it: the response names the new leaf, but the layout carrying it is a refresh behind,
  and a plain focus in that gap is reconciled away before the pane appears. The symptom is
  specific and was live for resume: the new session starts, and you are left looking at the tab
  you started from. A request is held until the leaf actually exists — not for one refresh, but
  however many it takes, since `refresh()` is deduplicated and the one a flow awaits can have
  been snapshotted before its own spawn committed. It is dropped early only if something else
  lands on a real leaf while waiting (a tab click, a project switch), which is a choice the user
  just made and outranks a request the layout still cannot satisfy. Ordering lives in
  `reconcileFocusView` (`viewState.ts`), kept pure so it is testable without a layout tree.
- Focusing a pane is not enough if something is covering it. These flows also close the
  full-screen overlay they were started from and, on a phone, the navigation drawer; the side
  panel closes on mobile only, since on desktop it is a docked column beside the workspace
  rather than over it.
- Mobile's top row has no separate session dropdown. The Project name is a real button: a
  single tap opens the Project menu (long-press and right-click stay equivalent), because
  reaching a menu should never require a hold on touch.
- The bar is flex with `nowrap`, not grid: expressed as grid, the `auto` track next to the
  name's `1fr` absorbed the slack and left the quota boxes stranded mid-bar.
- The bar is `[nav] [quota] [Project name] [Run] [side panel]`. Only the Project name flexes;
  everything else is content-sized, and the name is the one thing that can give, since it
  ellipsizes and the Project it names also appears in the sidebar and the tab strip. Measured
  in-page (the chip renders at `--ui-font-size`, so measuring it outside `.app-shell` reads
  ~20 px per chip too narrow): the fixed items take ~299 px at 100%, leaving the name ~90 px
  at 390 px and 20 px at 320 px. At 140% the chips grow to ~101 px each and the name is down
  to ~49 px at 390 px — tight, ellipsized, and still one row, which is the trade the scale
  setting is asking for.
- Mobile quota is **two boxes, one per provider**, each carrying every window that provider
  reports over the weekly reset countdown (`90/80/74` over `4d8h`) — see the quota-chip rules
  above. It previously showed a single number — whichever provider's weekly window was furthest
  along — which hid *which* provider was burning and gave no sense of how long until it
  cleared, and a phone has no hover tooltip to recover either. Providers render in the same
  order as every other surface.
- Nav is a glyph rather than the `:nav` label. No word survives at this width, and pinning a
  font size to force one would ignore the user's UI-scale setting, which this button is subject
  to through an `!important` rule. It and the side-panel toggle are one mirrored box (36 × 44
  px): whatever is true of the tap target for one drawer is true of the other. 24 px was too
  narrow to hit reliably — the 44 px height alone does not rescue a target that thin, because a
  thumb's contact patch is wider than it is tall — and the glyph scales with the box, or a
  wider button only frames a 9 px `≡` in dead space. Both drawers also open by swipe, so
  neither toggle is its panel's only entry point.
- Every Run trigger that targets the active Project — mobile toolbar, desktop header, collapsed
  rail — toggles: a second click collapses the menu. Sidebar project rows keep the plain open, so
  clicking another Project's `▶` while a menu is up switches to it rather than only closing.
- Both mobile toolbar triggers toggle: tapping an expanded Project menu or Run menu collapses it.
  Each needs its own guard, because they close by different routes. The Project menu relies on
  the document dismiss handler, so its trigger carries `data-menu-toggle` and that handler skips
  it — otherwise pointer-down closes and the click reopens, which looks like a menu that will not
  close. The Run menu instead has a full-viewport scrim above the toolbar, so a second tap
  dismisses through the scrim and never reaches the button; on touch the click then lands on
  whatever is under the finger once the scrim is gone, so the trigger treats a click within
  350 ms of a scrim dismissal as the closing half of the toggle rather than a fresh open.
- On touch devices, app chrome (toolbar, tab strips, sidebar, pane bars, action rail, voice
  strip, context menus) suppresses text selection and the native callout. Long-pressing a
  control to reach its menu must not raise selection handles or the magnifier over its label.
  Terminals, editors, and inputs keep normal selection — the suppression is scoped to chrome,
  never to content.
- Mobile's contextual toolbar includes the same Project-level Run menu as desktop.
- Mobile uses one horizontally scrolling tab rail and one selected view. This is a projection
  of the durable desktop pane tree; see `workspace-layout.md` for placement and restoration
  rules.
- The selected terminal keeps an in-flow session header above a remaining-height terminal
  surface. Terminal visibility does not depend on convergence with a separate global active ID.
- Agent headers omit cwd on every device. A spawn path marked `last-known::` is stale metadata,
  not an actionable session control, and consumed the header's central space. Shell headers keep
  cwd on desktop; touch hides the shell cwd via `.pane-bar>.pane-path` so status and tools remain
  on one row.
- Touch gestures are configurable command slots: single-finger horizontal swipes, two-finger
  horizontal *and vertical* swipes, and a two-finger tap. Only the **single**-finger vertical
  channel is reserved (terminal scrollback / application wheel); two-finger vertical is a real
  mappable slot. Edge- and top-anchored swipes stay with the OS. Slot names are validated
  server-side, so adding one requires the same slot list in `config.py`.
- The gesture recognizer yields to anything that owns horizontal scrolling (the action rail,
  tab strips, the voice strip, plus a generic `overflow-x` scan), and it must yield *cheaply*.
  Ownership is resolved from the touch event's **composed path**, not `event.target` plus
  `parentElement`: shadow-DOM retargeting hides an embedded component's internal scroller from
  ordinary ancestor walks. This is how Continuity's command rail keeps horizontal touch drags
  inside the editor instead of turning them into swe-mux tab or panel gestures.
  Only the `touchmove` listener ever calls `preventDefault`, and a non-passive `touchmove`
  registered on `window` forces Chrome to route every touch through the main thread before it
  may scroll — on a busy pane that is enough to swallow the first drag on the rail. So
  `touchstart`/`touchend` stay passive and the non-passive `touchmove` is attached only once a
  touchstart has claimed the gesture, then dropped when the sequence ends. Registering it during
  touchstart dispatch still yields cancelable moves, so owned gestures keep working while drags
  inside a scroller meet no handler at all.
- It also yields to a **pointer drag**, and that yield is stated rather than measured. Dragging a
  drawer tab along the strip is, in coordinates, exactly the single-finger horizontal swipe that
  toggles a panel — so rearranging tabs on a phone fired the swipe bindings until the drag began
  claiming the pointer (`pointerDragClaim.ts`, `workspace-layout.md` § Pointer drag contract).
  The claim is taken at the drag's 5 px threshold, so a swipe that merely starts on a tab is
  unaffected, and a sequence is dropped if a drag ran at *any* point inside it — a live "is a drag
  running" flag read at `touchend` would always say no, since `pointerup` releases the claim first.
  Applies to every drag on the contract, not the drawer strip alone.
- A recognized gesture gives a short haptic tick, and tab navigation shows a transient label
  pill naming the tab it landed on. Both exist because a swipe that lands on an unbound slot,
  or a tab change the eye misses, is otherwise indistinguishable from "nothing happened".
- While a slide-in panel (sidebar or utility drawer) is open, the horizontal swipe pointing
  back toward the edge it slid in from closes the panel instead of running that slot's binding
  — dismissing the right-edge drawer can never open the left sidebar on top of it. The
  override applies to one- and two-finger horizontal swipes alike, even to unbound slots
  (an open panel with a scrim makes the swipe-away motion unambiguous); the drawer wins when
  both panels are open because it overlays the sidebar. Resolution is a pure layer between
  recognition and dispatch (`resolveGestureCommand`), toggled by the hot-reloadable
  `mobile_gesture_swipe_away_close` config bool (default on, checkbox in Settings → Input →
  touch gestures).
- A pane that loses terminal input it was holding says so, instead of silently swallowing
  keystrokes: a strip names the device with the keyboard and offers a one-click "Take over".
  It appears for exactly two things — input this pane held moved to another device, or a
  keystroke was actually refused. It says nothing about a session the user merely opened:
  opening is not a request to type, output needs no ownership, and the first real keystroke
  claims input by itself and lands with it, so a refused attach costs nothing and reporting
  it asks the user to fix what is not broken. Showing it there made a phone demand "take
  over" on every session opened, which is a UI defect whatever the arbitration underneath is
  doing. Arbitration itself: `features/terminal-input.md`. While the
  arbitrated size is another device's, the pane letterboxes: it renders that grid at a reduced
  font size rather than re-fitting, since re-fitting is what pushed two devices into resizing
  each other in a loop.
- A terminal scrolled off its newest line shows a jump-to-latest chip in the terminal's own
  grid cell, above the action rail. It is checked per render, not only on scroll, because
  output arriving while scrolled up moves the buffer base without moving the viewport.
- Jump-to-latest changes only the terminal and application viewports.
  It does not focus the terminal input or raise the mobile soft keyboard.
- That check reads xterm's buffer, which is silent about a whole class of sessions. An
  application holding the mouse (Claude does; Codex enables no mouse mode at all) is handed
  every scroll gesture — the wheel on a desktop, and the drag a phone forwards as one via
  `mobileDragTarget` — and scrolls its own viewport, leaving xterm's pinned to its tail. So
  `offTail` never fires there, and for as long as the chip depended on it alone the chip
  simply never existed in a Claude session: the only jump-to-bottom on offer was Claude's own.
  The pane therefore also remembers forwarding a
  scroll *back* through the history, and raises the chip on that. Only on a drag back:
  arriving at the newest line again is something only the application knows, so the chip
  stays up until the jump is taken rather than guessing and vanishing early.
- Reaching the tail — from the chip or from a command-rail key — goes through
  `scrollTerminalToTail`, which re-issues xterm's scroll while it still makes progress. One
  call is not always enough: xterm applies scrolls through the DOM scroller in its `Viewport`
  and republishes that scroller's range only from `_sync()`, but answers `onResize` with
  `queueSync()`, which defers the update to a queued render callback. A refit moves the buffer
  base immediately, so a scroll issued before that callback is clamped to the pre-resize
  maximum and stops exactly `oldRows - newRows` rows short. A phone sits in that window
  constantly — the soft keyboard fires `visualViewport` resizes throughout its open animation,
  and every one refits the pane. The retry needs no timer and no frame wait: the first call's
  `onScroll` is what makes `_sync()` republish the real range, so the next one lands.
- Reaching the tail likewise means the *application's* viewport, not only xterm's: scrolling
  the terminal alone lands on a view nobody was looking at, which is what the rail's `^End`
  has always avoided by sending the key on its way past the local scroll. `appOwnsTail` is the
  rule: any backend but `shell`, when it is Claude or is being handed this pane's scrolls. A
  shell is excluded because it owns no viewport and the bytes would land in a half-typed
  command line. The key is sent off the broadcast path: a viewport gesture belongs to the pane
  that was tapped, and it is dropped rather than queued during replay, since a jump that
  arrives seconds late moves the user somewhere they stopped asking for.
- Every in-flow child of `.terminal-surface` names `grid-column:1`, and the surface declares a
  single explicit column. Overlays that share the terminal's cell must stack, never displace:
  auto-placement refuses to put an auto-column item into an occupied cell, so while the column
  was implicit the jump-to-latest chip pushed the terminal host into a second ~200px column at
  the pane's right edge the moment it appeared. The refit that followed collapsed the terminal
  to ~29 columns, resized the PTY to match, and reflowed the scrollback to the top — and the
  chip stays visible while off-tail, so one scroll notch left the pane stuck there.
- Mobile Run is tap-to-open-launcher, hold-to-repeat: a long-press starts the last launched
  backend directly. The click that follows a long-press is swallowed, or the launcher would
  open on top of the session the hold just started.
- Touch long-press in a terminal selects the word under the pointer and drag extends that xterm
  selection. Touch-originated synthetic context-menu events never open the desktop terminal
  menu. Selection release automatically attempts to copy by default; the preference is
  hot-reloadable from Settings.
- A still primary tap or click inside the currently editable agent composer moves its caret.
  Claude's desktop path remains xterm's native mouse handling; on touch the pane synthesizes the mouse pair xterm expects, and xterm encodes the coordinates using Claude's negotiated mode.
  Codex has no mouse mode, so the pane recognizes its live `›`/`!` composer and converges on the tapped terminal cell with redraw-verified, unicast Left/Right input.
  It refuses selections, drags, modifiers, read/select mode, scrollback, stale geometry, and anything outside the detected composer.
  New input or any loss of a stable target cancels the move rather than letting delayed arrows mutate a different screen.
- Narrow and coarse-pointer terminals focus a dedicated native IME bridge. Android composition
  replacements are converted to incremental terminal text and DEL input as they happen, so Gboard
  and other composing keyboards provide live PTY input without xterm's temporary composition box.
  Because that bridge, rather than xterm's internal textarea, owns DOM focus, xterm uses a static
  bar for its inactive cursor on these devices. The first terminal focus briefly initializes
  xterm before focus moves to the bridge; without that bootstrap a normal-screen Codex session
  has no cursor node at all. The bridge's own caret remains transparent; the visible bar is drawn
  at the real terminal buffer position.
  Every Android Enter path (`keydown`, cancelable `beforeinput`, and the final value-delta fallback)
  maps to the same backend-aware payload: Claude/Codex receive `ESC+CR` to insert a composer newline,
  while shells retain `CR` submit. Agent submission is the fixed rail **Send** action, never the
  soft-keyboard Enter key.
- A terminal pane is three rows: the header bar, an optional read-aloud player strip, then the
  terminal surface (terminal + action rail). The rows are placed explicitly so the middle track
  collapses to nothing when no strip is rendered.
- The pane tools row carries `note`, `queue[:N]` (agent sessions only — focuses that session
  and opens the drawer's Queue tab on it, the count is its pending items;
  `features/prompt-queue.md`), and the `⋯` session menu. Every labelled chip here **reports
  state** — empty/written/open, or a pending count — which is the bar for occupying a bar that
  also has to fit the session name and path; `proc` did not, and went to the drawer.
- The Queue tab's `auto:` line is a status as much as a control: on/off and the bounds
  actually in force (sends left, minutes left, quiet hours, why it is off), disclosing the
  toggle and the separate "accept agent messages armed" switch. It is unavailable — with the
  reason shown — when the install's master switch is off (`features/auto-delivery.md`).
- The **Mailbox** is the Queue tab's `inbox`/`outbox` scopes (app menu → Mailbox… lands
  there), app-level rather than per-Project: messages point at sessions across every Project,
  and those scopes carry the two controls that must be one gesture away on any device —
  pause all auto-delivery and report an unsafe delivery (`features/agent-messaging.md`).
- The pane header is `[status] [cwd] [voice] [tools]` and **must stay one row**. It is a grid
  with `grid-auto-flow:column`, which is what enforces that: without it, an item beyond the
  declared column count auto-places into a *second row*, and the voice group is a
  variable-length chip set, so the tools would silently drop under the status line. Overflow is
  absorbed by `.pane-voice`, which scrolls horizontally with a trailing fade, never by growing
  the bar. Phones drop the cwd column and cap the status width so the group keeps room.
- Every terminal has an in-flow action rail at the bottom of its pane on desktop and mobile,
  below the terminal rather than over it. It carries a keyboard toggle plus terminal-key
  buttons (Esc, Enter, Tab,
  Ctrl-C, and the four arrows), then Attach (agent sessions), Copy reply, Paste, and the
  clipboard-history picker (`Clip`),
  then a status readout. On narrow/coarse Claude and Codex panes, the configurable Enter item is
  removed from the scrolling strip and replaced by an always-visible **Send** end-cap in a separate
  grid column. The four arrows are non-focusing pointer controls: press sends once, then a 350 ms
  hold repeats every 75 ms until release or cancellation. Preventing pointer focus keeps an open
  mobile keyboard open; keyboard and assistive activation remain one-shot. A touch beginning on
  an arrow steers the terminal rather than horizontally scrolling the rail.
  The inner strip alone owns horizontal overflow, so Send does not scroll, cannot be
  reordered/hidden, and remains reachable after soft-keyboard Enter becomes newline-only. Shell and
  desktop Enter behavior is unchanged. Rail items now carry a **placement**: `strip` (here), `drawer` (the
  utility drawer's Commands tab), or `both`. That replaces the old enabled/disabled toggle, which
  was a bad model — the strip is horizontally scarce, so "off" was the only way to get an item out
  of it, and several useful built-ins (Home/End, ^Home/^End, newline, clear input, `/rewind`)
  shipped hidden for want of room. Those now ship *on*, in the drawer, and custom skills and slash
  commands default there too rather than crowding the arrows off the strip. The two regions are
  independent surfaces, not one slot: an item you hammer under the terminal can also carry its
  full label in the drawer, so the settings row edits them as two toggles (`Rail`, `Panel`) rather
  than a three-way select. Clearing both is what hides an item (`enabled: false`); the settings
  row is what dims. Saves predating placement are migrated on read: `enabled: false` meant "not on
  the strip", so it becomes a drawer item.
- Rail items come in five kinds: terminal `key`, built-in `action`, literal `text`, `slash`
  command, `skill`, and `prompt`. A `prompt` item is a *pointer* at a prompt-library template
  (`prompt-library.md`) — it stores the `scope:id` key, never the body, so the button always
  injects the template's current text and cannot drift into a stale copy. It is the one item type
  whose activation is asynchronous (the body is fetched on click) and the one that can never
  submit, which is the library's own contract. Templates carrying `{{variables}}` have nothing to
  inject yet, so the button opens the drawer's Prompts tab preselected with its fields expanded
  rather than pasting a half-rendered body. Both hosts route through `promptRail.ts` and insert
  over the `mux:terminal-action` bus, so the pane stays the single owner of terminal writes.
- The command-rail settings rows are a name-first grid: the item name owns the elastic column and
  wraps rather than truncating (it used to share a fixed column with the placement select and
  clipped to an ellipsis), with type/payload preview beneath it and the toggles auto-sized on the
  right. Placement chips are blue ("this region renders it") and the device/backend filter chips
  green ("this context is allowed to"), because one accent across seven chips read as a single
  toggle set. Phones keep two grid rows per item: name + reorder, then the toggles.
- The Commands tab is session-scoped but renders outside the terminal pane, so it activates items
  over the same `mux:terminal-action` bus (`sendKey`, `insertText`, `copyReply`, `copyResume`,
  `branch`, `relaunch`, `endSession`): the pane stays the single owner of terminal writes, so
  broadcast, replay, and read/select mode keep applying. With no terminal focused the tab says so instead of rendering
  dead buttons. Keys inject
  raw bytes on the normal input path. The built-in newline uses `ESC+CR`, the legacy sequence both
  Claude and Codex bind to composer newline; raw LF works in Claude but not Codex.
  The rail overflows on narrow panes and scrolls horizontally; it never wraps.
  The scrollbar stays hidden: endpoint-aware gradient chevrons overlay the strip without taking layout space, appear only when content remains in their direction, page to a command boundary on click, and keep focused commands clear of the overlays.
  Touch drag, native horizontal trackpad input, and translated vertical mouse-wheel input remain direct scrolling paths.
  Voice controls are not
  here — they are in the pane header (`voice.md`), because the rail is a scroller the user pages
  through and they kept scrolling out of reach.
- Below the configured rail items, an agent session's Commands tab lists **the skills that
  session's CLI can actually see** (`GET /sessions/{id}/skills`, `interfaces.md`) — the vendors'
  own `SKILL.md` directories, not swe-mux prompt templates or rail items. These are *discovered*,
  never configured here: the list is a window onto the CLI's state, so it groups by where each
  skill comes from (project / global / plugins / bundled) rather than by anything the user
  arranged, and a Rescan button refetches instead of a save button writing. Clicking inserts the
  invocation without submitting, over the same bus — a skill invoked bare runs with no context,
  and the point of typing it into a live composer is to say what it should act on.
- Three things about that list are load-bearing, because a skill list that looks complete and is
  not is worse than none. **Claude's built-in skills are compiled into the CLI binary** and cannot
  be enumerated from disk (`/skills` is a TUI-only command), so the tab discloses that in place
  rather than implying `/review` and `/security-review` do not exist. **A skill newer than the
  running agent process is flagged `new`**: it is on disk, the CLI read its skills at startup, and
  typing the invocation will not work until the agent is relaunched. **Codex's explicit-only
  skills** (`policy.allow_implicit_invocation: false` in `agents/openai.yaml`) are flagged
  `explicit` — real and invocable by name, but the model never reaches for one itself. Disabled
  plugins, unreadable entries, and truncation are named in the same footer note; nothing is
  dropped silently.
- Skills are scoped per session, not per Project, because the CLI resolves repo skills from its
  **live cwd** — a session sitting in a worktree sees a different set than one in the primary
  checkout of the same repository, and the tab refetches when that cwd moves.
- **End session** is the rail's one destructive item, so it ships in the drawer rather than on the
  strip — a kill button one mis-tap from the arrow keys is the wrong default even behind a confirm.
  Both hosts route it to the workspace's `session.kill` command, which already owns the two-click
  confirm (2 s window), the pty stop, and the layout/focus cleanup; neither host reimplements any
  of that. The armed id is broadcast on `mux:kill-armed` (App, on every change to `confirmKillId`)
  because the pane is memoized against callback props and could not otherwise see it: both the rail
  button and the drawer button read that broadcast instead of running a second timer, so their
  label can never disagree with what the next click does. On an exited or crashed session the
  button relabels to Remove, matching the command's own fallback. The drawer deliberately stays
  open on the arming click — closing it would leave nowhere to make the second one.
- Copy reply, Branch, and Paste render as icons alone; every other action keeps its text. The
  rail is width-starved — those three cost 74 px each on desktop and 96 px on a phone, which is
  most of a screen's worth of rail before the terminal keys begin — and their marks (offset
  sheets, git branch, clipboard) are conventional enough to read without a word. **Copy resume
  deliberately keeps its label**: a copy glyph cannot distinguish it from Copy reply, and the
  two sit side by side. Icon buttons size like keys (30/44 px) and carry an explicit
  `aria-label`, since the title attribute is not a name on touch.
- The Markdown editor carries a *second, separate* rail: Continuity's own, which the vendored
  editor renders only on touch-primary devices and persists per device in `localStorage`. swe-mux
  registers one host action on it (`mux:send-to-agent`) instead of projecting its `RailItem`
  catalog there: the two models share nothing (no backend/platform filters, no placement, no
  prompt pointers, a different store), and Continuity's rail hides while an editor is read-only.
  Desktop therefore gets the same action as a `→ agent` button in the resource pane header
  rather than by forcing Continuity's rail on, which would drop its whole 48 px formatting strip
  onto every note. See `project-resources.md` for what the action sends and where.
- Rail buttons must not carry a resting selected appearance. Hover styling is gated to
  hover-capable pointers, because touch browsers retain `:hover` on the last tapped element
  until another element is tapped, which reads as a stuck selection. Activation feedback is a
  one-shot pulse cleared when its animation ends, so every button returns to rest on its own.
  The pulse fires on **click, never pointer-down**: the rail is a horizontal scroller, so a
  finger landing on a button is as likely to begin a drag as a tap, and pulsing on contact made
  every swipe look like it had selected whatever button it started on. Only a real activation
  produces a click; a scroll drag produces none.
- The keyboard toggle is also a registered command (`terminal.keyboardToggle`), not only a rail
  button, so it can be bound to a gesture or a key and reached from the palette. It routes over
  the same session-targeted terminal-action bus as copy/paste/find.
- That command carries the default two-finger swipe-down gesture, which makes it the control a
  touch user reaches for to push the keyboard away *wherever they are* — and outside a terminal
  it used to be a no-op, leaving a note editor with no way to lower the keyboard at all. So it
  is available with no terminal focused, and when the keyboard is held up by anything other than
  the terminal's own live input it blurs that instead of toggling read/select mode on a terminal
  the mobile workspace is not even showing. With nothing holding the keyboard up it is still a
  plain toggle, which is what turns read mode back off. `keyboard.dismiss` is the same dismissal
  with no terminal mode behind it, for binding a slot that should only ever hide the keyboard.
- The keyboard toggle is a touch-only read/select mode: while on, tapping the terminal selects,
  scrolls, and positions without raising the on-screen keyboard, so selection auto-copy and
  Paste work keyboard-down; tapping the toggle again restores typing. Sending a key from the
  rail in this mode never raises the keyboard.
- Paste uses the browser clipboard when permitted and otherwise opens a focused native-paste
  target. Claude and Codex
  rails prefetch normalized transcript text so Copy reply runs inside the button gesture rather
  than typing `/copy` or waiting for OSC 52. Reply extraction walks back to the newest turn with
  meaningful assistant text; provider control acknowledgements such as `No response requested.`
  never replace the last copyable reply.
- Claude/Codex terminal bodies also accept OS file drops and copied-file paste, while the visible
  **Attach** rail button supplies the same multi-file picker on desktop and mobile. Upload status
  is reported in the rail. A general file inserts a quoted workspace-local path into the draft;
  a recognized image keeps the provider's native image reference. Neither path submits, and
  attachment input never follows terminal broadcast to sibling panes.
- Terminal copy is success-preserving: keyboard, menu, automatic selection, the action rail, and
  provider OSC 52 requests retain the exact text until a write succeeds. Blocked or insecure
  clipboard contexts open a prepared fallback automatically, leaving one explicit Copy tap.
## Utility drawer

- The right-edge **utility drawer** is where the app's lookup and injection surfaces live, so they are one gesture on mobile or one visible click on desktop away instead of two menu levels deep.
  The canonical default order is **Clipboard**, **Commands**, **Prompts**, **Queue**, **Transcript**, **Agent**, **Files**, **Notes**, **Context**, **Git**, **Processes**, **Mailbox**, and **Alerts**.
  Users may distribute those singleton tabs across a recursive arrangement, but the default order groups by what a tab acts on.
  The first
  four are the same verb - text into the focused session - and Transcript reads the same session
  back. Agent closes the session-scoped block with a passive view of the selected CLI environment.
  Files and Notes are the **navigators**:
  project-scoped indexes over documents rather than surfaces that type into one. Files opens what
  you select into a pane; Notes can do that too but opens *into the drawer* by default, because
  reading or adding to a note without leaving the session on screen is the whole point of it on a
  phone. Context is the read-only inventory of root agent instructions and provider learned
  memory; like the drawer's note editor it renders inside the drawer, and unlike it never opens a
  pane and never writes. Git closes the Project-scoped block without joining the navigators:
  it reads the repository behind the Project
  (branches, worktrees, dirty/upstream state) and opens nothing into a pane — see `git.md` for
  what it shows and the mutations it is allowed. **Processes** closes that block for the same
  shape of reason: Project-scoped, reports rather than opens, and see below for the split it
  represents. Notifications is neither, and sits last. Session history, usage, and automation
  stay modal, as does the process *inspector*: they are wide, table-shaped surfaces that a
  ~380 px column serves badly.
- The injection tabs share the verb but not the routing. Clipboard inserts land in the
  last-focused surface, editor or terminal. **Prompts** inserts are terminals-only and its rows
  additionally answer right-click / long-press with a target menu (a live agent session in this
  Project, or a new Claude/Codex one) — see `prompt-library.md`. Text meant for an agent must not
  be able to edit whichever note or file the user happened to open last.
- **Queue** is the odd one of the four: it does not inject, it *stages* - text held for the
  focused agent until a delivery is explicitly asked for (`features/prompt-queue.md`). It is
  here rather than in a workspace tab or a modal because the decision it exists for ("is now a
  safe moment to interrupt this agent") is read off the terminal, and only the docked column
  leaves the terminal on screen.
  Queue remains strictly session-scoped.
- **Mailbox** is an application-scoped provenance and delivery-state view over queued messages from every Project and session.
  It filters by explicit authorship, Project, and target session; owns the fleet-wide pending badge and emergency auto-delivery controls; and opens a target's Queue without pretending the global list is session-scoped.
- **Transcript** is an *inert* session surface: the focused session's conversation
  as prose you can scroll and copy, without touching the live terminal or scrolling it back.
  Deliberately no composer, no insert, no send. Mixing those actions into the surface for reviewing
  what already happened is how a stray tap
  becomes a message nobody wrote. Copy is the only verb: per message, or the whole conversation
  with speakers.
  The top-bar search filters the already loaded messages with literal, case-insensitive matching, highlights every occurrence, and leaves whole-conversation copy unchanged.
  Search owns a temporary scroll position and clearing it restores the reader's prior place.
  A message's copy control sticks to the body's top-right edge while that message is being read, then yields when the message leaves the viewport.
  An explicit Show more or Show less choice is device-local and keyed by session, agent run, and stable message identity, so appending messages, a moving transcript window, or navigating away does not reset it or apply it to a different message.
  Search temporarily showing a full matching message does not change that saved choice.
  Only expanded identifiers and recency timestamps are stored, and a 500-entry cap bounds stale state without storing transcript text.
  It is a drawer tab and not a pane because the point is to read *beside* the
  terminal rather than in place of it.
  Its owning stack unmounts the body when another tab is selected there, which is why the scroll place is kept outside the component.
- **Agent** is the session-scoped Agent Environment surface (`agent-environment.md`).
  It shows retained launch identity and passively discovered built-in tools, skills and commands, MCP configuration, plugins, hooks, custom agents, policies, feature overrides, sources, and diagnostics.
  Scope, origin, state, and completeness remain separate labels.
  Opening it never starts a configured server or executes extension code, and it has no mutation or terminal-insertion action.
  Source drift is measured against the current CLI process generation, not the latest conversation rollover.
- **What the reader shows is a filtered conversation, not the transcript.** Tool calls are gone
  entirely — not collapsed, not summarised. So is CLI machinery that both providers write into
  the transcript as `user` records: slash-command expansions and their output, `!` shell escapes,
  skill bodies injected mid-conversation, interrupt markers, Claude's `<system-reminder>` spans
  (stripped from the prompt that carries them rather than hiding it), and Codex's
  `<environment_context>`. The opening `# AGENTS.md instructions` block **stays**: it is the brief
  the run was given, and reading a Codex conversation without it starts in the middle.
  Classification is the daemon's (`transcript_view.conversation_view`), so history search can
  inherit the same distinction later, and it reads Claude's per-record provenance
  (`origin.kind === "human"`, `isMeta`, `interruptedMessageId`) rather than matching wrapper tags,
  which is both more accurate and version-durable. **The rules fail open**: a record is hidden
  only on positive evidence that it is machinery, because leaking a `<local-command-stdout>` is a
  blemish while hiding something the user typed is the surface lying about the conversation. The
  count of what was withheld is shown, so the filtering is never invisible.
- An agent's turn arrives as several records whenever a tool call interrupts it. With the tool
  records gone those fragments are one thing the agent said, so they are **merged into one
  message** — otherwise the copy button would copy "Let me check the registry." instead of the
  answer.
- Reading placement follows one rule: open at the newest message, and let only a reader *already*
  at the bottom be carried along by new ones. Scrolled up, the position holds and the arrival
  becomes a "N new" button. A live log that yanks the column mid-sentence every time an agent
  speaks cannot be read at all, which is the failure this tab exists to fix. Returning to a
  session still focused restores where reading stopped; moving to another session starts at its
  newest message, and nothing is remembered per session beyond the one you are on.
- It refreshes when the transcript observer consumes a user message (`transcript_message`) and at
  the assistant turn boundary (`turn_ended`), never on a timer. The first event makes a submitted
  prompt appear without waiting for the response; the second collects the completed answer.
  Polling would re-read a whole transcript to learn nothing for most of an agent's working minute.
  A pane whose conversation rolled over
  (`/clear`, `/new`) reloads onto the new run; the retired conversation stays in History, which
  is also where anything older than the loaded window lives.
- A live auto-named agent's session menu includes **Regenerate title**. It requests a fresh
  generated title from the latest observed user request. A manual Rename remains authoritative and
  removes this action because automation never overwrites a user title.
- When the transcript observer's link to the PTY has gone stale, the tab **says so** rather than
  presenting another conversation as this session's. Everywhere else that fault reads as odd
  telemetry; here it would be a stranger's words under this session's name.
- **Files** is a navigator, not a peer of the terminals it opens files next to, so it costs a
  drawer tab rather than a permanent workspace tab. As a pane it forced the layout to route
  every placement rule around it and it seeded every new Project with a narrow column
  most people ignored. Nothing is lost by the move: expanded-folder state was already persisted per Project
  outside the layout, and on desktop the drawer is an in-flow column, so a file row can still be
  dragged onto any pane.
  Files can remain visible beside Clipboard or another utility body when the user places them in separate drawer panes.
- **Notes** is a flat Project-owned collection *and* an editor, and a note is open in exactly one of the two hosts at a time.
  A non-wrapping sub-tab rail pins Scratchpad first and then shows every note in the active Project in stable creation order.
  These tabs are the primary navigation and cannot be closed.
  The selected tab scrolls into view automatically, and compact scroll controls handle ordinary overflow.
  A separate searchable browser handles large collections, all-Project discovery, and note management.
  Scratchpad is global, has no rename/delete controls, and uses the same drawer and workspace-tab placements as Project notes.
  The browser lists explicit notes, including empty notes, searchable over title, Project, and excerpt and scoped to this Project or to all Projects.
  It creates and renames notes through title prompts.
  Selecting a rail tab or browser row opens that note **in the drawer**; `⇥` moves it to a workspace tab instead.
  Each note row places a two-click inline delete control immediately before `⇥`.
  Desktop right-click and guarded mobile long-press expose open, rename, and revision-checked delete actions.
  Deletion submits the revision carried by the listing, refuses a concurrent edit with `409 revision_conflict`, logs the user action, and emits the normal note-change event with revision `missing`.
  An open clean editor follows that event to a deleted state; an editor with unsaved local work keeps its text and reaches the existing revision-conflict path instead of being silently cleared.
  Deleting the selected note selects the next Project note, then the previous note if needed, and falls back to Scratchpad only when none remain.
  Terminals and History do not create or own notes.
- **Why one host at a time is a rule and not a preference.** `noteSaveQueue` keys one entry per
  `(scope, resource)` at module scope, so two mounted editors on one note share it: each submits
  its whole document, newest wins, and the loser's text is dropped with no conflict for the daemon
  to detect, since the revision each holds is correct. Mounting the second is worse — its load
  calls `reset`, which discards whatever the first had pending. Two *devices* are safe by
  contrast (separate queues, separate revisions, so the second write 409s into the ordinary
  conflict banner); only same-browser duplication is silent.
  Selecting a note for the open drawer makes its pane leaf render an "open in the panel" placeholder.
  Placing that note in a pane closes the drawer before the pane editor takes ownership, while retaining the remembered Notes sub-tab.
- **The selection and temporary ownership are device-local and never touch the layout.** `project.layout` is persisted
  server-side and shared, so removing the leaf would let a phone rearrange the desktop's panes.
  The leaf keeps its slot and only stands down while the drawer owns the note.
  The selected sub-tab is stored per Project under `mux.drawer.note.v1`.
  It survives drawer close, utility-tab and session changes, Project switches, and reloads.
  Closing the drawer ends editor ownership but does not erase the selection, so a placeholder never points at a hidden panel and reopening resumes the same tab.
  Moving the selected note to a pane also closes the drawer and retains the selection.
- **The Notes body is the one drawer body kept mounted across tab switches**, hidden rather than
  unmounted. Both reasons are load-bearing: an editor unmounted on every switch loses cursor and
  undo history, and `insertTarget` refuses a detached editor handle, so switching to Clipboard to
  paste *into the note* would route the paste to a terminal instead — silently, into an agent's
  prompt. Keeping the body
  mounted is what makes hosting an editor safe. No other tab needs it, so no other tab gets it.
  Moving a note between hosts is still an unmount and a remount, which is lossless because the
  save queue outlives both: the arriving editor adopts any text the daemon has not acknowledged
  (`pendingText`) instead of the copy it was just served.
- On mobile, an insert normally closes the drawer, since it covers the terminal the text was for.
  When the text landed in the note the panel is hosting, it stays open and returns to the note
  instead — closing would hide the result that was just asked for. Desktop does not move at all,
  because the column sits beside the workspace and a second insert is the common next action.
- **Context** is titled **Instructions & Memory** and remains the Agent Context surface (`agent-context.md`). It shows Project-root
  `CLAUDE.md`/`AGENTS.md` in an initially expanded disclosure, fixed global
  `~/.claude/CLAUDE.md`/`~/.codex/AGENTS.md` in an initially collapsed disclosure, and one
  initially collapsed **Memories** disclosure badged with the provider file count. All three
  share the same high-contrast file-row surface; bodies are read-only.
  Fine-pointer desktop rows backed by real files expose **Open in default explorer** on
  right-click, using the Files browser's native reveal behavior; mobile keeps its native
  context-menu behavior.
  One `sync…` button opens a modal containing both deliberate Project-root whole-file copy
  directions, normalized diff confirmation, and revision-guarded restore points. Global
  instructions and learned memory are never write targets; nothing watches or synchronizes in
  the background.
- **Processes** is the *watch* half of process inspection; the modal inspector keeps the *act*
  half. The split is what makes a column viable at all. Watching is "which of my sessions are
  running something, is that dev server up" — a handful of numbers and a link, and a question you
  ask with a terminal in front of you, which is the same argument that put Queue here. Acting is
  the full tree with parent lineage, evidence state and confidence, and the
  interrupt/terminate/terminate-tree row; those need width to read and a visible confirm step to
  be safe, and a 300 px column with a confirm-on-second-click destructive button is how someone
  kills the wrong tree. **Nothing in the tab terminates anything.** `Full inspector` in the footer
  opens the modal, prefiltered to whatever the tab is scoped to.
- Rows are per session, not per process: a session's tree is mostly bookkeeping (`cmd`, `conhost`,
  the agent CLI), so a per-process column would be a wall of noise around the one row that
  matters. Each row is a rollup (process count, CPU, working set) plus the loopback servers that
  session is listening on, which are the only actionable things here — `preview` registers one as
  a tab beside its session, `copy` takes the URL. Ended processes are dropped rather than greyed:
  they support no action here and are already excluded from every total in the app.
- Scoped to the active Project by default, with **the focused session's row pinned first and
  marked**. That combination is deliberate. Session-scoped would read empty most of the time (most
  sessions are an agent CLI and a conhost) and would churn its whole body on every focus change,
  the same objection that sank a focus-following Notes tab; Project-scoped answers the question
  people actually have, and the pin answers "what is *this* session running" without a scope
  change. `All projects` is one click away and the choice survives a tab switch.
- **It starts no poll of its own**, reading the fleet sample `App` already refreshes for the
  sidebar's resource summary and its spawned-server rows. The reconcile walk behind that data
  holds the GIL on Windows (`processes-and-previews.md` § Sampling cost), so a panel left open
  all day must cost the daemon nothing extra. Any future addition here inherits that rule.
- The pane header lost its `proc` chip when this shipped. It was the only pane tool carrying no
  state of its own — `note` reports empty/written/open, `queue` its pending count — so it was pure
  navigation, and on a phone it cost 40 px of a bar that also has to fit the session name and
  path. The session context menu and the palette still open the inspector directly.
- **Alerts** shows open attention records first and dismissed ones only on request. Each row
  dismisses (or restores) and the footer clears the lot; both write `read_at` server-side, so
  the state follows the user to every device and to the dashboard inbox rather than being a
  per-browser hide. The tab lists what the daemon retains for 90 days, which made it
  append-only from the one surface a human actually reads: a single detector firing on a
  normal workflow buried every record that mattered. Dismissing deletes nothing — see
  `automation.md`.
- A note tab that appears and disappears with focus was considered and rejected: the desktop icon
  rail earns its keep by having fixed positions, a vanishing tab has no affordance for *creating*
  a note (the pane `note` chip already owns empty/written/open), and a Notes tab that followed
  focus would swap the document out from under someone mid-sentence. "Only when it exists"
  belongs to a row in a list, which is where it already lived.
- One component, two renderings (`UtilityDrawer.tsx`). **Mobile** is an overlay with a scrim,
  mutually exclusive with the navigation sidebar (opening either closes the other). **Desktop** is
  an in-flow column of the workspace grid: the pane tree shrinks rather than being covered, because
  covering a terminal in a tiling workspace is exactly backwards for a panel you opened to work
  *with* that terminal. The mobile overlay is an uncapped `90vw`, leaving a narrow strip of context
  and scrim on every phone and small tablet.
  Desktop width is pointer-resizable and device-local, like the sidebar's.
  It has no fixed maximum; its live maximum is the available viewport width after reserving the navigation chrome, utility rail, and a 150 px main workspace.
  Dragging its divider below 260 px previews collapse, and reversing the same drag past 280 px reopens it before release.
- Every width change reflows the pane tree and refits its terminals, which sends a resize to each
  PTY and makes agent TUIs redraw. Width persists globally for the device and the drag commits on
  pointer-up rather than per-frame.
  Collapse preview is transient during the drag; only the final open or collapsed state is written on release.
  Restoring a Project whose desktop drawer was expanded performs one deliberate reflow as part of restoring that Project's workspace presentation.
- **The drawer arrangement is a separate recursive utility workspace.**
  `drawerLayout.ts` owns a binary split tree whose leaves are tab stacks, and every registered utility tab occurs in exactly one stack.
  The tree supports nested horizontal and vertical splits, stable split ratios, independent pane rails, and any arrangement bounded by the number of registered tabs.
  It is not part of Project layout v7 and never adds utility leaves to Project layout PATCHes, SQLite state, workspace focus traversal, warm terminals, or the Project mobile projection.
- The complete tree is device-local and global across Projects under `mux.drawer.layout.v1`.
  Drawer width is also global per device.
  Each Project stores only `selected_tabs`, `focused_tab`, and `desktop_expanded` under `mux.drawer.projects.v2`.
  Switching Projects preserves geometry, membership, order, ratios, and width while restoring that Project's selections and desktop visibility.
  A transient no-Project presentation keeps app-scoped tabs usable before a Project exists.
- The former `drawerTabs` server setting is read once as migration input and is no longer written by drawer operations.
  The former `mux.drawer.projects.v1` and `mux.drawer.tab.v1` values migrate into the v2 presentation map only after valid new serializations succeed.
  Parsing repairs malformed branches, duplicate or missing tabs, duplicate node IDs, invalid ratios, stale selections, and excess depth without losing registered tabs.
- Desktop renders every saved stack simultaneously, with one selected body per stack and one independently scrolling rail.
  An overflowing pane rail hides its scrollbar and exposes endpoint-aware fade chevrons only where more tabs exist.
  The chevrons overlay the rail instead of reserving space, click-scroll by tab boundaries, and preserve wheel, trackpad, touch, keyboard, and drag-reorder behavior.
  The focused utility tab identifies the focused drawer pane for reopen, cycling, mobile selection, and geometry commands without taking terminal input ownership or changing Project workspace focus.
  Session-scoped bodies follow the focused session, Project-scoped bodies follow the active Project, and app-scoped bodies remain independent.
  Every body shows its current scope because several scoped bodies can be visible at once.
- Tabs move only through pane rails.
  Dragging across a rail gap performs exact insertion, dropping in a pane center joins that pane, and dropping on a pane edge creates a left, right, top, or bottom split.
  Moving the last tab out of a stack collapses its redundant parent split immediately.
  The desktop outer launcher is a depth-first mirror and activation control only, so it never becomes a second layout editor or content host.
- Dragging uses the shared pointer contract with a 5 px threshold, pointer ownership after activation, one fixed ghost, direct DOM indicators, a prospective tree in refs, and one commit on pointer-up.
  Escape, invalid targets, pointer cancellation, lost capture, window blur, Project changes, breakpoint changes, drawer closure, and unmount cancel without persistence.
  Mobile exposes no drag targets or split separators.
- Each internal split separator supports pointer resizing, arrow keys, Home, End, and double-click reset to an equal ratio.
  The outer divider supports pointer resizing, arrow keys, Home, End, and double-click reset to the default drawer width.
  `drawer.moveLeft`, `drawer.moveRight`, `drawer.moveUp`, and `drawer.moveDown` provide keyboard geometry changes, while `drawer.next` and `drawer.previous` cycle within the focused utility pane.
  `drawer.resetLayout` restores one canonical stack and reconciles every Project presentation.
  Existing bindings for `drawer.resetTabs` migrate through a hidden alias.
- Settings > Appearance exposes `drawer_tab_display` as **Icons** or **Titles**, defaulting to Icons.
  The setting applies to every pane rail, the mobile projection, and the desktop launcher.
  Icon mode uses `DRAWER_TAB_ICONS`, while title mode uses the short `DrawerTab.label`; neither mode renders both marks.
  Title rails remain one-line scrollers with the same endpoint-aware overflow controls, and title mode widens the outer launcher through `--utility-rail-width`.
  Queue and Alerts badges, scope dots, accessible names, tooltips, selection, focus, and drag state remain intact in both modes.
- Mobile renders one flattened depth-first rail and one body from the same desktop tree without rewriting tree membership, stack IDs, directions, ratios, or ordering.
  Selecting a mobile tab updates only its owning stack's Project selection and the Project's focused tab.
  Returning to desktop restores the exact recursive tree.
  The close control stays in stable drawer chrome outside the scrolling rail.
- The focused utility tab is remembered per Project and device, so
  `drawer.toggle` (default two-finger swipe **left**, the swipe that drags a right-edge panel in;
  the rightward swipe keeps the left-edge sidebar) reopens where you left off, while `drawer.<tab>`
  commands open one tab directly and close it if it is already showing.
- On mobile the toolbar's right-corner toggle is the drawer's only *visible* entry point because the desktop launcher is hidden there.
  Without it, the panel would be reachable only by gesture or command palette, neither of which announces itself.
  It mirrors the nav toggle at the opposite
  edge because the two full-height drawers they open are mirror images, and an edge toggle
  sitting anywhere but its own edge reads as unrelated to the panel it opens; Run gives up the
  corner for it and is found by its label instead. Its icon is the panel, not any one tab's
  mark, because like `drawer.toggle` it opens whichever tab was last used.
- Acting closes the drawer on mobile, where it covers the surface just acted on, and leaves it
  open on desktop, where the column sits beside that surface and a second insert (or a second
  file) is the common next action. Mobile overlay visibility is transient and never overwrites a
  Project's remembered desktop expansion. Crossing the responsive breakpoint closes the overlay;
  returning to desktop restores the Project's docked state. One rule, applied to inserting text
  and to opening a file or a note.
- **Clipboard history** is a shared, bounded ring of every text copied *inside* swe-mux, and the
  drawer's first tab. Capture is installed once at boot in `clipboardHistory.ts` rather than at each copy
  site: `Clipboard.prototype.writeText` is wrapped (which covers all ~30 in-app calls *and* the
  vendored Continuity editor, since it calls the same global) plus a capture-phase `copy`/`cut`
  listener for the paths that never reach `writeText` (plain DOM selections, the editor's
  `execCommand` fallback). One gesture can trip both hooks, so identical text inside a short
  window is collapsed client-side and the daemon promotes an existing entry instead of
  duplicating it. Nothing reads or polls the OS clipboard: copies made in other applications
  never appear, by design.
- On mobile the sidebar and the utility drawer are mutually exclusive: opening either closes the
  other. They are both full-height drawers over the workspace entering from opposite edges, so two
  open at once leave no workspace between them and bury one under the other's scrim. The rule is
  enforced in the state setters themselves (`App.tsx` wraps both `useState` setters), not at each
  call site, so every entry point — gesture, command, nav toggle, tutorial — inherits it. Desktop
  is unaffected: there the sidebar is an in-flow column the drawer's own column never covers.
- Opening either mobile panel also lowers the soft keyboard, in the same setters and for the same
  reason: the keyboard is held up by a field that is now behind the scrim, and it covers up to
  half of the panel that just opened. There is no API for "hide the keyboard", so the focused
  field is blurred (`mobileKeyboard.ts`; only fields that actually raise a keyboard, so focus on a
  button or a readonly field keeps its place in the tab order). This is not the terminal's
  read/select mode: nothing is made sticky, and tapping the terminal or any field once the panel
  is closed raises the keyboard again.
- Finding that field means walking **into open shadow roots**, not reading `document.activeElement`.
  Focus inside a shadow root retargets to the host, so the Continuity editor behind every note and
  `.md` file — a `<textarea>` inside `attachShadow({mode:'open', delegatesFocus:true})` — reports
  as a bare custom element that raises no keyboard, and the blur above silently did nothing over a
  note. `deepActiveElement` descends `activeElement.shadowRoot` until it stops moving (a closed
  root has no `activeElement`, so it stops at the host, which is the old answer and the best one
  available). Depth is capped so a malformed tree cannot spin a handler that runs on every gesture.
- The panels also dismiss any keyboard already visible at **touchstart, as soon as a second
  finger lands**, rather than waiting for the resolved command at touchend. Two fingers are never
  text entry, so the early blur is safe. Continuity 0.2.18 separately owns note-touch
  arbitration: pointerdown does not focus, a resolved tap places the caret and focuses, and
  scroll/cancel/long-press paths leave the keyboard closed. swe-mux adds no shadow-DOM or caret
  hit-testing workaround; single-finger touches pass to the editor unchanged.
- Spawning a terminal closes the mobile sidebar. Every launch focuses the new tab, so every
  launch has to clear what is covering it — launching from a sidebar Project row otherwise
  focused a tab the drawer was still hiding, which reads as "the Run button did nothing". This
  lives in `spawnTerminal` rather than at the Run menu's call site, so every entry point
  (sidebar row, toolbar Run, palette, keybinding, custom launcher) inherits it, and it runs
  with the optimistic state so the pending terminal is visible immediately. Project Actions
  take the same step in `attachActionSessions`.
- The drawer is deliberately *not* a modal layer. Inserting targets the surface that was focused
  before it opened — terminal or Continuity note, whichever was last focused (`insertTarget.ts`;
  a detached editor loses the routing to the focused terminal) — so the workspace has to stay
  visible behind it. Desktop therefore gets no scrim (Escape, ×, or the toggle closes it) while
  mobile dims, and the drawer and its scrim are in the gesture recognizer's allowlist so the same
  swipe that pulled it in pushes it back out. Clipboard rows carry previews only; full text is
  fetched per entry on use. Row actions are insert (primary), copy to the system clipboard, pin,
  forget.
- Clipboard history's safety properties are part of the feature, not an afterthought: the ring is
  **memory-only by default** (`clipboard_history_persist` opts into the SQLite mirror, and turning
  it back off deletes the rows), secret-shaped copies are skipped rather than stored, oversized
  copies are skipped rather than stored truncated (a partial paste into an agent prompt is worse
  than a missing entry), pinned entries are exempt from eviction/retention/Clear, and everything
  is reachable by anything that can reach the daemon — see `remote-access.md`.
- Shift+Enter and Ctrl+Enter insert a newline in Claude and Codex terminals instead of
  submitting. The browser cannot express either chord in the legacy encoding both agents parse,
  so the pane rewrites them to ESC+CR, the one sequence Claude and Codex each read as an editor
  newline. Shell terminals keep Enter's plain carriage return, and Ctrl+Enter is reserved from
  custom keybindings so no command can shadow the newline.
- Modal focus trapping, keyboard navigation, reduced-motion styling, clipboard recovery,
  resilient WebSocket reconnect, and IME/composition-aware terminal input apply on both desktop
  and mobile.
- Reopening a dormant client must never require the user to work around the UI. The tab that was
  focused when the client went to sleep is the one whose socket or load was in flight, so it is
  also the one that used to be stuck on "reconnecting…" or a fetch error until the user switched
  tabs and back (which forced a remount). Recovery is therefore watchdog-driven, not
  signal-driven: attempts have deadlines, a visible-only poll re-checks, and the terminal badge
  and resource error both carry a "retry" that skips the remaining backoff. The shared policy is
  `liveness.ts` — see `../../technical/frontend/packages.md`.

## Git review modal

- The Git review modal is rendered above the utility drawer without unmounting the Git tab.
- Closing returns to the same Map or Log scroll position, expanded row, and inline preview.
- Desktop bounds the modal to the viewport and provides a file navigator beside the diff.
- Mobile uses the full viewport and replaces the navigator with a compact file selector plus previous and next controls.
- A `ResizeObserver` measures the diff content region.
- Automatic layout is split at 900 CSS pixels and unified below 900 CSS pixels.
- A manual unified or split choice lasts until modal close; narrow manual split scrolls horizontally.
- Wrapping is off by default and is an explicit modal-lifetime toggle.
- Old and new line-number gutters are buttons with visible keyboard focus and accessible labels.
- Click starts a single-line annotation; Shift-click extends only within the same file, side, and frozen patch.
- The annotation composer renders below the selected diff row and supports Save, Cancel, Edit, and Delete.
- Escape closes the modal, focus is trapped while open, and focus returns to the invoking control on close.
- Copy/send results use a compact live region and never cause the entire diff to be announced.
- A local Git event produces a stale banner and retains the frozen patch and annotations.
- Reduced-motion preferences disable review-surface transitions and animations.

## Feature-owned UI

Detailed UI behavior belongs with the owning feature:

- Pane tabs, close behavior, drag/drop, and mobile flattening: `workspace-layout.md`
- Project registry and visibility: `projects.md`
- Notes, Files, ignores, and watches: `project-resources.md`
- Agent runtime and extension inspection: `agent-environment.md`
- Provider selection and reset review: `provider-accounts.md`
- CPU/RSS and Process fleet: `processes-and-previews.md`
- Quota/context/tool evidence: `operational-telemetry.md`
- Automation navigation and diagnostics: `automation.md`
- Project task discovery and trust: `project-actions.md`

## Diagnostics

Terminal render faults are a production phenomenon — they happen on the frozen desktop app,
which is a production build — so the instrumentation is not gated on `DEV` alone. Enable it on
any client with `localStorage.setItem('mux:terminal-render-diagnostics', '1')` and reload, then
read the last 100 entries from `window.__muxTerminalRenderDiagnostics` (each is `{at, sessionId,
phase, detail}`); `mux:terminal-render-diagnostic` fires on every append. Phases: `pane_mounted`,
`preconnect_fit`, `attach_ready_sent`, `full_redraw_requested`, `full_redraw_issued`,
`full_redraw_rendered`, `surface_redraw_confirmed`, `webgl_context_lost`, `webgl_load_failed`,
`webgl_render_error`. Note that `full_redraw_rendered` proves only that xterm's `RenderService`
fired `onRender`, which it does whether or not the renderer painted — it is not evidence that
pixels changed.

## Key files

- `frontend/src/App.tsx`
- `frontend/src/ProjectsManager.tsx`
- `frontend/src/Settings.tsx`
- `frontend/src/GuidedTutorial.tsx`
- `frontend/src/tutorial.ts`
- `frontend/src/ProviderAccounts.tsx`
- `frontend/src/ResourceUsage.tsx`
- `frontend/src/TerminalPane.tsx`
- `frontend/src/ProjectRunMenu.tsx`
- `frontend/src/DirectoryPicker.tsx`
- `frontend/src/terminalRenderDiagnostics.ts`
- `frontend/src/style.css`
