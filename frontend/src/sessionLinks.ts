// Session links: `https://<host>/?session=<id>`, sent by agents (Telegram, notifications), must
// land on that exact session - and on Windows, in the desktop app when it is installed.
//
// Three ways a link reaches this page, one inbox for all of them:
//
// - **The page's own URL.** A followed link carries `session=` and, unlike the URL the app
//   projects for itself (`viewState.viewUrl`, which always writes `project=`), usually nothing
//   else. A fresh navigation to any URL naming a session counts too, so a link that does name
//   its Project still wins; a reload or a history step does not, because then the URL is just
//   this tab's own projection and the per-device focus memory is the better answer.
// - **The desktop shell.** A `swe-mux://` link handed to the running window arrives through
//   `window.__swemuxOpenSessionLink` (`swe_mux/desktop_links.py`), installed here before the
//   app renders so a link that arrives during startup is queued rather than lost.
// - **A desktop cold start**, which opens the window at `/?session=<id>&handoff=<id>`: the URL
//   case again, plus a hand-off id to report back on.
//
// The inbox is drained by `App.tsx` once focus has hydrated and the session list has loaded,
// so "the session no longer exists" is a real answer rather than a list that has not arrived.

import type { HostKind, PlatformKind } from './hostProfile'

export const SESSION_LINK_HOOK = '__swemuxOpenSessionLink'
export const SESSION_PARAM = 'session'
export const PROJECT_PARAM = 'project'
export const HANDOFF_PARAM = 'handoff'

// The same closed patterns as `desktop_links.py`; a link is never parsed beyond them.
const SESSION_ID = /^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/
const HANDOFF_ID = /^[A-Za-z0-9_-]{16,64}$/

export type SessionLinkSource = 'url' | 'shell'

export type SessionLinkRequest = {
  sessionId: string
  /** The browser tab waiting to hear that the desktop window took this link. */
  handoff: string | null
  source: SessionLinkSource
}

export const validSessionId = (value: unknown): value is string =>
  typeof value === 'string' && SESSION_ID.test(value)

export const validHandoffId = (value: unknown): value is string =>
  typeof value === 'string' && HANDOFF_ID.test(value)

/** `navigate`, `reload`, `back_forward`, `prerender`, or null where the API is missing. */
export function currentNavigationType(
  perf: Pick<Performance, 'getEntriesByType'> | undefined = globalThis.performance,
): string | null {
  try {
    const entry = perf?.getEntriesByType('navigation')[0] as PerformanceNavigationTiming | undefined
    return entry?.type ?? null
  } catch {
    return null
  }
}

/** The link this page was opened with, or null when its URL is only its own projection. */
export function followedLink(search: string, navigationType: string | null): SessionLinkRequest | null {
  const params = new URLSearchParams(search)
  const sessionId = params.get(SESSION_PARAM)
  if (!validSessionId(sessionId)) return null
  const rawHandoff = params.get(HANDOFF_PARAM)
  const handoff = validHandoffId(rawHandoff) ? rawHandoff : null
  // A hand-off id is only ever minted for a link, so it settles the question by itself.
  if (handoff || !params.has(PROJECT_PARAM) || navigationType === 'navigate') {
    return { sessionId, handoff, source: 'url' }
  }
  return null
}

/** What the desktop shell passed to the hook, validated, or null. */
export function shellLink(value: unknown): SessionLinkRequest | null {
  if (!value || typeof value !== 'object') return null
  const raw = value as { session_id?: unknown; handoff?: unknown }
  if (!validSessionId(raw.session_id)) return null
  if (raw.handoff !== null && raw.handoff !== undefined && !validHandoffId(raw.handoff)) return null
  return { sessionId: raw.session_id, handoff: validHandoffId(raw.handoff) ? raw.handoff : null, source: 'shell' }
}

/** `href` without its hand-off id, as path+search+hash, or null when it carried none. */
export function withoutHandoff(href: string): string | null {
  const url = new URL(href)
  if (!url.searchParams.has(HANDOFF_PARAM)) return null
  url.searchParams.delete(HANDOFF_PARAM)
  return `${url.pathname}${url.search}${url.hash}`
}

export class SessionLinkInbox {
  private queue: SessionLinkRequest[] = []
  private readonly listeners = new Set<() => void>()
  /** The link this page loaded with, kept after it is drained: hydration asks about it. */
  initial: SessionLinkRequest | null = null

  push(request: SessionLinkRequest): void {
    this.queue.push(request)
    for (const listener of this.listeners) listener()
  }

  /** Everything waiting, oldest first, leaving the inbox empty. */
  drain(): SessionLinkRequest[] {
    const taken = this.queue
    this.queue = []
    return taken
  }

  get size(): number { return this.queue.length }

  subscribe(listener: () => void): () => void {
    this.listeners.add(listener)
    return () => { this.listeners.delete(listener) }
  }
}

export const sessionLinks = new SessionLinkInbox()

type InboxWindow = {
  location: Pick<Location, 'href' | 'search'>
  history: Pick<History, 'state' | 'replaceState'>
}

/**
 * Read the link this page was opened with, strip its hand-off id, and install the shell's hook.
 *
 * Runs before the first render (`main.tsx`). The hand-off id leaves the address bar at once: it
 * is single-use, and a reload or a copied URL must not report the hand-off a second time.
 */
