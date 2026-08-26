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
                                  -> Phase 7.7  Behavioral-summary consolidation + scan-timeline consumers [done]
                                    -> Phase 7.8  Git provenance re-attribution: committer + contributors [done]
                                      -> Phase 7.9  Code-structure graph: blast radius + per-session change map [done]
                                      -> Phase 7.10 Findings surface: annotation filters + doc_debt tool + Insight tab [done]
                                      -> Phase 7.11 Scan timeline as an agent-readable surface + run-level field continuity [built, not landed]
                                      -> Phase 7.12 Code-analysis expansion: conflict prediction + entity change facts + quality deltas [open]
                                        -> Phase 8  Telegram control            [descoped to decision-gated]
                                        -> Phase 9  SSH/native attach           [descoped to decision-gated]
                                          -> Phase 10  WSL bridge + Linux/macOS [Windows+Linux done; macOS unproven]
                                            -> Phase 10.5 Distribution licensing + voice-stack replacement [done 2026-08-24; redeploy outstanding]
                                              -> Phase 10.6 Mux assistant: conversational fleet operation [done+deployed 2026-08-18]
                                                -> Phase 11  Public packaging and release  [open]

Phase 14  Land queue: serialized branch landing   [built, not landed; live run outstanding]
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
| `HARNESS_EXPANSION_CANDIDATES.md` | research | Feeds Phase 12. Holds the per-candidate parity study for the agent CLIs not yet in the registry: what each one can give the declared capability axes, which registry gates it clears, and which candidates are rejected and why. Phase 12 sequences the work; this document holds the evidence behind each descriptor. |
| `PERFORMANCE_RUNBOOK.md`, `STATUS_INCIDENT_RUNBOOK.md`, `TERMINAL_INPUT_INCIDENT_RUNBOOK.md` | operational | Investigation procedures for shipped subsystems, not planned work. |
| `CONTINUITY_TOUCH_KEYBOARD_ASK.md` | open ask against a vendored dependency | Blocked on the note editor upstream, not on a phase. |
| `USABILITY_AUDIT_2026-08-20.md` | audit report, findings open | The deliverable of Phase 15's "Global usability audit session". Twelve ranked first-use and overwhelm findings, each anchored to a file and line, split into quick polish and needs-design. Nothing in it is scheduled; a maintainer decides which findings earn work. |

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
  cancelled rather than delivered late. Recurring/schedule-driven *sends* remain out of scope:
  a queue item is still delivered once and never re-armed.
  **Recurrence exists one layer up instead, as scheduled runs** (`design/features/scheduled-runs.md`):
  a schedule starts a *session* on a cron/interval/one-off trigger and stages its follow-up
  messages as ordinary queue items with these same constraints. That is deliberately not a
  repeating queue item - the recurring thing is the conversation, and a message that re-armed
  itself would have no run to bind to.

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
  *Superseded 2026-08-19*: the enablement-vs-configuration line proved invisible to users and inconsistent with itself (the scheduled-runs switch already lived in Settings), so the two global switches moved to Settings → Automation, the rules.toml editor moved to the dashboard, and the dashboard gained the read-only per-Project enablement matrix. The no-duplication half of this item still holds: each switch has exactly one owner (`design/features/ui.md`, `design/features/automation.md`).

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

**Status: landed and running on the frozen app (redeployed 2026-08-17).** The turn summarizer is retired and the
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

**Live-verification note (closed 2026-08-23, and it found a defect):** the adaptive-titler synthesis
call and the phase-transition signals exercise the real OpenRouter scan producer, which an isolated
test daemon cannot reach; the in-tree suite proves the pivot gate, the derivations, the endpoints
(gating + attribution), the summarizer retirement, and the config-load tolerance deterministically.
The deferred live check was the only thing that could have caught what shipped: `TITLE_SCHEMA`
declared `confidence` without requiring it, which strict-mode structured outputs reject outright, so
every synthesis call the titler ever made returned HTTP 400 and it re-titled nothing. Measured on the
primary host before the fix: 111 scan records across 7 runs, 14 pivots correctly detected and
escalated, 14 rejected calls, 0 re-titles, and no spend row at all - a rejected call bills nothing,
so the feature was invisible in the spend table rather than visibly broken. The pivot gate itself
behaved exactly as specified throughout. Fixed with the schema, a source-scanning strict-schema guard
(`tests/test_llm_schemas.py`), failure-path diagnostics parity for the observer row, and retention of
the provider's explanation on HTTP 400. Semantic re-title *quality* remains to be observed live now
that the call succeeds.

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

Landed and redeployed 2026-08-17; both verifications are recorded below.

- [x] Redeploy the frozen desktop app and confirm the tree-sitter grammar binaries load there (`parsing_available()` true in the bundle) so the graph is not silently empty on the frozen app.
  (Measured against the running frozen app, not the source tree, because that is the only claim
  worth making: the bundle re-parsed this checkout at 11:09 on 2026-08-17 - after the 09:53
  rebuild - producing 760 files, 13,507 symbols and 83,575 edges across **four** grammars
  (python 351, typescript 306, tsx 87, javascript 16). A bundle whose grammars failed to load
  parses nothing and writes nothing, so populated tables dated after the relaunch *are* the
  proof; `parsing_available()` returning true in a source venv would have proved nothing about
  the bundle. Read the graph tables rather than a status endpoint, because there is no endpoint
  that reports parser health - a gap worth closing, since the failure mode is silence.)
- [x] Verify live through mux MCP that `blast_radius` and the navigation tools return real structure for the swe-mux checkout, and that the per-session change map renders without lag on this codebase.
  (Operator-confirmed for the MCP tool calls and the change-map render. Independently corroborated
  on the substrate those tools read: the inbound edge set for `wsl_bridge.py` resolves to
  `profiles.py`, `server.py`, `tailscale.py` and its own tests with `imports`/`calls`/`defines`
  distinguished, and symbol lookup finds `_run_wsl` and `_reap_wsl_tree` - functions committed the
  same day - so the graph is both structurally real and incrementally fresh.)

**One defect this verification exposed, not yet fixed:** the graph indexes
`src/swe_mux/static/assets/*.js`, which is gitignored minified build output. Three bundles
account for 5,511 of the 5,662 javascript symbols (two of them truncated), including the
vendored ONNX runtime. Minified identifiers are noise in symbol lookup and inflate blast
radius, so build output should be excluded from graph ingestion the way it is from git.

### Phase 7.9 follow-up — the change map made readable and navigable

Three changes to the Surface 3 pane and its endpoint, prompted by scratchpad scripts appearing
on the map as isolated dots. Details in `design/features/code-graph.md`; the endpoint contract is
in `design/interfaces.md`.

- [x] **Seeds obey the graph's own admission rules.** The endpoint filtered writes on file
  extension alone while the engine additionally requires a path inside the checkout and outside
  generated/vendored/hidden directories — so the map drew seeds the graph is structurally
  incapable of ever linking. The drop is counted per distinct file and stated
  (`excluded: {outside_root, unindexable}`, `empty_reason: "excluded"`), because a file the reader
  knows they changed must not vanish silently. (`tests/test_change_map_endpoint.py`)
- [x] **Unify re-anchors against each session's own checkout.** Sibling worktree sessions record
  absolute paths under their own root, which the requesting session's root cannot strip; without
  this the new filter would have dropped an entire session from the unified map. Candidate roots
  are the requesting session's followed by each contributing session's `project_root` — no git
  call on the hot path. (`tests/test_change_map_endpoint.py`)
- [x] **Nodes carry an openable path.** Graph identities are casefolded and are not filesystem
  paths: a case-sensitive host cannot open one, and a case-insensitive one opens it under a
  second colliding pane identity. `resolve_display_paths` recovers real casing by directory
  listing (never `stat`, which succeeds on the wrong case on Windows), and `worktree` names the
  checkout it is relative to. A vanished file carries no `display_path` and the pane's button is
  disabled rather than dead. (`tests/test_code_graph.py`,
  `tests/test_change_map_endpoint.py`)
