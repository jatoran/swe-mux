# Authoring `.swe-mux/actions.toml`

This is the complete reference for a Project's own run actions.
swe-mux serves this file to agents through the `project_actions` MCP tool (`include_schema: true`) and to people at `src/swe_mux/assets/project-actions-schema.md`.

## What an action is, and is not

An action is a **manifest entry**, not a program.
It names a command, its arguments, its directory, and its environment.
It has no conditionals, no loops, and no variables beyond the fixed set listed here.

Put logic in a real script in the repository and point a `process` step at it:

```toml
[[actions]]
id = "deploy"
label = "Deploy"
type = "process"
command = "python"
args = ["tools/deploy.py", "--env", "staging"]
```

That script is written in whatever language the repository already uses.
swe-mux embeds no interpreter, and the deliberate consequence is that this file stays small enough to describe in one page.

## File shape

```toml
version = 1          # required, and currently always 1

[[actions]]
id = "verify"        # required; letters, digits, dot, dash, underscore
label = "Verify"     # optional; defaults to id
description = "Run the full test and lint suite."   # optional but recommended
sequential = false   # optional; false starts every step at once
platforms = ["windows"]                             # optional; applies to each step

# A single-step action puts the step fields directly on the action.
type = "shell"
command = "uv run pytest tests -q"
```

A multi-step action uses `[[actions.steps]]` instead:

```toml
[[actions]]
id = "services"
label = "Services"
description = "Start the frontend and backend together."
sequential = false

[[actions.steps]]
name = "frontend"
type = "process"
command = "npm"
args = ["run", "dev"]
cwd = "frontend"

[[actions.steps]]
name = "backend"
command = "uv run python -m app"
timeout_seconds = 3600
[actions.steps.env]
APP_ENV = "development"
```

An action holds one command or up to 32 steps.
A Project shows at most 128 actions across every source.

## Action fields

| Field | Type | Meaning |
|---|---|---|
| `id` | string, required | Stable identity. Letters, digits, dot, dash, underscore. The MCP id is `native:<id>`. |
| `label` | string | Shown in the Run menu. Defaults to `id`. |
| `description` | string | What the action is for. The only thing an agent can read to tell two similar actions apart, so write one. |
| `sequential` | bool | `true` starts steps in declared order. `false` (default) starts them all at once. |
| `platforms` | array | `windows`, `linux`, `darwin`. Applies to every step that does not set its own. |
| `inputs` | array of tables | Values collected when the action runs. See below. |
| `steps` | array of tables | The steps. Omit for a single-step action and put the step fields on the action. |

**`sequential` starts steps in order. It does not wait for one to finish.**
A step that must complete before the next begins belongs in one step, using the shell's own `&&` or `;`.

## Step fields

| Field | Type | Meaning |
|---|---|---|
| `name` | string | Terminal tab name. Defaults to the action label. |
| `type` | `shell` or `process` | `shell` hands the command line to a shell. `process` resolves the command on PATH and runs it directly. Defaults to `shell`. |
| `command` | string, required | The command. |
| `args` | array of strings | Arguments. For a `shell` step they are quoted and appended to `command`. |
| `cwd` | string | Directory, relative to the Project root. Must stay inside it. |
| `env` | table of strings | Extra environment. Merges over the launch profile and under mux's own identity variables. |
| `platforms` | array | `windows`, `linux`, `darwin`. Overrides the action's list. |
| `timeout_seconds` | number | Stop the step after this long. Above 0 and at most 86400. Omit for a step with no bound. |

### `shell` versus `process`

- `shell`: the command string passes through your shell, so pipes, redirection, and `&&` work.
  With no `args`, the command string is passed through untouched.
  `&&` works in PowerShell 7, CMD, and POSIX shells, but not Windows PowerShell 5.1.
- `process`: no shell. The command is resolved on PATH and given `args` verbatim.
  Prefer this when you do not need shell syntax; it has no quoting surprises.
  A `.cmd`/`.bat` shim (every npm-family entry point on Windows) is routed through the command processor automatically.

## Inputs

An input turns one action into a family of commands, instead of three near-identical entries.

