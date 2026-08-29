# Windows desktop shell

## What it is

An optional Windows WebView2 window and system-tray supervisor around the same Preact/aiohttp
surface used by ordinary desktop and mobile browsers. The daemon remains a separate process and
continues to own every terminal.

## Key concepts

- Desktop supervisor: `swe-mux`; window/tray/login-startup owner.
- Managed daemon: separate `muxd` child launched with a desktop control secret.
- Hide: close/minimize removes the window from desktop presentation without daemon mutation.
- Quit: explicit tray action that confirms live terminals and stops the managed daemon
  (shutdown `mode=quit`; with the PTY supervisor this also reaps every supervised session, so
  the end state matches the in-process mode).
- Restart daemon (keep sessions): tray action shown only when `pty_supervisor_enabled`,
  which is the default, so it is shown almost always. The gate is the *setting*, not
  evidence a supervisor attached: a daemon whose supervisor failed to spawn still shows
  the item, and `POST /api/daemon/restart` refuses it with 409 `supervisor_not_attached`. Sends
  shutdown `mode=restart` (the daemon detaches from the PTY supervisor without reaping), waits
  for the old daemon to exit, and starts a fresh one, which reattaches to the still-running
  sessions. This is the session-preserving "reload with my changes" path.

## Operations

- One instance exists per resolved config path. A second visible launch signals the existing
  instance to restore/focus; a duplicate hidden login launch exits silently.
- Startup probes `/api/health`. A healthy daemon is reused; otherwise the tray starts a
  consoleless child. **A daemon that answers 503 is starting, not healthy**: the daemon binds
  its listeners before it builds its runtime, so a bound port is no longer evidence of a usable
  daemon, and every probe here reads readiness (`ok is True`) rather than reachability. The
  in-progress phase is in the 503 body, and the daemon writes each phase transition to
  `<data_dir>/lifecycle.log` itself. The daemon is spawned via
  `popen_outside_job` (breakaway from any inherited Job object) so a tray relaunched from
  inside a session cannot hand the daemon that session's kill-on-close Job; the tray also
  checks `process_in_job()` at startup and records a warning in the lifecycle ledger.
- **A daemon that is still running has not failed.** Only a spawned child that *exits* is a
  startup failure, and it ends the wait immediately; uptime is never evidence against it. The
  health wait is budgeted at `DAEMON_HEALTH_TIMEOUT_SECONDS` (300s) because a daemon's runtime
  is not ready the moment its port is, and a start whose page cache was just flushed
  by a redeploy takes multiples of a warm one. Exhausting the budget with the child alive is
  not fatal either: the tray, the window and the activation signal all come up, and the window
  loads once health finally arrives (`load_when_healthy`). The tray exits only when there is
  genuinely nothing to show. A 30-second budget and a fatal verdict on expiry previously killed
  the tray during ordinary post-redeploy starts, leaving a healthy daemon with no shell
  attached and requiring a manual relaunch.
- The tray-menu restart waits `DAEMON_RESTART_WAIT_SECONDS` (30s) instead, because pystray runs
  a menu action on its message thread and a long wait there freezes the tray, and because that
  path has nothing to wait for: the window already exists and its SPA reconnects to the
  returning daemon by itself.
- Health waits are recorded in `<data_dir>/lifecycle.log`: how long the daemon took to answer,
  or that it is still starting, or that it exited before answering.
- The daemon's console output redirects to `<data_dir>/desktop-daemon.log` (rotated to `.1`
  at each spawn; it is a crash catcher — structured logs live in the rotating
  `<data_dir>/daemon.log` / `access.log`). The tray watches the daemon child and appends its
  exit code to `<data_dir>/lifecycle.log`, the only record an externally-killed daemon leaves.
- Daemon-owned maintenance commands (Git, Tailscale, usage/account probes, hooks, profile
  discovery, forced cleanup, and SAPI) use Windows no-window process creation. Interactive
  shells and agents remain attached only through ConPTY; background work never flashes a console.
- WebView2 uses persistent `<data_dir>/webview` storage and enables text selection. External
  links continue in the system browser.
- Production WebView2 runs with browser accelerator keys disabled by pywebview, so desktop-only
  bindings such as `Ctrl+Tab` reach the SPA. An ordinary Chrome/Edge tab keeps those browser
  shortcuts instead; the same saved mapping is harmless there because the page never receives
  the chord.
- Window close is cancelled and hidden. Minimize hides after the native transition. Tray Open
  shows/restores the same window; Open in browser preserves the ordinary browser surface.
- Normal window bounds and maximized state persist in
  `<data_dir>/desktop-window-state.json`, outside the replaceable application bundle.
  Move and resize events are debounced and atomically saved; minimized and hidden presentation
  are never persisted. Relaunch, redeploy, rollback, and login startup therefore restore the same
  usable geometry, while a missing or invalid state uses the centered 1440 x 920 default.
  An in-app redeploy separately samples whether any desktop app window is visible immediately before the stop, then relaunches the successful or rolled-back bundle with that same visible or tray-hidden presentation.
  Direct script invocations retain explicit `--hidden` behavior and otherwise launch visibly; `--restore-visibility` is reserved for callers that need the sampled presentation.
  Saved bounds are fitted to the current monitor working areas before window creation, so removing
  a monitor or reducing its resolution cannot restore the title bar off-screen.
