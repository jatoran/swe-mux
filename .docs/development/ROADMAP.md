# swe-mux roadmap

## Purpose

This roadmap tracks work still required to complete `AGENT_MUX_SPEC.md`, plus the
approved shell-profile, Settings, and cross-OS additions. It is a gap plan against
the current implementation, not a restatement of already-delivered behavior.

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
- Splits are explicit actions only. Ordinary terminal creation replaces or fills the
  focused pane while the displaced session remains live in the sidebar.
- Browser `alert`, `confirm`, and `prompt` dialogs are prohibited. Destructive toolbar
  actions use inline two-click confirmation; explicit context-menu Kill executes
  immediately.
- All UI chrome and terminal text share one monospace face, size, and weight matching
  xterm.
  Menus, hints, headings, inputs, history, and settings have no typography exceptions.

## Implemented baseline

The current application already provides the foundation this roadmap extends:

- Windows ConPTY session ownership, Win32 job reaping, bounded scrollback, replay,
  resize, multi-browser input ownership, and daemon/session lifecycle.
- Shell, Claude, and Codex adapters; in-place agent promotion; hooks and transcript
  observation; agent state/context display.
- Persistent space records/layout membership, history/events SQLite index, external history
  reconciliation, transcript viewing/resume, Git polling/worktrees, and meta-hooks.
- Bearer-aware HTTP/WS surfaces, `mux` CLI basics, sidebar spaces/sessions, up to four
  panes, explicit splits, detach/zoom, broadcast, palette/keybindings, responsive
  drawer, terminal-aware clipboard, and mobile Paste.

## Delivery order

```text
Runtime contracts
  -> Configuration service
    -> Settings + shell profiles
      -> command/pane/input completion
        -> history/events/hooks/git completion
          -> remote-security hardening
            -> platform abstraction + Linux/macOS
              -> release matrix and packaging
```

Cross-cutting test work ships with every phase; it is not deferred to the end.

## Phase 0 — Runtime contracts and reliability

### Adapter and state boundaries

- [ ] Complete the backend-adapter protocol: structured spawn/resume specification,
  parser creation, transcript association, hook setup/cleanup, and graceful exit.
- [ ] Remove Claude/Codex path, schema, and backend-name branching from
  `SessionManager`; keep backend-specific knowledge inside adapters.
- [ ] Replace `(application, quoted command_line)` with structured
  `SpawnSpec { executable, argv, env }`; platform PTY implementations own quoting.
- [ ] Centralize agent transitions in one StateMachine with explicit source priority:
  hook > transcript > PTY liveness.
- [ ] Deduplicate semantic turn/tool/approval events and reject stale or contradictory
  lower-priority transitions.
- [ ] Generate per-session hook configuration atomically; clean temporary settings and
  secrets after exit.
- [ ] Make Codex native-id/transcript association deterministic; retain cwd/time matching
  only as a bounded fallback.
- [ ] Add versioned Claude/Codex transcript fixtures and adapter contract tests.

### Session, scrollback, WebSocket, and persistence correctness

- [ ] Retain exactly the last configured N scrollback bytes, including oversized chunks.
- [ ] Define queue overflow behavior: account for dropped output and send a gap/resync
  control frame instead of silently losing data.
- [ ] Send live state/update/exit control frames on each PTY WebSocket; preserve attach
  order: state -> replay start -> replay bytes -> replay end -> live data.
- [ ] Derive xterm scrollback capacity from daemon configuration or expose an explicit
  documented byte/line policy.
- [ ] Persist rename, move, pin, effective executable/argv, and final session metadata
  into history transactionally. Profile identity is added with the Phase 2 model.
- [ ] Define ended-session registry behavior so exited records do not retain references
  to deleted spaces.
- [ ] Validate and version persisted layout schemas; add optimistic revision checks for
  concurrent browser updates.
- [ ] Implement atomic close-space disposition: move contained sessions to a selected
  space or kill them, while preserving the default space.

### Phase 0 exit criteria

- [ ] Race tests prove lower-priority transcript data cannot overwrite a newer hook state.
- [ ] Replay/live ordering, queue-gap recovery, exact scrollback bounds, and input-owner
  behavior pass API/WS integration tests.