export function installSessionLinkInbox(
  inbox: SessionLinkInbox = sessionLinks,
  win: InboxWindow = window,
  navigationType: string | null = currentNavigationType(),
  hookTarget: Record<string, unknown> = globalThis as unknown as Record<string, unknown>,
): void {
  const initial = followedLink(win.location.search, navigationType)
  inbox.initial = initial
  if (initial) inbox.push(initial)
  const stripped = withoutHandoff(win.location.href)
  if (stripped !== null) win.history.replaceState(win.history.state, '', stripped)
  hookTarget[SESSION_LINK_HOOK] = (value: unknown): boolean => {
    const request = shellLink(value)
    if (!request) return false
    inbox.push(request)
    return true
  }
}

// --- the browser tab's hand-off to the desktop app -------------------------------------

export type HandoffContext = {
  host: HostKind
  platform: PlatformKind
  /** An installed PWA window is already an app; only a browser tab hands off. */
  standalone: boolean
  source: SessionLinkSource
}

/**
 * Whether this page should ask the daemon about handing its link to the desktop app.
 *
 * Only a Windows browser tab a link just opened. The daemon then decides the part this page
 * cannot know: whether the tab is on the daemon's own machine and whether the app is there.
 */
export const desktopHandoffEligible = (context: HandoffContext): boolean =>
  context.source === 'url' && context.host === 'browser' && context.platform === 'win' && !context.standalone

export function standaloneDisplay(
  media: ((query: string) => { matches: boolean }) | undefined = globalThis.matchMedia?.bind(globalThis),
): boolean {
  if (!media) return false
  return ['standalone', 'fullscreen', 'minimal-ui', 'window-controls-overlay']
    .some(mode => { try { return media(`(display-mode: ${mode})`).matches } catch { return false } })
}

export type DesktopHandoffStart = { attempt: boolean; reason?: string; handoff_id?: string; url?: string }
export type DesktopHandoffState = { state: 'pending' | 'opened' | 'unknown'; found?: boolean | null }

export type HandoffDeps = {
  start: (sessionId: string) => Promise<DesktopHandoffStart>
  poll: (handoffId: string) => Promise<DesktopHandoffState>
  /** Opens the `swe-mux://` link; the page itself stays loaded. */
  launch: (url: string) => void
  close: () => void
  wait: (ms: number) => Promise<void>
  /** True once the person has started using this tab, which keeps it. */
  interacted: () => boolean
  now: () => number
  note: (message: string) => void
}

// There is no "closed" outcome: a tab that closes runs nothing after it.
export type HandoffOutcome =
  | 'not_attempted'
  | 'failed_to_start'
  | 'opened_tab_kept'
  | 'kept_by_interaction'
  | 'expired'
  | 'timed_out'

export const HANDOFF_POLL_MS = 500
// Long enough for a cold desktop start behind the browser's "Open swe-mux?" prompt. The tab is
// the full web UI on the linked session the whole time, so running out costs nothing.
export const HANDOFF_BUDGET_MS = 90_000
// How long a refused `window.close()` gets before the tab says what happened instead.
export const CLOSE_GRACE_MS = 400

/**
 * Hand this tab's link to the desktop app and close the tab once the app has it.
 *
 * Never leaves a dead page: the tab keeps running the web UI throughout, and every way this
 * can end other than the desktop window confirming the link leaves it exactly as it is.
 */
export async function runDesktopHandoff(
  sessionId: string,
  deps: HandoffDeps,
  { pollMs = HANDOFF_POLL_MS, budgetMs = HANDOFF_BUDGET_MS, closeGraceMs = CLOSE_GRACE_MS } = {},
): Promise<HandoffOutcome> {
  let started: DesktopHandoffStart
  try {
    started = await deps.start(sessionId)
  } catch (error) {
    deps.note(`desktop handoff could not start: ${error instanceof Error ? error.message : String(error)}`)
    return 'failed_to_start'
  }
  if (!started.attempt || !started.handoff_id || !started.url) {
    deps.note(`desktop handoff not attempted: ${started.reason || 'unknown'}`)
    return 'not_attempted'
  }
  const handoffId = started.handoff_id
  deps.note(`desktop handoff ${handoffId} launching`)
  deps.launch(started.url)
  const deadline = deps.now() + budgetMs
  while (deps.now() < deadline) {
    await deps.wait(pollMs)
    if (deps.interacted()) {
      deps.note(`desktop handoff ${handoffId} kept: the tab is in use`)
      return 'kept_by_interaction'
    }
    let state: DesktopHandoffState
    try {
      state = await deps.poll(handoffId)
    } catch {
      continue // a daemon blip; the next poll asks again
    }
    if (state.state === 'unknown') {
      deps.note(`desktop handoff ${handoffId} expired`)
      return 'expired'
    }
    if (state.state === 'opened') {
      deps.note(`desktop handoff ${handoffId} opened (found=${String(state.found)}); closing the tab`)
      deps.close()
      // A tab a script may not close (one with history behind it) simply stays.
      await deps.wait(closeGraceMs)
      return 'opened_tab_kept'
    }
  }
  deps.note(`desktop handoff ${handoffId} timed out; staying in the browser`)
  return 'timed_out'
}