- Start with Windows writes the exact current executable/config command to the current-user Run
  key. No machine-wide installation or elevation is required.
- Tray Quit uses a native topmost Windows confirmation owned by the desktop supervisor, not the
  WebView. Confirmation therefore works identically while the window is visible, minimized, or
  hidden. Confirmed Quit reports the live terminal count, requests authenticated graceful
  shutdown, stops the tray, and destroys the window. A desktop crash or forced window-process
  exit leaves the daemon running for recovery.

## Security boundary

- **The packaged app makes one outbound request of its own, and it is still not an
  updater.** The daemon's daily release check (`update_check.py`, `design/interfaces.md`,
  and the outbound bullet in `remote-access.md`) runs in the frozen build exactly as it
  does from source, carries nothing that identifies the machine, and is off entirely under
  `update_check_enabled`. It **detects and presents**; it downloads nothing, verifies no
  hash, and touches no bundle.
  The **updater** (`update_install.py`, `POST /api/update/install`, `mux update --install`)
  is the deliberate half and is a separate act in every sense: it runs only on an explicit
  press carrying `X-Mux-User-Gesture: update-install`, and only when that press *names the
  version it means*, so a manifest that moved between the banner and the button is refused
  rather than silently installed. Nothing about the passive check changed; a banner still
  starts nothing.
  **What the updater downloads is verified before it is staged, and the check is the point
  of the manifest's hashes.** The artifact's SHA-256 is computed over the bytes as they
  arrive, the file is written under a `.part` name, and only a matching digest promotes it
  to a name the swap can see - so a partial, corrupted, or substituted download is never a
  file the staged swap can find. A hash comes from the *manifest* only: the GitHub
  Releases fallback publishes none, so a release discovered that way can be announced and
  can never be installed.
- **The updater refuses a release that would need a new PTY supervisor, and that refusal is
  the feature.** The swap preserves sessions only because the supervisor outlives it, and
  refreshing `dist/swe-mux-supervisor/` reaps every live session (see the supervisor rules
  in the root `CLAUDE.md`). So each bundle declares the supervisor protocol its daemon
  speaks in `bundle.json` (`bundle_metadata.py`, written by `build_desktop.describe_bundle`),
  the running supervisor declares its own in `<data_dir>/supervisor.json`, and a difference
  stops the install with the manual flow named in the message.
  Three details are load-bearing. It compares the **protocol**, not a source hash:
  `build_desktop.supervisor_source_hash()` mixes in the *build machine's* pywinpty/psutil/
  PyInstaller versions, so hashes never match across a release and comparing them would
  refuse every update forever. It compares with `!=` rather than `>`, because the
  supervisor's `hello` refuses any mismatch and a downgrade strands the fleet exactly as a
  bump does. And an archive whose metadata is **missing or unreadable** is refused too -
  "cannot tell whether this reaps your sessions" is not a case to guess at, and it is
  precisely what an archive built before this contract looks like.
- **The frontend overlay replaces the UI without replacing the application, and the three
  properties that make that sound are not optional.**
  `frontend_overlay.py` lets a hash-verified `static/` tree in the data directory be served
  in place of the bundled one, so a CSS or JS fix reaches a frozen app in seconds rather than
  through a bundle swap.
  It is the same pattern Expo/EAS Update and CodePush use for React Native and asar swapping
  uses for Electron, and it is sound rather than hacky because of exactly three things.
  **Verification** is a full SHA-256 pass over every listed file plus a closed rule for
  everything unlisted, recomputed at every daemon start (measured 2026-08-29: 101 files,
  23.05 MiB, 70-91 ms, against a start measured in tens of seconds).
  **A compatibility pin with two halves**, both compared for exact equality and both checked
  before anything is hashed.
  `requires_backend` is `swe_mux.__version__`, which gives one rule an operator can hold in
  their head: *an app update always supersedes an overlay.*
  `requires_api` is a digest over the daemon's whole route table, and it is the half that
  actually catches the failure, because **`__version__` alone cannot**: this project's frozen
  app is rebuilt from a checkout that moves per commit while the version string moves per
  release, so a frontend built from master today and an app built from master last week both
  say "0.1.2" and disagree about which endpoints exist.
  The practical rule that follows: **package an overlay from the same checkout the running app
  was redeployed from.** If the backend has moved since, the overlay is refused with
  `api_mismatch` and a redeploy is the honest answer, because a backend change is not
  something an overlay can carry.
  **A revert** that flips one boolean in one small atomic file, moves and deletes nothing, and
  is reachable without the UI (`mux ui-overlay revert`) because an overlay's own failure mode
  is a frontend that will not load.
  Two consequences worth stating outright.
  The pin is a claim by the **producer** (`packaging/build_frontend_overlay.py`), and the
  daemon never mints one for a payload that arrived without a manifest - a pin the consumer
  invented is not a pin, so an unmanifested tree is refused rather than adopted.
  And every failure resolves back to the bundled tree with a reason: a bad overlay costs a
  stale frontend and a `WARNING`, never a daemon that will not start, which matters because
  the daemon is what serves the endpoint that would fix it.
  Installing is loopback-only and carries `X-Mux-User-Gesture: frontend-overlay-install`;
  reverting deliberately is not loopback-only, because it is the safe direction.
  A URL source additionally **requires** the SHA-256 it must match - there is no manifest
  here to take one from, and an unverified download reaching the served tree would be an
  arbitrary-code-execution path into the application's own UI.
