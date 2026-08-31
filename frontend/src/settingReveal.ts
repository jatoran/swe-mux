/**
 * Arriving at a setting: find the control, scroll it into view, and flash it.
 *
 * Landing on the right tab is not arriving. Every settings surface here is a long scroller —
 * the Automation tab is six sections, a Project's opt-in list is below every other Project
 * field — so a deep link that only opens the panel leaves the user searching for the switch
 * they were just promised, on a phone most of all, where the control is usually several
 * screens down. This module is the last leg: `[data-setting]` names the control, the reveal
 * waits for it, centres it, and flashes it.
 *
 * Three things make it reliable rather than a one-shot `scrollIntoView`:
 *
 *  - **It waits.** The control is often not in the DOM when the reveal is asked for: Settings
 *    fetches its bundle before rendering any tab, and a Project's automation list is a second
 *    fetch inside the panel. A MutationObserver watches until the control appears, bounded by
 *    a deadline so a target that never renders (a renamed control, a failed fetch) gives up
 *    quietly instead of holding an observer forever.
 *  - **It waits for layout, not just for the node.** A control that exists inside a subtree
 *    with no layout box cannot be scrolled to; `layoutBox.ts` answers that exactly.
 *  - **It centres.** Both surfaces carry a sticky header inside the scroller (the section rail
 *    in Settings, the title block in the Projects registry), and `block:'nearest'` parks the
 *    control underneath it. `'center'` clears both without measuring either, and behaves the
 *    same on the desktop two-column layout and the phone's single scrolling column.
 */

// Explicit extension: the node test runner imports this module directly and resolves no
// bare specifiers, the same reason `dismissStack.ts` and `serverClock.ts` are spelled out.
import { hasLayoutBox } from './layoutBox.ts'

/** How long the arrival flash stays on the control. */
export const SETTING_FLASH_MS = 1800
/** How long to wait for a control that has not rendered yet before giving up. */
export const SETTING_REVEAL_TIMEOUT_MS = 6000

export const SETTING_FLASH_CLASS = 'setting-flash'
export const SETTING_SECTION_FLASH_CLASS = 'setting-section-flash'
export const SETTING_SECTION_HEADING_FLASH_CLASS = 'setting-section-heading-flash'

export type RevealOptions = {
  flashMs?: number
  timeoutMs?: number
  behavior?: ScrollBehavior
  /** Coarse pointer: suppresses focusing a text field, which would raise the keyboard. */
  coarsePointer?: boolean
}

