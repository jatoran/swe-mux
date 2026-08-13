# Control plane roadmap

Status: the authoritative roadmap and design reference for the swe-mux control plane — the
out-of-band automation layer over Claude Code / Codex sessions. **§9 is the implementation
roadmap with a live completion checklist**; the rest is the design reference the checklist
points into. An agent picking this up should start at §9, then read the referenced design
sections for whatever step is next.

The approved read-only subset is also scheduled in `ROADMAP.md` Phases 6–7 (authoritative
for OpenRouter-only LLM access, write-only Settings key management, durable agent-run
annotation ownership, inert repository rules, and actuation deferrals). Items here stay
unscheduled in that roadmap unless promoted explicitly.

The design is organized as a **substrate → consumer hierarchy** (§4) with a **return path**
(§7), an **enablement model** (§8, per-project opt-in with a dependency graph), and the
**implementation roadmap** (§9). It folds in a 2024–2026 research review (agent-failure
taxonomies, trajectory telemetry, compression-chain fidelity, cascade economics,
supervisory-control/HCI); where a design choice is evidence-backed, the finding and source
are named inline (§2).

---

## 1. Framing: swe-mux as an agent control plane

The SOTA CLI agents (Claude Code, Codex) are the **data plane**: they do the
work. swe-mux is the **control plane**: it observes, annotates, routes
attention, and applies policy without being in any agent's execution path.

The strongest structural analogy is a **service mesh**. Istio/Envoy sidecars
intercept traffic from heterogeneous services without those services
cooperating or even knowing; the mesh gets uniform observability and policy
across apps that update independently. swe-mux is that for agent CLIs:

- PTY bytes, native transcripts, and hook callbacks are the intercepted traffic.
- Backend adapters are the sidecars.
- The normalized EventBus is the mesh telemetry.
- Rules and observers are mesh policy.

Related terms of art that apply: **out-of-band supervision** (the layer watches
from a position the supervised process cannot reach or corrupt) and **ambient
agents** (cheap background models that react to events rather than holding a
conversation). The mental image the design serves: the interactive agent is a
pillar; cheap ambient agents buzz around it, scaffolding, annotating, and doing
small bounded jobs, while a stronger agent is invoked rarely for deeper analysis.

### Why this position is durable

Scaffolding that compensates for a model's cognitive deficits depreciates:
each model generation absorbs the layer above it. What does not depreciate is
the layer that touches the world rather than the mind: process ownership,
isolation, observability, state persistence, identity, the human interface.
swe-mux contains almost no cognitive scaffolding to depreciate. In the
long-run "agent kernel" picture (harness as OS: tool calls as syscalls,
context as virtual memory, sessions as processes, budgets as cgroups), swe-mux
is a **userspace daemon** — systemd/Kubernetes, not the kernel — which is the
side of the commoditization line where differentiation survives.

Two independent 2026 arguments reinforce the out-of-band stance beyond agnosticism:
governance metadata is only defeat-proof when it rides a channel the agent cannot
see or edit (in-band policy sitting in the prompt is defeatable by injection); and
flag-don't-block is a published monitoring posture, not a compromise — continuous
monitors that score rather than gate are the established design for supervising a
process you do not control.

### Design laws

These are the non-negotiables that keep the layer scaffold-agnostic and safe:

1. **Out-of-band only.** Anything that must block or gate an agent's turn
   belongs to the native scaffold's own hooks. The mux layer is advisory and
   asynchronous. It never needs the CLI's cooperation precisely because it is
   never in the critical path. The moment it tries to be in-band it re-couples
   to a vendor hook API and loses its agnosticism.
2. **Above and around, never inside.** The layer observes, annotates, and
   notifies. It does not steer sessions. Actuation (`write_pty`) exists but is
   deliberately gated (see §16). Note the one *inverse* arrow: the data plane may
   pull from the control plane on its own initiative (§7). That is the agent using
   a tool, not the control plane entering its execution path.
3. **Consume normalized events and normalized transcript slices only.** No
   rule, observer, or feature outside an adapter may reference a native schema,
   path, or flag. Scaffold drift is absorbed in one adapter file.
4. **Advisory, not orchestration.** Roles, leads, and DONE protocols were killed
   deliberately. Observers notice; they do not command. Anything that directs
   agent behavior goes through the reserved relay path as an explicit future
   product decision, never as feature creep through the rules file.
5. **Eventually consistent by design.** Nothing in this layer participates in
   a turn. That constraint is why the layer survives scaffold updates.
6. **Prefer ground truth to self-report.** The one structural advantage of the
   out-of-band position is that the observer cannot be gaslit by the agent's
   narrative. A feature that reads the transcript is reading the agent's *story*
   about what it did. Wherever a fact exists deterministically — a git diff, a
   test exit code, a file hash — condition on the fact, not the claim. (TRAIL,
   2025: the best frontier model localized errors in full coding traces at ~5%.
   AgentLens, 2026: of 1,136 *passing* SWE-bench runs, only 20% had a clean
   trajectory. Passing tests and "done" claims are not evidence of correctness.)
7. **A better monitor can make oversight worse.** Reliable automation trains the
   human to stop checking (automation-induced complacency, Parasuraman 1993) and
   degrades their ability to take over when it fails (out-of-the-loop decrement,
   Endsley & Kiris 1995). Polished rationales *increase* deference rather than
   scrutiny (HBS/Lane 2025). Countermeasures are mandatory, not optional: always
   display suppressed-item counts (silence must never read as absence), keep the
   interrupt volume low enough that each item still gets real thought, and
   periodically surface a low-confidence item the layer explicitly will not judge.
8. **Nothing runs on a project that did not opt in.** Every automation is
   per-project opt-in; the substrate it needs is enabled with it through a
   dependency graph (§8). There are no automations that execute machine-wide.

---

## 2. The evidence base

The catalog below rests on a 2024–2026 review. The load-bearing findings, so the
rationales downstream are not folklore:

- **Failure taxonomies.** MAST (2025, 1,642 traces): the most frequent failure
  modes are step repetition (~16%), reasoning-action mismatch (~13%),
  termination-unawareness (~12%). Stopping behavior (premature termination +
  unawareness of termination) is roughly as failure-prone as looping — a monitor
  that only detects loops misses half the mass. TRAIL (2025): ~42% of trace
  errors are formatting / instruction non-compliance.
- **Windowing beats whole-transcript.** TRACE (2026): a windowed monitor hit
  recall 0.844 vs 0.405 for a full-trajectory monitor, using *fewer* model calls.
  Sampling is the accuracy-correct approach, not merely the cheap one.
- **Loop detection is hybrid, not semantic-only.** Unsupervised cycle detection
  (2026): structural signal alone F1 0.08, semantic alone 0.28, hybrid 0.72 at
  0.62 precision. Wink (2026, ~43k production traces) operationalizes a loop as
  ≥3 repeated/near-identical actions and calibrates classifiers to ≥80%
  precision. A no-progress gate is required to avoid flagging legitimate repeats.
- **Compression chains fail silently.** Slipstream (2026): ~90% of compaction
  failures are omissions; the continuation stays coherent with the bad summary,
  so the error is invisible. MemCollab (2026): weak-model records consumed by a
  strong model help 3–8% when good but *hurt 5–12%* when bad, because a wrong
  record is worse than a missing one. Governance-decay work (2026): repeated
  compaction drove constraint-violation rates from 0% to 30–59% unless
  constraints were pinned. → Never make a summary the only copy; keep source
  pointers and re-expansion.
- **Structured > prose for downstream machine use.** PrefixGuard (2026): typed
  step representations beat raw text by +0.137 AUPRC; removing tool-result
  fields collapsed performance. Factory compression eval (2025, 36,611 SWE
  messages): explicit typed sections beat free-form prose, and artifact/file
  tracking is the worst dimension across every compactor (2.19–2.45/5) — file
  paths must be a lossless list, never narrative.
- **Cascade economics.** With current pricing (Haiku vs Opus/Sonnet tiers), a
  single strong-model analysis pass already pays back the cheap-tier ingestion
  cost of a whole session. The break-even variable in *this* design is the
  **rehydration rate**: how often the strong model must expand a source pointer
  and read raw. Keep it under ~50% and the compression pays; above it, it's an
  expensive detour. Instrument it from day one.
- **Fixed-clock sampling is under-studied and probably wrong alone.** No
  controlled study supports wall-clock semantic sampling. Shipped monitors
  (SWE-PRM, Wink) sample on agent *steps*; compaction work (SelfCompact 2026)
  finds semantic-boundary triggers match or beat fixed intervals at 30–70% lower
  cost. → event-triggered with a max-time heartbeat, not clock-only.
- **Attention is the scarce resource, and its budget is tiny.** Three unrelated
  fields converge: AI-control audit budgets run at ~1%; SRE guidance says a human
  can absorb "a few pages a day" before fatigue; clinical alarm research shows the
  failure state at high volume is >60% of alarms getting no response. Budget
  interrupts per *day*, not per hour.
- **Self-report is unreliable.** METR RCT (2025): experienced devs were 19%
  slower with AI while believing they were ~20% faster. Do not build interrupt
  policy on stated preference; learn from observed behavior (what the user acts on
  vs dismisses) and show the induced rule for accept/reject (PrefMiner pattern).
- **Fan-out is the organizing metric.** Supervisory-control theory (Olsen &
  Goodrich 2004): agents-per-human ≈ neglect-time ÷ interaction-time. swe-mux
  owns both halves (scan timeline gives neglect time; attach/input telemetry
  gives interaction time), so it can compute a live sustainable-agent estimate no
  vendor can.
- **Provenance beats inferred causality.** Distributed-tracing lineage (Lamport
  1978; Dapper 2010) is deterministic and reliable; statistical root-cause over
  event streams over-attributes. → build the factual provenance graph; never emit
  a causal blame label.
- **Retrieval precision is a trust gate.** A usually-wrong `prior-resolution`
  poisons the whole annotation surface; a weak cross-session route costs an
  interrupt. Empty beats wrong for anything the agent or human will act on.

---

## 3. Naming conventions

Decided vocabulary. "Meta-hooks" is retired: "meta" says nothing concrete and
"hooks" collides with the CLIs' native hooks; the term also names the layer
after its most fragile input (it hooks *events*, not hooks — see §10).

