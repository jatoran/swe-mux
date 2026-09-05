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

## [0.2.3] - 2026-09-05

### Added

- **A resumable setup experience, shared across browser, desktop, and phone.**
  Setup now covers the experience tier, model provider, harnesses, existing accounts, project folders, and desktop integration before offering the UI tour.
  Continue later preserves progress in Getting started, a collapsible sidebar section above Usage that also holds optional first steps, documentation, and the live demo.
  Individual steps can be dismissed and restored, and Help can bring back the whole section.
- **Guided model configuration before enabling Automations.**
  Choose OpenRouter or a compatible local or hosted endpoint, store any required key, review cheap, standard, timeline, and assistant models, and set spending limits inside setup.
  Endpoint and model-role checks must succeed before activation; local servers can use one model without an API key.
  Deferring this step explicitly continues with Deterministic and leaves model setup available for later.
- **Project suggestions from native harness history and capture of existing logins.**
  Select recently active project folders from a checklist, with duplicate paths and worktrees grouped and unavailable folders identified, or browse for a folder manually.
  Choose the default harness for new sessions and save an already-signed-in supported account for usage and quota tracking without signing in again.
- **A choice to reuse retained settings or start with fresh global preferences.**
  Fresh setup backs up preferences before resetting them and preserves Projects, repository files, history, accounts, credentials, and connection addresses.
  Help and `swemux setup --restart` reopen this choice; `swemuxd --new-user-profile NAME` provides separate data and a separate local port for first-use testing.

### Changed

- **Experience tiers now set the global automation defaults Projects inherit, as well as the master switches.**
  Automations includes the Deterministic defaults, while explicit Project choices and unrelated global customizations remain respected.
  Setup and Settings show the defaults and their changes before applying them.
- **Desktop and phone integration are guided setup steps.**
  Desktop setup checks actual Start Menu, desktop, and sign-in registrations and offers the missing choices without a competing native prompt.
  The phone guide includes private access, HTTPS setup, connection refresh, and a QR code.
  Worktrees move to optional advanced exploration, while adding a Project and launching a first session are first steps.
- Installation instructions and the startup banner now explain how to launch the browser or desktop interface, add shortcuts, and return to setup.

### Fixed

- Initial configuration failures retry instead of silently hiding setup until another setting changes.
- First-step cards no longer appear twice or occupy the empty workspace behind setup.
- The UI tour follows setup, preserves its current step when deferred, and shares progress across clients instead of unexpectedly restarting in the desktop WebView.
- Model-dependent project options and grant controls require completed provider and model setup, and refresh in place after successful verification.


## [0.2.2] - 2026-09-05

### Added

- **A canonical activity ledger, and the fleet's own telemetry feeding it.**
  Tool calls, turns, runs, model requests, compactions, skill activations, and test outcomes are reduced into one provenance-preserving ledger under `<data_dir>/telemetry/` - monthly segments, content-free evidence (hashes, sizes, locators, never tool output), field-level source precedence with every contributing observation linked, exact closed-day rollups, and additive versioned migrations so a data directory written by an older build is upgraded on open rather than failing on the first write.
  Legacy telemetry and native transcripts are imported non-destructively and kept; nothing is deleted.
  `canonical_telemetry_native_otel_enabled` (Settings → Usage) hands each new Claude Code and Codex session an OTLP exporter pointed at the daemon over authenticated loopback; the contracts were measured against Claude Code 2.1.259 and Codex CLI 0.153.0, identity attributes are dropped and content attributes hashed before anything is stored, and every provider event name is counted per harness version so a renamed attribute shows as drift instead of silence.
  Resources → Fleet activity reads the ledger: exact totals over the selected window, cohort, backend, and layer; a tool-call audit down to its evidence; a collection-health readout with per-backend field coverage and parser signatures; deterministic inefficiency candidates with their denominators; and JSONL/CSV exports (`GET /api/telemetry/v2/export/{kind}`) that carry evidence identifiers and source locators.
  Schemas 3 and 4 (2026-09-03) finished the roadmap: every call carries its evidence quality and the approval wait it paired from request to resolution; a run's start says whether it was declared or estimated; closed hours are rolled up beside closed days so a 24-hour window is exact; skills, verifications, and compactions have daily rollups; Claude, Codex, OMP, Pi, and OpenCode native stores are reconciled straight into the ledger every five minutes and per run on demand (`POST /api/telemetry/v2/reconcile`); Codex's own metrics (`codex.tool.call` and its siblings) are exported to the daemon and compared per run against the ledger's count; a `codex.sandbox_outcome` verdict is a denied call whose cause stays named; and every displayed total names its range, cohort, denominator, and coverage.
  The view gained Project, model, family, outcome, and evidence-quality controls, an aggregate → calls → run → turn → evidence drill-down, skill and verification tabs, cohort comparison that refuses to compare cohorts differing on a dimension the split does not name, review buttons whose verdict is the only feedback a finding collects, and a legacy tab with a shadow comparison against the old `tool_events` table (`canonical_telemetry_legacy_dashboard_enabled`, on until an operator turns it off).
  Measured on 2026-09-03 at ten million calls, which is what forced schema 4: the quality readout is rolled up like the tool and workload figures, a page's exact count is summed from rollups, the repeated-call finding reads per-run counts the write path keeps, and a dimension filter is kept on the time index; every dashboard view answers from rollups under the 200 ms gate and every detail page under 500 ms, ingestion adds no measurable event-loop lag at two thousand observations a second, and a 24-hour live window audits call by call against the providers' own records (`tools/telemetry_audit_window.py`).

