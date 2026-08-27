# swe-mux

**swe-mux - mission control for your coding-agent fleet.**

swe-mux is a local, browser-based terminal multiplexer and control plane for the coding-agent CLIs you already run.
It owns the pseudoterminals, so Claude Code, Codex, opencode, and any other CLI or shell run in a real terminal exactly as they do outside it, while swe-mux adds the layer around them: sessions that outlive the app, one status vocabulary across vendors, a queue for work you stage while an agent is mid-turn, parallel git worktrees with a verification-gated landing path, a record of which agent wrote which commit, and the whole workspace reachable from a phone.
It runs entirely on your machine and has no server, no account, and no telemetry.

<!-- TODO(release): hero demo - drop the hero video/GIF here. Asset does not exist yet; the operator
     records it. Per site/README.md section 2, this is the desktop workspace at full width (sidebar,
     a split pane region with a live agent, the utility drawer open) with a phone showing the same
     workspace overlapping the lower right, scrubbed of real project names and account labels. -->

<!-- TODO(release): OWNER - the GitHub org/account is not decided. Replace OWNER in every
     github.com/OWNER/swe-mux URL below, including the two badge URLs, in one pass. -->

[![ci](https://github.com/OWNER/swe-mux/actions/workflows/ci.yml/badge.svg)](https://github.com/OWNER/swe-mux/actions/workflows/ci.yml)
[![license](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)

## What it does

**Sessions survive daemon restarts and app rebuilds.**
Every pseudoterminal is held by a supervisor process that is separate from the daemon and separate from the UI.
Restart the daemon, or rebuild and redeploy the whole desktop application, and the agents keep working: the next daemon rediscovers the supervisor and reattaches every live session from mirrored metadata plus a scrollback snapshot.
Reconnecting a terminal replays only the bytes you missed rather than resetting the screen, so a phone that slept through a long turn comes back to an intact buffer.

**Per-agent status you can check afterwards.**
Every session carries one state across vendors: working, ready, awaiting approval, or blocked.
It is derived from provider hooks first, then the transcript, then the PTY, then the CLI's own reported state, and every transition is written to a durable ledger with the layer readings that produced it, so a status that looked wrong is still explainable hours later.
A watchdog catches sessions that stopped reporting.

**A prompt queue that waits for a real gate.**
Stage ordered messages against a conversation while it is mid-turn.
The queue is durable across restarts and strictly head-of-line, and it is bound to the conversation's first run, so a cleared conversation strands its queue visibly instead of firing into a stranger.
Automatic delivery is off by default (`auto_delivery_enabled`), and when you turn it on it waits on a readiness gate and a stability window rather than on a binary "done" signal, with a per-conversation override.

**Parallel worktrees, landed behind a verification gate.**
Create a git worktree, run your setup command, and start a session in its exact root as one operation.
When a branch is finished, the land queue runs a fixed sequence for one branch at a time: reconcile with the trunk, run the verification command whose exact bytes you approved, then fast-forward only.
Fast-forward-only is what makes the trunk step safe to automate, because Git refuses it on divergence and refuses to overwrite local changes.
A conflict or a failed gate comes back to the branch's own agent as a message; the queue never resolves either one.
It is off by default, per project.

**Commit-level provenance.**
Commits carry which session and which conversation produced them, split into committer and contributor, with a confidence level and the files each contributor's writes account for.
That rests on deterministic capture rather than on the agent's own account of its work: file writes are hashed on the exact bytes written at the adapter boundary, commands keep their exit class, and test output is parsed down to the failing set.

**The whole workspace on a phone.**
The browser UI is an installable PWA, reached over your own Tailscale tailnet with no relay and no swe-mux login.
Live terminals, git review, the editor, the file tree, the queue, previews, and the Run menu are all there; nothing functional is desktop-only.
Voice is local: browser capture, local voice-activity detection, and a local faster-whisper decode, with a configurable wake word.
Optional web push reaches a lock screen when a session needs a person.

**Multi-harness, and any shell.**
Anything that runs in a terminal runs here unchanged, including a CLI swe-mux has never heard of.
For the harnesses it does know (Claude Code, Codex, opencode, and others in the registry) it normalizes input handling, status, transcripts, history search, account switching, and lifecycle events into one vocabulary.
Native transcripts are never moved, rewritten, or deleted; the searchable copy is a local derivative you can throw away and rebuild.

**Local-only.**
There is no swe-mux server, no swe-mux account, and no telemetry.
Your agent CLIs keep talking to their own vendors under your own subscription, and swe-mux proxies nothing and resells nothing.
Three optional features do reach the network, each off until you turn it on: control-plane summarization and voice summaries call an OpenRouter-compatible endpoint with your own key, web push is delivered through your browser vendor's push service with an encrypted payload, and experimental Edge TTS is described in "Not affiliated with the agent vendors" below.

## Install

<!-- TODO(release): pypi - swe-mux is not published to a package index yet, and there is no
     publish workflow in .github/workflows. Until it is, the source install below is the only
     one that works. -->

Once the package is published:

```
uv tool install swe-mux
uv tool install "swe-mux[desktop]"
```

That installs three entry points: `mux` (the CLI), `muxd` (the daemon), and, with the `desktop` extra on Windows, `swe-mux` (the desktop window and tray).

### From source

This is the flow that works today.

```
git clone https://github.com/OWNER/swe-mux
cd swe-mux
uv sync --extra desktop
npm --prefix frontend ci
npm --prefix frontend run build
uv run --extra desktop swe-mux
```

The frontend build output is gitignored, so a fresh clone serves no UI until you run the build once.
For a headless daemon and an ordinary browser, which is the Linux shape, run `uv run muxd` and open <http://127.0.0.1:8765>.

### The desktop app

Windows desktop mode adds a WebView2 window and a system-tray supervisor while `muxd` stays the independent terminal owner.
Closing or minimizing the window hides it to the tray; the tray menu restores the window, opens the browser UI, enables login startup, or quits, and quitting confirms when terminals are live.
WebView2 Runtime is required on the target machine.

Build the distributable folder with:

```
uv sync --extra desktop --extra voice-local --group package
uv run --extra desktop --extra voice-local --group package python packaging/build_desktop.py
```

The build is intentionally `onedir`: distribute the complete `dist/swe-mux/` folder, not only `dist/swe-mux/swe-mux.exe`.

<!-- TODO(release): desktop download - there is no published, signed release artifact yet. Link the
     download here and say which Windows builds and architectures it is signed for. -->

### Check the install

```
uv run mux doctor
```

`mux doctor` is a read-only consolidated report: daemon and PTY supervisor, whether the frontend has been built, which agent CLIs it can detect, the tailnet listener and Tailscale, the Windows firewall rule, and fleet and background-loop health.
`mux doctor --export` prints the full diagnostics bundle (config, remote, firewall, logs) as JSON.
It talks to a running daemon and exits 3 if it cannot reach one.

## Platform support

**Windows 10 or 11 is the proving platform.**
The full gate runs there in CI (`.github/workflows/ci.yml`, the `verify` job), including the real ConPTY integration tests and the Playwright renderer suite, and it is the only platform the desktop app ships on: `pystray` and `pywebview` are declared `sys_platform == "win32"`.
PowerShell 7 is the primary shell contract; Windows PowerShell 5.1, CMD, and a WSL distro shell are separately supported profiles with different limits, and other shells run as generic executable profiles with no agent-aware contract.

**Linux runs from source, headless plus a browser.**
CI runs the whole suite on `ubuntu-latest` as a required leg, syncing with no extras, so a stray Windows import fails there rather than in your terminal.
There is no Linux desktop app, by design rather than by omission.

**macOS is implemented and typechecked but unproven.**
The POSIX paths are written and typechecked for it, and the macOS CI leg exists so the gap is measured instead of assumed, but nothing has ever executed there: that leg is `continue-on-error`, and the flag comes off the first time it passes.
Treat macOS as unsupported until it does.

`.docs/development/CROSS_PLATFORM_FINDINGS.md` records what each of those claims rests on and what a fuller port still needs.

## Requirements

- Python 3.12 or newer, and [uv](https://docs.astral.sh/uv/).
- Node 20 or newer, to build the frontend from source.
- Windows only: WebView2 Runtime, for desktop mode.
- At least one agent CLI, already installed and logged in. swe-mux does not install, manage, or proxy the agent CLIs; it runs the ones you already have, on the subscription you already pay for.
- Tailscale, optionally, to reach the daemon from a phone.

On-device speech (Kokoro text-to-speech and faster-whisper dictation) is optional and adds `uv sync --extra voice-local`, roughly 400 MB of wheels and model machinery.
Without it swe-mux speaks through the OS voice engine and dictates through the browser, and Settings reports the local engines as unavailable rather than failing.
The desktop bundle is always built with it.

## First run

Open the UI, create a Project, and point it at an existing folder.
`Ctrl+Alt+T` opens a terminal at that Project's canonical root, and `Ctrl+Alt+P` opens the command palette.
Nothing is spawned until you ask for it.

Type `claude`, `codex`, or another supported CLI normally.
swe-mux puts its own launchers first on that terminal's PATH, so the usual command promotes the terminal you are standing in to an agent session in place: same pane, same scrollback, now carrying a transcript, a status, a queue, and a context meter.

**Run** in the active-project header, the Project row, or the mobile toolbar starts an agent, a shell, a new worktree session, or an imported Project task.
VS Code tasks, root `package.json` scripts, and `.swe-mux/actions.toml` are inert until their exact current bytes are reviewed and trusted, and any edit requires approval again.

At daemon startup, native Claude and Codex transcript directories are reconciled into the read-only History browser (disable with `reconcile_external_history = false`).
The original transcript files are never moved or deleted.

## Configuration and data

Configuration lives at `config.toml` inside the data directory, and meta-hooks at `hooks.toml` beside it.
The data directory is `~/.mux` on Windows, `$XDG_DATA_HOME/swe-mux` (else `~/.local/share/swe-mux`) on Linux, and `~/Library/Application Support/swe-mux` on macOS; an existing `~/.mux` always wins on every host, and `MUX_DATA_DIR` overrides all of it.

New worktrees launched from a Project Run menu default below `<data_dir>/worktrees`, grouped by Project and branch.
Change the root in Settings, Git and processes, Git and worktrees; existing worktrees are not moved.

The `mux` CLI resolves its daemon from `--url`, then `MUX_URL`, then the configured host and port, then `http://127.0.0.1:8765`.

## Remote access

By default the daemon listens on localhost and on the machine's detected Tailscale IPv4 address.
Open the tailnet URL reported by Settings, Remote and security, or by `mux doctor`.
Tailscale policy is the access boundary; swe-mux has no separate remote login, so a tailnet peer admitted by your policy has terminal and code-execution authority.
Use `muxd --local-only`, or disable the tailnet listener in Settings, to keep access local.

Tailscale encrypts direct tailnet transport, but browsers still treat an HTTP URL as an insecure context and restrict the clipboard and microphone APIs.
For those, put Tailscale Serve in front of the daemon:

```
tailscale serve --bg http://127.0.0.1:8765
```

Serve is not required for anything else.
`0.0.0.0`, direct LAN binding, Tailscale Funnel, port forwarding, and public ingress are unsupported.

The tailnet UI exposes the same terminals, project resources, process controls, and development previews as localhost.
Keep Vite and other development servers on `127.0.0.1` and open them from a session's Processes and previews panel: swe-mux bridges registered HTTP and WebSocket/HMR traffic through its own URL, so the phone never needs a raw dev-server port.

## Provider accounts

The sidebar account strip tracks Claude and Codex subscription windows for every saved account and switches the system-wide login with one click.
Open Settings, Accounts to:

- run the provider's normal browser login and save the resulting account;
- save an account already active in `~/.claude/.credentials.json` or `~/.codex/auth.json`;
- relabel, reauthenticate, switch, refresh, or remove saved accounts.

Only authentication is copied.
Existing provider config, skills, projects, transcripts, and running processes stay in their shared normal directories.
Quotas refresh for all accounts every 15 minutes; transient failures retain the last success for 30 minutes.

This is a convenience for **one person switching between accounts they personally own and pay for** - a personal subscription and a work one, say - replacing the manual logout/login cycle the provider CLIs otherwise require.
It is not account pooling and not a way around a usage limit: accounts are never shared between people, saved credentials are stored locally and sent nowhere but the provider's own endpoints, sessions are never load-balanced across accounts to extend a quota, and switching is always an explicit user action rather than something the daemon does when a limit is reached.
Each account remains subject to your own agreement with that provider.

## Documentation

<!-- TODO(release): site URL - the landing page in site/ is not deployed yet, and its own docs and
     blog links are still placeholders. Link the published site and docs here once both exist. -->

The maintained design contract starts at [`.docs/design/00_OVERVIEW.md`](.docs/design/00_OVERVIEW.md).
[`.docs/CLAUDE.md`](.docs/CLAUDE.md) is the routing table that says which document owns which subsystem.
The landing page and the argument it makes live in [`site/`](site/).

## Development

Contributions are welcome.
[`CONTRIBUTING.md`](CONTRIBUTING.md) covers the two things a change has to satisfy: the DCO sign-off, and the verification gate you run before opening a pull request.
[`CLAUDE.md`](CLAUDE.md) covers the working rules for this repository, including how to apply a change to a running install without killing live sessions, and how parallel worktrees are verified and landed.
Dependency changes have their own rules, in `CONTRIBUTING.md` under "Dependencies and licensing", because a new dependency is a new thing redistributed to every user of the frozen bundle.

## License

swe-mux is licensed under the [Apache License 2.0](LICENSE).
See [`NOTICE`](NOTICE) for attribution and [`TRADEMARK.md`](TRADEMARK.md) for what the license's trademark reservation does and does not allow.

Third-party software redistributed with swe-mux is listed in [`THIRD-PARTY-NOTICES.md`](THIRD-PARTY-NOTICES.md), generated from the lockfiles by `packaging/license_audit.py` so it cannot drift.
swe-mux ships no GPL or AGPL code.
It ships two LGPL libraries, `pystray` and `num2words`, each as replaceable source inside the bundle; the notices file says how to substitute your own build.

Contributions arrive under a [DCO sign-off](CONTRIBUTING.md) - `git commit -s` - and not a CLA.
A CLA would let the project relicense your contribution later; a DCO does not, and that is the intended trade.

### Not affiliated with the agent vendors

swe-mux launches and observes coding-agent CLIs published by other vendors, including Anthropic's Claude Code and OpenAI's Codex CLI.
It is **not affiliated with, endorsed by, or sponsored by** Anthropic, OpenAI, or any other such vendor, and it uses their names only to identify which tool a feature works with.

You run those CLIs under your own account and your own agreement with each vendor.
The same is true of the optional OpenRouter and Hugging Face integrations: they use your own API key and consume your own quota, under your agreement with those services.
swe-mux proxies nothing and resells nothing.

The optional Edge TTS integration is different: the upstream client uses Microsoft Edge's consumer Read Aloud endpoint without an API key or documented third-party service contract.
Selecting it requires an explicit disclosure acknowledgement, because each spoken segment is sent to an undocumented Microsoft consumer endpoint with no SLA and no published third-party commercial-use grant.
The LGPL client runs only in a managed isolated environment or an operator-supplied external Python and is absent from the frozen bundle, but that software boundary does not resolve Microsoft's service terms.
