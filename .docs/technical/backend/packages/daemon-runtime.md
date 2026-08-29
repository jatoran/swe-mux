# Backend: the daemon process, packaging, and the desktop shell

Index: `../packages.md`.
Design: `../../../design/architecture.md`, `../../../design/features/desktop-shell.md`, `../../../design/features/remote-access.md`.

Each entry lists what the module owns, then **Not:** what it deliberately does not.

## Startup and supervision

### `__main__.py`

Daemon argument and config resolution, and the reusable aiohttp site lifecycle.
The listeners bind as soon as `AppRunner.setup()` returns, which is now immediately — the runtime is built behind them (`server.runtime_context`).
Mobile-voice Serve setup waits for `wait_runtime_ready` before running, because Serve reclamation asks whether a swe-mux daemon answers health on the port behind an existing route, and this daemon would answer "no" about itself for the whole of its own startup.
It also carries the two install-facing surfaces, both reading `install_location.py`.
`--where` is answered before the config is loaded, because a config that does not load is one of the states someone runs it in.
The first-run `PATH` hint prints once per start, only when the commands this install shipped are unreachable by name, and is logged and ledgered because the terminal it printed to is gone by the time anyone investigates.
`load_daemon_config` splits into parse and `resolve_daemon_config` for that ordering and keeps its old signature and contract for every other caller.

**Not:** desktop window or tray state.

### `startup_phases.py`

`StartupTimeline`: the named, timed phases of one daemon start, the watchdog that reports a phase still *running*, and the `snapshot()` the health endpoint serves while the build is in flight.
Phase transitions also go to `lifecycle.log`, so a long start reads as progress from outside the process.

**Not:** any decision. It measures; what a phase does and whether it may be deferred belongs to `server.py`.

### `background_tasks.py`

Supervision and health for the daemon's long-lived loops: the per-iteration fault guard, restart with capped backoff, and the per-loop health snapshot.
The rules callers must follow are in `runtime-rules.md`.

**Not:** PTY host processes (that is `supervisor.py`), or any domain logic.

### `lifecycle.py`

Daemon death forensics: the rewritten heartbeat record, the append-only lifecycle ledger, unclean-death detection at startup, clean-exit marking, and `planned_handoff`.

`planned_handoff` is what keeps the unclean-death report honest.
A clean exit is written *last*, after the whole teardown drain, and a planned restart never gets that far: `redeploy_desktop.py` asks the daemon to detach, watches health, and terminates the process about three seconds after it stops answering.
So the predecessor died with `clean_exit` false every time, and every successor reported a crash - 39 of them in one log, none real, which is worse than no warning at all.
The intent is now recorded when it is *decided*, by the endpoint that decides it (`POST /api/desktop/shutdown`, `POST /api/daemon/restart`), and a successor that finds `planned_intent` on the record reports the handoff at INFO instead of a crash at WARNING.
A record is not treated as proof of a clean shutdown; it only says which of the two stories the missing clean exit belongs to.

**Not:** logging configuration, or process supervision.

### `logsetup.py`

Rotating `daemon.log` and `access.log` handlers, the faulthandler `crash.log`, runtime root-logger level control, and the two things that make a line reconstructable rather than merely readable.

- **`StructuredFormatter` serializes `extra`.** The plain format string dropped every keyword a call site passed through `extra=`, so instrumentation that had been written years apart - `git_monitor`'s root and git exit code, `observation`'s session and elapsed seconds, `usage`'s source count - produced nothing at the sink.
  Fields are appended after the message as `key=value`, which is the shape the daemon already logs in by hand (`git_mutation_started operation_id=… cwd=…`), so one convention covers both and `daemon.log` stays greppable instead of becoming a file of JSON objects.
  A value holding a space, a quote, an `=` or a newline is JSON-quoted, so a line always parses back into the fields it came from and always stays one line; values are truncated at `MAX_EXTRA_VALUE_CHARS` so one oversized field cannot push a rotation's worth of real lines out of the file.
  The traceback stays last, after the fields, where a reader expects it.
