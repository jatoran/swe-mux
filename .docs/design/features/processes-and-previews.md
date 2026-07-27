# Process ownership and previews

## What it is

- Per-session descendant attribution and bounded resource/listener snapshots.
- Explicit preview leaves for a detected or user-approved literal-loopback development server.

## Ownership and actions

- Windows assigns every root PTY process to the daemon kill-on-close job and also attempts
  a nested per-session Job Object. PID plus process creation time prevents PID-reuse errors;
  periodic `psutil` reconciliation records descendant appearance and exit. The assignment
  result, parent lineage, Project/run owner, first/last seen, exit evidence, command hash,
  and confidence persist as bounded observations; command text does not.
- Snapshots cap retained records and expose parent, executable label/command, start/exit,
  CPU, RSS, listeners, and measurable warning conditions. The inspector nests descendants
  by PID/parent PID; processes whose parent is outside the owned snapshot remain visible as
  roots. A no-output or high-resource label is diagnostic only; swe-mux never claims a
  server is hung or auto-kills it.
- Descendants leaving the current tree become `escaped`. After an ended root session's
  configurable grace, a matching survivor becomes `suspected_orphan`; swe-mux never
  auto-kills it. Startup revalidates persisted fingerprints and marks PID reuse or
  unverifiable ownership stale. Startup restores only fingerprints that might still be
  running: an already-exited durable record cannot become live again, so republishing it
  would fill the fleet with a previous daemon run's dead processes.
- The orphan grace is measured from a `root_ended_at` stamped **once**, on the first pass
  that observes the root ended. Its value is the session record's last activity while that
  record still exists, and otherwise the previous pass's `last_seen` (which after a restart
  is the previous daemon run's). Deriving the deadline from `last_seen` on every pass — as
  it once was — made it track the current time, so the window slid forever and a survivor of
  a session the manager had already dropped could never leave `escaped`. A root that comes
  back clears the stamp.
- The fleet and session snapshots report running processes. Ended records are excluded
  unless `include_ended` is requested, and a session with no remaining process is dropped
  once it is no longer live. This is a display and payload boundary, not a retention change:
  ended records still reach `process_evidence`, still appear under `include_ended` for the
  current daemon run, and were already absent from every resource total. Every live
  observational state, including `escaped`, `suspected_orphan`, and `inaccessible`, is by
  definition not ended and therefore always visible.
- Interrupt, terminate-process, and terminate-tree include the durable identity fingerprint
  and re-check PID creation time plus live ownership immediately before acting. A request
  for a PID owned by another session or a reused fingerprint is rejected.
- Terminating a root process can correctly leave its session in `crashed`, but session
  finalization releases the ended ConPTY host. Its `OpenConsole.exe`/`conhost.exe`
  infrastructure member therefore disappears from daemon accounting while the terminal's
  scrollback and exit evidence remain available.
- If optional process inspection support is unavailable, the API returns a typed diagnostic
  and all terminal/session behavior continues.
- The inspector opens from session/terminal right-click, pane-header `proc`, sidebar
  `: menu` Process fleet, or the command palette. Fleet mode uses the coherent all-session
  snapshot when available and falls back to aggregating session-scoped snapshots for an
  older running daemon; listener rows expose the preview action at the point of discovery.
  The coherent snapshot also exposes daemon/infrastructure members as a separate tree with
  PID, parent, executable/command, CPU, RSS, and network detail. These rows are observational:
  they allow PID copy but never interrupt/terminate actions against the swe-mux runtime.
- The bottom-sidebar owned-resource summary reuses the cached fleet snapshot and reports
  aggregate CPU/RSS. Its anchored viewport popover groups live attributed processes by
  Project, shows a separate daemon/infrastructure bucket, and links to Process fleet.
  Daemon accounting includes the daemon plus descendant infrastructure PIDs not already
  attributed to a session, preventing double counting. Process Fleet totals use that same
  additive bucket, so its count and usage reconcile with the sidebar. The closed popover and
  detailed fleet rows reuse the existing sample and cause no additional process enumeration.

## Sampling cost

The reconcile loop runs on every live session tree every few seconds, so its cost is paid
forever and lands on the daemon's event loop. Two properties keep it cheap:

- **One parent map per pass.** `Process.children(recursive=True)` snapshots every process on
  the machine, so calling it per session root re-walked the whole table N times a tick. A
  single `psutil._ppid_map()` builds one parent/child index that serves every root and the
  daemon tree. It is a private psutil API; if it disappears the walk falls back to
  `children(recursive=True)` per root.