- [x] **Hover and selection highlight a node's neighbourhood**, applied through Sigma's per-frame
  reducers with the state in refs, so a hover is one repaint rather than a Preact render that
  would re-seed the graph on every pointer move. The mobile list, which has no picture, spells the
  neighbours out instead. (`frontend/test/changeMap.test.ts`,
  `frontend/test/renderer/change-map-layout.spec.ts`, which drives the real canvas and reads back
  Sigma's resolved display data.)

One incidental fix: `vite.config.ts` now pre-bundles `sigma`, `graphology`, and
`graphology-layout-forceatlas2`. The layout worker's dependency was discovered at runtime, and
vite answers a newly discovered dependency with a full page reload that lands mid-test and reads
as an unrelated random failure across the renderer suite.

### Phase 7.9 follow-up 2 — the change map works in a worktree

Live measurement after the first follow-up: of twelve running sessions, four were in linked
worktrees, and **every one of them reported `project_root: D:\PROJECTS\swe-mux`** while
`git.root` pointed at `.claude/worktrees/<name>`. Their maps read `unindexable: 24`, `7`, `9`,
`2` — the whole session's work refused — and one read `no_edits` outright. Details in
`design/features/code-graph.md`; the endpoint contract is in `design/interfaces.md`.

- [x] **The endpoint asks where the session is actually working.** `record.git.root` and
  `record.git.worktree` are already resolved by the git monitor (`rev-parse --show-toplevel`, plus
  `--git-dir` ≠ `--git-common-dir` for linked-worktree identity); the map now reads them instead of
  the Project root, which is merely where the Project was *registered*. Re-anchoring is gated on
  `git worktree list` for this Project's repository, TTL-cached, so a **nested** repository inside
  a Project keeps its own identity rather than being merged into this one.
  Candidate roots are ranked deepest-first, because a worktree lives inside the Project root and
  stripping the outer root does yield a relative path — the useless one.
  (`tests/test_change_map_endpoint.py`, which builds real git worktrees.)
- [x] **Three scopes, defaulting to the branch in a worktree.** `session` unions this run's write
  facts with the session's landed paths from the git provenance ledger; `branch` seeds from
  `git_review.branch_changed_paths` (working tree vs merge base, plus untracked); `project` is the
  former `unify`, still accepted as an alias. Offerability is decided from the `compare_ref` the
  monitor already cached, so the branch diff runs only when it is served, and a `branch` request
  with no base falls back to `session` and says so rather than drawing an empty branch.
- [x] **Landed work survives.** Tier 0 facts expire on a six-hour window *and* on a conversation
  rollover, which is why a session read "no source edits yet" hours after its branch merged.
  Provenance rows do not expire, so a merged session's map is now built from its commits.
- [x] **A branch-only file is drawn and marked.** It has no node in the canonical graph — that is
  built from the primary checkout — so `indexed: false` says "not indexed here" where an empty
  neighbourhood would otherwise read as "nothing depends on this".

The boundary this rests on, stated so it is not eroded later: **re-anchor reads, never
ingestion.** `is_indexable_path` keeps worktree copies out of the graph on purpose. One
structural graph per repository, built from the primary checkout; a worktree's edits are located
in it by repository-relative path, and blast radius is therefore "what this reaches when it
lands". Indexing worktrees would make the graph a superposition of divergent trees where two
worktrees fight over one file's edges.

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
- [x] Verify on the isolated daemon (findings visible in both scopes, the `doc_debt` tool called from a live agent), then commit and redeploy. (Operator-confirmed 2026-08-17; landed and running on the frozen app.)

### Deferred

- [ ] Dismissal / acknowledged state for a finding.
- [ ] An undocumented-file detector for source paths absent from every `Key files` section — the mirror of doc-debt, and the completeness half of the same "off vs quiet" rule.

### Phase 7.10 exit criteria

- [x] `GET /api/annotations` serves findings filtered by tag, project, session, run, and `since`, with `tag_counts` in scope, and the store resolves a session filter through its run ids without a session column.
- [x] The `doc_debt` mux MCP tool returns re-derived `{doc, changed_files}` pairs, is gated on the `doc_debt` automation, returns empty rather than a guess when unpermitted, and names its blind spot.
- [x] The Insight tab exposes Timeline and Findings without losing a saved-workspace tab, and the Findings pane is read-only, scope-toggled, and always states what the scope excludes.
- [x] The surface is verified on the isolated daemon in both scopes and from a live agent before redeploy. (Operator-confirmed 2026-08-17.)

## Phase 7.11 — Scan timeline as an agent-readable surface, and the run-level fields

The scan timeline is the most compressed artifact swe-mux produces about a conversation, and it was the only one agents could not read.
All 27 mux MCP tools existed and none touched a scan record, so an agent asked to review or monitor a sibling had to page `read_transcript` over raw conversation while a distilled, semantically-labeled index of that same conversation already existed, paid for and stored.
This phase exposes it, and fixes the accuracy defect that measurement actually supports.

### Measured baseline

Re-derived 2026-08-20 against the whole live store (`~/.mux/mux.db`): **379 records across 10 runs**, rather than the single 218-record session the first draft of this phase used.
Where the two disagree, the fleet-wide numbers are authoritative and the differences are recorded, because a single long session is one shape of run and not the distribution.

- Schema enforcement is at its ceiling and working. `complete_json` sends `response_format: json_schema` with `strict: true` plus `provider.require_parameters: true`, and `models()` filters the catalog to models advertising structured outputs, so an unenforceable observer cannot be selected. Across all 379 records there were **zero** enum-fallback fires on `work_phase`/`blocked_on`/`approach_status`.
- 30.1% of records (114) carry a repair, and 99 of those are `behavior repeated a label`, plus 12 unknown labels and 3 text truncations. `maxItems`/`uniqueItems`/`maxLength` are assertion keywords constrained decoding does not enforce, which `_normalize_behavior` already documents as expected rather than exceptional. This residue is repaired losslessly and is **not** a reason to change models.
- A stored record averages ~3.2 KB. `evidence_refs` (0.4 MB) and `tier0_fact_ids` (0.1 MB) are 42% of the 1.2 MB total; **`target` alone is 211 KB (17%)**, the single largest field, which the first draft treated as an aside. Even after dropping all of them the projection is ~730 bytes a row, so a 230-record run is ~41k tokens: **field selection alone does not bound the read** and the page limit is load-bearing.
- The `catch_me_up` digest was **not** "a few hundred tokens". Measured on real runs it rendered 4,302 tokens (230 records), 3,433 (63), and 2,133 (58), because `progress` emitted one bullet per phase segment with unbounded summaries and `claims[:12]` was uncapped in length. Reusing it was right; reusing it unbounded was not.

Three hypotheses were measured and **rejected**, recorded so they are not re-proposed:

- **Input starvation.** 24 of 379 records were truncated (6.3%), and truncated records carry `abandoned` at 8.3% against 25.6% for whole ones - anti-correlated. `MAX_INPUT_MESSAGES`/`MAX_INPUT_BYTES` are not the cause and raising them fixes nothing.
- **Window redundancy.** There is no overlapping-window dedup lever; a projection must earn its savings by field selection and paging.
- **Window width as the cause of over-firing.** `abandoned` fires at **22.6% on the wide triggers** (`full_session`, `turn_ended`, `session_exited`) against **24.9% on narrow ones**. The trigger label does not separate them. Measured width does correlate (13.9% at `messages_seen >= 8`), but see "the run-level fields" below for why gating on it was not the right fix either.

A stronger observer model is therefore **not** in scope: the fields enforcement covers are already clean, and the residual repairs are `uniqueItems` artifacts no model choice fixes.

### Backend — the scan_timeline MCP tool

- [x] `scan_timeline` is the 28th mux MCP tool: session-scoped, answering a typed `disabled`/`unsupported` rather than a fake empty when off, matching the `doc_debt` / `dead_ends` pattern (`design/features/mux-mcp.md`).
- [x] Gated on a **new `scan_reads` consumer** (`requires ("scan_timeline",)`), not on the `scan_timeline` substrate id. A distilled intent summary is in some ways more revealing than the transcript excerpt behind it, so a Project must be able to keep its timeline and still withhold it from sibling agents; gating on the substrate leaves no way to express that. `scan_search` instead reuses `semantic_history_search`, which already gates the identical query on the human surface.
- [x] The gate is the **target session's** Project, not the caller's scoped Project set. `_memory_scope` answers "which of the Projects I may see opted in", which is right for a Project-wide read and wrong for one that names a single session; `_scan_target` is its session-scoped sibling.
- [x] Default `detail: "digest"` reuses the existing `catch_me_up` consumer rather than writing a second digest — but bounded first (`DIGEST_MAX_*` in `scan_consumers.py`): most-recent phase segments, length-capped lines, and `phase_segments_omitted`/`claims_omitted` reporting what each bound dropped. The bounds apply to `GET /api/sessions/{sid}/catch-me-up` too, because a 4k-token digest is wrong in a drawer as well.
- [x] `detail: "records"` returns the compact projection: `id`, `t0`, `t1`, `trigger`, `work_phase`, `blocked_on`, `approach_status` (when present), `behavior`, `intent`, `summary`, `confidence`, `repaired_fields`, and a `target_count` plus at most three paths. Drops `evidence_refs`, `tier0_fact_ids`, `prompt_hash`, `prompt_version`, `observer_model`, and the bulk of `coverage`.
- [x] The page is bounded: 30 records by default, 100 at most, newest-first by default. The measurement above is why this is not optional.
- [x] `repaired_fields` rather than the raw `repairs` list. `_ENUM_FALLBACKS` silently substitutes `unknown`/`none` for an out-of-range enum, so a stored fallback is indistinguishable from a model assertion except through repairs — but 99 of 114 repairs are a cosmetic `behavior` dedup, and an unclassified list on a third of records cries wolf. `repaired_fields` classifies each repair to the field it touched, so a `work_phase` fallback is distinguishable from a behavior dedup.
- [x] `messages_seen` and `window_truncated` are kept for the same reason (two scalars): a `work_phase` decided from one `tool_result` and one decided from forty messages are not the same claim. The first draft dropped `coverage` wholesale.
- [x] `detail: "full"` requires explicit `record_ids`, is bounded to five, and only returns records belonging to the session that was resolved and gated. Source rehydration stays behind `GET /api/sessions/{sid}/scan-timeline/{record_id}?rehydrate=1` and is not folded in, because it reparses a transcript and that cost does not belong behind a list read.
- [x] Filters shaped to the questions an agent asks: a time window, `blocked_only`, `approach_status`, `work_phase`, a `target` path fragment, `exclude_heartbeat`, and a `since_t1` cursor. All of them run in SQL (`json_extract` for the semantic ones), so a bounded page means rows *returned*: a `blocked_only` page filtered in Python after the read would come back short of its limit and a caller could not tell that from the end of the run.
- [x] `since_t1` is **exclusive**, so feeding back the newest `t1` seen returns strictly newer records and never repeats the boundary one. `next_since_t1` is taken from the page returned, not from the newest record in the store, so a filtered poll advances only past what it actually saw.
- [x] Every result returns the enablement and liveness block — `scanning`, `last_scan_at`, `skip_reason`, `run_decided`, `run_enabled`, `project_enabled`, and the closest-to-binding gate. `ScanTimelineService.liveness()` is its single owner, shared with the drawer's `snapshot()`, so the two surfaces cannot disagree.
- [x] An **ended** session is readable. `snapshot()` cannot serve one (it needs the live record for spend and gates), but records outlive their session and reviewing a finished sibling is the read this tool exists for. An ended session resolves through history, reports `session_live: false`, and reports its context-derived fields as unknown rather than `false` — a context that cannot be resolved is not an opt-in that is off.
- [x] Defaults to the caller's own Project with the standard `project` widening argument. Noted in the design doc that distilled intent summaries are in some ways more revealing than a transcript excerpt, which is also why `scan_reads` exists.

### Backend — the scan_search MCP tool

- [x] `scan_search` is the 29th tool, exposing the already-shipped `search_scan_records` / `GET /api/history/scan-search` to agents. An exposure, not a new capability.
- [x] Together with `scan_timeline` this mirrors the shipped `search_history` → `read_transcript` pair, and the composition is named in both tool descriptions with the **exact arguments that make it work**: a record's `t0`/`t1`/`agent_run_id` reach raw messages through `search_history(run_ids, message_after, message_before)` and then `read_transcript(hit_id)`. `read_transcript` has no time-window argument of its own, so an agent told only "use the window" would attempt something that does not exist.

### Backend — no write surface

- [x] Neither `POST /api/sessions/{sid}/scan-timeline/scan` nor the backfill endpoint is exposed through MCP. Reads cost nothing, but a scan spends the human's gated budget against caps set in Settings → Automation → Scan timeline, and an agent that can trigger scans can exhaust `scan_timeline_daily_budget` for every Project on the host.
- [x] No generic record-dump tool. Every mux MCP tool stays a question, not a table (the Phase 7.10 rule).

### Backend — the run-level fields (`approach_status`, `dead_end`)

The first draft of this phase proposed gating these to wide-window triggers. Measurement refuted both halves of that plan, and the diagnosis moved.

- [x] **Root cause: no run-level memory, not too narrow a window.** `approach_status` and `dead_end` are judgments about the whole run, and they were the only run-level fields absent from the continuity payload: the observer was handed six prior windows' `summary`/`intent`/`claim`/`user_ask`/`blocked_on`/`work_phase` and never its own earlier verdict, so it re-derived "was an approach tried and dropped in this run" from scratch every ~5 messages. Prompt v4 adds both fields to `CONTINUITY_FIELDS` and instructs the observer to repeat a prior verdict unless the delta shows it changed. Cost: roughly six enum values per call.
- [x] An absent field is storage-distinguishable from an asserted one. A withheld field is omitted from both the stored record and the continuity payload rather than sent as null or `unknown`, and the projection preserves that: `approach_status` missing means the question was not answered, never that it was answered "unknown".
- [x] `PROMPT_VERSION` bumped to 4. Existing v3 records are untouched and keep their semantics; consumers reading `approach_status` across the boundary tolerate both (`.get`, never `[...]` — the annotation gate in `scan_timeline.py` read the key directly and would have raised on a withheld field).
- [ ] **Deferred: gating the fields by window width.** Not scheduled, and this is a reversal of the original plan rather than a slip. Two measurements against it: the proposed trigger allowlist barely moves the error rate (22.6% wide vs 24.9% narrow), and — decisively — **all five records in the live store that satisfy `abandoned` plus a non-empty `dead_end` came from narrow windows**, several with correct text ("WSL instability; the WSL VM failed during the previous test run and repeated recovery attempts…"). The allowlist would suppress 5 of 5, i.e. the entire dead-end corpus rather than its false positives, which is the opposite of this item's own stated guard. Reopening precondition: evidence that v4 continuity did not move the **wide-window** `abandoned` rate. If it is reopened, the predicate is measured `messages_seen`, not the trigger label.
- [ ] **Re-measure after v4 has run.** Compare the `abandoned` rate on wide-window records **before and after**, not the pooled fleet ratio: the pooled ratio moves for compositional reasons under any of these changes and would report success without any judgement improving. Baseline re-derived on the production store at redeploy time (437 v3 records): **wide-window 25.8% abandoned, 3 succeeded, n=62**; narrow 30.7%, n=375; pooled 30.0%. The five-point wide/narrow gap at this larger sample is the same refutation of the trigger-allowlist plan, now on more data. Query the bands with `prompt_version` to separate v3 from v4, since both will coexist in the store.

### Corrections to the first draft of this phase, recorded

- "5 dead-end annotations reached other agents" was wrong: `automation_annotations WHERE tag='dead-end'` is **empty**. Five records satisfy the gate, but `dead_end_memory` is not opted in on any Project here, so the real blast radius was zero and `mux.dead_ends` currently answers `disabled` on this host.
- "the heavy fields are the majority of the bytes" overstated it: they are 42%, and `target` is another 17%.
- The wide-trigger set was drawn from `SCAN_TRIGGERS`, which is the **event-bus** vocabulary. The store also holds `heartbeat`, `enabled`, `manual` and `full_session` — 84 of 379 records, 22% — so a classifier written from that constant silently mis-buckets every one of them. `STORED_TRIGGERS` now names the union.
- Trigger name is not a proxy for window width: mean `messages_seen` runs from 1.25 (`turn_started`) to 35.6 (`full_session`), with `heartbeat` at 10.2 sitting among the "wide" ones.

### Deferred

- [ ] Make `behavior` uniqueness structural instead of repaired (an object of booleans, or fixed enum slots) so the 30% repair rate goes to roughly zero. Deliberately not scheduled: the current repair is lossless and free, and this costs a schema change, a prompt-version bump, and a projection change for a cosmetic win.
- [ ] Tiered observer models (cheap on deltas, stronger on wide-window scans). Precondition for reopening: evidence that a wide-window scan with v4 continuity is still mislabeling the run-level fields, which would mean the defect is genuinely about capability.
- [ ] A fleet-wide scan digest (one latest record per active session). `GET /api/attention/blockers` already aggregates live `blocked_on` across opted-in Projects, so this only earns a tool if a monitor agent is observed needing more than that.

### Docs, tests, and ship

- [x] Updated `design/features/mux-mcp.md` (the two tools, the projection contract, the composition, and why no scan trigger is exposed), `design/features/scan-timeline.md` (the agent-readable surface, the trigger vocabulary, the continuity change and the measured baseline), `design/features/automation-enablement.md` (`scan_reads`), `design/interfaces.md`, `design/CLAUDE.md` routing, and `technical/backend/packages.md`.
- [x] Backend tests (`tests/test_mcp_scan_timeline.py`, plus additions to `tests/test_scan_timeline.py`): the projection strips the heavy fields and keeps the trust signals; an absent `approach_status` stays absent; repair classification including the unrecognized case; the digest bounds and what they report dropping; each filter, the exclusive cursor, and that a filtered page is bounded by rows returned; newest-first paging; the stored-trigger vocabulary; `disabled`/`unsupported` rather than empty; the gate reading the target session's Project; `record_ids` bounded and unable to reach another session's record; an ended session readable; no reachable scan trigger; and that continuity carries the verdict forward while omitting a withheld field.
- [x] Extended the live-automations tier (`tests/test_live_automations.py`) to cover both tools over a real `AutomationStore` round-trip with run attribution — the SQL filters and the cursor really run in SQLite, which a stub cannot prove.
- [x] Verified on the isolated daemon (8799 / `~/.mux-hardening`, 2026-08-20) with two freshly spawned Claude sessions in a scratch Project: a reader agent driving the real MCP wire under its own injected bearer token against an observed sibling's seeded spine. Confirmed `tools/list` returns 29 with both new tools; the digest, the newest-first records page, the `since_t1` cursor (0 new at the head, strictly-newer rows mid-run), every filter, `full` with its 5-id bound, `scan_search` including two-term narrowing, and the liveness block. Both gates are independent and each names itself: `scan_reads` off refuses `scan_timeline` while `scan_search` keeps working, and vice versa. The freshly spawned session's settings file carries both tools in its permission allowlist, confirming the spawn-order caveat applies only to older sessions. Measured cost on that spine: digest ~390 tokens, a 7-record page ~1080, five `full` records ~2503.
- [x] Landed on master and redeployed to the frozen app 2026-08-20 (19 live sessions preserved through the staged swap; no supervisor rebuild was needed, since nothing here touches the supervisor's source closure).
- [x] Verified again on the **live fleet** against real observer-produced records, which the isolated daemon cannot produce (no OpenRouter key there). `scan_search` returns real distilled hits with correct run attribution - `your_current_run` for the reading session's own records, `sibling_run` for others - and its AND really narrows once the default limit stops saturating: `worktree` 100 hits, `worktree pytest` 50, `worktree kokoro` 1. `scan_timeline` serves a live sibling's 13-record spine with a real `closest_gate` (the daily scan-token budget, 60% used). Measured on real data: a digest ~880 tokens, a 4-record page ~1228. Both opt-ins were switched on for the swe-mux Project as part of this (`scan_reads`, `semantic_history_search`); they are per-Project and off everywhere else.

**Two defects the live run found that the unit tests did not** (fixed in 584d68a):

- The digest reported `phase_segments_omitted: 0` beside a `progress` list that had silently dropped four of six segments. `items[len(items) - keep:]` slices a *negative* index whenever the list is shorter than the bound but longer than the shortfall; both the far-larger and far-smaller cases are correct by Python's clamping, which is why a test at 40 segments and a test at 1 both passed over it. The counters now derive from what the digest actually carries rather than from the bound.
- A typo'd filter answered with an empty page: `detail` was validated and `work_phase`/`approach_status` were not, so `work_phase: "vibes"` read exactly like "no records are in that phase" - this surface's own silent-empty failure, arriving through an argument instead of a gate. Declaring the enums in the `inputSchema` is not enough; a server that leaves them to the client has the defect anyway.

The pattern worth keeping: both were *shapes of honesty* the phase already committed to, broken in places the phase's own tests were aimed away from.

### Phase 7.11 exit criteria

- [x] `scan_timeline` and `scan_search` are mux MCP tools, per-Project gated, returning a typed `disabled` rather than a fake empty, with a bounded digest default that reuses `catch_me_up` and a records projection that omits `evidence_refs`/`tier0_fact_ids`/hashes while keeping the fields that let a reader calibrate a label.
- [x] A monitor poll is bounded by the exclusive `since_t1` cursor and a page limit, and every result states `scanning`, `last_scan_at`, `skip_reason`, and `run_decided`, so a budget-stopped scanner is never readable as a quiet session.
- [x] No scan-triggering or backfill capability is reachable through MCP, and no generic record-dump tool exists.
- [x] An absent run-level field is storage-distinguishable from an asserted one, and `PROMPT_VERSION` is bumped.
- [ ] The `abandoned`/`succeeded` ratio is re-measured **on wide-window records** on a comparable session after v4 has run, against the 22.6%/n=62 baseline recorded above.
- [x] The observer model is unchanged, and the reason is recorded: enum conformance was already clean at zero fallback fires across 379 records, and the residual repairs are `uniqueItems` artifacts that no model choice fixes.

## Phase 7.12 - Code-analysis expansion: conflict prediction, entity-level change facts, and quality deltas

The structural graph (Phase 7.9) knows how the code connects, and the behavioral substrate knows what every session touched, but nothing yet joins the two at the symbol level.
This phase layers five deterministic analyses over that join, ordered by how much of their value only mux can produce: mux is the only observer that sees every parallel session's edits as they happen, which is what makes cross-session conflict prediction and stale-caller detection possible at all.
Everything here is model-free, registers as consumers in the enablement DAG (`automation_registry.py`), writes annotations only (never a PTY write), and inherits the Phase 7.9 honesty rules: static results are labeled a lower bound, unresolved is recorded rather than guessed, and empty beats a plausible guess.
It depends on Phase 7.9 (the graph and its per-write re-parse) and Phase 7.8 (provenance attribution).
The entity-diff substrate ships first because the conflict and drift detectors consume it.

### Substrate - changed-entity diff per turn

- [ ] On the `file_write` re-parse the graph already performs, diff the file's symbol set against its prior parse: added, deleted, and modified (body-hash changed) functions/classes/methods, plus each modified symbol's signature (name, parameter list) before and after. Store as compact per-run entity-change records keyed on `agent_run_id` and the `normalize_target()` path identity, so they join facts, provenance edges, and graph edges. (`code_graph.py` owns the parse; the diff is a comparison of two `defines` sets plus per-symbol content hashes, not a new parser.)
- [ ] Do not adopt an external tree-diff tool (the GumTree/`code-diff` class): parsing another tool's text output is brittle, and the symbol-table diff on our own parses answers the questions the consumers below actually ask.
- [ ] Surface the record where a turn is already narrated: attention-narration input, the change-map node detail card, and doc-debt precision (a doc owning a file where a signature changed owes more than one where only a body changed).

### Cross-session conflict prediction (fleet-unique)

- [ ] Detect symbol-level overlap between concurrently live branches of one repository: session A modified symbol `S` while session B, in a different checkout, modified `S` itself, a caller of `S`, or a file in `S`'s one-hop blast radius. File-level overlap is the cheap first tier; the graph is what makes it symbol-level.
- [ ] Fire before merge, as a fleet-scoped annotation naming both sessions, the overlapping symbols, and the linking edge kind (same symbol / caller / import), deduped per session pair per symbol per run. This warns about semantic conflicts a clean git merge cannot detect, and only a system observing every session's writes can produce it.
- [ ] Branch seeds reuse the change map's admission and re-anchoring rules (`_change_map_checkout`): repository-relative identity, worktree-aware, never ingesting worktree copies into the graph.

### Signature drift - stale callers

- [ ] When an entity-change record shows a signature change, intersect the symbol's reverse callers with the run's own `file_write` facts; callers in files the run never touched are flagged in one annotation naming the changed signature and the untouched caller files. Sharper than the shipped `_unexamined_callers` because it names the specific breaking change, and labeled a lower bound for the same dynamic-dispatch reasons.

### Lint delta

- [ ] After a turn's writes, run `ruff` on the touched Python files and report only diagnostics **new** against a per-file baseline captured at the previous parse, never the pre-existing count. Include the `ASYNC` rules: blocking-call-in-async is a bug class that has bitten this codebase (`loop_lag.py` exists because of it). Ruff is a standalone binary with no venv coupling, which is why it clears the bar LSP failed; `tsc`/`dmypy` deltas are explicitly deferred because they carry project-environment coupling.
- [ ] Bound the cost: touched files only, one run per turn boundary, a hard per-run diagnostic cap, and the annotation deduped per file per run.

### Dependency-introduction detection

- [ ] On a `file_write` to a dependency manifest or lockfile (`pyproject.toml`, `package.json`, `uv.lock`, `package-lock.json`), diff the declared dependency set and annotate each addition with the run that introduced it. Agents add packages casually, and "this run added dependency X" is a decision the human wants surfaced when it happens, not discovered weeks later.
- [ ] Optionally check additions against a vulnerability database via `osv-scanner` (a single Go binary, Windows-clean), gated separately because it is a network call. An absent scanner degrades to the introduction annotation alone, stated rather than silent.

### Deferred (same substrate, not scheduled)

- [ ] Runtime-traceback-to-graph linking: parse tracebacks out of the PTY stream mux already captures, resolve frames to graph nodes, and record observed dynamic call edges. The most novel derivation, held until the detectors above prove the annotation surface can carry more volume.
- [ ] Turn-scoped mutation testing: mutate only the turn's changed function bodies and run only the covering tests from the `test_gap` map, answering whether an agent-written test constrains the code it shipped with. Opt-in and background; needs a cost model first.
- [ ] Cross-language API contract mapping: join FastAPI route definitions to frontend fetch call sites from the same parses, so an endpoint-shape change flags untouched frontend callers - a blast radius that crosses HTTP and that no import graph can see.
- [ ] Config-schema validation on write, and per-turn complexity deltas.

### Phase 7.12 exit criteria

- [ ] Entity-change records are produced from the existing per-write re-parse, run-attributed, joinable on the shared path identity, with no external tree-diff dependency.
- [ ] A symbol-level overlap between two live checkouts of one repository produces one fleet-scoped annotation before merge, naming both sessions and the linking edge kind, deduped, and labeled a lower bound.
- [ ] A signature change with untouched reverse callers produces one annotation naming the change and the stale files.
- [ ] Lint findings are reported as new-only deltas on touched files with bounded cost, and dependency additions are annotated with the run that introduced them, with an absent vulnerability scanner stated rather than silent.
- [ ] Every detector is per-Project gated through the enablement DAG, writes annotations only, and pushes nothing into any agent.

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

- [x] Build a distro-side bridge for native WSL Claude/Codex executable discovery,
  promotion/demotion, hook-secret delivery/execution, transcripts, and native-id
  correlation. Windows interop commands alone do not qualify.
  (`wsl_bridge.py`. Discovery runs *inside* the distribution and rejects anything
  resolving under `/mnt/`, which is not a nicety: a WSL PATH normally carries the Windows
  npm directory through interop, so `command -v codex` frequently resolves to a Windows
  binary that runs - and accepting it would produce a session that looks bridged while the
  agent writes its transcript into the Windows home. Verified live against Ubuntu 24.04:
  the native `claude` at `/home/atora/.local/bin/claude` is found and the two interop
  `/mnt/c/.../npm` binaries are correctly refused.
  Instrumentation is a single dependency-free stdlib script (`mux_bridge.py`) plus
  per-harness shims materialized under `~/.mux-bridge/`, rather than installing swe-mux
  into every distribution - the distro side only has to run the real CLI and POST a hook.)
- [x] Translate Project, transcript, clipboard-media, preview/listener, instruction, and
  process ownership paths without leaking Windows-only paths or trusting guest listeners.
  (`wslpath` in both directions, with the drive-letter regex as a fallback only, because
  `wslpath` reads the distro's real mount table and a custom `automount.root` would defeat
  the regex. Verified live: `D:\PROJECTS\swe-mux` -> `/mnt/d/PROJECTS/swe-mux`, and
  `/home/atora/.claude` -> `\\wsl.localhost\Ubuntu\home\atora\.claude`, which is how the
  daemon reads a transcript written inside the distribution without executing anything
  there.)
- [x] Keep WSL profiles labelled `agent-bridge-unavailable` until native agents and
  promotion/state/history tests match Windows contracts.
  (`derive_capabilities` now takes `wsl_bridge_ready`, and **`None` - "not checked" -
  reports the same unavailable label as `False`**. That asymmetry is the whole point:
  claiming a bridge nobody verified would present an uninstrumented agent as an observed
  one, and the pane looks entirely normal while producing no hooks, no transcript link and
  no status. The readiness answer is injected rather than computed, because it costs
  `wsl.exe` round trips and the profile list re-renders on ordinary polls.)

**The network boundary, which is why the bridge is opt-in.** WSL2's default NAT networking
forwards `localhost` from Windows *into* a distribution but not back out: from inside, the
Windows host is the default gateway. A hook fired by a bridged agent therefore cannot reach
a loopback-bound daemon at all, and fails silently. So `wsl_bridge_enabled` (default off)
adds a listener on the WSL adapter, and that is a real widening - every process in every
distribution on the machine can then reach a daemon that has no application login - which is
why it is named in config rather than inferred from "this host has WSL".

**Measured on this host, and it took two live corrections.** First, the gateway lookup
shelled out to `ip route show default | awk '{print $3}'`; the pipeline came back
*unfiltered* across the Python -> `wsl.exe` -> `sh -lc` boundary, which parsed as "no
gateway" and fell back to loopback - the one address that cannot work. It now reads
`/proc/net/route` and parses in Python, which also removes the dependency on `iproute2`.
Second, with the address correct, a listener on the WSL adapter is reachable from Windows
and **times out** from inside the distribution: Windows Defender Firewall drops it. That is
the same class of problem the tailnet listener already has a rule for, so
`windows_firewall.build_wsl_repair_script` adds a matching inbound rule scoped to the WSL
subnet (`172.17.96.0/20` here) and to the swe-mux executable, never to `Any`. The rule was
created and confirmed present on this host.
`bridge_status(daemon_port=...)` probes reachability and names the firewall in its
`reasons`, and `mux doctor` reports one row per distribution - because a bridge that is
merely quiet is indistinguishable from one that is working.

**A setup surface, because machinery nobody can reach is not a feature.** The first pass left
`install_bridge` and `build_wsl_repair_script` written, tested and *called by nothing*: the
only way to use the bridge was to hand-edit `config.toml`, restart, read a `mux doctor`
sentence, and then write your own firewall rule. That is the same defect this phase spent its
time removing - something that looks like a capability and is not - so it is closed here.

`GET /api/wsl/bridge` answers **without** `wsl_bridge_enabled` being on, which is the whole
point: a user cannot be asked to turn something on before anything will tell them whether it
would work. It reports the adapter address and subnet, the rule name, `restart_required` (the
flag changes which sockets the daemon binds, and that only happens at startup - previously
silent), and one row per distribution. `?probe=1` inspects each distribution and is opt-in
because inspecting one *starts* it, so it is a button rather than something opening Settings
does. `POST /api/wsl/bridge/install` and `POST /api/wsl/bridge/firewall/repair` each require
their own gesture header, for the reason the tailnet repair does: one writes into the user's
distribution, the other raises a UAC prompt, and no background poll may reach either. The
repair refuses with `no_wsl_adapter` rather than guessing a scope, because an invented scope
would silently widen the rule past what the user agreed to.

`WslBridgePanel.tsx` renders it beside the firewall panel it mirrors, and `wslBridge.ts` owns
the blocker *ordering* - the order is the advice, so the firewall is named before the install,
because an install that could never phone home fixes nothing.

**The doctor check was fixed at the same time, and it had the same shape of bug.** It returned
nothing unless the bridge was already enabled, so it was silent in exactly the situation it
exists for: a host with WSL and a native agent inside it, where the user has no idea the bridge
is possible. It now reports one row per distribution when the feature is off - as `ok`/`info`
with "enable it in Settings", because an offer is not a fault - while still reporting a real
blocker as `unavailable`. What it still does not do when off is *probe*, since a diagnostics
read must not spend seconds booting a distribution nobody asked about.

Outstanding for this item: an end-to-end hook from a bridged agent through the firewall rule
to a live daemon. It needs the daemon actually binding the WSL adapter. The frozen-app
redeploy that used to block it is done (2026-08-17), so what remains is setting
`wsl_bridge_enabled` and restarting, rather than a build - but nobody has run that path, so it
stays recorded as unproven rather than claimed.

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
- [x] Add a cross-platform process-inspection boundary for descendants, resources,
  signals/termination, anomaly evidence, and listener ownership.
  (Already largely satisfied and now confirmed rather than assumed: `processes.py` is
  psutil-based throughout, and its one genuinely divergent operation - interrupt - already
  branches correctly, sending `SIGINT` on POSIX and `CTRL_BREAK_EVENT` on Windows with an
  explicit "cannot be interrupted, use terminate" rather than a raw psutil error. Ownership
  evidence is the part that needed a real second implementation, and that is
  `process_reaper` / `posix_process_group` above: `process_ids()` answers on both hosts the
  question a parent/child walk cannot, because a descendant whose intermediate parent exited
  is reparented away from the walk while its Job membership or process group still names it.)
- [x] Add OS reveal services: Explorer, macOS `open`, and Linux `xdg-open`.
  (Already shipped in `file_manager.file_manager_command`, which covers all three and takes
  the platform as an argument; the Win32 window-focus helper it sits beside imports
  `wintypes` lazily, so nothing here blocked a non-Windows import.)
- [x] Generate agent-promotion launchers per OS with safe structured argv/env/hook-secret
  propagation.
  (`launchers._write_shim` writes a `.cmd` on Windows and a `#!/bin/sh` + `exec` script on
  POSIX. The POSIX shim is `exec`ed rather than run as a child so the CLI inherits the shim's
  pid - a wrapper process between the pseudoterminal root and the real agent would make the
  root exit first and leave every process-tree walk rooted at a dead pid. Argument forwarding
  is `%*` and `"$@"` respectively, the spellings that do not re-split; proven end to end on
  Linux against a stub CLI with the nastiest real argv, Codex's JSON-valued `-c notify=[...]`,
  which an unquoted `$@` silently mangles.
  **The self-invocation trap the port reintroduced and this closes:** `is_mux_shim` accepted
  only `.cmd`/`.bat`, which is correct on Windows and wrong everywhere else, because a POSIX
  shim is deliberately extensionless so that `claude` resolves to it. Every shim would have
  read as a real CLI, so `harness.detect_installation` - which goes through `which_real` -
  would have reported every harness installed on Linux and every launch would have recursed
  into the shim. The suffix gate is now per-host and the marker check reads a bounded 4 KiB
  rather than the whole candidate file.
  Hook commands needed no change: `_bash_executable_path` translates a `C:/...` interpreter
  path for Claude's Bash hook runner and leaves a POSIX path alone, which was already the
  correct behaviour on both.)
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
- [x] Make Project root and `.swe-mux/` resolution platform-aware across Git worktrees,
  non-repository cwd, symlinks, UNC, and WSL translation.
  (Project identity comparisons moved to `path_identity.same_path`. The `os.path.normcase`
  they used was already platform-correct about *case* - unlike the `casefold` fixed earlier -
  but it is still a string test, so two genuinely different spellings of one directory
  registered as two Projects over the same tree, both owning the same `.swe-mux/` with
  nothing downstream able to tell them apart. Symlinks, junctions, a UNC path against a
  mapped drive, and bind mounts are all that shape. WSL translation is `wsl_bridge`'s
  `wslpath` pair. `.swe-mux/` itself is repo-relative and needed no change.)
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

- [x] Preserve the complete Windows regression contract while adding abstractions.
  (Windows went 2925 -> 2927 passing across the seam work, with no test weakened to get
  there. Where a test stopped passing on Linux it was triaged case by case: marked
  Windows-only when its subject is a Windows rule - COMSPEC, `.cmd` shims, PowerShell
  first-run, Win32 windows, NTFS case-insensitivity - and given host-appropriate fixture
  paths when it merely happened to be written with `C:/repo`, which on POSIX is a *relative*
  path whose first component contains a colon, so every assertion built on it was meaningless
  rather than merely failing. `tests/host_paths.py` holds that helper.)
- [x] Linux: PTY/process groups, bash/zsh/pwsh, Claude/Codex promotion/transcripts,
  `xdg-open`, Project files, processes/listeners, queue delivery, and daemon-death cleanup.
  Two live acceptance scripts on Ubuntu 24.04, both against a real daemon on an isolated
  port and data dir:
  `tools/linux_acceptance.sh` (**SMOKE-PASS**) - a real `/bin/bash` on a POSIX
  pseudoterminal, the child leading its own process group, ownership assigned, input and
  output over the same WebSocket a browser uses, and the session tree reaped after a
  SIGKILLed daemon.
  `tools/linux_agent_acceptance.sh` (**AGENT-PASS**) - a real authenticated Claude Code
  session: one real turn completed, the conversation served by the transcript endpoint, the
  transcript on disk at the adapter-derived path
  (`~/.claude/projects/-home-atora-swe-mux-linux/<conversation id>.jsonl`), the history row
  carrying it, and the tree cleaned up afterwards. `xdg-open` was already implemented.

  **The live run found two bugs that no offline test could have, and both are the same
  shape: a Windows-shaped assumption that does not fail on Linux, it silently succeeds.**

  1. *The daemon launched the Windows agent.* The harness registry declares
     `executable="claude.exe"`, correct while Windows was the only host. Under WSL the
     Windows install is on PATH through interop, so `shutil.which("claude.exe")` **succeeds**
     and resolves to `/mnt/c/.../claude.exe`. The Linux daemon then ran a Windows binary: it
     started, painted a TUI, reported the `wsl.localhost` share as its working directory,
     wrote its transcript into the Windows home where no Linux path points, and joined no
     Linux process group - so cleanup could not reach it, which is why the agent kept
     outliving the daemon. Fixed by `harness.host_executable` (drop `.exe` off Windows) plus
     a defence-in-depth guard, `host_platform.is_windows_interop_path`, that keeps
     `which_real` from returning an interop binary at all - because a bare `claude` lands
     there too, a WSL PATH carrying the Windows npm directory being the normal case.
  2. *An agent CLI cannot be driven by a client that only reads.* The CLI emitted `ESC[6n`
     (cursor-position report) and blocked, because a browser answers that and a probe script
     does not. It looked exactly like a hung agent and burned a core busy-waiting - the same
     signature as an orphaned node process that had to be cleared from the test host.
     `tools/pty_probe.py` now answers DSR and device-attribute queries, and sends prompt text
     and its carriage return as two writes with a pause, because a composer commits typed
     text asynchronously and a CR in the same write is processed against an empty composer.
     Both are recorded here because anything driving an agent TUI programmatically will hit
     them.
- [ ] macOS: PTY/process groups, zsh/bash/pwsh, promotion/transcripts, `open`, service
  environment behavior, Project files, queue delivery, ownership, and cleanup.
  **Implemented and typechecked, deliberately not claimed as proven.** Every POSIX path
  above is written for macOS as well as Linux and is typechecked under `--platform`, the
  Keychain secret backend is macOS-specific, `open`/`open -R` was already the reveal
  command, and the data directory follows Application Support. But no macOS host exists to
  run any of it on, and the cross-platform findings are explicit that a unit test which
  mocks the platform proves nothing about the platform. It stays unchecked until a real
  macOS run happens; the honest state is "should work, unverified", and writing it down that
  way is the point.
- [x] Define/migrate data and config locations consistently for Windows `~/.mux`, XDG, and
  macOS platform conventions.
  (`config.default_data_dir()`: `MUX_DATA_DIR` first, then Windows `~/.mux` unchanged,
  macOS `~/Library/Application Support/swe-mux`, Linux `$XDG_DATA_HOME/swe-mux` falling back
  to `~/.local/share/swe-mux`.
  **The migration rule is that an existing `~/.mux` always wins, on every host.** Applying a
  convention to a directory that already has data would start a POSIX user from an empty one
  *beside* their real one - projects gone, history gone, nothing reporting an error, and the
  old data still on disk looking fine. A convention is for a fresh install only.
  Secrets are the one thing that deliberately does not travel: DPAPI binds to the Windows
  account, the Keychain to the login keychain, libsecret to the session keyring, so a copied
  data directory arrives with secrets that are intact and undecryptable. That now fails
  closed with a message naming the cause and asking for a re-entry, rather than reading as
  corruption. Anything portable enough to survive the copy would be portable enough for
  whoever else obtained the copy.)

### Phase 10 exit criteria

Scored per target, because "passes" means something different on a host that has been run
and one that has not. **Windows and Linux are met. macOS is implemented and unproven, and
is written down that way rather than counted.**

- [x] Windows, WSL-agent-aware, Linux, and macOS targets pass applicable API/WS/session,
  ownership, telemetry, readiness, and cleanup suites.
  Windows **2943 passing**, Linux **2893 passing**, from one suite with per-host marks and no
  mocked platforms. `.github/workflows/ci.yml` now runs a `platform` job on `ubuntu-latest`
  and `macos-latest` beside the Windows gate, plus the two `--platform` mypy passes on every
  runner, so both implementations are typechecked wherever the gate runs. The WSL-agent half
  is `wsl_bridge.py` with its live discovery/translation/reachability verification.
  macOS runs `continue-on-error` - a visible statement that nothing has executed there, not a
  way to ignore failures; the flag comes off the first time it passes.
- [x] Input, resize, Unicode widths, signals, clipboard/paste, replay, shell exit, agent
  promotion, attach ownership, queue delivery, and crash cleanup work on each target.
  On Linux, proven end to end rather than by unit test: `tools/linux_acceptance.sh` drives
  input and output over the real `/pty/{sid}` socket with attach handshake, input-ownership
  claim and replay, and SIGKILLs the daemon to prove crash cleanup; resize goes through
  `TIOCSWINSZ`, signals through the process group, shell exit through waitpid codes
  normalized to 128+signal. Agent promotion is `tools/linux_agent_acceptance.sh`
  (**AGENT-PASS**) against a real authenticated Claude Code. Ctrl+C is the one asymmetry and
  it is the *Windows* side that is limited, not POSIX: writing `\x03` to a ConPTY does not
  interrupt (recorded in Phase 7), while POSIX delivers SIGINT to the foreground group
  normally.
- [x] Git/worktrees, history/resume, hooks, profiles, `.swe-mux/`, notes, instructions,
  processes/listeners, previews, and clipboard images work without escape/path leakage.
  Covered by the suite on both hosts, with the leakage-shaped defects found and fixed rather
  than assumed absent: `casefold`-based path identity replaced by `path_identity`
  (`os.path.samefile` first), Project registration now seeing through symlinks, Pillow made a
  real dependency so image handling is not a Windows feature, Playwright's browser cache
  resolved per host, and the interop guard that stops a Linux daemon launching a Windows
  agent whose paths all point at the wrong side of the boundary.

**The frozen Windows app now carries the seams.** Rebuilt and redeployed 2026-08-17 with a
single `uv run python packaging/build_desktop.py` from a shell outside swe-mux, which is the
right tool once everything is stopped: a plain run builds frontend, app bundle and supervisor
together, so the ordering trap between the two staged paths does not arise - the redeploy
script's preflight requires a live supervisor, while the supervisor build refuses to run while
one exists. Confirmed live afterwards: `supervisor_bundle_current()` flipped `False` -> `True`
with the stored hash matching source, `/api/health` reported `supervisor: true` and
`supervisor_state: "connected"`, the served bundle moved to the newly built asset rather than
the previous one, and the Tailscale Serve 443 route still pointed at the real daemon. It cost
every live session, which is why it was a deliberate act rather than part of the phase.

**Known gaps, deliberately not closed here and not counted as met:**

- `deterministic_consumers.normalize_target` still casefolds. It is the storage key for the
  code graph, doc-debt ownership and Tier 0 targets, so changing it rewrites existing
  `mux.db` keys; it needs a migration, not an edit.
- The WSL bridge's end-to-end hook delivery through the firewall rule is still unproven. The
  redeploy it was waiting on has now happened, so the remaining requirement is ordinary
  configuration rather than a build: `wsl_bridge_enabled` is off by default and changes which
  sockets are bound, so it needs setting plus a daemon restart before a bridged agent's hook
  can reach the daemon at all. Nothing has yet run that path end to end.

## Phase 10.5 - Distribution licensing and the voice-stack replacement

swe-mux has no `LICENSE` file, and the frozen desktop bundle it hands to a user redistributes GPL-2.0 code.
Both facts are inert while this machine is the only distribution, and both become real the moment Phase 11 publishes an artifact.
This phase is the precondition for that publication: it removes every copyleft component from the shipped closure, replaces the two voice engines that put them there, and states the project's own license.

The audit behind it ran on 2026-08-17 against the live `dist/swe-mux` bundle and the resolved dependency closure rather than against declared metadata.
Declared metadata is precisely what hid the defects: PyAV declares BSD-3-Clause and sherpa-onnx declares Apache-2.0, and both ship GPL binaries inside the wheel.

### Measured baseline

Copyleft in the shipped bundle:

- `dist/swe-mux/_internal/av.libs/` is 63 MB of FFmpeg, and `avcodec-62`'s import table links `libx264` and `libx265`, which are GPL-2.0-or-later.
  Its configure string carries `--enable-libx264 --enable-libx265 --enable-version3` while `av_license()` compiles to the single string `LGPL version 3 or later`.
  The linkage governs, not the self-description.
- Nothing in swe-mux uses it.
  `av` enters only through `faster_whisper/__init__` into `faster_whisper/audio.py`, whose module-level `import av` exists for `decode_audio`.
  `voice.py:1514` builds a float32 array from int16 PCM taken from a validated WAV header and hands it straight to `WhisperModel`, and no call site reaches `decode_audio`.
- `edge-tts` is LGPL-3.0 and is the default engine (`tts_engine: str = "edge"`, `config.py:760`).
  It is not a client of a public API: it reproduces Microsoft Edge's read-aloud call against an undocumented endpoint using an embedded client token.
  The three-attempt 403 retry in `_synthesize_edge` is the visible symptom of a gate Microsoft rotates.
  Every user of a published build would be making unauthorised calls to a Microsoft service, and the text sent is a distillation of the operator's own sessions.
- `pystray` is LGPL-3.0 and stays, under notice and relink terms rather than removal.
- MPL-2.0 (`certifi`, `pywebpush`, `py-vapid`, `pathspec`, `tqdm`) is file-level copyleft and needs license text only.
  Re-measured 2026-08-24 over the resolved distributed closure: `pathspec` is dev-only and does not ship, so the shipped MPL set is the other four.

**Correction, 2026-08-24: this baseline is incomplete, and the phase's own work is why.**
`num2words` is LGPL-2.1 and entered as a direct dependency *of the espeak-free TTS replacement*, after the audit was taken.
An audit is a photograph, and the remediation moved what it photographed - which is the argument for the dependency-review gate being a standing check rather than a one-off review.
Two further findings from re-measuring the closure rather than re-reading the audit:
`av` is still resolved (see the "Remove the GPL closure" note - the bundle excludes it, a wheel install does not), and `clr-loader` reaches the closure through `pywebview` declaring **no** license in any metadata field, only a `LICENSE` file, which is why the gate reads license text and treats undeterminable as failing.

Attribution owed regardless of the voice work:

- No `LICENSE`, `NOTICE`, or `THIRD-PARTY-NOTICES` exists anywhere in the repository, so the default is all rights reserved, which contradicts what `site/index.html` announces.
- Apache-2.0 dependencies that carry a NOTICE (`huggingface_hub`, `tokenizers`, `requests`, `aiohttp`) need it reproduced.
- The frontend bundle redistributes **modified** `@xterm/*` (MIT), patched at install time by `patch-xterm-webgl.mjs` and `patch-xterm-requestmode.mjs`, so the notice must state that modification.
- `ctranslate2` ships Intel's `libiomp5md.dll` under Intel's own redistribution terms.
- The notification sounds already carry `LICENSE.orca.txt` (MIT) and are compliant.

Replacements measured and **rejected**, recorded so they are not re-proposed:

- **sherpa-onnx with Parakeet TDT**, the stack Orca uses for dictation.
  Its published wheel statically links espeak-ng: `_sherpa_onnx.cp312-win_amd64.pyd` contains `espeak_Initialize`, `espeak_ng_Cancel`, and `ESPEAK_DATA_PATH`.
  TTS is compiled in by default, so espeak-ng ships whether or not TTS is ever called.
  Upstream agrees it is a licensing defect (k2-fsa/sherpa-onnx#3731, open since 2026-07-08, deferred to a 2.0.0 that has not shipped against a current 1.13.5).
  Reopen when 2.0.0 ships, not before, because the alternative is maintaining a `SHERPA_ONNX_ENABLE_TTS=OFF` source build per platform.
- **`kokoro-onnx`** as the Kokoro runtime.
  It requires `espeakng-loader` and `phonemizer-fork` unconditionally, and `import kokoro_onnx` fails outright without them.
  `espeakng-loader` ships `espeak-ng.dll` plus 18 MB of espeak data, so the dependency is a real GPL payload rather than a nominal one.
- **`misaki[en]`** as an extra, because the `en` extra pulls those same two packages.
  The base `misaki` distribution plus `spacy` and `num2words` must be depended on directly instead.
- **Piper**, which embeds espeak-ng from 1.3 onward, and **KittenTTS**, which uses the same phonemizer family.
- **Rebuilding PyAV against an LGPL-only FFmpeg**, and **relicensing swe-mux as GPL** so the current bundle becomes lawful.
  Both spend real effort solving a problem that disappears when an unused dependency is dropped.
- **Browser `speechSynthesis`.**
  It cannot produce the server-side clip that `/api/voice/clips/{clip_id}/audio`, clip history, and phone playback are built on.
- **AGPL or a source-available license.**
  The strip-mining threat model does not fit a local desktop app that drives local CLIs, and both repel the users and the investors the project wants.

Kokoro replacement baseline, measured on the development host: int8 ONNX through a direct onnxruntime session, misaki G2P, `fallback=None`.

| case | chars | g2p | synth | audio | RTF |
|---|---|---|---|---|---|
| short | 37 | 10.5 ms | 1.62 s | 2.39 s | 0.68 |
| medium | 125 | 3.9 ms | 3.49 s | 6.34 s | 0.55 |
| long | 383 | 7.3 ms | 10.60 s | 21.74 s | 0.49 |

Model load is 0.77 s against 89 MB of int8 weights plus 27 MB of voices.
The ONNX interface is three inputs (`tokens` int64, `style` float32[1, 256], `speed` float32[1]) and one `audio` output, which is why the runtime is a direct session rather than a wrapper library.
The G2P produces identical output with `espeakng_loader` and `phonemizer` physically removed from the environment, which is what makes the GPL-free path verified rather than intended.

Out-of-vocabulary behaviour without an espeak fallback was measured against this project's own vocabulary.
Unresolved words return a `❓` token: `pyproject`, `Worktree`, `healthcheck`, and the `swe` of `swe-mux`, which is roughly one word per sentence of release-note prose.
Numbers ("996", "58") and abbreviations ("ConPTY") already resolve correctly, and every failure is a compound, so the fix is a splitter that retries on camelCase, hyphens, and underscores before giving up, with a small project lexicon behind it.

### Decisions

- **swe-mux is licensed Apache-2.0.**
  Over MIT for three reasons that matter to a project that may later take investment: an explicit patent grant with retaliation termination where MIT is silent, an explicit trademark reservation that keeps the name and mark out of the grant, and §5, which states the license inbound contributions arrive under instead of relying on convention.
  The cost is length and a per-file header convention that this phase declines; one `LICENSE` plus a README section is the whole ceremony.
- **Contributions use a DCO sign-off, not a CLA.**
  Sole authorship today means relicensing is still possible; the first outside contribution ends that permanently, and a CLA only binds what follows it.
  The trigger to revisit is a funding conversation becoming concrete, not a contribution arriving.
  Copyright should sit with an entity rather than an individual if one is ever formed; `NOTICE` therefore says "The swe-mux Authors", which needs no edit when that happens.
  Re-examined 2026-08-24 against the explicit intent to raise venture funding, and **kept**.
  The concern a CLA answers is the ability to relicense or dual-license the core, and open-core does not need it: commercial features are separate code held outright, never a relicensed version of this repository.
  Open Core Ventures - a fund that exists specifically to back open-core companies - recommends the DCO over a CLA for the same friction reason, and GitLab runs this way.
  The countervailing evidence is real and recorded: a CLA is what traditional counsel asks for, and if a future plan does require relicensing the core, DCO-era contributions cannot be swept up retroactively.
- **Licensing is not what makes this fundable, and the comparable proves it.**
  Recorded because the question was asked directly and the premise was wrong.
  Herdr - a near-identical product, an open-source runtime that owns coding-agent terminal sessions and turns them into a working/blocked/done attention queue - joined YC's F26 batch with 25k GitHub stars and 340k downloads.
  The "$500k" attached to that is YC's standard deal for every company ($125k for 7% post-money, plus $375k on an uncapped MFN safe), not a raise its license earned.
  What licensing actually controls is diligence risk, and it is a narrow set: copyleft that could force disclosure of a *proprietary* layer, a clean enough IP chain to relicense if needed, and patent exposure.
  MIT versus Apache-2.0 is not a gate for any of them.
  Herdr's own move was AGPL → Apache-2.0 to remove adoption friction, which is the same conclusion this phase reached independently when it rejected AGPL.
- **`av` is dropped, not replaced.**
  A stub module satisfying the unused import is the entire fix, and it removes 66 MB along with the GPL closure.
- **STT stays on faster-whisper.**
  Once `av` is gone the remaining closure is MIT throughout (faster-whisper, CTranslate2, the Whisper weights), so there is no licensing reason to migrate, and no user-visible change to make.
- **TTS moves to Kokoro-82M** (Apache-2.0 model, trained on public-domain and permissively-licensed audio) driven directly through onnxruntime, which the bundle already carries.
- **Phonemization is lexicon-only, and no espeak-ng package may enter the closure.**
  This is the constraint that rejected three otherwise-reasonable libraries, and it is a dependency-review rule, not a preference.
- **Models are downloaded on demand and never bundled**, matching what already happens for the Whisper weights and the Silero VAD assets.
- **The OS voice stays the always-available fallback and becomes the default**, so a fresh install speaks without a 116 MB download and without a network call.

### Remove the GPL closure

- [x] Add an `av` stub and `--exclude-module av` to the PyInstaller spec, so `faster_whisper` imports cleanly with no FFmpeg present.
  Assert in a test that `av.libs` is absent from a built bundle, because this regresses silently the next time the spec is regenerated.
  (Done: `packaging/rthook_av_stub.py` + `excludes=["av"]`; `build_desktop.verify_no_gpl_av` fails the build when PyAV re-enters, and `tests/test_kokoro_tts.py` pins the stub and the spec.)
- [x] Prove the STT path end to end on the built bundle rather than in the source checkout, since the source venv keeps its own working `av`.
  (Done 2026-08-24, against the live frozen app - `dist/swe-mux/swe-mux.exe`, confirmed by process path, not by asset hash. One round trip exercises both engines inside the bundle: `GET /api/voice/models/kokoro/preview` synthesised 3.95 s of speech, and POSTing that same audio back at 16 kHz mono to `/api/voice/transcribe` returned its exact text through faster-whisper `turbo` in 1.94 s of decode. `av.libs` is absent from the bundle and `edge_tts` does not appear in the executable at all, so both paths ran with no FFmpeg and no Microsoft endpoint. This is stronger evidence than the isolated-daemon check the "Docs, tests, and ship" item asked for, and closes that one too.)
- [x] Add a dependency-review gate that fails on a GPL or LGPL distribution entering the resolved closure without an explicit allowlist entry, with `pystray` as the only initial entry.
  The audit's whole lesson is that declared package metadata does not describe shipped binaries, so the gate must read the resolved closure.
  (Done: `packaging/license_audit.py`, with **two** allowlist entries rather than one - see the baseline correction below. The gate is deliberately split in two, because neither half can do the other's job:
  **Metadata half** (`license_audit.py`). Resolves the *distributed* closure by walking `uv.lock` from the runtime dependencies plus the `desktop` extra, with dev groups excluded so build-only `pyinstaller` - GPL-2.0-with-exception - never cries wolf on the one copyleft package that provably cannot reach a user. Dependency markers are evaluated against every supported platform rather than the running one, because the Linux artifact carries Linux-only packages whatever host the audit runs on; that also correctly drops `httpx2`'s Pyodide-only `httpx2-jsfetch`. Licenses come from installed `dist-info`, falling back to sniffing the shipped license *text* when a PEP 639 package declares nothing at all (`clr-loader` is MIT and says so only in a file). An undeterminable license fails the gate rather than passing as permissive.
  **Artifact half** (`build_desktop.verify_bundle_licenses`). Reads the built tree for the payloads by artifact name, because that is the only thing that catches a wheel whose metadata lies.
  **Drift.** `--write` needs the full closure installed and refreshes `THIRD-PARTY-NOTICES.md` plus the machine-readable sidecar `packaging/third_party_licenses.json`; `--check` needs no environment at all and reconciles that sidecar against both lockfiles, so a dependency entering, leaving, or moving fails on any machine. `tests/test_license_audit.py` runs the same reconciliation inside the ordinary suite, so a forgotten regeneration fails the gate rather than surfacing in a diligence review.)
- [x] **Baseline correction: `num2words` is a second LGPL package, and it is not in the 2026-08-17 audit.** It entered *with the espeak-free TTS replacement this phase performed*, so the audit that motivated the work predates it. `misaki/en.py` imports it at module scope to speak numbers, meaning there is no misaki English path without it, and it is a declared direct dependency in `pyproject.toml`. The espeak removal therefore traded one copyleft payload for a much smaller one rather than eliminating copyleft, and the phase's own exit criterion ("no GPL or unallowlisted LGPL component") was unmet at the time it was written. Allowlisted with `pystray`, under the same reasoning and the same relink treatment.
- [x] **`num2words` ships as replaceable source, which it previously did not.** LGPL requires that a recipient be able to substitute their own build of the library. `pystray` already satisfied this by accident of being in the spec's `collect_all` loop - `collect_all` defaults to `include_py_files=True`, so it lands as readable `.py` under `_internal/pystray/` - while `num2words` was frozen into the executable's archive and was not replaceable at all. Adding it to that loop is the whole fix, and `verify_bundle_licenses` now asserts the property on the built tree so it cannot regress into a notices file that promises something untrue.
- [x] **Recorded, not fixed: `av` is still in the resolved closure, and a wheel install takes it.** The bundle excludes PyAV outright and the build fails if it returns, so the frozen app is clean. But `faster-whisper` hard-requires `av>=11`, so `pip install swe-mux` resolves and installs 63 MB of GPL FFmpeg onto the user's machine. swe-mux redistributes none of it - pip fetches it from PyPI - and the phase's exit criterion is scoped to the built bundle, which is met. The gap is real for **Phase 11**, whose artifact is the wheel, and a diligence scan reading the transitive closure will flag it. The gate no longer hides it: `MISDECLARED` records PyAV's true license against its BSD-3-Clause declaration, `BUNDLE_EXCLUDED` records what keeps it out and what remains open, and `THIRD-PARTY-NOTICES.md` carries both under "In the dependency closure but not redistributed". Closing it means installing the stub into `sys.modules` before `faster_whisper` is imported in source mode too - the existing `rthook_av_stub.py` covers only the frozen app - and then dropping `av` with a uv dependency override. Deliberately not done here: it changes the voice subsystem's import path, which is not a licensing-paperwork change.

### Replace the TTS engine

- [x] Add a `kokoro` engine behind the existing `tts_engine` seam in `voice.py`, producing the same clip contract into `<data_dir>/voice/` and `voice_clips` (`kokoro_tts.py`).
- [x] Drive the model through a direct onnxruntime session over the three-input interface; do not take a wrapper dependency.
  (Verified live 2026-08-18 against the pinned files: RTF 0.56-0.67 CPU int8.)
- [x] Depend on base `misaki` plus `spacy` and `num2words` directly, construct the G2P with `fallback=None`, and add a construction-time assertion that no espeak module is present.
  (`en_core_web_sm` is pinned as an explicit dependency so the frozen bundle never pip-installs at import time.)
- [x] Add the compound splitter and a project lexicon for the vocabulary the measurement found unresolved, with an unambiguous last resort (spell the word) so an unknown token is never silently dropped from speech.
  (Replacements are re-verified recursively — the audit's own "pyproject" respelling produced "py", itself unresolvable.)
- [x] Synthesize bounded natural segments and begin playback on the first clip, so perceived latency is the opening thought rather than the whole summary at RTF 0.5.
  (The existing segmented-clip stream already provides this; kokoro synthesizes per segment.)
- [x] Remove `edge-tts` from the dependency set once `kokoro` and `sapi` cover both quality tiers, and migrate an existing `tts_engine: "edge"` config forward rather than failing on it (schema 26).
  (Current correction, 2026-08-25: schema 33 reintroduces Edge only as an explicit experimental
  external provider.
  The frozen artifact still excludes `edge_tts`; source users may install the `voice-edge` extra,
  and Settings now offers an explicit `uv`-managed staged install under the data directory while
  retaining a separate-Python override.
  The schema-26 migration remains, so an old install is never silently reconnected to Microsoft.
  A versioned service/privacy acknowledgement, explicit probe and catalog refresh, no silent
  fallback, and provider backoff keep the unsupported endpoint non-load-bearing.)
- [x] Change the default `tts_engine` to the OS voice, and make `kokoro` selectable only once its model is present.

### Model acquisition

- [x] Add a Voice settings surface that downloads the Kokoro weights and voices into the data directory with visible progress, pinned by immutable revision and per-file SHA-256, with explicit `not-downloaded` / `downloading` / `ready` / `error` state so a partial download can never be loaded (`voice_models.py`, `KokoroModelPanel`).
- [x] Fold this into the same first-use-download inventory Phase 11 already owns for the Whisper model and the Silero VAD assets (`NEW_USER_RELEASE_READINESS.md`), rather than inventing a second mechanism.
  (Done: the Kokoro weights are one row in that inventory, sharing the pinned-revision plus per-file SHA-256 contract and the explicit `not-downloaded`/`downloading`/`ready`/`error` states, so Phase 11's "make every first-use asset download explicit rather than silent" item covers all three with one mechanism.)
- [x] Account for the G2P dependency weight (about 110 MB installed, mostly spaCy) as a deliberate bundle cost, against 66 MB removed with `av`.
  If it becomes a problem, the lever is a lighter lexicon-only G2P, not a return to espeak.
  (Accepted as a net cost of roughly 44 MB installed. The trade is deliberate and is not primarily about size: it buys a phonemizer with no GPL payload and no `espeak-ng` binary anywhere in the closure, which is the constraint that rejected `kokoro-onnx`, `misaki[en]`, Piper, and KittenTTS. `THIRD-PARTY-NOTICES.md` records the Kokoro model as a download rather than a bundled asset, so the 116 MB of weights is not part of this figure.)

### License and notices

- [x] Add `LICENSE` (Apache-2.0), a short `NOTICE`, and a `CONTRIBUTING.md` carrying the DCO sign-off requirement.
  (`LICENSE` is the canonical Apache-2.0 text verbatim, copied from a shipped copy rather than transcribed. `CONTRIBUTING.md` reproduces DCO 1.1 in full, states why a DCO rather than a CLA, and carries the two dependency rules the gate enforces. Copyright is held as "The swe-mux Authors", which needs no edit if an entity is later formed.)
- [x] **Added, not in the original list: `TRADEMARK.md`.** Apache-2.0 §6 *reserves* trademark rights without stating a policy, which is a reservation nobody can act on. The file says what nominative use is always allowed (saying your thing works with swe-mux, forking and saying so, writing about it) and what needs permission (naming a modified version "swe-mux", using the mark as a commercial product's primary brand). It also carries the vendor half: swe-mux uses "Claude Code" and "Codex CLI" nominatively and keeps vendor logos out of its own branding.
- [x] Generate `THIRD-PARTY-NOTICES.md` from the lockfiles so it cannot drift, covering the Python closure, the frontend bundle, and the downloaded models (Kokoro Apache-2.0, Whisper MIT, Silero VAD MIT).
  Reproduce the Apache-2.0 NOTICE files, the MPL license texts, the Intel OpenMP terms, and the LGPL notice for `pystray` with a pointer to the build instructions that satisfy its relink condition.
  (Generated: 203 packages - 197 Python, 96 frontend after marker resolution - plus the four models, the modified `@xterm/*` copies, and Intel's `libiomp5md.dll` inside the `ctranslate2` wheel. Measured posture: 2 weak-copyleft (both allowlisted, both relinkable), 4 file-level MPL-2.0 (`certifi`, `py-vapid`, `pywebpush`, `tqdm`) needing license text only, 1 strong-copyleft excluded from the bundle (`av`), and **0 unknown**. Full license texts are pointed at rather than inlined, because every package redistributes its own inside its own distribution and the bundle and wheel both preserve that - a copy in this file is a copy that drifts.)
- [x] State that the shipped `@xterm/*` copies are modified, and name the two patch scripts.
- [x] Add the license section to `README.md` and resolve the `github.com/REPLACE/swe-mux` placeholder in `site/index.html`.
  (Resolved to `github.com/jatoran/swe-mux` in both the hero clone command and the footer, which also gained license links. **Assumption worth revisiting:** the account name is taken from the repository's git identity; publishing under an organisation instead means changing those three places together, which `site/README.md` §10 now records. A test fails if `REPLACE` ever returns.)

### Terms that are not licenses

- [x] Add a "not affiliated with or endorsed by" line covering Anthropic, OpenAI, and the other harness vendors wherever the landing page and README name their CLIs, and keep vendor logos out of the site.
  (In `README.md`, `NOTICE`, `TRADEMARK.md`, and the site footer. A test asserts the disclaimer appears in every file that names a vendor, so the four cannot drift apart.)
- [x] Document managed provider accounts as one operator switching between accounts they own, in `design/features/provider-accounts.md`, so the feature is not read as rate-limit evasion by someone reviewing the project cold.
  (New "Scope and terms" section. The framing is only credible because the boundaries are structural rather than promised, so each is stated as the design constraint it is: one live system login per provider with no concurrent multi-account execution; **no code path selects an account in response to a quota reading or an exhausted limit**, verified against the source - every "rotate" in `provider_accounts.py` is OAuth *token* refresh, not account rotation; no pooling or sharing; credentials sent only to the provider that issued them. The same section records that quota polling reaches the endpoints the provider CLIs themselves use rather than a documented public API, and that the shipped stale-retention failure mode is what keeps that dependency from being load-bearing.)
- [x] Record that OpenRouter and HuggingFace access run on the user's own key and quota under the user's own agreement with those services.
  (In the `README.md` licensing section, beside the vendor disclaimer, with "swe-mux proxies nothing and resells nothing".)
- [ ] **Open, and larger than this phase: the provider-endpoint question is a diligence exposure, not a licensing one.** Quota polling calls `api.anthropic.com/api/oauth/usage`, `/api/oauth/profile`, and `chatgpt.com/backend-api/wham/usage` (`provider_accounts.py`). Those are the CLIs' own internal OAuth endpoints reached with the user's token, not documented public APIs - structurally the same shape as the optional external `edge-tts` integration, where an undocumented consumer endpoint is reached with an embedded client token. It is not a dependency-license question and nothing here blocks publication, but for an investor or a vendor it is sharper than any package license, because a vendor can withdraw the endpoint or object to the use. Prefer a documented endpoint wherever one exists; keep both integrations explicitly degraded and never load-bearing.

### Docs, tests, and ship

- [x] Update `design/features/voice.md` (the engine set, the G2P constraint, the model download, the new default) and the audio quick reference in `.docs/CLAUDE.md`, which named edge-tts as the TTS path.
- [x] Update `design/features/desktop-shell.md` for the bundle contents that change; `technical/backend/packages.md` carries the new modules already.
  (Two new Packaging invariants there: the build-time license verification, and why `pystray`/`num2words` are in the `collect_all` loop for a licensing rather than a packaging reason. `technical/backend/packages/daemon-runtime.md` gained the `packaging/license_audit.py` row - the split map is where module rows now live, not `packages.md` itself. `.docs/CLAUDE.md` gained two routing entries: one for any dependency change, one for the project's own terms.)
- [x] Backend tests: the `av` stub satisfies imports and refuses use; the G2P refuses to construct if an espeak module is importable; the splitter and lexicon resolve the measured vocabulary against the real espeak-free misaki; and a partial model download is rejected (`tests/test_kokoro_tts.py`).
- [x] Gate tests (`tests/test_license_audit.py`, 44): classification with the Lesser-before-plain-GPL ordering that is load-bearing; the closure excluding dev-only packages and unreachable platform markers; drift against both lockfiles; the notices file being generated rather than edited; the allowlist and the relinkable set agreeing; PyAV classified by what it ships rather than what it declares; and **negative** coverage on all three artifact-half failures plus the false positive that a bare `*espeak*` glob would cause on misaki's own inert wrapper.
- [x] Verify on the isolated daemon that read aloud works end to end with `edge-tts` uninstalled, then commit and redeploy.
  (Verified more directly than planned, against the live frozen app rather than an isolated daemon - see the STT item. Read aloud reports `engine: kokoro`, `engine_available: true`, a `ready` model at the pinned revision, and 71 clips already produced; `edge_tts` appears nowhere in the executable and is absent from the closure. **The redeploy is deliberately not part of this change** and is the one step left: everything here is source, docs, and gates, and the running bundle already behaves correctly.)

### Phase 10.5 exit criteria

- [x] A built bundle contains no GPL or unallowlisted LGPL component, proven by a test over the resolved closure rather than by inspection, and `av.libs` is absent.
  (Both halves. The closure test is `tests/test_license_audit.py` over `packaging/third_party_licenses.json`, reconciled against `uv.lock` and `package-lock.json` with nothing installed; the artifact test is `build_desktop.verify_bundle_licenses` on every build. **Scope, stated precisely:** the criterion is met for the *bundle*. `av` remains in the *install* closure because `faster-whisper` hard-requires it, which is recorded rather than hidden and is a Phase 11 precondition.)
- [x] Default read aloud works with `edge-tts` absent, and the frozen artifact carries no Edge
  client or automatic Microsoft call.
  (`edge-tts` is absent from the distributed closure and frozen bytes.
  Schema 33 later added a source-only optional extra and an explicitly selected external bridge;
  opening Settings and every status GET remain network-free.)
- [x] No espeak-ng binary, data directory, or Python wrapper exists anywhere in the closure, and the G2P fails loudly rather than silently falling back if one appears.
  (`espeakng-loader` and `phonemizer-fork` are both absent from the closure; the bundle carries no espeak shared library. `misaki/espeak.py` does ship and is inert - the G2P is built with `fallback=None` and asserts at construction that no espeak module is importable - so `verify_bundle_licenses` matches espeak *shared libraries* rather than the bare name, which a test pins, because a broader glob would fail every build over that one harmless file.)
- [x] `LICENSE`, `NOTICE`, `CONTRIBUTING.md`, and a generated `THIRD-PARTY-NOTICES.md` exist, and the notices file regenerates from the lockfiles with no manual edit.
  (Plus `TRADEMARK.md`. A test asserts the notices file byte-matches what the generator produces, so a hand edit fails the gate.)
- [x] A fresh install speaks using the OS voice with no download, and Kokoro is one explicit, hash-verified download away.

## Phase 10.6 - The Mux assistant: conversational fleet operation

A conversational operator for the whole workspace: the control plane given a voice and a chat surface.
It is not another coding agent and not an observer.
It never writes code, never touches a PTY directly, and never acts on its own initiative.
It converses, reads the fleet through the existing read models, and drives the same command registry the operator's fingers do.
It is reachable three ways with one brain behind all of them: falling through from a wake-word utterance the deterministic grammar does not match, explicitly opening the assistant surface, or typing into the same dialog, because voice-first does not mean voice-only.

The research base: Home Assistant's hybrid intent routing (local templates first, LLM only on no-match) and its measured ~9.5x latency win from streaming the pipeline rather than the model (Voice Chapter 11); LiveKit's preemptive generation and semantic turn detection; the RealtimeSTT/RealtimeTTS chunked-synthesis pattern; Willow's 500 ms grammar-path budget.
The existing reflex path already embodies most of these and is not touched by this phase.

### Standing decisions extended, not reopened

- **No model in the command path** stands.
  The assistant is a fallback tier behind the deterministic grammar, never in front of it.
  A wake-word utterance the grammar or a fuzzy pass matches acts in ~300 ms exactly as today; only an unmatched utterance routes to the assistant instead of dying as a spoken refusal.
- **The model never emits an identifier and never executes** stands.
  It proposes command and target names; deterministic code resolves them against real entities exactly as a spoken grammar phrase would, and a failed resolution returns structured candidates rather than acting.
- **Voice compiles to the existing command registry** stands.
  The assistant's single action tool is a registry bridge; no parallel action table exists.
- **Freshness and confidence are computed by the system, never self-assessed** stands.
  Status figures in assistant replies come from tool results, and staleness qualifiers are appended from provenance fields by deterministic code.
- **Wake words remain the boundary for the ambient stream** and disappear only inside dialog modes, where a single addressee removes the ambiguity the marker exists to resolve.

### Routing tiers

1. **Tier 1 - the existing deterministic grammar.** Unchanged, offline, sub-second.
2. **Tier 2 - fuzzy match over the same compiled grammar** (token-level, rapidfuzz-style, milliseconds, offline).
   It absorbs STT noise ("shesh in three" -> "session three") before any model is paid for.
   A fuzzy match above threshold acts like a grammar match; below it, the utterance falls through.
3. **Tier 3 - the assistant.** Cloud model, tool calls, confirmation policy, dialog state.
   Offline, the lane reports itself offline and tiers 1-2 keep working.

### Interaction contract (final state)

- No wake word inside an assistant dialog: endpoint plus end-of-turn detection ends the user's turn, barge-in ends the assistant's, and a follow-up window (~8 s) keeps the channel open after a reply, longest after a question.
- Dialog state is daemon-owned - history, pending confirmations, list context - so any device resumes the same conversation and a dropped tab never orphans a half-confirmed action (unclaimed confirmations expire).
- Anaphora resolves against dialog state: "it", "the second one", "same for pixel lab".
- Replies follow the short-response protocol: answer first, one or two sentences, detail on request.
- Replies are dual-form from day one: display text and separately paced speech text, the existing `voiceQueries.ts` pattern.
- Streaming end to end: tokens stream to the surface, sentence boundaries are emitted as events (the seam TTS chunking consumes), actions execute as they resolve mid-stream, and slow tool fetches are narrated.
- The assistant may also speak as a delivery channel for attention ranking, inside the existing interrupt budget; it delivers, it never decides.
- Asked for something that is coding, it routes rather than attempts: queue to an existing session or offer a spawn.

### Trust policy

Per action class, user-tunable, enforced by the daemon rather than by prompt:

- Reads and per-device navigation: execute silently.
- Reversible mutations (queue a message, append to a note, spawn): spoken/displayed restatement with a cancel window before execution.
- Consequential mutations (send-now to a live agent, interrupt, end session): explicit confirmation.
- Provider approvals: the existing fingerprinted two-step challenge, unchanged; no bulk approval exists.
- Every assistant-executed action lands in the dialog history as an auditable "asked / ran / outcome" record.
- All writes travel existing paths (queue, spawn API, terminal action bus, guarded approval), so `NON_OVERRIDABLE_REASONS` and the approval floor bind structurally.

### Topology

Thin satellites around a daemon that owns the conversation, the intelligence, and every durable side effect:

- Device-local by necessity: microphone, speaker, and the 32 ms loop around the mic (VAD, endpointing, barge-in), plus earcons.
- Device-local by nature: UI actions (focus, drawer, navigation) and the composer-merging send path, dispatched to the device the current dialog turn came from (device presence identifies it); with no client attached these are the only actions the assistant refuses, and it says so.
- Daemon-owned: STT decode, TTS synthesis and clips, the assistant brain, dialog state, pending confirmations, the trust policy, and the budget ledger.

### Model stack

- **Brain: a configurable OpenRouter model slot**, default `openai/gpt-5.6-terra` (verified on the live catalog: tool calling, 1M context, $2/M in $12/M out), with `openai/gpt-5.6-luna` documented as the cheap alternative and fast open-weight providers as the latency alternative.
  Spend is recorded in the shared automation ledger under `builtin:assistant` with an independent daily budget.
  Explicitly no local LLM: a 1-4B model on desktop CPU emits one tool call in 3-8 s, slower than the cloud by 5-10x.
- **Prompt: two layers.** A static primer (swe-mux concepts, command catalog semantics, response protocol) behind provider prompt caching, and a per-turn snapshot of the fleet projection, project/session index with canonical addresses, focused session, and pending confirmations - a few KB assembled from read models that already exist.
  Most questions answer with zero tool round trips.
- **STT: unchanged** (Silero VAD, faster-whisper, two decode profiles); assistant-lane turns decode on the dictation profile.
  Moonshine and distil models remain configuration options for CPU-only hosts, not architecture.
- **End-of-turn detection for dialog modes: deterministic heuristics first** (trailing conjunction/filler lengthens the tail, a complete clause shortens it), with a small open turn-detector model as the measured upgrade path only if heuristics misfire.
- **TTS: the Phase 10.5 stack** (Kokoro-82M via direct onnxruntime, OS-voice default and fallback), with sentence-chunked streaming synthesis and GPU execution providers load-bearing rather than optional, and file-based earcons (endpoint ack, cancel-window tick) because a 0 ms acknowledgment is what makes 1-2 s of brain latency feel fine.
  The `tts_summary_model` role folds into the assistant.

### Tool surface

- Read tools, composite and one-call: fleet overview, session detail, transcript slice, history search, project notes, git status, queue state, scan-timeline highlights where enabled.
  Shaped like the mux MCP tools because that shape was designed for this consumer.
- One action tool: `run_command(name, args-by-name)` into the command registry, with deterministic name resolution and structured ambiguity candidates.
- Latency budget for the lane: endpoint ~350 ms + decode 150-250 ms + TTFT 300-800 ms + first TTS chunk 300-1000 ms, roughly 1.2-2.5 s end of speech to first audio, with the earcon at ~350 ms.

### Build order inside the phase: text-first, by design

The brain, context assembly, tool bridge, name resolution, trust policy, dialog state, and audit trail are modality-independent; a text chat exercises all of them and iterates faster than debugging through STT noise.
The text surface is permanent, not scaffolding.
Voice attachment afterwards is a wiring task because STT, TTS, endpointing, and barge-in already exist.
Day-one rules that make the adaptation clean: dual-form responses, confirmation as typed state rather than prose, sentence-boundary streaming events, and the short-response protocol from the first prompt.

### Surface

- The assistant lives in the existing voice/Talk overlay as a toggleable conversation view, enlarged for real back-and-forth visibility, with streaming replies, confirmation cards, and the dialog history.
- The same dialog is reachable with Talk off as a pure text chat.
- Voice mode adds the earcons, spoken replies through the existing segmented-clip pipeline, follow-up window, and barge-in.

### Implementation checklist

- [x] Daemon `assistant.py`: dialog store (SQLite, one worker thread, mirroring `voice_clips`), the bounded OpenRouter tool-calling loop (`openrouter.complete_tools`), context assembly, trust-policy engine, budget ledger entry `builtin:assistant`, typed `AssistantError` diagnostics that never touch PTY/session/transcript state.
  Streaming is sentence-granular by design here: each model call's text is emitted as `assistant_sentence` events, and the tool loop already delivers incrementally across calls.
  The token-streaming seam that left is now filled (`assistant_stream_replies`, default on): `complete_tools` takes an `on_content` callback, `_SentenceStreamer` releases each sentence as the model writes it, and the client speaks them into one open speech stream per turn instead of waiting for `assistant_turn_done`.
  A provider that will not stream is answered unstreamed and the sentence contract is unchanged, so the client keeps one path to speak from.
- [x] Tool bridge: daemon-side read tools over existing read models; `run_ui_command` proposals delivered to the turn's originating device over the event stream with bounded acknowledgement; deterministic name resolution with candidate lists.
- [x] HTTP surface: create/continue/interrupt a dialog turn, events (sentence boundaries, tool status, typed action/confirmation state), confirm/cancel a pending action, dialog history read.
- [x] Frontend: conversation view in the voice overlay (toggleable `talk`/`chat`, taller), streaming renderer, confirmation cards with the cancel-window countdown, UI-action executor over the command registry, tier-2 fuzzy matcher (`voiceFuzzy.ts`) in front of the assistant fallback, follow-up window, earcons, TTS attachment via the existing application-speech path.
- [x] Config: `assistant_enabled`, `assistant_model`, `assistant_daily_budget`, `assistant_max_output_tokens`, `assistant_context_messages`, `assistant_trust_reversible` (the consequential confirm floor is fixed).
- [x] Logging: turns, tool calls, resolutions, confirmations, and refusals logged with dialog and turn ids plus per-turn call/token/cost/elapsed totals.
- [x] Tests: tool-bridge resolution and refusal paths, trust-policy classes, restart action expiry, budget refusal, UI dispatch acknowledgement, dual-form helpers, event reducer, fuzzy tier (`tests/test_assistant.py`, `frontend/test/assistantEvents.test.ts`, `frontend/test/voiceFuzzy.test.ts`).
- [x] Docs: new `design/features/assistant.md`, routing-table entry, `design/features/voice.md` tier cross-reference, `design/interfaces.md` endpoints, backend and frontend `packages.md` entries.

### Phase 10.6 exit criteria

Landed on master 2026-08-18 (2e9d97d through 95ef033) and redeployed the same day; live
verification ran against the real fleet on the frozen app. Post-landing testing drove four
follow-up rounds now part of the phase: display titles everywhere the assistant speaks or
resolves a name, the deterministic UI-dispatch ladder, granular note edits, chat-mode
microphone ownership with deterministic spoken confirm/cancel, and the voice audition picker.

A fifth round (2026-08-19, on `worktree-assistant-voice-latency`, not landed) addressed how the
spoken lane *felt* rather than what it could do. Measured on the live daemon log: a reply was
buffered whole by the model and then buffered again by synthesis (3.5 s for 34 characters,
11.4 s for 419), and a proposal was spoken twice - the card's line, then the model's paraphrase
of it, the second hard-stopping the first mid-word because starting a stream halts the current
one. The round: token streaming plus per-sentence speech into one open stream per turn, a
tighter opening clip for application speech, daemon-side speech suppression once a card is open,
a terse daemon-built card announcement that restarts the cancel window when a device begins
reading it, a per-dialog action ledger and identical-proposal guard (a spoken "confirm" the
closed grammar missed used to reach the model and write the note a second time), a forgiving
spoken-verdict grammar, and no chat patience while a card is open.

- [x] A typed dialog can list the fleet, answer status questions with system-computed freshness, queue a reworded message, spawn a session, and walk a guarded approval, all through the registry bridge with the trust policy enforced daemon-side.
  (Live: fleet listings by display title, statuses with ages, spawns, note reads/edits under the cancel-window card, a genuinely stranded queue item surfaced. The guarded-approval walk itself is covered by the suite and the unchanged two-step flow, not yet demonstrated in anger.)
- [ ] The same dialog continues across two devices against one daemon-owned state.
  (Daemon-owned by construction and event-fanned to every client; the one remaining check is opening the same dialog from the phone once.)
- [x] An unmatched wake-word utterance reaches the assistant instead of a refusal, and the reflex path's measured latency is unchanged.
- [x] Assistant replies play through the existing segmented TTS pipeline with barge-in working, and the assistant lane reports itself offline cleanly with tiers 1-2 unaffected.
  (Live hands-free use 2026-08-18/19: spoken turns, spoken confirmation cards, chat-mode mic routing.)
- [x] Spend appears in the automation ledger under `builtin:assistant` and stops at the daily budget.
  (Live spend recorded per turn; the budget refusal fails a turn closed in the suite.)

## Phase 11 — Public packaging and release

This phase carries forward original Roadmap Phase 12. Source-checkout development remains
acceptable until Windows proving and the supported platform matrix are complete.
Licensing, third-party notices, and the copyleft removal are Phase 10.5's and are not restated here;
this phase consumes their result and must not publish an artifact before they land.
They landed 2026-08-24, with one item handed forward rather than finished: the wheel's install
closure still resolves `av`, which is the first item under "Artifacts and installation" and is a
precondition for publishing, not a nice-to-have.
Every dependency this phase adds passes `packaging/license_audit.py`, and any addition requires
regenerating `THIRD-PARTY-NOTICES.md` in the same commit.
The packaging and external-trial readiness gaps, and the CI matrices, are inventoried in
`CROSS_PLATFORM_FINDINGS.md`.

### Artifacts and installation

- [ ] **Precondition inherited from Phase 10.5: get `av` out of the wheel's install closure.**
  Phase 10.5 closed the copyleft question for the *bundle*, which is the artifact that existed
  when it was written. This phase's artifact is a wheel, and `faster-whisper` hard-requires
  `av>=11`, so `pip install swe-mux` resolves and installs 63 MB of GPL FFmpeg onto the user's
  machine. swe-mux redistributes none of it and the dependency declaration is defensible, but
  it is the first thing a transitive-closure scan flags, and shipping it would make the wheel
  the one artifact that fails the standard the rest of the project now meets.
  The fix is two small pieces, and it is a *runtime* change, which is why 10.5 declined it:
  install the existing `av` stub into `sys.modules` before `faster_whisper` is imported in
  source mode as well as frozen (`packaging/rthook_av_stub.py` covers only PyInstaller today),
  then drop the dependency with a uv override. Verify by transcribing on a clean install with
  no `av` present at all. `packaging/license_audit.py`'s `BUNDLE_EXCLUDED` entry is where this
  is recorded, and the entry should be deleted rather than edited once the override lands.
- [ ] Guarantee every wheel contains a frontend bundle from the same revision; fail release
  validation on stale or missing assets.
- [ ] Complete package metadata/governance: URLs, platform classifiers, changelog, release
  policy, security/contact path, and accurate capability documentation.
  **The license half is already done** (2026-08-24, with Phase 10.5): `pyproject.toml`
  declared no license at all, so the wheel shipped as all-rights-reserved metadata over an
  Apache-2.0 repository - the one way a permissive project publishes as proprietary by
  omission. It now carries a PEP 639 `license = "Apache-2.0"` expression plus
  `license-files`, verified on a built wheel as `License-Expression: Apache-2.0` with
  `LICENSE`, `NOTICE`, and `THIRD-PARTY-NOTICES.md` under `dist-info/licenses/`, so an
  installed copy answers the question without the repository. URLs, classifiers, changelog,
  release policy, and the security contact remain open.
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

## Phase 12 - Harness expansion

The registry ships five harnesses (`claude`, `codex`, `omp`, `pi`, `opencode`) against a market
that has many more, and the abstraction was built so that adding one costs a descriptor plus an
adapter rather than a branch in every consumer.
This phase spends that abstraction.

`HARNESS_EXPANSION_CANDIDATES.md` is the evidence base: one parity study per candidate CLI,
covering launch and terminal surface, conversation identity, record format, hook or extension
surface, MCP, headless probes, platform and account model, and the achievable capability tier.
It also records the candidates that were examined and rejected, so a rejection is visible rather
than a silence.
This phase does not restate any of it.

A candidate becomes a harness only through the ordinary contract in
`../design/features/backends.md`: a descriptor, an adapter family, a transcript dialect or a
declared absence, conversation discovery, replay fixtures meeting its derived corpus floor, an
adapter-matrix entry, headless probes, and a regenerated frontend seed.
Exempting a candidate from a gate means changing its descriptor to state the capability it lacks,
never weakening the gate.

### Integration

- [ ] Settle the four decisions the studies surface, once each rather than per harness, and record
  each outcome on the descriptors it governs: whether mux may install hooks into a config file the
  user also owns, whether a harness with no local conversation records is worth integrating,
  whether an approval channel that also decides the approval may be observed at all, and the two
  registry changes named next.
- [ ] Make the two registry changes before the first harness that needs them: a conversation store
  resolved under the working directory rather than a per-user `data_home`, and a harness with no
  resume concept, which the descriptor currently rejects.
- [ ] Work the candidate list in the document's recommended sequence, one harness per branch,
  ending each with the full registry contract rather than a launchable stub.
- [ ] Measure each candidate's open questions against a real install before writing its descriptor.
  Every study ends with the three that matter most for that CLI, and two candidates in the first
  pass already had vendor documentation contradicted by shipped code.
- [ ] Record the CLI version each harness was measured against in `TESTED_CLI_VERSIONS`, so the
  untested-pairing signal fires against evidence rather than against a guess.

### Phase 12 exit criteria

- [ ] Every harness added in this phase clears the declaration, contract, wiring, coverage, and
  behaviour tiers with no test weakened and no per-harness skip.
- [ ] `../design/features/backends.md` names the reached tier for each added harness, and
  `HARNESS_EXPANSION_CANDIDATES.md` records the reason for every candidate not added.

## Phase 13 - Integrated browser: one web surface the operator and the agent share

swe-mux has two browser-shaped surfaces today and neither one is a browser.
A **Preview** is a reverse proxy of a loopback listener rendered in a sandboxed iframe: its
registered origin is immutable per request, off-origin redirects are rejected so the route cannot
become a network proxy, and `allow-same-origin` is deliberately omitted so preview code cannot read
the parent application (`../design/features/processes-and-previews.md`).
Those three properties are what make it safe, and each of them independently forbids the thing this
phase is about: navigating to an arbitrary site, and letting an agent drive the page.
**Preview capture** already runs a headless Chromium through the optional `preview-capture` extra,
but it renders one screenshot and exits; it is never interactive and never agent-reachable.
The third fact is `../design/features/ghost-windows.md`, which exists only because agents launch
their *own* browsers and closes with the reason the sweep can never be retired: "swe-mux does not
control which browser stack an agent invokes."

This phase adds the missing surface. One real Chromium per session, owned by mux, driven over the
Chrome DevTools Protocol, rendered as an ordinary layout leaf on every client including the phone,
and reachable by the agent through the seams that already carry environment, MCP, and CLI.
It is the same move the terminal already made: the value is not "a browser", it is that a browser
becomes a multiplexed, per-session, remotely visible, lifecycle-bound object instead of a window on
one desktop that nothing else can see.

### The product decision this phase requires

Decision-gated capabilities currently lists "arbitrary HTTP/network destinations" as un-scheduled
work, and an agent-drivable browser is exactly that capability wearing a different shape.
This phase does not quietly cross that line; it narrows it and records the narrowing.

- [ ] Decide and record the destination boundary before any navigation code ships: a per-Project
  browser grant with the same `off`/`draft`/`granted` shape the Phase 7.6 session-control grant
  already uses, defaulting to the inert state, plus an allowlist mode that constrains agent-issued
  navigation to declared hosts while operator-issued navigation stays unconstrained.
  The operator typing a URL into a pane is not the gated act; an agent choosing one is.
- [ ] Amend the decision-gated entry rather than leaving it contradicted, naming the grant, the
  allowlist, the audit record, and the kill switch as the conditions under which agent-issued
  navigation is in scope.
- [ ] Record the exfiltration property plainly in the feature document: a browser is an outbound
  HTTP client that routes around every restraint placed on the PTY, so the allowlist is the only
  thing standing between a prompt-injected agent and an arbitrary POST. Page content is untrusted
  input on the same footing as a transcript.

### Engine and ownership

- [ ] Drive a real Chromium over CDP. Reject the two cheaper shapes explicitly and record why: an
  iframe cannot leave its origin or expose a DOM, and an embedded native webview reaches only the
  desktop shell, leaving the browser-tab and phone clients with no pane at all.
  The reference implementation that chose a native webview (cmux) returns `not_supported` for
  network interception, offline emulation, tracing, screencast, and raw input injection, which is
  precisely the half of the surface this phase wants.
- [ ] Reuse the existing optional Chromium rather than adding a second browser dependency.
  `preview-capture` already installs Playwright and resolves the standard per-user browser cache
  from a frozen desktop build; the browser pane extends that extra instead of introducing a
  parallel download, and stays a typed `{available: false, reason, install}` when it is absent.
- [ ] Launch the headless shell binary, not full Chrome under `--headless`.
  Playwright already defaults to `chromium_headless_shell`, which creates no top-level window, so
  the pane produces no ghost by construction. The sweep remains, because it defends against browsers
  mux did not launch.
- [ ] Own the process from the supervisor through the existing `spawn` message rather than adding a
  supervisor protocol message. A `PROTOCOL_VERSION` bump reaps every live session, and
  `PLUGIN_SYSTEM_FINDINGS.md` already settled that non-terminal panes ride the shipped `spawn`
  path for exactly this reason. A browser pane then survives a daemon restart the way a PTY does.
- [ ] Bound the resource cost the way every other loop in this repository is bounded: lazy start on
  first use, idle reap, a cap on concurrent instances, and a documented per-instance memory figure
  measured rather than estimated.

### The pane

- [ ] Add a non-terminal leaf kind to the recursive layout (`frontend/src/layout.ts` currently
  models terminals only) and let a browser leaf be tabbed, split, dragged, and restored like any
  other, including the mobile workspace projection.
- [ ] Stream `Page.startScreencast` frames over the existing session WebSocket and acknowledge them
  with `Page.screencastFrameAck`, which supplies the coalescing contract for free: drop intermediate
  frames, never queue, so a slow link degrades to fewer frames rather than to lag.
- [ ] Send a lossless settle frame once the page has been quiet briefly, so a page at rest is
  pixel-accurate rather than showing compression artifacts on text the operator is trying to read.
- [ ] Forward pointer, scroll, key, and text input through `Input.dispatch*Event`, arbitrated by the
  device-presence and input-ownership rules already governing a shared PTY
  (`../design/features/terminal-input.md`), so two devices cannot fight over one page.
- [ ] Route between the two surfaces instead of merging them: a Preview registration offers "open in
  browser pane" for the cases the proxy cannot serve (a client-side router that ignores
  `window.__MUX_PREVIEW_BASE__`, an off-origin OAuth hop), and the browser pane offers the reverse
  for a loopback URL that has a registration. Preview keeps its boundary unchanged.

### The agent surface

Three injection tiers, in descending reliability, all riding seams that already exist.

- [ ] **Environment**, at spawn (`agent_environment.py`): the session's CDP endpoint and browser id,
  registered as protected keys that a launch profile cannot override, matching how the MCP token is
  already handled. This is the floor that works for a harness with no MCP and no skill format.
- [ ] **MCP**, per harness (`adapters/`): browser tools on the existing mux MCP server, scoped to
  the caller's own session by the same per-session token, and DAG-gated by the Project grant.
  No new transport and no second authorization boundary.
- [ ] **CLI**: `mux browser navigate|snapshot|click|type|console|network`, reading the environment
  variable, because bash is the one interface every harness has and MCP support is uneven.
  The reference implementation shipped its entire browser surface this way and never needed MCP.
- [ ] Return accessibility-tree snapshots with stable per-snapshot element refs and act on refs
  rather than on model-authored CSS selectors, with a snapshot-after option on mutating actions so
  the agent sees the result of its own click without a second round trip.
  Refs go stale on navigation and DOM change, and saying so in the tool description is what keeps an
  agent from silently acting on the wrong element.
- [ ] Carry the instruction through the shipped Agent Context surface rather than adding a new
  writer to instruction files. Phase 6's approved boundary is an explicit, previewed, one-time
  overwrite, and continuous instruction sync is decision-gated; a browser announcement does not get
  to reopen that.

### Boundaries and isolation

- [ ] Default to an ephemeral per-session profile. Do **not** import cookies from the operator's
  real browser profile, and specifically do not adopt the reference implementation's posture, where
  detecting a coding agent in the environment is what *suppresses* the import confirmation. Reusing
  a credentialed profile means a prompt injection on any page acts as the operator on every logged-in
  service, and that is an operator-initiated, per-Project, explicitly confirmed act if it is ever
  offered at all.
- [ ] Bind CDP to loopback on an ephemeral port with a per-instance token, never to the tailnet
  interface. An open CDP port is a full-take vector for any local process and is reachable from a
  malicious page by DNS rebinding against `127.0.0.1`; the remote-access boundary in
  `../design/features/remote-access.md` governs what the phone reaches, and it reaches the pane, not
  the protocol.
- [ ] Confine downloads to a per-session directory under the data dir rather than letting a page
  write anywhere the daemon can.
- [ ] Audit every agent-issued navigation and every allowlist refusal as an observation, and give
  the whole subsystem a config kill switch plus a per-Project one, per the completion policy's rule
  that every automatic action has provenance, bounds, auditability, and a kill switch.
- [ ] Keep console errors and failed requests as the payoff: they are the signal the attention,
  notification, and status surfaces already know how to consume, and they are what a screenshot
  cannot give. "This session is idle and its page is throwing" is the thing no external browser
  automation can report.

### What this phase does not do

- [ ] Do not redirect the repository's own test suite. A project's Playwright configuration owns its
  browsers, fixtures, isolation, and parallelism; steering `npm test` into one shared session browser
  breaks test isolation and produces wrong answers. This surface replaces an agent's ad-hoc poking,
  not a test runner. State it in the feature document, because the temptation is real and the failure
  is silent.
- [ ] Do not build a second automation vocabulary. The CDP endpoint stays attachable, so
  `playwright`, `playwright-mcp`, and `chrome-devtools-mcp` can connect to the same instance; a
  bespoke scriptable surface that no external tool can attach to is the trap the reference
  implementation is currently filing issues against itself to escape.
- [ ] Do not extend the browser to non-loopback hosts as an execution target. Reaching a remote
  service in a page is ordinary browsing; reaching a remote *host* is the multi-host control plane
  that remains decision-gated.

### Phase 13 exit criteria

- [ ] The destination decision is recorded, the decision-gated entry is amended, and the default
  Project state grants an agent no navigation authority.
- [ ] A browser leaf tabs, splits, restores, and renders on desktop and phone, and survives a
  session-preserving daemon restart with its page intact.
- [ ] An agent in every registry harness reaches the browser through at least the environment tier,
  and through MCP wherever the harness supports it, with no manual per-session configuration.
- [ ] Navigating, snapshotting, acting on a ref, and reading console and network activity are covered
  by tests against local fixtures rather than public sites, including one test that a stale ref fails
  loudly instead of acting on the wrong element.
- [ ] The allowlist refuses an off-list agent navigation, the refusal is audited, and both kill
  switches stop the subsystem without reaping sessions.
- [ ] `../design/features/` carries a browser-pane feature document, `../CLAUDE.md` routes to it, and
  `../design/features/processes-and-previews.md` states the Preview/browser division so the two are
  not re-merged by a later change.

## Phase 14 - Land queue: serialized branch landing

Landing a finished worktree branch is three fixed commands today - merge the trunk into the branch
inside its worktree, run the repository's verification task, fast-forward the trunk from the
primary checkout - and the operator serializes them by hand whenever several agents finish at once.
The sequence is mechanical until it is not: only a merge conflict or a verification failure needs
intelligence, and both belong to the branch's own agent, which holds the context.
This phase makes the daemon own the mechanical part, so N parallel branches land unattended in
sequence and the operator touches only the one that genuinely conflicts.

The move is the one the rest of the control plane already made: deterministic code executes a
fixed vocabulary through existing trust boundaries, and a model is never asked to choose a git
operation.
Fast-forward-only is what makes the trunk step safe for a machine - Git refuses it on divergence
and refuses to overwrite overlapping local changes, so the pipeline cannot lose work by
construction, which is the same property that already makes it the one merge shape permitted
outside a worktree.
Every prerequisite is shipped: worktree tooling (`../design/features/git.md`), the Phase 7.6
`off`/`draft`/`granted` authority grant, the Phase 4/5 queue and its readiness contract, the
bounded daemon-owned subprocess runner worktree bootstrap already uses, the project-actions
exact-content approval model, and Tier 0 fact capture.

### Decisions taken before the build (2026-08-20)

- **The verification task is not a Project Action.** The two mechanisms it would have needed are
  both refused by their own designs: an action's cwd is bounded by the canonical Project root and
  is *deliberately* denied the sibling-worktree widening that spawns get
  (`../design/features/project-actions.md`), and an action step becomes a one-shot terminal
  session rather than a captured subprocess. The pipeline needs an exit code and bounded output
  inside a tree that lives outside the Project root, which is exactly what `worktree_setup.py`
  already does for bootstrap.
  The verify runner therefore mirrors setup - `[worktree].verify_command` in
  `.swe-mux/config.toml` else the executable `.worktree-verify` convention - and borrows only the
  *trust* half from project actions: a machine-local SHA-256 over the resolved command or script
  bytes, keyed by canonical Project root, un-approved by any edit to it.
  Say the widening out loud rather than inheriting it quietly: worktree setup runs once per
  human-initiated create, while verify runs repeatedly on an agent's request, and the fingerprint
  approval is what makes that acceptable. An agent that edits the verify script un-approves it,
  so an agent still cannot approve its own command.
- **One trunk per Project.** A land targets the Project's primary checkout on its effective
  comparison ref (`git_review.resolve_comparison_ref`, the same inference the Git drawer and the
  session monitor already share), and any other target refuses. One trunk is one serialization
  domain, and fast-forward-only is only a safety proof against a trunk the daemon can identify.
- **The agent-facing request ships in this phase.** Operator-only would leave the operator doing
  the serializing, which is the whole thing the phase removes. The `draft` default is what makes
  that safe to ship at once.
- **A busy worktree waits rather than bouncing.** An agent that requests a land and keeps working
  is the common case, not an error, so the item holds in a visible `waiting` state and retries
  until a bounded timeout, and only then hands back.

### The request, not the action

- [x] Add a land request as the only entry point: a `mux.request_land` MCP tool scoped to the
  caller's own session by the existing per-session token, and an operator Land control on the
  worktree surface. Neither performs the land; both enqueue it.
- [x] Gate agent-initiated requests behind a per-Project grant with the Phase 7.6 shape -
  `off`/`draft`/`granted`, default `draft` so a human approves each land - registered as its own
  `land_queue` automation in the enablement DAG and capped by an hourly budget.
  Its own automation id rather than a second meaning for `session_control`: that one acts on a
  *session*, this one acts on a *repository*, and they deserve separate switches and separate
  budgets.
- [x] Bind the request to what it was made about: the worktree root and the branch tip OID at
  request time, recorded as evidence of what was asked for. The tip is re-read at each step
  rather than frozen - an agent that requests a land and keeps working is the case the hold
  exists for, and refusing on any movement would contradict it. The refusal that protects
  something is narrower and is in the pipeline below: a branch that moved past the OID that
  *verified* never lands, because that is the one movement that would put unverified code on
  the trunk.

### The pipeline

- [x] Serialize per trunk: one land in flight per Project primary checkout; further requests queue
  in arrival order. Verification is measured parallel-safe across worktrees, but `advance` re-runs
  every remaining item after each land anyway, so concurrency buys only the first item and is left
  as a config ceiling the store's shape permits rather than as v1 behaviour.
- [x] Check preconditions before **every** mutation rather than once at enqueue, and fail closed:
  the worktree is clean on tracked paths, its branch tip still matches the bound OID, the resolved
  trunk is the main tree and not a worktree (the `--absolute-git-dir` versus `--git-common-dir`
  test, never a name comparison), and no live session rooted in that worktree is `working`.
  The last one is the hazard the pipeline exists inside: reconcile writes into a checkout an agent
  owns and may be mid-turn writing to, and `delivery_readiness` already answers that question with
  the same fail-closed predicate `interrupt` gates on.
- [x] Hold a busy item in a visible `waiting` state with its reason, retrying on the ordinary tick
  until a bounded timeout; only the timeout hands back. A wait is a state a human can read, never
  an invisible sleep.
- [x] Reconcile: merge the trunk into the branch inside its worktree. A conflict stops the item,
  records the conflicting paths, and hands the request back to the originating agent session as a
  bounded deterministic template through the Phase 5 queue - a draft by default, promotable by the
  ordinary auto-delivery grant like any other item.
- [x] Verify: run the Project's declared verification command inside the worktree through the
  daemon's own bounded-subprocess runner, under the fingerprint approval above, and record the
  exact commit OID that passed.
  Run it verbatim and read its real exit code: never pipe it, never trim it, never wrap it in a
  shell that can replace the status. A gate command trimmed inside its own pipeline has already
  shipped a failing suite green in this repository once.
  Running under the daemon's `base_session_env` rather than an agent shell also removes the known
  intermittent false failure `.worktree-verify` shows in an agent shell, by construction.
- [x] Skip re-verification only for a reconcile that reported nothing to merge. A verified OID
  still standing is the one case where re-running the gate proves nothing.
- [x] Land: fast-forward-only merge in the primary checkout, refusing when the branch moved past
  the verified OID or the checkout is dirty on touched paths. A refusal is a reported failure,
  never a retried force.
- [x] Advance: after each successful land, re-run every remaining queued item from reconcile
  against the new trunk automatically, so one landing does not strand the other agents' now-stale
  reconciles.
- [x] Record each step as Tier 0 facts with the request's provenance, so a land is auditable end to
  end: who asked, what verified, which OID moved the trunk.
- [x] Keep the queue itself machine-local, like scheduled runs: a land queue committed to a
  repository would arm itself in every clone and every worktree of it.

### Boundaries

- [x] The pipeline never resolves a conflict, never rebases, never forces, and executes no
  model-chosen command; its git vocabulary is fixed, and fast-forward-only is the only trunk merge
  shape.
- [x] A verification failure hands back like a conflict, with the failing output attached. Retries
  are bounded and explicit - at most one, and only when configured - never silent, because a flaky
  gate that loops is worse than one that stops.
  A retry that fails *differently* from the first attempt stops rather than retrying again: two
  unlike failures are evidence about the gate, not about the branch.
- [x] A handback body is bounded and redacted before it becomes an agent's prompt: the tail of the
  output at a few KiB through the same `looks_like_secret` gate every other excerpt uses, keyed by
  the request id as its `correlation_id` so the queue's existing uniqueness index dedupes repeats.
- [x] The daemon is the single writer for the trunk merge, which also closes the race two sessions
  otherwise have over the primary checkout's one index.
- [x] Kill switches at the config level and per Project, per the completion policy; `off` is inert
  and produces no queue writes at all.
- [x] No decision-gated entry is crossed: execution authority is the already-trusted verification
  task plus the fixed git vocabulary, and the conflict handback rides Phase 5's bounded
  deterministic templates rather than a new agent-to-agent path.

### Phase 14 exit criteria

- [ ] Three finished branches requested together land in sequence under `granted`: the second
  reconciles against the first's result automatically, and the conflicting third is handed back
  with its conflict list while the trunk stays clean.
  (Covered against real repositories and real worktrees in `tests/test_land_queue.py`; still
  owed a live run on the isolated daemon with real agent sessions occupying the worktrees,
  which is the half a test with no live session cannot prove.)
- [x] The refusal paths - divergence, dirty checkout, branch moved after verify, verification
  failure, a trunk that resolves to a worktree - are covered by tests, and each reports rather than
  retries.
- [x] A worktree whose session is mid-turn holds in a readable `waiting` state and lands once that
  session settles, and only a bounded timeout converts the wait into a handback.
- [x] The verify command is refused until its exact bytes are approved, and editing it un-approves
  it, so no agent can author the command its own land runs.
- [x] The grant defaults to `draft`, `off` is inert, and every land carries provenance, audit, and
  budget accounting.
- [x] `../design/features/` carries a land-queue feature document, `../CLAUDE.md` routes to it, and
  the prompt-queue and mux-mcp documents name the handback template and the request tool.

## Phase 15 - Voice assistant follow-through: caching, patience, budgets, and reach

The assistant now streams speech progressively in daemon-batched natural clips, announces a card exactly once, reports its round
budget honestly, and can stage a prompt into a new session without sending it.
This phase is the set of follow-on items the 2026-08-20 voice sessions surfaced, scoped with the
operator on 2026-08-20.
Each section is independent and separately landable.
None introduces a new authority: every executing path already exists and keeps its trust
boundary, which is what makes the whole phase safe to parallelize.

### Decisions taken at scoping (2026-08-20)

- **No multi-model tier inside the assistant.** The considered design - a cheap default model, an
  escalate tool, per-model tool-catalog subsets, the assistant as its own orchestration harness -
  is dropped rather than deferred.
  The assistant keeps one configured model, and heavy work belongs in a real harness session the
  assistant spawns, which is the primitive swe-mux already has.
  Trimmed per-model tool catalogs are specifically rejected: a capability the model cannot see is
  a capability it denies having, which is the exact `seed_text` invisibility failure just fixed.
- **Phone-in-pocket TTS delivery stays out of scope.** Push notifications already carry "a reply
  is ready"; background audio on a locked phone is its own project and not this one.
- **No spoken read-back of action output.** A finished project action reports success or an issue
  flag, never its output stream; readback was rejected as spam at scoping.

### Prompt-cache accounting for assistant turns

Today the assistant re-sends its primer plus the dialog window on every model call with no
`cache_control` anywhere, so Anthropic-routed models get zero caching and nobody can see what any
model is hitting.

- [x] Mark the stable prefix (primer + tool definitions) with an explicit cache breakpoint when
  the resolved model routes to a provider that requires one (Anthropic); implicit-caching
  providers need no request change.
  Shipped as `cache_stable_message` / `needs_explicit_cache_control` in `openrouter.py`, applied
  to the primer only: the provider orders tool definitions ahead of the system prompt, so one
  breakpoint covers both, and a second one over the per-round tail would be a cache write billed
  at a premium and never read back.
- [x] Keep the per-round budget line trailing, as it is today, so the prefix stays cache-stable
  across the up-to-14 rounds of one turn; treat any future prompt change that inserts ahead of
  the primer as a cache regression.
  Both halves of that rule now have a test: the primer is asserted byte-identical across a
  turn's rounds, and the budget line is asserted last.
- [x] Record `cached_tokens` from the usage payload into the assistant spend ledger and surface
  the hit rate beside spend, so caching is measured rather than assumed.
  `automation_budget_ledger.cached_tokens` (schema 9, backfilled to 0), carried through `spend()`
  and `spend_breakdown` with `input_tokens` as the honest denominator, and drawn as a `cached`
  column plus a `prompt cache` tile in `AutomationSpendView` - the same component the Automation
  dashboard and Resources → Tokens both draw.

### "New conversation" by voice

- [x] Deterministic command-registry aliases ("mux, new conversation" / "clear context") that call
  the existing new-dialog path (`AssistantPanel` already has the button; voice has no route to
  it).
  Shipped as `assistant.newConversation`; both surfaces now route through one `startNewDialog`,
  which announces `mux:assistant-dialog-reset` rather than letting the panel clear itself.
- [x] No confirmation: the act is reversible, because the old dialog stays readable in the panel,
  and the spoken reply says both things - context cleared, old conversation still there.
  "Stays readable" was a claim the panel did not yet honour - it cleared its view outright - so
  the cleared conversation is now kept in a collapsed `previous conversation` disclosure.
  Without that the reply would have been describing a reversibility the operator had no way to
  reach, which is the one thing that would have made the absent confirmation unsafe.

### Unfinished-utterance deferral

The defining voice-agent complaint: the operator rushes because a pause becomes a reply.
The design is deterministic-first, because a model-arbitrated "are you done" loop is the
round-trip spam the feature exists to avoid.

- [x] A completeness heuristic runs before a chat turn is dispatched: an utterance ending
  mid-clause (dangling conjunction, preposition, or article) earns one adaptive patience
  extension instead of submitting.
  At most one deferral per utterance, so unbounded round-trips are structurally impossible.
- [x] Queue-merge stays the safety net for fragments that slip through: the second breath merges
  into the pending turn, and barge-in already silences a reply to fragment one.
- [x] The model is never instructed to return nothing - incomplete fragments are handled before
  the model, not by it.
- [x] The primer teaches the model to suggest hold/proceed once when the operator is clearly
  thinking aloud, rather than emulating it.
- [x] Every deferral is logged with the trigger token, so the heuristic's false-positive rate is
  measurable before anyone tunes it.

Shipped (`design/features/voice.md`, endpointing). `utteranceCompleteness.ts` is the pure rule
set, `utteranceDeferral.ts`'s clock-injected `DeferralPen` owns the one-deferral-per-utterance
decisions, and `ConversationControl.tsx` keeps the effects; the extension is the operator's own
`voice_chat_patience_ms` (floored at 600 ms, capped at 5 s) rather than a second knob, and it
also raises the gate's `endpointPatienceMs` while a fragment is held so the second breath is not
itself chopped in half.
Two structural guards keep false positives down without a parser - questions strand prepositions
legitimately, and prepositions that double as verb particles need a five-word clause - and the
resolution report (`POST /api/voice/deferral-diagnostic`) carries the trigger plus the outcome
that judges it, since `merged` versus `submitted` IS the false-positive rate.
The release timer re-arms while speech is still arriving or an utterance is mid-decode, bounded
by a hard 15 s hold ceiling.

### Token caps and cost caps everywhere

- [x] One budget shape for every cap: `{tokens?, usd?, mode: tokens | usd | either}`, where
  `either` trips on whichever is hit first.
  `src/swe_mux/budget.py` owns the shape and both comparisons - `spent_out` inclusive, because
  the money is already gone, and `would_exceed` strict, because a preflight estimate is a
  conservative maximum and refusing a call that fits exactly would refuse calls that fit.
  The mode's axes are *required*, so a cap can never claim to enforce a unit it has no figure
  for; the other axis may still hold a value and is never consulted, which is what lets the
  control keep a number the operator is only temporarily not enforcing.
- [x] One shared budget control renders it, and every budget setting - assistant daily, scan
  budgets, automation bounds - gets the choice, including settings that today carry only one
  unit.
  Eight caps, inventoried once in `BUDGET_SPECS` and listed in `design/features/budgets.md`.
  Two of them earned a Settings control they never had: the Project context card's budget was
  config-file-only, and a cap with no control cannot be offered a choice at all.
  Rate limits (`automation_hourly_call_cap`, `attention_daily_interrupt_budget`, the hourly
  message and land budgets) are deliberately **excluded**: they count acts, never read the
  ledger, and asking the operator to denominate them in tokens or dollars would be asking them
  to price something that has no price. Per-call ceilings are excluded for the same reason.
- [x] Enforcement reads the spend ledger, which already records both tokens and USD per call.
  Every site now calls one of the two shared comparisons against `AutomationStore.spend()`, so
  a refusal and the figure drawn beside it cannot disagree.
  The ledger gained the fact it was missing: `cost_known` (schema 11) distinguishes *unmeasured*
  from *free*, because a bring-your-own endpoint reports no `usage.cost` and recording those
  calls at `$0.00` would leave a dollar cap looking enforced while approaching nothing.
  What a dollar cap does about it is **stated**, not silent: it counts reported cost and nothing
  else, never guesses, never refuses the call (a local model has no bill, and failing closed
  would switch off every local-endpoint install), and says so in three places - the control
  warns while the dollar axis is selected against a provider whose `reports_cost` is false, the
  verdict carries `cost_blind`, and every total over a window with unpriced calls is drawn as a
  floor with the count. `either` is the honest configuration there, and the token axis is the
  backstop that binds.
- [x] Migration maps each existing cap onto the mode matching its current unit, so no cap
  silently loosens.
  The automation and scan-timeline daily ceilings checked both units, so they arrive as
  `either`; the scan run cap as `tokens`; the four dollar caps as `usd`.
  The case a naive migration loses is a config that set one half of a pair: the other half was
  still enforced, at a dataclass default the file never mentioned, so each `BudgetSpec.default`
  carries that figure and fills it. Schema 23's uplift still composes, because it is applied
  while the pre-`Budget` scalars are still visible.
  `tests/test_budget_shape.py` pins the mapping against a longhand table of what the old code
  compared, rather than deriving its expectation from `BUDGET_SPECS`.

### Global TTS switch and focus-driven playback

Read aloud is currently three independent switches (per-session mode, device autoplay, tts chip)
with no master, and the note behind this section is an overwhelm complaint as much as a feature
ask.

- [x] A global read-aloud master switch, shaped like the assistant's: off means no session
  generates or plays. `tts_enabled` is that master and is now checked on manual `generate` as
  well as on the automatic path and `speak`, so it governs every path rather than the ones that
  happened to consult it.
- [x] Per-session participation stays and narrows to "does this session generate" - some sessions
  should never speak. An explicit human "speak this reply" is still honoured for an `off`
  session: that is an instruction, not participation, and the shipped `tts_default_mode` is
  `off`, so refusing there would have silenced the speak button install-wide.
- [x] Playback policy becomes global and focus-driven: the focused session auto-plays, unfocused
  sessions generate and hold their clips (already durable in `voice_clips`), and held clips
  surface as ready-to-play rather than auto-playing over whatever the operator is doing.
  A Voice Comms pin is the one override, because that mode is a conversation with an agent the
  operator may not be looking at.
- [x] The Settings surface presents the three layers as one legible policy: master, per-session
  participation, this-device autoplay.

### Global usability audit session

- [x] Spawn a dedicated audit session - prompt staged for operator review, per `stage_text` -
  charged with an app-wide first-use/overwhelm audit: every surface with complex functionality,
  not voice alone, drawing on the continuity project's UI/UX-psychology documentation and its own
  research, producing a written report with ranked recommendations.
  This is a session to run, not code to write; it is in the phase so it is not lost.
  Delivered 2026-08-20 as `USABILITY_AUDIT_2026-08-20.md`: twelve ranked findings, each anchored
  to a file and line, split into quick polish and needs-design.
  The report is the deliverable and nothing in it is scheduled here; acting on a finding is a
  separate decision.
  Two findings are deliberately handed to the in-flight gated-feature enablement work rather than
  acted on (Run with no harness enabled, and the assistant's off-state naming a Settings tab that
  does not exist), and the report asks only that the Voice-tab split be sequenced before the
  global TTS master switch in this phase.

### Bring-your-own LLM endpoint, and gating on a verified provider

STT (faster-whisper) and TTS (Kokoro) are already local, so the remaining gap is the language
model.

- [x] A custom OpenAI-compatible endpoint - `{base_url, api_key, model}` - at the
  `OpenRouterClient` seam, which covers llama.cpp, Ollama, vLLM, and LM Studio with one shape;
  OpenRouter stays the default and other users' routing is untouched.
  `llm_endpoint.py` is the substitution for the origin constant: one `LlmEndpoint` says where a
  completion goes *and* what that destination may be assumed to support, so the three things a
  custom endpoint does not have are absent rather than wrong - no OpenRouter `provider` routing
  block, no `/generation` cost lookup (absent cost is unknown, never zero), and no catalog
  filter that would report a loaded model as no models at all.
  The endpoint is re-resolved per request, so a corrected base URL takes effect on the very next
  call, which is the verify press itself.
  A custom endpoint serves one model and every model setting in the app names an OpenRouter id it
  has never heard of, so the client redirects all of them at the seam and Accounts says so above
  the routing index rather than letting it list ids nothing will request.
  The origin remains **install configuration and never a caller parameter**, which is the property
  the old constant was protecting and the reason agent-chosen destinations stay decision-gated.
- [x] A verify action per configured provider: one tiny completion, output shown to the user,
  verified status recorded durably; an edit to the endpoint un-verifies it.
  Per *configured* provider rather than the active one, because an operator proving a local
  endpoint wants to prove it before switching the install onto it.
  The record's fingerprint covers the whole triple, so the un-verify is a property of the data
  rather than a rule every write path has to remember - it holds for an edit made by hand while
  the daemon was down, and `tests/test_custom_llm_endpoint.py` asserts it per field, since a
  fingerprint over two of the three would pass a single-case test.
  A failed verification records nothing and does not disprove a previous success: an endpoint
  unreachable this minute has not been disproven, and deleting the record would turn a network
  blip into a Project-wide switch-off.
  OpenRouter needs no separate act - storing its key already tests it against an origin swe-mux
  ships - so no existing install loses its automations on upgrade.
- [x] LLM-dependent automations gate on a verified provider through the existing enablement
  dependency graph, and an unverified provider reads as the reason the switch is inert rather
  than as a silent failure downstream.
  `Automation.needs_llm` is kept apart from `spends` because a model on the operator's own
  machine is a dependency with no bill, and `resolve(..., llm_ready=False)` subtracts those from
  `enabled` into `Resolution.unverified` - not from `requested`, so `catch_me_up` and
  `live_blockers`, which call nothing, keep reading records that already exist when somebody
  rotates a key.
  `unverified` is its own field rather than a `blocked` entry because `blocked` values are ids a
  grant can switch on, and no automation's enabling fixes an unproven endpoint.
  The provider is a *value* rather than a switch, so the surfaces held back by it link to
  Settings → Accounts instead of duplicating the grant system: a `GrantGate` over a `needs_llm`
  switch discloses it beside `spends` and still applies the grant, since the opt-in is a real
  permission and withholding it would mean granting twice.
  **Not covered, deliberately:** the install-wide model-backed *features* that are not registry
  automations - the assistant, read-aloud summaries, the Project context card - still fail at
  their own call rather than at a gate. They have no enablement DAG to gate through, which is
  what this item scoped itself to; `provider.llm` is on the status payload for whoever closes
  that separately.
  Caching gets the same treatment for the same reason: `cache_policy` is `unknown` for a custom
  endpoint, so no breakpoint is sent *and* no implicit hit is assumed, and a zero in the ledger
  reads as unmeasured rather than as a regression.

### Assistant reach: project actions

The hard part is already built: `run_action` executes only human-approved exact bytes, so the
assistant inherits that boundary wholesale and cannot run anything a person did not approve.

- [x] A list tool over the Project's actions, showing each action's approval state.
  `list_project_actions` reports approval **per action**, because trust is per source file and
  one unapproved file leaves the rest runnable.
- [x] A run tool for approved actions only, confirmation-carded per the existing trust policy;
  an unapproved action names the file a human must review, exactly as the MCP surface does.
  `run_project_action` classifies as *consequential* rather than reversible - a build or a
  deploy is not undone by a tombstone, so it never runs under an `auto` trust setting - and the
  refusal happens at preflight, so nothing pends that the executor would refuse. Resolution,
  the trust check, and input validation are one shared implementation (`preview_action_run`).
- [x] The outcome is a terse notification: success, or an issue flag when the exit code is
  nonzero or the output tail looks unhealthy - never an automatic read-back of output.
  A bounded watch over the step sessions reports one sentence through `assistant_notice` (the
  one assistant event belonging to no turn, since the exit code arrives after the confirmation).
  The unhealthy-tail markers are deliberately narrow: bare "error" and "failed" appear in
  healthy builds, and a flag that fires on green runs is one the operator learns to ignore.

### Assistant reach: spawn with a specified model

- [x] An optional `model` argument on `spawn_session`, mapped per harness by the adapter (claude
  and codex take `--model`; a harness with no model flag refuses honestly at the card, before
  anything spawns).
  The per-harness declaration is `HarnessDescriptor.model_selection` rather than adapter code:
  it carries the argv, the aliases the CLI takes *as* a model, and the namespaces a full id
  belongs to, so `omp`/`pi`/`opencode` answer `None` and their refusal names a launch profile
  as the way to set a model anyway. Recognition is namespace-plus-alias, never an enumerated
  catalogue of released models - a catalogue lags every vendor release and would refuse a
  model that works, while a namespace still catches `codex --model opus`.
- [x] A request-level model overrides the launch profile's, and an unrecognized model name fails
  at the card rather than as a dead session.
  The override is a **replacement** (`strip_model_args`), not a fourth argument slot: two
  `--model` flags on one command line is a per-CLI coin toss. `--model` deliberately stays
  unreserved so a profile pinning a model keeps working, and a test holds the two apart.
  The card check also pins the harness it validated against and restates the canonical
  spelling, so "opus 5" is confirmed and spawned as `claude-opus-5`; a model asked for in a
  Project with no default harness is answered by asking for one rather than by guessing.

### Phase 15 exit criteria

- [ ] Anthropic-routed assistant turns show nonzero cached tokens in the spend view, and the hit
  rate is visible beside spend.
  The breakpoint, the ledger column, and the surface are built and tested; the criterion stays
  open until an operator reads a nonzero rate off a real Anthropic-routed turn, which no test
  can stand in for.
- [ ] "Mux, new conversation" clears context by voice and says so.
- [ ] A deliberately trailed-off utterance is deferred exactly once and merges with its
  completion; the deferral appears in the log with its trigger.
- [x] Every budget setting offers tokens, USD, or first-hit, and pre-existing caps enforce
  exactly what they enforced before migration.
  Eight caps behind one control and one enforcement path (`design/features/budgets.md`), with
  the migration checked per cap against a longhand table of the old comparisons, including the
  half-a-pair case where the unmentioned axis was being enforced at its default all along.
- [x] Read aloud has one master switch; an unfocused session's reply holds its clip instead of
  speaking over the focused one.
- [ ] A local OpenAI-compatible endpoint passes verification and unlocks LLM-gated automations;
  removing or editing it re-locks them with a stated reason.
  Built and tested end to end, including the re-lock through the real HTTP path
  (`tests/test_llm_provider_api.py`). The criterion stays open until an operator points it at a
  real llama.cpp or Ollama and reads the reply off the Verify button, which no test can stand in
  for - a fake session proves the request shape and not that a local server accepts it.
- [ ] The assistant lists actions, runs an approved one behind a card, refuses an unapproved one
  by naming the file, and reports success or an issue flag with no output read-back.
- [ ] "Open an opus session in X" spawns with that model, and a bad model name fails at the card.
  Both halves are built and tested end to end in-process - the composed argv, the card
  refusal, and the canonical restatement - and the criterion stays open until an operator
  runs the spoken form against a real CLI, which no test can stand in for.

## Phase 16 - Usability follow-through: the first ten minutes, and a way to ask for help

The 2026-08-20 usability audit (`USABILITY_AUDIT_2026-08-20.md`) measured the first-run path
against the code and found it broken outright in three places, thin everywhere a user might ask
"what is this", and stale where the tour describes chrome that no longer exists.
This phase is that report's accepted findings, scoped 2026-08-21.
Ordering inside the phase is deliberate: the blockers are small and stop a first run today, the
help surface is the large item and the durable fix, and the verification section costs nothing
but reading.

### First-run blockers

Three defects each end or corrupt a brand-new user's guided first run.

- [ ] The tour must not strand on mobile: step `resources` is click-gated on
  `[data-tutorial="project-notes"]`, which only the desktop launcher rail or an already-open
  side panel carries, and an action step renders its hint instead of Next - so on a phone the
  only control at step 10 of 14 is Exit.
  `navigateTutorial` already opens the sidebar for other mobile steps; this step follows suit,
  and the same fix covers the desktop case of a hidden Notes tab.
- [ ] Step 5 must not demand a real provider login unskippably, while the harness panel one
  screen earlier calls CLI login a later step.
  The step becomes skippable and its copy agrees with the harness panel.
- [ ] The two first-run surfaces must not render stacked: the harness dialog (z-140) sits over
  the tour's blur (z-120), so the first frame is a dialog on a doubly-dimmed app with an
  invisible tour card beneath it.
  One surface leads and the other waits; which leads is the implementer's call, stated in the
  code.

### A help surface that exists

The largest gap past minute ten: 106 commands, 206 config keys, 17 settings tabs, 11 side-panel
tabs, and no `help.*` command, no docs link, and a tour reachable only from Settings → General.

- [ ] The tour becomes a registered command (palette + voice), because a recovery path nobody
  can find is not a recovery path.
- [ ] Complex tabs (scan timeline first, then the surfaces the audit lists) get an in-context
  help control opening a modal built from the tab's own feature doc, so the help cannot drift
  from the design document that defines the surface.
  The continuity project's generated-tutorial-from-docs pattern is the reference: the same two
  inputs exist here (48 feature docs + the command registry).
- [ ] The website-docs half of the operator's Release note is explicitly deferred to the
  release track; this phase ships only the in-app half, so the two are not coupled.

### Stale guidance

- [ ] The tour no longer describes the removed `Utilities` menu group.
- [ ] `ui.md` states the real side-panel tab count (the code has 11; the doc says 14/12 in two
  places).

### Verify the handed-off findings

The audit routed two findings to the then-in-flight grant-gates session rather than acting on
them; grant-gates has since landed, and nobody has checked whether they landed with it.

- [ ] The Project Run menu no longer silently drops every agent row when no harness is enabled -
  it says why the rows are missing, or shows them gated.
- [ ] The assistant's off-state names the switch's real home (section 4 of Settings → Voice),
  not the nonexistent "Settings → Assistant" tab.
- [ ] Whichever of the two grant-gates did not in fact cover becomes work in this phase, not a
  new report.

### Phase 16 exit criteria

- [ ] A first run on a phone reaches the end of the tour without stranding, skipping a provider
  login it does not have, or opening under a stacked dialog.
- [ ] "Help" is speakable and palettable, the tour is re-openable from it, and the scan
  timeline's help modal opens from the tab and matches its feature doc.
- [ ] The tour and `ui.md` describe only chrome that exists.
- [ ] Both handed-off findings are verified fixed, with a pointer to where.

## Phase 17 - Subagent visibility: nested child rows and on-demand transcript panes (deferred)

Recorded 2026-08-22 after evaluation; deferred, not scheduled.
The goal is visibility into a live session's subagents: child rows nested under the session's
sidebar row, and a read-only live transcript pane per subagent that closes without touching the
subagent itself.

The approach is settled by a measured constraint: there is no native way to attach a TUI to a
specific subagent from outside its session (confirmed against Claude Code docs, changelog, and
issue tracker 2026-08-22).
`--resume` refuses sidechain ids; `claude attach` reaches only background jobs, which are
separate sessions; agent-teams tmux panes are managed by the lead session and not externally
attachable; workflow agents have only the in-session read-only drill-down.
So a reader over the on-disk sidecar files is the only path, and the pane is a viewer, never a
terminal.

The system is **fully dormant until asked**.
Nothing scans, captures identity, or parses while nobody is looking; the ambient signal remains
the existing `⑂` standing-activity count badge.
The trigger is a context action on the session row/tab ("View subagents"), which is also what
makes the performance story trivial: cost is proportional to open viewers, not to fleet size.

- [ ] Capability declaration first: a new nullable `HarnessDescriptor` field (working name
  `subagent_visibility`) in the established None-is-a-declared-refusal idiom, published through
  `public_harness_registry()` so the frontend gates the context action on it.
  Claude declares it; codex/omp/opencode declare `None` with a deferred-with-evidence comment
  (child rollouts via `parent_thread_id`, task events, `parent_id` session rows respectively);
  pi declares `None` permanently (no task tool).
  Coverage guards in the registry/adapter-matrix idiom force every present and future harness
  to answer explicitly.
- [ ] Two stateless endpoints, computed on demand from disk.
  List: one directory read of `<root-native-id>/subagents/` plus its `agent-*.meta.json` files
  (`agentType`, `description`, `toolUseId`, `spawnDepth` - verified present and live-growing
  2026-08-22), with running/done inferred by matching `toolUseId` against the root transcript's
  `tool_result` records.
  Transcript: the existing claude-dialect parser with the `isSidechain` filter parameterized
  rather than bypassed (the root-view drops stay), paged like the existing transcript route,
  tolerating `type:"attachment"` records.
- [ ] UI: child rows appear under the session's sidebar row once invoked; opening one creates a
  pinned non-PTY pane leaf (`subagent:<session>:<agentId>`, the `queue:`/`changemap:` idiom in
  `layout.ts` + `layouts.py` in lockstep), so closing is a pure layout edit by construction.
- [ ] Open-pane refresh is debounced off the already-emitted `subagent_activity` events and
  reads incrementally by byte offset (Windows freezes mtime on open files - tail by size).
- [ ] Live session only: no durable index, no SQLite, no history integration; history keeps
  dropping sidechain records exactly as today.
- [ ] The load-bearing exclusions do not move: `reconcile.py`'s subagent-directory rejection
  (root-session binding) and the subagent-hook exclusion from root turn-liveness each fixed a
  real incident and stay intact beside the new read path.
- [ ] Later kinds roll in per-kind behind the same capability answer: workflow-run agents
  (separate per-agent files under the workflow transcript dir), background `--bg` jobs
  (separate sessions, already attachable), then other harnesses.

## Phase 18 - Conversation rollover adoption: following a pane whose CLI forks itself (deferred)

Recorded 2026-08-23 after a live incident; deferred, not scheduled.
This is the notes home for the "a pane silently stops being the conversation mux follows" class.
The goal is that a pane whose CLI replaces its own conversation is followed rather than
abandoned, and that a pane mux has demonstrably lost track of says so instead of freezing on
its last reading.

### The incident that opened it

Session `23eb2466` (Claude 2.1.241, running inside the `rail-pads` worktree) detached at
16:34 and stayed detached.
The operator pressed `←` on an empty prompt - Claude Code's documented one-keystroke
"detach and return to agent view" gesture, no confirmation - while a `Bash` call was in
flight.
The CLI printed "Backgrounding after the current tool finishes...", deferred, and four
seconds later **forked** rather than re-homing: `bg spawned f25f94e5 (slash)`.
The pane has run conversation `f25f94e5` ever since.

Every consequence followed from mux still believing in `23eb2466`.
The `Stop` hook at 16:38 was discarded as foreign along with 227 others, so the record's last
proven transition stayed `working` from 16:34:33 onward.
The Mux assistant's queued message then hard-blocked twice on `root_agent_working` against a
session that was idle, which is how the incident was noticed at all - by a *reason that was
false*, an hour later.

Upstream context, researched the same day.
The agent-view docs say `/bg` keeps the same session; the shipped binary has a deferred-fork
path and an `abort-then-fork` sibling, both respawning with `--reply-on-resume`.
`anthropics/claude-code#70373` reports the fork (open, repro'd on 2.1.186) but attributes it
to *in-flight subagents*; this incident had no subagent, only an in-flight tool, so the real
trigger is wider than the issue records.
An opt-out exists (`disableAgentView` / `CLAUDE_CODE_DISABLE_AGENT_VIEW=1`) but is
all-or-nothing: it also removes `claude agents`, `--bg`, `/background`, and the on-demand
daemon.

### Why both existing nets failed, which is the actual finding

mux already had two independent mechanisms for exactly this event.
Both were reached, and both were killed by the same comparison: **the CLI's reported cwd
against the session's spawn cwd**, which a worktree is precisely the case that breaks.

- The `SessionStart` hook path (`conversation_rollover_decision`, `observation.py`) compared
  the hook's cwd (`...\worktrees\rail-pads`) against `run_cwd` (`D:\PROJECTS\swe-mux`) and
  `runtime_cwd` (`...\worktrees\rail-pads\frontend`, stale, sourced from OSC 7).
  No exact match, so `cwd_mismatch`, so refused.
- The `cli_state` parked-conversation follower (`_parked_move`, `cli_state.py`) is
  purpose-built for this and was handed a perfect signal: the pane's own
  `~/.claude/sessions/51604.json` still read `sessionId: 23eb2466`, `kind: interactive`,
  `parkedJobId: f25f94e5`.
  It matched the session, resolved the job, saw a different conversation, then compared
  `parked.cwd` (the worktree) against `run_cwd` (the checkout) and returned `None` - before
  even writing its ledger line, which is why the session's timeline carries no `cli_state`
  entry to show it tried.

The job file already carried the discriminator in fields `_parse_job` does not read:
`originCwd` is exactly `record.run_cwd`, and `worktreePath`/`interactiveLineage` name the
rest.
`respawnFlags` even contains the owning mux session id in its `--settings` path.

Rarity is not safety here.
Four `cwd_mismatch` refusals since 08-17 against 189 `foreign_process_startup` ones, and
outside a worktree this same fork is adopted silently - so the failure is invisible until it
lands on a worktree session, and then it is total and permanent for that session.

### Candidate work, in the order it would be sequenced

- [ ] Record the `SessionStart` `source` and `cwd` on the refusal event and timeline entry.
  Today a refusal carries only `reason` and `native_session_id`, which is why diagnosing this
  incident required disassembling the CLI to learn what mux had already been handed.
  Cheapest item here and it precedes every behavioral change, because the others should be
  judged against recorded evidence rather than a reconstruction.
- [ ] Compare against `originCwd` (falling back to `cwd`) in `_parked_move`, and widen the
  hook check to accept a known worktree root or an ancestor/descendant of a known cwd.
  Note this makes net 2 *stricter*, not looser: exact equality against the field that actually
  means "where this pane was spawned", instead of an equality that a worktree always fails.
- [ ] A detached-pane detector: N consecutive foreign hooks for the same foreign id including
  a turn-lifecycle event proves detachment regardless of what refused it, and should set
  `observation_stale_since` with a diagnostic.
  This is the general net - session `16d01933` accumulated 1237 foreign hooks with no refusal
  at all, so a detector keyed on refusals would miss that shape entirely.
  It also converts the delivery block from a fabricated `root_agent_working` into the true
  `transcript_stale`, which `delivery_readiness` already understands.
- [ ] Transcript-prefix proof as an additional accept path: a fork shares a byte-identical
  prefix with its parent (confirmed in this incident and independently in #70373), which a
  nested child cannot forge.
  It supplements rather than replaces the cwd logic, because `/clear` legitimately produces an
  empty transcript with no shared prefix.
- [ ] Decide whether recovery is automatic (adopt on proof, roll, resume) or an explicit
  operator action; there is no rebind endpoint today, so restarting the pane is the only
  recovery and a detached session stays detached until someone notices.

### Open questions to resolve before scheduling

- What `source` value does the background fork actually carry?
  The documented set is `startup`/`resume`/`clear`/`compact`/`fork`, and `fork` is the obvious
  candidate, but this was never observed because mux does not record it.
  The first checklist item answers this, and the answer may make `source` a better primary
  discriminator than cwd for every non-`startup` rollover.
- Does `CLAUDE_CODE_DISABLE_AGENT_VIEW=1` stop the CLI writing `~/.claude/sessions/<pid>.json`?
  If it does, the prevention route costs mux its whole `cli_state` detection layer, which is a
  bad trade against a rare bug and would settle the question against prevention.
  Untested; it is the deciding fact and cheap to measure.
- Prevention is the weaker axis regardless: `/clear` in a worktree, compaction, and resume all
  produce a legitimate rollover and will keep happening, so adoption is the fix that
  generalizes and prevention is at most belt-and-braces.
- Worth reporting the wider trigger upstream on #70373 (an in-flight *tool*, not only an
  in-flight subagent), with the job-state and timeline artifacts from this incident.
- Local hazard worth carrying into the command-rail work: the rail emits arrow keys, and `←`
  into an empty Claude prompt is exactly this gesture.

## Phase 19 - Worktree verification as a portable, agent-reachable contract

Recorded 2026-08-24 after evaluation; not scheduled.
The land queue is worth nothing in a repository that has no verification command: `verify` refuses
rather than runs, and every land refuses with it.
Everything a new repository needs in order to grow one is already written down and already
install-agnostic - it is just not anywhere the agent doing the work can reach, and it covers only
half of the pair.

### What already exists

The convention is repository-neutral: an executable `.worktree-setup` / `.worktree-verify` at the
repository root, or `[worktree] setup_command` / `verify_command` in `.swe-mux/config.toml` as the
override, resolved per worktree so the script travels with the branch (`worktree_exec.py`,
`worktree_setup.py`, `worktree_verify.py`).
The authoring guidance exists as a product artifact rather than as this repository's own lore:
`verifySetupPrompt()` (`frontend/src/landSetupPrompt.ts`) states the contract - the branch's
worktree is the cwd, parallel-safe, bounded, the exit code is the only verdict, never pipe a check
through `tail`, the optional `=== name ===` step protocol - then both conventions, a
prove-it-fails-when-it-should procedure, and the ending that keeps it a proposal.
`SetupPromptDisclosure` (`GitLandBar.tsx`) draws it in the landing strip as text with a Copy
button.

### Why that is not yet enough

- **No agent can read it.** It is a frontend constant behind a disclosure, so a human has to find
  the strip and paste it into the repository that needs it. There is no MCP read, no CLI
  subcommand, and nothing that writes it into the target repository.
- **The refusal path does not hand it over.** `_refused_body` (`land_queue.py`) tells the
  requesting agent that this Project has no verification command and that approval is a human act,
  which is correct and is also the moment of maximum motivation to fix it - and it ships neither
  the contract nor a pointer to it.
- **`.worktree-setup` has no equivalent at all.** Its only guidance anywhere is the placeholder and
  one paragraph in the Projects registry editor (`ProjectsManager.tsx`). The bootstrap half - only
  gitignored state needs handling because tracked files come with the checkout, share the package
  caches, it runs before the session starts, it must be idempotent and bounded - is written down
  only in this repository's own script comments.
- **It is discoverable inside a feature that is off by default.** The strip lives under Git → Map →
  LANDING and the land queue is per-Project opt-in, so the surface that teaches the convention is
  behind the switch that needs it.
- **Nothing lands in the target repository.** After a successful setup the knowledge stays in the
  swe-mux install, so the next agent in that repository re-derives it from scratch.

### Candidate work, in the order it would be sequenced

- [ ] The prompt text moves to a daemon-owned module and the frontend renders what it is served.
  Its own comment argues it is "deliberately not fetched from the daemon", and that argument is
  about install-invariance rather than about who may read it; the one variable it interpolates
  (`scriptName`) already comes from the daemon through `gate.scriptName`, so the invariance
  survives the move intact.
- [ ] A read-only MCP tool returns it, and the `not_configured` / `unapproved` handback carries it
  or a pointer to it.
  This leaks no authority: the text is a description of a proposal, the daemon enforces
  digest-approval by a human regardless of what any prompt says, and the "you cannot approve this"
  paragraph gets *stronger* when the agent reads it first-person instead of receiving it pasted.
- [ ] The setup half gets its own prompt on the same surfaces, stating the bootstrap contract
  rather than a placeholder.
- [ ] `mux doctor` gains a per-Project readiness check - no verification command, no setup command,
  queue disabled - each naming its own fix, so the convention is reachable without first enabling
  the feature that draws it.
- [ ] The prompt's closing section tells the receiving agent to record the landing flow in that
  repository's own agent-instructions file.
  This is the part that actually answers "a new user on a new system": the contract travels with
  the product, and the convention travels with the repository.

### What the prompt does not yet say (field report, 2026-08-24)

An agent that set the gate up in an unfamiliar repository from the current prompt reported seven
things it hit that the text does not cover.
They are content requirements for the prompt, so they land in whatever this phase builds and in the
copyable text as it stands today - the two must not diverge, which is itself an argument for the
single daemon-owned source above.

- [ ] **Pin every external resource to something disposable inside the worktree.**
  The contract covers *collision* (ports, temp paths, singletons) and never asks the question one
  step before it: what live thing could this reach?
  In that repository `Settings.database_url` defaulted to the production SQLite file, so a gate run
  would have opened and written it.
  The line to add names the classes: database, cache, queue, API base URL.
- [ ] **Ignore the worktree root and the gate's own scratch directory, and prove it with a worktree
  open.**
  Whatever creates worktrees puts them somewhere, and that somewhere is usually inside the
  repository - `.claude/worktrees/`, `.agents/worktrees/`, `.codex/worktrees/`, and a bare
  `.worktrees/` are the ones this repository has had to ignore.
  Un-ignored, every open worktree makes the primary checkout read dirty forever.
  The precise consequence differs by consumer and the prompt should say the general rule rather
  than the local one: swe-mux's own precondition counts *tracked* changes only
  (`_tracked_changes`, `land_preconditions.py`) and the trunk check is narrowed to the incoming
  paths, so an un-ignored worktree root does not block a land here - but it breaks every gate,
  drift check, or tool that reads `git status --porcelain` for cleanliness, and it becomes a
  blocking fact the moment anything adds those paths.
  The verification step is one sentence: open a worktree and confirm `git status` is still empty.
- [ ] **Commit the script with `text eol=lf` and the exec bit.**
  This is the failure mode with the worst blast radius, because it is invisible where you author it.
  With `core.autocrlf=true` and no `.gitattributes`, Git writes CRLF into the working tree, so the
  script works in the checkout it was written in and breaks in every worktree it actually runs in -
  observed as exit 120 with a *different* set of failures, the stray `\r` landing outside the
  closing quote of each `export`.
  A gate that fails for a reason that is not the branch attributes a verdict to the wrong branch,
  which is the one outcome the whole approval model exists to prevent.
- [ ] **The gate must not perturb what it checks.**
  A scratch directory inside the worktree is correct per the contract and is still walked by
  anything that walks the tree; there, the repository's file-tree generator picked it up and the
  drift check failed *because the check was running*.
  Self-referential failure is not obvious from the outside and one sentence covers it.
- [ ] **Inventory what Git does not carry, then decide whether the gate needs it.**
  A worktree is not a runnable checkout.
  The two surprises were in opposite directions: `data/` was gitignored so no worktree had a
  database at all, while `backend/.env` was tracked, so secrets propagated into every worktree.
  Both are found by the same instruction and neither is found by reading the contract.
- [ ] **Say plainly that a red suite blocks the whole queue.**
  The gate is the suite, so until the suite is green no branch can land regardless of what it
  changes.
  The current text implies it ("write it as the full check suite") and never states the
  consequence, which is what makes "get the suite green" a prerequisite of setup rather than a
  nice-to-have, and which changes how parallel work is sequenced.
- [ ] **Give the proof step a way to be carried out.**
  It asks for two simultaneous worktree runs and says nothing about creating them, and `git
  worktree` was policy-blocked in that environment.
  Two concurrent runs in one tree is a legitimate substitute and is strictly harsher, since they
  share every path rather than only the machine; say so rather than leaving the reader to invent it.

### Deliberately not in scope

A library of per-stack starter verify scripts (uv/pytest, cargo, go, plain npm).
The contract is what a new repository is missing, not the syntax, and a template that lands unread
is exactly the gate-that-cannot-fail the prompt's prove-it section exists to prevent.
Stack detection may inform the prompt; it may not substitute for writing and proving the command.

### Phase 19 exit criteria

- [ ] An agent in a repository with no verification command can obtain the full contract without a
  human copying anything, and a refused land or verify tells it where.
- [ ] Both halves of the convention have authoring guidance, from one source that the browser and
  the agent surface both read.
- [ ] `mux doctor` names a missing verification command, a missing setup command, and a disabled
  queue, each with its fix.
- [ ] Approval remains a human act against the exact bytes on every one of the new paths, and no
  new path can produce an approved command.
- [ ] Every item in the field report is either in the shipped prompt text or recorded here as
  rejected with its reason, and the setup half is checked against the same list.

## Decision-gated capabilities

These remain recorded but are not committed roadmap work. Scheduling one requires a new
product decision defining authorization, trust, confirmation, audit, disablement, and
failure behavior:

- Repository-owned executable rules, project scripts, executable rulepacks, and a
  machine-owned fingerprinted trust store.
- Model-authored action selection, autonomous worker spawning, unrestricted PTY writes,
  auto-approval, arbitrary command execution, or arbitrary HTTP/network destinations.
  The network half of this entry is what Phase 13 must decide before it ships navigation: an
  agent-drivable browser is agent-chosen HTTP destinations, and Phase 13's per-Project grant plus
  host allowlist is the proposed narrowing. Until that decision is recorded, the entry stands and
  agent-issued navigation is out of scope; operator-issued navigation in a pane never was in it.
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