- **`CorrelationFilter` stamps the in-flight request id.** `request_id_var` is a contextvar; `bound_request_id` binds it, and the correlation middleware does that per request.
  The filter goes on the *handlers*, not on the root logger: `Logger.handle` consults only the filters of the logger the call was made on, so one installed on root would look armed and stamp nothing a submodule logs.
  `access.log` keeps the plain formatter (aiohttp's `AccessLogger` passes its whole atom table through `extra=`, which would repeat every line's contents twice over) and gets the id through `ACCESS_LOG_FORMAT` instead.
- **A background loop mints its own id rather than logging anonymously.**
  The contextvar's default is empty, which is honest for daemon startup and for anything an external event triggered.
  A *bounded operation* with nothing above it is not that case, and `git-monitor`'s poll proved it: a Git query that timed out inside one logged with neither a request id (there is no request) nor an operation id (nothing passed one), leaving nothing to join it to the poll it came from.
  The git monitor's poll and the provider quota poll each `bound_request_id(new_request_id())` around one iteration, which stamps the loop's own lines *and* reaches every subprocess the iteration starts, including those inside its per-session tasks.
  It is bound rather than threaded through call signatures for that reason; a parameter would have had to be added to every intermediate.
- **`run_bounded`'s `operation_id` is read from the same contextvar at each call site** (`usage.py` passes the refresh's own id instead, since a refresh is the operation and already stamps its event with it).
  The parameter existed with no caller until W4.5.3, so every `bounded_command_timed_out` and output-cap line read `operation_id=None` beside a caller line carrying a real one.

**Not:** the supervisor's logging, which is stdlib-inline there to keep its import closure frozen; nor deciding *what* is worth logging, which belongs to the module doing the work.

### `errors.py`

`NotFound`, the typed domain refusal `error_middleware` answers 404 for.
It subclasses `KeyError` deliberately: the convention it replaces has catch sites as well as raise sites (`routes/diagnostics.py` falls back to a post-mortem view when a session does not resolve, `mcp.py` maps a miss onto its own scope error), and a new base class would have silently stopped every one of them catching what it was written to catch.
Only the raise sites moved; each catch site can narrow to `NotFound` later, one at a time, on purpose.

**Not:** transport. It knows nothing about aiohttp, which is what lets every layer below the routes raise it.

## Transport

### `app_keys.py`

Every handle the daemon publishes into its `web.Application`, as a typed `web.AppKey` rather than a string.
One module rather than per-route constants, because the key table *is* the contract between the composition root that writes a handle and the route modules that read it, and a second table would let the two disagree.
The types are annotations (`CONFIG: web.AppKey[Config]`) with the runtime type argument omitted deliberately: aiohttp uses that argument for `repr` only, and requiring it would make the key table import the whole daemon and put a cycle between it and the services it names.

**Not:** creating or owning any handle - it names them; and not request-scoped state, which belongs on the request.

### `http_support.py`

The transport primitives with no domain knowledge: the compact JSON response, response security headers, the loopback-peer check, one-shot task-failure logging, and the correlation-id names (`REQUEST_ID_HEADER`, the typed `REQUEST_ID_KEY` request slot, `ACCESS_LOG_FORMAT`).
The header and the access format live together so the two spellings of the same id cannot drift apart.
Outside `routes/` deliberately, because `preview_transport.py` streams its own response and needs the security headers, and a module below the route layer reaching up into `routes/` would invert the dependency direction the package boundary states.

**Not:** the middlewares themselves, static-asset cache headers, or resolving a request to the Project or session it names (`routes/support.py`).

### `runtime_config.py`

Applying a changed config to a live runtime without a restart: the config-file mtime probe, the watch loop that notices an external edit, the field set that invalidates LLM readiness, and `apply_runtime_config`, which pushes each changed hot field onto the handle that owns it.

Its callers are the composition root (the watch loop) and every route that writes config - `PATCH /api/config`, the configurator's settings write, a grant, and a Project ignore write - which is why it is a module rather than a private helper of any one of them.

**Not:** validation or the write itself (`config.update_config`), which fields are hot rather than restart-required (also `config.py`), or the event that tells attached clients (`server.py` emits `settings_changed`).

### `server.py` startup

