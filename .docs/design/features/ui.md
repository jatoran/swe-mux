# Browser interaction model

## What it is

- Preact single-page client with xterm.js panes; spaces and sessions share one sidebar hierarchy.
- Visual language mirrors tmux inside a terminal: every chrome surface, menu, label,
  input, hint, heading, and xterm uses the same 11px semibold Cascadia Mono cell face,
  line height, and non-ligature rendering; prompt-like labels; square borders; neutral
  near-black surfaces; and restrained status-color accents without colored fills. Empty
  states use a flat background with no decorative grid or blinking cursor. Context menus
  and controls retain normal pointer behavior.
- Sidebar spaces/sessions render as an unboxed TUI tree. Right-clicking unused sidebar
  space opens workspace-wide Create space, process-fleet, and All Settings actions; space
  rows retain only space-owned actions. The footer exposes one `: menu` command instead of
  a persistent action strip. The sidebar heading owns the `swe_mux` identity and daemon
  status light; no global
  top bar repeats the active space. Space rows omit derivable session counts. The
  session-first tree has no visible `split`/`tabs` container rows: connector
  rails and direction/tab glyphs express nesting around the actual session rows. Layout-free
  sessions use the same neutral row treatment as other sessions; focus and runtime state
  never create a separate section or reorder sidebar rows.

## Operations

- `New terminal` opens immediately in the current space using active/recent cwd.
- `New terminal custom…` selects configured/detected shell profile plus typed, recent,
  active, pinned, or daemon-browsed cwd. The last custom choice is remembered without
  changing global/space defaults.
- Ordinary creation replaces the focused viewport; the displaced session stays live in the sidebar.
- Splits are explicit only: open an existing session in a split or create a new terminal in a split from the session context menu.
- Directory dropdown opens a compact recent/active cwd launcher; no backend modal.
- Recursive horizontal/vertical split trees persist per space. Explicit split, attach,
  detach, cross-space move, swap-next, draggable ratios, focused replacement, and temporary
  zoom never terminate displaced live sessions.
- Every terminal region shows a tab strip on desktop, including singleton sessions. The strip
  owns the session title and exposes a trailing `+` that creates a default terminal in the same
  region, anchored to a terminal tab because the active tab may be a spawned preview. Narrow
  screens hide these strips in favor of the focused-session selector.
- A preview registered by a session joins that session's tab strip instead of splitting a new
  region, so an agent and the servers it spawned swap with one click; preview tabs carry a `◱`
  glyph and their port. Sidebar session rows nest only that session's servers beneath them:
  an open preview activates its tab, and a detected-but-unopened loopback listener opens as
  one. Listening on a port is the sole test for a row, because a session's remaining children
  are bookkeeping (`cmd`, `python`, `claude.exe`) that no liveness or age filter separates
  from signal. Every other process stays in the process inspector, which remains the place to
  see the full tree.
  Multi-session stacks retain N atomic sessions; their bounded, horizontally scrollable tabs
  keep independent active/working/awaiting/finished state. The pane header carries runtime
  state, cwd, and actions without repeating the title. Diagnostic identity — Git
  (`git:<branch> +<dirty-files>`), PID, and the `boot:<time>` / `ready:<time>` startup
  chip — lives in an info row at the top of the expanded session menu instead of the
  header. The startup tooltip combines daemon phase/cumulative milestones with
  browser-local API response, pane mount, WebSocket open, and replay-ready totals from the
  original launch click. On replay-ready, the browser reports that bounded allowlisted
  timing map once; the daemon persists it as a browser-sourced event so closing the
  terminal does not discard the sample.
  The header's detach control renders only when detaching changes the layout: another
  pane/preview region exists or the session shares a tab stack. A lone pane hides the
  control, the context-menu item, and disables the palette command with an explanation.
  A confirmed OSC 7 cwd is live; before confirmation the immutable spawn cwd is visibly
  dimmed and prefixed `last-known::` so origin is never presented as current telemetry.
  Sidebar and stage navigation activate ancestor tabs bidirectionally. Grouped sessions
  follow persisted layout order and layout-free sessions follow stable creation order;
  focus and runtime state only change row styling. Selecting a layout-free session presents
  a transient singleton viewport without mutating the persisted layout. Only explicit
  attach, split, tab, detach, and move operations change grouping. Right-clicking a tab
  activates its session and opens the canonical session menu.
