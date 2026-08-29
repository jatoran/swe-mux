# Changelog

All notable changes to swe-mux are recorded here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project
follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
While the major version is `0`, a minor bump may carry a breaking change; the entry says so
explicitly when it does.

Entries describe what changed for someone running swe-mux, not how it was built.
Internal refactors, test changes, and documentation edits are omitted unless they change
observable behaviour, a supported platform, or a distributed artifact.

The release procedure that maintains this file is [`RELEASING.md`](RELEASING.md).

## [Unreleased]

## [0.1.2] - 2026-08-28

0.1.1 published to PyPI but produced no desktop artifact and no GitHub Release, so an
installed copy was still told 0.1.0 was the newest version.
This release carries everything 0.1.1 did, and the artifacts it could not build.

### Added

- The **Windows installer** and the portable desktop archive, which 0.1.1 intended to publish
  and did not.
  See the 0.1.1 notes below for what they are.
- The website's download section, filled from the release manifest rather than by hand, so it
  names the artifacts a release actually carries.

### Fixed

- **The Windows installer did not build.**
  Inno Setup resolves a relative source path against the installer script's own directory
  rather than against the working directory it is compiled from, so the build looked for the
  application bundle inside `packaging/installer/` and found nothing.
  Every earlier step of the release had succeeded, which is why 0.1.1 reached PyPI without it.
- The site now ships real screenshots, taken in a synthetic installation with invented
  projects and no personal data, rather than generated placeholders.

## [0.1.1] - 2026-08-28

A repair release.
`swe-mux[voice-local]` could not be installed from PyPI at all in 0.1.0, and a configuration
file carried between two hosts left the daemon unable to launch an agent.

This release also adds the first Windows installer, so swe-mux can be installed without Python.

### Added

- **A Windows installer** (`swe-mux-0.1.1-windows-x64-setup.exe`), attached to this release.
  It installs the application and its PTY supervisor, creates a Start Menu entry, and offers a
  desktop shortcut and a run-at-login task.
  It is **not code signed**, so Windows SmartScreen warns on first run; signing is planned.
  The portable archive is published alongside it for anyone who would rather not run an
  installer.
- `mux install-shortcut`, which creates Start Menu and desktop shortcuts for an install that
  came from `uv tool`, `pipx`, or `pip`.
  No Python packaging mechanism can create a shortcut at install time, so this is the
  equivalent for those installs.
- `mux doctor` now reports how swe-mux was installed, which directory its commands are in, and
  whether that directory is on `PATH`.
- `python -m swe_mux --where` answers the same question with nothing but an interpreter, for
  the case where the commands are not reachable by name.
- `muxd` prints a one-time hint at startup when its own commands are not on `PATH`, naming the
  directory and the command that fixes it.
- A Help surface: a modal reachable from the command palette and by voice, from which the
  guided tour can be reopened.
- Support for `.tar.gz` desktop bundles, which the updater already expected on macOS and Linux.

### Fixed

- **`pip install "swe-mux[voice-local]"` failed for everyone.**
  The published wheel required `en-core-web-sm`, which is on no package index, so both pip and
  uv refused the extra outright.
  The model is now acquired at first use and verified against a pinned hash, and the extra
  installs.
- **A `config.toml` written on one host and loaded on another kept values the new host cannot
  use.**
  A file written on Windows and loaded on Linux launched `claude.exe` and `codex.exe`, so the
  Run menu could not start an agent while typing `claude` in a shell worked.
  In the other direction a POSIX `worktree_root` made the daemon refuse to load its own
  configuration.
  Ten settings are now re-derived when their stored value is shaped for a different host.
  A deliberate override the host can run, such as `claude.cmd` on Windows, is left alone.
- **A refused executable reported the wrong reason.**
  Under WSL the Windows agent CLIs are reachable through interop, and swe-mux refuses them
  because such a session writes its transcript where no Linux path points and joins no Linux
  process group.
  That refusal reported "no such file or directory" rather than naming the binary it found and
  why it was rejected.
- Provider login and harness launch failures now reach `daemon.log`, with the configured value
  and the resolution that failed.
- The recovery that retries a configured `codex.exe` as `codex` ran only on Windows, where an
  `.exe` suffix is at least plausible, and not on POSIX, where it is certainly wrong.
- `cryptography` and `py-vapid` are imported when the daemon starts and were not declared as
  dependencies; they arrived only by way of another package's requirements.
- The desktop application opened a console window behind its native window.
  It is now a GUI entry point, and startup failures are reported in a dialog and written to
  `desktop-shell.log` rather than to a console that no longer exists.

### Changed

