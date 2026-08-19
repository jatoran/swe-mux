/** What the UI does while the frozen desktop app rebuilds itself.
 *
 *  A redeploy has two stages that feel nothing alike, and treating them as one
 *  is what made it read as an outage from the moment you clicked the button:
 *
 *  - **building** — several minutes of PyInstaller work that happens in
 *    `dist/.staging` while the current daemon keeps serving. Everything works:
 *    terminals, the fleet, settings. Blocking here (which the first version of
 *    this did, on the tab that started it) locks you out of a working app, and
 *    if the build then fails you were locked out for nothing.
 *  - **down** — the tail: the daemon is stopped, the bundle is swapped, and a
 *    cold PyInstaller start can take minutes before the successor answers. Here
 *    nothing works, and keystrokes typed into a terminal are *silently lost*,
 *    because the PTY sockets are proxied by the daemon rather than held open to
 *    the supervisor. Being told you cannot type beats losing input, so this
 *    stage does block.
 *
 *  The transition between them cannot be timed or guessed, so it is taken from
 *  two independent signals: the daemon broadcasts `daemon_redeploy_stopping`
 *  from its own shutdown handler (it is still alive when the script asks it to
 *  stop, so this is authoritative), and failing that, repeated health probes.
 *  A single failed probe is deliberately not enough — a phone waking or a blip
 *  would otherwise slam a full-screen overlay over a working app.
 *
 *  None of this needs the daemon in order to *render*. What needs the daemon is
 *  learning that a redeploy started at all, which is why it is broadcast during
 *  the build, minutes before it can affect anyone, and mirrored into
 *  sessionStorage so a reload or a second tab does not come up blind.
 */

export type RedeployPhase = 'idle' | 'building' | 'down'

/** How a finished redeploy is reported once the successor daemon is up. */
export type RedeployOutcome = {
  outcome?: string
  detail?: string
  exit_code?: number
  started_at?: number
  finished_at?: number
  log_tail?: string[]
}

export type RedeployStatus = {
  running?: boolean
  phase?: string
  pid?: number | null
  log_tail?: string[]
  last_result?: RedeployOutcome | null
  available?: boolean
}

/** Persisted so a reload, a second tab, or a client that was not the initiator
 *  comes up already knowing. Session-scoped on purpose: it must not outlive the
 *  browser session and strand a tab in a maintenance mode nothing will clear. */
export const REDEPLOY_STORAGE_KEY = 'mux.redeploy'
/** Set immediately before the deliberate post-redeploy reload and consumed once
 *  on the next boot. Without it, the "your change did not ship" notice would
 *  either be lost by the navigation or re-shown on every unrelated reload for as
 *  long as the result stayed fresh. */
export const REDEPLOY_RESULT_KEY = 'mux.redeploy.result-pending'
/** A redeploy that has not resolved by now is not one this UI should still be
 *  waiting on. Generous: the build alone can run for minutes and the successor's
 *  first cold start is budgeted at five more on the daemon side. */
export const REDEPLOY_MAX_MS = 15 * 60_000
/** Consecutive failed health probes before an unannounced daemon loss is treated
 *  as the outage rather than as a blip. */
export const REDEPLOY_DOWN_PROBES = 2
/** How long the wait loop waits between probes. */
export const REDEPLOY_POLL_MS = 2000
/** Deadline on a single probe. A `fetch` issued while a phone is waking can hang
 *  indefinitely rather than fail (see liveness.ts), and an outage is exactly when
 *  that happens — without a deadline the loop would stall silently instead of
 *  concluding the daemon is away. */
export const REDEPLOY_PROBE_TIMEOUT_MS = 5000

export type RedeployState = {
  phase: RedeployPhase
  /** When this client first learned about the redeploy (ms epoch). Drives the
   *  elapsed timer, which is the only honest progress signal once the daemon is
   *  gone and there is nothing left to ask. */
  startedAt: number
  /** Deadline after which the UI stops waiting and hands the user the log. */
  expiresAt: number
  /** Whether the daemon has been observed unreachable at least once. The reload
   *  at the end is gated on this: a healthy daemon that was never seen to go
   *  away is the build-failed case, not the successor. */
  sawDown: boolean
  /** Consecutive failed probes, for the two-strike rule above. */
  downProbes: number
  /** Most recent build-log lines, shown when the chip is expanded. */
  logTail: string[]
}

