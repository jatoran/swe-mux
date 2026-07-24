# Session-preserving daemon reload (PTY supervisor split)

Status: design + implementation plan, **not yet started**. Goal: update daemon and UI code
and restart the daemon without killing live agent sessions, so agents keep running
uninterrupted (even mid-work) across a reload. §7 is the implementation checklist; the rest
is the design reference it points into.

This is unscheduled work — it is not in `ROADMAP.md` or `CONTROL_PLANE_ROADMAP.md`. Promote
it into one of those if/when it is picked up.

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
  reattaches), and reaping is an **explicit command** (`mux kill-server` / `muxd --shutdown`).
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

- [ ] **7.1 Pin the `PtyHost` contract with tests.** Behavioral tests for the current
  in-process `PtyHost` (spawn/write/read/resize/exit/release, console-host reaping). The
  out-of-process implementation must pass the same suite. The interface becomes the thing that
  cannot silently break.
- [ ] **7.2 Extract the supervisor process.** Move `PtyHost` + read loop + scrollback + reaper
  Job behind a standalone entry point (mirror `daemon_command`'s frozen/source handling). It
  owns the ConPTYs and the Job.
- [ ] **7.3 Define the IPC protocol.** Local named pipe/socket. Messages:
  `spawn / write / resize / subscribe / detach / snapshot / reap_all_and_exit`. Include a
  **protocol-version handshake** on attach.
- [ ] **7.4 Make `SessionManager` a supervisor client.** Discover-or-spawn via named
  pipe/mutex keyed on config path; on daemon boot, reattach and pull per-session snapshots
  (reuse the `_attach_locked` shape). Scrollback now lives supervisor-side.
- [ ] **7.5 Ship behind a flag with in-process fallback.** If IPC to the supervisor fails,
  fall back to today's in-process spawn. Flip the default only once the flagged path is solid.
- [ ] **7.6 Wire intent-signaled shutdown.** Desktop: keep Quit = `reap_all_and_exit`, add
  "Restart daemon". Terminal: Ctrl-C detaches, add explicit `kill-server`/`--shutdown`. Add
  single-instance re-discovery and a "force quit everything" action; consider a linger timeout.
- [ ] **7.7 Move scrollback-tail detection to the subscription stream** (`session.py:1173`).
- [ ] **7.8 Docs.** Update `design/architecture.md`, `design/features/sessions.md`,
  `design/features/desktop-shell.md`, `design/interfaces.md`, `technical/backend/packages.md`
  per the routing table. Promote this item into `ROADMAP.md`/`CONTROL_PLANE_ROADMAP.md`.
- [ ] **7.9 (optional) Interim auto-resume-on-restart** (§6) if a stopgap is wanted before
  the split lands.
- [ ] **7.10 (optional) UI dev ergonomics:** auto-reconnect-with-backoff, build-version
  banner, Vite dev proxy for WS.

---

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
