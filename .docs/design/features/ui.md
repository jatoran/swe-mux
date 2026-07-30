# Browser shell and interaction

## What it is

The Project-first browser shell around the mixed-view workspace: persistent app identity,
active-Project navigation, provider/resource status, viewport overlays, settings, focus, and
responsive controls.

## Desktop chrome and sidebar

- A persistent top rail places `swe_mux`, sidebar collapse, and daemon activity above the
  sidebar column. Workspace tabs are not global top-rail state; every pane renders its own tab
  strip beside that rail.
- The sidebar is pointer/keyboard resizable from 190–480 px and collapsible. Width and collapse
  state are device-local browser preferences, not Project layout state.
- The sidebar shows only Projects marked for active navigation. Each Project row exposes a fixed
  `Note` / `Files` chip pair, then layout/session rows. An initialized or open session note
  appears beneath its terminal. The two chips open different kinds of surface — the note is a
  pane tab, Files is the drawer's navigator tab — so each reads its active state from where its
  surface actually lives, and the Files chip selects that Project before opening the drawer,
  since the drawer's Files view follows the active Project.
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
- Each quota chip stacks the provider's own mark above its weekly percentage. Weekly is the
  window worth a permanent glance: the 5-hour session window churns constantly, and `fable` is a
  sub-window of one provider's plan rather than a measure comparable across providers. The mark
  is the only thing identifying the row, so it keeps full contrast while the percentage carries
  the shared ok/warn/critical banding. Providers render in the same order as everywhere else.
  The mobile toolbar renders the same chip with the weekly countdown added beneath; the rail has
  no room for that second line, and on desktop the tooltip already carries it.
- The resource chip reports RAM rather than CPU, since a percentage that moves every sample is
  not worth a permanent glance, and abbreviates it (`3.2G`) to fit the strip.
- Popover direction is independent of the condensed trigger, so a rail anchored at the bottom of
  the window still opens upward.
- Git state is Project/session metadata. Worktrees have no first-class sidebar row, creation
  modal, or workspace ownership; the drawer's Git tab is their only surface (`git.md`).

## Menus and overlays

- Scope follows the menu that opened a surface, never a hidden mode. The app menu's unlabeled
  lead block opens History, session notes, Process fleet, prompt library, clipboard history,
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
- Split/new-terminal/move commands use non-clickable labels with directional arrow buttons.
  Only directions valid for the current desktop split tree are enabled. Mobile omits pane
  geometry actions entirely.
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
- Notes configures the shared Markdown editor behind every note and Markdown file: spellcheck,
  Markdown rendering, `Tab`, typography, the touch command rail, and the editor's own shortcut
  policy and per-chord overrides (`project-resources.md`). The chord table is enumerated from
  the editor package rather than hand-listed, so it cannot drift from what the editor binds.
- Terminals exposes `auto | webgl | dom` renderer selection. `auto` preserves accelerated WebGL
  on desktop with automatic DOM fallback; mobile and Codex terminals always use DOM regardless
  of the preference so their scrollback remains stable.
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
  bounds editor, not a schedule: the switch only makes the per-session opt-in available, and
  the runtime state it governs — the emergency pause and the opt-ins themselves — stays in
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
- Mobile's top row is `[nav] [quota] [Project name] [Run]`: navigation and the two provider
  quota boxes at the left, the Project name taking the slack in the middle, Run pinned to the
  far right. It has no separate session dropdown. The Project name is a real button: a single
  tap opens the Project menu (long-press and right-click stay equivalent), because reaching a
  menu should never require a hold on touch.
- The bar is flex with `nowrap`, not grid. Only the Project name flexes and the other three are
  content-sized; expressed as grid, the `auto` track next to the name's `1fr` absorbed the
  slack and left the quota boxes stranded mid-bar.
- Mobile quota is **two boxes, one per provider**, each carrying the weekly percentage and the
  weekly reset countdown (`23%` over `4d8h`). It previously showed a single number — whichever
  provider's weekly window was furthest along — which hid *which* provider was burning and gave
  no sense of how long until it cleared, and a phone has no hover tooltip to recover either.
  Providers render in the same order as every other surface.
- Nav is half width and therefore a glyph rather than the `:nav` label. No word survives at that
  width, and pinning a font size to force one would ignore the user's UI-scale setting, which
  this button is subject to through an `!important` rule. The 44 px touch target survives
  vertically, and the sidebar also opens by swipe, so this is not its only entry point.
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
- That header is a single row on touch: status at the left, note/proc/⋯ at the right. The cwd
  column (including its `last-known::` spawn-cwd marking) is desktop-only — on a phone it is the
  least useful field in the bar, and as a third item in a two-column grid it wrapped the tools
  onto a second row. It is hidden via `.pane-bar>.pane-path`, since the bar's own `.pane-bar>div`
  layout rule out-specifies a bare `.pane-path` override.
