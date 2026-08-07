# Sessions and terminals

## What it is

Daemon-owned interactive ConPTY processes with immutable Project ownership, bounded replay,
and reattachable browser viewports.

## Model

- A session is one adapter, one ConPTY process, bounded byte scrollback, and zero or more
  browser subscribers.
- `spawn_backend` and `spawn_native_session_id` are immutable root-process facts.
  `backend` and `native_session_id` may change only for a legitimate agent run promoted
  inside a root shell. A provider launched as the root can never be demoted by a child CLI.
- A session has one immutable canonical `project_id`. It cannot exist before a Project does.
- The daemon starts every new, split, stacked, resumed, or review session at the owning
  Project's canonical root.
- Validated OSC 7/runtime cwd is display and Git telemetry only. Navigating elsewhere does
  not change Project membership, layout, note, file browser, defaults, or history ownership.

## Operations

- Direct shell creation uses the requested/profile/Project/global profile precedence.
- Project Actions create ordinary shell-backed sessions. Every step is attributed to the
  selected Project, appears as a normal terminal, and spawns the imported command directly with
  its validated in-Project cwd/env carried as spawn fields. No swe-mux binary sits in the
  resulting process tree, which is what lets a task terminal survive a frozen-app redeploy.
- The browser inserts a client-only `starting terminal…` row/tab before the spawn request
  resolves. Temporary IDs never reach Project persistence or PTY routes; success atomically
  replaces the placeholder with the daemon session, while failure removes it and restores a
  surviving focus target.
- Spawn preparation runs independent Git identity probes concurrently and briefly caches the
  stable result for repeated launches. Synchronous ConPTY creation runs outside the daemon event
  loop, keeping existing terminals, events, and HTTP responsive during Windows process startup.
- Once ConPTY exists, the daemon publishes the in-memory session and returns the spawn response;
  durable Project/history/event registration continues in the background. Transcript imports or
  other SQLite work therefore cannot hide an already-usable terminal. Lifecycle writes that
  depend on the history row wait for this registration internally.
- PTY attach replay and input handling never await attachment/input telemetry persistence.
  Startup metrics separate interactive `server_ready` from `durable_registration` latency.
- xterm device replies are classified separately from human input. Codex OSC 10/11 color
  replies are suppressed in both browser and daemon because its short native-Windows startup
  probe may time out across the browser/WebSocket hop; Codex uses its console-palette fallback
  instead of receiving stale reply bytes as composer input.
- A browser fits xterm after selecting its renderer, then sends `attach_ready` with the active
  columns and rows. The daemon resizes ConPTY before sending replay bytes; older clients may use
  their first `resize` frame or the bounded compatibility timeout. Messages received while
  readiness is pending are processed only after the replay boundary.
- A session attached from several devices shares one keyboard and one size, and the daemon
  arbitrates both rather than letting the last client to speak decide. Attach, detach and
  reconnect never change process state, and neither does losing an ownership race: refused
  input is echoed back for one replay instead of dropped. Rules, frames and diagnostics:
  `features/terminal-input.md`.
- Desktop shell terminals default to WebGL with DOM fallback.
  Claude terminals are DOM-only because retained alternate-screen panes can return from a hidden compositing interval with a live but corrupt WebGL surface and no context-loss event.
  Codex terminals use the built-in DOM renderer under the `auto` preference, because its full-screen redraws can corrupt off-tail WebGL scrollback, and its rich renderer still reflows the transcript on resize.
  An explicit `webgl` preference reaches Codex, but not Claude or OMP.
  OMP continuously repaints its tail, and deep sessions are repeatedly reconstructed
  from bounded replay as panes leave the warm cache, so a stale WebGL surface looks
  exactly like missing replay until a real resize repairs it.
  On a visible cold attach at unchanged geometry, the daemon also pulses OMP by one
  column and restores the canonical size before live delivery starts.
  This queues a fresh application repaint behind the bounded replay without changing
  the session's final dimensions or sending application input.
  The pinned xterm 6 WebGL addon carries the upstream missing-buffer-line guard in its runtime bundles, preventing a resize/trim race from aborting a model update and leaving stale glyphs.
  Mobile remains DOM-only.
