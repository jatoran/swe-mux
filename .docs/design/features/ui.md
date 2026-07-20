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
- The sidebar shows only Projects marked for active navigation. Each Project row exposes its
  fixed Project note and Files view, then layout/session rows. An initialized or open session
  note appears beneath its terminal.
- The active-Project header and each Project row expose **Run**. Its compact menu contains new
  Claude/Codex/shell/custom-terminal launchers followed by trusted Project Actions; it is a
  launch surface, not persistent sidebar grouping.
- `projects` opens the viewport-level Projects manager, which lists configured visible and
  hidden Projects. A Project must exist before terminal actions are enabled.
- Separate Claude and Codex rows and owned CPU/RSS status remain pinned at the sidebar bottom.
  Account/resource popovers render through the viewport overlay layer, so a narrow or collapsed
  sidebar cannot clip them.
- Collapsing the desktop sidebar leaves a rail rather than a dead strip. Bottom-up it carries
  Projects, menu, one quota chip per provider, and owned RAM, mirroring the expanded sidebar
  where menu and Projects are the last rows and status sits above them. The rail keeps those
  controls reachable without an expand round-trip, and every indicator opens the same popover as
  its expanded counterpart.
- Each quota chip stacks the provider's own mark above its weekly percentage. Weekly is the
  window worth a permanent glance: the 5-hour session window churns constantly, and `fable` is a
  sub-window of one provider's plan rather than a measure comparable across providers. The mark
  is the only thing identifying the row, so it keeps full contrast while the percentage carries
  the shared ok/warn/critical banding. Providers render in the same order as everywhere else.
- The resource chip reports RAM rather than CPU, since a percentage that moves every sample is
  not worth a permanent glance, and abbreviates it (`3.2G`) to fit the strip.
- Popover direction is independent of the condensed trigger, so a rail anchored at the bottom of
  the window still opens upward.
- Git state is Project/session metadata. Worktrees have no first-class sidebar row, creation
  modal, or workspace ownership.

## Menus and overlays

- Scope follows the menu that opened a surface, never a hidden mode. The app menu's
  `BROWSE ALL PROJECTS` section opens History, session notes, Process fleet, prompt library,
  usage, and notifications across every Project; right-clicking a Project row opens the same
  surfaces under `BROWSE THIS PROJECT`, prefiltered to it. Right-clicking empty sidebar space is
  the no-Project case and matches the app menu. Only actions that must target somewhere (new
  terminal, Project settings) stay in the app menu's current-Project section.
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
- Account, resource-usage, context, and command popovers are viewport-anchored. Settings,
  Projects, transcript review, and confirmation dialogs use the modal layer. Opening a child
  dialog from Projects must place it above the manager, never beneath it.
- Every full-screen dialog layer stacks above all persistent chrome: the mobile toolbar, mobile
  nav toggle, desktop top bar, and context menus. Chrome painting over a dialog does not merely
  look wrong, it silently swallows taps on the dialog's own header, which is where close and
  primary actions live. On phones the sticky mobile toolbar previously covered the Projects
  registry header, so `+ Add project` rendered but could not be tapped.
- Backdrop clicks close Settings. Dirty settings first open an in-app Save/Discard decision;
  interaction with that confirmation is inside the modal boundary and cannot also trigger the
  Settings backdrop.

## Settings contract

- Form changes remain local drafts until explicit Save. Save state is visible as
  dirty/saving/saved, and a background refresh cannot reset the selected settings section.
- Close, Escape, backdrop click, and navigation away all share the Save/Discard guard when a
  draft is dirty. Shell executable/profile paths deliberately use this explicit flow rather
  than per-keystroke persistence.
- Multiline ignore inputs preserve Enter/newlines in draft state. Save trims entries and removes
  blank lines before sending normalized patterns.
- Notification sounds preview immediately after browser audio unlock. Bundled choices are
  intentionally restrained; volume, per-event enablement, quiet hours, and test playback are
  device preferences.

## Focus and responsive behavior

- Focus is device-local and URL-addressable by Project/session. Reload prefers a valid URL
  target, then remembered focus, then a visible fallback.
- Mobile's top row contains navigation at left, active Project name centered, and provider
  accounts at right. It has no separate session dropdown.
- Mobile's contextual toolbar includes the same Project-level Run menu as desktop.
- Mobile uses one horizontally scrolling tab rail and one selected view. This is a projection
  of the durable desktop pane tree; see `workspace-layout.md` for placement and restoration
  rules.
- The selected terminal keeps an in-flow session header above a remaining-height terminal
  surface. Terminal visibility does not depend on convergence with a separate global active ID.
- Touch long-press in a terminal selects the word under the pointer and drag extends that xterm
  selection. Touch-originated synthetic context-menu events never open the desktop terminal
  menu. Selection release automatically attempts to copy by default; the preference is
  hot-reloadable from Settings.
- Narrow and coarse-pointer terminals focus a dedicated native IME bridge. Android composition
  replacements are converted to incremental terminal text and DEL input as they happen, so Gboard
  and other composing keyboards provide live PTY input without xterm's temporary composition box.
- Every terminal has an in-flow clipboard rail on desktop and mobile. Paste uses the browser
  clipboard when permitted and otherwise opens a focused native-paste target. Claude and Codex
  rails prefetch normalized transcript text so Copy reply runs inside the button gesture rather
  than typing `/copy` or waiting for OSC 52. Reply extraction walks back to the newest turn with
  meaningful assistant text; provider control acknowledgements such as `No response requested.`
  never replace the last copyable reply.
- Terminal copy is success-preserving: keyboard, menu, automatic selection, the action rail, and
  provider OSC 52 requests retain the exact text until a write succeeds. Blocked or insecure
  clipboard contexts open a prepared fallback automatically, leaving one explicit Copy tap.
- Shift+Enter and Ctrl+Enter insert a newline in Claude and Codex terminals instead of
  submitting. The browser cannot express either chord in the legacy encoding both agents parse,
  so the pane rewrites them to ESC+CR, the one sequence Claude and Codex each read as an editor
  newline. Shell terminals keep Enter's plain carriage return, and Ctrl+Enter is reserved from
  custom keybindings so no command can shadow the newline.
- Modal focus trapping, keyboard navigation, reduced-motion styling, clipboard recovery,
  resilient WebSocket reconnect, and IME/composition-aware terminal input apply on both desktop
  and mobile.

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
- `frontend/src/ProviderAccounts.tsx`
- `frontend/src/ResourceUsage.tsx`
- `frontend/src/TerminalPane.tsx`
- `frontend/src/ProjectRunMenu.tsx`
- `frontend/src/style.css`
