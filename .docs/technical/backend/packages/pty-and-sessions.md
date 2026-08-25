# Backend: PTY host, supervisor, and sessions

Index: `../packages.md`.
Design: `../../../design/features/sessions.md`, `../../../design/features/session-recovery.md`, `../../../design/features/terminal-input.md`.
Supervisor design record: `../../../development/archive/SESSION_PRESERVING_RELOAD.md`.

Each entry lists what the module owns, then **Not:** what it deliberately does not.

## `supervisor.py`

The standalone PTY supervisor process: ConPTY plus read loop plus authoritative scrollback ownership, spawn-time initial scrollback seeding, the reaper Job, the loopback IPC server, the discovery file, the single-instance mutex, and reap-all teardown.

It must stay small and near-frozen, because it cannot be hot-updated without killing every live session, so volatile code belongs in the daemon.
It imports only `pty_host.py`, `scrollback.py`, `nested_job.py`, and the platform seams those stand on (`host_platform.py`, `pty_backend.py`, `pty_backend_windows.py`, `process_reaper.py`, `win_jobobj.py`).

Three rules the spawn path turns on.

**A session id is the deduplication key, and a reservation exists from the instant a `spawn` is accepted.** A duplicate `spawn` for a reserved or live id returns the first attempt's outcome (marked `deduped`) instead of erroring or starting a second process, and `spawn_status` answers `unknown` / `reserved` / `live` / `exited` for any id.
That query is what a daemon whose spawn reply was lost asks before falling back, and only `unknown` makes falling back safe - the id was never reserved, so nothing can be duplicated.
A *failed* spawn releases the reservation, so a retry really retries rather than being deduplicated against a session that never existed.
Neither addition is gated on `PROTOCOL_VERSION`, for the same reason `job_pids` is not: an older supervisor answers "unknown message type" and the daemon degrades, where a version bump would stop a new daemon driving a running older supervisor and orphan every live session.
A deduplicated spawn deliberately does **not** subscribe the asking connection - registration and the scrollback snapshot are one indivisible step in `subscribe`, and splitting them drops or duplicates the boundary chunk.

**Teardown order is the reap.** A closing flag goes up before the listener closes, new spawns are refused, `_background_tasks` are drained, a spawn that completed anyway during shutdown is stopped explicitly, and only then do the per-session and global Jobs close.
Closing the Job first is what let a child created moments later be assigned to nothing and killed by nothing: a reap that reported success while an agent kept running.
The drain is bounded and overrunning it is logged, because what escapes past the bound is exactly that orphan.

**The two wire directions are bounded differently.** Daemon-inbound frames carry header and payload caps; an unauthenticated connection gets a small header cap, a payload allowance of zero (so `hello` is payload-free by enforcement rather than convention), and a deadline.
The reverse direction is left unbounded on purpose - a legitimate `subscribe` reply carries a whole scrollback buffer, sized by the daemon's `scrollback_bytes`.
A refused or malformed frame closes the connection: the stream is desynced at an unknown offset, so there is nothing to resynchronise to.

**Not:** HTTP composition, SQLite, orchestration, or observation - anything volatile.

## `nested_job.py`

The nested per-session process owner: create a child reaper beneath the daemon-wide one, take ownership of the root pid, and report the outcome as the assignment-string suffix that process forensics read back (`;nested_session_job_assigned`, `;nested_session_job_failed:<error>`).

It never raises - a session whose cleanup is weaker than intended still beats no session - and it never reports ownership it does not have, so a job created but not assigned is closed rather than kept.

It is inside the hash-gated supervisor source closure and imports nothing but `process_reaper`, which was already in that closure, so sharing it did not widen what a supervisor rebuild covers.
That is the property that made the extraction safe: the same six lines had been duplicated near-verbatim in `supervisor.py` and `session.py`, including the strings.

**Not:** deciding *whether* a session should be owned, the daemon-wide reaper's lifetime, or any platform detail (`process_reaper` and the backends own those).

## `supervisor_client.py`

The daemon-side supervisor connection (framing, RPC, output dispatch), the `RemotePtyHost` PtyHost facade, discover-or-spawn, metadata and initial-scrollback transport, and `kill_server`.

