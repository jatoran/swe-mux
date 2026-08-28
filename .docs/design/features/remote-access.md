# Remote access

## What it is

The same browser/API surface over localhost or a policy-controlled direct Tailscale
listener, with optional Tailscale Serve for browser-recognized HTTPS.

## Supported topology

- muxd binds localhost plus the IPv4 returned by `tailscale ip -4` when
  `tailnet_enabled = true` (default). Detection failure leaves localhost operational and
  reports a diagnostic. `--local-only` disables the tailnet site for one run.
- Local and tailnet browsers/CLI clients connect directly without a swe-mux login.
  Tailscale policy controls the tailnet listener. Tailscale IP validation accepts only
  CGNAT `100.64.0.0/10`; arbitrary non-loopback config remains invalid.
- Both listener sites expose the same UI/API: terminals, notes, history, process actions,
  and registered HTTP/WebSocket/HMR previews. Development servers remain on workstation
  loopback; remote browsers reach them only through `/preview/{registration}/…`. Absolute
  loopback fetch/XHR/WebSocket calls between services in one Project are remapped to the
  destination service's registered route, never to the remote device's loopback.
  A **static** preview reaches a remote browser through the same route with no server
  involved: the daemon reads the document from the Project checkout. Its route id is derived
  from the served directory rather than a port, so a link copied to a phone survives daemon
  restarts, and its document carries a `sandbox` CSP so opening it outside the app's iframe
  cannot borrow the origin's authority (`features/processes-and-previews.md`).
- `0.0.0.0`, LAN interfaces, port forwarding, and Tailscale Funnel are unsupported.
- An SSH local forward is supported when its browser-facing address is loopback.
  For example, `ssh -L 9876:127.0.0.1:8765 workstation` exposes mux at
  `http://127.0.0.1:9876/` on the SSH client.
  The local port may differ from the daemon port because loopback Host and Origin authorities
  remain allowed.
  Addressing that forward through a LAN hostname or non-loopback address is rejected as
  `unsupported Host`.
  This is an authenticated SSH tunnel to the existing loopback listener, not router port
  forwarding and not Tailscale Funnel.
- Direct tailnet HTTP remains the supported remote transport and fallback. Browser microphone
  capture requires a secure context, so swe-mux provisions a private Tailscale Serve listener on
  HTTPS 443 (`https://<device>.ts.net/`) that proxies to the loopback port. 443 is required, not
  the swe-mux port: the daemon binds its port directly on the Tailscale IPv4 address for the
  HTTP fallback, so a Serve listener on that same port would collide with that host socket. Serve
  is brought up automatically at daemon startup (best-effort) and is idempotent; the mobile-voice
  setup route re-runs the same bounded command for one-time Tailscale HTTPS approval or repair.
  When Serve cannot be configured, the route returns a diagnostic and leaves ordinary mobile
  access unchanged. swe-mux never enables Funnel or changes tailnet policy, ACLs, shields, or
  device authorization.

## Boundaries and diagnostics

- Settings and `mux doctor` report localhost, detected tailnet address/direct URL,
  listener state, secure mobile URL/status, and Funnel warning. `POST
  /api/remote/mobile-voice/enable` requires the dedicated explicit-action header and returns a
  secure URL only when one is actually available; otherwise it returns a non-destructive error.
- `GET /api/remote/status` also reports the real Tailscale connection state, not only whether the
  CLI is on PATH.
  It reads `BackendState` and `Self.DNSName` from `tailscale status --json` and classifies the
  connection as not-installed, logged-out, connecting, needs-machine-auth, stopped, or
  connected-as-`<device>.ts.net`, each with the exact next command.
  `src/swe_mux/tailscale.py` `classify_tailscale_connection` is the pure classifier and is unit
  tested against real `BackendState` fixtures.
  The Settings Remote and Voice tabs render this state and cause-pointing text, so a logged-out
  or stopped tailnet reads as unconfigured rather than broken.
- The daemon cannot see the phone's DNS state, so the Remote and Voice tabs state it as a
  checklist: enable "Use Tailscale DNS" on the phone, and on Android set Private DNS to off or
  automatic.
  A missing Tailscale DNS setting is the most common silent first-connect failure.
