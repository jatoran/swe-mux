# Process ownership and previews

## What it is

- Per-session descendant attribution and bounded resource/listener snapshots.
- Explicit preview leaves for a detected or user-approved literal-loopback development server.
- Static document previews: a directory of the Project checkout served by the daemon itself,
  through the same registry and the same `/preview/<id>/` route, with no process and no port.

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
- **Inspection is one surface in two shells.** `ProcessFleetView` is the surface; the
  **Resources dialog's Processes segment** and the **Processes drawer tab** are shells around it. They differ in chrome and in
  default scope, and in nothing else — the tab draws the same process trees, parent lineage,
  evidence state/reason/confidence, listener and Preview rows, ended toggle, add-preview-by-URL,
  and the same guarded interrupt/terminate/terminate-tree.
  The tab adds exactly two things: it opens scoped to the active Project, and it marks the
  focused terminal's session and pins it to the top of its Project.
  Its footer button reopens the same view as the full-width dialog.
  The tab ships **hidden by default**: the dialog covers the terminal, so this tab is not
  redundant with it, but "what is this session running" is asked rarely enough not to spend a
  permanent rail slot on for someone who has not shown it (`ui.md`).
  This replaced a watch/act split in which the tab drew per-session rollups and could terminate
  nothing. The defence of that split was that a two-click destructive confirm in a 300 px column
  is how the wrong tree gets killed — an argument about *layout*, not about *capability*, and it
  is answered by layout: `.process-fleet-view` is a CSS container, and the column renders the
  same narrow layout the modal already used on a phone, with the same two-press confirm. What
  the split cost was that the surface open beside a terminal could not answer what was running
  under it, so every investigation ended in "now open the other one".
- Because trees and evidence are absent from the reduced `/api/processes?summary=1` projection,
  the drawer tab no longer reads it; it subscribes to the full snapshot like the modal does.
  The summary projection remains, and remains what the always-mounted sidebar rail polls
  (see § Sampling cost) — that is the poll whose payload size mattered, and it is unchanged.
  The full-snapshot poll is **refcounted and shared** (`processFleetFeed`): one request per tick
  per distinct scope, however many surfaces are drawing it, so a tab selected in both drawer
  stacks with the modal open over it is still one read. A surface with no subscribers polls
  nothing, and its last result is held only long enough (6 s) to survive a tab switch, so
  reopening redraws instantly and nothing older is ever drawn as live fleet state.
- A Project scope excludes the daemon/infrastructure group on both shells: the swe-mux runtime
  belongs to no Project, so a scoped view that listed it would report something the scope says
  is not there. Scoped totals are likewise recomputed from the rows on screen; only the
  unscoped line is the daemon's own totals plus the runtime bucket, which is the figure that
  reconciles with the sidebar's resource summary.
- Loopback listener rows are deduped by port, preferring the IPv4 form, so a server bound to
  both stacks is one previewable row rather than two rows for one endpoint.
- The drawer tab's stored scope distinguishes **unset** (`null`) from **every Project** (`''`),
  through `resolveProjectScope`. Unset resolves to the Project the drawer is sitting beside, so
  the tab follows a Project switch instead of pinning whichever was active when it opened.
  Collapsing the two made `All projects` unselectable: choosing it stored a falsy value that
  read as unset and snapped straight back to the active Project.
- **The inspector draws one line per process, and expands for the rest.** The line carries what
  you scan for — executable, PID, the command with its own executable stripped off the front,
  live CPU/RSS, and network counts only when there are any — plus anything abnormal.
  Its expander carries what you read before acting: the full command, parent, evidence
  reason/confidence, attribution, verification and first/last-seen times, listener and connection
  detail, warnings, and the actions themselves. Putting interrupt/terminate behind the same
  expander is deliberate: the evidence a destructive action depends on is then on screen at the
  moment it is pressed. Printed unconditionally that was six lines and ~120 px per process, so
  one session filled the panel and a phone showed one process at a time; it is now ~24 px on a
  desktop and two wrapped lines at column width.
