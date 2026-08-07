/** Shared recovery policy for everything that can be left behind when the app is
 *  resumed after being dormant: the long-lived `/events` and `/pty` sockets, and the
 *  REST loads that back a note or file tab.
 *
 *  The failure this exists to prevent: a phone wakes the PWA and the network stack is
 *  not usable yet. A WebSocket handshake or a `fetch` started at that instant can hang
 *  indefinitely — no error, no close event, no rejection — so a one-shot "reconnect
 *  when the page becomes visible" is not enough. Nothing fires again, and the pane sits
 *  on "reconnecting…" (or a note on its fetch error) until the tab is switched away and
 *  back, which is the only thing that used to force a remount.
 *
 *  So nothing here trusts a single signal: every attempt carries a deadline, and a poll
 *  re-checks liveness while the page is visible. A missed or ignored signal can delay
 *  recovery by a few seconds; it can never be terminal.
 */

/** A hidden stretch at least this long can silently kill a live connection (the daemon
 *  drops sockets that stop answering its 20s heartbeat), so anything older than the
 *  resume is treated as dead even when the browser still calls it OPEN. */
export const RESUME_SLEEP_MS = 5000
/** A handshake that has not completed by now is stalled, not slow. */
export const HANDSHAKE_TIMEOUT_MS = 8000
/** Deadline for the REST loads that a tab needs in order to show anything. */
export const REQUEST_TIMEOUT_MS = 20000
/** How often liveness is re-checked while the page is visible. */
export const LIVENESS_POLL_MS = 3000
/** Resume signals arrive in bursts (visibilitychange + focus + online together). An
 *  attempt younger than this is left alone so the burst cannot tear down its own work. */
export const ATTEMPT_SETTLE_MS = 600
export const RETRY_BASE_MS = 1000
export const RETRY_MAX_MS = 10000

export type ConnectionPhase = 'open' | 'connecting' | 'closed'
export type LivenessSignal = 'visible' | 'pageshow' | 'online' | 'focus' | 'poll'

/**
 * Whether a terminal socket attempt is useful after the process has ended.
 *
 * The first attach is still required because the daemon retains and replays the PTY buffer,
 * then sends its `already_ended` frame.
 * Later reconnects have nothing new to retrieve and would otherwise poll forever.
 */
export function terminalAttachAllowed(sessionEnded: boolean, reconnecting: boolean): boolean {
  return !sessionEnded || !reconnecting
}

/** Exponential backoff, capped. Attempt 0 is the first retry. */
export function retryDelay(attempt: number, base = RETRY_BASE_MS, cap = RETRY_MAX_MS): number {
  const exponent = Math.min(Math.max(0, Math.trunc(attempt)), 30)
  return Math.min(base * 2 ** exponent, cap)
}

export type ReconnectInput = {
  phase: ConnectionPhase
  /** When the current (or last) connection attempt began; null when none was made. */
  attemptStartedAt: number | null
  /** When the caller's own backoff timer is due to fire; null when none is pending. */
  nextAttemptAt: number | null
  /** Most recent resume from a suspension long enough to have killed a connection
   *  silently. Any attempt that predates it is worthless, whatever its readyState says. */
  staleBefore: number | null
  now: number
}

/** Whether a liveness check should force a fresh connection attempt now. */
export function shouldForceReconnect({ phase, attemptStartedAt, nextAttemptAt, staleBefore, now }: ReconnectInput): boolean {
  if (attemptStartedAt === null) return true
  if (now - attemptStartedAt < ATTEMPT_SETTLE_MS) return false
  if (staleBefore !== null && attemptStartedAt < staleBefore) return true
  if (phase === 'open') return false
  // A handshake still in flight past its deadline is the stall this module exists for.
  if (phase === 'connecting') return now - attemptStartedAt >= HANDSHAKE_TIMEOUT_MS
  // Closed: honour the caller's backoff, but take over the moment it is due — a timer
  // that was dropped or throttled while the page was frozen may never fire on its own.
  return nextAttemptAt === null || now >= nextAttemptAt
}

type ResumeListener = (event: { persisted?: boolean }) => void

