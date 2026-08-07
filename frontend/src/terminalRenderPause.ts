/**
 * Pause and resume xterm's renderer for a retained warm pane.
 *
 * xterm pauses its RenderService from an IntersectionObserver, and intersection is
 * geometric: `.pane-warm`'s `visibility:hidden` box still intersects, so a warm pane
 * kept rendering every animation frame with pending writes while `display:none`
 * (which the warm cache deliberately abandoned — renderers do not survive zero-sized
 * intervals) rendered nothing. Measured and pinned in the renderer Playwright suite.
 * Up to three warm panes hosting busy agents is continuous invisible DOM work on the
 * main thread, paid exactly when a visible pane is competing for it.
 *
 * The pause reuses xterm's own intersection handler rather than a parallel flag, so
 * it carries the library's complete pause bookkeeping: writes keep parsing (the
 * terminal model stays correct, which is the whole point of a warm pane), renders
 * defer into `_needsFullRefresh`, a resize while paused parks in the debounced idle
 * task, and resuming remeasures cell metrics and repaints every row.
 *
 * This reaches into pinned internals (`_core._renderService._handleIntersectionChange`,
 * verbatim in the vendored xterm 6.0.0 bundle, which the build already patches and
 * verifies). When an upgrade moves them, `available` turns false and every call
 * degrades to a no-op — warm panes simply keep rendering, today's behavior — and the
 * renderer suite fails loudly so the cost decision is remade rather than silently lost.
 */

type IntersectionEntryLike = { isIntersecting: boolean; intersectionRatio: number }
type RenderServiceLike = {
  _handleIntersectionChange?: (entry: IntersectionEntryLike) => void
}
type TerminalWithCore = { _core?: { _renderService?: RenderServiceLike } }

export interface TerminalRenderControl {
  /** Whether the pinned internals were found; false degrades every call to a no-op. */
  readonly available: boolean
  /** Stop rendering (parsing continues). Returns whether the pause was applied. */
  pause: () => boolean
  /** Resume rendering with xterm's own recovery: remeasure and full repaint. */
  resume: () => boolean
}

export function terminalRenderControl(term: unknown): TerminalRenderControl {
  const service = (term as TerminalWithCore | null | undefined)?._core?._renderService
  const handler = service?._handleIntersectionChange
  const available = typeof handler === 'function' && !!service
  let paused = false
  const apply = (isIntersecting: boolean): boolean => {
    if (typeof handler !== 'function') return false
    try {
      handler.call(service, { isIntersecting, intersectionRatio: isIntersecting ? 1 : 0 })
      return true
    } catch {
      return false
    }
  }
  return {
    available,
    pause() {
      if (!available || !service) return false
      if (!paused) {
        paused = true
        // A single synthetic entry is not sticky: the IntersectionObserver delivers
        // its own entries asynchronously (an initial one right after observation,
        // and more whenever layout crosses the threshold), and geometry says a
        // visibility:hidden box is intersecting — each delivery would silently
        // unpause the pane. Shadow the handler so every delivery lands as
        // not-intersecting until resume restores it.
        service._handleIntersectionChange = () => { apply(false) }
      }
      return apply(false)
    },
    resume() {
      if (!available || !service) return false
      if (paused) {
        paused = false
        service._handleIntersectionChange = handler
      }
      return apply(true)
    },
  }
}