- [ ] Session metadata and layouts survive browser reloads and daemon restarts where the
  product promises persistence; live PTYs still intentionally do not survive daemon exit.
- [ ] Forced daemon termination leaves no Windows child process.

## Phase 1 — Configuration service and Settings

### Versioned configuration service

- [ ] Replace ad-hoc TOML loading with a typed, versioned schema and migrations.
- [ ] Migrate existing `shell_exe`, backend executable paths, token, and other current
  values without losing user configuration.
- [ ] Validate before mutation; write through a temporary file and atomic replace; keep
  the previous valid configuration on parse or validation failure.
- [ ] Preserve unknown fields/comments where practical; otherwise document canonical
  rewrites and create a backup during migration.
- [ ] Separate public settings from secrets. Ordinary config reads never return the
  bearer token or per-session hook secrets.
- [ ] Define the shell-profile configuration schema and migration envelope used by Phase
  2, without exposing profile selection before spawn/runtime support exists.
- [ ] Define and validate one typed spawn contract before UI/CLI expansion:
  `backend`, `profile_id`, raw `executable/argv`, cwd/space defaults, and optional
  worktree request have explicit precedence and mutual-exclusion rules. Profiles are
  shell-only; direct agent backends use adapters.
- [ ] Add config read/update/reset endpoints with field-level errors and
  `restart_required` reporting. Host, port, and data directory require restart; safe
  runtime fields hot-apply.
- [ ] Add config revisions/ETags; reject or explicitly merge stale browser saves and
  external-file edits instead of silently overwriting them.
- [ ] Add read/write/validate endpoints for keybindings and hooks using the same atomic,
  last-known-good rules.
- [ ] Wire currently persisted space defaults into spawning with explicit precedence:
  request override -> space default -> global default -> daemon cwd.
- [ ] Emit configuration-changed and configuration-error events for UI diagnostics.

### Settings page

- [ ] Add a Settings route/surface reachable from `: menu` and the command palette.
- [ ] Use the established terminal/TUI visual system and the single global typography
  token; no native browser prompts or separate design language.
- [ ] Provide sections:
  - General: startup cwd, default space behavior, history/scrollback limits.
  - Terminals: non-profile terminal defaults initially; the profile editor, global
    profile default, and per-space profile defaults become active in Phase 2.
  - Agents: Claude/Codex executable paths, default args, observation/reconciliation.
  - Input: keybindings, middle-click paste, broadcast defaults, clipboard behavior.
  - Git and history: polling cadence, history reconciliation and retention controls.
  - Hooks and notifications: hooks editor/validation, channels, diagnostics.
  - Remote and security: read-only bind/auth status and Tailscale guidance until the
    Phase 5 token lifecycle is defined.
  - Appearance: one shared font/size/weight control applied uniformly if customization
    is exposed; never per-component typography.
- [ ] Show saved, hot-applied, restart-required, invalid, and externally-modified states.
- [ ] Support restore defaults, export sanitized config, and reveal config directory.
- [ ] Make the entire surface keyboard-operable with focus trap/restore and inline errors.

### Theme system

- [ ] Define one semantic color-token contract shared by UI chrome, state colors, xterm
  foreground/background/cursor/selection, and the ANSI palette.
- [ ] Ship built-in themes: Light, Dark, System, Solarized Dark, and Tokyo Night.
- [ ] `System` follows OS/browser color-scheme changes live; explicit selections remain
  stable until the user changes them.
- [ ] Add theme selection, live preview, cancel/revert, and persistence to Settings.
- [ ] Support custom themes with validated token editing plus import/export. Invalid or
  incomplete themes fall back safely without making terminal text unreadable.
- [ ] Preserve the no-texture terminal aesthetic unless a future explicit setting adds
  one; themes change semantic colors, not layout or typography.
- [ ] Test contrast, focus, selection, agent states, warnings/errors, and ANSI readability
  for every built-in theme in both UI chrome and live terminals.

### Phase 1 exit criteria

- [ ] Invalid settings never alter disk or runtime state.
- [ ] Safe Phase 1 settings affect the next relevant operation without daemon restart;
  bind changes clearly remain pending until restart.
