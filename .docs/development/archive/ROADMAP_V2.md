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
- [x] D1.2 From a terminal outside swe-mux: `uv run swemuxd --shutdown`, verify no `swe-mux`/`swe-mux-supervisor` processes, `uv run python packaging/build_desktop.py --supervisor-only`, relaunch the app. This is the deliberate reap; follow the CLAUDE.md supervisor-update flow exactly.
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
  While the supervisor is unreachable-but-alive it answers `409 supervisor_not_attached`, so the recovery that `supervisor_client` logs ("restart the daemon to reattach") and that `doctor` recommends is refused through both `POST /api/daemon/restart` and `swemux reload-daemon`.
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

- [x] S7.1 Structured sink: JSON (or key=value) formatter that serializes `extra` fields, so the correlation data call sites already write reaches `daemon.log`.
- [x] S7.2 Request correlation: contextvar request-ID middleware, ID returned in a response header and included in every log line; carry into subprocess/service logs where operation IDs already exist.
- [x] S7.3 Typed error translation (F5): introduce a `NotFound(KeyError)` (or typed domain exceptions) for the 30+ deliberate raise sites; let bare `TypeError` reach the 500 path; log both translation paths at debug with method, path, and request ID; stop echoing raw key reprs in 404 bodies.
- [x] S7.4 Restart-overlap durability (D2 finding 1): during a session-preserving restart the dying daemon's last writes hit `sqlite3.OperationalError: database is locked` (operational_telemetry, session_recovery, push, history) and are silently lost - precisely the telemetry that would explain the restart. Flush/close stores before the handoff or retry with bounded backoff during the shutdown drain, and make any final loss loud instead of silent.
- [x] S7.5 Planned-restart lifecycle truth (D2 finding 3): "previous daemon died without a clean shutdown" fires on every planned restart because the redeploy terminates the predecessor after asking it to detach. A planned handoff must record itself so the successor stops reporting a crash that did not happen.
- [x] S7.T Tests: formatter round-trip of extras; middleware test proving an accidental `KeyError` from a handler bug 500s with a traceback while a deliberate `NotFound` 404s; request-ID presence test; restart-overlap write-loss test; planned-restart-no-crash-warning test.
- [x] S7.D Docs: logging section of the relevant technical doc.

Five decisions worth knowing before D3, and one thing S7 deliberately did not do.