- Redundancy is stripped rather than repeated at every level. `active` is the state of nearly
  every row, so it is a coloured dot and only the other states get a badge — a badge appearing at
  all means something is wrong. The parent PID is a detail, not a column, because the tree
  already draws that edge. A session heading does not restate the Project heading directly above
  it, and its rollup is suppressed when the session has a single live process, where it would
  only restate the row beneath it. Loopback listeners and registered Previews are single rows
  with their actions inline, not headed sub-lists.
- The inspector opens from session/terminal right-click, the drawer tab's `Open full width`,
  sidebar
  `: menu` Resources, the app menu's `Resources…` row, the sidebar's resource chip, or the
  command palette (`processes.all`, unchanged). The pane header's `proc` chip is gone: it was
  the only pane tool with no state of its own, and the drawer tab covers what it was for. Fleet
  mode uses the coherent all-session
  snapshot when available and falls back to aggregating session-scoped snapshots for an
  older running daemon; listener rows expose the preview action at the point of discovery.
  The coherent snapshot also exposes daemon/infrastructure members as a separate tree with
  PID, parent, executable/command, CPU, RSS, and network detail. These rows are observational:
  they allow PID copy but never interrupt/terminate actions against the swe-mux runtime.
- The bottom-sidebar resource summary reuses the cached fleet snapshot and reports normalized
  whole-system CPU utilization alongside aggregate RSS for owned session and daemon processes.
  It leads with a live session count, which comes from the session fleet rather than from this
  snapshot: it is the one figure in the row that survives process inspection being unavailable,
  and reading it beside the process count is what answers "how many processes is one session
  costing me" without opening the dialog (`features/ui.md`).
  System CPU comes from deltas between cumulative OS CPU counters and therefore stays on the
  familiar 0–100% whole-machine scale regardless of logical processor count.
  The first sample is unavailable until a second counter reading establishes an interval.
  The anchored viewport popover keeps to three figures - system CPU, one RAM box, and the
  owned process count - and links to the Resources dialog's Processes segment for detail;
  per-Project, daemon/infrastructure, and duplicated-tooling breakdowns were removed from it
  (2026-08-26) to keep it small.
  Its top CPU figure is system-wide; memory and process counts remain explicitly owned, the
  RAM box preferring the reclaimable (unique-set) total the open panel samples over the
  working set.
  Daemon accounting still includes the daemon plus descendant infrastructure PIDs not already
  attributed to a session, preventing double counting in that owned total.
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
The always-mounted rail polls the summary projection every 10 seconds.
Fleet and keybinding safety refreshes run once per minute and event-driven invalidation remains
the primary refresh path.

The popover also names **duplicated per-session tooling**: language servers are per-session, so
N sessions open on one repo run N independent indexes of the same code. On a four-session
project that was the largest addressable line item and invisible in a flat process list, where
every copy is just another `node.exe` under its own session. The panel reports it and nothing
more — swe-mux does not reap or share language servers.

## Preview contract

This section is the `loopback` kind. The `static` kind shares every surface above the fetch
and differs where § Static document previews says it does.

- Registrations identify one endpoint within a canonical Project and record the live session
  that actually owns its listener. The URL must use literal
  `127.0.0.1` or `::1`, contain no credentials/query/fragment, and either match the host
  and port of a listener owned by some session in that Project or carry explicit user approval.
  Clicking a URL printed by another session therefore attributes the Preview to the listener
  owner, and the same Project/scheme/host/port can never create a second registration.
- The registry separates route-only identities from listed Previews.
  Automatic listener discovery creates a route-only identity so sandboxed Preview traffic can reach sibling Project services.
  A bounded HTTP probe automatically lists 2xx HTML/XHTML responses, HTML signatures, and relative redirects as browser-facing Previews.
  Authenticated, error, JSON, debugger, tool-bridge, non-HTTP, and unreachable endpoints stay route-only.
  Negative results retry with exponential backoff capped at five minutes and reset immediately when listener process identity changes.
  A terminal-link click, Processes action, or explicit `POST /previews` also promotes the stable identity to `listed=true`.
  `GET /previews` and the sidebar expose listed Previews only.
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
  Every detected loopback listener receives a route-only identity owned by its actual session without opening a workspace tab.
  Browser classification may add navigation without opening a workspace tab.
  Selecting a raw listener in Processes lists, opens, or activates that registered endpoint.