- [ ] Reloading the browser and daemon reproduces saved settings.
- [ ] Every built-in theme applies atomically to chrome and existing xterm instances;
  System reacts live and custom-theme validation prevents unusable combinations.
- [ ] Ordinary API responses, logs, exported settings, and browser URLs contain no token.

## Phase 2 — Shell profiles and terminal creation

### Profile model

- [ ] Implement the Phase 1 profile schema throughout spawn/runtime/history and replace
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

- [ ] Profile fields: stable id, label, executable, argv, optional environment overrides,
  platform/capability constraints, cwd strategy, icon/short marker, and enabled state.
- [ ] Never inject PowerShell `-NoLogo` globally; every argument belongs to its profile.
- [ ] Store `shell_profile_id` and effective executable/argv on live sessions and history.
- [ ] Retain raw executable/argv as an advanced API escape hatch with explicit validation;
  normal UI and CLI flows use profile ids.
- [ ] Detect available shells and offer presets without silently changing the user's
  selected default.
- [ ] Ship/verify Windows PowerShell, PowerShell 7, CMD, and installed WSL distro presets.
  Add bash, zsh, platform login shell, and PowerShell presets during Unix rollout.

### Default and custom creation UX

- [ ] `New terminal` and `Ctrl+Alt+T` immediately create the resolved default profile in
  the active space/cwd with no dialog and no split.
- [ ] Add `New terminal custom…` to workspace/session context menus, `: menu`, and command
  palette.
- [ ] The custom launcher chooses profile and cwd from recent, active, pinned, typed, or
  browsed directories; it is compact, searchable, and keyboard-operable. Absolute-path
  browsing is daemon-backed with platform roots, permission/error handling, and clear
  semantics when the browser is remote.
- [ ] Add persistent pin/unpin management for favorite directories.
- [ ] Remember the most recent custom profile/cwd without replacing the configured default.
- [ ] Add global default profile and per-space default profile/cwd controls in Settings.
- [ ] Enable the full Settings profile editor: add, duplicate, reorder, enable/disable,
  validate, delete, select defaults, and restore detected presets.
- [ ] Support explicit `New terminal custom in split…` only from split-capable pane/session
  actions; custom creation otherwise follows normal replacement behavior.
- [ ] Validate executable/profile/cwd before replacing a pane; failures leave the current
  pane intact and show an actionable inline error.
- [ ] Add API `profile_id` selection, profile listing/capabilities, and CLI
  `mux profiles` / `mux spawn --profile <id>`.
- [ ] Contract-test direct `backend=claude|codex` API/CLI spawn, shell-first UI promotion,
  and adapter-native history resume as distinct supported paths.

### WSL delivery

- [ ] Stage A: interactive WSL profiles open the selected distro and translate Windows
  cwd to a valid distro cwd using `wsl.exe --cd` or a tested equivalent.
- [ ] Stage B: install/use a distro-side mux bridge for Claude/Codex promotion, hooks,
  secrets, and transcript access. A Windows `.cmd` shim alone cannot observe Linux-side
  agents.
- [ ] Surface profile capabilities until Stage B ships so an interactive WSL shell is not
  incorrectly presented as agent-aware.

### Phase 2 exit criteria

- [ ] PowerShell, pwsh, CMD, and WSL profiles start interactively with correct arguments,
  cwd, resize, Unicode, clipboard, and exit behavior.
- [ ] Default creation is zero-dialog/non-split; custom creation starts exactly the chosen
  profile/cwd and persists its identity.
- [ ] WSL Claude and Codex promotion/state tests pass before WSL profiles are labelled
  agent-aware.

## Phase 3 — Commands, panes, terminal input, and interaction parity

### Unified command system

- [ ] Give every actionable UI operation a stable command id, label, availability
  predicate, and optional default binding.
- [ ] Make the palette fuzzy-search the complete registry and support arrow selection,
  Enter, Escape, disabled explanations, and current binding display.
- [ ] Centralize terminal key interception in one allowlisted router. Disabled, unknown,
  and unmatched chords always reach the PTY.
