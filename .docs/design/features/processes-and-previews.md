# Process ownership and previews

## What it is

- Per-session descendant attribution and bounded resource/listener snapshots.
- Explicit preview leaves for a detected or user-approved literal-loopback development server.

## Ownership and actions

- Windows assigns every root PTY process to the daemon kill-on-close job and also attempts
  a nested per-session Job Object. PID plus process creation time prevents PID-reuse errors;
  periodic `psutil` reconciliation records descendant appearance and exit.
- Snapshots cap retained records and expose parent, executable label/command, start/exit,
  CPU, RSS, listeners, and measurable warning conditions. The inspector nests descendants
  by PID/parent PID; processes whose parent is outside the owned snapshot remain visible as
  roots. A no-output or high-resource label is diagnostic only; swe-mux never claims a
  server is hung or auto-kills it.
- Interrupt, terminate-process, and terminate-tree re-check live ownership. A request for
  a PID owned by another session is rejected.
- If optional process inspection support is unavailable, the API returns a typed diagnostic
  and all terminal/session behavior continues.
- The inspector opens from session/terminal right-click, pane-header `proc`, sidebar
  `: menu` Process fleet, or the command palette. Fleet mode uses the coherent all-session
  snapshot when available and falls back to aggregating session-scoped snapshots for an
  older running daemon; listener rows expose the preview action at the point of discovery.

## Preview contract

- Registrations belong to one live session and space. The URL must use literal
  `127.0.0.1` or `::1`, contain no credentials/query/fragment, and either match the host
  and port of a listener owned by that session or carry explicit user approval.
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
- A server belongs beside whatever spawned it. Attaching a preview groups it as a tab in the
  region that already holds its owning session, so an agent and the services it started share
  one tab strip; it only falls back to a split when that session has no terminal in the
  layout. The same registration is nested under its session's sidebar row and activates that
  tab when selected. A detected loopback listener with no registration yet gets the same row
  and opens as a preview when selected.
- Sidebar rows are servers only. A live loopback listener is the sole test, since the rest of
  a session's tree is bookkeeping that no age or liveness filter distinguishes from signal;
  exited records and non-loopback listeners are excluded, and a port bound on both 127.0.0.1
  and ::1 collapses to one row. Descendant shells have no PTY, are never terminal tabs, and
  are never sidebar rows: the process inspector remains the one place showing the full tree.
  The sidebar read reuses the inspector's existing cached sample, so it adds no process
  enumeration.
- Preview leaves use `/preview/{registration}/…`; phones and desktop browsers never need
  the development server's raw port. The registered origin is immutable per request and
  redirects to another origin are rejected, so the route cannot become a network proxy.
- HTTP methods, bodies, queries, root-relative HTML/CSS/module paths, runtime fetch/XHR,
  WebSocket messages, and negotiated HMR subprotocols traverse the bridge.
  Browser Origin is replaced with the registered loopback origin for common dev servers.
- HTTP requests/responses are capped at 10/20 MiB with a 15-second total timeout and 32
  concurrent requests. WebSocket messages are capped at 4 MiB with 16 concurrent bridges,
  30-minute idle timeout, and 12-hour lifetime; clients may reconnect normally.
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

## Key files

- Ownership/registry: `src/swe_mux/processes.py`
- Job boundary: `src/swe_mux/win_jobobj.py`, `src/swe_mux/session.py`
- Inspector: `frontend/src/ProcessPanel.tsx`
- Preview leaf: `frontend/src/PreviewPane.tsx`
