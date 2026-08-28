# swe-mux: a setup guide for an AI agent

You are reading this because someone asked you to help them install and set up swe-mux.
Walk them through it step by step.
Do not run the install command for them without asking first, and do not skip the verification step at the end of each stage.

Canonical sources, in order of authority.
If this file and one of these disagree, the source below wins and this file is stale:

- `https://github.com/jatoran/swe-mux` - the repository. `README.md` is the install reference.
- `https://swemux.dev/docs/` - the published documentation index.
- `https://github.com/jatoran/swe-mux/blob/master/.docs/development/OPERATOR_LIFECYCLE.md` - install, upgrade, uninstall, and recovery in full.

This file was last revised on 2026-08-28, against swe-mux 0.1.0.

---

## 1. What swe-mux is

swe-mux is a **local daemon that owns pseudoterminals**, plus a browser UI in front of it.

The person you are helping already runs coding-agent CLIs: Claude Code, Codex, opencode, or something else.
swe-mux does not replace those, wrap them, or proxy them.
It starts them in real terminals on the user's own machine, under the user's own subscription, and adds a layer around them.

Three properties are the reason it exists, and they are the three things worth explaining:

1. **The terminals outlive the UI.** A separate supervisor process holds every pseudoterminal, so closing the browser, restarting the daemon, or rebuilding the desktop application leaves the agents running. Reconnecting replays only the bytes the user missed.
2. **It reads what the agents are doing.** Working, ready, awaiting approval, or blocked, in one vocabulary across vendors, drawn from provider hooks, the transcript, the terminal, and the CLI's own state files.
3. **The whole thing works from a phone.** It is an installable web app reached over the user's own Tailscale tailnet. There is no relay, no swe-mux account, and no server the project operates.

What it is not: a hosted service, a model provider, a team tool, or an agent that acts on its own.
There is no swe-mux login because there is no swe-mux server.

## 2. Before you install anything

Check these with the user and stop if one fails.

- **Python 3.12 or newer.** `python --version`.
- **An installer.** `uv` is the recommended one (`uv --version`); `pipx` also works; `pip` works with a caveat covered below.
- **At least one agent CLI already installed and logged in.** swe-mux does not install, manage, or authenticate them. If they have none, they should install Claude Code, Codex CLI, or opencode first and log into it, then come back.
- **The operating system.** Windows 10 or 11 is the proving platform and the only one with a packaged desktop application. Linux runs the daemon plus a browser. macOS installs and the CLI runs, but no continuous-integration job on any host has ever started a daemon there, so treat macOS as unproven and expect to help debug.

Node is **not** required.
The published wheel already carries the built frontend.
Node 22.6 or newer is only needed if the user is building swe-mux from a checkout.

## 3. Install

Every method below installs the same three commands: `mux` (the CLI), `muxd` (the daemon), and `swe-mux` (the desktop window and tray).

Ask which one the user wants, then run exactly one.

```
# Recommended. Isolated environment, all three commands on PATH globally.
uv tool install swe-mux

# On Windows, take the desktop extra: it is what adds the native window and the tray icon.
uv tool install "swe-mux[desktop]"

# The same isolated, on-PATH install, without uv.
pipx install swe-mux

# NOT the same act. Installs into whichever environment is currently active and puts
# nothing on PATH globally, so `mux` works only inside that environment.
pip install swe-mux
```

Two things no install of any kind does, which you should say out loud before the user waits for them:

- **No desktop shortcut and no Start Menu entry.** Wheels have no post-install hook and pip runs no install-time code, so this is structural rather than a step somebody forgot. swe-mux starts from a terminal.
- **No agent CLI is installed, updated, or logged in.** That stays the user's own arrangement with each vendor.

The `desktop` extra is Windows-only by declaration: `pystray` and `pywebview` both carry a `win32` platform marker, so on Linux and macOS the extra resolves to nothing and the daemon plus a browser is the whole product.
On Windows it wants the WebView2 Runtime, which recent Windows builds already have.