- Command registry gives routed actions stable IDs, availability explanations, and current
  bindings. The palette fuzzy-searches commands and supports arrow selection, Enter, and Escape.
  Default global bindings include `Ctrl+Alt+T` and `Ctrl+Alt+P`.
- Event-driven state refresh is burst-coalesced and single-flight; the periodic refresh
  shares that gate so a busy agent cannot create overlapping API request storms.
- Pane-next/previous and space 1–9 are named commands available to the same keybinding router.
- Settings gives Input its own tab and presents bindable commands as an interactive shortcut
  list rather than raw JSON. Clicking a row captures one chord; conflicts and the backend-owned
  browser/terminal reserved lists are rejected inline before save. The complete replacement file
  supports explicit unbinding while legacy bare-object files continue to merge over defaults.
- Terminal key interception is centralized in `attachCustomKeyEventHandler`.
- Terminal find is an inline SearchAddon widget with next/previous, case matching,
  match feedback, Escape close, and terminal-focus restoration.
- Ctrl+V uses `term.paste()` so bracketed paste survives Claude/Codex TUIs.
- xterm uses WebGL when available with canvas fallback, Unicode 11 widths, Web Links,
  Search, Fit, and OSC-52 Clipboard addons; screen-reader rows remain enabled.
- UI typography tokens stop at the xterm root; xterm owns its internal character
  measurement styles through matching Terminal options. ResizeObserver/window changes
  coalesce FitAddon work to the next animation frame, then invalidate every terminal row
  after layout settles. Replay completion, pane intersection, browser visibility/focus, and
  page restoration request the same full redraw and clear the WebGL texture atlas. WebGL
  context loss disposes the addon and falls back to the canvas renderer. Pane header height
  matches its grid track exactly so the final terminal row is never partially clipped.
- One attached xterm owns input at a time; focus claims ownership and prevents duplicate device responses.
- History surface renders native transcripts read-only and resumes as a new mux session.
- Agent rows show backend, working/ready/awaiting state, active tool detail, and
  `ctx used N%` when the native event stream has reported a context window.
- Location data has one owner per surface: space rows identify workflow context,
  sidebar session rows show identity/state, and pane headers show cwd once. Git branch
  and dirty-count status appears only in the session menu's info row, never in pane
  headers or sidebar rows.
- Sidebar/header live-session kill controls use `×` → `✓`; the confirmation expires back to
  `×` after two seconds. For exited or crashed sessions the same control is labeled Remove
  from sidebar and confirms removal without retrying process shutdown. Context-menu actions
  remain immediate and use the matching Kill or Remove wording.
- Context menus and `: menu` close on Escape or any pointer press outside their bounds.
- A pane header right-click and its accessible three-dot button open the same canonical
  session command model as the sidebar row; menu dismissal restores focus. Drag/drop and
  command/menu alternatives cover split, stack, reorder, detach, and dissolve on desktop,
  keyboard, touch, and narrow layouts.
- Session/space rename, worktree creation, and terminal find use
  terminal-styled panels; browser-native alert/confirm/prompt dialogs are prohibited.
- Settings uses the same monospace terminal cell styling as all chrome. A persistent TUI
  tab rail renders only the selected category; fields use label-left/value-right rows.
  Terminal profiles use a collapsed-by-default master/detail browser, so executable,
  arguments, environment, and capabilities appear only after explicit profile selection.
