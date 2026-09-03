# Backend: processes, Previews, clipboard, and devices

Index: `../packages.md`.
Design: `../../../design/features/processes-and-previews.md`, `../../../design/features/ghost-windows.md`, `../../../design/features/device-presence.md`, `../../../design/features/notifications.md`, `../../../design/features/operational-telemetry.md`.

Each entry lists what the module owns, then **Not:** what it deliberately does not.

Periodic psutil work in this area is the daemon's largest measured cost centre; the sampling rules and the measurements behind them are in `runtime-rules.md`.

## `processes.py`

- Normalized whole-system CPU sampling.
- Creation-causal descendant inspection and actions, and versioned parent-walk and Job provenance.
- Infrastructure reservation and ownership-conflict quarantine.
- Project-wide loopback registration, discovery, listener attribution, and route maps.
- Static document registration (`register_static`) and its derived route id (`static_preview_id`).
- The reduced fleet projection for the browser watch, built in one pass over the owned processes: `snapshot_all` indexes `session_id -> processes` once and serializes each process once, rather than re-scanning every owned process per session.
- The `background_tasks` fast-clear, since a descendant older than the annotation cannot be its task.

**Not:** proxy transport, authoritative ownership from PID alone, or deciding a process *is* a background task - it may only refute.

Preview rules it enforces:

- Preview registration identity is the Project endpoint, not the clicked terminal.
- Listener ownership is resolved across live sessions before attachment.
- Automatic discovery creates route-only identities for cross-service traffic.
- A bounded HTML probe or an explicit registration promotes an identity into the listed Preview inventory.
- Negative probes are cached by listener process identity and backed off, so a UI refresh does not create a request loop against tool listeners.
- The iframe sandbox is never weakened, and a browser never dials raw loopback for cross-service traffic.
- `kind` distinguishes a `loopback` registration from a `static` one, and every rule that differs between them is gated on that field rather than on an empty session id. A static registration is unowned, never pruned (it has no listener whose absence could mean anything), and absent from the cross-service route map (its `file://` url names bytes, not a service).

## `ghost_windows.py`

Windows-only detection and off-screen parking of headless-browser windows that DWM composites while Win32 reports them hidden, plus the conjunctive sweep predicate and its memoized command-line verdicts.

**Not:** closing or terminating any browser, session state, non-Windows behavior, or which browser stack an agent chooses.

## `preview_capture.py`

The optional headless preview screenshot (Playwright), typed-unavailable.

`capture_capability()` is the single owner of *which* of the three states this install is in - `ready`, `extra_missing` (no Playwright package), `browser_missing` (Playwright present, no Chromium binary under any browsers root) - each with the command for that half and nothing else.
Both readings are local: an import and a filesystem scan of `PLAYWRIGHT_BROWSERS_PATH` / the per-host `ms-playwright` cache / Playwright's in-package `.local-browsers`.
The scan can be wrong in one direction (a browsers root this host uses that it does not know about), so `capture_loopback` promotes Playwright's own launch error into the same `browser_missing` state rather than letting it surface as an unactionable failure.

Nothing here downloads a browser, and that is the rule rather than an omission: `playwright install chromium` is a large network fetch, and a daemon that runs it because someone pressed Capture is exactly the silent first-use cost this reporting exists to remove.

**Not:** proxy transport, PTY writes, or installing either half of its own backend.

## `preview_store.py`

The approved-preview mirror at `<data_dir>/previews.json`: load-at-startup, a whole-file rewrite on change, and dropping stored rows that cannot route.
Only *approved* and *static* registrations are mirrored, because detected ones are rediscovered from the live listener set under the same derived id and mirroring them could only go stale.
"Cannot route" is kind-dependent: a loopback row needs a host and a usable port, a static row needs a served directory and an entry, and neither carries the other's fields.

**Not:** being authoritative at runtime (`PreviewRegistry.items` is), minting ids (`processes.preview_id` does), or ever failing loudly enough to stop the daemon starting.

## `preview_transport.py`

