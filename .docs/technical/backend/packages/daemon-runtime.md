# Backend: the daemon process, packaging, and the desktop shell

Index: `../packages.md`.
Design: `../../../design/architecture.md`, `../../../design/features/desktop-shell.md`, `../../../design/features/remote-access.md`.

Each entry lists what the module owns, then **Not:** what it deliberately does not.

## Startup and supervision

### `__main__.py`

Daemon argument and config resolution, and the reusable aiohttp site lifecycle.

**Not:** desktop window or tray state.

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
