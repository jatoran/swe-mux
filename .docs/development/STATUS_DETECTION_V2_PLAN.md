# Status detection v2 — standing-activity annotations + detection-hierarchy consolidation

> **IMPLEMENTED 2026-07-31** — all four phases landed (A: model+plumbing, B: Claude
> signals, C: hierarchy, D: Codex+UI), each live-verified on the isolated daemon.
> `status-detection.md` is now the authoritative description; this file remains as the
> design rationale. Resolved [verify] items: cron jobs are session-only/in-memory (no
> on-disk store — transcript-only detection is complete); `~/.claude/sessions/<pid>.json`
> `updatedAt` is a status-change stamp, NOT a heartbeat (no staleness alarm built);
> observed `status` values are `busy`/`idle`; Codex has no side-state equivalent; the
> current CLI's background-wait footer is "N shell(s) still running · check the task
> status" (recaptured, pinned). Open per §7: cli-state promotion to a transition source
> awaits a release of `cli_state_disagrees` telemetry.

Implementation plan, written 2026-07-31 after the nested-child/marker-drift incident
(commit `bb81463`, `.docs/design/features/status-detection.md` § Foreign conversations).
Audience: the implementing agent. Everything here was verified against the live system on
this machine unless marked **[verify]**.

## 0. What this delivers

1. **Standing-activity annotations**: a session that is idle-and-green but has an armed
   loop, a cron schedule, running background tasks, or live subagents says so at a glance
   — without changing what green means. Idle stays idle; delivery stays safe; the standing
   engagement is a separate, composable annotation.
2. **A consolidated detection hierarchy**: status stops depending on CLI hooks as the only
   first-class live source. The daemon sits a level above the CLIs and should read every
   layer it owns: CLI-published side state, hooks, transcripts, the PTY screen, and the
   process tree — each with a defined rank, each feeding the same ledger.
3. **Drift alarms**: the marker-drift failure mode (screen classifier silently blind for
   an entire CLI generation) becomes self-detecting instead of user-reported.

## 1. Evaluation of the current system

### 1.1 What is strong (do not redesign)

- The **status contract** (`STATE_EVIDENCE_SOURCES`, `apply_state_transition`, the
  transition ledger with proven/inferred classification) and its regression defense
  (golden corpus, edge-case inventory, coverage matrix, capture pipeline). This is the
  right skeleton; v2 extends it, never bypasses it.
- **Source arbitration** (`{pty:0, transcript:1, hook:2}` within a turn, daemon force) and
  the watchdog recovery set, including the post-`bb81463` modern-marker classifier with
  real-capture fixtures (`tests/fixtures/pty_tails/`, `tests/test_pty_tail_modern.py`).
- **Identity discipline** (post-`bb81463`): rollover guard, foreign-conversation hook
  filter, heal-from-own-conversation. Annotations below must ride the same discipline
  (a nested child's loop is not this session's loop).
- The axis separation: SessionState vs `awaiting_reason` vs `delivery_state` vs attention.
  Annotations are a **fifth axis**, not new states.

### 1.2 Gaps (each verified live)

