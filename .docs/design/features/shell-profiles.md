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
- WSL translates the canonical Windows Project root through the selected distribution.

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
