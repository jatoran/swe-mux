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

Open <http://127.0.0.1:8765>. `Ctrl+Alt+T` creates a terminal immediately in the
current space and working directory. Type `claude` or `codex` normally; swe-mux
detects the nested agent and begins transcript/state monitoring automatically.
Mux-local `claude.cmd` and `codex.cmd` shims preserve the normal CLI commands while
authentically promoting the existing terminal to an agent session. The sidebar then
shows `[claude]`/`[codex]`, working/tool/ready/approval state, and context usage.

Useful controls:

- `Ctrl+V` / `Ctrl+Shift+V`: terminal-aware bracketed paste.
- `Ctrl+C`: copy when text is selected; otherwise send SIGINT.
- `Ctrl+Shift+P`: command palette.
- Right-click a terminal: copy, paste, select all, find, clear.
- Right-click a session: rename, move, split, broadcast, reveal, or kill.
- New terminals replace the focused pane by default; splits occur only through explicit context actions.
- Sidebar/header `×` uses inline two-click confirmation; context-menu Kill is immediate.
- Right-click a workspace for its terminal and workspace actions.
- Open the sidebar `: menu` for directory launch, history, workspace creation, broadcast, and the command palette.
- `Ctrl+Alt+Left/Right` focuses adjacent panes; `Ctrl+Alt+1..9` switches workspaces.
- Manage Git worktrees from a session context menu: create, list, open, and remove.
- On narrow screens, `:nav` opens the workspace/session drawer and only the focused pane is shown.

The command line client uses `MUX_URL` (default `http://127.0.0.1:8765`):

```powershell
uv run mux ls
uv run mux spawn --backend claude --cwd . --name review
uv run mux send review "check the failing tests`r"
uv run mux history
uv run mux doctor
```

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

The tailnet UI exposes the same terminals, notes, process controls, and development
previews as localhost. Keep Vite and other development servers on `127.0.0.1`; open them
from a session's Processes and previews panel. swe-mux bridges registered HTTP and
WebSocket/HMR traffic through its own URL, so the phone never needs a raw dev-server port.

At daemon startup, native Claude and Codex transcript directories are reconciled into
the read-only History browser (disable with `reconcile_external_history = false`).
The original transcript files are never moved or deleted.

The product contract is [`.docs/development/AGENT_MUX_SPEC.md`](.docs/development/AGENT_MUX_SPEC.md).
