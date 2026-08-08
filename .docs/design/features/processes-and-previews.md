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
- The PID+creation-time rule covers the **root** too. Ended sessions stay listed (the user
  keeps them for scrollback) with `record.pid` never cleared, so collection skips any session
  in `exited`/`crashed` outright, and for a live one verifies the root's creation time against
  the value captured at spawn (`SessionRecord.root_started_at`) before walking the tree.
  Without that gate a recycled root pid attributed an unrelated process tree to the dead
  session as `active`/`high` evidence — persisted, listed in the fleet, emitting
  `listener_detected` under the wrong session, and offering terminate on it. Sessions adopted
  from a supervisor predating the field have no reference and fall back to pid-only.
- The parent walk validates creation-time causality on **every edge**, not only against the session root.
  A child that predates its current parent names a recycled dead-parent PID and the entire foreign branch is rejected.
  Root-relative validation alone is insufficient because both the recycled parent and foreign child may postdate a long-lived session root.
- **A downward walk cannot reach a detached descendant, so job membership is a second
  attribution source.** Windows neither re-parents an orphan nor clears the dead pid from
  its ppid field, so once an intermediate parent exits its children are permanently
  unreachable from the session root. That is the *normal* outcome for anything an agent
  starts to outlive one tool call: Codex's shell tool runs one-shot and must detach
  (`Start-Process`), where Claude's holds the parent open for the command's whole life.
  The visible symptom was a Codex session serving a live dev server that swe-mux reported
  as zero listeners, which meant no Process candidate, implicit Preview route, or clickable route, while the
  identical thing under Claude worked. Each session's nested Win32 job is therefore
  queried (`JOBOBJECT_BASIC_PROCESS_ID_LIST`) and its members unioned into the walk.
  **This is not known to fix the Codex case — see § Detached servers: what is and is not
  known.** It is retained because it is correct for whatever it does catch and costs
  nothing when it catches nothing, not because it was shown to solve the reported bug.
  Membership is a *stronger* claim than the parent chain, not a fallback: a process enters
  a job only by being spawned inside one, and Windows drops a pid the instant it exits, so
  a recycled pid cannot appear by coincidence. Job members are consequently not re-filtered
  against the root's creation time the way mapped children are, and are not traversed for
  children (their children are already members). They carry
  `evidence_reason=live_job_object_member` so a parentless row reads as "detached,
  job-owned" rather than "lineage not sampled", and they are subject to the same
  interrupt/terminate rules — correctly, since closing that job already kills them.
- **Job evidence never outranks the root fingerprint.** The job handle is keyed to the
  session, so a root that fails its creation-time check discards the job's answer with the
  rest of the attribution. The lookup happens only after that check passes.
- The job handle lives wherever the PTY does. A supervisor-owned session keeps it in the
  supervisor (a daemon-held handle would kill the tree on daemon exit and defeat session
  survival), so the daemon fetches membership over a `job_pids` RPC; a daemon-owned PTY
  reads its own. The message is deliberately **not** gated on `PROTOCOL_VERSION`: bumping
  it would stop a new daemon from driving the already-running supervisor and orphan every
  live session over an attribution nicety. An older supervisor answers "unknown message
  type", which degrades to "no job evidence" and the parent walk alone. A failed refresh
  keeps the previous map rather than blanking it, so an RPC hiccup cannot flicker a
  Preview tab off and back.
- Snapshots cap retained records and expose parent, executable label/command, start/exit,
  CPU, RSS, listeners, and measurable warning conditions. The inspector nests descendants
  by PID/parent PID; processes whose parent is outside the owned snapshot remain visible as
  roots. A no-output or high-resource label is diagnostic only; swe-mux never claims a
  server is hung or auto-kills it.
- Stable provenance records `attribution_version`, `attribution_source` (`session_root`, `parent_walk`, or `job_membership`), `last_attributed_at`, and `last_job_confirmed_at` independently of mutable evidence state/reason.
  Process actions and server presentation require current-version ownership.
  The daemon and its descendant infrastructure fingerprints are reserved from session ownership, and equal-strength claims from multiple sessions are quarantined rather than assigned arbitrarily.