- [ ] Complete defaults for pane navigation/split/zoom, space switching, terminal find,
  Settings, custom terminal creation, and palette.
- [ ] Add a Settings keybinding editor with conflict validation and documented unbindable
  browser chords such as Ctrl+W/T/N.
- [ ] Reject unmodified keys, plain typing, and modifier combinations that shadow normal
  terminal input; show the exact validation reason.

### Real pane layout

- [ ] Replace flat pane membership/fixed CSS grids with a persisted recursive tiling tree:
  leaf session plus horizontal/vertical split nodes and ratios.
- [ ] Add split horizontal/vertical, attach existing session, detach, move/swap panes,
  drag dividers, and temporary zoom.
- [ ] Remove the arbitrary four-pane cap or replace it with a documented configurable
  safety limit.
- [ ] Empty leaves offer attach existing session, create default terminal, or create
  custom terminal; no Claude/Codex backend tiles.
- [ ] Preserve layout through browser reconnect and daemon restart; reconcile missing or
  exited session leaves predictably.
- [ ] Use layout revisions across browsers: stale split/drag/move writes conflict or
  rebase instead of overwriting another client's tree.

### Context and inline interaction completion

- [ ] Session menu: pin/unpin attention, resume exited as new, attach/open, directional
  split, custom terminal actions, and current rename/move/broadcast/copy/reveal/worktree/kill.
- [ ] Pane menu: clipboard actions plus directional split, detach, zoom, and kill.
- [ ] Space menu: Settings for defaults and close-space inline choice to move or kill
  contained sessions.
- [ ] Replace remaining native prompts for rename, worktree creation, terminal search,
  and token login with inline editors/panels.
- [ ] Keep all menus viewport-bound, Escape/outside-click dismissible, and keyboard
  navigable with focus restoration.

### Terminal input and find

- [ ] Add an inline xterm search widget with next/previous, case option, result feedback,
  and close/focus restoration.
- [ ] Verify Ctrl+C selection-copy vs SIGINT, Ctrl+Shift+C/V, OSC-52, Select All, Clear,
  and optional middle-click paste. Plain Ctrl+V must paste in shell, Claude, and Codex
  through `term.paste()`/native xterm semantics, preserve bracketed paste, never raw-write
  clipboard contents, and never silently swallow a denied clipboard request.
- [ ] Define Ctrl+Shift+C with no selection so the chord falls through or reports its
  unavailable command instead of disappearing.
- [ ] Add automated regression coverage in PowerShell, CMD, pwsh, WSL, Claude, and Codex.

### Phase 3 exit criteria

- [ ] Every visible action can run from the command system and is keyboard accessible.
- [ ] Arbitrary split trees round-trip through persistence; divider drag, swap, detach,
  zoom, and replacement preserve live sessions.
- [ ] Normal New Terminal changes only the focused leaf and leaves its displaced session
  live/unpaned; concurrent-client layout mutations cannot silently clobber each other.
- [ ] No browser-native dialog is reachable in normal operation.
- [ ] PTY input transparency tests prove ordinary keys and disabled chords are never lost.

## Phase 4 — History, events, agents, hooks, Git, and responsive completion

### History and events

- [ ] Make history agent-only. A new shell may have an internal provisional lifecycle
  record, but it is invisible through History and is deleted on exit unless the session
  promotes to Claude or Codex. Direct agent sessions are visible immediately.
- [ ] Migrate existing data so ordinary `backend=shell` entries no longer appear and are
  removed when they have no agent/native transcript relationship.
- [ ] On in-place promotion, atomically convert the provisional shell lifecycle into one
  Claude/Codex history entry while preserving original start time, cwd, mux id, and native
  id. Never create duplicate shell + agent entries for one process lifetime.
- [ ] Add indexed/paginated history search with backend, project, state, date, space, and
  external filters plus explicit loading/error/empty states. Backend choices are Claude
  and Codex only.
- [ ] Add history-project identity and metadata. Prefer Git common repository/remote
  identity so worktrees group together; fall back to normalized cwd; keep an explicit
  Ungrouped bucket when identity is unavailable.
