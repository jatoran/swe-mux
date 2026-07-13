# Browser interaction model

## What it is

- Preact single-page client with xterm.js panes; spaces and sessions share one sidebar hierarchy.
- Visual language mirrors tmux inside a terminal: every chrome surface, menu, label,
  input, hint, heading, and xterm uses the same 11px semibold Cascadia Mono cell face,
  line height, and non-ligature rendering; prompt-like labels; square borders; neutral
  near-black surfaces; and restrained status-color accents without colored fills. Empty
  states use a flat background with no decorative grid or blinking cursor. Context menus
  and controls retain normal pointer behavior.
- Sidebar workspaces/sessions render as an unboxed TUI tree. Workspace creation actions live on workspace right-click; the footer exposes one `: menu` command instead of a persistent action strip.

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
- Command registry gives routed actions stable IDs, availability explanations, and current
  bindings. The palette fuzzy-searches commands and supports arrow selection, Enter, and Escape.
  Default global bindings include `Ctrl+Alt+T` and `Ctrl+Shift+P`.
- Pane-next/previous and workspace 1–9 are named commands available to the same keybinding router.
- `~/.mux/keybindings.json` overrides command chords; unmodified and browser-reserved chords are rejected.
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
- Location data has one owner per surface: top chrome shows the active workspace,
  sidebar rows show session identity/state, and pane headers show cwd once. Git branch
  and dirty-count status appears only in the pane header, never in sidebar rows.
- Sidebar/header kill controls use `×` → `✓`; the confirmation expires back to `×`
  after two seconds. Context-menu Kill remains immediate.
- Context menus and `: menu` close on Escape or any pointer press outside their bounds.
- Session/space rename, worktree creation, terminal find, and bearer-token entry use
  terminal-styled panels; browser-native alert/confirm/prompt dialogs are prohibited.
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
- Phase 4 workspace tools are reachable where their identity is visible: space right-click
  opens space notes; session right-click opens session notes and processes/previews; pane
  headers expose `note` and `proc`; terminal right-click repeats the session tools. `: menu`
  groups history, both note scopes, process previews, notifications, project settings,
  usage analytics, hooks, and full Settings. The palette retains stable command access.
- Notes use two deliberate presentations of the same project Markdown: a centered quick
  modal for short edits and a persistent note leaf docked beside a live terminal for ongoing
  work. The default dock is a 62/38 terminal/note horizontal split; its divider remains
  draggable. Pane close removes only the leaf, `pop out` returns it to the modal, and mobile
  shows an explicitly activated docked note over the focused terminal with a return action.
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
- API/auth client: `frontend/src/api.ts`
- Notifications: `frontend/src/Notifications.tsx`
- Theme/layout: `frontend/src/style.css`
