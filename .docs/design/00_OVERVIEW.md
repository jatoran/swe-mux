# swe-mux overview

## System purpose

- Windows-native browser terminal multiplexer for long-lived shell and coding-agent CLI sessions.
- Browser lifetime is independent of session lifetime; daemon lifetime owns all child processes.

## Surfaces

- Runtime: `muxd` aiohttp daemon; Preact browser client; `mux` HTTP CLI.
- Data: in-memory live sessions + scrollback; SQLite spaces, history, and events.
- Integrations: ConPTY/pywinpty; PowerShell; Claude Code; Codex CLI.

## Doc map

### Structural

- Architecture: `architecture.md`
- HTTP and WebSocket contracts: `interfaces.md`

### Features

- Sessions and terminals: `features/sessions.md`
- Spaces: `features/spaces.md`
- History and events: `features/history.md`
- Backend detection and observation: `features/backends.md`
- Meta-hooks: `features/meta-hooks.md`
- Git awareness and worktrees: `features/git.md`
- Browser interaction model: `features/ui.md`

### Active development

- Final product contract: `../development/AGENT_MUX_SPEC.md`
- Remaining implementation roadmap: `../development/ROADMAP.md`

## Global invariants

- One daemon owns every ConPTY and assigns child processes to one kill-on-close Win32 job.
- Browser attach/detach never starts, stops, or resumes a session.
- Daemon termination kills live children; no live-session restoration occurs.
- Backend-specific executable flags and transcript locations remain inside adapters.
- PTY output has one consumer; fanout feeds scrollback and WebSocket subscribers.
- Native transcripts persist outside mux; mux never deletes transcript files.

## Key trade-offs

- Real interactive PTYs over headless execution ⇒ exact CLI TUI behavior; Windows-specific daemon core.
- Native transcripts over mux conversation copies ⇒ backend fidelity; parser isolation required for schema drift.
- SQLite over a service bus ⇒ one zero-ops daemon; single-machine scope.