- [ ] Add project list/group/filter APIs and a collapsible project -> agent sessions UI,
  ordered by most recent activity. Support a friendly project label without introducing
  roles, leads, workloads, or other retired orchestration behavior.
- [ ] Persist agent context summaries when observation data supports them: context-window
  size, final context used %, peak context used %, input/output token totals, model, and
  measurement source. Current-window use must not be confused with cumulative tokens.
- [ ] Backfill context summaries from native Claude/Codex transcripts where reliable.
  Display `context unavailable` rather than estimating or showing a misleading zero when
  the backend/schema does not expose enough information.
- [ ] Add safe index-entry deletion through API/UI inline confirmation; deleting an index
  entry never deletes or edits its native transcript.
- [ ] Use direct history-id lookup for transcript/resume instead of bounded list scans.
- [ ] Validate resumability and return typed errors for missing transcript, adapter,
  native id, cwd, or target space.
- [ ] Show exit reason, final/peak context summary, token totals, model, external marker,
  and normalized event detail without exposing raw transcript implementation details by
  default.
- [ ] Resume agents through adapter/native identity into a chosen space/pane. Update the
  target layout atomically so the new session is visibly attached; never offer or create
  shell-history resume and never mutate native transcripts.
- [ ] Add monotonic event-sequence cursor pagination, stable ordering, retention policy,
  and WS reconnect catch-up with no gaps or duplicates.
- [ ] Add mobile master-detail navigation for history and Settings.

### Agent observation and attention

- [ ] Finish adapter/state priority work from Phase 0 for all supported Claude/Codex
  versions and record parser capability/diagnostic status.
- [ ] Complete thresholded context usage, approval/tool detail, awaiting/crashed/exited
  ordering, pin behavior, title attention count, and accessible status announcements.
- [ ] Keep sidebar rows free of cwd and Git duplication; pane header owns cwd and Git.

### Meta-hooks and notifications

- [ ] Make malformed hook files non-fatal: retain last-known-good rules and surface
  diagnostics in Settings/events.
- [ ] Validate template variables and action payloads; bound request/body sizes.
- [ ] Add subprocess lifecycle, timeout, resource, and platform-shell policy for `run`.
- [ ] Add timeout/status/retry policy for `http`; define burst/rate-limit semantics.
- [ ] Keep UI notification delivery complete; add ntfy/Telegram only as optional channel
  adapters after secret storage and retry behavior are defined.

### Broadcast and Git

- [ ] Move `send --all-broadcast` fanout server-side as one operation with membership and
  delivery events; define dead-session handling. Membership is daemon-memory state and
  intentionally resets with live sessions on daemon restart.
- [ ] Test the targeting invariant: broadcast off writes only to the input owner;
  broadcast on mirrors once to each explicitly included live session, including detached
  targets, never duplicates source input, and always shows the warning banner.
- [ ] Report detached Git HEAD by short SHA, not repository directory name.
- [ ] Add Git subprocess timeout/cancellation/concurrency limits and parallel unique-root
  polling.
- [ ] Validate worktree removal against the repository's actual worktree list before any
  mutation; keep worktree mutations explicitly user initiated.
- [ ] Implement typed create-worktree-and-spawn semantics for API/CLI/UI, including target
  validation, spawned cwd, partial-failure reporting, and cleanup/retention policy.

### Responsive, touch, and accessibility

- [ ] Add a focused-pane session switcher for narrow screens.
- [ ] Add long-press context menus, touch-safe positioning, and >=44px targets when touch
  input is detected while retaining compact desktop density.
- [ ] Test sidebar, history, Settings, palette, custom launcher, and terminal controls at
  approximately 360px width and both orientations.
- [ ] Add semantic dialog/menu/listbox roles, labelled icon controls, focus trap/restore,
  aria-live errors/state, contrast checks, and reduced-motion support.

### Phase 4 exit criteria

- [ ] History/filter/resume, hook reload/action failure, server broadcast, Git timeout, and
  worktree validation have API/integration coverage.
- [ ] Plain shells never appear in History or persist after exit; a shell promoted to
  Claude/Codex appears exactly once under the correct project.
- [ ] Worktrees of one repository group under one project; non-repository cwd sessions
  group predictably or appear under Ungrouped.