- **A parent map is not a process tree.** Windows never clears a dead parent's pid from a
  child's ppid field and recycles pids aggressively, so the map contains parent links that
  were never real. The walk therefore rejects any descendant created *before* the root and
  does not traverse through it — the same guard `children(recursive=True)` applies
  internally, which is exactly why replacing that call means reproducing it. Skipping it is
  not a stray extra row: one stale link made the PTY supervisor look like a descendant of a
  single session, and since the supervisor parents *every* session, that session absorbed the
  whole fleet (34 processes, three `claude.exe`, another session's listeners) while its
  siblings reported zero. Any future change to this walk must keep the creation-time guard.
- **Handles and identity are cached, not rebuilt.** Constructing a `psutil.Process` is the
  most expensive single operation available (it re-queries the process to validate identity),
  and `cmdline()` is a remote-PEB read. Neither a process's name nor its command line changes
  while it lives, so both are read once per handle and reused; only `cpu_times` and
  `memory_info` are re-read per pass. Handles for pids that left the tree are dropped, so a
  pid always re-enters through fresh construction.

Together these took a five-session pass from **~930-1110 ms to ~22 ms**. That is not merely a
CPU saving: psutil's Windows calls hold the GIL, so the old pass starved the event loop for
roughly a fifth of every 5-second cycle, and the visible symptom was terminal input that
lagged and then caught up in a burst. Anything added to this loop must respect the same rule —
per-tick work is restricted to attributes that actually change per tick.

Identity safety does not depend on the cache. A pid whose parent changed between passes is
proof of recycling and rebuilds its handle; `_revalidate_unseen` constructs fresh handles for
everything that fell out of the walk; and every process action re-checks creation time against
a freshly constructed handle before acting.

## Memory reporting

`memory_bytes` is RSS, which on Windows is the working set and therefore counts each shared
page once per process mapping it. Summed across a session tree it overstates the real
footprint substantially — a measured fleet read 5.07 GB summed RSS against 3.32 GB summed USS.
Unique set size is the honest figure but costs roughly 200x an RSS read because it walks every
working set, so it is never sampled on the reconcile cadence. `GET /api/processes?unique_memory=1`
adds `memory_unique_bytes` per process, per daemon member, and in totals; the resource popover
requests it only while open and the background rail poll never does. A total is reported only
when every contributor supplied one, because a partial sum would read as a real but too-small
number rather than as "not sampled".

The popover also names **duplicated per-session tooling**: language servers are per-session, so
N sessions open on one repo run N independent indexes of the same code. On a four-session
project that was the largest addressable line item and invisible in a flat process list, where
every copy is just another `node.exe` under its own session. The panel reports it and nothing
more — swe-mux does not reap or share language servers.

## Preview contract

- Registrations identify one endpoint within a canonical Project and record the live session
  that actually owns its listener. The URL must use literal
  `127.0.0.1` or `::1`, contain no credentials/query/fragment, and either match the host
  and port of a listener owned by some session in that Project or carry explicit user approval.
  Clicking a URL printed by another session therefore attributes the Preview to the listener
  owner, and the same Project/scheme/host/port can never create a second registration.
- A wildcard bind is reported at its loopback address: `0.0.0.0` becomes `127.0.0.1` and
  `::` becomes `::1`, so a server that binds every interface — the default for most dev
  servers — is detected, listed, and previewable. This states a fact rather than widening
  the boundary, because a wildcard bind does serve loopback; the destination actually dialed
  is still literal loopback and the wildcard address itself remains an illegal destination.
  A bind to one specific non-loopback address is reported verbatim and stays unpreviewable,
  since it genuinely is not reachable on loopback.
- A preview is a real recursive-layout leaf; opening it does not replace or terminate its
  source terminal. The leaf supports refresh, mobile/tablet/fit viewport presets, copy,
  external open, mobile full-screen, and ownership/source status.
- Clicking a loopback HTTP(S) URL rendered in a terminal registers/activates it as an integrated
  Preview with explicit user approval. `localhost`, `0.0.0.0`, and wildcard IPv6 links normalize
  to literal loopback; non-loopback links retain ordinary external-browser behavior. Reopening
  the same Project endpoint reuses its registration. Closing its workspace tab leaves the
  sidebar registration intact; selecting the row or URL again reattaches and activates that
  same Preview leaf.
- A server belongs beside whatever spawned it. Attaching a preview groups it as a tab in the
  region that already holds its owning session, so an agent and the services it started share
  one tab strip; it only falls back to a split when that session has no terminal in the
  layout. Every detected loopback service receives a routing registration, nested under its
  actual owning session, without opening a workspace tab. Selecting its row opens or activates
  that registered service.
- Sidebar rows are servers only. A live loopback listener is the sole test, since the rest of
  a session's tree is bookkeeping that no age or liveness filter distinguishes from signal;
  exited records and non-loopback listeners are excluded, and a port bound on both 127.0.0.1
  and ::1 collapses to one row. Descendant shells have no PTY, are never terminal tabs, and
  are never sidebar rows: the process inspector remains the one place showing the full tree.
  The sidebar read reuses the inspector's existing cached sample, so it adds no process
  enumeration.
- Preview leaves use `/preview/{registration}/…`; phones and desktop browsers never need
  the development server's raw port. The runtime bridge maps absolute loopback fetch, XHR,
  and WebSocket destinations to other registered services in the same Project, so a frontend
  Preview can reach its backend without treating `127.0.0.1` as the phone. The registered
  origin is immutable per request and
  redirects to another origin are rejected, so the route cannot become a network proxy.
- HTTP methods, bodies, queries, root-relative HTML/CSS/module paths, runtime fetch/XHR,
  EventSource, WebSocket messages, and negotiated HMR subprotocols traverse the bridge.
  Browser Origin is replaced with the registered loopback origin for common dev servers.
- Rewriting covers `src`/`href`/`action` attributes **and inline `<script>` bodies**, because a
  module specifier inside an inline script is unreachable by attribute rewriting: the
  `@vitejs/plugin-react` preamble imports `/@react-refresh` that way, and an unprefixed miss
  leaves `window.$RefreshReg$` unset, which makes every transformed module throw and renders a
  blank page. Data blocks (`application/json`, `importmap`, `text/template`) are passed through
  byte-for-byte; only executable script types are rewritten.
- What the bridge cannot fix, it advertises. `window.__MUX_PREVIEW_BASE__` carries the mount
  path (`/preview/{registration}/`) for an app to pass to its router's `basename`. A
  client-side router reads `location.pathname` directly and `Location` is not patchable, so a
  root-mounted router matches no route under the prefix and renders nothing. Apps that ignore
  the global keep working only if all their routes are relative.
- HTTP requests/responses are capped at 10/20 MiB with a 10-second connect timeout,
  30-second per-read timeout, no wall-clock total, and 32 concurrent requests. WebSocket
  connects time out after 10 seconds; messages are capped at 4 MiB with 16 concurrent bridges,
  30-minute idle timeout, and 12-hour lifetime. Clients may reconnect normally.
- The iframe intentionally omits `allow-same-origin`, preventing preview code from reading
  the parent swe-mux application/API. Sandboxed `Origin: null` requests receive narrowly
  scoped CORS handling only on their registered preview route.
- A detected registration lasts as long as its listener. Once that listener has been gone for
  the restart grace the registration is dropped on the next read, and the browser retires the
  matching tab and sidebar row, so a stopped server never keeps a viewport pointed at nothing.
  The grace exists because dev servers rebind on every restart; a preview must survive that
  gap rather than disappear on each reload. A user-approved registration is never reaped: mux
  could not attribute that listener in the first place, so its absence is not evidence the
  server stopped, and only an explicit close removes it.
- Registrations live with the daemon. An ended source session returns `preview unavailable`;
  daemon restart leaves any restored preview leaf stale rather than guessing a destination.

## Preview capture

- The preview rail can screenshot the live loopback server headlessly and copy a reference
  to the clipboard (never sends to an agent, never writes a PTY). Full capture or a
  drag-selected region.
- Server-side rendering uses the optional `preview-capture` extra (Playwright + Chromium).
  Absent, the endpoint returns a typed `{available: false, reason, install}` — an optional
  integration, never a failure. Any render error returns `{available: true, error}` (502),
  not a 500. Rendering uses `wait_until="load"` (an HMR dev server never reaches
  `networkidle`) and points Playwright at the standard per-user browser cache so a frozen
  desktop build resolves the browser installed by `playwright install`.
- Region clip coordinates are page pixels captured from the top of the page: the iframe
  omits `allow-same-origin`, so the preview scroll position cannot be read.
- The PNG is saved into the owning Project's `.swe-mux/preview-shots/` (falling back to the
  data dir), so a local agent can read it in the repo it is already working in. The absolute
  path is returned for the copied reference.

## Key files

- Ownership/registry: `src/swe_mux/processes.py`
- Proxy and runtime bridge: `src/swe_mux/server.py`
- Durable evidence: `src/swe_mux/operational_telemetry.py`
- Job boundary: `src/swe_mux/win_jobobj.py`, `src/swe_mux/session.py`
- Inspector: `frontend/src/ProcessPanel.tsx`
- Resource summary: `frontend/src/ResourceUsage.tsx`, `frontend/src/resourceTotals.ts`
- Duplicate tooling classification: `frontend/src/resourceTooling.ts`
- Preview leaf + capture/region UI: `frontend/src/PreviewPane.tsx`
- Headless capture (optional Playwright): `src/swe_mux/preview_capture.py`
- Terminal-link routing: `frontend/src/TerminalPane.tsx`, `frontend/src/previewLinks.ts`
