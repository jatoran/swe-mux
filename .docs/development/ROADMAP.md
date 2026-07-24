# swe-mux roadmap v2

## Purpose

This roadmap is the active delivery plan after the Windows multiplexer, explicit-Project,
provider-account, and read-only control-plane foundations shipped. It sequences product
work by dependency: prove observation first, persist evidence second, expose deliberate
user actions third, and authorize bounded automation only after its safety predicates pass.

The original roadmap is preserved at `archive/ROADMAP.md`. Its completed Phases 0–7 are
historical records. Every incomplete Phase 8–12 item is carried into this roadmap's
Phases 7–11. Current behavior and invariants remain authoritative in `../design/`.

Checkboxes are completion records. A phase is complete only when implementation,
acceptance coverage, migrations, diagnostics, and relevant design/interface docs agree.

## Product direction

- swe-mux is a provider-neutral, out-of-band control plane over interactive Claude Code,
  Codex, and shell sessions; it is not a hidden orchestration framework.
- Durable evidence and explicit user intent precede autonomous action.
- Provider-native data is normalized at adapter boundaries. Unknown or degraded evidence
  fails closed wherever an action could enter a PTY, approve a request, or target another
  session.
- A PID, quota percentage, token count, inferred skill, or quiet terminal is never treated
  as proof by itself.
- Projects remain the only session, layout, note, file-resource, and project-configuration
  containers. Groups organize Project rows only.
- Browser, CLI, future mailbox clients, and integrations use the same typed daemon
  operations and authorization boundaries.
- Windows remains the proving platform through Phase 7. Platform expansion and public
  packaging remain late phases.

## Implemented baseline

- Windows ConPTY ownership, nested/global Job Objects, bounded scrollback/replay, resize,
  browser reconnect, multi-client input ownership, and daemon/session cleanup.
- Shell profiles, Claude/Codex adapters, in-place promotion, normalized lifecycle events,
  transcript reconciliation, agent history/resume, and current context usage in the
  session sidebar.
- Durable Project registry/Groups/sidebar visibility/layouts, Project and terminal-owned session
  notes, lazy Project file tree, global/project ignores, bounded leased file watches,
  revision-checked editors, Git status/worktrees, process inspector, listeners, and previews.
- Settings draft/save/discard flow, terminal/profile configuration, themes, commands,
  per-pane mixed-view tab stacks, non-native pointer drag/drop, projection-only mobile controls,
  and browser-native Web Speech STT.
- Optional cached `ccusage` analytics plus Claude/Codex saved-account quota polling and
  system-wide provider-account selection.
- Universal rules, normalized events, read-only OpenRouter observers, annotations,
  budgets, composite attention, fleet intelligence, and compatibility hooks.
- Session-preserving daemon reload (`pty_supervisor_enabled`, default off): an out-of-process
  PTY supervisor owns ConPTYs/scrollback/reaper Job so a daemon restart leaves agents running
  and the next daemon reattaches; intent-signaled shutdown (desktop Quit/Restart, terminal
  detach + `muxd --shutdown`) keeps explicit quit reaping cleanly. Reload triggers everywhere:
  UI menu/palette (`daemon.reload`/`ui.reload` via `POST /api/daemon/restart`),
  `mux reload-daemon`, and the frozen redeploy script `packaging/redeploy_desktop.py`, backed
  by the dedicated `dist/swe-mux-supervisor` bundle so app rebuilds never collide with a
  running supervisor. Design and completion checklist: `SESSION_PRESERVING_RELOAD.md`.

## Delivery order

```text
Phase 1  Evidence replay + delivery-readiness contract
  -> Phase 2  Durable process/quota/session telemetry
    -> Phase 3  Daily-workflow UX, prompts, config, and notifications
      -> Phase 3.5  Agent status-detection hardening and regression defense
        -> Phase 4  Persistent manual prompt queue
          -> Phase 5  Gated auto-delivery + mailbox + bounded agent communication
            -> Phase 6  Portable instructions and skills
              -> Phase 7  Windows maturity, CLI, doctor, and soak
                -> Phase 8  Telegram control
                  -> Phase 9  SSH/native attach
                    -> Phase 10  WSL bridge + Linux/macOS
                      -> Phase 11  Public packaging and release
```

Phase 3 interface work may proceed alongside Phase 2 when it does not depend on unfinished
telemetry. No Phase 4 or 5 delivery automation bypasses Phase 1 acceptance gates. Phase 3.5
hardens the lifecycle-state and readiness evidence those gates read from; it precedes any
delivery automation because an inaccurate `working`/`idle`/`awaiting` status silently
corrupts every downstream head-of-line, arming, and auto-delivery decision.
Cross-cutting tests ship with each phase.

## Phase 1 — Evidence replay and delivery readiness

Phase 1 makes adapter parsing, PTY observation, lifecycle state, and future injection
decisions reproducible. It produces no automatic PTY input.

### Versioned replay corpus

- [x] Add sanitized, versioned Claude and Codex fixtures covering native transcript and
  hook variants, partial writes, truncation, reordered records, unknown records, parser
  drift, and provider upgrades.
- [x] Normalize fixtures into golden event streams without exposing native schemas outside
  adapters. Preserve raw capability/version metadata for diagnosis.
- [x] Replay hook, transcript, PTY, process, and timer evidence with controllable ordering
  to test deduplication and race behavior deterministically.
- [x] Cover root turns, subagent/sidechain events, permission prompts, Q&A/elicitation,
  rate limits, stalls, cancellation, disconnect/reconnect, agent exit, shell demotion, and
  daemon restart.
- [x] Measure adapter fixture coverage, unknown-record rate, degraded-capability paths, and
  false lifecycle transitions; publish the results through test diagnostics.

### Delivery-readiness contract

- [x] Add provider-neutral `delivery_state = safe | blocked(reason) | unknown` separately
  from display/attention/agent state. Unknown always blocks automatic delivery.
- [x] Require positive root-agent prompt-ready evidence, compatible terminal mode, stable
  run identity, empty application composer, no partial human input, an attached exclusive
  input owner with a quiet human-input boundary, and adapter-declared delivery capability
  before `safe`.
