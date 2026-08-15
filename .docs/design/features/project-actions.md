# Project Actions

## What it is

The Project-level **Run** menu is the single launch surface for a new Claude, Codex, shell, custom terminal, worktree session, or an explicitly selected repository task.
It imports tasks from the Project root and opens every resulting process as an ordinary Project-owned terminal tab.

The worktree launcher is an explicit Git operation rather than a Project Action.
It creates a named branch below the configured global worktree root through `POST /api/git/worktrees`, closes the launcher once that durable operation succeeds, then bootstraps and starts the selected backend through `POST /api/git/worktrees/session`.
The browser immediately creates and focuses a client-only unpanned pending session at the worktree path.
Its full-workspace setup surface leaves the durable pane tree unchanged, so selecting another session removes setup from view without disturbing existing splits.
The pending row is replaced in place by the daemon session when setup and spawn finish.
Moving elsewhere during setup is respected: completion updates the pending location without reclaiming focus.
Its suggested checkout path is grouped by Project and branch below `worktree_root`, which defaults to `<data_dir>/worktrees` and is editable in Settings under Git and processes.
The resulting absolute path remains editable before creation, and changing the setting does not move existing worktrees.
Whitespace entered in the branch field becomes `-`, keeping the Git branch and suggested filesystem path aligned.

## Discovery

Discovery is read-only and recognizes three optional root files:

- `.vscode/tasks.json`: JSONC `shell` and `process` tasks, including `dependsOn`, task cwd/env,
  the `inputs` array (`promptString` and `pickString` only), `detail` as a description, and a
  bounded set of workspace/environment variables. VS Code presentation, pane, and split hints
  are deliberately ignored, as are `command` inputs: those run an editor command, which has no
  meaning outside VS Code and would be a second execution path if it did.
- `package.json`: root scripts, launched through the lockfile-selected package manager (`pnpm`,
  `yarn`, `bun`, or `npm`). The script body is carried as the action's description, which is
  what distinguishes `build` from `build:watch` for a caller that cannot see the file.
- `.swe-mux/actions.toml`: native version-1 actions with one command or up to 32 steps. Steps may
  be `shell` or `process`, carry args/cwd/env/platforms/timeout, and start in parallel by default
  or in declared order with `sequential = true`.

Compound actions do not create a visible Run Group. Each step is a normal terminal in the pane
that was active for the target Project, and the final tab receives focus. Sequential VS Code
dependencies preserve start order only; swe-mux does not import VS Code background/readiness or
completion-gating semantics.

Each step is also marked as a one-shot terminal. Its stdout/stderr remains in the terminal's
ConPTY, exit code `0` ends as **completed**, and a nonzero root exit remains **crashed** with the
exit code retained. Interactive shells and agent terminals keep their separate long-lived
lifecycle semantics.

## The format is a manifest, not a program

`.swe-mux/actions.toml` has no conditionals, no loops, and no variables beyond a fixed set.
Logic belongs in a script in the repository, with a `process` step pointing at it, written in whatever language the repository already uses.

That is a deliberate refusal to embed an interpreter, for three reasons that outlive any one of them:

- A third-party runtime inside the daemon means a plugin exception takes down observation for
  every live session (`../../development/PLUGIN_SYSTEM_FINDINGS.md`: subprocess only, never
  in-process).
- The frozen desktop app cannot assume a toolchain. "A step is exe plus argv" survives
  packaging; "a step is a script we interpret" does not.
- The agent-facing specification has to stay small enough to fit in one tool description.
  Control flow makes that impossible.

The complete authoring reference is `src/swe_mux/assets/project-actions-schema.md`.
It ships as a package asset rather than as documentation prose so one file serves both readers:
a person opening it, and an agent calling `project_actions(include_schema: true)`.
Two copies would drift, and the copy an agent reads is the one that must be right.

### Declarative additions

| Field | Where | Meaning |
|---|---|---|
| `description` | action | What the action is for. The only thing an agent can read to tell two similar actions apart. |
| `inputs` | action | Typed values collected at run time. `string` or `choice`, at most 16, at most 4096 characters each. |
| `platforms` | action or step | `windows`, `linux`, `darwin`. A step for another host is dropped with a diagnostic. |
| `timeout_seconds` | step | Above 0, at most 86400. The step's session is stopped when it elapses. |

`${input:id}` is substituted **at run time and never at discovery**.
The Run menu preview, the approval dialog, and the trust fingerprint therefore all describe the *template*, so an input cannot introduce a command a human did not approve.
Containment of a `cwd` naming an input is checked again after substitution, because it cannot be checked against a template.
A value outside a `choice` input's options, an unknown input key, and a reference to an undeclared input are each refused rather than silently substituted empty.

An action whose every step is for another platform is not shown, and says so as a diagnostic.
That is what lets one action carry a Windows and a POSIX implementation instead of the repository holding two near-identical entries.

A step timeout is a live timer, not a persisted deadline: it is not restored across a daemon restart.
The alternative is persisting a deadline per session and reconciling it at adoption, which is real machinery for a bound whose purpose is stopping a runaway task on the machine the user is sitting at.

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
  non-interactive shell launch profile, resolved against the step's own cwd
  (`launch-profiles.md`).

