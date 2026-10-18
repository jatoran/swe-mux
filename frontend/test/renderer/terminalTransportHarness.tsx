import { render } from 'preact'
import { TerminalPane } from '../../src/TerminalPane'
import { defaultMobileInputSettings } from '../../src/mobileInput'
import { TRANSPORT_STORAGE_KEY, terminalTransport } from '../../src/terminalTransport'
import type { Session } from '../../src/types'
import '../../src/style.css'

/**
 * The real `TerminalPane` over scripted sockets, to prove where it connects and where it
 * goes when a socket closes (`DAEMON_LOAD_ISOLATION.md` item 30).
 *
 * Every socket the pane opens is recorded with which process it was addressed to - the
 * gateway (the `/gateway/` path the dev server proxies, or the gateway's own port) or the
 * daemon (same-origin `/pty/`) - and answered the way that process is told to answer:
 * serve it, close it with a code, or never open at all. A served socket replays one
 * line naming who served it, so a spec can read off the screen which process it reached.
 */

type Kind = 'gateway' | 'daemon'
type Behavior = 'serve' | 'use-daemon' | 'use-gateway' | 'restarting' | 'unreachable' | 'stall'
type Record = { url: string; kind: Kind; opened: boolean; closedWith: number | null }

const GATEWAY_PORT = 8766
const SESSION_ID = 'transport-session'

const behaviors: { gateway: Behavior; daemon: Behavior } = { gateway: 'serve', daemon: 'serve' }
const sockets: Record[] = []
const live = new Set<ScriptedSocket>()

const kindOf = (url: string): Kind => {
  const parsed = new URL(url)
  return parsed.pathname.startsWith('/gateway/') || parsed.port === String(GATEWAY_PORT) ? 'gateway' : 'daemon'
}

const encoder = new TextEncoder()

class ScriptedSocket {
  static CONNECTING = 0
  static OPEN = 1
  static CLOSING = 2
  static CLOSED = 3
  readonly CONNECTING = 0
  readonly OPEN = 1
  readonly CLOSING = 2
  readonly CLOSED = 3
  url: string
  readyState = 0
  binaryType: BinaryType = 'blob'
  bufferedAmount = 0
  protocol = ''
  extensions = ''
  onopen: ((event: Event) => void) | null = null
  onmessage: ((event: MessageEvent) => void) | null = null
  onclose: ((event: CloseEvent) => void) | null = null
  onerror: ((event: Event) => void) | null = null
  readonly record: Record

  constructor(url: string | URL) {
    this.url = String(url)
    const kind = kindOf(this.url)
    this.record = { url: this.url, kind, opened: false, closedWith: null }
    sockets.push(this.record)
    const behavior = behaviors[kind]
    window.setTimeout(() => this.act(behavior), 10)
  }

  private act(behavior: Behavior): void {
    if (this.readyState !== 0) return
    // A black-holed connection (a firewall dropping the SYN): nothing ever happens, and
    // only the pane's own handshake deadline can end it.
    if (behavior === 'stall') return
    if (behavior === 'unreachable') {
      // A refused connection: an error and a close, and never an open.
      this.readyState = 3
      this.record.closedWith = 1006
      this.onerror?.(new Event('error'))
      this.onclose?.(new CloseEvent('close', { code: 1006 }))
      return
    }
    this.readyState = 1
    this.record.opened = true
    live.add(this)
    this.onopen?.(new Event('open'))
    // A process that does not hold the session accepts the upgrade and then says where to go.
    if (behavior === 'use-daemon') window.setTimeout(() => this.serverClose(4010), 5)
    if (behavior === 'use-gateway') window.setTimeout(() => this.serverClose(4012), 5)
    if (behavior === 'restarting') window.setTimeout(() => this.serverClose(4011), 5)
  }

