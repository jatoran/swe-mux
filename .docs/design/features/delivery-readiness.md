# Evidence replay and delivery readiness

## What it is

The versioned replay corpus provides a deterministic regression boundary around every observed
harness and a provider-neutral, read-only delivery classification.
It does not type into a PTY or authorize automation.

`delivery_state` is separate from display state:

- `safe` means every required root-lifecycle, run, observation, and human-input fact is
  positively known, and nothing contradicts the screen the agent draws its prompt on.
- `blocked` means current evidence positively forbids delivery, such as working, approval,
  elicitation, rate limit, a screen that is not the agent's own, recent/post-completion
  input, interrupted turn, demotion, exit, a stale transcript, or a non-local terminal boundary.
- `transcript_stale` blocks rather than degrading to unknown. When the followed transcript is
  no longer this PTY's conversation (an unfollowable in-CLI `/clear`/`/new` — `backends.md`),
  every positive signal here is being read off a retired conversation, so the evidence is not
  *missing*, it is *wrong about a different session*. Writing into a session whose state we
  are provably misreading is precisely the false-safe case this contract exists to prevent.
  Because it is a hard block with no softer degradation, the cost of raising it wrongly is
  paid entirely by the operator — see the 2026-08-06 correction.
- `unknown` means evidence is missing, stale, replaced, or degraded. Unknown is never safe.

Interrupt intent does not make a session deliverable.
`interrupt_pending_at` changes only the visible status and timer; the open root turn continues to block until provider evidence or the owned PTY confirms `turn_aborted`.
Interrupted, superseded, error, and length outcomes remain blocked by their typed root-turn reason rather than being treated as successful idle completion.

`local_terminal_boundary` is a required readiness check.
A session whose runtime boundary is `remote` is hard-blocked with `remote_terminal_boundary`, regardless of an otherwise idle-looking prompt.
An unrecognized or unavailable boundary is hard-blocked with `terminal_boundary_unknown`.
Only an explicit `local` boundary satisfies the check.
This keeps SSH authentication and remote shells out of inferred auto-delivery target sets while
preserving ordinary manual terminal input.
The readiness evidence records the boundary, remote authority, and remote transport state but
never terminal bytes or authentication prompt text.

Standing-activity annotations (`status-detection.md` § Standing-activity annotations) are
deliberately invisible to this classification: an idle session with an armed `/loop`, a
cron schedule, background tasks, or a decaying subagent annotation is exactly as
deliverable as an idle one — that is the user-facing point of modeling them as
annotations rather than states. The corpus pins a loop-armed idle turn end evaluating
`safe`.

`interject_state` is a second, strictly narrower classification beside `delivery_state`,
and never a relaxation of it.
It answers a different question - may text be written into a turn that is *currently running* - and it exists because `blocked` covers a dozen unrelated situations of which only one is "the agent is busy".
The rest are an approval dialog, a model picker, an elicitation, a rate limit, a retired transcript, a remote shell, and writing into any of those is corruption rather than urgency.
So it does not ask whether a block is overridable; it asks for a positive, corroborated reading that a turn is running and nothing else is true:

- the only hard block is `root_agent_working`, and
- the CLI's own screen agrees (`pty_state == "working"`, the "esc to interrupt" affordance).
  Requiring both is what closes the window between an approval dialog appearing and the daemon recording `awaiting`: the screen rules classify an approval prompt as `approval` and a picker or viewer as `uninformative` *before* any working marker is considered, and an unreadable tail is not corroboration either.
- run identity is stable, lifecycle evidence is fresh, the observation channel has spoken, the adapter's etiquette is known, and the boundary is local, and
- nothing has touched the composer since the turn started.
  The ordinary `partial_input_absent` check cannot see this: `input_revision_at_completion` is deliberately `None` mid-turn, because the CLI consumed the line the operator submitted and there is no completion boundary to compare against.
  So the tracker keeps its own `input_revision_at_turn_start`, snapshotted when the phase becomes `working`.
  Queue delivery is itself accounted as operator input, so a mid-turn write already made into this turn also fails this check - deliberately: one splice per running turn, and the boundary resets when the turn does.

Only `send_next` consumes it, and only for an item whose sender asked for it
(`constraints.delivery = "now"`, `auto-delivery.md`, `agent-messaging.md`).
It is not an override and does not become one: the non-overridable protections run before it, and the controller still cannot pass `confirm`.

Every result remains `authorized: false`: the tracker classifies evidence and never grants
authority. Callers act on that classification, all outside this module: the Phase 4
queue's `send_next` (a human act, with an explicit confirm for blocked/unknown), the
Phase 5 auto-delivery controller (which requires `safe` held continuously across a window
and can never confirm), and, since Phase 7.6, the session-control `interrupt` operation. An
interrupt is a PTY write, so it consumes this predicate with the same fail-closed contract:
`safe` proceeds, `blocked` refuses, and `unknown` never authorizes, because interrupting a
session that is mid-approval-prompt or in a menu is corruption, not a stop
(`mux-mcp.md`, `session_control.py`). Unlike `send_next`, the interrupt offers **no** confirm
override — a not-safe interrupt is always refused. See `auto-delivery.md`.

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

