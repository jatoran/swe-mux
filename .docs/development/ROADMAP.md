# swe-mux roadmap

## Purpose

This roadmap tracks work still required to complete `AGENT_MUX_SPEC.md`, plus the
approved shell-profile, Settings, project-local workspace, mobile-development, and
cross-OS additions. It is a gap plan against the current implementation, not a
restatement of already-delivered behavior.

Checkboxes are completion records. A phase is complete only when its exit criteria
pass in automated tests and the relevant design/interface docs describe the shipped
behavior.

## Product decisions that amend the original spec

These decisions are authoritative and must not be reverted while completing older
spec language:

- Spaces and sessions share the sidebar tree; no space-tab strip in the top bar.
- Git branch/dirty status appears in pane headers, not sidebar rows.
- Pane headers are the sole location for session cwd and Git status; the global top bar
  shows workspace identity only and never repeats cwd/session state.
- The default dark UI uses neutral near-black fills: no green background fills,
  main-stage grid, scanline texture, or decorative blinking cursor. The daemon is an
  icon-only status light with hover/accessible detail. Future selected themes may replace
  semantic colors without restoring texture.
- The sidebar has no persistent New Terminal row or `+` control. Creation begins from a
  workspace context menu, `: menu`, command palette, or hotkey.
- `New terminal` immediately creates the configured default shell in the current
  space and cwd. It does not open a backend picker and does not create a split.
- `New terminal custom…` is the explicit route for choosing another shell profile
  and/or cwd. It uses a compact keyboard-operable launcher, not a Claude/Codex tile
  modal.
- Claude and Codex start by running their normal commands inside a shell; the shell
  session promotes in place when the agent is detected.
- Programmatic API/CLI `backend=claude|codex` spawn and native history resume remain
  supported through their adapters. Shell profiles apply only to shell sessions and are
  mutually exclusive with direct agent backends.
- Session History is agent-only. Plain shells are not retained or shown unless the shell
  promotes in place to Claude or Codex. A history “project” is organizational metadata
  derived from repository/cwd identity, not a return of the retired orchestration Project
  model.
- Project-specific swe-mux state belongs inside `.swe-mux/` at its recorded project-scope
  root: the current Git worktree root when available, otherwise the normalized
  non-repository scope root. This includes project overrides, notes, and future
  project-owned metadata. Machine-wide daemon/runtime configuration remains under the
  user's mux data directory.
- Project scope and space are independent dimensions. A project scope is one concrete
  worktree/filesystem root that owns `.swe-mux/`; a space is a workflow/layout group that
  may contain sessions from multiple project scopes. Repository groups exist only for
  history/display and never own behavior, notes, config, hooks, or space anchors.
- Every session resolves one immutable project scope at spawn. Spaces carry an optional,
  explicit anchor used for defaults and future artifact placement, never as a membership
  constraint. Every project-resident artifact records its owning scope at creation and
  does not follow later anchor changes.
- Projects remain durable and browsable after their spaces/sessions close. Every recognized
  `.swe-mux/` artifact is reachable from a Projects view as linked, detached, unlinked, or
  conflicting; closing runtime/workflow objects never makes project files invisible.
- Repository files execute nothing. Project-scoped hooks in the current product are
  machine-owned `~/.mux/hooks.toml` rules matched against session project scope; repository-
  owned executable rules and their trust store remain unscheduled.
- Notes are human-readable Markdown files under `.swe-mux/notes/`, not opaque SQLite note
  bodies or agent transcripts. Space notes are primary; optional session annotations link
  to a space/project and use stable filenames. Notes never enter agent context implicitly.
- swe-mux does not manage Claude or Codex accounts, credentials, account-specific config
  roots, quota failover, or account switching. Reauthentication remains external to mux;
  all discovered Claude/Codex history remains intermingled for the one local user.
- swe-mux themes apply to its chrome and terminal emulator only. Native Claude/Codex theme
  management, theme-file generation, and ANSI-output rewriting are out of scope.
- Native Claude/Codex remote-control products are unrelated integrations and remain out of
  scope. swe-mux remote access is its own browser/API/CLI surface.
- Supported network topology is localhost plus direct tailnet: `muxd` listens on localhost
  and the detected Tailscale IPv4 by default. Tailscale supplies transport encryption and
  policy; swe-mux does not add a second login. Serve is optional HTTPS for browser secure-
  context features. Direct LAN binding, `0.0.0.0`, Funnel, and public exposure are unsupported.
- Splits are explicit actions only. Ordinary terminal creation replaces or fills the
  focused pane while the displaced session remains live in the sidebar.
- A session is one atomic process; panes and tabs are viewports over sessions. Sessions
  never contain other sessions. Horizontal split, vertical split, and tab stack are layout
  container types with no effect on session identity, lifecycle, hooks, or history.
- The sidebar must make layout membership explicit. Sessions in a split or tab stack appear
  once beneath that layout hierarchy; live sessions absent from the current layout appear
  in a distinct unpaned group. A flat list that makes grouped sessions look unrelated is
  not an acceptable presentation.
- Browser `alert`, `confirm`, and `prompt` dialogs are prohibited. Destructive toolbar
  actions use inline two-click confirmation; explicit context-menu Kill executes
  immediately.
- All UI chrome and terminal text share one monospace face, size, and weight matching
  xterm.
  Menus, hints, headings, inputs, history, and settings have no typography exceptions.
- Windows is the product-proving platform. Phase 5.5 first corrects project/space ownership;
  Phase 6 then improves Windows capability, diagnostics, automation, and reliability before
  platform expansion or a public release. Deferred priority is Telegram first, then
  SSH/native attach, native Linux/macOS support, and public packaging/release.
- The browser remains the primary product surface. `mux` is a practical controller for
  automation, diagnostics, and daemon actions; CLI parity covers useful control-plane
  operations, not presentation-only browser interactions.

## Implemented baseline

The current application already provides the foundation this roadmap extends:

- Windows ConPTY session ownership, Win32 job reaping, bounded scrollback, replay,
  resize, multi-browser input ownership, and daemon/session lifecycle.
- Shell, Claude, and Codex adapters; in-place agent promotion; hooks and transcript
  observation; agent state/context display.
- Persistent space records/layout membership, history/events SQLite index, external history
  reconciliation, transcript viewing/resume, Git polling/worktrees, and meta-hooks.
- Credential-free localhost/tailnet HTTP/WS surfaces, `mux` CLI basics, sidebar
  spaces/sessions, up to four
  panes, explicit splits, detach/zoom, broadcast, palette/keybindings, responsive
  drawer, terminal-aware clipboard, and mobile Paste.

## Delivery order

```text
Completed foundation: Phases 0-5
  -> Phase 5.5: project scopes + space anchors + artifact ownership + layout clarity
    -> Phase 6: Windows product maturity + practical CLI/diagnostics

Deferred queue:
  Phase 7: Telegram
    -> Phase 8: SSH/native attach
      -> Phase 9: WSL agent bridge + native Linux/macOS
        -> Phase 10: public packaging and release
```

Cross-cutting test work ships with every phase; it is not deferred to the end.

## Phase 0 — Runtime contracts and reliability

### Adapter and state boundaries

- [x] Complete the backend-adapter protocol: structured spawn/resume specification,
  parser creation, transcript association, hook setup/cleanup, and graceful exit.
- [x] Remove Claude/Codex path, schema, and backend-name branching from
  `SessionManager`; keep backend-specific knowledge inside adapters.
- [x] Replace `(application, quoted command_line)` with structured
  `SpawnSpec { executable, argv, env }`; platform PTY implementations own quoting.
- [x] Centralize agent transitions in one StateMachine with explicit source priority:
  hook > transcript > PTY liveness.
- [x] Deduplicate semantic turn/tool/approval events and reject stale or contradictory
  lower-priority transitions.
- [x] Generate per-session hook configuration atomically; clean temporary settings and
  secrets after exit.
- [x] Make Codex native-id/transcript association deterministic; retain cwd/time matching
  only as a bounded fallback.
- [x] Add versioned Claude/Codex transcript fixtures and adapter contract tests.

### Session, scrollback, WebSocket, and persistence correctness

- [x] Retain exactly the last configured N scrollback bytes, including oversized chunks.
- [x] Define queue overflow behavior: account for dropped output and send a gap/resync
  control frame instead of silently losing data.
- [x] Send live state/update/exit control frames on each PTY WebSocket; preserve attach
  order: state -> replay start -> replay bytes -> replay end -> live data.
