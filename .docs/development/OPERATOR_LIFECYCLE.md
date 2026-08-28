# Operator lifecycle: install, upgrade, uninstall, diagnose, recover, back up

The reference for someone who has swe-mux installed and needs to change it, understand where its state lives, or find out why it stopped working.
It is not a feature tour; `design/00_OVERVIEW.md` is the map of what the product does.

Three facts govern everything here and are stated once rather than repeated.

**swe-mux 0.1.0 is on PyPI, published 2026-08-28.**
`.github/workflows/release.yml` put it there over PyPI Trusted Publishing on a `v*` tag, so no API token exists anywhere to leak.
The artifacts are `swe_mux-0.1.0-py3-none-any.whl` and `swe_mux-0.1.0.tar.gz`; `https://pypi.org/pypi/swe-mux/json` answers 200.
The `uv tool`, `pipx`, and `pip` commands below were **executed** against that published wheel on 2026-08-28 rather than transcribed from `pyproject.toml`: each installed into a throwaway environment, put `mux`, `muxd`, and `swe-mux` on that environment's bin directory, and answered `--help` with exit 0.
An installed copy reports `0.1.0` and carries `swe_mux/static/index.html` with its 39 hashed JS assets, so it serves the interface without Node ever being present.
A package install is now the primary path, and the source install is for people changing swe-mux rather than running it.

**The platform matrix is: the wheel installs on all three hosts, and the daemon is proven only on Windows.**
That is drawn from `.github/workflows/ci.yml` rather than from any summary, including this one.
The `verify` job runs on `windows-latest` and carries the full gate plus the wheel build, artifact validation, install smoke, and the Playwright renderer suite.
The `platform` job is a matrix of `ubuntu-latest` (`unproven: false`, blocking) and `macos-latest` (`unproven: true`, so `continue-on-error` is true for that leg), and since 2026-08-28 both legs also build the wheel, validate it, and run the install smoke.
So "the wheel builds and installs and the CLI runs" is a question CI asks on all three hosts, and it was answered green on all three on 2026-08-28.
Read the badge for today's answer rather than this sentence: those steps sit after the suite in the job, so a test failure on a leg skips them, and a skipped step is not a passing one.

**What is proven nowhere is a running daemon.**
`install_smoke.py` says so in its own docstring and means it: it starts no daemon and binds no port, because the daemon owns a fixed port and a single data directory and a CI job that started one would be a second writer against whatever else is running.
No other CI job starts one either, on any host.
Do not let "installs and the CLI runs" be read as "verified working end to end" - they are different claims, and only the first one has evidence.
`pyproject.toml` declares `Operating System :: Microsoft :: Windows` and `Operating System :: POSIX :: Linux` and deliberately no macOS classifier and no `OS Independent`, which is the same distinction expressed in metadata.

---

## Install

### Requirements

- Python 3.12 or newer.
  `requires-python = ">=3.12"` in `pyproject.toml`, restated as `MINIMUM_PYTHON = (3, 12)` in `src/swe_mux/doctor_local.py` so the check works in an installed copy that carries no `pyproject.toml`; `tests/test_doctor_local.py` reconciles the two.
- Node 22.6 or newer, only to build the frontend from source (`engines.node` in `frontend/package.json`).
  A published wheel carries a built frontend and needs no Node.
- At least one agent CLI, already installed and logged in.
  swe-mux detects and observes them; it does not install, manage, or proxy them.

### From PyPI

```
uv tool install swe-mux
uv tool install "swe-mux[desktop]"
```

```
pipx install swe-mux
pipx install "swe-mux[desktop]"
```

Either installs the three entry points declared in `[project.scripts]`: `mux` (the CLI), `muxd` (the daemon), and `swe-mux` (the desktop window and tray).
Take the bracketed form on Windows and the plain one elsewhere; the Extras table below says why.

**`uv tool install` is not `uv add`, and the difference is the whole point of this section.**
`uv tool install` and `pipx install` give the package its own environment and put its console scripts on your PATH, so `mux` is a command you can run anywhere.
`uv add swe-mux` and `pip install swe-mux` install it into an environment you already have, which makes `import swe_mux` work there and leaves `mux` reachable only from inside that environment.
Both are legitimate; they answer different questions, and reaching for the second while wanting the first is the confusion this paragraph exists to prevent.

**There is no `--extra` flag on `uv tool install`.**
Extras are named in the requirement itself, which is why the second command above is `"swe-mux[desktop]"` and not `uv tool install swe-mux --extra desktop`.
The quotes are for the shell, which would otherwise glob or mangle the brackets.

