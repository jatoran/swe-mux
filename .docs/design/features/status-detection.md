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
- **Attention** — unread/pinned UX state (`sessionAttention.ts`), client-side only.

## The detection ladder

The daemon sits a level above the CLIs and reads every layer it owns — not only hooks.
Per signal class, every layer feeds the same ledger with its own `source` string:

| Layer | Source tag | What it may do |
| --- | --- | --- |
| CLI side state (`~/.claude/sessions/<pid>.json`) | `cli-state` | Corroborate + identity (counters, ledger entries). **Never drives SessionState** — promotion to a transition source requires a release of disagreement telemetry plus its own corpus fixtures. |
| Hooks (incl. `SubagentStart`/`SubagentStop`) | `hook` | Turn boundaries, blocks, identity (priority 2). |
| Transcript records | `transcript` | Ordered turn evidence (priority 1) + standing-activity extraction. |
| PTY screen classifier | `pty` / `watchdog-pty` | Recoveries, vetoes, the startup-dialog rule, drift self-check. |
| Process tree (ProcessInspector) | `process` | Liveness; `background_tasks` fast-clear (a vanished process cannot still be working). |
| Daemon lifecycle | `daemon` | Spawn/promotion/demotion/exit (force). |

**The `cli-state` layer** (`src/swe_mux/cli_state.py`, polled on the 5 s watchdog cadence,
stat-then-parse-on-change): Claude publishes per-process state files carrying
`{sessionId, cwd, pid, procStart, status, statusUpdatedAt, updatedAt, version}` (verified
2.1.220; observed `status` values `busy`, `idle`, and `waiting` — measured 2026-08-01, the
file reads `waiting` for the duration of a permission dialog). Files map to sessions by
conversation id.
What it feeds:

- **Status corroboration**: a *settled* contradiction (CLI `busy` while mux `idle`, CLI
  `idle` while mux `working`, both sides ≥ 10 s old) counts `cli_state_disagrees` once per
  standing fact and ledgers it (`kind: "cli_state"`). One release of this telemetry gates
  any future promotion to a transition source. `waiting` is deliberately outside that
  comparison — it is recorded as a `layer_reading` instead. Treating "CLI `waiting` while
  mux shows `working`" as a disagreement is a real candidate for the next iteration, and
  it goes through the same telemetry gate as any other expansion of this layer's role.
- **Identity corroboration**: a file in exactly one live session's cwd, bound to a
  conversation no live session owns, updated after that session's run began, is a nested
  child CLI observed deterministically — the signal the `bb81463` incident had to infer
  from hook `source` fields. Counts `nested_children_observed`, once per foreign
  conversation. Ambiguity (two live sessions in one cwd) stands down.
- The session's own file snapshot is surfaced as `cli_state` on the state-log endpoint.
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
| `working` | transcript, hook, watchdog-pty | A root turn began or root tool activity: user prompt / assistant / tool records in order, or `UserPromptSubmit`/`PreToolUse`/`PostToolUse` while the transcript is not authoritative. The PTY may never invent work — except under the two narrow `watchdog-pty` rules below, both of which need the CLI's own spinner to be the *last* marker on screen. |
| `awaiting` | transcript, hook, watchdog-pty | An explicit block: approval request, `request_user_input` (question), elicitation dialog, or rate limit — always with a typed `awaiting_reason`. `watchdog-pty` covers exactly one case: the startup-dialog rule below, on a session no turn has ever run. |
| `idle` | transcript, hook, pty, watchdog, watchdog-pty, daemon | A proven turn boundary (`turn_duration`, `end_turn`+text, `task_complete`, Stop hook, `idle_prompt`, interrupt marker, catch-up settle) — or a bounded inferred recovery (startup-quiet fallback, watchdog paths below). A catch-up settle over a session already idle also emits `root_turn_settled`, which changes no state but is the only way delivery readiness can learn that a session left running across a daemon restart is at its prompt (`delivery-readiness.md`). |
| `exited` / `crashed` | pty, daemon | Process ground truth: the exit code through `terminal_exit_outcome`. |

Ambiguous or absent evidence resolves to the conservative prior, never a guessed active
state. A transition from a source outside its state's set still applies (refusing could
strand a session) but is ledgered as a contract violation and counted; the corpus asserts
zero occurrences and the fleet health alarm fires on any at runtime.