- **Bind first, build behind it.** `runtime_context` returns at once and the runtime is constructed by a background task (`_build_runtime`), so the socket is open for the whole start.
  Until it finishes, `/api/health` answers HTTP 503 with the phase in flight and the phases already done, and `starting_middleware` refuses every other route with the same body — `STARTUP_OPEN_PATHS`/`STARTUP_OPEN_PREFIXES` are the exceptions, and they read nothing but `frontend_dir`.
  `wait_runtime_ready(app)` is how a caller that needs a *built* daemon waits for one; a started server is only a reachable one.
- A build that fails records the failure on the timeline and sets the daemon stop event.
  This is not defensiveness: while the build ran inline, an exception propagated out of `AppRunner.setup()` and the process died, which the tray and the redeploy script both already handle — a daemon left serving 503 forever would be worse than the crash it replaced.
- `publish(app, {keys.X: handle, ...})` writes handles into the started (frozen) application. aiohttp deprecates state writes after the runner starts, on the assumption that every handle exists before the socket does; the daemon inverts that ordering deliberately, and this keeps the coupling to one line, pinned by `tests/test_startup_gate.py`.
- `_teardown_runtime` reads every handle back out of `app` and treats each as optional, because a shutdown can now arrive with the runtime half-built.
  **Every read is by `AppKey`, never by name, and `_stop_handle`/`_close_handle` take the application and the key so that mypy enforces it.**
  An `AppKey` defines no `__eq__` and no `__hash__`, so it is hashed by identity: `app.get("provider_accounts")` against an app that published `keys.PROVIDER_ACCOUNTS` is a miss returning `None`, not another spelling of the same lookup.
  This teardown kept its string names through the move to `AppKey` and every one of its stop/close lines became a silent no-op for a week - no store closed and no service stopped at any shutdown - with a single visible trace anywhere: `ProviderAccountManager` is the one skipped service holding a socket, so aiohttp's finalizer printed "Unclosed client session" against whatever ran next.
  Optional-by-design is what made it silent, which is why the invariant is asserted rather than remembered (`tests/test_shutdown_teardown.py` stubs every key the teardown names and requires each stub to be reached; `tests/support/client_sessions.py` fails the live-tier test that leaks a `ClientSession` instead of leaving it to a finalizer).
  Cancelling a task is not the same as stopping the work it started: a one-shot task waiting on `asyncio.to_thread` leaves its worker running, and the loop joins that worker in `shutdown_default_executor` after this function has already returned.
  The startup native-history reconcile is cancelled through `scan_external_transcripts_async`, which hands the worker a token it polls per file, so the walk ends here rather than after teardown (`development/PERFORMANCE_RUNBOOK.md` § Traps).
- **The handoff between two daemons is a shared window, not two independent lifetimes**, and both halves of it are handled here.
  `runner.cleanup()` closes the listener *first* and only then runs `_teardown_runtime`, so the port frees several seconds before the last durable writes happen - the terminal ledger, the recovery rows, and exactly the telemetry that would explain the restart.
  A successor that started the moment the port opened spent that whole window holding `mux.db` for its own integrity check and schema work, and the predecessor's writes came back `database is locked` and were dropped: ten of them across a measured 2026-08-23 restart, in four subsystems, silently.
  So the successor's start gate has two halves (`__main__.wait_for_port_free`, then `wait_for_predecessor_exit`, which reads the pid off the heartbeat record and waits up to 20s, bounded, warning rather than refusing);
  and `_teardown_runtime` opens with `sqlite_store.begin_shutdown_drain()`, which widens the busy timeout for whatever is left of an 8s budget and makes any write still lost to a lock log `sqlite_write_lost` naming the store and the method (`technical/backend/sqlite.md`).
  The ordering fix is the real one; the drain is the second line for the redeploy path, where the predecessor is terminated about three seconds after health stops answering and does not get to choose how long it lives.
- The middleware chain is `correlation → error → security → starting → compression`.
  `correlation_middleware` is outermost so that the two refusals that never reach a handler - `unsupported Host` and `daemon_starting` - carry a request id too, and so that anything the middlewares below it log is correlated with it.
  It binds a contextvar rather than passing an argument down, because `asyncio.create_task` and `asyncio.to_thread` both inherit the context: the background work a handler starts stays correlated after the response has been written, which is the span an incident covers.