/** `CSS.escape` where it exists; a conservative fallback where it does not (tests, old hosts). */
function escapeValue(value: string): string {
  const escape = (globalThis as { CSS?: { escape?: (value: string) => string } }).CSS?.escape
  return escape ? escape(value) : value.replace(/["\\]/g, '\\$&')
}

export const settingSelector = (setting: string): string => `[data-setting="${escapeValue(setting)}"]`

const CONTROL_TAGS = new Set(['INPUT', 'SELECT', 'TEXTAREA', 'BUTTON'])
const TEXT_ENTRY_TYPES = new Set(['text', 'number', 'search', 'email', 'url', 'password', 'time', 'date', 'tel'])

/**
 * The control inside a revealed target that should take focus.
 *
 * Targets are marked on the labelled row rather than on the bare input, because a two-pixel
 * checkbox is not what the eye should land on. Focus still belongs on the control itself:
 * a deep link's whole promise is "here is the switch", and a keyboard or screen-reader user
 * arrives able to flip it.
 */
export function focusTarget(element: HTMLElement): HTMLElement | null {
  if (CONTROL_TAGS.has(element.tagName)) return element
  return element.querySelector<HTMLElement>('input,select,textarea,button')
}

/**
 * Whether to move focus at all. A text field focused on a touch device raises the on-screen
 * keyboard over the very control the user was sent to read, so those are left alone there;
 * checkboxes, selects, and buttons raise nothing and are focused everywhere.
 */
export function shouldFocusControl(control: HTMLElement | null, coarsePointer: boolean): boolean {
  if (!control) return false
  if (!coarsePointer) return true
  if (control.tagName === 'TEXTAREA') return false
  if (control.tagName !== 'INPUT') return true
  return !TEXT_ENTRY_TYPES.has((control as HTMLInputElement).type)
}

/** Paint the arrival cue on one element and clear it again. Returns a cancel. */
export function flashSetting(element: HTMLElement, flashMs = SETTING_FLASH_MS): () => void {
  // Restarted rather than left running: a second link to the same control has to flash again,
  // and re-adding a class the element already has animates nothing.
  element.classList.remove(SETTING_FLASH_CLASS)
  void element.offsetWidth
  element.classList.add(SETTING_FLASH_CLASS)
  const timer = window.setTimeout(() => element.classList.remove(SETTING_FLASH_CLASS), flashMs)
  return () => {
    window.clearTimeout(timer)
    element.classList.remove(SETTING_FLASH_CLASS)
  }
}

/** The most specific heading that introduces `target` inside its Settings section. */
export function settingsSectionHeading(target: HTMLElement): HTMLElement | null {
  if (target.matches('h3,h4')) return target
  const section = target.closest<HTMLElement>('section')
  if (!section) return null
  const headings = [...section.querySelectorAll<HTMLElement>('h3,h4')]
  let nearest: HTMLElement | null = null
  for (const heading of headings) {
    if (heading.compareDocumentPosition(target) & Node.DOCUMENT_POSITION_FOLLOWING) nearest = heading
  }
  return nearest || headings[0] || null
}

/**
 * Add context around an exact setting arrival: its heading and the border of the section
 * that owns it. Search still flashes the exact result, while this second cue answers which
 * block the result belongs to when the page contains several dense sections.
 */
export function flashSettingsSection(target: HTMLElement, flashMs = SETTING_FLASH_MS): () => void {
  const section = target.closest<HTMLElement>('section')
  const heading = settingsSectionHeading(target)
  if (!section || !heading) return () => {}
  const pairs = [
    [section, SETTING_SECTION_FLASH_CLASS],
    [heading, SETTING_SECTION_HEADING_FLASH_CLASS],
  ] as const
  for (const [element, className] of pairs) {
    element.classList.remove(className)
    void element.offsetWidth
    element.classList.add(className)
  }
  const timer = window.setTimeout(() => {
    for (const [element, className] of pairs) element.classList.remove(className)
  }, flashMs)
  return () => {
    window.clearTimeout(timer)
    for (const [element, className] of pairs) element.classList.remove(className)
  }
}

/**
 * Flash a sidebar-selected Settings section once its tab and page have rendered.
 * Several headings may map to one declared page, so the first visible match owns the cue.
 */
export function cueSettingsSection(
  container: ParentNode,
  sectionId: string,
  options: Pick<RevealOptions, 'flashMs' | 'timeoutMs'> = {},
): () => void {
  const flashMs = options.flashMs ?? SETTING_FLASH_MS
  const timeoutMs = options.timeoutMs ?? SETTING_REVEAL_TIMEOUT_MS
  let cancelled = false
  let clearFlash: (() => void) | null = null
  let observer: MutationObserver | null = null
  let frame = 0
  let timer = 0

  const stop = () => {
    observer?.disconnect()
    observer = null
    if (timer) { window.clearTimeout(timer); timer = 0 }
    if (frame) { cancelAnimationFrame(frame); frame = 0 }
  }
  const attempt = (): boolean => {
    const selector = `h3[data-settings-section="${escapeValue(sectionId)}"]`
    const heading = [...container.querySelectorAll<HTMLElement>(selector)].find(hasLayoutBox)
    if (!heading) return false
    stop()
    frame = requestAnimationFrame(() => {
      frame = 0
      if (!cancelled) clearFlash = flashSettingsSection(heading, flashMs)
    })
    return true
  }

  if (!attempt() && typeof MutationObserver !== 'undefined') {
    observer = new MutationObserver(() => { if (!cancelled) attempt() })
    observer.observe(container, {
      childList: true,
      subtree: true,
      attributes: true,
      attributeFilter: ['data-settings-section', 'hidden', 'style', 'class'],
    })
    timer = window.setTimeout(stop, timeoutMs)
  }
  return () => {
    cancelled = true
    stop()
    clearFlash?.()
  }
}

const reducedMotion = (): boolean =>
  typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches

const coarseDefault = (): boolean =>
  typeof matchMedia === 'function' && matchMedia('(pointer: coarse)').matches

/**
 * Reveal `setting` inside `container` as soon as it renders. Returns a cancel that stops the
 * wait and clears the flash, so a caller that unmounts (or is superseded by a second link)
 * leaves nothing behind.
 */
export function revealSetting(container: ParentNode, setting: string, options: RevealOptions = {}): () => void {
  const {
    flashMs = SETTING_FLASH_MS,
    timeoutMs = SETTING_REVEAL_TIMEOUT_MS,
    behavior = reducedMotion() ? 'auto' : 'smooth',
    coarsePointer = coarseDefault(),
  } = options
  let cancelled = false
  let clearFlash: (() => void) | null = null
  let observer: MutationObserver | null = null
  let frame = 0
  let timer = 0

  const stop = () => {
    observer?.disconnect()
    observer = null
    if (timer) { window.clearTimeout(timer); timer = 0 }
    if (frame) { cancelAnimationFrame(frame); frame = 0 }
  }

  // A control inside a closed disclosure is present but unreachable; the reveal opens the
  // way in rather than scrolling to a summary and calling it arrival.
  //
  // Opened *before* the layout-box test rather than after it, because how a closed
  // `<details>` hides its body is an engine detail this must not depend on. Current Chromium
  // skips it with `content-visibility`, which still reports client rects, so the check passes
  // either way there; a `display:none` implementation reports none, and the wait below would
  // then observe a control that is already in the DOM until its deadline expired. Opening
  // first is correct under both, and is what lets a settings section fold its body away
  // (`.settings-disclosure`) without stranding anything marked inside it.
  const openDisclosures = (target: HTMLElement): void => {
    for (let node = target.parentElement; node; node = node.parentElement) {
      if (node instanceof HTMLDetailsElement) node.open = true
    }
  }

  const arrive = (target: HTMLElement) => {
    stop()
    // One frame later: opening those disclosures reflows everything below them, and a scroll
    // measured against the pre-reflow layout lands short.
    frame = requestAnimationFrame(() => {
      frame = 0
      if (cancelled) return
      target.scrollIntoView({ block: 'center', behavior })
      clearFlash = flashSetting(target, flashMs)
      const control = focusTarget(target)
      // `preventScroll`, because focus would otherwise re-scroll the control to the edge the
      // browser prefers and undo the centring that was just done.
      if (shouldFocusControl(control, coarsePointer)) control?.focus({ preventScroll: true })
    })
  }

  const attempt = (): boolean => {
    const target = container.querySelector<HTMLElement>(settingSelector(setting))
    if (!target) return false
    openDisclosures(target)
    if (!hasLayoutBox(target)) return false
    arrive(target)
    return true
  }

  if (!attempt()) {
    if (typeof MutationObserver === 'undefined') return () => { cancelled = true; stop() }
    observer = new MutationObserver(() => { if (!cancelled) attempt() })
    observer.observe(container, { childList: true, subtree: true, attributes: true, attributeFilter: ['data-setting', 'hidden', 'style', 'class'] })
    timer = window.setTimeout(stop, timeoutMs)
  }

  return () => {
    cancelled = true
    stop()
    clearFlash?.()
  }
}
