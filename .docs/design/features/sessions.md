# Sessions and terminals

## What it is

- A session is one backend adapter, one ConPTY-hosted process, bounded byte scrollback, and zero or more browser subscribers.

## Key concepts

- Mux ID: identity of one process lifetime.
- Native ID: Claude/Codex transcript identity; reused only for manual resume.
- Scrollback: exact-tail daemon-memory byte buffer; discarded on daemon exit.
- State: `starting | running | working | idle | awaiting | exited | crashed`.

## Operations

- Spawn validates cwd and space before creating the PTY.
- Default shells use the selected profile's exact executable/argv; mux never seeds or
  injects visible text and never applies shell-specific flags globally.
- Shell sessions enter `running`; agent sessions enter `starting` pending adapter observation.
- Attach replays scrollback without changing process state.
- Slow subscribers receive a gap frame and deterministic scrollback resync instead of
  silent permanent output loss.
- PTY WebSockets receive revisioned full-snapshot updates and a final exit snapshot.
- Agent state source priority is hook > transcript > PTY; lower-priority transcript
  inference cannot regress an authoritative hook transition.
- Detach removes only the WebSocket subscriber.
- Kill uses adapter exit keys and process-tree fallback.

## Key files

- Manager and live model: `src/swe_mux/session.py`
- PTY wrapper: `src/swe_mux/pty_host.py`
- Backend adapters: `src/swe_mux/adapters/`
- Browser terminal: `frontend/src/TerminalPane.tsx`

## Relates to

- `history.md`: session start/end and event persistence.
- `spaces.md`: each session belongs to exactly one space.