### From source

```
git clone https://github.com/jatoran/swe-mux
cd swe-mux
uv sync --extra desktop
npm --prefix frontend ci
npm --prefix frontend run build
uv run --extra desktop swe-mux
```

For a headless daemon plus an ordinary browser, which is the Linux and macOS shape, run `uv run muxd` and open <http://127.0.0.1:8765>.

`npm --prefix frontend run build` is not optional on a fresh clone.
Its output lands in `src/swe_mux/static/` and is gitignored, so a checkout that has never run it serves the API and no interface at all.
This is the one respect in which a source install is harder than a package install: the published wheel carries that bundle already.

### The desktop app

The Windows distributable is a PyInstaller `onedir` build produced by `packaging/build_desktop.py`.
Distribute the whole `dist/swe-mux/` folder, never the `.exe` alone.
Build dependencies and the build itself:

```
uv sync --extra desktop --extra voice-local --group package
uv run --extra desktop --extra voice-local --group package python packaging/build_desktop.py
```

`voice-local` is optional to install and mandatory to build from.
`num2words` (LGPL-2.1) reaches the distributed closure through it and must ship as replaceable source under `_internal/num2words/`, so `build_desktop.verify_build_extras_installed` and `redeploy_desktop`'s preflight both refuse a build without it rather than producing a bundle that fails its own license verification.

### Extras

| Extra | What it adds | Degraded without it |
| --- | --- | --- |
| `desktop` | `pystray` and `pywebview`, both marked `sys_platform == 'win32'`. Gives the native window, the tray icon, and the "Start with Windows" registration. | The `swe-mux` entry point raises `RuntimeError("Desktop dependencies are missing. Install with: uv sync --extra desktop")`. `muxd` plus a browser is unaffected. On non-Windows hosts the extra resolves to nothing, which `mux doctor` reports as `unavailable` rather than as a fixable gap. |
| `voice-local` | On-device speech: `faster-whisper` dictation, Kokoro TTS through `onnxruntime`, and the misaki/spaCy English G2P behind it. Roughly 400 MB of wheels and model machinery. | Read aloud falls back to `tts_engine = "sapi"`, the OS voice, which is already the shipped default. Dictation falls back to `stt_engine = "sapi"`, which is Windows Speech Recognition driven through `powershell.exe` and refuses on any other host. Every call site imports lazily and answers with a typed diagnostic naming the extra. |
| `preview-capture` | `playwright`, for Preview screenshot capture. | Capture is unavailable. `capture_capability()` distinguishes the extra being absent from the extra being present with no browser binary, and carries the exact command for each. Nothing downloads a browser implicitly. |
| `voice-edge` | `edge-tts==7.2.8`, as a source-install convenience only. | Nothing, structurally. The runtime reaches Edge TTS through an externally managed bridge interpreter, so whether `edge_tts` resolves in this environment says nothing about whether the feature works. The frozen desktop spec excludes the LGPL package even when the build environment has this extra installed. `mux doctor` deliberately does not report a row for it, because such a row would be a confident wrong answer. |

Optional assets are a separate question from optional extras, and `mux doctor` reports both.
An extra installed with nothing downloaded and a cached model with no extra are different states with different commands.

- Playwright's Chromium: `uv run playwright install chromium`, about 150 MB.
- Kokoro voice weights: Settings → Voice → Download Kokoro voices, cached under `<data_dir>/voice-models/kokoro`.
- Whisper weights: Settings → Voice → Download speech model, cached in the Hugging Face hub cache (`~/.cache/huggingface/hub` by default; swe-mux sets no `HF_HOME` override).

Nothing downloads on a fresh install unless the operator turns voice on: `tts_enabled` and `stt_enabled` both default to `False`.

### The development sync trap

For a source checkout you develop and build in, use the full form:

```
uv sync --extra desktop --extra voice-local --group dev --group package
```

The short form silently strips PyInstaller and breaks the desktop build, which is why `RELEASE_MANUAL_TASKS.md` § Sync command names the long one.
A measured second half of the same trap: `uv sync --extra desktop --extra voice-local --group package` also uninstalls `playwright`, `greenlet`, and `pyee`, because the command does not name `preview-capture`.
That is correct for the bundle, whose distributed closure is `("desktop", "voice-local")`, and wrong for a source-run daemon, which silently loses Preview screenshot capture.
Adding `--extra preview-capture` is the fuller form for a primary checkout and leaves the license audit clean, because `preview-capture` is outside the distributed closure.