- **A stalled daemon now says where it was stuck, and the fleet no longer outranks it.**
  When the event loop stops for three seconds or more, every thread's stack is dumped to `<data_dir>/loop-stalls.log` from a thread that needs no GIL, and the stall is explained once in `daemon.log`, kept in `mux.db`, and shown under `stall_watchdog` on `/api/diagnostics/background` - including whether a canary thread was starved too, which separates synchronous work on the loop from a native call holding the GIL.
  Session process trees run below normal priority and the daemon runs above it (`session_process_priority`, `daemon_process_priority`), so a wave of concurrent builds slows the builds rather than the person at the keyboard.
- **Reading a Project's config file no longer blocks the daemon.**
  Every Projects poll read each Project's `.swe-mux/config.toml` on the event loop; the stall watchdog caught that read blocking for 6.6 s on a disk saturated by concurrent builds, and it now runs in a thread.
- **Daemon subprocesses are spawned off the event loop, and the health endpoint no longer stats the served frontend on every poll.**
  asyncio starts a child synchronously on the loop that asks, and the stall watchdog caught that call holding the daemon for 23.5 s while a build saturated the disk; every helper the daemon runs - git queries and mutations, Tailscale, the firewall check, hook commands - now runs on a spawn loop on another thread, with callbacks, request context, and cancellation preserved, and commands a person is waiting on take a lane of their own so a poller's stuck spawn cannot hold them.
  A git query whose output exceeds the daemon's cap is refused rather than shown clipped.
  The served frontend's identity is answered from the last reading for a few seconds instead of a stat per request.
- **The UI says when the daemon is not answering.**
  A slim banner appears after two missed health probes, counts the seconds, and clears on the first answer, so a stalled daemon reads as stalled rather than as a crashed app.

### Fixed

- **An upstream rate limit no longer reads as a broken schema, and is retried.**
  OpenRouter answers an upstream refusal with HTTP 200 and a body carrying only an error, so the call failed as "structured response must be an object" and - because 200 is in no retry set - was never retried, leaving a ledger row with zero tokens and no provider.
  The embedded status is now adopted whenever it names a real one, and the call takes the same bounded equal-jitter backoff any honest 429 would have; a body that answered and merely annotated the answer with an error is still an answer, and an unrecognisable code is handed to the caller's own ladder rather than guessed at.
  Measured over two days before the fix: 30 of 79 session-title calls lost this way, and one session that never got a title at all.
  The same frame on the streaming path used to leave the assistant answering a rate limit with silence; it is now raised, and retried only while nothing has been spoken.

## [0.2.1] - 2026-09-01

### Added

- **swemux.dev now carries an interactive demo of the real app.**
  The landing page runs the unmodified frontend against an in-page fake daemon, on a desktop frame and a phone frame that mirror one fleet, with a guided walkthrough and seven further scenarios - the prompt queue, orchestration, previews, landing a branch, the command palette, keymap presets, and the assistant - that label the parts of the screen they are talking about.
  Nothing in it listens, speaks, or reaches a real daemon.
- **Automations now have an install-wide default that new and existing Projects inherit.**
  The Automation → Policy matrix's Global cell gained a `default` checkbox beside the existing "off everywhere" lock, with a line under it saying how many Projects that default actually reaches ("12 inherit · 3 custom") before you click it.
  A Project that never wrote an automation down follows the default and keeps following it as you change your mind; a Project that wrote one still wins.
  The per-Project control gained a third position, `Follow global`, so a Project can go back to inheriting instead of being pinned to whatever it was set to once.
  `scan_timeline_auto_enable` - whether a new conversation arms the timeline by itself - inherits the same way.

- **The plugin marketplace now has one validated public catalog and a real website.**
  `/plugins/` and the in-app browser share exact-commit manifests discovered from the `swe-mux-plugin` GitHub topic, with explicit official/community labeling, release tags, permissions, platforms, runtimes, licenses, and install commands.
  The catalog executes no plugin source, excludes invalid repositories, refreshes through the site deploy workflow, and falls back to the unreviewed live GitHub topic inside the app if swemux.dev is unavailable.
- **Managed plugins retain their release channel.**
  Install and update now store the requested channel or ref, the selected tag or branch, and the resolved commit separately.
  `--ref latest` follows the newest GitHub release on each explicit update, while a literal tag remains pinned.

### Changed

- **Add project inherits your automation defaults instead of asking you to choose again.**
  The three starting-set checkboxes are now one summary line - what the new Project will run, and that it came from the install - which expands to a per-automation panel for changing individual rows for that Project only.
  Only a row that actually disagrees with what is inherited is written into the Project, so a form you never expand writes nothing and the Project keeps following your defaults.
  Where the install has no opinion at all the free analysis set is still pre-ticked, so a fresh install's first Project is not empty; once you have set a default either way, the form stops second-guessing it.
  The model-backed and agent-autonomy sets stay explicit checkboxes: one can bill and the other hands agents real authority.