- [ ] Known context usage displays tested final/peak percentages and provenance; missing
  data displays `context unavailable` without fabricated values.
- [ ] Awaiting-agent attention is usable from another tab and by screen readers.
- [ ] Core flows pass keyboard-only, touch, mobile viewport, and automated accessibility
  tests.

## Phase 5 — Remote access and security hardening

- [ ] Write a threat model that states the single-user remote bearer is code-execution
  authority because the product can spawn executables, write PTYs, run hooks, and mutate
  worktrees.
- [ ] Define the unauthenticated bootstrap boundary for static assets/health and enforce
  auth consistently on every protected HTTP route and WS upgrade.
- [ ] Replace query-string WS tokens and blocking token prompts with a proper login/token
  bootstrap using a secure header/cookie mechanism that does not leak through URLs.
- [ ] Validate browser Origin for WS and mutating requests; add CSP and request/body/rate
  limits appropriate to a local single-user application.
- [ ] Add token rotate/revoke, secure file permissions/ACLs, secret redaction, and audit
  events that never include secret values.
- [ ] Add the corresponding Settings login/token rotation/copy/revoke UI only after this
  lifecycle and storage contract is implemented.
- [ ] Harden hook ingress: loopback peer only, constant-time secret comparison, event
  allowlist, body/rate limits, and rejection after session expiry.
- [ ] Document Tailscale bind/serve/HTTPS flows and the risks of `0.0.0.0`.

### Phase 5 exit criteria

- [ ] Route-by-route HTTP/WS/auth tests pass for loopback and non-loopback configurations.
- [ ] Tokens never appear in URLs, ordinary config responses, logs, history, exports, or
  hook payloads.
- [ ] A clean remote login, expiration/rotation, and reconnect flow works without browser
  native dialogs.

## Phase 6 — Cross-OS compatibility

WSL profiles are a Windows feature; a native Linux daemon is separate work. Cross-OS
support must preserve the same API, browser behavior, session identity, attach/detach
invariant, and daemon-owned child lifecycle.

### Platform interfaces

- [ ] Introduce `PtyHost` protocol/factory implementations:
  - Windows: ConPTY/pywinpty.
  - Linux/macOS: POSIX PTY using `forkpty`/`openpty` or a vetted equivalent.
- [ ] Introduce lifecycle/reaper implementations:
  - Windows: existing Job Object.
  - POSIX: a per-session guardian owns the process group and watches a daemon pipe; EOF
    after clean exit or daemon SIGKILL triggers graceful signal, bounded wait, and group
    SIGKILL. Linux may add parent-death signals; macOS still uses the guardian contract.
- [ ] Introduce OS-specific reveal service: Explorer, macOS `open`, Linux `xdg-open`.
- [ ] Generate agent promotion launchers per OS: `.cmd` on Windows and executable POSIX
  shims/scripts on Unix, with safe argv/env/hook-secret propagation.
- [ ] Replace Windows string quoting with structured argv; quote only at the platform
  process boundary.
- [ ] Replace lowercased path comparisons with platform-aware normalization/same-file
  checks; support spaces, Unicode, symlinks, case sensitivity, and UNC/WSL paths.
- [ ] Make all imports platform guarded so package import, config, CLI, and non-PTY tests
  work on every supported OS.
- [ ] Make meta-hook `run`, executable discovery, default data directories, transcript
  locations, and reveal behavior platform aware.

### Rollout

- [ ] Windows regression: preserve ConPTY, Job Object, PowerShell/CMD/pwsh, WSL, agent
  promotion, worktrees, and crash cleanup.
- [ ] Linux: PTY/process groups, bash/zsh/pwsh profiles, Claude/Codex promotion and native
  transcripts, `xdg-open`, packaging, and forced-daemon-death cleanup.
- [ ] macOS: PTY/process groups, zsh/bash/pwsh profiles, Claude/Codex promotion and native
  transcripts, `open`, service environment behavior, packaging, and cleanup.
- [ ] Define and migrate config/data locations consistently; document any Windows
  `~/.mux` to XDG/platform-directory differences.

### Cross-OS exit criteria

