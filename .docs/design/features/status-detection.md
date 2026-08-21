# Agent status detection and regression defense

## What it is

The user-visible session status (`starting | running | working | idle | awaiting | exited |
crashed`, plus the `awaiting` sub-reason `approval | question | elicitation | rate_limit`)
is a contract, not a heuristic: every state names the positive evidence that may set it,
every transition is ledgered with that evidence and a proven/inferred classification, and
the whole surface is reproduced from the golden corpus so status regressions fail CI.

Three axes stay separate and are never collapsed:

- **SessionState** (+ `awaiting_reason`) — what the agent is doing, rendered per session.
- **`delivery_state`** — whether typed delivery would be safe (`delivery-readiness.md`).
- **Attention** - unread/pinned UX state (`turn_seq` / `read_turn_seq` on the record,
  rendered by `sessionAttention.ts`).
  It reads the transition contract but never feeds it: turn completions are counted *from*
  transitions, and a row is only unread once its agent has settled (`idle` or `awaiting`), so
  unread means "finished, and unseen" rather than "producing output".
  The counter and its acknowledgement are server-side because a per-browser mark could
  neither survive a reload nor follow the user to a second device.

## The detection ladder

The daemon sits a level above the CLIs and reads every layer it owns — not only hooks.
Per signal class, every layer feeds the same ledger with its own `source` string:

| Layer | Source tag | What it may do |
| --- | --- | --- |
| CLI side state (`~/.claude/sessions/<pid>.json`) | `cli-state` | Corroborate + identity (counters, ledger entries) + the live `cwd` that re-finds a relocated transcript. Claude `waiting` conservatively vetoes raw PTY evidence that would hide an approval, but never initiates a transition by itself. |
| Hooks (incl. `SubagentStart`/`SubagentStop`) | `hook` | Turn boundaries, blocks, identity (priority 2), and - for a hook speaking for the session's own conversation - the live `cwd` and the `transcript_path` the CLI says it is writing. |
| Transcript records | `transcript` | Ordered turn evidence (priority 1) + standing-activity extraction. |
| PTY screen classifier | `pty` / `watchdog-pty` | Recoveries, vetoes, the startup-dialog rule, drift self-check. |
| Process tree (ProcessInspector) | `process` | Liveness; `background_tasks` fast-clear (a vanished process cannot still be working). |
| Daemon lifecycle | `daemon` | Spawn/promotion/demotion/exit (force). |

**The `cli-state` layer** (`src/swe_mux/cli_state.py`, polled on the 5 s watchdog cadence,
stat-then-parse-on-change): Claude publishes per-process state files carrying
`{sessionId, cwd, pid, procStart, kind, status, statusUpdatedAt, updatedAt, version}`
(verified 2.1.220), plus `parkedJobId` and `entrypoint` from 2.1.227.
Observed `status` values are `busy`, `idle`, and `waiting` — measured 2026-08-01, the
file reads `waiting` for the duration of a permission dialog. Files map to sessions by
conversation id.
What it feeds:

- **Status corroboration**: a *settled* contradiction (CLI `busy` while mux `idle`, CLI `idle` while mux `working`, both sides at least 10 s old) counts `cli_state_disagrees` once per standing fact and ledgers it (`kind: "cli_state"`).
  `waiting` is outside that disagreement counter and is recorded as a `layer_reading`.
  It also conservatively overrides a raw PTY `working` result to `approval`, because measured Claude behavior makes `waiting` exact for the permission-dialog lifetime while the append-only PTY tail can contain later parallel redraws.
  This override can preserve or extend an existing approval but cannot initiate a state transition by itself.
- **Transcript relocation**: the file's `cwd` is the CLI's *live* working directory, which
  the spawn cwd stops describing the moment the agent enters a worktree. It is what
  `_relocated_transcript_candidate` re-derives a moved transcript's path from.
- **Identity corroboration**: a file in exactly one live session's cwd, bound to a
  conversation no live session owns, updated after that session's run began, is a nested
  child CLI observed deterministically — the signal the `bb81463` incident had to infer
  from hook `source` fields. Counts `nested_children_observed`, once per foreign
  conversation. Ambiguity (two live sessions in one cwd) stands down.
- **Backgrounded conversations**: `parkedJobId` names the job in
  `~/.claude/jobs/<id>/state.json` that a pane's conversation has moved into, and that
  job file publishes the conversation (`sessionId`) and the transcript (`linkScanPath`)
  it writes. The pane keeps its spawn conversation id and its own transcript stops
  growing, so without following the move the pane is observationally dead: measured
  2026-08-10, one sat displayed idle and nameless for 42 minutes while its job ran a
  full task. No hook can report this — the background CLI runs under a shared
  `claude daemon run` tree, so it is not a child of the PTY and speaks for neither this
  pane's conversation nor its hook credentials. The move is therefore applied as a
  **rollover on the CLI's own authority** (`SessionManager._follow_parked_conversation`,
  reason `conversation_backgrounded`), the same standing as a SessionStart-reported
  `/clear` and explicitly not the transcript-switch heuristic a Claude pane forbids.
  Guarded on the state file being the pane's own *and* `interactive`, the job naming a
  different conversation, and the job standing in the pane's own cwd; a move the pane
  refuses is retried `PARKED_MOVE_ATTEMPTS` times and then left alone, because each
  attempt stops and restarts the observer. Once the roll lands, the pane's retired
  conversation is excluded from the nested-child count: the interactive CLI's file still
  names it, and it is the pane's past rather than a child of it.
- The session's own file snapshot is surfaced as `cli_state` on the state-log endpoint.
- **Conversation ownership** (`conversation_holders`, not a detection input): the same
  directory answers who currently holds a conversation, which is what makes a resume of it
  impossible — a CLI opens a conversation once and answers a second opener by exiting.
  Read fresh at the moment of the resume rather than sampled on the poll, and shares neither
  the cache nor the cadence.
  A holder is reported only for a *proven* process: the pid must still be running, with a
  creation time consistent with the file's `startedAt`.
  The window is asymmetric because the CLI stamps its file after the process exists —
  measured 2026-08-14 across 13 live CLIs, 0.42 s to 1.31 s behind — so it tolerates lag and
  rejects a lead, which is what pid reuse looks like.
  An unprovable pid yields no holder: a missed holder degrades to the ordinary spawn failure
  `spawn_probe` reports, while a phantom one would make a resumable conversation permanently
  unresumable.
- **Deliberately absent**: a staleness alarm. `updatedAt` is a status-change timestamp,
  not a heartbeat — measured live: a legitimately busy session's file sat 51 minutes
  stale mid-turn.

Codex publishes no equivalent side state; this layer is Claude-only.

## Status contract

`STATE_EVIDENCE_SOURCES` in `src/swe_mux/session.py` is the machine-readable contract;
`tests/test_status_contract.py` asserts it is total over `SessionState`.

| State | Allowed sources | Positive evidence predicate |
| --- | --- | --- |
| `starting` | daemon | Spawn/promotion of an agent backend (lifecycle ownership). |
| `running` | daemon | Shell lifecycle (spawn or demotion back to shell). |
| `working` | transcript, hook, watchdog-pty | A root turn began or root tool activity: user prompt / assistant / tool records in order, or `UserPromptSubmit`/`PreToolUse`/`PostToolUse` while the transcript is not authoritative. The PTY may never invent work except under the two narrow `watchdog-pty` rules below. The unwitnessed first-turn rule also requires a submit from the current input owner before it may read a working marker. |
| `awaiting` | transcript, hook, watchdog-pty | A stabilized approval request, immediate `request_user_input` question, elicitation dialog, rate limit, or SSH authentication prompt, always with a typed `awaiting_reason`. `watchdog-pty` covers startup dialogs and SSH authentication prompts classified from the bounded PTY tail. |
| `idle` | transcript, hook, pty, watchdog, watchdog-pty, daemon | A proven turn boundary (`turn_duration`, `end_turn`+text, `task_complete`, native abort outcome, Stop hook, `idle_prompt`, interrupt marker, catch-up settle) or a bounded inferred recovery (startup-quiet fallback, watchdog paths below). Exact operator interrupt input plus this session's idle PTY is also bounded confirmation of an interrupted turn. A catch-up settle over a session already idle emits `root_turn_settled`, which changes no state but is the only way delivery readiness can learn that a session left running across a daemon restart is at its prompt (`delivery-readiness.md`). |
| `exited` / `crashed` | pty, daemon | Process ground truth: the exit code through `terminal_exit_outcome`. |

Ambiguous or absent evidence resolves to the conservative prior, never a guessed active
state. A transition from a source outside its state's set still applies (refusing could
strand a session) but is ledgered as a contract violation and counted; the corpus asserts
zero occurrences and the fleet health alarm fires on any at runtime.

Source arbitration is unchanged from before this phase: priority `{pty:0, transcript:1,
hook:2}` within a turn, released at new-turn boundaries, with `force` (interrupt/abort,
process exit, lifecycle changes, transcript-authoritative closes) reclaiming authority.

**Turn-boundary authority follows observed liveness, not parser confidence.**
The transcript suppresses redundant hook transitions only after the tailer has observed growth at or after the most recent root-scoped transcript-backed hook.
The comparison uses `Session.transcript_growth_ts` and `last_turn_hook_ts`.
It never uses filesystem mtime because Windows can freeze the timestamp of a file a CLI still holds open.
It never uses `parser_status`, which now reports only whether token, context, cost, and model measurements are trustworthy.
This preserves transcript precedence without letting a healthy-looking but silent parser suppress the only source still reporting.

