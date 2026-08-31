/**
 * The demo's WebSockets. `installFakeWebSocket()` replaces the constructor so
 * the app's two socket call sites - the `/events` bus and `/pty/{id}` terminal
 * streams - connect to an in-page simulation instead of a daemon.
 *
 * The PTY half speaks just enough of the real attach protocol that
 * `TerminalPane` is satisfied: it answers `attach_ready` with an ownership
 * grant and a `replay_start` → bytes → `replay_end` replay of the stored
 * scrollback, echoes typed input, and streams a canned reply on Enter. The
 * events half emits a watermark on open and a generic changed frame whenever
 * the demo store mutates, which is what drives the app's fleet refetch.
 */
import { liveScanRecord } from './conversation.ts'
import { BUSY_SESSION_IDS } from './fixtures.ts'
import { apply, nowSeconds, onMutation, session, state } from './store.ts'
import {
  buildReply, busyReply, clearComposer, composerBlock, composerInfo,
  consumeInput, demoBackendKind, promptFor, redrawComposer,
  type LineState, type ReplyTool,
} from './terminalSim.ts'

const encoder = new TextEncoder()

type SocketListener = (event: MessageEvent | Event | CloseEvent) => void

class FakeSocketBase {
  static CONNECTING = 0 as const
  static OPEN = 1 as const
  static CLOSING = 2 as const
  static CLOSED = 3 as const
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
  onopen: SocketListener | null = null
  onmessage: SocketListener | null = null
  onclose: SocketListener | null = null
  onerror: SocketListener | null = null

  private timers = new Set<number>()

  constructor(url: string) {
    this.url = url
    this.later(() => {
      if (this.readyState !== 0) return
      this.readyState = 1
      this.opened()
      this.onopen?.(new Event('open'))
    }, 15)
  }

  /** setTimeout that dies with the socket, so a closed pane never hears from it. */
  protected later(callback: () => void, delay: number): void {
    const timer = window.setTimeout(() => {
      this.timers.delete(timer)
      if (this.readyState === 1 || (this.readyState === 0 && delay <= 20)) callback()
    }, delay)
    this.timers.add(timer)
  }

  protected opened(): void {}
  protected disposed(): void {}

  protected deliver(payload: unknown): void {
    if (this.readyState !== 1) return
    this.onmessage?.(new MessageEvent('message', { data: payload }))
  }

  protected deliverJson(frame: Record<string, unknown>): void {
    this.deliver(JSON.stringify(frame))
  }

  protected deliverBytes(text: string): void {
    const bytes = encoder.encode(text)
    this.deliver(bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength))
  }

  send(_data: string | ArrayBufferLike | Blob | ArrayBufferView): void {}

  close(): void {
    if (this.readyState === 3) return
    this.readyState = 3
    for (const timer of this.timers) window.clearTimeout(timer)
    this.timers.clear()
    this.disposed()
    this.onclose?.(new CloseEvent('close', { code: 1000 }))
  }

  addEventListener(): void {}
  removeEventListener(): void {}
  dispatchEvent(): boolean { return true }
}

// ------------------------------------------------------------------- events

const eventSockets = new Set<FakeEventsSocket>()

class FakeEventsSocket extends FakeSocketBase {
  protected override opened(): void {
    eventSockets.add(this)
    this.deliverJson({ type: 'events_ready', sequence: state.seq })
  }

  protected override disposed(): void {
    eventSockets.delete(this)
  }

  notifyChanged(): void {
    this.deliverJson({ type: 'demo_state_changed', seq: state.seq })
  }

  /** A typed daemon frame, for the surfaces that listen for one by name rather
   *  than refetching on any change - the transcript reader above all. */
  notifyEvent(type: string, sessionId: string): void {
    this.deliverJson({ type, session_id: sessionId, seq: state.seq })
  }
}

/** Push one typed frame into every attached events socket in this frame. */
function broadcastEvent(type: string, sessionId: string): void {
  for (const socket of eventSockets) socket.notifyEvent(type, sessionId)
}

