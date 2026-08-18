/**
 * "Does this element generate layout boxes yet?", plus a one-shot notification for when it
 * starts to.
 *
 * Some children cannot be created inside a `display:none` subtree without breaking. The
 * Continuity note editor is the case this exists for: it positions its inline-code copy
 * affordances off `span.offsetParent`, which is null for every node in such a subtree, so its
 * first render throws there (see `ProjectNoteEditor`). Hosts hide a mounted subtree as a
 * matter of course - the utility drawer keeps its note host mounted-but-`hidden` so cursor,
 * undo history, and insert-target routing survive a tab switch; mobile `display:none`s every
 * unfocused pane; desktop zoom does the same to every pane but one - so such children are
 * deferred until their slot is real.
 *
 * `getClientRects()` is the exact test: it is empty precisely when the element, or an
 * ancestor, generates no box, and non-empty for a laid-out element even at zero size.
 * `offsetParent` would answer the same question for the ordinary case but also reports null
 * for a `position:fixed` element that is perfectly well laid out.
 */

/** A `ResizeObserver`, narrowed to what the wait below uses, so tests can supply one. */
export type SizeObserverLike = {
  observe: (target: Element) => void
  disconnect: () => void
}

/** Builds the observer that watches for the slot gaining a box; null when unavailable. */
export type SizeObserverFactory = (onResize: () => void) => SizeObserverLike | null

export function hasLayoutBox(element: Element): boolean {
  return element.getClientRects().length > 0
}

export function defaultSizeObserver(onResize: () => void): SizeObserverLike | null {
  if (typeof ResizeObserver === 'undefined') return null
  return new ResizeObserver(() => onResize())
}

/** Next frame, or immediately where there are no frames (tests, non-browser hosts). */
function schedule(run: () => void): void {
  if (typeof requestAnimationFrame === 'undefined') run()
  else requestAnimationFrame(run)
}

/**
 * Call `onLayoutBox` as soon as `element` generates layout boxes - synchronously when it
 * already does - and return a cancel function. Fires at most once.
 *
 * `ResizeObserver` rather than `IntersectionObserver`: the transition being waited for is
 * "gained a box", which a resize observation reports even for an element revealed outside the
 * viewport, while intersection would stay false until it was also scrolled into view.
 */
export function whenLayoutBox(
  element: Element,
  onLayoutBox: () => void,
  createObserver: SizeObserverFactory = defaultSizeObserver,
): () => void {
  if (hasLayoutBox(element)) {
    onLayoutBox()
    return () => {}
  }
  let settled = false
  let cancelled = false
  const observer = createObserver(() => {
    if (settled || !hasLayoutBox(element)) return
    settled = true
    observer?.disconnect()
    // Out of the resize callback before doing anything: whatever the caller mounts here
    // resizes the very box that was just observed, and a browser answers that with
    // "ResizeObserver loop completed with undelivered notifications" on `window.onerror`.
    // Harmless in itself, but it is noise in every error surface that watches that event.
    schedule(() => { if (!cancelled) onLayoutBox() })
  })
  // No observer to be had (a very old browser, or a DOM-less test double): report now rather
  // than never. That is the behavior from before this gate existed, hidden-render hazard
  // included, and a permanently empty editor would be the worse failure.
  if (!observer) {
    onLayoutBox()
    return () => {}
  }
  observer.observe(element)
  return () => {
    settled = true
    cancelled = true
    observer.disconnect()
  }
}
