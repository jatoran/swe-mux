# swe-mux overview

## System purpose

- Windows-native browser terminal multiplexer for long-lived shell, Claude Code, and Codex
  sessions.
- The daemon owns every ConPTY; browser reloads and disconnects do not stop sessions.
- Explicit Projects bind sessions, layouts, notes, history scans, and file browsing to canonical
  folders. The Project registry is independent of whether a Project is currently shown in the
  navigation sidebar.

## Surfaces

- Runtime: `muxd` aiohttp daemon, Preact browser client, and `mux` HTTP CLI.
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
- Backend detection and observation: `features/backends.md`
- Evidence replay and delivery readiness: `features/delivery-readiness.md`
- Fleet attention and intelligence: `features/fleet-intelligence.md`
- Git awareness and worktrees: `features/git.md`
- Sessions: `features/sessions.md`
- Project registry and Groups: `features/projects.md`
- Project/session notes, files, ignores, and watches: `features/project-resources.md`
- Mixed-view panes, tabs, drag/drop, and mobile projection: `features/workspace-layout.md`
- History: `features/history.md`
- Legacy hook compatibility: `features/meta-hooks.md`
- Durable operational evidence: `features/operational-telemetry.md`
- Process ownership and previews: `features/processes-and-previews.md`
- Prompt library: `features/prompt-library.md`
- Session and reset notifications: `features/notifications.md`
- Remote access and browser boundary: `features/remote-access.md`
- Browser UI: `features/ui.md`
- Shell profiles: `features/shell-profiles.md`
- Provider accounts: `features/provider-accounts.md`
- Usage analytics: `features/usage.md`
- Read aloud and dictation: `features/voice.md`

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
- Each Project has one project note and one folder browser. Any terminal may lazily create a
  distinct session note; opened notes and files are project-owned resource tabs.
- Worktrees remain backend Git capability, not a sidebar, tab, or session-creation concept.
- Native transcripts stay in vendor locations and are never deleted by swe-mux.
- Live provider system auth is authoritative. Startup never restores an older saved account;
  explicit switching replaces auth only while config, skills, transcripts, and running processes
  remain shared and provider-native.
- Sessions are processes; pane stacks and their tabs are viewports. Desktop split geometry is
  durable Project state, while the one-pane mobile workspace is only a projection. Closing a
  resource viewport never implies process or file deletion; closing a terminal requires an
  explicit inline kill confirmation.
- Automation observers cannot type, approve, spawn, execute scripts, or mutate projects.
- Delivery readiness is read-only and fail-closed: `unknown` never authorizes PTY input, and
  child-agent completion never implies root-agent readiness.
- Operational telemetry is observational: PID alone never identifies a process, reset alerts
  require confirmed fresh evidence, and quota correlation never proves personal identity.
- Prompt templates are inert text: selection may insert into a focused terminal but never
  submits or executes it. Device sounds consume normalized events and exclude child-agent stops.

## Key trade-offs

- One shared provider home preserves native live credential propagation, skills, and
  history; account switching is system-wide rather than concurrent/isolation-oriented.
- Explicit Projects make ownership deterministic; terminals may still `cd` elsewhere, but
  runtime location never retargets project resources.
- Leased non-recursive watches trade immediate whole-tree discovery for bounded idle cost.