- `<data_dir>/desktop-control.token` is random, user-local control material.
- The token reaches the child only through `SWE_MUX_DESKTOP_CONTROL_TOKEN`.
- `/api/desktop/shutdown` is absent as authority for standalone daemons, rejects non-loopback
  peers, and uses constant-time comparison. The browser never receives the token.
- An already-running unmanaged daemon remains operational when the desktop shell exits; the
  shell never guesses a PID or broadens network shutdown authority.

## Packaging

- Runtime extra: `uv sync --extra desktop`.
- Build dependencies: `uv sync --extra desktop --extra voice-local --group package`.
  `--group package` is what brings the `g2p-model` group with it, and the build refuses without it
  (`build_desktop.REQUIRED_BUILD_GROUPS`).
- `voice-local` is optional at install time and mandatory at build time.
  It carries the on-device speech closure, and with it the LGPL `num2words`, whose
  relink condition is met only by the spec collecting it as readable source under
  `_internal/num2words/`.
  `collect_all` on an absent package collects nothing and does not fail, so
  `build_desktop.verify_build_extras_installed` refuses the build up front and
  `redeploy_desktop`'s preflight runs the same check before it stops the app.
- `packaging/build_desktop.py` builds the frontend in `.runtime/`, publishes hashed assets before
  `index.html`, generates the ICO, and runs PyInstaller. It never empties the live static tree;
  locked content-addressed stale assets may remain harmlessly until a later build.
- **The bundle carries exactly two data trees:** `src/swe_mux/static` and `src/swe_mux/assets`
  (`packaging/swe_mux.spec`). Anything a runtime feature has to *read* therefore belongs under
  `assets/`, and `.docs/` is not in the bundle at all. A feature that reads prose from a
  repository path works on a maintainer's machine and is silently absent in the frozen app,
  where its whole audience is. The configurator's guides live at
  `src/swe_mux/assets/configurator/` for this reason, and its test asserts every listed guide
  has a file (`configurator.md`).
- **`build_frontend` runs `vite build` directly, so npm's `postbuild` hook never fires and every
  step it performs has to be repeated explicitly.** Forgetting one is silent in the worst way: the
  bundle builds, the daemon starts and reports healthy, and the defect appears only in a browser.
  `test_the_desktop_frontend_build_repeats_every_postbuild_step` reads `frontend/package.json` and
  fails when a `postbuild` script has no counterpart here. (`prebuild` needs no such guard: this
  path runs `npm run check`, whose `precheck` performs those steps.)
- **Precompressed variants are dropped on publish and regenerated, never copied over.** Vite emits
  no `.gz`, so a publish that only copies leaves the previous build's compressed files beside the
  new source. The daemon prefers a `.gz` for any client sending `Accept-Encoding: gzip` - every
  browser - and `index.html.gz` names content-hashed assets, so a stale one serves an index whose
  every asset 404s: a blank screen on a bundle that reports itself healthy.
  Since 2026-08-28 there is a **second** producer, and the bundle is the one place both run:
  `build_support.precompress_static` regenerates missing or stale sidecars as a daemon startup
  phase, because the wheel and the sdist deliberately carry none (they were 35% of the download).
  It is not a replacement for the build-time step - a `npm run build` in a source checkout does
  not restart the daemon, so the stale sidecar above would live until something did - and the two
  cannot disagree about which files earn one
  (`test_desktop.py::test_the_python_and_node_precompressors_agree_on_the_rule`).
  This defect also defeats the asset-hash check in the repository's `CLAUDE.md`, because `curl`
  without `--compressed` is served the correct plain `index.html`. To check the file a browser
  actually receives, request it compressed:
  `curl -s --compressed http://127.0.0.1:8765/ | grep -o 'assets/index-[A-Za-z0-9_-]*\.js'`.
- `packaging/swe_mux.spec` emits windowed `dist/swe-mux/swe-mux.exe` and `_internal/`. The
  complete `onedir` folder is the distributable unit; the executable is not standalone. It is
  deliberately the *only* executable here: a second one (`swe-mux-action.exe`) used to root
  task terminals, and a live task then locked this directory against the redeploy swap.
- **The bundle's license posture is proven at build time, not asserted in prose.**
  `build_app_bundle` calls `verify_bundle_licenses`, which fails the build on three
  things: a forbidden GPL payload present by artifact name (PyAV's `av.libs`, the
  espeak-ng loader, `phonemizer`, an x264/x265/avcodec/espeak shared library); an
  allowlisted LGPL package that is *not* shipped as readable source under
  `_internal/<pkg>/`; and, through `verify_no_gpl_av`, PyAV re-entering at all. The
  check reads the built tree rather than package metadata because declared metadata is
  exactly what hid the original defect - PyAV declares BSD-3-Clause and links GPL
  x264/x265, sherpa-onnx declares Apache-2.0 and statically links espeak-ng.
