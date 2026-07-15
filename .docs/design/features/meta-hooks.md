# Legacy hook compatibility

## What it is

- This is the isolated compatibility engine for the pre-Phase-6 `hooks.toml` contract.
  New automation uses Universal hooks in `rules.toml`; see `automation.md`.

## Configuration

`~/.mux/hooks.toml`:

```toml
[[hook]]
match = { type = "approval_needed", backend = "claude" }
action = { kind = "notify", channel = "ui" }

[[hook]]
match = { type = "turn_ended", session_name = "builder-*" }
action = { kind = "run", command = "Write-Host {session_name} finished" }
rate_limit_seconds = 5
```

Actions: `notify`, `run`, `write_pty`, `http`. Match values use shell globs. Templates
accept a validated, bounded variable set.

## Invariants

- Rules execute in the daemon and are rate-limited per rule.
- Reload parses and validates the complete file before replacement. A malformed edit keeps
  the last-known-good rules and emits a Settings/event diagnostic.
- `run` uses an explicit platform shell policy, bounded command/output sizes, a timeout,
  and process cleanup. `http` has body limits, timeout, status handling, and bounded retry.
- Notification actions append provider-neutral delivery records with correlation ID,
  sender/channel, reply target, attempts, and status. These records are the foundation for
  later optional external providers; no Telegram poller exists in this phase.
- Browser clients consume notification events as live toasts and retain the daemon's last
  100 UI notifications in an inbox reachable from `: menu` and the command palette.
- Hook ingress is loopback-only and requires the per-session secret inherited by the child.
- All hook observations enter the same persisted EventBus as transcript and PTY observations.
- A plain shell matches `project_scope_id` against its daemon-resolved spawn scope. An active
  Claude/Codex session matches its immutable run scope. Event payloads and OSC runtime scope
  cannot override these match fields, and `run` actions execute from trusted spawn/run cwd.
- Legacy rules remain last-known-good and are never silently migrated or deleted. Their
  `run`, `http`, and `write_pty` authority is not exposed to canonical rules, observers,
  model results, repository files, or the ordinary Automation editor.

## Key files

- Engine: `src/swe_mux/meta_hooks.py`
- Ingress: `src/swe_mux/server.py`
- Event bus: `src/swe_mux/event_bus.py`
- Inbox/toast UI: `frontend/src/Notifications.tsx`, `frontend/src/App.tsx`