- [x] Derive xterm scrollback capacity from daemon configuration or expose an explicit
  documented byte/line policy.
- [x] Persist rename, move, pin, effective executable/argv, and final session metadata
  into history transactionally. Profile identity is added with the Phase 2 model.
- [x] Define ended-session registry behavior so exited records do not retain references
  to deleted spaces.
- [x] Validate and version persisted layout schemas; add optimistic revision checks for
  concurrent browser updates.
- [x] Implement atomic close-space disposition: move contained sessions to a selected
  space or kill them, while preserving the default space.

### Phase 0 exit criteria

- [x] Race tests prove lower-priority transcript data cannot overwrite a newer hook state.
- [x] Replay/live ordering, queue-gap recovery, exact scrollback bounds, and input-owner
  behavior pass API/WS integration tests.
- [x] Session metadata and layouts survive browser reloads and daemon restarts where the
  product promises persistence; live PTYs still intentionally do not survive daemon exit.
- [x] Forced daemon termination leaves no Windows child process.

## Phase 1 — Configuration service and Settings

### Versioned configuration service

- [x] Replace ad-hoc TOML loading with a typed, versioned schema and migrations.
- [x] Migrate existing `shell_exe`, backend executable paths, token, and other current
  values without losing user configuration.
- [x] Validate before mutation; write through a temporary file and atomic replace; keep
  the previous valid configuration on parse or validation failure.
- [x] Preserve unknown fields/comments where practical; otherwise document canonical
  rewrites and create a backup during migration.
- [x] Separate public settings from secrets. Ordinary config reads never return the
  bearer token or per-session hook secrets.
- [x] Define the shell-profile configuration schema and migration envelope used by Phase
  2, without exposing profile selection before spawn/runtime support exists.
- [x] Define and validate one typed spawn contract before UI/CLI expansion:
  `backend`, `profile_id`, raw `executable/argv`, cwd/space defaults, and optional
  worktree request have explicit precedence and mutual-exclusion rules. Profiles are
  shell-only; direct agent backends use adapters.
- [x] Add config read/update/reset endpoints with field-level errors and
  `restart_required` reporting. Host, port, and data directory require restart; safe
  runtime fields hot-apply.
- [x] Add config revisions/ETags; reject or explicitly merge stale browser saves and
  external-file edits instead of silently overwriting them.
- [x] Add read/write/validate endpoints for keybindings and hooks using the same atomic,
  last-known-good rules.
- [x] Wire currently persisted space defaults into spawning with explicit precedence:
  request override -> space default -> global default -> daemon cwd.
- [x] Emit configuration-changed and configuration-error events for UI diagnostics.

### Settings page

- [x] Add a Settings route/surface reachable from `: menu` and the command palette.
- [x] Use the established terminal/TUI visual system and the single global typography
  token; no native browser prompts or separate design language.
- [x] Provide sections:
  - General: startup cwd, default space behavior, history/scrollback limits.
  - Terminals: non-profile terminal defaults initially; the profile editor, global
    profile default, and per-space profile defaults become active in Phase 2.
  - Agents: Claude/Codex executable paths, default args, observation/reconciliation.
  - Input: keybindings, middle-click paste, broadcast defaults, clipboard behavior.
  - Git and history: polling cadence, history reconciliation and retention controls.
  - Hooks and notifications: hooks editor/validation, channels, diagnostics.
  - Remote and security: localhost/tailnet listener control and status, plus optional
    Tailscale Serve guidance.
  - Appearance: one shared font/size/weight control applied uniformly if customization
    is exposed; never per-component typography.
- [x] Show saved, hot-applied, restart-required, invalid, and externally-modified states.
- [x] Support restore defaults, export sanitized config, and reveal config directory.
- [x] Make the entire surface keyboard-operable with focus trap/restore and inline errors.

### Theme system

- [x] Define one semantic color-token contract shared by UI chrome, state colors, xterm
  foreground/background/cursor/selection, and the ANSI palette.
- [x] Ship built-in themes: Light, Dark, System, Solarized Dark, and Tokyo Night.
- [x] `System` follows OS/browser color-scheme changes live; explicit selections remain
  stable until the user changes them.
- [x] Add theme selection, live preview, cancel/revert, and persistence to Settings.
- [x] Support custom themes with validated token editing plus import/export. Invalid or
  incomplete themes fall back safely without making terminal text unreadable.
- [x] Preserve the no-texture terminal aesthetic unless a future explicit setting adds
  one; themes change semantic colors, not layout or typography.
- [x] Test contrast, focus, selection, agent states, warnings/errors, and ANSI readability
  for every built-in theme in both UI chrome and live terminals.

### Phase 1 exit criteria

- [x] Invalid settings never alter disk or runtime state.
- [x] Safe Phase 1 settings affect the next relevant operation without daemon restart;
  bind changes clearly remain pending until restart.
- [x] Reloading the browser and daemon reproduces saved settings.
- [x] Every built-in theme applies atomically to chrome and existing xterm instances;
  System reacts live and custom-theme validation prevents unusable combinations.
- [x] Ordinary API responses, logs, exported settings, and browser URLs contain no token.

## Phase 2 — Shell profiles and terminal creation

### Profile model

- [x] Implement the Phase 1 profile schema throughout spawn/runtime/history and replace
  the singleton PowerShell-specific shell setting with ordered profiles:

```toml
default_shell_profile = "powershell"

[[shell_profiles]]
id = "powershell"
label = "Windows PowerShell"
executable = "powershell.exe"
args = ["-NoLogo"]

[[shell_profiles]]
id = "cmd"
label = "Command Prompt"
executable = "cmd.exe"
args = ["/Q"]

[[shell_profiles]]
id = "ubuntu"
label = "WSL: Ubuntu"
executable = "wsl.exe"
args = ["--distribution", "Ubuntu"]
```

- [x] Profile fields: stable id, label, executable, argv, optional environment overrides,
  platform/capability constraints, cwd strategy, icon/short marker, and enabled state.
- [x] Never inject PowerShell `-NoLogo` globally; every argument belongs to its profile.
- [x] Store `shell_profile_id` and effective executable/argv on live sessions and history.
- [x] Retain raw executable/argv as an advanced API escape hatch with explicit validation;
  normal UI and CLI flows use profile ids.
- [x] Detect available shells and offer presets without silently changing the user's
  selected default.
- [x] Ship/verify Windows PowerShell, PowerShell 7, CMD, and installed WSL distro presets.
  Add bash, zsh, platform login shell, and PowerShell presets during Unix rollout.

### Default and custom creation UX

- [x] `New terminal` and `Ctrl+Alt+T` immediately create the resolved default profile in
  the active space/cwd with no dialog and no split.
- [x] Add `New terminal custom…` to workspace/session context menus, `: menu`, and command
  palette.
- [x] The custom launcher chooses profile and cwd from recent, active, pinned, typed, or
  browsed directories; it is compact, searchable, and keyboard-operable. Absolute-path
  browsing is daemon-backed with platform roots, permission/error handling, and clear
  semantics when the browser is remote.
- [x] Add persistent pin/unpin management for favorite directories.
- [x] Remember the most recent custom profile/cwd without replacing the configured default.
- [x] Add global default profile and per-space default profile/cwd controls in Settings.
- [x] Enable the full Settings profile editor: add, duplicate, reorder, enable/disable,
  validate, delete, select defaults, and restore detected presets.
- [x] Support explicit `New terminal custom in split…` only from split-capable pane/session
  actions; custom creation otherwise follows normal replacement behavior.
- [x] Validate executable/profile/cwd before replacing a pane; failures leave the current
  pane intact and show an actionable inline error.
- [x] Add API `profile_id` selection, profile listing/capabilities, and CLI
  `mux profiles` / `mux spawn --profile <id>`.
- [x] Contract-test direct `backend=claude|codex` API/CLI spawn, shell-first UI promotion,
  and adapter-native history resume as distinct supported paths.

### WSL delivery

- [x] Stage A: interactive WSL profiles open the selected distro and translate Windows
  cwd to a valid distro cwd using `wsl.exe --cd` or a tested equivalent.
- Deferred to Phase 9 — Stage B: install/use a distro-side mux bridge for Claude/Codex
  promotion, hooks,
  secrets, and transcript access. A Windows `.cmd` shim alone cannot observe Linux-side
  agents.
  - Current Ubuntu probe: no native Claude; `codex` resolves to the Windows npm install
    under `/mnt/c`. The shipped WSL preset therefore remains correctly capability-gated
    and cannot satisfy bridge/promotion acceptance without installing native distro agents.