### If nothing is on PATH afterwards

This is the ordinary outcome of `pip install`, and its warning scrolls past unread.

```
# The daemon, needing no PATH setup at all: `python -m swe_mux` is exactly `muxd`.
python -m swe_mux

# Where the three executables went (a `Scripts` directory on Windows).
python -c "import sysconfig; print(sysconfig.get_path('scripts'))"

# Every file this install wrote, those three included.
pip show -f swe-mux
```

### From a checkout instead

Only if the user intends to change swe-mux itself.

```
git clone https://github.com/jatoran/swe-mux
cd swe-mux
uv sync --extra desktop
npm --prefix frontend ci        # only the source flow needs Node
npm --prefix frontend run build # a fresh clone serves no UI until this runs once
uv run --extra desktop swe-mux
```

The frontend bundle is git-ignored build output.
A fresh clone serves no UI at all until that build has run once, and the symptom is a blank page rather than an error.

## 4. First run

```
muxd
```

Then open `http://127.0.0.1:8765`.
On Windows with the `desktop` extra, `swe-mux` opens the same thing in a native window with a tray icon.

Verify before going further:

```
mux doctor
```

`mux doctor` is read-only.
It reports on the daemon, the supervisor process, the frontend build, which agent CLIs it can detect, the tailnet listener, and the background loops.
Read its output to the user rather than summarizing it as "fine": it is the one command that distinguishes "installed" from "working", and its exit code is meaningful.

If the daemon will not start, or the page loads with no UI, the recovery procedures are in `OPERATOR_LIFECYCLE.md` (linked at the top of this file).
Do not guess at them.

## 5. The concepts to explain, in this order

Explain these as you go rather than up front.
A user who has just installed something wants a working session, not a vocabulary lesson.

**Project.** A folder swe-mux is pointed at, and the thing everything else binds to: sessions, layout, notes, history, file browsing, and per-project settings. Nothing works until there is one. Create it and point it at a repository the user already has.

**Session.** One pseudoterminal with a process in it. A session is either a plain shell or an agent session; the difference is whether swe-mux recognises the harness running inside and can add its layer.

**The promotion trick, which is the part people miss.** Open a terminal in a Project (`Ctrl+Alt+T`) and type `claude` or `codex` the way they always have. swe-mux puts its own launchers first on that terminal's PATH, so the ordinary command promotes the terminal in place: same pane, same scrollback, now carrying a transcript, a status, a prompt queue, and a context meter. There is no special "start an agent" ritual to learn.

**Status.** Every session carries one of a small set of states, and they mean the same thing regardless of which vendor's CLI produced them. `awaiting` is the one that matters: it means the agent is waiting on the human.

**The prompt queue.** Ordered messages staged against a session that is mid-turn. It is durable and head-of-line, and **automatic delivery is off by default**: by default a queued message waits for the user to send it. Say this explicitly, because a user who assumes otherwise will queue three messages and wonder why nothing happened.

**The Run menu.** Starts an agent, a shell, a worktree session, or a task imported from the repository (VS Code tasks, root `package.json` scripts, `.swe-mux/actions.toml`). Imported tasks stay inert until their exact current bytes are approved, and any edit revokes that approval.

**The control plane.** The evidence layer: deterministic facts captured at the tool boundary, detectors for loops and stalls and unverified claims, attention ranking with an interrupt budget, and commit-level provenance. It is **off by default, per Project**. Do not describe it as something the user already has; describe it as something they can turn on once the basics work.

Two keyboard entry points cover almost everything: `Ctrl+Alt+T` for a terminal at the Project root, `Ctrl+Alt+P` for the command palette.

## 6. Where things live