A worktree is a different case and deliberately syncs less: `.worktree-setup` runs `uv sync --extra voice-local` and omits `desktop`, because a worktree never builds or runs the app.

---

## Upgrade

| Installation | Upgrade |
| --- | --- |
| `uv tool` | `uv tool upgrade swe-mux` |
| `pipx` | `pipx upgrade swe-mux` |
| Source checkout | `git pull`, then the sync command for that checkout, then `npm --prefix frontend ci && npm --prefix frontend run build` if frontend dependencies or sources changed |
| Frozen desktop app | `uv run python packaging/redeploy_desktop.py`, or the UI menu's "Rebuild + redeploy app (keep sessions)" (`POST /api/daemon/redeploy`) |

Three upgrade properties are worth knowing before you rely on them.

**A daemon restart preserves sessions only when the PTY supervisor owns them.**
`pty_supervisor_enabled` ships `False`.
With it off, `POST /api/daemon/restart` refuses with HTTP 409 and `{"error": "supervisor_not_attached"}` rather than silently reaping, and `mux reload-daemon --force` is the explicit override that accepts the reap.
The tray omits its "Restart daemon (keep sessions)" item entirely when the setting is off, because the item would be a footgun there.

**The frozen app respawns its own executable.**
`POST /api/daemon/restart` and a plain `npm run build` both reach a daemon that runs from source and neither reaches the frozen bundle, which serves its own copy at `dist/swe-mux/_internal/swe_mux/static` and respawns its own bundled backend.
Confirm which build is being served before assuming a change is live: compare the hashed asset the live daemon returns against the one you just built.

**The PTY supervisor is updated separately and reaps every session.**
`packaging/redeploy_desktop.py` cannot ship a supervisor change and says nothing when it does not.
Updating it is `uv run muxd --shutdown`, then `uv run python packaging/build_desktop.py --supervisor-only`, then relaunch, all from outside swe-mux.
A release that requires it says so in its release notes rather than leaving the updater to surprise the operator.

**A schema migration keeps a copy of what it replaced.**
`load_config` rewrites `config.toml` when it migrates, and takes `config.toml.bak` first.
Ordinary settings edits do not take a backup.
`settings.json` has the same arrangement with `settings.json.bak`.
`VoiceStore._migrate` is the one place an upgrade destroys rows: pre-schema-3 voice clips cannot be reassembled into streams and are discarded, which `tests/test_migration_compatibility.py` records as an asserted exemption rather than a surprise.

---

## Uninstall, and what is left behind

| Installation | Uninstall |
| --- | --- |
| `uv tool` | `uv tool uninstall swe-mux` |
| `pipx` | `pipx uninstall swe-mux` |
| Source checkout | Delete the checkout and its `.venv` |
| Frozen desktop app | Delete the `dist/swe-mux/` folder |

**The data directory is not removed by any of them, and that is deliberate.**
`~/.mux` on Windows, and its platform equivalents elsewhere, holds every project registration, the whole history database, provider account credentials, and your configuration.
An uninstall that deleted it would make reinstalling indistinguishable from starting over.

If you want a clean slate, delete the data directory yourself, and read the next paragraph first.

**Deleting the data directory can delete real git checkouts.**
`worktree_root` defaults to empty, which resolves to `<data_dir>/worktrees`.
Worktree checkouts created through swe-mux therefore live *inside* the directory you are about to remove unless you configured `worktree_root` elsewhere.
`GET /api/config` reports the resolved absolute path; check it before deleting anything.

Three registrations live outside the data directory and outside the installation, and no uninstall touches any of them.

- **The Windows login entry.** `HKEY_CURRENT_USER\Software\Microsoft\Windows\CurrentVersion\Run`, value name `swe-mux`, written only if you turned on the tray's "Start with Windows". Toggle it off before uninstalling, or delete the value.
- **Windows Defender Firewall rules.** `swe-mux Mobile` and `swe-mux WSL Bridge`, created only if you enabled the direct tailnet listener or the WSL bridge. Remove with `Remove-NetFirewallRule -Name "swe-mux Mobile"`.
- **Tailscale Serve.** A Serve configuration on 443 proxying to the daemon's loopback port, created by the mobile-voice auto-enable. It is Tailscale's state, not swe-mux's; `tailscale serve status` shows it.

Two caches are shared with other tools and are not swe-mux's to remove: the Hugging Face hub cache holding Whisper weights, and the Playwright browsers root (`%LOCALAPPDATA%\ms-playwright` on Windows, `~/Library/Caches/ms-playwright` on macOS, `$XDG_CACHE_HOME/ms-playwright` else `~/.cache/ms-playwright` on Linux).

