import type { ComponentChildren } from 'preact'
import { useEffect, useRef, useState } from 'preact/hooks'
import { horizontalWheelDelta } from './wheelScroll'
import {
  railFocusTarget,
  railOverflowState,
  railPageTarget,
  type RailOverflowState,
  type RailScrollDirection,
} from './railOverflow'

interface Props {
  children: ComponentChildren
}

const NO_OVERFLOW: RailOverflowState = { left: false, right: false }

function sameOverflow(left: RailOverflowState, right: RailOverflowState): boolean {
  return left.left === right.left && left.right === right.right
}

function metrics(strip: HTMLDivElement) {
  return {
    scrollLeft: strip.scrollLeft,
    scrollWidth: strip.scrollWidth,
    clientWidth: strip.clientWidth,
  }
}

function itemOffsets(strip: HTMLDivElement): number[] {
  const items = Array.from(strip.children) as HTMLElement[]
  const leading = items[0]?.offsetLeft ?? 0
  return items.map(item => item.offsetLeft - leading)
}

function reducedMotion(): boolean {
  return window.matchMedia('(prefers-reduced-motion: reduce)').matches
}

/** Horizontal command strip with endpoint-aware, non-layout-consuming controls. */
export function RailScroller({ children }: Props) {
  const stripRef = useRef<HTMLDivElement>(null)
  const [overflow, setOverflow] = useState<RailOverflowState>(NO_OVERFLOW)

  const syncOverflow = () => {
    const strip = stripRef.current
    if (!strip) return
    const next = railOverflowState(metrics(strip))
    setOverflow(current => sameOverflow(current, next) ? current : next)
  }

  useEffect(() => {
    const strip = stripRef.current
    if (!strip) return

    const resizeObserver = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(syncOverflow)
    const observeSizes = () => {
      resizeObserver?.observe(strip)
      for (const child of Array.from(strip.children)) resizeObserver?.observe(child)
    }
    const mutationObserver = typeof MutationObserver === 'undefined' ? null : new MutationObserver(() => {
      observeSizes()
      syncOverflow()
    })

    observeSizes()
    mutationObserver?.observe(strip, { childList: true, characterData: true, subtree: true })
    strip.addEventListener('scroll', syncOverflow, { passive: true })
    window.addEventListener('resize', syncOverflow)
    syncOverflow()
    return () => {
      resizeObserver?.disconnect()
      mutationObserver?.disconnect()
      strip.removeEventListener('scroll', syncOverflow)
      window.removeEventListener('resize', syncOverflow)
    }
  }, [])

  // Child labels and visibility can change during a normal Preact render without
  // resizing the strip itself. Reconcile after every render as the cheap backstop.
  useEffect(syncOverflow)

  const page = (direction: RailScrollDirection) => {
    const strip = stripRef.current
    if (!strip) return
    strip.scrollTo({
      left: railPageTarget(metrics(strip), itemOffsets(strip), direction),
      behavior: reducedMotion() ? 'auto' : 'smooth',
    })
  }

  return <div class="terminal-action-scroller">
    {overflow.left && <button class="rail-scroll-edge rail-scroll-left" type="button" aria-label="Scroll commands left" title="More commands to the left" onClick={() => page(-1)}>‹</button>}
    <div
      class="terminal-action-scroll"
      ref={stripRef}
      onWheel={event => {
        const strip = event.currentTarget
        const delta = horizontalWheelDelta(event, strip)
        if (!delta) return
        event.preventDefault()
        strip.scrollLeft += delta
      }}
      onFocusCapture={event => {
        const strip = event.currentTarget
        const item = event.target instanceof HTMLElement ? event.target.closest<HTMLElement>('button') : null
        if (!item || !strip.contains(item)) return
        const target = railFocusTarget(metrics(strip), item.offsetLeft, item.offsetLeft + item.offsetWidth)
        if (target !== strip.scrollLeft) strip.scrollTo({ left: target, behavior: reducedMotion() ? 'auto' : 'smooth' })
      }}
    >{children}</div>
    {overflow.right && <button class="rail-scroll-edge rail-scroll-right" type="button" aria-label="Scroll commands right" title="More commands to the right" onClick={() => page(1)}>›</button>}
  </div>
}
