# Roadmap v2 - audit remediation

Source: `development/CODE_QUALITY_AUDIT_2026-08-23.md` (finding numbers referenced as F1-F28 throughout).
This roadmap turns every accepted audit finding into a task, grouped into work packages sized for one Claude session each, and sequenced so downstream packages build on upstream ones instead of conflicting.

Execution model:

- Each work package (S1, S2, ...) runs in its own worktree and lands via the land queue or the manual two-command flow.
- Packages inside the same wave touch disjoint files and may run as parallel sessions; waves are strictly ordered.
- Deploy checkpoints (D1-D4) run in the primary checkout only, never a worktree.
- D1 is the only checkpoint that reaps sessions (supervisor bundle update); schedule it deliberately.
- Every package updates the docs its changes touch per `.docs/CLAUDE.md` routing, adds the tests listed, updates existing tests it breaks, and passes `.worktree-verify` before requesting land.

Merge order within a wave matters only where noted; otherwise land in whatever order finishes first.

## Wave 1 - supervisor correctness (S1 parallel with S2, then D1)

The supervisor-side and daemon-side halves are split so that exactly one deliberate session reap ships all supervisor-bundle changes at once.

### S1 - supervisor bundle (supervisor.py, pty_host.py; changes ship only at D1)

Everything here is inside the hash-gated supervisor source closure.
Do not touch files outside the closure except tests.
Design every protocol addition so an older supervisor rejecting it as "unknown message type" degrades gracefully in the daemon; avoid a `PROTOCOL_VERSION` bump unless truly forced.

- [x] S1.1 Spawn idempotency (F2, supervisor half): dedupe spawn by session id (a second spawn for a reserved/live id returns the existing outcome instead of erroring or double-spawning), and add a `spawn_status` query message so a client with a lost reply can learn the true outcome.
- [x] S1.2 Teardown quiesce (F8): set a closing flag before closing the listener, reject new spawns, drain `_background_tasks`, stop any host that completed during shutdown, then close per-session and global reapers. Fixes the orphan-escapes-the-job race.
- [x] S1.3 Frame bounds and auth-first (F13): require a payload-free hello with a small header cap, enforce a non-negative inbound payload limit before `readexactly`, add an authentication deadline. Keep the client-inbound direction generous (legitimate scrollback replies reach 5 MiB); the cap is for daemon-inbound frames.
- [x] S1.4 Exit sentinel reliability (F14): make the end-of-output sentinel enqueue wait like `_put_with_backpressure` does instead of giving up after 2s, so a full queue can never lose the exit signal.
- [x] S1.5 Read-failure diagnostics (F14): rate-limited logging plus a per-session counter for swallowed `PtyError`, surfaced through supervisor meta so a silent-but-alive session is diagnosable.
- [x] S1.6 Shared nested-job helper (F22): extract the duplicated job-object creation sequence into a module inside the supervisor closure and use it from `supervisor.py`; `session.py` adopts it in S2 only if that module is import-safe from the daemon without widening the closure - otherwise leave the daemon copy and add a cross-reference comment.
      Landed as `src/swe_mux/nested_job.py`, which imports only `process_reaper` (already in the closure), so it *is* import-safe from the daemon and adopting it in `session.py` widens nothing. `session.py` was left untouched here because S2 owns that file; the daemon-side adoption is S2's to make.
- [x] S1.T Tests: protocol test that drops the reply after a successful spawn and recovers via `spawn_status`; teardown-drain test; oversized/negative `plen` rejection test; sentinel-under-full-queue test.

Constraint reminder: while this branch is unmerged, `supervisor_bundle_current()` will report stale after landing; nothing rebuilds the bundle until D1, and no session should attempt it.

### S2 - daemon runtime client (supervisor_client.py, session.py spawn/ticker paths)

Runs in parallel with S1.
Where S2 depends on S1's protocol additions (S2.3), code the daemon to degrade gracefully when the running supervisor predates them, so S2 is correct both before and after D1.

- [x] S2.1 Tri-state liveness (F1, the P0): model alive / dead / unreachable instead of collapsing unreachable into dead. `RemotePtyHost.isalive()` must not return false merely because `client.connected` is false while `client.lost` says the supervisor pid is alive.
- [x] S2.2 Ticker gating (F1): the per-session ticker ends a session only on definitive `pty_exit` or confirmed supervisor death; on connection loss it freezes observation, surfaces the unreachable state (health/doctor already read `lost`), and never persists an end.
- [x] S2.3 Spawn fallback hardening (F2, daemon half): on RPC timeout or disconnect during spawn, query `spawn_status` (S1.1) before any in-process fallback; fall back only when failure is known to precede reservation. Against an old supervisor that rejects the query, keep current behavior but log the ambiguity.
- [x] S2.4 Control/data plane decoupling (F3): restructure the client read loop so one session's full output queue cannot block RPC responses or `pty_exit` delivery for other sessions (mirror the supervisor's per-connection drain pattern).
- [x] S2.5 Frame-desync handling (F1): a `ValueError` from one malformed frame must not silently take the whole-connection-lost path without distinct logging; classify and log desync separately.
- [x] S2.6 Discovery kill guard (F7): before `_terminate_supervisor`, validate pid plus the `started_at` the discovery file already records (and executable identity); fail closed when evidence is missing. Replaces the name-contains-"swe" check.
- [x] S2.7 `session.tasks` leak (F9): add the missing discard callbacks on the OSC7 and hook cwd-telemetry task registrations, matching every sibling site.
- [x] S2.T Tests: socket-loss-with-live-supervisor test proving no session is ended (the missing F1 test); ticker-gating test; discovery stale-pid fail-closed test; head-of-line test (one full queue, RPC on another session still answered); task-set growth regression test.