- **The bundle's *membership* is proven at build time too, and separately from its licensing.**
  `build_app_bundle` calls `verify_bundle_contents` before `verify_bundle_licenses`.
  It compares the top-level package directories under `_internal/` against
  `build_desktop.EXPECTED_BUNDLE_PACKAGES` - 51 packages, 371 MB, measured 2026-08-29 from a
  build in exactly the closure CI uses - and fails on a difference in either direction.
  The reason it is a separate check is that the two ask different questions and the license
  gate provably cannot answer this one: `dist/swe-mux` as built 2026-08-27 carried 101 MB of
  `playwright/driver`, collected because PyInstaller followed the lazy `import playwright` in
  `preview_capture.py` and the package happened to be in that build venv, while
  `license_audit.py` states plainly that `preview-capture` does not ship.
  Playwright is Apache-2.0, so `verify_bundle_licenses` passed it.
  A hundred megabytes of files a user's machine has never seen is also the dominant cost of an
  update, so this is a size gate as much as a hygiene one (`../../development/ROADMAP.md`
  Phase 21).
  The **missing** direction matters for the opposite reason: a `collect_all` entry that stops
  collecting is invisible until the frozen app runs one feature, which is exactly why those
  entries are explicit in the first place.
  `*.dist-info` directories are excluded from the comparison because their names carry version
  numbers and a manifest containing them would be edited without ever being read.
  Two passengers are recorded in that manifest rather than removed: `mypy` with `mypyc`'s
  `librt` and `ast_serialize` (3.8 MB of compiled `.pyd`, arriving through the
  `pydantic.mypy` and `thinc.mypy` static-analysis plugins that nothing imports at runtime),
  and `setuptools`. Excluding them is a behaviour change that has to be proven against a
  running frozen app, which this gate should not smuggle in.
- **UPX is pinned off in `swe_mux.spec`, and the pin is the point.**
  It was `upx=True` while UPX has never been installed on any machine that builds this, so it
  was a no-op that only meant something on the day somebody installed the tool - at which point
  it would add a compression pass over a ~400 MB closure to every build *and* give every shipped
  binary a packer signature, which is one of the best-known antivirus heuristics. Antivirus
  scanning is already the dominant cost of a swe-mux update, so the upside was a smaller download
  and the downside was more of the exact thing that makes updates slow.
  `packaging/swe_mux_supervisor.spec` still says `upx=True` and is **deliberately not edited,
  including its comment**: that file is a member of `build_desktop.SUPERVISOR_SOURCES`, whose
  SHA-256 is taken over file *bytes*, so a pure comment invalidates the supervisor bundle exactly
  as a value change would - `supervisor_bundle_current()` would report the running bundle stale
  forever, `mux doctor` would advise a rebuild, and that rebuild reaps every live session. Pin it
  in the same commit as the next deliberate supervisor rebuild, when the reap is paid for anyway.
- **PyAV is out of the dependency closure entirely, not just out of the bundle**
  (2026-08-27). `faster-whisper` hard-requires `av>=11` and nothing in swe-mux reaches it:
  the only import is `faster_whisper/audio.py`'s module-level `import av` for
  `decode_audio`, while `voice.py` hands validated raw PCM straight to `WhisperModel`.
  So the dependency is dropped rather than replaced, by a `[tool.uv]`
  `override-dependencies` entry whose marker no supported environment satisfies, and the
  import it existed for is satisfied by a stub.
  **That stub has one definition** - `src/swe_mux/av_stub.py` - reached by two entry
  points: `packaging/rthook_av_stub.py` (before any application import in the frozen app)
  and `voice.py` immediately before each of its `faster_whisper` imports. Keeping a second
  copy in the hook is what would drift, and the drift shows up as dictation that works in
  a source checkout and not in the shipped app. Any *use* of the stub raises; module
  dunders answer as ordinary missing attributes, because `repr()` of a module reads
  `__file__` and a stub that refused that turned every log line mentioning `av` into a
  RuntimeError from inside the stub.
  The override governs this project's resolution - `uv.lock`, `uv sync`, the bundle, the
  gate - and is *not* carried in the wheel's `Requires-Dist`, so a downstream
  `pip install swe-mux[voice-local]` resolves faster-whisper's own `av>=11`; swe-mux
  imports the real package on no path either way.
  That sentence was **unreachable until 2026-08-28**: the extra itself did not resolve, because
  it declared `en-core-web-sm` and that name is on no index
  (`../../development/DEPENDENCY_AUDIT_2026-08-28.md` § 4). Fixing the extra is what made the
  `av` residue real, which is the ordering to keep in mind when reading anything written about
  it before that date.
- **The spaCy G2P model is a dependency *group*, not an extra, and the bundle still collects it.**
  `en-core-web-sm` cannot be a published requirement at all - `[tool.uv.sources]` resolves it
  from a GitHub release and a `uv` source does not travel in a wheel - so it is declared in
  `g2p-model`, which PEP 735 never publishes. Two things have to name that group or the bundle
  and the audit stop describing each other: `license_audit.DISTRIBUTED_GROUPS` (it *is*
  redistributed, under `_internal/en_core_web_sm/`) and `build_desktop.REQUIRED_BUILD_GROUPS`
  (a build environment without it produces a bundle whose `collect_all` collected nothing, and
  `collect_all` on an absent package is silent). A wheel install has no bundled copy and
  downloads it at first use instead (`voice_models.SpacyModelStore`).
