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

The browser labels this surface `All-session health`; `fleet` remains the implementation term
for the complete set of live/recent sessions. Passive deterministic signals remain distinct
from optional OpenRouter attention observers. The Attention inbox contains actionable notices;
the away report aggregates inbox items and run annotations since the last attach/input activity.
The shared attention-observer setting also enables the 30-minute unread-attention digest.

## Cross-session intelligence

- Same trusted project scope + Git branch warns about concurrent work.
- Owned listeners and registered previews expose port collisions.
- Owned loopback connections reveal one session consuming another session's dev server.
- Resume, handoff, continuation, and review lineage link atomic agent runs without merging
  their history or confusing canonical Projects with derived Git scopes.
- Workload telemetry groups observed turn/stall/approval rates, duration, context,
  completion evidence, tokens, backend/model, and aggregate ccusage cost. It is explicitly
  correlation, not a causal benchmark.

## User-initiated review and knowledge

History can export a reviewable handoff from annotations or create a cross-vendor second
opinion. The preview contains the full prompt plus bounded current Git status/diff-stat;
confirmation must return its exact preview token. Only that typed user operation may spawn
the other backend, after which it records a review lineage edge.

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

## Key files

- `src/swe_mux/fleet_intelligence.py`
- `src/swe_mux/processes.py`
- `src/swe_mux/history.py`
- `src/swe_mux/server.py`
- `frontend/src/AutomationDashboard.tsx`