- Sidebar child rows are listed Previews only.
  Raw listeners are not asserted to be application servers because agent runtimes, browser debuggers, and tool bridges also bind loopback ports.
  Current-version ownership, liveness, and loopback reachability make a listener eligible as a Processes candidate; the browser probe or an explicit action makes it navigation.
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
- Registrations live with the daemon. An ended source session returns `preview unavailable`.
- **A registration id survives the daemon that made it.** The id is the path segment of the
  proxy route (`/preview/<id>/`), which is how a phone reaches a dev server over the tailnet,
  and the pane offers a button to copy that URL.
  It is derived (`preview_id`) from the endpoint identity the registry already dedupes on -
  Project, scheme, host, port - so re-detecting the same server after a restart reproduces the
  same id and every copied link keeps working.
  Session id is deliberately not in the material, because ownership legitimately moves between
  sessions in the same Project and the registry already reassigns it while keeping the id.
  Minting it from `uuid4` instead made every such URL die on any daemon restart - a redeploy, a
  "Reload daemon", a crash - while the server itself kept running perfectly, so the failure
  read as "the redeploy broke my server" when only the route to it had been re-keyed.
- **User-approved registrations are mirrored to `<data_dir>/previews.json`** (`preview_store.py`)
  and restored at startup. Detected ones are deliberately not: they come back from the live
  listener set within a poll, under the same id, so persisting them would only mirror state
  that can go stale. An approved registration is the opposite case - it exists precisely
  because mux could not attribute the listener (WSL, Docker, a tree mux does not own), so
  nothing will ever rediscover it, and before this it vanished on every restart. Removal is
  mirrored too, or the next restart would resurrect the one the user just deleted. The file is
  a mirror and never authoritative: unreadable content costs the approved previews and never
  the daemon's ability to start.
- **A redeploy reports what it will make unreachable, and refuses nothing for it.**
  `POST /api/daemon/redeploy` returns the affected previews in its 202, and
  `GET /api/daemon/redeploy` serves the same list whether or not one is in flight, so the
  confirm dialog can show it at the one moment it can change a decision. The payload states
  `kills_processes: false` in as many words: a redeploy never stops a dev server
  (`stop_app_processes` targets the app's own image, and even the blunt
  `force_stop_app_images` escalation is scoped to `swe-mux.exe`), it only takes the proxy away
  for the length of the restart. This is advisory on purpose - refusing a redeploy because a
  port is open would make it nearly un-runnable, since there is almost always a dev server up
  and redeploy is the only mechanism that ships anything, including the fix for a gate that
  refuses wrongly. The one genuine blocker, a process anchoring the bundle, remains a separate
  refusal (`409 bundle_in_use`) because that one really does fail the swap.

## Static document previews

A **static preview** is a second registration *kind*, not a second subsystem. It serves a
directory of the Project checkout from the daemon itself, with no process, no port, and no
owning session. Everything above the fetch is the shared Preview machinery unchanged: the
`/preview/<id>/` route a phone opens over the tailnet, the sidebar row, the workspace leaf
with its viewport presets, refresh, copy-URL, external open, and capture.

- `PreviewRegistration.kind` is `loopback` (proxied to a session-owned development server) or
  `static`. Every behavioural difference is gated on that field explicitly, never on "the
  session id is empty", so a future unowned kind cannot inherit a rule by accident.
- **The registration serves a directory and names an entry file within it.** A page's own
  `./style.css` and `../assets/x.png` are the normal case, and serving a single file would
  404 every one of them. The default doc root is the file's own folder; `scope: "project"`
  widens it to the whole checkout for a built page whose absolute paths are repo-root
  relative. Root-relative references are handled by the same `rewrite_preview_html`
  prefixing the loopback proxy uses, so `/app.css` resolves under the served directory.
- Only `.html`, `.htm`, and `.xhtml` may be an *entry* (`STATIC_PREVIEW_ENTRY_SUFFIXES`).
  Anything at all may be fetched as a subresource of the page that is served. A preview is a
  page; offering one on a stylesheet or a lone image would open a viewport showing something
  the file tab already shows better.