Delivered in `tests/test_supervisor_client_liveness.py`, `tests/test_supervisor_client_transport.py`, and `tests/test_supervisor_client_guards.py`, over a protocol-level `tests/support/fake_supervisor.py` (no ConPTY, so they run on every platform in milliseconds rather than joining the Windows-only real-console group).
Two S2 decisions worth knowing before D1:

- When a spawn RPC fails, the connection is gone, *and* the supervisor process is still alive, the daemon now **fails the spawn** rather than falling back in-process: it cannot ask what happened, and a fallback there is a coin flip on two agents in one workspace. A supervisor that predates `spawn_status` keeps the old fallback with an explicit ambiguity log, so this only bites when the socket dies mid-spawn.
- Output backpressure toward the supervisor is deliberately weakened to keep the control plane free: per-session staging is bounded by that session's scrollback budget and drops oldest-first with a counter and a rate-limited error, instead of stalling the whole connection. D1.5 should watch for that drop line as much as for the unreachable one.

### D1 - deploy checkpoint: supervisor update (primary checkout; REAPS ALL SESSIONS)

Run only after S1 and S2 have both landed on master.

- [x] D1.1 Full gate on master, then confirm `supervisor_bundle_current()` reports stale (expected after S1).
- [x] D1.2 From a terminal outside swe-mux: `uv run muxd --shutdown`, verify no `swe-mux`/`swe-mux-supervisor` processes, `uv run python packaging/build_desktop.py --supervisor-only`, relaunch the app. This is the deliberate reap; follow the CLAUDE.md supervisor-update flow exactly.
- [x] D1.3 Full redeploy of the app bundle (`uv run python packaging/redeploy_desktop.py`) so the daemon half (S2) ships too.
- [x] D1.4 Live soak, isolated daemon first (port 8799 + `~/.mux-hardening` per the isolated-daemon pattern), then the real daemon: spawn/kill/reload cycles; daemon restart with live sessions (sessions must survive); forced supervisor-socket close with the supervisor alive (sessions must NOT be marked ended, UI shows unreachable, restart reattaches); spawn-reply-drop drill via the new `spawn_status`; run the gated live tiers (`live_agent`, `live_subagent`, MCP-wire).
- [x] D1.5 Watch `daemon.log` and `supervisor.log` for the new desync/PtyError/unreachable diagnostics firing spuriously.

Run 2026-08-24 against the rebuilt supervisor bundle (`dist/swe-mux-supervisor`, source hash `94066ae7`) and a full `redeploy_desktop.py` pass.
The fleet held no live sessions at D1.2, so the deliberate reap cost nothing.

What the soak proved, on the isolated daemon (8799) and again on the real one (8765):