- **Configuration and data:** `~/.mux` on Windows, `$XDG_DATA_HOME/swe-mux` (else `~/.local/share/swe-mux`) on Linux, `~/Library/Application Support/swe-mux` on macOS. An existing `~/.mux` wins on every host, and `MUX_DATA_DIR` overrides all of it.
- **Configuration file:** `config.toml` inside that directory. Most of it is also editable in Settings, and Settings is the better route because it validates.
- **The daemon:** `http://127.0.0.1:8765` by default.
- **Agent transcripts:** wherever the vendor's CLI already put them. swe-mux reads them and never moves, rewrites, or deletes them.

## 7. Reaching it from a phone

This is optional and it is the step most likely to need you.

1. Install Tailscale on both the host machine and the phone, on the same tailnet.
2. Leave the daemon running. It listens on loopback and on the machine's detected Tailscale IPv4 address.
3. On the phone, open the machine's `.ts.net` hostname over HTTPS.

**HTTPS is not optional if the user wants the microphone or the clipboard**, because browsers restrict both outside a secure context.
swe-mux puts Tailscale Serve on port 443 in front of the daemon for exactly this.
The phone has to resolve the `.ts.net` name through Tailscale's DNS, so "Use Tailscale DNS" must be on and Android's Private DNS must be off or automatic.
The certificate is bound to the hostname, so the raw `100.x` address cannot serve HTTPS.

Say the security consequence plainly, because it is the whole access model: **Tailscale policy is the entire access boundary.**
There is no swe-mux login.
Any device the tailnet admits to that listener has terminal and code-execution authority on the host, equal to the account running the daemon.
Binding `0.0.0.0`, LAN interfaces, port forwarding, and Tailscale Funnel are unsupported configurations, not merely discouraged ones.

## 8. What leaves the machine

Answer this before the user asks, because they will.

swe-mux makes **one** network request on its own behalf: a daily `GET` of `https://swemux.dev/version.json` to find out whether a newer release exists.
It downloads nothing.
The file is byte-identical for every install on earth, and the request carries no query string, no custom header, no cookie, and no identifier of the machine or the install.
`update_check_enabled` in Settings, Diagnostics, Software updates turns it off, and off means no request is made at all.

Everything else that reaches the network is a feature the user turns on, or the user's own agent CLI talking to its own vendor:

- The agent CLIs contact their vendors under the user's subscription. swe-mux proxies nothing and resells nothing.
- Summarization, the assistant, and some control-plane features call an OpenRouter-compatible endpoint **with the user's own key**, and are off until configured.
- Web push goes through the browser vendor's push service, and only after the user subscribes a device.
- On-device speech models download once from Hugging Face, pinned by revision and verified by SHA-256.
- Saved provider accounts poll that vendor's own usage endpoint with the credential the user saved.
- Experimental Edge TTS reaches a Microsoft endpoint and requires an explicit acknowledgement before any text leaves the machine.

There is no analytics, no crash reporting, and no account.

## 9. Where to go next

- `https://swemux.dev/docs/` - the documentation index, including quick starts and per-feature pages.
- `https://swemux.dev/privacy/` - what the software and the website collect, stated precisely.
- `https://swemux.dev/compare/` - how swe-mux differs from the neighbouring tools, including where it loses.
- `https://github.com/jatoran/swe-mux/blob/master/.docs/development/OPERATOR_LIFECYCLE.md` - upgrade, uninstall, and every recovery procedure.
- `https://github.com/jatoran/swe-mux/issues` for bugs, and the Ideas discussions for feature requests.

## 10. Rules for you

- **Verify, do not assume.** After each stage, run the command that proves it worked (`mux doctor`, `python --version`, opening the page) and read the real output.
- **Do not invent commands or flags.** If something is not in this file or in `README.md`, look it up rather than guessing. A command that does not exist costs a user whose install is already broken the one thing they came for.
- **Do not run destructive commands.** Nothing here needs `sudo`, and nothing here needs an existing directory removed.
- **Say what you did not do.** If you skipped the phone step or the desktop extra, tell them, and tell them what they gave up.
- **The user's agent CLIs are not yours to reconfigure.** swe-mux reads their state; changing their settings, accounts, or transcripts is out of scope for this setup.
