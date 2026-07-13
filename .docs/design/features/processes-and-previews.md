# Process ownership and previews

## What it is

- Per-session descendant attribution and bounded resource/listener snapshots.
- Explicit preview leaves for a detected or user-approved loopback development server.

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

- Registrations belong to one session and space. The URL must be loopback and either match
  a listener owned by that session or carry explicit user approval.
- A preview is a real recursive-layout leaf; opening it does not replace or terminate its
  source terminal. The leaf supports refresh, mobile/tablet/fit viewport presets, copy,
  external open, mobile full-screen, and ownership/source status.
- Direct iframe embedding is best effort and remains usable through external open when an
  application blocks framing. Registrations live with the daemon; a restored stale leaf
  renders `preview unavailable`.
- The authenticated HTTP/WebSocket/HMR proxy is intentionally Phase 5 because it requires
  the remote threat model, Origin/CSP limits, and SSRF defenses. Phase 4 does not rewrite
  application content or proxy arbitrary URLs.

## Key files

- Ownership/registry: `src/swe_mux/processes.py`
- Job boundary: `src/swe_mux/win_jobobj.py`, `src/swe_mux/session.py`
- Inspector: `frontend/src/ProcessPanel.tsx`
- Preview leaf: `frontend/src/PreviewPane.tsx`