- A forced supervisor-socket close with the supervisor alive left every session `running` with no `exit_code` and no end reason for the whole 75s watch, health reported `supervisor_state=lost`, doctor named the supervisor unreachable, and a restart reattached every session at its original pid.
  The socket was dropped by routing the daemon through a frame-aware loopback proxy (the discovery file's port rewritten to it) and killing the proxy, so the supervisor process itself was never touched.
- Dropping exactly the reply frame to a successful `spawn` left the connection open, and the daemon queried `spawn_status`, was told `live`, and adopted the existing session (`process_job_assignment=supervisor_adopted_after_lost_reply`) instead of starting a second process.
  One session, one process, on both daemons: F2 is closed on the live wire, not only in tests.
- The other S2 decision held too: when `spawn_status` could not be delivered and the supervisor was alive, the daemon refused the in-process fallback and failed the spawn, creating nothing.
- D1.5 is clean. Across both daemons and both supervisors the new counters stayed at zero: no frame desync, no swallowed `PtyError`, and no output-drop/backpressure line.
  Every `unreachable` and `connection lost` entry maps to a drill that deliberately caused it, and the per-session unreachable warning repeated on its intended 60s cadence rather than per tick.

One defect found, and it needs no second reap because it lives in `server.py`, outside the supervisor closure:

- `daemon_restart` still gates on `supervisor.connected` alone, which is exactly the binary collapse S2.1 replaced everywhere else.
  While the supervisor is unreachable-but-alive it answers `409 supervisor_not_attached`, so the recovery that `supervisor_client` logs ("restart the daemon to reattach") and that `doctor` recommends is refused through both `POST /api/daemon/restart` and `mux reload-daemon`.
  Worse, the escape it advertises makes things worse: with `force=true` the same `attached` flag is still false, so the shutdown intent becomes `quit` and `reap_all_and_exit` destroys the very sessions that were still alive and adoptable.
  The fix is to treat a live supervisor as attachable (`connected or lost`) for both the gate and the intent; reattach itself already works, and was verified by restarting out of band.
- Smaller, same area: the refused-spawn path surfaces as a bare `500 {"error": "internal server error"}` because the `TimeoutError` reaches aiohttp's generic handler, so the operator never sees the reason the daemon logged.

Three live-tier failures, all pre-existing and none on a supervisor code path (each asserts on a direct `subprocess.run`, not on a PTY):

- `test_request_land_enqueues_the_callers_own_worktree` (all four harnesses) dies in 1s with `TypeError: _spawn_agent() got an unexpected keyword argument 'cwd'`.
  The call was added by ef9ccb9 on 2026-08-20 and the helper never took that argument, so this test has never once executed and `mux.request_land` has no live-wire coverage.
- The `opencode` store canary fails intermittently (twice in-file, passing five times standalone against the same isolated `XDG_DATA_HOME`); opencode rotated models between runs, and `_run` sends the CLI's stdout and stderr to `DEVNULL`, so the tier can never say why it failed.
- The `codex` subagent canary now fails consistently: the transcript carries `tool_use`/`tool_result` but no `subagent_activity`, which is provider drift the canary exists to catch.

## Wave 2 - structure and stores (S3, S4, S5, S6 in parallel, then D2)

These four packages touch disjoint files.
S3 and S4 are the conflict hubs of the codebase; landing them before waves 3-4 is what lets later packages avoid rebasing through 16k-line files.

### S3 - server.py decomposition (behavior-preserving)

Pure moves plus the AppKey migration; no behavior changes, so review is structural.

- [x] S3.1 Feature route extraction (F/codex-8): move route tables and thin handlers into per-domain route modules registered from the composition root; keep `_build_runtime_handles()` as the intentional root.
- [x] S3.2 Worktree/git mutation service: move the multi-stage repair/burial/rollback/quarantine/purge transaction out of the transport module into a service returning typed outcomes.
- [x] S3.3 Preview transport module: move Preview rewriting/proxy helpers into their own module (F21's future tree-sitter work then has a home).
- [x] S3.4 AppKey migration (F28): replace the ~632 string app-keys with typed `web.AppKey` handles; this also eliminates the bulk of the test-run warning noise and is the prerequisite for the warnings ratchet in S12.
- [x] S3.T Tests: existing suite is the harness (moves must not change behavior); add an import-boundary check so feature modules do not import the composition root.
- [x] S3.D Docs: update `technical/backend/packages.md` module map for every new module.

### S4 - App.tsx decomposition plus the refresh fix

- [x] S4.1 Fleet-refresh controller (F4): extract refresh into a controller module; give all five GETs a default `timeoutMs`, switch to `Promise.allSettled` with per-slice application, and make the in-flight dedupe abortable/resettable so a hung request can never pin future refreshes.
- [x] S4.2 Stale-await fix (F/frontend-new-3): a mutation that awaits refresh must not be handed a pre-mutation in-flight promise; return the queued follow-up instead.
- [x] S4.3 Command registry (F/codex, 5s clock): memoize the registry on its real inputs and gate `searchCommands` on the palette being open.
- [x] S4.4 Clock subtree isolation: move `useRowClock` consumers into memoized subtrees so the 5s tick re-renders the sidebar rows, not the shell.
- [x] S4.5 Controller extraction for the largest remaining App.tsx state clusters (layouts, overlays, gestures) to the extent it stays behavior-preserving; do not chase a line-count target.
- [x] S4.T Tests: unit tests for the refresh controller (hung request recovery, partial failure application, dedupe reset); renderer spec for palette gating; update any source-text tests that asserted on moved App.tsx code.
- [x] S4.D Docs: `technical/frontend/packages.md`.

Five modules came out of `App.tsx`: `fleetRefresh.ts` (deadlines, `allSettled`, the abandonable dedupe),
`fleetLayouts.ts` (the pure layout/join reconciliation the refresh cycle runs), `layoutWriter.ts` (the
optimistic write chain, generation guard and revisions), `fleetCommands.ts` (the fleet-derived half of the
command registry), and `SessionRowLive.tsx` (the sidebar row, with the ageing clock inside it).

Three decisions worth knowing:

- **S4.3 is a deliberate half-memo.** The fleet-derived commands are memoized on what determines them,
  with `run` handlers routed through a ref-backed facade so a memoized command cannot act on a stale
  snapshot. The ~120 hand-written commands stay inline: their `available` and `label` expressions read
  dozens of live UI values, and memoizing them correctly would mean passing every one as data - where a
  missed input is a silently disabled or mislabelled command, a worse failure than the allocation it
  saves. `searchCommands`, the expensive half, is gated on the palette instead.
- **S4.4 needed the clock to become shareable, not per-row.** `useRowClock` now subscribes to one
  module-scoped interval, so moving it below the shell did not trade "one timer for the whole sidebar"
  for "one timer per row". `deriveRowContext` split into `deriveRowFleetFacts` plus `now`, and the type
  makes it impossible to hand the clock-free facts where a full context is wanted.
- **One behaviour change beyond the specified fixes**: `registryLoaded` (which gates pruning sidebar
  fold state against the Group registry) is now set when the Groups read succeeds rather than when all
  five do. Under `allSettled` the old rule would have left it false through any partial cycle, and the
  flag is about the Group registry alone.

### S5 - store layer (automation_store, prompt_queue, voice, land_store; no S3/S4 overlap)

- [x] S5.1 Scan search (F6): pass `newest_first=True` in both search callers, add a truncation flag to the response, add an index for the unindexed `ORDER BY t0`, and propagate the discarded `_memory_scope` truncation flag.
- [x] S5.2 Retention batching (F12): per-table operations deleting bounded rowid batches with commits between batches, plus measured `created_at` indexes on the tables that need them; log rows-removed and elapsed per table.
- [x] S5.3 Voice eviction (F19): replace the unindexed `COALESCE` group scans with `WHERE stream_id=? OR id=?`, batch victim deletion.
- [x] S5.4 Prompt queue append (F18): skip renumbering on ordinary tail appends; renumber only on anchor inserts. Correct the schema comment that claims the partial unique index enforces NULL-sender dedup.
- [x] S5.5 LIKE escaping (F23): route `target_fragment` and the history metadata fallback through the existing `_escape_like`/`ESCAPE` pattern.
- [x] S5.6 Land audit atomicity (F/codex, downgraded): add `transition_with_event` / `enqueue_with_event` so a state transition and its audit row commit in one operation; fix the two write-event-first call sites.
- [x] S5.T Tests: >2000-record scan-search regression (newest record found, truncation reported); retention batching test proving intermediate commits; LIKE metacharacter test; tail-append write-count test; crash-between-transition-and-event test made impossible by construction.

Delivered in `tests/test_store_hardening.py` (21 tests), plus doc updates in
`technical/backend/sqlite.md`, `design/features/{scan-timeline,land-queue,voice,prompt-queue}.md`,
`design/{interfaces,data-model}.md`, and `design/features/mux-mcp.md`.
Four decisions worth knowing before the next package touches these stores:

- **S5.1 is its own read, not a flag on `scan_records`.** Search wants the opposite of what the
  derivations want: `scan_consumers` walks a run forwards and needs every record from the
  beginning, while search ranks whatever it is handed and re-sorts newest-first. So
  `AutomationStore.scan_search_page` is a separate newest-first, truncation-reporting read and
  `scan_records` keeps its oldest-first contract unchanged. The truncation flag reaches both
  surfaces (`records_truncated` on the tool, `truncated`/`scanned` on the endpoint), and the tool
  also stopped discarding `_memory_scope`'s scope-truncation flag by adopting the existing
  `_covered_projects` envelope.
- **S5.2's index list is measured and short.** Live 2.8 GB `mux.db`: the largest prune table is
  19,309 rows (already indexed) and everything except four is at or below 1,100. At those sizes an
  extra B-tree per insert loses to what it saves, so only `automation_budget_ledger`,
  `automation_annotations`, `scan_timeline_records`, and `automation_checkpoints(updated_at)`
  gained one. The batch statement carries **no `ORDER BY`**: ordering by the retention column
  forces a temp B-tree per batch on an unindexed table (1563ms against 263ms per 100,000 rows),
  while omitting it is within 20% of the best indexed plan and has no bad case. `prune` now
  returns rows-removed per table and logs per-table and per-sweep lines.
- **S5.6 is an optional `event: LandEvent` argument on `transition`/`enqueue`, not two new
  methods.** `transition` takes nineteen keyword arguments; a `transition_with_event` wrapper is
  either a twenty-line forwarder that silently drifts or an `Any`-typed `**kwargs` that type-checks
  nothing. The argument delivers the same guarantee with no duplication, and the event's
  `project_id` comes off the updated row so it cannot name a different Project. Three call sites
  wrote the event first, not two (`_skip_verification`, `_reuse_verification`, `_standing_verdict`);
  all three now pass through `_clear_gate`, and `LandStore.restore` writes its `orphaned` entries
  in the same commit as the requeue.
- **The LIKE helpers moved to `sqlite_store`** (`escape_like`, `like_contains`) rather than being
  copied a third time; `history`'s `_escape_like`/`_like_pattern` are aliases. Four call sites were
  unescaped, not two: the scan `target_fragment`, the experience browse, and both history metadata
  filters. `code_graph.definitions`' `name LIKE ?` (`f"%.{name}"`) has the same defect and was left
  alone as out of scope for S5.

### S6 - session.py and observation follow-ups (after wave 1 lands; conflicts with S2 otherwise)

- [x] S6.1 Attach replay off-loop (F10): move the whole-file read and per-line JSON decode of transcript attach into a thread with chunked yielding, so a multi-ten-MB transcript cannot stall the event loop; bound peak memory to a chunk, not the file.
- [x] S6.2 Poll-path cheapening (F10): stop re-opening the file for the 64-byte prefix probe on every 250ms poll where a cheaper identity check suffices.
- [x] S6.T Tests: attach a large synthetic transcript and assert loop responsiveness (use the `until` settle helpers, no fixed sleeps); rewrite-detection regression.

Done 2026-08-24, entirely inside `observation.py`; the tailer's call sites in `session.py` needed no change.

Measured on the primary host with a synthetic Claude transcript, worst event-loop gap across one attach replay:

| transcript | before | after |
| --- | --- | --- |
| 24 MiB (40,329 records) | 290 ms, loop serviced once | 11-16 ms, serviced ~10,000 times |
| 48 MiB (80,659 records) | 691 ms, loop serviced once | 9-17 ms, serviced ~19,000 times |

Replay wall time did not regress (it improved slightly: 691 ms to ~460-650 ms at 48 MiB), so the loop time is given back rather than moved.

Two things are worth carrying forward.
The replay boundary is unchanged by construction: a record's historical/live label is still its decoded byte position against the attach snapshot, and a test drives five-byte windows so a boundary falls inside every record and asserts the emitted sequence is byte-for-byte the one an unwindowed read produced.
And S6.2 could not be a pure `stat()` check - Windows freezes `st_mtime` on a file its writer holds open, so a same-length in-place rewrite is entitled to leave every readable field unchanged.
The identity check is therefore one-directional (a field moving proves change; nothing staying still proves the absence of it) and a 2 s prefix backstop closes the case, taking an idle session from four opens a second to at most one every two seconds.

### W2.5 - live-tier repair (added from the D1 soak findings; parallel with S3-S6, lands before D2)

The three live-tier failures D1 recorded are pre-existing and none touch the supervisor; their files are disjoint from S3-S6, so this runs alongside Wave 2.

- [x] W2.5.1 `request_land` live coverage: fix the `_spawn_agent() cwd` TypeError (introduced ef9ccb9) so `test_request_land_enqueues_the_callers_own_worktree` executes at all, then run it on the live wire for every harness - it has never once run, so treat what it finds as a fresh result, not a regression.
- [x] W2.5.2 opencode canary diagnosability: stop sending the CLI's stdout/stderr to `DEVNULL` in `_run`; capture bounded output into the failure message, then diagnose the intermittency from actual evidence.
- [x] W2.5.3 codex subagent drift: the canary consistently finds `tool_use`/`tool_result` but no `subagent_activity` - investigate whether current codex stopped emitting those records, and if so adapt swe-mux's subagent-visibility detection to the new transcript shape and update the canary to match. This is potentially a live product defect, not a test fix; report the investigation outcome either way.

Done 2026-08-24. All three tiers are green on the live wire, and the two questions the package was really asking - "is the canary broken or is mux broken?" - both answered "mux", in different places.

- W2.5.1: the helper now takes the `cwd` its caller always passed, and the canary ran for the first time on all four control harnesses. It found two things. The scratch worktree it built was level with the trunk, so the service correctly refused it as having nothing to land - a fixture gap, now given the branch a commit of its own. And that refusal reached the agent as `500 {"error": "internal server error"}`: `LandRefusal` escaped `_enqueue_land` untranslated while both HTTP land routes already answered a typed 409. Every land-queue refusal an agent could hit - already landed, already queued, budget exhausted, detached HEAD, unapproved gate - was opaque on the MCP wire. Translated in `mcp.py` (a one-hunk `except LandRefusal` to `QueueError`), with a default-tier test per tool and the wire canary now asserting the typed code.
- W2.5.2: `_run` captures both streams and puts a bounded prefix and tail into the failure message; the first captured red run named the cause in one line - opencode's provider relay answering `Upstream request failed: Endpoint is unavailable` for the model it had rotated to. Not a mux fact, so that narrowly-matched failure is retried once (each attempt into its own store, so a retry cannot measure the corpse of the attempt before it) and then skipped with the evidence. Every other CLI failure stays red, and a default-tier test pins the classifier. 5/5 green afterwards against 1/4 red before.
- W2.5.3: **a live product defect, not canary drift.** Codex still emits the subagent signal; it moved it. Through 2026-08-06 it wrote a top-level `sub_agent_activity` payload, and from 2026-08-07 (0.149) it nests the identical `kind`/`agent_thread_id`/`agent_path` fields inside `item_completed`'s `item` as `SubAgentActivity` - measured across the operator's 1548 archived rollouts, the two eras do not overlap by a single file. The observer read only the older envelope, so **every Codex pane running subagents carried no standing `subagents` annotation for 17 days**, and everything gated on it (auto-delivery, delivery readiness, the idle-with-children rule) read the pane as having nothing running. Both envelopes are now read. Two adjacent findings came out of the same measurement: `agent_path` is a slash-joined string, so the emitted `depth` had been a character count; and `item_completed` was in `observation.py`'s known vocabulary but not `operational_telemetry.py`'s, which put real sessions at a 0.31-0.34 unknown ratio against the 0.25 the telemetry canary fires at - a drift signal reporting drift that had not happened.

### D2 - deploy checkpoint (primary; no reap)

Precondition: W2.5 landed, so the live tiers D2.2 exercises are trustworthy.

- [x] D2.1 Full gate on master, `redeploy_desktop.py` (normal session-preserving flow).
- [x] D2.2 Live soak: UI regression pass on desktop and mobile (fleet refresh under a simulated hung endpoint, palette, sidebar tick); MCP `scan_search` against a >2000-record project; a land-queue cycle end-to-end; confirm retention runs without visible stalls.

Run 2026-08-24 in the primary checkout against the real daemon on 8765, with 28 live sessions resident throughout.
No reap: the supervisor bundle was already current (`supervisor_bundle_current()` is `True`, source hash `94066ae7`, the same one D1 built), and supervisor pid 89372 was never touched.

The gate is green on master: 4756 passed / 16 skipped in 45.2s, ruff clean, mypy clean over 213 files, `tsc --noEmit` clean for both `src` and the renderer harnesses, and 2013 frontend tests passing.
No frontend dependency landed this wave (the last `package.json` change is `f710c14`, 2026-08-22, already installed), so the `npm ci` trap did not apply.
Only 7 pytest warnings remain, all the `@pytest.mark.asyncio` misuse in `test_assistant.py`: S3.4 did remove the app-key warning bulk, and the residue is now small enough to be S12.4's actual scope rather than its excuse.

**The redeploy shipped, and the asset hash proves it rather than merely suggesting it.**
Before: the live daemon, `src/swe_mux/static`, and the frozen bundle all served `index-D6ReKD38.css`, and 21 files under `frontend/src` were newer than that build - the S4 decomposition was on disk and in nobody's browser.
After: all three serve `index-DQBLUWjn.css`.
The running process is the frozen app (`dist/swe-mux/swe-mux.exe`), which is why a plain `npm run build` would have shipped nothing.

What the soak proved:

- **Fleet refresh under a failing and a hung slice, on the real UI.**
  Driving the shipped frozen-app page with Playwright and intercepting one of the five slice GETs: a `/api/previews` that answers 500 leaves all 28 sidebar rows painted and raises one toast, the next clean cycle clears the toast, and a `/api/projects` that never settles leaves the fleet painted with a 6.9 ms rAF while it hangs and does not pin the refresh that follows it.
  That is F4's freeze reproduced as a deliberate condition and found absent.
  The palette opens and searches (9 hits for "settings"), and the 5 s clock still advances a row's age with the row below the shell.
  `fleetRefresh.test.ts`'s 15 unit tests cover the same controller from the other side and all pass in the gate.
- **`scan_search` against a 2554-record Project.**
  The newest record in the database came back first (`c900b05c`, t1 16:46 the same afternoon), and the response carried `records_truncated: true` with a note naming the 2000-record window and the Project.
  Both halves of S5.1 are live: before it, a search of this Project could not reach today's work at all.
- **Two land-queue cycles end to end, on the redeployed daemon, driven by other agents rather than by this checkpoint.**
  `lnd_f32db10b` (`worktree-untrack-generated-docs`, another Project) reconciled, ran the full gate (`verify_gate=full`, `verify_attempts=1`) and fast-forwarded trunk `28482dce` to `95cd8cfe` in 82 s at 16:53; `lnd_46d20655` (`worktree-wave-b-liveness`) landed in 39 s at 16:57.
  The W2.5-repaired live wire agrees: `live_mcp` is 24/24 green in 156 s, which is the first time `test_request_land_enqueues_the_callers_own_worktree` has run inside a deploy checkpoint.
  `live_agent` plus `live_subagent` are 8/8: observer conformance for claude, codex, omp and pi, the opencode store canary that W2.5.2 made diagnosable, and the three subagent canaries including the codex one whose envelope drift W2.5.3 fixed.
- **Retention is fast and its per-table lines are real.**
  The live hourly sweep logged `automation_retention_swept tables=14 rows_removed=0 elapsed_ms=3.9` - nothing in this database is 90 days old yet, so the live pass proves the absence of a stall but cannot show the batching.
  Driving the same `prune` over a consistent 2.84 GB backup of the live `mux.db` with a 1-day window does: 33,657 rows across 10 tables in 517 ms total, per-table lines carrying rows-removed, batch count and elapsed (`automation_observer_calls` 17,034 rows in 35 batches / 330 ms is the largest), and no table anywhere near the 400-batch cap that would have logged `automation_retention_capped`.
- **A session-preserving restart on the new build, which also answered why the first start was slow.**
  The redeploy's daemon took 90.5 s to be ready, against ~13 s for the daemon it replaced, almost all of it in `database-integrity` (60.9 s).
  A `POST /api/daemon/restart` afterwards was ready in 52 s with `database-integrity` back to 12.0 s, so that 60.9 s was the one-time cost of S5's new indexes over a 2.7 GB database and not a standing regression.
  Every session survived both transitions (28, then 29 as another agent spawned one mid-soak), the supervisor stayed `connected`, and no session was ever marked ended.
- **D2's log watch is clean.**
  Across 962 post-redeploy requests the only 4xx/5xx were this checkpoint's own probes (four `/api/git/*` 404s from calls that sent no `project_id`, and the redeploy's health poll 503s during startup).
  A registration sweep over the GET routes the S3 modules declare answers 200 on 79 of 89 and 82 with parameters supplied; the rest correctly demand a query parameter.
  No `ERROR` or traceback after either daemon became ready, no AppKey fallout, and the S2 counters stayed at zero: no frame desync, no swallowed `PtyError`, no output-drop or backpressure line, and not one `unreachable`.

Three defects, none of them Wave 2's and none blocking Wave 3:

- **A session-preserving restart loses the dying daemon's last writes.**
  While the successor holds the database and the predecessor is still flushing, `operational_telemetry`, `session_recovery`, `push` and `history` each raised `sqlite3.OperationalError: database is locked` and dropped what they were writing.
  Pre-existing (seven of the ten occurrences in the log are dated 2026-08-23, before this wave landed), bounded to the overlap window, and invisible to the operator - which is the part worth fixing, since the lost rows are exactly the telemetry that would explain a restart.
- **`worktree_graveyard_purge_failed` retries a path that no longer exists, forever.**
  1,165 warnings since 2026-08-21 over a handful of buried worktrees, each one a `FileNotFoundError` for a directory that is already gone, up to 24 repeats of a single path.
  Quiet since the redeploy, so this is a report rather than an active fire, but a purge that treats "already absent" as a failure will start again the next time one is buried.
- **"previous daemon died without a clean shutdown" fires on every planned restart**, 39 times in this log, because `redeploy_desktop.py` and the restart path terminate the predecessor after asking it to detach.
  A crash warning that is right 0% of the time is worse than no warning.

Also noted, not defects of this wave: one scan-timeline record failed on `OpenRouter structured response must be an object` (the v4-flash behavior-repair path, and it repaired), and the `session_claimed_infrastructure` ownership diagnostic fired once for the relaunched daemon, as it does for every redeploy run from inside a session.

Left for the operator, because they need hands on a phone or an eye on a real screen: the mobile pass (pull-to-refresh, palette, sidebar tick cadence, rail drag), a terminal-attach and scrollback check on a warm pane, and voice/assistant round-trip.

## Wave 3 - cross-cutting quality (S7-S10 in parallel, then D3)

All four build on S3/S4 structure; conflicts between them are minimal because S3 moved the surfaces apart.

### S7 - diagnosability (logsetup, middleware; F5, F25)

- [ ] S7.1 Structured sink: JSON (or key=value) formatter that serializes `extra` fields, so the correlation data call sites already write reaches `daemon.log`.
- [ ] S7.2 Request correlation: contextvar request-ID middleware, ID returned in a response header and included in every log line; carry into subprocess/service logs where operation IDs already exist.
- [ ] S7.3 Typed error translation (F5): introduce a `NotFound(KeyError)` (or typed domain exceptions) for the 30+ deliberate raise sites; let bare `TypeError` reach the 500 path; log both translation paths at debug with method, path, and request ID; stop echoing raw key reprs in 404 bodies.
- [ ] S7.4 Restart-overlap durability (D2 finding 1): during a session-preserving restart the dying daemon's last writes hit `sqlite3.OperationalError: database is locked` (operational_telemetry, session_recovery, push, history) and are silently lost - precisely the telemetry that would explain the restart. Flush/close stores before the handoff or retry with bounded backoff during the shutdown drain, and make any final loss loud instead of silent.
- [ ] S7.5 Planned-restart lifecycle truth (D2 finding 3): "previous daemon died without a clean shutdown" fires on every planned restart because the redeploy terminates the predecessor after asking it to detach. A planned handoff must record itself so the successor stops reporting a crash that did not happen.
- [ ] S7.T Tests: formatter round-trip of extras; middleware test proving an accidental `KeyError` from a handler bug 500s with a traceback while a deliberate `NotFound` 404s; request-ID presence test; restart-overlap write-loss test; planned-restart-no-crash-warning test.
- [ ] S7.D Docs: logging section of the relevant technical doc.

### S8 - subprocess and process consolidation (usage, provider_accounts, git_monitor, harness, agent_environment, processes)

- [ ] S8.1 Shared bounded runner (F/codex-G): extract the `worktree_exec` pattern (chunked bounded read, truncation reporting, timeout and `CancelledError` process-tree reap, correlation) into one helper; migrate `usage.py`, `provider_accounts.py`, `git_monitor.py`.
- [ ] S8.2 `probe_cli_version` unification (F22): one implementation (keep the shim-recursion-safe `which_real` behavior), one cache policy, both call sites.
- [ ] S8.3 `snapshot_all` grouping (F20): build the `session_id -> processes` index in one pass and serialize each process once.
- [ ] S8.4 Graveyard purge retry cap (D2 finding 2): `worktree_graveyard_purge_failed` retries already-absent paths forever (1,165 warnings since 2026-08-21, up to 24 repeats per path). Treat an absent path as purged, and bound retries for paths that persistently fail with a terminal log line.
- [ ] S8.T Tests: runner cancellation-reap test; output-cap truncation test; version-probe cache test; snapshot projection equivalence test; absent-path purge idempotency test.

### S9 - MCP and automation consumers (mcp.py, deterministic_consumers.py)

- [ ] S9.1 `handle_rpc` narrowing (F24): catch `ScopeMiss`/`AmbiguousIdentity`, not base `KeyError`, so handler bugs error instead of reading as "no such session".
- [ ] S9.2 Test-file classifier (F26): explicit path-segment and basename conventions (`tests/`, `test_*.py`, `*_test.py`, `*.test.ts`, `*.spec.tsx`, `__tests__/`); false-positive tests for `latest.py`-class names.
- [ ] S9.3 Blast-radius honesty (F/codex): failed provenance reads return `co_change_available: false` with a typed reason and a log line, not a silent empty list.
- [ ] S9.4 Parse-timeout guard (F24): stop advising retry while the abandoned parse still occupies an executor slot; single-flight per transcript with the timeout attached to the flight, not the wait.
- [ ] S9.5 Lock-map eviction (F24): evict per-key locks (scan_timeline, assistant dialogs, project_card) when their keyed entity ends.
- [ ] S9.6 `list_sessions` size fitting (F24): per-item size accounting instead of full re-serialization per popped item.
- [ ] S9.7 Doc-ownership cache (F/codex-K, F22): fingerprint paths+count+mtime_ns+size instead of max-mtime, and make the uncached `mcp.py` caller share the cached builder.
- [ ] S9.T Tests for each of the seven items.

### S10 - frontend quality (Settings, CodeEditor, bundle; server half lands in the S3-created route modules)

- [ ] S10.1 Atomic settings save (F11): one server endpoint transacting config and keybindings together; client sends one request; the failure message can no longer claim "nothing was changed" when half committed.
- [ ] S10.2 Restore defaults (F11): confirmation dialog on the destructive action, error handling with a visible failure status.
- [ ] S10.3 Post-save chain dedup (F22): one shared apply-config function for the save/reset/load paths.
- [ ] S10.4 CodeEditor (F/codex): last-emitted-string ref to skip the second full-document serialization and compare.
- [ ] S10.5 Bundle splitting (F17): lazy-load the resource editor (with on-demand grammar loading) and the change map (Sigma/Graphology); record the before/after main-asset size in the PR description.
- [ ] S10.T Tests: settings-atomicity test (server rejects half-commits); renderer specs for the confirm dialog and lazy routes; bundle-size assertion if the build exposes one cheaply.

### D3 - deploy checkpoint (primary; no reap)

- [ ] D3.1 Full gate, redeploy, full Playwright renderer suite on a free port.
- [ ] D3.2 Live soak: settings save/reset from two devices (revision conflict path); a full agent session lifecycle checking the new request-IDs correlate across `daemon.log` and `access.log`; MCP tool sweep against live sessions; editor and change-map lazy loads on desktop and mobile.

## Wave 4 - hygiene, dependencies, gates (S11, S12 in parallel, then D4)

### S11 - dependencies and hygiene (rewritten 2026-08-24 against the locked Phase 10.5 licensing posture)

Phase 10.5 landed as this roadmap started and supersedes the audit's licensing findings: Apache-2.0 plus DCO, and a two-half license gate (`packaging/license_audit.py --check` over the resolved closure in verification, `build_desktop.verify_bundle_licenses` over the built tree), with LGPL requiring an `ALLOWLIST` entry AND replaceable-source shipping under `_internal/<pkg>/` - which pystray and num2words both already satisfy via the spec's `collect_all` loop.
The original S11.1 (audit F15) assumed a pystray-only allowlist and is closed; do NOT swap num2words for `misaki[en]` - the closure resolves it either way, the compliance mechanism is identical, and the explicit declaration documents the runtime requirement the frozen build's `collect_all` depends on.
Any task here that changes a dependency must run the mandated flow: `uv sync --extra desktop`, then `uv run python packaging/license_audit.py --write`, and commit both generated files (`THIRD-PARTY-NOTICES.md`, `packaging/third_party_licenses.json`).

- [ ] S11.1 num2words posture verification (F15, superseded by Phase 10.5): no dependency change. Confirm the allowlist entry, notice text, and replaceable-source shipping are green through both gate halves, and add a pyproject comment on the num2words line saying why it is a direct dep (misaki's English G2P at runtime; frozen build `collect_all`s it as replaceable LGPL source).
- [ ] S11.2 voice-local extra (F16): move the voice/NLP closure (spacy, misaki, onnxruntime, faster-whisper, en-core-web-sm, num2words chain) behind an extra. Three gate interactions are load-bearing: `license_audit.py`'s closure walk must be defined over the union of extras (or the desktop build's extra set), not whatever happens to be synced; the frozen build REQUIRES the extra present (num2words' replaceable-source `collect_all` is license compliance, not just voice function), so `redeploy_desktop.py` preflight must assert it before building; and the frozen-app round-trip verify must stay green.
- [ ] S11.3 Vendor cleanup (F28): remove the 13 unreferenced `frontend/vendor/continuity-editor-*.tgz`.
- [ ] S11.4 Line endings (F28): `.gitattributes` entries (`.worktree-verify`/`.worktree-setup` `text eol=lf`) and renormalize. Check first whether this already landed - an uncommitted `.gitattributes` change existed in the primary on 2026-08-24.
- [ ] S11.5 `git_provenance_backfill.py` (F28): move to a tools/ location or document it as a one-shot migration so it stops reading as dead code.
- [ ] S11.T Verification: fresh `uv sync` matrix (base, `--extra voice-local`, desktop) each building and starting; both halves of the Phase 10.5 license gate green; frozen-app round-trip verify; generated notice files regenerated and committed for any dependency change.

### S12 - test infrastructure and ratchets

- [ ] S12.1 Frontend unit-test typechecking (F28): a tsconfig that covers the 184 untypechecked files, fixing fallout; wire it into `.worktree-verify` next to `check:renderer`.
- [ ] S12.2 Source-text test migration (F28): convert the worst of the 37 readFileSync-regex test files to behavior tests (controller units or renderer specs); leave a documented allowlist for the rest.
- [ ] S12.3 Complexity ratchet (F27): enable C901 with the threshold set at today's maximum and a recorded plan to step it down; first targeted reduction is table-driving `Config._validate`'s range checks (no Pydantic).
- [ ] S12.4 Warnings gate (F28): with AppKey landed (S3.4), fix residual warnings and make new warnings fail CI.
- [ ] S12.5 Preview JS rewriting (F21, optional): move the lexical rewrite to tree-sitter-based specifier rewriting in the S3.3 Preview module, with negative fixtures for strings, comments, templates, and data blocks. Defer if wave capacity is short; the practical exposure is narrow.
- [ ] S12.T The gates themselves are the tests; CI must be green with both ratchets on.

### D4 - final checkpoint (primary; no reap)

- [ ] D4.1 Full gate with ratchets on, redeploy, confirm the voice extra preflight fires when deliberately unset.
- [ ] D4.2 Extended live soak across a normal working day of real sessions; review `daemon.log` for new-diagnostic noise; close out this roadmap by archiving it with a completion note per docs governance.

## Dependency summary

- S1 and S2 are parallel; both precede D1; D1 precedes everything that assumes the new supervisor protocol at runtime.
- S3, S4, S5 are parallel; S6 needs wave 1 landed (session.py overlap with S2).
- S7-S10 need S3 (server.py structure) and S4 (App.tsx structure) landed; they are mutually parallel.
- S11 and S12 are parallel; S12.4 needs S3.4; S12.5 needs S3.3.
- Findings deliberately not scheduled: /events per-client serialization and awaited-refresh staleness beyond S4.2 (both noise-level; fix opportunistically when touching those files).