| Term | Meaning |
|---|---|
| **Universal hooks** | User-facing name for the whole layer: hooks defined once, above the CLI, that fire for any backend and survive CLI updates. The pitch word. |
| **Rules** | The mechanical tier: trigger → conditions → actions, deterministic, no model call. `hooks.toml` becomes `rules.toml`. |
| **Observers** | The LLM tier: stateless model calls via the `llm` action — read a normalized slice, emit an annotation or notification. No session, no tools, read-only. Cheap, safe, plentiful. |
| **Workers** | Full agent sessions a rule spawns via the `spawn` action to *do* something. Data-plane work, control-plane initiated; once spawned it is an ordinary session with a lineage edge back to its cause. Mutates the world, so it sits behind the actuation gate (§16). |
| **Substrate** | The foundational layers every consumer reads from: event log, raw store, Tier 0 facts, project card, scan timeline. Mostly deterministic and cheap. |
| **Consumer** | A feature assembled from substrate: provenance graph, dead-end memory, declared-vs-verified, loop detection, doc-debt, interlocks, attention ranking, digests, continuous title. |
| **Tier 0 / Tier 1 / Tier 2** | Deterministic facts / cheap-model semantic index (the scan timeline) / strong-model analysis. Named after cost and cadence, not model brand. |
| **Scan timeline** | The Tier 1 semantic index: periodic/event-triggered cheap-model records of what each session is doing, forming a per-session behavioral timeline. |
| **Return path** | The inverse arrow: how accumulated insight reaches the coding agent, primarily a queryable **mux MCP** read surface the agent pulls from (§7). |
| **mux MCP** | The agent-facing transport for the return path: MCP tools that are thin callers over the same typed daemon operations the browser/CLI/mailbox use. A transport, never a permission model; authority stays in the daemon op (§7.1). Ships as **v0** (read/discovery), **v0.5** (situational-awareness reads over shipped services, §7.5), **v1** (memory tools, needs steps 1–5), and finally **session control** (§7.6, the only part carrying authority). |
| **Enablement DAG** | The per-project opt-in dependency graph: a consumer cannot be enabled unless its substrate dependencies are enabled for that project (§8). |
| **Automation layer** | Umbrella term for rules + observers when one word is needed. |
| **Native hooks** | Reserved exclusively for the CLIs' own hook systems (Claude Code hooks, Codex `notify`). They are an event *source*, nothing more. |
| **Agent run** (`agent_run_id`) | One continuous provider conversation, and **the scope every control-plane record is keyed by** — Tier 0 facts, annotations, evidence sets, scan records, queue bindings. A session may have several runs in sequence; **a run never spans two conversations**, which is the invariant with teeth. It is usually also one PTY, but that is a consequence rather than a rule: a conversation the provider itself continues onto a new PTY (Claude `--resume`) is still one run. |
| **Conversation rollover** | An in-CLI `/clear` (Claude) or `/new` (Codex): same PTY, same mux session, new provider conversation, therefore **a new agent run**. Signalled by `agent_conversation_rolled` on the event log. Consumers do not detect it themselves — they inherit the boundary from the run id (`ROADMAP.md` Phase 5.4). |
| **Resume inheritance** | The mirror of a rollover: new PTY, new mux session, **same** provider conversation, therefore **the same agent run**. `claude --resume` and `codex resume` both append to the transcript they resumed, so the pane inherits that run (`spawn_agent_run_id`) and its history entry instead of opening a second over one file — and the timeline, facts, annotations, and title continue rather than restarting blank. The adapter decides, since the rule is the CLI's own: a Claude resume into a root other than the conversation's writes a different file, so that one is a new conversation and a new run. See `design/features/history.md`. |
| **Annotations** | Persisted observer/rule output attached to sessions (titles, summaries, verdicts, scan records). |
| **Universal commands** | Input-side sibling of universal hooks: mux-level canned prompts injectable into any backend (see §17). |
| **Rulepacks** | Shareable, parameterized bundles of rules + observers + scripts (see §15). |

Clean sentence test: *native hooks feed events, universal hooks fire, rules
match, observers think, workers act, the agent pulls.*

---

## 4. The architecture: substrate and consumers

The catalog splits into two layers. **Substrate** is foundational: mostly
deterministic, cheap, and written/read by everything above it. **Consumers** are
features that combine substrate. The rule of thumb: substrate is where facts live;
intelligence lives only in the scan timeline (Tier 1) and the ranking layer, and
both sit on top of everything else. A third, inverse arrow — the **return path**
(§7) — carries consumer output back to the coding agent on the agent's initiative.

```text
   DATA PLANE (the coding agent)  ──pull, agent-initiated──►  RETURN PATH (§7)
        ▲                                                         │ mux MCP read tools
        │ insight the agent consults while coding                │ instruction sync
        └─────────────────────────────────────────────◄─────────┘ human-mediated draft
                                                 │
                         ┌───────────────────────┴──────────────────────────┐
                         │              HUMAN ATTENTION                       │
                         │        (the actual scarce resource)               │
                         └───────────────────────▲──────────────────────────┘
                                                 │  budgeted: a few/day
                    ┌────────────────────────────┴───────────────────────────┐
                    │   ATTENTION RANKING / INBOX  (the top consumer)         │
                    │   fan-out estimate · interrupt budget · 4 channels      │
                    └───▲────────▲────────▲────────▲────────▲────────▲────────┘
                        │        │        │        │        │        │
        ┌───────────────┘        │        │        │        │        └──────────────┐
   ┌────┴─────┐ ┌────┴─────┐ ┌───┴────────┴──┐ ┌───┴────────┴───┐ ┌────┴─────┐ ┌────┴─────┐
   │ dead-end │ │ declared │ │  loop / stall  │ │  doc-debt      │ │ cross-   │ │ absence  │
   │ memory   │ │ vs verif │ │  detection     │ │  ledger        │ │ session  │ │ report / │
   │          │ │          │ │                │ │                │ │ interlock│ │ digest   │
   └──▲────▲──┘ └────▲─────┘ └───▲────────▲───┘ └───▲────────▲───┘ └────▲─────┘ └──▲────▲──┘
      │    │        │            │        │         │        │          │         │    │
      │  ┌─┴────────┴────────────┴──┐  ┌──┴─────────┴──┐  ┌──┴────────┐ │  ┌──────┴────┴──┐
      │  │   SCAN TIMELINE (Tier 1) │  │  PROJECT CARD │  │ PROVENANCE│ │  │ EVENT LOG    │
      │  │   cheap-model records;   │  │  distilled    │  │  GRAPH    │ │  │ normalized,  │
      │  │   the "why" + salient    │  │  architecture │  │ (Tier 0)  │ │  │ sequenced    │
      │  │   user/agent messages    │  └───────────────┘  └─────▲─────┘ │  └──────▲───────┘
      │  └───────────▲──────────────┘  (a continuous title      │       │         │
      │              │                   would have read this;  │       │         │
      │        ┌─────┴───────────────────abandoned, §6.11)──────┴───────┴─────────┴──────┐
      └────────┤            TIER 0  — DETERMINISTIC FACTS (no model)                      │
               │  file hashes · git state · test pass/fail · exit codes · tool           │
               │  fingerprints · process tree · attach/input/focus telemetry             │
               └────────────────────────────────▲───────────────────────────────────────┘
                                                 │  pointers back to source
               ┌─────────────────────────────────┴──────────────────────────────────────┐
               │      RAW STORE  — immutable native transcripts + PTY bytes              │
               │      authoritative; every derived record points back into it            │
               └────────────────────────────────────────────────────────────────────────┘

   Sibling consumers, need only PROJECT CARD + browser surface (not the timeline):
   ┌───────────────────────────────┐   ┌───────────────────────────────┐
   │ screenshot-to-agent           │   │ observation inbox (no AI —     │
   │ send a pane's view to the CLI │   │ a capture keystroke)           │
   └───────────────────────────────┘   └───────────────────────────────┘

   Every box above is per-project opt-in and gated by the enablement DAG (§8).
```

Build order follows the graph bottom-up: **raw store → Tier 0 → (project card,
provenance) → scan timeline → deterministic consumers → ranking last**, because
ranking needs every other signal to rank anything. See §9 for the full order.

---

## 5. Substrate

### 5.1 Normalized event log

One timestamped, monotonically sequenced stream of everything: tool calls, file
writes, test runs, process start/exit, git changes, attach/detach, input-owner
changes, annotation writes. Provider-neutral, so Claude and Codex look identical
to everything above the adapter. This is the spine — every consumer either writes
to it or reads from it, and `events` + the EventBus are already the bones of it.

Every event carries `source` (hook / transcript / PTY / mux) and `confidence`, so
consumers may condition on fidelity without depending on it (§10).

### 5.2 Raw store and source pointers

Native transcripts and PTY bytes are retained immutable and authoritative, and
**every derived record carries a pointer back to the exact span it came from**
(`transcript_id, event_id, byte_start, byte_end, content_hash`). This is the
single most important structural defense in the whole design: the compression
literature shows summary-only chains fail silently (§2), so nothing that
summarizes is ever allowed to be the only copy. swe-mux already treats native
transcripts as authoritative read-only sources with mtime/size watermarks, so this
layer is half-built.

Caveat the raw store must respect: PTY bytes are a *presentation* stream (redraws,
ANSI, spinners, truncation), not a clean event log. Structured facts come from the
adapter/transcript, not from scraping the terminal.

### 5.3 Tier 0 — deterministic facts

The no-model workhorse. Cheap, exact, immune to hallucination:

```text
file content hashes (read + written) · git HEAD/branch/diff hashes ·
test pass/fail counts + failing-test ids · command exit codes/classes ·
tool fingerprints (canonicalized: strip timestamps, temp paths, line numbers,
  random ids) · process tree (pid + creation time) · descendant listeners/ports ·
attach / detach / input-owner / focus telemetry · context-compaction records
```

Provenance, loop detection, and declared-vs-verified are *just queries over Tier
0*. Keeping these facts lossless and separate from any model output is what lets
the scan timeline stay cheap (it references Tier 0 rather than re-describing it)
and what makes the file/artifact trail reliable where every summarizer is weakest.

Every fact carries `agent_run_id` resolved at capture time, and the run is the
window those queries operate over. The boundary includes an in-CLI conversation
rollover, not just spawn/exit/promotion (`ROADMAP.md` Phase 5.4): a fingerprint
window, claim, or no-progress gate that spans a `/clear` is comparing two
conversations. Cross-run and cross-session queries stay possible and are the point
of §6.1 and §6.6 — they are just never *implicit*.

### 5.4 Project context card

Shipped as one user-owned Markdown file at `.swe-mux/project-context.md`.
It starts blank, is Project-scoped, and is prepended to scan-timeline calls as untrusted reference context.
swe-mux does not crawl `.docs`, `docs`, README files, routing tables, source files, or any other repository content to construct it.

The Timeline drawer exposes the file directly with a bounded editor, revision-checked atomic save, and a copyable setup prompt that asks an agent inside the Project to populate only that file from verified evidence.
Enabling Scan timeline for a Project creates the blank file lazily but does not generate content, authorize a run, or backfill history.
The active contract is in `design/features/project-card.md`.
The earlier generated, cached, model-written design is retired and archived at `development/archive/PROJECT_CARD_GENERATED_DESIGN.md`; its compatibility code and SQLite rows have no active consumer.

### 5.5 Scan timeline (Tier 1)

The one substrate layer that costs model tokens: a cheap model samples each
session and emits a compact **structured** record, and the records form a
per-session behavioral timeline. This is the compression cascade that makes
whole-session and cross-session analysis affordable: a 6-hour session's ~400k
transcript tokens become ~120 records (~5k tokens) that a strong model can read in
one cheap pass.

**What it reads.** The *delta* since the last scan (not the whole transcript),
plus its own last 2–3 records for continuity, plus the user-owned Project context and the
session's originating task. Tool calls are paired with their results (a Read alone
means nothing; Read+result means something), and the fat is stripped — file
bodies, full diffs, huge command output become "edited `layouts.py`, tests
failed," with the exact bytes reachable via the source pointer. Cost stays flat
regardless of session age.

**Emphasis on the human/agent message spine.** Tool churn is not the signal that
titles, summaries, and digests key off — what a session is *about* lives in the
user's actual asks (and mid-session redirections) and the agent's salient
responses and claims. The scan record must capture those as first-class, weighted
above interim tool chatter: each new or changed user request, and each agent turn
that states intent, makes a claim, or reports a result. This is what the absence
digest (§6.8) consumes (a continuous title would have too, before §6.11 was
abandoned); it is also the "read agent
and user turns, skip the interim" instinct made concrete — the delta-strip already
drops tool bodies, but the message spine is preserved and emphasized rather than
averaged into the noise.

**When it fires.** Event-triggered with a max-time heartbeat, *not* clock-only
(§2): tool completion/failure, test/build completion, git HEAD or meaningful diff
transition, process start/exit/crash, user input or wait transition, context
compaction, agent finish/error, detected no-progress episode, **conversation
rollover**; plus a Δmax of 3–5 minutes as a backstop so silence is still
represented. A fixed clock alone smears across causal boundaries (one record gets
the tool call, the next its result) and under/over-samples bursts vs waits.

**The agent run is the timeline's outer boundary.** A rollover (`/clear`, `/new` —
`ROADMAP.md` Phase 5.4) ends the current segment and starts a new one, and three
things reset with it: the delta window (the new conversation's transcript starts at
byte zero, so "the delta since the last scan" is meaningless across the seam), the
2–3 records of continuity context (feeding a model the predecessor conversation's
records is how it invents a continuity the agent does not have), and the `novelty`
comparison set. That last one matters most and is the least obvious: a `/clear`
that is *immediately followed by the same work restated* would score near-zero
novelty against the pre-clear records and be summarised as an unremarkable
continuation, when it is in fact the most significant transition in the session.
Compare within the run only. Segments remain joinable for a whole-session view —
they are just never silently concatenated.

