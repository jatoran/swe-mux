# Shell profiles

## What it is

- Ordered, versioned shell launch definitions shared by default creation, custom creation,
  Settings, API, CLI, live session metadata, and history.
- Profiles apply only to `backend=shell`. Direct Claude/Codex spawn and native history
  resume remain adapter-owned paths.

## Key concepts

- Profile ID: stable configuration/history identity.
- Effective command: resolved executable + profile argv + request argv.
- Cwd strategy: `native`, `home`, or `wsl` translation.
- Capability: explicit runtime promise such as `interactive`, `agent-aware`, `wsl`, or
  `agent-bridge-unavailable`.

## Data model

```toml
default_shell_profile = "powershell"

[[shell_profiles]]
id = "powershell"
label = "Windows PowerShell"
executable = "powershell.exe"
args = ["-NoLogo"]
env = {}
platforms = ["windows"]
cwd_strategy = "native"
marker = "ps"
capabilities = ["interactive", "agent-aware"]
enabled = true
```

- `SessionRecord.shell_profile_id` and `history.shell_profile_id` preserve identity.
- Effective executable/argv remain stored separately for diagnostics and reproducibility.
- `SpaceRecord.default_profile_id` optionally overrides the global default.

## Operations

- Resolution precedence: request raw executable > request profile > space profile >
  global profile. Raw executable and profile ID are mutually exclusive.
- `New terminal` resolves defaults without a dialog or split. `New terminal custom…`
  chooses a profile/cwd. Split creation is an explicit separate action.
- Detected Windows PowerShell, PowerShell 7, CMD, and installed WSL distros are offered
  without changing the selected default. Settings can persist/modify detected presets.
- No adapter injects `-NoLogo`; PowerShell profiles own the flag.
- WSL translates Windows cwd through distro `wslpath`, with drive-mount fallback, then
  starts through `wsl.exe --distribution ... --cd ...`.
- WSL agent-awareness is capability-gated. Interactive profiles remain usable, but are
  labelled `agent-bridge-unavailable` until native distro Claude/Codex plus bridge
  promotion/hook/transcript contracts pass. Windows interop commands do not qualify.

## API surface

- `GET /api/profiles`: configured + detected profiles and capabilities.
- `POST /api/sessions {profile_id,...}`: profile launch.
- `GET|POST|DELETE /api/directories/pins`: persistent favorite directories.
- `GET /api/fs/roots`, `GET /api/fs/list?path=`: daemon filesystem browsing.
- CLI: `mux profiles`; `mux spawn --profile ID [--arg VALUE]`.

## Key files

- Schema/migration: `src/swe_mux/config.py`
- Detection/resolution: `src/swe_mux/profiles.py`
- Spawn contract/API: `src/swe_mux/spawn_contract.py`, `src/swe_mux/server.py`
- Runtime/history: `src/swe_mux/session.py`, `src/swe_mux/history.py`
- UI: `frontend/src/App.tsx`, `frontend/src/Settings.tsx`