- [x] Surface profile capabilities until Stage B ships so an interactive WSL shell is not
  incorrectly presented as agent-aware.

### Phase 2 exit criteria

- [x] PowerShell, pwsh, CMD, and WSL profiles start interactively with correct arguments,
  cwd, resize, Unicode, clipboard, and exit behavior.
- [x] Default creation is zero-dialog/non-split; custom creation starts exactly the chosen
  profile/cwd and persists its identity.
- Deferred to Phase 9 — WSL Claude and Codex promotion/state tests must pass before WSL
  profiles are labelled
  agent-aware.

## Phase 3 — Commands, panes, terminal input, and interaction parity

### Unified command system

- [x] Give every global/menu UI operation a stable command id, label, availability
  predicate, and optional default binding.
- [x] Make the palette fuzzy-search the complete registry and support arrow selection,
  Enter, Escape, disabled explanations, and current binding display.
- [x] Centralize terminal key interception in one allowlisted router. Disabled, unknown,
  and unmatched chords always reach the PTY.
- [x] Complete defaults for pane navigation/split/zoom, space switching, terminal find,
  Settings, custom terminal creation, and palette.
- [x] Add a Settings keybinding editor with conflict validation and documented unbindable
  browser chords such as Ctrl+W/T/N.
- [x] Reject unmodified keys, plain typing, and modifier combinations that shadow normal
  terminal input; show the exact validation reason.

### Real pane layout

- [x] Replace flat pane membership/fixed CSS grids with a versioned persisted recursive
  tiling tree: typed leaf plus horizontal/vertical split nodes and ratios. Terminal leaves
  carry a session id; the schema reserves note and preview leaves so later workspace
  surfaces do not require another layout-model replacement.
- [x] Add split horizontal/vertical, attach existing session, detach, move/swap panes,
  drag dividers, and temporary zoom.
- [x] Remove the arbitrary four-pane cap or replace it with a documented configurable
  safety limit.
- [x] Empty leaves offer attach existing session, create default terminal, or create
  custom terminal; no Claude/Codex backend tiles.
- [x] Preserve layout through browser reconnect and daemon restart; reconcile missing or
  exited session leaves predictably.
- [x] Use layout revisions across browsers: stale split/drag/move writes conflict or
  rebase instead of overwriting another client's tree.

### Context and inline interaction completion

- [x] Session menu: pin/unpin attention, resume exited as new, attach/open, directional
  split, custom terminal actions, and current rename/move/broadcast/copy/reveal/worktree/kill.
- [x] Pane menu: clipboard actions plus directional split, detach, zoom, and kill.
- [x] Space menu: Settings for defaults and close-space inline choice to move or kill
  contained sessions.
- [x] Replace remaining native prompts for rename, worktree creation, terminal search,
  and token login with inline editors/panels.
- [x] Keep all menus viewport-bound, Escape/outside-click dismissible, and keyboard
  navigable with focus restoration.

### Terminal input and find

- [x] Add an inline xterm search widget with next/previous, case option, result feedback,
  and close/focus restoration.
- [x] Verify Ctrl+C selection-copy vs SIGINT, Ctrl+Shift+C/V, OSC-52, Select All, Clear,
  and optional middle-click paste. Plain Ctrl+V must paste in shell, Claude, and Codex
  through `term.paste()`/native xterm semantics, preserve bracketed paste, never raw-write
  clipboard contents, and never silently swallow a denied clipboard request.
- [x] Define Ctrl+Shift+C with no selection so the chord falls through or reports its
  unavailable command instead of disappearing.
- [x] Define clipboard-media routing separately from text paste. A user-initiated image
  paste or mobile Paste Image action detects supported clipboard image types, invokes a
  typed upload/handoff contract, and inserts a daemon-local file reference through the
  active backend adapter. It never sends encoded image bytes through the PTY or changes
  ordinary text/bracketed-paste behavior.
- [x] Add router/clipboard regression coverage for PowerShell, CMD, pwsh, WSL, Claude,
  and Codex. Broader Windows PTY smoke coverage remains in Phase 6's matrix.

### Phase 3 exit criteria

- [x] Every global/menu action can run from the command system and is keyboard accessible;
  form-local editor controls remain local by design.
- [x] Arbitrary split trees round-trip through persistence; divider drag, swap, detach,
  zoom, and replacement preserve live sessions.
- [x] Normal New Terminal changes only the focused leaf and leaves its displaced session
  live/unpaned; concurrent-client layout mutations cannot silently clobber each other.
- [x] No browser-native dialog is reachable in normal operation.
- [x] PTY input transparency tests prove ordinary keys and disabled chords are never lost.
- [x] Text and image clipboard paths are distinguishable, keyboard/touch accessible, and
  fail visibly without corrupting the terminal composer or leaking clipboard contents.

## Phase 4 — History, events, agents, hooks, Git, and responsive completion

### History and events

- [x] Make history agent-only. A new shell may have an internal provisional lifecycle
  record, but it is invisible through History and is deleted on exit unless the session
  promotes to Claude or Codex. Direct agent sessions are visible immediately.
- [x] Migrate existing data so ordinary `backend=shell` entries no longer appear and are
  removed when they have no agent/native transcript relationship.
- [x] On in-place promotion, atomically convert the provisional shell lifecycle into one
  Claude/Codex history entry while preserving original start time, cwd, mux id, and native
  id. Never create duplicate shell + agent entries for one process lifetime.
- [x] Add indexed/paginated history search with backend, project, state, date, space, and
  external filters plus explicit loading/error/empty states. Backend choices are Claude
  and Codex only.
- [x] Add history-project identity and metadata. Prefer Git common repository/remote
  identity so worktrees group together; fall back to normalized cwd; keep an explicit
  Ungrouped bucket when identity is unavailable.
- [x] Add project list/group/filter APIs and a collapsible project -> agent sessions UI,
  ordered by most recent activity. Support a friendly project label without introducing
  roles, leads, workloads, or other retired orchestration behavior.
- [x] Persist agent context summaries when observation data supports them: context-window
  size, final context used %, peak context used %, input/output token totals, model, and
  measurement source. Current-window use must not be confused with cumulative tokens.
- [x] Backfill context summaries from native Claude/Codex transcripts where reliable.
  Display `context unavailable` rather than estimating or showing a misleading zero when
  the backend/schema does not expose enough information.
- [x] Add safe index-entry deletion through API/UI inline confirmation; deleting an index
  entry never deletes or edits its native transcript.
- [x] Use direct history-id lookup for transcript/resume instead of bounded list scans.
- [x] Validate resumability and return typed errors for missing transcript, adapter,
  native id, cwd, or target space.
- [x] Show exit reason, final/peak context summary, token totals, model, external marker,
  and normalized event detail without exposing raw transcript implementation details by
  default.
- [x] Resume agents through adapter/native identity into a chosen space/pane. Update the
  target layout atomically so the new session is visibly attached; never offer or create
  shell-history resume and never mutate native transcripts.
- [x] Add monotonic event-sequence cursor pagination, stable ordering, retention policy,
  and WS reconnect catch-up with no gaps or duplicates.
- [x] Add mobile master-detail navigation for history and Settings.

### Project-local configuration and notes

- [x] Resolve the concrete project root from cwd for each session and project-file
  operation: current Git worktree root when available, otherwise normalized cwd. Never
  place project files in a Git common-dir or parent repository for a distinct worktree.
  Phase 5.5 replaces repeated cwd-based ownership with durable session scopes, optional
  space anchors, and artifact-owner records.
- [x] Define a versioned `.swe-mux/config.toml` containing only project-scoped fields.
  Establish precedence and diagnostics explicitly: request/session override -> space
  override -> project config -> global config -> daemon default. Machine auth, bind,
  secrets, data-directory, and unrelated user preferences are forbidden project fields.
  Space overrides remain in global SQLite; project defaults live in this project file.
  Phase 5.5 makes the scope used for each layer explicit and non-circular.
- [x] Store human-readable Markdown notes as
  `.swe-mux/notes/spaces/<stable-space-id>.md` and optional
  `.swe-mux/notes/sessions/<stable-session-or-history-id>.md`. Define safe filename
  mapping, metadata, rename/move behavior, orphan handling, and cleanup semantics without
  embedding note bodies in SQLite. Phase 5.5 adds durable owning-scope metadata so these
  paths remain unambiguous in mixed spaces and after re-anchoring.
- [x] Add atomic file replacement, optimistic revisions, external-edit detection, and
  conflict UI so browser autosave never silently overwrites editor/Git changes.
