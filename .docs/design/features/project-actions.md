# Project Actions

## What it is

The Project-level **Run** menu is the single launch surface for a new Claude, Codex, shell, custom terminal, worktree session, or an explicitly selected repository task.
It imports tasks from the Project root and opens every resulting process as an ordinary Project-owned terminal tab.

The worktree launcher is an explicit Git operation rather than a Project Action.
It creates a named branch below the configured global worktree root through `POST /api/git/worktrees`, closes the launcher once that durable operation succeeds, then bootstraps and starts the selected backend through `POST /api/git/worktrees/session`.
The completed session joins the Project session list without changing the user's current Project, pane, tab, or focus.
Its suggested checkout path is grouped by Project and branch below `worktree_root`, which defaults to `<data_dir>/worktrees` and is editable in Settings under Git and processes.
The resulting absolute path remains editable before creation, and changing the setting does not move existing worktrees.
Whitespace entered in the branch field becomes `-`, keeping the Git branch and suggested filesystem path aligned.

## Discovery

Discovery is read-only and recognizes three optional root files:

- `.vscode/tasks.json`: JSONC `shell` and `process` tasks, including `dependsOn`, task cwd/env,
  and a bounded set of workspace/environment variables. VS Code presentation, pane, and split
  hints are deliberately ignored.
- `package.json`: root scripts, launched through the lockfile-selected package manager (`pnpm`,
  `yarn`, `bun`, or `npm`).
- `.swe-mux/actions.toml`: native version-1 actions with one command or up to 32 steps. Steps may
  be `shell` or `process`, carry args/cwd/env, and start in parallel by default or in declared
  order with `sequential = true`.

Compound actions do not create a visible Run Group. Each step is a normal terminal in the pane
that was active for the target Project, and the final tab receives focus. Sequential VS Code
dependencies preserve start order only; swe-mux does not import VS Code background/readiness or
completion-gating semantics.

Each step is also marked as a one-shot terminal. Its stdout/stderr remains in the terminal's
ConPTY, exit code `0` ends as **completed**, and a nonzero root exit remains **crashed** with the
exit code retained. Interactive shells and agent terminals keep their separate long-lived
lifecycle semantics.

## Execution

A step becomes one ordinary spawn request: `executable`, `argv`, the step's contained `cwd`, and
its `env`. **No swe-mux binary appears in a task's process tree.** One did once, and a live task
terminal then held `dist/swe-mux` open and blocked the frozen redeploy swap; keeping the tree
free of them is what lets a task outlive a rebuild of the app that launched it.

- `shell` steps: command and args are folded into one command line quoted for the target shell
  (PowerShell single-quoting with `''` escapes and a call operator for a quoted command; cmd via
  `list2cmdline` plus metacharacter quoting; POSIX via `shlex`), then handed to that shell. VS
  Code semantics: the args array is quoted and appended, never dropped. A step with no args
  passes its command string through untouched, so syntax supported by the target shell is
  preserved. `&&` works in PowerShell 7, CMD, and POSIX shells but not Windows PowerShell 5.1.
- `process` steps: resolved on `PATH` by the daemon. A `.cmd`/`.bat` shim (every npm-family entry
  point on Windows) is routed through `%COMSPEC%`, since it is not a real executable.
- Shell resolution for a step without an explicit `options.shell.executable` uses the Project's
  non-interactive shell profile, resolved against the step's own cwd.

## Relaunch

Task-launched terminals are marked **relaunchable** and carry a **Relaunch** action on their
terminal rail (and the `session.relaunch` command). Relaunch is *from-record*: it replays the
session's exact retained executable, argv, `spawn_cwd`, and `spawn_env`, so no task file is
re-read and no trust re-approval is required. All four are replayed because a step's directory
and environment are spawn inputs in their own right and cannot be recovered from argv alone.
`POST /api/sessions/{sid}/relaunch`
spawns the fresh copy first (so a spawn failure leaves the original intact), then stops and removes
the old session; the browser swaps the new session id into the old one's layout leaf, keeping the
tab, split, and focus in place. It works on a still-running task (stop then restart) and on an
already-completed/crashed one (restart in place). Agent and plain shell sessions are never
relaunchable, so their rails are unaffected; editing the task definition itself still goes through
the Run menu and its normal trust check.

## Trust boundary

- Merely opening the Run menu never executes repository content.
- Before the first task execution, the browser shows every contributing task file and the exact
  command previews. Approval stores a local SHA-256 fingerprint of the presence and bytes of all
  supported task files, keyed by canonical Project root.
- Adding, removing, or editing any supported task file changes the fingerprint and requires a
  new approval. Trust is machine-local state in the daemon data directory, never portable
  repository state.
- Action cwd is resolved beneath the canonical Project root; an escaping cwd is rejected during
  discovery. The session retains immutable Project ownership even when the child command later
  changes its runtime cwd.
- Invalid or unsupported imports remain visible as diagnostics and do not block built-in session
  launchers.
- Worktree launch fields are user-authored and do not execute imported Project Action files.
  Worktree bootstrap is a separate, narrow repository execution path: an explicit create-and-spawn request runs `[worktree].setup_command` or the executable `.worktree-setup` convention before the harness starts.
  Its failure is shown in session scrollback and never blocks session creation or removes the worktree.
  A failed session start leaves the successfully created worktree intact and changes the launcher to retry only `POST /api/sessions` against that Git-listed root.

This is an explicit exception to the normally inert repository-configuration rule: a task file
can authorize only the command the user selected, only after exact-content approval. It cannot
trigger itself, broaden automation authority, store credentials, or bypass the normal session
and process ownership model.

Project setup commands (`projects.md`) reuse this spawn contract but sit outside the trust
boundary entirely: they are typed by the user into machine-local settings rather than imported
from a checkout, so there is nothing to fingerprint and nothing repository-supplied to approve.
Worktree setup is different: it is committed repository configuration, but it has authority only after the user explicitly selects New worktree session, and only for the newly created Git-listed root before that one session starts.

## Native file shape

```toml
version = 1

[[actions]]
id = "services"
label = "Services"
sequential = false

[[actions.steps]]
name = "frontend"
type = "process"
command = "npm"
args = ["run", "dev"]
cwd = "."

[[actions.steps]]
name = "backend"
command = "uv run python -m app"
[actions.steps.env]
APP_ENV = "development"
```

An action may put `command`, `type`, `args`, `cwd`, and `env` directly on `[[actions]]` when it
has only one step.

## API and UI

```text
GET  /api/projects/{project_id}/actions
POST /api/projects/{project_id}/actions/trust   {fingerprint}
POST /api/projects/{project_id}/actions/run     {action_id}
```

The desktop active-Project header and every Project row expose Run.
Mobile exposes the same menu from its contextual toolbar.
Built-ins are the launchable agent harnesses, `Shell`, `Custom terminal…`, and `New worktree session…`; imported actions follow in source sections.

## Key files

- `src/swe_mux/project_actions.py`
- `src/swe_mux/spawn_contract.py`
- `src/swe_mux/server.py`
- `frontend/src/ProjectRunMenu.tsx`
- `frontend/src/worktreeLaunch.ts`
- `frontend/src/App.tsx`

## Relates to

- `projects.md`: canonical root and repository configuration boundary.
- `sessions.md`: task processes are ordinary daemon-owned sessions.
- `workspace-layout.md`: action terminals join the focused pane as tabs.
- `processes-and-previews.md`: task listeners become Project routing registrations; users still
  choose which Preview tabs to open.
