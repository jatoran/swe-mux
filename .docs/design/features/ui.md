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
- A persistent top rail places sidebar collapse, daemon activity, the ellipsized active Project title,
  and the active Project's Run trigger above the sidebar column.
  The product wordmark is omitted because Project scope is the useful persistent identity in this compact row.
  Workspace tabs are not global top-rail state; every pane renders its own tab strip beside that rail.
  Scan timeline has no top-rail action or spend chip.
  Its complete control and status surface lives in the Timeline segment of the utility drawer's Activity tab (which was called Insight until the drawer consolidation, and absorbed the standalone Timeline tab before that; Findings and Changes are its other segments).
- The Timeline tab begins with Project-scoped controls: Project permission and the expandable `.swe-mux/project-context.md` editor with Save and **Copy setup prompt**.
  Session-scoped controls follow: current-run permission, current scan, full-session scan and progress, spend, records, rollover boundaries, and source expansion.
  Each record keeps its potentially long evidence-target list collapsed by default, exposes the target count in the disclosure label, and bounds the expanded list with its own scroll area.
  Enabling Project permission does not authorize a run or backfill history; **Scan full session** is the explicit catch-up action.
- The sidebar is pointer/keyboard resizable from 190-480 px and collapsible.
  Dragging its divider below 150 px previews collapse, and reversing the same drag past 170 px reopens it before release.
  The separate thresholds prevent state chatter near the boundary.
  Width and collapse state are device-local browser preferences, not Project layout state.
- Sidebar focus is structural rather than another status colour.
  The active Project has one continuous neutral rail flush to the sidebar edge across its heading and expanded children, backed by a shallow inward wash.
  The focused session has a compact neutral selection plate inside that Project block.
  Runtime state colours remain confined to status dots and status copy, so Ready green, working blue, attention amber, and failure red do not compete with selection.
- On mobile, selecting either a Project row or a session row closes the navigation overlay. Project
  selection closes it before restoring that Project's remembered active view, including when no
  valid remembered view exists.
- The sidebar shows only Projects marked for active navigation.
  A Project row is followed directly by its layout/session rows; notes do not appear in the tree.
  Its fold cell stays allocated for alignment, but the fold control renders only when the Project has sessions to hide.
- **Project rows carry no `Note` / `Files` chips.**
  Both surfaces are drawer tabs scoped to the selected Project, and selecting a Project is one click on the row those chips would have sat in - which is also why the Project context menu stopped offering its own copies of them.
- The global `PROJECTS` header is navigation chrome rather than a Group.
  Ungrouped Projects render as root rows, with optional named Group sections after them under Manual order and interleaved among them under every other mode.
  On desktop, a Group header reorders on press-and-move and folds on press-and-release; the drag swallows its ending click.
  On mobile, a Group header folds on tap and opens the Group's context menu on a hold, because Project rows are the sidebar's sole reorder target and there is no right-click to give; the hold's trailing click is swallowed the same way a drag's is.
  The Group header's only button is `✎`, which renames the Group.
  It appears on hover over its own header, and on keyboard focus, so the resting sidebar is a list of names rather than a column of glyphs; a coarse pointer has no hover and always shows it.
- **The `PROJECTS` header owns every control that acts on the tree as a whole**, because none of them may scroll away with the list.
  It holds five buttons:
  - A magnifier opens the typed filter, which hides rows out of this tree rather than replacing it.
    It leads the row because it is the control that scales: fold and sort rearrange a tree still on screen, and the filter is what answers "where is X" when it is not.
  - A double chevron folds or unfolds **every Project row and every Group** in one click — pointing up to collapse, down to expand.
    It offers Expand only once nothing on screen is left to collapse, so the next click finishes a half-folded tree instead of undoing it.
    It is an icon rather than the `⊟`/`⊞` it replaced: that pair is the box-drawing mark for a single *tree node*, so it reads as
    "fold this one", and at this row's size its two states differ by one hairline stroke.
    Material's `unfold_less`/`unfold_more` is the obvious substitute and is also wrong here — two converging chevrons with round
    joins render as an `✕` at this size. Two *parallel* chevrons differ by direction alone, which survives any size.
  - `⇅` sorts, one flat list of modes and no submenu: Manual, Recently used, Name A→Z / Z→A,
    Newest / Oldest first.
    The chosen mode orders root Projects, the Projects inside every Group, and — under
    anything but Manual — the Groups themselves in among the root Projects.
    It held a nested `Sort Groups` group for Group order, which could only place Groups below
    the whole ungrouped list and so could not lift one for the work inside it; two modes also
    meant a mismatched pair was possible and read as arbitrary.
    A closing note in the menu states how Groups are placed and that dragging returns the
    sidebar to Manual, because neither has an always-visible cue.
    The button highlights while the tree is sorted, and its tooltip carries the mode.
  - A cogwheel opens the Projects registry — the single per-Project editor. It was a footer button and an app-menu row, both a
    screen away from the tree they edit.
  - `+` opens the registry **and** its Add-project dialog in one click, so the create dialog dismisses onto the registry rather than
    back onto the tree. Reaching it used to mean opening the registry and finding its button.
- The four icon controls are sized against `⇅`, not against each other's boxes. `⇅` is a text glyph and carries far more ink per
  pixel than a 2-unit stroke on a 24-box does, so matching them by box left the icons reading as the smaller controls even at
  equal dimensions; they are drawn larger than the glyph's nominal size to land at the same visual weight.
- The five header controls are revealed by hovering the header, and by keyboard focus on any of them, so the resting sidebar is a
  title rather than a toolbar; a coarse pointer has no hover and always shows them. Opacity, never `display`, so the row does not
  reflow as they come and go. The guided tour spotlights the cogwheel and blocks clicks outside its ring, so the tour's presence
  overrides the reveal — a highlighted empty box is not something a first-run user knows to hover.
  The filter's own close button is exempt: while the filter is up it is the row's only control, and there is no title left to hover past.
- Project sort is one global mode, applied to root Projects, the Projects inside every Group, and
  the placement of the Groups themselves. It was per section once, on the
  theory that a Group might be a hand-arranged shortlist while another is a long alphabetical
  pile; in practice it was set the same everywhere and cost a `⇅` on every header. Group
  placement joined it later, because as its own setting it could only order Groups among Groups
  below the whole ungrouped list — so a Group holding the last minute's work still sat under root
  Projects that had never been opened. Placing anything by hand puts the sidebar back on Manual,
  because a hand-placed row that the next render re-sorts away reads as a broken drag; Manual is
  the two-tier tree, so from a sorted one that also re-splits the root, which the arrangement the
  drag produced survives.
- **A Group carries its own context menu**, on a right-click anywhere in its section other than a
  Project or session row, and on a header hold on mobile. It holds `Rename group…`,
  `Collapse group` / `Expand group`, and `Delete group…` — the first two mirroring the header's
  `✎` and its fold click, so the menu is a second route rather than the only one.
  No header button deletes a Group: the `×` that did sat one pixel from the fold toggle and
  dissolved a Group on a stray click. Removing it left no delete path at all, since emptying a
  Group by reassigning its Projects leaves the empty Group on screen, so delete came back here,
  behind a two-click confirm that states what survives it — the Projects return to the root list,
  and no folder, session, layout, or history is touched.
- **Every Group renders, including one holding nothing.** An empty Group shows its header plus a
  `Drag a Project here` hint. It used to be filtered out of the tree, which made creating a Group
  look like it had failed and pointed the only way to fill it — dragging a Project in — at a
  section that was not on screen. The hint is also the drop target: a header alone is too thin a
  strip to aim a dragged row at.
  The ungrouped root list follows the same rule in reverse: it renders whenever any Group exists,
  even with nothing in it, so a user who grouped every Project still has somewhere to drag one back out to.
- A folded Group shows a live-session count and a state dot for the strongest agent state
  inside it, in the collapsed rail's colours so one folded thing never speaks a different visual
  language from another. The dot exists because a count alone would let an agent waiting on
  approval vanish behind the fold.
- Sort modes and fold state (Projects and Groups) are device-local; Group order itself is shared.
  Device-local means stored per browser, not per visit: every one of them survives a reload, a daemon restart, and a desktop redeploy.
  Root Projects always precede Groups.
  Behavior detail lives in `projects.md`.
- No sidebar row is indented for pane geometry. Sessions sharing a tabbed pane are marked by a
  vertical thread drawn through their status dots instead of by a bracket in a left gutter, so
  they sit at exactly the depth a session alone in its pane does.
  The bracket that preceded it had to buy that gutter with an indent across the whole group,
  which pushed the names of tabbed sessions further from the eye than any other row for a
  connector nobody needs to read twice.
  Split branches remain siblings at the same depth and draw nothing: the sidebar is a session
  list, not a pane-geometry diagram.
- The thread is broken at each dot rather than run behind it. An unbroken rule would paint a
  coloured stripe across the dots, and the dot's colour is the status being reported.
  It is drawn per branch and drops its outer segment at the first and last rows, so it begins
  and ends on a dot instead of dangling past the note or preview rows a branch may carry.
  It paints above the rows, because the focused row's selection plate is opaque and would
  otherwise cut it at that row.
- The thread is the only marker of tab membership. Rows carried a per-row glyph saying the
  same thing, which repeated on every row of a group already threaded together and competed
  with the backend glyph and broadcast flag for the name's line.
- Agent attention edges (`viewing`, `unread`) sit on the row's right and are inset vertically.
  A vertical rule on the left reads as structure rather than state: consecutive marked rows
  merge into one long spine, and the left of the row is where the tab thread runs.
  Per-row state stays on the side nothing structural uses.
- `unread` requires a **settled** agent - `idle` or `awaiting` - not merely unseen output.
  A working agent is mid-turn: its output is still growing and there is nothing to catch up on,
  so counting it as unread brightened every off-screen agent for the entire length of its run,
  which is precisely the window in which the row means "nothing for you here".
  An agent that works off screen therefore turns unread the moment it stops, and the brightest
  tier means "finished, and waiting on you".
- **Unread is counted in turns, never in output.** The row compares two integers the daemon
  serves on the session record: `turn_seq`, which advances only where the status contract
  settles a working session or raises an approval, and `read_turn_seq`, the acknowledgement.
  Deriving it from `last_activity_ts` instead - the timestamp of the last PTY byte - was wrong
  in both directions at once, and neither failure was visible as a bug in the sidebar:
  - Every SIGWINCH makes an agent TUI repaint its whole screen, so resizing the window,
    collapsing the sidebar, changing UI scale, or attaching a phone stamped fresh activity on
    every attached session within milliseconds of each other and lit up a whole Project.
    Idle spinners and status footers did the same, slowly.
  - The read mark lived in one tab's memory, so a reload, a UI reload, or a phone evicting the
    page marked the entire fleet caught up, and a second device never saw the first device's
    marks at all.
  A server-held counter cannot be moved by a repaint, survives a reload, and gives every device
  the same number to compare against.
  `design/features/status-detection.md` owns which transitions count as a turn.
- **Being on screen is half of what marks a row read; a human at the window is the other half.**
  A pane can be mounted and visible in a window that is minimized, behind another app, or on a
  phone whose screen is off, and marking those read is how a night of finished turns used to
  vanish before anyone looked at them.
  The acknowledgement therefore requires `visibilityState === 'visible'` **and** window focus
  (`humanPresence.ts`), plus a short dwell, and is written through `POST /sessions/{id}/read`.
  The strictness is deliberate: a window parked visible on a second monitor with nobody at the
  desk is exactly the case that has to keep its marks, and erring this way only leaves a row lit
  slightly longer than needed.
- **The user can say so directly, and that outranks all of it.** A single
  `Mark as read` / `Mark as unread` toggle on the session menu and in the palette
  (`session.toggleRead`) writes the same `POST /sessions/{id}/read`, with `{"read": true|false}`
  instead of a cursor.
  Marking unread is the only thing in this system allowed to move the mark **backwards**, and it
  is deliberately narrow about how far: back to just before the latest counted turn, meaning "I
  have not read the last thing this agent said", not "forget the whole history" - which would
  relight the row again as soon as another device acknowledged some older turn.
  It also sets `unread_pin` on the session record, which is what makes the mark survive being
  applied to a pane that is on screen: without it the dwell acknowledgement above would re-read
  the row a second later and the click would appear to have done nothing.
  While the pin is set, implicit acknowledgement is refused by the daemon and not even attempted
  by the client (`pendingAcks` skips it), and the pin forces the unread tier regardless of the
  counters or the session's state - it is a statement, not a measurement, so marking a *working*
  agent unread lights the row now rather than whenever it happens to settle.
- **The pin lasts for the visit that set it, not forever.** Its whole job is to survive the dwell
  of the pane it was applied to; going back to that pane later is the user reading the very thing
  they marked, so it retires there.
  The client tracks that (`trackPinVisits`): a pin first seen on a session that is **on screen**
  is *held* until that session leaves the screen, and *released* from then on, while a pin set on
  a session that is not on screen - from the sidebar, on a pane you are not looking at - is
  released immediately, so the first visit reads it.
  A released pin's dwell is written as an explicit `{"read": true}`, because that is the shape the
  daemon lets clear a pin. Release is sticky: a released pin that scrolls back into view does not
  re-arm, or the row would relight on every switch away and back.
  Only the client can make this call, since which panes are on screen is client state and nothing
  the daemon can see. A fresh tab is deliberately generous - with no prior state a visible pin
  reads as newly set, so a reload keeps the mark.
  Three things clear it, then: the user marking it read, the user returning to the pane, or the
  agent completing another turn, which supersedes the pin and leaves the row unread on the
  ordinary counter comparison anyway.
  Before this, a hand-set mark could only be undone by a second trip to the menu, which made a
  read-state toggle behave like a permanent flag.
  Being server-held, the mark converges across devices exactly like the acknowledgement does.
- The row's kill control appears on hover, and on keyboard focus via `:focus-visible`; selecting
  a row does not reveal it.
  `:focus-within` did reveal it, because clicking a row leaves DOM focus on it, so every selected
  session wore a hover affordance until focus left the sidebar entirely.
  Touch raises neither hover nor `:focus-visible`, so there the tapped row keeps the
  `:focus-within` reveal — it is the only way to reach the control on a phone.
  **It overlays the row and reserves nothing.** It used to widen `.session-copy` by a lane while
  shown, which kept it clear of the flags at the cost of re-laying-out the row the instant the
  pointer arrived: every token slid left while you were reading them. Covering one token is a
  smaller loss than moving all of them. The button is opaque, and its container inherits the row's
  own background so a short mask fades whatever runs under it into that background instead of
  colliding with the button's edge — inherited rather than named, because a hovered, selected, and
  awaiting row each paint a different background and one of them pulses.
  `session-row-layout.spec.ts` guards it: hovering must leave the title, flag strip, and every
  flag box byte-identical.
- The active-Project header exposes **Run** persistently.
  On desktop, each Project row reveals its Run control while the pointer or keyboard focus is anywhere in that Project's row-and-session block; its reserved column preserves row alignment while hidden.
  On mobile, Project rows expose Run persistently and also expose `⋮` immediately left of it, giving Project actions direct tap targets.
  The compact Run menu contains new Claude/Codex/shell/custom-terminal launchers followed by trusted Project Actions; it is a launch surface, not persistent sidebar grouping.
  Each harness launch row is marked with that harness's own mark (`harnessMark`, `harnessIcons.tsx`) rather than a play triangle, and a launch profile wears its harness's mark because it launches that harness.
  Every row in the section starts a session, so a triangle on all of them distinguished nothing; which CLI a row starts is the fact that separates them.
  The Project Action rows keep `▶`/`▷`, which there is not decoration but the file's trust state.
- Run is the only always-present launcher, since tab strips carry no new-tab button
  (`workspace-layout.md`). The header Run is styled as an accent chip rather than a faint label,
  and because it has no room in the 40 px collapsed header column, the collapsed rail carries an
  equivalent `▶` button. Mobile's toolbar Run is the same surface.
- `projects` opens the viewport-level Projects manager, which lists configured visible and
  hidden Projects. A Project must exist before terminal actions are enabled.
  It is reachable from two places on purpose: the sidebar's `PROJECTS` header, beside the
  tree it edits, and `menu → Projects`, between the viewers and Configure Actions. The header
  button is discoverable only once the sidebar is open and its header is in view, while the
  app menu is where every other app-wide surface is looked for.
- The sidebar footer is two controls: `menu` at the left edge and the alerts bell at the right.
  It held four. `projects` moved into the `PROJECTS` header, beside the tree it edits, and the
  settings cog was removed: `menu → Settings` sits one row away from the button next to it,
  and a second permanent door to the same panel cost a footer slot for a saving of nothing.
  A named menu row is also searchable and keyboard-reachable, which an icon is not.
  The remaining pair still uses the left item's own `margin-right:auto` rather than
  `space-between`, so either removed button can come back without the layout re-deciding itself.
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
  Provider marks use inline vector geometry with `currentColor`, so platform emoji substitution cannot replace their configured identity colours.
- The **collapsed desktop rail and the mobile toolbar** draw the same square: one per provider,
  the provider's mark above one number, and that number is weekly. The 5-hour session window
  churns constantly, and `fable` is a sub-window of one provider's plan rather than a measure
  comparable across providers, so weekly is the one worth a permanent glance.
  The phone carries the square rather than the sidebar's breakdown for the same reason the rail
  does, only more so: the breakdown is three columns of numbers competing with the Project name
  and two run controls inside one 44 px row, and the question a phone is glanced at for is how
  much of the week is gone. The full reading is one tap away, in the popover the square opens,
  which is also where a device that cannot hover reaches what the tooltip says.
  A single square is banded by the number it prints, never by a hotter window it hides — a border
  contradicting the digits beside it is the same defect as banding an unrounded value.
- The **expanded sidebar** shows every window the provider reports, so Claude
  can show `5h/weekly[/fable]` while Codex shows only `weekly` when no 5-hour window is reported.
  Missing windows do not render placeholder dashes.
  The provider identity column has a fixed width so its mark stays left-aligned across rows with different window counts.
  Every window column is a fixed width too, and the row ends in a filler track that absorbs the sidebar's spare width.
  Sharing one track width is what makes the two provider rows readable as a table: while the columns were `1fr`, a widened sidebar was divided into three tracks on the row reporting Fable and two on the row that was not, so the same reading sat at a different offset in each row and both drifted away from the identity mark.
  Column contents are start-aligned for the same reason - a centred reading moves whenever its neighbour's width changes.
  Visible percentage signs distinguish usage readings from reset countdowns, while the tooltip names each reported window.
  Each window is banded on its own, so the breakdown says *which* one is hot.
  The weekly reset countdown stays on a second line beneath: "22% used" answers a different question from "and it clears in 4d12h".
  Every chip's tooltip, and therefore its accessible name, names every window the provider reports and says the countdown is the weekly one — including the mobile square, which draws only one of them.
- A band always describes the digits actually printed, not the value behind them: a rounded `90`
  colours as 90 even when the true reading is 89.6, or the colour would contradict the number
  beside it at exactly the threshold people watch for.
- The resource chip reports RAM rather than CPU, since a percentage that moves every sample is
  not worth a permanent glance, and abbreviates it (`3.2G`) to fit the strip.
- The expanded sidebar resource trigger is one row: boxed terminal-window icon and live session count, boxed process-tree icon and count, CPU icon and rounded system CPU percentage, then RAM icon and swe-mux process-tree working set.
  Its tooltip and accessible name expand the icon-only values and state that clicking opens usage details.
- The session count leads the row, and is the only value in it with no unavailable fallback.
  It is the operator's own unit of work, so it reads before the machine's accounting of it; and it is counted from the fleet the sidebar already renders rather than from process inspection, so it stays truthful on a host where process inspection is refused and the rest of the row reads `—`.
  A session counts as live by exactly the rule a Project's own collapsed badge uses: not pending, not exited, not crashed.
- The resource popover separates machine and process-tree scope explicitly.
  System CPU covers the whole machine; process count, reclaimable RAM, and working set cover swe-mux plus everything it started.
  Reclaimable RAM and working set are separate metric boxes because working set counts shared pages in every process while reclaimable RAM excludes them.
- Popover direction is independent of the condensed trigger, so a rail anchored at the bottom of
  the window still opens upward.
- Git state is Project/session metadata. Worktrees have no first-class sidebar row, creation
  modal, or workspace ownership; the drawer's Git tab is their only surface (`git.md`).

### Sidebar filter

The typed filter over Groups, Projects, and sessions.
It hides rows out of the sidebar's own tree; it never draws a list of its own.
Its rules, and what each one is defending:

- **The tree is the result surface.**
  Group sections, Project sections, and the session rows nested under each Project's pane layout all stay exactly where they are, and the only difference a query makes is which rows are still present.
  A flat ranked list in the tree's place was tried and is wrong: everything on screen moved at the first keystroke, so finding a row meant re-reading a column that no longer looked like the one being searched.
  Nothing is reordered by score - re-sorting a hand-arranged tree behind the user is the one thing this must not do.
- **Opening the filter changes nothing.**
  An empty query means *not filtering*, which is not the same as filtering to nothing: the tree draws itself untouched and narrows only once a character lands.
- **It replaces the header row rather than adding one.**
  The controls it replaces (fold, sort, registry, add) act on a tree that is being filtered rather than arranged, and an added row would push every row below it down a line at the instant the filter opened.
  The row keeps its height, so nothing reflows.
- **Two containment rules make it a tree filter rather than three independent ones.**
  A node that matched keeps its subtree: a Group matched by name keeps its Projects and their sessions, and a Project matched by name keeps its sessions, so typing a Project's name never renders it as an empty heading.
  A node that is kept keeps its ancestors: a matching session pulls its Project and that Project's Group back on screen, because a row with no heading over it does not say where it lives.
- **A pruned pane tree stops describing branches it no longer draws.**
  The layout cluster a split or a stack renders counts the terminals *still drawn* rather than the terminals present, so a split whose other side was filtered out collapses to a plain row instead of drawing an empty branch beside the match.
  It is the same count the tree already used to skip layout nodes holding no terminal.
- **A fold never hides a match.**
  While a query is up, a Project with anything left under it and a Group with anything left in it both draw open whatever their stored fold says - answering "where is X" with a closed section is answering with silence.
  The stored flags are untouched and return when the filter clears, which is why folding is *inert* while filtering: the Project row draws its collapse spacer instead of its toggle, and a Group header's fold click does nothing, rather than setting a preference whose effect nobody can see.
- **Every sidebar drag is inert while a query is up.**
  A drop computes its insertion index from the rows that are drawn, so reordering a partial tree would move a Project, a Group, or a session somewhere nobody aimed at.
  Drag returns the moment the query is empty again.
- **Ranking exists for exactly one thing: what `Enter` opens.**
  The best match carries an inset bar on its leading edge - an edge mark rather than a background, because Project and session rows already spend their background on selection and attention state.
  Arrows move it over the drawn rows in *sidebar* order and stop at both ends rather than wrapping; every keystroke releases it back to the new best match.
  With nothing typed there is no mark and `Enter` does nothing, rather than opening whichever Project happens to sit at the top.
  A row kept only by containment is never the best match: landing on a Project you were shown because one of its sessions matched goes to the wrong place.
- **Matching is shared with the Settings index** (`fuzzyText.ts`).
  Every whitespace-separated term must match something, so a second word narrows.
  A Project outranks a session it ties with, being the coarser destination and the one that contains the other; a live session outranks an ended one of the same name, though both are still drawn.
  A session matches on the name its row draws, plus its Project's name and root, its harness, and its branch or worktree.
- **It retires itself after five seconds untouched**, clearing the query and restoring the tree.
  A filter is a transient lens, and one left standing over a sidebar the user walked away from misreports the fleet at a glance.
  Typing, arrows, the pointer crossing the tree, and activating a row all restart the clock.
  Interaction is recorded in a ref and idleness is polled, so pointer movement over the tree costs no re-render.
- **Reopening always starts empty.**
  There is nothing worth restoring: a query typed minutes ago would filter the tree by a question already answered.
- It is a dismiss level, so Escape and the platform back gesture put the tree back.
  Unlike the sidebar itself it is a level on every device, because it is transient everywhere and so never leaves the stack permanently armed.
- Reachable from the palette and bindable as `sidebar.search`, which opens the sidebar with it - the filter is chrome inside a column that is hidden on a phone and collapsible to a rail on the desktop.

## Menus and overlays

- Scope follows the menu that opened a surface, never a hidden mode.
  The app menu opens History, Notes, the fleet queue, prompt library, clipboard history, Resources, and notifications across every Project; right-clicking a Project row opens Session history and the prompt library prefiltered to it.
  Right-clicking empty sidebar space is the no-Project case and matches the app menu.
- **A Project menu row has to earn its place against the drawer.**
  Notes, Processes, the fleet queue, and Browse files each left it, because each is a drawer tab or a dialog that already opens on the *selected* Project - so right-clicking a Project row to reach them was a second route to a place one click away, and the two that stayed are the two with no such home.
  What stayed with them is what has nowhere else to be pressed: Reveal in Explorer, the Group, Rename, Project settings, Hide, and Remove.
  Collapse-in-sidebar went the same way (clicking a Project header is the fold), and so did Move Project up/down, which could only ever step one place at a time while long-press drag moves a Project anywhere (see the pointer-drag contract below).
  The rows that remain carry no trailing ellipsis either, for the reason the app menu's do not: nearly every row here opens something, so a mark meaning "this opens something" distinguished none of them.
  What the palette keeps, it keeps: `project.moveUp` and `project.moveDown` stay registered and bindable, the same way the removed layout rows did, so dropping a button never removes the keyboard route.
