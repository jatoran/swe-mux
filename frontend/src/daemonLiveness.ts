/**
 * Is the daemon answering right now?
 *
 * The failure this exists to show: the daemon's event loop freezes for tens of
 * seconds under host load. Every HTTP request and every WebSocket frame hangs
 * for the duration, so terminals stop moving and clicks do nothing, and from
 * the browser that is indistinguishable from the app having crashed. It has
 * not: the daemon recovers on its own, and the agent sessions were never in
 * danger because a separate supervisor process owns the PTYs. What the operator
 * needs in that window is one sentence saying so, and how long it has been.
 *
 * This is a *symptom display*, not a health check. It probes `/api/health` with
 * a short deadline and reports when the daemon has stopped answering in time;
 * it draws no conclusion about why, restarts nothing, and clears itself the
 * moment an answer arrives. The redeploy wait loop (`redeployProgress.ts`) and
 * the post-restart reload in `App.tsx` watch the same endpoint for their own
 * reasons and are left alone; when one of those has deliberately taken the
 * daemon down the caller disables this so two surfaces do not describe one
 * outage.
 *
 * Two rules the numbers below encode. **A single slow probe never shows the
 * banner** - the redeploy loop learned the same lesson (`REDEPLOY_DOWN_PROBES`):
 * a phone waking, a GC pause, or one queued request behind a slow endpoint is
 * not a stall, so it takes two consecutive misses, roughly six seconds of
 * silence. **The first success clears it**, because the claim is "not answering
 * now" and one answer is the whole refutation.
 *
 * The state machine is pure and DOM-free so the thresholds are tested rather
 * than trusted; `watchDaemonLiveness` is the scheduler around it with every
 * side effect injectable, and `useDaemonLiveness` is the one-line hook.
 */

// Extension-qualified so this module resolves under `node --experimental-strip-types`,
// which is what runs the unit suite (see `updateCheck.ts`).
import { useEffect, useState } from 'preact/hooks'
import type { DocumentLike } from './liveness.ts'

/** How long the scheduler waits after one probe settles before sending the next. */
export const DAEMON_PROBE_INTERVAL_MS = 4000
/** Deadline on a single probe. An answer later than this is a miss: the daemon
 *  may well be alive, but it is not responding, which is the fact on display. */
export const DAEMON_PROBE_TIMEOUT_MS = 2500
/** Consecutive misses before the banner. One is a blip; two is a stall. */
export const DAEMON_STALL_MISSES = 2

export type DaemonProbeOutcome = 'ok' | 'missed'

export type DaemonLivenessState = {
  /** Consecutive missed probes. Reset to zero by any success. */
  misses: number
  /** When the first probe of the current run of misses was sent (ms epoch), or
   *  null while the daemon is answering. Stamped at the send rather than at the
   *  timeout so the elapsed figure counts the silence, not the waiting. */
  missedSince: number | null
  /** True once `misses` has reached the threshold; false again on the first success. */
  stalled: boolean
  /** True while nothing is being measured (hidden tab, or disabled by the caller). */
  paused: boolean
}

export const INITIAL_DAEMON_LIVENESS: DaemonLivenessState = {
  misses: 0, missedSince: null, stalled: false, paused: false,
}

/** When the stall on display began, or null when nothing is on display. */
export function stalledSince(state: DaemonLivenessState): number | null {
  return state.stalled ? state.missedSince : null
}

/**
 * One probe's outcome folded into the state. `sentAt` is when the probe left,
 * which is the honest start of the silence it measured.
 */
export function applyDaemonProbe(
  state: DaemonLivenessState, outcome: DaemonProbeOutcome, sentAt: number,
): DaemonLivenessState {
  if (state.paused) return state
  if (outcome === 'ok') {
    if (state.misses === 0 && !state.stalled && state.missedSince === null) return state
    return { ...state, misses: 0, missedSince: null, stalled: false }
  }
  const misses = state.misses + 1
  const missedSince = state.missedSince ?? sentAt
  return { ...state, misses, missedSince, stalled: state.stalled || misses >= DAEMON_STALL_MISSES }
}

