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
  cheap-model "why" is a separate layer (`attention-ranking.md`), off by default, and its
  absence never blocks these.
- **Evidence is a set.** A loop's case is "this fingerprint repeated three times and nothing
  moved"; one `source_event_seq` pointer would not let a reader check it. Every annotation
  carries the contributing fact references (`evidence_json`).
- **Two anchors.** A loop or verification finding belongs to one `agent_run_id`; doc debt is
  a property of the project and has no run to attach to, so it is anchored to `project_id`.
  `automation_annotations.agent_run_id` is nullable for exactly this reason.
- **The run anchor is a conversation boundary, and that includes an in-CLI `/clear`**
  (`backends.md`). A `/clear` mints a new `agent_run_id`, so fingerprint windows, no-progress
  gates, and open completion claims all stop at it without any detector knowing the concept
  exists. This matters most for the two detectors whose logic would otherwise invert: the same
  action repeated across a `/clear` is a human restarting from a clean context, not an agent
  looping — and the no-progress gate would *agree* with the false reading, since nothing
  progressed because the conversation was replaced. Likewise a completion claim from before
  the boundary must not be resolved by a test run after it.
- **Idempotent.** Every finding carries a `dedupe_key`; a detector re-running on the next
  turn boundary returns the existing row rather than a second copy of the same finding.
- **No delivery path.** Output is annotations. Nothing here writes a PTY, approves anything,
  spawns anything, or mutates a project. A `queue_draft` path arrives with the Phase 4 queue.
- **A test file is one by convention, not by spelling.** `is_test_path` matches a
  test *path segment* (`tests/`, `test/`, `__tests__/`, `spec/`) or a basename
  convention (`test_*.py`, `*_test.py`, `*_test.go`, `conftest.py`,
  `*.test|spec.{js,ts,tsx,…}`). It replaces `"test" in path` (audit F26), which
  classified `latest.py`, `contest.py`, and `attestation.ts` as tests. That
  failed in the unsafe direction in both consumers of it: `test_gap` *suppresses*
  a finding for anything it believes is a test, and `blast_radius` counts it as
  its own covering test — so untested code read as covered.

## Detector rules

### Loop / stall

Fires when one canonical fingerprint repeats **≥3** times *and* the no-progress gate holds
across that window. Progress is any of: a failing-test set that got smaller, a file write
whose `content_hash` was not seen earlier in the run, or a second distinct git `head`.

Only facts of a **change-attempting kind** (`command`, `file_write`, `test`, `test_result`)
can seed a loop; every fact in the window still feeds the progress gate. Read-only actions
(kinds `tool`, `file_read` and their results — Grep, Glob, file reads) produce no test
outcome, hash, or commit, so the gate is vacuously true for them by construction and any
agent searching the same directory three times would be flagged (observed live,
2026-07-28). Non-test result kinds are excluded for a second reason: a result fingerprint
collapses onto one value when the payload has no content hash, so distinct successful
edits can share a single `file_write_result` fingerprint.

Two further exclusions, both calibrated against a 24-hour corpus in which the rule scored
**0 true positives out of 13** (2026-08-21).

**A fact must name its action, and this fails closed.**
A fingerprint over an empty target, an empty content hash and an empty state is one constant
for every call of that tool, so a window of it counts distinct actions as repeats of one:
25,362 Bash facts in one day shared the fingerprint of `{"scope":"root","tool":"Bash"}`, and
390 of 397 lifetime findings rested on six such fingerprints.
A fact with no target, no content hash, and no structured test outcome carries no
discriminator and does not seed a loop.
This is deliberately done **as well as** the capture repair that gives the fact a target
(`tier0-facts.md`), not instead of it: the guard holds whatever a future adapter forgets to
send.

**A read-only shell command is repeated looking.**
Every Bash call classifies as `command`, a change-attempting kind, so the exclusion that
already protects `tool`/`file_read` did not reach the shell and an agent polling a
background task's output five times was flagged.
A command whose every pipeline stage begins with a known reading verb (`grep`, `ls`, `git
status|log|diff|show`, `curl` without a write flag, …) cannot seed a loop.
The predicate is conservative in the direction that preserves the detector: an unrecognised
verb, a redirection, a substitution, an unparseable line, or a command the stored target had
to truncate is **not** read-only.
It tokenises with `shlex` rather than splitting on characters, because the live case it
exists for carries a `|` *inside a quoted regex* and a character split reads the second half
as a pipeline stage running a command named `verification`.