- **The optional Edge TTS client is external-only.**
  `voice-edge` is a source-install convenience extra and is not a member of
  `license_audit.DISTRIBUTED_EXTRAS`.
  `swe_mux.spec` excludes `edge_tts` even when the build environment installed that extra, and
  `verify_bundle_licenses` rejects `_internal/edge_tts/` as a distribution-boundary regression.
  The bundle carries only `assets/integrations/edge_tts_bridge.py`, which is swe-mux Apache code;
  the explicit Settings action asks the host's `uv` to install the pinned LGPL client directly
  from PyPI under `<data_dir>/integrations/edge-tts/current`, or the operator names a separate
  Python override.
  The managed environment is staged and bridge-verified before activation, so a failed install
  does not replace a working one and still does not make the client part of `dist/swe-mux`.
- **`pystray` and `num2words` are in the spec's `collect_all` loop for a licensing
  reason, not a packaging one.** Both are LGPL. `collect_all` defaults to
  `include_py_files=True`, so each lands as plain source under `_internal/<pkg>/`
  instead of being frozen into the executable's archive, which is what lets a recipient
  substitute their own build and satisfies the LGPL relink condition that
  `THIRD-PARTY-NOTICES.md` promises. Removing either name is a build failure rather than
  a silent compliance regression. `num2words` is not optional: `misaki.en` imports it at
  module scope for the Kokoro G2P. The metadata half of the same gate lives in
  `packaging/license_audit.py` and runs in the verification gate.
- `packaging/swe_mux_supervisor.spec` emits the dedicated PTY supervisor bundle
  `dist/swe-mux-supervisor/` — a separate artifact precisely so rebuilding `dist/swe-mux`
  never collides with a running supervisor's file image (Windows locks running
  executables). `build_desktop.py` rebuilds it only when the supervisor's small source
  closure changes (hash gate: a supervisor rebuild requires reaping sessions first anyway).
  Frozen daemons prefer this bundle; `--supervisor-child` remains the fallback when the
  bundle is absent, and `SWE_MUX_SUPERVISOR_EXE` overrides resolution in any mode.
- `packaging/redeploy_desktop.py` is the frozen update workflow (usable by an agent from
  inside a supervised session, or via `POST /api/daemon/redeploy` behind the UI's
  "Rebuild + redeploy app" menu entry): preflight (dedicated supervisor running; a
  legacy check for `swe-mux-action.exe` terminals left over from a pre-removal bundle, which
  nothing creates any more; and the **bundle-in-use gate** — `bundle_locks.py` names any
  foreign process anchoring `dist/swe-mux` by exe or cwd, because such a process survives
  everything the redeploy may stop (sessions descend from the supervisor) and dooms the
  swap after minutes of build: typically a dev server behind a Preview tab or a terminal
  whose cwd inherited into the bundle. The gate runs pre-build, again pre-stop, and in the
  endpoint as `409 bundle_in_use`; `--force`/`force=true` downgrades it to a warning),
  then a **staged** cycle — build frontend + app bundle
  into `dist/.staging` while the old app keeps serving, detach-stop the daemon and shell only
  after the build succeeded, swap (`dist/swe-mux` → `dist/swe-mux.prev`, staging in; renames
  retry through lock stragglers), relaunch; the fresh daemon reattaches every live session.
  The `swe-mux.prev` rename target is proven free before the app is stopped — a tree Windows
  only partially deleted (mapped exe/DLL images) otherwise blocks the swap after the daemon
  is already down; a stubborn leftover is moved aside to `swe-mux.prev.stale-*`.
  A failed build never touches the running app, and a new build that never reports healthy is
  rolled back to `swe-mux.prev` (failed bundle kept at `dist/swe-mux.failed`), so a remote
  client cannot be stranded.
  **The health wait reports progress rather than waiting in silence.** The successor daemon
  binds before its runtime is built and answers 503 naming the phase it is in, so `wait_healthy`
  logs each phase *change* to `redeploy.log` and, on expiry, says which phase the budget ran out
  in. Once-per-phase and not once-per-poll: the elapsed seconds in the line move continuously, so
  comparing rendered text would write two lines a second. Nothing here estimates a remaining
  time — phase durations vary by orders of magnitude across fleets, and a wrong number is acted
  on where an absent one is not. This is what the 300s→600s budget change of 2026-08-21 was
  really about: a healthy-but-slow deploy was indistinguishable from a hung one, and raising the
  ceiling only widened the window in which nobody could tell.
  The UI endpoint invokes the script with `--restore-visibility`, so every relaunch path uses the presentation captured immediately before the old shell is stopped.
  The endpoint validates source checkout + `uv`, requires the
  attached supervisor (or `force`), and is single-flight via `<data_dir>/redeploy.lock` with
  output in `<data_dir>/redeploy.log`.
  The lock names the *script* process and is never removed on exit, so a crash releases it and
  nothing has to clean it up: the process is the authority.
  **It names the process's start time as well as its pid, and that is load-bearing.**
  A pid is not an identity on Windows - numbers are recycled aggressively - so a bare liveness
  check made a *successful* redeploy's lock read as live forever once something unrelated
  inherited the number. Measured on the primary host: a run that completed at 18:35 on
  2026-08-23 left pid 50760, which by the next morning was an `svchost`, and every redeploy in
  between was refused with "a redeploy is already running". Nothing surfaced it, because a
  refusal is a clean exit-2 abort rather than a failure - the operator simply sees a redeploy
  that never happens. Same pid with a different start is a different process
  (`bundle_locks.REDEPLOY_LOCK_NAME`, one rule shared by both readers).
  The identity is deliberately *not* also checked against the process's command line, though
  that would have caught this one: a `cmdline()` read can be slow or refused on Windows, and a
  false negative there starts a second redeploy racing the first for the same staging tree,
  which is worse than the failure being fixed and is the exact thing single-flight exists to
  prevent.
  A lock written by an older bundle carries only a pid and falls back to plain liveness, so the
  upgrade that introduces the stamp cannot decide an in-flight redeploy has stopped; exactly one
  such lock can exist per machine and a stale one is cleared by hand, once.
  A run started straight from a terminal claims the same lock itself (`--lock-held` tells the
  script the endpoint already did), which makes a CLI redeploy single-flight too and makes it
  visible to `GET /api/daemon/redeploy` exactly like a UI one.
  Every run records a machine-readable outcome in `<data_dir>/redeploy-result.json`
  (`succeeded` / `rolled_back` / `build_failed` / `swap_failed` / `unhealthy` / `refused` /
  `failed`, plus a detail sentence and a log tail), written whole via a temp file because the
  successor daemon reads it while starting up.
  The successor serves it as `last_result`, which is what lets the reconnecting UI say that a
  rollback happened: the app comes back looking entirely normal, so otherwise nothing would tell
  the operator their change never shipped.