- **"Adaptive session title" is now "Re-title on scope change".**
  It re-titles a session when its scope changes, and it is not what names a session in the first place - that is the Session titler, an install-wide switch on the Automation dashboard that runs whatever a Project opted into.
  Both were described as "session titles", which made declining the model-backed automations look like declining session titles.
- **The Files tab now opens files into itself, as tabs, instead of into a workspace pane.**
  A second rail below `File Explorer | Recent` holds what you have open; a plain click on a tree row, a search hit, or a Recent row lands there, and the side panel stays open over the session you were reading.
  A file moves into a pane on request - `⇥` beside the rail, `Open in a pane` on its tab or on its row in the tree, ctrl/cmd-click on a row, or the row drag that already worked - and it lives in exactly one of the two places at a time.
  This also fixes a real cross-device fault: opening a file used to insert a tab into the Project layout, which is shared, so browsing files on a phone permanently rearranged the desktop's panes.
  Open files are remembered per Project on the device, capped at eight, evicted by least-recent use, never evicted while they hold unsaved edits, and marked and guarded when they do.
- **Plugins is now a first-class app-menu destination.**
  The row opens plugin management directly, while Project-scoped plugin tools remain in each Project's Run menu.
- **Plugin marketplace selection now fills an immutable release tag, and manual installation exposes an optional ref field.**
  Expanded plugin details show the selected channel and resolved revision, while acquisition, approval, and enablement remain separate.
- **The account switcher's quota rows are narrower.**
  Each account's `5h`, weekly, and Fable figures sit closer together, divided by a hairline instead of a bullet in a wide gap, with a small breath between a percentage and its reset time, which there was none of before.
  Percentages still line up across stacked accounts.
- **The default desktop command rail leads with what a terminal has no key for.**
  Approve-once sat fourth, spending a visible slot on a control that is inert except while a turn is asking; the front of the row is now attach, both clipboard directions, the code fence, the resume command, Branch, clipboard history, and the skills picker.
  Copy-input renders its label rather than an icon that shared a silhouette with Copy reply.
  Your own rail configuration is untouched.

### Fixed

- **`swemux plugin validate` is now genuinely local and works in CI without a running daemon.**
  It calls the same canonical parser used by the daemon and still executes no plugin code.
- **A linked plugin whose manifest identity changed can still be uninstalled.**
  Uninstall no longer reloads a broken or renamed manifest merely to disable and unregister its existing record.
- **The desktop app is no longer reported as one of its own sessions' orphaned processes.**
  Rebuilding and relaunching the app from inside a swe-mux session left the desktop window attributed to that session; when the session ended, the live UI appeared in the process fleet as a suspected orphan with Terminate offered on it.
  swe-mux now recognizes its own shell, daemon and PTY supervisor by identity rather than by descent, and the terminate actions refuse a process that is swe-mux itself regardless of what the fleet believes owns it.
- **The "swe-mux runtime" row now reports swe-mux's whole footprint.**
  It counted only the daemon and its descendants, so the desktop window, its embedded browser, and the supervisor were reported as nothing at all.
- **Switching Claude accounts no longer tells you that your running sessions kept the old login.**
  The account switcher put the same sentence under every non-selected account's session count: "Switching is not retroactive - a session keeps the login it started with until it is restarted."
  That is true of Codex, whose CLI reads its login once at startup, and false of Claude Code, which re-reads its credential file when the file changes and sends its next request as the new account.
  The daemon now declares per provider whether a switch reaches sessions already running, and the notice says which: a muted "started under X, spending the selected account now" for Claude Code, whose only stale surface is its own `/status` line, and the amber "keeps spending X until restarted" for Codex.
  The Settings disclosure says the same, and the `selected` audit entry records which of the two the live sessions got.

## [0.2.0] - 2026-08-31

### Added

- **`swemux agent` - the mux fleet tools over a second transport.** Inside any swe-mux
  pane, `swemux agent tools` lists exactly the tools that session may call and
  `swemux agent call <tool> key=value ...` calls one (`key:=value` for JSON-typed values,
  `--input` for a whole JSON object). It authenticates as the calling session with the
  credentials every pane already carries and speaks to the same endpoint the MCP client
  uses, so it inherits every authority check, budget, and provenance record - and it is a
  pure passthrough, so new tools appear in it with no CLI change. A new per-harness
  "Agent CLI" toggle sits beside the MCP one under Settings → Harnesses → Fleet access;
  with both capabilities off, that harness's sessions hold no fleet surface at all and the
  daemon refuses their tokens. Every agent pane now carries `MUX_SURFACES` naming what it
  holds, and the shipped skill teaches whichever surface is present. In exchange, the
  session-acting operator commands (`swemux send`, `kill`, `spawn`) now refuse requests
  from an agent session's pane and name the agent surface instead.
