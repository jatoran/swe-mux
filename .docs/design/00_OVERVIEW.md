# swe-mux overview

## System purpose

- Windows-native browser terminal multiplexer for long-lived shell, Claude Code, and Codex
  sessions.
- The daemon owns every ConPTY; browser reloads and disconnects do not stop sessions.
- Explicit Projects bind sessions, layouts, notes, history scans, and file browsing to canonical
  folders. The Project registry is independent of whether a Project is currently shown in the
  navigation sidebar.

## Surfaces

- Runtime: `muxd` aiohttp daemon, Preact browser client, `mux` HTTP CLI, and optional Windows
  `swe-mux` WebView2/tray supervisor.
- Data: volatile live sessions/previews/scrollback; SQLite Projects, Groups, history,
  events, durable process/quota/compaction/tool evidence, automation and indexes;
  project-owned `.swe-mux/` resources.
- Integrations: ConPTY, Win32 Job Objects, shell profiles, Git, Claude Code, Codex CLI,
  optional ccusage, Tailscale, and bounded OpenRouter observers.

## Doc map

### Structural

- Architecture: `architecture.md`
- Durable data and ownership: `data-model.md`
- HTTP and WebSocket contracts: `interfaces.md`

### Features

- Automation and OpenRouter observers: `features/automation.md`
- Automation enablement (per-project opt-in DAG): `features/automation-enablement.md`
- Scheduled agent runs (cron/interval/one-off session starts): `features/scheduled-runs.md`
- Tier 0 deterministic facts: `features/tier0-facts.md`
- Model-free control-plane detectors: `features/deterministic-consumers.md`
- Spawn-request compatibility storage and retired Observation Inbox: `features/observations.md`
- Backend detection and observation: `features/backends.md`
- Evidence replay and delivery readiness: `features/delivery-readiness.md`
- Fleet attention and intelligence: `features/fleet-intelligence.md`
- Attention ranking, interrupt budget, and narration: `features/attention-ranking.md`
- Git awareness and worktrees: `features/git.md`
- Sessions: `features/sessions.md`
- Crash recovery for sessions the PTY supervisor could not keep alive: `features/session-recovery.md`
- Multi-device terminal input and shared geometry: `features/terminal-input.md`
- Device presence (which device the human is at): `features/device-presence.md`
- Project registry and Groups: `features/projects.md`
- Project-owned notes, files, ignores, and watches: `features/project-resources.md`
- Read-only Project/global agent instructions, memory, and manual Project-root sync: `features/agent-context.md`
- Passive session CLI tools, extensions, policies, and configuration inventory: `features/agent-environment.md`
- Trusted task discovery and the Project Run menu: `features/project-actions.md`
- Mixed-view panes, tabs, drag/drop, and mobile projection: `features/workspace-layout.md`
- History: `features/history.md`
- Legacy hook compatibility: `features/meta-hooks.md`
- Durable operational evidence: `features/operational-telemetry.md`
- Process ownership and previews: `features/processes-and-previews.md`
- Prompt library: `features/prompt-library.md`
- Session and reset notifications: `features/notifications.md`
- Remote access and browser boundary: `features/remote-access.md`
- Windows desktop and tray lifecycle: `features/desktop-shell.md`
- Browser UI: `features/ui.md`
- Launch profiles: `features/launch-profiles.md`
- Provider accounts: `features/provider-accounts.md`
- Usage analytics: `features/usage.md`
- Read aloud and hands-free conversation: `features/voice.md`

### Technical

- Technical index: `../technical/00_INDEX.md`
- Backend package responsibilities: `../technical/backend/packages.md`
- Shared SQLite operation rules: `../technical/backend/sqlite.md`
- Frontend package responsibilities: `../technical/frontend/packages.md`
- Workspace state and persistence: `../technical/frontend/workspace-state.md`

## Global invariants

- A session belongs to exactly one explicit Project for its entire lifetime.
- New sessions start at their Project's canonical root. Runtime cwd is display/Git
  telemetry and never changes ownership.
- A Group organizes Project rows only.
- Each Project has a flat collection of Project-owned notes and one folder browser.
- Notes are created from that collection and are independent of terminal and session lifetimes.
- Worktrees remain Git artifacts rather than Projects, sidebar rows, or workspace tabs.
  The Project Run menu may create one and start a session in its exact root as a single explicit user operation.
- Native transcripts stay in vendor locations and are never deleted by swe-mux.
- Live provider system auth is authoritative. Startup never restores an older saved account;
  explicit switching replaces auth only while config, skills, transcripts, and running processes
  remain shared and provider-native.
- Sessions are processes; pane stacks and their tabs are viewports. Desktop split geometry is
  durable Project state, while the one-pane mobile workspace is only a projection. Closing a
  resource viewport never implies process or file deletion; closing a terminal requires an
  explicit inline kill confirmation.
- One session may be attached from several devices, but exactly one connection may write to
  its PTY and one arbitrated size applies to all of them. Which device the human is at is a
  property of the whole app, decided once from presence, never per session or per
  subscription. Every presence path fails open to absent.
- Automation observers cannot type, approve, spawn, execute scripts, or mutate projects.
- Control-plane automations are per-project opt-in via a dependency graph: substrate is inert
  (captures, never acts/spends) and gates its consumers; nothing runs on a project that did not
  opt in. Tier 0 fact capture is the first gated substrate consumer.
- Delivery readiness is read-only and fail-closed: `unknown` never authorizes PTY input, and
  child-agent completion never implies root-agent readiness.
- Operational telemetry is observational: PID alone never identifies a process, reset alerts
  require confirmed fresh evidence, and quota correlation never proves personal identity.
- Prompt templates are inert text: selection may insert into a focused terminal but never
  submits or executes it. Device sounds consume normalized events and exclude child-agent stops.
- Repository task files remain inert until an explicit Run selection and exact-content local
  approval; any supported task-file change revokes that approval.
- Preview registrations identify Project-wide loopback endpoints and follow the session that
  actually owns each listener. Preview tabs are layout state; closing one never unregisters a
  still-live service.
- Desktop window close/minimize changes presentation only. Daemon termination requires the
  explicit tray Quit path and a per-install secret accepted only from loopback.

## Key trade-offs

- One shared provider home preserves native live credential propagation, skills, and
  history; account switching is system-wide rather than concurrent/isolation-oriented.
- Explicit Projects make ownership deterministic; terminals may still `cd` elsewhere, but
  runtime location never retargets project resources.
- Leased non-recursive watches trade immediate whole-tree discovery for bounded idle cost.