- **The updater is that same script with a download where the build was**, and nothing
  else. `packaging/redeploy_desktop.py --from-archive <zip> [--archive-sha256 <hex>]`
  verifies and extracts a release archive into `dist/.staging` instead of running
  PyInstaller; every step after it - the bundle-holder gate, the detach-stop, the swap, the
  health wait, the rollback to `dist/swe-mux.prev`, the `redeploy-result.json` record - is
  the same code and carries the same guarantees. Two things follow from the reuse being
  real rather than described: a failed download or a rejected archive leaves the running app
  completely untouched, because the refusal happens before anything stops; and the script
  re-checks the SHA-256 itself, because it is separately invocable with any path a person
  can type and a guarantee that only holds when the right caller invoked you is not one.
  The build-extras preflight is skipped for an archive install: the released bundle already
  satisfies the LGPL relink obligation that check protects, and requiring a local build
  environment would refuse exactly the install that needs none.
- **Every built bundle describes itself in `bundle.json`** at its root (schema, version,
  `supervisor_protocol`, platform, build stamp), written by `build_desktop.describe_bundle`
  after the license verification. Every bundle, not only released ones: the updater refuses
  an archive it cannot interrogate, so a bundle built without it is one nobody can update
  to - and a staged redeploy's tree becomes the next `dist/swe-mux`.
- **The release artifact's *name* is a contract**, because the manifest says only what an
  artifact is called, where it is, and what it hashes to - the updater has to recognize its
  own platform's bundle by name alone. `swe-mux-<version>-<platform>-<arch>.zip` on Windows
  (`.tar.gz` elsewhere), containing exactly one top-level `swe-mux/` directory.
  `packaging/package_desktop_release.py` is its only writer and derives the name from
  `update_install.release_archive_name`, so the two halves cannot drift into a release no
  installed copy can find; it prints the SHA-256 the manifest step needs. A release that
  publishes nothing matching the name is reported as "no desktop bundle for this platform"
  rather than guessed at.
- **A release carries two desktop artifacts, and they answer different questions.** The
  portable archive above is what the *in-app updater* downloads and hands to the staged
  swap; `swe-mux-<version>-<platform>-<arch>-setup.exe` is the Windows installer, and it is
  the only artifact usable by someone who does not already have Python.
  `update_install.release_installer_name` names it and answers `None` off Windows rather
  than inventing a name no release will carry. The two names are deliberately unable to
  collide under any version string, because the updater looks its own artifact up by
  *exact* name.
- **The container a release archive is in comes from its name, and the reader honours both.**
  `_ARCHIVE_SUFFIX` has always said `.zip` on Windows and `.tar.gz` on macOS and Linux;
  until 2026-08-28 `bundle_archive.py` could open only zips, so the POSIX half was a promise
  no reader could keep and the first POSIX desktop release would have been announced and then
  refused at install. Closed by teaching the reader, not by pointing POSIX at `.zip`: a zip
  cannot carry what a POSIX bundle needs, because `ZipFile.extractall` does not restore the
  mode bits it stores and the extracted `swe-mux` binary would arrive without its executable
  bit. A tarball is extracted under `filter="data"` (the interpreter's own refusal of
  absolute paths, `..` escapes, links leaving the tree, and special files), with
  `validate_members` still running first and independently. Both formats now also bound the
  *uncompressed* size: `update_install`'s ceiling bounds the download, which for a gzip
  stream says nothing about what extracting it would write.

### The Windows installer

`packaging/installer/swe-mux.iss` (Inno Setup 6) compiled by `packaging/build_installer.py`.
It exists because a wheel cannot fix the gap it fixes: wheels have no post-install hook - PEP
427 deliberately dropped `bdist_wininst`'s shortcut machinery - so a `pip install` leaves a
person with no shortcut, no tray, and no idea where anything went.

- **It is per-user (`PrivilegesRequired=lowest`) and never elevates, and there is no
  override.** Every piece of state swe-mux owns is per-user already: the data directory, the
  `HKCU\...\Run` login registration, a loopback daemon under the signed-in account, and
  `automation.secrets.json`'s current-user DPAPI blobs. A per-machine install would put the
  bundles where a standard user cannot write - which is exactly the tree a staged swap
  renames - trading one elevation prompt now for an update path that needs one every time.
  `{autopf}` under `lowest` is `%LOCALAPPDATA%\Programs`.
