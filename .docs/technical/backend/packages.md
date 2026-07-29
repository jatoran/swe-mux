# Backend package responsibilities

## Composition boundary

`src/swe_mux/server.py` is the aiohttp composition root. It creates stores/managers, wires
background workers, validates transport input, and translates domain errors to HTTP/WS results.
It should call domain packages rather than acquire their storage or process responsibilities.

## Package map

| Package | Owns | Does not own |
|---|---|---|
| `desktop.py` | Windows tray/WebView lifecycle, single instance, login startup, daemon child supervision | PTYs, HTTP composition, Project/session state |
| `__main__.py` | daemon argument/config resolution and reusable aiohttp site lifecycle | desktop window/tray state |
| `session.py` | live session registry, immutable root-provider identity, nested promotion/demotion, transcript ownership, spawn/stop, PTY fanout, bounded replay, interactive vs one-shot exit lifecycle, supervisor-session adoption/repair | provider transcript parsing, Project mutation |
| `pty_host.py` | ConPTY/process creation, resize, low-level I/O, root exit status, dead-host release | HTTP, SQLite, layout |
| `terminal_arbitration.py` | pure multi-device rules for one shared PTY: input-ownership claims (gesture beats passive, passive cannot displace an actively typed-into owner or come from a hidden window), epoch/release bookkeeping, and the arbitrated geometry (owner's viewport, else the smallest visible one) | sockets, PTYs, telemetry — `session.py` holds the state and `server.py` applies the decisions |
| `supervisor.py` | standalone PTY supervisor process: ConPTY + read-loop + authoritative scrollback ownership, reaper Job, loopback IPC server, discovery file, single-instance mutex, reap-all teardown | HTTP composition, SQLite, orchestration, observation — anything volatile |
| `supervisor_client.py` | daemon-side supervisor connection (framing/RPC/output dispatch), `RemotePtyHost` PtyHost facade, discover-or-spawn, metadata mirroring, `kill_server` | ConPTY creation, session registry |
| `scrollback.py` | byte-exact scrollback ring (append/seed/replay cursoring) shared by daemon sessions and the supervisor | subscribers, persistence |
| `subprocess_flags.py` | consoleless flags for daemon-owned Windows background commands; `popen_outside_job` breakaway spawn (with plain-spawn fallback) for children that must outlive any inherited Job object | interactive ConPTY children |
| `lifecycle.py` | daemon death forensics: rewritten heartbeat record, append-only lifecycle ledger, unclean-death detection at startup, clean-exit marking | logging configuration, process supervision |
| `background_tasks.py` | supervision and health for the daemon's long-lived loops: per-iteration fault guard, restart with capped backoff, per-loop health snapshot | PTY host processes (that is `supervisor.py`), any domain logic |
| `logsetup.py` | rotating `daemon.log`/`access.log` handlers, faulthandler `crash.log`, runtime root-logger level control | the supervisor's logging (stdlib-inline there to keep its closure frozen) |
| `build_support.py` | lock-safe staged frontend publication for desktop packaging | Vite compilation, runtime asset serving |
| `projects.py` | Project/Group validation and lifecycle | Git-derived identity, file content |
| `project_files.py` | safe Project config, notes, tree, bounded recursive name/content search, file reads/writes | layout placement, browser drafts |
| `project_watcher.py` | leased non-recursive directory watches | recursive Project crawl |
| `project_actions.py` | inert task import, normalization, exact fingerprint trust, per-step spawn requests (shell quoting, PATH/shim resolution) | automatic execution, UI placement, session ownership |
| `project_init.py` | user-authored setup commands from the daemon config: catalog, id selection in configured order, one step per command | trust fingerprints, repository reads (there are none), spawn execution |
| `spawn_contract.py` | spawn field validation: bounded env, cwd containment, Claude marker scrubbing | project ownership (the caller supplies the root) |
| `history.py` | shared schema, Project/layout persistence, run history, search index | live PTY lifecycle |
| `history_backfill.py` | bounded cancellable complete-history jobs | durable job scheduling, native file mutation |
| `transcript_view.py` | bounded Claude/Codex conversation parsing | process state, transcript writes |
| `layouts.py` | layout-v6 validation and migrations | UI focus or drag state |
| `operational_telemetry.py` | process/quota/reset/context/tool evidence; provider-evidence reset after proven session-identity repair | credentials, automatic process killing |
| `provider_accounts.py` | saved auth snapshots, explicit switching, safe quota reads | concurrent provider homes |
| `voice.py` | completed-reply TTS segments, bounded Whisper STT with GPU/CPU fallback, temporary audio lifecycle, voice-submit idempotency | browser microphone permission, PTY ownership |
| `tailscale.py` | direct-tailnet discovery/status and ephemeral certificate preparation for the daemon's direct private HTTPS listener | ACL/policy changes, Serve/Funnel enablement, browser permission |
| `processes.py` | descendant inspection/actions; Project-wide loopback registration, discovery, listener attribution, and route maps | proxy transport, authoritative ownership from PID alone |
| `adapters/` | provider command/resume/transcript/state normalization | public HTTP shapes |
| `automation_registry.py` | control-plane enablement DAG: substrate/consumer deps, cycle-checked resolution | storage, execution |
| `tier0_store.py` | deterministic no-model fact capture (Tier 0 substrate), gated per-project, source pointers, run/project fact queries | model calls, actuation |
| `deterministic_consumers.py` | model-free detectors over Tier 0 (loop/stall, declared-vs-verified, doc debt, provenance edges) and the turn-boundary runner | model calls, spend, anything that writes toward a session |
| `project_card.py` | per-Project distilled architecture card (CP substrate step 4): bounded `.docs` source gather, deterministic Key-files → area inversion, content fingerprint + cache validity, one budgeted cheap-model distillation, rendered prompt prefix | consumers of the card, any fallback when a provider is unavailable (there is none — no card), HTTP, UI |
| `mcp.py` | agent-facing MCP protocol + tools: four read tools (session list/status, bounded transcript read, history search) and two thin write callers (`notify`, `request_spawn`), token-derived caller identity, Project scoping, output redaction | relay policy and bounds (those live in `agent_messaging.py`), delivery, PTY writes, spawn, aiohttp handlers (`server.py`) |
| `prompt_queue.py` | persistent prompt queue: durable message store (states, strict head-of-line, revisions, sender provenance, correlation, relay depth), typed operations (enqueue/edit/arm/move/cancel/retarget/schedule/send-next), delivery constraints, auto-policy + proving-counter tables, event-driven stranding + startup reconcile, delivery audit, seed-prompt staging (`stage_seed_argv`) | *when* an automatic send happens (`auto_delivery.py`), who may address whom (`agent_messaging.py`), PTY ownership (delivery writes go through the injected operator-input helper), aiohttp handlers |
| `auto_delivery.py` | the gate on automatic sends: master/per-session opt-ins, run binding, expiry, consecutive cap, stability window over `delivery_state`, quiet hours, persisted emergency pause, expiry sweep, proving-period counters and `promotion_status` | delivery itself (calls `send_next`, cannot pass `confirm`), readiness evaluation, HTTP |
| `agent_messaging.py` | relay policy for agent-authored messages (Project scope, size, per-origin budget, target backlog, chain depth, cycle detection, kill switch, expiry), inert `spawn_request` drafts, mailbox projection | delivery, spawning (approval is a `server.py` human act), MCP protocol |
| `preview_capture.py` | optional headless preview screenshot (Playwright), typed-unavailable | proxy transport, PTY writes |
| `clipboard_store.py` | in-memory clipboard-history ring (dedupe by content hash, pins, count/time bounds, secret-shape refusal) plus its opt-in SQLite mirror | reading or polling the OS clipboard, deciding where inserted text goes |

Feature stores sharing `mux.db` use their own single-worker executor/connection and the common
operation coordinator described in `sqlite.md`.

## Dependency direction

Transport may depend on managers/stores; managers may depend on adapter and persistence
contracts; platform modules remain below both. Provider-native shapes stop at adapter/parser
boundaries. Browser response models are assembled at the transport boundary.

Correct:

```python
# server.py validates the Project and delegates the state transition.
session = await manager.spawn(project_id=project.id, profile_id=profile_id)
```

Incorrect:

```python
# A route must not open mux.db directly or duplicate a store transaction.
sqlite3.connect(data_dir / "mux.db").execute("UPDATE projects ...")
```

## Background-work rules

- Blocking ConPTY creation, filesystem scans, Git probes, and SQLite work stay off the asyncio
  event loop.
- **Every long-lived loop is supervised.** A bare `while True:` dies permanently on its first
  uncaught exception with no log at failure time, no restart, and no health signal — and this
  daemon is designed to run for weeks behind the PTY supervisor, so that is not a degradation
  but a feature that silently stops. Two layers, both required (`background_tasks.py`):
  wrap the *iteration* in `background.iteration(name)` so a transient fault costs one cycle and
  is attributed, and start the *loop* with `background.start(name, factory)` so anything that
  escapes anyway is restarted with capped backoff. `factory` must be a factory, not an
  already-created coroutine. Health is surfaced at `GET /api/diagnostics/background`; a new
  loop that appears there with `running: false` is the diagnostic that used to be missing.
  `tests/test_background_tasks.py` sweeps the source for unguarded `while True:` and fails
  on any new one. A loop that genuinely should not join the registry — scoped to a single
  connection or session, or already guarded in place — opts out with an inline
  `# unsupervised-loop-ok: <reason>` marker, so the exemption is reviewable where it lives
  instead of in a list that quietly grows.
- The lifecycle heartbeat is supervised for a sharper reason than the rest: its death is
  indistinguishable from the daemon's. One failed write used to end it silently, after which
  the daemon kept running normally while every later start reported it as "died without a
  clean shutdown" — a false forensic that sends the next investigation after a crash that
  never happened.
- A `stat()` in a polling watcher belongs inside the try. Editors save by delete+rename, so
  `exists()` and `stat()` genuinely disagree; treat the failure as "unchanged".
- The PTY read path must never fabricate end-of-output. A cross-thread handoff that cannot
  complete is backpressure (the correct response is to keep waiting while the child is alive),
  and `b""` reaching the supervisor is checked against `isalive()` before it is believed —
  the removal path closes a kill-on-close job, so a fabricated exit kills a live agent tree.
- Interactive readiness and durable registration are distinct. Once a ConPTY is usable, publish
  the in-memory session and return; serialize history registration behind it.
- Keep root-process identity separate from active nested-agent identity. Promotion is a
  `shell → agent` transition, matching demotion returns only that promoted shell, and every
  transcript candidate is checked against other live native/path claims. Supervisor snapshots
  mirror mutable observation state, not authority: adoption reasserts immutable spawn identity,
  repairs legacy conflicts, and quarantines any history run proven to contain sibling evidence.
- Conversation identity is a third axis beneath both: an in-CLI `/clear`/`/new` keeps the PTY
  and the root identity while replacing the provider conversation, so it is a **new agent run**
  (`_apply_conversation_rollover`), never an in-place rekey. Stop the observer before rewriting
  that identity — it re-derives `native_session_id` from the file it is tailing at the top of
  every loop, so a cancellation that has not landed will put the retired id back. The observer's
  own switch path applies the rollover in place for the same reason (calling the public entry
  point would cancel the caller). Adoption treats `agent_run_id != session id` on a root agent as
  corruption; `agent_run_seq > 0` is the marker that exempts a legitimately rolled run.
- A one-shot Project Action spawns its target directly under the supervisor's ConPTY: the shell
  for a `shell` step, the PATH-resolved program (or `%COMSPEC%` for a `.cmd`/`.bat` shim) for a
  `process` step. **No swe-mux executable may appear in a task's process tree.** One did once
  (`swe-mux-action.exe`, built into `dist/swe-mux`), and a live task terminal then held that
  directory open and blocked the redeploy swap. Exit code zero maps to completed/exited; nonzero
  remains crashed and observable.
- Step `cwd` and `env` travel as spawn-request fields, not as an encoded argv payload. A relaunch
  replays them from the record (`spawn_cwd`, `spawn_env`), because neither is recoverable from the
  argv alone.
- Frozen Windows startup may surface pywinpty's private `pyo3_runtime.PanicException` once with
  `ERROR_SEM_NOT_FOUND`; `pty_host.py` retries only that exact allocation panic with a strict bound.
  Other `BaseException` values retain normal control-flow semantics.
- PTY attach/input paths never wait for observational event persistence.
- Natural root exit captures status before detaching the dead ConPTY from the retained session.
  Final output drains through the reader's local handle; finalization cancels a frozen read after
  root exit so that local reference cannot leak. `pty_host.py` also binds pywinpty's daemon-sibling
  `OpenConsole` helper (or its delayed frozen-build `conhost` replacement) by creation time and
  revalidates PID, creation time, executable, and parent before reaping it. Durable/in-memory
  scrollback, not an OS pseudoconsole reference, supplies ended-session replay.
- Every poller/scan has an explicit bound, cancellation/stop path, freshness contract, and
  unavailable result. Optional integrations cannot make terminal operations fail.
- **`asyncio.to_thread` does not make psutil work free.** Its Windows calls are C extension
  calls that hold the GIL, so a long sampling pass in a worker thread starves the event loop
  just as a blocking call would — it only stops looking like a blocking call. `processes.py`
  once rebuilt every `psutil.Process` and re-read every `cmdline()` on each 5-second tick,
  costing ~930-1110 ms per pass; the observable symptom was terminal keystrokes lagging and
  then arriving in a burst, plus a daemon that idled at 15-19% CPU. The rule for any periodic
  psutil work: take **one** system-wide snapshot per pass (`_ppid_map()`, not
  `children(recursive=True)` per root), cache process handles across passes, and re-read only
  attributes that actually change per tick — never name, command line, or creation time.
  Expensive-but-honest metrics (unique set size) are opt-in per request, never on the cadence.
  See `design/features/processes-and-previews.md` §Sampling cost.
- Voice STT/TTS subprocesses and local models stay off the event loop. Incoming WAV duration,
  encoding, and bytes are validated before transcription; temporary utterances are deleted on
  success, error, or cancellation.
- Desktop presentation and daemon lifetime remain separate processes. Close/minimize hides the
  WebView; only authenticated loopback Quit stops the daemon. Never expose shutdown through the
  ordinary remote-control authority.
- The PTY supervisor (`supervisor.py`) must stay small and near-frozen: it cannot be hot-updated
  without killing every live session, so volatile code belongs in the daemon. It imports only
  `pty_host.py`, `scrollback.py`, and `win_jobobj.py`. In supervisor mode the daemon must never
  assign a supervised PID to a daemon-held Job handle (that would reap agents on daemon exit);
  nested per-session Jobs are created supervisor-side. Shutdown intent comes from outside the
  daemon: quit reaps through `reap_all_and_exit`, detach only flushes mirrored session metadata.
- The frozen supervisor ships as its own bundle (`dist/swe-mux-supervisor`), never inside
  `dist/swe-mux`, so app rebuilds cannot collide with a running supervisor's image. Keep the
  supervisor's import closure inside the hash-gated source list in `packaging/build_desktop.py`;
  adding an import to `supervisor.py`/`pty_host.py` without updating that list ships a stale
  bundle. Daemon self-restart (`/api/daemon/restart`) must spawn the successor with
  `--relaunch-wait` and detach intent; it is refused without an attached supervisor unless
  forced, because an unpreserved restart is a session-killing action. The frozen-app redeploy
  (`/api/daemon/redeploy`) follows the same authority rule and must spawn
  `packaging/redeploy_desktop.py` detached from the daemon's process group and lifetime (the
  script stops this very daemon mid-run), with cwd at the source root — never inside `dist/`
  — and the child env scrubbed of parent-Claude session markers.
