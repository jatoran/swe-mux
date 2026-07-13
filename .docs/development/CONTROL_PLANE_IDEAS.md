# Universal hooks and the agent control plane — ideas document

Status: brainstorm surface and design reference, not a roadmap. Nothing here is
scheduled; `ROADMAP.md` remains the delivery plan. This document records the
conceptual framing, naming decisions, trigger/action inventories, and feature
ideas for the automation layer that sits above the CLI agents swe-mux hosts.

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
conversation).

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

### Design laws

These are the non-negotiables that keep the layer scaffold-agnostic and safe:

1. **Out-of-band only.** Anything that must block or gate an agent's turn
   belongs to the native scaffold's own hooks. The mux layer is advisory and
   asynchronous. It never needs the CLI's cooperation precisely because it is
   never in the critical path. The moment it tries to be in-band it re-couples
   to a vendor hook API and loses its agnosticism.
2. **Above and around, never inside.** The layer observes, annotates, and
   notifies. It does not steer sessions. Actuation (`write_pty`) exists but is
   deliberately gated (see §9).
3. **Consume normalized events and normalized transcript slices only.** No
   rule, observer, or feature outside an adapter may reference a native schema,
   path, or flag. Scaffold drift is absorbed in one adapter file.
4. **Advisory, not orchestration.** The spec killed roles, leads, and DONE
   protocols deliberately. Observers notice; they do not command. Anything
   that directs agent behavior goes through the reserved relay path (spec §7)
   as an explicit future product decision, never as feature creep through the
   rules file.
5. **Eventually consistent by design.** Nothing in this layer participates in
   a turn. That constraint is why the layer survives scaffold updates.

---

## 2. Naming conventions

Decided vocabulary. "Meta-hooks" is retired: "meta" says nothing concrete and
"hooks" collides with the CLIs' native hooks; the term also names the layer
after its most fragile input (it hooks *events*, not hooks — see §3).

| Term | Meaning |
|---|---|
| **Universal hooks** | User-facing name for the whole layer: hooks defined once, above the CLI, that fire for any backend and survive CLI updates. The pitch word. |
| **Rules** | The mechanical tier: trigger → conditions → actions, deterministic, no model call. `hooks.toml` becomes `rules.toml`. |
| **Observers** | The LLM tier: stateless model calls via the `llm` action — read a transcript slice, emit an annotation or notification. No session, no tools, read-only. The titler, summarizer, stall detector are observers. Cheap, safe, plentiful. |
| **Workers** | Full agent sessions a rule spawns via the `spawn` action + a session template to *do* something (update docs, consolidate memory). Data-plane work, control-plane initiated; once spawned it is an ordinary session with history and a lineage edge back to its cause. Mutates the world, so it sits behind the actuation gate (§9). A "doc agent" is really an observer→worker pipeline: observe cheap, act expensive, human in between until trust is earned. |
| **Automation layer** | Umbrella term for rules + observers when one word is needed. |
| **Native hooks** | Reserved exclusively for the CLIs' own hook systems (Claude Code hooks, Codex `notify`). They are an event *source*, nothing more. |
| **Annotations** | Persisted observer/rule output attached to sessions (titles, summaries, verdicts). |
| **Universal commands** | Input-side sibling of universal hooks: mux-level canned prompts/commands injectable into any backend (see §11). |
| **Rulepacks** | Shareable, parameterized bundles of rules + observers + scripts (see §8). |

Clean sentence test: *native hooks feed events, universal hooks fire, rules
match, observers think, workers act.*

---

## 3. Trigger hierarchy and graceful degradation

Three signal channels with different fidelity/stability tradeoffs:

| Channel | Fidelity | Stability | Notes |
|---|---|---|---|
| **Native hooks** | Exact, semantic, can gate (natively) | Fragile: version-coupled config injection; availability differs per CLI (a hook on Claude may not exist on Codex) | Highest-fidelity event source. Per-CLI wiring lives in adapters. |
| **Transcript tailing** | Rich, complete payloads | Schemas drift; one-file parser fix | Post-hoc only: can react to a tool call, never gate it. |
| **PTY bytes + liveness** | Crude, near-zero semantics | Eternal: no scaffold update can remove it; we own the ConPTY | The only source for plain shells and for wedged/dead states. |