- **`static_preview_id` keeps the same bookmark contract as `preview_id`.** It is derived
  from Project, worktree, doc root, and entry, so re-previewing the same document reproduces
  the route and a copied tailnet link survives every daemon restart. Session id is absent
  from the material because there is no session. Re-registering is idempotent: pressing
  "preview" twice reactivates the existing registration rather than minting a rival on a new
  URL.
- The registration carries the exact `worktree` it was resolved inside (`""` for the Project
  root) and `doc_root_relative`, the served directory expressed in the checkout-relative
  paths the Project file watcher speaks. Without the first, a preview opened from a worktree
  file tab silently serves the primary checkout's copy of the same path; without the second,
  the browser would have to subtract one absolute path from another across two path syntaxes
  to know which change events are its own.
- **Static registrations are mirrored to `previews.json` and are never pruned.** They are the
  approved-preview case carried further: an approved preview exists because mux could not
  attribute a listener, while a static one has no listener at all, so no poll could ever
  bring it back and no absence could ever mean it stopped.
- **The session-liveness gate applies to `loopback` only.** A loopback preview points at a
  listener a session owns, so an ended session means the destination is gone. A static
  preview points at bytes in a Project that outlives every session.
- Read-only by construction: `GET`/`HEAD` answer, everything else is `405`. There is no
  upstream here, so a write has nothing it could mean. Containment is enforced by
  `project_path`, which rejects absolute paths and `..` segments and then re-checks the
  resolved target against the served directory, so neither a crafted tail nor a symlink
  inside the directory reaches outside it. A leading `/` on a tail is the route separator,
  not an escape; it resolves inside the directory as an ordinary hit or miss. Responses are
  capped at `PREVIEW_RESPONSE_BYTES` and carry `Cache-Control: no-cache`.
- **Web content types are stated, not guessed.** On Windows `mimetypes` consults the
  registry, where `.js` is routinely `text/plain` and `.css` sometimes is; combined with the
  `X-Content-Type-Options: nosniff` every response carries, that renders the page unstyled
  and scriptless with nothing in the network log to explain it.
- **A static preview document carries `Content-Security-Policy: sandbox allow-scripts
  allow-forms allow-popups allow-modals`.** The in-app iframe already withholds
  `allow-same-origin`, but the pane's `external` button navigates to the route directly on
  the daemon's own origin, and that origin *is* the authority - swe-mux has no login, so
  anything same-origin can drive the API. The CSP sandbox puts the document in an opaque
  origin however it was reached, and `security_middleware` refuses an `Origin: null` mutation
  outside `/preview/`. The cost is that a previewed page has no `localStorage`, which matches
  what the iframe already gave it. `frame-ancestors 'self'` is restated in that header
  because setting a CSP at all replaces the blanket preview policy.
- **Capture points Playwright at the daemon's own loopback proxy route** rather than at a
  port, so the screenshot is of exactly what the pane draws instead of a second render path
  that could drift from it. The shot still lands in the owning Project's `.swe-mux`, resolved
  from `project_id` since there is no session to resolve it from.
- The pane offers a `live` toggle for static previews. The lease on the served directory is
  held only while it is on, and a change under that directory bumps the iframe. It is a
  toggle rather than the behaviour because a page holding state is not worth blowing away on
  every keystroke-save, and an unwatched directory costs the daemon nothing.
- Entry points are the file browser's row menu (`Preview in a pane`), an open HTML file tab's
  own header (`preview`), and the command palette (`preview.file`, on the focused tab). All
  three call one `POST /api/previews` with `kind: "static"`. The launching view's id is sent
  as `target_view_id` so the preview lands as a tab in that pane rather than splitting an
  unrelated one.
- The sidebar lists static previews as Project-level rows rather than under a session, which
  is what they are. Closing the tab leaves the registration standing; the row reattaches it,
  the same contract a detected server's row has. Because that contract means nothing else
  ever retires one - and unlike a detected preview, no stopped listener will - the row
  carries the remove control that does, resting hidden and appearing on hover or focus,
  with a right-click menu offering the same `Close preview`. Both cover the whole row
  including the `×`, which sits outside `.sidebar-note-row` and would otherwise let a
  right-click fall through to the sidebar's background menu. Neither is offered on a
  session-owned preview row: that one follows its listener and is retired by the listener
  stopping, so there is nothing for a menu to do.