Three rules this module exists to keep:

- **Tri-state liveness.**
  `Liveness` is `alive`/`dead`/`unreachable`, and `liveness_of()` reads it from any PtyHost-shaped object (an in-process host is two-state by construction).
  `_alive` is cleared only by a definitive `pty_exit` or a confirmed supervisor death, so `isalive()` ("not dead") never reports a running child as gone because a socket dropped.
- **Dispatching a frame never waits.**
  RPC replies, output, and `pty_exit` for every session share one read loop, so output goes to a per-session staging deque drained by a per-session pump.
  The deque is bounded by that session's scrollback budget (floor 1 MiB) and drops oldest-first with a counter and a rate-limited log.
  It never drops the newest entry, which is what guarantees the exit sentinel is delivered.
- **Irreversible actions ask first.**
  `spawn_status`/`resolve_spawn_outcome` answer what became of a spawn whose reply was lost, separating "the supervisor says X" from "this supervisor cannot be asked" (`unsupported`) and "the question could not be delivered" (`indeterminate`).
  `_terminate_supervisor` verifies pid creation time against the discovery file's `started_at` plus a supervisor command line for this config, and fails closed.

**Not:** ConPTY creation, the session registry, or the decision of what a lost spawn reply means for a session (`session.py` owns that policy).

## `pty_host.py`

The platform-neutral half of one pseudoterminal session: the reader thread and its graduated poll ladder, output coalescing, the backpressure handoff onto the event loop, `merge_environment`, and the resize, exit-status, and release contracts.

Two rules the reader turns on.

**The end-of-output sentinel waits like a data chunk does.** It is the only signal that a session's output ended, so losing it under a momentarily full queue leaves a phantom-alive session that never emits `pty_exit`, a supervisor that lingers because it still counts a live session, and a pane that never resolves.
It used to give up after two seconds and swallow the exception.
The single bounded case is a deliberate teardown - `stop()`/`release()` set `_stop`, and removing a supervised session cancels its fanout, the only consumer - where waiting would park the reader thread for the life of the process; that drop is logged.

**A swallowed read failure is counted and logged.** `read_errors`, `last_read_error`, and `last_read_error_at` ride the supervisor's session inventory, because the daemon cannot see this reader thread and an alive-but-permanently-silent session is otherwise indistinguishable from an agent that is merely thinking.
The reader still continues past a read error on purpose - a transient failure on a live pseudoterminal is not a reason to end a session - and the log is rate-limited (first occurrence, then one line per interval carrying the counts) so a storm is diagnosable without becoming the storm.

**Not:** how a pseudoterminal is allocated or a child started on it (`pty_backend*`), lifetime ownership (`process_reaper`, `nested_job`), HTTP, SQLite, or layout.

## `scrollback.py`

The byte-exact scrollback ring (append, seed, replay cursoring) shared by daemon sessions and the supervisor.

**Not:** subscribers or persistence.

## `session.py`

The live session registry, immutable root-provider identity, nested promotion and demotion, transcript ownership, spawn and stop, PTY fanout, bounded replay, the interactive versus one-shot exit lifecycle, and supervisor-session adoption and repair.

**A session's two long-lived loops are owned on the way in and drained on the way out.**
`_start_session_task` registers the fanout and the ticker with the discard callback every sibling site already had, and `_mark_ended` ends them through `_drain_session_loops` before the `Session` can lose its last holder.
Ownership alone is not enough, which is what the 48 `Task was destroyed but it is pending!` ERRORs between 2026-08-19 and the D3 soak were: the fanout blocks on `output_queue.get()`, and on every end that did not arrive *as* that queue's end-of-output sentinel (a supervisor that died, an adopted session found dead, a hard `stop()` whose sentinel never came) nothing was ever going to feed it again.
Two ordering rules make the drain safe. It **waits before it cancels** (`SESSION_LOOP_DRAIN_SECONDS`), because the sentinel is queued *behind* the pane's last bytes and cancelling on sight drops them out of the scrollback; and it **excludes the calling task**, because `_mark_ended` runs inside the fanout itself on the ordinary end path.