- The default theme is now Tokyo Night, and the default sidebar session row shows more at a
  glance.
  An existing installation keeps whatever it already had; neither default is applied to a
  configuration that has been written before.
- The wheel no longer ships precompressed copies of the frontend bundle, which were duplicating
  content the wheel already compresses.
  They are regenerated once on first start, which takes under a second and makes the download
  about a third smaller.

## [0.1.0] - 2026-08-28

First public release.

swe-mux is a local daemon that owns long-lived pseudoterminals for coding-agent CLIs and
shells, and presents them in a browser.
Everything runs on the operator's own machine; the trust boundary is stated in
[`SECURITY.md`](SECURITY.md).

Windows is the proving platform and the only one with a frozen desktop application.
Linux runs the daemon and the test suite from source.
macOS is implemented and typechecked but has never been executed.

### Added

#### Session ownership and terminals

- `muxd`, an aiohttp daemon that owns every pseudoterminal, so closing or reloading the browser
  never stops a session.
- A separate PTY supervisor process that holds the terminals, so live sessions survive a daemon
  restart, a backend reload, and a rebuild of the desktop application.
- ConPTY on Windows and the stdlib `pty` module on POSIX behind one platform seam, with
  Win32 Job Object process ownership and its POSIX process-group equivalent.
- Crash recovery for sessions the supervisor could not keep alive: a durable session registry,
  terminal checkpoints, and cold sessions that stay readable after their process ends.
- Multi-device attach for one session, with exactly one connection permitted to write to the
  PTY and one arbitrated terminal size shared across devices.
- Terminal-aware bracketed paste, copy/paste, find, clipboard capture, and a bounded
  clipboard-history ring that refuses secret-shaped copies.

#### Projects and workspace

- Explicit Projects that bind sessions, layouts, notes, history, and file browsing to a
  canonical folder, plus optional Groups for sidebar organization.
- A mixed-view workspace of panes, tabs, splits, and drag/drop, with desktop split geometry as
  durable Project state and a single-pane projection for mobile.
- Project-owned notes, a file browser with editors, ignore rules, and leased non-recursive
  watches.
- A reusable prompt library whose templates are inert text: selecting one inserts, and never
  submits.
- Trusted task discovery and a per-Project Run menu over VS Code tasks, root package scripts,
  and `.swe-mux/actions.toml`; every task file stays inert until its exact current contents are
  reviewed and approved, and any edit revokes that approval.
- Process and preview registration for Project-local listeners, with HTTP, WebSocket, and HMR
  traffic bridged through the daemon's own URL, and static document previews served from the
  checkout under a sandbox CSP.

#### Agent sessions

- A harness registry with capability descriptors and adapter families, covering Claude Code,
  Codex CLI, and plain shells.
- Automatic promotion of a nested agent started inside an ordinary terminal, through mux-local
  `claude.cmd` and `codex.cmd` shims that preserve the normal CLI invocation.
- Session status detection with a durable transition ledger, a state watchdog, awaiting
  sub-reasons, and a golden detection corpus.
- A fail-closed delivery-readiness contract (`safe` / `blocked(reason)` / `unknown`) that never
  authorizes automatic terminal input on `unknown`, and never reads child-agent completion as
  root-agent readiness.
- Control-plane approvals driven by the harness's structured permission request rather than the
  terminal screen, with a floor that no configuration can reach past.
- Managed provider accounts: save, relabel, reauthenticate, switch, and remove Claude and Codex
  logins, with subscription-window polling. Only authentication is copied, and switching is
  always an explicit act.
- A read-only History browser that reconciles native Claude and Codex transcript directories at
  startup without moving or deleting the originals, and reads a Claude transcript as the
  branching DAG it is.
- Launch profiles for shells and agents, and a WSL agent bridge with an explicit reachability
  probe.

#### Fleet control plane

- Tier 0 deterministic fact capture with source pointers and fingerprints, scoped by a run
  boundary that survives an in-CLI conversation replacement.
- Model-free detectors for loops and stalls, declared-versus-verified claims, documentation
  debt, and provenance.
- A tree-sitter code-structure graph backing blast-radius, navigation, context, and test-gap
  reads.
- An opt-in scan timeline with per-run grants, budgets, source rehydration, and dead-end
  extraction.
- Attention ranking with an interrupt budget, four in-app delivery channels, breakpoint
  detection, and an absence digest.
- Automation observers that can capture and report but cannot type, approve, spawn, execute
  scripts, or mutate a Project, governed by a per-Project enablement dependency graph and an
  install-wide ceiling.
- An automation dashboard with policy, usage, and activity tabs as the single editor that may
  turn an automation off in either scope.
