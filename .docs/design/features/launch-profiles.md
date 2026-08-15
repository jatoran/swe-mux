# Launch profiles

## What it is

A launch profile is a named executable/argv/environment definition for one backend.
`backend = "shell"` is a terminal; `backend = "<harness>"` starts an agent CLI with extra arguments.
One Project can therefore offer `Claude` and `Claude (plan)` side by side in the Run menu, which the single global `harness_args` list could not express.

- Resolution precedence is raw executable, request profile, Project profile, project-local `.swe-mux/config.toml` selection, then the global default.
- `SessionRecord.shell_profile_id` and history preserve the selected identity for both kinds; effective executable and argv remain separate diagnostics.
- `New terminal` uses defaults immediately.
  `New terminal custom…` chooses only a shell profile; its Project root is fixed and read-only.

### Stored names

The stored keys are `shell_profiles`, `default_shell_profile`, and the history column `shell_profile_id`.
They keep those names because renaming them would rewrite a user's `~/.mux/config.toml`, a committed `.swe-mux/config.toml` key, and a database column, none of which buys a capability.
The concept is a **launch profile** everywhere it is spoken about; those three names are storage history.
The Python type is `LaunchProfile` and the TypeScript type is `LaunchProfile`.

## Agent launch profiles

- A profile declares `backend`. It defaults to `shell`, so a profile written by an older build loads unchanged.
- `executable` is optional on an agent profile and inherits `harness_exe[backend]`.
  A profile usually exists to add arguments, not to name a different binary.
- `cwd_strategy`, `cwd_integration`, and the PowerShell bootstrap are shell-only and are **refused** on an agent profile rather than accepted and ignored.
  An agent launch never reaches the resolver that implements them.
- Applying a profile to a backend it does not declare is refused at the spawn boundary, in both directions.

### The three argument slots

Least specific first:

1. `harness_args[backend]` - the global default for the harness.
2. The launch profile's `args`.
3. Whatever the launch itself asked for (`POST /api/sessions {argv}`, `mux spawn --arg`, a seed prompt).

Every adapter already concatenated `default_args` before `opts.args`, so the profile prepends into the second slot and no adapter changed.

### Reserved argv

`HarnessDescriptor.reserved_launch_args` declares the argv an adapter builds for itself.
A launch profile that sets one is refused, at configuration save time and again at the spawn boundary.

| Harness | Reserved |
|---|---|
| `claude` | `--session-id` `--settings` `--mcp-config` `--resume` `-r` `--continue` `-c` `--fork-session` |
| `codex` | `resume` `notify=` `mcp_servers.mux.` |
| `omp` | `--resume` |
| `pi` | `--session` `--resume` |
| `opencode` | `--session` |

An entry ending in `=` or `.` matches by prefix.
That is how a value-carrying config override is named without reserving the flag introducing it: Codex takes arbitrary `-c key=value` pairs and mux injects two of them, so `-c` itself stays available to the user.

The refusal exists because the failure it prevents is silent and total.
A profile passing its own `--settings` replaces the file holding that pane's hook identity.
The CLI runs, the pane looks healthy, and nothing ever reports a turn: the session is unobserved for the rest of its life, with no error anywhere.
Refusing the profile is the only point at which that is visible.

The check runs twice on purpose.
Configuration reaches the daemon by three routes with no shared validator: the settings API, a hand-edited `config.toml`, and a file written by a build that predates a newly reserved token.
The spawn boundary is the last place a profile arriving by any of them can be stopped.

## Project defaults

A Project selects one launch profile per harness, in two layers, device first:

- `ProjectRecord.default_agent_profiles` - machine-local, chosen in Projects → Options.
- `.swe-mux/config.toml` `default_agent_profiles` - committed, travels with the checkout.

The committed layer is a **selection, never a definition**.
It names a profile the user defined locally; it carries no argv of its own.
Argv for an agent CLI is an authority field (`--dangerously-skip-permissions` lives there), so repository-supplied argv would be a real escalation, while naming a locally-authored profile is the same kind of statement `preferred_backend` already makes.