swe-mux **reads** the agent CLIs' own homes (`~/.claude` and its siblings) for detection, skills, and transcripts, and writes nothing into them.
Every per-session harness artifact it materializes is written under its own data directory instead.

---

## Where things live

`default_data_dir()` in `src/swe_mux/config.py` resolves in this order, and the order is the contract.

1. `MUX_DATA_DIR`, if set and non-empty, expanded.
2. `~/.mux`, if it already exists, on every host.
   An existing directory always wins, so a Linux user of an earlier build, or anyone who copied a directory across, keeps their data rather than silently starting from an empty one beside it.
3. Otherwise the platform convention: `~/.mux` on Windows, `~/Library/Application Support/swe-mux` on macOS, and `$XDG_DATA_HOME/swe-mux` (else `~/.local/share/swe-mux`) on Linux.

`config.database_path` is `<data_dir>/mux.db`.

### The data directory, by what each entry is

**Configuration and user-authored state.**

| Path | Holds |
| --- | --- |
| `config.toml` | The daemon configuration, schema-versioned. `config.toml.bak` is the pre-migration copy. |
| `settings.json` | Per-device browser-owned settings, with `settings.json.bak` alongside. |
| `keybindings.json` | Keybinding overrides. |
| `rules.toml`, `hooks.toml` | Automation rules and legacy hook definitions. |
| `prompts/` | The global prompt library. |
| `notes/` | Note items and the global Scratchpad. |
| `prompt-library-state.json`, `previews.json`, `project-action-trust.json` | Prompt-library state, preview registrations, and the trusted-task digests. |

**Databases.**

| Path | Holds |
| --- | --- |
| `mux.db` (plus `-wal`, `-shm`) | One WAL database shared by History, Automation, Operational Telemetry, the Status Timeline, Voice clip rows, Tier 0 facts, the Prompt Queue, Schedules, Clipboard history, the code graph, and the durable session registry. |
| `land-queue.sqlite3` (plus `-wal`, `-shm`) | The land queue's requests and event trails. |

**Credentials.**

| Path | Holds |
| --- | --- |
| `provider-accounts/`, `provider-accounts.json` | Captured provider logins and the account manifest. Real credentials. |
| `automation.secrets.json` | The metered-API key store. On Windows the values are current-user DPAPI blobs; on macOS the Keychain and on Linux `secret-tool` hold them instead and this file is not the store. |
| `push-vapid.pem`, `push-subscriptions.json` | The web-push identity and its subscriptions. An unreadable PEM is regenerated and every existing subscription is dropped. |
| `desktop-control.token` | The desktop shell's control token, regenerated per run. |

**Logs.**
All rotate, so a noisy day cannot grow an unbounded file.

| Path | Holds |
| --- | --- |
| `daemon.log` | The application log, root logger, 10 MB × 5. Each line carries the request id that caused it and the `extra=` fields the call site passed. |
| `access.log` | The aiohttp request log, isolated with `propagate=False` so request spam cannot drown signal in `daemon.log`, 10 MB × 3. |
| `crash.log` | `faulthandler` output on hard native crashes. External kills cannot be observed in-process and are covered by the lifecycle ledger instead. |
| `desktop-daemon.log`, `daemon-relaunch.log` | The console redirect held by whoever spawned the daemon: an early-startup and native-stderr crash catcher, not the structured log. |
| `supervisor.log`, `supervisor-console.log` | The PTY supervisor's own logs. |
| `redeploy.log`, `redeploy-result.json` | The frozen-app redeploy's output and last verdict. |
| `lifecycle.log`, `git-provenance-backfill.log` | The lifecycle ledger and the provenance backfill's log. |

**Generated per run, and regenerated if deleted.**

| Path | Holds |
| --- | --- |
| `bin/` | The agent launcher shims, rewritten for every harness on each daemon start. This is what `MUX_SHIM_DIR` names and what a shell pane's PATH is prefixed with when `agent_shims_on_shell_path` is on. |
| `<harness>-hooks.json`, `<harness>-mcp.json` | Per-harness hook settings and MCP registration, written by the Claude adapter family. |
| `sessions/<session-id>/` | Per-session hook settings and identity, removed when the session tears down. |
| `omp-extensions/`, `opencode-configs/` | Per-harness config roots a shim-launched harness materializes into. |
| `supervisor.json`, `daemon-heartbeat.json`, `redeploy.lock` | Live-process discovery and coordination files. |
| `tls/` | Tailscale certificate material. |
| `usage-cache.json` | The usage reconstruction cache. |
| `webview/` | The desktop WebView's own storage. |
| `hook-spool/` | Hook events awaiting ingestion. |