- Windows only: swe-mux binds a real host socket on the `100.x` tailnet address, so Windows
  Defender Firewall governs inbound to `swe-mux.exe` on the Private profile for the direct
  `100.x:<port>` HTTP path.
  It does not govern the secure Tailscale Serve path: Serve terminates TLS in `tailscaled` on 443
  and proxies to `127.0.0.1:<port>`, which is a loopback connection the firewall never blocks.
  So when Serve is up (the normal case, auto-started at boot), the phone connects regardless of the
  firewall, and a missing or blocking inbound rule only affects the direct `100.x` fallback.
  The inspection therefore reads Serve state (`serve_active`) and only raises the repair alarm
  (`needs_repair`) when Serve is down and the direct path is the phone's only route; with Serve up
  it reports the direct-fallback rule state as a quiet note (`direct_path_blocked`) instead.
  `GET /api/remote/firewall` inspects for a blocking or missing rule, and `POST
  /api/remote/firewall/repair` (explicit-action header required) runs a one-click elevated
  PowerShell repair that removes conflicting Block rules and adds one scoped inbound Allow rule.
  Both are inert off a frozen Windows build (`firewall_supported` is false), because the rule must
  target the packaged `swe-mux.exe`, not a transient `python.exe`.
  The scope check is tailnet-specific: a sufficient Allow rule must cover the whole
  `100.64.0.0/10` range, because the phone connects from an unknown address inside it.
  `src/swe_mux/windows_firewall.py` owns the inspect/repair logic and its unit tests.
- `GET /api/diagnostics/export` returns one copyable bundle for a connection report: sanitized
  config (`public_dict`, no secrets), remote-connection state, firewall status, network counters,
  the fleet status-health aggregate, and the tails of `daemon.log` and `redeploy.log`.
  It never includes terminal bytes or message content; the two logs are command-free by design.
  `mux doctor --export` prints the same bundle from the CLI, and Settings → Remote copies it to
  the clipboard with a selectable textarea fallback for plain-HTTP tailnet clients where the
  Clipboard API is restricted.
- A "Connect a phone" modal (reachable from Settings → Remote) renders a scannable QR of the
  connection URL alongside the hostname, the DNS checklist, and the live connection state. The URL
  is built from the `.ts.net` MagicDNS name, not the raw `100.x` IP: the HTTPS certificate is bound
  to the name, and it prefers the secure Serve address when one is up. The QR is a self-contained
  inline SVG (the `qrcode-generator` dependency), rendered on a white plate so it scans in either
  theme.
- `GET /api/diagnostics/prerequisites` reports the presence of Git, Node, npm, and Tailscale, each
  with what it backs and a next step, so a feature that needs an absent tool reads as unconfigured
  rather than broken. It is surfaced in Settings → Remote.
- The tailnet-only, no-login posture is stated in the UI, not only in this doc: Settings → Remote
  and the Connect-a-phone modal both carry a line that any tailnet device reaches the daemon with
  full terminal and code-execution authority and no login, so the listener must not be enabled on a
  shared tailnet.
- **Outbound, and the one exception to "no telemetry".** Everything above is about who may
  reach *in*. The daemon makes exactly one request *out* on its own behalf: the release
  update check (`update_check.py`, `GET /api/update`, `design/interfaces.md`), a daily
  `GET` of `https://swemux.dev/version.json` with a GitHub Releases fallback.
  The README and `SECURITY.md` both say the project has no telemetry, and this is what
  keeps that literally true rather than approximately true: the request carries no query
  string, no custom header, no cookie jar, no body, and no identifier of this machine or
  install, so it is byte-identical for every copy of swe-mux on earth and the server
  learns nothing from it that an IP address does not already say.
  It is gated by `update_check_enabled` (Settings → Diagnostics → **Software updates**,
  on by default), and off means no request is made at all - not a reduced one, not a
  deferred one. Nothing downloads or installs.
  Installing an update **does** download, and it stays on the operator's side of that line:
  it happens only on an explicit act naming a version (`POST /api/update/install`,
  `mux update --install`, `design/interfaces.md`), it fetches the artifact the manifest
  names from GitHub Releases, and it verifies the SHA-256 before anything is staged. The
  same switch gates it, so "off means nothing leaves the machine" holds for both halves.
  Every other outbound path in the app belongs to a feature the operator turned on and
  points at a service they configured: the OpenRouter-compatible endpoint for
  summarization and the assistant, the browser vendor's web-push service, and experimental
  Edge TTS. The agent CLIs' own traffic is theirs, under the operator's own subscription.