- [x] Add terminal-styled desktop/mobile raw Markdown editing, export,
  and explicit Insert selection into session / Capture terminal selection actions. Notes
  never enter a prompt or context window without a direct user action. Provide both a
  centered quick-editor modal and a persistent terminal + note split leaf, with dock/pop-out,
  draggable ratio, targeted capture routing, and mobile return-to-terminal behavior.
- [x] Surface missing, read-only, malformed, disabled, and conflicting `.swe-mux/` states
  without blocking terminal use. Phase 4's schema is data-only and rejects executable,
  secret, and parent/absolute-cwd fields; Phase 5 adds resolved-path and privileged-boundary
  enforcement.
  Creating `.swe-mux/` is an explicit first write, not a side effect of merely opening a
  project.

### Usage analytics

- [x] Add an optional adapter using one supported, version-pinned unified `ccusage` CLI
  for Claude and Codex. Validate the external schema, record tool version/provenance, and
  normalize aggregates without replacing native transcript observation as live truth.
- [x] Run usage refresh only on explicit user request or a configurable low-priority
  background schedule. Enforce one concurrent refresh, timeout/cancellation, cached
  last-known-good data, and visible stale/error/refreshing states; startup and terminal
  interaction never wait on ccusage.
- [x] Cache normalized daily/monthly/session/model/token/cost aggregates. Treat calculated
  cost and quota-window data as estimates, distinguish them from current context use, and
  provide disable/clear-cache controls.
- [x] Use fixture-driven adapter tests without invoking npm/npx in the normal test suite.
  Runtime package download is never implicit; Settings provides the pinned one-time global
  install command. Migrate the former split Claude/Codex commands to provider subcommands
  of the unified CLI and resolve Windows npm command shims safely.

### Process and preview awareness

- [x] Introduce per-session process ownership beneath the daemon reaper. On Windows use
  nested Job Objects plus PID/create-time reconciliation; retain kill-on-daemon-close
  behavior while attributing descendant start/exit/accounting to one mux session.
- [x] Expose a bounded process snapshot: pid/parent, executable label, start/exit, CPU,
  memory, and session ownership. Detect listening ports owned by descendants and correlate
  them with the originating session; never infer ownership from port number alone.
- [x] Add a process inspector with interrupt, terminate process, terminate tree, copy PID,
  and open detected preview. Label measurable conditions such as high CPU/memory or no PTY
  output; never auto-kill or assert that an idle server is hung.
- [x] Add explicit preview registrations tied to a session/space and a detected or
  user-approved loopback listener. Preview leaves support embedded best-effort rendering,
  refresh, full-screen mobile view, viewport presets, copy/open externally, and clear
  ownership/process status.
- [x] Keep Phase 4 preview rendering direct/best-effort with copy/open-external fallback
  and typed unavailable state. Phase 5 replaces that path with the bounded registered
  HTTP/WebSocket/HMR bridge without introducing general URL proxying.

### Agent observation and attention

- [x] Finish adapter/state priority work from Phase 0 for all supported Claude/Codex
  versions and record parser capability/diagnostic status.
- [x] Complete thresholded context usage, approval/tool detail, awaiting/crashed/exited
  ordering, pin behavior, title attention count, and accessible status announcements.
- [x] Keep sidebar rows free of cwd and Git duplication; pane header owns cwd and Git.

### Meta-hooks and notifications

- [x] Make malformed hook files non-fatal: retain last-known-good rules and surface
  diagnostics in Settings/events.
- [x] Validate template variables and action payloads; bound request/body sizes.
- [x] Add subprocess lifecycle, timeout, resource, and platform-shell policy for `run`.
- [x] Add timeout/status/retry policy for `http`; define burst/rate-limit semantics.
- [x] Keep UI notification delivery complete and add provider-neutral delivery records,
  correlation ids, reply targets, retry state, and sender/channel metadata needed by later
  external adapters. Telegram itself is deliberately deferred to Phase 7.

### Broadcast and Git

- [x] Move `send --all-broadcast` fanout server-side as one operation with membership and
  delivery events; define dead-session handling. Membership is daemon-memory state and
  intentionally resets with live sessions on daemon restart.
- [x] Test the targeting invariant: broadcast off writes only to the input owner;
  broadcast on mirrors once to each explicitly included live session, including detached
  targets, never duplicates source input, and always shows the warning banner.
- [x] Report detached Git HEAD by short SHA, not repository directory name.
- [x] Add Git subprocess timeout/cancellation/concurrency limits and parallel unique-root
  polling.
- [x] Validate worktree removal against the repository's actual worktree list before any
  mutation; keep worktree mutations explicitly user initiated.
- [x] Implement typed create-worktree-and-spawn semantics for API/CLI/UI, including target
  validation, spawned cwd, partial-failure reporting, and cleanup/retention policy.

### Responsive, touch, and accessibility

- [x] Add a focused-pane session switcher for narrow screens.
- [x] Add long-press context menus, touch-safe positioning, and >=44px targets when touch
  input is detected while retaining compact desktop density.
- [x] Add responsive contract coverage for sidebar, history, Settings, palette, custom
  launcher, notes, process inspector, previews, clipboard-image handoff, and terminal
  controls at the mobile breakpoint. Phase 6 adds real-browser portrait/landscape visual
  regression to this source-level contract suite.
- [x] Add semantic dialog/menu/listbox roles, labelled icon controls, focus trap/restore,
  aria-live errors/state, contrast checks, and reduced-motion support.

### Phase 4 exit criteria

- [x] History/filter/resume, hook reload/action failure, server broadcast, Git timeout, and
  worktree validation have API/integration coverage.
- [x] Plain shells never appear in History or persist after exit; a shell promoted to
  Claude/Codex appears exactly once under the correct project.
- [x] Worktrees of one repository group under one project; non-repository cwd sessions
  group predictably or appear under Ungrouped.
- [x] Known context usage displays tested final/peak percentages and provenance; missing
  data displays `context unavailable` without fabricated values.
- [x] Project notes round-trip as Markdown under the resolved `.swe-mux/` directory;
  concurrent/external edits conflict visibly, and no note is injected into an agent
  without an explicit user action.
- [x] ccusage refresh is optional, cached, non-blocking, manually refreshable, and tested
  against pinned JSON fixtures for both Claude and Codex.
- [x] Process ownership survives descendant churn, detected listeners map to the correct
  session, and process/preview actions cannot target another session accidentally.
- [x] Awaiting-agent attention is usable from another tab and by screen readers.
- [x] Core flows have keyboard-router, touch/mobile layout, semantic-role, focus, live-region,
  and reduced-motion contract coverage. Phase 6 retains full real-browser accessibility
  and orientation regression as release-quality validation.

## Phase 5 — Full mobile workspace over direct Tailscale access

Phase 5 is intentionally a single-user, private-tailnet deployment contract. The phone or
other admitted tailnet device must receive the same swe-mux terminal, session, process,
notes, and development-preview experience as localhost. Tailscale supplies device/user
admission and encrypted transport; swe-mux does not add login, bearer tokens, enterprise
audit infrastructure, or public-ingress machinery.

### Supported deployment topology

- [x] Bind `muxd` to localhost plus the detected Tailscale IPv4 by default. Local and
  tailnet browser/CLI clients connect directly; detection failure degrades to localhost,
  and Settings or `--local-only` can disable the tailnet listener.
- [x] Treat direct LAN binding, `0.0.0.0`, Tailscale Funnel, port forwarding, and public
  ingress as unsupported configurations. Fail closed or require an explicit development-
  only escape hatch carrying a prominent diagnostic; production behavior never silently
  falls back to a network-wide listener.
- [x] Remove the generic remote bearer/login product path, including query-string WS
  tokens, token prompts, rotation/revocation UI, and token-bearing browser URLs. Migrate
  legacy bind/token configuration with a clear diagnostic instead of preserving a second
  authentication system.
- [x] Detect and validate the Tailscale address, report the direct tailnet URL/listener
  state, and expose optional Serve status/setup for browser-recognized HTTPS. Distinguish
  Serve from Funnel and never silently modify tailnet policy or enable either feature.
- [x] Document a least-privilege Tailscale grant restricted to the owning user/devices and
  the swe-mux host/service. Document device removal/revocation and the consequence that an
  admitted tailnet peer with access to swe-mux has terminal/code-execution authority.

### Browser, project, and privileged-operation boundaries

- [x] Validate `Host` and browser `Origin` for every mutating HTTP request and WebSocket
  upgrade. Allow only localhost, validated Tailscale IP, and full tailnet DNS origins;
  reject cross-site browser control even when the browser's device belongs to the tailnet.