- Touch gestures are configurable command slots: single-finger horizontal swipes, two-finger
  horizontal *and vertical* swipes, and a two-finger tap. Only the **single**-finger vertical
  channel is reserved (terminal scrollback / application wheel); two-finger vertical is a real
  mappable slot. Edge- and top-anchored swipes stay with the OS. Slot names are validated
  server-side, so adding one requires the same slot list in `config.py`.
- The gesture recognizer yields to anything that owns horizontal scrolling (the action rail,
  tab strips, the voice strip, plus a generic `overflow-x` scan), and it must yield *cheaply*.
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
- Reaching the tail also means the *application's* viewport, not only xterm's. Two scroll
  positions are stacked in a pane, and a TUI that keeps its own moves that one instead, so a
  purely local scroll lands on a view nobody was looking at and the chip reads as dead. Claude
  keeps such a viewport; Codex does not (it enables no mouse mode at all), which is exactly why
  the same chip worked in a Codex session and not in a Claude one on a phone — while the rail's
  `^End` worked in both, because it happens to send the key on its way past the local scroll.
  `appOwnsTail` is the rule: any backend but `shell`, when it is Claude or has taken the mouse
  — the same signal `mobileDragTarget` already uses to decide who receives a phone's drag. A
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
- Narrow and coarse-pointer terminals focus a dedicated native IME bridge. Android composition
  replacements are converted to incremental terminal text and DEL input as they happen, so Gboard
  and other composing keyboards provide live PTY input without xterm's temporary composition box.
- A terminal pane is three rows: the header bar, an optional read-aloud player strip, then the
  terminal surface (terminal + action rail). The rows are placed explicitly so the middle track
  collapses to nothing when no strip is rendered.
- The pane tools row carries `note`, `queue[:N]` (agent sessions only — opens the session's
  prompt-queue tab, the count is its pending items; `features/prompt-queue.md`), `proc`, and
  the `⋯` session menu.
- The Queue tab's auto-delivery strip is a status line as much as a control: the toggle, the
  bounds actually in force (sends left, minutes left, quiet hours, why it is off), and the
  separate "accept agent messages armed" switch. It is unavailable — with the reason shown —
  when the install's master switch is off (`features/auto-delivery.md`).
- The **Mailbox** overlay (app menu → Mailbox…) is app-level, not per-Project: messages
  point at sessions across every Project, and it carries the two controls that must be one
  gesture away on any device — pause all auto-delivery and report an unsafe delivery
  (`features/agent-messaging.md`).
- The pane header is `[status] [cwd] [voice] [tools]` and **must stay one row**. It is a grid
  with `grid-auto-flow:column`, which is what enforces that: without it, an item beyond the
  declared column count auto-places into a *second row*, and the voice group is a
  variable-length chip set, so the tools would silently drop under the status line. Overflow is
  absorbed by `.pane-voice`, which scrolls horizontally with a trailing fade, never by growing
  the bar. Phones drop the cwd column and cap the status width so the group keeps room.