Source arbitration is unchanged from before this phase: priority `{pty:0, transcript:1,
hook:2}` within a turn, released at new-turn boundaries, with `force` (interrupt/abort,
process exit, lifecycle changes, transcript-authoritative closes) reclaiming authority.

**Transcript authority is revoked when the transcript is stale.** Hooks are suppressed as
redundant only while the transcript is authoritative (`parser_status == "ready"`), which is
correct exactly as long as the file being tailed is this PTY's conversation. Staleness
evidence (`last_turn_hook_ts`) is **root-scope only**: a subagent-scoped tool hook proves a
*subagent* ran, not that the root conversation wrote records somewhere else — a background
subagent writes nothing into the root transcript, so counting its stream here false-fired
`observation_stale_since` on a healthy session waiting on its agents (measured live
2026-08-02, 666 s of staleness warning). When the transcript provably
is not this conversation — an in-CLI `/clear` or `/new` the daemon could not follow, marked by
`observation_stale_since` (`backends.md`) — the suppression is what freezes the session:
the transcript can no longer report a turn boundary and the only source that can is being
dropped. Staleness therefore returns state to the hook/PTY fallback tiers and hard-blocks
delivery, rather than leaving a healthy-looking session reporting a retired conversation.
A conversation rollover itself is a `daemon`-sourced forced transition to `starting`, the same
lifecycle class as promotion.

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

`idle_prompt` maps to `idle` (ready), never `awaiting`. It does not clobber a pending
approval unless this session's own screen proves the dialog is gone (see below).
`SessionRecord.awaiting_reason` is cleared by every transition off `awaiting`.

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
- **`background_tasks`** — a Bash tool_use with `run_in_background: true` opens (tracked
  by tool_use id in `observation_state`); the launch's tool_result ("Command running in
  background with ID: <task_id>") binds the task id. Closes: a `<task-notification>`
  user record naming the launch's `<tool-use-id>`, or `TaskStop {task_id}`. A close with
  no tracked open (state lost across a daemon restart) decrements the adopted
  annotation's own count. No evidence bounds a background task's duration, so the TTL is
  a slow decay (30 min) refreshed by background evidence and by the CLI's own
  background-wait footer at turn end, which corroborates without knowing the count.
  `idle_reason: waiting_on_background` is now *derived* from this annotation (or the
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
  `background_tasks` annotation older than the spawn-race grace (15 s) whose CLI root has
  **no live descendant** is cleared immediately (`process:descendants_zero`) — a vanished
  process cannot still be working, and this is the strongest clear there is. Never the
  reverse: descendants alone open nothing (an MCP-server child is not a background task),
  and any extra descendant leaves the annotation to transcript evidence and the TTL —
  which also makes a false clear structurally impossible while a task's process runs.
- **Codex** (`_codex`): no loop/cron equivalents exist in the Codex CLI, so those
  annotations stay empty rather than being faked, and no subagent lifecycle hooks exist —
  any `sub_agent_activity` record opens/refreshes the `subagents` annotation at count 1
  and the TTL is the only clear. Background tasks reach Codex through the process
  fast-clear side only.

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
3. **Notification + screen proof**: `idle_prompt` while `awaiting` clears to `idle` only
   when the tail's live frame is the idle input prompt.

The asymmetry is deliberate: showing a stale "awaiting" is a cosmetic defect, while
hiding a prompt the user has not answered loses their attention entirely. Every clear
therefore requires positive proof, and the PTY is used as a *veto* on the transcript path
rather than as a source.

### The unwitnessed session (PTY-only turns)

A session is **unwitnessed** when both tiers that may prove work are structurally
absent: no transcript is bound and no hook has ever arrived (`session_is_unwitnessed`).
This is reachable, not defensive. Codex has no session-start hook and mints its own
thread id, which it first names on `agent-turn-complete` — so until turn one *ends* a
fresh pane has neither tier, and `working` was reachable from neither source. The
startup-quiet PTY fallback's `idle` was therefore the last word for the whole turn:
measured live at 200 s of "ready · turn complete" while the agent worked, with the
rollout's own `task_started` sitting on disk 4 s after spawn.

For such a session the watchdog runs a symmetric pair, the only rules here that read
an `idle` session:

- `idle` + screen shows the working spinner → `begin_pty_turn` (opens a real root turn
  through `_begin_root_turn`, so `turn_started`, delivery readiness, and every turn
  consumer see what a transcript-started turn produces).
- `working` + screen shows the idle prompt → `end_pty_turn`.

Both are `watchdog-pty`/`inferred`, filed at PTY priority so any transcript or hook
evidence in the same turn outranks them without forcing. Neither is stall-gated: the
other recoveries wait because a proven source might still be about to speak, and here
none can. Neither can act on an approval screen, because `pty_tail_state` is
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

## Reading the PTY screen

The screen is read from the last `SCREEN_TAIL_BYTES` (32 KiB) via
`ScrollbackBuffer.tail_bytes`, which walks the chunk deque from the right. It matters that
this is not a slice of the joined retention: the watchdog reads the screen for every agent
session twice a pass on a 5-second loop, so joining full retention first cost tens of
megabytes a second of pure allocation across a fleet — worst for Codex, whose buffers are
the fullest. The window is sized against redraw *traffic*, not one frame: the current CLI's
spinner and a waiting dialog's `●` pulse keep writing while the screen is static, and 8 KiB
of that traffic evicted a dialog's own text within ~90 s (the verdict then degrades to
`unknown`, which is conservative but blind).