- **`await_session` - wait for another session inside one tool call.** The synchronous
  sibling of `watch_session`: it blocks until the target leaves working and holds a settled
  state, ends, or a bounded timeout elapses - and the timeout is a normal result carrying
  the current state, so a caller re-calls to keep waiting without ever outliving its own
  tool-call timeout. Nothing is staged or delivered; the answer is the call's return value.
- **`request_spawn` can now watch, place, and model its spawn in one call.** `watch:true`
  arms the settle watch you would otherwise arm separately (deferred through human approval
  on the draft path), `pane:"split_horizontal"|"split_vertical"` asks the first browser
  viewing the Project to open the new session in a split beside it, and an omitted `model`
  now genuinely takes the target Project's new `default_agent_models` table (Projects →
  Repository options, or `.swe-mux/config.toml`) before falling back to the CLI's own
  sticky default.
- **Monitoring agents now hear their answers without a human pumping the queue.** An open
  settle watch holds the watcher's auto-delivery grant open for the watch's own lifetime
  (previously any watch longer than the idle window lapsed the grant, and the notice
  arrived with nothing to deliver it), and any authenticated mux tool call now counts as
  the "somebody is reading this" evidence that resets the consecutive-send cap - so an
  orchestrator that reacts to notices by acting, rather than by writing replies, no longer
  goes silently deaf after three deliveries.
- **First-run setup asks the questions that used to wait for Settings.** The tier page now
  carries a live-previewed theme picker and a Customize fold-out with two new axes: an
  **agent autonomy** level (Supervised / Assisted / Autonomous - whether queued messages
  deliver themselves, and under how wide a set of caps) and one checkbox per switch the
  chosen tier sets, applied atomically with the tier through the same daemon-owned policy
  route (`POST /api/experience-tier` now takes `autonomy` and `overrides`;
  `GET /api/experience-tiers` serves the tables the panel draws). The agents page gains an
  install-wide **fleet access** choice - MCP + CLI, MCP only, CLI + skill, or none -
  answered once instead of per harness. And setup now ends on a **first steps** page: the
  same three-quest card the empty workspace shows, so finishing setup hands off into the
  guided voice, worktree, and phone setups rather than closing on a blank stage.
- **The command rail can be switched off per device class.** Settings → Appearance →
  Action rail: `rail_enabled_desktop` / `rail_enabled_mobile`, both on by default,
  hot-applied, and offered during first-run Customize. Turning one off hides the whole
  rail there (on mobile that includes the pinned Send button) while the configured layout
  is kept and comes back untouched.

- **The first launch of the Windows desktop shell offers to add itself to the Start Menu and
  to start when you sign in.** A wheel cannot create a shortcut and nothing runs after
  `pip`/`uv`, so `swemux install-shortcut` was a command nobody knew to run. Asked once per
  install whichever way you answer, never for the installer's own build, and never when a
  Start Menu entry already exists. It writes no desktop icon; that stays a choice in
  Settings.
- **`swemux start` runs the daemon in the background and returns once it is serving.** For
  the browser-only case, for Linux and macOS where there is no desktop app, and for
  iterating from a checkout: closing the terminal does not stop it, `swemuxd --shutdown`
  does, and a daemon that is already serving is reported and left alone. It is the only
  command that starts a daemon, and only when typed - `swemux ls` against a stopped daemon
  still says so.
- **A daemon started where you can see it opens the UI in your browser.** Gated on a
  terminal actually watching the process, which is what keeps it out of every start that
  should not do this: the tray's own daemon child, `swemux start`'s detached child, a
  restart successor, and a login task are all non-TTY and unaffected. `--no-browser` on
  either command, or `SWE_MUX_NO_BROWSER` for a caller that cannot pass a flag.

### Changed

- **The side panel leads with Notes and Files, and the default rails differ per device.**
  On a fresh install (or a device that never rearranged its tabs) the utility drawer's
  default order is now Notes, Files, then the session block - the two surfaces that are
  useful before a single session exists come first. The deterministic tier's default also
  puts the Activity tab away, since everything that feeds it is model-backed and off in
  that tier; choosing Automations brings it back. The Action rail's shipped layout is now
  a desktop row of mouse-verbs (copy surfaces, paste, approve, Markdown helpers, the
  pickers) and two mobile rows that keep the terminal keys, modifiers, and pads - lifted
  from a long-lived daily-driver configuration instead of one identical row on both.
  Existing arrangements and rail layouts are untouched; defaults only ever apply where
  nothing was stored.

- **`notify` now requires the `delivery` argument** (`"when_idle"` or `"now"`, and `"now"`
  requires a reason). With a silent default, senders never weighed whether the target
  should hear them before or after finishing its current turn; now the choice is made at
  every call. The message size cap also rose from 4,000 to 8,000 characters - a real task
  brief with acceptance criteria did not fit, and what got trimmed was the constraints.

- **On Windows, `swe-mux` now works on a plain `uv tool install swe-mux` or
  `pip install swe-mux`, with no terminal, no extra and no download.** `pystray` and
  `pywebview` moved out of the `desktop` extra and into ordinary dependencies, marked
  Windows-only. Previously every install got the `swe-mux` launcher - it is a GUI entry
  point, so it opens no console - while an install without the extra got a launcher whose
  only behaviour was to fail on a missing import, into a message box, with both suggested
  remedies leading back through a terminal. Over 2.4 MB of pure-Python packages. The
  `swe-mux[desktop]` spelling still resolves and now adds nothing, so existing scripts keep
  working.