Observation liveness and measurement confidence are separate facts.
Transcript growth at or after the latest transcript-backed root hook makes the transcript authoritative for ordered turn boundaries.
A newer hook leaves hooks active until the tailer reports again.
`parser_status == "degraded"` withholds new token, context, cost, and model measurements and the UI labels existing figures stale, but it does not by itself change lifecycle authority or delivery readiness.
Conversely, a parser that still reads `ready` cannot make a silent transcript authoritative.
This split preserves transcript precedence because Claude and Codex hooks are unordered retried side channels, while preventing an old confidence bit from suppressing current lifecycle evidence.

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
`transcript_mtime`.
The first correction was to date live writes from the daemon tailer's size polling rather than from the filesystem and retract a stale claim when the followed file is written again.
The remaining late-bind race was measured on 2026-08-07: the first observer could attach after Codex had already written the whole turn, so every byte was correctly historical, `transcript_growth_ts` stayed zero, and the frozen creation mtime again won.
The observer now also retains the newest valid provider timestamp carried by a record in the followed transcript.
That timestamp preserves its original age across daemon replay, but a catch-up containing `task_complete` within the hook quiet window corroborates the completed turn and retracts an earlier inferred `transcript_stale` claim.
Explicit conversation mismatch claims are not retractable through this path.

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
text — `swemux send`, note send-to-agent, the prompt library's delivery path — was invisible
to these counters and every readiness report emitted `partial_input_absent` /
`operator_quiet` as satisfied for text sitting undelivered in a composer. All operator
input paths now share one accounting helper (`_record_operator_input` in `server.py`): the
HTTP route (`source="http"`, 409 for ended sessions), broadcast per-target
(`source="broadcast"`, `input_owner=False`; ended targets skipped, and so are targets
whose supervisor connection is unreachable, whose bytes would be discarded), voice
(`source="voice"`), and — since Phase 4 — prompt-queue delivery (`source="queue"`,
`input_owner=False`; both the paste and the submit write, see
`features/prompt-queue.md`). Deliberately NOT counted: automation `write_pty`, the branch command
write, and process interrupts — those are not *operator* input, and advancing the
operator-quiet clock for them would mask a different hole. The WS path keeps its own
throttled accounting.

Voice prompt submission is an explicit human send, but it may not override `NON_OVERRIDABLE_REASONS`.
The handler evaluates current readiness and maps stabilized approval/question states to the same protected reason codes before it claims the utterance id or writes bytes.
Approval answering is a separate guarded route and rechecks the current PTY screen immediately before its one Enter write.

Transcript classification records schema version, recognized and unknown counts, bounded
unknown signatures, and a degraded status after sustained drift. Claude discovery follows
the CLI's current project-directory encoding. Codex discovery honors `CODEX_HOME` and rejects
rollouts with `parent_thread_id`; history reconciliation applies the same root-only rule.
Codex `item_completed` records are known non-semantic envelopes: their matching `response_item` records carry the tool and message semantics, so the envelopes affect neither lifecycle nor measurement confidence.

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

**A delivery the CLI never took blocks the next one, by name.** The composer estimate described
below cannot see this and never could: `prompt_queue` writes a body, presses Enter, watches for a turn to open,
and sometimes sees none - the CLI kept the text and typed the carriage return into its composer
(`prompt-queue.md`, the 2026-09-04 incident). The delivery path records that on the session as a
`PendingSubmit`, and it is a hard block here under its own reason,
`unsubmitted_delivery_in_composer`.

Its own reason rather than the `terminal_input_after_completion` it used to surface as, because
that one says *the operator typed something* about bytes mux itself wrote - true in the letter,
since the input revision did move, and misleading in every way an operator could act on. Four
messages queued behind a jam nothing named, while the three deliveries that were sent anyway
pasted on top of the stuck body and the agent received them as one prompt.

It is a positive witnessed fact rather than an estimate, which is what earns it a place in this
contract at all, and it is deliberately **overridable**: the automatic controller never confirms
and so stops, while a human who has looked at the pane may still say the mark is wrong. It clears
only on evidence that the composer emptied - a turn opening, or a non-queue operator write that
submits or discards it - and never on a timer, because an unsubmitted paste does not become
submitted by waiting.

**The unsent-composer estimate is not evidence here.** `composer_input.py` keeps a
finer-grained reading of the same PTY writes — it decrements on backspace, clears on `Esc`, and
treats a bracketed paste as content — and publishes it as `unsent_input` for the session row
(`features/terminal-input.md`). This tracker keeps its own coarse `input_revision` boundary and
never consults it. The two disagree exactly where it matters: an estimate that concluded "the
composer is empty again" would clear `terminal_input_after_completion` and authorize a send into
a composer whose contents nothing here can see, which is the false-safe the whole contract
exists to prevent. A gate may only be relaxed by evidence that cannot be wrong in that
direction.

## Watching readiness, without being able to change it

