# Code quality audit - 2026-08-23

Scope: evaluation of the codex "Audit Codebase" session (mux session `e5d0c5cd`, parent thread `01a02f74-8c11` with three subagents), followed by an independent Claude audit.
Method: five parallel read-only agents - three adversarially re-verified every codex claim against current source, two swept areas codex did not cover (asyncio hygiene, security surfaces, history/FTS, observation, WebSocket paths, dependencies/licensing).
No files were changed by either audit.

## Verdict on the codex audit

Accuracy is very high: of ~20 verifiable claims, none was fabricated, and every cited mechanism exists in the code at or near the cited lines.
The errors are calibration errors, not factual ones.

Scorecard:

| Verdict | Count | Items |
|---|---|---|
| Confirmed at stated severity | 13 | Supervisor connection-loss P0, spawn-duplicate, fleet-refresh freeze, scan-search cap, test-substring classifier, retention lock hold, voice eviction, prompt-queue renumber, log `extra` loss, middleware masking, bundle size (byte-exact), CodeEditor double-serialize, tests excluded from typecheck |
| Confirmed but severity overstated | 5 | Stale-discovery kill (P0 -> P2), land-event atomicity (P1 -> low-medium), subprocess consolidation (P1 -> medium-low), 5s clock "invalidates entire root" (terminal panes are shielded by memo comparator), snapshot_all O() (mechanism real, scale makes it noise) |
| Partly wrong | 2 | "No cancellation cleanup" in subprocess callers - all three DO reap the process tree on timeout; only the `CancelledError` path leaks. "Sync tests marked async" - refuted; all 547 `@pytest.mark.asyncio` marks sit on async defs (they are merely redundant under `asyncio_mode = "auto"`) |
| Environment-specific | 1 | ".worktree-verify failed locally at `set -o pipefail`" - the file IS checked out CRLF (`.gitattributes` covers only `*.sh`), but this host's Git Bash tolerates CR; the failure reproduces only under stricter bash (WSL dies at the shebang). The codex report never reconciled this with its own green gate run |

Notable severity corrections with the evidence that drove them:

- Stale-discovery kill (their P0 #3): requires an unclean supervisor exit AND PID reuse onto a "swe"-named process AND an operator running `swemuxd --shutdown`.
  It is real and worth fixing, but it is operator-triggered with a triple-coincidence precondition: P2.
  Codex also missed that the discovery file already records `started_at` (supervisor.py:179) - the guard they recommended building already has its data persisted, making the fix one line, not a schema change.
- Land state/event atomicity (their P1): the state machine never reads `land_events` for correctness; partial unique indexes enforce idempotency and one-inflight-per-trunk at schema level, and `restore()` re-derives from the repository after a crash.
  The gap is audit-trail fidelity only: low-medium.
- Subprocess callers (their P1): `usage.py`, `provider_accounts.py`, and `git_monitor.py` all call `reap_process_tree` on timeout; the claimed timeout leak does not exist.
  The remaining gaps (buffer-then-check on `usage.py`, no cap at all on the other two, `CancelledError` leak) are real but medium-low.

### Process critique

What codex did well:

- Read the docs routing table, design docs, roadmap, and git history before touching code, and used them: its defense of `session.py`'s size as documented co-location is correct and matches `.docs`.
- Ran the full verification gate for real (4,383 backend tests, ruff, mypy, tsc, 1,811 frontend tests, renderer suite) and respected the renderer port-collision trap by picking an isolated port.
- Every finding carried evidence, root cause, and remediation; the report separated deliberate trade-offs from defects and listed practices worth preserving.
- The parent compiled its three subagents' reports faithfully; nothing was invented during compilation.

What codex got wrong or missed:

1. Severity inflation on three of nine P0/P1 items, all in the same direction, and all from not asking "what actually consumes this state" (land_events) or "what already guards this" (timeout reaping, discovery `started_at`).
2. Findings missed inside files it audited deeply:
   - `supervisor_client.py` head-of-line blocking (finding N1 below) - arguably amplifies its own P0 #2, in the exact file its runtime agent spent the most time in.
   - The `session.tasks` leak (N2) in `session.py`, which it read extensively.
   - The scan-records `ORDER BY t0` with no supporting index, and LIKE-pattern injection in the same function its domain agent confirmed the 2,000-cap bug in.
   - The PTY exit-sentinel 2s give-up (its own finding 10 was worse than it said: a lost sentinel leaves the supervisor lingering forever with a phantom-alive session).
3. Coverage gaps: no security-surface pass, no asyncio-hygiene sweep, no history/FTS, observation, or WebSocket audit.
   Those areas turned out to be mostly sound (see below), but an audit cannot know that without looking; a fourth subagent lane would have completed the sweep.
4. An unreconciled internal contradiction (CRLF gate failure vs. its own green gate run) shipped in the final report.
5. Compilation lossiness: the frontend subagent reported eight findings; the compiled report carries five or so, and at least one dropped item (fail-fast `Promise.all` discarding all five slices on any single failure) is material because it compounds the retained P1.

## Independent audit findings

Everything below was verified against current source by this audit.
Codex-confirmed findings are folded in at corrected severity; items marked NEW were not in the codex report.

### P0 - fix before anything else

1. Supervisor connection loss falsely ends every live session.
   `RemotePtyHost.isalive()` is `self._alive and self.client.connected` (supervisor_client.py:196), the 1s ticker treats false as process exit (session.py:6951-6956), and `_mark_ended` durably persists it.
   `_on_connection_lost` (supervisor_client.py:445-470) computes exactly the needed "running unreachable" state and nothing reads it on this path; the codebase even documents the mechanism at session.py:7105-7108 for the deliberate-detach case.
   There is no reconnect; on the next daemon start, adoption resurrects sessions the UI recorded as ended.
   Untested: no test simulates socket loss with the supervisor alive.
   The `_read_loop` handler also catches ValueError, so a single desynced/malformed frame takes the same path.
   Fix: tri-state liveness; gate the ticker on `client.lost`; end sessions only on definitive `pty_exit` or confirmed supervisor death; add the missing test.

### P1

2. Ambiguous supervisor spawn can duplicate an agent process.
   60s RPC timeout (supervisor_client.py:41,409) -> broad `except Exception` fallback spawns in-process (session.py:2977-2981) while the supervisor's `_finish_spawn` completes unconditionally (supervisor.py:477-508).
   No cancel message exists, the late reply is discarded, no `remove` is sent, and next-boot adoption adopts the orphan as live: two agents can mutate one workspace.
   Reachable via a >60s ConPTY stall (Defender; the repo already raised redeploy health checks 300->600s for this) or via finding N1.
   Fix: idempotent spawn keyed by session id; on timeout/disconnect, query the supervisor before any fallback; protocol test that drops the reply after spawn succeeds.
3. NEW - client read loop head-of-line blocking couples the data and control planes.
   `_read_loop` awaits `host._queue.put(payload)` on one session's bounded queue (supervisor_client.py:427-434); while blocked, no RPC responses, output, or `pty_exit` are delivered for ANY session.
   One stalled consumer can push another session's spawn RPC past 60s and trigger finding 2, or time out stop/subscribe fleet-wide.
   The supervisor side already keeps the planes separate (per-connection high-water drain, supervisor.py:592-600); the client does not.
4. Fleet refresh can freeze permanently and is all-or-nothing.
   The five refresh GETs pass no `timeoutMs` (App.tsx:1556-1561), violating api.ts's own documented rule (api.ts:10-13); the in-flight dedupe (App.tsx:1549-1553) then pins every future refresh - interval, visibilitychange, WS reconnect - behind the hung promise until page reload.
   NEW aggravation: the `Promise.all` is fail-fast, so one transient 500 discards the whole fleet snapshot for that cycle.
   Fix: default deadline on all five, `Promise.allSettled` with per-slice application, and an abort/reset on the dedupe.
5. Transport middleware masks programming defects as client errors.
   KeyError -> unlogged 404, ValueError/TypeError -> unlogged 400 (server.py:555-606).
   The KeyError convention is deliberate (30+ intentional `raise KeyError` sites), but an accidental one is indistinguishable, the 404 body leaks the missing key's repr, and TypeError -> 400 is almost always a bug being hidden.
   Fix: typed domain exceptions (or a `NotFound(KeyError)` subclass), let bare TypeError reach the 500 path, and at minimum a debug-level log line on both translations.
6. Semantic scan search silently drops the newest records past 2,000.
   `scan_records` defaults oldest-first with a 2,000 cap (automation_store.py:1206-1208); neither search caller passes `newest_first=True` (mcp.py:3937, server.py:12490); the post-filter re-sorts newest-first so output looks correct while drawn from the oldest page.
   No truncation signal.
   The 365-day durable retention makes >2,000 records routine.
   Fix (one line each): pass `newest_first=True`; report truncation; longer term, FTS or SQL-side paging, plus an index for the unindexed `ORDER BY t0`.

### P2

7. Stale supervisor discovery can kill an unrelated "swe"-named process on `swemuxd --shutdown` (supervisor_client.py:587-614).
   One-line fix: validate against the `started_at` the discovery file already records, matching the PID+creation-time policy used everywhere in processes.py.
8. Supervisor teardown does not quiesce in-flight spawns (`_background_tasks` neither cancelled nor awaited, supervisor.py:205-220); a spawn racing `reap_all_and_exit` can land a child in no job, escaping the kill-on-close reap.
9. NEW - `session.tasks` grows unbounded on the OSC7/cwd-telemetry paths.
   session.py:6709-6713 and 6884-6891 add tasks with no discard callback, unlike every sibling site; a shell emits OSC 7 per prompt, so a long-lived shell leaks a dead Task per prompt on a daemon meant to run for weeks.
10. NEW - transcript attach replay reads and JSON-decodes the whole file synchronously on the event loop (observation.py:821-870); multi-tens-of-MB Claude transcripts mean loop stalls of hundreds of ms to seconds per attach/rebind, plus a 64-byte re-open per observed session every 250ms poll.
11. Settings Save is non-atomic and can report "nothing was changed" after the keybindings PUT committed (Settings.tsx:944-958); pre-validation mitigates one direction only, and a `_revision` conflict from another device triggers the false message.
    Adjacent NEW: "Restore defaults" is a one-click unconfirmed destructive action whose failure is a silent unhandled rejection (Settings.tsx:1246, 961-964).
12. Retention holds the process-wide mux.db operation lock across full-scan deletes on ~13 tables in one transaction (automation_store.py:2417-2425); no prune table has a leading `created_at` index, several have none at all.
    Scoped correction vs codex: the scans cover the automation tables, not the whole 2.73GB file - seconds, not minutes.
    Fix: per-table ops with bounded rowid batches, commit between batches.
13. Supervisor frames: unbounded `plen` accepted and payload fully read before auth (supervisor.py:64-73, 244, 277); loopback-only and token-gated, so hardening rather than exposure.
    Any inbound cap must stay above the 5MiB scrollback replies the client legitimately receives.
14. PTY exit-sentinel enqueue gives up after 2s with `except Exception: pass` (pty_host.py:209-213) while data writes wait indefinitely; a lost sentinel means no `pty_exit` ever, a phantom-alive session, and a supervisor lingering forever.
    Read-side `PtyError` swallowing (pty_host.py:166-189) has no log or counter.
15. num2words (LGPL-2.1+) is a direct dependency (pyproject.toml:27) that nothing imports - it exists for misaki's English G2P.
    The roadmap's planned license gate names pystray as the only LGPL allowlist entry and would fail on it.
    Cheapest compliant fix: second allowlist entry plus notice text, or depend on `misaki[en]` instead; the codex fork-misaki-and-adapt-inflect plan is heavier than the problem.
16. Voice/NLP stack (~290MiB measured: spacy, ctranslate2, onnxruntime, misaki, en-core-web-sm, faster-whisper) is in base deps; a `voice-local` extra is cleanly separable (imports confined to voice.py, kokoro_tts.py, llm_endpoint.py).
    Interaction warning: the frozen build collects spacy/misaki via collect_all, and a voice extra adds a second partial-env failure mode - redeploy preflight must assert the extra is installed.
17. Initial bundle is 3.38MB raw / 1.06MB gz with static imports of ~28 CodeMirror grammars, Sigma, and Graphology (codeLanguage.ts:13-41, ChangeMapPane.tsx:2-3); splitting exists only for GitDiffView and the ONNX/VAD stack.
    Lazy-load the resource editor and change map; load only the selected grammar.

### P3 and hygiene

18. Prompt-queue tail append renumbers every visible row it just read (prompt_queue.py:526-592) - O(n) writes per append for values already correct; only anchor-inserts need it.
    Adjacent NEW: the correlation dedup comment (prompt_queue.py:236-241) claims the partial unique index enforces what NULL `sender_id` rows actually escape; the SELECT-before-INSERT is the real guard.
19. Voice eviction: per-group full scans via unindexed `COALESCE(stream_id,id)` (voice.py:1044-1053); `WHERE stream_id=? OR id=?` uses both existing indexes.
20. `snapshot_all` per-session scans over owned processes (processes.py:1395-1424); one-pass grouping is a one-liner cleanup, not a perf fix at real fleet sizes.
21. Preview JS rewriting is lexical: `_JS_ROOT_SPECIFIER` (server.py:13566) rewrites `from '/x'` / `import '/x'` occurrences inside ordinary strings and comments; narrow surface, and the Tree-sitter stack is already a dependency if it ever bites.
22. NEW - DRY: two divergent `probe_cli_version` implementations (harness.py:2012 vs agent_environment.py:457 - different timeouts, TTLs, resolution); nested job-object creation duplicated near-verbatim (supervisor.py:487-497 vs session.py:3008-3021) including the string contract forensics rely on; `mcp.py:4009` builds doc ownership uncached while deterministic_consumers caches it.
23. NEW - LIKE-pattern injection inconsistencies: automation_store.py:1200-1205 interpolates `target_fragment` with no ESCAPE; history.py:3154-3155 skips the module's own `_escape_like` helper.
24. NEW - per-key asyncio.Lock dicts never evicted (scan_timeline.py:769,990; assistant.py:3673; project_card.py:451); `mcp.py` handle_rpc catches base KeyError, masking handler bugs the typed `ScopeMiss(KeyError)` exists to distinguish (mcp.py:4622-4636); timed-out transcript parses keep running in the default executor while the caller is told to retry (mcp.py:2486-2505).
25. Durable log format drops every `extra` field (logsetup.py:30,52) - the correlation instrumentation call sites already write is discarded at the sink, and there is no request-ID correlation.
    This is a direct gap against the project's own logging standard; a JSON formatter plus contextvar request IDs closes both.
26. Test-file classifier uses `"test" in path` (mcp.py:3988-3996) - `latest.py` counts as a test and suppresses test-gap findings in the unsafe direction.
27. Config complexity is real but concentrated: `Config._validate` C901=169, 192 functions >10, 10 >30.
    Disagreement with codex: adopting Pydantic for Config is not the right trade - the 169 is a deliberate single-choke-point (its own comment says so), mypy strict already covers types, and Pydantic would put a heavyweight dependency on the config hot path.
    Ratchet C901 (cap at current worst, tighten over time) and table-drive the range checks instead.
    Declaring pydantic explicitly is only warranted if something starts importing it.
28. Hygiene batch: add `.worktree-verify` / `.worktree-setup` `text eol=lf` lines to .gitattributes (currently CRLF on checkout; works under Git Bash by luck, dies under WSL bash); delete 13 of 14 tracked `frontend/vendor/continuity-editor-*.tgz` (~5.9MB dead, only 0.2.36 referenced); migrate aiohttp string keys to `web.AppKey` (~632 uses, zero AppKey - the bulk of the test-run warning noise); the 547 `@pytest.mark.asyncio` marks are redundant under `asyncio_mode="auto"` but harmless; 184 frontend unit-test files are typechecked by neither tsconfig, and 37 assert on source text via readFileSync - fragile in both directions; move or document `git_provenance_backfill.py` (one-shot migration script that reads as dead code).

### Verified sound - do not churn

- Asyncio hygiene repo-wide: only 3 `except Exception: pass` sites, all cosmetic teardown; loops run under the two-layer TaskSupervisor; ad-hoc tasks carry discard callbacks (finding 9 is the exception proving the pattern).
- Security surfaces: `project_path` containment defeats absolute, `..`, drive-relative, and symlink escapes; all token comparisons use `compare_digest`; destructive endpoints require loopback peer plus gesture headers; MCP results are byte-bounded with versioned cursors.
- History/FTS: correct external-content trigger protocol, fully neutralized user FTS syntax, watermark-gated indexing, graceful LIKE fallback.
- PTY WebSocket path: credit-based flow control, batches never crossing control frames, cancellation-safe cleanup ordering.
- Atomic writes: tmp + `os.replace` at every config/settings/manifest write site checked (~20).
- Libraries: aiohttp, stdlib SQLite, Preact, CodeMirror, psutil, watchfiles, Sigma/Graphology, platform PTY backends all fit; frontend dependency list has zero dead entries.

## Recommended execution order

1. P0 finding 1 (tri-state liveness + ticker gate + test).
2. Findings 2 and 3 together - idempotent spawn is only trustworthy once the control plane cannot be starved by the data plane.
3. Finding 4 (frontend deadlines + allSettled) and finding 6 (two one-line scan-search fixes) - highest value per line changed in the report.
4. Finding 7 (one-line discovery guard) and finding 5 (middleware logging), then the P2 batch opportunistically.
5. Hygiene batch (28) as a single sweep; ratchet gates (C901, warnings) after the AppKey migration so the ratchet starts from a clean floor.
