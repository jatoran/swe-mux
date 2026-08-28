# swe-mux

**swe-mux - mission control for your coding-agent fleet.**

swe-mux is a local, browser-based terminal multiplexer and control plane for the coding-agent CLIs you already run.
It owns the pseudoterminals, so Claude Code, Codex, opencode, and any other CLI or shell run in a real terminal exactly as they do outside it, while swe-mux adds the layer around them.
There is no server, no account, and no telemetry.

<!-- TODO(release): hero demo - video/GIF goes here. Asset does not exist yet; the operator records
     it, to the shot list in site/README.md section 2. -->

[![ci](https://github.com/jatoran/swe-mux/actions/workflows/ci.yml/badge.svg)](https://github.com/jatoran/swe-mux/actions/workflows/ci.yml)
[![license](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)

## What it adds

- **Sessions that outlive the app.** A supervisor process separate from the daemon and the UI holds every pseudoterminal, so a daemon restart or a full app rebuild leaves the agents working, and reconnecting replays only the bytes you missed. ([sessions](.docs/design/features/sessions.md), [recovery](.docs/design/features/session-recovery.md))
- **One status vocabulary across vendors.** Working, ready, awaiting approval, or blocked - read from provider hooks, the transcript, the PTY, and the CLI's own state, with every transition kept in a durable ledger. ([status detection](.docs/design/features/status-detection.md))
- **A prompt queue that waits for a real gate.** Stage ordered messages against a mid-turn conversation. The queue is durable, head-of-line, bound to the conversation, and automatic delivery is off by default. ([prompt queue](.docs/design/features/prompt-queue.md), [auto-delivery](.docs/design/features/auto-delivery.md))
- **Parallel worktrees, landed behind a verification gate.** One branch at a time: reconcile with the trunk, run the verification command whose exact bytes you approved, then fast-forward only. A conflict or a failed gate goes back to the branch's own agent. ([land queue](.docs/design/features/land-queue.md))
- **Commit-level provenance.** Which session and conversation produced a commit, split into committer and contributor, from deterministic capture rather than the agent's account of its work. ([Tier 0 facts](.docs/design/features/tier0-facts.md))
- **The whole workspace on a phone.** An installable PWA over your own Tailscale tailnet, with no relay and no swe-mux login. Terminals, git review, the editor, previews, local voice, and optional web push. ([remote access](.docs/design/features/remote-access.md), [voice](.docs/design/features/voice.md))
- **Any CLI, and any shell.** Anything that runs in a terminal runs here unchanged, including one swe-mux has never heard of; the harnesses in its registry get normalized input, status, transcripts, history, and accounts. Native transcripts are never moved or rewritten. ([backends](.docs/design/features/backends.md))

Your agent CLIs keep talking to their own vendors under your own subscription, and swe-mux proxies nothing and resells nothing.
Three optional features reach the network and each is off until you turn it on: summarization through an OpenRouter-compatible endpoint with your key, web push through your browser vendor, and experimental Edge TTS.

The one request swe-mux makes on its own behalf is a daily fetch of `https://swemux.dev/version.json` to check for a newer release - nothing downloads, and the file is identical for every install, with no query string, header, cookie, or identifier on it.
Settings → Diagnostics → Software updates (`update_check_enabled`) turns it off entirely.

## Install

<!-- TODO(release): pypi - not published to a package index yet, and there is no publish workflow in
     .github/workflows, so the source install below is the only one that works. Once published:
     `uv tool install swe-mux`, or `uv tool install "swe-mux[desktop]"`.
     TODO(release): desktop download - there is no signed release artifact yet. Link it here and say
     which Windows builds and architectures it is signed for. -->

```
git clone https://github.com/jatoran/swe-mux
cd swe-mux
uv sync --extra desktop
npm --prefix frontend ci
npm --prefix frontend run build
uv run --extra desktop swe-mux
```

The frontend build output is gitignored, so a fresh clone serves no UI until that build runs once.
For a headless daemon and an ordinary browser, which is the Linux shape, run `uv run muxd` and open <http://127.0.0.1:8765>.

Three entry points are installed: `mux` (the CLI), `muxd` (the daemon), and `swe-mux` (the Windows desktop window and tray, which needs the `desktop` extra and the WebView2 Runtime).
`uv run mux doctor` is a read-only health report covering the daemon, the supervisor, the frontend build, detected agent CLIs, the tailnet listener, and background loops.

Build the Windows distributable with `uv sync --extra desktop --extra voice-local --group package`, then `uv run --extra desktop --extra voice-local --group package python packaging/build_desktop.py`.
It is deliberately an `onedir` build: distribute the whole `dist/swe-mux/` folder, not only the `.exe`.
Packaging rules: [`.docs/design/features/desktop-shell.md`](.docs/design/features/desktop-shell.md).

### Requirements

- Python 3.12 or newer, and [uv](https://docs.astral.sh/uv/).
- Node 22.6 or newer, to build the frontend from source.
- At least one agent CLI, already installed and logged in. swe-mux does not install, manage, or proxy them.
- Optionally Tailscale, to reach the daemon from a phone.

On-device speech (Kokoro text-to-speech and faster-whisper dictation) is `--extra voice-local`, roughly 400 MB of wheels and model machinery, and the desktop bundle always carries it.
Without it swe-mux speaks through the OS voice engine and dictates through the browser.

## First run

Create a Project and point it at an existing folder.
`Ctrl+Alt+T` opens a terminal at that Project's root and `Ctrl+Alt+P` opens the command palette; nothing is spawned until you ask for it.

Then type `claude`, `codex`, or another supported CLI normally.
swe-mux puts its own launchers first on that terminal's PATH, so the usual command promotes the terminal you are standing in to an agent session in place: same pane, same scrollback, now carrying a transcript, a status, a queue, and a context meter.

The **Run** menu starts an agent, a shell, a worktree session, or an imported task. Imported tasks - VS Code tasks, root `package.json` scripts, `.swe-mux/actions.toml` - stay inert until their exact current bytes are trusted, and any edit requires approval again. ([project actions](.docs/design/features/project-actions.md))

## Platform support

- **Windows 10 or 11 is the proving platform.** The full gate runs there in CI (the `verify` job), including the real ConPTY integration tests and the Playwright renderer suite, and it is the only platform the desktop app ships on. PowerShell 7 is the primary shell contract; 5.1, CMD, and a WSL distro shell are separately supported profiles.
- **Linux runs from source**, headless plus a browser, on a required CI leg that syncs with no extras. There is no Linux desktop app, by design.
- **macOS is implemented and typechecked, and newly exercised.** Its CI leg runs the whole suite on `macos-latest`, but that leg is still `continue-on-error` and has not passed yet, so treat macOS as unproven.

What each claim rests on: [`.docs/development/CROSS_PLATFORM_FINDINGS.md`](.docs/development/CROSS_PLATFORM_FINDINGS.md).

## Remote access

The daemon listens on localhost and on the machine's detected Tailscale IPv4 address; `muxd --local-only` keeps it local.
Tailscale policy is the access boundary, and swe-mux has no separate remote login, so a tailnet peer your policy admits has terminal and code-execution authority.

Browsers restrict the clipboard and microphone over plain HTTP, so for those put Tailscale Serve in front of the daemon: `tailscale serve --bg http://127.0.0.1:8765`.
`0.0.0.0`, direct LAN binding, Tailscale Funnel, port forwarding, and public ingress are unsupported.
Detail, including how development previews are bridged so a phone never needs a raw dev-server port: [`.docs/design/features/remote-access.md`](.docs/design/features/remote-access.md).

## Configuration and data

Configuration is `config.toml` inside the data directory, which is `~/.mux` on Windows, `$XDG_DATA_HOME/swe-mux` (else `~/.local/share/swe-mux`) on Linux, and `~/Library/Application Support/swe-mux` on macOS.
An existing `~/.mux` always wins on every host, and `MUX_DATA_DIR` overrides all of it.
The `mux` CLI resolves its daemon from `--url`, then `MUX_URL`, then the configured host and port, then `http://127.0.0.1:8765`.

## Provider accounts

Settings → Accounts saves Claude and Codex logins, tracks each account's subscription window, and switches the system-wide login in one click. Only authentication is copied.

This is a convenience for **one person switching between accounts they personally own and pay for**, replacing the logout/login cycle the provider CLIs otherwise require.
It is not account pooling and not a way around a usage limit: accounts are never shared between people, credentials stay local and go nowhere but the provider's own endpoints, sessions are never load-balanced across accounts, and switching is always an explicit user action.
Scope and terms: [`.docs/design/features/provider-accounts.md`](.docs/design/features/provider-accounts.md).

## Documentation

<!-- TODO(release): site URL - the landing page in site/ is not deployed yet (swemux.dev currently
     404s) and its own docs and blog links are placeholders. Link the published site and docs here
     once both exist. -->

The maintained design contract starts at [`.docs/design/00_OVERVIEW.md`](.docs/design/00_OVERVIEW.md), and [`.docs/CLAUDE.md`](.docs/CLAUDE.md) routes each subsystem to the document that owns it.
The landing page and the argument it makes live in [`site/`](site/); the project homepage is <https://swemux.dev>.

## Contributing

Contributions are welcome.
[`CONTRIBUTING.md`](CONTRIBUTING.md) covers what a change has to satisfy - the DCO sign-off, the verification gate you run before opening a pull request, and the extra rules a dependency change carries.
[`CLAUDE.md`](CLAUDE.md) covers the working rules for this repository, and [`SECURITY.md`](SECURITY.md) is where a vulnerability report goes.

## License

swe-mux is licensed under the [Apache License 2.0](LICENSE).
See [`NOTICE`](NOTICE) for attribution and [`TRADEMARK.md`](TRADEMARK.md) for what the license's trademark reservation does and does not allow.

Contributions arrive under a [DCO sign-off](CONTRIBUTING.md) - `git commit -s` - and not a CLA.
A CLA would let the project relicense your contribution later; a DCO does not, and that is the intended trade.

Third-party software redistributed with swe-mux is listed in [`THIRD-PARTY-NOTICES.md`](THIRD-PARTY-NOTICES.md), generated from the lockfiles by `packaging/license_audit.py` so it cannot drift.
swe-mux ships no GPL or AGPL code; it ships two LGPL libraries, `pystray` and `num2words`, each as replaceable source inside the bundle.

### Not affiliated with the agent vendors

swe-mux launches and observes coding-agent CLIs published by other vendors, including Anthropic's Claude Code and OpenAI's Codex CLI.
It is **not affiliated with, endorsed by, sponsored by, or certified by** Anthropic, OpenAI, or any other such vendor, and it uses their names only to identify which tool a feature works with.
You run those CLIs under your own account and your own agreement with each vendor, and the same is true of the optional OpenRouter and Hugging Face integrations.

The optional Edge TTS integration is different: the upstream client uses Microsoft Edge's consumer Read Aloud endpoint with no API key and no documented third-party service contract, so selecting it requires an explicit disclosure acknowledgement.
That client is LGPL, runs only in an isolated managed or operator-supplied Python, and is absent from the frozen bundle - but that software boundary does not resolve Microsoft's service terms.