**The record.** Multi-axis, not a single `state` field — lifecycle, behavior, and
work-phase are different variables and behavior is multi-label:

```text
{
  t0, t1, session_id, agent_run_id, schema_version,
  lifecycle_state,     # starting|running|waiting_user|waiting_tool|
                       #   rate_limited|errored|finished|stopped
  behavior[],          # grounding|retrieving|reasoning|planning|
                       #   executing|evaluating|reflecting  (multi-label)
  work_phase,          # investigation|implementation|test|debug|review|explain
  target[],            # files/subsystems, grounded in Tier 0 files_touched
  intent,              # what it is trying to do
  claim,               # verbatim if the agent asserts something (NOT paraphrased)
  user_ask,            # verbatim/near-verbatim new or changed user request
  blocked_on,          # user_input|tool_error|rate_limit|missing_context|
                       #   ambiguous_spec|none   (closed vocab; the differentiator)
  novelty,             # 1 - max_{i<t} sim(e_t, e_i)  — computed mechanically
  evidence_refs[],     # pointers into raw store (§5.2)
  tier0_rollup_id,     # link to the deterministic facts for this interval
  coverage,            # events_seen / represented / pending_results
  confidence,          # per-field, with a calibration id
  observer_model, prompt_hash
}
```

Design constraints the research forces:

- **`intent` and `claim` are separate fields.** "Plans to fix the parser" ≠ "the
  parser is fixed." Collapsing them is the reasoning-action-mismatch failure mode.
- **`novelty` is mechanical, not model-judged.** Embed the record; `novelty =
  1 - max over prior records of cosine`. Deterministic and free; frees the model
  budget for the semantic fields. (Use max-over-all-prior, not consecutive-only,
  to catch two-state oscillation.)
- **`blocked_on` is the differentiator.** No observability schema (OTel GenAI,
  OpenInference, Langfuse, Weave) captures "what is the agent waiting on." Closed
  vocabulary makes it aggregable.
- **Session token budget, not a hard per-record cap.** 8–20 tokens for an
  unchanged wait, 40–80 for an ordinary transition, 100–300 + refs for a failure
  or test outcome. Same ~5k session budget, but information-dense moments get room.
- **The scan timeline is capture-first, supervision-never in its own right.** Its
  most defensible immediate payoff is a readable timeline, automatic dead-end
  capture, and a continuous title; ranking/alerting is a downstream consumer.

**The strong-model contract (Tier 2).** Tier 2 reads the records + Tier 0 rollups
+ pinned constraints *by default*, and may **expand source pointers on demand**
when it sees low confidence, conflicts, state transitions, novel claims, or a
loop/stall alert. It is never records-only: MemCollab and Slipstream show that a
strong model with no source access inherits the cheap model's silent errors as
ground truth. Trend/portfolio views may stay fully compressed; diagnosis and
high-stakes conclusions must be allowed to rehydrate. Track the **rehydration
rate** as a first-class metric — it is the break-even variable for the whole
architecture (§2).

**Model choice.** Pick the Tier 1 model by a labeled extraction benchmark built
from real Claude Code / Codex event streams, not chat/arena rankings: schema
validity, field-level macro-F1, source-attribution precision/recall,
unsupported-claim rate, outcome-vs-intent confusion, confidence calibration
(Brier/ECE), and cost per million transcript tokens. Assume mechanical fields
survive a small model and semantic fields (`claim`, `blocked_on`, `target`,
`user_ask`) may not — A/B them, and use two model families to expose correlated
extraction errors.

---

## 6. Consumers

Each consumer names the substrate it reads and the concrete payoff. The first four
are the highest value-to-risk in the whole catalog (deterministic or nearly so,
exploit the all-sessions vantage, half-owned already).

### 6.1 Provenance graph  ← Tier 0

Deterministic lineage: every file read/write (by content hash), every test's input
snapshot, process ancestry, shared-resource holds, recorded as typed edges. Then
answer factually: *"session B wrote hash X to file F; session A's failing test ran
against a snapshot containing X."* **Never** *"B caused A to fail"* — causal blame
over event streams over-attributes (§2); the factual form needs no inference. This
kills the most expensive class of parallel-work debugging (why did this suddenly
break) by showing facts instead of making the human reconstruct them. Rated the #1
value-to-risk capability by the supervisory review. Foundations: Lamport
happened-before, Dapper propagated ids.

### 6.2 Dead-end / negative-result memory  ← Tier 0 + scan timeline

Auto-capture abandoned work — a file edited then reverted, an approach tried then
dropped — with the scan timeline supplying the "why." At an abandonment boundary,
ask the human one compact line to confirm scope and reason; retrieval is
demand-driven, surfaced when another session is about to repeat a concretely
similar approach (or pulled by an agent through the return path, §7). Git records
only what survived; design-rationale capture has been a good idea since the 1980s
and failed every time because *someone had to write it down* — passive capture
removes the only thing that ever killed it. Rated #2 value-to-risk, and it is the
capability that helps a solo hands-on-testing workflow today.

**A conversation rollover is not an abandonment.** `/clear` says the human reset the
context window; it says nothing about whether the approach failed. Treating the
boundary as evidence would manufacture a dead end out of every routine context
reset — and worse, retrieval would later warn a session away from an approach that
was actually working. Only an approach tried and dropped *within* a run counts.
The inverse case is real and worth capturing separately: work that was in flight
when the run rolled is **unfinished**, not abandoned, and that is a handoff signal
(§6.6 lineage), not a negative result.

### 6.3 Declared vs verified  ← Tier 0 (test facts) + scan (claim)

Keep three facts that usually get mushed into one strictly apart: the agent
*declared* done, the tests *passed*, the code is *actually correct*. An agent
saying "fixed, all green" gets no green status unless tests actually ran and passed
against the actual current code — and even then it reads "verified," not "correct."
Status renders as "claims done · tests not run," never a single misleading ✓.
Directly attacks the documented trap where developers treat passing tests as a
correctness guarantee (AgentLens: 20% of passing runs are clean; field studies:
green tests read as proof). Cheap, deterministic, high-signal.

Claims do not survive a conversation rollover. An open "claims done · tests not
run" from before a `/clear` belongs to a conversation that no longer exists, and
leaving it open means the next test run in the fresh conversation resolves a claim
the agent never made — the exact false-verification this consumer exists to
prevent. Close open claims at the boundary as `superseded` (retained, inspectable,
never silently satisfied) rather than carrying or deleting them.

### 6.4 Loop / stall detection  ← Tier 0 (fingerprints) + scan (recurrence)

Hybrid, gated on progress. Fire when the same canonical action fingerprint repeats
≥3 times, **or** a period-2/3 oscillation score crosses threshold, **or** semantic
recurrence crosses a *per-agent-calibrated* threshold — **and** no objective
progress in the window (failing-test set didn't shrink, no new diagnostic, blocker
unchanged, no target-relevant diff). The no-progress gate is what keeps it from
crying wolf on legitimate repeated test runs; the ≥3 threshold and ≥80% precision
target have production precedent (Wink, ~43k traces). Semantic similarity alone is
weak (F1 0.28); the structural signal is what makes it usable (hybrid 0.72). Feeds
the ranking layer with a confidence — it does not fire an interrupt itself at 0.62
precision. Remember stopping-behavior is ~half the failure mass: pair loop
detection with **premature-termination detection** (turn ended + completion claim +
no verification evidence + open todos).

Fingerprint and oscillation windows are bounded by the agent run. Repeating the
same action three times across a `/clear` is a human restarting from a clean
context, not an agent stuck in a cycle, and the no-progress gate would agree with
the false reading — nothing progressed, because the conversation was replaced. The
genuinely interesting cross-run version of this (the *human* keeps re-clearing and
re-attempting the same thing) is a different, slower signal and belongs to the
cross-session layer (§6.6), where the boundary is explicit rather than accidental.

### 6.5 Doc-debt ledger  ← Tier 0 (files changed) + project card + routing table

In this repo it is barely an LLM problem: `.docs/CLAUDE.md` is a literal
change-type → owning-docs routing table. On turn end, map changed files to routing
entries and mark those docs dirty, with a pointer to the diff. **Do not nag per
turn** — accumulate a debt ledger with a visible count; when the human hits a
stopping point, one strong pass clears everything dirty since the last pass with
all accumulated diffs as context. One expensive call instead of forty
interruptions, and it enforces the project's own "docs must agree" completion
policy. Documentation is one consumer among many here, and one of the cheapest
because the routing table already did the hard part.

### 6.6 Cross-session interlocks  ← provenance graph

Referee environmental collisions agents are individually blind to: two sessions on
the same branch/subsystem, port collisions among owned listeners, one session's
dev server feeding another's tests. Temporal correlation is stronger than static
matching but must stay "candidate contributor," never blame (§2). Composes directly
out of the provenance graph plus owned-listener data.

### 6.7 Attention ranking / the inbox  ← everything

The top consumer, and the one that makes every other one viable. It decides what is
worth interrupting the human for. Non-negotiable shape from the HCI review:

- **Fan-out estimate as the headline.** agents-per-human ≈ neglect-time ÷
  interaction-time (Olsen & Goodrich). The scan timeline + per-project baselines
  give neglect time; attach/input telemetry gives interaction time. Surface *"you
  are sustainable at ~3 attended agents right now; 4 more are queued for review,"*
  not a wall of per-session status lights. No vendor can compute this — it needs
  both halves, which only the layer owning the human's terminals has.
- **Budget interrupts per day, small.** ~1% audit budget / "a few a day." An
  hourly cap silently authorizes 8–16/day, already fatigue territory; keep it only
  as a burst limiter under a daily budget. Budget is incident-based: many detector
  outputs about one underlying event consume one slot.
- **Four channels, split by cost-to-resolve, never merged.** interrupt-now
  (irreversible/worsening + concrete action + high confidence) · next-breakpoint
  (actionable, minutes of delay fine) · inbox (schedulable) · timeline/digest
  (anomaly, weak association, no action). Merging cheap-blocking (permission
  grants, y/n) with expensive-blocking (wrong plan, spec ambiguity) is the single
  most common design error and is the clinical-alarm failure mode. Cheap-blocking
  never spends interrupt budget; batch and drain at the human's next pause.
- **Deliver at the human's breakpoint, not the agent's.** swe-mux owns the human's
  terminals too — OSC 133 prompt markers in the *supervisor's* shell reveal "their
  test run just finished," empirically the strongest interrupt moment. No vendor
  can see the human finishing a build in another window. Predict relevance, observe
  timing (relevance is ~91% predictable, opportune-moment ~60%).
- **Learn from behavior, show the rule.** Mine dismiss/act/edit/rollback, surface
  the induced rule for accept/reject (PrefMiner). Never build policy on stated
  preference (METR self-report gap). The learning objective is expected avoided
  loss per unit attention, not click-through.
- **Complacency countermeasures are mandatory** (design law 7): display
  suppressed-item counts, keep rationale evidence-first not persuasive-prose,
  periodically force a judgment call.
- **Instrument resumption lag**, not throughput. Interrupted work completes faster
  but pays in stress that throughput hides (Mark 2008); resumption lag is the real
  cost function and swe-mux can measure it from focus/input telemetry.

### 6.8 Absence report / digest  ← event log + scan timeline

"What happened while I was away" as a query: everything the fleet did, decided, and
got stuck on since the last attach/input, in one narrated digest. Composes entirely
from the scan records (leaning on the user/agent message spine, §5.5) + events; no
CLI knows when the user was watching. The mobile/Telegram payoff, and the natural
home for everything demoted out of the interrupt channel (completions are ignored
50% of the time — they belong here).

A conversation rollover inside the absence window is rendered as a boundary, not
smoothed over. "You cleared this session here" is the single piece of context that
makes the rest of a multi-run digest legible, and a digest that narrates two
conversations as one continuous stretch of work is actively misleading about what
the agent currently knows.

### 6.9 Sibling consumers (need substrate but not the scan timeline)