- **The category headers went with them.**
  `BROWSE THIS PROJECT` and `PROJECT` labelled three rows apiece in a menu of nine, which is a fifth of its height spent saying what each row's own mark now says.
- **The app menu is two halves: the places you go, and below a rule, the things you set.**
  The viewers spent a while behind one folding `Utilities` row, on the argument that ten of
  them made the menu a wall. The wall was ten. Consolidating four resource modals into one
  Resources dialog took it to seven, and a fold over seven rows is a click that buys back
  four rows of height and costs one on every visit. It also had to invent a summary badge on
  the header to carry the pending-queue and unread counts that folding hid — two unrelated
  numbers added into one, answering *is there anything in here* and leaving the tooltip to
  say which. Unfolded, those counts are back on the rows that own them, where one number
  means one thing. What divides the halves now is a plain rule, not a `CONFIGURATION`
  heading over an already-obvious group.
- **No row in this menu ends in an ellipsis.** Every row here opens something — that is what
  the menu is — so a mark that means "this opens something" appeared on nearly all of them
  and therefore distinguished none.
- **Every row in the app menu, the sidebar context menu, and the Project and session context menus carries its own mark**, opting the row into `.menu-row` (icon, then label, then any trailing hint) and suppressing the terminal skin's `> ` prefix.
  Same argument as the ellipsis: a marker identical on fifteen rows says only "this is a menu row", so a reader scanned fifteen prefixes and still had to read every label.
  The marks come from `railIcons.tsx`, the drawer set, so a concept appearing in more than one place (Notes, Queue, Actions, Alerts, History, Groups, Settings) is literally the same drawing.
  `MenuGroup` takes an `icon` for the same reason and lines its header up with the rows it sits among.
  Where one control's label switches, its mark switches with it or it contradicts the word beside it: Kill session wears a power glyph and the same button wears a bin once the session has ended and only its row is left to remove.
  This is opt-in per row rather than applied to every `.context-menu button`: the remaining context menus are a handful of verbs about one object ("Rename", "Close", "Duplicate"), where a mark per verb is noise, while these four list a dozen unrelated acts and destinations.
  Inside them, a **radio set keeps its plain rows**: the three Read-aloud modes are already marked with a `✓`, and an icon column beside a check column draws two marks for one fact.
  Where two rows differ only in what they reload, the marks name the *thing* rather than the
  act — a server, a package, a refresh arrow — because three refresh arrows would say "reload"
  three times and name nothing.
- The app menu holds **nothing that acts on a single Project**, and no longer holds the Project
  registry either — adding and managing Projects are the two buttons in the sidebar's `PROJECTS`
  header, beside the tree they act on. Per-Project actions — Project settings, files, notes, and
  Project-scoped Fleet Queue approval rows — live on the Project itself: right-click a
  sidebar row, or tap the Project title in the mobile top bar (both open the same menu).
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
- **A choice among a list is a submenu, not a `<select>`.**
  The Project menu's Group control is a `MenuGroup` whose header carries the Project's current Group and whose flyout lists Ungrouped, every Group, and `Create new group`, scrolling when there are more Groups than fit.
  The native `<select>` it replaced rendered its options in a system sheet on a phone - with none of this menu's styling, none of its keyboard walk, and no room for the create row at all.
  Creating from there also *moves* the Project into what it creates: opening the same empty dialog and leaving the Project where it was would make the row a detour to the sidebar menu rather than an answer to "put this somewhere new".
  A failed move is reported and leaves the (successfully created) Group in place rather than trying to unwind it, so the sidebar shows exactly what the daemon holds.
- A prefiltered surface always exposes its scope as a visible, clearable control, so a Project
  entry point narrows the same browser rather than opening a different one. The prompt library is
  the deliberate exception: its Project argument adds that Project's templates to the global set
  rather than filtering, so opening it "unscoped" would remove templates. The app menu therefore
  still passes the active Project there.
- Context menus are source-aware. Terminal-only operations never appear on resource tabs;
  obsolete focused-terminal, detach/remove-from-group, Project-note, pane-swap, and pane-header
  minimize/close actions are absent.
- Opening tab or sidebar-session actions is non-activating.
  Desktop right-click and mobile long-press target the named tab or session without changing the active Project, pane-active tab, focused view, or active terminal.
  The click synthesized after a sidebar long-press is consumed; normal click/tap remains the activation gesture.
  Menu actions operate on the captured target without selecting it unless the action explicitly opens or focuses that target.
- Mobile Project long-press is not a context-menu gesture.
  A 325 ms hold with no movement beyond 8 px picks up the Project; earlier vertical movement remains native sidebar scrolling and shows no reorder feedback.
  Pickup closes open menus, claims the pointer, gives short haptic feedback, lifts the row, and enables insertion preview plus edge auto-scroll inside its current section.
  Once picked up it also **cancels `touchmove` for the rest of the drag**, without which the sidebar scrolled under the finger and the scroll cancelled the pointer — the row lifted and then nothing happened, which is what "mobile reordering does not work" looked like (`workspace-layout.md` § pointer drag contract).
  `⋮` opens the Project context menu on tap, while desktop right-click retains the same menu.
  Mobile sessions and every other sidebar row never start a sidebar drag; session long-press remains context-menu-only.
- Both sidebar lists preview a drop as the **landing slot** — a dashed outline of the dragged row, labelled with its name, over the gap it would fall into — rather than as a line at the pointer.
  What a drag is asking is "which two rows does this end up between", and an outline the shape of the row answers it where a line marks only where the finger is.
  Sessions add a second, deliberately different preview for the other thing a drop can mean: landing on a row rather than between two merges the pair into one tabbed pane, and shows a blue row highlight instead of the green slot, because the two targets sit a few pixels apart and must not read as one.
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
- **No context menu spawns a session either**, by the same argument one step further.
  `New terminal as tab` is gone from the session menu on every source and from the mobile tab
  menu.
  It creates *new* work, which is the Run menu's job everywhere else (see the Run bullet above),
  and reading it off a menu opened on some other session made the pane it landed in a guess.
  The palette keeps `pane.stackNew` for the focused pane, where "as a tab in *which* pane" has an
  answer.
- **Every session-menu row acts on that session, immediately.**
  `Insert prompt template` and `Processes and previews` left it entirely: both open a whole surface of their own (the prompt library, the Resources dialog) from a menu whose every other row does something to the session and closes, and both remain a palette command and a drawer tab away from wherever you already are.
  `Open in focused pane` left for a simpler reason - clicking the row already does it, from the same list the menu was opened on.
- **The session menu is still tiered by source.** A session's own header (`⋯`, or right-click on the pane bar) carries `Copy working directory`; a sidebar row, a desktop tab title and a mobile tab do not.
  Those menus are opened by pointing at a session from a list, and what a person points at a list row for is Rename, read state, broadcast, and Kill.
  Same action, same registry command, one surface.
- **The menu says what a session *is*, not how it started.**
  Its header carries the PID and the Git branch.
  The boot-timing chip that used to sit with them is a fact about how the session *began*, which nobody right-clicks a live session to learn; it lives in the durable startup diagnostics instead (`development/PERFORMANCE_RUNBOOK.md`), and the browser milestones are still recorded and POSTed - they simply no longer sit in render state that nothing reads.
- **Read state is one row, not two.** `Mark as read` / `Mark as unread` is a single toggle whose
  label states the action it performs, on every source, for live agent sessions.
  Listing both halves would make the reader work out which of the pair is currently true before
  clicking, which is the one thing the label already tells them.
  The mechanism is below, under the turn counter.
- **Read aloud is collapsed behind a `MenuGroup`** labelled with its current mode
  (`Read aloud · auto on reply`), holding the three modes plus `Speak last reply now`.
  Four flat rows plus a subtitle for a per-session setting most sessions never change is the
  exact case the group mechanism exists for, and carrying the mode in the header means the common
  case - reading what it is set to - still needs no click.
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
- **"Back" is one concept with one implementation.**
  Every dismissable level registers with the dismiss stack (`dismissStack.ts`) while it is open, and Escape, the platform back gesture, and the mobile back swipe all resolve to a single `pop()` that closes exactly one level.
  Back has a **second rung underneath that one**, reached only when nothing is layered: the recent-views ring (`viewHistory.ts`), which steps the mobile workspace back through the tabs and Projects most recently looked at before the press is finally handed to the platform.
  `composeBackTarget` joins the two into the single `BackTarget` that `systemBack.ts` and the `nav.back` command hold, so the ordering - overlays, then navigation, then leave, which is Android's own - lives in one place rather than in each entry point.
  Escape is the one channel deliberately left on `dismissStack.pop()` alone: with nothing open Escape belongs to the terminal, and stepping the workspace back a tab from inside an agent's TUI is the same class of side effect the flat Escape handlers were replaced to stop.
  Registration happens through `useModalFocus`, so declaring a focus-trapped modal is also how it declares what back means for it; anything that is not its own focus-trapped modal — a drill-down, a menu, a slide-in panel, an in-pane find bar — uses `useDismissLevel`.
  Coverage is total rather than partial: modals, the nine context menus, the mobile slide-in panels, the palette, the quick launcher, every root-owned dialog, and the in-pane widgets (terminal find, resource find, note outline, files tree menu, note row menu) are all levels.
  Four surfaces had grown private versions of this ladder before it existed — the paired `useModalFocus` gates in `AutomationDashboard.tsx`, the nested Escape ternary in `ProjectRunMenu.tsx`, the four-rung unwind in `Settings.tsx`, and two flat Escape handlers in `App.tsx` closing nine and eighteen things at once — which is the evidence that it is one behavior rather than a per-dialog decision.
- Stack order is **temporal, and specifically opening time rather than registration time**: the level that most recently became active pops first.
  The distinction is load-bearing rather than pedantic.
  A conditionally rendered component registers when it opens, so for it the two coincide.
  The composition root is always mounted and registers every one of its own dialogs and menus up front, in source order, so ordering on registration would pop them in the order they appear in `App.tsx` instead of the order the user opened them.
  A drill-down therefore needs no knowledge of its own depth, and a level that closes and reopens correctly returns to the top.
- A hand-written precedence ladder is the wrong shape for the same reason.
  `Settings.tsx` unwinds a close confirmation, the theme picker, and the search query before the panel itself, and those are now four independent levels rather than a fixed order, so back undoes them as they were actually opened instead of by a precedence that cannot know whether the theme picker or the search came first.
  Every one of them stands down while a shortcut is being recorded, because Escape then means "cancel the recording" and belongs to the capture handler.
- A level is gated off only to stop being the dismiss *target*, never to give up focus containment.
  The transcript inside session history registers **above** the browser rather than gating it off, so back returns to the results while the modal keeps its focus trap for the whole time the transcript is open.
  Before that level existed, Escape in a transcript closed the entire browser and discarded the search while the visible `← Results` button went back one step, so keyboard and pointer disagreed about what back meant.
- A level may declare itself **blocking**, which absorbs a back instead of letting it fall through to whatever is behind it.
  The daemon-reload and redeploy overlays use this: they are `alertdialog` waits with nothing reachable behind them, and back must not walk out of the app while the daemon is mid-restart.
- **The slide-in panels are levels on mobile only**, because only there are they overlays.
  On desktop the sidebar and the utility drawer are docked chrome, and `clipboardOpen` is a persisted expansion that is routinely true for a whole session; registering it would hold the stack permanently non-empty, arm the history sentinel forever, and stop the browser's Back button from working at all.
  The docked drawer keeps its own element-scoped Escape (`UtilityDrawer.tsx`), so Escape still closes it while focus is inside it.
  That is a deliberate narrowing: the flat handler it replaced fired on Escape from anywhere, so pressing Escape inside an agent's TUI collapsed the drawer and the sidebar as a side effect.