```toml
[[actions]]
id = "deploy"
label = "Deploy"
description = "Deploy to a chosen environment."
command = "python"
type = "process"
args = ["tools/deploy.py", "--env", "${input:environment}"]

[[actions.inputs]]
id = "environment"
label = "Environment"
kind = "choice"
options = ["staging", "production"]
default = "staging"
```

| Field | Type | Meaning |
|---|---|---|
| `id` | string, required | Referenced as `${input:id}`. Letters, digits, dot, dash, underscore. |
| `label` | string | Prompt text. Defaults to `id`. |
| `kind` | `string` or `choice` | `choice` requires `options`. |
| `options` | array of strings | The allowed values for a `choice` input. |
| `default` | string | Used when the caller supplies nothing. A `choice` default must be one of its options. |

Rules:

- At most 16 inputs per action; a value is at most 4096 characters.
- `${input:id}` may appear in `command`, `args`, `cwd`, and `env` values.
- Referencing an input that is not declared is an error, and the action does not load.
- A declared input that nothing references is reported as a diagnostic and dropped.
- A `string` input with no default is required.
- Substitution happens when the action runs, never when it is discovered.
  The Run menu preview, the approval dialog, and the trust fingerprint all show the template.
  An input therefore cannot introduce a command a human did not approve.

## Variables

Available in `command`, `args`, `cwd`, and `env` values:

| Variable | Expands to |
|---|---|
| `${workspaceFolder}` | The Project root, absolute. |
| `${workspaceFolderBasename}` | The Project root's folder name. |
| `${pathSeparator}` | `\` on Windows, `/` elsewhere. |
| `${env:NAME}` | The daemon's environment variable `NAME`, or empty. |
| `${input:id}` | A declared input, filled in at run time. |

Any other `${...}` is an error and the action does not load.
There is deliberately no `${command:...}`: it would be a second execution path.

## Platforms

A step whose `platforms` excludes this host is dropped, and the drop is reported as a diagnostic.
An action left with no runnable step is not shown, and says so as a diagnostic.

That is what lets one action carry two implementations:

```toml
[[actions]]
id = "clean"
label = "Clean"
sequential = false

[[actions.steps]]
name = "clean (windows)"
platforms = ["windows"]
command = "Remove-Item -Recurse -Force build"

[[actions.steps]]
name = "clean (unix)"
platforms = ["linux", "darwin"]
command = "rm -rf build"
```

## Trust

Nothing in this file runs because it exists.

- Opening the Run menu never executes repository content.
- Before an action runs the first time, a human sees the file and the exact command previews, and approves them.
- Approval stores a SHA-256 of that file's exact bytes, on this machine only, per Project root.
- **Editing the file un-approves it.** The next run needs a new human approval, which shows a diff against the approved bytes.
- Approval is per file. Editing `.swe-mux/actions.toml` does not un-approve `.vscode/tasks.json` or `package.json`.

For an agent this means: you can write and edit this file freely, and you cannot approve your own command.
Writing an action is a proposal. A human turns it into an authority.

## What happens when an action runs

- Each step becomes one ordinary terminal session in the Project, marked one-shot.
- Exit code `0` ends as **completed**; a nonzero exit stays **crashed** with the code retained.
- `get_session` reports `exit_code`, and with `output_bytes` returns the tail of the terminal output.
- Each session carries a **Relaunch** action that replays its exact executable, argv, directory, and environment.
- No swe-mux executable appears in a task's process tree, so a task outlives a rebuild of the app that launched it.

## Other sources

Two more files contribute actions to the same menu, read-only:

- `.vscode/tasks.json`: `shell` and `process` tasks, `dependsOn`, task `cwd`/`env`, the `inputs` array (`promptString` and `pickString`), and the variables listed above.
  Presentation, pane, and split hints are ignored.
  `dependsOrder: "sequence"` preserves start order only; background/readiness and completion gating are not imported.
- `package.json`: root `scripts`, launched through the lockfile-selected package manager (`pnpm`, `yarn`, `bun`, or `npm`).

Use `.swe-mux/actions.toml` for anything those two cannot express: inputs, platforms, timeouts, per-step environment, and a description.
