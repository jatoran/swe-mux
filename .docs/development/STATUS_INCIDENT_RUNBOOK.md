# Status incident runbook

The procedure for investigating "the session showed X but was actually Y" — a status the
UI displayed that did not match what the agent was doing. It answers questions of the
form: *"at 12:39 session S showed `working` but was actually idle — what happened in the
session, and what did swe-mux's detection hierarchy say?"* — including for sessions and
daemons that no longer exist.

It also covers the delivery half of the same question — *"the session says it is ready and
the prompt queue refuses to send to it"* — which reads facts the display does not; start at
§3a for that.

Background reading: `.docs/design/features/status-detection.md` (the detection ladder,
the transition contract, the durable timeline). This runbook is the operational half:
which endpoints, in which order, and how to adjudicate.

All endpoints are localhost HTTP on the daemon port (default `8765`). `rtk` compacts
command output lossily — fetch JSON with python `urllib`/`json`, not `curl | grep`, when
exact values matter.

## 0. Convert the report to a window

Turn "at 12:39pm" into epoch seconds, then take a window around it — start with
±15 minutes; widen if the incident's onset is unclear:

```python
import datetime
t = datetime.datetime(2026, 8, 1, 12, 39).timestamp()   # local time
window = (t - 900, t + 900)
```

Everything below takes `from`/`to` as epoch seconds.

## 1. One fetch: the diagnostic bundle

```text
GET /api/sessions/{sid}/diagnostic-bundle?from=<epoch>&to=<epoch>
```

`{sid}` may be the mux session id **or any agent-run id** (which is what a history row is
keyed by). Works for live sessions and post-mortem (`live: false`). The bundle packages
everything the rest of this procedure reads:

| Field | What it is |
| --- | --- |
| `timeline` | The durable detection timeline slice, oldest-first (see §2 — this is the investigation's spine) |
| `timeline_truncated` | True when the slice hit the row cap — narrow the window before concluding anything |
| `state_log` | The live session's current diagnostic fields (null for ended sessions): state, source priority, transcript binding + provisional flag, `unwitnessed`, observer faults, `cli_state` snapshot, `layer_readings`, `standing_activity`, `status_health` counters, input arbitration |
| `history` | The durable history row (final state, exit reason, transcript pointer) |
| `runs` | Every agent-run id the window touches — more than one means a conversation rollover happened |
| `fleet_status_health` | The fleet aggregate: alarms (`alarm_reasons`), consolidation counters, identity collisions |
| `transcripts` | Per run: the transcript records whose **native timestamps** fall inside the window (`ts_epoch` added), bounded per run — what the agent was *actually doing* |
| `timeline_sink` | Write-behind health: `rows_written`, `rows_lost_to_ring_eviction`, `flushes`, `dirty_sessions` — check `rows_lost_to_ring_eviction` before trusting a gap |

The state-log endpoint serves the same timeline with the live fields when you want it
without the transcript payload:

```text
GET /api/sessions/{sid}/state-log?from=<epoch>&to=<epoch>
```

## 2. Read the timeline

Every entry carries `ts`, `kind`, `agent_run_id`, and `seq` (monotonic per run — gaps
mean ring-evicted rows; cross-check `timeline_sink.rows_lost_to_ring_eviction`). The
kinds and what each means:

| `kind` | Meaning |
| --- | --- |
| `transition` | An applied state transition: `previous` → `state`, with `source` (`hook`/`transcript`/`pty`/`watchdog`/`watchdog-pty`/`daemon`/`process`), `priority`, `evidence`, `proof` (`proven`/`inferred`), `allowed_source` (false = contract violation), `seconds_in_previous`. Same-state entries are detail churn (`working · Read` → `working · Bash`) — evidence the session was being observed. |
| `layer_reading` | A detection-ladder layer's reading **changed**: `layer` (`pty_tail`/`cli_state`/`hook_recency`), `reading`, `previous`. The layer's reading at any instant is the last entry at or before it. `pty_tail`: `working`/`approval`/`idle`/`unknown` (the screen classifier's verdict). `cli_state`: `busy`/`idle`/`absent` (Claude's own side-state file). `hook_recency`: `fresh`/`stale` (90 s threshold on turn-affecting hooks)/`never`. |
| `watchdog_recovery` | The quiescence watchdog resolved something: `action` (e.g. `pty_idle_prompt`, `transcript_tail_terminal`, `pty_working_after_awaiting`, `startup_dialog_block`), `stalled_seconds`, `tail_verdict`. Recoveries are inferred — a healthy session has few. |
| `standing_activity` | Annotation lifecycle: `action` (`added`/`updated`/`removed`/`expired`) for `loop`/`cron`/`background_tasks`/`subagents`. An `expired` without a positive clear is a small drift signal. |
| `cli_state` | The corroboration layer counted something: `action: status_disagrees` (CLI `busy` vs mux `idle`, or CLI `idle` vs mux `working`, both settled ≥10 s) or `action: nested_child_observed` (a child CLI deterministically observed in this session's cwd). |
| `transition_refused` | The terminal latch refused a resurrection: a late hook tried to move an `exited`/`crashed` session. |
| `reopen_blocked` | The `closed_by_transcript` latch refused a late unordered begin that would have reopened `working` on a finished turn. |
| `screen_classifier_blind` | A witnessed `working` session's screen read `unknown` continuously for 120 s — the classifier cannot see this CLI generation (marker drift). |
| `foreign_conversation_hook_ignored` | A nested child CLI's hook was dropped before it could move state (`native_session_id` names the child). |
| `observer_fault` | The transcript observer crashed and was restarted (`error`, `restart_count`). |
| `hook_spool_discarded` / `hook_spool_replay` | Spooled hook fallback activity across daemon restarts (a discarded entry was older than the run/turn floor). |

## 3. Adjudicate: displayed state vs the layers

For the moment in question, from the timeline alone:

1. **What was displayed?** The last `transition` entry at or before the moment gives
   `state` + `detail` (+ `awaiting_reason`). Its `source`/`evidence`/`proof` say *why*.
2. **What did each layer say?** The last `layer_reading` per layer at or before the
   moment. Now compare:
   - Displayed `working`, `pty_tail: idle`, `cli_state: idle` → the session was actually
     done; the miss is in turn-end detection. Look for the close that never came: was
     there a Stop hook (`hook_recency` flipping fresh), a transcript close, or nothing?
     Was a `watchdog_recovery` late or absent? Was the screen classifier blind
     (`pty_tail: unknown` + `screen_classifier_blind`)?
   - Displayed `idle`, `pty_tail: working` or `cli_state: busy` → work the daemon never
     saw. Check `hook_recency: never/stale` (hooks not wired or dropped), `unwitnessed`
     in the state-log, and `foreign_conversation_hook_ignored` (the work may belong to a
     nested child, which is correct behavior). **`hook_recency: fresh` here is the
     signature of a transcript the daemon has lost**: hooks are arriving and being
     suppressed as redundant to a file that can no longer report anything. Confirm with
     `transcript_mtime: null` plus `parser_status: "ready"` in the state-log - the file at
     `transcript_path` does not exist, most often because the agent entered a worktree and
     the CLI moved its transcript to the new cwd's project directory. Expect a
     `transcript_relocated` event re-aiming the observer, or an `observation_stale`
     (`reason: transcript_missing`) revoking authority so hooks resume driving state.
   - Displayed `awaiting` long after the user answered → the known asymmetry (clears
     require positive proof). Look for the `resume_working` recovery and what delayed it.
3. **Which run?** If `runs` has several ids, check the transition to `starting` with
   evidence naming the rollover; rows are run-keyed, so a mixed-run reading means the
   incident spans a rollover seam — adjudicate each run separately.
4. **Was the machinery itself healthy?** `fleet_status_health.alarm_reasons`
   (`status_contract_violation`, `identity_collision`, `screen_classifier_blind`,
   `session_stuck_active`), `observer_fault` entries, `timeline_sink` loss counters, and
   `status_health.counters` (per session: `contract_violations`, `cli_state_disagrees`,
   `foreign_hook_ignored`, `reopen_blocked`, inferred-recovery counts).
5. **What was the agent actually doing?** The bundle's `transcripts` slice, by native
   timestamp. This is the ground truth the layers were trying to track: a tool call
   spanning the moment explains a long `working`; a final assistant message minutes
   before it confirms "actually idle".

## 3a. When the complaint is delivery, not display

"The session is ready and my queued message will not send" is the same investigation with a
different first question, because delivery reads facts the status ladder does not.
`GET /api/automation/injection-safety` gives the per-session checks and the blocking reason;
map it before touching the timeline:

| Reason | What it means | Where to look |
| --- | --- | --- |
| `transcript_stale` | The daemon believes the followed transcript is no longer this PTY's conversation. | The `observation_stale` events for the session, and whether an `observation_stale_cleared` followed. Compare their `transcript_mtime` against `transcript_growth_ts`: **an unmoving `transcript_mtime` beside a recent `transcript_growth_ts` is a filesystem that stopped dating a live file, not a stale conversation** (`design/features/backends.md`). Confirm against the file itself — its newest record's timestamp versus `stat().st_mtime`. |
| `terminal_input_after_completion` | Something advanced `input_revision` since the turn closed — usually real typing, historically also mouse reports. | `evidence.input_revision` vs `evidence.completion_input_revision`, and the `terminal_input` events in the window. |
| `root_agent_working` / `awaiting_*` | Delivery agrees with the display. | Treat it as an ordinary §3 status incident. |
| `no_root_lifecycle_evidence` | No turn has ever completed here that the daemon saw. | Expected on a brand-new session inside its settle window; otherwise a hook/transcript wiring problem. |

A refusal that repeats on a session the operator can see is idle is the dangerous shape, not
the harmless one: the only way to work is to override every time, which is how the
confirmation that exists to stop a genuinely unsafe send stops being read.

## 4. Corroborating surfaces (when §3 is not conclusive)

- `GET /api/diagnostics/status-health` — the live fleet aggregate (bounds + alarms).
- `GET /api/diagnostics/background` — was the state watchdog / cli-state poller / the
  `status-timeline-flush` loop actually running (restarts, faults, seconds since
  progress)? A dead watchdog loop explains missing recoveries and missing
  `layer_reading` flips.
- `GET /api/sessions/{sid}/state-log` (no range) — the live in-memory rings and the full
  current diagnostic fields, including `input_arbitration` when the complaint involves
  typed input going missing rather than status.
- `GET /api/history/{run_id}/transcript` — the full parsed conversation when the
  bundle's windowed slice is not enough.

## 5. Close the loop

A diagnosed incident is a regression-test candidate: capture the session through
`tests/support/status_capture.py` (scrubs prompt bodies, tool output, native ids,
terminal bytes) and promote the fixture into the detection corpus so the class of miss
fails CI from then on. If the diagnosis names a new detection gap rather than a fixture
gap, file it against `features/status-detection.md`'s ladder — with the timeline slice as
evidence.

## Worked example

*"Yesterday around 12:39 session `a1b2…` showed `working` but the agent was idle."*

1. Window: `from=12:24`, `to=12:54` as epoch seconds.
2. `GET /api/sessions/a1b2…/diagnostic-bundle?from=…&to=…` → `live: false` (the session
   was killed since), `runs: ["a1b2…"]`, timeline present.
3. Timeline around 12:39:
   - `12:31:04 transition working·Bash (source=transcript, proven)` — a turn opened.
   - `12:33:10 layer_reading pty_tail: working → idle` — the screen reached its prompt.
   - `12:33:12 layer_reading cli_state: busy → idle` — the CLI's own file agrees.
   - `12:35:00 layer_reading hook_recency: fresh → stale` — and no Stop hook ever came.
   - `12:41:55 cli_state status_disagrees (cli=idle, mux=working)` — the corroboration
     layer counted the contradiction.
   - No `watchdog_recovery` until `12:47:31 watchdog_recovery pty_idle_prompt`
     (`tail_verdict: open`) closing the turn as inferred idle.
4. Adjudication: displayed state lagged reality by ~14 minutes; every layer read idle
   from 12:33. The turn's terminal record never landed (`tail_verdict: open` — the close
   was missing from the transcript, consistent with the lost Stop hook), and the PTY
   backstop eventually recovered it. The gap between 12:33 and 12:47 is the incident;
   the transcript slice shows the last assistant message at 12:32:58, confirming idle.
5. `transcripts` + the timeline slice become the capture; the fixture pins the recovery
   at its threshold.
