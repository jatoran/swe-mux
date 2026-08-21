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
- **Supervisor change** (`supervisor.py`, `pty_host.py`, `scrollback.py`, `win_jobobj.py`,
  `subprocess_flags.py`, the supervisor spec/entry): **the redeploy above cannot ship this,
  and says nothing when it doesn't.** The redeploy's preflight *requires* a live supervisor,
  while the bundle build refuses to run while one exists (PyInstaller cannot overwrite the
  locked exe) — so the app bundle updates, `dist/swe-mux-supervisor/` stays stale, and your
  change silently does nothing. Restarting swe-mux around the redeploy does not help; the
  order is not the problem. Updating the supervisor **reaps every live session**, so it is a
  deliberate act, not part of a normal update. From a terminal **outside** swe-mux:
  1. `uv run muxd --shutdown` (reaps all sessions *and* stops the supervisor)
  2. `Get-Process swe-mux, swe-mux-supervisor -ErrorAction SilentlyContinue` — expect nothing
  3. `uv run python packaging/build_desktop.py --supervisor-only`
  4. relaunch the app

  Step 3 must run with no supervisor alive, so do not relaunch before it. Check whether you
  actually need any of this: `supervisor_bundle_current()` in `packaging/build_desktop.py`
  returns `False` when the running bundle is stale. **Prefer avoiding it entirely** — a new
  supervisor message that an older supervisor can reject ("unknown message type") while the
  daemon degrades gracefully needs no reap, whereas a `PROTOCOL_VERSION` bump forces one.
- **Never** run `muxd --shutdown`, kill `swe-mux-supervisor.exe`, or taskkill swe-mux
  processes as part of an update — those reap every live session. They are only for
  intentionally stopping everything, or for the deliberate supervisor update above.

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

**Landing a finished branch is two commands, and both are yours to run.** From inside the
worktree: `git merge master` (reconcile), then `.worktree-verify`. Then from the primary
checkout: `git merge --ff-only worktree-<name>`. The fast-forward is deliberately the only
merge the git-policy hook allows outside a worktree, because it cannot lose work: Git
refuses it if the branch diverged, and refuses it if it would overwrite uncommitted local
changes. `--no-ff` is blocked there and is not the flow; do not reach for it, and do not
escalate to the user to land your own branch. Leave the worktree in place afterwards
(`ExitWorktree` with keep) rather than removing it because the task ended.

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

Re-audited 2026-08-21 for *intra*-run parallelism, where xdist workers share one `tests/`
directory rather than each having their own:

- Exactly one test writes into `tests/` at all (`test_control_plane_enablement.py`), and
  the filename it writes already carries a `uuid4` - everything else writes under `tmp_path`.
- There are no `module`, `class`, `session`, or `package` scoped fixtures anywhere in the
  suite, and no `conftest.py`; every fixture is function-scoped, so no worker can inherit
  another's state and no test depends on a neighbour having run first. That is also why
  widening fixture scope for speed is the wrong trade here - the isolation is what makes
  `-n auto` safe, and a 6x win does not need it.
- The two tests that mutate `os.environ` (`test_pi_opencode_adapters.py`,
  `test_pty_supervisor.py`) both restore it in a `finally`, and a worker is its own process,
  so the mutation cannot reach another worker even in between.
- The supervisor tests' discovery file and its socket are per-`tmp_path`; nothing in the
  suite binds a fixed port.

If any of those stops being true, a verification lock is a stopgap.
Fix the isolation instead, because serialised verification is the largest cost in parallel work.

Worktree bootstrap (`.worktree-setup`) is `uv sync` plus `npm ci`, sharing the uv and npm
caches, so it is a dependency install rather than a download. It is not free: if agent
tasks are short, prefer reusing a few long-lived worktrees over creating one per task.

**The land queue automates exactly the sequence above** (`.docs/design/features/land-queue.md`):
the Git drawer's Land segment, or `mux.request_land` from inside the worktree, enqueues a
request and the daemon runs reconcile → `.worktree-verify` → fast-forward for one branch at a
time. It applies the docs-only half of the triage rule above by itself: after reconciling it
classifies the incoming paths against a closed allowlist (`*.md` anywhere, plus documentation
assets under `.docs/`/`docs/`) and skips the gate when every one of them matches, recording the
class and its reason in the request's event trail and on the row. Anything else - a source file,
a rename, a submodule, an unreadable diff - runs the full gate exactly as before. It never resolves a conflict and never runs a gate whose exact bytes a human has not
approved; a conflict or a failed gate comes back to the requesting session as a queue message.
It is off by default per Project, and the manual two commands remain the fallback and the
thing to reach for when the queue is not enabled.

