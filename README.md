# swe-mux

A Windows-native, browser terminal multiplexer for long-lived Claude Code, Codex
CLI, and PowerShell sessions. The local daemon owns every ConPTY; closing or
reloading the browser never stops a session.

## Development

```powershell
uv sync --group dev
cd frontend
npm install
npm run build
cd ..
uv run muxd
```

Open <http://127.0.0.1:8765>, create a project, and point it at an existing folder.
`Ctrl+Alt+T` then creates a terminal at that project's canonical root. Type `claude`
or `codex` normally; swe-mux
detects the nested agent and begins transcript/state monitoring automatically.
Mux-local `claude.cmd` and `codex.cmd` shims preserve the normal CLI commands while
authentically promoting the existing terminal to an agent session. The sidebar then
shows `[claude]`/`[codex]`, working/tool/ready/approval state, and context usage.

Useful controls:

- `Ctrl+V` / `Ctrl+Shift+V`: terminal-aware bracketed paste.
- `Ctrl+C`: copy when text is selected; otherwise send SIGINT.
- `Ctrl+Alt+P`: command palette.
- Right-click a terminal: copy, paste, select all, find, clear.
- Right-click a session: rename, split, broadcast, reveal, or kill.
- New terminals replace the focused pane by default; splits occur only through explicit context actions.
- Sidebar/header `×` uses inline two-click confirmation; context-menu Kill is immediate.
- Right-click a project for terminals, its project note, files, settings, and grouping.
- Create optional named Groups to organize projects in the sidebar; Groups do not control panes.
- Each project exposes terminals, previews, its project note, Files, and file editors in one
  unified tab/pane workspace; any tab can share a pane or split into another.
- `Ctrl+Alt+Left/Right` focuses adjacent panes; `Ctrl+Alt+1..9` switches projects.
- Git status remains available, while worktrees are intentionally not a first-class UI surface.
- On narrow screens, `:nav` opens the project/session drawer and only the focused pane is shown.

The command line client uses `MUX_URL` (default `http://127.0.0.1:8765`):

```powershell
uv run mux ls
uv run mux projects
uv run mux spawn --project PROJECT_ID --backend claude --name review
uv run mux send review "check the failing tests`r"
uv run mux history
uv run mux doctor
```

## Provider accounts

The sidebar account strip tracks Claude and Codex subscription windows for every saved
account and switches the system-wide login with one click. Open Settings → Accounts to:

- run the provider's normal browser login and save the resulting account;
- save an account already active in `~/.claude/.credentials.json` or `~/.codex/auth.json`;
- relabel, reauthenticate, switch, refresh, or remove saved accounts.

Only authentication is copied. Existing provider config, skills, projects, transcripts, and
running processes stay in their shared normal directories. Quotas refresh for all accounts
every 15 minutes; transient failures retain the last success for 30 minutes.

Configuration lives in `~/.mux/config.toml`; meta-hooks live in
`~/.mux/hooks.toml`. By default the daemon listens on localhost and the machine's
detected Tailscale IPv4 address. Open the tailnet URL reported by Settings → Remote and
security or `mux doctor`, for example `http://100.x.y.z:8765`. Tailscale policy is the
access boundary; swe-mux has no separate remote login. Use `muxd --local-only` or disable
the tailnet listener in Settings to keep access local.

Tailscale encrypts direct tailnet transport, but browsers still treat an HTTP URL as an
insecure context and may restrict programmatic clipboard APIs. Optional HTTPS:

```powershell
tailscale serve --bg http://127.0.0.1:8765
```

Tailscale Serve is not required. `0.0.0.0`, direct LAN binding, Tailscale Funnel, port
forwarding, and public ingress are unsupported.

The tailnet UI exposes the same terminals, project resources, process controls, and development
previews as localhost. Keep Vite and other development servers on `127.0.0.1`; open them
from a session's Processes and previews panel. swe-mux bridges registered HTTP and
WebSocket/HMR traffic through its own URL, so the phone never needs a raw dev-server port.

At daemon startup, native Claude and Codex transcript directories are reconciled into
the read-only History browser (disable with `reconcile_external_history = false`).
The original transcript files are never moved or deleted.

The maintained design contract starts at [`.docs/design/00_OVERVIEW.md`](.docs/design/00_OVERVIEW.md).
