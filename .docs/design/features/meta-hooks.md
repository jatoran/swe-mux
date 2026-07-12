# Meta-hooks

## What it is

- Hot-reloaded user rules map normalized mux events to local actions.

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

Actions: `notify`, `run`, `write_pty`, `http`. Match values use shell globs.

## Invariants

- Rules execute in the daemon and are rate-limited per rule.
- Hook ingress is loopback-only and requires the per-session secret inherited by the child.
- All hook observations enter the same persisted EventBus as transcript and PTY observations.

## Key files

- Engine: `src/swe_mux/meta_hooks.py`
- Ingress: `src/swe_mux/server.py`
- Event bus: `src/swe_mux/event_bus.py`