| # | Gap | Evidence |
|---|-----|----------|
| G1 | No representation of standing engagements. A `/loop`-armed or cron-scheduled session reads "ready · turn complete", indistinguishable from a finished one. | `sessionStatus.ts` renders only state + `idle_reason`; nothing in `SessionRecord` models loops/crons/subagents. |
| G2 | `idle_reason` is single-valued and PTY-only. "Background tasks AND a loop" cannot be expressed; `PTY_BACKGROUND_WAIT_MARKERS` ("waiting for", "background task") were never re-verified against the current CLI (the 2026-07-31 captures did not include a background-wait screen). | `session.py::pty_tail_waiting_on_background`; capture set in `tests/fixtures/pty_tails/`. |
| G3 | **Claude's CLI-published session state is unused.** `~/.claude/sessions/<pid>.json` carries `{sessionId, cwd, pid, procStart, kind, name, status, statusUpdatedAt, updatedAt, version}` — live, hook-free, CLI-authoritative. Observed `status: "busy"` on two running sessions; the file heartbeats (`updatedAt` advances during a turn). Nothing in `src/swe_mux` reads it. | Verified on disk 2026-07-31. |
| G4 | Subagent lifecycle hooks are never registered. `adapters/claude.py::_write_hook_settings` omits `SubagentStart`/`SubagentStop`, so subagent evidence arrives only via sidechain transcript records (which `_claude` maps to `subagent_activity` events consumed by delivery-readiness/auto-delivery — never by status display). | `claude.py:104-116`; `grep subagent_activity` → `delivery_readiness.py`, `auto_delivery.py`, `observation.py` only. |
| G5 | Process-tree evidence isn't folded into status. `process_observed` events already carry `descendants` counts (ProcessInspector), but nothing correlates "descendants appeared/persisted" with background work or nested agents. | `processes.py`; replay harness `process` step. |
| G6 | Screen-classifier drift has no alarm. The 2026-07-31 incident ran for weeks of CLI releases with `pty_tail_state` returning "unknown" on every busy screen; nothing counted it. | Incident postmortem; `status_health` has no classifier-liveness counter. |
| G7 | Startup dialogs read `idle`. Claude's workspace-trust dialog (SessionStart hook fires before the trust gate) and Codex's trust/update dialogs block the session while state shows idle. Since `bb81463` the classifier at least reads them as `approval` ("enter to confirm"/"esc to cancel"), but hook-sourced idle wins the displayed state. | Measured in E2E 2026-07-31; also `status-tracking-open-issues` memory. |
| G8 | Codex is structurally thinner: no session-start hook, no known per-pid side state **[verify]**, dialogs invisible pre-first-turn. | `backends.md`; unwitnessed-session machinery exists precisely because of this. |
| G9 | Loop/cron *lifecycle* is invisible even to transcripts consumers: `ScheduleWakeup` and `CronCreate` tool_use records flow through the observer today but are classified as generic tool activity. | Verified record shapes in live transcripts: `{"delaySeconds": 1500, "prompt": "<<autonomous-loop-dynamic>>", "reason": ...}` and `{"stop": true}`. |

## 2. Design: standing-activity annotations

### 2.1 Model

New per-session field `standing_activity: list[StandingActivity]` on `SessionRecord`
(snapshot-serialized, present in `/api/sessions`, `/api/sessions/{sid}`, state-log, and
the events fanout).

```python
@dataclass
class StandingActivity:
    kind: Literal["loop", "cron", "background_tasks", "subagents"]
    source: str            # same vocabulary as transitions: cli-state|hook|transcript|pty|process
    evidence: str          # e.g. "transcript:ScheduleWakeup", "hook:SubagentStart"
    since: float
    expires_at: float | None   # self-expiry; None = until positively cleared
    count: int = 1             # subagents/background tasks
    detail: str | None = None  # bounded; e.g. loop reason, next-fire ETA, cron cadence
```

Rules — these are the contract, mirror them in `status-detection.md`:

- **Not states.** SessionState and `awaiting_reason` are untouched. `delivery_state` is
  untouched (a loop-armed idle session is exactly as deliverable as an idle one — that is
  the user-facing point). The existing `idle_reason: waiting_on_background` is *migrated
  into* `background_tasks` (keep the field one release for UI compat, derived from the
  annotation, then drop).
- **Additive and composable.** A session can hold `loop` + `background_tasks` + `subagents`
  simultaneously; the UI composes them.
- **TTL'd, never latched.** Every annotation carries `expires_at` where the evidence
  implies one (loop: record ts + `delaySeconds` + 120 s slack; subagents: refresh on any
  subagent evidence, expire after `SUBAGENT_QUIET_SECONDS` ≈ 120 s without any;
  background: refresh on task evidence). A wrong annotation must decay on its own —
  this is the lesson of every stuck-status incident in this codebase.
- **Run-scoped.** `_apply_conversation_rollover`, `promote`, `demote`, `_mark_ended`, and
  `_heal_claude_identity` clear the set (same places that reset `observation_state`).
  Foreign-conversation hooks (the `bb81463` filter) must be dropped *before* annotation
  extraction — a nested child's ScheduleWakeup is not this session's loop.
- **Ledgered.** Additions/removals/expiries append non-transition ledger entries
  (`kind: "standing_activity"`, action added|removed|expired) to `state_transitions`, and
  `status_health.counters` gains `standing_activity_expired` (an expiry without a positive
  clear is a small drift signal worth counting).

