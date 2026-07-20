# Evidence replay and delivery readiness

## What it is

Phase 1 provides a deterministic regression boundary around Claude/Codex lifecycle
observation and a provider-neutral, read-only delivery classification. It does not type into
a PTY or authorize automation.

`delivery_state` is separate from display state:

- `safe` means every required root-lifecycle, run, parser/hook, terminal, ownership, and
  human-input fact is positively known.
- `blocked` means current evidence positively forbids delivery, such as working, approval,
  elicitation, rate limit, alternate screen, recent/post-completion input, disconnect,
  interrupted turn, demotion, or exit.
- `unknown` means evidence is missing, stale, replaced, or degraded. Unknown is never safe.

Every result remains `authorized: false`. The implementation is shadow diagnostics for a
future queue phase, not an actuation mechanism.

## Runtime evidence contract

Native hooks and transcripts normalize to root-scoped lifecycle/tool events or explicitly
subagent-scoped activity. Child stops never complete a root turn. Hook/transcript duplicates
coalesce, while completion boundaries remain tied to a stable `agent_run_id`.

The browser reports xterm's active `normal|alternate` buffer after input ownership, on buffer
changes, and periodically. Terminal protocol responses are labeled separately and do not
count as human input; keystrokes and bracketed paste advance an in-memory input revision.
The daemon evaluates these facts synchronously without an await boundary. It stores bounded
reasons/transitions only—never terminal bytes or prompt bodies.

Transcript classification records schema version, recognized and unknown counts, bounded
unknown signatures, and a degraded status after sustained drift. Claude discovery follows
the CLI's current project-directory encoding. Codex discovery honors `CODEX_HOME` and rejects
rollouts with `parent_thread_id`; history reconciliation applies the same root-only rule.

## Regression suite

`tests/fixtures/detection/v1/` is the sanitized golden corpus. The virtual clock and replay
harness control transcript chunks/truncation, hooks, terminal state/input/focus, process
evidence, timers, restart, replacement, demotion, and exit. Golden tests compare normalized
event streams, parser coverage, delivery state/reason, and safe/unsafe oracle checkpoints.
They contain no native prompt bodies, credentials, or terminal captures.

Ordinary CI runs the deterministic suite and skips provider calls:

```powershell
uv run pytest tests/test_detection_replay.py
```

Authenticated canaries are deliberately opt-in because they consume quota and create normal
provider transcript history:

```powershell
$env:SWEMUX_RUN_LIVE_AGENT_TESTS = '1'
uv run pytest tests/test_live_agent_conformance.py -m "live_agent and not live_subagent"

$env:SWEMUX_RUN_LIVE_SUBAGENT_TESTS = '1'
uv run pytest tests/test_live_agent_conformance.py -m live_subagent
```

Canaries use a temporary read-only workspace and harmless fixed prompts. They assert current
CLI transcript discovery, schema coverage, root start/completion, and child activity without
checking model prose. They should run on CLI upgrades and periodically in a protected
authenticated lane, not on every pull request.

## Diagnostics

`GET /api/automation/injection-safety` returns the v2 research contract, per-session checks,
bounded evidence, parser coverage, and aggregate shadow counts/reasons/unknown duration.
`GET /api/sessions` includes a compact `delivery_readiness` summary for the session surface.
The Automation Diagnostics view exposes the complete evidence and always states that
actuation is unauthorized.

## Key files

- `src/swe_mux/observation.py`
- `src/swe_mux/delivery_readiness.py`
- `src/swe_mux/event_bus.py`
- `tests/support/detection_replay.py`
- `tests/fixtures/detection/v1/`
- `tests/test_detection_replay.py`
- `tests/test_live_agent_conformance.py`