- [x] Apply secure response headers plus targeted size, duration, and concurrency bounds
  to expensive or upload/proxy routes. Do not add blanket per-user rate limiting to this
  single-user tailnet application. Record important privileged actions through the existing
  EventBus using metadata only; never record hook secrets, uploaded media, prompt contents,
  or terminal bytes.
- [x] Harden hook ingress independently of browser access: loopback peer only, constant-
  time per-session secret comparison, event allowlist, body/rate limits, and rejection
  after session expiry.
- [x] Define project-config trust boundaries. Opening a repository may read and display
  `.swe-mux/` state, but project files cannot define executables, hooks/commands, exposed
  ports, network policy, or secrets. A project may select only an existing machine-defined
  shell profile, and that profile runs only after an explicit New terminal action.
- [x] Harden per-session clipboard-media upload with user-gesture enforcement, MIME
  sniffing/allowlist, byte/count limits, randomized private paths, shell-safe/backend-safe
  path handoff, TTL/session cleanup, ownership checks, and metadata-only audit records.
- [x] Provide the full development-preview experience through the swe-mux origin. Proxy
  HTTP plus WebSocket/HMR traffic only to a detected session-owned loopback listener or an
  explicitly approved literal loopback URL. Preserve request paths, queries, subprotocols,
  and browser Origin behavior needed by common dev servers; bound concurrent bridges,
  response size, connection lifetime, and idle time. Sandbox embedded content, reject
  redirects or destination changes, and never expose raw development-server ports or offer
  a general network/URL proxy.
- [x] Keep every process and preview operation available from both localhost and direct
  tailnet clients through the same API/UI. A phone connects only to swe-mux; dev servers
  may remain safely bound to loopback on the workstation.

### Phase 5 exit criteria

- [x] Localhost and direct-tailnet browser/CLI access work without login, including
  terminal/event WebSocket reconnects, process management, and HTTP/WebSocket/HMR previews.
  Optional Serve preserves the same flows over HTTPS.
- [x] The daemon listener is unreachable from LAN peers and reachable only through
  localhost or the specific Tailscale interface; wildcard binding is never used.
- [x] Route-family Host/Origin/HTTP/WS tests pass for localhost, direct tailnet, and
  optional Serve-shaped origins; spoofed origin/host inputs fail closed.
- [x] No generic bearer token remains in URLs, config responses, logs, history, exports,
  browser storage, or hook payloads. Per-session hook secrets remain loopback-only and
  redacted.
- [x] Malicious project config, clipboard uploads, and preview destinations cannot cause
  command execution, cross-session access, filesystem escape, or arbitrary network
  proxying. Focused boundary tests cover each case.
- [x] Settings and the current lightweight `mux doctor` report local/tailnet listener
  health, direct URL, optional Serve availability, grant guidance, Funnel/public-exposure
  warnings, and actionable network errors without modifying tailnet policy. Phase 6
  expands `mux doctor` beyond this remote-status scope.

## Phase 5.5 — Project scopes, space anchors, and artifact ownership

Phase 4 shipped project-local config/notes by resolving a cwd at each request. That leaves
space notes and future project-resident space artifacts ambiguous when one space contains
sessions from multiple repositories. Phase 5.5 replaces implicit cwd routing with explicit
scope identities and ownership before more project-local systems are added.

This phase is corrective and precedes Phase 6. It also closes the layout-model visibility
gap by adding tab stacks and making split/stack membership explicit in the sidebar. It does
not turn projects into sidebar containers, constrain mixed spaces, move existing files
automatically, or introduce repository-owned executable rules.

### Scope identities and persistence

- [ ] Split the current overloaded project identity into:
  - `project_scopes`: stable id plus normalized concrete worktree/filesystem root and
    optional `repo_group_id`; one scope owns one `.swe-mux/` root.
  - `repo_groups`: Git common-directory/remote-derived history and display grouping only.
    Config, notes, hooks, anchors, sessions, and artifacts never reference a repo group for
    behavioral decisions.
- [ ] Create project-scope records only during session spawn, explicit anchor/artifact
  selection, migration, or another operation that needs durable identity. Directory
  listing and project inspection create neither a database identity nor `.swe-mux/` files.
- [ ] Add immutable `project_scope_id` to live sessions and history. Resolve it from the
  effective spawn cwd before process creation; shell `cd`, promotion/demotion, rename,
  space moves, and pane operations never change it. Cross-scope reassignment requires a
  new session.
- [ ] Add `anchor_mode: auto | fixed | none` and nullable
  `anchor_project_scope_id` to spaces with validated combinations:
  - `auto/null`: eligible for first successful spawn/default-cwd inference.
  - `auto/id`: inferred once and never re-inferred from later membership changes.
  - `fixed/id`: explicitly selected by the user.
  - `none/null`: explicitly unanchored and never inferred.
- [ ] Add durable ownership metadata for project-resident space artifacts. A space note
  records its `project_scope_id` when created; future artifact kinds use the same rule.
  Artifact ownership changes only through an explicit, conflict-safe Move operation.
- [ ] Preserve artifact records when their originating space or history relationship is
  removed. Store enough last-known type/id/label metadata to present a detached artifact;
  never cascade-delete project files or their only discoverability record.

### Deterministic spawn and configuration resolution

- [ ] Resolve session cwd in two non-circular phases:
  1. Seed cwd: request cwd → space default cwd → anchor scope root → global startup cwd →
     daemon cwd.
  2. Resolve `project_scope_id` from the seed, then final cwd: request cwd → space default
     cwd → resolved scope's relative project `default_cwd` → seed cwd.
- [ ] Keep project `default_cwd` as a bounded relative refinement inside an already-
  resolved scope. Reject absolute/parent traversal and symlink escape; if the configured
  directory is missing or invalid, use the seed cwd and emit a visible diagnostic rather
  than selecting another scope or failing terminal creation.
- [ ] Apply remaining spawn/config precedence as request overrides → space overrides →
  session-scope `.swe-mux/config.toml` → global config. Project shell defaults may select
  only an existing machine-defined profile and never define an executable or command.
- [ ] Enrich normalized events, history, process/preview records, notifications, and hook
  match fields with both `project_scope_id` and display-only `repo_group_id` where relevant.
  Behavior always selects the immutable session scope.

### Anchor lifecycle and migration

- [ ] In `auto` mode, resolve and store an anchor when an explicit space default cwd is
  saved or after the first successful session spawn into an empty `auto/null` space.
  Failed spawns and later mixed membership never alter the anchor.
- [ ] Space anchor changes affect defaults and placement of future artifacts only. They do
  not move sessions, change session scopes, retarget existing notes, or rewrite project
  files. Clearing an anchor sets `none/null`; choosing a scope sets `fixed/id`.
- [ ] Migrate existing SQLite state without moving project files or forcing choices:
  - Empty spaces → `auto/null`.
  - Spaces whose default cwd and existing session/history membership resolve to one scope
    → `auto/id`.
  - Mixed or conflicting spaces → `none/null`.
- [ ] Convert current history project identity into explicit scope plus repository-group
  fields while preserving filters, labels, worktree roots, native transcript associations,
  and agent-only history behavior.
- [ ] Discover existing space-note ownership only across known scopes. Exactly one matching
  file binds the artifact; multiple matches become a visible conflict; no match remains
  unbound until explicit note creation. Migration never copies, moves, deletes, or silently
  chooses between Markdown files.

### Notes and future project artifacts

- [ ] Create a space note in its current anchor scope and persist that ownership. Opening
  or saving the note uses artifact scope, not active cwd, active session, or current space
  anchor. Session/history notes use the immutable owning session/history scope.
- [ ] Opening notes on an unanchored space presents a blocking project-scope picker seeded
  from member-session scopes. No global scratch-note fallback exists; cancelling creates
  nothing.
- [ ] Re-anchoring a space with an existing note surfaces that the note belongs to another
  scope and offers explicit Keep, Move, and Copy actions:
  - Keep retains the canonical artifact and file in its original scope.
  - Move performs source/destination revision, collision, writable-root, symlink, and
    cross-volume checks; it updates ownership only after successful relocation.
  - Copy creates an explicitly separate file and does not silently change which artifact
    is canonical.
- [ ] Preserve external-edit conflicts, atomic writes, intentional orphan recovery, note
  size limits, disabled/read-only states, and the rule that layout removal never deletes
  Markdown.