Before matching, the tail is normalized (`_normalize_tail_text`): window titles (OSC) are
removed outright — the CLI rewrites them with arbitrary task text while working — cursor
movement reads as a space, styling as nothing, and whitespace runs collapse. This is not
cosmetic: the current CLI positions every word of a dialog footer at an absolute column
(`Enter\x1b[8Gto\x1b[11Gconfirm`), so no marker phrase is a contiguous substring of the
raw stream.

`pty_tail_state` classifies the normalized tail as `working`, `approval` (a permission or
workspace-trust dialog), `idle`, or `unknown`. The markers are version-layered: pre-2.x
CLIs draw "esc to interrupt" / "? for shortcuts", while the current CLI draws neither —
its frame-recurring working marker is the spinner phrase ellipsis ("✶ Envisioning…",
re-written on every animation tick), its idle marker the permission-mode footer's
"(shift+tab to cycle)", and its dialogs say "Do you want to proceed?" / "Esc to cancel" /
"Tab to amend" / "Enter to confirm". Marker drift here is silent and disabling — measured
2026-07-31, the old markers matched nothing the current CLI writes (0 hits across 518 KB
of a busy session), so every screen recovery below was dead and a stale "awaiting
approval" survived minutes of visible work. Real captured streams are pinned under
`tests/fixtures/pty_tails/` (`test_pty_tail_modern.py`); when the CLI drifts again,
recapture rather than synthesize.

The tail retains redraw history, so **presence is not enough** — a session that showed a
dialog and then resumed still contains the dialog text. Only the marker that appears
*last* describes the live frame, and every caller treats `unknown` as no evidence rather
than a licence to change state. Ordering is also what keeps the ellipsis honest: a dialog
or an idle footer is always drawn after the last spinner frame, so their markers outrank
it on a blocked or finished screen. The approval markers are deliberately narrow: a false
`approval` only makes the daemon more conservative (it vetoes clearing an awaiting and
blocks the idle backstop).

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
- `working`/`awaiting` stalled ≥ `STATE_WATCHDOG_ENDED_STUCK_SECONDS` (6s) with a quiet
  transcript whose tail **proves** the turn ended → force idle (`watchdog`).
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
`source: "startup"`, not another cwd), and state is guarded here: once a Claude session is
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
  observed); retention bounds the volume (`status_timeline_retention_days`, default 30).
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

`frontend/src/sessionStatus.ts` is the single mapping from `SessionState` (+
`awaiting_reason`) to the rendered indicator: `stateDotClass` (total, distinct per state,
neutral fallback for pending tabs) and `sessionStatus`/`awaitingLabel` (distinct
affordances for approval / question / elicitation / rate limit). Desktop sidebar, pane
headers, tab strips, context menus, and the mobile unified-tab projection all render
through it — `frontend/test/sessionStatus.test.ts` asserts totality, the awaiting
affordances, that idle never renders as awaiting approval, and (by source inspection)
that no surface reintroduces an inline heuristic.

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
`idle_reason` text so one fact never renders twice). Dense surfaces (sidebar rows, tab
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
  `note_classifier_blindness`
- `src/swe_mux/observation.py` — evidence extraction, `tail_turn_state`, hook/transcript
  handlers, `closed_by_transcript` latch, trailing-completion guard, standing-activity
  extractors
- `src/swe_mux/cli_state.py` — the `cli-state` corroboration poller
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
