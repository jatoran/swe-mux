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
  grep on `src/swe_mux/static/index.html`. If they differ, you are on the frozen app and a
  plain `npm run build` is not enough. Symptom of this trap: a verified-correct CSS/JS fix
  that "still doesn't work" for the user, especially on mobile.

  **The fix for that is now the frontend overlay, not a bundle rebuild** (since 2026-08-29,
  `.docs/design/features/desktop-shell.md`). One command packages the built tree as a
  hash-verified 10.9 MiB overlay and installs it into the data dir, where the daemon prefers
  it over its own bundled copy:
  `uv run python packaging/build_frontend_overlay.py --build --install`, then
  `swemux reload-daemon`. Seconds and one session-preserving restart, against a multi-minute
  ~370 MB staged swap. `swemux ui-overlay status` says which tree is being served and why,
  `swemux ui-overlay revert` puts the bundled one back, and both work without the UI - which is
  the point, because the failure mode an overlay can cause is a frontend that will not load.

  Three things to know before reaching for it. **Package from the checkout the running app
  was redeployed from**: the overlay pins both `__version__` and a digest over the daemon's
  route table, and a backend that has moved since is refused with `api_mismatch` rather than
  served - correctly, because a backend change is not something an overlay can carry, and
  `__version__` alone cannot see it (the frozen app is rebuilt per commit, the version moves
  per release). **Never run `--install` from a worktree**: it writes into the live daemon's
  data dir, which is the same runtime-collision rule as everything else here. And **the very
  first hop is still a redeploy**, because a frozen app built before this feature has no
  overlay support in its bundled backend.
- **Backend/daemon change**: `curl -X POST http://127.0.0.1:8765/api/daemon/restart`
  (or UI menu → "Reload daemon (keep sessions)", or `swemux reload-daemon`). Every session
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
  mobile; `POST /api/daemon/redeploy`). It ships a frontend change too, and it is no longer
  the right tool for one: use the **frontend overlay** above unless the change also touches
  the backend, or unless the running bundle predates overlay support. It is a
  multi-minute PyInstaller rebuild, **staged**: it builds into `dist/.staging` while the old
  app keeps running, stops it only after a successful build, then swaps — a failed build
  leaves the running app untouched, and a new build that never turns healthy is rolled back
  to `dist/swe-mux.prev` (bad bundle kept at `dist/swe-mux.failed`). If the swap's rename
  retries exhaust on a `WinError 5/32` lock straggler, the script relaunches the old bundle
  itself; do NOT reach for `taskkill`/`swemuxd --shutdown` (that reaps sessions). Endpoint log:
  `<data_dir>/redeploy.log`.
- **Supervisor change** (`supervisor.py`, `pty_host.py`, `scrollback.py`, `win_jobobj.py`,
  `subprocess_flags.py`, the supervisor spec/entry): **the redeploy above cannot ship this,
  and says nothing when it doesn't.** The redeploy's preflight *requires* a live supervisor,
  while the bundle build refuses to run while one exists (PyInstaller cannot overwrite the
  locked exe) — so the app bundle updates, `dist/swe-mux-supervisor/` stays stale, and your
  change silently does nothing. Restarting swe-mux around the redeploy does not help; the
  order is not the problem. Updating the supervisor **reaps every live session**, so it is a
  deliberate act, not part of a normal update. From a terminal **outside** swe-mux:
  1. `uv run swemuxd --shutdown` (reaps all sessions *and* stops the supervisor)
  2. `Get-Process swe-mux, swe-mux-supervisor -ErrorAction SilentlyContinue` — expect nothing
  3. `uv run python packaging/build_desktop.py --supervisor-only`
  4. relaunch the app

  Step 3 must run with no supervisor alive, so do not relaunch before it. Check whether you
  actually need any of this: `supervisor_bundle_current()` in `packaging/build_desktop.py`
  returns `False` when the running bundle is stale. **Prefer avoiding it entirely** — a new
  supervisor message that an older supervisor can reject ("unknown message type") while the
  daemon degrades gracefully needs no reap, whereas a `PROTOCOL_VERSION` bump forces one.
