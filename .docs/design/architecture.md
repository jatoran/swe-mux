# Architecture

## Scope

- In: process topology, ownership, package boundaries, session lifecycle.
- Out: detailed endpoint schemas (`interfaces.md`); final feature roadmap (`../development/AGENT_MUX_SPEC.md`).

## Vocabulary

- Daemon: `muxd`; owns PTYs, registries, event bus, persistence, HTTP, and WebSockets.
- Session: one process hosted by one ConPTY.
- Adapter: backend-specific spawn/resume/transcript/exit behavior.
- Pane: browser viewport attached to a session byte stream.
- Space: persistent named group of sessions and pane layout.

## Process model

```text
Browser SPA ── HTTP + WS ──> aiohttp daemon ──> ConPTY ──> shell/agent CLI
                               │       │
                               │       └── Win32 reaper job
                               └── SQLite history/events/spaces
```

## Boundaries

- `src/swe_mux/server.py`: transport composition; no backend-specific CLI knowledge.
- `src/swe_mux/session.py`: live registry, spawn/stop, scrollback, PTY fanout.
- `src/swe_mux/pty_host.py`: only module importing `winpty`.
- `src/swe_mux/win_jobobj.py`: only module calling Win32 job APIs.
- `src/swe_mux/adapters/`: executable flags, resume syntax, transcript paths, graceful exit keys.
- `src/swe_mux/launchers.py` + `agent_launcher.py`: mux-local CLI shims and authenticated in-place shell promotion.
- `src/swe_mux/history.py`: SQLite schema and serialized access.
- `src/swe_mux/reconcile.py`: bounded background discovery of external native transcripts.
- `frontend/src/`: Preact state and xterm rendering; talks only through public HTTP/WS contracts.

## Lifecycle invariants

1. `POST /api/sessions` selects an adapter and allocates mux/native IDs.
2. Adapter returns executable plus argument-only command line.
3. `PtyHost` spawns inside ConPTY and assigns PID to the shared reaper.
4. A single fanout task appends output to bounded scrollback and subscriber queues.
5. Each `/pty/{id}` attach receives state, scrollback replay, then live bytes.
6. Explicit kill attempts adapter-specific graceful exit, then process-tree force kill.
7. Unexpected EOF records `crashed`; explicit stop records `exited`.

## Failure modes

- Daemon crash ⇒ job handle closes ⇒ child processes terminate.
- Browser disconnect ⇒ subscriber removed ⇒ PTY and scrollback remain live.
- Slow browser ⇒ bounded queue drops chunks; reconnect replays current bounded scrollback.
- Process exit ⇒ EOF sentinel reaches all current subscribers; history exit fields update.
- Missing frontend build ⇒ `/` returns a build instruction; API remains operational.

## References

- `../../src/swe_mux/session.py`
- `../../src/swe_mux/pty_host.py`
- `../../frontend/src/TerminalPane.tsx`