- [x] Block on approval, Q&A/elicitation, rate limit, subagent-only stop, alternate screen or
  incompatible terminal mode, active user typing, disconnected ownership, stale evidence,
  ended/replaced run, and capability degradation.
- [x] Emit bounded transition evidence and reasons without storing terminal bytes or prompt
  bodies. Expose readiness read-only in diagnostics and the session inspector.
- [x] Add shadow evaluation that records when delivery would have occurred but never writes
  the PTY. Runtime diagnostics track states/reasons, unknown duration, and evidence freshness;
  replay oracles track false-safe and false-blocked decisions.

### State and parser regression matrix

- [x] Add sequence tests for working → approval → working → ready, working → Q&A,
  root/subagent overlap, rate-limit recovery, interrupted turns, compaction, resume, and
  promotion/demotion.
- [x] Test xterm/composer/input-owner races, bracketed paste, queued keystrokes, WebSocket
  reconnect, and focus changes around readiness transitions.
- [x] Require explicit fixtures for every new state-affecting Claude/Codex parser rule.
- [x] Make safe-to-inject changes fail CI when golden streams or conservative fallback
  behavior change without reviewed fixture updates.

### Phase 1 exit criteria

- [x] Claude/Codex lifecycle and delivery decisions are reproducible from fixtures across
  hook/transcript/PTY races.
- [x] No approval, Q&A, rate-limit, subagent-stop, unknown, or active-input fixture produces
  `delivery_state=safe`.
- [x] Shadow readiness is observable and causes no PTY writes or agent actions.

## Phase 2 — Durable operational evidence and telemetry

Phase 2 extends existing process, history, context, usage, and account systems. It records
facts and confidence; it does not auto-kill suspected processes or claim causal quota
attribution.

### Process ownership and orphan evidence

- [x] Persist bounded process observations, not authoritative live PID ownership: PID,
  creation time, executable/command hash, parent lineage, session/agent-run/Project owner,
  Job Object assignment result, first/last seen, exit evidence, and confidence.
- [x] Reconcile descendants by PID plus creation time during normal process-inspector
  polling. Handle PID reuse, inaccessible process details, vanished parents, and escaped
  descendants explicitly.
- [x] After a root session ends, wait a configurable bounded grace period and flag surviving
  attributable descendants as `suspected_orphan`; do not automatically terminate them.
- [x] On daemon startup, revalidate fingerprints and ownership before surfacing previous
  candidates. Mark unverifiable records stale rather than attaching them to reused PIDs.
- [x] Show active, exited, escaped, and suspected-orphan evidence in the process browser
  with reason/confidence, first/last seen, and explicit re-check before interrupt/terminate.
- [x] Bound retention and polling cost; ignored Project folders do not create filesystem
  watches, and process reconciliation does not scan unrelated system trees unnecessarily.
- [x] Add PID-reuse, daemon-death, child escape, inaccessible-process, and delayed-exit
  fixtures. Preserve existing Job Object kill-on-close behavior for owned processes.

### Quota samples, history, and reset detection

- [x] Add an append-only quota-sample store keyed by provider/account and sample time with
  session/weekly utilization, reset time, source, freshness, raw precision, error state,
  and active/inactive account status.
- [x] Retain the latest-snapshot API while deriving it from durable samples. Add retention,
  compaction, migration, and bounded query contracts.
- [x] Keep provider-safe polling limits. Permit event-triggered refresh after eligible root
  turn completion only behind a global minimum interval and without assuming immediate
  provider consistency.
- [x] Detect scheduled, unexpected, and uncertain reset events from fresh downward movement
  plus expected-reset tolerance. Confirm unexpected resets across two fresh samples and
  suppress account changes, stale/out-of-order samples, and authentication transitions.
- [x] Persist reset evidence: before/after values, expected reset, observed time,
  classification, confidence, and suppression reason. Maintain a browsable reset log.
- [x] Let users review a suspected reset as discarded detection evidence, or as manual Codex
  usage where that explanation is provider-valid; reviewed events no longer drive alert state.
- [x] Add a purple in-app reset indicator and optional sound only for confirmed unexpected
  reset events; support per-device mute and deduplication.

### Usage attribution

- [x] Correlate quota deltas with mux-owned session/turn/transcript activity in the sampled
  interval. Preserve an explicit unassigned/external remainder and overlapping-session
  ambiguity.
- [x] Optionally distribute mux-correlated activity among sessions using explicit native
  token evidence; never equate transcript tokens directly with provider quota weighting.
- [x] Report estimate ranges, confidence, sample gaps, concurrent-session ambiguity, and
  provider lag. Use “correlated with swe-mux activity,” never definitive personal identity
  or “someone else used X%.”
- [x] Validate estimates against synthetic timelines containing rounding, delayed quota
  updates, concurrent sessions, account switching, planned/unplanned resets, and external
  activity.

### Context, compaction, tools, and skills

- [x] Preserve current context usage in the sidebar and history. Record compaction count
  only from explicit provider-native evidence; token drops alone remain `unknown`.
- [x] Normalize explicit `context_compacted` records with backend capability and confidence;
  show count and last-compaction time where supported.
- [x] Build cross-Project historical tool metrics from explicit transcript records:
  counts, errors, duration when available, backend/model/Project/session grouping, and raw
  tool name plus normalized taxonomy.
- [x] Deduplicate hook/transcript copies and version provider-specific parsing. Report
  unknown/unmapped tools and reconciled-history coverage.
- [x] Count skill usage only when a provider records explicit invocation evidence. Do not
  infer skill use from prompt similarity, Markdown content, or generic file reads.

### Phase 2 exit criteria

- [x] Process, quota, reset, compaction, and tool records survive daemon restart with
  bounded retention and migrations.
- [x] Suspected-orphan UI never treats PID alone as identity and never auto-kills.
- [x] Unexpected reset alerts require confirmed fresh evidence; attribution UI remains
  explicitly probabilistic.
- [x] Parser and telemetry fixtures pass for Claude and Codex, including unknown/degraded
  paths.