**Historical findings are retracted at read time, never rewritten.**
The two rules above invalidate 390 of the 397 findings already stored.
A stored finding is a record of what a detector concluded, so the row is never edited or
deleted to change that record; what changes is what a reader is told about it.
`loop_finding_unsupported` applies the same rule to an annotation's own evidence at the
Findings read and at attention ranking, marking it `unsupported` with the reason.
Evidence recorded before this change carries no `content_hash` key at all, and an absent key
reads as absent evidence — the honest reading, because nothing in the row asserts a
discriminator was ever seen.

The gate is the entire difference between a useful signal and a detector that cries wolf:
running the same test command four times while fixing things is work, not a loop. The
threshold and the gate follow the production precedent cited in CP §2 — which measures
repeated ineffective *attempts*, not repeated looking.

### Declared vs verified

Three facts are kept strictly apart and never collapsed into one ✓: the agent **declared**
done, the tests **ran**, the tests **passed**. Only a claim *without* matching verification
is a finding — an agent that says it is done after a green run is reporting accurately.
Even a green run reads "verified", never "correct".

The claim side is a narrow literal pattern over the last assistant turn; a loose pattern
would turn every ordinary summary into a claim.
Three reductions keep it from matching ordinary English, each answering a measured failure
in a corpus where the rule scored 5 false out of 6 (2026-08-21):

- **The copula is required.** `(?:it|this|that|everything)(?:'s| is| are)?` made every "this
  working tree", "is it working, awaiting input" and "leave it fixed and unexposed" a claim;
  that one alternative produced 27 of 42 lifetime findings and every sampled one was false.
- **Quotation is not assertion.** Fenced blocks and code spans are blanked before matching,
  because both anti-overclaim findings in the corpus fired on a message *quoting the
  requirement*.
- **A claim is made in the closing summary**, so the search is bounded to a whole number of
  trailing paragraphs within `CLAIM_SCOPE_CHARS`, and a failure word in the few characters
  immediately before the match inverts it ("once shipped a failing test green").
  The window is short on purpose: "I fixed the failing tests and all tests pass" is a real
  claim whose sentence also contains "failing".

**A run with no test facts produces nothing at all.**
With zero `test_result` facts the detector cannot tell "this agent verified nothing" from
"this install captured nothing", and the second was almost always the true reading — one
test fact stood against 4,485 command results in the measured window — so every finding was
a statement about the substrate wearing the shape of a statement about an agent.
What remains is the checkable case: tests ran, they did not all pass, and the agent said it
was done anyway.
The recall this trades away is bought back by capturing the land queue's gate as a
`test_result` fact (`tier0-facts.md`), which is the durable half of the same repair.

**The finding carries the claim's own pointer**, not only the test facts: the session, run,
transcript and message timestamp it was read from.
Every one of the 42 lifetime findings carried an empty evidence set, which breaks the
"evidence is a set" contract outright — the reader had nothing to check, not even the
message the claim came from.

### Doc-debt ledger

The routing table in `.docs/CLAUDE.md` is keyed by *change type*, which no machine can match
to a file path. Each doc's **"Key files"** section is the same routing information already
written as literal paths, so `build_doc_ownership` inverts those sections into
`source path → owning docs`. A doc that adopts a module by listing it is immediately
covered, and there is no second list to maintain.

A file claimed by **more than 4 docs** (`DOC_HUB_OWNER_LIMIT`) is infrastructure — a
composition root like `server.py` (15 claimants) or `App.tsx` (8) — not a subject any
single doc owns, and carries no ownership signal: one `App.tsx` edit otherwise marks
eight unrelated feature docs dirty (observed live, 2026-07-28). The limit is calibrated
against this repo's `.docs` tree, where 1–4-owner files all have a genuine subject doc
among their claimants and the ≥5 tail is exactly the composition roots.

The hub rule applies to **dependency reach** exactly as it applies to direct ownership.
The Phase 7.9 reach refinement unioned the owning docs of every dependent of a changed file
with no limit, re-admitting through the back door the explosion `DOC_HUB_OWNER_LIMIT` was
calibrated to prevent: one window's finding read "21 doc(s) owe an update for 3 changed
source file(s)" — very nearly the whole `.docs` tree — from edits to three composition roots
(`server.py` reaches 19-20 files at ≤2 hops).
So a changed file whose reverse reach exceeds `DOC_REACH_DEPENDENT_LIMIT` is a hub by reach
and contributes no owners, and a reach set resolving to more than `DOC_HUB_OWNER_LIMIT` docs
is dropped whole.
Both are the same statement — a signal that points at everything points at nothing — and
truncating to the first N instead would report an arbitrary subset as *the* owners.