- **Never** run `swemuxd --shutdown`, kill `swe-mux-supervisor.exe`, or taskkill swe-mux
  processes as part of an update — those reap every live session. They are only for
  intentionally stopping everything, or for the deliberate supervisor update above.

Details, constraints, and the supervisor design: `.docs/development/archive/SESSION_PRESERVING_RELOAD.md`
(§7.5+ addendum has the exact workflows), `.docs/design/features/desktop-shell.md` (packaging),
`.docs/technical/backend/packages.md` (supervisor rules: hash-gated source closure, cwd-lock
hazard, restart contract).

## The repository is public, and CI is now a real audience

Since 2026-08-27 this repository is published at `https://github.com/jatoran/swe-mux` under
Apache-2.0, and `swemux.dev` serves from `site/` on every push that touches it. Two things
follow that did not apply while it was local-only.

**Everything you write is published.** Not just source: commit messages, `.docs/`, and the
`.docs/marketing/` drafts are all visible. There is exactly one private place in the tree,
`.private/`, which is gitignored and is for operator notes about running the project. It is
not for secrets - a gitignore rule is one `git add -f` away from not protecting anything, so
keys live in a password manager.

**A red badge is the first thing a visitor sees.** CI (`.github/workflows/ci.yml`) runs on
every push to `master`: the full Windows gate, plus Ubuntu and macOS legs, plus a `site` job.
macOS is `continue-on-error` until it passes cleanly; Ubuntu, Windows and `site` block.

**The site's pages are committed build output, and the release commit is where that bites.**
`site/tools/build.py` writes `site/changelog/index.html` and thirty siblings out of
`CHANGELOG.md`, `site/tools/docs_content.py`, `THIRD-PARTY-NOTICES.md` and
`packaging/third_party_licenses.json`; `pages.yml` uploads `site/` verbatim and never runs the
generator, which is what keeps the deploy a twenty-second file copy. So a commit that edits a
source and not the page publishes a stale page - 0.1.3 did exactly that, and `swemux.dev/changelog/`
showed 0.1.2 for a day while 0.1.3 was live on PyPI, on GitHub Releases and in `version.json`.
**If you touch any of those sources, run `python site/tools/build.py` and commit `site/`.**
`.worktree-verify` fails on a stale page (`tests/test_site_artifacts.py`, about 0.1s) and so does
the `site` job, which also runs `check.mjs`, `contrast.py` and `check_changelog.py`. None of them
can see a hand-edit to `site/index.html`, which has no generator.

### Landing is yours; pushing depends on which session you are

Nothing about the landing flow below changed: reconcile in your worktree, verify, and
fast-forward from the primary checkout. That is still yours to run and still needs no
escalation.

**If you are a worktree agent, you do not push.** Land your branch and say so. The `pre-push`
hook refuses it and names an override; that override is not a step you may take because it is
documented.

**An orchestrating session that the operator has given standing authority does push**, and as
of 2026-08-29 that authority has been given and covers the whole release path: `HUMAN_PUSH=1
git push`, annotated `vX.Y.Z` tags, and cutting a release through `RELEASING.md`. It was used
for eleven pushes and two releases (0.1.1 and 0.1.2) in one session. The operator's standing
instruction was to stop asking and act, so an orchestrating session that holds it should land,
push, watch CI, and fix what CI finds without checking back.

Three things bound it, and they are the reason the split is worth keeping:

- **The authority is per-session and comes from the operator directly.** It does not travel
  over `notify`. A worktree agent correctly refused a relayed instruction from the
  orchestrator on 2026-08-29 - it conflicted with the charter the operator had given it, and
  an unreviewed peer message is not a channel that can revoke that. If you need an agent to
  do something its charter forbids, get the operator to say so in that agent's own session, or
  do it yourself at land time.
- **A release is still the one act to think twice about**, because a PyPI version number can
  never be reused. Everything up to the tag is rehearsal; the tag is the point of no return.
