# swe-mux overview

## System purpose

- Windows-native browser terminal multiplexer and read-only control plane for long-lived
  shell and coding-agent CLI sessions.
- Browser lifetime is independent of session lifetime; daemon lifetime owns all child processes.

## Surfaces

- Runtime: `muxd` aiohttp daemon; Preact browser client; `mux` HTTP CLI.
- Data: in-memory live sessions, process/preview registrations, and scrollback; SQLite
  spaces, project scopes/repository groups, durable artifact relationships, agent-only
  history, events, automation evidence, annotations, lineage, notifications, and cached
  indexes; project-owned Markdown/TOML in `.swe-mux/`.
- Integrations: ConPTY/pywinpty; Win32 Job Objects; PowerShell/CMD/WSL profiles; Git;
  Claude Code; Codex CLI; optional locally installed unified ccusage adapter.
  Read-only LLM observers use the fixed OpenRouter API with a separately encrypted key.

## Doc map

### Structural

- Architecture: `architecture.md`
- Durable data and ownership: `data-model.md`
- HTTP and WebSocket contracts: `interfaces.md`

### Features

- Sessions and terminals: `features/sessions.md`
- Spaces: `features/spaces.md`
- History and events: `features/history.md`
- Backend detection and observation: `features/backends.md`
- Universal hooks and OpenRouter observers: `features/automation.md`
- Fleet attention and intelligence: `features/fleet-intelligence.md`
- Legacy hook compatibility: `features/meta-hooks.md`
- Git awareness and worktrees: `features/git.md`
- Browser interaction model: `features/ui.md`
- Shell profiles and terminal creation: `features/shell-profiles.md`
- Project configuration and notes: `features/projects-and-notes.md`
- Usage analytics: `features/usage.md`
- Process ownership and previews: `features/processes-and-previews.md`
- Remote access and browser boundary: `features/remote-access.md`
- Read aloud (TTS) and dictation: `features/voice.md`

### Active development

- Final product contract: `../development/AGENT_MUX_SPEC.md`
- Remaining implementation roadmap: `../development/ROADMAP.md`

## Global invariants

- One daemon owns every ConPTY. A global kill-on-close Win32 job guarantees daemon-exit
  cleanup; a nested per-session job plus process reconciliation attributes descendants.
- Browser attach/detach never starts, stops, or resumes a session.
- Daemon termination kills live children; no live-session restoration occurs.
- Backend-specific executable flags and transcript locations remain inside adapters.
- PTY output has one consumer; fanout feeds scrollback and WebSocket subscribers.
- Native transcripts persist outside mux; mux never deletes transcript files.
- Plain shells never become agent history. Promotion converts the provisional lifecycle
  in place; demotion updates live state without duplicating its agent history.
- Reading a project has no filesystem side effect. Only explicit saves create `.swe-mux/`.
- Shells retain immutable spawn scope plus untrusted live cwd; each Claude/Codex run retains
  immutable run scope. Spaces are project-independent, with app-owned notes; project and
  agent-run notes remain project-local.
- Sessions are processes; split/stack leaves are viewports. Layout removal never implies kill.
- Network listeners are explicit localhost plus detected Tailscale IPv4; never wildcard LAN.
- Universal hooks are asynchronous and out of band. Observer results can annotate and notify;
  they cannot type, approve, spawn, execute scripts, call arbitrary URLs, or mutate projects.
- Canonical automation is machine-owned `rules.toml`. Repository `.swe-mux/rules.toml` is
  diagnostic and inert. OpenRouter secrets never enter public config, SQLite, logs, exports,
  events, prompts-at-rest, or project files.

## Key trade-offs

- Real interactive PTYs over headless execution ⇒ exact CLI TUI behavior; Windows-specific daemon core.
- Native transcripts over mux conversation copies ⇒ backend fidelity; parser isolation required for schema drift.
- SQLite over a service bus ⇒ one zero-ops daemon; single-machine scope.
