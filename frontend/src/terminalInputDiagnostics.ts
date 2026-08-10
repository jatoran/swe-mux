export const INPUT_LATENCY_REPORT_MS = 400
export const MAIN_THREAD_STALL_REPORT_MS = 500
export const MAIN_THREAD_TICK_MS = 250
export const INPUT_TRACE_MAX_PENDING = 128
export const INPUT_TRACE_TTL_MS = 30_000

export type TerminalInputSource = 'beforeinput' | 'input' | 'keydown' | 'paste'

export type TerminalInputCapture = {
  eventAt: number
  onDataAt: number
  source: TerminalInputSource
}

export type TerminalInputProbe = {
  seq: number
  source: TerminalInputSource
  bytes: number
  eventAt: number
  onDataAt: number
  sentAt: number
  sentWallMs: number
  bufferedBefore: number
}

export type TerminalInputFrameDiagnostic = {
  input_seq: number
  client_sent_at_ms: number
  client_event_delay_ms: number
  client_queue_delay_ms: number
  input_source: TerminalInputSource
  ws_buffered_bytes: number
}

export type InputAckDiagnostic = {
  inputSeq: number
  source: TerminalInputSource
  eventToSendMs: number
  queueMs: number
  sendToAckMs: number
  serverReceivedAtMs: number | null
  bufferedBefore: number
}

export type InputEchoBatch = {
  probes: TerminalInputProbe[]
  outputAt: number
}

export type InputEchoDiagnostic = {
  firstInputSeq: number
  lastInputSeq: number
  source: TerminalInputSource
  inputs: number
  bytes: number
  eventToSendMs: number
  queueMs: number
  sendToOutputMs: number
  outputToParseMs: number
  parseToFrameMs: number
  totalMs: number
  bufferedBefore: number
}

export type MainThreadStall = {
  startedAt: number
  detectedAt: number
  durationMs: number
}

/**
 * DOM event timestamps are normally on the performance clock, but older WebKit
 * builds used epoch milliseconds. Normalize both without trusting a future stamp.
 */
export function inputEventPerformanceTime(
  eventTimestamp: number,
  now: number,
  wallNow = Date.now(),
): number {
  if (!Number.isFinite(eventTimestamp) || eventTimestamp <= 0) return now
  const normalized = eventTimestamp > 1_000_000_000_000
    ? now - Math.max(0, wallNow - eventTimestamp)
    : eventTimestamp
  return Math.max(0, Math.min(now, normalized))
}

function roundedDuration(value: number): number {
  return Math.max(0, Math.round(value))
}

/**
 * Correlates physical browser input with server acknowledgement and the first
 * output batch that can contain its echo. It retains no input content.
 */
export class TerminalInputLatencyTracker {
  private nextSeq = 1
  private readonly awaitingAck = new Map<number, TerminalInputProbe>()
  private awaitingEcho: TerminalInputProbe[] = []

  begin(
    capture: TerminalInputCapture,
    bytes: number,
    sentAt: number,
    sentWallMs: number,
    bufferedBefore: number,
  ): { probe: TerminalInputProbe; frame: TerminalInputFrameDiagnostic } {
    this.prune(sentAt)
    const probe: TerminalInputProbe = {
      seq: this.nextSeq,
      source: capture.source,
      bytes: Math.max(0, bytes),
      eventAt: capture.eventAt,
      onDataAt: capture.onDataAt,
      sentAt,
      sentWallMs,
      bufferedBefore: Math.max(0, bufferedBefore),
    }
    this.nextSeq = this.nextSeq >= 2_147_483_647 ? 1 : this.nextSeq + 1
    this.awaitingAck.set(probe.seq, probe)
    this.awaitingEcho.push(probe)
    this.bound()
    return {
      probe,
      frame: {
        input_seq: probe.seq,
        client_sent_at_ms: Math.round(probe.sentWallMs),
        client_event_delay_ms: roundedDuration(probe.sentAt - probe.eventAt),
        client_queue_delay_ms: roundedDuration(probe.sentAt - probe.onDataAt),
        input_source: probe.source,
        ws_buffered_bytes: Math.round(probe.bufferedBefore),
      },
    }
  }