// ---------------------------------------------------------------------- pty

const ptySockets = new Map<string, Set<FakePtySocket>>()

class FakePtySocket extends FakeSocketBase {
  readonly sessionId: string
  private epoch = 0
  private replayed = false

  constructor(url: string, sessionId: string) {
    super(url)
    this.sessionId = sessionId
  }

  protected override opened(): void {
    let attached = ptySockets.get(this.sessionId)
    if (!attached) ptySockets.set(this.sessionId, attached = new Set())
    attached.add(this)
  }

  protected override disposed(): void {
    ptySockets.get(this.sessionId)?.delete(this)
  }

  override send(data: string | ArrayBufferLike | Blob | ArrayBufferView): void {
    if (this.readyState !== 1 || typeof data !== 'string') return
    let frame: Record<string, unknown>
    try { frame = JSON.parse(data) as Record<string, unknown> } catch { return }
    const type = String(frame.type ?? '')
    if (type === 'attach_ready') {
      const cols = Number(frame.cols) || 80
      const rows = Number(frame.rows) || 24
      this.deliverJson({ type: 'geometry', cols, rows })
      this.epoch += 1
      this.deliverJson({ type: 'input_owner', active: true, epoch: this.epoch })
      this.startReplay()
      return
    }
    if (type === 'claim_input') {
      this.epoch += 1
      this.deliverJson({ type: 'input_owner', active: true, epoch: this.epoch })
      return
    }
    if (type === 'resize') {
      const cols = Number(frame.cols)
      const rows = Number(frame.rows)
      if (Number.isFinite(cols) && Number.isFinite(rows)) this.deliverJson({ type: 'geometry', cols, rows })
      return
    }
    if (type === 'input') {
      if (frame.kind === 'terminal_response') return
      const target = session(this.sessionId)
      if (!target || ['exited', 'crashed'].includes(target.state)) return
      apply({ kind: 'term-input', id: this.sessionId, data: String(frame.data ?? '') })
      return
    }
    // output_ack, terminal_state, client_diagnostic, repaint, presence - the
    // demo has no use for these, and ignoring them is safe.
  }

  private startReplay(): void {
    this.replayed = false
    this.deliverJson({ type: 'replay_start', reason: 'attach' })
    const scrollback = state.terminals[this.sessionId] ?? ''
    if (scrollback) this.deliverBytes(scrollback)
    this.deliverJson({ type: 'replay_end', position: encoder.encode(scrollback).byteLength })
    this.replayed = true
    const snapshot = session(this.sessionId)
    if (snapshot) this.sendState(snapshot)
  }

  writeLive(text: string): void {
    if (this.replayed) this.deliverBytes(text)
  }

  sendState(snapshot: object): void {
    this.deliverJson({
      type: 'update', revision: state.seq,
      snapshot: { ...snapshot, _snapshot_generation: 'demo', _snapshot_revision: state.seq },
    })
  }

  sendExit(snapshot: object | undefined): void {
    this.deliverJson({
      type: 'exit', revision: state.seq,
      snapshot: snapshot ? { ...snapshot, _snapshot_generation: 'demo', _snapshot_revision: state.seq } : undefined,
    })
  }
}

// -------------------------------------------------------- responder plumbing

const lineStates = new Map<string, LineState>()
const busy = new Set<string>()

const lineStateFor = (id: string): LineState => {
  let found = lineStates.get(id)
  if (!found) lineStates.set(id, found = { buffer: '' })
  return found
}

function appendOutput(id: string, text: string): void {
  apply({ kind: 'term-append', id, data: text })
}

/** Record what the visitor said, so the Transcript tab shows it immediately rather
 *  than only once a reply has finished streaming. */
function recordPrompt(id: string, text: string): void {
  const stamp = new Date().toISOString()
  apply({
    kind: 'transcript-append',
    id,
    message: {
      message_id: `${id}-live-u-${state.seq}`,
      ordinal: 0,
      role: 'user',
      ts: stamp,
      text,
      preceding_tool_calls: 0,
    },
  })
}

