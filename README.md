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

On-device speech (Kokoro text-to-speech and faster-whisper dictation) is optional and adds
`uv sync --extra voice-local`. Without it swe-mux speaks through the OS voice engine and
dictates through the browser, and Settings reports the local engines as unavailable rather
than failing. The desktop bundle is always built with it.

Experimental Edge TTS is a separate online provider and is never bundled.
For a source install, `uv sync --extra voice-edge` installs the tested `edge-tts` client and
Settings can use the current Python interpreter.
The frozen app requires a path to a separate user-managed Python containing
`edge-tts==7.2.8`.
Selecting Edge requires an explicit disclosure acknowledgement because each spoken segment is
sent to an undocumented Microsoft consumer endpoint with no SLA or published third-party
commercial-use grant.
Voice-list refresh and integration probes are explicit; opening Settings performs neither.

Windows desktop mode adds a WebView2 window and system-tray supervisor while keeping `muxd`
as the independent terminal owner:

```powershell
uv sync --extra desktop
uv run --extra desktop swe-mux
```

Closing or minimizing the desktop window hides it to the tray. The tray menu can restore the
window, open the ordinary browser UI, enable login startup, or explicitly quit swe-mux. Quit
confirms when terminals are live and then stops the daemon; closing the window does not.

Build the distributable `dist/swe-mux/swe-mux.exe` folder with:

```powershell
uv sync --extra desktop --extra voice-local --group package
uv run --extra desktop --extra voice-local --group package python packaging/build_desktop.py
```

The build is intentionally `onedir`: distribute the complete `dist/swe-mux/` folder, not only
the executable. WebView2 Runtime is required on the target Windows machine.

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
- A new project opens with a narrow Files column beside its project note; the first terminal
  joins the note's pane. Nothing is spawned until you ask for it.
- New terminals open as a tab in the focused pane; splits occur only through explicit context actions.
- Sidebar/header `×` uses inline two-click confirmation; context-menu Kill is immediate.
- Right-click a project for terminals, its project note, files, settings, and grouping.
- Use **Run** in the active-project header, Project row, or mobile toolbar to start Claude,
  Codex, a shell/custom terminal, a new worktree session, or an imported Project task.
  VS Code tasks, root package
  scripts, and `.swe-mux/actions.toml` are inert until their exact current files are reviewed
  and trusted; any edit requires approval again.
- Create optional named Groups to organize projects in the sidebar; Groups do not control panes.
- Each project exposes terminals, previews, its project note, Files, and file editors in one
  unified tab/pane workspace; any tab can share a pane or split into another.
- `Ctrl+Alt+Left/Right` focuses adjacent panes; `Ctrl+Alt+1..9` switches projects.
- Agent pane `talk:` enables hands-free Conversation mode. Speak across natural pauses, then use
  the `Mux` wake word: `send`, `cancel`, `undo`, `mute`, `read reply`, `summary`, `verbatim`,
  `interrupt`, `help`, or `stop listening`. Completed replies play as short streamed clips;
  speaking over playback stops the remaining reply.
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

This is a convenience for **one person switching between accounts they personally own
and pay for** - a personal subscription and a work one, say - replacing the manual
logout/login cycle the provider CLIs otherwise require. It is not account pooling and
not a way around a usage limit: accounts are never shared between people, saved
credentials are stored locally and sent nowhere but the provider's own endpoints,
sessions are never load-balanced across accounts to extend a quota, and switching is
always an explicit user action rather than something the daemon does when a limit is
reached. Each account remains subject to your own agreement with that provider.

Configuration lives in `~/.mux/config.toml`; meta-hooks live in
`~/.mux/hooks.toml`. By default the daemon listens on localhost and the machine's
detected Tailscale IPv4 address. Open the tailnet URL reported by Settings → Remote and
security or `mux doctor`, for example `http://100.x.y.z:8765`. Tailscale policy is the
access boundary; swe-mux has no separate remote login. Use `muxd --local-only` or disable
the tailnet listener in Settings to keep access local.

New worktrees launched from a Project Run menu default below `~/.mux/worktrees`, grouped by Project and branch.
Change the root in Settings → Git & processes → Git and worktrees; existing worktrees are not moved.

Tailscale encrypts direct tailnet transport, but browsers still treat an HTTP URL as an
insecure context and may restrict programmatic clipboard APIs. Optional HTTPS:

```powershell
tailscale serve --bg http://127.0.0.1:8765
```

Tailscale Serve is not required. `0.0.0.0`, direct LAN binding, Tailscale Funnel, port
forwarding, and public ingress are unsupported.

The tailnet UI exposes the same terminals, project resources, process controls, and development
previews as localhost. Keep Vite and other development servers on `127.0.0.1`; open them
from a session's Processes and previews panel or click their loopback URL in terminal output.
swe-mux bridges registered HTTP and
WebSocket/HMR traffic through its own URL, so the phone never needs a raw dev-server port.

At daemon startup, native Claude and Codex transcript directories are reconciled into
the read-only History browser (disable with `reconcile_external_history = false`).
The original transcript files are never moved or deleted.

The maintained design contract starts at [`.docs/design/00_OVERVIEW.md`](.docs/design/00_OVERVIEW.md).

## License

swe-mux is licensed under the [Apache License 2.0](LICENSE). See [`NOTICE`](NOTICE)
for attribution and [`TRADEMARK.md`](TRADEMARK.md) for what the license's trademark
reservation does and does not allow.

Third-party software redistributed with swe-mux is listed in
[`THIRD-PARTY-NOTICES.md`](THIRD-PARTY-NOTICES.md), generated from the lockfiles by
`packaging/license_audit.py` so it cannot drift. swe-mux ships no GPL or AGPL code.
It ships two LGPL libraries, `pystray` and `num2words`, each as replaceable source
inside the bundle; the notices file says how to substitute your own build.

Contributions are welcome under a [DCO sign-off](CONTRIBUTING.md) - `git commit -s`,
no CLA.

### Not affiliated with the agent vendors

swe-mux launches and observes coding-agent CLIs published by other vendors,
including Anthropic's Claude Code and OpenAI's Codex CLI. It is **not affiliated
with, endorsed by, or sponsored by** Anthropic, OpenAI, or any other such vendor,
and it uses their names only to identify which tool a feature works with.

You run those CLIs under your own account and your own agreement with each vendor.
The same is true of the optional OpenRouter and Hugging Face integrations: they use
your own API key and consume your own quota, under your agreement with those
services. swe-mux proxies nothing and resells nothing.

The optional Edge TTS integration is different: the upstream client uses Microsoft Edge's
consumer Read Aloud endpoint without an API key or documented third-party service contract.
The LGPL client runs only in a user-managed external Python and is absent from the frozen
bundle, but that software boundary does not resolve Microsoft's service terms.