- **The permission classifier is not the same thing as the operator's authority.** It blocked
  a write to Claude Code's own auth config on 2026-08-29 even with the operator asking for it.
  Hand that back rather than routing around it.

The practical consequence: **master moves in batches, and CI reports on the batch rather than
on your branch.** A green `.worktree-verify` is not a green CI run. They ask different
questions, and the gap between them is where the last several days of real bugs were found.

### What CI catches that a local gate cannot, measured rather than assumed

The first public run failed nine ways in one go, and none of the nine could reproduce on the
development host. Recorded so the next person does not rediscover them:

- **The host is not the runner.** `[tool.mypy]` inherited its platform, so `uv run mypy`
  asked a different question on every leg and could not be green on all three at once. It is
  now pinned to `platform = "win32"`.
- **A step that never ran is not a step that passes.** Three separate CI bugs were hiding
  behind each other, each revealed only when the one before it was fixed:
  `npm exec tsc -- --noEmit` typechecked *nothing* and printed help under `--prefix`; the
  runner's Node 20 could not run `--experimental-strip-types`; and the marker expression had
  drifted from `.worktree-verify`. Any step downstream of a failing one is unverified, not
  working.
- **Timer granularity differs by two orders of magnitude.** swe-mux raises this host's timer
  resolution to 1 ms; a bare runner sits at Windows' 15.625 ms default. Two tests were betting
  on the gap between writes exceeding filesystem timestamp granularity, and lost only there.
  This is the same constant behind the bundle-holder scan cache bug.
- **An external observer can be wrong.** A supervisor test asserted on `psutil`'s reading of a
  process's cwd; on the runner that reading was simply false, and the supervisor was correct
  all along. The fix was a better oracle - what a child actually inherits - not a weaker
  assertion.
