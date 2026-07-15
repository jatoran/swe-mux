# Sessions and terminals

## What it is

- A session is one backend adapter, one ConPTY-hosted process, bounded byte scrollback, and zero or more browser subscribers.

## Key concepts

- Mux ID: identity of one PTY/process lifetime. A plain shell may move between projects.
- Native ID: Claude/Codex transcript identity; reused only for manual resume.
- Agent run ID: identity of one Claude/Codex invocation within a PTY; this is the durable
  history and session-note owner.
- Scrollback: exact-tail daemon-memory byte buffer; discarded on daemon exit.
- State: `starting | running | working | idle | awaiting | exited | crashed`.

## Operations

- Spawn validates cwd and space before creating the PTY.
- Default shells use the selected profile's exact executable/argv; mux never seeds or
  injects visible text and never applies shell-specific flags globally.
- Shell sessions enter `running`; agent sessions enter `starting` pending adapter observation.
- Each live session snapshot exposes passive startup timing. Phase durations cover project
  resolution/config, profile resolution, ConPTY spawn, and daemon registration; cumulative
  milestones cover server readiness, first PTY output, and the first OSC 7 prompt signal.
  `first_prompt` is absent when the selected shell does not provide OSC 7 integration. These
  measurements do not inject input or mutate the prompt. The daemon persists one bounded
  `session_startup_measured` event at first prompt, or at session end when no prompt was
  observed, so removing the live session does not erase the diagnostic sample.
- Spawn cwd/scope remain immutable and trusted. OSC 7 runtime cwd is local-directory-
  validated, debounced, rate-limited, and display-only. Promotion captures an immutable run
  cwd/scope; demotion restores shell presentation without changing spawn identity.
- Attach replays scrollback without changing process state.
- Browser PTY attachments reconnect with bounded backoff after transport loss. Returning from a
  suspended page (`visibilitychange`, bfcache `pageshow`, or network `online`) forces a fresh
  attachment when needed, resets stale rendered output at replay start, refits the terminal, and
  reclaims input ownership; the process continues independently in the daemon while the phone
  sleeps.
- On the first attachment within five seconds of spawn, the browser may forward only
  xterm-generated terminal protocol replies while replay is being parsed. The daemon
  advertises that narrow reply path so PowerShell device-attribute negotiation does not
  wait for its multi-second timeout; ordinary keystrokes and all later/resync replay stay gated.
- Slow subscribers receive a gap frame and deterministic scrollback resync instead of
  silent permanent output loss.
- PTY WebSockets receive revisioned full-snapshot updates and a final exit snapshot.
- Mobile typing uses xterm's composition-aware input directly and writes through the same
  ownership-gated PTY WebSocket. When the software keyboard changes the visual viewport, the
  terminal refits to the remaining visible height.
- Agent state source priority is hook > transcript > PTY; lower-priority transcript
  inference cannot regress an authoritative hook transition.
- Demotion records the ended backend/native ID in the PTY lifetime. Fallback transcript
  detection ignores that stale run and considers only terminal output plus transcript
  activity created after the new shell-detection pass begins. Retained Claude/Codex output
  cannot promote the parent shell or select another backend. Explicit launcher promotion
  may reuse the ID for a deliberate resume.
- Detach removes only the WebSocket subscriber.
- Kill uses adapter exit keys and process-tree fallback. Crashed and exited sessions remain
  visibly ended until dismissed; dismissal removes the volatile live-session entry without
  trying to stop its PTY again and does not delete durable agent-run history.

## Key files

- Manager and live model: `src/swe_mux/session.py`
- PTY wrapper: `src/swe_mux/pty_host.py`
- Backend adapters: `src/swe_mux/adapters/`
- Browser terminal: `frontend/src/TerminalPane.tsx`

## Relates to

- `history.md`: session start/end and event persistence.
- `spaces.md`: each session belongs to exactly one space.