### 2.2 Per-annotation detection (Claude)

**`loop`** (dynamic `/loop` via ScheduleWakeup):
- Transcript observer (`observation.py::_claude`): assistant `tool_use` named
  `ScheduleWakeup` → if `input.stop` truthy → clear; else arm with
  `expires_at = record_ts + delaySeconds + slack`, `detail` from `reason`. The tool_result
  confirms scheduling **[verify: result shape on failure]**.
- The wakeup firing arrives as a new user record (the loop prompt / sentinel
  `<<autonomous-loop-dynamic>>`) → normal turn start; re-arm happens when the model calls
  ScheduleWakeup again. Between expiry and re-arm the annotation may briefly drop — the
  slack absorbs the normal case; do not chase perfection here.

**`cron`**:
- Transcript: `CronCreate` arms (parse cadence into `detail`; `expires_at = None`),
  `CronDelete` clears by job reference, `CronList` results refresh truth.
- **[verify]** Locate Claude Code's on-disk cron store (none exists on this machine —
  no cron has ever been created here; enumerate by creating one in a scratch session and
  diffing `~/.claude`). If a store exists, prefer polling it (survives daemon restarts and
  sessions that created crons before mux observed them); transcript evidence then becomes
  the fast path and the store the reconciler. If crons are server-side (cloud routines),
  transcript-only is acceptable — say so in the doc.
- Cron annotations should also survive daemon restart via the session snapshot the
  supervisor already persists **[verify how `SessionRecord` extras round-trip adoption]**.

**`background_tasks`**:
- Transcript: `Bash` tool_use with `run_in_background: true` opens/increments; the
  matching task-completion evidence (task-notification user records, `TaskStop`/
  `BashOutput` reads **[verify exact record shapes]**) decrements.
- PTY: re-capture the current CLI's background-wait line with the env-scrubbed probe
  (`claude-screen-capture-probe` memory) and fix `PTY_BACKGROUND_WAIT_MARKERS` if drifted.
  The PTY line only exists at turn end, so it corroborates rather than counts.
- Process tree: `process_observed.descendants > 0` sustained after turn end corroborates;
  descendants dropping to 0 fast-clears the annotation (this is the strongest *clear*
  signal — a vanished process cannot still be working).

**`subagents`**:
- Register `SubagentStart`/`SubagentStop` in `_write_hook_settings` (one-line change;
  hooks carry the root `session_id`, so the foreign filter composes correctly). Count
  = starts − stops with floor 0; refresh TTL on any sidechain evidence.
- Transcript: `Task` tool_use (sidechain launches) and `isSidechain` record recency as
  the fallback tier when hooks are lost (spool covers restarts for Stop-class events
  only; subagent hooks are not spool-durable — acceptable, the TTL decays).
- Render on the working axis too: `working · 3 subagents` (the user explicitly asked for
  "main session waiting on subagents" visibility; the root turn is open during Task tool
  execution, so this is a working-state detail, not an idle annotation).

### 2.3 Per-annotation detection (Codex)

- No loop/cron equivalents exist in the Codex CLI today — annotations stay empty rather
  than being faked. **[verify per current codex release]**
- `subagents`: `sub_agent_activity` transcript events already exist; wire count/TTL the
  same way. No lifecycle hooks exist; TTL is the only clear.
- `background_tasks`: codex `exec` children via process descendants only.
- Keep every extractor in the per-backend adapter path (`_claude` / `_codex`), not in
  shared code with `if backend` branches — match the existing structure.

## 3. Design: the detection hierarchy (control-plane consolidation)

### 3.1 The ladder

Formalize (in `status-detection.md`) the full evidence ladder. Per signal class, sources
ranked; every layer feeds the same ledger with its own `source` string:

| Layer | Source tag | What it may do |
|---|---|---|
| CLI side state (`~/.claude/sessions/<pid>.json`) | `cli-state` | Corroborate/alarm + identity. **Phase 1: never drives SessionState.** |
| Hooks (existing + SubagentStart/Stop) | `hook` | As today (priority 2) |
| Transcript records | `transcript` | As today (priority 1) + annotation extraction |
| PTY screen classifier | `pty` / `watchdog-pty` | As today + drift self-check |
| Process tree (ProcessInspector) | `process` | Annotation corroboration + fast-clear; liveness |
| Daemon lifecycle | `daemon` | As today (force) |

