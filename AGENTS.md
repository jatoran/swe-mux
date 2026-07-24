# swe-mux — agent instructions

Documentation lives in `.docs/`; the routing table in `.docs/CLAUDE.md` says which docs to
update for which kind of change. Read the relevant feature doc before changing a subsystem.

## Applying your changes (session-preserving reload)

Live agent/terminal sessions are owned by a separate PTY supervisor process and survive
daemon restarts and app rebuilds. Use these flows instead of killing swe-mux:

- **Frontend change**: `cd frontend && npm run build` (outputs into `src/swe_mux/static`),
  then refresh the browser / UI menu → "Reload UI". No daemon restart needed.
- **Backend/daemon change**: `curl -X POST http://127.0.0.1:8765/api/daemon/restart`
  (or UI menu → "Reload daemon (keep sessions)", or `mux reload-daemon`). The daemon
  restarts in place with your code; every session survives.
- **Frozen desktop app update** (rebuild `dist/` + relaunch, sessions preserved —
  safe to run from a session inside swe-mux): `uv run python packaging/redeploy_desktop.py`.
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
