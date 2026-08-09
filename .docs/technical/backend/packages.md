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
| `session_attachments.py` | trusted Project/worktree storage selection, filename normalization, image content classification, persistent `.swe-mux/attachments/` layout, atomic writes, and per-file/session quotas | HTTP multipart parsing, PTY insertion, provider parsing, retention cleanup |
| `pty_host.py` | ConPTY/process creation, resize, low-level I/O, root exit status, dead-host release | HTTP, SQLite, layout |
| `terminal_arbitration.py` | pure multi-device rules for one shared PTY: input-ownership claims (gesture beats passive; passive cannot displace an actively typed-into owner, come from a hidden window, or cross to the leading device class — and does cross from a trailing one when the claimant's class leads), epoch/release bookkeeping, and the arbitrated geometry (owner's viewport, else the smallest visible one). `server.py` records every decision in the session's bounded claim log and stops answering a connection's repeated passive claims for a second after refusing one | sockets, PTYs, telemetry, and *which* device is in use — `device_presence.py` answers that, `session.py` holds the state, `server.py` applies the decisions |
| `supervisor.py` | standalone PTY supervisor process: ConPTY + read-loop + authoritative scrollback ownership, reaper Job, loopback IPC server, discovery file, single-instance mutex, reap-all teardown | HTTP composition, SQLite, orchestration, observation — anything volatile |
| `supervisor_client.py` | daemon-side supervisor connection (framing/RPC/output dispatch), `RemotePtyHost` PtyHost facade, discover-or-spawn, metadata mirroring, `kill_server` | ConPTY creation, session registry |
| `scrollback.py` | byte-exact scrollback ring (append/seed/replay cursoring) shared by daemon sessions and the supervisor | subscribers, persistence |
| `subprocess_flags.py` | consoleless flags for daemon-owned Windows background commands; `popen_outside_job` breakaway spawn (with plain-spawn fallback) for children that must outlive any inherited Job object | interactive ConPTY children |
| `lifecycle.py` | daemon death forensics: rewritten heartbeat record, append-only lifecycle ledger, unclean-death detection at startup, clean-exit marking | logging configuration, process supervision |
| `background_tasks.py` | supervision and health for the daemon's long-lived loops: per-iteration fault guard, restart with capped backoff, per-loop health snapshot | PTY host processes (that is `supervisor.py`), any domain logic |
| `logsetup.py` | rotating `daemon.log`/`access.log` handlers, faulthandler `crash.log`, runtime root-logger level control | the supervisor's logging (stdlib-inline there to keep its closure frozen) |
| `network_usage.py` | daemon-boot/reset-window HTTP encoded-body and WebSocket application-frame counters by bounded route/channel/peer; compact JSON responses; dynamic text compression; metered WebSocket responses | packet, TLS, Tailscale, and HTTP-header overhead; durable raw request logs; quotas |
| `build_support.py` | lock-safe staged frontend publication for desktop packaging | Vite compilation, runtime asset serving |
| `bundle_locks.py` | who would block the frozen-bundle swap: exe/cwd anchors into `dist/swe-mux` from processes the redeploy cannot stop (the app's own image and its descendants are excluded), shared by the redeploy script's pre-build/pre-stop gates and the endpoint's `409 bundle_in_use` | stopping anything (reporting only), the swap itself (`packaging/redeploy_desktop.py`) |
| `projects.py` | Project/Group validation and lifecycle | Git-derived identity, file content |
| `git_review.py` | Project-scoped comparison-ref inference, exact worktree and relative-path validation, bounded worktree/commit summaries, numstat and porcelain parsing, commit graph reads, capped commit-message reads, patch snapshots, stale hashes, and typed review failures | network fetches, Git mutations, live-session polling, browser presentation |
| `project_files.py` | safe Project config, flat Project-note collection and legacy-note migration, lazy global Scratchpad storage, tree, exclusive leaf-only file/folder creation, bounded recursive name/content search, revision-checked text reads/writes, and allowlisted image inspection/content with byte, dimension, pixel, and frame limits | layout placement, browser drafts, generic browser MIME rendering, generic browser-file overwrite/move/delete operations |
| `agent_context.py` | read-only Project/global instruction and provider-memory inventory, fixed-path opaque source reads/reveal resolution, complete memory counts; normalized Project-root compare; preview/revision-guarded whole-file `CLAUDE.md` ↔ `AGENTS.md` sync; atomic replace and data-dir restore points | arbitrary browser-supplied paths, global-instruction or learned-memory writes, automatic sync, private Codex store formats, MCP exposure |
| `project_watcher.py` | leased non-recursive directory watches keyed by Project, exact root, path set, and watch id | recursive Project crawl, deciding whether a requested root is a listed Git worktree |
| `agent_skills.py` | read-only discovery of the CLIs' own skills: per-vendor roots (user / repo / plugin / bundled), `SKILL.md` frontmatter, Claude command files, Codex `agents/openai.yaml` policy, plugin enable-gating, shadowing, 10 s cache | writing or installing skills, speaking Codex's app-server protocol, enumerating Claude's compiled-in built-ins (impossible from disk) |
| `agent_environment.py` | bounded passive inventory of one live CLI generation: retained runtime options, documented built-ins, current skills, configured MCP, installed/configured plugins, hooks grouped by lifecycle event with their handler target and `swe_mux` ownership marked, custom agents, known policy keys, feature overrides, source drift, diagnostics, ten-second response cache, and one-hour version cache | starting or health-checking MCP, importing plugins, executing hooks, exposing hook command lines/arguments/inline shell bodies/environment/credentials, writing provider state, or claiming configured items are loaded/connected |
| `project_actions.py` | inert task import, normalization, exact fingerprint trust, per-step spawn requests (shell quoting, PATH/shim resolution) | automatic execution, UI placement, session ownership |
| `project_init.py` | user-authored setup commands from the daemon config: catalog, id selection in configured order, one step per command | trust fingerprints, repository reads (there are none), spawn execution |
| `spawn_contract.py` | spawn field validation: bounded env, cwd containment, Claude marker scrubbing | project ownership (the caller supplies the root) |
| `history.py` | shared schema, Project/layout persistence, run history, search index | live PTY lifecycle |
| `history_backfill.py` | bounded cancellable complete-history jobs | durable job scheduling, native file mutation |
| `transcript_view.py` | bounded Claude/Codex conversation parsing; the human-readable `conversation_view` reduction (CLI machinery classified out, agent turns merged, byte/message capped, own LRU) | process state, transcript writes, redaction (the reader is the machine's owner) |
| `observation.py` | provider hook/transcript normalization, root-turn state, supervisor-resumable 5 s approval stabilization with immediate delivery blocking, first/latest user-request capture, immediate `transcript_message` fanout, standing-activity evidence (including the three carriers one background-task completion rides, closed idempotently per task) | HTTP routing, title policy, transcript rendering, opening a `background_tasks` annotation from the PTY footer (that tier may only refresh) |
| `automation.py`, `automation_store.py` | bounded rule evaluation and observer lifecycle, including provisional/settled title state, retries, budgets, and append-only annotations | PTY writes, provider transcript mutation, browser presentation |
| `layouts.py` | layout-v6 validation and migrations | UI focus or drag state |
| `operational_telemetry.py` | process/quota/reset/context/tool evidence; provider-evidence reset after proven session-identity repair | credentials, automatic process killing |
| `status_timeline.py` | durable per-session detection timeline: `LedgerRing` (seq/run-id stamping + guarded sink nudge), write-behind batched drain into `status_timeline`, time-ranged/post-mortem queries, retention, `note_layer_reading` (on-change layer entries) | the transition contract itself (`apply_state_transition` never touches persistence), state decisions, HTTP handlers (`server.py`) |
| `provider_accounts.py` | saved auth snapshots, explicit switching, safe quota reads | concurrent provider homes |
| `voice.py` | completed-reply, manual, and application-text TTS streams with a coherent sentence-first clip and tracked segment-tail tasks; one-shot summary/verbatim overrides; bounded Whisper STT with GPU/CPU fallback; temporary audio lifecycle; compatibility voice-submit idempotency; one-use approval challenges bound to the current screen fingerprint | browser microphone permission, mounted-composer state, PTY ownership, approval-state classification |
| `device_presence.py` | which device class the human is at: per-`/events`-connection visible/focused plus interaction age, aggregated to active device classes plus the *leading* one (most recently touched, which breaks the routine both-active tie), and the "did anyone touch another device since this alert" question a deferred push turns on. Two consumers, for the same reason — notification routing and terminal-input arbitration both have to answer "is the user somewhere else" and neither can from its own per-subscription or per-session state. Fails open on every staleness path | push subscriptions, delivery, settings, terminal ownership |
| `push.py` | VAPID identity, subscriptions, per-endpoint focus presence, event→notification classification (including the running-work and startup suppressions), the routing plan (`notification_plan`), and both hold lifecycles: the `waiting` settle and the other-device deferral | which device is active (that is `device_presence.py`), notification preferences (`settings_store.py`), what counts as running work (`session.RUNNING_ACTIVITY_KINDS` owns it; this module restates the set and a test pins them equal) |
| `tailscale.py` | direct-tailnet discovery/status and ephemeral certificate preparation for the daemon's direct private HTTPS listener | ACL/policy changes, Serve/Funnel enablement, browser permission |
| `processes.py` | normalized whole-system CPU sampling; creation-causal descendant inspection/actions; versioned parent-walk/Job provenance; infrastructure reservation and ownership-conflict quarantine; Project-wide loopback registration, discovery, listener attribution, and route maps; reduced fleet projection for the browser watch; the `background_tasks` fast-clear (a descendant older than the annotation cannot be its task) | proxy transport, authoritative ownership from PID alone, deciding a process *is* a background task (it may only refute) |
| `ghost_windows.py` | Windows-only detection and off-screen parking of headless-browser windows that DWM composites while Win32 reports them hidden; the conjunctive sweep predicate and its memoized command-line verdicts | closing or terminating any browser, session state, non-Windows behavior, which browser stack an agent chooses |
| `adapters/`, `agent_launcher.py`, `hook_client.py`, `assets/omp_mux_hook.ts` | provider command/resume/transcript normalization; additive Claude/Codex lifecycle-hook launch wiring; packaged OMP in-process lifecycle extension; authenticated hook delivery/spooling | public HTTP shapes, bypassing provider hook trust/policy |
| `harness.py` | declared harness identity, capability axes, derived display level, delivery etiquette, tool catalogs, and hook event sets | adapter process behavior, provider parsing, state arbitration |
| `automation_registry.py` | control-plane enablement DAG: substrate/consumer deps, cycle-checked resolution | storage, execution |
| `tier0_store.py` | deterministic no-model fact capture (Tier 0 substrate), gated per-project, source pointers, run/project fact queries | model calls, actuation |
| `deterministic_consumers.py` | model-free detectors over Tier 0 (loop/stall, declared-vs-verified, doc debt, provenance edges) and the turn-boundary runner | model calls, spend, anything that writes toward a session |
| `project_card.py` | per-Project distilled architecture card (CP substrate step 4): bounded `.docs` source gather, deterministic Key-files → area inversion, content fingerprint + cache validity, one budgeted cheap-model distillation, rendered prompt prefix | consumers of the card, any fallback when a provider is unavailable (there is none — no card), HTTP, UI |
| `mcp.py` | agent-facing MCP protocol + tools: four read tools (session list/status, bounded transcript read, history search) and two thin write callers (`notify`, `request_spawn`), token-derived caller identity, Project scoping, output redaction | relay policy and bounds (those live in `agent_messaging.py`), delivery, PTY writes, spawn, aiohttp handlers (`server.py`) |
| `prompt_queue.py` | persistent prompt queue: durable message store (states, strict head-of-line, revisions, sender provenance, correlation, relay depth), typed operations (enqueue/edit/arm/move/cancel/delete/retarget/schedule/send-next), content-erasing delete tombstones, delivery constraints, auto-policy + proving-counter tables, event-driven stranding + startup reconcile, delivery audit, seed-prompt staging (`stage_seed_argv`) | *when* an automatic send happens (`auto_delivery.py`), who may address whom (`agent_messaging.py`), PTY ownership (delivery writes go through the injected operator-input helper), aiohttp handlers |
| `auto_delivery.py` | the gate on automatic sends: install master, default-on bounded grant per live agent run, conversation opt-out, run binding, expiry, consecutive cap, stability window over `delivery_state`, quiet hours, persisted emergency pause, expiry sweep, proving-period counters and `promotion_status` | delivery itself (calls `send_next`, cannot pass `confirm`), readiness evaluation, HTTP |
| `agent_messaging.py` | relay policy for agent-authored messages (Project scope, size, per-origin budget, target backlog, chain depth, cycle detection, kill switch, expiry), inert `spawn_request` drafts, the `mailbox()` authorship projection the fleet queue reads | delivery, spawning (approval is a `server.py` human act), MCP protocol |
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
  corruption; `agent_run_seq > 0` is the marker that exempts a legitimately rolled run — but not
  one whose conversation a sibling's root identity claims: that roll is itself corruption and is
  repaired back to the spawn anchor. Backends whose CLI reports rollovers over the hook ingress
  (`reports_conversation_rollover`: Claude) never take the transcript-switch heuristic, and
  `agent_lifecycle_id` only moves on CLI-confirmed rollovers, which is what makes it the heal
  target for the state watchdog's live identity sweep (`_reconcile_identity_collisions`): no two
  live sessions may claim one `(backend, native_id)`, and a Claude session provably off its own
  conversation is rebound to its anchor.
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
- **The PTY reader polls by choice, and its cadence follows the session.** `pty.read()` is
  called nonblocking because `blocking=True` parks the reader thread somewhere neither `_stop`
  nor a dead child can reach it. A single fixed interval then serves two opposite cases badly:
  while output flows it is pure added latency on every chunk, and while a session sits at its
  prompt it is a wakeup per interval, per session, forever. `read_poll_interval` grades it by
  time since the last read *or write* (`_last_io_at`) across an active window, the previous
  fixed 10 ms, and a deep-idle interval reached only after seconds of silence. `write()` arms
  the active window deliberately: a keystroke's echo is the one latency a human is timing, and
  a session quiet at its prompt is exactly the one whose reader would otherwise be sitting on
  the slowest rung. The ladder only ever slows down, and never below what the fixed interval
  already gave a session that was doing anything.
  **Re-tiering alone is not enough and shipping it alone was a regression.** Choosing the next
  interval does nothing about the one already running, so a keystroke arriving mid-sleep still
  waited it out: measured end to end at 30 ms p50 and 40 ms max against a 40 ms rung, worse
  than the fixed 10 ms it replaced, in exactly the case the ladder exists to improve. `write()`
  therefore also sets `_io_wake`, and the reader waits on that event rather than sleeping. With
  the wake in place, typed-input latency is independent of the rung entirely: p50 ~16 ms at
  idle gaps of 0.05 s, 1 s and 6 s alike, where the remaining 16 ms is the rest of the stack.
  `tools/pty_latency_bench.py` is what caught this and is what re-checks it.
- **`asyncio.to_thread` does not make psutil work free.** Its Windows calls are C extension
  calls that hold the GIL, so a long sampling pass in a worker thread starves the event loop
  just as a blocking call would — it only stops looking like a blocking call. `processes.py`
  once rebuilt every `psutil.Process` and re-read every `cmdline()` on each 5-second tick,
  costing ~930-1110 ms per pass; the observable symptom was terminal keystrokes lagging and
  then arriving in a burst, plus a daemon that idled at 15-19% CPU. The rule for any periodic
  psutil work: take **one** system-wide snapshot per pass (`_ppid_map()`, not
  `children(recursive=True)` per root), cache process handles across passes, and re-read only
  attributes that actually change per tick — never name, command line, or creation time.
- **`Process.ppid()` is that same snapshot, and `oneshot()` does not cache it.** On Windows
  psutil implements it as `ppid_map()[pid]`, rebuilding the whole parent table per call, and it
  carries no `@memoize_when_activated` unlike the `name`/`cmdline`/`memory_info` calls beside it
  in the same `oneshot()` block. One unguarded call site in `_revalidate_unseen` therefore made
  every sampling pass O(processes²): measured 2026-08-05 with py-spy against the live daemon it
  was **45.2% of all samples**, with `processes.py` the outermost module for 50.1% and the
  daemon holding 22.6% of a core at rest. `_refresh_tree` already builds the full table for the
  pass, so `_parents` is the only permitted source of a parent pid; `ppid()` is a fallback for a
  pid younger than that refresh and nothing else. Iteration-count health could never have shown
  this — `process-inspector` ticks about every 6.5 s, making it one of the *least* frequent
  loops in the daemon.
- **Tuning a wait below the OS timer tick is not tuning.** Windows expires waitable
  timers on a global ~15.625 ms boundary, so `threading.Event.wait(0.0005)` and
  `asyncio.sleep(0.0005)` both cost ~15.6 ms, and a 40 ms wait rounds *up* to 46.6 ms.
  That silently defeated the PTY reader's three-rung poll ladder: every rung resolved to
  the same value, and the rung meant to be cheapest cost exactly as much as the idle one.
  `timer_resolution.raise_timer_resolution()` is therefore called before the event loop
  in the daemon and before any reader starts in the supervisor. Since Windows 10 2004
  `timeBeginPeriod` is per-process, so this asks for a sharper timer for swe-mux and
  leaves the rest of the system alone. Measured end-to-end keystroke-to-echo across the
  full websocket/daemon/supervisor/ConPTY path: **p50 16.3 ms to 2.35 ms, min 15.65 ms to
  2.03 ms, max 17.1 ms to 6.4 ms**. `time.sleep` was never affected (CPython already backs
  it with a high-resolution timer on Windows); only the primitives this code waits on
  were. Anything measuring latency must read the *effective* period from
  `timer_resolution.effective_period_seconds()` rather than assuming either value, or it
  reports the scheduler as congestion at 15.6 ms and dismisses real stalls as noise at 1 ms.
- **A monitor must not mutate what it monitors: every read-only Git call passes
  `--no-optional-locks`.** `git status` and `git diff` refresh the index and *write it
  back* whenever a tracked file's mtime has moved, taking `.git/index.lock` to do so. In a
  repository where agents are editing files that is every poll, so a 5-second read of the
  branch name was writing to the user's repository and contending with the agents it was
  watching. Verified 2026-08-05 by touching a tracked file and comparing `.git/index` mtime
  across both forms: plain `status` rewrote it, `--no-optional-locks status` did not, with
  byte-identical output. Effect on `git-monitor`, measured over 133 polls before and after:
  p50 321 ms to 73 ms, **p95 4,415 ms to 83 ms**, worst 5,629 ms to 169 ms, `busy_share`
  9.1% to 1.5%. The failure mode this removes is worse than the waste: a write in flight
  when the daemon is killed strands `index.lock`, which blocks *every* Git operation in
  that repository for every agent until someone removes it by hand — one such lock was
  found stranded here, created within seconds of a daemon restart. The flag is global and
  must precede the subcommand; `tests/test_git_phase4.py` pins both facts.
- **Directory walks read mtime from the walk, not from a second syscall.** Windows fills a
  `DirEntry`'s stat fields during enumeration, so `os.scandir` answers for free what
  `Path.glob` + `path.stat()` pays a syscall per file to re-fetch. Codex rollout discovery
  (`adapters/codex.py`) walks a tree that mux never prunes, on a 2 s switch-watch tick, and it
  runs **synchronously on the event loop**: measured against 1,314 rollouts in 158 directories,
  35.8 ms to 7.5 ms (4.8x), turning a recurring daemon-wide stall into a much shorter one. That
  tree cannot be pruned by directory name to go faster, because `codex resume` appends to the
  original rollout and a file under an old date routinely holds the newest mtime. The walk is
  cached whole for that window rather than sliced to the newest few, because locating a *known*
  conversation (`transcript_path`) matches on the file name and would otherwise pay a second walk.
  Expensive-but-honest metrics (unique set size) are opt-in per request, never on the cadence.
  See `design/features/processes-and-previews.md` §Sampling cost.
- Voice STT/TTS subprocesses and local models stay off the event loop.
  Incoming WAV duration, encoding, and bytes are validated before transcription.
  Whisper decodes validated PCM from memory; the optional legacy SAPI recognizer deletes its bounded temporary WAV/text files after the request and sweeps stale files left by an abandoned recognizer.
- Desktop presentation and daemon lifetime remain separate processes. Close/minimize hides the
  WebView; only authenticated loopback Quit stops the daemon. Never expose shutdown through the
  ordinary remote-control authority.
- The PTY supervisor (`supervisor.py`) must stay small and near-frozen: it cannot be hot-updated
  without killing every live session, so volatile code belongs in the daemon. It imports only
  `pty_host.py`, `scrollback.py`, and `win_jobobj.py`. In supervisor mode the daemon must never
  assign a supervised PID to a daemon-held Job handle (that would reap agents on daemon exit);
  nested per-session Jobs are created supervisor-side. Shutdown intent comes from outside the
  daemon: quit reaps through `reap_all_and_exit`, detach only flushes mirrored session metadata.
- Because those per-session Jobs live supervisor-side, anything the daemon needs to know about
  Job membership must come over the wire (`job_pids`, consumed by process attribution — see
  `design/features/processes-and-previews.md`). **A new supervisor message must not bump
  `PROTOCOL_VERSION` unless the wire format genuinely changed**: a mismatch stops a new daemon
  from driving the already-running supervisor, and the only way to update the supervisor is to
  kill every live session. Add the message, let an older supervisor answer "unknown message
  type", and degrade on the daemon side.
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
  That same inheritance is what makes Job membership a sound *attribution* source: since only
  an opt-in breakaway leaves the Job, a member is provably the session's even when the parent
  chain to it has been broken by an intermediate exit.
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
- Preview registration identity is Project endpoint, not clicked terminal.
  Resolve listener ownership across live sessions before attachment.
  Automatic discovery creates route-only identities for cross-service traffic.
  A bounded HTML probe or explicit registration promotes an identity into the listed Preview inventory.
  Cache negative probes by listener process identity and back them off so UI refresh does not create a request loop against tool listeners.
  Do not weaken the iframe sandbox or let a browser dial raw loopback for cross-service traffic.
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