/** Record the reply and the behavioural record the observer would have written. */
function recordReply(id: string, ask: string, text: string, tools: ReplyTool[]): void {
  apply({
    kind: 'transcript-append',
    id,
    message: {
      message_id: `${id}-live-a-${state.seq}`,
      ordinal: 0,
      role: 'assistant',
      ts: new Date().toISOString(),
      text,
      preceding_tool_calls: tools.length,
      ...(tools.length ? { preceding_tools: tools } : {}),
    },
  })
  const target = session(id)
  if (target && target.backend !== 'shell') {
    apply({
      kind: 'timeline-append',
      id,
      record: liveScanRecord(target, ask, text, nowSeconds(), (state.timelines[id] ?? []).length),
    })
  }
}

function streamReply(id: string, submitted: string): void {
  const target = session(id)
  if (!target) return
  const kind = demoBackendKind(target.backend)
  const agent = kind !== 'shell'
  const info = composerInfo(target)
  // An agent pane owns a composer, so a submit is: erase the box, print the
  // prompt line the user just sent as transcript, then let the reply follow.
  // A shell has no box and simply echoes at its prompt, exactly as the real one
  // does - the contrast between the two is worth keeping.
  const openTurn = (): void => {
    if (agent) appendOutput(id, `${clearComposer()}${promptFor(kind)}${submitted}\r\n`)
  }
  const restoreComposer = (working = false): void => {
    const latest = session(id)
    appendOutput(id, composerBlock(
      latest ? { ...composerInfo(latest), working } : { ...info, working }, '',
    ))
  }

  if (submitted.trim() === '') {
    if (agent) appendOutput(id, redrawComposer(info, ''))
    else appendOutput(id, promptFor(kind))
    return
  }
  if (busy.has(id)) {
    if (agent) appendOutput(id, redrawComposer(info, ''))
    else appendOutput(id, `\x1b[38;5;243m(one thing at a time - still typing the last answer)\x1b[0m\r\n${promptFor(kind)}`)
    return
  }
  // A permanently-working pane answers rather than running the joke responder,
  // and never leaves `working`: it is the demo's stand-in for a turn in flight,
  // which is exactly the state the real product will not interleave input into.
  if (BUSY_SESSION_IDS.includes(id)) {
    openTurn()
    const refusal = busyReply(kind)
    // Still a turn, so the conversation records it: the refusal is what the pane
    // actually said, and a reader that showed nothing here would be wrong about a
    // real exchange rather than merely quiet.
    recordPrompt(id, submitted)
    window.setTimeout(
      () => recordReply(id, submitted, refusal.plain, []),
      260 + refusal.chunks.length * refusal.pace,
    )
    refusal.chunks.forEach((chunk, index) => {
      window.setTimeout(() => appendOutput(id, chunk), 260 + index * refusal.pace)
    })
    window.setTimeout(() => restoreComposer(true), 260 + refusal.chunks.length * refusal.pace)
    return
  }
  busy.add(id)
  openTurn()
  const reply = buildReply(kind, submitted)
  const startDelay = agent ? 900 : 120
  if (agent) recordPrompt(id, submitted)
  if (agent) {
    const now = Math.floor(Date.now() / 1000)
    apply({
      kind: 'session-patch', id,
      patch: { state: 'working', state_since: now, turn_started_at: now },
    })
    // The box stays under the reply while it streams, saying what the pane is
    // doing - which is what the real CLI shows for the whole of a turn.
    appendOutput(id, composerBlock({ ...info, working: true }, ''))
  }
  reply.chunks.forEach((chunk, index) => {
    window.setTimeout(() => {
      if (!session(id)) { busy.delete(id); return }
      if (agent && index === 0) appendOutput(id, clearComposer())
      appendOutput(id, chunk)
      if (index === reply.chunks.length - 1) {
        busy.delete(id)
        if (agent) {
          const finished = session(id)
          apply({
            kind: 'session-patch', id,
            patch: {
              state: 'idle', state_since: Math.floor(Date.now() / 1000),
              turn_started_at: null,
              turn_seq: (finished?.turn_seq ?? 0) + 1,
              last_turn_end_ts: Math.floor(Date.now() / 1000),
              last_activity_ts: Math.floor(Date.now() / 1000),
              tokens_in: (finished?.tokens_in ?? 0) + 800 + Math.floor(Math.random() * 600),
              cost_usd: Math.round(((finished?.cost_usd ?? 0) + 0.03) * 100) / 100,
            },
          })
          restoreComposer(false)
          recordReply(id, submitted, reply.plain, reply.tools)
        }
      }
    }, startDelay + index * reply.pace)
  })
}

