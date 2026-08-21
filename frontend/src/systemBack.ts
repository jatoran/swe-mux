// Makes the platform's own back gesture mean one step back inside the app.
//
// What a step *is* belongs to the target, not here: on its own the dismiss stack closes
// one overlay level, and `composeBackTarget` (`viewHistory.ts`) puts the recent-views
// ring underneath it so back keeps working once nothing is layered. This module only
// owns the browser half - one sentinel history entry, maintained against the target's
// depth - and is deliberately blind to which rung answered.
//
// swe-mux installs as a `display: standalone` PWA, where Android's back gesture is the
// primary navigation control. With no history entry to pop it backgrounds the app, so a
// phone user with a modal open lost the whole view instead of stepping back. The app
// keeps no route history of its own (`App` only ever `replaceState`s the URL to track
// the focused session), so the fix is a single sentinel entry that exists exactly while
// the target has somewhere to go back to.
//
// One sentinel, never one per level: per-level entries desynchronize the first time a
// level closes by button instead of by back, and nothing can resynchronize them.
//
// The coordinator is separated from the DOM so the four transitions that break real
// implementations — open, close by button, close by back, nested back — are testable.

import { dismissStack, type BackTarget } from './dismissStack.ts'

export type BackHost = {
  /** Push a history entry marked as ours, at the current URL. */
  pushSentinel: () => void
  /** Step back over our sentinel. Emits a popstate the coordinator must ignore. */
  back: () => void
  /** Whether the current history entry is the sentinel we pushed. */
  onSentinel: () => boolean
}

export type BackCoordinator = ReturnType<typeof createBackCoordinator>

export function createBackCoordinator(stack: Pick<BackTarget, 'depth' | 'pop'>, host: BackHost) {
  let armed = false
  // Counts popstates we caused ourselves. A counter rather than a flag because two
  // close-by-button transitions can leave two `back()` calls in flight.
  let selfInflicted = 0

  const arm = () => { if (armed) return; host.pushSentinel(); armed = true }

  const disarm = () => {
    if (!armed) return
    armed = false
    // Only step back if we are actually standing on our own sentinel. If something
    // else navigated in the meantime, `back()` would move the user somewhere they
    // did not ask to go, and dropping the entry is the cheaper mistake.
    if (!host.onSentinel()) return
    selfInflicted++
    host.back()
  }

  /** Call whenever the stack's depth may have changed. */
  const sync = () => {
    if (stack.depth() > 0) arm()
    else disarm()
  }

  const handlePopstate = () => {
    if (selfInflicted > 0) { selfInflicted--; return }
    if (!armed) return
    // The platform consumed our sentinel to get here.
    armed = false
    stack.pop()
    // Still layered (nested overlays, or a blocking level that refused): re-arm so the
    // next back keeps being ours rather than leaving the app.
    if (stack.depth() > 0) arm()
  }

  return {
    sync,
    handlePopstate,
    /** Diagnostics only. */
    state: () => ({ armed, selfInflicted }),
  }
}

const SENTINEL_KEY = 'muxDismiss'

function browserHost(): BackHost {
  return {
    pushSentinel: () => {
      const current = history.state && typeof history.state === 'object' ? history.state as Record<string, unknown> : {}
      // Same URL: the sentinel is a dismiss depth marker, not a route. `App`'s URL
      // sync uses `replaceState` and passes the existing state object through, so the
      // marker survives a session focus change while an overlay is open.
      history.pushState({ ...current, [SENTINEL_KEY]: true }, '', `${location.pathname}${location.search}${location.hash}`)
    },
    back: () => history.back(),
    onSentinel: () => Boolean(history.state && typeof history.state === 'object' && (history.state as Record<string, unknown>)[SENTINEL_KEY]),
  }
}

/**
 * Wire a live back target to the browser's history. Returns a teardown.
 *
 * Note the one gesture this deliberately does not see: when the Android soft keyboard
 * is up, back closes the keyboard and the page is never told, so the overlay behind it
 * survives the first press. That matches the platform everywhere else and is why the
 * keyboard case is not special-cased here (`ui.md` § focus and responsive behavior).
 */
export function installSystemBack(stack: BackTarget = dismissStack, host: BackHost = browserHost()): () => void {
  const coordinator = createBackCoordinator(stack, host)
  const onPopstate = () => coordinator.handlePopstate()
  window.addEventListener('popstate', onPopstate)
  const unsubscribe = stack.subscribe(coordinator.sync)
  coordinator.sync()
  return () => {
    window.removeEventListener('popstate', onPopstate)
    unsubscribe()
  }
}