  send(data: string | ArrayBufferLike | Blob | ArrayBufferView): void {
    if (this.readyState !== 1 || typeof data !== 'string') return
    let frame: { type?: string; cols?: number; rows?: number }
    try { frame = JSON.parse(data) } catch { return }
    if (frame.type === 'attach_ready') {
      this.json({ type: 'geometry', cols: Number(frame.cols) || 80, rows: Number(frame.rows) || 24 })
      this.json({ type: 'input_owner', active: true, epoch: 1 })
      this.json({ type: 'replay_start', reason: 'attach' })
      this.bytes(`served by the ${this.record.kind} via ${new URL(this.url).pathname}\r\n`)
      this.json({ type: 'replay_end', position: 0 })
    }
  }

  private json(frame: object): void {
    this.onmessage?.(new MessageEvent('message', { data: JSON.stringify(frame) }))
  }

  private bytes(text: string): void {
    const encoded = encoder.encode(text)
    this.onmessage?.(new MessageEvent('message', {
      data: encoded.buffer.slice(encoded.byteOffset, encoded.byteOffset + encoded.byteLength),
    }))
  }

  /** The server end closing, with the code a real process would send. */
  serverClose(code: number): void {
    if (this.readyState === 3) return
    this.readyState = 3
    this.record.closedWith = code
    live.delete(this)
    this.onclose?.(new CloseEvent('close', { code }))
  }

  close(): void {
    if (this.readyState === 3) return
    this.readyState = 3
    this.record.closedWith = this.record.closedWith ?? 1000
    live.delete(this)
  }

  addEventListener(): void {}
  removeEventListener(): void {}
  dispatchEvent(): boolean { return true }
}

window.WebSocket = ScriptedSocket as unknown as typeof WebSocket

// Every request answers an empty object; `/api/health` answers whatever transport the
// spec says the daemon would report, which is how a 4012 learns where the gateway is.
let healthTransport: object | null = null
window.fetch = async (input: RequestInfo | URL) => {
  const url = String(input instanceof Request ? input.url : input)
  const body = url.includes('/api/health') ? { ok: true, terminal_transport: healthTransport } : {}
  return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } })
}

const params = new URLSearchParams(location.search)
localStorage.removeItem(TRANSPORT_STORAGE_KEY)
const reported = params.get('report')
if (reported === 'gateway') terminalTransport().note({ transport: 'gateway', state: 'running', port: GATEWAY_PORT })
behaviors.gateway = (params.get('gateway') as Behavior | null) ?? 'serve'
behaviors.daemon = (params.get('daemon') as Behavior | null) ?? 'serve'

const now = Date.now() / 1000
const session: Session = {
  id: SESSION_ID, name: 'transport', project_id: 'p', backend: 'shell',
  native_session_id: 'native', cwd: '/tmp', exe: 'bash', args: [], pid: 4242,
  created_at: now - 60, state: 'idle', tokens_in: 0, process_job_assignment: 'assigned',
  tokens_out: 0, tokens_cache_read: 0, tokens_cache_write: 0, cost_usd: 0,
  context_window: 0, context_pct: 0, last_activity_ts: now,
  git: { dirty: 0, ahead: 0, behind: 0 }, pinned_attention: false, broadcast: false,
  context_peak_pct: 0, compaction_count: 0, runtime_cwd_live: false, runtime_cwd_source: 'harness',
  runtime_cwd_dropped: 0,
}

Object.assign(globalThis, {
  __sockets: () => sockets.map(item => ({ ...item })),
  __setBehavior: (kind: Kind, behavior: Behavior) => { behaviors[kind] = behavior },
  __setHealthTransport: (value: object | null) => { healthTransport = value },
  __closeLive: (code: number) => { for (const socket of [...live]) socket.serverClose(code) },
  __screen: () => document.querySelector('.xterm-rows')?.textContent ?? '',
})

render(
  <TerminalPane
    session={session}
    onState={() => {}}
    broadcast={false}
    scrollback={1000}
    rendererPreference="dom"
    mobileInput={defaultMobileInputSettings}
    uiScale={1}
    visible
    claudeMaxColumns={0}
  />,
  document.getElementById('root')!,
)