- **The `mux` and `muxd` aliases are gone. The commands are `swemux`, `swemuxd` and
  `swe-mux`** - one per program, which is the floor. `mux` is shared with at least one
  unrelated tool in the same category, and on a machine with both, PATH order silently
  decides which one a typed `mux` runs. Shipping a launcher under that name is what creates
  the collision rather than what survives it; not occupying it leaves nothing to shadow.
  If a `swemux` ever is unreachable, `swemux doctor` and the daemon's own startup hint say
  so by name rather than leaving you to guess.
- **The Windows installer now ticks "Start swe-mux when I sign in" by default.** A
  multiplexer that has to be launched before it can watch anything is answering the wrong
  question at sign-in. It starts hidden in the tray and the tray menu turns it off in one
  click. The desktop-icon box stays unticked.
- **`swemux install-shortcut`'s run-at-login entry can finally be created from the UI.**
  Settings → General → Desktop integration lets you choose which of the three shortcuts to
  write - Start Menu, Desktop, Start with Windows - where before it always reported all
  three, could remove all three, and could only ever create two. The only way to turn on
  run-at-login was the tray menu, which a phone or any remote client cannot reach.
- **"Start with Windows" in the tray menu now reads both mechanisms it can be turned on
  with.** A run-at-login entry created by `swemux install-shortcut --startup` or by Settings
  is a `shell:startup` shortcut, and the menu item only consulted the registry - so it
  showed "off" beside a swe-mux that demonstrably did start at sign-in, and turning it "on"
  left two entries racing to launch the same app. Either one now counts as on, and turning
  it off clears both.
- **`swemuxd` now prints where the UI is and how to stop it**, instead of a bare
  `======== Running on ... ========` line that happened to carry the URL. Nothing runs after
  `uv tool install` prints its executable list, so a daemon's first line is the only place
  those facts can be given. It also names the thing Ctrl-C does not do: Ctrl-C detaches and
  leaves supervised sessions running, and `swemuxd --shutdown` is what stops everything.
- The Settings → Desktop integration group no longer offers to download the desktop shell,
  because there is nothing left to download. It reports whether this environment can run the
  tray and, when it cannot, the reinstall command for how this copy was installed.

### Fixed

- **Turning the per-harness Agent CLI toggle off is now reported as restart-scoped**, like
  its MCP and instrumentation neighbours. It was documented and rendered as needing a
  restart while the daemon classified it as hot-applied, so the response claimed an apply
  that never reached already-built adapters.

Everything in the following group came from one testing session on a clean Windows 11 machine
that is not the development host, which is the first time swe-mux had been run on one. They
share a cause: a property of the development host was written down as a fact about Windows.

- **Claude Code sessions report their lifecycle again on machines without Git Bash.** The
  hook command named the interpreter in the MSYS form (`/c/Users/...`), which only Git Bash
  can run. Where Claude Code dispatches hooks through PowerShell instead, all eleven events
  failed with `CommandNotFoundException` and took status detection, history, the prompt queue
  and approvals with them - while the session kept running and looking healthy. The path is
  now written `C:/Users/...`, the one spelling PowerShell, Bash and cmd all execute, and it
  reaches the shell unquoted (a space is removed via the 8.3 short name, because at command
  position PowerShell reads a quoted string as a string rather than a program).
- **`swemux doctor` now fails when an agent session has never reported a hook.** The failure
  above was silent and total, and was found by a human reading the CLI's stderr. It reports
  only sessions this daemon spawned, so a daemon restart does not flag every healthy session.
- **A prerequisite that is installed but not on PATH is no longer reported as missing.**
  Detection equated "on PATH" with "installed", so a machine with Git installed and Tailscale
  installed *and connected to a tailnet* was told to `winget install` both. Tailscale's
  Windows installer never adds its directory to PATH, so that was every GUI install of it -
  and it silently disabled Tailscale Serve, `tailscale cert` and the direct TLS listener with
  it. There are now three states rather than two, the off-PATH remedy names PATH instead of
  an install, and detection looks in the default install locations.
- **Settings → Diagnostics has a Re-scan button and a per-tool path override.** Re-scan
  re-reads PATH from Windows before looking again, because a daemon inherits its environment
  once at startup and would otherwise keep reporting a tool installed five minutes ago as
  absent, with nothing saying why.
- **Install instructions are per-platform.** A Linux user missing Git was told to run
  `winget` and sent to a `/download/win` page.
- **`uv` is on the prerequisite checklist**, since managed integrations are installed with it.
- **The Edge TTS integration installs without uv when there is a real Python to use.** uv is
  still preferred and still tried first, because `uv venv --python 3.12` can provide the
  interpreter as well as the environment - which is the only thing that works on a machine
  with no Python, or in the frozen desktop app where `sys.executable` is the bundle rather
  than a Python. A source install with neither uv nor that problem now falls back to
  `python -m venv` and pip, installing the same pinned version from the same index. Where
  neither is possible the refusal names both remedies instead of only naming uv, and a
  Debian machine missing `python3-venv` is told that specifically.