- Escape reaching a terminal is now the ordinary case rather than a hazard.
  With nothing open, `pop()` reports an empty stack and does nothing, so the key belongs to the terminal.
  Surfaces that own Escape for themselves — the shortcut recorder, the utility drawer, the find bars — stop propagation, which is why the composition root's handler stays on the bubble phase where that shielding works.
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
  "Rebuild + redeploy app (keep sessions)" confirms, then posts `/api/daemon/redeploy` (staged
  frozen-app rebuild; the only reload that reaches the frozen bundle's own assets).
  A redeploy has two stages that the UI deliberately treats as unalike.
  While the new bundle builds in `dist/.staging` the current daemon keeps serving, so the app stays
  fully usable and the only sign of the redeploy is a persistent expandable spinner pinned to the
  top-left corner, carrying the stage, an elapsed timer, and the build log tail.
  Blocking here would lock the user out of a working app, and a failed build would have locked them
  out for nothing.
  Once the daemon actually goes away, the app is unusable in a way that loses work silently -
  the PTY sockets are proxied by the daemon, so keystrokes typed into a terminal go nowhere -
  so that stage does show a blocking overlay, and suppresses the request-failure toasts that would
  otherwise bury it.
  Every client shows both, not only the tab that started the redeploy: the daemon broadcasts
  `daemon_redeploy_started` when it accepts one (minutes before it can affect anyone) and
  `daemon_redeploy_stopping` from its own shutdown handler, which is the one authoritative
  "the outage starts now"; a client that misses the second falls back to two consecutive failed
  health probes, so a single blip cannot raise the overlay.
  The state is mirrored into `sessionStorage`, so a reload or a second tab comes up already knowing;
  restored state is clamped to the non-blocking stage, because a page that loaded at all was served
  by a live daemon.
  The page reloads when the daemon is seen to go away and come back; a daemon that never went away
  and reports `running: false` is the failed-build case, which surfaces as an error toast with the
  log tail and no reload (the running app is untouched).
  After the reload, a redeploy that did not ship what was built - a rollback above all, which
  otherwise looks exactly like success - is reported once from `last_result`.
  Every production build carries a deterministic `ui-build` identity derived from its content-addressed asset filenames.
  Every `/events` connection starts with that served identity, so clients that reconnect after a successful redeploy compare it with the identity in their loaded document.
  A hidden client reloads immediately, while a visible client keeps its current work and shows a persistent "UI update ready" banner with an explicit Reload now action.
  A rollback republishes the old identity and therefore does not reload other clients.
  The first release of this protocol cannot update clients still running the preceding UI because those clients do not yet perform the comparison; each such client needs one manual reload.
  "Reload UI" is a plain page reload for picking up freshly built frontend assets.

## Settings contract

- Form changes remain local drafts until explicit Save. Save state is visible as
  dirty/saving/saved, and a background refresh cannot reset the selected settings section.
- **One tab names one subsystem.** Seventeen tabs are grouped into four runs, declared once
  in `settingsTabs.ts` and rendered from that single order by both layouts:

  | Group | Tabs |
  | --- | --- |
  | Workspace | General, Projects, Terminals, Git, Processes |
  | Agents | Harnesses, Accounts, Prompt queue, Automation, Usage |
  | Interface | Appearance, Input, Text editor, Voice |
  | System | Alerts, Remote, Diagnostics |

  A group is a contiguous *run* of the array, not a declared membership list, so a tab that
  drifts away from its group produces a repeated heading rather than a silently miscategorised
  tab. **Both layouts draw the same grouped column**, headings and all — narrow, it slides in
  as a drawer rather than docking beside the content (below). The phone used to get the same
  order flat, in one horizontally scrolling rail with the headings suppressed, on the grounds
  that it could not spare the width; what that actually cost was the categories, which are the
  only thing making seventeen entries navigable, plus a permanent strip of vertical space. A
  drawer spends width it is not using anyway and costs no height at all. The group wrapper is
  `display:contents` and `role="presentation"`: a real box breaks the column, and a `tablist`
  admits only tabs, so the heading is a visual affordance and a screen reader gets the flat
  list underneath it.
  A heading must read as chrome rather than as one more entry, and typography cannot carry
  that distinction: `.settings-layer *` pins font family, size, weight, and line height with
  `!important` so the whole panel scales as one unit, and `.app-shell *` pins tracking. The
  separation is therefore built from what is left — a filled band where an entry is
  transparent until hovered, rules closing that band, full `--text` against the entries'
  muted grey, and flush with the sidebar's edge while every entry is indented past it. The
  fill is green-tinted rather than `--panel2`, because `--panel2` is what hover and the
  active tab use and a neutral fill reads as a selected row; the label is `--text` rather
  than the panel's heading green because green on a green-tinted band measures 2.4:1 on the
  low-chroma light themes, under where the `--green` headings already sit, while `--text`
  holds 5:1 at worst across the catalogue.
- **Narrow, the section list is a drawer and the header names where you are.** Below the
  workspace's own breakpoint (760px) the panel fills the screen, and the two pieces of chrome
  it used to spend height on both move:
  - The search box sits **inline in the header**, between the title and the close button,
    instead of wrapping onto a row of its own.
  - The section list becomes a **left slide-in drawer** over the content, opened by a hamburger
    at the header's left edge *and* by the header title itself — the title is where the eye
    already is, so on a phone it is the tap most people make. It is `visibility:hidden` when
    closed rather than `aria-hidden`, which takes its buttons out of the focus order and the
    accessibility tree without hiding elements that are still focusable, and still animates
    because the transform is what moves. Picking a section closes it, by every route into a
    tab: the list, a search result, a deep link.
  - The header therefore reads `SETTINGS` over the **current tab's name** rather than
    `CONFIG::V6` over `Settings`. What the panel is, is the one thing already obvious when it
    owns the whole screen; where you are in it is not.
  - It behaves as the workspace's own left sidebar does, deliberately: it is a dismiss level
    (`settings-nav`), so system back and the back swipe close it before they close Settings;
    its scrim closes it; and whichever gesture slot is bound to `sidebar.toggle` opens it,
    with either horizontal direction closing it again while it is open (touch gestures, below).
  Widening past the breakpoint turns it back into the docked column and closes the level, so a
  column that is permanently on screen never leaves a dismiss target standing.
- Where a setting lives follows the subsystem that owns it, not the feature that first needed it:
  - The **model provider, the OpenRouter key, and the two routed model defaults it unlocks** are
    on Accounts, with the other provider credentials. Everything model-backed depends on that one
    key, so filing it inside Automation made it unfindable from Voice, the scan timeline, or
    attention narration.
    A model belonging to *one* feature is not a routed default and does not go here: it lives with
    that feature, because a feature is configured in one pass and a shared model form would split
    every one of those passes across two tabs. Accounts indexes them all instead.
    The provider choice is one level above all of that - it decides which endpoint the whole index
    is requested from - so it is the first block on the tab.
  - **Auto-delivery and agent messaging** are the Prompt queue tab. They bound how a queued
    message reaches an agent whichever harness it runs, so they are delivery policy rather than
    harness configuration.
  - **Global project ignores** are on Projects, beside the per-Project list they compose with,
    rather than under process evidence. They filter the file tree and resource watchers, never Git.
  - **System prerequisites, the three session-preserving reload actions, and the diagnostics
    bundle** are the Diagnostics tab. None of them is remote configuration.
  - **Scrollback** is on Terminals, and it is three byte figures over one stream rather than one:
    what a session retains (`scrollback_bytes`), what a *fresh attach* is handed
    (`attach_replay_bytes`), and what survives losing the daemon and its PTY owner together
    (`session_recovery_checkpoint_bytes` and its two companions).
    They are one section because reading any of them without the other two is how "why did my
    pane come back short" stayed unanswerable.
    `history_limit` is *not* scrollback — it is the history browser's page size — so it sits
    with native-history indexing on Harnesses.
  - **Agent actuation** is its own section on Prompt queue, beside the messaging bounds rather
    than inside them. Everything above it delivers *text a human still reads*; spawn, interrupt,
    end, and settle-watch act on a session directly, and each is three layers deep (the install
    stop here, the Project's opt-in, the Project's grant).
  - **Ghost windows** and the **detection timeline** are their own sections on Processes rather
    than more rows under process evidence: the sweep is the one thing on that tab that changes
    what the machine looks like rather than what swe-mux records about it, and the timeline is
    detection evidence rather than process evidence.
  - **The daemon log level** is on Diagnostics, beside the bundle it decides the contents of.
    It applies on save with no restart, which is what makes "set DEBUG, reproduce, export" a
    single pass.
- Global automation policy is not duplicated across overlays, and Settings is its one home.
  Settings → Automation owns every install-wide automation switch and bound: the `automation_enabled` master switch, the `scan_timeline_enabled` gate, budgets, execution bounds, and retention; the credential and models are on Accounts.
  The Automation dashboard owns rules and runtime — per-rule enable and shadow/live state, the `rules.toml` editor, the per-Project enablement matrix, spend, and diagnostics — and shows the global switches only as read-only state linking into Settings.
  The line users can state: Settings decides policy, the Projects registry decides participation, the dashboard shows rules and what ran.
- Every spending cap is edited through one control, wherever the setting lives, the way every model setting is edited through one picker.
  A cap is `{tokens?, usd?, mode}` and the control draws the mode first, because the mode decides whether the two figures under it mean anything (`features/budgets.md`).
  An axis the mode does not enforce stays editable and is labelled unenforced rather than disabled or cleared: it is a number the operator is keeping, not one that is unavailable, and discarding it would make trying the other unit a one-way door.
  Where the dollar axis is enforced against a provider that reports no cost, the control says the cap cannot bind there and names first-hit as the configuration that still bounds it - a warning drawn at the moment the choice is made rather than discovered later as spend that never approached a limit.
- Harnesses is the per-harness section: an enable toggle, the detected executable path (read-only), the executable override, default arguments as a command line, and the width envelope where the harness declares it. It lists every registered harness including disabled ones (`allHarnessesIncludingDisabled`), because a section that hid a disabled harness could not re-enable it. The enable toggle is three-state: leaving a harness untouched follows detection, and a `follow detection` control clears an explicit choice. A disabled harness is only hidden from the launchers; it stays spawnable and its history stays searchable (`features/backends.md`). The tab also holds native-history reconcile: the startup toggle, a `Scan now` control with progress and cancel, and the browser's page size (`features/history.md`). That is history indexing rather than harness configuration, but the scan is scoped to exactly the enabled harnesses, so the two are read together or not at all.
- The first-run harness panel appears once, gated daemon-side by `harness_setup_complete`, not device-local storage, so a choice made on one device does not reappear on another. It lists detected harnesses pre-ticked, offers a separate `scan history` choice, and a skip that writes only the completion flag. `Configure in Settings…` hands off to Settings → Harnesses rather than duplicating the per-harness editors.
- Git exposes the absolute `worktree_root` used by the Project Run launcher.
  An empty stored value resolves to `<data_dir>/worktrees`; the field displays that resolved default, and changing it does not move existing worktrees.
- Settings opens on the **tab it was last left on** (`mux.settings.tab.v1`, per device) and, within
  that tab, on the **section it was last left on** (`mux.settings.section.v1`, a per-tab map).
  A caller that names a section still wins, such as Voice from the read-aloud chip or Accounts from the account switcher, because that caller knows where the user needs to be.
  Only an unqualified open restores the remembered tab, and a pending search jump always beats a
  remembered section because that caller named an exact control rather than a region.
  A caller may also name **the control itself** rather than a section: a surface that is inert
  because a switch is off links to that switch, and Settings scrolls to it and flashes it on
  arrival (`setting-links.md`). Those links reach the Projects registry the same way, since it
  owns the per-Project switches Settings does not.
  It is a device preference rather than App state so it survives a reload, and it is validated
  against the live tab list, so a renamed or removed tab degrades to General instead of
  rendering an empty panel.
  Tab ids persisted by older builds are migrated rather than discarded (`LEGACY_TAB_IDS`), so a
  device that last used `workspace` reopens on Git instead of reading as the panel forgetting.
  The panel is opened, scanned, and closed many times in a session, and landing on General
  every time re-charges the navigation that reached the tab someone actually lives in.
- A tab of four or more sections carries a **sticky section rail** at the top of its scroller, on
  desktop and mobile alike. It is scroll anchors, never sub-tabs, and that distinction is load-bearing:
  every section of a tab stays mounted, so the search index can still see the whole tab, `Ctrl`+`F`
  still works, and the single Save transaction can never hide a dirty field behind a pane the user
  cannot see while a validation error at the top names it.
  The rail is derived from the `<h3>` elements the tab actually rendered — a `MutationObserver`
  keeps it correct as child panels (Accounts, Alerts, the WSL bridge) paint after their fetches —
  so a new section joins the rail the moment it renders and there is no declared list to update.
  Repeated headings are numbered (`railSectionIds`) because a remembered section id has to survive
  a reload. Scroll-spy selects the last heading above the rail's underside, and hitting the bottom
  of the scroller selects the final section, whose heading is usually too close to the end to
  cross that line. A rail click holds the selection for the length of the scroll: without that
  hold the late sections of a short tab are unpickable, because scrolling to one lands at the
  bottom and the bottom rule immediately re-selects the last section.
  On mobile the rail is one non-wrapping row that scrolls sideways, so it costs a fixed strip
  rather than growing into the content. It stays a rail on both layouts — it is per-tab and
  short, unlike the seventeen-entry section list that became a drawer beside it.
- **A section is one `<section>`, and a section that is reference may fold its body.** The rail
  is built from headings, so a tab can satisfy it while still rendering as one unbroken column;
  the borders between concerns come from the section boxes, and Automation, Remote, and Voice all
  draw one box per heading. Where a section is *reference* rather than a control someone came to
  change — a full command catalog, a diagnostic readout, a one-time setup — its body goes behind
  a `<details class="settings-disclosure">` while its `<h3>` stays outside. That split is what
  keeps the rail entry, the search index, and the scroll-spy intact while the tab stops paying
  screen for something read twice a year. `data-setting` marks stay outside a collapsed
  disclosure by convention: `revealSetting` opens the disclosures above its target, but a switch
  a gate just promised should be on screen when the panel lands, not behind one more state change.
- **Voice is the worked example, because it was the worst case.** One `<section>` carried eight
  headings — read-aloud policy, engine, budgets, microphone, seventeen phrase rows, the whole
  spoken-command catalog, a latency readout, a tester, mobile setup — with the pronunciation
  lexicon buried as an `<h4>` inside the engine block's Kokoro branch. It is ten sections now, in
  reading order, three of them folded, with the lexicon owning one; the read-aloud policy stays
  one numbered block because its three layers are only useful read together. The full list and
  the rules are in `features/voice.md`, and `frontend/test/renderer/voice-settings.spec.ts` pins
  them.
- Opening loads one `GET /api/settings/bundle` (config, keybindings, profiles,
  projects, automation, provider, usage, project config) instead of nine per-section GETs,
  so a high-RTT client (phone over Tailscale) pays a single round trip. The panel chrome —
  header, section list, footer — renders immediately with a placeholder content area; tabs are
  selectable before data lands. The placeholder shell and the loaded one render that chrome
  from the same expressions, so the two cannot drift into different headers. `config` is required; other parts degrade to null with the
  reason under `errors`, except `keybindings`, whose absence blocks the
  form because Save writes it back unconditionally. The rules.toml text is deliberately not in
  the bundle or the Save: the Automation dashboard owns its editor (`automation.md`). Remote,
  voice, and firewall status stay separate non-blocking fetches.
- The Remote tab is one section per concern: the Tailscale connection state (not-installed,
  logged-out, connecting, stopped, or connected-as-`<device>.ts.net`) with cause-pointing
  next-step text, a "Connect a phone" button, a Windows-only Defender Firewall panel with a
  one-click Repair button when a blocking or missing rule is found, the WSL bridge, the secure
  HTTPS address, and a phone setup checklist (Use Tailscale DNS on, Android Private DNS off or
  automatic). The Voice tab's Mobile voice section deliberately repeats the connection state,
  secure-address button, and phone checklist: someone setting up dictation should not have to
  leave the tab, and Remote remains the canonical copy. It is the copy that folds, because it is
  done once; Remote's own sections stay open, since that whole tab is setup. Both tabs read the phone checklist from
  static copy because the daemon cannot detect the phone's DNS state. The firewall and WSL panels
  render nothing off a supported host, and because each now owns a heading, Settings states the
  unsupported case rather than leaving a heading with nothing under it.
- "Connect a phone" opens a modal (`ConnectPhone.tsx`) with a scannable QR of the connection URL
  (the `.ts.net` MagicDNS name, secure Serve address when up), a system-prerequisites checklist
  (Git, Node, npm, Tailscale, each with a next step), and a security-posture line stating that any
  tailnet device reaches the daemon with no login.
- The Diagnostics tab holds the standing system-prerequisites checklist, the three
  session-preserving reload actions (`ui.reload`, `daemon.reload`, `app.redeploy`), and an Export
  diagnostics button that copies one bundle to the clipboard with a selectable textarea fallback.
  The reload buttons dispatch the app's own command registry rather than re-implementing the
  paths, so a change to what "reload daemon" means reaches this panel for free, and a command the
  host does not offer disables its button instead of failing when pressed.
- Settings -> Harnesses renders two per-harness instrumentation toggles under each harness: the mux MCP
  server (offered only where the `mcp` capability is set) and "Instrument with mux hooks", whose
  off state shows an inline warning that a clean launch drops the harness to unobserved. Both note
  that the change applies on the next daemon restart. Each harness also shows its detected CLI
  version, flagged when it is newer than the version mux was tested against.
- Settings -> Voice defaults microphone input off; enabling it shows a note that the first Talk
  downloads the local Whisper model, and the language/model inputs are framed as a first-use choice.
  Settings -> Accounts lists what one OpenRouter key unlocks.
  The scan-timeline model is an editable, changeable default rather than a fixed read-only value,
  and it sits with the caps it is priced against under Automation -> Scan timeline rather than a
  tab away, so "which model is this budget being spent on" is answerable without navigating.
  The first-run panel discloses
  what mux injects per session and points at the next onboarding steps (project, CLI login, session,
  phone).
- **Every `Config` field the daemon enforces has a control here, or a written reason it has
  none.** A setting with no control is unreachable from the app and invisible to the search
  above, and nothing noticed thirty of them accumulating that way by 2026-08-21 - the panel's
  own tests only ever walked from a *link* to its control, which cannot see a field that never
  had one. `frontend/test/settingsCoverage.test.ts` walks from `config.py` instead, and the
  escape hatch is a named list with one sentence per entry (`setting-links.md`).
  A control also has to be honest about *when* it takes effect: a field whose owner reads it at
  use time applies on save, one whose owner is constructed at startup says so in its own help
  text and is in `RESTART_FIELDS`, and `tests/test_settings_hot_apply.py` holds the four that
  had to gain hot-apply wiring to keep the first claim true.
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
  The section rail is excluded from the index and from the jump's candidate scan alike: its
  buttons repeat every heading, so indexing them would duplicate results and counting them as
  candidates would shift the occurrence a recorded result points at.
- Every OpenRouter model setting uses the same filtering combobox, wherever it lives.
  It accepts typed queries and filters the cached catalog live by model name or exact ID, and its
  listbox scrolls inside a bounded desktop or mobile height instead of expanding to the height of
  the catalog.
  A native `select` cannot search hundreds of entries and cannot carry a second line, and a text
  input accepts a typo the daemon then rejects as an exact-ID validation error, so neither is used
  for a model any more.
- A picker row states the model name, then its exact ID and its price on one meta line.
  The ID stays visible despite reading much like the name, because it is what the collapsed
  control shows, what the config stores, and what the filter ranks on: a search result whose
  match is invisible cannot be explained.
  Price is per **million** tokens, input then output, converted from the per-token figures
  OpenRouter quotes, because at two decimals every model in the catalog is `$0.00`.
  Four values are not prices and never render as one: absent pricing renders nothing rather than
  `$0.00` (free and unknown are opposite answers), a wholly zero pair renders `free`, OpenRouter's
  negative marker renders `variable`, and a figure below the last printable digit renders
  `<$0.001`.
  The row cannot say which figure is input and which is output, so the option's title does.
- Wide, the ID yields and the price holds an auto-width right-aligned column, so figures are
  read down the list against each other.
  Narrow, the two stack: a setting's control column is about 216px on a phone against a price
  cell wanting 155-192px, and side by side the ID is erased with no hover to recover it from the
  title.
- Settings → Accounts → Models lists **where each model is used** as a read-only inventory:
  every feature, the model it resolves to under the edits currently in the form, its price, and
  a control that opens the setting deciding it.
  It is an index, not a second editor - two controls writing one config key is how a panel starts
  disagreeing with itself.
  Every row opens a real control now.
  The Project context card's model was the one that did not: its row said "Configuration file"
  and told the reader to edit `project_card_model` by hand, which is exactly the defect an index
  exists to surface rather than to record.
  It is edited with its budget and its per-build token ceilings, under Automation → Budgets and
  execution, like every other feature model.
- Settings → Accounts → **Model provider** chooses *which endpoint* every one of those models is
  requested from: OpenRouter's hosted catalog, or one OpenAI-compatible `/chat/completions` the
  operator runs (llama.cpp, Ollama, vLLM, LM Studio).
  It sits above the OpenRouter key rather than below it, because it decides whether that key is
  the credential in play at all, and a reader who chose a custom endpoint should not scroll past a
  key section that no longer applies to find out why.
  A custom endpoint is three fields - base URL, an optional key, and one model - and its model is
  a **pin**: blank is a validation error, because there is no routed default a local server could
  inherit.
  While it is selected, the index above says so and every row resolves to that one model, since a
  table still listing seven OpenRouter ids while one local model answers all of them would be the
  most misleading surface in the panel.
  The OpenRouter section stays, with a note that its key is stored and unused.
- **Verification** is per configured provider, not per active one: an operator proving a local
  endpoint wants to prove it *before* switching the install onto it, and a verify that only worked
  on the live provider would force exactly the risky ordering.
  It sends one tiny completion and prints the reply, because reachable and usable are different
  findings and only the words separate them - an endpoint answering with an empty string or a chat
  template's own scaffolding passes every check a tick could make.
  Each row shows one of three states, never two: verified (with the reply and when), *endpoint
  changed* (a record exists and no longer matches), or not verified.
  OpenRouter shows "no verification needed": storing its key already tests it against an origin
  swe-mux ships, so configuring it is verifying it.
  A failed verification changes nothing, including a previous success - an endpoint that worked
  yesterday and is unreachable this minute has not been disproven.
- The footer carries only draft state: status, Cancel, Save. Whole-config actions — reveal the
  config directory, export a sanitized copy, restore defaults — live in a General-tab block,
  because a footer repeats under every tab and so implied a per-tab scope none of them have
  (restoring defaults rewrites the entire saved config immediately, outside the draft/Save
  cycle). It also kept Cancel/Save in a horizontally scrolling footer on phones.
  Per-section resets that genuinely are scoped, such as gesture defaults and shortcut defaults, stay with their own section.
- Action layout is not a Settings section.
  **Configure Actions** opens as a standalone modal from the main menu, command palette, the in-place rail editor's "All options…", or the Quick actions section in the Actions drawer.
  This surface owns the shared catalog, custom action creation, and all four Desktop/Mobile Rail/Drawer placements; the rail gear itself opens the lighter in-place editor.
- Keyboard shortcuts distinguish browser-reserved chords from desktop-only chords. WebView2
  releases the latter to the app, while an ordinary browser keeps its own tab/window behavior;
  Settings exposes both categories and accepts `Ctrl+Tab` / `Ctrl+Shift+Tab` as mappable desktop
  inputs. Modified Tab chords never enter focus traps, drawer-tab traversal, or editor indentation.
  Application-reserved UI scale chords are fixed controls rather than command bindings, so a saved
  binding cannot compete with browser zoom suppression or leak the same input into xterm.
- Notes configures the shared Markdown editor behind every note and Markdown file: spellcheck,
  Markdown rendering, `Tab`, typography, the touch command rail, and the editor's own shortcut
  policy and per-chord overrides (`project-resources.md`). The chord table is enumerated from
  the editor package rather than hand-listed, so it cannot drift from what the editor binds.
- Voice lists the full spoken control surface in collapsible groups, in a Command reference section that is itself folded away by default.
  Fixed query and navigation grammar comes from the same reference used by spoken help, while current Project, session, panel, launch, status, and approval aliases come from the live command registry.
  Guarded aliases remain listed while unavailable and name the state they require.
- Appearance exposes one palette picker for the shared browser chrome and xterm theme.
  Every option shows the same six fixed-width color swatches, so palette comparison does not depend on label length.
  The custom listbox supports pointer selection, Up/Down/Home/End navigation, Enter/Space selection, and layered Escape dismissal.
  **Highlighting a theme applies it to the whole window immediately**, by arrow key or by
  hover, so a catalogue of twenty-eight can be walked and seen instead of chosen blind,
  reopened, and chosen again. It is a preview and not a choice: the draft moves only on
  Enter or click, the dirty flag never fires, and the trigger keeps showing the chosen
  theme rather than the highlighted one. Leaving the list any way at all hands the screen
  back — Escape, a click elsewhere, or the one gesture that closes the list and the whole
  panel together, which `Settings.tsx` owns rather than the picker precisely because the
  picker is gone before its own revert could run. The revert targets the *authoritative*
  theme rather than the draft's, since discarding unsaved settings has already put the
  saved theme back on its way out.
  The control is laid out as an ordinary field — label column, bounded control column —
  rather than stacked full-width, which had made the one setting in the panel that spans
  the label column read as a section heading.
  The built-in retro set includes Phosphor Blue, Phosphor Purple, Commodore 64, Amiga Workbench, CGA, Macintosh System 6, Game Boy, and Virtual Boy.
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
  - `Ctrl+wheel`, `Ctrl++`, and `Ctrl+-` move one step, while `Ctrl+0` restores `1.0`.
    The capture listener runs before browser zoom, xterm, editors, and configurable command bindings;
    exact scale inputs are consumed and every other wheel or key combination continues normally.
    High-resolution wheel streams accumulate into discrete steps, reset on reversal or a pause, and
    never turn one oversized event into a jump across multiple steps.
    Every accepted input reports `UI scale <percent>` through the shared bottom-right interaction HUD.
    Outside Settings the final value is persisted after a short debounce; inside Settings it joins the
    existing draft so Save and Discard remain atomic with the panel's other changes.
  - The Appearance selectors, shortcut inputs, config refresh, chrome, and xterm all pass through the
    same browser scale state.
    A live selector or shortcut preview therefore changes terminal type without disposing the terminal,
    and discarding the Settings draft restores both chrome and terminal type together.
  - The split is by device class because the same UI is driven from a desktop browser and a
    phone and one number cannot say "the phone is too small but the desktop is fine". A window
    resolves its value through the same `(max-width:760px)` breakpoint as the mobile workspace
    projection and the device-class settings profiles, and re-resolves when that breakpoint
    flips, so a desktop window dragged narrow adopts the mobile scale live. Both values are
    editable from either device — sizing the phone from the desktop is the point, since the
    phone is the harder device to type on — and the panel says which of the two the window
    you are looking at is currently using.
  - The sidebar row layout is deliberately **not** split by device class.
    It lives in one canonical `sessionRows` settings domain, and mobile differs only by the
    `mobileFields` flag inside that one blob.
    Sound and notification behaviour genuinely differ per device; a row layout does not, and a
    second copy would only be a thing to keep in sync by hand.
  - Excluded from it entirely: only the note editor, whose typography is its own
    `--continuity-*` setting under Notes. The terminal was excluded at first — it has its own
    font size and its cell grid feeds cross-device viewport arbitration
    (`features/terminal-input.md`) — but leaving the largest surface in the window at a fixed
    size while everything around it grew is not what the setting is asked for, and the
    arbitration consequence turned out to be the correct behaviour rather than the objection.
- Appearance also exposes **rail density** (`Comfortable | Compact | Dense`), stored per device
  class beside chrome scale and defaulting to Comfortable on both.
  It is a separate setting rather than another thing chrome scale multiplies, because scale is
  about *type you cannot read* and this is about *height you would rather give the terminal*.
  The Action rail is the one strip drawn under every pane at once, so on a desktop showing four
  terminals a step of density is four rows of output back.
  - Comfortable is the spacing that has always shipped, and it is expressed as the stylesheet's
    own `:root` values rather than as a third selectable block, so `railDensity.ts` writes no
    attribute at all for it.
    A device that has never opted in, and a browser whose daemon never answered, therefore
    render a file indistinguishable from the build before the setting existed — the same
    "the default must stay inert" check chrome scale is held to.
  - The steps are one variable group — gap, chip height, chip side padding, container padding,
    row height, and the overflow chip's fixed width — declared once per step in `style.css`.
    The numbers live there and not in TypeScript because they are six lengths that must move
    together; what crosses the boundary is one `data-rail-density` attribute on the root element.
  - The mobile group is a **second set of numbers, not the desktop set scaled**: a phone's
    Comfortable chip is a 44px touch target, which is a floor rather than a multiple of the
    desktop's 27.
    Below Comfortable a phone's chips fall under that guidance, and that is the content of the
    choice rather than an oversight — the setting exists to trade reach for rail height, it is
    opt-in on the device it applies to, and the panel says so.
  - Previewed live as it is picked, and restored with theme and scale when the draft is
    discarded, because the only way to judge a density is to see the rail at it.
- Terminals exposes `auto | webgl | dom` renderer selection.
  `auto` preserves accelerated WebGL for desktop shells with automatic DOM fallback, and keeps scrollback-repainting harnesses on DOM.
  Claude and OMP are also DOM-only, including under an explicit `webgl` preference: their repainting surfaces can return from a retained hidden interval or deep replay with a live but intermittently mangled WebGL context, no context-loss event, and no reliable recovery short of a real resize.
  Mobile is DOM-only regardless of the preference, because Chromium device emulation can keep a live context across a pixel-ratio change and leave the pane blank.
  An explicit `webgl` preference still reaches Codex: its exclusion belongs to the default, while OMP's continuous repaint makes the unsafe override look like incomplete replay and is not exposed.
  Its failure mode is visible (torn or blank scrollback) and reversible by selecting `dom`.
- The WebGL addon is constructed with `preserveDrawingBuffer: true`, and that is load-bearing
  rather than a tuning choice. `WebglRenderer._updateModel` skips any cell whose code, fg, bg
  and ext match its model, so a frame re-uploads only what changed and every other pixel is
  assumed to still be in the drawing buffer. Under the default `false` the browser may discard
  that buffer as soon as the canvas stops being composited, which can happen to a warm pane behind
  another tab. The pane then returns with only the changed
  cells drawn, and dragging a selection over the gaps repaints them — the "it draws once I
  highlight it" symptom. Nothing fires when a compositor drops a buffer, so an event-driven
  repair cannot cover this and the assumption is what has to go.
- Repaints are still repaired on the events that *are* observable (pane shown, intersection,
  `visibilitychange`, `pageshow`, window focus, replay end, context loss), plus one confirmation
  pass a settle later. The terminal's memo boundary compares pane visibility so a tab-only
  transition cannot swallow the show event before it reaches the retained xterm instance.
  Pane restoration also forces xterm's renderer-dimension path after the fit: both FitAddon and
  public `term.resize` return early when the cell grid is unchanged, even though a renderer
  returning from a retained hidden interval can still hold a stale pixel surface in the upper-left part of
  its host. `reflowVisibleTerminalRenderer` temporarily toggles and restores the public,
  non-geometric `customGlyphs` option; xterm treats that option as renderer-invalidating and
  invokes `handleResize` without changing the grid or sending a PTY resize frame. The explicit
  Resize action uses the same repair. The confirmation is surface-only — atlas clear and
  refresh, never a refit — because a fit is
  `term.resize` plus a pseudoconsole resize plus a full CLI repaint, and none of that is what a
  lost paint needs. xterm's `RenderService` fires `onRender` whether or not the renderer drew
  anything, so a dropped paint is invisible to the app and is never retried by the library;
  assuming a single redraw landed is what left panes half-drawn.
- The confirmation is owed by any pass that reshaped the surface — a changed grid *or* a changed
  host box — not only by the passes that invalidate the texture atlas. Gating it on the atlas
  left the one resize path most exposed to a lost paint as the only path with no confirmation
  at all: a soft keyboard animates for ~250-400 ms and refits the pane throughout, so the pass
  that runs when the burst settles reliably paints into a layout still in motion, and nothing
  asks the pane to draw again afterwards. The visible result was the strip of terminal the
  keyboard had just vacated staying blank until some unrelated event repainted it. `confirmedSurface`
  records the shape the last confirmation actually drew, and is written only by that confirmation:
  a pass that painted into a moving layout is precisely the one whose result must not be believed.
- The confirmation forces the renderer-dimension repair before it repaints, for the same reason
  pane restoration does: a box that changed while the grid did not leaves the renderer sized for
  the old box, and refreshing rows into a stale surface repaints only the region that was already
  correct.
- Surface repair is persistent debt rather than a one-shot animation frame.
  A reveal or confirmation that lands before a logically visible host becomes measurable keeps the debt, retries for a bounded number of frames, and stops without spinning if the pane remains hidden.
  The first later successful viewport measurement resumes it, and the terminal health sweep is the final recovery path if no resize or intersection event follows.
  Only a successful renderer reflow and row redraw clears the debt or advances `confirmedSurface`.
- Viewport fitting has separate persistent debt.
  A nonzero host is not sufficient evidence of a fit because FitAddon can still lack usable cell metrics while layout settles; only a finite dimensions proposal followed by geometry application clears the debt.
  Renderer recovery resumes pending fit debt, and the health sweep compares the current grid with a fresh FitAddon proposal so a faithfully rendered 20-row surface inside a host that now fits 40 rows cannot be mistaken for healthy.
- A retained warm pane keeps parsing but not rendering.
  Its renderer is explicitly paused while hidden and resumed on reveal ahead of the reveal's redraw, because xterm's own pause is geometric and never triggers for a measurable `visibility:hidden` box (`terminalRenderPause.ts`, `technical/frontend/workspace-state.md`).
- Warm retention is desktop-only and remains capped at three hidden terminals across the workspace.
  Mobile mounts only visible terminals so offscreen PTY sockets cannot consume mobile bandwidth.
- Desktop agent panes apply backend-specific width envelopes before registering PTY geometry.
  Claude's terminal body stops at `claude_max_columns` and remains centered when the pane grows wider, because Claude Code's live-region renderer can leave stale and duplicated cells across large width changes.
  The setting offers a fixed set of steps plus `0` for no cap, defaults to the historical 120 columns, and lives in Settings → Harnesses rendered for any harness declaring `applies_width_envelope`, not by CLI name; it is a setting rather than a constant because the defect it answers belongs to an independently released CLI, and a cap that outlives its evidence silently costs width.
  The corruption itself is repaired at its source by the settled-resize repaint pulse (`features/terminal-input.md`), which makes the child restate the screen the user stopped on; the envelope is now a width preference rather than the only defence, and `0` is a reasonable setting.
  A capped pane whose width change is clamped raises a transient notice naming the limit and offering the setting, since the symptom - text that stops widening while margin appears - otherwise reads as the CLI refusing to resize.
  That notice yields to the ownership and letterbox notices, which share its slot and describe geometry the user has less control over.
  `0` removes the host style entirely rather than relaxing its maximum, so a disabled envelope is the same code path as no envelope.
  The centered grid item retains an explicit `width:100%` before its maximum is applied.
  Centering without that definite width makes CSS Grid intrinsically size the host from xterm's own fitted child, creating a repeated shrink-and-refit loop.
  Codex panes that would fit fewer than 80 columns reduce their font, down to 8 px, and fit again before attach or resize; this preserves Codex's documented 80-column composer floor for ordinary narrow desktop panes.
  The compact mobile workspace keeps its normal readable font and native narrow geometry.
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
- The notes step is anchored on whichever **Notes** control the drawer currently shows: the
  launcher rail's button while the drawer is closed, and the drawer's own pane strip while it is
  open. Exactly one carries the anchor at a time, which a click-gated step requires.
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
- Nav is a mark rather than the `:nav` label. No word survives at this width, and pinning a
  font size to force one would ignore the user's UI-scale setting, which this button is subject
  to through an `!important` rule. It and the side-panel toggle are one mirrored box (36 × 44
  px): whatever is true of the tap target for one drawer is true of the other. 24 px was too
  narrow to hit reliably — the 44 px height alone does not rescue a target that thin, because a
  thumb's contact patch is wider than it is tall — and the mark scales with the box, or a
  wider button only frames a 9 px glyph in dead space. Both drawers also open by swipe, so
  neither toggle is its panel's only entry point.
  The mark itself is the mirror too: `NavPanelIcon` is `SidePanelIcon` reflected, a frame with
  its *left* column partitioned off. The pair only reads as a pair if their marks are one mark
  reflected, and the `≡` it replaced named no panel at all — it was a menu glyph on a button that
  opens a drawer.
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
  cwd on desktop; touch hides the shell cwd via `.pane-bar>.pane-path` so the name, voice group,
  and tools remain on one row.
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
- While the left sidebar is open, either horizontal swipe direction closes it instead of running that slot's binding; while the right utility drawer is open, a rightward swipe closes it.
  The override applies to one- and two-finger horizontal swipes alike, even to unbound slots; the drawer wins when both panels are open because it overlays the sidebar.
  Slide-in panels are dismiss-stack levels for system Back and Escape, but `gestureOverlayDepth` excludes their entries from modal-overlay gesture precedence.
  Resolution is a pure layer between recognition and dispatch (`resolveGestureCommand`), toggled by the hot-reloadable `mobile_gesture_swipe_away_close` config bool (default on, checkbox in Settings → Input → touch gestures).
- **The platform back gesture steps back one place inside the app.**
  swe-mux installs as a `display: standalone` PWA, where back is the primary navigation control, and the app keeps no route history of its own (the URL is only ever `replaceState`d to track the focused session).
  With nothing to pop, Android's back backgrounded the whole app while a modal was open.
  `systemBack.ts` keeps **one** sentinel history entry alive for exactly as long as the back target has somewhere to go: pushed when the first level opens, consumed by the platform on a back gesture, and re-pushed if anything remains.
  One sentinel rather than one per level is the invariant that matters — per-level entries desynchronize the first time a level closes by button instead of by back, with nothing able to resynchronize them.
  Closing the last level steps back over the sentinel so the next back press is not silently swallowed, and the popstate that step causes is counted and ignored rather than read as a user gesture.
  A sentinel that is no longer the current history entry is dropped rather than stepped over, because navigating the user somewhere they did not ask to go is the worse failure.
- The one back press this deliberately does not see is the one that dismisses the Android soft keyboard: the platform consumes it and never tells the page, so an overlay behind the keyboard survives the first press.
  That matches how the keyboard shadows back everywhere else on the platform and is not special-cased.
- **With nothing layered, back steps through the tabs and Projects most recently looked at, then leaves.**
  Closing the last overlay used to be the end of what back could do, so a phone user reading a session - by far the most common thing to be doing - had back background the entire PWA.
  `viewHistory.ts` holds a ring of the last ten distinct `(Project, view)` pairs, recorded from the **committed** focus pair rather than at the two dozen call sites that set focus, because only the settled value is what the user is actually looking at and per-call-site recording would rot the first time a new flow forgot it.
  It is an in-memory ring rather than one history entry per view, and that is the load-bearing choice: Chrome's history-manipulation intervention marks entries pushed without a user gesture as skippable, focus here moves programmatically constantly (a spawn, a resume, a branch, a closing pane handing focus to its neighbour), and those entries would quietly stop being poppable so that back left the app at random.
  Keeping the ring in memory means the feature adds no history entries at all - `systemBack.ts` maintains the same single sentinel it already did.
- Four rules are what separate that from a gesture that traps the user, and each is a test.
  A traversal **consumes** its entry and never re-records, because a ring that refills itself is a cycle back can never walk out of, which is worse than the bug being fixed.
  Recording is **MRU-distinct and excludes the destination**: a revisited view moves rather than repeating, and arriving somewhere drops it out of the ring, so flipping between two tabs twenty times still leaves exactly one step back and ten presses is a bound rather than a typical walk.
  Dead entries - a closed pane, an ended session, a removed Project - are **skipped when back is pressed rather than pruned when pushed**, since what is reachable is only knowable at that moment, and a run of them costs one press in total rather than one press each.
  The **traversal echo** (restoring a view makes the recorder observe a move *to* it) is recognized by identity with the entry just handed out, not by a "skip the next record" flag: a flag is silently eaten by a restore that changed nothing, taking a real navigation with it.
- A back that lands on another tab shows the same `InteractionHud` label pill a swipe does, prefixed with the Project name when the step crossed one.
  Same reason as the swipe: a tab change the eye misses is indistinguishable from "back did nothing", and the user presses again and leaves the app.
- The ring is **armed on the mobile layout only**, though it is recorded on every layout.
  On the desktop the tabs are on screen and one click away, and a permanently armed sentinel would stop the browser's own Back button from ever leaving the site - the same reason the docked sidebar and drawer are not dismiss levels there.
  Recording regardless is what lets a phone rotate across the 760 px breakpoint and back with its history intact instead of wiped.
  Liveness and the layout mode are state the ring cannot see, so `App` tells it when they move rather than having it poll; otherwise the sentinel stays armed against entries naming nothing.
- `gestureOverlayDepth` keeps reading `dismissStack.depth()` and never the composite.
  It asks "is an overlay painted over the workspace", and that answer is what resolves every non-back gesture slot to nothing - counting navigation history there would make it true permanently, and one tab switch would kill every gesture on the device.
- The hot-reloadable `mobile_back_view_history` config bool (default on, checkbox in Settings → Input → touch gestures) restores the original behaviour, where back on a session closed swe-mux outright.
  `nav.back` remains one command for both rungs, so a key binding, a gesture slot, and the palette entry all step back the same way.
- The same motion is available in-app as a **rightward swipe** while any level is open, since Android owns the edge-anchored swipes and only a mid-screen one is available.
  The overlay wrappers are in the recognizer's target allowlist solely to carry it, and the list is every wrapper rather than the most common one: `.modal-layer`, `.settings-layer`, `.usage-layer` (usage, automation, fleet queue, observations, bandwidth), `.process-layer`, `.folder-picker-layer`, and `.palette-layer`.
  Listing only `.modal-layer` left the swipe silently dead on most of the app's large surfaces.
  Every class in that list belongs to a surface that registers a dismiss level, which is the condition for adding one: a listed surface that registered nothing would let a swipe run its workspace binding behind the overlay.
  The floating voice dock is deliberately excluded on exactly that ground.
  Overlays remain immune to gesture *hijacking* by a stronger rule than the old one: whenever the dismiss stack is non-empty, `resolveGestureCommand` resolves the back slots to `nav.back` and **every other slot to nothing**, so no binding can run behind a modal.
  Turning off the hot-reloadable `mobile_gesture_overlay_back` config bool (default on, checkbox in Settings → Input → touch gestures) restores the original behaviour of an overlay swallowing every gesture, rather than letting the old bindings back through.
  The platform back gesture is unaffected by that switch.
- **An overlay that owns a left drawer of its own borrows the workspace sidebar's gesture for it.**
  Settings on a narrow layout is the only one today.
  `resolveGestureCommand` takes that drawer as an `OverlayLeftPanel` and applies the two rules the workspace sidebar already has, one level up: whichever slot is bound to `sidebar.toggle`/`open`/`close` opens it, and while it is open either horizontal direction closes it.
  The opening slot is *derived from the binding* rather than hard-coded, which is what makes it a mirror — rebinding the workspace sidebar moves both drawers together.
  Every other slot still falls through to the back rule, so backing out of the overlay never stops working: with the defaults, the two-finger rightward swipe opens the drawer and the single-finger one is still `nav.back`.
  The panel is supplied to the resolver only while Settings is the level back would act on (`dismissStack.topLabel()`), so a picker opened above it keeps the swipe for itself instead of quietly working the drawer underneath.
  Turning `mobile_gesture_overlay_back` off suppresses this along with the back swipe: "a dialog ignored every swipe" is the behaviour that switch restores, and a drawer inside the dialog is not an exemption from it.
- `nav.back` is a registered command like any other, so it is bindable to a key, assignable to any gesture slot, and reachable from the palette.
  It is unconditionally available rather than gated on stack depth: availability is a render-time snapshot, and a drill-down level owned by its own component opens without re-rendering the composition root, so a depth gate would refuse the command at exactly the moment the user swiped back.
  `pop()` is already inert on an empty stack, which reaches the same outcome without the stale claim.
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
  It does not focus the terminal input, so it raises no mobile soft keyboard — and it lowers
  none either.
  The chip is a sibling of the terminal host rather than a child, so the host's own
  focus-preserving `mousedown` guard never covered it: pressing it moved focus to the button,
  Android lowered the keyboard because focus had left the field holding it up, and the
  resulting `visualViewport` resize refit the pane away from the line the jump had just
  reached.
  The chip carries `holdSoftKeyboard`, which cancels the press's default focus move while a
  field is holding the keyboard up, and is inert otherwise.
- That check reads xterm's buffer, which is silent about a whole class of sessions. An
  application holding the mouse (Claude does; Codex enables no mouse mode at all) is handed
  every scroll gesture — the wheel on a desktop, and the drag a phone forwards as one via
  `mobileDragTarget` — and scrolls its own viewport, leaving xterm's pinned to its tail. So
  `offTail` never fires there, and a chip that depended on it alone would never exist in a
  Claude session at all.
- The pane therefore keeps a running estimate of where that second viewport is, since nothing
  reports it.
  `trackAppTailDistance` totals the scroll the pane forwards, in the rows it actually handed the
  application, and the chip is `appOffTailByDistance` reading the total as at least one rendered
  row.
  Both directions count.
  Vertical touch scrolling tracks finger travel linearly, scaled only by the explicit sensitivity setting; the default `1.0` keeps content under the finger instead of accelerating during a drag.
  A drag back through the history raises the chip, and a drag toward the newest output spends
  the same total back down to zero and takes it away, so a reader who scrolls their own way
  back is not left with a chip that only its own tap can dismiss, sitting over a viewport
  already exactly where the tap would send it.
  One row is the threshold because the pane converts drag pixels into whole wheel-button reports
  and carries the remainder (`terminalScrollSteps`), so a drag worth less than a row moves
  nothing behind it - and a finger resting on the glass delivers a pixel or two of jitter per
  touch event.
  The total is clamped at zero because the application clamps at its own tail: banking credit
  for scrolls that moved nothing would delay the next chip by exactly that credit.
- The estimate is reset outright, rather than spent down, by the four events that make it
  meaningless: taking the jump, the Action rail's `^End`, a session switch in a reused pane,
  and an alternate-screen switch (the application that owned the tracked viewport starting or
  exiting, after which `offTail` answers for whatever replaced it).
- Two things pull the estimate off the truth, in opposite directions, and the pane can observe
  neither.
  Dragging past the top of the application's own history totals distance nothing travelled, so
  a drag that did reach the newest output can leave the chip up, and its tap is the answer.
  Output arriving while the reader is scrolled up moves the tail with no gesture to total, so
  the chip can leave early, and the drag already in progress is the answer - any drag back
  through the history puts it straight back.
  Every flip and every reset (with its reason) goes to the render-diagnostics ring as
  `app_tail_estimate` / `app_tail_cleared`, because the estimate is the one part of the chip
  that nothing on screen can be checked against.
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
- A viewport pass preserves the reader's position, not only the tail. `scrollTerminalToTail`
  covers a viewport that was on the newest line; one that was deliberately scrolled up is
  restored by `restoreTerminalScrollAnchor` to the same **distance from the tail** it had before
  the fit. Distance rather than an absolute `viewportY`, because the resize is what moves
  `baseY`: a ConPTY buffer grows with blank rows instead of pulling scrollback down
  (`windows_pty_compatibility`), and shrinking pushes rows the other way, so only the offset
  from the newest line keeps the same text under the reader.
- Without that anchor the clamping described for `scrollTerminalToTail` had nothing to correct
  it. The scroller advertises a stale maximum for the rest of the pass, so each refit landed the
  viewport a little short, and a soft keyboard refits throughout its ~250-400 ms animation. The
  visible result was a session walking toward the top of its transcript when the keyboard opened
  or closed — only ever in a session whose history lives in scrollback, since an alternate-screen
  application has no scrollback for a viewport to walk through.
- Returning from a retained hidden interval restores the tail only for a viewport that was on it. A reader
  who had scrolled up keeps their place across a tab switch; the pass that runs on reveal has
  already restored their anchor.
- Reaching the tail likewise means the *application's* viewport, not only xterm's: scrolling
  the terminal alone lands on a view nobody was looking at, which is what the rail's `^End`
  has always avoided by sending the key on its way past the local scroll. `appOwnsTail` is the
  rule: any backend but `shell`, when it is Claude or is being handed this pane's scrolls. A
  shell is excluded because it owns no viewport and the bytes would land in a half-typed
  command line.
- An alternate-screen session (Claude Code enables `?1049h`, verified against v2.1.224)
  shows no vertical scrollbar by construction: the alternate buffer has no scrollback, the
  transcript lives in Claude's own internal viewport, and mux can steer that viewport (wheel
  forwarding, `^End`, the jump chip) but cannot read its position or length, so there is no
  scroll range to draw a thumb for. Codex and OMP show a scrollbar because mux keeps their
  transcripts in real xterm scrollback.
  Claude Code does support an inline mode (`CLAUDE_CODE_DISABLE_ALTERNATE_SCREEN=1` at
  launch, `/tui default` mid-session, `"tui": "default"` in settings.json), which mux
  deliberately does not enable yet: the Claude descriptor's `screen`, `repaints_scrollback`,
  the `appOwnsTail` scroll-forwarding rule, and touch caret placement all assume the
  alternate screen, so adopting it is a mode migration to be done as its own change, not a
  launch-flag tweak. The key is sent off the broadcast path: a viewport gesture belongs to the pane
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
- A terminal touch that is not a typing tap leaves the soft keyboard exactly as open or closed
  as it found it. Not raising it is only half of that, and was the only half the pane
  implemented: every touch here lands on non-editable content, because the mobile IME bridge is
  a 1px `pointer-events:none` field, and Android lowers the keyboard whenever a touch resolves
  against something other than the field holding it up. A long-press-and-drag to select and copy
  therefore closed the keyboard with nothing in the pane asking for it. The pane records the
  keyboard's holder at `pointerdown` and hands it back at `pointerup`/`pointercancel` unless the
  gesture resolved as a typing tap, which owns the focus decision itself. Restoration is deferred
  one frame and ordered after the selection copy, because the platform makes its own focus
  decision as the tap resolves and would otherwise undo a restore issued inside the handler.