- Agent startup state uses semantic evidence first. Claude and trusted Codex lifecycle hooks
  normally report `SessionStart`; Codex with disabled/untrusted hooks (or a degraded Claude hook
  path) may use settled live PTY output as a startup-only, lowest-priority readiness signal until
  its first native transcript event.
- Standing engagements (an armed `/loop`, a cron schedule, background tasks, live subagents)
  are annotations on the session (`SessionRecord.standing_activity`), never states: an idle
  session with an armed loop is exactly as idle, and as deliverable, as one without. They are
  run-scoped — every seam that resets observation identity (rollover, heal, promote, demote,
  end) clears them — and TTL'd so a wrong annotation decays on its own. Contract and
  detection: `features/status-detection.md`.
- Claude/Codex promotion preserves the parent PTY's canonical Project and records an atomic
  agent-run history lifecycle.
- Attach, detach, browser reconnect, and pane operations never change process state.
- **Retention and replay are separate budgets.** `scrollback_bytes` (5 MiB) is what the daemon
  keeps; `attach_replay_bytes` (512 KiB) is what a fresh attach or a resync is handed. A client
  must parse every replayed byte before it can render anything, and xterm time-slices that work
  across render frames, so a full-buffer replay is *watched* happening — worst for a CLI in raw
  scrollback mode (Codex), whose bytes are real lines that each allocate and scroll rather than
  repaints of one alternate screen. A trimmed window resumes after the next newline so it can
  never begin inside an escape sequence, and restates the alternate screen when it cut the
  child's `?1049h` off (otherwise a full-screen TUI would repaint into the client's *normal*
  buffer, growing scrollback on every frame — the exact cost the bound removes).
- Slow subscribers receive a gap frame and deterministic bounded replay.
- Explicit kill attempts adapter-specific graceful exit before process-tree termination.
- Once the root exit code is captured, an ended session releases its dead ConPTY host. The
  reader keeps only a thread-local reference long enough to drain final output, and finalization
  cancels any frozen pywinpty read still parked after root exit. Retained scrollback is independent
  of the OS pseudoconsole handle. This lets ended sessions remain visible until explicitly
  dismissed without retaining `OpenConsole.exe`/`conhost.exe`.
- Ended-session history remains durable.
- Sessions do not own notes.
  Notes are created and managed through the owning Project's flat Notes collection.
- Resume requires a target Project and a valid native identity/transcript. The new process
  starts at the selected Project root and receives a new mux identity.
- Terminal environments are built from a scrubbed base (`spawn_contract.base_session_env`):
  parent-Claude session markers (`CLAUDECODE`, `CLAUDE_CODE_CHILD_SESSION`, session
  id/entrypoint/pid/effort) are dropped at spawn for every session because a daemon relaunched
  from inside an agent session would otherwise mark every nested `claude` as a child session —
  disabling its transcript saving and with it swe-mux's observation. Deliberate user
  configuration (feature flags, `ANTHROPIC_*`) passes through untouched.
- Every session is spawned describing the terminal that actually terminates its PTY — the
  browser's xterm.js client, not whatever launched the daemon (`spawn_contract.terminal_env`,
  applied centrally in `SessionManager.spawn`). It forces an xterm-256color / truecolor
  capability and shadows inherited emulator and multiplexer markers (Windows Terminal, Kitty,
  WezTerm, iTerm2, VS Code, tmux, screen, Zellij, CMUX) so a CLI never mistakes the daemon's
  launch context for its own terminal. Without it a frozen, tray-launched daemon inherits no
  `TERM`/`COLORTERM` at all and every pane renders monochrome. This is the lowest-precedence
  layer: an adapter's own env, a shell profile, and a task's env all override it.