**One row per dirty doc**, dedupe-keyed on `(project, doc)`, exactly as provenance is keyed
per edge and for the same reason.
The original key was a set hash over the dirty-doc set, so one more dirty doc minted a whole
new row restating all the others: 137 rows carried 137 distinct keys, and one window's
8-doc set was a strict subset of the 9-doc set beside it (2026-08-21).
Keyed per doc, one dirty doc is one row forever and its changed-file list lives in the
content; a per-pass cap (`DOC_DEBT_MAX_NEW_PER_PASS`) bounds a first pass without truncating,
because a doc past the cap lands on the next turn boundary.

Debt accumulates with a visible count rather than nagging per turn, and a doc edited in the
same window is not dirty — the debt was paid as it was incurred.

**The ownership map is cached behind a fingerprint of the docs tree, and the cache is
shared.** `cached_doc_ownership` keys on every markdown path with its `mtime_ns` and size,
because the previous `max(mtime)` key could not see a delete or a rename: both leave the
newest mtime untouched, so the map kept owning a file no doc mentions. Size is in the key
for a second reason — Windows freezes a file's reported mtime while a handle is open, so a
doc being written right now grows without its mtime moving.
The cache is module-level rather than per-service so the MCP `blast_radius`/`doc_debt` tools
share one build with the consumer loop; `mcp.py` used to reparse the whole `.docs` tree on
every call while an identical map sat cached beside it (audit F22). It is lock-free — a
concurrent miss costs a duplicate build, which is what the uncached callers did every time —
and bounded to `_OWNERSHIP_CACHE_MAX_ROOTS` project roots, since a daemon runs for weeks.

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

**One annotation per edge**, dedupe-keyed on the two fact ids (`writer_fact_id >
reader_fact_id`), so one real-world write→read event is exactly one row forever. The
original set-hash key changed whenever the window's graph grew, minting a new annotation
that restated every prior edge — quadratic storage, and each edge counted once per
restatement by anything ranking annotations (observed live 2026-07-28). Re-deriving the
whole window each turn is fine: already-recorded edges dedupe to no-ops, and a per-pass cap
(`PROVENANCE_MAX_NEW_PER_PASS`, 50) bounds a first pass over a busy window without
truncating — the remainder lands on subsequent turn boundaries. Aggregation ("today's
cross-session graph") belongs to the reader, grouping rows by target.

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

- Findings are ordinary annotations with `provenance: "deterministic_consumer"` and tags
  `loop-detected`, `declared-vs-verified`, `doc-debt`, `provenance`.
- The human Findings read is `GET /api/annotations` (Phase 7.10), filtered by `tag`,
  `project_id`, `session_id`, `agent_run_id`, and `since`, and carrying `tag_counts` for the
  current scope so a quiet scope reads apart from one buried under provenance edges
  (`interfaces.md`).
  An item whose own evidence no longer supports it carries `unsupported` and
  `unsupported_reason`; the pane withholds it from the list and states the count and the
  reason rather than dropping it silently, and `tag_counts` still counts the stored row.
- The `doc_debt` mux MCP tool exposes the doc-debt finding to an agent as re-derived
  `{doc, changed_files}` pairs, gated on the same `doc_debt` automation (`mux-mcp.md`).
- Health under `deterministic_consumers` in `GET /api/diagnostics/background`.
- Per-project opt-in is edited through `GET|PUT /api/projects/{project_id}/automations`
  (see `automation-enablement.md`).

## Where findings surface, and the two scopes

The human surface is the Findings pane, a segment of the Insight drawer tab beside the scan
Timeline (`technical/frontend/packages.md`).
It is read-only by construction: no dismiss and no mutation, so the pane never enters the
actuation gate.
It scopes to this session or this Project, defaulting to session.
A session scope resolves to the session's run-id set and matches `agent_run_id`, so a
Project-anchored finding with no run — doc-debt and cross-session provenance — is absent from
session scope by construction.
That absence is correct, not a gap, so the pane always states what the current scope excludes:
silence must read as scope, never as absence, which is the same "off vs quiet" rule the memory
tools follow.
Provenance is the one high-volume tag, so the default view hides it and its chip count reveals
it, rather than letting it bury the sparse findings.

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
- `attention-ranking.md` — the consumer that decides which of these findings is worth
  interrupting a human for, and when.