- Restoration is scoped to a keyboard actually lost: focus that moved to another text field kept
  the keyboard up, so it stands rather than being pulled back to the terminal (`softKeyboardLost`
  in `mobileKeyboard.ts`). A gesture that began with the keyboard already down restores nothing,
  since raising one the user never asked for is the failure the whole path exists to avoid.
- A deliberate dismissal outranks the restore. `dismissSoftKeyboard` counts every request, the
  pane records that count when a touch lands, and a restore is abandoned if the count moved while
  the gesture ran. Without that precedence the edge swipe that opens the left sidebar fought
  itself: the swipe crosses the terminal, so the pane saw a gesture whose focus had gone missing
  and handed the keyboard straight back into the panel that had just dismissed it. The same swipe
  begun on the top bar never reaches the pane's pointer handlers, which is why only the one
  crossing the session misbehaved. Counted on intent rather than on whether a field happened to
  be focused at that instant, which during a gesture is a race rather than a decision.
- One gap remains, and it is not reachable through focus. Dismissing the keyboard with the
  Android back gesture hides it without blurring the field, so the pane sees an unchanged holder
  and Chrome may re-raise the keyboard on the next touch that resolves against that same focused
  field. Closing it needs an `inputmode` gate rather than focus bookkeeping. The keyboard toggle
  (`terminal.keyboardToggle`) is unaffected: it blurs, so the pane and the platform agree.
