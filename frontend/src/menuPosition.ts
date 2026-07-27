export const CONTEXT_MENU_WIDTH = 300

export function clampContextMenuLeft(
  pointerX: number,
  viewportWidth: number,
  preferredWidth = CONTEXT_MENU_WIDTH,
  gutter = 4,
): number {
  const availableWidth = Math.max(0, viewportWidth - gutter * 2)
  const renderedWidth = Math.min(preferredWidth, availableWidth)
  return Math.max(gutter, Math.min(pointerX, viewportWidth - renderedWidth - gutter))
}

/**
 * Ref callback for a `position:fixed` context menu: once it has mounted (and on
 * every re-render, e.g. when a confirm step expands it), measure the real box
 * and clamp it fully into the viewport. The inline top/left are seeded from
 * pointer coordinates minus rough height guesses, so a menu that is taller than
 * its guess — or opened near the bottom on a short mobile viewport — would
 * otherwise spill off-screen. CSS caps the menu's max-height to the viewport,
 * so a clamped top always leaves the whole menu visible.
 */
export function fitMenuInViewport(el: HTMLElement | null, gutter = 4): void {
  if (!el) return
  const rect = el.getBoundingClientRect()
  const top = Math.max(gutter, Math.min(rect.top, window.innerHeight - rect.height - gutter))
  const left = Math.max(gutter, Math.min(rect.left, window.innerWidth - rect.width - gutter))
  el.style.top = `${top}px`
  el.style.left = `${left}px`
}

/**
 * Same clamping as {@link fitMenuInViewport}, for a menu whose own body scrolls
 * (`overflow:auto`): the rendered height is first capped to the viewport, then
 * the top is lifted so the whole box — including its last row — is on-screen.
 *
 * A CSS `max-height` alone cannot do this. It is measured against the viewport,
 * not against the menu's own top, so a menu opened halfway down a short phone
 * screen can be viewport-tall and still hang off the bottom, clipping its final
 * entries with no way to scroll to them. Measuring is done with any previous
 * inline cap cleared, so repeated calls (re-render, resize, async content) are
 * idempotent rather than ratcheting the menu smaller.
 */
export function fitScrollingMenuInViewport(el: HTMLElement | null, gutter = 6): void {
  if (!el) return
  el.style.maxHeight = ''
  const rect = el.getBoundingClientRect()
  const available = Math.max(0, window.innerHeight - gutter * 2)
  if (rect.height > available) el.style.maxHeight = `${available}px`
  const height = Math.min(rect.height, available)
  const top = Math.max(gutter, Math.min(rect.top, window.innerHeight - height - gutter))
  const left = Math.max(gutter, Math.min(rect.left, window.innerWidth - rect.width - gutter))
  el.style.top = `${top}px`
  el.style.left = `${left}px`
}