export interface DocumentLike {
  hidden: boolean
  addEventListener(type: string, listener: ResumeListener): void
  removeEventListener(type: string, listener: ResumeListener): void
}

export interface WindowLike {
  addEventListener(type: string, listener: ResumeListener): void
  removeEventListener(type: string, listener: ResumeListener): void
  setInterval(handler: () => void, ms: number): number
  clearInterval(handle: number): void
}

export type ResumeEvent = {
  signal: LivenessSignal
  /** See `ReconnectInput.staleBefore`. Monotonic: once set it only moves forward. */
  staleBefore: number | null
  now: number
}

export type ResumeOptions = {
  pollMs?: number
  now?: () => number
  doc?: DocumentLike | null
  win?: WindowLike | null
}

const defaultDocument = (): DocumentLike | null =>
  typeof document === 'undefined' ? null : (document as unknown as DocumentLike)
const defaultWindow = (): WindowLike | null =>
  typeof window === 'undefined' ? null : (window as unknown as WindowLike)

/** Calls `handle` whenever the page resumes (or on the visible-only poll), so a caller
 *  can redo whatever it still owes. Returns a disposer. */
export function watchResume(handle: (event: ResumeEvent) => void, options: ResumeOptions = {}): () => void {
  const now = options.now ?? (() => Date.now())
  const doc = options.doc === undefined ? defaultDocument() : options.doc
  const win = options.win === undefined ? defaultWindow() : options.win
  const pollMs = options.pollMs ?? LIVENESS_POLL_MS
  let hiddenAt: number | null = doc?.hidden ? now() : null
  let staleBefore: number | null = null
  const emit = (signal: LivenessSignal) => handle({ signal, staleBefore, now: now() })
  const markStale = () => { staleBefore = now() }
  const onVisibility = () => {
    if (doc?.hidden) { hiddenAt = now(); return }
    const slept = hiddenAt !== null && now() - hiddenAt >= RESUME_SLEEP_MS
    hiddenAt = null
    if (slept) markStale()
    emit('visible')
  }
  // Restoring from the back/forward cache resumes with sockets the browser froze.
  const onPageShow: ResumeListener = event => { if (event?.persisted) markStale(); emit('pageshow') }
  // A network transition invalidates every socket that was opened over the old route.
  const onOnline = () => { markStale(); emit('online') }
  const onFocus = () => emit('focus')
  const poll = () => { if (!doc?.hidden) emit('poll') }
  doc?.addEventListener('visibilitychange', onVisibility)
  win?.addEventListener('pageshow', onPageShow)
  win?.addEventListener('online', onOnline)
  win?.addEventListener('focus', onFocus)
  const timer = win?.setInterval(poll, pollMs)
  return () => {
    doc?.removeEventListener('visibilitychange', onVisibility)
    win?.removeEventListener('pageshow', onPageShow)
    win?.removeEventListener('online', onOnline)
    win?.removeEventListener('focus', onFocus)
    if (timer !== undefined) win?.clearInterval(timer)
  }
}

export type LivenessOptions = ResumeOptions & {
  phase: () => ConnectionPhase
  attemptStartedAt: () => number | null
  /** Optional: when the caller's own backoff retry is due. */
  nextAttemptAt?: () => number | null
  /** Optional: false while this connection is deliberately gone (an exited session), so a
   *  permanently closed socket is not re-checked on every poll. */
  enabled?: () => boolean
  reconnect: (signal: LivenessSignal) => void
}

/** Keeps one socket honest: forces a fresh attempt whenever the current one is stalled,
 *  closed past its backoff, or older than a resume that should have killed it. */
export function watchLiveness(options: LivenessOptions): () => void {
  return watchResume(event => {
    if (options.enabled && !options.enabled()) return
    const force = shouldForceReconnect({
      phase: options.phase(),
      attemptStartedAt: options.attemptStartedAt(),
      nextAttemptAt: options.nextAttemptAt?.() ?? null,
      staleBefore: event.staleBefore,
      now: event.now,
    })
    if (force) options.reconnect(event.signal)
  }, options)
}