- A still primary tap or click inside the currently editable agent composer moves its caret.
  Which path a pane takes follows the terminal's measured mouse mode rather than which harness is running.
  A pane whose application negotiated mouse reporting (Claude, opencode) already receives desktop clicks through xterm's native handling; on touch the pane synthesizes the mouse pair xterm expects, because a touch's own compatibility mouse event is the one it suppresses, and xterm encodes the coordinates in the mode the application asked for.
  A pane with no mouse mode (Codex, OMP, pi) is steered instead: the pane recognizes that harness's composer from its measured shape and converges on the tapped terminal cell with redraw-verified, unicast Left/Right input.
  It refuses selections, drags, modifiers, read/select mode, scrollback, stale geometry, anything outside the detected composer, and that harness's own pickers, where arrows would move a list rather than a caret.
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
- The pane tools row carries `note`, `queue[:N]` (agent sessions only - focuses that session
  and opens the drawer's Queue tab on it, the count is its pending items;
  `features/prompt-queue.md`), `transcript` (transcript-capable sessions only - focuses that
  session and opens the drawer's Transcript tab), and the `⋯` session menu.
  The shared pane header supplies the same controls on desktop and in the mobile projection.
  Labelled chips either report session state or directly open a primary session-bound surface;
  `proc` did neither, and remains in the drawer.
- The Queue tab's `auto:` line is a status as much as a control: on/off and the bounds
  actually in force (sends left, minutes left, quiet hours, why it is off), disclosing the
  toggle and the separate "accept agent messages armed" switch. Both are checked by default
  for a live agent conversation, so the disclosure reads as an opt-*out*. Only the
  auto-delivery toggle is unavailable - with the reason shown - when the install's master
  switch is off; arming is authorization and stays editable regardless of who presses send
  (`features/auto-delivery.md`).
- That same `auto:` disclosure carries the two **install-wide** controls that must be one
  gesture away on any device — pause all auto-delivery and report an unsafe delivery —
  below a rule that marks them as not per-session. They are here, on the one queue surface
  that delivers, rather than in the fleet-queue overlay, because a brake reachable only by
  opening something is not reachable when it is wanted; `autodelivery.pause` reaches the same
  operation with nothing open (`features/auto-delivery.md`).
- The pane header is `[name] [cwd] [voice] [tools]` and **must stay one row**.
  It uses `grid-auto-flow:column`, so an item beyond the declared column count cannot auto-place into a second row.
  The pane-local voice group contains read-aloud only; workspace talk is in the app-level voice dock.
  Overflow is absorbed by `.pane-voice`, which scrolls horizontally with a trailing fade and never grows the bar.
  Phones drop the cwd column, and every device caps the name track so the group keeps room.
- The header's first field is the session's display name (`sessionNames.ts`), not its status.
  State is already carried by the tab, the sidebar row, and the terminal being read, while the name is the field those surfaces crop: a tab is only as wide as its strip allows.
  The name track is `fit-content()` rather than `auto`, because an `auto` track takes its max-content size before the flexible track expands - a sentence-length generated title would take the cwd's space and squeeze the voice chips to their floor.
  The rendered name ellipsizes; the whole of it leads the `title` tooltip, followed by the status line, any faults, and delivery readiness.
  Faults keep a visible marker beside the name (`.pane-fault`) because they have no other pane-level surface - an agent header draws no path chip, which is where a non-local boundary is otherwise reported - and because a stale observation is the one fault that looks like a healthy session.
  Routine state never re-enters the bar: that is what the tab and the row are for.
- **A fault is a condition, never the diagnostic text describing one**, and `sessionFaults` in `sessionStatus.ts` is the single predicate every surface asks.
  Exactly three qualify: a non-local `runtime_boundary`, `observation_stale_since`, and `parser_status === 'degraded'`.
  The paired strings - `observation_diagnostic` and `parser_diagnostic` - supply the wording for those, and are never triggers.
  The daemon writes `parser_diagnostic` on every observed session as routine detail (`tailing <id>.jsonl`, `schema v2: 801/801 recognized`), so reading its presence as a fault marked 17 of 17 live sessions when it shipped - an alarm that is always on reports nothing.
  A predicate for a visible marker therefore belongs in a unit-tested module and not inline in the surface: `sessionStatus.test.ts` pins that a healthy session carrying both strings has no faults.
  Faults deliberately do not touch the dot, the tab, or the status line, because a session can be perfectly `idle` while reporting on a conversation it no longer owns - which is precisely what a state axis cannot say.
- **A pane has two rows: header and terminal surface.** Nothing a feature toggles may add a third row.
  The pane's remaining height is the PTY's row count, so an in-flow strip that appears with a toggle resizes the terminal under a live agent and makes its TUI reflow and repaint.
  The read-aloud player strip floats from the zero-height `.voice-overlay-anchor` that shares the surface's track, so it costs no rows in the desktop grid or mobile flex column.
  The Talk toggle is app chrome directly before Run on mobile and desktop.
  It is a square, icon-only microphone button that is **lit only while capture is actually
  running**: green edge, green fill and a plain mic when on, and a recessed neutral box with a
  slashed mic when off.
  It used to wear the green chip in every state, which made the one question a microphone
  control has to answer - "is this listening to me right now?" - unanswerable from the button.
  The word "Talk" went with it: beside a microphone glyph it said nothing the glyph did not, and
  it was costing 16px of a phone toolbar that also has to fit nav, quota, the Project name, Run
  and the drawer toggle.
  A dashed edge is the third state, microphone input disabled in Settings, where the button opens
  Settings rather than listening - off and unconfigured both mean "not listening", but only one
  of them can start.
  `aria-pressed` and the `aria-label` carry the same three states for anyone not reading the glyph.
  Beside the microphone sits the **voice dock chip**, a second, separate button: the microphone starts and stops capture, the chip opens and closes the panel, and neither does the other's job.
  Collapsing the panel therefore never stops listening, which is what "close" used to mean when the surface existed only while capture ran.
  The chip carries a count when confirmation cards are open and a dot when a reply landed while the dock was collapsed.
  The dock itself is **one app-level surface** (`.voice-dock`) holding both the dictation draft and the assistant conversation, mounted once for the life of the app and never moved.
  It hangs from `.voice-dock-anchor`, a zero-height grid item in the main stage's own cell, so it floats over the top of the workspace and every pane's row count is identical at every dock size.
  It has three sizes: `full`, a one-row `peek` (the newest line plus every open confirmation card, with no composer), and `chip`, where `display:none` clears the workspace completely while the conversation inside keeps streaming and speaking.
  `chip` hides rather than unmounts, which is load-bearing: the per-device set of already-announced cards lives in the mounted component, so a remount speaks an open card's line a second time.
  The Talk history header is its disclosure control, and its expanded or collapsed state persists device-locally across remounts.
  The dock header contains the two size steps, the talk/chat tabs, phase, last latency, and the action row; response and transcript prose belongs in Talk history, while transient phase detail remains screen-reader text and a badge tooltip.
  The player strip and dock actions both open the shared voice-command catalog as a root viewport modal, so pane overflow cannot clip it and terminal geometry does not change.
  Voice Comms remains a dock toggle and spoken command, not a utility-drawer tab.
  Any new pane-local overlay belongs on the pane anchor, and any view placed there must remain out of flow.
  Anything sharing the surface's cell must pin **both** `grid-row` and `grid-column`: the
  pane declares no columns, so a second item with an auto column is auto-placed into an
  implicit column 2 and the pane splits in half. This contract is enforced by
  `test/renderer/pane-layout.spec.ts` (`npm run test:renderer`), which asserts the surface
  rect is identical with the overlay up and down. It exists because two regressions of
  exactly this shape shipped — a template one track short, then that missing column — and
  neither is reachable by `tsc` or the unit suite, while both resize the PTY under a live
  agent and leave its TUI reflowed after the overlay closes.
- Floating pane furniture is ordered by a fixed z-band inside the pane: terminal host, voice
  overlay (11), find bar (12), scroll-to-latest and peek buttons (14), file-drop overlay (18).
  A new overlay picks its place in that band rather than topping it, because every one of
  these has to stay reachable while the others are up. Overlay containers are click-through
  (`pointer-events:none`) with their cards opting back in, so the space between floating
  cards still belongs to the terminal.
- The app-level Conversation fallback uses z-index 24; workspace chrome uses lower values, while command-palette and modal layers use higher values.
  The normal named-target Talk panel follows focus inside the pane voice overlay without entering pane or drawer layout.
- Prose of unbounded length belongs on a floating surface of this kind, never in the header
  chip group: `.pane-voice` is a fixed-chip scroller in a bar that cannot wrap, so a readout
  placed there can only ever show a truncated tail.
- Every terminal has an in-flow **Action rail** at the bottom of its pane on desktop and mobile, below the terminal rather than over it.
  It carries a keyboard toggle plus terminal-key buttons (Esc, Enter, Tab, Shift+Tab, Ctrl-C, and the four arrows), Copy reply, Paste, the clipboard-history picker (`Clip`), and the session's skill picker (`Skills`).
  Shift+Tab sends back-tab (`ESC[Z`), which both agent TUIs read as the permission-mode cycle (`(shift+tab to cycle)`) and shells read as reverse focus/completion.
  Its built-in **Actions** item opens the Actions drawer as a transient Project-scoped override: the Project's last explicitly selected drawer tab is not written, completing an action or closing the drawer clears the override, and explicit drawer-tab navigation promotes that selected tab through the ordinary persistent path.
  Immediately after Up/Down, five editing helpers insert a blank-line-surrounded divider, start a blank-line-prefixed fenced code block, copy the composer, send Ctrl+U, and send Ctrl+Y in that order.
  The multiline helpers are agent-only raw key sequences: every logical newline is `ESC+CR`, matching the built-in newline command, so neither Claude nor Codex interprets one as submission.
  Attach is the final scrolling item on agent rails.
  A status readout and the customize gear ride the **last** rail row, so they stay put as rows are added and a rail configured down to nothing still has a way back into configuration.
  Only the gear is reserved out of a row's fit budget; the readout takes whatever is left and ellipsises, so a chip never shifts because a transient `Copied` appeared beside it.
- **A row that does not fit fills with what does and collapses the rest into a trailing `+N` chip.**
  The rail is a toolbar, and a toolbar's answer to overflow is an overflow menu rather than a scroller: a horizontal scroller hides an unknown number of controls behind a gesture, says nothing about how many, and puts the one you want at an offset you have to hunt for.
  Each row is split independently and gets its own popover, in row order, holding only that row's remainder.
  An empty remainder draws no `+N` chip at all, so a rail that fits looks exactly as it did before the split existed — including not reserving the chip's width "just in case".
  The `+N` chip is fixed-width by construction, because the one control that exists to absorb overflow must never be the thing that overflows, and the count follows the row's width live.
- **The `+N` chip rides the row's trailing cluster, beside the gear**, rather than trailing the last pinned chip.
  The split leaves up to a chip's width of slack, so following the chips put the control at a different offset on every rail and on every resize; the cluster takes that slack and pushes its contents right, which gives it one place on every row however that row is populated.
  Its panel is placed against the *cluster* for the same reason: aligning to the chip alone left the panel a gear-width short of the rail's edge on the one row that carries a gear and flush on every other row, which is two placements for one control.
  The status readout sits to the chip's left and is the only thing in the cluster that shrinks.
- **The popover is the rest of the row, not a picker, so a selection does not close it.**
  It is a wrap grid of the same real chips with their real handlers, which is what makes the two-click End session confirm complete in place and a repeat-tap arrow key repeat where it is.
  Dismissal is always deliberate: an outside press, Escape, or the panel's own close control, and the header says `stays open` because every other popover on the rail behaves the opposite way.
  It is capped in width and in height (half the viewport), right-aligned to its chip, and grows upward — so on a phone it hugs the rail's edge and adds rows rather than blanketing the composer.
  It also offers the in-place rail editor from its header, beside the close control: a full overflow is the surface that prompts "this rail needs configuring", and the gear behind the panel is a reach away.
  It is chrome rather than a chip in the grid, where it would read as one more thing to press into the terminal, and it closes the panel as it hands over — the editor replaces the whole rail area, so a panel left standing would float over a surface that no longer exists.
- **Every command-rail overlay is glass: the popover and each of the drop-ups.**
  Panel, chips, and rows are translucent over a backdrop blur, with borders and text at full opacity.
  One opacity for all of them, and a measured value rather than a taste one: the lowest at which the translucency costs no theme its label contrast when the terminal behind it is pure white or pure black, held under 95% so a later "fix" cannot buy contrast by quietly going opaque (`test/railGlassContrast.test.ts`, and the real composited pixels in `test/renderer/rail-overflow.spec.ts`).
  The number is set by the **single-layer** case: a drop-up row is transparent over its panel, so its label sits on one layer of glass, where a popover chip has its own background and sits on two.
  The binding surface owns the number and the other inherits it, rather than each carrying its own.
  A browser without `backdrop-filter` falls back to near-solid, because unblurred glass is a flat wash of whatever pixel happens to be behind each letter — neither the look nor a contrast anyone can reason about.
- **On a phone a rail overlay takes half the screen and goes to the screen's trailing edge**, whichever control opened it.
  On a wide pane hanging off the trigger is useful — it says which control this belongs to — but at half a phone's width a picker opened from a chip in the middle of the rail lands in the middle of the screen, so two pickers opened a second apart appear in two places.
  Below the device-class breakpoint they all go to one edge; above it they keep their trigger.
  Half the screen is the point rather than a limit: the terminal the panel is opened over has to stay readable beside it.
- **The soft keyboard is two separate corrections, and both live in `railOverlayPlacement.ts`.**
  Bounds are drawn against the **visual** viewport, not `window.innerHeight`: the app declares `interactive-widget=resizes-visual` so an open keyboard leaves the layout viewport at full height (deliberately — sizing the shell from `visualViewport` shrank every terminal, and shrinking an alternate-screen PTY discards rows permanently), which means half of `innerHeight` is half of a rectangle running well behind the keyboard.
  And the **containing block** is measured rather than assumed: `.terminal-surface` is translated up by the keyboard's inset so the rail stays visible, and a transformed ancestor is the containing block for its `position:fixed` descendants — which every rail overlay is — so the identical `bottom` means one place with the keyboard down and a place an inset higher with it up.
  That is the "the panel is thrown up the screen when I start typing" report exactly.
  Both are corrected rather than side-stepped by portalling the overlays to the body, which would take the rail's chip styling with them.
  Placement re-runs on `visualViewport` resize and scroll as well as on window resize and capture-phase scroll, because the keyboard's open and close fire nothing else.
- **The drop-up pickers work from inside the popover.**
  A Clip, Skills, Prompts, or Actions chip opened there renders its drop-up *over* the panel and leaves it standing, which needs two exemptions rather than one: the tap that opens a drop-up is an outside press for the panel, and Escape reaches both listeners on `window` where `stopPropagation` stops neither — so the panel stands aside for as long as a drop-up is open.
  The exception is a selection that *takes you somewhere else*: a drawer tab, a drawer section, or the prompt library folds the panel, because leaving it standing would cover the surface the tap just asked for.
- Configured chips — a skill, a slash command, a prompt template, a literal — **size to their own label** with symmetric padding, and their `min-width` is a floor for a short one rather than a width every one of them is stretched to.
  The rail's shared 74px minimum stays right for the built-in labelled buttons, whose wording is fixed and whose even widths are the row's rhythm; it was wrong for a label the user chose, and it padded a five-character skill out to the width of `Copy resume`.
  The gear flips the rail area into the in-place editor rather than opening the modal; the modal stays one click behind its "All options…" control.
  On narrow/coarse Claude and Codex panes, the configurable Enter item is removed from the scrolling strip and replaced by an always-visible **Send** end-cap in a separate grid column.
  The end-cap draws a right-arrow icon rather than the word: it is the one control on the rail with a fixed place, so it is recognised by shape, and the width the word cost goes back to the scrolling keys.
  It keeps its 44px tap height and its accessible name; only the width fell.
  The four arrows are non-focusing keys with two verbs: a clean tap sends once, and a press held in place starts repeating after 350 ms and then every 75 ms until release or cancellation.
  The tap is delivered by the button's own click, exactly as every other rail item is, which is what makes an arrow a legal place to begin a swipe: a touch the rail turns into a horizontal pan has its click suppressed, so a flick that merely started on an arrow scrolls the rail and sends nothing.
  This is the whole reason the arrows do not send on pointer-down; sending there is a decision the pan can no longer take back, and it made the arrows the one part of the rail a finger could not push off of.
  A press stops being a candidate for repetition once it has travelled as far as the pan needs to start scrolling, and a hold that *has* committed claims the pointer so the strip cannot scroll out from under the key being spammed.
  Focus is refused on mouse-down rather than pointer-down — the same guard the rest of the rail uses — because the click now carries the tap; this keeps an open mobile keyboard open, and keyboard and assistive activation remain one-shot.
  The inner strip alone owns horizontal overflow, so Send does not scroll, cannot be reordered/hidden, and remains reachable after soft-keyboard Enter becomes newline-only.
  Shell and desktop Enter behavior is unchanged.
- On touch, an overflowing Action rail owns horizontal pointer movement directly instead of depending on native overflow-scroll arbitration inside the keyboard-translated terminal surface.
  The first drag therefore moves the rail even while the soft keyboard is open.
  A modest drag gain compensates for the lost native fling without making nearby actions hard to target.
  The gesture preserves the active IME field, restores it if Android drops focus, and suppresses the resulting click.
  Every button on the rail is a legal place to begin that drag, the repeating arrow keys included; the suppressed click is what settles what a swipe did or did not activate.
- Activating an Action rail item preserves the mobile soft keyboard state it found.
  Keys, Send, Paste, prompt templates, skills, slash commands, and literal text execute with the keyboard down when it was down, while an already-open keyboard remains open.
  Synthetic terminal writes restore the dedicated IME bridge with `inputmode="none"` when needed, preserving physical-keyboard routing without turning DOM focus into typing intent.
  A terminal typing tap, returning explicitly to Live mode, opening Draft, and the manual Paste fallback remain the paths that intentionally request a soft keyboard.
  The fixed Send end-cap carries the same focus-preserving press guard as the scrolling rail.
- **Nothing raises the soft keyboard just because a surface opened.**
  The Queue tab's composer takes focus on open only where `hasSoftKeyboard()` is false - a physical keyboard is already there, so a caret costs nothing.
  Where the only keyboard is on-screen, focusing a field is a layout change rather than a convenience: it covers most of the drawer, so a tab opened to *read* a queue arrives with the list already hidden and a dismissal to perform.
  The condition is `hasSoftKeyboard()` rather than the mobile breakpoint on purpose - a narrowed desktop window has a real keyboard and a landscape tablet does not (`deviceSettings.ts`).
  The other half of the fix is at the caller: opening the Queue to *reveal* an already-written message no longer asks for focus at all (`prompt-queue.md`).
- Action configuration separates **what an action is** from **where it appears**, and the second half is per device.
  The shared *catalog* (`RailConfig.items`) holds identity and behaviour: label, what it injects, and the backends it means anything for.
  The *layouts* (`RailConfig.layouts`) hold position: one layout per device class, each with rows for the `strip` under the terminal and the `panel` rendered as Quick actions in the Actions drawer.
  The `strip` and `panel` property names remain internal storage terms for backward compatibility; user-facing controls call them Rail and Drawer.
  Desktop and mobile therefore have genuinely independent arrangements: their own rows, order, and membership, with no shared row and no "applies to both" switch.
  A shared row would be the trap the split exists to avoid: the two devices want different rails, so anything that live-links them is something you would immediately disable.
- Row membership subsumes three older mechanisms and replaces all of them.
  A per-item `platforms` tag, a per-item `strip`/`drawer`/`both` placement, and an `enabled` flag all said "not here" in different vocabularies; a command in no row on a device is now the single way to say it.
  Nothing else dims or hides.
  The one filter that stays on the item is `backends` (plus `agentOnly`), because that is a property of the command itself: `/rewind` means nothing outside Claude regardless of where it is placed.
- The strip renders **one horizontal scroller per configured row**, so a row that overflows pages independently of the others.
  Each row costs the terminal one row of height, which is why the practical ceiling is around three; the editor treats that as a soft guide, not a data constraint.
  Row count comes from configuration and is fixed for the render, never measured and then adjusted, which keeps it clear of the geometry-echo resize loop.
  The Drawer layout renders each row as an optionally captioned section, and within a section terminal keys still split into their own dense grid because a 44px key and a labelled action want different cell sizes.
- An item id may appear in several rows and more than once within a row, so a rendered entry carries a `key` of its own (`rowId:index:itemId`) rather than reusing the item id.
  Anything keyed by item id, including render keys, focus, and key-repeat, must key by the entry instead.
- Layouts always keep at least one row per surface, so the editor always has a drop target and a newly shipped built-in always has somewhere to land.
  Deleting the last row of a surface empties it instead of removing it.
  A built-in introduced after a config was saved is appended to the first row of its `defaultSurface` on both devices; cataloguing it without placing it would leave it permanently invisible to anyone with an existing layout.
- Saves predating the layout model are migrated on read, per scope and by shape rather than by a version field, so a rewritten global scope and a still-legacy project override coexist.
  The old list is resolved once for each device/surface combination and each result becomes a row, so an upgrade renders identically on both devices and only then diverges by hand.
  Legacy semantics are preserved through that resolution: `enabled: false` on an entry that predates `placement` meant "not on the strip", so it keeps rendering in the panel, while `enabled: false` alongside an explicit placement was a genuine hide and lands in no row.
  Saves predating the editing-helper cluster still receive the four helpers after Down and Attach at the end once.
  Project scopes are detected the same way: a legacy array or an `{items, layouts}` object is a fork honoured exactly as saved, while `mode: 'delta'` is the additive overlay — so a project forked under the old fork-on-first-edit editor keeps behaving as it did, and only deliberately created deltas track the live global layout.
  A delta item whose id collides with the base catalog is dropped (the base wins), which is what keeps a stale delta from shadowing a built-in.
- Action items come in six kinds: terminal `key`, built-in `action`, literal `text`, `slash` command, `skill`, and `prompt`.
  A `prompt` item is a *pointer* at a prompt-library template (`prompt-library.md`).
  It stores the `scope:id` key, never the body, so the button always injects the template's current text and cannot drift into a stale copy.
  It is the one item type whose activation is asynchronous because the body is fetched on click, and the one that can never submit, which is the library's own contract.
  Templates carrying `{{variables}}` have nothing to inject yet, so the button opens the Actions drawer with Prompt templates expanded and the template preselected rather than pasting a half-rendered body.
  Both hosts route through `promptRail.ts` and insert over the `mux:terminal-action` bus, so the pane stays the single owner of terminal writes.
- **A prompt button's *name* is a pointer too, unless somebody typed one.**
  Pinning takes no name, so the label it stores is the template's title at pin time - a snapshot of a name, which is exactly the kind of copy this item type exists to avoid.
  An item carrying `autoLabel` therefore renders the template's live title and renames itself when the template is renamed; the stored copy stays as the fallback for before the library has been read and for a template that has gone.
  A label the operator typed is never overridden, and neither is one saved before this rule existed - the flag's absence means "somebody's own name", so nothing that was deliberately named gets renamed under them.
  Clearing the label field in the catalog editor is how an existing button opts in; the field shows the live title as its placeholder.
  The titles come from one lazily filled cache shared by every surface (`promptTitles.ts`), and it is only read when the rows about to be drawn actually contain such a button, so a rail with no prompt buttons costs nothing.
- Talk exposes a deliberately smaller rail-derived command set through `railVoice.ts` when a live session is focused.
  Built-in keys and Paste require explicit `voicePhrases`; non-submitting configured agent skills and slash commands derive aliases from their names.
  Submitted custom commands require their own explicit voice phrases.
  Copy is the existing focused-terminal registry command because it is not a rail catalog item.
  Literal text and prompt macros are excluded, as are destructive and UI-only actions such as clear-input, Attach, keyboard mode, relaunch, and End session.
  Only items placed on the current device's Rail or Drawer layout participate, and duplicate placements collapse to one spoken command.
  The adapter emits the same `sendKey`, `insertText`, copy, or text-paste request the visible controls emit, while `terminalActions.ts` adds a request id and waits for the owning pane's success or error acknowledgement.
  Text-paste deliberately bypasses the visible Paste control's clipboard-image attachment branch.
- A project relates to the shared Action config in one of three strengths, and the middle one is the default a project accumulates (`railScope.ts`, `commandRail.ts`).
  Plain **inheritance** is no override at all.
  A **delta** overlays project-owned actions, project-owned rows, and the two shared-row overlays (splices and hides) *on the live global layout*: global edits keep flowing into the project, and only the overlay is project state.
  A **fork** is a detached full copy that stops tracking global edits; it is created only by the explicit Detach control, because the old fork-on-first-edit behavior deviated a project from every later global improvement the moment it added one button.
  A fork can be reattached as a delta from the same toolbar (below), which is the way back for one made before splices and hides existed.
  Edits route by ownership rather than by a write-target switch: an edit to a shared row lands in the global scope (all projects, said in place by the scope note and per-row origin tags), while project rows and project actions stay project state.
  Reverting is symmetric — "Remove project additions" drops a delta, "Use global layout" drops a fork — and unpinning the last project addition returns the project to plain inheritance with no stray delta behind it.
- **A shared row's *definition* never contains a project-owned action; only the resolved projection does.**
  That is the invariant, and it is what a delta's two shared-row overlays are built to preserve.
  A **splice** records "this project also places item X in shared row R, after item Y"; the row's definition stays global and keeps flowing through, and the splice is applied at resolve time, so a later global reorder, insert, or removal re-anchors it rather than breaking it.
  Anchoring is by item id and never by index, because an index would quietly mean somewhere else the moment global gained a button; `null` is the head of the row, and an anchor that is gone falls back to its end.
  A **hide** is the subtractive mirror - "this project does not render X in shared row R" - and it takes every occurrence, the way placement toggles already treat duplicates.
  Together they remove the two things that used to force a fork: a project action on the shared strip, and "everything shared except that one button".
  Editing the shared row's own items from a project view still round-trips to global exactly as it did before, so nothing about the existing behavior changed.
- One rule makes that split decidable and is enforced in `resolveDeltaScope` rather than trusted: **within one row, an id is either the project's or the definition's, never both**.
  A splice is applied only when its id cannot also arrive from the row's own definition - the action is project-owned, the definition does not carry it, or this project hides it from there (which is how a project-local *reorder* is said: hide plus splice).
  That is what lets an arbitrarily dragged-about row be split back apart afterwards from the *ids* alone, with no index tracked through the edit, and it is why the resolution reports per-row ownership rather than positions.
  Hidden occurrences are written back into the definition at the indices they held there: the project cannot see them, so it cannot have moved them, and dropping them would delete them for every project.
- Consequently the drag refuses no target any more.
  A project action dropped into a shared row becomes a splice; the inverse was always legal and still is, a global item in a project row being a project-local placement of a shared action.
  In a project scope a shared row's chips read apart: the project's own placements are marked and their × removes only this project's copy, a shared entry's × still removes it for every project (the tooltip says so), and its ⊘ hides it here alone.
  A hidden button is drawn back as a **ghost chip** in the slot the shared row still holds it in, because a hidden entry is in no row and nothing else could offer to restore it.
  Surface copy ("Copy from *other device*") stays hidden in delta scope — the fresh-id copy would flatten project rows into global state.
- **A fork can be reattached** (`railReattach.ts`), which is what rescues an arrangement that predates any of this rather than making somebody rebuild it by hand.
  The tool diffs the fork against the *current* global config and emits the delta that reproduces it: project actions, project rows, splices for what the fork added to a shared row, hides for what it took out, and hide-plus-splice pairs for a shared button it merely moved.
  It verifies itself - the candidate delta is resolved by the same function that will render it and compared row by row against the fork - so nothing is reported as reproduced on the strength of the diff that produced it.
  What a delta cannot express is named on the plan with the global value that wins: a renamed shared row, a reordered set of shared rows, a project row interleaved between them, and an action whose definition the fork edited.
  A row that survives none of that is redone with every id floated, which always reproduces it at the cost of no longer tracking global changes to that row.
  It is user-invoked and never automatic: a fork is somebody's arrangement, and a migration that happens to you is one you could not review first.
- **In-place rail editing** (`RailInlineEditor.tsx`) is the primary customization path: the rail gear flips the rail area into an editor showing the same rows as wrapping chips — real device, real backend, real scope — with drag to reorder, × to remove, and a per-row `+` opening a searchable picker over the catalog.
  Most rail edits are one reorder or one removal, and those should never cost a modal that also explains scopes and catalogs.
  Items another backend would hide render dimmed rather than hidden, because this is the one surface meant to answer "why is this button not on my shell rail".
  The picker excludes project-owned actions for shared rows (the ownership rule), and "New action…" plus "All options…" hand off to the full modal.
- The standalone **Configure Actions** modal (`ActionEditorModal.tsx` and `RailEditor.tsx`) opens from the main menu, command palette, the in-place editor's "All options…", and the Configure control in Quick actions rather than living inside Settings.
  It discloses progressively: one device's Rail and Drawer layouts first (defaulting to the device this browser is, with a Desktop/Mobile switch at every width), custom-action creation collapsed below them, and the complete catalog collapsed at the bottom behind a filterable "All actions" disclosure.
  The former permanent two-column device view is gone: it doubled the visual load for the rare cross-device drag that the catalog's placement checkboxes already cover.
  A dismissible first-open callout carries the three-line orientation (Rail vs Drawer, per-device layouts, the catalog) instead of a standing paragraph.
  A "Preview as" backend selector dims what a session of that type would not show, making the backend filter visible before a session surprises anyone.
  It **opens on the Project the operator was standing in**, not on Global.
  Global is a superset in rows but a subset in reach - a Project's own actions and its own prompt templates are listable from no other scope - so defaulting to Global made the editor look emptier than the rail it was opened beside.
  At Global scope it names which Projects hold something of their own, and whether each is a fork or additions, because project-held items are otherwise invisible from the default view and "all actions" reads as though the fleet had none of them.
- Four affordances are what keep two independent device layouts manageable, and none of them is a shared row.
  Adding a custom action places it into **both** device layouts, because a button you must remember to add twice is a button that never reaches the phone; in a project scope the add form offers "this project only" (the default there) or "all projects".
  The catalog's placement controls are four **labelled checkboxes** — Desktop rail, Desktop drawer, Mobile rail, Mobile drawer — inside an expandable per-action panel, with a plain-words summary ("Desktop rail + drawer · Mobile drawer") on the collapsed row; they replaced the abbreviated `Dr/Dp/Mr/Mp` badge code, which was a legend the user had to learn before the surface said anything.
  A per-surface "Copy from *other device*" seeds one layout from the other as a one-shot; it deliberately does not keep tracking.
  Dragging a catalog row into a layout places it exactly.
- Chips drag within a row, between rows, and between surfaces, on mouse and on touch, through the shared controller (`railDrag.ts`) both editors mount.
  Activation reuses the workspace contract (`dragReorder.ts`, `pointerDragClaim.ts`): a 5px movement threshold for pointers and a 325 ms hold with 8px slop for touch, so a finger that moves first scrolls the modal instead of dragging.
  The live preview is the config a drop would commit, recomputed from the committed config on every move rather than from the previous preview, so a long drag cannot accumulate drift.
  Pointer capture is taken on the editor root, not on the chip: the preview reparents the chip between rows, and a captured element that leaves the document loses the pointer mid-drag.
- The drop index is measured against the row **without** the dragged chip.
  That exclusion is what makes it a fixed point: re-measuring after the preview moves the chip gives the same answer, so a chip hovering over its own new home does not oscillate.
  The hit test is two-dimensional because the editor wraps a row's chips over several visual lines; a horizontal-only comparison would put every drop on the second line into the middle of the first.
- Keyboard placement is the equivalent path and the only one available without a pointer: arrows move a focused chip along its row and between rows, Delete unplaces it, and focus follows the chip so a run of presses keeps moving the same one.
- The expanded catalog panel also holds the per-action backend checkboxes under their harness display names ("Shown in these sessions"), custom-action editing (label, payload, submit-on-insert), and delete.
  The catalog head stays a name-first grid: the action name owns the elastic column and wraps rather than truncating, with the type/payload preview beneath it and the placement summary on the right; phones drop the summary under the name.
- The **Actions** tab is session-scoped and contains three independently collapsible sections that start expanded: **Quick actions**, **Skills**, and **Prompt templates**.
  Disclosure state is device-local and persists independently from the Action layout.
  Quick actions renders the `panel` surface of *this device's* layout, so its grouping and order are independent of the other device class.
  It is a configured overflow and favorites surface, not another catalog editor.
  Skills and prompt templates may intentionally appear both in Quick actions and in their complete sections because one is the chosen shortcut layout and the others are browsable inventories.
  The Configure control in the Quick actions header opens the standalone Configure Actions modal.
  A transient visit opened from the Action rail closes after an action completes on desktop as well as mobile; a prompt with unresolved fields remains open until those fields are completed, and an ordinary visit retains the existing repeated-action behavior.
  The Manage control in the Prompt templates header opens the full prompt-template editor.
  Prompt rows include a bounded body excerpt so similar titles can be distinguished in the drawer.
  Skill and template rows both carry a **Pin** toggle: one tap creates a placed Quick-actions button on both devices (`pinSkill` / `pinPrompt` in `railScope.ts`) instead of routing the most common creation — "give this thing a button" — through the editor's typed-name form.
  A pin follows its source's scope (a project skill or project template pins to the project's delta; anything else pins globally; a forked project pins into its fork), restricts a pinned skill to the harness it was discovered for (the same name is not guaranteed to exist for any other CLI), and stores a template pin as the usual key pointer.
  Pinned state is matched by payload rather than by id, so a hand-authored button counts, a built-in slash command reads as already pinned rather than growing a twin, and unpinning removes the catalog item wherever it lives.
  Actions renders outside the terminal pane, so it activates items over the same `mux:terminal-action` bus (`sendKey`, `insertText`, `copyReply`, `copyResume`, `branch`, `relaunch`, `endSession`).
  The pane stays the single owner of terminal writes, so broadcast, replay, and read/select mode keep applying.
  With no terminal focused, Quick actions and Skills explain what target is missing while Prompt templates remain browsable.
  Keys inject raw bytes on the normal input path.
  The built-in newline uses `ESC+CR`, the legacy sequence both Claude and Codex bind to composer newline; raw LF works in Claude but not Codex.
  The Action rail never wraps: a row that does not fit collapses its remainder into a trailing `+N` chip and a popover, above.
  It keeps the shared overflow scroller underneath that split rather than dropping it, because that component owns the touch-drag pan's click suppression, the soft-keyboard hold, wheel translation, and focus reveal — behaviours the `+N` chip needs as much as any other chip, and a pixel of measurement error still wants somewhere to go.
  With the split in place the strip does not actually scroll, so the endpoint chevrons never appear; they remain correct on the drawer, workspace, and Notes rails, which do scroll.
  Voice controls are not here.
  They are in the pane header (`voice.md`) because the rail is chrome the user reads across and they kept being the thing pushed out of it.
- The Skills section lists **the skills that the focused agent session's CLI can actually see** (`GET /sessions/{id}/skills`, `interfaces.md`).
  These come from the vendors' own `SKILL.md` directories, not swe-mux prompt templates or Action items.
  They are *discovered*, never configured here.
  The list is a window onto the CLI's state, so it groups by where each skill comes from (project / global / plugins / bundled) rather than by anything the user arranged, and a Rescan button refetches instead of a save button writing.
  Clicking inserts the invocation without submitting over the same bus because a skill invoked bare runs with no context, and the point of typing it into a live composer is to say what it should act on.
- Three things about that list are load-bearing, because a skill list that looks complete and is not is worse than none.
  **Claude's built-in skills are compiled into the CLI binary** and cannot be enumerated from disk (`/skills` is a TUI-only command), so the tab discloses that in place rather than implying `/review` and `/security-review` do not exist.
  **A skill newer than the running agent process is flagged `new`**: it is on disk, the CLI read its skills at startup, and typing the invocation will not work until the agent is relaunched.
  **Codex's explicit-only skills** (`policy.allow_implicit_invocation: false` in `agents/openai.yaml`) are flagged `explicit`: real and invocable by name, but the model never reaches for one itself.
  Disabled plugins, unreadable entries, and truncation are named in the same footer note; nothing is dropped silently.
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
  button relabels to Remove, matching the command's own fallback, and skips the confirm entirely:
  confirmation guards against destroying work, and an ended session has none left to destroy — no
  process to interrupt and no turn to lose, only a record the operator has finished reading. The
  drawer deliberately stays open on the arming click — closing it would leave nowhere to make the
  second one.
- **An ended or recovered session's pane is openable and read-only.** Its row and tab keep the
  same 0.62 dimming they always had; a *recovered* one adds a dotted underline, because the
  difference between "you watched this exit" and "this came back from disk after a crash" decides
  whether the way back is Resume, Restart, or nothing. A banner above the terminal says which it
  is, how old the replayed content is, and — when there is none — why not, since an empty
  recovered agent pane otherwise reads as a bug rather than the deliberate exclusion it is
  (`features/session-recovery.md`).
- **`Clip`, `Skills` and `Prompts` open a drop-up over the rail**, not the drawer.
  All three surfaces already exist as sections of the Actions tab, and all three are reached from the drop-up's sticky first row, so nothing became less reachable - what changed is that the two-tap jobs (paste the thing I copied a minute ago; type a skill name; insert a template) no longer cost a drawer trip.
  `Prompts` is the *whole* library rather than the handful somebody remembered to pin: per-template pinning still exists and still earns a template its own dedicated button, but a library reachable only through pinning leaves everything unpinned three taps away.
  It is deliberately not `agentOnly` - a template is text, and text suits a shell composer as readily as an agent's - and templates that mean something to one harness carry their own `backends` and are filtered by them.
  Its rows are ordered favourites, then most recently used, then title, which is the library's own notion of relevant rather than a second one, and a template with `{{fields}}` says so on its row before the tap hands off to the drawer.
  It carries a second sticky exit, `+ New`, which opens the prompt library already on a blank template, because "I want a template for this" is where a picker of existing ones most often ends.
  Two exits share the sticky bar side by side rather than each taking one of the five list rows.
  The drop-up shows five rows and then scrolls; the cap is a height, never a slice, because capping by count would make the sticky link the only route to a sixth entry.
  It opens upward from its trigger through `anchoredPopoverStyle`, the same placement math the account and resource popovers use, and repositions on scroll with capture because the rail is itself a horizontal scroller.
  A row does the one obvious thing and closes: Clipboard inserts the entry, Skills inserts the invocation without submitting, Prompts inserts the template body (also without submitting) or hands a template with fields to the drawer.
  Reading, searching, pinning and deleting stay in the drawer section - a drop-up that also expanded rows would rebuild the surface it is a shortcut past.
  Inserting from the ring never touches `navigator.clipboard`, which is what makes it the working paste path on a plain-HTTP tailnet client and on mobile Safari.
