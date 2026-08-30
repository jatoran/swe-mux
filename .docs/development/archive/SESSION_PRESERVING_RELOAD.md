# Session-preserving daemon reload (PTY supervisor split)

Status: **implemented** (2026-07-23), shipped behind `pty_supervisor_enabled` (default off,
per §7.5). **The default moved to on on 2026-08-28**; §7.5's "flip the default only once the
flagged path is solid" is the step that was taken, and what makes it safe rather than brave is
the fallback that clause was written around - a daemon that cannot reach or spawn a supervisor
starts unsupervised and says so, so the worst case of the new default is the old behaviour. Goal: update daemon and UI code and restart the daemon without killing live agent
sessions, so agents keep running uninterrupted (even mid-work) across a reload. §7 is the
implementation checklist; the rest is the design reference it points into.

Implementation map: `supervisor.py` (supervisor process), `supervisor_client.py`
(daemon-side client + `RemotePtyHost`), `scrollback.py` (shared ring buffer),
`session.py` (remote spawn/fallback, adoption, intent-aware shutdown), `server.py`
(wiring + shutdown-intent endpoint), `desktop.py` (tray Restart daemon, `--supervisor-child`),
`__main__.py` (`swemuxd --shutdown`), tests in `tests/test_pty_supervisor.py`.
Promoted into `ROADMAP.md` under "Implemented baseline".

Routing note (per `.docs/CLAUDE.md`): implementing this touches process/session lifecycle,
package boundaries, and daemon shutdown, so it will require updates to
`design/architecture.md`, `design/features/sessions.md`, `design/features/desktop-shell.md`,
`design/interfaces.md`, and `technical/backend/packages.md`.

---

## 1. Problem statement

Today, restarting `muxd` (to pick up daemon code changes) hard-kills every live agent and
shell session. The UI half of "reload with my changes" is essentially free already; the
daemon half is blocked by a deliberate design choice. We want:

- **UI reload:** rebuild frontend, reload the browser/WebView, all sessions still there
  and uninterrupted. **Already works today** via WS reattach + scrollback replay.
- **Daemon reload:** replace daemon code and restart the daemon process, with agents
  continuing to run untouched — including agents that were mid-turn when the reload happened.
  **Not possible today.** This document is the plan to make it possible.

Non-goal: hot-reloading the PTY-owning layer itself without a restart (see §8, residual
limitation). The plan concentrates volatile code in the restartable daemon and keeps the
PTY layer small and near-frozen instead.

---

## 2. Why a daemon restart kills everything today

Three things tie each agent's lifetime to the daemon **process**. All are current code:

1. **ConPTYs are created in-process.** `pty_host.py` (`PtyHost.spawn`, ~line 133) calls
   `winpty.PTY(...)` inside the daemon. The pseudoconsole handle and its read thread live in
   the daemon's address space. On Windows you **cannot reattach** to a ConPTY whose creating
   process has died — this is an OS constraint, not a swe-mux one.

2. **The reaper is deliberately lethal.** `win_jobobj.py` creates a Job Object with
   `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`; its docstring states the intent: "ensuring PTY
   children cannot outlive the daemon." When the daemon exits and its job handle closes,
   Windows terminates every agent process tree in the job. `server.py:638` calls
   `reaper.close()` on shutdown.

3. **Scrollback is daemon RAM.** `ScrollbackBuffer` lives in-process (`session.py:173`) and
   is only ever snapshotted to *attaching browsers* (`session.py:249`). It is never
   persisted. Startup runs only `reconcile_external_history` (rebuilds *history* from
   transcript files); there is no live-session rehydration path.

Net: a daemon restart is a hard kill of all live sessions, by construction.

## 2.1 What already helps

- **UI reload is solved.** Static assets are served from `static/`; the browser reconnects
  over WS and the daemon replays the scrollback snapshot on attach (`session.py:249`,
  `_attach_locked`). This already survives browser refreshes because the daemon keeps holding
  everything. Only dev ergonomics (auto-reconnect, a build-version banner, a Vite dev proxy)
  are missing, not capability.