- Every terminal has an in-flow action rail at the bottom of its pane on desktop and mobile,
  below the terminal rather than over it. It carries a keyboard toggle plus terminal-key
  buttons (Esc, Enter, Tab,
  Ctrl-C, and the four arrows), then Copy reply, Paste, and the clipboard-history picker (`Clip`),
  then a status readout. Rail items now carry a **placement**: `strip` (here), `drawer` (the
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
  raw bytes on the normal input path. The rail overflows on narrow panes and scrolls
  horizontally (touch drag, scrollbar, or mouse wheel); it never wraps. Voice controls are not
  here — they are in the pane header (`voice.md`), because the rail is a scroller the user pages
  through and they kept scrolling out of reach.
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
- Terminal copy is success-preserving: keyboard, menu, automatic selection, the action rail, and
  provider OSC 52 requests retain the exact text until a write succeeds. Blocked or insecure
  clipboard contexts open a prepared fallback automatically, leaving one explicit Copy tap.
## Utility drawer

- The right-edge **utility drawer** is where the app's lookup and injection surfaces live, so they
  are one gesture (mobile) or one visible click (desktop) away instead of two menu levels deep.
  Tabs, in order: **Clipboard**, **Commands** (the rail's long tail), **Prompts**, **Files**,
  **Notes**, **Git**, **Alerts** (notifications). Order groups by what a tab acts on, and the
  groups must stay contiguous so the rail reads as blocks rather than a list. The first three are
  the same verb — text into the focused session. Files and Notes are the **navigators**:
  project-scoped indexes that open a document into a pane instead of typing into one. Git closes
  the Project-scoped block without joining them: it reads the repository behind the Project
  (branches, worktrees, dirty/upstream state) and opens nothing into a pane — see `git.md` for
  what it shows and the mutations it is allowed. Notifications is neither, and sits last. Session
  history, the process fleet, usage, and automation stay modal: they are wide, table-shaped
  surfaces that a ~380 px column serves badly.
- The first three tabs share the verb but not the routing. Clipboard inserts land in the
  last-focused surface, editor or terminal. **Prompts** inserts are terminals-only and its rows
  additionally answer right-click / long-press with a target menu (a live agent session in this
  Project, or a new Claude/Codex one) — see `prompt-library.md`. Text meant for an agent must not
  be able to edit whichever note or file the user happened to open last.
- **Files** is a navigator, not a peer of the terminals it opens files next to, so it costs a
  drawer tab rather than a permanent workspace tab. As a pane it forced the layout to route
  every placement rule around it (an unanchored open, a Files-focused open, and session-note
  placement each had to skip Files panes) and it seeded every new Project with a narrow column
  most people ignored. Nothing is lost by the move: expanded-folder state was already persisted per Project
  outside the layout, and on desktop the drawer is an in-flow column, so a file row can still be
  dragged onto any pane. The one real cost is that Files and Clipboard can no longer be visible
  at once — the drawer shows one tab at a time — which is the trade the tab model makes.
- **Notes** is an index, not an editor. Notes stay ordinary pane tabs because the drawer unmounts
  a tab body on every switch: hosting the editor there would destroy cursor and undo history on
  each tab change, and would break insert routing outright (switching to Clipboard detaches the
  very editor the insert was meant for, so the text would silently land in a terminal). The tab
  pins the Project note first and unconditionally, pins the focused terminal's note second when
  that note holds text, then lists every other session note with content, searchable and scoped
  to this Project or to all of them. Selecting a row opens the note into a pane through the
  ordinary placement rule. This replaced the session-notes modal and its three
  scattered entry points (project context menu, app menu, `notes.browse`), all of which now open
  this tab.
- A note tab that appears and disappears with focus was considered and rejected: the desktop icon
  rail earns its keep by having fixed positions, a vanishing tab has no affordance for *creating*
  a note (the pane `note` chip already owns empty/written/open), and a Notes tab that followed
  focus would swap the document out from under someone mid-sentence. "Only when it exists"
  belongs to a row in a list, which is where it already lived.
- One component, two renderings (`UtilityDrawer.tsx`). **Mobile** is an overlay with a scrim,
  mutually exclusive with the navigation sidebar (opening either closes the other). **Desktop** is
  an in-flow column of the workspace grid: the pane tree shrinks rather than being covered, because
  covering a terminal in a tiling workspace is exactly backwards for a panel you opened to work
  *with* that terminal. Width is pointer-resizable and device-local, like the sidebar's.
- Every width change reflows the pane tree and refits its terminals, which sends a resize to each
  PTY and makes agent TUIs redraw. That is why the drawer never opens itself, its width persists,
  and the drag commits on pointer-up rather than per-frame.
- **Which tab a reopen lands on is a property of the entry point, not of the drawer.** The last
  tab is device-local state (`mux.drawer.tab.v1`), written on every selection and read at boot, so
  `drawer.toggle` — the gesture, the menu row, the palette — always reopens where you left it,
  across a close, a sidebar stealing the screen, or a PWA relaunch. The per-tab commands
  (`drawer.<tab>`, `clipboard.open` behind the rail's Clip button, the bell, "Browse files…")
  deliberately force their own tab: they name a surface, so honouring a remembered tab would
  ignore what was asked for. Binding one of those to the gesture you *open the panel* with is
  therefore indistinguishable from the drawer forgetting its tab, which is why the keybinding
  catalog's labels say outright which commands restore the last tab and which always force one.
- Desktop additionally has an always-visible 40 px **icon rail** on the far right, one icon per tab,
  with a badge for unread notifications. The rail is the part that actually fixes discoverability:
  the surfaces are visible without a menu, a chord, or any configuration. Mobile reaches the same
  tabs through the drawer's own tab strip.
- Both surfaces are **icon-only**, drawing from one map (`DRAWER_TAB_ICONS` in `railIcons.tsx`)
  so the strip and the rail agree by construction instead of by two lists kept in sync. The tabs
  used to carry glyph *and* label: six of them measured ~444 px, which overflowed a phone
  drawer (`min(430px, 92vw)`) into a scrollbar-less scroller and silently parked the last two
  tabs off-screen. Six icons were ~234 px. Adding Files and Notes is what pushed it over, and
  the icon-only strip is what leaves room for a seventh (Git) without repeating that.
- The marks are stroke SVG on a 24 viewBox, sized in CSS (17 px in the strip, 19 px on touch,
  16 px on the rail), never in `em`: these surfaces run a 9–12 px font. They replaced text
  glyphs for the same reason the command rail's did — a monospace font gives every glyph one
  advance width but wildly different ink, so `!` came out a hairline beside a heavy `⧉` with no
  way to normalize it. Two of the old glyphs were also simply wrong: `⌘` is the macOS Command
  key on a Windows-first app, and `❯` read as a shell prompt right next to the tab named
  Commands. The set is now clipboard-with-clock, terminal, speech bubble, folder, page, commit
  fork, bell — the two injection tabs and the two navigators each form a legible pair. Git's
  fork is deliberately close kin to the command rail's Branch mark: they never appear together
  (one is a terminal action, one a drawer tab), and the fork is the one mark that says
  "branches".
- Nothing is drawn with a word any more, so the `title` is the only place a tab is named and
  every title leads with its label; the label itself becomes the button's `aria-label`, since
  an icon button has no text to take an accessible name from and `title` is not a name on touch.
- Tabs are **user-arrangeable** by dragging one, from the strip or the rail. Both surfaces
  render one order and share one drag handler (`beginDrawerTabDrag`), because they are two
  renderings of one control: a per-surface order would let them disagree about what "third" is.
  It uses the app's pointer-drag contract like every other reorderable surface — no native DnD,
  refs and a single DOM drop-indicator attribute during the move, commit on pointer-up
  (`workspace-layout.md`) — so the pointer-up that ends a drag has its click suppressed on both
  surfaces, or moving a tab would also switch to it. On touch the drag also owns the pointer for
  its duration, which is what keeps rearranging the strip from doubling as a swipe gesture.
- The order is **server-persisted** in the `drawerTabs` settings domain, in one canonical bucket
  like the command rail and the file tree, rather than in localStorage beside drawer width and
  last-used tab. Those two are genuinely per-device; an arrangement says which surfaces *you*
  reach for, so a phone should inherit what a desktop set. Another device editing it arrives as
  the same `settings_changed` event as the cache first loading, so one listener handles both.
- Normalization is not optional (`drawerTabOrder.ts`): unknown and duplicate ids are dropped,
  and a tab the stored order predates is merged in beside its default predecessor rather than
  appended. A saved order must never hide a tab added later, and appending would put every new
  surface in the position that reads as an afterthought. The merge is relative to where that
  predecessor sits in the *user's* arrangement, so a new tab joins its neighbour wherever the
  neighbour was moved to. This mirrors the rule the mobile tab rail uses for the same problem.
- Keyboard cycling inside the strip walks the arranged order, not the registry order, or the
  keys would jump around a rearranged strip. `drawer.resetTabs` restores the default, because
  an arrangement is persistent state a drag can scramble and "drag five tabs back from memory"
  is not a way out.
- Last-used tab is remembered per device, so
  `drawer.toggle` (default two-finger swipe **left**, the swipe that drags a right-edge panel in;
  the rightward swipe keeps the left-edge sidebar) reopens where you left off, while `drawer.<tab>`
  commands open one tab directly and close it if it is already showing.
- Acting closes the drawer on mobile, where it covers the surface just acted on, and leaves it
  open on desktop, where the column sits beside that surface and a second insert (or a second
  file) is the common next action. One rule, applied to inserting text and to opening a file or
  a note.
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

## Feature-owned UI

Detailed UI behavior belongs with the owning feature:

- Pane tabs, close behavior, drag/drop, and mobile flattening: `workspace-layout.md`
- Project registry and visibility: `projects.md`
- Notes, Files, ignores, and watches: `project-resources.md`
- Provider selection and reset review: `provider-accounts.md`
- CPU/RSS and Process fleet: `processes-and-previews.md`
- Quota/context/tool evidence: `operational-telemetry.md`
- Automation navigation and diagnostics: `automation.md`
- Project task discovery and trust: `project-actions.md`

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
- `frontend/src/style.css`