The classifier pushes nothing, so every surface that displayed a verdict read one at fetch
time and kept it until some *other* fact happened to trigger a refresh.
That left three staleness regimes on one line of UI.
Lifecycle reasons (`root_agent_working`, `awaiting_*`, `session_ended`) ride `state_changed`,
which the browser already treats as a reason to re-read the fleet, so those were live.
Composer and screen reasons (`terminal_input_after_completion`, `operator_recently_typed`,
`unsubmitted_delivery_in_composer`,
`screen_not_at_agent_prompt`) turn on `terminal_input` and `terminal_mode_changed`, both
deliberately excluded from fleet refresh because they arrive at keystroke rate - so those
were stale for up to the browser's sixty-second safety poll.
And the clock-driven transitions have no event at all and never will: `operator_quiet`
becoming true is the *absence* of typing, and `readiness_debounce_pending` and
`lifecycle_evidence_stale` are thresholds crossing.
That third class is why `readiness_watch.py` is a loop rather than another event subscriber.

Four properties make a one-second loop affordable and safe, and each is load-bearing:

- **Edge-triggered.** A tick emits only when a session's `(state, reasons)` tuple differs
  from the last tick's, and a first sighting establishes the baseline silently because the
  client's REST load already carries that verdict.
- **Transient.** The frames are `MuxEvent.transient` (`emit_transient`): fanned out to live
  subscribers, never written to `events`. That table is swept to the newest 100k rows, so a
  per-second event type would not merely cost writes - it would evict the git-provenance,
  scan-timeline and incident-forensics history the window exists to hold. The trade is that
  a transient event carries no sequence number and cannot be resumed after a gap, which is
  acceptable only because a reconnecting client re-reads readiness from REST anyway.
- **Gated on a listener.** The only consumer is a browser, counted by the `/events`
  subscriber label, so a headless daemon skips the whole pass including the screen
  classification that is its real cost.
- **Scoped.** Sessions with an attached terminal (somebody is looking) or a pending queue
  item (somebody is waiting on this exact verdict). Classification measured 2.1 ms on a
  full 32 KiB tail, so following a fleet nobody is reading would spend real event-loop time
  on announcements with no recipient.

**The watcher evaluates with `adopt=False`, and that is a correctness requirement rather
than an optimization.** `evaluate` mutates: `_adopt_catchup_settle` and
`_adopt_first_prompt_ready` fill lifecycle gaps, and the second snapshots the *live* screen
as `screen_at_completion` - the baseline every later `screen_at_agent_prompt` check for that
run compares against. An observer running once a second would therefore make those adoptions
fire at the earliest legal instant rather than at the operator's first GET or send, and a
Claude session watched before it wrote `?1049h` would be remembered as having completed on
the normal screen and blocked for the rest of its run. An observer must not be able to change
the verdict it observes.

It also passes `record_metrics=False`. Those counters are the shadow distribution behind the
Phase 5 promotion argument (`GET /api/automation/injection-safety`); they describe delivery
attempts, and a watch is not one. A loop evaluating every followed session every second would
swamp the proving period within minutes - the same class of mistake `routes/terminal.py`
already carries a warning about.

The scan the loop pays for is not additional. `_pty_state` now *writes* the shared snapshot
cache from every caller and only *reads* it when the caller allows a bounded age, so
`GET /api/sessions` reuses the watcher's classification instead of rescanning every terminal
itself. Only reading is gated, which is the half that carries the safety argument:
authorization never trusts a verdict it did not measure itself.

## Diagnostics

`GET /api/automation/injection-safety` returns the v2 research contract, per-session checks,
bounded evidence, parser coverage, and aggregate shadow counts/reasons/unknown duration.
`GET /api/sessions` includes a compact `delivery_readiness` summary for the session surface,
and `GET /api/queue/messages` carries the same summary for its target.
Both are built by `delivery_summary` in `prompt_queue.py` - one builder, because three
hand-rolled projections of one verdict is how the Queue tab and the send-to-agent dialog come
to disagree about one session. It carries `state`, `reason`, every `reasons` entry, the
`protected` subset that no confirmation can override, `interject_state`, and `observed_at`;
`authorized` is pinned false there as it is at the source.
The Automation Diagnostics view exposes the complete evidence and always states that
actuation is unauthorized.

## Key files

- `src/swe_mux/observation.py`
- `src/swe_mux/delivery_readiness.py`
- `src/swe_mux/readiness_watch.py` (the edge-triggered announcer; read-only against the tracker)
- `src/swe_mux/prompt_queue.py` (`delivery_summary`, `protected_reasons` — the display projection)
- `frontend/src/deliveryReadiness.ts` (the reason vocabulary, worded once)
- `tests/test_readiness_watch.py`, `tests/test_events_ws.py` (transient fanout)
- `frontend/test/deliveryReadiness.test.ts`, `frontend/test/renderer/queue-readiness.spec.ts`
- `src/swe_mux/composer_input.py` (display-only sibling; deliberately not an input here)
- `src/swe_mux/screen_mode.py`
- `src/swe_mux/event_bus.py`
- `tests/test_delivery_readiness_evidence.py`, `tests/test_screen_mode.py`
- `tests/support/detection_replay.py`
- `tests/fixtures/detection/v1/`
- `tests/test_detection_replay.py`
- `tests/test_live_agent_conformance.py`