**Media and capture.**

| Path | Holds |
| --- | --- |
| `voice/` | Synthesized read-aloud clips, bounded by `tts_cache_mb` (200 MB default), plus `spelled_words.json` and the Edge voice catalog. |
| `voice-models/kokoro` | Downloaded Kokoro weights. |
| `media/` | Session media attachments. |
| `preview-shots/` | Preview screenshots. |
| `recovery/` | Terminal checkpoints for cold session restore, bounded by `session_recovery_checkpoint_bytes` (256 KB per session), `session_recovery_retention_days` (7), and `session_recovery_max_sessions` (40). |
| `agent-context-backups/` | Root instruction files backed up before a sync. |
| `integrations/` | Externally managed integrations, notably the Edge TTS bridge under `integrations/edge-tts/current`. |
| `worktrees/` | Worktree checkouts, when `worktree_root` is unset. Real git working trees, not swe-mux state. |

---

## Diagnosis: `mux doctor`

`mux doctor` is the entry point, and it has two modes that answer different questions.
The CLI decides between them by what happened to the request, never by a flag.

### Full report, when a daemon answers

`GET /api/diagnostics/doctor`, assembled by `doctor.build_doctor_report` from payloads the daemon already produces.
It is a flat `checks` list plus a `summary` count and a `capabilities` block, and nothing in it reads a secret, a terminal byte, or message content.

Categories: `daemon` (health, the UI build served, supervisor attachment, unadopted supervised sessions), `harness` (each registry harness and its detected version), `remote` (Tailscale connection and Serve), `firewall`, `prerequisites` (git, Node, npm, Tailscale), `status` (fleet status health), `background` (loop liveness and fault counts), `freshness` (agent sessions reporting a dead or relocated conversation), `wsl`, and `optional-assets`.

Each check carries a status and a severity, and the two are independent.
Status is `ok`, `warn`, `fail`, `unavailable`, or `unchecked`.
Severity separates an unavailable optional feature from a failure that compromises terminal ownership, cleanup, or delivery.

### Local report, when the daemon is unreachable

A connection failure produces `doctor_local.build_local_doctor_report` instead of a bare connection error, because the daemon not starting is the single most likely new-user failure and the full report presupposes exactly the thing that is missing.
It is a fallback, never a substitute: a daemon that answers is byte-for-byte unaffected, and an HTTP error from a daemon that *did* answer is deliberately not a fallback trigger, because that is a daemon fault rather than an install fault.

The checks are the ones that stop a daemon starting, in the order a reader should meet them.

1. `install.python` — the interpreter is at or above the 3.12 floor. A frozen build reports `ok` unconditionally, because it carries its own interpreter.
2. `install.imports` — `swe_mux.server` and its dependency graph import, with the real exception attached when they do not.
3. `install.config` — `config.toml` loads and validates. This is the fault the CLI otherwise hides: `resolve_base_url` swallows a config failure and falls back to the loopback default, so every `mux` command may be pointed at the wrong daemon.
4. `install.frontend` — the installed package carries a bundle. A source checkout with none is `warn` with a build command; an installed copy with none is `fail`, because that is a broken artifact.
5. `install.data_dir` — the data directory exists and is writable, probed with a real temporary file rather than `os.access`, which on Windows reports the read-only attribute and effectively nothing else.
6. `install.database` — `mux.db` opens. Opened `mode=rw` so the diagnostic never creates the file it is checking for, and probed with a schema read rather than `PRAGMA integrity_check`, which would cost minutes on a large store.
7. `install.port` — whether something already owns the configured port. A TCP connect, never a bind: binding to test a port is wrong on Windows, where `SO_REUSEADDR` lets a second socket take a port from its owner.
8. `install.pty` — this host's pseudoterminal backend imports. An unimportable backend is a daemon that starts, serves the UI, and fails every spawn.
9. `install.supervisor_bundle` — presence only, and only in a frozen build. It deliberately does not ask whether the bundle is current, because `supervisor_bundle_current()` reports "stale" when PyInstaller is merely absent and acting on that answer reaps every live session.

It then reuses the daemon report's own builders for `prerequisites` and `harness`, since those are pure host probes with no daemon state in them, adds an `extras` row for `desktop` and one for `voice-local` carrying the exact install command, and adds the same first-use asset rows the daemon report carries.
`preview-capture` and `voice-edge` have no `extras` row on purpose, for the reasons given in the extras table.
One implementation per check is the rule the reuse enforces: a second copy of a host probe would eventually disagree with the first, and what is not re-answered locally is anything that reads daemon runtime state.