- **A `resume` primitive exists.** `adapters/claude.py:96` relaunches the CLI with
  `--resume <native_id>`, reconstructing an agent's *conversation* from its on-disk
  transcript. Useful as a fallback (§6) but not sufficient: it loses scrollback, loses the
  in-flight turn (an agent mid-tool-call is killed and only the conversation replays), and
  does nothing for shell/PowerShell sessions, which have no transcript.

---

## 3. The system is already a three-layer split

The lifecycle today is three processes, not one, and it already contemplates a daemon that
outlives its UI:

1. **Desktop shell** (`DesktopRuntime`, webview + tray). Spawns the daemon as a child
   subprocess (`ensure_daemon`, `desktop.py:235`). On quit it does an **intent-signaled**
   shutdown: token-authed, loopback-only `POST /api/desktop/shutdown` (`desktop.py:308`),
   waits up to 15s, then falls back to `child.terminate()`. `ensure_daemon` health-checks
   first and **reuses an already-running daemon** rather than spawning a rival
   (`desktop.py:226`). The quit handler already has a branch warning "the daemon and its
   terminals will remain running" when it did not start the daemon (`desktop.py:321`).

2. **Daemon** (`muxd`/aiohttp). Owns the ConPTYs and holds the reaper Job. On the shutdown
   event it unwinds every subsystem and calls `reaper.close()` (`server.py:638`).

3. **Agent PTYs**, inside the reaper job.

Reaping rule today: **the reaper Job handle lives in the daemon process; daemon exit = clean
reap.** The plan must preserve that property for intentional quit while breaking it for
reload.

---

## 4. Approaches considered

**A. Out-of-process PTY supervisor — recommended.** Split the daemon into
**daemon (volatile) + supervisor (stable)**. The supervisor owns what must survive a daemon
restart: ConPTYs, the read loop, scrollback, and the reaper Job. The daemon becomes a client
talking to it over a local named pipe/socket. "Update the daemon" = restart the thin
orchestration/API layer; the supervisor and its ConPTYs keep running. This is the
tmux/mosh/VS Code-server model (the terminal server outlives the client). Detail in §5.

**B. Drop kill-on-close, reparent, reconnect — rejected.** You can let agents survive daemon
exit, but you still cannot reattach live ConPTY I/O afterward (constraint §2.1). Leaves you
with transcript-resume only. Dead end for "uninterrupted."

**C. In-process Python hot reload (no restart) — rejected.** Reloading modules in a ~50-module
asyncio app with long-lived sessions, threads, and DB executors is fragile and will not
survive dataclass/shape changes or new background tasks. Fine only for trivial config.

**D. ConPTY handle handoff to a successor daemon — rejected.** ConPTY handles are technically
duplicatable via `DuplicateHandle`, but pywinpty does not expose the pseudoconsole internals
and the PTY's signal thread belongs to its creator. Would require replacing pywinpty with a
custom ConPTY wrapper. Too deep/brittle for the payoff.

---

## 5. Recommended architecture (Approach A)

### 5.1 Layers after the split

- **Supervisor (new, small, near-frozen).** Owns `PtyHost` + read loop + scrollback + the
  reaper Job. Exposes local IPC: `spawn / write / resize / subscribe / detach` plus lifecycle
  `reap_all_and_exit`. Realistically `pty_host.py` + `win_jobobj.py` + scrollback + a thin
  IPC loop, ~600–800 lines that change a few times a year.
- **Daemon (all the volatile code you iterate on).** API, orchestration, observation,
  automation, history, UI serving. Becomes a **client** of the supervisor. Restarting it never
  touches the supervisor or the Job. winpty/psutil/win_jobobj leave the daemon's dependency
  surface entirely.
- **Desktop shell / terminal:** unchanged in structure; they still launch and talk to the
  daemon (see §5.4 for who spawns the supervisor).

### 5.2 Why the codebase is well-shaped for this

- **`PtyHost` is already the IPC surface.** A 334-line dataclass with a narrow interface
  (prepare/spawn/write/resize/isalive/exit_status/release/stop + an `output_queue`). The work
  is making the queue cross-process and the methods RPCs. winpty handles cannot cross a
  process boundary, so the supervisor owns the **whole** PTY lifecycle and the read thread;
  the daemon only ever sees byte streams. A clean severance.
