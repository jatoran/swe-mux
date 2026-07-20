# Browser interaction model

## What it is

The Project-first browser workspace: grouped sidebar navigation and one unified tab/pane
layout for terminals, previews, notes, file browsers, and file editors.

## Sidebar

- Desktop app chrome keeps `swe_mux`, sidebar collapse, and daemon activity in the persistent
  sidebar-column identity row. Workspace tabs are not mirrored into that row or a global rail;
  each pane renders its own header tab strip.
- The desktop sidebar is pointer-resizable from 190–480 px and collapsible from the top rail.
  Width and collapse state are device-local browser preferences, not Project layout state.
- Projects are the top-level session containers. The sidebar shows only Projects marked for
  active navigation; each visible Project row exposes its fixed project note and file browser,
  followed by its sessions/layout tree. The viewport-level Projects manager lists every
  configured Project and can add, edit, open, hide/show, or delete registrations without
  discarding hidden Projects' notes, files, settings, history, or layouts.
- Optional named Groups wrap Project rows and organize the Projects manager. Groups never
  alter panes or Project ownership.
- A Project must exist before terminal commands become available. The empty state opens the
  Projects manager to create the first Project or show an existing hidden Project.
- Sessions cannot move between Projects. A terminal may navigate elsewhere without changing
  its sidebar location or resources.
- Git status remains session/project metadata. Worktrees have no first-class sidebar row,
  tab, creation modal, or context action.

## Unified workspace layout

- Each Project persists one recursive split tree whose leaves are tab panes. Every pane can
  mix terminal, preview, note, Files, and file-editor tabs, and every view can be moved to
  another pane or split from any pane edge.
- A new Project presents one empty pane. New terminals open as tabs in the focused pane by
  default; explicit split commands create a pane to the right or below. The row and tab appear
  immediately as a non-persisted starting placeholder, then resolve to the daemon identity or
  disappear with an inline error. All terminals start at the Project root.
- Every pane tab has a fixed-width close cell, so changing its icon never shifts the label or
  neighboring tabs. Notes, Files, file editors, and previews close their viewport on one click.
  A live terminal/session requires two clicks: its `×` becomes `✓` in place, and the second
  click kills the terminal and removes its session. Ended sessions use the same confirmation
  before dismissal. The confirmation expires if the second click does not arrive promptly.
- Session menus are origin-aware. A tab's context menu omits actions that require a separate
  focused terminal. Deprecated detach/remove-from-group, Project-note, pane-swap, and pane-header
  minimize/close actions are absent because the tab rail owns activation, drag placement, and
  close controls. Sidebar and pane-header menus retain only source-appropriate actions.
- Context-menu split and move actions use shared non-clickable `Open in split:`, `New terminal
  in split:`, and `Move tab:` labels with individual directional arrow buttons. Only directions
  supported by the current split tree are enabled, and the same model applies to terminal,
  preview, note, Files, and file-editor tabs.
- Pane focus, tab order, zoom, and adjacent-pane commands apply to every view kind.
  `Ctrl+Alt+1..9` switches among Projects currently shown in the sidebar.
- Every split branch owns an independent tab strip. A right split gives the left and right
  panes separate strips; a down split does the same for the upper and lower panes. Dragging
  within a strip reorders; dragging across panes moves the view; dropping on an edge splits.

## Project resources

- Project note, file browser, and opened files are ordinary tabs in the unified Project
  workspace. There is no separate resource dock or pop-out presentation mode.
- The file browser expands directories in place as a lazy tree; file selection opens a
  closable editor tab. Project notes autosave after a short idle debounce. Text-file saves
  require the loaded revision; binary and oversized files report read-only states.
- `Tab` inserts a literal tab in note/file editors. Note soft-wraps retain the source
  line's leading indentation; `Enter` carries that indentation onto the next line. Global
  and project-local ignore lists configure tree visibility and the bounded open-resource
  watcher.
- Note-editor creation and controlled-value reconciliation are layout-synchronous. Passive
  reconciliation may overwrite the first browser edit with a stale prop and lock the UI.
- Project-note actions always open the owning Project note. There are no session, agent-run,
  Group, or app-owned notes.

## Interaction and accessibility

