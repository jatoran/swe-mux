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
- Ordinary creation replaces the focused viewport; the displaced session stays live in the sidebar.
- Splits are explicit only: open an existing session in a split or create a new terminal in a split from the session context menu.
- Directory dropdown opens a compact recent/active cwd launcher; no backend modal.
- Up to four persisted panes per space; focused pane replacement, context split, detach, double-click zoom.
- Command palette exposes named actions; default global bindings include `Ctrl+Alt+T` and `Ctrl+Shift+P`.
- Pane-next/previous and workspace 1–9 are named commands available to the same keybinding router.
- `~/.mux/keybindings.json` overrides command chords; unmodified and browser-reserved chords are rejected.
- Terminal key interception is centralized in `attachCustomKeyEventHandler`.
- Ctrl+V uses `term.paste()` so bracketed paste survives Claude/Codex TUIs.
- xterm uses WebGL when available with canvas fallback, Unicode 11 widths, Web Links,
  Search, Fit, and OSC-52 Clipboard addons; screen-reader rows remain enabled.
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
- Session/space rename uses an inline terminal-styled modal; native browser prompts are
  not used for rename.
- Responsive mode moves the sidebar into a `:nav` drawer and shows only the focused pane.
- Responsive panes expose an on-screen Paste control for mobile clipboard limitations.

## Key files

- App state and commands: `frontend/src/App.tsx`
- Terminal contract: `frontend/src/TerminalPane.tsx`
- API/auth client: `frontend/src/api.ts`
- Theme/layout: `frontend/src/style.css`
