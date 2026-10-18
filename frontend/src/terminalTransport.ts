/**
 * Which process a browser opens a session's terminal on: the terminal gateway or the
 * daemon (`DAEMON_LOAD_ISOLATION.md` item 30; `design/features/terminal-gateway.md`).
 *
 * The gateway is a process of its own that carries terminals, so typing keeps echoing
 * while the daemon stalls or restarts. The daemon's own `/pty/` stays the fallback, and
 * nothing here may ever leave a terminal with no route: every way the gateway can be
 * wrong - down, restarting, not holding this session - ends on the daemon, and the daemon
 * says so with a close code when the session is the gateway's after all.
 *
 * **What decides.** `/api/health` carries `terminal_transport` - `gateway` with its port
 * while the daemon is connected to one, `daemon` otherwise - and the liveness probe that
 * already polls it feeds it here. The last report is kept, in memory and in
 * `localStorage`, because the moment a terminal most needs the gateway is the moment the
 * daemon cannot answer: a reconnect during a daemon restart, or a page reloaded from the
 * service worker while the daemon is down.
 *
 * **The URL rule.**
 * - The daemon is always same-origin `/pty/<id>`, on every access path.
 * - An `http:` page served by the daemon reaches the gateway directly on its own port,
 *   `ws://<host>:<port>/pty/<id>` - loopback, the tailnet address, the WSL bridge.
 * - An `https:` page cannot open `ws:` to another port, so it goes through the reverse
 *   proxy in front of it: `wss://<host>/gateway/pty/<id>`, which Tailscale Serve mounts on
 *   the gateway (item 31). A mount at `/pty` itself would have been simpler and wrong: it
 *   would take the daemon's `/pty` away from an `https:` page, which is exactly the route a
 *   down gateway has to fall back to.
 * - The Vite dev server proxies `/gateway` the same way, so development is the `https:`
 *   shape over plain HTTP.
 *
 * **Close codes.** 4010 from the gateway is "this session is the daemon's" (in-process
 * and cold sessions); 4011 is "the gateway is going away, reconnect"; 4012 from the
 * daemon is "this session is the gateway's". A gateway that cannot be reached at all is
 * backed off from, doubling to a minute, and the daemon is used meanwhile. Each of these
 * redirects is answered at once rather than after the reconnect backoff, with a guard
 * against two processes bouncing one session between them.
 */

export const CLOSE_USE_DAEMON = 4010
export const CLOSE_GATEWAY_RESTARTING = 4011
export const CLOSE_USE_GATEWAY = 4012

/** The path prefix a reverse proxy (Tailscale Serve, Vite) mounts the gateway under. */
export const GATEWAY_PROXY_PREFIX = '/gateway'
export const TRANSPORT_STORAGE_KEY = 'swe-mux.terminal-transport'
/** A remembered report older than this is not trusted by a page that has none of its own. */
export const REPORT_MAX_AGE_MS = 10 * 60_000
/** How long the gateway's "this session is the daemon's" holds for that session. */
export const SESSION_REFUSAL_MS = 60_000
/** After 4011 the daemon is used for this long while the gateway restarts. */
export const GATEWAY_RESTART_HOLD_MS = 2_000
/** A gateway that could not be reached is not tried again for this long, doubling. */
export const GATEWAY_FAILURE_BASE_MS = 5_000
export const GATEWAY_FAILURE_MAX_MS = 60_000
/** How long the daemon's "this session is the gateway's" outranks a stale report. */
export const GATEWAY_ASSERTION_MS = 10_000
/** Redirects answered at once, per session, inside `REDIRECT_WINDOW_MS`; past this the
 *  ordinary reconnect backoff applies, so a disagreement cannot become a busy loop. */
export const MAX_IMMEDIATE_REDIRECTS = 4
export const REDIRECT_WINDOW_MS = 10_000
/** Delay before an immediate redirect: long enough to leave the close handler. */
export const REDIRECT_DELAY_MS = 50

export type TransportKind = 'gateway' | 'daemon'

export type TerminalTransportReport = {
  transport: TransportKind
  state: string
  port: number | null
  reason?: string
}

export type PtyRoute = { via: TransportKind; url: string }

/** What a page's location is read for. */
export type PageLocation = { href: string; protocol: string }

export type StorageLike = Pick<Storage, 'getItem' | 'setItem'>

