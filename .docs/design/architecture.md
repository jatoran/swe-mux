# Architecture

## Scope

- In: process topology, ownership, package boundaries, session lifecycle.
- Out: detailed endpoint schemas (`interfaces.md`); final feature roadmap (`../development/AGENT_MUX_SPEC.md`).

## Vocabulary

- Daemon: `muxd`; owns PTYs, registries, event bus, persistence, HTTP, and WebSockets.
- Session: one process hosted by one ConPTY.
- Adapter: backend-specific spawn/resume/transcript/exit behavior.
- Pane: browser layout leaf attached to a terminal, note, or preview resource.
- Space: persistent named group of sessions and pane layout.
- Project scope: concrete normalized worktree/filesystem root that owns `.swe-mux/` and
  behavioral defaults. Repository group: Git common/remote-derived display/history
  grouping only.
- Artifact: durable project-resident file relationship with immutable owning scope and
  last-known logical owner metadata.
- Universal hook: one machine-owned normalized event rule. Observer: a stateless,
  read-only OpenRouter call whose schema-validated result becomes an annotation or
  provider-neutral notification. Annotation: durable derived metadata owned by an agent run.

## Process model

```text
Browser SPA ── HTTP + WS ──> aiohttp daemon ──> ConPTY ──> shell/agent CLI
                               │       │             └── descendants/listeners
                               │       ├── global + nested Win32 jobs
                               │       ├── Git/events/legacy hooks/usage workers
                               │       ├── bounded automation queue ──> OpenRouter
                               │       ├── fleet evidence + attention/interlocks
                               │       └── project `.swe-mux/` files
                               └── SQLite history/events/spaces/scopes/artifacts/automation
```

## Deployment topology

- aiohttp listens on localhost plus the detected Tailscale IPv4 when
  `tailnet_enabled = true` (default). Failure to detect Tailscale degrades to localhost.
- Listener selection never uses `0.0.0.0` or a LAN interface. `--local-only` suppresses
  the tailnet site for one daemon run.
- Direct tailnet HTTP is the primary remote path. Tailscale provides transport encryption
  and policy; optional Tailscale Serve adds browser-recognized HTTPS.
- No swe-mux remote bearer/login path exists.

## Boundaries

- `src/swe_mux/server.py`: transport composition; no backend-specific CLI knowledge.
- `src/swe_mux/session.py`: live registry, spawn/stop, scrollback, PTY fanout.
- `src/swe_mux/pty_host.py`: only module importing `winpty`.
- `src/swe_mux/win_jobobj.py`: only module calling Win32 job APIs.
- `src/swe_mux/adapters/`: executable flags, resume syntax, transcript paths, graceful exit keys.
- `src/swe_mux/launchers.py` + `agent_launcher.py`: mux-local CLI shims and authenticated in-place shell promotion.
- `src/swe_mux/history.py`: SQLite schema and serialized access.
- `src/swe_mux/reconcile.py`: bounded background discovery of external native transcripts.
- `src/swe_mux/project_files.py` + `projects.py`: concrete scope/repository grouping and
  explicit, revisioned project-local config/Markdown notes.
- `src/swe_mux/processes.py`: bounded descendant reconciliation, ownership-checked
  actions, loopback listener discovery, and preview registration.
- `src/swe_mux/usage.py`: optional, cached, non-blocking external usage normalization.
- `src/swe_mux/automation.py`: normalized event/rule validation, transcript slices, bounded
  scheduling, deterministic actions, observers, budgets, and built-in observer definitions.
- `src/swe_mux/automation_store.py`: annotations, firing/action/call evidence, checkpoints,
  budgets, notifications, lineage, experiences, batches, and model-cache persistence.
- `src/swe_mux/openrouter.py` + `secret_store.py`: fixed-origin provider and write-only
  platform secret boundary.
- `src/swe_mux/fleet_intelligence.py`: deterministic attention evidence, environment
  interlocks, absence reports, experience suggestions, and actuation-gate diagnostics.
- `src/swe_mux/voice.py`: read-aloud clip pipeline — TTS engine boundary (edge-tts/SAPI),
  bounded app-owned audio cache, and the only non-observer OpenRouter call (spoken
  summaries, ledgered under `builtin:voice-summary`).
- `src/swe_mux/meta_hooks.py`: isolated legacy last-known-good compatibility engine.
- `frontend/src/`: Preact state and xterm rendering; talks only through public HTTP/WS contracts.

## Lifecycle invariants

1. `POST /api/sessions` selects an adapter and allocates mux/native IDs.
2. Adapter returns a platform-neutral `SpawnSpec` with executable, argv, and environment.
   The PTY host owns platform command-line quoting.
3. `PtyHost` spawns inside ConPTY, assigns PID to the shared reaper and a per-session
   nested job, then process reconciliation attributes descendant identity by PID and
   creation time.
4. A single fanout task appends output to bounded scrollback and subscriber queues.
5. Each `/pty/{id}` attach atomically subscribes, then receives revisioned state,
   replay brackets/bytes, and live bytes/updates without an attach-boundary gap.
6. Explicit kill attempts adapter-specific graceful exit, then process-tree force kill.
7. Unexpected EOF records `crashed`; explicit stop records `exited`.
8. Project files are data-only, revision checked, and created only by an explicit write.
9. Preview registration accepts only a detected session-owned or explicitly approved
   literal-loopback listener. Bounded HTTP/WebSocket traffic retains that immutable
   destination; raw development ports never become tailnet listeners.
10. A spawn records immutable spawn scope and agent promotion records immutable run scope.
    Spaces have no project owner: project/run notes remain project-local while space notes
    remain app-local, so mixed membership and `cd` never retarget an existing note.
11. Layout v3 separates process and viewport: split/stack nodes arrange independently
    killable session leaves, and removing a leaf never terminates its process.
12. Event persistence precedes bounded automation evaluation. A provider call receives only
    an explicit normalized slice; no prompt or slice is persisted. Every result is locally
    schema checked and can map only to the rule-authored annotation/notification action.
13. Cross-vendor reviews and observer batches require confirmation bound to the exact
    preview token. Reviews are typed user operations with lineage, never automation actions.

## Failure modes

- Daemon crash ⇒ job handle closes ⇒ child processes terminate.
- Browser disconnect ⇒ subscriber removed ⇒ PTY and scrollback remain live.
- Slow browser ⇒ bounded queue drops chunks; reconnect replays current bounded scrollback.
- Process exit ⇒ EOF sentinel reaches all current subscribers; history exit fields update.
- Missing frontend build ⇒ `/` returns a build instruction; API remains operational.
- Missing optional `psutil` or ccusage executable ⇒ the related surface reports a typed
  unavailable/error status; terminal operation remains unaffected.
- Missing/invalid OpenRouter key, stale model metadata, budget exhaustion, queue overflow,
  provider timeout, invalid JSON, or cancellation ⇒ visible automation diagnostics; no PTY,
  session state, note, transcript, history, or project mutation is affected.
- TTS engine failure, missing edge-tts, or voice budget exhaustion ⇒ a failed clip record
  and `voice_clip_failed` event; terminals and agent runs are unaffected.

## References

- `../../src/swe_mux/session.py`
- `../../src/swe_mux/pty_host.py`
- `../../frontend/src/TerminalPane.tsx`
