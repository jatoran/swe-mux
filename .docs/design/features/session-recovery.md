# Cold session recovery

## What it is

Durable per-session state that outlives both the daemon and the PTY supervisor, so sessions whose
processes died without anyone recording it come back as visible, dead, resumable rows instead of
vanishing.

The PTY supervisor already keeps live sessions running across a daemon restart
(`features/sessions.md`, "Session-preserving reload").
This is the fallback behind it, for the cases the supervisor cannot cover: its own death, a force
close, a power loss, or a daemon running with `pty_supervisor_enabled` off.

## Model

- A **cold session** is a `Session` in the registry with `state="crashed"` and `record.cold=True`.
  Its `PtyHost` is prepared but never spawned, which is already the exact contract a dead pane
  needs: `isalive()` false, `pid` -1, `release`/`stop` no-ops, writes refused.
- `cold` is a flag beside a terminal state rather than a new `SessionState`.
  Dozens of consumers gate on `state in {"exited","crashed"}` - delivery, auto-delivery, attention,
  identity claims, prompt queue, MCP - and a cold session must be excluded from every one of them.
  A new state would mean auditing all of them; the flag makes the exclusion structural, and only
  the UI and the revive paths ever ask about it.
- Two independent layers, and either can work without the other.
  - **Layer A, the registry.** The per-session metadata blob - the same one
    `SessionManager._session_meta` mirrors into the supervisor - written to SQLite with an *open*
    marker. This is what brings a session back at all, and it works with no terminal bytes.
  - **Layer B, terminal checkpoints.** Bytes on disk, so a recovered pane shows what it printed.
    Only affects what the pane displays.

## The open marker

- A row with no `closed_at` describes a session nobody was able to say goodbye for.
- The row is written on the spawn registration task, *before* any history write, because until it
  exists the session is unrecoverable and a crash inside that window is exactly what this feature is
  for.
  It is one small INSERT on the recovery store's own worker; the history writes share SQLite with
  transcript reconciliation and can queue behind a large import.
- `_mark_ended` closes it, after `_await_registration`, so an open and a close can never race.
- Shutdown intent decides the rest, with no special casing:
  - `quit` stops every session, so every row closes.
  - `detach` leaves supervised sessions running and their rows open on purpose - they are still
    running, and the next daemon reaches them through the supervisor.
  - A crash closes nothing, which is the whole signal.

## Restore

- `SessionManager.restore_cold_sessions()` runs **after** `adopt_supervisor_sessions()`, and that
  order is the correctness argument.
  An open row for a session adoption already claimed describes a live process; restoring it as cold
  would present a running agent as a dead pane.
- A row whose Project no longer exists is closed rather than restored: there is nowhere to render
  it, and leaving it open would have every later boot reconsider it.
- The restored record is scrubbed of everything that described a running process: standing
  activity, the open turn, awaiting/idle reasons, a pending interrupt, and the exit code, which is
  genuinely unknown rather than whatever was last observed.
- A cold session gets a **fresh random hook secret** and an empty MCP token.
  Neither is persisted: the process they authenticated is gone.
  An empty hook secret would be worse than a random one - `compare_digest("", "")` is True, so a
  hook sending no header would authenticate.
- Restored bytes are fed through the same mode parsers the live session used
  (`screen`, `bracketed_paste`, `sticky_modes`, `osc_signals`), exactly as supervisor adoption does.
  The modes a child sets once and never restates are only recoverable by re-reading the bytes that
  set them, and without this the attach preamble would have nothing to restate.
- **A cold session never serves a delta attach.**
  Its ring was rebuilt from disk, so its positions describe a different stream from the one any
  client parsed before the crash.
  A `since` from that stream can land inside range by coincidence, and a delta across the boundary
  is appended to a terminal that was never reset - silent corruption, in the one case where the
  operator is reading the pane for evidence.

## Checkpoints

- **Written by the daemon, not the supervisor.**
  The daemon already holds a byte-exact mirror of the authoritative buffer (`Session.scrollback`,
  fed by the supervisor subscription), and the supervisor is deliberately near-frozen because
  shipping a change to it reaps every live session
  (`development/archive/SESSION_PRESERVING_RELOAD.md` §8).
  Both processes die together in the case this exists for, so a daemon-side writer captures the same
  bytes at the same moment.
- **Accepted residual:** a daemon that dies and stays dead while the supervisor keeps sessions
  running leaves a checkpoint that ages until the next daemon attaches.
  The checkpoint records when it was taken and the pane says so, rather than presenting itself as
  current.
- **Sampled, not teed.**
  Each pass asks every live session for `scrollback.bytes_since(cursor)` rather than intercepting
  output.
  Zero cost on the PTY hot path, naturally coalesced, and a ring that wrapped past the cursor is
  detectable (`position - cursor > scrollback.size`) and forces a rebase instead of splicing a hole
  into the stream.
- **Only harnesses whose retained bytes are a transcript are checkpointed.**
  Two independent tests, because the descriptor and the live stream answer different questions.
  - The descriptor says what a harness always does: an alternate-screen CLI's bytes are a
    differential frame stream whose bounded window reconstructs to a blank or half-drawn screen
    (`harness.replay_needs_repaint`), and a repaint-heavy one wraps its own ring until the window
    holds no transcript at all (`harness.repaints_scrollback`).
    Neither can be repaired without a live child to pulse, which is the one thing a cold session
    does not have.
  - The live screen tracker catches what the descriptor cannot: a plain shell that happened to be
    inside `vim`, `htop`, or `less` at the moment of the crash is in exactly the same position.
  - A skipped session still gets its registry row, and the reason is recorded so the pane can say
    why it is empty instead of looking broken.
    For an agent the real recovery is the conversation transcript, which the pane points at.