The existing state priority (hook > transcript > PTY) is already a graceful
degradation ladder. The rule layer inherits it:

- A rule declares `on: turn_ended`; the daemon resolves the best available
  source per session/backend. Rules never name sources.
- Every normalized event carries `source` and `confidence` fields so rules
  *may* condition on fidelity without depending on it.
- When a native hook is unavailable (CLI update broke wiring, or the backend
  never had that hook), the trigger silently degrades to transcript inference;
  when the parser degrades, features thin out rather than break.

### Degradation detection

Agnosticism requires knowing when fidelity has dropped:

- **Per-adapter capability flags**: each adapter version declares which events
  it can source at which fidelity (roadmap Phase 4 "parser capability/
  diagnostic status"). Observers declare required fidelity and disable
  themselves cleanly rather than firing on garbage.
- **Versioned transcript fixtures + contract tests** (already in place) catch
  drift at test time.
- **Runtime probes**: hook silence while transcript shows activity implies
  broken hook wiring → emit a `capability_degraded` event, surface in
  Settings diagnostics, and (itself) become a matchable trigger.
- Transcript silence is ambiguous (idle vs hung vs crashed); PTY signals exist
  to disambiguate it. Composite triggers (§4.4) formalize this.

---

## 4. Trigger inventory

### 4.1 Transcript-derived (rich, semantic, post-hoc)

Turn structure:
- `turn_started` / `turn_ended`
- `user_prompt` with content match (regex over the user's text)
- `assistant_text` with content match (claims like "tests pass", questions to
  the user, refusals, retry apologies)
- `stop_reason` (clean end vs truncation)
- Derived timing: turn duration, turn count, turns-per-hour velocity

Tool activity (observed, never blocking):
- `tool_use` by name + input payload: file edited (which path), command run
  (which text), web fetch (which URL), subagent spawned
- `tool_result`: success vs error content, nonzero exit codes
- Derived churn: same file edited N times in a window, same command failed M
  times — the stall detector's raw material
- Todo/plan payload changes

Resource and model telemetry:
- Per-message token usage → `context_pct` threshold crossings, cost
  accumulation, tokens-per-turn velocity
- Model id change, CLI version from session metadata
- Compaction/summary records (context was squashed)

Session semantics:
- Sidechain/subagent activity (Claude); patch-applied and exec records (Codex)
- API errors, retry records
- Session start metadata (cwd, version)

Limits: strictly after-the-fact; latency is the file-flush interval; schema
drift is the fragility (adapter parser is the blast radius); silence is
ambiguous.

### 4.2 PTY-derived (crude, semantic-free, eternal)

Process level:
- `session_exited` / `session_crashed` (EOF + exit code; explicit stop vs
  unexpected death)
- Spawn success/failure
- Via the job object: descendant process start/exit (agent launched a dev
  server; a test runner is still alive)
- Polled CPU/memory of the process tree: distinguishes hung from
  busy-but-silent; catches runaways

Output stream:
- Activity/silence: bytes flowing vs quiet for N seconds. Combined with
  transcript state this disambiguates idle from wedged — probably the single
  most useful PTY trigger.
- Output rate/volume: bursts, output storms (a loop spraying the terminal)
- Content match on ANSI-stripped bytes: error strings, permission-prompt text,
  spinner glyphs. Universal but fragile to theme/version/locale; last-resort
  fallback only, never a primary source.

Terminal control sequences (underrated):
- **BEL (0x07)**: CLIs ring the bell on attention/completion; a clean,
  semantic-ish signal that costs nothing to parse
- **OSC 0/2 title changes**: CLIs set window titles reflecting state
- **OSC 133 prompt markers**: with shell integration, plain shells emit
  command start/end/exit-code markers → `command_finished {exit_code}`
  triggers for non-agent sessions, which have no transcript at all
- Alternate-screen enter/exit: TUI came up or dropped to shell — the in-place
  promotion detector generalized
- Bracketed-paste / cursor-mode toggles: composer open vs menu state; inputs
  to a future safe-to-inject predicate (§9)

### 4.3 Mux-side (free because we own the plumbing)

- Attach/detach, input-owner changes, keystrokes flowing → "human is
  watching/typing" vs unattended; should modulate notification rules (don't
  push to the phone what the user is looking at)
- `git_changed` (branch, dirty, ahead/behind per cwd)
- Space/session lifecycle: spawned, renamed, moved, pinned
- Timer/interval and threshold triggers (see §5.1)
- `annotation_created` (observer output re-entering the trigger space, §7)
- `capability_degraded` (§3)

### 4.4 Composite triggers (the valuable ones)

Some triggers are only expressible across sources, and these are exactly the
ones no native hook system can offer — the core pitch of universal hooks:

- `stalled`: transcript says working + PTY silent + CPU flat
- `unattended_attention`: awaiting approval + no browser attached
- `promotion`: alternate screen entered + native transcript file appeared
- `runaway`: output storm + no turn progress
- `claim_unverified`: assistant text claims completion + no test tool_result
  in the same turn

Design the trigger schema around composites from the start.

---

## 5. Rule anatomy: trigger → conditions → actions

Steal Home Assistant's proven triad. A rule is:

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

### 5.1 Trigger options

Every trigger supports, uniformly:
- **Debounce/coalesce**: "on turn_ended, wait 30s, fire once." Without this,
  observers are economically unviable.
- **Interval**: fire every N minutes (for polling-style observers and digests).
- **Threshold**: fire when a numeric field crosses a value (`context_pct > 80`),
  with hysteresis so it doesn't re-fire every tick.
- **Content match**: regex/glob params where the trigger carries text.

### 5.2 Conditions inventory

Guards that gate a fired trigger (cheap field/op/value rows in the UI):
- Field matches: backend, session name glob, space, cwd/project, state,
  source, confidence
- State guards: attended/unattended, context_pct range, session age,
  pinned, broadcast membership
- Time windows: quiet hours, weekdays
- Rate guards: "at most once per session per hour" (beyond per-rule
  rate limits)
- Annotation guards: "only if no `stall` annotation in the last 10 minutes"

### 5.3 Actions inventory and the action ladder

Actions are an ordered list. The design ladder, in the order the UI pushes
users (declarative first, scripts last):

1. **Declarative primitives** — validated, budgetable, dry-runnable, safe:
   - `notify {channel, message_template}` (ui, phone/ntfy, telegram-later)
   - `annotate {tag, content_template}` (writes the annotations table, §7)
   - `http {url, body_template, timeout, retry}`
   - `spawn {backend/profile, cwd, prompt_template}` (e.g. second-opinion
     sessions, §12) — powerful; same gating posture as write_pty
   - `write_pty {text_template}` — exists, deliberately gated (§9)
2. **`llm` — the soft script** (§6). Most things users would script
   (classify, extract, decide-whether-to-escalate) are a prompt + output
   schema + follow-up action. No file, no language, no platform issues.
3. **`run` — the hard escape hatch.** Arbitrary script for the residual 10%.

Template variables (`{session_name}`, `{payload.tool_name}`,
`{annotation.content}`) are the connective tissue, with editor autocomplete
driven by the chosen trigger's event schema.

### 5.4 Script contract (`run`)

Mirror native hooks so the mental model transfers, plus composability:
- Event payload as JSON on stdin; template vars in argv/env
- Daemon-enforced timeout, subprocess lifecycle policy
- **stdout JSON may emit follow-up actions or annotations**; exit codes have
  defined meanings — a script is not a dead end; its output re-enters the
  rule system like any other event
- Scripts live in a scripts dir (global or project, §10); the UI lists them,
  reveals the folder, shows last-run/exit/stderr per script

---

## 6. The `llm` action kind and observers

The minimal kernel from which every observer builds:

```
event pattern → transcript slice → cheap model call → structured output → follow-up action
```

Action shape:

```toml
do = [{ kind = "llm",
        model = "cheap-model-id",
        input = { slice = "last_turn" },          # TranscriptSlice spec
        prompt = "...template...",
        schema = "title_v1",                       # JSON schema for output
        on_result = { kind = "annotate", tag = "title", content = "{result.title}" } }]
```

Principles:
- The engine stays dumb and deterministic; intelligence is entirely inside the
  model call. "Hooks that can think."
- Observers are stateless between invocations; their memory is the annotations
  table. This is the guard rail against re-growing orchestration.
- Later observers read cheap prior annotations (turn summaries) instead of
  re-reading transcripts — the summarizer is substrate, not a feature.

### TranscriptSlice service

Adapters expose normalized slices in a minimal common shape (role, text, tool
name, timestamps): `last_turn`, `last_n_messages`, `since_event`,
`since_annotation`, `full_session_summary_chain`. Observers written against
this survive parser drift: when a parser degrades, slices get thinner and
features degrade instead of breaking. No observer ever sees a native schema.

---

## 7. Annotations table

The composition backbone. One table:

```
annotations(id, session_id, kind/tag, content, provenance, model, cost, ts)
```

- Writing an annotation emits `annotation_created`, which is itself matchable
  — titler writes an annotation, the UI renders it, a digest rule aggregates
  it, nothing is hardcoded.
- Titles, summaries, verdicts, extracted facts are all annotations over
  history: queryable via API, displayable in the history browser, exportable.
- Provenance and cost are first-class columns: every derived artifact knows
  which rule/model produced it and what it cost.

---

## 8. Safety, economics, and trust machinery

- **Budgets**: per-rule and global token/dollar caps enforced by the daemon.
  Once a cap is hit, further `llm` actions fail visibly. Spend per rule is
  visible in Settings from day one. "Many cheap models buzzing" fails not by
  being wrong but by silently costing $40/day doing nothing useful.
- **Chain-depth cap**: rule → action → event → rule chains have a depth limit;
  loop detection flags a chain that revisits the same rule.
- **Kill switch**: one toggle disables the entire automation layer.
- **Shadow mode**: new rules can run logging-only ("would have fired 12 times
  today; here's what it would have done") before going live — how users safely
  tune regexes and debounce values.
- **Test against history**: pick any recent event from the persisted event
  log, dry-run a rule against it, see rendered actions without side effects.
  Nearly free because events are already persisted; nobody trusts an
  automation they can't test.
- **Firing log**: per-rule history of trigger event → condition results →
  actions taken → cost.
- **Read-only first**: the observer layer ships annotate-and-notify only.
  That is ~80% of the value at ~5% of the risk. Actuation comes later, gated
  (§9).
- **Rulepacks**: a rulepack is a TOML file + optional scripts + declared
  parameters ("which sessions", "which channel"), imported through the same
  validation path. Distribution story without a marketplace; also
  agent-generatable and shareable.

---

## 9. Actuation: the deliberate gate

Observation is safe and scaffold-agnostic. Actuation is the fragile half:
`write_pty` is typing into a screen whose state is inferred. Inject mid-turn,
into a menu, or into a permission prompt, and the session is corrupted.

Posture:
- The observer layer is **read-only first**. `write_pty` (and `spawn` with a
  prompt) stay out of observer reach until a **safe-to-inject predicate**
  exists: state is idle, composer empty, per-adapter injection etiquette;
  bracketed-paste/cursor-mode/alt-screen signals (§4.2) are inputs to it.
- Nudging a stalled agent, auto-answering approvals, and cross-session relay
  are all real future features — and all go through the same deliberate gate
  as the spec's reserved relay (§7 of the spec), as product decisions, not
  rules-file creep.
- Auto-approval (approval triage acting on its verdict) additionally requires
  an explicit allowlist policy and belongs with the Phase 5 trust-boundary
  work.
- The line that keeps this from re-growing orchestration: observers are
  advisory and stateless; anything that *commands* agents is relay, and relay
  is reserved.

---

## 10. Scoping: global and per-project

All universal hooks machinery is definable at two scopes, mirroring the
existing config precedence:

- **Global**: `~/.mux/rules.toml` (+ scripts dir) — machine-wide rules and
  observers.
- **Project**: `.swe-mux/rules.toml` (+ `.swe-mux/scripts/`) at the resolved
  project root — rules that travel with the repo (e.g. "run this verifier
  after turns in this project", project-specific titling conventions,
  doc-drift watchers pointed at this repo's docs).

Precedence follows the established ladder: session/request override → space →
project → global. Project-scoped rules are subject to the Phase 5 project-
config trust boundary: untrusted repos cannot silently run scripts, make
model calls on the user's account, or reach actuation — executable behavior
requires an explicit trust decision, same as project config generally.

Files are the source of truth; the Settings UI is a two-way editor over them,
not a separate store. This keeps rules versionable/diffable, preserves the
atomic-write/last-known-good machinery, and — most distinctively — makes
**Claude itself the best rule author**: an agent that knows the rule schema
turns "watch my builder sessions and ping my phone when one stalls" into a
valid rules file. Design the schema for agent authorship (documented, strict
validation, good error messages); the UI becomes the review-and-toggle
surface.

---

## 11. Universal-X: the same abstraction applied elsewhere

Universal hooks abstracts the CLIs' *signals*. The same move applies to other
per-CLI surfaces:

- **Universal commands** (input side): palette entries that inject canned
  prompts into whatever agent is focused, regardless of backend — `/handoff`,
  `/status-report`, `/review-your-last-turn` working identically in Claude and
  Codex, living in mux config, surviving CLI churn. A prompt library with
  injection. Anything "skill-shaped" folds into this plus rulepacks; skills
  proper run *inside* an agent's context and need no mux-side third concept.
- **Instruction/memory sync**: one canonical instruction set rendered
  per-backend (CLAUDE.md / AGENTS.md / skills dirs), keeping them from
  drifting. The anti-corruption-layer move applied to config.
- **Universal history/search**: one search across Claude and Codex transcripts
  (mostly built; no vendor will build the other half).
- **Universal budgets**: spend/quota across providers in one place (ccusage
  phase).
- **Universal attention**: approval routing and notification policy in one
  place instead of per-CLI config (already the design).

---

## 12. Novel capabilities only the control plane enables

The CLIs are first-person and present-tense; the control plane is third-person
and has memory. These features require seeing *all agents, all time, one
machine* at once — ground no scaffold update can contest, and no single vendor
can ship:

- **Cross-vendor second opinions**: one keystroke — "have Codex review what
  Claude just did" — spawn the other backend in the same cwd with a generated
  prompt referencing the diff. Adversarial review across labs with genuinely
  independent failure modes. Structurally exclusive to a neutral control
  plane; cheap to build (spawn + prompt template).
- **The experience database**: every error, fix, and dead end across all
  sessions, both backends, all time, mined from indexed transcripts into an
  error→resolution cache. An observer annotates a live session: "session X
  hit this exact error in March; the fix was Y." CLI memory is per-project
  and per-vendor; this is per-machine and cumulative. Highest-ceiling novel
  feature in the project.
- **Scaffold telemetry / A/B**: both harnesses observed doing real work under
  identical conditions — which backend stalls more on this repo, cost per
  completed task, approval-interrupt rates. Same-task-both-backends in twin
  worktrees falls out of existing machinery. The mux as an eval harness *of
  scaffolds*, on the user's actual workload.
- **Environment interlocks**: the mux knows every session's cwd, git state,
  and (roadmap) child processes/ports. Warn when two sessions touch the same
  branch or files, detect port collisions, flag that session A's dev server is
  what session B's tests hit. Agents trip over each other's environmental side
  effects and are individually blind to it; only the layer seeing all of them
  can referee.
- **Session lineage**: resumes, handoffs, and continuations tracked as a
  graph, giving *work items* continuity across sessions and backends (started
  in Claude Tuesday, resumed twice, verified by a Codex review session). The
  CLIs have sessions; the mux can have threads of work — a new noun the
  history UI will eventually want.
- **The absence report**: event log + annotations makes "what happened while
  I was away" a query — everything the fleet did, decided, and got stuck on
  since last attach, in one narrated digest. Composes entirely from planned
  observers; no CLI knows when the user was watching.

---

## 13. Observer ideas catalog

Ordered by a sensible build sequence: 1–2 build infrastructure, 3–5 route
attention, 6–8 build trust and compound knowledge.

1. **Session titler.** Cheap call over a transcript slice → concise apt title
   annotation. Trivial, immediately visible (sidebar, history), and forces the
   whole llm-action pipeline end to end. The right first observer.
2. **Turn summarizer.** One line per turn into annotations. The substrate:
   stall detection, digests, and narration consume summaries instead of
   re-reading transcripts.
3. **Stall/spiral detector.** Diffs recent turn summaries; flags "no progress
   in N turns" (retry loops, repeated failed edits, permission thrash).
   Highest value per token in the catalog: saves frontier-model spend and
   user attention simultaneously.
4. **Approval triage.** On `approval_needed`, classify the pending action:
   badge quietly, or push a one-line summary to the phone. Turns the worst
   interruption pattern into exception handling. Auto-approve only ever behind
   an explicit allowlist (§9).
5. **Attention digest / space narration.** Interval rollup of annotations
   across sessions into one "state of the workspace" paragraph. The
   mobile/Telegram payoff; nearly free once 1–3 exist.
6. **Verifier (trust-but-verify).** After turns claiming completion, a `run`
   action re-executes tests; an observer compares claim to reality and
   annotates discrepancies. An independent verifier that never shares the
   agent's context can't be gaslit by it. Where trust in the layer gets
   earned — and the precondition for ever considering actuation.
7. **Handoff generator / context-pressure advisor.** When `context_pct`
   crosses a threshold, draft a handoff/compact document from the transcript;
   suggest compaction timing. Pairs with history/resume.
8. **Overnight miners.** Batch observers over ended sessions: extract durable
   facts for memory files, cluster failures, harvest real failures into
   regression tests/eval cases, detect repeated manual procedures and propose
   skill/command candidates. Lowest urgency, biggest long-term compounding.
   Transcripts are the richest untapped dataset the mux owns — including
   externally reconciled ones.

Additional catalog entries (unordered):
- **Cross-session conflict sentinel**: two sessions in worktrees of one repo
  touching the same subsystem → warn both (composes with environment
  interlocks, §12).
- **Anomaly/injection sentinel**: compare the agent's actions against its
  stated task; a prompt-injection tripwire at a layer the injected agent
  cannot touch. Out-of-band position makes this credible where in-scaffold
  defenses aren't.
- **Doc-drift watcher**: after turns that edit code, check whether governed
  docs plausibly need updating; annotate rather than nag.
- **Prompt/convention auditor**: check outputs against standing project
  conventions (behavioral lint).
- **Knowledge-graph builder**: entity/relation extraction over transcripts
  into a queryable graph (long-horizon variant of the experience database).

---

## 14. Open questions

- Trigger schema shape for composites: are composites declared (named,
  daemon-computed) or user-composed from primitives? Leaning declared, with
  the primitive fields exposed for conditions.
- Where does the safe-to-inject predicate live: per-adapter method, or a
  daemon-level heuristic over PTY mode signals? Probably both (adapter
  etiquette + daemon state gate).
- Annotation retention/GC policy, and whether annotations on external
  (reconciled) sessions are allowed.
- Model routing for observers: fixed per-rule model ids vs a small named-tier
  indirection ("cheap"/"standard") resolved in global config. Leaning tiers.
- How rulepack parameters are declared and validated; whether project-scope
  rulepacks can bundle scripts at all given the trust boundary.
- Whether `spawn` as a rule action ships gated with actuation or earlier for
  user-initiated flows only (second opinions are user-initiated, so likely a
  palette/universal-command first, rule action later).