- [ ] Make durable session notes agent-only. A live plain shell's note action redirects to
  its space note and explains why; once the shell promotes to Claude/Codex, its stable
  session/history identity may own a session note. Agent notes remain reachable from both
  history detail and the owning project after exit.
- [ ] Index pre-existing plain-shell notes and files whose recorded space/session no longer
  exists as unlinked or detached artifacts. Never hide, auto-delete, or guess a new owner.

### Projects registry and artifact discoverability

- [ ] Add a durable Projects view reachable from `: menu` and the command palette. It is a
  shelf for active and dormant project scopes, not a replacement sidebar hierarchy.
- [ ] List each scope with concrete root/worktree, repository group, last activity,
  active/dormant/missing-root state, anchor/space references, live sessions, agent history,
  project-config status, and artifact counts.
- [ ] Add project detail for configuration diagnostics plus every recognized supported
  `.swe-mux/` artifact. Classify artifacts as:
  - linked: owning live space or agent history exists;
  - detached: a recorded owner was closed/deleted;
  - unlinked: a supported file exists without a known artifact relationship;
  - conflicting: multiple files/records claim one canonical identity.
- [ ] Build a bounded, symlink-safe inventory of supported `.swe-mux/` config/note paths
  inside known scope roots. The filesystem remains authoritative for user-authored files;
  the index accelerates discovery and records ownership but never treats an absent row as
  permission to hide or delete a recognized file.
- [ ] Add project hide/unhide for recent-list hygiene. Add explicit Forget only when no
  live session, history, anchor, or artifact record references the scope; otherwise return
  typed blockers. Forget removes machine-owned registry/cache state only and never deletes
  `.swe-mux/`, Git content, native transcripts, or user notes. Rediscovery is allowed.
- [ ] Keep missing/offline roots visible with diagnostics while referenced. A missing path
  cannot silently collapse into another scope or make its indexed artifacts appear owned
  by a replacement directory.

### Layout stacks and sidebar hierarchy

- [ ] Preserve the process/view boundary: a session remains one independently addressable,
  killable, movable, observable process; a pane leaf is its viewport. No API, label, or UI
  action may imply that a session owns another session.
- [ ] Extend the versioned recursive layout schema with a stable-id `stack` container. A
  stack owns ordered terminal leaves plus `active_child_id`, occupies one tiled region, and
  displays one active child at a time. Stacks compose inside horizontal/vertical splits;
  the initial stack contract does not require a tab to contain another split subtree.
- [ ] Add tab-group operations: create from selected/paned sessions, add a new or existing
  session, activate, reorder, detach, move into a split, and dissolve when fewer than two
  children remain. All mutations use existing optimistic layout revisions.
- [ ] Keep lifecycle actions unambiguous. `Remove from tab group` and `Detach from layout`
  leave the session running and visible as unpaned; `Kill session` terminates it and removes
  every layout reference. Do not put an ambiguous close control on a tab.
- [ ] Render each space in the sidebar as a clear presentation hierarchy:
  - current layout group, with collapsible horizontal-split, vertical-split, and tab-stack
    rows that mirror the persisted layout tree;
  - each paned session exactly once beneath its containing layout node, in visual order;
  - a distinct `unpaned` group containing live sessions absent from the layout tree.
- [ ] Give layout rows compact terminal-style glyphs/labels, connector lines, child counts,
  and stable expansion state. Do not repeat cwd, Git, PID, or other pane-header metadata.
  A split pair and an unrelated third session must be distinguishable without opening or
  focusing any terminal.
- [ ] Make sidebar and stage navigation bidirectional. Clicking a paned session activates
  all ancestor stack tabs, focuses its pane, and reveals it; focusing a pane highlights and
  reveals its one sidebar row. Clicking an unpaned session follows the existing attach/
  replacement behavior and updates layout grouping atomically.
- [ ] Expose the canonical session context menu from every terminal pane header. Right-
  clicking the session title, status, agent indicator, or otherwise non-interactive header
  surface opens the same session-scoped commands, ordering, disabled explanations, and
  inline confirmations as right-clicking that session's sidebar row. Right-clicking the
  terminal body retains its pane/clipboard context menu.
- [ ] Add a visible three-dot button to every session pane header that opens that same
  canonical session menu for mouse, touch, and keyboard users. Anchor the menu to the
  button, label its purpose accessibly, restore focus on dismissal, and preserve the
  existing Escape/outside-click dismissal contract. Do not maintain separate sidebar and
  header menu definitions that can drift.
- [ ] Show agent working/awaiting/finished attention on both the session row and any
  collapsed ancestor layout rows. Background tabs retain independent state and badges;
  stack status is derived from children and never becomes session state.
- [ ] Add drag/drop and context/command-palette actions for split, stack, unstack, attach,
  and detach with keyboard equivalents and touch-safe menus. Invalid drops explain why and
  never silently replace, duplicate, or kill a session.
- [ ] Make tab strips and the sidebar hierarchy usable on narrow/mobile screens: bounded
  horizontally scrollable tabs or an overflow picker, minimum touch targets, visible active
  and attention states, and focus restoration after drawer navigation.
- [ ] Reconcile stacks after exit, restart, reconnect, or missing sessions without losing
  surviving sessions. Persist active child by stable id, not array index; choose a
  deterministic surviving tab when the active child disappears.

### Hooks and project safety

- [ ] Add `project_scope_id` as a match predicate for machine-owned global meta-hooks.
  Rules fire for matching sessions regardless of which mixed space contains them.
- [ ] Keep `.swe-mux/config.toml` data-only. Repository-owned `rules.toml`, commands,
  executables, hooks, secrets, bind policy, and raw preview destinations execute nothing.
  If repository rules are inspected for diagnostics, label them inert and never feed them
  into the active hook engine.
- [ ] Do not add a trust column or trust workflow. A future repository-rule feature must
  define a separate machine-owned fingerprinted trust store and defaults to untrusted when
  scope identity changes; that feature is not scheduled by this phase.

### API, UI, and operator behavior

- [ ] Extend session/space/history/note APIs with scope, repo-group, anchor-mode, anchor,
  and artifact-owner fields. Anchor patches use optimistic revisions; note Move/Copy uses
  typed conflict responses and never accepts an arbitrary filesystem destination.
- [ ] Add project-registry list/detail, artifact inventory, hide/unhide, and guarded Forget
  APIs with pagination/bounds and stable structured statuses. These routes never mutate
  repository files.
- [ ] Replace cwd-driven space-note requests with stable space/artifact identity. Retain a
  bounded compatibility migration path for existing clients without allowing caller cwd to
  override a recorded artifact owner.
- [ ] Keep spaces as the canonical workflow containers in the sidebar. Within each space,
  render the layout hierarchy and unpaned group defined in this phase; these are presentation
  nodes, not new session owners. Show an anchor badge on each anchored space and a compact
  foreign-scope marker when a session's scope differs from its space anchor. Mixed spaces
  remain fully usable.
- [ ] Add terminal-styled anchor selection/change/clear and note disposition UI to space
  context actions and Settings. Mobile receives the same operations without introducing a
  project-containment navigation model.
- [ ] Add project-oriented filtering/grouping as a view over sessions/history, not a
  `project → space → session` ownership tree. Labels distinguish concrete scope/worktree
  from broader repository grouping.
- [ ] Surface linked/detached/unlinked/conflicting notes in project detail with safe Open,
  Relink, Move, Copy, or reveal actions as applicable. Closing a space/session requires no
  cleanup prompt because durable artifacts remain available from the project shelf.

### Phase 5.5 exit criteria

- [ ] Mixed spaces pass spawn, move, note, hook, history, process/preview, reload, and
  mobile tests without cross-project config or artifact leakage.
- [ ] Every live/history session has one immutable scope; every project-resident space
  artifact has one recorded owner; repository groups influence display/history only.
- [ ] Seed/final cwd precedence and fallback diagnostics pass tests for explicit cwd, space
  cwd, anchor, project relative default, global default, missing paths, symlinks, Git
  worktrees, and non-repository directories.
- [ ] Auto/fixed/none anchors survive restart and migration. Existing mixed spaces and
  Markdown remain unmoved; ambiguous note ownership requires explicit resolution.
- [ ] Re-anchor Keep/Move/Copy tests prove no silent note retargeting, overwrite,
  cross-volume partial move, filesystem escape, or loss after conflict/restart.
- [ ] Global hook scope predicates follow session scope across mixed spaces, while all
  repository-owned executable/rule content remains inert.