- Ownership rejections emit bounded command-free diagnostics in the process snapshot and structured rolling daemon log.
  Diagnostics cover causally impossible edges, infrastructure contamination, ambiguous multi-session claims, and legacy-evidence retirement.
- Descendants leaving the current tree become `escaped`. After an ended root session's
  configurable grace, a matching survivor becomes `suspected_orphan`; swe-mux never
  auto-kills it. Startup revalidates persisted fingerprints and marks PID reuse or
  unverifiable ownership stale. Startup restores only fingerprints that might still be
  running: an already-exited durable record cannot become live again, so republishing it
  would fill the fleet with a previous daemon run's dead processes.
- Persisted evidence written before edge-causal attribution is version 1.
  On upgrade, every currently reachable tree or Job Object member is rewritten as version 2; an uncorroborated version-1 survivor is marked stale with `ownership_rejected`, and its listeners are cleared without signaling the OS process.
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
- `interrupt` on the session root writes Ctrl-C to its PTY. On Windows a non-root descendant
  gets `CTRL_BREAK_EVENT` (psutil rejects SIGINT there, which made the action unusable for
  every descendant on the primary platform) and a typed "cannot be interrupted; use
  terminate" error when even that is not deliverable — never a raw psutil failure.
- `terminate_tree` terminates the descendants of a root whose PID *and* creation time were
  just revalidated. Matching children against the last sample's owned set by raw PID let a
  child that respawned since (a dev server restarting) survive the action while the user
  believed the tree was gone.
- Inspection is split in two. The **Processes drawer tab** is the watch surface: one rollup row
  per session (process count, CPU, working set) plus the loopback servers it is listening on,
  scoped to the active Project with the focused session's row pinned first, and `preview`/`copy`
  as its only actions. The **modal inspector** keeps everything that needs width or a
  confirmation: the process tree, parent lineage, evidence state/reason/confidence, the ended
  toggle, add-preview-by-URL, and interrupt/terminate/terminate-tree. **The drawer tab cannot
  terminate anything**, deliberately — a two-click destructive confirm in a 300 px column is how
  the wrong tree gets killed. It reads the fleet sample the frontend already polls and starts no
  loop of its own, so leaving it open adds no process enumeration (see § Sampling cost); its
  `Full inspector` button opens the modal prefiltered to the tab's scope.
- The inspector opens from session/terminal right-click, the drawer tab's `Full inspector`,
  sidebar
  `: menu` Process fleet, or the command palette. The pane header's `proc` chip is gone: it was
  the only pane tool with no state of its own, and the drawer tab covers what it was for. Fleet
  mode uses the coherent all-session
  snapshot when available and falls back to aggregating session-scoped snapshots for an
  older running daemon; listener rows expose the preview action at the point of discovery.
  The coherent snapshot also exposes daemon/infrastructure members as a separate tree with
  PID, parent, executable/command, CPU, RSS, and network detail. These rows are observational:
  they allow PID copy but never interrupt/terminate actions against the swe-mux runtime.
- The bottom-sidebar resource summary reuses the cached fleet snapshot and reports normalized
  whole-system CPU utilization alongside aggregate RSS for owned session and daemon processes.
  System CPU comes from deltas between cumulative OS CPU counters and therefore stays on the
  familiar 0–100% whole-machine scale regardless of logical processor count.
  The first sample is unavailable until a second counter reading establishes an interval.
  The anchored viewport popover groups live attributed processes by Project, shows a separate
  daemon/infrastructure bucket, and links to Process fleet.
  Its top CPU figure is system-wide; memory and process counts remain explicitly owned.
  Attributed CPU remains additive for attribution detail and is presented as equivalent core
  load (`1.0×` means one logical processor), not as a misleading whole-machine percentage.
  Daemon accounting includes the daemon plus descendant infrastructure PIDs not already
  attributed to a session, preventing double counting.
  Process Fleet totals use that same additive owned bucket, so its count and usage reconcile
  with the detailed rows rather than the system-wide sidebar CPU figure.

## Detached servers: what is and is not known

Investigated 2026-08-03, after a Codex session in a Project served a live dev server that
never appeared in the sidebar. **Status: root cause confirmed, mitigation shipped but
unverified against the real case, and further process forensics deliberately stopped.**
Read this before spending time on the problem again.