/** A report off the wire, or null when it is not one: anything unreadable is the daemon. */
export function parseTerminalTransport(value: unknown): TerminalTransportReport | null {
  if (!value || typeof value !== 'object') return null
  const record = value as Record<string, unknown>
  const transport = record.transport
  if (transport !== 'gateway' && transport !== 'daemon') return null
  const state = typeof record.state === 'string' ? record.state : ''
  const rawPort = record.port
  const port = typeof rawPort === 'number' && Number.isInteger(rawPort) && rawPort > 0 && rawPort < 65536
    ? rawPort
    : null
  if (transport === 'gateway' && port === null) return null
  const reason = typeof record.reason === 'string' && record.reason ? record.reason : undefined
  return { transport, state, port, ...(reason ? { reason } : {}) }
}

/** The daemon's terminal URL: same-origin `/pty/<id>`, whatever the access path. */
export function daemonPtyUrl(sessionId: string, page: PageLocation): string {
  const url = new URL(`/pty/${encodeURIComponent(sessionId)}`, page.href)
  url.protocol = page.protocol === 'https:' ? 'wss:' : 'ws:'
  return url.toString()
}

/** The gateway's terminal URL for this page (see the module comment for the rule). */
export function gatewayPtyUrl(
  sessionId: string,
  page: PageLocation,
  port: number,
  { proxied = false }: { proxied?: boolean } = {},
): string {
  const path = `/pty/${encodeURIComponent(sessionId)}`
  if (page.protocol === 'https:' || proxied) {
    const url = new URL(`${GATEWAY_PROXY_PREFIX}${path}`, page.href)
    url.protocol = page.protocol === 'https:' ? 'wss:' : 'ws:'
    return url.toString()
  }
  const url = new URL(path, page.href)
  url.protocol = 'ws:'
  url.port = String(port)
  return url.toString()
}

type Clock = () => number

/** The state one page holds about where terminals go. One instance per page in use. */
export class TerminalTransportState {
  report: TerminalTransportReport | null = null
  reportedAt = 0
  /** The gateway port last reported, kept after a report says `daemon`, so the daemon's
   *  4012 can still be followed before the next health answer arrives. */
  lastGatewayPort: number | null = null
  private refusedUntil = new Map<string, number>()
  private assertedUntil = new Map<string, number>()
  private redirects = new Map<string, number[]>()
  private gatewayHeldUntil = 0
  private gatewayFailures = 0
  private readonly now: Clock
  private readonly storage: StorageLike | null
  private readonly proxied: boolean

  constructor({ now = () => Date.now(), storage = null, proxied = false }: {
    now?: Clock; storage?: StorageLike | null; proxied?: boolean
  } = {}) {
    this.now = now
    this.storage = storage
    this.proxied = proxied
    this.restore()
  }

  /** A `terminal_transport` from `/api/health`. */
  note(value: unknown): void {
    const report = parseTerminalTransport(value)
    if (!report) return
    const becameGateway = report.transport === 'gateway' && this.report?.transport !== 'gateway'
    this.report = report
    this.reportedAt = this.now()
    if (report.port !== null) this.lastGatewayPort = report.port
    // A gateway newly up is worth trying at once, whatever the last one did.
    if (becameGateway) { this.gatewayFailures = 0; this.gatewayHeldUntil = 0 }
    this.persist()
  }

  /** Where to open `sessionId`'s terminal now. */
  route(sessionId: string, page: PageLocation): PtyRoute {
    const now = this.now()
    const port = this.gatewayPortFor(sessionId, now)
    if (port !== null) return { via: 'gateway', url: gatewayPtyUrl(sessionId, page, port, { proxied: this.proxied }) }
    return { via: 'daemon', url: daemonPtyUrl(sessionId, page) }
  }

  private gatewayPortFor(sessionId: string, now: number): number | null {
    if ((this.refusedUntil.get(sessionId) ?? 0) > now) return null
    if (this.gatewayHeldUntil > now) return null
    if (this.report?.transport === 'gateway' && this.report.port !== null) return this.report.port
    if ((this.assertedUntil.get(sessionId) ?? 0) > now) return this.lastGatewayPort
    return null
  }

  /** A socket opened; a gateway that answers is a gateway worth trying again. */
  opened(sessionId: string, via: TransportKind): void {
    if (via !== 'gateway') return
    this.gatewayFailures = 0
    this.assertedUntil.delete(sessionId)
  }