export const IDLE_REDEPLOY: RedeployState = {
  phase: 'idle', startedAt: 0, expiresAt: 0, sawDown: false, downProbes: 0, logTail: [],
}

export function beginRedeploy(now: number): RedeployState {
  return { ...IDLE_REDEPLOY, phase: 'building', startedAt: now, expiresAt: now + REDEPLOY_MAX_MS }
}

/** Enter the blocking stage. Keeps `startedAt` so the elapsed timer stays
 *  continuous across the transition. */
export function enterOutage(state: RedeployState): RedeployState {
  if (state.phase !== 'building') return state
  return { ...state, phase: 'down', sawDown: true, downProbes: REDEPLOY_DOWN_PROBES }
}

export type ProbeResult =
  | { healthy: true; status: RedeployStatus | null }
  | { healthy: false }

export type ProbeVerdict =
  /** Nothing to decide yet; carry `state` forward. */
  | { action: 'wait'; state: RedeployState }
  /** The successor (or the rolled-back previous build) answered. Reload. */
  | { action: 'reload' }
  /** It ended without the daemon ever going away, so nothing was swapped. */
  | { action: 'finished'; state: RedeployState }
  /** The deadline passed with no resolution. */
  | { action: 'timeout'; state: RedeployState }

/**
 * One step of the wait loop, as a pure function of the probe and the clock.
 *
 * The ordering matters and is the part worth testing: `sawDown` is what
 * separates "the successor is up, reload into it" from "the build failed and
 * this is the same daemon that has been serving all along". Reloading in the
 * second case would be wrong twice over — there is no new UI to load, and the
 * failure message would be thrown away by the navigation.
 */
export function applyProbe(state: RedeployState, probe: ProbeResult, now: number): ProbeVerdict {
  if (state.phase === 'idle') return { action: 'wait', state }
  if (!probe.healthy) {
    const downProbes = state.downProbes + 1
    // Two strikes before the overlay, unless the daemon already told us it was
    // stopping (which sets `phase` to 'down' directly and skips this entirely).
    const phase = downProbes >= REDEPLOY_DOWN_PROBES ? 'down' : state.phase
    const next = { ...state, phase, downProbes, sawDown: state.sawDown || phase === 'down' }
    if (now >= state.expiresAt) return { action: 'timeout', state: next }
    return { action: 'wait', state: next }
  }
  if (state.sawDown) return { action: 'reload' }
  const next = { ...state, downProbes: 0, logTail: probe.status?.log_tail ?? state.logTail }
  // `running: false` from a daemon we never lost means the script stopped before
  // the stop stage: a refused preflight, or a failed build. Either way the
  // current app is untouched and there is nothing to reload into.
  if (probe.status && probe.status.running === false) return { action: 'finished', state: next }
  if (now >= state.expiresAt) return { action: 'timeout', state: next }
  return { action: 'wait', state: next }
}