An unusable default degrades to a `project_launch_profile_unavailable` event and a warning log, and the session starts without the arguments.
It is a *default*: refusing would let one stale id in a shared repository file stop every agent session in the Project from starting.
An explicitly requested `profile_id` is the opposite case and still raises.

`default_shell_profile` is scoped to shell profiles, globally and per Project.
An agent profile named there would make every plain `New terminal` unspawnable.

## Detected shells

- Initial global configuration prefers `pwsh.exe` when PowerShell 7 is available and falls back to `powershell.exe`.
  Each startup upgrades an auto-managed Windows PowerShell default when PowerShell 7 becomes available; user-customized profiles remain authoritative.
- Detected PowerShell, PowerShell 7, CMD, and WSL profiles remain configurable.
  PowerShell launches use `-NoLogo`, not `-NoProfile`, so the selected edition's user profile still loads.
  Cwd integration is process-local and never edits profile files.
- Because the user's `$PROFILE` does load, every interactive PowerShell profile is wrapped in `-NoExit -Command <bootstrap>`, which runs after it and re-asserts `MUX_SHIM_DIR` at the front of PATH.
  Cwd integration adds the OSC 7 `prompt` hook to that same bootstrap; it does not gate it.
  A profile whose own args include `-Command`/`-File` has no prompt to instrument and no room for a second script, so it is left alone, except with cwd integration explicitly on, which still rejects it.
  Rationale: `backends.md`.
- The same bootstrap emits OSC 133 shell-integration markers (`D` command finished with its exit status, then `A` prompt start), reported as the `breakpoint-osc133` capability and controlled globally by `attention_breakpoint_markers`.
  The prompt function runs exactly when the human's own command has finished, which is the breakpoint attention ranking delivers against (`attention-ranking.md`).
  `$?` is read as the wrapper's first statement, because anywhere later it reports the wrapper's own last operation.
  A profile carrying `-Command`/`-File` degrades to no markers rather than failing to spawn.
- WSL translates the canonical Windows Project root through the selected distribution.

## Current compatibility limits

- PowerShell 7 is the primary profile.
  Windows PowerShell 5.1 accepts the mux bootstrap but differs in language and task semantics; in particular, it rejects `&&` and `||`.
- The bootstrap repairs PATH ordering but cannot override PowerShell aliases or functions named for an agent command.
  A profile carrying `-Command`/`-File` also cannot receive the repair.
- The detected CMD profile uses `/Q` without `/D`; registry AutoRun commands can mutate PATH, install DOSKEY macros, or change cwd before the prompt.
- WSL is interactive-shell-only for agent integration and remains labelled `agent-bridge-unavailable`.
- `cwd_strategy = "home"` is accepted by config and exposed in Settings, but only the `wsl` strategy currently has resolver behavior; `home` still starts at the Project root.
- Git Bash/MSYS/Cygwin and other custom executables are generic profiles, not declared agent-aware compatibility targets.

## API and CLI

- `GET /api/profiles` - every configured and detected profile; each carries `backend`.
- `POST /api/sessions {project_id, backend, profile_id, argv, ...}` - `profile_id` is accepted for any backend.
- `PATCH /api/projects/{id} {default_profile_id, default_agent_profiles}`
- `mux profiles`
- `mux spawn --project ID [--backend NAME] [--profile ID] [--arg VALUE]`

## Key files

- `src/swe_mux/config.py` - `LaunchProfile`, validation, the reserved-argument check at save time.
- `src/swe_mux/profiles.py` - `find_profile`, `resolve_profile` (shell), `resolve_agent_profile`.
- `src/swe_mux/harness.py` - `reserved_launch_args`, `reserved_launch_arg_conflict`.
- `src/swe_mux/server.py` - `_spawn_from_body`, `_project_agent_profile`.
- `src/swe_mux/spawn_contract.py`
- `frontend/src/Settings.tsx` - the profile editor.
- `frontend/src/ProjectRunMenu.tsx` - profiles listed under their harness.
- `frontend/src/ProjectsManager.tsx` - per-Project selection in two layers.
- `tests/test_launch_profiles.py`

## Relates to

- `backends.md` - the harness registry that declares reserved argv.
- `projects.md` - the Project record and the committed configuration boundary.
- `sessions.md` - how a resolved profile becomes a spawn.