- Responsive mode moves the sidebar into a `:nav` drawer and shows only the focused pane. A
  safe-area-aware top toolbar keeps `:nav`, the focused-session selector, and a one-tap `+`
  (new default terminal in the current space) in normal layout flow above the workspace. The
  drawer retains its 44px `: menu` and Notes footer actions. Long-press context menus render
  above the open drawer and its scrim, so space/session actions remain visible and operable.
- Mobile typing goes directly through xterm for every backend. The app shell follows the live
  visual viewport (and requests `interactive-widget=resizes-content`), so opening the software
  keyboard shrinks the terminal above it; the existing resize observer then refits terminal rows.
- Narrow screens also add mobile history master/detail navigation, a Settings section navigator,
  full-screen notes/process/preview surfaces, long-press context menus, touch-safe positioning,
  and 44px coarse-pointer controls. Input settings configure vertical drag routing, natural/wheel
  direction, sensitivity, and long-press behavior. Smart vertical
  drag preserves xterm scrollback unless a TUI has enabled mouse tracking, where it synthesizes
  wheel input so Claude/Codex history can scroll.
- Focus is device-local and URL-addressable (`space`/`session` query parameters). Reload chooses
  a valid URL target, then the remembered session for that space, then a visible live fallback;
  focusing updates the URL with `replaceState` so refresh and shared links do not jump to the
  first session.
- Dialogs and menus use semantic roles, keyboard focus trap/restore, labelled icon
  controls, live status/error regions, and reduced-motion behavior. The browser tab title
  remains the stable `swe-mux`; in-app indicators and a screen-reader live region announce
  the number of agents awaiting attention.
- Normal paste has no floating controls. Ctrl+V suppresses only xterm's control byte; the ensuing
  browser paste event is the sole clipboard-payload owner, so one gesture produces one upload.
  Text continues through xterm unchanged. A supported image pasted or dropped onto an open
  Claude/Codex pane is stored as bounded private media, then delivered as one isolated file-path
  paste. Claude recognizes the path; Codex accepts it only while bracketed paste is active and
  converts it into a local-image attachment. Dragging a file shows an in-pane drop target and
  never navigates the browser. The long-press menu's single Paste action uses the richer Clipboard
  API as an explicit fallback.
- OSC-52 writes use the browser clipboard when permitted. If a mobile/insecure-context browser
  rejects the write, swe-mux retains the bounded text in a visible user-gesture `Copy now`
  surface with a selectable-text fallback instead of discarding `/copy` output.
- Project tools are reachable where their identity is visible: space right-click
  opens the app-owned space note; agent session right-click opens its run-owned note; shell
  menus open the explicitly labelled current project note; pane
  headers expose `note` and `proc`; terminal right-click repeats the session tools. `: menu`
  groups durable navigation and configuration rather than duplicating Create space,
  note-presentation, or selected-session process actions. The palette retains stable
  command access.
- Projects is a durable registry, separate from the space sidebar. It exposes project
  config, diagnostics, detached/unlinked files, hide/unhide, guarded registry Forget, and
  direct project-note entry. History project filters use concrete scope; repository grouping
  is display-only.
- `# notes` is aligned opposite `: menu` in the sidebar footer and opens the global Notes
  shelf. The shelf searches saved space/project/agent-run notes and filters them by Recent,
  Spaces, Projects, Agent runs, and Recovered. Friendly titles, owners, project/backend/state,
  timestamps, and excerpts lead; paths appear only for non-openable recovery diagnostics.
- Notes edit raw Markdown in one CodeMirror surface — never a WYSIWYG or an edit/preview
  split. It exists to soft-wrap with a hanging indent, so a wrapped line continues at its own
  leading indentation instead of resetting to column 0; a textarea has no per-line boxes and
  cannot do this. The editor carries a panel background distinct from terminal panes. Tab
  indents the line by one indent unit and Shift-Tab dedents, writing spaces rather than hard
  tabs; `Ctrl-m` (`Shift-Alt-m` on macOS) toggles tab-focus mode so Tab can still move focus
  and the binding never traps keyboard navigation.