**`unchecked` is its own status, and that is the load-bearing decision.**
Folding a skipped check into `ok` claims health nobody measured; folding it into `unavailable` claims a capability was measured absent.
Either turns a degraded report into a confident wrong one, which is worse than the connection error it replaced.
So every check the local report does not run is emitted as a row naming what is unknown and why, counted separately in the summary, and rendered `[????]` rather than reusing `[n/a ]`.

Seven categories are declared unchecked, for two distinct reasons the detail states.
`daemon`, `status`, `background`, and `freshness` read live-process state that exists nowhere else.
`remote` and `firewall` are not the question while nothing is listening.
`wsl` is skipped because inspecting a distribution starts it, which a diagnostic must not do on a host that has not opted into the bridge.

The report adds three fields the daemon report does not carry, so the two can never be confused: `mode: "local"`, `complete: false`, and a `daemon` block recording what was unreachable.
Its header reads `swe-mux doctor (local, no daemon)`, and its preamble states the unreachable URL and what `[????]` means.

### Exit codes

The CLI's codes are a contract; scripts branch on them, never on prose.

| Code | Meaning |
| --- | --- |
| 0 | Success. For `doctor`, a full report with no failing check. |
| 1 | A `doctor` report with a failing check, local or full. |
| 2 | Usage error, reserved by argparse. |
| 3 | Daemon unreachable. For `doctor`, also a clean local report. |
| 4 | Daemon HTTP error. |
| 5 | Ambiguous session name. |
| 6 | Not found. |

The `doctor` codes compose the two that already existed rather than adding a scheme.
A failing local check is still `1`, because a named broken check is the more actionable fact.
A local report with nothing failing is `3`, which is exactly what `3` has always meant.
**A degraded report therefore never exits `0`**, and a script gating on `mux doctor` keeps working unchanged.

Verified on this host: `mux doctor --url http://127.0.0.1:59999` against nothing exits `3` and prints the local report; `mux doctor` against a live daemon with one degraded background loop exits `1`.

### `mux doctor --export`

The full diagnostics bundle as JSON: the sanitized config through `public_dict()`, remote-connection state, firewall status, network counters, the fleet status-health aggregate, the status-timeline and session-recovery sink stats, any cold sessions, and the tails of `daemon.log` and `redeploy.log`.
It is always JSON, because it is an artifact to copy rather than a table to read.

**It has no local form**, because every one of its sections is daemon state.
An unreachable daemon fails with exit `3` and a message pointing at the command that does answer.
Terminal bytes are never included, for the same reason scrollback is not: they are whatever the child printed.

---

## Recovery

### The daemon will not start

Run `mux doctor`.
With nothing listening it produces the local report, and the first `FAIL` is the one to fix.
Each failing row carries a remedy line, so the next step is not a documentation hunt.

The four causes that account for most of these, and what the report says about each:

- **A config that does not validate.** `install.config` fails with the parse or validation error. Fix `config.toml`, or move it aside; a removed config is rewritten with defaults on the next start.
- **The port is already held.** `install.port` fails and names the owner-finding command for this host (`netstat -ano | findstr :<port>` on Windows, `ss -ltnp` on Linux). Stop the owner, or set a different port. If the report targeted a different URL than this machine's configured one, the row says so explicitly and declines to blame the local port.
- **A broken install.** `install.imports` fails with the real exception. On Windows the specific risk is `pywinpty`, the one compiled dependency in the runtime closure, where a wheel/ABI mismatch or a missing VC++ runtime surfaces as an `ImportError` that `install.pty` names.
- **An unwritable or missing data directory.** `install.data_dir` distinguishes "exists and cannot be written" from "does not exist and cannot be created", and points at `MUX_DATA_DIR`.

`muxd --local-only` starts without the direct Tailscale listener, which removes tailnet detection from the startup path when you are isolating a network problem.
`muxd --port` and `muxd --host` override the configured listener for one run; `--host` must be a loopback address, because the Tailscale listener is detected rather than configured.

A bound listener is not a ready daemon.
`/api/health` answers 503 with the startup phase in flight until the runtime exists, so "it is listening but everything 503s" is the daemon still building, not a fault.

### Sessions look lost after a restart

Three different mechanisms are involved, and which one applies decides what you can get back.