## Phase 3 — Daily-workflow UX, prompts, configuration, and notifications

### Project and tab ordering

- [x] Make Project rows drag-reorderable using persisted `ProjectRecord.position`; normalize
  positions transactionally and preserve ordering across restart and concurrent clients.
- [x] Add keyboard/context actions for Move up/down so reordering is not pointer-only.
- [x] Add drag ordering for every workspace tab and persist open-tab order. Tabs can move
  between panes or create pane-edge splits, with exact insertion and split previews.
- [x] Make Project rows and tabs directly draggable without space-consuming handles while
  keeping session-row layout grouping unambiguous.
- [x] Replace native HTML drag loops with a pointer-capture gesture that provides a ghost,
  forgiving nearest-slot insertion, pane-edge/tab-bar indicators, and complete Escape/cancel/
  lost-capture cleanup across responsive transitions.

### Unified workspace panes

- [x] Replace the parallel terminal tree and dock/pop-out resource workspace with one
  optimistic-revisioned split tree whose panes can mix terminals, previews, Project note,
  Files, and file editors.
- [x] Make focus, tab navigation, reorder, detach, zoom, swap, and split behavior apply to
  every view kind. Preserve file drafts and file-tree expansion while views are reparented.
- [x] Migrate layout versions 1–5 on read. Convert a visible legacy resource workspace into
  an adjacent pane and treat a hidden workspace as closed; tolerate obsolete config fields
  without exposing them as current settings.
- [x] Move desktop app identity and daemon status into a persistent rail above the sidebar while
  keeping workspace tabs attached to their owning panes. Add device-local sidebar drag
  sizing/collapse and bottom-aligned, separate Claude/Codex account-status rows.
- [x] Keep one independent tab strip per desktop pane and project every pane into one mobile
  tab rail without mutating persisted desktop geometry. Preserve mobile tab creation, close,
  terminal rendering, and non-secure-context browser ID fallbacks.
- [x] Add a Projects manager independent of active sidebar visibility so configured Projects
  can be hidden without losing their sessions, layout, notes, settings, files, or history.

### Typed per-Project options

- [x] Expand `.swe-mux/config.toml` only with typed, portable, non-secret settings: preferred
  backend/profile, additive ignores, enabled prompt-library scope,
  and explicitly approved display/notification preferences.
- [x] Define global → Project → request precedence and validation for every setting. Show
  inherited versus overridden values and support reset-to-inherited.
- [x] Keep executable commands, hooks, credentials, bind/network authority, and automatic
  actuation out of repository-owned Project configuration.

### Universal prompt library

- [x] Add global and Project-local prompt templates with stable id, title, body, tags,
  variables, backend compatibility, source scope, timestamps, and schema version.
- [x] Store global templates in the mux data directory and portable Project templates under
  `.swe-mux/`; templates are non-executable text.
- [x] Add browse/search, command-palette invocation, favorites/recent use, variable preview,
  and explicit conflict handling for same-id global/Project templates.
- [x] Selecting a template populates the composer and never auto-submits in this phase.

### Session and reset notifications

- [x] Add per-device sound preferences for normalized top-level root events: turn complete,
  waiting for input, approval/Q&A attention, failure, and confirmed unexpected quota reset.
- [x] Exclude subagent/sidechain completion by default. Deduplicate hook/transcript sources
  and expose why a notification fired.
- [x] Reuse browser audio-unlock behavior and allow a bundled or user-selected safe sound.
  Do not make arbitrary script execution the universal default notification path.
- [x] Preserve existing notification inbox/toast behavior and support quiet hours, volume,
  test sound, and per-event mute.

### Voice boundary

- [x] Keep microphone capture app-owned in a secure browser context and transcription
  daemon-owned through offline Windows Speech Recognition or optional local faster-whisper.
- [x] Instrument capability/availability only; never retain audio or transcript content for
  analytics without explicit consent.
- [x] Bound utterance size and duration, delete temporary WAV input after every outcome,
  and commit buffered speech only through explicit wake commands.

### Searchable session archive

- [x] Make History an ordinary split/movable workspace tab with mobile unified-tab
  projection, role-aware match excerpts, transcript match navigation, and composed
  Project/provider/state/origin/time filters.
- [x] Add a rebuildable versioned SQLite FTS index over native user prompts and agent replies;
  preserve vendor transcripts as authoritative read-only sources.
- [x] Add cancellable Project-scoped complete-history scans with most-specific-root
  attribution, progress/results, parser/source watermarks, and serialized bounded writes.
- [x] Show chronological provider-native start/final-message times in result rows and timestamps
  on transcript messages. Handle out-of-order Claude records, current/legacy Codex record
  deduplication, and explicitly unavailable native timestamps.
- [x] Restore lazily initialized terminal-owned session notes for shell and agent sessions,
  expose them from terminal context menus and History, and persist note identity across exit.

### Phase 3 exit criteria

- [x] Ordering, Project options, prompt templates, and notification preferences persist and
  work with keyboard, pointer, responsive, and multi-client flows.
- [x] Prompt templates never submit or execute implicitly.
- [x] Root completion sounds exclude subagent-only stops and remain optional per device.
- [x] Project registry visibility, session-note recovery, searchable-history timestamps, and
  responsive workspace projection have regression coverage and current design/interface docs.

## Phase 3.5 — Agent status-detection hardening and regression defense

The user-visible session status (`starting | running | working | idle | awaiting | exited |
crashed`) is derived from a layered pipeline: transcript-authoritative ordering with source
priority `{pty:0, transcript:1, hook:2}`, a hook fallback while `watching`/`degraded`, the
quiescence + PTY watchdog, notification-type mapping, and the sibling-transcript
cross-attribution gate. It is correct on the common path but still shows residual
inconsistency — sessions that blink `working` after a turn ended, races that reopen a closed
turn, `awaiting` that misclassifies approval versus Q&A versus elicitation, and recoveries
that depend on inference rather than proof.