/**
 * Stop measuring. Everything observed so far is dropped rather than frozen: a
 * hidden tab sends no probes, so a banner restored on return would be claiming
 * a silence nobody measured, and a stall that ended while the phone was locked
 * would come back reading "not responding (900s)" for a round trip. The first
 * probe after resuming starts the count again.
 */
export function pauseDaemonProbes(state: DaemonLivenessState): DaemonLivenessState {
  if (state.paused && state.misses === 0 && !state.stalled) return state
  return { ...INITIAL_DAEMON_LIVENESS, paused: true }
}

export function resumeDaemonProbes(state: DaemonLivenessState): DaemonLivenessState {
  if (!state.paused) return state
  return { ...state, paused: false }
}

/** Whole seconds the daemon has been silent, floored at zero. */
export function stallSeconds(since: number, now: number): number {
  return Math.max(0, Math.floor((now - since) / 1000))
}

/** What is happening. Rendered on its own so the ticking clock beside it can be
 *  kept out of what a screen reader re-announces every second. */
export const DAEMON_STALL_HEADING = 'swe-mux daemon is not responding'
/** The two things the operator most needs to hear: their sessions are fine, and
 *  there is nothing to press. Deliberately no "try reloading" - a reload during a
 *  stall hangs on the first request and turns a paused UI into a blank one. */
export const DAEMON_STALL_PROMISE = 'Sessions keep running; the UI will catch up on its own.'

/** The whole sentence, as the banner's tooltip and as one string to test. */
export function daemonStallText(since: number, now: number): string {
  return `${DAEMON_STALL_HEADING} (${stallSeconds(since, now)}s). ${DAEMON_STALL_PROMISE}`
}

type FetchLike = (input: string, init?: RequestInit) => Promise<{ ok: boolean }>

/**
 * One probe of `/api/health`. Same shape as the redeploy wait loop's `ask`:
 * `cache: 'no-store'` so no cache can answer for the daemon, an `AbortController`
 * deadline so a hung request settles as a miss rather than never, and any
 * rejection - abort, network error - counted the same as a non-2xx.
 */
export async function probeDaemon(
  fetchImpl: FetchLike = (input, init) => fetch(input, init),
  timeoutMs = DAEMON_PROBE_TIMEOUT_MS,
): Promise<DaemonProbeOutcome> {
  const controller = new AbortController()
  const deadline = setTimeout(() => controller.abort(), timeoutMs)
  try {
    const response = await fetchImpl('/api/health', { cache: 'no-store', signal: controller.signal })
    return response.ok ? 'ok' : 'missed'
  } catch {
    return 'missed'
  } finally {
    clearTimeout(deadline)
  }
}

export interface TimerHost {
  setTimeout(handler: () => void, ms: number): number
  clearTimeout(handle: number): void
}

export type DaemonLivenessOptions = {
  /** Reported on every change; never called with an identical state twice in a row. */
  onChange: (state: DaemonLivenessState) => void
  probe?: () => Promise<DaemonProbeOutcome>
  intervalMs?: number
  now?: () => number
  doc?: DocumentLike | null
  timers?: TimerHost
  /** False while a deliberate outage is in flight (a redeploy's daemon-down
   *  stage, a session-preserving restart); nothing is measured and nothing is
   *  shown, because that outage already has its own surface. */
  enabled?: boolean
}

const defaultDocument = (): DocumentLike | null =>
  typeof document === 'undefined' ? null : (document as unknown as DocumentLike)
const defaultTimers = (): TimerHost => ({
  setTimeout: (handler, ms) => setTimeout(handler, ms) as unknown as number,
  clearTimeout: handle => clearTimeout(handle as unknown as ReturnType<typeof setTimeout>),
})