**The PTY supervisor is the primary path, and it ships off.**
`pty_supervisor_enabled` defaults to `False`.
With it on, PTYs are spawned in an out-of-process supervisor and survive a daemon restart, an app rebuild, and a redeploy.
With it off, in-process spawning is the fallback and a restart reaps every session, which is why `POST /api/daemon/restart` refuses with HTTP 409 unless the caller passes `force: true`.

**"Attached" is not the same as "connected".**
The restart precondition accepts a supervisor that is alive but whose socket is currently down (`client.lost`), because in that state the sessions are running and adoptable, and a restart is precisely the recovery.
Gating on `connected` alone refused the recovery it should have allowed.

**Cold recovery covers what the supervisor cannot.**
`session_recovery_enabled` defaults to `True` and is independent of the supervisor.
A durable registry row is written on the spawn registration task, before any history write, with an *open* marker; a clean shutdown closes it and a crash closes nothing, which is the whole signal.
Sessions whose daemon and PTY owner both died come back as visible, dead, resumable rows rather than vanishing.
`session_recovery_checkpoint_bytes` set to `0` keeps the registry, which is the part that brings sessions back, and stores no terminal bytes.

Check the state before concluding anything: `mux doctor` against a running daemon reports supervisor attachment as a `daemon` row, and `mux doctor --export` lists cold sessions with their reason and capture state.

### The UI is blank on a fresh clone

This is the most confusing first-run symptom in the project and it is not a bug.

`src/swe_mux/static/` is build output and is gitignored.
A fresh clone, a fresh worktree, and a CI checkout all have none, so the daemon answers the API perfectly and serves no interface.

```
npm --prefix frontend ci
npm --prefix frontend run build
```

`mux doctor` distinguishes the two cases that look identical from the browser.
In a source checkout, `install.frontend` is `warn` and carries exactly that command, because a missing bundle there is normal and one command away.
In an installed copy, the same absence is `fail`, because a wheel that ships without a UI is a packaging fault.
The distinction is drawn by `_source_checkout_root()`, which looks for `frontend/package.json` two directories above the package.

A present `index.html` with no build identity is reported `warn` rather than `ok`, because it may be a stale or hand-made file rather than a production build.

### A wheel that shipped without a frontend

Hatchling's `artifacts = ["src/swe_mux/static/**", ...]` includes those files only when they happen to exist on disk at build time.
A wheel built from a clean clone therefore contains no UI at all, builds cleanly, uploads cleanly, and serves a blank page to every user.

`packaging/verify_release_artifact.py` is the gate, and `ci.yml` runs it on every push rather than only at release:

```
uv run python packaging/verify_release_artifact.py dist/swe_mux-*.whl
uv run python packaging/verify_release_artifact.py --json <wheel>
```

Exit 0 when every check passes, 1 when any fails, including a wheel that cannot be opened at all.

The load-bearing check is not presence but **consistency**: every `assets/...` filename the wheel's own `index.html` references must be a file the wheel contains.
Vite's content-hashed names make that exact, so a stale `index.html` beside fresh assets, or the reverse, cannot satisfy it.
The join runs in one direction only; unreferenced assets are normal, because every dynamically imported route is its own chunk reached from the entry rather than from the HTML, and are reported as a count rather than as a failure.

`packaging/install_smoke.py` is the companion and answers the questions a reader of the zip cannot:

```
uv run python packaging/install_smoke.py dist/swe_mux-*.whl
```

It installs into a throwaway virtualenv created outside the checkout, with `PYTHONPATH`, `PYTHONHOME`, and `VIRTUAL_ENV` stripped, then asks the installed copy whether `mux` and `muxd` run, whether `swe_mux` imports, and whether the packaged UI is reachable from `swe_mux.__file__`.
The isolation is the point and is not trusted: `import-isolation` reads the imported package's `__file__` back out of the child and fails unless it resolves inside the virtualenv, because a checkout satisfies every other check by itself.
It starts no daemon and binds no port.

If a published wheel reaches you in this state, `mux doctor`'s `install.frontend` row says so from the copy already on your machine, and reinstalling from a complete artifact is the fix.

---

## Backup

### What to copy

Stop the daemon first, or accept that SQLite's WAL files are part of the copy.
A data directory copied while a daemon is running leaves a WAL the restoring host has to recover, which is one of the two ways to end up with a `mux.db` that will not open.

Copy, from the data directory:

