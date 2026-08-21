# Backend: PTY host, supervisor, and sessions

Index: `../packages.md`.
Design: `../../../design/features/sessions.md`, `../../../design/features/session-recovery.md`, `../../../design/features/terminal-input.md`.
Supervisor design record: `../../../development/archive/SESSION_PRESERVING_RELOAD.md`.

Each entry lists what the module owns, then **Not:** what it deliberately does not.

## `supervisor.py`

The standalone PTY supervisor process: ConPTY plus read loop plus authoritative scrollback ownership, spawn-time initial scrollback seeding, the reaper Job, the loopback IPC server, the discovery file, the single-instance mutex, and reap-all teardown.

It must stay small and near-frozen, because it cannot be hot-updated without killing every live session, so volatile code belongs in the daemon.
It imports only `pty_host.py`, `scrollback.py`, and the platform seams those two stand on (`host_platform.py`, `pty_backend.py`, `pty_backend_windows.py`, `process_reaper.py`, `win_jobobj.py`).

**Not:** HTTP composition, SQLite, orchestration, or observation - anything volatile.

## `supervisor_client.py`

The daemon-side supervisor connection (framing, RPC, output dispatch), the `RemotePtyHost` PtyHost facade, discover-or-spawn, metadata and initial-scrollback transport, and `kill_server`.

**Not:** ConPTY creation, or the session registry.

## `pty_host.py`

The platform-neutral half of one pseudoterminal session: the reader thread and its graduated poll ladder, output coalescing, the backpressure handoff onto the event loop, `merge_environment`, and the resize, exit-status, and release contracts.

**Not:** how a pseudoterminal is allocated or a child started on it (`pty_backend*`), lifetime ownership (`process_reaper`), HTTP, SQLite, or layout.

## `scrollback.py`

The byte-exact scrollback ring (append, seed, replay cursoring) shared by daemon sessions and the supervisor.

**Not:** subscribers or persistence.

## `session.py`

The live session registry, immutable root-provider identity, nested promotion and demotion, transcript ownership, spawn and stop, PTY fanout, bounded replay, the interactive versus one-shot exit lifecycle, and supervisor-session adoption and repair.

**Not:** provider transcript parsing, or Project mutation.

## `session_recovery.py`

The durable session registry (`session_recovery` table, open marker, redaction) plus daemon-side terminal checkpoints: a framed append log and an atomic rebased checkpoint under `<data_dir>/recovery/<sid>/`, a crash-tolerant reader, and retention and orphan sweeps.

**Not:** the supervisor process - the PTY supervisor is the *primary* recovery path and a change there reaps every live session, so nothing here may run in it - nor the session registry or HTTP handlers.

## `session_attachments.py`

Trusted Project and worktree storage selection, filename normalization, image content classification, the persistent `.swe-mux/attachments/` layout, atomic writes, and per-file and per-session quotas.

**Not:** HTTP multipart parsing, PTY insertion, provider parsing, or retention cleanup.

## `terminal_arbitration.py`

Pure multi-device rules for one shared PTY.

- Input-ownership claims: gesture beats passive; passive cannot displace an actively typed-into owner, come from a hidden window, or cross to the leading device class - and does cross from a trailing one when the claimant's class leads.
- Epoch and release bookkeeping.
- The arbitrated geometry: the owner's viewport, else the smallest visible one.

`server.py` records every decision in the session's bounded claim log and stops answering a connection's repeated passive claims for a second after refusing one.

**Not:** sockets, PTYs, telemetry, or *which* device is in use - `device_presence.py` answers that, `session.py` holds the state, and `server.py` applies the decisions.

## `composer_input.py`

A pure estimate of unsent composer text from the bytes written to a PTY: what each write does to it (typed, erased, submitted, discarded), bracketed-paste state carried between writes, and the empty-to-non-empty crossing that is the only reportable event.

Which keys count as a discard is a *parameter* (`clear_keys`) rather than a constant, supplied from `HarnessDescriptor.composer_clear_keys`.
Ctrl+U is a line kill in Claude Code, so a fixed set reads a one-line kill on a four-line draft as "now empty" - the false safe this module exists to avoid.
The declared sequence is matched against the raw frame, since Claude's is itself an escape sequence and the escape stripper would have eaten it.

`server.py` calls it wherever `input_revision` is advanced, and `session.py` clears it when a turn opens or the session ends.

**Not:** reading the composer, which nothing can, or authorizing anything - `delivery_readiness.py` keeps its own coarse boundary and never consults this.

## `spawn_contract.py`

Spawn field validation: bounded env, cwd containment, Claude marker scrubbing, and model resolution against the harness registry (`resolve_spawn_model`, `apply_spawn_model`).

**Not:** Project ownership, since the caller supplies the root, or which backend a request resolves to.

## `spawn_probe.py`

Proof that a pane survived its own spawn: a settle window with optional caller-supplied proof-of-life, discard, bounded retry, and the harness's own dying output cleaned of terminal control bytes.

**Not:** why it died, which it never classifies; whether to retry, which the caller decides; or ongoing session health, which the watchdog owns.

## `session_resume.py`

The single resume authority, shared by the History route and the scheduled-resume path.

- The structural refusals: `not_agent`, `native_id_missing`, `cwd_missing`, `transcript_unavailable`, `target_project_missing`, `adapter_missing`.
- The two claim checks: a mux pane on the conversation, and `conversation_holder` for processes mux does not own.
- Whether the resumed pane inherits the run row, which is the adapter's answer and never this module's.
- Proof that the pane survived its settle window.
- `fork_run` for a fork-then-resume.
- `resolve_latest_run`, which follows rollovers and `resume` edges and deliberately not `branch`, `review`, or `handoff` ones.

**Not:** layout attachment and lineage, which belong to the callers because only they differ on it; HTTP; trigger arithmetic; or the fork writer's byte work (`transcript_fork.py`).

## `session_titles.py`

The one display-name rule every surface that names a session reads: the run id an annotation is keyed by (`agent_run_id or id`, which is also the History row id), the generated-title lookup, and the record and row name resolvers.
A generated title wins only while the session is `auto_named`; a live record's flag is a bool and a History row's is SQLite `0`/`1`.
Titles are queried **by id in bounded chunks**, never swept off the newest N annotations, because a caller decorating a long tail of runs would otherwise render older ones as never titled.
The browser twin is `frontend/src/sessionNames.ts`.

**Not:** storage, HTTP shape, which surfaces call it, or what a session is *named* - that is a rename or the titler.
