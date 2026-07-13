# Process ownership and previews

## What it is

- Per-session descendant attribution and bounded resource/listener snapshots.
- Explicit preview leaves for a detected or user-approved literal-loopback development server.

## Ownership and actions

- Windows assigns every root PTY process to the daemon kill-on-close job and also attempts
  a nested per-session Job Object. PID plus process creation time prevents PID-reuse errors;
  periodic `psutil` reconciliation records descendant appearance and exit.
- Snapshots cap retained records and expose parent, executable label/command, start/exit,
  CPU, RSS, listeners, and measurable warning conditions. A no-output or high-resource
  label is diagnostic only; swe-mux never claims a server is hung or auto-kills it.
- Interrupt, terminate-process, and terminate-tree re-check live ownership. A request for
  a PID owned by another session is rejected.
- If optional process inspection support is unavailable, the API returns a typed diagnostic
  and all terminal/session behavior continues.
- The inspector opens from session/terminal right-click, pane-header `proc`, `: menu`, or
  the command palette; listener rows expose the preview action at the point of discovery.

## Preview contract

- Registrations belong to one live session and space. The URL must use literal
  `127.0.0.1` or `::1`, contain no credentials/query/fragment, and either match the host
  and port of a listener owned by that session or carry explicit user approval.
- A preview is a real recursive-layout leaf; opening it does not replace or terminate its
  source terminal. The leaf supports refresh, mobile/tablet/fit viewport presets, copy,
  external open, mobile full-screen, and ownership/source status.
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
- Registrations live with the daemon. An ended source session returns `preview unavailable`;
  daemon restart leaves any restored preview leaf stale rather than guessing a destination.

## Key files

- Ownership/registry: `src/swe_mux/processes.py`
- Job boundary: `src/swe_mux/win_jobobj.py`, `src/swe_mux/session.py`
- Inspector: `frontend/src/ProcessPanel.tsx`
- Preview leaf: `frontend/src/PreviewPane.tsx`