Serving a registered Preview through the daemon at `/preview/{preview_id}/…`: the injected runtime bridge, HTML/CSS/JavaScript URL rewriting, the static-preview content-type table and its sandbox CSP, upstream target resolution, the forwarded and hop-by-hop header sets, the concurrency slots, the WebSocket relay, and the HTTP proxy itself.

The proxy streams its own `StreamResponse`, so it stamps `apply_security_headers` before `prepare()`: the security middleware stamps after a handler returns, which is too late once bytes are on the wire.
It never copies `Content-Length` from an upstream response it decompressed, because aiohttp would then truncate the outbound body to the compressed length - a silent fail-open.

The JavaScript rewriting is parsed, not matched (S12.5, closing audit F21).
A module specifier is found by *being* one - a `string` reached through `import_statement.source`, `export_statement.source`, or a dynamic `import()`'s argument, in the tree-sitter javascript grammar the code graph already depends on - so the identical characters inside an ordinary string, a comment, or a template literal are not reachable and are not rewritten.
Two consequences beyond the fix: a protocol-relative `//cdn…/lib.js` now stays on its own origin instead of becoming a path on the mux one, and a body that does not parse as JavaScript falls back to the old lexical regex, which is deliberate - an over-broad rewrite beats a Preview whose every module 404s.
The parser and query are built once and reused, and their import is deferred so the module still imports where the grammars are absent.

**Not:** the registry that answers *which* Preview this is (`processes.py`), the durable mirror (`preview_store.py`), screenshots (`preview_capture.py`), or route registration (`routes/processes.py`).

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

## `operational_telemetry.py`

Legacy durable process, quota, reset, compaction, and tool observations in `mux.db`.
It remains readable while canonical activity migration runs.

**Not:** the canonical cross-session activity ledger or long-term detailed analytics.

## `telemetry_schema.py`

The catalog and segment schemas, the versioned additive migrations that bring an older file to
them, per-file structural signatures, and the pure helpers the reducer shares (canonical JSON,
digests, UTC period and day keys, source precedence ranks, tool classification).

**Not:** any write of evidence, or any decision about what a file's rows mean.

## `telemetry_ledger.py`

The synchronous write path of the canonical ledger under `<data_dir>/telemetry/`: entity
identity, field-level source precedence, evidence links, closed-day rollup rebuilds, segment
sealing, and storage and schema status.
Reduces content-free evidence into run, turn, tool-call, model-request, compaction,
skill-invocation, and verification entities, one home segment per entity across months.
`LegacyImportMixin` and `LedgerQueryMixin` are mixed in here so one class is the ledger.

**Not:** provider transcript content, the browser presentation, causal performance claims,
deletion of legacy telemetry, or anything async.

## `telemetry_imports.py`

Resumable, non-destructive importers for the legacy `tool_events`, `history`, status-timeline
turn, `context_compactions`, and Tier 0 test-outcome streams in `mux.db`, each past a durable
cursor and each re-read periodically after it first catches up.

**Not:** parsing a native transcript itself; that remains the legacy store's reconciler.

## `telemetry_queries.py`

Exact aggregates over the whole requested window (closed days from rollups, everything else
from entities, consecutive raw days merged into one query per month), exact-match filters,
cursor-bounded detail and export pages, the tool-call audit, deterministic inefficiency
candidates, field-completeness quality, and the parser-signature readout.

**Not:** any total derived from a displayed page.

## `telemetry_service.py`

The daemon adapter: one worker thread owning every ledger call, batched EventBus ingestion,
the legacy catch-up loop, the rollup and sealing worker, schema-drift logging, and the health
block `GET /api/diagnostics/background` reports.

**Not:** the reducer's rules; it only schedules them.

## `telemetry_otlp.py`

Reduction of provider OTLP/JSON log and metric batches to canonical events, the exporter
environment and arguments a new session is launched with, and the per-batch signature of event
names seen, measured against Claude Code 2.1.259 and Codex CLI 0.153.0.

**Not:** storage of any content or identity attribute; both classes are hashed or dropped
before the batch is released.