- **Copy input reads the draft off the terminal grid.**
  Nothing else can answer the question: no harness publishes its composer, and the daemon's write log deliberately keeps a character count rather than text (`features/terminal-input.md`).
  It is disabled with its reason on a harness whose composer geometry has not been measured, rather than hidden - a missing button reads as "not built", a disabled one reads as "not here yet", and only the second is true.
- **There is deliberately no Clear-composer button.**
  The rail carries a raw `^U` key beside `^Y`, and `^U` is all it claims to be: it kills to the start of the line, which clears a single-line draft and leaves the other lines of a multi-line one standing.
  A Clear button existed briefly and sent the harness's declared whole-composer discard sequence (`composer_clear_keys`, `features/backends.md`), which on Claude is a double Esc - and a double Esc interrupts a running turn.
  A button labelled as tidying a draft that can abort work is the wrong shape of mistake to leave one tap from the arrow keys, and making it turn-state-aware was declined in favour of removing it: an honest key beats a clever button.
  The declared sequence itself stays published, because the daemon's unsent-input accounting still needs to know which write discards a draft.
  A saved rail layout holding the retired button migrates to the `^U` key in the same slot rather than losing the position (`technical/frontend/packages.md`).
- Copy reply, Branch, and Paste render as icons alone; every other action keeps its text. The
  rail is width-starved — those three cost 74 px each on desktop and 96 px on a phone, which is
  most of a screen's worth of rail before the terminal keys begin — and their marks (offset
  sheets, git branch, clipboard) are conventional enough to read without a word. **Copy resume
  deliberately keeps its label**: a copy glyph cannot distinguish it from Copy reply, and the
  two sit side by side. Icon buttons size like keys (30/44 px) and carry an explicit
  `aria-label`, since the title attribute is not a name on touch.
- **Branch opens a point picker where the daemon honours one** (`BranchPicker.tsx`, gated on the
  published `branch_from_message`), and forks on the click where it does not — offering a choice
  the daemon would then refuse is worse than offering none. The picker is a dialog of its own
  rather than controls added to the Transcript tab: that tab writes nothing back, because it is
  where somebody reviews what an agent already did and a stray tap there must not start a session
  or put words into one. The rule is about *what a tap can reach*, not about a count of buttons:
  copy, select, and read-aloud playback all leave the conversation, the PTY, and the session
  exactly as they were, which is why per-message playback could join them (`voice.md`) while
  branching could not. The one thing playback does spend - a summary's model call - is stated on
  the chip before it is pressed, and its verbatim twin spends nothing at all. Rows run newest first, since a branch is normally a recent regret; each
  states the cut it would make in the words of the act (a prompt is branched *before*, a reply
  *after*); and a row whose cut is illegal stays visible with its reason inline, because the reader
  can see the message and hiding why it is not offered leaves them guessing.
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
- The touch-only keyboard control cycles agent terminals through three exclusive modes: live input (`⌨`), read/select (`↕`), and Draft (`✎`).
  Shell terminals retain the original live/read two-state cycle because they have no agent composer.
  Read/select keeps terminal-body taps keyboard-down persistently; ordinary Live mode now also leaves a dismissed keyboard down across command-rail actions.
  Draft opens a native multiline composer as a floating surface in the terminal cell, so its appearance does not resize the terminal or reflow the running TUI.
  There is no separate Draft rail action; the single keyboard control owns the whole mode cycle.
- Draft text is device-local and keyed by session rather than pane, so hiding the composer, changing workspace tabs, remounting the pane, reloading, or browser suspension does not discard it.
  The registry is written immediately to `localStorage`, limits each draft to 64 KiB, retains at most 50 sessions for 30 days, and falls back to memory if browser storage is unavailable.
  A green dot on the keyboard control and every tab for that terminal discloses saved text without exposing its content.
- Enter inserts a newline in Draft, while Ctrl+Enter or its dedicated **Insert** button appends the exact draft text to the live agent composer without submitting it.
  Agent multiline insertion uses bracketed paste, including the stale-mode fallback, so newlines and leading or trailing spaces remain composer text rather than becoming Enter key submissions.
  The Draft path never emits a trailing carriage return.
  A successful insertion clears the saved draft and returns to live input for review; a rejected insertion leaves the text editable and reports the error in the composer.
  Insertion appends to any text already present in the live terminal composer, because terminal applications do not expose that existing buffer for safe import into Draft.
  Hiding Draft always preserves it; discarding text requires the explicit **Clear** action.
- Paste uses the browser clipboard when permitted and otherwise opens a focused native-paste
  target.
  Native terminal paste and the rail action use the same pane-owned text path, including the agent multiline stale-mode repair, so Ctrl+V cannot submit clipboard lines individually while the rail keeps them composed.
  Claude and Codex
  rails prefetch normalized transcript text so Copy reply runs inside the button gesture rather
  than typing `/copy` or waiting for OSC 52. Reply extraction walks back to the newest turn with
  meaningful assistant text; provider control acknowledgements such as `No response requested.`
  never replace the last copyable reply.
- Claude/Codex terminal bodies also accept OS file drops and copied-file paste, while the paperclip rail button supplies the same multi-file picker on desktop and mobile.
  Attach is a built-in Action item, so it can be moved, filtered, placed in Quick actions, or hidden.
  Upload status is reported in the rail.
  A general file inserts a quoted workspace-local path into the draft; a recognized image keeps the provider's native image reference.
  Neither path submits.
  Attachment input never follows terminal broadcast to sibling panes.
- Terminal copy is success-preserving: keyboard, menu, automatic selection, the action rail, and
  provider OSC 52 requests retain the exact text until a write succeeds. Blocked or insecure
  clipboard contexts open a prepared fallback automatically, leaving one explicit Copy tap.
- Every successful terminal copy uses the same `Copied to clipboard` HUD on desktop and mobile instead of hiding acknowledgment in the Action rail.
  The HUD is a polite live region anchored to the bottom-right safe area, carries no copied content, stays above modal layers, and never appears for a rejected clipboard write.
  `InteractionHud.tsx` owns its state and dismissal timer below the composition root, so copy and cut feedback cannot re-render terminals, agent chats, or Continuity editors and disturb an active selection or edit transaction.
## Utility drawer

- The right-edge **utility drawer** is where the app's lookup and injection surfaces live, so they are one gesture on mobile or one visible click on desktop away instead of two menu levels deep.
  The canonical default order is **Actions**, **Queue**, **Transcript**, **Activity**, **Agent**, **Files**, **Notes**, **Git**, **Processes**, **Schedule**, and **Alerts**.
  Users may distribute those singleton tabs across a recursive arrangement, but the default order groups by what a tab acts on.
  Actions leads the session-scoped block because it inserts into the focused work surface, while Queue stages text for a focused agent and Transcript reads the same session back.
  Activity and Agent close the session-scoped block with passive views of what the selected session did and what it is running with.
  Files and Notes are the **navigators**: Project-scoped indexes over documents rather than surfaces that type into one.
  Files opens what you select into a pane.
  Notes can do that too but opens *into the drawer* by default, because reading or adding to a note without leaving the session on screen is the whole point of it on a phone.
  Git closes the Project-scoped block without joining the navigators: it reads the repository behind the Project and opens nothing into a pane.
  See `git.md` for the branches, worktrees, dirty/upstream state, and allowed mutations it shows.
  **Processes** continues that block for the same reason: it is Project-scoped and acts on sessions rather than opening panes.
  **Schedule** closes it, immediately after Processes, because the two answer the same question at different times: Processes is what this Project's sessions are running now, Schedule is what it will start later (`scheduled-runs.md`).
  **Processes is the one tab hidden by default** (`DEFAULT_HIDDEN_DRAWER_TABS`).
  It is not made redundant by the Resources dialog that also draws its surface - a modal covers the terminal, and this tab exists to answer "what is *this* session running" beside it, with the focused session pinned first - but that is asked rarely enough not to spend a permanent rail slot on for someone who has not asked for it.
  The default applies only to a browser with no stored visibility choice at all; an explicitly emptied set is a choice and stays empty.
  **Alerts is deliberately not hidden**: it is the only tab that draws an unread badge, so hiding it would remove the one glanceable "something needs you" signal from the rail.

### Segments and sections

- Three former tabs are now **segments** or a **section** of the tab beside them, on one rule: a low-frequency *inspection* surface can afford one more click, and an *injection* surface cannot.
  Clipboard is a **section of Actions** - same verb, same insert contract, and a section is co-visible, so the surface people reach for fastest cost no extra click.
  Change Map is **Activity's third segment**, because "what the session narrated" and "which files it actually wrote" were always two readings of one run; its pop-out into a workspace tab survived the merge and matters more now, since a force-directed graph wants more width than this column has.
  Agent Context is **Agent's Instructions segment**, because tools, policies, and instruction files are the halves of "what is this agent running with".
- The two kinds are deliberately distinct rather than collapsed into one (`frontend/src/drawerSegments.ts`).
  A **segment** is a mutually exclusive view of a tab, drawn by the drawer's single shared segmented control under the pane heading; the heading names the segment, not the tab, because "Change Map" is what that pane *is* while "Activity" is only where it lives.
  A **section** is a co-visible region of one scroller, reached by scrolling to it and flashing it through the same `settingReveal.ts` arrival the Settings deep links use (`setting-links.md`).
- Segments are **registered, not local state**, and that is the point rather than tidiness.
  Every registered tab generates two palette commands and three voice phrases; a segment reached only by clicking would have none, so folding Clipboard into Actions and Change Map into Activity would have *deleted* "open Clipboard" and "open Change Map" as commands and as spoken navigation.
  The registry generates a command and a voice phrase per segment and per section, and every retired command id migrates forward in `keybindings.py` so an existing binding keeps working.
- **Retiring a segment is the same obligation in reverse, and is a row rather than a deletion.**
  A retired id stays in `RETIRED_DRAWER_SEGMENTS` naming the live segment that absorbed it, so its palette entry and its phrases keep answering and simply land somewhere else; a *stored* selection migrates on read through `migratedDrawerSegment` rather than falling through to "the tab's first segment", which is right only by coincidence and stops being right when the order changes; and the command id migrates in `keybindings.py`, where an unmigrated id is rejected outright rather than ignored.
  Git's **Land** is the first: landing folded into the worktree map, so "open Land" now opens Map (`land-queue.md`).
- Segment availability is a predicate, and a stored choice that cannot render falls back to the first that can rather than drawing an empty body.
  Activity's Timeline needs a harness transcript; Findings and Changes do not.
  Agent's Config and Tools read a live harness inventory and are unavailable on a shell; **Instructions is not**, which is what the separate Project-scoped Context tab used to buy and why neither tab is gated as a whole.
  Unavailable segments are omitted from the control rather than disabled, because a greyed-out "Timeline" promises a surface that does not exist.
- The selection is persisted **per Project beside the tab selection** (`selected_segments` in `mux.drawer.projects.v3`), keyed by tab rather than by stack so dragging a tab into another pane does not lose which view it was showing.
- Only Change Map is kept mounted while another segment is selected.
  Everything else unmounts, because a hidden body that polls or refetches costs network for a surface nobody is looking at; the map is the exception because its layout worker's settled positions are the expensive part and remounting re-runs the simulation on every return.
- **Git** is registered the same way, so the drawer has one mechanism for this idea rather than two: Map, Log, and Provenance are segments, drawn by the shared control above the tab's toolbar rather than by a toggle of its own, and each has its own palette entry and voice phrase.
  Landing is deliberately **not** a fourth one. It was, and the split it rested on - Map answers what is *in* a worktree, Land what is *happening to* it - did not hold: the act belongs on the row showing the diff behind it, and once it moved there the segment held a single Project-wide block. It is now a compact strip at the head of Map, one summary line with the rest behind a disclosure, so the tab still opens on a map (`land-queue.md`).
  It is a tab rather than a modal because the decisions it offers - pause this, run it now, is last night's session still open - are judgements about live sessions, which are legible in the workspace behind the drawer.
  Like Processes it carries its own Project/all-Projects scope instead of a companion modal, since "what fires tonight" spans Projects even though every schedule belongs to exactly one.
  Notifications is neither and sits last.
  Session history, usage, and automation stay modal, as do the process *inspector* and the *fleet queue*.
  They are wide, table-shaped surfaces that a ~380 px column serves badly, and none decides anything that has to be read off a terminal.
- **A tab is drawn unless it is structurally absent or the user put it away, and those two are kept apart.**
  Structural absence means the tab has nothing to act on and nothing the user does inside swe-mux changes that; Transcript on a shell session is the entire list.
  It is never persisted and no control offers to restore it.
  Hiding is the user's own choice, made from the tab's context menu.
  Git is deliberately not gated on whether the Project's folder is a repository, because that is the one case where the tab has a decision to offer (`git.md`).
- **Hidden is one global set, device-local, exactly like the arrangement it filters.**
  Visibility *is* arrangement: which tabs exist is not a property that may vary by Project while their position, stack membership, and split ratio do not.
  Per-Project visibility would also be the only structural property that changes as you switch Projects, which is what would let a split pane hold content on one Project and nothing on the next.
  Device-local means a phone can carry a tighter rail than the desktop, which is where the cost of fourteen tabs is actually paid.
  It is read synchronously at startup so no tab is drawn and then taken away again.
- **Hiding is a render filter and never a layout mutation.**
  Layout normalization keeps every registered tab in the tree exactly once and re-inserts a missing one at its *canonical* position, so removing a hidden tab from the layout would silently discard wherever the user had dragged it the moment they showed it again.
- **The way back is where the way out was.**
  Right-clicking any tab — or long-pressing one on mobile, which already opens this menu — offers `Hide <tab>` flat, and a `Panels · N of 14` group holding the full checklist and `Show all`.
  The count is on the group header, so a rail missing something says so without being opened.
  The same checklist is mirrored in Settings → Appearance → Visible panels, which is the reachable path on mobile and the searchable one everywhere.
  Settings alone would have been the wrong home: the rail is where a missing tab is noticed, and a settings page is not where anyone looks for chrome they removed by right-clicking.
- **Hiding the last remaining tab is refused rather than allowed and recovered from**, because the restore control lives on the tab strip.
  The bound counts the hidden set alone and ignores structural availability, so the answer does not change with the focused session and Settings can render the identical checklist without one.
- **Explicit navigation is never filtered.**
  A palette entry, a voice command, or a menu row that names a surface has already said "show me this", so a hidden tab reached by name is *peeked* — shown for as long as it stays selected — rather than quietly unhidden.
  Hiding is about the resting rail, not about what you can ask for.
  A peek never overrides structural absence.
- A pane whose every tab is filtered out is dropped and its space handed back to its sibling, rather than drawn as an empty box under whichever heading the selection fell back to.
  The split survives in the layout either way.
  A drawer with nothing left at all — reachable only by the structural filter taking the remainder — carries its own `Choose panels…` control, because a drawer with no tab strip has no context menu to right-click.
- Actions replaces the former separate Commands and Prompts tabs.
  Saved drawer layouts, orders, and selected tabs map either legacy id to one `actions` singleton on read, with the first encountered position winning so migration cannot duplicate a tab.
  Existing `RailConfig` data is not rewritten; its `panel` layouts become the Quick actions section unchanged.
- The injection surfaces share verbs but not routing.
  Clipboard inserts land in the last-focused surface, editor or terminal.
  Prompt-template inserts inside **Actions** are terminals-only, and their rows additionally answer right-click / long-press with a target menu for a live agent session in the current Project or a new Claude/Codex session.
  See `prompt-library.md`.
  Text meant for an agent must not be able to edit whichever note or file the user happened to open last.
- **Queue** does not inject; it *stages* text held for the focused agent until delivery is explicitly requested (`features/prompt-queue.md`).
  It is here rather than in a workspace tab or a modal because the decision it exists for, "is now a safe moment to interrupt this agent", is read off the terminal, and only the docked column leaves the terminal on screen.
  Queue remains strictly session-scoped.
  Its header carries a `fleet` control, labelled with the fleet-wide pending count, that opens the fleet queue.
- The **fleet queue** is an application-scoped provenance and delivery-state view over queued messages from every Project and session, and is a **modal**, not a tab.
  It filters by explicit authorship, Project, and target session, and opens a target's Queue without pretending the global list is session-scoped.
  It is modal for the reason Queue is not: the argument for docking Queue is that the decision to interrupt is read off the terminal, and the fleet queue makes no such decision — it has no send button, so it needs nothing on screen beside it.
  It also stops the rail from carrying two queue-shaped tabs that read as duplicates.
- **Transcript** is an *inert* session surface: the focused session's conversation
  as prose you can scroll and copy, without touching the live terminal or scrolling it back.
  A capability-gated `transcript` chip beside `queue[:N]` in each terminal header focuses that
  session and opens this tab on desktop and mobile.
  Deliberately no composer, no insert, no send. Mixing those actions into the surface for reviewing
  what already happened is how a stray tap
  becomes a message nobody wrote. Copy is the only verb: per message, or the whole conversation
  with speakers.
  Its head row carries the message count and not the session name: the pane heading directly
  above already says which session this is, and printing it twice in twenty pixels of chrome
  made the count - the one thing this row knows that the heading does not - read as a
  subtitle of the name rather than as the state of the transcript.
  The top-bar search filters the already loaded messages with literal, case-insensitive matching, highlights every occurrence, and leaves whole-conversation copy unchanged.
  Search owns a temporary scroll position and clearing it restores the reader's prior place.
  A message's copy control sticks to the body's top-right edge while that message is being read, then yields when the message leaves the viewport.
  **Copying part of a message goes through a flat-text sheet, because dragging a selection across the column cannot be made to work on a phone.**
  The messages live in a nested `overflow-y:auto` scroller, and once the anchor handle scrolls out of view Chrome re-derives the selection's base from a screen coordinate that is no longer over the anchor, so extending the other handle swallows every message above it.
  That is the browser's touch-selection controller, not this app's DOM: making the column's chrome unselectable only moved where the runaway base landed, from "and the head bar too" to "from the first message down".
  So **Select** - per message in its sticky control, and for the whole conversation in the head - opens the text in a read-only `<textarea>` filling the viewport, with **Copy selection** beside it.
  A text control is a different selection path entirely: offsets into one buffer, with the control's own drag autoscroll and no scroller, sticky control, seam, or message boundary for a handle to catch on.
  Nothing selected copies all of it, since a reader who opened the sheet to copy is not served by a button that copies an empty string.
  Selecting in the column directly is still supported and still the faster path within one screenful; it is the scroll that breaks it, not the gesture.
  To that end none of the column's chrome can hold a selection endpoint - not the head bar, the sticky controls, the message headers, the seams, or Show more - which keeps speaker labels, timestamps, and seam counts off the clipboard so a dragged selection yields the prose and nothing else.
  On touch the sticky controls also hide for as long as a selection is held, since they are the last things in the path a selection handle travels that still accept a tap.
  Holding a selection over the column suspends the follow-scroll below for its duration; the arrival becomes the same "N new" pill a reader who scrolled up gets, and releasing the selection re-reads whether the column is still at its bottom rather than trusting the answer from before the freeze.
  Every message header shows its full local date and time, not only a time-of-day.
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
  A per-server **Fetch tools** button on an MCP row is the single exception, and it is a read rather than a mutation: it may start a short-lived probe and label what that probe proves, while tab-open stays passive.
  Source drift is measured against the current CLI process generation, not the latest conversation rollover.
- **What the reader shows is a filtered conversation, not the transcript.** Tool calls are not
  rendered by default.
  A `Tool calls` toggle replaces each count-only seam with individually collapsed native call names and input arguments, and it also exposes calls after the newest prose message.
  Expanding a call reveals only its input; tool results, telemetry, and private reasoning never enter this projection.
  The CLI machinery that both providers write into
  the transcript as `user` records: slash-command expansions and their output, `!` shell escapes,
  skill bodies injected mid-conversation, interrupt markers, Claude's `<system-reminder>` spans
  (stripped from the prompt that carries them rather than hiding it), and Codex's
  `<environment_context>`. A provider control operation's synthetic `No response requested.`
  acknowledgement goes with them. The opening `# AGENTS.md instructions` block **stays**: it is the
  brief the run was given, and reading a Codex conversation without it starts in the middle.
  Classification is the daemon's (`transcript_view.conversation_view`), so history search can
  inherit the same distinction later, and it reads Claude's per-record provenance
  (`origin.kind === "human"`, `isMeta`, `interruptedMessageId`) rather than matching wrapper tags,
  which is both more accurate and version-durable. **The rules fail open**: a record is hidden
  only on positive evidence that it is machinery, because leaking a `<local-command-stdout>` is a
  blemish while hiding something the user typed is the surface lying about the conversation. The
  count of what was withheld is shown, so the filtering is never invisible.
- **A turn is split into segments at its tool calls, and the seam is drawn.** A provider splits a
  reply across records for two unrelated reasons and they need opposite treatment. Streaming
  splits one continuous message on no boundary at all, and those fragments are stitched back
  together. A tool call splits a turn on a real boundary: the narration that introduces a tool
  ("I'll investigate the sidebar sort.") is a different thing from the conclusion that follows it,
  and gluing the two together is what used to make the copy button hand back the narration on top
  of the answer. So the fragments merge only where no tool call sits between them, and each
  message carries `preceding_tool_calls`. Where it is non-zero the column draws a seam naming the
  count, on the same principle as `hidden`: a reader seeing two agent messages in a row must not
  have to guess whether the gap between them is nothing or twenty minutes of tool work.
  With the toggle enabled, the seam becomes one collapsed disclosure per useful tool call.
  The seam and disclosures are not drawn under a search, where the neighbours are whatever matched rather than what followed.
- **A branch the conversation left is folded, not deleted.** A retry, a `/rewind`, or a resend
  after a failed request appends a new branch and leaves the previous attempt in the transcript
  forever (`transcript-branches.md`). Those turns were never sent to the model, so they are not
  read as conversation - but this is the one surface that still shows them, because a reader who
  watched themselves resend a prompt eight times through an outage cannot tell a transcript with
  the eight removed from one the reader is mangling. Each contiguous run collapses to one muted
  row naming its size, opening on click, dimmed and rule-marked so no scroll position makes a
  member look like part of the conversation. The fold is not remembered between mounts: an
  abandoned branch is something to check once, not a preference. Under a search it is dropped and
  the members stand on their own, on the same rule as the tool seam - the reader asked for every
  match, and a fold nothing reveals them through would hide one. Copy-all, Select, and the header
  count take the live messages only, with the abandoned total named beside them.
- **The rail's Copy reply is the last agent message in this tab**, by construction rather than by
  agreement: `/sessions/{id}/last-reply` reads this same reduction (`final_reply_text`), as does
  read-aloud. The reader is where a doubt about what was copied or spoken gets settled.
- Reading placement follows one rule: open at the newest message, and let only a reader *already*
  at the bottom be carried along by new ones. Scrolled up, the position holds and the arrival
  becomes a "N new" button. A live log that yanks the column mid-sentence every time an agent
  speaks cannot be read at all, which is the failure this tab exists to fix. Returning to a
  session still focused restores where reading stopped; moving to another session starts at its
  newest message, and nothing is remembered per session beyond the one you are on.
  A held selection counts as "not at the bottom" for as long as it is held, on the same
  principle: an agent finishing a turn is precisely when someone is selecting what it just said,
  and moving the column then destroys the selection outright.
- It refreshes when the transcript observer consumes a user message (`transcript_message`) and at
  the assistant turn boundary (`turn_ended`), never on a timer. The first event makes a submitted
  prompt appear without waiting for the response; the second collects the completed answer.
  Polling would re-read a whole transcript to learn nothing for most of an agent's working minute.
  A pane whose conversation rolled over
  (`/clear`, `/new`) reloads onto the new run; the retired conversation stays in History, which
  is also where anything older than the loaded window lives.
  History transcript messages use the same agent/you labels, full local timestamp, sticky per-message copy control, long-message Show more/Show less treatment, and default-off collapsed tool-call disclosures.
  A search-matched history message is temporarily unfolded so its result cannot remain behind the clamp.
- A live auto-named agent's session menu includes **Regenerate title**. It requests a fresh
  generated title from the latest observed user request. A manual Rename remains authoritative and
  removes this action because automation never overwrites a user title.
- **One session has one name on every surface.** The rule - a generated title wins only while the
  session is still `auto_named` - lives in one place per side (`frontend/src/sessionNames.ts`,
  `src/swe_mux/session_titles.py`) and every surface reads it: sidebar rows, workspace tabs, the
  drawer's session-scoped headings, prompt and queue targets, the Git tab, the Processes tab and
  the process fleet inspector, the mobile draft composer, voice, and History. A
  surface that spells the rule out itself is the one that eventually disagrees with the sidebar,
  which is what a heading still reading `claude-0e7d93` beside a titled pane looked like.
  The two payload shapes disagree about types and are read through separate entry points: a live
  session carries `auto_named` as a boolean, a run row carries SQLite's `0`/`1`, and an absent
  field means auto-named in both.