- [ ] Windows, Linux, and macOS pass the same API/WS/session lifecycle contract suite.
- [ ] Interactive input, resize, UTF-8, Unicode widths, signals, clipboard/paste,
  scrollback/replay, shell exit, and agent promotion work on every OS.
- [ ] Clean termination and an actual daemon SIGKILL close the guardian pipe and leave no
  owned child/process group in tested platform scenarios.
- [ ] Reveal, Git/worktrees, history reconciliation/resume, hooks, and default/custom
  shell profiles behave natively on every supported OS.

## Phase 7 — CLI parity, diagnostics, packaging, and release quality

### CLI and diagnostics

- [ ] Add session filters, profile/custom argv support, rename/move/pin, complete space
  management, server-side broadcast, Settings/config, and profile commands.
- [ ] Load URL/token from the secure config by default while preserving `MUX_URL` and
  `MUX_TOKEN` overrides.
- [ ] Return a conflict for ambiguous names; add stable structured errors and
  human-table/`--json` output modes.
- [ ] Add `mux doctor`: platform/PTY backend, shell/profile executable checks, agent
  capabilities, writable data/config paths, bind/port/auth status, frontend bundle
  version, and non-secret diagnostics.

### Automated quality matrix

- [ ] Expand Python coverage: config/migrations, adapters/state races, session lifecycle,
  API/auth/WS, spaces/layout, history/resume, events/hooks, Git/worktrees, CLI, and reaper.
- [ ] Add frontend component/Playwright coverage: default/custom creation, non-split
  replacement, inline kill, spaces, pane persistence, palette/input transparency,
  Settings, history resume, auth, responsive/touch, and accessibility.
- [ ] Add real PTY platform integration tests for paths with spaces/Unicode, large output,
  resize, Ctrl+C/signals, bracketed paste, multiple browser input ownership, and forced
  daemon death.
- [ ] Add Windows/Linux/macOS CI for ruff, mypy, pytest, frontend typecheck/build,
  wheel/sdist install smoke, and platform PTY tests.

### Packaging and operations

- [ ] Guarantee the wheel contains a frontend bundle built from the same source revision;
  fail release validation on stale assets.
- [ ] Provide clean-machine install, upgrade, uninstall, config migration/backup, logging,
  diagnostics, and recovery documentation.
- [ ] Add optional service/autostart recipes only after per-platform child lifecycle is
  proven.
- [ ] Publish capability/version information through health/config diagnostics.

### Phase 7 exit criteria

- [ ] A clean machine can install, start `muxd`, open the bundled UI, create the default
  and a custom shell, promote Claude/Codex, and cleanly stop on each supported OS.
- [ ] Release artifacts, bundled UI, migrations, and documented commands are validated in
  CI from the tagged revision.

## Spec traceability

| Spec section | Remaining delivery |
|---|---|
| §1–3 Product/architecture | Phase 0 runtime contracts; Phase 6 platform abstraction |
| §4 Backend adapters | Phases 0, 2, 4, 6 |
| §5 Session/state/scrollback | Phase 0 |
| §6 Events/meta-hooks | Phases 0 and 4 |
| §7 Reserved communication | Keep `links` + `write_pty`; no relay consumer scheduled |
| §8 HTTP API + CLI | Phases 1, 2, 4, 5, 7 |
| §9 History/resume | Phase 4; amended to agent-only, project-grouped history |
| §10 Frontend | Phases 1–4; amended UI decisions in this roadmap prevail |
| §11 Remote/security | Phase 5 |
| §12 Git/worktrees | Phase 4 |
| §13 Refactor inventory | Core donor replacement complete; remaining boundary cleanup in Phase 0 |
| §14 Configuration | Phases 1–2 |
| §15 Non-goals | Preserved; no orchestration, headless agents, live restore, in-daemon TLS, or marketplace |

## Completion policy

- Do not mark tasks complete from code presence alone; acceptance coverage and current docs
  are required.
- Do not expand the reserved relay into orchestration work without a new product decision.
- Move this roadmap to `.docs/development/archive/ROADMAP.md` only after every scheduled
  phase is complete or explicitly removed from scope with a documented replacement plan.