- **A Windows 11 machine without WSL stops spawning `wsl.exe` every 30 seconds.** Windows 11
  ships that binary whether or not the subsystem is installed, so availability was a
  guaranteed false positive; the stub then blocked, because the daemon runs windowless, and
  was tree-killed at an 8s timeout on every status poll, forever. Availability is now a
  registry check and no WSL command inherits the daemon's stdin.
- **Voice setup works on Python 3.13 and 3.14.** `uv tool install` picks the newest CPython
  on the machine, and the pinned voice closure had no wheel spaCy could load on 3.14, so
  local voice dead-ended at "no wheel this interpreter can load for: spacy". The pins now
  cover 3.12 through 3.14, CI checks the closure against each of them rather than only the
  3.12 it pins, and the refusal names the interpreter and the supported range instead of
  reading as a broken package.
- **Downloads verify TLS against the OS certificate store.** The Kokoro pronunciation model
  failed with `unable to get local issuer certificate` against github.com on a machine whose
  browser reached the same URL: Windows fetches most roots on demand and Python's reading of
  the store only sees the ones already fetched.
- **The Edge TTS install no longer throws away 80 seconds of work to report a timeout.** It
  checks it can reach PyPI before building anything, and separates "cannot connect" from
  "cannot verify". Its verification step - which imports the package and makes no network
  request at all - had a 20s budget that a first-ever import on a cold, scanned filesystem
  could not meet; it is now 120s and says what it was actually doing.

## [0.1.5] - 2026-08-30

### Added

- **swe-mux now ships an agent skill, embedded in every install.** `swemux --skill` prints
  the copy matching the running release, and `swemux install-skill` writes it into the skill
  directories agent CLIs actually read - two writes inside a checkout cover every registered
  harness, with no third-party tool and no registry. The skill teaches an agent the
  in-session environment check and where the current contract lives (the mux MCP tools, or
  `swemux --help`); it deliberately enumerates no commands, so it cannot go stale between
  releases. Installing into the per-user skill roots, which reach every agent you run
  anywhere, prints the exact paths first and proceeds only under `--yes`; `--remove` takes
  back only files the installer can recognize as its own.
- **A PyPI install can now gain the tray, the native window, and shortcuts without
  reinstalling.** Settings → General → Desktop integration installs or removes the Start
  Menu and Desktop shortcuts, and acquires the desktop shell's dependencies on one press -
  about 2.4 MB, verified against pinned hashes on the same path that fetches the speech
  libraries, never anything without an explicit press. One of those dependencies publishes
  no wheel at all, so the acquirer gained a pinned-sdist case with a strict rule: the
  archive is extracted, never built - nothing from it is ever executed, and an sdist that
  would need a build step is refused. The tray starts inside the desktop app, so after
  acquiring you launch (or restart) `swe-mux` once; every surface says so. On platforms
  with no desktop app the whole group is simply absent.
- **First run now asks how much swe-mux should do.** Three experience tiers, phrased as
  three genuine products: pure terminal (real terminals, nothing watching - no hooks, no
  status detection, no fleet plumbing), deterministic (transcripts, live status, managed
  harnesses, the agent fleet surface; model-free), and automations (adds the scan timeline
  and the model-backed observers, under your budgets). A tier is a batch of defaults,
  never a lock: everything it turns off stays one switch away, the choice is re-applyable
  and reversible from Settings → General, and existing installs are never stamped with a
  choice they did not make.
- **The empty workspace now offers three first steps instead of nothing.** A quest log on
  the empty stage points at the three setups that cannot finish in one screen: voice
  (opens the guided setup), isolated worktrees, and connecting a phone. It is capped at
  three by design - it will never become a todo list - the voice entry completes itself
  when voice is set up, and dismissing an entry is permanent, on every device.
- **Voice setup is now a guided walk.** Settings → Voice gains "Guided setup" (also in the
  command palette and by voice: "set up voice"): pick the engine, watch the one-press
  download's three progress lines, test the microphone where a permission prompt is
  visible and explained, and hear one spoken sentence at the end. Every step drives the
  same controls Settings already has, so nothing new can drift.
- **The side panel's default density follows the chosen tier.** A pure-terminal install
  opens with five panel tabs (Actions, Files, Notes, Git, Alerts) instead of ten; the
  agent-layer tabs stay one right-click away, exactly as before. Only a device that has
  never touched the panel visibility menu follows the tier default - any choice you have
  made, including showing everything, is never overwritten.
- **Agents can be handed the skill automatically, per harness.** A new "Fleet access"
  control in Settings → Harnesses phrases the choice as capability - may agents in this
  harness's sessions see the fleet, and how do they learn they can - with MCP tools, the
  skill file, both, or neither. The two routes are deliberately not symmetric and the
  control says so: for Claude the skill travels as a session-scoped plugin from the mux
  data directory and nothing is written into your Projects, while for codex, pi, omp, and
  opencode it is written into the Project's `.agents/skills/` at session start, because
  those CLIs read skills from nowhere else. Automatic delivery is off by default -
  swe-mux does not write into your checkout unasked - and turning it off later stops the
  writes without deleting anything.

