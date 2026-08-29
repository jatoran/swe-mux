# swe-mux

**For developers running multiple coding agents locally, swe-mux shows what each agent actually did and lands finished branches behind checks you approved.**

swe-mux is a local, browser-based terminal multiplexer and control plane for the coding-agent CLIs you already run.
It owns the pseudoterminals, so Claude Code, Codex, opencode, and any other CLI or shell run in a real terminal exactly as they do outside it, while swe-mux adds the layer around them.
It runs on your own machine: no vendor-operated backend, no relay, no account, and no telemetry.

<!-- TODO(release): hero demo - video/GIF goes here. The capture rig exists
     (`trailer/capture_env.py`, a synthetic install with invented projects) and the video does not;
     the operator records it, to the shot list in site/README.md section 2. -->

[![ci](https://github.com/jatoran/swe-mux/actions/workflows/ci.yml/badge.svg)](https://github.com/jatoran/swe-mux/actions/workflows/ci.yml)
[![license](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)

## What it adds

- **Deterministic evidence of what each agent did.** Every file write hashed on the exact bytes written, every command with its exit class, test output parsed down to the failing set, git operations, tool calls - each keeping a pointer back to the moment it happened. Read from the work rather than from the agent's account of it. ([Tier 0 facts](.docs/design/features/tier0-facts.md))
- **Commit-level provenance.** Which session and conversation produced a commit, split into committer and contributor, from that same deterministic capture. ([Tier 0 facts](.docs/design/features/tier0-facts.md))
- **Parallel worktrees, landed behind a verification gate.** One branch at a time: reconcile with the trunk, run the verification command whose exact bytes you approved, then fast-forward only. A conflict or a failed gate goes back to the branch's own agent, and an agent cannot approve the gate its own land runs. ([land queue](.docs/design/features/land-queue.md))
- **One status vocabulary across vendors.** Working, ready, awaiting approval, or blocked - read from provider hooks, the transcript, the PTY, and the CLI's own state, with every transition kept in a durable ledger. Ambiguous evidence resolves to the conservative prior rather than to a guess. ([status detection](.docs/design/features/status-detection.md))
- **A prompt queue that waits for a real gate.** Stage ordered messages against a mid-turn conversation. The queue is durable, head-of-line, bound to the conversation, and automatic delivery is off by default. ([prompt queue](.docs/design/features/prompt-queue.md), [auto-delivery](.docs/design/features/auto-delivery.md))
- **The whole workspace on a phone.** An installable PWA over your own Tailscale tailnet, with no relay and no swe-mux login. Terminals, git review, the editor, previews, on-device voice, and optional web push. ([remote access](.docs/design/features/remote-access.md), [voice](.docs/design/features/voice.md))
- **Any CLI, and any shell.** Anything that runs in a terminal runs here unchanged, including one swe-mux has never heard of; the harnesses in its registry get normalized input, status, transcripts, history, and accounts. Native transcripts are never moved or rewritten. ([backends](.docs/design/features/backends.md))
- **Sessions that outlive the app.** A supervisor process separate from the daemon and the UI holds every pseudoterminal, so a daemon restart or a full app rebuild leaves the agents working, and reconnecting replays only the bytes you missed. New builds of swe-mux ship from an agent session running inside swe-mux. Behind it, cold session recovery covers what a supervisor cannot - its own crash, a force close, a power loss - by bringing those sessions back as readable, resumable rows carrying their last scrollback. ([sessions](.docs/design/features/sessions.md), [recovery](.docs/design/features/session-recovery.md))

### Almost everything in the control plane is off until you ask for it

This is the shape of the whole product and it is worth knowing before you install it rather than after.

- **Automations are per-Project opt-in and every one of them ships off**, with one exception: `session_control`, a permission gate that reads nothing, runs nothing, and spends nothing on its own.
- **The land queue needs four things** before an agent can trigger one: the install-wide switch, the Project's opt-in, a `land_grant` raised from its default of `draft`, and a verification command whose exact bytes you approved. Running it yourself from the Git drawer needs the last of those.
- **The model-backed capabilities ship off**: the behaviour timeline, the attention observers, and the Mux assistant.
- **Read aloud ships off** (`tts_enabled`), and hands-free conversation is a separate opt-in beside it.

Nothing in the control plane runs on a Project that did not opt in, and nothing reaches a model without a budget you set.

### What crosses the network

swe-mux runs on your own machine, and the project operates no backend and no relay: your data is SQLite on your disk, there is no swe-mux account, and nothing reports usage anywhere.
It is not a tool with no network in it, and the difference matters.

Your agent CLIs keep talking to their own vendors under your own subscription; swe-mux proxies nothing and resells nothing.
Four optional capabilities reach out, and each is off until you turn it on:

- Model calls through an OpenRouter-compatible endpoint, with your key.
- Web push, through your browser vendor.
- The on-device speech models, downloaded once from Hugging Face and then run locally.
- Experimental Edge TTS, which additionally requires an explicit service and privacy acknowledgement before any text leaves the machine.

The one request swe-mux makes on its own behalf is a daily fetch of `https://swemux.dev/version.json` to check for a newer release - nothing downloads, and the file is identical for every install, with no query string, header, cookie, or identifier on it.
Settings → Diagnostics → Software updates (`update_check_enabled`) turns it off entirely.
Installing an update is a separate act you take: `swemux update --install <version>` downloads that release, checks its SHA-256 against the published manifest before anything is staged, and refuses rather than installs if the release would need a new PTY supervisor - which would end your live sessions.

## Install

<!-- TODO(release): the installer exists as of v0.1.2 and is NOT code signed. When a signing
     certificate is in hand, say here which Windows builds and architectures it is signed for and
     drop the SmartScreen sentence below. The site does not need that edit:
     https://swemux.dev/#download is drawn from the release manifest. This file is the copy that
     stays manual, so it is the one to remember. -->

swe-mux is on PyPI. The wheel is pure Python and carries the built frontend, so this needs no Node and no checkout.
Every install below writes the same five commands, which are three programs: `swemux` (the CLI), `swemuxd` (the daemon), and `swe-mux` (the desktop window and tray).
`mux` and `muxd` are kept as aliases of the first two - the same programs under shorter names, so anything written against them keeps working.
Prefer `swemux` and `swemuxd` in anything you write down: `mux` is a name shared with at least one unrelated tool, and on a machine that has both, whichever installed last is the one your shell finds.

```
# Recommended. Isolated environment, and every command on your PATH globally.
uv tool install swe-mux

# Windows: the `desktop` extra is what adds the native window and the tray icon.
uv tool install "swe-mux[desktop]"

# The same isolated, on-PATH install, without uv.
pipx install swe-mux

# NOT the same act. Installs into whichever environment is currently active and
# puts nothing on PATH globally, so `swemux` works only inside that environment.
pip install swe-mux
```

Then run `swemuxd` and open <http://127.0.0.1:8765>, or `swe-mux` for the desktop window on Windows.
`swemux doctor` is a read-only health report covering the daemon, the supervisor, the frontend build, detected agent CLIs, the tailnet listener, and background loops.

**No Python install of any kind creates a desktop shortcut or a Start Menu entry.**
Wheels have no post-install hook and pip runs no install-time code, so that is structural rather than a step somebody forgot: start swe-mux from a terminal, or run `swemux install-shortcut` afterwards.

A **Windows installer** that does create one is published from v0.1.2 onward, alongside a portable archive, on the [releases page](https://github.com/jatoran/swe-mux/releases); <https://swemux.dev/#download> is drawn from the same release manifest and says which desktop builds the current release actually carries.
It is **not code signed**, so Windows SmartScreen warns on first run and you have to choose to continue past it.
Signing is planned and needs a certificate; until then, the PyPI install avoids that prompt entirely.

**On Windows, take the `desktop` extra.**
Without it you still get a `swe-mux` command, and it fails on a missing import rather than opening a window.
The extra wants the WebView2 Runtime, and it is Windows-only by declaration - `pystray` and `pywebview` both carry a `win32`-only platform marker in `pyproject.toml` - so on Linux and macOS it resolves to nothing and the daemon plus a browser is the whole product.

If nothing is on your PATH afterwards, which is the ordinary outcome of `pip install` and whose `WARNING: The scripts ... are installed in '...' which is not on PATH` scrolls past unread:

```
# The daemon, needing no PATH setup at all: `python -m swe_mux` is exactly `swemuxd`.
python -m swe_mux

# Where the executables went (a `Scripts` directory on Windows).
python -c "import sysconfig; print(sysconfig.get_path('scripts'))"

# Every file this install wrote, those included.
pip show -f swe-mux
```

To run from a checkout instead, which is what you want if you are changing it:

```
git clone https://github.com/jatoran/swe-mux
cd swe-mux
uv sync --extra desktop
npm --prefix frontend ci        # only the source flow needs Node
npm --prefix frontend run build # a fresh clone serves no UI until this runs once
uv run --extra desktop swe-mux
```

Upgrades are `uv tool upgrade swe-mux` or `pipx upgrade swe-mux`; the full install, upgrade, uninstall and recovery reference is [`.docs/development/OPERATOR_LIFECYCLE.md`](.docs/development/OPERATOR_LIFECYCLE.md).

Build the Windows distributable with `uv sync --extra desktop --extra voice-local --group package`, then `uv run --extra desktop --extra voice-local --group package python packaging/build_desktop.py`.
It is deliberately an `onedir` build: distribute the whole `dist/swe-mux/` folder, not only the `.exe`.
Packaging rules: [`.docs/design/features/desktop-shell.md`](.docs/design/features/desktop-shell.md).

### Requirements

- Python 3.12 or newer, and [uv](https://docs.astral.sh/uv/) or pipx to install with, or pip if you are installing into an environment you already keep.
- Node 22.6 or newer, only to build the frontend from source. The published wheel carries it already and needs no Node.
- At least one agent CLI, already installed and logged in. swe-mux does not install, manage, or proxy them.
- Optionally Tailscale, to reach the daemon from a phone.

On-device speech (Kokoro text-to-speech and faster-whisper dictation) is `--extra voice-local`, roughly 400 MB of wheels and model machinery, and the desktop bundle always carries it.
Its models are downloaded once, from Hugging Face, on an explicit press, and verified against a pinned hash; nothing is fetched until then.

**Speech-to-text decodes on your own machine in both shipped configurations.**
The default is faster-whisper, which needs the extra; the alternative is Windows Speech Recognition, which needs Windows and no extra.
The `stt_engine` setting selects between them, as `whisper` or `sapi`.
There is no cloud speech path and no browser speech-recognition fallback: without either engine available, transcription returns a typed error rather than sending audio anywhere.
Text-to-speech has the same shape, except that the explicitly experimental Edge TTS provider is the one option that does leave the machine, and selecting it requires an acknowledgement first.

## First run

Create a Project and point it at an existing folder.
`Ctrl+Alt+T` opens a terminal at that Project's root and `Ctrl+Alt+P` opens the command palette; nothing is spawned until you ask for it.

Then type `claude`, `codex`, or another supported CLI normally.
swe-mux puts its own launchers first on that terminal's PATH, so the usual command promotes the terminal you are standing in to an agent session in place: same pane, same scrollback, now carrying a transcript, a status, a queue, and a context meter.

The **Run** menu starts an agent, a shell, a worktree session, or an imported task. Imported tasks - VS Code tasks, root `package.json` scripts, `.swe-mux/actions.toml` - stay inert until their exact current bytes are trusted, and any edit requires approval again. ([project actions](.docs/design/features/project-actions.md))

## Platform support

The wheel is `py3-none-any`, and CI builds it, validates it, and install-smokes it on `windows-latest`, `ubuntu-latest` and `macos-latest` on every push, so installing and running the CLI is checked on all three.
CI also starts a real daemon on `ubuntu-latest` and `windows-latest` from the **source checkout**, on an ephemeral port under a temporary data directory, and proves it serves a shell session and exits cleanly.
No CI job on any host starts a daemon from a **published artifact**, so that is exactly where the proof stops. Beyond it:

- **Windows 10 or 11 is the proving platform.** The full gate runs there in CI (the `verify` job), including the real ConPTY integration tests and the Playwright renderer suite, and it is the only platform the desktop app ships on. PowerShell 7 is the primary shell contract; 5.1, CMD, and a WSL distro shell are separately supported profiles.
- **Linux runs headless plus a browser**, on a required CI leg that syncs with no extras. There is no Linux desktop app, by design.
- **macOS is implemented, typechecked, and exercised.** Its CI leg runs the whole suite on `macos-latest`, but that leg is still `continue-on-error` and so is not required to pass; treat macOS as unproven.

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

The published docs are at <https://swemux.dev/docs/>.
The maintained design contract starts at [`.docs/design/00_OVERVIEW.md`](.docs/design/00_OVERVIEW.md), and [`.docs/CLAUDE.md`](.docs/CLAUDE.md) routes each subsystem to the document that owns it.
The landing page and the argument it makes live in [`site/`](site/); the project homepage is <https://swemux.dev>.

## Contributing

Contributions are welcome.
Feature requests are [Discussions in the Ideas category](https://github.com/jatoran/swe-mux/discussions/categories/ideas), where a thumbs-up is a vote and the most-voted open ideas are drawn on the [roadmap](https://swemux.dev/roadmap/); [issues](https://github.com/jatoran/swe-mux/issues) are for bugs.
Describe the problem rather than the fix, and check [deliberately not on the roadmap](https://swemux.dev/roadmap/#not-planned) first.
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