- Agent harnesses additionally get colour forced (`FORCE_COLOR`, `CLICOLOR_FORCE`;
  `spawn_contract.session_terminal_env` gated on `is_agent_harness`). Node-based CLIs (Claude
  Code via chalk/supports-color) refuse colour unless stdout is detected as a TTY, and swe-mux
  launches agents through a shim → windowed frozen `swe-mux.exe` → `cmd.exe` → CLI chain that
  hides the ConPTY's TTY-ness from that check; Rust CLIs (Codex, OMP) key off `COLORTERM` and
  are unaffected by the TTY gate, but the forcing keeps every agent harness coloured regardless.
  The same base (`base_session_env`) also drops an inherited `NO_COLOR` for agents: it is the
  one launch-context pollutant a session's terminal env cannot override (a merge can add but not
  remove a key), and Codex obeys `NO_COLOR` over `CLICOLOR_FORCE` — so without the drop a daemon
  redeployed from inside an agent session leaves Codex monochrome and makes Node warn `NO_COLOR
  ignored due to FORCE_COLOR`. All of this is scoped to agents on purpose: a plain shell keeps
  honouring pipe semantics (no forced flag leaks ANSI into `cmd > file`) and an inherited
  `NO_COLOR` (no-color.org).
- Session-preserving reload (`pty_supervisor_enabled`): PTYs spawn inside the standalone PTY
  supervisor through a `RemotePtyHost` with the same host contract (spawn/write/resize/
  isalive/exit_status/release/stop). The supervisor keeps the authoritative scrollback and the
  per-session/global reaper Jobs; the daemon mirrors scrollback from the subscription stream
  (attach replay, nested-agent detection, and the PTY-idle watchdog all read the mirror). Each
  session's record snapshot, hook secret, and transcript path are mirrored into the supervisor
  (debounced, deduplicated) so a restarted daemon can rebuild the `Session`, reseed scrollback,
  revalidate provider/transcript ownership, and restart its observer/detection tasks — agents
  mid-turn are never touched. Legacy snapshots without immutable root fields are reconstructed
  from the retained spawn executable/argv. If mutable metadata conflicts with that root identity
  or another live session's transcript claim, adoption repairs the record before publishing it
  and quarantines the misattributed history run. A direct agent returns to its root provider; a
  shell whose promoted run claimed a live sibling returns conservatively to shell/detection.
  Shutdown intent decides the sessions' fate: quit stops and reaps everything as before; detach
  (daemon restart) leaves supervised sessions running — and stops those sessions' tickers
  first, because after the client disconnects `isalive()` is false by definition and one more
  tick would persist a spurious exit for an agent that is still running. If the supervisor is
  unreachable at spawn time the daemon falls back to today's in-process ConPTY, whose lifetime
  is daemon-bound as before.
- A spawn mirrors the session's initial metadata with the spawn RPC itself, not only through
  the debounced meta sink: a daemon crash inside that ~0.5s window otherwise left the
  supervisor holding a live session with empty metadata, permanently unadoptable and
  reachable only by reaping everything.
- **A broken connection is not a dead supervisor.** Only a supervisor whose process is
  actually gone means the kill-on-close Jobs closed and the trees died; a transient socket
  fault leaves sessions running. Treating the second as the first fabricated an exit for
  every live session, recorded false history, and re-adopted them on the next boot. The
  daemon reports `supervisor_state: "lost"` instead, and recovery is a daemon restart.
