# swe-mux roadmap v2

## Purpose

This roadmap is the active delivery plan after the Windows multiplexer, explicit-Project,
provider-account, and read-only control-plane foundations shipped. It sequences product
work by dependency: prove observation first, persist evidence second, expose deliberate
user actions third, and authorize bounded automation only after its safety predicates pass.

The original roadmap is preserved at `archive/ROADMAP.md`. Its completed Phases 0–7 are
historical records. Every incomplete Phase 8–12 item is carried into this roadmap's
Phases 7–11. Current behavior and invariants remain authoritative in `../design/`.

Control-plane work is planned in `CONTROL_PLANE_ROADMAP.md`, which is authoritative for its
own scope and design. The decimal phases here (3.7, 4.5, 5.5, 6.5, 7.5) pin that document's
build-order steps into this delivery order so the two tracks progress in one sequence rather
than two; see "Control-plane track interlock".

This roadmap is not the whole plan inventory.
Work planned in a sibling `development/` document is listed under "Plans not sequenced here"
with its status, so that this file's silence on a subject is never read as evidence that
nothing is planned.

Checkboxes are completion records. A phase is complete only when implementation,
acceptance coverage, migrations, diagnostics, and relevant design/interface docs agree.

## Product direction

- swe-mux is a harness-neutral, out-of-band control plane over interactive agent CLIs and
  shell sessions; it is not a hidden orchestration framework.
  Harnesses are declared in a registry (`harness.py`) rather than named in code: `claude`,
  `codex`, and `omp` (oh-my-pi) ship today, capability is two independent axes
  (`state_sources`, `measurement_source`) with the display tier derived from them, and adding
  a fourth is a descriptor plus its adapter rather than a new branch in every consumer.
  Phase text that still names Claude and Codex specifically means those two harnesses, not
  the shape of the surface.
- Durable evidence and explicit user intent precede autonomous action.
- Provider-native data is normalized at adapter boundaries. Unknown or degraded evidence
  fails closed wherever an action could enter a PTY, approve a request, or target another
  session.
- A PID, quota percentage, token count, inferred skill, or quiet terminal is never treated
  as proof by itself.
- Projects remain the only session, layout, note, file-resource, and project-configuration
  containers. Groups organize Project rows only.
  The global Scratchpad is the one deliberate exception: it is explicitly not Project-owned,
  because its job is to survive Project switching.
- Browser, CLI, future mailbox clients, and integrations use the same typed daemon
  operations and authorization boundaries.
- Windows remains the proving platform through Phase 7. Platform expansion and public
  packaging remain late phases.

## Implemented baseline

- Windows ConPTY ownership, nested/global Job Objects, bounded scrollback/replay, resize,
  browser reconnect, multi-client input ownership, and daemon/session cleanup.
- Shell profiles, the harness registry with its `claude`/`codex`/`omp` descriptors and
  adapters, in-place promotion, normalized lifecycle events, transcript reconciliation,
  agent history/resume, and current context usage in the session sidebar.
  Transcript resolution is the adapter's answer rather than one shared filesystem heuristic:
  Claude resolves by working directory and is followed when the CLI relocates the file into a
  worktree slug, Codex resolves by thread id so a resume continues its conversation instead of
  forking a second history row.
  Completion record: `archive/HARNESS_ABSTRACTION_AND_OMP.md`.
- Durable Project registry/Groups/sidebar visibility/layouts, per-Project note collections and
  the non-Project global Scratchpad, lazy Project file tree, global/project ignores, bounded
  leased file watches, revision-checked editors, Git status/worktrees/diff review, process
  inspector, listeners, and previews.
- Sessions launched directly into a configured Git worktree, bootstrapped per harness, with the
  pending setup visible as a session rather than as a silent gap.