- **Screenshot-to-agent** ← project card + browser surface. Right-click a pane →
  send its screenshot + viewport + relevant component tree to the focused agent.
  The single biggest per-iteration cost in a hands-on UI-testing loop is
  translating "this looks wrong" into words; this collapses it to a click.
  Playwright is already in the frontend deps and the mux owns the browser surface.
- **Observation inbox** ← nothing but a keystroke. A capture surface that drops a
  line into a per-project list without leaving the browser or switching to the
  agent, so a testing pass ends with six items instead of one plus a nagging sense
  of five forgotten. No AI at all; hands the batch to the agent at once instead of
  one-at-a-time context switching.

### 6.10 Novel capabilities only the control plane enables

The CLIs are first-person and present-tense; the control plane is third-person and
has memory. These require seeing *all agents, all time, one machine* at once:

- **Cross-vendor second opinions.** One keystroke: "have Codex review what Claude
  just did" — spawn the other backend in the same cwd with a generated prompt
  referencing the diff. Adversarial review across labs with independent failure
  modes; structurally exclusive to a neutral control plane; cheap (spawn + prompt
  template). User-initiated, so it ships as a palette/universal-command before it
  is ever a rule action.
- **The experience database.** Error→resolution evidence mined across both
  backends, all sessions, all time, into a per-machine cumulative cache; an
  observer annotates a live failure with a prior resolution, and the return path
  (§7) lets an agent pull it directly. Precision risk: error-string similarity is a
  poor retrieval key, and a usually-wrong `prior-resolution` poisons trust in the
  whole annotation surface — require exact normalized error signature + same
  project before annotating or returning.
- **Scaffold telemetry / A-B.** Both harnesses observed on real work under
  identical conditions: which stalls more on this repo, cost per completed task,
  approval-interrupt rate. The mux as an eval harness *of scaffolds*, on the user's
  actual workload. Explicitly correlation, never a causal benchmark.
- **Session lineage.** Resumes, handoffs, continuations, reviews as a graph, giving
  *work items* continuity across sessions and backends. The CLIs have sessions; the
  mux can have threads of work.

### 6.11 Continuous session title  ← ABANDONED

**Not being built.** Retained as a record of why, because the idea is an easy one to
have twice.

The premise was that a title should track the work. Field behaviour showed the
opposite requirement: a title's job is to be a *handle* — the thing a user finds a
tab by after not looking at it for an hour — and a handle that moves is not one. The
shipped `turn_ended` stage was already a weak version of "adapt as the session
progresses", and it was the source of the complaint that titles "gradually become
something not clear": naming a run from its most recent turn produced `OK`,
`FrozenClaude`, `Reply FROZENCODEX` for runs whose actual subject was stable and
obvious from the opening request.

What ships instead (`design/features/automation.md`): one title per `agent_run_id`,
taken from the request the run opened with and pinned durably so retries and daemon
restarts reproduce it; the `turn_ended` reading demoted to a fallback for runs whose
request was never captured (Codex); background retry on provider failure. A rollover
still retitles, since that mints a new run.

The rest of this section is the abandoned design.

How it would have stayed efficient and safe:

- **Derive from the scan timeline, don't re-read transcripts.** The records already
  capture `user_ask`, `intent`, `claim`, `work_phase`, and `target` (§5.5). A title
  is a cheap derivation over the most recent records — often the current
  `work_phase` + primary `target` + latest `user_ask`. The scan pass is already
  paid for; the title rides on it rather than issuing its own reads. This is why
  §5.5 emphasizes the user/agent message spine: the title (and the digest) are its
  main consumers.
- **Recompute on material shift, not per turn.** Gate on a meaningful change —
  `novelty` spike, `work_phase`/`target` transition, or a new `user_ask` — with
  debounce and hysteresis so the title doesn't flicker and doesn't cost a call
  every turn. Interim tool chatter never moves it.
- **A conversation rollover always retitles.** It is the strongest material shift
  there is: the tab is describing work the conversation no longer contains. This
  is also the case today's one-shot titler gets visibly wrong — its reserve is per
  `agent_run_id`, so before `ROADMAP.md` Phase 5.4 a `/clear`ed session kept its
  pre-clear title for the rest of its life. With the run boundary in place the
  existing reserve becomes correct rather than sticky, and the continuous titler
  inherits the fix instead of re-solving it.
- **Explicit rename wins, permanently.** If the user renames the shell/session, the
  title is pinned and auto-update disables for that session. User intent is
  authoritative; the automation never overwrites a human-chosen name — and the pin
  is a property of the *session*, so it survives a rollover too. A human who named
  a tab did not un-name it by clearing the conversation.
- **Compact task labels**, no backend or "terminal session" prefixes — the existing
  titling convention carries over.

**One piece was kept.** `builtin.session-titler-initial` fires on `turn_started` and
titles the pane from the user's submitted prompt. It shipped as the "provisional" half of
a two-stage scheme and is now the whole titler, because the request turned out to be a
better title source than the completed turn rather than a placeholder for it. It also
needs neither a transcript nor semantic observation, which fixed titling in the
degraded-observation states that were failing it outright in the field (5 of 6 observed
failures were `observer requires semantic observation; current capability is inferred`).

---

## 7. Return path: making insights available to the data plane

Everything above is the control plane *observing* the agent. The return path is the
inverse arrow: how accumulated insight gets back *into* the agent while it codes.
Closing this loop is what makes the system compound instead of merely accumulate.

**Principle: pull, not push.** The control plane is a queryable memory; the agent
consults it on its own initiative. This is the only channel that respects every
design law at once — swe-mux never enters the agent's execution path, it just
answers when asked. The agent reaching out to swe-mux is the agent using a tool,
same as any other; swe-mux is not gating the turn, so it stays out-of-band.

**Mechanism: a mux MCP server.** Both Claude Code and Codex speak MCP, so swe-mux
exposes tools the agent calls mid-coding when relevant. The memory tools below are
the v1 target; the smaller read/discovery surface that ships first is §7.5, and the
two bounded write tools are §7.2:

```text
mux.provenance(file)        → who touched this, what hash, what tests ran on it
mux.priorResolutions(error) → "this exact error was hit in March; fix was Y"
mux.deadEnds(subsystem)     → approaches tried and abandoned here, and why
mux.verifiedStatus(claim)   → is this actually tested, or just declared done
mux.searchHistory(query)    → cross-vendor transcript search (mostly built)
mux.memorySources()         → exact Project instruction/provider-memory inventory
mux.readMemory(source_id)   → bounded read of one attributed inventoried source
```

The control plane's third-person, all-time, all-sessions memory becomes available
to the first-person agent, on the agent's initiative, with no injection. The last
two tools also expose the provider-owned memory that Phase 6's read-only Agent Context
drawer inventories. They are exact-source reads rather than semantic matches: raw vendor
memory is attributed context, not verified control-plane evidence.

**Graded ladder of channels, by authority:**

1. **Pull (agent-initiated).** The MCP tools above. Safest, primary. The agent
   decides to consult.
2. **Instruction sync (curated, durable).** Roadmap Phase 6 begins with a read-only
   Agent Context inspector and an explicit human-triggered whole-file
   `CLAUDE.md ↔ AGENTS.md` overwrite in either direction. That direct copy is never
   scheduled, watched, remembered, or exposed to agents. Slow-moving distilled
   control-plane insights — a mined convention, a recurring failure mode — use the
   later governed path: sentinel-delimited sections of those provider files. The agent
   sees generated sections as standing context every session, no query needed. Right
   for stable facts, wrong for live ones.
3. **Human-mediated injection (queue-draft / universal commands).** Live insight →
   inert draft → human approves → it enters the agent (§13). Or `/handoff` seeds a
   new prompt with relevant provenance and dead-ends. Human in the loop, per the
   actuation gate.
4. **Actuation (reserved, gated).** The observer autonomously injecting. Stays
   behind the safe-to-inject predicate (§16); not near-term.

**Precision is a trust gate, not a nicety.** An agent that calls `priorResolutions`
and gets a plausible-but-wrong match learns to stop calling it — and worse, may act
on the bad match. The pull tools must return high-precision, tightly scoped results
(same project, exact normalized error signature, verified provenance) and return
**nothing** rather than a weak guess. Empty is fine; wrong is corrosive. Same
principle as the interrupt budget: a usually-wrong signal is worse than none.

**Attribute the run, always.** A result may come from a sibling session, from this
session's own superseded run, or from months ago; all three are legitimate, and the
agent must be able to tell them apart. The dangerous case is the middle one: after a
conversation rollover the agent has no memory of what its predecessor run did, so an
unlabelled result from that run reads as its own recollection and is trusted at
exactly the wrong strength. Carrying `agent_run_id` on every returned record costs
nothing and is what keeps "the control plane remembers" from becoming "the agent
misremembers".

**Implications for the substrate.** Every artifact must be **addressable and
retrievable** — the source pointers, provenance edges, and scan records are not
just for the human UI, they are the index behind these tools. And insights need a
**confidence/scope tag** so the retrieval layer can withhold low-confidence items
from the agent even while showing them (with a suppressed count) to the human.

### 7.1 MCP is transport, not authority

The MCP server is an interface, not a permission model. Every tool it exposes is a
thin caller over the same typed daemon operations the browser, CLI, and mailbox use
(`ROADMAP.md` Phase 7). Nothing is implemented *inside* the MCP layer.

This matters most for the write tools. Building agent-to-agent messaging into the
MCP server itself would create a second delivery path that skips the Phase 4/5
queue's readiness contract, head-of-line ordering, provenance, and loop bounds —
two delivery semantics to keep in sync, and the newer one is the one an agent can
reach without a human. The rule:

> **The daemon owns the queue and every safety predicate. MCP is one more client of
> it, alongside browser, CLI, and mailbox.**

Corollary: chain depth, cycle detection, per-origin budgets, target allowlists, and
the loop kill switch live in the typed daemon operation. If they lived in the MCP
layer, the browser and mailbox paths would not get them, and every later client
would have to reimplement them.

### 7.2 Write tools: `notify` bounded, `spawn` drafted