- **Transcript ownership is corroborated, never assumed.** The candidate pool for a session's
  transcript is the backend's *shared* per-cwd directory, which every CLI on the machine writes
  into — a VS Code Claude extension, a scripted `claude -p`, a one-off terminal run. Three
  gates keep another writer's conversation from being adopted (which would rekey
  `native_session_id`, rebind the history row, and stream the outsider's status and tokens
  under this session's identity):
  - **Bind at first observe — by identity evidence, never by elimination.** The
    single-unclaimed-candidate fallback is refused for every backend. Claude does not need
    it: its transcript path is *derived* from the native id mux injected as `--session-id`,
    so the exact-match route always exists. For a backend that mints its own conversation id
    the fallback is not *safe*, and the gates that looked sufficient were measured and are
    not — "created after this run began" and "our PTY produced output when it appeared" both
    pass for an unmanaged CLI, because an agent TUI repaints continuously. Live: an unbound
    Codex pane adopted the rollout of a `codex` started outside mux in the same cwd and
    rekeyed itself onto the stranger's thread. Codex's `session_meta` does not separate them
    either — `originator` betrays only the headless `codex exec` (`codex_exec`/`exec`); an
    interactive outsider reports `codex-tui`/`cli`, exactly like ours.
  - **Codex binds from its own lifecycle hook.** What an outsider cannot forge is a hook: it
    arrives over this session's own loopback ingress authenticated with this session's own
    secret. Trusted Codex hooks report `session_id` on `SessionStart`, so normal binding lands
    before the first turn and transcript discovery can exact-match it. The older
    `agent-turn-complete` `thread-id` remains the compatibility binding path when lifecycle hooks
    are disabled, untrusted, or unavailable (`_bind_native_id_from_hook` accepts both). Whether the id was
    dictated at spawn is an adapter declaration, `assigns_conversation_id`, and is
    deliberately **not** inferred from the shape of the id: mux session ids are UUIDs too, so
    a shape test treats every fresh Codex placeholder as already bound and refuses the only
    evidence that could bind it.
  - **The CLI's own answer wins over any heuristic.** `claude --continue` / `-r <term>` let
    the CLI choose the conversation, so the shim cannot inject or read a `--session-id` and
    promotes with an empty native id (injecting one anyway is what the CLI rejects outright
    with exit 1). The root `SessionStart` hook then arrives over this session's own loopback
    ingress with this session's own secret, which is the strongest available proof of which
    conversation this PTY runs; it fills an *unknown* id only and never overwrites a bound
    one, so a hook cannot rekey a session.
  - **Filesystem switch fallback is Codex-only.** Backends whose adapter reports conversation
    replacement itself (`reports_conversation_rollover` — Claude, via the SessionStart ingress)
    never take the filesystem switch heuristic at all: the CLI's own
    report is strictly stronger evidence, and guessing from mtimes is the one mechanism that
    could latch a session onto a sibling's conversation in a shared cwd. Where the heuristic
    does apply (Codex keeps it because hooks can be unavailable), following a freshly-written
    transcript
    additionally requires that this session's own PTY produced output around the time the
    candidate appeared. An outside CLI leaves our PTY silent, which is what distinguishes it.
  - **Unresolved siblings block.** Another live session in the same cwd makes a fresh
    transcript ambiguous — but only while it *is* ambiguous. A same-backend sibling is ruled
    out per candidate when its own transcript was still being written after the candidate
    appeared (it is demonstrably on its own conversation), or when its PTY produced nothing
    across the candidate's creation (it cannot have written it). Anything else — a sibling
    that went quiet while still talking, or one with no transcript bound yet — keeps blocking,
    because that is indistinguishable from a sibling that just cleared. An unpromoted *shell*
    that has echoed this backend's name blocks unconditionally: its shim-less launch is about
    to create a transcript here, it owns no id or file to rule it out with, and this session's
    2s switch watcher can beat that shell's 0.5s detection loop to the claim.
- **Replacing a bound conversation is a lifecycle transition, not a rebind.** An in-CLI
  `/clear` or `/new` keeps the PTY and replaces the conversation, so the daemon retires the
  agent run and opens a successor (`agent_run_seq`, `agent_conversation_rolled`) rather than
  rekeying the live one — see `backends.md`. The one-way bind above is unchanged: a hook still
  cannot silently rekey a bound session, it can only report that the session it authenticated
  as is now writing somewhere else, which ends the run it was bound to. A root agent's
  `agent_run_id` is otherwise pinned to its session id by adoption; `agent_run_seq > 0` is what
  tells the restart path that a differing run id is the daemon's own successor rather than the
  misattribution it repairs. That trust is bounded: a rolled conversation that a *sibling's*
  root identity claims is corrupt by definition (two panes cannot write one transcript), so
  adoption falls back to the pane's own spawn anchor, quarantines the corrupt run row, and
  clears the roll counter — and a rolled Claude root keeps a standing claim on the
  conversation named by its own mux id, so the rightful owner's own corruption cannot hide
  the conflict. `agent_lifecycle_id` only ever moves on CLI-confirmed rollovers (never on a
  heuristic switch), which is what makes it a trustworthy heal target.