- **The layout inside `{app}` is a contract with the running daemon.**
  `supervisor_client.dedicated_supervisor_exe()` resolves the supervisor as
  `<exe>\..\..\swe-mux-supervisor\swe-mux-supervisor.exe`, so the installer reproduces
  `dist/`'s shape exactly: `{app}\swe-mux\swe-mux.exe` beside
  `{app}\swe-mux-supervisor\swe-mux-supervisor.exe`. Flattening them into `{app}` resolves
  one directory too high and the daemon falls back to `--supervisor-child`, silently
  re-creating the file-lock collision the separate bundle exists to prevent.
- **An upgrade deletes the old bundles before writing the new ones** (`[InstallDelete]`), and
  keeps one `AppId`. A PyInstaller onedir tree is not additive: a dependency dropped between
  releases leaves an importable stale `.pyd` behind, and copying over the top produces a tree
  that is neither version. `CloseApplications=yes` lets Restart Manager close whatever holds a
  file under `{app}` first, because a running app locks its own `.exe`.
- **The Ready page states what an upgrade costs.** Replacing the bundles closes the PTY
  supervisor, and that ends every live terminal session - the deliberate out-of-band act the
  supervisor-update flow exists to make explicit. Restart Manager offers to close the
  processes; it does not say that. `UpdateReadyMemo` appends the warning only when a previous
  version is installed, so a fresh install is not warned about sessions it does not have.
- **The optional login task writes the tray's own registry value, not a second mechanism.**
  Same key and same value name as `desktop.RUN_KEY`/`RUN_VALUE`, and the `.iss` reproduces
  `desktop.startup_command()`'s `list2cmdline` quoting and argument order, because
  `startup_enabled` compares the value *exactly*. A Startup-folder shortcut would have been
  the easy alternative and is the wrong one: it autostarts the app while the tray's checkbox
  reports that nothing does. `tests/test_windows_installer.py` pins the reproduction against
  the Python side. The one case the two can disagree is a `MUX_DATA_DIR` written with a
  leading `~`, which the installer takes literally; the cost is a checkbox that reads off
  until it is clicked once, never a missing or duplicated registration.
- **The uninstaller removes that value only when it names `{app}`**, rather than using
  `uninsdeletevalue`: the tray can turn the toggle on after install, and one install's
  uninstaller must not strip another's login entry.
- **`packaging/swe-mux.ico` is gitignored build output**, rendered by `build_desktop` from
  `desktop.create_tray_image` on every build. A fresh clone has none, so `build_installer.py`
  refuses with the command that makes it rather than letting ISCC report a bare "The system
  cannot find the file specified" and a line number.
- **Every comment in the `[Code]` section is `//`, never `{ ... }`.** Pascal's brace comment
  ends at its first `}` and this script's comments are about `{app}`, so a braced one
  terminates mid-sentence and the prose after it compiles as code - reported by ISCC as
  `'BEGIN' expected` on the line *after* the comment. A test fails on the comment instead.
- **Signing is a hook and not a step.** No certificate exists yet
  (`RELEASE_MANUAL_TASKS.md` § 1), so nothing signs and nothing fails when nothing is
  configured: the `.iss`'s `SignTool`/`SignedUninstaller` pair is behind `#ifdef SignTool`,
  and `build_installer.py` emits both the `/S<name>=` registration and the `/DSignTool=`
  symbol only when `SWE_MUX_SIGNTOOL` is set. Turning signing on is one environment variable
  and no file change. The *payload* executables are a separate question and belong to
  `build_desktop.py`; an installer signed around unsigned binaries still raises SmartScreen
  on first launch.
- **An installer-managed install cannot use the in-app updater, and says so rather than
  failing oddly.** `redeploy_launch.redeploy_source_root()` requires
  `packaging/redeploy_desktop.py` and `pyproject.toml` beside the bundle, which an installed
  copy has neither of, so `UpdateInstaller._preflight` refuses with `no_swap_tool` before
  anything is downloaded. Upgrading such an install means running the new installer, which is
  what the Add/Remove Programs entry and the same-`AppId` in-place upgrade are for.
- `release.yml`'s `build-desktop` job builds both artifacts on `windows-latest` and uploads
  them as the `desktop` artifact; `github-release` and `update-manifest` download it into the
  same `dist/` the wheel lands in, so the manifest step's directory enumeration picks both up
  with real hashes and no name list to keep in step. Inno Setup ships on the runner image
  (its own image test asserts `Get-Command iscc` resolves); the workflow installs it only if
  a future image drops it.