Precedence deliberately did not move to hooks.
Claude and Codex hooks are independently delivered and retried, so a late `PreToolUse` can arrive after the ordered transcript has already recorded the turn end.
Making hooks generally authoritative would strand that finished turn as `working`.
The ordered transcript therefore still owns boundaries whenever it is live, while explicit cross-source corrections remain able to abort an interrupted turn, close a missed completion, or clear an approval after the existing post-block slack.

OMP's in-process extension adds a strictly increasing source sequence to every hook envelope.
The daemon persists the last accepted sequence in supervisor metadata, preserving the ledger across
daemon reloads, `/clear`, `/new`, `/fork`, and `/resume` while the extension process remains alive.
A repeated or lower sequence is acknowledged without reapplying state, increments the bounded
duplicate counter, and is exposed in the state log.
Sequence ordering makes retries idempotent but does not override the transcript-liveness authority
contract.

Liveness evidence (`last_turn_hook_ts`) is root-scope only.
A subagent-scoped tool hook proves a subagent ran, not that the root conversation wrote records somewhere else.
A background subagent writes nothing into the root transcript, so counting its stream here false-fired `observation_stale_since` on a healthy session waiting on its agents, measured live 2026-08-02 as 666 seconds of false staleness.
The four stale, missing, rollover-refused, and rollover-unadoptable paths retain their diagnostic reason strings and delivery block, but authority itself has no special-case branch for them.
Each path naturally leaves transcript growth behind a newer hook, which returns state to the hook and PTY fallback tiers.
A conversation rollover itself is a `daemon`-sourced forced transition to `starting`, the same
lifecycle class as promotion.

**A transcript that is *missing* revokes authority the same way a stale one does.**
Claude derives a transcript's directory from the CLI's working directory, so entering a
native worktree *moves the file*: the path resolved at spawn stops existing and a file with
the same name appears under the new directory's slug, with nothing telling the daemon.
`parser_status` then stays frozen at `ready` from the last successful read.
Before authority was split from measurement confidence, that stale value kept the hook tier suppressed as redundant to a transcript that could no longer report anything.
Measured live 2026-08-06:
a session latched `idle` for four minutes while its own screen showed the working spinner,
its cli-state file read `busy`, and root turn hooks kept arriving 8 s apart - every layer
that could have spoken was either blind or being dropped. Three rules close it, in the
order the observer's switch watcher consults them:

- **The CLI's own report** (`SessionManager.note_hook_transcript_path` →
  `_staged_transcript_relocation`): every Claude hook payload carries `transcript_path`,
  and the daemon used to read it only when the payload *also* reported a new conversation
  id - so the one case it could not see was the file moving while the conversation stayed
  the same. Staged on the ingress task and consumed by the observer, because re-aiming
  stops and restarts the tail task and the hook handler must never do that (Claude blocks
  the user's turn on the POST). Accepted without corroboration: the POST is loopback-only
  and hook-secret authenticated, the caller has already established the payload speaks for
  this session's own conversation (`foreign_conversation_hook_id`), and the reported file
  must still be the one named after that conversation id - which mux dictated at spawn, so
  a nested child inheriting the hook wiring cannot point the session anywhere. The watcher
  wakes on it rather than waiting out a poll tick.
- **Re-resolution** (`SessionManager._relocated_transcript_candidate`): when the followed
  path does not exist and the cli-state file reports a different `cwd`, the adapter
  re-derives the path from (native id, live cwd) and the observer re-aims at it. This is
  not the mtime heuristic below and needs none of its ownership analysis - that one
  guesses *which* conversation a session moved to and can latch onto a sibling's, while
  this re-finds a file named by the conversation id the session already owns. Both halves
  are proven: the followed path is gone, and the candidate's stem is this session's own
  `native_session_id`. A switch to a file naming the conversation already owned is
  therefore a **relocation, not a rollover** - rolling would rekey identity, close the
  history row, and mint a new agent run for a conversation that never ended. Claude-only:
  Codex mints rollout filenames the daemon cannot reconstruct.
- **The staleness net**: a missing followed file alongside a recent root turn hook marks
  `observation_stale_since` (`reason: transcript_missing`) and hard-blocks delivery while
  the liveness comparison independently leaves hooks unsuppressed. Previously a missing file returned early - "no reading" was
  read as "no evidence" - which made the guard written for a moved conversation
  unreachable in the case where it moved hardest. A missing file *without* a turn hook
  stays silent: the observer is aimed before the CLI creates the file, so that is an
  ordinary startup race.

Both repairs need a live session to act on. **Binding and adoption search by conversation
id instead** (`BackendAdapter.locate_transcript`, `_relocated_conversation_transcript`):
every other discovery path reads the directory the *spawn* cwd names, so a session whose
CLI had moved came back from a daemon restart with nothing to adopt - the mirrored path was
dead and the recency scan was looking in the wrong place. Searching for `<native id>.jsonl`
across the backend's project directories is safe where an mtime scan is not, because mux
dictates that id at spawn: a file named after it is this session's by construction, and the
ordinary claim rules still apply on top. Throttled to `TRANSCRIPT_LOCATE_INTERVAL_SECONDS`
and run off-thread in the binding loop, because it searches rather than computes (measured
2026-08-06: 9 ms across 448 project directories). Adoption also requires the mirrored path
to still exist before re-adopting it, since a remembered path is not evidence a file is
there.

`SessionStart` is lifecycle and identity evidence, never a turn boundary.
When it arrives during an active `working` or `awaiting` root turn, including Codex compaction, it is ledgered as `session_start_state_ignored` and cannot move the session to `idle`.
A `SessionStart` refused as another process generation returns from hook ingress before binding, liveness, automation, or status observation.

**Terminal latch.** `exited`/`crashed` is process ground truth, so once a record is in one of
them only `source="daemon"` may move it out; every other source is refused and ledgered
(`kind: transition_refused`, `reason: terminal_latch`). `force` does not override it — force
reclaims arbitration *between live sources*, not from process reality. Without this, a hook
that arrived after the PTY reported EOF (a `SessionEnd` the shim re-spooled after the file was
unlinked) resurrected a dead session to `idle`, leaving a live-looking session on a dead PTY
that fleet and status surfaces counted as active.

### Hook spool replay

Terminal hook events whose POST failed are appended to a per-session spool for the watchdog to
replay. The spool file is keyed by mux session id, which outlives both the agent run and the
session itself, so replay is guarded rather than unconditional:

Blocking events (`PermissionRequest`, `Notification`, Codex approval/question requests) are
spooled alongside the terminal ones. A permission dialog raised during a session-preserving
daemon restart — a routine operation here — otherwise has no second source: the transcript
tail reads "open" and the PTY reads "approval", so neither watchdog path can fire and the
session sits displayed as "working" until the 900s no-evidence alarm.

- Each entry is stamped with a wall-clock `spooled_at` by the shim.
- At drain, an entry older than the current agent run's start **or** the current turn's start
  is discarded and ledgered (`hook_spool_discarded`). A `Stop` from turn N must not close
  turn N+1, and a leftover from a previous run must not close the first turn of the next one.
  Entries with no stamp (written by an older shim) still replay.
- A session in a terminal state discards its spool instead of replaying it.
- `demote()`, `promote()` and `_mark_ended()` all discard the spool, so nothing survives into
  a run it does not describe.
- Consumption is by rename (`<sid>.jsonl` → `<sid>.jsonl.consuming`), so a shim append that
  lands after the snapshot goes to a fresh file rather than being truncated away. On Windows
  the rename simply fails while the shim holds the file, which correctly defers the drain one
  poll instead of racing it.

### Awaiting sub-reasons

| `awaiting_reason` | Set by |
| --- | --- |
| `approval` | `PermissionRequest`/`approval_needed` hooks, `permission_prompt` notification, Codex `exec_approval_request`/`apply_patch_approval_request` |
| `question` | Codex `request_user_input` |
| `elicitation` | `elicitation_dialog` notification |
| `rate_limit` | rate-limit hooks/records |
| `authentication` | SSH password, key-passphrase, host-key confirmation, keyboard-interactive, verification-code, or MFA prompt in the bounded PTY tail |

`idle_prompt` maps to `idle` (ready), never `awaiting`. It does not clobber a pending
approval unless this session's own screen proves the dialog is gone (see below).
`SessionRecord.awaiting_reason` is cleared by every transition off `awaiting`.

### Approval stabilization

**A request mux answers itself never enters this machinery at all.**
When the conversation holds a non-`wait` approval mode and the request clears the floor and the
allowlist (`approvals.md`), `apply_hook_observation` returns a decision and returns *before*
`_request_stabilized_approval`: no candidate, no timer, no `approval_detected`, no `awaiting`,
no sound, and no web push.
This is the one case where mux knows the decision instant rather than inferring it, which is
precisely the evidence the delegated-approval rules below have to recover from the screen.
A request the mode declines to answer — including every floor deferral — takes the ordinary path
unchanged, so the contract below still describes every approval a human ever sees.

Approval detection has an internal and a user-visible boundary.
`approval_detected` blocks delivery readiness immediately but does not change `SessionState`, run automation attention, play a sound, or route web push.
If the approval remains unresolved for `APPROVAL_STABILIZATION_SECONDS` (5 s), the daemon transitions to `awaiting(approval)` and emits one stabilized `approval_needed` event.
Positive resumed-work evidence or terminal input cancels the pending approval before the boundary.
Cancellation is its own step (`note_activity_evidence`) rather than a side effect of `_transition`, because the evidence that matters most often changes no state: a `PostToolUse` hook on a session the transcript is driving, and a resume record arriving while the session still reads `working`, both used to return before reaching the cancel and left the timer to expire on its own.
`PreToolUse` is deliberately not counted - Codex fires it *before* the permission decision, so it proves an attempt, not an answer.
The approval candidate retains its `tool_use_id`, so a completion carrying a different id cannot cancel it even during the race before either the PTY dialog or Claude `waiting` state has been observed.
A matching completion is definitive resolution evidence and may cancel the candidate despite stale screen or CLI state.
The effective PTY approval result vetoes unidentified cancellation evidence.
The candidate timestamp and evidence are mirrored into supervisor-owned metadata, so a session-preserving daemon reload resumes the remaining delay instead of losing or restarting it.
Once stabilized, the active approval retains the same tool identity until it leaves `awaiting(approval)`, so parallel hook completions remain unable to clear the visible status.
Questions, elicitation prompts, and rate limits remain immediate because they are not routinely auto-approved.

### SSH boundary and prompt classification

A non-local authority in an OSC 7 `file` URI changes the session runtime boundary from `local`
to `remote` without treating the remote path as a host filesystem path.
The session records the authority, boundary time, and remote transport state while clearing the
last local runtime cwd and Git telemetry.
A later valid local OSC 7 directory is the only positive signal that restores the local boundary.
Hook cwd reports are ignored while the boundary is non-local because remote hook ingress is unavailable.
Transcript reads, hook ingress, skills, environment discovery, Project promotion, local cwd, and Git telemetry report an explicit unavailable result while the boundary is remote or unknown.
Manual PTY input remains available.

SSH prompt classification is deliberately narrow and runs before generic quiet-shell recovery.
It recognizes host-key confirmation, OpenSSH password prompts, private-key passphrases,
keyboard-interactive prompts, verification codes, OTP, MFA, and Duo challenges.
A generic `Password:` line is not enough because it may be a local `sudo` prompt.
An authentication match transitions to `awaiting(authentication)` and sets the transport state
to `authentication`; its diagnostic preview is always the fixed
`[SSH authentication prompt withheld]` marker.
The daemon never logs or stores the matched prompt body.

`Connection closed`, `Connection reset`, `Broken pipe`, and SSH timeout variants set the remote
transport state to `ended`.
They do not turn the session into a generic quiet `idle` result.
A remote OSC 7 prompt marks the connected remote shell idle.
Submitting a newline-terminated command from that prompt marks it running until a later remote prompt arrives.
This separates a quiet remote prompt from a long-running remote command without treating arbitrary output as a prompt.
`tests/fixtures/ssh-boundaries-v1.json` pins every supported authentication and disconnect class,
the quiet-remote case, and the expected typed state.

**Delegated approvals.**
Codex can hand every approval to an automated reviewer instead of the user (`approvals_reviewer: auto_review`, the CLI's "Automatic approval").
It still fires `permission_request`, there is no resolution hook, and the decision is written nowhere: across every August 2026 rollout, approval records appear exactly zero times.
The only evidence the reviewer said yes is the tool *finishing*, so any auto-approved tool that outran the 5 s window became sidebar attention, a turn-completion badge, and a push notification for a question nobody was ever asked - measured live on 2026-08-09, an 11 s `exec` committed at 5.0 s and was resumed 5.3 s later.
So a delegated approval is held past the stabilization window until this session's own screen actually shows the dialog, which is what an escalation to the human looks like and what an auto-approval never produces.
`APPROVAL_AUTO_REVIEW_CEILING_SECONDS` (60 s) is the backstop for a screen the classifier cannot read: a late approval is a nuisance, a hidden one strands the session.
The setting is read per thread from the rollout's `turn_context` and `thread_settings_applied` rather than from `config.toml`, because the CLI's own picker changes it live and the file would then describe a session that no longer matches it; a session whose setting is unknown keeps the plain window.

The transition ledger records `approval_stabilization_started` (carrying `delegated` and `tool_use_id`), `approval_stabilization_coalesced`, `approval_stabilization_cancelled` (carrying `tool_use_id`), and `approval_stabilization_committed` (carrying `gate` and `tool_use_id`) so a missing or late alert is reconstructable.

### Idle sub-reason

| `idle_reason` | Set by |
| --- | --- |
| `waiting_on_background` | this session's own PTY tail showing the CLI's background-wait line after the last working marker |

The idle-axis sibling of `awaiting_reason`, and deliberately *not* a state: the turn really
did end, the composer accepts input, and `delivery_state` is unchanged. What it fixes is the
reading — "ready · turn complete" says "finished, nothing more is coming", which is wrong
while the agent has background work that will wake it back up. The UI renders
"ready · background work running", and the completion sound and push alert skip that turn
end (the next one is the moment worth the user's attention). Cleared by every transition off
`idle`, so a self-wake into `working` clears it with no user prompt involved.

### Standing-activity annotations (the fifth axis)

`SessionRecord.standing_activity` models standing engagements — an armed `/loop` wakeup
(`loop`), a cron schedule (`cron`), running background tasks (`background_tasks`), live
subagents (`subagents`) — as a list of
`StandingActivity {kind, source, evidence, since, expires_at, count, detail}`. The contract:

- **Not states.** SessionState, `awaiting_reason`, and `delivery_state` are untouched: a
  loop-armed idle session is exactly as deliverable as an idle one — that is the
  user-facing point. (`idle_reason: waiting_on_background` above will migrate onto the
  `background_tasks` annotation once its detection sources land, kept one release for UI
  compat.)
- **Additive and composable.** A session can hold `loop` + `background_tasks` +
  `subagents` simultaneously; the UI composes them.
- **TTL'd, never latched.** Every annotation carries `expires_at` where the evidence
  implies one (loop: schedule time + slack; subagents/background: refreshed on evidence,
  decayed after a quiet window). The watchdog pass sweeps expiries before any other rule.
  A wrong annotation must decay on its own — the lesson of every stuck-status incident in
  this codebase.
- **Run-scoped.** Every seam that resets observation identity clears the set: conversation
  rollover, identity heal, promote, demote, session end, and the adoption-repair paths.
  Foreign-conversation hooks (see below) are dropped *before* annotation extraction — a
  nested child's loop is not this session's loop.
- **Ledgered.** Additions/removals/expiries append non-transition ledger entries
  (`kind: "standing_activity"`, action `added|updated|removed|expired`) to the transition
  ring, and `status_health.counters.standing_activity_expired` counts decays (an expiry
  without a positive clear is a small drift signal). A pure TTL refresh is silent —
  subagent evidence renews at tool-record cadence and would bury the entries that matter.

- **Manually retractable.** `POST /api/sessions/{sid}/standing-activity/clear` (optional
  `{kind}`; the whole set otherwise) retracts an annotation the user can see is wrong,
  ledgered `manual` like any other clear, and drops the run-scoped launch bookkeeping with
  it so a later duplicate completion cannot decrement a fresh annotation. Bounded by the
  axis: it cannot move `SessionState`, `awaiting_reason`, or `delivery_state`, and it
  cannot *assert* activity - only retract it, after which a genuinely running task
  re-announces itself on its next piece of evidence. Every source here is evidence about
  work the daemon cannot observe directly, so any of them can be left holding a false
  claim whose only other exit is a 30-minute TTL. Surfaced as "Clear standing activity" in
  the session context menu and the command palette, offered only when a badge is showing.

Add/refresh/expire/clear go through `set_standing_activity` /
`clear_standing_activity` / `expire_standing_activity` / `clear_all_standing_activity`
(`session.py`), shared by the live `Session` and the replay harness exactly as
`apply_state_transition` is. The list is serialized in the record snapshot (so supervisor
adoption round-trips it across daemon restarts), appears on `/api/sessions` rows and
`/api/sessions/{sid}/state-log`, and rides every `update` fanout frame.

Detection sources (Claude — extractors in `observation.py`, live records only; historical
catch-up never arms an annotation, a pre-restart set survives via the snapshot instead).
Record shapes verified 2026-07-31 against live transcripts and the CLI's own tool schemas:

- **`loop`** — assistant `ScheduleWakeup` tool_use `{delaySeconds, reason, prompt}` arms
  with `expires_at = record ts + delaySeconds (clamped to the runtime's [60, 3600]) +
  slack` and the reason as `detail`; `{stop: true}` positively clears. The wakeup firing
  is a normal user turn; the re-arm silently refreshes the expiry, and the slack absorbs
  the gap. A loop whose wakeup never fires decays on its own.
- **`cron`** — the CLI's cron jobs are **session-only**: in-memory, no on-disk store
  (`durable` is a documented no-op), gone when the CLI exits, recurring jobs auto-expired
  after 7 days. The transcript is therefore the complete source, and the run-scoped
  clears already match the store's lifetime. `CronCreate {cron, prompt, recurring?}`
  increments (cadence as `detail`, expiry mirroring the CLI's 7-day bound),
  `CronDelete {id}` decrements, the last delete clears; `CronList` results are free text
  and only refresh.
- **`background_tasks`** - opens are read from the launch's **tool_result**, which is the
  only place both launch shapes appear: an explicit `run_in_background: true` Bash
  tool_use (which also supplies the `detail`, from its `description`), and a *foreground*
  command the CLI moved to the background when it outran its timeout, whose input carries
  no flag at all. Both results name the task id ("Command running in background with ID:
  <task_id>" / "was moved to the background (ID: <task_id>)"). Tracked by tool_use id in
  `observation_state`, so the whole set is run-scoped.

  Closes: a `<task-notification>` naming the launch's `<tool-use-id>`, or
  `TaskStop {task_id}`. **The notification rides up to three carriers for one
  completion** (verified live 2026-08-06): a `queue-operation` record (`operation:
  "enqueue"` when the task finishes, `"remove"` when it is handed to the model) with the
  body in its top-level `content`; an `attachment` record (`attachment.commandMode ==
  "task-notification"`) with the body in `attachment.prompt`; and - only if the CLI gets
  to deliver it into a turn - a plain user record. A session whose turn ends before the
  shell exits never gets the user record at all, so reading only that one left the
  annotation open for its full TTL on every background shell that outlived its turn.
  Whichever carrier arrives first closes; the rest are no-ops, because closes are
  idempotent per task rather than decrementing (duplicate announcements are the normal
  case, and each extra would subtract again). The queued carriers are still excluded from
  `_CLAUDE_TAIL_IGNORED`'s turn-state judgement - they are not turn activity, only
  completion evidence.

  A close with no tracked open (state lost across a daemon restart) decrements the adopted
  annotation's own count. No evidence bounds a background task's duration, so the TTL is
  a slow decay (30 min) refreshed by background evidence and by the CLI's own
  background-wait footer at turn end.

  **The footer may only refresh, never open.** It is read from a 32 KiB append-only window
  of redraw traffic, so the line drawn while a task genuinely ran stays matchable long
  after it finished; because `set_standing_activity` creates when absent, corroboration had
  quietly become a source. Measured live 2026-08-06: the transcript positively closed the
  annotation and this reading re-added it 29 s later with a fresh 30-minute TTL, after
  which nothing but that TTL could clear it. This is the same rule the subagent tier's
  `create=False` already had.
  The background rules currently include a count because the measured footer does, but that is no longer a classifier-wide requirement.
  Region scoping now distinguishes the live footer from prose elsewhere in the retained screen.
  The captured negative fixture includes the former false-positive wording outside the current frame.

  `idle_reason: waiting_on_background` is still *derived* from this annotation (or the
  live footer) at each turn end — kept one release for UI compat.
- **`subagents`** — `SubagentStart`/`SubagentStop` lifecycle hooks (registered in
  `adapters/claude.py`) own the count (starts − stops, floor 0; a stop at zero clears).
  Fallback when hooks are lost: `Task`/`Agent` tool_use opens, its tool_result closes —
  but only while no lifecycle hook has arrived this run, so one subagent is never
  counted by two tiers. Any sidechain record refreshes recency (creating at count 1 if
  even the launch was missed) — **and so does the subagent's own tool-hook stream**
  (subagent-scoped `PreToolUse`/`PostToolUse`), which is not a nicety but the only
  recency evidence a *background* subagent produces at all: measured live 2026-08-02, a
  session whose agents ran 16 minutes wrote zero `isSidechain` records into the root
  transcript, so without the hook refresh the TTL expired the annotation ~2 minutes in
  while the agents kept working. Tool-hook activity may also re-create a zeroed
  annotation at count 1 (healing a count a lone under-counted `SubagentStop` collapsed),
  but only `SUBAGENT_REOPEN_GRACE_SECONDS` (10 s) past the last stop: hooks are
  unordered and retried, so the stopped agent's last `PostToolUse` can land after its
  stop, and re-opening on that straggler is the hook-channel twin of the
  trailing-transcript flap below. Hooks arrive subagent-scoped carrying the root
  `session_id`; the foreign filter runs first, so a nested child's fleet never counts.
  Rendered on the working axis too (`working · Task · 2 subagents`) — the root turn is
  open during Task execution.

Two cross-backend sources complete the set:

- **Process fast-clear** (`processes.py`): on each inspector pass, a session holding a
  `background_tasks` annotation older than the spawn-race grace (15 s) with **no live
  descendant that could be that task** is cleared immediately
  (`process:no_task_descendants`) - a vanished process cannot still be working, and this
  is the strongest clear there is. Never the reverse: descendants alone open nothing (an
  MCP-server child is not a background task).

  A descendant could be the task unless it is the CLI root itself, or it was **already
  running when the annotation opened** - a background task's process starts with the
  launch that opened the annotation, so anything older is one of the CLI's own long-lived
  children. The discriminator is deliberately age against `annotation.since` and not a
  name match, which would drift with every CLI release. An unreadable start time counts as
  task-capable: refusing to clear leaves the TTL in charge, while a wrong clear retracts a
  true "an agent is still working", so a false clear stays structurally impossible.

  The earlier test was "the session has exactly one descendant, the CLI root". That is the
  same intent and an unreachable gate: a Claude session that has opened a file holds a
  language server, one with a stdio MCP server holds that too, and real sessions carry
  4-10 permanent children. Measured on the live fleet 2026-08-06, the count was never 1 on
  any session that could run a background task, so the one positive clear that does not
  depend on the transcript had never fired.
- **Codex** (`_codex`): no loop/cron equivalents exist in the Codex CLI, so those
  annotations stay empty rather than being faked. Trusted `SubagentStart`/`SubagentStop`
  hooks own the subagent count; before any lifecycle hook arrives, `sub_agent_activity`
  records provide the count-1/TTL fallback. Once hooks own the count, those transcript
  records only refresh recency and cannot reopen a stopped subagent. Background tasks
  reach Codex through the process fast-clear side only.

### Leaving `awaiting` (answered prompts)

Nothing fires when a permission dialog is dismissed, and the approval was raised by an
unordered hook at the *highest* arbitration priority, so ordered transcript evidence
cannot outrank it. Without an explicit rule the status stays "awaiting approval" for the
rest of the turn — observed live for 558 seconds of real work. Three exits, in order of
strength:

1. **Transcript-proven** (`resumed_after_awaiting:*`, proven): a record that proves the
   model or its tools are running again — a tool result, a new assistant message, a fresh
   user prompt, or the Codex equivalents — provided it is **provably newer** than the
   block (`AWAITING_RESUME_SLACK_SECONDS` past `awaiting_since`; the transcript is polled
   while hooks POST immediately, so the record that *caused* the prompt can be observed
   just after it) **and** the screen is not currently showing a dialog.
2. **Screen-proven** (`watchdog-pty`, inferred): an approved long-running tool writes no
   record until it finishes, so the CLI's own working spinner is the only timely proof.
   `resume_working` fires after `STATE_WATCHDOG_AWAITING_RESUME_SECONDS`, and may only
   move `awaiting` → `working` inside a turn that is already running.
   For Claude, this exit is suppressed while its published CLI state is `waiting`, even if a later parallel spinner repaint makes the raw PTY write order classify as `working`.
3. **Notification + screen proof**: `idle_prompt` while `awaiting` clears to `idle` only
   when the tail's live frame is the idle input prompt.

The asymmetry is deliberate: showing a stale "awaiting" is a cosmetic defect, while
hiding a prompt the user has not answered loses their attention entirely. Every clear
therefore requires positive proof, and the PTY is used as a *veto* on the transcript path
rather than as a source.

### The unwitnessed session (PTY-only turns)

A session is **unwitnessed** when both tiers that may prove work are structurally
absent: no transcript is bound and no hook has ever arrived (`session_is_unwitnessed`).
This is reachable, not defensive. Codex lifecycle hooks may be disabled, untrusted, or
unreachable; in that fallback mode its minted thread id is first named by
`agent-turn-complete`. Until turn one *ends* a fresh pane can therefore have neither tier,
and `working` is reachable from neither source. The
startup-quiet PTY fallback's `idle` was therefore the last word for the whole turn:
measured live at 200 s of "ready · turn complete" while the agent worked, with the
rollout's own `task_started` sitting on disk 4 s after spawn.

For such a session the watchdog runs a paired fallback, the only rules here that read
an `idle` session:

- `idle` + a current-owner submit was observed + screen shows the working spinner -> `begin_pty_turn` (opens a real root turn
  through `_begin_root_turn`, so `turn_started`, delivery readiness, and every turn
  consumer see what a transcript-started turn produces).
- `working` + screen shows the idle prompt -> `end_pty_turn`.

Both are `watchdog-pty`/`inferred`, filed at PTY priority so any transcript or hook
evidence in the same turn outranks them without forcing. Neither is stall-gated: the
other recoveries wait because a proven source might still be about to speak, and here
none can. The submit arm is run-local and prevents a retained or replayed working marker from
making a newly started or resumed session report `working` before the user sends anything.
Neither rule can act on an approval screen, because `pty_tail_state` is
ordering-aware and a live dialog reads `approval` rather than `working` or `idle` — so
this pair can no more start a turn on top of an unanswered prompt than close one that
is still blocked. A single hook or a bound transcript ends the standing permanently;
a temporary silence on a channel that exists is what the stall-gated paths above are
for. Latency is one watchdog poll (5 s), and in practice the provisional binding
(`backends.md`) usually beats it.

## Transition ledger

Every applied transition goes through `apply_state_transition` (shared verbatim by the
live `Session` and the replay harness) and lands in the per-session ring buffer with:
prior state, next state, detail, `awaiting_reason`, source, priority, the `evidence`
string that justified it, `proof` (`proven` for hook/transcript/notification/process
evidence; `inferred` for watchdog/PTY-backstop/startup-quiet recoveries), monotonic and
wall timing, and seconds spent in the previous state. No transition can occur without a
ledger entry; `promote`/`demote`/nested-agent detection/`_mark_ended` all route through it.
Inferred transitions are recovery events — counted, bounded, and never the primary path
for a healthy session.

### Turn completions (the attention counter)

The same funnel counts turns, because it is the only place that sees every transition after
arbitration. `is_turn_completion` decides, and `note_turn_completion` advances
`record.turn_seq` and stamps `last_turn_end_ts` / `last_turn_evidence`. The completing
transition carries its `turn_seq` in the ledger - rather than a second entry of its own, which
would double the durable timeline's write rate - so "why did this row light up?" is answerable
from `/api/sessions/{sid}/state-log` after the fact.

A turn completes when the agent stops holding the floor:

| Transition | Counts | Why |
| --- | --- | --- |
| `working -> idle` | yes | The agent finished speaking. |
| any state but `awaiting` -> `awaiting` | yes | An approval is the loudest thing a session can want, including one raised from `idle`. |
| `starting -> idle` | no | The CLI finished booting. Counting it made every freshly spawned session unread before it had said anything. |
| `awaiting -> idle` | no | The human answered the prompt. Their own action is not news for them. |
| `awaiting -> awaiting` | no | A detail change on the same approval is the same approval. |
| anything -> `exited` / `crashed` | no | An ended session carries its own muted styling. |

Counting inside the funnel is what makes the count exact rather than approximate: a refused
transition (the terminal latch, or a source that lost arbitration) is not a turn, and a
duplicate report of one turn - the hook and the transcript landing milliseconds apart - is a
no-op transition and so counts once.

`turn_seq` is deliberately not derived from `last_activity_ts`, which moves on every PTY byte
including the full-screen repaint a resize provokes. `design/features/ui.md` covers what the
sidebar does with the counter and why the old signal failed; `read_turn_seq` on the same record
is the user-level acknowledgement, written through `POST /sessions/{id}/read`
(`design/interfaces.md`). Both round-trip through `SessionRecord.snapshot()`, so a
session-preserving daemon restart keeps them, and a snapshot written by a daemon that predates
them adopts as caught up rather than as a wall of false unread.

## Timing exposed on the record

Two timings cross the API boundary so a client can age a session without a second request.

- `state_since` is the wall-clock instant of the transition into the current state, written by
  `apply_state_transition` alongside `Session.last_state_change_ts`.
  Wall-clock rather than monotonic because a browser has no access to this process's clock origin.
  A record adopted from a daemon that predates the field is seeded at adoption rather than left
  at zero, and a client renders `0` as "unknown", never as "just now".
- `turn_started_at` is the instant the current root turn began, cleared when it ends.
  This, not `state_since`, is what "how long has it been working" means: a turn spans every tool call and every approval inside it, while `state_since` restarts on each of them.
  Run-scoped like `last_turn_ms`.
- `turn_epoch` is a monotonic root-turn generation within the current observation identity.
  `active_turn_id` is the optional provider-native or mux-synthesized identity for the open generation.
  A terminal event with a different non-empty ID is stale, is ignored, and increments `stale_turn_terminal_ignored` instead of closing newer work.
  OMP, pi, and opencode synthesize process-local IDs at their native root-start event; Codex IDs pass through unchanged.
- A logical root prompt is distinct from tool activity and duplicate start evidence.
  The same prompt reported once by a hook and once by the transcript joins one generation.
  A later logical prompt or a different native turn ID while the previous generation is still open proves that its terminal boundary was missed.
  Mux emits `turn_aborted(outcome=superseded, recovered_boundary=true)`, increments `turn_boundary_recovered`, and opens the new generation without publishing a false idle interval.
- `last_turn_ms` is the length of the last **completed** root turn.
  A harness-reported `duration_ms` outranks any measurement taken from the outside, which also counts observation lag.
  Otherwise the two boundary stamps are subtracted, and the result is published only if it is a plausible turn.
  Implausible in either direction leaves the previous value in place rather than replacing it with a lie, because a stale-but-real number beats a fresh wrong one on a row a human reads to decide whether to intervene.
  Longer than `MAX_TURN_DURATION_SECONDS` (6 h) is a missed boundary, not a measurement: an overnight-idle session must not claim its last turn took nine hours.
  Shorter than `MIN_TURN_DURATION_SECONDS` (250 ms) is a boundary artifact, not a turn — a root turn is a model round trip at minimum, and a published artifact renders as the literal `0s` a duration column exists to avoid.
  Negative is the same rejection and reaches it the same way, rather than clamping to zero and publishing the clamp as a real measurement of no time.
  The field is run-scoped and cleared wherever observation identity resets, because a duration
  measured in a replaced conversation is not this conversation's.
- `last_human_prompt_at` is when a **person** last submitted a request here, and is deliberately a different question from `turn_started_at`.
  Plenty of turns are opened by something other than a person: mux delivering an agent-authored queued message, or a Stop hook injecting a teammate message the instant the previous turn ends.
  A session can therefore be minutes into a fresh turn and an hour past anything its operator said — measured live at a `3m22` turn on a session thirteen minutes into work asked for once.
  Run-scoped like the turn fields, and `None` rather than guessed when unknown.
- `running_work_since` is when the current stretch of **running** work began, and is the third answer to "how long has this been going" — the one the other two cannot give.
  A harness that dispatches background agents ends its root turn to hand off: `turn_started_at` goes `None`, `last_turn_ms` freezes at the length of the *dispatching* turn, and the row then reports a finished fragment of a request that is still running.
  Measured live 2026-08-19 on three ultracode sessions 37, 64, and 81 minutes into their requests, all three reading ~10m, with `cli_state: busy`, `pty_tail: working`, and live `subagents` annotations on every one of them.
  It is worse than stale: every phase of a workflow ends with a short main-loop turn that overwrites `last_turn_ms`, so the number can *shrink* as the run continues.
  Latched, not tracked. Stamped when a `RUNNING_ACTIVITY_KINDS` annotation opens with none already latched, and anchored to the **turn that dispatched the work** rather than to the annotation, because the request started when the operator asked for it and not when the first agent happened to register minutes later.
  A turn start that is missing, in the future, or older than `MAX_RUNNING_WORK_ANCHOR_AGE_SECONDS` (6 h, the same ceiling `last_turn_ms` refuses at) falls back to the annotation's own instant rather than publishing it.
  Released only when a **root turn closes with nothing running** — the main loop came back, finished, and left nothing behind, which is the one observable that means the request is over.
  Deliberately not released by the annotations emptying on their own: a workflow's subagent count reaches zero for seconds at a time between phases (measured at four seconds on the 37-minute session above), and re-anchoring there would report a long multi-phase run as however long its newest phase has lasted.
  The per-kind clear that a phase boundary goes through therefore leaves it standing, while the run-scope clear at a lifecycle seam takes it with the annotations it was latched from.
  Run-scoped like the turn fields.
  This changes the **time** axis only. `idle` stays the correct state — the turn really did end, the composer really does accept input, and delivery is untouched.
- `interrupt_pending_at` and `interrupt_pending_source` record exact operator Esc or Ctrl-C intent while a root turn is working.
  Intent is not completion proof, so state remains `working` and delivery remains blocked.
  The UI renders `interrupt requested` and freezes the displayed duration at the request instant instead of continuing to claim that cancellation time is active work.
  Native terminal evidence clears the fields immediately; an unconfirmed intent expires after 120 seconds and the running timer resumes.

### Prompt authorship is captured at delivery or not at all

`_note_prompt_authorship` runs on every root submit hook, ahead of the transcript-authority check that returns early for healthy sessions.
An unmarked submit is a person: typing in the pane, the web terminal, and the mobile composer all reach the PTY without passing the queue.
Only a delivery mux performed itself can claim otherwise, and it says so by leaving `Session.queue_delivery_mark` — `(delivered_at, authored_by_a_human)` — which the observer consumes and expires after `QUEUE_DELIVERY_ATTRIBUTION_SECONDS`, so a delivery whose hook never arrived cannot silently disown the next prompt a person types.

The test is authorship, not delivery mechanism: a human's queued message is still the human speaking, so only `sender_kind` outside `HUMAN_SENDER_KINDS` disowns the prompt.

This cannot be recovered from the transcript, which is why it is stamped here.
By the time a prompt is a transcript record, a teammate's injected message and a typed one are the same shape.
The field therefore survives a session-preserving restart on the snapshot and stays `None` on a cold adoption.

### Turn boundaries are dated by the records that carry them

`_turn_now` stamps both ends of a turn, and while a transcript record is being dispatched it returns *that record's own* `timestamp` rather than the wall clock.
This is what makes the timing **derived** rather than observed: catch-up after a restart or a redeploy replays the same records and recomputes the same numbers, so the values are idempotent across daemon lifetime.
Measuring against the wall clock instead collapsed every replayed turn to the milliseconds the replay itself took, writing `0.0` (which the sidebar draws as nothing) or a millisecond or two (which it draws as `0s`).

`_dispatch_transcript_event` scopes the stamp for the duration of one record and restores the previous value on the way out, so it never persists and never describes anything but the record in flight.
Scoping it at the shared dispatch rather than inside the per-harness readers is what makes the rule harness-agnostic: claude, codex, omp and pi all write a top-level ISO `timestamp`, and a harness added to the registry inherits the rule by routing through the same dispatch.
Backends with no transcript at all — opencode's hooks, shell — never reach it and keep the wall clock, which is the honest answer for a boundary observed as it happens.

`_plausible_record_ts` gates admission: finite, positive, and no later than the present plus `HISTORICAL_TIMESTAMP_SLACK_SECONDS`.
Arbitrarily old is admissible because replaying history is the point of the field; only the future is impossible.
A rejected stamp falls back to `_session_now`, so a corrupt line degrades to the previous behavior instead of poisoning the measurement.

Under the replay harness both ends still agree, because its fixture records are stamped from the same virtual clock (`VirtualClock` via `_stamp`); a virtual start can no more pair with a real end than before.

A turn is closed only once the arbiter accepts the close.
`_finish_root_turn` takes the turn bookkeeping down provisionally and restores it through `_restore_refused_turn` when `_transition` refuses, and records the duration only after.
Dismantling it first stranded the session as `working` with no turn: the row fell back to ageing the state, which restarts on every tool call, the next tool call reopened the turn and restamped its start, and `last_turn_ms` was measured for a turn that was still running.
`_turn_close_landed` distinguishes the one refusal that means "this close lost to better evidence" from the blanket `False` `_transition` returns throughout replay, where the turn genuinely ended and only the fanout is suppressed.

`_finish_transcript_catchup` carries an open turn's replay-derived start into `_begin_root_turn` through `started_at`.
Without it, re-adopting a turn that history left open restamped it as beginning now, and every working row in the fleet read `0s` the moment the daemon came back and then aged from the restart rather than from the work.
When catch-up settles to `idle` instead, it clears both `turn_started_at` stamps: the record survives a session-preserving restart, so a stamp left behind is one the next `working` reading would age from.

## Reading the PTY screen

The screen is read from the last `SCREEN_TAIL_BYTES` (32 KiB) via `ScrollbackBuffer.tail_bytes`, which walks the chunk deque from the right.
It matters that this is not a slice of the joined retention: the watchdog reads the screen for every agent session twice a pass on a 5-second loop, so joining full retention first cost tens of megabytes a second of pure allocation across a fleet, worst for Codex, whose buffers are the fullest.
The window is sized against redraw *traffic*, not one frame: the current CLI's spinner and a waiting dialog's `●` pulse keep writing while the screen is static, and 8 KiB of that traffic evicted a dialog's own text within about 90 s.
The verdict then degrades to `unknown`, which is conservative but blind.

The raw ConPTY stream is a write log, not a snapshot of the terminal's rendered cells.
A parallel tool can repaint its task list and spinner after Claude draws a permission dialog without removing that dialog from the visible screen.
Raw prompt-marker ordering therefore remains useful evidence but is not authoritative proof that the dialog disappeared.
For Claude, `pty_tail_state` combines that raw result with the CLI-published status.
`waiting` yields an effective `approval`.
`busy` changes only a raw `idle` result to effective `working`, because one completed parallel tool can repaint the input prompt while a sibling tool remains active.
`idle` lets the raw screen result stand.

Before matching, the tail is normalized by `_normalize_tail_text`.
OSC is removed from body text, cursor movement reads as a space, styling reads as nothing, and horizontal whitespace runs collapse.
This is not cosmetic because the current CLI positions every word of a dialog footer at an absolute column, such as `Enter\x1b[8Gto\x1b[11Gconfirm`.

`PTY_RULES` is one declared, ordered Python table.
Every rule carries an id, outcome, `ScreenRegion`, and regex predicate.
The supported regions are `whole_tail`, `bottom_non_empty_lines(n)`, `after_last_prompt_marker`, `osc_title`, and `osc_progress`.
First match wins when two rules conflict.
The named prompt-frame region centralizes the former `rfind` ordering arithmetic used separately by lifecycle and background-wait classification.

`pty_tail_state` returns `working`, `approval`, `idle`, `uninformative`, or `unknown`.
Agent-owned model pickers, transcript viewers, and resume lists are `uninformative` because their body text describes a selected item rather than the live agent state.
All three consumers withhold lifecycle conclusions from that outcome: delivery vetoes sending, watchdog recovery does nothing, and the unwitnessed first-turn fallback does nothing.
The viewer fixtures and the prose false-positive fixture live under `tests/fixtures/pty_tails/`.

The markers remain version-layered.
Pre-2.x CLIs draw "esc to interrupt" and "? for shortcuts", while the current Claude CLI draws neither.
Its recurring working marker is the spinner ellipsis, its idle marker is `(shift+tab to cycle)`, and its dialogs expose narrow cancel, amend, and confirm affordances.
Marker drift here is silent and disabling.
Measured 2026-07-31, the old markers matched nothing across 518 KB of a busy session, so every screen recovery was dead and a stale approval survived minutes of visible work.
Real captured streams are pinned by `test_pty_tail_modern.py` and must be recaptured when the CLI drifts.

OSC 0/2 title and OSC 9;4/4 progress values are extracted incrementally with the same bounded partial-sequence retention used for OSC 7.
They stay isolated from body text and appear only through their named regions and diagnostics.
Local ConPTY measurement on 2026-08-06 used Claude Code 2.1.223 and Codex CLI 0.146.1.
Claude emitted two OSC 0 title writes during startup and no OSC 2, OSC 9;4, or OSC 4 writes.
Codex emitted none of those channels during startup.
The retained real Claude captures likewise contain OSC 0 only.
No title or progress classification rule was added because the measured title is arbitrary task text and neither harness supplied a verified semantic state signal.

`pty_tail_explain` returns every evaluated rule, its match result, its region, and a bounded region preview.
It exposes both `screen_outcome` and effective `outcome`, plus `outcome_source` and `cli_state_status`, so the exact arbitration is visible.
The live state-log exposes this as `pty_explain`, so marker drift and corroboration overrides can be diagnosed without attaching a debugger.
The durable timeline records the raw result as `pty_tail_screen`, the effective result as `pty_tail`, and the source as `pty_tail_arbitration`, so a transient disagreement remains reconstructable after the screen changes.

## Watchdog recovery (pinned behavior)

`watchdog_decision` (pure, shared with the harness) encodes the quiescence watchdog:

- `awaiting(approval)` with the screen showing the working spinner for
  ≥ `STATE_WATCHDOG_AWAITING_RESUME_SECONDS` (5s) → `resume_working`. Checked *before* the
  transcript-quiet gate, because after an approval the transcript is usually busy rather
  than quiet, which would skip the pass entirely. **Approval only**: it is the one block
  whose dialog the tail classifier can recognize, so it is the one where "the spinner is up"
  proves the block is gone. A Codex question or an elicitation shows neither an approval nor
  an idle marker while redraw history still holds "esc to interrupt" from before the block —
  resuming on that would hide a prompt the user must answer.
  Claude `waiting` changes the effective screen result to `approval`, so a parallel spinner repaint cannot trigger this exit while the permission dialog remains active.
- Claude `busy` changes a raw idle prompt to effective `working`, so one completed parallel tool cannot force-idle the session while a sibling tool remains active.
  This preserves the same root-turn lifetime and its `turn_started_at` timestamp; the sidebar working timer therefore cannot reset on that repaint.
- `working`/`awaiting` stalled ≥ `STATE_WATCHDOG_ENDED_STUCK_SECONDS` (6s) with a quiet
  transcript whose tail **proves** the turn ended → force idle (`watchdog`).
- Exact Esc or Ctrl-C input recorded against a working root turn arms interrupt intent.
  After `INTERRUPT_PTY_SETTLE_SECONDS` (2s), this session's own idle prompt confirms `turn_aborted(outcome=interrupted)` on the next watchdog pass without waiting for a provider marker that may never be written.
  A busy, approval, uninformative, or unknown PTY cannot confirm it.
  This rule is available even for a hook-only harness because it reads only the owned PTY and operator intent.
- **Unwitnessed** (no transcript bound and no hook ever received): `idle` + working
  spinner → `begin_pty_turn`; `working` + idle prompt → `end_pty_turn`. Evaluated
  first, because it is the only rule that reads an `idle` session, and deliberately
  not stall-gated (see above).
- Tail `unknown`/`open` (schema drift, missing marker, observer on a sibling transcript)
  → only after `STATE_WATCHDOG_PTY_STUCK_SECONDS` (60s) **and** the screen's live frame is
  the idle prompt → force idle (`watchdog-pty`). A session parked at a real dialog reads
  `approval`, so the backstop can never force-idle it and hide the prompt.
- A **missing or unreadable transcript** reaches that same backstop with verdict `unknown`
  rather than returning early. Returning made the recovery the design promises for exactly
  that case unreachable — and it is the case with no other recovery path.
- Stall duration, screen verdict, and current state are **re-derived after** the threaded
  tail read. They were captured before it, so an approval raised inside that window used to
  be judged against pre-approval evidence and instantly resumed to `working`.
- "esc to interrupt" on screen always reads busy: a genuine long tool is never cut short.
- **Startup dialogs**: a session **no turn has ever run this agent run** whose screen
  reads `approval` continuously for `STATE_WATCHDOG_STARTUP_DIALOG_SECONDS` (10 s) →
  `awaiting(approval)` with evidence `startup_dialog` (source `watchdog-pty`, inferred,
  forced — the hook-sourced startup idle holds arbitration at hook priority and would
  refuse a PTY-priority block forever; safe because the no-turn gate guarantees no
  hook-raised approval exists yet). Claude's workspace-trust dialog fires *after* its
  `SessionStart` hook reported idle, and Codex's trust/update dialogs appear before any
  evidence channel exists, so a blocked session displayed "ready" while typed input landed
  in the dialog. Only this rule may clear its own block: screen leaves `approval` →
  back to `idle` (`startup_dialog_cleared`). The gate means it can never fight
  mid-conversation evidence; the first turn resets the tracker permanently. Fixtures:
  `claude-startup-dialog`, `codex-startup-dialog`.

All of these classify as `inferred`, record the stall duration and tail verdict, and are
pinned by fixtures at their exact thresholds (`claude-watchdog-ended-stuck`,
`claude-esc-pause-without-marker`, `codex-watchdog-unknown-tail`,
`claude-long-tool-never-cut`, `claude-approval-resume-pty`,
`claude-modern-spinner-resume`, `claude-pending-approval-not-cleared`,
`codex-unwitnessed-first-turn`).

The watchdog pass also runs the **classifier-drift self-check**: a witnessed session
continuously `working` for `SCREEN_CLASSIFIER_BLIND_SECONDS` (120 s) while every screen
read in that window returns `unknown` counts `screen_classifier_blind` (once per blind
window) and ledgers it — changing no state. This is the 2026-07-31 marker-drift failure
mode made self-detecting: it ran for weeks of CLI releases with every screen recovery
silently dead, discovered only by a user report. Two independently blind sessions raise
the fleet status-health alarm (`screen_classifier_blind` in `alarm_reasons`); an
unwitnessed session is exempt by construction (its state *came* from the screen).

## Foreign conversations on the hook channel

The hook ingress authenticates the *session*, not the process: a nested child CLI
launched by the session's own tool call inherits the hook wiring and speaks over the same
channel with its own conversation id. Identity is guarded at the rollover decision
(`backends.md` — a bound session rolls only on an in-place replacement: not
`source: "startup"`, not another cwd), and state is guarded here: once a Claude or Codex session is
bound, `apply_hook_observation` drops any hook naming a different conversation before it
can move state — a child's `PermissionRequest` must not raise an "awaiting approval" no
screen shows. Drops are ledgered (`foreign_conversation_hook_ignored`) and counted in
`status_health.counters.foreign_hook_ignored`, and a dropped hook does not refresh
`last_hook_ts` — a child's chatter is not this session's liveness. The one id never
treated as foreign is the session's own mux id: its spawn conversation speaking while the
record is bound elsewhere is identity-corruption evidence, and it heals the binding back
to the anchor instead (`session_identity_reconciled`, trigger `own_conversation_hook`),
guarded by the retired-run set so a `/clear` can never be un-cleared. Pinned by
`claude-nested-child-hooks` and `tests/test_conversation_rollover.py`.

## Status-health metrics and bounds

Per session: proven/inferred transition counts, inferred recoveries by source, watchdog
recovery actions, blocked reopen attempts (`closed_by_transcript` latch refusals),
contract violations, observer restarts, terminal latencies, seconds in state, and seconds
since *any* evidence landed.

- `GET /api/sessions/{sid}/state-log` — the full typed ledger plus `status_health` for
  one session (first stop when diagnosing a stuck status). `state_changes` holds real
  transitions in their own ring: one busy turn emits dozens of same-state tool detail
  updates, which would otherwise evict the history that explains how a session got here.
- `GET /api/diagnostics/status-health` — fleet aggregate with explicit bounds and an
  `alarm` flag: inferred share of turn terminals above
  `STATUS_HEALTH_MAX_INFERRED_TERMINAL_RATIO` (5%, once ≥20 terminals), any contract
  violation, any session claiming to be active with **no evidence of any kind** for
  `STATUS_HEALTH_STUCK_ACTIVE_SECONDS` (900s), or any `identity_collisions[]` entry —
  live agent sessions sharing one `(backend, native_session_id)` or one transcript path,
  the cross-attribution signature that shows up to the user as sessions with linked
  status. Time in state is deliberately *not* the
  stuck signal — a single turn legitimately stays `working` for many minutes while tools
  run, and an elapsed-time bound alarmed on every healthy long turn in the live fleet.
  The soak matrix (Phase 7) asserts on this endpoint; a rise in inferred recoveries is a
  tracked regression, not silent drift.
- The state watchdog also runs the **identity sweep** each pass (`sessions.md`): collision
  groups emit `identity_collision_detected` once, and a Claude session provably off its own
  conversation is healed back to its anchor (`session_identity_reconciled`, trigger
  `live_sweep`).

## Durable timeline (post-mortem investigability)

The per-session ledger rings answer "how did this session get here" only while both the
daemon and the session are alive: a restart wipes them, a busy turn's detail churn evicts
old entries, and a killed session takes its ledger with it. The durable timeline
(`status_timeline.py`, `status_timeline` table in mux.db) makes "at 12:39 this session
showed `working` but was actually idle — what did each layer say?" answerable after the
fact, including for sessions that no longer exist.

- **A sink, not part of the contract.** `apply_state_transition` stays pure and shared
  with the replay harness; the corpus pins that persistence never enters it. The live
  `Session`'s ring is a `LedgerRing` that stamps each appended entry with a monotonic
  `seq` and the `agent_run_id` current *at append time* (run-keyed so a rollover's
  successor rows never mix with its predecessor's — the cross-run bleed incident class),
  then nudges a guarded callback in the `meta_sink` discipline. The store batches dirty
  sessions and drains on its own SQLite worker; nothing on the transition or hook-ingress
  path ever waits on the database. Durable seqs continue after whatever a previous daemon
  boot wrote, so restarts neither collide nor double-write; ring evictions between drains
  are counted (`rows_lost_to_ring_eviction`), never silent. Sessions get a final drain in
  `_mark_ended`, and daemon shutdown drains once more after `sessions.shutdown()`.
- **Every ledger kind persists** — transitions *and* the non-transition kinds
  (`watchdog_recovery`, `standing_activity`, `cli_state`, `layer_reading`,
  `screen_classifier_blind`, `foreign_conversation_hook_ignored`, `transition_refused`,
  `reopen_blocked`, `observer_fault`, hook-spool records) — payloads verbatim. Same-state
  detail churn is deliberately kept (it is the evidence that a session was being
  observed); retention bounds the volume (`status_timeline_retention_days`, default 30,
  edited in Settings → Processes → Detection timeline and applied to the running store).
- **Layer readings are ledgered on change.** The watchdog pass records the
  `pty_tail_state` verdict and the hook-recency bucket (`fresh`/`stale` against
  `TRANSCRIPT_STALE_SECONDS`, `never` before the first turn hook; always
  `last_turn_hook_ts`, never `last_hook_ts`), and the cli-state poll records the file's
  `status` (`busy`/`idle`/`absent`) — each as a `layer_reading` entry appended **only when
  the reading changes**, never per 5 s pass. The reading at any instant is the last entry
  at or before it, which is what lets a post-mortem adjudicate the displayed state
  against every layer of the ladder without a polling firehose.
- **Queries.** `GET /api/sessions/{sid}/state-log?from=&to=` serves the durable slice
  (live ring flushed first, so it is complete to the request moment) and answers for
  ended sessions in post-mortem mode; `GET /api/sessions/{sid}/diagnostic-bundle?from=&to=`
  packages the timeline, state-log fields, fleet status-health, and the window's
  transcript records into one artifact. The investigation procedure lives in
  `development/STATUS_INCIDENT_RUNBOOK.md`.

### Observation-freshness check (the healthy-looking silent fault)

A stale observation is the one fault class that presents as a perfectly healthy session:
the daemon is silent, the dot is green, and delivery is nonetheless blocked because the
followed transcript went quiet (`transcript_stale`), moved or vanished
(`transcript_missing`), or the CLI rolled onto a conversation a live sibling already owns
(`conversation_owned_elsewhere` / `explicit_conversation_mismatch` /
`rollover_adoption_failed`). Because the per-session state-log answers one session at a
time, nothing surfaced this across the fleet, so a silent daemon read as evidence of health.

`doctor.observation_freshness(sessions, now)` closes that: it scans every agent session for a
set `observation_stale_since`, and emits one content-free row per affected session -
`{id, name, backend, reason, since, seconds_stale, diagnostic, delivery_blocking}` - reading
the same `record.observation_stale_since`, `session.observation_stale_reason`, and
`record.observation_diagnostic` fields the state-log exposes (`session.py`
`_note_transcript_staleness`, the rollover-refusal path, and the reason initialization at
construct time). It is a read-only projection, never a new authority: the delivery block and
the transition ledger are unchanged. The consolidated `GET /api/diagnostics/doctor` report
(`mux doctor`) folds it into a `freshness` check that fails when any row is delivery-blocking
and warns otherwise, so `mux doctor` answers "is any session quietly reporting a dead
conversation" without reading each session's state-log by hand.

## Regression defense

- **Golden corpus** (`tests/fixtures/detection/v1/`): every fixture pins `expected.states`
  — the normalized transition stream (previous/state/source/proof/awaiting_reason) — and
  asserts `SessionState`/`awaiting_reason` at every delivery checkpoint, alongside the
  Phase 1 events/parser/delivery goldens. Status-affecting parser, mapping, or watchdog
  changes fail CI unless the fixture update is reviewed.
- **Edge-case inventory** (`tests/fixtures/detection/v1/edge_case_inventory.json`): every
  known edge case with its reproducing fixture, closing guard, and one-line root cause.
  `test_edge_case_inventory_is_closed_and_consistent` fails when either half disappears.
- **Coverage matrix**: `test_replay_corpus_covers_phase35_status_matrix` requires the
  corpus to keep exercising every reachable state, every awaiting sub-reason, both proof
  classes, both watchdog paths, and zero contract violations.
- **Live conformance** (`tests/test_live_agent_conformance.py`, opt-in canaries): scripted
  real-CLI runs must reach terminal status through proven transitions only; any inferred
  recovery in the captured state stream fails the canary — that is the drift signal.
- **Capture pipeline** (`tests/support/status_capture.py`): a real stuck/misclassified
  session's transcript (+ optional state-log) is scrubbed — no prompt bodies, tool
  arguments/outputs, native ids, or terminal bytes survive — and converted into a
  replayable fixture with its expected block auto-filled for review, then promoted into
  the corpus as a permanent regression test.

## UI reflection

`frontend/src/fleetStatus.ts` is a read-only consumer of this contract.
It recomputes from each current session snapshot, preserves measurement source and activity age on every projected field, and supplies a closed predicate set for deterministic voice targeting.
It never infers an approval from spoken wording and never caches state separately from the session ledger.
The guarded approval route requires both stabilized `awaiting(approval)` and a fresh effective `pty_tail_state(...)=approval` reading before prepare and again before confirmation.

`frontend/src/sessionStatus.ts` is the single mapping from `SessionState` (+
`awaiting_reason`) to the rendered indicator: `stateDotClass` (total, distinct per state,
neutral fallback for pending tabs) and `sessionStatus`/`awaitingLabel` (distinct
affordances for approval / question / elicitation / rate limit). Desktop sidebar, pane
headers, tab strips, context menus, and the mobile unified-tab projection all render
through it — `frontend/test/sessionStatus.test.ts` asserts totality, the awaiting
affordances, that idle never renders as awaiting approval, and (by source inspection)
that no surface reintroduces an inline heuristic.

An open working turn with `interrupt_pending_at` renders as `interrupt requested` with a static amber indicator.
`frontend/src/sessionRowFields.ts` caps the displayed duration at `interrupt_pending_at`; provider or watchdog resolution clears the open-turn timestamp, while intent timeout resumes the same root-turn clock.

Standing activity renders through the same mapping and — with one exception — **never
changes the dot**: hue variants of green fail at a glance and fail colorblind users, so
green keeps meaning exactly "ready, you can type and send". The exception
(`sessionDotClass`): an *idle* session with running work — live `subagents` or
`background_tasks`, never a merely scheduled `loop`/`cron` — renders a hollow **blue
ring** (`state-dot idle standing`). Blue says an agent is engaged; the ring (vs the
working dot's filled pulse) says "not generating — you can type", which is still true:
delivery and the state axis are untouched. It is deliberately a *shape* difference, not
a motion or green-hue one: `prefers-reduced-motion` disables the working pulse, so a
solid-vs-pulsing distinction would collapse for exactly the users who need it, and it is
not a green variant so ready stays unmistakable. Every dot surface (sidebar, tab strips,
the mobile unified tabs, pickers, context menus) renders through `sessionDotClass`, so
the ring appears wherever the dot does. `activityBadges(session)` yields one compact
affordance per annotation — `⟳` for loop *and* cron (one glyph; the tooltip
distinguishes "loop armed" from the cron cadence — sidebar density beats taxonomy),
`≡` background tasks with count, `⑂` subagents with count — and `sessionStatus`
composes them into the line: `ready · loop armed`, `working · Task · 3 subagents`,
`ready · 2 background tasks` (the background annotation supersedes the derived
`idle_reason` text so one fact never renders twice). Each badge's tooltip carries the
annotation's own `detail`, which for `background_tasks` names the newest open launch and
how many others there are ("Restart the harness daemon (+1 more)"). That is not
decoration: a count alone is unfalsifiable from the outside - "1 background task" on a
session with nothing running looks exactly like a correct reading - so the failure these
sources are prone to is the one the UI could not otherwise show. Dense surfaces (sidebar rows, tab
strips, the mobile projection) show the dimmed glyphs beside the dot; the full text lives
in the status line and tooltips. Tests assert every annotation kind renders a glyph and
label and that idle-with-loop still classifies as ready with an unchanged dot class.
Notification policy: annotations neither add nor suppress sounds, with one exception —
**running work** (`subagents`, `background_tasks`) suppresses the turn-end and ready
alerts, because the turn ended and the agent did not. Scheduled engagements (`loop`,
`cron`) still notify: ready means ready, and an armed wakeup is not work in flight. That
split is the same one `hasRunningActivity` uses for the blue ring, defined once per side
(`session.RUNNING_ACTIVITY_KINDS`, `push.RUNNING_ACTIVITY_KINDS`, and the frontend's
`hasRunningWork`; a test pins the first two equal). The rule reaches the notification path
only because `state_changed` carries the axes — see below.

### The agent-facing consumer

`session_watch.py` is the third read-only consumer of this contract, beside the UI and the
notification path, and it is the one whose audience cannot ask a follow-up question: an
orchestrator agent is *told* "your worker settled" and acts on it.
It therefore inherits both of this contract's hard-won suppressions rather than restating
them - a session with running work has not finished, and a `starting` session that reaches
`idle` through the startup-quiet fallback has not started - and it applies the same 120 s
hold `push.py` applies, because the same measured flap (89 of 211 idle transitions back to
`working` inside 120 s) would otherwise make two of every five notices wrong.
It never renders `idle` as "done": every notice carries the state, its `awaiting_reason` or
`idle_reason`, and any running standing activity, because those are three different answers
that share one word (`mux-mcp.md`).

### What `state_changed` carries, and why

`state_changed` is the event the "the agent is waiting for your input" alert is raised
from, so it carries every field that decides whether to interrupt a human:
`{previous, state, detail, awaiting_reason, idle_reason, standing, proof}`. `standing` is
the open annotation *kinds* only — a consumer deciding whether to interrupt needs to know
*that* subagents are running, never their count or expiry, and a fat payload on every
transition is paid by every websocket client.

This is a contract, not a convenience. `idle_reason` and the standing axis used to ride
only `turn_ended`, which meant both suppression rules in `push.py` and `sessionSounds.ts`
read fields their event never had and silently never fired; measured over one 10-hour,
17-session day, 39% of "ready" pushes were raised while the session had running work
annotated. Both producers of the event (`observation._transition` and the startup-quiet
fallback in `session.py`) must keep the payload in step, and the startup path stamps
`proof: inferred` for the same reason: an idle read off screen quiet must not reach a
consumer looking as solid as a hook-proven turn end.

## Key files

- `src/swe_mux/session.py` — contract tables, `apply_state_transition`,
  `watchdog_decision`, `pty_tail_appears_idle`, `apply_watchdog_recovery`,
  `session_is_unwitnessed`, `session_status_health`, `fleet_status_health`,
  standing-activity set management, `startup_dialog_observation`,
  `note_classifier_blindness`, `_relocated_transcript_candidate`,
  `note_hook_transcript_path`, `_staged_transcript_relocation`,
  `_relocated_conversation_transcript`, `note_hook_cwd`, `_note_transcript_staleness`
- `src/swe_mux/observation.py` — evidence extraction, `tail_turn_state`, hook/transcript
  handlers, `closed_by_transcript` latch, trailing-completion guard, standing-activity
  extractors
- `src/swe_mux/cli_state.py` — the `cli-state` corroboration poller, and the conversation
  ownership oracle (`ConversationHolder`, `conversation_holders`) that resume preflights read
- `src/swe_mux/status_timeline.py` — `LedgerRing`, `note_layer_reading`, and the durable
  `StatusTimelineStore` (write-behind sink, time-ranged queries, retention)
- `src/swe_mux/processes.py` — background-task process fast-clear
- `src/swe_mux/models.py` — `SessionState`, `AwaitingReason`, `StandingActivity`,
  `SessionRecord.awaiting_reason`/`standing_activity`
- `src/swe_mux/server.py` — state-log and status-health endpoints
- `tests/support/detection_replay.py`, `tests/support/status_capture.py`
- `tests/test_status_contract.py`, `tests/test_detection_replay.py`,
  `tests/fixtures/detection/v1/` (+ `edge_case_inventory.json`)
- `frontend/src/sessionStatus.ts`, `frontend/test/sessionStatus.test.ts`
