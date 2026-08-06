# Evidence replay and delivery readiness

## What it is

Phase 1 provides a deterministic regression boundary around Claude/Codex lifecycle
observation and a provider-neutral, read-only delivery classification. It does not type into
a PTY or authorize automation.

`delivery_state` is separate from display state:

- `safe` means every required root-lifecycle, run, parser/hook, and human-input fact is
  positively known, and nothing contradicts the screen the agent draws its prompt on.
- `blocked` means current evidence positively forbids delivery, such as working, approval,
  elicitation, rate limit, a screen that is not the agent's own, recent/post-completion
  input, interrupted turn, demotion, exit, or a stale transcript.
- `transcript_stale` blocks rather than degrading to unknown. When the followed transcript is
  no longer this PTY's conversation (an unfollowable in-CLI `/clear`/`/new` — `backends.md`),
  every positive signal here is being read off a retired conversation, so the evidence is not
  *missing*, it is *wrong about a different session*. Writing into a session whose state we
  are provably misreading is precisely the false-safe case this contract exists to prevent.
  Because it is a hard block with no softer degradation, the cost of raising it wrongly is
  paid entirely by the operator — see the 2026-08-06 correction.
- `unknown` means evidence is missing, stale, replaced, or degraded. Unknown is never safe.

Standing-activity annotations (`status-detection.md` § Standing-activity annotations) are
deliberately invisible to this classification: an idle session with an armed `/loop`, a
cron schedule, background tasks, or a decaying subagent annotation is exactly as
deliverable as an idle one — that is the user-facing point of modeling them as
annotations rather than states. The corpus pins a loop-armed idle turn end evaluating
`safe`.

Every result remains `authorized: false`: the tracker classifies evidence and never grants
authority. Two callers act on that classification, both outside this module — the Phase 4
queue's `send_next` (a human act, with an explicit confirm for blocked/unknown) and the
Phase 5 auto-delivery controller (which requires `safe` held continuously across a window
and can never confirm). See `auto-delivery.md`.

The controller's authority is still separate from this classifier: the install master must be
enabled, and the daemon materializes a bounded default-on grant for each live agent run. A
per-conversation opt-out, global pause, expiry, or exhausted send cap keeps it from acting even
when readiness is `safe`.

**Promotion criteria.** Widening auto-delivery beyond its Phase 5 bounds requires zero known
false-safe results across six fixture classes — `approval_required`, `awaiting_user_input`,
`rate_limited`, `subagent_activity`, `active_operator_input`, `run_replacement` — pinned by
`tests/test_delivery_readiness_promotion.py` both directly against this tracker and over the
golden corpus below, plus a volume/duration proving period counted in SQLite and reported at
`GET /api/queue/auto`. An operator's unsafe report resets the clock and pauses the feature.

## Runtime evidence contract

Native hooks and transcripts normalize to root-scoped lifecycle/tool events or explicitly
subagent-scoped activity. Child stops never complete a root turn. Hook/transcript duplicates
coalesce, while completion boundaries remain tied to a stable `agent_run_id` — and
"stable" now includes the in-CLI conversation boundary: a `/clear` mints a new run id, so
`stable_run_identity` fails and the tracker refuses until the successor proves itself.

Provider identity and transcript ownership are prerequisites for interpreting that evidence.
Root-process provider identity is immutable; nested launcher hooks cannot replace it. A
transcript/native pair claimed by another live session is rejected, and multiple unowned
candidates remain unknown rather than being selected by recency. Adoption repairs legacy
supervisor metadata before restarting observers, so sibling state, model, token, context, and
delivery facts cannot become authoritative for the wrong session.

The daemon reads the screen switch (`\x1b[?1049h/l` and the older `?47`/`?1047` spellings)
off the PTY stream itself (`screen_mode.py`), so it holds this fact for every session and
not only for one somebody is watching; an adopted session replays its retained scrollback
through the same parser, because the switch is written once at startup and never repeated.
The browser also reports xterm's active `normal|alternate` buffer after input ownership, on
buffer changes, and periodically, but that report is diagnostic and can never block. It is
not the same kind of fact: xterm reports the buffer *its own replayed copy* of the stream
selected, so a pane that attached after the child's `?1049h` scrolled out of the retained
scrollback reports `normal` for a child that has been on the alternate screen since startup
— measured on a live Claude session right after a daemon restart. A derived state that is
wrong exactly when the daemon's own reading is missing cannot stand in for it. Terminal protocol responses and mouse reports are labeled separately
and do not count as human input; keystrokes and bracketed paste advance an in-memory input
revision. The daemon evaluates these facts synchronously without an await boundary. It
stores bounded reasons/transitions only—never terminal bytes or prompt bodies.