### On-disk format

Per session, under `<data_dir>/recovery/<session_id>/`:

| File | Contents |
| --- | --- |
| `checkpoint.bin` | Base bytes: the ring tail at the last rebase |
| `checkpoint.json` | `{generation, position, cols, rows, captured_at}`, written tmp+rename |
| `output.log` | Framed appends since that base |

- Log framing is `magic SMKL + u8 version + u32le generation`, then
  `u8 kind + u32le length + payload` frames (`0x01` output, `0x02` resize).
- **Framed rather than raw because a crash can tear the final append.**
  Length prefixes make a torn tail detectable, so a restore truncates at the last complete frame
  instead of replaying half an escape sequence into a terminal.
- **The generation is what makes a rebase crash-safe.**
  The log is truncated to the new generation *before* the metadata naming it lands, so the two can
  only disagree in the safe direction: a crash in between leaves a log whose generation matches
  nothing the reader asks for, and it is refused rather than replayed onto the wrong base.
- Deliberately not fsynced. The point of the framing is that a torn tail is *detectable*, not that
  it never happens; paying a flush every interval per session to narrow a window the format already
  tolerates is the wrong trade.
- Resize frames carry the geometry those bytes were written under, and the log's last word outranks
  the checkpoint's.
  Two stores that can disagree after a crash is why geometry is framed into the log rather than only
  held in the registry row.
- No `clear` frame exists. Byte-exact replay makes a clear implicit: it is already in the stream.

## Bounds and retention

- `session_recovery_checkpoint_bytes` (256 KiB) is what a session keeps, far below `scrollback_bytes`
  because a cold pane is a post-mortem rather than a session. `0` keeps Layer A and stores no bytes.
- The append log is rebased onto a fresh checkpoint before it exceeds its cap, bounding both disk
  per session and restore replay cost.
- `session_recovery_retention_days` (7) ages out **closed** rows.
  `session_recovery_max_sessions` (40) caps **open** ones by count, newest first, because those are
  what come back as cold sessions and a machine that crashes repeatedly would otherwise accumulate
  them forever.
- Terminal bytes are whatever the child printed, which includes anything a command echoed.
  The directory is created 0700, it is its own `storage_usage` bucket, and the diagnostics export
  carries the recovery store's counters but never its bytes - the same reason scrollback itself is
  not in that bundle.
- Credentials are never persisted (`session_recovery.redact_meta` drops `hook_secret` and
  `mcp_token`).

## Dismissal and the way back

- **Only an explicit dismissal deletes recovery data.**
  An ordinary end closes the row and keeps the bytes: "this session finished" and "I am done looking
  at this session" are different statements, and only the second is a reason to throw away what it
  printed.
  `DELETE /api/sessions/{id}` and a relaunch that supersedes a cold session both discard.
- A **cold agent** is resumed: `POST /api/history/{run}/resume` already reopens the conversation,
  and a cold row carries the run id it needs.
- A **cold shell** is relaunched from its recorded argv (`POST /api/sessions/{id}/relaunch`).
  This is the one deliberate widening of the `relaunchable` gate, which exists to keep relaunch away
  from a live lifecycle - a cold session has none.
  Cold *agents* stay excluded: replaying an agent's argv would start a fresh conversation while
  re-injecting the old one's `--session-id`, where the operator asked to return to the conversation.
  A relaunched cold shell does not inherit `relaunchable`, which drives an affordance that only makes
  sense for a task step whose argv the daemon vouches for.

## Ended panes are readable

Recovering a session is pointless if its pane is destroyed the moment it dies, so the same change
makes an ended session's pane survive.

- The browser's layout reconcile keeps a leaf for every session the daemon still holds, ended ones
  included.
  A session leaves the layout when it leaves the fleet - killed, or dismissed - which the kill
  tombstones already express.
  Before this, a session that ended on its own kept its sidebar row and lost its tab in the same
  instant, and the pruned layout was written back, so the pane showing what it printed was destroyed
  at exactly the moment somebody wanted to read it.
- Ended and cold sessions can be opened in a pane and in splits.
- The pane is read-only on both sides.
  The daemon refuses input for a terminal-state session (`server.session_accepts_input`) rather than
  letting `PtyHost.write` raise into a 500 or a dropped socket; the client stops sending it, and does
  not claim the keyboard away from a live pane on another device while doing it.
- Dismissing an ended session needs no kill confirmation: confirmation guards against destroying
  work, and there is none left to destroy.

## Key files

- `src/swe_mux/session_recovery.py` (store, framed log codec, checkpoint reader)
- `src/swe_mux/session.py` (`restore_cold_sessions`, `_build_cold_session`, `_attach_recovery`)
- `src/swe_mux/history.py` (`close_orphaned_runs`)
- `src/swe_mux/server.py` (boot restore, `session_accepts_input`, relaunch widening)
- `frontend/src/coldSession.ts`, `frontend/src/EndedPaneBanner.tsx`

## Relates to

- `features/sessions.md`: the live lifecycle, and supervisor adoption as the primary recovery path.
- `features/history.md`: the durable agent-run lifecycle a cold agent resumes into.
- `development/archive/SESSION_PRESERVING_RELOAD.md`: why the supervisor cannot be the writer.
