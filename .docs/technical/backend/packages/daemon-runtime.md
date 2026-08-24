# Backend: the daemon process, packaging, and the desktop shell

Index: `../packages.md`.
Design: `../../../design/architecture.md`, `../../../design/features/desktop-shell.md`, `../../../design/features/remote-access.md`.

Each entry lists what the module owns, then **Not:** what it deliberately does not.

## Startup and supervision

### `__main__.py`

Daemon argument and config resolution, and the reusable aiohttp site lifecycle.
The listeners bind as soon as `AppRunner.setup()` returns, which is now immediately — the runtime is built behind them (`server.runtime_context`).
Mobile-voice Serve setup waits for `wait_runtime_ready` before running, because Serve reclamation asks whether a swe-mux daemon answers health on the port behind an existing route, and this daemon would answer "no" about itself for the whole of its own startup.

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

Daemon death forensics: the rewritten heartbeat record, the append-only lifecycle ledger, unclean-death detection at startup, and clean-exit marking.

**Not:** logging configuration, or process supervision.

### `logsetup.py`

Rotating `daemon.log` and `access.log` handlers, the faulthandler `crash.log`, and runtime root-logger level control.

**Not:** the supervisor's logging, which is stdlib-inline there to keep its import closure frozen.

## Transport

### `app_keys.py`

Every handle the daemon publishes into its `web.Application`, as a typed `web.AppKey` rather than a string.
One module rather than per-route constants, because the key table *is* the contract between the composition root that writes a handle and the route modules that read it, and a second table would let the two disagree.
The types are annotations (`CONFIG: web.AppKey[Config]`) with the runtime type argument omitted deliberately: aiohttp uses that argument for `repr` only, and requiring it would make the key table import the whole daemon and put a cycle between it and the services it names.

**Not:** creating or owning any handle - it names them; and not request-scoped state, which belongs on the request.

### `server.py` startup

- **Bind first, build behind it.** `runtime_context` returns at once and the runtime is constructed by a background task (`_build_runtime`), so the socket is open for the whole start.
  Until it finishes, `/api/health` answers HTTP 503 with the phase in flight and the phases already done, and `starting_middleware` refuses every other route with the same body — `STARTUP_OPEN_PATHS`/`STARTUP_OPEN_PREFIXES` are the exceptions, and they read nothing but `frontend_dir`.
  `wait_runtime_ready(app)` is how a caller that needs a *built* daemon waits for one; a started server is only a reachable one.
- A build that fails records the failure on the timeline and sets the daemon stop event.
  This is not defensiveness: while the build ran inline, an exception propagated out of `AppRunner.setup()` and the process died, which the tray and the redeploy script both already handle — a daemon left serving 503 forever would be worse than the crash it replaced.
- `publish(app, {keys.X: handle, ...})` writes handles into the started (frozen) application. aiohttp deprecates state writes after the runner starts, on the assumption that every handle exists before the socket does; the daemon inverts that ordering deliberately, and this keeps the coupling to one line, pinned by `tests/test_startup_gate.py`.
- `_teardown_runtime` reads every handle back out of `app` and treats each as optional, because a shutdown can now arrive with the runtime half-built.
  Cancelling a task is not the same as stopping the work it started: a one-shot task waiting on `asyncio.to_thread` leaves its worker running, and the loop joins that worker in `shutdown_default_executor` after this function has already returned.
  The startup native-history reconcile is cancelled through `scan_external_transcripts_async`, which hands the worker a token it polls per file, so the walk ends here rather than after teardown (`development/PERFORMANCE_RUNBOOK.md` § Traps).
