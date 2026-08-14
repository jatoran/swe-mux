# Fleet attention and intelligence

## What it is

Explainable cross-session evidence, attention routing, lineage, and user-confirmed review
built on normalized events and read-only observer results.

## Attention

`FleetIntelligence` combines persisted events with trusted run scope, transcript progress,
PTY activity, attach/input state, process CPU/listeners/connections, and native context
telemetry. It emits explainable evidence/confidence for repeated-failure spirals, stalls,
unattended approvals, output runaways, context pressure, and unverified completion claims.
Semantic triage uses the Phase-6 observer/budget substrate; deterministic evidence remains
preferred. The shared inbox, interval digest, and checkpoint-based absence report are
provider-neutral and work in desktop/mobile browsers.

These records are evidence, not a routing decision. Which of them is worth interrupting a
human for, and when, is decided by `attention-ranking.md`, which consumes the fault-carrying
events emitted here (`stalled`, `runaway`, `context_pressure`, `claim_unverified`,
`unattended_attention`, and the `port_collision` interlock) alongside the deterministic
detectors' annotations. `cross_session_dev_server` stays out of ranking for the same reason it
stays out of the inbox: it is the documented workflow, not a fault.

The browser labels this surface `All-session health`; `fleet` remains the implementation term
for the complete set of live/recent sessions. Passive deterministic signals remain distinct
from optional OpenRouter attention observers. The Attention inbox contains actionable notices;
the away report aggregates inbox items and run annotations since the last attach/input activity,
and carries the ranked items, suppressed counts, and rollover boundaries the digest half of
`attention-ranking.md` adds to the same response.
The shared attention-observer setting also enables the 30-minute unread-attention digest.

## Cross-session intelligence

- Same trusted project scope + Git branch warns about concurrent work.
- Owned listeners and registered previews expose port collisions.
- Owned loopback connections reveal one session consuming another session's dev server.
- An interlock is a **condition, announced once per appearance**, not a repeating event. The
  5s sweep refreshes the fingerprint it already holds and re-arms it only after the condition
  has been absent for the clear window (300s), so a legitimately shared server costs one
  record rather than twelve an hour for the life of both sessions. Anything still true is not
  news.
- Not every interlock is a fault. `port_collision` (two sessions bound to the same port) is
  one and becomes an attention record; `cross_session_dev_server` is **evidence only** — it
  reaches the event bus, automation rules, and the absence report, but never the inbox,
  because driving another session's loopback server is how a second daemon, a preview, or a
  test harness is meant to be exercised. A detector that fires on the documented workflow
  trains the user to ignore the surface it fires into.
- Resume, handoff, continuation, and review lineage link atomic agent runs without merging
  their history or confusing canonical Projects with derived Git scopes.
- Workload telemetry groups observed turn/stall/approval rates, duration, context,
  completion evidence, tokens, backend/model, and aggregate ccusage cost. It is explicitly
  correlation, not a causal benchmark.

## User-initiated review and knowledge

History can export a reviewable handoff from annotations or create a cross-vendor second
opinion. A handoff identifies the swe-mux history row and provider-native session, embeds the
authoritative native transcript path, and explicitly directs the recipient to inspect that file
for the complete conversation; it does not copy transcript content. The review preview contains
the full prompt plus bounded current Git status/diff-stat; confirmation must return its exact
preview token. Only that typed user operation may spawn the other backend, after which it records
a review lineage edge.

The experience index stores error/resolution evidence across backends; the browser presents
these entries as `Learned fixes`. A similar live tool failure may create a `prior-resolution`
annotation only; it never injects text or writes a memory/project file. Explicit observer
batches accept at most 25 ended transcript-backed runs, show a cost/token estimate, require an
exact preview token, respect all budgets, and retain preview/export results without repository
mutation.

## Actuation boundary

The injection-safety endpoint reports idle/input-owner/composer/terminal-mode/adapter
evidence and always returns `authorized: false`. Unknown evidence fails closed. Observers
may annotate, notify, summarize, suggest, and report; they never type, approve, spawn,
execute, relay, or mutate a project.

## Reliability

Both loops — the 5s inspection pass and the event consumer — run under the shared
background-task supervisor, so a single failure costs one iteration rather than every
detector for the rest of the daemon's life. This is not hypothetical: the interlock emit
suspends on the durable event sink, and the server pops sessions concurrently, so the
subsequent lookup could raise `KeyError` and silently take stall, unattended, runaway,
context-pressure, interlock and digest detection with it. Health is at
`GET /api/diagnostics/background`; per-session accumulators (including the delivery-readiness
tracker's) are dropped on `session_exited`/`session_crashed`, and in-flight claim checks are
strongly referenced and cancelled at `stop()`.

## Key files

- `src/swe_mux/fleet_intelligence.py`
- `src/swe_mux/background_tasks.py`
- `src/swe_mux/processes.py`
- `src/swe_mux/history.py`
- `src/swe_mux/server.py`
- `frontend/src/AutomationDashboard.tsx`
