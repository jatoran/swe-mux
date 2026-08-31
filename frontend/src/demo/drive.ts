/**
 * Driving the real app from outside it, without pretending to be the daemon.
 *
 * Two things in the demo need this and used to each have their own copy: the cross-frame
 * view mirror (`mirror.ts`), which converges one frame onto another frame's state, and
 * the scenario director (`director.ts`), which replays a scripted visitor. Both want the
 * same small vocabulary - dispatch a named command, press a real control, read what is on
 * screen - and both must reach the app the way a person does rather than by calling into
 * its internals.
 *
 * The rule the whole layer follows: **the app's own command bus and the app's own
 * controls, never its state.** `mux:command` is the bus every keyboard chord, menu row,
 * command palette entry and voice phrase already routes through, so anything driven here
 * is indistinguishable from a visitor having done it - including to the mirror, which is
 * why a director beat on one frame lights up the other for free.
 */
import { state } from './store.ts'

/** Collapse whitespace so a label read out of the DOM compares against an authored one. */
export const text = (value: string | null | undefined): string =>
  (value || '').replace(/\s+/g, ' ').trim()

/** On screen at all. `getClientRects()` rather than `offsetParent`, because a `position:
 *  fixed` overlay has no offset parent and would read as hidden. */
export const visible = (element: Element): boolean => element.getClientRects().length > 0

/** The phone layout, by the same query the app's own CSS uses. */
export const narrow = (): boolean => window.matchMedia('(max-width: 760px)').matches

export const delay = (ms: number): Promise<void> =>
  new Promise(resolve => { window.setTimeout(resolve, ms) })

/** Ask the app to run a named command, exactly as a chord or a menu row would. */
export const runCommand = (command: string): void => {
  window.dispatchEvent(new CustomEvent('mux:command', { detail: command }))
}

/**
 * The app's own dismiss stack, reached the way a person reaches it.
 *
 * Only ever sent when this frame has something open, so it can never fall through to the
 * terminal underneath - Escape there is an interrupt, which is not a thing to send by
 * accident.
 */
export const pressEscape = (): void => {
  window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
}

/**
 * Segmented controls nothing here may drive.
 *
 * The side panel's own tab strip is a tablist, and clicking its selected tab collapses
 * the panel - so a generic rule would close the panel it had just opened. The pane tab
 * rails are a tablist too, and they are pane geometry, which the mirror deliberately
 * leaves alone.
 */
export const mirrorableTablist = (strip: Element): boolean =>
  !strip.querySelector('[data-drawer-tab-id]') && !strip.classList.contains('stack-tabs')

/** The first element matching any of these selectors that is actually drawn. Ordered:
 *  the caller's list is a preference, not a set. */
export function firstVisible(selectors: readonly string[] | undefined): HTMLElement | null {
  for (const selector of selectors || []) {
    for (const candidate of document.querySelectorAll<HTMLElement>(selector)) {
      const rect = candidate.getBoundingClientRect()
      if (rect.width > 4 && rect.height > 4) return candidate
    }
  }
  return null
}

/** Press the first visible match and hand it back, so a caller can draw at it. */
export function clickFirst(selectors: readonly string[]): HTMLElement | null {
  const target = firstVisible(selectors)
  if (!target) return null
  target.click()
  return target
}

/** Select a Project by its row in the fleet column. Names rather than ids, because the
 *  row carries the name and the id is not in the DOM. */
export function clickProject(projectId: string): boolean {
  const name = state.projects.find(item => item.id === projectId)?.name
  if (!name) return false
  for (const row of document.querySelectorAll<HTMLElement>('.project-row')) {
    if (text(row.querySelector('.project-name-text')?.textContent) !== name) continue
    row.click()
    return true
  }
  return false
}

export function clickSession(sessionId: string): boolean {
  const row = document.querySelector<HTMLElement>(
    `[data-sidebar-session-id="${CSS.escape(sessionId)}"]`,
  )
  if (!row) return false
  row.click()
  return true
}

/** Select a tab in the segmented control announcing itself with `label`. Returns false
 *  when it is already selected, because pressing it again is not a no-op everywhere. */
export function clickTab(label: string, wanted: string): boolean {
  for (const strip of document.querySelectorAll('[role="tablist"][aria-label]')) {
    if (strip.getAttribute('aria-label') !== label || !mirrorableTablist(strip)) continue
    for (const tab of strip.querySelectorAll<HTMLElement>('[role="tab"]')) {
      if (text(tab.textContent) !== wanted) continue
      if (tab.getAttribute('aria-selected') === 'true') return false
      tab.click()
      return true
    }
  }
  return false
}
