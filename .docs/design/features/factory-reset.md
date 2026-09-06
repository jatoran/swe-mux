# Factory reset

Returns an install to its first-run state.
Configuration, Projects, history, the database, notes, prompts, plugins, stored credentials and logs stop describing this machine, and the next start comes up on onboarding with nothing carried over.
It is the other end of `design/features/first-run.md`: one creates the install, the other ends it.

Implementation: `src/swe_mux/factory_reset.py`, `src/swe_mux/routes/maintenance.py`, the `factory-reset` startup phase in `src/swe_mux/server.py`, `frontend/src/factoryReset.ts`, `frontend/src/FactoryResetDialog.tsx`.
Surface: Settings -> Maintenance -> **Factory reset**.

## Why it cannot run in the daemon that was asked for it

A running daemon holds `mux.db`, its stores hold every JSON file beside it, and the PTY supervisor holds live sessions in a separate process the daemon cannot speak for.
So a reset is a durable *request* honoured by a successor, in the pattern `db_maintenance` established for `VACUUM`.

1. `POST /api/maintenance/factory-reset` writes `factory-reset.json` into the data directory.
   The request is the only durable record of consent, so it is written before anything is destroyed.
2. The route reaps every session and stops the supervisor (`supervisor_client.kill_server`).
   This is the one operation in swe-mux where reaping every session is the point rather than a cost.
3. The route spawns a successor daemon and sets the stop event.
4. The successor honours the request in the `factory-reset` startup phase, ahead of `database-maintenance` and before any store opens a file.
   That is the only moment a daemon owns its own data directory outright.

The phase waits for the predecessor process to exit before it starts, because renaming a file another process has open fails on Windows.
A request that could not run is kept for the next start.
A request that *did* run is cleared before the work rather than after: a reset that dies part-way has already moved an unknowable amount of the install aside, and re-running it would sweep the fresh install it had just begun writing.

## What it does

**Nothing is deleted.**
Each top-level entry of the data directory is renamed into `.trash/factory-reset-<timestamp>/`.
The reset is therefore a handful of renames rather than a recursive delete of a multi-gigabyte tree, and the whole of the old install stays recoverable by hand.
`storage_usage` reports `.trash` as its own bucket, so the space it costs is visible rather than hidden.

**What survives is a closed keep-list**, not a delete-list.
Residue is what defeats the feature, and a data-directory entry added later is far more likely to be state than to be load-bearing, so the list fails towards a clean reset.
`tests/test_factory_reset.py` pins it.

| Kept | Why |
| --- | --- |
| `bin` | Shim directory on the user's PATH, rewritten from scratch on every daemon start. |
| `webview` | Held open by the desktop shell, which is still running. Its origin state is cleared by the client instead. |
| `voice-models`, `voice-runtime` | Hundreds of megabytes of cached downloads. Assets, not configuration. |
| `frontend-overlay` | Build payload pinned to this backend. Removing it reverts the UI to a possibly-stale bundled copy. |
| `worktrees` | The user's own git checkouts. Reported, never removed. |
| `desktop-control.token` | Authenticates the shell process that is still running against the daemon it manages. |
| `.trash` | Where the reset is moving everything else. |

**A repository is never touched.**
`.swe-mux/config.toml`, `actions.toml` and `project-context.md` are committed content in someone else's repository, and `.swe-mux/notes/` is the least recoverable thing near this code.
Forgetting a Project is what a reset does; editing that Project's working tree is not, and would surface as an unexplained dirty `git status`.
Worktree checkouts stay for the same reason, one step stronger: uncommitted work inside one has no other copy.
The result records where they are.

**A failure is recorded, never fatal.**
Every step is best-effort, and the daemon starts regardless.
The files the process holds open itself - its own logs - cannot be renamed on Windows, so they are truncated in place and reported as `truncated` rather than counted as moves.

**The process forgets the install it just reset.**
`restore_defaults` rebuilds the live `Config` through `load_config` against the now-absent path.
Without it the daemon would finish starting on the settings it loaded before the sweep and write them back the first time anything saved, quietly reinstating the install.
`load_config` writes a default `config.toml` for a path that has none, which is what an ordinary first start does too, so a reset install has a default config on disk rather than no config.

The one thing that does not take effect until the *next* start is anything the daemon read before the phase ran: the listening port and the startup log level are already bound and configured by then.
An install on a non-default port therefore keeps serving that port for the rest of the boot and moves to the default on the following start.
The client polls the origin it is already on, so this is invisible to the reset itself.

**The client empties its own origin.**
The daemon cannot reach localStorage, IndexedDB, the caches or the service worker, and a reset that left the last install's layouts, dismissed banners and remembered tab would look like one that did not work.
`clearClientStorage` runs on the client that pressed the button, once the daemon has accepted, and reports the stores it could not clear rather than throwing.

## The opt-in external group

Parts of the footprint live outside the data directory.
Only those with a removal path that runs unattended are offered, and only on request, because each was a separate disclosed act rather than part of installing swe-mux:

- **Agent skills** (`skill_install.remove`), which refuses anything without the `managed-by: swe-mux` marker. A user-authored skill sharing the directory is reported and left.
- **Windows shortcuts** (`shortcuts.apply_shortcuts(remove=True)`), which addresses every slot.

The rest is named in the result rather than implied away: the inbound firewall rule needs an elevated prompt, and Tailscale Serve keeps its own configuration (`tailscale serve reset`).

## Refusals

| Refusal | Why |
| --- | --- |
| `not_local` (403) | Loopback only. Every other destructive control is scoped to a session or a Project and is reachable from the phone; this one is scoped to the machine. |
| `restart_unavailable` (409) | A daemon with no relaunch command would reset the install and never come back. |
| `redeploy_in_flight` (409) | A bundle swap is rewriting the app; the two must not interleave. |
| `not_confirmed` (400) | The typed phrase did not match. The daemon states the phrase and the client echoes it, so there is one copy. |

## What the dialog must say before the press

Every fact is the daemon's answer from `GET /api/maintenance/factory-reset`; a second implementation of "what will this destroy" would eventually disagree with the one that destroys it.
Ordered by what matters:

- **Sessions end**, named rather than counted and with the ask to stop anything mid-run first. A count is a number; a name is a decision.
- **What survives** - repositories, worktree checkouts, voice downloads, the PATH shims. The fear a reset produces is about the work, and an unanswered fear is what stops someone pressing a button they wanted.
- **Where it goes** - the data directory is moved into `.trash`, which is both the safety net and a real disk cost.

Consent is typed, not clicked.
