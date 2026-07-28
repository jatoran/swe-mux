# Deterministic control-plane consumers

## What it is

The first layer that turns captured Tier 0 facts into user-visible judgements, and it is
deliberately **model-free**: every detector is a query over deterministic facts, spends no
tokens, and writes nothing but annotations. Roadmap Phase 3.7 / control-plane build-order
step 3 (`../../development/CONTROL_PLANE_ROADMAP.md` §6.1, §6.3–6.5).

Four detectors ship together:

| Detector | Reads | Answers |
|---|---|---|
| Loop / stall | Tier 0 fingerprints | the same canonical action repeated with nothing moving |
| Declared vs verified | Tier 0 test facts + the last assistant turn | "done" claimed without verification |
| Doc-debt ledger | Tier 0 file writes + each doc's Key files | which docs owe an update |
| Provenance graph | Tier 0 writes/reads across sessions | who wrote what this session then read |

## Key concepts

- **Deterministic detector, never a narrator.** A finding states what the facts say. The
  cheap-model "why" is a separate, later layer (CP §14) and its absence never blocks these.
- **Evidence is a set.** A loop's case is "this fingerprint repeated three times and nothing
  moved"; one `source_event_seq` pointer would not let a reader check it. Every annotation
  carries the contributing fact references (`evidence_json`).
- **Two anchors.** A loop or verification finding belongs to one `agent_run_id`; doc debt is
  a property of the project and has no run to attach to, so it is anchored to `project_id`.
  `automation_annotations.agent_run_id` is nullable for exactly this reason.
- **Idempotent.** Every finding carries a `dedupe_key`; a detector re-running on the next
  turn boundary returns the existing row rather than a second copy of the same finding.
- **No delivery path.** Output is annotations. Nothing here writes a PTY, approves anything,
  spawns anything, or mutates a project. A `queue_draft` path arrives with the Phase 4 queue.

## Detector rules

### Loop / stall

Fires when one canonical fingerprint repeats **≥3** times *and* the no-progress gate holds
across that window. Progress is any of: a failing-test set that got smaller, a file write
whose `content_hash` was not seen earlier in the run, or a second distinct git `head`.

The gate is the entire difference between a useful signal and a detector that cries wolf:
running the same test command four times while fixing things is work, not a loop. The
threshold and the gate follow the production precedent cited in CP §2.

### Declared vs verified

Three facts are kept strictly apart and never collapsed into one ✓: the agent **declared**
done, the tests **ran**, the tests **passed**. Only a claim *without* matching verification
is a finding — an agent that says it is done after a green run is reporting accurately.
Even a green run reads "verified", never "correct".

The claim side is a narrow literal pattern over the last assistant turn; a loose pattern
would turn every ordinary summary into a claim.

### Doc-debt ledger

The routing table in `.docs/CLAUDE.md` is keyed by *change type*, which no machine can match
to a file path. Each doc's **"Key files"** section is the same routing information already
written as literal paths, so `build_doc_ownership` inverts those sections into
`source path → owning docs`. A doc that adopts a module by listing it is immediately
covered, and there is no second list to maintain.

Debt accumulates with a visible count rather than nagging per turn, and a doc edited in the
same window is not dirty — the debt was paid as it was incurred.

### Provenance graph

Write-side and read-side content hashes are **not joinable by equality** (a `Read` result
hashes the CLI's rendering of a file, not the file), so the edge is stated as `target` plus
time order, carrying the writer's hash as the thing that was written. This is the recorded
resolution of the step 1 gap: a per-backend normalizer would have to reconstruct file bytes
from a lossy, version-drifting rendering, and truncated reads make that impossible in
general.

Edges are cross-session only, and `ambiguous` marks the case where another write to the same
target falls between the write and the read — precisely when "the reader saw this write"
stops being a fact. Never a causal blame label; the human draws the conclusion.

## Operations

- Event-driven on `turn_ended`, not polled: every detector is a query over facts that only
  change when a turn produces them.
- Gated per project through the same enablement resolution and TTL cache Tier 0 uses, so a
  project can never have one running under a stale answer the other already refreshed.
- Run-scoped queries read `tier0_facts` by `agent_run_id`; the provenance and doc-debt
  queries read by `project_id` across sessions. Both windows are bounded for cost.
- Findings are counted and the loop's liveness is reported: a detector that stopped
  producing findings is otherwise indistinguishable from a quiet fleet.

## API surface

- No dedicated route. Findings appear as ordinary annotations (`GET /api/automation/...`,
  the History transcript view) with `provenance: "deterministic_consumer"` and tags
  `loop-detected`, `declared-vs-verified`, `doc-debt`, `provenance`.
- Health under `deterministic_consumers` in `GET /api/diagnostics/background`.
- Per-project opt-in is edited through `GET|PUT /api/projects/{project_id}/automations`
  (see `automation-enablement.md`).

## Configuration

Per-project opt-in in `<project>/.swe-mux/config.toml`, e.g.

```toml
automations = { raw_store = true, tier0 = true, loop_detection = true, doc_debt = true }
```

A consumer whose substrate is not enabled resolves as blocked and does nothing.

## Key files

- Detectors and runner: `src/swe_mux/deterministic_consumers.py`
- Fact queries: `src/swe_mux/tier0_store.py`
- Annotation storage (project anchor, evidence set, dedupe key):
  `src/swe_mux/automation_store.py`
- Gate resolver, wiring, toggle routes: `src/swe_mux/server.py`
- Toggle surface: `frontend/src/ProjectsManager.tsx`

## Relates to

- `tier0-facts.md` — the substrate every detector reads.
- `automation-enablement.md` — the opt-in DAG that gates them.
- `automation.md` — the model tier (observers), which these deliberately are not.