**Corrected 2026-07-30 — four preconditions no real session could satisfy.** `safe` was
unreachable, so every queued message needed the operator's explicit override, which trains
the operator to click through the one prompt that is meant to stop them. Each is now
evidence rather than a prerequisite:

- **An attached browser and an exclusive input owner were required** (`terminal_observer_
  disconnected`). Delivery is the daemon writing to a PTY it owns; a rendering pane is not
  part of that, and demanding one blocked every session the operator was not looking at —
  the entire population a queue exists for. Both facts remain under `evidence`.
- **The alternate screen was treated as danger.** Claude Code enters it at startup and never
  leaves, so a *watched* Claude session was permanently `alternate_screen_active` while an
  unwatched one was `terminal_observer_disconnected`: both branches blocked. What is checked
  now is a *change* away from the screen the CLI was on when its root turn completed — the
  moment it had definitely just rendered its prompt — which needs no per-version or
  per-configuration knowledge and does not punish a Codex launched with an explicit
  `tui.alternate_screen` override. `ADAPTER_DELIVERY_ETIQUETTE[…]["screen"]` (claude
  `alternate`, codex `normal`, because mux launches Codex with `tui.alternate_screen="never"`
  for scrollback) is the fallback when a completion predates any screen evidence. Absent
  screen evidence is missing, not damning.
- **Mouse reports counted as typing.** xterm delivers them on the same channel as
  keystrokes once the child enables tracking, so a pointer crossing a pane advanced
  `input_revision` (~170 per 20s on one live session) and readiness read that as a
  half-typed prompt in the composer — `terminal_input_after_completion`, permanently, since
  the completion snapshot only moves at the next turn. `pointer_report_kind` (`server.py`)
  now classifies them: a click or wheel notch is presence and moves the operator-quiet
  clock, pure motion is neither, and neither advances the revision.
- **Lifecycle evidence expired after five minutes**, so an agent parked at its prompt — the
  most deliverable state there is — decayed to `lifecycle_evidence_stale`. The bound now
  applies to every phase except a completed root turn on a still-idle session, which is a
  resting state that any new activity (a turn start, an approval, operator input) would
  itself contradict through a path this tracker already watches.

**Corrected 2026-07-30 (second pass) — readiness did not survive a daemon restart.** The
tracker's lifecycle memory lives in the daemon process, and the observer deliberately
suppresses replayed history, so a session that was already idle when the daemon restarted
had no record that its last root turn finished — and `parser_status` only reaches `ready`
off a *live* transcript record, which such a session cannot produce until its next turn (no
hook fires while it waits). Both gaps closed at once: when the observer's catch-up settles
(`catchup:settled`) it leaves `observation_state["catchup_settled"]` on the session, holding
the number of records it read plus the input revision and screen mode *at that instant*, so
the composer-collision guard still measures from when the conclusion was true. The tracker
picks it up on its next evaluation and takes it as a completed root turn *only when it has
no lifecycle evidence of its own* — it fills a gap, it never overrules — and a non-zero
record count as proof of observation capability, since reading and interpreting this
session's own transcript is exactly what that check asks.

It is left on the session rather than only announced because ordering had already eaten the
announcement: adoption catches observers up hundreds of lines of startup before
`fleet.start()` subscribes to the bus, so on a live fleet the one session whose observer
settled during startup emitted `root_turn_settled` to no subscriber at all. The event is
still emitted for the audit trail and the live path — but never as a synthetic `turn_ended`,
which would fire read-aloud, notifications, and turn observers for a turn that ended before
the restart.

**Corrected 2026-07-30 (third pass) — a new session could not send its first prompt.** Every
signal above is about a turn *ending*, which a session nobody has used yet cannot produce, so
the first message to a fresh agent was refused as `no_root_lifecycle_evidence` on the one
session where nothing can possibly be in flight. The CLI's own `SessionStart` hook is the
positive evidence that was being ignored: it now sets `observation_state["session_start_seen"]`
and readiness takes it as `agent_started_awaiting_first_prompt` once a settle has passed —
but only for a session with `input_revision == 0`, because keystrokes mean a composer whose
contents this cannot see.