Phase 3.5 makes the user-visible status as reproducible and regression-guarded as Phase 1
made `delivery_state`, without loosening any conservative fallback and without introducing
PTY writes. It treats every inferred/watchdog recovery as a defect to be reproduced,
fixtured, and measured — not as acceptable steady-state behavior. Detection, delivery
readiness, and the UI indicator must agree, and that agreement must be provable from
fixtures and held by CI over time.

Reference material an agent picking this up must read first: the delivery-readiness contract
(`design/features/delivery-readiness.md`, `src/swe_mux/delivery_readiness.py`), the session
state machine and watchdog (`src/swe_mux/session.py`: `state_watchdog_loop`,
`_watchdog_check_session`, `_transcript_authoritative`, the `closed_by_transcript` latch),
the adapter/transcript observation path (`src/swe_mux/observation.py`:
`transcript_tail_turn_state`, `_pty_appears_idle`, `tool_call_evidence`), the existing golden
corpus (`tests/test_detection_replay.py` + `tests/fixtures/detection/v1/`), the live-agent
conformance harness (`tests/test_live_agent_conformance.py`), and the `GET
/api/sessions/{sid}/state-log` ring-buffer diagnostic.

### Status contract and evidence ledger

- [ ] Write down, per `SessionState` value, the exact positive evidence predicate that may
  set it and which sources (`pty`/`transcript`/`hook`/`watchdog`/`notification`) are allowed
  to, mirroring the `delivery_state` discipline. Ambiguous or absent evidence resolves to the
  conservative prior, never a guessed active state.
- [ ] Define and document the total mapping from `SessionState` (plus the `awaiting`
  sub-reason: approval / Q&A / elicitation) to the single user-visible status shown per
  session, and its relationship to `delivery_state` and attention. These three axes stay
  separate; the UI renders one coherent status without collapsing them incorrectly.
- [ ] Make the `state-log` ring buffer a complete, typed transition ledger: every transition
  carries prior state, next state, source, the evidence that justified it, whether it was
  inferred, and monotonic timing. No transition may occur without a ledger entry.
- [ ] Classify each transition as `proven` (hook/transcript/notification evidence) or
  `inferred` (watchdog/PTY backstop). Inferred transitions are recovery events, counted and
  bounded, never the primary path for a healthy session.

### Golden corpus extension to user-visible status

- [ ] Extend the detection replay corpus to assert `SessionState` (and `awaiting` sub-reason)
  at every checkpoint, not only `delivery_state`, `events`, and `parser`. The user-visible
  status becomes a golden-stream output with the same no-drift protection.
- [ ] Add deterministic fixtures for every documented failure mode, each with a root-cause
  note and the guard that closes it: hook/transcript race reopening `working` (late
  `PreToolUse`/`PostToolUse` landing after the transcript `end_turn`), the
  `closed_by_transcript` latch refusing a hook-sourced re-begin, ESC-pause-without-marker,
  observer stuck on a sibling transcript (cross-attribution), crash mid-turn, compaction,
  resume, promotion/demotion, `idle_prompt` versus `permission_prompt` versus
  `elicitation_dialog`, subagent-only stop, rate-limit abort, and daemon restart mid-turn.
- [ ] Pin the watchdog recovery paths as golden behavior, not incidental timing: ENDED-stuck
  force-idle at `STATE_WATCHDOG_ENDED_STUCK_SECONDS`, the PTY backstop force-idle at
  `STATE_WATCHDOG_PTY_STUCK_SECONDS` for both `unknown` and `open` tails, and the
  `_pty_appears_idle` true/false branches (a genuine long tool with "esc to interrupt" up
  must never be cut short).
- [ ] Make status-affecting parser, mapping, or watchdog changes fail CI when golden status
  streams change without a reviewed fixture update, mirroring the Phase 1 safe-to-inject gate.

### Edge-case inventory and closure

- [ ] Maintain an explicit, tracked inventory of every known status edge case with: a
  reproducing fixture, the guard that closes it, and a one-line root cause. Closing an edge
  case means both exist; removing either fails CI.
- [ ] Guarantee no session can remain in a non-terminal active state indefinitely: for every
  `working`/`awaiting` path there is a proven or bounded-inferred exit, and the watchdog
  bounds are covered by fixtures at their thresholds.
