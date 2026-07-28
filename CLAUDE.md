# swe-mux — agent instructions

Documentation lives in `.docs/`; the routing table in `.docs/CLAUDE.md` says which docs to
update for which kind of change. Read the relevant feature doc before changing a subsystem.

## Applying your changes (session-preserving reload)

Live agent/terminal sessions are owned by a separate PTY supervisor process and survive
daemon restarts and app rebuilds. Use these flows instead of killing swe-mux:

- **Frontend change**: `cd frontend && npm run build` (outputs into `src/swe_mux/static`),
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

Details, constraints, and the supervisor design: `.docs/development/SESSION_PRESERVING_RELOAD.md`
(§7.5+ addendum has the exact workflows), `.docs/design/features/desktop-shell.md` (packaging),
`.docs/technical/backend/packages.md` (supervisor rules: hash-gated source closure, cwd-lock
hazard, restart contract).

## Verification

Backend: `uv run pytest tests -q -m "not live_agent and not live_subagent and not
live_telemetry and not live_quota"`, `uv run ruff check src/swe_mux tests packaging`,
`uv run mypy`. Frontend (in `frontend/`): `npx tsc --noEmit`, `npm test`.