- Windows Job membership is inherited by every descendant, and the supervisor's per-session
  Jobs are kill-on-close: anything (re)launched from a shell inside a session dies silently
  when that session is removed. The session Jobs therefore set `JOB_OBJECT_LIMIT_BREAKAWAY_OK`
  (containment is unchanged — escape is opt-in per spawn), and every relaunch that must outlive
  sessions (supervisor spawn, daemon successor, tray→daemon, redeploy script and its app
  relaunch) goes through `subprocess_flags.popen_outside_job`, which requests
  `CREATE_BREAKAWAY_FROM_JOB` and falls back to a plain spawn if denied (pre-BREAKAWAY_OK Jobs).
  `CREATE_NEW_PROCESS_GROUP` does **not** escape a Job — never treat it as detachment. The
  daemon, tray, and supervisor each call `win_jobobj.process_in_job()` at startup and leave a
  loud warning when they find themselves inside a Job (the poisoned-launch breadcrumb).
- Nothing in-process can observe an external TerminateProcess, so death forensics live outside
  the daemon: `lifecycle.py` keeps `daemon-heartbeat.json` fresh (~10 s) and marks clean exits
  with their intent; the next daemon reports a predecessor whose record has no clean exit and a
  dead pid. The tray, which holds the daemon's process handle, ledgers the observed exit code.
  Rotating logs are per-process-owned files (`daemon.log`, `access.log`, `supervisor.log` via
  stdlib handlers); console redirects (`desktop-daemon.log`, `supervisor-console.log`,
  `daemon-relaunch.log`) stay append-only crash catchers, never shared with a rotating handler
  — the child holds the redirect handle for life, so rotation of the same file can never
  succeed.