- `mux.db`, with its `-wal` and `-shm` siblings if present. This is history, transcript indexing, telemetry, the status timeline, Tier 0 facts, the prompt queue, schedules, clipboard history, the code graph, and the durable session registry.
- `land-queue.sqlite3`, with its `-wal` and `-shm` siblings.
- `config.toml`, `settings.json`, `keybindings.json`, `rules.toml`, `hooks.toml`.
- `prompts/`, `notes/`, `media/`, `agent-context-backups/`.
- `prompt-library-state.json`, `previews.json`, `project-action-trust.json`, `push-subscriptions.json`.
- `provider-accounts/`, `provider-accounts.json`, `push-vapid.pem`, `automation.secrets.json`. These are credentials; treat the backup as a secret.

Project *content* is not in here and is not swe-mux's to back up: Projects point at directories you already own, and `.swe-mux/` inside a checkout is per-machine state that the repository does not carry.

### What is safely disposable

- Every log: `daemon.log*`, `access.log*`, `crash.log`, `desktop-daemon.log*`, `daemon-relaunch.log`, `supervisor.log`, `supervisor-console.log`, `redeploy.log`, `lifecycle.log`, `git-provenance-backfill.log`.
- Everything regenerated per run: `bin/`, `<harness>-hooks.json`, `<harness>-mcp.json`, `sessions/`, `omp-extensions/`, `opencode-configs/`, `supervisor.json`, `daemon-heartbeat.json`, `desktop-control.token`, `redeploy.lock`, `redeploy-result.json`, `hook-spool/`, `tls/`, `usage-cache.json`, `webview/`.
- Re-downloadable assets: `voice-models/`.
- Bounded caches whose loss costs only history you can regenerate: `voice/` (clips, capped by `tts_cache_mb`), `preview-shots/`, `recovery/` (post-mortem terminal bytes; the registry rows that bring sessions back live in `mux.db`, not here).

`worktrees/` is neither: those are real git working trees with real commits, and they are only inside the data directory because `worktree_root` is unset.

### What does not travel between machines

- **`automation.secrets.json` on Windows.** The values are current-user DPAPI blobs, bound to the Windows account that wrote them. A data directory copied from another machine or another account produces a decryption failure, not corruption. Re-enter the keys on the new host.
- **`push-vapid.pem` paired with subscriptions from a different key.** An unreadable PEM is regenerated and every existing subscription is dropped, so restore the pair or neither.
- **`tls/`.** Tailscale certificate material is hostname-bound.
- **Absolute paths in config**, with one half now handled and the other half not, and the line between them is the *host* rather than the machine.
  A stored value shaped for a **different host** is re-derived on the next load and written back: `shell_exe`, `harness_exe`, `shell_profiles`, `data_dir`, `worktree_root`, `new_project_parent`, `startup_cwd`, `tts_edge_python`, `pinned_directories`, and the `ccusage` commands.
  So a `config.toml` written on Windows and loaded on Linux no longer launches `claude.exe`, and one written on Linux and loaded on Windows no longer refuses to load at all over a `worktree_root` that Windows does not read as absolute.
  The rule is *shape*, never whether a path exists, so a directory that is simply missing today is never rewritten - and neither is a deliberate override this host could run (`claude.cmd` on Windows, `/usr/local/bin/claude` on Linux).
  What that leaves is a **same-host** move: `D:\PROJECTS` restored onto another Windows machine is a valid Windows path to nothing, and nothing can tell it from a correct one. Fix those by hand in Settings.
  Project roots are not in `config.toml` at all - they live in `mux.db` and none of this reaches them.

---

## Relates to

- `README.md` — the product-facing quickstart and platform statement.
- `RELEASING.md` — the release procedure, including the TestPyPI validation that must precede a PyPI publish.
- `RELEASE_MANUAL_TASKS.md` — the operator acts that are deliberately not automated. § 6 (PyPI name registration and Trusted Publishing) is done as of 2026-08-28; § 8, clean-machine validation, is not.
- `SECURITY.md` — the trust boundary: a local daemon on loopback and optionally a tailnet, where any admitted device holds code-execution authority.
- `CROSS_PLATFORM_FINDINGS.md` — what each platform claim rests on.
- `design/features/session-recovery.md` — cold and inactive sessions in full.
- `design/features/desktop-shell.md` — packaging, the WebView, the tray, and login startup.
- `development/archive/SESSION_PRESERVING_RELOAD.md` — the PTY supervisor design and the exact reload workflows.
- `technical/backend/packages/daemon-runtime.md` — what a `daemon.log` line carries and how a request is correlated across it.
- `STATUS_INCIDENT_RUNBOOK.md`, `TERMINAL_INPUT_INCIDENT_RUNBOOK.md`, `PERFORMANCE_RUNBOOK.md` — the per-subsystem investigation procedures this document does not replace.
