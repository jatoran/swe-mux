import type { ComponentChildren, JSX } from 'preact'
import { useEffect, useRef, useState } from 'preact/hooks'
import { horizontalWheelDelta } from './wheelScroll'
import {
  railFocusTarget,
  railOverflowState,
  railPageTarget,
  type RailOverflowState,
  type RailScrollDirection,
} from './railOverflow'

type OverflowRailStripProps = Omit<JSX.HTMLAttributes<HTMLDivElement>, 'class' | 'className' | 'ref' | 'onWheel' | 'onFocusCapture'> & {
  [name: `data-${string}`]: string | number | boolean | undefined
}

interface OverflowRailProps {
  children: ComponentChildren
  className: string
  itemLabel: string
  wrapperClassName?: string
  activeKey?: string
  stripProps?: OverflowRailStripProps
  touchDrag?: boolean
}

type TouchDragState = {
  pointerId: number
  startX: number
  startY: number
  startScrollLeft: number
  dragging: boolean
  cancelled: boolean
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

function revealItem(strip: HTMLDivElement, item: HTMLElement): void {
  let railItem = item
  while (railItem.parentElement && railItem.parentElement !== strip) railItem = railItem.parentElement
  if (railItem.parentElement !== strip) return
  const stripBounds = strip.getBoundingClientRect()
  const itemBounds = railItem.getBoundingClientRect()
  const itemStart = strip.scrollLeft + itemBounds.left - stripBounds.left
  const target = railFocusTarget(metrics(strip), itemStart, itemStart + itemBounds.width)
  if (target !== strip.scrollLeft) strip.scrollTo({ left: target, behavior: reducedMotion() ? 'auto' : 'smooth' })
}

/** Horizontal strip with endpoint-aware, non-layout-consuming controls. */
export function OverflowRail({
  children,
  className,
  itemLabel,
  wrapperClassName = '',
  activeKey,
  stripProps,
  touchDrag = false,
}: OverflowRailProps) {
  const stripRef = useRef<HTMLDivElement>(null)
  const touchDragRef = useRef<TouchDragState | null>(null)
  const suppressClickUntilRef = useRef(0)
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
      resizeObserver?.disconnect()
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

  useEffect(() => {
    if (activeKey === undefined) return
    const frame = requestAnimationFrame(() => {
      const strip = stripRef.current
      const selected = strip?.querySelector<HTMLElement>('[role="tab"][aria-selected="true"]')
      if (strip && selected) revealItem(strip, selected)
      syncOverflow()
    })
    return () => cancelAnimationFrame(frame)
  }, [activeKey])

  const page = (direction: RailScrollDirection) => {
    const strip = stripRef.current
    if (!strip) return
    strip.scrollTo({
      left: railPageTarget(metrics(strip), itemOffsets(strip), direction),
      behavior: reducedMotion() ? 'auto' : 'smooth',
    })
  }

  const finishTouchDrag = (wrapper: HTMLDivElement, pointerId: number, cancelled: boolean) => {
    const state = touchDragRef.current
    if (!state || state.pointerId !== pointerId) return
    if (state.dragging && !cancelled) suppressClickUntilRef.current = performance.now() + 500
    touchDragRef.current = null
    if (wrapper.hasPointerCapture(pointerId)) wrapper.releasePointerCapture(pointerId)
    syncOverflow()
  }

  return <div
    class={`overflow-rail ${touchDrag ? 'overflow-rail-touch-drag' : ''} ${wrapperClassName}`.trim()}
    onPointerDown={event => {
      if (!touchDrag || event.pointerType !== 'touch' || !event.isPrimary) return
      const strip = stripRef.current
      if (!strip || strip.scrollWidth <= strip.clientWidth + 1) return
      touchDragRef.current = {
        pointerId: event.pointerId,
        startX: event.clientX,
        startY: event.clientY,
        startScrollLeft: strip.scrollLeft,
        dragging: false,
        cancelled: false,
      }
      event.currentTarget.setPointerCapture(event.pointerId)
    }}
    onPointerMove={event => {
      const state = touchDragRef.current
      const strip = stripRef.current
      if (!state || !strip || state.pointerId !== event.pointerId || state.cancelled) return
      const dx = event.clientX - state.startX
      const dy = event.clientY - state.startY
      if (!state.dragging) {
        if (Math.max(Math.abs(dx), Math.abs(dy)) < 6) return
        if (Math.abs(dy) >= Math.abs(dx)) { state.cancelled = true; return }
        state.dragging = true
      }
      event.preventDefault()
      strip.scrollLeft = state.startScrollLeft - dx
      syncOverflow()
    }}
    onPointerUp={event => finishTouchDrag(event.currentTarget, event.pointerId, false)}
    onPointerCancel={event => finishTouchDrag(event.currentTarget, event.pointerId, true)}
    onClickCapture={event => {
      if (performance.now() > suppressClickUntilRef.current) return
      event.preventDefault()
      event.stopPropagation()
    }}
  >
    {overflow.left && <button class="overflow-rail-edge overflow-rail-left" type="button" aria-label={`Scroll ${itemLabel} left`} title={`More ${itemLabel} to the left`} onClick={() => page(-1)}>‹</button>}
    <div
      {...stripProps}
      class={`overflow-rail-strip ${className}`}
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
        revealItem(strip, item)
      }}
    >{children}</div>
    {overflow.right && <button class="overflow-rail-edge overflow-rail-right" type="button" aria-label={`Scroll ${itemLabel} right`} title={`More ${itemLabel} to the right`} onClick={() => page(1)}>›</button>}
  </div>
}

/** Command-rail specialization of the shared overflow treatment. */
export function RailScroller({ children }: Pick<OverflowRailProps, 'children'>) {
  return <OverflowRail className="terminal-action-scroll" itemLabel="commands" wrapperClassName="terminal-action-scroller">
    {children}
  </OverflowRail>
}