- A session showing a standing-activity badge (`⟳`, `≡`, `⑂`) offers **Clear standing activity**
  in its menu and the command palette. Those badges assert work the daemon cannot observe
  directly - live subagents, background shells, an armed wakeup - so any of them can outlive the
  thing it names, and the only other exit is a 30-minute decay. The action retracts and nothing
  more: the state dot, delivery, and awaiting are untouched, and a task that really is running
  re-announces itself on its next piece of evidence. Offered only where there is something to
  clear, because an always-present control for a rare fault reads as a routine one.
  Each badge's tooltip names what it believes is running (`≡` carries the launching command),
  which is what makes the claim checkable before the user decides to retract it.
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
  Two *views* of one note - here and on a phone - are safe for a different reason, and it is not
  the revision check: each side rebases onto the revision it was just handed, so a save loop
  between them is made of individually legitimate writes. What stops it is that a commit is only
  a save when the content changed and a human touched this editor (`project-resources.md`,
  `noteEditGuard.ts`); when neither is true and the writes keep coming, the note reports
  `Autosave paused` in its header with a banner offering **Resume autosave** or
  **Reload from disk**, and typing anywhere in it resumes immediately.
  Selecting a note for the open drawer makes its pane leaf render an "open in the panel" placeholder.
  Placing that note in a pane closes the drawer before the pane editor takes ownership, while retaining the remembered Notes sub-tab.
- **The selection and temporary ownership are device-local and never touch the layout.** `project.layout` is persisted
  server-side and shared, so removing the leaf would let a phone rearrange the desktop's panes.
  The leaf keeps its slot and only stands down while the drawer owns the note.
  The selected sub-tab is stored per Project under `mux.drawer.note.v1`.
  It survives drawer close, utility-tab and session changes, Project switches, and reloads.
  Closing the drawer ends editor ownership but does not erase the selection, so a placeholder never points at a hidden panel and reopening resumes the same tab.
  Moving the selected note to a pane also closes the drawer and retains the selection.
  Spoken `open Notes` opens rather than toggles the drawer and makes this selected note the insertion target without moving DOM focus or raising a soft keyboard.
  A later terminal, note, file, Scratchpad, or Queue-composer focus report replaces that one-shot routing claim normally.
- **The Notes body is the one drawer body kept mounted across tab switches**, hidden rather than
  unmounted. Both reasons are load-bearing: an editor unmounted on every switch loses cursor and
  undo history, and `insertTarget` refuses a detached editor handle, so switching to Clipboard to
  paste *into the note* would route the paste to a terminal instead — silently, into an agent's
  prompt. Keeping the body
  mounted is what makes hosting an editor safe. No other tab needs it, so no other tab gets it.
  Hidden is not the same as started hidden: the editor element itself is created only once its
  slot has a layout box, because Continuity's first render throws under `display:none`
  (`project-resources.md`). A drawer opened on any other tab used to start exactly such an editor,
  which is what put a raw `Cannot read properties of null (reading 'offsetLeft')` toast on screen.
  Moving a note between hosts is still an unmount and a remount, which is lossless because the
  save queue outlives both: the arriving editor adopts any text the daemon has not acknowledged
  (`pendingText`) instead of the copy it was just served.
- On mobile, an insert normally closes the drawer, since it covers the terminal the text was for.
  When the text landed in the note the panel is hosting, it stays open and returns to the note
  instead — closing would hide the result that was just asked for. Desktop does not move at all,
  because the column sits beside the workspace and a second insert is the common next action.
- **Agent → Instructions** is titled **Instructions & Memory** and is the Agent Context
  surface (`agent-context.md`). It shows descriptor-declared Project-root
  instruction sources in an initially expanded disclosure, descriptor-declared global
  instruction sources in an initially collapsed disclosure, and one
  initially collapsed **Memories** disclosure badged with the provider file count. All three
  share the same high-contrast file-row surface; bodies are read-only.
- **Nothing is opened for you, and the viewer exists whether or not anything is.** The
  segment used to select whichever readable file sorted first — in practice the focused
  harness's own `CLAUDE.md` or `AGENTS.md` — which read as a decision the tab had made on
  your behalf, and left the body ambiguous at a glance: a file pinned directly under the
  memory list, with a background change and no rule or label between them, is more list.
  The viewer is a permanently drawn, labelled region now, divided from the disclosures above
  it by a rule heavier than the ones between them, and it says `No file selected` when that
  is the truth. Selecting is a choice you make and can undo — the header carries a close
  control, so empty is a place you can go back to rather than only the state you arrived in.
  What you pick is remembered **per Project**, device-local, because it is a reading position
  rather than a setting; a stored id whose file has since gone resolves to the empty state
  rather than to something else's body.
  The tab's own `Agent Context` title is gone for the reason the Actions session line is: the
  pane heading above already carries it. The line under it stays, because which harness and
  which working directory the inventory resolved against is a fact the heading has not got.
  Fine-pointer desktop rows backed by real files expose **Open in default explorer** on
  right-click, using the Files browser's native reveal behavior; mobile keeps its native
  context-menu behavior.
  One `sync…` button opens a modal containing both deliberate Project-root whole-file copy
  directions, normalized diff confirmation, and revision-guarded restore points. Global
  instructions and learned memory are never write targets; nothing watches or synchronizes in
  the background.
- **Processes** is the process inspector, docked. It renders the same component as the
  Resources dialog's Processes segment (`ProcessFleetView`), so it has the same process trees, the same parent
  lineage, evidence state and confidence behind each row's expander, the same listener and
  Preview rows, the same ended toggle, and the same guarded
  interrupt/terminate/terminate-tree. `Open full width` in the footer reopens that view as the
  dialog, at whatever the tab is scoped to.
  The tab is hidden by default and the dialog is one app-menu row, which is the shape the
  watch-here/act-there split has everywhere: keep the rarely-asked question one command away,
  and keep the surface that must sit beside a terminal available to anyone who shows it.
- It shipped first as a *watch* surface that could terminate nothing, on the argument that a
  column narrow enough to sit beside a terminal is too narrow to hold a destructive
  confirm-on-second-click. That was an argument about **layout**, and layout answers it:
  `.process-fleet-view` is a CSS container, so the column gets the same rendering the modal
  already used on a phone — one wrapped line per process, arguments dropped before numbers,
  details stacked — and the confirm is the same two-press confirm. What the split actually cost
  was that the surface docked beside a terminal could not say what was running under it: the tab
  answered "is something up", every follow-up needed the modal, and the two surfaces drifted.
- Rows are per process, not per session — the tree is the point. A session's tree does carry
  bookkeeping (`cmd`, `conhost`, the agent CLI), which is why a collapsed row is one line and the
  six lines of evidence live behind its expander rather than being dropped.
  A listener is not asserted to be an application server; `preview` explicitly lists one beside
  its session, and `copy` takes the URL. Listener rows are deduped by port, so a server bound to
  both loopback stacks is one row.
  Independently, the daemon lists browser-facing HTML endpoints automatically while leaving debugger and tool listeners raw.
  Ended processes are hidden unless the `ended` toggle asks for them: they support no action and
  are already excluded from every total in the app.
- Scoped to the active Project by default, with **the focused session pinned first inside its
  Project and marked `focused`**. That combination is deliberate. Session-scoped would read empty
  most of the time and would churn its whole body on every focus change, the same objection that
  sank a focus-following Notes tab; Project-scoped answers the question people actually have, and
  the pin answers "what is *this* session running" without a scope change. Clicking a session
  heading narrows the tab to that session alone, and clicking it again widens back.
  `All projects` is one click away and the choice survives a tab switch. A Project scope also
  drops the daemon/infrastructure group, on both surfaces: the runtime belongs to no Project.
- **A closed tab polls nothing, and open tabs share one poll.** Trees and evidence are absent
  from the reduced sample the sidebar's resource summary polls, so this tab subscribes to the
  full snapshot the way the modal does — through a refcounted shared feed, so the tab open in
  both drawer stacks with the modal over it is still one request per tick. The reconcile walk
  behind that data holds the GIL on Windows (`processes-and-previews.md` § Sampling cost); what
  that rule protects is the *always-mounted* poll, and that one still reads the reduced
  projection and is unchanged. Any future addition here inherits both halves.
- The pane header lost its `proc` chip when this shipped. It was the only pane tool carrying no
  state of its own — `note` reports empty/written/open, `queue` its pending count — so it was pure
  navigation, and on a phone it cost 40 px of a bar that also has to fit the session name and
  path. The session context menu and the palette still open the inspector directly.
- **Schedule** is where a Project's scheduled agent runs are written, watched, paused, and run
  on demand (`scheduled-runs.md`). Rows collapse to label, cadence, and countdown; the prompt
  and the run history open on demand, because a 380 px column cannot show five schedules with
  their bodies expanded. Three rules the surface keeps:
  **it never computes a fire time** (cron plus a timezone plus daylight saving has one
  implementation, in the daemon, and the editor previews through it, so what is promised before
  saving is what will happen);
  **it never renders a schedule that cannot fire as if it can** (a Project that has not opted
  into `scheduled_runs`, an install-wide switch that is off, or a resume whose conversation has
  been deleted from History, is drawn on the row itself with the way to fix it, because an
  armed-looking row that is silently inert is the failure this surface exists to prevent);
  and **a deliberate pause is not an alarm** - a paused schedule is dimmed but never counted as
  needing attention.
  A schedule that *reopens* a conversation rather than starting a new session is never authored
  from a blank form here. It arrives seeded from the conversation itself - the History row's
  "Resume later…" or a pane's own menu - because the one thing this tab cannot offer is a way
  to find a conversation, and a form with an empty run-id box would be a worse conversation
  picker than the two that already exist. What the tab does own for a seeded resume is
  everything else: the trigger, whether the target may move, the ceiling on a rolling
  continuation, the fixed point a fork is cut at, and what to say on arrival.
  The editor replaces the list rather than opening beside it, for the same width reason.
- **Alerts** leads with ranked attention (`attention-ranking.md`): the fan-out headline, the
  daily interrupt budget, incidents grouped by channel, any behaviour-mined rule awaiting an
  explicit decision, and the count of what was held back and why. Ranking is the reading; the
  raw record list under it is how the decision is checked. It is one tab rather than two
  because a second app-wide icon would compete with the one that already exists for exactly
  this subject, and the phone rail has no room for it.
  Ranked items surface here and nowhere else — no sound, no push.
  Below the ranked view, Alerts shows open attention records first and dismissed ones only on request. Each row
  dismisses (or restores) and the footer clears the lot; both write `read_at` server-side, so
  the state follows the user to every device and to the dashboard inbox rather than being a
  per-browser hide. The tab lists what the daemon retains for 90 days, which made it
  append-only from the one surface a human actually reads: a single detector firing on a
  normal workflow buried every record that mattered. Dismissing deletes nothing — see
  `automation.md`.
  Below the records sits the collapsed **away report** ("what happened while I was away?"),
  which summarizes attention items and run notes since the last terminal attach or input.
  It came here from the Automation dashboard, where it sat in a view named for something else
  and nobody found it: it is a *reading of this inbox*, not a fact about the pipeline that
  fills the inbox. It is fetched only on request, because generating it is a server-side scan.
- **Alerts is the only home for attention items**, and Activity → Findings is the only home for
  run notes. The automation pipeline produces exactly those two things, and the Automation
  dashboard used to draw a second copy of each: the same ranked inbox under its `attend` group
  and the same `/api/annotations` table, differently filtered, under `review`. One record with
  two homes and two filters is a record you can see in one place and miss in the other. Both
  dashboard views are links now (`automation.md`), and Findings grew a source filter
  (deterministic / observer / all) to cover what the dashboard's copy showed.
- **A Group is drawn as a container, not as a run with a gap above it.** Groups and
  ungrouped Projects are sibling sections in one flat list, and for a long time the only
  thing between them was an 8px margin — so a Group holding two Projects with ungrouped
  Projects beneath it read as one list of four. The gap is real; a gap between two runs of
  identical rows is simply not a statement about which run is inside anything.
  Three cues carry it now, and the load-bearing one is the **indent**: a grouped Project row
  starts further right than an ungrouped one, which is the fact being communicated, and it is
  the only cue that does not wash out at sidebar contrast. A left rail and a ground a shade
  off the sidebar's make the indent read as containment rather than as ragged alignment, and
  the heading cancels the indent so it sits as the box's lid rather than as its first
  indented child. A folded Group drops the indent and the rule under its heading, because it
  has no body left to contain. None of it costs vertical space.
- **The Actions tab's four sections are separated by their headers, not by a line between
  them.** A 1px rule between sections is the same rule every row *inside* a section already
  carries, so the boundary read as one more row. Each header is a full-bleed bar with its own
  darker ground and a leading edge, and a rule above it twice the weight of any row rule; the
  bodies sit on the panel ground beneath, so a header reads as a lid over its section. The
  edge is deliberately muted rather than accent: every green edge in this app means *this one
  is active* — the selected Project, the active pane tab, the live segment — and four
  permanent green bars on four permanent headers would spend that meaning on decoration.
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
  Mobile tab-rail drags use a 2.5x horizontal gain so one committed swipe can traverse the complete rail.
  Desktop width is pointer-resizable and device-local, like the sidebar's.
  The docked drawer sits one tonal step away from the Project workspace and casts a restrained shadow across its neutral resize gutter.
  Its launcher rail takes a second tonal step, while internal drawer panes reuse the workspace's neutral gutter and focus-frame language.
  It has no fixed maximum; its live maximum is the available viewport width after reserving the navigation chrome, utility rail, and a 150 px main workspace.
  Dragging its divider below 260 px previews collapse, and reversing the same drag past 280 px reopens it before release.
- `sidebar.open` and `sidebar.close` are semantic navigation commands across both renderings.
  They open or close the mobile overlay and expand or collapse the desktop navigation column.
  Their voice aliases include navigation-sidebar and left-sidebar forms.
- **The launcher rail is what the closed drawer looks like**, exactly as the collapsed sidebar rail
  is what the closed sidebar looks like. It is desktop-only, holds the workspace's last column while
  the drawer is closed, and is replaced by the drawer itself on open.
  Keeping it beside an open drawer duplicated that drawer's own tab strip: with the default
  single-stack layout the two lists are the same twelve icons, and the rail spent a column
  restating what the strip already said.
  The rail's width stays reserved in both states and is handed to the drawer on open, so the
  Project workspace is exactly as wide either way and opening the drawer sends no larger reflow to
  the PTYs than it did when the rail stayed put.
  What the rail uniquely provides — discoverability without a menu or a chord, and the Alerts unread
  badge — only matters while the drawer is closed, which is precisely when it is drawn.
  The cost is a split drawer, where the rail was the one place all twelve tabs appeared together and
  each pane strip shows only its own subset. The per-tab palette commands, their voice phrases, and
  pane tab cycling all still reach any tab, and a rail that appears and disappears with split
  geometry would be harder to predict than one that simply means "collapsed".
- Losing the rail on open also loses the pointer affordance for closing again, since clicking an
  already-selected tab collapses the drawer but does not advertise that it will.
  Exactly one pane heading therefore carries a **collapse control**: the pane holding the drawer's
  top-right corner, resolved by taking the right branch of horizontal splits and the top branch of
  vertical ones. One per drawer rather than one per pane, because the drawer collapses as a whole
  and a heading is the only chrome available to hang it on.
  Escape inside the drawer, the outer resizer's collapse threshold, and the `drawer.toggle` command
  remain the other ways out.
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
  The desktop outer launcher is a depth-first mirror and activation control only, so it never becomes a second layout editor or content host, and it is not on screen at all while the drawer is open.
- Dragging uses the shared pointer contract with a 5 px threshold, pointer ownership after activation, one fixed ghost, direct DOM indicators, a prospective tree in refs, and one commit on pointer-up.
  Escape, invalid targets, pointer cancellation, lost capture, window blur, Project changes, breakpoint changes, drawer closure, and unmount cancel without persistence.
  Mobile exposes no drag targets or split separators.
- Each internal split separator supports pointer resizing, arrow keys, Home, End, and double-click reset to an equal ratio.
  The outer divider supports pointer resizing, arrow keys, Home, End, and double-click reset to the default drawer width.
  `drawer.moveLeft`, `drawer.moveRight`, `drawer.moveUp`, and `drawer.moveDown` provide keyboard geometry changes, while `drawer.next` and `drawer.previous` cycle within the focused utility pane.
  `drawer.resetLayout` restores one canonical stack and reconciles every Project presentation.
  Existing bindings for `drawer.resetTabs` migrate through a hidden alias.
- Settings > Appearance exposes independent **Icons** or **Titles** preferences for drawer tabs (`drawer_tab_display`) and the desktop right rail (`utility_rail_display`), both defaulting to Icons.
  Right-clicking a tab changes only the surface that owns that tab, so an internal tab-mode change cannot resize the outer workspace grid.
  Mobile long-press opens the drawer-tab menu after 550 ms with 8 px movement tolerance; the following synthetic click is consumed.
  The root-rendered menu stays above the mobile drawer overlay so every action remains tappable.
  Icon mode uses `DRAWER_TAB_ICONS`, while title mode uses the short `DrawerTab.label`; neither mode renders both marks.
  A tab's mark names the *thing the tab is about* rather than the surface it renders in, which is what the five redrawn in 2026-08 corrected: Actions is the command-key glyph (not a terminal window — every tab targets a terminal), Agent is a robot head (not an abstract "core"), Notes is a page with a pencil (a note is written, which a plain page left out), Queue is a list of rules with a clock (a queued message is one not delivered *yet*), and Activity is a pulse trace (not a lightbulb — a finding is an idea, the tab is the record).
  The one collision that survives that rule is Activity and Processes, which are both traces and sit on the same rail, so they are held apart by **silhouette** rather than by detail: a bare full-bleed squiggle against a monitor on a stand.
  A detail that distinguishes two marks at 34px and not at 17px distinguishes nothing, because 17px is the size these are read at.
  Title rails remain one-line scrollers with the same endpoint-aware overflow controls, and only right-rail title mode widens the outer launcher through `--utility-rail-width`.
  Queue and Alerts badges, scope dots, accessible names, tooltips, selection, focus, and drag state remain intact in both modes.
- Mobile renders one flattened depth-first rail and one body from the same desktop tree without rewriting tree membership, stack IDs, directions, ratios, or ordering.
  Selecting a mobile tab updates only its owning stack's Project selection and the Project's focused tab.
  Returning to desktop restores the exact recursive tree.
  The drawer has no redundant global title or close header.
  Clicking or tapping the selected tab collapses the drawer; clicking the active desktop right-rail launcher retains the same toggle behavior.
  Every tab context menu also exposes **Collapse utility drawer**, disabled while the drawer is already collapsed.
- The focused utility tab is remembered per Project and device, so
  `drawer.toggle` (default two-finger swipe **left**, the swipe that drags a right-edge panel in;
  the rightward swipe keeps the left-edge sidebar) reopens where you left off, while `drawer.<tab>`
  commands open one tab directly and close it if it is already showing.
- `drawer.open` and `drawer.close` are idempotent command-registry entries with side-panel, right-sidebar, and utility-sidebar voice aliases.
  They coexist with the pointer/gesture-oriented `drawer.toggle` and the per-tab `drawer.show:<tab>` voice commands.
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
- The same boot hooks emit a payload-free `mux:clipboard-copied` event only after a programmatic write resolves or a native copy completes.
  `App.tsx` turns that event into the shared interaction HUD, so explicit copy controls, selection auto-copy, plain DOM copy, and legacy fallback copy have one confirmation path.
- On mobile the sidebar and the utility drawer are mutually exclusive: opening either closes the
  other. They are both full-height drawers over the workspace entering from opposite edges, so two
  open at once leave no workspace between them and bury one under the other's scrim. The rule is
  enforced in the state setters themselves (`App.tsx` wraps both `useState` setters), not at each
  call site, so every entry point — gesture, command, nav toggle, tutorial — inherits it. Desktop
  is unaffected: there the sidebar is an in-flow column the drawer's own column never covers.
- The soft keyboard overlays the mobile layout, and never resizes it. The viewport meta carries
  `interactive-widget=resizes-visual`, so the layout viewport — and with it `innerHeight`,
  `100dvh`, `--app-height`, and every terminal grid — keeps its full height while the keyboard is
  up. Only the visual viewport shrinks, and the difference is published as `--keyboard-inset`
  plus a `soft-keyboard-open` class on the root element.
- This replaced `interactive-widget=resizes-content`, under which the keyboard shrank the layout
  viewport and refitted every terminal, resizing the real PTY. Shrinking an **alternate-screen**
  PTY is lossy in a way no repaint can undo: the alternate screen has no scrollback, so the rows
  that no longer fit are discarded, and growing back appends blank ones. Measured on a Claude
  session at 412x915: 45 rows with 19 painted and nothing blank at the bottom, down to 19 rows
  while the keyboard was up, back to 45 rows with only 14 painted and a 26-row blank tail that
  never recovered. The daemon's arbitrated geometry was correct at every step
  (`[61,45]`/`[61,19]`/`[61,45]`) — the conversation had simply been destroyed by the shrink.
  Under the current model the same sequence leaves the grid at 48 rows, the PTY at `[61,48]`, and
  the painted rows unchanged.
- `softKeyboardInset` is thresholded at `SOFT_KEYBOARD_MIN_INSET_PX`, because a shrinking visual
  viewport is not always a keyboard: collapsing browser chrome moves it ~50-60 px and pinch-zoom
  and rounding move it by a few, and sliding the workspace for those would read as the UI
  twitching. A visual viewport *larger* than the layout one (pinch-zoom out) clamps to zero
  rather than sliding the workspace off the bottom of the screen.
- The composer stays reachable by sliding, not by resizing: `.terminal-surface` is translated up
  by `--keyboard-inset` so its bottom edge, the agent composer and the Action rail, sits
  exactly on top of the keyboard, while its height, and therefore the terminal grid inside it, is
  untouched. What is lost is the top of the terminal, which comes back when the keyboard closes.
  The translate is scoped to `.soft-keyboard-open` rather than applied as an always-present zero,
  because a transform makes an element the containing block for its `position:fixed` descendants
  even at zero, which would re-anchor the sidebar and drawer overlays.
- The surface and nothing above it. Translating the whole `.workspace` carried the tab rail and
  the pane header off the top of the screen and drew the terminal where they had been; those are
  navigation rather than content, and they stay put. `.terminal-pane` clips while the keyboard is
  up, which covers everything outside the pane box (the tab rail, the mobile toolbar).
- A terminal slid for the keyboard offers a peek toggle, because the slide shows the bottom of a
  grid taller than the space left and that is the wrong half on a fresh agent session: the
  composer is pinned to the bottom of the alternate screen while the conversation fills from the
  top, so a first message and its reply land in the hidden region. Nothing could reach them — the
  alternate screen has no scrollback, so a scroll gesture has nothing to move, and the rows are
  clipped by a transform rather than scrolled away.
- The toggle is offered to a reader, not to every raised keyboard (`peekToggleVisible`). At the
  composer of a conversation with any length it is clutter over content, so it appears only while
  the viewport is off the tail on either axis — xterm scrollback, or the app-held estimate that
  counts forwarded drags. The fresh-session case above still summons it: the estimate grows on a
  forwarded swipe even when the application moved nothing, so the first swipe up toward a trapped
  reply is itself the signal. An active peek always keeps its toggle, because it is the way back
  down. It sits at the bottom-right beside jump-to-latest with a fixed one-button offset, so
  neither control moves under a thumb when the other appears.
- The toggle pushes the terminal host back down *inside* the already-slid surface, so the command
  rail and the chips stay where the slide put them. That is what makes it a toggle rather than a
  trapdoor: the control that reveals the top is still on screen and still takes you back.
  Measured with a 415px keyboard over a 48-row grid: the first visible row moves 27 → 0 → 27
  across toggle presses while the rail holds at 447..500 and the chip at 400..438.
- `nextPeekState` owns when peeking ends. Typing returns to the composer (the caret is there and a
  reader who types has stopped reading) and losing the keyboard ends it outright (the whole grid
  fits again). Output deliberately does **not** end it: a streaming reply is exactly when a reader
  is peeking, so snapping back on writes would make the toggle useless when it is most needed.
- The one output that *does* move the pane is `hiddenOutput`, and it is narrow on purpose
  (`hiddenOutputDeservesPeek`). It exists for a reader parked at the composer **with nothing to
  look at** — a first message and its reply both landing in the half the keyboard hides — so it
  fires only while the visible half is blank, on top of the existing "not already peeking" and
  "not mid-sentence" guards. That condition used to be stated only as intent, and an agent CLI
  repaints its whole screen constantly, so "the hidden rows changed" was true on nearly every
  frame and the pane jumped to the top by itself for the whole life of every session — most
  visibly just after sending a message, which is when the reader had stopped typing long enough
  for the input grace to lapse.
- A keyboard reservation (`keyboardReserve.ts`) is held only while a keyboard is up or one is on
  its way, where "on its way" is a typing gesture within `RESERVE_INTENT_WINDOW_MS` — the
  reservation has to be in place before the keyboard finishes animating in, or the grid it was
  meant to pre-size is already full and the shrink is refused. The predicate originally had no
  keyboard term at all: its only release was the session's own content filling the smaller grid,
  which an agent TUI with whitespace in its layout never reaches, so a pane that reserved once
  held ~40% of the screen back permanently and read as a session rendering on half the screen
  with the keyboard collapsed. The keyboard check is asked *before* `measurable`, because holding
  still on an untrustworthy grid is the right answer to "may I shrink this" and the wrong one to
  "may I stop shrinking it" — growing back cannot destroy rows.
- Every other mobile surface shortens rather than sliding, and the asymmetry is the whole point:
  a terminal must not resize because shrinking an alternate-screen PTY destroys rows, while a note
  editor, file view, or drawer reflows losslessly. Shortening is strictly better where it is safe,
  because the entire surface stays reachable instead of having its top pushed off screen. The
  mobile resource pane loses `--keyboard-inset` from its height; the drawer and mobile sidebar
  overlays take it as a `bottom` offset.