### Changed

- **A session row now reports the whole time that session has worked**, not just the length of
  its current turn - the sum of every completed turn the record was willing to report, so a
  measurement it refused as "the last turn" is not quietly admitted into the total either.
  Context is also coloured on a finer ramp: the old one's first step arrived at 70%, by which
  point the decision it exists to inform - carry on in this thread or start a fresh one - has
  already been made for you.
- **The account switcher's quota figures line up.** Each provider's rows now sit under one set
  of headed columns rather than being printed as a sentence per account, so several accounts can
  actually be compared against each other. The Git drawer's refresh also shows that it is
  reading rather than appearing to have finished.

### Fixed

- **A transcript's Copy and Select controls no longer sit on top of the timestamp they were
  meant to clear.** They were drawn at rest on every reply, sized against a gutter that predated
  the read-aloud markers. They now appear when asked for: on hover or keyboard focus with a
  pointer, and one at a time by tapping with a finger, which has no hover.

- **The daemon starts tens of seconds faster when its database has grown large.**
  Every start used to re-verify the whole of `mux.db` before serving anything - a full-file
  read whose cost is the size of the file, measured at 60-84 seconds of every cold start
  against a 3.36 GB database, more than the entire rest of the startup sequence. The full
  verification now runs only when it can tell you something new: after the previous daemon
  died uncleanly (a crash or an external kill - the one signal that says the file's history
  is suspect), or when the last passing check is more than 24 hours old. Every other start
  runs a milliseconds header-and-schema probe instead, which still catches the
  gross-corruption class that used to stop the daemon coming up at all, and still quarantines
  a bad file before anything opens it. The trade, stated plainly: a corrupted page deep
  inside a cleanly-managed file can now go unnoticed for up to a day rather than until the
  next restart. Each start logs which check it ran and why, and deleting
  `mux.db.last-verified.json` beside the database forces a full check on the next start.

## [0.1.4] - 2026-08-30

### Added

- **The Windows installer now installs `swemux` and `mux`, and puts them on your PATH.**
  0.1.3 added those names for people installing from PyPI and said plainly that the installer
  shipped no command-line program; it does now. The installer writes a third directory beside
  the app and the PTY supervisor, holding the two launchers and nothing else, and adds that
  one directory to your user PATH - no elevation prompt, because the whole install is
  per-user. Open a new terminal afterwards: Windows tells Explorer about the change, and a
  console that is already open never hears about it. It is a tickbox on the setup wizard, on
  by default, so a machine whose PATH you curate by hand can decline it; declining installs
  the commands anyway, and `swemux doctor` says where they are. Installing a newer version
  over the top leaves PATH exactly as it found it - one entry, never two - and uninstalling
  removes that entry and nothing near it, with a `%USERPROFILE%\bin` coming back as a
  variable rather than as whatever it meant at the time. `swemuxd`/`muxd` are deliberately
  not part of this: the application already is the daemon and starts one when you launch it.

### Changed

- **Notes and Markdown files draw one more indent guide.**
  The editor they share moved to Continuity 0.2.40, which draws a guide at the first indent
  level as well as the deeper ones. Indent guides are on by default in swe-mux, so this shows
  up on every nested list without anything to turn on; Settings → Text editor still turns
  guides off entirely if you would rather not have them. Nothing else about the editor changed.

### Fixed

- **`swemux doctor` no longer reports three critical faults on a healthy install.**
  Run from the new command-line client, the checks that ask whether the daemon can start were
  asking it of the wrong program - the client deliberately contains no daemon, no browser UI
  and no terminal backend, all of which live in the application beside it. Those rows now say
  so and point at where the daemon actually is.
- **Opening the Agent tab no longer stalls when a recorded directory is on a filesystem that
  is not reachable.**
  Claude's `~/.claude.json` keeps an entry for every directory it has ever run in, and finding
  the one for your session used to ask the filesystem about each of them in turn.
  A recorded directory can name anywhere you have ever worked - a drive that is no longer
  attached, a share on a machine that is off, a WSL distribution that is stopped - and Windows
  does not answer for those quickly, it retries.
  On one machine with 183 recorded directories, one of them a stopped WSL distribution, a
  single request took 367 seconds.
  Path comparison now settles the ordinary case from the directory names alone, and any
  question it does put to the filesystem is given a deadline and its failure remembered, so an
  unreachable location costs a moment once instead of a minute per entry.
  Sessions starting up were on the same path and were delayed the same way.

## [0.1.3] - 2026-08-29

### Added

- **swe-mux now installs as `swemux` and `swemuxd`.**
  The old `mux` and `muxd` are unchanged aliases of the same two programs, so nothing you have
  already written needs updating.
  The new names exist because `mux` is shared with an unrelated tool, and on a machine carrying
  both, whichever installed last is the one your shell finds.
  This applies to installs from PyPI; the Windows installer ships no command-line program yet,
  and an operator who wants one alongside it can `uv tool install swe-mux`.
