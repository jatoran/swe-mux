# Backend: processes, Previews, clipboard, and devices

Index: `../packages.md`.
Design: `../../../design/features/processes-and-previews.md`, `../../../design/features/ghost-windows.md`, `../../../design/features/device-presence.md`, `../../../design/features/notifications.md`.

Each entry lists what the module owns, then **Not:** what it deliberately does not.

Periodic psutil work in this area is the daemon's largest measured cost centre; the sampling rules and the measurements behind them are in `runtime-rules.md`.

## `processes.py`

- Normalized whole-system CPU sampling.
- Creation-causal descendant inspection and actions, and versioned parent-walk and Job provenance.
- Infrastructure reservation and ownership-conflict quarantine.
- Project-wide loopback registration, discovery, listener attribution, and route maps.
- The reduced fleet projection for the browser watch.
- The `background_tasks` fast-clear, since a descendant older than the annotation cannot be its task.

**Not:** proxy transport, authoritative ownership from PID alone, or deciding a process *is* a background task - it may only refute.

Preview rules it enforces:

- Preview registration identity is the Project endpoint, not the clicked terminal.
- Listener ownership is resolved across live sessions before attachment.
- Automatic discovery creates route-only identities for cross-service traffic.
- A bounded HTML probe or an explicit registration promotes an identity into the listed Preview inventory.
- Negative probes are cached by listener process identity and backed off, so a UI refresh does not create a request loop against tool listeners.
- The iframe sandbox is never weakened, and a browser never dials raw loopback for cross-service traffic.

## `ghost_windows.py`

Windows-only detection and off-screen parking of headless-browser windows that DWM composites while Win32 reports them hidden, plus the conjunctive sweep predicate and its memoized command-line verdicts.

**Not:** closing or terminating any browser, session state, non-Windows behavior, or which browser stack an agent chooses.

## `preview_capture.py`

The optional headless preview screenshot (Playwright), typed-unavailable.

**Not:** proxy transport, or PTY writes.

## `preview_store.py`

The approved-preview mirror at `<data_dir>/previews.json`: load-at-startup, a whole-file rewrite on change, and dropping stored rows that cannot route.
Only *approved* registrations are mirrored, because detected ones are rediscovered from the live listener set under the same derived id and mirroring them could only go stale.

**Not:** being authoritative at runtime (`PreviewRegistry.items` is), minting ids (`processes.preview_id` does), or ever failing loudly enough to stop the daemon starting.

## `clipboard_store.py`

The in-memory clipboard-history ring - dedupe by content hash, pins, count and time bounds, secret-shape refusal - plus its opt-in SQLite mirror.

**Not:** reading or polling the OS clipboard, or deciding where inserted text goes.

## `device_presence.py`

Which device class the human is at: per-`/events`-connection visible and focused state plus interaction age, aggregated to active device classes plus the *leading* one (most recently touched, which breaks the routine both-active tie), and the "did anyone touch another device since this alert" question a deferred push turns on.

It has two consumers for the same reason: notification routing and terminal-input arbitration both have to answer "is the user somewhere else", and neither can from its own per-subscription or per-session state.
It fails open on every staleness path.

**Not:** push subscriptions, delivery, settings, or terminal ownership.

## `push.py`

VAPID identity, subscriptions, per-endpoint focus presence, event-to-notification classification including the running-work and startup suppressions, stable route verdict and reason codes, decision-ledger emission, and both hold lifecycles - the `waiting` settle and the other-device deferral.

**Not:** durable decision storage (`operational_telemetry.py`), which device is active (`device_presence.py`), notification preferences (`settings_store.py`), or what counts as running work - `session.RUNNING_ACTIVITY_KINDS` owns that set; this module restates it and a test pins them equal.