### 3.2 The `cli-state` poller (new, Claude)

A daemon poll loop (piggyback on the existing 5 s watchdog pass; stat-then-parse-on-change
so cost is one `stat` per file) over `~/.claude/sessions/*.json`:

- **Mapping**: file → session by matching `sessionId` against live sessions'
  `native_session_id` (and `pid`/`procStart` against the supervisor's PTY child pids —
  `procStart` makes pid reuse safe).
- **Identity corroboration (the quiet superpower).** A file whose `pid` is a descendant
  of session S's PTY but whose `sessionId` ≠ S's bound conversation is a *nested child
  agent detected deterministically* — the signal the `bb81463` incident had to infer from
  hook `source` fields. Feed it to: (a) the identity sweep as confirmation, (b) a possible
  future `nested_agent` annotation, (c) `status_health` (`nested_children_observed`).
- **Liveness/staleness**: `updatedAt` is a heartbeat. Heartbeat fresh + hooks silent +
  transcript quiet = alive-but-idle (protects against false stale alarms); heartbeat stale
  while state is working = new health counter `cli_state_heartbeat_stale`, a strong input
  to the 900 s stuck alarm (can justify lowering it).
- **Status corroboration**: enumerate the `status` enum empirically (observed: `busy`;
  expect at least an idle-like value; **[verify]** whether a blocked permission prompt gets
  its own value — if it does, this is a hook-free `awaiting` source and G7's cleanest fix).
  Log mismatches (`cli_state_disagrees` counter) for one release *before* letting it drive
  transitions; promote to a transition source only with corpus fixtures.
- Codex: **[verify]** whether any equivalent exists under `~/.codex`; if not, this layer is
  Claude-only and the doc says so.

### 3.3 Classifier drift self-check (G6)

In the watchdog pass: if a session has been `working` on proven hook/transcript evidence
for ≥ 120 s continuously while `pty_tail_state` returns `unknown` on every read in that
window, increment `screen_classifier_blind` (per-session counter + fleet aggregate +
`alarm_reasons` entry when the fleet-wide blind ratio exceeds ~50% of busy time). This
turns the next CLI redesign into an alarm within minutes instead of a user report. Zero
false-positive cost: it changes no state, it only counts.

### 3.4 Startup dialogs (G7)

Allow a narrow new watchdog rule: state `idle`, **no turn has ever run this agent run**
(`agent_run_started_at` recent, no `turn_started` in ledger), and `pty_tail_state` reads
`approval` for ≥ 10 s → transition to `awaiting(approval)` with source `watchdog-pty`,
evidence `startup_dialog`, inferred. The same rule un-blocks: screen leaves `approval` →
return to idle. Gate hard on "no turn yet" so it can never fight mid-conversation hook
evidence. Covers Claude trust and Codex trust/update dialogs in one rule. Corpus fixture +
inventory entry required (`startup_dialog_reads_idle`).

## 4. UI/UX

One recommendation, not options (rationale below):

- **Dot colors do not change.** Green keeps meaning exactly "ready — you can type and
  send". Hue variants of green fail at a glance and fail colorblind users.
- **Standing activity renders as a compact affordance next to the dot plus status-line
  text**, through the existing single mapping (`sessionStatus.ts` — extend, never fork):
  - New `activityBadges(session): {glyph, label, title}[]` — `⟳` loop/cron (one glyph for
    both; `title` distinguishes "loop armed · next ~12m" vs "cron · daily 9am"), `⑂` (or
    layered-dots) subagents with count, `≡` background tasks with count.
  - Status line composes: `ready · turn complete · ⟳ loop armed`,
    `working · Task · 3 subagents`, `ready · 2 background tasks`.
  - Sidebar rows / tab strips (dense): render the glyph(s) only, after the dot, dimmed to
    the dot's palette; full text lives in the pane header and tooltip. Mobile unified-tab
    projection gets the same glyphs.
  - `frontend/test/sessionStatus.test.ts`: extend totality assertions — every annotation
    kind renders a glyph and a label; idle+loop must still classify as ready (assert the
    dot class is unchanged by annotations).
- Notification policy: annotations do not suppress or add sounds in v1, with one
  exception — keep the existing `waiting_on_background` end-of-turn sound suppression,
  now driven by the `background_tasks` annotation. A loop-armed turn end still notifies
  (the user said: ready means ready).

## 5. Regression defense (extend, same machinery)

- **Corpus fixtures** (new, `tests/fixtures/detection/v1/`): loop lifecycle
  (arm → wake → re-arm → stop; expiry without stop), subagent up/down via hooks and via
  transcript-only, background task open/close + process fast-clear, rollover/heal clearing
  annotations, foreign child's ScheduleWakeup ignored, startup-dialog rule (claude + codex).
- **Coverage matrix**: `test_replay_corpus_covers_phase35_status_matrix` grows a clause —
  every annotation kind must appear in the corpus with at least one add and one clear.
- **Real captures**: background-wait and subagent-running screens captured with the
  env-scrubbed probe and pinned in `tests/fixtures/pty_tails/` (see
  `claude-screen-capture-probe` memory for the procedure and its traps — the env scrub is
  what protects the live daemon).
- **Inventory entries**: `startup_dialog_reads_idle`, `screen_classifier_blind_alarm`,
  plus one per annotation source that has a known failure mode.
- **Live canary**: extend `tests/test_live_agent_conformance.py` with a loop-armed and a
  subagent scenario (opt-in markers as today).

## 6. Phasing, files, acceptance

**Phase A — model + plumbing** (no behavior change visible yet):
`models.py` (StandingActivity, snapshot), `session.py` (set management, TTL sweep in
watchdog pass, clears in the five lifecycle sites, ledger entries, health counters),
`server.py` (snapshot passthrough), `sessions.md`/`data-model.md`/`interfaces.md` docs.
*Accept:* snapshots carry an empty `standing_activity: []`; suite green.

**Phase B — Claude signals**: transcript extractors in `observation.py::_claude`
(ScheduleWakeup, CronCreate/Delete/List, run_in_background, Task), SubagentStart/Stop in
`adapters/claude.py::_write_hook_settings` + handling in `apply_hook_observation`
(scope-aware: these arrive subagent-scoped — route them to annotation management before
the subagent early-return), cron-store investigation.
*Accept:* corpus fixtures for each lifecycle pass; live: a real `/loop` session on the
isolated daemon (8799 + `~/.mux-hardening`, primary checkout only — see
`isolated-daemon-testing` memory) shows `⟳` while idle and drops it after `stop:true`.

**Phase C — hierarchy**: `cli-state` poller module (new file, e.g.
`src/swe_mux/cli_state.py`, wired from SessionManager), identity corroboration + health
counters, classifier drift self-check, startup-dialog watchdog rule, background-wait
marker recapture.
*Accept:* poller disagreement counter visible in `/api/diagnostics/status-health`; blind
counter fires when markers are artificially broken in a test; trust-dialog E2E on the
isolated daemon shows awaiting-approval within 15 s of spawn into an untrusted dir.

**Phase D — Codex parity + UI + polish**: `_codex` extractors, codex side-state
investigation, `sessionStatus.ts` + CSS + tests, mobile projection, docs sweep
(`status-detection.md` gains a "Standing activity" section and the ladder table;
routing per `.docs/CLAUDE.md`).
*Accept:* frontend tests assert totality + dot-class invariance; `npx tsc --noEmit`,
`npm test`, full backend suite, `gwt land`.

**Ordering constraint**: A before B/C (both write annotations); B and C are independent;
D last. Each phase lands separately through `gwt land`.

## 7. Open questions (defaults chosen — override if wrong)

1. Should `cron` annotations persist across daemon restarts even if no on-disk store is
   found (i.e., persist in mux's own DB)? **Default: yes**, in the session snapshot the
   supervisor already round-trips; a cron is precisely a standing fact that outlives a
   process.
2. Should `cli-state` ever be promoted to a SessionState *driver* (not just corroboration)?
   **Default: revisit after one release of disagreement telemetry**; promotion needs its
   own corpus fixtures and an entry in `STATE_EVIDENCE_SOURCES`.
3. One glyph for loop and cron, or two? **Default: one (⟳) with distinguishing tooltip** —
   sidebar density beats taxonomy.