### Confirmed by measurement

- The agent started the server with PowerShell `Start-Process` from a one-shot
  `pwsh -NonInteractive -EncodedCommand` tool call. That shell exits ~1 s later.
- Windows neither re-parents the orphan nor clears the dead pid: the server's ppid still
  named a pid that returned `NoSuchProcess`. Two such servers existed, both unreachable.
- Consequence: `_tree_handles` reported 6 processes and **zero listeners** for a session
  that was demonstrably serving HTTP.
  No listener means no Process candidate and no implicit Preview route.
- Claude never hits this because its Bash tool keeps a `bash.exe` alive as the parent for
  the command's whole life, background commands included, so the server stays a genuine
  descendant. This is a difference between the two CLIs' shell tools, not a gap in swe-mux's
  Codex support.
- There is a race, not a guarantee: reconcile runs every 5 s and the launching shell lives
  ~1 s, so a pass can occasionally catch the server while its parent is alive and then
  retain it as `escaped`/`suspected_orphan`. Several stale `http.server` records in the
  fleet were captured exactly that way. It essentially never wins.
- The supervisor `job_pids` RPC works end to end and returns real membership.
- `bash.exe` descendants of a session's `claude.exe` **are** in that session's job, so job
  capture is functioning in general.

### Not established

**Whether a `Start-Process` child lands in the supervisor's nested per-session job.** An
isolated bench (one self-created `BREAKAWAY_OK | KILL_ON_JOB_CLOSE` job, root → pwsh →
`Start-Process` grandchild) showed the grandchild *was* in the job and died on job close.
That result did not reproduce against a live session, and the reason is a limitation of the
test rig rather than evidence either way: **nothing spawned from Claude Code's Bash tool is
in the session job** — not a sandboxed process, not one with the sandbox disabled, not even
a plain `subprocess.Popen` child. There was no foothold inside the job to launch from, so
the end-to-end test could never be made faithful from an agent session.

Testing this properly needs a real Codex session performing a real detached launch, with
`job_pids` sampled against the resulting pid. Do not re-derive the above first.

### The durable answer is a convention, not more forensics

Process topology is the wrong layer to fight. Codex's shell tool *must* return, so "keep the
server in the process tree" argues with the tool's design, and Windows job semantics under a
nested-job ConPTY arrangement proved not worth the measurement cost. Have the agent **declare
the server** instead — one call, independent of process topology, deterministic:

```
POST /api/previews {"session_id": "<sid>", "url": "http://127.0.0.1:<port>/", "approved": true}
```

This is the same `approved` path a terminal link click already uses, so it needs no new
surface, and it was confirmed to work on the reported server on the first attempt. The
cheap form is a line in the Project's `AGENTS.md`/`CLAUDE.md` telling agents to register a
server after starting it. The fuller form is a `register_preview` MCP tool beside `notify`
and `request_spawn` — deferred, because it widens a write surface `mux-mcp.md` keeps
deliberately narrow, and the convention should be shown to be forgotten before paying for it.

Until either exists, the manual escape hatch is the modal inspector's add-preview-by-URL,
which takes the same `approved` path.

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
  were never real. The walk therefore rejects any child created before its current parent and
  does not traverse through it. Root-only comparison misses a foreign long-lived process that
  postdates the session root but predates a recycled intermediate parent. Skipping this guard is
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
- The registry separates route-only identities from declared Previews.
  Automatic listener discovery creates an undeclared identity only so sandboxed Preview traffic can reach sibling Project services.
  A terminal-link click, Processes action, or explicit `POST /previews` promotes that stable identity to `declared=true`.
  `GET /previews` and the sidebar expose declared Previews only.
- A wildcard bind is reported at its loopback address: `0.0.0.0` becomes `127.0.0.1` and
  `::` becomes `::1`, so a server that binds every interface — the default for most dev
  servers — is detected in Processes and previewable by explicit action.
  This states a fact rather than widening
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
  the same Project endpoint reuses its registration.
- **Both link kinds route there.** Plain-text URLs are matched by the web-links addon; an
  OSC 8 hyperlink carries its destination out of band and renders as a label, so xterm's
  `linkHandler` resolves through the same handler. Without it a server announced only as a
  markdown link — how a Codex TUI renders `[label](http://127.0.0.1:…)` — had no clickable
  route to a Preview at all, since there is no URL text on screen to match. Closing its workspace tab leaves the
  sidebar registration intact; selecting the row or URL again reattaches and activates that
  same Preview leaf.