## Preview capture

- The preview rail can screenshot the live loopback server headlessly and copy a reference
  to the clipboard (never sends to an agent, never writes a PTY). Full capture or a
  drag-selected region.
- Server-side rendering uses the optional `preview-capture` extra (Playwright + Chromium).
  Absent, the endpoint returns a typed `{available: false, state, reason, remedy}` — an optional
  integration, never a failure. Any render error returns `{available: true, error}` (502),
  not a 500. Rendering uses `wait_until="load"` (an HMR dev server never reaches
  `networkidle`) and points Playwright at the standard per-user browser cache so a frozen
  desktop build resolves the browser installed by `playwright install`.
- **The two halves of that backend fail separately, so they are never reported together.**
  The Python package can be absent (`extra_missing`) or present with no browser binary under it
  (`browser_missing`), and a fresh install can be in either.
  They need different commands, so one "capture unavailable" sent an operator who had already
  installed the extra to install it again and get nowhere.
  `state` is the machine-readable discriminator and `remedy` is the command for *that half only*
  — this is the discipline `design/features/agent-environment.md` states for an empty MCP
  catalog: an absent capability must say which kind of absent it is.
  `remedy` is `null` where no command on this machine helps, which is the honest answer on the
  packaged desktop app: `preview-capture` is outside `DISTRIBUTED_EXTRAS`
  (`packaging/license_audit.py`), so the bundle carries no Playwright and a `uv sync` against the
  source tree cannot reach its interpreter.
- **Nothing installs or downloads either half.** `playwright install chromium` is a large network
  fetch, and a daemon that runs it because someone pressed Capture is the silent first-use cost
  this reporting exists to remove.
  Detection is two local reads (an import, and a scan of the browsers roots), so an operator who
  runs the remedy sees the state change on the next press with no daemon restart.
  The scan can be wrong in one direction — a browsers root this host uses that it does not know
  about — so a launch that fails with Playwright's own missing-executable error is promoted to
  the same `browser_missing` state rather than surfacing as an unactionable capture failure.
- The state is also a row in the consolidated `mux doctor` report
  (`optional_asset:preview_capture`, plus a `capabilities.optional_assets` entry), at severity
  `optional` so an uninstalled optional feature never fails the report. That is the *proactive*
  surface; pressing Capture is still what probes, because a probe on every preview list would
  import Playwright on a polling path.
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
- Inspector (the act surface, modal): `frontend/src/ProcessPanel.tsx`, `frontend/src/processRows.ts`
  (the pure row model: command-tail stripping, the abnormal-state rule, detail assembly, rollup
  suppression), `frontend/test/renderer/process-fleet-layout.spec.ts` (the density geometry)
- Drawer watch tab: `frontend/src/ProcessesTab.tsx`, `frontend/src/processWatch.ts` (the pure row
  model: rollups, focused-first ordering, ended-process rules)
- Resource summary: `frontend/src/ResourceUsage.tsx`, `frontend/src/resourceTotals.ts`
- Duplicate tooling classification: `frontend/src/resourceTooling.ts`
- Static previews: `src/swe_mux/processes.py` (`static_preview_id`, `static_preview_url`,
  `PreviewRegistry.register_static`), `src/swe_mux/project_files.py`
  (`read_static_preview_file`, `is_static_preview_entry`,
  `STATIC_PREVIEW_ENTRY_SUFFIXES`), `src/swe_mux/server.py`
  (`_register_static_preview`, `_serve_static_preview`, `static_preview_content_type`),
  `frontend/src/staticPreview.ts` (the client-side entry allowlist),
  `tests/test_static_preview.py`
- Preview leaf + capture/region UI: `frontend/src/PreviewPane.tsx`, `frontend/src/previewCapture.ts`
  (the pure unavailable-state wording), `frontend/test/previewCapture.test.ts`
- Headless capture (optional Playwright): `src/swe_mux/preview_capture.py`,
  `tests/test_first_use_assets.py`
- Terminal-link routing: `frontend/src/TerminalPane.tsx`, `frontend/src/previewLinks.ts`