- Clients are told about a redeploy rather than discovering it as failed requests.
  The daemon emits `daemon_redeploy_started` when it accepts one or is asked to announce a
  terminal-launched one (`POST /api/daemon/redeploy/announce`, loopback-only and refused unless
  `redeploy.lock` names a live process - it describes a real redeploy, it is not a way to put the
  fleet's UI into a fake maintenance mode).
  It emits `daemon_redeploy_stopping` from `POST /api/desktop/shutdown` when a redeploy is in
  flight, then lingers briefly so the frame reaches the `/events` sockets the shutdown is about to
  close.
  That is the only authoritative "the outage starts now" a browser can get: the script stops the
  daemon through that endpoint, so the daemon is still alive and still has its sockets when it
  learns the build finished, whereas inferring the same thing from a dropped socket is
  indistinguishable from an ordinary blip.
  Nothing here is load-bearing for the redeploy itself - it only buys the UI a progress chip, so an
  unreachable or older daemon costs nothing but the old behaviour.
- **The press is acknowledged before the daemon has accepted it.**
  Every signal a client can observe about a redeploy - the `202`, the broadcast, the lock a status
  read reports - is produced *after* the accept path's preflight, whose bundle-holder scan walks
  every process on the host reading its exe and cwd (7.8s cold and 2.7s warm, measured on the
  primary host 2026-08-27). So the button did nothing observable for seconds, on the one action in
  the app that takes minutes and that an operator has every reason to doubt they pressed.
  Two changes, and only together: the UI enters a `requested` phase at the press and probes nothing
  until the accept lands (`design/features/ui.md`, `technical/frontend/packages/composition.md`),
  and the scan moves off the press - the confirm dialog runs it via `GET /api/daemon/redeploy?holders=1`
  while the dialog is being read, and the accept joins that single-flight scan rather than starting
  a second one.
  The dialog therefore also *names* a blocker before the operator commits, instead of refusing after
  they have. The scan's 15s reuse window is short by construction: a holder appearing inside it is
  missed, and the swap's own rename retry and rollback stay the backstop rather than becoming the
  first line of defence.
- **A planned stop records itself before it happens**, because it usually does not get to finish.
  `POST /api/desktop/shutdown` and `POST /api/daemon/restart` both call `lifecycle.planned_handoff`
  at the moment they decide the intent, which is the last moment the daemon is guaranteed to still
  be running: the script watches health and terminates the process about three seconds after it
  stops answering, several seconds before the teardown reaches its clean-exit write.
  Without that record every planned restart looked like a crash to the next daemon - 39 false
  "previous daemon died without a clean shutdown" warnings in one log, a forensic that was right
  0% of the time and sent every investigation after a crash that never happened.
  A genuinely unannounced death still warns, exactly as before.
  The successor also waits for the predecessor *process* rather than just for its port
  (`packages/daemon-runtime.md`), because the port frees before the last durable writes happen.
- Packaged `--daemon-child` re-enters the daemon entry inside a separate process; source mode
  uses `python -m swe_mux`. Packaged `--supervisor-child` mirrors the same split for the PTY
  supervisor; source mode uses `python -m swe_mux.supervisor`. The daemon discovers-or-spawns
  the supervisor itself, so the desktop shell never needs to know it exists.
- Project Actions have no swe-mux ConPTY root of their own in either mode: the step's shell (or
  its PATH-resolved program) is the root, spawned with the step's cwd and env as spawn fields.
  A `.cmd`/`.bat` shim goes through `%COMSPEC%`, which inherits the pseudoconsole correctly, so
  build tools still cannot detach into a visible external CMD window.
- The windowed executable emulates only allowlisted internal `-m` entrypoints for hook delivery
  and nested-agent launch. Arbitrary module dispatch is rejected.

## Key files

- Desktop runtime: `src/swe_mux/desktop.py`
- Desktop window-state validation and persistence: `src/swe_mux/desktop_window_state.py`
- Daemon runner: `src/swe_mux/__main__.py`
- Shutdown boundary: `src/swe_mux/server.py`
- Package metadata: `pyproject.toml`, `uv.lock`
- Bundle entries/spec: `packaging/desktop_entry.py`, `packaging/swe_mux.spec`
- Reproducible build: `packaging/build_desktop.py`
- Release archive writer (the artifact-name contract): `packaging/package_desktop_release.py`
- Windows installer: `packaging/installer/swe-mux.iss`, `packaging/build_installer.py`
- Installer tests: `tests/test_windows_installer.py`
- Frozen-app updater: `src/swe_mux/update_install.py`, `src/swe_mux/routes/update.py`
- Frontend overlay: `src/swe_mux/frontend_overlay.py`, `src/swe_mux/routes/frontend.py`,
  `packaging/build_frontend_overlay.py`, `frontend/src/frontendOverlay.ts`
- Frontend-overlay tests: `tests/test_frontend_overlay.py`,
  `tests/test_frontend_overlay_endpoints.py`, `tests/test_frontend_overlay_packaging.py`,
  `tests/test_cli_ui_overlay.py`, `frontend/test/frontendOverlay.test.ts`
- Bundle self-description and archive rules: `src/swe_mux/bundle_metadata.py`,
  `src/swe_mux/bundle_archive.py`
- Shared redeploy launch (endpoint and updater): `src/swe_mux/redeploy_launch.py`
- Updater tests: `tests/test_update_install.py`
- Closure license gate and notice generation: `packaging/license_audit.py`,
  `packaging/third_party_licenses.json`, `THIRD-PARTY-NOTICES.md`
- Lifecycle tests: `tests/test_desktop.py`
- License-gate tests: `tests/test_license_audit.py`

## Relates to

- `sessions.md`: daemon-owned terminals survive viewport closure.
- `remote-access.md`: tailnet access remains separate from loopback desktop shutdown.
- `ui.md`: the same browser UI runs inside WebView2.