- Browser control validates Host plus the full Origin authority, including an explicit
  port, on mutations and WebSocket upgrades. Responses carry CSP, nosniff, referrer,
  permissions, opener, and resource policy headers.
- Expensive/upload/bridge routes use targeted body, response, concurrency, idle, and
  duration limits. No blanket per-user limiter or enterprise audit subsystem exists;
  privileged user actions enter the existing metadata-only EventBus.
- `GET /api/diagnostics/network` reports in-memory HTTP body and WebSocket application-frame counters for the current daemon boot or explicit measurement window, grouped by bounded route template, socket channel, and peer address.
  Sent PTY binary frames also have a peer-aware payload-phase breakdown: initial attach replay, resynchronization replay, and live output.
  `DELETE /api/diagnostics/network` logs the previous totals and begins a fresh window without
  restarting the daemon or any session.
  Reads and resets of this endpoint are excluded from their own counters.
  The app menu's `Bandwidth usage…` modal refreshes these counters while open, presents totals, routes, WebSocket channels, PTY download phases, and peers, and can reset the measurement window in place.
- HTTP response counts are encoded body bytes after negotiated compression.
  They exclude headers, TLS, Tailscale, and packet overhead.
  WebSocket counts are text/binary frame payload bytes before per-message compression and exclude control frames.
  PTY phase rows are a classified subset of the WebSocket download total and must not be added to it.
  Use an OS or browser wire capture when transport-exact totals are required.
- Forwarded peer identity is accepted only from a valid `X-Forwarded-For` address received
  through a loopback peer, which covers the local Tailscale Serve proxy without trusting a
  direct tailnet client's header.
- Dynamic JSON and text responses of at least 1 KiB negotiate compression.
  Production frontend builds also create gzip siblings for eligible static assets, which
  aiohttp serves according to `Accept-Encoding`.
- The root document uses `Cache-Control: no-cache, must-revalidate` because it names content-addressed assets and carries the production UI identity.
  Hashed `/assets/` responses use a one-year immutable cache because a changed payload receives a changed filename.
- The three Git readings - `GET /api/git/worktrees`, `/api/git/graph`, and `/api/git/provenance` - are the **conditional** API responses: a weak `ETag` over the exact bytes served, with `Cache-Control: no-cache`.
  They earn it because every open client refetches them on any session's dirty tick and the great majority of those answers are byte-identical to the one the client already holds - so the common case becomes a request with no body at all, over a link that may be a phone on a tailnet.
  The ledger gains most in absolute terms: it is the largest payload this daemon serves (994KB, 140KB compressed, at 500 rows), and it is append-mostly.
  `no-cache` is "revalidate before every use", not "do not store"; without it a browser never sends `If-None-Match` and the conditional never happens.
  The client code is unchanged, because `fetch` turns the 304 back into a 200 from its own cache.
  Compression makes bytes smaller; this is the only mechanism here that makes them absent, which is why the same endpoint also serves a `detail=summary` reading that withholds per-file lists nothing on screen is drawing (`git.md`).
- A cold `/events` socket receives only the current durable sequence watermark, because the
  initial REST snapshot already supplies authoritative state.
  Reconnect replay is capped at 64 records; a wider gap sends one watermark and triggers one
  full refresh instead of transferring partial history.
  Provider hook audit records remain durable but their `PreToolUse`, `PostToolUse`, `tool_use`,
  and `tool_result` payloads are not sent to browsers because no browser state consumes them.
- Mobile workspaces mount only visible terminal panes.
  Desktop may keep up to three hidden panes warm for fast tab switching, but mobile avoids
  paying for offscreen PTY output over a metered connection.