// One subscription per frame: routes bytes into attached panes, runs the
// responder for input this frame originated, and mirrors lifecycle changes.
onMutation((mutation, local) => {
  if (mutation.kind === 'term-append') {
    const attached = ptySockets.get(mutation.id)
    if (attached) for (const socket of attached) socket.writeLive(mutation.data)
    return
  }
  if (mutation.kind === 'term-input') {
    const editor = lineStateFor(mutation.id)
    const result = consumeInput(editor, mutation.data)
    if (local) {
      // An agent's box is repainted with the whole buffer rather than echoing
      // the keystroke, which is how a real TUI keeps text inside its border.
      const typing = session(mutation.id)
      const inBox = typing && demoBackendKind(typing.backend) !== 'shell'
      if (result.submitted === null && inBox && result.echo) {
        appendOutput(mutation.id, redrawComposer(composerInfo(typing), editor.buffer))
      } else if (result.echo && !inBox) {
        appendOutput(mutation.id, result.echo)
      }
      if (result.submitted !== null) streamReply(mutation.id, result.submitted)
    }
    return
  }
  // Named frames rather than the generic changed frame, and raised here rather than
  // by the responder so the *other* frame (the phone beside the desktop) hears them
  // too: the transcript reader refreshes on `transcript_message` by name, and a
  // mirrored mutation that only produced `demo_state_changed` would leave the second
  // frame's reader a turn behind the pane it is sitting next to.
  if (mutation.kind === 'transcript-append') {
    broadcastEvent('transcript_message', mutation.id)
    if (mutation.message.role === 'assistant') broadcastEvent('turn_ended', mutation.id)
  }
  if (mutation.kind === 'session-patch') {
    const snapshot = session(mutation.id)
    if (snapshot && 'state' in mutation.patch) {
      const attached = ptySockets.get(mutation.id)
      if (attached) for (const socket of attached) socket.sendState(snapshot)
    }
  }
  if (mutation.kind === 'session-remove') {
    const attached = ptySockets.get(mutation.id)
    if (attached) {
      for (const socket of attached) socket.sendExit(undefined)
      ptySockets.delete(mutation.id)
    }
  }
  // Everything except pure terminal traffic (which returned above) nudges the
  // event bus - that is what makes the *other* frame (the phone beside the
  // desktop) refetch and follow along.
  for (const socket of eventSockets) socket.notifyChanged()
})

// ------------------------------------------------------------------ install

export function installFakeWebSocket(): void {
  const FakeWebSocket = function (this: unknown, url: string | URL) {
    const parsed = new URL(String(url), location.href)
    const ptyMatch = parsed.pathname.match(/^\/pty\/(.+)$/)
    if (ptyMatch) return new FakePtySocket(parsed.toString(), decodeURIComponent(ptyMatch[1]))
    return new FakeEventsSocket(parsed.toString())
  } as unknown as typeof WebSocket
  ;(FakeWebSocket as unknown as Record<string, number>).CONNECTING = 0
  ;(FakeWebSocket as unknown as Record<string, number>).OPEN = 1
  ;(FakeWebSocket as unknown as Record<string, number>).CLOSING = 2
  ;(FakeWebSocket as unknown as Record<string, number>).CLOSED = 3
  window.WebSocket = FakeWebSocket
}