- This is what the note editor's command rail needs. Continuity pins that rail to the bottom of
  the element the host gives it, so a box still running to the bottom of the layout viewport puts
  the rail behind the keyboard however the editor is scrolled — the host owns the box, so the host
  owns the fix, and no editor change can reach it. Measured on a Project note with a 415px
  keyboard: the rail moves from `858..914` to `443..499` as the drawer's bottom lifts from 915 to
  500.
- Clipping is not enough for the pane's *own* header and read-aloud strip: they are inside the
  clip and the surface is a later sibling, so a translated surface paints over them while they
  remain laid out and hit-testable underneath. That is the worst version of hidden —
  `getBoundingClientRect` reports them exactly where they belong while `elementFromPoint` returns
  `xterm-screen` — so a rectangle-based check cannot see the bug and only paint order fixes it.
  Both take a stacking layer of their own while the keyboard is open.
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
  text entry, so the early blur is safe. Continuity 0.2.36 separately owns single-finger note-touch
  arbitration and the editor's Android soft-keyboard gate. swe-mux adds no shadow-DOM or caret
  hit-testing workaround; single-finger touches pass to the editor unchanged.
- Continuity tracks explicit typing intent and visual-viewport keyboard occlusion per editor.
  A long-press selection or selection-handle adjustment leaves an already-visible keyboard up and
  leaves a dismissed keyboard down. When a selection exists with the keyboard down, the first tap
  inside the selected range raises the keyboard without collapsing the range or removing its
  action bar; a later tap may collapse or reposition it normally. This policy must stay inside
  Continuity because Android can hide the IME without blurring its focused shadow-root textarea.
  The original gate rationale and device verification sequence remain in
  `development/CONTINUITY_TOUCH_KEYBOARD_ASK.md`. The same platform behavior on the terminal side
  is covered by that pane's own gesture rules.
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
  visible behind it. Desktop therefore gets no scrim (Escape *while focus is inside it*, ×, or
  the toggle closes it; the docked drawer is not a dismiss level, so back does not reach it) while
  mobile dims, and the drawer and its scrim are in the gesture recognizer's allowlist so the same
  swipe that pulled it in pushes it back out. Clipboard rows carry previews only; full text is
  fetched per entry on use. Row actions are insert (primary), copy to the system clipboard, pin,
  forget. Copying from the history tab, including its manual fallback, bypasses capture: it changes
  the OS clipboard without promoting the entry or changing its timestamp.
- Tapping a clipboard row *reads* it rather than acting on it: the row expands to the full text,
  selectable so part of an entry can be copied by hand, and the four actions move to a per-row bar.
  A two-line preview cannot separate two similar copies, and while the row body was the insert
  button the cost of finding that out was inserting the wrong one into a live agent. One entry is
  open at a time. Its head pins to the top of the list and a Collapse footer to the bottom, so a
  body many screens tall keeps *that* entry's copy/pin/forget and a way out on screen for the whole
  scroll. The expanded body is `data-clipboard-capture="ignore"`: a part-selection copied back out
  of the history surface is transport, like the Copy button, not a new capture, and recording it
  would reorder the list under the reader. Fetched text is cached per entry (an entry's text never
  changes, a re-copy promotes the existing row), which also lets Copy on an open entry run inside
  the click gesture, where the legacy `execCommand` fallback still works.
- The clipboard filter is autofocused on desktop and never on a soft-keyboard device
  (`hasSoftKeyboard()`, a *separate* question from the `MOBILE_QUERY` layout breakpoint: a narrowed
  desktop window has a real keyboard and a landscape tablet does not). Opening the tab to read what
  was copied should not raise a keyboard over it; there the keyboard arrives by tapping the field.
  Same rule as everywhere else in `ui.md`: nothing shows the soft keyboard the user did not ask for.
- Row actions are an icon column beside the preview on desktop and a full-width labelled row under
  it on mobile, where a four-icon column would take a third of the drawer from the text it exists
  to show.
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
- CPU/RSS and the Resources dialog's Processes segment: `processes-and-previews.md`
- Quota/context/tool evidence: `operational-telemetry.md`
- Automation navigation and diagnostics: `automation.md`
- Project task discovery and trust: `project-actions.md`
- Global Talk, registry-backed navigation, fleet speech, and guarded approvals: `voice.md`

## Diagnostics

Terminal render faults are a production phenomenon — they happen on the frozen desktop app,
which is a production build — so the instrumentation is not gated on `DEV` alone. Enable it on
any client with `localStorage.setItem('mux:terminal-render-diagnostics', '1')` and reload, then
read the last 100 entries from `window.__muxTerminalRenderDiagnostics` (each is `{at, sessionId,
phase, detail}`); `mux:terminal-render-diagnostic` fires on every append. Phases: `pane_mounted`,
`preconnect_fit`, `attach_ready_sent`, `full_redraw_requested`, `full_redraw_issued`,
`full_redraw_rendered`, `viewport_fit_deferred`, `viewport_fit_resumed`,
`viewport_fit_drift_repair`, `surface_redraw_deferred`, `surface_repair_resumed`,
`surface_redraw_confirmed`, `scrollback_repaint_requested`, `webgl_context_lost`,
`webgl_load_failed`, `webgl_render_error`.
Note that `full_redraw_rendered` proves only that xterm's `RenderService`
fired `onRender`, which it does whether or not the renderer painted — it is not evidence that
pixels changed.

Repair-class phases additionally reach the daemon unconditionally — no opt-in — as
`client_diagnostic` PTY frames persisted to the durable event log as `terminal_client_repair`
(allowlist and rate limits: `design/interfaces.md`). This is what makes each repair layer
individually auditable in production: a layer that never fires in months of logs is a removal
candidate, one that fires daily is load-bearing. Alongside them, every honored transcript
restatement is logged as `terminal_repaint_requested` with its trigger reason.

## Configurable session rows

A sidebar session row is a fixed gutter holding the state indicator, plus two lines.
Each line has a left-aligned and a right-aligned section, and each section is an ordered list of field slots.
The layout is user-configurable in Settings → Appearance → Session rows.

- **The indicator is not a field.**
  It sits outside both sections, is always drawn, and its colour, pulse, and hollow "engaged" variant are not configurable.
  Its *shape* is: hexagon (default), circle, or square.
  Its *size* is, in whole pixels within `DOT_SIZE_MIN`–`DOT_SIZE_MAX` (10–24), and separately per device class.
  It is vertically aligned to the **title line**, not to the middle of the row: centred on a two-line row it belongs to neither line.
- **The indicator's box, the row's gutter column, the row's height, and the stack thread all derive from `--session-dot` and the `--session-row-inset-*` pair.**
  The thread is drawn through the sessions' own status dots, so a hard-coded offset stops covering the dot the moment the indicator's size changes and paints a rule straight across it.
  The row's height is the same kind of hazard: a fixed `40px * --ui-scale` stopped containing the row's own content as soon as the indicator became configurable, so it is now that value *or* what the content needs, whichever is larger.
  Every existing configuration keeps exactly the height it had; only a row that would have clipped grows.
  The indicator's box is additionally floored at the title line's own line box (`--session-line-box`), because an indicator **smaller** than the line it is aligned to top-aligns inside it and sits visibly above the name.
  That floor is measured rather than assumed: 14px at scale 1, growing with the type, and never shrinking below 14px because the line also carries a 12px provider mark raised 2px.
  Above the floor the indicator sizes the line; below it the SVG's own `preserveAspectRatio` centres the shape in the slack.
  Expressing all of them from the same variables is what keeps drift impossible; `frontend/test/renderer/session-row-layout.spec.ts` measures containment and centring at both ends of the size range, because pure CSS geometry is invisible to every unit test.
  The indicator is deliberately **not** multiplied by `--ui-scale`, matching every other icon and touch target.
- **Size is the one part of the row configuration split desktop from mobile.**
  The layout is shared because the same person wants the same facts in the same order on both screens; a physical size is the one property a shared layout cannot express, because the two screens are held at different distances.
  Mobile's default is **larger** than desktop's (17px against 15px): a phone row is read at arm's length, is never hovered for the tooltip that would confirm it, and competes with a touch target rather than with a dense scannable list.
  Both values live in the single `sessionRows` blob as `dotSizeDesktop`/`dotSizeMobile` rather than in a second settings profile, so editing either is one write from either device.
  `sessionRowPrefs.applySessionDotSize` publishes the resolved value as `--session-dot` on the root element and re-resolves when a window crosses the device-class breakpoint, exactly as chrome scale does.
  Handing the number to `StateIndicator` as a prop instead would resize the glyph and leave the gutter, the thread, and the row height behind.
  A stored size outside the bounds is **clamped**, not discarded: a blob from a build with wider bounds should render at the nearest size this one can draw rather than silently reset and look like a lost setting.
- **The row never prints the state word.**
  The indicator already carries it, so `working`, `ready`, and `turn complete` are duplication rather than information.
  The `state` field exists for anyone who wants it back but is defined as never notable, so it renders only in `always` mode.
- **Placement and visibility are one decision.**
  A field placed in no section is off; there is no separate enable flag, and therefore no "enabled but nowhere" state.
- **Presence-only marks live in a flag strip pinned to the top line's right edge.**
  The strip is the top line's right section: the broadcast flag, standing activity, and unsent input, in that order, unshrinkable and never shed.
  Placed after the title instead, the marks sat inside the section that clips, so a title long enough to fill the sidebar hid every one of them — and the rows with the most to report are the ones with the longest names.
  A flag whose entire content is "this is true" has nothing to ellipsize, while a name that loses its tail stays recognisable, so the strip is laid out first and the title takes whatever is left.
  Placement is still configurable: putting a flag ahead of the title in the left section keeps it fully visible too, at the cost of a ragged left edge down the list.
- **Only the title yields on the top line.**
  Flexbox shrinks siblings in proportion, so the provider mark's token was squeezed below the mark itself, and — not being the last child, which is the only one that clips — its glyph spilled visibly under the first letter of a long name.
- **The hover-revealed row control gets a lane, and only while it is shown.**
  It is absolutely positioned over the row's right edge, so it lands on the flag strip (and, as it always had, on the bottom line's right-hand tokens) exactly when the pointer arrives to read them.
  Reserved on hover rather than always, because 26 px of every row is the width the strip exists to save.
- **Standing activity renders in exactly one place**, chosen by a single setting like context pressure: glyphs with counts in the flag strip (default), or a pip on the state indicator, or off.
  The pip costs no row width and cannot be clipped, and says only *that* something is standing — the kinds and counts move to the tooltip.
  It is a CSS mark at the indicator box's empty top-right corner, not another SVG path: a 24-unit box with a 6.2 core and a 10.2 ring has no empty annulus left, a mark on the ring is indistinguishable from the context gauge's peak dash, and one inside it lands on the state colour.
  Sized in pixels, it also stays legible at a 10 px indicator, where a shape-relative mark would be under two pixels across.
  The choice applies to tab strips and menus too, so one session never reports it twice on one screen.
- **Unsent input is marked with a caret bar (`▌`), in teal.**
  It reports `unsent_input` from the daemon (`features/terminal-input.md`) unioned with this device's own mobile draft registry, which never reaches the PTY and so is invisible to every other client.
  Where the two disagree the mark reports the **oldest**: the question is how long something has been sitting there, and a phone draft from an hour ago is not made recent by a keystroke on the desktop a minute ago.
  Teal because green, blue, amber, and red already say something about what the *agent* is doing, and this is the one mark on the row that is about the operator; the same family as the unread edge, for the same reason.
  An ended session has no composer and is never marked.
- **Every placed field is `when notable` or `always`.**
  Notability is per field: a branch that differs from the project's most common branch, a diff with changed lines, a queue with items, a model that differs from the project default, an account when more than one is live, a duration past its per-state threshold.
  The default configuration is almost entirely `when notable`, so a quiet fleet shows a title and a duration and anything visible has earned its place.
- Read-only model labels use the shared compact presentation rule, while tooltips, accessibility labels, configuration controls, session comparisons, and API values retain the exact provider identifier.
  The rule removes the provider path and a leading vendor-brand token (`claude-`, `gpt-`) and touches nothing else, because every surface that prints a model draws the session's provider mark beside it.
  A token that names a model *family* — `codex`, `kimi`, `o3`, `sonnet` — is not branding and stays.
  It replaced a hand-maintained per-family table, which printed the raw id for every model nobody had added yet: the sidebar showed `opus-5` beside `claude-fable-5`, and `sonnet-4-6` beside `gpt-5.6-sol`, in the same list.
- **Separators are per line** and render only between tokens that actually drew, so a hidden conditional field leaves no dangling or doubled mark.
- **Sections meet but never overlap, and the RIGHT one has precedence.**
  On both lines the right section is laid out first and the left section ellipsizes into what remains.
  A value the reader deliberately pinned to the row's edge must not be deletable by a long value on the other side.
  The bottom line used to do the opposite: neither section shrank, and the right one was pushed off the line's edge and clipped.
  Measured at the 190 px minimum, a 22-character worktree on the left held a fixed 116 px while 49 of the model's 68 px were cut off the right edge, mid-glyph and with no ellipsis — the box was never squeezed, so `text-overflow` never engaged.
  The failure the old rule guarded against, a fixed right section squeezing the left one out of existence, is prevented by a `min-width` floor on the left instead of by making it unshrinkable.
  Lopsided flex-shrink factors (1000 against 1) are what sequence the two: flexbox shrinks siblings in proportion, so the left absorbs the whole deficit until it reaches its floor, after which the right begins to yield.
  Exactly one token per section yields — the left section's last, the right section's first, the ones furthest from the edge the section is anchored to — because a squeezed non-clipping sibling spills its glyph under its neighbour instead of ellipsizing.
- **A field too wide for the row degrades down a three-rung ladder, in this order.**
  1. **Truncated.** CSS ellipsizes the yielding token down to `ROW_MIN_CHARS` (6). Nothing in the engine acts; it only accounts for the rung. The browser measures the available space exactly and truncates at every intermediate pixel, which a JS step could only quantise, and worse.
  2. **Its own mark.** Below that floor a field with an unambiguous glyph collapses to it — worktree `⌂`, branch `⎇` — and the value moves to the tooltip. An icon costs two characters against a value's ten or twenty, so collapsing the line is far cheaper than deleting from it and keeps every placed fact on screen, which is the point of having placed the field.
  3. **Dropped.** For a field with no honest mark, and for a line so narrow that even the marks do not fit.
  Six is the floor rather than a target: below it a worktree reads `feat-t`, which two sibling checkouts in one fleet will share, so the token is spending width to say something that no longer distinguishes.
  The mark is drawn whatever `gitGlyphs` says — that setting decides whether a glyph decorates the full value; this rung is the field's identity at the width where its value no longer fits.
  Not every field earns rung 2: `model` deliberately has no glyph, because the provider mark is already the `glyph` field and cannot tell opus from sonnet, so an icon there would claim to identify something it does not.
  Within rungs 2 and 3 the order is ascending `priority`, so the field the reader ranked lowest is the first to lose its value and the first to leave.
- **Rungs 2 and 3 happen in the token engine, never in CSS.**
  A container query cannot do it: hiding a token with `display:none` removes the token but not the separator already emitted beside it, so a narrowed row rendered as `· apply_patch` — a leading mark belonging to a token that was gone.
  The separator invariant is a property of the token list, so the list is what degrades.
- **A section is never emptied while it still holds a token.**
  The count-based shedding this replaced had no floor beyond "more than one token to begin with", so a two-token section at shed 2 lost both: a sidebar dragged to 230 px rendered a blank bottom line, and deleted an `always`-mode field to do it.
- **The budget is measured in characters, off a probe shaped like a row's text column.**
  `.row-metric` is a zero-height stand-in for one row's text column, nested in the real container chain and built from the same variables `.session-row` is (`--session-dot`, `--session-row-gap`, `--session-row-inset-x`), so the measured column cannot drift from the drawn one.
  Characters rather than pixels because the bottom line is monospace: on the line that carries almost every degradable field the unit is exact rather than estimated.
  It replaced pixel thresholds compared against the width of the whole `<aside class="sidebar">`, which overstates a row's room by the indicator gutter, the tree's and list's padding, and the scrollbar — 49 to 63 px at the default 254 px width depending on `--session-dot`, a setting the thresholds could not see.
  The shipped default therefore degraded a token from every section before anybody dragged anything, and a user who enlarged the indicator got the same thresholds over less room.
  A `ResizeObserver` on the probe's inner cell also catches the scrollbar appearing and the indicator being resized, neither of which resizes the sidebar at all.
  The probe deliberately does **not** carry the `.session-row` class: a second element wearing it changes what `querySelector('.session-row')` returns for every other reader, which is too large a side effect for a measuring stick.
- **The settings preview has its own width control, and measures its budget the same way.**
  It used to render at a fixed 420 px — wider than the sidebar can be dragged — so the one behaviour a reader cannot predict from the field list was the one behaviour the panel never showed.
  The width is device-local and unpersisted: it is an inspection control for this visit to the panel, not a property of the layout.
- **The empty bottom line is kept on desktop and dropped on mobile.**
  Constant row height is what makes a list scannable, and the blank reads as "nothing to report"; on a phone the vertical space is worth more.
- **One duration field, and within a turn it measures one thing.**
  Every live session is aged from its **turn**, not its state: a turn survives every tool call and every approval inside it, while `state_since` restarts on each of them, so a busy agent's timer reset every few seconds and never reported the length of the actual work.
  `awaiting` used to be the exception, aged from the block on the reasoning that the live question there is "how long has it been waiting on me".
  That answer is worth having, but not at the cost of the number changing what it measures underneath the reader: a session with several subagents raising permission prompts made the figure collapse to seconds and spring back to the turn length each time one appeared and was answered, which reads as a timer resetting at random.
  How long the block has stood now rides the **detail** text, where it is labelled (`awaiting approval 5m`) and can disagree with the turn without either looking broken.
  `idle` reports how long the **last completed turn** took; an ended session reports its lifetime.
- **A ready session with running work reports the request, not the turn that dispatched it.**
  The one exception to the rule above, and the case that rule could not see.
  A harness that dispatches background agents ends its root turn to hand off, so both of the column's clocks stop at once: `turn_started_at` goes away and `last_turn_ms` freezes at the length of the dispatching turn.
  Measured live 2026-08-19 on three ultracode sessions 37, 64, and 81 minutes into their requests, every one of them reading ~10m — and the number can shrink as the run continues, because every phase ends with a short main-loop turn that overwrites the measurement.
  The row prefers the daemon's `running_work_since`, falls back to `last_human_prompt_at` for records written before that field and for sessions adopted mid-flight, and falls back again to the last turn when neither anchor survives — a stale-but-real number still beats a fresh invented one.
  Gated on the same `hasRunningActivity` split the blue ring and the notification suppression use, so an armed `loop` or `cron` never turns a settled row into a running clock.
  It takes the **working** notability threshold rather than the last-turn one: the low threshold exists because a finished turn is worth reporting sooner than a running one is worth interrupting for.
  The state axis is untouched — the turn really did end, the dot stays a ready ring, and typing is still safe.
- **`Since your prompt` answers what the turn cannot.**
  A turn is one request-to-completion cycle, and auto-delivery or an injected teammate message opens turns nobody asked for, so a busy session can be minutes into a fresh turn and an hour past anything its operator said.
  The field reads `last_human_prompt_at` and carries a `⌨` mark, which is load-bearing rather than decorative on the same grounds as the branch scope mark: two bare durations in one section are indistinguishable, and these two are exactly the pair a reader is trying to tell apart.
  Notable only when it exceeds *whatever the duration column is currently measuring* by `PROMPT_GAP_NOTABLE_SECONDS` — where the two numbers track each other the second one is one fact twice.
  On an `idle` session with nothing running it never speaks, because "you asked an hour ago" describes no outstanding work; on an `idle` session with running work it can, because there the duration column is a live measurement and the two can genuinely part.
  Every form is at most four characters (`59s`, `1m12`, `22m`, `1h30`, `3d6h`) so the right section forms a column rather than a ragged edge.
  A ready session's number is static, so a settled fleet re-renders on no clock at all — the one ready row whose token changes between ticks is the one with work still running.
- **The duration renders nothing rather than a placeholder when it cannot answer.**
  A row that says nothing is read as "no measurement"; a row that says `0s` is read as a measurement of zero, and both failures this field has actually produced were indistinguishable from a turn that had just begun.
  A `last_turn_ms` under `MIN_REPORTABLE_TURN_MS` (250 ms) is refused: daemons predating record-dated turn boundaries wrote the replay's own elapsed time into records that survive a restart, so those values are already on disk and the row has to refuse them on the way out too.
  A `turn_started_at` or `state_since` more than `CLOCK_SKEW_TOLERANCE_SECONDS` (5 s) in this device's future is refused for the same reason.
  Inside that band a zero is the truth — a turn one tick old really has run for no whole seconds — so the tolerance must not swallow the honest zero.
- **Sidebar durations are aged on the daemon's clock, not the browser's.**
  Every timestamp the row subtracts from was written by the daemon, and the two clocks are only the same clock when the browser runs on the same machine.
  Remote access is a first-class way to use swe-mux, so a laptop or phone a few seconds behind made the age of every working session negative, and clamping the negative away froze the row at `0s` for the whole turn with nothing else on screen looking wrong.
  `serverClock.ts` holds the offset, read from the HTTP `Date` header that every response already carries — including error responses, so the clock stays honest through an outage, which is exactly when a wrong offset would be least likely to be noticed.
  Request latency is halved out the way NTP does it, and the offset only moves when a reading disagrees by more than `CLOCK_OFFSET_NOISE_FLOOR_SECONDS` (2 s), because a duration the user watches count up must not step backwards because one poll landed differently.
- **Every Git field describes the checkout, never the session.**
  `git status` answers for the whole repository however it is invoked, so sessions sharing a working tree necessarily report identical figures and there is no per-session measurement to take.
  A quantity whose checkout (`GitState.root`) has more than one **live** session is drawn underlined, and its tooltip says how many; ended sessions do not count, because one live session in a checkout is unambiguous however many corpses sit beside it.
  Only the quantities are marked — lines and files, uncommitted and branch — not the branch or worktree name, which nobody reads as one agent's work.
- **The Git quantities come in two scopes, and both are offered.**
  `diff`/`dirty` are measured against HEAD, so they drop to zero the moment a session commits.
  `compareDiff`/`compareFiles` are measured from the merge base with the Project's comparison ref and therefore keep counting committed work; they carry a `⎇` scope mark so a row holding both does not print the same `+312 -48` twice with no way to tell which is which.
  A worktree-per-branch fleet that commits as it goes reads `+0 -0` on the HEAD-scoped pair alone, which is what the branch-scoped pair exists to fix.
  Either pair renders nothing at all when it could not be measured; a zero would claim a clean tree, or a branch identical to its base.
- **Context pressure renders in exactly one place**, chosen by a single setting: an arc around the indicator (default, costs no row width, marks the peak on the same outline), a four-cell gauge, a percentage, or off.
- **The working pulse animates the indicator's core alone.**
  The arc is a measurement that only moves when the conversation grows, so blinking it alongside the core made a static reading look like live activity.

Mobile shares the one layout rather than keeping a second one.
`mobileFields` decides whether the phone renders the configured sections or identity only — indicator, provider mark, title, and the flag strip.
The strip survives the identity projection because a phone is where an unsent draft is most likely to have been left behind, and a projection that dropped the marks would be silent on exactly the device that stages text and walks away.
Both screens want the same information in the same order; only how much of it fits differs.

The stored layout is versioned, and version 2 introduced the flag strip.
Changing the shipped default reaches nobody who has ever opened the settings — a stored blob is authoritative and an unplaced field is off — so the migration moves already-placed flags into the strip and places `draft`, which nobody could have declined because it did not exist.
A flag the user had removed stays removed: the migration relocates a choice, it does not re-impose one.

The state indicator is SVG rather than a styled element.
A hexagon is expressible as `clip-path`, a *hollow* hexagon is not, and a gauge that follows a hexagon's outline is not expressible in CSS at all.
In SVG all three are one path: `pathLength="100"` normalizes every shape's perimeter, so one `stroke-dashoffset` calculation fills a circle, a square, and a hexagon identically, clockwise from twelve o'clock.
Colour still arrives through the existing `.state-dot` state classes, so themes keep overriding one palette.

## Key files

- `frontend/src/App.tsx`
- `frontend/src/dismissStack.ts`
- `frontend/src/systemBack.ts`
- `frontend/src/viewHistory.ts`
- `frontend/src/modalFocus.ts`
- `frontend/src/sidebarSearch.ts`
- `frontend/src/fuzzyText.ts`
- `frontend/src/sessionRowConfig.ts`
- `frontend/src/sessionRowFields.ts`
- `frontend/src/sessionRowPrefs.ts`
- `frontend/src/SessionRowBody.tsx`
- `frontend/src/SessionRowSettings.tsx`
- `frontend/src/StateIndicator.tsx`
- `frontend/src/dotShapes.ts`
- `frontend/src/ProjectsManager.tsx`
- `frontend/src/Settings.tsx`
- `frontend/src/GuidedTutorial.tsx`
- `frontend/src/tutorial.ts`
- `frontend/src/ProviderAccounts.tsx`
- `frontend/src/ResourceUsage.tsx`
- `frontend/src/TerminalPane.tsx`
- `frontend/src/TerminalDraftComposer.tsx`
- `frontend/src/mobileTerminalDraft.ts`
- `frontend/src/ProjectRunMenu.tsx`
- `frontend/src/DirectoryPicker.tsx`
- `frontend/src/terminalRenderDiagnostics.ts`
- `frontend/src/style.css`
