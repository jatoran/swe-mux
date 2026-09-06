import { api } from './api.ts'

/**
 * The client half of a factory reset: what the daemon says one would do, the
 * request that starts it, and the part only a browser can do.
 *
 * That last part is the reason this file exists rather than four lines inside
 * the dialog. **The daemon cannot reach the browser's own storage.** Layouts,
 * the remembered Settings tab, dismissed banners, the device's sound profile
 * and the service worker all live in this origin, and a reset that wiped
 * `~/.mux` and left them would come back up looking like the install it just
 * erased - which reads as the feature not working. So `clearClientStorage`
 * empties the origin, and it runs on every client that pressed the button,
 * once the daemon has accepted.
 *
 * Everything here is best-effort by design. A browser that refuses one storage
 * API (private mode, a disabled IndexedDB, a cross-origin cache) must not stop
 * the reload; the reset itself already happened on the daemon side, and a
 * client stuck on a thrown `SecurityError` is strictly worse than one that
 * comes back with a stale layout.
 */

export type FactoryResetSession = {
  id: string
  name: string
  backend: string
  state: string
  project: string
}

export type FactoryResetPreview = {
  confirmation_phrase: string
  data_dir: string
  sessions: FactoryResetSession[]
  worktrees: string[]
  entries: string[]
  kept: string[]
  external_left: { item: string; detail: string }[]
  relaunchable: boolean
  local: boolean
  last_result: FactoryResetResult | null
}

export type FactoryResetResult = {
  performed_at: number
  seconds: number
  trash_path: string
  moved: string[]
  truncated: string[]
  kept: string[]
  failed: { name: string; error: string }[]
  worktrees: string[]
  external: { item: string; path: string; action: string; detail: string }[]
}

export type FactoryResetAccepted = {
  status: string
  sessions_reaped: number
  supervisor_stopped: boolean
  external: boolean
  clear_client_storage: boolean
}

export const FACTORY_RESET_PATH = '/api/maintenance/factory-reset'

/** How long to wait for the successor daemon before giving up on the reload.
 *  Longer than the daemon-reload wait: the successor waits out its predecessor
 *  and then moves the whole data directory aside before it serves. */
export const RESET_HEALTH_TIMEOUT_MS = 180_000

/** How long to let the old daemon exit before a healthy answer means the new one. */
export const RESET_SETTLE_MS = 2000

export const RESET_POLL_MS = 750

export function fetchFactoryResetPreview(): Promise<FactoryResetPreview> {
  return api<FactoryResetPreview>('GET', FACTORY_RESET_PATH, undefined, { timeoutMs: 10_000 })
}

export function requestFactoryReset(confirm: string, external: boolean): Promise<FactoryResetAccepted> {
  return api<FactoryResetAccepted>('POST', FACTORY_RESET_PATH, { confirm, external })
}

/** Whether what was typed will be accepted, by the daemon's own phrase. */
export function confirmationMatches(typed: string, phrase: string): boolean {
  return typed.trim().toLocaleLowerCase() === phrase.trim().toLocaleLowerCase()
}

async function clearCaches(): Promise<void> {
  if (typeof caches === 'undefined') return
  const names = await caches.keys()
  await Promise.all(names.map(name => caches.delete(name)))
}

async function clearIndexedDb(): Promise<void> {
  const factory = typeof indexedDB === 'undefined' ? null : indexedDB
  // `databases()` is unavailable on Firefox, where there is no way to enumerate
  // them at all. Nothing to do there is the honest outcome, not a failure.
  if (!factory || typeof factory.databases !== 'function') return
  const databases = await factory.databases()
  await Promise.all(databases.map(entry => new Promise<void>(resolve => {
    if (!entry.name) { resolve(); return }
    const request = factory.deleteDatabase(entry.name)
    request.onsuccess = request.onerror = request.onblocked = () => resolve()
  })))
}

async function unregisterWorkers(): Promise<void> {
  if (typeof navigator === 'undefined' || !navigator.serviceWorker) return
  const registrations = await navigator.serviceWorker.getRegistrations()
  await Promise.all(registrations.map(registration => registration.unregister()))
}

/**
 * Empty this origin: web storage, IndexedDB, the caches, and the service worker.
 *
 * Returns the names of the steps that threw, so the dialog can say what it could
 * not clear rather than claiming a clean origin it did not get.
 */
export async function clearClientStorage(): Promise<string[]> {
  const failures: string[] = []
  const steps: [string, () => void | Promise<void>][] = [
    ['local storage', () => { localStorage.clear() }],
    ['session storage', () => { sessionStorage.clear() }],
    ['caches', clearCaches],
    ['indexed databases', clearIndexedDb],
    ['service worker', unregisterWorkers],
  ]
  for (const [name, step] of steps) {
    try { await step() } catch { failures.push(name) }
  }
  return failures
}

/** Poll `/api/health` until the successor answers, then resolve true. */
export async function waitForDaemon(
  deadlineMs = RESET_HEALTH_TIMEOUT_MS,
  fetchImpl: typeof fetch = fetch,
  sleep: (ms: number) => Promise<void> = ms => new Promise(resolve => setTimeout(resolve, ms)),
): Promise<boolean> {
  // The first second could still be the predecessor draining, and treating that
  // as the successor would reload the page into a daemon that is about to exit.
  await sleep(RESET_SETTLE_MS)
  const deadline = Date.now() + deadlineMs
  while (Date.now() < deadline) {
    try {
      const response = await fetchImpl('/api/health', { cache: 'no-store' })
      if (response.ok) return true
    } catch { /* the daemon is still away, which is the expected case */ }
    await sleep(RESET_POLL_MS)
  }
  return false
}