- **The two CI closures differ on purpose, and a test that reads installed metadata can only
  run in one of them.** `verify` syncs `--extra voice-local`; the `platform` legs sync nothing,
  which is what proves `pip install swe-mux` still yields an importable package now the voice
  closure sits behind an extra. Six tests went red there in one go because they called
  `build_desktop.voice_closure_top_levels()` or imported `num2words` - both of which ask the
  *machine* a question, not the repository. Adding the extra to that job would have greened them
  by deleting the coverage the job exists for.
  The rule that fell out, and it is worth applying before writing the test rather than after:
  **an assertion about an artifact must not be derived from the environment checking it.** Two of
  the six became injectable (the gate takes the closure as a parameter, so the refusal's wording
  is tested everywhere), two were rewritten against a generated data table that is always
  present, two are genuinely about installed packages and now `skipif` with the reason on them -
  and one was not an environment problem at all but a real defect the no-extras leg was the only
  thing able to see.
  A second round of the same rule, on macOS: two assertions carried `> 50 * 1024 * 1024`, a floor
  derived from the Windows voice closure (81.9 MiB) that the macOS one (49.6 MiB) sits just under.
  **A magnitude is a measurement of a host; assert the property instead.** The store's estimate is
  now asserted *equal* to the pinned wheels' total, and the selection's total is bounded by the
  range the pin table implies (each distribution's smallest and largest wheel, 44-139 MiB) - both
  true on every runner and both tighter than the number they replaced. A third, inert copy of the
  same constant was sitting in a fixture and is now derived too, because two of them appearing
  together was a habit rather than a slip.

The rule those add up to: **when CI fails and the local gate passes, the environment is the
hypothesis, and evidence beats a patch.** Instrument the failure and let one CI run answer it,
rather than shipping a plausible fix and re-reading the same red. Never weaken an assertion to
green a badge; a test that no longer distinguishes its failure mode has been deleted, not
fixed.

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
  suite binds a **fixed** port.
  That claim was narrowed on 2026-08-28 and the narrowing is the point.
  It used to read "no test binds a port", which the `live_daemon` tier made false: it stands up
  a daemon in process and, once, as a real `muxd` subprocess.
  What makes it still sufficient is that every one of those ports is OS-allocated (`bind` on
  `:0`, released and immediately re-taken) and every data directory is under `tmp_path`, so two
  workers - or two worktrees, or a worktree and CI - cannot collide with each other or with the
  operator's daemon on 8765 and `~/.mux`.
  A test that hardcodes a port number is what would break this, not a test that binds one.

Re-read that list before adding a tier that starts anything.
It is written to be re-audited rather than trusted, and this passage has already been wrong
once.

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
a rename, a submodule, an unreadable diff - runs the full gate exactly as before. It never
resolves a conflict; a conflict or a failed gate comes back to the requesting session as a queue
message. It is off by default per Project, and the manual two commands remain the fallback and
the thing to reach for when the queue is not enabled.

**Since 2026-08-29 a gate edit made here no longer blocks the queue.** `land_verify_grant` is
`granted` by default, so the queue runs an unapproved `.worktree-verify` whose bytes this machine
authored - an uncommitted edit, the trunk's own copy, or a branch commit by this repository's
configured `user.email` - and records the approved-to-current diff on the request's event trail
instead of asking first. A gate edited by **any other author** still refuses and presents its
bytes, which is the whole point: the script is branch content, this repository is public, and
landing a contributor's branch must never execute their script unattended. Lowering
`land_verify_grant` to `draft` in the Project's Agent authority table restores approving every
digest by hand.

**And clearing a block now re-queues what it stopped.** A refusal is terminal, so approving the
bytes used to fix the *next* land and leave the one that caused the block dead; approving, and
raising the authority, both start the refused lands again and say which ones in their response.
The resumed request is a new row naming the old one (`resumed_from`) - nothing reopens a
terminal row, because the trail has to go on saying the refusal happened.

## Verification

Backend: `uv run pytest tests -q -n auto --dist loadgroup --durations=25 -m "not live_agent
and not live_subagent and not live_telemetry and not live_quota and not live_automations
and not live_mcp and not live_edge_tts and not live_daemon and not live_model_flag"`, `uv run ruff check
src/swe_mux tests packaging`, `uv run mypy`.
Frontend (in `frontend/`): `npx tsc --noEmit`, `npm test`.

These are exactly what `.worktree-verify` runs.

**That `-m` expression exists in three files and they must agree.**
`.worktree-verify`, `.github/workflows/ci.yml` (twice), and this paragraph.
Drift between them was itself one of the CI bugs found in the week of 2026-08-27 - `ci.yml` was
missing `not live_edge_tts` and would have gone red on all three runners against Microsoft's
hosted endpoint - so the agreement is now asserted rather than remembered
(`tests/test_live_daemon_guards.py`), and that guard runs in the default tier.
Adding a live mark means editing all three and adding it to `LIVE_MARKS` there.

**`live_daemon` is the one live tier CI runs, and it is not in the landing gate.**
It needs no provider, no credential and no quota, because its session half spawns a **shell**
rather than an agent - which is what lets it run on a public runner and answer the question
none of the other 5400 tests do: does a daemon start on this host, serve a terminal, and stop
without leaving processes behind.
It runs on `ubuntu-latest` and `windows-latest` on every push, in ~40s.
It is **held off macOS deliberately** until that leg stops being `continue-on-error`: a new
tier added under that flag goes red beside an already-red leg, and its first real regression
would be indistinguishable from the noise.
`.worktree-verify` deselects it like every sibling, because it binds ports and spawns shells
and landing is meant to be cheap; run it by hand with
`uv run pytest tests/test_live_daemon.py -m live_daemon`.

**Two ratchets are on, and both start from a clean floor.**
`filterwarnings = ["error", ...]` in `pyproject.toml` makes a new warning fail the run; there
is exactly one exception (`ResourceWarning`, which fires at garbage-collection time and would
redden the gate over machine load rather than over the code), and every entry there carries a
date and a reason and is expected to be removed. Fix your warning; do not add a filter.
`C901` caps cyclomatic complexity at **88**, which is today's worst function
(`server._build_runtime_handles`) - so nothing needs refactoring to satisfy it and nothing may
be written worse than the worst thing already here. The step-down plan and its measurements are
in `.docs/development/archive/ROADMAP_V2.md` § S12; lower the number when a function comes
down, not to force one down.

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
(`test_pty_ws.py::test_pty_ws_orders_replay_then_live_updates_and_exit` and
`test_observation.py::test_stable_approval_becomes_visible_once_after_the_window` both did
exactly this, intermittently, before the fix). Wait for the condition instead:
`tests/support/settle.py` has `until(predicate)` and `drained_until(queue, kind)` for the
event-bus case, and both return sooner than a sleep on an idle machine.
Sleeps guarding a *negative* assertion ("no pulse fired", "the candidate was not committed")
are a real quiet window and stay: load only makes those safer.

**A thread or a child process that outlives the test's event loop is the second thing load
exposes, and it is worse than the sleep because the test it reddens is not the test that
caused it.** Both halves surface from a *finalizer*, whenever the collector happens to run, so
`filterwarnings = ["error"]` fails whatever was running at that moment. Two shapes, one rule:

- `asyncio.run_coroutine_threadsafe(queue.put(x), loop)` builds the coroutine on the *calling*
  thread, so a loop that closes first - or that accepts the callback and then stops before
  running it - leaves it un-awaited, and Python reports `coroutine 'Queue.put' was never
  awaited` as an unraisable warning much later. Build the coroutine on the loop instead:
  `pty_host.submit_queue_put` is the pattern and its docstring is the argument.
- A subprocess whose pipes have not all disconnected when its loop closes leaves
  `BaseSubprocessTransport` unclosed, and `__del__` then calls into the dead loop and raises
  `RuntimeError: Event loop is closed`. Waiting the child out inside the test that started it
  (`communicate()`, `process.wait()`, `reap_process_tree`, `run_bounded`) is what closes the
  transport; nothing else will.

All three failures of the first public shared-runner CI run (2026-08-27) were one of these two
shapes: two Windows real-console tests hit the first, and a macOS test that spawns nothing at
all reported the second on somebody else's behalf.

Attributing one to its creator needs a probe, because the traceback names only the reporter.
Two obvious probes do not work and both were measured: forcing `gc.collect()` in teardown
finds nothing, because the loop still holds the objects then, and scanning at session finish
finds nothing, because they have been collected by then. What works is stamping the object at
construction with the current nodeid and reading it back from a patched finalizer. Self-check
any such probe against a test that leaks on purpose before believing a clean run.

The pytest run above includes the real-ConPTY integration tests (`-m conpty`,
Windows-only, `tests/test_conpty_integration.py`) and the harness-adapter coverage
matrix (`tests/test_harness_adapter_matrix.py`, which fails when a harness is added to
the registry with no adapter/spawn coverage) — the `not live_*` filter does not deselect
them. `.github/workflows/ci.yml` mirrors this gate on `windows-latest` and adds the
production frontend build and the full Playwright renderer suite
(`npm run test:renderer`, in `frontend/`). `frontend/tsconfig.json` includes only
`src`, so the plain `npx tsc --noEmit` does NOT typecheck `frontend/test/`; everything
under `test/` is typechecked separately by `npm run check:tests`
(`tsconfig.test.json`, `src` + `test`), which `.worktree-verify` runs so a
harness prop that drifts from the component it mounts fails at typecheck instead of only
at Playwright runtime (the way `pane-layout.spec.ts` once rotted), and a hand-built unit
fixture that no longer matches the type it fakes fails there too.
It is full `strict`, the same rules as `src` - adopting the 184 unit-test files cost 12
errors in 4 files, which was not worth a weaker second set of rules to remember.

**Put every assertion inside a `test()` body.** A `*.test.ts` that asserts at module
scope is the suite's worst failure mode: when such an assertion throws, `all.ts` stops
importing there, every later suite silently never registers, and the summary still reads
`# fail 0` (measured: 2042 tests became 1047, all "passing"; only the exit code told the
truth). `testRegistry.test.ts` fails the gate on any test file that does not register
with `node:test`.

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