- Sidebar rows mark a session in the broadcast set with a `⇶` glyph, so membership is visible
  without opening a menu.
- Notes use one persistent tabbed Notes workspace owned by each space. It has docked and
  pop-out presentations; changing presentation moves the entire workspace, including every
  open tab, and keeps inactive editors mounted so pending edits are not discarded. The dock
  sits right of the entire terminal layout on desktop, uses the bottom half on mobile, keeps
  its draggable desktop size, and remains visible across session/tab/split selection. A
  global opening preference is inherited by default and each space may override it with
  Dock or Pop-out; explicit presentation changes do not rewrite either preference.
- Layout v5 separates the terminal/preview tree from `note_workspace`, which persists open
  note ids, active tab, visibility, dock size, and presentation mode. Loading v4 `note_dock`
  state or v2/v3 embedded note leaves migrates them into workspace tabs and collapses former
  split branches without file or terminal loss. Closing a tab changes only layout state;
  hiding the workspace retains its tabs.
- Sidebar note rows are intentionally terse: `space note`, `project note`, or `agent note`.
  Space notes sit under their space, live agent notes may sit under their run, and project or
  ended-run notes stay at space level rather than inheriting an arbitrary terminal owner.
  Saved space-note presence still comes from the app-owned inventory, not only dock state.
- Note rows and Notes-workspace tabs share one right-click presentation menu: Dock workspace,
  Pop out workspace, browse the Notes shelf, or close the selected note tab. Presentation
  moves the whole tabbed Notes workspace and never changes note ownership.
- The process-fleet surface samples all live session trees once per refresh and groups them
  by space then session. Fleet totals and rows expose CPU, resident memory, listener count,
  and established-connection count; each session renders a PID/parent-PID hierarchy rather
  than a flat process list. Registered and detected previews remain session-owned.
  Selecting a session narrows the same surface and exposes process actions plus preview
  registration; `← all processes` restores fleet scope. The sidebar `: menu` exposes Process
  fleet as a durable global entry point. Network byte throughput is omitted because the
  portable process boundary cannot measure it reliably on Windows.
- Opening from the Notes shelf hides rather than unmounts the index. Editor Back/Browse
  restores the existing query, filter, and scroll position instead of forcing a new search.
- A meta-hook UI notification appears as a live clickable toast and in a keyboard-trapped
  retained notification inbox. Inbox entries show delivery status/correlation and can
  focus their still-live originating session.
- Settings edits ordered profiles: add, duplicate, reorder, enable/disable, validate,
  delete, restore detected presets, choose global default, and set per-space profile/cwd.
- `: menu` and the command palette open a keyboard-trapped terminal-style Settings
  surface. Config, keybindings, and hooks are validated before atomic replacement;
  field errors and hot-apply/restart status remain inline.
- Dark, Light, System, Solarized Dark, Tokyo Night, and validated custom themes share
  semantic chrome/xterm tokens. Preview updates existing terminals, Cancel reverts,
  System follows browser color-scheme changes, and custom theme JSON can be imported or
  exported. Foreground/background contrast below 4.5:1 is rejected.
- The daemon retains exact scrollback bytes. xterm line capacity is derived from that
  limit using a documented 160-byte average, clamped to 1,000–100,000 lines.

## Key files

- App state and commands: `frontend/src/App.tsx`
- Command registry/search: `frontend/src/commands.ts`
- Layout tree operations: `frontend/src/layout.ts`
- Terminal contract: `frontend/src/TerminalPane.tsx`
- API/WebSocket client: `frontend/src/api.ts`
- Notifications: `frontend/src/Notifications.tsx`
- Notes discovery: `frontend/src/NotesShelf.tsx`
- Theme/layout: `frontend/src/style.css`
