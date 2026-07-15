# Remote access

## Supported topology

- muxd binds localhost plus the IPv4 returned by `tailscale ip -4` when
  `tailnet_enabled = true` (default). Detection failure leaves localhost operational and
  reports a diagnostic. `--local-only` disables the tailnet site for one run.
- Local and tailnet browsers/CLI clients connect directly without a swe-mux login.
  Tailscale policy controls the tailnet listener. Tailscale IP validation accepts only
  CGNAT `100.64.0.0/10`; arbitrary non-loopback config remains invalid.
- Both listener sites expose the same UI/API: terminals, notes, history, process actions,
  and registered HTTP/WebSocket/HMR previews. Development servers remain on workstation
  loopback; remote browsers reach them only through `/preview/{registration}/…`.
- `0.0.0.0`, LAN interfaces, port forwarding, and Tailscale Funnel are unsupported.
- Tailscale Serve is optional HTTPS termination for browser secure-context APIs:
  `tailscale serve --bg http://127.0.0.1:8765`. swe-mux never changes tailnet policy,
  enables Serve, or enables Funnel automatically.

## Boundaries and diagnostics

- Settings and `mux doctor` report localhost, detected tailnet address/direct URL,
  listener state, optional Serve URL/command, and Funnel warning.
- Browser control validates Host plus the full Origin authority, including an explicit
  port, on mutations and WebSocket upgrades. Responses carry CSP, nosniff, referrer,
  permissions, opener, and resource policy headers.
- Expensive/upload/bridge routes use targeted body, response, concurrency, idle, and
  duration limits. No blanket per-user limiter or enterprise audit subsystem exists;
  privileged user actions enter the existing metadata-only EventBus.
- There is no swe-mux bearer/login path. Tailscale policy decides which devices/users may
  reach the direct listener or optional Serve endpoint; an admitted peer has terminal and
  code-execution authority.
- Per-session hook secrets remain a separate loopback-only machine integration boundary.
  Hook ingress has a bounded body/burst, event allowlist, constant-time secret check, and
  rejects ended sessions.

## Operator guidance

- Restrict the Tailscale grant to the owning user/devices and this host. Remove a device
  or revoke its tailnet access immediately when it should no longer control swe-mux.
- Direct tailnet HTTP is encrypted by Tailscale, but browsers may restrict Clipboard API
  operations because the URL is not a browser secure context. OSC-52 copy failures retain the
  prepared text for a one-tap retry/selectable fallback. A browser-delivered paste event can
  still carry an image, but proactive clipboard reads (including the long-press Paste fallback)
  require a secure context. Use optional Serve when those capabilities matter.
- Do not enable Funnel. Re-run `mux doctor` or use Settings → Remote and security after
  changing Tailscale/listener configuration.

## Key files

- Listener/config policy: `src/swe_mux/config.py`, `src/swe_mux/__main__.py`
- Browser boundary and status route: `src/swe_mux/server.py`
- Tailscale inspection: `src/swe_mux/tailscale.py`
- Settings status: `frontend/src/Settings.tsx`