- [ ] Closing spaces and agent sessions leaves their notes reachable from Projects/history;
  plain shells cannot create a soon-to-be-hidden durable session note; legacy shell notes
  appear as unlinked artifacts.
- [ ] Inventory tests prove every recognized supported `.swe-mux/` artifact under a known
  scope appears in project detail despite deleted spaces, deleted index relationships,
  missing history, duplicate files, malformed content, or daemon restart.
- [ ] Project hide/Forget/missing-root tests prove registry cleanup cannot cascade into
  project files, transcripts, anchors, history, or artifact ownership.
- [ ] Split and stack trees round-trip through persistence and reconnect. Sidebar tests
  prove grouped sessions are nested exactly once in visual order, unpaned sessions are
  distinct, collapsed ancestors propagate attention, and focus navigation stays
  bidirectional across desktop and mobile.
- [ ] Stack mutation and lifecycle tests prove tab activation/reorder/detach/dissolve never
  changes session identity, kills a detached session, duplicates a sidebar row, or loses a
  surviving child when another exits.
- [ ] Session-menu parity tests prove sidebar right-click, pane-header right-click, and the
  pane-header three-dot button resolve the same target session and command model across
  shell/Claude/Codex state changes, split/stack moves, keyboard use, touch, and narrow
  layouts.
- [ ] Current design/interface docs are updated to describe the shipped scope, anchor,
  artifact, spawn-precedence, hook, migration, layout-stack, and sidebar contracts before
  Phase 5.5 is marked complete.

## Phase 6 — Windows product maturity, CLI control, and diagnostics

Phase 6 follows the corrective Phase 5.5 scope/anchor refactor. It supports a hands-on
Windows proving period: expand useful control capabilities, make failures diagnosable,
and harden the desktop/mobile browser experience before any deferred integration or
release work begins.

### Practical CLI control

- [ ] Expand `mux` from a thin JSON client into a practical daemon controller: filtered
  session listing; profile/custom-argv spawn; rename/move/pin/kill; complete space
  and anchor management; project-scope/repository-group inspection; broadcast
  membership/send; history filters/resume; profile inspection; and safe Settings/config
  reads and updates.
- [ ] Keep browser presentation actions out of the CLI. CLI parity means parity for useful
  daemon control operations, not panes, modal presentation, visual focus, or theme preview.
- [ ] Resolve localhost, direct-tailnet, or optional Serve URLs from config while
  preserving explicit `MUX_URL` precedence.
- [ ] Use stable ids, conflicts for ambiguous names, actionable exit codes, structured
  errors, human-readable tables, and `--json` output. Scripts never need to parse UI prose.
- [ ] Route browser, CLI, and future Telegram actions through the same typed daemon
  operations so behavior and authorization boundaries cannot drift by client.

### Consolidated diagnostics

- [ ] Expand the current remote-status-only `mux doctor` into a read-only diagnostic:
  daemon/frontend version, ConPTY and Job Object health, shell/profile executables,
  Claude/Codex promotion capabilities, writable global/project paths, project config,
  scope/anchor/artifact conflicts, unified ccusage availability/version, process
  inspection, previews, listener/port, Tailscale/optional Serve, and non-secret hook status.
- [ ] Publish machine-readable capability/version information through health diagnostics;
  redact hook secrets, terminal bytes, prompt content, media, and credentials.
- [ ] Give every failed check a concrete remedy and distinguish unavailable optional
  features from failures that compromise terminal ownership or session cleanup.

### Windows soak and quality matrix

- [ ] Expand Python coverage for configuration/migrations, adapter state races, session
  lifecycle, Host/Origin/WS boundaries, spaces/layout, history/resume, events/hooks,
  project scopes/anchors/artifacts, Git/worktrees, CLI behavior, process ownership,
  previews, and reaping.
- [ ] Add real-browser component/Playwright coverage for default/custom creation,
  non-split replacement, inline kill, spaces/panes, palette/input transparency, Settings,
  history, notes, processes/previews, clipboard media, direct-tailnet use, responsive/touch,
  orientation changes, focus management, and accessibility.
- [ ] Add real Windows ConPTY integration tests for paths with spaces/Unicode, large
  output, resize, Ctrl+C, bracketed paste, input-owner handoff, browser reconnect, process
  attribution, and forced daemon death.
- [ ] Maintain a Windows CI lane for ruff, mypy, pytest, frontend typecheck/test/build, and
  focused ConPTY/browser smoke tests. Public artifact and multi-OS matrices remain Phase 10.
- [ ] Use the proving period to record and prioritize observed workflow friction without
  reopening completed product decisions or silently expanding into orchestration.

### Phase 6 exit criteria

- [ ] `mux` can inspect and automate the important daemon operations with stable human and
  JSON output while the browser remains the primary interactive interface.
- [ ] `mux doctor` identifies actionable local configuration, integration, ownership, and
  tailnet problems without mutating state or exposing secrets.
- [ ] Windows desktop/mobile core workflows and forced-cleanup scenarios pass the focused
  automated matrix; unresolved product friction is captured as explicit follow-up work.

## Phase 7 — Deferred 1: Telegram multi-session control

Telegram is first in the deferred queue. It is not part of the current Phase 6 proving
work, but it should be the next capability integration when external control becomes
useful. Phase 4/5 event, notification, correlation, and privileged-action boundaries are
the foundation; Telegram does not create a second session or account model.

### Provider and routing

- [ ] Implement one daemon-owned Telegram adapter per configured bot token. Never start a
  competing poller in each Claude/Codex session or rely on a backend-native channel plugin
  for cross-session routing.
- [ ] Persist opaque mappings from Telegram chat/message/thread/callback identifiers to mux
  session and space ids. A reply to a notification targets its originating session; an
  unthreaded message uses an explicit per-chat active-session selection or session picker.
- [ ] Label every outbound prompt, approval, completion, and response with backend,
  session, and space identity. Support Select active session, Open, Approve, Reject, Reply,
  and Clear selection only when the normalized daemon action supports that operation.
- [ ] Reuse Phase 6 typed actions and structured errors. Telegram never writes directly to
  a PTY, guesses a target from display names, or invents provider-specific session state.

### Configuration, safety, and reliability

- [ ] Keep Telegram optional and disabled by default. Store bot secrets outside public
  config responses/exports; provide terminal-styled Settings for enablement, sender
  allowlists, pairing, revocation, delivery status, and a test notification.
- [ ] Enforce sender allowlists, pairing/revocation, webhook or polling exclusivity,
  update-offset persistence, deduplication, body/media limits, retry/backoff, provider
  rate limits, expired-session rejection, and prompt-injection-safe confirmations.
- [ ] Preserve one-user history semantics. Telegram never manages or switches Claude/Codex
  accounts, stores backend OAuth credentials, or creates a separate conversation archive.
- [ ] Persist correlation/delivery metadata but never bot secrets, terminal bytes, prompt
  bodies, uploaded media, or backend credentials in general event/audit records.

### Phase 7 exit criteria

- [ ] One bot routes concurrent Claude and Codex notifications, replies, and supported
  approvals without ambiguous delivery.
- [ ] Session selection, reply mappings, retries, deduplication, daemon restart recovery,
  revocation, and expired targets pass provider-adapter and integration tests.

## Phase 8 — Deferred 2: SSH and native terminal attach

SSH remains behind Telegram. Direct Tailscale browser access is the supported remote
product path; this phase adds an optional terminal-native workflow without changing daemon
ownership, browser replay, or the rule that a daemon restart ends live sessions.

### Forwarding and attach

- [ ] Document browser access through OpenSSH local forwarding, including WebSocket
  behavior, key authentication, daemon service lifetime, and how it differs from the
  supported direct Tailscale listener.
- [ ] Add `mux attach SESSION` as a native-terminal client over the existing PTY contract:
  raw input/output, resize, input ownership, exit status, reconnect, and a
  detach chord that never kills the daemon-owned session.
- [ ] Make browser and native attachments use the same explicit input-owner handoff.
  Read-only observers never duplicate terminal input or xterm device responses.
- [ ] Add SSH-driven `mux attach` integration tests for disconnect/reconnect, Unicode,
  resize, Ctrl+C, bracketed paste, input-owner handoff, and daemon/session termination.
  Browser and native attachments must not duplicate input or device responses.

### Phase 8 exit criteria

- [ ] An SSH disconnect leaves the mux session live, and a later `mux attach` restores an
  interactive terminal without changing browser attach/replay semantics.
- [ ] Forwarding and attach documentation clearly distinguish daemon-owned session
  lifetime, SSH transport authentication, Tailscale browser access, and detach versus kill.

