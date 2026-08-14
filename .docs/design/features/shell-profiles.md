# Shell profiles

## What it is

- Profiles are ordered executable/argv/environment definitions for `backend=shell`.
- Resolution precedence is raw executable, request profile, Project profile, project-local
  `.swe-mux/config.toml` profile, then global profile.
- `SessionRecord.shell_profile_id` and history preserve the selected identity; effective
  executable and argv remain separate diagnostics.
- `New terminal` uses defaults immediately. `New terminal custom…` chooses only a profile;
  its Project root is fixed and read-only.
- Initial global configuration prefers `pwsh.exe` when PowerShell 7 is available and falls
  back to `powershell.exe`. Each startup upgrades an auto-managed Windows PowerShell default
  when PowerShell 7 becomes available; user-customized profiles remain authoritative.
- Detected PowerShell, PowerShell 7, CMD, and WSL profiles remain configurable. PowerShell
  launches use `-NoLogo`, not `-NoProfile`, so the selected edition's user profile still
  loads. Cwd integration is process-local and never edits profile files.
- Because the user's `$PROFILE` does load, every interactive PowerShell profile is wrapped
  in `-NoExit -Command <bootstrap>`, which runs after it and re-asserts `MUX_SHIM_DIR` at
  the front of PATH. Cwd integration adds the OSC 7 `prompt` hook to that same bootstrap;
  it does not gate it. A profile whose own args include `-Command`/`-File` has no prompt to
  instrument and no room for a second script, so it is left alone — except with cwd
  integration explicitly on, which still rejects it. Rationale: `backends.md`.
- The same bootstrap emits OSC 133 shell-integration markers (`D` command finished with its
  exit status, then `A` prompt start), reported as the `breakpoint-osc133` capability and
  controlled globally by `attention_breakpoint_markers`. The prompt function runs exactly when
  the human's own command has finished, which is the breakpoint attention ranking delivers
  against (`attention-ranking.md`). `$?` is read as the wrapper's first statement, because
  anywhere later it reports the wrapper's own last operation. A profile carrying
  `-Command`/`-File` degrades to no markers rather than failing to spawn.
- WSL translates the canonical Windows Project root through the selected distribution.

## Current compatibility limits

- PowerShell 7 is the primary profile. Windows PowerShell 5.1 accepts the mux bootstrap but
  differs in language and task semantics; in particular, it rejects `&&` and `||`.
- The bootstrap repairs PATH ordering but cannot override PowerShell aliases or functions named
  for an agent command. A profile carrying `-Command`/`-File` also cannot receive the repair.
- The detected CMD profile uses `/Q` without `/D`; registry AutoRun commands can mutate PATH,
  install DOSKEY macros, or change cwd before the prompt.
- WSL is interactive-shell-only for agent integration and remains labelled
  `agent-bridge-unavailable`.
- `cwd_strategy = "home"` is accepted by config and exposed in Settings, but only the `wsl`
  strategy currently has resolver behavior; `home` still starts at the Project root.
- Git Bash/MSYS/Cygwin and other custom executables are generic profiles, not declared
  agent-aware compatibility targets.

## API and CLI

- `GET /api/profiles`
- `POST /api/sessions {project_id, profile_id, ...}`
- `mux profiles`
- `mux spawn --project ID --profile ID [--arg VALUE]`

## Key files

- `src/swe_mux/config.py`
- `src/swe_mux/profiles.py`
- `src/swe_mux/spawn_contract.py`
- `frontend/src/Settings.tsx`
