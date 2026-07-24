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