## Verification

Backend: `uv run pytest tests -q -n auto --dist loadgroup --durations=25 -m "not live_agent
and not live_subagent and not live_telemetry and not live_quota"`, `uv run ruff check
src/swe_mux tests packaging`, `uv run mypy`.
Frontend (in `frontend/`): `npx tsc --noEmit`, `npm test`.

These are exactly what `.worktree-verify` runs.

**The gate runs pytest across the host's cores** (pytest-xdist, a `dev` dependency).
Measured 2026-08-21 on the 16-physical-core primary host: 241.9s serial against 39.5-47.3s
over seven `-n auto` runs, all 4214 tests passing in every one - about 6x, and `-n 32`
(oversubscribed) measured no better than `-n auto`, so the physical-core default is the
right one.
The two real-console files carry `pytest.mark.xdist_group`, which is honoured **only** by
`--dist loadgroup`; `--dist worksteal` ignores the mark entirely, so choosing it would
silently scatter `test_conpty_integration.py` and `test_pty_supervisor.py` across workers
and run their wall-clock-sensitive pseudoconsole tests concurrently with each other.
Unmarked tests are distributed exactly as under `--dist load`, so the grouping costs
nothing elsewhere.
Each real-console file is its own group rather than the two sharing one: a file's own real
consoles never overlap, while the two files still run on two workers.
The measured critical path is the bulk of the suite (31s with both real-console files
removed), not the groups, so pinning them buys robustness for about 11s.
While iterating, plain `uv run pytest tests/test_x.py` is still the right thing - worker
startup is not worth paying on a three-test run.

Collection is not a barrier to this and needs no lazy-import work: cold collection is 10.4s,
of which nearly all is pytest's assertion rewriting compiling to `__pycache__` (shared by
every worker, paid once), and warm collection is 1.15s. The marginal import cost of all 198
test modules together is 1.45s.

**A fixed `asyncio.sleep` before a positive assertion is the failure mode parallelism
exposes**, and the one thing to write differently now. A worker sharing the host with
fifteen others is not scheduled inside the 10ms that such a sleep bets on, and the test
reddens the gate over machine load rather than over the code
(`test_pty_ws.py::test_pty_ws_orders_replay_then_live_updates_and_exit` did exactly this,
intermittently, before the fix). Wait for the condition instead - `until(...)` in
`tests/test_pty_ws.py` is the shape - which also returns sooner on an idle machine.
Sleeps guarding a *negative* assertion ("no pulse fired") are a real quiet window and stay:
load only makes those safer.

The pytest run above includes the real-ConPTY integration tests (`-m conpty`,
Windows-only, `tests/test_conpty_integration.py`) and the harness-adapter coverage
matrix (`tests/test_harness_adapter_matrix.py`, which fails when a harness is added to
the registry with no adapter/spawn coverage) — the `not live_*` filter does not deselect
them. `.github/workflows/ci.yml` mirrors this gate on `windows-latest` and adds the
production frontend build and the full Playwright renderer suite
(`npm run test:renderer`, in `frontend/`). `frontend/tsconfig.json` includes only
`src`, so the plain `npx tsc --noEmit` does NOT typecheck `frontend/test/`; the
renderer harnesses are typechecked separately by `npm run check:renderer`
(`tsconfig.test.json`, `src` + `test/renderer`), which `.worktree-verify` now runs so a
harness prop that drifts from the component it mounts fails at typecheck instead of only
at Playwright runtime (the way `pane-layout.spec.ts` once rotted).

**The renderer suite binds a port and is therefore not parallel-safe by default.** It
drives a Vite dev server on 4174 with `reuseExistingServer`, so a second checkout that
finds 4174 already taken runs its entire suite against *the other checkout's code* — the
failure reads as harness pages that "do not exist", or, worse, as a green run that proved
nothing. From a worktree, give it a port **nothing else is on** — check with
`netstat -ano | grep <port>` first and do not reuse a
number a doc handed you, because a fixed alternate just moves the collision from
checkout-vs-CI to worktree-vs-worktree: `RENDERER_PORT=<free> npm run test:renderer`. CI leaves
the variable unset and keeps 4174. The suite is CI-only and takes about a minute, so while
iterating run just your own file (`npx playwright test --config playwright.renderer.config.ts
<spec>`, seconds) and keep the full suite for the end.
