# Project Actions

## What it is

The Project-level **Run** menu is the single launch surface for a new Claude, Codex, shell,
custom terminal, or an explicitly selected repository task. It imports tasks from the Project
root and opens every resulting process as an ordinary Project-owned terminal tab.

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

This is an explicit exception to the normally inert repository-configuration rule: a task file
can authorize only the command the user selected, only after exact-content approval. It cannot
trigger itself, broaden automation authority, store credentials, or bypass the normal session
and process ownership model.

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

The desktop active-Project header and every Project row expose Run. Mobile exposes the same menu
from its contextual toolbar. Built-ins are `Claude`, `Codex`, `Shell`, and `Custom terminal…`;
imported actions follow in source sections.

## Key files

- `src/swe_mux/project_actions.py`
- `src/swe_mux/action_runner.py`
- `src/swe_mux/server.py`
- `frontend/src/ProjectRunMenu.tsx`
- `frontend/src/App.tsx`

## Relates to

- `projects.md`: canonical root and repository configuration boundary.
- `sessions.md`: task processes are ordinary daemon-owned sessions.
- `workspace-layout.md`: action terminals join the focused pane as tabs.
- `processes-and-previews.md`: task listeners become Project routing registrations; users still
  choose which Preview tabs to open.