- **A frontend fix can now reach an installed app without replacing it.**
  `mux ui-overlay` packages the built frontend as a hash-verified overlay the daemon
  prefers over its bundled copy, at about 11 MiB against the ~370 MiB a full application
  update rewrites.
  The overlay is pinned twice - to the release and to a hash of the daemon's own route
  table - and an overlay that does not match the running backend is refused rather than
  served, because a frontend that disagrees with its daemon about which endpoints exist
  fails arbitrarily rather than legibly.
  Reverting to the bundled copy is one action.
- **Signing in to a provider account now starts from the account switcher.**
  It could previously switch between saved accounts but not add one, so an install with
  nothing saved showed "No saved accounts" beside a `manage...` button - the one screen a
  new install always reaches, and the one with no way forward on it.
  A sign-in also outlives the request that began it.
- **An agent can name the model for a session it asks to spawn.**
  Three of the five harnesses had declared model selection unmeasured; running their CLIs
  found all three accept a model flag, so this was being refused on a majority of harnesses
  because nobody had read `--help`.
  What the session actually started with is checked afterwards rather than assumed.
- **Agent authority can be set once for every Project.**
  Fifteen Projects previously meant fifteen editors to say one thing, with no way at all to
  say it about a Project whose own file already held a value.
  An install-wide default now reaches unset fields and an install-wide ceiling caps every
  Project, and may only narrow.
- **Agent instruction files can be linked into a session's context and unlinked again.**

### Changed

- **The desktop application is about 111 MiB instead of about 400 MiB.**
  The on-device speech closure - spaCy, CTranslate2, onnxruntime, misaki and their
  dependencies, roughly 277 MiB - is no longer shipped.
  Both speech features are off by default, so every install was previously downloading it,
  and letting the operating system scan it, for a capability it had not been asked to
  provide.
  It is now acquired on an explicit press, from pinned URLs verified against pinned
  SHA-256s, in one action that reports each part separately.
  A user who never enables voice saves about 289 MiB of download and disk; a user who does
  downloads about 193 MiB rather than 400 MiB.
- **An update now writes only the files that changed.**
  Measured across two real consecutive builds, 97.9% of files and 92.3% of bytes were
  already present and are reused in place rather than rewritten - which matters twice,
  because a file that is not rewritten also keeps the verdict the operating system's
  scanner already gave it.
  A whole-archive checksum is still verified before anything is staged, and an update that
  cannot reuse enough falls back to replacing the application outright.
- **A Project can trust its own agents' edits to the verification gate.**
  A branch that edited `.worktree-verify` refused its own land every time, and approving it
  was a per-digest act a human had to perform first - a routine edit here, so it stalled
  work on a review nobody was really performing.
  A gate edited by any other author still refuses and presents its bytes.

### Fixed

- **Voice could refuse to start on a page that had been open a long time, and say the daemon was
  at fault.**
  The voice status was fetched once when the page loaded, and a page that lost that one request
  never asked again - so every later attempt was refused with "daemon transcription is
  unavailable", a claim about a daemon it had never successfully reached and did not re-ask
  before refusing.
  The desktop application is what paid for this, because its page is opened once and kept for
  days across daemon restarts and updates, while a browser tab gets reloaded and quietly repairs
  itself.
  The status is now re-read whenever the event stream reconnects and again by the attempt itself
  before it refuses, and the reason is written where you can read it instead of appearing as a
  bare `error`.
- **The desktop application now decides its own microphone permission.**
  It previously inherited whatever the installed WebView2 runtime happened to do with a request
  nobody answered, for whatever address the embedded browser had been pointed at, and remembered
  that answer in its profile.
  The microphone is now granted to swe-mux's own address, denied to any other, and nothing is
  persisted.
- **A land stopped by a verification block now restarts when the block is cleared.**
  A refusal was terminal, so approving the gate's bytes fixed the *next* land and left the
  one that caused the block dead, to be asked for again by hand - or by an agent that had
  already been told its request was over.
- **An operator's own Land now shows the explanation the queue wrote for it.**
  The queue composed a bounded message naming what stopped it, in which checkout, against
  which trunk, and what to do next, and then dropped it whenever the requester was a person
  rather than an agent.

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

[Unreleased]: https://github.com/jatoran/swe-mux/compare/v0.2.3...HEAD
[0.2.3]: https://github.com/jatoran/swe-mux/releases/tag/v0.2.3
[0.2.2]: https://github.com/jatoran/swe-mux/releases/tag/v0.2.2
[0.2.1]: https://github.com/jatoran/swe-mux/releases/tag/v0.2.1
[0.2.0]: https://github.com/jatoran/swe-mux/releases/tag/v0.2.0
[0.1.5]: https://github.com/jatoran/swe-mux/releases/tag/v0.1.5
[0.1.4]: https://github.com/jatoran/swe-mux/releases/tag/v0.1.4
[0.1.3]: https://github.com/jatoran/swe-mux/releases/tag/v0.1.3
[0.1.2]: https://github.com/jatoran/swe-mux/releases/tag/v0.1.2
[0.1.1]: https://github.com/jatoran/swe-mux/releases/tag/v0.1.1
[0.1.0]: https://github.com/jatoran/swe-mux/releases/tag/v0.1.0