The settle is `AGENT_FIRST_PROMPT_SETTLE_SECONDS` (8s from the spawn the tracker observed,
plus the ordinary debounce), and it is a timer on purpose. Measured against Claude Code
v2.1.220: the session reports idle at spawn+1.02s but a submitted line is **silently
swallowed** until spawn+~4.5s — the paste lands in the composer and only the CR is dropped,
so the message sits there looking delivered, which is exactly the false-safe this contract
exists to prevent. A submit at idle+3.0s failed and at idle+3.5s succeeded, 3/3 each way. The
PTY offers nothing better to key on: output goes quiet from spawn+1.0s to spawn+3.4s and only
*then* paints the composer, so "the terminal settled" fires inside the swallow window — which
is also why the status layer's own one-second startup fallback calls this idle, and why only
the CLI's hook counts here.

**Corrected 2026-08-06 — `transcript_stale` fired on healthy Codex sessions.** A finished,
idle Codex agent refused its armed queue message, and the operator's forced send reported
`Not safe right now: transcript_stale`. Nothing was stale: staleness was being measured with
`stat().st_mtime`, and Windows does not keep a live file's last-write time current. Measured
across five Codex rollouts, every one reported an mtime frozen at the file's *creation* while
its content ran 290 s to 3.5 h ahead, and every Win32 timestamp API agreed. The affected
session's ledger held 30+ `observation_stale` events, all citing one unmoving
`transcript_mtime`. The fix is in `backends.md`: the daemon dates writes from its own tailer's
size polling rather than from the filesystem, and a stale claim is retracted when the followed
file is written again.

This is the failure mode the four corrections above share, in its most damaging form. A hard
block with no softer degradation, raised by evidence that is silently wrong, presents to the
operator as a session reading `idle · turn complete` in every surface while the queue refuses
it — so the only way to work is to override, every time, which is precisely how the
confirmation that exists to stop a genuinely unsafe send stops being read. A false `blocked`
is not the safe direction of a mistake; it is how the safety mechanism gets trained away.

What carries the safety argument after those four is the composer-collision guard:
`partial_input_absent` compares the input revision against its value when the root turn
completed, so anything the operator typed since — including opening a pager, which takes
typing — still blocks.

**Closed 2026-07-28 (with Phase 4.5).** `POST /sessions/{id}/input` and broadcast fan-out
previously wrote to the PTY without advancing `input_revision` / `last_input_event_ts`,
without emitting `terminal_input`, and without an ended-session guard, so REST-delivered
text — `mux send`, note send-to-agent, the prompt library's delivery path — was invisible
to these counters and every readiness report emitted `partial_input_absent` /
`operator_quiet` as satisfied for text sitting undelivered in a composer. All operator
input paths now share one accounting helper (`_record_operator_input` in `server.py`): the
HTTP route (`source="http"`, 409 for ended sessions), broadcast per-target
(`source="broadcast"`, `input_owner=False`, ended targets skipped), voice
(`source="voice"`), and — since Phase 4 — prompt-queue delivery (`source="queue"`,
`input_owner=False`; both the paste and the submit write, see
`features/prompt-queue.md`). Deliberately NOT counted: automation `write_pty`, the branch command
write, and process interrupts — those are not *operator* input, and advancing the
operator-quiet clock for them would mask a different hole. The WS path keeps its own
throttled accounting.

Transcript classification records schema version, recognized and unknown counts, bounded
unknown signatures, and a degraded status after sustained drift. Claude discovery follows
the CLI's current project-directory encoding. Codex discovery honors `CODEX_HOME` and rejects
rollouts with `parent_thread_id`; history reconciliation applies the same root-only rule.

## Regression suite

`tests/fixtures/detection/v1/` is the sanitized golden corpus. The virtual clock and replay
harness control transcript chunks/truncation, hooks, terminal state/input/focus, process
evidence, timers, restart, replacement, demotion, and exit. Golden tests compare normalized
event streams, parser coverage, delivery state/reason, and safe/unsafe oracle checkpoints.
They contain no native prompt bodies, credentials, or terminal captures. Since Phase 3.5
the same corpus also pins the user-visible status stream (`expected.states`), awaiting
sub-reasons, and the watchdog recovery paths, with an edge-case inventory that fails CI
when a fixture or its guard disappears; see `design/features/status-detection.md`.

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
- `src/swe_mux/screen_mode.py`
- `src/swe_mux/event_bus.py`
- `tests/test_delivery_readiness_evidence.py`, `tests/test_screen_mode.py`
- `tests/support/detection_replay.py`
- `tests/fixtures/detection/v1/`
- `tests/test_detection_replay.py`
- `tests/test_live_agent_conformance.py`