- There is no swe-mux bearer/login path. Tailscale policy decides which devices/users may
  reach the direct listener or optional Serve endpoint; an admitted peer has terminal and
  code-execution authority.
- Clipboard history widens what an admitted peer can *read*, not what it can do: `/api/clipboard`
  serves recent copied text from every device driving this install. That is the reason the ring is
  memory-only by default, time- and count-bounded, refuses secret-shaped copies, and can be turned
  off entirely (Settings → Input). On direct tailnet HTTP — where the Clipboard API is restricted
  because the URL is not a secure context — the ring is also the practical paste path: insert from
  the panel instead of asking the browser to read the system clipboard.
- Per-session hook secrets remain a separate loopback-only machine integration boundary.
  Hook ingress has a bounded body/burst, event allowlist, constant-time secret check, and
  rejects ended sessions.
- Desktop daemon shutdown is a second machine-local boundary: unavailable for standalone
  daemons, IP-loopback only, and gated by the desktop-generated bearer secret. Tailnet peers
  cannot use it even when admitted to the ordinary UI/API.

## One Serve route, several daemons

Tailscale Serve on 443 is a single machine-wide route, and more than one swe-mux may want it:
the desktop app on 8765, a terminal daemon on another port, a short-lived test instance. The
rule is ownership, not first-come:

- A **foreign** (non-loopback) route is never touched.
- An **abandoned** swe-mux route - loopback, but nothing answering on that port - is
  reclaimable. That is what lets a terminal daemon take over after an unclean exit, and losing
  it would strand the route permanently.
- A route held by a **running** swe-mux is left alone, and the refusal names the port holding
  it and suggests `--local-only`.

That last rule was missing until 2026-08-17, and its absence is not theoretical: a test daemon
on an ephemeral port took the route from the live desktop app, and mobile access stayed broken
after that daemon exited. The damage is entirely one-sided and silent - the victim keeps
serving loopback and never learns it lost the address, so nothing on the desktop looks wrong
while every phone is stranded.

**Any daemon that is not the machine's primary must start with `--local-only`.** An isolated
port and an isolated data directory are not enough: neither of them isolates the tailnet, and
the Serve route is shared regardless of which port or data directory a daemon uses.

## Operator guidance

- Restrict the Tailscale grant to the owning user/devices and this host. Remove a device
  or revoke its tailnet access immediately when it should no longer control swe-mux.
- Treat access through `ssh -L` as terminal and code-execution authority on the mux host.
  The SSH account controls tunnel admission, and swe-mux adds no separate login inside it.
- Direct tailnet HTTP is encrypted by Tailscale, but browsers may restrict Clipboard API
  operations because the URL is not a browser secure context. OSC-52 copy failures retain the
  prepared text for a one-tap retry/selectable fallback. A browser-delivered paste event can
  still carry an image, but proactive clipboard reads (including the long-press Paste fallback)
  require a secure context. Press Talk or Enable secure mobile access to provision the private
  HTTPS address without entering a Serve command, then grant the browser microphone permission.
- Do not enable Funnel. Re-run `mux doctor` or use Settings → Remote and security after
  changing Tailscale/listener configuration.

## Key files

- Listener/config policy: `src/swe_mux/config.py`, `src/swe_mux/__main__.py`
- Browser boundary and status route: `src/swe_mux/server.py`
- Traffic accounting and dynamic compression: `src/swe_mux/network_usage.py`
- Static precompression: `frontend/scripts/compress-static.mjs`
- Tailscale inspection, connection-state classifier, and bounded Serve setup:
  `src/swe_mux/tailscale.py`
- Windows Defender Firewall inspect and repair (platform-gated): `src/swe_mux/windows_firewall.py`
- The single outbound request and its switch: `src/swe_mux/update_check.py`,
  `src/swe_mux/routes/update.py`, `frontend/src/UpdateBanner.tsx`,
  `frontend/src/updateCheck.ts`
- Diagnostics export bundle: `src/swe_mux/server.py` (`diagnostics_export`), `src/swe_mux/cli.py`
  (`mux doctor --export`)
- Settings/status and browser redirect: `frontend/src/Settings.tsx`,
  `frontend/src/mobileVoice.ts`, `frontend/src/ConversationControl.tsx`