/**
 * Probe on a cadence while the page is visible, fold each outcome into the state,
 * and report changes. Returns a disposer.
 *
 * Self-rescheduling rather than an interval, for the reason the redeploy loop
 * gives: a probe that takes its full deadline must not have the next one start
 * on top of it, or a burst of piled-up failures would spend the whole two-miss
 * budget on one stall. The gap is measured from when the last probe settled.
 *
 * Hidden tabs measure nothing (mobile battery, and see `pauseDaemonProbes` for
 * why the state is dropped rather than kept); becoming visible probes at once,
 * so a phone unlocked during a stall learns about it in one round trip plus one
 * interval rather than two.
 */
export function watchDaemonLiveness(options: DaemonLivenessOptions): () => void {
  const now = options.now ?? (() => Date.now())
  const doc = options.doc === undefined ? defaultDocument() : options.doc
  const timers = options.timers ?? defaultTimers()
  const probe = options.probe ?? (() => probeDaemon())
  const intervalMs = options.intervalMs ?? DAEMON_PROBE_INTERVAL_MS
  const enabled = options.enabled ?? true
  let state: DaemonLivenessState = INITIAL_DAEMON_LIVENESS
  let disposed = false
  let timer: number | undefined
  // A probe result that lands after the tab was hidden belongs to a measurement
  // that was abandoned; the generation makes it fall on the floor.
  let generation = 0
  let inFlight = false

  const publish = (next: DaemonLivenessState) => {
    if (next === state) return
    state = next
    options.onChange(state)
  }
  const measuring = () => enabled && !disposed && !(doc?.hidden ?? false)
  const cancelTimer = () => {
    if (timer !== undefined) { timers.clearTimeout(timer); timer = undefined }
  }
  const schedule = () => {
    cancelTimer()
    if (!measuring()) return
    timer = timers.setTimeout(() => { timer = undefined; void tick() }, intervalMs)
  }
  const tick = async () => {
    if (!measuring() || inFlight) return
    // A resume arriving while a scheduled probe is pending sends this one now
    // and drops the pending one, rather than sending both.
    cancelTimer()
    inFlight = true
    const myGeneration = generation
    const sentAt = now()
    let outcome: DaemonProbeOutcome
    try { outcome = await probe() } catch { outcome = 'missed' }
    inFlight = false
    if (disposed) return
    // Abandoned by a hide/show cycle while it was out: its answer is about a
    // measurement that was dropped, and the resume that dropped it found this
    // probe still in flight and could not start its own. Start that one now.
    if (myGeneration !== generation) { void tick(); return }
    if (!measuring()) return
    publish(applyDaemonProbe(state, outcome, sentAt))
    schedule()
  }
  const pause = () => {
    generation += 1
    cancelTimer()
    publish(pauseDaemonProbes(state))
  }
  const resume = () => {
    // Visibility cannot turn a disabled watcher on; the caller disabled it.
    if (!enabled || disposed) return
    publish(resumeDaemonProbes(state))
    void tick()
  }
  const onVisibility = () => {
    if (doc?.hidden) pause()
    else resume()
  }

  doc?.addEventListener('visibilitychange', onVisibility)
  if (!enabled || doc?.hidden) publish(pauseDaemonProbes(state))
  else void tick()

  return () => {
    disposed = true
    generation += 1
    cancelTimer()
    doc?.removeEventListener('visibilitychange', onVisibility)
  }
}

export type DaemonLiveness = {
  stalled: boolean
  /** ms epoch when the stall on display began; null when none is. */
  stalledSince: number | null
}

/** The hook. Re-subscribes when `enabled` flips, which is what drops a
 *  measurement taken during a deliberate outage. */
export function useDaemonLiveness(enabled = true): DaemonLiveness {
  const [state, setState] = useState<DaemonLivenessState>(INITIAL_DAEMON_LIVENESS)
  useEffect(() => {
    setState(INITIAL_DAEMON_LIVENESS)
    return watchDaemonLiveness({ enabled, onChange: setState })
  }, [enabled])
  return { stalled: state.stalled, stalledSince: stalledSince(state) }
}