- **The sink is `key=value`, not JSON.** The daemon already logs `git_mutation_started operation_id=… cwd=…` by hand in dozens of places, so appending `extra` in the same shape makes one convention instead of two and keeps `daemon.log` greppable; a JSON-object-per-line sink would have made every existing message a string field inside it. Values that hold a space, a quote, an `=` or a newline are JSON-quoted, so a line still parses back into its fields and always stays one line.
- **The correlation filter is on the handlers, not on the root logger**, and that is not a style choice. `Logger.handle` consults only the filters of the logger the call was made on, so a filter installed on root would have looked armed and stamped nothing that `swe_mux.session` logs. The first version of this had that bug and every unit test passed anyway; `test_the_configured_daemon_writes_fields_and_ids_into_daemon_log` is what catches it, because it reads the real file.
- **`NotFound` subclasses `KeyError`** so only the *raise* sites had to move (36 of them, plus `configurator.py`'s catalog-carrying one). The catch sites - `routes/diagnostics.py`'s post-mortem fallback, `routes/usage.py`, `mcp.py`'s scope mapping - keep catching what they were written to catch, and several tests stub a resolver that raises a bare `KeyError`. Narrowing a catch site to `NotFound` is now a deliberate one-at-a-time change rather than a precondition.
  `supervisor.py:655` is the one deliberate raise site left alone: it is inside the hash-gated supervisor closure, its `KeyError` never reaches the HTTP middleware, and touching it would force a session reap for nothing.
- **S7.4's real fix is ordering, not retry.** The root cause is that `runner.cleanup()` frees the port *before* `_teardown_runtime` writes anything durable, so `--relaunch-wait` (which waited only for the port) had the successor running its 12-61s integrity check on `mux.db` while the predecessor was still flushing. The successor now also waits for the predecessor *process* (`wait_for_predecessor_exit`, bounded at 20s, warns rather than refuses). The drain widening in `run_sqlite_operation` is the second line, for the redeploy path where the predecessor is terminated ~3s after health stops answering: it widens the busy timeout rather than re-running the operation, so nothing executes twice and a batched commit cannot re-apply a batch.
- **A lost write is now loud wherever it happens**, not only during a drain: `sqlite_write_lost` names the store and method off the operation closure's qualified name, so no store passes its own name down. D3 should watch for that line as much as for the S2 counters.
- **Not done, and deliberately:** `routes/project_files.py`'s two `raise ValueError("project resource does not exist")` still answer 400 where they mean 404. Converting them is a wire change outside S7's scope; it is a one-line change per site whenever someone wants it. Its four `raise TypeError` validation sites *were* converted to `ValueError`, because those did have to move - a bare `TypeError` is now a 500.

### S8 - subprocess and process consolidation (usage, provider_accounts, git_monitor, harness, agent_environment, processes)

- [x] S8.1 Shared bounded runner (F/codex-G): extract the `worktree_exec` pattern (chunked bounded read, truncation reporting, timeout and `CancelledError` process-tree reap, correlation) into one helper; migrate `usage.py`, `provider_accounts.py`, `git_monitor.py`.
- [x] S8.2 `probe_cli_version` unification (F22): one implementation (keep the shim-recursion-safe `which_real` behavior), one cache policy, both call sites.
- [x] S8.3 `snapshot_all` grouping (F20): build the `session_id -> processes` index in one pass and serialize each process once.
- [x] S8.4 Graveyard purge retry cap (D2 finding 2): `worktree_graveyard_purge_failed` retries already-absent paths forever (1,165 warnings since 2026-08-21, up to 24 repeats per path). Treat an absent path as purged, and bound retries for paths that persistently fail with a terminal log line.
- [x] S8.T Tests: runner cancellation-reap test; output-cap truncation test; version-probe cache test; snapshot projection equivalence test; absent-path purge idempotency test.

Delivered as `src/swe_mux/bounded_subprocess.py` (`run_bounded`, `bounded_read`) and
`src/swe_mux/cli_version.py` (`probe`, `CliVersion`), with
`tests/test_bounded_subprocess.py`, `tests/test_cli_version_probe.py`,
`tests/test_processes_snapshot_grouping.py`, and six additions to
`tests/test_worktree_graveyard.py`.

Four decisions worth knowing before D3:

- **`run_bounded` raises `OSError` from the spawn rather than folding it into an
  outcome.** Every caller already phrases its own "could not start" diagnostic
  (`install ccusage`, `Could not start codex`), and swallowing the error would have
  made each of them re-derive it from a string field. Everything *after* the spawn
  reaps the tree on its way out, `CancelledError` included - which is the gap the
  audit correctly identified, against a timeout leak that did not exist.
- **The cap is per stream and reported, never hidden.** `usage.py` raises its
  existing "exceeded 10 MiB" on `stdout_truncated` (same message, but the limit now
  bounds memory instead of describing it after the fact), while `git_monitor._git`
  returns a new code **125** beside its reserved 124: every Git caller parses what it
  gets back, so a capture that lost its middle has to read as a failure rather than
  as a smaller repository.
- **The two `probe_cli_version` bodies were unified at the mechanism and kept apart
  at the presentation.** One subprocess per resolved executable per 5-minute TTL,
  `which_real` resolution for both (which additionally stops the agent-environment
  path from ever probing a mux shim); the registry still returns the version *token*
  because `version_is_untested` compares it against a bound, and the inventory still
  returns the CLI's own line and still requires a zero exit because it is shown to a
  person and used as an MCP catalog cache key. Collapsing those would have changed a
  displayed string and a cache key for nothing.
- **The graveyard's retry bound is in memory and dies with the daemon**, which is the
  point: a restart is a cheap deliberate "try that again", so a lock held by a process
  that has since exited gets a fresh budget while a live one stops writing the same
  warning forever. An absent path now counts as purged and says nothing at all.

One behavior difference worth watching in the D3 soak: `git_monitor` now spawns Git
with `stdin=DEVNULL` (it previously inherited the daemon's), so a Git invocation that
decides to prompt fails fast instead of blocking on a stdin nothing will ever write.
No query here reads stdin, so this should be invisible.

### S9 - MCP and automation consumers (mcp.py, deterministic_consumers.py)

- [x] S9.1 `handle_rpc` narrowing (F24): catch `ScopeMiss`/`AmbiguousIdentity`, not base `KeyError`, so handler bugs error instead of reading as "no such session".
- [x] S9.2 Test-file classifier (F26): explicit path-segment and basename conventions (`tests/`, `test_*.py`, `*_test.py`, `*.test.ts`, `*.spec.tsx`, `__tests__/`); false-positive tests for `latest.py`-class names.
- [x] S9.3 Blast-radius honesty (F/codex): failed provenance reads return `co_change_available: false` with a typed reason and a log line, not a silent empty list.
- [x] S9.4 Parse-timeout guard (F24): stop advising retry while the abandoned parse still occupies an executor slot; single-flight per transcript with the timeout attached to the flight, not the wait.
- [x] S9.5 Lock-map eviction (F24): evict per-key locks (scan_timeline, assistant dialogs, project_card) when their keyed entity ends.
- [x] S9.6 `list_sessions` size fitting (F24): per-item size accounting instead of full re-serialization per popped item.
- [x] S9.7 Doc-ownership cache (F/codex-K, F22): fingerprint paths+count+mtime_ns+size instead of max-mtime, and make the uncached `mcp.py` caller share the cached builder.
- [x] S9.T Tests for each of the seven items.

Landed in `tests/test_mcp_consumer_hardening.py` (52 tests). Three notes for whoever reads
this next:

- S9.1 narrows to `ScopeMiss`/`AmbiguousIdentity` **and** adds a `-32603` catch-all with a
  logged traceback, because narrowing alone would only have moved an accidental `KeyError`
  into the transport middleware, which turns it into the same unlogged 404 (that half is
  S7.3's). Two legitimate misses that used to ride the base-`KeyError` path were typed at
  their call sites instead: `run_action`'s catalog race now answers `unknown_action`, and
  `configurator_guide` already converted its own.
- S9.4 keys the flight on the transcript path and *refuses* a request for a different page
  of a transcript that is mid-parse, rather than running a second thread for it. The worker
  thread cannot be cancelled, so the flight is retired on thread completion; a caller that
  gave up is exactly the case the old code got wrong.
- S9.5's `ProjectCardService.forget_project` has no caller: nothing constructs that service
  yet (`project-card.md` already records this). The other two evict from their own
  lifecycles - scan-timeline after an ended session's final scan, plus a liveness-gated sweep
  that catches what a chained catch-up leaves behind, and the assistant when a dialog's turn
  finishes with nothing queued behind it.

### S10 - frontend quality (Settings, CodeEditor, bundle; server half lands in the S3-created route modules)

- [x] S10.1 Atomic settings save (F11): one server endpoint transacting config and keybindings together; client sends one request; the failure message can no longer claim "nothing was changed" when half committed.
- [x] S10.2 Restore defaults (F11): confirmation dialog on the destructive action, error handling with a visible failure status.
- [x] S10.3 Post-save chain dedup (F22): one shared apply-config function for the save/reset/load paths.
- [x] S10.4 CodeEditor (F/codex): last-emitted-string ref to skip the second full-document serialization and compare.
- [x] S10.5 Bundle splitting (F17): lazy-load the resource editor (with on-demand grammar loading) and the change map (Sigma/Graphology); record the before/after main-asset size in the PR description.
- [x] S10.T Tests: settings-atomicity test (server rejects half-commits); renderer specs for the confirm dialog and lazy routes; bundle-size assertion if the build exposes one cheaply.

`POST /api/settings/apply` (in the S3 settings route module) is the transaction, and its guarantee is an *ordering* rather than a database one, because the config and the keybindings are separate files: check the revision and normalize the chords (both pure, so an invalid document of either kind is a 422 with nothing written), stage the keybindings beside their destination, let `update_config` validate the whole candidate before it saves, then rename the staged file into place.
Only that last rename can fail after something has committed, and it answers 500 with `committed: ["config"]` instead of denying both.
Every answer carries `committed`, and `settingsSave.ts` derives the footer message from it - including the case the old code could not distinguish at all, a request that never came back, which now says the outcome is unknown rather than that nothing changed.

Two things found while doing it, both fixed here:

- The footer's dirty hint won over everything but "saving…", and a rejected save leaves the draft dirty - so the explanation the panel had just produced was replaced by "unsaved changes" and only the errors block said anything. A write in flight or refused now speaks over the hint.
- `CodeEditor`'s value reconcile could not tell a *lagging echo* from an external rewrite. The parent stores each emitted string and re-renders, so it is a turn behind the keyboard; during a burst the effect ran with a document the editor had already moved past, replaced the document with that older copy, re-emitted, and replaced it again. At machine typing speed this wedges the page outright (reproduced on the pre-change code, so it predates S10.4); at human speed it silently drops characters. `pendingEchoes` answers it - the ref S10.4 asked for handles the caught-up case in O(1), and the count handles the lagging one.

Bundle, measured on this branch (`npm run build`, entry chunk):

| | raw | gzip |
| --- | --- | --- |
| before | 3,421.45 kB | 1,075.35 kB |
| after | 2,165.91 kB | 654.55 kB |
| change | -1,255.54 kB (-36.7%) | -420.80 kB (-39.1%) |

CSS moved 467.10 -> 467.60 kB raw (77.70 -> 77.78 kB gzip), the two placeholder rules.
`CodeEditor` (361.56 kB / 117.32 kB gzip) and `ChangeMapPane` (176.28 kB / 44.40 kB gzip) became their own chunks, and each grammar its own; none is preloaded from `index.html`, so they are fetched on the click that needs them.
The dynamic grammar specifiers are mirrored into `optimizeDeps.include` because dev answers a runtime-discovered dependency with a full page reload that lands mid-spec in the renderer suite; `bundleSplit.test.ts` fails if the two lists drift.

### D3 - deploy checkpoint (primary; no reap)

- [x] D3.1 Full gate, redeploy, full Playwright renderer suite on a free port.
- [x] D3.2 Live soak: settings save/reset from two devices (revision conflict path); a full agent session lifecycle checking the new request-IDs correlate across `daemon.log` and `access.log`; MCP tool sweep against live sessions; editor and change-map lazy loads on desktop and mobile.

Run 2026-08-24 in the primary checkout against the real daemon on 8765, with 20-23 live sessions resident throughout.
No reap: `supervisor_bundle_current()` was `True` before and after, the redeploy logged "Supervisor bundle up to date", and supervisor pid 89372 (started 13:18) was never touched.
Scope covers all of Wave 3 plus the operator's project-config fix (`d196a90`), which is on master and shipped in the same bundle.

The gate is green on master: 4907 passed / 16 skipped in 52.8s, ruff clean, mypy clean over 216 files plus both `--platform` passes, `tsc --noEmit` clean for `src` and for the renderer harnesses, and 2042 frontend tests passing.
No frontend dependency landed this wave - `frontend/package.json` last moved at `f710c14` on 2026-08-22, already installed at D2 - so the `npm ci` trap did not apply, and S10's `vite.config.ts` change is picked up by the build the redeploy runs anyway.
The same 7 `@pytest.mark.asyncio` warnings in `test_assistant.py` are the only warnings left, unchanged from D2 and still S12.4's scope.

**The redeploy shipped, and three independent readings of the entry chunk prove it.**
The live daemon, `src/swe_mux/static/index.html`, and `dist/swe-mux/_internal/swe_mux/static/index.html` all serve `index-BXP-KtkV.js` and `index-5gIQGhV9.css`; before the run all three served D2's `index-DQBLUWjn.css`.
The shipped entry is 2,170,976 bytes raw / 654,935 gzip, against the 3.42 MB / 1.08 MB the S10 table records as "before" - so the 36.7% split is in the bundle a browser actually loads, not only in a build log.
`CodeEditor-CdaXUKvk.js` (361,627) and `ChangeMapPane-C_BstNQW.js` (176,280) are separate files in the bundle, and the served `index.html` contains exactly two asset references and no `modulepreload` at all.
The running process is the frozen app (`dist\swe-mux\swe-mux.exe --daemon-child`), which is why a plain `npm run build` would again have shipped nothing.
The swap hit the documented `WinError 5` straggler and escalated to an image-wide kill of 5 in-session hook helpers, exactly as the script says it will; no session was affected.

The renderer suite is 336/336 in 2.4 minutes on `RENDERER_PORT=4231` (`netstat` showed 4174, 4231, 4232 and 4233 all free; 4231 was chosen so a worktree run could not collide).
`rail-overflow.spec.ts` passed first time, so its known flake did not appear and no isolated re-run was needed.

What the soak proved:

- **A session-preserving restart no longer lies about how the last daemon ended, and no longer loses its last writes.**
  The redeploy's own successor still logged "previous daemon pid 78876 died without a clean shutdown", because *that* predecessor was the D2 bundle and S7.5's record is written by the process being replaced - the first restart after S7.5 lands cannot benefit from it.
  A `POST /api/daemon/restart` afterwards, with the new code on both sides, is the real test and it is clean: `lifecycle.log` shows `daemon pid 15952 planned detach handoff requested`, then `daemon pid 16028 started`, then `daemon pid 15952 clean exit (intent=detach)`, and `daemon.log` carries no crash warning at all for that transition.
  S7.4's ordering fix is visible beside it: the successor logged `waiting for predecessor daemon pid 15952 to finish its shutdown drain`, warned at the 20s bound and started anyway rather than refusing, and the predecessor finished 5s later.
  `sqlite_write_lost` has never fired - not once in 47,310 lines - and `database is locked` appears zero times after either transition, against five occurrences during D2's restart at 17:01 (`git-monitor`, `session_recovery`, and the loop faults they caused).
  Both transitions preserved every session: 22 live before the redeploy and 22 reattached, 21 reattached after the restart with the fleet back to 22 as agents spawned, supervisor `connected`, `supervisor_unadopted` 0, `cold_sessions` 0, and no session marked ended.
- **S7's correlation and typed errors, measured on the wire.**
  A plain `GET /api/health` comes back with a minted `X-Request-ID`; a well-formed inbound one is adopted rather than replaced.
  A bogus session id on `GET /api/sessions/{sid}/diagnostic-bundle` answers `404 {"error":"no such session","code":"not_found","kind":"session"}` - it names the kind and the key the caller sent appears nowhere in the body.
  A `PATCH /api/config` with a list body answers `500 {"error":"internal server error"}` and logs `unhandled request error method=PATCH path=/api/config request_id=d3soak-500-0001` with the traceback naming `routes/settings.py:95` and `TypeError: pop expected at most 1 argument, got 2` - the deliberate/accidental split doing exactly what S7.3 describes.
  All three request ids appear in `access.log` and `daemon.log` alike, and the daemon's own background lines carry them too (a transcript relocation and the observation warning it caused share one id).
- **S8's consolidation is invisible in the way it was meant to be.**
  Zero `exit_code=125` in the whole log and zero output-cap warnings, so nothing tripped the 16 MiB bound.
  `git-monitor` ran 25 iterations with 0 faults and 0 restarts, and `GET /api/git/graph` and `/api/git/worktrees` return real data for the live repository including this afternoon's commits - the `stdin=DEVNULL` change is as invisible as predicted.
  `worktree_graveyard_purge_failed` fired 0 times since the redeploy against 1,165 before S8.4; the last occurrence in the log is 14:31, on the old build.
  Provider-quota polling produces fresh rows (`sampled_at` 19:03, `status: ready`, `freshness: fresh`) for four of five accounts, and the provider-account audit trail is current.
- **S9's consumers answer honestly against live data.**
  `blast_radius` on `src/swe_mux/git_monitor.py` returns `co_change_available: true` with 21 hop-ordered callers, a 41-file co-change net and two owning docs, and no `co_change_unavailable_reason` - the silent-empty-list failure is gone in the direction that matters.
  `status()` carries `transcript_parses {in_flight: 0, timeouts: 0, refusals: 0}` after 12 tool calls including six from other agents' sessions.
  `list_sessions` fitted 20 sessions across 8 Projects into one page with `has_more: false`, and `deterministic_consumers` reports `running: true`, `findings: 0`, `last_error: null` - the doc-debt and scan consumers are quiet rather than broken.
- **S10's transaction and its split, on the shipped daemon.**
  `POST /api/settings/apply` with the current revision answers `200` with `committed: ["config"]` and moves the revision 154 -> 155.
  Replaying the now-stale `_revision`, with a *different* keybindings document attached, answers `409 {"error":"configuration changed externally","revision":155}` and commits nothing: `keybindings.json` is byte-identical with an unchanged mtime, the config revision and field are unchanged, and no `keybindings.json.tmp` is left behind.
  Driving the shipped page with Playwright, the initial load fetches three assets (entry JS, entry CSS, the note editor's wasm) and not one grammar, `CodeEditor` or `ChangeMapPane` chunk; importing the two the way the lazy wrappers do fetches them at that moment and nothing sooner.
  A machine-speed typing burst against the real `CodeEditor` (two 61-character bursts with no inter-key delay, run against the renderer harness) loses no characters in either the document or the parent's stored value - the condition that wedged the page before `pendingEchoes`.
- **The project-config fix ends the false conflict without weakening the real guard.**
  Three field-scoped writes in quick succession from two different drawer sections - the defaults form, then the automation opt-ins, then the defaults form again on its *original* cached read - all answer `200`.
  A whole-document write with a deliberately stale revision still answers `409 revision_conflict`.
  The file is byte-identical afterwards and its values are unchanged, so the guard was exercised rather than the content.
- **The gated live tiers are green.**
  32 passed / 15 skipped in 225s across `live_agent`, `live_subagent` and `live_mcp`: observer conformance for claude, codex, omp and pi, the opencode store canary, the subagent canaries, and the six MCP-wire control tests (`request_spawn`, `end_session`, `interrupt`) driven through a real agent's `/mcp` on an isolated daemon.
  The 15 skips are the `live_automations` tier (11) and four tests behind `SWEMUX_RUN_LIVE_PHASE2_TESTS` or absent per-provider system credentials - none of them in D3's scope.
- **The log watch is clean.**
  Across 6,653 post-redeploy requests the only 5xx is this checkpoint's own deliberate probe, and the 4xx are its own too (four `PUT /api/project/config` 400s from a first attempt with an unregistered project id, four 404s) plus seven `409`s from other agents' queue head-of-line contention.
  The 1,091 `503`s are the two startup windows answering "starting, phase X" as designed; the hook posts refused in that window are covered by the shim's disk spool, and no spool residue from today exists.
  Exactly two `ERROR` lines fired after readiness - the deliberate 500, and one pre-existing `asyncio: Task was destroyed but it is pending!`.
  The S2 counters stayed at zero: no `unreachable`, no frame desync, no swallowed `PtyError`, no output-drop or backpressure line.
  Retention swept on schedule (`automation_retention_swept tables=14 rows_removed=0 elapsed_ms=12.9`).

Five defects, none of them Wave 3's and none blocking Wave 4:

- **The code-structure graph has not re-indexed since Wave 3 landed, and answers stale rather than empty.** (medium)
  `code_context` returns nothing at all for `src/swe_mux/errors.py`, `src/swe_mux/bounded_subprocess.py` and `src/swe_mux/cli_version.py` - the three modules S7 and S8 created - and returns `logsetup.py`'s *pre-S7* symbol set, missing `request_id_var`, `bound_request_id`, `new_request_id` and the structured formatter.
  `git_monitor.py`'s indexed imports likewise omit `bounded_subprocess`.
  The cause is in `deterministic_consumers._code_graph`: `index_project` runs "at most once per project per process" and only when a session in that Project produces a turn with source writes, while `maintain_files` only covers files this daemon's own sessions edited.
  A branch that arrives by `git merge` - which is every landing - is therefore invisible until some session happens to edit a source file in that Project.
  This is pre-existing behaviour rather than S9's doing, but it is what S9's tools read, and stale-but-plausible is worse than the empty result the tools' own notes warn about.
- **`ccusage` refreshes have been timing out at 30s since 2026-08-21.** (low)
  A forced `POST /api/usage/refresh` during the soak failed the same way; the usage cache's last successful refresh was 2026-08-24 00:40.
  Pre-existing and outside this wave, and S8.1 made it *more* legible rather than less - the failure now logs `bounded_command_timed_out label=ccusage timeout_s=30` beside the adapter's own line, both carrying the request id.
- **`run_bounded`'s `operation_id` parameter has no caller.** (low)
  All three migrated sites - `usage.py`, `git_monitor.py`, `provider_accounts.py` - omit it, so every `bounded_command_timed_out` and cap line reads `operation_id=None` while the sibling line from the caller carries a real one.
  S7's request-id contextvar covers the request-driven paths, but `git-monitor`'s poll has no request id either, so a timeout there would log with neither identifier.
- **`SessionManager._fanout` tasks are garbage-collected while pending.** (low)
  48 `ERROR asyncio: Task was destroyed but it is pending!` since 2026-08-19, one of them during this soak at the exact moment the Playwright browser closed.
  A disconnecting WebSocket client leaves its fanout task uncancelled; the consequence is an unowned ERROR line rather than lost output.
- **Every scan-timeline completion pays a rejected round-trip first.** (low)
  `OpenRouter rejected completion parameter profile ... (max_completion_tokens -> max_tokens)` appears 23,132 times since 2026-08-20.
  `OpenRouterClient` picks its profile order from `_model_capabilities` on every call and never remembers which one the model accepted, so the same rejection repeats forever for `deepseek/deepseek-v4-flash`.
  Natural S12 material: one cached per-model profile removes an HTTP round-trip from every scan and 23k lines from the log.

Also noted, not defects: one supervisor-side `KeyError: 'unknown session'` at 18:51 from a resize for a session the supervisor did not know - the `supervisor.py:655` raise site S7.3 deliberately left alone, and pre-redeploy; the scan-timeline `OpenRouter structured response must be an object` behavior-repair path firing twice and repairing both times; the `session_claimed_infrastructure` ownership diagnostic, which fires for every redeploy run from inside a session; and six hook-spool files from 2026-08-10 to 2026-08-17 belonging to sessions that ended before their spool drained.

Not exercised here, deliberately: `POST /api/config/reset` ("Restore defaults") was not run against the live daemon, because it would discard the operator's real settings to prove a confirmation dialog that `settings-save.spec.ts` already covers.
Left for the operator: the mobile pass (editor and change-map lazy loads on a phone, and how the placeholder reads over a slower link), a genuine two-device settings save, and the voice round-trip.
One thing to watch there: a 409 from another device renders as "invalid · nothing was changed" plus the daemon's "configuration changed externally" in the errors block.
"Nothing was changed" is true, but the shared `_revision_conflict` answer carries no `committed` array, so `saveFailureStatus` falls through to the same wording an invalid field gets.

**Wave 4 is unblocked.**

## Wave 4 - hygiene, dependencies, gates (S11, S12 in parallel, then D4)

### S11 - dependencies and hygiene (rewritten 2026-08-24 against the locked Phase 10.5 licensing posture)

Phase 10.5 landed as this roadmap started and supersedes the audit's licensing findings: Apache-2.0 plus DCO, and a two-half license gate (`packaging/license_audit.py --check` over the resolved closure in verification, `build_desktop.verify_bundle_licenses` over the built tree), with LGPL requiring an `ALLOWLIST` entry AND replaceable-source shipping under `_internal/<pkg>/` - which pystray and num2words both already satisfy via the spec's `collect_all` loop.
The original S11.1 (audit F15) assumed a pystray-only allowlist and is closed; do NOT swap num2words for `misaki[en]` - the closure resolves it either way, the compliance mechanism is identical, and the explicit declaration documents the runtime requirement the frozen build's `collect_all` depends on.
Any task here that changes a dependency must run the mandated flow: `uv sync --extra desktop`, then `uv run python packaging/license_audit.py --write`, and commit both generated files (`THIRD-PARTY-NOTICES.md`, `packaging/third_party_licenses.json`).

- [x] S11.1 num2words posture verification (F15, superseded by Phase 10.5): no dependency change. Confirm the allowlist entry, notice text, and replaceable-source shipping are green through both gate halves, and add a pyproject comment on the num2words line saying why it is a direct dep (misaki's English G2P at runtime; frozen build `collect_all`s it as replaceable LGPL source). (Verified: `--check` clean at 203 packages, `ALLOWLIST['num2words']` and its notice section present, `RELINKABLE_LGPL` covers it, and the spec's `collect_all` loop still names it. The comment now lives on the num2words line inside the new `voice-local` extra and records why `misaki[en]` is the wrong declaration.)
- [x] S11.2 voice-local extra (F16): move the voice/NLP closure (spacy, misaki, onnxruntime, faster-whisper, en-core-web-sm, num2words chain) behind an extra. Three gate interactions are load-bearing: `license_audit.py`'s closure walk must be defined over the union of extras (or the desktop build's extra set), not whatever happens to be synced; the frozen build REQUIRES the extra present (num2words' replaceable-source `collect_all` is license compliance, not just voice function), so `redeploy_desktop.py` preflight must assert it before building; and the frozen-app round-trip verify must stay green. (`voice-local` = en-core-web-sm, faster-whisper, misaki, num2words, onnxruntime, spacy. `DISTRIBUTED_EXTRAS` is now `("desktop", "voice-local")`, so the audited closure is byte-identical to before the move - the sidecar did not change at all and the notices changed only where they now name the owning extra. `build_desktop.verify_build_extras_installed` refuses a build whose environment lacks either extra, and `redeploy_desktop`'s preflight runs the same check first, before it inspects the supervisor or stops anything, recording a `refused` outcome that names the extra. `.worktree-setup` and the Windows CI job sync it because the real-G2P tests are `importorskip`-guarded; the Linux CI job stays bare on purpose and is the leg that proves the package still imports without it.)
- [x] S11.3 Vendor cleanup (F28): remove the 13 unreferenced `frontend/vendor/continuity-editor-*.tgz`. (Done; only 0.2.36 remains, 5.66 MB removed.)
- [x] S11.4 Line endings (F28): `.gitattributes` entries (`.worktree-verify`/`.worktree-setup` `text eol=lf`) and renormalize. Check first whether this already landed - an uncommitted `.gitattributes` change existed in the primary on 2026-08-24. (Already landed as `* text=auto eol=lf` in 1fc2193, which covers both files - `git check-attr` reported `eol: lf` before the change and both check out LF. Added the two explicit named entries anyway, next to the `*.sh` block, because they are bash scripts without the extension that says so and the land gate is one of them. `git add --renormalize .` produced no churn: the index was already LF throughout.)
- [x] S11.5 `git_provenance_backfill.py` (F28): move to a tools/ location or document it as a one-shot migration so it stops reading as dead code. (Moved to `src/swe_mux/tools/git_provenance_backfill.py` with a docstring naming it a one-shot operator migration, plus a `swe_mux/tools/__init__.py` that states the rule. Inside the package rather than the repository's top-level `tools/`: it imports swe-mux internals, and moving it out would drop 1700 lines from mypy strict, ruff, and the test suite while breaking `python -m` - a worse outcome than the one being fixed. Three test importers and the docs updated; a new test pins that no daemon module imports `swe_mux.tools`.)
- [x] S11.T Verification: fresh `uv sync` matrix (base, `--extra voice-local`, desktop) each building and starting; both halves of the Phase 10.5 license gate green; frozen-app round-trip verify; generated notice files regenerated and committed for any dependency change. (Base leg: a throwaway `uv sync` with no extras imports `swe_mux.server`, `voice`, and `kokoro_tts`, runs `swemux --help`, and passes the whole suite. voice-local and desktop legs: the worktree venv carries both and passes `.worktree-verify`. Both gate halves green - `license_audit --check` clean at 203 packages, and the bundle half's unit coverage extended. The frozen-app round-trip build and its live verify belong to D4, which owns the redeploy.)

### S12 - test infrastructure and ratchets

- [x] S12.1 Frontend unit-test typechecking (F28): a tsconfig that covers the 184 untypechecked files, fixing fallout; wire it into `.worktree-verify` next to `check:renderer`.
- [x] S12.2 Source-text test migration (F28): convert the worst of the 37 readFileSync-regex test files to behavior tests (controller units or renderer specs); leave a documented allowlist for the rest.
- [x] S12.3 Complexity ratchet (F27): enable C901 with the threshold set at today's maximum and a recorded plan to step it down; first targeted reduction is table-driving `Config._validate`'s range checks (no Pydantic).
- [x] S12.4 Warnings gate (F28): with AppKey landed (S3.4), fix residual warnings and make new warnings fail CI.
- [x] S12.5 Preview JS rewriting (F21, optional): move the lexical rewrite to tree-sitter-based specifier rewriting in the S3.3 Preview module, with negative fixtures for strings, comments, templates, and data blocks. Defer if wave capacity is short; the practical exposure is narrow.
- [x] S12.T The gates themselves are the tests; CI must be green with both ratchets on.

Done 2026-08-24. Both ratchets are on, `.worktree-verify` is green in 74s against 62s before, and the
suite went from 4907 to 5025 backend tests and 2042 to 2104 frontend tests with no behaviour change.

**S12.1 widened `tsconfig.test.json` rather than adding a second config.** It now includes `src` +
`test` (was `src` + `test/renderer`) at full `strict`, and `check:renderer` became `check:tests` in
`package.json`, CI, and `.worktree-verify`. Two measurements decided both halves. Adopting the 184
unit-test files under the same strict rules the renderer harnesses already use cost **12 errors in 4
files** - a stale `GitProvenance` fixture missing the two fields S7 added, an `as const` fixture
whose readonly arrays no longer matched `GitGraphCommit`, a `never`-narrowed capture, and four calls
passing a bare `'c'` where xterm declares a `const enum` - so a weaker second set of rules for the
unit tests would have bought nothing and cost a rule everyone has to remember. And one config beats
two because the second recompiles all of `src` for nothing: `src`+`test` is ~23s where
`src`+`test/renderer` was ~10s, while two separate passes would have been ~33s.

**The trap the widening exposed is worse than the typing.** Twelve `*.test.ts` files asserted at
*module scope* rather than inside `test()`. Measured by breaking one deliberately: `all.ts` stops
importing at the throw, **2042 tests became 1047 - and all 1047 were reported passing, `# fail 0`**.
Only the exit code told the truth, so anything reading the summary (a human, a `| grep fail`) reads
it as green. All twelve were converted, and `testRegistry.test.ts` now fails any test file that does
not register with `node:test`, so the thirteenth cannot be written.

**S12.2 is one conversion plus a guard with reasons, and the ratio is the finding.** The audit
counted 37; the real number is 42, and reading each one shows why they are not simply bad tests:
18 read `App.tsx` (the composition root, which has no unit seam - the fix is a controller
extraction, as S4 did five of, not a renderer spec), and 16 read `style.css` (which *is* the
artifact - a contrast floor is a fact about the CSS, and a renderer spec could only check the states
it happens to mount). `railDensity`'s assertion that Comfortable writes no `data-rail-density`
attribute became a real behaviour test against a stubbed root element, and gained a second test for
the per-device-class key it could not previously reach. The rest are listed in
`frontend/test/sourceText.test.ts` against eight reason codes, each saying what channel does not
exist yet; a test file that reads source without a listed reason fails the gate, an entry that
outlives its file fails it too, and an unclaimed reason code fails it as well so the list cannot
quietly grow reasons nobody uses. Two of the codes - `negative-invariant` (asserting something is
*absent* from a whole file, like the second cron implementation `schedules.test.ts` forbids) and
`stylesheet` - do not expire; `component-jsx` and `composition-root` are the debt, and a renderer
harness is the way off them.

**S12.3's threshold is 88 and it is `server._build_runtime_handles`.** `Config._validate` was 170,
twice anything else in the codebase; table-driving its **103** mechanical checks (76 numeric ranges,
20 fixed-spelling choices, 5 bounded strings, 2 whole-value patterns) into four declarative tables
took it to **76**, which handed the maximum to the composition root. So the ratchet starts one step
lower than "today's worst" would have been, and still requires no refactor of anything else. No
Pydantic, as the audit argued: the choke point is the design, not the accident.

The rewrite was mechanical *and proved so*. An AST pass identified each branch by shape rather than
by text, so nothing was deleted that had not been shown to have the form; then a differential run
imported HEAD's `config.py` alongside the new one and drove both with **505 probe values** across
every table field - each range's `low-1`/`low`/`high`/`high+1`/`0`, every legal spelling of every
choice, every string bound, every pattern - and found **zero divergence**. Every error message is
the byte-identical string the branch produced, which matters beyond the tests: `settings_catalog`
*probes* `_validate` for the sentence it shows an agent (`design/features/configurator.md`), so a
reworded message would have silently changed the configurator's advertised constraints.
`tests/test_config_validation_tables.py` (106 tests) keeps the tables honest afterwards - one rule
per field, every rule refuses both ends, no rule refuses its own default.

Step-down plan, to be taken one function at a time and only when that function is being touched for
another reason - a decomposition done to satisfy a number is how a choke point becomes six places
that disagree:

| threshold | unblocked by | note |
| --- | --- | --- |
| 88 (now) | S12.3 | `server._build_runtime_handles` (88), then `config._validate` (76) |
| 76 | decomposing `_build_runtime_handles` into per-subsystem handle builders | the composition root is the natural place for this; S3 left it whole deliberately |
| 48 | `_validate` (76), `history.search_history_index` (47), `operational_telemetry.scan_native_telemetry` (44), `observation.apply_hook_observation` (42), `config.load_config` (41) | five functions, four owners |
| 30 | the ~10 functions between 30 and 40 | |
| 25 | the long tail | 192 functions were over 10 at the audit; 25 is where a reviewer can still hold a function in their head |

**S12.4 starts from zero, not from an allowlist.** The 18 warnings were three causes, all ours and
all fixed rather than filtered: a docstring containing `\wsl.localhost` that was not a raw string
(11), a module-level `pytest.mark.asyncio` in `test_assistant.py` that `asyncio_mode = "auto"` has
made redundant since it was written and which warned once per *sync* test in the file (6), and an
`app[key] = …` write after the app had started (1). `filterwarnings = ["error", …]` is now in
`pyproject.toml` with the rule that every entry is dated and expected to be removed. There is
exactly one entry and it is structural: `ResourceWarning` fires when the garbage collector reaches
an unclosed handle rather than when the leak happens, so under `-n auto` it attaches to whichever
test allocated next and its count varies run to run - promoting it would redden the gate over
machine load rather than over the code, the same failure mode CLAUDE.md records for fixed
`asyncio.sleep` calls. Turning it on *deliberately* (`-W error::ResourceWarning`) still works and is
how the two real leaks this found were fixed: `adapters/codex.py` and `observation.py` each opened a
transcript to read one line and never closed it.

**S12.5 was done rather than deferred**, because tree-sitter and the javascript grammar are already
dependencies (the code graph loads them) so the cost was the query, not the stack. A module
specifier is now found by *being* one - a `string` reached through `import_statement.source`,
`export_statement.source`, or a dynamic `import()`'s argument - which no comment, ordinary string, or
template literal can be. Two findings beyond F21 came out of writing the negative fixtures: the
lexical rewrite also prefixed **protocol-relative** specifiers, turning `import "//cdn.example.com/lib.js"`
into a path on the mux origin; and a body that does not parse as JavaScript now falls back to the old
regex on purpose, because an over-broad rewrite beats a Preview whose every module 404s.

Two things the next package should know. `.worktree-verify` grew by ~12s, all of it the widened
typecheck, and that is the whole added cost - the two ratchets are free at runtime. And the frontend
suite's real hazard is not the typing but the reporting: `# fail 0` is not evidence that the suite
ran, only `# tests <N>` is.

### W4.5 - D3 findings sweep (added from the D3 soak; parallel with S11/S12, lands before D4)

Five defects the D3 soak surfaced, none caused by Wave 3; files are disjoint from S11/S12.

- [x] W4.5.1 Code-graph staleness after a landed merge (D3 finding 1, medium): `index_project` runs once per project per process and `maintain_files` covers only this daemon's own edits, so files arriving via `git merge` are invisible or stale in `code_context`/`blast_radius` (post-Wave-3, `errors.py`/`bounded_subprocess.py`/`cli_version.py` were absent and `logsetup.py` answered with pre-S7 symbols). Reindex on trunk movement or fingerprint-invalidate per file; a stale-but-plausible answer is worse than the empty one the tools already warn about.
- [x] W4.5.2 `SessionManager._fanout` task ownership (D3 finding 4): 48 unowned "Task was destroyed but it is pending" ERRORs since 2026-08-19 (and the same line appears in the 2026-08-05 incident logs); own and drain fanout tasks like the sibling sites.
- [x] W4.5.3 `run_bounded` operation correlation (D3 finding 3): all three migrated callers omit `operation_id`, so a poll timeout logs with no identifier; wire real ids so S7's correlation reaches subprocess logs.
- [x] W4.5.4 Scan-timeline accepted-profile cache (D3 finding 5): every call pays a rejected round-trip (23,132 log lines since 2026-08-20) because the accepted profile is never cached per model; cache it.
- [x] W4.5.5 ccusage refresh timeout (D3 finding 2): timing out at 30s since 2026-08-21, pre-existing; diagnose with the bounded runner's new legibility and fix the cause or the bound, whichever the evidence names.
- [x] W4.5.T Tests per item where a regression test is expressible; W4.5.1 needs one proving a merge-arrived file becomes visible.

Delivered on `worktree-w45-d3-sweep`, 2026-08-24. Gate green in the worktree: 4936 passed / 16 skipped in 48.2s, ruff clean, mypy clean over 214 files plus both `--platform` passes, `tsc --noEmit` clean for `src` and for the renderer harnesses, 2042 frontend tests passing, and the same 7 pre-existing `@pytest.mark.asyncio` warnings from `test_assistant.py` and nothing new.

Four decisions worth knowing:

- **W4.5.1 detects the trunk moving; it never re-indexes per request.** The graph now records which commit it reflects (`code_graph_index_state`, schema version 2) and each turn boundary compares that against `rev-parse HEAD`. Unmoved is one `rev-parse` and nothing else; moved is one `git diff --name-only --no-renames --relative -z` plus the ordinary per-file re-parse of exactly what changed. Only an *unusable* delta re-seeds the tree - a commit git can no longer resolve, or one over `MAX_TRUNK_DELTA_FILES` (400). The head is read **before** the seed walks the tree, because a commit landing mid-parse recorded as indexed is permanent staleness, while recorded as not-yet-indexed it costs one delta pass. `tests/test_code_graph_trunk_refresh.py` builds real repositories and runs real merges, for the same reason `test_change_map_endpoint.py` builds real worktrees.
- **W4.5.2 needed a drain, not only ownership.** All 48 ERRORs were `fanout-*` blocked on `output_queue.get()`, so the discard callback alone would not have stopped them: on an end that did not arrive *as* the queue's own sentinel, nothing was ever going to feed that task again. `_drain_session_loops` waits `SESSION_LOOP_DRAIN_SECONDS` before cancelling, because the sentinel is queued behind the pane's last bytes, and excludes the calling task, because `_mark_ended` runs inside the fanout on the ordinary end path.
- **W4.5.3's second id source is `bound_request_id`, not a new parameter.** The git-monitor poll and the provider quota poll each mint one id per iteration and bind it, which stamps the loop's own lines *and* reaches every subprocess the iteration starts, including those inside per-session tasks; `usage.py` passes the refresh id it already had. `worktree_exec.py` was wired the same way while the pattern was open, so no `run_bounded` caller omits correlation now.
- **W4.5.5 was measured, and the bound was simply wrong.** Running the daemon's exact command from a shell on the primary host (36,529 Claude transcripts, ~21 GB of corpus): 33.9s cold, 10.3s warm, 5.8s warm with `--offline`. Exit code 0 and an empty stderr every time - no hang, no update check, just a whole-corpus read that outgrew a 30s bound. `USAGE_TIMEOUT_SECONDS` is now 120s (4x the measured cold cost) and a timeout names the bound and what it spent. `--offline` was deliberately not adopted: it buys ~4s by changing what the dollar figures are computed from.

### D4 - final checkpoint (primary; no reap)

- [x] D4.1 Full gate with ratchets on, redeploy, confirm the voice extra preflight fires when deliberately unset.
- [x] D4.2 Extended live soak across a normal working day of real sessions; review `daemon.log` for new-diagnostic noise; close out this roadmap by archiving it with a completion note per docs governance.

Run 2026-08-24 in the primary checkout against the real daemon on 8765, with 18-21 live sessions resident throughout.
No reap: `supervisor_bundle_current()` was `True` before and after (source hash `94066ae7`, the one D1 built), every run logged "Supervisor bundle up to date", and supervisor pid 89372 was never touched.

**The primary venv gained S11's extras structure first, and the exact command has a footnote.**
`uv sync --extra desktop --extra voice-local --group package` leaves `pyinstaller 6.21.0` importable, `license_audit.py --check` clean at 203 packages, and `missing_extra_distributions()` returning `[]`.
It also *uninstalls* `playwright`, `greenlet` and `pyee`, because those come from the `preview-capture` extra that the command does not name - correct for the bundle (`DISTRIBUTED_EXTRAS` is `("desktop", "voice-local")` and the audited closure is unchanged either way), and wrong for a source-run daemon, which silently loses Preview screenshot capture.
The full form for a primary checkout is therefore **`uv sync --extra desktop --extra voice-local --extra preview-capture --group package`**, which was run before the successful redeploy; the license audit is still clean at 203 packages afterwards, because `preview-capture` is outside the distributed closure.

**The gate is green on master with both ratchets on, and the warnings ratchet is clean rather than filtered.**
5068 passed / 16 skipped in 61.2s, **zero warnings** - no warnings-summary section in the output at all - ruff clean, mypy clean over 217 source files plus both `--platform` passes, `tsc --noEmit` clean for `src` and for `src`+`test`, and 2104 frontend tests passing reported as `# tests 2104` (the count, not `# fail 0`, being the evidence the suite ran, per S12's own finding).
The backend number is 5068 rather than S12's recorded 5025 because W4.5 landed on top of it.

**The voice-extra build preflight refuses, in both directions, without uninstalling anything from the primary venv.**
A throwaway venv carrying only the base dependencies (`uv pip install .`, no extras) loaded `packaging/build_desktop.py` by file path and ran the real functions.
`missing_extra_distributions()` named all eight distributions with the extra that owns each, and `verify_build_extras_installed()` raised `SystemExit` carrying the instruction to run `uv sync --extra desktop --extra voice-local` and the LGPL relink reason.
Installing only the `desktop` extra into that venv narrowed the refusal to the six `--extra voice-local` entries, so the message names the extra that is actually missing rather than reciting a fixed list.
`redeploy_desktop._run` reads the same function before it inspects the supervisor or stops anything, and records `refused`.
One incidental finding: `missing_extra_distributions()` imports `packaging.requirements`, which no extra or group declares - it arrives transitively through PyInstaller, so every real build environment has it, but a bare venv raises `ModuleNotFoundError` from the check rather than reporting the missing extras.

**The redeploy shipped on the fourth attempt, and the three failures name a hazard worth writing down.**
Attempts one to three aborted at `replace_dir(dist/swe-mux -> dist/swe-mux.prev)` with `WinError 5`, escalated to the documented image-wide kill, were denied again, and returned `swap_failed` ("Your change did NOT ship") after relaunching the old bundle unchanged - which is the staged design working: no attempt ever left the fleet without a daemon, and all 18-21 sessions survived every one.
The third attempt ran the same script with the rename retried for 180s instead of 20s and a holder report on every failed attempt, and was denied for the whole 180s.

What the diagnostics ruled out, and the one correlation that survived:

- Not the directory or its permissions: `dist/swe-mux.failed`, a sibling bundle in the same parent, renames and renames back instantly, and `Get-Acl` is byte-identical between the two.
- Not a visible holder: a **path-component** anchor scan finds only the running app and its daemon child inside `dist/swe-mux`, and both are terminated before the rename. The first version of that scan used `startswith`, which matches `dist/swe-mux-supervisor` and reported eighteen winpty consoles as holders of the app bundle - a false lead worth naming, because the two bundle directories are prefixes of each other by construction.
- Not a cwd anchor: every `swe-mux.exe`, `swe-mux-supervisor.exe` and winpty `OpenConsole.exe` reports `cwd=~/.mux`, which is exactly what `launch_app`'s "cwd must stay OUT of dist/" comment exists to guarantee.
- Not helper churn *at rest*: sampling every 0.5s for 45s on a healthy daemon found exactly two `swe-mux.exe` processes, the app and its daemon child, and no short-lived helpers.
- But the log correlates perfectly with helpers *at stop time*: every failed attempt logged "sparing N in-session swe-mux helper(s) (hook clients)" (4, 1, 7) and then escalated; **the successful attempt logged no sparing line at all and swapped first try.** The reading that fits is that a hook helper's image stays mapped through its own teardown for longer than the 20s budget, and `force_stop_app_images()` runs once rather than while the rename is retried.

The practical rule this leaves: **a redeploy wants a quiet fleet.** If the swap is denied, do not reach for a kill - wait for the in-session helpers to drain and run it again, which is what worked here. Naming the holder beyond that needs elevation (`handle64`, or reading Defender's exclusions), and Defender is a live suspect on this host: real-time protection is on, 162 processes report `AccessDenied` to an unelevated scan, and `APP_HEALTH_TIMEOUT_SECONDS` was already raised 300 -> 600 because a fresh bundle's first launch spends minutes in image scanning.

**The shipment is proved by the swap on disk and by behaviour, because the asset hash could not prove it this time.**
No `frontend/src` file changed between D3 and D4 - S11 removed vendored tarballs, S12 touched tests and configs, W4.5 is backend - so the rebuilt entry chunk is byte-identical (`index-BXP-KtkV.js`, `index-5gIQGhV9.css`) and all three readings agreeing says nothing.
`dist/swe-mux/swe-mux.exe` is dated 21:27 against `dist/swe-mux.prev/swe-mux.exe` at 18:57 (D3's bundle, retained for rollback), and `dist/.staging` is consumed.
The behavioural proofs below are what actually establish that the new backend is the one running.

**W4.5 is live, all five items, on the redeployed daemon.**

- **W4.5.1, the trunk refresh.** `code_context` for `src/swe_mux/tools/git_provenance_backfill.py` - the module S11.5 created, which reached this checkout by merge - returned **nothing at all** before the redeploy and returns 40 symbols plus its five imports after it. The contrast is the point: `bounded_subprocess.py`, which this daemon's own sessions had edited, was already indexed on the old build. The graph now follows the trunk rather than the edits.
- **W4.5.2, fanout ownership.** Four pty WebSockets were opened against live sessions and abandoned by aborting the transport with no close frame - the shape a closed browser tab produces, which is what D3 caught 48 times. Zero `Task was destroyed but it is pending` and zero new `ERROR` lines followed, across a window longer than `SESSION_LOOP_DRAIN_SECONDS`.
- **W4.5.3, subprocess correlation.** `ccusage refresh started ... request_id=5a9e89de612b47c9 operation_id=696850b8a7594e229db28683a9bfd595` and its matching completion line, against `operation_id=None` on the old build an hour earlier.
- **W4.5.4, the accepted-profile cache.** The rejection lines stop, and the shape of what remains is worth knowing: ten fire between 21:29:40 and 21:30:01, all for `deepseek/deepseek-v4-flash`, because the restore-scan-timeline catch-up starts about ten completions concurrently and none of them has learned anything yet. The first success logs `accepted completion parameter max_tokens; later calls start there` at 21:30:01 and **not one rejection follows**. So the cost is a bounded burst once per process against 23,132 lines over four days. `set_model_catalog` clears the cache deliberately, so a catalog refresh re-pays that burst.
- **W4.5.5, the usage bound.** A forced `POST /api/usage/refresh` completed in **44,157 ms** with `status: ready` and `error: null`. The same call against the old build 40 minutes earlier returned `"error": "ccusage refresh timed out"` after 31s. 44s is a result the 30s bound could not produce and the 120s bound has room for.

**S11 is green on the shipped bundle and in the repository.**
A TTS round-trip through the redeployed daemon (`POST /api/voice/speak`) returned a ready 103,244-byte WAV, 2.1s, `engine: kokoro`, `error: null`, and `GET /api/voice` reports `engine_available: true` with `diagnostic: null` and the Kokoro model `ready` - so the diagnostics that would name the missing extra are correctly silent.
`THIRD-PARTY-NOTICES.md` and `packaging/third_party_licenses.json` are committed at `0b3b4fb` and current: re-running `license_audit.py --write` reproduces both byte-identically.

**The restart path is clean across four transitions, which is more soak than a single redeploy would have given.**
`sqlite_write_lost` **0**, `database is locked` **0**, and "previous daemon ... died without a clean shutdown" **0** - against 40 occurrences of that warning earlier in the same log file.
S7.5's record replaced it every time, in the accurate form: `lifecycle.log` shows `daemon pid 88532 planned detach handoff requested`, then `previous daemon pid 88532 ended a planned detach handoff without recording a clean exit; last heartbeat 6s before this start (expected: ...)`, then `daemon pid 96812 started`.
Every session survived every cycle - 19, 18, 18, then 21 reattached - with `supervisor_state=connected`, `supervisor_unadopted` 0, `cold_sessions` 0, and no session marked ended.
The S2 counters stayed at zero throughout: no `unreachable`, no frame desync, no swallowed `PtyError`, no output-drop or backpressure line.
`worktree_graveyard_purge_failed` fired 0 times.

**The log watch after the final redeploy is clean: zero `ERROR` lines after the daemon became ready.**
Two `ERROR` shapes did appear during the *failed* attempts, both bounded to a restart transition, and both are reports rather than fires:

- `asyncio: Exception in callback BaseProactorEventLoop._start_serving.<locals>.loop(...)` ending in a bare `AssertionError` from `base_events._attach`, twice, each at the moment a redeploy stopped that daemon: a connection accepted while the loop is tearing down attaches a transport to a closed loop. New - D3's watch did not record it - and the same class of unowned-ERROR-line defect W4.5.2 fixed for fanout tasks. **(low)**
- `aiohttp.server: Unhandled exception ... RuntimeError: Connection closed`, three times, all inside a startup window before the daemon answered ready: clients that gave up while `database-integrity` ran. **(low)**

**The gated live tiers are green: 32 passed / 15 skipped in 369.9s** across `live_agent`, `live_subagent` and `live_mcp` - the same 32/15 D3 recorded, with the 15 skips being the `live_automations` tier and the four tests behind `SWEMUX_RUN_LIVE_PHASE2_TESTS`.

Left for the operator, because they need hands on a phone or an eye on a real screen: the mobile pass (voice round-trip on the phone, editor and change-map lazy loads, rail drag, sidebar tick cadence), and a day of normal use as the real soak - the extended soak this checkpoint could only run across four restarts in one evening.
`dist/swe-mux.prev` holds D3's bundle if any of that goes wrong.

**Roadmap v2 is complete.** Every work package S1-S12, both mid-wave sweeps W2.5 and W4.5, and all four deploy checkpoints are landed, shipped, and verified on the live daemon.

## Dependency summary

- S1 and S2 are parallel; both precede D1; D1 precedes everything that assumes the new supervisor protocol at runtime.
- S3, S4, S5 are parallel; S6 needs wave 1 landed (session.py overlap with S2).
- S7-S10 need S3 (server.py structure) and S4 (App.tsx structure) landed; they are mutually parallel.
- S11 and S12 are parallel; S12.4 needs S3.4; S12.5 needs S3.3.
- Findings deliberately not scheduled: /events per-client serialization and awaited-refresh staleness beyond S4.2 (both noise-level; fix opportunistically when touching those files).
