/**
 * The polling loop behind process inspection, shared by every surface that draws it.
 *
 * The full snapshot carries identity evidence, parent lineage, connections, and daemon members,
 * and it is the payload the reduced `?summary=1` projection exists to avoid sending on the
 * always-mounted rail poll. Two surfaces now ask for the full one — the modal inspector and the
 * drawer's Processes tab — and the drawer can be split so the tab is selected in both stacks at
 * once. Left to themselves that is three identical reads every two seconds of a sample the
 * daemon computed once.
 *
 * So the loop lives here, refcounted and keyed by the request, and the mounted surfaces are
 * subscribers. One request per tick per distinct scope, however many components are drawing it.
 *
 * A closed surface polls nothing: the last listener leaving stops the timer. Its final result is
 * kept only long enough to survive a tab switch (`GRACE_MS`), so re-opening draws the tree it
 * had rather than a loading line, and anything older is discarded rather than shown as live.
 */

import { api } from './api.ts'
import {
  combineSessionSnapshots, normalizeSnapshot,
  type FleetSnapshot, type Preview, type SessionSnapshot,
} from './processFleet.ts'

export const FLEET_POLL_MS = 2000
/** How long a result outlives its last subscriber. Longer than one poll so a tab switch is
 *  seamless; short enough that nothing minutes-old is ever drawn as the live fleet. */
const GRACE_MS = 6000

export type FleetRequest = { sessionId: string | null; includeEnded: boolean }
export type FleetResult = {
  snapshot: FleetSnapshot | null
  previews: Preview[]
  error: string
  /** `performance.now()` of the read, so a subscriber can tell a served cache from a fresh one. */
  at: number
}

type ScopeSession = { id: string; project_id: string; state?: string }
type Listener = (result: FleetResult) => void
type Feed = {
  listeners: Set<Listener>
  timer: number | null
  expiry: number | null
  inflight: boolean
  last: FleetResult | null
}

const feeds = new Map<string, Feed>()
let sessions: ScopeSession[] = []
let visibilityBound = false

/** The live session list, which the older-daemon fallback enumerates and which resolves a
 *  session-scoped payload's Project. Every surface renders from the same list, so one shared
 *  value is correct and saves threading it through the subscription. */
export function setFleetSessions(next: ScopeSession[]): void {
  sessions = next
}

export const fleetRequestKey = (request: FleetRequest): string =>
  `${request.sessionId || ''}|${request.includeEnded ? 'ended' : 'live'}`

const requestPath = (request: FleetRequest): string => {
  const ended = request.includeEnded ? 'include_ended=1' : ''
  if (request.sessionId) {
    return `/api/processes?session=${encodeURIComponent(request.sessionId)}${ended ? `&${ended}` : ''}`
  }
  return `/api/processes${ended ? `?${ended}` : ''}`
}

/** A daemon predating the coherent all-session payload rejects the unscoped read by name. */
const wantsSessionScope = (cause: unknown) =>
  cause instanceof Error && /session query parameter is required/i.test(cause.message)

async function readSnapshot(request: FleetRequest): Promise<FleetSnapshot> {
  const current = sessions
  try {
    return normalizeSnapshot(
      await api<FleetSnapshot | SessionSnapshot>('GET', requestPath(request)),
      current,
    )
  } catch (cause) {
    if (request.sessionId || !wantsSessionScope(cause)) throw cause
    const live = current.filter(item => !['exited', 'crashed'].includes(item.state || ''))
    const snapshots = await Promise.all(live.map(async session => {
      try {
        return await api<SessionSnapshot>('GET', requestPath({ ...request, sessionId: session.id }))
      } catch (error) {
        // A session that ended between the listing and this read is not an outage.
        if ((error as Error & { status?: number }).status === 404) return null
        throw error
      }
    }))
    return combineSessionSnapshots(
      snapshots.filter((item): item is SessionSnapshot => item !== null),
      current,
    )
  }
}

function publish(feed: Feed, result: FleetResult): void {
  feed.last = result
  for (const listener of [...feed.listeners]) listener(result)
}

async function poll(key: string, request: FleetRequest): Promise<void> {
  const feed = feeds.get(key)
  if (!feed || feed.inflight) return
  feed.inflight = true
  try {
    const previews = api<{ items: Preview[] }>('GET', '/api/previews')
    const snapshot = await readSnapshot(request)
    const items = (await previews).items
    if (feeds.get(key) !== feed) return
    publish(feed, { snapshot, previews: items, error: '', at: performance.now() })
  } catch (cause) {
    if (feeds.get(key) !== feed) return
    // The last good snapshot is kept beside the error: a failed refresh should not blank a
    // tree the operator is reading, and the error line says the figures stopped moving.
    publish(feed, {
      snapshot: feed.last?.snapshot || null,
      previews: feed.last?.previews || [],
      error: cause instanceof Error ? cause.message : String(cause),
      at: performance.now(),
    })
  } finally {
    feed.inflight = false
  }
}

function bindVisibility(): void {
  if (visibilityBound) return
  visibilityBound = true
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) return
    for (const [key, feed] of feeds) if (feed.listeners.size) void poll(key, parseKey(key))
  })
}

const parseKey = (key: string): FleetRequest => {
  const [sessionId, mode] = key.split('|')
  return { sessionId: sessionId || null, includeEnded: mode === 'ended' }
}

/**
 * Draw from `request`'s feed until the returned function is called.
 *
 * The listener fires immediately with a cached result when one is fresh enough to be worth
 * drawing, and then on every completed read.
 */
export function subscribeFleet(request: FleetRequest, onResult: Listener): () => void {
  const key = fleetRequestKey(request)
  bindVisibility()
  let feed = feeds.get(key)
  if (!feed) {
    feed = { listeners: new Set(), timer: null, expiry: null, inflight: false, last: null }
    feeds.set(key, feed)
  }
  if (feed.expiry !== null) { clearTimeout(feed.expiry); feed.expiry = null }
  feed.listeners.add(onResult)
  if (feed.last && performance.now() - feed.last.at <= GRACE_MS) onResult(feed.last)
  else feed.last = null
  if (feed.timer === null) {
    feed.timer = window.setInterval(() => {
      if (!document.hidden) void poll(key, request)
    }, FLEET_POLL_MS)
  }
  void poll(key, request)
  return () => {
    const current = feeds.get(key)
    if (!current) return
    current.listeners.delete(onResult)
    if (current.listeners.size) return
    if (current.timer !== null) { clearInterval(current.timer); current.timer = null }
    // Held briefly so a tab switch redraws instantly, then dropped so nothing stale returns.
    current.expiry = window.setTimeout(() => {
      if (feeds.get(key) === current && !current.listeners.size) feeds.delete(key)
    }, GRACE_MS)
  }
}

/** Re-read now, for the explicit refresh control. */
export function refreshFleet(request: FleetRequest): void {
  const key = fleetRequestKey(request)
  if (feeds.has(key)) void poll(key, request)
}

/** Drop every cached result, so the next read is genuinely fresh. Used after an action that
 *  changes the fleet (terminate), where showing the pre-action tree for a tick reads as a
 *  failed action. */
export function invalidateFleet(): void {
  for (const [key, feed] of feeds) {
    feed.last = null
    if (feed.listeners.size) void poll(key, parseKey(key))
  }
}
