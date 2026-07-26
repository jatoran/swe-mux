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

## Status contract

`STATE_EVIDENCE_SOURCES` in `src/swe_mux/session.py` is the machine-readable contract;
`tests/test_status_contract.py` asserts it is total over `SessionState`.

| State | Allowed sources | Positive evidence predicate |
| --- | --- | --- |
| `starting` | daemon | Spawn/promotion of an agent backend (lifecycle ownership). |
| `running` | daemon | Shell lifecycle (spawn or demotion back to shell). |
| `working` | transcript, hook | A root turn began or root tool activity: user prompt / assistant / tool records in order, or `UserPromptSubmit`/`PreToolUse`/`PostToolUse` while the transcript is not authoritative. The PTY may never invent work. |
| `awaiting` | transcript, hook | An explicit block: approval request, `request_user_input` (question), elicitation dialog, or rate limit — always with a typed `awaiting_reason`. |
| `idle` | transcript, hook, pty, watchdog, watchdog-pty, daemon | A proven turn boundary (`turn_duration`, `end_turn`+text, `task_complete`, Stop hook, `idle_prompt`, interrupt marker, catch-up settle) — or a bounded inferred recovery (startup-quiet fallback, watchdog paths below). |
| `exited` / `crashed` | pty, daemon | Process ground truth: the exit code through `terminal_exit_outcome`. |

Ambiguous or absent evidence resolves to the conservative prior, never a guessed active
state. A transition from a source outside its state's set still applies (refusing could
strand a session) but is ledgered as a contract violation and counted; the corpus asserts
zero occurrences and the fleet health alarm fires on any at runtime.

Source arbitration is unchanged from before this phase: priority `{pty:0, transcript:1,
hook:2}` within a turn, released at new-turn boundaries, with `force` (interrupt/abort,
process exit, lifecycle changes, transcript-authoritative closes) reclaiming authority.

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

`pty_tail_state` classifies the scrollback tail as `working` ("esc to interrupt"),
`approval` (a permission dialog), `idle` ("? for shortcuts") or `unknown`. The tail
retains redraw history, so **presence is not enough** — a session that showed a dialog and
then resumed still contains the dialog text. Only the marker that appears *last* describes
the live frame, and every caller treats `unknown` as no evidence rather than a licence to
change state. The approval markers are deliberately narrow: a false `approval` only makes
the daemon more conservative (it vetoes clearing an awaiting and blocks the idle backstop).

## Watchdog recovery (pinned behavior)

`watchdog_decision` (pure, shared with the harness) encodes the quiescence watchdog:

- `awaiting` with the screen showing the working spinner for
  ≥ `STATE_WATCHDOG_AWAITING_RESUME_SECONDS` (5s) → `resume_working`. Checked *before* the
  transcript-quiet gate, because after an approval the transcript is usually busy rather
  than quiet, which would skip the pass entirely.
- `working`/`awaiting` stalled ≥ `STATE_WATCHDOG_ENDED_STUCK_SECONDS` (6s) with a quiet
  transcript whose tail **proves** the turn ended → force idle (`watchdog`).
- Tail `unknown`/`open` (schema drift, missing marker, observer on a sibling transcript)
  → only after `STATE_WATCHDOG_PTY_STUCK_SECONDS` (60s) **and** the screen's live frame is
  the idle prompt → force idle (`watchdog-pty`). A session parked at a real dialog reads
  `approval`, so the backstop can never force-idle it and hide the prompt.
- "esc to interrupt" on screen always reads busy: a genuine long tool is never cut short.

All three classify as `inferred`, record the stall duration and tail verdict, and are
pinned by fixtures at their exact thresholds (`claude-watchdog-ended-stuck`,
`claude-esc-pause-without-marker`, `codex-watchdog-unknown-tail`,
`claude-long-tool-never-cut`, `claude-approval-resume-pty`,
`claude-pending-approval-not-cleared`).

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
  violation, or any session claiming to be active with **no evidence of any kind** for
  `STATUS_HEALTH_STUCK_ACTIVE_SECONDS` (900s). Time in state is deliberately *not* the
  stuck signal — a single turn legitimately stays `working` for many minutes while tools
  run, and an elapsed-time bound alarmed on every healthy long turn in the live fleet.
  The soak matrix (Phase 7) asserts on this endpoint; a rise in inferred recoveries is a
  tracked regression, not silent drift.

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

## Key files

- `src/swe_mux/session.py` — contract tables, `apply_state_transition`,
  `watchdog_decision`, `pty_tail_appears_idle`, `apply_watchdog_recovery`,
  `session_status_health`, `fleet_status_health`
- `src/swe_mux/observation.py` — evidence extraction, `tail_turn_state`, hook/transcript
  handlers, `closed_by_transcript` latch, trailing-completion guard
- `src/swe_mux/models.py` — `SessionState`, `AwaitingReason`,
  `SessionRecord.awaiting_reason`
- `src/swe_mux/server.py` — state-log and status-health endpoints
- `tests/support/detection_replay.py`, `tests/support/status_capture.py`
- `tests/test_status_contract.py`, `tests/test_detection_replay.py`,
  `tests/fixtures/detection/v1/` (+ `edge_case_inventory.json`)
- `frontend/src/sessionStatus.ts`, `frontend/test/sessionStatus.test.ts`