- A server belongs beside whatever spawned it. Attaching a preview groups it as a tab in the
  region that already holds its owning session, so an agent and the services it started share
  one tab strip; it only falls back to a split when that session has no terminal in the
  layout.
  Every detected loopback listener receives an undeclared routing identity owned by its actual session without opening a workspace tab or adding navigation.
  Selecting its row in Processes declares, opens, or activates that registered endpoint.
- Sidebar child rows are declared Previews only.
  Raw listeners are not asserted to be application servers because agent runtimes, browser debuggers, and tool bridges also bind loopback ports.
  Current-version ownership, liveness, and loopback reachability make a listener eligible as a Processes candidate, not as general navigation.
  Rejected/stale records, exited records, and non-loopback listeners remain excluded, and a port bound on both `127.0.0.1` and `::1` collapses to one candidate.
  Descendant shells and the full process tree remain visible only in process tooling.
- Preview leaves use `/preview/{registration}/…`; phones and desktop browsers never need
  the development server's raw port. The runtime bridge maps absolute loopback fetch, XHR,
  and WebSocket destinations to other registered services in the same Project, so a frontend
  Preview can reach its backend without treating `127.0.0.1` as the phone. The registered
  origin is immutable per request and
  redirects to another origin are rejected, so the route cannot become a network proxy.
- HTTP methods, bodies, queries, root-relative HTML/CSS/module paths, runtime fetch/XHR,
  EventSource, WebSocket messages, and negotiated HMR subprotocols traverse the bridge.
  Browser Origin is replaced with the registered loopback origin for common dev servers.
- Root-relative `src`/`href`/`action` values created after load traverse the same bridge.
  The injected runtime intercepts DOM attribute/property writes and HTML insertion, with a
  mutation fallback for detached fragments; external, protocol-relative, `data:`, and `blob:`
  destinations keep browser-native behavior.
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
- Preview responses use `Cache-Control: no-cache` and preserve upstream validators, so a manual
  refresh revalidates same-URL assets instead of requiring a new port to escape an upstream
  `max-age`. HMR/live reload remains server-owned; a plain server still needs manual refresh,
  and server code without an autoreloader still needs a same-port process restart.
- Preview chrome is width-contained by a shrinkable grid column. On mobile, the header action
  rail scrolls inside the tab while the viewport and iframe remain within the visible width.
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
- Shots expire after 7 days through the daemon's media-cleanup loop, across every registered
  Project root plus the data-dir fallback. They live *inside the user's repository*, and a
  UI-iteration session takes dozens of multi-hundred-KB PNGs a day; the window is longer than
  pasted media because an agent may reasonably read one days later.

## Key files

- Ownership/registry: `src/swe_mux/processes.py`
- Job membership: `src/swe_mux/win_jobobj.py` (`ReaperJob.process_ids`),
  `src/swe_mux/supervisor.py` (`job_pids` message),
  `src/swe_mux/session.py` (`SessionManager.job_process_ids` merges both PTY ownerships)
- Proxy and runtime bridge: `src/swe_mux/server.py`
- Durable evidence: `src/swe_mux/operational_telemetry.py`
- Job boundary: `src/swe_mux/win_jobobj.py`, `src/swe_mux/session.py`
- Inspector (the act surface, modal): `frontend/src/ProcessPanel.tsx`
- Drawer watch tab: `frontend/src/ProcessesTab.tsx`, `frontend/src/processWatch.ts` (the pure row
  model: rollups, focused-first ordering, ended-process rules)
- Resource summary: `frontend/src/ResourceUsage.tsx`, `frontend/src/resourceTotals.ts`
- Duplicate tooling classification: `frontend/src/resourceTooling.ts`
- Preview leaf + capture/region UI: `frontend/src/PreviewPane.tsx`
- Headless capture (optional Playwright): `src/swe_mux/preview_capture.py`
- Terminal-link routing: `frontend/src/TerminalPane.tsx`, `frontend/src/previewLinks.ts`