## Reading the result

A task whose result nobody can read is a task that writes to `/dev/null`.
Three things close that:

- `completion_mode` and `exit_code` travel in the MCP session summary.
  `completion_mode` is what makes `exit_code: null` readable: on a one-shot task it means "still
  running", and on an interactive pane it means there is no such thing as a result.
- `get_session(output_bytes: N)` returns the tail of a shell or task session's terminal output,
  capped at 64 KiB and passed through the same `looks_like_secret` redaction gate every other
  excerpt uses. A task that echoes a token is exactly what that gate is for.
- An agent session refuses the read and names `read_transcript` instead. Its PTY bytes are a
  differential frame stream, not a transcript: returning them would be gibberish at high context
  cost.

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

Substituted inputs travel in the retained argv, so relaunching an action that asked for a value
repeats the run that happened rather than prompting again.

## Trust boundary

- Merely opening the Run menu never executes repository content.
- Before the first execution of an action, the browser shows the contributing task file, the
  exact command previews, and a **diff against the last approved bytes**. Approval stores a
  local SHA-256 of that file's bytes, keyed by canonical Project root.
- **Approval is per source file.** The three files are authored by different people for
  different reasons, and one combined digest meant that editing `.swe-mux/actions.toml`
  un-trusted the VS Code tasks and the package scripts as well, so every Run menu entry needed a
  fresh human approval for a change that touched none of them. That became the common case once
  agents started authoring actions.
- Trust is machine-local state in the daemon data directory, never portable repository state.
  The approved bytes are retained alongside the digest, up to 128 KiB per file, so the approval
  dialog can show what changed: "these files changed" cannot separate a renamed label from a new
  `curl | sh`.
- The pre-per-file store held one digest as a bare string. It is still honoured exactly: if it
  matches the current combined digest, nothing has changed since that approval and every present
  file is approved. If it does not, the old format cannot say which file moved, so nothing is.
- Action cwd is resolved beneath the canonical Project root; an escaping cwd is rejected during
  discovery and again after input substitution. The session retains immutable Project ownership
  even when the child command later changes its runtime cwd.
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

## The agent surface

Two MCP tools (`mux-mcp.md`), both thin callers over the same services the Run menu uses:

- `project_actions` lists what a Project declares, per-action, with its source file, its steps,
  its declared inputs, and whether that file is currently approved.
  `include_schema: true` returns the authoring reference in the same result.
  One tool rather than two: the agent that lists actions is the agent that wants to write one,
  and a separate documentation tool is not called.
- `run_action` starts one **already-approved** action. An unapproved action refuses with
  `trust_required` naming the file a human must review, as a typed result rather than a protocol
  fault, so an agent can adapt instead of retrying blindly.

**`run_action` grants no new authority.** An agent in a mux session already holds a shell and can
type the command directly; the same-host boundary decision (`agent-messaging.md`) already
establishes that this token is identity and read scope rather than an authorization boundary.
What the tool adds is that the command it can run is one whose exact bytes a human approved, and
an agent that edits a task file un-approves it, so **an agent cannot approve its own command**.
Writing an action is a proposal; a human turns it into an authority.

An agent-started action goes through the same trust check, substitution, spawn path, and timeout
arming as the Run menu, because the two share `_start_project_action`. A second implementation
would be a second authority path.

## API and UI

```text
GET  /api/projects/{project_id}/actions
GET  /api/projects/{project_id}/actions/diff
POST /api/projects/{project_id}/actions/trust   {fingerprint}            # every present file
POST /api/projects/{project_id}/actions/trust   {source, fingerprint}    # one file
POST /api/projects/{project_id}/actions/run     {action_id, inputs}
```

The desktop active-Project header and every Project row expose Run.
Mobile exposes the same menu from its contextual toolbar.
Built-ins are the launchable agent harnesses and their launch profiles, `Shell`,
`Custom terminal…`, and `New worktree session…`; imported actions follow in source sections.
An action whose file is not approved carries a lock marker rather than a play marker, so the
prompt that follows is expected rather than a surprise.

## Key files

- `src/swe_mux/project_actions.py`
- `src/swe_mux/assets/project-actions-schema.md` (the authoring reference)
- `src/swe_mux/spawn_contract.py`
- `src/swe_mux/server.py` (`_start_project_action`, `_arm_action_timeout`, `diff_project_actions`)
- `src/swe_mux/mcp.py` (`project_actions`, `run_action`)
- `frontend/src/ProjectRunMenu.tsx`
- `frontend/src/worktreeLaunch.ts`
- `frontend/src/App.tsx`
- `tests/test_project_actions.py`, `tests/test_project_actions_v2.py`,
  `tests/test_mcp_project_actions.py`

## Relates to

- `projects.md`: canonical root and repository configuration boundary.
- `launch-profiles.md`: the shell a task step runs through.
- `sessions.md`: task processes are ordinary daemon-owned sessions.
- `workspace-layout.md`: action terminals join the focused pane as tabs.
- `mux-mcp.md`: the agent-facing tools and their bounds.
- `processes-and-previews.md`: task listeners become Project routing registrations; users still
  choose which Preview tabs to open.