- [ ] Prove the cross-attribution gate: an observer bound to the wrong sibling transcript
  never sets this session's status from another session's evidence, and the PTY backstop (own
  session's ground truth) still recovers it.
- [ ] Prove notification semantics: `idle_prompt` maps to `idle` and never clobbers a real
  pending approval; `permission_prompt`/`elicitation_dialog` map to `awaiting` with the
  correct sub-reason.

### Regression detection over time

- [ ] Add a sanitized capture → golden-fixture pipeline: a real stuck or misclassified
  session's `state-log` (and the minimal evidence stream that produced it) can be captured,
  scrubbed of terminal bytes and prompt bodies, and promoted into the versioned corpus so it
  becomes a permanent regression test.
- [ ] Publish status-health metrics through test and runtime diagnostics: inferred-recovery
  count by source (`watchdog-ended`, `watchdog-pty`), reopen-after-authoritative count,
  unknown/open-tail durations, and time-to-terminal after a turn ends. A rise in inferred
  recoveries is a tracked regression signal, not silent drift.
- [ ] Extend the live-agent conformance harness to diff captured `state-log` transitions
  against expected proven-transition shapes for scripted real-CLI runs, flagging any run that
  reached a terminal status only via inference.
- [ ] Bound and alarm on the health metrics in soak: define the acceptable inferred-recovery
  rate for a healthy fleet and fail the soak matrix when it is exceeded.

### UI reflection correctness

- [ ] Add a frontend contract test that the `SessionState` → sidebar/pane indicator mapping is
  total and unambiguous: no state renders blank or as a permanent blinking `working`, and a
  terminal transition in the `state-log` always clears the working indicator.
- [ ] Assert the `awaiting` indicator distinguishes approval, Q&A, and elicitation with the
  correct affordance, and that `idle_prompt`-driven idle never renders as awaiting approval.
- [ ] Verify the mobile unified-tab projection shows the same status as the desktop pane for
  the same session, driven from the same evidence, with no independent heuristic.

### Phase 3.5 exit criteria

- [ ] Every `SessionState` transition (and `awaiting` sub-reason) is reproducible from
  fixtures across hook/transcript/PTY races and is asserted in the golden corpus, not only
  `delivery_state`.
- [ ] Every documented stuck-`working`, reopened-turn, misclassified-`awaiting`, and
  mis-attribution edge case has a named fixture and a guard; removing either fails CI.
- [ ] Inferred/watchdog recoveries are measured, bounded, and alarmed; a healthy session
  reaches terminal status by proven evidence, and a rise in inferred recoveries surfaces as a
  regression.
- [ ] Desktop and mobile UI reflect status through a total, tested mapping with no permanent
  `working` on a completed turn and correct `awaiting` sub-reasons.

## Phase 4 — Persistent manual prompt queue

Phase 4 introduces durable user intent but keeps delivery manual. The storage model is a
mailbox-shaped queue so later senders can be added without a migration into an orchestration
framework.

### Queue and message model

- [ ] Add persistent messages keyed to stable target agent-run/history identity with
  Project/session provenance, sender kind/id, ordered position, body, revision, timestamps,
  delivery constraints, and audit metadata.
- [ ] Make the sender/provenance model rich enough to carry a control-plane `queue_draft`
  on day one, not just a local user. Persist originating rule/observer id, the source
  Tier 0 fact(s)/fingerprint, and the annotation or fact snapshot that produced the draft,
  so drafts remain auditable back to their cause without a later schema migration. The
  queue-draft channel (`CONTROL_PLANE_ROADMAP.md` §13) is the first non-human sender and
  writes only inert drafts a human must arm and send.
- [ ] Use explicit states: `draft`, `armed`, `delivering`, `sent`, `blocked`, `failed`,
  `cancelled`, and `stranded`. State transitions are transactional and idempotent.
- [ ] Enforce strict head-of-line delivery: later messages may be armed, but an earlier
  unsent draft/armed/blocked item prevents downstream delivery until sent, cancelled, or
  explicitly skipped.
- [ ] Let users edit drafts and armed messages until delivery begins; each edit increments
  revision and records time. Sent/delivering items are immutable.
- [ ] Persist no hidden rendered prompt variant. The exact user-visible body being delivered
  is the audited body.

### Session-attached UI

- [ ] Add a Queue workspace tab attached to the target session/agent run, plus a compact
  queue count/status affordance in the session pane. It uses the same mixed-view pane system.
- [ ] Support add, insert after, reorder, arm/unarm, edit, cancel/skip, copy, and manual
  “send next now.” Sent messages remain visible, crossed out, and uneditable.
- [ ] Make lock/arm semantics explicit: any downstream item may be armed in advance, while
  strict ordering still blocks it behind earlier unsent items.
- [ ] Preserve queues when views close. If the target session/run ends, mark pending items
  stranded and offer explicit copy/retarget/cancel; never silently target a replacement or
  resumed run.
- [ ] Add history access for completed/stranded queues and bounded retention/export that
  excludes secrets by user choice.

### Manual delivery

- [ ] Route “send next now” through one typed daemon operation with target/revision/input-
  owner checks, clear blocked reasons, and idempotency key.
- [ ] Require explicit confirmation when readiness is blocked or unknown; do not offer a
  generic force-send that bypasses approval/Q&A/target identity protection.
- [ ] Record delivery attempt/result without duplicating prompt text into general event or
  automation logs.

### Phase 4 exit criteria

- [ ] Queue order/state survives daemon and browser restart without duplicate delivery.
- [ ] Closing a session strands pending work instead of losing or retargeting it.
- [ ] Every actual delivery is initiated by an explicit user action and is auditable.

## Phase 5 — Gated auto-delivery, mailbox, and bounded agent communication

Phase 5 authorizes narrowly scoped actuation after Phase 1 shadow evidence and Phase 4
manual-delivery reliability pass. It does not authorize model-selected actions,
auto-approval, arbitrary PTY writes, or uncontrolled relay chains.

### User-authored same-session auto-delivery

- [ ] Define quantitative promotion criteria for Phase 1 shadow readiness, including zero
  known false-safe deliveries across approval, Q&A, rate-limit, subagent, active-input, and
  run-replacement fixtures plus an operator-reviewed proving period.
- [ ] Add opt-in auto-delivery for armed, user-authored messages to the same live agent run
  only when `delivery_state=safe` remains stable for a bounded debounce window.
- [ ] Re-check target identity, message revision, head-of-line state, input ownership,
  composer state, terminal mode, and adapter capability atomically immediately before send.
- [ ] On uncertainty, remain blocked and surface the reason. Never retry blindly after
  partial/unknown PTY delivery; require user reconciliation.
- [ ] Provide pause-all, per-session enablement, expiry, maximum consecutive sends, quiet
  hours, audit view, and an emergency disable independent of provider availability.

### Human/device mailbox

- [ ] Expose the generalized message model with explicit sender provenance for local user,
  authenticated remote user/device, deterministic rule, and session/agent sources.
- [ ] Deliver human/device messages through the same queue and readiness contract; remote
  origin never weakens target selection, confirmation, expiry, or input-owner checks.
- [ ] Add inbox/outbox, delivery status, sender/target labels, retry-safe correlation, and
  revocation. Avoid creating a second transcript or conversation archive.

### Agent-to-agent communication

- [ ] Start with explicit user-authored or user-approved “send output from A to B.” Session A
  does not gain unrestricted knowledge of or authority over session B.
- [ ] Preserve source session/run, exact selected output span or annotation, requesting
  user/rule, target, transformations, and delivery result as provenance.
- [ ] Add target allowlists, maximum message/body size, expiry, rate limits, max chain depth,
  cycle detection, per-origin budgets, and loop kill switches.
- [ ] Require receiver-side readiness and queue policy. A message from another agent waits;
  it never interrupts an active turn or bypasses approvals/Q&A.
- [ ] Permit deterministic rules to enqueue only fixed/user-reviewed templates or bounded
  annotation output. Do not automatically lift arbitrary model output into another prompt.
- [ ] Keep autonomous model-authored routing, worker spawning, approval decisions, command
  execution, and arbitrary network destinations outside this phase.

Scope boundary (reconciling the `.swe-mux/notes/project.md` agent-to-agent request): the
desire for "agent A finishes a task and notifies a specific agent B, and sometimes spawns a
new session for B" splits across the trust line. **In scope for Phase 5:** user-authored or
user-approved A→B messages into an existing target run through the same queue/readiness
contract, carrying full provenance. **Not in scope — decision-gated:** an agent
autonomously selecting a target and *spawning* a new session to receive the message; that is
worker spawning behind the actuation gate (`CONTROL_PLANE_ROADMAP.md` §16) and requires a
separate product decision (see "Decision-gated capabilities"). Phase 5 delivers bounded
messaging between sessions that already exist; it does not let one agent create another.

### Phase 5 exit criteria

- [ ] User-authored same-session auto-delivery is opt-in, conservative, bounded, and shows
  no unsafe delivery in the proving corpus/period.
- [ ] Human/device and approved A→B messages retain provenance, cannot loop indefinitely,
  and never silently retarget ended runs.
- [ ] Disabling Phase 5 leaves the Phase 4 manual queue and ordinary agent sessions usable.

## Phase 6 — Portable instructions and skills

### Canonical instruction rendering

- [ ] Add an optional canonical shared instruction body, preferably
  `.swe-mux/instructions.md`, without replacing user ownership of `CLAUDE.md` or `AGENTS.md`.
- [ ] Render only sentinel-delimited generated sections into provider files. Never overwrite
  whole files or content outside owned sentinels.
- [ ] Add deterministic preview/diff, atomic write, source hash, generated hash, conflict
  detection, restore/backup, dry run, and manual sync before any optional autosync.
- [ ] Add an explicit manifest mapping canonical sources to nested target paths/scopes.
  Do not recursively discover and rewrite nested instruction files by default.
- [ ] Model nested precedence and symlink/path escape safety; a mapping cannot write outside
  the Project root or into an unapproved file.
- [ ] Add watcher-loop suppression and multi-client conflict tests. Autosync disables itself
  on ambiguous ownership or external edits.

### Prompt and skill portability

- [ ] Reuse Phase 3 prompt templates as the universal portable primitive. Keep template
  bodies separate from provider-specific invocation syntax.
- [ ] Define a canonical skill content model that separates portable Markdown body/assets
  from Claude/Codex-specific frontmatter, directory layout, capability declarations, and
  installation scope.
- [ ] Add provider adapters that validate and render metadata rather than copying entire
  skill directories blindly.
- [ ] Start with preview/export/import and explicit sync. Require conflict detection and
  provenance before considering autosync.
- [ ] Never sync secrets, executable trust decisions, provider caches, generated histories,
  or unsupported metadata by content similarity.

### Phase 6 exit criteria

- [ ] Shared instructions render reproducibly without changing unrelated provider content,
  including explicitly mapped nested files.
- [ ] External edits create a visible conflict instead of an overwrite loop.
- [ ] Skill portability preserves provider-specific validation and never claims unsupported
  equivalence.

## Phase 7 — Windows product maturity, CLI control, and diagnostics

This phase carries forward every incomplete item from original Roadmap Phase 8 and expands
its quality matrix with the Phase 1–6 contracts.

### Practical CLI control

- [ ] Expand `mux` into a practical daemon controller: filtered session listing;
  Project-bound profile/custom-argv spawn; rename/pin/kill; Project/Group management;
  repository-group inspection; broadcast membership/send; history filters/resume; profile
  inspection; queue/mailbox inspection; and safe Settings/config reads/updates.
- [ ] Keep browser presentation actions out of the CLI. CLI parity covers useful daemon
  control, not pane/modal presentation, visual focus, drag gestures, or theme preview.
- [ ] Resolve localhost, direct-tailnet, or optional Serve URLs from config while preserving
  explicit `MUX_URL` precedence.
- [ ] Use stable ids, conflicts for ambiguous names, actionable exit codes, structured
  errors, human-readable tables, and `--json`; scripts never parse UI prose.
- [ ] Route browser, CLI, mailbox, and future Telegram actions through shared typed daemon
  operations.
- [ ] Add read-only CLI inspection for automation status, normalized capabilities, rules,
  firings, annotations, observer spend/budgets, provider health, delivery readiness,
  process anomalies, quota/reset evidence, and message delivery status.
- [ ] Permit explicit enable/disable/shadow/dry-run operations through typed APIs. Never
  accept or print an OpenRouter/provider secret through ordinary output or JSON diagnostics.

### Consolidated diagnostics

- [ ] Expand `mux doctor` into a read-only diagnostic covering daemon/frontend version,
  ConPTY and Job Object health, shell/profile executables, Claude/Codex promotion,
  writable global/Project paths, Project config, artifact/migration conflicts, `ccusage`,
  process inspection/orphan evidence, previews/listeners, Tailscale/Serve, normalized
  observer/delivery capabilities, rule queue/last-known-good state, OpenRouter catalog,
  budgets, account quota sampling, queue/mailbox health, and instruction-sync conflicts.
- [ ] Publish machine-readable capability/version information through health diagnostics;
  redact secrets, terminal bytes, prompt/message content, media, and credentials.
- [ ] Give every failed check a concrete remedy and distinguish unavailable optional
  features from failures compromising terminal ownership, cleanup, or delivery safety.

### Windows soak and quality matrix

- [ ] Expand Python coverage for configuration/migrations, adapters/state races, lifecycle,
  Host/Origin/WS boundaries, Projects/layouts, history/resume, events/rules/annotations,
  OpenRouter fixtures, Project resources/accounts, Git/worktrees, CLI, process ownership,
  previews/reaping, telemetry, queues/mailboxes, and instruction rendering.
- [ ] Add real-browser/Playwright coverage for Project creation/folder selection,
  default/custom session creation, resources/autosave, replacement/kill, Projects/panes,
  account switching, palette/input transparency, Settings, history, processes/previews,
  quota/reset UI, prompt library/queue, clipboard media, tailnet, responsive/touch,
  orientation, focus management, drag ordering, and accessibility.
- [ ] Add real Windows ConPTY integration tests for paths with spaces/Unicode, large output,
  resize, Ctrl+C, bracketed paste, input-owner handoff, browser reconnect, process
  attribution, forced daemon death, manual queue send, and safe auto-delivery races.
- [ ] Maintain Windows CI for ruff, mypy, pytest, frontend typecheck/test/build, and focused
  ConPTY/browser smoke tests. Public artifact and multi-OS matrices remain Phase 11.
- [ ] Use the proving period to record observed workflow friction as explicit follow-up work
  without reopening completed decisions or silently expanding authority.

### Phase 7 exit criteria

- [ ] `mux` controls important daemon operations with stable human/JSON output while the
  browser remains the primary interactive interface.
- [ ] `mux doctor` identifies actionable local configuration, integration, ownership,
  tailnet, provider, telemetry, automation, and queue problems without mutation or leaks.
- [ ] Windows desktop/mobile core workflows, delivery-safety cases, and forced cleanup pass
  the focused automated matrix; unresolved friction is explicitly scheduled or rejected.

## Phase 8 — Telegram multi-session control

This phase carries forward original Roadmap Phase 9. Telegram consumes typed Phase 5/7
mailbox and daemon operations; it does not create a second session, observer, account, or
conversation model.

### Provider and routing

- [ ] Implement one daemon-owned Telegram adapter per configured bot token. Never start a
  competing poller per Claude/Codex session or depend on backend-native channel plugins.
- [ ] Persist opaque Telegram chat/message/thread/callback mappings to mux session/run and
  Project ids. Replies target their originating run; unthreaded messages require explicit
  active-session selection or a picker.
- [ ] Label outbound prompts, approvals, completions, reset alerts, and responses with
  backend, session, and Project identity.
- [ ] Support Select session, Open, Approve, Reject, Queue/Reply, and Clear selection only
  when the normalized typed operation authorizes it. Telegram never writes directly to a
  PTY, guesses display-name targets, or invents provider state.

### Configuration, safety, and reliability

- [ ] Keep Telegram optional/disabled by default. Store bot secrets outside public config,
  exports, and logs; expose enablement, allowlists, pairing, revocation, delivery status,
  and test notification in Settings.
- [ ] Enforce sender allowlists, pairing/revocation, polling/webhook exclusivity, update
  offsets, deduplication, body/media limits, retry/backoff, rate limits, expiry, and
  prompt-injection-safe confirmations.
- [ ] Preserve one-user history semantics. Telegram may report selected provider account
  but never captures/removes provider OAuth credentials or creates another archive.
- [ ] Persist correlation/delivery metadata without bot secrets, terminal bytes, message
  bodies, uploaded media, or backend credentials in general event/audit records.
- [ ] Route incoming prompts through the Phase 5 mailbox/readiness policy; remote origin
  never bypasses an unsafe/unknown delivery state.

### Phase 8 exit criteria

- [ ] One bot routes concurrent Claude/Codex notifications, queued replies, and supported
  approvals without ambiguous delivery.
- [ ] Selection, mappings, retries, deduplication, restart recovery, revocation, expiry, and
  delivery readiness pass provider-adapter and integration tests.

## Phase 9 — SSH and native terminal attach

This phase carries forward original Roadmap Phase 10. Direct Tailscale browser access
remains the supported remote product path.

### Forwarding and attach

- [ ] Document OpenSSH browser forwarding, WebSocket behavior, key authentication, daemon
  service lifetime, and differences from the supported direct Tailscale listener.
- [ ] Add `mux attach SESSION` over the existing PTY contract: raw input/output, resize,
  input ownership, exit status, reconnect, and a detach chord that never kills the
  daemon-owned session.
- [ ] Make browser and native attachments use the same explicit input-owner handoff.
  Read-only observers and queued delivery never duplicate terminal input/device responses.
- [ ] Add SSH-driven attach tests for disconnect/reconnect, Unicode, resize, Ctrl+C,
  bracketed paste, ownership handoff, queued-delivery exclusion, and daemon/session exit.

### Phase 9 exit criteria

- [ ] SSH disconnect leaves the mux session live; later attach restores interaction without
  changing browser replay/attach semantics.
- [ ] Documentation distinguishes session lifetime, SSH transport authentication, Tailscale
  access, input ownership, detach, and kill.

## Phase 10 — WSL agent bridge and native Linux/macOS

This phase carries forward original Roadmap Phase 11. Platform expansion preserves the
same API, browser behavior, session identity, attach/detach, evidence, and daemon-owned
child-lifecycle contracts.

### WSL agent bridge

- [ ] Build a distro-side bridge for native WSL Claude/Codex executable discovery,
  promotion/demotion, hook-secret delivery/execution, transcripts, and native-id
  correlation. Windows interop commands alone do not qualify.
- [ ] Translate Project, transcript, clipboard-media, preview/listener, instruction, and
  process ownership paths without leaking Windows-only paths or trusting guest listeners.
- [ ] Keep WSL profiles labelled `agent-bridge-unavailable` until native agents and
  promotion/state/history tests match Windows contracts.

### Platform interfaces

- [ ] Introduce `PtyHost` implementations for Windows ConPTY/pywinpty and Linux/macOS POSIX
  PTY through `forkpty`/`openpty` or a vetted equivalent.
- [ ] Introduce lifecycle/reapers: retain Windows Job Objects; on POSIX, a per-session
  guardian owns the process group and daemon pipe, then performs graceful signal, bounded
  wait, and group SIGKILL after daemon loss.
- [ ] Add a cross-platform process-inspection boundary for descendants, resources,
  signals/termination, anomaly evidence, and listener ownership.
- [ ] Add OS reveal services: Explorer, macOS `open`, and Linux `xdg-open`.
- [ ] Generate agent-promotion launchers per OS with safe structured argv/env/hook-secret
  propagation.
- [ ] Replace lowercased path comparisons with platform-aware same-file normalization for
  spaces, Unicode, symlinks, case sensitivity, UNC, and WSL paths.
- [ ] Make Project root and `.swe-mux/` resolution platform-aware across Git worktrees,
  non-repository cwd, symlinks, UNC, and WSL translation.
- [ ] Guard platform imports so config, CLI, package import, and non-PTY tests work on all
  targets. Adapt data directories, executable/transcript discovery, hook `run`, reveal,
  config migration, Web Speech documentation, and instruction rendering per platform.

### Native rollout

- [ ] Preserve the complete Windows regression contract while adding abstractions.
- [ ] Linux: PTY/process groups, bash/zsh/pwsh, Claude/Codex promotion/transcripts,
  `xdg-open`, Project files, processes/listeners, queue delivery, and daemon-death cleanup.
- [ ] macOS: PTY/process groups, zsh/bash/pwsh, promotion/transcripts, `open`, service
  environment behavior, Project files, queue delivery, ownership, and cleanup.
- [ ] Define/migrate data and config locations consistently for Windows `~/.mux`, XDG, and
  macOS platform conventions.

### Phase 10 exit criteria

- [ ] Windows, WSL-agent-aware, Linux, and macOS targets pass applicable API/WS/session,
  ownership, telemetry, readiness, and cleanup suites.
- [ ] Input, resize, Unicode widths, signals, clipboard/paste, replay, shell exit, agent
  promotion, attach ownership, queue delivery, and crash cleanup work on each target.
- [ ] Git/worktrees, history/resume, hooks, profiles, `.swe-mux/`, notes, instructions,
  processes/listeners, previews, and clipboard images work without escape/path leakage.

## Phase 11 — Public packaging and release

This phase carries forward original Roadmap Phase 12. Source-checkout development remains
acceptable until Windows proving and the supported platform matrix are complete.

### Artifacts and installation

- [ ] Guarantee every wheel contains a frontend bundle from the same revision; fail release
  validation on stale or missing assets.
- [ ] Complete package metadata/governance: license, URLs, platform classifiers, changelog,
  release policy, security/contact path, and accurate capability documentation.
- [ ] Test wheel/sdist install, upgrade, uninstall, config/database migration/backup,
  embedded frontend, and `mux`/`muxd` on clean machines without source checkout or Node.js.
- [ ] Validate `uv tool install swe-mux` and `pipx install swe-mux`; document clean install,
  upgrade, uninstall, logging, diagnosis, recovery, and backup.
- [ ] Add service/autostart recipes only after daemon-death child cleanup is proven for each
  supported target.

### Release automation

- [ ] Add final Windows/Linux/macOS CI for ruff, mypy, pytest, frontend typecheck/test/build,
  artifact-install smoke, browser smoke, platform PTY cleanup, and migration compatibility.
- [ ] Validate a TestPyPI alpha before reserving/publishing the PyPI package. Production
  publishing uses Trusted Publishing and no long-lived repository token.
- [ ] Validate tag, source, frontend bundle, wheel/sdist metadata, migrations, documented
  commands, and capability/version diagnostics as one release unit.

### Phase 11 exit criteria

- [ ] A clean supported machine can install, start `muxd`, open the bundled UI, create
  shells, promote agents, use declared optional capabilities, and stop without owned
  process leakage or message duplication.
- [ ] Artifacts upgrade/uninstall cleanly and public documentation matches the exact tag,
  supported platforms, security boundaries, and optional capabilities.

## Decision-gated capabilities

These remain recorded but are not committed roadmap work. Scheduling one requires a new
product decision defining authorization, trust, confirmation, audit, disablement, and
failure behavior:

- Repository-owned executable rules, project scripts, executable rulepacks, and a
  machine-owned fingerprinted trust store.
- Model-authored action selection, autonomous worker spawning, unrestricted PTY writes,
  auto-approval, arbitrary command execution, or arbitrary HTTP/network destinations.
- Alternate observer providers/base URLs that weaken the fixed-origin secret/network
  boundary.
- Autonomous agent-to-agent routing beyond Phase 5 user-authored/user-approved messages and
  bounded deterministic templates.
- Automatic termination of suspected orphan processes.
- Definitive identity attribution for shared-account quota usage.
- Bidirectional whole-file instruction sync or blind cross-provider skill-directory sync.
- A daemon-hosted STT service absent demonstrated browser STT product limitations.
- Native Claude/Codex theme management, ANSI rewriting, provider-native Remote Control,
  concurrent provider homes, automatic quota failover, public Funnel/LAN exposure, live
  session restore after daemon restart, and a skill/plugin marketplace.

## Original-roadmap carry-forward map

| Original roadmap item | Roadmap v2 destination |
|---|---|
| Phase 8 practical CLI | Phase 7 Practical CLI control |
| Phase 8 `mux doctor` | Phase 7 Consolidated diagnostics |
| Phase 8 Windows tests/CI/soak | Phases 1 and 7 |
| Phase 9 Telegram | Phase 8 |
| Phase 10 SSH/native attach | Phase 9 |
| Phase 11 WSL bridge and Linux/macOS | Phase 10 |
| Phase 12 packaging/release | Phase 11 |
| Reserved safe-to-inject predicate | Phases 1, 4, and 5 |
| Reserved relay/mail consumer | Phases 4 and 5, within bounded authority |
| Executable repository rules/trust store | Decision-gated; not scheduled |
| Model-selected actions/spawn/approval/arbitrary HTTP | Decision-gated; not scheduled |

## Completion policy

- Do not mark tasks complete from code presence alone; acceptance coverage and current docs
  are required.
- Do not weaken a conservative boundary merely to satisfy a phase checkbox. Record blocked
  evidence and revise the plan explicitly.
- Migrations are forward-safe, tested from supported prior schemas, and do not silently
  discard user state.
- Every background poller, watcher, queue, and retained history has explicit bounds,
  cancellation, freshness, and diagnostics.
- Every externally initiated or automatic action has stable target identity, provenance,
  idempotency, rate/loop bounds, auditability, and a kill switch.
- Move this roadmap to `.docs/development/archive/ROADMAP.md` only after every scheduled
  phase completes or is explicitly removed with a documented replacement plan.
