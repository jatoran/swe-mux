# Browser interaction model

## What it is

- Preact single-page client with xterm.js panes; spaces and sessions share one sidebar hierarchy.
- Visual language mirrors tmux inside a terminal: every chrome surface, menu, label,
  input, hint, heading, and xterm uses the same 11px semibold Cascadia Mono cell face,
  line height, and non-ligature rendering; prompt-like labels; square borders; neutral
  near-black surfaces; and restrained status-color accents without colored fills. Empty
  states use a flat background with no decorative grid or blinking cursor. Context menus
  and controls retain normal pointer behavior.
- Sidebar spaces/sessions render as an unboxed TUI tree. Space creation actions live on
  space right-click; the footer exposes one `: menu` command instead of a persistent action
  strip. The sidebar heading owns the `swe_mux` identity and daemon status light; no global
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
- Every terminal region shows a tab strip, including singleton sessions. The strip owns the
  session title and exposes a trailing `+` that creates a default terminal in the same region.
  Multi-session stacks retain N atomic sessions; their bounded, horizontally scrollable tabs
  keep independent active/working/awaiting/finished state. The pane header carries runtime,
  cwd, Git (`[git:<branch> +<dirty-files>]`), PID, and actions without repeating the title.
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
  coalesce FitAddon work to the next animation frame, and pane header height matches its
  grid track exactly so the final terminal row is never partially clipped.
- One attached xterm owns input at a time; focus claims ownership and prevents duplicate device responses.
- History surface renders native transcripts read-only and resumes as a new mux session.
- Agent rows show backend, working/ready/awaiting state, active tool detail, and
  `ctx used N%` when the native event stream has reported a context window.
- Location data has one owner per surface: space rows identify workflow context,
  sidebar session rows show identity/state, and pane headers show cwd once. Git branch
  and dirty-count status appears only in the pane header, never in sidebar rows.
- Sidebar/header kill controls use `×` → `✓`; the confirmation expires back to `×`
  after two seconds. Context-menu Kill remains immediate.
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
- Responsive mode moves the sidebar into a `:nav` drawer and shows only the focused pane.
- Responsive panes expose an on-screen Paste control for mobile clipboard limitations.
- Narrow screens add a focused-session selector, mobile history master/detail navigation,
  a Settings section navigator, full-screen notes/process/preview surfaces, long-press
  context menus, touch-safe positioning, and 44px coarse-pointer controls.
- Dialogs and menus use semantic roles, keyboard focus trap/restore, labelled icon
  controls, live status/error regions, and reduced-motion behavior. The document title and
  a screen-reader live region announce the number of agents awaiting attention.
- Clipboard images use a separate user-gesture path from text. Only Claude/Codex sessions
  accept supported image MIME types; the daemon stores bounded private media and xterm
  pastes only the resulting local file reference.
- Project tools are reachable where their identity is visible: space right-click
  opens the app-owned space note; agent session right-click opens its run-owned note; shell
  menus open the explicitly labelled current project note; pane
  headers expose `note` and `proc`; terminal right-click repeats the session tools. `: menu`
  groups history, both note scopes, process previews, notifications, project settings,
  usage analytics, hooks, and full Settings. The palette retains stable command access.
- Projects is a durable shelf, separate from the space sidebar. It exposes project config,
  project/agent-run notes, diagnostics, detached/unlinked files, hide/unhide, and guarded
  registry Forget. A clearly separate app-owned section exposes live and archived space
  notes. History project filters use concrete scope; repository grouping is display-only.
- Notes use two deliberate presentations of the same project Markdown: a centered quick
  modal for short edits and a persistent note leaf docked beside a live terminal for ongoing
  work. The default dock is a 62/38 terminal/note horizontal split; its divider remains
  draggable. Pane close removes only the leaf, `pop out` returns it to the modal, and mobile
  shows an explicitly activated docked note over the focused terminal with a return action.
- Docked note leaves do not become anonymous layout rows. The sidebar nests a space note
  under its space and project/agent notes under their associated session, naming both note
  kind and owner. Selecting a session clears mobile note focus and reveals its terminal;
  resource-only layouts preserve the note while restoring the selected terminal beside it.
- Sidebar note presence is not inferred solely from layout. Saved space notes come from the
  app-owned note inventory and display `saved` or `open pane`; project/agent rows remain
  session-attached viewport resources to avoid duplicating one project note under every shell.
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
- Theme/layout: `frontend/src/style.css`