- What may be deferred is decided by whether serving depends on it, and the reason is recorded at each site. Deferred: process-ownership restore (a full psutil sweep, measured 20.7s cold / 6.0s warm over 482 processes; the inspector's own poll refreshes it forever afterwards, and `start()` lives inside the deferring task so the poll cannot run against a half-restored map).
  Deliberately not deferred: the historical provider-collision reconcile (it hides false runs from the first request), the provider-account reconcile (system auth is authoritative, `architecture.md` invariant 10), and every `restore()` whose loop is started immediately after it.

### Event and PTY transport

`routes/pty.py` serves both WebSockets; the module map is `routes.md`.

- UI identity hello before event recovery, cold event watermarks, 64-record reconnect recovery, and snapshot fallback for wider gaps.
- Browser omission of audit-only hook payloads.
- Delta attach for reconnecting terminals: a handshake-first replay decision, `since`-validated missed-bytes replay, and `replay_end` position anchors, with `Session.attach_and_subscribe` owning the coverage rule.
- Zero-delay batching of already-queued PTY output.
- Content-free physical-input acknowledgements and allowlisted per-phase client latency diagnostics.

Worktree transport - integrated or split create, setup, and spawn orchestration, and configured-root-bounded parent creation - is `routes/git.py`; the removal transaction it calls is `worktree_mutation.py`.

**Not:** durable event storage, browser state ownership, or transport-level compression.

### `network_usage.py`

Daemon-boot and reset-window HTTP encoded-body and WebSocket application-frame counters by bounded route, channel, and peer, including the non-additive sent-PTY payload-phase breakdown; compact JSON responses; dynamic text compression; and metered WebSocket responses.

**Not:** packet, TLS, Tailscale, and HTTP-header overhead; durable raw request logs; or quotas.

## Packaging and build identity

### `ui_build.py`

Strict production build-id parsing from the served `index.html`, plus stat-keyed lookup caching that observes source-mode rebuilds without a daemon restart.

**Not:** generating the identity, or deciding whether a browser reloads.

### `build_support.py`

Lock-safe staged frontend publication for desktop packaging, and the gzip sidecars the static tree is served from.

`precompress_static` is the daemon's `static-precompress` startup phase, in a thread because it is CPU-bound.
It exists because a distribution carries no `.gz` at all - they were 4.43 MiB of a 12.70 MiB wheel, re-compressing what the zip container had already compressed - while aiohttp's `add_static` does no on-the-fly compression, so without them a phone over Tailscale fetches the 10.7 MiB ONNX runtime uncompressed.
Measured: 2.02 s for all 40 on the first start after an install, 0.03 s and no writes on every start after that.
Whether a sidecar is current is a **content** question and never a timestamp one: gzip records the CRC-32 and the length of what it was made from, so eight bytes off the sidecar against one CRC pass over the source settles it exactly, with no manifest to drift and no dependence on a host's timer granularity.
A sidecar whose source is gone is swept; a tree it cannot write is counted and logged, never raised, because a read-only install is a slower UI and not a daemon that refuses to start.
The temporary carries the pid, so two daemons sharing one source tree cannot interleave into one file.

`frontend/scripts/compress-static.mjs` is the *other* producer and is not redundant: it keeps a `npm run build` from leaving the previous build's `index.html.gz` beside fresh content-hashed assets, which is a blank screen on a daemon nothing in that loop restarts.
`test_desktop.py::test_the_python_and_node_precompressors_agree_on_the_rule` compares the two definitions of which files earn one.

**Not:** Vite compilation, or runtime asset serving.

### `bundle_locks.py`

Who would block the frozen-bundle swap: exe and cwd anchors into `dist/swe-mux` from processes the redeploy cannot stop, excluding the app's own image and its descendants.
Shared by the redeploy script's pre-build and pre-stop gates and by the endpoint's `409 bundle_in_use`.

**Not:** stopping anything, since it reports only, or the swap itself (`packaging/redeploy_desktop.py`).

### `packaging/license_audit.py`

The metadata half of the distribution license gate, and the generator behind `THIRD-PARTY-NOTICES.md`.
Resolves the *distributed* closure: `uv.lock` walked from the runtime dependencies plus every extra in `DISTRIBUTED_EXTRAS` (`desktop` and `voice-local`), with dev groups excluded so build-only `pyinstaller` (GPL-2.0-with-exception) never cries wolf.
Dependency markers are evaluated against every supported platform rather than the running one, because the Linux artifact carries Linux-only packages whatever host the audit runs on.
The walk is defined over that declared set rather than over what is installed, which is what keeps the answer independent of the syncing machine: `num2words` reaches the closure only through `voice-local`, so a walk over a bare `uv sync` would report a copyleft-free bundle that ships it anyway.
Licenses come from installed `dist-info`, falling back to sniffing the shipped license text when a PEP 639 package declares nothing; an undeterminable license fails the gate rather than passing as permissive.
`--write` needs the full closure installed and refreshes both the notices file and the machine-readable sidecar `packaging/third_party_licenses.json`; `--check` needs no environment at all and reconciles that sidecar against both lockfiles, so a dependency entering or moving fails on any machine.
Copyleft ships only with an `ALLOWLIST` entry naming the reason and the relink story - `pystray` and `num2words` today, both LGPL.

**Not:** inspecting the built bundle (`build_desktop.verify_bundle_licenses` owns the artifact half, because declared metadata is exactly what hid the original PyAV defect), not deciding whether the build environment can produce a compliant bundle (`build_desktop.verify_build_extras_installed`), and not choosing swe-mux's own license.

## Desktop shell

### `desktop.py`

Windows tray and WebView lifecycle, single instance, login startup, and daemon child supervision.
It is a `[project.gui-scripts]` entry, so its launcher opens no console and the process has `sys.stdout is None` and `sys.stderr is None`; `redirect_gui_streams` runs first in `main` and points both at `<data_dir>/desktop-shell.log`, and `report_launch_failure` puts a startup failure in a message box, in the lifecycle ledger, and in that log with its traceback.
Without those three the entry-point choice would be a net loss, since invisible is worse than a stray console window - `argparse` alone dies on `None.write` inside its own error reporter.

**Not:** PTYs, HTTP composition, or Project and session state.

### `desktop_window_state.py`

Versioned normal-window bounds, maximized state, debounced atomic persistence, and current-monitor fitting.

**Not:** WebView creation, tray actions, or browser workspace layout.

## Remote reachability

### `tailscale.py`

Direct-tailnet discovery and status, ephemeral certificate preparation for the daemon's direct private HTTPS listener, and the pure connection-state classifier `classify_tailscale_connection` (not-installed, logged-out, or connected-as-`<device>`, from `BackendState` plus `Self.DNSName`).

**Not:** ACL or policy changes, Serve or Funnel enablement, or browser permission.

### `windows_firewall.py`

Windows-only, frozen-build-gated Defender Firewall inspect and repair for the tailnet listener: the tailnet-scope sufficiency check (`100.64.0.0/10`), the pure `interpret_inspection`, the PowerShell inspect, repair, and elevation script builders, and the injectable runner.

**Not:** non-Windows and source-build behavior (`firewall_supported` gates it off), any rule not owned by swe-mux's own program path, or the phone's own firewall.

### `update_check.py`

The release update check, in three layers.
The pure PEP 440 comparison (`parse_version`, `compare_versions`, `is_newer`), which returns "cannot tell" rather than an order when a version does not parse.
The schema-gated `parse_manifest` and `parse_github_release` reductions, which refuse an unrecognized `schema` before reading a field.
And `UpdateChecker`, which owns the daily interval persisted in `<data_dir>/update-check.json`, the bounded cookie-free fetch, the GitHub Releases fallback, per-version dismissal, and the snapshot the route serves.
It is the **only** module that reaches the network on swe-mux's own behalf.
`update_check_enabled` gates it, and off means no request is made at all - which `tests/test_update_check.py` proves by counting fetches.

**Not:** downloading, hash-verifying, staging, or installing anything (`update_install.py`); raising on any path; blocking startup - the loop is supervised as `update-check` and takes its first look 60s in.
It parses the manifest's `artifacts` for that module but neither persists nor serves them: a stored hash is a claim about bytes nobody is holding, and the release workflow re-uploads with `--clobber`.

### `update_install.py`

The frozen-app updater, in five steps.
Install-kind detection (`sys.frozen` plus the executable's own directory, never the presence of a `dist/` beside a checkout) and the release-artifact naming contract (`release_archive_name`, `release_platform_tag`).
Then the streaming bounded download hashed as it arrives, the promotion of a `.part` file only on a matching digest, the supervisor-protocol gate, and the handoff to `packaging/redeploy_desktop.py --from-archive`.
Its refusal vocabulary is closed and durable in `<data_dir>/update-install.json`, because the daemon does not survive the swap it starts.
Every phase transition is logged with the attempt's `install_id` and persisted at the moment of decision.

**Not:** the swap (`packaging/redeploy_desktop.py`, unchanged except for the flag), the archive's shape rules or extraction (`bundle_archive.py`), what a bundle claims about itself (`bundle_metadata.py`), or the decision that an update exists at all (`update_check.py`).
It never updates `dist/swe-mux-supervisor/`: that reaps every live session, so a release needing it is refused with the manual flow named.

### `bundle_metadata.py` / `bundle_archive.py`

What a built bundle says about itself (`bundle.json`: schema, version, `supervisor_protocol`, platform, build stamp) and the rules for reading a release archive.
Split from the updater because two processes need them - the daemon interrogates the archive before deciding anything, and the redeploy script re-validates and extracts it minutes later in its own process, so a rule enforced in only one of them is a rule the other does not have.
An archive is exactly one top-level `swe-mux/` directory; an absolute path, a drive letter, a `..` segment, or a second root is refused rather than normalized, because a hash proves which file arrived and nothing about what extracting it would write.

**Not:** deciding what to do about a mismatch (that is the updater's refusal, with the message an operator can act on).

### `redeploy_launch.py`

How `packaging/redeploy_desktop.py` gets started, shared by `POST /api/daemon/redeploy` and the updater's handoff.
Three things: the source-checkout resolution (`redeploy_source_root`, with `PACKAGE_DIR` anchored on the package rather than counted from a file), the atomically claimed `redeploy.lock` naming the script process, and the detached breakaway spawn with the parent-Claude markers scrubbed and the cwd kept out of `dist/`.

**Not:** whether a redeploy may run - the preconditions differ between the two callers (a bundle-holder scan for one, a supervisor-protocol gate for the other), so each owns its own refusals.

## Operator surfaces

### `prerequisites.py`

The onboarding presence check for Git, Node, npm, and Tailscale, each with what it backs and a next step (`detect_prerequisites`), resolved through `which_real`.

**Not:** installing anything, version comparison (that is the harness CLI-drift signal), or non-tool prerequisites like the OpenRouter key.

### `doctor.py`

The pure assembly behind `mux doctor` and `GET /api/diagnostics/doctor`.
`build_doctor_report` turns already-fetched diagnostic payloads into a flat `checks[]` list with per-check status, severity, and remedy plus a machine-readable capability block.
`observation_freshness` projects the fleet's stale, relocated, and sibling-blocked agent sessions from the status fields the state-log exposes.

**Not:** fetching anything (the server handler gathers the payloads), any mutation, new detection logic, or printing secrets or terminal or message content.

### `doctor_local.py`

The degraded `mux doctor` report, run by the CLI when no daemon answers.
It covers the install-integrity faults that stop a daemon starting: where the copy is installed and whether its commands are on `PATH`, the Python floor, `swe_mux.server`'s import graph, the config file, the frontend bundle in the installed package, the data directory, `mux.db`, the configured port, the host PTY backend, the frozen supervisor bundle, and each optional extra.
The two install rows come first, because they are the only faults whose symptom is nothing at all and every later check presupposes the reader found a way to run something.
Prerequisite, harness, and first-use-asset rows are produced by `doctor.py`'s own builders over the same detection functions, so there is one implementation of them rather than two that can disagree.
The asset probes need no daemon even though the route reads them off the live `VoiceService`: `capture_capability()` is an import plus a filesystem read, and both model stores answer from a data directory.
Every check it does not run is emitted as an `unchecked` row naming what is unknown and why - a status that exists because "not measured" is neither healthy nor absent, and collapsing it into either is what turns a degraded report into a confident wrong one.

**Not:** anything reading daemon runtime state (that is what the `unchecked` rows are for), a second copy of a check `doctor.py` already builds, or any write into the data directory beyond a removed temporary file proving it is writable.
Nor a bind test for the port probe - it is a TCP connect, because `SO_REUSEADDR` would let a diagnostic take a port from its owner.
Nor a supervisor-bundle *currency* check: `supervisor_bundle_current()` reports a false stale without PyInstaller, and acting on that answer reaps every live session.

### `cli.py`

The `mux` control surface: config-based URL resolution with `MUX_URL` precedence, stable-id/name/prefix session resolution with ambiguity conflicts, actionable exit codes, human tables with an explicit `--json`, registry-driven harness choices, and the scriptable spawn, ls, kill, resume, reload, and doctor operations routed through the daemon's typed endpoints.
`doctor` is the one command that answers when the daemon does not: an unreachable daemon falls back to `doctor_local`, rendered by the same `_render_doctor`.
Its local-only preamble, `[????]` mark, and `unchecked` tally are each conditioned on a field the daemon payload does not carry, so the bytes that path prints are unchanged.
Its exit code composes the existing two rather than adding a scheme - `1` for a failing local check, `3` for a clean degraded one, never `0`.

`install-shortcut` is the one subcommand that reaches no daemon, because the person who needs it is the one whose install produced no way to start one.

**Not:** new client-side authorization or business logic, since actions route through the daemon ops; browser presentation actions; or accepting or printing a provider secret.

### `install_location.py`

Where this copy of swe-mux is installed and whether its commands can be reached: the install method (frozen, `uv tool`, `pipx`, virtual environment, system), the launcher directory, the tool-owned shim directory when one of ours is actually in it, and per command both its own path and what the bare name resolves to on `PATH`.
It is the single source for the first-run hint `muxd` prints, `python -m swe_mux --where`, the `install.location` / `install.path` rows in `doctor_local.py`, and the target `shortcuts.py` points at, so the four cannot disagree about one filesystem.
Every input is an argument with a live default - platform, `PATH`, scripts directory, environment, and the existence probe - so a Windows layout is described and asserted from any host.
`extra_install_command` is the other thing it answers, added 2026-08-28: the command that adds an optional extra to *this* copy.
Every diagnostic used to say `uv sync --extra <name>`, which needs a `pyproject.toml` and a `uv.lock` beside it - so the one audience most likely to read it, somebody who installed from PyPI and is looking at a capability that is simply not there, could not run it at all, and a remedy that cannot be run ends the search instead of continuing it.
`source_checkout` is what `_detect_kind` cannot answer, because a `uv sync`ed checkout and a `pip install swe-mux` into a virtualenv are both `INSTALL_VENV` and take opposite advice; it is decided by layout (`<root>/src/swe_mux` beside a `pyproject.toml`), which is the only thing that actually differs.

**Not:** changing anything, probing whether swe-mux *works*, or collapsing "the launcher is absent" into "the launcher is unreachable", which are different faults with different fixes.
Nor reporting an install that shipped no commands as reachable: `all()` over an empty set is vacuously true and would answer the question backwards.

### `shortcuts.py`

Windows Start Menu, Desktop, and `shell:startup` shell links for the desktop app, created and removed by `mux install-shortcut` - the thing a wheel structurally cannot do, since `pip` and `uv` have no post-install hook.
Known-folder destinations through `SHGetKnownFolderPath`, because a redirected Desktop makes `%USERPROFILE%\Desktop` the wrong directory and a link written there is invisible.
`.lnk` authoring goes through PowerShell's `WScript.Shell`, so no dependency is added to reach the COM object.
The plan and the script builder are pure with an injectable runner, and the write is idempotent, reporting `created`/`updated`/`unchanged`/`removed`/`absent`/`failed` per slot with its absolute path, also appended to the lifecycle ledger.
The icon is rendered once from `desktop.create_tray_image` into `<data_dir>/icons/`, because `packaging/swe-mux.ico` is build output under `packaging/` and the wheel carries no `.ico` at all; a frozen bundle uses index 0 of its own executable instead.

**Not:** a POSIX equivalent - it reports unsupported with the reason and writes nothing rather than failing obscurely - and not deciding where swe-mux is installed, which is `install_location.py`'s answer.
