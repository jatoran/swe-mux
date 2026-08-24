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

- [ ] S3.1 Feature route extraction (F/codex-8): move route tables and thin handlers into per-domain route modules registered from the composition root; keep `_build_runtime_handles()` as the intentional root.
- [ ] S3.2 Worktree/git mutation service: move the multi-stage repair/burial/rollback/quarantine/purge transaction out of the transport module into a service returning typed outcomes.
- [ ] S3.3 Preview transport module: move Preview rewriting/proxy helpers into their own module (F21's future tree-sitter work then has a home).
- [ ] S3.4 AppKey migration (F28): replace the ~632 string app-keys with typed `web.AppKey` handles; this also eliminates the bulk of the test-run warning noise and is the prerequisite for the warnings ratchet in S12.
- [ ] S3.T Tests: existing suite is the harness (moves must not change behavior); add an import-boundary check so feature modules do not import the composition root.
- [ ] S3.D Docs: update `technical/backend/packages.md` module map for every new module.

### S4 - App.tsx decomposition plus the refresh fix

- [ ] S4.1 Fleet-refresh controller (F4): extract refresh into a controller module; give all five GETs a default `timeoutMs`, switch to `Promise.allSettled` with per-slice application, and make the in-flight dedupe abortable/resettable so a hung request can never pin future refreshes.
- [ ] S4.2 Stale-await fix (F/frontend-new-3): a mutation that awaits refresh must not be handed a pre-mutation in-flight promise; return the queued follow-up instead.
- [ ] S4.3 Command registry (F/codex, 5s clock): memoize the registry on its real inputs and gate `searchCommands` on the palette being open.
- [ ] S4.4 Clock subtree isolation: move `useRowClock` consumers into memoized subtrees so the 5s tick re-renders the sidebar rows, not the shell.
- [ ] S4.5 Controller extraction for the largest remaining App.tsx state clusters (layouts, overlays, gestures) to the extent it stays behavior-preserving; do not chase a line-count target.
- [ ] S4.T Tests: unit tests for the refresh controller (hung request recovery, partial failure application, dedupe reset); renderer spec for palette gating; update any source-text tests that asserted on moved App.tsx code.
- [ ] S4.D Docs: `technical/frontend/packages.md`.

### S5 - store layer (automation_store, prompt_queue, voice, land_store; no S3/S4 overlap)

- [ ] S5.1 Scan search (F6): pass `newest_first=True` in both search callers, add a truncation flag to the response, add an index for the unindexed `ORDER BY t0`, and propagate the discarded `_memory_scope` truncation flag.
- [ ] S5.2 Retention batching (F12): per-table operations deleting bounded rowid batches with commits between batches, plus measured `created_at` indexes on the tables that need them; log rows-removed and elapsed per table.
- [ ] S5.3 Voice eviction (F19): replace the unindexed `COALESCE` group scans with `WHERE stream_id=? OR id=?`, batch victim deletion.
- [ ] S5.4 Prompt queue append (F18): skip renumbering on ordinary tail appends; renumber only on anchor inserts. Correct the schema comment that claims the partial unique index enforces NULL-sender dedup.
- [ ] S5.5 LIKE escaping (F23): route `target_fragment` and the history metadata fallback through the existing `_escape_like`/`ESCAPE` pattern.
- [ ] S5.6 Land audit atomicity (F/codex, downgraded): add `transition_with_event` / `enqueue_with_event` so a state transition and its audit row commit in one operation; fix the two write-event-first call sites.
- [ ] S5.T Tests: >2000-record scan-search regression (newest record found, truncation reported); retention batching test proving intermediate commits; LIKE metacharacter test; tail-append write-count test; crash-between-transition-and-event test made impossible by construction.

### S6 - session.py and observation follow-ups (after wave 1 lands; conflicts with S2 otherwise)

- [ ] S6.1 Attach replay off-loop (F10): move the whole-file read and per-line JSON decode of transcript attach into a thread with chunked yielding, so a multi-ten-MB transcript cannot stall the event loop; bound peak memory to a chunk, not the file.
- [ ] S6.2 Poll-path cheapening (F10): stop re-opening the file for the 64-byte prefix probe on every 250ms poll where a cheaper identity check suffices.
- [ ] S6.T Tests: attach a large synthetic transcript and assert loop responsiveness (use the `until` settle helpers, no fixed sleeps); rewrite-detection regression.

### W2.5 - live-tier repair (added from the D1 soak findings; parallel with S3-S6, lands before D2)

The three live-tier failures D1 recorded are pre-existing and none touch the supervisor; their files are disjoint from S3-S6, so this runs alongside Wave 2.

- [ ] W2.5.1 `request_land` live coverage: fix the `_spawn_agent() cwd` TypeError (introduced ef9ccb9) so `test_request_land_enqueues_the_callers_own_worktree` executes at all, then run it on the live wire for every harness - it has never once run, so treat what it finds as a fresh result, not a regression.
- [ ] W2.5.2 opencode canary diagnosability: stop sending the CLI's stdout/stderr to `DEVNULL` in `_run`; capture bounded output into the failure message, then diagnose the intermittency from actual evidence.
- [ ] W2.5.3 codex subagent drift: the canary consistently finds `tool_use`/`tool_result` but no `subagent_activity` - investigate whether current codex stopped emitting those records, and if so adapt swe-mux's subagent-visibility detection to the new transcript shape and update the canary to match. This is potentially a live product defect, not a test fix; report the investigation outcome either way.

### D2 - deploy checkpoint (primary; no reap)

Precondition: W2.5 landed, so the live tiers D2.2 exercises are trustworthy.

- [ ] D2.1 Full gate on master, `redeploy_desktop.py` (normal session-preserving flow).
- [ ] D2.2 Live soak: UI regression pass on desktop and mobile (fleet refresh under a simulated hung endpoint, palette, sidebar tick); MCP `scan_search` against a >2000-record project; a land-queue cycle end-to-end; confirm retention runs without visible stalls.

## Wave 3 - cross-cutting quality (S7-S10 in parallel, then D3)

All four build on S3/S4 structure; conflicts between them are minimal because S3 moved the surfaces apart.

### S7 - diagnosability (logsetup, middleware; F5, F25)

- [ ] S7.1 Structured sink: JSON (or key=value) formatter that serializes `extra` fields, so the correlation data call sites already write reaches `daemon.log`.
- [ ] S7.2 Request correlation: contextvar request-ID middleware, ID returned in a response header and included in every log line; carry into subprocess/service logs where operation IDs already exist.
- [ ] S7.3 Typed error translation (F5): introduce a `NotFound(KeyError)` (or typed domain exceptions) for the 30+ deliberate raise sites; let bare `TypeError` reach the 500 path; log both translation paths at debug with method, path, and request ID; stop echoing raw key reprs in 404 bodies.
- [ ] S7.T Tests: formatter round-trip of extras; middleware test proving an accidental `KeyError` from a handler bug 500s with a traceback while a deliberate `NotFound` 404s; request-ID presence test.
- [ ] S7.D Docs: logging section of the relevant technical doc.

### S8 - subprocess and process consolidation (usage, provider_accounts, git_monitor, harness, agent_environment, processes)

- [ ] S8.1 Shared bounded runner (F/codex-G): extract the `worktree_exec` pattern (chunked bounded read, truncation reporting, timeout and `CancelledError` process-tree reap, correlation) into one helper; migrate `usage.py`, `provider_accounts.py`, `git_monitor.py`.
- [ ] S8.2 `probe_cli_version` unification (F22): one implementation (keep the shim-recursion-safe `which_real` behavior), one cache policy, both call sites.
- [ ] S8.3 `snapshot_all` grouping (F20): build the `session_id -> processes` index in one pass and serialize each process once.
- [ ] S8.T Tests: runner cancellation-reap test; output-cap truncation test; version-probe cache test; snapshot projection equivalence test.

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

### S11 - dependencies and licensing

- [ ] S11.1 num2words (F15): depend on `misaki[en]` (or add an explicit LGPL allowlist entry plus notice text) and update the roadmap license-gate wording; do not fork misaki.
- [ ] S11.2 voice-local extra (F16): move the voice/NLP closure (spacy, misaki, onnxruntime, faster-whisper, en-core-web-sm, num2words chain) behind an extra; `redeploy_desktop.py` preflight must assert the extra is installed before building, or the frozen bundle silently ships without voice.
- [ ] S11.3 Vendor cleanup (F28): remove the 13 unreferenced `frontend/vendor/continuity-editor-*.tgz`.
- [ ] S11.4 Line endings (F28): `.gitattributes` entries (`.worktree-verify`/`.worktree-setup` `text eol=lf`) and renormalize.
- [ ] S11.5 `git_provenance_backfill.py` (F28): move to a tools/ location or document it as a one-shot migration so it stops reading as dead code.
- [ ] S11.T Verification: fresh `uv sync` matrix (base, `--extra voice-local`, desktop) each building and starting; license inventory check against the roadmap gate.

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