  acknowledge(
    inputSeq: number,
    acknowledgedAt: number,
    serverReceivedAtMs: number | null,
    thresholdMs = INPUT_LATENCY_REPORT_MS,
  ): InputAckDiagnostic | null {
    const probe = this.awaitingAck.get(inputSeq)
    if (!probe) return null
    this.awaitingAck.delete(inputSeq)
    const latency = acknowledgedAt - probe.sentAt
    if (latency < thresholdMs) return null
    return {
      inputSeq,
      source: probe.source,
      eventToSendMs: roundedDuration(probe.sentAt - probe.eventAt),
      queueMs: roundedDuration(probe.sentAt - probe.onDataAt),
      sendToAckMs: roundedDuration(latency),
      serverReceivedAtMs,
      bufferedBefore: Math.round(probe.bufferedBefore),
    }
  }

  reject(inputSeq: number): void {
    this.awaitingAck.delete(inputSeq)
    this.awaitingEcho = this.awaitingEcho.filter(probe => probe.seq !== inputSeq)
  }

  takeEchoBatch(outputAt: number): InputEchoBatch | null {
    this.prune(outputAt)
    if (this.awaitingEcho.length === 0) return null
    const probes = this.awaitingEcho
    this.awaitingEcho = []
    return { probes, outputAt }
  }

  completeEchoBatch(
    batch: InputEchoBatch,
    parsedAt: number,
    frameAt: number,
    thresholdMs = INPUT_LATENCY_REPORT_MS,
  ): InputEchoDiagnostic | null {
    const first = batch.probes[0]
    const last = batch.probes.at(-1)
    if (!first || !last) return null
    const total = frameAt - first.eventAt
    if (total < thresholdMs) return null
    return {
      firstInputSeq: first.seq,
      lastInputSeq: last.seq,
      source: first.source,
      inputs: batch.probes.length,
      bytes: batch.probes.reduce((sum, probe) => sum + probe.bytes, 0),
      eventToSendMs: roundedDuration(first.sentAt - first.eventAt),
      queueMs: roundedDuration(first.sentAt - first.onDataAt),
      sendToOutputMs: roundedDuration(batch.outputAt - first.sentAt),
      outputToParseMs: roundedDuration(parsedAt - batch.outputAt),
      parseToFrameMs: roundedDuration(frameAt - parsedAt),
      totalMs: roundedDuration(total),
      bufferedBefore: Math.round(first.bufferedBefore),
    }
  }

  private prune(now: number): void {
    const cutoff = now - INPUT_TRACE_TTL_MS
    for (const [seq, probe] of this.awaitingAck) {
      if (probe.sentAt < cutoff) this.awaitingAck.delete(seq)
    }
    this.awaitingEcho = this.awaitingEcho.filter(probe => probe.sentAt >= cutoff)
  }

  private bound(): void {
    while (this.awaitingAck.size > INPUT_TRACE_MAX_PENDING) {
      const oldest = this.awaitingAck.keys().next().value
      if (typeof oldest !== 'number') break
      this.awaitingAck.delete(oldest)
    }
    if (this.awaitingEcho.length > INPUT_TRACE_MAX_PENDING) {
      this.awaitingEcho.splice(0, this.awaitingEcho.length - INPUT_TRACE_MAX_PENDING)
    }
  }
}

type StallListener = (stall: MainThreadStall) => void

const stallListeners = new Set<StallListener>()
let stallTimer: number | undefined
let lastStallTick = 0

function startStallClock(): void {
  if (stallTimer !== undefined || typeof window === 'undefined') return
  lastStallTick = performance.now()
  stallTimer = window.setInterval(() => {
    const now = performance.now()
    const elapsed = now - lastStallTick
    const startedAt = lastStallTick + MAIN_THREAD_TICK_MS
    lastStallTick = now
    const durationMs = elapsed - MAIN_THREAD_TICK_MS
    if (durationMs < MAIN_THREAD_STALL_REPORT_MS) return
    const stall = { startedAt, detectedAt: now, durationMs: roundedDuration(durationMs) }
    for (const listener of stallListeners) listener(stall)
  }, MAIN_THREAD_TICK_MS)
}

/** One clock serves every mounted pane; listeners decide whether a stall touched input. */
export function watchMainThreadStalls(listener: StallListener): () => void {
  stallListeners.add(listener)
  startStallClock()
  return () => {
    stallListeners.delete(listener)
    if (stallListeners.size !== 0 || stallTimer === undefined) return
    window.clearInterval(stallTimer)
    stallTimer = undefined
  }
}