- What may be deferred is decided by whether serving depends on it, and the reason is recorded at each site. Deferred: process-ownership restore (a full psutil sweep, measured 20.7s cold / 6.0s warm over 482 processes; the inspector's own poll refreshes it forever afterwards, and `start()` lives inside the deferring task so the poll cannot run against a half-restored map).
  Deliberately not deferred: the historical provider-collision reconcile (it hides false runs from the first request), the provider-account reconcile (system auth is authoritative, `architecture.md` invariant 10), and every `restore()` whose loop is started immediately after it.

### `server.py` event and PTY transport

- UI identity hello before event recovery, cold event watermarks, 64-record reconnect recovery, and snapshot fallback for wider gaps.
- Browser omission of audit-only hook payloads.
- Delta attach for reconnecting terminals: a handshake-first replay decision, `since`-validated missed-bytes replay, and `replay_end` position anchors, with `Session.attach_and_subscribe` owning the coverage rule.
- Zero-delay batching of already-queued PTY output.
- Content-free physical-input acknowledgements and allowlisted per-phase client latency diagnostics.
- Integrated or split create-worktree, setup, and spawn transport orchestration, with configured-root-bounded parent creation, exact repair and remove post-state validation, and atomic quarantine of unregistered directory remnants.

**Not:** durable event storage, browser state ownership, or transport-level compression.

### `network_usage.py`

Daemon-boot and reset-window HTTP encoded-body and WebSocket application-frame counters by bounded route, channel, and peer, including the non-additive sent-PTY payload-phase breakdown; compact JSON responses; dynamic text compression; and metered WebSocket responses.

**Not:** packet, TLS, Tailscale, and HTTP-header overhead; durable raw request logs; or quotas.

## Packaging and build identity

### `ui_build.py`

Strict production build-id parsing from the served `index.html`, plus stat-keyed lookup caching that observes source-mode rebuilds without a daemon restart.

**Not:** generating the identity, or deciding whether a browser reloads.

### `build_support.py`

Lock-safe staged frontend publication for desktop packaging.

**Not:** Vite compilation, or runtime asset serving.

### `bundle_locks.py`

Who would block the frozen-bundle swap: exe and cwd anchors into `dist/swe-mux` from processes the redeploy cannot stop, excluding the app's own image and its descendants.
Shared by the redeploy script's pre-build and pre-stop gates and by the endpoint's `409 bundle_in_use`.

**Not:** stopping anything, since it reports only, or the swap itself (`packaging/redeploy_desktop.py`).

### `packaging/license_audit.py`

The metadata half of the distribution license gate, and the generator behind `THIRD-PARTY-NOTICES.md`.
Resolves the *distributed* closure - `uv.lock` walked from the runtime dependencies plus the `desktop` extra, dev groups excluded so build-only `pyinstaller` (GPL-2.0-with-exception) never cries wolf - with dependency markers evaluated against every supported platform rather than the running one, because the Linux artifact carries Linux-only packages whatever host the audit runs on.
Licenses come from installed `dist-info`, falling back to sniffing the shipped license text when a PEP 639 package declares nothing; an undeterminable license fails the gate rather than passing as permissive.
`--write` needs the full closure installed and refreshes both the notices file and the machine-readable sidecar `packaging/third_party_licenses.json`; `--check` needs no environment at all and reconciles that sidecar against both lockfiles, so a dependency entering or moving fails on any machine.
Copyleft ships only with an `ALLOWLIST` entry naming the reason and the relink story - `pystray` and `num2words` today, both LGPL.

**Not:** inspecting the built bundle (`build_desktop.verify_bundle_licenses` owns the artifact half, because declared metadata is exactly what hid the original PyAV defect), and not choosing swe-mux's own license.

## Desktop shell

### `desktop.py`

Windows tray and WebView lifecycle, single instance, login startup, and daemon child supervision.

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

## Operator surfaces

### `prerequisites.py`

The onboarding presence check for Git, Node, npm, and Tailscale, each with what it backs and a next step (`detect_prerequisites`), resolved through `which_real`.

**Not:** installing anything, version comparison (that is the harness CLI-drift signal), or non-tool prerequisites like the OpenRouter key.

### `doctor.py`

The pure assembly behind `mux doctor` and `GET /api/diagnostics/doctor`.
`build_doctor_report` turns already-fetched diagnostic payloads into a flat `checks[]` list with per-check status, severity, and remedy plus a machine-readable capability block.
`observation_freshness` projects the fleet's stale, relocated, and sibling-blocked agent sessions from the status fields the state-log exposes.

**Not:** fetching anything (the server handler gathers the payloads), any mutation, new detection logic, or printing secrets or terminal or message content.

### `cli.py`

The `mux` control surface: config-based URL resolution with `MUX_URL` precedence, stable-id/name/prefix session resolution with ambiguity conflicts, actionable exit codes, human tables with an explicit `--json`, registry-driven harness choices, and the scriptable spawn, ls, kill, resume, reload, and doctor operations routed through the daemon's typed endpoints.

**Not:** new client-side authorization or business logic, since actions route through the daemon ops; browser presentation actions; or accepting or printing a provider secret.