  /**
   * A socket closed, or never opened (`code` 0 for a handshake that stalled). Returns
   * whether the next attempt should go at once, because the close named where to go.
   */
  closed(sessionId: string, via: TransportKind, code: number, opened: boolean): { immediate: boolean } {
    const now = this.now()
    let redirect = false
    if (via === 'gateway' && code === CLOSE_USE_DAEMON) {
      this.refusedUntil.set(sessionId, now + SESSION_REFUSAL_MS)
      this.assertedUntil.delete(sessionId)
      redirect = true
    } else if (via === 'gateway' && code === CLOSE_GATEWAY_RESTARTING) {
      this.gatewayHeldUntil = Math.max(this.gatewayHeldUntil, now + GATEWAY_RESTART_HOLD_MS)
      redirect = true
    } else if (via === 'gateway' && !opened) {
      // Unreachable rather than refusing: down, firewalled, or not there yet.
      const hold = Math.min(GATEWAY_FAILURE_MAX_MS, GATEWAY_FAILURE_BASE_MS * 2 ** this.gatewayFailures)
      this.gatewayFailures += 1
      this.gatewayHeldUntil = Math.max(this.gatewayHeldUntil, now + hold)
      redirect = true
    } else if (via === 'daemon' && code === CLOSE_USE_GATEWAY) {
      this.refusedUntil.delete(sessionId)
      this.assertedUntil.set(sessionId, now + GATEWAY_ASSERTION_MS)
      this.gatewayHeldUntil = 0
      redirect = true
    }
    if (!redirect) return { immediate: false }
    const recent = (this.redirects.get(sessionId) ?? []).filter(at => now - at < REDIRECT_WINDOW_MS)
    recent.push(now)
    this.redirects.set(sessionId, recent)
    return { immediate: recent.length <= MAX_IMMEDIATE_REDIRECTS }
  }

  /** Whether a redirect to the gateway has nowhere to go yet - a 4012 before any report
   *  named the gateway's port - so the caller should ask `/api/health` again. */
  needsReport(sessionId: string): boolean {
    return (this.assertedUntil.get(sessionId) ?? 0) > this.now() && this.lastGatewayPort === null
  }

  private persist(): void {
    if (!this.storage || !this.report) return
    try {
      this.storage.setItem(TRANSPORT_STORAGE_KEY, JSON.stringify({ report: this.report, at: this.reportedAt }))
    } catch { /* storage full or blocked: memory still holds it */ }
  }

  private restore(): void {
    if (!this.storage) return
    try {
      const raw = this.storage.getItem(TRANSPORT_STORAGE_KEY)
      if (!raw) return
      const parsed = JSON.parse(raw) as { report?: unknown; at?: unknown }
      const at = typeof parsed.at === 'number' ? parsed.at : 0
      const report = parseTerminalTransport(parsed.report)
      if (!report || this.now() - at > REPORT_MAX_AGE_MS) return
      this.report = report
      this.reportedAt = at
      if (report.port !== null) this.lastGatewayPort = report.port
    } catch { /* unreadable: start without one */ }
  }
}

const browserStorage = (): StorageLike | null => {
  try { return typeof localStorage === 'undefined' ? null : localStorage } catch { return null }
}

const devServer = (): boolean => {
  try { return Boolean((import.meta as unknown as { env?: { DEV?: boolean } }).env?.DEV) } catch { return false }
}

let shared: TerminalTransportState | null = null

/** The page's one transport state. */
export function terminalTransport(): TerminalTransportState {
  shared ??= new TerminalTransportState({ storage: browserStorage(), proxied: devServer() })
  return shared
}

/** Feed a `/api/health` body to the page's transport state. */
export function noteHealthBody(body: unknown): void {
  if (body && typeof body === 'object') terminalTransport().note((body as Record<string, unknown>).terminal_transport)
}

let refreshing: Promise<void> | null = null

/** Ask `/api/health` once now (deduplicated while in flight) and note what it says. */
export function refreshTerminalTransport(
  fetchImpl: (input: string, init?: RequestInit) => Promise<Response> = (input, init) => fetch(input, init),
): Promise<void> {
  refreshing ??= (async () => {
    try {
      const response = await fetchImpl('/api/health', { cache: 'no-store' })
      if (response.ok) noteHealthBody(await response.json())
    } catch { /* the liveness probe will try again */ } finally {
      refreshing = null
    }
  })()
  return refreshing
}