**Not:** provider transcript parsing, or Project mutation.

## `session_recovery.py`

The durable session registry (`session_recovery` table, open marker, redaction) plus daemon-side terminal checkpoints: a framed append log and an atomic rebased checkpoint under `<data_dir>/recovery/<sid>/`, a crash-tolerant reader, and retention and orphan sweeps.

**Not:** the supervisor process - the PTY supervisor is the *primary* recovery path and a change there reaps every live session, so nothing here may run in it - nor the session registry or HTTP handlers.

## `session_attachments.py`

Trusted Project and worktree storage selection, filename normalization, image content classification, the persistent `.swe-mux/attachments/` layout, atomic writes, and per-file and per-session quotas.

**Not:** HTTP multipart parsing, PTY insertion, provider parsing, or retention cleanup.

## `session_media.py`

What a session may store and when it stops being stored: the accepted media types and their magic-byte signatures, the per-session media directory, and the two expiry sweeps (session media at `SESSION_MEDIA_TTL_SECONDS`, Preview screenshots at `PREVIEW_SHOT_TTL_SECONDS`).

Validation is by signature rather than by declared type, because the declared type is the caller's claim.

**Not:** the attachment path (`session_attachments.py`, which is durable and Project-scoped), multipart parsing, or the loops that call the sweeps (`server.py` owns those).

## `terminal_arbitration.py`

Pure multi-device rules for one shared PTY.

- Input-ownership claims: gesture beats passive; passive cannot displace an actively typed-into owner, come from a hidden window, or cross to the leading device class - and does cross from a trailing one when the claimant's class leads.
- Epoch and release bookkeeping.
- The arbitrated geometry: the owner's viewport, else the smallest visible one.

`routes/pty.py` records every decision in the session's bounded claim log and stops answering a connection's repeated passive claims for a second after refusing one.

**Not:** sockets, PTYs, telemetry, or *which* device is in use - `device_presence.py` answers that, `session.py` holds the state, and `routes/pty.py` applies the decisions.

## `composer_input.py`

A pure estimate of unsent composer text from the bytes written to a PTY: what each write does to it (typed, erased, submitted, discarded), bracketed-paste state carried between writes, and the empty-to-non-empty crossing that is the only reportable event.

Which keys count as a discard is a *parameter* (`clear_keys`) rather than a constant, supplied from `HarnessDescriptor.composer_clear_keys`.
Ctrl+U is a line kill in Claude Code, so a fixed set reads a one-line kill on a four-line draft as "now empty" - the false safe this module exists to avoid.
The declared sequence is matched against the raw frame, since Claude's is itself an escape sequence and the escape stripper would have eaten it.

`newline_keys` is the same shape of parameter, from `HarnessDescriptor.composer_newline`.
ESC+CR is not a control sequence the escape stripper matches, so without naming it the bare CR survived and every composer-newline write classified as a submit - the same false safe, already live through the rail's Markdown divider and code-fence buttons.

It also **builds** an insertion (`composer_insertion`), not only classifies one, so the construction and the reading of these bytes cannot disagree.
The body is a bracketed paste with newlines as CR; a *leading* newline run is lifted out and emitted as `newline_keys` presses ahead of it when the harness declares `paste_leading_newline_submits`, because Codex reads a paste's first newline as Enter and submits whatever the composer held (measured 2026-08-22 against v0.149.0).
`harness.composer_insertion_rules(backend)` resolves the pair, `server._composer_insertion` is the daemon's one caller-facing wrapper, and `composerInsertion.ts` is the browser's mirror.

`routes/pty.py` and `routes/terminal.py` call it wherever `input_revision` is advanced, and `session.py` clears it when a turn opens or the session ends.

**Not:** reading the composer, which nothing can, or authorizing anything - `delivery_readiness.py` keeps its own coarse boundary and never consults this.
Not the queue's delivery bytes either: `prompt_queue.delivery_payload` builds those on top of this, and *drops* a leading newline rather than lifting it, because a delivery has passed the readiness gate and is about to submit.

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