- A prompt queue with head-of-line ordering, stranding, and seed staging for new sessions;
  gated auto-delivery with a stability window, quiet hours, an emergency pause, and a
  consecutive-send cap.
- Agent-to-agent messages and a fleet queue, where a non-human sender's write ends at a human
  unless the receiver granted it or itself solicited it.
- Scheduled runs (cron, interval, one-off) that go through the ordinary spawn, resume, and queue
  paths and grow no second authority.
- A land queue that serializes branch landing: reconcile, then a verification gate whose exact
  bytes a human approved, then a fast-forward-only merge, returning conflicts and failures to
  the branch's own agent.

#### Agent-facing MCP surface

- A per-session MCP server exposing reads over the fleet: sibling sessions and their status,
  paged transcripts, archived conversation search, Project notes, Agent Context sources, scan
  timeline and search, provenance, verified status, prior resolutions, and dead ends.
- Bounded writes only: staging a message into another session's prompt queue, drafting a spawn
  request for human approval, arming a session-settle watch, and interrupting or ending a
  session behind a per-Project grant.

#### Git

- Git status, comparison, diff review, first-time repository initialization, a commit graph, and
  a provenance ledger that separates who authored a change from who landed it.
- Worktree creation and removal, including a background purge that never appears in
  `git worktree list` and never raises a checkout's dirty count.

#### Voice and assistant

- Read aloud, from a summarized or verbatim slice of the last turn, through the OS voice engine,
  a local Kokoro model, or an explicitly acknowledged external Edge TTS provider that is never
  bundled.
- Hands-free conversation: browser capture through an AudioWorklet, Silero VAD, a frame-counted
  endpoint gate, and faster-whisper transcription with configurable wake words and commands.
- The Mux assistant, where the model proposes names, deterministic code resolves and executes
  through existing paths, and the confirmation floor for a consequential action is not
  configurable.

#### Remote access and desktop

- A loopback listener plus an optional direct Tailscale listener carrying the same UI and API,
  with automatic Tailscale Serve on HTTPS 443 so a phone browser gets the secure context its
  microphone requires.
- Web push notifications with per-device preferences, and device presence that decides which
  device the operator is at once for the whole application.
- A Windows desktop shell: a WebView2 window, a system tray supervisor, login startup, and a
  frozen `onedir` bundle that can rebuild and redeploy itself while preserving live sessions.
- A progressive web app manifest and service worker for phone installation.
- A daily release check against a static `version.json`, which is the only request swe-mux
  makes on its own behalf: it carries nothing identifying the install, it downloads nothing,
  and `update_check_enabled` turns it off entirely.
- An updater for the frozen desktop app (`mux update --install <version>`,
  `POST /api/update/install`) that downloads a release only on an explicit act naming a
  version, verifies its SHA-256 against the published manifest before staging anything, and
  reuses the redeploy's staged swap so live sessions survive. It refuses, rather than
  installs, a release that would require a new PTY supervisor - that upgrade ends every live
  session and is an announced, deliberate act.

#### Operations

- `mux`, an HTTP CLI with stable human and JSON output, and `mux doctor` for local
  configuration, integration, ownership, tailnet, provider, telemetry, automation, and queue
  problems.
- A copyable diagnostics bundle (sanitized config, remote state, firewall status, network
  counters, status-health aggregate, log tails) that contains no terminal bytes and no message
  content.
- Durable operational telemetry for process ownership, quota samples and reset detection,
  compaction, and tool evidence, all recorded as observations with confidence rather than as
  authoritative facts.
- Usage analytics that never sum agent spend, metered automation spend, and provider quota into
  one figure, and spending budgets denominated in tokens or dollars with an honest floor when
  cost is unmeasurable.
- Traffic accounting, response compression, static precompression, and conditional Git reads.

#### Licensing and governance

- Apache-2.0 with `NOTICE` and `TRADEMARK.md`, declared in package metadata as a PEP 639
  license expression and carried into the wheel.
- Contributions under a DCO sign-off rather than a CLA.
- A generated `THIRD-PARTY-NOTICES.md` and a two-half license gate: a metadata check over the
  resolved dependency closure that runs in the test suite, and a payload check over the built
  desktop bundle. No GPL or AGPL code ships; the two LGPL libraries ship as replaceable source.

[Unreleased]: https://github.com/jatoran/swe-mux/compare/v0.1.2...HEAD
[0.1.2]: https://github.com/jatoran/swe-mux/releases/tag/v0.1.2
[0.1.1]: https://github.com/jatoran/swe-mux/releases/tag/v0.1.1
[0.1.0]: https://github.com/jatoran/swe-mux/releases/tag/v0.1.0