- Manual "send to agent" from every Continuity-backed Markdown view (Project note, Scratchpad,
  Markdown file): the selection, or the whole document, seeds a new agent session through
  the agent CLI's argv or is written into a live agent session over the input endpoint, targeted
  by a Project/session picker that excludes shells and dead sessions. Delivery is per-message and
  user-initiated; the durable queue behind it is Phase 4 (see that phase's sender items).
- Settings draft/save/discard flow, terminal/profile configuration, themes, commands,
  per-pane mixed-view tab stacks, non-native pointer drag/drop, a configurable sidebar session
  row, per-device command-rail layouts, UI scale controls, and mobile surfaces with their own
  keyboard viewport, persistent terminal drafts, and drawer projection.
- Hands-free voice: browser capture through Silero VAD into daemon-owned faster-whisper with
  two decode profiles, wake words over the command registry, spoken navigation and fleet
  queries, TTS read-aloud with barge-in, and Voice Comms.
  Decisions: `archive/VOICE_INTERACTION_ROADMAP.md`.
- Optional cached `ccusage` analytics plus saved-account quota polling and system-wide
  provider-account selection for the harnesses that declare account management.
- Universal rules, normalized events, read-only OpenRouter observers, annotations,
  budgets, composite attention, fleet intelligence, and compatibility hooks.
- Attention ranking (Phase 6.5): detector findings and fleet faults grouped into incidents and
  routed to four cost-to-resolve channels under a hard daily interrupt budget, with a measured
  fan-out estimate, OSC 133 breakpoint delivery from the user's own shells, behaviour-mined
  demotion rules that require explicit acceptance, the absence digest with rollover boundaries,
  and optional budgeted narration. In-app only: nothing here reaches a device.
  `design/features/attention-ranking.md`.
- Status detection v2 and its durable diagnostics: the standing-activity axis, the `cli_state`
  layer, the persisted `status_timeline` ledger with on-change layer readings, and the
  time-ranged state-log and diagnostic-bundle endpoints behind `STATUS_INCIDENT_RUNBOOK.md`.
- Agent Environment (bounded passive configuration inventory per session) and Agent Context
  (Project-root instructions plus provider learned memory, read-only, with the manual
  `CLAUDE.md` ↔ `AGENTS.md` overwrite).
- Web push with server-persisted preferences, device presence and leading-device routing, the
  settle-gated `waiting` alert, and per-session mute.
- Trusted task imports and the Project Run menu, clipboard capture with a history ring,
  headless-browser ghost-window sweeping, preview screenshot capture, and background-task
  annotations bounded to the work they name.
- Performance and traffic substrate: per-loop cost accounting, event-loop lag sampling,
  Windows timer-resolution handling, HTTP/WebSocket traffic accounting with response and static
  precompression, PTY replay and input-latency instrumentation, and the bandwidth metrics
  surface behind `PERFORMANCE_RUNBOOK.md`.
- Control-plane build-order steps 0–2 (`CONTROL_PLANE_ROADMAP.md` §9): the per-project
  enablement framework with its cycle-checked dependency DAG (`automation_registry.py`),
  Tier 0 deterministic fact capture with source pointers (`tier0_store.py`), and the
  helps-today siblings (observation inbox, preview screenshot capture). Step 1 retains one
  known gap: git commit/tree hashes and read-side file hashes, which block the provenance
  graph.
- Control-plane build-order step 3 (Phase 3.7): the four model-free detectors over Tier 0
  (loop/stall with a no-progress gate, declared-vs-verified, doc-debt ledger, provenance
  edges), the annotation anchor/evidence/dedupe schema they needed, and the per-project
  enablement toggle surface. `design/features/deterministic-consumers.md`.
- The 2026-07-27 hardening audit backlog is closed (P0/P1 in the prior pass, P2 and the
  Appendix A leads in this one). Highlights: per-store schema versions and a corrupt-database
  quarantine replacing a startup crash, `AutomationStore` migrations and named-column writes,
  explicit-Project identity threaded through every project-resource helper (the nested-Project
  bleed), transcript sidechain/orphan-file exclusion and size-keyed parse caching, supervisor
  liveness distinguished from supervisor death, watchdog re-derivation after the threaded tail
  read, `resume_working` confined to answered approvals, durable blocking-hook spooling, and
  the `idle(waiting_on_background)` sub-state.
- Agent conversation rollover (Phase 5.4): an in-CLI `/clear` or `/new` is a new `agent_run_id`
  rather than a silent conversation swap under a live run. Claude's own `SessionStart` hook is
  the trigger (immune to the sibling gate); the transcript-switch watcher, with a per-candidate
  sibling gate instead of a blanket one, is the Codex/hookless fallback; and an unfollowable
  replacement fails closed as `observation_stale_since` — hooks reclaim state authority and
  delivery hard-blocks — instead of reporting a retired conversation as live. Queue items
  strand, the auto-delivery grant lapses, the retired conversation keeps its own history row
  and messages, Branch follows the live conversation, and `agent_run_seq` keeps a rolled run
  from being repaired away by adoption. `design/features/backends.md`.
- Session-preserving daemon reload (`pty_supervisor_enabled`, default off): an out-of-process
  PTY supervisor owns ConPTYs/scrollback/reaper Job so a daemon restart leaves agents running
  and the next daemon reattaches; intent-signaled shutdown (desktop Quit/Restart, terminal
  detach + `muxd --shutdown`) keeps explicit quit reaping cleanly. Reload triggers everywhere:
  UI menu/palette (`daemon.reload`/`ui.reload` via `POST /api/daemon/restart`),
  `mux reload-daemon`, and the frozen redeploy script `packaging/redeploy_desktop.py`, backed
  by the dedicated `dist/swe-mux-supervisor` bundle so app rebuilds never collide with a
  running supervisor. Design and completion checklist: `archive/SESSION_PRESERVING_RELOAD.md`.

## Delivery order

```text
Phase 1  Evidence replay + delivery-readiness contract                      [done]
  -> Phase 2  Durable process/quota/session telemetry                       [done]
    -> Phase 3  Daily-workflow UX, prompts, config, and notifications       [done]
      -> Phase 3.5  Agent status-detection hardening + regression defense   [done]
        -> Phase 3.7  Control-plane deterministic consumers                 [done: CP step 3]
          -> Phase 4  Persistent manual prompt queue                        [done]
            -> Phase 4.5  mux MCP v0: read + discovery surface              [done: CP step 2.5, §7.5]
              -> Phase 5  Gated auto-delivery + fleet queue + bounded agent communication
                 (incl. mux.notify / mux.requestSpawn over the queue)       [done: CP §7.2]
                -> Phase 5.4  Agent conversation rollover                   [done: CP step 3.5]
                  -> Phase 5.6  mux MCP v0.5: situational-awareness reads   [done]
                    -> Phase 5.5  Project context + scan timeline           [done: CP steps 4-5]
                      -> Phase 5.8  SSH boundary handling in terminals      [correctness done; profiles deferred]
                        -> Phase 6  Agent Context + instruction coverage    [coverage done; rest deferred/culled]
                           (return-path channel 2: standing context, not pull) [CP §7]
                          -> Phase 6.5  Attention ranking + narration       [done: CP steps 6-7]
                            -> Phase 7  Windows maturity, CLI, doctor, soak [done, scope cut]
                              -> Phase 7.5  mux MCP v1 semantic memory      [done: CP step 8]
                                -> Phase 7.6  mux MCP session control       [done: CP step 9]
                                  -> Phase 7.7  Behavioral-summary consolidation + scan-timeline consumers [done: not yet redeployed]
                                    -> Phase 7.8  Git provenance re-attribution: committer + contributors [done]
                                      -> Phase 7.9  Code-structure graph: blast radius + per-session change map [done]
                                      -> Phase 7.10 Findings surface: annotation filters + doc_debt tool + Insight tab [done]
                                        -> Phase 8  Telegram control            [descoped to decision-gated]
                                        -> Phase 9  SSH/native attach           [descoped to decision-gated]
                                          -> Phase 10  WSL bridge + Linux/macOS [in progress]
                                            -> Phase 11  Public packaging and release    [open]
```

Phase 3 interface work may proceed alongside Phase 2 when it does not depend on unfinished
telemetry. No Phase 4 or 5 delivery automation bypasses Phase 1 acceptance gates. Phase 3.5
hardens the lifecycle-state and readiness evidence those gates read from; it precedes any
delivery automation because an inaccurate `working`/`idle`/`awaiting` status silently
corrupts every downstream head-of-line, arming, and auto-delivery decision. Phase 5.4 does
the same job one level down, for *identity* rather than state: `agent_run_id` is the key
every queue binding, Tier 0 fact, annotation, detector, and MCP read is scoped by, and until
5.4 that key survived an in-CLI conversation replacement it should not have survived.
Phase 5.6 precedes Phase 5.5 deliberately: the free reads come before the first continuously
costing feature, and their observed usage is the evidence for or against building the timeline
at all.
Phase 5.8 has no predecessor: it depends only on shipped shell profiles, status detection, and
runtime-cwd machinery, and it is drawn at that position solely so it does not preempt
control-plane substrate already in flight. Its status-detection item may be pulled forward at
any time, and should be if an SSH auth prompt is ever observed reading as `idle` on a session
that auto-delivery could arm against.
The chain is a default order, not a dependency proof.
Phases 3.7, 5.5, and 5.8 are explicitly parallelizable, and a phase marked descoped is not a
predecessor of anything.
Cross-cutting tests ship with each phase.

### Control-plane track interlock

The control-plane work is planned in `CONTROL_PLANE_ROADMAP.md`, whose §9 build order is
sequenced by the enablement DAG (substrate before consumers, deterministic before model).
That ordering is authoritative for control-plane content; the decimal phases below exist so
the two documents progress in one order instead of two. Each is a thin pointer plus its
cross-track dependency edges — scope, design, and acceptance detail stay in the control-plane
document and are not duplicated here.

| Control-plane step (§9) | Roadmap v2 phase | Cross-track dependency |
|---|---|---|
| 0 · Enablement framework | shipped (Implemented baseline) | — |
| 1 · Tier 0 + raw store | shipped | write/read hashes are not equality-joinable; §6.1's edge is restated as `target` + time order (CP §9 step 1) |
| 2 · Helps-today siblings | shipped (Implemented baseline) | `requestSpawn` drafts appear as targetless Fleet Queue approval rows; the old observation file is compatibility storage only |
| 2.5 · mux MCP v0 | **Phase 4.5** | needs Phase 3.5 status contract; independent of Phase 4 |
| 2.6 · mux MCP v0.5 reads | **Phase 5.6** | needs **Phase 5.4** (a read across a rollover must name the run it came from); reads shipped substrate only, and now runs **before** Phase 5.5 so its usage can justify or retire the timeline |
| 3 · Deterministic consumers | shipped (Phase 3.7) | writes drafts through the Phase 4 queue once it exists |
| 3.5 · Run boundary contract | **Phase 5.4** | not a control-plane step of its own, but a hard prerequisite for steps 4–8, which inherit their boundary from it and must never implement conversation-change detection themselves |
| 4–5 · Project context + scan timeline | **Phase 5.5** | shipped; context is one user-owned Markdown file, and the timeline is opt-in at the global, Project, and current-run levels with explicit full-session backfill and Phase 5.4's run boundary |
| 6–7 · Attention ranking + narration | **Phase 6.5** | shipped; the four channels are in-app surfaces and route to no push path, and both halves stop at Phase 5.4's run boundary |
| 8 · Cross-session + mux MCP v1 | **Phase 7.5** | shipped (semantic half): the four reads `provenance`/`verified_status`/`prior_resolutions`/`dead_ends`, each DAG-gated and run-attributed. The memory-source reads moved to Phase 5.6; cross-session interlocks/digests (CP §6.6/§6.8) stay deferred |
| 9 · Agent session control | **Phase 7.6** | shipped: `interrupt`/`end_session` over `SessionControlService`, the per-Project off/draft/granted grant, the shared graceful-end daemon op, and the fail-closed readiness gate. Agent-held spawn `granted` stays deferred pending the grant-model soak |
| §7.2 return-path write tools | shipped (Phase 5) | callers over the Phase 5 A→B queue, not a separate path |
| §13 queue-draft channel | inside **Phase 4** | `sender_kind` + typed payload land with the queue model |

Ordering rules across the two tracks:

- A control-plane phase never introduces a new delivery path. Anything that writes toward a
  session goes through the Phase 4/5 queue and its readiness contract.
- Phases 3.7 and 5.5 have no dependency on the queue phases and may proceed in parallel with
  Phase 4/5 when capacity allows; the reverse is not true, because Phase 5's observer-sourced
  messages read Phase 3.7 output.
- Phase 4.5 depends on shipped machinery plus the Phase 3.5 status contract only. It is
  deliberately pulled out of control-plane step 8 so the MCP transport, caller-identity, and
  daemon-restart decisions (`CONTROL_PLANE_ROADMAP.md` §7.3–7.4) are proven cheaply, before
  the memory tools in Phase 7.5 depend on them.
- **Every control-plane record is scoped by `agent_run_id`, and Phase 5.4 is what makes that
  scope mean "one conversation".** Tier 0 facts, annotations, detector evidence sets, scan
  records, and MCP reads all inherit their boundary from the run, so no later phase needs its
  own conversation-change detection — but no later phase may key on `session_id` alone
  either. A consumer that spans a rollover silently merges two conversations, which is the
  one failure mode the substrate is supposed to make impossible.

### Plans not sequenced here

These `development/` documents own scope this roadmap does not restate.
A phase here may depend on one, but none of them is a phase.

| Document | Status | Relationship to this roadmap |
|---|---|---|
| `CONTROL_PLANE_ROADMAP.md` | active | Interlocked through the decimal phases; authoritative for control-plane scope and design. |
| `archive/HARNESS_ABSTRACTION_AND_OMP.md` | complete and archived: 138 items, 0 open, close-out signed off | Delivered the harness registry and `omp`; its result is the "harness-neutral" line in Product direction. Current harness behaviour lives in `design/features/backends.md`; the archived plan is the record of how it was built. |
| `AGENT_ENVIRONMENT_RUNTIME_INVENTORY.md` | active plan, nothing committed | Would replace the passive Agent Environment scan with evidence-tagged runtime inventory. Deliberately unscheduled: it needs a product decision on probe cost before it earns a phase. |
| `CROSS_PLATFORM_FINDINGS.md` | research | Feeds Phases 10 and 11; holds the platform-interface inventory and verification matrix those phases would otherwise duplicate. |
| `NEW_USER_RELEASE_READINESS.md` | active plan | Feeds Phases 7 and 11; holds the fresh-machine onboarding detail those phases depend on: the remote-connection connect flow (connection state, phone DNS, QR), Windows Defender Firewall repair, agent instrumentation toggles, the onboarding-prerequisites surface, and first-use download costs. Records the audit finding that the shippable code is free of hardcoded identity, absolute personal paths, and a hardcoded daemon host, so agnosticism here is a defaults-and-onboarding concern rather than an un-hardcoding one. |
| `PLUGIN_SYSTEM_FINDINGS.md` | research | Decision-gated. Records what a plugin system would add over the shipped meta-hooks/automation/project-actions substrate, and the constraints any design must accept. |
| `PERFORMANCE_RUNBOOK.md`, `STATUS_INCIDENT_RUNBOOK.md`, `TERMINAL_INPUT_INCIDENT_RUNBOOK.md` | operational | Investigation procedures for shipped subsystems, not planned work. |
| `CONTINUITY_TOUCH_KEYBOARD_ASK.md` | open ask against a vendored dependency | Blocked on the note editor upstream, not on a phase. |

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
- [x] Suppress the ready/complete alerts while running work (subagents, background tasks)
  or a startup settle means the agent is not actually waiting on the human, and hold the
  ready alert 120 s so a turn the agent walks back never reaches a lock screen.
- [x] Deliver alerts off-device: web push with server-persisted per-device preferences,
  device presence and a leading-device rule so one alert reaches the device the user is
  actually at, and a per-session mute.
  `design/features/notifications.md`, `design/features/device-presence.md`.
- [x] Re-measure the residual `waiting` volume, then decide it.
  A 2026-08-12 replay over all retained events from 2026-08-08 22:51 through 2026-08-12 21:19 covered 94.5 hours and 107 sessions.
  The current classifier admitted 509 `waiting` candidates; 254 resumed or received human input inside the 120-second settle, leaving 255 residual candidates, or 27.0 per 10 hours.
  That is 56% below the prior 62-per-10-hour measurement before the suppression and settle fixes.
  Keep the 120-second settle and current default policy without adding recency scoping or rate limiting.
  This replay cannot reconstruct historical subscriptions, preferences, device presence, or push-service outcomes, so it is an upper bound on delivered alerts rather than a delivery count.
  The decision ledger below now measures actual future delivery.
  Read `archive/NOTIFICATION_SCOPING_PROPOSAL.md` before reopening recency or rate limits.
- [x] Persist one row per notification decision (session, category, plan verdict, outcome).
  The append-only, content-free `notification_decisions` ledger records classifier suppressions, settle holds/cancellations, per-profile route verdicts, and final delivery outcomes under one candidate id.
  `GET /api/diagnostics/notifications?days=N` reports category breakdowns and the actual `waiting` delivery rate over a retained window.

### Voice boundary

- [x] Keep microphone capture app-owned in a secure browser context and transcription
  daemon-owned through offline Windows Speech Recognition or optional local faster-whisper.
- [x] Instrument capability/availability only; never retain audio or transcript content for
  analytics without explicit consent.
- [x] Bound utterance size and duration, delete temporary WAV input after every outcome,
  and commit buffered speech only through explicit wake commands.
- [x] Take voice from per-pane dictation to hands-free operation: STT latency, wake-word
  selection, a global talk surface, voice navigation over the command registry, and a
  model-free spoken fleet status. Completed plan and decisions:
  `archive/VOICE_INTERACTION_ROADMAP.md`.

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
  Superseded in place: the note surface is now per-Project note collections plus the global
  Scratchpad (`design/features/project-resources.md`), and `history.note_id` is what remains of
  the per-terminal identity.

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

- [x] Write down, per `SessionState` value, the exact positive evidence predicate that may
  set it and which sources (`pty`/`transcript`/`hook`/`watchdog`/`notification`) are allowed
  to, mirroring the `delivery_state` discipline. Ambiguous or absent evidence resolves to the
  conservative prior, never a guessed active state.
- [x] Define and document the total mapping from `SessionState` (plus the `awaiting`
  sub-reason: approval / Q&A / elicitation) to the single user-visible status shown per
  session, and its relationship to `delivery_state` and attention. These three axes stay
  separate; the UI renders one coherent status without collapsing them incorrectly.
- [x] Make the `state-log` ring buffer a complete, typed transition ledger: every transition
  carries prior state, next state, source, the evidence that justified it, whether it was
  inferred, and monotonic timing. No transition may occur without a ledger entry.
- [x] Classify each transition as `proven` (hook/transcript/notification evidence) or
  `inferred` (watchdog/PTY backstop). Inferred transitions are recovery events, counted and
  bounded, never the primary path for a healthy session.

### Golden corpus extension to user-visible status

- [x] Extend the detection replay corpus to assert `SessionState` (and `awaiting` sub-reason)
  at every checkpoint, not only `delivery_state`, `events`, and `parser`. The user-visible
  status becomes a golden-stream output with the same no-drift protection.
- [x] Add deterministic fixtures for every documented failure mode, each with a root-cause
  note and the guard that closes it: hook/transcript race reopening `working` (late
  `PreToolUse`/`PostToolUse` landing after the transcript `end_turn`), the
  `closed_by_transcript` latch refusing a hook-sourced re-begin, ESC-pause-without-marker,
  observer stuck on a sibling transcript (cross-attribution), crash mid-turn, compaction,
  resume, promotion/demotion, `idle_prompt` versus `permission_prompt` versus
  `elicitation_dialog`, subagent-only stop, rate-limit abort, and daemon restart mid-turn.
- [x] Pin the watchdog recovery paths as golden behavior, not incidental timing: ENDED-stuck
  force-idle at `STATE_WATCHDOG_ENDED_STUCK_SECONDS`, the PTY backstop force-idle at
  `STATE_WATCHDOG_PTY_STUCK_SECONDS` for both `unknown` and `open` tails, and the
  `_pty_appears_idle` true/false branches (a genuine long tool with "esc to interrupt" up
  must never be cut short).
- [x] Make status-affecting parser, mapping, or watchdog changes fail CI when golden status
  streams change without a reviewed fixture update, mirroring the Phase 1 safe-to-inject gate.

### Edge-case inventory and closure

- [x] Maintain an explicit, tracked inventory of every known status edge case with: a
  reproducing fixture, the guard that closes it, and a one-line root cause. Closing an edge
  case means both exist; removing either fails CI.
- [x] Guarantee no session can remain in a non-terminal active state indefinitely: for every
  `working`/`awaiting` path there is a proven or bounded-inferred exit, and the watchdog
  bounds are covered by fixtures at their thresholds.
- [x] Prove the cross-attribution gate: an observer bound to the wrong sibling transcript
  never sets this session's status from another session's evidence, and the PTY backstop (own
  session's ground truth) still recovers it.
- [x] Prove notification semantics: `idle_prompt` maps to `idle` and never clobbers a real
  pending approval; `permission_prompt`/`elicitation_dialog` map to `awaiting` with the
  correct sub-reason.

### Regression detection over time

- [x] Add a sanitized capture → golden-fixture pipeline: a real stuck or misclassified
  session's `state-log` (and the minimal evidence stream that produced it) can be captured,
  scrubbed of terminal bytes and prompt bodies, and promoted into the versioned corpus so it
  becomes a permanent regression test.
- [x] Publish status-health metrics through test and runtime diagnostics: inferred-recovery
  count by source (`watchdog-ended`, `watchdog-pty`), reopen-after-authoritative count,
  unknown/open-tail durations, and time-to-terminal after a turn ends. A rise in inferred
  recoveries is a tracked regression signal, not silent drift.
- [x] Extend the live-agent conformance harness to diff captured `state-log` transitions
  against expected proven-transition shapes for scripted real-CLI runs, flagging any run that
  reached a terminal status only via inference.
- [x] Bound and alarm on the health metrics: the acceptable inferred-recovery rate for a
  healthy fleet is defined, exposed with an `alarm` flag at
  `GET /api/diagnostics/status-health`, and unit-covered
  (`test_fleet_status_health_alarm_bounds`). The *soak run* that asserts against it lands
  with the Phase 7 quality matrix, which is where the soak matrix itself is built.

### UI reflection correctness

- [x] Add a frontend contract test that the `SessionState` → sidebar/pane indicator mapping is
  total and unambiguous: no state renders blank or as a permanent blinking `working`, and a
  terminal transition in the `state-log` always clears the working indicator.
- [x] Assert the `awaiting` indicator distinguishes approval, Q&A, and elicitation with the
  correct affordance, and that `idle_prompt`-driven idle never renders as awaiting approval.
- [x] Verify the mobile unified-tab projection shows the same status as the desktop pane for
  the same session, driven from the same evidence, with no independent heuristic.

### Phase 3.5 exit criteria

- [x] Every `SessionState` transition (and `awaiting` sub-reason) is reproducible from
  fixtures across hook/transcript/PTY races and is asserted in the golden corpus, not only
  `delivery_state`.
- [x] Every documented stuck-`working`, reopened-turn, misclassified-`awaiting`, and
  mis-attribution edge case has a named fixture and a guard; removing either fails CI.
- [x] Inferred/watchdog recoveries are measured, bounded, and alarmed; a healthy session
  reaches terminal status by proven evidence, and a rise in inferred recoveries surfaces as a
  regression.
- [x] Desktop and mobile UI reflect status through a total, tested mapping with no permanent
  `working` on a completed turn and correct `awaiting` sub-reasons.

## Phase 3.7 — Control-plane deterministic consumers

Control-plane build-order step 3, plus the step 1 gap it is blocked on
(`CONTROL_PLANE_ROADMAP.md` §9). This is the first phase that turns captured Tier 0 facts
into user-visible judgements, and it is deliberately model-free: every detector here is a
query over deterministic facts, writing to `annotations`. Design detail lives in the
control-plane document; this phase exists to fix its position in the delivery order.

Substrate status going in (2026-07-27 hardening pass): the step 1 defects that would have
been discovered mid-phase are closed — silent 4 KB fact drops, NULL run/project ownership,
`tool_result` fingerprint collapse, missing structured test facts, and git commit/tree plus
read-side hashes. See `CONTROL_PLANE_ROADMAP.md` §9 step 1 for exactly what shipped.

Behavior and rules are documented in `design/features/deterministic-consumers.md`.

- [x] Close the one remaining step 1 gap. **Decision: restate §6.1's edge as `target` + time
  order**, carrying the writer's content hash as what was written, with an `ambiguous` marker
  when another write to the same target intervenes. The rejected alternative — a per-backend
  normalizer reconstructing file bytes from the CLI's rendering — depends on a lossy format
  that drifts per version and is impossible for a truncated read.
- [x] Annotation anchor + evidence schema: nullable `agent_run_id` beside a `project_id`
  anchor, `evidence_json` for the fact *set*, `dedupe_key` for idempotence, and the additive
  migration path `AutomationStore` previously lacked.
- [x] Loop/stall deterministic half (CP §6.4) — fingerprint repeat ≥3 behind the no-progress
  gate.
- [x] Declared-vs-verified (CP §6.3) — Tier 0 test facts plus completion-claim detection,
  reported as three separate facts.
- [x] Doc-debt ledger (CP §6.5) — ownership inverted from each doc's literal "Key files"
  section, because the routing table is keyed by change *type* and cannot be matched to a
  path by machine.
- [x] Provenance graph (CP §6.1).
- [x] Ship the enablement-DAG toggle surface (CP §9 UI work), including an `implemented` flag
  so a reserved id cannot be switched on into a no-op.

### Phase 3.7 exit criteria

- [x] Each detector is per-project opt-in through the existing DAG, inert when disabled, and
  spends no model tokens.
- [x] Every annotation these produce is traceable to the exact Tier 0 fact(s) that caused it:
      `evidence_json` carries the fact set, not one `source_event_seq`.
- [x] No detector writes toward a session. Output is annotations only until the Phase 4 queue
  gives it a `queue_draft` path (`CONTROL_PLANE_ROADMAP.md` §13).

## Phase 4 — Persistent manual prompt queue

Phase 4 introduces durable user intent but keeps delivery manual. The storage model is a
mailbox-shaped queue so later senders can be added without a migration into an orchestration
framework.

### Queue and message model

- [x] Add persistent messages keyed to stable target agent-run/history identity with
  Project/session provenance, sender kind/id, ordered position, body, revision, timestamps,
  delivery constraints, and audit metadata. (`prompt_queue.py` `queue_messages`; run
  identity binds at enqueue or to the target's first run, and is never re-bound.)
- [x] Make the sender/provenance model rich enough to carry a control-plane `queue_draft`
  on day one, not just a local user. Persist originating rule/observer id, the source
  Tier 0 fact(s)/fingerprint, and the annotation or fact snapshot that produced the draft,
  so drafts remain auditable back to their cause without a later schema migration. The
  queue-draft channel (`CONTROL_PLANE_ROADMAP.md` §13) is the first non-human sender and
  writes only inert drafts a human must arm and send. (`sender_kind`/`sender_id`/
  `origin_json`/`payload_json`/`constraints_json`; the HTTP surface pins
  `sender_kind="user"`, non-human senders can only create inert drafts.)
- [x] Use explicit states: `draft`, `armed`, `delivering`, `sent`, `blocked`, `failed`,
  `cancelled`, and `stranded`. State transitions are transactional and idempotent.
- [x] Enforce strict head-of-line delivery: later messages may be armed, but an earlier
  unsent draft/armed/blocked item prevents downstream delivery until sent, cancelled, or
  explicitly skipped.
- [x] Let users edit drafts and armed messages until delivery begins; each edit increments
  revision and records time. Sent/delivering items are immutable.
- [x] Persist no hidden rendered prompt variant. The exact user-visible body being delivered
  is the audited body. (Delivery wraps the body in bracketed paste at write time; nothing
  rendered is persisted.)

### Session-attached UI

- [x] Add a Queue workspace tab attached to the target session/agent run, plus a compact
  queue count/status affordance in the session pane. It uses the same mixed-view pane system.
  (`queue` pane leaf + the pane header's `queue[:N]` chip.)
- [x] Support add, insert after, reorder, arm/unarm, edit, cancel/skip, copy, and manual
  “send next now.” Sent messages remain visible, crossed out, and uneditable. (Insert-after
  is an API capability; the tab exposes it as add + reorder.)
- [x] Make lock/arm semantics explicit: any downstream item may be armed in advance, while
  strict ordering still blocks it behind earlier unsent items.
- [x] Preserve queues when views close. If the target session/run ends, mark pending items
  stranded and offer explicit copy/retarget/cancel; never silently target a replacement or
  resumed run.
- [x] Add history access for completed/stranded queues and bounded retention/export that
  excludes secrets by user choice. (`prompt_queue_retention_days`; `GET /api/queue/export`
  with opt-out secret redaction.)

### Manual delivery

- [x] Route “send next now” through one typed daemon operation with target/revision/input-
  owner checks, clear blocked reasons, and idempotency key. (`POST /api/queue/send-next`;
  delivery writes go through `_record_operator_input(source="queue")`.)
- [x] Require explicit confirmation when readiness is blocked or unknown; do not offer a
  generic force-send that bypasses approval/Q&A/target identity protection. (Per-send
  `confirm`; `approval_required`/`awaiting_user_input`/awaiting sub-reasons and target
  identity/liveness are never overridable.)
- [x] Record delivery attempt/result without duplicating prompt text into general event or
  automation logs. (`queue_deliveries` audit rows; `queue_updated`/`queue_delivery` events
  carry ids and counts only.)

### Send-to-agent senders and coverage

Note/Markdown "send to agent" already ships as a *direct* manual send (see
`design/features/project-resources.md`): the selection either seeds a new session through the
agent CLI's argv or is written to a live session over `POST /sessions/{id}/input`. It is the
first non-trivial producer of agent-bound text outside a terminal, so it is the natural first
caller of the queue rather than a second delivery path to maintain.

- [x] Make the dialog a queue sender: "add to queue" (draft/armed) alongside today's "send now",
  so a message aimed at a busy session waits in one audited place instead of racing the
  composer. Today's direct write becomes the queue's own "send next now" path. (The
  no-submit "fill the composer" variant deliberately stays a plain input write — it never
  submits, so it is not a delivery.)
- [x] Move the readiness decision into the queue. The dialog currently *reports* blocked/unknown
  readiness and lets the user proceed; once the queue owns delivery, that becomes the queue's
  confirmation rule so there is exactly one place where a not-safe target is overridden.
  (The dialog banner is advisory; refusal and the confirm override live in `send_next`.)
- [x] Extend the surface to the views that own no Continuity selection: plain-text file editors
  (whole-document send) and the Files tree context menu, which already fetches file contents for
  its copy action. Keep the shell exclusion — an agent composer holds a paste inert, a shell runs
  it. (The daemon also enforces it: `not_agent_target`.)
- [x] Replace the 20,000-character ceiling on a *new* session's seed prompt. argv is a Windows
  command line, so a long body is currently refused with a pointer at the live-session route;
  the durable fix is to stage the body (temp file or session note) and seed a prompt that reads
  it, which also removes the quoting-inflation risk. (`seed_text` on the spawn request:
  short bodies inline into argv, longer ones stage into `.swe-mux/seeds/` with a reader
  prompt.)
- [x] Finish or delete the reverse direction. `TerminalPane` handles a `captureSelection` action
  and emits `mux:terminal-selection` with a `targetKey`, but nothing dispatches it and nothing
  listens: terminal-selection-into-notes is a half-wired stub, and shipping the note→terminal
  direction without resolving it leaves two incomplete halves of one idea. (Deleted — dead in
  both directions.)
Dropped from this phase: making the note surface's two send buttons configurable.
No demand appeared, the command rail meanwhile gained per-device layouts and rows of its own,
and unifying the rail's server-persisted `RailItem` model with Continuity's localStorage rail
is a design task rather than a settings row.
Reopen it as its own item if the need returns; it is not queue work.

### Phase 4 exit criteria

- [x] Queue order/state survives daemon and browser restart without duplicate delivery.
  (Live-verified 2026-07-28 against the frozen app: order and states intact across
  `POST /api/daemon/restart`, and a replayed idempotency key returned the recorded
  outcome without a second PTY write. Browser state is nothing but a caller of the
  daemon's queue reads.)
- [x] Closing a session strands pending work instead of losing or retargeting it.
  (Live-verified: killing the target stranded the armed item with its reason retained;
  retarget exists only as an explicit act on stranded items.)
- [x] Every actual delivery is initiated by an explicit user action and is auditable.
  (One typed operation; refusals and sends alike leave `queue_deliveries` rows carrying
  readiness evidence and confirmation flags, never prompt text. Live verification also
  surfaced and fixed two defects: a refused attempt no longer consumes its idempotency
  key, and the Claude adapter no longer lets variadic `--mcp-config` swallow a trailing
  argv seed prompt — the Phase 4.5 regression that killed every argv-seeded spawn.)

## Phase 4.5 — mux MCP v0: read and discovery surface

Control-plane build-order step 2.5, pulled forward out of step 8
(`CONTROL_PLANE_ROADMAP.md` §7.5). The return path is how accumulated control-plane insight
gets back into a coding agent, and its transport is an MCP server both Claude Code and Codex
can call. v0 is **read-only**: it exposes machinery that already exists, adds no authority,
and answers the "agents can see prior and concurrent sessions" request directly. It also
proves the transport, identity, and restart decisions cheaply, before the Phase 7.5 memory
tools depend on them.

Depends on the Phase 3.5 status contract (a session status an agent reads must be the same
one the UI reads) and on shipped history/transcript search. It does not depend on Phase 4.

Follow-on read tools that were not part of proving the transport are scheduled in **Phase
5.6**, not here; this phase stays a completion record.

### Server placement and transport

- [x] Host the MCP endpoint **in the daemon, never in the PTY supervisor** (`POST /mcp`,
  protocol + tools in `mcp.py`, thin handler in `server.py`; supervisor untouched).
- [x] Streamable-HTTP on the existing daemon port. Codex support verified 2026-07-28:
  installed codex-cli 0.145.0 takes `mcp_servers.<name>.url` +
  `bearer_token_env_var` natively, so no stdio shim exists — one implementation.
- [x] Auto-register per backend: Claude via a static `--mcp-config` file
  (`Authorization: Bearer ${MUX_MCP_TOKEN}` env expansion), Codex via `-c` argv overrides;
  both also injected by the agent shims for user-typed `claude`/`codex`, gated on the
  session actually holding a token.
- [x] Restart tolerance: in-flight calls die with the TCP connection (retryable transport
  error to every MCP client); no partial or fabricated result (a parse that misses its
  budget reports itself transient); token survives restart via supervisor-meta recovery;
  unknown token gets a typed 401 that forbids retry-forever. Port stable, config never
  rewritten.

### Caller identity

- [x] Per-session token minted at spawn (`MUX_MCP_TOKEN` + `MUX_MCP_URL` in the session
  env). Caller derived from the token; no tool has a sender parameter.
- [x] Persisted the same way the hook secret already is: mirrored into supervisor session
  meta and recovered at adoption — no separate token table to invalidate, and a token is
  never regenerated (a fresh one would authenticate nobody). Empty token (pre-feature
  session) never authenticates.
- [x] Token scope defaults to the session's Project (`project_id`, falling back to the git
  `project_scope_id` for ungrouped sessions). The separate explicit grant shipped 2026-08-14
  as the per-call `project` argument on every tool (`"fleet"`, or a Project name or id;
  `project_scope.py`). The default is unchanged and nothing widens implicitly.
- [x] **Same-host caller boundary — DECIDED 2026-07-28: option (b), same-host agents are
  fully trusted in v0.** The per-session MCP token is an *identity and read-scoping*
  mechanism (attribution, Project scope), not an authorization boundary. Basis for the
  decision: v0 is read-only end to end, so the token's real job is scoping reads and
  attributing calls; the un-tokened mutating surface predates MCP and is unchanged by it.
  Consequences, recorded so they are not rediscovered: (1) Phase 5's budget/allowlist/cycle
  machinery bounds *well-behaved* callers only — a prompt-injected same-host agent can
  bypass it via the open localhost API, and Phase 5's design must say so; (2) **this
  decision MUST be revisited before Phase 5 arms any auto-delivery or agent-to-agent
  write path** — the enforcement option remains extending the token check to mutating
  `/api` routes with a daemon-local browser bearer minted at page load (sessions never
  receive it). The evidence behind the concern (verified 2026-07-27): every mutating HTTP
  route — spawn, kill, `POST /sessions/{id}/input`, settings — is callable from localhost
  behind the Host/Origin middleware alone, and every spawned session holds the daemon's
  ingress URL in its environment. The existing trust model (Tailscale device admission)
  addresses the network-peer boundary and says nothing about same-host children.
- [x] Human-input evidence hole closed 2026-07-28: `POST /sessions/{id}/input` and
  broadcast fan-out now share one accounting helper with voice
  (`_record_operator_input`) — revision/timestamp advance, `terminal_input` emission
  (`source="http"`/`"broadcast"`), ended-session guard (409 on the route, skip per
  broadcast target). Automation `write_pty`, branch writes, and interrupts deliberately
  stay uncounted — they are not operator input. Details:
  `design/features/delivery-readiness.md`.

### v0 tool surface (read-only)

- [x] Four read tools: `list_sessions` (live + optionally ended, caller marked),
  `get_session` (live or archived, same status the UI reads), `read_transcript` (bounded
  tail), `search_history` (FTS over the archive, `agent_visible` quarantine kept).
- [x] Return nothing rather than a weak match: scope misses and true misses answer
  identically ("no such session in your Project"); search returns only FTS hits.
- [x] Redaction: session records leave through an explicit allowlist (`session_summary` —
  `spawn_env` can never leak); transcript messages and search excerpts that trip the
  clipboard credential gate (`looks_like_secret`) are replaced with a redaction marker.
- [x] Bounds and rate limit: 512 KiB / 200-message transcript cap, 50-entry search cap,
  256 KiB request bodies, 120 calls/min per session with swept sliding windows.

### Phase 4.5 exit criteria

- [x] A live agent session can enumerate sibling sessions and search history through MCP with
  no user setup, and every result is attributable to the calling session's token.
  Live-verified 2026-07-28: a freshly spawned session called `initialize`,
  `list_sessions` (siblings + ended, caller marked `you`), and `search_history` (real FTS
  excerpts) using only its injected env; a wrong token got the typed 401.
- [x] A daemon reload mid-call surfaces a retryable error and leaves no partial state; after
  the reload the same token still works. Live-verified 2026-07-28: after
  `POST /api/daemon/restart`, the adopted session's original token authenticated again
  (supervisor-meta recovery) and all tools answered identically.
- [x] The surface is read-only end to end: no tool in v0 can enqueue, deliver, spawn, or write
  to a PTY. Pinned by `tests/test_mcp.py` (the tool set is a closed allowlist of four read
  tools).

## Phase 5 — Gated auto-delivery, the fleet queue, and bounded agent communication

Phase 5 authorizes narrowly scoped actuation after Phase 1 shadow evidence and Phase 4
manual-delivery reliability pass. It does not authorize model-selected actions,
auto-approval, arbitrary PTY writes, or uncontrolled relay chains.

**Standing precondition (from the Phase 4.5 boundary decision, 2026-07-28): before this
phase arms any auto-delivery or agent-to-agent write path, revisit the same-host caller
boundary.** — **DONE 2026-07-29: re-examined and re-affirmed, with the limitation written
into the design (`design/features/agent-messaging.md` § the same-host boundary).** The
enforcement option (token check on mutating routes + a daemon-local browser bearer) cannot
deliver the property it appears to: an agent session runs as the same user on the same
host, so it can request whatever credential the browser is given. A real boundary here needs
OS-level isolation (a per-user ACL'd pipe/socket the browser holds and spawned sessions do
not), which is a separate product decision, recorded in the design as the enforceable path
if it is ever needed. What was done instead is the part that *is* enforceable: agent-reachable
authority stays strictly narrower than the browser's — no tool delivers, spawns, or writes
to a PTY; every agent write is token-attributed, bounded, expiring, revocable, and visible in
the fleet queue; and the receiving session's own policy decides whether an agent message is even
armed. Stated plainly in the design: the bounds below constrain well-behaved callers, and a
prompt-injected agent can still reach `POST /api/sessions/{id}/input` exactly as it could
before Phase 5.

### User-authored same-session auto-delivery

- [x] Define quantitative promotion criteria for Phase 1 shadow readiness, including zero
  known false-safe deliveries across approval, Q&A, rate-limit, subagent, active-input, and
  run-replacement fixtures plus an operator-reviewed proving period.
  `auto_delivery.promotion_status`: zero false-safe (machine-checked over the six fixture
  classes in `tests/test_delivery_readiness_promotion.py`, directly against the tracker and
  over the golden replay corpus, plus the operator's `report_unsafe`, which resets the clock
  and pauses the feature), ≥50 automatic sends, ≥14 proving days. Counters are persisted and
  reported at `GET /api/queue/auto`.
- [x] Add opt-in auto-delivery for armed, user-authored messages to the same live agent run
  only when `delivery_state=safe` remains stable for a bounded debounce window.
  `AutoDeliveryController`: master switch (off by default) plus a per-session grant bound to
  `agent_run_id`; the window is `auto_delivery_stable_seconds` (default 8) held continuously
  for the same message revision, and any flap resets it.
- [x] Re-check target identity, message revision, head-of-line state, input ownership,
  composer state, terminal mode, and adapter capability atomically immediately before send.
  Unchanged from Phase 4 — the controller calls the same `send_next`, which claims
  state/revision/head-of-line in one transaction and then re-checks liveness, run identity,
  and full readiness before the write.
- [x] On uncertainty, remain blocked and surface the reason. Never retry blindly after
  partial/unknown PTY delivery; require user reconciliation. The controller cannot pass
  `confirm` at all (`confirm_requires_user`), and a failed delivery disables the session's
  opt-in with "verify the terminal before re-enabling"; a refusal backs the session off
  instead of spinning the audit log.
- [x] Provide pause-all, per-session enablement, expiry, maximum consecutive sends, quiet
  hours, audit view, and an emergency disable independent of provider availability. The
  pause is a persisted SQLite flag (not config, not a provider), the grant expires
  (default 60 min) and caps consecutive sends (default 3, reset by any manual send), quiet
  hours pause automatic sends only, and `queue_deliveries.initiator` records who pressed
  send. The emergency disable and the unsafe report are on the Queue tab's `auto:`
  disclosure and on `autodelivery.pause` (mobile included); the fleet-queue overlay reports
  their state and owns neither.
- [x] Add time-based delivery — "send after N minutes" and "send at a time" — as a *delivery
  constraint on a queue item*, never as a private timer in a sender's UI.
  `constraints_json` (`not_before`/`expires_at`, 30-day horizon, `delay_seconds` resolved at
  write time). Both paths honour it: an early manual send is refused `delivery_not_due` and
  keeps its state, "Send now" is the explicit human override, and an expired item is
  cancelled rather than delivered late. Recurring/schedule-driven sends remain out of scope.

### Human/device fleet queue

- [x] Expose the generalized message model with explicit sender provenance for local user,
  authenticated remote user/device, deterministic rule, and session/agent sources.
  `sender_kind` is `user | remote_user | agent | rule | queue_draft` and is **derived**
  (transport for the human kinds, MCP token for `agent`) — no API accepts a sender argument.
- [x] Deliver human/device messages through the same queue and readiness contract; remote
  origin never weakens target selection, confirmation, expiry, or input-owner checks. Remote
  origin is recorded and changes nothing downstream.
- [x] Add application-wide authorship views, delivery status, sender/target labels, retry-safe correlation, and
  revocation. Avoid creating a second transcript or conversation archive.
  `GET /api/queue/mailbox` + `FleetQueue.tsx` project the existing `queue_messages` rows;
  `correlation_id` is partial-unique per sender (a retry returns the original message);
  revocation is `cancel_kind: revoked`. No new store.

### Agent-to-agent communication

- [x] Start with explicit user-authored or user-approved “send output from A to B.” Session A
  does not gain unrestricted knowledge of or authority over session B. Receiver-side policy
  (`accept_agent_messages`, part of the per-run grant) decides how much an arriving message is
  worth.
  **Default changed 2026-08-09**: a live agent conversation now accepts agent-authored messages
  `armed` rather than as an inert draft, so the shipped default is no longer "a human approves
  each one".
  What still holds is the envelope: an armed item waits for head-of-line order and the same
  delivery-readiness gate as any other queue item, so it never interrupts an active turn and
  never bypasses an approval or question prompt, and the receiving operator can turn acceptance
  off for the run.
- [x] Preserve source session/run, exact selected output span or annotation, requesting
  user/rule, target, transformations, and delivery result as provenance. `origin_json`
  carries the relay path, source session/run/backend, and the stated reason; the delivery
  audit carries the rest.
- [x] Add target allowlists, maximum message/body size, expiry, rate limits, max chain depth,
  cycle detection, per-origin budgets, and loop kill switches. All in the daemon operation
  (`agent_messaging.py`), so the browser and any later client inherit them: Project scope +
  live-agent + not-self, 4 000 chars, 24 h expiry, 20 messages/hour per origin, 5 undelivered
  per target, 3 hops, cycle detection over the recorded path, and `agent_messaging_enabled`.
- [x] Require receiver-side readiness and queue policy. A message from another agent waits;
  it never interrupts an active turn or bypasses approvals/Q&A. It is an ordinary queue item:
  head-of-line order, the same readiness gate, the same non-overridable protections.
- [x] Permit deterministic rules to enqueue only fixed/user-reviewed templates or bounded
  annotation output. Do not automatically lift arbitrary model output into another prompt.
  Unchanged from Phase 4: `rule`/`queue_draft` senders create inert drafts only, and arming
  them is a human act.
- [x] Keep autonomous model-authored routing, worker spawning, approval decisions, command
  execution, and arbitrary network destinations outside this phase. `mux.requestSpawn` is a
  draft producer; nothing here selects a target on the model's behalf beyond the tool call
  the human can read in the mailbox.

### Agent-facing surface: mux MCP write tools

Phase 5's A→B path is what an agent reaches through the Phase 4.5 MCP transport. The tools
are thin callers over the typed queue operation defined above; they are not a second
implementation of it (`CONTROL_PLANE_ROADMAP.md` §7.1–7.2).

- [x] Add `mux.notify(target, body)` as a caller over the same typed A→B operation the
  browser and CLI use. It inherits target allowlists, size/expiry/rate limits, chain depth,
  cycle detection, per-origin budgets, receiver-side readiness, and the kill switch by
  construction, because those live in the daemon operation and not in the tool. The tool
  body is four lines: resolve args, call `AgentMessagingService.notify`.
- [x] Derive the sender from the calling session's Phase 4.5 token, never from a tool
  argument, so per-origin budgets and cycle detection are enforceable. Pinned by
  `tests/test_agent_messaging.py::test_the_mcp_tools_derive_the_sender_from_the_token`
  (a forged `from_session` argument is simply ignored).
- [x] Add `mux.requestSpawn(...)` as a **draft producer only**: it writes an inert typed request
  that now appears as a Fleet Queue approval row with the proposed target Project, prompt,
  and calling-session provenance, and starts nothing. Approving the draft is an explicit human
  action (available on mobile) and is what actually spawns the session - `POST …/observations/{id}/decide`,
  which seeds the new session through the ordinary spawn path and can be decided once.
- [x] Keep the queue path and the MCP path on one audit trail. A message that arrived through
  MCP is distinguishable by sender provenance but is otherwise an ordinary queue item.

Scope boundary (reconciling the `.swe-mux/notes/project.md` agent-to-agent request): the
desire for "agent A finishes a task and notifies a specific agent B, and sometimes spawns a
new session for B" splits across the trust line. **In scope for Phase 5:** user-authored,
user-approved, or `mux.notify` A→B messages into an existing target run through the same
queue/readiness contract, carrying full provenance. **Not in scope — decision-gated:** an
agent autonomously selecting a target and *spawning* a new session to receive the message;
that is worker spawning behind the actuation gate (`CONTROL_PLANE_ROADMAP.md` §16) and
requires a separate product decision (see "Decision-gated capabilities"). Phase 5 delivers
bounded messaging between sessions that already exist and a drafted request to create one; it
does not let one agent create another.

### Phase 5 exit criteria

- [x] User-authored same-session auto-delivery is opt-in, conservative, bounded, and shows
  no unsafe delivery in the proving corpus/period. Live-verified 2026-07-29 on the frozen
  app with a browser-equivalent terminal observer attached to a real Claude session: an
  armed user message auto-delivered 16 s after readiness went `safe`
  (`initiator=auto, confirmed=false, delivery_state=safe`), the third consecutive automatic
  send disabled the opt-in ("reached 3 consecutive automatic sends"), the emergency pause
  held an armed message undelivered, and both the pause and the opt-in survived
  `POST /api/daemon/restart` with no duplicate delivery. Schedule (`delivery_not_due`, item
  stays armed) and expiry (`cancelled`/`expired`) both verified. Corpus side: zero false-safe
  across the six fixture classes (`tests/test_delivery_readiness_promotion.py`); the
  volume/duration proving period is instrumented and running, not yet met — by design, since
  it gates *widening*, not the bounded opt-in.
- [x] Human/device and approved A→B messages retain provenance, cannot loop indefinitely,
  and never silently retarget ended runs. Live: an `mux.notify` from one live agent landed in
  the sibling's queue carrying `sender_kind=agent`, sender label, relay path, `chain_depth`,
  and a 24 h expiry. Bounds (size, per-origin budget, target backlog, depth, cycle, self,
  and the requested Project scope) are refused by the daemon operation and pinned in
  `tests/test_agent_messaging.py`. Scope became a per-call `project` argument on 2026-08-14
  rather than a fixed own-Project prohibition; the default is unchanged. Ended/replaced runs still strand — unchanged Phase 4
  behaviour, and the auto controller additionally disables its opt-in on a replaced run.
- [x] Disabling Phase 5 leaves the Phase 4 manual queue and ordinary agent sessions usable.
  Live-verified with the master switch off and nothing paused: stage → arm → view → cancel
  all worked, and the summary route answered normally.
- [x] An MCP-originated message is indistinguishable in safety terms from a browser-originated
  one: same readiness gate, same bounds, same audit trail, no separate delivery path.
  Live: the agent-authored message was delivered by the *same* `send_next` (audit row
  `('user','sent',confirmed)` for the manual send, `('auto','sent',false,'safe')` for the
  automatic one), in head-of-line order, with the body appearing only in `queue_messages`.
- [x] `mux.requestSpawn` has produced no session without an explicit human approval, and
  disabling the tool leaves the rest of Phase 5 intact. Live: the tool wrote a `pending`
  Fleet Queue approval row and started nothing; `POST …/observations/{id}/decide` with
  `approve` created the session with the drafted prompt seeded and marked the request
  `approved`; a second decision was refused `409 already_decided`. `request_spawn_enabled=false`
  yields a typed refusal and touches nothing else (`tests/test_agent_messaging.py`).

Also observed live and worth keeping: the agent CLI's own permission prompt gates the first
use of each mux write tool, so a notify or spawn draft costs the operator one deliberate
approval inside the session before it ever reaches the daemon.

## Phase 5.4 — Agent conversation rollover (the run boundary contract)

An in-CLI `/clear` (Claude) or `/new` (Codex) replaces the conversation underneath a live
PTY: the CLI mints a new native session id and starts a new transcript file, while the mux
session id, `agent_run_id`, PTY, hook secret, and MCP token all stay the same. Verified
against the local transcript store — every `/clear` command record sits at the head of a
*new* `.jsonl`, never inside the file it was typed in.

swe-mux currently treats this as a file-following problem rather than an identity problem,
and gets it wrong in two different ways depending on the situation:

- **With a sibling agent in the same cwd** (the normal case in a real project) the
  transcript-switch watcher's sibling gate refuses to move, so the observer tails a file
  that will never change again. `parser_status` stays `ready`, which makes
  `_transcript_authoritative()` true, which makes the hook fallback drop
  `UserPromptSubmit`/`PreToolUse`/`PostToolUse` as redundant. Status, tokens, context, and
  model then stop updating from *both* sources.
- **Without a sibling** the watcher does move, and rekeys `native_session_id` in place under
  the *same* `agent_run_id`. The history row's `native_id` and `transcript_path` are
  overwritten, `replace_history_messages` deletes the pre-clear messages under that
  `history_id` and reindexes the new file, and every run-scoped consumer keeps accumulating
  across a boundary it cannot see. `agent_lifecycle_id` is never updated at all, so Branch
  forks from the pre-clear conversation.

This phase makes the conversation replacement a first-class lifecycle transition —
a **conversation rollover** — and derives every downstream correction from it. It adds no new
delivery path, no model cost, and no new user surface beyond diagnostics.

### The rollover primitive

- [x] Define a rollover as: the same PTY, the same mux session, a **new agent run**. Mint a
  fresh `agent_run_id` and `agent_run_started_at`, bump a new `SessionRecord.agent_run_seq`
  (0 = the run the session spawned with), and set both `native_session_id` and
  `agent_lifecycle_id` to the new conversation id. `run_cwd` and the run's Project scope are
  unchanged — the conversation moved, the working directory did not.
- [x] Close the outgoing run exactly as an agent exit does: `update_agent_summary`,
  `agent_run_ended(reason="conversation_rolled")`, discard the hook spool, cancel and restart
  the observer, and reset observation state (`root_turn_active`, parser counters/status/
  diagnostic, `state_source_priority`, tokens, context window/pct/peak, model,
  `measurement_source`, compaction count/last/capability/confidence). Nothing that measured
  the old conversation may carry into the new one.
- [x] Open a **new** history row for the new run instead of rewriting the old one. The
  pre-rollover row keeps its `native_id`, `transcript_path`, indexed messages, and final
  token/context figures, and is reachable in history and search exactly like any other ended
  run. This replaces today's behaviour where the pre-clear conversation is deleted from the
  index and only reappears later as a detached `external=1` backfill row.
- [x] Emit `agent_conversation_rolled` (session, previous/next `agent_run_id`, previous/next
  `native_session_id`, backend, `reason`, `source`, `agent_run_seq`) onto the persisted
  EventBus, so the boundary is queryable by every later consumer rather than inferred.
- [x] Keep the rollover idempotent and re-entrant: a repeated signal for a conversation id
  the session is already bound to is a no-op, and a rollover racing the agent-exit check or a
  demotion resolves to one transition, not two. Claude blocks on the hook POST (2 s first
  timeout, 3 attempts), so a retry after a slow daemon must be free — and a rollover that
  *fails* must fail closed to `observation_stale_since` rather than degrading back to the
  silent swap, because "the CLI told us it moved and we did not follow" is precisely the
  state that field exists to name.

### Triggers, strongest evidence first

- [x] **Claude: the CLI's own `SessionStart` hook.** It already arrives over this session's
  loopback ingress authenticated with this session's own secret — the strongest identity
  evidence available, and immune to the sibling gate. Roll over when the reported
  `session_id` is a valid conversation id that differs from the bound `native_session_id`.
  Record the hook's `source` (`clear` / `resume` / `compact` / `startup`) as the reason but
  do not enumerate on it: "the CLI says it is now writing a different conversation" is the
  fact, and the id comparison is what establishes it. `compact` and `startup` report the
  unchanged id and therefore never roll.
- [x] Preserve the existing one-way bind (`_bind_native_id_from_hook`) for the *unbound* case
  unchanged. Rollover is a separate, explicit transition — not a relaxation of the rule that
  a hook cannot silently rekey a bound session.
- [x] Stop discarding the evidence: `hook_event_payload` strips `source` because it collides
  with the EventBus envelope. Re-emit it as `start_source` for `SessionStart` so the reason a
  run rolled is in the event log.
- [x] **Codex and hookless launches: the transcript-switch watcher**, routed through the same
  rollover primitive instead of rekeying in place. Codex now has stable lifecycle hooks, but
  the filesystem watcher remains the fallback when they are disabled, untrusted, or unavailable.
- [x] Narrowed 2026-08-06: whether a transcript belongs to this conversation is the *adapter's*
  answer, because it is the CLI's own resolution rule.
  Claude resolves by working directory, so a relocation into a worktree slug is followed from
  the `transcript_path` every hook payload already carries rather than read as a new
  conversation.
  Codex resolves by thread id, so a resume continues its rollout instead of forking a second
  history row, and a pane never starts out tailing a file a live pane holds.
  The mtime-recency scan is no longer load-bearing for either, which removes the class of
  false rollovers and false staleness that Windows' frozen mtime on an open file produced.
- [x] Tighten the sibling gate instead of accepting it: it currently blocks on the mere
  *existence* of a live same-backend session in the cwd. Narrow it to siblings that are
  genuinely unaccounted for — a sibling whose own transcript was written after the candidate
  appeared is demonstrably still on its own file, and a sibling whose PTY was silent across
  the candidate's creation window cannot have produced it. An unpromoted shell with a pending
  agent launch keeps blocking unconditionally; that race (`_has_transcript_sibling`) is
  unchanged.

### Fail closed when the conversation cannot be followed

- [x] Add `SessionRecord.observation_stale_since`: set when the observed transcript has been
  quiet past a threshold while this session's PTY is demonstrably active and no switch is
  permitted. This is the honest state for a Codex `/new` behind a sibling that cannot be
  ruled out — the alternative is a session that silently reports a dead conversation's
  status forever.
- [x] Take "demonstrably active" from hooks whose event *necessarily wrote root transcript
  records*, not from PTY output and not from any hook. Corrected after the first live pass:
  keying on any hook let `Notification:idle_prompt` — which fires ~60 s after a turn ends
  precisely to say nothing is happening — mark 8 healthy idle sessions stale and 0 real ones.
  Tracked separately as `Session.last_turn_hook_ts`.
- [x] While stale: `_transcript_authoritative()` returns false so the hook/PTY fallback
  resumes driving state; delivery readiness hard-blocks on `transcript_stale`; the session
  inspector and `GET /api/diagnostics/*` show it with the last-observed mtime. Cleared by the
  first record read on any followed transcript, and by a rollover.
- [x] Never guess from PTY bytes. A cleared screen or a redrawn banner is presentation, not
  identity (`CONTROL_PLANE_ROADMAP.md` §5.2); staleness plus fallback is the correct answer
  when structured evidence is absent.

### Downstream corrections

- [x] **Prompt queue**: items bound to the outgoing run strand on `agent_conversation_rolled`
  through the existing `target_run_replaced` path, with the reason "target agent conversation
  was replaced". This is the protection Phase 4 already designed and `/clear` was bypassing —
  a message written for one conversation must never be delivered into its successor.
- [x] **Auto-delivery**: the per-session grant is bound to `agent_run_id`, so a rollover
  invalidates it; verify the controller disables with an explicit audit reason rather than
  failing an opaque `stable_run_identity` check.
- [x] **Branch**: `_branch_source_id` reads `agent_lifecycle_id`, which the rollover now
  updates, so the "original" sibling pane resumes the conversation the user was actually in.
  Fixed by construction; pinned by a regression test.
- [x] **Root-identity reconcile after daemon restart**: `_reconcile_adopted_root_identity`
  currently asserts `agent_run_id == record.id` for a root agent and quarantines anything
  else as misattribution. Teach it about `agent_run_seq > 0` so a legitimately rolled run
  survives adoption instead of being repaired away, and keep the quarantine for genuinely
  unexplained ids. Same for the legacy `_infer_spawn_backend` fallback.
- [x] **Operational telemetry**: per-run compaction/tool/coverage reconcile scopes to the
  live run; the outgoing run's rows are retained, not rewritten by a scan of the new file.
- [x] **mux MCP**: `get_session` exposes `agent_run_id` and `agent_run_seq` so a sibling agent
  can tell that a conversation it was told about has been replaced; `read_transcript` follows
  the live run and never silently splices two conversations.
- [x] **Titler, detectors, Tier 0, annotations**: all already key on `agent_run_id` and need
  no change — the point of this phase is that the key now means what they assume. Cover it
  with tests rather than code so the assumption stops being accidental.

### Phase 5.4 exit criteria

Implementation, fixtures, docs, and live verification are complete
(`tests/test_conversation_rollover.py`, 27 cases).
The frozen desktop app live pass completed 2026-08-12 against real Claude and Codex
conversation replacements under live PTYs.

- [x] `/clear` in a live Claude session rolls the run within one hook round-trip, with and
  without sibling agents in the same cwd, and the session keeps reporting accurate status,
  tokens, context, and model afterwards. **Live-verified 2026-07-29** on the frozen app with
  5 sibling Claude sessions in the same cwd: `SessionStart(start_source='clear')` →
  `agent_conversation_rolled` (`agent_run_seq` 0→1, run `a62be90f…`→`0cb38e64…`, native
  `a62be90f…`→`5d9f5d8d…`), the observer retargeted to the new transcript, `parser_status`
  back to `ready`, and the next turn reported normally.
- [x] The pre-rollover conversation remains a complete, searchable history row with its own
  transcript path and messages; the post-rollover conversation is a separate row. Neither
  contains the other's messages. **Live-verified**: the retired row closed
  `exit_reason='conversation_rolled'`, `final_state='idle'`, keeping its own `native_id`,
  transcript path, and final tokens; the successor row opened under the new run id and native
  id with its own transcript path. Both `agent_visible=1`, sharing the terminal `note_id`.
- [x] A queue item armed before a `/clear` is stranded, not delivered, and an auto-delivery
  grant does not survive the rollover.
  **Live-verified 2026-08-12**: the armed item moved to `stranded` with
  `target agent conversation was replaced`, produced no delivery row, and the successor run
  received a fresh grant with zero sends used.
- [x] Branch after a `/clear` reopens the conversation the user was in, not its predecessor.
  **Live-verified 2026-08-12**: the branched history retained the post-clear marker
  `ROLLOVER-5831` and returned it exactly from the branch.
- [x] Codex `/new` either rolls the run or marks observation stale and blocks delivery, and
  never continues reporting the replaced conversation as live.
  Re-check this against the 2026-08-06 thread-id binding rather than the sibling-corroboration
  path the criterion was written for: a Codex resume now continues its rollout, so `/new` is
  the only Codex event that should produce a new run at all.
  **Live-verified 2026-08-12**: `/new` emitted `agent_conversation_rolled`, opened distinct
  predecessor and successor history rows and transcript paths, and the successor returned to
  `idle` without stale observation state.
- [x] Every rolled run survives `POST /api/daemon/restart` with its identity intact and no
  misattribution quarantine.
  **Operator-confirmed 2026-08-12** on the frozen desktop app after the Claude and Codex
  rollover checks.

## Phase 5.5 - Project context and scan timeline

Control-plane build-order steps 4–5 (`CONTROL_PLANE_ROADMAP.md` §5.4–5.5). The first
model-cost layer of the control plane and the substrate every semantic consumer reads from.
Capture-first: a readable per-session behavioral timeline before anything ranks or narrates
on top of it. No dependency on Phases 4–5, but it **does** depend on Phase 5.4: a timeline is
a claim about one continuous piece of work, and without the run boundary it would describe
two unrelated conversations as one session's history.

**Resequenced 2026-08-10 to run after Phase 5.6, and now gated on its evidence.** This is the
first feature whose cost is continuous rather than per-run, and Phase 5.6 delivers overlapping
self-continuity for free.
Start the timeline only once the free reads are in use and are observably insufficient; if they
turn out to cover the need, this phase shrinks to whatever the deterministic detectors and
transcript reads genuinely cannot answer.

- [x] Project context card: one user-owned, bounded `.swe-mux/project-context.md` file that feeds the scan timeline.
  It starts blank, is edited from the Timeline drawer, saves atomically with revision checks, and includes a copyable setup prompt for an agent to populate it from verified repository evidence.
  swe-mux never crawls Project docs or generates context itself.
  The retired generated-card design is archived and its legacy database rows are inert.
  `design/features/project-card.md`.
- [x] Scan timeline (CP §5.5): periodic and event-triggered cheap-model records forming a
  per-session timeline, per-project opt-in, budgeted, and inert when disabled.
- [x] **Scan records carry `agent_run_id`, not `session_id` alone, and a run is the timeline's
  outer boundary** (CP §5.5). A rollover ends the current segment: the delta window resets to
  the new transcript, the "last 2–3 records for continuity" do not reach back across it, and
  `novelty` is computed only against records from the same run — otherwise the first record
  of a fresh conversation scores as unremarkable because it resembles the one it replaced.
  `agent_conversation_rolled` is itself a scan trigger, so the boundary is represented rather
  than inferred from a gap.
- [x] Instrument the rehydration rate from the first commit — it is the measurement that
  decides whether a Tier 2 source expansion is ever justified.
- [x] Dead-end / negative-result memory (CP §6.2) as the first consumer of the timeline.
  The continuous session title that was to be the second (CP §6.11) is **abandoned** — a
  title that moves stops being a handle the user can find a tab by, which is the whole job.
  Titling is one call per run off the opening request; see `design/features/automation.md`.
- [x] Dead-end capture must not read a rollover as an abandonment. `/clear` says the human
  reset the context, not that the approach failed; only an approach that was tried and
  dropped *within* a run is evidence of a dead end (CP §6.2).
- [x] Ship the persistent spend/budget line (CP §9 UI work) with this phase; this is the
  first feature whose cost is continuous rather than per-run.
- [x] Keep every timeline control in the Timeline drawer.
  The drawer owns Project permission, Project context, the per-run grant, current scan, full-session scan, spend, records, and source expansion; the topbar has no scan action.
- [x] Full-session scan processes uncovered current-run messages oldest first, preserves source-time ordering, never moves the live cursor backwards, and reports completed, partial, or failed progress without weakening any gate or budget.
- [x] Automation enablement is centralized in the Automation dashboard.
  Settings retains provider, model, budget, execution, and advanced rule configuration but no duplicate engine, timeline, titler, summarizer, or attention-observer switches.

### Phase 5.5 exit criteria

- [x] Scan records are per-project opt-in, budget-bounded, and degrade to no records rather
  than to guesses when a provider is unavailable.
- [x] The rehydration rate is measured and visible, not assumed.
- [x] Model spend for the timeline is visible in the Timeline drawer before the feature is enabled by default anywhere.
- [x] No timeline segment, continuity window, novelty comparison, or derived title spans a
  conversation rollover, and the boundary is visible in the timeline UI.

**Live-verified 2026-08-13 on the frozen desktop app.**
The global, Project, and current-run gates were enabled for a new Codex session, which produced a valid record through `deepseek/deepseek-v4-flash` with 330 input tokens, 107 output tokens, and `$0.00004714304` recorded spend.
The provider returned the exact requested model, source expansion rehydrated the authoritative assistant transcript, the visible rehydration metric reached `1.0`, and both scan background loops reported zero faults.
Run-toggle reset and rollover-boundary isolation are covered by the backend and frontend verification suites; the live command path itself was already verified with `/new` under Phase 5.4.

## Phase 5.6 - mux MCP v0.5: situational-awareness reads

Control-plane build-order step 2.6 (`CONTROL_PLANE_ROADMAP.md` §7.5). Phase 4.5 proved the
transport with four read tools; Phase 7.5 is the semantic memory half and is genuinely late.
This phase is the gap between them: the questions an agent asks constantly that the daemon
can already answer from **shipped** machinery, and that today force it to guess, shell out,
or ask the human. It adds no authority: every item is a read, Project-scoped, through the
same token, allowlist, redaction, and rate limit Phase 4.5 established.

Depends on Phase 5.4 (a read that crosses a conversation rollover must name the run it came
from) and on the shipped queue and notes services. It does **not** depend on Phase 5.5.

**Resequenced 2026-08-10 to run before Phase 5.5.** This phase costs no new substrate and no
model tokens, while Phase 5.5 is the first continuously-costing feature.
Its self-continuity read also overlaps part of what the scan timeline was meant to provide, so
building the timeline first risks paying for substrate that free tools would have covered.
Use this phase's observed usage as evidence for or against Phase 5.5.

### Transcript reads: both ends, and your own past

- [x] Make `read_transcript` bidirectional and pageable: a `from` selector (`tail` default,
  `head`) plus an opaque cursor, so a caller can read the **beginning** of a session's
  conversation, not only its tail. The opening request is what identifies a run's work (the
  same finding that made the one-shot titler correct and killed the continuous title,
  CP §6.11), and it is currently unreachable through MCP on any session long enough to
  matter.
- [x] Keep paging inside one `agent_run_id`. A cursor never walks off the end of a run into
  its predecessor; two conversations are never concatenated into one read (Phase 5.4).
- [x] Exclude system/meta records by default, with an explicit opt-in argument for the caller
  that wants them. Existing byte/message caps and the `looks_like_secret` redaction gate are
  unchanged and apply to head reads identically.
- [x] Let a caller read **its own superseded runs**. After a `/clear` the agent retains
  nothing its predecessor run did, and the daemon has all of it; this is the cheapest
  possible self-continuity and it needs no new substrate. Every message is labelled with its
  `agent_run_id`/`agent_run_seq`, and a result from the caller's own earlier run is marked as
  such rather than blended into the present: the Phase 7.5 retrieval-precision rule applied
  one phase early, because this is the exact case it was written for.

### Cheap answers that avoid a transcript read entirely

- [x] Put the run brief on `get_session`: the run's pinned title and opening request
  (`builtin.session-titler-initial` already pins one per `agent_run_id`) beside the existing
  status, `agent_run_id`, and `agent_run_seq`. "What is that session actually working on"
  should cost one small call, not a paged transcript read.
### Deliberately not included

A tool earns its place here only by answering something the caller cannot answer itself.
Every swe-mux agent session is a CLI with shell access, so anything reachable by running a
command in a directory it already knows is not a daemon capability, it is a wrapper.

- **Bounded Git read (branch, status, changed files, diff stat): dropped.** The caller can run
  `git status` and `git diff --stat` itself, and a sibling's worktree path comes from
  `get_session`, so the cross-session case resolves through the shell too.
  Design law 6 still holds (condition on the diff, not on the sibling's story about it); it
  simply does not need an MCP tool to hold.
- **`project_card()`: dropped unless the card demonstrably beats reading the repository.**
  A distilled architecture summary competes with the agent reading root instructions and the
  tree directly, which costs it nothing and is always current.
  Reopen only with evidence that the card answers something the repository does not.

### Memory-source reads pulled forward from Phase 7.5

These are thin callers over shipped Agent Context reads, so they belong with the cheap tools
rather than hostage to the Phase 7.5 semantic layer.
The Phase 6 harness-coverage fix shipped with these tools, so both read the same
descriptor-driven inventory as the Agent Context drawer.

- [x] `memory_sources()` - Project-scoped inventory of the same root-instruction and
  learned-memory sources the Agent Context drawer reads, with harness, scope, capability,
  content hash, modified time, and entrypoint kind.
- [x] `read_memory(source_id)` - exact bounded read of one inventoried source, resolved through
  the typed daemon operation, never a caller-supplied filesystem path.

### Closing the loops that Phase 5 left open

- [x] Add `message_status(message_id)` so the sender of an `mux.notify` can see whether it is
  drafted, armed, delivered, stranded, expired, or refused. Today the write is
  fire-and-forget from the sender's side, which forces an agent to either assume delivery or
  re-send; both are worse than a read.
- [x] Expose the Project's **notes** read-only, and the state of the caller's own
  `request_spawn` drafts. This is the human-to-agent channel with no new trust boundary: notes a
  human captured while testing are exactly the context the agent lacks.
  Retargeted from the observation inbox to Project notes, because that is where humans actually
  write (see the consolidation item).
- [x] Decide the cross-Project read question (CP §18) explicitly rather than by default. "What
  else am I working on right now" is inherently cross-Project; the default stays own-Project
  only, and any widening is a named grant with its own surface, not a quiet scope change.
  Decision 2026-08-12: v0.5 has no cross-Project grant.
  Superseded 2026-08-14: the named grant shipped as the per-call `project` argument on every
  read and write tool (`"fleet"`, or a Project name or id; `project_scope.py`), which is the
  surface that decision asked for. The own-Project default and the no-quiet-widening rule both
  hold — a widened call states its scope and the answer echoes it back.

### Consolidate the observation inbox out of existence

The inbox exists because `request_spawn` needed somewhere inert to land; capturing human notes
was retrofitted onto it.
That leaves a third place to monitor, next to the per-session queue and the fleet queue, which
notifies nothing and which nobody types into because Project notes and the Scratchpad are better
editors that are already open.
A surface you must remember to check is worse than no surface, because entries rot and you stop
trusting it.

- [x] Land pending `request_spawn` drafts in the **fleet queue** as an approval row, so one
  surface holds everything an agent wants from a human.
  A spawn request names no target session because the session does not exist yet; that is a
  grouping problem in a view that already renders sender provenance, not a reason for a second
  surface.
- [x] Drop the human-notes half and point at Project notes and the Scratchpad, which are
  Project-scoped, searchable, editable, and already carry "send to agent".
- [x] Retire the inbox as a place a human goes. `.swe-mux/observations.json` may survive as
  storage if that is cheaper than migrating; the `observations.open` command and the standalone
  view do not.
- [x] Keep approval an explicit human act with the once-only decision and the `seed_text` spawn
  path unchanged. This moves where a request appears, never what approving it means.

### Construction rules

These are Phase 4.5 constraints that every tool here inherits, not separate deliverables.
Every tool is a thin caller over the same typed daemon operation the browser uses (CP §7.1),
so nothing is implemented inside the MCP layer and later clients inherit every bound.
Tools are listed even when disabled and answer with a typed refusal, because clients cache
`tools/list` at session start and a vanished tool is indistinguishable from a broken server.
Scope misses and true misses stay indistinguishable, and empty beats a weak match.

### Post-completion live-audit hardening

The 2026-08-13 frozen-app audit proved the read substrate but found five contract defects that made the free reads insufficient in practice.

- [x] Add `self` and omitted-session defaults to `get_session` and `read_transcript`.
- [x] Add an explicit `agent_run_id` selector for the caller's superseded runs, resolved ahead of live ids so the initial run-id/logical-session-id collision cannot redirect the read to the successor.
- [x] Make `list_sessions` compact, queryable, and cursor-pageable, with one combined 25-row and 32 KiB budget across live and ended results.
- [x] Generate a Claude allowlist for exactly the ten read tools and advertise the same split through MCP read-only annotations; `notify` and `request_spawn` remain permission-gated.
- [x] Store the notification provenance envelope in the queue body itself so the receiver sees sender, run, message id, and correlation id without relying on hidden metadata.
- [x] Repeat the frozen-app Codex/Claude audit after redeploy and record the observed result here.
  Verified 2026-08-13 against fresh Claude and Codex sessions on the frozen app: Claude executed the full read inventory without a permission prompt; both harnesses received a 25-row combined session page with `has_more` and a cursor; omitted session ids resolved to self; Codex's notification arrived after the receiver became ready with the visible message/correlation/sender/run envelope intact; and a post-`/clear` Claude successor read its exact seq-0 run by the colliding old id with `own_superseded_run: true` and no cross-run messages.

### Phase 5.6 exit criteria

- [x] An agent can read the first messages of a sibling's conversation and of its own
  superseded run, and can always tell which run any message came from.
- [x] No read tool crosses a Project boundary, and none returns a record the browser's own
  allowlist would have withheld.
- [x] The surface remains read-only: nothing added in this phase can enqueue, deliver, spawn,
  interrupt, end a session, or write to a PTY. Pinned by the tool-set allowlist test.
- [x] Adding these tools costs no new substrate: each one is traceable to a service that
  shipped in an earlier phase.
- [x] A session on one harness can read an available memory source produced by another through
  the same Project-scoped inventory, with exact attribution and no prompt-time bulk injection or
  store mutation.
- [x] No tool here duplicates something the calling session could answer with a shell command in
  a directory it already knows.

## Phase 5.8 - SSH boundary handling in terminals

A user who types `ssh box` in a mux terminal already gets a working session: bytes, resize,
Ctrl+C, bracketed paste, scrollback, and replay are transport-agnostic and need nothing added.
What does not survive the SSH boundary is every mux *integration*, and it fails silently: the
`MUX_SHIM_DIR` PATH repair does not reach the remote shell, hook ingress is a loopback-only
secret, transcript tailing reads local files, `local_directory_from_osc7` discards any OSC 7
URI that is not an existing local directory so runtime cwd freezes at its last local value,
and status detection has no model for an `ssh` password or host-key prompt. This phase makes
that degradation explicit and makes the prompts safe.

This phase is deliberately not remote execution. A remote host does not become an execution
host here: no remote filesystem provider, no host-scoped Git, no deployed agent bridge. Those
belong to the decision-gated list.

**Split 2026-08-10 into a correctness half and a convenience half.** The correctness half is
the whole reason this phase exists: today a crossed session reports stale values as current and
an auth prompt can read as `idle`, which is the only open work in the roadmap with a
delivery-safety consequence.
The convenience half (named SSH profiles) makes retyping `ssh box` unnecessary and can be
dropped without losing anything.
Ship the correctness half on its own.

**Correctness half completed 2026-08-12.** Named SSH profiles and their doctor check remain
deferred convenience work.

Standing boundary across both halves: mux never persists an SSH password or key passphrase and
never prompts for one outside the PTY.
Authentication happens where the user can see it, and key and agent auth are the supported
paths.

### Correctness: remote-boundary detection and honest degradation

- [x] Detect that a session has crossed an SSH boundary. An OSC 7 URI with a non-local
  authority is already parsed and discarded by `runtime_cwd.py`; it becomes the signal instead
  of being dropped. Detection is best-effort and its absence never asserts a local boundary.
- [x] Report cwd, agent promotion, transcript following, hook ingress, and shim PATH repair as
  **unavailable** for a crossed session rather than stale, reusing the existing
  `agent-bridge-unavailable` vocabulary rather than inventing a second one.
- [x] Freeze runtime cwd with an explicit reason and surface "remote" in the UI instead of a
  stale local path. A frozen value presented as current is the failure mode this phase exists
  to remove.
- [x] Keep a crossed session out of any inference-driven target set: it never becomes an
  auto-delivery or queue target by promotion, and remains a manual send target exactly as any
  shell is.

### Correctness: status detection for SSH prompts

- [x] Classify `ssh` authentication prompts as blocked on a human rather than `idle`: password,
  key passphrase, host-key `yes/no` confirmation, and keyboard-interactive/MFA challenges.
  `idle` is the state delivery arms against, so a blocked prompt read as idle is the one
  failure here with a safety consequence.
- [x] Classify disconnect output (`Connection closed`, `Connection reset`, `Broken pipe`,
  `Timeout, server not responding`) as a terminated transport, not a quiet session.
- [x] Add captured screens for each prompt and disconnect class to the detection golden corpus,
  and verify SSH sessions against the standing-activity axis: a quiet remote shell is idle, a
  remote long-running command is not.

### Deferred: SSH shell profiles (convenience)

Nothing here fixes a wrong answer; it saves the user from retyping a destination.
Schedule it only if the correctness half ships and the retyping actually annoys you.
Design constraints, kept so they are not re-derived: a profile is executable `ssh` plus argv
naming a destination through the existing profile precedence with no new spawn path;
`~/.ssh/config` stays the only source of truth because a mux-stored copy that disagrees with
what OpenSSH resolves is the entire bug class to avoid; and
`-o ServerAliveInterval=20 -o ServerAliveCountMax=2` is injected only when the user's own argv
and resolved config do not already set them, since a user-authored SSH argv is authoritative.

The matching `mux doctor` SSH check in Phase 7 is deferred with it.

### Documentation

- [x] Document reaching mux over `ssh -L` in `design/features/remote-access.md`. This carries
  forward the documentation half of original Roadmap Phase 10 and describes shipped behavior:
  a loopback-addressed forward on any local port passes `allowed_browser_host` and the
  Origin/Host authority check, while a forward addressed by a LAN name returns
  `unsupported Host`. Distinguish it from the router port-forwarding and Funnel exposure the
  same document rejects, and state that an SSH-forwarded peer inherits the same terminal and
  code-execution authority an admitted tailnet peer has.
- [ ] Document SSH profiles and their compatibility limits in
  `design/features/launch-profiles.md` beside the existing WSL and CMD entries, if and when the
  deferred profile half ships.
- [x] Document the prompt classes and the remote-boundary unavailability vocabulary in
  `design/features/status-detection.md`.

### Phase 5.8 exit criteria

- [x] `ssh` from a mux terminal survives, resizes, pastes, and scrolls exactly as a local
  shell, and every integration it disables is visibly unavailable rather than silently stale.
- [x] No SSH authentication prompt is ever reported as `idle`, proven by golden-corpus cases.
- [x] No SSH credential is stored by mux in configuration, history, telemetry, diagnostics, or
  a diagnostic bundle.

## Phase 6 - Agent Context and instruction coverage

Instructions are the *push* half of an agent's context: a harness reads its root instruction
file at startup and it conditions every turn, with no invocation.
That is the axis the prompt library does not cover, because a template is *pull* and affects
the one turn a human invokes it for.
The control-plane framing is the same distinction: instructions are **channel 2 of the return
path** (`CONTROL_PLANE_ROADMAP.md` §7), the slow-moving standing context, while live facts stay
behind the pull tools of Phases 4.5/5.6/7.5.

The first wave shipped 2026-08-02: a read-only Agent Context drawer over Project-root
instructions and harness learned memory, plus an explicit, previewed, conflict-safe one-shot
whole-file copy between root `CLAUDE.md` and `AGENTS.md`.
That already solves the case people actually have, which is keeping root instruction files
consistent.

**Scope reduced 2026-08-10 to harness coverage alone.**
What remains of the original phase was reshaped after two findings.
The generated-content machinery (canonical body, sentinel sections, nested manifest) is
deferred because it has no consumer yet, and skill portability is culled because the harnesses
now solve it by convention.
Both decisions are recorded so they are not re-proposed as scheduled work.

### First wave: Agent Context and manual instruction overwrite

- **Implemented 2026-08-02.** Current-state contract: `design/features/agent-context.md`.
- [x] Add a Project-scoped **Agent Context** utility-drawer tab after Notes and before Git.
  It is a lookup surface, not an editor: file and memory bodies render read-only in the drawer
  with source, scope, modified time, size, and a highlighted selected source. No Agent Context view
  registers an insert target or opens an editable resource tab.
- [x] Show the root `CLAUDE.md` and root `AGENTS.md` as **Project instructions**, including
  `missing`, `in sync`, `different`, and `changed since this agent run started` states. Compare
  normalized text so CRLF-vs-LF alone is not drift. Label this inventory "Project-root sources";
  it does not claim to be the complete effective chain of global, local, nested, imported,
  override, or path-scoped instructions.
- [x] Show **Learned memory** through provider adapters. Claude inventory resolves the
  repository-derived (or configured) auto-memory directory, identifies `MEMORY.md` as the
  entrypoint, lists bounded topic Markdown files, and discloses that repository worktrees share
  the source. Codex uses only a documented/stable inventory when one is available. Every adapter
  returns typed `available | disabled | unsupported | unreadable` capability/status results;
  an unavailable Codex inventory is shown with its reason rather than inferred by scraping an
  undocumented private database.
- [x] Resolve "what this agent could have loaded" from the focused session's backend, live cwd,
  repository identity, and run start while keeping the inventory anchored to the active Project.
  With no focused session, show the Project inventory without a loaded/current claim. A nested
  Project or worktree that shares a provider memory store says so instead of presenting the
  store as exclusively Project-owned.
- [x] Expose inventory and bounded reads through typed daemon operations and allowlisted source
  ids, never a client-supplied home-directory path. Contain Project instruction reads to the
  canonical root; resolve provider memory only through its adapter; reject traversal/symlink
  escapes; cap file count and UTF-8 bytes; and return explicit binary/oversize/unreadable states.
  Reads remain Project-scoped even when the browser reaches the daemon remotely.
- [x] Add **Sync instructions…** as the only first-wave mutation. It offers both explicit
  directions — `CLAUDE.md → AGENTS.md` and `AGENTS.md → CLAUDE.md` — and runs once per user
  confirmation. A missing destination may be created; an existing destination is a deliberate
  whole-content overwrite. No memory file participates, and no direction becomes preferred,
  remembered, scheduled, watched, or automatic.
- [x] Before either overwrite, show the normalized comparison and deterministic diff, name the
  exact source/target, and require confirmation. Commit with expected source and target hashes,
  atomic replacement, conflict refusal on intervening edits, and a recoverable backup/restore
  record. Preserve the existing destination's line-ending convention (source convention when
  creating it), and never follow a target symlink outside the canonical Project root.
- [x] Refresh on explicit Rescan and relevant open-drawer change events without watching every
  provider home continuously. A read failure leaves the last successful inventory visibly stale;
  it never turns a provider into an empty memory list and never affects a running agent.
- [x] Accommodate the ninth drawer icon on narrow touch layouts without a silent two-row wrap.
  Keep 44 px touch height and provide an explicit horizontal-scroll/edge-fade affordance with
  selected-tab auto-scroll, or prove an equivalent single-row layout across the mobile matrix.

### First-wave exit criteria

- [x] The drawer reads root instructions and every supported provider memory source without
  offering inline edits, arbitrary filesystem reads, or misleading unavailable-as-empty states.
- [x] Either manual overwrite direction creates/replaces only its named root target, survives an
  EOL-only comparison without false drift, refuses stale confirmation, and restores from backup.
- [x] No file is written until the user confirms a displayed direction and diff; no background
  process ever repeats or reverses that decision.
- [x] Project switching, focused-session switching, worktrees, nested Projects, disabled memory,
  stale inventories, remote viewing, and the narrowest supported mobile drawer are covered.

### Completed item: harness coverage

This is a defect in shipped behavior rather than new scope, and it is the whole of Phase 6's
remaining work.

- [x] Make Agent Context descriptor-driven instead of a two-harness special case.
  Previously, `agent_context.py` enumerated `claude` and `codex` by name (`instruction:claude` →
  `CLAUDE.md`, `instruction:codex` → `AGENTS.md`, plus two hardcoded memory roots), so a focused
  `omp` session showed a neighbouring harness's inventory or nothing.
  Agent *Environment* was generalized through the registry and covers `omp`, which is what makes
  this an omission rather than a scope choice.
  Resolve it on the descriptor: which harnesses declare a root instruction file, which declare a
  memory inventory, and `unsupported` stays an explicit typed result.
- [x] Extend the shipped whole-file copy along the same axis, so the operation is "copy between
  two declared instruction files" rather than "copy between `CLAUDE.md` and `AGENTS.md`".
  Preview, hash-checked commit, conflict refusal, backup, and line-ending preservation are
  unchanged; only target selection generalizes.

### Deferred: canonical instruction rendering

Deferred, not cancelled, and gated on a consumer rather than on a queue position.

The only thing a canonical `.swe-mux/instructions.md` plus sentinel-delimited rendering can do
that the shipped whole-file copy cannot is write **generated** content into a file the user
owns without clobbering the rest of it.
No such generator exists: mined conventions and recurring failure modes are Phase 6.5 and 7.5
output, and until one of those is committed to writing standing context, the sentinel format,
the nested-target manifest, the nested-precedence and symlink-escape model, and the
watcher-loop conflict tests are cost with nothing to carry.

Precondition for scheduling it: a named consumer that produces durable distilled insight and
needs it in standing context rather than behind a pull tool.
Design constraints that survive whenever that happens, so they are not re-derived: the
canonical body never replaces user ownership of a root file, only sentinel-delimited sections
are written, nested targets come from an explicit manifest rather than recursive discovery, a
mapping can never write outside the Project root, and every write stays an explicit user action
with preview, diff, source and generated hashes, backup, and conflict refusal.
Autosync remains out of scope in every version of this.

### Culled: prompt and skill portability

Culled 2026-08-10.
The harnesses converge on shared skill directories by convention faster than mux could
normalize them: OMP's capability providers already load skills from native `.omp`, imported
`.claude` and `.codex`, shared `.agent` and `.agents`, and project `.github`
(`design/features/backends.md`).
A mux-owned canonical skill model with per-harness adapters would be a converter sitting in a
path the ecosystem is retiring, and it would have to claim equivalences the roadmap elsewhere
forbids claiming.

The prompt library keeps its own job as the pull surface and needs nothing from this phase.
One rule is retained as a standing boundary rather than a task: mux never syncs secrets,
executable trust decisions, harness caches, generated histories, or metadata matched by content
similarity.

### Phase 6 exit criteria

- [x] Agent Context makes Project-root instructions and harness learned memory inspectable
  without making any body editable, and reports exact harness/scope/capability.
- [x] Manual `CLAUDE.md ↔ AGENTS.md` overwrite remains bidirectional, previewed, conflict-safe,
  recoverable, and user-triggered only.
- [x] Every registered harness that declares an instruction file or a memory inventory is
  covered by both surfaces, and one that declares neither reports `unsupported` rather than
  showing a neighbour's sources.

## Phase 6.5 — Control-plane model narration and attention ranking

Control-plane build-order steps 6–7 (`CONTROL_PLANE_ROADMAP.md` §14, §6.7). Narration adds a
cheap-model "why" on top of the deterministic detectors from Phase 3.7; attention ranking is
last in the control-plane order because it needs every other signal. Depends on Phase 5.5
substrate, Phase 2 telemetry, and the Phase 3 notification channels.

Split by cost and by evidence: attention ranking answers "which of 17 sessions needs me", which
nothing else does, while narration is a model-cost "why" layered over annotations the
deterministic detectors already write with their evidence attached.
Build the ranking half first, and gate narration on the annotation surface actually being read.
If the annotations are not being looked at today, narration is polish on an unused feature and
the honest fix is to make annotations worth reading, not to describe them more fluently.

**Shipped 2026-08-13.** Behaviour and invariants: `design/features/attention-ranking.md`.

The narration gate was resolved by decision rather than by measurement: the user stated
plainly that annotations were not going to be read, which is the same evidence the gate was
waiting for and the reason ranking is what makes them reachable at all. Narration ships
alongside it, off by default, because ranking gives it something worth narrating.

The delivery half carries one standing constraint the user set explicitly: **ranked items
never push.** The four channels are in-app surfaces, and the interrupt budget bounds how
often something is presented as urgent rather than how often a device buzzes. The older
settle-gated `waiting` push alert is untouched.

- [x] Model narration (CP §14): the `llm` action kind over normalized slices, stateless,
  read-only, budgeted. A narration failure degrades to the deterministic detector's output,
  never to silence and never to a fabricated cause.
  Gated on evidence that the deterministic annotations are being read.
  (`attention_narration.py`; typed `disabled`/`no_model`/`budget`/`failed`/`empty` statuses,
  metered under `builtin:attention-narration`, per-project `model_narration` opt-in.)
- [x] Attention ranking / inbox (CP §6.7): fan-out estimate, a daily interrupt budget, the
  four delivery channels, and breakpoint delivery.
  (Fan-out from measured burst duration over inter-burst gaps, reporting
  `insufficient_samples` instead of a number below five samples; breakpoint delivery from OSC
  133 markers emitted by the user's own shells, agent panes excluded.)
- [x] Honor the interrupt budget as a hard bound. A usually-wrong signal is worse than no
  signal; the same trust logic as the return-path precision gate.
  (Counted per *incident*, so several detectors describing one event share one slot; the
  hourly cap is only a burst limiter beneath the daily bound.)
- [x] **Rank against the live run only.** A finding anchored to a run the session has rolled
  past describes a conversation the agent can no longer act on, and surfacing it spends
  interrupt budget on something the user already resolved by clearing. Annotations from
  superseded runs stay inspectable in the session's history and are excluded from ranking
  (`agent_run_id != record.agent_run_id`) rather than deleted. Narration slices likewise stop
  at the run boundary — a "why" assembled across two conversations is a fabricated cause.
  (Checked on arrival *and* on every inbox read, so an item ranked before a `/clear` is
  demoted after it.)
- [x] Absence report / digest (CP §6.8) for the time the user was away. A rollover inside the
  absence window is shown as a boundary in the digest, not smoothed over: "you cleared here"
  is exactly the context that makes the rest of the digest readable.
  (Delivered as extra keys on the existing `GET /api/attention/absence` rather than a second
  endpoint; its original keys are unchanged.)

### Phase 6.5 exit criteria

- [x] Ranking never exceeds the configured daily interrupt budget, and suppressed items remain
  inspectable rather than discarded.
- [x] Every ranked item traces to the deterministic facts and annotations behind it; narration
  is presentation over evidence, not a substitute for it.
- [x] No ranked item, narration slice, or digest entry mixes evidence from two agent runs of
  the same session.
- [x] Disabling narration leaves the deterministic detectors and their annotations intact.

## Phase 7 — Windows product maturity, CLI control, and diagnostics

This phase carries forward every incomplete item from original Roadmap Phase 8 and expands
its quality matrix with the Phase 1–6 contracts.

### Practical CLI control

Starting point: `mux` is a thin JSON wrapper over a dozen endpoints
(`ls`, `spawn`, `send`, `kill`, `reload-daemon`, `history`, `projects`, `profiles`, `accounts`,
`history-duplicates`, `resume`, `doctor`), it prints raw API JSON with no table or `--json`
distinction, and `doctor` is an alias for `GET /api/remote/status`.

**Scope cut 2026-08-10 from CLI parity to CLI usefulness.** The original item chased feature
parity with the browser across eleven inspection surfaces.
Two things retired most of that: the browser and mobile surfaces are the interactive client and
do not need a text twin, and MCP now serves the structured-read consumer that would otherwise
have been scripts shelling out to `mux`.
What is left is the part with no substitute: things you run when the UI is not the right tool,
and things a script needs.

- [x] Give the CLI stable ids, conflicts for ambiguous names, actionable exit codes, structured
  errors, human-readable tables by default, and an explicit `--json`; scripts never parse UI
  prose.
  (`cli.py`: `resolve_session` matches exact id, exact name, then unique id prefix, and an
  ambiguous name lists candidates and exits `5`; exit codes `0/2/3/4/5/6/1` for
  success/usage/unreachable/HTTP/ambiguous/not-found/doctor-fail; `render_table` is the default
  and `--json` prints the raw payload.)
- [x] Resolve localhost, direct-tailnet, or optional Serve URLs from config while preserving
  explicit `MUX_URL` precedence.
  (`resolve_base_url`: `--url` then `MUX_URL` then the daemon host/port from `load_config` then the
  loopback default; a tailnet or Serve URL is reachable by setting `MUX_URL`/`--url`.)
- [x] Take every backend/harness list, choice, and label from the harness registry.
  A CLI that hardcodes `claude`/`codex` reintroduces exactly what
  `archive/HARNESS_ABSTRACTION_AND_OMP.md` removed, one layer out.
  (`spawn --backend` choices come from `agent_harnesses()`; `mux harnesses` and the `doctor`
  report read `GET /api/harnesses` / `public_harness_registry`; no harness name is compiled in.)
- [x] Add only the operations that are genuinely better without a browser: scriptable spawn with
  Project-bound profile and argv, session listing with filters, kill, and history resume.
  Anything else waits for a concrete need rather than a parity list.
  (`spawn` takes `--project/--profile/--exe/--arg`; `ls` filters by `--project/--state/--backend`;
  `kill`, `resume`, `reload-daemon` kept.)
- [x] Keep browser presentation actions out of the CLI, and keep every action routed through the
  shared typed daemon operations so authorization, readiness, bounds, and audit live in the op
  rather than in any one client.
  (Every command is a thin call to an existing daemon endpoint; name resolution is client-side
  presentation over stable ids, and the mutation it precedes still routes through the typed op.)
- [x] Never accept or print a provider secret through ordinary output or JSON diagnostics.
  (The CLI renders only the daemon's already-sanitized payloads; the `doctor` report and its
  capability block are built from `public_dict`, connection state, and content-free rows.)

Dropped: broad read-only inspection commands for automation status, capabilities, rules,
firings, annotations, budgets, readiness, process anomalies, quota evidence, memory
capabilities, and message delivery status.
The browser shows these to humans and MCP serves them to agents, so a third rendering is
maintenance with no distinct consumer.

### Consolidated diagnostics

This is aggregation, not new capability.
The daemon already serves 230 routes including `/api/health`, `/api/remote/status`,
`/api/diagnostics/{background,network,status-health}`, and per-session `state-log` and
`diagnostic-bundle`, so `mux doctor` is a formatter over what exists plus the few checks below
that nothing currently answers.

- [x] Turn `mux doctor` from its `GET /api/remote/status` alias into one read-only report over
  the existing diagnostic endpoints: daemon/frontend version, ConPTY and Job Object health,
  shell/profile executables, harness detection and promotion, writable global/Project paths,
  Project config, artifact/migration conflicts, `ccusage`, process/orphan evidence,
  previews/listeners, Tailscale connection state (installed, logged in, connected) and Serve,
  the Windows Defender Firewall inbound rule for the tailnet socket, observer/delivery
  capabilities, rule state, OpenRouter catalog, budgets, quota sampling, queue health, and
  instruction-copy conflicts. The tailnet connection-state, phone-side DNS, and firewall checks,
  and the separate (mutating) firewall repair, are detailed in `NEW_USER_RELEASE_READINESS.md`.
  (`GET /api/diagnostics/doctor`, assembled by the pure `doctor.build_doctor_report` over
  health/remote/firewall/prerequisites/status-health/background/harness-registry payloads; the
  supervisor, background-loop, and identity-collision checks cover ConPTY/Job/observer/queue
  health. The finer local-config checks - ccusage, migration conflicts, OpenRouter catalog,
  quota sampling, instruction-copy - fold in as further checks over the endpoints that already
  serve them, without changing the report shape.)
- [x] Add the **observation-freshness check** (Phase 5.4), which nothing exposes today: agent
  sessions whose followed transcript is stale, whose bound conversation id no longer matches the
  CLI's, or whose rollover was blocked by an unresolvable sibling. This is the one class of
  fault that presents as a perfectly healthy session, so a silent daemon is not evidence of
  health.
  (`doctor.observation_freshness` scans every agent session's `observation_stale_since` /
  `observation_stale_reason` and emits one content-free row per affected session; the report's
  `freshness` check fails when a row is delivery-blocking and warns otherwise. Covered by
  `tests/test_doctor.py`; documented in `features/status-detection.md`.)
- [x] Publish machine-readable capability/version information through health diagnostics;
  redact secrets, terminal bytes, prompt/message content, media, and credentials.
  (The report's `capabilities` block carries daemon/UI versions, platform, per-harness detection
  and CLI-version-drift, and remote/firewall availability; every input is an already-sanitized
  source and the freshness rows are content-free.)

Deferred with the Phase 5.8 convenience half: the SSH-profile check that resolves each
configured destination through `ssh -G` without connecting.
The remote-boundary listing it also carried belongs to the correctness half and lands with the
observation-freshness reporting instead.
- [x] Give every failed check a concrete remedy and distinguish unavailable optional
  features from failures compromising terminal ownership, cleanup, or delivery safety.
  (Each check carries a `severity`: `critical` for a lost supervisor, a dead background loop, an
  identity collision, a delivery-blocking stale observation, or a needs-repair firewall rule;
  `optional` for an uninstalled harness or a logged-out Tailscale; `info` for CLI-version drift.
  Every non-ok check names a concrete `remedy`.)

### Windows soak and quality matrix

- [x] Expand Python coverage for configuration/migrations, adapters/state races, lifecycle,
  Host/Origin/WS boundaries, Projects/layouts, history/resume, events/rules/annotations,
  OpenRouter fixtures, Project resources/accounts, Git/worktrees, CLI, process ownership,
  previews/reaping, telemetry, queues/mailboxes, and the instruction copy operation.
  (The suite already covers most of this densely - `test_security_phase5.py` pins the
  Host/Origin/WS boundary, `test_config_service.py`/`test_settings_store.py` the config and
  migrations, and so on - so this phase's additions filled the measured gaps rather than
  padding: `tests/test_cli.py` and `tests/test_doctor.py` (CLI + diagnostics), and the
  harness-adapter matrix and ConPTY files below (adapters, process ownership). Per the
  "small trusted suite" rule, redundant tests for already-covered subsystems were deliberately
  not added.)
- [x] **The agnostic harness coverage guard.** `tests/test_harness_adapter_matrix.py`
  parametrizes over `HARNESSES` through the real `build_agent_adapter` dispatch and asserts,
  for every registered harness, a launchable spawn spec, a resume spec that carries the
  declared resume tokens and the conversation id, graceful exit keys, and (on Windows) that the
  argv survives the exact ConPTY `list2cmdline`/`CommandLineToArgvW` quoting round-trip. A
  per-harness `_ADAPTER_EXPECTATIONS` entry is required and `test_adapter_matrix_covers_every_
  harness` fails without one, so a future harness cannot ship without adapter coverage - the
  adapter-level sibling of `test_every_dialect_has_a_reader_that_actually_reads`.
- [x] Add real-browser/Playwright coverage where a defect would be invisible to the existing
  suites and expensive to catch by hand: the mobile keyboard viewport, pane drag and split,
  terminal input ownership across two clients, and focus management.
  Prefer a small suite that is trusted and kept green over a broad matrix that rots; add a case
  when a real defect escapes, rather than enumerating every screen up front.
  (Playwright was already wired - `frontend/test/renderer/*.spec.ts`,
  `playwright.renderer.config.ts` - and the four targets are covered by the unit suite
  (`inputOwnership`, `mobileKeyboard`/`keyboardReserve`, `dragReorder`/`pointerDragClaim`,
  `modalFocus`) plus renderer geometry. Added `frontend/test/renderer/workspace-smoke.spec.ts`:
  a deliberately small pane-geometry + mobile-composer smoke on stable selectors that stays green
  even if the voice overlay regresses. The pre-existing `pane-layout.spec.ts` rot was then
  root-caused and fixed (see the note below), so CI runs the whole renderer suite
  (`npm run test:renderer`, 73 tests green).)
- [x] Add real Windows ConPTY integration tests for paths with spaces/Unicode, large output,
  resize, Ctrl+C, bracketed paste, input-owner handoff, browser reconnect, process
  attribution, forced daemon death, manual queue send, and safe auto-delivery races.
  (`tests/test_conpty_integration.py` (marked `conpty`, Windows-only) spawns a real `cmd.exe`
  through `PtyHost` and covers spaces/Unicode cwd, non-ASCII output round-trip, a >256 KiB
  output burst past the coalescing window, resize, Ctrl+C-injection survival, and pid capture.
  The others were already proven: reconnect/forced-death/attribution in
  `test_pty_supervisor.py`, input-owner handoff in `test_terminal_arbitration.py`, queue/
  delivery races in `test_prompt_queue.py`/`test_auto_delivery.py`. Bracketed paste is an
  xterm/application feature, covered by the frontend suites and the paste-replay tests, not a
  pseudoconsole property.)
- [x] Maintain Windows CI for ruff, mypy, pytest, frontend typecheck/test/build, and focused
  ConPTY/browser smoke tests. Public artifact and multi-OS matrices remain Phase 11.
  (`.github/workflows/ci.yml`, a single windows-latest job mirroring `.worktree-verify` plus the
  production frontend build and the `workspace-smoke` renderer suite; the ConPTY integration
  tests and the harness-adapter matrix run inside the ordinary pytest step. The repo is
  local-only today, so the workflow activates when a GitHub remote is added; every step is
  verified to pass locally.)
- [x] Use the proving period to record observed workflow friction as explicit follow-up work
  without reopening completed decisions or silently expanding authority.
  (Recorded below.)

**Proving-period friction (follow-up, not reopening decisions):**

- The `frontend/test/renderer/pane-layout.spec.ts` renderer suite was **pre-existing red on
  master** and is now **fixed**. Root cause: `paneHarness.tsx` stubbed `status.commands` as `{}`
  and omitted the now-required `commands: Command[]` prop on `<VoicePlayer>`/`<ConversationSurface>`;
  `VoicePlayer` passes `status.commands` straight into `configuredCommands`, and `{} || []` stays
  `{}`, so `configuredActionsFor` called `.find` on a non-array and crashed the *entire* overlay
  render (`rootChildCount: 0`). It went unnoticed because **`frontend/tsconfig.json` includes only
  `src`, so `test/` is never typechecked**, and `.worktree-verify` does not run `test:renderer`.
  Fixed by making the harness stub match the current component contracts; the whole renderer suite
  (73 tests) is green and now runs in CI. The deeper gap - test files being outside tsc's scope,
  which is how a harness can silently drift from the components it mounts - is the real follow-up:
  a `tsconfig.test.json` (or adding `test` to `include` with the vite/preact ambient types) would
  have caught this at typecheck time.
- Writing `\x03` to a `cmd.exe` ConPTY does not interrupt a running command in this
  environment (a console-control-event nuance, not a byte-delivery one). swe-mux only owns
  forwarding the byte, so the ConPTY test asserts the shell *survives* the injection rather than
  that the command is interrupted; if reliable Ctrl+C-to-agent behaviour is ever required, it is
  a separate investigation into `GenerateConsoleCtrlEvent` over ConPTY.

### Phase 7 exit criteria

- [x] `mux` controls important daemon operations with stable human/JSON output while the
  browser remains the primary interactive interface.
- [x] `mux doctor` identifies actionable local configuration, integration, ownership,
  tailnet, provider, telemetry, automation, and queue problems without mutation or leaks.
- [x] Windows desktop/mobile core workflows, delivery-safety cases, and forced cleanup pass
  the focused automated matrix; unresolved friction is explicitly scheduled or rejected.
  (The focused matrix - `.worktree-verify` plus the real-ConPTY integration tests, the
  harness-adapter coverage guard, and the `workspace-smoke` renderer suite, all wired into
  `.github/workflows/ci.yml` - is green; the two pieces of residual friction are recorded
  above with their follow-up owners.)

## Phase 7.5 — mux MCP v1 and cross-session memory

Control-plane build-order step 8 (`CONTROL_PLANE_ROADMAP.md` §6.6, §6.8, §6.10, §7). This is
the memory half of the return path: the tools that make swe-mux's third-person, all-time,
all-sessions record queryable by a first-person agent mid-task. It sits here because it needs
Phase 5.5 substrate underneath and the Phase 7 typed daemon operations to call through, and
because it inherits the transport, identity, and restart contract already proven in Phase 4.5.

**Split 2026-08-10.** The memory-source reads (`memory_sources`, `read_memory`) were thin
wrappers over shipped Agent Context and are pulled forward into Phase 5.6, so they are no longer
hostage to substrate that may never be built.

What remains splits again by dependency rather than by tool family, per `CONTROL_PLANE_ROADMAP.md`
§9 step 8: `provenance` and `verifiedStatus` read Tier 0 and the shipped step 3 detectors and are
buildable now, while only `priorResolutions` and `deadEnds` genuinely need the Phase 5.5 scan
timeline.
Do not hold the deterministic half behind the semantic half.

### v1 tool surface

- [x] `mux.provenance(file)` — who touched this, at what hash, and what tests ran on it
  (CP §6.1). (`build_provenance_edges` plus raw file_write/file_read/test_result facts;
  ambiguous edges withheld and counted.)
- [x] `mux.priorResolutions(error)` — normalized error signature to a previously verified fix
  (CP §6.10). (Equality on the SHA-256 error fingerprint via `automation_store.experiences`,
  never a substring; low-confidence <0.5 withheld.)
- [x] `mux.deadEnds(subsystem)` — approaches tried, abandoned, and why (CP §6.2). (Scan records
  with `approach_status in {abandoned,failed}` and a non-empty `dead_end`; `subsystem` matched
  as a substring of target/intent/summary; a `/clear` rollover structurally never counts.)
- [x] `mux.verifiedStatus(claim)` — is this actually tested or merely declared done (CP §6.3).
  (`detect_declared_vs_verified` over a run's test facts; defaults to the caller's own current
  run, `session_id` targets another.)
- [ ] Cross-session interlocks (CP §6.6) and digests (CP §6.8) as the human-facing half of the
  same substrate. (Deferred: `cross_session_interlocks` stays reserved and unimplemented in
  `automation_registry.py`; the four agent-facing reads shipped without it.)

### Harness-memory bridge

The tools moved to Phase 5.6; these are the rules that govern them wherever they ship.

- Access stays pull-only. Harness memory is never injected into every prompt, copied into
  another harness's private store, or written by MCP. The Phase 6 instruction overwrite remains
  a human operation over declared root files; it is not an agent tool.
- Cross-Project sources are indistinguishable from missing, and disabled, unsupported,
  unreadable, and stale sources stay explicit results rather than empty success.
- Raw memory is attributed with harness, repository/Project scope, source id and hash, and
  modification time. It is inspectable context, not a verified fact: it enters
  `priorResolutions`, `deadEnds`, `provenance`, or standing instructions only through the
  evidence and confidence gates, keeping its origin.
- Reads reuse the Phase 6 adapters and typed daemon operations. MCP never couples to a harness's
  undocumented database and never downgrades an `unsupported` status to "no memories".

### Retrieval precision gate

- [x] Enforce per-tool scope and confidence thresholds below which a tool returns **nothing**
  rather than a weak match: same Project, exact normalized signature, verified provenance.
  Empty is acceptable; plausible-but-wrong is corrosive, because an agent that acts on one bad
  match either stops calling or propagates the error. (Fingerprint equality on
  `prior_resolutions`, ambiguous-edge withholding on `provenance`, and the <0.5 / <0.4
  confidence floors, each returning a suppressed count.)
- [x] **A retrieved memory names the agent run it came from, and a result from the caller's
  own earlier run is labelled as such rather than blended into the present.** After a `/clear`
  the agent has no memory of the work Phase 5.4 attributed to its predecessor run; a tool that
  returns that work unlabelled reads as the agent's own recollection and is exactly the
  "plausible but wrong" failure this gate exists to prevent. Sibling-run results are legitimate
  and useful — they just have to be attributed. (`_run_attribution` tags every result
  `your_current_run` | `your_earlier_run` | `sibling_run` | `unknown`.)
- [x] Tag every retrievable insight with confidence and scope so low-confidence items can be
  withheld from the agent while still being shown, with a suppressed count, to the human.
  (Each tool returns `low_confidence_suppressed`/`ambiguous_suppressed` counts and a
  `not_opted_in` note for Projects in scope that did not enable the automation.)
- [x] Measure retrieval outcomes. A tool whose results are not being used, or are being
  contradicted, is a defect to fix, not a feature to leave running. (`McpService.memory_outcomes`
  records per memory tool how often it returned something, returned empty, and how many
  low-confidence items it withheld, surfaced in `McpService.status()` under `memory_outcomes`; a
  tool whose `empty` count dominates its `calls` is the defect this makes visible. A
  used-vs-contradicted behavioural signal, which needs correlating later agent turns, is the
  deeper follow-on and is not built.)

### Phase 7.5 exit criteria

- [x] Every v1 tool returns results traceable to specific Tier 0 facts, annotations, or scan
  records, and returns empty in preference to a low-confidence match. (Each result carries the
  source facts/rows and its run attribution; the confidence floors and ambiguous-edge
  withholding enforce empty-over-weak.)
- [x] v1 adds no authority: the surface remains read-only, with writes still confined to the
  Phase 5 queue callers. (All four tools carry read-only MCP annotations and live in
  `READ_TOOL_NAMES`.)
- [x] Enabling v1 is per-project opt-in through the existing enablement DAG, and disabling it
  leaves the Phase 4.5 and 5.6 read surfaces working. (`MEMORY_TOOL_AUTOMATION` gates each tool;
  a disabled automation raises `disabled` (409), an absent substrate `unsupported` (503), never
  a fake empty, and the other read tools are untouched.)
- [x] No tool result silently merges two agent runs, and a caller can always tell which run a
  result came from. (`_run_attribution` labels every result, and a caller's own superseded run
  reads as `your_earlier_run` rather than blending into the present.)

## Phase 7.6 - mux MCP session control: interrupt and end

Control-plane build-order step 9 (`CONTROL_PLANE_ROADMAP.md` §7.6, §16). Every MCP tool
before this phase is a read or a draft. This is the first one that **acts on another running
agent**, and it is scheduled deliberately rather than left decision-gated forever: an agent
that can see a sibling wedged in a loop, or that has finished the work a worker was spawned
for, should be able to stop it instead of only telling a human about it.

Two capabilities, kept as two tools, because they have different blast radii and must be
grantable separately:

- **`interrupt(target)`**: stop the current turn. The session keeps living, its conversation
  and PTY survive, and the work it was doing is discarded.
- **`end_session(target)`**: the session goes away. Allowed against the caller itself.

It sits after Phase 7 because the graceful-stop daemon operation it needs does not exist yet
(`SessionManager.stop` is a hard kill that marks the record `killed`), after Phase 5's
readiness contract because an interrupt is a PTY write, and after Phase 6.5 because a
sibling-initiated interrupt is exactly the kind of event that must never be silent.

### The daemon operations underneath

- [x] Build a **graceful session end** as a typed daemon operation, used identically by the
  browser, CLI, and MCP: interrupt the current turn, send the harness's own exit sequence
  from its adapter, wait bounded for the CLI to tear itself down (transcript flushed, history
  row closed, run ended cleanly), and fall back to the existing hard stop only on timeout.
  (`_end_session_gracefully` in `server.py`, bounded by `session_control_graceful_timeout_s`;
  the interrupt op is `_interrupt_session_pty`, both routed through `_record_operator_input`.)
- [x] Distinguish the end reasons durably: `agent_ended` (a session ended by an agent through
  this surface, gracefully or by fallback) is not `killed` (operator hard stop) and not
  `exited` (the CLI ended on its own). A post-mortem must be able to tell which happened. (New
  `SessionRecord.requested_end_reason`, preferred by `_mark_ended`; `SessionManager.stop` gained
  a `reason` param.)
- [x] Own the interrupt sequence in the **adapter**, never in the MCP layer. The escape
  sequence is per-harness and already resolved for the voice interrupt path; MCP calls the
  same operation rather than learning any keystroke. (The graceful exit sequence comes from the
  adapter's `graceful_exit_keys()`, carried on the PTY as `graceful_exit`.)
- [x] Gate the interrupt on the delivery-readiness predicate with its existing fail-closed
  contract: `safe` proceeds, `blocked` refuses, `unknown` never authorizes. Interrupting a
  session that is mid-approval-prompt or in a menu is corruption, not a stop. (`_gate_readiness`
  in `session_control.py`, refusal code `readiness_not_safe`; no confirm override exists.)

### Authority: a per-Project grant, and the same model spawn has been waiting for

- [x] Add a per-Project **agent authority grant** for these tools with three positions, and
  make `draft` the default: `off` (typed refusal), `draft` (the call writes an inert
  observation-inbox request with full provenance, identical in shape to `request_spawn`, and a
  human approves and the approval is what acts), and `granted` (direct, inside a per-origin
  budget, fully audited). It lives in the existing enablement/opt-in surface, per Project,
  never machine-wide. (`off` is the absence of the new `session_control` automation; the
  draft/granted split is the `.swe-mux/config.toml` field `session_control_grant`, default
  `draft`, read by `project_session_control_grant()`; a draft writes a `control_request`
  observation.)
- [x] Resolve agent-held **spawn** authority under this same grant, or not at all. The
  decision-gated entry exists because spawn converts one prompt injection into unbounded
  fan-out; the answer is a bounded standing grant with a budget and an audit trail, not a
  permanently different mechanism for a capability the user wants exposed. If the grant model
  proves out for interrupt/end, `request_spawn` gains the same `granted` position with its
  own budget; if it does not, spawn stays drafted and so do these. (Implemented 2026-08-16 on
  user request: `request_spawn` now takes the `granted` position through the same
  `SessionControlService`. A new per-Project `spawn_grant` config field ("draft"|"granted",
  default "draft"), gated by the same `session_control` automation, decides whether an agent
  creates a session in a Project directly or drafts the Phase 5 inert request. Authority is by
  target Project, exactly as for interrupt/end, so an agent can spawn into any registered
  Project that granted it - the flow "a swe-mux session spins up and later ends a Continuity
  session" works when both Projects grant it. Bounded by a dedicated per-origin
  `agent_spawn_hourly_budget` (default 10, smaller than the interrupt/end budget because spawn's
  blast radius is larger), idempotency, and an `agent_session_control` audit event with
  `action:"spawn"`. The default everywhere stays the inert draft, so nothing spawns directly
  until an operator raises a Project's grant.)
- [x] Bound the granted path with the machinery `agent_messaging.py` already proves: Project
  scope, live-agent-only targets, per-origin budget, chain depth and cycle detection over the
  recorded path (A interrupting B while B interrupts A is a loop), idempotency keys, typed
  refusals instead of JSON-RPC faults, and a master kill switch. (`SessionControlService`:
  per-origin hourly budget, reciprocal-cycle guard `relay_cycle`, `correlation_id` idempotency,
  typed `QueueError` refusals, and the `session_control_enabled` master switch.)

### What must stay impossible

- [x] Never reachable: a target outside the scope the call asked for (indistinguishable from
  nonexistent, as everywhere else), a shell or non-agent pane, and **the session that owns
  the running daemon**, because job-object inheritance means ending that session takes the daemon
  with it, which is a known failure mode and not something an agent may trigger.
  Scope here means what it means for `notify` since 2026-08-14: the caller's own Project by
  default, another Project only when the call names it. (Refusal codes `unknown_target`,
  `not_agent_target`, and `forbidden_target`; the daemon-owner check is `_session_owns_daemon`,
  a psutil ancestry test, holding even for an approved draft.)
- [x] Self-termination is permitted and is the ordinary case for a finished worker, but it is
  the caller's last act and must not destroy the record of why: the tool returns its result
  before teardown begins, the final turn is flushed and retained, and the ended session stays
  readable through `list_sessions(include_ended)`, `get_session`, and history. An agent may
  end itself; it may not erase itself. (Self-end schedules the graceful op and returns a
  `self: true` result before teardown; only `end_session` allows self, `interrupt` refuses it
  with `self_not_allowed`.)
- [x] Never add automatic remediation on top of this. "Interrupt and re-run the turn" is
  resampling, which amplifies injected content (CP §16); a rewind stays human-directed with a
  corrected instruction. (No resampling or auto-retry path exists on either tool.)
- [x] Never let an interrupt or end happen silently. Each one emits to the event log with the
  calling session and run as provenance, appears in the fleet audit surface beside agent
  messages, and is a candidate for the Phase 6.5 attention channels. The human learning that
  one agent stopped another from a status change alone is a defect. (Every action emits
  `agent_session_control`; drafts emit `agent_control_drafted` and surface in the Fleet Queue
  `control_requests` list.)

### Phase 7.6 exit criteria

- [x] A live agent can interrupt and end another agent session in its Project, and its own,
  through MCP, with the default grant requiring a human approval and the granted position
  bounded and audited. (`interrupt`/`end_session` MCP tools over `SessionControlService`;
  `draft` writes a `control_request`, `granted` acts inside the budget/cycle/idempotency
  bounds.)
- [x] An agent-ended session's transcript, final turn, history row, and end reason survive the
  end and are readable afterwards. (Self-end returns before teardown; the record persists with
  `agent_ended` and stays readable through `list_sessions(include_ended)`/`get_session`/history.)
- [x] A graceful end is attempted before any hard stop, and the two are distinguishable in the
  durable record. (`_end_session_gracefully` tries the adapter exit sequence within
  `session_control_graceful_timeout_s` and reports `graceful: true/false`; the hard fallback
  still records `agent_ended`.)
- [x] An interrupt is refused when readiness is `blocked` or `unknown`, and no interrupt lands
  in an approval prompt or a menu in the proving corpus. (`_gate_readiness` refuses any
  non-`safe` state as `readiness_not_safe`, on both the granted and the approved-draft paths.)
- [x] Disabling the grant leaves every earlier MCP phase working unchanged, and the browser's
  own stop/interrupt controls are unaffected by any setting here. (The grant gates only the two
  tools; the read surface, `notify`/`request_spawn`, and the browser's own DELETE/interrupt
  paths are untouched.)

## Phase 7.7 — Consolidate behavioral summary: retire the turn summarizer, adaptive titling, and near-term scan-timeline consumers

**Status: implemented in-tree (not yet landed or redeployed).** The turn summarizer is retired and the
scan timeline is the single behavioral-summary producer; adaptive titling (`continuous_title`) and
phase-transition signals (`phase_transitions`) ride a freshly saved scan record through
`behavioral_consumers.py` on one shared pivot definition; and the near-term consumers
(`timeline_handoff`, `catch_me_up`, `live_blockers`, `semantic_history_search`) are model-free
derivations over the scan spine in `scan_consumers.py`. Design: `design/features/automation.md`,
`design/features/scan-timeline.md`.

The scan timeline (Phase 5.5) is a run-scoped semantic index over transcript deltas and Tier 0 facts, per-Project and per-run gated, with backfill.
The turn summarizer (`observer_summarizer_enabled`) is the older, cheaper observer: one `turn-summary` run note per completed turn, a single global bool, no per-Project or per-run gate, and no backfill.
The scan timeline subsumes it: a scan record already carries a turn's `work_phase`, `intent`, `summary`, `blockers`, and target paths, plus a deterministic novelty score the summarizer never produced.
So this phase makes the scan timeline the single behavioral-summary substrate and spends the freed surface on a title that adapts as a run's scope widens, rather than maintaining two overlapping summarizers.

This phase depends only on shipped substrate (the Phase 5.5 scan timeline and its novelty signal) and the existing one-shot titler; it needs neither Phase 7.5 nor Phase 7.6.

### Retire the turn summarizer

- [x] Identify every consumer that reads `turn-summary` run notes — the run-notes view, the away report (`/api/attention/absence`), and any attention input — and repoint it at the scan timeline, because a scan record is not a run note and the feed would otherwise silently go empty. (Run-notes view carries `scan_records`; away report carries `scan_records`; the stalled-triage `summary_chain` input, the handoff export, and the second-opinion prompt read the scan spine.)
- [x] Remove the `builtin.turn-summarizer` observer and the `observer_summarizer_enabled` config field once no consumer depends on the tag. Migrate an existing `true` value to nothing rather than leaving a dead toggle; a config predating the removal must load without error.
- [x] Keep the historical `turn-summary` run notes already written; they stay readable, they are just no longer produced. Do not delete durable records to retire a producer. (Handoff/second-opinion fallbacks still read the `turn-summary` tag.)

### Adaptive titling driven by the scan timeline

The titler today names a pane once from the run's opening request, then freezes the title until a manual `title/regenerate`.
An adaptive titler broadens the name only when the work genuinely pivots — a run that opened on "Phase 7" becomes "Phase 7 + 7.5 diagnostics/MCP" once the scope actually widens — by consuming the scan timeline the phase above makes canonical.

This deliberately revisits `CONTROL_PLANE_ROADMAP.md` §6.11, which abandoned continuous titling because a title is a *handle* a user finds a tab by, and a handle that moves is not one.
That objection is retained here as the **binding design constraint**, not overridden: the failure it recorded was a `turn_ended` titler that renamed the pane from its most recent turn every turn, producing `OK` / `FrozenClaude` for runs whose subject never changed.
The bet this phase makes is that a scan-timeline-driven titler, gated on real pivots and biased hard toward stability, can broaden on the rare material shift without becoming that thrashing handle — and it is user-toggleable, so a user who dislikes any movement turns it off and keeps the one-shot title.

- [x] **Stability is the default; a re-title is the exception.** The gate fires only on a genuine pivot: a `novelty` spike combined with a `work_phase`/`target` transition or a new `user_ask`, with debounce and hysteresis so a brief detour does not move the title and a title never rewrites twice in quick succession. Routine progress, tool chatter, and same-subject turns never re-title. Per-turn re-titling is explicitly forbidden. (`evaluate_pivot` + the titler's cooldown in `behavioral_consumers.py`.)
- [x] **The model prompt is written to under-do it, not over-do it.** The synthesis call is given the current title and the recent scan records and instructed to *keep the current title unchanged unless the run's subject has materially changed*, to return the existing title verbatim when in doubt, to prefer broadening the existing handle over inventing a new one, and to emit a compact task label (no backend or "session" prefixes). "No change" is a first-class, common, cheap outcome — the prompt must make returning the current title the easy answer, and a no-change result writes nothing.
- [x] Synthesize from the accumulated same-run scan records (their `work_phase`, `intent`, `user_ask`, and `summary`), on the cheap model, and write the title the same way every producer does — a new `title` annotation that becomes the run's `generated_title`. (Deviation from the sketch: the write goes through the shared `title`-annotation → `generated_title` path directly rather than re-entering the prompt-titler's `title_regenerate_requested` state machine, which is coupled to prompt input and the provisional/settled ladder; a dedicated `BehavioralConsumerService` keeps the anti-thrash discipline testable and off the scan path's budget/latency. Manual `title/regenerate` and the one-shot prompt titler are unchanged.)
- [x] Stay `auto_named`-only: an adaptive re-title never overwrites a title the user set by hand, and an explicit manual regenerate still wins. The pin is a property of the session and survives a rollover, per §6.11.
- [x] Stay `agent_run_id`-scoped, inheriting Phase 5.4's run boundary: a title broadens within one conversation, and a `/clear` starts a fresh run that retitles from its own opening request rather than carrying the old one across the boundary (a rollover is the one always-material shift). (Pivot state is keyed per `agent_run_id`; a rollover disables the old run's scan grant, so no adaptive record crosses the boundary.)
- [x] Gate it per-Project and per-run through the same enablement surface as the scan timeline it consumes, and make it independently toggleable and **off by default**, so enabling the scan timeline does not force moving titles on anyone; a Project or run without it simply keeps the one-shot title. (`continuous_title` opt-in per Project; per-run inherited because the consumer only fires on a live scan record, which needs the run's scan grant.)
- [x] Measure it before trusting it: count re-titles per run and surface the rate, so "it re-titles too often" is a number to tune the gate against rather than a vibe. A titler that moves on anything but a real pivot is a defect to fix, exactly as §6.11 warned. (Re-title count in the scan snapshot's `adaptive_title` field; the in-tree suite asserts a stable-subject run measures zero.)

### Near-term scan-timeline consumers

Once the scan timeline is the single behavioral-summary substrate, these are cheap derivations over the per-record spine (`work_phase`, `intent`, `claim`, `user_ask`, `blockers`, `target_paths`, `summary`, `novelty`), not new transcript reads.
Each is independently toggleable through the same per-Project/per-run enablement as the timeline it reads, and each obeys the shared discipline: **empty beats plausible-but-wrong, and every derived result names the `agent_run_id` it came from** so a sibling run's work is never blended into the present.

- [x] **Timeline-based handoff.** Regenerate the handoff export from a run's scan records rather than from annotations, so the handoff is phase-structured ("was in X, hit blocker Y, next step Z"). The run's scan spine is the best handoff prompt there is (`design/features/history.md` owns the export surface). (`timeline_handoff` opt-in; falls back to annotation summaries when off or the run has no scan records.)
- [x] **Catch-me-up digest.** An on-demand rollup of the run's scan spine — what phases it went through, what it claims done, what is blocking it — the same derivation as the absence report but scoped and pulled rather than time-bounded. (`GET /api/sessions/{sid}/catch-me-up`, gated `catch_me_up`. Shipped per-session; a per-Project rollup endpoint is a thin follow-up over the same `catch_me_up` derivation and is not yet added.)
- [x] **Live blockers view.** Aggregate the `blocked_on` field across active sessions into a fleet glance of "these sessions are waiting on something," without opening any of them. (`GET /api/attention/blockers`, gated `live_blockers`. v1 signal is the latest scan record's live `blocked_on`; the "no matching Tier 0 progress since" tightening is a noted refinement, not yet wired.)
- [x] **Phase-transition signals.** Emit a durable annotation on a genuine `work_phase` pivot (`phase-pivot`, informational) and on a prolonged flat-`novelty` stall within one phase (`phase-stall`, cheap-blocking), feeding the Phase 6.5 attention channels through the ordinary annotation-ranking path. This expresses "session pivoted" / "stuck in debugging for 40 min" — states today's status detection cannot. It reuses the same pivot gate (`evaluate_pivot`) as adaptive titling, so the two never disagree about what a pivot is.
- [x] **Semantic history search.** Search over scan `summary`/`intent`/`target` records so "find the run where I fixed the CRLF thing" resolves against distilled subjects rather than a raw transcript grep. (`GET /api/history/scan-search`, gated `semantic_history_search`, scoped to one run or Project.)

### Phase 7.7 exit criteria

- [x] Exactly one behavioral-summary producer runs (the scan timeline); no consumer of the former `turn-summary` notes silently loses its feed, and a config predating the summarizer's removal still loads.
- [x] An auto-named run's title changes only on a material pivot and stays stable through routine progress, drawing only on that run's scan records, and never overwrites a human-set title. The measured re-title rate for a stable-subject run is zero.
- [x] Adaptive titling is off by default and independently toggleable; with it off (or the scan timeline off) titling is exactly the current one-shot behavior.
- [x] Each near-term consumer is independently toggleable, returns empty rather than a low-confidence guess, attributes every result to its `agent_run_id`, and never merges two runs. Adaptive titling and phase-transition signals share one pivot definition.

**Live-verification note (open):** the adaptive-titler synthesis call and the phase-transition
signals exercise the real OpenRouter scan producer, which an isolated test daemon cannot reach; the
in-tree suite proves the pivot gate, the derivations, the endpoints (gating + attribution), the
summarizer retirement, and the config-load tolerance deterministically. Semantic re-title behavior
is to be confirmed live on the frozen app after landing, matching the scan-timeline verification
stance.

## Phase 7.8 — Git provenance re-attribution: committer and contributors, not shared-head ambiguity

Git provenance today marks almost every commit `ambiguous`, and the cause is one rule, not a fundamental limitation.
`GitProvenanceService` sets `ambiguous` whenever `_checkout_session_count(root) > 1` — that is, whenever more than one live session shares the checkout directory (`src/swe_mux/git_provenance.py`).
A shared `HEAD` is a fact about the starting point, not about the commit event, so this measures the wrong thing.
The swe-mux primary checkout is the canonical multi-session shared checkout, so `_checkout_session_count` is nearly always greater than one there and nearly every commit is stamped `ambiguous`, even on the path that saw the exact session run `git commit`.

The design conflates two distinct, separately answerable questions, and this phase separates them.
The **committer** is the one session whose process ran `git commit`.
The **contributors** are the one or more sessions whose file writes went into the commit.
Neither is unknowable, and the shared-checkout heuristic throws away attribution mux already holds.

This phase depends on shipped substrate only (Tier 0 `file_write` facts with adapter-boundary content hashes, `git_monitor` commit metadata, the existing `git_provenance` table).
It runs before Phase 7.9 because that phase colors the change map by contributor and leans on the same commit-to-write-fact join.

### Committer attribution: command-anchored, matched to the commit OID

- [x] On an observed `git commit` tool call, attribute the **specific new commit** to the running session by walking `git rev-list <pending_head>..<new_head>` and selecting the commit whose parent chain roots at the head the session started on (`pending.position.head`), rather than reading `HEAD` after the command and trusting the read-back. The exact-OID match is what survives a concurrent sibling commit that the bare read-back mis-correlates. (`read_commit_range` + `select_commit`. **Corrected in build:** the parent-chain rule alone does not settle the race it was written for — when a sibling commits first, the commit rooted at the starting head is *theirs*, not this session's. The selection ladder is therefore: a single commit in the range settles it; otherwise the command's own `-m` subject, which two concurrent commits never share; otherwise the command's time window.)
- [x] Drop the `shared -> ambiguous` downgrade on the command path in `_note_tool_result` (`git_provenance.py`). A commit whose OID was isolated by parent-walk is `exact` regardless of how many sessions share the checkout. Shared-head count no longer influences committer confidence. (`_checkout_session_count` is deleted outright; no path counts sessions.)
- [x] Handle `--amend` explicitly: the amended commit replaces the prior head, so `<pending_head>..<new_head>` is empty. Detect the amend (the classifier already emits `rewrote`) and attribute the amending session directly, taking the diff against the new commit's own parent. (The range is not empty for an amend: the replaced commit stops being an ancestor, so the rewritten one appears in `old..new` and the same ladder selects it. The diff is against the commit's own first parent either way.)
- [x] Reserve `ambiguous` for the genuinely undecidable committer: two observed commit commands from different sessions collapsing to one OID, or a merge/rebase that moves many commits at once. These are rare and named, not the default. (`command_ambiguous` and `monitor_range`, each with its own reader-facing sentence.)

### Contributor attribution: content-anchored to Tier 0 write facts

- [x] Read each commit's changed files and blob hashes at record time (one `git diff-tree` / `git show` read) and match them against recent per-session Tier 0 `file_write` facts on that checkout. The session(s) whose write set appears in the commit are its contributors. ~~The write-side Tier 0 hash is the exact bytes the agent wrote and a committed blob is real file bytes, so this is a legitimate hash equality join~~ — **this was wrong and is corrected in build.** A blob *id* is SHA-1 over a `blob <len>\0` header, not a digest of the bytes, and a write fact hashes whole-file content only for a whole-file write (an edit tool hashes the replacement fragment; a codex patch hashes the envelope). The join is therefore path-and-time anchored, with an *optional* content confirmation that hashes the committed blob's actual bytes with SHA-256 and compares: a confirmed contributor is `exact`, a path-matched one is `correlated`. Blob reads are skipped entirely where no candidate write carries a whole-file hash.
- [x] Record `contributors[]` as a set, not a single author. The shared-index case — session A stages files, session B runs `git commit` sweeping A's staged changes plus B's own — resolves to committer B and contributors {A, B}, and that plural answer is a feature, no git tool records it because git keeps only one configured author. (The set is assembled at the read layer from one durable row per session per commit, each carrying its own `role`, method and matched paths. A denormalized set column would have to be rewritten on every row of a commit each time a later pass discovers one more contributor.)
- [x] Fall back to `ambiguous` only when a commit matches no session's write facts and had no observed command — a human editing in an external editor and committing in the terminal is work mux never observed, and honest ambiguity is correct there. (Expressed as the per-commit `attribution` in the rollup. The occupancy row keeps saying `correlated`, because "this session was in the checkout" stays true whether or not Tier 0 is on, and corrupting it would lose that fact rather than state ambiguity.)

### Data model, backfill, and the observer stance

- [x] Extend the `git_provenance` record and `record_git_provenance` upsert (`src/swe_mux/history.py`, `_GIT_PROVENANCE_UPSERT`) to carry the committer session and the contributor set, keeping the evidence-rank promotion contract so stronger evidence still wins in place. Do not delete or rewrite historical rows destructively. (`role`, `match_method`, `contributed_paths_json`, migrated additively to `observer`. Every new attribution rank outranks every rank written before this phase, so re-attribution promotes a historical row in place instead of being refused. Contributed paths are exempt from rank replacement in one direction: an empty set never erases a populated one.)
- [x] Do not inject a per-session git identity (`GIT_AUTHOR_*`, commit trailers) to make attribution trivial. That mutates the agent's committed bytes and breaks the observer stance (design law 2, `CONTROL_PLANE_ROADMAP.md` §1). Attribution stays observational.
- [x] Rewrite the git-provenance backfill (`src/swe_mux/git_provenance_backfill.py`) to re-derive committer and contributors for existing commits across all projects using the same parent-walk and diff-to-write-fact join, so historical rows are attributed rather than left with the old shared-head `ambiguous`. Bound the pass and make it idempotent, matching the existing backfill's retention and batching. (`--all-projects`; three passes — transcript import, ancestry re-attribution of live command rows, contributor derivation over the newest 500 recorded commits within the Tier 0 window, 400 object reads. The contributor pass calls `git_provenance`'s own matchers, so historical and live answers cannot drift.)

### Live verification across harnesses

- [x] Redeploy the frozen desktop app with the re-attribution and backfill (`uv run python packaging/redeploy_desktop.py`), since git provenance runs in the daemon and a source-only change never reaches the running frozen app.
- [x] Verify live through mux MCP, not by assertion: spawn a session (`request_spawn`, human-approved), have it make one granular commit on the swe-mux checkout while other sessions are live on the same checkout, and confirm the commit records an `exact` committer and the correct contributor while sibling sessions share the head. (Done without a spawn: a live session in the swe-mux Project made commit `2495d99` while three sibling sessions held the same checkout. It recorded `role=committer`, `confidence=exact`, `match_method=command_range`, with its own write of `.docs/development/ROADMAP.md` as the contributed path; the siblings recorded `role=observer` / `monitor_head` and forced no ambiguity.)
- [x] Repeat the test with a second and third session under different harnesses (Claude Code and Codex; avoid opencode, whose tool-call surface differs), including a shared-index case where one session stages and another commits, and confirm committer and contributors are correct in each. Only when all harness cases pass is git provenance considered attributed. (`tests/test_live_git_attribution.py`, gated by `SWEMUX_RUN_LIVE_AUTOMATIONS_TESTS=1`: two real runs of each harness write two files, one commit carries both, and both runs must be attributed their own file. Passing for claude, codex, omp and pi. This is the shared-index case with real evidence and it is repeatable, which a one-off manual spawn is not.)

**The live run changed the design, which is why it was run.** Codex applies patches through its shell/exec tool, so its write *call* classifies as a `command` and only its `patch_apply_end` result carries the written path — a contributor query reading `file_write` facts alone was blind to every codex write, silently. That same result fact carries a hash of the applied file contents, so codex confirms by **content**, not by path as predicted above. The fix reads result facts as content evidence only, never as placement, because every other harness puts a hash of its result *message* there and hash equality is the only thing that separates the two. Measured strengths: claude, codex and pi confirm by content; omp records a relative target with no content hash and is matched by path.

### Phase 7.8 exit criteria

- [x] A commit made by one session on a checkout shared by other live sessions records an `exact` committer, not `ambiguous`. Shared-head count no longer forces ambiguity on any path that identified the commit. (Covered by `test_shared_checkout_still_records_an_exact_committer`, which replaced the test that asserted the retired behavior.)
- [x] Every commit records the contributor session(s) whose Tier 0 writes it contains, including the multi-contributor shared-index case, and `ambiguous` survives only for commits mux never observed the work for. (`test_commit_records_every_contributing_session`; the real-repository join is proved end to end against actual git output in `tests/test_git_attribution.py`.)
- [x] The backfill re-attributes existing `git_provenance` rows across all projects idempotently, and no historical row is destructively lost. (Every write goes through the ranked upsert; the all-projects sweep is asserted idempotent in `test_backfill_attributes_contributors_across_all_projects`.)
- [x] Committer and contributor attribution is confirmed live on the frozen app across Claude Code and Codex sessions on the swe-mux checkout, including a shared-index commit. (Committer on the frozen app with three siblings sharing the head; contributors across all four fact-producing harnesses through the gated live canary, which found and closed the codex blindness the offline tests could not see.)

## Phase 7.9 — Code-structure graph: blast radius, structural context, and the per-session change map

swe-mux captures a behavioral graph (Tier 0 facts, the provenance edges, doc-debt ownership, git provenance) but has no code-structure graph: it knows what agents touched, not how the code connects.
This phase adds a deterministic, always-fresh code-structure graph, maintained off the Tier 0 `file_write` stream, and exposes it as pull-only agent tools and a per-session change map.
Blast radius is the flagship query; structural context, navigation, and test-gap ride the same graph.

The graph is deterministic and model-free, so it costs no tokens and registers as one consumer in the automation registry (`src/swe_mux/automation_registry.py`) requiring `tier0`, gated per-Project through the existing enablement DAG (`design/features/automation-enablement.md`).
It depends on Phase 7.8 because the change map colors nodes by contributor.

### The engine

- [x] Build the code graph with tree-sitter (the `tree-sitter` binding plus `tree-sitter-language-pack`, whose prebuilt grammar wheels avoid per-platform grammar compilation). Do not use LSP: it buys type-accurate rename precision this feature does not need, does not close the dynamic-dispatch recall gap, and imposes a per-language, per-OS, venv-coupled server burden a frozen Windows app should not carry. (`code_graph.py`; Python/TS/TSX/JS with per-grammar capture queries. **Build note:** the tree-sitter 0.26 API replaced `Language.query()` with `Query(lang, src)` + `QueryCursor(q).captures(node)`; TS and TSX name a class with `type_identifier` while JS uses `identifier`, and tree-sitter rejects the whole query if any single pattern is impossible for the grammar, so the class-name pattern cannot be shared across the two.)
- [x] Resolve references to definitions with import-aware name resolution over the tag queries, not bare syntactic name matching, so a same-named symbol in another module is not a false caller. Treat every static reverse-caller set as a lower bound and label the known blind spots (`getattr`, dict dispatch, decorators, DI, dynamic imports). (`resolve_import` reads the real filesystem, so resolution is order-independent; a call resolves only through an actual import or a same-file definition, proven by `test_import_aware_call_not_a_false_edge`. Unresolved references are stored with `resolved=0`, never guessed.)
- [x] Store nodes and edges as tables in `mux.db`, keyed on the same `normalize_target()` path identity the other consumers use, so the graph joins cleanly with provenance, doc-debt, and git facts. Nodes are file-level by default with symbol detail resolved on demand; edges are `imports`, `calls`, `references`, `defines`. (`code_graph_files`, `code_graph_symbols`, `code_graph_edges`; `CodeGraphStore` mirrors the `Tier0Store` single-writer pattern on the shared WAL.)
- [x] Maintain the graph incrementally off the normalized event stream that already feeds `tier0_facts`: on a `file_write` fact, re-parse that one file and update its edges. No separate file watcher and no full rebuild — mux already observes every edit with a race-free content hash, which is the freshness advantage a standalone index lacks. (`maintain_files` re-parses per turn-boundary off the run's `file_write` facts, parse-if-stale by content hash. **Correction in build:** a reverse-dependency query needs the *importers* in the graph, and an importer the session never edited is invisible, so a **one-time bounded seed index** (`index_project`, at most once per Project per process, on a worker thread) parses the existing tree once as the baseline the incremental updates then maintain. This is not the rejected per-edit rebuild.)
- [x] Answer the reverse-dependency query with a bounded SQLite recursive CTE (who imports and who calls, N hops), and back it with a git co-change net mined from `git_provenance` / `history` as the recall safety net for the dynamic edges tree-sitter misses. The co-change net is required, not decorative. (`reverse_dependents` CTE; `co_change_net` groups git-provenance contributor rows by commit.)
- [x] Package the tree-sitter grammar binaries into the frozen bundle: the PyInstaller spec must include the grammar shared libraries as data or the frozen app parses nothing. This is a named acceptance check, not an afterthought. (`packaging/swe_mux.spec` `collect_all` now covers `tree_sitter` and `tree_sitter_language_pack`; `parsing_available()` is the runtime acceptance check. Verified in-tree; frozen-bundle load is confirmed at redeploy, held for after code review.)

### Surface 1 — agent pull tools (no push)

- [x] Expose the graph as pull-only mux MCP tools, each gated on the consumer's per-Project opt-in and returning a disabled note when off, mirroring the `provenance` / `dead_ends` pattern (`design/features/mux-mcp.md`). No notification is ever injected into an agent; the agent consults the tools on its own initiative. (Six tools gated on `code_graph`; off answers `disabled`, no store answers `unsupported`, never a fake empty — `tests/test_mcp_code_graph.py`.)
- [x] `blast_radius(file_or_symbol)` returns the reverse callers, co-changed files, covering tests, and owning docs, hop-ordered, token-budgeted, with static results labeled a lower bound and blind spots named.
- [x] Navigation tools (`find_definition`, `find_callers`, `find_references`) return the precise structural neighborhood instead of the agent reconstructing it by grep, which is the token-efficiency win — it removes the expensive who-calls-this traversals, not the cheap exact-string greps. (`find_callers` reports unresolved same-name callers separately, so the lower bound is visible.)
- [x] `code_context(files_or_task)` returns a ranked, compact structural neighborhood (key symbol signatures and the specific callers, not whole files) for context packing.
- [x] A test-gap read intersects the reachable set with covering tests, surfacing changed code whose blast radius contains no test.

### Surface 2 — human passive annotations

- [x] A `turn_ended` detector emits a blast-radius annotation for an edit with large or unexamined reach, written as an annotation only, never a PTY write, feeding the Phase 6.5 attention channels which decide whether it interrupts. These annotations render in the Phase 7.10 Findings pane, which is the human surface for this signal; Phase 7.10 ships first for that reason. (`_blast_radius` fires at `BLAST_MIN_REACH`, deduped to one row per edited file per run.)
- [x] Compute the mux-unique "callers edited but not examined" signal by intersecting an edited symbol's reverse callers with the session's own Tier 0 `file_read` facts. This flags callers whose behavior the session may have broken without opening them, a signal no standalone code-graph tool can produce because none observe the agent's reads. (`_unexamined_callers`.)

### Surface 3 — the per-session change map

- [x] Render a per-session diff graph: red for this session's edits, yellow for their blast radius, blue for immediate context. Red nodes come from `file_write` facts filtered by `session_id`, so concurrent other-session edits are excluded by construction, and the baseline is the git head captured when the session started. (`GET /api/sessions/{sid}/change-map`; the non-unified view reads one run's facts, so concurrent edits are excluded by construction — `tests/test_change_map_endpoint.py`.)
- [x] Compute each view server-side and ship only the bounded subgraph the view needs (changed nodes plus blast radius plus one hop), never the whole codebase graph, with symbol detail expanded on demand. Frontend performance is then independent of codebase size, and there is no path to lag on a large codebase. (`CodeGraphStore.subgraph`.)
- [x] Render with a WebGL graph renderer (Sigma.js with graphology), running the ForceAtlas2 layout in a Web Worker so layout never blocks the UI thread. Match the app's existing WebGL usage (the xterm WebGL addon) and its self-contained, CSP-safe serving; use no external host. (`ChangeMapPane.tsx` + a bundled Vite **module** worker `changeMapLayout.worker.ts`. **Build note:** the app CSP is `script-src 'self' 'wasm-unsafe-eval'` with no `worker-src`/`blob:`, which blocks graphology/Sigma's stock blob-URL worker helper; a bundled module worker is same-origin and allowed. Mobile falls back off WebGL, the same pixel-ratio hazard the xterm renderer avoids on mobile.)
- [x] Provide a unify-from-baseline toggle that switches from the session-scoped view to the union of all sessions' edits since a chosen commit, coloring each session's changes a distinct hue, which is the multi-session and multi-worktree change map the fact attribution makes possible. (`?unify=true`; seed nodes carry `sessions[]` and a per-session hue, with a legend.)

### Additional derivations (same substrate)

- [x] Dead-code and orphan detection over nodes with no inbound references, guarded against entry points and dynamic callers. (`orphans`; the annotation names that an entry point or dynamic caller is a false positive, since static reachability cannot see them.)
- [x] Import-cycle and god-node (high fan-in) detection, surfaced as ordinary annotations. (`import_cycles` bounded-DFS, `god_nodes` at `GOD_NODE_MIN_FAN_IN`.)
- [x] A doc-debt precision upgrade: refine which docs an edit affects by dependency reach, not only direct ownership, feeding the existing doc-debt ledger. (Optional `dependents` map on `detect_doc_debt`/`build_doc_debt_map`, supplied by the graph when `code_graph` is enabled; a doc owning a dependent of a changed file also owes an update. Additive — off when the graph is absent.)

### Phase 7.9 exit criteria

- [x] The code graph is built with tree-sitter and no LSP, keyed on `normalize_target`, kept fresh incrementally off the Tier 0 `file_write` stream, and its grammar binaries load in the frozen app. (Grammar-load in the frozen app is confirmed at redeploy, held for after code review.)
- [x] `blast_radius` and the navigation, context, and test-gap tools are pull-only mux MCP tools, per-Project gated, token-budgeted, and return empty rather than a low-confidence guess. No signal is pushed into an agent.
- [x] The per-session change map renders red/yellow/blue from `session_id`-attributed facts, excludes concurrent sessions by construction, ships only bounded server-side subgraphs, renders in WebGL with worker-side layout, and does not lag on a large codebase. (Server-side bounding and exclusion are unit-proven; the WebGL "does not lag on a large codebase" property is a live-UI check held for after code review.)
- [x] The "callers edited but not examined" signal is produced from reverse callers intersected with the session's `file_read` facts, and static results are labeled a lower bound with blind spots named throughout.

Held for after code review, by the user's instruction — the branch is not landed or redeployed until then:

- [ ] Redeploy the frozen desktop app and confirm the tree-sitter grammar binaries load there (`parsing_available()` true in the bundle) so the graph is not silently empty on the frozen app.
- [ ] Verify live through mux MCP that `blast_radius` and the navigation tools return real structure for the swe-mux checkout, and that the per-session change map renders without lag on this codebase.

## Phase 7.10 — Findings surface: annotation filters, the doc_debt tool, and the Insight tab

The deterministic consumers (Phase 3.7) already produce findings as `automation_annotations` — loop, declared-vs-verified, doc-debt, provenance — but nothing surfaces them scoped and readable to the human, and only some are reachable by an agent.
This phase exposes those findings two ways: a filtered read for the human Findings pane, and a `doc_debt` mux MCP tool for agents.
It depends only on shipped substrate (Phase 3.7 deterministic consumers, Phase 7.5 mux MCP), is independent of Phase 7.8 and Phase 7.9, and ships before Phase 7.9 because Phase 7.9's human-passive blast-radius annotations render in the pane this phase builds.

### Backend — annotation filters

- [x] Extend the existing `GET /api/annotations` (`server.py`, `list_annotations`), do not add a parallel `/api/automation/annotations`. Add `project_id`, `session_id`, and `since` alongside the current `tag`, `agent_run_id`, and `limit`. A second near-identical endpoint would fork the read surface.
- [x] Extend `AutomationStore.annotations()` (`automation_store.py`) to support `since` and a session filter. `session_id` is not a column: annotations are anchored to `agent_run_id` (nullable) and `project_id`, so a session filter resolves the session's run ids and matches that set. A project-anchored finding with a null run (doc-debt, provenance) is therefore structurally absent from session scope — that is correct behavior, not a gap, and it is what the exclusion notice below exists to explain.
- [x] Add `tag_counts` to the response: per-tag totals within the current scope (project/session/since honored, the tag chip ignored), so "no findings" is distinguishable from "buried under provenance edges" and the chips show true in-scope counts.
- [x] Leave the dashboard payload's `recent_annotations` key untouched for compatibility; point the new UI at the extended endpoint.

### Backend — the doc_debt mux MCP tool

- [x] Add `doc_debt` as the 21st mux MCP tool, same shape as `prior_resolutions`: Project-scoped read, a `project` argument, gated on the `doc_debt` automation, returning empty when unpermitted rather than a fake result (`design/features/mux-mcp.md`).
- [x] Return `{doc, changed_files}` pairs an agent can act on, re-derived from `build_doc_ownership` inverted to `doc -> changed files` over the project's changed-file facts. Do not scrape the annotation: `DocDebtFinding.content` is a human sentence and `.dirty`/`.changed` are two flat lists, not the per-doc mapping. Re-deriving from substrate matches how `provenance` and `prior_resolutions` already work.
- [x] State the known blind spot in the tool description: a file no doc lists in a `Key files` section produces no debt, so an empty result is not proof the docs are current.
- [x] Add no generic `read_annotations` table-dump tool. Every mux MCP tool stays a question, not a table.

### Frontend — the Insight tab

- [x] Replace the `timeline` drawer tab with an `insight` tab holding a segmented control: Timeline and Findings. The Timeline pane is unchanged (`frontend/src/ScanTimelineTab.tsx`, `frontend/src/UtilityDrawer.tsx`).
- [x] Preserve or migrate the persisted tab id so saved workspaces do not lose the tab (`technical/frontend/workspace-state.md`).

### Frontend — the Findings pane

- [x] Scope toggle: this session and this Project, defaulting to session.
- [x] Always state what the current scope excludes — project-scoped tags hidden in session scope, and the inverse — so silence reads as scope, not as absence. This is the "off vs quiet" rule and it is required, not optional, because doc-debt and provenance are invisible in session scope by construction.
- [x] Tag filter chips driven by `tag_counts`, with provenance off by default given its volume.
- [x] Rows show tag, content, timestamp, provenance (`deterministic` vs model), and the run id when run-scoped.
- [x] Read-only: no dismiss and no mutation, keeping the pane out of the actuation gate.
- [x] A footer button opens the full Automation dashboard, mirroring the Timeline pane's Project-settings button.

### Docs, tests, and ship

- [x] Update `design/features/deterministic-consumers.md` (where findings surface and the two scopes), `design/features/mux-mcp.md` and `design/interfaces.md` (the new tool and the extended endpoint), and `technical/frontend/packages.md` (the Insight tab's two panes and their boundary).
- [x] Backend tests: the new filter predicates including the session run-set resolution, `tag_counts` scoping, and the `doc_debt` tool including the empty and unpermitted cases.
- [x] Frontend contract tests: the scope toggle, the exclusion notice, the dashboard link, and read-only (no mutation calls).
- [ ] Verify on the isolated daemon (findings visible in both scopes, the `doc_debt` tool called from a live agent), then commit and redeploy.

### Deferred

- [ ] Dismissal / acknowledged state for a finding.
- [ ] An undocumented-file detector for source paths absent from every `Key files` section — the mirror of doc-debt, and the completeness half of the same "off vs quiet" rule.

### Phase 7.10 exit criteria

- [ ] `GET /api/annotations` serves findings filtered by tag, project, session, run, and `since`, with `tag_counts` in scope, and the store resolves a session filter through its run ids without a session column.
- [ ] The `doc_debt` mux MCP tool returns re-derived `{doc, changed_files}` pairs, is gated on the `doc_debt` automation, returns empty rather than a guess when unpermitted, and names its blind spot.
- [ ] The Insight tab exposes Timeline and Findings without losing a saved-workspace tab, and the Findings pane is read-only, scope-toggled, and always states what the scope excludes.
- [ ] The surface is verified on the isolated daemon in both scopes and from a live agent before redeploy.

## Phase 8 - Telegram multi-session control (descoped)

**Descoped 2026-08-10 to a decision-gated capability.** The phase number is kept so later
phases are not renumbered; nothing here is scheduled work.

What retired it is what shipped around it.
Outbound alerting to a phone is web push with device-presence routing.
Inbound control is the mobile browser UI over the tailnet, which is first-class and reaches
every operation rather than a chat-shaped subset.
What Telegram would still add is replying and approving from a chat app without opening the UI,
which is a convenience with a real cost: a bot token to store, a poller or webhook to own,
chat/message/thread/callback mappings to persist, and a second confirmation surface to keep
prompt-injection-safe.

Preconditions for reopening it, so the decision is evidence-driven: a repeated, recorded case of
wanting to answer a session from a phone where opening the UI was genuinely not workable.
Should it ever be built, the constraints are unchanged: one daemon-owned adapter per bot token
and never a poller per session, replies bound to their originating run and refused with a
re-pick when that run was superseded, every incoming prompt through the Phase 5 queue and
readiness policy, and bot secrets kept out of config exports, logs, and audit records.

### Phase 8 exit criteria

## Phase 9 - SSH and native terminal attach (descoped)

**Descoped 2026-08-10 to a decision-gated capability.** The phase number is kept so later
phases are not renumbered; nothing here is scheduled work.

"SSH" named two unrelated things and only one of them was ever this phase.
SSH *outbound*, what a user does by typing `ssh` in a pane, is owned by Phase 5.8.
SSH *inbound* as a transport to reach mux is shipped behavior whose documentation is also in
Phase 5.8.
What was left here is `mux attach`, a native-terminal client for driving a session without a
browser.

Why it is not scheduled: the browser reaches every session from anywhere on the tailnet, the
mobile surfaces are first-class, and voice control covers the hands-free case, so `mux attach`
serves the narrow case of an SSH login with no browser available.
Against that, it is expensive and structurally risky: it must be a second consumer of the
supervisor contract and its input arbitration, it must survive a daemon restart the way a
browser client does, and it must route every action through the Phase 7 typed operations or it
becomes exactly the parallel ownership implementation those operations exist to prevent.

Constraints that hold if it is ever built: no dependency on any SSH multiplexing primitive,
since Win32 OpenSSH provides no `ControlMaster`/`ControlPath` and there is no native Windows
mosh client, so a shared master socket or roaming UDP transport does not port to the proving
platform; SSH-adjacent runtime state owned at daemon or supervisor lifetime and never at a
browser client's; destinations resolved through `ssh -G` with a mux-stored field treated as an
override only when explicitly non-default; and SSH transport authentication, Tailscale
admission, and mux session lifetime kept as three separate concepts, since an SSH-admitted peer
has the same terminal and code-execution authority as a tailnet peer and mux still has no login
of its own.

Neither axis makes a remote host an execution host. That remains separately decision-gated.

### Prior art considered

Two reference implementations were reviewed before scoping this phase and neither is adopted
wholesale. cmux exposes SSH as a first-class workspace command that reads `~/.ssh/config`,
multiplexes through one ControlMaster socket, injects keepalives by default, delegates remote
persistence to tmux on the remote host, and lets remote processes call back to raise local
notifications; its rejection of a deployed remote-server model on trust and maintenance
grounds is the reasoning this roadmap follows. orca takes the opposite path, treating SSH
hosts as execution hosts behind a deployed relay, and its incident record is the cost
estimate: head-of-line blocking between bulk transfers and keystroke echo on a shared channel,
orphaned port forwards from mixing window and process lifetimes, and a standing project-wide
rule that no change may assume local-only execution. The keepalive defaults, the
`~/.ssh/config`-as-truth rule, and the lifetime-ownership constraint are taken from these; the
relay, the multiplexed transport, and the remote provider stack are not.

Acceptance, if it is ever reopened: an SSH disconnect leaves the mux session live and a later
attach restores interaction without changing browser replay semantics, and `mux attach` shares
one input-ownership implementation with the browser, proven by a test that drives both clients
against one session.

## Phase 10 — WSL agent bridge and native Linux/macOS

This phase carries forward original Roadmap Phase 11. Platform expansion preserves the
same API, browser behavior, session identity, attach/detach, evidence, and daemon-owned
child-lifecycle contracts.

`CROSS_PLATFORM_FINDINGS.md` holds the inventory this phase sequences: where native code is
concentrated versus where platform behavior is merely distributed, the current non-Windows
import and startup blockers, the Windows-host shell compatibility matrix (PowerShell 7, 5.1,
profile interference, CMD, WSL, Git Bash), the required platform interfaces, and the target
order.
Read it before scoping any item here rather than re-deriving it.

### WSL agent bridge

- [ ] Build a distro-side bridge for native WSL Claude/Codex executable discovery,
  promotion/demotion, hook-secret delivery/execution, transcripts, and native-id
  correlation. Windows interop commands alone do not qualify.
- [ ] Translate Project, transcript, clipboard-media, preview/listener, instruction, and
  process ownership paths without leaking Windows-only paths or trusting guest listeners.
- [ ] Keep WSL profiles labelled `agent-bridge-unavailable` until native agents and
  promotion/state/history tests match Windows contracts.

### Platform interfaces

- [x] Introduce `PtyHost` implementations for Windows ConPTY/pywinpty and Linux/macOS POSIX
  PTY through `forkpty`/`openpty` or a vetted equivalent.
  (`pty_backend.py` holds the `PtyProcess` contract and the unified `PtyError`;
  `pty_backend_windows.py` and `pty_backend_posix.py` implement it. `pty_host.py` keeps the
  half that is genuinely shared - reader thread, poll ladder, coalescing, backpressure
  handoff, resize/exit-status/release - so there is one buffering implementation rather than
  one per target. The POSIX side uses `pty.fork` specifically for the controlling terminal:
  without `TIOCSCTTY` in the right order the child silently loses Ctrl+C, SIGWINCH, and job
  control while still passing an `isatty` check.)
- [x] Introduce lifecycle/reapers: retain Windows Job Objects; on POSIX, a per-session
  guardian owns the process group and daemon pipe, then performs graceful signal, bounded
  wait, and group SIGKILL after daemon loss.
  (`process_reaper.py` is the contract; `win_jobobj.ReaperJob` and
  `posix_process_group.ProcessGroupReaper` implement it, and `posix_guardian.py` is the
  separate process that covers the case a process group cannot: the daemon dying without
  asking. EOF on the daemon's pipe is the trigger, chosen because a crashed daemon cannot
  decline to close its descriptors - unlike a heartbeat, there is no failure mode where the
  daemon dies and the guardian keeps waiting. An explicit `release` exits without killing,
  which is the POSIX half of the session-preserving restart contract.
  **The safety property that needed enforcing:** `assign()` refuses a pid whose process
  group is the daemon's own. That happens when a child was started without `setsid`, and
  owning it would make session cleanup signal the daemon, the supervisor, and every sibling
  session - turning a cleanup bug into a whole-app kill.)
- [ ] Add a cross-platform process-inspection boundary for descendants, resources,
  signals/termination, anomaly evidence, and listener ownership.
- [x] Add OS reveal services: Explorer, macOS `open`, and Linux `xdg-open`.
  (Already shipped in `file_manager.file_manager_command`, which covers all three and takes
  the platform as an argument; the Win32 window-focus helper it sits beside imports
  `wintypes` lazily, so nothing here blocked a non-Windows import.)
- [ ] Generate agent-promotion launchers per OS with safe structured argv/env/hook-secret
  propagation.
- [x] Replace lowercased path comparisons with platform-aware same-file normalization for
  spaces, Unicode, symlinks, case sensitivity, UNC, and WSL paths.
  (`path_identity.py`: `os.path.samefile` when both paths exist - which answers symlinks,
  junctions, mapped drives, bind mounts and per-directory case sensitivity at once - then a
  textual fallback with NFC normalization and the right case rule, refined by a read-only
  per-directory case-sensitivity probe. Containment is component-wise, because a string
  prefix reports `project-old` as inside `project`. Migrated: the rollover cwd comparison,
  the CLI-state cwd grouping key, and the Claude per-project MCP table match.
  **Known gap, deliberately not changed here:** `deterministic_consumers.normalize_target`
  still casefolds. It is the storage key for the code graph, doc-debt ownership and Tier 0
  targets, so changing it rewrites existing `mux.db` keys; it needs a migration, not an
  edit, and is tracked in the native-rollout item below.)
- [ ] Make Project root and `.swe-mux/` resolution platform-aware across Git worktrees,
  non-repository cwd, symlinks, UNC, and WSL translation.
- [x] Guard platform imports so config, CLI, package import, and non-PTY tests work on all
  targets. Adapt data directories, executable/transcript discovery, hook `run`, reveal,
  config migration, voice-capability documentation, and instruction-file resolution per
  platform.
  (Import parity is proven rather than asserted: on a native-ext4 Ubuntu 24.04 checkout the
  suite went from **1211 collected with 87 collection errors** to **2874 passing, 0
  failures**. The blockers removed: the unconditional `import winpty` in `pty_host`, the
  unconditional `from PIL import ...` in `project_files` (Pillow is now a real dependency on
  every target, since image presentation is not a Windows feature, with its absence handled
  as a degraded capability), the unconditional `ReaperJob()` in `server.create_app`, and
  `from ctypes import wintypes` at `secret_store` module scope - now isolated in
  `secret_cipher_windows.py`. Data-directory and instruction-file placement are Stage 7 and
  remain open.)

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
The packaging and external-trial readiness gaps, and the CI matrices, are inventoried in
`CROSS_PLATFORM_FINDINGS.md`.

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
- [ ] Resolve the preview-capture Chromium assumption (`CONTROL_PLANE_ROADMAP.md` §9 known
  gaps): a clean-machine build needs Chromium bundled or a first-run `playwright install`,
  otherwise screenshot capture is silently unavailable on a fresh install.
- [ ] Make every first-use asset download explicit rather than silent, and neutralize
  workflow-specific defaults, so a fresh install matches its documented capabilities: the STT
  Whisper model and Silero VAD assets download on first Talk (default STT off or gate it), and
  the voice/language defaults are locale-neutral rather than one operator's choice
  (`NEW_USER_RELEASE_READINESS.md` owns the inventory).

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
- Autonomous agent-to-agent routing beyond Phase 5 user-authored/user-approved/`mux.notify`
  messages and bounded deterministic templates.
- ~~Agent-held spawn authority.~~ **Decided and implemented (2026-08-16).** Phase 5 shipped
  `mux.requestSpawn` as a draft producer only; the Phase 7.6 per-Project authority grant
  became its deciding vehicle, and the grant now covers spawn. A Project's `spawn_grant`
  (`draft` default, `granted`) lets an agent create a session in it directly, gated by the
  `session_control` automation, capped by `agent_spawn_hourly_budget`, audited, and target-
  Project-scoped like interrupt/end. The default stays the inert draft, so this is opt-in per
  Project (`design/features/mux-mcp.md`, ROADMAP Phase 7.6).
- Automatic termination of suspected orphan processes. Agent-initiated termination of a
  *session* is a different question and is scheduled in Phase 7.6; this entry remains about
  the daemon acting on processes it merely suspects are orphaned.
- **Telegram control (descoped from Phase 8, 2026-08-10).** Web push covers outbound alerting
  and the mobile browser covers inbound control, so a chat adapter would add a bot token, a
  poller or webhook, persistent chat-to-run mappings, and a second injection-safe confirmation
  surface to buy "reply without opening the UI". Reopen on a recorded pattern of needing it.
- **`mux attach`, a native terminal client (descoped from Phase 9, 2026-08-10).** The browser
  reaches every session over the tailnet and voice covers hands-free, leaving the narrow case of
  an SSH login with no browser; against that it must become a second consumer of the supervisor
  contract and its input arbitration. Full constraints are retained in the Phase 9 section.
- swe-mux as a multi-host control plane: SSH hosts as execution hosts, a remote filesystem
  provider, host-scoped Git with per-host capability caching, remote port-forward management,
  or a deployed remote relay or agent bridge. Phases 5.8 and 9 deliberately stop at the
  terminal and the attach client. A reviewed reference implementation of this scope runs to
  roughly a hundred modules with its own relay deployment, versioning, and flow control, and
  it forces a standing "assume no local-only execution" rule on every unrelated change. Note
  the cheaper alternative before scheduling this one: a remote host already on the tailnet can
  reach the daemon's HTTP surface directly, so remote-agent visibility may not need an SSH
  bridge at all.
- Definitive identity attribution for shared-account quota usage.
- Automatic/background bidirectional instruction sync, or any mux-owned cross-harness skill
  normalization. Phase 6's explicit, previewed, one-time instruction-file overwrite is the
  approved boundary; widening it to continuous reconciliation requires a new product decision,
  and the skill half was culled outright because harnesses now cross-import skill directories
  by convention.
- Native harness theme management, ANSI rewriting, provider-native Remote Control,
  concurrent provider homes, automatic quota failover, and public Funnel/LAN exposure.
- A plugin system: third-party panes, contributed actions, link handlers, and packaging
  identity layered over the shipped meta-hooks, automation, and project-actions substrate.
  `PLUGIN_SYSTEM_FINDINGS.md` records what is genuinely missing, the constraints any design
  must accept (subprocess only, plugin panes over the existing supervisor `spawn` message so no
  `PROTOCOL_VERSION` bump reaps every session, no second event-to-action path), and the value
  ranking if it is ever picked up.

Resolved out of this list, recorded so neither is re-proposed as gated:

- **Daemon-hosted STT.** The browser-STT limitation was demonstrated and the daemon-owned
  faster-whisper path shipped with the voice work.
- **Live session restore after daemon restart.** Shipped as the PTY supervisor split, which
  is stronger than the gated idea: sessions stay alive across the restart rather than being
  reconstructed after it.

## Original-roadmap carry-forward map

| Original roadmap item | Roadmap v2 destination |
|---|---|
| Phase 8 practical CLI | Phase 7 Practical CLI control |
| Phase 8 `mux doctor` | Phase 7 Consolidated diagnostics |
| Phase 8 Windows tests/CI/soak | Phases 1 and 7 |
| Phase 9 Telegram | Phase 8, descoped 2026-08-10 to decision-gated |
| Phase 10 SSH/native attach | Phase 5.8 (outbound + inbound docs); `mux attach` descoped 2026-08-10 to decision-gated |
| Phase 10 OpenSSH forwarding documentation | Phase 5.8 |
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