## Phase 9 — Deferred 3: WSL agent bridge and native Linux/macOS

Platform expansion begins only after the Windows product and earlier deferred integrations
justify the maintenance cost. Interactive WSL shells already work; WSL agent awareness and
native Linux/macOS daemons must preserve the same API, browser behavior, session identity,
attach/detach invariant, and daemon-owned child lifecycle.

### WSL agent bridge

- [ ] Build a distro-side bridge for native WSL Claude/Codex executable discovery,
  promotion/demotion, hook-secret delivery, hook execution, transcript discovery, and
  native-id correlation. Windows-interoperability commands alone do not qualify.
- [ ] Translate project, transcript, clipboard-media, and preview/listener ownership paths
  without exposing a Windows-only path to a Linux-side agent or trusting an unowned guest
  listener.
- [ ] Keep WSL profiles labelled `agent-bridge-unavailable` until native distro agents and
  promotion/state/history integration pass the same contract tests as Windows agents.

### Platform interfaces

- [ ] Introduce `PtyHost` protocol/factory implementations: Windows ConPTY/pywinpty and
  Linux/macOS POSIX PTY using `forkpty`/`openpty` or a vetted equivalent.
- [ ] Introduce lifecycle/reaper implementations: retain the Windows Job Object; on POSIX,
  a per-session guardian owns the process group and watches a daemon pipe. EOF after clean
  exit or daemon SIGKILL triggers graceful signal, bounded wait, then group SIGKILL.
- [ ] Add a cross-platform process-inspection boundary for descendant lifecycle, resource
  snapshots, signals/termination, and listener ownership. Windows jobs, POSIX process
  groups, and the WSL bridge produce the same bounded public model.
- [ ] Add OS-specific reveal services: Explorer, macOS `open`, and Linux `xdg-open`.
- [ ] Generate agent promotion launchers per OS: `.cmd` on Windows and executable POSIX
  shims/scripts on Unix, with safe structured argv/env/hook-secret propagation.
- [ ] Replace lowercased path comparisons with platform-aware normalization/same-file
  checks covering spaces, Unicode, symlinks, case sensitivity, UNC, and WSL paths.
- [ ] Make project-root and `.swe-mux/` resolution platform-aware across Git worktrees,
  non-repository cwd, symlinks, case sensitivity, UNC paths, and WSL translation.
- [ ] Guard platform imports so package import, config, CLI, and non-PTY tests work on every
  supported OS. Make default data directories, executable/transcript discovery, meta-hook
  `run`, reveal behavior, and config migration platform-aware.

### Native rollout

- [ ] Preserve the complete Windows regression contract while introducing abstractions.
- [ ] Linux: PTY/process groups, bash/zsh/pwsh profiles, Claude/Codex promotion and native
  transcripts, `xdg-open`, project files, process/listener ownership, and forced-daemon-
  death cleanup.
- [ ] macOS: PTY/process groups, zsh/bash/pwsh profiles, Claude/Codex promotion and native
  transcripts, `open`, service environment behavior, project files, ownership, and cleanup.
- [ ] Define and migrate data/config locations consistently, including Windows `~/.mux`
  versus XDG/macOS platform directories.

### Phase 9 exit criteria

- [ ] Windows, WSL-agent-aware, Linux, and macOS targets pass the applicable shared
  API/WS/session lifecycle and ownership contract suites.
- [ ] Interactive input, resize, UTF-8/Unicode widths, signals, clipboard/paste,
  scrollback/replay, shell exit, agent promotion, and crash cleanup work on each target.
- [ ] Git/worktrees, history/reconciliation/resume, hooks, profiles, `.swe-mux/` files,
  notes, processes/listeners, previews, and clipboard-image handoff behave natively without
  filesystem escape or cross-environment path leakage.

## Phase 10 — Deferred 4: public packaging and release

Public distribution is last. Source-checkout development remains acceptable until the
product has survived the Windows proving period and the intended platform matrix is known.

### Artifacts and installation

- [ ] Guarantee every wheel contains a frontend bundle built from the same source revision;
  fail release validation on stale or missing assets.
- [ ] Complete package metadata and governance: license, project URLs, supported-platform
  classifiers, changelog/release policy, security/contact path, and accurate capability
  documentation.
- [ ] Test wheel/sdist installation, upgrade, uninstall, config migration/backup, embedded
  frontend, and `mux`/`muxd` entry points on clean supported machines without a source
  checkout or Node.js.
- [ ] Validate `uv tool install swe-mux` and `pipx install swe-mux`; provide clean-machine
  install, upgrade, uninstall, logging, diagnostics, recovery, and backup documentation.
- [ ] Add service/autostart recipes only after each target's daemon-death child cleanup is
  proven.

### Release automation

- [ ] Add the final Windows/Linux/macOS CI matrix for ruff, mypy, pytest, frontend
  typecheck/test/build, artifact install smoke, browser smoke, and platform PTY cleanup.
- [ ] Validate a TestPyPI alpha before reserving/publishing the `swe-mux` PyPI package.
  Production publishing uses Trusted Publishing with short-lived CI identity; never store
  a long-lived repository publishing token.
- [ ] Validate tagged revision, source, frontend bundle, wheel/sdist metadata, migrations,
  documented commands, and capability/version diagnostics as one release unit.

### Phase 10 exit criteria

- [ ] A clean supported machine can install, start `muxd`, open the bundled UI, create
  default/custom shells, promote Claude/Codex, use declared optional capabilities, and
  stop without leaving owned processes.
- [ ] Tested artifacts can be upgraded and removed cleanly, and the public documentation
  matches the exact tagged capabilities and supported platforms.

## Spec traceability

| Spec section | Remaining delivery |
|---|---|
| §1–3 Product/architecture | Phase 0 runtime contracts; Phase 9 platform expansion |
| §4 Backend adapters | Phases 0, 2, and 4; Phase 9 platform adapters |
| §5 Session/state/scrollback | Phase 0; immutable project scope in Phase 5.5 |
| §6 Events/meta-hooks | Phases 0 and 4; scope predicates in Phase 5.5; optional Phase 7 Telegram |
| §7 Reserved communication | Keep `links` + `write_pty`; no relay consumer scheduled |
| §8 HTTP API + CLI | Phases 1–5; scope/anchor APIs in Phase 5.5; Phase 6 CLI; Phase 8 attach |
| §9 History/resume | Phase 4 agent-only history; scope/group split in Phase 5.5 |
| §10 Frontend | Phases 1–5; scope/anchor/artifact UI in Phase 5.5 |
| §11 Remote/security | Phase 5 |
| §12 Git/worktrees | Phase 4; concrete worktree scopes in Phase 5.5 |
| §13 Refactor inventory | Core donor replacement complete; remaining boundary cleanup in Phase 0 |
| §14 Configuration | Phases 1–4; scope precedence in Phase 5.5; Phase 9 platform adaptation |
| §15 Non-goals | No orchestration, headless agents, live restore, daemon TLS, or marketplace |

### Approved extension traceability

| Extension | Delivery |
|---|---|
| Project scopes, space anchors, and artifact ownership | Corrective Phase 5.5 |
| Durable Projects view and artifact discoverability | Corrective Phase 5.5 |
| Layout-level tab stacks and explicit sidebar hierarchy | Corrective Phase 5.5 |
| Project-local config and Markdown notes | Phases 3–5; ownership refactor in Phase 5.5; Phase 9 equivalence |
| Clipboard image handoff | Phases 3–5; platform equivalence in Phase 9 |
| Session-owned processes and previews | Phases 3–5; Phase 5.5 scope ownership; Phase 9 parity |
| Optional ccusage cached analytics | Phase 4; consolidated diagnostics in Phase 6 |
| Practical CLI control and diagnostics | Phase 6 |
| Telegram multi-session routing | Phase 4/5 foundations; deferred Phase 7 provider |
| SSH browser forwarding and native attach | Deferred Phase 8 |
| WSL agent bridge and native Linux/macOS | Deferred Phase 9 |
| Public PyPI package | Deferred Phase 10 |
| Agent account/profile management | Tabled; explicitly out of scope |
| Native Claude/Codex themes and Remote Control | Explicitly out of scope |

## Completion policy

- Do not mark tasks complete from code presence alone; acceptance coverage and current docs
  are required.
- Do not expand the reserved relay into orchestration work without a new product decision.
- Move this roadmap to `.docs/development/archive/ROADMAP.md` only after every scheduled
  phase is complete or explicitly removed from scope with a documented replacement plan.
