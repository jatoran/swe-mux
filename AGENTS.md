# swe-mux — agent instructions

Documentation lives in `.docs/`; the routing table in `.docs/CLAUDE.md` says which docs to
update for which kind of change. Read the relevant feature doc before changing a subsystem.

## Applying your changes (session-preserving reload)

Live agent/terminal sessions are owned by a separate PTY supervisor process and survive
daemon restarts and app rebuilds. Use these flows instead of killing swe-mux:

- **Frontend change**: `cd frontend && npm run build` (outputs into `src/swe_mux/static`,
  whose hashed `assets/` and `index.html` are **gitignored build output** — never commit
  them, and expect a fresh clone or worktree to serve no UI until you run this once),
  then refresh the browser / UI menu → "Reload UI". **This only reaches the running app if
  the daemon serves from source** (`uv run` / dev). The **frozen desktop app** (`dist/`,
  what the tray icon and any remote/phone client connect to) serves its OWN bundled copy at
  `dist/swe-mux/_internal/swe_mux/static`, which `npm run build` does NOT touch — so the
  rebuilt CSS/JS never loads and your change silently does nothing on that client. Before
  assuming a frontend change is live, **confirm which build is being served**: compare the
  hashed asset the live daemon returns against the one you just built —
  `curl -s http://127.0.0.1:8765/ | grep -o 'assets/index-[A-Za-z0-9_-]*\.css'` vs the same
  grep on `src/swe_mux/static/index.html`. If they differ, you are on the frozen app: ship
  the change with the **Frozen desktop app update** flow below (a plain `npm run build` is not
  enough). Symptom of this trap: a verified-correct CSS/JS fix that "still doesn't work" for
  the user, especially on mobile.
- **Backend/daemon change**: `curl -X POST http://127.0.0.1:8765/api/daemon/restart`
  (or UI menu → "Reload daemon (keep sessions)", or `mux reload-daemon`). Every session
  survives — but the daemon restarts with your code **only when it runs from source**
  (`uv run` / dev). The restart spawns a successor of the *same executable*: a **frozen
  desktop app** daemon respawns its bundled (old) backend code, and your source change
  silently does nothing. This is the backend half of the frontend trap above — same check
  applies (compare the served asset hash; a frozen daemon also serves the bundled
  frontend). On the frozen app, ship backend changes with the **Frozen desktop app
  update** flow below.
- **Frozen desktop app update** (rebuild `dist/` + relaunch, sessions preserved —
  safe to run from a session inside swe-mux): `uv run python packaging/redeploy_desktop.py`,
  or from the UI: menu → "Rebuild + redeploy app (keep sessions)" (`app.redeploy`, also on
  mobile; `POST /api/daemon/redeploy`). This is the correct way to push a **frontend-only**
  change to the frozen app too (it rebuilds the frontend into the bundle). It is a
  multi-minute PyInstaller rebuild, **staged**: it builds into `dist/.staging` while the old
  app keeps running, stops it only after a successful build, then swaps — a failed build
  leaves the running app untouched, and a new build that never turns healthy is rolled back
  to `dist/swe-mux.prev` (bad bundle kept at `dist/swe-mux.failed`). If the swap's rename
  retries exhaust on a `WinError 5/32` lock straggler, the script relaunches the old bundle
  itself; do NOT reach for `taskkill`/`muxd --shutdown` (that reaps sessions). Endpoint log:
  `<data_dir>/redeploy.log`.
- **Never** run `muxd --shutdown`, kill `swe-mux-supervisor.exe`, or taskkill swe-mux
  processes as part of an update — those reap every live session. They are only for
  intentionally stopping everything.

Details, constraints, and the supervisor design: `.docs/development/archive/SESSION_PRESERVING_RELOAD.md`
(§7.5+ addendum has the exact workflows), `.docs/design/features/desktop-shell.md` (packaging),
`.docs/technical/backend/packages.md` (supervisor rules: hash-gated source closure, cwd-lock
hazard, restart contract).

## Worktrees and parallel changes

Use provider-native worktree lifecycle controls when available.
Codex may also use `git worktree add` directly when its native control is unavailable or the user
requests manual creation.
`master` is the single shared trunk; there is no agent-only integration branch or `gwt` landing flow.
Claude worktrees own their generated branches.
Codex worktrees may begin detached but must create a named branch before committing.
Worktree agents commit only their own branch.
Reconcile a finished branch with current `master`, verify it, and integrate branches one at a time.

