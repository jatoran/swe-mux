import { railOverlayBox, type RailOverlayBox, type RailOverlayView, type RailPopoverRect } from './railOverflow'

// The DOM half of placing a command-rail overlay: what "visible" means right now, and what
// `position:fixed` actually means at the place this overlay is mounted.
//
// Both halves exist because of the soft keyboard, and they are two separate bugs that looked
// like one.
//
// The first is the viewport. swe-mux declares `interactive-widget=resizes-visual`, so an open
// keyboard leaves the *layout* viewport at full height - deliberately, because sizing the shell
// from `visualViewport` shrank every terminal, and shrinking an alternate-screen PTY discards
// rows permanently. Every bound drawn against `window.innerHeight` therefore describes a
// rectangle that runs well behind the keyboard, and an overlay allowed half of *that* is half
// of a screen the operator cannot see.
//
// The second is the containing block. `.terminal-surface` is translated up by the keyboard's
// inset so the rail stays above it, and a transformed ancestor is the containing block for its
// `position:fixed` descendants - which every rail overlay is. So the same `bottom:120px` means
// one place with the keyboard down and a place `inset` pixels higher with it up, which is the
// "the panel is thrown up the screen when I start typing" report exactly. Rather than portal
// these out (which would take the rail's chip styling with them), the block is measured and
// subtracted, so the numbers the pure placement produces keep meaning screen coordinates.

/** What the operator can see, in layout-viewport coordinates. */
export function railOverlayView(): RailOverlayView {
  const visual = typeof window !== 'undefined' ? window.visualViewport : null
  if (!visual) return { left: 0, top: 0, width: window.innerWidth, height: window.innerHeight }
  return { left: visual.offsetLeft, top: visual.offsetTop, width: visual.width, height: visual.height }
}

/**
 * The box a `position:fixed` descendant of `host` is really positioned against.
 *
 * The same walk the engine does: the nearest ancestor establishing a containing block for
 * fixed descendants, or the viewport if there is none. `contain` and `will-change` are
 * checked alongside the obvious three because they do it too, and a future style that adds
 * one of them to a pane would otherwise move every rail overlay silently.
 */
export function fixedContainingBlock(host: HTMLElement | null): DOMRect | null {
  for (let node = host?.parentElement ?? null; node; node = node.parentElement) {
    const style = getComputedStyle(node)
    if (
      style.transform !== 'none'
      || style.perspective !== 'none'
      || style.filter !== 'none'
      || style.backdropFilter !== 'none'
      || /transform|perspective|filter/.test(style.willChange)
      || /paint|layout|strict|content/.test(style.contain)
    ) return node.getBoundingClientRect()
  }
  return null
}

/** Screen coordinates as the CSS a fixed element mounted at `host` needs to land on them. */
export function railOverlayCss(box: RailOverlayBox, host: HTMLElement | null): Record<string, string> {
  const block = fixedContainingBlock(host)
  const originX = block?.left ?? 0
  const originBottom = block?.bottom ?? (typeof window === 'undefined' ? 0 : window.innerHeight)
  return {
    left: `${Math.round(box.left - originX)}px`,
    bottom: `${Math.round(originBottom - box.bottom)}px`,
    width: `${box.width}px`,
    maxHeight: `${box.maxHeight}px`,
  }
}

/** Place a rail overlay: the pure geometry, then the conversion for where it is mounted. */
export function railOverlayStyle(
  anchor: RailPopoverRect,
  host: HTMLElement | null,
  maxWidth: number,
): Record<string, string> {
  return railOverlayCss(railOverlayBox(anchor, railOverlayView(), maxWidth), host)
}

/**
 * Re-place on everything that can move an overlay out from under itself.
 *
 * The keyboard's open and close are `visualViewport` resizes and nothing else - no `resize`
 * on `window` fires for them under `resizes-visual` - and the rail is a horizontal scroller,
 * so a trigger can pan away beneath a fixed panel. Returns the detach function.
 */
export function watchRailOverlayPlacement(reposition: () => void): () => void {
  const visual = window.visualViewport
  window.addEventListener('resize', reposition)
  window.addEventListener('scroll', reposition, true)
  visual?.addEventListener('resize', reposition)
  visual?.addEventListener('scroll', reposition)
  return () => {
    window.removeEventListener('resize', reposition)
    window.removeEventListener('scroll', reposition, true)
    visual?.removeEventListener('resize', reposition)
    visual?.removeEventListener('scroll', reposition)
  }
}