- **The reattach pattern already exists.** `_attach_locked` (snapshot replay + subscribe,
  `session.py:249`) is how browsers survive reloads. Daemon-reattaches-supervisor is the same
  shape one level down. Scrollback moves from `Session` into the supervisor so it survives
  daemon restarts; the daemon subscribes to the stream.
- **Per-session ownership is already anticipated.** `ReaperJob.create_child()` exists for
  nested per-session jobs under a wider job — so "supervisor-wide job + per-session child
  jobs" is already the intended shape.

### 5.3 Lifecycle and reaping — the intent-signaled model

The core hazard: **the daemon's own exit cannot tell "restart" from "quit."** The intent must
come from outside the daemon. Keep shutdown intent-signaled (the desktop→daemon boundary
already works this way) and extend it one level down:

- **Restart daemon (update):** replace the daemon process only. Never touches the supervisor
  or Job. Agents keep running, truly uninterrupted. On reboot the daemon reattaches and pulls
  each session's snapshot (the existing `snapshot()` + subscribe path, one level down).
- **Quit swe-mux (teardown):** send the supervisor `reap_all_and_exit`. It closes its Job →
  every agent dies → supervisor exits. Clean, deterministic, **no orphan, no taskkill**.
  Identical end state to today.

Because the supervisor holds kill-on-close and quit sends an explicit reap, **intentional quit
still reaps cleanly**. The lingering-daemon behavior only happens on the reload path, and only
for as long as the reload takes.

Failure modes, enumerated honestly:

1. **Desktop + daemon both die without sending the reap signal** (power loss, wrong PID
   killed). Supervisor lingers with live agents — this is the *survival* property, but must
   never require a manual taskkill. Safety valves:
   - **Single-instance re-discovery on next launch** (named pipe/mutex keyed on config path,
     same pattern as `WindowsSingleInstance` + `ensure_daemon`'s health probe). Next launch
     reattaches instead of orphaning or spawning a rival.
   - **A "force quit everything" tray/CLI action** and optionally a **linger timeout**
     (supervisor self-exits after N minutes with zero attached clients, tmux-style).
2. **Supervisor itself crashes or is force-killed.** Its Job closes and agents die — same
   blast radius as a daemon crash today. No worse.

Net change vs today: a **daemon crash goes from "all agents die" to "agents survive, reattach
on relaunch"** — strictly better. Only a supervisor crash or an explicit quit takes agents
down, which is the intended contract.

### 5.4 Terminal vs desktop mode

Survival mechanics, IPC, reattach, and the reaping guarantee are **identical** in both modes.
The daemon discovers-or-spawns the supervisor (named pipe/mutex keyed on config path), so the
desktop shell never needs to know the supervisor exists. Frozen-vs-source launch is already
handled by `daemon_command` (`--daemon-child` vs `-m swe_mux`); the supervisor mirrors that
with its own entry point.

The **only** divergence is where the "quit vs restart" intent comes from:

- **Desktop has two distinct buttons.** Keep tray **Quit** = `reap_all_and_exit` (clean reap,
  as now); add a **Restart daemon** item that preserves agents. Desktop UX otherwise unchanged.
- **Terminal has one ambiguous gesture (Ctrl-C).** Pick a convention. Recommended: the tmux
  model — **Ctrl-C detaches** (daemon exits, supervisor + agents survive, re-running `muxd`
  reattaches), and reaping is an **explicit command** (`swemux kill-server` / `swemuxd --shutdown`).
  This fits dev iteration ("restart to get my changes") exactly. Alternative — keep
  "Ctrl-C = everything dies" and add a second gesture (Ctrl-Break / a flag) for
  preserve-restart — is clunkier; prefer the tmux model.

Bonus that falls out: **cross-mode continuity.** Start agents from a terminal `muxd`, Ctrl-C
it, later open the desktop app on the same config → reattach to the same live sessions.
Caveat: "same config" is load-bearing (different config path/port keys a different
supervisor). Fine today with one config; just don't expect two configs to share agents.

### 5.5 Observation under the split

`_observe` reads the transcript file directly (`~/.claude/...`), so it is unaffected by the
split. Only scrollback-tail detection (`session.py:1173`) needs to consume the supervisor's
byte stream via subscription instead of a local buffer. Minor.

---

## 6. Interim fallback (optional, cheap)

Make daemon restart **soft** by auto-`resume`-ing all agent sessions on reboot (reuse the
existing `--resume` primitive). Honest caveats: loses scrollback, loses the in-flight turn,
and does nothing for shell sessions. Good as a stopgap, **not** the answer to "uninterrupted
even if they'd been working." Ship §5 for the real property.

---

## 7. Implementation checklist

Ordered to never ship a broken state. Do not check a box from code presence alone —
implementation + tests + docs must agree.

- [x] **7.1 Pin the `PtyHost` contract with tests.** Behavioral tests for the current
  in-process `PtyHost` (spawn/write/read/resize/exit/release, console-host reaping). The
  out-of-process implementation must pass the same suite. The interface becomes the thing that
  cannot silently break. *(`tests/test_pty_supervisor.py` — the contract tests are
  parameterized over `PtyHost` and `RemotePtyHost`; console-host reaping remains covered by
  `test_windows_reaper.py` plus the supervisor reap test.)*
- [x] **7.2 Extract the supervisor process.** Move `PtyHost` + read loop + scrollback + reaper
  Job behind a standalone entry point (mirror `daemon_command`'s frozen/source handling). It
  owns the ConPTYs and the Job. *(`supervisor.py`; frozen entry `--supervisor-child` in
  `desktop.py`, source entry `python -m swe_mux.supervisor`.)*
- [x] **7.3 Define the IPC protocol.** Local socket (loopback TCP + random token in the
  discovery file). Messages: `spawn / write / resize / set_graceful_exit / subscribe /
  unsubscribe / set_meta / stop / release / remove / list / ping / reap_all_and_exit`, with a
  protocol-version handshake in `hello`.
- [x] **7.4 Make `SessionManager` a supervisor client.** Discover-or-spawn via discovery file
  + supervisor-side single-instance mutex keyed on config path; on daemon boot,
  `adopt_supervisor_sessions()` reattaches and pulls per-session snapshots (subscribe replay,
  the `_attach_locked` shape one level down). Authoritative scrollback lives supervisor-side;
  the daemon seeds and maintains a mirror from the subscription stream.
- [x] **7.5 Ship behind a flag with in-process fallback.** `pty_supervisor_enabled` (default
  off; **flipped to on 2026-08-28**). If IPC to the supervisor fails, spawning falls back to
  today's in-process path. Flip the default only once the flagged path is solid. What made the
  flip answerable rather than a judgement call: the `live_daemon` CI tier now runs the real
  entry point *at the default* on Linux and Windows, and proves a shell spawned through the
  supervisor outlives its daemon and is adopted - by the same child - by a successor.
- [x] **7.6 Wire intent-signaled shutdown.** Desktop: Quit = mode `quit` → sessions stopped +
  `reap_all_and_exit`; new tray "Restart daemon (keep sessions)" = mode `restart` → detach.
  Terminal: Ctrl-C detaches; explicit `swemuxd --shutdown` is kill-server (also the
  "force quit everything" action). Single-instance re-discovery on next launch; linger
  timeout implemented as supervisor self-exit after 15 idle minutes with no clients **and**
  no live sessions (never while agents are alive).
- [x] **7.7 Scrollback-tail detection over the subscription stream.** The daemon-side mirror
  (`Session.scrollback`) is fed exclusively by the supervisor subscription in remote mode and
  seeded from the snapshot on reattach; `_pty_appears_idle` and nested-agent detection read
  that mirror unchanged.
- [x] **7.8 Docs.** Updated `design/architecture.md`, `design/features/sessions.md`,
  `design/features/desktop-shell.md`, `design/interfaces.md`, `technical/backend/packages.md`;
  promoted into `ROADMAP.md` (Implemented baseline).
- [ ] **7.9 (optional) Interim auto-resume-on-restart** (§6) — not needed; the split shipped
  directly.
- [ ] **7.10 (optional) UI dev ergonomics:** auto-reconnect-with-backoff, build-version
  banner, Vite dev proxy for WS.

---

## 7.5+ Addendum: dedicated supervisor bundle and reload triggers (implemented)

Follow-up shipped after the core split, closing the frozen-build gap and adding the
user/agent-facing triggers:

- **Dedicated supervisor artifact.** `packaging/swe_mux_supervisor.spec` builds
  `dist/swe-mux-supervisor/swe-mux-supervisor.exe` — its own bundle in its own directory, so
  rebuilding `dist/swe-mux` can never collide with a running supervisor's file image (the
  Orca-style relocation problem solved by construction instead of a runtime copy).
  `build_desktop.py` gates the supervisor rebuild on a hash of its source closure
  (`supervisor.py`, `pty_host.py`, `scrollback.py`, `win_jobobj.py`, `subprocess_flags.py`,
  entry, spec + pywinpty/psutil/pyinstaller versions): it only rebuilds when the supervisor
  itself changed, which is exactly the §8 case that requires reaping first. Resolution order
  in `supervisor_command()`: `SWE_MUX_SUPERVISOR_EXE` override → frozen sibling bundle →
  frozen `--supervisor-child` fallback → source `python -m swe_mux.supervisor` (source mode
  deliberately never picks the frozen bundle, so iterated code is the code that runs).
- **Daemon self-restart** (`POST /api/daemon/restart`): the daemon spawns a successor with
  `--relaunch-wait` (waits for the port), sets detach intent, and exits; the successor
  reattaches. **The successor is the same executable**: a source daemon re-imports current
  source (backend changes apply), but a frozen daemon relaunches its bundled code — backend
  source changes do NOT reach a frozen app via this route; use the frozen redeploy below
  (observed live 2026-07-28: a detector fix "shipped" by restart kept producing old-code
  findings until a redeploy). Refused with 409 when no supervisor is attached unless
  `force=true`. Triggers:
  UI app menu / sidebar menu / command palette ("Reload daemon (keep sessions)",
  `daemon.reload`) with a blocking overlay + auto page reload; `swemux reload-daemon [--force]`;
  plain HTTP for agents (`curl -X POST http://127.0.0.1:<port>/api/daemon/restart`). "Reload
  UI" (`ui.reload`) is the frontend half: rebuild assets (`npm run build`), reload the page.
  **Caveat:** `npm run build` writes to `src/swe_mux/static`, which is **gitignored build
  output** (`.gitignore` explains why) and is served directly *only*
  when the daemon runs from source. The frozen desktop app serves its bundled copy under
  `dist/swe-mux/_internal/swe_mux/static`, so a source rebuild + "Reload UI" does nothing for
  a frozen/remote/phone client — push frontend-only changes to the frozen app via the redeploy
  below. Verify what's actually served by comparing the `index-*.css` hash from
  `curl -s http://127.0.0.1:<port>/` against `src/swe_mux/static/index.html`.
- **Frozen redeploy** (`uv run python packaging/redeploy_desktop.py [--hidden|--restore-visibility|--no-launch|
  --skip-build|--force]`) builds exactly the current checkout.
  Worktree branches remain intentionally absent until they are integrated into `master`.
  The command first preflights that a supervisor is running *outside*
  `dist/swe-mux`
  and that no legacy `swe-mux-action.exe` task terminals hold the dist tree (task steps no
  longer run any swe-mux binary, so only pre-removal terminals can), then runs a **staged**
  cycle: build frontend + app bundle into `dist/.staging` while the old app keeps running,
  detach-stop the daemon (control token) and kill the shell only after the build succeeded,
  swap (`dist/swe-mux` → `dist/swe-mux.prev`, staging → `dist/swe-mux`, with a bounded rename
  retry for lock stragglers), relaunch, and report reattached sessions. The health wait allows
  up to five minutes because Windows can spend several minutes scanning a newly written
  PyInstaller tree on its first launch; it still fails immediately if the launched shell
  exits. This prevents a healthy-but-slow bundle from being killed and falsely rolled back.
  UI-triggered redeploys use `--restore-visibility`, which samples the shell immediately before the stop and applies the same visible or tray-hidden presentation to successful, swap-failure, and rollback relaunches.

  **The stop is pid-targeted, not image-wide.** `swe-mux.exe -m swe_mux.<module>` is a helper
  an agent session spawns inside its *own* process tree — `hook_client` runs on every
  PreToolUse/PostToolUse — and it shares the app's image name while being neither the shell
  nor the daemon. A bare `taskkill /F /IM swe-mux.exe` therefore reaches into live sessions;
  it once killed the only session that happened to be mid-tool-call (recorded as
  `exit_reason=killed`, versus `agent_exit` for a clean finish), which is precisely the
  guarantee this whole flow exists to provide. So `partition_app_processes` splits the two by
  argv and the ordinary stop signals only the shell/daemon pids. A process whose argv cannot
  be read counts as a helper: an unkilled straggler costs a bounded rename retry, an
  over-eager kill costs a session. The blunt image-wide `taskkill` still exists but fires
  **only** if the `dist/swe-mux` → `dist/swe-mux.prev` rename then fails, where the
  alternative is an aborted redeploy; it logs how many helpers it is about to take with it,
  and the rename is retried once afterwards. The rollback slot is
  cleared *before* the app is stopped, and never with a bare `rmtree(ignore_errors=True)`:
  Windows will not unlink an exe/DLL whose image is still mapped, so a partially removed
  `swe-mux.prev` survives and blocks every later rename onto it (WinError 183). Removal is
  retried, and a leftover that still will not go is moved aside to `swe-mux.prev.stale-*`
  (swept on a later run) so a poisoned slot can never abort a swap after the daemon is down.
  A failed build leaves
  the running app untouched; a new build that never reports healthy is rolled back to
  `swe-mux.prev` (the bad bundle is kept at `dist/swe-mux.failed`), so a remote/phone client
  is never stranded without a daemon. Safe to run from an agent session inside swe-mux: the
  agent's PTY lives in the supervisor and survives the whole cycle. Refreshing the supervisor
  bundle itself still requires `swemuxd --shutdown` first (§8), and the redeploy script keeps
  the old bundle with a warning when supervisor sources changed while sessions are live.
- **Redeploy from the UI** (`POST /api/daemon/redeploy`, menu/palette "Rebuild + redeploy app
  (keep sessions)", `app.redeploy` — works from desktop and mobile): the daemon validates it
  runs from a source checkout with `uv` available and a supervisor attached (409 otherwise;
  `force=true` matches the restart semantics), takes a pid single-flight lock
  (`<data_dir>/redeploy.lock`), and spawns the redeploy script detached from its own lifetime
  with output to `<data_dir>/redeploy.log`. `GET /api/daemon/redeploy` reports
  `{running, log_tail, available}` — while the build stage runs the old daemon still serves,
  so the UI detects an early build failure (lock cleared, daemon never dropped) and shows the
  log instead of waiting out the reconnect window; once the daemon drops it polls health and
  reloads when the successor (or rolled-back predecessor) answers.
  Every production index embeds a deterministic SHA-256 identity derived from Vite's content-addressed emitted filenames.
  The returning daemon sends that identity first on every `/events` connection and exposes it through health.
  Other clients reload automatically only while hidden; visible clients keep their work and show a persistent manual reload banner.
  A rollback returns the previous identity, so it does not trigger those clients.
  Clients running the release immediately before this protocol still require one manual reload because they have no comparison logic.

## 7.6 Addendum: Job breakaway for relaunches + death forensics (implemented)

Root-caused 2026-07-26 from "kill a session → UI freezes → daemon dead, no traceback":
Windows **Job membership is inherited by every descendant**, and the supervisor puts each
session root in a nested kill-on-close Job. So a redeploy (or any app relaunch) run from a
shell *inside* a session left the new tray + daemon inside that session's Job —
`CREATE_NEW_PROCESS_GROUP` does not escape a Job — and removing the session later
(`_remove_and_reply` → `ownership_job.close()`) silently terminated the daemon. The poison
persisted across `/api/daemon/restart` because the successor inherits the old daemon's Jobs;
it cleared only on a fresh Explorer/tray launch. Fixes:

- `ReaperJob` sets `JOB_OBJECT_LIMIT_BREAKAWAY_OK` alongside kill-on-close: containment is
  unchanged (escape is opt-in per spawn), but children may now request breakaway.
- Every spawn that must outlive sessions goes through `subprocess_flags.popen_outside_job`
  (`CREATE_BREAKAWAY_FROM_JOB`, plain-spawn fallback when denied): supervisor spawn
  (`connect_or_spawn`), daemon successor (`_spawn_daemon_successor`), tray→daemon
  (`ensure_daemon`), the redeploy endpoint's script spawn, and the redeploy script's app
  relaunch. **Note:** breakaway against a Job created *before* this change is denied (it
  lacks BREAKAWAY_OK), so the fix fully lands only after the supervisor bundle is rebuilt and
  restarted (`swemuxd --shutdown` + reap, per §8).
- The daemon, tray, and supervisor each check `win_jobobj.process_in_job()` at startup and
  log/ledger a loud warning when inside a Job — the poisoned-launch breadcrumb.
- Death forensics, since an external TerminateProcess is invisible in-process:
  `lifecycle.py` maintains `<data_dir>/daemon-heartbeat.json` (refreshed ~10 s, clean exits
  marked with intent); the next daemon logs "previous daemon pid X died without a clean
  shutdown; last heartbeat Ns before this start". `<data_dir>/lifecycle.log` is a small
  append-only ledger (daemon starts/clean exits, tray-observed daemon exit codes, Job
  warnings). The tray watches its daemon child and ledgers the exit code.
- Rolling logs so diagnosis never depends on a 160 MB console dump: rotating
  `<data_dir>/daemon.log` (10 MB × 5) for the app log, `access.log` for aiohttp request spam
  (`propagate=False`), rotating `supervisor.log` (stdlib-inline in `supervisor.py` to keep its
  closure frozen), and `crash.log` via `faulthandler` (daemon, tray, supervisor) for hard
  native crashes. Console redirects remain crash catchers: `desktop-daemon.log` (rotated to
  `.1` by the tray at each daemon spawn), `supervisor-console.log` (renamed from the old
  `supervisor.log` redirect), `daemon-relaunch.log`. Runtime verbosity:
  `POST /api/debug/log-level {"level": "DEBUG"}` (GET returns the current level), or set
  `log_level` in config — both apply live, config is the startup default.

## 8. Residual limitation (accept it)

You can update the **daemon** freely with agents untouched. You **cannot** hot-update the
**supervisor itself** without killing agents — you can't hand a live ConPTY to a successor
process (pywinpty doesn't expose handle duplication, and the pseudoconsole's signal thread
belongs to its creator). The whole design rests on this discipline: put everything you iterate
on in the daemon; keep the supervisor tiny and near-frozen. As long as churn stays out of that
box (~600–800 lines), you get session-preserving reload, and quitting still reaps cleanly.

---

## 9. Prototype-first de-risk

Before committing to the full split, validate the load-bearing claim in isolation: **a ConPTY
spawned by a standalone supervisor process survives that supervisor's client dying and can be
re-subscribed.** If that prototype holds on the target Windows build (including the frozen
pywinpty path), the rest is mechanical. If it doesn't, revisit §6 as the ceiling.

**Validated.** `test_supervisor_process_outlives_client_and_reaps_on_command` runs the
supervisor as a real subprocess, aborts the client connection uncleanly, verifies the agent
process and its output survive, re-subscribes from a second client with scrollback intact,
and confirms `reap_all_and_exit` kills the tree and removes the discovery file. The frozen
path is verified too: the packaged `swe-mux.exe --supervisor-child` was driven directly
through the same spawn → I/O → client-abort → re-subscribe → reap sequence (2026-07-23).