- **Resuming a conversation is the mirror image, and inherits its run.** A rollover is one PTY
  moving to a new conversation; a resume is one conversation moving to a new PTY. Claude's
  `--resume` appends to the same transcript under the same id, so the new pane continues the
  run it resumed rather than opening a second one over one file, and `spawn_agent_run_id` —
  immutable spawn evidence, the counterpart of `agent_run_seq` — is what tells adoption that
  this differing run id is inherited rather than the misattribution it repairs. The inheritance
  lapses on its own: a later rollover mints a run of the pane's own, which no longer matches.
  Two bounds keep it honest. It is refused when a sibling's spawn claim covers the same
  conversation (same rule as a rolled conversation, same fallback to the pane's own anchor).
  And a run id repaired away this way is *dropped, never quarantined* — it names the resumed
  conversation's own row, so quarantining it would delete a conversation's real history over a
  dispute about which conversation this PTY is on. The pane's ownership evidence is unchanged
  by any of this: an unrolled resume still proves its claim through its spawn id, so the sweep
  still never heals it off the conversation it was spawned to continue. Codex mints a new
  rollout id on resume, so there the pane starts a genuinely new conversation and run.
  Before that new run is created, resume resolves the source row's effective visible name: a manual name remains pinned, while an auto-generated title becomes the new pane's initial auto-nameable name instead of falling back to `codex-<id>`.
- **A rollover onto a conversation a live sibling owns is refused outright.** The collision is
  prevented rather than repaired, because repair does not work here: a rollover moves
  `agent_lifecycle_id`, so a pane that followed an in-CLI `/resume` onto a sibling's live
  conversation would then satisfy the ownership test itself, and the sweep — seeing two
  rightful owners — heals neither. Verified live: pane B resumed pane A's conversation from the
  `/resume` picker, `identity_collision_detected` fired in 1.1 s, and both panes then reported
  A's conversation and its tokens indefinitely. The refusal keeps the pane's own identity
  intact, emits `conversation_rollover_refused`, and fails the pane's observation closed
  (`observation_stale_since`) — its CLI genuinely is writing elsewhere, so the pane's status is
  no longer trustworthy even though its identity is. A sibling only counts as the owner when
  its own claim is supported by identity evidence, so deferring to a *misattributed* sibling
  cannot freeze corruption in place.
- **One live session per conversation, continuously enforced.** The state watchdog runs an
  identity sweep each pass as the backstop for corruption that predates the refusal above: any
  two live agent sessions claiming one `(backend, native_id)`
  are logged and emitted as `identity_collision_detected`, and a Claude member whose claim is
  unsupported by identity evidence (its own mux id, its CLI-confirmed lifecycle anchor, or an
  unrolled resume's spawn id) is healed back to its strongest anchor — observer rebound to the
  anchor conversation's deterministic transcript, history row repaired or the corrupt run row
  quarantined (`session_identity_reconciled`, trigger `live_sweep`). Collisions also surface
  in `/api/diagnostics/status-health` as `identity_collisions` and raise its alarm.
- The root process's OS creation time is captured at spawn (`SessionRecord.root_started_at`).
  A pid alone is not an identity on Windows, and exited sessions stay listed with their pid
  intact, so every later consumer of `record.pid` pairs the two.

## Key files

- `src/swe_mux/session.py`
- `src/swe_mux/pty_host.py`
- `src/swe_mux/supervisor.py`
- `src/swe_mux/supervisor_client.py`
- `src/swe_mux/scrollback.py` (`tail_bytes()` reads the end without joining; `tail()` is
  the replay budget; `bytes()` is full retention)
- `src/swe_mux/git_projects.py`
- `src/swe_mux/spawn_contract.py`
- `src/swe_mux/adapters/`
- `frontend/src/App.tsx`
- `frontend/src/TerminalPane.tsx`

## Relates to

- `projects.md`: canonical ownership and Project registration.
- `project-resources.md`: Project-owned notes.
- `history.md`: durable agent-run lifecycle.
- `project-actions.md`: trusted multi-session task launch.