- Context menus and modals use inline confirmation rather than native browser dialogs.
- Project title rows and all workspace tabs are directly draggable. Each Project Group and
  tab strip is one continuous reorder surface: gaps, nested Project content, the dragged item's old
  position, strip spacers, and outer edges resolve to the nearest insertion slot. Hover previews
  insertion by shifting items in place; hovering a pane edge previews a new split. A labeled ghost
  follows the pointer, and Escape restores the pre-drag order without saving.
  Visual reordering keeps the browser's native drag-source node mounted; drop commits the latest
  previewed order, including a final hover that has not rendered yet. Markers span the pending block
  or tab boundary.
- Settings edits remain local drafts until explicit Save and never change the active settings
  section. The footer exposes dirty/saving/saved state. Close, Escape, backdrop clicks, and
  navigation out of Settings require an in-app save/discard decision when drafts are dirty;
  interacting with that decision cannot trigger the Settings backdrop close path.
- Focus is device-local and URL-addressable by Project/session. Reload prefers a valid URL
  target, remembered focus, then a visible fallback.
- Mobile uses the same Project/session and unified-workspace model, one focused view,
  resilient reconnect, and composition-aware terminal input. Its top row contains navigation,
  the centered active Project name, and provider accounts. The focused pane's real tab strip
  sits directly under that row as one non-wrapping, horizontally scrollable touch surface;
  mobile has no separate session dropdown.
- Modal focus trapping, keyboard navigation, reduced-motion styling, clipboard recovery,
  and terminal replay contracts remain shared across desktop and mobile.

## Provider accounts

- Sidebar and Settings show live daemon-host system auth, not a remembered UI preference.
- Desktop provider status sits at the bottom of the sidebar in separate Claude and Codex
  rows. Terminal-style `✳` and `⌬` marks replace provider names while accessible labels and
  tooltips retain explicit identity; each row shows current account, quota percentage,
  compact reset countdown, and state. The account switcher renders in the viewport layer,
  independent of sidebar clipping.
- Each provider visibly reports a saved account, external/unsaved login, signed-out state, or
  unreadable credentials. External identity metadata appears when the provider auth format
  exposes it.
- Account selection is the only UI action that materializes a saved snapshot into system auth.
  Startup and passive quota refresh never restore an unrelated saved login.
- A confirmed unexpected reset displays a purple account indicator. Optional sound is a
  browser-local, default-off preference deduplicated by reset identity.

## Operational evidence

- The Process inspector labels active, exited, escaped, suspected-orphan, stale, and
  inaccessible evidence with reason/confidence, first/last seen, Job Object result, command
  fingerprint, and exit evidence. Every process action revalidates the identity first.
- Bottom-sidebar owned-resource status reports combined daemon and fleet CPU/RSS from the
  cached process sample. Its anchored viewport popover groups session attribution by Project,
  separates daemon/infrastructure usage, and opens Process fleet on request.
- Usage navigation includes Historical, Quota + resets, Tools + skills, and Context +
  compaction. Quota attribution is explicitly probabilistic and preserves an external
  remainder; compaction/skill counts state their explicit-evidence requirement.
- Settings owns process cadence/grace/retention, telemetry retention, scheduled quota polling,
  and optional rate-limited root-turn refresh.

## Automation dashboard

- Primary navigation follows user outcomes: Overview, Automations, Attention, Run notes,
  All-session health, Learned fixes, and Diagnostics.
- Overview defines observer, run note, attention, all-session health, and learned fix before
  exposing provider or execution detail.
- Automations presents built-in system observers and custom rules together. Built-ins toggle
  their config-backed setting inline; custom rules retain enable/shadow controls.
- All-session health explains passive fleet signals before telemetry. Learned fixes explains
  the experience index before reviewed batch creation.
- Event dry-run, firing/action/call traces, provider diagnostics, and injection research stay
  in Diagnostics.

## Key files

- `frontend/src/App.tsx`
- `frontend/src/layout.ts`
- `frontend/src/dragReorder.ts`
- `frontend/src/ProjectResource.tsx`
- `frontend/src/ProjectNoteEditor.tsx`
- `frontend/src/TerminalPane.tsx`
- `frontend/src/AutomationDashboard.tsx`
- `frontend/src/ProviderAccounts.tsx`
- `frontend/src/ProcessPanel.tsx`
- `frontend/src/UsageDashboard.tsx`