/** mm:ss since the redeploy was first observed. */
export function elapsedLabel(startedAt: number, now: number): string {
  const seconds = Math.max(0, Math.floor((now - startedAt) / 1000))
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`
}

/** What the chip says about where the redeploy is. There is no real progress to
 *  report during the outage — the only process that knows is the one that took
 *  the daemon away — so this says what stage it is in and lets the elapsed timer
 *  carry the rest, rather than inventing a percentage. */
export function phaseLabel(phase: RedeployPhase): string {
  if (phase === 'building') return 'Rebuilding app'
  if (phase === 'down') return 'Restarting app'
  return ''
}

export function phaseDetail(phase: RedeployPhase): string {
  if (phase === 'building') {
    return 'The new build runs alongside the current app, so you can keep working. '
      + 'The app restarts when it finishes.'
  }
  if (phase === 'down') {
    return 'The app is being swapped and restarted around your live sessions. '
      + 'This page reloads by itself when it comes back.'
  }
  return ''
}

/** A user-facing sentence for a finished redeploy, or '' when it plainly worked.
 *  A rollback is the case this exists for: the app comes back looking normal, so
 *  without being told, the operator has no way to know their change never shipped. */
export function outcomeNotice(result: RedeployOutcome | null | undefined): string {
  if (!result || !result.outcome || result.outcome === 'succeeded') return ''
  const detail = (result.detail || '').trim()
  const headline: Record<string, string> = {
    rolled_back: 'Redeploy failed - the previous app was restored.',
    build_failed: 'Redeploy failed during the build.',
    swap_failed: 'Redeploy failed while swapping the app bundle.',
    unhealthy: 'The rebuilt app never reported healthy.',
    refused: 'Redeploy was refused before anything changed.',
    failed: 'Redeploy failed.',
  }
  const lead = headline[result.outcome] || 'Redeploy did not complete.'
  return detail ? `${lead} ${detail}` : lead
}

/** True when a result is worth showing after the page reloads. Bounded by age so
 *  a week-old failure cannot resurface on an unrelated reload. */
export function outcomeIsFresh(
  result: RedeployOutcome | null | undefined, now: number, maxAgeMs = REDEPLOY_MAX_MS,
): boolean {
  if (!result || typeof result.finished_at !== 'number') return false
  const finishedAt = result.finished_at * 1000
  return finishedAt <= now + 60_000 && now - finishedAt <= maxAgeMs
}

type Storage = Pick<globalThis.Storage, 'getItem' | 'setItem' | 'removeItem'>

/** Read back a redeploy this client (or a previous load of it) already knew
 *  about. Anything expired is dropped rather than resumed: a stale sentinel is
 *  how a tab would come up permanently blocked with nothing to un-block it. */
export function loadRedeploy(store: Storage | null, now: number): RedeployState {
  if (!store) return IDLE_REDEPLOY
  let raw: string | null = null
  try { raw = store.getItem(REDEPLOY_STORAGE_KEY) } catch { return IDLE_REDEPLOY }
  if (!raw) return IDLE_REDEPLOY
  try {
    const parsed = JSON.parse(raw) as Partial<RedeployState>
    const startedAt = Number(parsed.startedAt) || 0
    const expiresAt = Number(parsed.expiresAt) || 0
    const known = parsed.phase === 'down' || parsed.phase === 'building'
    if (!known || !expiresAt || now >= expiresAt) return IDLE_REDEPLOY
    // Restored state never blocks and never claims to have seen an outage, even
    // if it was saved during one. There is no offline shell, so a page that
    // managed to load was served by a live daemon: the outage is over as far as
    // this document is concerned, and the wait loop's first probe will say
    // whether the redeploy itself still is. Restoring 'down' verbatim would
    // instead flash a full-screen overlay over a working app, and restoring
    // `sawDown` would make that first healthy probe reload the page for nothing.
    return {
      phase: 'building', startedAt: startedAt || now, expiresAt,
      sawDown: false, downProbes: 0, logTail: [],
    }
  } catch { return IDLE_REDEPLOY }
}

export function saveRedeploy(store: Storage | null, state: RedeployState): void {
  if (!store) return
  try {
    if (state.phase === 'idle') store.removeItem(REDEPLOY_STORAGE_KEY)
    // The log tail is deliberately not persisted: it is refetched from the
    // daemon whenever one is reachable, and it is the only unbounded field here.
    else store.setItem(REDEPLOY_STORAGE_KEY, JSON.stringify({ ...state, logTail: [] }))
  } catch { /* private mode, or a full quota; the in-memory state still works */ }
}

/** Hand the next page load a one-shot request to report the redeploy's outcome,
 *  and drop the in-flight sentinel so the reloaded page does not resume a wait
 *  loop for a redeploy that has already finished. */
export function markResultPending(store: Storage | null): void {
  if (!store) return
  try {
    store.removeItem(REDEPLOY_STORAGE_KEY)
    store.setItem(REDEPLOY_RESULT_KEY, '1')
  } catch { /* see saveRedeploy */ }
}

/** True once, on the boot that follows a redeploy reload. */
export function takeResultPending(store: Storage | null): boolean {
  if (!store) return false
  try {
    const pending = store.getItem(REDEPLOY_RESULT_KEY) === '1'
    if (pending) store.removeItem(REDEPLOY_RESULT_KEY)
    return pending
  } catch { return false }
}