- A Windows process's working directory locks that directory against deletion. Every
  long-lived swe-mux process (shell-spawned daemon, self-restart successor, supervisor) must
  anchor its cwd in the data dir, and the supervisor chdirs itself defensively at startup — a
  supervisor whose cwd landed inside `dist/` would silently block every session-preserving
  rebuild even though its own image lives elsewhere.
- Windowed builds must route only allowlisted internal module entrypoints through `desktop.py`;
  daemon-owned maintenance subprocesses use `subprocess_flags.py`, while interactive commands
  remain under ConPTY so suppressing console flashes never suppresses terminal output.
- Preview registration identity is Project endpoint, not clicked terminal. Resolve listener
  ownership across live sessions before attachment; do not weaken the iframe sandbox or let a
  browser dial raw loopback for cross-service traffic.
- Once a route has resolved an explicit Project, Project-resource helpers must receive that
  canonical identity (`_registered_identity(project)` → the `project=` keyword on
  `read_note`/`write_note`/`initialize_note`/`read_project_config`/`write_project_config`/
  the observation helpers). Git discovery answers "which worktree contains this path", which
  is the wrong question once the owner is known: a Project registered *inside* a larger
  worktree resolves to the enclosing toplevel, and every derived path — notes, config,
  observations — lands in the wrong Project. `git_projects.rebase_identity` re-anchors the
  root/scope while keeping repository-group metadata describing the real worktree.
- Detectors and observers are different tiers and must not blur. Anything under
  `deterministic_consumers.py` is a query over Tier 0 facts: no model call, no spend, no
  transcript interpretation beyond a literal claim pattern, and no output but annotations.
  A finding carries the *set* of facts it rests on, because a single event pointer cannot
  express "this repeated three times and nothing moved".
- `project_card.py` is the first substrate that spends, and it is the only one. Two rules keep
  that honest: it is lazy (nothing is built until a consumer asks, so an enabled project no
  one reads costs nothing), and it never degrades to a heuristic. Missing provider, missing
  key, provider error, empty answer, exhausted budget, undocumented project — every one of
  them yields *no card*, because a consumer prepending a wrong card is worse off than one
  prepending nothing. Its file → area map is copied verbatim from the docs, never routed
  through the model.

## Related design

- `../../design/architecture.md`
- `../../design/interfaces.md`
- `../../design/features/sessions.md`
- `../../design/features/history.md`
- `../../design/features/project-actions.md`