The request that motivates this (`.swe-mux/notes/project.md`: "agent A finishes and
notifies agent B, and sometimes spawns a new session for B") is three asks of
different risk classes. They must not be gated together:

| Ask | Risk | Disposition |
|---|---|---|
| See active, prior, and concurrent sessions | read-only | MCP v0. No gate. |
| Read a session's conversation from either end, and your own superseded runs | read-only | MCP v0.5 (§7.5), step 2.6. No gate. |
| Notify a specific existing session | writes into another agent's input | Phase 5 queue op, surfaced as an MCP tool. |
| Spawn a new session to receive the message | creates a new actor, spend, and account use | Gated (§16); drafted today, and the grant decision moves to step 9 (§7.6). |
| Interrupt another session's current turn | discards in-flight work; is a PTY write | Step 9 (§7.6), behind the readiness predicate and the authority grant. |
| End a session, including your own | destroys a running actor | Step 9 (§7.6), graceful-first, drafted by default, never erasing the record. |

`mux.notify(target, body)` is a tool-shaped caller over the Phase 5 A→B message
operation: the message enters the target's queue, waits for receiver-side readiness,
never interrupts an active turn or bypasses approvals/Q&A, and carries the calling
session as provenance. Session A gains no knowledge of or authority over session B
beyond what the target allowlist grants.

Spawn stays out of agent reach, but a flat refusal loses the workflow actually
wanted. The middle path is the §13 queue-draft pattern applied to spawn:
`mux.requestSpawn(...)` creates an **inert draft in the observation inbox**, not a
session. The human approves it with one tap, including from the phone, and the
approved draft is what starts the session with the handoff prompt pre-seeded. The
agent never holds spawn authority, and approval stays a single deliberate act rather
than a standing policy exception. This is the one boundary worth being rigid about:
a prompt-injected agent that can spawn workers is a fan-out amplifier, and that is
the failure mode a queue purge cannot undo.

### 7.3 Where the server runs, and what the supervisor split changed

The PTY supervisor split (`SESSION_PRESERVING_RELOAD.md`, shipped 2026-07-23)
constrains the return path in ways this section predates:

- **The MCP surface belongs in the daemon, never the supervisor.** §8 of that
  document is the hard rule: the supervisor cannot be updated without killing every
  live session, so it stays tiny and near-frozen (~600–800 lines). A tool surface
  that grows a new tool per consumer is exactly the churn that must live on the
  volatile side.
- **Prefer a streamable-HTTP endpoint on the daemon over a stdio server.** The daemon
  is already an HTTP server on a stable configured port. stdio would mean a
  subprocess per session, an MCP entry point shipped inside the frozen bundle, and a
  server process living inside the supervisor's reaper Job. HTTP gives one
  implementation, per-session auth as a header, and nothing new to package. *Verify
  before committing:* Claude Code supports http/sse transports; confirm the targeted
  Codex version supports streamable HTTP in `mcp_servers`. If it is stdio-only, ship
  a thin stdio shim that proxies to the daemon endpoint, keeping the daemon as the
  single implementation either way.
- **Tool calls must tolerate a daemon restart.** `POST /api/daemon/restart` and the
  redeploy cycle replace the daemon process while agents keep running, so an
  in-flight MCP call can fail through no fault of the caller. Tools return a typed
  transient error the agent may retry; they never return a partial or fabricated
  result. The listen port is stable across restarts, so the CLI-side server config
  never has to be rewritten.

### 7.4 Caller identity is injected, never claimed

A tool that accepts a `from_session` argument has forgeable provenance, which makes
per-origin budgets, allowlists, and cycle detection decorative. swe-mux spawns every
session, so it mints a per-session token at spawn and injects it into the session
environment; the daemon derives the caller from the token, and no tool signature has
a sender parameter.

Two consequences to design for up front:

- **Tokens must be persisted, not in-memory.** The daemon now restarts under live
  sessions by design. An in-memory token table would invalidate every live session's
  MCP credential on each reload — precisely the operation the supervisor split exists
  to make routine.
- **A token's default scope is its session's Project.** Cross-project reads are a
  separate explicit grant. The default answer to "what may this agent see" is its own
  Project, consistent with per-project opt-in (§8).

**Same-host boundary decision (2026-07-28, re-affirmed 2026-07-29 with Phase 5):**
same-host agents are fully trusted — the token is identity and read scope, not
authorization; the un-tokened mutating HTTP surface is unchanged. The Phase 5
re-examination concluded the proposed enforcement (token check on mutating routes +
daemon-local browser bearer) cannot deliver the property: a same-user process on this host
can request whatever credential the browser is given, so the only real boundary is OS-level
isolation (an ACL'd per-user pipe the browser holds and sessions do not). Consequence to
carry forward: **the budgets, allowlists, depth caps, and cycle detection in §7.2 bound
well-behaved callers, not a compromised one.** What compensates is that agent-reachable
authority is strictly narrower than the browser's — no tool delivers, spawns, or writes to a
PTY. Full reasoning: `design/features/agent-messaging.md`.

### 7.5 Shipping order: a v0 read surface long before v1 memory

The §9 build order originally placed the whole return path last, which conflates two
very different dependency profiles:

- **v0 — buildable now.** `searchHistory` (mostly built), list active/prior/concurrent
  sessions, session status and metadata, transcript read. These depend on machinery
  that already exists plus the Phase 3.5 status contract; nothing in Tier 0, the
  project card, or the scan timeline. v0 is also the entirety of the "agents can see
  concurrent sessions" utility, which is useful on its own and is the cheapest way to
  prove the transport, identity, and restart-tolerance decisions above.
- **v0.5, the gap between them, buildable now.** Bidirectional transcript paging (the
  *beginning* of a conversation, not only its tail), reads of the caller's **own superseded
  runs**, the run brief on `get_session`, `message_status` for a `notify` sender, Project notes
  read-only, and the `memorySources` / `readMemory` pair pulled forward from v1. Every one
  is a read over a service that already shipped, so this is a tool-surface phase and not a
  substrate phase. It needs Phase 5.4's run boundary, because reading across a rollover
  without naming the run is the precise failure the retrieval gate exists to prevent. Ships as
  `ROADMAP.md` Phase 5.6 / step 2.6.
  Dropped from v0.5 on 2026-08-10: `project_card()` and the bounded Git read, because a caller
  with shell access answers both itself.
- **v1, genuinely late.** `provenance`, `priorResolutions`, `deadEnds`, and
  `verifiedStatus`. These need substrate that steps 1–5 produce and cannot ship earlier, and
  they split again by dependency: `provenance` and `verifiedStatus` read Tier 0 and the shipped
  step 3 detectors, while only `priorResolutions` and `deadEnds` need the step 5 timeline.

Ship v0 as its own small phase, add `notify`/`requestSpawn` when the Phase 5 queue
lands, ship the v0.5 reads once the run boundary exists, and keep v1 in step 8 where it
belongs. Session control (§7.6) is later still and is the only part of the surface that
carries authority rather than answers.

**Roadmap composition.** The MCP surface is a natural extension of the typed daemon
operations Phase 7 already wants (browser, CLI, mailbox routed through shared typed
ops — MCP is one more consumer). Agent Context inspection, manual root-file overwrite,
and governed instruction rendering are Phase 6; the queue-draft path is Phase 4/5. The
return path is a read API layered over machinery already
scheduled, not a new pillar. In `ROADMAP.md` it lands in five places: **Phase 4.5**
(v0 read/discovery), the Phase 5 agent-to-agent section (`notify`/`requestSpawn` as
callers over the queue), **Phase 5.6** (v0.5 situational-awareness reads, step 2.6),
**Phase 7.5** (v1 memory tools, this document's step 8), and **Phase 7.6** (session control,
step 9, the one part that is not a read).

### 7.6 Session control: the first agent-reachable actuation

Everything else in the return path answers questions or drafts a request. Interrupting and
ending sessions is different in kind: it acts on a running actor. It is scheduled rather than
left permanently decision-gated, because the capability is genuinely wanted (an agent that
watches a sibling wedge in a loop, or that finishes the job a worker was spawned for, should
be able to stop it rather than only report it), but it is scheduled *last*, with the strictest
machinery in the design around it. Delivered as `ROADMAP.md` Phase 7.6.

**Two capabilities, two tools, separately grantable.** `interrupt(target)` stops the current
turn and leaves the session alive; `end_session(target)` destroys the actor. Merging them into
one "stop" verb would make the smaller authority carry the larger one's risk, which is the same
error §6.7 warns about for interrupt channels.

**An interrupt is a PTY write, and therefore §16's gate applies unchanged.** It goes through
the fail-closed `safe|blocked|unknown` readiness contract, and `unknown` never authorizes.
The escape sequence belongs to the adapter, the same place the voice interrupt path already
resolves it, because a keystroke encoded in the MCP layer is a native detail leaking above
the adapter boundary (design law 3).

**Ending is graceful-first, and the graceful path does not exist yet.** Today's teardown is a
hard stop that marks the record `killed`. The typed operation this needs is: interrupt, send
the harness's own exit sequence, wait bounded for the CLI to tear itself down so the transcript
flushes and the run closes cleanly, and hard-stop only on timeout. The end reason must
distinguish `agent_ended` from an operator `killed` and from a CLI `exited`, or the durable
record loses the one fact a post-mortem needs.

**Self-termination is allowed and is the ordinary case.** A finished worker ending itself is
the point of the feature. Two constraints make it safe rather than merely convenient: the tool
returns before teardown begins, and the ended session's transcript, final turn, and history row
are retained and readable afterwards. An agent may end itself; it may not erase itself. This
is also the answer to the termination-unawareness failure mode (§2): the human's ability to read
what happened cannot depend on the agent's judgment about whether it mattered.

**Authority is a per-Project three-position grant, defaulting to drafted.** `off` refuses with a
typed result; `draft` writes an inert observation-inbox request with full provenance, exactly
like `requestSpawn`, and a human approval is what acts; `granted` acts directly inside a
per-origin budget with a full audit trail. This is deliberately the same shape spawn has been
waiting for, and §16's spawn decision is made here or nowhere: a permanent "drafted forever" for
one capability and a real grant model for another would be an inconsistency the user would have
to work around rather than a boundary.

**Bounds are the ones `notify` already proved**, for the same reason they lived in the daemon
operation there: Project scope, live-agent targets only, per-origin budgets, chain depth and
cycle detection over the recorded path (A interrupting B while B interrupts A is a loop), and a
master kill switch. Two targets are never reachable at any grant level: a session outside the
caller's Project, and the session that owns the running daemon, because job-object inheritance makes
ending that one take the daemon with it.

**Nothing here is silent.** An agent stopping another agent emits to the event log with the
caller's session and run as provenance, appears in the fleet audit surface beside agent
messages, and is a candidate for the §6.7 attention channels. And it never grows an automatic
remediation: "interrupt and re-run the turn" is resampling, which §16 rules out on evidence.

---

## 8. Enablement: per-project opt-in and the dependency graph

Design law 8: nothing runs on a project that did not opt in. The mechanics:

- **Automations are per-project opt-in.** Anything that spends model tokens or
  consumes attention is enabled explicitly, per project, in `.swe-mux/config.toml`
  (which already carries an "enabled scope" field for the prompt library). This is
  not only preference — it is the Phase 5 trust posture: an untrusted repo must not
  silently make model calls on the user's account or reach actuation.
- **Substrate is per-project too, but inert.** Event-log and Tier 0 capture record;
  they never act or spend. A consumer cannot be enabled unless its substrate
  dependencies are enabled for that project. Enablement is therefore a **dependency
  DAG**: turning on the provenance graph auto-requires Tier 0 capture; turning on
  dead-end memory requires Tier 0 + the scan timeline; the ranking layer requires
  whatever detectors feed it. The UI presents the dependency when you toggle a
  consumer, and disabling a substrate node disables its dependents.
- **No global automations.** There is no `rules.toml` that executes on every repo.
  Global config exists only as an **inherited default template** a new project
  adopts and then opts into — you get the ergonomics of not reconfiguring titling
  per repo, without machine-wide execution on untrusted code.
- **Cross-project consumers are aggregators over the opted-in set.** Fan-out and
  the absence report are inherently machine-wide, but they are not global
  automations: they only ever see data from projects that opted into producing it.
  A repo you never enabled contributes nothing. The rule holds — nothing runs on a
  non-opted-in project — while the cross-project views operate over the opted-in
  set.
- **Config-value precedence is a separate axis.** Once an automation is enabled, a
  setting still resolves session/request → project → global-default. That value
  precedence is distinct from enablement gating; do not conflate them.

Project-scoped rules and scripts remain subject to the Phase 5 project-config trust
boundary: executable behavior requires an explicit trust decision, same as project
config generally. Files are the source of truth; the Settings UI is a two-way
editor over them, preserving the atomic-write/last-known-good machinery and making
**Claude itself a capable rule author** (an agent that knows the schema turns "ping
my phone when a builder stalls" into a valid, reviewable rules file).

---

## 9. Implementation roadmap and status

Ordered by the enablement DAG (substrate before consumers, deterministic before model),
then pulled forward where it helps a solo hands-on-testing loop today. Checkboxes track what
has actually shipped — implementation + tests + docs must agree before a box is checked — so
another agent can pick up mid-plan. Section links point to the design detail.

### Build order

- [x] **0 · Enablement framework** (§8). Per-project opt-in + dependency-DAG gating; must
  exist before any consumer.
  - [x] Registry + cycle-checked DAG + resolver (`automation_registry.py`)
  - [x] Per-project `automations` opt-in in `.swe-mux/config.toml` (parse/serialize/validate)
  - [x] Gate wiring + tests (`test_control_plane_enablement.py`)
- [x] **1 · Substrate: Tier 0 + raw store** (§5.2–5.3). Deterministic fact capture.
  - [x] `tier0_facts` store, gated per-project capture, source pointers (`tier0_store.py`)
  - [x] Race-free content hash + normalized target at the adapter boundary
    (`observation.tool_call_evidence`)
  - [x] Raw store: native transcripts authoritative + `source_seq` pointer (half — enough)
  - [x] Bounded-but-lossless capture: `detail_json` is bounded per value, never by slicing
    the serialization (which produced unparseable rows that were then dropped whole,
    deterministically destroying exactly the long test/build facts §6.3–6.4 need); capture
    failures are counted and surfaced at `GET /api/diagnostics/background`
  - [x] Ownership on every fact: `agent_run_id` + `project_id` resolved at capture time.
    Per-run queries cannot be recovered from `session_id` across promotion, branch, or a
    Codex resume — and, from `ROADMAP.md` Phase 5.4, across an in-CLI `/clear` or `/new` as
    well, which is the one run boundary the daemon used not to draw. A **Claude** resume is
    the opposite case and draws no boundary: it continues one conversation onto a new PTY, so
    the run (and everything keyed by it) carries across the new `session_id`
  - [x] `tool_result` facts classified per action (`command_result`, `file_read_result`, …)
    with the tool's target correlated forward from its `tool_use`; the exit class no longer
    collapses success and failure onto one fingerprint
  - [x] Structured test facts: `{framework, passed, failed, errors, skipped, failing_tests[]}`
    parsed at the adapter boundary (pytest, jest/vitest, go, cargo, unittest), with the
    failing set folded into the fingerprint — this is the substrate §6.4's no-progress gate
    ("the failing-test set didn't shrink") queries
  - [x] Git commit/tree hashes: `git_changed` carries the commit `head` and a `dirty_hash` of
    the working-tree change set
  - [x] Read-side file hashes: a tool result hashes the exact bytes the agent saw, before the
    payload's `detail` is bounded
  - [x] **Gap resolved by decision, not by code:** write-side and read-side hashes are not
    joinable by equality — a `Read` result hashes the CLI's *rendering* of a file, not the
    file. The provenance edge is therefore **restated as `target` + time order**, carrying
    the writer's content hash as the thing that was written. The rejected alternative was a
    per-backend normalizer reconstructing file bytes from a lossy, version-drifting
    rendering, which truncated reads make impossible in general. Where another write to the
    same target falls between the write and the read, the edge is marked `ambiguous` rather
    than asserted — that is exactly when "the reader saw this write" stops being a fact.
    Recorded in `design/features/deterministic-consumers.md`.
- [x] **2 · Helps-today siblings** (§6.9). Cheap, high daily leverage, no scan-timeline dep.
  - [x] Observation inbox (`.swe-mux/observations.json`, no AI) — full stack + tests
  - [x] Screenshot capture (full + drag region) → copies a reference to the clipboard;
    optional Playwright backend; saves into the Project's `.swe-mux/preview-shots/`,
    swept at 7 days. Endpoint coverage for the unavailable-backend shape and the
    shot-directory resolution is in `tests/test_processes_phase4.py` (no Chromium needed).
- [x] **2.5 · mux MCP v0: read + discovery surface** (§7.5). Shipped 2026-07-28 as
  `ROADMAP.md` Phase 4.5. Design: `design/features/mux-mcp.md`.
  - [x] Streamable-HTTP MCP endpoint on the **daemon** (`POST /mcp`, never the supervisor,
    §7.3), per-session tokens minted at spawn, persisted via supervisor-meta recovery
    (the hook-secret pattern — no token table), Project-scoped
  - [x] Read tools only: `list_sessions`, `get_session`, `read_transcript` (bounded),
    `search_history` — thin callers over `SessionManager`/`HistoryIndex`, allowlisted
    output, credential-shape redaction, not-found identical for scope and true misses
  - [x] Auto-registration per backend: Claude `--mcp-config` (env-expanded bearer), Codex
    0.145 native streamable HTTP `-c` overrides (verified; no stdio shim), plus both shims
  - [x] Restart-tolerance: retryable transport error mid-reload, no partial results, token
    survives adoption, typed 401 for a token the daemon no longer knows. Also closed the
    human-input evidence hole (Phase 4.5 checklist) so readiness shadow metrics are honest
    before Phase 5 reads them.
  - [x] **§7.2 write tools shipped 2026-07-29** with `ROADMAP.md` Phase 5: `notify` (a thin
    caller over the queue's typed enqueue — every bound lives in `agent_messaging.py`, not
    in the tool) and `request_spawn` (an inert Fleet Queue approval row; approval is a human
    act and is what spawns). One audit trail with the browser path; sender derived from the
    token. Design: `design/features/agent-messaging.md`. The same-host boundary was
    re-examined and re-affirmed with its limits written down (§7.4).
- [x] **2.6 · mux MCP v0.5: situational-awareness reads** (§7.5). Shipped as `ROADMAP.md`
  Phase 5.6. Tool-surface work only: every item is a read over a service that already
  shipped, so it adds no substrate and no authority. Needs step 3.5's run boundary.
  - [x] `read_transcript` reads from **either end** with an opaque cursor, so an agent can read
    the *beginning* of a conversation. The opening request is what identifies a run's work
    (the finding that killed §6.11), and it is currently unreachable on any session long enough
    to matter. Paging stays inside one `agent_run_id`; system/meta records are excluded by
    default with an explicit opt-in; caps and credential redaction are unchanged.
  - [x] A caller may read **its own superseded runs**. After a `/clear` the agent retains
    nothing its predecessor did and the daemon has all of it. Every message names its
    `agent_run_id`/`agent_run_seq`, and a result from the caller's own earlier run is labelled
    rather than blended into the present: §7's attribution rule applied where it matters most.
  - [x] Run brief on `get_session`: the run's pinned title and opening request, so "what is
    that session working on" costs one small call instead of a paged transcript read.
  - [x] `message_status(id)` closes the `notify` loop for the sender, and the Project's **notes**
    become readable, which is the human-to-agent channel with no new trust boundary. Retargeted
    2026-08-10 from the observation inbox to notes, because notes are where humans actually
    write: Project-scoped, searchable, editable, already carrying "send to agent".
  - [x] **Consolidate the observation inbox out of existence.** It exists because `requestSpawn`
    needed somewhere inert to land, and note capture was retrofitted onto it, leaving a third
    surface to monitor beside the per-session queue and the fleet queue that notifies nothing.
    Pending spawn drafts move into the fleet queue as an approval row, so one place holds
    everything an agent wants from a human; a spawn request naming no target session is a
    grouping problem in a view that already renders sender provenance, not a second surface.
    Approval stays an explicit once-only human act over the unchanged `seed_text` spawn path.
  - [x] `memory_sources()` and `read_memory(source_id)` over the Phase 6 Agent Context
    inventory, pulled forward from step 8 (2026-08-10) because they are thin callers over a
    shipped read and do not need this step's semantic substrate. Agent Context is now
    harness-declared rather than a claude/codex special case. Raw memory stays
    unverified, pull-only, Project-scoped, and attributed; never bulk-injected, written by MCP,
    or copied into another harness's private store.
  - **Dropped 2026-08-10: `project_card()` and the bounded ground-truth Git read.** A tool earns
    its place only by answering something the caller cannot answer itself, and every agent
    session is a CLI with shell access: `git status` and `git diff --stat` are one command away,
    and a sibling's worktree path already comes from `get_session`. Design law 6 still holds
    (condition on the diff, not on the sibling's story about it); it does not need a tool to
    hold.
    The former generated internal Project card was retired on 2026-08-13 in favor of the user-owned `.swe-mux/project-context.md` file.
    A future `project_card()` MCP read would expose only that reviewed file and still requires evidence that it answers something the caller cannot read directly.
  - [x] Decide the cross-Project read question (§18) explicitly. Default stays own-Project;
    "what else am I working on right now" is inherently cross-Project and needs a named grant
    if it is ever answered, not a quiet scope widening.
    Decision 2026-08-12: v0.5 ships no cross-Project grant.
- [x] **3 · Deterministic consumers** (§6.1, 6.3, 6.4, 6.5). No model; write to `annotations`.
  Design: `design/features/deterministic-consumers.md`.
  - [x] Annotation anchor + evidence schema: `automation_annotations.agent_run_id` is now
    nullable beside a `project_id` anchor (a project-scoped detector has no run), findings
    carry `evidence_json` — the *set* of Tier 0 facts the case rests on — and a `dedupe_key`
    makes a re-running detector idempotent. `AutomationStore` gained the additive migration
    path it never had; the rebuild is gated on the new column being absent.
  - [x] Loop/stall deterministic half (§6.4) — fingerprint repeat ≥3 with the no-progress
    gate (failing-test set did not shrink, no new write hash, no second git head).
    Calibrated live 2026-07-28: only change-attempting kinds (`command`/`file_write`/
    `test`/`test_result`) seed a loop — the gate is vacuously true for read-only actions,
    which flagged repeated Greps as a loop.
  - [x] Declared-vs-verified (§6.3) — Tier 0 test facts + a narrow completion-claim pattern,
    reported as three separate facts and never one ✓
  - [x] Doc-debt ledger (§6.5) — the routing table is keyed by change *type*, which no
    machine can match to a path, so ownership is inverted from each doc's literal
    **"Key files"** section instead. Accumulates a count; does not nag per turn.
    Calibrated live 2026-07-28: a file claimed by >4 docs (`server.py`: 15, `App.tsx`: 8)
    is infrastructure and carries no ownership signal — one `App.tsx` edit had marked
    eight unrelated docs dirty.
  - [x] Provenance graph (§6.1) — `target` + time order per the step 1 decision, cross-session
    only, with `ambiguous` marking an intervening write. Never a causal blame label.
    Calibrated live 2026-07-28: one annotation per edge with a per-edge dedupe key
    (writer_fact > reader_fact) — the original set-hash key restated the whole growing
    graph every evaluation (quadratic storage, edges double-counted by ranking).
- [x] **3.5 · Run boundary contract** - `ROADMAP.md` Phase 5.4, not a control-plane step of
  its own but a hard prerequisite for everything below it. An in-CLI `/clear` or `/new`
  becomes a new `agent_run_id` (`agent_conversation_rolled` on the event log) instead of a
  silent conversation swap under a live run. Steps 4–8 inherit their boundary from it and
  must not implement conversation-change detection themselves.
- [x] **4 · Project context card** (§5.4).
  Shipped as `ROADMAP.md` Phase 5.5 (first slice).
  Design: `design/features/project-card.md`.
  - [x] One fixed user-owned `.swe-mux/project-context.md` file; blank by default and never inferred from repository docs or source.
  - [x] Bounded UTF-8 reads, fixed contained path, unsafe symlink/type rejection, atomic revision-checked writes, and empty-context degradation.
  - [x] Timeline-drawer editor with configured/empty state, byte count, Save, and **Copy setup prompt** for an agent-assisted user workflow.
  - [x] Enabling Project timeline creates the blank file lazily but does not generate content, authorize a run, or backfill history.
  - [x] HTTP read/write routes and background diagnostics; legacy generated-card storage and code are inert compatibility artifacts.
- [x] **5 · Scan timeline (Tier 1)** (§5.5). First model-cost layer. Capture-first: readable
  timeline + dead-end memory (§6.2). Instrument the rehydration rate from commit one. Records
  carry `agent_run_id`; delta window, continuity context, and `novelty` all reset at a rollover.
  The continuous title is **not** part of this step: §6.11 abandoned it, and titling is one call
  per run off the opening request.
  **Gated 2026-08-10 on step 2.6 evidence.** This is the first continuously costing feature and
  step 2.6's free reads overlap part of what the timeline was meant to provide, so ship the
  reads first and let their observed usage justify or retire this step.
  - [x] The Timeline drawer owns Project permission, Project context, run permission, current scan, full-session scan, spend, records, and source expansion; the topbar owns none of them.
  - [x] Full-session scans process uncovered current-run messages oldest first under ordinary gates and budgets, keep the live cursor monotonic, and expose running/completed/partial/failed progress.
  - [x] Global and built-in automation enablement is centralized in the Automation dashboard; Settings retains configuration only.
- [ ] **6 · Model narration** (§14). Cheap-model "why" on top of the deterministic detectors.
  A narration slice never spans two agent runs.
- [ ] **7 · Attention ranking / inbox** (§6.7). Last — needs every other signal. Fan-out,
  daily interrupt budget, four channels, breakpoint delivery. Findings anchored to a
  superseded run stay inspectable and are excluded from ranking.
- [ ] **8 · Cross-session + novel + mux MCP v1** (§6.6, 6.8, 6.10, 7). Interlocks, digests,
  second opinions, experience DB, and the memory half of the return path
  (`provenance`, `priorResolutions`, `deadEnds`, `verifiedStatus`) layered onto the v0
  transport from step 2.5. Every derived record names its `agent_run_id`. Delivered as
  `ROADMAP.md` Phase 7.5.
  The `memorySources` / `readMemory` reads moved to step 2.6 on 2026-08-10, so the harness-memory
  bridge is no longer hostage to this step's substrate; the rules governing it are unchanged and
  are restated in `ROADMAP.md` Phase 7.5.
  - [ ] Split the shipping order by dependency rather than by tool family: `provenance` and
    `verifiedStatus` read Tier 0 and the step 3 detectors, both shipped, so they are buildable
    ahead of the rest; only `priorResolutions` and `deadEnds` genuinely need the step 5 scan
    timeline. Do not hold the deterministic half behind the semantic half.
- [ ] **9 · Agent session control** (§7.6, §16). The first agent-reachable actuation, scheduled
  last and gated hardest. Delivered as `ROADMAP.md` Phase 7.6.
  - [ ] `interrupt(target)`: stop the current turn, session survives. A PTY write, so it runs
    behind the fail-closed `safe|blocked|unknown` readiness predicate; `unknown` never
    authorizes. The escape sequence lives in the adapter, never in the MCP layer.
  - [ ] `end_session(target)`: destroys the actor, and is allowed against the caller itself.
    Needs a **graceful-end daemon operation that does not exist yet**: interrupt, harness exit
    sequence, bounded wait for the CLI's own teardown, hard stop only on timeout. End reasons
    must distinguish `agent_ended` from operator `killed` and CLI `exited`.
  - [ ] Self-termination returns before teardown and retains the transcript, final turn, and
    history row. An agent may end itself; it may not erase itself.
  - [ ] Per-Project three-position authority grant (`off` / `draft` / `granted`), default
    `draft`, in the existing per-project opt-in surface. `draft` writes an inert
    observation-inbox request exactly like `requestSpawn`.
  - [ ] Resolve agent-held **spawn** authority under the same grant, or leave both drafted.
    One capability with a real grant model and another drafted forever is an inconsistency,
    not a boundary (§16).
  - [ ] Reuse the `notify` bounds wholesale: Project scope, live-agent targets, per-origin
    budget, chain depth, cycle detection, idempotency, typed refusals, master kill switch.
    Never reachable at any grant level: a target outside the caller's Project, and the session
    that owns the running daemon.
  - [ ] Every interrupt and end is logged with caller provenance, visible in the fleet audit
    surface, and eligible for the §6.7 attention channels. No automatic remediation is built
    on top of it (§16: resampling amplifies injected content).

### UI work (design before it ships — the enablement surface is the big risk)

- [x] **Enablement-DAG toggle surface.** Shipped in the per-Project settings editor
  (`ProjectsManager.tsx`) over `GET|PUT /api/projects/{id}/automations`: each row names the
  substrate it needs and how much of it is still off, enabling a consumer enables its whole
  transitive closure, disabling substrate disables its dependents, and a reserved id with no
  implementation renders disabled rather than as ready to switch on (the registry carries an
  `implemented` flag and the route refuses `409 automation_not_implemented`).
- [x] **Per-project scope affordance.** The toggle lives in the one per-Project editor, so
  scope is structural rather than a label; global config remains an inherited default.
- [x] **Persistent scan-timeline spend/budget line.** Timeline tokens/cost today and the
  current run budget are visible in the always-on active-session status line, not only in
  the timeline tab.
- [ ] **Daily interrupt budget extension.** Add the interrupt budget to that status line when
  Phase 6.5 ships the interrupt-ranking policy that owns it.
- [ ] **Progressive disclosure on rule rows.** Show name + state + one-line summary by
  default; expand for `when::trigger · reads::slice · model → result · setting::key`.

### Known gaps and follow-ups

- [x] **Continuous title, closed by abandoning it.** The doc/code divergence is gone because
  the doc changed: one title per `agent_run_id` off the run's opening request is the intended
  behaviour, not a shortfall (§6.11 records why). `features/automation.md` and
  `test_automation_phase6.py` both describe that.
- [x] **Opt-in UI.** Shipped with the deterministic consumers (see the toggle surface above);
  hand-editing `.swe-mux/config.toml` still works and remains the source of truth.
- [x] **Preview-shot retention.** Swept at 7 days by the media-cleanup loop across registered
  Project roots and the data-dir fallback.
- [ ] **Preview capture assumes a locally installed Chromium.** A clean-machine desktop build
  needs Chromium bundled or a first-run `playwright install` (Phase 11 packaging).

The through-line: substrate before consumers, deterministic before model, helps-you-today
pulled forward, ranking genuinely last.

---

## 10. Trigger hierarchy and graceful degradation

Three signal channels with different fidelity/stability tradeoffs:

| Channel | Fidelity | Stability | Notes |
|---|---|---|---|
| **Native hooks** | Exact, semantic, can gate (natively) | Fragile: version-coupled; availability differs per CLI | Highest-fidelity source. Per-CLI wiring in adapters. |
| **Transcript tailing** | Rich, complete payloads | Schemas drift; one-file parser fix | Post-hoc only: can react to a tool call, never gate it. |
| **PTY bytes + liveness** | Crude, near-zero semantics | Eternal: we own the ConPTY | Only source for plain shells and wedged/dead states. |

The state priority (hook > transcript > PTY) is a graceful-degradation ladder the
rule layer inherits:

- A rule declares `on: turn_ended`; the daemon resolves the best available source
  per session/backend. Rules never name sources.
- Every normalized event carries `source` and `confidence` so rules *may* condition
  on fidelity without depending on it.
- When a native hook is unavailable, the trigger degrades to transcript inference;
  when the parser degrades, features thin rather than break.

Backend asymmetry is expected and acceptable: Claude Code exposes a rich hook set
(the non-blocking subset — `SessionStart/End`, `PermissionDenied`, `FileChanged`,
`PostCompact`, `Notification` with `agent_needs_input`/`idle_prompt`/`agent_
completed`, etc. — is the principled subscription boundary for an out-of-band
layer). Codex has no equivalent. Do not degrade Claude to the common denominator;
consume the richer signal where it exists and fall back where it does not.

### Degradation detection

- **Per-adapter capability flags**: each adapter version declares which events it
  can source at which fidelity. Observers declare required fidelity and disable
  themselves cleanly rather than firing on garbage.
- **Versioned transcript fixtures + contract tests** (already in place) catch drift
  at test time.
- **Runtime probes**: hook silence while transcript shows activity implies broken
  wiring → emit `capability_degraded`, surface in diagnostics, become a matchable
  trigger.
- Transcript silence is ambiguous (idle vs hung vs crashed); PTY signals
  disambiguate it. Composite triggers (§11.4) formalize this.

---

## 11. Trigger inventory

### 11.1 Transcript-derived (rich, semantic, post-hoc)

Turn structure: `turn_started`/`turn_ended`; `user_prompt` with content match;
`assistant_text` with content match (completion claims, questions, refusals, retry
apologies); `stop_reason`; derived timing (duration, count, velocity).

Tool activity (observed, never blocking): `tool_use` by name + payload (which file,
which command, which URL, subagent spawned); `tool_result` success/error + exit
codes; derived churn (same file edited N times, same command failed M times — the
loop detector's raw material); todo/plan payload changes.

Resource/model: per-message token usage → `context_pct` crossings, cost, velocity;
model-id change; compaction records.

Session semantics: sidechain/subagent activity (Claude); patch-applied/exec records
(Codex); API errors/retries; start metadata.

Limits: strictly after-the-fact; latency is the flush interval; schema drift is the
fragility (adapter parser is the blast radius); silence is ambiguous.

### 11.2 PTY-derived (crude, semantic-free, eternal)

Process: `session_exited`/`session_crashed`; spawn success/failure; job-object
descendant start/exit; polled CPU/memory of the tree (hung vs busy-but-silent,
runaways).

Output: activity/silence (bytes vs quiet for N seconds — combined with transcript
state, disambiguates idle from wedged); output rate/volume (storms); ANSI-stripped
content match (error/permission strings — fragile, last-resort only).

Control sequences (underrated): BEL (0x07) attention/completion bell; OSC 0/2 title
changes; OSC 133 prompt markers → `command_finished {exit_code}` for plain shells
*and for the human's own shell* (the breakpoint signal in §6.7); alternate-screen
enter/exit (promotion generalized); bracketed-paste/cursor-mode toggles (composer
vs menu — inputs to the safe-to-inject predicate).

### 11.3 Mux-side (free because we own the plumbing)

Attach/detach, input-owner changes, keystrokes → attended vs unattended (modulates
notifications; also the interaction-time half of fan-out); `git_changed`;
session lifecycle; timer/interval and threshold triggers; `annotation_created`
(observer output re-entering the trigger space); `capability_degraded`;
`no_progress` (Tier 0 progress gate went N intervals without advancing).

### 11.4 Composite triggers (the valuable ones)

Only expressible across sources — exactly what no native hook can offer:

- `stalled`: transcript working + PTY silent + CPU flat
- `unattended_attention`: awaiting approval + no browser attached
- `promotion`: alternate screen + native transcript appeared
- `runaway`: output storm + no turn progress
- `claim_unverified`: completion claim + no passing test tool_result in the turn
- `premature_stop`: turn ended + completion claim + open todos + no verification
- `test_gamed`: a test file modified in the same turn that made the test pass
- `looping`: fingerprint recurrence ≥3 + no-progress gate (§6.4)

Design the trigger schema around composites from the start.

---

## 12. Rule anatomy: trigger → conditions → actions

Steal Home Assistant's proven triad:

```toml
[[rule]]
name = "stall-alert"
on = { trigger = "stalled", debounce_s = 120 }
when = [
  { field = "backend", in = ["claude", "codex"] },
  { field = "session_name", glob = "builder-*" },
  { state = "unattended" },
]
do = [
  { kind = "annotate", tag = "stall", content = "no progress detected" },
  { kind = "notify", channel = "phone", message = "{session_name} looks stalled" },
]
```

### 12.1 Trigger options

Uniformly: **debounce/coalesce** ("wait 30s, fire once" — without it observers are
economically unviable); **interval** (every N minutes, for digests); **threshold**
with hysteresis (`context_pct > 80` without re-firing every tick); **content
match** (regex/glob where the trigger carries text).

### 12.2 Conditions inventory

Field matches (backend, session glob, project, state, source, confidence); state
guards (attended/unattended, context_pct range, age, pinned, broadcast); time
windows (quiet hours, weekdays); rate guards ("at most once per session per hour");
annotation guards ("only if no `stall` annotation in 10m").

### 12.3 Actions inventory and the action ladder

Ordered list; the UI pushes declarative first, scripts last:

1. **Declarative primitives** — validated, budgetable, dry-runnable:
   `notify {channel, message_template}` · `annotate {tag, content_template}` ·
   `http {url, body_template, timeout, retry}` · `spawn {backend, cwd,
   prompt_template}` (gated like write_pty) · `write_pty {text_template}` (gated,
   §16) · **`queue_draft {session, body_template, provenance}`** — write an inert
   draft into the Phase 4 manual queue (§13).
2. **`llm` — the soft script** (§14): a prompt + output schema + follow-up action
   covers most of what users would otherwise script.
3. **`run` — the hard escape hatch**: arbitrary script for the residual.

Template variables (`{session_name}`, `{payload.tool_name}`, `{annotation.content}`)
are the connective tissue, with editor autocomplete from the trigger's event schema.

### 12.4 Script contract (`run`)

Mirror native hooks: event payload as JSON on stdin, template vars in argv/env;
daemon-enforced timeout and lifecycle; **stdout JSON may emit follow-up actions or
annotations** (a script is not a dead end; its output re-enters the rule system);
scripts live in a scripts dir (project-scoped, §8); the UI lists them and shows
last-run/exit/stderr.

---

## 13. The queue-draft channel: actuation-free output

Observers cannot type (design law 2). But the Phase 4 manual prompt queue
introduces a durable `draft` state that is inert by design and requires an explicit
human arm + send. So **let observers write drafts into the queue** instead of
nudging:

- The stall detector drafts *"you've retried this approach 4×; the failing
  assertion is X, consider Y"* into the target session's queue.
- The diff/scope check drafts *"revert the changes to `layouts.py`, out of scope."*
- The context advisor drafts the handoff document itself, not a suggestion to write
  one.

Zero new trust boundary: the draft is text sitting in a queue; head-of-line
ordering, revision tracking, and the audit trail already apply, and the human
reviews and sends. This is the entire value of "the ambient layer can act" with
none of the actuation risk. It needs the Phase 4 message model to carry a
`sender_kind` for observer provenance and a **structured action payload** (not just
a body string) that is re-validated at send time — the correct drafting-for-approval
shape (persist a typed payload, re-check at commit), not "show text and re-ask the
model to do it." Keep friction proportional to consequence and never pre-focus
"send," or approval degrades into rubber-stamping (2026 oversight studies: a warned
monitor still had 56% accept malicious code). This is also channel 3 of the return
path (§7).

---

## 14. The `llm` action kind and observers

The minimal kernel:

```
event pattern → normalized slice → cheap model call → structured output → follow-up action
```

```toml
do = [{ kind = "llm",
        model = "cheap",                          # named tier, resolved in config
        input = { slice = "last_turn" },          # TranscriptSlice / scan-record spec
        prompt = "...template...",
        schema = "title_v1",                       # JSON schema for output
        on_result = { kind = "annotate", tag = "title", content = "{result.title}" } }]
```

Principles:

- The engine stays dumb and deterministic; intelligence is entirely inside the
  model call. "Hooks that can think."
- **Deterministic detector, model describer.** Never spend a model call where a
  Tier 0 counter can fire. Use the model only to *narrate* an already-suspicious
  event, so observers fire per-anomaly, not per-turn — two orders of magnitude
  cheaper and higher precision (the model is never asked the open-ended "is
  anything wrong?", which it over-answers yes).
- Observers are stateless between invocations; their memory is the annotations /
  scan-record tables. This is the guard rail against re-growing orchestration.
- Later observers read cheap prior records (scan timeline, turn summaries) instead
  of re-reading transcripts — the summarizer is substrate, not a feature. But note
  the turn summarizer is substrate *only*; nobody reads a per-turn summary feed, so
  do not render one. (The continuous title, §6.11, is the visible consumer of that
  substrate.)

### TranscriptSlice service

Adapters expose normalized slices (role, text, tool name, timestamps): `last_turn`,
`last_n_messages`, `since_event`, `since_annotation`, `full_session_summary_chain`.
Observers written against these survive parser drift — slices thin, features
degrade, nothing breaks. No observer sees a native schema.

### Cascade economics reminder

A cheap-model preprocessor feeding a strong consumer is not a cascade (no deferral
decision), which sidesteps the classic cascade structural cost but forfeits its
safety signal: nothing says "this window needs raw." Restore it with the per-record
`confidence` + `coverage` fields and the rehydration path (§5.5). At current
pricing one strong pass repays a session's cheap ingestion; the variable that
decides whether the whole thing pays is the rehydration rate, so meter it.

---

## 15. Annotations, safety, economics, and trust machinery

### Annotations table

The composition backbone:

```
annotations(id, session_id, kind/tag, content, provenance, model, cost, refs[], ts)
```

Writing an annotation emits `annotation_created`, itself matchable — titler writes
one, the UI renders it, a digest aggregates it, nothing hardcoded. Titles,
summaries, verdicts, scan records, extracted facts are all annotations over
history: queryable, displayable, exportable. Provenance, cost, and source `refs`
are first-class columns.

### Machinery

- **Budgets**: per-rule and global token/dollar caps enforced by the daemon; once
  hit, `llm` actions fail visibly; spend per rule visible in Settings from day one.
  "Many cheap models buzzing" fails not by being wrong but by silently costing
  $40/day doing nothing useful.
- **Attention budget** (the missing primitive alongside tokens/dollars): a hard cap
  on interrupts per *day*, with the ranking layer deciding which cross the bar.
  Volume, not correctness, is what kills the feature in daily use.
- **Chain-depth cap**: rule → action → event → rule chains have a depth limit; loop
  detection flags a chain revisiting the same rule.
- **Kill switch**: one toggle disables the entire automation layer.
- **Shadow mode**: new rules run logging-only ("would have fired 12× today") before
  going live — how users tune regexes and debounce safely. This is also the Phase 1
  shadow-readiness posture generalized.
- **Test against history**: dry-run a rule against any persisted event, see rendered
  actions with no side effects. Nearly free (events already persisted); nobody
  trusts an automation they can't test.
- **Firing log**: per-rule trigger → conditions → actions → cost history.
- **Read-only first**: the observer layer ships annotate-and-notify (and
  queue-draft) only — ~80% of the value at ~5% of the risk. Actuation comes later,
  gated (§16).
- **Rulepacks**: a TOML file + optional scripts + declared parameters, imported
  through the same validation path. Distribution without a marketplace;
  agent-generatable and shareable.

---

## 16. Actuation: the deliberate gate

Observation is safe and scaffold-agnostic. Actuation is the fragile half:
`write_pty` types into a screen whose state is inferred. Inject mid-turn, into a
menu, or into a permission prompt, and the session is corrupted.

Posture:

- The observer layer is **read-only first**. `write_pty` (and `spawn` with a
  prompt) stay out of observer reach until a **safe-to-inject predicate** exists:
  idle state, empty composer, per-adapter etiquette; bracketed-paste/cursor-mode/
  alt-screen signals (§11.2) are inputs. This is the same fail-closed
  `safe|blocked|unknown` contract as delivery-readiness; `unknown` never authorizes
  a write.
- Nudging a stalled agent, auto-answering approvals, and cross-session relay are all
  real future features — all through the same deliberate gate, as product
  decisions, not rules-file creep.
- **Never add "re-run the turn" / resample as an automatic remediation.** Adaptive
  attacks (2026) show resampling amplifies injected content; semantic rewind stays
  human-directed with a corrected instruction, never an automatic loop.
- Auto-approval additionally requires an explicit allowlist and belongs with the
  Phase 5 trust-boundary work.
- **Agent-requested spawn is drafted, never granted**, until the grant model exists. The
  return path exposes `mux.requestSpawn` (§7.2), which writes an inert draft into the
  observation inbox and starts nothing. It is the §13 pattern applied to spawn: the capability
  an agent gets is "ask the human," not "create an actor." An agent holding real spawn
  authority turns a single prompt injection into unbounded fan-out, which is why this
  stays gated even though the messaging half (`mux.notify`) ships in Phase 5. **Step 9 (§7.6)
  is where that gate acquires a lever rather than only a lock:** a per-Project three-position
  authority grant (`off`/`draft`/`granted`), default drafted, budgeted and audited when
  granted. Spawn is decided under that grant or stays drafted with it.
- **Agent session control is scheduled, not permanently reserved.** Interrupting a sibling's
  turn and ending a session (including the caller's own) are real capabilities the product
  wants, and they land in step 9 behind the same readiness predicate as any other PTY write,
  the same grant, and the same bounds `notify` proved. Design detail: §7.6. What does not
  move: an interrupt on `unknown` readiness, a target outside the caller's Project, the
  daemon-owning session, an end that discards the record of why, and any automatic
  remediation layered on top.
- The line that keeps this from re-growing orchestration: observers are advisory and
  stateless; anything that *commands* agents is relay, and relay is reserved. The
  drafting-for-approval channel (§13) is the safe pressure-release: it delivers
  observer intent to the human, not to the PTY.

---

## 17. Universal-X: the same abstraction applied elsewhere

Universal hooks abstracts the CLIs' *signals*. The same move applies elsewhere:

- **Universal commands** (input side): palette entries injecting canned prompts into
  whatever agent is focused — `/handoff`, `/status-report`, `/review-your-last-turn`
  identical across backends, living in mux config. A prompt library with injection,
  and channel 3 of the return path (§7). Skill-shaped things fold into this +
  rulepacks; skills proper run inside an agent and need no third concept.
- **Agent Context + instruction/memory continuity**: first inspect Project-root
  `CLAUDE.md` / `AGENTS.md` and provider-owned learned memory read-only; allow a human to
  manually overwrite either root instruction file from the other with preview, conflict
  detection, and recovery; then render one canonical instruction set into owned per-backend
  sections and expose attributed provider memory to sibling agents as pull-only mux MCP
  reads. No automatic file sync or provider-store replication. Also return-path channel 2
  and the exact-source part of channel 1 (§7). The inspector and manual-overwrite first wave
  shipped 2026-08-02; canonical rendering and MCP reads remain later work.
- **Universal history/search**: one search across both vendors' transcripts (mostly
  built; no vendor will build the other half).
- **Universal budgets**: spend/quota across providers in one place.
- **Universal attention**: approval routing and notification policy in one place.

---

## 18. Open questions

- Trigger schema for composites: declared (named, daemon-computed) or user-composed
  from primitives? Leaning declared, with primitive fields exposed for conditions.
- Where the safe-to-inject predicate lives: per-adapter method or daemon-level
  heuristic over PTY mode signals? Probably both.
- Scan-timeline cadence in practice: which event boundaries earn a record, and what
  Δmax heartbeat balances cost against temporal aliasing. Prototype and measure —
  fixed-rate sampling is genuinely unvalidated (§2).
- Scan-record salient-message weighting: how strongly to privilege `user_ask` and
  agent `claim` over tool activity, and whether that weighting is a prompt concern
  or a pre-filter on the delta.
- The rehydration-rate target: instrument it first, then decide the confidence/
  coverage thresholds that trigger a Tier 2 source expansion.
- Return-path retrieval precision: the scope/confidence thresholds below which MCP
  tools return nothing rather than a weak match, per tool.
- MCP transport on the Codex side: whether the targeted Codex version accepts a
  streamable-HTTP `mcp_servers` entry, or whether a stdio proxy shim is required
  (§7.3). Verify against the shipped CLI before the v0 phase starts, not during it.
- MCP v0 read scope: whether cross-Project reads are ever granted to an agent token,
  and if so what the grant surface looks like — the default is own-Project only
  (§7.4), but "what else am I working on right now" is inherently cross-Project.
- Whether `mux.notify` targets need a per-Project allowlist UI or whether "any live
  session in the same Project, plus explicit user-added targets" is sufficient
  bounding for Phase 5.
- Session-control grant defaults (§7.6): whether `granted` is ever a sensible per-Project
  default for `interrupt` on a session the caller itself spawned, or whether the parent/child
  relationship deserves its own position between `draft` and `granted`.
- Whether an agent-ended session should be retained differently from an operator-killed one in
  the UI. The record must survive either way, but a worker that ended itself on completion is
  ordinary and a worker another agent ended is worth a human's attention.
- Enablement-DAG UX: how to present a consumer's substrate dependencies when
  toggling, and how disabling a substrate node communicates to its dependents.
- Per-project baselines: how much history before an ETA / neglect-time estimate is
  credible, and how to present it as an uncertainty band, never a percent-complete.
- Cheap Tier 1 model selection: build the labeled extraction benchmark from real
  Claude/Codex streams before choosing; A/B semantic fields across two families.
- Annotation/scan-record retention and GC, and whether records on external
  (reconciled) sessions are allowed.
- Model routing for observers: fixed per-rule ids vs named tiers ("cheap"/
  "standard") in global config. Leaning tiers.
- Rulepack parameter declaration/validation; whether project-scope rulepacks may
  bundle scripts at all given the trust boundary.
- Whether `spawn` ships gated with actuation or earlier for user-initiated flows
  only (second opinions are user-initiated → palette/universal-command first, rule
  action later).
- Interrupt-preference learning: which observed signals (dismiss/act/edit/rollback)
  train the ranking model, and how to surface the induced rule for accept/reject
  without a black box.
```