**Landing is meant to be cheap; do not re-verify what did not change.** `.worktree-verify` is
~45s of pytest (it runs across the host's cores; it was ~175s before 2026-08-21) plus ~17s for
ruff, mypy, tsc, and `npm test` together, so after `git merge
master` scope the re-run to what actually arrived (`git diff --stat ORIG_HEAD..`): docs-only
means land immediately, anything not touching `src/swe_mux` means the ~17s half is enough, and
only an incoming backend change earns another full pytest. Run the gate once and read all of
it — piping it through `tail` or `grep` hides the part you needed and costs a second full run.
Land the moment it is green, because the window is wide enough for master to move and
refuse the fast-forward; when that happens, merge again and apply the same triage instead of
re-running everything.

**A worktree is for editing and testing, not for running the app.** Worktrees isolate the
working tree, not the runtime. The daemon owns port 8765 and a single data dir at
`~/.mux`, both of which are process-wide singletons. Never start `muxd`, run the frozen
app, or trigger a redeploy from inside a worktree — it will collide with the live daemon
and your real sessions. All of the session-preserving reload flows above apply to the
**primary checkout only**.

Run `.worktree-verify` directly in each finished worktree.
The suite is parallel-safe, so serialising verification only makes several agents finishing
at once take N times longer for nothing.

Measured 2026-07-29 — two and then three worktrees running
`.worktree-verify` simultaneously: 996 passed in each, at 58/58/62s against ~60s for a
solo run, so there is no contention and no slowdown. Why it holds, in case someone is
tempted to re-add the lock on a hunch:

- No test points at the real `~/.mux`. 29 construct `Config(data_dir=...)` explicitly;
  `provider_accounts.py` and `reconcile.py` take an injectable `home`; and the single bare
  `Config()` (`tests/test_usage_phase4.py`) only reads two command strings and does no I/O.
- The SQLite files tests create live under `tests/`, which is per-worktree.
- No test binds a port. Every `8765` in the suite is a string assertion.

Re-audited 2026-08-21 for *intra*-run parallelism, where xdist workers share one `tests/`:
the only test that writes into `tests/` already uses a `uuid4` filename; there are no
`module`/`class`/`session`/`package` scoped fixtures and no `conftest.py` anywhere, so no
worker inherits another's state; and both `os.environ` mutations restore in a `finally`
inside a process of their own.

If any of those stops being true, a verification lock is a stopgap.
Fix the isolation instead, because serialised verification is the largest cost in parallel work.

Worktree bootstrap (`.worktree-setup`) is `uv sync` plus `npm ci`, sharing the uv and npm
caches, so it is a dependency install rather than a download. It is not free: if agent
tasks are short, prefer reusing a few long-lived worktrees over creating one per task.

## Verification

Backend: `uv run pytest tests -q -n auto --dist loadgroup --durations=25 -m "not live_agent
and not live_subagent and not live_telemetry and not live_quota and not live_automations
and not live_mcp and not live_edge_tts and not live_daemon and not live_model_flag"`, `uv run ruff check
src/swe_mux tests packaging`, `uv run mypy`.
Frontend (in `frontend/`): `npx tsc --noEmit`, `npm test`.

These are exactly what `.worktree-verify` runs.

That `-m` expression is copied into five files and they must agree; drift between them was
itself one of the CI bugs of 2026-08-27, so `tests/test_live_daemon_guards.py` now asserts the
agreement in the default tier. `live_daemon` is the one live tier that needs no provider,
credential, or quota - it starts a real daemon on an ephemeral port and drives a **shell**
session through it - so CI runs it on `ubuntu-latest` and `windows-latest` as its own step,
while the landing gate deselects it like every sibling. Run it by hand with
`uv run pytest tests/test_live_daemon.py -m live_daemon` (~15s).

The gate runs pytest across the host's cores (pytest-xdist, a `dev` dependency): measured
2026-08-21 on 16 physical cores, 241.9s serial against 39.5-47.3s over seven `-n auto` runs.
`--dist loadgroup` is not interchangeable with `--dist worksteal`: it is the only mode that
honours the `xdist_group` mark keeping each real-console file
(`test_conpty_integration.py`, `test_pty_supervisor.py`) on a single worker.
While iterating, plain `uv run pytest tests/test_x.py` is still the right thing.
A fixed `asyncio.sleep` before a *positive* assertion is what breaks under this - wait for
the condition instead (`until` and `drained_until` in `tests/support/settle.py`); a sleep
guarding a negative assertion is a real quiet window and stays.

The Playwright renderer suite (`npm run test:renderer`, in `frontend/`) is CI-only and
binds a port: it drives a Vite dev server on 4174 with `reuseExistingServer`, so a second
checkout that finds 4174 taken runs its whole suite against *the other checkout's* code.
From a worktree, give it a port **nothing else is on** — check with `netstat -ano | grep <port>`
first and do not reuse a number a doc handed you, because a fixed alternate just moves the
collision from checkout-vs-CI to worktree-vs-worktree: `RENDERER_PORT=<free> npm run
test:renderer`. It takes about a minute, so while iterating run just your own
file (`npx playwright test --config playwright.renderer.config.ts <spec>`, seconds) and keep the
full suite for the end.
